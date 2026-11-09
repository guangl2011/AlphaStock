"""Live records sources built on gridstatus 0.37.0.

Every adapter's default records source is gridstatus's normalized
interconnection-queue DataFrame (``<Iso>.get_interconnection_queue()``
flattened to plain dict rows). The import is deliberately lazy and the module
never runs at test time: tests inject fixture snapshots, so CI makes no live
ISO calls.

Grounded against gridstatus 0.37.0 (installed and inspected this session):
all seven ISO classes expose ``get_interconnection_queue()`` and normalize to
the shared column set in ``gridstatus.utils._interconnection_columns``.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

from .base import RecordSource, json_safe_record

#: gridstatus module and class per canonical ISO name.
_GRIDSTATUS_SPECS: dict[str, tuple[str, str]] = {
    "PJM": ("pjm", "PJM"),
    "ERCOT": ("ercot", "Ercot"),
    "MISO": ("miso", "MISO"),
    "CAISO": ("caiso", "CAISO"),
    "SPP": ("spp", "SPP"),
    "NYISO": ("nyiso", "NYISO"),
    "ISO-NE": ("isone", "ISONE"),
}


def make_gridstatus_records_source(iso: str) -> RecordSource:
    """Build the live records source for ``iso`` (gridstatus, lazily imported)."""
    if iso not in _GRIDSTATUS_SPECS:
        raise KeyError(f"no gridstatus source wired for ISO {iso!r}")
    module_name, class_name = _GRIDSTATUS_SPECS[iso]

    def _load() -> list[dict[str, Any]]:
        module = import_module(f"gridstatus.{module_name}")
        iso_cls = getattr(module, class_name)
        frame = iso_cls().get_interconnection_queue()
        return [json_safe_record(record) for record in frame.to_dict("records")]

    return _load
