# Pre-Pilot Gate Board — 2026-06-02

> Single source of truth for everything remaining until pilot ship (target 2026-07-15). Pull from `dev`. Supersedes the 2026-06-01 orchestrator board. Owner: tech-lead orchestrator.

## Pilot scope (LOCKED)
- **Wellness = B (phased):** pilot ships **food + water + daily-note** (`/summary?with_comment`). Mood/sleep/body/symptom + real nutrition advice + **Tier-B = Phase 2 (post-pilot)**.
- **ED-safety contract (profile-driven):** when `health_flags.eating_disorder = true` hide calories, PFC/macros, calorie targets, deficit/surplus language, nutrition scoring; render diary as **neutral meal-log**. No user toggle in pilot; override = post-pilot + safety review.
- **R-1 retention:** honest copy now ("messages kept while account active, deleted on account deletion"); 180-day anonymiser = post-pilot.

## A. Pre-pilot BLOCKERS (must close before pilot)
| Item | Owner | Status | Notes |
|---|---|---|---|
| #842 PII tokenizer (Path A) | W2 | TOP / in progress | wire `pii_context()` at ALL skill LLM call-sites + G10 AST-lint + W3 adversarial review |
| food_scanner skill backend (`feat/w4/food-scanner-backend`) | W4 | starting | unblocks W1 swap; bundles #956 / Q-BACK-5 / Q-BACK-6 / server-EXIF #957 |
| B-1 consumers + smoke (#943 → #965) | Gamma | merge pending | merge → unblocks delivery flip |
| #927 idempotency no-dup-DM + cross-tenant (payment.failed path) | Gamma | next | MUST_FIX_PRE_PILOT, W3 review |
| Delivery flip dev/staging (#185) | Alpha | held | flip on tech-lead signal after #943+#965 on dev |
| R-1 honest retention copy → ship | Tau → W1 | to do | fixes live Variant-3 violation in DisclosureSheet.tsx |
| #956 consent server audit-trail | W4 | in food_scanner bundle | mini-app endpoint → Ayla disclaimer_acked |
| EXIF GPS strip: client (#957 done) + server | W1 done / W4 server | server pending | defence-in-depth |
| Photo→US vision path (#4) | tech-lead / founder + legal | CONFIRMED | food photos → **OpenAI (US)** by default (`FOOD_SCANNER_PRIMARY="openai"`); "RF-perimeter" claim FALSE. Resolution = **Opt 2 (lawful OpenAI)**: localize-first + explicit cross-border consent + RKN notification + minimization (EXIF strip + downscale/crop) + OpenAI zero-retention + honest disclosure. GATED on legal verdict (#947). Yandex (`FOOD_SCANNER_PRIMARY` flip) = fallback if legal insufficient. |

## B. Pre-SHIP gates (before go-live — NOT merge; legal/ops/founder)
| Gate | Owner | Status |
|---|---|---|
| #947 cross-border legal verdict (brief PR #973) | founder → lawyer | pending |
| Cross-border food-photo transfer lawfulness (Opt 2) — legal verdict + RKN notification | founder / lawyer | pending (gated on #947) |
| Anthropic + OpenAI DPA / zero-retention tier (legal point #3) | founder / ops | verify |
| STRICT_TENANT_REFUSE flip + D-2 ceilings checklist | founder (date) + W3 | flip date pending |
| Records/payments 7-year retention confirm (R-2) | Alpha | confirm |
| Prod delivery flip | Alpha + ops | after dev/staging round-trip green |

## C. In-flight non-blocking (fillers / follow-ups)
- W3: contract-matrix security columns (auth-fragmentation first); Phase 2 pre-flip audit.
- Alpha: option-2 filler (`user.profile.updated` internal endpoint); Phase 5 done (#186).
- W1: polish #958 / #959.
- Gamma: ALLOWED_EVENT_NAMES ↔ _KNOWN_NAMES sync-guard test.

## D. Post-pilot (DEFERRED — not in pilot)
Tier-B FSM · broader wellness (mood/sleep/body/symptom) + real advice · R-1(a) 180-day anonymiser · Block D booking ownership · ADR-0015 cross-service privacy · memory layer (UserPersonalContext/MemoryEntry) · Block E cleanup / legacy deletion · #948 #952 #958 #959 #969 #970.

## Per-stream marching orders (scope-locked)

### W2 (skills / PII) — branch `stabilization/w2/*`, PR→dev
1. **#842 PII Path A — TOP CRITICAL.** mypy fix → wire `pii_context()` at EVERY skill/orchestrator LLM call-site (booking/faq/food_*/health_screening/nutrition_anketa/cross_domain/welcome/payment_failed/pipeline — NOT booking-only) → integration test (tokenize-out/reverse-in) → G10 AST-lint guard (coord W4) → verify tests in CI → W3 adversarial review. DoD: pii_context active everywhere, G10 blocks bypass, CI green, W3 sign-off.
2. MAX-hardening (3 guards: `cb:request_contact`, `MAX_KEYBOARD_ROWS=29` cap, `open_app` flat-slug assert) — one PR ~75min, after #842.
- Done: B-1e, B-2 (by W4). D3 = post-pilot. Anti-touch: Ayla, frontend, eventbus internals.

### W4 (coord / CI / photo / food-backend) — branch `feat/w4/*` or `stabilization/w4/*`, PR→dev
1. **food_scanner skill backend (`feat/w4/food-scanner-backend`) — #1 pilot-critical.** NUTRITION_ENABLED flag; 152-ФЗ consent gate (#956/Q-BACK-5); miniapp_api endpoint (coord W1 stub contract); server-side EXIF GPS strip (#957); call Ayla `/nutrition/internal/{scan,profile,food-log,summary}`; `display_numbers` from `health_flags.eating_disorder` per ED-safety contract (Q-BACK-6); `/дневник` handler; daily-note via `/summary?with_comment`. DoD: end-to-end W1→miniapp_api→skill→Ayla→back; ED-mode hides numbers; W2 review (waiver into apps/skills/food_scanner) + Code Reviewer.
2. G10 PII AST-lint contract — coord with W2 #842.
3. Block A residuals (A3/A6/A7/A11) — stabilization, lower.
- Anti-touch: Ayla repo (call its endpoints, don't edit), W1 frontend, W2 skill internals except food_scanner waiver.

### Gamma (eventbus / contracts) — branch `stabilization/gamma/*`, PR→dev
1. **Merge #943 (A10) → then #965 (booking.confirmed + smoke)** to dev. Report "on dev" (unblocks delivery flip).
2. **#927** idempotency no-dup-DM + cross-tenant on payment.failed path. MUST_FIX_PRE_PILOT, W3 review.
3. Sync-guard test: ALLOWED_EVENT_NAMES ↔ _KNOWN_NAMES.
- Don't touch payment.* (shipped); payment.authorized stays (Variant 1). Anti-touch: Ayla, frontend.

### Alpha (Ayla backend) — branch `stabilization/alpha/*`, PR→Ayla dev
1. **#185 delivery gate — HELD.** Flip on tech-lead signal after #943+#965 on dev (topics: booking.created/cancelled/rescheduled/confirmed + payment.captured/failed; NOT payment.authorized). Prod flip = pilot deploy.
2. **R-2:** confirm records/payments 7-year retention (≥7y AND ≤7y, no over-retention).
3. Option-2 filler (P1-3 internal profile endpoint / user.profile.updated) — interruptible.
4. P3 (only if W4 #956 opens gap): `declined_at` serializer extension.
- Wellness backlog = SNAPPED (B → post-pilot). Anti-touch: bot-platform code, frontend.

### W1 (frontend / webview) — branch `feat/w1/*`, PR→dev
1. **Ship corrected DisclosureSheet.tsx** with R-1 honest retention copy (from Tau) — closes live violation.
2. **food_scanner swap stub→real** — GATED on W4 backend; coord endpoint contract; swap on tech-lead signal.
3. **#152 swap** — GATED on Alpha #165; scope = **food/water ONLY** (B locked).
4. ED-mode suppression in food_scanner frontend (Q-BACK-6 contract).
5. Polish #958/#959 — optional filler.
- Anti-touch: backend/Python/Ayla.

### W3 (security) — branch `stabilization/w3/*`, PR→dev
1. **Contract-matrix security columns** — UNBLOCKED (on dev). Priority: auth-fragmentation rows; cross-link #925/#927/#928.
2. **#925 + #927 pre-fix adversarial analysis → fix-specs to W2/Gamma; then mandatory §H.3 review** of their fixes.
3. **Phase 2 pre-flip audit** — GO.
4. D-2 ceilings checklist — GATED on flip date (founder).
- Anti-touch: other streams' code (audit-only).

### Tau (UX/voice, support-mode) — branch `docs/tau/*`, PR→dev
1. **R-1 honest retention copy → hand to W1** (pilot).
2. **Merge #960 (food_scanner spec) + #967 (profile/runbook/reminders)** — spec docs (incl. §4.2.x); merge so source-of-truth on dev. (r2 disclosure UI ships only after legal verdict.)
3. Brand Guardian on hardening PRs; voice-review on request. Else standby. Anti-touch: code, new design discovery.

### Sigma (visual tokens) — standby
Pilot tokens done (#916/#966). Re-engage only on new token request (none under B).

## Tech-lead held gates
- **Delivery flip signal** (payment + booking) — after #943+#965 on dev + round-trip green.
- **W1 food_scanner swap signal** — after W4 backend + Ayla data ready.
- **#4 photo-path** — RESOLVED to Opt 2 (lawful OpenAI path); GATED on legal verdict (#947) + RKN cross-border notification. Yandex flip = fallback if legal insufficient.
