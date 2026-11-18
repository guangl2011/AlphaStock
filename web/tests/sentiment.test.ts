/**
 * Market Sentiment lib contract: the parser validates every field loudly,
 * collapses append-only runs to the latest scan window, and the derivations
 * (confidence band, volume delta, score formatting) match the page's reads.
 */
process.env.ALPHASTOCK_DATA_SOURCE = "fixture";

import { describe, expect, it } from "vitest";

import sentimentFixture from "../fixtures/sentiment.json";
import {
  confidenceBand,
  parseSentimentPayload,
  sentimentBand,
  volumeDelta,
} from "../lib/sentiment";

const payload = sentimentFixture as unknown;

describe("parseSentimentPayload", () => {
  it("parses the shipped fixture and collapses to the latest run", () => {
    const data = parseSentimentPayload(payload);
    expect(data.latestAsOf).toBe("2026-11-18T12:00:00Z");
    expect(data.runs.map((r) => r.topicId)).toEqual([
      "dc-capex",
      "ai-power-demand",
      "interconnection-backlog",
      "cooling-tech",
      "gpu-supply",
      "reit-leasing",
    ]);
    // Previous-window counts survive for the volume delta.
    expect(data.previousCounts["dc-capex"]).toBe(24);
    // Only the latest run's posts are exposed.
    expect(data.posts.every((p) => p.runId === "run-2026-11-18T12:00:00Z")).toBe(true);
  });

  it("parses the v2 coverage block with every platform at or above the keep target", () => {
    const data = parseSentimentPayload(payload);
    for (const platform of ["reddit", "x-indexed", "linkedin-indexed"] as const) {
      const entry = data.coverage[platform];
      expect(entry, platform).toBeDefined();
      expect(entry!.kept).toBeGreaterThanOrEqual(entry!.keepTarget);
      expect(entry!.ladderSteps[0]).toBe("L0");
      expect(Object.keys(entry!.topics).length).toBeGreaterThan(0);
    }
    expect(data.coverage["reddit"]!.ladderSteps).toEqual(["L0"]); // cleared the target at base
    expect(data.coverage["linkedin-indexed"]!.ladderSteps).toContain("L2a");
  });

  it("parses per-post confidence and its band from the pipeline", () => {
    const data = parseSentimentPayload(payload);
    const banded = data.posts.filter((p) => p.confidenceBand !== null);
    expect(banded.length).toBeGreaterThan(0);
    for (const post of banded) {
      expect(post.confidence).toBeGreaterThanOrEqual(0);
      expect(["low", "medium", "high"]).toContain(post.confidenceBand);
    }
  });

  it("tolerates pre-v2 payloads without coverage or post confidence", () => {
    const runs = sentimentFixture as { runs: unknown[] };
    const posts = sentimentFixture as { posts: unknown[] };
    const bare = {
      runs: [runs.runs[runs.runs.length - 1]], // latest run only
      posts: [
        { ...(posts.posts[0] as object), confidence: undefined, confidence_band: undefined },
      ],
    };
    const data = parseSentimentPayload(bare);
    expect(data.coverage).toEqual({});
    expect(data.posts[0].confidence).toBeNull();
    expect(data.posts[0].confidenceBand).toBeNull();
  });

  it("rejects malformed coverage loudly", () => {
    const base = sentimentFixture as { runs: unknown[]; posts: unknown[] };
    expect(() =>
      parseSentimentPayload({ ...base, coverage: { reddit: { ...((sentimentFixture as unknown as { coverage: { reddit: object } }).coverage.reddit), kept: "many" } } }),
    ).toThrow(/kept/);
    expect(() =>
      parseSentimentPayload({ ...base, coverage: { reddit: { ...((sentimentFixture as unknown as { coverage: { reddit: object } }).coverage.reddit), ladder_steps: "L0" } } }),
    ).toThrow(/ladder_steps/);
  });

  it("keeps the zero-post topic as an empty row, never drops it", () => {
    const data = parseSentimentPayload(payload);
    const reit = data.runs.find((r) => r.topicId === "reit-leasing");
    expect(reit?.postCount).toBe(0);
    expect(data.posts.filter((p) => p.topicId === "reit-leasing")).toHaveLength(0);
  });

  it("rejects malformed payloads loudly", () => {
    expect(() => parseSentimentPayload({ runs: [], posts: [] })).toThrow(/must not be empty/);
    expect(() => parseSentimentPayload({})).toThrow(/must be arrays/);
    expect(() =>
      parseSentimentPayload({
        runs: [{ ...((sentimentFixture as { runs: unknown[] }).runs[0] as object), as_of: "2026-11-18" }],
        posts: [],
      }),
    ).toThrow(/ISO-8601/);
    expect(() =>
      parseSentimentPayload({
        runs: [{ ...((sentimentFixture as { runs: unknown[] }).runs[0] as object), topic_score: 3 }],
        posts: [],
      }),
    ).toThrow(/\[-1, 1\]/);
    expect(() =>
      parseSentimentPayload({
        runs: (sentimentFixture as { runs: unknown[] }).runs,
        posts: [{ ...((sentimentFixture as { posts: unknown[] }).posts[0] as object), platform: "tiktok" }],
      }),
    ).toThrow(/platform/);
  });
});

describe("derivations", () => {
  it("bands confidence like the pipeline's count/spread model", () => {
    expect(confidenceBand(0.9)).toBe("high");
    expect(confidenceBand(0.66)).toBe("high");
    expect(confidenceBand(0.65)).toBe("medium");
    expect(confidenceBand(0.4)).toBe("medium");
    expect(confidenceBand(0.39)).toBe("low");
    expect(confidenceBand(0)).toBe("low");
  });

  it("formats scores as qualitative bands, never two-decimal numbers", () => {
    expect(sentimentBand(0.94)).toBe("strong-positive");
    expect(sentimentBand(0.2385)).toBe("positive");
    expect(sentimentBand(0.1)).toBe("neutral");
    expect(sentimentBand(-0.2)).toBe("negative");
    expect(sentimentBand(-0.61)).toBe("strong-negative");
    expect(sentimentBand(0)).toBe("neutral");
  });

  it("computes the volume delta against the previous window only", () => {
    const data = parseSentimentPayload(payload);
    const dcCapex = data.runs.find((r) => r.topicId === "dc-capex");
    expect(dcCapex).toBeDefined();
    expect(volumeDelta(dcCapex!, data.previousCounts)).toBe(43);
    // A topic absent from the previous window is a first scan, not a delta from zero.
    expect(volumeDelta(dcCapex!, {})).toBeNull();
  });
});
