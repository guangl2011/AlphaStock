/**
 * Fixture contract: every fixture row must pass its mapper, and every hash
 * must be a real 64-hex SHA-256 output. Fixture rot (like a malformed batch
 * hash) must fail CI loudly — the same rule the product applies to sources.
 */
process.env.ALPHASTOCK_DATA_SOURCE = "fixture";

import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import alertHistoryFixture from "../fixtures/alert-history.json";
import attestationsFixture from "../fixtures/attestations.json";
import companiesFixture from "../fixtures/companies.json";
import signalsFixture from "../fixtures/signals.json";
import { toPublicAlert, toPublicAttestation, toPublicCompany, toPublicSignal } from "../lib/view";

const HEX64 = /^[0-9a-f]{64}$/;

function load(name: string): string {
  return readFileSync(new URL(`../fixtures/${name}`, import.meta.url), "utf8");
}

function hashesIn(file: string, key: string): Array<[string, string]> {
  return [...load(file).matchAll(new RegExp(`"${key}": "([0-9a-f]*)"`, "g"))].map((m) => [
    file,
    m[1],
  ]);
}

describe("fixture contract", () => {
  it("parses every fixture row through its mapper", () => {
    for (const row of signalsFixture as unknown[]) expect(() => toPublicSignal(row)).not.toThrow();
    for (const row of companiesFixture as unknown[])
      expect(() => toPublicCompany(row)).not.toThrow();
    for (const row of attestationsFixture as unknown[])
      expect(() => toPublicAttestation(row)).not.toThrow();
    for (const row of alertHistoryFixture as unknown[])
      expect(() => toPublicAlert(row)).not.toThrow();
  });

  it("carries only well-formed SHA-256 hashes", () => {
    const hashes: Array<[string, string]> = [
      ...hashesIn("signals.json", "hash"),
      ...hashesIn("attestations.json", "batch_hash"),
    ];
    expect(hashes.length).toBeGreaterThanOrEqual(10);
    for (const [kind, hash] of hashes) {
      expect(hash, `${kind} hash must be 64 hex chars`).toMatch(HEX64);
    }
  });

  it("references only signals that exist", () => {
    const ids = new Set(
      (signalsFixture as Array<{ signal_id: string }>).map((row) => row.signal_id),
    );
    for (const att of attestationsFixture as Array<{ signal_ids: string[] }>) {
      for (const id of att.signal_ids) expect(ids, `${id} missing from signals.json`).toContain(id);
    }
  });
});
