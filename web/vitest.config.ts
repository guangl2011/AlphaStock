import { fileURLToPath } from "node:url";

import { defineConfig } from "vitest/config";

// Mirrors the "@" path alias in tsconfig.json so component and page tests
// resolve imports exactly as the Next.js build does.
export default defineConfig({
  esbuild: {
    // The app ships with tsconfig jsx: preserve (Next transforms it); vitest
    // needs the automatic runtime to handle JSX in components and pages.
    jsx: "automatic",
  },
  resolve: {
    alias: {
      "@": fileURLToPath(new URL(".", import.meta.url)),
    },
  },
  test: {
    environment: "node",
    include: ["tests/**/*.test.ts", "tests/**/*.test.tsx"],
  },
});
