# E0.1 follow-up — 4 handlers diff review

**Date:** 2026-05-31
**Auditor:** general-purpose agent (E0.1 follow-up)
**Scope:** `food_scanner.py` / `health_screening.py` / `food_correction.py` / `cross_domain.py`
**Method:** read full legacy + full current port, feature-by-feature crosscheck against `apps/*`, grep verification.

## TL;DR

| File | Current port | Verdict | Delete-ready? | PORT_NOW gaps |
|---|---|---|---|---|
| `food_scanner.py` (554 LOC) | `apps/skills/food_scanner/skill.py` (283 LOC) | **SIG_GAP** | NO | 5 |
| `health_screening.py` (533 LOC) | `apps/skills/health_screening/{skill,classifier}.py` (~150 LOC) | **SIG_GAP — different feature** | NO (legacy is unique) | 1 (full FSM) |
| `food_correction.py` (361 LOC) | `apps/skills/food_correction/skill.py` (120 LOC) | **SIG_GAP** | NO | 3 |
| `cross_domain.py` (281 LOC) | `apps/skills/cross_domain/skill.py` (162 LOC) | **PARTIAL — mostly intentional** | NO (sign-off) | 0 critical |

The current `apps/skills/health_screening/` is a **completely different feature** (pain-mention classifier with a 1-shot soft/red-flag reply for booking triage). The legacy Tier-B nutrition health-screening FSM (consent → pregnancy → breastfeeding → diabetes → chronic → allergies → meds → menopause → Ayla `upsert_profile`) has **no port** in `apps/*` — the current skill's own docstring at `apps/skills/health_screening/skill.py:27-38` acknowledges this and defers it.

## Per-file diff

---

### food_scanner.py (554 → 283 LOC)

**Current port:** `apps/skills/food_scanner/skill.py`
**Supporting:** `apps/orchestrator/ui/keyboards.py::food_recognition_keyboard`, `apps/integrations/ayla/nutrition_client.py`.

**Legacy feature inventory:**
1. F-filter `attachments` dispatch — photo-only fast path [legacy_maxbot/handlers/food_scanner.py:58-78]
2. `NUTRITION_ENABLED` feature-flag gate with "Скоро будет" fallback [85-94]
3. FSM-aware skip when user in `NutritionAnketaStates` [98-107]
4. 152-FZ consent gate (`bot_user.food_scanner_consent_at`) + render consent card + accept/decline callbacks [113-115, 195-219]
5. Photo download from MAX CDN with 8s timeout + 10 MiB hard cap (`_PhotoTooLargeError`) [46-47, 119-136, 538-549]
6. "👀 Распознаю..." loading-card edit-message pattern [144-165]
7. `client.scan_photo()` with FoodNotRecognized / Unavailable / API error branches [167-186]
8. `ai_ui.render_food_scan_v2(scan.raw)` — full scan card render
9. Meal-type buttons (`cb:nutrition:log:{scan_id}:{meal_type}`) — breakfast/lunch/dinner/snack [225-280]
10. Idempotency-key (`uuid5(NAMESPACE_OID, ext_id:scan_id:meal_type)`) for log_meal [251]
11. Post-log `render_food_logged_with_footer(meal_label, dish, kcal)` [282-291]
12. Evening-inline daily report trigger (cached pre-checks: `daily_report_time`, `evening_inline_shown_at`, hour 18-22) [299-371]
13. Cross-domain insight card hook (`eating_disorder` gate + `CROSS_DOMAIN_ENABLED`/internal-list gate) [372-449]
14. `/дневник` / `/день` / `день` / `дневник` text command → daily report [461-516]
15. `daily_report_footer_keyboard` attached on diary [514]

**Coverage:**
| # | Feature | Status | Evidence | Notes |
|---|---|---|---|---|
| 1 | Photo-only dispatch | ⚠️ PARTIAL | `apps/skills/food_scanner/skill.py:100-102` (`matches` checks `has_attachments`) | Skill-level guard only; relies on channel-adapter conventions |
| 2 | `NUTRITION_ENABLED` feature flag | ❌ MISSING | grep `NUTRITION_ENABLED` in apps → 0 hits | No "Скоро будет" fallback |
| 3 | FSM-aware skip during anketa | ❌ MISSING | grep `NutritionAnketaStates.*startswith` → 0 hits | Skill matches even if anketa active |
| 4 | 152-FZ consent gate + buttons | ❌ MISSING | grep `food_scanner_consent_at\|cb:nutrition:consent\|render_food_consent` in apps → 0 hits | Skill docstring lines 41-43 say "channel adapter handles consent uplink in Phase 1" — not yet wired |
| 5 | Photo download from CDN, size cap | ❌ MISSING | grep `MAX_PHOTO_BYTES\|PHOTO_DOWNLOAD_TIMEOUT\|_PhotoTooLargeError` in apps → 0 hits | Skill reads `conversation.last_photo_bytes` — punted to channel adapter; channel adapter side not in this audit |
| 6 | Loading-card edit-message pattern | ❌ MISSING | grep `render_loading_card\|edit_message` in apps/skills → 0 hits | UX regression (cold ~3-7s feels frozen) |
| 7 | `scan_photo` with error branches | ✅ PORTED | `apps/skills/food_scanner/skill.py:137-160` | Three branches present |
| 8 | Scan card render (v2) | ⚠️ PARTIAL | `apps/skills/food_scanner/skill.py:252-283 (_format_scan_card)` | Simpler text-only render, no `ai_ui.render_food_scan_v2` parity (no hedge of macros JSON shape) |
| 9 | Meal-type buttons (4 types) | 🔄 INTENTIONALLY_REMOVED | docstring lines 44-48 — Sprint 9 cut, defaults to `meal_type="other"` | Documented cut |
| 10 | Idempotency-key UUID5 | ⚠️ PARTIAL | `apps/skills/food_scanner/skill.py:205` uses `f"diary:{external_id}:{scan_id}"` string key | Different shape; functionally idempotent at Ayla side per `log_meal` contract |
| 11 | `render_food_logged_with_footer` | ⚠️ PARTIAL | line 227 `f"Записала: {log.dish_name} — {int(log.calories)} ккал."` | Plain string, no footer keyboard |
| 12 | Evening-inline daily report | 🔄 INTENTIONALLY_REMOVED | docstring lines 48-50 — "belongs in P3 nutrition_anketa or notification job" | Documented cut, but no follow-up port exists |
| 13 | Cross-domain insight hook on log | ❌ MISSING / 🔄 BY DESIGN | grep `_maybe_send_cross_domain_card\|render_cross_domain_card` in apps → 0 hits | Per memory `cross_domain_insight_safety_gap` cards REMOVED for MVP; matches removal |
| 14 | `/дневник` text command | ❌ MISSING | grep `/дневник\|on_diary_command\|in_\(("/день"` in apps → 0 hits | No diary text-command route in apps |
| 15 | `daily_report_footer_keyboard` | ❌ MISSING | grep `daily_report_footer_keyboard` in apps → 0 hits | — |

**Delete risk:** **HIGH**. Five missing features include the consent gate (legal — 152-FZ), the `/дневник` command, the channel-side photo download with size cap, the loading-card UX, and the FSM-aware skip. Until channel adapter + Phase 1 wiring confirmed, legacy file MUST remain.

**PORT_NOW recommendations:**
- **Photo download + size cap + loading card** — confirm `apps/channels/max/` (or whichever ingests MAX photos) handles the URL→bytes→`conversation.last_photo_bytes` stash with 10 MiB cap. Audit didn't cover that. (~2-4h verification, port if missing)
- **152-FZ consent gate** — port `food_scanner_consent_at` field check + accept/decline callbacks. Legal blocker for Russian pilot. (~3-4h)
- **`/дневник` text command** — port to a small "diary" skill or extend `food_scanner`. (~1-2h)
- **FSM-aware skip** — once `conversation.skill_state` lands (P3), gate photo path on it. (~30min)
- **`NUTRITION_ENABLED` flag + COMING_SOON_TEXT** — flag-gate the entire flow for ops control. (~30min)

---

### health_screening.py (533 → ~150 LOC)

**Current port:** `apps/skills/health_screening/skill.py` (107 LOC) + `apps/skills/health_screening/classifier.py` (135 LOC) — **note: this is a different feature.**

**Legacy feature inventory:**
1. Public entry `start_health_screening` invoked from ai_assistant pre-hook / explicit opt-in [79-88]
2. Tier-B consent screen with 2-button keyboard [40-43, 94-112]
3. `_persist_health_flag(bot_user, key, value)` JSON-field upsert helper [61-69]
4. Screen 1 — pregnancy yes/no, persist `pregnant` flag [155-172]
5. Screen 1b — breastfeeding yes/no/skip [178-205]
6. Screen 2 — diabetes 4-option (no/t1/t2/pre) [208-237]
7. Screen 2b — chronic conditions multi-select with toggle/done/none + state-stored `chronic_selected` list + edit-message refresh [243-320]
8. Screen 3 — allergies choice (none/text/vague) + free-text branch with `parse_allergies()` LLM-assisted parser [342-402]
9. Screen 3b — meds yes/no/skip [408-441]
10. Conditional menopause screen (only `age≥45 AND gender=='female'`) [430-442, 455-475]
11. Screen 4 — menopause no/yes/unsure/skip [445-475]
12. `_fetch_age_and_gender(bot_user)` reads cached TIER-A profile fields [326-339]
13. `_build_ayla_payload` whitelist of 8 keys → Ayla `upsert_profile(POST /profile/)` [481-504]
14. `render_overrides_applied(profile)` — "Учла важное" output formatting + macros recap + water target [519-533]
15. `health_consent_acked_at` / `health_consent_declined_at` timestamping [104-106, 124-127]
16. Decline path with "если передумаешь, напиши «настрой советы»" CTA [128-135]
17. Failure-mode handling: Ayla `upsert_profile` down → "сохранила, советы появятся чуть позже" [504-518]

**Coverage:**
| # | Feature | Status | Evidence | Notes |
|---|---|---|---|---|
| ALL 17 | Tier-B FSM (consent → pregnancy → breastfeeding → diabetes → chronic → allergies → meds → menopause → Ayla upsert → render overrides) | ❌ MISSING | `apps/skills/health_screening/skill.py:27-38` docstring explicitly: "The original `legacy_maxbot/handlers/health_screening.py` (533 LOC) is the Tier-B nutrition pre-anketa flow ... The full Tier-B port is tracked separately (TBD ticket)". grep `tier_b\|upsert_profile\|render_overrides_applied\|awaiting_pregnancy\|awaiting_diabetes\|awaiting_chronic\|awaiting_menopause` in `apps/` → 0 hits. | Zero ported. The current skill is a **separate pain-triage feature** (DRF-358 T04). |
| BONUS | Pain-mention classifier with soft/red-flag tiers + 1-shot reply | ✅ ADDED (new feature, no legacy equivalent) | `apps/skills/health_screening/classifier.py:33-103` + skill | Net NEW capability, not in legacy. Legitimately keeps the namespace busy but doesn't replace it. |

**Delete risk:** **HIGH**. The legacy file IS the only implementation of Tier-B FSM in the repo. Deleting it without porting deletes the entire pregnancy/diabetes/menopause/allergies/meds safety-screening flow — legally meaningful (medical context, 152-FZ adjacent), pilot-relevant per `mysite_origin_history` memory ("nutrition anketa production-validated 30+ days").

**PORT_NOW recommendations:**
- **Full Tier-B FSM port** — 8-screen lazy FSM, Ayla `upsert_profile` upsert, override render. Estimated effort: ~3-5 days (1 dev) given groundwork: `apps/skills/base.SkillFSM`, `apps/integrations/ayla/nutrition_client.upsert_profile` (verify exists), `apps/skills/nutrition_anketa/fsm.py` precedent. **Owner stream:** founder pilot scope memory `pilot_scope_discipline` says wellness MVP is in scope for 15-July pilot — this is **W1 / Wellness OS work**. File a dedicated ticket; mark as pilot-blocker if Tier-B is in pilot scope.
- **OR** explicit founder/tech-lead waiver: Tier-B deferred post-pilot. Currently no such waiver exists; the only related memory (`pilot_scope_discipline`, `variant_b_wellness_mvp`) implies full wellness is in pilot scope.

---

### food_correction.py (361 → 120 LOC)

**Current port:** `apps/skills/food_correction/skill.py`.

**Legacy feature inventory:**
1. `cb:scan:correct:menu:{scan_id}` → open correction menu with `food_scan_correct_menu_keyboard` [41-58]
2. `cb:scan:correct:portion:menu` → portion submenu [PAYLOAD_SCAN_PORTION_OPEN_MENU, 62-80]
3. `cb:scan:correct:portion:smaller|normal|larger` → stub "пришли фото снова" (legacy notes Phase 3.2 will recalc via `scan_photo(portion_multiplier=...)`) [83-109]
4. `cb:scan:correct:other_dish` — Phase 3.2 stub [118-127]
5. `cb:scan:correct:add_ingredient` — Phase 3.2 stub [130-140]
6. `cb:scan:correct:delete` — Phase 3.2 stub (needs `DELETE /food-log/{id}/`) [143-152]
7. `cb:scan:retake` → "пришли фото ещё раз" [155-166]
8. `cb:scan:manual` — Phase 3.2 stub [169-179]
9. `cb:nutrition:view_day` → hybrid daily report (daily_summary + water_today + `render_daily_full_report`) [182-234]
10. `cb:report:weekly` — Phase 3.3 stub [237-254]
11. `cb:report:time` settings menu (18:00/21:00/23:00/off) → persist `daily_report_time` setting [257-305]
12. `cb:water:reminders:footer` → water-reminders settings menu (status + toggle keyboard) [308-335]
13. `cb:water:reminders:toggle` → persist `water_reminders_enabled` setting [338-361]

**Coverage:**
| # | Feature | Status | Evidence | Notes |
|---|---|---|---|---|
| 1-6 | Correction sub-menu (portion / other_dish / add_ingredient / delete / portion-multiplier) on `cb:scan:correct:*` wire | ❌ MISSING (different wire format) | `apps/skills/food_correction/skill.py:60-94` ports a totally different surface: 3 prompts (`grams`, `name`, `macros`) on `cb:food:correct:*` payloads (note `food` not `scan`). Sub-menu + stubs gone. | Different design — the apps version is a one-prompt-per-field flow, not a sub-menu. Both shapes have "Phase 3.2/Phase 1" work tracked as the apply path. |
| 7 | Retake | ❌ MISSING | grep `cb:scan:retake` in apps → 0 hits | — |
| 8 | Manual input stub | ❌ MISSING | grep `cb:scan:manual` in apps → 0 hits | — |
| 9 | `cb:nutrition:view_day` hybrid daily report | ❌ MISSING | grep `cb:nutrition:view_day\|daily_summary\|render_daily_full_report` in apps → 0 hits | Sibling of `/дневник`; same gap as food_scanner #14 |
| 10 | Weekly report stub | ❌ MISSING | grep `cb:report:weekly\|PAYLOAD_REPORT_WEEKLY` in apps → 0 hits | Low-value stub, OK to skip |
| 11 | Daily-report time settings (18/21/23/off) | ❌ MISSING | grep `daily_report_time\|cb:report:time` in apps → 0 hits | Phase 3.1 Part 2D feature; user-facing setting |
| 12-13 | Water-reminder settings menu + toggle | ❌ MISSING | grep `water_reminders_enabled\|cb:water:reminders` in apps → 0 hits | Production-validated feature per `mysite_origin_history` memory |

**Delete risk:** **MEDIUM-HIGH**. The "apply correction" half is documented as deferred (Phase 1, `DRF-825 follow-up`), but the SETTINGS half (daily report time, water reminders toggle) is unrelated to corrections and is a user-facing feature with no port. Also `cb:nutrition:view_day` (sibling of `/дневник` text command) is missing — same regression as food_scanner.

**PORT_NOW recommendations:**
- **Daily report time settings** (`cb:report:time:*` → `daily_report_time` BotUser setting) — ~2-3h
- **Water reminders settings menu + toggle** — ~2-3h, but verify `apps/skills/water/` doesn't already own this surface
- **Correction APPLY path** (portion-multiplier / dish-rename / manual macros → Ayla update endpoint) — pilot-relevant but documented as Phase 1 backlog. Confirm with tech-lead whether pilot ships with prompt-only correction OK.

---

### cross_domain.py (281 → 162 LOC)

**Current port:** `apps/skills/cross_domain/skill.py`.

**Legacy feature inventory:**
1. `render_cross_domain_card(insight)` — text + 2-button row (📅 Записаться / Не сейчас) [48-74]
2. `_shown_id_from_payload` helper [80-84]
3. `_category_for_slug(slug)` ORM lookup helper [87-95]
4. `_send_booking_redirect` — opens booking flow with pre-selected `service_category_slug`; falls back to top-level category list [98-148]
5. `cb:cross:dismiss:{shown_id}` callback — POST `/dismiss/` + "поняла, не буду беспокоить" reply [154-187]
6. `cb:cross:convert:{shown_id}` callback — looks up insight, POST `/convert/` with placeholder `appointment_id`, then `_send_booking_redirect` [190-252]
7. `cb:cross:seen:{shown_id}` callback — fire-and-forget telemetry [255-281]
8. PII safety rule (no `insight_text`/`rationale_text` at INFO log level)

**Coverage:**
| # | Feature | Status | Evidence | Notes |
|---|---|---|---|---|
| 1 | `render_cross_domain_card` — auto-rendering insight cards | 🔄 INTENTIONALLY_REMOVED | grep `render_cross_domain_card\|_maybe_send_cross_domain_card` in apps → 0 hits. Per memory `project_cross_domain_insight_safety_gap`: cross-domain insights REMOVED from MVP (food-scanner F4 + dashboard Block 6) due to anti-medical / anti-shame filter gap | **Documented removal, expected.** |
| 2 | `_shown_id_from_payload` | ✅ PORTED | `apps/orchestrator/ui/keyboards.py::parse_callback` used in `apps/skills/cross_domain/skill.py:83-86` | Generic callback parser |
| 3-4 | `_category_for_slug` + `_send_booking_redirect` — actual redirect to booking flow with pre-selected category | ⚠️ PARTIAL (degraded to ACK-only) | `apps/skills/cross_domain/skill.py:147-162` returns `"Поняла, передаю менеджеру..."` without actually opening booking flow; docstring 28-30 says "Sprint 9 cut: we tell them... Phase 1 wires this to the booking skill (DRF-839)". | Apps version routes to manager handoff instead of in-bot booking redirect. Functional regression once booking skill ships. |
| 5 | `dismiss` callback + POST `/dismiss/` | ✅ PORTED | `apps/skills/cross_domain/skill.py:128-145` | Includes graceful Ayla-down fallback |
| 6 | `convert` callback + POST `/convert/` + booking redirect | ⚠️ PARTIAL | `apps/skills/cross_domain/skill.py:147-162` does NOT call `post_cross_domain_convert` (deliberate — no appointment_id) and does NOT redirect; legacy DID call `/convert/` with placeholder appointment_id | Telemetry signal lost in apps (legacy logged conversion intent on click; apps doesn't). |
| 7 | `seen` callback | ✅ PORTED | `apps/skills/cross_domain/skill.py:110-126` | Identical fire-and-forget |
| 8 | PII safety rule | ✅ PORTED | Docstring 32-36 + log calls use `shown_id` only | Verbatim. |

**Delete risk:** **LOW-MEDIUM**. Per memory `cross_domain_insight_safety_gap`, the rendering half is intentionally gone for MVP. The remaining gap (convert→booking-redirect with `service_category_slug`) is documented as Phase 1 backlog (DRF-839) and isn't pilot-blocking if conversion intent is acceptably handled via manager handoff. **Recommend retain until founder signs off given the memory caveat (safety gap audit post-pilot required).**

**PORT_NOW recommendations:** None pre-pilot. Add to FOLLOW_UP backlog:
- POST `/convert/` telemetry signal (currently dropped — analytics blind spot)
- `_send_booking_redirect` once booking skill (DRF-839) lands
- Anti-medical / anti-shame safety filter audit before re-enabling card rendering (memory `cross_domain_insight_safety_gap`)

---

## Summary

| Metric | Count |
|---|---|
| Total PORT_NOW gaps across 4 files | ~10 (5 food_scanner + 1 health_screening + 3 food_correction + 1 cross_domain follow-up) |
| Delete-ready files (no sign-off needed) | **0** |
| Files needing sign-off before delete | **4** (all four) |
| Highest-risk file | `health_screening.py` — 533 LOC Tier-B FSM with **zero** port; only a separately-scoped pain classifier squats the namespace |
| Cross-doc-confirmed intentional removals | cross-domain card rendering (memory `cross_domain_insight_safety_gap`); meal-type buttons + evening-inline (Sprint 9 docstring); correction APPLY path (Phase 1 backlog) |

### Top PORT_NOW gaps (ranked by risk)

1. **Tier-B health-screening FSM** (`health_screening.py`) — 8-screen FSM + Ayla `upsert_profile` + override render. Pilot-blocking for Wellness MVP if 15-July pilot retains nutrition advice; legally sensitive (pregnancy/diabetes context). Effort: 3-5 dev-days. **Owner: W1 / Wellness.**
2. **Photo pipeline + consent + `/дневник`** (`food_scanner.py`) — 152-FZ consent gate, 10 MiB photo download/cap, loading card, `/дневник` text command, FSM-aware skip, `NUTRITION_ENABLED` flag. Pilot-blocking. Effort: 1-2 dev-days assuming channel adapter side already does some of this. **Owner: W1 / channel adapter team.**
3. **Daily report time + water reminder settings** (`food_correction.py`) — production-validated feature per `mysite_origin_history`; user-controllable nudges. Effort: ~half-day. **Owner: W1 / nudges stream.**

## Open questions

- **Channel adapter photo path coverage:** the `food_scanner` skill relies on `conversation.last_photo_bytes` set upstream. Did NOT verify whether `apps/channels/max/` actually downloads + size-caps photos. If it doesn't, the 5 PORT_NOW gaps under food_scanner are higher-risk than stated.
- **`apps.integrations.ayla.NutritionClient.upsert_profile` existence:** Tier-B port assumes Ayla side already exposes `POST /profile/`. Not verified in this audit; check Ayla djangoproject side before scheduling Tier-B port.
- **Tier-B inclusion in 15-July pilot:** memory `pilot_scope_discipline` + `variant_b_wellness_mvp` together imply yes, but no explicit founder verdict on Tier-B FSM scope was located. Founder sign-off needed before scheduling 3-5 dev-days.
- **`cb:nutrition:view_day` vs `/дневник` design:** both surface daily report; unclear if one will replace the other in apps. Currently both gone.
- **Outstanding "intentional removal" memory verification:** the `cross_domain_insight_safety_gap` memory specifies food-scanner F4 + dashboard Block 6 cards removed. The dismiss/convert/seen callback handlers in apps could theoretically still fire if some other surface ever rendered a card — but with no renderer, they're effectively dead code that still loads. Confirm whether to fully tombstone the skill or keep the callbacks for forward-compat.
