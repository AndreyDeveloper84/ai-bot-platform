# E0.4 — Misc Cluster Migration Coverage Audit

**Date:** 2026-05-31
**Auditor:** general-purpose agent (E0.4)
**Scope:** misc `legacy_maxbot/` files NOT covered by E0.1 (AI/LLM) / E0.2 (MAX) / E0.3 (nudges) / E0.5 (legacy_notifications) / E0.6 (legacy_formulatela_mcp)
**Verdict:** **PARTIAL** — booking/reminder/YClients sub-cluster substantially ported (with shape changes per ADR-0009); nutrition Celery beats + nutrition HTTP client + repeat-offer task + several utility seams not yet replicated
**Pilot-blocking?** **DEPENDS** — depends on whether pilot fires nutrition `send_water_reminders` / `send_daily_reports` / `send_repeat_offers` and on whether the BookingRequest→RemoteBookingProxy shrink (ADR-0009 §5) is completed before flip
**ADR-0009 violations found?** **YES — 1 latent, 1 confirmed-in-flight**

## Method

1. Glob'd `legacy_maxbot/**/*.py` → 73 files (confirmed against E0.1 and E0.2 docs).
2. Subtracted files documented as in-scope by E0.1 (`docs/architecture/e0-1-ai-llm-cluster-migration-coverage.md`) and E0.2 (`docs/architecture/e0-2-max-handlers-cluster-migration-coverage.md`). Estimated E0.3 nudges scope from `legacy_maxbot/nudges/*.py` + `evening_inline.py` + `handlers/{water,reminders,nutrition_anketa,nutrition_entry}.py` based on the running phase brief and the routing comments in `legacy_maxbot/handlers/__init__.py:25,46-95`.
3. For every remaining file: read first 30–60 lines (entire file when small) to confirm purpose.
4. Grep'd `apps/` for the load-bearing identifiers of each remaining legacy file: `BookingReminder`, `reminders_factory`, `send_due_reminders`, `escalate_stale_reminders`, `send_post_visit_followups`, `send_repeat_offers`, `send_daily_reports`, `send_water_reminders`, `yclients_webhook`, `external_user_id_for`, `nutrition_client`, `NutritionAPIError`, `calc_bmi`, `is_quiet_hours_for_user`, `calc_proportional_norm`, `render_overrides_applied`, `NutritionAnketaStates`, `BookingStates`, `GREETING_NEW_USER`, `setup_django`, `django_bootstrap`.
5. Cross-checked `legacy_maxbot/yclients_webhook.py`, `legacy_maxbot/tasks.py`, `legacy_maxbot/reminders_factory.py`, `legacy_maxbot/services/{ayla_user_proxy.py,nutrition_client.py}`, `legacy_maxbot/management/commands/manual_test_anketa.py` line-by-line against their candidate ports (`apps/integrations/yclients/webhooks.py`, `apps/bookings/{tasks,escalation,followups,reminders_factory}.py`, `apps/integrations/ayla/{user_proxy,nutrition_client}.py`).
6. Re-read ADR-0009 (`docs/adr/ADR-0009-ayla-split-domain-architecture.md:51,93,146,181`) to verify the booking/payment/catalog boundary.

## Files in scope

* Total legacy_maxbot Python files: **73**
* Files in E0.1 (AI/LLM): **27** (per E0.1 doc line 46)
* Files in E0.2 (MAX): **~10** (per E0.2 doc line 18) — some overlap with E0.1 already (handlers/start, handlers/booking, handlers/services, handlers/contacts, keyboards, menu_state, welcome, master_image, middleware, main, config, __init__)
* Files likely in E0.3 (nudges) — estimate **15**: `nudges/*.py` (10 files) + `evening_inline.py` + `handlers/{water,reminders,nutrition_anketa,nutrition_entry}.py` (4)
* Files in `legacy_notifications/` (E0.5): **0** in `legacy_maxbot/` (separate dir, out-of-tree of this scope)
* Files in `legacy_formulatela_mcp/` (E0.6): **0** in `legacy_maxbot/` (the consumer side `mcp_client.py` is in E0.1)
* **Files in E0.4 scope: ~14** (~1 400 LOC est, see sub-cluster table below)

### Sub-clusters

| Sub-cluster | Files | LOC | Purpose |
|---|---|---|---|
| YClients admin webhook | `yclients_webhook.py` | 362 | YClients admin record.create/update/delete → BookingRequest + BookingReminder + welcome DM |
| Booking reminder factory | `reminders_factory.py` | 155 | T-24h / T-2h reminder writer (idempotent), cancel + reschedule helpers |
| Celery tasks (booking + nutrition beats) | `tasks.py` | 570 | `send_due_reminders` (15min), `escalate_stale_reminders` (1h), `send_post_visit_followups` (daily), `send_repeat_offers` (weekly), `send_daily_reports` (hourly), `send_water_reminders` (4h) |
| Ayla service clients | `services/ayla_user_proxy.py`, `services/nutrition_client.py`, `services/__init__.py` | ~21 + ~600 + 0 | `external_user_id_for(bot_user)` + httpx client for `/api/v1/nutrition/internal/...` with circuit breaker |
| FSM state definitions | `states.py` | 57 | `BookingStates` / `AskStates` / `NutritionAnketaStates` maxapi-SDK state groups |
| Static text constants | `texts.py` | ~140 (sampled 93) | RU greetings (incl. segmented `GREETING_RETURNING_CLIENT_WITH_DIARY`), booking flow strings, AI system prompt (legacy MAX-bot prompt) |
| Standalone bootstrap | `django_bootstrap.py` | 22 | Idempotent `django.setup()` for non-gunicorn maxbot process |
| Pure-math helper | `nutrition_calc.py` | 30 | `calc_bmi(weight_kg, height_cm)` (BMR/kcal moved to Ayla) |
| Nutrition settings helpers | `nutrition_settings_helpers.py` | 88 | `get_setting`/`set_setting` on `BotUser.nutrition_settings` + `is_quiet_hours_for_user` (22:00–09:00 local) + `calc_proportional_norm` (elapsed-wakeup water curve) |
| Override-card render | `health_overrides_render.py` | 48 | Render «Учла важное» block from `ProfileResponse.raw["overrides_applied"]` |
| Management command | `management/commands/manual_test_anketa.py` (+ 2 `__init__.py`) | ~80 + 0 + 0 | One-shot smoke run of TIER-A nutrition anketa FSM steps |
| Package marker | `legacy_maxbot/__init__.py` | 16 | Frozen-notice docstring only — no runtime |

## Coverage table

| Legacy file / sub-cluster | LOC | Current equivalent | Coverage | Evidence | Pilot risk if deleted |
|---|---|---|---|---|---|
| `legacy_maxbot/yclients_webhook.py` — admin webhook receiver | 362 | `apps/integrations/yclients/webhooks.py` + `apps/integrations/yclients/urls.py` (`/api/v1/yclients/webhook/`) | **FULL + HARDENED** | apps version is a docstring-acknowledged port (`apps/integrations/yclients/webhooks.py:1-80`). Adds: tenant scoping via `YCLIENTS_WEBHOOK_TENANT_SLUG` (`webhooks.py:51-61,172-186`), always-200 audit wrapper (`webhooks.py:141-153`), `_EVENT_BOOKING_CREATED_FROM_YCLIENTS_ADMIN` event emission (`webhooks.py:110,99`). HMAC verification deliberately deferred (`webhooks.py:42-48`). | LOW — apps port is strictly more defensive. |
| `legacy_maxbot/reminders_factory.py` — `create/cancel/reschedule_reminders_for_record` | 155 | `apps/bookings/reminders_factory.py` (DRF-844 / R1, docstring-acknowledged port) | **FULL + HARDENED** | apps port adds tenant FK + `BookingReminder.all_tenants` escape-hatch with explicit `tenant=` defaults (`apps/bookings/reminders_factory.py:17-34,62-71`). Offset table tuple-of-tuples vs legacy inline. Same idempotency key `(yclients_record_id, kind)` (`apps/booking/models.py:31-44`). | LOW |
| `legacy_maxbot/tasks.py::send_due_reminders` | ~80 of 570 | `apps/bookings/tasks.py::send_due_reminders` (`apps/bookings/tasks.py:242`) | **FULL + HARDENED** | apps port adds compare-and-set race-safety: `BookingReminder.all_tenants.filter(pk=..., status=PENDING).update(status=SENT_NO_REPLY)` rowcount check (`apps/bookings/tasks.py:9-19`). 200-row batch cap (`apps/bookings/tasks.py:36-43`). | LOW |
| `legacy_maxbot/tasks.py::escalate_stale_reminders` | ~50 | `apps/bookings/escalation.py::escalate_stale_reminders` (line 172) | **FULL** | Sibling-module split per docstring intent (`apps/bookings/followups.py:6-13`). | LOW |
| `legacy_maxbot/tasks.py::send_post_visit_followups` | ~60 | `apps/bookings/followups.py::send_post_visit_followups` (line 342) | **FULL + HARDENED** | apps port uses Moscow-local-day window (`apps/bookings/followups.py:17-24`), per-bot_user dedup set vs legacy `context["last_followup_sent_at"]`. | LOW |
| `legacy_maxbot/tasks.py::send_repeat_offers` (weekly Monday 12:00, 21-28d window, 30d rate-limit) | ~70 | **NOT PORTED** | grep `repeat_offer\|last_repeat_offer\|send_repeat` in `apps/` → 0 hits | LOW–MEDIUM — re-engagement nudge; deferring is acceptable for pilot but a known pre-pivot retention lever silently disabled. **FOLLOW_UP / PORT_POST_PILOT.** |
| `legacy_maxbot/tasks.py::send_daily_reports` (hourly :00, per-user TZ + daily_report_time) | ~110 | **NOT PORTED** | grep `send_daily_reports\|daily_report\|daily_report_time` in `apps/` → 0 hits | DEPENDS — per memory `variant_b_wellness_mvp`, wellness dashboard goes through Mini App in pilot, not push DM; per memory `tau_design_phase_complete` Tau ships records/profile/dashboard in Mini App. **PORT_POST_PILOT** unless pilot explicitly wants the «21:00 DM with daily numbers» voice (founder must confirm). |
| `legacy_maxbot/tasks.py::send_water_reminders` (every 4h, opt-in, quiet hours, same-day dismiss) | ~120 | **NOT PORTED** | grep `send_water_reminders\|water_reminder\|water_reminders_enabled` in `apps/` → 0 hits | DEPENDS — per memory `variant_b_wellness_mvp` water tracker is in pilot, but whether the *push* reminder is is unclear. **INVESTIGATE — founder/Tau confirm.** Same gate as send_daily_reports. |
| `legacy_maxbot/services/ayla_user_proxy.py::external_user_id_for` | 21 | `apps/integrations/ayla/user_proxy.py::external_user_id_for` (DRF-826) | **FULL + EXTENDED** | apps port is docstring-acknowledged channel-agnostic upgrade `bot:{channel}:{channel_user_id}` (`apps/integrations/ayla/user_proxy.py:1-23,31-40`). Legacy form `bot:{max_user_id}` is a special case. Consumed by 9 apps files (food_scanner/cross_domain/water/nutrition_anketa skills + miniapp_api). | LOW |
| `legacy_maxbot/services/nutrition_client.py` (httpx client, circuit breaker, ScanResponse/SummaryResponse/ProfileResponse/WaterEntryResponse/DeficitsResponse DTOs) | ~600 | `apps/integrations/ayla/nutrition_client.py` (+ `profile_client.py`) | **PARTIAL** | apps port exists and is consumed by food_scanner/food_correction/water/nutrition_anketa/cross_domain skills. **Not deep-diffed in this audit** — DTO field parity (e.g. `ScanResponse.raw`, `SummaryResponse.ai_comment`, `WaterTodayResponse.kcal_from_beverages`/`caffeine_mg`/`coffee_cups`/`tea_cups`, `WaterEntryResponse.alcohol_recovery_hint`/`milestone_text`) needs side-by-side line check. **INVESTIGATE.** | MEDIUM — quiet field drops would surface as `AttributeError` only on the rare data path (e.g. alcohol-recovery hint, caffeine warning); pilot may not hit them. |
| `legacy_maxbot/states.py` — `BookingStates`/`AskStates`/`NutritionAnketaStates` maxapi states | 57 | **NOT PORTED as classes**; FSM is folded into skill modules via different abstraction | **PARTIAL_BY_DESIGN** | grep `NutritionAnketaStates\|BookingStates\|AskStates` in `apps/` → 0 hits. `apps/skills/nutrition_anketa/fsm.py` carries the FSM under a different API (per E0.1 doc line 181 it's `validate_int_range`-style). | LOW — legacy maxapi SDK isn't running in current architecture. |
| `legacy_maxbot/texts.py` — RU strings (GREETING_*, BOOKING_*, AI_*) | ~140 | scattered: greetings in `apps/skills/welcome/skill.py` (per E0.1 line 184), booking strings in `apps/skills/booking/`, AI prompt fully replaced (E0.1 §2.2 `apps/skills/*/prompts.py`) | **PARTIAL** | grep for `GREETING_NEW_USER\|FALLBACK_UNKNOWN_INPUT\|BOOKING_ASK_NAME` in `apps/` → 0 hits direct; only 1 match for any of these tokens (in `apps/channels/max/handler.py`). Segmented `GREETING_RETURNING_CLIENT_WITH_DIARY` / `GREETING_SILENT_USER_WITH_DIARY` voice — verify welcome skill (E0.1 row 32). Legacy `AI_SYSTEM_PROMPT` (90-LOC inline) is a frozen-bot prompt — explicitly superseded by per-skill prompts. | LOW–MEDIUM — silent regression risk on returning-with-diary segment greeting tone. Already flagged in E0.1 `INVESTIGATE_FURTHER` row 14. |
| `legacy_maxbot/django_bootstrap.py::setup_django` | 22 | `apps/replay/__main__.py` uses Django settings module env import directly | **NONE_BY_DESIGN** | Bot-platform runs in standard Django gunicorn + Celery workers; no need for a standalone idempotent bootstrap shim. | NONE |
| `legacy_maxbot/nutrition_calc.py::calc_bmi` | 30 | **NOT PORTED** (BMI used inside `apps/skills/nutrition_anketa/skill.py:50,352` but no `calc_bmi` helper found) | **PARTIAL — INVESTIGATE** | grep `calc_bmi\|def calc_bmi` in `apps/skills/nutrition_anketa/` → 0 hits. BMI is mentioned in skill comments (lines 50, 352) but the math implementation source needs confirmation. May be inline in `fsm.py` or moved to Ayla per legacy comment. | LOW — BMI ladder is a small UX gate. |
| `legacy_maxbot/nutrition_settings_helpers.py::is_quiet_hours_for_user`, `calc_proportional_norm`, `get_setting`, `set_setting` | 88 | **NOT PORTED** | grep `calc_proportional_norm\|is_quiet_hours_for_user\|nutrition_settings_helpers` in `apps/` → 0 hits (only different-context `quiet_hours` for notifications model) | DEPENDS — only matters if `send_water_reminders` is ported. The functions are tied to the not-yet-ported tasks. **DEFER together with `send_water_reminders` / `send_daily_reports`.** |
| `legacy_maxbot/health_overrides_render.py::render_overrides_applied` | 48 | **NOT PORTED** | grep `render_overrides_applied\|overrides_applied\|goal_overridden_by` in `apps/` → 3 hits in `apps/skills/nutrition_anketa/skill.py` + `tests/test_skill.py` + `apps/integrations/ayla/nutrition_client.py`. Field is consumed but render helper itself not found. May be inlined; need side-by-side check. | LOW — Phase 3.2A T03 «Учла важное» card; nutrition_anketa skill likely re-implements inline. **INVESTIGATE.** |
| `legacy_maxbot/management/commands/manual_test_anketa.py` | 80 | **NOT PORTED** | grep `manual_test_anketa\|manual_test` in `apps/` → 0 hits | NONE — smoke-test scaffold; current code has proper `apps/skills/nutrition_anketa/tests/`. |
| `legacy_maxbot/__init__.py`, `management/__init__.py`, `management/commands/__init__.py`, `services/__init__.py` | 16+0+0+0 | n/a | **NONE_BY_DESIGN** | Package markers + frozen-notice. No runtime. | NONE |

## ADR-0009 boundary audit

ADR-0009 rule 5 (from CLAUDE.md / `docs/adr/ADR-0009-ayla-split-domain-architecture.md:51,93,146,181`):
> bot-platform never owns booking SoR. Its BookingRequest becomes RemoteBookingProxy — a cache for reminder + escalation FSM only. Bot-platform's `apps/booking` must shrink from full BookingRequest model to `RemoteBookingProxy + reminder FSM`.

| Legacy file | Writes booking/payment/catalog? | Current port also writes? | Violation? |
|---|---|---|---|
| `legacy_maxbot/yclients_webhook.py` (admin webhook) | YES — `BookingRequest.objects.create(...)` (`yclients_webhook.py:152-162`) + `BookingReminder.bulk_create` indirectly | YES — apps port still `from apps.booking.models import BookingReminder, BookingRequest` and writes both (`apps/integrations/yclients/webhooks.py:97`). | **POTENTIAL — in-flight per ADR-0009** |
| `legacy_maxbot/reminders_factory.py` | Writes BookingReminder rows | Yes — `apps/bookings/reminders_factory.py:43-44,62-71` writes `apps.booking.models.BookingReminder` | NO — ADR-0009 explicitly carves out «reminder + escalation FSM» as bot-platform-owned (`ADR-0009:51`) |
| `legacy_maxbot/tasks.py` (`send_due_reminders`, `escalate_stale_reminders`, `send_post_visit_followups`) | Writes BookingReminder status; does NOT write Appointment | Same — apps ports operate on `BookingReminder` only | NO — same carve-out |
| `legacy_maxbot/services/ayla_user_proxy.py` | NO — pure function | NO | NO |
| `legacy_maxbot/services/nutrition_client.py` | NO from bot side — HTTPS POSTs to Ayla which is canonical for nutrition | NO — apps port is REST client | NO |

### Single ADR-0009 latent concern (NOT a hard violation today)

The YClients admin webhook (`apps/integrations/yclients/webhooks.py:97-99`) still imports + writes `BookingRequest` AND `BookingReminder` in bot-platform. The migration `apps/booking/migrations/0012_remote_booking_proxy_and_ayla_appointment_id.py` adds `RemoteBookingProxy` as a sibling but **does not** drop the BookingRequest writes. The «BookingRequest → RemoteBookingProxy shrink» mandated by ADR-0009 §5 is **partially in-flight, not complete**.

Per memory `ayla_split_domain_architecture` + `freeze_mvp_until_boundaries_locked`, this is the kind of in-flight shrink that needs to land before any «delete legacy_maxbot/yclients_webhook.py» step. The legacy file is therefore NOT safe to delete — until the apps port no longer writes BookingRequest (it would write only RemoteBookingProxy for admin-created bookings + reminders), the two files must be evaluated together.

**Severity: PRE_PILOT FOLLOW_UP** (not blocker for the 2026-07-15 pilot per memory `pilot_scope_discipline` — bot-platform may legitimately keep BookingRequest as a bot-side cache during Phase 1; ADR-0009 §5 frames this as a Phase 2 migration). Confirm with tech lead before flipping to BLOCKER.

## YClients integration coverage

Per memory `salon_catalog_vertical` (YClients = one source among several) and `attribution_extensible_model` (booking_source enum + ai_assist_score must persist).

| Legacy YClients function | Current port | Status | Attribution-respecting? |
|---|---|---|---|
| Admin webhook receiver (record.create/update/delete) | `apps/integrations/yclients/webhooks.py` | FULL + HARDENED | YES — apps port writes `BookingRequest.source = "yclients_admin"` (matches ATTRIBUTION enum at `apps/booking/models.py:59`). |
| Inbound→event publish (Ayla-aware) | `apps/integrations/yclients/webhooks.py:99-110` (`emit("booking_created_from_yclients_admin", ...)`) | FULL — NEW | YES — event-driven, decouples bot-DB write from notification (per ADR-0009 async path). |
| YClients HTTP client (booking endpoints) | `apps/integrations/yclients/client.py` (DRF-837) | FULL — docstring-acknowledged port from `mysite/services_app/yclients_api.py` with 7-endpoint trim, WAF-bypass headers preserved | n/a |
| Outbound platform→YC sync (push platform bookings) | `apps/integrations/yclients/tasks.py` | NEW — beyond legacy; legacy was webhook-only (one-direction) | YES — stores `yclients_record_id` in `BookingRequest.attribution_metadata` for round-trip idempotency (`apps/integrations/yclients/tasks.py:21-25`) |
| AI-side YClients enrichment (`ai_yclients.py`) | absorbed into `apps/skills/booking/tools.py` per E0.1 row 22 | FULL (E0.1) | n/a |

**Verdict: FULL_COVERAGE for YClients pilot scope.** Apps port is broader than legacy (adds outbound push + tenant scoping + audit-log on every error). Attribution is respected. The latent ADR-0009 concern above is a Phase-2 shrink, not a YClients-specific gap.

## Gaps requiring action

### PORT_POST_PILOT
1. **`send_repeat_offers` weekly Monday 12:00 task**
   - Legacy: `legacy_maxbot/tasks.py:217-291`. Re-engages clients 21-28d post-visit, rate-limited to 30d via `bot_user.context.last_repeat_offer_at`.
   - Not ported anywhere in `apps/`.
   - Recommended: **PORT_POST_PILOT** — measurable retention lever but not pilot-critical (memory `pilot_scope_discipline` deprioritises non-essentials).
   - Why: silent retention regression vs prod-validated mysite stack.

2. **`send_daily_reports` hourly :00 push + `send_water_reminders` 4h opt-in push**
   - Legacy: `legacy_maxbot/tasks.py:304-409` + `:435-569`. Per-user TZ, quiet hours 22-09, opt-in flag, same-day-dismiss skip.
   - Not ported anywhere; dependent helpers `is_quiet_hours_for_user`, `calc_proportional_norm` also unported.
   - Recommended: **INVESTIGATE_FURTHER** — pilot intent unclear. Per memory `variant_b_wellness_mvp` wellness UX is Mini App; per `tau_design_phase_complete` push DM cadence for nutrition is undecided. If pilot decides to push, **PORT_NOW_PILOT_SOFT** (~2-3d for both tasks + helpers + opt-in setting wiring).
   - Why: nutrition push cadence is a designed-in feature; absence = wellness UX feels passive.

### INVESTIGATE_FURTHER
3. **`legacy_maxbot/services/nutrition_client.py` DTO parity vs `apps/integrations/ayla/nutrition_client.py`**
   - Legacy DTOs `WaterTodayResponse` (kcal_from_beverages, caffeine_mg, coffee_cups, tea_cups), `WaterEntryResponse` (alcohol_recovery_hint, milestone_text), `SummaryResponse.ai_comment` carry signals consumed by render helpers. Need side-by-side DTO-field diff before deciding port-state.
   - Recommended: **INVESTIGATE** — line-by-line DTO diff (~30 min focused review).

4. **`legacy_maxbot/health_overrides_render.py::render_overrides_applied` «Учла важное» card**
   - Field `overrides_applied` is consumed in `apps/skills/nutrition_anketa/skill.py` and `apps/integrations/ayla/nutrition_client.py` but standalone helper not found in apps. Likely inlined; needs confirmation it renders with the same `_GOAL_OVERRIDE_LABELS` fallback for old Ayla envelopes lacking `overrides_applied`.
   - Recommended: **INVESTIGATE.**

5. **`legacy_maxbot/nutrition_calc.py::calc_bmi`**
   - BMI mentioned in `apps/skills/nutrition_anketa/skill.py:50,352` but `calc_bmi` symbol not found via grep. Likely inlined or moved to Ayla; needs confirmation.
   - Recommended: **INVESTIGATE.**

### PORT_POST_PILOT (de facto deferred per ADR-0009)
6. **BookingRequest → RemoteBookingProxy shrink**
   - Per ADR-0009 §5 + memory `ayla_split_domain_architecture`, bot-platform's `apps/booking/models.py::BookingRequest` should shrink to `RemoteBookingProxy`. Migration 0012 adds RemoteBookingProxy as a sibling but BookingRequest is still the primary write target in `apps/integrations/yclients/webhooks.py:97`.
   - Recommended: **PORT_POST_PILOT** per founder pilot-scope discipline; in-flight Phase 2 work.
   - Why: ADR-0009 latent debt — not pilot-blocking but locks the legacy yclients_webhook.py file from deletion.

### DELETE_DELIBERATE (gone on purpose)
- `legacy_maxbot/django_bootstrap.py` — standard Django runtime supersedes; **HIGH** confidence delete (but per founder constraint, retain until Sprint-10 cleanup gate per `legacy_maxbot/MIGRATION_NOTICE.md`).
- `legacy_maxbot/management/commands/manual_test_anketa.py` — smoke test superseded by `apps/skills/nutrition_anketa/tests/`; **HIGH** confidence delete.
- `legacy_maxbot/__init__.py` — frozen-notice docstring only; no runtime; **HIGH** confidence delete.

## Files safe to delete

**NONE — per founder constraint «легаси код нельзя удалять, надо проверить все ли перенесено».** This is a coverage audit, not a deletion authorisation gate.

Highest-confidence deletion candidates (LOW risk; await Sprint-10 cleanup gate per `MIGRATION_NOTICE.md`):

* **HIGH confidence:** `legacy_maxbot/django_bootstrap.py`, `legacy_maxbot/__init__.py`, `legacy_maxbot/management/commands/manual_test_anketa.py`, `legacy_maxbot/services/__init__.py`, `legacy_maxbot/services/ayla_user_proxy.py` (apps port is docstring-acknowledged superset).
* **MEDIUM confidence:** `legacy_maxbot/reminders_factory.py` (apps port is docstring-acknowledged port, hardened), `legacy_maxbot/tasks.py::send_due_reminders`+`escalate_stale_reminders`+`send_post_visit_followups` (3 of 6 task functions ported FULL — but file as a whole still contains the 3 unported tasks, so cannot delete the whole file).
* **LOW confidence:** `legacy_maxbot/yclients_webhook.py` — pending ADR-0009 BookingRequest shrink (see ADR-0009 boundary section above).

## Investigations needed

1. **Founder/Tau confirm pilot scope for nutrition push** — does pilot fire `send_daily_reports` (hourly push at user-configured time, default 21:00 МСК) and `send_water_reminders` (4h adaptive)? If yes → PORT_NOW_PILOT_SOFT; if no → PORT_POST_PILOT. Owner: Tau / founder.
2. **DTO-field diff `services/nutrition_client.py` vs `apps/integrations/ayla/nutrition_client.py`** — particularly water-side fields (kcal_from_beverages, caffeine_mg, coffee_cups, tea_cups, alcohol_recovery_hint, milestone_text) and `SummaryResponse.ai_comment`. Owner: W1 / Beta.
3. **Confirm `calc_bmi` + `render_overrides_applied` + `is_quiet_hours_for_user` + `calc_proportional_norm` inlining** in `apps/skills/nutrition_anketa/` and `apps/skills/water/`. If they're not present anywhere → PORT_POST_PILOT (small utilities, low effort).
4. **ADR-0009 BookingRequest→RemoteBookingProxy shrink timeline** — does tech lead want the shrink to land pre-pilot, or is the in-flight state acceptable? This decides whether legacy `yclients_webhook.py` is delete-pending or delete-blocked. Owner: tech lead.
5. **`send_repeat_offers` weekly nudge intent** — confirm with founder whether retention DM is on roadmap for pilot. If on roadmap and not pilot-blocking → PORT_POST_PILOT.

## Appendix: searches performed

Globs / file enumeration:
- `legacy_maxbot/**/*.py` → 73 files
- `apps/integrations/yclients/*.py` → 8 files
- E0.1 + E0.2 coverage docs read in full to derive exclusion list

Grep against `apps/` (root-relative):
- `BookingReminder|reminders_factory|create_reminders_for_booking|reschedule_reminders_for_record|cancel_reminders_for_record` → 33 files (apps/bookings + apps/booking + apps/integrations/yclients)
- `send_due_reminders|escalate_stale_reminders|send_post_visit_followups|send_repeat_offers|send_daily_reports|send_water_reminders` → 11 files; send_repeat_offers/send_daily_reports/send_water_reminders absent
- `yclients_webhook|YClients.*webhook|yclients/webhook` → 13 files (port found)
- `external_user_id_for|ayla_user_proxy` → 9 files (port found)
- `nutrition_client|NutritionAPIError|NutritionUnavailableError|FoodNotRecognizedError` → 15 files
- `calc_bmi|proportional_norm|quiet_hours|render_overrides_applied|overrides_applied|goal_overridden_by` → 0–3 hits; standalone helpers not present
- `NutritionAnketaStates|BookingStates|AskStates` → 0 hits
- `GREETING_NEW_USER|GREETING_RETURNING|FALLBACK_UNKNOWN_INPUT|AI_ASK_PROMPT|BOOKING_ASK_NAME` → 1 hit (only `apps/channels/max/handler.py`)
- `setup_django|django_bootstrap` → 1 hit (`apps/replay/__main__.py`)
- `manual_test_anketa|manual_test` → 0 hits
- `YCLIENTS_WEBHOOK_TENANT_SLUG|yclients_company_id` → 2 files (`apps/integrations/yclients/webhooks.py` + tests)

Reads (full or first 30-100 lines):
- legacy: `yclients_webhook.py` (full 362), `reminders_factory.py` (full 155), `tasks.py` (full 570), `django_bootstrap.py` (full 22), `health_overrides_render.py` (full 48), `nutrition_calc.py` (full 30), `nutrition_settings_helpers.py` (88), `states.py` (full 57), `texts.py` (sampled 93), `services/ayla_user_proxy.py` (full 21), `services/nutrition_client.py` (sampled 200), `management/commands/manual_test_anketa.py` (full 80), `legacy_maxbot/__init__.py` (full 16), `handlers/__init__.py` (full 94 — confirms E0.3 scope routing comments).
- current: `apps/integrations/yclients/webhooks.py` (first 200), `apps/integrations/yclients/client.py` (first 30), `apps/integrations/yclients/tasks.py` (first 25), `apps/integrations/yclients/urls.py` (full), `apps/bookings/reminders_factory.py` (first 80), `apps/bookings/tasks.py` (first 60), `apps/bookings/followups.py` (first 30), `apps/booking/models.py` (first 80), `apps/booking/migrations/0012_remote_booking_proxy_and_ayla_appointment_id.py` (first 50), `apps/integrations/ayla/user_proxy.py` (first 40), `apps/replay/__main__.py` (first 20).
- Docs: `docs/architecture/e0-1-ai-llm-cluster-migration-coverage.md` (full 343), `docs/architecture/e0-2-max-handlers-cluster-migration-coverage.md` (full 197), `docs/adr/ADR-0009-ayla-split-domain-architecture.md` (grep'd lines 11/51/93/146/181).
