import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// D7: build output must land at <repo>/frontend/dist — this is exactly
// where rentcomp.app._ui_dist_dir() resolves by default, so a different
// outDir would silently break static serving in production. "dist" (Vite's
// own default) already satisfies this; set explicitly so the intent is
// documented, not accidental.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "dist",
  },
});
