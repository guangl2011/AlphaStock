"""CLI: ``python -m pipelines.correlation [flags]``.

Modes:

* ``fixture`` (default) — everything from checked-in correlation fixtures
  (closes, index levels, signal events); no network, no secrets. This is
  what CI and demos run.
* ``live`` — constituent and proxy EOD from Tiingo (``TIINGO_API_KEY``
  required, cache-first, paced exactly like the index pipeline); index
  levels read from a canonical ``index_levels.csv`` produced by the index
  pipeline (default ``outputs/index/index_levels.csv``).

Outputs in ``--out-dir`` (default ``outputs/correlation``):

* ``factor_exposures.csv`` — per index × factor: OLS beta/SE/t (regression
  factors only), R², and full/63d/21d correlations.
* ``stock_factor_summary.csv`` — per stock × factor: trailing 63-day beta
  and correlation, plus per-direction signal-event abnormal-return
  aggregates for that stock.
* ``correlation_matrices.csv`` — pairwise correlations among both indices
  and all eight factors, per window (full/63d/21d).
* ``correlation_metadata.json`` — factor definitions, window rules, event
  aggregates by direction and category, alignment gaps, and the SHA-256
  attestation of all three CSVs.

Determinism contract: outputs are a pure function of the inputs — no
wall-clock timestamps, sorted iteration, fixed float formatting — so two
runs over the same inputs are byte-identical.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import logging
import os
import sys
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path

from ..indices.benchmarks import INDEX_BENCHMARKS
from ..indices.engine import EodRecord
from ..indices.fetcher import TiingoEodClient
from ..scoring.alpha_score import SignalEvent
from . import factors as factor_defs
from .model import (
    WINDOW_LONG,
    WINDOW_SHORT,
    aggregate_ars,
    event_abnormal_returns,
    index_factor_exposures,
    stock_factor_rows,
)
from .stats import pearson

logger = logging.getLogger(__name__)

METHODOLOGY_VERSION = "1.0"
WINDOWS: tuple[str, ...] = ("full", "63d", "21d")

#: Factors the stock-level summary covers: MKT plus each thematic comparator.
STOCK_FACTORS: tuple[str, ...] = ("GRID", "MKT", "NUKE", "ROBO", "UTIL")

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "correlation"
FIXTURE_CLOSES = FIXTURE_DIR / "closes.csv"
FIXTURE_LEVELS = FIXTURE_DIR / "index_levels.csv"
FIXTURE_SIGNALS = FIXTURE_DIR / "signal_events.csv"
DEFAULT_OUT_DIR = Path(__file__).resolve().parents[2] / "outputs" / "correlation"
DEFAULT_LIVE_LEVELS = Path(__file__).resolve().parents[2] / "outputs" / "index" / "index_levels.csv"
DEFAULT_RESEARCH_LEVELS = (
    Path(__file__).resolve().parents[2] / "outputs" / "research" / "research_levels.csv"
)
DEFAULT_CACHE_DIR = Path(__file__).resolve().parents[2] / "outputs" / "correlation_cache"


class CorrelationInputError(ValueError):
    """Raised when a run's inputs are missing or malformed."""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m pipelines.correlation",
        description="Correlation & factor-relationship model over AS-100 / AS-MCG100 / AS-MCB100.",
    )
    parser.add_argument(
        "--mode", choices=("fixture", "live", "research"), default="fixture",
        help="fixture: checked-in correlation fixtures; live: Tiingo prices; research: "
             "the checked-in 2024-01-02 research frame + research index levels (offline)",
    )
    parser.add_argument("--closes-file", default=str(FIXTURE_CLOSES))
    parser.add_argument("--index-levels", default=None,
                        help="canonical index_levels.csv (fixture default: "
                             "tests/fixtures/correlation/index_levels.csv)")
    parser.add_argument("--signals-file", default=str(FIXTURE_SIGNALS))
    parser.add_argument("--stocks", default=None,
                        help="comma-separated tickers for the stock layer "
                             "(default: every non-proxy ticker in the price data)")
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--pace-seconds", type=float, default=3.0)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

    default_levels = {
        "fixture": FIXTURE_LEVELS,
        "live": DEFAULT_LIVE_LEVELS,
        "research": DEFAULT_RESEARCH_LEVELS,
    }[args.mode]
    levels_path = Path(args.index_levels or default_levels)
    if not levels_path.is_file():
        raise CorrelationInputError(
            f"index levels file not found: {levels_path} — run the index pipeline first"
        )
    index_levels = load_index_levels(levels_path)

    # Research mode prices real constituents; the checked-in signal events are
    # synthetic fixture rows, so the event study is skipped there (not faked).
    signals = [] if args.mode == "research" else load_signals(Path(args.signals_file))

    if args.mode == "live":
        api_key = os.environ.get("TIINGO_API_KEY", "")
        if not api_key:
            raise CorrelationInputError(
                "live mode requires TIINGO_API_KEY in the environment "
                "(fixture mode needs nothing and stays offline)"
            )
        client = TiingoEodClient(api_key, args.cache_dir, pace_seconds=args.pace_seconds)
        proxies = sorted(factor_defs.proxy_names())
        if args.stocks:
            stocks = _resolve_stocks(args.stocks, set(), set())
        else:
            # Default stock layer: every constituent of both indices.
            from ..indices.compute import load_compositions

            compositions = load_compositions()
            stocks = sorted(
                {t for tickers in compositions.values() for t in tickers} - set(proxies)
            )
        start = min(d for levels in index_levels.values() for d in levels)
        end = date.today()
        records = client.fetch_range(proxies + stocks, start, end)
        source = "live-tiingo"
    elif args.mode == "research":
        from ..indices.research import DEFAULT_FRAME, parse_frame
        from ..indices.yahoo import read_frame_text

        records = parse_frame(read_frame_text(DEFAULT_FRAME))
        from ..indices.compute import load_compositions

        stocks = _resolve_stocks(
            args.stocks,
            {t for tickers in load_compositions().values() for t in tickers} & set(records),
            factor_defs.proxy_names(),
        )
        source = "research-yahoo"
    else:
        records = load_fixture_records(Path(args.closes_file))
        stocks = _resolve_stocks(args.stocks, set(records), factor_defs.proxy_names())
        source = "fixture"

    outputs = run_model(index_levels, records, stocks, signals, source)
    out_dir = Path(args.out_dir)
    hashes = write_outputs(out_dir, outputs)
    for name, digest in sorted(hashes.items()):
        print(f"{name}: {digest}")
    return 0


def _resolve_stocks(
    requested: str | None, available: set[str], proxies: set[str]
) -> list[str]:
    if requested:
        return sorted({t.strip().upper() for t in requested.split(",") if t.strip()})
    return sorted(available - proxies)


# -- input loaders -----------------------------------------------------------


def load_fixture_records(path: Path) -> dict[str, list[EodRecord]]:
    """Read closes CSV (same canonical columns as the index fixture)."""
    from ..indices.__main__ import load_fixture_records as _load

    return _load(path)


def load_index_levels(path: Path) -> dict[str, dict[date, float]]:
    """Read a canonical index_levels.csv into {index_id: {date: level}}."""
    levels: dict[str, dict[date, float]] = {}
    with path.open(newline="", encoding="utf-8") as f:
        for i, row in enumerate(csv.DictReader(f), start=2):
            try:
                index_id = row["index_id"].strip()
                day = date.fromisoformat(row["date"].strip())
                level = float(row["level"])
            except (KeyError, ValueError) as exc:
                raise CorrelationInputError(f"{path.name} row {i}: malformed ({exc})") from exc
            levels.setdefault(index_id, {})[day] = level
    if not levels:
        raise CorrelationInputError(f"{path} carries no index levels")
    return levels


def load_signals(path: Path) -> list[SignalEvent]:
    """Read signal events: signal_id, category, direction, strength,
    published_at (tz-aware ISO), tickers (``|``-separated)."""
    events: list[SignalEvent] = []
    with path.open(newline="", encoding="utf-8") as f:
        for i, row in enumerate(csv.DictReader(f), start=2):
            try:
                events.append(
                    SignalEvent(
                        signal_id=row["signal_id"].strip(),
                        category=row["category"].strip(),
                        direction=row["direction"].strip(),
                        strength=int(row["strength"]),
                        published_at=_parse_tz_datetime(row["published_at"].strip(), i),
                        tickers=tuple(
                            t.strip().upper() for t in row["tickers"].split("|") if t.strip()
                        ),
                    )
                )
            except (KeyError, ValueError) as exc:
                raise CorrelationInputError(f"signals row {i}: malformed ({exc})") from exc
    return events


def _parse_tz_datetime(value: str, row: int) -> object:
    from datetime import datetime

    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise CorrelationInputError(
            f"signals row {row}: published_at {value!r} must be timezone-aware"
        )
    return parsed


# -- model assembly ----------------------------------------------------------


def levels_to_returns(levels: Mapping[date, float]) -> dict[date, float]:
    """Day-over-day returns of one index's level series, in sorted-date order."""
    ordered = sorted(levels)
    if not ordered:
        raise CorrelationInputError("index level series is empty")
    return {
        cur: levels[cur] / levels[prev] - 1.0
        for prev, cur in zip(ordered, ordered[1:], strict=False)
    }


def run_model(
    index_levels: dict[str, dict[date, float]],
    records: dict[str, list[EodRecord]],
    stocks: Sequence[str],
    signals: list[SignalEvent],
    source: str,
) -> dict[str, str]:
    """Compute all three CSV payloads plus metadata from resolved inputs."""
    build = factor_defs.build_factors(records)
    factor_series = build.series

    index_returns = {
        index_id: levels_to_returns(levels) for index_id, levels in index_levels.items()
    }

    # factor_exposures.csv ---------------------------------------------------
    exposures: list[dict[str, object]] = []
    for index_id in sorted(index_returns):
        exposures.extend(index_factor_exposures(index_id, index_returns[index_id], factor_series))
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(
        ["index_id", "factor", "beta", "std_error", "t_stat", "r_squared",
         "corr_full", "corr_63d", "corr_21d", "dropped_collinear", "regression_days"]
    )
    for row in exposures:
        writer.writerow(
            [row["index_id"], row["factor"],
             _fmt(row["beta"]), _fmt(row["std_error"]), _fmt(row["t_stat"]),
             _fmt(row["r_squared"]), _fmt(row["corr_full"]), _fmt(row["corr_63d"]),
             _fmt(row["corr_21d"]), ";".join(row["dropped_collinear"]), row["regression_days"]]
        )
    factor_exposures_csv = buf.getvalue()

    # stock_factor_summary.csv ----------------------------------------------
    stock_returns = {t: factor_defs.daily_returns(records[t]) for t in stocks if records.get(t)}
    per_stock_events = event_abnormal_returns(signals, stock_returns, factor_series["MKT"])
    aggregates = aggregate_ars(per_stock_events)
    by_stock_direction: dict[str, dict[str, list[float]]] = {}
    for event in per_stock_events:
        by_stock_direction.setdefault(event.ticker, {}).setdefault(event.direction, []).append(
            event.abnormal_return
        )

    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(
        ["ticker", "factor", "beta_63d", "corr_63d",
         "mean_abnormal_return", "abnormal_return_events"]
    )
    for ticker in sorted(stock_returns):
        for row in stock_factor_rows(ticker, stock_returns[ticker], factor_series, STOCK_FACTORS):
            writer.writerow([ticker, row.factor, _fmt(row.beta), _fmt(row.corr), "", ""])
        for direction in ("negative", "neutral", "positive"):
            ars = by_stock_direction.get(ticker, {}).get(direction)
            if not ars:
                continue
            writer.writerow(
                [ticker, f"event_{direction}", "", "",
                 _fmt(sum(ars) / len(ars)), len(ars)]
            )
    stock_summary_csv = buf.getvalue()

    # correlation_matrices.csv ----------------------------------------------
    matrices_csv = correlation_matrices_csv(index_returns, factor_series)

    # correlation_metadata.json ---------------------------------------------
    metadata = {
        "methodology_version": METHODOLOGY_VERSION,
        "source": source,
        "factor_definitions": build.definitions,
        "regression_factors": list(factor_defs.REGRESSION_FACTORS),
        "correlation_factors": list(factor_defs.FACTOR_NAMES),
        "windows": {
            "correlation": {"full": "all aligned days", "63d": WINDOW_LONG, "21d": WINDOW_SHORT},
            "stock_beta": WINDOW_LONG,
            "event_study": {
                "estimation_returns_before_event": 63,
                "window": "[t-1, t+3] compounded returns",
            },
        },
        "alignment_gaps": build.alignment_gaps,
        "event_aggregates": {
            "by_direction": aggregates.by_direction,
            "by_category": aggregates.by_category,
        },
        "event_rows": [
            {
                "signal_id": e.signal_id, "ticker": e.ticker, "category": e.category,
                "direction": e.direction, "event_date": e.event_date.isoformat(),
                "beta": f"{e.beta:.6f}", "abnormal_return": f"{e.abnormal_return:.6f}",
            }
            for e in per_stock_events
        ],
        "benchmark_proxy_mapping_note": (
            "factors are built only from ETF proxies in "
            "pipelines.indices.benchmarks.PROXY_MAP; index benchmark sets: "
            f"{ {k: list(v) for k, v in sorted(INDEX_BENCHMARKS.items())} }"
        ),
        "sha256_factor_exposures_csv": hashlib.sha256(factor_exposures_csv.encode()).hexdigest(),
        "sha256_stock_factor_summary_csv": hashlib.sha256(stock_summary_csv.encode()).hexdigest(),
        "sha256_correlation_matrices_csv": hashlib.sha256(matrices_csv.encode()).hexdigest(),
    }
    metadata_json = json.dumps(metadata, indent=2, sort_keys=True, ensure_ascii=False) + "\n"

    return {
        "factor_exposures.csv": factor_exposures_csv,
        "stock_factor_summary.csv": stock_summary_csv,
        "correlation_matrices.csv": matrices_csv,
        "correlation_metadata.json": metadata_json,
    }


def correlation_matrices_csv(
    index_returns: Mapping[str, Mapping[date, float]],
    factor_series: Mapping[str, Mapping[date, float]],
) -> str:
    """Pairwise correlations across both indices and all factors, per window.

    The windowed value is the final trailing-window value — the last entry
    of the rolling series, which by construction uses only data up to the
    series end.
    """
    series = {
        name: dict(returns)
        for name, returns in [*index_returns.items(), *factor_series.items()]
    }
    names = sorted(series)
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["series_a", "series_b", "window", "correlation"])
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            common = sorted(set(series[a]) & set(series[b]))
            xs = [series[a][d] for d in common]
            ys = [series[b][d] for d in common]
            full = pearson(xs, ys)
            w63 = (
                pearson(xs[-WINDOW_LONG:], ys[-WINDOW_LONG:])
                if len(xs) >= WINDOW_LONG
                else None
            )
            w21 = (
                pearson(xs[-WINDOW_SHORT:], ys[-WINDOW_SHORT:])
                if len(xs) >= WINDOW_SHORT
                else None
            )
            values = {"full": full, "63d": w63, "21d": w21}
            for window in WINDOWS:
                writer.writerow([a, b, window, _fmt(values[window])])
    return buf.getvalue()


def _fmt(value: float | int | None) -> str:
    """Fixed 6-decimal formatting; ``None`` serializes as empty, never 0."""
    if value is None:
        return ""
    return f"{value:.6f}"


def write_outputs(out_dir: Path | str, outputs: Mapping[str, str]) -> dict[str, str]:
    """Write artifacts; returns {filename: sha256} (attestation trail)."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    hashes: dict[str, str] = {}
    for name, content in outputs.items():
        (out / name).write_text(content, encoding="utf-8")
        hashes[name] = hashlib.sha256(content.encode()).hexdigest()
        logger.info("wrote %s (sha256 %s)", out / name, hashes[name])
    return hashes


if __name__ == "__main__":
    try:
        sys.exit(main())
    except CorrelationInputError as exc:
        logger.error("%s", exc)
        sys.exit(2)
