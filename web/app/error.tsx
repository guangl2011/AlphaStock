"use client";

import { useEffect, useState } from "react";

import { MESSAGES } from "@/lib/messages";

/**
 * Route-level error boundary — data-layer failures surface here with a
 * recovery action instead of a blank page or a swallowed error. Client-side,
 * so the language comes from the same `as_lang` cookie the server reads.
 */
export default function ErrorPage({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  const [locale, setLocale] = useState<"en" | "zh">("en");
  useEffect(() => {
    if (document.cookie.split("; ").includes("as_lang=zh")) setLocale("zh");
  }, []);
  const t = MESSAGES[locale].error;
  return (
    <div className="as-error" role="alert">
      <h2>{t.title}</h2>
      <p>{t.body}</p>
      {error.digest ? <p className="as-meta">{t.reference(error.digest)}</p> : null}
      <button type="button" onClick={reset}>
        {t.retry}
      </button>
    </div>
  );
}
