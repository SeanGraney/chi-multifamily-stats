/**
 * F7-S1 — filter engine: the browser-genuine half.
 *
 * QA-authored, written RED before the developer's branch exists
 * (AGENT_QA.md protocol step 3).
 *
 * ---------------------------------------------------------------------------
 * WHY EACH TEST HERE IS L3 AND NOT L2
 * ---------------------------------------------------------------------------
 * `backend/tests/api/test_f7s1_filter_engine.py` owns everything that is a
 * `DerivedState` field comparison, and that is deliberately the LARGER half of
 * this story: which comps each filter takes, that a filtered comp is
 * numerically identical to a hand-excluded one across every statistic in the
 * payload, that an override beats every filter and is inert without one, and
 * the reconciliation identity across a filter/weight/override matrix. None of
 * it is repeated here — a Playwright spec asserting many exact numbers is the
 * third smell in AGENT_QA.md.
 *
 * What is left is what only a browser can witness:
 *
 *   1. that a filtered comp LEAVES THE LIST (which rows mount — a React fact,
 *      so decision-procedure step 3 sends it here);
 *   2. that the list obeys the SERVER'S label rather than re-deriving
 *      membership in TypeScript (D5/D13) — provable only by making the two
 *      disagree, which needs a real response and a real render;
 *   3. that a filter change re-renders the NUMBERS too, not only the list —
 *      the representative half of "and all calculations" (the split rule:
 *      exhaustive at L2, once here);
 *   4. that a manual INCLUDE override is client state that a filter RESET
 *      does not clear — a state-transition claim with no wire expression,
 *      because the server is stateless;
 *   5. that ALL/NONE still act over the visible set once something can
 *      actually be invisible;
 *   6. that a filtered comp stays RUST ON THE MAP — a render fact, and the
 *      one thing in this story that cannot be tested at all yet (F6-S1). It
 *      is a STRICT XFAIL below, never a skip.
 *
 * ---------------------------------------------------------------------------
 * TEST-ID CONTRACT (QA-proposed — PM to confirm before the developer starts)
 * ---------------------------------------------------------------------------
 * Existing, from F5-S2 (unchanged):
 *   [data-testid="comp-row"] + data-comp-key="<key>"
 *   [data-testid="comp-include-toggle"] / [data-testid="comp-weight"]
 *   [data-testid="select-all"] / [data-testid="select-none"]
 *
 * New for F7-S1 — the minimum a browser needs to drive the story at all:
 *   [data-testid="filter-hide-censored"]   a control that sets hide_censored
 *   [data-testid="filter-max-distance"]    a control that sets max_distance_mi
 *   [data-testid="filter-reset"]           clears the filters
 *   [data-testid="filtered-row"] + data-comp-key   a filtered comp, shown
 *                                          dimmed/collapsed, carrying
 *   [data-testid="filtered-include"]       its per-row INCLUDE override
 *
 * ⚠ SCOPE NOTE FOR THE PM, RAISED BEFORE THE DEVELOPER STARTS. Three of this
 * story's own acceptance criteria ("leave the list", "overrides survive
 * filter resets", ALL/NONE over the visible set) are not observable unless a
 * filter can be switched on and an override can be created from the browser.
 * F7-S2 is "filter strip + hidden footer" and is tagged Broad — the polished
 * strip, the collapsed "N filtered · show" affordance and the layout are
 * plainly its. QA's reading is therefore that F7-S1 owns *functional*
 * controls (however plain) and F7-S2 owns their designed form. If the PM
 * rules otherwise, these tests cannot be honestly written for this story and
 * the story cannot be verified against its own AC — that is a decision to
 * take deliberately, not to discover at the verify round.
 *
 * Every locator below falls back to a role/text query, so a developer who
 * names things differently but sensibly still passes.
 *
 * Deliberately NOT pinned: the filter controls' markup (checkbox vs toggle vs
 * button), the rust colour's hex (asserted as "the filtered pin state", not
 * as #b7410e), whether filtered rows are hidden or collapsed behind a
 * disclosure (both satisfy "leave the list" — what is asserted is that they
 * are not in the comp list), and the debounce interval.
 *
 * ---------------------------------------------------------------------------
 * PORT: EPHEMERAL, NOT 8000 — deliberately different from every other spec
 * ---------------------------------------------------------------------------
 * WORKFLOW.md §2 documents a false green with no bad code anywhere: the
 * server spawn's bind to 8000 fails because another agent holds it,
 * `waitForServer` succeeds against THAT agent's build, and the spec reports
 * green having tested somebody else's code. §2 names "have the spec bind an
 * ephemeral port" as the fix and nobody has done it yet.
 *
 * So this spec launches `uvicorn rentcomp.app:app` on a free high port
 * instead of the `rentcomp` console script (which hardcodes 8000 in
 * `__main__.py` with no override). It is the same ASGI app object the console
 * script serves — `rentcomp.app:app` — so nothing about what is under test
 * changes; what changes is that a second agent cannot be reached by accident.
 * D7's "one command launches it" is F0-S1b's AC and is covered by
 * `f0-s1b-integration.spec.ts`; it is not this story's claim.
 *
 * Two further guards, because "we bound a free port" is a claim and not a
 * fact: the spawned process must still be ALIVE (a lost bind kills uvicorn,
 * so a server answering while our child is dead is somebody else's), and its
 * startup banner must name OUR port. Both are checked before the first test
 * and again after the last.
 *
 * ---------------------------------------------------------------------------
 * ZERO LIVE CALLS (WORKFLOW.md §6, D17)
 * ---------------------------------------------------------------------------
 * `RENTCOMP_LIVE` is emptied, `RENTCAST_API_KEY` is removed from the child's
 * environment, `RENTCOMP_HOME` is a fresh temp dir, and the evidence is the
 * committed `fixtures/synthetic/pulls/`. The ledger cannot move.
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

/**
 * The source tree the server must import. `pip install -e .` resolves against
 * the MAIN checkout, so without this a worktree run tests the wrong branch —
 * silently, which is the whole point of WORKFLOW.md §2's warning.
 */
const BACKEND_SRC = path.join(REPO_ROOT, "backend", "src");

test.describe.configure({ mode: "serial" });

let serverProcess: ChildProcess | null = null;
let rentcompHome: string | null = null;
let setupFailure: string | null = null;
let baseUrl = "";
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

/**
 * "The server answering us is the one we started."
 *
 * Not a status code and not a version string — both are guessable and both
 * are identical on every agent's build. This asks two questions no other
 * agent's server can answer the same way: is OUR child still running (a lost
 * bind kills uvicorn, so a dead child plus a live port means we are talking
 * to somebody else), and did OUR child announce OUR port?
 */
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
  rentcompHome = mkdtempSync(path.join(tmpdir(), "rentcomp-f7s1-home-"));

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

// ---------------------------------------------------------------------------
// Locators — testid first, then a role/text fallback (see the contract note)
// ---------------------------------------------------------------------------

const isDerive = (url: string) => url.includes("/api/derive");

function rows(page: Page) {
  return page.locator("[data-testid='comp-row']");
}

function rowFor(page: Page, key: string) {
  return page.locator(`[data-testid='comp-row'][data-comp-key="${key}"]`).first();
}

function weightIn(page: Page, key: string) {
  return rowFor(page, key)
    .locator("[data-testid='comp-weight']")
    .or(rowFor(page, key).getByRole("spinbutton"))
    .first();
}

function bulkButton(page: Page, which: "all" | "none") {
  return page
    .locator(`[data-testid='select-${which}']`)
    .or(page.getByRole("button", { name: new RegExp(`^\\s*${which}\\s*$`, "i") }))
    .first();
}

function hideCensoredControl(page: Page) {
  return page
    .locator("[data-testid='filter-hide-censored']")
    .or(page.getByRole("checkbox", { name: /censor/i }))
    .or(page.getByRole("switch", { name: /censor/i }))
    .first();
}

function resetControl(page: Page) {
  return page
    .locator("[data-testid='filter-reset']")
    .or(page.getByRole("button", { name: /reset|clear.*filter/i }))
    .first();
}

/** A filtered comp as the UI surfaces it — the dimmed row behind "N filtered". */
function filteredEntry(page: Page, key: string) {
  return page.locator(`[data-testid='filtered-row'][data-comp-key="${key}"]`).first();
}

function includeOverrideControl(page: Page, key: string) {
  return filteredEntry(page, key)
    .locator("[data-testid='filtered-include']")
    .or(filteredEntry(page, key).getByRole("button", { name: /include/i }))
    .first();
}

function anchorText(page: Page) {
  return page.locator("[data-testid='anchor']").or(page.getByText(/anchor:/i)).first();
}

/** Navigate to Results and wait for its first derive. */
async function gotoResults(page: Page): Promise<any> {
  const first = page.waitForResponse((r) => isDerive(r.url()) && r.request().method() === "POST", {
    timeout: 30_000,
  });
  await page.goto(baseUrl + "/");
  const link = page
    .getByRole("link", { name: /results/i })
    .or(page.getByRole("button", { name: /results/i }))
    .or(page.locator("[data-testid='nav-results']"));
  if (await link.count()) await link.first().click();
  const response = await first;
  expect(response.status(), "the initial derive did not succeed").toBe(200);
  await expect(rows(page).first()).toBeVisible({ timeout: 20_000 });
  return response.json();
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

/** The keys of every comp row currently rendered in the list. */
async function renderedKeys(page: Page): Promise<string[]> {
  return await rows(page).evaluateAll((els) =>
    els.map((el) => el.getAttribute("data-comp-key") ?? ""),
  );
}

/**
 * Require a filter control to exist, with a message that names the scope
 * decision rather than looking like a locator typo.
 */
async function requireFilterControls(page: Page): Promise<void> {
  const count = await hideCensoredControl(page).count();
  expect(
    count,
    "no filter control is reachable from the browser. F7-S1's own AC — 'filtered comps " +
      "leave the list', 'manual INCLUDE overrides survive filter resets' — cannot be " +
      "exercised without one, and a test written around the gap would pass by being " +
      "vacuous. See this file's SCOPE NOTE: QA reads F7-S1 as owning functional controls " +
      "and F7-S2 as owning their designed form (the strip, the collapsed footer, layout).",
  ).toBeGreaterThan(0);
}

// ===========================================================================
// the unmarked precondition — one loud failure, so the skips below cannot
// masquerade as a pass (WORKFLOW.md §2: a skip that exits 0 is a false green)
// ===========================================================================

test("the harness stood up a server of our own, on a port nobody else holds", () => {
  expect(setupFailure, setupFailure ?? "").toBeNull();
  expect(
    serverIdentityProblem(),
    "the server this spec is about to test is not the one it started",
  ).toBeNull();
});

test.describe("F7-S1 — filters thin the view without deleting evidence", () => {
  test.beforeEach(skipIfSetupFailed);

  // -------------------------------------------------------------------------
  // AC4 — filtered comps leave the LIST
  // -------------------------------------------------------------------------

  test("a comp the server labels 'filtered' has no row in the comp list", async ({ page }) => {
    // Driven by rewriting the RESPONSE rather than by switching a filter on,
    // and that is the honest place for it: the rule under test is a CLIENT
    // rule — "the list renders the comps the server did not filter" — so the
    // payload is exactly the right precondition to state. It also makes this
    // test non-vacuous today, before any filter control exists, and keeps it
    // independent of which control the developer ends up building.
    let victim = "";
    await page.route("**/api/derive", async (route: Route) => {
      const response = await route.fetch();
      const payload = await response.json();
      // Chosen by a rule, not by call order, so every response in this test
      // carves out the SAME comp.
      victim = payload.comps
        .map((c: any) => c.key)
        .sort()
        .at(0);
      const comp = payload.comps.find((c: any) => c.key === victim);
      comp.state = "filtered";
      comp.contribution_share = null;
      payload.breakdown.included = payload.comps.filter((c: any) => c.state === "included").length;
      payload.breakdown.filtered = payload.comps.filter((c: any) => c.state === "filtered").length;
      await route.fulfill({ response, json: payload });
    });

    const payload = await gotoResults(page);
    expect(victim, "the route interception never ran").not.toBe("");

    const shown = await renderedKeys(page);
    expect(
      shown,
      `comp ${victim} is labelled 'filtered' by the server and is still rendered as a comp ` +
        "row. F7-S1: filtered comps LEAVE THE LIST. (They stay in the payload and on the " +
        "map — this asserts only that they are out of the list.)",
    ).not.toContain(victim);

    const expected = payload.comps
      .filter((c: any) => c.key !== victim)
      .map((c: any) => c.key)
      .sort();
    expect(
      shown.slice().sort(),
      "the list is not exactly the unfiltered comps — a filter must remove the filtered " +
        "comps and nothing else",
    ).toEqual(expected);
  });

  // -------------------------------------------------------------------------
  // AC10 — D5/D13: the list obeys the server, it does not re-derive membership
  // -------------------------------------------------------------------------

  test("list membership follows the server's state, not a predicate recomputed in TypeScript", async ({
    page,
  }) => {
    // -----------------------------------------------------------------------
    // THE D5 GUARD, BEHAVIOURAL. "Is this comp further than max_distance_mi?"
    // is one comparison — the single most tempting thing in this story to do
    // in the view, and `distance_mi` is right there on every comp.
    //
    // F5-S2 proved a structural regex over `frontend/src` cannot catch this
    // class of thing: `f0-s1b-structural.spec.ts` greps for `.reduce` and for
    // named statistics, and a plain `w / total` walked straight through it.
    // What caught it was making the two answers DISAGREE. Same technique
    // here, and it needs both directions to be conclusive:
    //
    //   the NEAREST comp is marked `filtered`  -> a local predicate keeps it
    //   the FARTHEST comp is marked `included` -> a local predicate drops it
    //
    // Whichever set is on screen names the implementation. One direction
    // alone would also be satisfied by a view that ignores `state` entirely.
    // -----------------------------------------------------------------------
    await requireFilterControls(page);

    let nearest = "";
    let farthest = "";
    await page.route("**/api/derive", async (route: Route) => {
      const response = await route.fetch();
      const payload = await response.json();
      const byDistance = [...payload.comps].sort((a: any, b: any) => a.distance_mi - b.distance_mi);
      nearest = byDistance[0].key;
      farthest = byDistance[byDistance.length - 1].key;
      for (const comp of payload.comps) {
        if (comp.key === nearest) comp.state = "filtered";
        if (comp.key === farthest) comp.state = "included";
      }
      await route.fulfill({ response, json: payload });
    });

    await gotoResults(page);
    await deriveCausedBy(page, () => hideCensoredControl(page).click());
    const shown = await renderedKeys(page);

    expect(
      shown,
      `the nearest comp (${nearest}) is on screen although the server labelled it ` +
        "'filtered'. The view is deciding membership itself — D5/D13: the frontend never " +
        "computes a statistic and never re-derives a classification; it renders what " +
        "/api/derive returned. Two implementations of one rule is exactly the drift the " +
        "architecture exists to remove.",
    ).not.toContain(nearest);
    expect(
      shown,
      `the farthest comp (${farthest}) is missing although the server labelled it ` +
        "'included' — same violation, other direction: the view dropped a comp on its own " +
        "reading of distance_mi.",
    ).toContain(farthest);
  });

  // -------------------------------------------------------------------------
  // AC3 (representative half) — the NUMBERS move too, not just the list
  // -------------------------------------------------------------------------

  test("switching a filter on re-renders the anchor, not only the comp list", async ({ page }) => {
    // The split rule. L2 asserts across every statistic and every filter that
    // a filtered comp is numerically identical to a hand-excluded one; this
    // asserts ONCE, in a browser, that the screen actually caught up — a list
    // that shrank while the anchor beside it still described the old evidence
    // is the "no stale panels" failure (F0-S2) wearing this story's clothes,
    // and it is the version of it a user would actually believe.
    await requireFilterControls(page);
    const initial = await gotoResults(page);

    const before = await anchorText(page).innerText();
    const { payload } = await deriveCausedBy(page, () => hideCensoredControl(page).click());

    expect(
      payload.breakdown.filtered,
      "hiding censored comps filtered nothing — this fixture cannot exercise the case",
    ).toBeGreaterThan(0);
    expect(
      payload.anchor?.n_comps,
      "the anchor still rests on as many comps after a filter removed some of them",
    ).toBeLessThan(initial.anchor.n_comps);

    await expect(async () => {
      const after = await anchorText(page).innerText();
      expect(
        after,
        `the anchor on screen still reads ${JSON.stringify(before)} after a filter took ` +
          `${payload.breakdown.filtered} comp(s) out of the evidence. Filtered comps leave ` +
          "the list AND ALL CALCULATIONS — a panel still showing the pre-filter number is a " +
          "claim resting on evidence the user just removed (NORTH_STAR).",
      ).not.toBe(before);
    }).toPass({ timeout: 5_000 });
  });

  // -------------------------------------------------------------------------
  // AC7 (client half) — an override is state a RESET does not clear
  // -------------------------------------------------------------------------

  test("a manual INCLUDE override survives a filter reset and the filter coming back", async ({
    page,
  }) => {
    // The server is stateless (F0-S2), so "survive" is a claim about the
    // CLIENT: the override list is durable curation, not a one-shot
    // instruction that the next filter change spends. L2 pins that the server
    // honours whatever list it is sent and that the list is inert with no
    // filter on; only a browser can witness that a RESET click does not
    // quietly empty it.
    await requireFilterControls(page);
    await gotoResults(page);

    const filtered = await deriveCausedBy(page, () => hideCensoredControl(page).click());
    const target = filtered.payload.comps.find((c: any) => c.state === "filtered")?.key;
    expect(target, "the filter took nothing, so there is nothing to override back in").toBeTruthy();

    const overridden = await deriveCausedBy(page, () =>
      includeOverrideControl(page, target).click(),
    );
    expect(
      overridden.body.include_overrides ?? [],
      `clicking INCLUDE on a filtered comp did not put ${target} into include_overrides. ` +
        "That list is the only channel by which a manual re-include reaches the server " +
        "(F7-S1); without it the click cannot survive anything.",
    ).toContain(target);
    expect(
      overridden.payload.comps.find((c: any) => c.key === target)?.state,
      "the overridden comp did not come back included",
    ).toBe("included");
    await expect(
      rowFor(page, target),
      "a comp re-included by hand is still not in the list — an override that leaves the " +
        "comp invisible is indistinguishable from no override at all",
    ).toBeVisible({ timeout: 10_000 });

    // --- RESET: filters go, the override stays.
    const reset = await deriveCausedBy(page, () => resetControl(page).click());
    expect(
      reset.body.filters?.hide_censored ?? false,
      "reset did not clear the filters",
    ).toBe(false);
    expect(
      reset.body.include_overrides ?? [],
      `resetting the filters also cleared the include_overrides (${target} is gone). The AC ` +
        "is literally 'manual INCLUDE overrides survive filter resets' — a reset returns the " +
        "view to unfiltered; it must not discard a decision the user made by hand.",
    ).toContain(target);

    // --- and the filter coming back finds the override still standing.
    const again = await deriveCausedBy(page, () => hideCensoredControl(page).click());
    expect(
      again.body.include_overrides ?? [],
      "re-applying the filter dropped the override — it survived the reset only until the " +
        "next filter change, which is not surviving",
    ).toContain(target);
    expect(
      again.payload.comps.find((c: any) => c.key === target)?.state,
      `${target} was filtered out again despite its manual INCLUDE. The user would have to ` +
        "redo the same decision every time they touch a filter.",
    ).toBe("included");
  });

  // -------------------------------------------------------------------------
  // AC9 — ALL/NONE over the VISIBLE set, now that something can be invisible
  // -------------------------------------------------------------------------

  test("ALL and NONE leave filtered comps' weights untouched, and the filter clears back to them", async ({
    page,
  }) => {
    // ---------------------------------------------------------------------
    // This closes the gap F5-S2's QA could only mark: its AC2b is a STRICT
    // XFAIL ("no comp can be made invisible from the browser") whose note
    // says to remove the marker when a filter control exists. This is the
    // assertion that note describes, written from this story's side.
    //
    // It EXTENDS QUEUE.md row 14b; it does not contradict it. Row 14b pins
    // that ALL and NONE are deliberately NOT inverses over the VISIBLE set.
    // What is new here is the set itself: a bulk action must not reach
    // evidence the user cannot see, in either direction — otherwise clearing
    // the filter resurrects comps at a weight the user never chose, which is
    // hidden state, and F7-S1's "overrides survive filter resets" would be
    // false for the plainest possible reason.
    // ---------------------------------------------------------------------
    await requireFilterControls(page);
    await gotoResults(page);

    const filtered = await deriveCausedBy(page, () => hideCensoredControl(page).click());
    const hidden: string[] = filtered.payload.comps
      .filter((c: any) => c.state === "filtered")
      .map((c: any) => c.key);
    const visible: string[] = filtered.payload.comps
      .filter((c: any) => c.state !== "filtered" && c.psf !== null)
      .map((c: any) => c.key);
    expect(hidden.length, "the filter took nothing — this test would be vacuous").toBeGreaterThan(0);
    expect(visible.length, "the filter took everything — ALL/NONE would sweep nothing").toBeGreaterThan(0);

    // Give one hidden comp a weight no bulk action would ever write, so
    // "left alone" and "overwritten" are distinguishable rather than
    // coincidentally equal (the technique row 14b established).
    const marked = hidden[0];
    const withMark = await deriveCausedBy(page, async () => {
      await resetControl(page).click();
      await weightIn(page, marked).fill("3");
      await weightIn(page, marked).blur();
    });
    expect(withMark.body.weights?.[marked], "the hand-set weight never reached the request").toBe(3);
    await deriveCausedBy(page, () => hideCensoredControl(page).click());

    const none = await deriveCausedBy(page, () => bulkButton(page, "none").click());
    expect(
      none.body.weights?.[marked],
      `NONE zeroed ${marked}, which the filter had taken out of view. NONE deselects ` +
        "everything VISIBLE — a bulk action must not reach evidence the user cannot see, " +
        "or clearing the filter brings comps back at a weight nobody chose (F7-S1: manual " +
        "overrides survive filter resets)",
    ).toBe(3);
    for (const key of visible) {
      expect(
        none.body.weights?.[key] ?? 1,
        `NONE left visible comp ${key} at a non-zero weight — it did not cover the rendered set`,
      ).toBe(0);
    }

    const all = await deriveCausedBy(page, () => bulkButton(page, "all").click());
    expect(
      all.body.weights?.[marked],
      `ALL overwrote ${marked} (now ${String(all.body.weights?.[marked])}, was 3), and it was ` +
        "filtered out of view. Same rule, other direction",
    ).toBe(3);

    // And the whole point of leaving it alone: it comes back as the user left it.
    const cleared = await deriveCausedBy(page, () => resetControl(page).click());
    expect(
      cleared.payload.comps.find((c: any) => c.key === marked)?.weight,
      `${marked} came back from behind the filter at a weight other than the 3 the user set`,
    ).toBe(3);
  });

  // -------------------------------------------------------------------------
  // AC5 — rust on the map. STRICT XFAIL: F6-S1 does not exist.
  // -------------------------------------------------------------------------

  test("a filtered comp keeps a pin on the map, in the filtered (rust) state", async ({ page }) => {
    // ---------------------------------------------------------------------
    // STRICT XFAIL — the pattern F4-S2's QA introduced on this project, and
    // the one F5-S2's AC2b used for exactly this reason one story earlier.
    //
    // "Stay rust on the map" is a render fact and belongs in a browser, but
    // F6-S1 (the Leaflet map) is not built: there is no map, no pin and no
    // legend to assert against. A test written now would pass by being
    // vacuous, or would have to be skipped.
    //
    // `test.fail()` rather than `test.skip()`, and the reason is the defect
    // class this project has hit FOUR times: a skip exits 0 and is
    // indistinguishable from a pass. This fails LOUDLY the day the gap
    // closes — the moment a map renders and this passes, Playwright reports
    // "expected to fail, but passed", which forces whoever lands F6-S1 to
    // delete this marker and confirm the assertion for real. The opposite
    // decay curve from a comment.
    //
    // The L2 half of this clause is NOT deferred and is already asserted:
    // `test_a_filter_relabels_every_comp_and_removes_none` pins that a
    // filtered comp stays in the payload with its coordinates, which is the
    // precondition without which no map could draw it.
    // ---------------------------------------------------------------------
    test.fail(
      true,
      "F6-S1 (Leaflet map) is not built, so there is no pin to assert a state on. Remove " +
        "this marker when the map renders — see this test's note.",
    );

    let victim = "";
    await page.route("**/api/derive", async (route: Route) => {
      const response = await route.fetch();
      const payload = await response.json();
      victim = payload.comps.map((c: any) => c.key).sort().at(0);
      payload.comps.find((c: any) => c.key === victim).state = "filtered";
      await route.fulfill({ response, json: payload });
    });
    await gotoResults(page);

    // The assertion this becomes, spelled out so whoever removes the marker
    // does not have to reinvent it. The rust COLOUR is not pinned — the pin's
    // filtered STATE is, because the colour is F6-S1's to choose and the
    // meaning ("still visible as evidence, out of the analysis") is not.
    const pin = page.locator(`[data-testid='map-pin'][data-comp-key="${victim}"]`).first();
    await expect(
      pin,
      `comp ${victim} is filtered and has no pin. The map NEVER hides evidence (F6 epic) — ` +
        "filtering thins the list, it does not delete anything",
    ).toBeVisible({ timeout: 10_000 });
    expect(
      await pin.getAttribute("data-pin-state"),
      "a filtered comp's pin does not carry the filtered state, so it is indistinguishable " +
        "from an excluded (grey) or included (green) one — and the rust pin is the only " +
        "route to the re-include override in the F6 flow",
    ).toBe("filtered");
    expect(
      await renderedKeys(page),
      "the same comp must be off the list while it is on the map — that pairing IS the story",
    ).not.toContain(victim);
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
