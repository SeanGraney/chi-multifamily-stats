/**
 * F0-S1b — Layer 3-structural content checks (QA, written pre-implementation).
 *
 * Three ACs land here rather than in a browser:
 *   1. Design tokens exist and are wired in the *shared Tailwind config*
 *      (not hardcoded hex scattered in components) — functional spec top
 *      of file, dispatch note "design tokens".
 *   2. `openapi-typescript` codegen is wired as a real script with committed
 *      output (D12), not a manual one-off.
 *   3. F0-S2's AC3, first enforcement here: "no statistic is computed
 *      anywhere in TypeScript." A grep-style guard per the dispatch note
 *      ("Write this as a structural review test... grep-style guard, or a
 *      documented manual review note, whichever actually holds the
 *      invariant").
 *
 * Why these live here and not in Layer 1/2 (pytest) or as a Vitest file:
 * they're assertions about TypeScript/config *source text*, which no
 * Python layer can see, and D23 reserves Vitest for the `useDerive` hook
 * only (no general component/config test suite). Playwright is the one JS
 * test runner this repo has standardized on (D22), so these run under it —
 * but as plain Node assertions with no `page` fixture and no server, which
 * is a materially different thing from the browser-genuine checks in
 * f0-s1b-integration.spec.ts. Flagging this explicitly in the handoff: it's
 * a judgment call, not a clean fit for the L1/L2/L3 procedure as written
 * (that procedure is Python-vs-browser shaped; there's no lower-than-L3 JS
 * layer defined for "does this repo's TS source content satisfy an
 * invariant" checks).
 *
 * The no-TS-stat grep is deliberately a heuristic denylist, not a proof —
 * documented as a review guard, same posture the dispatch note asks for.
 * It will need a human/`frontend-reviewer` look on every later FE story;
 * this file is the first checkpoint, not the last line of defense.
 */
import { test, expect } from "@playwright/test";
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import path from "node:path";

const REPO_ROOT = path.resolve(__dirname, "../..");
const FRONTEND_DIR = path.join(REPO_ROOT, "frontend");
const SRC_DIR = path.join(FRONTEND_DIR, "src");

function skipIfNoFrontend() {
  test.skip(
    !existsSync(FRONTEND_DIR),
    "frontend/ does not exist yet — see f0-s1b-scaffold-exists.spec.ts for the gate failure"
  );
}

function findTailwindConfig(): string | null {
  const candidates = [
    "tailwind.config.js",
    "tailwind.config.cjs",
    "tailwind.config.ts",
    "tailwind.config.mjs",
  ];
  for (const name of candidates) {
    const p = path.join(FRONTEND_DIR, name);
    if (existsSync(p)) return p;
  }
  return null;
}

function walk(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    if (entry === "node_modules" || entry === "dist" || entry === "api") continue;
    const full = path.join(dir, entry);
    const st = statSync(full);
    if (st.isDirectory()) {
      walk(full, out);
    } else if (/\.(ts|tsx)$/.test(entry)) {
      out.push(full);
    }
  }
  return out;
}

test.describe("F0-S1b — Tailwind design tokens (functional spec top of file)", () => {
  test.beforeEach(skipIfNoFrontend);

  test("a shared Tailwind config file exists", () => {
    const cfg = findTailwindConfig();
    expect(
      cfg,
      "no tailwind.config.{js,cjs,ts,mjs} found under frontend/ — tokens must be " +
        "wired in the shared theme config, not hardcoded hex in components"
    ).not.toBeNull();
  });

  test("dark surface background #0b0c0b is a declared token", () => {
    const cfg = findTailwindConfig();
    if (!cfg) test.skip(true, "no tailwind config to inspect");
    const text = readFileSync(cfg as string, "utf-8").toLowerCase();
    expect(text).toContain("0b0c0b");
  });

  test("comp-state palette tokens are declared: amber, green, grey, rust", () => {
    const cfg = findTailwindConfig();
    if (!cfg) test.skip(true, "no tailwind config to inspect");
    const text = readFileSync(cfg as string, "utf-8").toLowerCase();
    // f5a623 = amber/subject-anchor-emphasis, 52d48a = green/included,
    // 555 = grey/excluded, c45c2a = rust/filtered-hidden. Not exercised for
    // real comp data yet (dispatch note) — just confirming the values exist
    // so later stories (F5-S1 etc.) inherit them.
    for (const hex of ["f5a623", "52d48a", "c45c2a"]) {
      expect(text, `expected token ${hex} in tailwind config`).toContain(hex);
    }
    // grey #555 is short-hex and could false-positive on unrelated "555"
    // substrings (e.g. a port number) — require it in a hex-looking context.
    expect(
      /#555\b|['"]#555['"]|555555/.test(text),
      "expected excluded-grey (#555 / #555555) in tailwind config"
    ).toBe(true);
  });

  test("monospace type is declared in the shared theme, not ad hoc per component", () => {
    const cfg = findTailwindConfig();
    if (!cfg) test.skip(true, "no tailwind config to inspect");
    const text = readFileSync(cfg as string, "utf-8").toLowerCase();
    expect(
      text.includes("mono") || text.includes("monospace"),
      "expected a monospace font family entry in the Tailwind theme config"
    ).toBe(true);
  });
});

test.describe("F0-S1b — openapi-typescript codegen wiring (D12)", () => {
  test.beforeEach(skipIfNoFrontend);

  test("package.json declares a real codegen script invoking openapi-typescript", () => {
    const pkgPath = path.join(FRONTEND_DIR, "package.json");
    if (!existsSync(pkgPath)) test.skip(true, "no frontend/package.json");
    const pkg = JSON.parse(readFileSync(pkgPath, "utf-8"));
    const scripts: Record<string, string> = pkg.scripts ?? {};
    const codegenScript = Object.entries(scripts).find(([, cmd]) =>
      cmd.includes("openapi-typescript")
    );
    expect(
      codegenScript,
      "expected a package.json script invoking openapi-typescript (D12) — " +
        `found scripts: ${JSON.stringify(scripts)}`
    ).toBeTruthy();
    const depOnLib =
      pkg.dependencies?.["openapi-typescript"] ?? pkg.devDependencies?.["openapi-typescript"];
    expect(depOnLib, "openapi-typescript must be a declared dependency").toBeTruthy();
  });

  test("generated types are committed under frontend/src/api, not gitignored", () => {
    const apiDir = path.join(SRC_DIR, "api");
    if (!existsSync(apiDir)) test.skip(true, "frontend/src/api does not exist");
    const generated = readdirSync(apiDir).filter((f) => /\.(ts|d\.ts)$/.test(f));
    expect(
      generated.length,
      "expected at least one committed generated-types file under frontend/src/api/ " +
        "(D12 — codegen output is committed, not a build-time-only artifact)"
    ).toBeGreaterThan(0);

    // Committed, not gitignored: git ls-files must know about at least one of them.
    const { execSync } = require("node:child_process");
    const tracked = execSync("git ls-files frontend/src/api", { cwd: REPO_ROOT })
      .toString()
      .trim();
    expect(
      tracked.length,
      "frontend/src/api/ generated files exist on disk but none are tracked by git " +
        "(`git ls-files` returned nothing) — D12 requires committed output"
    ).toBeGreaterThan(0);
  });
});

test.describe("F0-S1b — F0-S2 AC3 first enforcement: no statistic computed in TypeScript", () => {
  test.beforeEach(skipIfNoFrontend);

  // Heuristic denylist — identifiers/patterns that would indicate a
  // statistic is being computed client-side instead of rendered from
  // `/api/derive`'s response (ARCHITECTURE.md D5). This is a review guard,
  // not a proof: a sufficiently obfuscated computation could dodge it. It's
  // the "first enforcement" the dispatch note asks for; standing practice
  // moves to a `frontend-reviewer` pass on every later FE story.
  const FORBIDDEN_PATTERNS: Array<{ re: RegExp; why: string }> = [
    { re: /weightedMedian/i, why: "weighted median must be computed in Python (D5)" },
    { re: /kaplanMeier|survivalCurve/i, why: "KM curve must be computed in Python (D5/D8)" },
    { re: /\bknn\b|nearestNeighbors?/i, why: "kNN retrieval must be computed in Python (D19)" },
    {
      re: /\.reduce\(\s*\([^)]*\)\s*=>\s*\w+\s*[+*]\s*\w+/,
      why: "a .reduce() combining numeric values looks like statistic computation, not display formatting",
    },
  ];

  test("no component/hook in frontend/src computes a statistic beyond display formatting", () => {
    if (!existsSync(SRC_DIR)) test.skip(true, "frontend/src does not exist");
    const files = walk(SRC_DIR);
    const offenders: string[] = [];
    for (const file of files) {
      const text = readFileSync(file, "utf-8");
      for (const { re, why } of FORBIDDEN_PATTERNS) {
        if (re.test(text)) {
          offenders.push(`${path.relative(REPO_ROOT, file)}: ${why} (matched ${re})`);
        }
      }
    }
    expect(
      offenders,
      "found TypeScript that looks like it computes a statistic rather than rendering " +
        `one /api/derive returned (D5, F0-S2 AC3):\n${offenders.join("\n")}`
    ).toEqual([]);
  });
});
