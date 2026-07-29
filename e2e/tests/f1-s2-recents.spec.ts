/**
 * F1 · Home / Recent Searches — the browser-genuine half.
 *
 * WHAT IS HERE AND WHY IT IS SO SHORT
 * -----------------------------------
 * F1-S2 is a [BE] story and almost all of it is Layer 2
 * (`backend/tests/api/f1s2/`): the round trip, the exact restoration, the
 * ordering of the data, every corruption shape, and the structural zero-call
 * pin. Only four things about F1 genuinely need a browser — a table has to
 * render, a row has to be clickable, a view has to mount, and an empty state
 * has to hide something. Per AGENT_QA.md's split rule (exhaustive below,
 * representative above), each appears exactly once here and NO restored value
 * is asserted from the browser; that is L2's job.
 *
 * THE FOUR ROWS BELOW BELONG TO F1-S1, NOT F1-S2 — read this before judging a run
 * ------------------------------------------------------------------------------
 * `frontend/src/views/Home.tsx` is still the F0-S1b scaffold: no NEW SEARCH
 * button, no recents table, no router link to Results. F1-S1 ("[FE] Home view.
 * NEW SEARCH primary button + recents table (address, specs, radius, anchor,
 * age), newest-first") is a separate story, BLOCKED on this one in QUEUE.md.
 * F1-S2's developer cannot make these green and must not be asked to.
 *
 * So they are written test-first and marked `test.fail()` — Playwright's strict
 * xfail: the run fails if one of them PASSES, which forces the marker off the
 * day F1-S1 lands rather than letting the coverage quietly rot into a comment.
 * The QA of F1-S1 inherits this file: remove the four `test.fail()` calls, and
 * it is that story's acceptance spec.
 *
 * `the app serves Home at all` is deliberately UNMARKED. Without it a broken
 * harness (no build, no server, wrong port) would render every `test.fail()`
 * above it as an "expected failure" and the file would report success having
 * verified nothing — the false-green shape WORKFLOW.md §2 forbids.
 *
 * MONEY: zero live RentCast calls (D17, WORKFLOW.md §6). `RENTCOMP_LIVE` is
 * never set, `RENTCOMP_HOME` is a fresh temp dir, and the seeded pulls are
 * served from a temp fixtures directory by `support/seed-f1s2-home.py`.
 */
import { test, expect, type Page } from "@playwright/test";
import { existsSync, mkdtempSync, rmSync, readdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { spawn, execFileSync, type ChildProcess } from "node:child_process";
import { REPO_ROOT, NPM, NPM_NEEDS_SHELL } from "./support/local-server";

const FRONTEND_DIR = path.join(REPO_ROOT, "frontend");
const FRONTEND_DIST = path.join(FRONTEND_DIR, "dist");
const BASE_URL = "http://127.0.0.1:8000"; // D7/__main__.py hardcodes 8000

/**
 * The venv, which is NOT always under `REPO_ROOT`.
 *
 * `support/local-server.ts` resolves `<REPO_ROOT>/.venv`, and an isolated
 * worktree has no `.venv` of its own — CLAUDE.md is explicit that the repo-root
 * one is the project's ONLY Python environment. Resolved literally, every test
 * in this file sets a skip reason and the file exits 0 having verified nothing:
 * the same class of setup artifact WORKFLOW.md §2 documents for `NODE_PATH` and
 * `RENTCOMP_UI_DIR`, and the same false green. `RENTCOMP_VENV_DIR` is the third
 * door (found by F1-S2's QA, 2026-07-28; reported to the PM for §2).
 */
const VENV_DIR = process.env.RENTCOMP_VENV_DIR ?? path.join(REPO_ROOT, ".venv");
const VENV_BIN = path.join(VENV_DIR, process.platform === "win32" ? "Scripts" : "bin");
const VENV_RENTCOMP = path.join(
  VENV_BIN,
  process.platform === "win32" ? "rentcomp.exe" : "rentcomp",
);
const VENV_PYTHON = path.join(
  VENV_BIN,
  process.platform === "win32" ? "python.exe" : "python",
);

test.describe.configure({ mode: "serial" });

let serverProcess: ChildProcess | null = null;
let rentcompHome: string | null = null;
let setupFailure: string | null = null;
let refs: string[] = [];

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

/** Save one curation state through the real API — no on-disk format assumed. */
async function saveWorkspace(ref: string, body: Record<string, unknown>): Promise<number> {
  const res = await fetch(`${BASE_URL}/api/workspaces/${ref}`, {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  return res.status;
}

function curation(ref: string, overrides: Record<string, unknown> = {}) {
  return {
    pull_ref: ref,
    subject: {
      address: "3651 S Wood St Unit 2",
      lat: 41.8286,
      lng: -87.6716,
      sqft: 1000.0,
      beds: 3.0,
      baths: 1.0,
    },
    weights: {},
    include_overrides: [],
    filters: { max_distance_mi: null, hide_censored: false, leased_only: false },
    drift_pct: 0.0,
    candidate_rent: null,
    ...overrides,
  };
}

test.beforeAll(async () => {
  if (!existsSync(VENV_RENTCOMP)) {
    setupFailure =
      `${VENV_RENTCOMP} not found — expected \`pip install -e backend/\` into the repo-root ` +
      "venv, or RENTCOMP_VENV_DIR pointing at it when running from a worktree";
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

  rentcompHome = mkdtempSync(path.join(tmpdir(), "rentcomp-f1s2-home-"));
  const fixturesDir = path.join(rentcompHome, "e2e-fixtures");
  const env = {
    ...process.env,
    RENTCOMP_HOME: rentcompHome,
    RENTCOMP_FIXTURES_DIR: fixturesDir,
    RENTCOMP_LIVE: "", // never live (D17)
  };

  try {
    const seeded = execFileSync(
      VENV_PYTHON,
      [path.join(__dirname, "support", "seed-f1s2-home.py")],
      { cwd: REPO_ROOT, env, stdio: "pipe" },
    ).toString();
    refs = JSON.parse(seeded.trim().split("\n").pop() as string).refs;
  } catch (err: any) {
    setupFailure = `seeding two pulls failed: ${err?.stderr?.toString?.() ?? err}`;
    return;
  }

  serverProcess = spawn(VENV_RENTCOMP, [], { cwd: REPO_ROOT, env, stdio: "pipe" });
  if (!(await waitForServer(`${BASE_URL}/openapi.json`, 20_000))) {
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

/** Locators try a testid, then a role, then text — no markup F1-S1 has not chosen yet is pinned. */
function recentsTable(page: Page) {
  return page.locator("[data-testid='recents-table']").or(page.getByRole("table"));
}

function recentRows(page: Page) {
  return page
    .locator("[data-testid='recent-row']")
    .or(recentsTable(page).getByRole("row"))
    .or(page.getByRole("listitem"));
}

// ===========================================================================
// the unmarked precondition — this is what makes a broken harness visible
// ===========================================================================

test.describe("F1 · Home", () => {
  test("the app serves Home at all (harness precondition, never expected to fail)", async ({
    page,
  }) => {
    // Deliberately NOT `skipIfSetupFailed`: a skip exits 0 and is
    // indistinguishable from a pass (WORKFLOW.md §2). If the build, the venv or
    // the server is broken, this file must go RED, not quiet.
    expect(setupFailure, "the F1-S2 E2E harness could not be stood up").toBeNull();

    const response = await page.goto(BASE_URL + "/");
    expect(response?.status(), "the built UI must be served from the running rentcomp").toBeLessThan(
      400,
    );
    expect(refs.length, "two pulls were seeded for this spec").toBe(2);
  });

  // =========================================================================
  // F1-S1's four rows — expected to fail until the Home view exists
  // =========================================================================

  test("the recents table lists the saved searches, newest first", async ({ page }) => {
    skipIfSetupFailed();
    test.fail(); // F1-S1 owns the Home view; see the file header.
    expect(await saveWorkspace(refs[0], curation(refs[0]))).toBeLessThan(300);
    expect(await saveWorkspace(refs[1], curation(refs[1], { candidate_rent: 2450 }))).toBeLessThan(
      300,
    );

    await page.goto(BASE_URL + "/");
    await expect(recentsTable(page).first()).toBeVisible({ timeout: 10_000 });
    // Order only — the values in the row are pinned at L2.
    const text = await recentsTable(page).first().innerText();
    expect(text.indexOf(refs[1].slice(0, 8))).toBeLessThan(text.indexOf(refs[0].slice(0, 8)));
  });

  test("clicking a recent row lands on Results with the workspace mounted", async ({ page }) => {
    skipIfSetupFailed();
    test.fail(); // F1-S1 owns the Home view; see the file header.
    expect(await saveWorkspace(refs[0], curation(refs[0], { candidate_rent: 2450 }))).toBeLessThan(
      300,
    );

    await page.goto(BASE_URL + "/");
    const derivePromise = page.waitForResponse(
      (res) => res.url().includes("/api/derive") && res.request().method() === "POST",
      { timeout: 15_000 },
    );
    await recentRows(page).first().click();

    // Browser-genuine only: a real round trip happened and the comps view
    // mounted. That the restored candidate rent is 2450 is asserted at L2.
    const derived = await derivePromise;
    expect(derived.status()).toBe(200);
    await expect(page.locator("[data-testid='comp-row']").or(page.getByRole("row")).first()).toBeVisible({
      timeout: 10_000,
    });
  });

  test("with no recents the table is hidden and NEW SEARCH is the only path", async ({ page }) => {
    skipIfSetupFailed();
    test.fail(); // F1-S1 owns the Home view; see the file header.
    // Nothing saved in this test's run order-independent state: assert on a
    // Home whose store is empty by asking the API first.
    const listed = await (await fetch(`${BASE_URL}/api/workspaces`)).json();
    test.skip(Array.isArray(listed) && listed.length > 0, "a previous test saved a workspace");

    await page.goto(BASE_URL + "/");
    await expect(recentsTable(page)).toHaveCount(0);
    await expect(
      page.getByRole("button", { name: /new search/i }).or(page.getByRole("link", { name: /new search/i })),
    ).toBeVisible();
  });

  test("a corrupt workspace renders as an error row offering refresh", async ({ page }) => {
    skipIfSetupFailed();
    test.fail(); // F1-S1 owns the Home view; see the file header.
    expect(await saveWorkspace(refs[0], curation(refs[0]))).toBeLessThan(300);

    // Corrupt it on disk, wherever the store filed it (the location itself is
    // pinned at L2: `workspaces/<cache-key>.json`).
    const dir = path.join(rentcompHome as string, "workspaces");
    const file = readdirSync(dir).find((name) => name.includes(refs[0]));
    expect(file, `no workspace file for ${refs[0]} under ${dir}`).toBeTruthy();
    writeFileSync(path.join(dir, file as string), "{ not json", "utf-8");

    await page.goto(BASE_URL + "/");
    const row = recentRows(page).filter({ hasText: /error|could not|unreadable|broken/i });
    await expect(row.first()).toBeVisible({ timeout: 10_000 });
    await expect(
      page.getByRole("button", { name: /refresh/i }).or(page.getByText(/refresh/i)).first(),
    ).toBeVisible();
  });
});
