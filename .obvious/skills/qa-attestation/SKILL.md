---
name: qa-attestation
description: Use when handling human approval decisions on staged Daily Alpha issues or weekly alerts, monthly universe review rows, or index publication rights — A7 is Richard Lu, a person, not automation.
---

# A7 — QA & Attestation (human: Richard Lu)

## Mission

Approve Daily Alpha issues and weekly alerts, run the monthly universe review, and hold index publication rights — the platform's only human gate.

## Workflow

**This is a person-only role. No automation acts on A7's behalf; an agent that finds itself "acting as A7" must stop and hand the decision to Richard Lu.**

**Triggers and steps:**

1. **Daily Alpha approval** — when a `daily-alpha`-labeled issue is staged (weekdays 11:30 UTC run): read the brief, spot-check figures against cited sources, then approve (publish), reject, or request changes as a comment on the issue.
2. **Weekly alerts approval** — when the `weekly-alert` issue is staged (Mondays 12:00 UTC): same decision set on the top-10 alert list.
3. **Monthly universe review** — first week of each month: add a row to the Monthly Review sheet (`Review Date`, `Review Type`, `Scope`, `Action`, `Stocks Removed`/`Added`, `Reason`, `Decided By`) for any constituent change, keeping the two universes disjoint and tiers balanced (replacements within the same category, sourced rationale per row).
4. **Index publication rights** — client-facing publication of AS-100 / AS-MC100 levels requires written Tiingo authorization plus compliance review; A7 is the sole decision-maker and nothing ships without that authorization on file.

**Recurring review cadence:** monthly (universe); per-issue (briefs and alerts).

## Gates

- **Exactly one human gate per client-facing path** — these approvals are it. Approve, reject, or request changes; each decision is recorded append-only with SHA-256 (PR #3 attestation store). Never bypass, pre-approve in bulk, or delegate the decision to an agent.
- Agents may prepare evidence for A7 (links, reconciliations) but may never record an approval decision.
- Universe changes that would make AS-100 and AS-MC100 overlap, or that reintroduce PSTG (excluded for missing EOD history), are invalid — reject regardless of rationale until the methodology changes.
- No publication of index levels to clients without written Tiingo authorization plus compliance review.

## Verification

A reviewer checks:

- Every published brief/alert traces to a staged issue with a recorded approval decision (comment or review row) — no publication without one.
- Approval decisions appear as append-only attestation rows with SHA-256 in the `attestations` table (PR #3 store); history is never mutated.
- Monthly Review sheet rows exist for every universe change, with sourced rationale and `Decided By: Richard Lu`.
- No code path (grep the repo) sends or publishes client-facing content without a recorded A7 decision.
