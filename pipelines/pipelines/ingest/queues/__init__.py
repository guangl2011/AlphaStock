"""Interconnection-queue adapters: one per ISO/RTO, one canonical project shape."""

from __future__ import annotations

from datetime import datetime

from .adapters import (
    NORMALIZED_COLUMNS,
    CAISOQueueAdapter,
    GridstatusQueueAdapter,
    ISONEQueueAdapter,
    MISOQueueAdapter,
    NYISOQueueAdapter,
    SPPQueueAdapter,
)
from .base import (
    ALL_ISOS,
    QUEUE_SOURCE_URLS,
    QueueAdapter,
    QueueDataError,
    QueueProject,
    RecordSource,
)
from .ercot import ERCOTQueueAdapter
from .ladder import (
    CANONICAL_ORDER,
    CANONICAL_STATUSES,
    RUNG_ZH,
    WITHDRAWN,
    StatusMapping,
    UnmappedStatusError,
    ladder_rank,
    mapping_for,
    normalize_status,
)
from .pjm import PJMQueueAdapter
from .sources import make_gridstatus_records_source

#: Adapter class per canonical ISO name. PJM and ERCOT carry custom parsers;
#: the other five ride gridstatus's normalized queue with YAML status mapping.
ADAPTER_CLASSES: dict[str, type[QueueAdapter]] = {
    "PJM": PJMQueueAdapter,
    "ERCOT": ERCOTQueueAdapter,
    "MISO": MISOQueueAdapter,
    "CAISO": CAISOQueueAdapter,
    "SPP": SPPQueueAdapter,
    "NYISO": NYISOQueueAdapter,
    "ISO-NE": ISONEQueueAdapter,
}


def build_adapter(
    iso: str,
    records_source: RecordSource | None = None,
    retrieved_at: datetime | None = None,
) -> QueueAdapter:
    """Instantiate the adapter for ``iso``, optionally with an injected source."""
    try:
        adapter_cls = ADAPTER_CLASSES[iso]
    except KeyError as exc:
        raise KeyError(f"unknown ISO {iso!r}; expected one of {ALL_ISOS}") from exc
    return adapter_cls(records_source, retrieved_at)


def build_all_adapters(
    records_sources: dict[str, RecordSource] | None = None,
    retrieved_at: datetime | None = None,
) -> list[QueueAdapter]:
    """Instantiate all seven adapters; ``records_sources`` injects per-ISO fixtures."""
    sources = records_sources or {}
    return [build_adapter(iso, sources.get(iso), retrieved_at) for iso in ALL_ISOS]


__all__ = [
    "ADAPTER_CLASSES",
    "ALL_ISOS",
    "CANONICAL_ORDER",
    "CANONICAL_STATUSES",
    "NORMALIZED_COLUMNS",
    "QUEUE_SOURCE_URLS",
    "RUNG_ZH",
    "RecordSource",
    "StatusMapping",
    "UnmappedStatusError",
    "WITHDRAWN",
    "GridstatusQueueAdapter",
    "ISONEQueueAdapter",
    "MISOQueueAdapter",
    "NYISOQueueAdapter",
    "QueueAdapter",
    "QueueDataError",
    "QueueProject",
    "build_adapter",
    "build_all_adapters",
    "ladder_rank",
    "make_gridstatus_records_source",
    "mapping_for",
    "normalize_status",
]
