"use client";

/**
 * Route-level error boundary — data-layer failures surface here with a
 * recovery action instead of a blank page or a swallowed error.
 */
export default function ErrorPage({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <div className="as-error" role="alert">
      <h2>Something went wrong loading this page</h2>
      <p>
        The Explorer could not read the signal store. This is usually
        temporary — a source or the database may be briefly unavailable.
      </p>
      {error.digest ? (
        <p className="as-meta">Reference: {error.digest}</p>
      ) : null}
      <button type="button" onClick={reset}>
        Try again
      </button>
    </div>
  );
}
