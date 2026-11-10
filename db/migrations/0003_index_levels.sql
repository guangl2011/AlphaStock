-- AlphaStock index store: AS-100 / AS-MC100 daily levels + attestation.
-- Apply with: psql "$SUPABASE_DB_URL" -f db/migrations/0003_index_levels.sql
-- Design notes:
--   * index_levels is append-only by trigger, like signals (migration 0001):
--     recomputation writes new rows or no-ops on (index_id, date); nothing
--     is ever mutated. The hash column attests the canonical level row so
--     served values can be verified against the ledger.
--   * index_attestations mirrors attestations: one batch hash per pipeline
--     run, derived from the sorted per-row hashes.

BEGIN;

CREATE TABLE index_levels (
    index_id          text NOT NULL CHECK (index_id IN ('AS-100', 'AS-MC100')),
    date              date NOT NULL,
    level             numeric NOT NULL CHECK (level > 0),
    constituent_count integer NOT NULL CHECK (constituent_count > 0),
    excluded          text[] NOT NULL DEFAULT '{}',
    hash              text NOT NULL CHECK (hash ~ '^[0-9a-f]{64}$'),
    published_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (index_id, date)
);

CREATE TABLE index_attestations (
    batch_id     text PRIMARY KEY,
    batch_hash   text NOT NULL CHECK (batch_hash ~ '^[0-9a-f]{64}$'),
    index_count  integer NOT NULL CHECK (index_count >= 0),
    index_ids    text[] NOT NULL DEFAULT '{}',
    anchored_at  timestamptz NOT NULL DEFAULT now()
);

CREATE FUNCTION index_levels_append_only () RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'index_levels is append-only: % blocked', TG_OP;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER index_levels_no_update
    BEFORE UPDATE OR DELETE ON index_levels
    FOR EACH ROW EXECUTE FUNCTION index_levels_append_only ();

CREATE FUNCTION index_attestations_append_only () RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'index_attestations is append-only: % blocked', TG_OP;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER index_attestations_no_update
    BEFORE UPDATE OR DELETE ON index_attestations
    FOR EACH ROW EXECUTE FUNCTION index_attestations_append_only ();

COMMIT;
