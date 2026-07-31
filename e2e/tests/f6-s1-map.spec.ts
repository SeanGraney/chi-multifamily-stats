/**
 * F6-S1 — the Leaflet map, narrowed to ONE invariant.
 *
 * QA-authored, written RED before the developer's branch exists and before
 * `leaflet` is installed (AGENT_QA.md protocol step 3).
 *
 * ===========================================================================
 * THE SCOPE, AS THE OWNER NARROWED IT
 * ===========================================================================
 * F6-S2 (pin tooltip) and F6-S3 (hover sync) are CUT from the release
 * (QUEUE.md row 21). F6-S2 carried the `[INVARIANT]` this file is named for
 * and will not be built, so F6-S1 solely owns it:
 *
 *     THE MAP AND THE LIST NEVER DISAGREE ABOUT WHICH COMPS ARE INCLUDED.
 *
 * It is the one property only the VIEW can break. `pipeline/derive.py` drives
 * everything from a single `included` mask — `classify_membership` labels each
 * comp exactly once and `_breakdown` counts `len()` of the list beside it — so
 * no backend change can make the two panels disagree. Two renderers reading
 * one label can.
 *
 * IN SCOPE here, because it is the minimum that makes that invariant testable:
 * pins drawn from the server's coordinates, each pin's state following
 * `comp.state`, and the subject distinguishable from the comps.
 *
 * DELIBERATELY NOT TESTED (deferred; reported to the PM as follow-up rows):
 * tooltips and hover cards, list<->map hover sync, clustering/spiderfy, zoom
 * and pan behaviour, ANY animation — the pulsing ring for censored comps is
 * asserted nowhere, because asserting an animation frame is a flake generator
 * and the honest claim ("this comp is still active") is already a state on the
 * comp, not a property of the map — legend copy, tile provider, and colour
 * values (`data-pin-state="filtered"` is asserted; `#b7410e` is not).
 *
 * ===========================================================================
 * TEST PLAN — every in-scope AC exactly once
 * ===========================================================================
 * | #    | Acceptance criterion                                    | Layer | Test |
 * |------|---------------------------------------------------------|-------|------|
 * | P1   | `leaflet` + `react-leaflet` are installed (D10 §1a)     | L3    | "leaflet and react-leaflet are installed" |
 * | P2   | the server under test is the one this spec started      | L3    | "the harness stood up a server of our own..." (x2, before and after) |
 * | AC1  | Results mounts a map                                    | L3    | "the Results view mounts a map" |
 * | AC2  | every comp in the payload has exactly one pin           | L3    | "every comp in the payload has exactly one pin on the map" |
 * | AC3  | pins are placed FROM the server's coordinates           | L3    | "pins are placed by the server's coordinates, not by a swapped pair" |
 * | AC4  | each pin's state is the server's `comp.state`           | L3    | "every pin carries the state the server gave its comp" |
 * | AC5  | **[INVARIANT]** map and list agree on the included set  | L3    | "the map's included set is exactly the list's included set" |
 * | AC6  | **[INVARIANT]** neither view re-derives that set (D5)   | L3    | "pin state follows the server's label, not a predicate recomputed in the view" |
 * | AC7  | a curation change moves both, with no stale panel       | L3    | "excluding a comp in the list repaints its pin, and the two still agree" |
 * | AC8  | the subject is distinguishable from every comp          | L3    | "the subject is on the map and is not one of the comps" |
 * | AC9  | a comp with no honest coordinate is not silently placed | L3    | "a comp with no honest coordinate is never plotted at null island" |
 *
 * Every row is L3, and that is the decision procedure's answer, not laziness:
 * each one fails because of a React fact (which pins mount, what they carry,
 * whether two renderers agree) — AGENT_QA.md step 3, "Python -> L2, React ->
 * L3". Nothing here is a `DerivedState` field comparison.
 *
 * NOT DUPLICATED FROM L2, considered and rejected:
 *   - "a filtered comp stays in the payload with its coordinates" is already
 *     `test_a_filter_relabels_every_comp_and_removes_none` (F7-S1). It is the
 *     precondition for AC2 and re-asserting it here would be an L2 test in a
 *     browser costume.
 *   - "`included + excluded + filtered == pulled`" is F7-S1's, at L2.
 *   - "no comp reaches the wire with a reconstructed coordinate" is QUEUE.md
 *     row 9d's, at the DTO boundary. AC9 below is the VIEW's half of it: what
 *     the map must do when it is handed one anyway.
 *
 * ===========================================================================
 * TEST-ID CONTRACT — three of the four are ALREADY ON MAIN
 * ===========================================================================
 * `[data-testid="map-pin"][data-comp-key="<key>"]` carrying
 * `data-pin-state="included" | "excluded" | "filtered"` is NOT invented here.
 * It is the contract F7-S1's QA wrote into the strict xfail at
 * `f7-s1-filter-engine.spec.ts` ("a filtered comp keeps a pin on the map"),
 * which is on main. Adopted verbatim so the two specs meet at one seam rather
 * than at two names for the same thing.
 *
 *   [data-testid="map"]                          the map container
 *   [data-testid="map-pin"] + data-comp-key      one per placeable comp,
 *                            + data-pin-state    mirroring `comp.state`
 *   [data-testid="map-subject"]                  the subject marker
 *   [data-testid="map-unplaceable"]              AC9's disclosure; carries
 *                            + data-comp-key     the comp(s) that have no
 *                                                honest coordinate
 *
 * ⚠ Keyed on testids and on BEHAVIOUR, never on layout or DOM structure:
 * `Results.tsx` is the most contended file in the repo and its markup moves
 * under two other stories while this one is being written.
 *
 * ===========================================================================
 * WHY THIS FILE IS RED TODAY, AND WHY THAT IS NOT A SKIP
 * ===========================================================================
 * `leaflet` and `react-leaflet` are budgeted and pre-authorised (D10 §1a) and
 * are NOT INSTALLED. Installing them is the developer's first act, not QA's.
 * So NO assertion in this file can pass today, and the first test states that
 * as a FAILURE rather than as a skip.
 *
 * `test.skip()` exits 0 and is indistinguishable from a pass — the defect
 * class WORKFLOW.md §2 records five recurrences of. A precondition expressed
 * as a red test cannot be mistaken for success by anyone reading the gate.
 *
 * DELIBERATELY NOT `mode: "serial"` — same reason as F7-S1: serial mode turns
 * every test after the first failure into "did not run", which is the silent
 * skip again. A test-first spec exists to tell the developer WHICH ACs are
 * red, so it must report all of them on every run. Nothing here depends on a
 * previous test; each navigates fresh and `/api/derive` is stateless.
 *
 * ===========================================================================
 * PORT: EPHEMERAL, NOT 8000
 * ===========================================================================
 * WORKFLOW.md §2's false green — another agent's server answers on 8000 and
 * the spec reports green having tested somebody else's build. Same fix as
 * F7-S1: bind a free high port, spawn `rentcomp.app:app` under uvicorn, and
 * check both that OUR child is still alive and that it announced OUR port.
 *
 * ZERO LIVE CALLS (WORKFLOW.md §6, D17): `RENTCOMP_LIVE` emptied,
 * `RENTCAST_API_KEY` deleted from the child env, `RENTCOMP_HOME` a fresh temp
 * dir. The ledger cannot move.
 */
import { test, expect, type Page, type Route } from "@playwright/test";
import { existsSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
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

/** The two packages this story cannot be built without (D10 §1a). */
const MAP_PACKAGES = ["leaflet", "react-leaflet"] as const;

let serverProcess: ChildProcess | null = null;
let rentcompHome: string | null = null;
let setupFailure: string | null = null;
let baseUrl = "";
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
    if (serverProcess && serverProcess.exitCode !== null) return false; // died on bind
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

/** "The server answering us is the one we started." */
function serverIdentityProblem(): string | null {
  if (!serverProcess) return "no server process was ever spawned";
  if (serverProcess.exitCode !== null) {
    return (
      `the server we spawned exited with code ${serverProcess.exitCode}, yet ${baseUrl} ` +
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
  baseUrl = `http://127.0.0.1:${serverPort}`;
  rentcompHome = mkdtempSync(path.join(tmpdir(), "rentcomp-f6s1-home-"));

  const env = { ...process.env } as NodeJS.ProcessEnv;
  delete env.RENTCAST_API_KEY; // D17: a live call must be impossible, not unlikely
  env.RENTCOMP_LIVE = "";
  env.RENTCOMP_HOME = rentcompHome;
  env.RENTCOMP_UI_DIR = FRONTEND_DIST;
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

  if (!(await waitForServer(`${baseUrl}/openapi.json`, 30_000))) {
    setupFailure = `server did not come up on ${baseUrl} within 30s. Output:\n${serverLog.slice(-800)}`;
  }
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

// ===========================================================================
// P1 / P2 — the preconditions, both as loud failures rather than skips
// ===========================================================================

test("leaflet and react-leaflet are installed", () => {
  // ⚠ EXPECTED TO FAIL UNTIL F6-S1's DEVELOPER'S FIRST COMMIT. Read the
  // message below before treating this as a defect in the spec.
  const pkgPath = path.join(FRONTEND_DIR, "package.json");
  const declared: Record<string, string> = existsSync(pkgPath)
    ? {
        ...(JSON.parse(readFileSync(pkgPath, "utf8")).dependencies ?? {}),
        ...(JSON.parse(readFileSync(pkgPath, "utf8")).devDependencies ?? {}),
      }
    : {};

  const missing = MAP_PACKAGES.filter(
    (name) =>
      !(name in declared) || !existsSync(path.join(FRONTEND_DIR, "node_modules", name)),
  );

  expect(
    missing,
    `${missing.join(" and ")} not installed in frontend/. This is the EXPECTED state until ` +
      "F6-S1's developer's first act: both are budgeted and pre-authorised by " +
      "ARCHITECTURE.md D10 §1a, and QA is explicitly not the one to install them.\n\n" +
      "It is a FAILING test and not a skip on purpose: `test.skip()` exits 0, which on " +
      "this project is indistinguishable from a pass and is the defect class WORKFLOW.md " +
      "§2 records five recurrences of. Everything below this line is unmeasurable until " +
      "this line is green, and it says so out loud rather than quietly.",
  ).toEqual([]);
});

test("the harness stood up a server of our own, on a port nobody else holds", () => {
  expect(setupFailure, setupFailure ?? "").toBeNull();
  expect(
    serverIdentityProblem(),
    "the server this spec is about to test is not the one it started",
  ).toBeNull();
});
