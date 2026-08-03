/**
 * F2 · New Search — Layer 3 (Playwright). QA-authored, written RED before any
 * implementation exists (AGENT_QA.md protocol).
 *
 * Everything here is browser-genuine per the AGENT_QA.md decision procedure:
 * a component has to mount, a real click has to happen, inline validation has
 * to surface in the DOM, and a request has to be blocked (or not) as a
 * *consequence* of that click. The arithmetic this story adds — the call
 * estimate, the cohort plan, the config translation — is asserted exhaustively
 * at L1/L2 (`backend/tests/api/test_f2s1_search_preview.py`,
 * `test_f2s1_config_route.py`, `test_f2s1_settings_key_route.py`,
 * `backend/tests/unit/test_query_planner_dev.py`). The split rule applies:
 * this file asserts ONE number flows through to the screen, not twelve.
 *
 * WHAT IS DELIBERATELY NOT HERE
 * ------------------------------
 * * No assertion that comps render. In fixture mode an arbitrary address has
 *   no saved response, so a pull comes back incomplete by construction; comps
 *   rendering is WS-1's spec and F4-S6's story.
 * * No parametrized sweeps. Ten radius values are an L2 loop, not ten browser
 *   sessions.
 *
 * THE HARNESS FAILS LOUDLY, IT DOES NOT SKIP QUIETLY
 * ---------------------------------------------------
 * `the local-server harness is available` is a real test, not a `test.skip`
 * guard. The existing specs in this suite set a skip reason when `.venv` or
 * the frontend build is missing, which makes a whole run report
 * "13 passed, 10 skipped" and exit 0 — a green gate that ran none of the
 * assertions (this repo's documented worktree failure mode, 2026-07-28). One
 * named failure is worth more than ten silent skips.
 *
 * MARKUP CONTRACT (QA-PROPOSED — the developer may satisfy any of the
 * fallbacks instead). Every locator below tries a `data-testid` first and
 * falls back to an accessible role/label, the technique
 * `ws1-results-view.spec.ts` and `f0-s1b-integration.spec.ts` already use:
 *
 *   nav-new-search · search-form · search-address · search-bedrooms ·
 *   search-bathrooms · search-sqft · search-radius · search-window-start ·
 *   search-window-end · search-years-back · search-property-types ·
 *   search-source-mode · search-preview · search-submit ·
 *   search-error-* (inline blocking errors) · search-warning-* (soft, allow) ·
 *   nav-settings · settings-api-key · settings-api-key-save ·
 *   settings-api-key-status
 *
 * ZERO LIVE RENTCAST CALLS (D17, WORKFLOW.md §6): `RENTCOMP_LIVE` is never
 * set, `RENTCOMP_HOME` is a fresh temp dir, and no API key is configured for
 * the search half of this file.
 *
 * ===========================================================================
 * PORT: EPHEMERAL, NOT 8000 (QUEUE.md row 44a)
 * ===========================================================================
 * This file used to spawn the `rentcomp` console script on the hardcoded
 * `127.0.0.1:8000` with no identity check. Moved to the WORKFLOW.md §2
 * ratified pattern — bind a free high port, spawn `rentcomp.app:app` directly
 * under uvicorn, and check both that our own child process is still alive and
 * that its startup banner names our port, before the first test and again
 * after the last.
 */
import { test, expect, type Page, type Locator } from "@playwright/test";
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

/** `pip install -e .` resolves against the MAIN checkout — without this a
 * worktree run silently tests the wrong branch (WORKFLOW.md §2). */
const BACKEND_SRC = path.join(REPO_ROOT, "backend", "src");

/**
 * NO `test.describe.configure({ mode: "serial" })` — removed under QUEUE row
 * 44a (PM-authorised).
 *
 * It bailed every test after the first failure, so one stale assertion in the
 * prefill test reported as `1 failed, 11 did not run` — and "did not run" is
 * the same family as the silent skip WORKFLOW.md §2 records repeated
 * recurrences of: it hides how much of the suite is actually red, which is not
 * a number anyone can merge on.
 *
 * It bought nothing: `playwright.config.ts` already pins `workers: 1,
 * fullyParallel: false`, and every test below opens its own form via
 * `openSearchForm` (which re-navigates from scratch) — none depends on
 * another's state.
 */

let serverProcess: ChildProcess | null = null;
let rentcompHome: string | null = null;
let setupFailure: string | null = null;
let openApiPaths: string[] = [];
let BASE_URL = "";
let SEARCH_URL = "";
let serverPort = 0;
let serverLog = "";

/** The live-preview route (F2-S3, QA-proposed — see the L2 file). */
const PLAN_PATH = "/api/search/plan";

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
      // not up yet
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
  if (!existsSync(FRONTEND_DIR)) {
    setupFailure = "frontend/ does not exist";
    return;
  }
  if (!existsSync(VENV_PYTHON)) {
    setupFailure =
      `${VENV_PYTHON} not found — expected the repo-root .venv, or RENTCOMP_VENV_DIR ` +
      "pointing at it when running from a worktree (WORKFLOW.md §2)";
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
  SEARCH_URL = `${BASE_URL}/api/search`; // the only route that can spend money (F4-S9)
  rentcompHome = mkdtempSync(path.join(tmpdir(), "rentcomp-f2s1-home-"));

  const env = { ...process.env } as NodeJS.ProcessEnv;
  env.RENTCOMP_HOME = rentcompHome;
  env.RENTCOMP_LIVE = ""; // never live (D17)
  env.RENTCAST_API_KEY = ""; // the Settings half must start from "no key"
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

  const up = await waitForServer(`${BASE_URL}/openapi.json`, 20_000);
  if (!up) {
    setupFailure = `server did not come up on ${BASE_URL} within 20s. Output:\n${serverLog.slice(-800)}`;
    return;
  }
  try {
    const doc = (await (await fetch(`${BASE_URL}/openapi.json`)).json()) as {
      paths?: Record<string, unknown>;
    };
    openApiPaths = Object.keys(doc.paths ?? {});
  } catch (err) {
    setupFailure = `could not read /openapi.json: ${err}`;
  }
});

test.afterAll(() => {
  if (serverProcess) {
    serverProcess.kill();
    serverProcess = null;
  }
  if (rentcompHome) {
    rmSync(rentcompHome, { recursive: true, force: true });
    rentcompHome = null;
  }
});

function skipIfSetupFailed() {
  test.skip(!!setupFailure, setupFailure ?? "");
}

// ---------------------------------------------------------------------------
// tolerant locators — no markup the developer has not chosen yet is pinned
// ---------------------------------------------------------------------------

function byTestIdOr(page: Page, testId: string, fallback: Locator): Locator {
  return page.locator(`[data-testid='${testId}']`).or(fallback);
}

function field(page: Page, testId: string, label: RegExp): Locator {
  return byTestIdOr(page, testId, page.getByLabel(label)).first();
}

async function openSearchForm(page: Page) {
  await page.goto(`${BASE_URL}/`);
  const entry = byTestIdOr(
    page,
    "nav-new-search",
    page.getByRole("button", { name: /new search/i }).or(page.getByRole("link", { name: /new search/i })),
  );
  await entry.first().click();
  await expect(
    byTestIdOr(page, "search-form", page.getByRole("form")).first(),
    "clicking NEW SEARCH must open the search form (F2 step 1, spec §6.3)",
  ).toBeVisible({ timeout: 10_000 });
}

/** A complete, valid set of §2.1 inputs. One helper, so every test differs
 *  only in the field it is actually about. */
async function fillValidSearch(page: Page, overrides: Record<string, string> = {}) {
  const values: Record<string, string> = {
    address: "3651 S Wood St, Chicago, IL 60609",
    bedrooms: "3",
    bathrooms: "1",
    sqft: "1200",
    radius: "2",
    "window-start": "06-15",
    "window-end": "06-30",
    ...overrides,
  };
  const labels: Record<string, RegExp> = {
    address: /address/i,
    bedrooms: /bed/i,
    bathrooms: /bath/i,
    sqft: /sq\.? ?ft|square/i,
    radius: /radius/i,
    "window-start": /window start|start/i,
    "window-end": /window end|end/i,
  };
  for (const [name, value] of Object.entries(values)) {
    if (value === "") continue;
    await field(page, `search-${name}`, labels[name]).fill(value);
  }
}

function submitButton(page: Page): Locator {
  return byTestIdOr(
    page,
    "search-submit",
    page.getByRole("button", { name: /search|submit|pull/i }),
  ).first();
}

/** Records every POST to the money route. `[]` after a click is the proof
 *  that submit was actually blocked, not merely visually discouraged. */
function recordSearchPosts(page: Page): string[] {
  const seen: string[] = [];
  page.on("request", (request) => {
    if (request.method() === "POST" && request.url().replace(/\/$/, "") === SEARCH_URL) {
      seen.push(request.url());
    }
  });
  return seen;
}

async function inlineErrors(page: Page): Promise<Locator> {
  return page.locator("[data-testid^='search-error']").or(page.getByRole("alert"));
}

/** One observation per preview response, in arrival order. */
interface PlanObservation {
  status: number;
  estimatedCalls: number | null;
}

/**
 * Records EVERY `POST /api/search/plan` response, newest last.
 *
 * Why not `page.waitForResponse`: that resolves on the *first* response, and
 * the preview effect is debounced with one `AbortController` per request — so
 * the number on screen is the number from the LAST response, which is not
 * necessarily the first. Filling the form usually produces exactly one request
 * (every field lands inside the 400ms debounce), but "usually" is a flake:
 * whenever an inter-fill gap exceeds the debounce, an earlier request goes out
 * carrying the form's *default* window rather than the one this spec fills, and
 * the two disagree on days when the filled window is structurally empty for the
 * current year and the default one is not. Comparing against the latest
 * response is both flake-free and the stronger assertion — latest-wins is the
 * D13 property this preview is supposed to have. (Mechanism identified by the
 * F2-S1 developer; the defect was in this spec, not in the implementation.)
 */
function recordPlanResponses(page: Page): PlanObservation[] {
  const seen: PlanObservation[] = [];
  page.on("response", (res) => {
    if (!res.url().includes(PLAN_PATH) || res.request().method() !== "POST") return;
    // Pushed synchronously so arrival order is exact; the body is filled in
    // when it resolves.
    const entry: PlanObservation = { status: res.status(), estimatedCalls: null };
    seen.push(entry);
    void res
      .json()
      .then((body: { estimated_calls?: number }) => {
        entry.estimatedCalls = typeof body?.estimated_calls === "number" ? body.estimated_calls : null;
      })
      .catch(() => {
        /* superseded or non-JSON — left null, the poll below simply waits */
      });
  });
  return seen;
}

// ===========================================================================
// harness — loud, never a silent skip
// ===========================================================================

test.describe("F2 · harness", () => {
  test("the local-server harness is available (a skip here would be a false green)", () => {
    expect(
      setupFailure,
      "the live-server harness could not start, so every spec below would SKIP while the run " +
        "still exits 0. That is this repo's documented false-green mode (INCIDENT #5, " +
        "2026-07-28): `13 passed, 10 skipped` has been reported as green before. Fix the " +
        "harness or report the leg as un-runnable — do not read the exit code as a pass.",
    ).toBeNull();
  });

  test("the spawned server is serving THIS tree's code, not the main checkout's", () => {
    skipIfSetupFailed();
    expect(
      openApiPaths,
      "the running server's route table does not contain the route this story adds, so either " +
        "the story is not implemented yet (expected while red) or — the dangerous case — the " +
        "editable console script resolved to the MAIN checkout's backend/src and this whole " +
        "file just tested somebody else's code.",
    ).toContain(PLAN_PATH);
  });

  // ⚠ The check above proves nothing about PORT identity — any branch's server
  // satisfies "route exists in openapi.json" (row 44a's own finding, F6-S1's
  // QA verify: 2026-07-31). This is the check that actually rules out another
  // agent's build answering on our port.
  test("the harness stood up a server of our own, on a port nobody else holds", () => {
    expect(
      serverIdentityProblem(),
      "the server this spec is about to test is not the one it started",
    ).toBeNull();
  });
});

// ===========================================================================
// §2.1 — the form carries every field, with the spec's defaults
// ===========================================================================

test.describe("F2 · New Search form", () => {
  test.beforeEach(skipIfSetupFailed);

  test("NEW SEARCH opens a form carrying every §2.1 field", async ({ page }) => {
    await openSearchForm(page);

    const expected: [string, string, RegExp][] = [
      ["address", "search-address", /address/i],
      ["bedrooms", "search-bedrooms", /bed/i],
      ["bathrooms", "search-bathrooms", /bath/i],
      ["unit sqft", "search-sqft", /sq\.? ?ft|square/i],
      ["property types", "search-property-types", /property type/i],
      ["radius", "search-radius", /radius/i],
      ["date window start", "search-window-start", /window start|start/i],
      ["date window end", "search-window-end", /window end|end/i],
      ["years back", "search-years-back", /years? back/i],
      ["comp source mode", "search-source-mode", /source|exact|rentcast/i],
    ];
    for (const [name, testId, label] of expected) {
      await expect(
        field(page, testId, label),
        `spec §2.1 field "${name}" is missing from the search form — the story's AC is "All ` +
          `§2.1 fields"`,
      ).toBeVisible();
    }
  });

  test("property types, years back and comp source carry the spec's defaults", async ({ page }) => {
    await openSearchForm(page);

    // §2.1: "Default: Multi-Family + Apartment + Townhouse" — Apartment is in
    // the set on purpose (unit-level listings in 2-4 flats are frequently
    // typed "Apartment" in RentCast's taxonomy), so a default that drops it
    // silently narrows every pull.
    const types = byTestIdOr(page, "search-property-types", page.getByLabel(/property type/i)).first();
    const selected = await types.innerText().catch(() => "");
    const checked = page.locator(
      "[data-testid='search-property-types'] input:checked, [data-testid='search-property-types'] option:checked",
    );
    const checkedLabels = (await checked.count())
      ? (await checked.evaluateAll((nodes) =>
          nodes.map((n) => (n as HTMLInputElement).value || n.textContent || ""),
        )).join(" ")
      : selected;
    for (const type of ["Multi-Family", "Apartment", "Townhouse"]) {
      expect(
        checkedLabels,
        `property types must default to Multi-Family + Apartment + Townhouse (§2.1); ` +
          `"${type}" is not among the defaults`,
      ).toContain(type);
    }

    await expect(
      field(page, "search-years-back", /years? back/i),
      "§2.1: years back defaults to 2",
    ).toHaveValue("2");

    const mode = field(page, "search-source-mode", /source|exact|rentcast/i);
    const modeValue = ((await mode.inputValue().catch(() => "")) || (await mode.innerText().catch(() => "")))
      .toLowerCase();
    expect(
      modeValue,
      "comp source mode must default to Exact — the /listings pipeline is the primary and " +
        "only evidence source (NORTH_STAR: /avm is RentCast's own model output, a benchmark, " +
        "never the pipeline). The backend-side guard is " +
        "backend/tests/unit/test_rentcast_client.py::test_the_client_package_never_mentions_the_avm_endpoint",
    ).toContain("exact");
  });

  test("re-opening NEW SEARCH prefills the subject rather than asking for it again", async ({
    page,
  }) => {
    // "Prefill from subject where sensible" is the story's own wording, and
    // §2.1 adds "default exact-match to subject" for beds and baths. On a
    // FIRST search there is no prior subject — the form IS where the subject
    // is defined — so the only reading with a testable consequence is the
    // returning-user one: the fields the user just defined come back, instead
    // of an address and a sqft they have to retype to change one number.
    // (The other reading — that the pull's beds/baths filter defaults to the
    // subject's own beds/baths — is a single-field form in this design, so it
    // is satisfied by construction. Flagged to the PM as an AC ambiguity.)
    await openSearchForm(page);
    await fillValidSearch(page);

    /**
     * ⚠ WAIT FOR THE SEARCH TO FINISH, NOT FOR THE FORM TO DISAPPEAR.
     *
     * This used to be `expect(search-form).toBeHidden()`, which meant "the
     * search finished" only for as long as the form stayed on screen for the
     * duration of the request. F4-S6 added the progress surface — `SearchForm`
     * now returns `<PipelineProgress/>` while `submitting` — so the form
     * vanishes the instant the request *starts*, and this test raced ahead and
     * called `page.goto("/")` before the response landed.
     *
     * That matters because `saveSearchValues(values)` runs *after*
     * `await postJson("/api/search")` resolves, so the reload discarded the
     * pending write and the reopened form was blank. The assertion's intent was
     * never wrong; only its trigger went stale.
     *
     * So wait for the two things that are actually load-bearing, in order:
     * the response arrives, and then the app *acts* on it by landing the user
     * on Results (F4 flow step 4, `App.openPull`) — which happens strictly
     * after the values are persisted. Waiting on the response alone would still
     * race the continuation that writes them.
     */
    const searchFinished = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        response.url().replace(/\/$/, "") === SEARCH_URL,
      { timeout: 20_000 },
    );
    await submitButton(page).click();
    await searchFinished;
    await expect(
      page.getByRole("heading", { name: /^results$/i }),
      "the search never landed the user on Results, so it did not complete successfully and " +
        "nothing was ever persisted — this test cannot say anything about prefill until it does",
    ).toBeVisible({ timeout: 15_000 });

    await openSearchForm(page);
    await expect(
      field(page, "search-address", /address/i),
      "re-opening NEW SEARCH lost the subject. A user narrowing a radius or widening a window " +
        "should not have to retype an address, beds, baths and sqft to change one number",
    ).toHaveValue(/3651 S Wood St/);
    await expect(field(page, "search-sqft", /sq\.? ?ft|square/i)).toHaveValue("1200");
  });

  test("property type defaults are editable, not fixed", async ({ page }) => {
    await openSearchForm(page);
    const townhouse = page
      .getByRole("checkbox", { name: /townhouse/i })
      .or(page.getByRole("option", { name: /townhouse/i }))
      .or(page.locator("[data-testid='search-property-types'] input[value*='Townhouse']"));
    await expect(townhouse.first(), "§2.1: property types are a multi-select, 'editable'").toBeVisible();
    await townhouse.first().click();
    await expect(townhouse.first()).not.toBeChecked();
  });
});

// ===========================================================================
// inline validation — the F2 "Edges" row, every branch
// ===========================================================================

test.describe("F2 · validation blocks the spend", () => {
  test.beforeEach(skipIfSetupFailed);

  test("an empty address shows an inline error and blocks submit", async ({ page }) => {
    await openSearchForm(page);
    const posts = recordSearchPosts(page);
    await fillValidSearch(page, { address: "" });
    await submitButton(page).click();

    await expect(
      (await inlineErrors(page)).first(),
      "F2 edge: address required — the error must be inline, next to the field (spec §6.3 is " +
        "a modal; there is nowhere else to put it)",
    ).toBeVisible({ timeout: 5_000 });
    expect(
      posts,
      "submit was not blocked — a POST /api/search went out with no address. On a cache miss " +
        "that route PULLS, so an invalid submit is a real spend against a 50-call month",
    ).toEqual([]);
  });

  test("an empty unit sqft shows an inline error and blocks submit", async ({ page }) => {
    await openSearchForm(page);
    const posts = recordSearchPosts(page);
    await fillValidSearch(page, { sqft: "" });
    await submitButton(page).click();

    await expect(
      (await inlineErrors(page)).first(),
      "F2 edge: 'Sqft empty → block (anchor math requires it)'. Without subject sqft there is " +
        "no $/sqft to multiply the weighted median by, so the anchor cannot exist — this is a " +
        "block, not a warning",
    ).toBeVisible({ timeout: 5_000 });
    expect(posts, "an empty-sqft search must never reach the pull route").toEqual([]);
  });

  test("bedrooms min > max is surfaced inline and never dispatched as-is", async ({ page }) => {
    await openSearchForm(page);
    const posts = recordSearchPosts(page);
    await fillValidSearch(page, { bedrooms: "4-2" });
    await submitButton(page).click();

    // F2 edge says "inline swap-or-error", and the PM already ruled (F2-S2,
    // 2026-07-27) that the PARSER refuses rather than reorders. Either UI
    // resolution is acceptable; what is not is dispatching 4-2 unchanged.
    const errored = await (await inlineErrors(page)).first().isVisible().catch(() => false);
    const swapped = await field(page, "search-bedrooms", /bed/i)
      .inputValue()
      .then((value) => /^\s*2\s*[-:]\s*4\s*$/.test(value))
      .catch(() => false);
    expect(
      errored || swapped,
      "min > max must be resolved at the form: either an inline error (and no dispatch) or a " +
        "visible swap to 2-4. client/beds_baths.py::parse_bed_bath_field refuses to reorder " +
        "its own input, so silently sending '4-2' produces a 422 the user never sees",
    ).toBe(true);
    if (errored && !swapped) {
      expect(posts, "an invalid range must not be dispatched").toEqual([]);
    }
  });

  test("an absurd radius warns but still allows submit", async ({ page }) => {
    await openSearchForm(page);
    const posts = recordSearchPosts(page);
    await fillValidSearch(page, { radius: "75" });

    const warning = page
      .locator("[data-testid^='search-warning']")
      .or(page.getByText(/wide|large|unusual|are you sure|far/i));
    await expect(
      warning.first(),
      "F2 edge: 'Absurd radius/window → soft warning, allow'. A 75-mile comp radius in a " +
        "Chicago micro-market is almost certainly a slip, but the tool does not know the " +
        "user's intent and must not decide for them",
    ).toBeVisible({ timeout: 5_000 });

    await submitButton(page).click();
    await expect
      .poll(() => posts.length, {
        message:
          "the soft warning became a block — 'allow' is the explicit half of this edge, and " +
          "it is the half that distinguishes it from the address/sqft rows",
        timeout: 10_000,
      })
      .toBeGreaterThan(0);
  });
});

// ===========================================================================
// F2 step 3 — the live cohort-plan preview
// ===========================================================================

test.describe("F2 · cohort plan preview", () => {
  test.beforeEach(skipIfSetupFailed);

  test("the preview line renders the cohort plan and a call estimate before submit", async ({
    page,
  }) => {
    const observed = recordPlanResponses(page);
    await openSearchForm(page);
    await fillValidSearch(page);

    await expect
      .poll(() => observed.length, {
        message:
          "no POST /api/search/plan was made. The preview must come from the backend: D5/D13 " +
          "forbid the SPA computing a statistic, and the call estimate is the number the user " +
          "consents to spend against",
        timeout: 15_000,
      })
      .toBeGreaterThan(0);

    const preview = byTestIdOr(
      page,
      "search-preview",
      page.getByText(/cohort|est\.? .*call/i),
    ).first();
    await expect(preview).toBeVisible({ timeout: 10_000 });

    // ONE value flowing through proves the wiring (AGENT_QA.md split rule);
    // the estimate's CORRECTNESS is asserted exhaustively at L2 in
    // backend/tests/api/test_f2s1_search_preview.py.
    //
    // Compared against the LATEST response rather than the first: the effect is
    // debounced and aborts superseded requests, so the line on screen is by
    // construction the newest one's. Re-read inside the poll so it converges
    // once typing has settled instead of racing a request still in flight.
    await expect
      .poll(
        async () => {
          const latest = observed[observed.length - 1];
          if (!latest || latest.estimatedCalls === null) return false;
          const text = (await preview.innerText()).replace(/\s+/g, " ");
          return text.includes(String(latest.estimatedCalls));
        },
        {
          message:
            "the preview line does not show the call estimate from the most recent " +
            "/api/search/plan response — either the number is not rendered, or a superseded " +
            "response landed on screen after a newer one (D13 latest-wins)",
          timeout: 10_000,
        },
      )
      .toBe(true);

    expect(
      observed[observed.length - 1].status,
      "the preview that reached the screen must be a 200 from the backend",
    ).toBe(200);
  });

  test("the preview updates live when years back changes", async ({ page }) => {
    await openSearchForm(page);
    await fillValidSearch(page);
    const preview = byTestIdOr(page, "search-preview", page.getByText(/cohort|est\.? .*call/i)).first();
    await expect(preview).toBeVisible({ timeout: 10_000 });
    const before = (await preview.innerText()).replace(/\s+/g, " ");

    await field(page, "search-years-back", /years? back/i).fill("4");

    await expect
      .poll(async () => (await preview.innerText()).replace(/\s+/g, " "), {
        message:
          "F2 step 3 says the plan previews LIVE — going from 2 cohorts to 4 doubles what the " +
          "pull costs, and a stale line is the user consenting to the wrong number",
        timeout: 10_000,
      })
      .not.toBe(before);
    expect(
      (await preview.innerText()).replace(/\s+/g, " "),
      "the updated line must name the cohort years it now covers (spec §6.3: " +
        '"Jun 15-30 · 2026, 2025 (2 cohorts) · est. N API calls")',
    ).toMatch(/20\d\d/);
  });
});

// ===========================================================================
// submit — the flow completes, and a gap is never silent
// ===========================================================================

test.describe("F2 · submit", () => {
  test.beforeEach(skipIfSetupFailed);

  test("a valid search dispatches trimmed params and leaves the form", async ({ page }) => {
    await openSearchForm(page);
    const bodies: any[] = [];
    page.on("request", (request) => {
      if (request.method() === "POST" && request.url().replace(/\/$/, "") === SEARCH_URL) {
        try {
          bodies.push(JSON.parse(request.postData() ?? "{}"));
        } catch {
          bodies.push(request.postData());
        }
      }
    });

    // Whitespace a user cannot see, on the two fields where it does damage:
    // plan_pull_queries REJECTS "06-01 " outright (F4-S1 QA), and a padded
    // address hashes to a different cache key for a search the user considers
    // identical (F3-S1). PM ruling 3: this story trims the INPUT side.
    await fillValidSearch(page, {
      address: "  3651 S Wood St, Chicago, IL 60609  ",
      "window-start": "06-15 ",
    });
    await submitButton(page).click();

    await expect.poll(() => bodies.length, { timeout: 15_000 }).toBeGreaterThan(0);
    const body = bodies[0];
    expect(
      body.address,
      "the address reached POST /api/search untrimmed — PM ruling 3, and F3-S1's cache key is " +
        "computed over exactly this string",
    ).toBe(body.address?.trim?.());
    expect(
      body.window_start,
      'the month-day reached the planner untrimmed. plan_pull_queries refuses "06-01 " and ' +
        "that strictness is deliberate and QA-protected — the fix belongs on the input side",
    ).toBe(body.window_start?.trim?.());

    await expect(
      byTestIdOr(page, "search-form", page.getByRole("form")).first(),
      "a dispatched search must leave the form (F2 step 4 → F3 or F4); staying on it with no " +
        "feedback is the state where a user clicks submit twice and pays twice",
    ).toBeHidden({ timeout: 15_000 });
  });

  test("an address with no resolvable evidence names the gap instead of showing an empty view", async ({
    page,
  }) => {
    // The F2 edge is worded "address fails geocoding → inline error, block
    // submit", but a real geocode check IS a paid RentCast call, so it cannot
    // happen before submit (that is the AC ambiguity flagged to the PM). What
    // is testable, and is the honest form of the same requirement: an address
    // that yields no evidence must SAY so. In fixture mode an unknown address
    // has no saved response, so the pull returns complete=false with the
    // missing windows named — ARCHITECTURE §5a's "the gap is named loudly".
    await openSearchForm(page);
    await fillValidSearch(page, { address: "999 Nowhere Ave, Atlantis, XX 00000" });
    await submitButton(page).click();

    const notice = page
      .locator("[data-testid^='search-error'], [data-testid^='pull-warning'], [data-testid='pull-gap']")
      .or(page.getByRole("alert"))
      .or(page.getByText(/missing|no comps|nothing|incomplete|could not|widen/i));
    await expect(
      notice.first(),
      "an address that returns no evidence left the user on a silent view. Spec §7: 'Pull " +
        "returns 0 comps → suggest widening radius/window/types; show which constraint bound'; " +
        "ARCHITECTURE §5a: an incomplete pull is usable ONLY with the gap named loudly",
    ).toBeVisible({ timeout: 20_000 });
  });
});

// ===========================================================================
// Settings — F0-S4's "API key entered in Settings" half-AC (PM ruling 2)
// ===========================================================================

test.describe("Settings · API key entry", () => {
  test.beforeEach(skipIfSetupFailed);

  test("a key can be entered and saved, and the screen reports it configured", async ({ page }) => {
    // Never a real credential (CLAUDE.md: secrets never enter the repo), and
    // it is written only into this run's temp RENTCOMP_HOME.
    const fakeKey = "rc-fake-e2e-key-NOT-A-CREDENTIAL-0000";

    await page.goto(`${BASE_URL}/`);
    const settings = byTestIdOr(
      page,
      "nav-settings",
      page.getByRole("button", { name: /settings/i }).or(page.getByRole("link", { name: /settings/i })),
    );
    await expect(
      settings.first(),
      "there is no way to reach Settings. F0-S4's AC is 'API key entered in Settings' and its " +
        "storage half is already built and verified — this is the missing half (PM ruling 2)",
    ).toBeVisible({ timeout: 10_000 });
    await settings.first().click();

    const input = field(page, "settings-api-key", /api key/i);
    await expect(input).toBeVisible();
    await input.fill(fakeKey);
    await byTestIdOr(page, "settings-api-key-save", page.getByRole("button", { name: /save/i }))
      .first()
      .click();

    const status = byTestIdOr(
      page,
      "settings-api-key-status",
      page.getByText(/configured|saved|key set|stored/i),
    );
    await expect(
      status.first(),
      "saving must confirm on screen — otherwise the only way to know whether a key is stored " +
        "is to spend one of the month's 50 calls finding out",
    ).toBeVisible({ timeout: 10_000 });
  });

  test("a stored key is never rendered back into the page", async ({ page }) => {
    const fakeKey = "rc-fake-e2e-key-NOT-A-CREDENTIAL-0001";

    await page.goto(`${BASE_URL}/`);
    await byTestIdOr(page, "nav-settings", page.getByRole("button", { name: /settings/i }))
      .first()
      .click();
    const input = field(page, "settings-api-key", /api key/i);
    await input.fill(fakeKey);
    await byTestIdOr(page, "settings-api-key-save", page.getByRole("button", { name: /save/i }))
      .first()
      .click();

    await page.reload();
    await byTestIdOr(page, "nav-settings", page.getByRole("button", { name: /settings/i }))
      .first()
      .click();
    await expect(field(page, "settings-api-key", /api key/i)).toBeVisible({ timeout: 10_000 });

    const html = await page.content();
    expect(
      html.includes(fakeKey),
      "the stored API key was rendered back into the page after a reload. Presence is a " +
        "boolean (D16): the DOM ends up in devtools, in HAR exports and in this suite's own " +
        "retained failure traces",
    ).toBe(false);
  });
});

test("the server we tested against is still the one we started (checked again after the last test)", () => {
  expect(
    serverIdentityProblem(),
    "the server identity changed partway through this run — everything above may have been " +
      "answered by a different agent's build",
  ).toBeNull();
});
