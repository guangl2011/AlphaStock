import type { Metadata } from "next";
import Link from "next/link";

import { SiteNav } from "@/components/nav";
import "./globals.css";

export const metadata: Metadata = {
  title: "AlphaStock Explorer",
  description:
    "Read-only Explorer for bilingual AI-energy and Physical-AI signal research.",
};

/**
 * Global disclaimer — every page inherits it. Content is non-personalized,
 * source-linked research, never investment advice (design doc §11).
 */
function SiteFooter() {
  return (
    <footer className="as-footer">
      <p style={{ margin: 0 }}>
        Research information only — not investment advice. All signals link to
        public sources and carry invalidation conditions. ·{" "}
        <span lang="zh-Hans">
          仅供研究参考，不构成投资建议；所有信号均附来源链接与失效条件。
        </span>
      </p>
      <p style={{ margin: "0.3rem 0 0" }}>
        <Link href="/track-record">Public track record</Link> ·{" "}
        <span lang="zh-Hans">公开业绩记录</span>
      </p>
    </footer>
  );
}

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>
        <div className="as-shell">
          <header className="as-header">
            <div className="as-header-inner">
              <Link href="/" className="as-logo">
                Alpha<span>Stock</span>
              </Link>
              <SiteNav />
            </div>
          </header>
          <main className="as-main">{children}</main>
          <SiteFooter />
        </div>
      </body>
    </html>
  );
}
