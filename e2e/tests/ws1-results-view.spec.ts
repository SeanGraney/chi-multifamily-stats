/**
 * WS-1 — the walking skeleton's one browser-genuine assertion: a real
 * `POST /api/derive` round-trips and Results renders REAL numbers
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
 *
 * ===========================================================================
 * RETARGETED FOR F4-S6 — PM RULING, AND WHAT DID *NOT* CHANGE
 * ===========================================================================
 * This file used to be titled "Results fires the hardcoded derive and renders
 * real data". It made two claims, and F4-S6 only invalidated the first:
 *
 *   "fires the hardcoded derive"  — OBSOLETE BY DESIGN. `Results.tsx` held
 *     `const PULL_REF = "ws1-real"` and derived it unconditionally on mount,
 *     so the owner could spend real API calls on a real address, open Results,
 *     and be shown the analysis of a fixture. F4-S6 deleted that constant on
 *     purpose; Results now derives nothing until a pull is open.
 *   "renders real data"           — STILL WS-1's COVERAGE, and still valuable.
 *
 * So the door changed and the assertions did not. Every `expect` in this file
 * survives untouched, because — checked one by one — not one of them actually
 * asserts that the derive is *hardcoded*: that claim lived entirely in the
 * describe title, this docstring, and one test's name and failure message.
 * The round-trip assertion (`status === 200`) is agnostic about which door
 * opened the pull, so it is retargeted rather than dropped. **No coverage was
 * quietly deleted here; there was nothing to delete.**
 *
 * ===========================================================================
 * PORT: EPHEMERAL, NOT 8000 (QUEUE.md row 44a)
 * ===========================================================================
 * This file used to spawn the `rentcomp` console script on the hardcoded
 * `127.0.0.1:8000` with no identity check — the file whose flaky red at
 * F7-S1's QA verify first established the port-8000-contention-within-our-own-
 * suite mechanism (`serverProcess.kill()` is async, so the next spec's bind
 * can fail while `waitForServer` succeeds against the previous, dying server).
 * Moved to the WORKFLOW.md §2 ratified pattern — bind a free high port, spawn
 * `rentcomp.app:app` directly under uvicorn, and check both that our own
 * child process is still alive and that its startup banner names our port,
 * before the first test and again after the last.
 */
import { test, expect, type Page } from "@playwright/test";
import { existsSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import net from "node:net";
import path from "node:path";
import { spawn, execFileSync, type ChildProcess } from "node:child_process";
import { REPO_ROOT, NPM, NPM_NEEDS_SHELL } from "./support/local-server";
import { arriveWithAPullToOpen, submitSearch } from "./support/open-pull";

const FRONTEND_DIR = path.join(REPO_ROOT, "frontend");
const FRONTEND_DIST = path.join(FRONTEND_DIR, "dist");

/** See WORKFLOW.md §2's third door — a worktree has no `.venv` of its own. */
const VENV_DIR = process.env.RENTCOMP_VENV_DIR ?? path.join(REPO_ROOT, ".venv");
const VENV_BIN = path.join(VENV_DIR, process.platform === "win32" ? "Scripts" : "bin");
const VENV_PYTHON = path.join(VENV_BIN, process.platform === "win32" ? "python.exe" : "python");

/** `pip install -e .` resolves against the MAIN checkout — without this a
 * worktree run silently tests the wrong branch (WORKFLOW.md §2). */
const BACKEND_SRC = path.join(REPO_ROOT, "backend", "src");

/**
 * NO `test.describe.configure({ mode: "serial" })` — removed under QUEUE row
 * 44a (PM-authorised).
 *
 * It bailed every test after the first failure, so the five tests below
 * reported as `1 failed, 4 did not run` — and "did not run" is the same family
 * as the silent skip WORKFLOW.md §2 records repeated recurrences of: it hides
 * how much of the suite is actually red. Measured on this very file during the
 * F4-S6 seam fix: `1 failed, 4 did not run` with it, versus all five reporting
 * their own result without it.
 *
 * It bought nothing to begin with: `playwright.config.ts` already pins
 * `workers: 1, fullyParallel: false`, and every test below arranges its own
 * navigation and its own derive — none depends on another's state.
 */

let serverProcess: ChildProcess | null = null;
let rentcompHome: string | null = null;
let setupFailure: string | null = null;
let BASE_URL = "";
let serverPort = 0;
let serverLog = "";

/** A port nothing is listening on right now, chosen by the OS. */
async function freePort(): Promise<number> {
  return await new Promise<number>((resolve, reject) => {
    const probe = net.createServer();
    probe.once("error", reject);
    probe.listen(0, "127.0.0.1", () => {
      const address = probe.address();
      if (typeof address === "string" || address === null) {
        probe.close(() => reject(new Error("could not resolve an ephemeral port")));
        return;
      }
      const { port } = address;
      probe.close(() => resolve(port));
    });
  });
}

async function waitForServer(url: string, timeoutMs: number): Promise<boolean> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (serverProcess && serverProcess.exitCode !== null) return false; // died on bind
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

/** "The server answering us is the one we started" (WORKFLOW.md §2). */
function serverIdentityProblem(): string | null {
  if (!serverProcess) return "no server process was ever spawned";
  if (serverProcess.exitCode !== null) {
    return (
      `the server we spawned exited with code ${serverProcess.exitCode}, yet ${BASE_URL} ` +
      "answered — every result in this run is about a DIFFERENT agent's build " +
      `(WORKFLOW.md §2, port contention). Server output:\n${serverLog.slice(-800)}`
    );
  }
  if (!serverLog.includes(`:${serverPort}`)) {
    return (
      `the server we spawned never announced port ${serverPort}. Output so far:\n` +
      serverLog.slice(-800)
    );
  }
  return null;
}

test.beforeAll(async () => {
  if (!existsSync(FRONTEND_DIR)) {
    setupFailure = "frontend/ does not exist";
    return;
  }
  if (!existsSync(VENV_PYTHON)) {
    setupFailure =
      `${VENV_PYTHON} not found — expected the repo-root .venv, or RENTCOMP_VENV_DIR ` +
      "pointing at it when running from a worktree (WORKFLOW.md §2)";
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

  serverPort = await freePort();
  BASE_URL = `http://127.0.0.1:${serverPort}`;
  rentcompHome = mkdtempSync(path.join(tmpdir(), "rentcomp-ws1-home-"));

  const env = { ...process.env } as NodeJS.ProcessEnv;
  delete env.RENTCAST_API_KEY; // D17: a live call must be impossible, not unlikely
  env.RENTCOMP_LIVE = ""; // never live (D17)
  env.RENTCOMP_HOME = rentcompHome;
  env.RENTCOMP_UI_DIR = FRONTEND_DIST; // serve the build this spec just made
  env.PYTHONPATH = [BACKEND_SRC, process.env.PYTHONPATH]
    .filter(Boolean)
    .join(path.delimiter); // ';' on Windows — WORKFLOW.md §2

  serverProcess = spawn(
    VENV_PYTHON,
    ["-m", "uvicorn", "rentcomp.app:app", "--host", "127.0.0.1", "--port", String(serverPort)],
    { cwd: REPO_ROOT, env, stdio: "pipe" },
  );
  serverProcess.stdout?.on("data", (chunk) => (serverLog += chunk.toString()));
  serverProcess.stderr?.on("data", (chunk) => (serverLog += chunk.toString()));

  const up = await waitForServer(`${BASE_URL}/openapi.json`, 20_000);
  if (!up) {
    setupFailure = `server did not come up on ${BASE_URL} within 20s. Output:\n${serverLog.slice(-800)}`;
  }
});

test("the harness stood up a server of our own, on a port nobody else holds", () => {
  expect(setupFailure, setupFailure ?? "").toBeNull();
  expect(
    serverIdentityProblem(),
    "the server this spec is about to test is not the one it started",
  ).toBeNull();
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

/**
 * Open a pull and land on Results — the user's own route in.
 *
 * Was: navigate to Results and let its hardcoded `PULL_REF` derive on mount.
 * F4-S6 removed that constant, so a pull has to be opened first, through the
 * search form (F4 flow step 4, "the system lands the user on Results"). The
 * derive itself is still the LIVE server's, which is the whole point of this
 * file — "renders real data" cannot be asserted against a stubbed payload.
 * See `support/open-pull.ts`.
 */
async function gotoResults(page: Page) {
  await arriveWithAPullToOpen(page, BASE_URL);
  await submitSearch(page);
}

test.describe("WS-1 — a real derive round-trips and Results renders real data", () => {
  test.beforeEach(skipIfSetupFailed);

  test("a real POST /api/derive round-trips and succeeds", async ({ page }) => {
    const derivePromise = page.waitForResponse(
      (res) => res.url().includes("/api/derive") && res.request().method() === "POST",
      { timeout: 15_000 }
    );
    await gotoResults(page);
    const response = await derivePromise;
    expect(
      response.status(),
      // Was "the hardcoded search must round-trip successfully". The search is
      // no longer hardcoded — F4-S6 made the ref arrive from the pull the user
      // opened — but the round trip this asserts is unchanged and still real.
      "the derive must round-trip successfully — a non-200 means the wiring (pull_ref " +
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

  test("the bucket table also shows the DOM min–max range and the censored-floors list, not just the median (F10-S1 §150)", async ({ page }) => {
    // Grounded in the ACTUAL /api/derive response, not an assumed markup —
    // per the split rule (AGENT_QA.md), L1/L2 already pin the exact values
    // (test_ws1_bucket_outcome_stats.py, test_derive_contract.py); this test
    // only needs to prove the wiring: that a bucket carrying a real
    // leased_dom_min/max distinct from its median, and a bucket carrying a
    // real censored floor, both surface SOMEWHERE on the page. §150 names
    // five per-bucket items — count, leased-only DOM median, DOM min–max,
    // cut-before-lease rate, censored-floors list — and only three of them
    // (count, median, cut-rate) are wired into <BucketTable> today.
    const derivePromise = page.waitForResponse(
      (res) => res.url().includes("/api/derive") && res.request().method() === "POST"
    );
    await gotoResults(page);
    const response = await derivePromise;
    const derived = await response.json();

    const buckets: Array<{
      id: string;
      leased_dom_min: number | null;
      leased_dom_max: number | null;
      censored_floors: number[];
    }> = derived.buckets;

    const bucketWithRange = buckets.find(
      (b) => b.leased_dom_min !== null && b.leased_dom_max !== null && b.leased_dom_min !== b.leased_dom_max
    );
    const bucketWithCensoredFloor = buckets.find((b) => b.censored_floors.length > 0);

    expect(
      bucketWithRange,
      "expected at least one bucket in the real ws1-real derive to carry a distinct " +
        "leased_dom_min/leased_dom_max — if this fails, the fixture itself can't prove the point"
    ).toBeTruthy();
    expect(
      bucketWithCensoredFloor,
      "expected at least one bucket in the real ws1-real derive to carry a non-empty " +
        "censored_floors list — if this fails, the fixture itself can't prove the point"
    ).toBeTruthy();

    // Scoped to the actual bucket-table ROW, never bare `body` text — a loose
    // page-wide substring match would false-pass on an unrelated number (a
    // comp's rent, DOM, or distance) that happens to share digits with a
    // bucket's min/max/floor. Every row in <BucketTable> starts with its
    // bucket id as the first cell (Results.tsx `<td>{bucket.id}</td>`).
    const bucketsSection = page.locator("table", { hasText: "Leased DOM" }).first();
    await expect(bucketsSection, "no bucket table found on the page at all").toBeVisible({
      timeout: 10_000,
    });

    async function rowTextFor(bucketId: string): Promise<string> {
      const row = bucketsSection.locator("tr", { hasText: bucketId }).first();
      return (await row.innerText().catch(() => "")) || "";
    }

    if (bucketWithRange) {
      const minStr = String(bucketWithRange.leased_dom_min);
      const maxStr = String(bucketWithRange.leased_dom_max);
      const rowText = await rowTextFor(bucketWithRange.id);
      expect(
        rowText.includes(minStr) && rowText.includes(maxStr),
        `expected the "${bucketWithRange.id}" bucket's OWN row to show its DOM min (${minStr}) and ` +
          `max (${maxStr}) — §150 requires "leased-only DOM median + min–max", and today's ` +
          `<BucketTable> row reads "${rowText}", which has only the median`
      ).toBe(true);
    }
    if (bucketWithCensoredFloor) {
      const floorStrs = bucketWithCensoredFloor.censored_floors.map(String);
      const rowText = await rowTextFor(bucketWithCensoredFloor.id);
      expect(
        floorStrs.some((f) => rowText.includes(f)),
        `expected the "${bucketWithCensoredFloor.id}" bucket's OWN row to show at least one censored ` +
          `floor (${floorStrs.join(", ")}) — §150 requires a "censored-floors list" per bucket, and ` +
          `today's <BucketTable> row reads "${rowText}", which has no censored-floors list at all`
      ).toBe(true);
    }
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

test("the server we tested against is still the one we started (checked again after the last test)", () => {
  expect(
    serverIdentityProblem(),
    "the server identity changed partway through this run — everything above may have been " +
      "answered by a different agent's build",
  ).toBeNull();
});
