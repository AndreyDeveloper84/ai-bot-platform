# Planning Constraints Inventory — что реально существует

**Статус:** DRAFT (инвентаризация, часть 1 из 2)
**Дата замера:** 2026-09-07
**Трек:** D — Planning Constraints & Capability Composition
**Режим:** read-only discovery. Код не менялся. Тесты не запускались, контейнеры не поднимались.
**Парный документ:** `docs/specs/PLANNING_CONSTRAINTS_CONTRACT_v1.0.md` (часть 2)

---

## 0. Как читать этот документ

Это **карта пустот**, а не проектная спецификация. Он отвечает на один вопрос:
из каких **контролируемых данных** сегодня физически можно построить план — и каких
данных **не существует в природе**.

### 0.1 Главное правило

```
UNKNOWN ≠ 0 ≠ false ≠ default ≠ пустая строка ≠ пустой список
```

Отсутствие данных фиксируется как `UNKNOWN`. Ни одно поле не переводится
в `0`, `false`, «по умолчанию 60 минут» или вывод модели.

**Итог инвентаризации: `UNKNOWN` — самый частый ответ. Это результат, а не провал.**

### 0.2 Легенда статусов

| Статус | Значение |
|---|---|
| `EXISTS_POPULATED` | поле/таблица есть **и** заполнена проверяемыми данными |
| `EXISTS_EMPTY` | поле/таблица есть, данные пусты — то есть контента нет |
| `EXISTS_UNVERIFIED` | поле есть, заполненность не замерена в этой сессии |
| `ABSENT` | ни поля, ни таблицы, ни файла — искали, не нашли |
| `PROSE_ONLY` | существует только как текст канона/документа, не как данные |
| `STALE` | замер существует, но старше суток и подпадает под §47.7 правило 2 |

### 0.3 Срок годности замеров (§47.7 правило 2)

Часть чисел ниже взята из документов, а не снята заново в этой сессии
(запрет на подъём контейнеров). Каждое такое число помечено `STALE`
с датой исходного замера и владельцем замера. **Их нельзя выдавать
за сегодняшний факт без повторной проверки.**

---

## 1. Ландшафт источников — кто вообще мог бы владеть планировочным знанием

Прежде чем перечислять типы ограничений, надо зафиксировать, **какие хранилища
существуют физически**. Планировочное утверждение может прийти только оттуда.

| # | Хранилище | Репозиторий / путь | Что там лежит | Владелец | Версионирование |
|---|---|---|---|---|---|
| S1 | Канонический каталог услуг Ayla | `djangoproject`, `services_servicetemplate` | 1223 шаблона услуг, 85 категорий (2 уровня) | бэкенд Ayla | **UNKNOWN** — схемы версий не найдено |
| S2 | Салонные услуги Ayla | `djangoproject`, `services_salonservice` | конкретные продаваемые позиции тенанта | бэкенд Ayla | `updated_at` строки, схемы версий нет |
| S3 | Связка мастер×услуга | `djangoproject`, `SpecialistService` | продаваемость + `resolved_requires_health_check` | бэкенд Ayla | нет |
| S4 | Зеркало каталога в боте | `ai-bot-platform`, `apps/catalog/models.py:90` `CatalogService` | read-replica S2, скалярные поля | **никто** — реплика, не источник (ADR-0009 hard rule #1, `apps/catalog/models.py:105-107`) | `cache_version` (`:148`), `external_updated_at` (`:72`) |
| S5 | Связка мастер×услуга в боте | `ai-bot-platform`, `apps/catalog/models.py:357` `MasterService` | **два писателя**: оператор (MM4-матрица) и sync | смешанный, см. `models.py:365-378` | нет |
| S6 | Слотовая политика тенанта | `ai-bot-platform`, `apps/scheduling/models.py:613` `SlotConfig` | шаг сетки, буфер, lead time, горизонт | салон (админка) | `updated_at` (`:649`), схемы версий нет |
| S7 | Канон разговора | `docs/ayla-conversation-state-v1.1-reconciled.md` | 14 решений, семантика | владелец продукта | версия в имени файла (`v1.1`), отменяет `v1.0` |
| S8 | Решения владельца | `docs/OPEN_DECISIONS.md` | §1–§48, датированные решения | владелец | нумерация параграфов + даты |
| S9 | Канонические знания | `ayla-knowledge` | см. §2, разбор ниже | `ayla-knowledge` (по канону §15.7) | см. §2 |
| S10 | База знаний бота | `ai-bot-platform`, `apps/kb/projectors.py` | проекция S4 в текст для retrieval | производная от S4 | производное |

**Ключевой факт про S4.** Зеркало объявлено read-replica и **не является
источником истины** ни для одного планировочного утверждения. Всё, чего нет
в S1–S3, не появится в S4.

**Ключевой факт про S6.** Это единственное хранилище, где сегодня лежат
**заполненные, контролируемые, владельцем настраиваемые временные параметры**.
Но они — параметры **одной записи**, а не плана: шаг сетки, буфер до/после,
минимальный отрыв от «сейчас», горизонт видимости.
**Ни одного межпроцедурного параметра там нет.**

---

## 2. Как отсутствие данных сегодня схлопывается в значение

Это раздел про **дефект схемы**, а не про план. Он идёт до перечня типов, потому
что определяет, можно ли вообще отличить `UNKNOWN` от `нет ограничения` в том,
что уже существует.

### 2.1 Поля, которые схлопывают UNKNOWN (дефект)

`ai-bot-platform/apps/catalog/models.py`:

| Поле | Строка | Объявление | Что происходит при отсутствии данных |
|---|---|---|---|
| `short_description` | `:111` | `TextField(blank=True, default="")` | «нет описания» неотличимо от «описание не пришло» |
| `description` | `:112` | `TextField(blank=True, default="")` | то же |
| `contraindications` | `:121` | `TextField(blank=True, default="")` | **«противопоказаний нет» неотличимо от «мы не знаем»** |
| `requires_health_check` | `:120` | `BooleanField(default=False)` | отсутствие → `False` → **fail-open** |
| `goals` | `:119` | `JSONField(default=list, blank=True)` | «услуга ни для какой цели» неотличимо от «не размечена» |

Последствие для retrieval: `apps/kb/projectors.py:117-119` включает
`Противопоказания: …` в текст для модели **только если строка непуста**.
При пустой строке блок просто отсутствует — модель видит описание услуги,
в котором про противопоказания не сказано **ничего**, и не имеет признака,
отличающего «противопоказаний нет» от «поле не заполнено».

### 2.2 Поля, которые UNKNOWN сохраняют (правильный образец)

| Поле | Строка | Объявление | Поведение |
|---|---|---|---|
| `CatalogService.duration_min` | `:114` | `IntegerField(null=True, blank=True)` | `NULL` = длительность неизвестна |
| `CatalogService.price_from` | `:113` | `Decimal(null=True, blank=True)` | `NULL` = цена неизвестна |
| `MasterService.resolved_requires_health_check` | `:457` | `BooleanField(null=True, blank=True, default=None)` | **tri-state**, `NULL` = UNKNOWN → гейт закрыт |

**`MasterService.resolved_requires_health_check` — единственный найденный в кодовой базе
образец правильно смоделированного UNKNOWN.** Комментарий в модели
(`apps/catalog/models.py:441-456`) прямо формулирует правило, которое трек D
обязан обобщить:

> «A non-null default would have made every pre-existing row read as "no screening
> needed" the moment the migration ran — a fail-OPEN backfill of a medical gate.
> Hence null=True with no default.»

Читатель — `apps/skills/booking/skill.py:1250-1298`
(`_resolved_health_check_for_edge`), и он держит гейт **закрытым** на `None`:
«absence of evidence is not evidence of safety».

**Этот образец берётся как эталон для контракта части 2.**

### 2.3 Дефолты, подставляемые вместо отсутствующей политики

`ai-bot-platform/apps/scheduling/models.py:113-116`:

```
DEFAULT_SLOT_GRANULARITY_MIN = 15
DEFAULT_BUFFER_MIN            = 5
DEFAULT_LEAD_TIME_MIN         = 60
DEFAULT_MAX_ADVANCE_DAYS      = 60
```

`SlotConfig` (`:613`) — строка на тенанта; **отсутствие строки трактуется
резолвером как «использовать дефолты»** (`models.py:618-621`). Для операционной
записи это допустимо и явно задокументировано. **Но эти числа не являются
планировочным знанием** и не могут быть источником ни одного утверждения
о сроках между процедурами.

---

## 3. Инвентаризация по типам ограничений

Сводка. Подробности по каждому типу — в §4.

| # | Тип ограничения | Статус данных | Source of truth | Владелец | Версионирование |
|---|---|---|---|---|---|
| T1 | Семантика способностей | `PROSE_ONLY` (реестр CAP-008 в каноне) | `ayla-knowledge` | канон | **UNKNOWN** |
| T2 | Услуга ↔ способность | `ABSENT` — включая сам словарь статусов | нет | не назначен | **UNKNOWN** |
| T3 | Время и длительность | **`EXISTS_POPULATED`** (единственный тип с данными) | `SalonService.duration_minutes` + `SlotConfig` | бэкенд Ayla / салон | `updated_at`, схемы версий нет |
| T4 | Окна событий (подготовка к дате) | `ABSENT` | нет | не назначен | — |
| T5 | Минимальные интервалы | `ABSENT` | нет | не назначен | — |
| T6 | Максимальные интервалы | `ABSENT` | нет | не назначен | — |
| T7 | Повторяемость / курс | `ABSENT` + **явный запрет в каноне** | нет | не назначен | — |
| T8 | Последовательность шагов | `ABSENT` | нет | не назначен | — |
| T9 | Предусловия | `ABSENT` | нет | не назначен | — |
| T10 | Совместимость | `ABSENT` | нет | не назначен | — |
| T11 | Несовместимость | `ABSENT` | нет | не назначен | — |
| T12 | Восстановление после процедуры | правила `ABSENT`; поле `aftercare_text` — `EXISTS_EMPTY` | нет | бэкенд Ayla (только поле) | — |
| T13 | Ограничения безопасности | `EXISTS_PARTIAL` (один булев флаг) / текст `EXISTS_EMPTY` | `resolved_requires_health_check` | бэкенд Ayla | нет |

**Счёт: из 13 типов один заполнен, один заполнен частично, одиннадцать — `UNKNOWN`.**

---

## 4. Разбор по типам

### T1. Семантика способностей (Capability) — `PROSE_ONLY`

**Что требует канон.** §14: `Need/Outcome → Capability → CanonicalService → TenantOffer`,
четыре уровня, схлопывать нельзя.

**Что есть.**

* В каноне `ayla-knowledge` есть **реестр способностей** с полем `business_purpose`
  (запись CAP-008), и словарь каталога `Service, Service Category, Service Attribute,
  Service Taxonomy, Service Restriction, Service Version, Canonical Service Mapping`
  (`docs/ANALYSIS_PACKAGES.md:219` со ссылкой на разбор канона).
* Доменная модель канона описывает `Catalog Service` (§7.6) с полями
  `service_id, canonical_name, category_id, description, constraints, safety_profile,
  status, version` (`docs/ANALYSIS_PACKAGES.md:203`).
  **Поля `constraints` и `safety_profile` — это ровно те носители, о которых трек D.
  В коде их нет ни в одной из проверенных схем.**

**Чего нет.**

* В `ai-bot-platform` доменного понятия `Capability` **нет вообще**. Слово занято
  другим смыслом: 97 вхождений `capability` в `apps/` — это **права доступа**
  (`apps/identity/services/role_resolver.py:23,64,95`, `apps/admin_api/auth.py:88`).
  **Именная коллизия:** реализация канонической `Capability` в этом репозитории
  столкнётся с уже занятым словом в слое авторизации.
* Ближайший существующий суррогат — `GoalOption` (7 штук) → `GoalOptionCategory`
  (19–20 связей) → `ServiceCategory` (`djangoproject`, `/app/services/models.py:775`).
  Это связь **цель → категория**, а не **способность → услуга**, и канон её
  не признаёт: «`GoalOption` / `GoalOptionCategory` — прагматический слой
  DRF-1190/OD-1, у которого **в каноне нет соответствия вообще**. Реестр кодов
  Goal/Outcome как канон ещё не существует» (`docs/ANALYSIS_PACKAGES.md:219`).

**Source of truth:** `ayla-knowledge` по канону §15.7. Фактически — **не назначен**,
потому что данных нет.
**Версионирование:** `UNKNOWN`.

**Вердикт:** `PROSE_ONLY`. Планировщик не может запросить «какие способности
нужны для этого исхода» — отвечать нечему.

---

### T2. Сопоставление услуга ↔ способность — `ABSENT`

**Что требует канон.** §14.1–14.2: канонические знания владеют
`CanonicalService ↔ Capability`; статусы `VERIFIED / REVIEW_REQUIRED / UNMAPPED`;
только `VERIFIED` — обычное основание рекомендации; LLM может предложить кандидата,
но не может сделать его `VERIFIED`.

**Что есть.**

* Ни одной строки со статусом маппинга ни в одной схеме.
  В `ai-bot-platform` строк `VERIFIED / REVIEW_REQUIRED / UNMAPPED` нет.
* Есть ссылка `SalonService.template → ServiceTemplate` (`djangoproject`,
  `/app/services/models.py:314`) — это `TenantOffer ↔ CanonicalService`,
  **третий из четырёх уровней**, а не сопоставление со способностью.
  Замер `STALE` 23.08.2026: **привязано 0 из 58** (`docs/catalog/CATALOG_NORMALIZATION.md`).
  На 07.09 пилот вырос до 265 услуг; доля привязанных **не перезамерена**.
* В зеркале бота есть `CatalogService.ayla_service_id`
  (`apps/catalog/models.py:129`) — это идентичность строки, не маппинг семантики.

**Чего нет.** Статуса маппинга, таблицы маппинга, процедуры верификации,
владельца верификации.

**Замер, зафиксированный владельцем (`OPEN_DECISIONS.md:3018-3020`, 07.09):**

```
услуг на пилоте                                    265
с заполненными contraindications                     0
с заполненными short_description                     0
с VERIFIED-маппингом CanonicalService ↔ Capability   0
```

**Прямое следствие по букве канона §14.3:** все 265 услуг `catalog_visible`
и `bookable`, но **ни одна не `recommendation_eligible`**.
Это открытый вопрос владельцу `OPEN_QUESTIONS_OWNER.md` §6.1 — **не решён**.

**Уточнение из §41 (07.09):** блок «Ayla подобрала тебе» рекомендует **мастеров**,
а не услуги, и единственная причина в `reasoning_text` — `«Рейтинг 4.9»`
при `reviews_count: 0`. То есть сегодня работающий рекомендатель вообще
не обращается к маппингу услуг. Спор про 265 услуг его не касается —
но и опорой для планирования он быть не может.

**Вердикт:** `ABSENT`. Ни один PlanStep не может быть построен на уровне
Capability, как требует канон §15.3.

---

### T1/T2 — дополнение по замеру `ayla-knowledge`

Разведка репозитория `ayla-knowledge` уточняет §T1 и §T2:

**Реестр способностей существует и заполнен** — `00 Foundation/Ayla Domain Capability Registry.md`,
§6, строки 331–1728: **27 записей `CAP-001 … CAP-027`**, каждая — YAML-блок
с фиксированной схемой (`capability_id`, `canonical_name`, `status`, `classification`,
`confidence`, `evidence_status`, `business_purpose`, `owned_concepts`, `key_invariants`,
`upstream_capabilities`, `downstream_capabilities`, `candidate_contexts`, `mvp_scope`,
`evidence[]`, `open_questions`). Схема записи — строки 278–328, контракт неизменности
ID — строка 319.

**Но это не тот Capability, который нужен планировщику.** `CAP-*` — это *доменные
способности системы* («Safety Policy Enforcement», «Catalog»), то есть карта
ответственности сервисов. Канон §14 требует `Capability` в смысле «что должно
обеспечивать подходящее решение» — свойство **услуги**, а не подсистемы.
Совпадение имени не делает эти реестры одним и тем же. **Реестра способностей
услуг не существует.**

**Что есть из маппингов:**

| Маппинг | Статус | Где | Объём |
|---|---|---|---|
| Intent → Capability | `EXISTS_POPULATED`, помечен `draft` / «proposal-маппинг» | `03 AI System/Contracts/intent-registry.yaml`, поле `downstream_capability`, строки 36,50,67,80,93,106,119,132,148,161,174,195 | 12 интентов, 18 слотов |
| Need/Outcome → Capability | `ABSENT` | отложено в «Capability Registry Wave 2» — `01 Product/User Journeys/Ayla MVP User Journey Specification.md:867,1106,1132`; решение владельца OD-16 — `:103`, `00 Foundation/Canon Governance/OWNER_DECISION_REGISTER.md:573` | 0 |
| Capability → CanonicalService | `ABSENT` | понятие названо (`Ayla Domain Capability Registry.md:731`, CAP-008 owned concept), реализация в future work — `02 Strategy/Ayla Multi-Provider Product Validation Execution Scope.md:482`; граница до сих пор открытый вопрос — `05 Architecture/Ayla Domain Context Map.md:1992` | 0 |
| Статусы `VERIFIED / REVIEW_REQUIRED / UNMAPPED` | `ABSENT` | **словаря не существует.** `UNMAPPED` — 0 вхождений во всём репозитории. `REVIEW_REQUIRED` — 3 вхождения, все `TARGETED_REVIEW_REQUIRED` про статус ревью *документа*. `VERIFIED` — только в тексте аудита | 0 |

**Следствие, которое надо назвать прямо.** Канон v1.1 §14.2 вводит статусы
`VERIFIED / REVIEW_REQUIRED / UNMAPPED` как основание рекомендации, а в каноническом
репозитории `ayla-knowledge` этого словаря **нет ни одного вхождения**. То есть
статус, которым канон разрешает и запрещает рекомендацию, сегодня **не определён
нигде, кроме самого текста v1.1**. Это не «маппинг не построен» — это «шкала,
по которой его строили бы, не заведена».

**Ключевой факт про wiring.** Ни один из машиночитаемых файлов знаний
не читается кодом. Проверено: `scripts/validate_knowledge.py` (432 строки) валидирует
**только frontmatter-метаданные** документов, `render_domain_registry.py` генерирует
индекс, CI (`.github/workflows/validate-knowledge.yml`) запускает ровно эти два
и `unittest discover`. Grep по `scripts/ tests/ .github/` на
`intent-registry|slot-registry|intent-output.schema` — **ноль вхождений**.
`.knowledge/sources-manifest.yaml` — `enabled_default: false`, зеркалирование выключено.

> **Зрелая механика этого репозитория управляет метаданными документов,
> а не доменными правилами.**

**Расхождение версий схемы.** `ayla-knowledge/.knowledge/schema.yaml` —
`schema_version: "1.13"`; `ayla-knowledge-main/.knowledge/schema.yaml` — `"1.14"`.
Две копии канонического репозитория разошлись. Планировочные файлы при этом
байт-идентичны (проверено на `Ayla Domain Capability Registry.md`).

---

### T3. Время и длительность — `EXISTS_POPULATED` (единственный заполненный тип)

Это единственное место, где планировщику есть на что опереться. Но опора уже́,
чем кажется.

**Что заполнено.**

| Слой | Поле | Путь | Заполненность |
|---|---|---|---|
| Продаваемая услуга (marketplace) | `Service.duration_minutes` | `djangoproject/services/models.py:245` | **NOT NULL**, валидатор 15..480 — на уровне БД пустой быть не может |
| Продаваемая услуга (салон) | `SalonService.duration_minutes` | `djangoproject-catalog/services/models.py:349` | nullable |
| Связка мастер×услуга | `SpecialistService.duration_minutes` | `djangoproject-catalog/services/models.py:423` | nullable |
| Зеркало бота | `CatalogService.duration_min` | `ai-bot-platform/apps/catalog/models.py:114` | nullable |
| Буфер после | `Service.buffer_after_minutes` | `djangoproject/services/models.py:253` | `default=0` |
| Слотовая политика тенанта | `SlotConfig` | `ai-bot-platform/apps/scheduling/models.py:613` | дефолты 15/5/60/60 |

**Что пусто — и это главный факт типа T3.**

Канонический каталог **не знает длительностей**. Замер по сид-файлу
`djangoproject/services/seeds/canonical_catalog_2026-07.json` (1223 строки):

```
duration_default   заполнено 0 из 1223
duration_min       заполнено 0 из 1223
duration_max       заполнено 0 из 1223
price              заполнено 0 из 1223
```

Команда загрузки пишет `None` явно:
`djangoproject/services/management/commands/seed_canonical_catalog.py:126-128`.

Единственный источник настоящих длительностей в каноне — **~50 захардкоженных
кортежей** в `djangoproject/services/management/commands/seed_service_templates.py:29…`,
применяемых на `:151-165`. Покрывают только маникюр/педикюр/брови/массаж/
косметологию/волосы. Это подтверждает `STALE`-замер 23.08 из
`docs/catalog/CATALOG_NORMALIZATION.md`: «Длительностей в каноне нет ни у одного из 1223».

**Правильная механика резолва, которую надо сохранить.**
`djangoproject-catalog/services/models.py:466` `resolved_duration()` — цепочка
specialist → salon → template, и при всех `None` возвращает **`None`, а не 60**.
`SpecialistService.clean()` (`:481`) отклоняет активную услугу с неразрешимой
длительностью. Это второй найденный образец правильного `UNKNOWN`.

**Дефект, который прямо противоречит этому образцу.**
`djangoproject/appointments/models.py:269-275` — свойство
`Appointment.duration_minutes` возвращает **`0`** при отсутствующих
`start_datetime`/`end_datetime`. И `appointments/models.py:84` —
`snapshot_duration_minutes = PositiveSmallIntegerField(default=0)`: строка,
записанная мимо `CreateBookingService`, получает длительность `0` без ошибки.
**Отсутствие окна времени читается как визит нулевой длины.**
Тот же узор — `snapshot_price` (`:85`), `snapshot_service_name` (`:83`, `default=""`),
`snapshot_timezone` (`:97`, `default="Europe/Moscow"` — молча предполагает Москву).

**Замеренное противоречие в данных (не разрешено).**
`docs/catalog/CATALOG_NORMALIZATION.md` В-3 утверждает «у всех лазерных зон стоит
`duration_min = 60`, включая верхнюю губу за 500 ₽», а тот же документ выше
утверждает «длительностей в каноне нет ни у одного из 1223». Два утверждения
о разных таблицах (шаблон канона против салонной строки) читаются как
противоречие. **Оба замера `STALE` (23.08). Не разрешать домыслом — перезамерить.**

**Source of truth:** `SpecialistService → SalonService → ServiceTemplate` (цепочка
резолва, бэкенд Ayla). **Владелец:** бэкенд Ayla. **Версионирование:** `updated_at`
строки; схемы версий каталога нет.

**Что T3 НЕ даёт планировщику.** Длительность — это про **одну процедуру**.
Ни одного поля про время **между** процедурами не существует (см. T5–T7).

---

### T4. Окна событий (подготовка к дате) — `ABSENT`

**Что искали:** `event`, `occasion`, `lead_time`, `before_event`, `event_date`,
`дата события`, `повод`, `deadline`.

**Что нашли:**

* `djangoproject`: **ничего**. `target_date` существует, но это параметр запроса
  доступности (`appointments/application/services/availability_query_service.py:43,79,127`),
  не свойство цели человека.
* `ai-bot-platform`: цель `Собраться к событию` существует как `GoalOption`
  и ведёт к 3 категориям (`docs/SYSTEM_GOALS.md:120-135`). **Даты события в ней нет.**
  Это ярлык категорий, а не окно.
* `ayla-knowledge`: единственная числовая таблица временных окон во всём
  репозитории — `02 Strategy/Killer PRD.md:280-286`, §6.3, три строки:
  `immediate_slot`/`repeat_booking` 24 ч, `recovery_wellness` 72 ч,
  `event_preparation` `min(recommendation_shown_at + 14 days, event_at)`.

**Это окна атрибуции маркетинга, а не сроки подготовки к процедуре.**
Соблазн переиспользовать `event_preparation = 14 дней` как планировочное правило —
именно та подмена, которую трек D обязан запретить: число описывает, сколько
дней засчитывать конверсию, а не за сколько дней до свадьбы делать процедуру.

Более того, реестра этих окон **не существует**: `Killer PRD.md:288` требует
«Versioned Attribution Window Registry», файла нет;
`05 Architecture/Ayla MVP Recommendation Contract.md:820` подтверждает, что окна
делегированы в несуществующий «Measurement Framework», а контракт хранит только
`attribution_policy_version`.

Семантический слой исключает событие явно:
`05 Architecture/Ayla Goal Outcome Semantic Model Working Design.md:1113` относит
`event_type`, `event_date`, `time_to_event` к «context/runtime semantics» —
**не моделируются**; `:610` исключает дедлайн, дату события, время до эффекта
и каденцию исполнения из `DesiredChangeSpecification`.

**Source of truth:** нет. **Владелец:** не назначен. **Вердикт:** `UNKNOWN`.

---

### T5 / T6. Минимальные и максимальные интервалы между процедурами — `ABSENT`

**Что искали:** `min_interval`, `max_interval`, `repeat_after`, `periodicity`,
`frequency`, `cadence`, `cooldown`, `интервал`, `интервал между`, «не ранее»,
«не чаще», «раз в N», «каждые N», «через N дней/недель».

**Результат — ноль полей во всех трёх контурах:**

* `djangoproject` / `djangoproject-catalog` — ни одного поля ни на услуге,
  ни на шаблоне, ни на записи.
* `ai-bot-platform` — ни одного поля на `CatalogService` (полный список полей:
  `apps/catalog/models.py:109-129`).
* `ayla-knowledge` — ноль вхождений по всему репозиторию.

**Ложные срабатывания, которые нельзя принимать за источник.**
В `djangoproject` существуют `cooldown_*` поля — но они принадлежат
**движку подсказок по питанию**, не каталогу услуг:
`nutrition/models.py:748-752` — `cooldown_rule_days=30`, `cooldown_trigger_days=14`,
`cooldown_category_days=7`, `skip_extend_days=7`, `double_skip_pause_days=60`
на `CrossDomainRule`. Плюс `users/models.py:532` `last_asked_at` — 24-часовой
антиповтор вопроса.

Это **частотная политика обращения к человеку**, а не интервал между процедурами.
Перенос этих чисел в план был бы выдумыванием.

**Source of truth:** нет. **Владелец:** не назначен. **Вердикт:** `UNKNOWN`.

---

### T7. Повторяемость / курс — `ABSENT` + **явный запрет канона**

Здесь ситуация хуже, чем «данных нет»: канон **запрещает** восполнять пробел выводом.

**Запрет.** ADR-0012 N-04 (`ayla-knowledge`):

> «Запрещено: предложить **курс процедур** … только потому, что из поведения выведено…»

Это единственное вхождение слова «курс» в каноническом репозитории —
и оно является запретом, а не определением
(зафиксировано `docs/ANALYSIS_PACKAGES.md:213`).

**Каденция вынесена из семантики явно.**
`05 Architecture/Ayla Goal Outcome Semantic Model Working Design.md` (GO2-W3):
каденция конкретной услуги («раз в месяц») относится downstream
к execution/planning; `:610` — «execution cadence» в модель сознательно не входит.
То есть канон переложил каденцию на планировщик, **не дав ему данных**.
`01 Product/Ayla Conversation Product Map.md:167` фиксирует «follow-up cadence»
как непокрытый пробел.

**Рынок знает, а мы нет.** `docs/catalog/MARKET_PENZA.md:22,96`:

> «Лазерная эпиляция продаётся курсом, не разовой процедурой… пакетами по 6–8
> сеансов. **В каноне понятия курса нет вовсе** — только разовые шаблоны.
> Наш пилот тоже продаёт разово.»

Отдельный замеренный случай: у салона «Афродита» 5 из 8 услуг имеют
`base_price = 0.00`, потому что салон **не продаёт разовые сеансы, только курс**
(`docs/ANALYSIS_PACKAGES.md:147-168`). Одна строка каталога несёт два разных
масштаба: длительность описывает **один сеанс**, цена — **курс из шести**.
Это самостоятельный дефект модели данных, а не источник знания о повторяемости.

**Замечание про пакеты, важное для планирования.** Состав пакета не выводится
ни из имени, ни из цены, ни из длительности — все три способа проверены
и признаны негодными (`docs/ANALYSIS_PACKAGES.md:126-198`). Единственная
иерархическая колонка во всей схеме — `services_servicecategory.parent_id`
(`docs/ANALYSIS_PACKAGES.md:11-22`, подтверждено `djangoproject/services/models.py:17`).
Составных объектов, M2M и self-FK у услуг нет.

**Source of truth:** нет. **Владелец:** не назначен.
**Вердикт:** `UNKNOWN`, и вывод повторяемости моделью **прямо запрещён** ADR-0012 N-04.

---

### T8. Последовательность шагов — `ABSENT` (для процедур)

Разделить два разных «sequencing», которые легко спутать:

| Что | Статус | Где |
|---|---|---|
| Последовательность **слотов внутри интента** (какие поля надо собрать) | `EXISTS_POPULATED` | `ayla-knowledge/03 AI System/Contracts/intent-registry.yaml`, `slot_requirements: all_of / any_of / minimum_present` + `optional_slots` для всех 12 интентов (напр. строки 37–40, 51–57) |
| Последовательность **транзакции записи** (слот выбран → холд → подтверждение) | `PROSE_ONLY` | `01 Product/User Journeys/Ayla MVP User Journey Specification.md:743`; `05 Architecture/Ayla Core Domain Model Specification.md:826` |
| Последовательность **процедур в плане** | **`ABSENT`** | названо как стремление один раз — `02 Strategy/Killer PRD.md:195`, `01 Product/Ayla Product Vision.md:443`. Модели шага нет, графа зависимостей нет |

В `djangoproject` искали `sequence`, `step`, `step_number`, `prerequisite`,
`precondition`, `requires`, `depends_on` — нашли только `sort_order`
(`services/models.py:22,125,252`), это **порядок показа**, не граф зависимостей.
`depends_on` — только зависимости миграций Django.

**Source of truth:** нет. **Вердикт:** `UNKNOWN`.

---

### T9. Предусловия — `ABSENT` (для процедур)

Найденные «preconditions» относятся к выкатке проекта, не к домену:
`02 Strategy/Ayla Multi-Provider Product Validation Execution Scope.md:114`,
`02 Strategy/Ayla MVP v0.3 Downstream Migration Plan.md:53` — это гейты релиза.

Единственное предусловие домена, которое существует и исполняется, —
**медицинский скрининг перед записью** (см. T13). Оно булево и не описывает,
*что* должно быть выполнено до процедуры.

**Source of truth:** нет. **Вердикт:** `UNKNOWN`.

---

### T10 / T11. Совместимость и несовместимость — `ABSENT`, и **сознательно отклонено владельцем**

Это самый важный отрицательный результат трека: пустота здесь не «ещё не сделали»,
а **принятое решение не делать**.

`ayla-knowledge/05 Architecture/Ayla Goal Outcome Semantic Model Working Design.md`:

* **`:500-504` (OD-CI-4)** — «Глобальный compatibility registry **не создаётся**.»
* **`:506-514` (OD-CI-5)** — «Pairwise matrix, graph, mutual exclusions и conditional
  subset constraints **не проектируются**… любое непустое подмножество candidates
  должно быть допустимо.»
* **`:590-592`** — «Compatibility algorithm/matrix/registry этим документом
  не проектируются.»

Все остальные ~40 вхождений `compatib*` в репозитории — про **обратную
совместимость схем и контрактов** (напр. `05 Architecture/Ayla Core Domain Model
Specification.md:1361`, `02 Strategy/Ayla Decision Log.md:1165`).
Ни одного про процедуры.

В `djangoproject` искали `compatible`, `incompatible`, `conflicts_with`,
`mutually_exclusive`, `combo`, `bundle`, `package_`, `совместим` — все попадания
про совместимость API/схем или S3-compatible storage. **Таблицы отношений
услуга↔услуга не существует.**

**Следствие, которое надо назвать.** Пока OD-CI-4/OD-CI-5 действуют,
план **не имеет права** утверждать ни «эти процедуры сочетаются»,
ни «эти процедуры нельзя вместе». Оба утверждения одинаково безосновательны.

Обратите внимание на асимметрию: OD-CI-5 постановил, что **любое непустое
подмножество кандидатов допустимо** — это разрешение на комбинирование
**в наборе рекомендаций**, но **не** знание о медицинской сочетаемости процедур.
Путать эти два смысла нельзя: первое — про то, что показывать вместе на экране,
второе — про то, что безопасно делать с телом.

**Source of truth:** нет, и создание реестра отклонено. **Вердикт:** `UNKNOWN`.

---

### T12. Восстановление после процедуры — `EXISTS_EMPTY` (одно поле) / `ABSENT` (правила)

**Единственное найденное поле:** `Service.aftercare_text`,
`djangoproject/services/models.py:265` — `TextField(blank=True, default='')`,
миграция `services/migrations/0010_service_aftercare_text.py:27-40`, дефолт пустая строка.

Свойства этого поля:

* **свободный текст**, а не срок и не структура — из него нельзя вычислить окно;
* читается только уведомлениями — `notifications/tasks.py:177,221`;
  при пустой строке пуш подавляется;
* **не входит ни в один сериализатор** — правится только через админку
  (`ServiceAdmin` без `fields`/`fieldsets`);
* дефолт пустой строки схлопывает `UNKNOWN` (см. §2.1);
* **в зеркало бота не мигрирует** — в `CatalogService` такого поля нет
  (`ai-bot-platform/apps/catalog/models.py:109-129`).

**Окон восстановления не существует.** Искали `recovery`, `downtime`,
`реабилитация`, `восстановление`. В `djangoproject` попадания — стрик-логика
питания (`nutrition/services/returning_success_service.py`), не про процедуры.
В `ayla-knowledge` «восстановление» встречается ~20 раз и **каждый раз** означает
либо (а) предметную область велнеса в перечислении «питание, движение, спорт,
восстановление, сон» (`00 Foundation/Ayla Constitution.md:64,96`;
`01 Product/Ayla Product Vision.md:175`), либо (б) техническое восстановление
соединения/данных (`01 Product/User Journeys/…:1091`; `02 Strategy/Killer PRD.md:369`).

Ближайшее к сроку — `recovery_wellness` 72 ч в таблице **атрибуции**
(`Killer PRD.md:283`) и повествовательный пример «через 2 недели Ayla напоминает»
(`01 Product/Ayla User Journey Specification.md:1646`). **Ни то ни другое
не является правилом восстановления.**

**Source of truth:** нет. **Вердикт:** окна восстановления — `UNKNOWN`;
поле для текста ухода существует и пусто.
---

### T13. Ограничения безопасности — `EXISTS_PARTIAL`, но не то, что требует канон

Единственный тип, где есть работающий исполняемый механизм. И единственный,
где реализация и канон **говорят на разных языках**.

#### 13.1 Что существует и работает

**Детерминированный движок безопасности** —
`ai-bot-platform/apps/orchestrator/safety/` (`gate.py`, `pre_check.py`,
`post_check.py`, `outbound.py`, `voice_check.py`).

* Чистая регулярка, **без LLM**: `pre_check.py:5` — «Regex keyword guard —
  no LLM, <10ms p95»; `gate.py:122-123`; `post_check.py:40`;
  `apps/skills/health_screening/classifier.py:3`.
* Правила лежат константами модуля: `pre_check.py:78-141` `_DEFAULT_PATTERNS`,
  `outbound.py:47-68` `_MEDICAL / _PROMISES / _CONTACTS`,
  `health_screening/classifier.py:85-115,132-174,234-265`.
* Тенант может только **ужесточить**: `pre_check.py:169-183` — «the override
  list is APPENDED to defaults».
* Живая проводка: `apps/channels/max/handler.py:933,1325,1625,1748`,
  `apps/channels/telegram/handler.py:230,304`.

**Медицинский гейт записи** — `apps/skills/booking/skill.py:1301`
`_service_requires_health_check`, вызывается на `:908`, отдаёт handoff
`booking_health_check_required` (`:919-924`). Читает
`MasterService.resolved_requires_health_check`; **`Unknown → gate closed`**.

**Заполненность контролируемых данных безопасности:**

```
шаблонов канона всего                       1223
requires_health_check = true                 102   (8,3 %)
health_check_reason заполнено                102
contraindications (из колонки note)           13   (1,1 %)
```

Замер по `djangoproject/services/seeds/canonical_catalog_2026-07.json`.
На пилоте (265 услуг) `contraindications` заполнено **0**
(`OPEN_DECISIONS.md:2642-2645`, замер 06.09, `STALE`).

**Итого:** от всей безопасности плана сегодня существует **один булев флаг
на связку мастер×услуга** и **102 отметки на 1223 шаблонах**. Ни срока,
ни условия, ни причины в машиночитаемом виде.

#### 13.2 Расхождение канона и реализации — зафиксировать, не переписывать

| | Канон v1.1 §7.1 | Реализация |
|---|---|---|
| Входящие состояния | `NORMAL / CLARIFY / CAUTION / STOP` | `ALLOW / CLARIFY / BLOCK / HANDOFF` — `apps/orchestrator/safety/pre_check.py:60-64` |
| Исходящие | не описаны отдельно | `ALLOW / REVISE / BLOCK` — `post_check.py:53+`; категории `("medical","promise","contact")` — `outbound.py:70-74` |
| `rule_id` | обязателен (§7.2) | **ABSENT** в обоих репозиториях |
| `policy_version` | обязателен (§7.2) | **ABSENT** в коде; в каноне существует только как **имя поля** без значений — `05 Architecture/Ayla MVP Recommendation Contract.md:217,219,409,417,1011` (`safety_policy_version`) |
| Что логируется при блоке | сигналы + provenance + ограничения способностей | **только категория** — `outbound.py:122`, `gate.py:203-207`, событие `safety.outbound_blocked` с `{surface, categories, text_len}` (`gate.py:212-224`) |

**Четыре состояния канона ≠ четыре вердикта кода.** `CAUTION` («продолжение
разрешено в суженном пространстве политики») в коде **не существует вовсе**:
`BLOCK` и `HANDOFF` — это два разных способа остановиться, а не «продолжить
осторожно». `NORMAL` и `ALLOW` совпадают по смыслу, `CLARIFY` совпадает.

Это **не дефект реализации** — канон §7 сам признаёт: «Still open: exact
domain-specific safety signal matrix and exact production wording»
(`ayla-conversation-state-v1.1-reconciled.md:288`). Разведка `ayla-knowledge`
подтверждает буквально: `signal_matrix` — **0 вхождений**; `CAUTION` и `NORMAL`
как уровни безопасности — **0 вхождений**; CAP-014 (Safety Policy Enforcement) —
`status: identified`, `confidence: medium`, `evidence_status: partial`,
`business_outcome: null`, `mvp_scope: undetermined`
(`00 Foundation/Ayla Domain Capability Registry.md:1013-1062`).
Каталог `06 Safety and Governance/` содержит **два файла** и **ни одного правила
безопасности**: `Consent Scope Registry.md` и `Data Inventory Matrix.md`.
`01 Product/Ayla Product Principles.md:760` прямо: «Не safety policy —
не задаёт матрицы компетенций, рисков и эскалации.»

**Вывод по T13, как и требовал бриф:** ограничения безопасности **для плана**
сегодня `UNKNOWN`. Существующий движок защищает **реплику в диалоге**
(не сказать диагноз, не пообещать результат, не дать контакт) и **вход в запись**
(булев скрининг). Он не отвечает и не может ответить на планировочные вопросы:
«можно ли этому человеку эту процедуру», «через какой срок после предыдущей»,
«что нельзя сочетать».

**Source of truth:** `apps/orchestrator/safety/*` (константы модуля) для реплики;
`SpecialistService.resolved_requires_health_check` (бэкенд Ayla) для гейта записи.
**Владелец:** по канону §7.2 — `ayla-knowledge`; фактически — код `ai-bot-platform`.
**Версионирование:** отсутствует полностью.

---

## 5. Что есть в коде из механики, на которую можно опереться

Не всё пусто. Три существующих механизма — готовые кирпичи для контракта части 2,
и их надо **переиспользовать, а не изобретать заново**.

### 5.1 Tri-state UNKNOWN с fail-closed чтением

`MasterService.resolved_requires_health_check`
(`ai-bot-platform/apps/catalog/models.py:457`, комментарий `:441-456`) +
читатель `apps/skills/booking/skill.py:1250-1298`.
Единственный в кодовой базе правильно смоделированный `UNKNOWN`.

### 5.2 Цепочка резолва, возвращающая `None`, а не подстановку

`djangoproject-catalog/services/models.py:466` `resolved_duration()` —
specialist → salon → template, при всех `None` возвращает `None`.
`SpecialistService.clean()` (`:481`) отклоняет активную услугу
с неразрешимой длительностью.

### 5.3 Существующий словарь provenance у маппинга — маленький, но настоящий

`ai-bot-platform/apps/catalog/services/linking.py:68`:

```
matched_by: str   # "pair" | "pair+duration" | "manual"
```

и `:289` — **`"""Manual mapping first, then pair+duration. Never guess."""`**,
тайбрейк по длительности `:304-307`, несопоставленные строки остаются
несопоставленными (корзины отчёта `matched_auto` / `matched_manual` / unmatched —
`apps/catalog/management/commands/link_ayla_service_ids.py:20`).

**Это ближайший существующий аналог статусов `VERIFIED / REVIEW_REQUIRED /
UNMAPPED`.** Он про идентичность строки, а не про способность, но **словарь
происхождения уже заведён и уже соблюдает правило «никогда не угадывай»**.
Контракт части 2 обязан строиться на нём, а не вводить четвёртый параллельный
словарь.

### 5.4 Правило отрисовки, уже сформулированное верно

`ai-bot-platform/apps/marketplace/dto.py:77-81`:

> «a missing value must render as "not told", never as an invented number»

и `apps/orchestrator/discovery.py:900-903` — цена и длительность добавляются
в строку только при непустом значении.

**Это готовая формулировка §2 контракта.** Её надо распространить с цены
и длительности на все планировочные утверждения.
---

## 6. Где сегодня стена стоит — и где её нет

Инвентаризация обязана ответить не только «каких данных нет», но и «что сейчас
происходит на месте отсутствующих данных». Ответ хуже, чем пустота.

### 6.1 Что модель получает на ход

Живой консьерж **не ходит в Ayla на ход диалога**. Он читает локальное зеркало:
`apps/orchestrator/discovery.py:39,49` → `apps/marketplace/discovery.py:27`
(`CatalogMaster`, `CatalogService`, через `.all_tenants`).

Карточка услуги, которую видит модель — `apps/marketplace/dto.py:72-96`
`ServiceCard`:

```
tenant_id, service_id, name, price_from, duration_min,
salon_name, city, has_bookable_master
```

**Восемь полей. Ни описания, ни противопоказаний, ни целей, ни ухода.**

`CatalogService.contraindications` **не заполняется синхронизацией никогда** —
`apps/catalog/services/upserter.py:128`: «is_popular, contraindications) keep
their model defaults». То есть колонка существует, дефолт `""`, писателя нет.

Текст противопоказаний потребляют ровно два места, **и ни одно не на живом пути
бота**: `apps/kb/projectors.py:116-119` (KB, достижим только через
`search_knowledge_base` из FAQ-скилла) и `apps/miniapp_api/views.py:658`
(экран мини-приложения). Набор инструментов консьержа —
`apps/orchestrator/concierge.py:621-628` — `search_knowledge_base` **не содержит**.

### 6.2 Где стена стоит

Все антигаллюцинационные правила во всех живых промптах перечисляют **один
и тот же закрытый список**:

* `apps/orchestrator/concierge.py:1030-1031` — «Салон, цену или адрес называй
  ТОЛЬКО из ответов инструментов — ничего не выдумывай; нет данных в ответе —
  честно скажи, что нет.»
* `ayla-ai-core/src/ayla_ai_core/prompts.py:132-134` — «НИКОГДА не выдумывай
  мастеров вне списка… НИКОГДА не выдумывай цены, длительность, режим работы.»
* `apps/skills/booking/prompts.py:133,168,198,304` — не выдумывать ID,
  `promo_code`, `record_id`.

**Закрытый список: мастер, цена, адрес, длительность, идентификаторы.**

### 6.3 Где стены нет — главный вывод трека

**Ни одного правила — ни в промпте, ни в константе, ни в коде — про интервал,
частоту, длину курса, последовательность услуг, совместимость, срок
восстановления, подготовку и противопоказания.**

Grep по `concierge.py` на `интервал`, `через сколько`, `курс`, `совместим`,
`противопоказ`, `восстановлен`, `подготов`, `последовательн` — **ноль вхождений**.

При этом промпт **приглашает** такие вопросы:
`concierge.py:1070-1072` — «Вопрос про услуги, цены, длительность… вызывай
`show_services`». Но `show_services` отдаёт восемь полей из §6.1, и любое
продолжение разговора про подготовку, интервал, восстановление или совместимость
остаётся **свободным сочинением**: инструмента нет, правила заземления нет.

Хуже: `concierge.py:1109-1114` прямо просит модель **сформулировать риск своими
словами**:

> «остановись, коротко назови границу без диагноза («я не врач и не оцениваю
> здоровье»), **спокойно обозначь риск простыми словами** и предложи безопасный шаг»

Ничто не ограничивает этот «риск простыми словами» источником.

**Исходящий фильтр не ловит планировочную выдумку.**
`apps/orchestrator/safety/outbound.py:47-54` ловит только утвердительные формы
*диагноз / назначение / дозировка* и по замыслу (`outbound.py:45`) пропускает
«противопоказания обсудите с врачом». Фраза вида
**«между курсами нужно 3–4 недели»** не попадает ни в одну из трёх категорий
(`medical`, `promise`, `contact`) и **уходит человеку**.

### 6.4 Единственный правильный образец промпта

`apps/skills/faq/prompts.py:238-242`:

> «Если вопрос требует фактической информации (часы, цены, адрес,
> **противопоказания**), вызови инструмент `search_knowledge_base`.
> Если ответа не нашлось — скажи "уточню у мастера".»

и `:233-236` — «Отвечай ТОЛЬКО на основе извлечённых фрагментов».

Это ровно та форма, которую трек D должен обобщить. Но сегодня она бесполезна
по двум причинам сразу: FAQ-скилл не в наборе инструментов консьержа (§6.1),
а KB нечего вернуть — `contraindications` не заполняется синхронизацией.

---

## 7. Дефекты, найденные попутно

Документируются отдельно, как требует общий бриф. **Трек D их не чинит.**

### P1-D1. Планировочная выдумка не ловится ни на входе, ни на выходе

**Что.** См. §6.3. Утверждение об интервале, курсе, совместимости, восстановлении
или подготовке не имеет ни источника, ни правила заземления, ни исходящего фильтра.

**Доказательство.** `apps/orchestrator/concierge.py:1030-1031` (закрытый список
заземляемых фактов), `apps/orchestrator/safety/outbound.py:47-74` (три категории,
ни одна не покрывает срок), `apps/marketplace/dto.py:72-96` (8 полей карточки).

**Почему P1, а не P0.** Каталог сегодня не даёт модели поводов рассуждать
о курсах — планировочного контекста в карточке нет. Риск реализуется, когда
человек спрашивает сам («через сколько повторить?»), а это не гипотетический
сценарий: рынок продаёт лазерную эпиляцию курсами 6–8 сеансов
(`docs/catalog/MARKET_PENZA.md:22`).

### P1-D2. Пять точек, где `NULL` длительности молча превращается в 60 минут

`ai-bot-platform`:

| Путь | Строка | Что делает |
|---|---|---|
| `apps/master_api/services/schedule.py` | `:95`, применяется `:301-305` | `DEFAULT_SERVICE_DURATION_MIN = 60` |
| `apps/master_api/services/dashboard.py` | `:67`, возвращается `:259` | то же |
| `apps/miniapp_api/views.py` | `:354`, применяется `:418` | `DEFAULT_OCCUPIED_DURATION_MIN = 60` |
| `apps/bookings/tasks.py` | `:428-432` | комментарий буквально: «NULL means "unknown, assume 60"» |
| `apps/admin_api/services/master_deactivation.py` | `:609-611` | `if duration_min <= 0: duration_min = 60` |

**Почему это важно для трека D.** Бэкенд Ayla специально устроен так, чтобы
неразрешимая длительность оставалась `None`
(`djangoproject-catalog/services/models.py:466-478`), а активная услуга
с неразрешимой длительностью отклонялась (`:481`). **Зеркало эту дисциплину
теряет на пяти путях сразу.** `UNKNOWN` превращается в `60` — ровно то, что
трек D запрещает. Для операционной сетки это осознанный компромисс;
**как планировочный факт число 60 использовать нельзя ни в одном из пяти мест.**

### P1-D3. Живой канал впрыска клинических утверждений без проверки содержимого

**Что.** `ai-bot-platform/apps/promptreg/voice_examples.py` содержит few-shot
примеры, обучающие модель клиническим и планировочным утверждениям:

* `:69-76` — «При беременности и варикозе антицеллюлитный нельзя»;
* `:107-113` — «"отдаёт в руку" обычно сигнал что задет нерв, массаж в острой
  фазе может ухудшить»;
* `:123-131` — «зажим в шейно-воротниковой зоне ухудшает кровоток в голову»;
* `:206-211` — «Реалистично 2-3 кг/месяц»;
* `:180-192` — «это 95% воды, считаю как 240 мл», «Алкоголь не идёт в счёт воды».

**Смягчающий факт, требующий отдельной проверки:** `render_voice_examples` /
`examples_for_intent` в продакшене **не импортируются** — только тестом
`apps/promptreg/tests/test_voice_examples.py`. То есть сегодня примеры
определены, но не впрыскиваются.

**Но канал открыт:** `apps/voice/services.py:86` читает потенантное поле
`voice_examples` (`apps/persona/models.py:64`, JSONField), через которое
оператор может впрыснуть эквивалентный текст. **Проверки содержимого в коде нет.**

**Отдельно:** два из этих примеров **скопированы дословно в отгруженный код** —
`apps/skills/health_screening/skill.py:65-69` `RED_FLAG_REPLY` и `:57-61`
`SOFT_PAIN_REPLY`. Это статические строки за детерминированным классификатором,
а не вывод модели, — но клиническое утверждение в них то же самое,
и `rule_id` у него нет.

### P1-D4. Два расходящихся бэкенд-дерева, и неясно, какое каноническое

`djangoproject/services/models.py` заканчивается на строке 303 и содержит
`ServiceCategory / ServiceTemplate / RegionalPricing / Service`.
`djangoproject-catalog/services/models.py` содержит **дополнительно**
`SalonService` (`:312`), `SpecialistService` (`:399`), `DraftSalonService` (`:500`),
`ExternalSourceMapping` (`:578`), `ExternalBusyInterval` (`:667`).

Пилотные замеры владельца оперируют `services_salonservice`
(`docs/catalog/CATALOG_NORMALIZATION.md`, `OPEN_DECISIONS.md:2642`) —
то есть **живой контур соответствует `djangoproject-catalog`, а не
`djangoproject`**. Какое дерево является source of truth для планировочных
полей — **не установлено этой инвентаризацией**. До ответа любое утверждение
«поле есть в бэкенде» обязано называть, в каком из двух.

### P1-D5. Расхождение версии схемы знаний

`ayla-knowledge/.knowledge/schema.yaml` — `schema_version: "1.13"`;
`ayla-knowledge-main/.knowledge/schema.yaml` — `"1.14"` (добавлены типы
документов `standard` / `map` / `research`). Две копии канонического
репозитория разошлись; какая каноническая — не установлено.

### P1-D6. Каноническое знание не читается ничем

`ayla-knowledge/scripts/` — три файла, все валидируют **метаданные документов**.
Grep по `scripts/ tests/ .github/` на `intent-registry|slot-registry|intent-output.schema`
— **ноль вхождений**. `.knowledge/sources-manifest.yaml` — `enabled_default: false`.

Следствие для трека D: даже те 27 записей `CAP-*` и 12 интентов, что существуют
как данные, **сегодня не имеют пути в рантайм**. Контракт части 2 обязан
описывать загрузку как то, чего нет, а не как то, что надо переиспользовать.

### D7 (не дефект, а незакрытый замер)

`docs/catalog/CATALOG_NORMALIZATION.md` содержит два взаимно противоречащих
утверждения о длительностях канона (см. T3). Оба `STALE` от 23.08.
**Требуется перезамер, а не выбор одного из двух по вкусу.**

---

## 8. Сводка source of truth

| Тип | Source of truth | Владелец | Версионирование | Путь в рантайм |
|---|---|---|---|---|
| T1 Способности | `ayla-knowledge/00 Foundation/Ayla Domain Capability Registry.md` (27 записей, YAML в markdown) — но это способности **подсистем**, не услуг | Product Architecture (frontmatter) | frontmatter `version: "1.2"`, `schema_version` 1.13/1.14 | **нет** |
| T2 Услуга ↔ способность | **не существует** | не назначен | — | нет |
| T2' Идентичность строки каталога | `apps/catalog/services/linking.py:68` `matched_by ∈ {pair, pair+duration, manual}` | `ai-bot-platform` | нет | есть |
| T3 Длительность | цепочка `SpecialistService → SalonService → ServiceTemplate`, `resolved_duration()` | бэкенд Ayla | `updated_at` | есть (с потерей `NULL`, P1-D2) |
| T3' Слотовая политика | `apps/scheduling/models.py:613` `SlotConfig` | салон | `updated_at` | есть |
| T4 Окна событий | **не существует** (есть окна атрибуции — другой предмет) | не назначен | — | нет |
| T5/T6 Интервалы | **не существует** | не назначен | — | нет |
| T7 Повторяемость | **не существует**, вывод запрещён ADR-0012 N-04 | не назначен | — | нет |
| T8 Последовательность процедур | **не существует** | не назначен | — | нет |
| T8' Последовательность слотов интента | `03 AI System/Contracts/intent-registry.yaml` (12 интентов, статус `draft`) | `ayla-knowledge` | `registry_version: "1.0"` | **нет** (не читается) |
| T9 Предусловия | **не существует** | не назначен | — | нет |
| T10/T11 Совместимость | **не существует**, реестр отклонён OD-CI-4/OD-CI-5 | отклонено | — | нет |
| T12 Восстановление | поле `Service.aftercare_text` пусто; правил нет | бэкенд Ayla (админка) | нет | нет (в зеркало не мигрирует) |
| T13 Безопасность реплики | `apps/orchestrator/safety/*` — константы модуля | `ai-bot-platform` (по канону должен `ayla-knowledge`) | **нет** | есть |
| T13' Гейт записи | `SpecialistService.resolved_requires_health_check` | бэкенд Ayla | нет | есть |

**Ни один тип планировочных ограничений не имеет одновременно: заполненных
данных, назначенного владельца, схемы версий и пути в рантайм.**
Ближе всех T3 — но и у него нет схемы версий.

---

## 9. Вопросы владельцу

Формат общего брифа. Мелочи, разрешимые из канона, здесь не выносятся.

### Вопрос D-1. Кто владеет планировочными правилами и заводит первую запись

**1. Что не определено.** Канон §15.7 назначает владельцем планировочных правил
`ayla-knowledge` / канонический каталог. Разведка показала: в `ayla-knowledge`
нет ни файла правил, ни словаря статусов, ни загрузчика — и **ничего из знаний
не читается кодом** (P1-D6). В бэкенде Ayla полей под эти правила тоже нет.
То есть назначенный владелец не имеет носителя.

**2. Почему канон не даёт ответа.** §15.7 назначает владение, но не говорит,
**в какой форме** правило хранится и **кто заводит первую запись**. Обе
трактовки совместимы с каноном.

**3. Вариант A.** Носитель — `ayla-knowledge`: правила как версионируемые
YAML-файлы, бэкенд и бот читают их через сборку/публикацию. Канонично;
требует построить загрузчик и путь публикации, которых сегодня нет вовсе.

**4. Вариант B.** Носитель — бэкенд Ayla: правила как таблица рядом с каталогом,
`ayla-knowledge` остаётся описанием. Быстрее (там уже есть образец
`CrossDomainRule` с `rule_id`, `legal_reviewed`, `legal_review_date` —
`djangoproject/nutrition/models.py:724,757-759`); расходится с §15.7.

**5. Последствия.** A — планировочные правила версионируются вместе с каноном
и переживают смену бэкенда, но до первой строки правила пройдёт вся работа
по загрузчику. B — первая строка появляется быстро и сразу доступна рантайму,
но правила окажутся в репозитории, который канон владельцем не считает,
и рискуют разъехаться с `ayla-knowledge`.

**6. Рекомендация.** **B с оговоркой:** носитель — бэкенд Ayla, но каждая строка
обязана нести `source` и `source_version`, указывающие на документ
в `ayla-knowledge`. Причина: существующий образец `CrossDomainRule` уже несёт
`rule_id` и след юридического ревью, а вариант A требует построить путь
публикации знаний, которого нет ни в одном виде — и до его постройки
не появится **ни одного** планировочного правила.

**7. Что заблокировано до решения.** Часть 2 может описать **форму** правила
и запрет без provenance, но **не может назвать физический адрес** источника.
Plan Engine не может быть начат.

### Вопрос D-2. Что Ayla отвечает на планировочный вопрос сегодня

**1. Что не определено.** Человек спрашивает «через сколько повторить?»,
«можно ли это вместе?», «за сколько до свадьбы?». Данных нет ни для одного
ответа (T4–T12). Что делает бот — не решено нигде.

**2. Почему канон не даёт ответа.** Канон §15.6 говорит, что **план** может
быть `INCOMPLETE`. Он не говорит, что делать с **вопросом вне плана**,
на который нет данных. §14.4 даёт форму «явные варианты восстановления»,
но только для случая отсутствующей услуги.

**3. Вариант A.** Честный отказ с передачей: «этого я не знаю, спрошу у мастера» —
по образцу `apps/skills/faq/prompts.py:242`.

**4. Вариант B.** Передача человеку салона сразу, по образцу решения §36
(«гейт сработал, текста противопоказаний нет → человек передаётся салону»).

**5. Последствия.** A дешевле и держит разговор в боте, но оставляет человека
без ответа. B дороже для салона (каждый такой вопрос — обращение к живому
человеку), но соответствует уже принятому владельцем принципу «цена ошибки
выше цены неудобства» на процедурах со скринингом.

**6. Рекомендация.** **A по умолчанию, B при поднятом гейте здоровья.**
Причина: §36 решение владельца привязало передачу к **срабатыванию гейта**,
а не к любому пробелу знания; распространять передачу на все планировочные
вопросы значит переносить в салон весь объём, который должен закрыть каталог.

**7. Что заблокировано до решения.** Формулировка `UNKNOWN`-ответа в части 2
описывается как форма, но **дословный текст для человека не утверждается**.

### Вопрос D-3 не выносится

Вопрос «включать ли рекомендации без `VERIFIED`-маппинга» уже стоит перед
владельцем как `OPEN_QUESTIONS_OWNER.md` §6.1 / `OPEN_DECISIONS.md` §40.4 п.1.
**Дубликат не заводится.** Трек D добавляет к нему один факт: словаря
`VERIFIED / REVIEW_REQUIRED / UNMAPPED` не существует **нигде**, включая
канонический репозиторий (§T1/T2 дополнение). То есть вариант «сначала построить
маппинг» сегодня означает ещё и «сначала завести шкалу», а не только «разметить
265 строк».

---

## 10. Прохождение пяти gates

| Gate | Результат | Почему |
|---|---|---|
| 1. Canon reconciliation | **пройден** | Сверено с Decisions 4, 5, 11, 12 канона v1.1. Найденные расхождения (состояния безопасности §13.2, отсутствие словаря статусов §T2) зафиксированы как расхождения, канон под код не переписан |
| 2. Code reality | **пройден** | Каждое утверждение «есть/нет» несёт repo/path/line. Утверждений о несуществующих API нет — наоборот, документ перечисляет, чего нет |
| 3. Cross-contract consistency | **пройден** | Идентификаторы и владение сверены. Отмечены две коллизии имён: `Capability` (RBAC против доменной способности, §T1) и `Plan` (канон §15 против `Care Plan` из `docs/SPEC_CARE_CONTRACT.md:269`). Сверка с `docs/specs/DECISION_READINESS_ENGINE_v1.0.md` — в контракте части 2, §10.3: расхождение состояний безопасности зафиксировано обоими документами независимо и одинаково |
| 4. Failure behavior | **пройден** | `UNKNOWN`, устаревшее (`STALE`, §0.3), недоступное (P1-D6), конфликтующее (D7, P1-D4/D5), заблокированное безопасностью (T13), отсутствующий маппинг (T2), отсутствующее свидетельство (§6.3) — разобраны |
| 5. Implementability | **не применим к части 1** | Инвентаризация — карта, а не контракт. Требование «разработчик пишет код без новых продуктовых решений» адресовано части 2 |

---

## 11. Что этот документ сознательно не делал

* Не проектировал Plan Engine — non-goal трека.
* Не проектировал Goal, DecisionReadiness, ранжирование.
* Не заводил недостающие данные.
* Не разрешал противоречие D7 выбором одного из двух замеров.
* Не решал за владельца вопрос §6.1 про `VERIFIED`.
* Не поднимал контейнеры и не снимал новые production measurements —
  все числа из документов помечены `STALE` с датой.
