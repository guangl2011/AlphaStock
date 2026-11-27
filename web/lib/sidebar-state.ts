/**
 * Sidebar collapse persistence. localStorage can throw (private mode, quota,
 * disabled storage) — a collapsed-rail preference must never take the page
 * down, so failures fall back to the expanded default by design.
 */
export const SIDEBAR_STORAGE_KEY = "as_sidebar_collapsed";

/** Minimal storage shape — real localStorage in the browser, fakes in tests. */
export type StorageLike = {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
};

/** True only for an explicit "1"; anything else (missing, "0", corrupt) is expanded. */
export function loadCollapsed(store: StorageLike | null | undefined): boolean {
  try {
    return store?.getItem(SIDEBAR_STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

export function persistCollapsed(
  store: StorageLike | null | undefined,
  collapsed: boolean,
): void {
  try {
    store?.setItem(SIDEBAR_STORAGE_KEY, collapsed ? "1" : "0");
  } catch {
    // Preference is best-effort; the session keeps the in-memory state.
  }
}
