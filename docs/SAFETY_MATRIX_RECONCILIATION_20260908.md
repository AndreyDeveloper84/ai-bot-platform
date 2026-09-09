# Отчёт главному окну: сверка по окну канонизации матрицы сигналов безопасности

**Дата:** 08.09.2026
**Окно:** канонизация матрицы безопасности (по `docs/HANDOFF_SAFETY_MATRIX.md`)
**Этап:** первый шаг передачи — сверка, а не документ. Код не тронут. Канонический документ не начат: есть вопросы владельцу, блокирующие строки матрицы.

**Источники сверки (три, как велит передача):**

1. Бриф/канон: `docs/ayla-conversation-state-v1.1-reconciled.md` (Working Canon, Decision 4 / §7), `docs/OPEN_DECISIONS.md` §40.4 п.3, §71–§74, `docs/OPEN_QUESTIONS_OWNER.md` §6.3.
2. Действующий контракт: `docs/specs/RECOMMENDATION_RESOLVER_CONTRACT_v1.0.md` (§4.1, §7.2, §7.3, §8.2, §14, §15).
3. Реализация: `ai-bot-platform/apps/orchestrator/safety/**` (пять модулей + тесты), consumers, `apps/skills/health_screening/**`.

---

## 1. Объявление пакета (до первого коммита, по `WINDOW_PATH_REGISTRY.md`)

`apps/orchestrator/safety/**` в реестре путей ни за одним окном не числится — **объявляю пакет за этим окном целиком**:

```
apps/orchestrator/safety/__init__.py
apps/orchestrator/safety/pre_check.py
apps/orchestrator/safety/post_check.py
apps/orchestrator/safety/outbound.py
apps/orchestrator/safety/voice_check.py
apps/orchestrator/safety/gate.py
apps/orchestrator/safety/tests/**
```

Дополнительно окно читает и стыкуется, но **не переписывает** без решения главного окна:

- `apps/skills/health_screening/**` — клиентская поверхность `ayla-95`;
- health-гейт `apps/skills/booking/skill.py:1436-1505` (`_service_requires_health_check`) — клиентская поверхность `ayla-95`;
- safety-блоки в `apps/channels/max/handler.py` и `apps/channels/telegram/handler.py` — транспорт, ничьи пути.

Проверено по реестру 08.09: пересечений с занятым нет.

---

## 2. Расхождения трёх источников (подтверждены чтением, не предположены)

### 2.1. Канон знает 4 состояния, контракт — 6

- Канон v1.1 (`ayla-conversation-state-v1.1-reconciled.md:1391-1394`): схема состояния — только `NORMAL | CLARIFY | CAUTION | STOP`.
- Контракт резолвера §4.1 (`RECOMMENDATION_RESOLVER_CONTRACT_v1.0.md:286`): `NORMAL | CLARIFY | CAUTION | STOP | UNKNOWN | NOT_APPLICABLE` — единственное место, где перечислены все шесть.
- `UNKNOWN | NOT_APPLICABLE` живут только в контракте и OD §72. **Канон после §72 не обновлён.**

### 2.2. Приоритет `STOP > CLARIFY > CAUTION > NORMAL` существует только в передаче

Ни канон, ни контракт, ни OPEN_DECISIONS этой строки не содержат (проверено грепом по всем md). Единственный письменный источник — `HANDOFF_SAFETY_MATRIX.md:36-40`, цитирующий owner ruling. В коде зашит прежний порядок `_VERDICT_PRIORITY = [ALLOW, CLARIFY, BLOCK, HANDOFF]` (`pre_check.py:253-259`).

### 2.3. `CAUTION` не нормирован нигде

- Контракт §14 (`:1164-1189`) содержит строки для STOP / CLARIFY / UNKNOWN / NOT_APPLICABLE — **строки для CAUTION нет**; поведение резолвера при CAUTION не определено.
- В коде CAUTION **не существует вовсе**: система умеет только пропустить или остановить.
- Канон §7.1 даёт лишь «continuation is allowed only within constrained policy space and wording» — без перечня сигналов и без ответа «можно ли записаться».

### 2.4. Строки «сигнала не было» нет ни в одном документе

§72 (`OPEN_DECISIONS.md:5648-5658`) прямо фиксирует это как незакрытый остаток: без такой строки исполнитель по контракту §14 добросовестно вернёт fail-closed на неразговорной поверхности. §71 предупреждал об этом до решения — предупреждение в силе.

### 2.5. В коде три несовместимых вердиктных словаря

| слой | файл | словарь |
|---|---|---|
| inbound | `pre_check.py:60-73` | `SafetyVerdict{allow, clarify, block, handoff}` |
| pipeline-outbound | `post_check.py:54-57` | `PostCheckVerdict{allow, revise, block}` |
| канальный outbound | `outbound.py:262-275` | бинарный `OutboundVerdict{allowed, categories}` |

`NORMAL / CAUTION / STOP / UNKNOWN / NOT_APPLICABLE`, а также `rule_id`, `policy_version`, `evidence_ref`, `ELIG_SAFETY_CLEARED` в safety-слое **отсутствуют** (проверено грепом по `apps/`). Единственный `NOT_APPLICABLE` в коде — `shadow_turn.py:68,158`, пометка шага tool invocation, не safety-вердикт.

### 2.6. Persistence отсутствует конструктивно

Все четыре проверки (`pre_check`, `post_check`, `evaluate_outbound`, `validate_voice`) stateless и per-message: читают только текст, ничего не пишут в `Conversation.skill_state`. Вердикт сохраняется как данные (TurnResult, audit, replay, `Message.action_type="safety_pre_check"`) — на последующие проверки не влияет. Человек назовёт красный флаг, через три реплики попросит записать — система не помнит. Единственное меж-ходовое состояние рядом — memo скрининга (`health_screening/memo.py`, `skill_state`, TTL 1800 с), и оно гасит только повтор SOFT, RED_FLAG никогда.

### 2.7. Сигнал 4 (лекарства): код блокирует любое упоминание

`pre_check.py:129-136` + тест `test_pre_check.py:60-71` («посоветуйте ибупрофен перед массажем» → BLOCK). Инвариант 10 владельца разделяет: **упоминание ≠ запрос подобрать/назначить/рекомендовать**. Это подтверждённое владельцем изменение поведения — пойдёт в контракт с targeted proof обеих веток (краснеет до, зеленеет после). Само по себе не вопрос — но ветка «упоминание без запроса» требует ответа, какое состояние она получает (см. В1).

### 2.8. `health_screening` и safety-движок не связаны кодом

Ни один не импортирует другого (проверено). Красные флаги скрининга (температура, онемение, отдача, давит в груди, теряю сознание — `classifier.py:296-327`) приводят к тексту «Звучит серьёзно — лучше сначала к врачу» (`skill.py:69-73`) и **не получают safety-состояния, запись не закрывают**. Связь только архитектурная: скилл стоит первым в диспетчере (`apps/skills/apps.py:26-31`).

### 2.9. Реестры рассинхронизированы

`OPEN_QUESTIONS_OWNER.md` вопрос №14 (матрица сигналов, §6.3) формально ещё открыт, хотя §72–§74 дали содержательные ответы. После сдачи канонического документа статус обязан быть закрыт главным окном.

---

## 3. Карта consumers (полная, для раздела «что сломается»)

**Inbound-вердикт (`pre_check` / `evaluate_inbound`) читают:**

- `apps/channels/max/handler.py:1603` (global) и `:2678` (per-tenant, с barge-guard по `Conversation.State.HUMAN_HANDOFF`) — `allowed=False` → canned reply, `action_type="safety_pre_check"`, emit `channels.max.safety.pre_check_triggered`;
- `apps/channels/telegram/handler.py:230-252` — зеркало MAX (DRF-1300);
- `apps/master_api/services/assistant.py:203-214` — гейт до rate-limiter («человеку в кризисе нельзя "придите через минуту"»);
- `apps/orchestrator/pipeline.py:678-754` — полный pipeline (по docstring `gate.py:4-7` в проде не используется): BLOCK/CLARIFY → canned fallback, HANDOFF → `AdminTask`;
- `apps/orchestrator/shadow_turn.py:220-232` — shadow-наблюдение, HANDOFF → `CONTROL_HANDOFF`.

**Outbound-страж (`evaluate_outbound` / `guard_outbound` / `post_check`) читают:**

- `apps/channels/max/handler.py:2323,2897`, `telegram/handler.py:304`, `apps/orchestrator/concierge.py:1579-1581`;
- `apps/master_api/services/assistant.py:257-315` (через `_finish`);
- `apps/notifications/proactive.py:201-220`, `apps/nutrition_proactive/tasks.py:102-127`, `apps/bookings/followups.py:186-192`, `apps/admin_api/services/master_deactivation.py:428-439`, `apps/orchestrator/coach_observation.py:219-223`;
- `apps/orchestrator/composer.py:78-149` — `post_check` BLOCK → `_BLOCK_TEMPLATE`, REVISE → префикс «я не врач»;
- `apps/replay/` — поле `safety_decision: "allow"|"block"` в трейсах и фикстурах.

**События/вокаб:** `safety_triggered` (voice_check, consent-денай), `safety.outbound_blocked`, `channels.*.safety.crisis_delivery_failed`, метрика `skill_selected="safety_pre_check"`.

**Что сломается при смене словаря:** ветвления по `BLOCK/HANDOFF` в обоих каналах и master_api; структурный AST-сторож `apps/channels/tests/test_handler_safety_parity.py:385-487` (требует `evaluate_inbound` + `guard_outbound` в каждом модуле, вызывающем `orchestrate_turn`); replay-фикстуры с `safety_decision`; бюджет ложных срабатываний `test_outbound_guard_budget.py`; тесты приоритета `test_pre_check.py:84-94`, `test_post_check.py:73-80`.

---

## 4. Решение, принятое окном самостоятельно (не расширяет политику)

Канонический документ нормирует **два слоя**: inbound-матрицу состояний (сигнал → состояние → поведение → что говорит Ayla → можно ли записаться) и outbound-стражей как отдельный бинарный слой без состояний. В таблице соответствия будет прямо названо: `REVISE` (post_check) — ближайший родственник CAUTION на outbound, но **не** его эквивалент; подгонять не будем. Основание: постановка окна («сигнал → состояние → поведение») описывает разговорную безопасность; outbound-стражи (утечки, обещания, контакты, нагging, выдуманные нормы) не являются состояниями диалога.

---

## 5. Вопросы владельцу (до изменения поведения, формат по `BRIEF_TRACKS_COMMON.md`)

### В1. Семантика `CAUTION`: какие сигналы туда ведут и разрешена ли запись

1. **Не определено:** перечень сигналов, ведущих в CAUTION; разрешена ли запись в CAUTION; какие ограничения формулировок.
2. **Почему канон не отвечает:** §7.1 даёт только «continuation allowed only within constrained policy space and wording»; §24 п.1 прямо оставляет матрицу и формулировки открытыми; контракт §14 строки CAUTION не имеет.
3. **Вариант A:** разговор продолжается, запись разрешена, формулировки ограничены (дисклеймер, без health-рекомендаций).
4. **Вариант B:** разговор продолжается, запись закрыта до resolution.
5. **Последствия:** при B состояние неотличимо от CLARIFY по записи и теряет смысл; при A появляется единственный путь «продолжаем с оговоркой», которого сегодня нет.
6. **Рекомендация: A.**
7. **Блокирует:** строки матрицы для упоминания лекарства (инвариант 10), колонку «можно ли записаться».

### В2. Куда ведут запрос диагноза и юридический совет

1. **Не определено:** состояния для сигналов «поставьте диагноз» и «юридический совет» (сегодня оба BLOCK).
2. **Почему канон не отвечает:** инвариант 10 разделяет только лекарства; про диагноз и юристов владелец не говорил; выводить из кода запрещено (§66).
3. **Вариант A:** STOP — полный отказ с отработкой.
4. **Вариант B:** CAUTION — отказ отвечать по существу темы, диалог и запись продолжаются.
5. **Последствия:** при A обычный вопрос «а что у меня за сыпь?» закрывает диалог так же, как кризис; при B STOP остаётся за кризисом, насилием, острой медициной и запросом назначить препарат.
6. **Рекомендация: B.**
7. **Блокирует:** таблицу соответствия `BLOCK → ?`.

### В3. Красные флаги скрининга — какое состояние

1. **Не определено:** какое safety-состояние получают RED_FLAG-сигналы (`classifier.py:296-327`: температура, онемение, отдача, давит в груди, одышка, теряю сознание, не могу встать).
2. **Почему канон не отвечает:** канон §7 и §24 п.1 оставляют доменную матрицу открытой; код сегодня даёт всем один текст и ноль последствий; health_screening с safety не связан.
3. **Вариант A:** все RED_FLAG → STOP.
4. **Вариант B:** все RED_FLAG → CAUTION.
5. **Вариант C:** разделить — жёсткие (давит в груди, теряю сознание, не могу встать/ходить) → STOP; мягкие (температура, онемение, отдача) → CAUTION.
6. **Последствия:** A закрывает запись человеку с температурой так же, как с болью в груди; B пропускает боль в груди на запись; C различает цену сигнала ценой двух списков флагов.
7. **Рекомендация: C.**
8. **Блокирует:** связь `health_screening` с матрицей, persistence-правила для флагов.

### В4. Resolution/expiry сохранённого safety-состояния

1. **Не определено:** что закрывает сохранённое состояние (инвариант 8 требует памяти между ходами; сроки владелец не назвал — «если понадобится число, это вопрос владельцу»).
2. **Почему канон не отвечает:** §7.2 допускает retraction явной коррекцией пользователя с auditable provenance, но не называет ни порядка resolution, ни TTL.
3. **Варианты resolution:** (а) явная коррекция пользователя с auditable provenance; (б) подтверждение человеком-оператором; (в) TTL.
4. **Числа, которые нужны:** TTL для STOP и для CAUTION отдельно (ориентир рядом: memo скрининга живёт 1800 с).
5. **Последствия:** без TTL состояние вечно (человек с раз погасшим флагом никогда не запишется); без resolution по событию состояние забывчиво — ровно то, что инвариант 8 запрещает.
6. **Рекомендация:** resolution — (а) и (б); expiry — TTL, числа прошу назвать.
7. **Блокирует:** раздел resolution/expiry контракта, тест-сторожа инварианта 8.

---

## 6. Что готово к сборке сразу после ответов

- Таблица соответствия `ALLOW / CLARIFY / BLOCK / HANDOFF` → новые состояния, с прямо названным отсутствием предшественника у CAUTION (не подбираем ближайшее);
- строка «сигнала не было» (остаток §72) с тремя строками §72 и override;
- provenance-форма: `rule_id`, `evidence_ref`, `policy_version`, `activated_at`, правила resolution/expiry (форма есть — `SafetyResult.matched_patterns` + реестр §7.2 контракта; значения — после В4);
- тест-сторожа на каждый из 10 инвариантов — по дереву, не по тексту (§74), с targeted proof;
- список consumers из §3 с конкретикой «что сломается» у каждого;
- две ветки сигнала 4 (упоминание / запрос назначения) с targeted proof обеих.

**Сначала контракт, затем код.** Кода не касаемся, пока документ не сдан и не одобрен.
