/**
 * F5-S1 — the comp row (spec §6.4): the browser-genuine half.
 *
 * QA-authored, written RED before the developer's branch exists
 * (AGENT_QA.md protocol step 3).
 *
 * ---------------------------------------------------------------------------
 * WHY THESE TESTS ARE L3 AND WHAT DELIBERATELY IS NOT HERE
 * ---------------------------------------------------------------------------
 * Two of this story's fifteen acceptance criteria are backend work and are
 * asserted below the browser, where the debugging happens:
 *
 *   AC6 (`sqft_suspect` computed server-side) and AC9's day-offset ARITHMETIC
 *   → `backend/tests/unit/test_f5s1_row_fields.py`
 *     `backend/tests/api/test_f5s1_row_fields_on_the_wire.py`
 *
 * AC7's named real-data case (2350 S Leavitt St 1R) is likewise proven at
 * Layer 2 against the real pull. This file never asserts a computed value it
 * could have asserted over HTTP; where it touches those fields it asserts only
 * the WIRING — that the number on screen is the server's — which is the split
 * rule in `AGENT_QA.md` ("exhaustive below, representative above").
 *
 * Two more ACs are regression, already owned by existing specs, and are NOT
 * duplicated here — duplicating them would double the browser cost of every
 * future run for no added coverage:
 *
 *   AC13 ALL / NONE bulk toggles, incl. ALL's no-sqft rule
 *        → `f5-s2-selection-weight.spec.ts` (AC2, AC2b, AC2c)
 *   AC14 filtered comps leave the list, stay rust on the map, per-row INCLUDE
 *        → `f7-s1-filter-engine.spec.ts`
 *
 * What is left is what only a browser can witness: that the row is three
 * lines and not one string (layout), that each field is on the line §6.4 puts
 * it on, that a badge appears, that a panel expands, that a link points
 * somewhere real — and, twice, that a rendered number came from the payload
 * rather than from arithmetic done in TypeScript, which is only provable by
 * making the two disagree.
 *
 * ---------------------------------------------------------------------------
 * TEST-ID CONTRACT (QA-proposed — PM to confirm before the developer starts)
 * ---------------------------------------------------------------------------
 * Existing, must not regress:
 *   [data-testid="comp-row"] + data-comp-key="<key>"
 *   [data-testid="comp-include-toggle"] / [data-testid="comp-weight"]
 *   [data-testid="comp-contribution"] / [data-testid="select-all"] / "select-none"
 *
 * New for this story:
 *   [data-testid="comp-address"]           line 1 — address (+ unit)
 *   [data-testid="comp-ask"]               line 1 — initial ask
 *   [data-testid="comp-specs"]             line 2 — state · beds·baths · sqft ·
 *                                          distance · cohort year
 *   [data-testid="comp-outcome"]           line 2 — the outcome string
 *   [data-testid="comp-psf"]               the $/sqft
 *   [data-testid="comp-no-sqft"]           the missing-sqft badge
 *   [data-testid="comp-verify-sqft"]       the "verify sqft" flag
 *   [data-testid="comp-cut-history"]       line 3 — the ✂ cut line
 *   [data-testid="comp-stitch-badge"]      line 3 — the ⟲ re-list badge
 *   [data-testid="comp-withdrawal-suspect"] line 3 — the withdrawal-suspect badge
 *   [data-testid="comp-expand"]            the expand control
 *   [data-testid="comp-detail"]            the expanded panel
 *   [data-testid="comp-zillow"]            the Zillow deep link (an <a>)
 *   [data-testid="comp-streetview"]        the Google Street View link (an <a>)
 *
 * Deliberately NOT pinned: wording, punctuation, colour hexes, element tags,
 * the order of items within a line, or the exact number format (matched with
 * an optional thousands separator throughout). Only the FACTS §6.4 names, and
 * which of the three lines each fact sits on.
 *
 * ---------------------------------------------------------------------------
 * ZERO LIVE CALLS (WORKFLOW.md §6, D17)
 * ---------------------------------------------------------------------------
 * `RENTCOMP_LIVE` is never set, `RENTCAST_API_KEY` is deleted from the child
 * env, `RENTCOMP_HOME` is a fresh temp dir, and the evidence is the committed
 * `fixtures/live-samples/` that `ws1-real` resolves against. The ledger stays
 * where it is.
 *
 * NO `mode: "serial"` — `playwright.config.ts` already pins `workers: 1,
 * fullyParallel: false`, so it would add nothing but "stop after the first
 * failure", and "did not run" reads like a pass (WORKFLOW.md §2).
 */
import { test, expect, type Page, type Route } from "@playwright/test";
import { existsSync, mkdtempSync, readFileSync, readdirSync, rmSync, statSync } from "node:fs";
import { tmpdir } from "node:os";
import net from "node:net";
import path from "node:path";
import { spawn, execFileSync, type ChildProcess } from "node:child_process";
import { REPO_ROOT, NPM, NPM_NEEDS_SHELL } from "./support/local-server";
import { arriveWithAPullToOpen, submitSearch } from "./support/open-pull";

const FRONTEND_DIR = path.join(REPO_ROOT, "frontend");
const FRONTEND_DIST = path.join(FRONTEND_DIR, "dist");
const FRONTEND_SRC = path.join(FRONTEND_DIR, "src");

/** WORKFLOW.md §2's third door — a worktree has no `.venv` of its own. */
const VENV_DIR = process.env.RENTCOMP_VENV_DIR ?? path.join(REPO_ROOT, ".venv");
const VENV_BIN = path.join(VENV_DIR, process.platform === "win32" ? "Scripts" : "bin");
const VENV_PYTHON = path.join(VENV_BIN, process.platform === "win32" ? "python.exe" : "python");
const BACKEND_SRC = path.join(REPO_ROOT, "backend", "src");

// ---------------------------------------------------------------------------
// The named comps. Everything else is picked out of the payload at runtime, so
// this spec keeps working when the fixture's incidental contents move.
// ---------------------------------------------------------------------------

/** AC7's comp: $4.28/sqft, +156.7%, a 3bd/3ba in 700 sqft. */
const LEAVITT = "2350 s leavitt st|1r";
/** One cut: $1,700 → $1,600, on day 71 of its spell. */
const CUT_ONCE = "1758 w 35th st|3f";
const CUT_ONCE_DAY = 71;

let serverProcess: ChildProcess | null = null;
let rentcompHome: string | null = null;
let setupFailure: string | null = null;
let BASE_URL = "";
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
  rentcompHome = mkdtempSync(path.join(tmpdir(), "rentcomp-f5s1-home-"));

  const env = { ...process.env } as NodeJS.ProcessEnv;
  delete env.RENTCAST_API_KEY; // D17: a live call must be impossible, not unlikely
  env.RENTCOMP_LIVE = "";
  env.RENTCOMP_HOME = rentcompHome;
  env.RENTCOMP_UI_DIR = FRONTEND_DIST;
  env.PYTHONPATH = [BACKEND_SRC, process.env.PYTHONPATH].filter(Boolean).join(path.delimiter);

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
// Locators and helpers
// ---------------------------------------------------------------------------

const isDerive = (url: string) => url.includes("/api/derive");

function rows(page: Page) {
  return page.locator("[data-testid='comp-row']");
}

/**
 * A ROW by key. The `:not([data-testid='map-pin'])` exclusions are F6-S1's
 * amendment to the same helper in `f5-s2-selection-weight.spec.ts`: map pins
 * carry `data-comp-key` too, and `.first()` takes DOM order, which is the pin.
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

type Row = ReturnType<typeof rowFor>;

function part(row: Row, testid: string) {
  return row.locator(`[data-testid='${testid}']`).first();
}

/** `1700` -> /1[,\s]?700/ — format-agnostic about the thousands separator. */
function numberish(value: number): RegExp {
  const whole = String(Math.round(value));
  if (whole.length <= 3) return new RegExp(`\\b${whole}\\b`);
  const head = whole.slice(0, whole.length - 3);
  const tail = whole.slice(whole.length - 3);
  return new RegExp(`\\b${head}[,\\s]?${tail}\\b`);
}

/** A $/sqft as it could reasonably be rounded for display (2dp or 1dp). */
function psfish(psf: number): RegExp {
  const two = psf.toFixed(2).replace(".", "\\.");
  const one = psf.toFixed(1).replace(".", "\\.");
  return new RegExp(`${two}|${one}`);
}

async function textOf(row: Row): Promise<string> {
  return ((await row.innerText()) ?? "").replace(/\s+/g, " ");
}

/** The vertical band an element occupies. */
async function band(locator: Row): Promise<{ top: number; bottom: number }> {
  const box = await locator.boundingBox();
  expect(box, "element has no bounding box — it is not rendered").toBeTruthy();
  return { top: box!.y, bottom: box!.y + box!.height };
}

/**
 * Open a pull, land on Results, and return the derive payload.
 *
 * F4-S6 removed `Results.tsx`'s hardcoded `PULL_REF`, so a pull has to be
 * opened through the search form, which is the user's own route in. The
 * derive is the LIVE server's — a stubbed one would assert a fixture back at
 * itself. See `support/open-pull.ts`.
 */
async function gotoResults(page: Page): Promise<any> {
  await arriveWithAPullToOpen(page, BASE_URL);
  const first = page.waitForResponse((r) => isDerive(r.url()) && r.request().method() === "POST", {
    timeout: 20_000,
  });
  await submitSearch(page);
  const response = await first;
  expect(response.status(), "the initial derive did not succeed").toBe(200);
  await expect(rows(page).first()).toBeVisible({ timeout: 15_000 });
  return response.json();
}

/**
 * Land on Results with the server's own payload, edited on the way through.
 *
 * The server is real and the derive is real; `patch` rewrites the response
 * body before the view sees it. This is how the two D5 wiring claims are
 * proven — by making the payload disagree with what a client-side computation
 * would produce — and how payload states the real pull does not contain (a
 * removal with no `days_since_removal`, a cut with no day) are reached
 * without hand-building a whole `DerivedState`.
 */
async function gotoResultsPatched(page: Page, patch: (payload: any) => void): Promise<any> {
  let seen: any = null;
  await page.route(
    (url) => url.pathname === "/api/derive",
    async (route: Route) => {
      const response = await route.fetch();
      const payload = await response.json();
      patch(payload);
      seen = payload;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(payload),
      });
    },
  );
  await arriveWithAPullToOpen(page, BASE_URL);
  await submitSearch(page);
  await expect(rows(page).first()).toBeVisible({ timeout: 20_000 });
  expect(seen, "no derive was intercepted — the view never asked for one").toBeTruthy();
  return seen;
}

function compIn(payload: any, key: string): any {
  const comp = payload.comps.find((c: any) => c.key === key);
  expect(
    comp,
    `${key} is not in the ws1-real payload any more — re-measure this spec's constants ` +
      "rather than deleting the assertion",
  ).toBeTruthy();
  return comp;
}

function findComp(payload: any, predicate: (c: any) => boolean, what: string): any {
  const comp = payload.comps.find(predicate);
  expect(comp, `no comp in the payload is ${what} — this test cannot be exercised`).toBeTruthy();
  return comp;
}

const NULLISH = /\bnull\b|\bundefined\b|\bNaN\b/;

// ---------------------------------------------------------------------------

test.describe("F5-S1 — the comp row is §6.4's row", () => {
  test.beforeEach(skipIfSetupFailed);

  test("AC1: the row is three lines, not one run-on string", async ({ page }) => {
    // Today `CompRow` renders a single `<li>` holding one flat sentence
    // (`Results.tsx`). §6.4's mock is three lines, and which line a fact sits
    // on is the whole scannability claim of F5's flow step 1 ("user scans
    // rows"). Layout — jsdom has none, so this is L3 by the decision
    // procedure's last question.
    const payload = await gotoResults(page);
    const comp = compIn(payload, CUT_ONCE); // has a cut, so line 3 exists
    const row = rowFor(page, comp.key);
    await expect(row).toBeVisible({ timeout: 10_000 });

    const one = await band(part(row, "comp-address"));
    const two = await band(part(row, "comp-specs"));
    const three = await band(part(row, "comp-cut-history"));

    expect(
      two.top,
      "the address and the unit's specs are on the same line — that is today's run-on row, " +
        "which is exactly what AC1 replaces",
    ).toBeGreaterThanOrEqual(one.bottom - 1);
    expect(
      three.top,
      "the cut-history line is not below the specs line — §6.4 puts badges on line 3",
    ).toBeGreaterThanOrEqual(two.bottom - 1);
  });

  test("AC2: line 1 carries the toggle, the address, the weight, the contribution and the ask", async ({
    page,
  }) => {
    // Every one of these is already rendered today — this is a re-layout, and
    // the risk it carries is regressing F5-S2's controls while moving them.
    // Their BEHAVIOUR stays F5-S2's spec; what is asserted here is that they
    // are all present and all on line 1.
    const payload = await gotoResults(page);
    const comp = findComp(payload, (c: any) => c.state === "included", "included");
    const row = rowFor(page, comp.key);
    await expect(row).toBeVisible({ timeout: 10_000 });

    for (const testid of ["comp-include-toggle", "comp-weight", "comp-contribution", "comp-address", "comp-ask"]) {
      await expect(part(row, testid), `line 1 is missing [data-testid=${testid}]`).toBeVisible();
    }

    const line1 = await band(part(row, "comp-address"));
    for (const testid of ["comp-include-toggle", "comp-weight", "comp-contribution", "comp-ask"]) {
      const other = await band(part(row, testid));
      expect(
        other.top < line1.bottom && line1.top < other.bottom,
        `[data-testid=${testid}] is not on the same line as the address — §6.4 puts all five ` +
          "on line 1",
      ).toBe(true);
    }

    await expect(part(row, "comp-address")).toContainText(comp.address);
    if (comp.unit) await expect(part(row, "comp-address")).toContainText(comp.unit);
    await expect(part(row, "comp-ask")).toHaveText(numberish(comp.initial_ask));
  });

  test("AC3: line 2 carries beds, baths, sqft, distance and cohort year — the unit itself", async ({
    page,
  }) => {
    // ⚠ `beds`, `baths` and `sqft` are on the wire and rendered NOWHERE today:
    // the unit a comp actually IS is currently invisible, which makes "is this
    // comparable to mine?" unanswerable from the list.
    //
    // ⚠ ONE FIELD AC3 NAMES IS NOT ASSERTED HERE, DELIBERATELY — QA RAISED IT
    // WITH THE PM RATHER THAN GUESSING. AC3's list opens with "state", and it
    // is ambiguous which state: §6.4's mock reads `INACTIVE · 3bd·2ba · ...`,
    // which is the LISTING's status (off-market), while `DerivedComp.state` is
    // the CURATION status (included/excluded/filtered) — and the curation
    // status is already on line 1, as the checkbox. Asserting the wrong one
    // would force the developer to build the wrong thing. Everything else AC3
    // names is asserted below; this row is pending the PM's ruling.
    const payload = await gotoResults(page);
    const comp = findComp(
      payload,
      (c: any) => c.sqft !== null && c.baths !== null && c.distance_mi > 0,
      "a fully-specced comp (beds, baths, sqft, distance)",
    );
    const row = rowFor(page, comp.key);
    await expect(row).toBeVisible({ timeout: 10_000 });
    const specs = part(row, "comp-specs");
    const text = ((await specs.innerText()) ?? "").replace(/\s+/g, " ");

    expect(text, `beds (${comp.beds}) not on line 2: ${text}`).toMatch(numberish(comp.beds));
    expect(text, `baths (${comp.baths}) not on line 2: ${text}`).toMatch(numberish(comp.baths));
    expect(text, `sqft (${comp.sqft}) not on line 2: ${text}`).toMatch(numberish(comp.sqft));
    expect(text, `cohort year (${comp.cohort_year}) not on line 2: ${text}`).toContain(
      String(comp.cohort_year),
    );
    expect(text, `distance (${comp.distance_mi} mi) not on line 2: ${text}`).toMatch(
      new RegExp(comp.distance_mi.toFixed(2).replace(".", "\\.") + "|" + comp.distance_mi.toFixed(1).replace(".", "\\.")),
    );
  });

  test("AC4: the outcome string tells the truth about which of the four states a comp is in", async ({
    page,
  }) => {
    // NORTH_STAR's most important distinction lives in this string: a censored
    // comp has not leased, and a pending removal is not yet a lease. A row
    // that says "leased" about either is the failure this whole tool exists
    // to avoid — so the negative assertions here matter more than the
    // positive ones.
    const payload = await gotoResults(page);

    const censored = findComp(payload, (c: any) => c.censored, "still active (censored)");
    const censoredText = await textOf(part(rowFor(page, censored.key), "comp-outcome"));
    expect(censoredText, `censored comp reads: ${censoredText}`).toMatch(/activ/i);
    expect(censoredText).toMatch(numberish(censored.effective_dom));
    expect(
      censoredText,
      "a still-active listing is being described as leased — its DOM is a FLOOR, not an " +
        "outcome (NORTH_STAR: censored is never counted as leased)",
    ).not.toMatch(/leas/i);
    expect(censoredText, "the DOM floor is not marked as a floor (§6.4: `active 45+ d`)").toMatch(
      /\+/,
    );

    const pending = findComp(
      payload,
      (c: any) => c.removal_class === "pending",
      "a pending removal (<7d off market)",
    );
    const pendingText = await textOf(part(rowFor(page, pending.key), "comp-outcome"));
    expect(pendingText, `pending comp reads: ${pendingText}`).toMatch(/classif|pending/i);
    expect(
      pendingText,
      "a removal too recent to trust is being reported as a lease — pendings are EXCLUDED " +
        "from lease statistics, not marked in them",
    ).not.toMatch(/leas/i);
    expect(
      pendingText,
      "the row does not say how long ago the removal was, so the user cannot check the claim",
    ).toMatch(numberish(pending.days_since_removal));

    const provisional = findComp(
      payload,
      (c: any) => c.removal_class === "provisional",
      "a provisional lease (>=7d off market)",
    );
    const provisionalText = await textOf(part(rowFor(page, provisional.key), "comp-outcome"));
    expect(provisionalText, `provisional comp reads: ${provisionalText}`).toMatch(/leas/i);
    expect(
      provisionalText,
      "a provisional lease is rendered identically to a confirmed one — the confidence " +
        "ladder is invisible to the reader",
    ).toMatch(/provisional/i);
    expect(provisionalText).toMatch(numberish(provisional.effective_dom));

    const confirmed = findComp(
      payload,
      (c: any) => c.removal_class === "confirmed",
      "a confirmed lease",
    );
    const confirmedText = await textOf(part(rowFor(page, confirmed.key), "comp-outcome"));
    expect(confirmedText, `confirmed comp reads: ${confirmedText}`).toMatch(/leas/i);
    expect(confirmedText).toMatch(numberish(confirmed.effective_dom));
    expect(
      confirmedText,
      "a confirmed lease is being hedged as provisional or still classifying",
    ).not.toMatch(/provisional|classif/i);
  });

  test("AC4b: a removal with no `days_since_removal` renders a row, not the word null", async ({
    page,
  }) => {
    // ⚠ ROW 10b's TRAP, AIMED AT EXACTLY THIS STORY. `responses.py` says
    // `days_since_removal` is `None` **iff** `removal_class` is `None`. That
    // is a PIPELINE guarantee, not a type guarantee, and it was false for
    // every pre-shaped synthetic fixture — including `synthetic-basic`, the
    // pull the entire L2 suite derives against, where rows read
    // *"removed nulld — classifying"*. A developer reading the docstring
    // writes `days_since_removal!` and is wrong.
    //
    // The real pull no longer contains the state, so it is arranged here on
    // the way through. The row must degrade to "we do not know how long",
    // never to a rendered `null`.
    const payload = await gotoResultsPatched(page, (state: any) => {
      const victim = state.comps.find((c: any) => c.removal_class === "pending");
      expect(victim, "no pending comp to patch").toBeTruthy();
      victim.days_since_removal = null;
      state.__victim = victim.key;
    });
    const row = rowFor(page, payload.__victim);
    await expect(row).toBeVisible({ timeout: 10_000 });
    const outcome = part(row, "comp-outcome");
    await expect(
      outcome,
      "the row has no outcome element to inspect — AC4 has not been built, so this trap " +
        "cannot be checked yet (this test is scoped to `comp-outcome` on purpose: scoped to " +
        "the whole row it would pass today, on a row that renders neither the number nor " +
        "the word null, and a green test in a red suite is worse than no test)",
    ).toBeVisible({ timeout: 10_000 });
    const text = ((await outcome.innerText()) ?? "").replace(/\s+/g, " ");
    expect(
      text,
      `the outcome printed a nullish value: ${text} — this is row 10b's trap, and the fix is ` +
        "to render the rung without the number, not to assume the number is there",
    ).not.toMatch(NULLISH);
    expect(text, "the row lost its classification state entirely").toMatch(/classif|pending/i);
    expect(
      text,
      "a removal whose age is unknown is still not a lease (NORTH_STAR)",
    ).not.toMatch(/leas/i);
  });

  test("AC5: every row shows a $/sqft, or says it has none", async ({ page }) => {
    // §6.4 puts $/sqft on every row because it is the quantity premiums are
    // built from. A row with neither the number nor the badge is a row whose
    // silence the reader cannot interpret.
    const payload = await gotoResults(page);

    const withSqft = findComp(payload, (c: any) => c.psf !== null, "priced per sqft");
    await expect(
      part(rowFor(page, withSqft.key), "comp-psf"),
      "a comp with a server-computed $/sqft does not display it",
    ).toHaveText(psfish(withSqft.psf));

    const noSqft = findComp(payload, (c: any) => c.psf === null, "missing its square footage");
    const noSqftRow = rowFor(page, noSqft.key);
    await expect(
      part(noSqftRow, "comp-no-sqft"),
      "a comp with no square footage carries no `no sqft` badge — §7 makes it " +
        "default-excluded, and the badge is the only thing that explains why",
    ).toBeVisible();
    expect(
      await textOf(noSqftRow),
      "a comp with no sqft is displaying something where its $/sqft would be",
    ).not.toMatch(NULLISH);

    // Every rendered row, in one round trip: the number or the badge, never
    // neither. The loop is over the DOM, not over browser sessions, so this
    // is not the parametrize-in-Playwright smell.
    const offenders = await page.evaluate(() => {
      const bad: string[] = [];
      document.querySelectorAll("[data-testid='comp-row']").forEach((row) => {
        const hasPsf = row.querySelector("[data-testid='comp-psf']");
        const hasBadge = row.querySelector("[data-testid='comp-no-sqft']");
        if (!hasPsf && !hasBadge) bad.push(row.getAttribute("data-comp-key") ?? "?");
      });
      return bad;
    });
    expect(
      offenders.slice(0, 5),
      `${offenders.length} row(s) show neither a $/sqft nor a no-sqft badge`,
    ).toEqual([]);
  });

  test("AC5b: the $/sqft on screen is the server's, not a division done in TypeScript", async ({
    page,
  }) => {
    // D5/D13. `psf` is computed in Python; the view may never divide
    // `initial_ask / sqft`. The only way to tell the two apart is to make them
    // disagree — the same technique F5-S2 used for `contribution_share`.
    const payload = await gotoResultsPatched(page, (state: any) => {
      const victim = state.comps.find((c: any) => c.psf !== null && c.sqft !== null);
      expect(victim, "no comp with a psf to patch").toBeTruthy();
      victim.psf = 9.99;
      state.__victim = victim.key;
    });
    const comp = compIn(payload, payload.__victim);
    const shownIfDivided = comp.initial_ask / comp.sqft;
    expect(
      Math.abs(shownIfDivided - 9.99),
      "the patched value coincides with the division — pick another comp",
    ).toBeGreaterThan(0.5);
    await expect(
      part(rowFor(page, comp.key), "comp-psf"),
      "the row is showing `initial_ask / sqft` computed in the browser instead of the " +
        "server's `psf` (D5: all derivation happens in Python)",
    ).toHaveText(/9\.99/);
  });

  test("AC8: the verify-sqft flag renders where the server raised it, and points at Zillow", async ({
    page,
  }) => {
    // AC7's comp: $4.28/sqft, +157% off its own cohort's median, a 3bd/3ba
    // listed as 700 sqft. Whether the flag is RAISED is Layer 2's assertion
    // (`test_f5s1_row_fields_on_the_wire.py`); what is asserted here is that
    // the row shows it and attaches the action — a flag with nothing to do
    // about it is not the feature. RentCast ships no photos, so Zillow is the
    // only verification path there is.
    const payload = await gotoResults(page);
    const leavitt = compIn(payload, LEAVITT);
    expect(
      leavitt.sqft_suspect,
      "the server has not raised sqft_suspect on the +157% comp — that is AC6/AC7, asserted " +
        "at Layer 2; this test cannot pass until it does",
    ).toBe(true);

    const row = rowFor(page, LEAVITT);
    await expect(row).toBeVisible({ timeout: 10_000 });
    await expect(part(row, "comp-verify-sqft")).toBeVisible();

    await part(row, "comp-expand").click();
    const zillow = part(row, "comp-zillow").or(page.locator("[data-testid='comp-zillow']").first());
    await expect(
      zillow,
      "the flagged row offers no Zillow link — the flag says 'go and check' and there is " +
        "nowhere to go",
    ).toBeVisible();

    const ordinary = findComp(
      payload,
      (c: any) => !c.sqft_suspect && c.psf !== null,
      "an unflagged comp with a $/sqft",
    );
    await expect(
      part(rowFor(page, ordinary.key), "comp-verify-sqft"),
      "an unflagged comp is showing the verify-sqft flag — a badge on every row carries no " +
        "information",
    ).toHaveCount(0);
  });

  test("AC8b: the verify flag follows the server's boolean, not a rule of the view's own", async ({
    page,
  }) => {
    // D5 again, from the other direction: flip the flag on a comp that is
    // nowhere near its cohort median and the badge must appear anyway.
    const payload = await gotoResultsPatched(page, (state: any) => {
      const victim = state.comps.find(
        (c: any) => !c.sqft_suspect && c.psf !== null && Math.abs(c.premium ?? 0) < 0.05,
      );
      expect(victim, "no ordinary comp to patch").toBeTruthy();
      victim.sqft_suspect = true;
      state.__victim = victim.key;
    });
    await expect(
      part(rowFor(page, payload.__victim), "comp-verify-sqft"),
      "the view decided for itself whether the sqft is suspect instead of rendering the " +
        "server's flag (D5)",
    ).toBeVisible();
  });

  test("AC9: the cut-history line shows both prices and the day of the spell", async ({ page }) => {
    // §6.4: `✂ 2,150 → 2,050 (day 21)`. The real comp: $1,700 → $1,600 on
    // day 71. The ARITHMETIC is the server's (asserted at L1/L2 — the view may
    // not subtract two dates, D5/D15); what is asserted here is that all three
    // facts reach the line.
    const payload = await gotoResults(page);
    const comp = compIn(payload, CUT_ONCE);
    expect(comp.cut_history.length, `${CUT_ONCE} no longer has exactly one cut`).toBe(1);
    const line = part(rowFor(page, CUT_ONCE), "comp-cut-history");
    await expect(line).toBeVisible({ timeout: 10_000 });
    const text = ((await line.innerText()) ?? "").replace(/\s+/g, " ");
    expect(text, `cut line reads: ${text}`).toMatch(numberish(comp.cut_history[0].from_price));
    expect(text).toMatch(numberish(comp.cut_history[0].to_price));
    expect(
      text,
      `the cut line does not say WHEN in the spell the cut happened (expected day ` +
        `${CUT_ONCE_DAY}) — "day 21" is in §6.4's mock because a cut on day 3 and a cut on ` +
        "day 90 mean opposite things about how the price was received",
    ).toMatch(new RegExp(`\\b${CUT_ONCE_DAY}\\b`));

    const uncut = findComp(payload, (c: any) => c.cut_history.length === 0, "never cut");
    await expect(
      part(rowFor(page, uncut.key), "comp-cut-history"),
      "a comp that was never cut is rendering an empty cut line",
    ).toHaveCount(0);
  });

  test("AC9b: a cut whose day is unknown renders the cut, not `day null`", async ({ page }) => {
    // Row 10b's shape again, in the new field: the day offset is `int | None`
    // because `StitchedComp.first_listed` is `date | None`. Real evidence
    // always has it; a pre-shaped or hand-built comp does not. Losing the day
    // must cost the day, not the whole line.
    const payload = await gotoResultsPatched(page, (state: any) => {
      const victim = state.comps.find((c: any) => c.cut_history.length > 0);
      expect(victim, "no comp with a cut to patch").toBeTruthy();
      for (const cut of victim.cut_history) {
        for (const field of Object.keys(cut)) {
          if (!["on", "from_price", "to_price"].includes(field)) cut[field] = null;
        }
      }
      state.__victim = victim.key;
    });
    const comp = compIn(payload, payload.__victim);
    const line = part(rowFor(page, comp.key), "comp-cut-history");
    await expect(line, "the cut disappeared entirely when its day went missing").toBeVisible();
    const text = ((await line.innerText()) ?? "").replace(/\s+/g, " ");
    expect(text, `cut line reads: ${text}`).not.toMatch(NULLISH);
    expect(text).toMatch(numberish(comp.cut_history[0].from_price));
    expect(text).toMatch(numberish(comp.cut_history[0].to_price));
  });

  test("AC10: a re-listed comp carries the stitch badge with its count and gap", async ({
    page,
  }) => {
    // `⟲ re-listed ×1 (6d gap)`. Both numbers are already on the wire and
    // neither is rendered. The gap matters as much as the count: a 6-day gap
    // is one listing that hiccuped, a 60-day gap is two different attempts to
    // rent the unit.
    const payload = await gotoResults(page);
    const comp = findComp(
      payload,
      (c: any) => c.relist_count > 0 && c.gap_days > 0,
      "re-listed with a real off-market gap",
    );
    const badge = part(rowFor(page, comp.key), "comp-stitch-badge");
    await expect(badge).toBeVisible({ timeout: 10_000 });
    const text = ((await badge.innerText()) ?? "").replace(/\s+/g, " ");
    expect(text, `stitch badge reads: ${text}`).toMatch(numberish(comp.relist_count));
    expect(text, `the badge does not report the ${comp.gap_days}-day gap: ${text}`).toMatch(
      numberish(comp.gap_days),
    );

    const never = findComp(payload, (c: any) => c.relist_count === 0, "never re-listed");
    await expect(
      part(rowFor(page, never.key), "comp-stitch-badge"),
      "a comp that was never re-listed is showing a re-list badge",
    ).toHaveCount(0);
  });

  test("AC11: a withdrawal-suspect comp is marked, and only that comp", async ({ page }) => {
    // NORTH_STAR: display-only. The badge casts doubt on whether the "lease"
    // happened; the human makes the call. So it must be visible, and it must
    // NOT be attached to comps the server did not flag.
    const payload = await gotoResults(page);
    const suspect = findComp(payload, (c: any) => c.withdrawal_suspect, "withdrawal-suspect");
    await expect(
      part(rowFor(page, suspect.key), "comp-withdrawal-suspect"),
      "a comp that completed a spell and re-appeared 6w–6mo later carries no warning",
    ).toBeVisible({ timeout: 10_000 });

    const clean = findComp(payload, (c: any) => !c.withdrawal_suspect, "not withdrawal-suspect");
    await expect(
      part(rowFor(page, clean.key), "comp-withdrawal-suspect"),
      "an unflagged comp is showing the withdrawal-suspect badge",
    ).toHaveCount(0);
  });

  test("AC12: a row expands to detail with a Zillow deep link and a Street View link", async ({
    page,
  }) => {
    // §6.4: RentCast ships no photos and no listing URLs, so these two links
    // are the ENTIRE visual-inspection path — the thing F5's flow step 4 has
    // the user adjust a weight from.
    const payload = await gotoResults(page);
    const comp = findComp(
      payload,
      (c: any) => c.lat !== null && c.lng !== null,
      "placeable (has coordinates)",
    );
    const row = rowFor(page, comp.key);
    await expect(row).toBeVisible({ timeout: 10_000 });

    await expect(
      part(row, "comp-detail"),
      "every row's detail panel is open before anything is clicked — 567 expanded rows is " +
        "not a list",
    ).toHaveCount(0);

    await part(row, "comp-expand").click();
    await expect(part(row, "comp-detail")).toBeVisible();

    const zillow = part(row, "comp-zillow");
    await expect(zillow).toBeVisible();
    const zillowHref = (await zillow.getAttribute("href")) ?? "";
    expect(zillowHref, `Zillow href: ${zillowHref}`).toMatch(/zillow\.com\/homes\//i);
    expect(zillowHref, "§6.4 pins the `_rb/` deep-link form").toMatch(/_rb\/?/i);
    const firstWord = comp.address.split(/\s+/)[0].toLowerCase();
    expect(
      zillowHref.toLowerCase(),
      `the Zillow link does not name this comp's address (${comp.address}) — a deep link to ` +
        "the wrong home is worse than no link",
    ).toContain(firstWord);

    const streetview = part(row, "comp-streetview");
    await expect(streetview).toBeVisible();
    const svHref = (await streetview.getAttribute("href")) ?? "";
    expect(svHref, `Street View href: ${svHref}`).toMatch(/google/i);
    expect(
      svHref,
      `the Street View link does not carry this comp's coordinates (${comp.lat}, ${comp.lng})`,
    ).toContain(comp.lat.toFixed(4));
    expect(svHref).toContain(comp.lng.toFixed(4));

    // Collapsing again — an expand control that only expands turns the list
    // into a one-way ratchet.
    await part(row, "comp-expand").click();
    await expect(part(row, "comp-detail")).toHaveCount(0);
  });

  test("AC12b: expanding one row does not expand every row", async ({ page }) => {
    const payload = await gotoResults(page);
    const first = findComp(payload, (c: any) => c.lat !== null, "placeable");
    await part(rowFor(page, first.key), "comp-expand").click();
    await expect(part(rowFor(page, first.key), "comp-detail")).toBeVisible();
    expect(
      await page.locator("[data-testid='comp-detail']").count(),
      "expanding one row expanded others — the panel is sharing one piece of state across " +
        "the whole list",
    ).toBe(1);
  });
});

// ---------------------------------------------------------------------------
// AC15 — D5/D13, checked in the source rather than in a browser
// ---------------------------------------------------------------------------

/**
 * A structural test, not a browser one. It lives in this file because the
 * repo has no other JavaScript-side test harness for the view layer, and
 * because `f4s6-derive-targets-the-users-pull.spec.ts` already established the
 * precedent (its AC8d walks `frontend/src` for a forbidden literal).
 *
 * ⚠ This is a NET, not a proof. AC15's real gate is the PM's
 * `frontend-reviewer` + `backend-reviewer` pass — a review can see intent,
 * and a regex cannot. What this catches is the specific, likely mistakes this
 * story invites: dividing the ask by the sqft (AC5), and subtracting two
 * dates to get "day 21" (AC9). Both were live temptations at dispatch.
 *
 * ⚠ SCOPED, TWICE, ON PURPOSE — an over-broad version of this test is worse
 * than none, because it goes red on innocent code and gets deleted:
 *   1. Only files that RENDER A COMP ROW are scanned. `views/search/*` builds
 *      the search form's default date window with `new Date()`, which is
 *      correct (there is no server round trip to ask, and F2-S1 owns it), and
 *      the anchor panel legitimately prints the string "$/sqft".
 *   2. Comments and the tails of URLs are stripped before matching, so prose
 *      explaining the rule is not itself a violation of it.
 */
function sourceFiles(dir: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir)) {
    const full = path.join(dir, entry);
    if (statSync(full).isDirectory()) {
      out.push(...sourceFiles(full));
    } else if (/\.tsx?$/.test(entry) && !/\.test\.tsx?$/.test(entry) && !/\.d\.ts$/.test(entry)) {
      out.push(full);
    }
  }
  return out;
}

/** Source with block and line comments blanked out, line numbering preserved. */
function withoutComments(source: string): string[] {
  const withoutBlocks = source.replace(/\/\*[\s\S]*?\*\//g, (match) =>
    match.replace(/[^\n]/g, " "),
  );
  return withoutBlocks.split("\n").map((line) => line.replace(/\/\/.*$/, ""));
}

/** Files that render a comp row — the only ones this rule is about. */
function compRowSources(): string[] {
  return sourceFiles(FRONTEND_SRC).filter((file) => {
    if (file.includes(`${path.sep}api${path.sep}`)) return false;
    const source = readFileSync(file, "utf-8");
    return /comp-row|CompRow|DerivedComp/.test(source);
  });
}

test("AC15: the view formats server fields — it does not compute or subtract dates", () => {
  expect(existsSync(FRONTEND_SRC), `${FRONTEND_SRC} does not exist`).toBe(true);
  const files = compRowSources();
  expect(
    files.length,
    "no file under frontend/src renders a comp row — this net has nothing to scan, which " +
      "means it would pass no matter what the developer wrote",
  ).toBeGreaterThan(0);

  const offences: string[] = [];
  for (const file of files) {
    withoutComments(readFileSync(file, "utf-8")).forEach((line, index) => {
      const where = `${path.relative(REPO_ROOT, file)}:${index + 1}`;
      if (/\bnew Date\s*\(|\bDate\.parse\s*\(|\.getTime\s*\(/.test(line)) {
        offences.push(
          `${where} date arithmetic where a comp row is rendered (D15 — the day offset and ` +
            `days_since_removal ship as numbers precisely so this is unnecessary): ${line.trim()}`,
        );
      }
      // The divisor has to be a PROPERTY access (`comp.sqft`, `c.sqft`), not
      // the bare word: `${anchor.psf.mid.toFixed(2)}/sqft)` and the sentence
      // "no selected comp has a $/sqft" are both text, and both would trip a
      // looser rule. A real violation reads `comp.initial_ask / comp.sqft`.
      if (/\binitial_ask\s*\//.test(line) || /\/\s*\w+\.sqft\b/.test(line)) {
        offences.push(`${where} computing $/sqft in the view (D5 — use \`psf\`): ${line.trim()}`);
      }
      if (/\b(days_since_removal|day_offset|first_listed)\s*[-+]\s*\w/.test(line)) {
        offences.push(`${where} arithmetic on a server-shipped count (D5): ${line.trim()}`);
      }
    });
  }
  expect(
    offences,
    "the view is deriving instead of rendering. Every one of these has a server-side field " +
      "that already holds the answer — that is why they were shipped as numbers",
  ).toEqual([]);
});
