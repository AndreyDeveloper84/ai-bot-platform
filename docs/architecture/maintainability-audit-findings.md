# Maintainability Audit — 3 Repos Consolidated Findings

**Date:** 2026-05-29
**Layers:** L1 CLI (vulture/radon/deptry) + L2 hex-graph (clones/hotspots/unused)
**Scope:** ai-bot-platform · Ayla/djangoproject · ayla-ai-core

## Cross-Repo Health Comparison

| Метрика | bot-platform | Ayla | ayla-ai-core |
|---|---|---|---|
| **Verdict** | 🔴 SICK | 🟡 OK | 🟢 CLEAN |
| Legacy dead dirs | 3 | 0 | 0 |
| Deptry issues | 29 651 | 16 (false-pos) | n/a |
| Clone groups | 89 | 24 | 0 |
| Worst CC | 40 | **47** | 16 |
| Worst MI prod | 8.66 | 17.01 | n/a |
| Real prod dead code | handful + 3 legacy | ~0 | 0 |

## bot-platform — Concentrated Debt

### P0 — Legacy dead directories (delete entire)
- `legacy_maxbot/` (29k+ deptry issues, dead duplicates incl `looks_like_food_drink`)
- `legacy_notifications/`
- `legacy_formulatela_mcp/`

**Action:** delete all 3 + verify no live imports. Single PR = -29k issues.

### P0 — Complexity hotspots (E/D rank)
| File:Line | Function | CC |
|---|---|---|
| admin_api/views_services_mapping.py:266 | services_mapping_bulk | 40 |
| admin_api/services/master_deactivation.py:540 | execute_deactivation | 39 |
| skills/booking/tools.py:1590 | execute_reschedule | 35 |
| eventbus/ingest_envelope.py:109 | parse_envelope | 31 |
| admin_api/views.py:490 | _master_update | 29 |
| catalog/.../seed_from_mysite.py:57 | handle | 25 |
| admin_api/services/availability.py:256 | list_pending_for_admin | 23 |
| channels/max/handler.py:263 | _handle_max_event_inner | 21 |

### P1 — Scattered helpers (DRY)
- `_parse_json_body` ×6 across admin_api + internal_chat + master_api views
- `_split_name` ×3 in master_api/services
- `initials` ×3 in master/admin screens
- `pluralRu` ×2 (Russian pluralization util)
- `firstName` ×2 admin screens
- `isoDateNDaysAhead` ×2 booking/reschedule screens
- `request` HTTP client ×2 (near-miss) api.ts vs internal-chat-api.ts
- `_sign` ×7 test fixtures

### P1 — Fat module
- `skills/booking/tools.py` — 2500 LOC, MI 8.66

### P1 — Test architecture
- `master_api/tests/test_ai_drafts.py` — MI 0.00 (unmaintainable)
- 30+ `master_service` unused-var test fixtures (test smell)

## Ayla djangoproject — Focused Refactor

### P0 — Complexity hotspots
| File:Line | Function | CC |
|---|---|---|
| nutrition/services/profile_upsert_service.py:185 | _serialize | **47** |
| tenants/management/commands/backfill_tenants.py:57 | handle | 30 |
| nutrition/services/pattern_detection_service.py:262 | _collect_food_stats | 28 |
| payments/views.py:300 | PaymentWebhookView | 25 |
| users/personal_context_events.py:26 | _emit | 25 |
| payments/views.py:342 | PaymentWebhookView.post | 24 |
| nutrition/services/cross_domain_engine.py:220 | _cooldown_ok | 23 |
| ai/.../recommendation_engine.py:371 | _score_service_match | 22 |
| ai/concierge_factory.py:122 | render_ayla_system_prompt | 22 |
| nutrition/services/nutrition_profile_service.py:123 | compute_norms | 21 |
| nutrition/services/pattern_detection_service.py:815 | _build_pattern | 21 |
| notifications/outbox_handlers.py:106 | _appointment_context | 21 |

`payments/views.py` overlap с integration audit P0-3 (payment contract drift).

### P1 — Real prod clones (non-test)
- `get_distance_km` ×2 — search/views.py + users/specialists_api.py
- `update` ×2 — services/views.py + users/views.py

### P2 — Test infrastructure DRY
20 of 24 clone groups = test fixtures (`auth_client` ×9, `specialist` ×5, `_clear_cache` ×6, `test_pro_app_type_returns_403` ×4). Extract shared conftest base.

## ayla-ai-core — Reference Quality

6887 LOC, 22% comments, 0 dead code, max CC 16, 0 production clone duplication.
**No refactoring needed. Reference for other repos.**

## Next Layer
- L3 AI judgment via codebase-audit-suite ln-620 master (37 auditors)
- L3 Software Architect synthesis → unified remediation roadmap
- ADR-0009 import-linter contracts (executable layer enforcement)
