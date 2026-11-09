-- AlphaStock signal store — system of record (MVP, spec art_jLZaGljO).
-- Apply with: psql "$SUPABASE_DB_URL" -f db/migrations/0001_signal_store.sql
-- Design notes:
--   * signals is the append-only event store. The unit of signal is a status
--     transition, not raw queue megawatts, so transition fields (iso, project_id,
--     status_from/status_to) are first-class columns.
--   * signals and attestations are append-only by trigger: UPDATE and DELETE raise.
--     Corrections are new signals that reference the superseded hash; nothing is
--     ever mutated.
--   * scenario_range holds bear/base/bull relative-excess-return bounds. It is an
--     internal modeling field per design doc §4.2; public routes never render it.

BEGIN;

CREATE TABLE signals (
    signal_id        text PRIMARY KEY,
    hash             text NOT NULL CHECK (hash ~ '^[0-9a-f]{64}$'),
    category         text NOT NULL,
    event_type       text NOT NULL CHECK (event_type IN (
                         'new_project', 'status_transition', 'capacity_change',
                         'withdrawal', 'other')),
    tickers          text[] NOT NULL DEFAULT '{}',
    theme            text,
    iso              text,
    project_id       text,
    status_from      text,
    status_to        text,
    summary_en       text NOT NULL,
    summary_zh       text NOT NULL,
    direction        text NOT NULL CHECK (direction IN ('positive', 'negative', 'neutral')),
    strength         integer NOT NULL CHECK (strength BETWEEN 1 AND 5),
    horizon_days     integer,
    scenario_range   jsonb,
    probability_edge numeric,
    macro_overlay    jsonb NOT NULL DEFAULT '{}',
    invalidation     text NOT NULL,
    sources          jsonb NOT NULL DEFAULT '[]',
    verified         boolean NOT NULL DEFAULT false,
    published_at     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX signals_category_idx ON signals (category);
CREATE INDEX signals_published_at_idx ON signals (published_at DESC);

CREATE TABLE companies (
    ticker          text PRIMARY KEY,
    name            text NOT NULL,
    category        text NOT NULL,
    themes          text[] NOT NULL DEFAULT '{}',
    exposure_type   text,
    market_cap      numeric,
    market_cap_as_of date,
    thesis          text,
    key_risk        text,
    source_url      text NOT NULL
);

CREATE TABLE attestations (
    batch_id     text PRIMARY KEY,
    batch_hash   text NOT NULL CHECK (batch_hash ~ '^[0-9a-f]{64}$'),
    signal_count integer NOT NULL CHECK (signal_count >= 0),
    signal_ids   text[] NOT NULL DEFAULT '{}',
    anchored_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE source_health (
    source_name     text PRIMARY KEY,
    status          text NOT NULL CHECK (status IN ('ok', 'stale', 'failed')),
    last_success_at timestamptz,
    detail          text,
    checked_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE alert_history (
    alert_id    text PRIMARY KEY,
    alert_date  date NOT NULL,
    ticker      text NOT NULL REFERENCES companies (ticker),
    alpha_score numeric,
    score_delta numeric,
    brief_en    text NOT NULL,
    brief_zh    text NOT NULL,
    signal_ids  text[] NOT NULL DEFAULT '{}',
    approved    boolean NOT NULL DEFAULT false,
    sent_at     timestamptz
);

-- Append-only enforcement for the two tamper-evident tables.
CREATE FUNCTION prevent_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'append-only: % on % is not allowed', TG_OP, TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER signals_no_update BEFORE UPDATE ON signals
    FOR EACH ROW EXECUTE FUNCTION prevent_mutation();
CREATE TRIGGER signals_no_delete BEFORE DELETE ON signals
    FOR EACH ROW EXECUTE FUNCTION prevent_mutation();

CREATE TRIGGER attestations_no_update BEFORE UPDATE ON attestations
    FOR EACH ROW EXECUTE FUNCTION prevent_mutation();
CREATE TRIGGER attestations_no_delete BEFORE DELETE ON attestations
    FOR EACH ROW EXECUTE FUNCTION prevent_mutation();

COMMIT;
