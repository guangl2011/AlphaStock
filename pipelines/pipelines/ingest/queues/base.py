"""Canonical queue-project model and the adapter contract for the V2 pipeline.

Every ISO/RTO adapter — whether gridstatus-backed or a custom PJM/ERCOT
parser — reduces its source to ``QueueProject`` rows. The unit of signal is
the status transition between scans, so adapters resolve each project's
native status through the per-ISO mapping table (``ladder.py``): an unmapped
status raises and fails the scan loudly, never silently bucketed to unknown.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

#: Canonical ISO identifiers, matching the signals table and per-ISO mappings.
ALL_ISOS: tuple[str, ...] = ("PJM", "ERCOT", "MISO", "CAISO", "SPP", "NYISO", "ISO-NE")

#: Where each raw queue is published. Grounded from gridstatus 0.37.0 class
#: attributes (``interconnection_homepage``); used as per-project source_url
#: so every signal carries provenance.
QUEUE_SOURCE_URLS: dict[str, str] = {
    "PJM": "https://www.pjm.com/planning/service-requests/services-request-status",
    "ERCOT": "http://mis.ercot.com/misapp/GetReports.do?reportTypeId=15933",
    "MISO": "https://www.misoenergy.org/planning/generator-interconnection/GI_Queue/",
    "CAISO": "https://rimspub.caiso.com/rimsui/logon.do",
    "SPP": "https://www.spp.org/engineering/generator-interconnection/",
    "NYISO": "https://www.nyiso.com/interconnections",
    "ISO-NE": "https://irtt.iso-ne.com/reports/external",
}

#: QueueProject.load_type values (design doc §4.2). Adapters set ``unknown``
#: unless the source states the service type; the Signal Agent stage refines.
LOAD_TYPE_GENERATION = "generation"
LOAD_TYPE_LARGE_LOAD = "large_load"
LOAD_TYPE_MIXED = "mixed"
LOAD_TYPE_UNKNOWN = "unknown"

#: Records source: returns the raw queue as plain JSON-safe dicts. The default
#: builds on gridstatus's normalized queue DataFrame; tests inject fixtures.
RecordSource = Callable[[], list[dict[str, Any]]]


class QueueDataError(ValueError):
    """A source record is structurally unusable (missing id, absent status, ...)."""


@dataclass(frozen=True)
class QueueProject:
    """One project row from an ISO/RTO queue, normalized to the V2 shape."""

    iso: str
    project_id: str
    name: str
    capacity_mw: float | None
    load_type: str
    data_center_likelihood: float
    status: str  # ISO-native raw status string
    status_norm: str  # canonical ladder node (see ladder.py)
    county: str | None
    state: str | None
    raw: dict[str, Any]  # full raw record — provenance is non-negotiable
    source_url: str
    retrieved_at: datetime


class QueueAdapter(ABC):
    """One adapter per ISO/RTO.

    Subclasses implement :meth:`to_projects` (pure: records → projects). The
    shared :meth:`fetch` loads records from the injected source — the default
    lazily imports gridstatus so tests and CI never touch the network.
    """

    iso: str

    def __init__(
        self,
        records_source: RecordSource | None = None,
        retrieved_at: datetime | None = None,
    ) -> None:
        self._records_source = records_source
        self.retrieved_at = retrieved_at or datetime.now(tz=UTC)

    @abstractmethod
    def to_projects(self, records: list[dict[str, Any]]) -> list[QueueProject]:
        """Reduce raw source records to canonical projects (pure, no I/O)."""

    def fetch(self) -> list[QueueProject]:
        """Load a snapshot and reduce it. Never returns quietly-partial data."""
        records = self._load_records()
        projects = self.to_projects(records)
        seen: set[str] = set()
        for project in projects:
            if project.project_id in seen:
                raise QueueDataError(
                    f"{self.iso}: duplicate project_id in one snapshot: {project.project_id!r}"
                )
            seen.add(project.project_id)
        return projects

    def _load_records(self) -> list[dict[str, Any]]:
        if self._records_source is not None:
            return self._records_source()
        from .sources import make_gridstatus_records_source

        return make_gridstatus_records_source(self.iso)()


def json_safe_record(record: dict[str, Any]) -> dict[str, Any]:
    """Coerce one raw source record to JSON-safe plain types.

    Values arriving from gridstatus DataFrames are numpy scalars, pandas
    timestamps, or NaN; provenance records are hashed and stored as JSONB, so
    they must be plain. Duck-typing keeps this free of numpy/pandas imports.
    """
    return {key: json_safe_value(value) for key, value in record.items()}


def json_safe_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool)):
        return value
    if value != value:  # NaN and pandas.NaT compare unequal to themselves
        return None
    if isinstance(value, float):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    item = getattr(value, "item", None)  # numpy scalars
    if callable(item):
        return json_safe_value(item())
    iso = getattr(value, "isoformat", None)  # pandas.Timestamp and friends
    if callable(iso):
        return iso()
    if isinstance(value, dict):
        return {str(k): json_safe_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe_value(v) for v in value]
    return str(value)


def text_field(record: dict[str, Any], column: str) -> str | None:
    """Whitespace-trimmed text of a raw column; blank and missing become None."""
    value = record.get(column)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def milestone_field(record: dict[str, Any], column: str) -> str | None:
    """A milestone cell that counts as reached.

    Y/N-style columns report "N" as a present value; it must not count as a
    reached milestone. Dates and flags both flow through this gate.
    """
    text = text_field(record, column)
    if text is None:
        return None
    if text.lower() in {"n", "no", "false", "none", "null"}:
        return None
    return text


def capacity_mw_field(record: dict[str, Any]) -> float | None:
    """Reported capacity, preferring the combined column over season splits."""
    for column in ("Capacity (MW)",):
        value = text_field(record, column)
        if value is not None:
            return _as_float(value, column)
    seasonal = [
        _as_float(text_field(record, c), c)
        for c in ("Summer Capacity (MW)", "Winter Capacity (MW)")
        if text_field(record, c) is not None
    ]
    seasonal = [v for v in seasonal if v is not None and not math.isnan(v)]
    return max(seasonal) if seasonal else None


def _as_float(value: str, column: str) -> float | None:
    try:
        return float(value.replace(",", ""))
    except ValueError as exc:
        raise QueueDataError(f"non-numeric {column!r} value: {value!r}") from exc
