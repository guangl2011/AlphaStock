"""Research-frame EOD source: Yahoo Finance via ``yfinance`` (keyless).

Why a second source: the three-year research frame (2024-01-02 forward)
needs ~210 symbols x ~720 sessions in one pull. Tiingo's observed quota
(~50 requests/hour) makes that a multi-hour job, and the backfill is a
*research* artifact — it is never published (methodology §8 publication
gate). Yahoo serves the whole frame in one batched request.

Output contract matches the fixture closes CSV the index engine already
reads (``ticker, universe, date, close, adjClose``) plus a ``source``
column, gzip-compressed and checked in under ``data/research/`` so every
research run after the pull is deterministic and network-free (rule 1).

Field mapping: Yahoo ``Close`` is split-adjusted (not dividend-adjusted)
and ``Adj Close`` is split+dividend adjusted. Because splits are already
folded into ``Close``, raw-vs-adjusted divergence only fires on large
distributions; the engine's 2% corporate-action threshold still logs
those loudly. Live Tiingo mode is untouched.

Network use happens only in :func:`fetch_frame` (``--refresh``); nothing
in CI or the default research run calls it.
"""

from __future__ import annotations

import csv
import gzip
import io
import logging
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)

SOURCE_LABEL = "yahoo-finance (yfinance), research frame"


def fetch_frame(
    tickers: list[str], start: date, end_inclusive: date
) -> dict[str, list[tuple[date, float, float]]]:
    """One batched Yahoo pull: {ticker: [(date, close, adjClose), ...]}.

    Tickers with no rows come back as empty lists — the caller reconciles
    them against ``index_exclusions.csv``; nothing is dropped silently.
    """
    try:
        import yfinance as yf  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - env-dependent
        raise RuntimeError(
            "yfinance is not installed — `pip install -e '.[research]'` to refresh "
            "the research frame (the default run reads the checked-in file)"
        ) from exc

    end_exclusive = date.fromordinal(end_inclusive.toordinal() + 1)
    frame = yf.download(
        sorted(tickers), start=start.isoformat(), end=end_exclusive.isoformat(),
        auto_adjust=False, progress=False, group_by="ticker", threads=True,
    )
    out: dict[str, list[tuple[date, float, float]]] = {}
    for ticker in sorted(tickers):
        rows: list[tuple[date, float, float]] = []
        if ticker in frame.columns.get_level_values(0):
            sub = frame[ticker][["Close", "Adj Close"]].dropna()
            for ts, (close, adj) in sub.iterrows():
                rows.append((ts.date(), float(close), float(adj)))
        if not rows:
            logger.warning("research frame: %s returned no rows", ticker)
        out[ticker] = rows
    return out


def frame_csv(
    rows: dict[str, list[tuple[date, float, float]]], universe_of: dict[str, str]
) -> str:
    """Deterministic CSV text: sorted by (ticker, date), fixed float format."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["ticker", "universe", "date", "close", "adjClose", "source"])
    for ticker in sorted(rows):
        for d, close, adj in sorted(rows[ticker]):
            writer.writerow(
                [ticker, universe_of.get(ticker, ""), d.isoformat(),
                 f"{close:.6f}", f"{adj:.6f}", "yahoo"]
            )
    return buf.getvalue()


def write_frame(path: Path, text: str) -> None:
    """Gzip with mtime=0 so identical data produces identical bytes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as raw, gzip.GzipFile(
        filename="", mode="wb", fileobj=raw, mtime=0
    ) as gz:
        gz.write(text.encode("utf-8"))


def read_frame_text(path: Path) -> str:
    with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
        return f.read()
