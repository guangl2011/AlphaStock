-- AlphaStock V2.1 — user-scoped tables (watchlists, portfolios, alert settings).
-- Apply with: psql "$SUPABASE_DB_URL" -f db/migrations/0004_user_tables.sql
-- Contract (differs from 0002 on purpose):
--   * These three tables are PER-USER. RLS policies are scoped to
--     auth.uid() for the `authenticated` role — SELECT/INSERT/UPDATE/DELETE
--     all covered by one FOR ALL policy per table, with WITH CHECK pinning
--     new rows to their owner.
--   * No anon policy exists, and anon grants are revoked as
--     defense-in-depth: unauthenticated readers of the public Explorer
--     (anon key) cannot see or touch user data.
--   * user_id references auth.users(id) — the Supabase Auth identity.

BEGIN;

CREATE TABLE user_watchlists (
    user_id uuid NOT NULL REFERENCES auth.users (id) ON DELETE CASCADE,
    ticker text NOT NULL,
    added_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, ticker)
);

CREATE TABLE user_portfolios (
    user_id uuid NOT NULL REFERENCES auth.users (id) ON DELETE CASCADE,
    ticker text NOT NULL,
    shares numeric NOT NULL CHECK (shares > 0),
    cost_basis numeric,
    PRIMARY KEY (user_id, ticker)
);

CREATE TABLE user_alert_settings (
    user_id uuid NOT NULL REFERENCES auth.users (id) ON DELETE CASCADE,
    ticker text NOT NULL,
    channel text NOT NULL,
    enabled boolean NOT NULL DEFAULT true,
    PRIMARY KEY (user_id, ticker, channel)
);

ALTER TABLE user_watchlists ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_portfolios ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_alert_settings ENABLE ROW LEVEL SECURITY;

CREATE POLICY user_watchlists_owner ON user_watchlists
    FOR ALL TO authenticated
    USING (user_id = auth.uid())
    WITH CHECK (user_id = auth.uid());
CREATE POLICY user_portfolios_owner ON user_portfolios
    FOR ALL TO authenticated
    USING (user_id = auth.uid())
    WITH CHECK (user_id = auth.uid());
CREATE POLICY user_alert_settings_owner ON user_alert_settings
    FOR ALL TO authenticated
    USING (user_id = auth.uid())
    WITH CHECK (user_id = auth.uid());

GRANT SELECT, INSERT, UPDATE, DELETE
    ON user_watchlists, user_portfolios, user_alert_settings TO authenticated;

REVOKE INSERT, UPDATE, DELETE, TRUNCATE
    ON user_watchlists, user_portfolios, user_alert_settings FROM anon;

COMMIT;
