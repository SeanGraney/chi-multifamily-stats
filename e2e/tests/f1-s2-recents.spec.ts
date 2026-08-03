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
 * THREE OF THE ROWS BELOW BELONG TO F1-S1, NOT F1-S2 — read this before judging a run
 * -----------------------------------------------------------------------------------
 * `frontend/src/views/Home.tsx` has no recents table and no router link to
 * Results. F1-S1 ("[FE] Home view. NEW SEARCH primary button + recents table
 * (address, specs, radius, anchor, age), newest-first") is a separate story,
 * BLOCKED on this one in QUEUE.md. F1-S2's developer cannot make those green
 * and must not be asked to.
 *
 * UPDATED 2026-07-29 (F1-S2 dev): this paragraph originally said Home was still
 * the F0-S1b scaffold with "no NEW SEARCH button". That was true of the base
 * this file was written against and is no longer true of main — **F2-S1 shipped
 * the NEW SEARCH button**. The empty-state row's `test.fail()` has therefore
 * been removed and that row now passes for real; the other three markers stay.
 *
 * So they are written test-first and marked `test.fail()` — Playwright's strict
 * xfail: the run fails if one of them PASSES, which forces the marker off the
 * day F1-S1 lands rather than letting the coverage quietly rot into a comment.
 * The QA of F1-S1 inherits this file: remove the three remaining `test.fail()`
 * calls, and it is that story's acceptance spec.
 *
 * `the app serves Home at all` is deliberately UNMARKED. Without it a broken
 * harness (no build, no server, wrong port) would render every `test.fail()`
 * above it as an "expected failure" and the file would report success having
 * verified nothing — the false-green shape WORKFLOW.md §2 forbids.
 *
 * MONEY: zero live RentCast calls (D17, WORKFLOW.md §6). `RENTCOMP_LIVE` is
 * never set, `RENTCOMP_HOME` is a fresh temp dir, and the seeded pulls are
 * served from a temp fixtures directory by `support/seed-f1s2-home.py`.
 *
 * ===========================================================================
 * PORT: EPHEMERAL, NOT 8000 — THIS FILE IS THE ACTUAL 2026-08-03 INCIDENT (row 44a)
 * ===========================================================================
 * This file used to spawn the `rentcomp` console script on the hardcoded
 * `127.0.0.1:8000` with NO identity check at all. During F5-S1's QA verify,
 * the owner's real `rentcomp` app server was up on 8000 for the owner's own
 * use; this spec's spawn silently failed to bind, `waitForServer` polled 8000
 * anyway and got the OWNER'S REAL SERVER, and `saveWorkspace()` below wrote
 * two test-fixture files into the owner's real `~/.rentcomp/workspaces/`. No
 * real data was overwritten (verified read-only afterward), but it is exactly
 * the failure class row 44a exists to close, with a live victim. Moved to the
 * WORKFLOW.md §2 ratified pattern — bind a free high port, spawn
 * `rentcomp.app:app` directly under uvicorn, and check both that our own
 * child process is still alive and that its startup banner names our port,
 * before the first test and again after the last. Structurally impossible to
 * repeat once this file never touches 8000 at all.
 */
import { test, expect, type Page } from "@playwright/test";
import { existsSync, mkdtempSync, rmSync, readdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import net from "node:net";
import path from "node:path";
import { spawn, execFileSync, type ChildProcess } from "node:child_process";
import { REPO_ROOT, NPM, NPM_NEEDS_SHELL } from "./support/local-server";

const FRONTEND_DIR = path.join(REPO_ROOT, "frontend");
const FRONTEND_DIST = path.join(FRONTEND_DIR, "dist");

/**
 * The venv, which is NOT always under `REPO_ROOT`.
 *
 * `support/local-server.ts` resolves `<REPO_ROOT>/.venv` (fixed under row
 * 44a's Half C to also read `RENTCOMP_VENV_DIR` — see that file), and an
 * isolated worktree has no `.venv` of its own — CLAUDE.md is explicit that the
 * repo-root one is the project's ONLY Python environment. Resolved literally,
 * every test in this file sets a skip reason and the file exits 0 having
 * verified nothing: the same class of setup artifact WORKFLOW.md §2 documents
 * for `NODE_PATH` and `RENTCOMP_UI_DIR`, and the same false green.
 * `RENTCOMP_VENV_DIR` is the third door (found by F1-S2's QA, 2026-07-28;
 * reported to the PM for §2). This file already resolved it inline before
 * local-server.ts did, which is why local-server.ts's fix mirrors this.
 */
const VENV_DIR = process.env.RENTCOMP_VENV_DIR ?? path.join(REPO_ROOT, ".venv");
const VENV_BIN = path.join(VENV_DIR, process.platform === "win32" ? "Scripts" : "bin");
const VENV_PYTHON = path.join(
  VENV_BIN,
  process.platform === "win32" ? "python.exe" : "python",
);

/** `pip install -e .` resolves against the MAIN checkout — without this a
 * worktree run silently tests the wrong branch (WORKFLOW.md §2). */
const BACKEND_SRC = path.join(REPO_ROOT, "backend", "src");

/**
 * NO `mode: "serial"` — removed under QUEUE row 44a (PM-authorised).
 * `playwright.config.ts` already pins `workers: 1, fullyParallel: false`, so
 * serial mode added nothing except "stop after the first failure"; nothing
 * below depends on another test's state (each test either reads a fresh empty
 * store or saves its own workspace(s) as its own arrange step).
 */

let serverProcess: ChildProcess | null = null;
let rentcompHome: string | null = null;
let setupFailure: string | null = null;
let refs: string[] = [];
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
  if (!existsSync(VENV_PYTHON)) {
    setupFailure =
      `${VENV_PYTHON} not found — expected \`pip install -e backend/\` into the repo-root ` +
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

  serverPort = await freePort();
  BASE_URL = `http://127.0.0.1:${serverPort}`;
  rentcompHome = mkdtempSync(path.join(tmpdir(), "rentcomp-f1s2-home-"));
  const fixturesDir = path.join(rentcompHome, "e2e-fixtures");
  const env = {
    ...process.env,
    RENTCOMP_HOME: rentcompHome,
    RENTCOMP_FIXTURES_DIR: fixturesDir,
    RENTCOMP_LIVE: "", // never live (D17)
    // Serve the build THIS spec just made. `app.py::_ui_dist_dir()` resolves
    // repo-relative from the installed package, which under a worktree (or a
    // runner that exported RENTCOMP_UI_DIR for someone else's benefit) is a
    // different, possibly stale `frontend/dist` — and a stale bundle renders a
    // page these locators then race, which is how this spec first reported a
    // NEW SEARCH button that does not exist in any source file.
    RENTCOMP_UI_DIR: FRONTEND_DIST,
  } as NodeJS.ProcessEnv;
  delete env.RENTCAST_API_KEY; // D17: a live call must be impossible, not unlikely

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

  const serverEnv = {
    ...env,
    PYTHONPATH: [BACKEND_SRC, env.PYTHONPATH].filter(Boolean).join(path.delimiter), // ';' on Windows
  };
  serverProcess = spawn(
    VENV_PYTHON,
    ["-m", "uvicorn", "rentcomp.app:app", "--host", "127.0.0.1", "--port", String(serverPort)],
    { cwd: REPO_ROOT, env: serverEnv, stdio: "pipe" },
  );
  serverProcess.stdout?.on("data", (chunk) => (serverLog += chunk.toString()));
  serverProcess.stderr?.on("data", (chunk) => (serverLog += chunk.toString()));

  if (!(await waitForServer(`${BASE_URL}/openapi.json`, 20_000))) {
    setupFailure = `server did not come up on ${BASE_URL} within 20s. Output:\n${serverLog.slice(-800)}`;
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

/**
 * Load Home and wait for the app to actually mount.
 *
 * `page.goto` resolves on the document, not on React. Asserting straight after
 * it reads a half-built DOM, and an absence assertion ("no recents table") is
 * then trivially and wrongly true. The top bar is F0-S1b's pinned, always-
 * present element, so it is the honest "the app is up" signal.
 */
async function gotoHome(page: Page): Promise<void> {
  await page.goto(BASE_URL + "/");
  await expect(page.locator("[data-testid='top-bar']").or(page.getByRole("banner")).first())
    .toBeVisible({ timeout: 15_000 });
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
    // The identity check itself (row 44a): this is the file that actually
    // wrote to the owner's real ~/.rentcomp/workspaces/ on 2026-08-03 by
    // trusting whatever answered on 8000. Fail loudly rather than silently
    // exercising someone else's server.
    expect(
      serverIdentityProblem(),
      "the server this spec is about to test is not the one it started",
    ).toBeNull();

    const response = await page.goto(BASE_URL + "/");
    expect(response?.status(), "the built UI must be served from the running rentcomp").toBeLessThan(
      400,
    );
    expect(refs.length, "two pulls were seeded for this spec").toBe(2);
  });

  // =========================================================================
  // the empty-state row runs FIRST, deliberately
  // -------------------------------------------------------------------------
  // It is the only row whose precondition is "nothing has been saved yet", and
  // three of the rows below save a workspace as their arrange step. Ordering it
  // first is what makes that precondition a fact rather than a hope.
  //
  // Found by F1-S2's QA on verify (2026-07-29): it previously sat fourth behind
  // a `test.skip(listed.length > 0)` guard and passed only because Playwright
  // restarts the worker after a failing test, handing it a fresh RENTCOMP_HOME
  // by accident. The three `test.fail()` rows are what produce those failures —
  // so **the day F1-S1 lands and the markers come off, the worker stops
  // restarting and this row would have silently SKIPPED**, exiting 0 having
  // verified nothing: the exact false green this file's header forbids. Moved
  // up and the conditional skip replaced with an assertion, so the precondition
  // is now stated out loud and fails if it is ever untrue.
  // =========================================================================

  test("with no recents the table is hidden and NEW SEARCH is the only path", async ({ page }) => {
    skipIfSetupFailed();
    // `test.fail()` REMOVED by F1-S2's developer (disclosed to QA via the PM,
    // and verified on QA's verify pass). The marker was written against main at
    // 86a9286, where Home was still the F0-S1b scaffold; **F2-S1 shipped the
    // NEW SEARCH button** (`Home.tsx:49`) before F1-S2 was dispatched, so both
    // halves of this row are now genuinely satisfied — no recents table (still
    // F1-S1's) and a visible NEW SEARCH. The strict marker did exactly its job:
    // it went red the day the gap closed and demanded its own removal rather
    // than decaying into a comment. The other three markers in this file stay —
    // the recents TABLE is F1-S1's.
    const listed = await (await fetch(`${BASE_URL}/api/workspaces`)).json();
    expect(
      listed,
      "this row must run against an empty workspace store; it is ordered first for exactly " +
        "that reason, so a non-empty store means something above it started saving",
    ).toEqual([]);

    await gotoHome(page);
    await expect(recentsTable(page)).toHaveCount(0);
    await expect(
      page.getByRole("button", { name: /new search/i }).or(page.getByRole("link", { name: /new search/i })),
    ).toBeVisible();
  });

  // =========================================================================
  // F1-S1's three rows — expected to fail until the Home view exists
  // =========================================================================

  test("the recents table lists the saved searches, newest first", async ({ page }) => {
    skipIfSetupFailed();
    test.fail(); // F1-S1 owns the Home view; see the file header.
    expect(await saveWorkspace(refs[0], curation(refs[0]))).toBeLessThan(300);
    expect(await saveWorkspace(refs[1], curation(refs[1], { candidate_rent: 2450 }))).toBeLessThan(
      300,
    );

    await gotoHome(page);
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

    await gotoHome(page);
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

    await gotoHome(page);
    const row = recentRows(page).filter({ hasText: /error|could not|unreadable|broken/i });
    await expect(row.first()).toBeVisible({ timeout: 10_000 });
    await expect(
      page.getByRole("button", { name: /refresh/i }).or(page.getByText(/refresh/i)).first(),
    ).toBeVisible();
  });
});

test("the server we tested against is still the one we started (checked again after the last test)", () => {
  expect(
    serverIdentityProblem(),
    "the server identity changed partway through this run — everything above may have been " +
      "answered by a different agent's build",
  ).toBeNull();
});
