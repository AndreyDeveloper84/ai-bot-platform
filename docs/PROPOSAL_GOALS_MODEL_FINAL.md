# ФИНАЛЬНОЕ ПРЕДЛОЖЕНИЕ — домен измеримых целей (V3, с amendments)

**Подготовлено окном goals 25.08.** Заменяет `docs/PROPOSAL_GOALS_MODEL_V2.md`.
**V3 принят концептуально** (`docs/OD_GOALS_RULINGS.md` § REVIEW FINAL);
сюда внесены пять amendments (A–E) и GOALS-R6 — после них разрешена первая
миграция каркаса `wellness`.

**Основания в порядке приоритета:** `docs/OD_GOALS_RULINGS.md` (GOALS-R1..R6,
amendments A–E, дословно), review главного окна (`docs/REPLY_GOALS.md`),
В-1..В-6, `docs/SPEC_CARE_CONTRACT.md`, Consent Scope Registry.

**Разрешение на миграцию НЕ является разрешением включить persistent writes**
(GO): Gate D — только после утверждения `goal_memory`; Gate O — до Registry +
Privacy/Legal + verified consent integration. После миграции — настоящий
readback схемы и review перед включением любого writer.

---

## 1. Aggregate boundaries

Новый bounded context в **Ayla backend** (`djangoproject`), имя приложения —
**`wellness`** (принято, точка 5 review; основание В-2).

**Внутри:** `DesiredOutcome`, `PersonalPlan`, `PlanOutcomeLink`,
`ProgressObservation`, данные Evidence Registry (§5).

**Снаружи:** `goals.ClientGoal` (не трогаем; `goal_intention_gate` станет его
policy boundary отдельной задачей — GOALS-R6, долг, не прецедент); нормы
питания (Nutrition — потребитель, зависимость односторонняя); Progress
(производная, не сущность); NBA (Recommendation context).

**Ключ владения — человек, tenant-less.** Прецедент: `ClientGoal` ссылается
только на `settings.AUTH_USER_MODEL` (SPEC §6.2).

## 2. Entities + cardinality

| Сущность | Кардинальность | Хранимое состояние |
|---|---|---|
| `PersonalPlan` | 0..1 ACTIVE на человека — общий контейнер (OD-GOAL-4); закрытые ряды = история | `status: ACTIVE \| CLOSED_BY_USER` |
| `DesiredOutcome` | 0..N активных на человека, **с первого дня**; аналога `clientgoal_one_active_per_client` нет | `status: OPEN \| CLOSED_BY_USER` |
| `PlanOutcomeLink` | ≤1 ACTIVE на outcome (GOALS-R4); N за жизнь результата | `status`, `target_date` (NULL легален) |
| `ProgressObservation` | 0..N на человека | `superseded_by` (§8) |
| `EvidenceRegistryEntry` | курируемые данные; изменение — owner approval | — |

`ACHIEVED` / `FAILED` отсутствуют во всех перечислениях — система физически
не может объявить цель достигнутой или проваленной.

## 3. PlanOutcomeLink

```
plan         FK -> PersonalPlan
outcome      FK -> DesiredOutcome
target_date  Date, NULL = цель без срока
status       ACTIVE | CLOSED_BY_USER
created_at / closed_at
```

Partial unique: не более одной ACTIVE связи на `outcome`.

**Продолжение (GOALS-R4 + AMENDMENT A):** тот же outcome в новом плане —
новая строка связи; старая `CLOSED_BY_USER` остаётся историей. Результат не
клонируется. **Новый план baseline не сбрасывает** — baseline привязан к
outcome, не к связи (§5).

## 4. Observation value model

Первый срез (GOALS-R1), оба `origin = user_stated`:

| Тип | Значение | Колонка | Инструмент (AMENDMENT B) |
|---|---|---|---|
| `WEIGHT` | numeric, unit = kg (фиксирована) | `value_numeric` | — (instrument NULL) |
| `SELF_ASSESSMENT` | ordinal 0–3 + canonical label | `value_ordinal` | **`NOTICEABILITY_0_3_V1`** — обязателен |

- **Versioned instrument (AMENDMENT B).** `SELF_ASSESSMENT` несёт
  `instrument` — версионированный код шкалы. Canonical labels
  (`0 Не замечаю … 3 Сильно заметна`) принадлежат версии инструмента и
  сервером хранятся как константа. **Смена формулировок — новая версия
  инструмента, не правка на месте**: иначе ряд перестаёт быть сравнимым сам
  с собой. Экран и бот показывают labels дословно.
- Типизированные колонки, не JSONB; CheckConstraint: заполнена ровно колонка
  своего типа. Универсальный реестр значений запрещён (WD:1607-1612).
- LLM не маппит прозу на шкалу: значение входит только через фиксированный
  контрол; свободный текст сам не становится уровнем (GOALS-R2).
- `origin` обязателен; `measured`/`inferred`/`derived` в перечислении нет —
  выводимое наблюдение невозможно записать.

## 5. Evidence registry vs concrete provenance

**Registry (допустимость) — тройка, не пара (AMENDMENT B):**
`(outcome_target × observation_type × origin × instrument)` +
`approved_by/approved_at`. Первый срез:

```
(BODY_WEIGHT, WEIGHT,          user_stated, instrument = NULL)
(EDEMA,       SELF_ASSESSMENT, user_stated, NOTICEABILITY_0_3_V1)
```

Расширение — отдельный owner approval (GOALS-R1). Запись наблюдения
валидируется против реестра fail-closed.

**Baseline (AMENDMENT A).** Baseline — самое раннее допустимое наблюдение
**не раньше `DesiredOutcome.created_at`**: история человека до появления
Outcome не становится его Progress. **Новый Personal Plan того же Outcome
baseline не сбрасывает** — ряд привязан к результату, не к связи.
Progress = последнее наблюдение − baseline. Скоуп по периоду связи не
вводится (ужесточается запросом без миграции).

**Concrete provenance — место показано, не реализовано.** Первый срез:
детерминированный запрос по правилу выше. Место под связь: аддитивная
`ProgressEvidence(outcome FK, observation FK)` в будущем; схема связь не
делает невозможной.

## 6. Lifecycle / horizon state machine

| Ось | Значения | Кто меняет |
|---|---|---|
| `DesiredOutcome.status` | `OPEN → CLOSED_BY_USER` | только человек |
| `PersonalPlan.status` | `ACTIVE → CLOSED_BY_USER` | только человек |
| `PlanOutcomeLink.status` | `ACTIVE → CLOSED_BY_USER` | только человек; продолжение = новая связь |
| `PlanOutcomeLink.horizon_status` | `NONE \| UPCOMING \| ELAPSED` | **вычисляется** (принято): `target_date IS NULL → NONE`; `today ≤ target_date → UPCOMING`; иначе `ELAPSED` |

`ELAPSED` — context fact для существующей Decision Policy; автозакрытия нет,
Progress не обнуляется, observations не удаляются, `no_action` валиден.

## 7. Consent enforcement — две именованные точки, разной природы

**AMENDMENT E (форма сигнала, общая для обеих точек).** Сервис **не принимает
`consent=True/False`**. Единственный вход — типизированный
`ConsentAttestation` с `authority` и `provenance` (кто утверждал, на каком
основании, scope, document_version). Топология подключения не выбирается до
Registry amendment; fail-closed сохраняется. В аудит пишется заявленное
основание — видно, *на каком основании* запись прошла.

### 7.1 Gate D — `goal_intention_gate` (намерение)

**GOALS-R6:** для persistent `explicit_goal` создаётся **отдельный scope
`goal_memory`** — `preference_memory` не расширяется. Allowed data:
`explicit_goal`; provenance: `user_stated` only; не входят body observations,
symptom, inferred/derived goals, behavioral inference.

- **Одна именованная точка — `goal_intention_gate`.** Она же станет policy
  boundary существующего `ClientGoal` — отдельной задачей, не этой
  миграцией.
- Текущая запись `ClientGoal` без scope — **governance debt, не прецедент**.
  **Consent backfill существующих данных не выполнять** (решение владельца).
  **Замер долга на пилоте 25.08 (главное окно): `goals_clientgoal` — 0
  строк.** Долг существует в коде, но не в данных: путь построен без
  основания и **ни разу не использован**. Следствия: backfill не имеет
  предмета; гейт можно ставить сразу и жёстко, без режима совместимости;
  долг не блокирует пилот и чинится тем же `goal_intention_gate`, отдельной
  работы не требует.
- Gate D **fail-closed до утверждения `goal_memory`** (GO): каркас пишется,
  writer не включается.

### 7.2 Gate O — body observations

- **Fail-closed до Registry amendment + Privacy/Legal + verified consent
  integration** (GOALS-R5/R6, GO). **Никаких bypass / feature-flag
  разрешений.**
- **AMENDMENT C — гейт защищает product processing, не права субъекта.**
  Это **свойство гейта, не исключение в вызывающем коде**: гейт принимает
  `purpose`. Для `purpose = processing` (запись, чтение значений для
  Progress/Recommendation) — требуется валидная attestation. Для
  `purpose = subject_rights` (inspect / export / correction / deletion уже
  сохранённого, в том числе **после revoke**) — гейт не блокирует. Ночной
  прецедент обратной ошибки: «забудь всё», загейтованное на отозванное
  согласие, делало стирание невозможным ровно тогда, когда оно обязательно.
- После revoke observations **не используются** для Progress/Recommendation
  до нового разрешения (AMENDMENT C) — при fail-closed это состояние по
  умолчанию.

### 7.3 Fail-closed доказуем, не заявлен

В definition of done каркаса — **постоянная проверка в CI**: запись body
observation без attestation отказана; boolean/self-declared «consent»
невозможен контрактно (AMENDMENT E); запись намерения до утверждения
`goal_memory` отказана. Тест краснеет при любом ослаблении.

## 8. Deletion / correction semantics

**Удаление человека (152-ФЗ).** Шагом в существующую воронку Ayla-стороны
(`InternalPersonalDataDeleteView → erase_personal_context`, C5.2), не своим
каскадом. Идемпотентно; per-step честный отчёт; аудит — actor + scope,
никогда значения. Путь прав субъекта **не проходит Gate O** (AMENDMENT C).

- `ProgressObservation` — **hard delete**; Progress производный, исчезает сам.
- `DesiredOutcome` / `PersonalPlan` / `PlanOutcomeLink` — удаляются; в аудит
  идут коды, не тексты.

**Удаление одной точки** — hard delete, ряд пересчитывается при проекции.

**Исправление — append-only supersede** (принято): старая строка
`superseded_by`, исключена из прогресса, хранится в retention; экспорт полон.
«Удалить» стирает, «исправить» оставляет историю.

**Место под текст различия операций** (требование review): документ проекции
для каждой деструктивной операции несёт серверную строку `operation_notice`
(что произойдёт и что необратимо); экран показывает дословно.

## 9. API / document projection + частичный успех

Маршрут один: **модель → сериализатор → эфемерный документ → экран**.
Auth — как у `goals/api.py` (`IsBotServiceWithVerifiedClient`).

### 9.1 Частичный успех + идемпотентность

GOALS-R5 §3: одно сообщение может порождать два объекта. Отказ записи
Observation **не уничтожает** запись Desired Outcome.

- **Общая транзакция запрещена.** Два объекта пишутся независимо, каждый
  через свой гейт.
- **AMENDMENT D — идемпотентность на уровне запроса И независимых
  подопераций.** Composite endpoint принимает `idempotency_key`; результат
  запроса сохраняется, повтор с тем же ключом возвращает тот же исход.
  Подоперации `outcome` и `observation` идемпотентны каждая сама по себе:
  **retry после partial success не создаёт второй `DesiredOutcome`** (ключ
  подоперации → существующая строка, не новая).
- Ответ — per-object исходы: `outcome: recorded|refused(reason_code)`,
  `observation: recorded|refused(reason_code)` (прецедент `DeleteStep`).
- **Что человек видит:** «Цель записана. Текущий замер не сохранён» —
  нейтральная серверная строка, без оценки; состояние прогресса честно
  остаётся `no_observations`.

### 9.2 Остальные endpoint'ы

- `POST …/observations/` — `type` + значение + `instrument` по §4; `origin`
  клиент не присылает — сервер ставит `user_stated` сам.
- `DELETE …/observations/{id}/` — §8 (путь прав субъекта).
- Документ состояния, секция `plan`: per-outcome `{target, direction,
  desired_state, link_status, horizon_status, progress_state, progress_text,
  last_observation}`; для деструктивных операций — `operation_notice`.
- `progress_state ∈ {no_measure, no_observations, baseline_only, derived}`;
  `progress_text` — серверная строка, только арифметика.

**Бот** — тот же документ; запись наблюдений ботом — тот же `POST`,
`origin` всегда `user_stated`. Заготовка контракта шага 6, не его начало:
`nutrition/` не трогаем.

## 10. Release gate и оценка

```
Schema ready → code deployed fail-closed → Privacy/Legal + Registry
   → consent integration verified → body observation writes enabled
```

**41 SP** (принято главным окном). Amendments легли в существующие пункты:
A/B — п.3 (baseline-правило, versioned instrument), C/E — п.1/3 (форма
гейтов), D — п.3 (идемпотентность). Пересмотра не требуется.

## Следующий шаг

Первая миграция каркаса `wellness`: четыре сущности + Evidence Registry +
оба гейта fail-closed + CI-проверка §7.3. Без writers. После миграции —
**настоящий readback схемы из базы** (имена, типы, ограничения, индексы —
не `showmigrations`) и review главного окна перед включением любого writer.

## Приложение — источники

| Утверждение | Файл | Место |
|---|---|---|
| GOALS-R1..R6, amendments A–E | `docs/OD_GOALS_RULINGS.md` | целиком |
| Review V2; release gate; «доказуем, не заявлен» | `docs/REPLY_GOALS.md` | §REVIEW V2, §GOALS-R5 ПРИШЛО, §GO ПОЛУЧЕН |
| В-1..В-6 | `docs/OD_CARE_CONTRACT_RULINGS.md` | §2 |
| Две линии прогресса; формулировки | `docs/SPEC_CARE_CONTRACT.md` | §3 |
| explicit_goal / goal_memory: история вопроса | Consent Scope Registry | §4, §5.1, §5.7 |
| Частичные исходы честно | `ai-bot-platform/.../privacy.py` | DeleteStep |
| Воронка удаления Ayla | `djangoproject: users/personal_data_api.py` | C5.2 |
