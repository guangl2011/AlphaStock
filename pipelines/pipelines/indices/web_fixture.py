"""Web Explorer fixture export from pipeline run artifacts.

Merges ``comparison.json`` (index + benchmark-proxy levels) with
``index_metadata.json`` (base dates, constituent counts, exclusions) into
the single JSON file the Explorer's ``/indices`` page reads in fixture
mode. A pure transformation of pipeline outputs — no computation, no
network — so the committed web fixture is reproducible offline in two
commands::

    python -m pipelines.indices --mode=fixture --out-dir=outputs/index
    python -m pipelines.indices.web_fixture \
        --pipeline-out-dir=outputs/index --out=web/fixtures/index.json

Only the benchmarks each index is actually compared against (per the
methodology's benchmark sets) are kept; the thematic comparators in the
full proxy map are dropped from the web payload.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .benchmarks import INDEX_BENCHMARKS


def _sorted_levels(raw: dict[str, str]) -> dict[str, str]:
    return dict(sorted(raw.items()))


def build_fixture(comparison: dict, metadata: dict) -> dict:
    """Merge the two pipeline artifacts into the web fixture payload.

    Raises ``ValueError`` when the inputs disagree (unknown index id,
    missing benchmark series, no exclusion record where one is required) —
    a malformed merge must fail loudly, never ship a partial demo.
    """
    meta_by_id = {i["index_id"]: i for i in metadata["indices"]}
    series = comparison["series"]

    unknown = sorted(set(series) ^ set(meta_by_id))
    if unknown:
        raise ValueError(f"comparison/metadata index ids disagree: {unknown}")

    indices: dict[str, dict] = {}
    for index_id in sorted(series):
        meta = meta_by_id[index_id]
        payload = series[index_id]
        benchmark_levels = payload["benchmarks"]
        missing = sorted(set(INDEX_BENCHMARKS[index_id]) - set(benchmark_levels))
        if missing:
            raise ValueError(f"{index_id}: comparison export is missing benchmarks {missing}")
        kept = {p: benchmark_levels[p] for p in sorted(INDEX_BENCHMARKS[index_id])}
        indices[index_id] = {
            "index_id": index_id,
            "base_date": meta["base_date"],
            "constituent_count": meta["constituent_count"],
            "levels": _sorted_levels(payload["levels"]),
            "benchmarks": {
                proxy: {
                    "name": kept[proxy]["name"],
                    "levels": _sorted_levels(kept[proxy]["levels"]),
                }
                for proxy in sorted(kept)
            },
            "date_gaps": sorted(payload.get("date_gaps", [])),
            "excluded": sorted(
                ({"ticker": e["ticker"], "reason": e["reason"]} for e in meta["excluded"]),
                key=lambda e: e["ticker"],
            ),
        }

    # Proxy names used on the page, trimmed to the displayed benchmark set.
    proxy_mapping = {
        proxy: comparison["benchmark_proxy_mapping"][proxy]
        for index in indices.values()
        for proxy in index["benchmarks"]
    }

    return {
        "methodology_version": metadata["methodology_version"],
        "source": metadata["source"],
        "benchmark_proxy_mapping": dict(sorted(proxy_mapping.items())),
        "indices": indices,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m pipelines.indices.web_fixture",
        description="Merge pipeline index artifacts into the Explorer's web fixture JSON.",
    )
    parser.add_argument(
        "--pipeline-out-dir",
        default="outputs/index",
        help="directory a `pipelines.indices` run wrote (comparison.json, index_metadata.json)",
    )
    parser.add_argument("--out", required=True, help="path of the web fixture JSON to write")
    args = parser.parse_args(argv)

    out_dir = Path(args.pipeline_out_dir)
    comparison_path = out_dir / "comparison.json"
    metadata_path = out_dir / "index_metadata.json"
    for path in (comparison_path, metadata_path):
        if not path.is_file():
            parser.error(f"{path} not found — run `python -m pipelines.indices` first")

    comparison = json.loads(comparison_path.read_text(encoding="utf-8"))
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    try:
        fixture = build_fixture(comparison, metadata)
    except (KeyError, TypeError, ValueError) as exc:
        print(f"web fixture build failed: {exc}", file=sys.stderr)
        return 1

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(fixture, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
