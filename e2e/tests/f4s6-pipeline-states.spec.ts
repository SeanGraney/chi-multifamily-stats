/**
 * F4 · Pull & Clean — F4-S6's three user-facing states. Layer 3 (Playwright).
 * QA-authored, written RED before any implementation exists (AGENT_QA.md
 * protocol).
 *
 * WHAT IS HERE, AND WHY IT IS BROWSER-GENUINE
 * --------------------------------------------
 * Every test below needs a component to mount, a real click, or a request
 * that a click produced:
 *
 *  * progress exists only *while* a request is in flight — there is no
 *    payload to assert it from;
 *  * "renders the empty state INSTEAD of the panels" is render exclusivity,
 *    the same shape as F11's guard-state-vs-curve AC;
 *  * "a widen shortcut actually widens" is only observable from the request
 *    the click produces;
 *  * "names the gap precisely" is words on a screen, assembled from two
 *    payload fields.
 *
 * WHAT IS DELIBERATELY NOT HERE
 * ------------------------------
 * The *values*. Whether `meta.partial_pull` is populated for a pull that is
 * one window short, whether `calls_to_complete` counts only owed windows,
 * whether a zero-comp pull derives at all — all Layer 2, in
 * `backend/tests/api/test_f4s6_partial_pull_and_empty_state.py`, exhaustively.
 * This file asserts the wiring once per state (AGENT_QA.md's split rule).
 *
 * THE HARNESS FAILS LOUDLY, IT DOES NOT SKIP QUIETLY (WORKFLOW.md §2): "the
 * harness is available" is a real test. `13 passed, 10 skipped, exit 0` is
 * this repo's documented false green.
 *
 * NO PORT 8000, NO `.venv`: the SPA is served from an ephemeral port and every
 * `/api/**` request is fulfilled from `e2e/fixtures/f4s6/` — payloads
 * generated from the real pipeline and re-validated against the Pydantic
 * response models by `test_f4s6_stub_payloads_match_the_wire.py` on every
 * pytest run. See `support/static-ui.ts` for the full rationale.
 *
 * MARKUP CONTRACT (QA-PROPOSED — the developer may satisfy any fallback
 * instead; every locator tries a `data-testid` first, then an accessible
 * role, then a text pattern, the technique `f2-s1-search-form.spec.ts` and
 * `ws1-results-view.spec.ts` already use):
 *
 *   pipeline-progress · empty-state · empty-state-constraint ·
 *   widen-radius / widen-window / widen-types (any one is enough) ·
 *   partial-pull (or F2-S1's existing `pull-gap`) · resume-pull
 *
 * ZERO LIVE RENTCAST CALLS (D17, WORKFLOW.md §6): no server is started, no
 * key exists, and an unstubbed `/api/**` request is answered 503 rather than
 * being allowed to reach anything.
 */
import { test, expect, type Locator, type Page } from "@playwright/test";
import { buildUi, fixture, serveUi, stubApi, type ApiCall, type StaticUi } from "./support/static-ui";

/**
 * NO `test.describe.configure({ mode: "serial" })`. It was here and it is
 * removed deliberately (PM ruling, F4-S6): serial mode bails the rest of the
 * describe after the first failure, so eight independent red tests report as
 * `1 failed, 7 did not run` — and "did not run" is the same family as the
 * silent skip this repo has been burned by four times. The suite is already
 * sequential (`playwright.config.ts`: `workers: 1, fullyParallel: false`), so
 * serial mode bought nothing but the loss of signal. Every test below arranges
 * its own stub and its own navigation; none depends on another's state.
 */

let ui: StaticUi | null = null;
let setupFailure: string | null = null;

const PLAN = fixture("plan.json");
const PLAN_UNREAL = fixture("plan-unreal-years.json");
const SEARCH_COMPLETE = fixture("search-complete.json");
const SEARCH_ZERO = fixture("search-complete-zero.json");
const SEARCH_PARTIAL = fixture("search-partial.json");
const DERIVE_COMPLETE = fixture("derive-complete.json");
const DERIVE_PARTIAL = fixture("derive-partial.json");
const DERIVE_DIVERGENT = fixture("derive-partial-divergent.json");
const DERIVE_EMPTY = fixture("derive-empty.json");

/** The narrow search the zero-comp cases submit — every value is echoed in
 *  the assertions, so the empty state naming "0.25" or "06-15" is naming
 *  something real rather than matching a stray digit. */
const NARROW = {
  address: "9990 S Nowhere Ave, Chicago, IL 60609",
  bedrooms: "3",
  bathrooms: "",
  sqft: "1000",
  radius: "0.25",
  windowStart: "06-15",
  windowEnd: "06-30",
  yearsBack: "2",
};

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

/** First locator that resolves to something visible, else null. */
async function firstVisible(candidates: Locator[]): Promise<Locator | null> {
  for (const candidate of candidates) {
    if (await candidate.first().isVisible().catch(() => false)) return candidate.first();
  }
  return null;
}

function byTestIdOrText(page: Page, testIds: string[], pattern: RegExp): Locator[] {
  return [
    ...testIds.map((id) => page.getByTestId(id)),
    page.locator("body").getByText(pattern),
  ];
}

/** Fill and submit the search form. Leaves the page wherever the app takes it. */
async function submitSearch(page: Page, values: Record<string, string>): Promise<void> {
  await page.getByTestId("nav-home").click();
  await page.getByTestId("nav-new-search").click();
  await expect(page.getByTestId("search-form")).toBeVisible();
  for (const [field, value] of Object.entries(values)) {
    const testId = {
      address: "search-address",
      bedrooms: "search-bedrooms",
      bathrooms: "search-bathrooms",
      sqft: "search-sqft",
      radius: "search-radius",
      windowStart: "search-window-start",
      windowEnd: "search-window-end",
      yearsBack: "search-years-back",
    }[field];
    if (!testId) continue;
    await page.getByTestId(testId).fill(value);
  }
  // The preview is debounced (400ms) — waiting for it means the plan the
  // progress surface reads its years from has actually arrived.
  await expect(page.getByTestId("search-preview")).toBeVisible({ timeout: 10_000 });
  await page.getByTestId("search-submit").click();
}

/**
 * Reach the surface that renders a `DerivedState`. The developer may land the
 * user there straight off a search (F4 flow step 4) or leave Results reachable
 * from the top bar; either satisfies this story, so the helper accepts both
 * rather than pinning a navigation decision the AC does not make.
 *
 * `expectedRef` is not optional, deliberately. "A derive happened" is not the
 * precondition these tests need — "a derive *of the pull this search produced*
 * happened" is. The weaker version certifies the broken seam as working (see
 * `ApiStub.deriveOnlyRef`), so the ref travels with every call site.
 */
async function openWorkspace(page: Page, calls: ApiCall[], expectedRef: string): Promise<void> {
  const derives = () => calls.filter((call) => call.path.endsWith("/api/derive"));
  for (let waited = 0; waited < 3000 && derives().length === 0; waited += 250) {
    await page.waitForTimeout(250);
  }
  if (derives().length === 0) await page.getByTestId("nav-results").click();
  await expect
    .poll(() => derives().length > 0, {
      timeout: 10_000,
      message: "no POST /api/derive was ever issued, so no derived state is on screen",
    })
    .toBe(true);

  const refs = [...new Set(derives().map((call) => call.body?.pull_ref))];
  expect(
    refs,
    `this view derived ${JSON.stringify(refs)} but the only pull the user created in this ` +
      `test is ${JSON.stringify(expectedRef)}. Every number now on screen was computed from ` +
      "evidence the user never asked for (F4 flow step 4; PM's +1 AC on this story).",
  ).toEqual([expectedRef]);
}

// ===========================================================================
// harness
// ===========================================================================

test("the static-UI harness is available", async () => {
  expect(setupFailure, setupFailure ?? "").toBeNull();
  expect(ui, "the static UI server did not start").not.toBeNull();
});

// ===========================================================================
// AC1 — per-year pull → stitch → derive progress
// ===========================================================================

test("AC1a: a pull in flight shows pipeline progress naming its phases", async ({ page }) => {
  const calls = await stubApi(page, {
    plan: PLAN,
    // Held open: "progress" is only a claim about what the user sees while
    // they are waiting, so the wait has to be real.
    search: SEARCH_COMPLETE,
    searchDelayMs: 4000,
    derive: DERIVE_COMPLETE,
    deriveOnlyRef: SEARCH_COMPLETE.pull_ref,
  });
  await page.goto(baseUrl());
  await submitSearch(page, { ...NARROW, address: "3651 S Wood St, Chicago, IL 60609" });

  const progressLocators = () =>
    byTestIdOrText(page, ["pipeline-progress"], /pull|stitch|deriv/i);

  await expect
    .poll(async () => (await firstVisible(progressLocators())) !== null, {
      timeout: 8_000,
      message:
        "no progress surface appeared while POST /api/search was in flight. F4 step 2 " +
        "asks for a per-year pull → stitching → deriving indicator; the user is " +
        "otherwise looking at a form with a disabled button for the length of a real pull.",
    })
    .toBe(true);

  const progress = await firstVisible(progressLocators());
  const text = ((await progress?.innerText()) ?? "").toLowerCase();
  for (const phase of [/pull/, /stitch/, /deriv/]) {
    expect(
      phase.test(text),
      `the progress surface never names the ${phase} phase. The story's unit of ` +
        `progress is "per-year pull → stitch → derive", not a spinner. Saw: ${text}`,
    ).toBe(true);
  }
  expect(calls.some((c) => c.path.endsWith("/api/search"))).toBe(true);
});

test("AC1b: the years in the progress surface come from the plan, not from a clock", async ({
  page,
}) => {
  // 1999/1998 are not producible by `new Date().getFullYear() - i`, so this
  // fails the moment the cohort years are reconstructed in TypeScript (D5:
  // the planner owns the New-Year wrap and the Feb-29 clamp, and a second
  // implementation disagrees exactly on the dates nobody checks).
  await stubApi(page, {
    plan: PLAN_UNREAL,
    search: SEARCH_COMPLETE,
    searchDelayMs: 4000,
    derive: DERIVE_COMPLETE,
    deriveOnlyRef: SEARCH_COMPLETE.pull_ref,
  });
  await page.goto(baseUrl());
  await submitSearch(page, { ...NARROW, address: "3651 S Wood St, Chicago, IL 60609" });

  // Same fallback chain as AC1a, deliberately: this file's markup contract is
  // QA-PROPOSED, so hard-pinning `pipeline-progress` here while AC1a accepts a
  // text match would fail a developer who satisfied AC1a by another name.
  await expect
    .poll(async () => (await firstVisible(byTestIdOrText(page, ["pipeline-progress"], /pull|stitch|deriv/i))) !== null, {
      timeout: 8_000,
      message: "no progress surface appeared while POST /api/search was in flight",
    })
    .toBe(true);
  const progress = (await firstVisible(
    byTestIdOrText(page, ["pipeline-progress"], /pull|stitch|deriv/i),
  ))!;
  const text = await progress.innerText();
  for (const year of ["1999", "1998"]) {
    expect(
      text,
      `the progress surface does not name the cohort year ${year} that the server planned. ` +
        "Per-year progress that does not say which year is not per-year progress — and the " +
        "years are the plan's, never the browser's arithmetic (D5: the planner owns the " +
        `New-Year wrap and the Feb-29 clamp). Saw: ${text}`,
    ).toContain(year);
  }
});

// ===========================================================================
// AC2 / AC3 — the 0-comp empty state
// ===========================================================================

test("AC2a: a search that found nothing renders an empty state, not zeroed panels", async ({
  page,
}) => {
  const calls = await stubApi(page, {
    plan: PLAN,
    search: SEARCH_ZERO,
    derive: DERIVE_EMPTY,
    deriveOnlyRef: SEARCH_ZERO.pull_ref,
  });
  await page.goto(baseUrl());
  await submitSearch(page, NARROW);
  await openWorkspace(page, calls, SEARCH_ZERO.pull_ref);

  const empty = await firstVisible(
    byTestIdOrText(page, ["empty-state"], /no comps|0 comps|nothing (came )?back|found nothing/i),
  );
  expect(
    empty,
    "a pull holding zero comps rendered no empty state. Spec §7 and the F4 epic edge both " +
      "require one: a user left in front of an empty table cannot tell whether the tool " +
      "broke or the market is thin.",
  ).not.toBeNull();

  // Render exclusivity, the same rule as guard-state-vs-curve: an empty
  // bucket table full of dashes next to an empty state is two answers to one
  // question.
  const bucketRows = page.locator("table tbody tr");
  const bucketCount = await bucketRows.count().catch(() => 0);
  expect(
    bucketCount,
    "the three-bucket table is still rendered underneath the empty state. With no comps " +
      "every cell is a dash, which reads as data that came back empty rather than as a " +
      "search that matched nothing.",
  ).toBe(0);
});

test("AC2b: the empty state names the constraints that produced it", async ({ page }) => {
  const calls = await stubApi(page, {
    plan: PLAN,
    search: SEARCH_ZERO,
    derive: DERIVE_EMPTY,
    deriveOnlyRef: SEARCH_ZERO.pull_ref,
  });
  await page.goto(baseUrl());
  await submitSearch(page, NARROW);
  await openWorkspace(page, calls, SEARCH_ZERO.pull_ref);

  const empty = await firstVisible(
    byTestIdOrText(
      page,
      ["empty-state-constraint", "empty-state"],
      /no comps|0 comps|nothing (came )?back|found nothing/i,
    ),
  );
  expect(empty, "no empty state to read a constraint off").not.toBeNull();
  const text = (await empty!.innerText()).toLowerCase();

  // "Names the binding constraint" — the epic's own words. A message that
  // says only "no comps found" names nothing: the whole value of this state
  // is telling the user WHICH knob to turn, and the knobs are the search's
  // own values, echoed back.
  const named = [
    { what: "the radius", hit: text.includes("0.25") },
    { what: "the date window", hit: text.includes("06-15") || text.includes("06-30") },
    { what: "the property types", hit: /multi-family|apartment|townhouse/.test(text) },
    { what: "the beds filter", hit: /\b3\b/.test(text) && /bed/.test(text) },
  ].filter((entry) => entry.hit);

  expect(
    named.map((n) => n.what),
    `the empty state names none of the constraints that produced it. Saw: ${text}\n` +
      "The F4 edge is 'empty state naming the binding constraint', not 'empty state'. " +
      "Note for the PM: WHICH constraint was binding needs a backend datum that does not " +
      "exist yet (see test_f4s6_partial_pull_and_empty_state.py::" +
      "test_an_empty_result_says_which_constraint_emptied_it) — this assertion is the " +
      "affordable half, the constraints actually applied, echoed with their values.",
  ).not.toHaveLength(0);
});

test("AC3: a widen shortcut actually widens the search", async ({ page }) => {
  const calls = await stubApi(page, {
    plan: PLAN,
    search: SEARCH_ZERO,
    derive: DERIVE_EMPTY,
    deriveOnlyRef: SEARCH_ZERO.pull_ref,
  });
  await page.goto(baseUrl());
  await submitSearch(page, NARROW);
  await openWorkspace(page, calls, SEARCH_ZERO.pull_ref);

  const widen = await firstVisible([
    page.getByTestId("widen-radius"),
    page.getByTestId("widen-window"),
    page.getByTestId("widen-types"),
    page.getByRole("button", { name: /widen|broaden|wider/i }),
  ]);
  expect(
    widen,
    "the empty state offers no widen shortcut. The F4 edge asks for one because the empty " +
      "state's only useful action is changing a constraint.",
  ).not.toBeNull();

  const before = calls.length;
  await widen!.click();

  await expect
    .poll(() => calls.length > before, {
      timeout: 10_000,
      message:
        "clicking the widen shortcut produced no request at all — neither a re-plan nor a " +
        "re-search. A shortcut that widens nothing is a dead end with a button on it.",
    })
    .toBe(true);

  const issued = calls
    .slice(before)
    .filter((call) => call.path.includes("/api/search") && call.body);
  const wider = issued.filter((call) => {
    const radius = Number(call.body.radius);
    const types: string[] = call.body.property_types ?? [];
    const windowWidened =
      call.body.window_start !== NARROW.windowStart || call.body.window_end !== NARROW.windowEnd;
    const yearsWidened = Number(call.body.years_back) > Number(NARROW.yearsBack);
    return (
      radius > Number(NARROW.radius) || types.length > 3 || windowWidened || yearsWidened
    );
  });

  // The form may be re-opened with widened values instead of re-submitting;
  // that is a legitimate implementation and is checked here too.
  const radiusField = page.getByTestId("search-radius");
  const formWidened =
    (await radiusField.isVisible().catch(() => false)) &&
    Number(await radiusField.inputValue()) > Number(NARROW.radius);

  expect(
    wider.length > 0 || formWidened,
    "the widen shortcut re-issued the SAME search: " +
      JSON.stringify(issued.map((c) => c.body)) +
      `. Nothing was widened relative to radius=${NARROW.radius}, window ` +
      `${NARROW.windowStart}..${NARROW.windowEnd}, years_back=${NARROW.yearsBack} — so the ` +
      "second search costs calls and returns the same nothing.",
  ).toBe(true);
});

// ===========================================================================
// AC4 / AC5 / AC7 — the partial-pull state
// ===========================================================================

test("AC4: a partial pull still renders its evidence — usable, not blocked", async ({ page }) => {
  const calls = await stubApi(page, {
    plan: PLAN,
    search: SEARCH_PARTIAL,
    derive: DERIVE_PARTIAL,
    deriveOnlyRef: SEARCH_PARTIAL.pull_ref,
  });
  await page.goto(baseUrl());
  await submitSearch(page, { ...NARROW, address: "3651 S Wood St, Chicago, IL 60609" });
  await openWorkspace(page, calls, SEARCH_PARTIAL.pull_ref);

  // §5a: "an incomplete first pull is usable, with the gap named loudly."
  // With a 50-call cap, refusing to show anything until the set is perfect
  // strands the user.
  await expect(
    page.getByTestId("comp-row").first(),
    "a partial pull rendered no comps at all. The gap must be named, not used as a reason " +
      "to withhold the evidence that did arrive (ARCHITECTURE §5a).",
  ).toBeVisible({ timeout: 10_000 });
  expect(await page.getByTestId("comp-row").count()).toBe(DERIVE_PARTIAL.comps.length);
});

test("AC5: the gap is named precisely — the window AND the call count", async ({ page }) => {
  // EXPECTED RED — PM deferral, not a defect. Marked by the PM (not by QA or the
  // developer) because it encodes a PM ruling; see queue rows 12a and 13j.
  // The `calls_to_complete` RENDERING is deliberately not built: it is a cost
  // promise made to the owner, and the quote-vs-spend defect behind it (row 13j)
  // is still open. Rendering a call count we KNOW can be wrong is worse than
  // rendering none. This is strict, so it flips to a hard red — "expected to
  // fail, but passed" — the day that rendering lands, which is exactly when
  // someone must come back and read this note.
  test.fail(true, "calls_to_complete rendering deferred behind row 13j (PM ruling)");
  const calls = await stubApi(page, {
    plan: PLAN,
    search: SEARCH_PARTIAL,
    derive: DERIVE_PARTIAL,
    deriveOnlyRef: SEARCH_PARTIAL.pull_ref,
  });
  await page.goto(baseUrl());
  await submitSearch(page, { ...NARROW, address: "3651 S Wood St, Chicago, IL 60609" });
  await openWorkspace(page, calls, SEARCH_PARTIAL.pull_ref);

  const banner = await firstVisible(
    byTestIdOrText(page, ["partial-pull", "pull-gap"], /missing|incomplete|to complete/i),
  );
  expect(
    banner,
    "a workspace whose manifest is short a window rendered no gap banner. 'A missing cohort " +
      "skews every downstream number and must never be silently absent' — a partial pull " +
      "that looks identical to a whole one is the exact failure this story exists to stop.",
  ).not.toBeNull();

  const text = await banner!.innerText();
  // The story's own copy: "2025 inactive missing · 1 call to complete". The
  // label is the manifest's string, rendered verbatim (client/pull.py::_label)
  // — a view that rebuilt "2019 Inactive" from a year plus a status would be
  // a second implementation of a naming convention it does not own.
  expect(
    text,
    `the banner does not name the missing window. Saw: ${text}`,
  ).toContain(DERIVE_PARTIAL.meta.partial_pull.missing[0]);
  expect(
    text.toLowerCase(),
    `the banner does not say how many calls would complete the pull. Saw: ${text}`,
  ).toMatch(/1 call\b/);
  expect(
    text.toLowerCase(),
    `"1 calls" — the singular case is the one the story's own example copy uses. Saw: ${text}`,
  ).not.toMatch(/1 calls/);
});

test("AC5/AC7: the call count is the server's number, not the length of the missing list", async ({
  page,
}) => {
  // EXPECTED RED — PM deferral, not a defect. Marked by the PM; see rows 12a/13j.
  // Same deferral as the test above. ⚠ Note this test defends a DIFFERENT error
  // than the one deferred for: it guards against the VIEW counting
  // `missing.length` in TypeScript, while the deferral is about the SERVER's
  // number not yet being trustworthy. Both are real, and this assertion must be
  // restored — not deleted — when row 13j lands.
  test.fail(true, "calls_to_complete rendering deferred behind row 13j (PM ruling)");
  // `missing` has 3 entries and `calls_to_complete` is 1 — a real manifest
  // state (a window holding a 2xx the parser cannot use is missing and not
  // owed; storage/cache.py::QueryStatus.owed), pinned at Layer 2 by
  // test_the_call_count_is_not_the_length_of_the_missing_list. A view that
  // counted `missing.length` in TypeScript would quote the user 3 calls for a
  // 1-call finish — D5, in the place where the mistake costs money.
  const calls = await stubApi(page, {
    plan: PLAN,
    search: SEARCH_PARTIAL,
    derive: DERIVE_DIVERGENT,
    deriveOnlyRef: SEARCH_PARTIAL.pull_ref,
  });
  await page.goto(baseUrl());
  await submitSearch(page, { ...NARROW, address: "3651 S Wood St, Chicago, IL 60609" });
  await openWorkspace(page, calls, SEARCH_PARTIAL.pull_ref);

  const banner = await firstVisible(
    byTestIdOrText(page, ["partial-pull", "pull-gap"], /missing|incomplete|to complete/i),
  );
  expect(banner, "no gap banner to read the call count off").not.toBeNull();
  const text = await banner!.innerText();

  for (const label of DERIVE_DIVERGENT.meta.partial_pull.missing) {
    expect(text, `the banner omits the missing window ${label}. Saw: ${text}`).toContain(label);
  }
  expect(
    text.toLowerCase(),
    "the banner quotes a call count the server did not send. calls_to_complete is 1 " +
      `(three windows are missing, only one is owed). Saw: ${text}`,
  ).toMatch(/\b1 call\b/);
  expect(
    text,
    "the banner shows 3 calls — the length of `missing`, computed in the view. The two " +
      "numbers are different facts and only the server knows the second one.",
  ).not.toMatch(/3 calls?\b/);
});

test("AC4/AC5: a partial pull that DID return comps never claims nothing came back", async ({
  page,
}) => {
  /**
   * A live defect on `main`, found by inspection and pinned here. `Home.tsx`'s
   * `SearchOutcome` branches on `result.complete`, and inside the incomplete
   * branch it prints BOTH the gap and:
   *
   *   "Nothing usable came back for these constraints. Try widening the
   *    radius, the date window, or the property types..."
   *
   * But `SearchResult` carries no comp count — `complete` is about which
   * *windows arrived*, not about whether the pull found anything. So a pull
   * that is one window short and returned twelve perfectly good comps tells
   * the user nothing came back, and sends them off widening a search that is
   * working. "Incomplete" and "empty" are different states with opposite
   * remedies (finish the pull for 1 call vs change a constraint and pay for a
   * new one), and conflating them is exactly the honesty failure this story
   * exists to close.
   *
   * The two states are separately reachable and separately asserted: this test
   * owns incomplete-with-evidence, AC2a/AC2b own complete-with-nothing.
   */
  const calls = await stubApi(page, {
    plan: PLAN,
    search: SEARCH_PARTIAL,
    derive: DERIVE_PARTIAL,
    deriveOnlyRef: SEARCH_PARTIAL.pull_ref,
  });
  await page.goto(baseUrl());
  await submitSearch(page, { ...NARROW, address: "3651 S Wood St, Chicago, IL 60609" });

  expect(
    DERIVE_PARTIAL.comps.length,
    "this test needs a partial pull that DID return comps",
  ).toBeGreaterThan(0);

  const body = await page.locator("body").innerText();
  expect(
    body,
    "a partial pull that returned " +
      `${DERIVE_PARTIAL.comps.length} comps told the user "nothing usable came back". The ` +
      "pull is short a window, not short of evidence — the remedy is the 1-call resume, not " +
      `widening a search that is working. Saw: ${body.slice(0, 400)}`,
  ).not.toMatch(/nothing usable came back/i);

  await openWorkspace(page, calls, SEARCH_PARTIAL.pull_ref);
  const after = await page.locator("body").innerText();
  expect(after, "the same claim appears on the derived-state surface").not.toMatch(
    /nothing usable came back/i,
  );
});

test("AC5 control: a complete pull shows no gap banner", async ({ page }) => {
  const calls = await stubApi(page, {
    plan: PLAN,
    search: SEARCH_COMPLETE,
    derive: DERIVE_COMPLETE,
    deriveOnlyRef: SEARCH_COMPLETE.pull_ref,
  });
  await page.goto(baseUrl());
  await submitSearch(page, { ...NARROW, address: "3651 S Wood St, Chicago, IL 60609" });
  await openWorkspace(page, calls, SEARCH_COMPLETE.pull_ref);

  await expect(page.getByTestId("comp-row").first()).toBeVisible({ timeout: 10_000 });
  const banner = await firstVisible(
    byTestIdOrText(page, ["partial-pull", "pull-gap"], /missing|to complete/i),
  );
  expect(
    banner,
    "a whole pull is showing a gap banner. A warning that is always on stops being read, " +
      "and `meta.partial_pull` is null precisely so this state is distinguishable.",
  ).toBeNull();
});

// ===========================================================================
// AC6 — the resume action
// ===========================================================================

test("AC6: the gap banner offers a resume that asks to finish, not to re-buy", async ({ page }) => {
  const calls = await stubApi(page, {
    plan: PLAN,
    search: SEARCH_PARTIAL,
    derive: DERIVE_PARTIAL,
    deriveOnlyRef: SEARCH_PARTIAL.pull_ref,
  });
  await page.goto(baseUrl());
  await submitSearch(page, { ...NARROW, address: "3651 S Wood St, Chicago, IL 60609" });
  await openWorkspace(page, calls, SEARCH_PARTIAL.pull_ref);

  const resume = await firstVisible([
    page.getByTestId("resume-pull"),
    page.getByRole("button", { name: /resume|complete (the )?pull|finish/i }),
  ]);
  expect(
    resume,
    "the partial-pull state offers no resume action. The story names one explicitly " +
      "(\"with a resume action\"), and without it the gap is a message the user cannot act on.",
  ).not.toBeNull();

  const before = calls.length;
  await resume!.click();
  await expect
    .poll(() => calls.slice(before).some((call) => call.path.endsWith("/api/search")), {
      timeout: 10_000,
      message: "the resume action issued no request",
    })
    .toBe(true);

  const request = calls.slice(before).find((call) => call.path.endsWith("/api/search"))!;
  expect(
    request.body?.force_refresh,
    "the resume button is wired to force_refresh. That is F3-S2's REFRESH — it re-fetches " +
      "every window (client/pull.py: `known = {} if force_refresh`), so a 1-call gap would " +
      "be billed as a full re-pull. §5a: a resume costs exactly the remainder.",
  ).not.toBe(true);
  expect(
    request.body?.resume,
    "the resume request carries nothing that asks the server to finish this pull. As of " +
      "today POST /api/search fetches only on a cache MISS or on force_refresh, so this " +
      "request is a no-op that returns the same partial outcome. See " +
      "test_a_resume_finishes_a_partial_pull_without_re_buying_what_is_on_disk — the wire " +
      "affordance is missing and is the PM's call to scope.",
  ).toBe(true);
});
