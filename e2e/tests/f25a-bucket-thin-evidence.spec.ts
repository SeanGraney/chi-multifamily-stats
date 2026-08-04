/**
 * Row 25a — the bucket panel renders per-statistic evidence with no gate.
 *
 * QA-authored, written RED before any implementation exists (AGENT_QA.md
 * protocol). No code implements this story yet, so every test below except
 * the harness-identity check is expected to fail.
 *
 * ===========================================================================
 * WHAT THIS FILE PROVES (and what it deliberately leaves to L2)
 * ===========================================================================
 * `backend/tests/api/test_25a_bucket_thin_evidence_gate.py` exhaustively pins
 * the NUMBER (`leased_count`, `thin`, and the per-cell-not-total-count
 * semantics of PM Ruling 2). Per the split rule (AGENT_QA.md/WORKFLOW.md),
 * this file is representative, not exhaustive: it proves the WIRING —
 * `<BucketTable>` (`Results.tsx`) renders a visible, distinguishing signal on
 * a bucket the server marked `thin`, and does NOT render that signal on a
 * bucket the server did not mark thin — reading the server's own `thin`
 * field, never recomputing thinness client-side (D5/D13).
 *
 * It also pins DISCLOSURE, NOT SUPPRESSION: a thin bucket's DOM figure must
 * still be on the page, next to the warning — not replaced by it. See the
 * module docstring in the L2 file for the NORTH_STAR/row-26 reasoning this
 * decision rests on. If the PM/owner instead wants a bucket's DOM stats
 * hidden entirely under `thin`, that reading changes this assertion and is
 * a call for them, not for QA to make silently.
 *
 * ===========================================================================
 * NO FIXED MARKUP ASSUMED
 * ===========================================================================
 * `bucketRowFor()`/`thinSignalIn()` try a `[data-testid]` convention QA is
 * PROPOSING here (`bucket-thin-warning`, modeled on the existing
 * `price-test-guard`/`price-test-curve` convention in the same file) and
 * fall back to a text pattern, the same technique every other spec in this
 * suite uses for an AC the developer hasn't implemented yet.
 *
 * ===========================================================================
 * THE THIN CONSTRUCTION — SAME KEYS AS THE L2 FILE, ON PURPOSE
 * ===========================================================================
 * Zero live server can seed a curated state directly (no second door into
 * Results yet — F1-S1's recents door is unbuilt, F4-S6), so this spec drives
 * the same construction through the UI: NONE, then re-include (weight 1) the
 * same six comp keys the L2 file uses on `ws1-real`, producing an `above`
 * bucket with 1 leased comp among 4 total members. See the L2 file's
 * docstring for the probe that established these keys.
 *
 * ===========================================================================
 * PORT: EPHEMERAL, NOT 8000 (WORKFLOW.md §2, QUEUE.md row 44a)
 * ===========================================================================
 * Copied from `f5-s2-selection-weight.spec.ts` / `ws1-results-view.spec.ts`:
 * bind a free high port, spawn `rentcomp.app:app` directly under uvicorn,
 * verify our own child is still alive and its banner names our port, before
 * the first test and again after the last. No `mode: "serial"` — the suite
 * already runs `workers: 1, fullyParallel: false`, and serial mode only
 * costs "did not run" information on a failure (measured, WORKFLOW.md §2).
 *
 * ZERO LIVE RENTCAST CALLS (D17, WORKFLOW.md §6): `RENTCOMP_LIVE` is never
 * set, `RENTCAST_API_KEY` is deleted from the child env, and the only pull
 * this spec derives (`ws1-real`) resolves against the committed
 * `fixtures/live-samples/` with zero network.
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

const VENV_DIR = process.env.RENTCOMP_VENV_DIR ?? path.join(REPO_ROOT, ".venv");
const VENV_BIN = path.join(VENV_DIR, process.platform === "win32" ? "Scripts" : "bin");
const VENV_PYTHON = path.join(VENV_BIN, process.platform === "win32" ? "python.exe" : "python");
const BACKEND_SRC = path.join(REPO_ROOT, "backend", "src");

let serverProcess: ChildProcess | null = null;
let rentcompHome: string | null = null;
let setupFailure: string | null = null;
let BASE_URL = "";
let serverPort = 0;
let serverLog = "";

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
    if (serverProcess && serverProcess.exitCode !== null) return false;
    try {
      const res = await fetch(url);
      if (res.status < 500) return true;
    } catch {
      /* not up yet */
    }
    await new Promise((r) => setTimeout(r, 250));
  }
  return false;
}

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
  if (!existsSync(VENV_PYTHON)) {
    setupFailure =
      `${VENV_PYTHON} not found — expected the repo-root .venv, or RENTCOMP_VENV_DIR ` +
      "pointing at it when running from a worktree (WORKFLOW.md §2)";
    return;
  }
  if (!existsSync(FRONTEND_DIR)) {
    setupFailure = "frontend/ does not exist";
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
  rentcompHome = mkdtempSync(path.join(tmpdir(), "rentcomp-25a-home-"));

  const env = { ...process.env } as NodeJS.ProcessEnv;
  delete env.RENTCAST_API_KEY;
  env.RENTCOMP_LIVE = "";
  env.RENTCOMP_HOME = rentcompHome;
  env.RENTCOMP_UI_DIR = FRONTEND_DIST;
  env.PYTHONPATH = [BACKEND_SRC, process.env.PYTHONPATH].filter(Boolean).join(path.delimiter);

  serverProcess = spawn(
    VENV_PYTHON,
    ["-m", "uvicorn", "rentcomp.app:app", "--host", "127.0.0.1", "--port", String(serverPort)],
    { cwd: REPO_ROOT, env, stdio: "pipe" },
  );
  serverProcess.stdout?.on("data", (chunk) => (serverLog += chunk.toString()));
  serverProcess.stderr?.on("data", (chunk) => (serverLog += chunk.toString()));

  if (!(await waitForServer(`${BASE_URL}/openapi.json`, 30_000))) {
    setupFailure = `server did not come up on ${BASE_URL} within 30s. Output:\n${serverLog.slice(-800)}`;
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
  serverProcess?.kill();
  serverProcess = null;
  if (rentcompHome) {
    rmSync(rentcompHome, { recursive: true, force: true });
    rentcompHome = null;
  }
});

function skipIfSetupFailed() {
  test.skip(!!setupFailure, setupFailure ?? "");
}

// ---------------------------------------------------------------------------
// Locators
// ---------------------------------------------------------------------------

const isDerive = (url: string) => url.includes("/api/derive");

function rowFor(page: Page, key: string) {
  return page
    .locator(`[data-testid='comp-row'][data-comp-key="${key}"]`)
    .or(
      page.locator(
        `[data-comp-key="${key}"]:not([data-testid='map-pin']):not([data-testid='map-unplaceable'])`,
      ),
    )
    .first();
}

function weightIn(row: ReturnType<typeof rowFor>) {
  return row
    .locator("[data-testid='comp-weight']")
    .or(row.getByRole("spinbutton"))
    .or(row.locator("input[type='number']"))
    .first();
}

function bulkButton(page: Page, which: "all" | "none") {
  return page
    .locator(`[data-testid='select-${which}']`)
    .or(page.getByRole("button", { name: new RegExp(`^\\s*${which}\\s*$`, "i") }))
    .first();
}

/** The bucket table's own ROW for a bucket id — scoped, never bare `body`
 * text (`ws1-results-view.spec.ts`'s established technique on this exact
 * table). Every `<BucketTable>` row starts with its bucket id as the first
 * cell (`Results.tsx`, `<td>{bucket.id}</td>`). */
function bucketRowFor(page: Page, bucketId: string) {
  const table = page.locator("table", { hasText: "Leased DOM" }).first();
  return table.locator("tr", { hasText: bucketId }).first();
}

/** QA-PROPOSED contract, needs PM/dev confirmation (see module docstring):
 * a `[data-testid='bucket-thin-warning']` inside the thin bucket's own row,
 * falling back to a text pattern for a developer who names it differently. */
function thinSignalIn(bucketRow: ReturnType<typeof bucketRowFor>) {
  return bucketRow
    .locator("[data-testid='bucket-thin-warning']")
    .or(bucketRow.getByText(/thin|caveat|based on\s+\d+\s+leased/i))
    .first();
}

const SIX_KEEP_KEYS = [
  "1317 w 31st pl|1fl",
  "1922 w 47th st|2",
  "2241 w 21st st|",
  "2241 w 21st st|2",
  "2345 w 21st st|2",
  "2624 w 24th st|1f|2026-06-12",
];

async function gotoResults(page: Page) {
  await arriveWithAPullToOpen(page, BASE_URL);
  await submitSearch(page);
}

test.describe("25a — bucket panel per-cell evidence gate", () => {
  test.beforeEach(skipIfSetupFailed);

  test("a well-evidenced bucket (real ws1-real pull, no curation) shows no thin-evidence signal", async ({
    page,
  }) => {
    const derivePromise = page.waitForResponse((res) => isDerive(res.url()) && res.request().method() === "POST");
    await gotoResults(page);
    await derivePromise;

    // On the unmodified real pull every bucket clears dozens of leased comps
    // (L2's construction confirms below/at/above all >= 69) — none of the
    // three rows should carry a thin-evidence signal.
    for (const bucketId of ["below", "at", "above"]) {
      const row = bucketRowFor(page, bucketId);
      await expect(row, `no "${bucketId}" row found in the bucket table`).toBeVisible();
      await expect(
        thinSignalIn(row),
        `bucket "${bucketId}" is well-evidenced on the unmodified real pull and must not ` +
          "show a thin-evidence warning",
      ).toHaveCount(0);
    }
  });

  test("a bucket reduced to one leased comp shows a visible thin-evidence signal, and keeps its DOM figure", async ({
    page,
  }) => {
    const firstDerive = page.waitForResponse((res) => isDerive(res.url()) && res.request().method() === "POST");
    await gotoResults(page);
    await firstDerive;

    await bulkButton(page, "none").click();

    // Re-include exactly the six comps the L2 construction uses, leaving the
    // `above` bucket with 4 total members and exactly 1 leased.
    for (const key of SIX_KEEP_KEYS) {
      const row = rowFor(page, key);
      await expect(row, `comp row ${key} not found — the fixture may have changed shape`).toBeVisible();
      const nextDerive = page.waitForResponse((res) => isDerive(res.url()) && res.request().method() === "POST");
      await weightIn(row).fill("1");
      await weightIn(row).blur();
      await nextDerive;
    }

    const aboveRow = bucketRowFor(page, "above");
    await expect(aboveRow, "no \"above\" row found in the bucket table").toBeVisible();

    await expect(
      thinSignalIn(aboveRow),
      "the \"above\" bucket now has exactly ONE leased comp behind its DOM median (4 total " +
        "members, 6 comps included overall — both clear F5-S3's own >=5-included shape) and " +
        "must show a visible thin-evidence signal; nothing on today's page discloses this " +
        "(row 25a's named defect)",
    ).toBeVisible();

    // Disclosure, not suppression (module docstring): the numeric DOM figure
    // must still be on the page next to the warning, never replaced by it.
    const rowText = await aboveRow.innerText();
    expect(
      /\d+\s*d(ays?)?\b/i.test(rowText),
      `the "above" row must still show its real DOM figure alongside the warning — got: ${rowText}`,
    ).toBe(true);
  });
});
