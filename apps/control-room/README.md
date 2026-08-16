# ChangeGuard Control Room

An **optional** visualization/interaction layer over the existing ChangeGuard AI workflow. This app does not replace, modify, or duplicate any ChangeGuard core logic — the CLI/Makefile workflow documented in the repository root `README.md` continues to work fully whether or not this app is running.

The Control Room renders exactly what ChangeGuard's existing scripts and Kiro Crew Gateway already produce: candidate change → parallel Security + Reliability reviewers → `CHANGE_BLOCKED` → Human Approval Gate → Remediator → post-remediation parallel re-review → `SAFE_TO_SHIP`. It performs no SEC-001/SEC-002/REL-001/BR-001 policy judgment of its own — see `src/lib/changeguard/state.ts`'s module docstring for the "no policy in React" boundary this app is built around.

## Two modes

**Fixture mode (default)** — `npm run dev`. Renders eight hand-authored fixture states (`src/fixtures/controlRoomStates.ts`) for visual development. Makes zero network calls, and never contacts the Kiro Crew Gateway approval API — confirmed by `src/lib/changeguard/gateway.test.ts` and `src/hooks/useControlRoomState.test.ts`.

**Live mode** — `npm run dev:live`. Adds a small local dev-server proxy (`server/controlRoomProxyPlugin.ts`, Node-only, never bundled to the browser) that reads real `artifacts/*.json` files and relays a genuine approve/reject decision to the real Kiro Crew Gateway, using the exact same `GET /api/approvals` / `POST /api/approvals/{id}/{action}` endpoints `scripts/changeguard_launch.py` already documents as the sole approval-resolution mechanism. The browser never receives the Gateway's `X-Internal-Secret`, any approval token, or any credential — see the proxy plugin's module docstring for the full security rationale.

Live mode configuration (environment variables, read server-side only):

```bash
export CONTROL_ROOM_GATEWAY_URL=http://127.0.0.1:8787
export CONTROL_ROOM_INTERNAL_SECRET=$(cat ~/.kiro/crew/.local_secret)  # optional
npm run dev:live
```

If `CONTROL_ROOM_GATEWAY_URL` is unset, the proxy's approval endpoints fail closed (HTTP 503) rather than silently no-opping.

## Commands

```bash
cd apps/control-room
npm install
npm run dev        # fixture mode (default) — http://localhost:5173
npm run dev:live   # live mode — requires a running kirocrew gateway
npm run build       # type-check + production build
npm test            # vitest run (fast, no network calls)
```

## Architecture

- `src/types/changeguard.ts` — types mirroring the existing `ReviewResult`/`Finding`/`RemediationStageResult`/`FinalVerdict` JSON shapes exactly as ChangeGuard's Python scripts already produce them.
- `src/lib/changeguard/artifacts.ts` — parses raw artifact JSON into those types. No judgment.
- `src/lib/changeguard/state.ts` — the ONLY place live artifact/Gateway data is normalized into a `ControlRoomState`. Components never parse artifact JSON directly.
- `src/lib/changeguard/gateway.ts` — browser-side client for this app's own local proxy endpoints only, never the Gateway directly.
- `server/controlRoomProxyPlugin.ts` — the Node-only Vite dev-server middleware that holds the Gateway URL/secret and relays approvals. Never ships to the browser bundle.
- `src/fixtures/controlRoomStates.ts` — the eight fixture states used in fixture mode.
- `src/hooks/useControlRoomState.ts` — the single hook every view uses to obtain state (fixture or live) and to submit an approval decision.
- `src/components/*`, `src/views/ControlRoomView.tsx` — the single main screen and its pieces.

## What this app will never do

- Run `terraform apply` or `terraform destroy`.
- Call the AWS CLI or require an AWS account.
- Mutate `terraform/main.tf` directly.
- Invoke the Remediator agent directly.
- Simulate or fabricate an approval decision.
- Compute a SEC-001/SEC-002/REL-001/BR-001 policy judgment.
- Expose `.local_secret`, an approval token, or any credential to browser JavaScript.
