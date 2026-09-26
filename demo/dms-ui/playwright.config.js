// @ts-check
const { defineConfig, devices } = require("@playwright/test");

const API = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";
const UI = process.env.PLAYWRIGHT_BASE_URL || "http://127.0.0.1:3000";
// T2-FAILCLOSED (#263): no key ships with Cortex. Take the viewer key from the
// environment (demo/run_demo.ps1 exports it); fail loudly instead of guessing.
const VIEWER_KEY = process.env.NEXT_PUBLIC_DMS_VIEWER_KEY || process.env.DMS_E2E_API_KEY || "";
if (!VIEWER_KEY) {
  throw new Error(
    "Set NEXT_PUBLIC_DMS_VIEWER_KEY (or DMS_E2E_API_KEY) to a viewer key the engine accepts; " +
      "demo/run_demo.ps1 generates one per install in data/local/demo_api_keys.env."
  );
}

module.exports = defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  fullyParallel: false,
  retries: process.env.CI ? 1 : 0,
  reporter: [["list"]],
  use: {
    baseURL: UI,
    trace: "on-first-retry",
    extraHTTPHeaders: {
      "X-API-Key": VIEWER_KEY,
    },
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: process.env.PLAYWRIGHT_SKIP_WEBSERVER
    ? undefined
    : {
        command: "npm run dev",
        url: UI,
        reuseExistingServer: !process.env.CI,
        timeout: 120_000,
      },
  metadata: { api: API },
});
