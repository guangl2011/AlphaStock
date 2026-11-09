"""Custom PJM parser: milestone-driven status resolution.

PJM's queue workbook carries per-study columns that gridstatus's generic
Status enum collapses (column names grounded from gridstatus 0.37.0's
``PJM.get_interconnection_queue`` rename/extra lists). The parser resolves
the canonical rung from those milestones first — withdrawal date, actual
in-service date, interconnection agreement status, then study progress — and
falls back to the per-ISO YAML mapping on the native Status only when the
milestones are silent. Derivation keys on column presence (via the Y/N-safe
milestone gate), not on specific cell values.
"""

from __future__ import annotations

from typing import Any

from .adapters import GridstatusQueueAdapter, first_milestone
from .ladder import WITHDRAWN, normalize_status

#: Milestone columns, most-advanced last. Presence of any asserts the studied rung.
_STUDY_COLUMNS: tuple[str, ...] = (
    "Initial Study",
    "Feasibility Study",
    "Feasibility Study Status",
    "System Impact Study",
    "System Impact Study Status",
    "Facilities Study",
    "Facilities Study Status",
)

_AGREEMENT_COLUMNS: tuple[str, ...] = (
    "Interim/Interconnection Service/Generation Interconnection Agreement",
    "Interim/Interconnection Service/Generation Interconnection Agreement Status",
)


class PJMQueueAdapter(GridstatusQueueAdapter):
    """PJM: milestone-first canonical status, YAML fallback on native Status."""

    iso = "PJM"

    def _resolve_status(
        self, raw: dict[str, Any], native_status: str, project_id: str
    ) -> str:
        if raw.get("Withdrawn Date") is not None:
            return WITHDRAWN
        if raw.get("Actual Completion Date") is not None:  # renamed from in-service date
            return "energized"
        if first_milestone(raw, _AGREEMENT_COLUMNS) is not None:
            return "esa_signed"
        if first_milestone(raw, _STUDY_COLUMNS) is not None:
            return "studied"
        return normalize_status(self.iso, native_status, project_id)
