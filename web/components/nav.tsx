"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import type { Locale } from "@/lib/i18n";
import type { Messages } from "@/lib/messages";

const NAV_KEYS = [
  ["/feed", "feed"],
  ["/themes", "themes"],
  ["/indices", "indices"],
  ["/research", "research"],
  ["/factors", "factors"],
  ["/track-record", "trackRecord"],
  ["/alerts", "alerts"],
] as const;

/** Brand nav; `usePathname` marks the current route and keeps the language switch in place. */
export function SiteNav({ locale, labels }: { locale: Locale; labels: Messages["nav"] }) {
  const pathname = usePathname() || "/";
  const other: Locale = locale === "en" ? "zh" : "en";

  return (
    <nav className="as-nav" aria-label={labels.primary}>
      {NAV_KEYS.map(([href, key]) => (
        <Link
          key={href}
          href={href}
          aria-current={
            pathname === href || pathname.startsWith(`${href}/`) ? "page" : undefined
          }
        >
          {labels[key]}
        </Link>
      ))}
      <a
        className="as-lang-switch"
        href={`/lang?l=${other}&next=${encodeURIComponent(pathname)}`}
        aria-label={labels.switchLabel}
        lang={other === "zh" ? "zh-Hans" : "en"}
      >
        {labels.switchTo}
      </a>
    </nav>
  );
}
