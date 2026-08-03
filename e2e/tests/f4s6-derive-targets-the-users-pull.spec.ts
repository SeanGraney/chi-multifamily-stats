/**
 * F4-S6, the +1 AC (AC8) — **a derive may never target a pull the user did not
 * just create or explicitly open.** Layer 3 (Playwright), QA-authored RED.
 *
 * THE DEFECT THIS EXISTS TO CLOSE
 * --------------------------------
 * On `main` today the search flow and the Results flow are not connected:
 *
 *   frontend/src/views/Results.tsx:25   const PULL_REF = "ws1-real";
 *   frontend/src/views/Results.tsx:65   useDerive({ pull_ref: PULL_REF, ... })
 *   frontend/src/views/Home.tsx:77      renders `result.pull_ref` as TEXT, then drops it
 *   frontend/src/App.tsx                a four-way useState tab switch, no pull identity in it
 *
 * So the owner can spend real API calls on a real address, open Results, and be
 * shown the analysis of a **fixture** — with nothing on screen saying the two
 * are unrelated. Every panel is internally consistent and every number is
 * correctly computed; they are just computed from evidence nobody asked for.
 * That is the confident-output-the-evidence-does-not-support failure
 * `NORTH_STAR.md` exists to prevent, at the highest level this product has, and
 * it is F4 flow step 4 ("the system lands the user on Results") verbatim.
 *
 * WHY "MAKE `PULL_REF` A PROP" DOES NOT SATISFY THIS FILE
 * -------------------------------------------------------
 * Threading the ref through as a prop with a default, or holding a last-known
 * ref that survives into a fresh session, still lets Results paint numbers for
 * a pull the user did not open. So the tests below are written against the
 * property rather than the plumbing:
 *
 *   AC8a  numbers on screen came from the ref THIS search produced
 *   AC8b  with no pull open, Results derives NOTHING and says so
 *   AC8c  a second search retargets — the first search's ref is not stale-served
 *   AC8d  no pull-ref literal exists in the frontend source at all (structural)
 *
 * AC8b is the load-bearing one. AC8a and AC8c can both be satisfied by a
 * default that happens to be overwritten in time; only AC8b fails when a
 * default ref exists at all.
 *
 * THE OTHER DOOR (F1-S1, QUEUE row 38)
 * -------------------------------------
 * "Open recent → full state restored" is the second entrance to this same view,
 * and whichever story lands second inherits the seam. This file deliberately
 * does NOT assert F1-S1's recents row — `GET /api/workspaces` and its table are
 * that story's deliverable, and a red test for them here would block F4-S6 on
 * F1-S1. What it does instead is forbid the shape that only one door can use:
 * AC8b + AC8d together mean the ref must arrive from a *response at runtime*
 * and Results must own no fallback, which is exactly the mechanism a recents
 * row can also feed. See the handoff note to the PM.
 *
 * ZERO LIVE RENTCAST CALLS (D17, WORKFLOW.md §6): no server is started, no key
 * exists, the SPA is served from an ephemeral port, and every `/api/**` request
 * is fulfilled from `e2e/fixtures/f4s6/`. Unstubbed calls answer 503.
 */
import { test, expect, type Page } from "@playwright/test";
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import path from "node:path";
import { buildUi, fixture, serveUi, stubApi, type ApiCall, type StaticUi } from "./support/static-ui";
import { REPO_ROOT } from "./support/local-server";

let ui: StaticUi | null = null;
let setupFailure: string | null = null;

const PLAN = fixture("plan.json");
const SEARCH_COMPLETE = fixture("search-complete.json");
const SEARCH_ZERO = fixture("search-complete-zero.json");
const DERIVE_COMPLETE = fixture("derive-complete.json");
const DERIVE_EMPTY = fixture("derive-empty.json");

/** The ref hardcoded on `main`. Named so the failure message can point at it. */
const FIXTURE_REF = "ws1-real";

const SEARCH_A = {
  address: "3651 S Wood St, Chicago, IL 60609",
  bedrooms: "3",
  bathrooms: "",
  sqft: "1000",
  radius: "0.5",
  windowStart: "06-15",
  windowEnd: "06-30",
  yearsBack: "2",
};

const SEARCH_B = { ...SEARCH_A, address: "9990 S Nowhere Ave, Chicago, IL 60609", radius: "0.25" };

test.beforeAll(async () => {
  try {
    buildUi();
    ui = await serveUi();
  } catch (err: any) {
    setupFailure = `static-UI harness setup failed: ${err?.stderr?.toString?.() ?? err}`;
  }
});

test.afterAll(async () => {
  await ui?.close();
  ui = null;
});

function baseUrl(): string {
  if (!ui) throw new Error(setupFailure ?? "static UI harness not started");
  return ui.baseUrl;
}

async function submitSearch(page: Page, values: Record<string, string>): Promise<void> {
  await page.getByTestId("nav-home").click();
  await page.getByTestId("nav-new-search").click();
  await expect(page.getByTestId("search-form")).toBeVisible();
  const ids: Record<string, string> = {
    address: "search-address",
    bedrooms: "search-bedrooms",
    bathrooms: "search-bathrooms",
    sqft: "search-sqft",
    radius: "search-radius",
    windowStart: "search-window-start",
    windowEnd: "search-window-end",
    yearsBack: "search-years-back",
  };
  for (const [field, value] of Object.entries(values)) {
    if (ids[field]) await page.getByTestId(ids[field]).fill(value);
  }
  await expect(page.getByTestId("search-preview")).toBeVisible({ timeout: 10_000 });
  await page.getByTestId("search-submit").click();
}

/** Every `pull_ref` this browser has asked to derive, in order. */
function derivedRefs(calls: ApiCall[]): string[] {
  return calls
    .filter((call) => call.path.endsWith("/api/derive"))
    .map((call) => call.body?.pull_ref);
}

/** Reach the derived-state surface, however this build gets there. */
async function goToResults(page: Page, calls: ApiCall[]): Promise<void> {
  for (let waited = 0; waited < 2000 && derivedRefs(calls).length === 0; waited += 250) {
    await page.waitForTimeout(250);
  }
  if (derivedRefs(calls).length === 0) await page.getByTestId("nav-results").click();
  await page.waitForTimeout(1500);
}

// ===========================================================================
// harness — a failing precondition, never a skip (WORKFLOW.md §2)
// ===========================================================================

test("the static-UI harness is available", async () => {
  expect(setupFailure, setupFailure ?? "").toBeNull();
  expect(ui, "the static UI server did not start").not.toBeNull();
});

// ===========================================================================
// AC8a — the numbers on screen belong to the search that produced them
// ===========================================================================

test("AC8a: after a search, the derive targets that search's pull_ref", async ({ page }) => {
  const calls = await stubApi(page, {
    plan: PLAN,
    search: SEARCH_COMPLETE,
    derive: DERIVE_COMPLETE,
    deriveOnlyRef: SEARCH_COMPLETE.pull_ref,
  });
  await page.goto(baseUrl());
  await submitSearch(page, SEARCH_A);
  await goToResults(page, calls);

  const refs = derivedRefs(calls);
  expect(
    refs.length,
    "the browser never issued POST /api/derive, so the search produced a pull the user " +
      "cannot see. F4 flow step 4: the system lands the user on Results.",
  ).toBeGreaterThan(0);

  expect(
    [...new Set(refs)],
    `this build derived ${JSON.stringify([...new Set(refs)])}; the search returned ` +
      `${JSON.stringify(SEARCH_COMPLETE.pull_ref)}.` +
      (refs.includes(FIXTURE_REF)
        ? ` It derived the hardcoded fixture ref ${JSON.stringify(FIXTURE_REF)} ` +
          "(Results.tsx:25) — the user is being shown the analysis of a fixture after " +
          "spending real calls on a real address."
        : " A derive must never target a pull the user did not just create or explicitly open."),
  ).toEqual([SEARCH_COMPLETE.pull_ref]);

  // And the evidence actually rendered, so "targets the right ref" is not
  // satisfied by a view that targets it and shows nothing.
  await expect(page.getByTestId("comp-row").first()).toBeVisible({ timeout: 10_000 });
  expect(await page.getByTestId("comp-row").count()).toBe(DERIVE_COMPLETE.comps.length);
});

// ===========================================================================
// AC8b — THE load-bearing one: no pull open ⇒ no numbers, and no derive
// ===========================================================================

test("AC8b: with no pull open, Results derives nothing and says why", async ({ page }) => {
  // Note what is NOT stubbed: nothing. `deriveOnlyRef` is set to a ref no
  // search produced, so ANY derive this build attempts is a 404 — and the
  // assertion below is that it never attempts one at all.
  const calls = await stubApi(page, {
    plan: PLAN,
    search: SEARCH_COMPLETE,
    derive: DERIVE_COMPLETE,
    deriveOnlyRef: "no-pull-is-open-in-this-test",
  });
  await page.goto(baseUrl());

  // Straight to Results on a cold app. No search has been run in this session.
  await page.getByTestId("nav-results").click();
  await page.waitForTimeout(2500);

  const refs = derivedRefs(calls);
  expect(
    refs,
    "Results issued a derive with no pull open: " +
      JSON.stringify(refs) +
      (refs.includes(FIXTURE_REF)
        ? ". That is the hardcoded `PULL_REF = \"ws1-real\"` at Results.tsx:25."
        : ". A default or remembered ref on first paint is the same defect as a hardcoded " +
          "one — the user is shown an analysis they did not ask for and cannot attribute.") +
      " Threading the ref in as a prop does not fix this; the view must have NO ref to " +
      "fall back on.",
  ).toEqual([]);

  // Nothing derived means nothing derived-looking on screen.
  expect(
    await page.getByTestId("comp-row").count(),
    "comp rows are rendered with no pull open",
  ).toBe(0);
  await expect(
    page.locator("body"),
    "with no pull open Results shows an anchor. An anchor is a claim about a market, and " +
      "there is no evidence behind this one.",
  ).not.toContainText(/Anchor:/);

  // The honest state is not a blank screen either — it says what to do.
  const body = (await page.locator("body").innerText()).toLowerCase();
  expect(
    /search|no pull|no workspace|nothing open|start/.test(body),
    "with no pull open Results renders neither numbers nor an explanation. Spec §7: say " +
      `what is wrong and what to do. Saw: ${body.slice(0, 300)}`,
  ).toBe(true);
});

// ===========================================================================
// AC8c — a second search retargets; the first is not stale-served
// ===========================================================================

test("AC8c: a second search retargets the derive, leaving no stale ref", async ({ page }) => {
  // Both refs are derivable here, precisely so that serving the STALE one is a
  // 200 rather than a 404 — the test has to be able to observe the wrong
  // answer, not be rescued from it by the stub.
  const byRef: Record<string, unknown> = {
    [SEARCH_COMPLETE.pull_ref]: DERIVE_COMPLETE,
    [SEARCH_ZERO.pull_ref]: DERIVE_EMPTY,
  };
  const calls = await stubApi(page, {
    plan: PLAN,
    search: (body: any) => (body?.radius === 0.25 ? SEARCH_ZERO : SEARCH_COMPLETE),
    derive: (body: any) => byRef[body?.pull_ref] ?? DERIVE_COMPLETE,
  });
  await page.goto(baseUrl());

  await submitSearch(page, SEARCH_A);
  await goToResults(page, calls);
  expect(
    derivedRefs(calls).at(-1),
    "the first search did not reach the derived-state surface at all",
  ).toBe(SEARCH_COMPLETE.pull_ref);

  await submitSearch(page, SEARCH_B);
  await goToResults(page, calls);

  expect(
    derivedRefs(calls).at(-1),
    "after a second search the view is still deriving the FIRST search's pull. The panels " +
      "are showing one address's evidence under another address's search — and both " +
      "searches cost real calls.",
  ).toBe(SEARCH_ZERO.pull_ref);

  // `SEARCH_ZERO` is the zero-comp pull, so the panels must have followed the
  // ref rather than kept the first payload standing.
  expect(
    await page.getByTestId("comp-row").count(),
    "the second search's pull holds zero comps, yet comp rows from the first search are " +
      "still on screen. A stale panel after a retarget is worse than a slow one: it is " +
      "indistinguishable from a result.",
  ).toBe(0);
});

// ===========================================================================
// AC8d — structural: no pull-ref literal in the frontend at all (no browser)
// ===========================================================================

test("AC8d: no hardcoded pull ref exists anywhere in frontend/src", async () => {
  const srcDir = path.join(REPO_ROOT, "frontend", "src");
  expect(existsSync(srcDir), `frontend/src not found at ${srcDir}`).toBe(true);

  const files: string[] = [];
  const walk = (dir: string) => {
    for (const entry of readdirSync(dir)) {
      const full = path.join(dir, entry);
      if (statSync(full).isDirectory()) walk(full);
      else if (/\.(ts|tsx)$/.test(entry) && !entry.endsWith(".d.ts")) files.push(full);
    }
  };
  walk(srcDir);
  expect(files.length, "walked frontend/src and found no TypeScript files").toBeGreaterThan(0);

  /**
   * Two shapes, both of which put a pull identity in the source:
   *  - the known fixture refs (`ws1-real`, `ws1-perf`, ...)
   *  - a 64-char hex string, which is what `pull_ref_for` actually mints
   *    (SHA256 of the canonicalized search params, F3-S1)
   * Comments are stripped first: this file and the developer's own docstrings
   * are allowed to *name* the defect they forbid.
   */
  const offenders: string[] = [];
  for (const file of files) {
    const source = readFileSync(file, "utf-8")
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .replace(/(^|[^:])\/\/.*$/gm, "$1");
    for (const [index, line] of source.split(/\r?\n/).entries()) {
      if (/["'`]ws1-[a-z0-9-]+["'`]/.test(line) || /["'`][0-9a-f]{64}["'`]/.test(line)) {
        offenders.push(`${path.relative(REPO_ROOT, file)}:${index + 1}: ${line.trim()}`);
      }
    }
  }

  expect(
    offenders,
    "a pull ref is written into the frontend source:\n" +
      offenders.join("\n") +
      "\n\nA pull ref addresses evidence the user paid for, so it may only ever arrive from " +
      "a response at runtime — `SearchResult.pull_ref`, or the workspace a recents row " +
      "opens (F1-S1). A literal here is a default the view can silently fall back to, which " +
      "is the whole defect: it makes 'the analysis of the pull you just ran' and 'the " +
      "analysis of whatever was compiled in' render identically.",
  ).toEqual([]);
});
