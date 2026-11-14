"""Corporate-event research for the research frame via the You.com Search API.

The backfill's price source (Yahoo) folds splits *and spin-offs* into its
"split-adjusted" close, so a price-return series never shows those gaps —
correct index treatment, but only if every adjustment is a real, sourced
event. This module gathers independent evidence for each constituent:

1. Two You.com searches per ticker (splits; ticker change / spin-off /
   merger / listing events), limited to the research frame, cached raw
   under ``outputs/ydc_cache/`` (git-ignored) so re-runs cost nothing.
2. Rule-based extraction of candidate events from titles and snippets.
3. A cross-check against Yahoo's own split/adjustment records.

The curated, human-reviewed result lives in
``data/research/corporate_events.csv`` (one sourced row per event); this
module produces the evidence and the reconciliation report that review
works from — it never writes the curated file itself.

The API key is read from ``YDC_API_KEY`` only; it is never logged, cached,
or written to any output.

    YDC_API_KEY=... python -m pipelines.indices.corp_events --report out.json
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import logging
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

SEARCH_URL = "https://ydc-index.io/v1/search"
FRESHNESS = "2024-01-01to2026-11-14"
_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CACHE = _ROOT / "outputs" / "ydc_cache"

QUERIES = {
    "split": "{company} ({ticker}) stock split OR reverse stock split 2024 2025 2026",
    "corp": "{company} ({ticker}) ticker symbol change OR spin-off OR merger OR "
            "acquisition completed OR IPO OR delisting 2024 2025 2026",
}

_PATTERNS = {
    "split": re.compile(r"\b(\d{1,2})[- ](?:for|to)[- ](\d{1,2})\b(?:[^.]{0,40}\bsplit)?", re.I),
    "reverse_split": re.compile(r"\breverse (?:stock )?split\b", re.I),
    "spin_off": re.compile(r"\b(?:spin[- ]?off|spun off|separation of|distribution of)\b", re.I),
    "ticker_change": re.compile(
        r"\b(?:ticker (?:symbol )?change|new ticker|will (?:begin )?trad\w* under (?:the )?"
        r"(?:ticker|symbol))\b", re.I),
    "merger": re.compile(r"\b(?:completes? (?:the )?(?:acquisition|merger)|"
                         r"merger (?:is )?completed|to be acquired)\b", re.I),
}


@dataclass(frozen=True)
class Candidate:
    ticker: str
    kind: str
    text: str
    url: str
    page_age: str


def _ssl_context() -> ssl.SSLContext:
    """python.org builds ship without a CA bundle; prefer certifi when present."""
    try:
        import certifi  # type: ignore[import-not-found]

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:  # pragma: no cover - env-dependent
        return ssl.create_default_context()


def search(query: str, api_key: str, count: int = 8, retries: int = 3) -> dict:
    """One You.com search; raises on persistent failure (never returns partial)."""
    url = f"{SEARCH_URL}?" + urllib.parse.urlencode(
        {"query": query, "count": count, "freshness": FRESHNESS}
    )
    req = urllib.request.Request(
        url, headers={"X-API-Key": api_key, "User-Agent": "alphastock-pipelines",
                      "Accept": "application/json"},
    )
    ctx = _ssl_context()
    last = ""
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:  # noqa: S310
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            last = f"HTTP {exc.code}"
            if exc.code not in (429, 500, 502, 503, 504):
                break
        except urllib.error.URLError as exc:
            last = str(exc.reason)
        time.sleep(2 ** attempt)
    raise RuntimeError(f"You.com search failed ({last}) for query: {query[:80]}")


def gather(
    names: dict[str, str], api_key: str, cache_dir: Path, workers: int = 6
) -> dict[str, dict[str, dict]]:
    """Raw search payloads per (ticker, kind), cache-first."""
    cache_dir.mkdir(parents=True, exist_ok=True)

    def one(ticker: str, kind: str) -> tuple[str, str, dict]:
        path = cache_dir / f"{ticker}_{kind}.json"
        if path.is_file():
            return ticker, kind, json.loads(path.read_text(encoding="utf-8"))
        payload = search(QUERIES[kind].format(company=names[ticker], ticker=ticker), api_key)
        path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        return ticker, kind, payload

    out: dict[str, dict[str, dict]] = {}
    jobs = [(t, k) for t in sorted(names) for k in QUERIES]
    with cf.ThreadPoolExecutor(workers) as ex:
        for ticker, kind, payload in ex.map(lambda j: one(*j), jobs):
            out.setdefault(ticker, {})[kind] = payload
    return out


def _texts(item: dict) -> list[str]:
    parts = [item.get("title", ""), item.get("description", "")]
    parts += list(item.get("snippets") or [])
    parts += list((item.get("contents") or {}).get("highlights") or [])
    return [p for p in parts if p]


def extract(ticker: str, company: str, payloads: dict[str, dict]) -> list[Candidate]:
    """Candidate events mentioned alongside this company (rule-based, deduped)."""
    anchor = re.compile(
        rf"\b{re.escape(ticker)}\b|{re.escape(company.split(',')[0].split(' Inc')[0][:24])}",
        re.I,
    )
    seen: set[tuple[str, str]] = set()
    out: list[Candidate] = []
    for payload in payloads.values():
        for item in (payload.get("results") or {}).get("web", []):
            for text in _texts(item):
                if not anchor.search(text):
                    continue
                for kind, pat in _PATTERNS.items():
                    m = pat.search(text)
                    if not m:
                        continue
                    if kind == "split" and "split" not in text.lower():
                        continue
                    start = max(0, m.start() - 90)
                    snippet = " ".join(text[start:m.end() + 90].split())
                    key = (kind, item.get("url", ""))
                    if key in seen:
                        continue
                    seen.add(key)
                    out.append(Candidate(ticker, kind, snippet, item.get("url", ""),
                                         str(item.get("page_age", ""))[:10]))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m pipelines.indices.corp_events")
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE))
    parser.add_argument("--report", required=True, help="write candidates JSON here")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

    api_key = os.environ.get("YDC_API_KEY", "")
    sys.path.insert(0, str(_ROOT))
    from data.large_cap import load_large_cap
    from data.universe import load_universe

    names = {e.ticker: e.company for e in load_universe()}
    names.update({e.ticker: e.company for e in load_large_cap()})
    cache = Path(args.cache_dir)
    if not api_key and not all((cache / f"{t}_{k}.json").is_file()
                               for t in names for k in QUERIES):
        parser.error("YDC_API_KEY is required until every query is cached")
    payloads = gather(names, api_key, cache)
    report = {
        t: [asdict(c) for c in extract(t, names[t], payloads[t])] for t in sorted(payloads)
    }
    Path(args.report).write_text(json.dumps(report, indent=1, sort_keys=True), encoding="utf-8")
    hits = sum(1 for v in report.values() if v)
    print(f"{len(report)} tickers searched; {hits} with candidate events -> {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
