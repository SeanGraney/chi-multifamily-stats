/**
 * A built SPA on an ephemeral port, with every `/api/**` request fulfilled
 * from committed payloads.
 *
 * WHY NOT THE USUAL LIVE-SERVER HARNESS (`local-server.ts`)
 * ---------------------------------------------------------
 * Three reasons, in order of weight:
 *
 * 1. **The states under test are payload states.** F4-S6 is about what the
 *    view does with "one window never arrived" and "the pull found nothing".
 *    Arranging those through a real server means seeding a partial cache
 *    entry and a zero-record dataset per case; arranging them here is a JSON
 *    file. The *backend* facts behind those payloads are asserted where they
 *    belong — `backend/tests/api/test_f4s6_partial_pull_and_empty_state.py`
 *    produces both states through the real pull orchestrator.
 * 2. **Port 8000 is contended.** `local-server.ts` polls a hardcoded
 *    `127.0.0.1:8000`, so with a second Playwright-capable agent running, a
 *    spec can bind nothing, reach *the other agent's* build and report green
 *    (WORKFLOW.md §2, the false-green note). Listening on port 0 removes the
 *    failure mode instead of scheduling around it.
 * 3. **No `.venv` door.** Nothing here spawns `rentcomp`, so the worktree
 *    `rentcomp.exe` copy, `RENTCOMP_VENV_DIR` and `PYTHONPATH` cannot be got
 *    wrong — and a wrong one of those reports "did not run", which does not
 *    look like a red test.
 *
 * The stub's one real risk — a fixture that drifts from the wire it stands in
 * for — is closed at Layer 2:
 * `backend/tests/api/test_f4s6_stub_payloads_match_the_wire.py` validates
 * every file in `e2e/fixtures/f4s6/` against the Pydantic response models on
 * every pytest run.
 *
 * ZERO LIVE RENTCAST CALLS: no server, no key, no `RENTCOMP_LIVE`; the
 * fixtures were generated offline from committed sample data.
 */
import { execFileSync } from "node:child_process";
import { createReadStream, existsSync, readFileSync, statSync } from "node:fs";
import http from "node:http";
import path from "node:path";
import type { AddressInfo } from "node:net";
import type { Page, Request, Route } from "@playwright/test";
import { REPO_ROOT, NPM, NPM_NEEDS_SHELL } from "./local-server";

const FRONTEND_DIR = path.join(REPO_ROOT, "frontend");
const FRONTEND_DIST = path.join(FRONTEND_DIR, "dist");
export const FIXTURES_DIR = path.join(REPO_ROOT, "e2e", "fixtures", "f4s6");

const CONTENT_TYPES: Record<string, string> = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".svg": "image/svg+xml",
  ".json": "application/json; charset=utf-8",
  ".ico": "image/x-icon",
};

export interface StaticUi {
  baseUrl: string;
  close: () => Promise<void>;
}

/**
 * Build the SPA **of this checkout**. Not optional and not cached: the point
 * of a QA branch is to test the developer's code, and a stale `dist/` from
 * another branch is the browser-leg version of importing the wrong source
 * tree.
 */
export function buildUi(): void {
  if (!existsSync(FRONTEND_DIR)) {
    throw new Error(`frontend/ does not exist under ${REPO_ROOT}`);
  }
  execFileSync(NPM, ["install"], { cwd: FRONTEND_DIR, stdio: "pipe", shell: NPM_NEEDS_SHELL });
  execFileSync(NPM, ["run", "build"], { cwd: FRONTEND_DIR, stdio: "pipe", shell: NPM_NEEDS_SHELL });
  if (!existsSync(path.join(FRONTEND_DIST, "index.html"))) {
    throw new Error("npm run build completed but frontend/dist/index.html does not exist");
  }
}

/** Serve `frontend/dist` on an ephemeral port. */
export async function serveUi(): Promise<StaticUi> {
  const server = http.createServer((req, res) => {
    const url = new URL(req.url ?? "/", "http://localhost");
    let file = path.join(FRONTEND_DIST, decodeURIComponent(url.pathname));
    if (!file.startsWith(FRONTEND_DIST)) {
      res.writeHead(403).end();
      return;
    }
    if (!existsSync(file) || statSync(file).isDirectory()) {
      // Single-page app: unknown paths are the app's own routes.
      file = path.join(FRONTEND_DIST, "index.html");
    }
    res.writeHead(200, {
      "Content-Type": CONTENT_TYPES[path.extname(file)] ?? "application/octet-stream",
      "Cache-Control": "no-store",
    });
    createReadStream(file).pipe(res);
  });

  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const { port } = server.address() as AddressInfo;
  return {
    baseUrl: `http://127.0.0.1:${port}`,
    close: () =>
      new Promise<void>((resolve, reject) =>
        server.close((err) => (err ? reject(err) : resolve())),
      ),
  };
}

export function fixture(name: string): any {
  return JSON.parse(readFileSync(path.join(FIXTURES_DIR, name), "utf-8"));
}

export interface ApiStub {
  /** `POST /api/search/plan` */
  plan?: unknown;
  /** `POST /api/search` — a payload, or a function of the request body. */
  search?: unknown | ((body: any) => unknown);
  /** `POST /api/derive` */
  derive?: unknown | ((body: any) => unknown);
  /** Milliseconds to hold `POST /api/search` open, to make "in flight" real. */
  searchDelayMs?: number;
  /**
   * The ONLY `pull_ref` this stub will derive. A `POST /api/derive` for any
   * other ref gets a 404 — the same status the real route returns for a ref it
   * cannot resolve (`api/derive.py`: `PullNotFoundError` -> 404).
   *
   * WHY THIS DEFAULTS ON, AND WHY IT IS IN THE HARNESS RATHER THAN IN ONE TEST
   * -------------------------------------------------------------------------
   * Without it every test in this file is satisfiable by a view that derives
   * the WRONG pull. A stub that answers the same payload to any body will hand
   * a gap banner, a comp list and an anchor to a `Results` view holding a
   * hardcoded fixture ref — so the spec goes green while the screen shows the
   * analysis of evidence the user never searched for. That is the exact
   * failure NORTH_STAR exists to prevent (a confident output the evidence does
   * not support), and it is live on `main` today: `Results.tsx` holds
   * `const PULL_REF = "ws1-real"` and derives it unconditionally.
   *
   * Putting the check here rather than in a single test is the point. One test
   * would prove the seam is wired; this makes it impossible for any *other*
   * test in the file to accidentally certify the broken wiring as working.
   */
  deriveOnlyRef?: string;
}

export interface ApiCall {
  method: string;
  path: string;
  body: any;
}

/**
 * Fulfil every `/api/**` request from `stub`, and record what was asked.
 *
 * Recording matters as much as answering: several of F4-S6's acceptance
 * criteria are about the request a click *produces* (does a widen shortcut
 * actually widen; does a resume ask for a resume rather than a re-buy), which
 * is only observable from the outgoing body.
 */
export async function stubApi(page: Page, stub: ApiStub): Promise<ApiCall[]> {
  const calls: ApiCall[] = [];

  const bodyOf = (request: Request): any => {
    try {
      return request.postDataJSON();
    } catch {
      return null;
    }
  };

  const resolve = (value: unknown, body: any) =>
    typeof value === "function" ? (value as (b: any) => unknown)(body) : value;

  await page.route("**/api/**", async (route: Route, request: Request) => {
    const pathname = new URL(request.url()).pathname;
    const body = bodyOf(request);
    calls.push({ method: request.method(), path: pathname, body });

    const send = async (payload: unknown, delayMs = 0) => {
      if (delayMs > 0) await new Promise((r) => setTimeout(r, delayMs));
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(payload),
      });
    };

    if (pathname.endsWith("/api/search/plan") && stub.plan !== undefined) {
      await send(stub.plan);
      return;
    }
    if (pathname.endsWith("/api/search") && stub.search !== undefined) {
      await send(resolve(stub.search, body), stub.searchDelayMs ?? 0);
      return;
    }
    if (pathname.endsWith("/api/derive") && stub.derive !== undefined) {
      if (stub.deriveOnlyRef !== undefined && body?.pull_ref !== stub.deriveOnlyRef) {
        // Not an error in the harness — this IS the assertion. See
        // `ApiStub.deriveOnlyRef`.
        await route.fulfill({
          status: 404,
          contentType: "application/json",
          body: JSON.stringify({
            detail:
              `no pull named ${JSON.stringify(body?.pull_ref)}. This browser derived a ref ` +
              `the user never searched for; the only ref this test supplied is ` +
              `${JSON.stringify(stub.deriveOnlyRef)}.`,
          }),
        });
        return;
      }
      await send(resolve(stub.derive, body));
      return;
    }
    if (pathname.endsWith("/api/workspaces")) {
      await send([]);
      return;
    }
    if (pathname.endsWith("/api/config")) {
      await route.fulfill({ status: 404, contentType: "application/json", body: "{}" });
      return;
    }
    // Anything unstubbed answers 503 rather than reaching the network: an
    // unexpected call must be visible, not silent.
    await route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({ detail: `no stub for ${request.method()} ${pathname}` }),
    });
  });

  return calls;
}
