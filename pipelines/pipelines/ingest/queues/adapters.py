"""The generic gridstatus-backed queue adapter and the five standard ISOs.

All seven ISO adapters share this reduction from gridstatus's normalized
queue record to :class:`QueueProject`. PJM and ERCOT subclass it with custom
milestone-driven status resolution (their workbooks carry per-study columns
that gridstatus's generic Status enum collapses — see pjm.py / ercot.py); the
other five ISOs use the base resolution, which normalizes the native Status
through the per-ISO YAML mapping table.
"""

from __future__ import annotations

from typing import Any

from .base import (
    LOAD_TYPE_UNKNOWN,
    QUEUE_SOURCE_URLS,
    QueueAdapter,
    QueueDataError,
    QueueProject,
    capacity_mw_field,
    milestone_field,
    text_field,
)
from .ladder import normalize_status

#: gridstatus's normalized queue columns (gridstatus.utils._interconnection_columns).
NORMALIZED_COLUMNS: tuple[str, ...] = (
    "Queue ID",
    "Project Name",
    "Interconnecting Entity",
    "County",
    "State",
    "Interconnection Location",
    "Transmission Owner",
    "Generation Type",
    "Capacity (MW)",
    "Summer Capacity (MW)",
    "Winter Capacity (MW)",
    "Queue Date",
    "Status",
    "Proposed Completion Date",
    "Withdrawn Date",
    "Withdrawal Comment",
    "Actual Completion Date",
)


def first_milestone(raw: dict[str, Any], columns: tuple[str, ...]) -> str | None:
    """First reached milestone among ``columns``, else None (Y/N-safe gate)."""
    for column in columns:
        reached = milestone_field(raw, column)
        if reached is not None:
            return reached
    return None


class GridstatusQueueAdapter(QueueAdapter):
    """Reduces gridstatus-normalized queue records to canonical projects.

    Concrete subclasses pin ``iso``. Status resolution defaults to the
    per-ISO YAML mapping on the native Status column.
    """

    iso: str

    def to_projects(self, records: list[dict[str, Any]]) -> list[QueueProject]:
        return [self._project_from_record(record) for record in records]

    def _project_from_record(self, record: dict[str, Any]) -> QueueProject:
        raw = record  # already json-safe; callers pass fixture or gridstatus rows
        project_id = text_field(raw, "Queue ID")
        if project_id is None:
            raise QueueDataError(f"{self.iso}: record without Queue ID: {raw!r}")
        native_status = text_field(raw, "Status")
        if native_status is None:
            raise QueueDataError(f"{self.iso} project {project_id!r}: record without Status")
        return QueueProject(
            iso=self.iso,
            project_id=project_id,
            name=text_field(raw, "Project Name") or project_id,
            capacity_mw=capacity_mw_field(raw),
            load_type=LOAD_TYPE_UNKNOWN,
            data_center_likelihood=0.0,  # Signal Agent (LLM stage) classifies later
            status=native_status,
            status_norm=self._resolve_status(raw, native_status, project_id),
            county=text_field(raw, "County"),
            state=text_field(raw, "State"),
            raw=raw,
            source_url=QUEUE_SOURCE_URLS[self.iso],
            retrieved_at=self.retrieved_at,
        )

    def _resolve_status(
        self, raw: dict[str, Any], native_status: str, project_id: str
    ) -> str:
        """Default resolution: the per-ISO YAML mapping on the native Status."""
        return normalize_status(self.iso, native_status, project_id)


class MISOQueueAdapter(GridstatusQueueAdapter):
    """MISO: native applicationStatus via gridstatus, YAML-mapped."""

    iso = "MISO"


class CAISOQueueAdapter(GridstatusQueueAdapter):
    """CAISO: workbook application status via gridstatus, YAML-mapped."""

    iso = "CAISO"


class SPPQueueAdapter(GridstatusQueueAdapter):
    """SPP: gridstatus pre-maps raw statuses to its generic enum, YAML-mapped."""

    iso = "SPP"


class NYISOQueueAdapter(GridstatusQueueAdapter):
    """NYISO: statuses assigned by sheet membership, YAML-mapped."""

    iso = "NYISO"


class ISONEQueueAdapter(GridstatusQueueAdapter):
    """ISO-NE: one-letter queue statuses pre-mapped by gridstatus, YAML-mapped."""

    iso = "ISO-NE"
