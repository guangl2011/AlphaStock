import { isFixtureMode } from "@/lib/data";
import type { Messages } from "@/lib/messages";

/**
 * Honest demo-data notice. Shown on every data page when the app runs in
 * fixture mode, so fixture renders can never be mistaken for live data.
 */
export function FixtureNotice({ t }: { t: Messages }) {
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
      {t.fixtureNotice}
    </p>
  );
}

/** Marks research text that exists only in English, in the Chinese view. */
export function EnglishOnly({ t, show }: { t: Messages; show: boolean }) {
  if (!show) return null;
  return (
    <p className="as-meta" style={{ margin: "0.2rem 0", fontStyle: "italic" }}>
      {t.englishOnly}
    </p>
  );
}
