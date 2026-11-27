/**
 * Sidebar + AccountMenu shell tests. SSR renders (react-dom/server) cover the
 * static contract — nav items, active marker, collapsed default, closed
 * account menu, language-switch href — and the pure persistence helpers in
 * sidebar-state.ts carry the collapse behavior that needs a browser.
 */
process.env.ALPHASTOCK_DATA_SOURCE = "fixture";

import { renderToString } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  usePathname: () => "/themes",
  useSearchParams: () => new URLSearchParams("year=2026&quarter=Q3"),
}));

import { AccountMenu } from "../components/account-menu";
import { Sidebar } from "../components/sidebar";
import { navItems } from "../lib/nav";
import { MESSAGES } from "../lib/messages";
import {
  SIDEBAR_STORAGE_KEY,
  loadCollapsed,
  persistCollapsed,
} from "../lib/sidebar-state";

function fakeStorage(initial: Record<string, string> = {}) {
  const map = new Map(Object.entries(initial));
  return {
    getItem: (key: string) => map.get(key) ?? null,
    setItem: (key: string, value: string) => void map.set(key, value),
  };
}

function renderSidebar(props: Partial<Parameters<typeof Sidebar>[0]> = {}) {
  return renderToString(
    <Sidebar
      items={navItems(MESSAGES.en.nav)}
      locale="en"
      email="researcher@gridcapital.net"
      fixture
      navLabels={MESSAGES.en.nav}
      sidebarLabels={MESSAGES.en.sidebar}
      accountLabels={MESSAGES.en.account}
      {...props}
    />,
  ).replace(/<!-- -->/g, "");
}

describe("Sidebar", () => {
  it("renders every nav item with its label and href", () => {
    const html = renderSidebar();
    for (const item of navItems(MESSAGES.en.nav)) {
      expect(html).toContain(`href="${item.href}"`);
      expect(html).toContain(item.label);
    }
  });

  it("marks the current route with aria-current=page", () => {
    const html = renderSidebar();
    const current = html.match(/<a[^>]*href="\/themes"[^>]*>/);
    expect(current?.[0]).toContain('aria-current="page"');
    // Exactly one active item — the rail never highlights two routes.
    expect(html.match(/aria-current="page"/g)?.length).toBe(1);
  });

  it("server-renders expanded by default (no flash) with the collapse control", () => {
    const html = renderSidebar();
    expect(html).not.toContain("as-collapsed");
    const toggle = html.match(/<button[^>]*class="as-rail-toggle"[^>]*>/);
    expect(toggle?.[0]).toContain('aria-expanded="true"');
    expect(toggle?.[0]).toContain('aria-controls="as-sidebar-nav"');
  });

  it("renders the bottom-fixed Help entry and account toggle closed by default", () => {
    const html = renderSidebar();
    expect(html).toContain('href="/help"');
    expect(html.match(/class="as-account-toggle"/g)?.length).toBe(1);
    expect(html).toContain('aria-expanded="false"');
    // Placeholder actions stay inside the closed menu; nothing pretends to work.
    expect(html).not.toContain("Sign out");
    expect(html).not.toContain("Light mode");
  });

  it("renders the open menu on demand: one language switch, fixture disables password change only", () => {
    const html = renderSidebar({ defaultOpen: true });
    expect(html.match(/as-lang-switch/g)?.length).toBe(1);
    // The lang switch renders the full query-preserving href even before hydration.
    expect(html).toContain(
      `href="/lang?l=zh&amp;next=${encodeURIComponent("/themes?year=2026&quarter=Q3")}"`,
    );
    expect(html).toContain("Light mode");
    expect(html).toContain("Coming soon");
    // Fixture mode: light mode + password change disabled; sign-out works.
    expect(html.match(/disabled/g)?.length).toBe(2);
    expect(html).toContain("Sign out");
  });

  it("renders exactly one language switch link", () => {
    const html = renderSidebar({ defaultOpen: true });
    expect(html.match(/as-lang-switch/g)?.length).toBe(1);
  });
});

describe("sidebar collapse persistence", () => {
  it("treats anything but an explicit '1' as expanded", () => {
    expect(loadCollapsed(null)).toBe(false);
    expect(loadCollapsed(fakeStorage())).toBe(false);
    expect(loadCollapsed(fakeStorage({ [SIDEBAR_STORAGE_KEY]: "0" }))).toBe(false);
    expect(loadCollapsed(fakeStorage({ [SIDEBAR_STORAGE_KEY]: "garbage" }))).toBe(false);
    expect(loadCollapsed(fakeStorage({ [SIDEBAR_STORAGE_KEY]: "1" }))).toBe(true);
  });

  it("persists both directions so reload restores the last state", () => {
    const store = fakeStorage();
    persistCollapsed(store, true);
    expect(store.getItem(SIDEBAR_STORAGE_KEY)).toBe("1");
    expect(loadCollapsed(store)).toBe(true);
    persistCollapsed(store, false);
    expect(loadCollapsed(store)).toBe(false);
  });

  it("survives storage failures instead of breaking the shell", () => {
    const throwing = {
      getItem: () => {
        throw new Error("blocked");
      },
      setItem: () => {
        throw new Error("blocked");
      },
    };
    expect(() => loadCollapsed(throwing)).not.toThrow();
    expect(() => persistCollapsed(throwing, true)).not.toThrow();
    expect(loadCollapsed(throwing)).toBe(false);
  });
});

describe("AccountMenu", () => {
  it("disables light mode and (in fixture) password change when open; sign-out is a live form", () => {
    const html = renderToString(
      <AccountMenu
        email="researcher@gridcapital.net"
        locale="en"
        themeDisabled
        fixture
        labels={MESSAGES.en.account}
        navLabels={MESSAGES.en.nav}
        sidebarLabels={MESSAGES.en.sidebar}
        defaultOpen
      />,
    );
    expect(html).toContain("researcher@gridcapital.net");
    expect(html.match(/disabled/g)?.length).toBe(2);
    expect(html).toContain("Light mode");
    expect(html).toContain("Coming soon");
    // Sign-out posts to a server action form, not a dead button.
    expect(html).toContain("Sign out");
    expect(html).not.toContain('type="button" disabled>Sign out');
    // No password form until the item opens, and none in fixture mode.
    expect(html).not.toContain("New password");
  });

  it("keeps the query-preserving language-switch href contract", () => {
    const html = renderToString(
      <AccountMenu
        email="researcher@gridcapital.net"
        locale="en"
        themeDisabled
        fixture
        labels={MESSAGES.en.account}
        navLabels={MESSAGES.en.nav}
        sidebarLabels={MESSAGES.en.sidebar}
        defaultOpen
      />,
    );
    // SSR escapes attribute ampersands; the encoded next value itself is ampersand-free.
    expect(html).toContain(
      `href="/lang?l=zh&amp;next=${encodeURIComponent("/themes?year=2026&quarter=Q3")}"`,
    );
  });
});
