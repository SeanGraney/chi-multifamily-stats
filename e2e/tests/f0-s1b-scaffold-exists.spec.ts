/**
 * F0-S1b [FE] Frontend scaffold — Layer 3-structural gate (QA, written
 * pre-implementation, AGENT_QA.md protocol step 3).
 *
 * This is the **root structural assertion** the other F0-S1b spec files
 * lean on. It is deliberately the only thing in this file so a missing
 * `frontend/` produces one legible failure per check, never a collection
 * error that hides the state of the rest of the suite (AGENT_QA.md, "commit
 * early and often" — legible red).
 *
 * Nothing here needs a browser or a running server — filesystem checks
 * only, hence no `page` fixture. See f0-s1b-structural.spec.ts for the
 * content-level checks (tokens, codegen wiring, no-TS-stat guard) that
 * build on the fact this file proves true.
 */
import { test, expect } from "@playwright/test";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";

const REPO_ROOT = path.resolve(__dirname, "../..");
const FRONTEND_DIR = path.join(REPO_ROOT, "frontend");

const HINT =
  "F0-S1b not implemented yet: {problem} (expected under frontend/, " +
  "ARCHITECTURE.md §2 — Vite + React 18 + TS + Tailwind)";

function fail(problem: string): never {
  expect(false, HINT.replace("{problem}", problem)).toBe(true);
  throw new Error("unreachable"); // satisfies TS control-flow analysis
}

test.describe("F0-S1b scaffold — root structural gate", () => {
  test("frontend/ directory exists", () => {
    if (!existsSync(FRONTEND_DIR)) fail("frontend/ directory does not exist");
  });

  test("frontend/package.json exists and declares the D6 stack", () => {
    const pkgPath = path.join(FRONTEND_DIR, "package.json");
    if (!existsSync(pkgPath)) fail("frontend/package.json does not exist");
    const pkg = JSON.parse(readFileSync(pkgPath, "utf-8"));
    const deps = { ...pkg.dependencies, ...pkg.devDependencies };
    for (const name of ["react", "react-dom", "vite", "typescript", "tailwindcss"]) {
      expect(
        deps[name],
        `frontend/package.json is missing a dependency on '${name}' (D6/ARCHITECTURE.md §1a)`
      ).toBeTruthy();
    }
  });

  test("frontend/package.json declares a build script producing frontend/dist", () => {
    const pkgPath = path.join(FRONTEND_DIR, "package.json");
    if (!existsSync(pkgPath)) fail("frontend/package.json does not exist");
    const pkg = JSON.parse(readFileSync(pkgPath, "utf-8"));
    expect(
      pkg.scripts?.build,
      "frontend/package.json must declare a 'build' script " +
        "(rentcomp.app._ui_dist_dir() resolves to <repo>/frontend/dist — D7)"
    ).toBeTruthy();
  });

  test("frontend/src exists", () => {
    if (!existsSync(path.join(FRONTEND_DIR, "src"))) {
      fail("frontend/src does not exist");
    }
  });

  test("frontend/src/views holds the three screen-graph views (Home/Results/Analysis)", () => {
    const viewsDir = path.join(FRONTEND_DIR, "src", "views");
    if (!existsSync(viewsDir)) {
      fail(
        "frontend/src/views does not exist (ARCHITECTURE.md §2 names this " +
          "directory explicitly for Home/Results/Analysis)"
      );
    }
  });

  test("frontend/src/api exists for generated types + useDerive (D12/D13)", () => {
    if (!existsSync(path.join(FRONTEND_DIR, "src", "api"))) {
      fail("frontend/src/api does not exist");
    }
  });
});
