/**
 * Empty state — never a blank region. Explains why it is empty and what
 * would make content appear (the no-signals-yet / no-match cases).
 */
export function EmptyState({
  title,
  hint,
}: {
  title: string;
  hint: string;
}) {
  return (
    <div className="as-empty" role="status">
      <p style={{ fontWeight: 600, margin: "0 0 0.25rem" }}>{title}</p>
      <p style={{ margin: 0 }}>{hint}</p>
    </div>
  );
}
