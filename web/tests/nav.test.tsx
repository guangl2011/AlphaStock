/**
 * Sidebar navigation contract (V2.1 shell). The eight Explorer routes migrate
 * 1:1 from the old top bar; /companies, /reports and /screener join the same
 * rail. Order is the product contract — assertions here, never weakened to go
 * green.
 */
process.env.ALPHASTOCK_DATA_SOURCE = "fixture";

import { describe, expect, it } from "vitest";

import { navItems } from "../lib/nav";
import { MESSAGES } from "../lib/messages";

describe("sidebar nav contract", () => {
  it("carries all eight Explorer routes plus /companies and /reports, in order", () => {
    const hrefs = navItems(MESSAGES.en.nav).map((item) => item.href);
    expect(hrefs).toEqual([
      "/feed",
      "/themes",
      "/indices",
      "/research",
      "/factors",
      "/sentiment",
      "/daily-alpha",
      "/track-record",
      "/alerts",
      "/companies",
      "/reports",
      "/screener",
    ]);
  });

  it("keeps Market Sentiment between Factors and Track Record", () => {
    const items = navItems(MESSAGES.en.nav).map((item) => item.href);
    expect(items.indexOf("/sentiment")).toBeGreaterThan(items.indexOf("/factors"));
    expect(items.indexOf("/sentiment")).toBeLessThan(items.indexOf("/track-record"));
  });

  it("renders one bilingual label per item, in the active language only", () => {
    for (const item of navItems(MESSAGES.en.nav)) {
      expect(item.label, item.href).toMatch(/^[A-Za-z][A-Za-z ]*$/);
      expect(item.icon, item.href).not.toBe("");
    }
    const zhItems = navItems(MESSAGES.zh.nav).map((item) => item.label);
    expect(zhItems).toEqual(["信号流", "主题热度", "指数", "回溯研究", "因子库", "市场情绪", "每日信号", "存证记录", "每周警报", "公司", "周报", "筛选器"]);
  });
});
