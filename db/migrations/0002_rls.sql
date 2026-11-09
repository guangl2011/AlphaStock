-- AlphaStock row-level security — anon is read-only on every table.
-- Apply with: psql "$SUPABASE_DB_URL" -f db/migrations/0002_rls.sql
-- Contract:
--   * RLS enabled on all five tables.
--   * anon gets exactly one policy per table: FOR SELECT, USING (true).
--   * no INSERT/UPDATE/DELETE policy exists for anon, and direct grants are
--     revoked as defense-in-depth — anon writes are doubly denied.
--   * writes go through the service role (SUPABASE_SERVICE_KEY) used by the
--     pipelines and the publish path; the web Explorer reads with the anon key.

BEGIN;

ALTER TABLE signals ENABLE ROW LEVEL SECURITY;
ALTER TABLE companies ENABLE ROW LEVEL SECURITY;
ALTER TABLE attestations ENABLE ROW LEVEL SECURITY;
ALTER TABLE source_health ENABLE ROW LEVEL SECURITY;
ALTER TABLE alert_history ENABLE ROW LEVEL SECURITY;

CREATE POLICY anon_read_signals ON signals
    FOR SELECT TO anon USING (true);
CREATE POLICY anon_read_companies ON companies
    FOR SELECT TO anon USING (true);
CREATE POLICY anon_read_attestations ON attestations
    FOR SELECT TO anon USING (true);
CREATE POLICY anon_read_source_health ON source_health
    FOR SELECT TO anon USING (true);
CREATE POLICY anon_read_alert_history ON alert_history
    FOR SELECT TO anon USING (true);

GRANT SELECT ON signals, companies, attestations, source_health, alert_history TO anon;

REVOKE INSERT, UPDATE, DELETE, TRUNCATE
    ON signals, companies, attestations, source_health, alert_history FROM anon;

COMMIT;
