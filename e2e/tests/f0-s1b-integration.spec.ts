/**
 * F0-S1b — Layer 3 browser-genuine checks (QA, written pre-implementation).
 *
 * Everything in this file needs a *real* build and a *real* running server —
 * per the dispatch note, "the build directory exists" is not sufficient
 * proof; this makes an actual HTTP request through the mounted app and
 * asserts on it. Covers:
 *
 *   - `npm run build` really runs and produces frontend/dist (not asserted
 *     via a synthetic tmp dir the way backend/tests/api/test_scaffold_static_ui.py
 *     does for F0-S1a — that test already proves the *mounting logic* works
 *     against a fake dist; this proves the *real* Vite build's output is
 *     actually compatible with it, e.g. matches rentcomp.app._ui_dist_dir()'s
 *     default resolution of <repo>/frontend/dist).
 *   - the backend serves the built index.html at `/`
 *   - `/openapi.json` still resolves correctly alongside the static mount
 *     for a real build — this is exactly the "router registered after a
 *     LAST-mounted static handler 404s" bug class that bit F0-S2's QA
 *     (QUEUE.md row 24 / 2026-07-27 "QA self-correction" log entry).
 *   - three routable views exist (Home / Results / Analysis)
 *   - a persistent top bar renders across all three and survives navigation
 *   - the dark surface background + monospace type are visibly applied
 *
 * One real build + one real server for the whole file (not one per test) —
 * these are integration-cost tests; sharing setup keeps the regression
 * suite's runtime sane once this file joins the permanent suite
 * (AGENT_QA.md protocol step 7).
 *
 * Zero live RentCast calls (WORKFLOW.md §6): RENTCOMP_HOME points at a
 * fresh temp dir for the spawned server, RENTCOMP_LIVE is never set.
 *
 * ===========================================================================
 * PORT: EPHEMERAL, NOT 8000 (QUEUE.md row 44a)
 * ===========================================================================
 * This file used to spawn the `rentcomp` console script on the hardcoded
 * `127.0.0.1:8000` with no identity check at all — one of the two confirmed
 * incidents row 44a exists for (F7-S1's QA verify: `serverProcess.kill()` is
 * async, so back-to-back specs on 8000 can bind-fail against a still-dying
 * previous server and get a false answer). Moved to the WORKFLOW.md §2
 * ratified pattern — bind a free high port and spawn `rentcomp.app:app`
 * directly under uvicorn — plus an identity check that is neither a status
 * code nor a guessable version string: our own child process must still be
 * alive AND its startup banner must name our port, checked before the first
 * test and again after the last.
 *
 * This does not weaken D7's ("one command launches `rentcomp.app:app` on
 * localhost:8000") coverage: `backend/tests/unit/test_scaffold_launch.py`
 * already pins that `rentcomp`/`python -m rentcomp` calls
 * `uvicorn.run("rentcomp.app:app", host="127.0.0.1", port=8000)` and that the
 * `[project.scripts]` entry point is exactly that function — without ever
 * binding a real socket (D21). What THIS file is actually about — a real
 * `npm run build` output served correctly by the real ASGI app, real routing,
 * real theme — needs the same app object on any port, not literally the
 * console-script process. Same ASGI object either way, so nothing under test
 * changes.
 */
import { test, expect, type Page } from "@playwright/test";
import { existsSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import net from "node:net";
import path from "node:path";
import { spawn, execFileSync, type ChildProcess } from "node:child_process";
import { REPO_ROOT, NPM, NPM_NEEDS_SHELL } from "./support/local-server";

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
 * NO `mode: "serial"` — removed under QUEUE row 44a (PM-authorised).
 * `playwright.config.ts` already pins `workers: 1, fullyParallel: false`, so
 * serial mode added nothing except "stop after the first failure"; nothing
 * below depends on another test's state.
 */

let serverProcess: ChildProcess | null = null;
let rentcompHome: string | null = null;
let buildFailure: string | null = null;
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
    buildFailure = "frontend/ does not exist yet (F0-S1b not implemented)";
    return;
  }
  if (!existsSync(VENV_PYTHON)) {
    buildFailure =
      `${VENV_PYTHON} not found — expected the repo-root .venv, or RENTCOMP_VENV_DIR ` +
      "pointing at it when running from a worktree (WORKFLOW.md §2)";
    return;
  }

  try {
    execFileSync(NPM, ["install"], { cwd: FRONTEND_DIR, stdio: "pipe", shell: NPM_NEEDS_SHELL });
    execFileSync(NPM, ["run", "build"], { cwd: FRONTEND_DIR, stdio: "pipe", shell: NPM_NEEDS_SHELL });
  } catch (err: any) {
    buildFailure = `npm install/build failed: ${err?.stderr?.toString?.() ?? err}`;
    return;
  }

  if (!existsSync(FRONTEND_DIST)) {
    buildFailure =
      "npm run build completed but frontend/dist does not exist — " +
      "rentcomp.app._ui_dist_dir() resolves to exactly this path (D7), so a " +
      "different outDir will silently break static serving in production";
    return;
  }

  serverPort = await freePort();
  BASE_URL = `http://127.0.0.1:${serverPort}`;
  rentcompHome = mkdtempSync(path.join(tmpdir(), "rentcomp-f0s1b-home-"));

  const env = { ...process.env } as NodeJS.ProcessEnv;
  delete env.RENTCAST_API_KEY; // D17: a live call must be impossible, not unlikely
  env.RENTCOMP_LIVE = "";
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
    buildFailure = `server did not come up on ${BASE_URL} within 20s. Output:\n${serverLog.slice(-800)}`;
  }
});

test("the harness stood up a server of our own, on a port nobody else holds", () => {
  expect(buildFailure, buildFailure ?? "").toBeNull();
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

function skipIfBuildFailed() {
  test.skip(!!buildFailure, buildFailure ?? "");
}

test.describe("F0-S1b — build output consumed by backend static serving", () => {
  test.beforeEach(skipIfBuildFailed);

  test("GET / serves the real built index.html", async ({ page }) => {
    const response = await page.goto(BASE_URL + "/");
    expect(response?.status()).toBe(200);
    const html = await page.content();
    expect(html.toLowerCase()).toContain("<div id=\"root\"");
  });

  test("GET /openapi.json still resolves alongside the static mount", async () => {
    const response = await fetch(`${BASE_URL}/openapi.json`);
    expect(
      response.status,
      "a real frontend build must not swallow API routes registered before the " +
        "static mount — this is the exact bug class that bit F0-S2's QA " +
        "(static mount at '/' registered LAST; anything after it 404s)"
    ).toBe(200);
    const body = await response.json();
    expect(String(body.openapi ?? "")).toMatch(/^3\./);
  });
});

test.describe("F0-S1b — three-view routing + persistent top bar", () => {
  test.beforeEach(skipIfBuildFailed);

  async function topBarLocator(page: Page) {
    // No fixed implementation assumed (nav landmark, header, or a
    // data-testid) — try the semantic option first, fall back to a testid
    // convention so this doesn't pin a [DEFAULT] the developer is free to
    // pick differently.
    const header = page.locator("header, nav, [data-testid='top-bar']").first();
    return header;
  }

  test("Home view loads at the app root", async ({ page }) => {
    await page.goto(BASE_URL + "/");
    await expect(page.locator("body")).toBeVisible();
    const topBar = await topBarLocator(page);
    await expect(
      topBar,
      "expected a persistent top bar (header/nav/[data-testid='top-bar']) on Home"
    ).toBeVisible();
  });

  test("top bar persists across navigation to Results and Analysis", async ({ page }) => {
    await page.goto(BASE_URL + "/");
    const topBar = await topBarLocator(page);
    await expect(topBar).toBeVisible();
    const homeBodyText = await page.locator("body").innerText();

    // Navigate via in-app controls, not by presuming URL routes exist
    // (react-router paths vs. state-driven tabs is a [DEFAULT] choice this
    // AC doesn't lock — "three-view routing" only requires the views and
    // nav to exist and work, not a particular routing library).
    const resultsLink = page.getByRole("link", { name: /results/i })
      .or(page.getByRole("button", { name: /results/i }))
      .or(page.locator("[data-testid='nav-results']"));
    await expect(
      resultsLink,
      "expected some clickable control (link/button/[data-testid='nav-results']) to reach Results"
    ).toBeVisible();
    await resultsLink.first().click();

    await expect(topBar, "top bar must still be present after navigating to Results").toBeVisible();
    const resultsBodyText = await page.locator("body").innerText();
    expect(
      resultsBodyText,
      "navigating to Results must change the rendered view, not just leave Home in place"
    ).not.toBe(homeBodyText);

    const analysisLink = page.getByRole("link", { name: /analysis/i })
      .or(page.getByRole("button", { name: /analysis/i }))
      .or(page.locator("[data-testid='nav-analysis']"));
    await expect(
      analysisLink,
      "expected some clickable control (link/button/[data-testid='nav-analysis']) to reach Analysis"
    ).toBeVisible();
    await analysisLink.first().click();

    await expect(topBar, "top bar must still be present after navigating to Analysis").toBeVisible();
    const analysisBodyText = await page.locator("body").innerText();
    expect(analysisBodyText).not.toBe(resultsBodyText);
  });
});

test.describe("F0-S1b — dark monospace theme visibly applied", () => {
  test.beforeEach(skipIfBuildFailed);

  test("root surface is the dark background token and body text is monospace", async ({ page }) => {
    await page.goto(BASE_URL + "/");

    const bg = await page.evaluate(() => {
      const el = document.body;
      return getComputedStyle(el).backgroundColor;
    });
    // #0b0c0b => rgb(11, 12, 11)
    expect(
      bg,
      `expected the dark surface token #0b0c0b (rgb(11, 12, 11)) applied to <body> or an ` +
        `ancestor whose background shows through; got ${bg}`
    ).toBe("rgb(11, 12, 11)");

    const fontFamily = await page.evaluate(() => getComputedStyle(document.body).fontFamily);
    expect(
      fontFamily.toLowerCase(),
      `expected a monospace font family on <body>; got '${fontFamily}'`
    ).toMatch(/mono/);
  });
});

test("the server we tested against is still the one we started (checked again after the last test)", () => {
  expect(
    serverIdentityProblem(),
    "the server identity changed partway through this run — everything above may have been " +
      "answered by a different agent's build",
  ).toBeNull();
});
