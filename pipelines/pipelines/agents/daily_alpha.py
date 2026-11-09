"""Daily Alpha CLI: build → verify → stage as a GitHub issue (never send).

Reads canonical signal rows either from a JSON file (``--signals-file``,
fixture/development mode) or from the Supabase REST endpoint
(``SUPABASE_URL`` + ``SUPABASE_SERVICE_KEY``). Builds the bilingual brief,
runs the verification gate, and writes ``issue.md`` + ``status.json`` +
``brief.json`` into ``--out-dir``; the workflow turns ``issue.md`` into the
staged GitHub issue.

Exit codes: 0 = staged (any of pending_approval / rejected / empty_run);
2 = configuration or input error. A missing database config is an error, not
an empty run — a silent "no signals" would mask misconfiguration (build spec
named failure case). There is deliberately no send path here: approval and
delivery are wired in operations (design doc §8.1 human gate).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pipelines.agents.verifier import HttpSourceChecker, SourceChecker
from pipelines.agents.writer import (
    HISTORY_DAYS,
    ITEM_WINDOW_HOURS,
    EmptyRunReport,
    WriterInputError,
    build_daily_alpha,
    stage_brief,
)


class _NoNetworkChecker:
    """Offline/development mode: structural source checks only, no probes."""

    def reachable(self, url: str) -> bool:
        return True


def fetch_signals_supabase(
    base_url: str, service_key: str, since_iso: str
) -> list[dict[str, Any]]:
    """Read canonical signal rows via the Supabase REST endpoint (stdlib only)."""
    query = urllib.parse.urlencode(
        {
            "select": "*",
            "published_at": f"gte.{since_iso}",
            "order": "published_at.desc",
            "limit": "1000",
        }
    )
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/rest/v1/signals?{query}",
        headers={
            "apikey": service_key,
            "Authorization": f"Bearer {service_key}",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if not isinstance(payload, list):
        raise WriterInputError("unexpected Supabase REST response — expected a JSON array")
    return [row for row in payload if isinstance(row, dict)]


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m pipelines.agents.daily_alpha",
        description="Build, verify, and stage the bilingual Daily Alpha brief (never sends).",
    )
    parser.add_argument(
        "--signals-file",
        help="JSON file of canonical signal rows (fixture mode); default: Supabase REST",
    )
    parser.add_argument(
        "--source-checker",
        choices=("http", "none"),
        default="http",
        help="http = probe source URLs (runtime); none = structural checks only (offline/dev)",
    )
    parser.add_argument(
        "--history-days",
        type=int,
        default=HISTORY_DAYS,
        help=f"Supabase lookback for the Alpha Score window (default {HISTORY_DAYS})",
    )
    parser.add_argument(
        "--item-window-hours",
        type=int,
        default=ITEM_WINDOW_HOURS,
        help=f"item selection window before as-of (default {ITEM_WINDOW_HOURS})",
    )
    parser.add_argument(
        "--out-dir",
        default="daily-alpha-out",
        help="where issue.md / status.json / brief.json are written",
    )
    parser.add_argument(
        "--as-of", help="override the clock (tz-aware ISO 8601); default: now UTC"
    )
    return parser.parse_args(argv)


def _load_rows(args: argparse.Namespace, as_of: datetime) -> list[dict[str, Any]]:
    if args.signals_file:
        rows = json.loads(Path(args.signals_file).read_text(encoding="utf-8"))
        if not isinstance(rows, list) or not all(isinstance(r, Mapping) for r in rows):
            raise WriterInputError("--signals-file must contain a JSON array of signal rows")
        return list(rows)

    base_url = os.environ.get("SUPABASE_URL", "")
    service_key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not base_url or not service_key:
        raise WriterInputError(
            "no --signals-file given and SUPABASE_URL / SUPABASE_SERVICE_KEY are not set — "
            "refusing to guess (a silent empty run would mask misconfiguration)"
        )
    since = (as_of - timedelta(days=args.history_days)).isoformat()
    return fetch_signals_supabase(base_url, service_key, since)


def _resolve_as_of(raw: str | None) -> datetime:
    if not raw:
        return datetime.now(UTC)
    as_of = datetime.fromisoformat(raw)
    if as_of.tzinfo is None:
        raise WriterInputError("--as-of must be a timezone-aware ISO timestamp")
    return as_of


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)

    try:
        as_of = _resolve_as_of(args.as_of)
        rows = _load_rows(args, as_of)
        built = build_daily_alpha(rows, as_of, item_window_hours=args.item_window_hours)
    except (WriterInputError, OSError, json.JSONDecodeError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 2

    checker: SourceChecker = (
        HttpSourceChecker() if args.source_checker == "http" else _NoNetworkChecker()
    )
    staged = stage_brief(built, source_checker=checker)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "status": staged.status,
        "issue_title": staged.title,
        "as_of": as_of.isoformat(),
        "signal_count": len(rows),
        "item_count": 0 if isinstance(built, EmptyRunReport) else len(built.items),
        "verification": {
            "passed": staged.verification.passed,
            "issues": [asdict(issue) for issue in staged.verification.issues],
        },
    }
    (out_dir / "issue.md").write_text(staged.markdown, encoding="utf-8")
    (out_dir / "status.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (out_dir / "brief.json").write_text(
        json.dumps(asdict(built), indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )
    print(f"daily-alpha: {staged.status} — {staged.title} (staged in {out_dir})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
