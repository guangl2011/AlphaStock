/**
 * 中文 rendering: with the `as_lang=zh` cookie every page renders Chinese UI
 * text only — no English UI strings mixed in. (Proper nouns such as tickers,
 * index ids and ETF symbols are language-neutral; English-only research
 * prose is explicitly labelled as such.)
 */
process.env.ALPHASTOCK_DATA_SOURCE = "fixture";

import type { ReactElement } from "react";
import { renderToString } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/headers", () => ({
  cookies: async () => ({ get: (name: string) => (name === "as_lang" ? { value: "zh" } : undefined) }),
}));

import AlertsPage from "../app/alerts/page";
import FactorsPage from "../app/factors/page";
import FeedPage from "../app/feed/page";
import IndicesPage from "../app/indices/page";
import ResearchPage from "../app/research/page";
import ThemesPage from "../app/themes/page";
import { safeNextPath } from "../lib/i18n";
import { MESSAGES } from "../lib/messages";

async function renderPage(component: unknown, props: unknown = {}): Promise<string> {
  const page = component as (p: never) => Promise<ReactElement>;
  return renderToString(await page(props as never)).replace(/<!-- -->/g, "");
}

/** English UI words that must never appear in a 中文 render. */
const ENGLISH_UI = [
  "Signal Feed", "Demo data", "Read before the numbers", "Total return", "Large-Cap 100",
  "View signals", "Weekly Alerts", "Factor catalog", "Index factor exposures", "Since base",
];

function expectChineseOnly(html: string) {
  expect(html).toMatch(/[一-鿿]/);
  for (const word of ENGLISH_UI) expect(html, word).not.toContain(word);
}

describe("中文 renders", () => {
  it("/feed", async () => {
    const html = await renderPage(FeedPage, { searchParams: Promise.resolve({}) });
    expect(html).toContain("信号流");
    expect(html).toContain("劳登县");
    expectChineseOnly(html);
  });

  it("/themes", async () => {
    const html = await renderPage(ThemesPage);
    expect(html).toContain("查看信号");
    expect(html).toContain("年度总结");
    expect(html).toContain("年初至今");
    expect(html).toContain("精选市场发现");
    expect(html).toContain("Delta Forge 园区");
    expectChineseOnly(html);
  });

  it("/alerts", async () => {
    const html = await renderPage(AlertsPage);
    expect(html).toContain("待负责人批准，未发送");
    expectChineseOnly(html);
  });

  it("/indices", async () => {
    const html = await renderPage(IndicesPage);
    expect(html).toContain("大盘 100 指数");
    expect(html).toContain("混合市值成长 100 指数");
    expect(html).toContain("混合市值均衡 100 指数");
    expect(html).toContain("标普中盘 400（ETF 代理）");
    expectChineseOnly(html);
  });

  it("/research", async () => {
    const html = await renderPage(ResearchPage);
    expect(html).toContain("阅读数据前须知");
    expect(html).toContain("幸存者偏差");
    expect(html).toContain("以下研究原文暂只有英文版本");
    expectChineseOnly(html);
  });

  it("/factors", async () => {
    const html = await renderPage(FactorsPage);
    expect(html).toContain("因子目录");
    expect(html).toContain("相关系数矩阵");
    expectChineseOnly(html);
  });
});

describe("dictionaries", () => {
  it("themes.findings keys exist in both locales", () => {
    for (const locale of ["en", "zh"] as const) {
      const findings = MESSAGES[locale].themes.findings;
      expect(typeof findings.yearlyTitle).toBe("string");
      expect(typeof findings.quarterlyTitle).toBe("string");
      expect(typeof findings.yearToDate).toBe("string");
      expect(typeof findings.curated).toBe("string");
      expect(typeof findings.empty).toBe("string");
      expect(typeof findings.sourceHeading).toBe("string");
      expect(typeof findings.quarterLabel).toBe("function");
    }
  });

  it("zh mirrors every en key (type-enforced; spot-check at runtime)", () => {
    const keys = (o: object): string[] =>
      Object.entries(o).flatMap(([k, v]) =>
        v && typeof v === "object" && !Array.isArray(v) ? keys(v).map((x) => `${k}.${x}`) : [k],
      );
    expect(keys(MESSAGES.zh).sort()).toEqual(keys(MESSAGES.en).sort());
  });

  it("the language switch only redirects to same-site paths", () => {
    expect(safeNextPath("/indices")).toBe("/indices");
    expect(safeNextPath("//evil.example")).toBe("/");
    expect(safeNextPath("https://evil.example")).toBe("/");
    expect(safeNextPath(null)).toBe("/");
  });
});
