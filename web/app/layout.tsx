import type { Metadata } from "next";
import Link from "next/link";

import { SiteNav } from "@/components/nav";
import { getI18n, htmlLang } from "@/lib/i18n";
import "./globals.css";

export async function generateMetadata(): Promise<Metadata> {
  const { t } = await getI18n();
  return { title: t.meta.siteTitle, description: t.meta.siteDescription };
}

/**
 * Root layout. One language per request (EN or 中文, chosen in the nav and kept
 * in the `as_lang` cookie); the global disclaimer renders in that language only.
 */
export default async function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  const { locale, t } = await getI18n();
  return (
    <html lang={htmlLang(locale)}>
      <body>
        <div className="as-shell">
          <header className="as-header">
            <div className="as-header-inner">
              <Link href="/" className="as-logo">
                Alpha<span>Stock</span>
              </Link>
              <SiteNav locale={locale} labels={t.nav} />
            </div>
          </header>
          <main className="as-main">{children}</main>
          <footer className="as-footer">
            <p style={{ margin: 0 }}>{t.footer.disclaimer}</p>
            <p style={{ margin: "0.3rem 0 0" }}>
              <Link href="/track-record">{t.footer.trackRecord}</Link>
            </p>
          </footer>
        </div>
      </body>
    </html>
  );
}
