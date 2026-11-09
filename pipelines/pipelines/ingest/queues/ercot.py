"""Custom ERCOT parser: milestone-driven status resolution.

ERCOT's GIS-report workbook carries per-milestone columns that gridstatus's
generic Status enum collapses into Active/Completed (grounded from gridstatus
0.37.0's ``Ercot`` parsing code, which derives Status purely from whether
``IA Signed`` is null). The parser resolves the canonical rung from the
milestones — withdrawal date, approved-for-synchronization, IA signed, FIS
approved, study progress — and falls back to the per-ISO YAML mapping on the
native Status only when no milestone fires. Rows from the cancelled/inactive
sheets arrive with a Withdrawn Date set (gridstatus maps their Inactive/Cancel
dates onto that column), which the withdrawal check handles first.

``Approved for Energization`` is deliberately not a derivation input: its
ordering relative to IA signing is ambiguous in the source, while ``Approved
for Synchronization`` is unambiguous energization evidence.
"""

from __future__ import annotations

from typing import Any

from .adapters import GridstatusQueueAdapter, first_milestone
from .ladder import WITHDRAWN, normalize_status

#: Milestone columns, most-advanced last. Presence of any asserts the studied rung.
_STUDY_COLUMNS: tuple[str, ...] = (
    "GIM Study Phase",
    "Screening Study Started",
    "Screening Study Complete",
    "FIS Requested",
)


class ERCOTQueueAdapter(GridstatusQueueAdapter):
    """ERCOT: milestone-first canonical status, YAML fallback on native Status."""

    iso = "ERCOT"

    def _resolve_status(
        self, raw: dict[str, Any], native_status: str, project_id: str
    ) -> str:
        if raw.get("Withdrawn Date") is not None:
            return WITHDRAWN
        if raw.get("Approved for Synchronization") is not None:
            return "energized"
        if raw.get("IA Signed") is not None:
            return "esa_signed"
        if raw.get("FIS Approved") is not None:
            return "approved"
        if first_milestone(raw, _STUDY_COLUMNS) is not None:
            return "studied"
        return normalize_status(self.iso, native_status, project_id)
