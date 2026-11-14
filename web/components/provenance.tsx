import { formatDate } from "@/lib/format";
import type { Locale } from "@/lib/i18n";
import type { Messages } from "@/lib/messages";
import type { SourceLink } from "@/lib/types";

import { EnglishOnly } from "./fixture-notice";

/**
 * Provenance block — sources and invalidation are ALWAYS visible on every
 * route (free-tier display rule, design doc §6). No component may hide or
 * truncate this block behind a toggle. Invalidation text is authored in
 * English; the Chinese view labels it as English-only rather than mixing.
 */
export function Provenance({
  sources,
  invalidation,
  t,
  locale,
}: {
  sources: SourceLink[];
  invalidation: string;
  t: Messages;
  locale: Locale;
}) {
  return (
    <div className="as-provenance">
      <p className="as-provenance-title">{t.provenance.invalidation}</p>
      <EnglishOnly t={t} show={locale === "zh"} />
      <p className="as-invalidation" style={{ margin: "0 0 0.35rem" }} lang="en">
        {invalidation}
      </p>
      <p className="as-provenance-title">{t.provenance.sources}</p>
      <ul>
        {sources.map((source, index) => (
          <li key={`${source.url}-${index}`}>
            <a href={source.url} target="_blank" rel="noopener noreferrer" lang="en">
              {source.label}
            </a>{" "}
            <span className="as-meta">{t.provenance.retrieved(formatDate(source.retrievedAt))}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
