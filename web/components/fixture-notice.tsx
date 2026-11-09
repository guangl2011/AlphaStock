import { isFixtureMode } from "@/lib/data";

/**
 * Honest demo-data notice. Shown on every data page when the app runs in
 * fixture mode, so fixture renders can never be mistaken for live data.
 */
export function FixtureNotice() {
  if (!isFixtureMode()) return null;
  return (
    <p
      className="as-meta"
      role="note"
      style={{
        background: "var(--color-accent-soft)",
        border: "1px solid var(--brand-cyan)",
        borderRadius: "var(--radius)",
        padding: "0.45rem 0.75rem",
        margin: "0 0 1.25rem",
      }}
    >
      Demo data — this environment has no live signal store connected. ·{" "}
      <span lang="zh-Hans">演示数据 — 当前环境未连接实时信号库。</span>
    </p>
  );
}
