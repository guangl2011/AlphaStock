"""Weekly-scan workflow contract: scheduled live scans get the env they need.

gridstatus 0.37.0's PJM class reads the API key straight from the environment
(``os.getenv("PJM_API_KEY")``) and refuses to build without it, and the queue
sources call ``PJM()`` with no arguments — so the workflow's env mapping is
the whole plumbing. The secret VALUE is maintained by the owner in GitHub
Actions settings; this test pins that the mapping exists.
"""

from __future__ import annotations

from pathlib import Path

import yaml

WORKFLOW_PATH = (
    Path(__file__).resolve().parents[2] / ".github" / "workflows" / "weekly-scan.yml"
)


def _scan_step_env() -> dict[str, object]:
    workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["scan-and-report"]["steps"]
    scan_steps = [
        step
        for step in steps
        if isinstance(step, dict)
        and str(step.get("name", "")).startswith("Run weekly scan")
    ]
    assert len(scan_steps) == 1, "weekly-scan.yml must have exactly one scan step"
    env = scan_steps[0].get("env")
    assert isinstance(env, dict), "scan step must declare env"
    return env


def test_scan_step_plumbs_pjm_api_key_to_gridstatus() -> None:
    env = _scan_step_env()
    assert env["PJM_API_KEY"] == "${{ secrets.PJM_API_KEY }}"


def test_scan_step_keeps_the_existing_secret_mappings() -> None:
    env = _scan_step_env()
    assert env["SUPABASE_DB_URL"] == "${{ secrets.SUPABASE_DB_URL }}"
