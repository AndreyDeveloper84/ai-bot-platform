# MEASUREMENT: DecisionReadiness / Question Resolution — Current Runtime

**Трек:** G2-DR — DecisionReadiness / Question Resolution — CURRENT RUNTIME MEASUREMENT
**Дата замера:** 2026-09-09
**Режим:** MEASURE-FIRST. Ничего не исправлено, не спроектировано, не изменено. Все выводы — code-path proof (live execution не выполнялся; отсутствие runtime probe помечено).

---

## 1. Measurement bases

| Repo | Canonical branch (фактическая) | Измеренный checkout | SHA | Divergence |
|---|---|---|---|---|
| ai-bot-platform | `dev` (origin/dev = `83ed56a`, local dev = `91ec947`; origin/HEAD не установлен) | `feat/recommendation-boundary-client` | `fd6f4e87acde54aebc0cc7c50d55bff49fab82dc` | 3 ahead / **43 behind origin/dev** |
| djangoproject-catalog | `dev` (= origin/dev) | `dev` | `95c917e684652476feef3ae9d790fb2c8d277378` | 0 (точно canonical) |
| ayla-ai-core | `main` (origin/main = `d72a5de`) | `fix/memory-origin-vocabulary` | `73b0422b01e7e684491b7d2fe83e15e3b65fc836` | 2 ahead / 1 behind; **код идентичен main** (ветка docs-only) |
| ayla-knowledge | `main` (origin/main = `207eeb6`) | `drf-1148/canon-under-version-control` | `9e166179e9634b36498010cfad50e9e402c61729` | не критично: искомых спек в repo нет ни на одной ветке |

**Важные оговорки базы:**
1. Runtime ai-bot-platform измерен на feature-ветке, отстающей от `origin/dev` на 43 коммита. Ни одно из найденных отсутствий (DecisionReadiness, question_id, ledger) не может «появиться» от этих 43 коммитов без отдельной проверки — см. §23 (UNKNOWN).
2. Canon-спеки (DRE v1.0, Goal Candidate, Resolver, Safety FREEZE) физически лежат в **ai-bot-platform** `docs/specs/` + `docs/decisions/` на `origin/dev`, а НЕ в ayla-knowledge. Корневой канон `docs/ayla-conversation-state-v1.1-reconciled.md` отсутствует в mainline вообще — существует только на ветке `docs/rescue-knowledge` (worktree). SAFETY_SIGNAL_MATRIX §1.6 прямо фиксирует: «файл канона в репозитории отсутствует».
3. В текущем checkout ai-bot-platform спека `DECISION_READINESS_ENGINE_v1.0.md` присутствует только внутри `.claude/worktrees/*` (копии), в основном дереве `docs/specs/` её нет.

---

## 2. Executive verdict

1. **Есть ли DecisionReadiness runtime?** **НЕТ.** Ни одно из 5 канонических состояний не встречается в коде ни одного runtime repo (0 совпадений вне worktree-копий спеки). `question_ledger`, `allow_recommend`, `readiness_state` отсутствуют.
2. **Кто authority?** Де-факто — **LLM tool-choice**: промпт консьержа (`apps/orchestrator/concierge.py:1163`, ai-bot-platform) и системный промпт AI-чата (`ai/prompts.py:30`, djangoproject-catalog). Плюс разбросанные точечные детерминированные гейты.
3. **Deterministic ли он?** **НЕТ** на основном пути (LLM_ONLY — прямое нарушение канона Z1–Z5 / DRE §6). Детерминированные островки существуют, но локальны и не образуют readiness.
4. **Все 5 canonical states?** **Ни одного.** Ближайший аналог — 4 статуса `intent_resolution.py:112` (`resolved/needs_clarification/unresolved/blocked_safety`), но это post-reply telemetry, не влияющая на ход.
5. **Stable question_id?** **НЕТ.** Только quasi-id: FSM step name (анкета), field name (memory_ask), UUID token (PendingBookingAction, 10 мин). Для LLM-вопросов (`ask_clarification`) id отсутствует полностью.
6. **Asked/resolved ledger?** **НЕТ единого.** 5+ независимых механизмов (refusal memo 30 мин, screening memo 30 мин, time_pref 10 мин, booking_flow 10 мин, clarify probe по последним 12 сообщениям, GoalAnketaAnswer в catalog). TTL 2h inactivity из канона не реализован нигде.
7. **Может ли resolved question повториться без controlled reason?** **ДА.** LLM re-ask неконтролируем (нет duplicate detection); screening/refusal memo протухают за 30 мин и «забывают» resolved факты (задокументированный trade-off, `refusal_memo.py:50-54`).
8. **Может ли assistant output стать USER evidence?** В durable user-memory — **НЕТ** (единственный call-site extraction на тексте текущего user event, `handler.py:2453`; callers подтверждены полным grep). В LLM-контексте — **ДА** (assistant реплики в истории следующего хода, `concierge.py:576-589`; tool-результаты подаются как role=user, `concierge.py:1318-1405`). Отдельно: `log_water`/`clarify_food_entry` исполняются на модельной нормализации и пишут в Ayla-дневник — MODEL_INFERENCE → domain data **by design** (`nutrition_global.py:59-66`).
9. **Есть ли второй readiness authority?** **Да, несколько**: booking skill (Phase-1 LLM + свои missing-context отказы), health_screening classifier+memo, per-tenant intent_router, legacy weighted recommendation engine (djangoproject-catalog, долг T9 под сторожем), personalization engine. Ничто не читает центральный readiness — его нет.
10. **Исправлен ли nutrition question loop?** **ЧАСТИЧНО.** DRF-1542 закрыл health_screening: вето по словам человека (`nutrition_global.py:292-330`), memo «вопросы заданы» (30 мин), RED_FLAG неприкосновенен, регрессионные тесты на боевом 5-ходовом диалоге. Остаток: нет stable question_id; ответы на screening-вопросы кодом не хранятся (LLM_ONLY через историю); повтор инициирует LLM, гасит код.
11. **Семантически одинаковы quick reply и free text?** **ЧАСТИЧНО.** Контракт «tap == typed answer» реализован подстановкой фразы на входе (и стирает provenance), но: anketa choice-шаги отклоняют текстовую метку («Женский» не проходит `validate_choice`, принимает только slug от тапа); clarify toggle/«Ни один вариант» не имеют текстового эквивалента; «Не знаю» в memory_ask diet сохраняется как `"other"` — прямое нарушение канона.
12. **Safety имеет правильный приоритет?** **ЧАСТИЧНО.** В recommendation resolver (djangoproject-catalog) fail-closed + NOT_APPLICABLE-by-semantics исполнены и огорожены NEGATIVE_GUARD. Но: настоящего SafetyResult producer'а нет (бот присылает enum сам); Safety CLARIFY → приоритетный вопрос — MISSING; booking-путь (`confirm_booking`/`ActionService`) не читает `requires_health_check` вообще.

### Counts (по классифицированным находкам всех треков)

| Class | Count |
|---|---|
| EXISTS | 21 |
| PARTIAL | 16 |
| MISSING | 19 |
| CONTRADICTS_CANON | 8 |
| STALE_SPEC | 5 |
| LLM_ONLY | 6 |
| PROMPT_ONLY | 5 |
| UNKNOWN_NOT_MEASURED | 4 |

(Counts — по дискретным находкам в отчётах треков; одна находка может нести две метки, тогда учтена в обеих.)

---

## 3. Current readiness architecture

Единого readiness engine не существует. Фактическая карта «действовать или спросить»:

**ai-bot-platform (MAX global, основной диалоговый путь):**
- LLM tool-choice консьержа: `apps/orchestrator/concierge.py:1100-1263` `build_concierge_system_prompt()` → `generate_concierge_reply` (`:1518`) ← `turn_seam.py:179` ← `apps/channels/max/handler.py`. Инструменты вкл. `ask_clarification`, `start_booking`, `health_screening`. AUTHORITY: LLM prompt. REACHABLE. **LLM_ONLY.**
- Точечные детерминированные гейты:
  - `discovery.py:2470 has_discovery_criteria` + `:2481 render_no_criteria_clarification` — «ноль критериев поиска». EXISTS (PARTIAL).
  - `discovery.py:2506-2601 clarifying_question` (DRF-1531) — один различающий вопрос из каталога; гейт `DISCOVERY_CLARIFY_MIN_TIER` default 0 → **фактически отключён**. EXISTS (PARTIAL, flag-gated).
  - `refusal_memo.py:104-198` + `concierge.py:1468 _repeats_a_refusal` — short-circuit повтора отказанного (service, city), TTL 1800s. EXISTS.
  - `apps/channels/max/handler.py:680-727 _confidence_floor_reason` (DRF-1209) — LLM self-confidence < порог → handoff; default OFF. **CONTRADICTS_CANON** по дизайну (канон: readiness ≠ LLM confidence).
- `intent_router.py:47-463 IntentDecision` (per-tenant): LLM-классификатор, confidence — LLM-оценка. LLM_ONLY.
- `intent_resolution.py:112` — RESOLUTION_STATUSES {resolved, needs_clarification, unresolved, blocked_safety}: post-reply telemetry, никогда не меняет ход («After the reply, never before it»). UNREACHABLE-as-authority.
- `pipeline.py:1-25` — 19-шаговый pipeline: DEPRECATED, caller только replay. DEAD_CODE/STALE_SPEC.
- `legacy_maxbot/*` — не импортируется runtime. DEAD_CODE.

**djangoproject-catalog:**
- Recommendation resolver S0–S6 (`recommendation/_pipeline.py`, `_stages.py`) — детерминированный, StagePolicy CONTROLLED_POLICY, LLM не участвует; `StageVerdict DISTINGUISHED/TIED/INACTIVE`. Это дискриминация кандидатов, не readiness действия; выхода `next_question` нет. EXISTS (PARTIAL vs canon). Callers: `recommendation/views.py:78` (internal HTTP), `users/catalog_recommendations_api.py:229` (home screen).
- Goal-slot readiness: `goals/decision_context.py:141 _goal_is_resolved` — детерминированный, узкий. EXISTS.
- Booking readiness в AI-чате: правило 5 промпта `ai/prompts.py:30` — решает модель. **PROMPT_ONLY/LLM_ONLY, CONTRADICTS_CANON.**
- Legacy weighted engine `ai/application/services/recommendation_engine.py:57-69` — запрещённая контрактом единая взвешенная сумма; живой, reachable через `ai/concierge_factory.py:109-137`; долг T9 под сторожем `test_boundary_guards.py:63`. **CONTRADICTS_CANON (признано).**
- Anti-spam вопросный гейт: `users/personalization_engine.py:106 should_ask_question` — 8 детерминированных правил. EXISTS.

**ayla-ai-core:** readiness отсутствует полностью (MISSING). `ActionType` (tools.py:243-262) — wire-константы UI-действий, не state model. Решение — LLM tool choice. Детерминированны только anti-hallucination fallback на clarification (`tool_handlers.py:154-166`) и memory provenance-рендер.

**A1–A7:** A1 — нет; A2 — concierge LLM + точечные гейты; A3 — центрального owner-модуля нет; A4 — основной путь LLM; A5 — ни одного; A6 — фактическая модель: `Conversation.state ∈ {idle, consulting, escalated, human_handoff}` (Postgres, ADR-0007) + разрозненный `skill_state` JSON + `PendingBookingAction` + Message.action_data probes; A7 — несколько параллельных authorities (см. §4).

---

## 4. Authority map

| Решение | Фактический authority | Repo / file:line | Class |
|---|---|---|---|
| Действовать или спросить (global) | LLM prompt | ai-bot-platform `concierge.py:1163` | LLM_ONLY |
| Действовать или спросить (per-tenant chat) | LLM prompt | djangoproject-catalog `ai/prompts.py:30` | PROMPT_ONLY |
| Discovery clarify (один вопрос) | Код (каталог) | ai-bot-platform `discovery.py:2506`, `marketplace/discovery.py:1679` | EXISTS, flag-gated off |
| Booking missing context | Booking skill (код+LLM) | ai-bot-platform `skills/booking/skill.py:445+,975` | EXISTS (PARTIAL) — parallel authority |
| Health screening ask | Regex classifier + memo | ai-bot-platform `skills/health_screening/` | EXISTS — parallel authority |
| Ranking кандидатов (resolver) | Детерминированный pipeline | djangoproject-catalog `recommendation/_pipeline.py` | EXISTS |
| Ranking кандидатов (chat context) | Legacy weighted engine | djangoproject-catalog `recommendation_engine.py:57` | CONTRADICTS_CANON, parallel |
| Спрашивать ли профильное поле | Personalization engine (код) | djangoproject-catalog `personalization_engine.py:106` | EXISTS |
| Ask-eligibility memory_ask | Внешняя policy в Ayla | ai-bot-platform `memory_ask.py:125-131` | EXISTS (external) |
| Confidence handoff | LLM self-confidence | ai-bot-platform `handler.py:680` | CONTRADICTS_CANON, default OFF |

Вывод: **PARALLEL_AUTHORITY** — 5+ независимых контуров, центральный отсутствует.

---

## 5. Question sources (Track B)

| Источник | WHO DECIDES | STABLE ID | ANSWER STORAGE | RESOLUTION | RE-ASK POLICY | Class |
|---|---|---|---|---|---|---|
| Nutrition anketa (5 шагов, wizard) `skills/nutrition_anketa/fsm.py:44-73` | Код, позиция в wizard | Step name (UI-id) | `skill_state["nutrition_anketa"]` → Ayla `upsert_profile` | Валидатор шага | Неограничен при невалидном вводе | EXISTS; CONTRADICTS_CANON (текст-метка отклоняется) |
| Memory-ask (1 вопрос) `orchestrator/memory_ask.py` | Внешняя policy (Ayla eligibility) | Field name | Redis pending 24h → PATCH personal-context `source: conversational` | Regex-парсеры per field | Внешний cooldown 24h | EXISTS (external policy) |
| Discovery `ask_clarification` | **LLM генерирует вопрос и опции** | **НЕТ** | Ответ = обычное user-сообщение, без привязки | Тап=текст, re-resolve | Отсутствует | LLM_ONLY; CONTRADICTS_CANON |
| Discovery no-criteria | Код, фиксированный текст | НЕТ | — | — | — | EXISTS |
| Booking pickers (`_MASTER_PICK_PROMPT` и др.) `skills/booking/skill.py:220-267` | Код + LLM | Бизнес-id в callback | `skill_state["booking_flow"]` TTL 600s | Revalidation при тапе | По flow state | EXISTS (PARTIAL) |
| Welcome/consent `skills/welcome/skill.py` | Код | Callback slug | DB consent stamp | Тап | — | EXISTS (PARTIAL) |
| Food clarify/correction | Regex-детектор / код | Quasi-id (field+scan_id) | skill_state TTL | Answer shape | — | PARTIAL |
| Health screening `skills/health_screening/skill.py:61-65` | Regex classifier | НЕТ (одна константа) | Ответ НЕ хранится | MISSING | Memo 30 мин гасит SOFT | PARTIAL/MISSING |
| Goal anketa (catalog) `goals/anketa.py:65-88` | Сервер, hardcoded порядок | **step_key slug — EXISTS** | `GoalAnketaAnswer` durable | answered_keys не переспрашиваются | Проход повторяем бесконечно | EXISTS |
| Профильные 8 полей (catalog) `users/internal_personal_context_api.py:56-65` | Server engine | Field name | `UserPersonalContext` + `last_asked_at`/`skipped_questions` | Наличие значения | 24h cooldown, 2×skip→30d | EXISTS |
| Chat `ask_clarification` (catalog) `ai/tools.py:142-164` | **LLM** | **НЕТ** | user-сообщение без привязки | НЕТ | НЕТ | LLM_ONLY |
| Safety clarification | — | — | — | — | — | **MISSING в обоих runtime repo** |
| Mini App forms | Ayla API (вне замера) | UNKNOWN | — | — | — | UNKNOWN_NOT_MEASURED |

Вопросы «по позиции в wizard/анкете»: nutrition anketa и goal anketa — оба последовательный перебор полей; goal anketa не переспрашивает отвеченные (EXISTS), nutrition anketa не проверяет «а знаем ли уже» (PARTIAL).

---

## 6. Stable question IDs (Track C)

- `question_id` в production-коде: только `callback_query_id` канала (сброс спиннера). Семантического реестра вопросов нет. **MISSING.**
- Quasi-id: FSM step (стабилен, переживает рестарт); memory_ask field (24h Redis); PendingBookingAction UUID (10 мин, single-use CAS).
- **C1:** Нет. **C2:** Только quasi-id у FSM/anketa. **C3:** Нельзя для LLM-вопросов; можно для анкет. **C4:** Только анкеты (answered_keys / наличие значения). **C5:** **Да** — LLM может переформулировать тот же вопрос, duplicate detection отсутствует (единственный анти-повторный механизм — refusal_memo, и он про отказы каталога, не про вопросы). CONTRADICTS_CANON (DRE §13.2 требует `question_id = b16(sha256(semantics))`, «от семантики, не от текста»).

---

## 7. Asked/resolved ledger (Track D)

Единого ledger нет. Независимые хранилища:

| Механизм | Хранилище | TTL | Забывает resolved facts? |
|---|---|---|---|
| refusal_memo | `skill_state["no_match"]` | 1800s | **Да** (осознанно, `refusal_memo.py:50-54`) |
| screening memo | `skill_state["health_screening_asked"]` | 1800s | **Да** (только факт «спрашивала», не какой вопрос) |
| time_pref | `skill_state["time_pref"]` | 600s | да |
| booking_flow | `skill_state["booking_flow"]` | 600s | flow, не факты |
| PendingBookingAction | Postgres, `expires_at` | 10 мин | n/a (transaction) |
| clarify offer | probe последних 12 assistant messages | нет (depth-based) | «stale» = глубина истории |
| Skill FSM | `skill_state[skill_name]` | нет | нет |
| GoalAnketaAnswer | Postgres durable | нет | нет |
| Short-term memory | Redis | 24h (default) | да |
| `_health_already_declined` (медсогласие) | Message table probe | нет (осознанно, `handler.py:972-975`) | нет |

**Canon TTL 2h inactivity: не реализован нигде.** `resolve_active_conversation` (`conversations/services.py:45-116`) возвращает открытый Conversation независимо от возраста. В djangoproject-catalog TTL сессий отсутствует; reset только через 152-ФЗ soft-delete. **CONTRADICTS_CANON (канон §4.1) + канон §4.1 нарушается и в обратную сторону: TTL 30 мин забывает resolved semantic facts, что канон прямо запрещает.**

DRE §13.4 требует ledger в `Conversation.skill_state["decision_readiness"]` с fail-closed записью — не реализовано.

---

## 8. Evidence provenance (Track E)

Детерминированный write-path (ai-bot-platform, MAX global):
- `handler.py:2453 record_explicit_green_facts(bot_user, event.text)` → `personal_context.py:43` (gate order: consent → extraction → identity → erasure-tombstone → dedup → Ayla bridge) → `MemoryEntry(source=EXPLICIT)`. Extraction — чистые regex, без LLM (`persona/memory_extract.py:413`). **EXISTS, USER_EXPLICIT only.**
- Полный grep callers `extract_user_facts`: один production call-site. Extraction на истории/assistant-строках не вызывается нигде. **EXISTS (доказанное отсутствие contamination в durable memory).**
- Telegram surface: write-path отсутствует. PARTIAL.

Классификация источников:
- USER_EXPLICIT: extraction write-path; memory_ask answers (`source: conversational`); goal anketa answers.
- USER_CLICK: DRF-990 резолверы тапов (человеческая фраза в историю или None).
- AUTHORITATIVE_DOMAIN: anketa → Ayla `upsert_profile`; catalog facts.
- CONFIRMED_MEMORY: memory_block read-side с per-field origin (`memory_block.py:110-245`), declared=1.0/inferred=0.6.
- GOAL: `NeedOrigin=GOAL` в resolver (`_types.py:87-93`).
- ASSISTANT_OUTPUT: намеренно включён в `_conversation_text` grounding-блоб для имён салонов (`concierge.py:953-977` → `salon_named_in`) — evidence-of-said, не user-fact.
- MODEL_INFERENCE: `log_water`/`clarify_food_entry` исполняются на модельной нормализации → Ayla diary (by design, пиннed тестами).
- В djangoproject-catalog: `EvidenceOrigin = DOMAIN_FACT/USER_EXPLICIT/USER_CLICK/CURATED_KNOWLEDGE` — **MODEL_INFERENCE структурно отсутствует** (`recommendation/_evidence.py:42-53`). EXISTS, canon-aligned. Но `_SOURCE_CHOICES` допускает `conversational` и unstamped→`explicit` по умолчанию (`internal_personal_context_api.py:90-109`) — PARTIAL.
- ayla-ai-core: бинарная модель stated/explicit vs derived в memory-рендере (`memory.py:79-114`), fail-closed для незнакомых. Трёхклассовой USER/ASSISTANT/MODEL классификации нет. PARTIAL.

---

## 9. Assistant-output contamination

**Durable user memory: contamination невозможна по коду** (один call-site, per-turn user text, grep-подтверждено). Но гарантия держится на топологии call-site, **теста-сторожа нет** (MISSING NEGATIVE_GUARD: «подать assistant/history текст в write-path → 0 writes»). Regression-риск: новый caller ничего не сломает.

**LLM-контекст: assistant output перечитывается каждый ход** (`concierge.py:576-589`, обе роли, 10 строк). Tool-результаты подаются как role=user (`concierge.py:1318-1405`, сознательно, provider-агностичность, не персистится). PROMPT_ONLY-риск: модель видит свои слова и может счесть их словами клиента внутри своего рассуждения — детерминированной защиты на этом уровне нет; есть outbound-guard до записи assistant-строки (`concierge.py:1579-1594`) и hint-маркировка «(вывод, клиент этого не говорил)» в catalog (`ai/personal_context_hint.py:66,99-103`).

**intent_resolution** имеет code-enforced verbatim-доказательство: `fragment` обязан быть точной подстрокой сообщения КЛИЕНТА (`intent_resolution.py:210-213,456-468`) — но consumer только телеметрия.

---

## 10. Nutrition regression measurement (Track F)

Метод: code-path proof (live execution недоступен).

**Health screening (DRF-1542 — фикс присутствует в измеренном checkout):**
- Вето по словам ЧЕЛОВЕКА: `nutrition_global.py:292-330` — `health_screening` исполняется на `message_text` (обязательный аргумент), не на `symptom_text` модели. Caller: `concierge.py:2020-2030`. EXISTS.
- Помнит ли вопрос: PARTIAL — boolean-флаг «спрашивала» с TTL 30 мин; **какой вопрос — не помнит** (stable question_id отсутствует).
- Помнит ли ответ: **MISSING (code) / LLM_ONLY** — ответы классифицируются NONE, ничего не пишется; живут только в LLM-истории.
- Повторяется ли: триггер — LLM каждый ход; гасит код (classifier + memo в `matches()`, RED_FLAG всегда проходит — решение §35 п.5).
- Contamination ответа: детерминированных записей ответа нет → контаминировать нечего; LLM-уровень — модель видит свои реплики (PROMPT_ONLY).
- Тесты: `test_screening_loop_1542.py` (CONTRACT+NEGATIVE_GUARD+REGRESSION на боевом 5-ходовом диалоге), `test_screening_memo_1542.py` (UNIT+ORDERING).

**Nutrition anketa:** здоровый loop изначально — stable step ids, FSM владеет следующим ходом, answers → Ayla upsert_profile (AUTHORITATIVE_DOMAIN). Изъяны: текстовая метка на choice-шаге отклоняется (G); stale-tap guard отсутствует (H).

**Вердикт F:** loop повторного health_screening **исправлен на уровне детерминированного гашения** (memo+classifier), но **не на уровне канона**: нет stable question_id, нет asked/resolved ledger, ответы не evidence, повтор решает LLM.

---

## 11. Free text / quick reply normalization (Track G)

- Канал сводит всё к `CanonicalEvent.text`: тап = `callback_data` или подставленная фраза (`telegram/parser.py:185-202`; MAX аналогично). Отдельного tap-события нет.
- Подстановка тап→фраза до записи в историю (`quick_actions.py:355-411 resolve_tap_text` и др.) → downstream не отличает тап от текста; **provenance click-vs-text стирается намеренно** и нигде не хранится (кроме log-строк booking gate). PARTIAL, CONTRADICTS_CANON (canon: USER_CLICK — отдельный origin).
- Расхождение семантики: anketa choice-шаги — тап несёт slug `female`, текст «Женский» отклоняется (`skills/fsm.py:261-266`). **CONTRADICTS_CANON.**
- Лучший пример канона: booking confirm — тап и текст «да» сходятся в одних handlers (`bookings/callbacks.py:634-671`). EXISTS.
- Роли SemanticOption (CHOICE/DELEGATE/ESCAPE/CONFIRM/ACTION/REACTION/CONSTRAINT_RESOLUTION): **MISSING** везде (grep пуст во всех repo). Ближайшее — `clarification.mode` (confirm_one/choose_many/free) — модальность, не роли.
- Hardcoded special cases: `_SKIP_MARKERS` (memory_ask.py:57-68); `_CONFIRM_VOCAB`/`_CANCEL_VOCAB` (pending_actions.py:98-131); `CLARIFY_NONE` «Ни один вариант» (discovery.py:2212); `STALE_TAP_TEXT` (quick_actions.py:306); «Не сейчас» consent_refuse; diet `"other"` fallback.
- **«Не знаю» ≠ «Другое» нигде не проводится; в memory_ask diet_type «не знаю» сохраняется как `"other"` (`memory_ask.py:275-280`) — CONTRADICTS_CANON (canon §13.2).** «Начать с нуля»/«Хочу другое» — handlers не найдены.

---

## 12. Delegation

Controlled delegation LOW/MEDIUM/HIGH: **MISSING во всех repo.** Ближайшее:
- intent `need_guidance` (catalog `decision_context.py:77,84`) — явный пользовательский выбор, не системная делегация.
- Промптная эскалация менеджеру (ayla-ai-core `prompts.py:139-140,150-151`) — PROMPT_ONLY.
- LLM волен импровизировать на «не знаю — выбери сама» — LLM_ONLY.
- Коды `DELEG_` зарезервированы за «треком A» и намеренно не завезены (`recommendation/_reason_codes.py:7-10`).

Канон (DRE §10, §10.2; canon §7.2/§8/§18): delegation поднимается только явным действием человека; DELEGATION CEILING — не bypass safety/required context. Runtime-аналога нет — это отсутствие слоя, не нарушение.

---

## 13. Safety seam

**djangoproject-catalog (resolver) — сильная сторона:**
- `SafetyState UNKNOWN → fail-closed как STOP` (`_stages.py:240`); `NOT_APPLICABLE` — по типу поверхности, отменяется содержимым через `_is_safety_sensitive` (`_stages.py:241-253,348-367`): `requires_health_check` ИЛИ персонализация → fail-closed. EXISTS + NEGATIVE_GUARD (AST-сторож, `test_safety_not_applicable.py:172-203`).
- `safety_state` на проводе обязателен, без default (`_serializers.py:95-104`; тест «missing safety_state is refused, not silently emptied»).
- **Но:** SafetyResult producer'а нет — бот присылает enum сам; CLARIFY/CAUTION не превращаются в вопрос («решает трек A», `_stages.py:323-329`); **safety CLARIFY-приоритет над обычными вопросами — MISSING** (DRE §13.6 порядок SAFETY_CLARIFICATION > REQUIRED_CONTEXT > DISCRIMINATION не реализован).
- **Booking/chat seam MISSING:** `confirm_booking`/`ActionService` не читают `requires_health_check` — услуга с противопоказаниями бронируется без health check. **P0.**
- Доменные аналоги: wellness GateDecision fail-closed (`wellness/services.py:70-103`, NEGATIVE_GUARD), nutrition override-лестница, admission weight-loss, cross-domain линтер. EXISTS.
- STALE_SPEC: `_stages.py:91-93` утверждает «поле безопасности убрано из схемы» — фактически обязательно.

**ai-bot-platform:** safety gate (`orchestrator/safety/gate.py`) — canned replies, уточняющих вопросов не задаёт; health_screening — ближайший аналог safety clarification (см. §10).

**ayla-ai-core:** safety отсутствует полностью (0 попаданий). MISSING.

---

## 14. Goal seam

- **Scalar, не active_goals[]**: одна активная цель (partial unique `goals/models.py:85-89`); смена закрывает прежнюю. Соответствует GOAL_CANDIDATE_CONTRACT §4.1 («скаляр по конструкции»; канон §12.4 multiple ACTIVE — семантический потолок, пилот — одна; контракт это разрешает).
- Readiness/questions читают Goal: `build_decision_context` читает активную цель (`decision_context.py:195-199`); home screen: сохранённая цель → `need.goal_key` (`catalog_recommendations_api.py:492`); resolver source разворачивает через `goal_category_ids_for_key`. EXISTS.
- Goal как **required** context: **НЕТ** — отсутствие цели не блокирует выдачу (`wiring.py:12-31`). Не противоречие: канон не требует goal как required.
- Goal не заменяет current explicit intent: текст в запросе → USER_EXPLICIT, иначе цель → GOAL (`catalog_recommendations_api.py:494-498`). Бинарно грубо, но приоритет explicit над goal соблюдён. PARTIAL.
- Hardcoded goal questions: goal anketa — единственный серверный вопросный контур. EXISTS.
- ayla-ai-core: слова `goal` нет вовсе. Boundary: goal layer живёт в catalog; в ai-bot-platform goal flow — через Ayla API (miniapp). Классификация boundary: PARALLEL_AUTHORITY не обнаружено; расхождение «канон multiple vs пилот single» — разрешено контрактом (не TRUE_CONTRADICTION).

---

## 15. Stale callbacks (Track H)

Общего механизма (decision reference / state revision / option ownership) **нет**. PARTIAL, точечно сильная:
- **Эталон:** PendingBookingAction — TTL 10 мин + CAS consume + ownership до CAS (`pending_actions.py:234-286`, `callbacks.py:696-768`). BookingReminder CAS + «уже обработана». Slot revalidation при тапе.
- Catalog chips: fresh re-read по id, `render_stale_card()`. EXISTS.
- Quick actions: retired slug → `STALE_TAP_TEXT`. EXISTS (только `cb:qa:*`).
- **Дефекты (документируются, не исправляются):**
  1. Clarify multi-select: тап по СТАРОЙ клавиатуре резолвится против НОВЕЙШЕГО offer (`handler.py:313 _last_clarification_offer`, вызов `:1299`) → silent misattribution. CONTRADICTS_CANON.
  2. Anketa: `cb:anketa:choice:{step}:{value}` не сверяется с текущим `fsm.current_step` — `{step}`-сегмент выбрасывается (`skill.py:297`); stale tap падает в чужой валидатор. MISSING guard.
  3. Канальная схема callback→text лишает тап связи с сообщением-источником.
- Catalog: anketa echo-check — 409 `ANKETA_STEP_MISMATCH` (`goals/api.py:304-312`). EXISTS. Chat `/action/`: MISSING (протухшая карточка confirm_booking исполнится; защита — только idempotency key).

---

## 16. Surface consistency (Track I)

Surfaces: **MAX global** (concierge+nutrition+memory), **MAX per-tenant** (skill registry), **Telegram** (per-tenant only, без concierge/memory extraction), **Mini App / mobile API** (REST; question authority в Ayla backend), **backend internal API** (djangoproject-catalog).

- Раздельных readiness authorities по surfaces не найдено — потому что центрального readiness нет вообще. Расхождение возможно по построению: global path решает concierge LLM, per-tenant — intent_router + skills, mobile chat — catalog LLM. Один semantic context может получить «READY» на одной surface и «clarify» на другой — **детерминированной гарантии консистентности нет** (MISSING by absence).
- Safety-authority различается: home screen объявляет NOT_APPLICABLE константой поверхности (`catalog_recommendations_api.py:472-476`), бот обязан прислать safety_state явно. EXISTS (зафиксировано контрактом).
- Резолверу читать `surface` стадиями запрещено (§9.5, гард `test_boundary_guards.py:344`). EXISTS.

---

## 17. Test reality (Track J)

| Область | Покрытие | Class |
|---|---|---|
| Clarification mode контракт | `test_clarification_mode.py` (381 стр., wire-shape byte-identity) | UNIT+CONTRACT |
| DRF-1531 clarifying question гейты | `test_clarifying_question_drf1531.py`, `test_clarify_material_drf1531.py` | UNIT |
| Intent resolution contract | `test_intent_resolution.py` (34) + tool_choice (18) | UNIT+CONTRACT |
| Refusal repeat (боевой транскрипт) | `test_concierge_refusal_memo.py` | NEGATIVE_GUARD/regression |
| Screening loop DRF-1542 | `test_screening_loop_1542.py` | CONTRACT+NEGATIVE_GUARD+REGRESSION |
| Screening memo ordering | `test_screening_memo_1542.py` | UNIT |
| Memory extraction гарды | `test_memory_extract.py` (allergy drop, no-fabrication) | UNIT |
| Write-path gates | `test_personal_context_write.py`, `test_memory_erasure_matrix.py`, `test_memory_bridge.py` | CONTRACT |
| Handler live-path | `test_handler_clarification_multiselect.py`, `test_handler_confidence_floor.py` | integration |
| Golden replay | `apps/replay/fixtures/golden/**` (через DEPRECATED pipeline!) | GOLDEN (stale pipeline) |
| Resolver pipeline/wire/safety | catalog `test_pipeline.py`, `test_wire_contract.py`, `test_safety_not_applicable.py` (AST guard), `test_boundary_guards.py` | UNIT+CONTRACT+NEGATIVE_GUARD |
| Goal anketa (stale answer, re-pass) | `test_goal_anketa.py` | UNIT+CONTRACT |
| Service match | `test_service_match_matrix.py` | GOLDEN |
| Personalization cooldown/skip | `test_personalization_engine.py` | UNIT |
| Wellness fail-closed | `test_fail_closed.py` | NEGATIVE_GUARD |

**MISSING tests (ключевые):**
- Тест, падающий если assistant output становится USER evidence в общем виде — **нет** (гарантия = топология call-site).
- Тест на повтор resolved question без controlled reason — **нет** (нет механизма).
- Тесты на 5 canonical states, stable question_id, asked/resolved ledger, TTL 2h, safety-precedence над обычными вопросами, goal-as-required-context, stale chat-action callback, delegation — **нет, потому что нет механизмов**.
- CROSS_REPO / LIVE — отсутствуют (seam'ы только в контрактных документах).

---

## 18. Canon/spec reconciliation

Формат: RUNTIME / SPEC / CLASS / CONSEQUENCE.

1. **Readiness engine**
   - RUNTIME: LLM tool-choice + точечные гейты; 0 из 5 states.
   - SPEC: DRE v1.0 §5–6 (deterministic, LLM_FORBIDDEN, fail-closed, question_ledger).
   - CLASS: **RUNTIME_BEHIND_CANON** (канон не начат; DRE статус «реализуем, не включаем», shadow mode §18.2).
   - CONSEQUENCE: весь question loop сегодня — LLM-импровизация без ledger.

2. **Readiness terminology**
   - RUNTIME/SPEC: ayla-knowledge Glossary «Readiness Level» (exploring/considering/ready) ≠ DecisionReadiness; OPEN_DECISIONS §40.3(б): «DecisionReadiness (§8) и DRF-1533 — одно и то же, названное дважды».
   - CLASS: **TERMINOLOGY_COLLISION**.
   - CONSEQUENCE: риск перекрёстных ссылок при реализации.

3. **Conversation canon location**
   - RUNTIME: канон v1.1 (TTL 2h, quick reply contract, readiness states) отсутствует в mainline обоих repo (только ветка `docs/rescue-knowledge`); ayla-knowledge Conversation Model Specification v1.0 явно отказывается задавать TTL/runtime mechanics.
   - CLASS: **PARALLEL_AUTHORITY + STALE_SPEC**.
   - CONSEQUENCE: корневой канон не воспроизводим из mainline — блокер для любого reconciliation-автомата.

4. **TTL**
   - RUNTIME: 2h inactivity нигде; 30-мин memo TTL забывают resolved facts.
   - SPEC: канон §4.1 (2h inactivity; TTL ≠ забыть resolved semantic fact); DRE §13.4 (ledger fail-closed).
   - CLASS: **RUNTIME_BEHIND_CANON** (обе стороны нарушены: TTL нет, и TTL забывает).
   - CONSEQUENCE: resolved question может повториться через 30 мин.

5. **Evidence provenance**
   - RUNTIME: durable write-path чист (USER_EXPLICIT only); но LLM-контекст содержит assistant; resolver-enum без MODEL_INFERENCE — соответствует.
   - SPEC: DRE §3 (CONFIRMABLE_ORIGINS, нет конструктора ModelSignal→ConfirmedEvidence); Safety Freeze M12; Memory Domain Contract (persistent только user_stated/user_confirmed_inference).
   - CLASS: **RUNTIME_BEHIND_CANON** (нет модели ConfirmedEvidence/ModelSignal; есть только ad-hoc origin) + частичное соответствие.
   - CONSEQUENCE: контаминация durable memory закрыта топологией, не контрактом.

6. **Quick reply**
   - RUNTIME: tap→text подстановка, provenance стирается; ролей нет; «Не знаю»==«Другое» в diet.
   - SPEC: канон §13 (SemanticOption roles, label≠value, free text always valid).
   - CLASS: **RUNTIME_BEHIND_CANON**.
   - CONSEQUENCE: невозможны DELEGATE-driven delegation и CONTROLLED escape.

7. **Safety**
   - RUNTIME: resolver fail-closed — соответствует; producer SafetyResult отсутствует; booking не читает requires_health_check; CLARIFY-приоритет отсутствует.
   - SPEC: Safety Architecture v1 FINAL FREEZE (M5, M11, M13; V4; V8).
   - CLASS: **RUNTIME_BEHIND_CANON** + **TRUE_CONTRADICTION** (booking path vs M5: услуга с requires_health_check бронируется без SafetyResult).
   - CONSEQUENCE: P0.

8. **DRE vs Safety Freeze (spec-vs-spec)**
   - DRE (2026-09-07) держит safety-матрицу UNKNOWN и выносит владельцу вопросы, часть которых Freeze (2026-09-09, owner-approved) уже закрыл (M11–M13).
   - CLASS: **STALE_SPEC** (DRE §22 не ссылается на Freeze).
   - CONSEQUENCE: часть «открытых» вопросов DRE фактически решена — reconciliation должен зафиксировать приоритет Freeze.

9. **Goal**
   - RUNTIME: scalar ClientGoal; goal не required context; explicit intent приоритетнее goal.
   - SPEC: GOAL_CANDIDATE_CONTRACT §4.1 + канон §12.4 (разрешённое несоответствие).
   - CLASS: соответствие; **GoalCandidate как сущность ABSENT** (RUNTIME_BEHIND_CANON, но контракт DRAFT).

10. **Resolver ↔ readiness**
    - RUNTIME: resolver не вызывает readiness (и наоборот некому); `CandidateSetSignature`/`separation` не реализованы (`_pipeline.py:26-30`).
    - SPEC: RESOLVER_CONTRACT §13.3 (резолвер отдаёт сигнатуру, решение — трек A; обратный вызов = второй authority).
    - CLASS: **RUNTIME_BEHIND_CANON** (интерфейс к треку A сознательно не реализован — ожидает этот замер).
    - CONSEQUENCE: seam чист, параллельного authority со стороны resolver нет.

---

## 19. Confirmed contradictions

1. **TRUE_CONTRADICTION (P0):** booking path не читает `requires_health_check` — услуга с противопоказаниями бронируется без health check (`ai/tools_handlers.py:245`, `action_service.py:105-145`, djangoproject-catalog) vs Safety Freeze M5.
2. **CONTRADICTS_CANON:** readiness фактически = LLM tool-choice / LLM confidence floor (DRF-1209) vs канон «readiness — deterministic, не LLM confidence».
3. **CONTRADICTS_CANON:** «Не знаю» → `"other"` в memory_ask diet (`memory_ask.py:275-280`) vs канон §13.2.
4. **CONTRADICTS_CANON:** anketa choice-шаги отклоняют free text метку — tap и текст одного ответа дают разный исход (`skills/fsm.py:261-266`).
5. **CONTRADICTS_CANON:** clarify stale tap → silent misattribution против новейшего offer (`max/handler.py:313,1299`).
6. **CONTRADICTS_CANON (признано, под сторожем):** legacy weighted engine жив в chat-контексте (долг T9).
7. **CONTRADICTS_CANON:** 30-мин TTL забывают resolved semantic facts vs канон §4.1.
8. **PARALLEL_AUTHORITY:** два «канона» о conversation (ayla-knowledge v1.0 без runtime mechanics vs v1.1 вне mainline); 5+ runtime authorities ask-vs-act.
9. **TERMINOLOGY_COLLISION:** «readiness» — два словаря (Glossary vs DRE).

---

## 20. Migration hazards

1. **Введение ledger сломает memo-экономику:** refusal/screening memo (30 мин) сегодня — единственная анти-повторная защита; замена на durable ledger меняет наблюдаемое поведение (бот перестанет «забывать» через 30 мин) — нужен migration policy, регрессионные тесты пиннят текущее поведение (`test_screening_loop_1542.py`).
2. **tap→text подстановка стирает provenance на входе** — введение USER_CLICK origin требует изменения канального контракта до записи в историю; затрагивает все resolve_*_tap резолверы (DRF-990).
3. **Golden replay fixtures идут через DEPRECATED pipeline** — любой новый readiness слой сделает golden-suite stale; pipeline либо revive, либо fixtures переснять.
4. **Канон v1.1 вне mainline** — reconciliation и любые ссылки на канон не воспроизводимы из dev/main; сначала вернуть канон в mainline.
5. **Измеренная ветка ai-bot-platform на 43 коммита позади origin/dev** — часть находок может быть уже изменена в dev (см. §23).
6. **`_SOURCE_CHOICES` допускает `conversational`, unstamped→explicit** (catalog) — при введении строгого provenance существующие записи потребуют backfill-классификации.
7. **Mini App вопросы приходят из Ayla API** — stable question_id нельзя ввести только в боте; контракт cross-repo.
8. **Зарезервированные reason-code namespaces** (`_reason_codes.py:7-10`) — коды трека A зарезервированы, но не реализованы; коллизий при вводе нет, но registry — single source.

---

## 21. Owner decisions required

Канон уже отвечает на: determinism (да), stable ids (да), assistant≠user evidence (нет, нельзя), safety bypass через delegation (нельзя). Это НЕ вопросы владельцу (см. §22).

**OD-DR-1 — Пороги дискриминации.**
- Runtime evidence: resolver возвращает StageVerdict, но `tau_separation`, `N_broad`, `N_substantiated` не заданы нигде (DRE §16.1, DRF-1519; RESOLVER_CONTRACT CONTROLLED_POLICY).
- Existing canon: значения открыты, владелец не назначил.
- Why canon does not answer: пороги — owner-level policy по самому DRE.
- Option A: назначить пилотные значения сейчас (фиксируются в policy_version). Option B: запустить DRE в shadow с лог-калибровкой и назначить по данным.
- Consequences: A — быстрый старт, риск переспросов/недоспрашиваний; B — отсрочка включения, калиброванные пороги.
- Blocks: включение DRE за флагом (shadow не блокирован).

**OD-DR-2 — Поведение при BLOCKED(ASK_BUDGET_EXHAUSTED).**
- Runtime evidence: сегодня бюджета вопросов нет вообще; DRE §13.5 вводит MAX_ASKS_PER_QUESTION_ID=2, MAX_CONSECUTIVE=2; открытый вопрос владельца 2 в DRE.
- Existing canon: исчерпание бюджета → BLOCKED, но что показывать пользователю — не задано.
- Option A: «тихий» fallback — рекомендация с оговоркой без вопроса. Option B (рекомендация DRE): каталог по явному выбору без Recommendation.
- Consequences: A — риск нерелевантной рекомендации; B — честная деградация, лишний клик.
- Blocks: DRE §13.5 enforcement UX.

**OD-DR-3 — Может ли PROMOTED_MEMORY закрывать safety-слот.**
- Runtime evidence: health evidence сегодня не персистится как safety evidence; screening answers вообще не хранятся.
- Existing canon: Freeze M7 «transient health evidence не auto-promote», M12 «SafetyResult не создаёт медицинское evidence обратно» — частично закрывают, но вопрос «подтверждённый ранее факт (напр. хрон. состояние из анкеты) закрывает ли safety-слот без повторного вопроса» явно не закрыт (DRE открытый вопрос 1, рекомендация B «предзаполнение»).
- Option A: PROMOTED_MEMORY никогда не закрывает safety-слот (всегда переспрос). Option B: предзаполнение с подтверждением («актуально ли ещё…»).
- Consequences: A — безопаснее, раздражает повторами; B — меньше трения, требует validity windows (V4: универсального TTL нет).
- Blocks: DRE §16.2 safety-таблица, M11-совместимый question registry.

**OD-DR-4 — Кто хранит ResumeSummary (24h) и его состав.**
- Runtime evidence: сегодня short-term memory Redis 24h существует, но ResumeSummary как минимального безопасного summary нет; Conversation живёт бессрочно.
- Existing canon: канон §4.2 определяет семантику («не authoritative transaction truth»), но owner хранилища открыт (DRE открытый вопрос 3).
- Option A: ai-bot-platform (Redis, рядом с short-term). Option B: Ayla backend (durable, cross-surface).
- Consequences: A — быстрее, не переживает деплой/смену surface; B — консистентнее для Mini App, дороже.
- Blocks: реализация TTL 2h (канон §4.1) — без неё ResumeSummary неактивируем.

---

## 22. What is NOT owner decision

- DecisionReadiness deterministic — **уже решено** (DRE §6, Z1–Z5; Freeze).
- Stable question_id — **уже решено** (DRE §13.2; Freeze M11).
- Assistant output как USER evidence — **запрещено** (Freeze M12; DRE §3; Memory Domain Contract).
- Delegation bypass safety/required context — **запрещено** (DRE §10.2; канон §7.2/§8).
- «Не знаю» ≠ «Другое», free text всегда доступен — **уже решено** (канон §13.2) → найденные нарушения — дефекты, не вопросы.
- Safety CLARIFY приоритет над обычными вопросами — **уже решено** (DRE §13.6).
- NOT_APPLICABLE по semantics, не по отсутствию данных — **уже решено** (OD-SAFE-1/§72; исполнено в resolver).
- TTL 2h inactivity и «TTL не забывает resolved facts» — **уже решено** (канон §4.1) → отсутствие/нарушение — gap, не вопрос.
- Повтор resolved question только по controlled reason (закрытый список из 5) — **уже решено** (DRE §13.5).

---

## 23. Unknown / not measured

1. **origin/dev ai-bot-platform (+43 коммита от измеренного checkout):** возможны изменения в затронутых файлах. Быстрая проверка — `git diff fd6f4e87 origin/dev -- apps/orchestrator apps/skills apps/channels | diffstat`; полная перепроверка находок §3–§17 на dev не выполнена. UNKNOWN_NOT_MEASURED.
2. **Mini App / Ayla internal API question content & ids:** содержимое вопросов goal-select/recommendation flow вне обоих repo (`miniapp_api/views.py:3594` → Ayla). UNKNOWN_NOT_MEASURED.
3. **Ask-eligibility policy (memory_ask):** внешняя anti-spam policy в Ayla (`memory_ask.py:125-131`) — внутренности не измерены. UNKNOWN_NOT_MEASURED.
4. **Live runtime probe:** все выводы — code-path proof; ни один сценарий не прогнан на живой системе (staging). UNKNOWN_NOT_MEASURED (runtime).
5. **Флаг `DISCOVERY_CLARIFY_MIN_TIER` в production env:** default 0 (выключен) — фактическое env-значение не измерено.
6. **Telegram surface memory extraction:** отсутствует write-path — намеренно или gap, не установлено (документировано как PARTIAL).

---

## 24. Exact reproduction commands

```bash
ROOT=C:/Users/user/PycharmProjects/Ayla

# --- Measurement bases ---
for r in ai-bot-platform djangoproject-catalog ayla-ai-core ayla-knowledge; do
  git -C "$ROOT/$r" branch --show-current
  git -C "$ROOT/$r" rev-parse HEAD
done
git -C $ROOT/ai-bot-platform rev-list --count origin/dev..HEAD   # 3
git -C $ROOT/ai-bot-platform rev-list --count HEAD..origin/dev   # 43

# --- A: отсутствие canonical DecisionReadiness в runtime ---
grep -rn "NEEDS_DISCRIMINATION\|NEEDS_REQUIRED_CONTEXT\|INSUFFICIENT_EVIDENCE" \
  $ROOT/ai-bot-platform/apps $ROOT/djangoproject-catalog/ai $ROOT/djangoproject-catalog/recommendation \
  $ROOT/ayla-ai-core/src --include="*.py"
# ожидание: 0 совпадений (кроме reservation-комментария в recommendation/_reason_codes.py:7-10)

grep -rn "question_ledger\|allow_recommend\|readiness_state" \
  $ROOT/ai-bot-platform/apps --include="*.py"
# ожидание: 0 совпадений

# --- A: фактический authority (LLM tool-choice) ---
sed -n '1100,1263p' $ROOT/ai-bot-platform/apps/orchestrator/concierge.py   # build_concierge_system_prompt
sed -n '47,80p'   $ROOT/ai-bot-platform/apps/orchestrator/intent_router.py
sed -n '25,35p'   $ROOT/djangoproject-catalog/ai/prompts.py                 # правило 5 confirm_booking

# --- B/C: отсутствие semantic question_id ---
grep -rn "question_id" $ROOT/ai-bot-platform/apps $ROOT/djangoproject-catalog --include="*.py" | grep -v test | grep -v callback_query_id
# ожидание: пусто

# --- D: разрозненные TTL, отсутствие 2h ---
grep -rn "1800\|600" $ROOT/ai-bot-platform/apps/orchestrator/refusal_memo.py \
  $ROOT/ai-bot-platform/apps/skills/health_screening/memo.py | head
grep -rn "7200\|hours=2" $ROOT/ai-bot-platform/apps $ROOT/djangoproject-catalog/ai --include="*.py" | grep -vi test
# ожидание: нет session TTL 2h

# --- E: единственный write-path extraction ---
grep -rn "record_explicit_green_facts" $ROOT/ai-bot-platform/apps --include="*.py" | grep -v test
# ожидание: apps/channels/max/handler.py:2453 (+ определение в orchestrator/memory/personal_context.py)
grep -rn "extract_user_facts" $ROOT/ai-bot-platform/apps --include="*.py" | grep -v test
# ожидание: только personal_context.py:61

# --- E: assistant в LLM-контексте ---
sed -n '576,589p'   $ROOT/ai-bot-platform/apps/orchestrator/concierge.py   # history, обе роли
sed -n '1318,1405p' $ROOT/ai-bot-platform/apps/orchestrator/concierge.py   # tool result as role=user

# --- F: nutrition loop (DRF-1542) ---
sed -n '292,330p' $ROOT/ai-bot-platform/apps/orchestrator/nutrition_global.py
sed -n '1,60p'    $ROOT/ai-bot-platform/apps/skills/health_screening/memo.py
cd $ROOT/ai-bot-platform && .venv/Scripts/python -m pytest \
  apps/orchestrator/tests/test_screening_loop_1542.py \
  apps/skills/health_screening/tests/test_screening_memo_1542.py -q

# --- G: «Не знаю» == «Другое» ---
sed -n '275,280p' $ROOT/ai-bot-platform/apps/orchestrator/memory_ask.py
# --- G: anketa отклоняет текст-метку ---
sed -n '227,268p' $ROOT/ai-bot-platform/apps/skills/fsm.py   # validate_choice только slug

# --- H: clarify stale misattribution ---
sed -n '313,370p'  $ROOT/ai-bot-platform/apps/channels/max/handler.py   # _last_clarification_offer
sed -n '285,300p'  $ROOT/ai-bot-platform/apps/skills/nutrition_anketa/skill.py  # step-сегмент выбрасывается

# --- Safety: resolver fail-closed + booking seam missing ---
sed -n '235,260p' $ROOT/djangoproject-catalog/recommendation/_stages.py
grep -rn "requires_health_check" $ROOT/djangoproject-catalog/ai --include="*.py"
# ожидание: 0 (booking/chat path не читает)
cd $ROOT/djangoproject-catalog && .venv/Scripts/python -m pytest \
  recommendation/tests/test_safety_not_applicable.py \
  recommendation/tests/test_resolve_endpoint.py -q

# --- Canon specs (лежат на origin/dev ai-bot-platform) ---
git -C $ROOT/ai-bot-platform show origin/dev:docs/specs/DECISION_READINESS_ENGINE_v1.0.md | sed -n '1,60p'
git -C $ROOT/ai-bot-platform show origin/dev:docs/decisions/Ayla_Safety_Architecture_v1_FINAL_FREEZE_2026-09-09.md | sed -n '1,40p'
git -C $ROOT/ai-bot-platform ls-tree --name-only origin/dev docs/specs/
```

---

*Конец measurement. STOP condition соблюдён: ничего не исправлено, не спроектировано сверх reconciliation, PR/Linear не тронуты.*

---

## 25. origin/dev Delta Recheck

**Дата:** 2026-09-09 (после owner rulings OD-DR-1..4 CLOSED).
**Base (previous measured SHA):** `fd6f4e87acde54aebc0cc7c50d55bff49fab82dc` (feat/recommendation-boundary-client).
**Target:** `origin/dev` = **`83ed56a94eb0a96d9599c632f0e6ba2d48c6c5b7`** (получен через `git fetch origin dev` в момент замера).
**Scope:** только ai-bot-platform origin/dev, read-only через `git grep`/`git show`/`git diff` по ref, без checkout. djangoproject-catalog / ayla-ai-core / ayla-knowledge НЕ перепроверялись (вне scope этого recheck).
**Объём дельты:** 146 файлов, +18016/−1553; в релевантных областях (`orchestrator`, `skills`, `channels`, `bookings`, `conversations`, `persona`, `identity`) изменены 28 файлов; содержательные изменения — menu/OD-UI-2, DRF-1576 (клавиатура повторного отказа), DRF-1559 (manager addressing), DRF-1563/1565 (resolver boundary client + ranking guard), visits, scheduling/tenancy admin. Файлы `skills/health_screening/*`, `orchestrator/nutrition_global.py`, `skills/nutrition_anketa/*`, `orchestrator/memory_ask.py`, `skills/fsm.py`, `orchestrator/safety/*` — **без изменений**.

### Owner rulings, полученные после основного measurement (контекст, не переоткрываются)

- **OD-DR-1 CLOSED:** DecisionReadiness v1 — сначала shadow calibration; discrimination thresholds = versioned controlled policy, калибруются на реальных данных; hard required-context rules статистической калибровки не требуют.
- **OD-DR-2 CLOSED:** `ASK_BUDGET_EXHAUSTED` блокирует semantic recommendation; fabricated fallback запрещён; разрешён controlled user-directed continuation (категории / самостоятельный выбор услуги / начать заново) с обычными Safety и execution gates.
- **OD-DR-3 CLOSED:** PROMOTED_MEMORY сама по себе не закрывает safety slot; memory = evidence только по rule-specific admissibility (provenance + applicability + temporal validity + consent + contradiction checks); глобального memory-safety TTL не вводить.
- **OD-DR-4 CLOSED:** ResumeSummary P0 owner = ai-bot-platform, storage = Redis; ConversationState inactivity TTL = 2h; ResumeSummary TTL = 24h; summary — не authoritative domain truth, не mutable transaction truth, не UserMemory.

### Проверка 14 пунктов

**1. Runtime DecisionReadiness появился?**
- PREVIOUS: 0 совпадений (`fd6f4e87`).
- CURRENT origin/dev: 0 совпадений (`git grep "DecisionReadiness|readiness_state|allow_recommend" origin/dev -- apps/` → пусто). Спека присутствует в main tree (`docs/specs/DECISION_READINESS_ENGINE_v1.0.md` на origin/dev), кода нет.
- CHANGED: **NO**.
- IMPACT: вердикт §2.1 стоит.

**2. Canonical states появились?**
- PREVIOUS: 0 из 5.
- CURRENT: `git grep "NEEDS_DISCRIMINATION|NEEDS_REQUIRED_CONTEXT|INSUFFICIENT_EVIDENCE" origin/dev -- apps/` → 0.
- CHANGED: **NO**. IMPACT: §2.4 стоит.

**3. Stable semantic question_id появился?**
- PREVIOUS: только quasi-id (FSM step, field, booking token).
- CURRENT: `git grep "question_id|question_ledger" origin/dev -- apps/` (без тестов) → 0.
- CHANGED: **NO**. IMPACT: §2.5 стоит.

**4. Asked/resolved ledger появился?**
- PREVIOUS: 5+ независимых memo, единого нет.
- CURRENT: `git grep "decision_readiness|resolved_by_evidence" origin/dev -- apps/` → 0; набор memo-хранилищ без изменений (соответствующие файлы не входят в дельту).
- CHANGED: **NO**. IMPACT: §2.6 стоит.

**5. ConversationState 2h inactivity TTL появился?**
- PREVIOUS: отсутствует; `resolve_active_conversation` возвращает Conversation независимо от возраста.
- CURRENT: `git grep "7200|hours=2" origin/dev -- apps/` → только booking reminders (`TWO_HOURS`), eventbus cleanup, metrics windows; session/conversation TTL отсутствует; `apps/conversations/` в дельте — только `apps.py`.
- CHANGED: **NO**. IMPACT: §2.6/§7 стоят; теперь это прямой gap против CLOSED OD-DR-4 (owner = ai-bot-platform, TTL 2h).

**6. Global ask-vs-act authority изменился?**
- PREVIOUS: LLM tool-choice консьержа (`concierge.py` system prompt), точечные детерминированные гейты.
- CURRENT: дельта `concierge.py` — 11 строк (DRF-1576: клавиатура едет с повторным отказом); `build_concierge_system_prompt`/`ask_clarification` на месте (12 вхождений); authority = LLM.
- CHANGED: **NO**. IMPACT: §2.2/§2.3 стоят.

**7. Assistant/user evidence boundary изменился?**
- PREVIOUS: единственный write-path `handler.py:2453 record_explicit_green_facts(event.text)`; extraction только из `personal_context.py:61`; assistant в LLM-истории.
- CURRENT: тот же единственный call-site (`handler.py:2466`, сдвиг строки), `extract_user_facts` вызывается только из `personal_context.py:61`; новых callers нет.
- CHANGED: **NO**. IMPACT: §2.8 стоит (durable — нет contamination; LLM-контекст — да; теста-сторожа по-прежнему нет).

**8. Nutrition screening loop изменился?**
- PREVIOUS: DRF-1542 фикс (вето по словам человека + memo 30 мин); ответы не хранятся; нет question_id.
- CURRENT: `git diff fd6f4e87 origin/dev -- apps/skills/health_screening apps/orchestrator/nutrition_global.py apps/skills/nutrition_anketa` → пусто.
- CHANGED: **NO**. IMPACT: §2.10 стоит.

**9. Quick reply / free-text semantics изменились?**
- PREVIOUS: tap→text подстановка со стиранием provenance; anketa choice — slug only; «Не знаю»→`"other"` в memory_ask diet.
- CURRENT: дельта `quick_actions.py` — только diary-кнопки/меню (OD-UI-2, DRF-1547); `memory_ask.py`, `skills/fsm.py` без изменений.
- CHANGED: **NO**. IMPACT: §2.11 стоит (включая CONTRADICTS_CANON «Не знаю»==«Другое»).

**10. Stale callback behavior изменился?**
- PREVIOUS: эталонный CAS у PendingBookingAction; clarify tap против новейшего offer (misattribution); anketa игнорирует step-сегмент.
- CURRENT: дельта `bookings/callbacks.py` — только manager addressing (DRF-1559); `_last_clarification_offer`/`_CLARIFY_LOOKBACK=12` на месте (`handler.py:312,369,1307`); anketa без изменений.
- CHANGED: **NO**. IMPACT: §15 и P0-дефекты стоят.

**11. SafetyResult producer появился?**
- PREVIOUS: safety gate = regex pre_check + canned replies; канонического SafetyResult (Freeze M13: `safety_contract_version`, `based_on_state_revision`, capability_decisions) нет.
- CURRENT: `SafetyResult` в `safety/pre_check.py` — это regex-вердикт `ALLOW/CLARIFY/BLOCK/HANDOFF` (существовал и на base SHA; `apps/orchestrator/safety/` вне дельты). Канонического producer'а нет.
- CHANGED: **NO**. IMPACT: §13 стоит. Терминологическая коллизия зафиксирована: имя `SafetyResult` занято regex pre-check, не Freeze-контрактом.

**12. Safety CLARIFY → Question Resolver появился?**
- PREVIOUS: отсутствует; CLARIFY-приоритет над обычными вопросами не реализован.
- CURRENT: `gate.py:16-19,57-59,145` — CLARIFY **намеренно пропускается** в normal handling («deliberately do NOT short-circuit CLARIFY»); механизма приоритетного safety-вопроса нет.
- CHANGED: **NO**. IMPACT: §13 стоит; дополнительно зафиксировано: текущий gate по дизайну конфликтует с DRE §13.6 (SAFETY_CLARIFICATION > остальные) — это задокументированное расхождение, не регресс.

**13. Booking начал читать requires_health_check?**
- PREVIOUS (уточнение scope): в ai-bot-platform booking skill гейт **уже существовал** на base SHA (`_service_requires_health_check`, handoff `booking_health_check_required`, `skill.py:1037-1057`; 13 вхождений на `fd6f4e87`). P0-противоречие §19.1 относится к **djangoproject-catalog** (`ai/tools_handlers.py:245`, `action_service.py:105-145`) — вне scope этого recheck.
- CURRENT: ai-bot-platform booking gate без изменений (13 вхождений, файлы вне дельты).
- CHANGED: **NO** (в ai-bot-platform). IMPACT: P0 по catalog-пути не перепроверен — см. REMAINING UNKNOWN.

**14. Parallel readiness authorities исчезли/изменились?**
- PREVIOUS: ~5 параллельных контуров (booking skill, screening, intent_router, confidence floor, personalization/legacy engine в catalog).
- CURRENT: центральный authority не появился; существующие контуры без изменений. Коммит `cf8ba57f` (DRF-1563/1565) добавил resolver boundary client + ranking guard — это транспорт/гард границы резолвера, не readiness authority.
- CHANGED: **NO**. IMPACT: §2.9 стоит.

### Delta verdict

**EXECUTIVE VERDICT CHANGED: NO.** Ни один из 12 пунктов §2 не изменён; counts не изменены. Owner rulings OD-DR-1..4 не переоткрываются; OD-DR-4 превращает отсутствие TTL 2h из «gap против канона» в «gap против CLOSED owner decision» (приоритет повышен, суть та же).
