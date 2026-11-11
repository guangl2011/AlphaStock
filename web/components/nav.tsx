"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const NAV_LINKS = [
  { href: "/feed", label: "Signal Feed" },
  { href: "/themes", label: "Themes" },
  { href: "/indices", label: "Indices" },
  { href: "/track-record", label: "Track Record" },
  { href: "/alerts", label: "Alerts" },
] as const;

/** Brand nav; `usePathname` marks the current route for orientation. */
export function SiteNav() {
  const pathname = usePathname();

  return (
    <nav className="as-nav" aria-label="Primary">
      {NAV_LINKS.map(({ href, label }) => (
        <Link
          key={href}
          href={href}
          aria-current={
            pathname === href || pathname.startsWith(`${href}/`)
              ? "page"
              : undefined
          }
        >
          {label}
        </Link>
      ))}
    </nav>
  );
}
