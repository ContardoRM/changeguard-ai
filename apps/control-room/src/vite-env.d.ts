/// <reference types="vite/client" />

/** Injected by vite.config.ts's `define` at build time. "live" only when
 * started via `npm run dev:live`; "fixture" otherwise (the default). */
declare const __CONTROL_ROOM_MODE__: "fixture" | "live";
