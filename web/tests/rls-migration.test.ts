/**
 * RLS migration review, as tests: the 0004 user tables must be per-user —
 * RLS enabled, every policy scoped to auth.uid(), no anon read path. This
 * pins the migration contract so a future edit cannot quietly reintroduce
 * the global anon-SELECT pattern from 0002.
 */
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const migration = readFileSync(
  new URL("../../db/migrations/0004_user_tables.sql", import.meta.url),
  "utf8",
);

const TABLES = ["user_watchlists", "user_portfolios", "user_alert_settings"] as const;

describe("db/migrations/0004_user_tables.sql", () => {
  it("creates all three user tables", () => {
    for (const table of TABLES) {
      expect(migration, table).toContain(`CREATE TABLE ${table}`);
      expect(migration, table).toContain("user_id uuid NOT NULL REFERENCES auth.users (id)");
    }
  });

  it("enables RLS on every table", () => {
    for (const table of TABLES) {
      expect(migration, table).toContain(`ALTER TABLE ${table} ENABLE ROW LEVEL SECURITY`);
    }
  });

  it("scopes every policy to auth.uid() with WITH CHECK, covering all four operations", () => {
    for (const table of TABLES) {
      const policy = migration.match(
        new RegExp(`CREATE POLICY ${table}_owner[\\s\\S]*?;`),
      )?.[0];
      expect(policy, table).toBeDefined();
      expect(policy, `${table} policy targets authenticated`).toContain("TO authenticated");
      expect(policy, `${table} policy USING auth.uid()`).toContain("USING (user_id = auth.uid())");
      expect(policy, `${table} policy WITH CHECK auth.uid()`).toContain(
        "WITH CHECK (user_id = auth.uid())",
      );
      // FOR ALL covers SELECT/INSERT/UPDATE/DELETE — assert it is not a
      // per-operation policy that misses one.
      expect(policy, table).toContain("FOR ALL");
    }
  });

  it("grants writes only to authenticated and revokes from anon", () => {
    expect(migration).toContain(
      "GRANT SELECT, INSERT, UPDATE, DELETE",
    );
    expect(migration).toContain("TO authenticated");
    expect(migration).toContain("REVOKE INSERT, UPDATE, DELETE, TRUNCATE");
    expect(migration).toContain("FROM anon");
    // The 0002 global-read pattern must not leak in.
    expect(migration).not.toContain("TO anon USING (true)");
  });
});
