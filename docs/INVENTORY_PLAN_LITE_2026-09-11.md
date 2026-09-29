# Инвентарь Plan Lite (§133) — есть ли у связного пути предмет в коде — 11.09.2026

Сабагент окна ЦЕЛИ И ПЛАН по брифу `docs/BRIEF_SUB_PLAN_LITE_INVENTORY.md`. Только чтение `origin/dev` обоих репозиториев: Linear не менялся, код не писался, `ssh` не использовался, на хосты не ходил. Рабочие копии на диске не читались — все пути ниже это `git show`/`git grep origin/dev:<путь>`.

**Базы (после `git fetch origin dev`, 11.09):**

```
ai-bot-platform   origin/dev  d8a1804d4088a7cbd57b80c8958a4d03bb4872fa  13:53 MSK  (#1588)
beautygo_backend  origin/dev  bced6f3a302381dfa57b8c1aa83eb587ebc2536e  — на старте инвентаря
                             fc771674f605aa1d59b2f1711bc4a20ab3088047  14:06 MSK  — доехал во время работы (#331);
                             diff между ними: только nutrition/…/purge_unconsented_body_parameters.py + его тест —
                             ни одного файла из этого инвентаря не трогает
```

Значения флагов — только `docs/PILOT_MEASUREMENTS.md@origin/dev` бота (далее PM), замер §11.5 от 09.09.2026 16:33 UTC и §5.2 от 09.09. Категории — по §138 (ИСПОЛНЕНО / ЗАПЕРТО / ОТМЕНЕНО / ПОДЛЕЖИТ). Где §138 категории не хватает (механизм включён, а экспозиция ноль по данным), механизм и экспозиция названы двумя строками, а не одной.

## §133 — дословно (`docs/OPEN_DECISIONS.md@origin/dev`, строки 8772–8795)

> Полный Plan Engine к пилоту не обязателен. Но **продукт обязан уже показывать связный путь**:
> ```
> Goal → следующий полезный шаг → nutrition / service context
>      → Recommendation → Booking / execution
> ```
> Урезание допустимо **в глубине механизма** (Plan Lite вместо Plan Engine), но **не в наличии способности**.

Рамка сверху: §112 — «К реализации Plan не переходить, пока не закрыты Goal Domain Contract и DecisionReadiness»; `Goal → PlanEligibility → optional Plan`. §35 п.13 — на пилоте один простой экран «Моя цель» (изменён в §82-разделе: проценты разрешены при активном ориентире). §110 — без VERIFIED-кандидатов Ayla не рекомендует ничем; `SearchResult ≠ CatalogResult ≠ CandidateSet ≠ Recommendation`.

**Слов `Plan Lite`, `PlanEligibility`, `plan_engine`, `PlanEngine` в коде нет ни в одном репозитории** (`git grep -il` по `apps/`, `config/` бота и по всему каталогу без `docs/` → 0 файлов в обоих). Всё, что ниже, — звенья, построенные под другими именами.

## Звено 1 — Goal

| Источник | Вычислитель | Поверхность | Рубильник (PM) | §138 | Зависит от § |
|---|---|---|---|---|---|
| `goals.ClientGoal` (`goals/models.py:36-100`): `goal_key`/`goal_text`, `is_active`, схема `clientgoal_one_active_per_client` (:85-89). `GoalAnketaRun`/`GoalAnketaAnswer` (:103, :158). Замер PM §4.2: 33 цели, активных 3 (relax ×2, recharge ×1), с ключом 31, текстом 2 | `goals/decision_context.py::build_decision_context` (:183-277) — документ `known.goal / missing / suggestions / intents / next`; `goals/api.py::GoalSelectView.post` (:190-252) пишет цель, `_create_goal` (:124-153) гасит прежнюю; анкета `goals/anketa.py` (3 шага; PM §4.4: 2 из 3 ни на что не влияют) | **Mini App**: `GoalSelectScreen.tsx` на `/customer/goal-select` (`App.tsx:1367`), блок «Текущая цель» (:417-421); дашборд `CustomerWellnessDashboardScreen.tsx` — «Цель: {title}» (:968), CTA «Моя цель/Выбери цель» (:379-386 → :280). **Бот**: кнопка «🎯 Выбрать цель» в welcome (`skills/welcome/skill.py:999,1012`), пункт «Моя цель» меню (`skills/menu/marketplace.py:460-465`, `where="miniapp"`) — оба deep-link в Mini App, в самом боте цель не показывается. Прокси бота `miniapp_api/urls.py:125-133` | `GOAL_ANKETA_ENABLED=True` (PM §11.5, 09.09); отдельного флага на экран нет | **ИСПОЛНЕНО** (экран «Моя цель» по §35 п.13 есть: без шкал и процентов, дашборд :988-990 это оговаривает) | §97 OD-GOAL-B (4 состояния — DRF-1660 у другого окна, здесь не разбирается), OD-GOAL-E (`ACTIVE ≠ CURRENT`), §112 (один authoritative Goal domain) |

Отдельно: `ClientGoal` уже режет выдачу — см. звено 3 (§97 P0). Сама цель в бот-диалог **не доезжает**, если у неё нет `goal_text` — см. звено 3, строка «контекст ДЛЯ рекомендации».

## Звено 2 — «Следующий полезный шаг»

Прямой ответ на вопрос брифа: **ни одного вычислителя «следующего шага из цели», который доходит до человека, нет.** Четыре кандидата, каждый не доходит по своей причине:

| Кандидат | Источник | Вычислитель | Поверхность | Рубильник (PM) | §138 | Зависит от § |
|---|---|---|---|---|---|---|
| A. `next` в decision-context | `ClientGoal` | `goals/decision_context.py:257-262` — `next_step_hint = {"id": "browse_catalog", "label": "Найти услугу"}`, **константа, присутствует всегда**, от цели не зависит; комментарий :255 «GOAL_RESOLUTION_ENABLED на пилоте выключен» устарел (PM §11.5: True) | `GoalSelectScreen.tsx:116,335` → `/customer/catalog` (каталог без цели, см. звено 4) | нет | ИСПОЛНЕНО как кнопка; **как «шаг из цели» — ПОДЛЕЖИТ** (не вычисляется) | §112 |
| B. Подсказка диетолога (goal × паттерн недели) | `nutrition_coach.goals.active_goal` (бот, :62-116, читает decision-context по HTTP) + недельная картина | `nutrition_coach/triggers.py::any_trigger` (:140): пары «поздний ужин × цель сна», «завтраки × цель энергии»; словарь ключей `GOAL_KEYS_SLEEP/ENERGY` (:54-64) = `sleep_better, sleep_rested_wake, sleep_weekend_recovery, energy_more_day, energy_less_fatigue, energy_morning_vigor, more_energy` | (i) проактивно: `nutrition_proactive/coach.py::plan_coach_hints` → beat `tasks.py:452-471`; (ii) при открытии дневника: `orchestrator/coach_observation.py::decide_observation` (:177-200) → `personal_surface.py:415-445` | `NUTRITION_COACH_ENABLED=True`, `NUTRITION_COACH_DRY_RUN=True` (PM §5.2) → (i) планируется, **не отправляется**; (ii) DRY_RUN не читает (`coach_observation.py:177` только `enabled()`) | (i) **ЗАПЕРТО** (dry-run); (ii) включено, но **экспозиция 0 по данным**: живые ключи целей — `relax, body_shape, self_care, event, new_look, skin_care, recharge` (`services/seeds/goal_options_2026-08.json`, PM §4.1); пересечение с ключами триггеров **пустое** → `any_trigger` возвращает `None` для любой живой цели | Q-04/Q-NUTRITION-05 (в коде), §97 OD-GOAL-C, словарь ключей — вопрос владельцу не поставлен |
| C. Повод OBSERVE от Personal Plan | `wellness.PersonalPlan/DesiredOutcome` (каталог) через `GET /internal/me/wellness-context/` (`wellness/context_read.py`) | `wellness_proactive/tasks.py::plan_observe_occasions` (:237), семейство `OCCASION_FAMILY="OBSERVE"` (:91) | **нет**: докстринг :12-17 «Отправки в этой задаче нет … `Decision.send` не существует»; в `CELERY_BEAT_SCHEDULE` не стоит (:55) | `WELLNESS_PROACTIVE_ENABLED=False` (PM §5.2; код :128-129) | **ЗАПЕРТО** (флаг) — и при включении до человека не дойдёт структурно; плюс входа нет: writers `wellness/services.py::record_outcome/record_observation` (:110-140) всегда отказ, `PersonalPlan.objects.create` вне тестов — 0 | GOALS-R6 / Gate D — scope `goal_memory` не утверждён (`docs/OD_GOALS_RULINGS.md:256, 323`; в `OPEN_DECISIONS.md` слово `goal_memory` — 0 попаданий); §112 |
| D. Чипы после анкеты | завершение **анкеты питания**, не цель | `skills/nutrition_anketa/skill.py::_post_anketa_chips` (:559-575): `CHIP_WATER`, `CHIP_DIARY` | бот, кнопки | `NUTRITION_ENABLED=True` | ИСПОЛНЕНО — но это шаг из анкеты питания, **не из цели** | — |

Смежное, но не «шаг из цели»: уточняющий вопрос каталога `orchestrator/discovery.py:2536-2600` (`DISCOVERY_CLARIFY_MIN_TIER=4`, PM §11.5) считается от **текущей реплики**, сохранённую `ClientGoal` не читает (`git grep -i "ClientGoal\|decision.context\|active_goal"` по `apps/marketplace`, `orchestrator/discovery.py`, `concierge.py`, `pipeline.py`, `skills/booking` → 0 вне комментариев). Welcome-текст бота обещает «предложу подходящий следующий шаг» (`channels/max/global_onboarding.py:171,194`) — обещание без вычислителя за ним.

DecisionReadiness (`apps/orchestrator/decision_readiness/`, 20 модулей) — импортёров вне пакета и тестов **0** (`git grep "from apps.orchestrator.decision_readiness"` → пусто). Не ЗАПЕРТО флагом — не подключено. `docs/HANDOFF_PLAN_ENGINE.md`: «реализуем, включать нельзя». Реестр планировочных правил `apps/planning_rules/` — то же: загрузчик есть, потребителей 0.

## Звено 3 — nutrition / service context

| Что | Источник | Вычислитель | Поверхность | Рубильник (PM) | Контекст ДЛЯ рекомендации или фильтр выдачи | §138 | Зависит от § |
|---|---|---|---|---|---|---|---|
| Блок питания в промпте консьержа | дефициты недели + сегодняшние блюда (Ayla, HTTP) + активная цель | `orchestrator/nutrition_context.py::build_nutrition_context_block` (:157-200); гейт согласий PERSONAL_DATA+HEALTH (:167); `_render_goal_lines` (:245-259): **строка цели идёт в промпт только при непустом `goal.text`**, голый ключ не идёт | бот, system-prompt DM | `CONCIERGE_NUTRITION_CONTEXT_ENABLED=True` (PM §11.5) | контекст для разговора (не для резолвера) | ИСПОЛНЕНО механизм; **экспозиция цели 0 по данным**: 3 активные цели — все ключевые (PM §4.2: с ключом 31/33), текста у них нет → цель в промпт не попадает (ровно дефект §97 OD-GOAL-C «цель исчезает из разговора»). Число HEALTH-согласий на пилоте в PM не замерено | §97 OD-GOAL-C |
| `decision_context` | `ClientGoal` | `goals/decision_context.py` (см. звено 1) | Mini App экран цели; бот читает для коуча | `GOAL_ANKETA_ENABLED=True` | контекст (документ состояния), не фильтр | ИСПОЛНЕНО | — |
| `goals/service_match.py::match_named_service` | текст цели ↔ имена услуг/категорий | `decision_context._goal_is_resolved` (:141-180) | решает, нужен ли шаг `goal_clarification` | внутри `GOAL_ANKETA_ENABLED` | контекст (готовность цели), не фильтр | ИСПОЛНЕНО | — |
| `goals/resolution.py::resolve_goal_category_ids` + `goals/wiring.py::goal_category_ids_for` (:71-92), `goal_category_ids_for_key` (:95-125) | `ClientGoal.goal_key → GoalOption → GoalOptionCategory` → раскрытие в подкатегории | единственное чтение флага `wiring.py:66-68` | (а) `users/catalog_recommendations_api.py:492-498`: `goal_key` → `NeedSpec` полки 2 (резолвер), `goal_category_ids` → полка 3 `_catalog_pool` (:535); (б) `users/home_api.py:237,258` → `RecommendationQuery(goal_category_ids=…)` полки `nearby_specialists`; (в) `users/recommendation_source.py:118` — метка совпадения по цели у кандидата | `GOAL_RESOLUTION_ENABLED=True` (PM §11.5; умолчание в коде false, `settings/base.py:506-507`) | **фильтр выдачи** (а-полка 3, б) и вход резолвера (а-полка 2, в) | ИСПОЛНЕНО механизм; **экспозиция 0**: полка 3 (`layer_3_explore`) Mini App **не потребляет** (`lib/customer-booking.ts` — `layer_3` только в комментарии :24), `/api/v1/home/` вызывающего нет (§140; grep по боту → 0), полка 2 пуста по VERIFIED=0 (звено 4). Т.е. «10/6/6» из §97 режет ответ, который никто не рисует | §97 P0 («фильтрацию автоматически не отключать, сначала доказать путь»), §125 (убрать цель из «Рядом с тобой» — в `home_api.py:258` **ещё стоит**, ПОДЛЕЖИТ) |

## Звено 4 — Recommendation

Прямой ответ: **сегодня рекомендация не может быть непустой ни у кого.** Причина — данные, а не код: `recommendation_eligible = (mapping_status == VERIFIED)` (`recommendation/_stages.py:372-393`, второй ветки нет по §76), а `verified` на контуре = 0 и ни у одной из 265 строк нет следа подтверждения (PM §11.2, 09.09: `review_required 206 / unmapped 59`, `mapping_confirmed_*` = 0). Резолвер честно отдаёт `ELIG_EXCLUDED_NOT_RECOMMENDABLE` → `NO_VERIFIED_CANDIDATES` (`_pipeline.py:308-311`).

| Что | Источник | Вычислитель | Поверхность | Рубильник (PM) | §138 | Зависит от § |
|---|---|---|---|---|---|---|
| Канонический резолвер | `SalonService.mapping_status`, `SpecialistCandidateSource` (пул 31, PM §2.1; мост ключей починен 09.09, §2.3) | `recommendation/_pipeline.py`, `_stages.py` (S1 маппинг, S2 нужда, безопасность fail-closed) | Mini App: `CustomerCatalogScreen.tsx:196-201` и `CustomerWellnessDashboardScreen.tsx:696-720` — блок «✨ Ayla подобрала» рисуется **только при непустых `picks` с WHY**; путь бот → `miniapp_api/views.py::customer_recommendations` (:2614) → `POST /internal/me/catalog/recommendations/` | флага нет (PM §11.5: «ни одного флага с именем про рекомендации») | ИСПОЛНЕНО механизм / **экспозиция 0 по данным** (verified=0) | §110, §76, §93 (третий исход `NOT_RECOMMENDABLE` уже в `_types.py:108-125`) |
| Подтверждение связи (что сделает выдачу непустой) | `services/admin.py:186-230` — форма админки Ayla требует провенанс для `VERIFIED`/`NOT_RECOMMENDABLE`; `mapping_confirmed_*` в `services/models.py:694-800` | ручное решение оператора | админка Ayla | — | ИСПОЛНЕНО механизм, применён 0 раз (PM §11.2) | §93 («для 56 услуг живого салона — ручной разбор», три исхода), §61 (остаток 59 из 265; у `formula-tela` шаблонов 0 из 58, PM §1.2) |
| Бот-каталог по цели из реплики | зеркало `CatalogService.goals` | `apps/marketplace/discovery.py::_match_goal_keys` (:716-744), `_parse_query` (:746-778): «хочу расслабиться» → ключ `relax` → `_goal_row_q` (:1063,1076) | бот: карточки мастеров/услуг + уточняющие чипы | без флага | ИСПОЛНЕНО — но это **CatalogResult**, не Recommendation (§110/§125); стартует от **сказанного сейчас**, сохранённую цель не читает | §110 инвариант |
| «Рядом с тобой» (`home_api._nearby_specialists`) | `RecommendationEngine` (`ai/application/services/recommendation_engine.py`) | — | `GET /api/v1/home/` — вызывающего нет (§140) | — | по §125 должен стать честным каталогом; на `origin/dev` ещё передаёт `goal_category_ids` и `match_reasons` (:258, :270) → **ПОДЛЕЖИТ** | §125, §137 (0.5 за неизвестное расстояние — :240 комментарий ещё описывает «neutral 0.5») |

## Звено 5 — Booking / execution

| Что | Источник | Вычислитель | Поверхность | Рубильник (PM) | §138 | Зависит от § |
|---|---|---|---|---|---|---|
| Создание брони | бот `apps/booking/models.py::BookingRequest` (:82); Ayla `appointments/models.py::Appointment` (:21) | бот `apps/booking/services/create.py:315-346`; Ayla `appointments/application/services/create_booking_service.py` | бот-диалог `ai_direct` (9 строк, PM §27.2) и Mini App: каталог → `/customer/catalog/:serviceId` → мастер → `fetchSlots` (`lib/api.ts:619-633`, ручка `miniapp_api/urls.py:16`) → `POST bookings` (:22) | `BOOKING_VIA_AYLA_REST=True` (PM §11.5) | **ИСПОЛНЕНО** (18 Appointment на Ayla, PM §27.2) | §27 гейт здоровья (95 из 95 записываемых единиц пилота решены отсутствием шаблона, PM §11.3) |
| «Найти время» / контракт доступности | — | — | нет: `admin/SalonPilotScheduleScreen.tsx:63` «„Найти время для записи" здесь тоже нет … без контракта доступности», `lib/admin-api.ts:1262` | — | ПОДЛЕЖИТ | реестр §4 «Свободные окна мастера» (задан 24.08, ответа нет; в Linear это DRF-1349, не проверял) |
| **Связь «эта запись — шаг к этой цели»** | — | — | — | — | **ПОДЛЕЖИТ — нет вовсе**: `git grep -i "goal\|plan_id\|personal_plan\|desired_outcome" origin/dev -- appointments/` → **0**; `apps/booking/` (без тестов) → **0**; `apps/skills/booking/` → 1 (слово «goal» в докстринге про порт скилла). `BookingRequest.attribution_metadata` (`:381`) несёт только `actor_type/started_by/created_by/test_mode/conversation_id` (`services/attribution.py:613-646`); у `Appointment` полей происхождения нет вовсе (PM §27.3: «поля со словом source: []») | §112 (Plan после Goal Domain Contract), §97 OD-GOAL-B («завершённая бронь сама по себе не переводит цель в ACHIEVED» — т.е. и обратная связь нужна явная, не выводимая) |

## Связность — показывает ли одна поверхность путь целиком или два соседних звена

| Поверхность | Что видит человек | Соседние звенья на одной поверхности | Вывод |
|---|---|---|---|
| Mini App «Главная» (`CustomerWellnessDashboardScreen`) | «Цель: relax» (:968) → карточка «Подобрать услугу под твою цель — расскажу что подойдёт» + кнопка «Найди услугу» (:1031-1036 → `/customer/catalog`, :284) → каталог, **который цель не читает** (`getCatalogBrowse()` без аргументов, `customer-booking.ts:383`; полка 2 пуста, полка 3 не рендерится) | Goal → [обещание шага] → Catalog. Текст обещает «под твою цель», каталог отдаёт всё подряд | **Goal и Booking связаны словами, не данными**; Recommendation отсутствует (verified=0) |
| Mini App экран цели (`GoalSelectScreen`) | «Текущая цель» → «Найти услугу» (константа `next`) → тот же каталог | Goal → Catalog | то же |
| Бот-диалог | «хочу расслабиться» → каталог по ключу `relax` из реплики → чипы услуг → мастера → бронь `ai_direct` | Utterance → Catalog → Booking — **три соседних звена, но без Goal**: сохранённая цель не читается, а результат по §110 — CatalogResult, не Recommendation | связный путь есть, но начинается не с Goal и не проходит через Recommendation |
| Промпт консьержа | цель — только если есть `goal_text` (0 из 3 живых) | Goal → context: на живых данных не срабатывает | нет |
| Push / проактив | коуч dry-run, OBSERVE без отправки, словарь ключей не совпадает | — | нет |

**Итог по связности:** связного пути `Goal → шаг → context → Recommendation → Booking` ни на одной поверхности нет. Звенья 1, 3, 4 (механизм), 5 существуют **порознь**; звено 2 не существует как вычислитель; переходы между звеньями — либо константная кнопка в каталог (1→5 в обход 2–4), либо текст «под твою цель», за которым данных нет. Единственная цепочка из трёх звеньев подряд (бот: реплика → каталог → бронь) обходит Goal и Recommendation по построению. Обратной связи Booking → Goal в данных нет.

## Решения владельца, без которых звено не собирается

| § | Что решает | Какое звено запирает |
|---|---|---|
| §112 | один authoritative Goal domain (`ClientGoal` + `DesiredOutcome`), `Goal → PlanEligibility → optional Plan`; Plan — после Goal Domain Contract и DecisionReadiness | звено 2 целиком; Plan Lite как понятие в коде отсутствует (0 попаданий) |
| §97 OD-GOAL-B/C/E (+ DRF-1660 у другого окна) | 4 состояния, `user_wording` опционален, `ACTIVE ≠ CURRENT`; «цель исчезает из разговора» | звено 1 (жизненный цикл), звено 3 (цель в промпт только по тексту — `nutrition_context.py:245-259`) |
| §97 P0 | «фильтрацию автоматически не отключать; сначала доказать путь `ClientGoal → резолвер → категории`» | звено 3 (`GOAL_RESOLUTION_ENABLED=True` режет ответ, который не рендерится) |
| §110, §76 | без VERIFIED — `NO_VERIFIED_CANDIDATES`, fallback запрещён | звено 4: непустая рекомендация невозможна до подтверждения связей |
| §93 | ручной разбор 56/58 услуг пилотного салона, три исхода; подбор только по подтверждённым связям | звено 4: кто и когда подтверждает — работа оператора, не код |
| §125 | «Рядом с тобой» — честный каталог без цели | звено 4: `home_api.py:258` ещё передаёт цель — ПОДЛЕЖИТ |
| GOALS-R6 / Gate D (`OD_GOALS_RULINGS.md:256,323`) | scope `goal_memory` для persistent DesiredOutcome | звено 2C: `PersonalPlan/PlanAction/DesiredOutcome` без писателя; `goal_memory` в реестре не упоминается |
| реестр §4 (24.08, без ответа) | read-ручка availability | звено 5 «Найти время» |
| не поставлен | словарь ключей целей для триггеров коуча (`sleep_*`, `energy_*`, `more_energy`) против живого (`relax … recharge`) | звено 2B: единственный работающий вычислитель «шага из цели» не может сработать ни на одной живой цели |
| не поставлен | связь Booking ↔ Goal в данных (поле/FK/attribution-ключ) | звено 5→1: замер «Recommendation → Booking» и «бронь как шаг к цели» невозможен (PM §27.3) |

## Что не смог проверить и почему

* **Живые значения** флагов и чисел — только из PM (09.09); срок годности у них есть, 11.09 не перемерялись (хосты запрещены брифом). В частности: число HEALTH-согласий на пилоте в PM отсутствует → «не замерен», а значит доля людей, у кого блок питания вообще открыт, неизвестна.
* **`verified = 0` на 11.09** — цитирую PM §11.2 от 09.09; после него в каталоге могли подтвердить связь через админку — не проверял.
* **Что реально отдаётся Mini App на пилоте** (сборка от 09.09, PM §8): читал `origin/dev`, а не `miniapp-dev.gobeauty.site`; расхождение выкладки и `dev` (PM §10.2) возможно.
* **DRF-1349 вопрос 9** — в Linear не ходил; в `OPEN_DECISIONS.md` это §4 «Свободные окна мастера в салонном дне».
* **DRF-1660** — не разбирал по брифу, только сослался.
* `ayla-knowledge` (реестр планировочных правил, §70) не открывал — на путь до человека не влияет (потребителей `apps/planning_rules` 0).

## Убрано за собой

Временные выгрузки только в scratchpad сессии (`…/scratchpad/OD.md`, `…/scratchpad/PM.md` — копии `docs/OPEN_DECISIONS.md` и `docs/PILOT_MEASUREMENTS.md` с `origin/dev`), удалены по завершении. В репозиториях ничего не создано и не изменено; ветки, worktree, контейнеры не заводились. Единственный созданный файл — этот.
