"""Canonical status ladder and per-ISO native-status mappings.

The ladder is the V2 event grammar's spine: ``requested → studied → approved
→ esa_signed → energized``, with ``withdrawn`` as an absorbing state. Native
ISO status strings resolve through per-ISO YAML tables under ``mappings/`` —
mapping is data, reviewed in PRs, so source-site changes are handled by
editing a table, not code. A raw status with no mapping raises
:class:`UnmappedStatusError`; unmapped statuses are never silently bucketed
to ``unknown`` and ignored.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from importlib import resources
from typing import Any

import yaml

#: The canonical ladder, in ascending order of progression.
CANONICAL_ORDER: tuple[str, ...] = ("requested", "studied", "approved", "esa_signed", "energized")

#: Absorbing state: once withdrawn, a project's exit is itself the signal.
WITHDRAWN = "withdrawn"

#: Every canonical status a mapping may target.
CANONICAL_STATUSES = frozenset(CANONICAL_ORDER) | {WITHDRAWN}

#: Bilingual rung labels for deterministic summaries.
RUNG_ZH: dict[str, str] = {
    "requested": "申请",
    "studied": "研究",
    "approved": "批准",
    "esa_signed": "签署并网协议",
    "energized": "并网",
    "withdrawn": "撤回",
}


class UnmappedStatusError(RuntimeError):
    """A native queue status has no mapping for its ISO — the scan fails loudly."""

    def __init__(
        self, iso: str, raw_status: str, project_id: str | None = None
    ) -> None:
        self.iso = iso
        self.raw_status = raw_status
        self.project_id = project_id
        where = f"{iso} project {project_id!r}" if project_id else iso
        super().__init__(
            f"unmapped queue status for {where}: {raw_status!r} — add it to the "
            f"{iso} mapping table (pipelines/pipelines/ingest/queues/mappings)"
        )


@dataclass(frozen=True)
class StatusMapping:
    """One ISO's native→canonical status table, loaded from its YAML file."""

    iso: str
    source_url: str
    notes: str
    mapping: dict[str, str]


def ladder_rank(status_norm: str) -> int:
    """Position on the ladder; withdrawn ranks below everything (absorbing)."""
    if status_norm == WITHDRAWN:
        return -1
    if status_norm not in CANONICAL_ORDER:
        raise ValueError(f"not a canonical ladder status: {status_norm!r}")
    return CANONICAL_ORDER.index(status_norm)


def mapping_filename(iso: str) -> str:
    """Mapping-table resource path for a canonical ISO name (``ISO-NE`` → ``isone.yaml``)."""
    slug = "".join(ch for ch in iso.lower() if ch.isalnum())
    if not slug:
        raise ValueError(f"ISO name reduces to an empty slug: {iso!r}")
    return f"mappings/{slug}.yaml"


@cache
def mapping_for(iso: str) -> StatusMapping:
    """Load (and cache) one ISO's mapping table. Unknown targets fail loudly."""
    resource = resources.files("pipelines.ingest.queues").joinpath(mapping_filename(iso))
    return load_status_mapping(iso, resource.read_text(encoding="utf-8"))


def load_status_mapping(iso: str, table_text: str) -> StatusMapping:
    """Parse and validate one mapping table's text. Pure: no importlib, no cache."""
    try:
        payload: dict[str, Any] = yaml.safe_load(table_text)
        mapping = {str(k): str(v) for k, v in payload["mapping"].items()}
        loaded = StatusMapping(
            iso=str(payload["iso"]),
            source_url=str(payload["source_url"]),
            notes=str(payload.get("notes", "")),
            mapping=mapping,
        )
    except (KeyError, TypeError, AttributeError) as exc:
        raise RuntimeError(f"malformed {iso} status mapping table: {exc}") from exc
    if loaded.iso != iso:
        raise RuntimeError(f"{iso}.yaml declares iso={loaded.iso!r}")
    bad = {k: v for k, v in mapping.items() if v not in CANONICAL_STATUSES}
    if bad:
        raise RuntimeError(f"{iso} mapping targets are not canonical statuses: {bad}")
    return loaded


def normalize_status(iso: str, raw_status: str, project_id: str | None = None) -> str:
    """Map one native status to the canonical ladder node for ``iso``.

    Matching is exact first, then case-insensitive — a source that changes
    casing does not take the scan down, but a genuinely new status does.
    """
    raw = raw_status.strip()
    if not raw:
        raise UnmappedStatusError(iso, raw_status, project_id)
    table = mapping_for(iso).mapping
    if raw in table:
        return table[raw]
    lowered = raw.lower()
    for native, canonical in table.items():
        if native.lower() == lowered:
            return canonical
    raise UnmappedStatusError(iso, raw_status, project_id)
