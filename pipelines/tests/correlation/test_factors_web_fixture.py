"""The committed Factors-library fixture regenerates from a research-mode run."""

from __future__ import annotations

import json
from pathlib import Path

from pipelines.correlation.__main__ import main as run_correlation
from pipelines.correlation.web_fixture import build
from pipelines.indices.research import main as run_research

WEB_FIXTURE = Path(__file__).resolve().parents[3] / "web" / "fixtures" / "factors.json"


def test_factors_fixture_is_reproducible(tmp_path: Path) -> None:
    research_out = tmp_path / "research"
    assert run_research(["--out-dir", str(research_out)]) == 0
    corr_out = tmp_path / "corr"
    assert run_correlation([
        "--mode", "research", "--index-levels", str(research_out / "research_levels.csv"),
        "--out-dir", str(corr_out),
    ]) == 0
    built = json.dumps(build(corr_out), indent=1, sort_keys=True) + "\n"
    assert built == WEB_FIXTURE.read_text(encoding="utf-8")
    payload = json.loads(built)
    assert payload["index_ids"] == ["AS-100", "AS-MCB100", "AS-MCG100"]
    assert {c["factor"] for c in payload["catalog"]} == {
        "BREADTH", "GRID", "MICRO", "MKT", "NUKE", "ROBO", "SIZE", "UTIL",
    }
