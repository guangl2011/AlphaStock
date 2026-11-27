import type { Metadata } from "next";
import Link from "next/link";
import { headers } from "next/headers";
import { IBM_Plex_Mono, Inter, Space_Grotesk } from "next/font/google";

import { Sidebar } from "@/components/sidebar";
import { AUTH_EMAIL_HEADER, AUTH_SHELL_HEADER, FIXTURE_USER_EMAIL } from "@/lib/auth";
import { isFixtureMode } from "@/lib/data";
import { getI18n, htmlLang } from "@/lib/i18n";
import { navItems } from "@/lib/nav";
import "./globals.css";

/**
 * Brand typography (gridcapital.net faces), self-hosted by next/font at build
 * time — no runtime external font requests. The generated CSS variables feed
 * the --font-* tokens in globals.css; CJK fallbacks live in the tokens.
 */
const displayFont = Space_Grotesk({
  subsets: ["latin"],
  variable: "--font-space-grotesk",
  display: "swap",
});
const bodyFont = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
  display: "swap",
});
const monoFont = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "600"],
  variable: "--font-ibm-plex-mono",
  display: "swap",
});

export async function generateMetadata(): Promise<Metadata> {
  const { t } = await getI18n();
  return { title: t.meta.siteTitle, description: t.meta.siteDescription };
}

/**
 * Root layout. The middleware resolves the session and forwards the verdict
 * via request headers: authenticated pages render the sidebar shell with the
 * session email (fixture mode uses the demo identity); the login page renders
 * standalone (no rail). One language per request (EN or 中文, kept in the
 * `as_lang` cookie); the global disclaimer renders in that language only.
 */
export default async function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  const { locale, t } = await getI18n();
  const fontVars = `${displayFont.variable} ${bodyFont.variable} ${monoFont.variable}`;
  const requestHeaders = await headers();
  const shell = requestHeaders.get(AUTH_SHELL_HEADER) === "1";
  const email = requestHeaders.get(AUTH_EMAIL_HEADER) ?? FIXTURE_USER_EMAIL;
  const fixture = isFixtureMode();

  if (!shell) {
    return (
      <html lang={htmlLang(locale)} className={fontVars}>
        <body>{children}</body>
      </html>
    );
  }

  return (
    <html lang={htmlLang(locale)} className={fontVars}>
      <body>
        <div className="as-shell">
          <Sidebar
            items={navItems(t.nav)}
            locale={locale}
            email={email}
            fixture={fixture}
            navLabels={t.nav}
            sidebarLabels={t.sidebar}
            accountLabels={t.account}
          />
          <div className="as-content">
            <main className="as-main">{children}</main>
            <footer className="as-footer">
              <p style={{ margin: 0 }}>{t.footer.disclaimer}</p>
              <p style={{ margin: "0.3rem 0 0" }}>
                <Link href="/track-record">{t.footer.trackRecord}</Link>
              </p>
            </footer>
          </div>
        </div>
      </body>
    </html>
  );
}
