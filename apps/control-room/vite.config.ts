import { defineConfig } from "vitest/config";
import type { Plugin } from "vite";
import react from "@vitejs/plugin-react";
import { createControlRoomProxyPlugin } from "./server/controlRoomProxyPlugin";

// ChangeGuard Control Room — Vite config.
//
// The `createControlRoomProxyPlugin()` plugin below runs ONLY inside Vite's
// Node.js dev server process — its code never ships to the browser bundle.
// It exists so the browser can read repository `artifacts/*.json` files and
// relay genuine Kiro Crew Gateway approval calls WITHOUT the browser ever
// holding a filesystem path outside this app or a Gateway secret/token
// (see server/controlRoomProxyPlugin.ts for the full security rationale).
export default defineConfig(() => {
  const plugins: Plugin[] = [react()];
  // Only registered when explicitly requested (VITE_CONTROL_ROOM_MODE=live,
  // set by `npm run dev:live`) -- default `npm run dev` never mounts the
  // proxy at all, so fixture mode has zero server-side surface.
  if (process.env.VITE_CONTROL_ROOM_MODE === "live") {
    plugins.push(createControlRoomProxyPlugin());
  }

  return {
    plugins,
    root: ".",
    server: {
      port: 5173,
    },
    define: {
      __CONTROL_ROOM_MODE__: JSON.stringify(
        process.env.VITE_CONTROL_ROOM_MODE === "live" ? "live" : "fixture",
      ),
    },
    test: {
      environment: "jsdom",
      globals: true,
      setupFiles: ["./src/test/setup.ts"],
    },
  };
});
