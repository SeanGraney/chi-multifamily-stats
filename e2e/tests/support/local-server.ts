/**
 * Platform-dependent bits of "spawn a real `rentcomp` and build the UI".
 *
 * Shared by every spec that stands up a live server (currently
 * `f0-s1b-integration.spec.ts` and `ws1-results-view.spec.ts`), which
 * otherwise duplicate these constants verbatim.
 *
 * Not collected as a test: Playwright's default `testMatch` only picks up
 * `*.spec.ts` / `*.test.ts`. It lives under `tests/` anyway because
 * `tsconfig.json` only type-checks `tests/**\/*.ts`.
 *
 * Why this file exists at all — both values are POSIX-only when written
 * literally, and both fail *silently* on Windows rather than loudly:
 *
 *   1. A venv's console scripts land in `bin/` on POSIX but `Scripts/` on
 *      Windows, with a `.exe` shim. A spec that probes `.venv/bin/rentcomp`
 *      with `existsSync` finds nothing, sets its skip-reason, and every
 *      live-server test skips — while Playwright still exits 0. A gate that
 *      reports success having run half the suite is worse than a red one.
 *
 *   2. On Windows `npm` is `npm.cmd`, and launching it needs BOTH fixes:
 *      `execFileSync` bypasses the shell so it never consults PATHEXT to
 *      resolve the bare name, and since Node 18.20.2/20.12.2 (CVE-2024-27980)
 *      `spawnSync` outright refuses a `.cmd`/`.bat` target without
 *      `shell: true`, throwing EINVAL rather than permitting a known
 *      argument-injection vector. So the shell is required here, not
 *      optional. That is safe in this specific case because every argument
 *      is a hardcoded literal (`install`, `run build`) — no interpolated or
 *      caller-supplied values reach the command line.
 *
 * All three are no-ops on POSIX: the non-win32 branches are byte-identical to
 * the literals they replaced, and `shell: false` is execFileSync's default.
 */
import path from "node:path";

const IS_WINDOWS = process.platform === "win32";

/** Repo root, resolved from this file's location (`e2e/tests/support/`). */
export const REPO_ROOT = path.resolve(__dirname, "../../..");

/**
 * The venv root — NOT always `REPO_ROOT`. An isolated worktree has no
 * `.venv` of its own (CLAUDE.md: the repo-root one is the project's ONLY
 * Python environment), so `f1-s2-recents.spec.ts` and
 * `f5-s2-selection-weight.spec.ts` already resolve this inline as
 * `process.env.RENTCOMP_VENV_DIR ?? REPO_ROOT`. This file — used by every
 * OTHER live-server spec — did not, and that gap is what let
 * `f0-s1b-integration`, `f2-s1-search-form` and `ws1-results-view` silently
 * SKIP (not false-green; a genuine, honestly-reported skip) in a worktree
 * with no `.venv` of its own, rather than fail loudly. Folded into row 44a
 * 2026-08-03 as the actual root cause of that day's live-workspace incident:
 * `f1-s2-recents` was the one spec that resolved the venv correctly AND had
 * no port-8000 identity check, so it alone reached a real, wrong server.
 * One-line fix — mirror what those two specs already do.
 */
const VENV_ROOT = process.env.RENTCOMP_VENV_DIR ?? REPO_ROOT;

/**
 * The `rentcomp` console script inside {@link VENV_ROOT}.
 */
export const VENV_RENTCOMP = path.join(
  VENV_ROOT,
  ".venv",
  IS_WINDOWS ? "Scripts" : "bin",
  IS_WINDOWS ? "rentcomp.exe" : "rentcomp",
);

/** Human-readable form of the above, for skip/failure messages. */
export const VENV_RENTCOMP_HINT = IS_WINDOWS
  ? ".venv\\Scripts\\rentcomp.exe"
  : ".venv/bin/rentcomp";

/** npm executable name. */
export const NPM = IS_WINDOWS ? "npm.cmd" : "npm";

/**
 * Whether launching {@link NPM} requires a shell. True only on Windows, where
 * Node refuses to spawn a `.cmd` without it (see note 2 above). Pass as
 * `execFileSync(NPM, args, { shell: NPM_NEEDS_SHELL, ... })`; on POSIX this is
 * `false`, which is already execFileSync's default.
 */
export const NPM_NEEDS_SHELL = IS_WINDOWS;
