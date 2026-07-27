import type { Config } from "tailwindcss";

/**
 * Design tokens (functional spec, top of file) — shared theme config so
 * later stories (F5-S1 comp rows, etc.) consume the same values instead of
 * hardcoding hex scattered through components.
 *
 * - surface:  #0b0c0b — dark background, applied to <body> (see src/index.css)
 * - amber:    #f5a623 — subject / anchor / emphasis
 * - green:    #52d48a — included
 * - grey:     #555555 — excluded
 * - rust:     #c45c2a — filtered-hidden
 */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        surface: "#0b0c0b",
        amber: "#f5a623",
        green: "#52d48a",
        grey: "#555555",
        rust: "#c45c2a",
      },
      fontFamily: {
        mono: [
          "ui-monospace",
          "SFMono-Regular",
          "Menlo",
          "Consolas",
          "Liberation Mono",
          "monospace",
        ],
      },
    },
  },
  plugins: [],
} satisfies Config;
