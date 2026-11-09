import { defineConfig, devices } from "@playwright/test";

// Smoke tests run against the production build served by `next start`, in
// fixture mode — no live Supabase and no runtime LLM in CI.
export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 30_000,
  expect: { timeout: 10_000 },
  use: {
    baseURL: "http://localhost:3100",
    trace: "retain-on-failure",
  },
  webServer: {
    command: "npm run start",
    port: 3100,
    env: { ...process.env, ALPHASTOCK_DATA_SOURCE: "fixture", PORT: "3100" },
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
