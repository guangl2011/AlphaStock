# Queue fixture snapshots

One JSON file per ISO, in the shape gridstatus 0.37.0's
`<Iso>.get_interconnection_queue()` DataFrame produces after
`to_dict("records")` + `json_safe_record` — see
`pipelines.pipelines.ingest.queues.adapters.NORMALIZED_COLUMNS` for the
shared column set, plus per-ISO extra columns (e.g. PJM's per-study statuses,
ERCOT's GIS-report milestones).

**These records are synthetic.** They are written for tests, not scraped:
column names and status vocabularies follow gridstatus 0.37.0's source (the
`InterconnectionQueueStatus` enum values Active/Withdrawn/Completed, SPP's
raw statuses, ISO-NE's one-letter statuses), but the projects are invented.
Runtime drift (a source changing shape or adding a status) is caught by the
source-health checks, which fail the scan loudly per the build spec.

Fixture rows deliberately cover, per ISO: an in-queue project, a
completed/agreement project, and a withdrawn project; PJM and ERCOT add
milestone-rich rows so the custom parsers' derivation ladder is exercised
(requested → studied → approved (ERCOT) → esa_signed → energized, and
withdrawn).
