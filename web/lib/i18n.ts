/**
 * Locale plumbing. The Explorer renders exactly one language per request —
 * English or Simplified Chinese, never the two mixed on one page. The choice
 * lives in the `as_lang` cookie, set by the /lang route from the nav switch.
 */
import { cookies } from "next/headers";

import { MESSAGES, type Messages } from "./messages";

export type Locale = "en" | "zh";

export const LOCALE_COOKIE = "as_lang";
export const LOCALES: readonly Locale[] = ["en", "zh"];

export function isLocale(value: unknown): value is Locale {
  return value === "en" || value === "zh";
}

/** BCP-47 tag for <html lang>. */
export function htmlLang(locale: Locale): string {
  return locale === "zh" ? "zh-Hans" : "en";
}

/** The request's locale; defaults to English (also outside a request, e.g. tests). */
export async function getLocale(): Promise<Locale> {
  try {
    const store = await cookies();
    const value = store.get(LOCALE_COOKIE)?.value;
    return isLocale(value) ? value : "en";
  } catch {
    return "en";
  }
}

export function messagesFor(locale: Locale): Messages {
  return MESSAGES[locale];
}

/** Locale + its dictionary, the one call every server page makes. */
export async function getI18n(): Promise<{ locale: Locale; t: Messages }> {
  const locale = await getLocale();
  return { locale, t: messagesFor(locale) };
}

/** Only same-site relative paths may be redirect targets after a switch. */
export function safeNextPath(next: string | null | undefined): string {
  if (!next || !next.startsWith("/") || next.startsWith("//") || next.includes("\\")) {
    return "/";
  }
  return next;
}
