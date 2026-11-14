"""Explorer fixture for the Factors library page (/factors).

A pure transformation of a ``python -m pipelines.correlation`` run (normally
``--mode research``: the 2024-01-02 research frame and the three backfilled
indices) into the single JSON the page reads. No computation beyond sorting
and selecting, so the page shows exactly what the model wrote.

    python -m pipelines.correlation --mode research --out-dir outputs/correlation_research
    python -m pipelines.correlation.web_fixture \
        --run-dir outputs/correlation_research --out ../web/fixtures/factors.json
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from ..indices.compute import load_compositions
from .factors import FACTOR_NAMES, REGRESSION_FACTORS, RETURN_FACTORS, SPREAD_FACTORS

#: Stocks shown per factor and index: the strongest 63-day co-movers.
LEADERS_PER_FACTOR = 5


def _num(text: str) -> float | None:
    return None if text == "" else float(text)


def build(run_dir: Path) -> dict:
    meta = json.loads((run_dir / "correlation_metadata.json").read_text(encoding="utf-8"))
    with (run_dir / "factor_exposures.csv").open(newline="", encoding="utf-8") as f:
        exposures = list(csv.DictReader(f))
    with (run_dir / "correlation_matrices.csv").open(newline="", encoding="utf-8") as f:
        matrix = list(csv.DictReader(f))
    with (run_dir / "stock_factor_summary.csv").open(newline="", encoding="utf-8") as f:
        stocks = [r for r in csv.DictReader(f) if not r["factor"].startswith("event_")]

    index_ids = sorted({r["index_id"] for r in exposures})
    by_index: dict[str, dict] = {}
    for index_id in index_ids:
        rows = [r for r in exposures if r["index_id"] == index_id]
        by_index[index_id] = {
            "r_squared": _num(rows[0]["r_squared"]),
            "regression_days": int(rows[0]["regression_days"]),
            "dropped_collinear": rows[0]["dropped_collinear"],
            "factors": {
                r["factor"]: {
                    "beta": _num(r["beta"]), "std_error": _num(r["std_error"]),
                    "t_stat": _num(r["t_stat"]), "corr_full": _num(r["corr_full"]),
                    "corr_63d": _num(r["corr_63d"]), "corr_21d": _num(r["corr_21d"]),
                }
                for r in rows
            },
        }

    series = index_ids + list(FACTOR_NAMES)
    corr = {w: {} for w in ("full", "63d", "21d")}
    for r in matrix:
        if r["series_a"] in series and r["series_b"] in series:
            corr[r["window"]][f"{r['series_a']}|{r['series_b']}"] = _num(r["correlation"])

    compositions = load_compositions()
    leaders: dict[str, dict[str, list[dict]]] = {}
    for index_id in index_ids:
        members = set(compositions.get(index_id, []))
        leaders[index_id] = {}
        for factor in sorted({r["factor"] for r in stocks}):
            rows = [r for r in stocks if r["factor"] == factor and r["ticker"] in members
                    and r["corr_63d"] != ""]
            rows.sort(key=lambda r: (-float(r["corr_63d"]), r["ticker"]))
            leaders[index_id][factor] = [
                {"ticker": r["ticker"], "beta_63d": _num(r["beta_63d"]),
                 "corr_63d": _num(r["corr_63d"])}
                for r in rows[:LEADERS_PER_FACTOR]
            ]

    catalog = []
    for name in FACTOR_NAMES:
        proxies = ([RETURN_FACTORS[name]] if name in RETURN_FACTORS
                   else list(SPREAD_FACTORS[name][::-1]))
        catalog.append({
            "factor": name,
            "kind": "return" if name in RETURN_FACTORS else "spread",
            "proxies": proxies,
            "construction": meta["factor_definitions"][name],
            "in_regression": name in REGRESSION_FACTORS,
        })

    return {
        "methodology_version": meta["methodology_version"],
        "source": meta["source"],
        "windows": meta["windows"],
        "index_ids": index_ids,
        "series": series,
        "catalog": catalog,
        "exposures": by_index,
        "correlations": corr,
        "stock_leaders": leaders,
        "sha256": {
            k: v for k, v in sorted(meta.items()) if k.startswith("sha256_")
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m pipelines.correlation.web_fixture")
    parser.add_argument("--run-dir", default="outputs/correlation_research")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    run_dir = Path(args.run_dir)
    if not (run_dir / "correlation_metadata.json").is_file():
        parser.error(f"{run_dir} has no correlation run — run `python -m pipelines.correlation`")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(build(run_dir), indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
