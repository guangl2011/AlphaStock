import Link from "next/link";

import { getI18n } from "@/lib/i18n";

export default async function Home() {
  const { t } = await getI18n();
  return (
    <>
      <section className="as-hero">
        <h1>{t.home.headline}</h1>
        <p>{t.home.intro}</p>
      </section>

      <div className="as-hero-card-grid">
        {t.home.surfaces.map(({ href, title, blurb }) => (
          <Link key={href} href={href} className="as-card">
            <h2 className="as-card-title">{title}</h2>
            <p className="as-meta">{blurb}</p>
          </Link>
        ))}
      </div>

      <p className="as-meta" style={{ marginTop: "1.5rem" }}>
        {t.home.companyHint} <Link href="/companies/VST">/companies/VST</Link>. {t.home.rules}
      </p>
    </>
  );
}
