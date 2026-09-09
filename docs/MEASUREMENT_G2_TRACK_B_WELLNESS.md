# G2 · ТРЕК B — замер приложения `wellness` и его гейтов

**Дисциплина:** MEASURE → CLASSIFY → GAP → OWNER DECISION ONLY IF NECESSARY.
**STOP после замера.** Код не менялся, миграций нет, PR нет, Linear не трогался,
гейт не включался, ничего не исправлялось.

Дата замера — **2026-09-09**. Каждое утверждение ниже несёт репозиторий, SHA,
файл и строку. Где предмет не замерен — так и написано, `UNKNOWN_NOT_MEASURED`
не превращён в `MISSING`.

---

## 1. Базы замера (полные SHA)

| Репозиторий | Ветка | Полный SHA | Как установлено |
|---|---|---|---|
| `djangoproject-catalog` (`AndreyDeveloper84/beautygo_backend`) | `dev` (= `origin/dev`) | `95c917e684652476feef3ae9d790fb2c8d277378` | `git rev-parse HEAD` == `git rev-parse origin/dev` |
| `ai-bot-platform` | `origin/dev` | `83ed56a94eb0a96d9599c632f0e6ba2d48c6c5b7` | `git rev-parse origin/dev`; рабочий чекаут стоит на `feat/recommendation-boundary-client` (`fd6f4e87acde54aebc0cc7c50d55bff49fab82dc`), поэтому **весь замер по этому репозиторию сделан через `git show origin/dev:<path>`**, не по рабочему дереву |
| `ayla-ai-core` (`AndreyDeveloper84/ayla-ai-core`) | `origin/main` | `d72a5de451f985d118d9449d2b17ce51bf0a6e25` | `origin/dev` **не существует**; каноническая ветка установлена следом `remotes/origin/HEAD -> origin/main` в выводе `git branch -a`. Ветка `main` — единственная удалённая |

Рабочее дерево `djangoproject-catalog` чистое, кроме нетрекаемых `.claude/` и
`AGENTS.md` (`git status --porcelain`) — на предмет замера не влияют.

**Отдельно: документы-контракты живут вне всех трёх репозиториев.**
`docs/PROPOSAL_GOALS_MODEL_FINAL.md`, `docs/OD_GOALS_RULINGS.md`,
`docs/SPEC_CARE_CONTRACT.md`, `docs/DRAFT_ADHERENCE_CONTRACT.md` — которые
докстринги `wellness/*.py` цитируют как `docs/...` — физически лежат в
`C:/Users/user/PycharmProjects/Ayla/docs/`, то есть в корне рабочего
пространства, а **не** в `djangoproject-catalog/docs/`. Проверено:
`git log --all --diff-filter=A -- docs/PROPOSAL_GOALS_MODEL_FINAL.md ...` в
каталоге не находит ни одного коммита добавления. Это не дефект кода, но
читатель кода по ссылке из докстринга файл не откроет. Класс: `PARTIAL`
(ссылка есть, адресат вне репозитория).

---

## 2. Сводка по классам — числом

| Класс | Число | Что попало |
|---|---|---|
| `EXISTS` | 11 | 6 моделей (схема + ограничения), 2 гейта, 3 производных/читающих модуля (`progress`, `adherence`, `context_read`) — все с доказанным поведением |
| `PARTIAL` | 5 | `admission.py` без вызывающего; `PlanAction` без `outcome_ref`; ссылки докстрингов на документы вне репозитория; consent-проверка health-класса живёт у потребителя, не у владельца данных; `EvidenceRegistryEntry` с полями approval, но без механизма approval |
| `MISSING` | 6 | Писатели всех шести моделей; отображение `ClientGoal ↔ DesiredOutcome`; шаг стирания wellness в воронке 152-ФЗ; wellness в экспорте персональных данных; админка/сериализаторы; испускание событий |
| `CONTRADICTS_CANON` | 3 | Носитель целевой даты в документах бот-репозитория; кардинальность Plan ↔ Goal; словарь `horizon_*` в `PLAN_ENGINE_CONTRACT_v1.0` |
| `STALE_SPEC` | 2 | `PLAN_ENGINE_CONTRACT_v1.0.md §4.6` (поля `horizon_kind` / `horizon_origin`, «дата читается из `DesiredOutcome`»); замер «`goals_clientgoal` — 0 строк» от 25.08, процитированный как текущий факт |
| `UNKNOWN_NOT_MEASURED` | 5 | Состояние пилотной БД (применены ли миграции, есть ли строки); прогон тестов; текущее число строк `goals_clientgoal`; наличие потребителей вне трёх репозиториев и `frontAyla`; поведение под `STRICT_TENANT_SCOPE=strict` в живом рантайме |

Всего 32 классифицированных наблюдения.

---

## 3. Состав приложения (что вообще есть)

`djangoproject-catalog@95c917e6`, каталог `wellness/` — 21 файл, 2905 строк:

```
wellness/models.py            417   6 моделей
wellness/services.py          137   Gate D, Gate O, два публичных writer'а
wellness/admission.py         106   чистая функция допуска weight-loss плана
wellness/progress.py          147   Outcome Progress — производная
wellness/adherence.py          94   Plan Adherence — производная
wellness/fact_providers.py     62   счётчики фактов из журналов nutrition
wellness/context_read.py       83   эфемерный документ состояния
wellness/api.py                37   GET /api/v1/internal/me/wellness-context/
wellness/urls.py                8
wellness/apps.py                5
wellness/migrations/0001_initial.py    376
wellness/migrations/0002_planaction.py  70
wellness/tests/*.py          1183   6 файлов
```

**Чего в приложении нет вовсе** (проверено `ls wellness/`): `admin.py`,
`serializers.py`, `signals.py`, `tasks.py`, `management/`. Класс: `MISSING`
для админки, сериализаторов и испускания событий — это не пропуск замера,
файлов нет.

**LLM в приложении нет.** `grep -rn -i "llm|openai|gpt|prompt|anthropic" wellness/`
— ноль совпадений. Ни одно решение `wellness` не принимается моделью.

Регистрация: `djangoProject/settings/base.py:67` — `'wellness'` в
`INSTALLED_APPS`. URL: `djangoProject/urls.py:68-71` —
`path('api/v1/internal/me/wellness-context/', include('wellness.urls'))`.

**Полная перепись ссылок на `wellness` вне приложения**
(`grep -rn "wellness" --include=*.py .`, минус `./wellness/`):
`settings/base.py:67`, `urls.py:65,67,69,70`,
`nutrition/data/deficit_recommendations.py:9` (слово в тексте про формулировки),
`scripts/pilot_smoke/scenarios.py:504` (путь `/api/v1/customer/wellness/today` —
**к этому приложению отношения не имеет**: такого маршрута в
`djangoproject-catalog` нет, `grep` по репозиторию не находит его определения),
`services/models.py:27` (слово в комментарии).

**Перепись ссылок на имена моделей вне приложения:**
`grep -rn "DesiredOutcome|PersonalPlan|PlanOutcomeLink|PlanAction|ProgressObservation|EvidenceRegistryEntry" --include=*.py .`
минус `./wellness/` → **ноль совпадений**. Ни в `.md`, ни в `.yml`, ни в
`.json` каталога — тоже ноль.

**`frontAyla`** — `grep -rl -i "wellness-context|DesiredOutcome|PersonalPlan"` →
ноль. **`ayla-ai-core@d72a5de4`** — `git grep -l -i "wellness|DesiredOutcome|PersonalPlan" origin/main`
→ ноль; 12 модулей `src/ayla_ai_core/` вообще не знают об этом домене.

---

## 4. `DesiredOutcome`

`wellness/models.py:23-90`. Миграция `0001_initial`.

**Поля.** `id` UUID pk (`:42`); `user` FK → `AUTH_USER_MODEL`,
`on_delete=PROTECT`, `related_name="desired_outcomes"` (`:43-47`);
`target` SlugField(64) — ключ объекта результата (`:48-51`);
`statement_text` TextField, дословная формулировка (`:52-54`);
`direction` CharField(16) nullable, `reduce|increase|maintain` (`:55-61`);
`desired_state_numeric` Decimal(8,2) nullable (`:62-68`);
`status` CharField(16) `open|closed_by_user`, default `open` (`:69-73`);
`created_at` auto_now_add (`:74`); `closed_at` nullable (`:75`).

**Ограничения.** `CheckConstraint desiredoutcome_direction_or_numeric_present`
(`:80-86`) — заполнено хотя бы одно из `direction` / `desired_state_numeric`.
**Ограничения «одна активная на человека» НЕТ**, и это заявлено осознанным
(`:24-31`), подтверждено тестом `test_many_open_outcomes_allowed`
(`wellness/tests/test_models.py:79`).

**Поля даты у модели нет вовсе.** Ни `target_date`, ни `horizon`, ни
`deadline`. Это факт схемы, а не вывод из имени.

**Временные метки:** только `created_at` / `closed_at`. `created_at` несёт
поведение: `progress.py:87` фильтрует ряд наблюдений `observed_at__gte=outcome.created_at`.

**Кардинальность:** 0..N `OPEN` на человека. Обратные связи: `plan_links`
(`PlanOutcomeLink.outcome`, `:158-162`).

**Писатели:** `wellness/services.py:106-119` `record_outcome(...)` — **единственный
объявленный writer**, и он не пишет: тело функции целиком —
`return goal_intention_gate(attestation, purpose=Purpose.PROCESSING)`
(`:119`), а этот вызов при `processing` всегда даёт
`GateDecision(allowed=False, reason_code="scope_not_approved")` (`:83-84`).
ORM-вызовов `DesiredOutcome.objects.create` в продуктовом коде нет — только в
`wellness/tests/` (перепись вызывающих по каждой публичной функции сделана
`grep` по репозиторию). Класс: **`MISSING`** (писателя в рантайме нет).

**Читатели:** `wellness/context_read.py:76-78` (за закрытым гейтом),
`wellness/progress.py:102` (принимает объект аргументом; читает `.target`,
`.user_id`, `.created_at`, `.desired_state_numeric`).

**API:** только проекция в `wellness-context` (`wellness/api.py:37`).
**Гейт допуска:** Gate D. **Флаги:** ни одного — включения флагом нет.
**Тесты:** `wellness/tests/test_models.py:59-95`, `test_fail_closed.py:84-95`.
**Испускание событий:** нет.

**Достижима ли в живом рантайме:** таблица создаётся миграцией; **записать в
неё из живого рантайма нечем** — нет HTTP-ручки записи, нет админки, нет
management-команды, нет задачи Celery, а единственный сервисный writer
отказывает безусловно. Чтение достижимо только внутрипроцессно и только через
`build_wellness_context`, который при закрытых гейтах до ORM не доходит
(`context_read.py:69-74` — ранний возврат **до** запроса).

### Ответы B1–B8

**B1. Что это по смыслу — по поведению, не по имени.**
Это **носитель измеримого желаемого состояния и якорь допустимого ряда
свидетельств**. Доказательство поведением, а не именем класса:
`progress.py:71-92` строит ряд наблюдений, отбирая записи по
`EvidenceRegistryEntry.outcome_target == outcome.target` и по
`observed_at >= outcome.created_at`; `progress.py:116` переносит
`outcome.desired_state_numeric` в снапшот как есть. То есть строка
`DesiredOutcome` определяет (а) какие типы свидетельств вообще считаются
для этого результата и (б) с какого момента начинается его baseline. Ни
одного другого поведения у неё нет.

**B2. Дубликат Goal, исход Plan, внутренняя проекция или отдельная сущность?**
**Отдельная сущность.** Доказано связями и поведением, не именем:
- *не дубликат `goals.ClientGoal`*: между `goals/` и `wellness/` **ноль**
  ссылок в обе стороны (`grep` по репозиторию); нет FK, нет таблицы
  отображения, нет общего ключа; наборы полей не пересекаются — у
  `ClientGoal` (`goals/models.py:36-99`) `goal_key`/`goal_text`/
  `source_channel`/`is_active` и партиальный unique
  `clientgoal_one_active_per_client` (`:83-87`), у `DesiredOutcome` —
  `target`/`direction`/`desired_state_numeric` и **демонстративное
  отсутствие** такого ограничения;
- *не исход Plan*: FK на `PersonalPlan` у неё нет вовсе; связь вынесена в
  третью строку `PlanOutcomeLink`, и `DesiredOutcome` создаётся и живёт без
  плана (фикстуры `wellness/tests/test_progress.py` создают outcome без
  плана и получают полноценный прогресс);
- *не внутренняя проекция*: это durable-таблица с миграцией
  (`0001_initial`), а не эфемерная структура — в отличие от
  `OutcomeProgress` (`progress.py:46-61`, frozen dataclass, ничего не
  хранит).

**B3. Кто способен создать сегодня?**
Никто из рантайма. Только код, имеющий прямой ORM-доступ внутри процесса
(сегодня это исключительно тесты). Ни бот, ни Mini App, ни оператор, ни
фоновая задача создать строку не могут — путей записи не существует.

**B4. Кто меняет жизненный цикл?**
Никто. `grep` по репозиторию не находит ни одного присваивания
`.status` / `.closed_at` на `DesiredOutcome` вне тестов. Перечисление
статусов — `OPEN → CLOSED_BY_USER`; `ACHIEVED`/`FAILED` отсутствуют
физически (`models.py:38-40`, заявлено в докстринге модуля `:10-12`).
Контракт (`PROPOSAL §6`) отдаёт переход только человеку.

**B5. Кто читает и зачем?**
Два читателя. `context_read._outcome_payload` (`:45-56`) — чтобы отдать
решающему слою бота четыре кода: `target`, `link_status`, `horizon_status`,
`progress_state`. `progress.compute_outcome_progress` — чтобы вычислить
состояние ряда. Оба сегодня недостижимы наружу: первый заперт гейтом,
второй вызывается только из первого и из тестов.

**B6. Может ли существовать без `ClientGoal`?**
**Да.** Нет FK, нет валидации, нет сервисной сцепки. Фикстура
`wellness/tests/test_models.py` создаёт `DesiredOutcome` от одного только
`User`. Обратное тоже верно: `ClientGoal` существует без `DesiredOutcome`
(вся живая ветка целей в `goals/` о нём не знает).

**B7. Есть ли отображение `ClientGoal ↔ DesiredOutcome`?**
**Нет. Фиксирую прямо: отображения не существует ни в одном виде** — ни FK,
ни промежуточной таблицы, ни функции, ни словаря ключей, ни даже общего
словаря `target` ↔ `goal_key`. `goals/decision_context.py` (эфемерный
документ целей) не упоминает outcome/plan/wellness ни разу.
Следовательно **авторитет в коде не определён**: две таблицы описывают
пересекающуюся продуктовую сущность и не знают друг о друге.
Класс: **`MISSING`**.

**B8. Есть ли код, предполагающий, что `DesiredOutcome` уже канонический Goal?**
**Кода такого нет.** Есть **документы**, которые это утверждают, и они
расходятся с рантаймом — см. §10, противоречие C-1.

---

## 5. `PersonalPlan`

`wellness/models.py:93-129`.

**Поля.** `id` UUID pk; `user` FK → `AUTH_USER_MODEL`, `PROTECT`,
`related_name="personal_plans"` (`:105-109`); `status` `active|closed_by_user`,
default `active` (`:110-114`); `created_at` auto_now_add; `closed_at` nullable.
**Больше полей нет** — ни имени, ни описания, ни версии, ни ссылки на цель,
ни `created_via`, ни `current_revision_id`.

**Ограничение.** `UniqueConstraint(fields=["user"], condition=Q(status="active"),
name="personalplan_one_active_per_user")` (`:120-126`). Доказано тестами
`test_second_active_rejected` / `test_second_active_allowed_after_close`
(`tests/test_models.py:98-116`).

**Жизненный цикл.** Две вершины, один переход: `ACTIVE → CLOSED_BY_USER`.
Ни `PAUSED`, ни `SUPERSEDED`, ни `ARCHIVED` в перечислении нет. Кода,
меняющего статус, в репозитории нет вовсе. Версионирования нет: ревизий,
снимков, монотонного номера — ни одного поля.

**Связи.** `outcome_links` → `PlanOutcomeLink` (`:153-157`, FK `plan`,
`PROTECT`); `actions` → `PlanAction` (`:387-391`, FK `plan`, `PROTECT`).
Прямой связи `PersonalPlan → DesiredOutcome` **нет** — только через
`PlanOutcomeLink`.

**Писатели:** ни одного. **Читатели:** `context_read._plan_payload` (`:37-42`)
— берёт первый `ACTIVE` план и отдаёт наружу **только код статуса**;
`adherence.compute_plan_adherence` (`:68-93`) — принимает план аргументом,
читает `plan.actions.all()` и `plan.user_id`.
**API:** только `{"status": "active"}` или `null` в `wellness-context`.
**Админка/сериализаторы/события:** нет.

**Достижим ли в живом рантайме:** нет пути создания. Читается только внутри
`build_wellness_context`, до которого при закрытых гейтах исполнение не
доходит.

### Ограничение «0..1 активный план на человека» — конфликт или контракт?

**Это принятый контракт, а не старое предположение.** Доказательство —
источник, а не пересказ:

- `Ayla/docs/PROPOSAL_GOALS_MODEL_FINAL.md §2`, таблица кардинальностей:
  «`PersonalPlan` — 0..1 ACTIVE на человека — общий контейнер (OD-GOAL-4);
  закрытые ряды = история».
- `Ayla/docs/OD_GOALS_RULINGS.md`, GOALS-R4 (записано дословно 25.08,
  документ имеет приоритет над любым пересказом главного окна):
  «одному плану иметь **N outcomes с разными сроками**»; «Два одновременно
  активных Personal Plans для одного Outcome пока **НЕ вводить** без
  отдельной продуктовой причины»; «Несколько одновременно активных Desired
  Outcomes **разрешены с первого дня**».

Значит: **несколько активных целей ≠ несколько активных планов**, и
runtime-ограничение ровно выражает принятое решение. Автоматическим
конфликтом с «несколько ACTIVE Goals разрешены» оно **не является**.

Конфликт, который здесь действительно есть, — не с каноном целей, а с
проектируемым Plan Engine: см. §10, противоречие C-2, и `OD-GWP-1`.

---

## 6. `PlanOutcomeLink`

`wellness/models.py:132-204`.

**Поля.** `id` UUID pk; `plan` FK → `PersonalPlan`, `PROTECT`,
`related_name="outcome_links"` (`:153-157`); `outcome` FK → `DesiredOutcome`,
`PROTECT`, `related_name="plan_links"` (`:158-162`);
**`target_date` DateField, `null=True`, `blank=True`** (`:163-167`);
`status` `active|closed_by_user` (`:168-172`); `created_at`; `closed_at`.

**Ограничение.** `UniqueConstraint(fields=["outcome"], condition=Q(status="active"),
name="planoutcomelink_one_active_per_outcome")` (`:179-183`). Обратите
внимание: ограничение на **`outcome`**, не на пару `(plan, outcome)` и не на
`plan`.

**`horizon_status` — вычислимое `@property`, НЕ колонка** (`:186-198`).
Значения: `none` (`target_date IS NULL`), `upcoming`
(`timezone.localdate() <= target_date`), иначе `elapsed`. В миграции
`0001_initial` колонки под него нет. Тест
`test_horizon_status_is_computed_property` (`tests/test_models.py:137`).

### Точная семантика связки — по коду, не по названию поля

Является ли `target_date` свойством Goal, Plan или **отношения** — установлено
фактом схемы и подтверждено источником решения:

- **Фактически это свойство отношения.** Колонка объявлена на
  `PlanOutcomeLink` (`models.py:163`), а не на `DesiredOutcome` (у которой
  поля даты нет вовсе) и не на `PersonalPlan` (у которой полей, кроме
  статуса и меток, нет вовсе). Миграция `0001_initial` это подтверждает.
- **Так и решено владельцем.** `OD_GOALS_RULINGS.md`, GOALS-R4:
  «горизонт живёт **на связи** `PersonalPlan ↔ DesiredOutcome`, а не внутри
  `DesiredOutcome` и не один на `PersonalPlan`»; основание —
  «`PlanOutcomeLink` — “как данный outcome участвует в данном плане”,
  включая индивидуальный горизонт».
- **Кто имеет право её менять:** по контракту (`PROPOSAL §6`) статус связи
  меняет только человек, продолжение результата — новая строка связи.
  **В коде права менять её нет ни у кого**: ни одного присваивания
  `target_date` вне тестов. Класс: `MISSING` (писатель), `EXISTS` (схема и
  правило).

### D1–D6

**D1. Может ли один исход иметь разные даты в разных планах/ревизиях?**
**Да — последовательно; нет — одновременно.** Ограничение единственности
стоит на `outcome` **при `status='active'`**, поэтому в каждый момент у
результата не более одной активной связи, а за жизнь результата их N, и у
каждой строки свой `target_date`. Докстринг (`:133-141`) описывает ровно этот
механизм («продолжение результата в новом плане — новая строка связи; старая
`closed_by_user` остаётся историей»), и поведение подтверждено тестами
`test_second_active_link_on_outcome_rejected` / `test_second_link_allowed_after_close`
(`tests/test_models.py:119-131`). Ревизий как объекта не существует.

**D2. Может ли один план обслуживать несколько исходов?**
**Да.** Единственность наложена на `outcome`, не на `plan`; FK `plan` не
уникален; обратная связь называется `outcome_links` (множественное число —
это не доказательство, доказательство — отсутствие ограничения на `plan`).
Источник подтверждает намерение: GOALS-R4 — «одному плану иметь N outcomes с
разными сроками».

**D3. Может ли цель существовать без плана?**
**Да.** `DesiredOutcome` не имеет FK на план; связь необязательна.
`context_read._outcome_payload` (`:48-55`) прямо обрабатывает случай
отсутствия связи: `link_status` и `horizon_status` становятся `null`
(тест `test_outcome_without_link_has_null_link_fields`,
`tests/test_context_read.py:242`).

**D4. Может ли дата существовать без плана?**
**Нет.** Колонка `target_date` существует только на `PlanOutcomeLink`, а у
`PlanOutcomeLink` поле `plan` — обязательный FK (`null` не разрешён,
`models.py:153-157`). Значит **дата физически требует существования
`PersonalPlan` И `DesiredOutcome`**. Это прямое следствие схемы, и оно
порождает продуктовую развилку — см. `OD-GWP-2`.

**D5. Что происходит после истечения даты?**
**Ничего в данных.** `horizon_status` начинает вычисляться как `elapsed`
(`:194-198`). Ни одной записи в БД при этом не происходит: свойство
вычисляется на чтении. Единственный потребитель значения `elapsed` в обоих
репозиториях — `ai-bot-platform@83ed56a9:apps/wellness_proactive/tasks.py:99`
(`TRIGGER_HORIZON_STATUS = "elapsed"`) и `:176-186`
(`observe_occasion_codes`): истёкший горизонт порождает **повод семейства
`OBSERVE`**, то есть предложение внести текущий замер. Отправки в задаче нет
(`:302-334` — пишется решение и след, `Decision.send` не существует).

**D6. Меняет ли истечение жизненный цикл — или это только вычисляемый сигнал
свежести?**
**Только вычисляемый сигнал.** Доказательства: (а) колонки нет — писать
нечего; (б) кода, закрывающего связь/результат по дате, в репозитории нет;
(в) `ACHIEVED`/`FAILED` отсутствуют во всех перечислениях; (г) источник
согласен — GOALS-R3: «Календарь **не** ставит `ACHIEVED`, **не** ставит
`FAILED`, **не** закрывает Desired Outcome, **не** обнуляет Progress, **не**
удаляет observations», «`elapsed` — не lifecycle status связи».
Класс: `EXISTS`, соответствие канону подтверждено.

---

## 7. `PlanAction`

`wellness/models.py:362-417`, миграция `0002_planaction` (2026-08-25).

**Поля.** `id` UUID pk; `plan` FK → `PersonalPlan`, `PROTECT`,
`related_name="actions"` (`:387-391`);
`action_type` CharField(32), курируемый ключ, ровно два значения —
`log_food`, `log_water` (`:378-380`, `:392-396`);
`cadence` CharField(16) — `per_day | per_week` (`:382-384`, `:397-400`);
`target_count` PositiveSmallInteger, default 1 (`:401-404`);
`created_at` auto_now_add. Индекс `planaction_plan_idx` по `plan` (`:410`).

**Чего у `PlanAction` нет — это и есть доказательство:** нет `outcome_ref`,
нет `capability_ref`, нет `level`, нет `role`, нет `status`, нет порядка
(`ordering = ["created_at"]` — не поле сортировки, а порядок выборки), нет
`execution`, нет `alternative_of`, нет `provenance`, нет ссылок на услугу,
провайдера, запись или рекомендацию, нет поля завершения.

**Исполнение и завершение.** Своего понятия «выполнено» у модели нет.
Сопоставление с фактами делает **производная** `adherence.compute_plan_adherence`
(`adherence.py:68-93`): по каждому действию считает вёдра каденса
(`_buckets`, `:51-65`) и суммирует `min(фактов в ведре, target_count)`.
Факты берутся у `fact_providers.count_facts` (`fact_providers.py:45-62`),
который читает **журналы nutrition**: `WaterEntry` по `ts` c
`deleted_at__isnull=True` (`:21-28`) и `FoodLog` по `logged_at` (`:31-36`).
Неизвестный ключ — `ValueError`, а не тихий ноль (`:56-61`).
Общего итога по плану нет и структурно быть не может — результат это список
`ActionAdherence(action_type, cadence, target_total, fulfilled_count)`
(`:36-48`), проверено тестом `test_result_has_no_plan_total`
(`tests/test_adherence.py:152`).

**Писатели:** ни одного вне тестов. **Читатели:** только
`adherence.compute_plan_adherence`, у которого, в свою очередь,
**вызывающих в продуктовом коде нет** (перепись `grep` — только
`wellness/tests/test_adherence.py`). **API:** отсутствует —
`wellness-context` не содержит ни `actions`, ни adherence.
**Достижим ли в живом рантайме:** нет; ни записи, ни чтения снаружи.

### Классификация относительно `PlanStep`

Эталон для сравнения — `ai-bot-platform@83ed56a9:docs/specs/PLAN_ENGINE_CONTRACT_v1.0.md §4.2`
(«`PlanStep` — единица стратегии»): обязательные `capability_ref`
(«шаг без способности не существует»), `outcome_ref`, `role`
(`CORE|OPTIONAL|ALTERNATIVE`), `level` (`CAPABILITY|SERVICE|OFFER`),
`canonical_service_ref`, `tenant_offer_ref`, `assertions[]`, `execution`,
`provenance`; живёт внутри иммутабельной `PlanRevision` (§4.4).

**Классификация: `DIFFERENT_ENTITY`.** Доказательство, а не сходство имён:

1. **Ноль общих полей**, кроме FK на контейнер и `created_at`. Ни одного из
   обязательных семантических входов `PlanStep` у `PlanAction` нет — включая
   тот единственный, без которого шаг «не существует» (`capability_ref`).
2. **Разные предметы утверждения.** `PlanStep` утверждает, *что предлагает
   Ayla* (способность → услуга → оффер, вплоть до исполнимости через
   `PendingBookingIntent`). `PlanAction` утверждает, *что человек обещал
   регистрировать сам*, и сверяется исключительно с журналами питания и воды.
   Источник это фиксирует явно: `DRAFT_ADHERENCE_CONTRACT.md §2` —
   «`PlanAction` — обязательство внутри Personal Plan. **Не engine**: ни
   расписания, ни напоминаний, ни пересчёта — только запись “что обещано”».
3. **Разные направления связи с результатом.** `PlanStep.outcome_ref` —
   обязательная ссылка на `DesiredOutcome`. `PlanAction` **не способен
   назвать результат вообще**: поля нет, и adherence поэтому не может быть
   отнесена ни к одной цели.
4. **Разные контейнеры.** `PlanStep` живёт в снимке иммутабельной ревизии;
   `PlanAction` — обычная mutable-строка при `PersonalPlan`, у которого
   ревизий нет.

Единственное пересечение — структурное («обе строки висят на объекте
плана»), и оно не семантическое. `PARTIAL_OVERLAP` не назначаю: перекрытие
нулевое по полям и по предмету.

Отдельно: **отсутствие `outcome_ref` — `PARTIAL`**, но это осознанная граница
первого среза (`DRAFT_ADHERENCE_CONTRACT §4`, §6), а не пробел.

---

## 8. `ProgressObservation` и `EvidenceRegistryEntry`

### `ProgressObservation` — `wellness/models.py:207-312`

**Поля.** `id` UUID pk; `user` FK `PROTECT`
(`related_name="progress_observations"`); `observation_type`
`weight|self_assessment` (`:223-225`, `:240-243`); `origin` — перечисление из
**одного** значения `user_stated` (`:227-228`, `:244-248`); `instrument`
SlugField(64) nullable (`:249-254`); `value_numeric` Decimal(8,2) nullable;
`value_ordinal` PositiveSmallInteger nullable; `observed_at`
(default `timezone.now`); `superseded_by` FK на себя, `PROTECT`, nullable
(`:268-275`); `created_at`.
Константа `INSTRUMENT_NOTICEABILITY_0_3_V1` (`:232`).

**Ограничение.** `CheckConstraint progressobservation_value_matches_type`
(`:283-299`): либо `weight` + `value_numeric` + `value_ordinal IS NULL` +
`instrument IS NULL`, либо `self_assessment` + `value_ordinal` + `instrument`
+ `value_numeric IS NULL`. Ничего между. Индекс
`progressobs_user_type_time_idx` по `(user, observation_type, observed_at)`.

**Структурный запрет на вывод.** В перечислении `Origin` нет
`measured`/`inferred`/`derived` — **выводимое наблюдение невозможно
записать**, потому что нечего положить в колонку. Тест
`test_origin_only_user_stated` (`tests/test_models.py:209`).

**Писатели:** `services.record_observation` (`services.py:122-137`) — тело
целиком `return body_observation_gate(...)` (`:137`), всегда отказ
`blocked_pending_privacy_legal` (`:102`). ORM-записи вне тестов нет.
**Читатели:** `progress._series_query` (`progress.py:64-92`).

### `EvidenceRegistryEntry` — `wellness/models.py:315-359`

**Поля.** `id` UUID pk; `outcome_target` SlugField(64); `observation_type`;
`origin`; `instrument` **CharField с `default=""`, не NULL** (`:340-345`, и
причина названа в докстринге `:322-324`: иначе `unique_together` не работает,
NULL ≠ NULL); `approved_by` CharField(255); `approved_at` DateTimeField.
`unique_together` по четвёрке `(outcome_target, observation_type, origin,
instrument)` (`:351-353`).

**Что считается свидетельством — по коду.** `progress._series_query`
(`:71-92`) строит фильтр **из реестра, а не из хардкода**: на каждую запись
реестра добавляется `Q(observation_type=..., instrument=... or None)`. Если
реестр пуст — `allowed = Q(pk__in=[])`, и вызывающий получает не пустой ряд,
а состояние `no_measure` (`:112-117`). То есть **строка реестра — это и есть
допуск свидетельства**, и она же определяет, что человек увидит как «прогресс».

**Писатели реестра:** ни одного. Нет админки, нет ручки, нет команды, нет
фикстуры в продуктовом коде. Поля `approved_by`/`approved_at` заполняются
кем угодно, у кого есть ORM-доступ: **механизма owner approval, который
GOALS-R1 требует, в коде нет** — есть только место, куда его записать.
Класс: **`PARTIAL`** (поля учёта есть, принуждения нет).

### Что считается прогрессом, и может ли он объявить цель достигнутой

`progress.compute_outcome_progress` (`:102-147`) возвращает четыре состояния:
`no_measure` (реестр не знает такого `outcome_target`), `no_observations`,
`baseline_only` (одна точка — старт, не прогресс), `derived`. В `derived`
кладутся `baseline_value`, `latest_value` и `delta = latest − baseline` —
**только вычитание**. Ни темпа, ни экстраполяции, ни оценки, ни текста.

**Может ли наблюдение изменить жизненный цикл цели?** Нет. Модуль ничего не
пишет (`OutcomeProgress` — frozen dataclass, `computed_at` помечает снапшот);
статусов он не касается; удаление наблюдения аннулирует прогресс само
(тест `test_deletion_annuls_progress`, `tests/test_progress.py:184`).

**Может ли завершённая запись (booking) считаться прогрессом?** **Нет, и это
структурно.** `progress.py` не импортирует ни `nutrition`, ни бронирования;
в модуле проверяется AST-тестами: `test_no_nutrition_imports_in_ast`,
`test_no_path_to_profile_weight_in_ast`, `test_import_does_not_load_nutrition`,
`test_no_field_where_two_lines_meet` (`tests/test_progress.py:268-320`), и с
другой стороны — `test_adherence_does_not_import_progress_module`
(`tests/test_adherence.py:158`). Две линии — Outcome Progress и Plan
Adherence — разведены и не имеют поля, где могли бы сойтись.
**Канон соблюдён:** завершённая запись целью достигнутой не делает; более
того, «достигнутой» цель не может стать вообще — `ACHIEVED` в схеме нет.

**Есть ли вывод LLM?** Нет — ни в одном файле `wellness/`.
**Есть ли происхождение?** Есть, и оно однозначное: `origin=user_stated` —
единственное допустимое значение. Concrete provenance (какие именно
наблюдения вошли в конкретный прогресс) не реализовано — `PROPOSAL §5`
называет это «место показано, не реализовано»; сегодня это детерминированный
запрос. Класс: `PARTIAL`, соответствует контракту.
**Есть ли пороги?** Нет. Ни одного числового порога в модуле.
**Используется ли в рантайме?** Только `.state` — `context_read.py:55` берёт
из `OutcomeProgress` **одно поле**, и то за закрытым гейтом. Значения
(`baseline_value`, `latest_value`, `delta`) не покидают процесс никогда:
в документе полей под них просто нет.

---

## 9. Гейты — почему писатели заперты

### Что именно блокирует

`wellness/services.py:70-85` (Gate D) и `:88-103` (Gate O). Обе функции —
чистые, без БД, без настроек, без флагов:

```
purpose == "subject_rights"  -> allowed=True,  reason_code="subject_rights"
purpose == "processing"      -> allowed=False, reason_code="scope_not_approved"          (Gate D)
                             -> allowed=False, reason_code="blocked_pending_privacy_legal" (Gate O)
иначе                        -> allowed=False, reason_code="unknown_purpose"
```

Отказ при `processing` — **безусловный**: `attestation` в теле не читается
вовсе. Валидная по форме `ConsentAttestation` (`:44-59`: scope, authority,
provenance, document_version, captured_at) гейт не открывает — доказано
тестом `test_observation_with_valid_attestation_still_refused`
(`tests/test_fail_closed.py:71-82`).

### Это намеренная заморозка, а не заглушка и не мёртвый код

Природа блокировки установлена по источникам, а не по имени:

- **Гейт согласия/приватности, не заморозка продукта.**
  `OD_GOALS_RULINGS.md` (owner, 25.08, дословно): «**Persistent body
  observations остаются BLOCKED** до утверждённого consent/policy»;
  «`ConsentType.HEALTH` … **Enum является техническим reuse candidate, но НЕ
  является legal authorization**. До Registry amendment + Privacy/Legal
  approval persistent body observations остаются BLOCKED».
- **Gate D заперт другим основанием — отсутствием утверждённого scope.**
  `PROPOSAL §7.1` / GOALS-R6: для persistent `explicit_goal` создаётся
  **отдельный scope `goal_memory`**, `preference_memory` не расширяется.
  Пока scope не утверждён — отказ.
- **Решение, которое это ввело:** `OD_GOALS_RULINGS.md` GOALS-R1/R5/R6 +
  `PROPOSAL_GOALS_MODEL_FINAL.md` §7 и врезка вверху: «**Разрешение на
  миграцию НЕ является разрешением включить persistent writes**». Коммит,
  которым это въехало в код: `djangoproject-catalog` `a922cb61`
  «feat(wellness): каркас измеримых целей — модели, гейты fail-closed,
  миграция (GOALS-R1..R6) (#254)».
- **Не мёртвый код:** гейты имеют живого потребителя (`context_read.py:66-67`)
  и постоянную CI-проверку (`tests/test_fail_closed.py`, требование
  `PROPOSAL §7.3` «fail-closed доказуем, не заявлен»).

### Какие пути чтения остаются разрешены

- `purpose=subject_rights` — оба гейта пропускают **без attestation и даже
  после отзыва согласия** (amendment C; тесты `tests/test_fail_closed.py:123-131`).
  Это защита от обратной ошибки: «забудь всё», загейтованное на отозванное
  согласие, делало бы стирание невозможным ровно тогда, когда оно
  обязательно (`PROPOSAL §7.2`).
- HTTP-чтение `GET /api/v1/internal/me/wellness-context/` **разрешено и
  работает**, но отдаёт честное «закрыто»:
  `{"plan": null, "outcomes": [], "gated": {"gate_d": "...", "gate_o": "..."}}`
  (`context_read.py:69-74`). Коды причин берутся **из самих гейтов**, не из
  хардкода — доказано тестом
  `test_document_is_gated_with_gate_reason_codes` (`tests/test_context_read.py:173-184`).
  Ранний возврат стоит **до** ORM-запросов: при закрытых гейтах строки в БД
  документ не открывают (`test_existing_rows_do_not_open_the_document`,
  `:186-207`, включая grep-стражу «96 и 90 не встречаются в теле ответа»).

### Какие пути записи физически недостижимы

Все. `record_outcome` и `record_observation` — единственные объявленные
writer'ы, оба возвращают решение гейта и **не содержат ни одного ORM-вызова**.
Ручек записи нет (`wellness/urls.py` — один GET). Админки нет.
Management-команд нет. Задач Celery нет. Сигналов нет.

### Включается ли флагом конфигурации?

**Нет.** В `wellness/` нет ни одного `settings.`-обращения
(проверено: единственные импорты `django.conf.settings` — в `models.py` ради
`AUTH_USER_MODEL`). Ни `getattr(settings, ...)`, ни переменной окружения, ни
feature-flag. `PROPOSAL §7.2` требует этого дословно: «Никаких bypass /
feature-flag разрешений». Включение — **правка кода** плюс, по контракту,
внешние условия (утверждение scope `goal_memory` для Gate D; Registry
amendment + Privacy/Legal + verified consent integration для Gate O).

### Какие предусловия проверяются

Ровно одно — `purpose`. `attestation` не проверяется ни в одной ветке.
Неизвестный `purpose` — отказ, а не пропуск (`unknown_purpose`), тест
`test_unknown_purpose_refused` (`tests/test_fail_closed.py:133-136`).

### Контрактная страховка, которая краснеет при ослаблении

`tests/test_fail_closed.py:103-115` проверяет **сигнатуры**: параметра
`consent` нет ни у одного из четырёх публичных объектов, а `attestation`
типизирован `ConsentAttestation`. Это amendment E, выраженный тестом:
boolean-согласие невозможно контрактно.

### Асимметрия, которую надо назвать

Gate D запирает запись `wellness.DesiredOutcome` как persistent
`explicit_goal` — а соседняя ручка выбора цели пишет `goals.ClientGoal`
**вообще без гейта** (`goals/select_urls.py`, `djangoProject/urls.py:62-64`).
`PROPOSAL §7.1` называет это «governance debt, не прецедент» и обосновывает
безопасность долга замером «`goals_clientgoal` — **0 строк** на пилоте
**25.08**». Замер датирован; на 09.09 он **не перепроверен** (см. §13).
Класс: `STALE_SPEC` (для цитируемого факта), `PARTIAL` (для самого долга —
путь его закрытия уже назначен и отдельного решения не требует).

---

## 10. Подтверждённые противоречия

### C-1. Носитель целевой даты: документы бот-репозитория против рантайма и против owner ruling

**Утверждают документы** (`ai-bot-platform@83ed56a9`):
- `docs/specs/PLAN_ENGINE_CONTRACT_v1.0.md:81-82` — «дата стоит на
  `DesiredOutcome`, горизонт — на `PlanOutcomeLink` (§4.6)»;
- там же `:268` — схема `PlanOutcomeLink` с полем
  «`outcome_ref` DesiredOutcome (**носитель даты — НЕ этот объект**)»;
- там же `:273` — «Дата здесь **не хранится и не дублируется**: читается из
  `DesiredOutcome`»;
- `docs/HANDOFF_PLAN_ENGINE.md:76-79` и
  `docs/HANDOFF_PLANNING_RULES_REGISTRY.md:93` — то же, с пометкой
  «**Владельцу не выносить**»;
- `docs/design/policies/customer-wellness-goal-setting-ux.md:157` — то же;
- первоисточник этого чтения — `docs/OPEN_DECISIONS.md §54` (ruling главного
  окна): «дата хранится — но не на строке `ClientGoal`».

**Показывает рантайм** (`djangoproject-catalog@95c917e6`):
- у `DesiredOutcome` (`wellness/models.py:23-90`) **поля даты нет вовсе**;
- единственная дата домена — `PlanOutcomeLink.target_date`
  (`wellness/models.py:163-167`), и она — колонка, а не чтение откуда-то.

**Говорит владелец** (`Ayla/docs/OD_GOALS_RULINGS.md`, GOALS-R4, записано
дословно 25.08; шапка документа: «Имеет приоритет над … любым пересказом
главного окна»): «горизонт живёт **на связи** … а **не внутри
`DesiredOutcome`**».

**Класс: `CONTRADICTS_CANON`** для документов бот-репозитория, `EXISTS` для
рантайма. Разрешается **прецедентом источников**, а не выбором продукта:
owner ruling старше по приоритету, рантайм ему соответствует, `PROPOSAL §3`
ему соответствует. **Владельцу не выносится** — правится документ, а не код.

### C-2. Кардинальность Plan ↔ Goal: `PLAN_ENGINE_CONTRACT_v1.0` против GOALS-R4 и рантайма

- `PLAN_ENGINE_CONTRACT_v1.0.md §4.7`: «Один Plan относится ровно к одному
  Goal (`goal_ref` скалярен). **План, обслуживающий две цели, не
  существует**»; «Один Goal — не более одного `ACTIVE` Plan».
- GOALS-R4 (owner, дословно) + `PROPOSAL §2` + рантайм: **0..1 ACTIVE
  `PersonalPlan` на человека**, обслуживающий **N** `DesiredOutcome` с
  разными сроками; ограничение единственности стоит на `outcome`, не на
  `plan` (`wellness/models.py:179-183`).

Это **не** коллизия имён, как C-1: два документа делают
взаимоисключающие утверждения о числе планов у человека с несколькими
целями. См. `OD-GWP-1`.

### C-3. Словарь горизонта

`PLAN_ENGINE_CONTRACT_v1.0.md §4.6` объявляет у `PlanOutcomeLink` поля
`horizon_kind` (`NONE | EVENT_DATED | EVENT_UNDATED`) и `horizon_origin`
(`stated | click`, «derived ЗАПРЕЩЁН»). В рантайме нет ни того, ни другого;
есть вычислимое `horizon_status` (`none | upcoming | elapsed`,
`wellness/models.py:147-150`, `:186-198`) — словарь, утверждённый GOALS-R3.
**Класс: `STALE_SPEC`** для §4.6. Никакого продуктового выбора здесь нет:
это две разные оси (вид горизонта против свежести срока), и одна из них
просто не реализована.

### C-4. Контракт удаления и экспорта не исполнен

`PROPOSAL §8` требует: «`ProgressObservation` — **hard delete**;
`DesiredOutcome` / `PersonalPlan` / `PlanOutcomeLink` — **удаляются**»
шагом в существующую воронку `InternalPersonalDataDeleteView →
erase_personal_context` (C5.2).

Фактически `users/personal_context_erasure.py:110-154` работает **только с
`UserPersonalContext`** (`:123`, `:100-107`): поля возвращаются к дефолтам,
`data_sources` становится надгробием. Ни одна модель `wellness` в модуле не
упоминается. Три вызывающих —
`users/personal_data_api.py:172` (C5.2, бот),
`users/personal_context_views.py:193` (приложение),
`users/services.py:1228` (удаление аккаунта) — все зовут тот же глагол.
Экспорт `users/personal_data_api.py:126-133` отдаёт `profile` и
`personal_context`, `wellness` в ответе отсутствует.

**Класс: `MISSING`.** Сегодня это без последствий — писателей нет, строк
неоткуда взяться; но это прямая опасность включения (см. §12).
Решением владельца **не является**: §8 уже выбрал hard delete.

### C-5. Механизм owner approval у `EvidenceRegistryEntry` отсутствует

GOALS-R1: «Любое расширение матрицы `observation_type × origin` требует
**отдельного owner approval**». В коде — поля `approved_by`/`approved_at`
(`models.py:346-347`) и **ничего**, что бы их проверяло: ни валидации, ни
ограничения, ни отсутствующей админки с правами. Любая строка реестра
немедленно расширяет допустимый ряд в `progress._series_query`.
**Класс: `PARTIAL`.** Решением владельца не является — приказ уже отдан,
недостаёт принуждения.

---

## 11. Точки интеграции безопасности

Архитектура безопасности — FINAL FREEZE, отдельный трек. Здесь **только
перечень точек**, ни одна политика не проектируется.

**S-1. `wellness/admission.py` — допуск плана по вердикту safety-лестницы.**
`personal_plan_admission` (`:78-106`) отказывает в открытии weight-loss плана
при `goal_overridden_by ∈ {eating_disorder, pregnancy, breastfeeding,
bmr_floor}` (`:42-47`, `:102-103`) и при маркере `weight_kg` в
`assumed_inputs` (`:52`, `:104-105`). Значение веса не читается вообще
(AST-тест `test_no_weight_value_access_paths`, `tests/test_admission.py:159`).
Это **решение о допустимости, принимаемое по health-сигналам** — точка
интеграции по определению.
**Состояние:** вызывающих нет. Вход (`goal_overridden_by`, `assumed_inputs`)
реально производится в `nutrition/services/profile_upsert_service.py:224,234`
и `nutrition/serializers.py:508,512` — то есть **данные есть, функция есть,
провода нет**. Класс: `PARTIAL`.

**S-2. `wellness/fact_providers.py` — межтеменное чтение журналов питания.**
`WaterEntry.objects` / `FoodLog.objects` (`:23`, `:33`). Обе модели —
обычные `models.Model` без тенантных менеджеров (проверено:
`grep "objects = |all_tenants" nutrition/models.py` → пусто), так что
ловушка `STRICT_TENANT_SCOPE` здесь не срабатывает по построению. Но это
поведенческие данные о человеке, читаемые **без обращения к какому-либо
гейту согласия**: ни `body_observation_gate`, ни `ConsentType.HEALTH` в
цепочке adherence не участвуют.

**S-3. Consent-проверка health-класса живёт у потребителя, а не у владельца
данных.** `wellness/api.py:26-27` — `authentication_classes = []`,
`permission_classes = [IsBotServiceWithVerifiedClient]`. Это только
Bearer-токен + `X-External-User-ID`; проверки согласия на стороне Ayla нет.
Согласие проверяет **бот**: `ai-bot-platform@83ed56a9:apps/wellness_proactive/tasks.py:101-105`
требует пары `PERSONAL_DATA + HEALTH` **до** обращения к Ayla, и модуль прямо
объясняет почему (`:23-27`): «документ wellness-context — это health-class
данные, и та же пара согласий является основанием для их *чтения*».
То есть основание для чтения health-class данных проверяет тот, кто их
запрашивает. Класс: `PARTIAL`. **Сегодня без последствий** — документ всегда
`gated`.

**S-4. `IsBotServiceWithVerifiedClient` без второго фактора на GET.**
`users/permissions.py:154-161` сам называет defense-in-depth: view **обязан**
сверять `client_id` из тела с `request.user.id`, потому что Bearer — один
секрет. У `WellnessContextView` тела нет (GET), сверки нет. Утёкший токен +
подделанный заголовок = чтение документа за любого человека. Плюс
`resolve_external_user` **лениво создаёт proxy-User** на первом вызове
(`:148-150`) — то есть чтение способно порождать строку пользователя.
Сегодня документ отдаёт только коды закрытых гейтов.

**S-5. Истёкший горизонт порождает исходящий повод о здоровье.**
`apps/wellness_proactive/tasks.py:96-99, 176-186`: `progress_state ∈
{no_observations, baseline_only}` **или** `horizon_status == elapsed` →
повод `OBSERVE`. Дальше `vet_outbound` над сериализацией кодов (`:210-212`,
`:284-289`). Отправки нет; в beat задача не зарегистрирована; выключатель
`WELLNESS_PROACTIVE_ENABLED` по умолчанию `False`
(`:139-141`, и `apps/adminconsole/health.py:123-124` показывает его как
осознанно выключенный). Точка интеграции: **дата и состояние ряда решают,
обращаться ли к человеку по поводу здоровья**.

**S-6. Реестр свидетельств как точка допуска.** `EvidenceRegistryEntry` —
единственное место, где решается, что вообще считается свидетельством для
результата (`progress.py:71-80`). Писателя и принуждения approval нет (C-5).

**S-7. Обратные точки — где безопасность обеспечена структурно** (фиксирую,
чтобы трек безопасности не искал заново): `origin` без `inferred/derived`;
отсутствие `ACHIEVED`/`FAILED`; отсутствие полей значений в
`wellness-context` и в `Decision.as_log`; `append-only supersede` вместо
правки; отсутствие LLM во всём приложении; AST-тесты, запрещающие смыкание
Progress и Adherence.

---

## 12. Опасности миграции

**M-1. Включение любого writer'а без шага стирания создаёт неудаляемые
health-данные.** C-4: воронка 152-ФЗ и «удалить аккаунт» строк `wellness`
не касаются. Удаление аккаунта — мягкое (`users/services.py:1180-1230`:
анонимизация + `deleted_at`, строка `User` остаётся), поэтому
`on_delete=PROTECT` на FK ничего не блокирует и ошибку не поднимет — данные
просто тихо переживут «забудь всё». Сегодня предмета нет: строк нет.

**M-2. Экспорт неполон по тому же признаку.** Права субъекта гейты
пропускают (`subject_rights`), но выгружать нечем: `wellness` в экспорте
отсутствует.

**M-3. Реестр свидетельств меняет продуктовый смысл прогресса без миграции.**
Вставка одной строки `EvidenceRegistryEntry` немедленно расширяет ряд
(`progress.py:71-80`) — механизма approval нет (C-5).

**M-4. `PROTECT` повсюду.** Все шесть FK на пользователя и между моделями —
`PROTECT`. Любая будущая жёсткая чистка (в отличие от нынешней мягкой)
упрётся в порядок удаления.

**M-5. `instrument` в двух формах.** У `ProgressObservation` — `null=True`
(`models.py:249-254`), у `EvidenceRegistryEntry` — `default=""` не-NULL
(`:340-345`), и `progress.py:79` их согласует выражением
`instrument=entry.instrument or None`. Согласование живёт **в одной строке
запроса**, а не в ограничении. Правка любой из двух колонок ломает
сопоставление молча.

**M-6. `horizon_status` — property, а не колонка.** Отдать его в SQL-фильтр,
в индекс или в аннотацию нельзя без миграции. Любой потребитель,
рассчитывающий фильтровать по `elapsed` на стороне БД, наткнётся на это.

**M-7. Значения `Decimal` только в памяти.** `OutcomeProgress` — не таблица;
любая попытка «сохранить прогресс» превращает производную в источник истины,
что контракт запрещает (`progress.py:5-8`, AYLA-DEC-0082).

---

## 13. Что НЕ замерено — честно

| Предмет | Почему | Команда воспроизведения |
|---|---|---|
| Применены ли миграции `wellness` на пилоте (`api-dev.gobeauty.site`) и есть ли там строки | Нет доступа к боевой БД из этого окна | на хосте пилота: `python manage.py showmigrations wellness` и `SELECT count(*) FROM wellness_desiredoutcome, wellness_personalplan, wellness_planoutcomelink, wellness_progressobservation, wellness_evidenceregistryentry, wellness_planaction;` |
| Текущее число строк `goals_clientgoal` на пилоте | Цитируемый в `PROPOSAL §7.1` ноль датирован **25.08**; на 09.09 не перепроверен. `STALE_SPEC` для цитаты, `UNKNOWN_NOT_MEASURED` для факта | `SELECT count(*) FROM goals_clientgoal;` на пилоте |
| Прогон тестов `wellness` | Замер статический; тесты не запускались | `cd djangoproject-catalog && python -m pytest wellness/ --junitxml=out.xml -q` (числа брать из `--junitxml`, не из текста; `errors` и `failures` не складывать) |
| Поведение под `STRICT_TENANT_SCOPE=strict` в живом рантайме | Установлено только статически (у `nutrition` нет тенантных менеджеров) | замер на стенде с `STRICT_TENANT_SCOPE=strict` |
| Потребители вне четырёх проверенных деревьев | Проверены `djangoproject-catalog`, `ai-bot-platform@origin/dev`, `ayla-ai-core@origin/main`, `frontAyla`. Прочие каталоги рабочего пространства (`djangoproject`, `djangoproject-alpha`, `itsolve`, `migration-backup`, worktrees) не грепались | `grep -rl "DesiredOutcome\|PersonalPlan\|wellness-context" <дерево>` |
| Кто и когда обслуживает `/api/v1/customer/wellness/today` (упомянут в `scripts/pilot_smoke/scenarios.py:504`) | Определения маршрута в `djangoproject-catalog` нет; к приложению `wellness` отношения не имеет; вне предмета трека B | `grep -rn "customer/wellness" <все деревья>` |

---

## 14. Решения владельца

Формат: вопрос · runtime evidence · существующий канон · почему канон не
отвечает · варианты A/B · последствия · что блокирует. **Рекомендации нет
намеренно.**

### OD-GWP-1 — Сколько планов у человека с несколькими целями

**Вопрос.** Один активный `PersonalPlan` на человека, обслуживающий все его
результаты, — или один активный Plan на каждую цель?

**Runtime evidence.**
`djangoproject-catalog@95c917e6:wellness/models.py:120-126` —
`UniqueConstraint(fields=["user"], condition=Q(status="active"))`: **0..1
активный план на человека**. `:179-183` — единственность связи наложена на
`outcome`, не на `plan`: **один план держит N результатов**. Тесты
`tests/test_models.py:98-131` подтверждают оба поведения.
`ai-bot-platform@83ed56a9:docs/specs/PLAN_ENGINE_CONTRACT_v1.0.md §4.7` —
«Один Plan относится ровно к одному Goal (`goal_ref` скалярен). План,
обслуживающий две цели, не существует»; «Один Goal — не более одного
`ACTIVE` Plan».

**Существующий канон.** GOALS-R4 (owner, дословно 25.08): «одному плану иметь
N outcomes с разными сроками»; «Два одновременно активных Personal Plans для
одного Outcome пока НЕ вводить без отдельной продуктовой причины».
`PROPOSAL §2`: «`PersonalPlan` — 0..1 ACTIVE на человека — общий контейнер».
С другой стороны §4.7 выводит своё правило из канона §12.6 «Plan is one
proposed path».

**Почему канон не отвечает.** Оба утверждения опираются на канон и
несовместимы численно: при трёх активных целях первое даёт **один** план,
второе — **три**. Ни один документ не отменяет другой явно;
`PLAN_ENGINE_CONTRACT_v1.0` имеет статус **DRAFT** и написан позже
(07.09.2026), GOALS-R4 — owner ruling с объявленным приоритетом над
пересказами. Приоритет источников закрывает вопрос **происхождения**, но не
вопрос **продукта**: владелец мог с тех пор изменить намерение, и различие
видно человеку.

**Что видит человек.**
Вариант **A** (один общий план): экран «Мой план» один; цели — разделы
внутри; закрытие плана закрывает сопровождение всех целей сразу; adherence
считается по одному набору обязательств.
Вариант **B** (план на цель): у человека столько планов, сколько активных
целей; каждый закрывается отдельно; появляется вопрос «какой план сейчас
главный» и «что показывать на домашнем экране»; `PersonalPlan` нужно
дополнить ссылкой на цель и снять ограничение по пользователю.

**Последствия.**
A: `PLAN_ENGINE_CONTRACT_v1.0 §4.7` правится, `Plan` движка либо становится
тем же `PersonalPlan`, либо обязан объяснить, чем он от него отличается;
миграций нет.
B: миграция `PersonalPlan` (снятие `personalplan_one_active_per_user`,
добавление скалярной ссылки на цель), пересмотр `PlanOutcomeLink` (при плане
на цель связка вырождается), правка `context_read._plan_payload` (сегодня
возвращает **один** план), правка `wellness_proactive` (сегодня читает
`has_plan` как булев факт).

**Что блокирует.** Проектирование Plan Engine: §4.7 и §4.4 определяют
durable `Plan` с `plan_id`, не ссылаясь на существующую таблицу
`wellness_personalplan` — до ответа неизвестно, одна это сущность или две.

### OD-GWP-2 — Где живёт целевая дата у цели, у которой нет измеримого результата

**Вопрос.** Человек называет дату события («свадьба 12 июня»), но
измеримого результата (веса, отёчности) у него нет. Куда кладётся дата?

**Runtime evidence.** Единственная дата домена —
`wellness/models.py:163-167` `PlanOutcomeLink.target_date`. У
`PlanOutcomeLink` поле `plan` — обязательный FK (`:153-157`), поле
`outcome` — обязательный FK (`:158-162`). Ни `nullable`, ни отдельной
таблицы горизонта нет. **Следствие (ответ D4): дата физически требует, чтобы
уже существовали и `PersonalPlan`, и `DesiredOutcome`.** У `ClientGoal`
(`goals/models.py:36-99`) поля даты нет.

**Существующий канон.** Решение владельца: целевую дату **сейчас не
добавляем** в `ClientGoal` — сначала нужен authority reconciliation с
`wellness`. GOALS-R4: горизонт живёт на связи. `PROPOSAL §3`: «`target_date`
NULL легален — цель без срока».

**Почему канон не отвечает.** Канон сказал, куда дату **не** класть
(`ClientGoal`), и куда её класть **для измеримого результата** (на связь).
Он не сказал, что делать с датой, когда измеримого результата нет вовсе —
а именно это самый частый случай салонной цели («подготовиться к
мероприятию»). Оба выхода допустимы и продуктово различны.

**Что видит человек.**
Вариант **A** (дата всегда через связь): чтобы принять дату, система обязана
завести `PersonalPlan` и `DesiredOutcome`-оболочку без измеримого состояния.
Но `CheckConstraint desiredoutcome_direction_or_numeric_present`
(`models.py:80-86`) требует заполнить `direction` **или**
`desired_state_numeric` — то есть оболочка обязана солгать о направлении.
Вариант **B** (горизонт отвязан от измеримости): горизонт становится
самостоятельным носителем, применимым к цели без результата; `wellness`
перестаёт быть единственным владельцем срока.

**Последствия.**
A: ослабление или обход `CheckConstraint`; появление «пустых» результатов,
которые `progress` вернёт как `no_measure`; каждая датированная цель тянет за
собой создание плана, а планов у человека 0..1 (см. OD-GWP-1).
B: новая сущность или колонка вне `wellness`; пересмотр GOALS-R4 в части
«горизонт живёт только на связи»; `horizon_status` придётся считать в двух
местах либо вынести.

**Что блокирует.** Любой разговорный или анкетный путь, где человек называет
дату; `goals/anketa.py` сегодня даты не спрашивает, и добавить её некуда.

### OD-GWP-3 — Одна вещь называется планом или две

**Вопрос.** `wellness.PersonalPlan` (существует, пустой контейнер статуса) и
`Plan` из `PLAN_ENGINE_CONTRACT_v1.0 §4.4` (проектируется, durable, с
ревизиями и шагами) — это одна сущность или две разные, живущие рядом?

**Runtime evidence.** `wellness/models.py:93-129`: у `PersonalPlan` ровно
`user`, `status`, `created_at`, `closed_at`. Детей двое:
`PlanOutcomeLink` (результаты со сроками) и `PlanAction` (обязательства
самозаписи, `:362-417`). Ни у `PersonalPlan`, ни у `PlanAction` нет ни
`capability_ref`, ни ссылки на услугу, ни ревизий, ни снимков.
`PLAN_ENGINE_CONTRACT_v1.0 §4.4` описывает `Plan` с `plan_id uuid4, бэкенд
Ayla`, `goal_ref`, `status ACTIVE|PAUSED|SUPERSEDED|ARCHIVED`,
`current_revision_id` и иммутабельной `PlanRevision` со `steps_snapshot[]`
— **и ни разу не упоминает существующую таблицу `wellness_personalplan`**.

**Существующий канон.** `DRAFT_ADHERENCE_CONTRACT §2`: «`PlanAction` —
обязательство внутри Personal Plan. **Не engine**». `PROPOSAL §2`:
`PersonalPlan` — «общий контейнер». `OD_CARE_PLAN_BOUNDARY п.7` (цитируется
в `DRAFT_ADHERENCE_CONTRACT §6`): «Personal Plan engine не строится до
первого работающего вертикального среза».

**Почему канон не отвечает.** Канон запретил **строить** движок и разрешил
**завести** контейнер. Он не сказал, чем эти два объекта станут друг другу,
когда движок появится. Это различие видно человеку, а не только схеме.

**Что видит человек.**
Вариант **A** (одна сущность): «мой план» — один объект, в котором и
обязательства самозаписи (`log_water`), и предлагаемые Ayla шаги
(процедуры). `PersonalPlan` дорастает до `Plan` движка: статусы `PAUSED`/
`SUPERSEDED`/`ARCHIVED`, ревизии, `goal_ref`.
Вариант **B** (две сущности): «план заботы» (`wellness`, обязательства и
горизонты) и «план из движка» (стратегия по услугам) — два разных объекта,
которые человек видит по отдельности и которые могут расходиться.

**Последствия.**
A: миграция `PersonalPlan` — расширение перечисления статусов (сегодня их
два), добавление `goal_ref` и ревизий; правка `context_read._plan_payload`,
который отдаёт наружу **один код статуса**, и `wellness_proactive`, который
читает его как булев `has_plan`; `PlanAction` придётся сосуществовать со
`PlanStep` в одном контейнере, не имея `outcome_ref`.
B: два объекта с пересекающимся именем в интерфейсе; вопрос, чей горизонт
авторитетен, если оба ссылаются на один `DesiredOutcome`; adherence и
стратегия никогда не сводятся — что согласуется с AYLA-DEC-0082, но требует
явного продуктового объяснения человеку.

**Что блокирует.** Оба контракта Plan Engine (§4.4 и §4.7) и любую попытку
«просто включить» `wellness`: включённый writer `PersonalPlan` создаёт
строки, которые движок потом либо унаследует, либо будет обязан
игнорировать.

---

## 15. Что НЕ является решением владельца

Перечисляю явно, чтобы эти пункты не попали в очередь к владельцу.

1. **Носитель целевой даты (C-1).** Закрывается прецедентом источников:
   owner ruling GOALS-R4 старше по приоритету, рантайм и `PROPOSAL §3` ему
   соответствуют. Правится документ бот-репозитория.
2. **Словарь `horizon_kind`/`horizon_origin` (C-3).** Протухшая спека; в
   рантайме этих полей нет и никогда не было.
3. **Шаг стирания и экспорт wellness (C-4).** `PROPOSAL §8` уже выбрал hard
   delete и путь через существующую воронку. Недостаёт исполнения.
4. **Принуждение owner approval у `EvidenceRegistryEntry` (C-5).** Приказ
   отдан (GOALS-R1), нужен механизм — инженерная работа.
5. **Отсутствие писателей.** Прямое следствие уже принятого fail-closed:
   Gate D до утверждения scope `goal_memory`, Gate O до Registry amendment +
   Privacy/Legal + verified consent integration.
6. **Ограничение «0..1 активный план на человека» само по себе.** Это
   принятый контракт (`PROPOSAL §2`, GOALS-R4), а не старое предположение.
7. **Отсутствие `outcome_ref` у `PlanAction`.** Осознанная граница первого
   среза (`DRAFT_ADHERENCE_CONTRACT §4`, §6).
8. **Отсутствие админки и сериализаторов у `wellness`.** Следствие того же
   fail-closed: нечего администрировать, пока нет писателей.
9. **Асимметрия «`ClientGoal` пишется без гейта, `DesiredOutcome` — с
   гейтом».** `PROPOSAL §7.1` назвал это governance debt и назначил путь
   закрытия (тот же `goal_intention_gate` станет policy boundary
   `ClientGoal` отдельной задачей).
10. **Имя `PlanAction`.** Имя класса ничего не решает; по полям и предмету
    это не `PlanStep` (§7).

---

## 16. Точные команды воспроизведения

```bash
# --- базы замера ---
cd C:/Users/user/PycharmProjects/Ayla/djangoproject-catalog && \
  git rev-parse HEAD && git rev-parse origin/dev && git status --porcelain
cd C:/Users/user/PycharmProjects/Ayla/ai-bot-platform && \
  git rev-parse origin/dev && git rev-parse --abbrev-ref HEAD
cd C:/Users/user/PycharmProjects/Ayla/ayla-ai-core && \
  git branch -a && git rev-parse origin/main      # origin/HEAD -> origin/main; origin/dev НЕТ

# --- состав приложения ---
cd C:/Users/user/PycharmProjects/Ayla/djangoproject-catalog
wc -l wellness/*.py wellness/tests/*.py wellness/migrations/*.py
ls wellness/                                       # нет admin.py/serializers.py/tasks.py/management
grep -rn -i "llm\|openai\|gpt\|prompt\|anthropic" wellness/     # пусто

# --- перепись потребителей ---
grep -rn "wellness" --include=*.py . | grep -v "^./wellness/"
grep -rn "DesiredOutcome\|PersonalPlan\|PlanOutcomeLink\|PlanAction\|ProgressObservation\|EvidenceRegistryEntry" \
     --include=*.py . | grep -v "^./wellness/"                  # пусто
for f in record_outcome record_observation goal_intention_gate body_observation_gate \
         personal_plan_admission compute_plan_adherence compute_outcome_progress \
         build_wellness_context count_facts; do
  echo "--- $f"; grep -rn "$f" --include=*.py . | grep -v "def $f"; done

# --- воронка стирания и экспорт ---
grep -rn "erase_personal_context" --include=*.py .
sed -n '100,155p' users/personal_context_erasure.py
sed -n '118,180p' users/personal_data_api.py

# --- бот-сторона ---
cd C:/Users/user/PycharmProjects/Ayla/ai-bot-platform
git grep -n "wellness\|DesiredOutcome\|PersonalPlan\|PlanOutcomeLink" origin/dev
git show origin/dev:docs/specs/PLAN_ENGINE_CONTRACT_v1.0.md | sed -n '70,95p;155,290p'
git show origin/dev:docs/HANDOFF_PLAN_ENGINE.md | sed -n '68,92p'
git show origin/dev:docs/HANDOFF_PLANNING_RULES_REGISTRY.md | sed -n '85,100p'
git show origin/dev:docs/OPEN_DECISIONS.md | sed -n '395,455p;4010,4075p'
git show origin/dev:apps/wellness_proactive/tasks.py
git show origin/dev:apps/integrations/ayla/wellness_context_client.py

# --- ayla-ai-core: предмет отсутствует ---
cd C:/Users/user/PycharmProjects/Ayla/ayla-ai-core
git grep -l -i "wellness\|DesiredOutcome\|PersonalPlan" origin/main    # пусто

# --- источники решений (вне всех трёх репозиториев) ---
cd C:/Users/user/PycharmProjects/Ayla/docs
sed -n '1,130p' OD_GOALS_RULINGS.md          # GOALS-R1..R4
sed -n '1,267p' PROPOSAL_GOALS_MODEL_FINAL.md
cat DRAFT_ADHERENCE_CONTRACT.md
```

**Не воспроизведено намеренно:** тесты не запускались, БД не открывалась,
`wellness/` не менялся, ветки не создавались, worktree не заводился.
