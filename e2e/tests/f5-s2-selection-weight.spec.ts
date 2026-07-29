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
const BASE_URL = "http://127.0.0.1:8000";

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
      /* not up yet */
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

  rentcompHome = mkdtempSync(path.join(tmpdir(), "rentcomp-f5s2-home-"));
  serverProcess = spawn(VENV_RENTCOMP, [], {
    cwd: REPO_ROOT,
    env: { ...process.env, RENTCOMP_HOME: rentcompHome, RENTCOMP_LIVE: "" },
    stdio: "pipe",
  });

  if (!(await waitForServer(`${BASE_URL}/openapi.json`, 20_000))) {
    setupFailure = "rentcomp server did not come up within 20s";
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
  return page
    .locator(`[data-testid='comp-row'][data-comp-key="${key}"]`)
    .or(page.locator(`[data-comp-key="${key}"]`))
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
    // Scoped to comps that HAVE a $/sqft: whether ALL should also re-include
    // the no-sqft comps (which default to weight 0, F5 epic edge) is an open
    // question escalated to the PM, and this spec deliberately does not
    // pre-judge it.
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

  test("AC2b: ALL/NONE leave filtered-out comps' weights untouched", async ({ page }) => {
    // ---------------------------------------------------------------------
    // STRICT XFAIL — the pattern F4-S2's QA introduced on this project.
    //
    // This clause of the AC ("ALL/NONE operate on currently VISIBLE
    // (unfiltered) comps only") cannot be honestly driven today: the filter
    // ENGINE exists server-side (`pipeline/membership.py`, F0-S2 scope) but
    // there is no UI that can turn a filter on — that is F7-S1/F7-S2, both
    // BLOCKED on this very story. With no filter reachable from the browser,
    // nothing is ever invisible, and a test written now would pass by being
    // vacuous.
    //
    // `test.fail()` rather than `test.skip()` on purpose: a skip decays
    // silently and would still be sitting there in six weeks. This fails
    // LOUDLY the day the gap closes — the moment a filter control renders and
    // this test passes, Playwright reports "expected to fail, but passed",
    // which forces whoever lands F7-S2 to delete this marker and confirm the
    // assertion for real. The opposite decay curve from a note.
    // ---------------------------------------------------------------------
    test.fail(
      true,
      "F7-S1/F7-S2 (filter engine + filter strip) are not built, so no comp can be made " +
        "invisible from the browser. Remove this marker when a filter control exists."
    );

    const initial = await gotoResults(page);
    const filterControl = page
      .locator("[data-testid='filter-strip']")
      .or(page.getByRole("button", { name: /filter/i }))
      .or(page.locator("[data-testid='filter-max-distance']"));
    expect(
      await filterControl.count(),
      "no filter control is reachable — see the strict-xfail note above"
    ).toBeGreaterThan(0);

    // The assertion this test will make once a filter exists, spelled out so
    // the person removing the marker does not have to reinvent it:
    await filterControl.first().click();
    const filteredKeys: string[] = initial.comps.map((c: any) => c.key).slice(0, 1);
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
