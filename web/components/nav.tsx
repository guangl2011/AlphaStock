"use client";

import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { Suspense } from "react";

import type { Locale } from "@/lib/i18n";
import type { Messages } from "@/lib/messages";

const NAV_KEYS = [
  ["/feed", "feed"],
  ["/themes", "themes"],
  ["/indices", "indices"],
  ["/research", "research"],
  ["/factors", "factors"],
  ["/sentiment", "sentiment"],
  ["/track-record", "trackRecord"],
  ["/alerts", "alerts"],
] as const;

/** Same path + query, other language — period selections like /themes?year=2026&quarter=Q3 survive the switch. */
function LangSwitchLink({ other, labels }: { other: Locale; labels: Messages["nav"] }) {
  const pathname = usePathname() || "/";
  const query = useSearchParams().toString();
  const next = query ? `${pathname}?${query}` : pathname;
  return (
    <a
      className="as-lang-switch"
      href={`/lang?l=${other}&next=${encodeURIComponent(next)}`}
      aria-label={labels.switchLabel}
      lang={other === "zh" ? "zh-Hans" : "en"}
    >
      {labels.switchTo}
    </a>
  );
}

/** Nav anchor rendered before hydration resolves the query string (same href minus the query). */
function LangSwitchFallback({ other, labels }: { other: Locale; labels: Messages["nav"] }) {
  const pathname = usePathname() || "/";
  return (
    <a
      className="as-lang-switch"
      href={`/lang?l=${other}&next=${encodeURIComponent(pathname)}`}
      aria-label={labels.switchLabel}
      lang={other === "zh" ? "zh-Hans" : "en"}
    >
      {labels.switchTo}
    </a>
  );
}

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
      {/* useSearchParams needs a Suspense boundary to keep pages statically renderable. */}
      <Suspense fallback={<LangSwitchFallback other={other} labels={labels} />}>
        <LangSwitchLink other={other} labels={labels} />
      </Suspense>
    </nav>
  );
}
