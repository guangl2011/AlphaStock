import { getI18n } from "@/lib/i18n";

export const dynamic = "force-dynamic";

export async function generateMetadata() {
  const { t } = await getI18n();
  return { title: t.help.title };
}

/**
 * Placeholder help index — the knowledge-base content itself is scoped out of
 * this PR; the sidebar's Help entry needs an honest landing page meanwhile.
 */
export default async function HelpPage() {
  const { t } = await getI18n();
  return (
    <>
      <h1 className="as-page-title">{t.help.title}</h1>
      <p className="as-page-sub">{t.help.sub}</p>
      {t.help.sections.map((section) => (
        <section key={section.heading}>
          <h2 className="as-section-title">{section.heading}</h2>
          <p>{section.body}</p>
        </section>
      ))}
    </>
  );
}
