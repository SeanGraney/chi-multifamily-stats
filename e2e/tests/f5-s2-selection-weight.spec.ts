/**
 * F5-S2 — selection & weight state: the browser-genuine half.
 *
 * QA-authored, written RED before the developer's branch exists
 * (AGENT_QA.md protocol step 3).
 *
 * ---------------------------------------------------------------------------
 * WHY EACH TEST HERE IS L3 AND NOT L2
 * ---------------------------------------------------------------------------
 * `backend/tests/api/test_f5s2_selection_weight.py` owns everything that is a
 * `DerivedState` field comparison: the contribution formula across a dozen
 * weight shapes, weight-0 leaving every aggregate, the partition, the counts.
 * None of that is repeated here — a Playwright spec asserting many exact
 * numbers is the third smell in AGENT_QA.md.
 *
 * What is left is what only a browser can witness:
 *
 *   1. that the TOGGLE and the WEIGHT INPUT are one piece of state, not two
 *      that happen to agree today (a React bug, so L3 per the decision
 *      procedure's step 3);
 *   2. that ALL/NONE act over the RENDERED set;
 *   3. that the contribution number ON SCREEN is the server's and not a
 *      division done in TypeScript (D5) — provable only by making the two
 *      disagree, which needs a real response and a real render;
 *   4. that above ~40% it is a different colour (styling; jsdom has none);
 *   5. that a curation change leaves no panel stale.
 *
 * ---------------------------------------------------------------------------
 * TEST-ID CONTRACT (QA-proposed — PM to confirm before the developer starts)
 * ---------------------------------------------------------------------------
 *   [data-testid="comp-row"]              already exists (WS-1), plus
 *     data-comp-key="<comp key>"          so a row can be addressed by the
 *                                         same key the wire uses
 *   [data-testid="comp-include-toggle"]   per row; checked <=> included
 *   [data-testid="comp-weight"]           per row; the numeric weight control
 *   [data-testid="comp-contribution"]     per row; the contribution % text
 *   [data-testid="select-all"] / [data-testid="select-none"]   ALL / NONE
 *
 * Every locator below falls back to a role/text query, the technique
 * `f0-s1b-integration.spec.ts` and `ws1-results-view.spec.ts` already use, so
 * a developer who names things differently but sensibly still passes.
 *
 * Deliberately NOT pinned: the contribution string's format (parsed as a
 * number and compared with tolerance, so "61%", "61.0%" and "60.95%" all
 * pass), the warning colour's hex (asserted as "different from a
 * below-threshold row", not as #f5a623), and the exact warning threshold (the
 * story says "~40%", so the fixtures sit at 61% and 5%, far from the edge).
 *
 * ---------------------------------------------------------------------------
 * ZERO LIVE CALLS (WORKFLOW.md §6, D17)
 * ---------------------------------------------------------------------------
 * `RENTCOMP_LIVE` is never set, `RENTCOMP_HOME` is a fresh temp dir, and the
 * evidence is the committed `fixtures/live-samples/` that `pull_ref` resolves
 * against. The ledger stays at 8/50.
 */
import { test, expect, type Page, type Route } from "@playwright/test";
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

/** The source tree the server must import — `pip install -e .` points at MAIN. */
const BACKEND_SRC = path.join(REPO_ROOT, "backend", "src");

/**
 * NO `mode: "serial"` — removed with the F7-S1 dispatch, on the same evidence
 * WORKFLOW.md §2 now records. `playwright.config.ts` already pins
 * `workers: 1, fullyParallel: false`, so serial mode added nothing except
 * "stop after the first failure": F7-S1's QA measured the same red suite
 * reporting `1 failed, 6 did not run` with it and `5 failed, 3 passed,
 * 0 did not run` without. "Did not run" reads like a pass to anyone scanning
 * exit codes, and it hides the shape of the failure set from the developer
 * who has to fix it. Nothing below depends on a previous test: every one of
 * them navigates fresh and `/api/derive` is stateless.
 *
 * The server harness has also moved to WORKFLOW.md §2's ratified pattern —
 * `python -m uvicorn rentcomp.app:app` on an OS-chosen port instead of the
 * `rentcomp` console script on a hardcoded 8000 — because the port-contention
 * false green it prevents (another agent's build answering, spec reports
 * green) is not something this spec could otherwise detect. Same ASGI object,
 * so nothing about what is under test changes; D7's "one command launches it"
 * is F0-S1b's AC and `f0-s1b-integration.spec.ts` still covers it.
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
      /* not up yet */
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
  rentcompHome = mkdtempSync(path.join(tmpdir(), "rentcomp-f5s2-home-"));

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
// Locators — testid first, then a role/text fallback (see the contract note)
// ---------------------------------------------------------------------------

const isDerive = (url: string) => url.includes("/api/derive");

function rows(page: Page) {
  return page.locator("[data-testid='comp-row']");
}

/**
 * ⚠ AMENDED BY F6-S1, and the amendment is a locator fix with no assertion
 * touched. Every `expect` in this file is exactly as F5-S2's QA wrote it.
 *
 * The `.or()` fallback below is deliberately loose — "however the developer
 * marked up a row, find it by its key" — and it was unambiguous when
 * `data-comp-key` appeared on comp rows and nowhere else. F6-S1 puts a map at
 * the TOP of Results, and its testid contract (written by F7-S1's QA, adopted
 * verbatim by F6-S1's) carries the same `data-comp-key` on every pin:
 *
 *     [data-testid='map-pin'][data-comp-key="<key>"]
 *
 * So `[data-comp-key="K"]` now matches a pin AND a row, and `.first()` takes
 * DOM order — the pin. The five failures that produced were not F5-S2
 * regressing: `rowFor` was silently returning a map marker, which has no
 * checkbox, no weight box and no contribution cell. Excluding the map's own
 * markers keeps the fallback's intent ("any element addressable by this key")
 * while restoring the thing it was always about: a ROW.
 */
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

function toggleIn(row: ReturnType<typeof rowFor>) {
  return row
    .locator("[data-testid='comp-include-toggle']")
    .or(row.getByRole("checkbox"))
    .or(row.getByRole("switch"))
    .first();
}

function weightIn(row: ReturnType<typeof rowFor>) {
  return row
    .locator("[data-testid='comp-weight']")
    .or(row.getByRole("spinbutton"))
    .or(row.locator("input[type='number']"))
    .first();
}

function contributionIn(row: ReturnType<typeof rowFor>) {
  return row.locator("[data-testid='comp-contribution']").first();
}

function bulkButton(page: Page, which: "all" | "none") {
  return page
    .locator(`[data-testid='select-${which}']`)
    .or(page.getByRole("button", { name: new RegExp(`^\\s*${which}\\s*$`, "i") }))
    .first();
}

/** Navigate to Results and wait for its first derive to come back. */
async function gotoResults(page: Page): Promise<any> {
  const first = page.waitForResponse((r) => isDerive(r.url()) && r.request().method() === "POST", {
    timeout: 20_000,
  });
  await page.goto(BASE_URL + "/");
  const link = page
    .getByRole("link", { name: /results/i })
    .or(page.getByRole("button", { name: /results/i }))
    .or(page.locator("[data-testid='nav-results']"));
  if (await link.count()) await link.first().click();
  const response = await first;
  expect(response.status(), "the initial derive did not succeed").toBe(200);
  await expect(rows(page).first()).toBeVisible({ timeout: 15_000 });
  return response.json();
}

/**
 * Run `action`, then return the request body AND the parsed response of the
 * derive it caused. Both halves matter: the body is what the client decided
 * (ALL/NONE, toggle, weight), the response is what the screen must then show.
 */
async function deriveCausedBy(
  page: Page,
  action: () => Promise<void>
): Promise<{ body: any; payload: any }> {
  const responsePromise = page.waitForResponse(
    (r) => isDerive(r.url()) && r.request().method() === "POST",
    { timeout: 20_000 }
  );
  await action();
  const response = await responsePromise;
  const body = JSON.parse(response.request().postData() ?? "{}");
  return { body, payload: await response.json() };
}

function firstIncluded(payload: any): any {
  const comp = payload.comps.find((c: any) => c.state === "included");
  expect(comp, "no comp came back included — nothing in this spec can be exercised").toBeTruthy();
  return comp;
}

/** "61.0%" -> 61.0. Format-agnostic on purpose (see the contract note). */
function percentIn(text: string): number {
  const match = text.match(/(-?[\d.]+)\s*%/);
  expect(match, `no percentage found in ${JSON.stringify(text)}`).toBeTruthy();
  return Number(match![1]);
}

// ---------------------------------------------------------------------------

test.describe("F5-S2 — curation controls drive the derive", () => {
  test.beforeEach(skipIfSetupFailed);

  test("AC1: toggling a comp off and setting its weight to 0 are the same state", async ({
    page,
  }) => {
    // [INVARIANT] "one source of truth: the weight". The wire already makes a
    // second channel unrepresentable (`extra="forbid"`, pinned at L2). What a
    // browser adds is the half the wire cannot see: that the two CONTROLS
    // write to one piece of React state. Two `useState`s that are updated
    // together today, and drift the first time someone adds a third control,
    // pass every backend test in this repo and fail here.
    const initial = await gotoResults(page);
    const target = firstIncluded(initial).key;
    const row = rowFor(page, target);
    await expect(
      row,
      `no rendered row carries data-comp-key="${target}" — rows must be addressable by the ` +
        "same key the wire uses (F13-S1 keys curation by address+unit, not by listing id)"
    ).toBeVisible({ timeout: 10_000 });

    // Path A — the toggle.
    const viaToggle = await deriveCausedBy(page, () => toggleIn(row).click());
    expect(
      viaToggle.body.weights?.[target],
      "toggling a comp off did not send weight 0 for it. Toggle-off IS weight 0 (F5-S2 " +
        "[INVARIANT]); anything else means selection is being tracked somewhere other than " +
        "the weight"
    ).toBe(0);
    await expect(
      weightIn(row),
      "the comp was toggled off but its weight control does not read 0 — the toggle and the " +
        "weight input are two sources of truth that can disagree on screen"
    ).toHaveValue(/^0(\.0*)?$/);

    // Path B — the weight input, from a clean load.
    const reloaded = await gotoResults(page);
    expect(
      firstIncluded(reloaded).key,
      "the same comp is no longer the first included one after a reload — the two paths " +
        "would not be comparable"
    ).toBe(target);
    const row2 = rowFor(page, target);
    const viaWeight = await deriveCausedBy(page, async () => {
      await weightIn(row2).fill("0");
      await weightIn(row2).blur();
    });
    expect(viaWeight.body.weights?.[target]).toBe(0);
    await expect(
      toggleIn(row2),
      "the comp's weight was set to 0 but its toggle still reads included — same two " +
        "sources of truth, other direction"
    ).not.toBeChecked();

    // AC, literally: identical derived state. `DerivedState` carries no clock
    // or environment field by construction (ADR-001 §4.6), so this can be an
    // exact comparison rather than a field-by-field one with exclusions.
    expect(
      viaWeight.payload,
      "toggling a comp off and setting its weight to 0 produced DIFFERENT derived state. " +
        "That is the story's acceptance criterion verbatim, and it can only differ if the " +
        "two controls take different paths into the request"
    ).toEqual(viaToggle.payload);
  });

  test("AC2: NONE deselects every visible comp and ALL restores them", async ({ page }) => {
    // No filter is active, so "currently visible" is the whole pull. The
    // filtered-out half of this AC is the strict-xfail test below.
    const initial = await gotoResults(page);
    const visibleKeys: string[] = initial.comps.map((c: any) => c.key);

    const none = await deriveCausedBy(page, () => bulkButton(page, "none").click());
    const missed = visibleKeys.filter((k) => (none.body.weights?.[k] ?? 1) !== 0);
    expect(
      missed.slice(0, 5),
      `NONE left ${missed.length} of ${visibleKeys.length} visible comps at a non-zero ` +
        "weight — the bulk action did not cover the rendered set"
    ).toEqual([]);
    expect(
      none.payload.breakdown.included,
      "after NONE the server still counts comps as included"
    ).toBe(0);

    const all = await deriveCausedBy(page, () => bulkButton(page, "all").click());
    // Scoped to comps that HAVE a $/sqft. When this test was written, whether
    // ALL should also re-include the no-sqft comps (which default to weight 0,
    // F5 epic edge) was an open question escalated to the PM, so this
    // deliberately did not pre-judge it. The PM has since ruled and QUEUE.md
    // row 23 records the ruling; AC2c below now asserts it in BOTH directions.
    // This scoping stays exactly as it is — it is what makes AC2c's carve-out
    // an addition rather than a contradiction.
    const withPsf = initial.comps.filter((c: any) => c.psf !== null).map((c: any) => c.key);
    const notRestored = withPsf.filter((k: string) => (all.body.weights?.[k] ?? 0) <= 0);
    expect(
      notRestored.slice(0, 5),
      `ALL left ${notRestored.length} visible comps (that have a $/sqft) at weight 0`
    ).toEqual([]);
    expect(
      all.payload.breakdown.included,
      "after ALL the server counts nothing as included"
    ).toBeGreaterThan(0);
  });

  test("AC2c: ALL preserves a manual override of a no-sqft comp; NONE zeroes it", async ({
    page,
  }) => {
    // -----------------------------------------------------------------------
    // ⚠ THIS TEST PINS AN ASYMMETRY ON PURPOSE. DO NOT "FIX" IT.
    //
    // QUEUE.md row 23: ALL and NONE are deliberately NOT inverses.
    //
    //   ALL  writes weight 1 only where `psf !== null`, so a manual
    //        re-include of a no-sqft comp SURVIVES a bulk sweep. The F5 epic
    //        says no-sqft comps are "excluded by default, **manual**
    //        re-include allowed" — and a bulk sweep is not a manual act. It
    //        also stops a comp that contributes nothing to the anchor from
    //        silently acquiring a contribution share.
    //   NONE zeroes everything visible, INCLUDING that override, because
    //        "deselect everything I can see" has no ambiguous cases.
    //
    // The consequence, stated plainly so nobody discovers it as a "bug":
    // after NONE→ALL a no-sqft re-include is gone permanently until the user
    // redoes it by hand. Each half was judged individually right; the pair is
    // not symmetric and is not meant to be.
    //
    // Row 14b exists because F5-S2's AC2 asserted this in NEITHER direction —
    // a rule with a written-down rationale and no test is one refactor away
    // from being tidied into symmetry by someone acting in good faith.
    // -----------------------------------------------------------------------

    // The `ws1-real` pull is not guaranteed to contain a comp with no sqft,
    // and a test that silently does nothing when it doesn't is worse than no
    // test. So one comp is MADE no-sqft on every derive response — the same
    // route-rewriting technique AC3 uses, and the reason it is honest here is
    // that the rule under test is a CLIENT rule ("ALL skips comps whose psf
    // is null"), so the payload is exactly the right place to state the
    // precondition. `state` and `weight` are rewritten alongside `psf` so the
    // row starts where a real no-sqft comp starts: visible, at weight 0.
    let noSqftKey = "";
    await page.route("**/api/derive", async (route: Route) => {
      const response = await route.fetch();
      const payload = await response.json();
      // Chosen by a rule, not by call order, so every response in this test
      // carves out the SAME comp.
      noSqftKey = payload.comps
        .map((c: any) => c.key)
        .sort()
        .at(0);
      const comp = payload.comps.find((c: any) => c.key === noSqftKey);
      comp.psf = null;
      comp.weight = 0;
      comp.state = "excluded";
      comp.contribution_share = null;
      await route.fulfill({ response, json: payload });
    });

    const initial = await gotoResults(page);
    expect(noSqftKey, "the route interception never ran").not.toBe("");

    const withPsf: string[] = initial.comps
      .filter((c: any) => c.psf !== null)
      .map((c: any) => c.key);
    expect(
      withPsf.length,
      "every comp came back without a $/sqft, so ALL would sweep nothing and this test " +
        "would pass vacuously"
    ).toBeGreaterThan(0);

    const row = rowFor(page, noSqftKey);
    await expect(row, `no rendered row for the no-sqft comp ${noSqftKey}`).toBeVisible({
      timeout: 10_000,
    });
    await expect(
      toggleIn(row),
      "a comp with no $/sqft rendered as included — it cannot contribute to a $/sqft-based " +
        "anchor, so it starts excluded (F5 epic edge) and is opt-in"
    ).not.toBeChecked();

    // The MANUAL re-include, at a weight ALL would never write. ALL writes 1;
    // this writes 2, so "ALL left it alone" and "ALL overwrote it" are
    // distinguishable rather than coincidentally equal.
    const override = await deriveCausedBy(page, async () => {
      await weightIn(row).fill("2");
      await weightIn(row).blur();
    });
    expect(
      override.body.weights?.[noSqftKey],
      "manually setting a weight on a no-sqft comp did not reach the request — a no-sqft " +
        "comp must still be re-includable by hand"
    ).toBe(2);

    // --- ALL: sweeps the selectable comps, and leaves the override standing.
    const all = await deriveCausedBy(page, () => bulkButton(page, "all").click());
    const notSwept = withPsf.filter((k) => (all.body.weights?.[k] ?? 0) <= 0);
    expect(
      notSwept.slice(0, 5),
      `ALL left ${notSwept.length} comps that DO have a $/sqft at weight 0 — the sweep did ` +
        "not happen, so the assertion below would pass for the wrong reason"
    ).toEqual([]);
    expect(
      all.body.weights?.[noSqftKey],
      `ALL overwrote a manual re-include of a no-sqft comp (it is now ` +
        `${String(all.body.weights?.[noSqftKey])}, was 2). ALL means "select what is ` +
        'selectable by default"; a no-sqft comp is not selectable by default (F5 epic: ' +
        '"excluded by default, MANUAL re-include allowed"), and a bulk sweep is not a ' +
        "manual act. QUEUE.md row 23: this is deliberate, not a missing case"
    ).toBe(2);

    // --- NONE: deselects everything visible, override included.
    const none = await deriveCausedBy(page, () => bulkButton(page, "none").click());
    expect(
      none.body.weights?.[noSqftKey],
      `NONE left the no-sqft comp at ${String(none.body.weights?.[noSqftKey])}. NONE means ` +
        '"deselect everything I can see" — there is no ambiguity to protect here, and a ' +
        "comp still carrying weight after the user pressed NONE is hidden state, which is " +
        "the one thing the F5 flow is explicitly against"
    ).toBe(0);
    expect(
      none.payload.breakdown.included,
      "after NONE the server still counts comps as included"
    ).toBe(0);
  });

  test("AC2b: ALL/NONE leave filtered-out comps' weights untouched", async ({ page }) => {
    // ---------------------------------------------------------------------
    // The strict xfail that used to guard this test is GONE, and its own note
    // is why: "Remove this marker when a filter control exists." F7-S1/F7-S2
    // landed the filter strip, so from the moment that shipped Playwright
    // would have reported "expected to fail, but passed" — a FAILURE at the
    // regression gate. The marker did exactly the job it was written to do
    // (the opposite decay curve from a comment), and this is the removal it
    // was asking for. PM ruling, F7-S1 dispatch.
    //
    // Two changes to the body it spelled out, both of which make it assert
    // MORE than the sketch did, and both flagged to the PM rather than made
    // quietly:
    //
    //   1. the filter click is now awaited through `deriveCausedBy`. As
    //      written, the click and the NONE click landed inside one 150ms
    //      debounce window, so they coalesced into a single request and the
    //      view still held the pre-filter comp set when NONE swept it. The
    //      test could not have passed under any implementation.
    //   2. the comp checked is read off the response's own `state` labels
    //      rather than being `comps[0]` by position. Position assumed the
    //      first comp in the payload happens to be one the filter takes; the
    //      server's label is the thing the AC is actually about.
    // ---------------------------------------------------------------------
    await gotoResults(page);
    const filterControl = page
      .locator("[data-testid='filter-strip']")
      .or(page.getByRole("button", { name: /filter/i }))
      .or(page.locator("[data-testid='filter-max-distance']"));
    expect(
      await filterControl.count(),
      "no filter control is reachable from the browser — F7-S1/F7-S2 own the strip"
    ).toBeGreaterThan(0);

    const filtered = await deriveCausedBy(page, () => filterControl.first().click());
    const filteredKeys: string[] = filtered.payload.comps
      .filter((c: any) => c.state === "filtered")
      .map((c: any) => c.key)
      .slice(0, 1);
    expect(
      filteredKeys.length,
      "switching the filter control on took no comps at all, so this test would be vacuous"
    ).toBe(1);

    const none = await deriveCausedBy(page, () => bulkButton(page, "none").click());
    for (const key of filteredKeys) {
      expect(
        none.body.weights?.[key] ?? 1,
        `NONE zeroed ${key}, which was filtered out of view. A bulk action must not touch ` +
          "evidence the user cannot see — otherwise clearing the filter resurrects comps at " +
          "a weight the user never chose (F7-S1: manual overrides survive filter resets)"
      ).not.toBe(0);
    }
  });

  test("AC3: the contribution % on screen is the server's number, not a division done in TypeScript", async ({
    page,
  }) => {
    // ---------------------------------------------------------------------
    // THE D5 GUARD. Contribution % is `weight / Σ weights` — trivial
    // arithmetic, and therefore the single most tempting thing in this
    // project to compute in the view. The story says it is computed
    // server-side "like every other derived value".
    //
    // A regex over `frontend/src` cannot catch this (`f0-s1b-structural.spec`
    // already greps for `.reduce` and for named statistics; a plain `w /
    // total` sails past it). What catches it is making the two answers
    // DISAGREE: the response is rewritten so every weight is 1 — a naive
    // local division would give 1/N — while `contribution_share` says
    // something else entirely. Whichever number appears on screen names the
    // implementation.
    // ---------------------------------------------------------------------
    await page.route("**/api/derive", async (route: Route) => {
      const response = await route.fetch();
      const payload = await response.json();
      const included = payload.comps.filter((c: any) => c.state === "included");
      expect(included.length, "the fixture derive returned no included comps").toBeGreaterThan(2);

      const rest = (1 - 0.61 - 0.05) / (included.length - 2);
      included.forEach((comp: any, index: number) => {
        comp.weight = 1.0; // naive local division would now give 1/N for all
        comp.contribution_share = index === 0 ? 0.61 : index === 1 ? 0.05 : rest;
      });
      await route.fulfill({ response, json: payload });
    });

    const payload = await gotoResults(page);
    const included = payload.comps.filter((c: any) => c.state === "included");
    const naive = 100 / included.length;

    const dominant = rowFor(page, included[0].key);
    const cell = contributionIn(dominant);
    await expect(
      cell,
      "no [data-testid='comp-contribution'] in the row — the contribution % must be rendered " +
        "per row so dominance is visible (F5 epic, step 3)"
    ).toBeVisible({ timeout: 10_000 });

    const shown = percentIn((await cell.innerText()).trim());
    expect(
      shown,
      `the row shows ${shown}% where the server said 61%. ${
        Math.abs(shown - naive) < 1
          ? `That is ${naive.toFixed(1)}% — exactly weight/Σweights computed locally. ` +
            "The division has been moved into TypeScript, which is a D5/D13 violation: " +
            "one formula, one implementation, in Python."
          : "The view is not rendering contribution_share."
      }`
    ).toBeCloseTo(61, 0);
  });

  test("AC3b: a comp above ~40% contribution renders in a warning colour", async ({ page }) => {
    // The split rule: L2 asserts the VALUE across a dozen weight combinations
    // and asserts that it is never capped; this asserts, once, that the view
    // colours it. The threshold itself is "~40%" in the story, so the two
    // fixtures sit at 61% and 5% — far from the edge, because pinning the
    // exact cutoff would be pinning a "~".
    await page.route("**/api/derive", async (route: Route) => {
      const response = await route.fetch();
      const payload = await response.json();
      const included = payload.comps.filter((c: any) => c.state === "included");
      const rest = (1 - 0.61 - 0.05) / (included.length - 2);
      included.forEach((comp: any, index: number) => {
        comp.contribution_share = index === 0 ? 0.61 : index === 1 ? 0.05 : rest;
      });
      await route.fulfill({ response, json: payload });
    });

    const payload = await gotoResults(page);
    const included = payload.comps.filter((c: any) => c.state === "included");

    const dominantCell = contributionIn(rowFor(page, included[0].key));
    const quietCell = contributionIn(rowFor(page, included[1].key));
    await expect(dominantCell).toBeVisible({ timeout: 10_000 });
    await expect(quietCell).toBeVisible();

    const colourOf = (locator: typeof dominantCell) =>
      locator.evaluate((el) => getComputedStyle(el).color);
    const [dominant, quiet] = [await colourOf(dominantCell), await colourOf(quietCell)];

    expect(
      dominant,
      `a comp at 61% contribution renders in ${dominant}, the same colour as a comp at 5% ` +
        `(${quiet}). The F5 epic's edge is "one comp >~40% contribution → its contribution % ` +
        'renders in warning colour (no hard cap by design)" — the colour IS the warning, and ' +
        "it is the only part of this the view is responsible for"
    ).not.toBe(quiet);
  });

  test("AC4: a curation change re-derives, and no row is left showing stale numbers", async ({
    page,
  }) => {
    // F5's success criterion is "no hidden state" (and "every recompute
    // <100ms" — the server-side budget is measured at L2 in
    // `test_derive_performance.py`; what a browser adds is that the RENDER
    // follows the response rather than lagging behind it).
    const initial = await gotoResults(page);
    const target = firstIncluded(initial).key;
    const row = rowFor(page, target);

    await expect(
      contributionIn(row),
      "an included comp renders no contribution % before any edit"
    ).toBeVisible({ timeout: 10_000 });

    const started = Date.now();
    const { payload } = await deriveCausedBy(page, () => toggleIn(row).click());
    expect(
      payload.comps.find((c: any) => c.key === target)?.state,
      "the server did not treat the toggled comp as excluded"
    ).toBe("excluded");

    // The row must catch up with the response it caused. Written as a polled
    // block rather than `not.toContainText`, because the view may legitimately
    // render a dash OR drop the element entirely for an excluded comp, and a
    // negated text assertion against a missing element is not reliable.
    await expect(async () => {
      const cell = contributionIn(row);
      const text = (await cell.count()) ? (await cell.innerText()).trim() : "";
      expect(
        text,
        "the comp is excluded but its row still shows a contribution % — a share of an " +
          "analysis a comp is no longer part of is a stale panel, and it is the exact " +
          "thing the F5 flow's 'instantly' is about"
      ).not.toMatch(/%/);
    }).toPass({ timeout: 5_000 });
    await expect(toggleIn(row)).not.toBeChecked({ timeout: 5_000 });

    expect(
      Date.now() - started,
      "a single toggle took over 2s to come back and re-render. The server budget is <100ms " +
        "(asserted at L2); this is the generous browser-side ceiling that catches a round " +
        "trip that never settles at all"
    ).toBeLessThan(2_000);
  });
});
