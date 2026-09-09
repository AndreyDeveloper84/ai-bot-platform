# G6 / группа A — что записано: доменная модель, поля связи, сиды, импорт и синхронизация

**Треки промпта:** A (domain model), B (current mapping fields), C (seeds), K (import / sync).
Полный промпт владельца — `docs/measure-first-CanonicalService.md`.
Замер сделан **по коду и схеме**; живого доступа к контуру у этого окна нет — числа пилота взяты
из реестра как есть и не перепроверялись (см. §11 «Что не замерено»).

**Режим:** MEASURE-FIRST. Ничего не изменено, ничего не закоммичено, worktree не заводился,
production-код не написан, миграций нет, ни один `mapping_status` не тронут.

---

## 1. Базы замера

| репозиторий | каноническая ветка | как установлена | SHA | состояние рабочей копии |
|---|---|---|---|---|
| `djangoproject-catalog` (`AndreyDeveloper84/beautygo_backend`) | `origin/dev` | `origin/HEAD → master`, но `master` отстаёт на **694** коммита и датирован 03.09; пилот — `api-dev`/`dev.gobeauty.site`; рабочая копия стоит на `dev` | `95c917e684652476feef3ae9d790fb2c8d277378` (2026-09-09 06:50 +0300) | чисто (2 неотслеживаемых: `.claude/`, `AGENTS.md`) |
| `ai-bot-platform` | `origin/dev` | **других mainline-веток нет**: `origin/main` и `origin/master` не существуют | `83ed56a94eb0a96d9599c632f0e6ba2d48c6c5b7` (2026-09-09 10:52 +0300) | рабочая копия стоит на `feat/recommendation-boundary-client` `fd6f4e8` — **читалось не с неё**, всё через `git show origin/dev:<path>` |
| `ayla-ai-core` | `origin/main` | `origin/HEAD → main`; **`origin/dev` по-прежнему не существует** (подтверждает прошлый замер) | `d72a5de451f985d118d9449d2b17ce51bf0a6e25` | чисто |
| `ayla-knowledge` | `origin/main` | `origin/HEAD → main` | `207eeb638580e529aaff66e0d1412aa6b559c0a3` | рабочая копия на `drf-1148/canon-under-version-control`; читалось через `git show origin/main:` |
| `Ayla/docs` (отдельный git, remote не настроен) | `main` | единственная ветка | `b3c782be493f1a7ca4c883c0ac973c8e448334e8` | **60 изменённых строк статуса** — цитаты помечены как «из рабочей копии» |

Все `git fetch` выполнены перед `rev-parse`. Каноническая ветка нигде не угадывалась.

---

## 2. Сводка по классам числом

| класс | шт. | что именно |
|---|---:|---|
| `EXISTS` | 9 | схема связи и два её `CheckConstraint`; оба слоя предложения; зеркало и его сторожа; провенанс рёбер бота; C6-линковщик; односторонность границы |
| `PARTIAL` | 6 | `ServiceTemplate` в роли CanonicalService; админка; провенанс C6-линковки; `suggested_template`; B7/B8; сериализатор границы |
| `MISSING` | 5 | CanonicalService как сущность; Capability как runtime-слой; писатель `mapping_status` в проде; переходы статусов; audit trail и инвалидация |
| `SEED_ONLY` | 2 | 171 из 206 связей; `bootstrap_e2e_wave1` |
| `MANUAL_ONLY` | 3 | 35 из 206 связей (загрузка 23.08); админ-форма как единственный писатель; `suggested_template` |
| `DEAD_CODE` | 2 | `capability_refs`/`canonical_service_refs`; `human_confirmed_ref` как `source_ref` |
| `CONTRADICTS_CANON` | 4 | два параллельных слоя предложения; Need→категория мимо двух уровней; канонический идентификатор теряется при загрузке; статус зависит от времени вставки строки |
| `TERMINOLOGY_COLLISION` | 2 | «Capability» = продуктовая способность в каноне ≠ Capability канона G6; «template» несёт три роли |
| `STALE_SPEC` | 1 | канон `ayla-knowledge` про Canonical Service помечен `Preliminary`, границу держит открытой |
| `UNKNOWN_NOT_MEASURED` | 4 | см. §11 |

**Ответ на главный вопрос группы A одной строкой:** в проде **нет ни одного пути кода,
который пишет `SalonService.mapping_status` или любое из пяти полей провенанса.**
Единственный писатель за всю историю — миграция данных `0017`. Все `VERIFIED`
в репозитории существуют только в тестовых фикстурах.

---

## 3. TRACK A — доменная модель

### 3.1 Что фактически является TenantOffer сегодня

**Два разных слоя, и это не одна модель.** `djangoproject-catalog@95c917e`:

| модель | файл | ключ | есть `template`? | есть `mapping_status`? |
|---|---|---|---|---|
| `SalonService` | `services/models.py:314` | uniq `(tenant, template, name)` | **да**, FK, nullable | **да** |
| `Service` (легаси, маркетплейс) | `services/models.py:216` | natural key отсутствует | **нет** | **нет** |
| `SpecialistService` (записываемая единица) | `services/models.py:498` | uniq `(specialist, salon_service)` | через `salon_service` | своего нет |

`services/catalog_reads.py:295 catalog_services_for()` возвращает **оба слоя**, и подбор
читает оба (`users/recommendation_source.py:210`). Легаси-строка канонической связи иметь
не может **по устройству**: `_MappingFacts.status()` (`recommendation_source.py:424-428`)
отдаёт для неё `UNMAPPED`, и это записано прямо в докстринге.

**Следствие, которое надо назвать.** `TenantOffer` — не одна вещь. Мастер, чьи услуги живут
только в легаси `Service`, **структурно недостижим для `VERIFIED`**: у его строк нет поля,
которое можно подтвердить. Это не дефект разметки и никакой разметкой не лечится.
**Класс: `CONTRADICTS_CANON`.**

Коммерческие признаки предложения на месте: `base_price`, `duration_minutes`, `is_active`,
`source` (`manual|yclients|seed`), локальное `name`; цена и возможность записи мастера —
на `SpecialistService`. **Класс: `EXISTS`.**

### 3.2 Что фактически является CanonicalService сегодня

**Отдельной сущности с таким именем нет ни в одном из четырёх репозиториев.**
Проверено грепом по канонической ветке каждого: в `djangoproject-catalog` совпадения
только на `canonical_service_refs` (поле запроса, §3.5) и на именах тестов;
в `ai-bot-platform` — `apps/planning_rules/registry.py:68 CLOSED_SUBJECT_KINDS`
(строковый литерал `"canonical_service"` в перечне допустимых видов субъекта правила,
без модели за ним); в `ayla-ai-core` — ноль.

Роль канонической смысловой идентичности **фактически играет `ServiceTemplate`**
(`services/models.py:78`), и это доказывается поведением, а не именем:

* `SalonService.template` — единственная FK, по наличию которой миграция `0017` отличала
  «связь есть» от «связи нет»;
* `seed_demo_salons._refuse_unresolved_templates()` (`:218`) падает, если услуга сида не
  ложится на шаблон — шаблон трактуется как обязательная каноническая опора;
* `SpecialistService.resolved_requires_health_check()` (`services/models.py:574`) берёт
  **пол безопасности с шаблона** — то есть шаблон несёт смысловые свойства, а не оформление;
* внутренний сериализатор границы отдаёт `template` боту (`services/serializers.py:185`).

**Но в роли канона он неполон. Класс: `PARTIAL`.** Чего нет:

1. **Канонического идентификатора.** Файл-источник `services/seeds/canonical_catalog_2026-07.json`
   несёт **1223 строки и 1223 уникальных кода** вида `"1.1.1"` (иерархический номер услуги).
   `seed_canonical_catalog._seed()`
   (`services/management/commands/seed_canonical_catalog.py:113-135`) **код не сохраняет —
   такого поля в `ServiceTemplate` нет вовсе.** Идентичность канонической услуги после
   загрузки — это `unique_together = [('category', 'name')]` (`services/models.py:131`),
   то есть **пара отображаемых строк**. Переименование шаблона молча создаёт другую
   каноническую услугу.
2. **Версии.** Ни `catalog_version`, ни аналога нигде. `SalonService.mapping_rule_version`
   версионирует **правило подтверждения**, а не каталог.
3. **Алиасов и синонимов.** Грепом по `services/`, `users/`, `recommendation/`, `goals/`:
   ни таблицы, ни поля, ни файла алиасов. Все `alias` в репозитории — про URL-маршруты.
4. **Жизненного цикла.** Ни `status`, ни `deprecated`, ни `published`. Канон
   `ayla-knowledge@207eeb6` `05 Architecture/Ayla Domain Context Map.md:1969-1977` объявляет
   `Draft → Reviewed → Published → Updated → Deprecated → Archived` — рантайм не имеет ни одного.
5. **Согласованности мультиарендности.** `ServiceTemplate` **не имеет** `tenant`, а его
   `category` — имеет (`ServiceCategory.tenant`, `services/models.py:31`). Сид кладёт
   категории с `tenant=NULL`. Глобальный шаблон висит на категории, которую арендатор
   в принципе может присвоить. Плюс `ServiceCategory.name` объявлено `unique=True`
   глобально — пространство имён таксономии общее на всех арендаторов.

### 3.3 Есть ли Capability как runtime-сущность

**Нет. Класс: `MISSING`.** Ни модели, ни таблицы, ни поля ни в одном репозитории.

**И здесь стоит ловушка имени, которую надо снять явно.**
`ayla-knowledge@207eeb6` содержит `00 Foundation/Ayla Domain Capability Registry.md`
(v1.2, `status: draft`, `decision_status: proposed`). Прочитан: это реестр
**продуктовых/бизнес-способностей** — «понимание намерения», «формирование объяснимых
рекомендаций», «биллинг и eligibility», с идентификаторами `CAP-001…CAP-022` и циклом
`identified → … → retired`. Документ **сам** говорит (§1.1): «Domain capability … не
описывает конкретную реализацию, сервис, модуль, API, таблицу базы данных».

Это **не** Capability канона G6 («то, что услуга способна обеспечить»).
**Класс: `TERMINOLOGY_COLLISION`.** Принять совпадение слова за наличие слоя — ровно тот
случай, против которого правило «имя производной не доказывает происхождение».

Слой `Capability → CanonicalService` в смысле G6: **`MISSING_RUNTIME`**, в каноне —
`DECLARED_ONLY` и притом `Preliminary` (§3.4).

### 3.4 Что об этом говорит канон (сравнение — ПОСЛЕ кода)

`ayla-knowledge@207eeb6` `05 Architecture/Ayla Domain Context Map.md`:

* `:1983-1989` — инварианты Service Catalog: «одна canonical service identity не должна
  иметь противоречивый смысл», «provider-specific offering не должно изменять canonical
  service», «deprecated service не должна использоваться для новых рекомендаций»;
* `:1992-1996` — **открытые вопросы того же документа**: «Где проходит граница между
  canonical service и provider offering?», «Кто владеет health-check metadata?»,
  «Как версионируется taxonomy?»;
* `:2021` — `Canonical Service` перечислен в **Non-Owned Concepts** контекста
  Specialist/Provider; `:2051` — «provider offering должно ссылаться на canonical service».

Все разделы помечены `Preliminary`. **Класс: `STALE_SPEC` / `DECLARED_ONLY`** — канон
описывает слой, которого рантайм не имеет, и сам же держит его границу открытой.

`ayla-ai-core@d72a5de` в семантике связи **не участвует вовсе**: грепом по `src/` ни
`canonical`, ни `mapping` в этом смысле; это библиотека промптов и оркестрации.

### 3.5 Need → Capability → CanonicalService в рантайме

Реально существует другая цепочка, короче канонической на два уровня:

```
GoalOption (курируемая подсказка цели)          services/models.py:834
        │  GoalOptionCategory                    services/models.py:871
        ▼
ServiceCategory                                  services/models.py:13
        ▲
        │ SalonService.category  (или template.category)
TenantOffer
```

То есть **нужда привязана к КАТЕГОРИИ, а не к канонической услуге и не к способности.**
Capability пропущен целиком, CanonicalService пропущен как звено нужды.
**Класс: `CONTRADICTS_CANON`.**

Отдельно: `recommendation/_types.py:281-282` объявляет на `NeedSpec` поля
`capability_refs: tuple[UUID, ...]` и `canonical_service_refs: tuple[UUID, ...]`,
`recommendation/_serializers.py:73-74,122-123` принимает их **с провода**. Полный список
их потребителей — одна строка: `_types.py:293 is_specified()`, где они участвуют только
в проверке «нужда вообще названа». **Ни одна стадия резолвера их не разрешает ни во что.**
**Класс: `DEAD_CODE` с живым последствием:** вызывающий, передавший
`canonical_service_refs`, получит `is_specified() == True` и подбор, который про эти
ссылки не знает. Имя поля обещает разрешение, которого нет.

---

## 4. TRACK B — нынешние поля связи

### 4.1 Схема (`services/models.py:395-465`)

| поле | тип | умолчание | ограничение |
|---|---|---|---|
| `mapping_status` | `CharField(16)`, choices `unmapped/review_required/verified` | **`unmapped`** | индекс `(tenant, mapping_status)` |
| `mapping_confirmed_by` | FK `AUTH_USER_MODEL`, `SET_NULL`, nullable | `NULL` | — |
| `mapping_confirmed_rule` | `CharField(100)`, blank | `""` | — |
| `mapping_rule_version` | `CharField(32)`, blank | `""` | — |
| `mapping_confirmed_at` | `DateTimeField`, nullable | `NULL` | — |
| `mapping_source_ref` | `CharField(200)`, blank | `""` | — |

Два `CheckConstraint` — **на уровне схемы, а не договорённости**:

1. `salonservice_verified_requires_provenance` — `mapping_status='verified'` разрешён,
   только если `mapping_confirmed_at IS NOT NULL` **и** `mapping_source_ref <> ''` **и**
   (`mapping_confirmed_by IS NOT NULL` **или** `mapping_confirmed_rule <> ''`).
2. `salonservice_rule_confirmation_carries_version` — непустое правило обязано нести версию.

Проверено, что это действительно схема: оба присутствуют в
`0016_salonservice_mapping_status.py:66-95` как `AddConstraint`, то есть живут в БД
и держат **любой** путь записи — включая `.update()`, `bulk_update` и миграции данных.
`SalonService.save()` (`:475`) зовёт только `clean()`, проверяющий исключительно
«template или category». **Класс: `EXISTS`** — и это самая сильная часть всей связи.

**Смысловое разделение `source` и `mapping_*` зафиксировано в коде явно**
(`services/models.py:395-400`): `source` говорит, откуда взялась **строка**; `mapping_*` —
чем доказана **связь**. Это ровно то различение, которого требует канон.

### 4.2 Миграции — прочитаны построчно, не по именам

**`services/migrations/0016_salonservice_mapping_status.py`** (сгенерирована 2026-09-08 09:09).
Чистая схема: шесть `AddField`, один `AddIndex`, два `AddConstraint`. **Данных не трогает.**
Зависимости: `services.0015_goaloption_goaloptioncategory`,
`tenants.0003_seed_default_tenants`, `swappable_dependency(AUTH_USER_MODEL)`.

**`services/migrations/0017_mapping_status_backfill.py`.** Три строки логики: берёт
историческую модель, зовёт `backfill.classify(...)`, печатает счётчики. Обратной операции
**намеренно нет** (`RunPython.noop`) — докстринг объясняет: откат, возвращающий всё
в `unmapped`, стёр бы решения людей, принятые после применения.

**`services/migrations/_mapping_status_backfill.py:classify()`** — правило целиком:

```python
undecided = salon_service_model.objects.filter(mapping_status="unmapped")
review_required = undecided.filter(template__isnull=False).update(
    mapping_status="review_required",
)
unmapped = salon_service_model.objects.filter(mapping_status="unmapped").count()
```

То есть **`REVIEW_REQUIRED` ⟺ `template IS NOT NULL` на момент прогона миграции.**
Ничего не вычислялось, ничего не сравнивалось, никакой matcher не звался.
Имя файла начинается с `_`, чтобы `MigrationLoader` его не подхватил; модуль вызывается
и из миграции, и из теста `services/tests/test_mapping_status_provenance.py:197`.
`VERIFIED` этот код **не умеет писать физически** — литерала `"verified"` в нём нет.

### 4.3 Кто пишет — полная перепись

Грепом по всему `djangoproject-catalog@95c917e` на
`mapping_status|mapping_confirmed|mapping_rule_version|mapping_source_ref`:
**135 вхождений**. Разложены полностью:

| писатель | где | что пишет | класс |
|---|---|---|---|
| миграция `0017` | `_mapping_status_backfill.py:52` | `unmapped → review_required` при `template IS NOT NULL` | `EXISTS`, одноразово |
| **никто больше в проде** | — | — | **`MISSING`** |
| админ-форма `SalonServiceAdmin` | `services/admin.py:113` | все шесть полей редактируемы (нет `fields`/`fieldsets`, нет в `readonly_fields`), но **нет** в `list_display` и `list_filter`, нет действий и очереди | `MANUAL_ONLY` / `PARTIAL` |
| тесты | 7 файлов | `VERIFIED` с `mapping_confirmed_rule="test_fixture"` и `"canonical_exact_name"` | фикстура |

**Отдельно назову ловушку.** В `services/tests/test_mapping_status_provenance.py:146`
фикстура пишет `mapping_confirmed_rule="canonical_exact_name"`. **Правила с таким именем
в коде не существует** — грепом по всем четырём репозиториям ноль совпадений вне теста.
Имя правила в фикстуре — не правило.

**Ответы на B1–B9:**

* **B1 — кто выставляет `UNMAPPED`:** никто явно. Это **умолчание колонки**
  (`default=MappingStatus.UNMAPPED`). Строка получает его при любом `INSERT`.
* **B2 — кто выставляет `REVIEW_REQUIRED`:** миграция `0017`, один раз, по признаку
  «`template` заполнен». В проде — никто. Через админ-форму — человек вручную.
* **B3 — кто выставляет `VERIFIED`:** **никто.** Ни одна строка production-кода не
  присваивает `MappingStatus.VERIFIED`. Только тестовые фикстуры и, теоретически, человек
  в Django-админке, вручную заполнив поля провенанса, — иначе строку не примет БД.
* **B4/B5 — переходы:** реализован **ровно один**: `UNMAPPED → REVIEW_REQUIRED`, и только
  внутри миграции. `REVIEW_REQUIRED → VERIFIED`, `VERIFIED → REVIEW_REQUIRED`,
  `VERIFIED → UNMAPPED` — **`MISSING`**, кода нет. Authority ни на один переход не
  выражена: нет ни permission-класса, ни роли, ни проверки.
* **B6 — audit trail:** **`MISSING`** на стороне каталога. Есть только
  `mapping_confirmed_at` (одна перезаписываемая отметка) и `mapping_source_ref`
  (свободная строка). Истории переходов нет; кто снял `VERIFIED` — узнать неоткуда.
  Контраст с ботом — §6.4.
* **B7 — почему `VERIFIED`:** частично. Схема гарантирует, что `mapping_source_ref`
  непуст, но это свободная строка без типа. **`PARTIAL`.**
* **B8 — кто verified:** да, если человек (FK `mapping_confirmed_by`); если правило — его
  имя. Взаимоисключительность объявлена в комментарии, но **ограничением не проверяется**:
  схема допускает и `confirmed_by`, и `confirmed_rule` одновременно. **`PARTIAL`.**
* **B9 — на какой версии каталога verified:** **`MISSING`.** `mapping_rule_version`
  версионирует правило, а не каталог. Версии каталога нет как поля нигде (§3.2 п. 2).
  Смыкается с **OD-CSM-1**.

### 4.4 Кто читает

| читатель | где | что делает |
|---|---|---|
| `SpecialistCandidateSource._mapping_by_specialist` | `users/recommendation_source.py:196-207` | кладёт `salon.mapping_status` в карту `SalonService.id → статус`; **только читает** |
| `_MappingFacts.status()` | `users/recommendation_source.py:393-434` | по совпавшей услуге отдаёт её статус; без названной нужды — лучший по мастеру (`_RANK`); без предложений — `UNKNOWN` |
| `_mapping_admission` | `recommendation/_stages.py:371-385` | **`recommendation_eligible = (mapping_status == VERIFIED)`**, второй ветки нет; всё, кроме `VERIFIED`, выбывает |
| наблюдаемость | `recommendation/_stages.py:266` | считает распределение статусов по кандидатам |

**Мёртвая проводка, которую надо назвать.** `recommendation_source.py:203-205` вычисляет
`facts.human_confirmed_ref = f"draft_confirmed:{salon.id}"` для тех `SalonService`, что
материализованы из **подтверждённого человеком драфта с `suggested_template`**. Значение
доезжает ровно в одно место — `CandidateFacts.source_ref` (`:288`), а тот читается ровно
в одном — `_stages.py:381`, **внутри ветки, куда попадают только `VERIFIED`**. Поскольку
`VERIFIED` не выставляет никто, эта строка **никогда не исполняется**.
**Класс: `DEAD_CODE`.** Но она опаснее обычного мёртвого кода: она **готова** объявить
«подтверждено человеком» на основании факта «человек нажал подтвердить драфт» — а это
разные акты (**OD-CSM-3**).

### 4.5 `suggested_template` и `template` — разные поля разных моделей

Существенное уточнение к постановке: **`suggested_template` не является полем
`SalonService`.** Оно живёт на `DraftSalonService` (`services/models.py:633`) —
staging-строке импорта.

| | `SalonService.template` | `DraftSalonService.suggested_template` |
|---|---|---|
| смысл | установленная связь | гипотеза до подтверждения |
| писатели в проде | `seed_demo_salons:406`, `confirm.py:84,93`, `bootstrap_e2e_wave1:121` | **ни одного** |
| писатели вне прода | админка, тесты | админка (`admin.py:148 raw_id_fields`), тесты |
| читатели | резолвер, сериализатор границы, сид, расчёт health-check | `confirm.py:73`; `recommendation_source.py:188` (только `IS NOT NULL`) |

**Перепроверено и подтверждено: `suggested_template` не вычисляет никто.**
`services/integrations/intake/pipeline.py:upsert_service_draft()` — единственный
production-путь создания драфта — **не присваивает `suggested_template` ни при создании
(`:84-92`), ни при обновлении (`:73-80`)**. Значения появляются только рукой через
админку. **Класс: `MANUAL_ONLY`.**
Побочно хорошее: повторный импорт `suggested_template` **не затирает** (его нет
в `update_fields`) — рукотворная гипотеза переживает ночной ре-импорт.

---

## 5. TRACK C — сиды

### 5.1 `seed_demo_salons.py` — перепроверено и названо точно

Предварительное утверждение («`:402` берёт шаблон из собственной фикстуры и пишет обе
стороны связи») **подтверждается по механике, но требует двух уточнений.**

Дословно (`services/management/commands/seed_demo_salons.py@95c917e`):

```
:400   templates = self._template_map(salon)
:402   template = templates[(svc["category"], svc["template"])]
:403   name = svc.get("name", svc["template"])
:404   salon_service, created = SalonService.objects.get_or_create(
:405       tenant=tenant,
:406       template=template,
:407       name=name,
:408       defaults={"category": template.category, ..., "source": SalonService.Source.SEED},
```

`_template_map()` (`:449`) — **точный словарный поиск** по паре
`(имя категории, имя шаблона)`, взятой из строки фикстуры. Никакой нормализации, никакой
близости, никакого выбора между кандидатами. `_refuse_unresolved_templates()` (`:218`)
заранее падает `CommandError` со списком всех непопавших пар.

**Уточнение 1: сид пишет одну сторону связи, а не «обе».** Он пишет FK `template`
и локальное имя, но **`mapping_status` и все пять полей провенанса не трогает вовсе** —
их в `defaults` нет, строка создаётся с умолчанием `unmapped`. `REVIEW_REQUIRED` появился
позже и **не от сида, а от миграции `0017`** по признаку «`template` заполнен».
Это важнее, чем звучит: **сид не сделал вид, что связь проверена; вид, что связь
предположена, сделала миграция.**

**Уточнение 2: сид — не единственный источник 206.** Разбор фикстуры
`services/seeds/demo_salons_2026-08.json` (единственная версия в истории файла, коммит
`908406ca`, 31.08): **5 салонов, 171 строка услуг, 171 уникальная пара**:

| slug фикстуры | услуг в фикстуре | `review_required` в замере 08.09 §1.2 |
|---|---:|---:|
| `olhovyy-dvor` | 62 | 62 |
| `fevralskiy-svet` | 43 | 43 |
| `sorok-okon` | 28 | 28 |
| `pylca-i-lyon` | 21 | 21 |
| `mednyy-kovsh` | 17 | 17 |
| **итого** | **171** | **171** |

**Совпадение построчное, по всем пяти.** Это доказывает: 171 из 206 — строки
`seed_demo_salons`, и их `template` взят из фикстуры её автором.
**Класс: `SEED_ONLY`.**

**Остальные 35** — арендаторы `mkt-*`, которых в фикстуре нет:
`mkt-mediclinic 24`, `mkt-afrodita 8`, `mkt-lumina 2`, `mkt-spatrium 1` (+1 `unmapped`).
Их происхождение установлено **из рабочей копии `Ayla/docs@b3c782b`**
(`docs/catalog/MARKET_PENZA.md:171-186`): загружены **23.08 вручную**, по временному
разрешению владельца («грузи, потом снесём, надо тестировать на нескольких»), из открытых
справочников (2ГИС, Zoon, сайты салонов) — **сами салоны согласия не давали**. Там же:
«35 из 36 услуг привязаны к каноническому шаблону… Три комплекса «Афродиты» сначала не
привязались из-за разницы в кавычках — канон пишет `“…”`, я писал `«…»`. Исправлено
**нормализацией**».

То есть 35 связей поставлены **разовым ручным действием с нормализацией имён, которой
нет в коде ни одного репозитория**. Скрипта нет в дереве; провенанс не сохранён ни в одной
строке БД. **Класс: `MANUAL_ONLY`; сам механизм — `UNKNOWN_NOT_MEASURED`** (его нельзя ни
прочитать, ни воспроизвести).

**Итог трека C:**

```
206 review_required = 171 SEED_ONLY + 35 MANUAL_ONLY
  0 подобрано production-кодом
 59 unmapped        = 58 formula-tela (настоящий импорт YClients) + 1 mkt-spatrium
```

Проверка сходится и со стороны бота: `ai-bot-platform@83ed56a`
`docs/runbooks/connect-five-salons-drf1510.md` считает «уже подключено:
`formula-tela` 58, `mkt-mediclinic` 24, `mkt-afrodita` 8, `mkt-lumina` 2,
`mkt-spatrium` 2 = 94; 94 + 171 = 265 = весь бэкенд». `265 = 206 + 59`.

Гипотеза промпта («production mapping mechanism может отсутствовать вообще») в части
треков A/B/C/K **подтверждается**: механизма нет, а видимость «206 связей ждут проверки»
создана сочетанием фикстуры, ручной загрузки и миграции, которая опросила только
`template IS NOT NULL`.

**Создаёт ли сид иллюзию работающего pipeline — да, но не тем, чем кажется.** Сам сид
честен: он падает, если пара не находится, и статусов не пишет. Иллюзию создаёт
**число 206 в отчёте**, у которого нет колонки «чем поставлено».

### 5.2 Прочие сид-пути

| путь | что делает со связью | класс |
|---|---|---|
| `services/.../seed_canonical_catalog.py` | создаёт **сам канон**: 22 корневые категории, подкатегории, 1223 `ServiceTemplate` через `update_or_create` по `(category, name)`; `requires_health_check` берёт из строки `"true"/"false"` файла (**102 из 1223** — `true`), `contraindications` — из поля `note` | `EXISTS`, но см. §7 H2 |
| `appointments/.../bootstrap_e2e_wave1.py:121` | `SalonService` с **`template=None`** и `source=SEED` → навсегда `unmapped` | `SEED_ONLY`, безвредно |
| `seed_goal_options.py`, `seed_service_templates.py`, `seed_regional_pricing.py`, `seed_track_e_categories.py` | к `mapping_*` не прикасаются (грепом) | — |
| `ai-bot-platform` `apps/catalog/.../seed_dev_formula_tela.py` | зеркало бота, не Ayla; штампует синтетические `uuid5` на рёбра, чтобы синхронизация могла их потом снять | вне предмета |

**TEST/DEMO DATA GENERATION против PRODUCTION MAPPING LOGIC:** второй колонки не существует.
Всё, что в контуре выглядит как сопоставление, — первая.

---

## 6. TRACK K — импорт и синхронизация

### 6.1 Граница: направление и её односторонность

Источник истины — Ayla (`djangoproject-catalog`), зеркало — бот (`ai-bot-platform`,
`apps/catalog`). Направление одно: Ayla → бот.
Проверено, что **обратного пути для каталога нет**: полный перечень салонных маршрутов,
которые бот вообще умеет звать, объявлен как данные в
`apps/integrations/ayla/salon_surface.py:138+` (`SALON_ROUTES`) — записи, брони,
расписание, отмены; **ни одного `POST`/`PATCH` в каталог или в поля связи**.
**Синхронизация не может затереть установленную связь на стороне Ayla — по отсутствию
пути записи.** Класс: `EXISTS`.

### 6.2 Что копируется — и чего в копии нет

**`SalonServiceInternalSerializer`** (`djangoproject-catalog/services/serializers.py:167-188`),
`Meta.fields`:

```
id, tenant, template, category, name, duration_minutes, base_price,
requires_health_check, is_active, source, goals, created_at, updated_at
```

**`mapping_status` в списке нет. Ни одного из пяти полей провенанса нет.**

Дальше по цепи: `apps/catalog/services/http_client.py:95 CatalogSalonServiceDTO` несёт
`template: str | None` и `raw=row` (весь JSON строки, `:833`) — но раз сериализатор
статуса не отдаёт, в `raw` его тоже нет **по устройству**, а не по забывчивости.
`apps/catalog/services/upserter.py:_service_fields()` (`:123-147`) пишет в зеркало ровно
восемь полей: `external_updated_at, name, is_active, requires_health_check, price_from,
duration_min, goals, raw`. **`template` не пишется даже в колонку — своей колонки для него
в `CatalogService` нет** (докстринг признаёт это прямо).

Грепом по `ai-bot-platform@83ed56a`, `*.py`, без тестов: `mapping_status`, `MappingStatus`,
`review_required` — **ноль вхождений**.

**Следствие.** На зеркале `UNMAPPED`-предложение и `VERIFIED`-предложение **неразличимы**.
Гейт допустимости целиком живёт на стороне Ayla (`recommendation/_stages.py:378`), и бот
получает его результат вызовом резолвера
(`apps/integrations/ayla/recommendation_resolver_client.py`), а не собственным
рассуждением. По ADR-0009 (зеркало — read-replica) это **не противоречие**, но означает:
**любой путь бота, отвечающий клиенту из зеркала напрямую, минуя резолвер, работает вне
гейта связи.** Такой путь существует — `apps/marketplace/discovery.py`, разобран
в `Ayla/docs@b3c782b:ANALYSIS_SERVICE_MATCHING.md` на `7884222e`; это предмет групп B/C,
здесь фиксирую как факт границы и **не переизмеряю**. **Класс: `PARTIAL`.**

### 6.3 Что синхронизация перезаписывает каждый раз, а что нет

| строка | перезаписывается на каждом такте | не трогается никогда |
|---|---|---|
| `CatalogService` (услуга-зеркало) | восемь полей `_service_fields`, включая `goals` **целиком** — пустой список сверху опустошает зеркало, намеренно | `slug`, описания, `seo_*`, `is_popular`, `contraindications`, `ayla_service_id` |
| `CatalogMaster` (мастер-зеркало) | `name, bio, experience, rating, review_count, is_active, ayla_user_id, external_updated_at, raw, address, location_lat/lng` | **платформенные**: `invite_status, mode, photo_url, archived_at, invited_at, max_handle, linked_bot_user` |
| `MasterService` (ребро мастер↔услуга) | только строки с `ayla_specialist_service_id IS NOT NULL` | **операторские строки (`NULL`) синхронизация не снимает никогда** |

`upsert_specialists` (`upserter.py:150+`) дополнительно несёт **сторож чужого арендатора**
(DRF-1313): DTO с `tenant`, не равным синхронизируемому, пропускается с предупреждением;
DTO **без** `tenant` не блокируется — «непроверяемо ≠ несовпадение».
Услуги и мастера — **upsert-only**, проактивной деактивации нет; рёбра реконсилируются,
и неполный обход страниц (`complete=False`) **понижает такт до аддитивного**
(`sync.py:262-266`), чтобы отсутствие в неполном снимке не превратилось в удаление.

**Прямой ответ на вопрос брифа — может ли синхронизация затереть установленную связь:**

* **Ayla → бот: нет.** Полей связи в потоке нет; писать в Ayla бот не умеет.
* **Внутри бота: нет** для операторских рёбер — их отличает `NULL`
  в `ayla_specialist_service_id`, и это единственный дискриминатор синхронизации.
* **Внутри Ayla: да, один путь, и он не про синхронизацию — про повторное подтверждение.** §6.5.

### 6.4 Контраст, который стоит назвать

У бота **ребро мастер↔услуга** несёт полный провенанс записи
(`apps/catalog/models.py:741-770`): `source` (какой писатель создал),
`created_by_actor_id` (`BotUser.id` человека, **не FK** — «форензический штамп, обязанный
пережить удаление строки»), плюс `ayla_specialist_service_id` как дискриминатор владения.
Комментарий там же честно фиксирует, что старое поле `created_by` **мертво с миграции
`0002`** и что 232 пилотных ребра «выглядели безавторными» именно поэтому.

У Ayla **связь предложение↔канон** имеет такие же по замыслу колонки — и **ни одного
писателя**. Более слабая по смыслу связь (кто выполняет услугу) снабжена работающим
провенансом; более сильная (что услуга значит) — только схемой.

Отдельно, тем же классом: `apps/catalog/services/linking.py` — **единственный
реализованный сопоставитель имён во всей системе** (контракт C6: `(category_slug,
нормализованное имя)`, длительность как тайбрейк). Он связывает **легаси-строку зеркала
с id услуги Ayla**, а не предложение с каноном; `matched_by` (`"pair" | "pair+duration" |
"manual"`) вычисляется и **в отчёт печатается, но не сохраняется**:
`row.save(update_fields=["ayla_service_id", "synced_at"])` (`:258`). Полученный ключ
затем гейтит health-check и запись. **Класс: `PARTIAL`, провенанс — `MISSING`.**

### 6.5 Единственный путь Ayla, способный стереть связь

`services/integrations/intake/confirm.py:_get_or_create_salon_service()`:

```
:73    template = draft.suggested_template
:74    category = None if template is not None else fallback_category
:75-79 if template is None and category is None:  raise DraftNotConfirmable
:83    if mapping is not None:              # повторное подтверждение
:84        salon.template = template        # ← может быть None
:85        salon.category = category
:89        salon.save()
```

Повторное подтверждение драфта **переписывает `salon.template`** значением
`draft.suggested_template` — а оно в проде **всегда `None`** (§4.5). При переданном
`--category` строка проходит `clean()` и сохраняется **с обнулённым `template`**, при
этом `mapping_status` и провенанс **остаются прежними**. `CheckConstraint` этого не ловит:
поля провенанса не изменились.

Получается строка `review_required` (а в будущем — `verified`) **без связи вообще**.
Инвариант, на который опиралась миграция `0017` («`review_required` ⟺ `template` заполнен»),
после такого прогона нарушен молча.

**Достижимость — честно.** Штатная команда `intake_confirm`
(`services/management/commands/intake_confirm.py:59`) выбирает **только `status=PENDING`**,
а `confirm_draft` в конце ставит `CONFIRMED`. Значит через поставляемую команду ветка
повторного подтверждения **недостижима**. Она достижима прямым вызовом `confirm_draft(...)`
(shell, будущий вызывающий, админ-действие). **Класс: `EXISTS` (код) + `UNREACHABLE`
(через поставляемую команду).** Механизмом прода я его **не объявляю** — его никто не зовёт.

Побочно, в той же команде: `confirm_draft` зовётся **без `actor`** (`:79-82`), поэтому
`draft.confirmed_by` остаётся `NULL`, хотя команду запускал человек. Единственный
существующий human-in-the-loop путь **теряет личность подтверждающего**.

### 6.6 Сквозная проверка одного предложения — не сделана

Промпт требует прогнать один реальный `TenantOffer` по цепи
`строка БД → payload → строка зеркала → вход резолвера`. **Живого доступа у этого окна нет**
(EXECUTOR-RULES: замеры на контуре делает главное окно). По коду цепь ключей такова:

```
SalonService.id            ──serializer 'id'──▶ DTO.ayla_service_id ──▶ CatalogService.ayla_service_id
SpecialistProfile.user_id  ──recommendation_source.py:258──▶ CandidateRef ──▶ CatalogMaster.ayla_user_id
```

Второй ключ — тот самый дефект `SpecialistProfile.id` vs `user_id`, найденный замером
08.09 (реестр §2.3) и закрытый PR #303/#1492; в измеренном срезе
`recommendation_source.py:258` действительно отдаёт `specialist.user_id`.
**Первый ключ (услуга) сквозной проверкой не подтверждён — `UNKNOWN_NOT_MEASURED`,
и это ровно тот класс дефекта, который однажды уже дал «по 31 с обеих сторон,
пересечение ноль».**

---

## 7. Опасности миграции

**H1. Статус строки зависит от времени вставки, а не от её содержания.**
Миграция `0017` — разовая. Любой `SalonService` с непустым `template`, вставленный **после**
её применения (повторный `seed_demo_salons` на новую услугу, `confirm.py`, админка),
получит `unmapped`, тогда как строка-близнец, вставленная до, несёт `review_required`.
Через месяц по колонке нельзя будет отличить «про связь ничего не сказано» от «строка
младше миграции». **`CONTRADICTS_CANON`** — умолчание перестаёт означать «решения не
принимали». Сторожа нет.

**H2. Повторный `seed_canonical_catalog` стирает курирование канона.**
`update_or_create` с `defaults` жёстко пишет `duration_default=None, duration_min=None,
duration_max=None`, `requires_health_check` из файла, `contraindications` из `note`,
`is_popular=False`, `sort_order=idx`. Повторный прогон **обнуляет выкуренные длительности
и перезаписывает медицинские признаки версией файла 2026-07**. Обратимости нет,
предупреждения нет. И это тот же файл, из которого берётся `requires_health_check`
для **102 из 1223** канонических услуг.

**H3. Переименование канонической услуги — это создание другой канонической услуги.**
Идентичность `ServiceTemplate` = `(category, name)`; `code` из файла-источника
(1223 уникальных) не сохраняется. Переименование или перенос в другую подкатегорию даёт
новую строку и **оставляет старые связи висеть на прежней** либо ломает `unique_together`.
Ни инвалидации, ни версии каталога нет. Предмет **OD-CSM-1**.

**H4. `template` можно обнулить, не тронув статус** (§6.5). Пока `VERIFIED` нет ни у кого,
цена нулевая; после первого `VERIFIED` это становится путём к «подтверждённой связи,
которой нет».

**H5. Взаимоисключительность «кто ИЛИ правило» объявлена, но не сторожится.**
Комментарий `services/models.py:406-408` требует одного из двух;
`salonservice_verified_requires_provenance` принимает **и оба сразу**. Проверяемый
инвариант живёт в комментарии — ровно то, что EXECUTOR-RULES §4 запрещает.

**H6. Откат `0016` уносит колонку с решениями людей.** Осознано и записано в докстринге
`0017`; фиксирую как известную принятую опасность, не как дефект.

**H7. 35 из 206 строк очереди принадлежат салонам, которые владелец распорядился снести**
(«потом снесём», `MARKET_PENZA.md:174`). Любая работа, измеряющая «сколько связей
проверено», получит меняющийся знаменатель. Это **не решение владельца** (он уже
высказался), а вопрос **очерёдности** для главного окна.

---

## 8. Подтверждённые противоречия

| # | RUNTIME | SPEC / CANON | CLASS | CONSEQUENCE |
|---|---|---|---|---|
| C1 | `TenantOffer` — две несводимые модели (`SalonService`, легаси `Service`); у второй нет поля связи | канон: один уровень `TenantOffer` | `CONTRADICTS_CANON` | часть предложений **структурно** недостижима для `VERIFIED`; никакая разметка их не спасёт |
| C2 | нужда связана с `ServiceCategory` (`GoalOption → GoalOptionCategory → ServiceCategory`) | канон: `Need → Capability → CanonicalService` | `CONTRADICTS_CANON` | два уровня канона пропущены; «цель» ведёт в таксономию, а не в смысл услуги |
| C3 | `Capability` в `ayla-knowledge` — реестр **продуктовых** способностей `CAP-001…022`, явно «не описывает сервис/таблицу» | канон G6: Capability = что услуга способна обеспечить | `TERMINOLOGY_COLLISION` | наличие документа читается как наличие слоя; слоя нет |
| C4 | `ServiceTemplate` совмещает три роли: онбординговая подсказка мастера, каноническая идентичность, носитель медицинских свойств | канон: CanonicalService — отдельная роль | `TERMINOLOGY_COLLISION` | правка шаблона ради онбординга меняет канон и безопасность |
| C5 | `capability_refs` / `canonical_service_refs` принимаются с провода, не разрешаются нигде | §5 промпта: имя поля не создаёт свидетельства | `DEAD_CODE` | вызывающий примет приём за поддержку |
| C6 | `mapping_status` не пересекает границу зеркала | `ai-bot-platform:docs/specs/DECISION_READINESS_ENGINE_v1.0.md:352` объявляет `recommendation_eligible (маппинг VERIFIED)` собственностью бэкенда Ayla как `AUTHORITATIVE_DOMAIN` | **не противоречие** — соответствие ADR-0009 | но любой ответ бота из зеркала мимо резолвера идёт вне гейта |
| C7 | `Canonical Service` в `Domain Context Map` помечен `Preliminary`, а его граница — открытым вопросом | канон G6 считает уровень принятым | `STALE_SPEC` | документ-источник ещё не отвечает на вопрос, который G6 считает закрытым |
| C8 | тест выставляет `mapping_confirmed_rule="canonical_exact_name"` | правила с таким именем нет нигде | `CONTRADICTS_CANON` (свидетельство без источника) | зелёный тест читается как «правило есть» |

---

## 9. Решения владельца

Формулирую только то, что **не закрывается** каноном, фактом рантайма, механикой миграции
или инженерным выбором. Рекомендаций не даю.

---

### OD-CSM-1 — Что является идентичностью канонической услуги, и что обязано аннулировать подтверждённую связь

**Runtime evidence.**
`ServiceTemplate` (`djangoproject-catalog@95c917e:services/models.py:78`) идентифицируется
`unique_together = [('category', 'name')]` — парой отображаемых строк. Файл-источник
`services/seeds/canonical_catalog_2026-07.json` несёт **1223 строки с 1223 уникальными
кодами** вида `"1.1.1"`; `seed_canonical_catalog._seed()` (`:113-135`) **этот код не
сохраняет — поля для него нет**. Версии каталога нет ни на шаблоне, ни на категории, ни
глобально; `SalonService.mapping_rule_version` версионирует правило подтверждения, а не
каталог. Механизма пересмотра `VERIFIED` при изменении шаблона не существует (грепом:
ни сигналов, ни задач, ни команд).

**Existing canon.** `ayla-knowledge@207eeb6` `Domain Context Map:1983-1989`: «одна canonical
service identity не должна иметь противоречивый смысл», «taxonomy changes должны сохранять
historical consistency». Канон G6: `VERIFIED` — «достаточное контролируемое свидетельство».

**Why canon does not answer.** Канон требует, чтобы идентичность не была противоречивой,
и запрещает выдавать неподтверждённое за подтверждённое. Он **не говорит**, считается ли
переименование канонической услуги изменением **той же** идентичности или появлением
**другой**. Оба ответа непротиворечивы и дают разные продукты. Тот же документ канона сам
держит этот вопрос открытым (`:1992-1996`).

**Option A.** Идентичность канонической услуги — **курируемый стабильный код** (`code`
из файла-источника). Имя и категория — атрибуты. Переименование и перенос идентичность
**сохраняют**; подтверждённая связь их переживает.

**Option B.** Идентичность — **пара «категория + имя»** (как сейчас). Переименование =
объявление другой канонической услуги. Все связи на прежнюю обязаны быть пересмотрены,
то есть **переименование канона аннулирует подтверждения**.

**Consequence A.** Курирование каталога перестаёт быть опасным для разметки; появляется
внешняя ответственность — за стабильность кодов, включая ошибки курирования (код,
присвоенный не тому смыслу, теперь тоже стабилен). Требуется явное правило, что делать,
когда две канонические услуги признаны одной.

**Consequence B.** Смысловая гарантия сильнее: подтверждали именно то, что сейчас
написано. Цена — курирование каталога становится массовым аннулированием: каждое
переименование обнуляет разметку, а без версии каталога нельзя даже показать человеку,
**что именно** изменилось.

**Blocks.** Треки G (инвалидация) и M (жизненный цикл); любую работу, вводящую первое
`VERIFIED`; любую правку канонического каталога.

---

### OD-CSM-2 — Закрыт ли канонический каталог для услуг, которых в нём нет

**Runtime evidence.**
Канон — 1223 `ServiceTemplate`. `formula-tela` — единственный салон с настоящим импортом
YClients: **58 услуг, у всех `template IS NULL`**, `mapping_status='unmapped'`
(реестр §1.2, 08.09). Сырьё импорта (`DraftSalonService.raw_payload`) — комбо-услуги вроде
«Классика (голени с коленями + глубокое бикини + подмышки)»
(`Ayla/docs@b3c782b:ANALYSIS_PACKAGES.md §1.2`). У `mkt-spatrium` 1 из 2 услуг —
«Тайский массаж», и `MARKET_PENZA.md:180` фиксирует: «в каноне такого шаблона нет».
Код такую услугу принимает: `SalonService.clean()` разрешает `template=NULL` при
заполненной `category`, и `confirm.py` держит для этого `fallback_category`.

**Existing canon.** Промпт: «НЕ менять каталог ради улучшения match rate».
`Domain Context Map:2051`: «provider offering должно ссылаться на canonical service».
Канон G6: `UNMAPPED` не значит «услуга плохая, запрещённая или не bookable».

**Why canon does not answer.** Канон запрещает **править каталог ради процента совпадений** —
это уже закрыто. Он **не отвечает**, может ли куратор **осознанно расширить** канон новой
канонической услугой, когда предложение салона реально не имеет канонического двойника.
Разница между «подогнать канон под данные» и «признать канон неполным» — продуктовая,
и от неё зависит, могут ли 58 услуг живого салона **когда-либо** стать рекомендуемыми.

**Option A — канон закрыт.** Список 1223 — полный перечень того, о чём Ayla умеет
рассуждать. Предложение без двойника остаётся `UNMAPPED` **навсегда**: доступно прямым
выбором и записью, но никогда не участвует в подборе и не наследует смысловых свойств.

**Option B — канон расширяем контролируемо.** Существует явная процедура: куратор
(не LLM, не matcher, не импорт) заводит каноническую услугу, и предложение становится
пригодным к сопоставлению. Расширение — отдельный акт со своим провенансом, запрещённый
как побочный эффект импорта или сопоставления.

**Consequence A.** Канон остаётся неподвижным и полностью контролируемым. Цена: для
`formula-tela` подбор **не загорится никогда**, независимо от любой разметки, — и это
надо сказать вслух, потому что сегодняшний ноль читается как «ещё не разметили».

**Consequence B.** У живого салона появляется путь. Цена: канон начинает расти от данных
арендаторов, и нужен сторож, отделяющий «признали пробел» от «подогнали под совпадение», —
иначе через полгода канон станет объединением прайсов.

**Blocks.** Всю работу по `formula-tela`; определение знаменателя «сколько связей вообще
возможно»; проектирование очереди проверки (в варианте A очередь для таких строк пуста
по правилу, в варианте B — содержит заявки на расширение канона).

---

### OD-CSM-3 — Является ли подтверждение импортного драфта подтверждением СВЯЗИ

**Runtime evidence.**
`services/integrations/intake/confirm.py:confirm_draft()` — единственный существующий
human-in-the-loop путь. Он ставит `status=CONFIRMED`, `confirmed_at`, `confirmed_by`
**на драфт** и **ничего не пишет в `mapping_*` материализованной `SalonService`** — та
рождается с умолчанием `unmapped`. При этом `users/recommendation_source.py:203-205`
**уже готовит** для таких строк `human_confirmed_ref = "draft_confirmed:<id>"`,
а `_stages.py:378-383` подставляет это значение в `source_ref` свидетельства
`CAPABILITY_MAPPING` со `strength=CONFIRMED` — но только для строк со статусом `VERIFIED`,
которого никто не выставляет. Плюс: штатная команда зовёт `confirm_draft` **без `actor`**,
поэтому `confirmed_by` остаётся `NULL` даже когда команду запускал человек.

**Existing canon.** `VERIFIED` = «подтверждена человеком ЛИБО детерминированным правилом
с provenance». Запрещено считать скрытым `VERIFIED` в том числе «demo fixture так сказала».

**Why canon does not answer.** Канон говорит, что **человек** может подтвердить, но не
говорит, **какой именно акт человека** этим является. Оператор, подтверждающий драфт
импорта, отвечает на вопрос «эта услуга у салона есть и её можно продавать». Вопрос
«это предложение означает вот эту каноническую услугу» ему **не задавали** — формы для
него нет, и в проде `suggested_template` всегда пуст. Признать первый акт вторым или
потребовать отдельного акта — продуктово разные системы, и канон между ними не выбирает.

**Option A.** Подтверждение драфта с непустым `suggested_template` **является**
подтверждением связи: `confirm_draft` пишет `VERIFIED` с `mapping_confirmed_by=actor`
и `mapping_source_ref='draft:<id>'`. Существующая проводка `human_confirmed_ref` тогда верна.

**Option B.** Подтверждение связи — **отдельный акт** с отдельной поверхностью и отдельной
записью. Подтверждение драфта даёт максимум `REVIEW_REQUIRED`. Проводка
`human_confirmed_ref` в нынешнем виде обманчива и подлежит снятию или переименованию.

**Consequence A.** Первые `VERIFIED` появляются без новой поверхности, на уже существующем
пути. Цена: подтверждённой окажется связь, о которой оператора **не спрашивали**, —
и это неотличимо от подтверждения, которого он не давал; ровно тот класс, что «demo
fixture так сказала».

**Consequence B.** Подтверждение означает ровно то, что подтверждали. Цена: нужна
поверхность проверки, которой сегодня нет вообще (админка не имеет ни очереди, ни фильтра
по `mapping_status`, ни действий), — то есть до неё `VERIFIED` не появится ни у одной строки.

**Blocks.** Трек F (human review workflow); смысл `human_confirmed_ref`; первую строку
`VERIFIED` на контуре.

---

## 10. Что НЕ является решением владельца

Перечисляю то, что могло бы попасть в список ошибочно, и почему не попало:

* **Хранить `code` канонического каталога отдельной колонкой** — механика; **что считается
  идентичностью и что аннулирует подтверждение** — OD-CSM-1. Колонка следует за ответом.
* **Признак «`review_required` ⟺ template заполнен» после миграции не поддерживается**
  (H1) — обычная механика разовой миграции данных; лечится сторожем, инженерное.
* **`confirm.py` может обнулить `template`, не тронув статус** (§6.5) — дефект, не выбор:
  целевая семантика («связи нет ⇒ статус не может остаться прежним») уже задана каноном.
* **Отсутствие `mapping_status` в зеркале бота** — следует из ADR-0009 и из того, что
  `recommendation_eligible` объявлен `AUTHORITATIVE_DOMAIN` бэкенда Ayla
  (`ai-bot-platform@83ed56a:docs/specs/DECISION_READINESS_ENGINE_v1.0.md:352`).
* **Взаимоисключительность `confirmed_by` / `confirmed_rule` не сторожится** (H5) —
  правило уже сформулировано владельцем в комментарии модели; нужен сторож, не решение.
* **Судьба 35 строк `mkt-*`** — владелец **уже высказался** («грузи, потом снесём»,
  `MARKET_PENZA.md:174`). Открыт только срок; это вопрос очерёдности к главному окну.
* **Отсутствие `actor` в `intake_confirm`** — дефект; личность подтверждающего теряется
  при любом ответе на OD-CSM-3.
* **Непереносимый `matched_by` в C6-линковке** (§6.4) — провенанс уже требуется каноном;
  где его хранить — инженерное.
* **Название модели, индекс, размер пачки, конкретная management-команда** — по §28 промпта.
* **Безопасность.** Установленное в §11 U-строках и в §3.2 — вход для Safety Architecture v1
  как отдельной канонической власти, а не решение по каталогу. Здесь не проектирую.

---

## 11. Что не замерено — честно

| # | предмет | почему | класс |
|---|---|---|---|
| U1 | Свежие числа контура (`SalonService total`, распределение, `suggested_template populated`) | у окна-исполнителя нет доступа к `dev-web-1` (EXECUTOR-RULES: замеры делает главное окно). Использованы числа реестра от 08.09 с явной датой | `UNKNOWN_NOT_REMEASURED` |
| U2 | Сквозная проверка одного предложения `строка БД → payload → зеркало → вход резолвера` по ключу услуги | требует живого контура; **именно там однажды нашлось «по 31 с обеих сторон, пересечение 0»** | `UNKNOWN_NOT_MEASURED` |
| U3 | Механизм, поставивший 35 связей `mkt-*` 23.08 (нормализация кавычек) | скрипта нет ни в одном дереве; провенанс не сохранён ни в одной строке БД | `UNKNOWN_NOT_MEASURED` |
| U4 | Применены ли `0016`/`0017` на **каждом** контуре, кроме `dev` | `django_migrations` не читан | `UNKNOWN_NOT_MEASURED` |
| U5 | Треки D, E (полностью), F, G, H, I, J, L, M, N, O | **не мой трек** — группы B и C | вне предмета |
| U6 | `Ayla/docs` цитировался из рабочей копии `b3c782b` с 60 изменёнными строками | `MARKET_PENZA.md`, `ANALYSIS_PACKAGES.md`, `ANALYSIS_SERVICE_MATCHING.md` прочитаны с диска; их «чистота» относительно коммита не проверена | оговорка к цитатам |

**`UNKNOWN` здесь ни разу не превращён в `MISSING`.**

---

## 12. Точные команды воспроизведения

```bash
cd /c/Users/user/PycharmProjects/Ayla

# --- базы замера --------------------------------------------------------
for d in ai-bot-platform djangoproject-catalog ayla-ai-core ayla-knowledge; do
  git -C $d fetch --all -q
  for b in origin/dev origin/main origin/master; do
    git -C $d rev-parse "$b" 2>/dev/null && echo "  ^ $d $b"
  done
done
git -C djangoproject-catalog rev-list --count origin/master..origin/dev   # 694
git -C docs rev-parse HEAD                                                # b3c782b (60 dirty)

# --- TRACK A: сущностей CanonicalService / Capability нет ----------------
grep -rn "CanonicalService\|canonical_service" --include=*.py djangoproject-catalog
git -C ai-bot-platform grep -n -i "canonicalservice\|canonical_service" origin/dev -- '*.py'
git -C ayla-ai-core   grep -n -i "canonicalservice\|capabilit" origin/main -- '*.py'   # пусто
grep -rn "capability_refs\|canonical_service_refs" --include=*.py djangoproject-catalog

# --- TRACK B: полная перепись полей связи (135 вхождений) ---------------
grep -rn "mapping_status\|mapping_confirmed\|mapping_rule_version\|mapping_source_ref" \
  --include=*.py djangoproject-catalog | grep -v __pycache__
# ни одного писателя вне migrations/ и tests/:
grep -rn "mapping_status *=" --include=*.py djangoproject-catalog \
  | grep -v "tests/\|migrations/\|models.py"
# бот о статусе не знает вовсе (ноль строк):
git -C ai-bot-platform grep -n "mapping_status\|MappingStatus" origin/dev -- '*.py'

# --- миграции построчно --------------------------------------------------
cat djangoproject-catalog/services/migrations/0016_salonservice_mapping_status.py
cat djangoproject-catalog/services/migrations/0017_mapping_status_backfill.py
cat djangoproject-catalog/services/migrations/_mapping_status_backfill.py

# --- TRACK C: 171 из фикстуры, построчно по салонам ----------------------
cd djangoproject-catalog && python - <<'PY'
import json
d = json.load(open('services/seeds/demo_salons_2026-08.json', encoding='utf-8'))
for s in d['salons']:
    print(s['slug'], len(s['services']))
print('total', sum(len(s['services']) for s in d['salons']))     # 171
PY
sed -n '400,420p' services/management/commands/seed_demo_salons.py
git log --format='%H %ci %s' -- services/seeds/demo_salons_2026-08.json   # одна версия

# --- канонический код, теряющийся при загрузке ---------------------------
python - <<'PY'
import json
d = json.load(open('services/seeds/canonical_catalog_2026-07.json', encoding='utf-8'))
print(len(d), 'строк;', len({r['code'] for r in d}), 'уникальных кодов')  # 1223 / 1223
print(sum(1 for r in d if str(r['requires_health_check']).lower() == 'true'), 'с health-check')  # 102
PY
grep -n "ServiceTemplate.objects.update_or_create" -A 14 \
  services/management/commands/seed_canonical_catalog.py     # 'code' отсутствует

# --- TRACK K: чего нет на границе ---------------------------------------
grep -n "class SalonServiceInternalSerializer" -A 25 services/serializers.py  # нет mapping_*
cd .. && git -C ai-bot-platform show origin/dev:apps/catalog/services/upserter.py \
  | sed -n '123,150p'                                        # _service_fields — восемь полей
git -C ai-bot-platform show origin/dev:apps/integrations/ayla/salon_surface.py \
  | grep -n "SALON_ROUTES" -A 200 | grep -ci "catalog"       # 0 — записи в каталог нет
git -C ai-bot-platform show origin/dev:apps/catalog/services/linking.py \
  | grep -n "update_fields"                                  # matched_by не сохраняется

# --- единственный путь, способный обнулить связь -------------------------
sed -n '63,95p' djangoproject-catalog/services/integrations/intake/confirm.py
sed -n '55,70p' djangoproject-catalog/services/management/commands/intake_confirm.py  # PENDING only
```

---

**Стоп после замера.** В G2 не вмешивался, Goal/Plan не проектировал, статусов не менял,
Linear не трогал, ничего не коммитил, worktree не заводил, временных файлов не оставил.
