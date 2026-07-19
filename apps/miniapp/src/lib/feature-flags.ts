/**
 * Pilot feature flags (pilot 2026-08-15, W4).
 *
 * `STUB_SURFACES_ENABLED` — DEV-only stub-backed surfaces and sections.
 *
 * Several Mini App surfaces still run on hardcoded stubs (wellness
 * dashboard, catalog recommendations, records, profile consents) whose
 * backing endpoints are pilot phase 3 / post-pilot work. The pilot
 * honesty rule (orchestrator): NOTHING fake in prod — a hidden surface
 * is more honest than invented data, and a real surface must never fall
 * back to a stub. In production builds (`import.meta.env.DEV === false`,
 * statically replaced — the stub branch tree-shakes out) gated surfaces
 * render `PilotComingSoonScreen` instead; DEV builds keep the stubs for
 * local development and QA.
 *
 * Gated today (commit 4): `/customer/main` (wellness — hidden until
 * S4/post-pilot), `/customer/catalog` (real wiring is phase 3 item 1),
 * stub-backed sections of `CustomerProfileScreen` (commit 3).
 * Records (`/customer/records`) is intentionally NOT gated here — it
 * gets real data as phase 3 item 2; if phase 3 slips, gate it the
 * same way (orchestrator decision 2026-07-19).
 */
export const STUB_SURFACES_ENABLED = import.meta.env.DEV;
