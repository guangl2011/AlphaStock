"""Snapshot-level queue diffing: the V2 event detector.

Takes the previous scan's project index and the current scan's projects and
emits canonical signal rows (V2_INTERCONNECTION) for every material event:
new projects, status transitions (including backward moves), withdrawals,
and material capacity changes as de-weighted context.

Idempotence contract (build spec acceptance criteria): diffing a snapshot
against ITSELF yields zero candidates. Re-running the diff on the same
(prev, curr) pair is a pure recomputation — identical inputs produce
identical rows, and content-addressed signal ids let the append-only store
absorb accidental re-ingest without duplicates.

No network, no clock reads: event timestamps come from the snapshot that
observed the event, so tests are deterministic and the weekly scan
reproduces a run byte-for-byte.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping

from pipelines.ingest.queues.base import QueueProject
from pipelines.signals.transitions import (
    QueueTransition,
    classify_pair,
    new_project_event,
    to_signal_dict,
    withdrawal_event,
)

#: The blueprint's name for the grammar's event object.
SignalCandidate = QueueTransition

CATEGORY = "V2_INTERCONNECTION"

#: Theme assignment by load type (design doc §3.1 theme names). A queue row
#: often doesn't say what the load is for — the Signal Agent's classification
#: stage refines this later; the MVP default is the grid theme.
_THEME_BY_LOAD_TYPE = {
    "generation": "AI Power",
    "large_load": "Data Centers",
    "mixed": "Grid & Transmission",
    "unknown": "Grid & Transmission",
}

_SIGNAL_ID_PREFIX = "SIG"


def project_key(iso: str, project_id: str) -> str:
    """A project's identity across scans: ISO + native queue id."""
    return f"{iso}|{project_id}"


def index_projects(projects: Iterable[QueueProject]) -> dict[str, QueueProject]:
    """Key a snapshot by project identity (last row wins on duplicates)."""
    return {project_key(p.iso, p.project_id): p for p in projects}


def diff_projects(
    prev: Mapping[str, QueueProject],
    curr: Iterable[QueueProject],
) -> list[dict[str, object]]:
    """Emit one canonical signal row per material event.

    ``prev`` is the indexed previous snapshot (``index_projects`` output);
    ``curr`` is the current scan's projects. Rows are ordered by signal id so
    identical inputs produce byte-identical output.
    """
    curr_index = index_projects(curr)

    # The event and the snapshot that observed it, together: a withdrawal is
    # observed by the PREVIOUS scan (the project is absent from the current
    # one); every other event is observed by the scan reporting the new state.
    events: list[tuple[QueueProject, QueueTransition]] = []
    for key, project in curr_index.items():
        previous = prev.get(key)
        if previous is None:
            events.append((project, new_project_event(project)))
            continue
        event = classify_pair(previous, project)
        if event is not None:
            events.append((project, event))  # unchanged pairs take no event
    for key, project in prev.items():
        if key not in curr_index:
            events.append((project, withdrawal_event(project)))

    return sorted(
        (_to_row(project, event) for project, event in events),
        key=lambda row: str(row["signal_id"]),
    )


def _to_row(project: QueueProject, event: QueueTransition) -> dict[str, object]:
    retrieved_at = project.retrieved_at.strftime("%Y-%m-%dT%H:%M:%SZ")
    return to_signal_dict(
        event,
        signal_id=signal_id_for(event, retrieved_at),
        source_url=project.source_url,
        retrieved_at=retrieved_at,
        theme=_THEME_BY_LOAD_TYPE.get(project.load_type, "Grid & Transmission"),
    )


def signal_id_for(event: QueueTransition, retrieved_at: str) -> str:
    """Content-addressed id — the same event re-detected yields the same id.

    Identity binds the event to the scan that observed it (via the snapshot
    timestamp), so idempotent re-detection maps onto one id the store can
    dedupe, while a later scan observing the same transition again records a
    new, honestly-timestamped event.
    """
    identity = "|".join(
        (
            event.iso,
            event.project_id,
            event.event_type,
            event.status_from or "",
            event.status_to or "",
            retrieved_at,
        )
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:8]
    day = retrieved_at[:10].replace("-", "")
    return f"{_SIGNAL_ID_PREFIX}-{day}-{digest}"
