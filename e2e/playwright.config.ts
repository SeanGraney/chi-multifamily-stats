import { defineConfig, devices } from "@playwright/test";

/**
 * D22: Playwright is the E2E framework, confirmed over Puppeteer/Cypress/
 * Selenium. No global `webServer` block here on purpose — different specs
 * in this suite need different servers up (or none at all: the structural
 * specs are pure filesystem/content checks with no browser and no server).
 * Each spec that needs a live `rentcomp` server manages its own
 * build+spawn+teardown in `beforeAll`/`afterAll` so a broken server does
 * not take down specs that never needed one — see
 * `tests/f0-s1b-integration.spec.ts`.
 *
 * Zero live RentCast calls from this suite, ever (WORKFLOW.md §6) — every
 * spec that starts a real server points `RENTCOMP_HOME` at a fresh temp
 * dir and never sets `RENTCOMP_LIVE=1`.
 */
export default defineConfig({
  testDir: "./tests",
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: 0,
  workers: 1,
  reporter: [["list"]],
  timeout: 60_000,
  use: {
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
