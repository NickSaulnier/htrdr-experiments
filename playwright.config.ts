import { defineConfig, devices } from "@playwright/test";

// Cross-browser end-to-end tests: pnpm e2e
//
// WebKit here is Playwright's WebKit build, the engine behind Safari, not
// Safari itself; real Safari needs macOS.
//
// Firefox is the installed, stock Firefox driven over WebDriver BiDi
// (experimental in Playwright). Playwright's own patched
// Firefox build fails to start on this machine: Windows rejects its
// mozglue.dll side-by-side assembly.
const PORT = 5175;

export default defineConfig({
  testDir: "e2e",
  timeout: 120_000,
  fullyParallel: true,
  reporter: [["list"]],
  use: {
    baseURL: `http://localhost:${PORT}`,
    acceptDownloads: true,
    viewport: { width: 1280, height: 800 },
  },
  webServer: {
    command: `pnpm dev --port ${PORT} --strictPort`,
    url: `http://localhost:${PORT}`,
    reuseExistingServer: true,
    timeout: 60_000,
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
    // A "moz-" channel makes Playwright drive the installed Firefox over BiDi.
    { name: "firefox", use: { ...devices["Desktop Firefox"], channel: "moz-firefox" } },
    { name: "webkit", use: { ...devices["Desktop Safari"] } },
  ],
});
