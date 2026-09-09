# ПРЕДЛОЖЕНИЕ V2 — домен измеримых целей

**Подготовлено окном goals 25.08.** Обновляет `docs/PROPOSAL_PROGRESS_OBSERVATIONS.md`
по десяти пунктам из `docs/OD_GOALS_RULINGS.md` (приоритет — за ним, дословно).
**Статус: предложение, не миграция.** Следующий шаг — review главного окна,
первая миграция только после него.

---

## 1. Aggregate boundaries

Новый bounded context в **Ayla backend** (`djangoproject`), рабочее имя
приложения — **`wellness`** (В-2: wellness/obs bounded context; не бот и не
`NutritionProfile`).

**Внутри границы:** `DesiredOutcome`, `PersonalPlan`, `PlanOutcomeLink`,
`ProgressObservation`, данные Evidence Registry (§5).

**Снаружи и не пересекается:**

- `goals.ClientGoal` — semantic framing, **не трогаем** (OD-GOAL-1). Новое
  приложение отдельно от `goals/`, чтобы не смешивать framing с измеримым
  слоем.
- Нормы питания — Nutrition, **потребитель** (В-2); направление зависимости
  одно: wellness знает про Nutrition, Nutrition про wellness — нет.
- Progress — **не сущность**, производная (§9).
- NBA — Recommendation bounded context; план поставляет context facts, не
  механизм (SPEC §6.3).

**Ключ владения — человек, tenant-less.** Прецедент уже в коде:
`ClientGoal` ссылается только на `settings.AUTH_USER_MODEL`, без tenant FK;
обоснование — SPEC §6.2: цель принадлежит человеку, не салону. Новые
сущности — так же.

## 2. Proposed entities + cardinality

| Сущность | Кардинальность | Хранимое состояние |
|---|---|---|
| `PersonalPlan` | **0..1 ACTIVE на человека** — общий контейнер (OD-GOAL-4); закрытые ряды = история | `status: ACTIVE \| CLOSED_BY_USER` |
| `DesiredOutcome` | **0..N активных на человека, с первого дня.** Никакого аналога `clientgoal_one_active_per_client` — ловушка брифа, обойдена в схеме, а не обещанием | `status: OPEN \| CLOSED_BY_USER` |
| `PlanOutcomeLink` | ≤1 ACTIVE на один outcome (следствие GOALS-R4: два плана на один outcome не вводим); N за жизнь результата | `status` (§3), `target_date` (NULL легален) |
| `ProgressObservation` | 0..N на человека | `superseded_by` (§8) |
| `EvidenceRegistryEntry` | курируемые данные; изменение — owner approval (GOALS-R1) | — |

Значений `ACHIEVED` / `FAILED` нет ни в одном перечислении — та же техника
структурного запрета, что с `inferred` в `origin`: чего нет в enum, система
не произносит.

## 3. PlanOutcomeLink

Смысл по GOALS-R4: «как данный outcome участвует в данном плане».

```
plan         FK -> PersonalPlan
outcome      FK -> DesiredOutcome
target_date  Date, NULL = цель без срока (§4 v1, подтверждено)
status       ACTIVE | CLOSED_BY_USER          (хранится)
created_at / closed_at
```

Ограничение: не более одной ACTIVE связи на `outcome` (partial unique —
здесь оно уместно, в отличие от `DesiredOutcome`).

**Кейс продолжения (дословное основание GOALS-R4):** тот же outcome входит в
новый план с другим горизонтом — **новая строка связи**, старая
`CLOSED_BY_USER` остаётся историей. Результат не клонируется и не
перезаписывается.

## 4. Observation value model

Два типа первого среза (GOALS-R1), оба `origin = user_stated`:

| Тип | Значение | Хранение |
|---|---|---|
| `WEIGHT` | numeric, unit = kg (фиксирована) | `value_numeric` |
| `SELF_ASSESSMENT` | ordinal 0–3 + canonical label (GOALS-R2) | `value_ordinal` |

- **Типизированные колонки, не JSONB.** CheckConstraint: заполнена ровно та
  колонка, которую объявляет тип. Универсальный реестр значений не строим —
  прямой запрет WD:1607-1612 (ни Value/Unit Registry, ни JSON/Pydantic-схем).
- **Canonical labels** (`0 Не замечаю … 3 Сильно заметна`) — серверная
  версионируемая константа, отдаётся в документ; экран и бот показывают
  дословно, не сочиняют.
- **LLM не маппит прозу на шкалу** (GOALS-R2): значение входит только через
  фиксированный контрол шкалы. Свободный текст «да отёки сильные» не
  становится тройкой — contract flow с явным выбором уровня, иначе наблюдения
  нет.
- `origin` обязателен; значений `measured`/`inferred`/`derived` в перечислении
  **нет** — выводимое наблюдение невозможно записать (принято главным окном).

## 5. Evidence registry vs concrete provenance

Две задачи разведены, как требует владелец:

**Evidence Registry (допустимость).** Записи
`(outcome_target × observation_type × origin)` + `approved_by/approved_at`.
Первый срез:

```
(BODY_WEIGHT, WEIGHT, user_stated)
(EDEMA,      SELF_ASSESSMENT, user_stated)
```

Расширение — отдельный owner approval (GOALS-R1). Запись наблюдения
валидируется против реестра: незарегистрированная пара не пишется
(fail-closed).

**Concrete provenance (место показано, не реализовано).** В первом срезе
происхождение прогресса вычислимо детерминированным запросом: все наблюдения
человека, чей тип допустим для `target` результата. Ряд **общий** для двух
результатов с одинаковым target — это корректно, а не дыра: факт один,
результатов два.

Место под связь: будущая таблица `ProgressEvidence(outcome FK, observation
FK)` — **аддитивная**, не требует миграции существующих строк и не меняет их
смысл. Схема связь не делает невозможной — требование выполнено формой, а не
обещанием.

Правило ряда первого среза: baseline = самое раннее наблюдение допустимого
типа; progress = последнее − baseline. Скоуп ряда по периоду связи — сознательно
не вводится (ужесточается запросом позже без миграции).

## 6. Lifecycle / horizon state machine

| Ось | Значения | Кто меняет |
|---|---|---|
| `DesiredOutcome.status` | `OPEN → CLOSED_BY_USER` (терминально) | только человек; календарь — никогда (GOALS-R3) |
| `PersonalPlan.status` | `ACTIVE → CLOSED_BY_USER` | только человек |
| `PlanOutcomeLink.status` | `ACTIVE → CLOSED_BY_USER` | только человек; продолжение = новая связь (§3) |
| `PlanOutcomeLink.horizon_status` | `NONE \| UPCOMING \| ELAPSED` | **вычисляется, не хранится** |

**Отмеченное решение (к review):** `horizon_status` — вычислимое поле:
`target_date IS NULL → NONE`; `today ≤ target_date → UPCOMING`; иначе
`ELAPSED`. GOALS-R3 требует разделить две оси — ось отделена; хранить
производное значение значит завести дрейф (дата прошла ночью, строка устарела).
Если главное окно хочет хранимую колонку — скажет, цена известна (джоба
перевода + гонка).

`ELAPSED` сам ничего не делает: это context fact для существующей Decision
Policy (review — GOALS-R3), `no_action` — валидный исход. Автозакрытия нет,
Progress не обнуляется, observations не удаляются.

## 7. Consent enforcement point

**Одна точка, не россыпь.** Публичная поверхность контекста — сервисный
модуль (`record_observation`, построители проекции); **модели не
импортируются нигде вне `wellness`**. Гейт живёт на границе данных, и новый
экран получает его бесплатно — урок `master-api.ts`, где точечный список
запрещённых PII-полей применялся к одному экрану из двух и через второй
утекали цифры телефона.

Инструмент: линт границ импортов. Прецедент — `ai-bot-platform/tools/lint/
import_boundaries.py`, `red_zone_guard.py`; в `djangoproject` аналога нет —
добавить тем же срезом, дёшево.

**Честно названный разрыв (вопрос к review):** consent-записи живут в боте
(`ai-bot-platform/apps/consent`), наблюдения — в Ayla backend. Источник
сигнала согласия для Ayla-стороны — единственный реальный вопрос раздела.
Предложение: точка строится **fail-closed сразу** — без явно переданного
подтверждённого сигнала ни записи, ни чтения значений; до Registry amendment
источник не подключён, и гейт отказывает всегда — что ровно и требуется:
**persistent body observations BLOCKED** (GOALS-R1). Варианты источника
(бот аттестует при вызове / Ayla читает mirror) — на review.

`ConsentType.HEALTH` — технический reuse candidate, **не правовое основание**
(поправка владельца принята).

## 8. Deletion / correction semantics

Образец — `apps/identity/services/privacy.py`, дыры его учтены (обещание
шире действия — пять ночных задач).

**Удаление человека (152-ФЗ).** Ayla-сторона уже имеет единую воронку:
`InternalPersonalDataDeleteView → erase_personal_context(user, initiator=…)`
(C5.2, `users/personal_data_api.py`). Таблицы `wellness` добавляются **шагом
в эту воронку**, а не своим каскадом: идемпотентно, per-step честный отчёт,
аудит пишет actor + scope — никогда не значения.

- `ProgressObservation` — **hard delete**: чистые персональные данные, никем
  не защищены `PROTECT`; Progress производный — исчезает сам (SPEC §1.3).
- `DesiredOutcome` / `PersonalPlan` / `PlanOutcomeLink` содержат дословное
  намерение человека — тоже удаляются; в аудит идут коды, не тексты
  (прецедент reason code из AUDIT_CARE_RUNTIME).

**Удаление одной точки.** Человек может удалить отдельное наблюдение («внёс
не то») — hard delete, ряд пересчитывается при следующей проекции.

**Исправление — append-only supersede (отмечено владельцу).** Исправленная
строка помечается `superseded_by`, исключается из прогресса, хранится в
пределах retention. Основание — прецедент памяти (DRF-1262) и логика
`privacy.py`: исправленная строка всё ещё «обрабатывается», значит экспорт
обязан быть полным. Различие операций сознательное: **«удалить» стирает,
«исправить» оставляет историю.**

## 9. API / document projection

Маршрут один (бриф): **модель → сериализатор → эфемерный документ → экран**.
Второй не заводится.

Внутренние endpoint'ы — тот же auth-паттерн, что у `goals/api.py`
(`IsBotServiceWithVerifiedClient`, `X-External-User-ID`):

- `POST …/observations/` — тело: `type` + значение по §4. **`origin` клиент
  не присылает — сервер ставит `user_stated` сам**, чтобы клиент физически
  не мог объявить `measured`.
- `DELETE …/observations/{id}/` — §8.
- Документ состояния расширяется секцией `plan`: per-outcome —
  `{target, direction, desired_state, link_status, horizon_status,
  progress_state, progress_text, last_observation: {value, label, at}}`.
  `progress_state ∈ {no_measure, no_observations, baseline_only, derived}`
  (v1 §3); `progress_text` — серверная строка. Экрану нечего фильтровать и
  вычислять — инвариант «тупого отрисовщика» держится.

**Что пишется до consent amendment (флаг к review).** Предложение считает:
`DesiredOutcome` / `PersonalPlan` / `PlanOutcomeLink` — данные того же
класса, что `ClientGoal` (заявленное намерение; категория `explicit_goal` в
Registry §4), и пишутся под существующим основанием. **BLOCKED — только
persistent body observations** (дословно GOALS-R1). Если главное окно
читает OD-GOAL-3 строже («намерение — только в рамках утверждённого
consent/policy») и считает, что намерение тоже ждёт amendment, — это
останавливает запись целиком, и надо сказать сейчас.

**Бот.** Читатель цели у бота отсутствует сегодня — он получает тот же
документ состояния (GET уже существует, секция расширяется). Запись
наблюдений ботом — через тот же `POST`, `origin` всегда `user_stated`.
Это заготовка контракта к шестому пункту цены, не его начало:
`nutrition/` не трогаем.

## 10. Revised remaining SP estimate

Честный пересчёт, не подгонка под 36:

| # | пункт | было | стало | дельта и почему |
|---|---|---|---|---|
| 1 | Personal Plan — контейнер | 5 | **8** | +3: появился `PlanOutcomeLink` — третья сущность, две оси состояния, история связей, кейс продолжения |
| 2 | Desired Outcome | 5 | **5** | многоместность заложена в схему с первого дня и стоит столько же — это и была ловушка |
| 3 | Progress Observations | 8 | **10** | +2: deletion/correction проектируется сразу (урок ночи: дописанное потом дорожает), шкала с canonical labels и contract-flow запретом LLM-маппинга |
| 4 | consent/policy | 5 | **5** | внешний блокер не изменился; enforcement point — инженерная часть пп. 1/3, не юридическая |
| 5 | multi-outcome в рекомендациях | 8 | **8** | рассуждение, оценка стоит |
| 6 | Food Diary и everyday signals | 5 | **5** | без изменений |
| | **Итого** | **36** | **41** | рост назван, не спрятан: +3 `PlanOutcomeLink`, +2 deletion/correction |

---

## Что на review главного окна (пять точек)

1. `horizon_status` — вычислимое поле, не колонка (§6).
2. Источник consent-сигнала для Ayla-стороны — топология (§7).
3. Намерение пишется под существующим основанием, BLOCKED только наблюдения
   тела — или OD-GOAL-3 читается строже (§9).
4. Исправление наблюдения = supersede с историей (§8).
5. Имя приложения `wellness` (§1).

## Приложение — источники

| Утверждение | Файл | Место |
|---|---|---|
| GOALS-R1..R4, evidence model, consent correction | `docs/OD_GOALS_RULINGS.md` | целиком |
| Матрица первого среза, четыре состояния прогресса | `docs/PROPOSAL_PROGRESS_OBSERVATIONS.md` | §1, §3 |
| В-2 (где живёт наблюдение), В-3, В-5, В-6 | `docs/OD_CARE_CONTRACT_RULINGS.md` | §2 |
| Запрет универсального движка | SPEC (WD:1607-1612) | §7.2 |
| Образец каскада удаления; «no success for work not done» | `ai-bot-platform/apps/identity/services/privacy.py` | целиком |
| Воронка удаления на Ayla-стороне | `djangoproject origin/dev: users/personal_data_api.py` | `InternalPersonalDataDeleteView` |
| Точечная проверка не переживает экран | `ai-bot-platform/apps/miniapp/src/lib/master-api.ts` | :498-522 |
| Линт границ импортов | `ai-bot-platform/tools/lint/` | import_boundaries.py, red_zone_guard.py |
