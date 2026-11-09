import { formatDate } from "@/lib/format";
import type { SourceLink } from "@/lib/types";

/**
 * Provenance block — sources and invalidation are ALWAYS visible on every
 * route (free-tier display rule, design doc §6). No component may hide or
 * truncate this block behind a toggle.
 */
export function Provenance({
  sources,
  invalidation,
}: {
  sources: SourceLink[];
  invalidation: string;
}) {
  return (
    <div className="as-provenance">
      <p className="as-provenance-title">Invalidation condition · 失效条件</p>
      <p className="as-invalidation" style={{ margin: "0 0 0.35rem" }}>
        {invalidation}
      </p>
      <p className="as-provenance-title">Sources · 来源</p>
      <ul>
        {sources.map((source, index) => (
          <li key={`${source.url}-${index}`}>
            <a href={source.url} target="_blank" rel="noopener noreferrer">
              {source.label}
            </a>{" "}
            <span className="as-meta">retrieved {formatDate(source.retrievedAt)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
