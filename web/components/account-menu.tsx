"use client";

import { usePathname, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";

import type { Locale } from "@/lib/i18n";
import type { Messages } from "@/lib/messages";

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

/** Language anchor rendered before hydration resolves the query string (same href minus the query). */
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

export interface AccountMenuProps {
  email: string;
  locale: Locale;
  /** Light mode is scoped out of V2.1 — rendered disabled with "coming soon". */
  themeDisabled: boolean;
  labels: Messages["account"];
  navLabels: Messages["nav"];
  sidebarLabels: Messages["sidebar"];
  /** Open on first render — for previews and SSR tests; users start closed. */
  defaultOpen?: boolean;
}

/**
 * Bottom-of-rail account popup. Email, language switch, disabled light mode,
 * and change-password / sign-out placeholders (auth lands in the next PR —
 * the two actions render disabled rather than pretending to work).
 */
export function AccountMenu({
  email,
  locale,
  themeDisabled,
  labels,
  navLabels,
  sidebarLabels,
  defaultOpen = false,
}: AccountMenuProps) {
  const [open, setOpen] = useState(defaultOpen);
  const containerRef = useRef<HTMLDivElement>(null);
  const other: Locale = locale === "en" ? "zh" : "en";

  // Close on click-outside and Escape while open; both listeners exist only
  // when open, so closed menus cost nothing.
  useEffect(() => {
    if (!open) return;
    const onClickOutside = (event: MouseEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onClickOutside);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onClickOutside);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  return (
    <div className="as-account" ref={containerRef}>
      <button
        type="button"
        className="as-account-toggle"
        aria-expanded={open}
        aria-haspopup="menu"
        aria-label={labels.title}
        onClick={() => setOpen((value) => !value)}
      >
        <span className="as-account-avatar" aria-hidden="true">
          {email.slice(0, 1).toUpperCase()}
        </span>
        <span className="as-account-email">{email}</span>
      </button>
      {open ? (
        <div className="as-account-popover" role="menu" aria-label={labels.title}>
          <p className="as-account-identity">{email}</p>
          {/* useSearchParams needs a Suspense boundary to keep pages statically renderable. */}
          <Suspense fallback={<LangSwitchFallback other={other} labels={navLabels} />}>
            <LangSwitchLink other={other} labels={navLabels} />
          </Suspense>
          <button
            type="button"
            className="as-account-item"
            disabled={themeDisabled}
            title={themeDisabled ? labels.comingSoon : undefined}
            onClick={() => setOpen(false)}
          >
            {labels.lightMode}
            {themeDisabled ? <span className="as-account-soon">{labels.comingSoon}</span> : null}
          </button>
          <button type="button" className="as-account-item" disabled>
            {labels.changePassword}
          </button>
          <button type="button" className="as-account-item" disabled>
            {labels.signOut}
          </button>
        </div>
      ) : null}
      <a className="as-sidebar-help" href="/help" title={sidebarLabels.help}>
        <span className="as-nav-icon" aria-hidden="true">
          ?
        </span>
        <span className="as-nav-label">{sidebarLabels.help}</span>
      </a>
    </div>
  );
}
