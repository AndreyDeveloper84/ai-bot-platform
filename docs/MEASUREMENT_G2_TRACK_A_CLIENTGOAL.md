# G2 · ТРЕК A — замер `ClientGoal` и сквозных путей 1–2

**Дисциплина:** MEASURE → CLASSIFY → GAP → OWNER DECISION ONLY IF NECESSARY.
**Что это НЕ:** не проект Plan Engine, не план миграции, не исправления.
Ни строки кода, ни миграции, ни PR, ни Linear в рамках этого замера не тронуто.

Замер сделан **от кода канонических веток**, не от handoff/spec. Документы
открывались только после замера и только для сравнения — каждое такое место
явно помечено словом «документ».

---

## 1. Базы замера

`git fetch` выполнен во всех трёх репозиториях 2026-09-09.

| Репозиторий | Каноническая ветка | Полный SHA | Верхний коммит |
|---|---|---|---|
| `djangoproject-catalog` (`AndreyDeveloper84/beautygo_backend`) | `origin/dev` | `95c917e684652476feef3ae9d790fb2c8d277378` | 2026-09-09 06:50:27 +0300 «fix(recommendation): кандидат называется ключом ПОЛЬЗОВАТЕЛЯ… (#303)» |
| `ai-bot-platform` (`AndreyDeveloper84/ai-bot-platform`) | `origin/dev` | `83ed56a94eb0a96d9599c632f0e6ba2d48c6c5b7` | 2026-09-09 10:52:48 +0300 «docs: живой реестр замеров пилота… (#1500)» |
| `ayla-ai-core` (`AndreyDeveloper84/ayla-ai-core`) | `origin/main` | `d72a5de451f985d118d9449d2b17ce51bf0a6e25` | 2026-09-03 04:50:47 +0300 «fix(memory): цитатой считается только объявленное “сказал сам”… (#18)» |

### Каноническая ветка `ayla-ai-core` — установлена, не угадана

`git branch -r` даёт **ровно две строки**: `origin/HEAD -> origin/main` и
`origin/main`. `git rev-parse --verify origin/dev` → `fatal: Needed a single
revision`. Ветки `origin/dev` в этом репозитории не существует и никогда не
было выложено; `origin/HEAD` указывает на `main`. Каноническая ветка —
**`origin/main`**, класс `EXISTS`, не `UNKNOWN`.

### Оговорка по рабочим деревьям

* `djangoproject-catalog`: рабочее дерево стоит на локальном `dev`, который
  совпадает с `origin/dev` (`95c917e…`).
* `ai-bot-platform`: рабочее дерево стоит на `feat/recommendation-boundary-client`
  (`fd6f4e87…`), **разошедшемся с `origin/dev` на сотни файлов**. Поэтому все
  чтения по боту делались через `git show origin/dev:<path>` и
  `git grep … origin/dev`, ни одна строка не читалась из файловой системы.
* `origin/HEAD` каталога указывает на `origin/master`, но пилотный контур
  (`api-dev.gobeauty.site`) идёт с `dev`: `dev` впереди `master` на 694 коммита,
  `master` впереди `dev` на 42. Предмет замера — `dev`.

---

## 2. Сводка по классам — числом

| Класс | Число утверждений | Из них |
|---|---|---|
| `EXISTS` | 21 | модель, 5 миграций, единственный писатель, 6 читателей, событие воронки, 2 флага, 2 ручки каталога, 2 ручки бота, экран Mini App, анкета, `GoalOption`/`GoalOptionCategory`, обратный индекс |
| `PARTIAL` | 5 | `goal_key` без проверки на прямом пути; `_goal_is_resolved` расходится с резолвером; `title` смешивает чип и слова человека; `active_goals` — список длиной ≤1; свобода уйти из анкеты держится только `next` |
| `MISSING` | 9 | 4 состояния жизненного цикла; ось свежести; прекращение цели без выбора новой; `active_goals[]`/`relevant_goal_refs[]` в каталоге; запись цели из MAX DM; извлечение цели LLM; собственное durable-хранилище цели в боте; передача цели в тело запроса рекомендаций; ClientGoal в поверхности удаления персданных |
| `CONTRADICTS_CANON` | 4 | `clientgoal_one_active_per_client`; `known.goal` как канонический скаляр; `active_goals[0] == текущая цель` в боте; шаги анкеты `area`/`feeling` живы в коде |
| `STALE_SPEC` | 4 | докстринг `0004` ссылается на несуществующий `goals/tenant_scope.py`; докстринг `wiring.py` «ClientGoal = 0» без даты; `views.py:2526` перечисляет `goal` в теле, у которого нет отправителя; `nutrition_coach/goals.py:120` защищается от поля `is_active`, которого контракт не отдаёт |
| `BLOCKED_BY_GATE` | 2 | фильтр выдачи по сохранённой цели (`GOAL_RESOLUTION_ENABLED`, default `false`); `wellness.DesiredOutcome` (Gate D — безусловный отказ) |
| `UNKNOWN_NOT_MEASURED` | 6 | значение `GOAL_RESOLUTION_ENABLED` на боевом контуре; число строк `ClientGoal` сегодня; применены ли `wellness`-миграции на пилоте; боевые ответы ручек; поведение `nutrition_coach` при `active_goal() is None`; влияние `X-External-User-ID` на рекомендации в боевом прогоне |

**Один тяжёлый дефект — отдельно (см. §9.1):** конверт ответа. Четыре
python-читателя бота читают документ не с того уровня. Класс подтверждения —
`EXISTS` (дефект существует и доказан кодом обеих сторон), но проявление на
боевом контуре — `UNKNOWN_NOT_MEASURED` (сетевой ответ не снимался).

---

## 3. `ClientGoal` — поля и ограничения

Источник: `djangoproject-catalog@95c917e` → `goals/models.py:36-100`.

| Поле | Тип, строка | Фактическая семантика |
|---|---|---|
| `id` | `UUIDField(primary_key, default=uuid4, editable=False)` — `:43` | суррогатный |
| `client` | `FK(AUTH_USER_MODEL, on_delete=PROTECT, related_name="client_goals")` — `:44-48` | **владение — человек, не салон**; удаление человека блокируется |
| `goal_key` | `SlugField(max_length=64, null=True, blank=True)` — `:52-57` | ключ курируемой подсказки. **FK на `GoalOption` НЕТ** — только слаг |
| `goal_text` | `TextField(null=True, blank=True)` — `:60-64` | дословная формулировка человека, не нормализуется (OD-2, корпус) |
| `selected_at` | `DateTimeField(default=timezone.now)` — `:65` | момент выбора (не момент записи) |
| `source_channel` | `CharField(max_length=16, choices=[bot, miniapp])` — `:66-70` | **обязателен, без `blank`, без `default`** — «канал неизвестен» не выражается |
| `is_active` | `BooleanField(default=True)` — `:71` | единственная ось состояния |
| `created_at` | `DateTimeField(auto_now_add=True)` — `:72` | момент записи |
| `updated_at` | `DateTimeField(auto_now=True)` — `:73` | последняя запись строки |

**Чего в модели НЕТ (замерено `git grep -i "paused\|achieved\|archived\|lifecycle" origin/dev -- goals/` → ни одного совпадения):**
целевой даты, статуса, состояний `PAUSED`/`ACHIEVED`/`ARCHIVED`, оси свежести,
прогресса, `tenant` (снят миграцией `0005`), связи с планом, связи с записью.

### Ограничения (`Meta`, `:75-96`)

| Имя | Вид | Что делает |
|---|---|---|
| `clientgoal_key_or_text_present` | `CheckConstraint(Q(goal_key__isnull=False) \| Q(goal_text__isnull=False))` | хотя бы одно из двух заполнено |
| `clientgoal_one_active_per_client` | `UniqueConstraint(fields=["client"], condition=Q(is_active=True))` | **ровно одна активная цель на человека** |
| `clientgoal_client_active_idx` | `Index(["client", "is_active"])` | индекс чтения |
| `ordering` | `["-selected_at"]` | по времени выбора |

`clientgoal_one_active_per_client` — **`CONTRADICTS_CANON`** (канон 2:
несколько ACTIVE разрешены). Это ограничение уровня схемы, то есть запрет
живёт в БД, а не в коде: любая попытка держать две активные цели упадёт в
`IntegrityError` до всякой логики.

### Соседние модели того же приложения

* `GoalAnketaRun` (`:103-155`) — проход анкеты; `client` PROTECT,
  `goal FK → ClientGoal, on_delete=SET_NULL`, `goalanketarun_one_open_per_client`
  (partial unique по `completed_at IS NULL`).
* `GoalAnketaAnswer` (`:158-209`) — ответ на шаг; `run` CASCADE,
  `goalanketaanswer_one_per_step`, `goalanketaanswer_option_or_text_present`.

**`SET_NULL` на `GoalAnketaRun.goal` — опасность миграции**, см. §10.

---

## 4. Миграции — прочитаны сами файлы, построчно

`goals/migrations/` содержит `0001`…`0005` + `__init__.py`. Прочитаны все пять
целиком (`git show origin/dev:goals/migrations/<f>.py`).

| Файл | Дата в шапке | Операции — фактически |
|---|---|---|
| `0001_initial.py` | 2026-08-19 07:24 | `CreateModel ClientGoal` с девятью полями в точности как в §3; `options` несёт `ordering`, `indexes=[clientgoal_client_active_idx]`, `constraints=[clientgoal_key_or_text_present, clientgoal_one_active_per_client]`. Зависимость — только `swappable_dependency(AUTH_USER_MODEL)`. **Ограничение «одна активная» стоит в схеме с первого дня**, не добавлено позже. |
| `0002_goalanketarun_goalanketaanswer_and_more.py` | 2026-09-03 11:31 | `CreateModel GoalAnketaRun` (+`goal FK→goals.clientgoal, SET_NULL`), `CreateModel GoalAnketaAnswer`, затем `AddIndex goalanketarun_client_open_idx`, `AddConstraint goalanketarun_one_open_per_client`, `AddConstraint goalanketaanswer_option_or_text_present`, `AddConstraint goalanketaanswer_one_per_step`. `ClientGoal` не тронут. |
| `0003_clientgoal_tenant.py` | 2026-09-04 06:58 | `AddField clientgoal.tenant` (`FK→tenants.tenant`, `null=True`, `blank=True`, `PROTECT`, `related_name="client_goals"`), `AddIndex clientgoal_tenant_active_idx`. Зависит от `tenants.0003_seed_default_tenants`. |
| `0004_backfill_clientgoal_tenant.py` | — (рукописная) | `RunPython(backfill, unbackfill)`. `backfill` (`:32-77`): берёт `ClientGoal` с `tenant IS NULL`, собирает **одной** выборкой активные `TenantUserRelationship` по всем клиентам; если у клиента ровно одна активная связь — ставит её тенанта; **если несколько — `continue`, строка остаётся NULL** (`:68-70`); если связей нет — берёт легаси `User.tenant`; `None` → пропуск. Пишет `update()` пачками по тенанту. `unbackfill` (`:80-82`): `ClientGoal.objects.filter(tenant__isnull=False).update(tenant=None)`. |
| `0005_remove_clientgoal_tenant.py` | — (рукописная) | `RemoveIndex clientgoal_tenant_active_idx`, `RemoveField clientgoal.tenant`. Докстринг объясняет, почему эффект отменён **вперёд**, а `0003`/`0004` не удалены: они уже применены на боевом пилоте слиянием PR #286 (`ddda863`). |

**Что миграции говорят фактами, а не намерением:**

1. Схема `ClientGoal` менялась **три раза** (`0001` создала, `0003` добавила
   `tenant`, `0005` его сняла), и текущее состояние колонок **тождественно**
   `0001`. Таблица на пилоте прошла полный цикл добавления и снятия колонки.
2. `0004` — **единственная в приложении миграция данных**. Её обратная операция
   не восстанавливает исходное состояние по строкам, а обнуляет колонку целиком.
   Это корректно только потому, что до `0003` колонки не существовало, — и
   докстринг это утверждает явно.
3. `0004` **честно оставляет NULL** там, где выбрать нельзя (несколько салонов).
   Отсутствие доехало отсутствием — правило соблюдено.
4. `STALE_SPEC`: докстринг `0004:8` ссылается на «тот же порядок, что в
   `goals/tenant_scope.py`». Файла `goals/tenant_scope.py` в `origin/dev` нет
   (`git ls-tree -r --name-only origin/dev goals/` — 17 файлов, такого нет).
   Ссылка на удалённый модуль.
5. Докстринг `0005:22` утверждает «целей на пилоте две». Это **датированное
   утверждение** (написано вместе с DRF-1472, ~04.09.2026), а не сегодняшний
   факт. Сегодняшнее число строк — `UNKNOWN_NOT_MEASURED`.

---

## 5. Писатели

**Писатель ровно один.** `git grep -n "ClientGoal" origin/dev -- '*.py'`
(41 попадание) даёт всего два места, создающих или меняющих строку вне тестов
и вне миграций:

| Место | Что делает |
|---|---|
| `goals/api.py:143-152` `_create_goal()` | `with transaction.atomic(): ClientGoal.objects.filter(client=client, is_active=True).update(is_active=False)` → `ClientGoal.objects.create(client=…, goal_key=goal_key or None, goal_text=(goal_text or "").strip() or None, source_channel=source_channel)` |
| `goals/api.py:144-146` | **единственное место во всём репозитории, где `is_active` становится `False`** (проверено `git grep -n "is_active" origin/dev -- goals/`) |

Внешних вызывающих `_create_goal` два, оба внутри `goals/api.py`:

1. `GoalSelectView.post:233-241` — прямой выбор (`goal_key` **или** `goal_text`),
   затем `_close_open_run()` и `_emit_goal_selected()`.
2. `GoalSelectView._answer_anketa:348-360` — финальный шаг анкеты
   (`expected.key == anketa.FINAL_STEP_KEY`), под `@transaction.atomic`.

Обе точки — за одной ручкой: `POST /api/v1/internal/me/goals/select/`
(`djangoProject/urls.py:62-63` → `goals/select_urls.py:7`).

### Кто может дойти до этой ручки

`permission_classes = [IsBotServiceWithVerifiedClient]`,
`authentication_classes = []` (`goals/api.py:193-194`). Контракт класса
(`users/permissions.py:134-207`): `Authorization: Bearer` строго равен
`settings.AYLA_INTERNAL_API_TOKEN` (`compare_digest`, пустая настройка = отказ
всем), плюс `X-External-User-ID`, разрешаемый в `User`. То есть
**Mini App не может писать цель напрямую** — только через сервис-хоп бота.

### PARTIAL — прямой путь не проверяет `goal_key` против `GoalOption`

`GoalSelectSerializer` (`goals/api.py:68-104`) проверяет ровно две вещи: что
заполнено ровно одно из `goal_key`/`goal_text`/`intent`/`answer`, и что при
выборе цели присутствует `source_channel`. Поле объявлено как
`serializers.SlugField(max_length=64)` — **никакой сверки с
`GoalOption.objects`**. В `post:233-238` значение уходит в `_create_goal` как
есть.

Контраст доказывает, что это пропуск, а не решение: на **анкетном** пути та же
проверка есть и была написана нарочно —
`goals/api.py:316-324`, `allowed = {key for key, _ in expected.options}`, с
комментарием `:317-319`: «Без `and expected.options`: на салоне без активных
`GoalOption` список финального шага пуст, и прежний вид проверки пропускал
ЛЮБОЙ слаг прямо в `ClientGoal.goal_key`». На прямом пути этой стражи нет
вовсе.

Тест `goals/tests/test_goal_layer.py:320-324` (`test_unknown_key_returns_none`)
создаёт строку с `goal_key="ghost"` **через ORM напрямую** и проверяет только,
что резолвер вернёт `None`. Он не утверждает, что ручка такой ключ отвергает,
— и не может: она его принимает.

Последствие — расхождение внутри одного слоя (см. §7 и §9.2).

---

## 6. Читатели

Все читатели `ClientGoal` в каталоге (полный список вне тестов и миграций):

| # | Место | Что читает | Что делает |
|---|---|---|---|
| R1 | `goals/decision_context.py:195-201` | активная цель, `order_by("-selected_at").first()` | кладёт в `known["goal"]` (или `None`) |
| R2 | `goals/decision_context.py:141-180` `_goal_is_resolved` | `goal_key` / `goal_text` | решает, нужен ли шаг `goal_clarification` |
| R3 | `goals/resolution.py:62-86` `resolve_goal_category_ids` | активная цель | `goal_key` → `GoalOption` → `GoalOptionCategory` → раскрытие в подкатегории; `goal_text` → **только точное совпадение с `label` (casefold)**, иначе `None` |
| R4 | `goals/wiring.py:132` `_log_unresolved` | `.exists()` | различает «цели нет» и «цель есть, но не разрешается» в логе |
| R5 | `users/catalog_recommendations_api.py:560-575` `_saved_goal_key` | активная цель | отдаёт `goal.goal_key` — **но только если `goal_category_ids_for(client) is not None`**, то есть только при включённом флаге |
| R6 | `users/home_api.py:237,258` | через `goal_category_ids_for(user)` | `RecommendationQuery(goal_category_ids=…)` для полки `nearby_specialists` |
| R7 | `users/recommendation_source.py:79,118` | через `goal_category_ids_for_key(need.goal_key)` | помечает кандидата совпадением по цели; **флаг здесь НЕ читается** — но `need.goal_key` приходит из R5, который читается |

Единственное чтение флага `GOAL_RESOLUTION_ENABLED` во всём репозитории —
`goals/wiring.py:66-68` (докстринг модуля это утверждает, `git grep`
подтверждает: вне `wiring.py`, `settings/base.py`, докстрингов и тестов
совпадений нет).

Читатели в боте (`ai-bot-platform@83ed56a`), все — **живое чтение по HTTP,
без кеша и без собственного хранения**:

| # | Место | Что делает |
|---|---|---|
| R8 | `apps/miniapp_api/views.py:3790-3820` `customer_decision_context` | прокси `GET /api/v1/customer/decision-context` → `JsonResponse(ayla_body)` дословно |
| R9 | `apps/miniapp_api/views.py:2996-3013, 3044-3045` | третье чтение дашборда → `_active_goals_from_context` → `payload["active_goals"]`; при отказе ключ **опускается**, не `[]` |
| R10 | `apps/adminconsole/clients.py:286-311` `_active_goal_fact` | «есть» / «нет» / «нет данных»; текст цели нарочно не показывается |
| R11 | `apps/nutrition_coach/goals.py:62-105` `active_goal` | `Goal(key, text)` или `None`; любое исключение → `None` |
| R12 | `apps/integrations/ayla/goals_client.py:425-455` `_reconcile_goal_select` | после `ReadTimeout` перечитывает документ и сверяет, прошла ли запись |

---

## 7. Представление в decision-context

`goals/decision_context.py:270-277` — форма документа, отдаваемого наружу:

```
{
  "version": 2,
  "known":       {"goal": {goal_key, goal_text, selected_at, source_channel} | null},
  "missing":     [ … ],
  "suggestions": [{key, label}, …],
  "intents":     [{id, label}, …],
  "next":        {"id": "browse_catalog", "label": "Найти услугу"}
}
```

Обёртка ответа — `users/response.py:9-14`: `success_response` возвращает
`{"data": <документ>}`. То есть по проводу едет **`{"data": {"version": 2, "known": …}}`**.

`_goal_payload` (`:103-109`) отдаёт четыре поля цели. `is_active`,
`created_at`, `updated_at`, `id` наружу не выходят.

**`CONTRADICTS_CANON` (канон 16):** `known.goal` — канонический скаляр, ровно
то, что должно исчезнуть. Полей `active_goals[]` и `relevant_goal_refs[]` в
каталоге не существует: `git grep -n "active_goals\|relevant_goal_refs\|current_goal" origin/dev -- '*.py'`
в `djangoproject-catalog` → **ноль совпадений**.

**`PARTIAL` — `_goal_is_resolved` и резолвер расходятся.**
`_goal_is_resolved` (`:165-166`) возвращает `True` при **любом** непустом
`goal_key`, не проверяя ни существования `GoalOption`, ни его `is_active`.
`resolve_goal_category_ids` (`:70-74`) на том же ключе вернёт `None`, если
`GoalOption` с таким ключом нет. Соединяя это с §5 (ручка принимает любой
слаг), получаем достижимое состояние: документ утверждает, что цель готова
и уточнять нечего, а выдача при этом ничего не фильтрует и пишет
`logger.warning("goal.unresolved …")` (`goals/wiring.py:134-139`). Наружу
это состояние никак не названо.

**`CONTRADICTS_CANON` (канон 17):** шаги `area` и `feeling` живы в коде —
`goals/anketa.py:65-88`, `ANKETA_STEPS` из двух `AnketaStep` с ключами
`"area"` и `"feeling"`, `TOTAL_STEPS = 3`. Решение об их удалении из P0
принято, в `origin/dev@95c917e` не выложено. Причину не переисследовал.

**`PARTIAL` — «анкета не ворота» держится одной строкой.**
`next_step_hint` (`:257-260`) присутствует **безусловно**; комментарий
`:238-256` объясняет, что раньше он молчал, пока в `missing` был вопрос, и
это делало анкету воротами. Механизм рабочий, но выход из анкеты обеспечен
только тем, что клиент отрисует `next`; серверной стражи, которая упала бы,
если `next` исчезнет, нет.

---

## 8. Ответы A1–A5

### A1. Есть ли, кроме `ClientGoal`, другие рантаймовые authoritative Goal-сущности?

**Ответ: authoritative сегодня — только `ClientGoal`. Ещё три сущности
goal-формы существуют, и ни одна из них не является authoritative Goal —
каждая по своей, замеренной причине.**

| Кандидат | Где | Класс | Почему не authoritative Goal |
|---|---|---|---|
| `wellness.DesiredOutcome` | `djangoproject-catalog:wellness/models.py:23-90`; схема выложена миграцией `wellness/0001_initial.py` (`CreateModel DesiredOutcome` + `AddConstraint desiredoutcome_direction_or_numeric_present`) | `BLOCKED_BY_GATE` | Единственный публичный писатель — `wellness/services.py:106-119` `record_outcome`, тело которого целиком: `return goal_intention_gate(attestation, purpose=Purpose.PROCESSING)`. `goal_intention_gate` (`:70-85`) для `purpose=processing` возвращает `GateDecision(allowed=False, reason_code="scope_not_approved")` **безусловно**, даже с валидной attestation. Флага обхода нет. Читатель (`wellness/context_read.py:66-74`) при закрытом гейте отдаёт `{"plan": None, "outcomes": [], "gated": {…}}`, не обращаясь к таблице. То есть таблица есть, писать в неё нечем. |
| `nutrition.NutritionProfile.goal` | `djangoproject-catalog:nutrition/models.py:377-418` — `TextChoices(lose/maintain/gain/tone)`, `blank=True, default=""` | `EXISTS`, но **другой предмет** | Durable и записываемый, но это параметр расчёта норм (Mifflin-St Jeor × activity × goal_factor), а не желаемый исход человека. Живёт в `OneToOne`-профиле, отдельное пространство ключей, никакой связи с `GoalOption`/`ClientGoal` в коде нет. |
| `catalog.CatalogService.goals` (бот) | `ai-bot-platform:apps/catalog/models.py:131`, `JSONField(default=list)`; миграции `catalog/0001_initial.py:245`, `catalog/0020_…:532` | `EXISTS`, **зеркало, не источник** | jsonb-теги «цели услуги», приезжающие из каталога (`services/goal_resolution.CategoryGoalIndex` → `services/serializers.py:199-201`). Это «услуга → цели», а не «человек → цель». ADR-0009: зеркало не источник. |

Собственного durable-хранилища цели человека в боте **нет**:
`git grep -n -i "goal" origin/dev -- 'apps/*/models.py' 'apps/*/migrations/*.py'`
даёт четыре попадания, все — вышеупомянутый `CatalogService.goals` и
комментарий в `apps/promotions/models.py:44`.

В `ayla-ai-core@d72a5de` слова `goal` нет вообще:
`git grep -n -l -i "goal" origin/main` → пустой вывод. Класс: `MISSING`
(измерено, не предположено).

**Запрещённый вывод, который здесь НЕ делается:** «`GoalOption` →
`DesiredOutcome` автоматически». Ни одной строки кода, связывающей эти две
сущности, не существует; см. §10 о том, почему такое отображение и
невозможно без выдумывания значений.

### A2. Чем `ClientGoal` работает на самом деле?

**Всё сразу, но не поровну.** По замеренным потребителям:

| Роль | Есть? | Доказательство |
|---|---|---|
| **Желаемый исход** | `PARTIAL` | Модель хранит формулировку/ключ, но ни направления, ни измеримого состояния, ни срока, ни прогресса. Докстринг `:1` называет её «Transformation Goal — durable-факт выбора». Факт выбора — да; исход как измеримая величина — нет. |
| **Выбранное предпочтение** | `EXISTS` | `goal_key` = ключ курируемого чипа `GoalOption`; `_create_goal` пишет ровно то, что человек нажал/написал. |
| **Фильтр рекомендаций** | `BLOCKED_BY_GATE` | R5/R6/R7 через `goals/wiring.py:81` — при `GOAL_RESOLUTION_ENABLED=false` (default, `settings/base.py:506-508`) `goal_category_ids_for` возвращает `None` **до** обращения к резолверу, и выдача остаётся прежней. |
| **Состояние интерфейса** | `EXISTS` | `known.goal` определяет, какой `missing` показать (`decision_context:206-226`), какие `intents` предложить (`:228-234`), и — в боте — что нарисовать в `active_goals` дашборда (`views.py:3044-3045`) и что написать в карточке админки (`clients.py:309-311`). |
| **Событие аналитики** | `EXISTS` | `goals/api.py:107-121` `_emit_goal_selected` → `AnalyticsEvent(event_name="goal_selected", payload={goal_key, has_text, source_channel})`. Дословный `goal_text` в BI **не уходит нарочно** (`analytics/event_catalogue.py:95-97`). |

Про приоритет ролей код высказывается прямо:
`users/catalog_recommendations_api.py:488-493` — «Курируемая цель говорит,
только когда человек молчит: сказанное сейчас старше выбранного когда-то
(OD-1)»: `goal_key = None if goal else _saved_goal_key(request.user)`.
Канон 4 в этой точке **соблюдён**.

### A3. Кто считает `ClientGoal` источником истины?

Шесть потребителей, из них ни один не хранит копию:

1. `goals/decision_context.build_decision_context` — строит документ поверх
   таблицы на **каждый** запрос, в БД не кладёт (`:12-13`).
2. `goals/resolution.resolve_goal_category_ids` — цель → категории.
3. `users/home_api._nearby_specialists` — полка «рядом» (за флагом).
4. `users/catalog_recommendations_api.CatalogRecommendationsView` — полки 2 и 3
   Mini App (за флагом).
5. `apps/miniapp_api/views.customer_wellness_today` (бот) — блок «цель» на
   дашборде.
6. `apps/nutrition_coach/goals.active_goal` (бот) — цель в контекст питания;
   оттуда в `apps/orchestrator/nutrition_context.py:237` и в блок промпта DM.

Плюс `apps/adminconsole/clients.py` — карточка клиента для оператора
(факт «есть/нет», без текста).

**Ни одного потребителя, который бы кешировал цель или дублировал её в свою
таблицу, не найдено** — ни в каталоге, ни в боте. `goals_client.py` кеша не
имеет вовсе; единственное состояние — пул соединений
(`keepalive_expiry=50.0`, `:84`).

### A4. Что нельзя мигрировать без потери provenance

| # | Что | Почему невозможно без потери |
|---|---|---|
| A4-1 | **`goal_text` при `goal_key IS NULL`** | Дословные слова человека. Ни из чего не восстанавливаются. Целевая сущность `wellness.DesiredOutcome.statement_text` — `TextField` **без `null=True` и без `blank=True`** (`wellness/models.py:52-54`), то есть обязательная. |
| A4-2 | **Строка с `goal_key`, но `goal_text IS NULL`** | Такая строка legal по `clientgoal_key_or_text_present` и создаётся любым нажатием чипа (`_create_goal:150` пишет `goal_text=None`). У неё **нет ни одного слова человека**. Перенести её в `DesiredOutcome` нельзя, не подставив в обязательный `statement_text` подпись чипа — то есть не подделав формулировку (канон 12 это прямо запрещает). |
| A4-3 | **`DesiredOutcome.target` (обязательный `SlugField`)** | В `ClientGoal` нет ничего, что им является: `goal_key` — ключ подсказки каталога («relax»), а `target` — ключ объекта результата («body_weight», «edema»). Отображение между ними нигде не существует. |
| A4-4 | **`desiredoutcome_direction_or_numeric_present`** | `CheckConstraint`, требующий `direction` **или** `desired_state_numeric` (`wellness/models.py:80-86`). `ClientGoal` не хранит ни того, ни другого. Значит **каждая существующая строка `ClientGoal` нарушила бы это ограничение**, и автоматический перенос невозможен по схеме, а не по вкусу. |
| A4-5 | **Пара `selected_at` / `created_at`** | Момент выбора человеком и момент записи строки различены нарочно. В `DesiredOutcome` есть только `created_at (auto_now_add)`. Слияние двух в одно необратимо стирает «когда человек это выбрал». |
| A4-6 | **`is_active=False`** | Такая строка **не была закрыта человеком**: единственный код, гасящий флаг, — `_create_goal:144-146`, побочный эффект выбора следующей цели. `DesiredOutcome.Status` знает ровно два значения — `open` / `closed_by_user`. Отобразить в `closed_by_user` значит утверждать поступок человека, которого не было; канон 14 запрещает и `ARCHIVED`, и `ACHIEVED`. Состояния, соответствующего действительности, в целевом перечислении нет. |
| A4-7 | **`source_channel`** | Обязательное поле без «неизвестно». У перенесённой строки, происхождение которой утеряно, честного значения не будет; ближайшая по смыслу целевая сущность канала вообще не хранит. |
| A4-8 | **`GoalAnketaRun.goal` (`SET_NULL`)** | Связь «этот проход анкеты породил вот эту цель» — единственный след того, откуда цель взялась при анкетном пути. `on_delete=SET_NULL` означает: удаление строки `ClientGoal` **молча** обнулит связь, не подняв ошибку. Провенанс исчезнет без единого признака. |

### A5. Реальное содержимое миграций `ClientGoal`

Изложено построчно в §4. Кратко и без пересказа имён: `0001` создаёт таблицу
сразу с обоими ограничениями и индексом; `0002` целевой таблицы не касается;
`0003` добавляет `tenant` + индекс; `0004` — единственная миграция данных,
проставляет тенанта только там, где он однозначен, и оставляет NULL там, где
нет; `0005` снимает и индекс, и колонку, оставляя `0003`/`0004` в истории,
потому что они применены на боевом пилоте. Текущий набор колонок тождественен
`0001`.

---

## 9. Пути — по рёбрам

Ни одной подразумеваемой стрелки. Каждое ребро — файл и строка.

### PATH 1 · выбор цели в Mini App → … → `ClientGoal` → … → рекомендация

| # | Ребро | Класс | Доказательство |
|---|---|---|---|
| 1.1 | Экран выбора цели существует | `EXISTS` | `ai-bot-platform:apps/miniapp/src/screens/GoalSelectScreen.tsx`; входы — маршрут `/customer/goal-select` (`App.tsx:1357`), deep-link `open_goal_select` (`lib/max-sdk.ts:195`), `GoalInviteCard.tsx:130`, дашборд (`CustomerWellnessDashboardScreen.tsx:273-280`), корень (`CustomerEntryScreen.tsx:49,75`) |
| 1.2 | Экран → бот по HTTP | `EXISTS` | `lib/customer-goals.ts:146-166`: `GET /decision-context`, `POST /goals/select`, база `API_BASE="/api/v1/customer"` (`lib/api.ts:4`). Тела: `{goal_key}` / `{goal_text}` / `{intent:"need_guidance"}` / `{intent:"start_anketa"}` / `{answer:{step,option_key}}` / `{answer:{step,text}}`, у всех `source_channel:"miniapp"` (`customer-goals.ts:134-143`; отправки — `GoalSelectScreen.tsx:323-326, 443-448, 483-485, 536-538, 548-550`) |
| 1.3 | Бот принимает | `EXISTS` | `apps/miniapp_api/urls.py:124-133`; `views.customer_decision_context:3790-3820`, `views.customer_goal_select:3823-3879`. `@require_init_data`. Единственная проверка тела — `content_type == "application/json"` и `isinstance(parsed, dict)` (`:3848-3856`); форму валидирует Ayla |
| 1.4 | Бот → каталог | `EXISTS` | `apps/integrations/ayla/goals_client.py:307-313, 316-349`: `GET/POST {AYLA_BASE_URL}/api/v1/internal/me/{decision-context,goals/select}/`, заголовки `Authorization: Bearer {AYLA_INTERNAL_API_TOKEN}` + `X-External-User-ID: bot:{channel}:{channel_user_id}` (`:237-242`). Payload пробрасывается **как есть** (`:337-340`). Таймауты `connect=6.0/read=5.0` (`:70-72`), брейкер 5/60с→30с (`:86-88`). Кеша нет. Тихих дефолтов нет: всё — исключения `GoalsConfigError`/`GoalsBadRequest`/`GoalsUnavailable` |
| 1.5 | Каталог создаёт `ClientGoal` | `EXISTS` | `goals/api.py:205-248` и `:270-365` → `_create_goal:143-152`. Пробел: `goal_key` на прямом пути не сверяется с `GoalOption` (§5) — **ребро есть, страж отсутствует**, класс `PARTIAL` для стража |
| 1.6 | Запись → событие воронки | `EXISTS` | `goals/api.py:241, 360` → `_emit_goal_selected:107-121` → `AnalyticsEvent("goal_selected")`. `goal_text` в payload не идёт |
| 1.7 | Запись → закрытие прохода анкеты | `EXISTS` | `_close_open_run:155-170` вызывается на **каждом** прямом выборе |
| 1.8 | Ответ каталога → экран | `EXISTS` | `success_response` (`users/response.py:9-14`) → `{"data": документ}`; бот отдаёт дословно (`views.py:3820`); фронт разворачивает `env.data` (`customer-goals.ts:147-151, 161-165`). **Стрелка сходится** |
| 1.9 | Ответ каталога → python-читатели бота | **дефект, см. §9.1** | те же байты, но `goals_client.py:440`, `views.py:2746`, `clients.py:307`, `nutrition_coach/goals.py` читают `known` **с корня**, минуя `data` |
| 1.10 | Цель → тело запроса рекомендаций | `MISSING` | `lib/api.ts:379-380`: `fetchRecommendations = () => request("/recommendations", {method:"POST"})` — **тела нет вовсе**. Бот: `views.py:2610-2625` без `Content-Type` получает `{}` и шлёт его в `fetch_recommendations`. Ни одна строка не подмешивает цель. `STALE_SPEC`: докстринг `views.py:2526` перечисляет `goal` среди полей тела — отправителя у этого поля нет |
| 1.11 | Цель → рекомендация, вывод на стороне каталога | `BLOCKED_BY_GATE` | Каталог выводит цель сам, по `X-External-User-ID`: `users/catalog_recommendations_api.py:492-493` `goal_key = None if goal else _saved_goal_key(request.user)`. Но `_saved_goal_key:568` отдаёт ключ **только если** `goal_category_ids_for(client) is not None`, а `goals/wiring.py:81-82` возвращает `None`, пока `GOAL_RESOLUTION_ENABLED` не `true`. Умолчание — `false` (`settings/base.py:506-508`). Значение на боевом контуре в репозиториях не задано (`git grep` по `.github/`, `docker*`, `*.env*` — пусто) → `UNKNOWN_NOT_MEASURED` |
| 1.12 | Цель → навигация | `EXISTS` | Сервер отдаёт `next={"id":"browse_catalog"}` (`decision_context.py:257-260`); экран отображает таблицей `NEXT_ROUTES = { browse_catalog: "/customer/catalog" }` (`GoalSelectScreen.tsx:115-117`). **Цель при переходе не передаётся** — ни параметром маршрута, ни состоянием |

**Итог PATH 1:** от нажатия чипа до строки в БД путь **сплошной**. От строки
в БД до выдачи — **перекрыт флагом**, и цель туда попадает не по проводу, а
выводом каталога из заголовка личности.

### PATH 2 · фраза в MAX DM → … → discovery matcher → … → durable Goal?

| # | Ребро | Класс | Доказательство |
|---|---|---|---|
| 2.1 | Входящий текст MAX → хендлер | `EXISTS` | `apps/ingress/views.py:73` `max_webhook()` (`apps/ingress/urls.py:17`) → Redis-стрим (`:137`, `channel="max"`) → `apps/channels/handlers.py:56/83` → `apps/channels/max/handler.py:1143` `handle_global_max_event` → `:1206` `_handle_global_max_event_inner` |
| 2.2 | Хендлер → LLM-консьерж | `EXISTS` | `handler.py:2170` `orchestrate_turn(TurnContext(surface=SURFACE_GLOBAL, tenant=None))` → `apps/orchestrator/turn_seam.py:179,188` → `concierge.py:1518` `generate_concierge_reply`. Детерминированный обход — `handler.py:1978` |
| 2.3 | LLM-тул → парсер запроса | `EXISTS` | тул `show_masters` (`apps/orchestrator/discovery.py:281`) принимает `city`, `specialization`, `services` — **цели среди аргументов нет**; `concierge.py:1857,1872,1922` → `apps/marketplace/discovery.py:1490,1602,1643` `_matched_services(_parse_query(specialization))` |
| 2.4 | Парсер → `_match_goal_keys` | `EXISTS` | `apps/marketplace/discovery.py:775` (свободный текст) и `:833` (`parse_stems`, путь с кнопки) |
| 2.5 | «discovery matcher» как модуль/термин | `MISSING` как сущность | `git grep -n "matcher" origin/dev -- 'apps/**/*.py'` вне тестов даёт только `catalog/services/linking.py:192`, `orchestrator/nutrition_global.py`, `orchestrator/personal_surface.py`, `persona/memory_commands.py`, `apps/skills/*` — ни одно не про discovery. Роль разложена по `apps/marketplace/discovery.py`: `_parse_query:746` + `_match_goal_keys:716` + `_service_match_q:1016` + `_goal_row_q:1043` |
| 2.6 | `_match_goal_keys` — что делает | `EXISTS` | `apps/marketplace/discovery.py:716-744`. Словарь **не захардкожен**: `_known_goals():649` читает зеркало каталога `CatalogService.all_tenants.filter(is_active=True).values_list("goals", flat=True).distinct()`. Матчит токены запроса против **слов метки цели**: `_goal_label_words:688` = `re.findall(r"\w+", label.casefold())` минус слова короче `_GOAL_MIN_PREFIX=4`; `_token_names_word:700` = сравнение префиксов длиной `_STEM_LEN=6`. Гейт жёсткий: цель проходит, только если **каждый** токен запроса назван каким-то словом метки. Возвращает список ключей |
| 2.7 | Ключи целей → фильтр каталога | `EXISTS`, и это **единственный** потребитель | `discovery.py:1043` `_goal_row_q`: `Q(services_offered__service__goals__contains=[{"key": key}])`, OR по ключам, склеен с `_service_row_q()`; роутинг `:1067` `_relation_match_q`. При непустом совпадении `_parse_query:775` **обнуляет стемы** (DRF-1324) — запрос перестаёт быть поиском по названию |
| 2.8 | Ключи целей → durable Goal | `MISSING` | `git grep -n "post_goal_select" origin/dev` даёт в рантайме **ровно одного вызывающего** — `apps/miniapp_api/views.py:3859` внутри `customer_goal_select`, то есть HTTP-эндпоинт Mini App. Импортов `goals_client` в `apps/channels/**` нет вообще. Из `_handle_global_max_event_inner` вызывающих нет. Показано отсутствием вызывающих, не догадкой |
| 2.9 | Извлечение цели LLM из свободного текста | `MISSING` | `git grep -n "goal_key\|goal_text" origin/dev -- apps/orchestrator/**` → **ноль**. Модель заполняет `specialization`/`services`/`city`. Результат уходит в SQL каталога, в отрисованный ответ и в журнал отказов (`concierge.py:1956` `remember_refusal`) — в БД как цель никуда |
| 2.10 | Гейт/флаг, выключающий запись цели из DM | `MISSING` (не `BLOCKED_BY_GATE`) | семейства `FEATURE_*` в `apps/`+`config/` нет. Гейты, которые есть (`NUTRITION_COACH_ENABLED`, `config/settings/base.py:1556`; `interpretation_eligible`), влияют на **чтение**. Записи нет как кода — снимать нечего |
| 2.11 | Обратное ребро: DM **читает** цель | `EXISTS` | `apps/nutrition_coach/goals.py:87` `active_goal` → `apps/orchestrator/nutrition_context.py:237` → блок в промпте DM (`handler.py:~2148`) |

**Итог PATH 2:** `MAX DM → хендлер (EXISTS) → LLM show_masters (EXISTS) →
_parse_query/_match_goal_keys (EXISTS) → SQL-фильтр каталога (EXISTS) →
durable Goal (MISSING)`. Цель, распознанная из свободной фразы, существует
ровно на время одного SQL-запроса и умирает вместе с ним.

**Канон 11 соблюдён фактически:** `_match_goal_keys` нигде не объявлен
authority кандидата, потому что его результат не покидает
`ParsedQuery.goals` → `_relation_match_q`. Единственное упоминание вне кода —
`ai-bot-platform:docs/specs/RECOMMENDER_GAP_RESEARCH.md:231` (**документ**,
не рантайм).

---

## 9.1. Подтверждённое противоречие — конверт ответа

**Доказано кодом обеих сторон, а не предположено.**

Каталог заворачивает **любой** успешный ответ:
`djangoproject-catalog:users/response.py:9-14`

```python
def success_response(data, meta=None, status_code=200):
    body = {"data": data}
```

`DecisionContextView.get:187` и `GoalSelectView` возвращают именно
`success_response(build_decision_context(...))`. По проводу едет
`{"data": {"version": 2, "known": {...}, ...}}`.

Бот **не разворачивает**: `goals_client._request:293-304` возвращает
`resp.json()` как есть (единственные проверки — что это dict и что статус 200),
`fetch_decision_context:307-313` возвращает результат `_request` без обработки.

Дальше расхождение:

| Потребитель | Как читает | Верно? |
|---|---|---|
| `apps/miniapp/src/lib/customer-goals.ts:147-151, 161-165` | `const env = await request<DecisionContextEnvelope>(…); return env.data` | **да** |
| `apps/integrations/ayla/goals_client.py:440` | `known = document.get("known")` | нет |
| `apps/miniapp_api/views.py:2746` | `known = doc.get("known")` | нет |
| `apps/adminconsole/clients.py:307` | `known = doc.get("known") if isinstance(doc, dict) else None` | нет |
| `apps/nutrition_coach/goals.py:108` `_goal_from_document` | `known.goal` из документа | нет |

Проявления, вытекающие из кода напрямую:

* `payload["active_goals"]` в `/customer/wellness/today` — всегда `[]` при
  живой Ayla, независимо от цели человека (`views.py:2744-2749` возвращает
  `[]` при `known is None`).
* Карточка клиента в админке — всегда «нет», никогда «есть»
  (`clients.py:307-311`).
* `nutrition_coach.active_goal()` — всегда `None`; цель никогда не попадает в
  контекст питания и в промпт DM.
* `_reconcile_goal_select` после `ReadTimeout` — всегда `reconcile_miss`
  (`:442-448`), то есть **успешная запись отчитывается как неудача**.

Почему тесты этого не ловят: фикстуры бота построены вручную и плоские —
`apps/miniapp_api/tests/test_wellness_today.py:77-84`
(`{"version": 1, "known": {"goal": None}, …}`). Обе стороны данных построил
один автор, поэтому проверяется согласованность фикстуры, а не системы. Лишнее
подтверждение того же: фикстура объявляет `"version": 1`, а каталог отдаёт
`"version": 2` (`decision_context.py:271`).

**Что здесь НЕ измерено:** боевой ответ ручки не снимался. Дефект доказан
кодом обеих сторон; его проявление на `api-dev.gobeauty.site` —
`UNKNOWN_NOT_MEASURED`.

## 9.2. Прочие подтверждённые противоречия

| # | Противоречие | Класс | Строки |
|---|---|---|---|
| C1 | `clientgoal_one_active_per_client` против канона 2 | `CONTRADICTS_CANON` | `goals/models.py:85-89`, `goals/migrations/0001_initial.py:89-93` |
| C2 | `known.goal` — канонический скаляр против канона 16 | `CONTRADICTS_CANON` | `goals/decision_context.py:201` |
| C3 | `active_goals` бота — список длиной ≤ 1, целиком выведенный из скаляра: `_active_goals_from_context` возвращает `[]` или `[entry]` (`return [entry]`, `:2769`). Это буквально `active_goals[0] == current_goal`, запрещённое каноном 16 | `CONTRADICTS_CANON` | `ai-bot-platform:apps/miniapp_api/views.py:2719-2769`; контракт фронта `apps/miniapp/src/lib/customer-wellness.ts:187-190` |
| C4 | шаги `area`/`feeling` живы против канона 17 | `CONTRADICTS_CANON` | `goals/anketa.py:65-88` |
| C5 | нет способа прекратить цель, не выбрав новую (канон 15). Единственное место, гасящее `is_active`, — побочный эффект `_create_goal`. Ручка не принимает ни `intent` прекращения, ни `DELETE` | `MISSING` | `goals/api.py:144-146`; `GoalSelectSerializer:79-81` знает только `need_guidance` / `start_anketa` |
| C6 | нет 4 состояний жизненного цикла и нет отдельной оси свежести (канон 13) | `MISSING` | `git grep -i "paused\|achieved\|archived" origin/dev -- goals/` → ноль |
| C7 | `_goal_is_resolved` называет готовой цель, которую резолвер не разрешает | `PARTIAL` | `decision_context.py:165-166` против `resolution.py:70-74` |
| C8 | прямой путь `goal_key` не сверяется с `GoalOption`, анкетный — сверяется | `PARTIAL` | `goals/api.py:233-238` против `:316-324` |
| C9 | `title` в `active_goals` заполняется подписью чипа, когда своих слов нет; поля происхождения рядом нет, потребитель не может отличить слова человека от курируемой подписи | `PARTIAL` | `views.py:2751-2759` |
| C10 | `ClientGoal` не входит в поверхность удаления персданных. Единственный глагол стирания — `users/personal_context_erasure.erase_personal_context:110-154`, и он трогает **только** `UserPersonalContext` (`:123-146`). Ручка 152-ФЗ `InternalPersonalDataDeleteView.delete:162-180` вызывает ровно его (`:172`), больше ничего. Дословный `goal_text` — личная формулировка, которая, по собственной оценке кода админки (`clients.py:289`), «может касаться здоровья», — переживает удаление персданных | `MISSING` | `personal_context_erasure.py:110-154`, `personal_data_api.py:136-180` |
| C14 | `nutrition_coach/goals.py:120` защищается от поля `is_active` в документе (`if goal.get("is_active") is False: return None`), которого `_goal_payload` (`decision_context.py:103-109`) никогда не отдаёт. Проверка недостижима; читается как знание о контракте, которого у контракта нет | `STALE_SPEC` | — |
| C11 | `ClientGoal` не зарегистрирован в админке (`goals/admin.py` не существует; `git grep "ClientGoal" origin/dev -- '*admin*'` → пусто) — посмотреть цель человека в каталоге нечем | `MISSING` | — |
| C12 | докстринг `goals/wiring.py:27` «на пилоте `ClientGoal` = 0» без даты, при том что `0005:22` говорит «целей на пилоте две» | `STALE_SPEC` | оба — датированные утверждения разных дней, не противоречие данных |
| C13 | докстринг `0004:8` ссылается на `goals/tenant_scope.py`, которого в дереве нет | `STALE_SPEC` | — |

---

## 10. Опасности миграции

Не план миграции. Перечень мест, где неаккуратное движение теряет то, что не
восстанавливается.

1. **`GoalAnketaRun.goal` — `SET_NULL`, а не `PROTECT`**
   (`goals/models.py:128-135`). Удаление или пересоздание строк `ClientGoal`
   **молча** обнулит связь «этот проход анкеты породил эту цель». Ни ошибки,
   ни следа. Единственный уцелевший след происхождения анкетной цели исчезнет
   без признака. Сравните: `client` — `PROTECT`, то есть человека удалить
   нельзя, а цель — можно.

2. **Каждая существующая строка нарушает целевой `CheckConstraint`.**
   `desiredoutcome_direction_or_numeric_present` требует `direction` или
   `desired_state_numeric`; `ClientGoal` не хранит ни того, ни другого.
   Массовый перенос упадёт на первой же строке — и это лучший из исходов;
   худший — кто-то подставит `direction="maintain"` как «нейтральное»
   умолчание, и через день «человек не говорил» станет неотличимо от
   «человек выбрал».

3. **`statement_text` обязателен, а у чиповой цели слов нет** (A4-2).
   Соблазн заполнить его `GoalOption.label` максимально велик, потому что
   подпись всегда под рукой (она едет в том же документе, `suggestions[]`), —
   и бот уже делает ровно это для `title` (`views.py:2754-2757`). Один шаг от
   отображения до записи, и подделка формулировки станет durable.

4. **`is_active=False` не имеет честного целевого состояния** (A4-6). Любое
   автоматическое отображение утверждает поступок человека, которого не было.

5. **`goal_key` — слаг без FK.** Переименование или удаление `GoalOption` не
   тронет исторические строки и не поднимет ошибку: `GoalOptionCategory` имеет
   `on_delete=CASCADE` на `goal_option` (`services/models.py:881-885`), то есть
   связи цели с категориями исчезнут вместе с подсказкой, а `ClientGoal.goal_key`
   останется висеть указателем в пустоту. Резолвер вернёт `None`, документ при
   этом продолжит называть цель готовой (C7).

6. **Слияние `selected_at` и `created_at`** необратимо стирает различие
   «когда человек выбрал» / «когда строка записана» (A4-5). Для целей,
   мигрированных пачкой, `created_at` будет датой миграции — и по нему потом
   посчитают «свежесть».

7. **`0004` уже применена на боевом пилоте** (`0005` это утверждает со
   ссылкой на `ddda863` / PR #286). Любая правка истории миграций `goals`
   оставит на контуре строки `django_migrations`, ссылающиеся в пустоту.

8. **Отсутствие `ClientGoal` в поверхности удаления персданных** (C10)
   означает, что при переносе цели человека, попросившего об удалении, могут
   оказаться единственной уцелевшей копией его дословных слов.

9. **Порядок «сначала данные, потом схема» здесь уже нарушался безопасно и
   это стоит повторить**: `0003`/`0004`/`0005` разнесены по файлам нарочно —
   докстринг `0004:3-6` объясняет, почему схема и данные откатываются по
   разным причинам и в разном темпе. Обратная операция `0004` корректна
   только потому, что до `0003` колонки не было; для колонки с историей такой
   `unbackfill` был бы потерей.

---

## 11. Решения владельца — только оставшиеся

Формат по брифу. **Рекомендации не даются.** Каждое решение проверено на
критерий: минимум два продуктово различающихся допустимых варианта, и канон
между ними не выбирает.

### OD-GWP-1 · Какое состояние получают исторические строки при переходе к четырём состояниям жизненного цикла

**Runtime evidence.** `ClientGoal.is_active` — единственная ось состояния
(`goals/models.py:71`). Гасится ровно в одном месте — `goals/api.py:144-146`,
внутри `_create_goal`, как побочный эффект выбора **следующей** цели. Человек
при этом не совершал никакого действия «закончить с этой целью»: ручка такого
входа не принимает вовсе (`GoalSelectSerializer:79-81` знает только
`need_guidance` и `start_anketa`). То есть каждая строка с `is_active=False`
на контуре — это «система погасила, потому что человек назвал другую цель».

**Существующий канон.** Канон 13: жизненный цикл — `ACTIVE` / `PAUSED` /
`ACHIEVED` / `ARCHIVED`. Канон 14: `is_active=False` **нельзя** автоматически
трактовать как `ARCHIVED` или `ACHIEVED`. Канон 2: несколько `ACTIVE`
разрешены. Канон 6: завершённая запись не доказывает достижение.

**Почему канон не отвечает.** Канон 14 закрывает два состояния из четырёх.
Остаются `ACTIVE` и `PAUSED`. `PAUSED` подразумевает, что человек приостановил
— он этого не делал. `ACTIVE` подразумевает, что цель в силе — он её не
подтверждал. Пятого состояния канон 13 не допускает. Состояния, соответствующего
фактическому происхождению этих строк, в каноне нет, и канон не говорит, какое
из неточных выбрать.

**Вариант A.** Исторические `is_active=False` становятся `ACTIVE` — на том
основании, что человек никогда не говорил, что перестал этого хотеть, а
канон 2 несколько активных разрешает.
*Последствия:* человек, четыре раза за пилот менявший цель, при первом же
открытии приложения увидит четыре активные цели, ни одну из которых он в этом
качестве не подтверждал. Все они попадут в `active_goals[]` и, при включённом
`GOAL_RESOLUTION_ENABLED`, в фильтр выдачи.

**Вариант B.** Исторические `is_active=False` остаются durable-фактами вне
жизненного цикла: они хранятся, участвуют в корпусе формулировок (OD-2), но
не выражаются ни одним из четырёх состояний и наружу как цели не выходят.
*Последствия:* поверхность целей начинает жизнь только с тех целей, которые
человек подтвердит после перехода; прошлые выборы перестают быть видимыми
человеку, оставаясь в базе. Понадобится отдельное имя для «не в жизненном
цикле», иначе молчаливая пустота станет отказом без имени.

---

### OD-GWP-2 · Может ли цель, созданная нажатием чипа, стать измеримым результатом без слов человека

**Runtime evidence.** `_create_goal:147-152` при нажатии чипа пишет
`goal_key=<slug>`, `goal_text=None` — так устроен весь путь
`GoalSelectScreen.tsx:483-485` → `{goal_key, source_channel}`. Ограничение
`clientgoal_key_or_text_present` такую строку допускает. У неё **нет ни одного
слова человека**. Целевая сущность измеримого результата, уже выложенная
схемой, требует слова: `wellness/models.py:52-54`,
`statement_text = models.TextField(...)` — без `null=True`, без `blank=True`.
Дополнительно `desiredoutcome_direction_or_numeric_present` (`:80-86`) требует
направления или числа, которых `ClientGoal` не хранит вовсе.

**Существующий канон.** Канон 8: клик по курируемой опции — достаточное явное
действие **для создания цели**; отдельный экран подтверждения не обязателен.
Канон 12: идентичность цели и формулировка человека различаются; нельзя
подделывать формулировку текстом чипа. Канон 1: Goal — durable желаемый исход.

**Почему канон не отвечает.** Канон 8 говорит про создание **цели**. Про то,
достаточно ли клика для перехода цели в **измеримый результат**, он не
говорит. Канон 12 запрещает подделывать формулировку, но не говорит, обязана
ли она вообще существовать. Два требования сходятся в точке, где канон молчит:
чиповая цель имеет идентичность и не имеет формулировки.

**Вариант A.** Формулировка человека необязательна: измеримый результат может
существовать при пустой формулировке, и отсутствие слов доезжает отсутствием
(нулевое поле, а не подставленная подпись чипа).
*Последствия:* клик остаётся одним движением, воронка не удлиняется; корпус
формулировок (OD-2) перестаёт пополняться теми, кто выбирает чипами — то есть,
судя по устройству экрана, большинством; на всех поверхностях появляется
состояние «цель есть, слов нет», которому нужно своё имя и своя отрисовка.

**Вариант B.** Формулировка человека обязательна для измеримого результата:
клик создаёт цель (канон 8 соблюдён), но переход к измеримому результату
требует, чтобы человек сказал это своими словами.
*Последствия:* корпус формулировок пополняется всегда; появляется шаг, которого
сегодня нет, и он ложится ровно на тот путь, который канон 8 сделал коротким;
цели, созданные чипом до перехода, останутся без результата, пока человек не
вернётся и не скажет.

---

### OD-GWP-3 · Где дословная формулировка человека вправе появляться

**Runtime evidence.** Один и тот же `goal_text` сегодня обрабатывается по трём
разным политикам, и каждая записана намеренно:

* **не уходит** в аналитику: `analytics/event_catalogue.py:95-97` — «payload
  carries goal_key/has_text/source_channel, NEVER the verbatim goal_text
  (personal phrasing stays in goals.ClientGoal, not in BI)»; `_emit_goal_selected`
  шлёт `has_text: bool`, не текст;
* **не показывается** оператору: `apps/adminconsole/clients.py:286-292` —
  «Сам текст цели не показываем: формулировка может касаться здоровья»;
  наружу идёт «есть» / «нет» / «нет данных»;
* **показывается** на дашборде: `views.py:2751` `title = (goal.get("goal_text") or "").strip()`
  → `payload["active_goals"]` → `customer-wellness.ts:187-190`; и **уходит
  целиком** в документ состояния — `decision_context._goal_payload:103-109`
  отдаёт `goal_text` как есть, `PROMPT_GOAL_CLARIFICATION:65-68` подставляет
  первые 200 символов формулировки прямо в текст вопроса.

**Существующий канон.** Канон 12: идентичность цели и формулировка человека
различаются; нельзя подделывать формулировку текстом чипа.

**Почему канон не отвечает.** Канон 12 запрещает **подмену** формулировки. Он
не говорит, каким поверхностям формулировка вообще полагается. Сегодняшнее
состояние — не политика, а три независимых локальных решения, принятых в трёх
тикетах; при появлении новых поверхностей (план, напоминания, салонная сторона)
правило придётся вывести заново, и оно разъедется молча — ровно как разъехались
`known` в §9.1.

**Вариант A.** Дословная формулировка — собственность человека и возвращается
ему на любой поверхности, где зритель — он сам; всем прочим потребителям
(BI, оператор, салон) идёт только курируемая идентичность.
*Последствия:* дашборд и экран цели показывают его слова, что делает
«записала: …» узнаваемым; каждая новая клиентская поверхность обязана уметь
отрисовать произвольный текст, в том числе про здоровье; граница «зритель — он
сам» должна быть выражена механизмом, а не соглашением.

**Вариант B.** Дословная формулировка живёт только на поверхности цели
(экран выбора/уточнения) и в корпусе; все прочие поверхности, включая дашборд
того же человека, получают только курируемую идентичность или нейтральную
подпись.
*Последствия:* сегодняшнее поведение дашборда придётся изменить; человек,
написавший цель своими словами, на дашборде своих слов не увидит; зато
поверхность распространения формулировки закрыта одной строкой правила, и
новые потребители не смогут её расширить по недосмотру.

---

## 12. Что НЕ является решением владельца

Перечислено явно, чтобы не унести в реестр то, что уже закрыто.

| Вопрос | Почему не решение |
|---|---|
| Снимать ли `clientgoal_one_active_per_client` | Закрыто каноном 2. Продуктовое решение принято, не переоткрывается. Способ снятия — обычная безопасная механика миграции. |
| Убирать ли `known.goal` | Закрыто каноном 16. |
| Убирать ли шаги `area`/`feeling` | Закрыто каноном 17, причина измерена. |
| Добавлять ли целевую дату в `ClientGoal` | Закрыто каноном 18. |
| Считать ли `_match_goal_keys` authority кандидата | Закрыто каноном 11; рантайм это и так не делает (§9, 2.7). |
| Проверять ли `goal_key` против `GoalOption` на прямом пути (C8) | Дефект рантайма. Страж на соседнем пути уже написан и объяснён комментарием; тут его просто нет. Правится, не решается. |
| Разворачивать ли `data` в python-читателях бота (§9.1) | Дефект рантайма, доказанный кодом обеих сторон. Обратносовместимая правка потребителя. |
| Должен ли человек мочь прекратить цель, не выбирая новую (C5) | Закрыто каноном 15. Отсутствие входа — инженерный пробел. |
| Как назвать состояние «цель есть, но не разрешается» (C7) | Имя поля/состояния. Диагностика уже разделена в логе (`_log_unresolved`). |
| Включать ли `GOAL_RESOLUTION_ENABLED` | Обычная раскатка за флагом; политика при `None` записана в `goals/wiring.py:12-31` и владельцем не оспорена. |
| Регистрировать ли `ClientGoal` в админке (C11) | Инструментальная правка. |
| Удалять ли протухшую ссылку на `goals/tenant_scope.py` (C13) | Удаление протухшего комментария. |

---

## 13. Что НЕ замерено — честно

1. **Значение `GOAL_RESOLUTION_ENABLED` и `GOAL_ANKETA_ENABLED` на боевом
   контуре.** В репозитории они не заданы: `git grep` по `.github/`,
   `docker*`, `*.env*`, `*.yml`, `deploy*` — пусто. Известны только умолчания
   кода (`false` и `true`). `UNKNOWN_NOT_MEASURED`.
2. **Число строк `ClientGoal` на пилоте сегодня.** В коде два датированных
   утверждения разных дней («= 0» на 2026-08-29, «две» ~04.09). Ни одно не
   цитируется как сегодняшний факт. `UNKNOWN_NOT_MEASURED`.
3. **Применены ли миграции `wellness/0001`,`0002` на боевом контуре.** Файлы
   существуют; факт применения не проверялся.
4. **Боевые ответы ручек** `/api/v1/internal/me/decision-context/` и
   `/api/v1/customer/wellness/today` не снимались. Дефект §9.1 доказан кодом
   обеих сторон; его проявление на контуре — не замерено.
5. **Что делает вызывающий `nutrition_coach.active_goal()` при `None`** —
   молчит коуч или говорит без цели. За рёбрами треков 1–2.
6. **`wellness` целиком как предмет** (адхеренция, прогресс, admission,
   `PlanAction`) — предмет другого трека; здесь измерено ровно столько,
   сколько нужно для ответа A1.
7. **`djangoproject`, `djangoproject-alpha`, `frontAyla`, `itsolve`** и прочие
   каталоги рабочей директории не осматривались: брифом заданы три
   репозитория.
8. **Тесты не запускались.** Ни один вывод здесь не опирается на прогон;
   ссылки на тесты — это чтение их текста как кода.

---

## 14. Точные команды воспроизведения

```bash
# 1. Базы замера
cd C:/Users/user/PycharmProjects/Ayla/djangoproject-catalog && git fetch origin \
  && git rev-parse origin/dev
cd C:/Users/user/PycharmProjects/Ayla/ai-bot-platform && git fetch origin \
  && git rev-parse origin/dev
cd C:/Users/user/PycharmProjects/Ayla/ayla-ai-core && git fetch origin \
  && git branch -r && git symbolic-ref refs/remotes/origin/HEAD \
  && git rev-parse origin/main
# отсутствие origin/dev в ayla-ai-core:
cd C:/Users/user/PycharmProjects/Ayla/ayla-ai-core && git rev-parse --verify origin/dev

# 2. Модель и миграции (читать сами файлы, не имена)
cd C:/Users/user/PycharmProjects/Ayla/djangoproject-catalog
git show origin/dev:goals/models.py
git ls-tree --name-only origin/dev goals/migrations/
for f in 0001_initial 0002_goalanketarun_goalanketaanswer_and_more \
         0003_clientgoal_tenant 0004_backfill_clientgoal_tenant \
         0005_remove_clientgoal_tenant; do
  echo "== $f =="; git show origin/dev:goals/migrations/$f.py; done

# 3. Писатели и читатели
git grep -n "ClientGoal" origin/dev -- '*.py'
git grep -n "is_active" origin/dev -- goals/
git grep -n "goal_category_ids_for\|goal_resolution_enabled\|resolve_goal_category_ids" origin/dev -- '*.py'

# 4. Канон 13/16 — отсутствие
git grep -n -i "paused\|achieved\|archived\|lifecycle" origin/dev -- goals/
git grep -n "active_goals\|relevant_goal_refs\|current_goal" origin/dev -- '*.py'

# 5. A1 — другие Goal-сущности
git grep -n "^class .*[Gg]oal" origin/dev -- '*.py'
git grep -n "goal" origin/dev -- '*/models.py'
git show origin/dev:wellness/services.py          # Gate D — безусловный отказ
cd C:/Users/user/PycharmProjects/Ayla/ayla-ai-core && git grep -n -l -i "goal" origin/main

# 6. Конверт ответа (§9.1) — обе стороны
cd C:/Users/user/PycharmProjects/Ayla/djangoproject-catalog
git show origin/dev:users/response.py | sed -n '1,15p'
cd C:/Users/user/PycharmProjects/Ayla/ai-bot-platform
git show origin/dev:apps/integrations/ayla/goals_client.py | sed -n '289,313p'
git show origin/dev:apps/miniapp_api/views.py | sed -n '2744,2770p'
git show origin/dev:apps/adminconsole/clients.py | sed -n '294,312p'
git show origin/dev:apps/miniapp_api/tests/test_wellness_today.py | sed -n '72,100p'

# 7. PATH 2 — отсутствие писателя из DM
cd C:/Users/user/PycharmProjects/Ayla/ai-bot-platform
git grep -n "post_goal_select" origin/dev
git grep -n "goals_client" origin/dev -- 'apps/channels/**'
git grep -n "goal_key\|goal_text" origin/dev -- 'apps/orchestrator/**'
git show origin/dev:apps/marketplace/discovery.py | sed -n '716,744p'
```

Все команды — только чтение. `git fetch` не меняет рабочих деревьев;
рабочее дерево `ai-bot-platform` в замере не использовалось и не трогалось.
