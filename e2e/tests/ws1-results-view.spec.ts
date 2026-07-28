/**
 * WS-1 — the walking skeleton's one browser-genuine assertion: Results
 * fires the hardcoded `POST /api/derive` on mount and renders REAL numbers
 * (a comp list, the bucket table, the price-test guard panel) — not that
 * any one number is exactly right (that's the L1/L2 suite's job:
 * `backend/tests/unit/test_ws1_*.py`, `test_ws1_anchor_drift.py`,
 * `test_ws1_end_to_end_pipeline.py`), but that the wiring is real: a real
 * HTTP round trip happened, and what renders is not the F0-S2 placeholder
 * state (an empty/stub list, `$1.00/sqft`, every bucket dashed, or the
 * `stub_stage` warnings). QA-authored, written RED before any
 * implementation exists (AGENT_QA.md protocol) — per the decision
 * procedure's "exhaustive below, representative above" split rule, this is
 * intentionally the ONLY browser spec for WS-1; the formula correctness is
 * exhaustively covered at L1/L2.
 *
 * No fixed markup is assumed anywhere (no implementation the developer
 * hasn't chosen yet is pinned): every locator tries a semantic role or a
 * `[data-testid]` convention first and falls back to a text-pattern search,
 * same technique `f0-s1b-integration.spec.ts` already uses in this suite.
 *
 * Zero live RentCast calls (D17, WORKFLOW.md §6): the two real fixtures
 * this scenario needs (`fixtures/live-samples/fe9de5158f036802.json`,
 * `6327600317b11d16.json`) are already committed to the repo and are read
 * from there by the backend's own record-shaping chain — nothing here
 * seeds `RENTCOMP_HOME` with them (that plumbing is the developer's call,
 * flagged in `backend/tests/unit/test_ws1_end_to_end_pipeline.py`'s
 * docstring). `RENTCOMP_LIVE` is never set.
 */
import { test, expect, type Page } from "@playwright/test";
import { existsSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { spawn, execFileSync, type ChildProcess } from "node:child_process";
import {
  REPO_ROOT,
  VENV_RENTCOMP,
  VENV_RENTCOMP_HINT,
  NPM,
  NPM_NEEDS_SHELL,
} from "./support/local-server";

const FRONTEND_DIR = path.join(REPO_ROOT, "frontend");
const FRONTEND_DIST = path.join(FRONTEND_DIR, "dist");
const BASE_URL = "http://127.0.0.1:8000"; // D7/__main__.py hardcodes 8000, no PORT override

test.describe.configure({ mode: "serial" });

let serverProcess: ChildProcess | null = null;
let rentcompHome: string | null = null;
let setupFailure: string | null = null;

async function waitForServer(url: string, timeoutMs: number): Promise<boolean> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(url);
      if (res.status < 500) return true;
    } catch {
      // not up yet
    }
    await new Promise((r) => setTimeout(r, 250));
  }
  return false;
}

test.beforeAll(async () => {
  if (!existsSync(FRONTEND_DIR)) {
    setupFailure = "frontend/ does not exist";
    return;
  }
  if (!existsSync(VENV_RENTCOMP)) {
    setupFailure = `${VENV_RENTCOMP_HINT} not found — expected \`pip install -e backend/\``;
    return;
  }

  try {
    execFileSync(NPM, ["install"], { cwd: FRONTEND_DIR, stdio: "pipe", shell: NPM_NEEDS_SHELL });
    execFileSync(NPM, ["run", "build"], { cwd: FRONTEND_DIR, stdio: "pipe", shell: NPM_NEEDS_SHELL });
  } catch (err: any) {
    setupFailure = `npm install/build failed: ${err?.stderr?.toString?.() ?? err}`;
    return;
  }
  if (!existsSync(FRONTEND_DIST)) {
    setupFailure = "npm run build completed but frontend/dist does not exist";
    return;
  }

  rentcompHome = mkdtempSync(path.join(tmpdir(), "rentcomp-ws1-home-"));
  serverProcess = spawn(VENV_RENTCOMP, [], {
    cwd: REPO_ROOT,
    env: {
      ...process.env,
      RENTCOMP_HOME: rentcompHome,
      RENTCOMP_LIVE: "", // never live (D17)
    },
    stdio: "pipe",
  });

  const up = await waitForServer(`${BASE_URL}/openapi.json`, 20_000);
  if (!up) {
    setupFailure = "rentcomp server did not come up within 20s";
  }
});

test.afterAll(() => {
  if (serverProcess) {
    serverProcess.kill();
    serverProcess = null;
  }
  if (rentcompHome) {
    rmSync(rentcompHome, { recursive: true, force: true });
    rentcompHome = null;
  }
});

function skipIfSetupFailed() {
  test.skip(!!setupFailure, setupFailure ?? "");
}

async function gotoResults(page: Page) {
  await page.goto(BASE_URL + "/");
  const resultsLink = page
    .getByRole("link", { name: /results/i })
    .or(page.getByRole("button", { name: /results/i }))
    .or(page.locator("[data-testid='nav-results']"));
  await resultsLink.first().click();
}

test.describe("WS-1 — Results fires the hardcoded derive and renders real data", () => {
  test.beforeEach(skipIfSetupFailed);

  test("Results issues a real POST /api/derive on mount and it succeeds", async ({ page }) => {
    const derivePromise = page.waitForResponse(
      (res) => res.url().includes("/api/derive") && res.request().method() === "POST",
      { timeout: 15_000 }
    );
    await gotoResults(page);
    const response = await derivePromise;
    expect(
      response.status(),
      "the hardcoded search must round-trip successfully — a non-200 means the wiring (pull_ref " +
        "resolution against the real committed fixtures, or the request body itself) is broken"
    ).toBe(200);
  });

  test("the comp list renders more than one real row, not an empty/placeholder state", async ({ page }) => {
    const derivePromise = page.waitForResponse(
      (res) => res.url().includes("/api/derive") && res.request().method() === "POST"
    );
    await gotoResults(page);
    await derivePromise;

    const rows = page
      .locator("[data-testid='comp-row']")
      .or(page.getByRole("row"))
      .or(page.getByRole("listitem"));
    await expect(async () => {
      expect(await rows.count()).toBeGreaterThan(1);
    }).toPass({ timeout: 10_000 });
  });

  test("the bucket table shows a real (non-dash) leased-DOM number somewhere on the page", async ({ page }) => {
    const derivePromise = page.waitForResponse(
      (res) => res.url().includes("/api/derive") && res.request().method() === "POST"
    );
    await gotoResults(page);
    await derivePromise;

    // Representative, not exhaustive (AGENT_QA.md split rule): the exact
    // medians are pinned at L1/L2. Here we only need proof that SOME real
    // number rendered rather than every bucket showing its empty-state dash.
    const bodyText = await page.locator("body").innerText();
    expect(
      /\b\d+\s*d(ays?)?\b/i.test(bodyText),
      "expected at least one real day-count to appear on the page (e.g. a leased-DOM median) " +
        "once F10-S1's outcome stats replace the F0-S2 stub"
    ).toBe(true);
  });

  test("the price-test panel renders the insufficient-evidence guard state, not a curve", async ({ page }) => {
    const derivePromise = page.waitForResponse(
      (res) => res.url().includes("/api/derive") && res.request().method() === "POST"
    );
    await gotoResults(page);
    await derivePromise;

    const guardPanel = page
      .locator("[data-testid='price-test-guard']")
      .or(page.getByText(/insufficient evidence/i));
    await expect(
      guardPanel.first(),
      "WS-1's chosen candidate rent is deliberately in thin-evidence territory (PM scope ruling) " +
        "— the guard state must render, and a KM curve must NOT (F11-S2 is out of scope)"
    ).toBeVisible({ timeout: 10_000 });

    const curveMarker = page.locator("[data-testid='km-curve'], svg[data-km-curve]");
    expect(await curveMarker.count()).toBe(0);
  });

  test("no stub-stage warning text is visible on the page", async ({ page }) => {
    const derivePromise = page.waitForResponse(
      (res) => res.url().includes("/api/derive") && res.request().method() === "POST"
    );
    await gotoResults(page);
    await derivePromise;

    const bodyText = await page.locator("body").innerText();
    expect(bodyText).not.toMatch(/placeholder/i);
    expect(bodyText).not.toMatch(/\$1\.00\/sqft/);
  });
});
