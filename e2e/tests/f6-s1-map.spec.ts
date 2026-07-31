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
import { fixture, submitSearch } from "./support/static-ui";

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

/**
 * ===========================================================================
 * OPENING A PULL — WHY THE SETUP BELOW EXISTS (F4-S6, merged after this spec)
 * ===========================================================================
 * This spec used to reach Results by navigating there. That worked only
 * because `Results.tsx` held `const PULL_REF = "ws1-real"` and derived it
 * unconditionally on mount — so *any* visit to Results derived a fixture,
 * whatever the user had searched. F4-S6 removed that constant, and Results now
 * derives nothing until `App` holds an `OpenPull` (`frontend/src/pull.ts`).
 *
 * ⚠ THAT IS THE SEAM WORKING, NOT A REGRESSION IN EITHER STORY. Before it, the
 * owner could spend real API calls on a real address, open Results, and be
 * shown the analysis of a fixture with nothing on screen saying so. Nothing
 * here may weaken it: no default ref, no "derive on mount", no second door.
 * The only change below is **setup** — this spec now opens a pull the way the
 * user does, through the search form (`support/static-ui.ts::submitSearch`,
 * the same helper F4-S6's own specs use). Every assertion is untouched.
 *
 * WHY THE SEARCH RESPONSE IS STUBBED AND THE DERIVE IS NOT
 * --------------------------------------------------------
 * The map's assertions need a REAL derive: AC3 needs comps with genuine
 * coordinate spread, AC4 needs a payload that carries more than one `state`,
 * and AC7 needs the server to actually re-classify a comp when the user
 * unchecks it. A stubbed derive would make AC7 assert my own fixture back at
 * me. So `POST /api/derive` goes to the live server this file spawned, exactly
 * as before, and only `POST /api/search` is intercepted — because a real one
 * cannot answer here by design: `RENTCOMP_LIVE` is empty and the key is
 * deleted from the child env (D17), so a cache-miss search has nothing to
 * fetch and would mint a ref with no evidence behind it.
 *
 * The stub hands back the ONE ref this backend resolves offline:
 * `storage/pulls.py::WS1_REAL_PULL_REF`, which shapes the two committed T-S3
 * gate fixtures through the real record-shaping chain, independent of
 * `RENTCOMP_HOME`. The literal is written here and NOT in `frontend/src` —
 * `f4s6-derive-targets-the-users-pull.spec.ts` AC8d walks the source tree for
 * exactly this string and must keep finding nothing.
 */
const WS1_REAL_PULL_REF = "ws1-real";

/**
 * What `POST /api/search` answers. The wire shape is F4-S6's committed fixture
 * (re-validated against the Pydantic response models on every pytest run by
 * `test_f4s6_stub_payloads_match_the_wire.py`) with one field changed: the ref
 * this backend can actually derive. `complete: true` keeps the gap banner off
 * the screen — a partial-pull banner is F4-S6's subject, not this file's.
 */
const SEARCH_OUTCOME = { ...fixture("search-complete.json"), pull_ref: WS1_REAL_PULL_REF };

/**
 * A valid search to submit. Every value here is inert with respect to this
 * spec's assertions — `Results` still derives a hardcoded `SUBJECT` (disclosed
 * by F4-S6's developer as an open finding) and the comps come from the ref
 * above, not from these fields. They exist only to get past the form's inline
 * validation, so they are the ws1 fixture's own subject for coherence rather
 * than for effect. `01-01`–`12-31` matches the window `_load_ws1_real_pull`
 * reads the fixtures under.
 */
const SEARCH_VALUES = {
  address: "3651 S Wood St, Chicago, IL 60609",
  bedrooms: "4",
  bathrooms: "2",
  sqft: "1200",
  radius: "0.5",
  windowStart: "01-01",
  windowEnd: "12-31",
  yearsBack: "2",
};

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

// ---------------------------------------------------------------------------
// Locators and readers — testid first, behaviour always
// ---------------------------------------------------------------------------

const isDerive = (url: string) => url.includes("/api/derive");

function mapContainer(page: Page) {
  return page.locator("[data-testid='map']").first();
}

function pins(page: Page) {
  return page.locator("[data-testid='map-pin']");
}

function pinFor(page: Page, key: string) {
  return page.locator(`[data-testid='map-pin'][data-comp-key="${key}"]`).first();
}

function rows(page: Page) {
  return page.locator("[data-testid='comp-row']");
}

function rowFor(page: Page, key: string) {
  return page.locator(`[data-testid='comp-row'][data-comp-key="${key}"]`).first();
}

/** key -> `data-pin-state`, for every pin currently on the map. */
async function pinStates(page: Page): Promise<Record<string, string>> {
  const entries = await pins(page).evaluateAll((els) =>
    els.map((el) => [el.getAttribute("data-comp-key") ?? "", el.getAttribute("data-pin-state") ?? ""]),
  );
  return Object.fromEntries(entries);
}

/**
 * The comps the MAP says are included.
 *
 * Read off `data-pin-state`, which is the only thing about a pin this spec
 * pins. The colour that state is painted (green / grey / rust) is F6-S1's to
 * choose; the meaning is not.
 */
async function mapIncludedKeys(page: Page): Promise<string[]> {
  const states = await pinStates(page);
  return Object.entries(states)
    .filter(([, state]) => state === "included")
    .map(([key]) => key)
    .sort();
}

/**
 * The comps the LIST says are included — its checked rows.
 *
 * Deliberately read from the CHECKBOX and not from the payload: the whole
 * point of the invariant is to compare what the two panels TELL THE USER, and
 * a reader that consulted the response for one side would be comparing the
 * map against the server rather than against the list.
 */
async function listIncludedKeys(page: Page): Promise<string[]> {
  const entries = await rows(page).evaluateAll((els) =>
    els.map((el) => [
      el.getAttribute("data-comp-key") ?? "",
      !!el.querySelector<HTMLInputElement>("[data-testid='comp-include-toggle']")?.checked,
    ]),
  );
  return entries
    .filter(([, checked]) => checked)
    .map(([key]) => key as string)
    .sort();
}

async function renderedRowKeys(page: Page): Promise<string[]> {
  return await rows(page).evaluateAll((els) =>
    els.map((el) => el.getAttribute("data-comp-key") ?? ""),
  );
}

/**
 * Answer `POST /api/search` with {@link SEARCH_OUTCOME}, and nothing else.
 *
 * Deliberately narrow. `POST /api/search/plan` is left to the live server —
 * it is structurally incapable of spending a call (`api/plan.py`), so the cost
 * preview the form waits for is the real planner's answer rather than a
 * fixture pretending to be one. And the predicate matches the search route
 * *exactly*: a `"**\/api/search"` glob would be a trap the day someone reads it
 * as also covering `/api/search/plan`.
 */
async function stubTheSearch(page: Page): Promise<void> {
  await page.route(
    (url) => url.pathname === "/api/search",
    async (route: Route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(SEARCH_OUTCOME),
      });
    },
  );
}

/**
 * Open a pull and land on Results, then wait for its first derive.
 *
 * The navigation is the user's own: fill the search form, submit it, and let
 * `App.openPull` put the ref it got back on screen (F4 flow step 4, "the
 * system lands the user on Results"). Nothing here clicks the Results tab —
 * post-F4-S6 that reaches a view with no pull open, which correctly derives
 * nothing, and a spec that waited for a derive there would hang for 30s and
 * blame the map.
 */
async function gotoResults(page: Page): Promise<any> {
  await stubTheSearch(page);
  await page.goto(baseUrl + "/");
  const first = page.waitForResponse((r) => isDerive(r.url()) && r.request().method() === "POST", {
    timeout: 30_000,
  });
  await submitSearch(page, SEARCH_VALUES);
  const response = await first;
  expect(response.status(), "the initial derive did not succeed").toBe(200);
  await expect(rows(page).first()).toBeVisible({ timeout: 20_000 });
  return await response.json();
}

/** Run `action`; return the request body AND parsed response of the derive it caused. */
async function deriveCausedBy(
  page: Page,
  action: () => Promise<void>,
): Promise<{ body: any; payload: any }> {
  const responsePromise = page.waitForResponse(
    (r) => isDerive(r.url()) && r.request().method() === "POST",
    { timeout: 30_000 },
  );
  await action();
  const response = await responsePromise;
  return { body: JSON.parse(response.request().postData() ?? "{}"), payload: await response.json() };
}

/**
 * Require the map to exist before an assertion that would otherwise pass by
 * being vacuous.
 *
 * Every "no pin disagrees with the list" style assertion is trivially true
 * over an empty set. Three of the tests below would report green on a page
 * with no map at all without this — the vacuity trap F7-S1's QA shipped once
 * (`requireFilterControls`) and had caught by the developer.
 */
async function requireMap(page: Page): Promise<void> {
  await expect(
    mapContainer(page),
    "no map is mounted on Results, so every pin assertion below would be a claim about " +
      "the empty set. F6-S1: the map occupies the top of Results and is always visible " +
      "with the list (F6 epic, Entry).",
  ).toBeVisible({ timeout: 20_000 });
  expect(
    await pins(page).count(),
    "the map mounted but has no pins at all — the same vacuity, one level in",
  ).toBeGreaterThan(0);
}

/** Recompute `breakdown` after a test rewrote states, so no panel is torn. */
function reconcileBreakdown(payload: any): void {
  for (const state of ["included", "excluded", "filtered"] as const) {
    payload.breakdown[state] = payload.comps.filter((c: any) => c.state === state).length;
  }
}

test.describe("F6-S1 — the map and the list never disagree", () => {
  test.beforeEach(skipIfSetupFailed);

  // -------------------------------------------------------------------------
  // AC1 — there is a map
  // -------------------------------------------------------------------------

  test("the Results view mounts a map", async ({ page }) => {
    await gotoResults(page);
    await expect(
      mapContainer(page),
      "Results renders no map. The F6 epic's Entry is 'map occupies top of Results, always " +
        "visible with the list' — the map is not a panel the user opens, it is how they " +
        "judge comps by where they are",
    ).toBeVisible({ timeout: 20_000 });
  });

  // -------------------------------------------------------------------------
  // AC2 — one pin per comp, and no comp missing
  // -------------------------------------------------------------------------

  test("every comp in the payload has exactly one pin on the map", async ({ page }) => {
    const payload = await gotoResults(page);
    await requireMap(page);

    const pinned = await pins(page).evaluateAll((els) =>
      els.map((el) => el.getAttribute("data-comp-key") ?? ""),
    );
    const expected = payload.comps.map((c: any) => c.key).sort();

    const duplicated = pinned.filter((k, i) => pinned.indexOf(k) !== i);
    expect(
      duplicated,
      `${duplicated.length} comp(s) have more than one pin (${duplicated.slice(0, 3).join(", ")}). ` +
        "A comp rendered twice is two votes for one piece of evidence, and it makes every " +
        "count on the map disagree with the breakdown",
    ).toEqual([]);

    expect(
      pinned.slice().sort(),
      "the pins on the map are not exactly the comps in the payload. The map NEVER hides " +
        "evidence (F6 epic) — excluded and filtered comps keep their pins, which is what " +
        "makes a rust pin clickable back into the analysis. A comp on the list with no pin " +
        "is the map/list disagreement this story exists to prevent, in its quietest form. " +
        "(Clustering would also break this assertion — it is deferred out of this story.)",
    ).toEqual(expected);
  });

  // -------------------------------------------------------------------------
  // AC3 — pins are placed FROM the server's coordinates
  // -------------------------------------------------------------------------

  test("pins are placed by the server's coordinates, not by a swapped pair", async ({ page }) => {
    // Relative geometry, never pixels: the easternmost comp's pin must be to
    // the RIGHT of the westernmost, and the northernmost ABOVE the southernmost.
    //
    // That is enough to catch the failure that actually happens — lat and lng
    // handed to Leaflet in the wrong order, which draws a perfectly plausible
    // map of the wrong hemisphere — while pinning nothing about zoom, centre
    // or projection, all of which are deferred out of this story.
    const payload = await gotoResults(page);
    await requireMap(page);

    const comps = payload.comps as any[];
    const byLng = [...comps].sort((a, b) => a.lng - b.lng);
    const byLat = [...comps].sort((a, b) => a.lat - b.lat);
    const west = byLng[0];
    const east = byLng[byLng.length - 1];
    const south = byLat[0];
    const north = byLat[byLat.length - 1];

    expect(
      east.lng - west.lng,
      "every comp in this fixture shares one longitude, so this test could not tell a " +
        "correct map from a swapped one — it needs a pull with spread",
    ).toBeGreaterThan(0);
    expect(
      north.lat - south.lat,
      "every comp in this fixture shares one latitude — same problem, other axis",
    ).toBeGreaterThan(0);

    const box = async (key: string) => {
      const b = await pinFor(page, key).boundingBox();
      expect(
        b,
        `the pin for ${key} has no position on screen. A pin that is in the DOM but nowhere ` +
          "on the map is not evidence the user can judge by location",
      ).not.toBeNull();
      return b!;
    };

    const [westBox, eastBox, northBox, southBox] = [
      await box(west.key),
      await box(east.key),
      await box(north.key),
      await box(south.key),
    ];

    expect(
      eastBox.x,
      `the comp at lng ${east.lng} is drawn WEST of the comp at lng ${west.lng}. The pins ` +
        "are not being placed from the coordinates the server sent — the classic form of " +
        "this is lat and lng passed to Leaflet in the wrong order, which produces a map " +
        "that looks entirely reasonable and is 5,000 miles wrong",
    ).toBeGreaterThan(westBox.x);
    expect(
      northBox.y,
      `the comp at lat ${north.lat} is drawn SOUTH of the comp at lat ${south.lat} (screen y ` +
        "grows downward). Same failure, other axis",
    ).toBeLessThan(southBox.y);
  });

  // -------------------------------------------------------------------------
  // AC4 — each pin carries the server's state
  // -------------------------------------------------------------------------

  test("every pin carries the state the server gave its comp", async ({ page }) => {
    const payload = await gotoResults(page);
    await requireMap(page);

    const states = await pinStates(page);
    const disagreements = (payload.comps as any[])
      .filter((c) => states[c.key] !== c.state)
      .map((c) => `${c.key}: server said "${c.state}", pin says "${states[c.key] ?? "no pin"}"`);

    expect(
      disagreements,
      `${disagreements.length} pin(s) do not carry their comp's state:\n  ` +
        disagreements.slice(0, 5).join("\n  ") +
        "\n\nThe three states are the map's entire vocabulary (F6 epic: green = included, " +
        "grey = excluded, rust = filtered-out). A pin whose state is not the server's is a " +
        "comp the user will judge on the wrong information — and since `derive.py` labels " +
        "every comp exactly once from a single mask, the discrepancy can only have been " +
        "manufactured in the view.",
    ).toEqual([]);

    // Non-vacuity: a payload where every comp happens to be included would let
    // a map that hardcodes "included" pass the loop above.
    const distinct = new Set((payload.comps as any[]).map((c) => c.state));
    expect(
      distinct.size,
      "every comp in this pull carries the same state, so the loop above cannot distinguish " +
        "a map that reads `state` from one that hardcodes a single colour. The mutation " +
        "test below is the one that closes this — but the fixture should be able to show " +
        "at least two states on its own",
    ).toBeGreaterThan(1);
  });

  // -------------------------------------------------------------------------
  // AC5 — [INVARIANT] the two panels agree
  // -------------------------------------------------------------------------

  test("the map's included set is exactly the list's included set", async ({ page }) => {
    // THE STORY'S ONE INVARIANT, stated over what the user can see: the comps
    // the map paints as included, and the comps the list shows checked, are
    // the same comps. Neither side is read from the payload here — that would
    // be two comparisons against the server instead of one between the panels.
    await gotoResults(page);
    await requireMap(page);

    const onMap = await mapIncludedKeys(page);
    const inList = await listIncludedKeys(page);

    expect(
      onMap.length,
      "the map paints nothing as included, so this comparison is between two empty sets",
    ).toBeGreaterThan(0);

    const mapOnly = onMap.filter((k) => !inList.includes(k));
    const listOnly = inList.filter((k) => !onMap.includes(k));
    expect(
      { mapOnly, listOnly },
      "the map and the list disagree about which comps are included.\n" +
        `  included on the map but not checked in the list: ${mapOnly.slice(0, 5).join(", ") || "none"}\n` +
        `  checked in the list but not included on the map: ${listOnly.slice(0, 5).join(", ") || "none"}\n\n` +
        "This is F6-S1's single [INVARIANT] (inherited from the cut F6-S2, QUEUE.md row " +
        "21). `pipeline/derive.py` derives every panel from one `included` mask, so the " +
        "backend cannot produce this — only two renderers reading the label differently " +
        "can. A user who selects from the map and reads the total from the list is then " +
        "pricing against evidence they did not choose.",
    ).toEqual({ mapOnly: [], listOnly: [] });
  });

  // -------------------------------------------------------------------------
  // AC6 — [INVARIANT] and neither panel re-derives that set (D5/D13)
  // -------------------------------------------------------------------------

  test("pin state follows the server's label, not a predicate recomputed in the view", async ({
    page,
  }) => {
    // -----------------------------------------------------------------------
    // BEHAVIOURAL, NOT STRUCTURAL — this is the part that matters.
    //
    // F5-S2 proved a structural guard over `frontend/src` cannot catch this
    // class: a plain `w / total` written in TypeScript walked straight past
    // `f0-s1b-structural.spec.ts`'s regexes. What caught it was making the
    // server's answer and a plausible local answer DISAGREE, and then asking
    // which one is on screen.
    //
    // The map is the most tempting surface in this codebase to re-derive on,
    // because `distance_mi`, `censored` and `removal_class` are all sitting on
    // every comp and "which pins do I paint rust?" is one comparison away.
    //
    // So the response is rewritten so that every plausible local predicate
    // gives the OPPOSITE answer to the server's label, in both directions:
    //
    //   NEAREST comp   -> state "filtered"  (a distance predicate keeps it;
    //                     weight left > 0, so a weight predicate keeps it too)
    //   FARTHEST comp  -> state "included"  (a distance predicate drops it)
    //
    // Both directions are needed: one alone is also satisfied by a view that
    // ignores `state` entirely. And the mutated payload stays internally
    // consistent with `classify_membership` — "filtered" beats a positive
    // weight there, so nothing here asks the view to render an impossible
    // state.
    // -----------------------------------------------------------------------
    let nearest = "";
    let farthest = "";
    await page.route("**/api/derive", async (route: Route) => {
      const response = await route.fetch();
      const payload = await response.json();
      const byDistance = [...payload.comps].sort((a: any, b: any) => a.distance_mi - b.distance_mi);
      nearest = byDistance[0].key;
      farthest = byDistance[byDistance.length - 1].key;
      for (const comp of payload.comps) {
        if (comp.key === nearest) {
          comp.state = "filtered";
          comp.weight = 1.0;
          comp.contribution_share = null;
        }
        if (comp.key === farthest) {
          comp.state = "included";
          comp.weight = 1.0;
        }
      }
      reconcileBreakdown(payload);
      await route.fulfill({ response, json: payload });
    });

    await gotoResults(page);
    expect(nearest, "the route interception never ran").not.toBe("");
    await requireMap(page);

    const states = await pinStates(page);

    expect(
      states[nearest],
      `the nearest comp (${nearest}) is labelled "filtered" by the server and its pin says ` +
        `"${states[nearest] ?? "no pin"}". The map is deciding membership itself — D5/D13: ` +
        "the frontend never re-derives a classification, it renders what /api/derive " +
        "returned. Two implementations of one rule is exactly the drift the architecture " +
        "exists to remove, and on a map it is invisible until a user acts on it.",
    ).toBe("filtered");
    expect(
      states[farthest],
      `the farthest comp (${farthest}) is labelled "included" by the server and its pin says ` +
        `"${states[farthest] ?? "no pin"}". Same violation, other direction: the view greyed ` +
        "out a comp on its own reading of distance_mi.",
    ).toBe("included");

    // ...and the LIST must have followed the same label, or the two panels
    // disagree under exactly the conditions the invariant is about.
    const rowKeys = await renderedRowKeys(page);
    expect(
      rowKeys,
      `${nearest} is filtered and still has a row. The map keeps its pin (rust) and the list ` +
        "lets it go — that pairing IS this story",
    ).not.toContain(nearest);
    expect(
      rowKeys,
      `${farthest} is included and has no row — the list dropped a comp the server kept`,
    ).toContain(farthest);

    const onMap = await mapIncludedKeys(page);
    const inList = await listIncludedKeys(page);
    expect(
      onMap.includes(nearest),
      `${nearest} is painted as included on the map although it is filtered out of every ` +
        "calculation. A filtered comp stays VISIBLE as evidence and stays OUT of the " +
        "analysis (F7-S1) — painting it as included says the opposite of both",
    ).toBe(false);
    expect(
      { mapOnly: onMap.filter((k) => !inList.includes(k)), listOnly: inList.filter((k) => !onMap.includes(k)) },
      "under a payload whose labels contradict the geometry, the two panels parted company — " +
        "which is the precise failure the invariant names",
    ).toEqual({ mapOnly: [], listOnly: [] });
  });

  // -------------------------------------------------------------------------
  // AC7 — a curation change moves both, with no stale panel
  // -------------------------------------------------------------------------

  test("excluding a comp in the list repaints its pin, and the two still agree", async ({
    page,
  }) => {
    // The invariant is not a property of the first render; it has to hold
    // across every curation action, which is when the user is actually
    // looking. A map still painting a comp green after the user unchecked it
    // is the "no stale panels after a curation change" honesty invariant
    // (AGENT_QA.md step 3) in the form a user would most readily believe.
    const payload = await gotoResults(page);
    await requireMap(page);

    const target = (payload.comps as any[]).find((c) => c.state === "included")?.key;
    expect(target, "no comp starts included, so there is nothing to un-include").toBeTruthy();
    expect(
      await pinFor(page, target).getAttribute("data-pin-state"),
      "the comp this test is about does not start included on the map",
    ).toBe("included");

    const { payload: after } = await deriveCausedBy(page, () =>
      rowFor(page, target).locator("[data-testid='comp-include-toggle']").click(),
    );
    expect(
      after.comps.find((c: any) => c.key === target)?.state,
      "unchecking the row did not make the server call the comp excluded — the click never " +
        "reached the curation state, so nothing below is about the map",
    ).toBe("excluded");

    await expect(async () => {
      expect(
        await pinFor(page, target).getAttribute("data-pin-state"),
        `${target} was excluded by hand and its pin still reads "included". The map is ` +
          "showing a decision the user has already reversed; every judgement they make " +
          "from it after this point is made on stale evidence",
      ).toBe("excluded");
      expect(
        await mapIncludedKeys(page),
        "the pin repainted but the map's included set still contains the comp",
      ).not.toContain(target);
      expect(
        await listIncludedKeys(page),
        "the list still shows the comp checked after its own checkbox was clicked",
      ).not.toContain(target);
    }).toPass({ timeout: 10_000 });

    await expect(
      pinFor(page, target),
      "the excluded comp lost its pin entirely. Excluding is not deleting — the map never " +
        "hides evidence, and a pin that vanishes cannot be clicked back in",
    ).toBeVisible();
  });

  // -------------------------------------------------------------------------
  // AC8 — the subject is not a comp
  // -------------------------------------------------------------------------

  test("the subject is on the map and is not one of the comps", async ({ page }) => {
    // "The subject is never evidence for itself" (`Subject` docstring). On a
    // map that distinction is carried entirely by the marker, so a subject
    // drawn as an ordinary pin invites the user to count their own unit as a
    // comp — and it would also break AC2's one-pin-per-comp arithmetic.
    const payload = await gotoResults(page);
    await requireMap(page);

    const subject = page.locator("[data-testid='map-subject']");
    await expect(
      subject.first(),
      "no subject marker on the map. The user's orientation starts from where their own " +
        "unit is (F6 epic step 1: amber = subject) — without it the pins are a cloud with " +
        "no centre",
    ).toBeVisible({ timeout: 10_000 });
    expect(await subject.count(), "more than one subject marker").toBe(1);

    const subjectIsAComp = await subject
      .first()
      .evaluate((el) => el.getAttribute("data-testid") === "map-pin" || el.hasAttribute("data-comp-key"));
    expect(
      subjectIsAComp,
      "the subject marker is also a comp pin. The subject is never evidence for itself — a " +
        "marker that is both would be counted by anything that counts pins",
    ).toBe(false);

    const pinnedKeys = await pins(page).evaluateAll((els) =>
      els.map((el) => el.getAttribute("data-comp-key") ?? ""),
    );
    expect(
      pinnedKeys.length,
      "the number of comp pins changed once the subject was on screen — the subject is " +
        "being counted among the comps",
    ).toBe(payload.comps.length);
  });

  // -------------------------------------------------------------------------
  // AC9 — QUEUE.md row 9d: a comp that cannot be honestly placed
  // -------------------------------------------------------------------------

  test("a comp with no honest coordinate is never plotted at null island", async ({ page }) => {
    // -----------------------------------------------------------------------
    // QUEUE.md row 9d, at the view. `float(record.get("latitude") or 0.0)`
    // destroys "absent" before a `Spell` exists, so a chain in which no spell
    // reported a coordinate arrives at the map as an exact (0, 0) — a pin in
    // the Gulf of Guinea, 5,000 miles into the Atlantic, indistinguishable
    // from a comp that genuinely sits on the equator.
    //
    // Measured unreachable today: 0 of 539 records are missing a coordinate.
    // This test manufactures it, which is the only way to have the behaviour
    // decided BEFORE a pull with a gap in it arrives.
    //
    // ⚠ WHEN ROW 9d LANDS and types `lat`/`lng` as optional, the mutation
    // below becomes `null` and NOT ONE ASSERTION CHANGES — the claim is about
    // "no honest coordinate", not about the sentinel that currently encodes it.
    //
    // QA's recommendation, for the PM to rule on: render it as EXPLICITLY
    // UNPLACEABLE, do not silently omit it. Omitting makes the map show fewer
    // comps than the list without saying so, which is this story's own
    // invariant failing quietly; and the F6 epic's rule is that the map never
    // hides evidence. So: no marker on the geography (there is no honest place
    // to put one), plus a visible disclosure naming the comps that could not
    // be placed.
    // -----------------------------------------------------------------------
    let victim = "";
    let victimAddress = "";
    await page.route("**/api/derive", async (route: Route) => {
      const response = await route.fetch();
      const payload = await response.json();
      // By a rule, not by call order, so every response carves out the same comp.
      victim = payload.comps
        .map((c: any) => c.key)
        .sort()
        .at(0);
      const comp = payload.comps.find((c: any) => c.key === victim);
      victimAddress = comp.address;
      comp.lat = 0.0;
      comp.lng = 0.0;
      await route.fulfill({ response, json: payload });
    });

    await gotoResults(page);
    expect(victim, "the route interception never ran").not.toBe("");
    await expect(mapContainer(page)).toBeVisible({ timeout: 20_000 });

    expect(
      await pinFor(page, victim).count(),
      `${victim} reports no coordinate and the map placed a pin for it anyway. At (0, 0) ` +
        "that pin sits in the Gulf of Guinea — a comp for a Chicago subject drawn 5,000 " +
        "miles into the Atlantic, at a location no evidence supports. A comp that cannot " +
        "be honestly placed must not be silently placed (QUEUE.md row 9d; NORTH_STAR: " +
        "state the absence, never fill it in).",
    ).toBe(0);

    // Three shapes accepted, because the affordance's FORM is the developer's
    // (one element per unplaceable comp, a container of them, or a count with
    // the addresses in it) and only its EXISTENCE is the AC.
    const disclosure = page.locator("[data-testid='map-unplaceable']");
    const disclosureCount = await disclosure.count();
    const named =
      (await page
        .locator(`[data-testid='map-unplaceable'][data-comp-key="${victim}"]`)
        .count()) > 0 ||
      (await disclosure.filter({ has: page.locator(`[data-comp-key="${victim}"]`) }).count()) > 0 ||
      (disclosureCount > 0 && (await disclosure.first().innerText()).includes(victimAddress));
    expect(
      named,
      `${victim} has no pin (correct) and the map says nothing about why (not correct). ` +
        "Omitting it silently makes the map show fewer comps than the list without ever " +
        "admitting it — which is this story's own invariant failing in the quietest way " +
        "available. The map never hides evidence (F6 epic): the comp must be named as " +
        "unplaceable, not disappeared.",
    ).toBe(true);

    await expect(
      rowFor(page, victim),
      "the comp also vanished from the LIST. A missing coordinate is a gap in one field — " +
        "it is not grounds to drop a piece of evidence the user paid for",
    ).toBeVisible({ timeout: 10_000 });
  });
});

// ===========================================================================
// and the identity check again, AFTER the run (WORKFLOW.md §2)
// ===========================================================================

test("the server that answered every test above was still ours at the end", () => {
  test.skip(!!setupFailure, setupFailure ?? "");
  expect(
    serverIdentityProblem(),
    "our server died partway through, so some results above came from another process",
  ).toBeNull();
});
