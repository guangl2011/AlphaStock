"""County permitting watchlist — V4 pilot leg 2.

Monitors permit / development announcements in a curated 10-20 county
watchlist of data-center-heavy jurisdictions (config, not code:
``data/county_watchlist.csv`` is PR-reviewed). Announcements in watchlist
counties map onto the canonical signal-event contract in
``db/migrations/0001_signal_store.sql``.

No network in this module: the caller injects announcement records scraped
by the connector layer, so tests run against fixtures.
"""

from __future__ import annotations

import csv
import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from pipelines.ingest.source_health import (
    SourceHealthRecord,
    record_failure,
)

CATEGORY = "V4_DATA_CENTER_BUILDOUT"
THEME = "Data Centers"
SOURCE_TYPE = "COUNTY_PERMIT"
SOURCE_NAME = "county-watchlist"

EVENT_NEW_PROJECT = "new_project"

WATCHLIST_CSV = Path(__file__).resolve().parents[2] / "data" / "county_watchlist.csv"

# The pilot is deliberately narrow (build spec: "10-20 county watchlist");
# the loader enforces the band so scope creep or silent shrinkage fails CI.
MIN_COUNTIES = 10
MAX_COUNTIES = 20

CAPACITY_BULK_MW = 100.0

_CAPACITY_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(GW|MW|gigawatt|megawatt)s?\b", re.IGNORECASE)

_REQUIRED_ANNOUNCEMENT_FIELDS = ("county", "state", "date", "headline", "url")


class WatchlistValidationError(ValueError):
    """Raised when county_watchlist.csv does not meet the pilot contract."""


class AnnouncementError(ValueError):
    """Raised when an announcement record is malformed — never dropped silently."""


@dataclass(frozen=True)
class WatchlistCounty:
    county: str
    state: str
    region_note: str
    source_url: str

    @property
    def key(self) -> str:
        return county_key(self.county, self.state)


@dataclass(frozen=True)
class PermitAnnouncement:
    county: str
    state: str
    date: str  # ISO date of the announcement
    headline: str
    detail: str
    url: str

    @property
    def key(self) -> str:
        return county_key(self.county, self.state)

    @property
    def capacity_mw(self) -> float | None:
        match = _CAPACITY_RE.search(self.detail)
        if not match:
            return None
        value = float(match.group(1))
        return value * 1000.0 if match.group(2).upper() == "GW" else value


def county_key(county: str, state: str) -> str:
    return f"{county.strip()}, {state.strip()}"


def load_watchlist(path: Path | str = WATCHLIST_CSV) -> list[WatchlistCounty]:
    """Load and validate the county watchlist.

    Raises :class:`WatchlistValidationError` on missing columns, empty fields,
    duplicate counties, or a county count outside the 10-20 pilot band.
    """
    path = Path(path)
    if not path.is_file():
        raise WatchlistValidationError(f"county watchlist file not found: {path}")
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames or []
        missing = [c for c in ("county", "state", "region_note", "source_url") if c not in header]
        if missing:
            raise WatchlistValidationError(f"county watchlist missing column(s): {missing}")
        rows = [dict(r) for r in reader]

    counties: list[WatchlistCounty] = []
    seen: set[str] = set()
    for i, row in enumerate(rows, start=2):
        columns = ("county", "state", "region_note", "source_url")
        fields = {c: row.get(c, "").strip() for c in columns}
        empty = [c for c, v in fields.items() if not v]
        if empty:
            raise WatchlistValidationError(f"county watchlist line {i}: empty field(s) {empty}")
        key = county_key(fields["county"], fields["state"])
        if key in seen:
            raise WatchlistValidationError(f"county watchlist line {i}: duplicate county {key!r}")
        seen.add(key)
        counties.append(
            WatchlistCounty(
                county=fields["county"],
                state=fields["state"],
                region_note=fields["region_note"],
                source_url=fields["source_url"],
            )
        )

    if not MIN_COUNTIES <= len(counties) <= MAX_COUNTIES:
        raise WatchlistValidationError(
            f"county watchlist must hold {MIN_COUNTIES}-{MAX_COUNTIES} counties, "
            f"found {len(counties)}"
        )
    return counties


def parse_announcements(records: list[dict[str, object]]) -> list[PermitAnnouncement]:
    """Parse announcement records loudly — malformed records raise, never drop."""
    parsed: list[PermitAnnouncement] = []
    for i, record in enumerate(records, start=1):
        missing = [f for f in _REQUIRED_ANNOUNCEMENT_FIELDS if not str(record.get(f, "")).strip()]
        if missing:
            raise AnnouncementError(f"announcement record {i}: missing field(s) {missing}")
        parsed.append(
            PermitAnnouncement(
                county=str(record["county"]).strip(),
                state=str(record["state"]).strip(),
                date=str(record["date"]).strip(),
                headline=str(record["headline"]).strip(),
                detail=str(record.get("detail", "")).strip(),
                url=str(record["url"]).strip(),
            )
        )
    return parsed


def strength_for(announcement: PermitAnnouncement) -> int:
    capacity = announcement.capacity_mw
    if capacity is not None and capacity >= CAPACITY_BULK_MW:
        return 4
    if capacity is not None:
        return 3
    return 2


def _bilingual_summaries(a: PermitAnnouncement) -> tuple[str, str]:
    capacity = a.capacity_mw
    capacity_en = f" Capacity context: ~{capacity:g} MW." if capacity is not None else ""
    capacity_zh = f" 容量参考：约 {capacity:g} 兆瓦。" if capacity is not None else ""
    summary_en = f"{a.key}: {a.headline}. {a.detail}{capacity_en}"
    summary_zh = f"{a.key}：{a.headline}。{a.detail}{capacity_zh}"
    return summary_en, summary_zh


_INVALIDATION_EN = (
    "The permit application is withdrawn, denied, or the project is paused beyond two quarters."
)


def signal_id_for(a: PermitAnnouncement) -> str:
    """Content-addressed id: re-ingesting the same announcement is idempotent."""
    identity = f"{a.key}|{a.date}|{a.url}"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:8]
    return f"SIG-{a.date.replace('-', '')}-{digest}"


def ingest_announcements(
    records: list[dict[str, object]],
    watchlist: list[WatchlistCounty],
    now: datetime,
) -> tuple[list[dict[str, object]], list[SourceHealthRecord]]:
    """Convert announcement records into canonical V4 signal events.

    Announcements outside the watchlist are a config decision (the county is
    not monitored) and are skipped by design — that is not a failure.
    Malformed records and watchlist-shape violations are failures via the
    raised errors; the returned health records cover the batch outcome.
    """
    watch_keys = {c.key for c in watchlist}
    signals: dict[str, dict[str, object]] = {}
    health: list[SourceHealthRecord] = []

    try:
        parsed = parse_announcements(records)
    except AnnouncementError as exc:
        return [], [record_failure(SOURCE_NAME, str(exc))]

    skipped = 0
    for a in parsed:
        if a.key not in watch_keys:
            skipped += 1
            continue
        summary_en, summary_zh = _bilingual_summaries(a)
        signals[signal_id_for(a)] = {
            "signal_id": signal_id_for(a),
            "category": CATEGORY,
            "event_type": EVENT_NEW_PROJECT,
            "tickers": [],
            "theme": THEME,
            "summary_en": summary_en,
            "summary_zh": summary_zh,
            "direction": "positive",
            "strength": strength_for(a),
            "horizon_days": 180,
            "macro_overlay": {"rate_sensitivity": "high", "usd_sensitivity": "low"},
            "invalidation": _INVALIDATION_EN,
            "sources": [
                {
                    "type": SOURCE_TYPE,
                    "url": a.url,
                    "retrieved_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                }
            ],
            "verified": False,
        }

    health.append(
        SourceHealthRecord(
            source_name=SOURCE_NAME,
            status="ok",
            detail=f"{len(signals)} event(s), {skipped} non-watchlist record(s) skipped",
        )
    )
    return list(signals.values()), health
