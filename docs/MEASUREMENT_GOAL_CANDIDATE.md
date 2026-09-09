# Замер контура Goal Candidate

Режим: read-only. Код не менялся, тесты не запускались, контейнеры не поднимались,
worktree не заводился, в Linear не писалось.

Вердикт ставится **по коду рантайма**, а не по тому, что обещает контракт.
`docs/specs/GOAL_CANDIDATE_CONTRACT_v1.0.md` прочитан целиком (1351 строка) и
использован **только как то, с чем сравнивать**. Там, где мой замер расходится с
его разделом 1 («Discovery»), расхождение названо отдельно в §4.

---

## 1. Базы замера

| что | значение |
|---|---|
| Дата, время | 2026-09-09, 10:23–11:30 MSK |
| Репозиторий бота | `C:/Users/user/PycharmProjects/Ayla/ai-bot-platform`, `origin/dev` = **`e850b3f1e8d9b8ca5a10cba84d7dcabed78dc05a`** |
| Репозиторий Ayla | `C:/Users/user/PycharmProjects/Ayla/djangoproject-catalog`, `origin/dev` = **`95c917e684652476feef3ae9d790fb2c8d277378`** |
| Третий репозиторий | `C:/Users/user/PycharmProjects/Ayla/ayla-ai-core` — **ветки `origin/dev` не существует**: `git rev-parse origin/dev` → `fatal: ambiguous argument 'origin/dev'`. Есть `origin/main` и локальная `fix/memory-origin-vocabulary` @ `73b0422b01e7e684491b7d2fe83e15e3b65fc836` (2026-09-03). Замер по ai-core **не сделан** — см. §8 |
| Чем снято | `git fetch -q origin dev`, затем `git show origin/dev:<путь>`, `git grep -n <шаблон> origin/dev`, `git ls-tree -r --name-only origin/dev`. Рабочее дерево не читалось |
| Числа с пилота | взяты как есть из замера главного окна 09.09.2026, контейнер `dev-web-1`. **Не перепроверялись** |

Пилотные числа (не мои, цитата задания):
`GoalOption` 7 · `GoalOptionCategory` 20 · `ClientGoal` 33 (активных 3, с ключом 31,
со свободным текстом 2) · `GoalAnketaRun` 5 (все завершены) · `GoalAnketaAnswer` 15
(area 5 · feeling 5 · goal 5; свободным текстом — 0).

---

## 2. Вердикты по восьми узлам рантайма

Сводка: `EXISTS` — 3 · `PARTIAL` — 3 · `MISSING` — 2 · внутри них 4 отдельных
`CONTRADICTS_CANON` (перечислены в §4).

---

### Узел 1 — entry points: где вообще начинается путь цели

**`EXISTS`** — вход один, целиком Mini App; разговорного входа нет.

Единственная точка записи в контуре — `POST /api/v1/internal/me/goals/select/`.

Evidence — маршрут записи (Ayla):
* `djangoproject-catalog:djangoProject/urls.py:62-63` — `'api/v1/internal/me/goals/select/'` → `include('goals.select_urls')`;
* `djangoproject-catalog:goals/api.py:190-194` — `GoalSelectView`, `permission_classes = [IsBotServiceWithVerifiedClient]`.

Evidence — все поверхности, ведущие туда (бот):

| # | вход | путь и строка |
|---|---|---|
| E1 | кнопка бота «🎯 Выбрать цель» (web_app) | `ai-bot-platform:apps/skills/welcome/skill.py:999` |
| E2 | та же кнопка ссылкой (deep-link) | `ai-bot-platform:apps/skills/welcome/skill.py:1012` |
| E3 | слаг deep-link → маршрут | `ai-bot-platform:apps/miniapp/src/lib/max-sdk.ts:195` — `open_goal_select: "/customer/goal-select"` |
| E4 | маршрут экрана | `ai-bot-platform:apps/miniapp/src/App.tsx:1357` |
| E5 | корень клиента `/` монтирует ту же поверхность | `ai-bot-platform:apps/miniapp/src/screens/CustomerEntryScreen.tsx:101` — `return <GoalSelectScreen initialDoc={state.doc} />` |
| E6 | приглашение с домашнего экрана | `ai-bot-platform:apps/miniapp/src/components/GoalInviteCard.tsx:130` — `navigate("/customer/goal-select")` |
| E7 | блок «Моя цель» на дашборде | `ai-bot-platform:apps/miniapp/src/screens/CustomerWellnessDashboardScreen.tsx:279` |

Evidence — разговорного входа **НЕТ**:
* `cd ai-bot-platform && git grep -n "post_goal_select" origin/dev -- ':!*/tests/*' ':!docs/'` → **три хита, все внутри самого прокси**: `apps/integrations/ayla/goals_client.py:316` (определение), `apps/miniapp_api/views.py:3844,3859` (проксирование запроса Mini App). Ни одного вызова из канала, скилла или оркестратора;
* `git grep -rn "source_channel" origin/dev -- ':!docs/' ':!*/tests/*'` → в рантайм-коде значение `"bot"` не пишет никто. `ai-bot-platform:apps/miniapp/src/lib/customer-goals.ts:135-143` — тип `GoalSelectBody`, литерал `source_channel: "miniapp"` во **всех шести** вариантах тела. `"bot"` встречается только в тестовых фикстурах (`CustomerEntryScreen.test.tsx:93`, `CustomerRecordsScreen.goalEntry.test.tsx:98`);
* при этом `bot` легален в схеме: `djangoproject-catalog:goals/models.py:40-41` (`SourceChannel.BOT`). Итого по каналу `bot` — **объявлено и не используется**.

---

### Узел 2 — extraction: из фразы человека выделяется кандидат

**`PARTIAL`** — извлечение существует, работает и детерминировано; но извлекает
не «кандидата цели», а фильтр витрины на один запрос.

Это главное расхождение моего замера с §1.2/§2 контракта, который объявляет
извлечение `ABSENT`. Извлекатель **есть**, он не LLM, он живёт в боте и работает
по **тому же словарю `GoalOption`** — результат просто никуда не сохраняется.

Evidence — извлекатель:
* `ai-bot-platform:apps/marketplace/discovery.py:649-685` — `_known_goals()` строит `{goal_key: label}` из `CatalogService.goals` (поле приезжает из Ayla, DRF-1308, той же парой `{"key","label"}`, что и `GoalOption`);
* `ai-bot-platform:apps/marketplace/discovery.py:716-743` — `_match_goal_keys(tokens)`: цель квалифицируется, если **каждый** токен запроса называет слово ярлыка цели;
* `ai-bot-platform:apps/marketplace/discovery.py:775-778` — `_parse_query`: `goal_keys = _match_goal_keys(service_tokens)`; при непустом результате `return ParsedQuery(stems=[], cities=named_cities, goals=goal_keys)`;
* `ai-bot-platform:apps/orchestrator/discovery.py:2530` — «A goal («хочу расслабиться») parses to no stems at all».

Evidence — результат **не кандидат**:
* `ai-bot-platform:apps/marketplace/discovery.py:1063` — единственное употребление: `any_goal |= Q(services_offered__service__goals__contains=[{"key": key}])`, то есть фильтр выдачи на один запрос;
* `cd ai-bot-platform && git grep -ln -e "GoalCandidate" -e "user_wording" -e "goal_candidate" origin/dev -- ':!docs/' ':!*.md'` → **один файл: `apps/skills/menu/tests/test_skill.py`**, строка 136, имя теста `test_user_wording_is_preserved_for_the_booking_llm` — про бронирование, не про цель. В рантайм-коде — **ноль**;
* `cd djangoproject-catalog && git grep -c "GoalCandidate" origin/dev` → **0 файлов**; `git grep -c "user_wording" origin/dev` → **0**; `git grep -c "goal_candidate" origin/dev` → **0**.

**Разрыв, который стоит назвать отдельно:** `_match_goal_keys` возвращает
**список** (`matched: list[str]`, `matched.append(key)`, строки 736-743), и
вызывающий их **OR-ит** (строка 1063). Единственный существующий в контуре
извлекатель цели **многоместен по конструкции**, а контракт §8 механизм 2 требует
`max_candidates_per_event = 1`. Сегодня безвредно (результат не продвигается);
при подключении — прямой конфликт.

---

### Узел 3 — candidate representation: чем кандидат является на хранении и в памяти

**`MISSING`** — ни в БД, ни в эфемерном состоянии, ни в типах.

Evidence — grep-ноль, обе стороны:
```
cd djangoproject-catalog
git grep -c "GoalCandidate"          origin/dev  → 0 файлов
git grep -c "canonical_outcome"      origin/dev  → 0
git grep -c "discrimination_facets"  origin/dev  → 0
git grep -c "source_event_ref"       origin/dev  → 0
git grep -c "candidate_id"           origin/dev  → 9 файлов, все про рекомендации/слоты

cd ai-bot-platform
git grep -l "GoalCandidate"          origin/dev  → 7 файлов, ВСЕ в docs/
git grep -l "GoalCreateCommand"      origin/dev  → 3 файла, ВСЕ в docs/
git grep -l "NEEDS_RECONFIRMATION"   origin/dev  → 1 файл: docs/specs/GOAL_CANDIDATE_CONTRACT_v1.0.md
```

Ближайшее к кандидату в рантайме — `ParsedQuery`
(`ai-bot-platform:apps/marketplace/discovery.py:631-646`, поля `stems / cities / goals`).
У него нет ни `user_wording`, ни `source_event_ref`, ни `origin`, ни `ambiguity`,
ни `sensitivity`, ни `lifecycle`; он живёт внутри одного вызова.

---

### Узел 4 — validation: что проверяется до того, как кандидат станет целью

**`PARTIAL`** — проверок пять, все синтаксические; семантических нет ни одной.

Что **есть** (Ayla):

| # | проверка | путь и строка |
|---|---|---|
| V1 | ровно одно из `goal_key / goal_text / intent / answer` (XOR) | `goals/api.py:87-97` |
| V2 | `source_channel` обязателен при выборе цели | `goals/api.py:98-103` |
| V3 | эхо шага анкеты сверяется с шагом, вычисленным сервером заново; расхождение → HTTP 409 `ANKETA_STEP_MISMATCH` | `goals/api.py:305-312` |
| V4 | `option_key` обязан быть в allowlist текущего шага | `goals/api.py:316-324` |
| V5 | БД: хотя бы одно из ключ/текст | `CheckConstraint` **`clientgoal_key_or_text_present`**, `goals/models.py:78-84` |

Чего **нет вовсе** — из девяти правил §11 контракта не реализовано ни одно:
* **безопасность** (правило 1): `cd djangoproject-catalog && git grep -n -i -e "safety" -e "STOP" -e "CLARIFY" origin/dev -- goals/ ':!goals/tests/'` → **пусто**;
* **сверка свидетельства с журналом событий** (правило 2): `source_event_ref` — grep-ноль (узел 3);
* **кардинальность** (правило 3): проверять нечего, кандидата нет;
* **чувствительность и согласие** (правила 4, 5): `git grep -n -i "consent" origin/dev -- goals/ ':!goals/tests/'` → **пусто**. Гейт `HEALTH` в контуре при этом **существует и работает** — `ai-bot-platform:apps/consent/health.py`, используется `apps/consent/services.py:475` — но к целям не подключён ничем;
* **идентичность/дедупликация** (правило 6): см. узел 5в;
* **неоднозначность** (правила 7, 8): `ambiguity` — grep-ноль.

---

### Узел 5 — promotion: переход кандидат → authoritative Goal

**`MISSING`** как граница (нет двух сторон, между которыми она стоит).
Внутри — три отдельных расхождения.

#### 5а. Явного подтверждения человеком НЕТ — запись происходит сразу

Клик по чипу **сам является** записью. Отдельного шага подтверждения нет ни на
одной поверхности.

Evidence:
* `djangoproject-catalog:goals/api.py:233-241` — после разбора тела сразу `goal = _create_goal(...)`, затем `_close_open_run`, затем `_emit_goal_selected`. Между разбором и записью нет ничего;
* `djangoproject-catalog:goals/api.py:346-347`, дословный комментарий кода:
  > «Финальный шаг. Завершение анкеты И ЕСТЬ выбор цели — отдельной кнопки «готово» нет, **потому что нечего было бы подтверждать**.»
* `ai-bot-platform:apps/miniapp/src/screens/GoalSelectScreen.tsx:227-250` — `submit()` шлёт `postGoalSelect(body)` прямо из обработчика; `setSavedNotice(noticeFor(body))` (строка 235) выставляется **после** успешного ответа — уведомление о совершившемся, не подтверждение до записи;
* `ai-bot-platform:apps/miniapp/src/lib/customer-goals.ts:134-143` — в теле нет ни `confirmation`, ни `question_id`, ни `decision_id`, ни `state_revision`.

Формально канон §12.1 («Core cannot create authoritative Goal by itself») не нарушен:
цель создаёт человек, а не модель. Нарушен другой инвариант — контракт §3:
«Между ними всегда стоит явная политика продвижения». Политики нет: клик = запись.

#### 5б. Свободный ввод против чипа — разное поведение, и оно теряет данные

* **чип** → `goal_key` заполнен, `goal_text` = `NULL`;
* **свободный текст** → `goal_text` заполнен, `goal_key` = `NULL`.

Evidence: XOR в `djangoproject-catalog:goals/api.py:87-97`; `_create_goal`
(`goals/api.py:147-152`) пишет `goal_key=goal_key or None`,
`goal_text=(goal_text or "").strip() or None`.

**`CONTRADICTS_CANON`.** Обе стороны дословно:
* докстринг модели, `djangoproject-catalog:goals/models.py:58-59`:
  > «Дословная формулировка пользователя. **Хранится даже при распознанном ключе** — это будущий датасет формулировок (OD-2).»
* код API, `djangoproject-catalog:goals/api.py:94-97`:
  > `if sum(provided) != 1: raise serializers.ValidationError("Provide exactly one of: goal_key, goal_text, intent, answer.")`

Побеждает API. На пилоте 31 из 33 рядов — с ключом; для них дословной формулировки
человека не сохранено.

#### 5в. Идемпотентности нет — повтор создаёт новый ряд и второе событие воронки

Evidence:
* `djangoproject-catalog:goals/api.py:143-152` — `_create_goal` в транзакции гасит все активные и **безусловно** `ClientGoal.objects.create(...)`. Проверки «та же цель уже активна» нет;
* `djangoproject-catalog:goals/api.py:108-110`, дословный комментарий:
  > «повторная запись той же цели = новая строка (смена цели и есть событие)»
* `cd djangoproject-catalog && git grep -n "idempotency_key" origin/dev -- goals/` → **пусто**;
* бот знает об этом и обходит частично — `ai-bot-platform:apps/integrations/ayla/goals_client.py:327-332`:
  > «...такой отказ закрывается сверкой состояния (`_reconcile_goal_select`), а не повтором POST: **повтор не идемпотентен** по событию воронки (`goals/api.py:_emit_goal_selected` создаёт новую строку на каждый вызов) и по строке `ClientGoal`.»

Обход (`goals_client.py:388-455`) срабатывает **только** при `ReadTimeout`
(строка 419) и **только** для тела, несущего цель (строки 421-423). При
`ConnectTimeout` и при двойном клике человека он не срабатывает.

---

### Узел 6 — persistence: где и как цель хранится

**`EXISTS`**, с одним подтверждённым `CONTRADICTS_CANON` и четырьмя `MISSING` по полям.

Хранение — `djangoproject-catalog:goals/models.py:36-99`, таблица `ClientGoal`.
Поля целиком: `id`, `client`, `goal_key`, `goal_text`, `selected_at`,
`source_channel`, `is_active`, `created_at`, `updated_at`. **Больше ничего**
(`models.py:44-73`).

Ограничения БД:
* **`clientgoal_key_or_text_present`** — `CheckConstraint`, `goals/models.py:78-84`;
* **`clientgoal_one_active_per_client`** — `UniqueConstraint(fields=["client"], condition=Q(is_active=True))`, `goals/models.py:85-89`.

**`CONTRADICTS_CANON` №1** (подтверждён владельцем, продуктовое решение не переоткрывается):
* канон §12.4 дословно: «Multiple ACTIVE Goals are allowed»;
* код: `djangoproject-catalog:goals/models.py:85-89`.
Полный перечень потребителей, молча полагающихся на «одна» — §5.

Чего в хранении нет:

| # | чего нет | требует | evidence |
|---|---|---|---|
| P1 | статусов `PAUSED / ACHIEVED / ARCHIVED` | канон §12.3 | `cd djangoproject-catalog && git grep -c -e "PAUSED" -e "ARCHIVED" origin/dev` → **0 файлов**. `ACHIEVED` → **1 хит, и тот отрицающий**: `wellness/models.py:10` — «`ACHIEVED`/`FAILED` отсутствуют во всех перечислениях». В `ClientGoal` — только `is_active = models.BooleanField(default=True)`, `goals/models.py:71` |
| P2 | оси свежести `FRESH / NEEDS_RECONFIRMATION` | канон §12.3 | `git grep -n "NEEDS_RECONFIRMATION" origin/dev` в обоих репозиториях → только `docs/specs/GOAL_CANDIDATE_CONTRACT_v1.0.md` |
| P3 | причины закрытия цели | политика §6.2 (три причины) | `git grep -c "close_reason" origin/dev` в каталоге → **0 файлов**. Гасится `is_active=False` без причины (`goals/api.py:144-146`) |
| P4 | горизонта / целевой даты | канон §12.2 | `git grep -n -e "target_date" -e "horizon" origin/dev -- goals/ services/models.py` → **пусто**. `git grep -n "ck_target_date_only_for_event" origin/dev` (ограничение из политики §10.1) → **0** |

**Открытие, которого нет в контракте: приложение `wellness` в каталоге.**
Оно построено, зарегистрировано, подключено к URL — и **содержит `target_date`**:
* `djangoproject-catalog:djangoProject/settings/base.py:67` — `'wellness'` в `INSTALLED_APPS`;
* `djangoproject-catalog:djangoProject/urls.py:69-70` — `api/v1/internal/me/wellness-context/`;
* `djangoproject-catalog:wellness/models.py` — `DesiredOutcome`, `PersonalPlan`, `PlanOutcomeLink`, `ProgressObservation`, `EvidenceRegistryEntry`, `PlanAction`;
* `PlanOutcomeLink.target_date` — `DateField(null=True)`, плюс `horizon_status ∈ {none, upcoming, elapsed}` **вычислимым свойством, а не колонкой**;
* `DesiredOutcome` — **0..N активных**; докстринг прямым текстом: «Аналога `clientgoal_one_active_per_client` НЕТ — это осознанное требование (§2)».

Но **писателей у него нет**:
* `git grep -n -E "DesiredOutcome\.objects|PersonalPlan\.objects|ProgressObservation\.objects|PlanOutcomeLink\.objects" origin/dev` вне тестов → **четыре хита, все `.filter(...)`**: `wellness/context_read.py:39,48,76`, `wellness/progress.py:82`. Ни одного `.create()` / `.save()`;
* гейт fail-closed по построению: `wellness/services.py:70-80` — `goal_intention_gate` для `purpose=processing` «**всегда отказ, даже с валидной attestation**».

То есть вопрос «где живёт целевая дата» в коде уже имеет **половину ответа**:
место построено вне `ClientGoal` — и заперто.

---

### Узел 7 — retrieval: кто и как её потом достаёт

**`EXISTS`** — один источник, один документ, строится на каждый запрос, в БД не хранится.

Evidence — построение:
* `djangoproject-catalog:goals/decision_context.py:195-199` — `active_goal = ClientGoal.objects.filter(client=client, is_active=True).order_by("-selected_at").first()`;
* `djangoproject-catalog:goals/decision_context.py:201` — `known: dict[str, Any] = {"goal": _goal_payload(active_goal) if active_goal else None}`;
* `djangoproject-catalog:goals/decision_context.py:270-277` — форма документа `version: 2`.

Evidence — транспорт бот↔Ayla:
* `ai-bot-platform:apps/integrations/ayla/goals_client.py:307-313` — `fetch_decision_context`, GET, документ **as-is**;
* бюджеты: `goals_client.py:70-72` (connect 6 s / read 5 s / reconcile 3 s);
* брейкер: `goals_client.py:86-89` — `ayla.goals`, 5 отказов / 60 с → 30 с;
* 4xx **не** взводит брейкер: `goals_client.py:275-287`.

Четыре живых читателя:

| # | читатель | путь и строка |
|---|---|---|
| R1 | экран цели (Mini App) | `ai-bot-platform:apps/miniapp/src/lib/customer-goals.ts:146-151` |
| R2 | дашборд «Моя цель» | `ai-bot-platform:apps/miniapp_api/views.py:3005`, далее `views.py:3013` → `_active_goals_from_context` |
| R3 | нутриционный коуч | `ai-bot-platform:apps/nutrition_coach/goals.py:83-105` (`active_goal`) |
| R4 | карточка клиента в админке | `ai-bot-platform:apps/adminconsole/clients.py:294-311` (`_active_goal_fact`) — отдаёт «есть / нет / нет данных», **текст цели наружу не выносит** |

Разбор отказов на границе реализован и правильный — три различимых состояния:
`ai-bot-platform:apps/miniapp/src/lib/customer-wellness.ts:159-190` —
`[{…}]` / `[]` / `undefined` (последнее = слой целей недоступен), DRF-1476.

**Расхождение комментария и кода, обнаруженное при замере:**
`ai-bot-platform:apps/nutrition_coach/goals.py:41-43` утверждает:
> «Matches Ayla's own cap (`goals/decision_context.py` truncates `goal_text` at 200), so a text that survives her side survives ours unchanged.»

Ayla этого не делает. Обрезка `[:200]` в `goals/decision_context.py:224` стоит
**только** внутри `PROMPT_GOAL_CLARIFICATION.format(...)`, то есть в тексте
подсказки. `_goal_payload` (`decision_context.py:103-109`) отдаёт `goal.goal_text`
**целиком**, а сериализатор принимает до 1000 символов (`goals/api.py:78`).
Значит формулировка длиной 201–1000 символов молча режется **на стороне бота**
(`goals.py:145`, `MAX_GOAL_TEXT_CHARS = 200`) — именно та формулировка, ради сбора
которой поле заводилось.

---

### Узел 8 — relevance / use: где цель реально влияет на поведение

**`PARTIAL`** — четыре потребителя, но главный выключен на пилоте.

| # | где | что делает | evidence | активно? |
|---|---|---|---|---|
| U1 | подбор услуг: цель → категории | **сужает**, не ранжирует | `djangoproject-catalog:goals/resolution.py:55-88`, `goals/wiring.py:71-92` | **НЕТ**: `GOAL_RESOLUTION_ENABLED` умолчание `false` — `djangoProject/settings/base.py:506-507`; единственное чтение флага — `goals/wiring.py:66-68` |
| U2 | дашборд «Моя цель» | заголовок + `week_num` | `ai-bot-platform:apps/miniapp_api/views.py:2719-2769` | да |
| U3 | нутриционный проактив / наблюдение | цель как условие «молчать или сказать»; уходит в промпт LLM | `ai-bot-platform:apps/nutrition_proactive/coach.py:113`, `apps/orchestrator/coach_observation.py:187`, `apps/orchestrator/nutrition_context.py:237-239,247-259` | да |
| U4 | карточка клиента в админке | факт наличия цели | `ai-bot-platform:apps/adminconsole/clients.py:160` | да |

Поведение при `None` («цели нет» / «не разрешилась») — **фильтр не применяется,
выдача прежняя**: `djangoproject-catalog:goals/wiring.py:18` дословно —
«`None` → фильтр по цели не применяется, выдача остаётся прежней». Фолбэка на
неотфильтрованную выдачу при **пустом результате** фильтра нет намеренно
(`goals/wiring.py:43-49`): пустая полка при разрешённой цели должна быть видна как
дефект курирования.

Свободный текст разрешается **только точным совпадением с `label`** —
`goals/resolution.py:76-86`, `filter(label_lower=normalized)`. Порога уверенности
нет. На пилоте это значит: 2 ряда со свободным текстом влияют на подбор только при
побуквенном совпадении с одним из 7 ярлыков.

---

## 3. Прослеженный путь целиком

```
фраза человека
   │
   ├─ в БОТЕ (DM): _match_goal_keys → goal_key(и)      ← извлечение ЕСТЬ
   │      └─ уходит в фильтр витрины на один запрос и УМИРАЕТ
   │         (ai-bot-platform:apps/marketplace/discovery.py:1063)
   │         цель не создаётся, кандидат не рождается, ничего не сохраняется
   │
   └─ в MINI APP: клик по чипу / свободный текст / финальный шаг анкеты
          │
          │  ✗ GoalCandidate — нет            (grep-ноль, узел 3)
          │  ✗ семантическая валидация — нет  (узел 4)
          │  ✗ подтверждение — НЕТ            (goals/api.py:346-347)
          │  ✗ GoalCreateCommand — нет        (grep-ноль)
          │  ✗ идемпотентность — нет          (goals/api.py:143-152)
          ▼
     POST /internal/me/goals/select/  →  _create_goal  →  ClientGoal (durable)
          │                                гасит все прежние, создаёт новую,
          │                                обе операции в одной транзакции
          ▼
     build_decision_context  →  known.goal (СКАЛЯР)  →  прокси as-is  →  4 читателя
```

**Кто вправе менять, снимать, заменять цель:**
* **менять текст цели — никто.** `UPDATE` строки цели в рантайме отсутствует: `git grep -n "ClientGoal.objects" origin/dev` в каталоге вне тестов → `goals/api.py:144` (`.update(is_active=False)`), `goals/api.py:147` (`.create`), `goals/decision_context.py:196` (чтение), `goals/resolution.py:63` (чтение), `goals/wiring.py:132` (чтение). Правки формулировки не существует;
* **заменять — только человек**, и только созданием новой: `goals/api.py:143-152`;
* **снимать без замены — никак.** Такого входа в API нет: `GoalSelectSerializer` (`goals/api.py:68-104`) принимает только `goal_key / goal_text / intent / answer`; ни `deactivate`, ни `clear`, ни `intent=drop` не существует;
* **салон, мастер, оператор — не вправе и не могут:** `permission_classes = [IsBotServiceWithVerifiedClient]` (`goals/api.py:194`), колонки `tenant` на цели нет (снята миграцией `goals/migrations/0005_remove_clientgoal_tenant.py`).

---

## 4. Расхождения канона и кода — каждое отдельно, обе стороны дословно

### D1. Число активных целей — `CONTRADICTS_CANON` (schema-level; закрыт владельцем)
* Канон §12.4: «Multiple ACTIVE Goals are allowed».
* Код: `djangoproject-catalog:goals/models.py:85-89` — `UniqueConstraint(fields=["client"], condition=models.Q(is_active=True), name="clientgoal_one_active_per_client")`.
Требует миграции и аудита потребителей — §5.

### D2. `user_wording` при выборе чипа — `CONTRADICTS_CANON`
* Докстринг модели, `djangoproject-catalog:goals/models.py:58-59`: «Дословная формулировка пользователя. **Хранится даже при распознанном ключе** — это будущий датасет формулировок (OD-2).»
* Код API, `djangoproject-catalog:goals/api.py:94-97`: XOR — прислать оба нельзя.
Побеждает API. 31/33 ряда на пилоте — без формулировки.

### D3. Явное подтверждение — `MISSING`
* Контракт §3: «Между ними всегда стоит явная политика продвижения».
* Код, `djangoproject-catalog:goals/api.py:346-347`: «отдельной кнопки «готово» нет, потому что нечего было бы подтверждать».

### D4. Идемпотентность — `CONTRADICTS_CANON`
* Канон §12.6: команды цели идемпотентны.
* Код, `djangoproject-catalog:goals/api.py:108-110`: «повторная запись той же цели = новая строка».
* Бот это знает и обходит частично: `ai-bot-platform:apps/integrations/ayla/goals_client.py:327-332`.

### D5. Статусы жизненного цикла — `MISSING`
* Канон §12.3: `ACTIVE / PAUSED / ACHIEVED / ARCHIVED`.
* Код: `is_active = models.BooleanField(default=True)` — `goals/models.py:71`. Grep `PAUSED` / `ARCHIVED` по каталогу → 0.

### D6. Свежесть как отдельная ось — `MISSING`
* Канон §12.3: `FRESH | NEEDS_RECONFIRMATION`, свежесть — не статус.
* Код: grep `NEEDS_RECONFIRMATION` по обоим репозиториям → только текст контракта.

### D7. Целевая дата — `MISSING` в цели, построено и заперто рядом
* Канон §12.2: Goal хранит «target date/time horizon when relevant».
* `ClientGoal`: поля нет (grep `target_date` по `goals/` → пусто).
* `djangoproject-catalog:wellness/models.py::PlanOutcomeLink.target_date` **построен**, вместе с `horizon_status`, подключён к URL — и **не имеет писателей**; гейт `wellness/services.py:70-80` всегда отказывает.

### D8. `source_channel="bot"` — объявлено и не используется
* Схема: `djangoproject-catalog:goals/models.py:40-41` допускает `bot`.
* Код: писателя нет; `ai-bot-platform:apps/miniapp/src/lib/customer-goals.ts:135-143` — все шесть вариантов тела с литералом `"miniapp"`.

### D9. Извлечение цели из фразы — `PARTIAL`, и оно многоместно
* Контракт §2 G2: «Семантического извлечения цели из реплики» — `ABSENT`.
* Замер: извлечение **есть** — `ai-bot-platform:apps/marketplace/discovery.py:716-743`, детерминированное, по тому же словарю `GoalOption`. И оно **многоместно** (`matched: list[str]`), а контракт §8 требует ровно одного кандидата на событие.

### D10. Комментарий про обрезку `goal_text` — расхождение комментария и кода
* `ai-bot-platform:apps/nutrition_coach/goals.py:41-43` утверждает, что Ayla режет `goal_text` до 200.
* `djangoproject-catalog:goals/decision_context.py:103-109` (`_goal_payload`) не режет; `[:200]` есть только в тексте подсказки (`decision_context.py:224`); сериализатор принимает до 1000 (`goals/api.py:78`).

### D11. «Сужающие шаги» анкеты ничего не сужают — расхождение имени и кода
* `djangoproject-catalog:goals/anketa.py:24` и `:62` — шаги названы «сужающими».
* `djangoproject-catalog:goals/anketa.py:105-116` — `_final_step()` **не принимает аргументов** и возвращает `GoalOption.objects.filter(is_active=True)` целиком. Ответы `area` и `feeling` на состав финального шага не влияют. Подробно — §6.

### D12. `ConsentType.HEALTH` — контракт устарел
* Контракт §1.7 помечает `ConsentType.HEALTH` как `EXISTS_UNUSED`.
* На текущей голове это **неверно**: путь выдачи и отзыва построен — `ai-bot-platform:apps/consent/health.py` (DRF-1453), используется `apps/consent/services.py:475`. К целям при этом не подключён (узел 4).

### D13. Дизайн-политика описывает мультицель с лимитом 7 — не реализована вовсе
* `ai-bot-platform:docs/design/policies/customer-wellness-goal-setting-ux.md:711-753` — модель со `status: active/archived_*`, `sequence_number`, «10.2 Active goals limit (enforced at API)»: `{"error": "active_goals_limit_exceeded", "max_active": 7}`, «UI warns at 5 (soft), enforces at 7 (hard)», плюс endpoints `POST/GET/PATCH /customer/wellness/goals`, `/goals/<id>/archive`, `/goals/<id>/restore`.
* Код: **ни один из них не существует**; реализовано ровно противоположное — `clientgoal_one_active_per_client`.

---

## 5. ПЕРЕЧЕНЬ 1 — все места, предполагающие ОДНУ активную цель

Помечено: **[HARD]** — сломается или молча потеряет данные при нескольких активных
целях; **[SOFT]** — формально многоместно, но текст, тип, тест или подпись
предполагают одну.

### 5.1 Корень допущения — БД и докстринги (`djangoproject-catalog`)

| # | место | как зашито |
|---|---|---|
| 1 | `goals/models.py:85-89` | **[HARD]** `UniqueConstraint(fields=["client"], condition=Q(is_active=True), name="clientgoal_one_active_per_client")` — физический запрет |
| 2 | `goals/models.py:21-25` | **[SOFT]** докстринг модуля: «одна активная цель на клиента (partial UniqueConstraint): смена цели закрывает прежний ряд» |
| 3 | `goals/models.py:37` | **[SOFT]** докстринг класса: «Выбранная клиентом цель — **единственная активная на клиента**» |

### 5.2 Запись (`djangoproject-catalog`)

| # | место | как зашито |
|---|---|---|
| 4 | `goals/api.py:143-146` | **[HARD]** `_create_goal` безусловно гасит **все** активные: `ClientGoal.objects.filter(client=client, is_active=True).update(is_active=False)`. Семантика замены, не добавления — при мультицели каждый выбор молча архивирует остальные цели человека. Вызывается из двух мест: `api.py:233` и `api.py:348` |
| 5 | `goals/api.py:155` | **[HARD]** `def _close_open_run(client, *, goal: ClientGoal \| None)` — проход анкеты завершается одной целью |
| 6 | `goals/models.py:129-136` | **[HARD]** `GoalAnketaRun.goal` — `ForeignKey`, не M2M: один проход → максимум одна цель |
| 7 | `goals/api.py:107` | **[HARD]** `_emit_goal_selected(*, client, goal: ClientGoal)` — событие воронки несёт скалярный `goal_key`; при мультицели «сменил» и «добавил вторую» неотличимы |

### 5.3 Чтение и потребители — `djangoproject-catalog`

| # | место | как зашито |
|---|---|---|
| 8 | `goals/decision_context.py:195-199` | **[HARD]** `.filter(client, is_active=True).order_by("-selected_at").first()` — на этой переменной висит вся ветвистость документа: строки 209, 215, 217, 224, 229 |
| 9 | `goals/decision_context.py:201` | **[HARD]** **самый заразный узел**: `known = {"goal": … \| None}` — скаляр, а не список. Это сетевой контракт, его дальше читают восемь потребителей в боте |
| 10 | `goals/decision_context.py:103` | **[HARD]** `def _goal_payload(goal: ClientGoal) -> dict` — одна цель → один dict; `_goals_payload` не существует |
| 11 | `goals/decision_context.py:141` | **[HARD]** `def _goal_is_resolved(goal: ClientGoal, …) -> bool` — одноместный предикат «готова ли цель» |
| 12 | `goals/resolution.py:62-66` | **[HARD]** `.filter(is_active=True).order_by("-selected_at").first()`; возврат `list[UUID] \| None` — категории **одной** цели, объединения нет |
| 13 | `goals/wiring.py:71-92` | **[HARD]** `goal_category_ids_for(client) -> tuple[UUID, …] \| None` — плоский кортеж без разметки, из какой цели какая категория |
| 14 | `goals/wiring.py:118-122` | **[HARD]** `goal_category_ids_for_key(goal_key: str \| None)` — один ключ, не набор |
| 15 | `goals/wiring.py:132` | **[HARD]** `_log_unresolved`: `.exists()` — булев флаг; при N целях лог соврёт («цель есть» и когда разрешились 2 из 3) |
| 16 | `users/catalog_recommendations_api.py:570-575` | **[HARD]** `_saved_goal_key()` — `.first()`, возврат `str \| None` |
| 17 | `users/catalog_recommendations_api.py:492-496` | **[HARD]** `goal_key = … ; goal_category_ids = goal_category_ids_for(request.user)` → `NeedSpec` |
| 18 | `users/home_api.py:258` | **[HARD]** `goal_category_ids=goal_category_ids_for(user)` — пассивная выдача «Рядом с вами» фильтруется категориями одной цели |
| 19 | `users/recommendation_source.py:118` | **[HARD]** `goal_categories = goal_category_ids_for_key(need.goal_key)` |
| 20 | `recommendation/_types.py:283,293` | **[HARD]** `NeedSpec.goal_key: str \| None` — нужда несёт ровно один ключ |
| 21 | `recommendation/_serializers.py:75` | **[HARD]** `goal_key = serializers.CharField(…)` — скаляр в API-схеме |
| 22 | `recommendation/_types.py:371` | **[HARD]** `matched_goal_category_ref: UUID \| None` — обоснование «почему показали» одноместно; читается `recommendation/_stages.py:435,449` |
| 23 | `analytics/event_catalogue.py:95-97` | **[SOFT]** payload воронки — `goal_key` в единственном числе |

### 5.4 Чтение и потребители — `ai-bot-platform`

| # | место | как зашито |
|---|---|---|
| 24 | `apps/miniapp_api/views.py:2747` | **[HARD]** `goal = known.get("goal")`; `if not isinstance(goal, dict): return []` |
| 25 | `apps/miniapp_api/views.py:2765-2769` | **[HARD]** `return [entry]` — **`active_goals` это НЕ массив целей, а одноэлементный список, синтезированный из скаляра**. `_active_goals_from_context` физически не может вернуть больше одного |
| 26 | `apps/miniapp_api/views.py:2719` | **[SOFT]** тип `-> list[dict[str, Any]]` / **[HARD]** реализация |
| 27 | `apps/miniapp_api/views.py:3003,3013,3045` | **[SOFT]** многоместно по форме, одноместно по содержимому |
| 28 | `apps/miniapp_api/views.py:2818-2821` | **[SOFT]** докстринг фиксирует словами: «goal present → **one-element list**» |
| 29 | `apps/nutrition_coach/goals.py:62-66` | **[HARD]** `def active_goal(...) -> Goal \| None` — скалярный возврат |
| 30 | `apps/nutrition_coach/goals.py:117,131` | **[HARD]** `goal = known.get("goal")`; `return Goal(key=key, text=text)` — одна цель на человека безальтернативно |
| 31 | `apps/nutrition_coach/goals.py:49-59` | **[HARD]** `@dataclass(frozen=True) class Goal` — скалярные `key` / `text` |
| 32 | `apps/orchestrator/nutrition_context.py:237-239` | **[HARD]** `return active_goal(bot_user)`; докстринг строки 233: «**цель — одна на диетолога**, и два места её читать это два места разойтись» |
| 33 | `apps/orchestrator/nutrition_context.py:247-259` | **[HARD]** `_render_goal_lines(goal)` → `return [f"Цель клиента своими словами: {text}"]` — **в промпт LLM уходит одна строка цели**, «Цель стоит ПЕРВОЙ в блоке: она рамка» |
| 34 | `apps/nutrition_proactive/coach.py:102,113` | **[HARD]** `fetch_goal: Callable[[Any], Goal \| None]` |
| 35 | `apps/orchestrator/coach_observation.py:75,187` | **[HARD]** тот же скалярный `Goal` |
| 36 | `apps/nutrition_coach/management/commands/nutrition_coach_dryrun.py:39` | **[HARD]** `_STUB_GOAL = Goal(key="sleep_better", text=None)` |
| 37 | `apps/adminconsole/clients.py:308-311` | **[HARD]** `_active_goal_fact` → «есть / нет / нет данных»; мультицель схлопнется в «есть» |
| 38 | `apps/adminconsole/clients.py:160` | **[HARD]** `"active_goal": _active_goal_fact(bot_user)` — ключ контекста в ед. ч. |
| 39 | `apps/adminconsole/templates/adminconsole/client_card.html:40` | **[HARD]** `<dt>Активная цель</dt><dd>{{ active_goal }}</dd>` — одна `<dd>`, не список |
| 40 | `apps/integrations/ayla/goals_client.py:441` | **[HARD]** `goal = known.get("goal")` в `_reconcile_goal_select` |
| 41 | `apps/integrations/ayla/goals_client.py:376-385` | **[HARD]** `_selected_goal_matches` сравнивает **одну** цель документа с **одной** отправленной. При мультицели: цель записалась, но она не «та единственная» → `reconcile_miss` → **человек получает 502 на успешной записи** |
| 42 | `apps/integrations/ayla/goals_client.py:352-373` | **[HARD]** `_durable_goal_intent(payload) -> dict[str, str]` — одна пара |

### 5.5 TypeScript-типы и экраны (`ai-bot-platform`)

| # | место | как зашито |
|---|---|---|
| 43 | `apps/miniapp/src/lib/customer-goals.ts:114` | **[HARD]** `known: { goal: KnownGoal \| null }` — зеркало серверного скаляра; тип `KnownGoal` — строки 22-27 |
| 44 | `apps/miniapp/src/lib/customer-wellness.ts:187-190` | **[SOFT]** тип `active_goals?: Array<{…}>`, но строка 160: «Active goals (**cap=1 for MVP**)», строка 181: «**Одна цель, не несколько** — тем же решением [владельца №13]» |
| 45 | `apps/miniapp/src/screens/CustomerWellnessDashboardScreen.tsx:835` | **[HARD]** `const goal = data.active_goals?.[0];` с комментарием «Одна цель, не несколько (решение владельца №13, 06.09)» — вторая и последующие не отрисуются |
| 46 | `apps/miniapp/src/screens/CustomerWellnessDashboardScreen.tsx:961-984` | **[HARD]** весь блок «Goal row §11.2» рендерит скаляр; `aria-label` = `Цель: ${goal.title}` |
| 47 | `apps/miniapp/src/screens/CustomerWellnessDashboardScreen.tsx:378-384` | **[SOFT]** длина считается, но подпись CTA бинарная: «Моя цель» / «Выбери цель» / «Цель» — при 3 целях соврёт |
| 48 | `apps/miniapp/src/screens/GoalSelectScreen.tsx:272-277` | **[HARD]** `const knownGoal = doc.known.goal;` + лестница заголовка |
| 49 | `apps/miniapp/src/screens/GoalSelectScreen.tsx:421` | **[HARD]** `<p className="goal-select__current">{knownLabel}</p>` — «текущая цель» одним параграфом |
| 50 | `apps/miniapp/src/screens/GoalSelectScreen.tsx:474` | **[HARD]** `const active = knownGoal?.goal_key === s.key;` — подсветится максимум один чип |
| 51 | `apps/miniapp/src/lib/customer-goals.ts:134-143` | **[HARD]** `GoalSelectBody` — union с одним `goal_key` / одним `goal_text`; API «добавить цель» не существует, только «выбрать» |

### 5.6 Тесты, прибивающие «одну» (доказательства намерения)

`djangoproject-catalog`: `goals/tests/test_goal_layer.py:94-101` (`test_one_active_per_client` — второй `create` ожидает `IntegrityError`), `:228,242` (`.get(is_active=True)` — бросит `MultipleObjectsReturned`), `:257-260` (`test_replacement_closes_previous`); `goals/tests/test_goal_anketa.py:286` (`.get(...)`), `:624` (`doc["known"]["goal"]["goal_key"]` — индексация скаляра), `:645` (`count() == 1` после трёх проходов); `goals/tests/test_service_match_matrix.py:130-132` (фикстура `.delete()` перед `create` — умеет только заменять).

`ai-bot-platform`: `apps/miniapp_api/tests/test_wellness_today.py:410,452,476,486,546` (везде `active_goals[0]`); `apps/nutrition_coach/tests/test_goals.py:74,86` (документ с одиночным `goal`, ни одного теста с массивом); `apps/miniapp/src/screens/CustomerWellnessDashboardScreen.test.tsx:308,354,400` (всегда ровно один элемент).

Единственное место во всём контуре, где `active_goals` содержит **два** элемента —
`apps/miniapp/src/screens/CustomerWellnessDashboardScreen.test.tsx:378-380`. Что
именно этот тест утверждает, **я не проверял** (см. §8).

### 5.7 Grep-команды, давшие НОЛЬ (тоже evidence)

`djangoproject-catalog`, `git grep -n "<шаблон>" origin/dev -- '*.py'`:

| шаблон | результат | что доказывает |
|---|---|---|
| `active_goals` | 0 | каталог никогда не отдаёт множественное имя; `active_goals` — изобретение бота |
| `ClientGoalSerializer` | 0 | **сериализатора для `ClientGoal` не существует** — `_goal_payload()` собирает dict руками; нет ни `many=True`, ни `ListSerializer` |
| `client_goals\.` | 0 | `related_name="client_goals"` объявлен (`models.py:47`) и **не используется ни разу** — множественная форма живёт только в имени |
| `goal__in` | 0 | фильтров по набору целей нет |
| `prefetch_related.*goal` | 0 | никто не готовится читать много целей |
| `ClientGoal.objects.latest` / `.all` | 0 | ни «последняя цель», ни полный перебор |
| `ClientGoal` в `*admin*` | 0 | модель не в админке — нет и админского списка целей |

`ai-bot-platform`, `git grep -n "<шаблон>" origin/dev -- '*.py' '*.ts' '*.tsx'`:

| шаблон | результат | что доказывает |
|---|---|---|
| `active_goals.map` / `goals.map(` | 0 | **массив `active_goals` ни разу не итерируется** — только `[0]` |
| `active_goals[1]` | 0 | ко второй цели никто не обращается |
| `for goal in` | 0 | ни одного цикла по целям во всём репозитории (py + ts) |
| `list[Goal]` | 0 | `nutrition_coach.Goal` не встречается во множественном числе |
| `Goal[]` (TS) | 0 | TS-типа «массив целей» не существует |
| `known.goals` | 0 | множественного ключа в документе нет ни у одного потребителя |

### 5.8 Смежное — тот же паттерн, но ДРУГИЕ сущности (не путать при миграции)

* `djangoproject-catalog:wellness/models.py:94` — `PersonalPlan`: «0..1 ACTIVE на человека (OD-GOAL-4)», `personalplan_one_active_per_user`;
* `djangoproject-catalog:wellness/models.py:133` — `PlanOutcomeLink`: «≤1 ACTIVE на outcome (GOALS-R4)»;
* `djangoproject-catalog:wellness/context_read.py:38` — то же в чтении;
* `djangoproject-catalog:nutrition/` + `ai-bot-platform:apps/nutrition_proactive/render.py:184,195` — `profile.goal ∈ {lose, maintain, gain, tone}`: цель **по весу** из анкеты питания, другая сущность;
* `ai-bot-platform:apps/marketplace/discovery.py:641,649,674` — `goals: list[str]` у **услуги** в зеркале каталога; направление обратное, допущения «одна» нет.

**Единственный контракт, который сознательно от допущения отстраивается:**
`ai-bot-platform:docs/specs/PLAN_ENGINE_CONTRACT_v1.0.md:283-285` — «многоместность
закладывается схемой (`goal_ref` — поле, а не ограничение «ровно одна цель» на
клиента — **ограничение пилота не копируется**)».

### 5.9 Что сломается первым при снятии `clientgoal_one_active_per_client`

1. `goals/decision_context.py:195-201` — `known.goal` скаляр. Потянет за собой восемь потребителей в боте разом;
2. `goals/tests/test_goal_layer.py:228,242`, `test_goal_anketa.py:286` — `.get(is_active=True)` бросит `MultipleObjectsReturned`. **Упадёт громко — это хорошо**;
3. `goals/api.py:144` — `_create_goal` тихо погасит все прочие цели. **Упадёт тихо — это плохо**;
4. `apps/miniapp_api/views.py:2769` (`return [entry]`) и `CustomerWellnessDashboardScreen.tsx:835` (`?.[0]`) — тихая потеря данных без единого исключения;
5. `apps/integrations/ayla/goals_client.py:376` — `_selected_goal_matches` даст ложные 502 на успешных записях.

---

## 6. ПЕРЕЧЕНЬ 2 — потребители ответов `area` и `feeling`

Установлено сплошным обходом обоих репозиториев. Не удаляю и не оправдываю —
только устанавливаю.

### 6.1 Что вообще пишется

`djangoproject-catalog:goals/api.py:334-339` — ответы на **не-финальные** шаги
пишутся в `GoalAnketaAnswer`:
`update_or_create(run=run, step_key=expected.key, defaults={"option_key": option_key, "answer_text": text})`.
На этом путь записи кончается — сразу `return success_response(build_decision_context(client))`.

### 6.2 Полный перечень мест, где сохранённые ответы читаются

Команды (каталог):
```
git grep -n "GoalAnketaAnswer" origin/dev
git grep -n "\.answers" origin/dev -- ':!*/migrations/*'
git grep -n -e "answer_text" -e "option_key" origin/dev -- '*.py' | grep -v /migrations/
```

Результат — **ровно один читатель во всём контуре**:

| # | читатель | путь и строка | что именно читает |
|---|---|---|---|
| C1 | `next_anketa_step` | `djangoproject-catalog:goals/decision_context.py:137` — `answered = set(run.answers.values_list("step_key", flat=True))` | **только `step_key`**, то есть «на какие шаги уже ответили». `option_key` и `answer_text` не читаются |

Больше нигде:
* `GoalAnketaAnswer` вне `goals/api.py`, `goals/models.py` и тестов **не упоминается** (вывод первой команды выше — 14 строк, все из этих трёх мест);
* `git grep -rn "GoalAnketa" origin/dev -- '*/admin.py' '*/serializers.py' 'analytics/'` → **пусто**: ни админки, ни сериализатора, ни события аналитики;
* `git grep -n "anketa" origin/dev -- analytics/` → **пусто**;
* `goals/admin.py` **не существует**: `git ls-tree -r --name-only origin/dev goals/ | grep -i admin` → пусто;
* **по проводу значения не уезжают**: документ состояния (`goals/decision_context.py:270-277`) отдаёт `version / known / missing / suggestions / intents / next`. Поля с ответами анкеты в нём нет;
* в боте: `git grep -rn -e '"area"' -e '"feeling"' origin/dev -- ':!docs/'` → **8 хитов, все в тестах** (`apps/integrations/ayla/tests/test_goals_client_cold_path.py:342`, `GoalSelectScreen.anketa.test.tsx:54,77,174,400,527`, `CustomerEntryScreen.test.tsx:74`, `GoalSelectScreen.test.tsx:299`). В рантайм-коде бота — **ноль**;
* единственная ссылка бота на шаг анкеты — `ai-bot-platform:apps/integrations/ayla/goals_client.py:79` `ANKETA_FINAL_STEP: Final[str] = "goal"`, и нужна она ровно чтобы **отфильтровать** не-финальные шаги: `goals_client.py:369`. Докстринг `goals_client.py:361-363` дословно: «Ответы на СУЖАЮЩИЕ шаги durable-следа в `ClientGoal` не оставляют (цели ещё нет), поэтому примирять их нечем».

**Ловушка имени.** В боте есть **другая** анкета — `nutrition_anketa` / `cb:anketa:*`
(`ai-bot-platform:apps/channels/max/handler.py:1340`, `apps/conversations/models.py:184`).
К анкете целей она отношения не имеет; при грепе по слову `anketa` она даёт
десятки ложных совпадений.

### 6.3 Влияют ли `area` / `feeling` на состав финального шага

**Нет.** `djangoproject-catalog:goals/anketa.py:105-116` — `_final_step()`
**не принимает аргументов** и строит варианты из
`GoalOption.objects.filter(is_active=True)` целиком. `next_step(answered_keys)`
(`anketa.py:119-129`) использует `answered_keys` только чтобы выбрать «какой шаг
не отвечен», и на финальном шаге вызывает `_final_step()` без единого параметра.

При этом код называет эти шаги «сужающими» — `anketa.py:24`, `anketa.py:62`.
Ровно тот случай, о котором предупреждает свод правил: **имя говорит одно, код
делает другое.**

### 6.4 Вывод перечня 2

Потребитель **значений** ответов `area` и `feeling` — **ноль**, во всех трёх
местах, где он мог бы быть: в проекции состояния, по проводу, в аналитике или
админке. Потребитель **факта ответа** — один (`decision_context.py:137`), и ему
нужен только `step_key`.

Это **факт замера, а не приглашение удалять шаги.** У сохранения ответов есть
объявленное назначение вне рантайма: `djangoproject-catalog:goals/models.py:161-163`
дословно — «Хранится дословно и не нормализуется по той же причине, что и
`ClientGoal.goal_text`: **это будущий корпус формулировок (OD-2)**». На пилоте
корпус пуст именно в той части, ради которой заводился: **свободным текстом на
анкету ответили 0 раз из 15** (число главного окна).

---

## 7. Решения владельца, без которых нельзя определить целевой контракт

Только те, без которых нельзя. Без вариантов и без рекомендаций: что неизвестно
и что это блокирует.

**OD-1. Нужно ли явное подтверждение перед созданием цели, и на каких входах.**
Не определено: сегодня клик по чипу **и есть** запись (`goals/api.py:233-241`,
`goals/api.py:346-347`), отдельного шага нет ни на одной поверхности.
Блокирует: `GoalCandidate` как сущность (без подтверждения ему негде жить),
`question_id` подтверждения, `GoalCreateCommand.confirmation`, весь узел 5.

**OD-2. Что записывать при выборе чипа — ключ, ярлык или оба.**
Не определено: докстринг модели требует хранить формулировку «даже при
распознанном ключе» (`goals/models.py:58-59`), API это запрещает XOR-ом
(`goals/api.py:94-97`). Побеждает API.
Блокирует: снятие XOR, миграцию, требование канона §12.2 хранить `user_wording`,
и — для 31 из 33 рядов на пилоте — сам сбор корпуса формулировок.

**OD-3. Существует ли путь «снять цель, не выбирая другую».**
Не определено: такого входа в API нет вовсе (`GoalSelectSerializer`,
`goals/api.py:68-104`). Цель гасится **только** созданием следующей.
Блокирует: статусы `PAUSED` / `ARCHIVED`, причину закрытия, поведение
«человек передумал».

**OD-4. Что означает `is_active=False` — три причины политики §6.2 или одна.**
Не определено: колонки причины нет (`git grep "close_reason"` → 0). Цель, которую
человек считает достигнутой, неотличима от заменённой.
Блокирует: `ACHIEVED`, аналитику воронки, любой текст «получилось».

**OD-5. Разговорный вход цели, и что делать с уже существующим извлекателем.**
Не определено, и предмет шире, чем формулировка контракта: извлекатель
`_match_goal_keys` (`ai-bot-platform:apps/marketplace/discovery.py:716-743`)
**уже узнаёт цель во фразе человека и уже возвращает несколько ключей**. Сегодня
результат живёт один запрос.
Блокирует: `source_channel="bot"` (объявлен, не используется), инвариант
«не более одного кандидата на событие» (сегодняшний извлекатель многоместен),
границу EVIDENCE ORIGIN для текстового пути.

**OD-6. Куда переезжает целевая дата и что делать с уже построенным `wellness`.**
Не определено: `ClientGoal` даты не имеет; `PlanOutcomeLink.target_date` и
`horizon_status` **построены** (`djangoproject-catalog:wellness/models.py`),
подключены к URL (`djangoProject/urls.py:69-70`) и **заперты гейтом, который
всегда отказывает** (`wellness/services.py:70-80`). Без решения нельзя сказать,
ждёт ли вопрос миграции `ClientGoal` или открытия гейта `wellness`.
Блокирует: блок `horizon` кандидата, поведение «дата прошла», текст «до события N дней».

**OD-7. Контракт чтения после снятия `clientgoal_one_active_per_client`.**
Решение по существу принято (несколько активных — канон). Не определено, **что
считать «текущей целью» при чтении**: сегодня `known.goal` — скаляр
(`goals/decision_context.py:201`), и его читают восемь независимых потребителей
(§5.4–5.5), а `active_goals` в боте — одноэлементный список, синтезированный из
этого скаляра (`views.py:2769`). Без правила выбора и порядка миграция меняет
форму документа состояния, а не только схему БД.
Блокирует: саму миграцию.

---

## 8. Не замерено — честный список

1. **`ayla-ai-core`.** Ветки `origin/dev` в этом репозитории **не существует**
   (`git rev-parse origin/dev` → `fatal: ambiguous argument`). Есть `origin/main`
   и локальная `fix/memory-origin-vocabulary` @ `73b0422` от 2026-09-03.
   Утверждение контракта «`ayla-ai-core` не содержит слова `goal` вовсе» в этой
   сессии **не переснято**: греп по несуществующей ветке доказательством не является.
2. **Живой рантайм.** Ни одного HTTP-запроса к `api-dev.gobeauty.site` не сделано,
   контейнеры не поднимались. Всё сказанное — свойство кода на двух названных SHA,
   а не наблюдение поведения на пилоте.
3. **Числа с пилота.** Взяты у главного окна как есть, не перепроверялись.
   Срок годности — 09.09.2026.
4. **Тесты.** Не запускались. Ни одно утверждение здесь не подтверждено прогоном.
5. **`ai-bot-platform:apps/miniapp/src/screens/CustomerWellnessDashboardScreen.test.tsx:378-380`** —
   единственное место в контуре, где `active_goals` содержит **два** элемента.
   Что этот тест утверждает, я не проверял. Это ближайший к мультицели тест во
   всём проекте, и его стоит прочитать до миграции.
6. **`services/goal_resolution.py`** — файл существует
   (`git ls-tree origin/dev services/goal_resolution.py` → blob `51efd41`) и
   используется (`goals/resolution.py:30`), но прочитан только по факту
   существования; `expand_categories_with_descendants` построчно не разбиралась.
7. **`goals/morphology.py`** и **`goals/service_match.py`** — не прочитаны. Они
   участвуют в `_goal_is_resolved` через `match_named_service`
   (`decision_context.py:180`), то есть влияют на узлы 7 и 8.
8. **Полный обход `wellness/`** — прочитаны `models.py`, `urls.py`, `api.py`,
   начало `services.py`. Не читались целиком `adherence.py`, `admission.py`,
   `context_read.py`, `fact_providers.py`, `progress.py`.
9. **Миграции.** Читались только имена файлов `goals/migrations/*`. Ни одна не
   прочитана построчно; утверждение «колонка `tenant` снята» опирается на имя
   файла `0005_remove_clientgoal_tenant.py` и докстринг модели, а не на содержимое.
10. **Безопасностный слой** (канон §7, состояния `STOP` / `CLARIFY`) — установлено
    только его отсутствие **внутри `goals/`**. Существует ли он в контуре вообще —
    не измерялось.
11. **Поверхности бота вне Mini App.** Проверено отсутствие вызовов
    `post_goal_select`; полный обход всех скиллов `apps/skills/*` на предмет
    скрытых путей к цели не делался.
