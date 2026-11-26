"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

import { AccountMenu } from "@/components/account-menu";
import type { Locale } from "@/lib/i18n";
import type { NavItem } from "@/lib/nav";
import type { Messages } from "@/lib/messages";
import { loadCollapsed, persistCollapsed } from "@/lib/sidebar-state";

export interface SidebarProps {
  items: NavItem[];
  /** Controlled collapse state; when omitted the rail persists to localStorage itself. */
  collapsed?: boolean;
  /** Active route override (tests/previews); defaults to the current pathname. */
  activePath?: string;
  /** Change callback for controlled usage; uncontrolled usage persists internally. */
  onToggle?: (collapsed: boolean) => void;
  locale: Locale;
  email: string;
  /** Serializable label groups only — the full dictionary carries functions
   * and cannot cross the server→client boundary. */
  navLabels: Messages["nav"];
  sidebarLabels: Messages["sidebar"];
  accountLabels: Messages["account"];
  /** Open the account popup on first render (previews/tests). */
  defaultOpen?: boolean;
}

/**
 * The V2.1 shell: a left brand rail replacing the old top bar. Server render
 * is expanded by default (no flash); the stored preference applies after
 * hydration. Below 900px the rail becomes a drawer opened from a mobile bar.
 */
export function Sidebar({
  items,
  collapsed,
  activePath,
  onToggle,
  locale,
  email,
  navLabels,
  sidebarLabels,
  accountLabels,
  defaultOpen = false,
}: SidebarProps) {
  const [internalCollapsed, setInternalCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const isControlled = collapsed !== undefined;
  const isCollapsed = isControlled ? collapsed : internalCollapsed;
  const pathname = usePathname() || "/";
  const active = activePath ?? pathname;

  // Hydrate the persisted preference once; server render stays expanded so
  // there is no layout flash before this effect runs.
  useEffect(() => {
    if (!isControlled) setInternalCollapsed(loadCollapsed(window.localStorage));
    // eslint-disable-next-line react-hooks/exhaustive-deps -- isControlled is constant for a mount
  }, []);

  const toggle = () => {
    const next = !isCollapsed;
    if (isControlled) {
      onToggle?.(next);
      return;
    }
    setInternalCollapsed(next);
    persistCollapsed(window.localStorage, next);
  };

  const navLink = (item: NavItem) => (
    <Link
      key={item.href}
      href={item.href}
      className="as-nav-link"
      aria-current={
        active === item.href || active.startsWith(`${item.href}/`) ? "page" : undefined
      }
      title={isCollapsed ? item.label : undefined}
      onClick={() => setMobileOpen(false)}
    >
      <span className="as-nav-icon" aria-hidden="true">
        {item.icon}
      </span>
      <span className="as-nav-label">{item.label}</span>
    </Link>
  );

  return (
    <>
      {/* Mobile-only top bar; the drawer below is transformed off-canvas on small screens. */}
      <div className="as-mobile-bar">
        <button
          type="button"
          className="as-rail-toggle as-mobile-toggle"
          aria-expanded={mobileOpen}
          aria-controls="as-sidebar-nav"
          aria-label={mobileOpen ? sidebarLabels.closeMenu : sidebarLabels.menu}
          onClick={() => setMobileOpen((value) => !value)}
        >
          ☰
        </button>
        <Link href="/" className="as-logo">
          Alpha<span>Stock</span>
        </Link>
      </div>
      {mobileOpen ? (
        <div
          className="as-scrim"
          role="presentation"
          onClick={() => setMobileOpen(false)}
        />
      ) : null}
      <aside
        className={`as-sidebar${isCollapsed ? " as-collapsed" : ""}${
          mobileOpen ? " as-mobile-open" : ""
        }`}
      >
        <div className="as-sidebar-top">
          <Link href="/" className="as-logo" title={isCollapsed ? "AlphaStock" : undefined}>
            Alpha<span>Stock</span>
          </Link>
          <button
            type="button"
            className="as-rail-toggle"
            aria-expanded={!isCollapsed}
            aria-controls="as-sidebar-nav"
            aria-label={isCollapsed ? sidebarLabels.expand : sidebarLabels.collapse}
            onClick={toggle}
          >
            {isCollapsed ? "»" : "«"}
          </button>
        </div>
        <nav className="as-sidebar-nav" id="as-sidebar-nav" aria-label={navLabels.primary}>
          {items.map(navLink)}
        </nav>
        <div className="as-sidebar-bottom">
          <AccountMenu
            email={email}
            locale={locale}
            themeDisabled
            labels={accountLabels}
            navLabels={navLabels}
            sidebarLabels={sidebarLabels}
            defaultOpen={defaultOpen}
          />
        </div>
      </aside>
    </>
  );
}
