# E0.1 — AI/LLM Cluster Migration Coverage Audit

**Date:** 2026-05-31
**Auditor:** general-purpose agent (E0.1)
**Scope:** AI/LLM cluster in `legacy_maxbot/` vs current `apps/*`
**Verdict:** **PARTIAL — multiple high-value capability gaps remain ported**

The founder's intuition is confirmed: while the *skeleton* of the AI/LLM
layer has been ported (LLM provider abstraction, orchestrator pipeline,
booking + FAQ skills, KB retrieval, conversations model, voice examples),
**several concrete behaviours that live in `legacy_maxbot/` have no
equivalent yet** in `apps/*`. Most gaps are not architectural — they're
specific features (RAG response cache, MCP client, recommend_services
tool, ask_clarification fallback semantics, ai_parsers LLM hybrid,
silent-promise retry, fast-path phatic intent router, ai_ui callback
wire-format) that were dropped or deferred during the rewrite.

Two items are *deliberately* gone per ADR-0009 + the wellness pivot
(MCP/chromadb is replaced by `apps/kb/chromadb_client.py`; `ai_yclients`
direct fetch is replaced by per-skill `tools.py` against catalog/REST).
The rest are genuine gaps worth re-confirming.

## Method

1. Listed every `legacy_maxbot/**/*.py` and filtered to files whose
   filename or top-of-file docstring marks them as AI/LLM cluster
   (calls OpenAI / orchestrates LLM tools / manages prompt assembly /
   does RAG / parses LLM output / handles AI conversation lifecycle).
2. Read the first ~50–200 lines of every legacy AI file to confirm
   purpose; deep-read load-bearing modules (`llm.py`, `ai_concierge.py`,
   `ai_prompts.py`, `ai_tools.py`, `ai_tool_handlers.py`, `ai_context.py`,
   `ai_parsers.py`, `intents.py`, `ai_store.py`, `mcp_client.py`,
   `voice_examples.py`, `tier_b_triggers.py`, `response_cache.py`,
   `personalization.py`, `popular_questions.py`).
3. Searched `apps/*` with Grep (excluding `.claude/worktrees/`) for the
   identifier patterns load-bearing in each legacy module: `is_giveup`,
   `MCPClient`, `response_cache`, `recommend_services`,
   `ask_clarification`, `tool_choice="required"`, `detect_intent`
   (phatic), `tier_b_triggers`, `parse_age_value`, `RAG_MIN_SCORE`,
   `MasterContext`, `enrich_show_masters`, `popular_questions`.
4. Compared shape — not just "name exists" but "does the current code
   do what the legacy code did". When in doubt, defaulted to PARTIAL.

## Legacy AI/LLM surface area

Total files in scope: **27**
Total LOC in scope: **7 869**

### Sub-clusters identified

#### 1. LLM client + RAG loop
- `legacy_maxbot/llm.py` (251 LOC) — `AsyncOpenAI` factory with RU proxy,
  MCP→OpenAI tools schema converter, `chat_with_tools` tool-use loop,
  `chat_rag` RAG-as-context, `LLM_GIVEUP_MESSAGE` + `is_giveup` detector,
  `RAG_MIN_SCORE=0.45`, `RAG_TOP_K=3`.
- `legacy_maxbot/popular_questions.py` (28 LOC) — 10 hardcoded popular
  questions for warmup.
- `legacy_maxbot/warmup.py` — preloads `response_cache` at boot.
- `legacy_maxbot/response_cache.py` (65 LOC) — Redis-backed normalised
  question→answer cache, 24h TTL, ~50ms vs ~6.7s warm/cold replies.

#### 2. AI Concierge pipeline (the main chat orchestrator)
- `legacy_maxbot/ai_concierge.py` (422 LOC) — `send_message()` 12-step
  pipeline: resolve conversation, save user msg, build master context,
  load history, render system prompt, compose OpenAI messages, OpenAI
  call with TOOL_DEFINITIONS, parse + dispatch tool_call,
  **silent-promise retry** with `tool_choice="required"`, **yclients
  enrichment** for slots/bookings, save assistant message with
  action_type + action_data + tokens + latency. Returns `ChatResponseDTO`.
- `legacy_maxbot/ai_store.py` (76 LOC) — `BotConversationStore` shim
  over ORM (resolve/save/load history) for the post-migration
  `ayla_ai_core.orchestrator.ConversationStore` Protocol.
- `legacy_maxbot/ai_context.py` (167 LOC) — `MasterContext` builder:
  Top-N masters + categorised services + frozenset for
  anti-hallucination ID checks + summary text for system prompt.
- `legacy_maxbot/ai_prompts.py` (497 LOC) — `SYSTEM_PROMPT_TEMPLATE` +
  `render_system_prompt(today, client_name, bookings_count,
  master_context, last_visits, extra_hint, advice_mode)` — 11 behaviour
  rules, degraded vs full advice mode, client-history block,
  cross-domain hint.

#### 3. LLM tool catalog + dispatch
- `legacy_maxbot/ai_tools.py` (259 LOC) — 6 OpenAI tool specs:
  `show_masters`, `show_slots`, `confirm_booking`, `show_my_bookings`,
  `recommend_services`, `ask_clarification`. `ActionType` enum.
- `legacy_maxbot/ai_tool_handlers.py` (336 LOC) — side-effect-free
  validators per tool, `_fallback_clarification` always returns
  `ask_clarification` action when LLM hallucinates IDs; `dispatch_tool_call`
  master dispatcher.
- `legacy_maxbot/ai_yclients.py` (304 LOC) — async enrichment functions
  (`enrich_show_masters`, `enrich_show_slots`, `enrich_show_my_bookings`)
  that fetch real slot availability from YClients after tool_handler
  validation.

#### 4. AI render layer (UI rendering — borderline AI cluster)
- `legacy_maxbot/ai_ui.py` (1 016 LOC) — action_type → MAX inline
  keyboard renderer. `cb:ai:*` callback wire-format (pick_master,
  pick_slot, confirm, cancel, edit, answer). Renders 5 action types.
- `legacy_maxbot/ai_action_service.py` (376 LOC) — `execute_confirm_booking`
  callback: idempotency cache, BookingRequest creation, YClients
  record creation, graceful fallback when no `yclients_staff_id`.

#### 5. Intent router (fast-path before LLM)
- `legacy_maxbot/intents.py` (81 LOC) — phatic regex matcher
  (greeting / thanks / small-talk). Canned responses, $0 LLM, ~ms.

#### 6. Conversational free-text handlers
- `legacy_maxbot/handlers/ai_assistant.py` (391 LOC) — `on_free_text`
  + `run_ai_turn`: typing indicator, intent router fast-path, food/drink
  hint card, water log shortcut, health-signal trigger, BotInquiry
  escalation on giveup.
- `legacy_maxbot/handlers/ai_callbacks.py` (461 LOC) — `cb:ai:*`
  callback handlers, pick→pseudo-user-message loop.
- `legacy_maxbot/handlers/fallback.py` (35 LOC) — last-router fallback
  to main menu.
- `legacy_maxbot/handlers/faq.py` (86 LOC) — HelpArticle list + answer
  (curated FAQ, not RAG).
- `legacy_maxbot/handlers/cross_domain.py` (281 LOC) — render cross-domain
  insight card + convert/dismiss/seen callbacks.

#### 7. LLM-assisted parsers (hybrid regex+LLM)
- `legacy_maxbot/ai_parsers.py` (690 LOC) — `parse_age`, `parse_height`,
  `parse_weight`, `parse_allergies`, `parse_beverage` etc. Regex ladder
  first, LLM tool-call fallback (gpt-4o-mini, `tool_choice` forced)
  gated by `len(text) ≤ 30`. `REFUSED` sentinel for explicit declines.
- `legacy_maxbot/food_drink_hints.py` (~150 LOC) — pure regex food/drink
  heuristic (no LLM, but feeds the LLM pipeline).
- `legacy_maxbot/tier_b_triggers.py` (44 LOC) — pure regex health-signal
  detector (pregnancy, breastfeeding, diabetes, hypertension,
  eating_disorder).

#### 8. Personalization + segmentation + voice
- `legacy_maxbot/personalization.py` (138 LOC) — `get_or_create_bot_user`,
  `greet_text`, `update_context`, `get_client_history` (bookings_count,
  last 3 visits — fed into system prompt).
- `legacy_maxbot/segmentation.py` (65 LOC) — Phase 3 50/50 A/B middleware
  on `bot_user.id`.
- `legacy_maxbot/voice_examples.py` (147 LOC) — `FORMULA_TELA_EXAMPLES`
  (5) + `DIAGNOSTIC_FIRST_PAIN_EXAMPLES` (5) — few-shot pool for the
  system prompt.

#### 9. MCP integration
- `legacy_maxbot/mcp_client.py` (143 LOC) — persistent stdio
  `MaxbotMCPClient` singleton wrapping `formulatela_mcp` subprocess
  (chromadb-backed). Used by `chat_with_tools` for tool-use loop and
  `chat_rag` for `search_faq`.

## Coverage table

| Legacy file (or sub-cluster) | LOC | Current equivalent | Coverage | Evidence | Risk if deleted |
|---|---|---|---|---|---|
| `legacy_maxbot/llm.py` — async OpenAI client + RU proxy | 251 | `apps/llm/providers/openai_provider.py`, `apps/llm/router.py`, `apps/orchestrator/llm/openai_provider.py` | **PARTIAL** | Provider + router exist. RU `OPENAI_PROXY` httpx-client pattern not visible in `apps/llm/providers/openai_provider.py` — verify before pilot. | MEDIUM — Russia LLM unreachable without proxy |
| `legacy_maxbot/llm.py::chat_with_tools` — tool-use loop | (incl.) | `apps/skills/booking/skill.py`, `apps/skills/faq/skill.py` two-call patterns | **PARTIAL** | Skills hardcode one tool-call hop each. No general N-iteration loop; legacy had `MAX_TOOL_ITERATIONS=5` reusable across callers. | LOW — current shape may be intentional |
| `legacy_maxbot/llm.py::chat_rag` — RAG-as-context | (incl.) | `apps/skills/faq/skill.py` + `apps/kb/services/retriever.py::search_kb` | **PARTIAL** | FAQ skill issues `search_knowledge_base` tool, parses chunks, second LLM call grounded. `RAG_MIN_SCORE=0.45` threshold gating present as "confidence < 0.5 → handoff" in skill but not the legacy "low score → empty FAQ context, let LLM redirect" semantic. | LOW |
| `legacy_maxbot/llm.py::is_giveup` + `LLM_GIVEUP_MESSAGE` | (incl.) | none | **NONE** | `grep is_giveup\|GIVEUP_MESSAGE` in `apps/` → 0 hits. Current skills use `should_handoff=True` + `handoff_reason` instead. Functionally similar but the canonical text + detector are gone, meaning if LLM produces the legacy phrase, no auto-handoff fires. | LOW — current contract supersedes |
| `legacy_maxbot/response_cache.py` — 24h answered-question cache | 65 | none | **NONE** | `grep response_cache\|popular_questions\|warmup` in `apps/` → 0 hits. Hot-path (3–7s) regression risk on pilot if not added. | MEDIUM — pilot latency budget hit |
| `legacy_maxbot/popular_questions.py` — warmup seed | 28 | none | **NONE** | same | LOW — only matters with response_cache |
| `legacy_maxbot/warmup.py` — boot-time cache fill | small | none | **NONE** | same | LOW |
| `legacy_maxbot/intents.py` — phatic regex fast-path | 81 | none | **NONE** | `grep detect_intent\|GREETING_RE\|THANKS_RE` in `apps/` → 0 hits. `apps/orchestrator/intent_router.py` is an LLM-based classifier (gpt-4o-mini structured JSON) that ALWAYS spends a token for "привет"/"спасибо". Legacy bypassed for $0 + ~50ms. | MEDIUM — cost & latency hit on every greeting |
| `legacy_maxbot/ai_concierge.py::send_message` 12-step pipeline | 422 | `apps/orchestrator/pipeline.py::turn` 19-step + `apps/skills/booking/skill.py` | **PARTIAL** | Orchestrator pipeline supersedes the structural concierge — wider responsibilities (safety pre/post, intent router, composer, replay). But several concrete features are not ported: see below. | covered by row-level rows |
| ↳ silent-promise retry (`_looks_like_promise_without_tool` + `tool_choice="required"`) | (incl.) | none | **NONE** | `grep _PROMISE_STEMS\|promise_without_tool` in `apps/` → 0 hits. Legacy mitigates LLM "I'll find masters" without tool_call by retrying forced. Without this, current skills can emit promise-text and call no tool, leaving the user hanging. | MEDIUM — concrete UX failure mode |
| ↳ master context fed into system prompt (`MasterContext`) | (incl.) | none | **NONE** | `grep MasterContext\|build_master_context\|specialist_context` in `apps/` → 0 hits. Booking skill (`apps/skills/booking/tools.py`) instead loads masters per-tool at call time. Anti-hallucination guard is per-tool validator, but the system prompt currently does NOT receive the candidate list with explicit IDs. | HIGH — anti-hallucination guard weakens without prompt-side ID list |
| ↳ `enrich_show_masters/slots/my_bookings` async YClients | (incl.) | none in apps/skills | **NONE** | `grep enrich_show_masters\|ai_yclients` in `apps/` → 0 hits. Booking skill does the work, but the legacy pattern of "validate then enrich slots" is fully absorbed into booking skill code. (Probably acceptable.) | LOW |
| `legacy_maxbot/ai_store.py` — ConversationStore adapter | 76 | `apps/conversations/services.py` + `apps/conversations/models.py` | **FULL** | `apps/conversations/models.py` lines 437–465 — `action_type`, `action_data`, `tool_call`, `tool_call_id`, `tokens_in`, `tokens_out`, `latency_ms` all present. Conversation lifecycle in `apps/conversations/services.py`. | LOW |
| `legacy_maxbot/ai_context.py::MasterContext` + `build_master_context` | 167 | none | **NONE** | see ai_concierge.MasterContext row above. | HIGH (same as above) |
| `legacy_maxbot/ai_prompts.py::SYSTEM_PROMPT_TEMPLATE` + `render_system_prompt` | 497 | `apps/skills/booking/prompts.py::build_booking_prompt`, `apps/skills/faq/prompts.py::build_faq_prompt` | **PARTIAL** | Skill-level prompts replace the monolithic concierge prompt. But: (1) `client_history_block` (bookings_count, last_visits) is not visible in booking/faq prompts; (2) `extra_hint` (cross-domain nutrition deficit) is not threaded into skill prompts; (3) `advice_mode` (full vs degraded for unscreened users) is not visible. | MEDIUM — Tier-B advice gate may not be enforced in the AI path |
| `legacy_maxbot/ai_tools.py` — 6 tool specs | 259 | `apps/skills/booking/tools.py` (5 tool specs incl. cancel/reschedule), `apps/skills/faq/tools.py` (1 tool spec) | **PARTIAL** | `SHOW_MASTERS`, `SHOW_SLOTS`, `CONFIRM_BOOKING`, `SHOW_MY_BOOKINGS` present in booking. `CANCEL_BOOKING_TOOL_SPEC` + `RESCHEDULE_BOOKING_TOOL_SPEC` ADDED (post-pivot extension). **`RECOMMEND_SERVICES` MISSING**: `grep recommend_services` in `apps/` → 0 hits. **`ASK_CLARIFICATION` MISSING**: `grep ASK_CLARIFICATION\|ask_clarification` in `apps/` → 1 hit, only in `apps/orchestrator/ayla_adapter.py` docstring. | MEDIUM — discovery-by-goal UX missing; LLM fallback to clarification on hallucination missing |
| `legacy_maxbot/ai_tool_handlers.py::_fallback_clarification` semantic | 336 | none | **NONE** | When LLM hallucinates master_id, legacy returns an `ask_clarification` action that asks the user to refine. Current booking skill returns `should_handoff=True` with `booking_invalid_master_id` (sends user to a human). The legacy "bounce to clarification, don't escalate" pathway is gone. | MEDIUM — more handoffs than necessary on LLM ID hallucination |
| `legacy_maxbot/ai_yclients.py` — async enrichment | 304 | absorbed into `apps/skills/booking/tools.py` | **FULL** | Booking skill tools fetch directly. | LOW |
| `legacy_maxbot/ai_ui.py` — render action → MAX keyboard | 1 016 | `apps/orchestrator/composer.py` + `apps/orchestrator/ui/keyboards.py` + per-channel renderers in `apps/channels/max/` | **PARTIAL** | Composer + keyboards exist. The legacy `cb:ai:*` wire-format (pick_master, pick_slot, confirm, cancel, edit, answer) does NOT appear in `apps/` — `grep "cb:ai:"` in `apps/` returns 0 hits. Booking skill uses different `cb:book:*` payloads (`CALLBACK_BOOK_PICK_MASTER_PREFIX` etc.) Wire-format incompatible with legacy → fine since neither side wired yet, but the **confirm/cancel/edit** flow for AI-suggested actions is not yet implemented. | MEDIUM — confirm-booking flow not wired end-to-end via AI |
| `legacy_maxbot/ai_action_service.py::execute_confirm_booking` | 376 | partial in `apps/skills/booking/tools.py` confirm path + `apps/bookings/services.py` | **PARTIAL** | Booking confirmation logic exists in apps but tied to mini-app + REST handoff to Ayla per ADR-0009. Bot-side single-flow execute_confirm_booking with idempotency cache and admin Telegram notification needs confirmation. The legacy file imports `notifications.send_notification_telegram` which is **out of scope** (notifications cluster). | MEDIUM — manual review needed for AI-driven booking confirmation path |
| `legacy_maxbot/handlers/ai_assistant.py` — on_free_text + run_ai_turn | 391 | `apps/orchestrator/pipeline.py::turn` + `apps/channels/max/*` ingress | **PARTIAL** | Pipeline replaces the handler-level orchestration. **Side branches missing in current code:** food_drink_hints inline card before AI; water log shortcut; tier_b health_signal trigger ahead of LLM; BotInquiry creation on giveup. Some are absorbed into skills, some genuinely gone. | MEDIUM — see specific rows below |
| `legacy_maxbot/handlers/ai_callbacks.py` — cb:ai:* callbacks | 461 | none | **NONE** | `grep "cb:ai:"` in `apps/` → 0 hits. AI-suggested action confirmation has no wire-format yet. | MEDIUM — see ai_ui.py row |
| `legacy_maxbot/handlers/fallback.py` | 35 | `apps/orchestrator/pipeline.py` `intent='unknown'` path | **FULL** | Pipeline routes unknown intents to canned reply / handoff. | LOW |
| `legacy_maxbot/handlers/faq.py` — curated HelpArticle list | 86 | not the AI path (`apps/skills/faq/` is RAG-based) | **NONE_BY_DESIGN** | The legacy `faq.py` is the *button-list curated FAQ* (HelpArticle records), distinct from the AI/RAG search. AI skill replaces dynamic RAG. The button-list itself is a Sigma/Tau UI concern — flag for out-of-scope review. | LOW |
| `legacy_maxbot/handlers/cross_domain.py` | 281 | `apps/skills/cross_domain/skill.py` (162 LOC) | **PARTIAL** | Skill exists; verify card render + convert/dismiss/seen callback handlers (`cb:cross:*`) and Ayla `/dismiss` API call are wired. NOT deep-read by this audit — INVESTIGATE. | LOW (already flagged in current pilot scope) |
| `legacy_maxbot/handlers/food_clarify.py` | 62 | `apps/skills/food_clarify/skill.py` + `apps/skills/food_clarify/hints.py` | **FULL** | `hints.py` is a faithful port (acknowledged in docstring as Sprint 9 P4 / DRF-821). | LOW |
| `legacy_maxbot/handlers/food_correction.py` | 361 | `apps/skills/food_correction/skill.py` (120 LOC) | **PARTIAL** | Big LOC delta (361→120). The portion-correction + retake + manual + daily summary callbacks need verification — INVESTIGATE. | MEDIUM — TIER-B nutrition feature; pilot-critical |
| `legacy_maxbot/handlers/food_scanner.py` | 554 | `apps/skills/food_scanner/skill.py` (283 LOC) | **PARTIAL** | Big LOC delta. Photo path + consent + meal-type buttons / `/дневник` text shortcut + evening_inline trigger need verification — INVESTIGATE. The wellness flagship for the pilot — high risk if gaps. | HIGH — pilot blocker |
| `legacy_maxbot/handlers/health_screening.py` | 533 | `apps/skills/health_screening/skill.py` (106 LOC) + `classifier.py` | **PARTIAL** | Big LOC delta (533→~150). Lazy FSM (consent → pregnancy → breastfeeding → diabetes → ED) is the legacy. Current skill has a pain classifier but TIER-B health-flag screening FSM is not visible. | HIGH — Tier-B feature legally meaningful (medical/safety) |
| `legacy_maxbot/ai_parsers.py` — hybrid regex+LLM parsers | 690 | partial in `apps/skills/water/parser.py` (regex only?) + `apps/skills/nutrition_anketa/fsm.py` (regex only) | **PARTIAL** | `grep parse_age\|parse_height\|parse_weight\|parse_allergies` → 4 hits ALL in `water` (parse_beverage analog) + 0 hits for anketa LLM-fallback. Current anketa FSM uses `validate_int_range` regex-only — legacy LLM fallback for «тридцать» / «1м75» / weight ranges is GONE. | MEDIUM — anketa completion rate drops for word-form inputs |
| `legacy_maxbot/food_drink_hints.py` | ~150 | `apps/skills/food_clarify/hints.py` | **FULL** | Faithful port per docstring. | LOW |
| `legacy_maxbot/tier_b_triggers.py` — health-signal regex | 44 | none | **NONE** | `grep detect_health_signal\|tier_b_triggers` in `apps/` → 0 hits. (`apps/skills/health_screening/classifier.py` is pain-specific, not pregnancy/diabetes/ED.) | MEDIUM — legally-sensitive (pregnant user not auto-routed to Tier-B screening) |
| `legacy_maxbot/personalization.py::get_client_history` | 138 | partial in `apps/skills/booking/tools.py` show_my_bookings | **PARTIAL** | `last_visits` and `bookings_count` are NOT visible being fed into the booking/faq system prompt. Legacy prompt warmed up returning-client tone via this. | MEDIUM — voice quality degrades for returning customers |
| `legacy_maxbot/personalization.py::greet_text` (segmented) | (incl.) | `apps/skills/welcome/skill.py` (600 LOC) | **PARTIAL** | Welcome skill is bigger — likely covers segmentation. Not deep-read. INVESTIGATE. | LOW |
| `legacy_maxbot/segmentation.py` — Phase 3 50/50 A/B | 65 | `apps/experiments/` (likely) | **INVESTIGATE** | Not deep-read. | LOW |
| `legacy_maxbot/voice_examples.py` | 147 | `apps/promptreg/voice_examples.py` (269 LOC) | **FULL** | Docstring of `apps/promptreg/voice_examples.py` explicitly says "ports curated dialogue pairs from `mysite/maxbot/voice_examples.py`" and "adds three new categories". Net superset. | LOW |
| `legacy_maxbot/mcp_client.py` — persistent stdio MCP wrapper | 143 | `apps/kb/chromadb_client.py` + `apps/kb/services/retriever.py` | **PARTIAL_BY_DESIGN** | ADR-0009 + the rewrite intentionally drop MCP-as-subprocess in favour of direct chromadb client + per-tenant retriever. **However:** the legacy `MaxbotMCPClient` also exposed any tool the MCP server published (e.g. `search_faq`). Current code only ports `search_knowledge_base`. If `formulatela_mcp` had tools beyond `search_faq` (e.g. `find_master_by_skill`, `service_info`), those are gone. | LOW — most tools have direct ORM equivalents in `apps/catalog/` |

## Gaps requiring action

Bucketed by severity per founder's «не всё перенесено» concern:

### PORT_NOW (pre-pilot)
1. **Phatic intent fast-path** — port `legacy_maxbot/intents.py` (81 LOC)
   as a pre-LLM regex shortcut inside `apps/orchestrator/pipeline.py`
   step 6 or as a step 5.5. Saves ~$ + ~3s on every "привет" / "спасибо".
   **Recommended action: PORT_NOW.**
2. **Master context in system prompt** — port `legacy_maxbot/ai_context.py`
   `MasterContext` + `build_master_context` into `apps/skills/booking/prompts.py`
   so the LLM sees the candidate `master_id=N` list with categorised
   services BEFORE it emits a tool_call. Current per-tool validator is
   defence-in-depth; the prompt-side list is the primary defence.
   **Recommended action: PORT_NOW. HIGH risk.**
3. **TIER-B health-signal trigger** — port `legacy_maxbot/tier_b_triggers.py`
   (44 LOC) — pregnancy/breastfeeding/diabetes/hypertension/ED regex —
   so the AI pipeline auto-routes a pregnant client to the screening
   flow instead of giving generic advice. **HIGH legal+safety risk if
   skipped.** Recommended action: PORT_NOW.
4. **ai_parsers LLM fallback for nutrition anketa** — port
   `parse_age/height/weight` LLM-fallback path (≤30 chars,
   tool_choice forced) into `apps/skills/nutrition_anketa/fsm.py`.
   Current FSM is regex-only and rejects "тридцать", "1м75", "65-75 кг".
   **Recommended action: PORT_NOW (food scanner is wellness flagship).**

### PORT_POST_PILOT (deferred deliberately or "nice to have")
5. **Response cache + warmup** — port `legacy_maxbot/response_cache.py` +
   `popular_questions.py` + `warmup.py`. ~50ms vs ~6 700ms for the top
   10 questions. **Action: PORT_POST_PILOT** unless pilot latency
   telemetry shows >3s p95 on hot questions.
6. **recommend_services tool** — port discovery-by-goal tool spec +
   handler. Legacy was 12 enum goals (relax/back_pain/recovery/...).
   Current booking skill jumps to show_masters too aggressively for
   vague queries. **Action: PORT_POST_PILOT** — booking covers MVP.
7. **ask_clarification fallback semantic** — when LLM hallucinates IDs,
   bounce to `ask_clarification` instead of `should_handoff=True`. Less
   abrupt UX. **Action: PORT_POST_PILOT.**
8. **silent-promise retry with tool_choice="required"** — adds ~1 LLM
   call when LLM emits "I'll find a master..." without tool_call.
   **Action: PORT_POST_PILOT** unless dev-bot logs show it firing.
9. **client_history block in system prompt** — feed bookings_count +
   last 3 visits into booking/faq prompts for returning-client tone.
   **Action: PORT_POST_PILOT** (voice quality, not function).

### INVESTIGATE_FURTHER (couldn't determine from this audit)
10. **handlers/food_scanner.py 554→283 LOC** — verify photo path,
    consent gate, meal-type buttons, /дневник shortcut, evening_inline
    trigger all present. Pilot blocker if gaps.
11. **handlers/health_screening.py 533→~150 LOC** — verify lazy FSM
    (consent → pregnancy → breastfeeding → diabetes → ED) ported.
    Legal/safety blocker if gaps.
12. **handlers/food_correction.py 361→120 LOC** — verify portion
    correction + retake + manual + daily summary callbacks.
13. **handlers/cross_domain.py 281→162 LOC** — verify
    convert/dismiss/seen callbacks + Ayla /dismiss API call.
14. **personalization.py::greet_text** segmented welcome — confirm
    `apps/skills/welcome/skill.py` covers is_new / returning_with_diary /
    silent_user branches.
15. **legacy_maxbot/ai_action_service.py** confirm-booking idempotency +
    admin Telegram notification — REST handoff to Ayla per ADR-0009
    handles the booking-create, but the bot-side idempotency cache +
    "AI conv ..." comment may have no equivalent.
16. **OPENAI_PROXY support in `apps/llm/providers/openai_provider.py`** —
    confirm the RU httpx-proxy pattern is ported; legacy `llm.py:68-77`
    is explicit about it.

### DELETE_DELIBERATE (gone on purpose per ADR-0009 / wellness pivot)
None within this audit. Even `mcp_client.py` is partial — keep until
all MCP-exposed tools (beyond `search_faq`) are confirmed redundant.

## Files safe to delete

**None — full confidence.** This is a coverage audit, not a deletion
authorisation. Per `legacy_maxbot/MIGRATION_NOTICE.md`, files are
tombstoned after migration with a "drained" table entry; the table is
currently empty (`_(none yet)_`). Until the table is populated AND a
maintainer signs off per file, default behaviour is **retain**.

If forced to nominate, lowest-risk-to-delete candidates (still requiring
explicit sign-off):

- `legacy_maxbot/voice_examples.py` — `apps/promptreg/voice_examples.py`
  explicitly supersedes (docstring says so + adds 3 new categories).
- `legacy_maxbot/ai_store.py` — Conversation/Message ORM matches in
  `apps/conversations/models.py`.
- `legacy_maxbot/food_drink_hints.py` — `apps/skills/food_clarify/hints.py`
  is a docstring-acknowledged port.

## Investigations needed

See **INVESTIGATE_FURTHER** items above (10–16). The audit was time-boxed
to ~30 file deep-reads; remaining handler-level files (`food_scanner`,
`food_correction`, `health_screening`, `cross_domain`) need a follow-up
diff-style review (legacy vs current side by side) to confirm coverage
of the +200–400 LOC delta in each.

**Recommended next step:** sequenced port of the 4 PORT_NOW items
(phatic intents, master context, tier_b_triggers, ai_parsers LLM
fallback) BEFORE proceeding to deletion of any legacy file. The
PORT_POST_PILOT items can be tracked as GitHub issues with severity
labels.

## Appendix: searches performed

Glob patterns:
- `legacy_maxbot/**/*.py`
- `apps/skills/*/`, `apps/orchestrator/*`, `apps/llm/*`,
  `apps/kb/services/*`, `apps/voice/*`, `apps/promptreg/*`,
  `apps/tools/*`, `apps/persona/*`, `apps/conversations/*`

Grep patterns (against `apps/` excluding `.claude/worktrees/`):
- `food_drink_hints|looks_like_food_drink` → port present
- `response_cache|popular_questions|warmup` → 0 hits
- `MCPClient|mcp_client|formulatela_mcp` → only in
  `apps/kb/management/commands/migrate_legacy_kb.py`
- `ai_parsers|parse_age_value|parse_allergies|REFUSED` → 0 hits
- `detect_health_signal|tier_b_triggers` → 0 hits
- `is_giveup|GIVEUP_MESSAGE|giveup` → 0 hits
- `intent_router|detect_intent|greeting|small_talk` → router-tool
  matches only; no phatic regex
- `build_master_context|MasterContext|specialist_context` → 0 hits
- `FORMULA_TELA_EXAMPLES|DIAGNOSTIC_FIRST_PAIN` → 1 hit in
  `apps/promptreg/voice_examples.py` (the port)
- `RAG_MIN_SCORE|chat_rag|search_faq` → 0 hits
- `RECOMMEND_SERVICES|recommend_services` → 0 hits
- `ASK_CLARIFICATION|ask_clarification` → 1 hit (docstring only)
- `_PROMISE_STEMS|promise_without_tool|looks_like_promise` → 0 hits
- `enrich_show_masters|enrich_show_slots|enrich_show_my_bookings|ai_yclients`
  → 0 hits
- `retry.*tool_choice|tool_choice.*required|silent.failure|promise.*tool`
  → 0 hits (in skills/orchestrator code)
- `parse_age|parse_height|parse_weight|parse_allergies|parse_beverage`
  → 4 hits, all in `apps/skills/water/` (parse_beverage analog only)
- `phatic|small.talk|GREETING_RE|THANKS_RE` → 0 hits

Reads (deep-read or sampled first ~50–200 lines):
- legacy: `llm.py`, `ai_concierge.py`, `ai_prompts.py`, `ai_tools.py`,
  `ai_context.py`, `ai_tool_handlers.py`, `ai_action_service.py`,
  `ai_yclients.py`, `ai_ui.py`, `ai_store.py`, `ai_parsers.py`,
  `intents.py`, `response_cache.py`, `mcp_client.py`,
  `personalization.py`, `segmentation.py`, `voice_examples.py`,
  `tier_b_triggers.py`, `popular_questions.py`, `food_drink_hints.py`,
  `handlers/{ai_assistant, ai_callbacks, cross_domain, fallback, faq,
  food_clarify, food_correction, food_scanner, health_screening}.py`,
  `MIGRATION_NOTICE.md`, `README.md`.
- current: `apps/llm/{router, protocol, providers/}`,
  `apps/orchestrator/{pipeline, intent_router, composer, tool_invoker,
  memory/coordinator, safety/pre_check}.py`,
  `apps/skills/{booking, faq, food_clarify, food_scanner,
  food_correction, health_screening, cross_domain, nutrition_anketa,
  welcome}/`, `apps/kb/services/retriever.py`,
  `apps/voice/rewriter.py`, `apps/promptreg/voice_examples.py`,
  `apps/conversations/models.py`.
