# CONTROLLED PILOT READINESS MATRIX — 11.09.2026

**Предмет:** повторный вердикт готовности по форме матрицы 09.09
(`docs/CONTROLLED_PILOT_READINESS_MATRIX.md`) на сегодняшнем `dev` обоих репозиториев.
**Режим:** только замер и сверка. Код не правился, PR по дефектам не открывались, Linear не
трогался. Где вердикт изменился — с доказательством (PR / файл:строка / число с пилота); где
не изменился — так и написано, с тем, чем проверено.
**Составлено:** 11.09.2026 14:40–16:00 UTC, окно ayla-b0, по поручению главного окна [b64ea5].

---

## EXECUTIVE VERDICT

```
CONTROLLED PILOT STATUS:      NO_GO           (09.09: NO_GO — не изменился)
FIRST 3–5 USERS READY TODAY:  NO
STOP BLOCKERS COUNT:          5               (09.09: 7 — B-1 закрыт, B-6 → кандидат в DEGRADED)
DEGRADED CAPABILITIES COUNT:  12              (09.09: 11 — +1: B-6 остаток)
OPEN OWNER DECISIONS COUNT:   4 из 6 прежних + 3 новых (§27)
UNKNOWN_NOT_MEASURED COUNT:   5 → 6           (+1: живые числа наблюдаемости не перемерены)
```

**Что изменилось за двое суток (09.09 → 11.09), одной строкой каждое:**

1. **B-1 закрыт механизмом.** Гейт здоровья стоит в единственной точке истины записи
   (`create_booking_service.py:242-244` → `_booking_guards.check_health_screening`), флаг
   трёхзначен (`services/models.py:927` `-> bool | None`), три поверхности бота разводят 422 в
   передачу, а не в ошибку (#1528). Экспозиция по данным по-прежнему ноль (пилотный салон —
   0 услуг с гейтом), но теперь ноль держится **контролем**, а не отсутствием данных.
2. **B-6 наполовину закрыт:** `payment_required` решает сервер (#305). Остаток — сравнение
   показанного и применённого (цена/длительность) и умолчание `str(uuid4())` — **не изменился**
   (`appointments/internal_api.py:96`). Предлагаю DEGRADED при выключенной предоплате; решает
   владелец (OD-PILOT-7).
3. **B-3 продвинулся, но живого пути не достиг.** Производитель `SafetyResult` построен
   (A-1 `apps/orchestrator/safety/assessment.py`, A-2 `record.py`; шесть состояний §3,
   приоритет корзин, NOT_APPLICABLE по capability — #1559, #1600, #1611, #1613, #1614), но
   `assess()` / `record_verdict()` имеют **ноль вызывающих вне тестов**; живой путь MAX
   по-прежнему `pre_check` (regex) + `tool_choice` LLM. STOP сохраняется.
4. **DecisionReadiness появился как вертикаль** (`apps/orchestrator/decision_readiness/`,
   20 модулей: engine, ledger, questions, resume, shadow, audit — DRF-1629 Done), но на живой
   путь **не подключён**: из `apps/channels`, `concierge.py`, `discovery.py`, `skills/` его не
   импортирует никто. P10 — из MISSING в **BUILT_NOT_WIRED**; STOP для пути рекомендаций
   сохраняется по правилу владельца §10 (ask-vs-act решает LLM).
5. **Транзит рекомендаций переведён на границу резолвера** (#1529, consumer-driven contract).
   Авторитетов ранжирования по-прежнему **три**: `apps/marketplace/discovery.py` и
   `apps/orchestrator/handoff.py` стоят в аллоулисте сторожа
   (`tests/contracts/test_recommendation_boundary_guard.py:48,51`), легаси-движок каталога
   `ai/application/services/recommendation_engine.py` жив с тремя импортёрами. B-4 STOP.
6. **B-5 наполовину закрыт:** снимок базы перед выкладкой бота — в `deploy-dev.yml:96`
   (#1510, с ротацией); выкладка по проверенному SHA — **нет** (`git pull --ff-only origin dev`,
   `:292`); тегов образов нет; `docker-compose.staging.local.yml` в репозитории нет. STOP
   (OD-PILOT-4 открыт).
7. **B-7 не изменился ни в одном пункте:** `CELERY_BEAT_SCHEDULE` без `aggregate_ai_metrics_daily`
   и диспетчера ящика (grep 0); `X-Request-ID` ни в одном из клиентов `apps/integrations/ayla/`
   (grep 0); семантические события — только `recommendation.boundary.no_verified_candidates`
   (`apps/miniapp_api/views.py:2635`); `recommendation_presented`, `stale_callback`,
   `safety_result` не пишутся. STOP.
8. **B-2 не изменился на стороне каталога:** `has_object_permission` — grep 0 на `dev`; PR #318
   открыт, не слит. Бот называет действующий субъект (#1535, B-2.4). STOP.
9. **Continuity цели починена** (#1506: конверт `{"data": …}` снимается на границе,
   `apps/integrations/ayla/goals_client.py:327`); жизненный цикл цели — четыре состояния
   вместо `is_active` (#345, `goals/models.py:24-26`). P3 из PARTIAL с дефектом → PARTIAL без
   дефекта; cap 13 DEFECT → EXISTS.
10. **Питание:** граница каталога не принимает параметры тела без основания (#324), два
    согласия (#1602), утверждение о согласии в POST (#1587), ориентиры без происхождения
    очищаются (#332), дневник без анкеты (#1686). На пилоте `NUTRITION_ENABLED = true` при
    6 профилях с `targets_source = unknown_legacy` — cap 34 DEFECT → PARTIAL, экспозиция живая.
11. **Место и координаты:** `Tenant` получил восемь полей §139 и правило `is_geocoded` (#333),
    адаптер геокодера (#342); на пилоте координат **0 у 11 тенантов и 0 у 31 мастера**,
    `geocode_status = not_attempted` у всех 11 (замер 14:18 UTC). Данными путь не начат.
12. **Выкладка и наблюдаемость выкладки:** дерево пилота принадлежит владельцу (#1574, #1581,
    #1586, #1588, #1596), состояние поверхности снимается командой и публикуется выкладкой
    (#1591, #1609, каталог #337) — cap 42 PARTIAL с новым содержанием; откат по-прежнему нет.

**Оговорка, та же, что 09.09.** `NO_GO` получен применением правила владельца «один surface
bypass — STOP», а не собственной оценкой риска. Поверхностей, пропускающих бронь без гейта
здоровья, **больше нет** (было пять); остались пропуски на **рекомендательных** поверхностях
(MAX-выдача и AI-чат каталога без `SafetyResult`), и они сегодня пусты данными (`verified = 0`).

### Десять ответов — что изменилось

| # | вопрос | 09.09 | 11.09 | доказательство |
|---|---|---|---|---|
| 1 | Что мешает 3–5 людям | 5 поверхностей брони без гейта; общий токен; нет отката; нет событий | гейт закрыт; **общий токен (#318 не слит), откат, события — как были**; плюс `verified = 0` и координат 0 | §6 |
| 2 | Blockers Safety | producer нет; 3 из 8 состояний; 0 из 8 точек читают флаг | producer построен, 0 живых вызывающих; **все** точки брони читают флаг через одну точку истины | `assessment.py:175` 0 callers; `create_booking_service.py:242` |
| 3 | Blockers Recommendation | verified 0; авторитетов три; рубильника нет | **не изменилось**: verified 0 (14:18 UTC), три авторитета, рубильника `RECOMMENDATIONS*` нет (grep 0 в `base.py`) | снимок; аллоулист сторожа |
| 4 | Blockers Booking | цена/длительность молча; `payment_required` от клиента; `uuid4()` умолчание | `payment_required` — сервер (#305); **остальное не изменилось** | `internal_api.py:96` |
| 5 | Blockers DecisionReadiness | 0 из 5 состояний; LLM решает; ledger нет | вертикаль есть (`ledger.py`, `questions.py`, `question_id`), **на живом пути не вызывается**; LLM по-прежнему решает | `concierge.py:428 tool_choice="required"` |
| 6 | VERIFIED-подмножество | нет, 0 | **нет, 0** (review_required 206 / unmapped 59) | снимок 14:18 UTC |
| 7 | Отключить Plan Lite | и не включён | **не изменилось** (`WELLNESS_PROACTIVE_ENABLED` ЗАПЕРТО, писателей 0) | снимок; #1590 — только постановка |
| 8 | Быстро отключить Recommendation | рубильника нет | **не изменилось** | grep `^RECOMMENDATION.*_ENABLED` = 0 |
| 9 | E2E замкнутого контура | CROSS-BOUNDARY 0 | **не изменилось**: cross-boundary 0; CI получил junit и шарды (#1544, #1560) — это доказательство счёта, не контура | `tests/` без cross_boundary |
| 10 | Observability инцидента | AIDailyMetricSummary 0, ReplayTrace 0, id не переживает границу | **не изменилось по коду** (задачи не в расписании, заголовка нет); живые счётчики не перемерены — §29 | grep |

---

## 1. Measurement bases

| repo | ветка | SHA | момент | что внутри |
|---|---|---|---|---|
| `ai-bot-platform` | `origin/dev` | `eb0ea8219bddf22bf184a466748ba070fce9fc75` | 11.09 14:30 UTC | все 110 PR, слитые с 09.09, включая #1605 (14:30) |
| `beautygo_backend` (каталог) | `origin/dev` | `98d4fada5331bd042fe355e6a3891472287a0e30` | 11.09 14:39 UTC | пачка #330…#352 (14:10–14:39) внутри; **#318, #347, #350, #343, #344, #353 — открыты, снаружи** |
| `ayla-ai-core` | `origin/main` | не перемерялось | — | 09.09: `d72a5de4`; в предметах матрицы не участвует (§29 09.09) |

**Живой контур** — снимок `docs/SURFACE_STATE_SNAPSHOT_2026-09-11_1420.md` (главное окно):
хост `ruvds-o1mqo`, бот — файл выкладки 14:06 UTC (`docs/generated/SURFACE_STATE.md`, пульс:
последний ход диалога 10.09 18:01), каталог — `surface_state` из `dev-web-1` 14:18 UTC.
Выложенный SHA бота — 005260b3 (deploy-dev 34607527799, 14:05 UTC).

**Правило свежести:** числа поверхности — 14:06/14:18 UTC сегодня. Что не перемерено сегодня —
названо в §29 с датой прежнего замера, и в вердикт входит как «по коду», а не «по данным».

---

## 2. Pilot definition

Не изменилось: настоящая Ayla на ограниченном production-like контуре, реальные люди, MAX,
каталог, мастера, расписание, Booking; ограниченный Product Brain. Уточнение из свода 11.09:
**один настоящий салон** (`formula-tela`, г. Пенза, ул. Пушкина, д. 45 — §10) плюс
соло-мастера; **3–5 человек**. §133: не урезаем основную продуктовую ценность; §138: ИСПОЛНЕНО =
построено, включено **и доступно человеку**.

---

## 3. Executive verdict

См. блок выше. Кратко: **NO_GO сохраняется**, но состав причин изменился — из семи STOP закрыт
один (B-1) и наполовину два (B-5, B-6); четыре не изменились ни строкой (B-2 на каталоге, B-4,
B-7, откат в B-5); B-3 построен, но не подключён.

---

## 4. Current pilot scope

Пилотный tenant — `formula-tela`. Доказательство 09.09 (единственный в
`EVENT_INGEST_ALLOWED_TENANTS` и `BOOKING_NO_PREPAYMENT_TENANTS`) не перемерялось — окружение
пилота не читалось.

```
контур целиком (14:18 UTC): 11 тенантов · 265 SalonService · 31 мастер · 63 строки часов у 9 мастеров
                            18 записей (17 за 30 дней) · 6 профилей питания · 3 активные цели
бот (14:06 UTC):            12 тенантов (соло 0) · 26 BotUser в 5 тенантах · 34 карточки (linked 2)
трафик:                     последний ход диалога 10.09 18:01 UTC — 20 часов тишины
```

Срез `formula-tela` (58 услуг / 95 единиц / 4 мастера — 09.09) сегодня не перемерен: запрошен
у главного окна, см. §29.

---

## 5. Pilot scenario readiness P1–P10

| # | сценарий | 09.09 | 11.09 | что изменилось — доказательство |
|---|---|---|---|---|
| P1 | прямая услуга → бронь | PARTIAL | **PARTIAL ↑** | гейт здоровья читается в единственной точке истины (`create_booking_service.py:242`); `payment_required` — сервер (#305); адрес визита виден клиенту (#1515, #1567). Осталось: цена/длительность применяются молча (`price_quoted` не сравнивается — grep 0 в `appointments/`), перенос не читает гейт (`check_health_screening` только в create) |
| P2 | discovery → рекомендация → бронь | MISSING | **MISSING** | `verified = 0` (14:18 UTC); транзит ходит на границу резолвера (#1529), но кандидатов резолвер не отдаёт; DecisionReadiness не на живом пути |
| P3 | durable Goal | PARTIAL (+дефект continuity) | **PARTIAL** | конверт снят на границе (#1506, `goals_client.py:327`) — бот видит цель; четыре состояния цели (#345, `goals/models.py:24`); `GoalCandidate` и подтверждение по-прежнему нет |
| P4 | Plan Lite к событию | MISSING (осознанно) | **MISSING (осознанно)** | только постановка (#1590, §150 — пять решений у владельца); писателей 0 |
| P5 | Safety CLARIFY | MISSING | **BUILT_NOT_WIRED** | `SafetyState.CLARIFY` производится маппингом из `pre_check` (`assessment.py:67`) и обрабатывается движком (`engine.py:343`), но `assess()` никто не зовёт; приоритетного safety-вопроса на живом пути нет |
| P6 | Safety STOP | PARTIAL | **PARTIAL ↑** | на пути брони — гейт здоровья закрыт (см. P1); `STOP` от кризиса и от политики различимы (`handoff`, §127, #1558); «лекарство ≠ STOP» (#1613). Не на живом пути как `SafetyResult` |
| P7 | нет VERIFIED-кандидата | EXISTS | **EXISTS ↑** | + аудит-событие `recommendation.boundary.no_verified_candidates` (`miniapp_api/views.py:2611-2635`); + `NO_VERIFIED_CANDIDATES` через границу резолвера (#1529) |
| P8 | протухшая транзакция | PARTIAL | **PARTIAL** | не изменилось: время перепроверяется, цена/длительность/активность — нет (grep `price_quoted` в `appointments/` пуст) |
| P9 | free text против тапа | PARTIAL | **PARTIAL** | уточняющий вопрос — формулировка владельца (#1418); «Не знаю» → `"other"` и отклонение текстовой метки не перемерялись — считаю не изменившимися |
| P10 | повтор отвеченного вопроса | MISSING | **BUILT_NOT_WIRED** | `question_id`, `ledger.py`, `ledger_store.py`, `resume.py` (E9: ResumeSummary после 2 ч паузы, #1546) существуют в `apps/orchestrator/decision_readiness/`; вызывающих из живого пути **0** |

Новая градация **BUILT_NOT_WIRED** введена здесь намеренно: она отличается и от MISSING (кода
нет), и от ЗАПЕРТО §138 (есть рубильник). Здесь нет ни рубильника, ни вызова — только модуль и
его тесты. Для правила владельца «доступно человеку» это MISSING.

---

## 6. STOP blockers

| # | блокер | 09.09 | 11.09 | доказательство изменения / неизменности | экспозиция сегодня |
|---|---|---|---|---|---|
| **B-1** | гейт здоровья на путях брони | STOP | **ЗАКРЫТ** | `services/models.py:927` `-> bool \| None`; `_booking_guards.py:49,100,104` (`health_check_answerable`, fail-closed на `None`); вызов из `create_booking_service.py:242-244` — единственной точки `Appointment.objects.create`; бот #1528 (Mini App, диалог, салонная админка — 422 → передача); Mini App читает код 422 (`miniapp_api/views.py:1010`). Остаток: перенос не проходит `check_health_screening` (услуга при переносе не меняется — риск низкий, названо) | 0 услуг с гейтом на пилоте — ноль **контролем** |
| **B-2** | общий токен без проверки владения | STOP | **STOP** | каталог: `has_object_permission` grep 0 на `98d4fada`; #318 открыт. Бот: субъект называется (#1535) | как 09.09: у предъявителя токена |
| **B-3** | producer `SafetyResult` | STOP | **STOP (построен, не подключён)** | `assessment.py:175 assess()` — 0 вызывающих вне тестов; `record.py:82 record_verdict()` — 0; живой путь `channels/max/handler.py:197` импортирует только `safety.gate`; `CAUTION` — 0 правил по §126 (`assessment.py:85`) | постоянная — на рекомендательных поверхностях; на брони закрыта B-1 |
| **B-4** | три авторитета ранжирования | STOP | **STOP** | `test_recommendation_boundary_guard.py:48,51` — `discovery.py`, `handoff.py` в аллоулисте; `ai/application/services/recommendation_engine.py` жив, импортёры: `specialist_context_builder.py`, `users/home_api.py`, `check_distance_readiness.py`; транзит переведён (#1529) — это четвёртая поверхность, не снятие трёх | при `verified = 0` полка пуста; AI-чат каталога смонтирован |
| **B-5** | откат и воспроизводимость | STOP | **STOP (½)** | снимок базы: `deploy-dev.yml:96` «Postgres backup before deploy» (#1510) — **есть**; SHA: `:292` `git pull --ff-only origin dev` — **как было**; `IMAGE_TAG`/`image:` — как было; `docker-compose.staging.local.yml` в дереве нет; каталог — бэкапа на пути выкладки нет | постоянная |
| **B-6** | транзакционная истина | STOP | **DEGRADED-кандидат (OD-PILOT-7)** | `payment_required` — сервер (`create_booking_service.py:61-68`, #305); `internal_api.py:96` `or str(uuid4())` — как было; сравнения `price_quoted` нет | частичная: предоплата на пилоте отключена аллоулистом |
| **B-7** | наблюдаемость инцидента | STOP | **STOP** | `config/settings/base.py` `CELERY_BEAT_SCHEDULE`: `aggregate_ai_metrics_daily` / диспетчер — grep 0; `apps/integrations/ayla/` `X-Request-ID` — grep 0; `recommendation_presented`/`stale_callback`/`safety_result` — не пишутся; есть только `no_verified_candidates` (Mini App) и `booking_completed` в ящик (`bookings/recheck.py:86`) | постоянная |

**Новое с 11.09, не блокер 09.09, но экспозиция живая:** `NUTRITION_ENABLED = true` на пилоте
(снимок 14:06) при 6 профилях, все с `targets_source = unknown_legacy`, и границе согласия,
слитой только сегодня (#324, #1602, #1587). Это не bypass брони и под правило «один bypass —
STOP» не подпадает; подпадает под 152-ФЗ. Классификация — у владельца (OD-PILOT-8).

---

## 7. DEGRADED capabilities

Как 09.09 (11 позиций) **плюс** B-6 остаток. Значения рубильников — из снимка 14:06/14:18 UTC:
Plan Lite (писателей 0), `NUTRITION_PROACTIVE_ENABLED` ЗАПЕРТО, `WELLNESS_PROACTIVE_ENABLED`
ЗАПЕРТО, `POST_VISIT_FOLLOWUP_ENABLED` ЗАПЕРТО, `FOOD_PHOTO_SCAN_ENABLED` ЗАПЕРТО,
ResumeSummary (построен в DRE, не подключён), Advanced WHY (данными), альтернативы рекомендации
(нет), `GOAL_RESOLUTION_ENABLED` **открыт** (каталог), Discovery clarify (порог не перемерялся),
AI-drafts (не перемерялся), shadow-оркестратор (`ORCHESTRATOR_SHADOW_ENABLED`, `base.py:2366`;
`SHADOW_GROUND_TRUTH_PATH` пусто, `:1997`).

Отличие от 09.09: `NUTRITION_ENABLED` и `NUTRITION_COACH_ENABLED` на пилоте **открыты** — питание
не в DEGRADED, а в живой экспозиции (§6, последний абзац).

---

## 8. POST-PILOT scope

Не изменился (матрица §8). Добавлено сводом §15: перерождение семи старых эпиков, §13 наблюдения
тела до Privacy/Legal review, §2 ClientProfile/SalonCustomer.

---

## 9. Capability readiness matrix

Изменившиеся строки — с доказательством; неизменившиеся — «как 09.09» с тем, чем проверено.

| # | capability | 09.09 STATE / IMPACT | 11.09 STATE / IMPACT | доказательство |
|---|---|---|---|---|
| 1 | Identity / Auth | PARTIAL / STOP | **PARTIAL / STOP** | + соло-мастер S1/S2 (#1573, #1585), ключ прокси не уезжает (#1568), мастера нельзя отобрать (#1565); STOP держит B-2 (#318 не слит) и «аноним = отказ транспорта» (DRF-1319, замер #1539) |
| 2 | MAX conversation entry | PARTIAL / DEGRADED | как 09.09 | не перемерялось |
| 3 | ConversationState | PARTIAL / DEGRADED | **PARTIAL ↑** | `decision_readiness/state.py` + вердикт безопасности едет в ConversationState (#1570, A-2) — построено; на живом пути не пишется (0 вызывающих `record_verdict`) |
| 4 | ResumeSummary | MISSING | **BUILT_NOT_WIRED** | `decision_readiness/resume.py` (#1546 E9) |
| 5 | SemanticUserEvent | PARTIAL / DEGRADED | **PARTIAL ↑** | `decision_readiness/events.py` (#1533 E1); живой путь не подключён |
| 6 | Quick Replies / free text | CONTRADICTS_CANON | как 09.09 | #1418 — только формулировка вопроса |
| 7 | DecisionReadiness | MISSING / STOP | **BUILT_NOT_WIRED / STOP** | `apps/orchestrator/decision_readiness/` 20 модулей; вызывающих из `channels`/`concierge`/`skills` — 0 (grep); `tool_choice="required"` в `concierge.py:428` |
| 8 | Question Resolver / ledger | MISSING / STOP | **BUILT_NOT_WIRED / STOP** | `ledger.py`, `ledger_store.py`, `questions.py`, `question_id` — есть; см. 7 |
| 9 | Safety Architecture v1 | CONTRADICTS_CANON / STOP | **PARTIAL / STOP** | шесть состояний §3 (`safety_input.py:43-61`), приоритет корзин (#1614), NOT_APPLICABLE по capability (#1611), сторож «умолчание вместо вердикта» (#1610), `handoff` (#1558); производитель не подключён (B-3) |
| 10 | Goal | EXISTS / DEGRADED | как 09.09 | — |
| 11 | GoalCandidate | MISSING / POST_PILOT | как 09.09 | grep `GoalCandidate` в `goals/` — только докстринги (не перемерялось детально) |
| 12 | Goal lifecycle | MISSING / DEGRADED | **EXISTS / DEGRADED** | #345: `PAUSED/ACHIEVED/ARCHIVED` (`goals/models.py:24-26`), пауза/снять без выбора новой |
| 13 | Goal relevance / continuity | **DEFECT** | **EXISTS** | #1506: `goals_client.py:327 document = body["data"]` — конверт снят на границе |
| 14 | Recommendation Resolver | EXISTS / DEGRADED | **EXISTS ↑** | граница потребителя — #1529 (транзит + consumer-driven contract); порядок карточек с происхождением (#323); фикция доступности снята (#326) |
| 15 | CanonicalService | MISSING / STOP | **PARTIAL / STOP** | #313 (жизненный цикл справочника), #312 (синонимы); эпик DRF-1622 (50 SP) не начат; канонический код по-прежнему не идентичность |
| 16 | TenantOffer mapping | PARTIAL / STOP | **PARTIAL ↑ / STOP** | провенанс связи, `not_recommendable` (#311, #314), админка статуса (#309); `verified` на пилоте 0 — STOP держат данные и решение владельца |
| 17 | Capability mapping | MISSING / POST_PILOT | как 09.09 | — |
| 18 | Grounded WHY | PARTIAL / DEGRADED | как 09.09 | #323 — происхождение порядка, не WHY |
| 19 | Recommendation lifecycle / `recommendation_id` | MISSING / STOP | **PARTIAL / STOP** | `decision_id` в контракте резолвера (`recommendation/_serializers.py:188`, DRF-1628 Done); **не сохраняется** ни одной моделью (grep по `recommendation`/`ai`/`users` без совпадений в моделях) |
| 20 | PendingBookingIntent | CONTRADICTS_CANON | как 09.09 | не перемерялось |
| 21 | Booking | PARTIAL / STOP | **PARTIAL / STOP** | + гейт здоровья, + `payment_required` сервер; STOP держат B-6 остаток и B-2 |
| 22 | Availability | PARTIAL / DEGRADED | **PARTIAL ↑** | обед не показывается свободным (#1566, DRF-1638); контракт доступности v1.0 — документ (#1549); авторитет по-прежнему не один (DRF-1637) |
| 23 | Cancellation | PARTIAL / DEGRADED | как 09.09 | — |
| 24 | Reschedule | CONTRADICTS_CANON | как 09.09 | лимит 3 / lineage — не найдены (не перемерялось детально) |
| 25 | Stale callbacks | PARTIAL | как 09.09 | `stale_callback` событие — нет |
| 26 | Provider unavailable | PARTIAL | **PARTIAL ↑** | у отсутствия мастера появляется автор (§142, #340); salon_block «В сейчас» (#1594) |
| 27 | Offer unavailable | PARTIAL | как 09.09 | — |
| 28 | Memory | EXISTS | как 09.09 | — |
| 29 | Memory promotion boundary | EXISTS / сторожа нет | как 09.09 | сторож не найден (не перемерялось детально) |
| 30 | Analytics / attribution | MISSING / STOP | **MISSING / STOP** | `recommendation_id` не сохраняется; `recommendation_presented` не пишется; см. B-7 |
| 31 | Observability | PARTIAL / STOP | **PARTIAL / STOP** | + `surface_state` в обоих репозиториях и в выкладке (#1591, #1609, #337); + junit/шарды CI (#1544, #1560); B-7 — как было |
| 32 | Feature flags / kill switches | PARTIAL / DEGRADED | **PARTIAL ↑** | все рубильники печатаются с источником (`surface_state`, §138 ЗАПЕРТО/открыт); рубильника `RECOMMENDATIONS` по-прежнему нет; `BOOKING_VIA_AYLA_REST` на пилоте True (снимок) |
| 33 | Food diary | EXISTS / DEGRADED | **EXISTS ↑** | дневник без анкеты, `NOT_CONFIGURED` (#1686, #1606); ориентир доезжает отсутствием (#1499, #1583) |
| 34 | Nutrition screening | **DEFECT** / DEGRADED | **PARTIAL / живая экспозиция** | согласия: #1602 (типы, миграция), #1587 (утверждение в POST), #324 (граница не принимает без основания), #327/#331 (стирание собранного без согласия); анкета стоп-сценарии — не перемерялись; на пилоте 6 профилей `unknown_legacy` при `NUTRITION_ENABLED = true` |
| 35 | Nutrition proactive | EXISTS (выключен) | как 09.09 | ЗАПЕРТО (снимок) |
| 36 | Plan Lite | EXISTS (заперт) / POST_PILOT | как 09.09 | постановка #1590; §150 у владельца |
| 37 | Planning Rules plumbing | PARTIAL / POST_PILOT | как 09.09 | — |
| 38 | Outbound guards | PARTIAL | **PARTIAL ↑** | непроверенный текст не уходит при упавшей проверке (#1541); `safety/outbound.py` |
| 39 | Cross-surface consistency | CONTRADICTS_CANON / STOP | **PARTIAL / STOP** | медицинская передача одинакова на трёх поверхностях (#1528); адрес трёхзначен на всех экранах (#1515, #1567, #1518); STOP держит B-4 (три авторитета) |
| 40 | Pilot catalog subset | PARTIAL / STOP | **PARTIAL / STOP** | `verified = 0` (14:18 UTC); срез formula-tela не перемерен (§29) |
| 41 | Test suite / golden E2E | PARTIAL / STOP | **PARTIAL / STOP** | изоляция от вендора LLM (#1545, #1551 — зелень от соседства снята), junit (#1544), шарды (#1560), сентинел пустого прогона в каталоге (#352); CROSS_BOUNDARY по-прежнему 0 (`tests/` без cross-boundary файлов) |
| 42 | Deployment / rollback | PARTIAL / STOP | **PARTIAL ↑ / STOP** | бэкап (#1510), владелец дерева (#1574…#1596), `surface_state` в выкладке (#1609), compose от владельца в каталоге (#354); отката и SHA-привязки нет |

Счёт по IMPACT: STOP-строк **14** (09.09: 14 — те же номера, B-1 держал 21/39 вместе с B-2/B-4;
ни одна строка не вышла из STOP целиком, потому что каждую держит ещё один блокер).
DEFECT-строк: 2 → **0**.

---

## 10. Safety gate

**Цепочка канона теперь существует наполовину:** `pre_check → assess() → SafetyAssessment →
to_readiness_input() → SafetyResult → DecisionReadiness engine → ASK/RECOMMEND/BLOCK` — все
звенья есть как код (`apps/orchestrator/safety/assessment.py`, `decision_readiness/engine.py`),
**и ни одно не вызывается с живого пути**: `handler.py:197` импортирует только `safety.gate`
(regex `pre_check` + `guard_outbound`).

**Шесть состояний §3:** `NORMAL, CLARIFY, CAUTION, STOP, UNKNOWN, NOT_APPLICABLE`
(`safety_input.py:50-61`). `CAUTION` — 0 правил по §126 (`assessment.py:85`, названо в коде).
`CLARIFY` производится маппингом из `pre_check` (`assessment.py:67`). `NOT_APPLICABLE` —
шестое состояние, по capability (#1611).

**Восемь поверхностей — ответ на «requires_health_check=true без SafetyResult»:**

| поверхность | 09.09 | 11.09 | чем |
|---|---|---|---|
| Рекомендация — полки Mini App | закрывается механизмом | закрывается механизмом | `_stages.py:241` `_is_safety_sensitive` (any) |
| Рекомендация — выдача MAX-бота | **пропускает** | **пропускает** | safety на `discovery.py` нет; producer не подключён |
| Рекомендация — AI-чат каталога | **пропускает** | **пропускает** | движок жив, вердикта не читает |
| Прямая бронь | **пропускает** | **закрыта** | `create_booking_service.py:242` |
| Бронь из чата MAX-бота | условно (флаг) | **закрыта** | тот же путь через REST (`BOOKING_VIA_AYLA_REST=true` на пилоте) + #1528 |
| Бронь из AI-чата каталога | **пропускает** | **закрыта** | `action_service.py` → тот же `CreateBookingService` |
| Бронь из Mini App | **пропускает** | **закрыта** | `miniapp_api/views.py:1010` читает 422 |
| Бронь мастером | **пропускает** | **закрыта** | `tenants/appointments_api.py` → тот же сервис; салонная админка бота #1528 |

**Шов `template=None → False` закрыт:** `resolved_requires_health_check() -> bool | None`
(`services/models.py:927`, «`None` means unknown, not `False`»), гейт fail-closed на `None`
(`_booking_guards.py:104`). Числа «96 из 387 единиц с принудительным False» сегодня **не
перемерены** — после #307 они должны стать `None`; запрошено (§29).

**Пилотный салон: 0 услуг с гейтом** (09.09; сегодня не перемерено). Читать как «безопасно»
по-прежнему нельзя — но теперь ноль защищён механизмом: первая же услуга с гейтом упрётся в
`check_health_screening` на любом входе.

---

## 11. DecisionReadiness gate

**Изменилось:** пять канонических состояний, `question_id`, ledger, resume после 2 ч,
теневой режим — **существуют** (`apps/orchestrator/decision_readiness/`, DRF-1629 Done 49 SP,
#1533, #1536, #1537, #1546, #1548, #1570, #1611). **Не изменилось для человека:** authority на
живом пути — LLM `tool_choice="required"` (`concierge.py:428`); из `apps/channels`,
`concierge.py`, `discovery.py`, `skills/` DRE не импортируется (grep 0). TTL 2h inactivity —
`resume.py` (E9) построен; TTL 30 мин memo на живом пути — не перемерялось.

Поправка 09.09 про `DISCOVERY_CLARIFY_MIN_TIER = 4` — не перемерялась, считаю в силе.

Вердикт по правилу владельца §10 — STOP для пути рекомендаций **сохраняется**: право решать
пока у модели.

---

## 12. Goal gate

**Изменилось:** конверт `{"data": …}` снимается на границе (#1506) — три живых человека с целью
теперь видны дашборду, карточке, коучу, сверке. Четыре состояния `PAUSED/ACHIEVED/ARCHIVED`
и вход «пауза / снять без выбора новой» (#345). «Шаги на сегодня» вместо «Цели сегодня» (#1605,
§5.2). **Не изменилось:** `GoalCandidate` нет, подтверждения нет (клик = запись), разговорного
входа нет. На пилоте: активных 3, закрытых 33, людей с целью 3 (14:18 UTC).

---

## 13. Recommendation gate

Гейт `recommendation_eligible = (mapping_status == VERIFIED)` — как 09.09, второй ветки нет.
`verified = 0` → `NO_VERIFIED_CANDIDATES` — корректный fail-closed, теперь **через границу
резолвера с валидацией формы** (#1529) и с аудит-событием на стороне бота
(`miniapp_api/views.py:2611`).

**Авторитетов три — не изменилось.** Реестр авторитетов и сторож границы (#320, #1630 «LLM не
получает неавторизованный ranked set», #321 «промпт получает состояние») сузили четвёртого
кандидата (LLM), но `discovery.py`, `handoff.py` и `recommendation_engine.py` живы и названы в
аллоулисте по номеру T12.

`decision_id` — в контракте (`_serializers.py:188`), не сохраняется. `recommendation_id` — нет.

---

## 14. Mapping gate

```
SalonService 265 (14:18 UTC): review_required 206 | unmapped 59 | verified 0 | not_recommendable 0
```

**Число не изменилось строка в строку с 09.09** — дрейфа нет, разметки нет. Что изменилось в
механизме: провенанс связи обязателен для `VERIFIED` и `NOT_RECOMMENDABLE` (check constraints
`salonservice_verified_requires_provenance`, `…not_recommendable_requires_provenance`,
`…rule_confirmation_carries_version` — проверено фикстурой DRF-1661: без происхождения строка не
вставляется), подтверждение оставляет след синонимом (#314), отказ отличим от отсутствия
(#311), статус виден в админке (#309). Писатель `VERIFIED` в production-коде — админка
(`services/admin.py:192,335`), то есть **человек через форму**, роли и очереди нет (§145 —
оператор канона у владельца). `CanonicalService` — жизненный цикл справочника есть (#313),
канонический код как идентичность — нет (DRF-1622).

---

## 15. Booking gate

Одна точка истины, восемь входов — как 09.09; **гейт здоровья теперь стоит в точке истины**, и
поэтому покрывает все входы разом. `payment_required` — сервер (`PAYMENT_REQUIRED_REFUSED`,
`create_booking_service.py:68`). **Не изменилось:** идемпотентность снимается умолчанием
`str(uuid4())` (`internal_api.py:96`); цена/длительность/активность не ревалидируются;
перенос без лимита/lineage; `PendingBookingIntent` канона нет. Двойной `POST` — ждёт OD-PILOT-6.

---

## 16. Memory / continuity

Не перемерялось: топология call-site и отсутствие сторожа — считаю как 09.09.

---

## 17. Nutrition

**Изменилось много, и в обе стороны.** Граница согласия построена (#1602 типы и предикаты,
#1587 утверждение в POST, #324 граница каталога не принимает без основания), собранное без
согласия стирается командой (#327, пол — #331), фото удаляется по сроку 30 суток (#334),
ориентиры без происхождения очищаются (#332), дневник без анкеты (#1686). **На пилоте при этом
`NUTRITION_ENABLED = true`, `NUTRITION_COACH_ENABLED = true`, 6 профилей — все
`targets_source = unknown_legacy`, 9 записей еды у 2 людей** (14:06/14:18 UTC). Стоп-сценарии
анкеты (беременность, РПП, возраст) — не перемерялись; текст согласия — DRF-1698 у владельца.
Шов «открытый гейт анкеты — Safety по существу» — теперь закрыт границей #324, но по-прежнему
живёт в окне питания, не в safety.

---

## 18. Plan Lite

Как 09.09: писателей 0, гейты безусловны. Добавилась постановка (#1590: 16 SP, 10 «путь
виден») и пять решений §150 у владельца. `WELLNESS_PROACTIVE_ENABLED` ЗАПЕРТО (снимок).

---

## 19. Analytics / observability

**По коду — не изменилось ни в одном пункте B-7** (§6). Живые числа (`AIRequestMetric` 266,
`AIDailyMetricSummary` 0, `ReplayTrace` 0, ящик 53/0, `AnalyticsEvent` 37) — **не перемерены**,
запрошены (§29); по коду они не могли измениться в сторону улучшения — задач в расписании нет.

Новое: состояние поверхности снимается командой и публикуется выкладкой (#1591, #1609, #337)
— это наблюдаемость **данных**, не инцидента; в шапке — пульс контура (последний ход диалога
20 ч назад).

---

## 20. Feature flags / kill switches

Рубильников `RECOMMENDATIONS`, `PLAN_LITE`, `PROACTIVE_HINTS` — **по-прежнему нет** (grep
`^RECOMMENDATION.*_ENABLED` в `base.py` = 0). Правило «флаг не должен превращать safety в
небезопасный фолбэк» — `BOOKING_VIA_AYLA_REST`: при `ON` (пилот) путь идёт через REST и гейт
каталога; ветка `OFF` не перемерялась. Новое: все рубильники печатаются с именем, значением и
источником (`surface_state`, правило 46).

---

## 21. Golden E2E coverage

CROSS-BOUNDARY — **0** (в `tests/` нет cross-boundary файлов; `NetworkTripwire` не перемерялся).
Изменилось в конвейере: изоляция от вендора LLM (#1545 «двадцать узлов до, ноль после», #1551),
junit из полного прогона (#1544), пять шардов (#1560), схлопывание очереди (#1612), сентинел на
пустой прогон в каталоге (#352). Это доказательства **счёта**, не контура: треть golden
по-прежнему не утверждает ничего на стыке с Ayla (09.09, не перемерялось).

---

## 22. Deployment / rollback

| | 09.09 | 11.09 |
|---|---|---|
| бэкап на пути выкладки (бот) | нет | **есть** — `deploy-dev.yml:96`, с ротацией (#1510) |
| бэкап на пути выкладки (каталог) | нет | нет |
| выкладка по проверенному SHA | нет (`git pull --ff-only origin dev`) | **нет** — `:292` |
| теги образов | нет | нет |
| машина воспроизводится из репозитория | нет (`staging.local.yml` только на хосте) | нет |
| владелец дерева выкладки | root + taximeter + uid 1001 | **один** — владелец дерева, все каналы (#1574, #1581, #1586, #1588, #1596; каталог #354) |
| актор шага в логе | нет | **есть** — `id -u` числом в каждом ssh-шаге |
| состояние поверхности после выкладки | нет | **есть** — `docs/generated/SURFACE_STATE.md`, uid 1000 (run 34607527799) |
| откат статики Mini App | одно поколение | как было |
| защита `dev` | 0 аппрувов | не перемерялось |

Вердикт B-5: STOP сохраняется (OD-PILOT-4) — откат по-прежнему не существует; но выкладка
перестала портить дерево, и перед ней есть снимок базы.

**22-бис.** Отметок времени у `SpecialistWorkingHours` — не перемерялось; строки без
`created_at` в модели каталога на `98d4fada` — как 09.09 (модель не менялась в слитых PR по
заголовкам). Подтверждение расписания за флагом (#1502) — `MASTER_SCHEDULE_CONFIRMATION_REQUIRED`
ЗАПЕРТО на пилоте (снимок).

---

## 23. Data / privacy / legal dependencies

Изменилось: поверхность стирания шире одной модели — параметры тела без согласия стираются
(#327, #331), фото по сроку (#334), `reset_test_account` половинами (#346 слит; бот #1597 в
очереди), `AIRequestMetric SET_NULL` (#1608 в очереди), пути стирания замерены (#1569).
Не изменилось: `DeletionRequest` §7 — нет в обоих репозиториях (grep 0); экспорт/удаление
асимметричны (не перемерялось); реестр ПДн DRF-156, политика DRF-159 — у владельца; инцидент
DRF-1269 (ПДн в публичной истории git) — мер в слитых PR нет; PAT в `.git/config` — #353 открыт.

---

## 24. Pilot catalog subset

Срез `formula-tela` сегодня **не перемерен** (§29). По контуру: `verified = 0` → **PILOT
RECOMMENDATION SUBSET = 0**, как 09.09. Путь к первому `VERIFIED` теперь оформлен механизмом
(провенанс обязателен, синоним, отказ) — не хватает человека с ролью (§145) и решения владельца.

---

## 25. Pilot metrics

Все восемь метрик — **по-прежнему неисчислимы**; изменилась одна причина:

| метрика | 09.09 | 11.09 |
|---|---|---|
| No Verified Candidate Rate | код есть, события нет | **событие есть** на пути Mini App (`recommendation.boundary.no_verified_candidates`); на пути MAX-бота — нет |
| остальные семь | как 09.09 | как 09.09 (`recommendation_presented`, `recommendation_id`, `question_id`-события, `safety_result`, `booking_completed` в ящике без диспетчера, `stale_callback`) |

---

## 26. Rollout readiness

Как 09.09. Плюс: нагрузки нет — последний ход диалога 20 ч назад; калибровать пороги
по-прежнему не на чем. Пороги — `OWNER_THRESHOLD_REQUIRED`.

---

## 27. Open owner decisions

| решение | 09.09 | 11.09 |
|---|---|---|
| OD-PILOT-1 — B-1 нули: STOP или DEGRADED | открыт | **снят обстоятельствами**: B-1 закрыт механизмом, вопрос про экспозицию потерял предмет |
| OD-PILOT-2 — каноническая услуга: код или пара | открыт | открыт; эпик владельца DRF-1622 |
| OD-PILOT-3 — кто ставит `VERIFIED` | открыт | **частично отвечен §1 п.7 и §145** (провенанс: автор или правило; оператор канона — владелец или назначенный); механизм слит; человек не назначен |
| OD-PILOT-4 — пилот без отката | открыт | открыт; бэкап появился, откат — нет |
| OD-PILOT-5 — принимать `booking.completed`/`master.schedule.updated` | открыт | открыт (не перемерялось) |
| OD-PILOT-6 — одна записывающая проверка | открыт | открыт |
| **OD-PILOT-7 (новый)** — B-6 остаток: STOP или DEGRADED при отключённой предоплате | — | открыт |
| **OD-PILOT-8 (новый)** — питание включено на пилоте при 6 профилях без происхождения и согласиях, слитых сегодня: оставить включённым до первой выкладки границы или выключить `NUTRITION_ENABLED` | — | открыт |
| **OD-PILOT-9 (новый)** — пилот с полкой рекомендаций (P2) или без (P1/P7 только): от этого зависит, держат ли B-3/B-4 STOP | — | открыт (см. `PILOT_FULL_ESTIMATE_2026-09-11.md` §5) |

---

## 28. Non-owner implementation defects

Из четырнадцати 09.09:

| # | дефект | 11.09 |
|---|---|---|
| 1 | конверт ответа — четыре читателя бота | **закрыт** #1506 |
| 2 | `resolved_requires_health_check`: `None → False` | **закрыт** #307 (`services/models.py:927`) |
| 3 | `mapping_status` не пересекает границу | не перемерялось; в зеркале бота поля по-прежнему нет (по заголовкам PR) |
| 4–7 | «Не знаю» → other; текстовая метка; clarify stale tap; step-сегмент | не перемерялись — считаю открытыми |
| 8 | `"slot" in exc.code` | не перемерялось |
| 9–10 | `WebhookJournal.processed_at`; идемпотентность приёма | не перемерялись |
| 11 | FAQ few-shot с выдуманными часами | не перемерялось |
| 12 | `aggregate_ai_metrics_daily` не в расписании | **открыт** (grep 0) |
| 13 | диспетчер ящика не в расписании | **открыт** (grep 0; #1513 — сторож, не диспетчер) |
| 14 | `nutrition_coach` режет `goal_text` до 200 | не перемерялось |

Новые, найденные сегодня замером DRF-1661: `events.Event` пишется при каждом старте процесса
(`worker.subscriber_audit`) — опора пульса, которую наполняет сам измеритель (вычтена в
`measurement_subject.py`); `GOAL_RESOLUTION_ENABLED` живёт в каталоге, не в боте — `docs/PILOT_MEASUREMENTS.md:551`
называл путь `settings/base.py:502-505` без репозитория, и главное окно передало флаг как
бот-рубильник; в боте setting отсутствует (grep 0).

---

## 29. Unknown / not measured

**Осталось пять с 09.09** (двойной POST — OD-PILOT-6; `.env.staging`; 24 journey глазами клиента;
AI-чат каталога под трафиком; Google Doc KB) **плюс шесть не перемеренных сегодня**, запрошенных
у главного окна 14:45 UTC (ответ на момент письма не получен):

1. срез `formula-tela`: услуги по `mapping_status`, активных единиц, с гейтом, `None` после #307;
2. `AIRequestMetric` / `AIDailyMetricSummary` / `ReplayTrace` — count;
3. исходящий ящик — всего / с попытками / последняя запись;
4. `AnalyticsEvent` по именам (каталог);
5. `events.Event` по именам `recommendation*`, `safety*`, `booking_completed`, `stale_callback`;
6. чекаут на хосте бота и SHA последней выкладки каталога.

Для каждого: **по коду** значение 09.09 не могло улучшиться (задачи не в расписании, событий
не пишут), поэтому вердикты §6/§19 стоят на коде; числа впишу, когда придут.

**Отдельной строкой:** CROSS-BOUNDARY = 0 — как 09.09; треть golden не утверждает ничего на
стыке — не перемерялось.

---

## 30. Exact reproduction commands

```bash
# базы
git -C ai-bot-platform rev-parse origin/dev        # eb0ea8219bddf22bf184a466748ba070fce9fc75
git -C beautygo_backend rev-parse origin/dev       # 98d4fada5331bd042fe355e6a3891472287a0e30
R_BOT=eb0ea8219bddf22bf184a466748ba070fce9fc75; R_CAT=98d4fada5331bd042fe355e6a3891472287a0e30

# B-1
git -C beautygo_backend grep -n "check_health_screening" $R_CAT -- appointments | grep -v tests
git -C beautygo_backend show $R_CAT:services/models.py | grep -n "def resolved_requires_health_check"
# B-2
git -C beautygo_backend grep -c "has_object_permission" $R_CAT -- users core          # 0
# B-3 / DRE — вызывающие вне тестов
git -C ai-bot-platform grep -ln "decision_readiness" $R_BOT -- apps | grep -v "/tests/\|decision_readiness/"
for f in assess record_verdict to_readiness_input; do git -C ai-bot-platform grep -l "\b$f\b" $R_BOT -- apps | grep -v "/tests/\|orchestrator/safety/\|decision_readiness/"; done
# B-4
git -C ai-bot-platform grep -n "discovery.py\|handoff.py" $R_BOT -- tests/contracts/test_recommendation_boundary_guard.py
git -C beautygo_backend grep -ln "RecommendationEngine()" $R_CAT -- ai users | grep -v tests
# B-5
git -C ai-bot-platform grep -n "Postgres backup before deploy\|git pull --ff-only origin dev" $R_BOT -- .github/workflows/deploy-dev.yml
# B-6
git -C beautygo_backend grep -n "str(uuid4())" $R_CAT -- appointments/internal_api.py
git -C beautygo_backend grep -n "price_quoted" $R_CAT -- appointments | grep -v tests      # пусто
# B-7
git -C ai-bot-platform grep -c "aggregate_ai_metrics_daily\|dispatch_outbox" $R_BOT -- config/settings/base.py   # 0
git -C ai-bot-platform grep -l "X-Request-ID" $R_BOT -- apps/integrations/ayla | wc -l                       # 0
# рубильники / данные
cat docs/SURFACE_STATE_SNAPSHOT_2026-09-11_1420.md
```

---

## 31. Critical path to first 3–5 users

Шесть строк 09.09 — что с каждой:

| CP | 09.09 gap | 11.09 |
|---|---|---|
| CP-1 гейт здоровья | 0 из 8 точек | **выполнен** в части брони (все входы через одну точку истины; fail-closed на `None`); событие `safety_result` и счётчик fail-closed — нет (лог `booking.health_gate.refused`, `_booking_guards.py:120`, есть) |
| CP-2 владение объектом | нет нигде | **не выполнен**: #318 открыт |
| CP-3 откат и SHA | ничего | **½**: бэкап есть; SHA и откат — нет |
| CP-4 один авторитет | три | **не выполнен**: транзит переведён, три авторитета живы (T12) |
| CP-5 наблюдаемость | ничего | **не выполнен** ни в одном пункте |
| CP-6 первый `VERIFIED` | 0, провенанса нет | **механизм готов**, `VERIFIED` 0 — ждёт человека и решения владельца |

Новые строки, которых 09.09 не было и которые теперь держат путь (из свода 11.09; SP — в
`PILOT_FULL_ESTIMATE_2026-09-11.md`): CP-7 подключение DRE и producer'а к живому пути (B-3/P5/P10
из BUILT_NOT_WIRED); CP-8 согласие в питании до конца при включённом `NUTRITION_ENABLED`
(OD-PILOT-8); CP-9 место и координаты (§9/§139: 0 из 11 тенантов, 0 из 31 мастера).

---

*Конец матрицы. STOP-условие соблюдено: дефекты не исправлялись, PR по коду не создавались,
product scope не менялся, пороги не назначались, Linear не обновлялся.*
