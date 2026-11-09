import { describe, expect, it } from "vitest";

import { scoreDirection } from "../lib/alpha";

describe("scoreDirection", () => {
  it("labels scores by band", () => {
    expect(scoreDirection(70)).toBe("positive");
    expect(scoreDirection(50)).toBe("neutral");
    expect(scoreDirection(20)).toBe("negative");
  });
});
