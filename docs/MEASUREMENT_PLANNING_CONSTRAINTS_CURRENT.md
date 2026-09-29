# MEASUREMENT: Planning Constraints — Current Runtime / Knowledge

**Трек:** G4-PC — Planning Constraints — CURRENT RUNTIME / KNOWLEDGE MEASUREMENT
**Режим:** MEASURE-FIRST, read-only. Production code не писался, правила не создавались, migrations не делались, Linear не трогался.
**Дата замера:** 2026-09-09
**Парные документы (canon, не переоткрываются):** `docs/specs/PLANNING_CONSTRAINTS_INVENTORY.md` (07.09), `docs/specs/PLANNING_CONSTRAINTS_CONTRACT_v1.0.md` (DRAFT), `docs/HANDOFF_PLANNING_RULES_REGISTRY.md` (08.09, D-1 отвечен), `docs/specs/PLAN_ENGINE_CONTRACT_v1.0.md` (DRAFT), `docs/specs/PLAN_ENGINE_DEPENDENCY_MAP.md`.

---

## 1. Measurement bases

Замер снят по **рабочим чекаутам** в `C:/Users/user/PycharmProjects/Ayla/`. Каноническая ветка ни для одного репозитория явным решением не установлена → для целей замера зафиксирован фактический HEAD + состояние `origin/main`/`origin/dev`, где это расходится.

| Repo | Branch (фактический HEAD) | SHA | Замечания |
|---|---|---|---|
| `ayla-knowledge` | `drf-1148/canon-under-version-control` | `9e166179e9634b36498010cfad50e9e402c61729` | HEAD **не содержит** реестра планировочных правил. Реестр существует на `origin/main` (merge `207eeb6`, PR #20, 08.09.2026) и замерен отдельно через `git show origin/main:...`. Working tree: `UX Agents/`, `docs/`, `repomix-output.xml` — untracked |
| `ayla-ai-core` | `fix/memory-origin-vocabulary` | `73b0422b01e7e684491b7d2fe83e15e3b65fc836` | clean tree |
| `djangoproject-catalog` | `dev` | `95c917e684652476feef3ae9d790fb2c8d277378` | живой контур каталога (пилотные замеры владельца оперируют `services_salonservice`) |
| `ai-bot-platform` | `feat/recommendation-boundary-client` | `fd6f4e87acde54aebc0cc7c50d55bff49fab82dc` | **не содержит** `apps/planning_rules/` — точка приёма реестра существует только на `origin/dev` (commit `fd41ff10`, PR #1485, 08.09) |
| `docs` (канон/specs) | `main` | `b3c782be493f1a7ca4c883c0ac973c8e448334e8` | носитель контрактов |

**Не измерено в этом прогоне:** дерево `djangoproject` (legacy-параллельное; прежний инвентарь 07.09 покрыл его частично — см. §21, P1-D4), `frontAyla`, `ayla-knowledge-main` (вторая копия канона, schema 1.14 против 1.13 — расхождение P1-D5 подтверждено прежним инвентарём), production DB (контейнеры не поднимались — все числа заполненности из seed-файлов и прежних `STALE`-замеров).

---

## 2. Executive verdict

| # | Вопрос | Ответ |
|---|---|---|
| 1 | Есть ли executable Planning Constraints Registry сегодня? | **НЕТ на замеренных SHA.** На `ayla-knowledge@origin/main` реестр существует (`03 AI System/Contracts/planning-rules-registry.yaml`, `registry_version: "0.1"`, 13 записей: **10 UNKNOWN, 3 INTENTIONALLY_UNSUPPORTED, 0 KNOWN**), точка приёма с версией-сторожем существует на `ai-bot-platform@origin/dev` (`apps/planning_rules/`, PR #1485). На замеренных HEAD — отсутствует в обоих. Даже на новейших ветках payload не несёт ни одного значения |
| 2 | Authoritative timing rules? | **НЕТ.** Есть только transaction timing (booking window, cancellation, slot hold) — CONTROLLED_POLICY транзакционного контура, не planning |
| 3 | Authoritative frequency rules? | **НЕТ**, и вывод запрещён (ADR-0012 N-04); в реестре `REPETITION = INTENTIONALLY_UNSUPPORTED` |
| 4 | Authoritative sequence/dependency rules? | **НЕТ** для процедур. Существует sequence слотов интентов (`intent-registry.yaml`, draft, ничем не читается) — другой предмет |
| 5 | Authoritative compatibility rules? | **НЕТ**, сознательно отклонено владельцем (OD-CI-4/OD-CI-5); в реестре `COMPATIBILITY/INCOMPATIBILITY = INTENTIONALLY_UNSUPPORTED` |
| 6 | Event-relative rules? | **НЕТ.** Есть окна атрибуции маркетинга (`Killer PRD.md:286-290`) и иллюстративная проза — не правила |
| 7 | Provenance? | **PARTIAL.** Форма определена контрактом (§5: `rule_id/source/version/applicability`) и реализована в реестре на main. В runtime на замеренных SHA provenance планировочных утверждений **отсутствует**; единственный живой словарь происхождения — `matched_by` (идентичность строки каталога, не planning) |
| 8 | Versioning? | **PARTIAL.** `registry_version: "0.1"` + отвержение неизвестной major на `origin/dev`. Ни один источник planning-правил в эксплуатации версий не имеет (каталог — `updated_at`, safety-движок — без версий) |
| 9 | Читает ли rules какой-либо Plan Engine? | **НЕТ.** Plan Engine не существует. Wellness-модели fail-closed и unreachable (§13) |
| 10 | Может ли LLM сегодня выдать unsupported planning assertions? | **ДА.** Ни один живой промпт не запрещает интервалы/курсы/последовательность/совместимость. С 08.09 существует исходящий regex-гард `_PLANNING` (`apps/orchestrator/safety/outbound.py:212-245`), но он ловит только модально-маркированные формы — немодальные выдуманные интервалы проходят **by design** (закреплено тестом `test_outbound.py:360,387-390`) |
| 11 | Есть ли guard против invented numbers? | **PARTIAL.** Только shape-based regex на выходе (fail-open при ошибке regex, `outbound.py:292-294`). Number-vs-rule валидации нет — не с чем сравнивать. Prompt-level запрета нет |
| 12 | Что Ayla СЕГОДНЯ имеет право сказать в сценарии «свадьба через два месяца»? | См. §19/S1. Доказуемо: существование услуг, цены, длительности (domain facts), доступность мастеров и слотов, гейт здоровья для 102 помеченных шаблонов, честное «не знаю» про сроки подготовки. НЕ доказуемо: что делать за N дней, сколько сеансов, в каком порядке, что совместимо |

### Counts (по всем четырём репозиториям, замеренные material findings)

| Authority class | Count | Что входит |
|---|---|---|
| AUTHORITATIVE_RULE (planning) | **0** | — |
| CONTROLLED_NOT_EXECUTABLE | **~8** | реестр на main (13 записей, 0 значений); `PlanAction.cadence` (нет caller'а); ADR-0009 передача contraindications в `apps/kb`; resolver client (нет caller'а); `policy_version`-поля в draft Recommendation Contract; evidence registry (gated) |
| CONTROLLED_POLICY (исполняемые, но НЕ planning-семантика) | **~10** | booking/cancel/reschedule политики; `requires_health_check`-каскад + гейт записи; safety regex-движок; outbound `_PLANNING` guard; ID-anti-hallucination в ai-core; wellness fail-closed gates |
| DOMAIN_FACT | **~4 группы** | цепочка длительностей template→salon→specialist; `buffer_after_minutes`; цены; `_WATER_GLASS_ML` |
| DISPLAY_TEXT_ONLY | **много (5 групп)** | имена услуг-курсов/абонементов; event-relative имена («Уход перед мероприятием»); `aftercare_text`; 13 строк contraindications в seed; goal-chip «Собраться к событию»; design docs |
| PROMPT_ONLY | **9 поверхностей** (8 live + 1 dead) | concierge/discovery/booking/FAQ/master-assistant/ai-drafts/intent-resolution prompts + `voice_examples` (dead) |
| TEST_ONLY | **6+ наборов** | outbound fixtures, adversarial replay, KB pregnancy smoke, adherence, seed guard, wellness fail-closed |
| SEED_ONLY | **2** | «Курс 10 процедур» (dev seed `seed_dev_formula_tela.py:63`); абонементы 4/5/6/8/10 (имена в canonical seed) |
| LLM_GENERATED | **2** (неконтролируемые рабочие записи на шине) | `UX Agents/bus/PLANNING-RULES-brief-*.md` (untracked) |
| UNSUPPORTED_ASSUMPTION / дефекты | **4** | G6: `services/models.py:578-580` (template=None → floor False); G6: `users/recommendation_source.py:388,271`; legacy YClients health path fail-open (`skill.py:1444-1446,1512-1513`); live FAQ few-shot с выдуманными часами/ценой/длительностью (`faq/prompts.py:116-124`) |
| CONTRADICTORY | **3** | см. §22 |
| UNKNOWN (по 13-типовой шкале контракта) | **10 из 13** | в реестре на main; +1 тип (DURATION) известен в runtime, но в реестре тоже UNKNOWN |

---

## 3. Global inventory (Track A)

Семантические паттерны (interval/days/frequency/course/sequence/compatible/recovery/prerequisite + русские эквиваленты) прогнаны по code / Markdown / YAML / JSON / prompts / migrations / seeds / serializers / validators / tests всех четырёх репозиториев. Итог по носителям:

| Носитель | Содержимое | Класс |
|---|---|---|
| `ayla-knowledge@main: 03 AI System/Contracts/planning-rules-registry.yaml` | 13 записей-оболочек, 0 значений | CONTROLLED_BUT_NOT_EXECUTABLE |
| `ai-bot-platform@origin/dev: apps/planning_rules/` | точка приёма: vendored artifact + `source.json` sha256-pin + тест отвержения unknown major | CONTROLLED_BUT_NOT_EXECUTABLE (нет на замеренном SHA) |
| `djangoproject-catalog/services/models.py` | duration chain, `requires_health_check`, `contraindications` (не экспонируется), `buffer_after_minutes`, `aftercare_text` | DOMAIN_FACT / CONTROLLED_POLICY / DISPLAY_TEXT_ONLY |
| `djangoproject-catalog/wellness/models.py` | `PlanOutcomeLink.target_date`, `PlanAction.cadence {per_day, per_week}` — gated, unreachable | CONTROLLED_BUT_NOT_EXECUTABLE |
| `ai-bot-platform/apps/orchestrator/safety/outbound.py:212-245` | `_PLANNING` regex-категория (13 форм) | CONTROLLED_POLICY (исполняемый гард, не правило) |
| Prompts (9 поверхностей) | ни одного planning-правила; anti-invention списки покрывают мастер/цена/адрес/длительность/ID | PROMPT_ONLY |
| Seeds | «Абонемент на N процедур», «Курс …» — строки в `name` | SEED_ONLY / DISPLAY_TEXT_ONLY |
| ayla-knowledge проза | запреты (N-04, OD-CI-4/5), booking-константы с owner-decision provenance, иллюстративные примеры | CONTROLLED_POLICY / DOMAIN_FACT / DISPLAY_TEXT_ONLY |

**Ни одного structured, versioned, executable planning rule ни в одном замеренном контуре.**

---

## 4. Timing rules (Track B)

Assertions вида «X за N дней до/после Y»:

| Где | Что | Hard/Recommendation | Provenance | Executable consumer | Вердикт |
|---|---|---|---|---|---|
| `djangoproject-catalog/djangoProject/settings/base.py:420-422` | `BOOKING_MIN_AHEAD_MINUTES=60`, `BOOKING_MAX_AHEAD_DAYS=60`, `BOOKING_SLOT_GRID_MINUTES=30` | hard | platform config | `slot_builder.py:20-23,56-59`, `policies.py:296-318` | TRANSACTION_CONSTRAINT, не planning |
| `appointments/domain/policies.py:122-124,269` | cancel 24h/2h/50%, reschedule 4h | hard | platform policy | policies | TRANSACTION_CONSTRAINT |
| `notifications/tasks.py:31-33,42-44` | reminder T−60min; aftercare T+2h±30min | hard | platform policy | Celery beat | TRANSACTION/OPS, не planning |
| `ayla-knowledge 05 Architecture/Ayla Core Domain Model Specification.md:285,951,1785` | Slot Hold TTL 15 мин; горизонт проекции 30 дней; late-window 1 час; 3 reschedule | hard (домен) | **AYLA-DEC-0021/0022 (owner decisions, cited)** | backend | DOMAIN_FACT с AUTHORITATIVE provenance, booking-domain |
| `ayla-knowledge 02 Strategy/Killer PRD.md:286-290` | окна атрибуции 24ч/72ч/14д/90д | measurement policy | draft PRD | none | DISPLAY_TEXT_ONLY (не planning rule — инвентарь §T4 это явно запрещает переиспользовать) |

**Числа, зашитые в prompt/code без provenance (planning-shaped):**

- `ai-bot-platform/apps/skills/faq/prompts.py:116` — «с 10:00 до 22:00» (LIVE few-shot, выдуманные часы).
- `ai-bot-platform/apps/skills/faq/prompts.py:123` — «от 1500 рублей за сеанс 60 минут» (LIVE few-shot).
- `ai-bot-platform/apps/promptreg/voice_examples.py:80` — «Накануне в 19:00 пришлю напоминание» (DEAD_CODE).
- `ayla-ai-core/src/ayla_ai_core/prompts.py:147,152` — «Через 3 дня» / «Через неделю» (UX-навигация при пустых слотах, PROMPT_ONLY, не service-planning claim).
- `ai-bot-platform/apps/promptreg/voice_examples.py:209` — «Реалистично 2-3 кг/месяц» (DEAD_CODE).

**Planning-timing правил (событийных/интервальных) не найдено ни одного.**

---

## 5. Frequency / repeatability (Track C)

- **Правил частоты нет.** `REPETITION` в реестре на main: `INTENTIONALLY_UNSUPPORTED` (обоснование: ADR-0012 N-04 запрещает вывод курса из поведения).
- Коммерческая упаковка vs клинический курс — различие подтверждено замером: «Абонемент на 4/5/6/8/10 процедур» существуют **только как строки `name`** в `services/seeds/canonical_catalog_2026-07.json:18098-18158`; поля `sessions_count` не существует нигде. «Курс 10 процедур» — в dev-seed description (`ai-bot-platform/apps/catalog/management/commands/seed_dev_formula_tela.py:63`) → проецируется в KB body (`kb/projectors.py:107-108`) → **может попасть в контекст LLM в dev** (SEED_ONLY, опасный путь).
- `PlanAction.cadence {per_day, per_week}` + `target_count` (`wellness/models.py:382-404`) — сознательно бедный каденс (docstring :373-375), production write path отсутствует, читатель `wellness/adherence.py:68-94` имеет только тестовый caller → DEAD_CODE.
- Wellness/nutrition relapse thresholds (`returning_success_service.py:36-40`) — CONTROLLED_POLICY, но про питание, не про процедуры.
- Рынок продаёт курсами 6–8 сеансов (`docs/catalog/MARKET_PENZA.md:22,96`), в каноне понятия курса нет — зафиксировано инвентарём §T7.

**M-вывод (Track M):** M1 — да, коммерческая упаковка (имена); M2 — semantic claim отсутствует; M3/M4 — controlled sequence/frequency отсутствуют; M5 — никто не утверждает; M6/M7 — ни Plan Engine, ни Recommendation Resolver использовать это не могут (и обязаны не использовать).

---

## 6. Sequence / dependencies (Track D)

- Процедурный sequence: **ABSENT** во всех контурах. Проверены `sequence/step/prerequisite/depends_on/requires/сначала/затем` — все попадания: `sort_order` (порядок показа), зависимости миграций, CI/релизные гейты. **Ни одна не является planning dependency.**
- Существующие sequence другого типа (не путать): порядок слотов интента (`intent-registry.yaml`, draft, unread); порядок стадий booking-транзакции (проза канона); pipeline stages резолвера рекомендаций (draft contract).
- Граф зависимостей процедур, таблица `A→B`, `B requires A` — не существуют ни как схема, ни как данные.

---

## 7. Compatibility / incompatibility (Track E)

- **INTENTIONALLY_UNSUPPORTED** по OD-CI-4/OD-CI-5 (`ayla-knowledge/05 Architecture/Ayla Goal Outcome Semantic Model Working Design.md:500-514,590-592`): глобальный реестр, pairwise matrix, mutual exclusions не создаются. В реестре на main обе записи несут этот статус с provenance.
- Таблицы отношений услуга↔услуга не существует ни в одной схеме.
- Единственная исполняемая «совместимость» — entity-membership (мастер предлагает услугу) в `ayla-ai-core/tool_handlers.py:246-571` — не service-service incompatibility.
- Единственное natural-language утверждение несовместимости — dead few-shot «при беременности и варикозе антицеллюлитный нельзя» (`voice_examples.py:69-75`): PROMPT_ONLY, unreachable, authority отсутствует.
- Outbound guard блокирует **форму** «нельзя совмещать/несовместим» (`outbound.py:226-228`) с rationale «реестра нет и не будет, значит сказать нечего» — корректный fail-closed на уровне реплики.

---

## 8. Event-relative rules (Track F)

- **Controlled event-relative semantics отсутствуют полностью.** Полей `event_date`, `before_event`, `time_to_event` нет ни в одной модели.
- Goal chip «Собраться к событию» (`services/seeds/goal_options_2026-08.json:33-40`) — ярлык категорий без даты события (DISPLAY_TEXT_ONLY).
- Семантический слой **исключает** событие явно: `Ayla Goal Outcome Semantic Model Working Design.md:610,1113,1647` — event/Journey Mode не моделируется в DesiredOutcome.
- Имена услуг «Уход перед мероприятием», «Прическа для мероприятия» и т.п. — display names, не правила.
- «Идеально перед событием»-советы существуют только как иллюстративная проза в draft strategy (`Killer PRD.md:189-195`) — authority отсутствует.

---

## 9. Catalog / service fields (Track G)

| Поле | Schema exists? | Populated? | Authoritative? | Writer | Reader | Runtime consumer |
|---|---|---|---|---|---|---|
| `duration` (template/salon/specialist/service) | да (`services/models.py:99-110,246-248`) | canonical seed: **все NULL** (`seed_canonical_catalog.py:127-129`); demo seed: 171; curated seed ~50 | DOMAIN_FACT | seeds/admin | `resolved_duration()` :562-572 → serializers :258-259 | slot engine, bot mirror |
| `requires_health_check` (3 уровня) | да (:114-117,388,534) | 102 true / 1223 template; salon/specialist default False | CONTROLLED_POLICY (safety gate) | seed/admin | cascade :574-583 → serializers :222,252,261-262 | recommendation safety stage, booking gate (бот) |
| `contraindications` | да (:118-121) | **13 строк** текста из 1223 | нет | seed | **NONE** — ни в одном serializer | DEAD_CODE |
| `aftercare_text` | да (`Service`, :259-272) | EXISTS_EMPTY | нет | admin only | `notifications/tasks.py` (verbatim push) | DISPLAY_TEXT_ONLY |
| `recommended_frequency / min_interval / max_interval / sessions_count / course_size / recovery_period / before_event / after_event / compatibility / prerequisites` | **НЕТ — ноль полей** | — | — | — | — | MISSING |
| `buffer_after_minutes` | да (:254-257,535) | да | DOMAIN_FACT (transaction) | admin | slot_builder :36,52-53 | slot engine |

**Duration ≠ planning interval** — зафиксировано: нигде duration не интерпретируется как planning constraint.

---

## 10. ayla-knowledge reality (Track H)

Цепочка, замеренная пострелочно:

```
KNOWLEDGE EXISTS ............ EXISTS (проза + 3 machine-readable contracts; 0 planning rules на HEAD)
   ↓
REGISTRY EXISTS ............. EXISTS только на origin/main (13 записей, 0 KNOWN) / MISSING на замеренном SHA
   ↓
EXPORT EXISTS? .............. MISSING на замеренном SHA (scripts/ = validate_knowledge.py + render_domain_registry.py + amd001 validator; CI не публикует артефактов).
                              PARTIAL на origin/main (реестр + CI-сторож валидации) и origin/dev бота (vendored artifact + sha256 pin)
   ↓
RUNTIME LOAD? ............... MISSING на замеренном ai-bot-platform SHA; EXISTS на origin/dev (apps/planning_rules/registry.py)
   ↓
CONSUMER? ................... MISSING везде — registry.py на dev ничем не потребляется для решений (точка приёма, не consumer)
   ↓
DECISION? ................... MISSING — Plan Engine не существует
```

- `.knowledge/sources-manifest.yaml` — inbound-only, `enabled_default: false` (все источники disabled).
- Provenance-поля правил: только в реестре на main (`provenance.source/version`); в tracked-контенте замеренного SHA structured provenance отсутствует (prose-citations: ADR-0012 «Источник: …», AYLA-DEC-#### inline).
- Versioning: frontmatter `version` + `schema_version: "1.13"` (на main — 1.14, разделение P1-D5 подтверждено); `intent-registry.yaml` несёт `registry_version: "1.0"` при статусе draft.
- Safety knowledge: CSR v1.4 (`06 Safety and Governance/Consent Scope Registry.md`) — **единственный approved-status документ**: health_related_signal вне MVP/запрещено (:159), подтверждённая беременность = red (:1186). Prose, не executable.
- Recovery-window правил: **ABSENT** во всём tracked-контенте.

---

## 11. ai-core reality (Track I)

| Искомое | Вердикт |
|---|---|
| PlanningAssertion / PlanningConstraint / PlanValidation / PlanDecision / Plan / PlanStep | **MISSING** — ноль совпадений в `src/` и `tests/` |
| constraint/compatibility/interval evaluator | **MISSING** |
| rule registry client / knowledge loader | **MISSING** — ноль ссылок на ayla-knowledge в коде |
| Anti-invention guard | EXISTS но узкий: `prompts.py:133-134` «НИКОГДА не выдумывай цены, длительность, режим работы» — интервалы/курсы/совместимость **не покрыты** |
| ID anti-hallucination | EXISTS (`tool_handlers.py:246-571`) — фильтрует только ID |
| Safety (pregnancy/contraindications) | не реализовано; red-zone keys молча отбрасываются из prompt (`memory.py:188-202`, тест `test_memory.py:94-97`) — privacy guard, не safety-planning; ADR-0009:165 назначает фильтрацию в ai-bot-platform |
| Versioning (policy/rule/knowledge) | **ZERO** matches repo-wide |
| Planning tests | **ZERO**; negative guards покрывают только ID/структуру/приватность |

**LLM в ai-core может свободно выдумать интервал** — канала для rules нет, запрета нет, валидации нет, теста нет.

---

## 12. Bot / prompt behavior (Track J)

Живые prompt-поверхности (все PROMPT_ONLY, anti-invention списки ограничены мастер/цена/адрес/длительность/ID):

1. `apps/orchestrator/concierge.py:1100` (concierge, LIVE) — anti-hallucination :1158-1159; medical boundary :1245-1250; nutrition addendum «БЕЗ ЦИФР» :1286-1302.
2. `apps/orchestrator/discovery.py:574-618` (LIVE).
3. `apps/skills/booking/prompts.py:121-266` (LIVE).
4. `apps/skills/faq/prompts.py:207-245` (LIVE, KB-grounded — единственный правильный образец; но few-shot :112-140 несёт выдуманные числа).
5. `apps/orchestrator/intent_resolution.py:149-231` (LIVE, verbatim-evidence extraction).
6. `apps/orchestrator/intent_router.py:66` (DEAD_CODE в prod).
7. `apps/master_api/services/assistant.py:101-128` (LIVE).
8. `apps/master_api/services/ai_drafts.py:391-396` (LIVE).
9. `apps/promptreg/voice_examples.py` (DEAD_CODE — render_voice_examples без caller'ов; канал впрыска через tenant `voice_examples` открыт, проверки содержимого нет — P1-D3).

**Ответы J1–J7:**

| Вопрос | Ответ |
|---|---|
| J1 LLM может придумать interval? | **ДА** на prompt-уровне (ничего не запрещает). На выходе — частично блокируется: `_PLANNING` ловит модальные формы («нужно/следует раз в неделю»); немодальные («Приходите через месяц») проходят by design (`test_outbound.py:360,387-390`) |
| J2 course length? | Prompt: не запрещено. Output: блокируются формы «курс из N» и modal+quantity (`outbound.py:214-215,222`); «курс десяти процедур»/«курсом» без «из» — не покрыто (shape-bound) |
| J3 sequence? | **Гарда нет ни на одном слое** — ни prompt rule, ни regex для «сначала X, потом Y» |
| J4 compatibility? | Output-block: `outbound.py:226-228` (нельзя совмещать/несовместим). Prompt: ничего |
| J5 guard против invented numbers? | Только regex shape-filter `evaluate_outbound` + `guard_outbound` (`safety/gate.py:179-244`). Number-vs-rule валидации нет. Guard **fails open** при ошибке regex (`outbound.py:292-294`) |
| J6 structured evidence input? | PARTIAL: KB chunks (включая contraindication doc-type) для FAQ; catalog tool results; wellness codes-only. **Planning rules не инжектируются никуда — нечего инжектировать** |
| J7 output validation чисел против правил? | **НЕТ.** Resolver client валидирует структуру/версию/reason_codes и отвергает display-строки, но не planning-семантику, и не имеет production caller'а |

---

## 13. Wellness / Plan model seam (Track K)

`djangoproject-catalog/wellness/models.py` (gated per docstring :1-13):

| Модель | timing | sequence | frequency | target_date | dependencies | provenance | version |
|---|---|---|---|---|---|---|---|
| `DesiredOutcome` (:23-90) | нет | нет | нет | нет | нет | нет | нет |
| `PersonalPlan` (:93-129) | нет | нет | нет | нет | нет | нет | нет |
| `PlanOutcomeLink` (:132-204) | нет | нет | нет | **да, nullable (:163-167)** + computed `horizon_status` (:186-198) | нет | нет | нет |
| `PlanAction` (:362-417) | нет | нет | `cadence {per_day,per_week}` + `target_count` (:382-404) | нет | нет | нет | нет |

Факты: production write path отсутствует (ноль non-test `.objects.create`); reader — `wellness/context_read.py:37-83` отдаёт боту только коды статусов + `horizon_status` (raw `target_date` сознательно НЕ отдаётся, docstring :14-17); `adherence.py` — test-only caller (DEAD_CODE). Gates fail-closed.

**Зафиксировано, не решено:** наличие `target_date` на `PlanOutcomeLink` — current fact; канон (§54, HANDOFF «Что уже решено») уже закрепил: целевая дата на `DesiredOutcome`, горизонт на `PlanOutcomeLink`. Принадлежность Plan не переоткрывается.

---

## 14. Booking vs Planning constraints (Track L)

Обязательная классификация — все найденные booking-ограничения отнесены к TRANSACTION:

| Constraint | Где | Класс |
|---|---|---|
| min advance 60 min / max ahead 60 days / grid 30 min | `settings/base.py:420-422` → slot_builder, policies | TRANSACTION_CONSTRAINT |
| cancel 24h free / 2h partial 50% | `policies.py:122-124,194-210` | TRANSACTION_CONSTRAINT |
| reschedule min 4h | `policies.py:269,285-290` | TRANSACTION_CONSTRAINT |
| Slot Hold TTL 15 min / horizon 30 days / late window 1h / 3 reschedules | канон Core Domain Model (AYLA-DEC-0021/0022) | TRANSACTION (domain facts с owner provenance) |
| working hours / time off / closures / external busy | `appointments/models.py:478-784`, `services/availability.py` | TRANSACTION_CONSTRAINT |
| reminder T−60 / aftercare T+2h | `notifications/tasks.py:31-44` | OPS timing |
| `buffer_after_minutes` 0–120 | `services/models.py:254-257` | TRANSACTION (slot spacing) |

**Ни одно из них не является и не может стать planning constraint** («процедуру за N дней до события») — преобразование запрещено.

---

## 15. Packages / courses (Track M)

См. §5. Дополнительно: «Программа восстановления после родов/отпуска/стресса», «Составление плана процедур», «Сопровождение курса» — service names в canonical seed (`:4373,17003,17033,17363,17933,18038,18263,18278`) — DISPLAY_TEXT_ONLY. Составных объектов/M2M/self-FK у услуг нет (инвентарь §T7 подтверждён). «Абонемент на 10 процедур» не доказывает «Goal requires 10 sessions» — зафиксировано как запрещённый вывод.

---

## 16. Provenance (Track N)

«WHY DO WE BELIEVE THIS?» по классам:

| Evidence class | Примеры | Вердикт |
|---|---|---|
| Normative controlled rule | реестр на main (13 записей со structured provenance, но 0 значений) | EXISTS, valueless |
| Authoritative domain fact | AYLA-DEC-0021/0022 booking-константы (проза с cited provenance) | EXISTS (prose) |
| Verified canonical relation | `matched_by ∈ {pair, pair+duration, manual}` — идентичность строки, «never guess» | EXISTS, не planning |
| Human-curated rule | safety regex-константы `apps/orchestrator/safety/*` | EXISTS без rule_id/policy_version |
| Imported source | canonical seed (102 health-check flags «draft flags for later owner review», seed_canonical_catalog.py:10-11) | PARTIAL — draft provenance |
| LLM inference | — | запрещён каноном; живого пути в planning нет, но и стены нет (§12) |
| Marketing copy | «Курс 10 процедур» (dev seed), MARKET_PENZA.md | NOT evidence |
| Unknown | все timing/frequency/sequence/compatibility значения | **UNSUPPORTED** |

---

## 17. Versioning / invalidation (Track O)

- `registry_version: "0.1"` + `compatible_contract_version: "1.0"` — только на main; runtime-сторож отвержения unknown major — только на origin/dev.
- Каталог: `updated_at` строки — не версия (инвентарь §5.3 контракта: «ни один источник не имеет годной схемы версий»).
- Safety-движок: версий нет вообще (`outbound.py` константы).
- Consumers caching rules: `apps/planning_rules` vendor-ит артефакт с sha256-pin (на dev) — единственный контролируемый кэш.
- **Ответ на вопрос «Plan по Rule v1, завтра v2 — найдём ли затронутые Plans?»**: **НЕТ.** Plan Engine и PlanRevision не существуют; механизм уведомления о смене версии не существует (контракт §9.6 сам это фиксирует). **Migration/revalidation hazard подтверждён структурно, а не гипотетически.** (Revalidation Engine не проектируется — per brief.)

---

## 18. Test reality (Track P)

| Тест | Где | Класс |
|---|---|---|
| Invented planning shapes blocked (13 форм) | `ai-bot-platform apps/orchestrator/safety/tests/test_outbound.py:299-353` | UNIT + NEGATIVE_GUARD |
| Anti-overreach (приглашения/факты проходят) | `test_outbound.py:355-391` | UNIT |
| Guard budget / `_MUST_BLOCK` corpus | `test_outbound_guard_budget.py:123-291` | UNIT/GOLDEN-corpus |
| Guard wired на всех reply paths | `apps/channels/tests/test_handler_outbound_guard.py`, `test_handler_safety_parity.py` | CONTRACT (wiring) |
| Health-gate UNKNOWN→closed | `apps/catalog/services/tests/test_upserter_master_services.py:218-286` | UNIT + NEGATIVE_GUARD |
| Safety NOT_APPLICABLE AST-guard | `djangoproject-catalog/recommendation/tests/test_safety_not_applicable.py` | NEGATIVE_GUARD |
| Wellness fail-closed | `wellness/tests/test_fail_closed.py` | NEGATIVE_GUARD |
| Catalog contract field-set mirror | `services/tests/test_catalog_contract_s3d.py` | CONTRACT |
| Resolver client contract | `apps/integrations/ayla/tests/test_recommendation_resolver_client.py` | CONTRACT |
| Boundary guard (no new ranking in bot) | `tests/contracts/test_recommendation_boundary_guard.py` | CROSS_REPO-mirror CONTRACT |
| ai-core ID anti-hallucination | `ayla-ai-core/tests/test_tool_handlers.py` | UNIT + NEGATIVE_GUARD |
| **LLM-hallucinated-number end-to-end (живой transcript через гард)** | **NOT FOUND** | MISSING |
| **«no rule → no invented interval» на prompt-уровне** | **NOT FOUND** | MISSING |
| **Timing/frequency/sequence/incompatibility/provenance/versioning tests** | **NOT FOUND** (кроме shape-regex выше) | MISSING |
| marketing text → not hard rule; package count → not course | **NOT FOUND** как тесты | MISSING |

---

## 19. Scenario probes (Track 24)

### S1. «Через два месяца свадьба, хочу комплексно подготовиться»

- **SUPPORTED:** услуги существуют (каталог, duration/price как domain facts); мастера и слоты доступны (transaction); goal chip «Собраться к событию» существует как навигация; для 102/1223 шаблонов — health-check gate; Ayla может честно сказать «не знаю сроков подготовки».
- **UNKNOWN:** что делать за 60/30/14 дней до события; сколько сеансов; в каком порядке; что с чем совместимо; дата события не хранится нигде.
- **UNSUPPORTED_BUT_CURRENTLY_POSSIBLE_FOR_LLM:** выдумать «начните за месяц», «курс из 6 сеансов с интервалом 2 недели» — prompt не запрещает; outbound гард поймает только если фраза модально-маркирована. Иллюстративный пример в Killer PRD («До события осталось 14 дней… план процедур») — не правило и не вход.

### S2. «Хочу регулярно расслабляться»

- **SUPPORTED:** разовая запись; repeat-booking как транзакция.
- **UNKNOWN:** weekly/monthly/course — доказательств ноль; REPETITION intentionally unsupported; cadence-модель wellness gated/unreachable.
- **UNSUPPORTED_BUT_CURRENTLY_POSSIBLE_FOR_LLM:** «приходите раз в неделю» — shape-dependent на гарде.

### S3. «Хочу лучше восстанавливаться после тренировок»

- **SUPPORTED:** nothing planning-grade.
- **UNKNOWN:** interval, sequence, recovery rule — RECOVERY_WINDOW = UNKNOWN в реестре; `aftercare_text` пуст и display-only.
- **UNSUPPORTED_BUT_CURRENTLY_POSSIBLE_FOR_LLM:** «восстановление занимает три дня» — заблокируется (есть в negative corpus), но «обычно пару дней» пройдёт (hedged форма запрещена контрактом §4.4, не ловится кодом).

### S4. «Хочу улучшить кожу к событию»

- **SUPPORTED:** nothing event-relative.
- **UNKNOWN:** EVENT_WINDOW = UNKNOWN; event-date не собирается ни одним интентом.
- **UNSUPPORTED_BUT_CURRENTLY_POSSIBLE_FOR_LLM:** event-relative timing claim без гарда при немодальной форме.

---

## 20. Authority matrix (Track 25)

| Responsibility | ayla-knowledge | ai-core | backend catalog | bot | LLM | human/admin |
|---|---|---|---|---|---|---|
| rule identity | AUTHORITATIVE (назначено D-1; реестр на main) | ABSENT | ABSENT | CONSUMER (dev only) | LLM_FORBIDDEN | author via change control |
| timing | ABSENT (только booking-проза DOMAIN_FACT) | ABSENT | DOMAIN_FACT (transaction only) | CONSUMER | LLM_FORBIDDEN (стены нет) | salon: SlotConfig (transaction) |
| frequency | INTENTIONALLY_UNSUPPORTED (N-04) | ABSENT | ABSENT (cadence gated/dead) | ABSENT | LLM_FORBIDDEN | — |
| sequence | ABSENT (процедурный) | ABSENT | ABSENT | ABSENT | LLM_FORBIDDEN (гарда нет вообще) | — |
| compatibility | INTENTIONALLY_UNSUPPORTED (OD-CI-4/5) | ABSENT (только entity-membership) | ABSENT | outbound-block формы | LLM_FORBIDDEN | — |
| event-relative rule | ABSENT (исключено из семантики) | ABSENT | ABSENT | ABSENT | LLM_FORBIDDEN (стены нет) | — |
| provenance | AUTHORITATIVE (форма на main) | ABSENT | PARTIAL (matched_by — row identity) | ABSENT | LLM_FORBIDDEN | — |
| versioning | CONTROLLED (frontmatter/registry_version) | ABSENT | ABSENT (updated_at ≠ версия) | CONSUMER (major-reject на dev) | — | — |
| execution | ABSENT (нет export на HEAD) | ABSENT | DOMAIN executor (booking/safety gate) | executor (safety гарды) | LLM_ALLOWED (render/extract only — по контракту) | — |
| rendering | ABSENT | PRESENTATION (prompt) | ABSENT | PRESENTATION | LLM_ALLOWED (отрисовка готового assertion) | — |

---

## 21. Canon / spec reconciliation (Track 26)

| # | RUNTIME | CANON | CLASS | CONSEQUENCE |
|---|---|---|---|---|
| R1 | `outbound.py:212-245` — `_PLANNING` категория **существует** (13 форм, wired на всех reply paths) | CONTRACT §7.3 требует её **построить** («четвёртая категория — условно planning_claim»); INVENTORY §6.3/P1-D1 фиксирует «фраза уходит человеку» (замер 07.09, `outbound.py:70-74` три категории) | **RUNTIME_AHEAD_OF_SPEC** (гард построен после 07.09) | Обновить spec-ссылки; остаточный риск (немодальные формы, fail-open) в spec не отражён |
| R2 | Реестр существует на `origin/main` ayla-knowledge + точка приёма на `origin/dev` бота | CONTRACT §5.2/§9 «неисполнимо до D-1»; HANDOFF фиксирует D-1 отвечен 08.09 | **RUNTIME_AHEAD_OF_SPEC** (на ветках новее замеренных) | Контракт §12 п.1 может быть снят после merge в замеренные линии; замеренные HEAD этого не содержат |
| R3 | ai-core anti-invention: «цены, длительность, режим работы» | INVENTORY §6.2 подтверждает тот же список | согласовано | planning не покрыт — gap подтверждён обеими сторонами |
| R4 | Состояния safety кода: `ALLOW/CLARIFY/BLOCK/HANDOFF`; канон: `NORMAL/CLARIFY/CAUTION/STOP` + UNKNOWN | INVENTORY §13.2 + HANDOFF_SAFETY_MATRIX (решения §72-74: приоритет STOP>CLARIFY>CAUTION>NORMAL) | **TERMINOLOGY_COLLISION**, известная, owner rulings приняты, миграция — чужое окно | Не planning scope; зафиксировано |
| R5 | `requires_health_check`: template=None → floor False (`services/models.py:578-580`); legacy-only master → `CandidateFacts(False)` (`recommendation_source.py:388,271`) | SAFETY принцип «UNKNOWN → fail-closed»; CONTRACT §8.2 | **TRUE_CONTRADICTION** (G6 defect, свежий) | False НЕ является доказанным planning/safety fact; gate-критично |
| R6 | FAQ prompt few-shot: выдуманные часы/цена/длительность (`faq/prompts.py:116-124`), LIVE | INVENTORY §6.4 называет этот же prompt «единственным правильным образцом» (по grounding-части :233-242) | **TRUE_CONTRADICTION** внутри одного файла: grounding-rules образцовые, few-shot — invention teacher | Удаление чисел из few-shot — дефект, не owner decision |
| R7 | `PlanOutcomeLink.target_date` существует в wellness (gated) | HANDOFF «Что уже решено»: дата на DesiredOutcome, горизонт на PlanOutcomeLink (§54) | согласовано (не переоткрывается) | — |
| R8 | Capability registry CAP-001..027 — способности подсистем | CONTRACT §10.2 + INVENTORY §T1: коллизия имён разведена | TERMINOLOGY_COLLISION, задокументированная | Реестр способностей услуг не существует |
| R9 | `docs/OPEN_DECISIONS.md` отсутствует в замеренном чекауте ai-bot-platform (есть только в worktree-копиях); код ссылается на него (`outbound.py:27`) | — | **STALE_SPEC / parallel trees** | Authority-текст живёт вне замеренного дерева |
| R10 | PLAN_ENGINE_CONTRACT = DRAFT; Plan Engine не существует | DRAFT держат safety matrix + §6.1 VERIFIED | согласовано | Реестр значений (0 KNOWN) не блокирует форму, блокирует содержание |

---

## 22. Confirmed contradictions (сильнейшие)

1. **G6 (двойной):** отсутствие canonical/template связи молча читается как `requires_health_check=False` в двух независимых точках (`services/models.py:578-580`; `users/recommendation_source.py:388` + hard-coded `safety_blocked=False` :271) — при том что вся архитектура построена на «UNKNOWN → fail-closed», а bot-сторона на `None` гейт держит закрытым. Backend и bot моделируют UNKNOWN **противоположно** на одном и том же факте.
2. **Spec vs runtime по исходящему гарду:** контракт §7.3 описывает planning-категорию как несуществующую; runtime её имеет, но с осознанными дырами (немодальные формы пропускаются, regex-error → fail-open), о которых spec молчит.
3. **FAQ prompt:** один файл одновременно — эталон заземления (:233-242) и источник выдуманных чисел в LIVE few-shot (:116-124).
4. **Канон vs product vision по событиям:** семантический слой явно исключает event (`Goal Outcome …:610,1113,1647`), а Killer PRD/Product Vision обещают «подготовку к событию → план процедур». EVENT_WINDOW в реестре UNKNOWN, а не INTENTIONALLY_UNSUPPORTED — статус не отражает исключение из семантики.

---

## 23. Migration / revalidation hazards

- **H1.** Смена версии правила сегодня необнаружима для потребителей: нет механизма уведомления, нет PlanRevision, нет consumers. Любой будущий Plan, построенный до проводки versioning, станет невалидируемым молча. (Не проектируется — per brief.)
- **H2.** Реестр и точка приёма живут на ветках (`origin/main`, `origin/dev`), не входящих в замеренные HEAD → merge-order hazard: consumer появится раньше значений; пустой реестр с working plumbing — допустимое состояние по решению владельца, но рантайм обязан отличать «реестр пуст» от «реестра нет».
- **H3.** Два backend-дерева (`djangoproject` vs `djangoproject-catalog`) — P1-D4 не закрыт этим замером; любая миграция planning-полей в «бэкенд» двусмысленна до решения.
- **H4.** Dev-seed «Курс 10 процедур» доезжает до KB body → LLM context; при подключении живых tenant `voice_examples` (P1-D3) открывается второй канал впрыска без проверки содержимого.
- **H5.** schema-версии знаний разошлись (1.13/1.14, P1-D5) — export boundary обязан пинновать schema_version, иначе drift молчалив.

---

## 24. Safety seams (Track 5 брифа)

- Safety Architecture v1 FINAL FREEZE не переоткрывалась; planning ничего не создаёт в safety.
- Что planning может читать сегодня: `resolved_requires_health_check` (tri-state, bot: NULL → closed) и template-level флаг (102/1223, draft provenance «for later owner review»).
- Где UNKNOWN подменяется: §22.1 (две точки backend) + legacy YClients path fail-open (`skill.py:1444-1446,1512-1513`, model default False открывает гейт).
- Гейт записи — routing-only, не interlock: `apps/booking/services/create.py`, `admin_api/views_booking_create.py`, miniapp создают записи, не читая флаг.
- Pregnancy: runtime-логики нет; red-факт исключается из prompt в ai-core (privacy), консультируется нигде; Tier-B pregnancy FSM не портирован (`health_screening/skill.py:26-38`).
- Contraindication text: 13 строк в seed, не экспонируется никому; в боте sync не заполняет колонку (`upserter.py:128`).
- Health-related assertion без controlled safety provenance → не planning rule: ни одного такого правила не существует, и существующие тексты (13 строк, few-shot, dev seed) классифицированы NON-authoritative.

---

## 25. Owner decisions required (Track 27)

Только то, что не отвечено существующим каноном.

**OD-PC-1 — Допустимые классы свидетельства для перевода записи реестра в KNOWN.**

- Runtime evidence: реестр существует с 0 KNOWN; контракт §5.5 перечисляет, что provenance НЕ является (модель, description, слова пользователя, рынок), но не определяет, какое свидетельство **достаточно** для, например, MIN_INTERVAL лазерной эпиляции.
- Existing canon: D-1 назначил носитель и change control; HANDOFF запретил заполнять выдуманными значениями; классы evidence не определены нигде.
- Why canon does not answer: носитель решён, критерий наполнения — нет.
- Option A: owner-curated вручную per-rule (каждая KNOWN-запись — отдельное решение владельца с cited source).
- Option B: классовая политика («медицинская норма из названного класса источников достаточна для kind X») — владелец утверждает классы один раз, записи заводятся по ним.
- Consequences: A — медленно, каждое значение рецензировано; B — быстрее, но требует осторожной калибровки классов; обе не блокируют plumbing.
- Blocks: любое наполнение реестра значениями; снятие DRAFT с содержательной части PLANNING_CONSTRAINTS_CONTRACT.

**OD-PC-2 — Статус event-relative planning: исключить или планировать?**

- Runtime evidence: событие исключено из семантического слоя (`Goal Outcome …:610,1113,1647`); дата события нигде не хранится; product vision и Killer PRD обещают «подготовку к событию»; EVENT_WINDOW в реестре UNKNOWN.
- Existing canon: §15 допускает Plan; Goal/Outcome модель событие не моделирует; коллизия не разрешена ни одним документом.
- Why canon does not answer: два нормативных слоя противоречат (исключение из семантики vs продуктовое обещание).
- Option A: EVENT_WINDOW → INTENTIONALLY_UNSUPPORTED для v1 (событие = только goal chip + честный UNKNOWN-ответ).
- Option B: событие вводится как context (event_date на Goal/DesiredOutcome) + EVENT_WINDOW остаётся UNKNOWN до правил — то есть канал есть, значений нет.
- Consequences: A — честно, дёшево, закрывает сценарий S1/S4 отказом; B — открывает сценарий «свадьба» как first-class, но требует моделирования event context (чужой трек: Goal/Wellness authority).
- Blocks: любой ответ на S1/S4 сверх «не знаю»; координация с треком Goal/Wellness/Plan Authority.

---

## 26. What is NOT an owner decision

- Где живёт normative registry → **решено D-1: ayla-knowledge + export при сборке** (08.09).
- Может ли LLM придумывать timing/frequency/sequence/compatibility → **нет** (контракт §6.5, канон §15.2).
- Можно ли UNKNOWN заменять нулём/false/диапазоном/«обычно» → **нет** (контракт §4, HANDOFF инварианты).
- Package size = нужный курс → **нет без отдельного evidence** (N-04, инвентарь §T7).
- Целевая дата на DesiredOutcome, горизонт на PlanOutcomeLink → **решено §54**.
- Глобальный compatibility registry → **отклонено OD-CI-4/5**; REPETITION — вывод запрещён, статус INTENTIONALLY_UNSUPPORTED.
- Дословный текст UNKNOWN-ответа → **решено D-2** («уточню у мастера и вернусь», DRF-1577).
- Форма PlanValidation (VALID/INCOMPLETE/BLOCKED), SAFETY=UNKNOWN→BLOCKED → **канон §15.6, контракт §8**.

---

## 27. Unknown / not measured

- `djangoproject` (legacy backend tree) — не замерено в этом прогоне (P1-D4 открыт); частичные данные — из инвентаря 07.09, помечены STALE там.
- Production DB / пилот (заполненность живых строк, доля привязанных шаблонов из 265 услуг) — контейнеры не поднимались; числа из документов — STALE.
- `ayla-knowledge-main` (вторая копия канона) — не замерено; расхождение schema 1.13/1.14 подтверждено прежним инвентарём.
- Содержимое внешнего Google Doc, питающего KB (`seed_kb_from_gdocs.py`) — provenance UNKNOWN_NOT_MEASURED.
- `frontAyla` — не замерено (presentation layer, planning-правил ожидать не приходится, но не проверено).
- Потребление `apps/planning_rules` на `origin/dev` сверх точки приёма — не замерено (вне замеренного SHA); по состоянию на dev это intake, а не decision consumer.
- Поведение живого LLM end-to-end (реальные транскрипты через `_PLANNING` гард) — тестов нет, не измерено.

---

## 28. Exact reproduction commands

```bash
# Базы замера
for r in ayla-knowledge ayla-ai-core djangoproject-catalog ai-bot-platform docs; do
  git -C "$r" branch --show-current; git -C "$r" rev-parse HEAD
done

# Реестр на main ayla-knowledge (отсутствует на HEAD 9e16617)
git -C ayla-knowledge show origin/main:"03 AI System/Contracts/planning-rules-registry.yaml"
git -C ayla-knowledge merge-base --is-ancestor 207eeb6 HEAD; echo $?   # 1 = не предок

# Точка приёма на origin/dev бота (отсутствует на HEAD fd6f4e8)
git -C ai-bot-platform ls-tree -r --name-only origin/dev apps/planning_rules/
git -C ai-bot-platform log --oneline -2 origin/dev -- apps/planning_rules
ls ai-bot-platform/apps/planning_rules   # → не существует на замеренном SHA

# Catalog fields
grep -n "requires_health_check\|contraindications\|duration\|aftercare_text\|buffer_after" \
  djangoproject-catalog/services/models.py
grep -n "recommended_frequency\|min_interval\|max_interval\|sessions_count\|course_size\|recovery_period" \
  -r djangoproject-catalog/services/    # → ноль

# G6 defect
sed -n '574,583p' djangoproject-catalog/services/models.py
sed -n '380,395p;265,280p' djangoproject-catalog/users/recommendation_source.py

# Wellness seam
grep -n "target_date\|cadence\|target_count" djangoproject-catalog/wellness/models.py

# Booking transaction constants
sed -n '420,422p' djangoproject-catalog/djangoProject/settings/base.py
sed -n '122,124p;269p' djangoproject-catalog/appointments/domain/policies.py

# Packages/courses как имена
grep -n "Абонемент на\|Курс \|курсом" djangoproject-catalog/services/seeds/canonical_catalog_2026-07.json | head

# Outbound planning guard (runtime ahead of spec)
sed -n '212,245p;292,294p' ai-bot-platform/apps/orchestrator/safety/outbound.py
grep -n "PLANNING\|планиров" ai-bot-platform/apps/orchestrator/safety/tests/test_outbound.py | head

# Live few-shot с выдуманными числами
sed -n '112,140p' ai-bot-platform/apps/skills/faq/prompts.py

# ai-core: anti-invention scope
sed -n '133,134p;147,152p' ayla-ai-core/src/ayla_ai_core/prompts.py
grep -rn "PlanningAssertion\|PlanningConstraint\|PlanValidation\|rule_registry\|knowledge_loader" \
  ayla-ai-core/src/ ayla-ai-core/tests/    # → ноль

# Проводка knowledge → runtime
ls ayla-knowledge/scripts/ ayla-knowledge/.github/workflows/
grep -rn "planning-rules\|planning_rules" ayla-knowledge/scripts/ ayla-knowledge/.github/  # → только на main
grep -rn "ayla-knowledge" ai-bot-platform/apps --include="*.py" | grep -v "^Binary" | head  # → 2 комментария в miniapp TS, 0 в apps/*.py (инвентарь §P1-D6)
```

---

**STOP condition соблюдено:** Plan Engine не построен, registry implementation не создан, правила не перенесены и не добавлены, prompts не исправлены, PR с production implementation не сделан, owner decisions не приняты.
