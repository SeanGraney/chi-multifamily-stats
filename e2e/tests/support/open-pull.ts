/**
 * Opening a pull on the LIVE-SERVER harness — the one door into Results.
 *
 * ===========================================================================
 * WHY THIS MODULE EXISTS (F4-S6, merged after all four of its callers)
 * ===========================================================================
 * Four specs used to reach Results by navigating there and waiting for a
 * derive. That worked only because `Results.tsx` held
 * `const PULL_REF = "ws1-real"` and derived it unconditionally on mount — so
 * *any* visit to Results derived a fixture, whatever the user had searched.
 * F4-S6 removed that constant: `Results` now derives nothing until `App` holds
 * an `OpenPull`, and the only producer of one today is `SearchForm`'s
 * `onSubmitted` (`frontend/src/pull.ts`, "the two doors" — F1-S1's recents row
 * is the second and is not built).
 *
 * ⚠ THAT IS THE SEAM WORKING, NOT A REGRESSION. Before it, the owner could
 * spend real API calls on a real address, open Results, and be shown the
 * analysis of a fixture with nothing on screen saying so — the
 * confident-output-the-evidence-does-not-support failure `NORTH_STAR.md`
 * exists to prevent. **Nothing here may weaken it:** no default ref, no derive
 * on mount, no second door. This module changes how the specs *arrive*, never
 * what they assert.
 *
 * It is one module rather than four copies because there is one door. When
 * F1-S1 lands the second one, this is the single place that learns about it.
 *
 * ===========================================================================
 * WHY THE SEARCH IS STUBBED AND THE DERIVE IS NOT
 * ===========================================================================
 * Every caller here needs a REAL derive, and they would each be weakened in a
 * different way by a stubbed one:
 *
 *   f6-s1-map    AC3 needs comps with genuine coordinate spread, AC4 needs a
 *                payload carrying more than one `state`, AC7 needs the server
 *                to actually re-classify a comp when the user unchecks it
 *   f5-s2        the weight assertions need real re-classification and a real
 *                `contribution_share` — the whole point is that the number on
 *                screen is the server's, not a division done in TypeScript
 *   f7-s1        the filter assertions need the server to genuinely re-label
 *   ws1          "renders real data" is the entire claim
 *
 * A stubbed derive would make each of those assert a fixture back at itself:
 * a passing test that proves nothing. So `POST /api/derive` goes to the live
 * server the calling spec spawned, and only `POST /api/search` is intercepted
 * — because a real one cannot answer on this harness by design. `RENTCOMP_LIVE`
 * is empty and `RENTCAST_API_KEY` is deleted from the child env (D17), so a
 * cache-miss search has nothing to fetch and would mint a ref with no evidence
 * behind it.
 *
 * `POST /api/search/plan` is deliberately left to the live server: it is
 * structurally incapable of spending a call (`api/plan.py`), so the cost
 * preview the form waits for is the real planner's answer rather than a
 * fixture pretending to be one.
 *
 * ZERO LIVE RENTCAST CALLS (D17, WORKFLOW.md §6). The ledger cannot move.
 */
import { expect, type Page, type Route } from "@playwright/test";
import { fixture } from "./static-ui";

/**
 * The one ref this backend resolves offline.
 *
 * `storage/pulls.py::WS1_REAL_PULL_REF` shapes the two committed T-S3 gate
 * fixtures through the real record-shaping chain, resolved relative to the repo
 * rather than to `RENTCOMP_HOME` — so a spec running against a fresh temp home
 * finds it with zero seeding.
 *
 * ⚠ The literal lives HERE and must never appear in `frontend/src`:
 * `f4s6-derive-targets-the-users-pull.spec.ts` AC8d walks the source tree for
 * exactly this string and must keep finding nothing.
 */
export const WS1_REAL_PULL_REF = "ws1-real";

/**
 * What the stubbed `POST /api/search` answers.
 *
 * The wire shape is F4-S6's committed fixture — re-validated against the
 * Pydantic response models on every pytest run by
 * `test_f4s6_stub_payloads_match_the_wire.py`, so this cannot drift from the
 * contract it stands in for — with one field changed: the ref this backend can
 * actually derive. `complete: true` keeps the gap banner off the screen; a
 * partial-pull banner is F4-S6's subject, not any of these specs'.
 */
const SEARCH_OUTCOME = { ...fixture("search-complete.json"), pull_ref: WS1_REAL_PULL_REF };

/**
 * A valid search to submit.
 *
 * Every value here is inert with respect to the callers' assertions: `Results`
 * still derives a hardcoded `SUBJECT` (disclosed by F4-S6's developer as an
 * open finding, not something these specs can close), and the comps come from
 * the ref above rather than from these fields. They exist only to get past the
 * form's inline validation, so they are the ws1 fixture's own subject for
 * coherence rather than for effect. `01-01`–`12-31` matches the window
 * `_load_ws1_real_pull` reads the fixtures under.
 */
export const WS1_SEARCH_VALUES: Record<string, string> = {
  address: "3651 S Wood St, Chicago, IL 60609",
  bedrooms: "4",
  bathrooms: "2",
  sqft: "1200",
  radius: "0.5",
  windowStart: "01-01",
  windowEnd: "12-31",
  yearsBack: "2",
};

/** `SearchValues` field name → the form control's `data-testid`. */
const SEARCH_FIELD_TESTIDS: Record<string, string> = {
  address: "search-address",
  bedrooms: "search-bedrooms",
  bathrooms: "search-bathrooms",
  sqft: "search-sqft",
  radius: "search-radius",
  windowStart: "search-window-start",
  windowEnd: "search-window-end",
  yearsBack: "search-years-back",
};

/**
 * Answer `POST /api/search` with {@link SEARCH_OUTCOME}, and nothing else.
 *
 * The predicate matches the search route *exactly*. A `"**\/api/search"` glob
 * would be a trap the day someone reads it as also covering
 * `/api/search/plan` — which must keep reaching the live planner.
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
 * Stub the search and land on the app's home screen, ready to submit.
 *
 * Split from {@link submitSearch} on purpose: callers arm their
 * `waitForResponse` for the derive *between* the two, so the listener is
 * attached before the click that can produce a derive but after the navigation
 * that would discard it.
 */
export async function arriveWithAPullToOpen(page: Page, baseUrl: string): Promise<void> {
  await stubTheSearch(page);
  await page.goto(baseUrl + "/");
}

/**
 * Fill and submit the search form. `App.openPull` then lands the user on
 * Results with the ref the response carried (F4 flow step 4, "the system lands
 * the user on Results") — so no caller clicks the Results tab, which post-F4-S6
 * reaches a view with no pull open that correctly derives nothing.
 *
 * Waiting for `search-preview` is not cosmetic: the preview is debounced 400ms,
 * and submitting before it lands races the plan the progress surface reads its
 * cohort years from.
 */
export async function submitSearch(
  page: Page,
  values: Record<string, string> = WS1_SEARCH_VALUES,
): Promise<void> {
  await page.getByTestId("nav-home").click();
  await page.getByTestId("nav-new-search").click();
  await expect(page.getByTestId("search-form")).toBeVisible();
  for (const [field, value] of Object.entries(values)) {
    const testId = SEARCH_FIELD_TESTIDS[field];
    if (testId) await page.getByTestId(testId).fill(value);
  }
  await expect(page.getByTestId("search-preview")).toBeVisible({ timeout: 10_000 });
  await page.getByTestId("search-submit").click();
}
