/**
 * F4-S6 — VERIFY-PHASE ADVERSARIAL PROBES. Layer 3 (Playwright), QA-authored
 * *after* the developer's code existed, deliberately and with the reason stated.
 *
 * WHY THIS FILE EXISTS AT ALL, GIVEN THE CHARTER SAYS "WRITE TESTS FIRST"
 * -----------------------------------------------------------------------
 * `f4s6-derive-targets-the-users-pull.spec.ts` was written blind, before any
 * implementation, and it holds. This file is the second half of the same job:
 * the PM's standing ruling on this story is that the seam "must NOT be closed by
 * making `PULL_REF` a prop", and that an implementation which admits the ref is
 * passed in **but still permits a default ref on first paint has not held the
 * line**. AC8a–AC8d prove the happy path and the cold-start path. They do not
 * prove that the property survives the four ways it can be got around *once you
 * know how it was built*, and "verify by test, not by reading" is the whole
 * instruction. So these are written against the shipped mechanism, on purpose,
 * and each one names the way in it is trying:
 *
 *   S1  the payload lies about which pull it is for   (server-side / race)
 *   S2  the ref is real but the pull is gone          (deleted cache entry)
 *   S3  the ref survives a reload                     (persistence)
 *   S4  the empty state names the WRONG constraint    (the untested branch)
 *
 * S1–S3 attack the seam. **S4 is not an attack on the seam — it is the half of
 * "names the binding constraint" that no other test in this story reaches.**
 * `EmptyState` has two branches and the whole point of putting F4-S4's
 * `dropped_outside_window` on the wire (this story's backend change) is that the
 * two say opposite things to the user: *widen the radius* vs *widen the date
 * window*. Every existing spec exercises the `dropped_outside_window == null`
 * branch only, so the branch the new wire datum was added FOR was, until this
 * file, asserted by nothing. A datum that is serialised and never changes what a
 * user is told is the "computed, serialised, rendered by nothing" shape this
 * whole story exists to close — reproducing it inside the fix would be a poor
 * joke.
 *
 * WHAT IS DELIBERATELY *NOT* HERE: the subject seam. `Results.tsx` still holds a
 * hardcoded `SUBJECT` (address, lat, lng, sqft, beds, baths), so `distance_mi`,
 * the distance filter and the anchor's sqft multiplier are all computed for a
 * fixture unit whatever the user searched. The developer disclosed it in a
 * comment. It is a real and serious finding and it is reported to the PM in
 * prose with a reproduction, **not** pinned as a red test here, because it is
 * not an acceptance criterion of F4-S6 and a red test for a requirement the
 * story never made would block a merge on scope nobody agreed. The PM sequences
 * it; see the verify report.
 *
 * ZERO LIVE RENTCAST CALLS (D17, WORKFLOW.md §6): no server, no key, ephemeral
 * port, every `/api/**` request fulfilled from `e2e/fixtures/f4s6/`.
 */
import { test, expect, type Page } from "@playwright/test";
import { buildUi, fixture, serveUi, stubApi, type ApiCall, type StaticUi } from "./support/static-ui";

let ui: StaticUi | null = null;
let setupFailure: string | null = null;

const PLAN = fixture("plan.json");
const SEARCH_COMPLETE = fixture("search-complete.json");
const SEARCH_ZERO = fixture("search-complete-zero.json");
const DERIVE_COMPLETE = fixture("derive-complete.json");
const DERIVE_EMPTY = fixture("derive-empty.json");

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

function derivedRefs(calls: ApiCall[]): string[] {
  return calls.filter((call) => call.path.endsWith("/api/derive")).map((c) => c.body?.pull_ref);
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

/** Settle: let any derive the app wanted to issue actually go out. */
async function settle(page: Page): Promise<void> {
  await page.waitForTimeout(2000);
}

// ===========================================================================
// harness — a failing precondition, never a skip (WORKFLOW.md §2)
// ===========================================================================

test("the static-UI harness is available", async () => {
  expect(setupFailure, setupFailure ?? "").toBeNull();
  expect(ui, "the static UI server did not start").not.toBeNull();
});

// ===========================================================================
// S1 — the payload claims to be about a different pull
// ===========================================================================

test("S1: a payload whose meta.pull_ref is not the open pull's paints no numbers", async ({
  page,
}) => {
  /**
   * The seam is not only "which ref did we ASK for" — it is "which ref did the
   * numbers on screen come from". Those diverge for reasons that are nobody's
   * bug: `useDerive` keeps the previous answer standing while a new one is in
   * flight (right for a slider move, wrong for a ref change), and a
   * latest-wins race can land an older response last.
   *
   * So: the user searches the ZERO pull (`a0629c…`, 0 comps) and the server
   * answers with a payload that says it is about the COMPLETE pull (`c1dc50…`,
   * 12 comps). A view that renders whatever arrived shows twelve comps from an
   * address the user did not search — the AC8 defect reconstructed out of a
   * mismatched response instead of a constant. Nothing about the request is
   * wrong here, which is exactly what makes it worth testing separately.
   */
  const calls = await stubApi(page, {
    plan: PLAN,
    search: SEARCH_ZERO,
    derive: DERIVE_COMPLETE, // meta.pull_ref = c1dc50…, NOT the searched a0629c…
  });
  await page.goto(baseUrl());
  await submitSearch(page, NARROW);
  await settle(page);

  expect(
    derivedRefs(calls),
    "the request itself targeted the wrong pull, which is AC8a's failure, not this test's",
  ).toEqual([SEARCH_ZERO.pull_ref]);

  expect(
    DERIVE_COMPLETE.meta.pull_ref,
    "this probe needs the stubbed payload to disagree with the searched ref",
  ).not.toBe(SEARCH_ZERO.pull_ref);

  expect(
    await page.getByTestId("comp-row").count(),
    `the view rendered comps from a payload that says it is about ` +
      `${DERIVE_COMPLETE.meta.pull_ref} while the open pull is ${SEARCH_ZERO.pull_ref}. ` +
      "Every number on screen is then about evidence the user did not buy, and the " +
      "identity line above it says otherwise — which is worse than the hardcoded ref, " +
      "because it is self-consistent.",
  ).toBe(0);

  await expect(
    page.locator("body"),
    "an anchor is on screen for a payload that belongs to another pull. An anchor is a " +
      "claim about a market; this one is a claim about a different one.",
  ).not.toContainText(/Anchor:/);
});

// ===========================================================================
// S2 — the ref is genuine and the evidence behind it is gone
// ===========================================================================

test("S2: a pull that no longer exists shows no numbers and says what happened", async ({
  page,
}) => {
  /**
   * The ordinary end of a frozen workspace: the ref came from a real search,
   * `~/.rentcomp/cache/<ref>` was deleted or moved, and `POST /api/derive`
   * answers 404 (`api/derive.py`: `PullNotFoundError` → 404). `useDerive` sets
   * `error` and — this is the part worth pinning — leaves the *previous*
   * payload standing by design. If a previous payload could survive a 404 into
   * this render, the user would be reading numbers whose evidence has been
   * deleted, with an error message next to them.
   *
   * Reached the hard way: derive the FIRST search successfully, then run a
   * second search whose pull 404s. That is the sequence in which a stale
   * payload has something to be stale *with*.
   */
  const calls = await stubApi(page, {
    plan: PLAN,
    search: (body: any) => (body?.radius === 0.25 ? SEARCH_ZERO : SEARCH_COMPLETE),
    derive: DERIVE_COMPLETE,
    deriveOnlyRef: SEARCH_COMPLETE.pull_ref, // the ZERO pull's ref 404s
  });
  await page.goto(baseUrl());

  await submitSearch(page, { ...NARROW, address: "3651 S Wood St, Chicago, IL 60609", radius: "0.5" });
  await settle(page);
  await expect(
    page.getByTestId("comp-row").first(),
    "the first search never rendered, so this probe has no stale payload to try to keep",
  ).toBeVisible({ timeout: 10_000 });

  await submitSearch(page, NARROW); // radius 0.25 -> the ZERO pull -> 404 on derive
  await settle(page);

  expect(
    derivedRefs(calls).at(-1),
    "the second search did not retarget the derive",
  ).toBe(SEARCH_ZERO.pull_ref);

  expect(
    await page.getByTestId("comp-row").count(),
    "the previous pull's comps are still on screen after the open pull failed to derive. " +
      "The evidence behind those rows is gone; leaving them up under a new search's " +
      "identity is the stale-panel failure with an error message beside it.",
  ).toBe(0);

  const body = (await page.locator("body").innerText()).toLowerCase();
  expect(
    /fail|error|not found|no pull|search/.test(body),
    `a pull that cannot be derived rendered neither numbers nor an explanation. Spec §7: ` +
      `say what is wrong and what to do. Saw: ${body.slice(0, 300)}`,
  ).toBe(true);
});

// ===========================================================================
// S3 — the ref must not survive a reload
// ===========================================================================

test("S3: a reload after a search leaves no pull open and derives nothing", async ({ page }) => {
  /**
   * The PM's ruling names "a default ref on first paint" as the thing that must
   * not exist. A remembered ref is that default with a longer fuse: the app
   * already persists the search *form* values to `localStorage`
   * (`searchValues.ts::saveSearchValues`), so persisting the ref alongside them
   * is a one-line change somebody will reach for, and it would put numbers in
   * front of a user who has opened the tab and done nothing.
   *
   * This is the only probe that a `PULL_REF`-as-a-prop implementation could
   * still pass, which is precisely why it is worth having: it fails the moment
   * the ref acquires any storage at all.
   */
  const calls = await stubApi(page, {
    plan: PLAN,
    search: SEARCH_COMPLETE,
    derive: DERIVE_COMPLETE,
    deriveOnlyRef: SEARCH_COMPLETE.pull_ref,
  });
  await page.goto(baseUrl());
  await submitSearch(page, { ...NARROW, address: "3651 S Wood St, Chicago, IL 60609" });
  await settle(page);
  expect(derivedRefs(calls).length, "the search never derived").toBeGreaterThan(0);

  const before = derivedRefs(calls).length;
  await page.reload();
  await page.getByTestId("nav-results").click();
  await settle(page);

  expect(
    derivedRefs(calls).slice(before),
    "after a reload the app derived a pull on its own. The user has opened a tab and asked " +
      "for nothing; a ref that survived the session is a default ref with extra steps, and " +
      "the numbers it paints cannot be attributed to anything the user did in this session.",
  ).toEqual([]);
  expect(
    await page.getByTestId("comp-row").count(),
    "comp rows are on screen after a reload with no pull re-opened",
  ).toBe(0);
});

// ===========================================================================
// S4 — the untested half of "names the binding constraint"
// ===========================================================================

test("S4: a window-emptied pull blames the date window, not the radius", async ({ page }) => {
  /**
   * The branch `breakdown.dropped_outside_window` was put on the wire FOR, and
   * the only test in this story that reaches it.
   *
   * Two pulls, identical in every way a user can see — both `pulled == 0`, both
   * `comps == []` — and opposite remedies. One wants a wider radius; this one
   * wants a wider date window, and widening the radius would spend calls and
   * return the same nothing. An empty state that cannot tell them apart is
   * guessing, and the F4 edge says "names the binding constraint", not "names a
   * constraint".
   *
   * Asserted as a *difference*, not just as a keyword: the same view fed the two
   * payloads must say two different things. A message that happened to contain
   * the window bounds in both branches would pass a keyword check and still be
   * telling the user nothing.
   */
  const windowEmptied = {
    ...DERIVE_EMPTY,
    breakdown: { ...DERIVE_EMPTY.breakdown, dropped_outside_window: 3 },
  };

  const read = async (payload: unknown): Promise<string> => {
    await stubApi(page, {
      plan: PLAN,
      search: SEARCH_ZERO,
      derive: payload,
      deriveOnlyRef: SEARCH_ZERO.pull_ref,
    });
    await page.goto(baseUrl());
    await submitSearch(page, NARROW);
    await settle(page);
    const state = page.getByTestId("empty-state");
    await expect(state, "no empty state rendered for a zero-comp pull").toBeVisible({
      timeout: 10_000,
    });
    return (await state.innerText()).toLowerCase();
  };

  const nothingCameBack = await read(DERIVE_EMPTY);
  const droppedByWindow = await read(windowEmptied);

  expect(
    droppedByWindow,
    "the two empty states are word-for-word identical. `dropped_outside_window` is then a " +
      "field that is computed, serialised and changes nothing a user is told — the exact " +
      "shape this story exists to close, reproduced inside the fix.",
  ).not.toBe(nothingCameBack);

  expect(
    /date window|window/.test(droppedByWindow) &&
      (droppedByWindow.includes("06-15") || droppedByWindow.includes("06-30")),
    `a pull whose records were all dropped by the date window does not name the window as ` +
      `what bound it. Saw: ${droppedByWindow}`,
  ).toBe(true);

  expect(
    droppedByWindow,
    "the window-emptied state does not report how many listings the window threw away. " +
      "'3 listings were pulled and every one fell outside your window' is the sentence that " +
      "makes the remedy obvious; 'no comps' is not.",
  ).toContain("3");

  // And the two must not both blame the radius, which is the failure mode a
  // single-branch implementation actually has.
  expect(
    nothingCameBack.includes("0.25"),
    `the "nothing came back" branch does not echo the radius that bound it. Saw: ${nothingCameBack}`,
  ).toBe(true);
});
