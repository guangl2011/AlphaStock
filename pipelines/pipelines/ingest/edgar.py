"""EDGAR full-text search sweep — V4 pilot leg 1.

Sweeps EDGAR FTS (efts.sec.gov, free since 2001) for filings containing
data-center / energy-expansion language across the covered universe, and maps
each qualifying filing onto the canonical signal-event contract stored in
``db/migrations/0001_signal_store.sql``.

No network in this module: the caller injects recorded API responses, so the
same code paths run live and in CI tests against fixtures.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from pipelines.ingest.source_health import (
    SourceHealthRecord,
    record_failure,
)

CATEGORY = "V4_DATA_CENTER_BUILDOUT"
THEME = "Data Centers"
SOURCE_TYPE = "EDGAR_FTS"
SOURCE_NAME = "edgar-fts"

# Each sweep keyword defines one FTS query; a hit returned for a keyword is by
# construction a match for it, so classification needs no re-fetch of filings.
SWEEP_KEYWORDS = (
    "data center",
    "data center campus",
    "hyperscale",
    "power capacity",
    "energy expansion",
)

# Canonical event contract (signals.event_type CHECK constraint). Filing
# mentions that carry an explicit capacity figure map to capacity_change;
# every other qualifying expansion mention is a first-time project disclosure.
EVENT_NEW_PROJECT = "new_project"
EVENT_CAPACITY_CHANGE = "capacity_change"

# MW / GW figures in filing text; GW is normalized to MW.
_CAPACITY_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(GW|MW|gigawatt|megawatt)s?\b", re.IGNORECASE)

_SIGNAL_ID_PREFIX = "SIG"


class EdgarResponseError(ValueError):
    """The recorded EDGAR FTS response does not match the expected shape."""


@dataclass(frozen=True)
class FilingHit:
    """One EDGAR filing matched by a sweep keyword."""

    cik: str
    accession: str  # e.g. "0001193125-26-123456"
    document: str  # primary document filename
    company: str
    file_type: str
    file_date: str  # ISO date
    keyword: str  # sweep keyword the hit matched
    excerpt: str  # matched text surrounding the keyword
    url: str

    @property
    def capacity_mw(self) -> float | None:
        match = _CAPACITY_RE.search(self.excerpt)
        if not match:
            return None
        value = float(match.group(1))
        return value * 1000.0 if match.group(2).upper() == "GW" else value


def filing_source_url(cik: str, accession: str, document: str) -> str:
    """Stable SEC archive URL for a filing (cik/accession/primary-doc)."""
    accession_nodash = accession.replace("-", "")
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession_nodash}/{document}"


def parse_hit(hit: Mapping[str, object], keyword: str) -> FilingHit:
    """Parse one ``hits.hits[]`` entry from an EDGAR FTS response.

    Raises :class:`EdgarResponseError` on any missing field — a changed API
    shape is a source-health failure, never a silent empty sweep.
    """
    source = hit.get("_source")
    if not isinstance(source, Mapping):
        raise EdgarResponseError(f"EDGAR FTS hit missing _source object: {hit!r}")
    hit_id = str(hit.get("_id", ""))
    if ":" not in hit_id:
        raise EdgarResponseError(
            f"EDGAR FTS hit _id {hit_id!r} missing accession:document form"
        )
    accession, document = hit_id.split(":", 1)

    missing = [
        field
        for field in ("cik", "display_names", "file_type", "file_date")
        if not source.get(field)
    ]
    if missing:
        raise EdgarResponseError(f"EDGAR FTS hit {_id_label(hit_id)} missing field(s): {missing}")

    display_names = source["display_names"]
    company = display_names[0] if isinstance(display_names, Sequence) and display_names else ""
    if not company:
        raise EdgarResponseError(f"EDGAR FTS hit {_id_label(hit_id)} has no display_names")

    excerpt = str(hit.get("_excerpt", "") or source.get("excerpt", "") or "")
    return FilingHit(
        cik=str(source["cik"]),
        accession=accession,
        document=document,
        company=str(company),
        file_type=str(source["file_type"]),
        file_date=str(source["file_date"]),
        keyword=keyword,
        excerpt=excerpt,
        url=filing_source_url(str(source["cik"]), accession, document),
    )


def _id_label(hit_id: str) -> str:
    return hit_id or "<missing _id>"


def classify_filing_event(hit: FilingHit) -> str:
    """Capacity figures → capacity_change; plain expansion mentions → new_project."""
    return EVENT_CAPACITY_CHANGE if hit.capacity_mw is not None else EVENT_NEW_PROJECT


def strength_for(hit: FilingHit) -> int:
    """Deterministic strength: explicit big capacity > any capacity > mention."""
    capacity = hit.capacity_mw
    if capacity is not None and capacity >= 100.0:
        return 4
    if capacity is not None:
        return 3
    return 2


def _bilingual_summaries(hit: FilingHit, tickers: list[str]) -> tuple[str, str]:
    """Deterministic EN/ZH summary drafts; the Writer Agent polishes later."""
    capacity = hit.capacity_mw
    capacity_en = f" Capacity context: ~{capacity:g} MW." if capacity is not None else ""
    capacity_zh = f" 容量参考：约 {capacity:g} 兆瓦。" if capacity is not None else ""
    who = f"{hit.company} ({'/'.join(tickers)})" if tickers else hit.company
    summary_en = (
        f"EDGAR FTS: {who} filed a {hit.file_type} on {hit.file_date} matching "
        f"'{hit.keyword}': {hit.excerpt.strip()}.{capacity_en}"
    )
    summary_zh = (
        f"EDGAR 全文检索：{who} 于 {hit.file_date} 提交 {hit.file_type}，"
        f"命中关键词“{hit.keyword}”：{hit.excerpt.strip()}。{capacity_zh}"
    )
    return summary_en, summary_zh


_INVALIDATION_EN = (
    "Expansion scope is reduced, withdrawn, or materially amended in a subsequent EDGAR filing."
)


def signal_id_for(hit: FilingHit) -> str:
    """Content-addressed id: re-sweeping the same filing yields the same signal."""
    identity = f"{hit.cik}|{hit.accession}|{hit.keyword}|{hit.file_date}"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:8]
    return f"{_SIGNAL_ID_PREFIX}-{hit.file_date.replace('-', '')}-{digest}"


class UniverseLike(Protocol):
    """Minimal shape the loaders provide (data.universe.UniverseEntry)."""

    ticker: str
    company: str


def match_universe(company: str, universe: Sequence[UniverseLike]) -> list[str]:
    """Tickers whose universe company name matches the filing's display name.

    Exact match first; falls back to case/punctuation-insensitive comparison.
    No match returns [] — a theme-level signal is still valid without tickers.
    """
    normalized = _normalize_name(company)
    exact = [e.ticker for e in universe if e.company == company]
    if exact:
        return exact
    return [e.ticker for e in universe if _normalize_name(e.company) == normalized]


def _normalize_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def sweep(
    responses: Sequence[tuple[str, Mapping[str, object]]],
    universe: Sequence[UniverseLike],
    now: datetime,
) -> tuple[list[dict[str, object]], list[SourceHealthRecord]]:
    """Run the sweep over recorded FTS responses.

    Returns ``(signals, health_records)``. Each response is a ``(keyword,
    payload)`` pair as recorded from ``https://efts.sec.gov/LATEST/search-index``.
    A malformed response produces a failed health record for its keyword and
    is excluded from the signal set — the caller decides via ``assert_healthy``
    whether the scan fails (it should). Deduplicated by content-addressed id.
    """
    by_id: dict[str, dict[str, object]] = {}
    health: list[SourceHealthRecord] = []

    for keyword, payload in responses:
        try:
            hits = payload.get("hits")
            if not isinstance(hits, Mapping) or "hits" not in hits:
                raise EdgarResponseError(
                    f"EDGAR FTS response for keyword {keyword!r} missing hits.hits"
                )
            for raw in hits["hits"]:  # type: ignore[index]
                hit = parse_hit(raw, keyword)
                signal = _filing_to_signal(hit, universe, now)
                by_id.setdefault(signal["signal_id"], signal)  # type: ignore[index]
            health.append(
                SourceHealthRecord(
                    source_name=f"{SOURCE_NAME}:{keyword}",
                    status="ok",
                    detail=f"{len(hits['hits'])} hit(s)",  # type: ignore[index]
                )
            )
        except (EdgarResponseError, ValueError) as exc:
            health.append(record_failure(f"{SOURCE_NAME}:{keyword}", str(exc)))

    return list(by_id.values()), health


def _filing_to_signal(
    hit: FilingHit, universe: Sequence[UniverseLike], now: datetime
) -> dict[str, object]:
    tickers = match_universe(hit.company, universe)
    summary_en, summary_zh = _bilingual_summaries(hit, tickers)
    retrieved = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "signal_id": signal_id_for(hit),
        "category": CATEGORY,
        "event_type": classify_filing_event(hit),
        "tickers": tickers,
        "theme": THEME,
        "summary_en": summary_en,
        "summary_zh": summary_zh,
        "direction": "positive",
        "strength": strength_for(hit),
        "horizon_days": 90,
        "macro_overlay": {"rate_sensitivity": "high", "usd_sensitivity": "low"},
        "invalidation": _INVALIDATION_EN,
        "sources": [
            {
                "type": SOURCE_TYPE,
                "url": hit.url,
                "retrieved_at": retrieved,
            }
        ],
        "verified": False,
    }
