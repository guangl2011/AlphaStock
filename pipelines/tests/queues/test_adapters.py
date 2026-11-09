"""Queue adapter tests: fixture snapshots per ISO, no live ISO calls.

Every adapter runs against an injected fixture snapshot (synthetic rows in
gridstatus 0.37.0's normalized shape — see tests/fixtures/queues/README.md),
so CI never touches the network. The tests pin the canonical reduction:
schema fields, per-ISO status normalization (including the PJM/ERCOT
milestone ladders), provenance round-trip, and loud failure on unmapped
statuses and structurally broken records.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from pipelines.ingest.queues import (
    ADAPTER_CLASSES,
    ALL_ISOS,
    CANONICAL_STATUSES,
    QUEUE_SOURCE_URLS,
    QueueDataError,
    QueueProject,
    RecordSource,
    UnmappedStatusError,
    build_adapter,
    build_all_adapters,
)

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "queues"
RETRIEVED_AT = datetime(2026, 11, 9, 6, 0, 0, tzinfo=UTC)


def load_fixture(iso: str) -> list[dict[str, Any]]:
    filename = f"{iso.lower().replace('-', '')}_snapshot.json"
    path = FIXTURES_DIR / filename
    return json.loads(path.read_text(encoding="utf-8"))


def fixture_source(iso: str) -> RecordSource:
    """An injectable records source: a callable returning one fixture snapshot."""

    def _load() -> list[dict[str, Any]]:
        return load_fixture(iso)

    return _load


def records_as_source(records: list[dict[str, Any]]) -> RecordSource:
    """Wrap already-loaded records as an injectable source."""

    def _load() -> list[dict[str, Any]]:
        return records

    return _load


def make_adapter(iso: str) -> Any:
    return build_adapter(iso, fixture_source(iso), RETRIEVED_AT)


@pytest.fixture(autouse=True)
def _clear_mapping_cache():
    """Mapping tables are cached per process; reload per test for isolation."""
    from pipelines.ingest.queues.ladder import mapping_for

    mapping_for.cache_clear()
    yield
    mapping_for.cache_clear()


class TestFixtureSnapshotsPerIso:
    """Every ISO's fixture reduces to a clean list of canonical projects."""

    def test_all_seven_adapters_registered(self) -> None:
        assert set(ADAPTER_CLASSES) == set(ALL_ISOS)

    @pytest.mark.parametrize("iso", ALL_ISOS)
    def test_fixture_reduces_to_canonical_projects(self, iso: str) -> None:
        adapter = make_adapter(iso)
        projects = adapter.fetch()
        assert len(projects) == len(load_fixture(iso))
        for project in projects:
            assert isinstance(project, QueueProject)
            assert project.iso == iso
            assert project.project_id
            assert project.status_norm in CANONICAL_STATUSES
            assert project.source_url == QUEUE_SOURCE_URLS[iso]
            assert project.retrieved_at == RETRIEVED_AT
            assert project.load_type == "unknown"
            assert project.data_center_likelihood == 0.0
            # Provenance: the raw record round-trips JSON-safe.
            assert json.dumps(project.raw, allow_nan=False) is not None

    @pytest.mark.parametrize("iso", ALL_ISOS)
    def test_fixture_covers_all_generic_statuses(self, iso: str) -> None:
        """Each fixture exercises requested(via Active), completed, withdrawn."""
        projects = make_adapter(iso).fetch()
        norms = {p.status_norm for p in projects}
        assert norms >= {"requested", "esa_signed", "withdrawn"}, (iso, norms)

    def test_build_all_adapters_injects_per_iso_sources(self) -> None:
        adapters = build_all_adapters(
            {iso: fixture_source(iso) for iso in ALL_ISOS}, RETRIEVED_AT
        )
        assert [a.iso for a in adapters] == list(ALL_ISOS)
        for adapter in adapters:
            assert adapter.fetch()  # no network, no import of gridstatus


class TestPjmErcotMilestoneParsers:
    """The custom parsers derive the canonical rung from milestone columns."""

    def test_pjm_ladder_from_milestones(self) -> None:
        by_id = {p.project_id: p for p in make_adapter("PJM").fetch()}
        assert by_id["PJM-0001"].status_norm == "studied"  # SIS complete, no IA
        assert by_id["PJM-0002"].status_norm == "esa_signed"  # agreement signed
        assert by_id["PJM-0003"].status_norm == "energized"  # actual in-service date
        assert by_id["PJM-0004"].status_norm == "withdrawn"  # withdrawal date

    def test_ercot_ladder_from_milestones(self) -> None:
        by_id = {p.project_id: p for p in make_adapter("ERCOT").fetch()}
        assert by_id["INR-100234"].status_norm == "requested"  # no milestones yet
        assert by_id["INR-100245"].status_norm == "studied"  # screening started
        assert by_id["INR-100251"].status_norm == "approved"  # FIS approved
        assert by_id["INR-100260"].status_norm == "esa_signed"  # IA signed
        assert by_id["INR-100271"].status_norm == "energized"  # approved to sync
        assert by_id["INR-100288"].status_norm == "withdrawn"  # cancelled row

    def test_ercot_withdrawal_beats_earlier_milestones(self) -> None:
        records = load_fixture("ERCOT")
        cancelled = next(r for r in records if r["Queue ID"] == "INR-100288")
        # Milestones are still set on the cancelled row; withdrawal must win.
        assert cancelled["Screening Study Started"] is not None
        project = make_adapter("ERCOT").to_projects([cancelled])[0]
        assert project.status_norm == "withdrawn"

    def test_pjm_yn_flags_do_not_count_as_milestones(self) -> None:
        record = load_fixture("PJM")[0]
        record["Feasibility Study Status"] = "N"  # present but not reached
        project = make_adapter("PJM").to_projects([record])[0]
        assert project.status_norm == "studied"  # SIS still complete, N ignored

    def test_ercot_native_status_fallback_when_milestones_silent(self) -> None:
        record = load_fixture("ERCOT")[0]
        record["Status"] = "Completed"  # gridstatus's derived enum, no milestones
        project = make_adapter("ERCOT").to_projects([record])[0]
        assert project.status_norm == "esa_signed"


class TestLoudFailure:
    """Unmapped statuses and broken records fail the scan — never silent unknown."""

    @pytest.mark.parametrize("iso", ALL_ISOS)
    def test_unmapped_native_status_raises(self, iso: str) -> None:
        # A minimal probe record: no milestone columns, so every ISO's
        # resolution path bottoms out in the YAML mapping — which must
        # reject a status it has never seen, loudly.
        records = [
            {
                "Queue ID": "TEST-1",
                "Project Name": "Probe Project",
                "Status": "Brand New Status From ISO Redesign",
            }
        ]
        adapter = build_adapter(iso, records_as_source(records), RETRIEVED_AT)
        with pytest.raises(UnmappedStatusError) as excinfo:
            adapter.fetch()
        assert iso in str(excinfo.value)
        assert "Brand New Status From ISO Redesign" in str(excinfo.value)

    def test_case_only_status_changes_still_map(self) -> None:
        records = load_fixture("MISO")
        records[0]["Status"] = "active"  # case drift, same status
        project = build_adapter("MISO", records_as_source(records), RETRIEVED_AT).fetch()[0]
        assert project.status_norm == "requested"

    def test_missing_status_is_a_data_error(self) -> None:
        records = load_fixture("CAISO")
        records[0]["Status"] = None
        with pytest.raises(QueueDataError, match="without Status"):
            build_adapter("CAISO", records_as_source(records), RETRIEVED_AT).fetch()

    def test_missing_queue_id_is_a_data_error(self) -> None:
        records = load_fixture("SPP")
        records[0]["Queue ID"] = None
        with pytest.raises(QueueDataError, match="without Queue ID"):
            build_adapter("SPP", records_as_source(records), RETRIEVED_AT).fetch()

    def test_duplicate_project_ids_fail(self) -> None:
        records = load_fixture("NYISO")
        records.append(dict(records[0]))
        with pytest.raises(QueueDataError, match="duplicate project_id"):
            build_adapter("NYISO", records_as_source(records), RETRIEVED_AT).fetch()

    def test_mapping_targeting_unknown_canonical_fails_at_load(self) -> None:
        from pipelines.ingest.queues.ladder import load_status_mapping

        broken = """
iso: MISO
source_url: https://example.com
notes: broken by test
mapping:
  "Active": in_queue  # not on the canonical ladder
"""
        with pytest.raises(RuntimeError, match="not canonical statuses"):
            load_status_mapping("MISO", broken)


class TestCapacityAndProvenance:
    """Capacity is context, provenance is non-negotiable."""

    def test_capacity_prefers_combined_column(self) -> None:
        by_id = {p.project_id: p for p in make_adapter("PJM").fetch()}
        assert by_id["PJM-0001"].capacity_mw == 150.0

    def test_capacity_falls_back_to_season_max(self) -> None:
        records = load_fixture("MISO")
        del records[0]["Capacity (MW)"]
        adapter = build_adapter("MISO", records_as_source(records), RETRIEVED_AT)
        project = adapter.to_projects([records[0]])[0]
        assert project.capacity_mw == 100.0  # max(summer 100, winter 85)

    def test_capacity_number_formatting_is_tolerated(self) -> None:
        records = load_fixture("CAISO")
        records[0]["Capacity (MW)"] = "1,350.5"  # thousands separator
        adapter = build_adapter("CAISO", records_as_source(records), RETRIEVED_AT)
        project = adapter.to_projects([records[0]])[0]
        assert project.capacity_mw == 1350.5

    def test_non_numeric_capacity_fails_loudly(self) -> None:
        records = load_fixture("CAISO")
        records[0]["Capacity (MW)"] = "about 300"
        with pytest.raises(QueueDataError, match="non-numeric"):
            build_adapter("CAISO", records_as_source(records), RETRIEVED_AT).fetch()

    def test_raw_record_preserved_verbatim(self) -> None:
        project = make_adapter("ISO-NE").fetch()[0]
        fixture_record = load_fixture("ISO-NE")[0]
        assert project.raw == fixture_record


def test_default_source_resolves_without_network_call() -> None:
    """The live source factory imports gridstatus lazily; building it is offline."""
    pytest.importorskip("gridstatus")
    from pipelines.ingest.queues.sources import make_gridstatus_records_source

    loader = make_gridstatus_records_source("MISO")
    assert callable(loader)  # not called — no network in CI
