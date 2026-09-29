# G6 / группа C — канонический слой, потребитель-рекомендация, WHY, реальность тестов

**Режим:** MEASURE-FIRST. Кода не писал, статусов не менял, Linear не трогал, worktree не заводил, ничего не коммитил. После замера STOP.

**Треки:** H (canonical service / capability) · I (recommendation consumer) · J (WHY / evidence) · O (test reality) + шов безопасности.

**Не мой предмет и не измерялось мной:** доменная модель и поля связи, сиды, синхронизация (группа A); матчер, `suggested_template`, ручная проверка, инвалидация, жизненный цикл (группа B); живой образец `formula-tela`, матрица авторитетов, сверка с контрактами (главное окно).

---

## 1. Базы замера

| репозиторий | каноническая ветка | SHA | дата головы | как установлена |
|---|---|---|---|---|
| `djangoproject` (beautygo_backend) — каталог + резолвер | `origin/dev` | `95c917e684652476feef3ae9d790fb2c8d277378` | 2026-09-09T06:50:27+03:00 | `git branch -r` даёт `origin/dev`, `origin/main`, `origin/master`; рабочий worktree `djangoproject-catalog` стоит на `dev` ровно этим SHA |
| `ai-bot-platform` — бот, зеркало, Mini App | `origin/dev` | `83ed56a94eb0a96d9599c632f0e6ba2d48c6c5b7` | 2026-09-09T10:52:48+03:00 | единственная не-feature ветка на `origin`; `main`/`master` не существуют |
| `ayla-ai-core` | `origin/main` | `d72a5de451f985d118d9449d2b17ce51bf0a6e25` | 2026-09-03T04:50:47+03:00 | `git branch -r` → только `origin/HEAD -> origin/main` и `origin/main`. **`origin/dev` не существует** — подтверждено, не предположено |
| `ayla-knowledge` | `origin/main` | `207eeb638580e529aaff66e0d1412aa6b559c0a3` | 2026-09-08T21:04:18+03:00 | `origin/HEAD -> origin/main` |

**Чтение источника.** Локальный чекаут `ai-bot-platform` стоит на `feat/recommendation-boundary-client` (`fd6f4e87`, 3 вперёд / **43 назад** от `origin/dev`) — как истину я его не грепал. Все утверждения про бота сняты через `git show origin/dev:<path>` и `git grep origin/dev`. Worktree `djangoproject-catalog` стоит ровно на `origin/dev`, рабочее дерево чистое (только неотслеживаемые `.claude/`, `AGENTS.md`).

**Живой контур не переснимался.** У этого окна нет доступа к `ssh taximeter@176.119.159.141` (`docs/PILOT_MEASUREMENTS.md`: замеры делает главное окно). Числа §1.1–1.2 (`review_required 206 · unmapped 59 · verified 0`, снято **08.09.2026 16:40 MSK**, контейнер `dev-web-1`) взяты как исторический базис с явной датой — **`UNKNOWN_NOT_REMEASURED` на сегодня**.

---

## 2. Сводка по классам

| класс | число | что это |
|---|---|---|
| `EXISTS` | 14 | гейт VERIFIED-only; чтение статуса полем; fail-closed при неизвестном значении; независимость прямого выбора; двусторонний запрет строк показа; «нет WHY → нет блока»; закрытый реестр кодов; структурный запрет рейтинга без отзывов; схемный CheckConstraint на provenance; красные сторожа на `REVIEW_REQUIRED` (два уровня); положительная стража `VERIFIED`; отсутствие `MODEL_INFERENCE` в происхождении; fail-closed `NULL` health-check на боте; снятая настройка-обход |
| `PARTIAL` | 5 | `ServiceTemplate` как канонический слой; provenance записан, но не читается; `capability_refs`/`canonical_service_refs` принимаются и никуда не идут; `source_ref` свидетельства берётся не оттуда; тесты покрывают один репозиторий |
| `MISSING` | 5 | `CanonicalService` как сущность; `Capability` как runtime-сущность; `mapping_status` на границе синхронизации; cross-boundary тест; читатель колонок provenance |
| `CONTRADICTS_CANON` | 3 | `ELIG_CAPABILITY_VERIFIED` → фраза на экране из одного статуса; поверхность бота ранжирует мимо гейта; safety-семантика наследуется без учёта статуса связи |
| `DEAD_CODE` / `UNREACHABLE` | 2 | `MatchLevel.CAPABILITY_ONLY` / `MATCH_CAPABILITY_ONLY`; `resolve_recommendation` на стороне бота |
| `TERMINOLOGY_COLLISION` | 2 | `template` совмещает три роли; «Capability» в `ayla-knowledge` — другой предмет |
| `UNKNOWN_NOT_MEASURED` | 7 | см. §12 |

---

## 3. TRACK H — канонический слой

### H1. `CanonicalService` как сущность — `MISSING`

Грепом по `djangoproject-catalog` (`origin/dev` @ `95c917e6`) имя `CanonicalService` встречается **только** как поле запроса резолвера (`recommendation/_serializers.py:74`, `recommendation/_types.py:282`) и как имя аннотации счётчика (`services/catalog_reads.py:346`). Модели с таким именем нет ни в одном репозитории.

**Роль исполняет `services.models.ServiceTemplate`** (`services/models.py:78`) — и это доказывается поведением и связями, а не именем:

* `SalonService.template` — FK на `ServiceTemplate` (`models.py:371`), и именно наличие/отсутствие этой связи бэкфилл превращает в `REVIEW_REQUIRED`/`UNMAPPED` (`services/migrations/_mapping_status_backfill.py`; сторожит `test_link_to_template_becomes_review_required_never_verified`);
* `SpecialistService.resolved_requires_health_check()` (`models.py:574`) читает `salon.template.requires_health_check` как **канонический пол** безопасности;
* уникальность `("tenant", "template", "name")` (`models.py:432`) — шаблон участвует в идентичности предложения тенанта.

### H2. `ServiceTemplate` как канонический слой — `PARTIAL`

Есть: category ancestry (FK на `ServiceCategory`), `name` / `name_short`, `duration_default/min/max`, `requires_health_check`, `contraindications`, `is_popular`, `sort_order`, `created_at/updated_at`.

Нет: aliases; связи с capability; версии; канонического внешнего идентификатора; таксономии глубже одного FK на категорию. **Slug отсутствует намеренно** — `services/serializers.py:214` фиксирует контракт C6: «ServiceTemplate intentionally has NO slug field (no migration for matching's sake in the pilot)».

Докстринг самого класса (`models.py:79`) описывает его как «предустановленный шаблон услуги… чтобы предложить готовый список популярных услуг вместо пустой формы» — то есть **онбординговый прайс-лист**. Это `TERMINOLOGY_COLLISION`: одна таблица одновременно (1) UX-подсказка онбординга, (2) каноническая семантическая идентичность, (3) носитель health/safety-атрибутов. Три роли, один жизненный цикл, одно право на изменение.

### H3. `Capability` как runtime-сущность — `MISSING`

Греп `capability|capabilities` по `djangoproject-catalog` даёт **ноль** доменных сущностей: только поле запроса, имена кодов (`ELIG_CAPABILITY_VERIFIED`, `MATCH_CAPABILITY_ONLY`, `EvidenceKind.CAPABILITY_MAPPING`) и два несвязанных совпадения в `payments/views.py`, `users/permissions.py`.

Ближайшее, что реально работает — курируемая связь «цель → категории»: `services.models.GoalOption` / `GoalOptionCategory` (`models.py:834`, `:871`), подключаемая через `goals/wiring.py::goal_category_ids_for_key`. Гранулярность — **категория, не услуга**; владелец связи — человек-куратор. Это не `Capability` канона (что услуга способна обеспечить), а «какие категории отвечают цели».

**`MatchLevel.CAPABILITY_ONLY` и `ReasonCode.MATCH_CAPABILITY_ONLY` — `DEAD_CODE` / `UNREACHABLE`.** Единственный производственный источник фактов `users/recommendation_source.py::SpecialistCandidateSource._match` возвращает ровно четыре уровня: `SERVICE_EXACT`, `SERVICE_PARTIAL`, `GOAL_CATEGORY`, `UNDETERMINED`. `CAPABILITY_ONLY` не производит никто; ранг (`_types.py:136`) и отображение в код (`_types.py:145`) для него объявлены, фраза на экране написана (`customer-booking.ts`: «Делает такие услуги») — и не может быть показана.

### H4. `capability_refs` / `canonical_service_refs` в запросе — `PARTIAL`, с ловушкой

`recommendation/_serializers.py:73–74` принимает оба списка UUID; `_types.py:281–282` их хранит. **Единственный их потребитель — `NeedSpec.is_stated`** (`_types.py:293`). Ни одна стадия и ни один источник их не читает.

Последствие достижимо через ручку `POST /api/v1/internal/recommendation/resolve/`: запрос только с `capability_refs` делает нужду «названной», после чего S1 (`_stages.py`, ветка `need_is_stated and _effective_match_level(facts) is MatchLevel.UNDETERMINED`) исключает **каждого** кандидата с кодом `ELIG_EXCLUDED_NOT_CAPABLE`. Гейт связи при этом даже не спрашивается. Наружу это читается как «никто не умеет», а причина — «этим измерением мы не умеем мерить».

### H5. `ayla-knowledge` — `DECLARED_ONLY` + `TERMINOLOGY_COLLISION`

`00 Foundation/Ayla Domain Capability Registry.md` (`origin/main` @ `207eeb63`, v1.2, `status: draft`, `decision_status: proposed`) описывает **бизнес-способности продукта** («понимание намерения», «формирование объяснимых рекомендаций», «поиск услуг и специалистов»), явно оговаривая: «не описывает конкретную реализацию, сервис, модуль, API, таблицу базы данных». Это другой предмет, чем `Capability` канона G6. Реестра service-level capability нет нигде.

### H6. `ayla-ai-core` в семантике маппинга не участвует — измерено

`git grep -i "mapping_status|canonical_service|CanonicalService|capability|ServiceTemplate|suggested_template" origin/main -- '*.py'` на `d72a5de4` — **ноль файлов**. Каноническая ветка установлена по следам (`origin/dev` в этом репозитории не существует), поэтому это `MISSING`, а не `UNKNOWN`.

---

## 4. TRACK I — потребитель-рекомендация и гейт

### I1. Где стоит гейт — `EXISTS`

`djangoproject-catalog/recommendation/_stages.py:371` — `_mapping_admission(facts, policy)`:

```
if facts.mapping_status is MappingStatus.VERIFIED:
    return True, EvidenceItem(kind=CAPABILITY_MAPPING, value=VERIFIED,
                              strength=CONFIRMED, origin=CURATED_KNOWLEDGE,
                              source_ref=facts.source_ref)
return False, None
```

Второй ветки нет — докстринг говорит прямо, что она была и удалена. Вызывается из `stage_eligibility` (S1); отказ даёт `ELIG_EXCLUDED_NOT_RECOMMENDABLE`.

### I2. Читает ли он статус — `EXISTS`, статус прочитанный, а не выведенный

`users/recommendation_source.py::_MappingFacts.status()` берёт `SalonService.mapping_status` **полем**. Синтез «есть шаблон → REVIEW_REQUIRED» из кода удалён (докстринг модуля, строки 45–52); сторожит `test_status_is_read_from_the_field_not_inferred_from_a_template`.

Переход через границу словарей сделан правильно: домен пишет `"verified"` (нижний регистр, `models.py:349–351`), контракт объявляет `VERIFIED` (верхний, `_types.py:109–112`), перевод — `_MappingFacts._as_status`:

```
try:    return MappingStatus(raw.upper())
except ValueError: return MappingStatus.UNMAPPED
```

Незнакомое значение читается как `UNMAPPED`, не как допуск. `EXISTS`, fail-closed.

Дополнительно: статус берётся **у совпавшей услуги**, а не лучший по мастеру (`status(matched_service_ref=…)`), иначе мастер проходил бы по проверенной связи услуги Б в ответ на вопрос про услугу А. Сторожит `test_status_belongs_to_the_matched_service_not_to_the_best_one`.

### I3. `REVIEW_REQUIRED` и `UNMAPPED` — `EXISTS`, оба выбывают

Оба дают `eligible=False` → `ExcludedCandidate(..., StageId.S1, ELIG_EXCLUDED_NOT_RECOMMENDABLE)`. Четвёртое состояние `UNKNOWN` («предложений нет вовсе») ведёт себя так же, но по отдельной причине и видно в переписи. Обхода нет: настройка `RECOMMENDATION_PILOT_MAPPING_OVERRIDE` из production-кода **удалена** (греп оставляет её только в тестах), а `recommendation/tests/test_boundary_guards.py:324–325` держит имена `RECOMMENDATION_PILOT_MAPPING_OVERRIDE` и `mapping_override_enabled` в списке запрещённых к возвращению.

### I4. `UNMAPPED` ≠ «услуги нет / плохая / не bookable» — `EXISTS` в коде

`mapping_status` читается ровно в **одном** производственном месте — `users/recommendation_source.py`. Грепом по всему `djangoproject-catalog` вне `recommendation/`, `users/recommendation_source.py`, `services/models.py` и миграций остаются только тесты.

Следствия, проверенные по коду:

* `services/catalog_reads.py::catalog_services_for` (строка 295) — общий читатель каталога для брони и витрины — **не фильтрует по статусу**;
* пул кандидатов `_pool()` строится по `is_available / is_booking_enabled / status ACTIVE / tenant active` — доменная допустимость, не политика;
* полка 3 «что вообще есть» (`users/catalog_recommendations_api.py::_build_layer_3`) считается по видимому каталогу и **живёт при нуле допущенных** — тест `test_only_verified_reaches_the_shelf` явно утверждает `data["layer_3_explore"]["categories"]` непустым;
* эмиттеры синхронизации (`services/serializers.py`) отдают услугу в зеркало независимо от статуса.

То есть прямой выбор и запись на `UNMAPPED`-услугу работают; ограничен только семантический подбор. Это и есть `catalog_visible ≠ recommendation_eligible`.

### I5. `NO_VERIFIED_CANDIDATES` при `verified=0` — **правильный fail-closed, не дефект**

При нуле подтверждённых S1 не пропускает никого, `ordered` пуст, в `excluded` — `ELIG_EXCLUDED_NOT_RECOMMENDABLE`, и полка несёт этот код наружу (`_shelf`, `catalog_recommendations_api.py:250`), чтобы потребитель сказал «нет подтверждённых», а не «никто не подошёл». Перепись (`MappingCensus`) считает **увиденных, а не выживших** (`_stages.py:266`, комментарий объясняет зачем) и уходит в лог, не в ответ. Классификация: `EXISTS`, соответствует §4 канона. **Чинить ослаблением гейта нечего.**

### I6. Расхождение поверхностей — `CONTRADICTS_CANON`

Гейт VERIFIED-only стоит **только на пути «Mini App → каталог»**. Живой путь MAX-бота его не проходит.

`ai-bot-platform` @ `83ed56a9`, `tests/contracts/test_recommendation_boundary_guard.py`, словарь `_ALLOWED`:

```
"apps/marketplace/discovery.py":  "DRF-1573 (T12): бот зовёт resolve() вместо своего порядка",
"apps/orchestrator/handoff.py":   "DRF-1573 (T12): та же миграция, вторая поверхность бота",
```

Комментарий над первой строкой: «Авторитет A. **Настоящая точка решения бота**; становится потребителем в T12». Миграция не сделана. `apps/marketplace/discovery.py` читает зеркало (`CatalogMaster.all_tenants`, `CatalogService`), режет запрос на токены со стеммингом до 6 символов и **ранжирует суммой совпавших токенов** (комментарий DRF-1283, живой случай 23.08 «покажи массажистов в пензе»). Вызывается из `apps/channels/max/handler.py`, `apps/orchestrator/discovery.py`, `apps/channels/max/global_onboarding.py`.

**Статуса связи там нет физически** (см. I7), поэтому гейт не «обойдён по недосмотру» — его нечем исполнить.

### I7. `mapping_status` не пересекает границу синхронизации — `MISSING`

* Эмиттеры каталога: `services/serializers.py::SalonServiceInternalSerializer.Meta.fields` (строки 182–187) и `SpecialistServiceInternalSerializer.Meta.fields` (247–255). Обе отдают `template` (сам факт канонической связи) и `resolved_requires_health_check` (её safety-следствие). **`mapping_status` не отдаёт ни одна.**
* Приёмник: `ai-bot-platform/apps/catalog/models.py` — `CatalogService` (slug, name, описания, `price_from`, `duration_min`, `is_active`, `is_popular`, seo-поля, `goals`, `requires_health_check`, `contraindications`, `raw`, `ayla_service_id`) и `MasterService` (…, `ayla_specialist_service_id`, `resolved_requires_health_check`). Поля статуса нет.
* Проверено грепом `mapping_status` по `origin/dev`: 4 файла, из них 3 теста и один клиент границы. В моделях, синхронизаторе (`apps/catalog/services/upserter.py`) и HTTP-клиенте (`apps/catalog/services/http_client.py`) — ни одного упоминания.

Граница переносит **связь и её последствия, но не признак доказанности связи**.

### I8. `resolve_recommendation` на стороне бота — `DEAD_CODE` сегодня

`apps/integrations/ayla/recommendation_resolver_client.py::resolve_recommendation` (строка 86) написан аккуратно: три различимых исхода, `try` накрывает ровно транспорт, проверка формы стоит после него, конформность целостная. **Производственных вызывающих нет**: `git grep resolve_recommendation origin/dev` даёт определение, свои тесты и упоминания в докстрингах/фронте. Живой путь Mini App — `apps/miniapp_api/views.py::customer_recommendations` (строка 2522) → `recommendations_client.fetch_recommendations` → полка каталога `POST /internal/me/catalog/recommendations/`. Гейт при этом соблюдается — но потому, что его исполняет каталог, а не потому, что бот его зовёт.

### I9. `safety_state` — точка интеграции, не мой предмет

Ручка `POST .../recommendation/resolve/` требует `safety_state` полем запроса (`_serializers.py:104`). `_is_safety_sensitive` (`_stages.py:346`) опровергает содержанием решения **только `NOT_APPLICABLE`**; ложно заявленный `NORMAL` не опровергается ничем. Полка (`catalog_recommendations_api.py`) поля безопасности не имеет вовсе — состояние определяется типом поверхности, и докстринг называет запрещённый вид кода поимённо. Фиксирую как точку интеграции; политику не создаю.

---

## 5. TRACK J — WHY и происхождение

### J1. Как WHY собирается сегодня

```
SalonService.mapping_status (домен)
      ↓  users/recommendation_source.py::_MappingFacts.status()
CandidateFacts.mapping_status
      ↓  recommendation/_stages.py::_mapping_admission
ReasonCode.ELIG_CAPABILITY_VERIFIED  +  EvidenceItem(CAPABILITY_MAPPING, …)
      ↓  users/catalog_recommendations_api.py::_project / _evidence_payload
{"reason_codes": [...], "evidence": [{kind, strength, origin, value, source_ref}]}
      ↓  бот: apps/miniapp_api/views.py — транзит, без изменения
      ↓  apps/miniapp/src/lib/customer-booking.ts::displayableReasons + REASON_PHRASES
      ↓  apps/miniapp/src/screens/CustomerCatalogScreen.tsx — блок «✨ Ayla подобрала»
```

### J2. Что сделано правильно — `EXISTS`

* **Строки для показа с сервера не уходят.** `recommendation/_serializers.py:40`: `FORBIDDEN_RESPONSE_FIELDS = {"reasoning_text","reason_text","why_text","score","match_score"}`, проверяется тестом W1. Зеркальная проверка на клиенте: `apps/miniapp/src/lib/api.ts:460` — «ответ несёт строку для показа человеку — граница отдаёт reason_codes и evidence». Договор проверяют оба конца.
* **«Нет displayable WHY → нет блока» реализовано данными, а не флагом.** `customer-booking.ts:443` — `.filter((p) => p.reasons.length > 0)`; `CustomerCatalogScreen.tsx:200` — секция рендерится только при `picksWithWhy.length > 0`, с комментарием «Never render a stand-in WHY here».
* **Реестр кодов закрыт и версионирован.** `recommendation/_reason_codes.py`, `REGISTRY_VERSION = "1.0.0"`, версия уезжает в `policy_versions` ответа. Код вне реестра фразы не даёт и нарушением формы не считается.
* **Рейтинг структурно не может стать WHY.** `EvidenceItem.__post_init__` не собирается без `RatingValue(rating, review_count)`; `N_SUBSTANTIATED = None`, значит `CONFIRMED` для рейтинга не выдаётся вовсе; `QUALITY_RATING_SUBSTANTIATED` в `REASON_PHRASES` = `null`. `EvidenceOrigin` **не имеет** члена `MODEL_INFERENCE` — и докстринг говорит, что не появится.

### J3. `ELIG_CAPABILITY_VERIFIED` → фраза на экране — `CONTRADICTS_CANON`

`apps/miniapp/src/lib/customer-booking.ts`, таблица `REASON_PHRASES`:

```
ELIG_CAPABILITY_VERIFIED: "Делает именно это",
```

Этот код выдаётся в `_stages.py` строкой `granted.add(ReasonCode.ELIG_CAPABILITY_VERIFIED)` **сразу после успеха `_mapping_admission`**, то есть его единственное основание — `mapping_status == VERIFIED`. Канон §19: «Не считать `mapping_status` сам по себе explanation».

Два различимых следствия, оба достижимы:

1. **Нужда не названа.** Тогда `matched_service_ref is None`, статус берётся как лучший среди предложений мастера (`_MappingFacts.status()`, ветка «предмет — сам мастер»), кандидат живёт (`test_unresolved_provider_stays_when_the_need_was_never_stated`), и фраза «Делает **именно это**» указывает на «это», которого в запросе не было. Утверждение о совпадении, сделанное там, где совпадение не вычислялось.
2. **Фраза — утверждение о способности**, а за ней стоит значение статуса. Имя поля свидетельства не создаёт; здесь свидетельство создаёт **значение** поля — тот же приём.

Классификация показанного WHY по §19: **`UNSUPPORTED`** для случая 1; **`DERIVED_FROM_GROUNDED`** для случая 2 при условии, что за `VERIFIED` действительно предъявлено происхождение — а это условие сейчас не читается (J4).

### J4. Provenance записан, но до WHY не доезжает — `PARTIAL`, читатель `MISSING`

Схема требует provenance у `VERIFIED` **на уровне БД**, а не по договорённости — `services/models.py:442–456`, `CheckConstraint("salonservice_verified_requires_provenance")`: `verified` ⇒ `mapping_confirmed_at IS NOT NULL` И `mapping_source_ref <> ''` И (`mapping_confirmed_by IS NOT NULL` ИЛИ `mapping_confirmed_rule <> ''`). Второй констрейнт требует `mapping_rule_version` при подтверждении правилом. Проверка стоит в констрейнте намеренно — комментарий: «`clean()` обходится любым `update()` и любой миграцией данных».

**И ни одна из этих колонок не читается в production.** Греп `mapping_source_ref|mapping_confirmed_by|mapping_confirmed_rule|mapping_rule_version|mapping_confirmed_at` по `djangoproject-catalog` вне тестов даёт только определение модели и миграцию `0016`. В админке они тоже не выведены (греп `mapping` по всем `admin.py` — пусто).

Вместо них в свидетельство едет **другое** поле:

```
source_ref = facts.source_ref = _MappingFacts.human_confirmed_ref
```

а `human_confirmed_ref` ставится в `f"draft_confirmed:{salon.id}"` **только** если существует `DraftSalonService` со `status=CONFIRMED`, `confirmed_salon_service_id = salon.id` и непустым `suggested_template` (`recommendation_source.py::_mapping_by_specialist`). Во всех прочих случаях — `None`.

Итог: рекомендация может выйти наружу с

```
{"kind":"CAPABILITY_MAPPING","strength":"CONFIRMED","origin":"CURATED_KNOWLEDGE","value":"VERIFIED","source_ref":null}
```

и фразой «Делает именно это» — при том, что настоящее происхождение лежит в соседней колонке той же строки и по конституции схемы обязано быть непустым. Свидетельство объявляет силу `CONFIRMED`, не предъявляя того, чем подтверждено.

Тест `test_human_confirmed_draft_still_does_not_grant_the_status` сторожит обратное направление (драфт не даёт статуса). Что у `CONFIRMED`-свидетельства есть `source_ref`, не сторожит никто.

### J5. Права LLM — что нарушается

| право канона §6 | измерено | класс |
|---|---|---|
| LLM сам ставит `VERIFIED` | писателей `mapping_status` / provenance вне миграций, тестов и админки нет вообще; LLM-путей к ним нет | не нарушается |
| LLM — источник истины связи | связь = FK `SalonService.template`, пишется человеком/сидом; резолвер ORM не трогает вовсе (порт `CandidateSource`) | не нарушается |
| LLM выдумывает `Capability` / `CanonicalService` | выдумывать нечего — runtime-сущностей нет. Соблюдение **по отсутствию предмета, а не по контролю** | не нарушается, но не защищено |
| LLM выводит health/safety из названия | не найдено. `requires_health_check` всегда булево поле из шаблона/салона/мастера; бот читает `MasterService.resolved_requires_health_check` (`apps/skills/booking/skill.py:1392`), `NULL` → «скрининг нужен» (fail-closed); текст `contraindications` логируется, но **не толкуется** (`skill.py:45`, `:1045`) | не нарушается |
| similarity → авторитетная связь | **нарушается по существу, но не LLM.** Два места: (а) `apps/marketplace/discovery.py` — порядок кандидатов человеку строится суммой совпавших токенов после стемминга до 6 символов; (б) контракт C6, записанный в `services/serializers.py:214` — бот связывает свои строки с Ayla по `(category_slug, нормализованное имя)` с длительностью как тай-брейком. Межрепозиторная идентичность устанавливается нормализацией строк, без статуса и без provenance | `CONTRADICTS_CANON` |
| silent overwrite связи | не измерялось — предмет группы B/G | `UNKNOWN_NOT_MEASURED` |

---

## 6. TRACK O — реальность тестов

### Классификация найденного

| файл | класс | что доказывает |
|---|---|---|
| `recommendation/tests/test_pipeline.py` (503 стр., 27 тестов) | `UNIT_ONE_SIDE` | логику резолвера на `StaticSource` (`conftest.py:102`) — обе стороны данных построил один автор |
| `users/tests/test_recommendation_source.py` (502 стр., класс `TestMappingStatus` — 7 тестов) | `CONTRACT_ONE_FIXTURE`, через реальную ORM | перевод домен → контракт на настоящих строках БД |
| `users/tests/test_catalog_recommendations_99.py` (875 стр., `TestFailClosedStates`) | `CONTRACT_ONE_FIXTURE`, через настоящий HTTP | ORM → источник → резолвер → сериализатор внутри одного репозитория |
| `services/tests/test_mapping_status_provenance.py` (238 стр., 8 тестов) | схемный | констрейнты БД, а не договорённости |
| `recommendation/tests/test_boundary_guards.py` + `ai-bot-platform/tests/contracts/test_recommendation_boundary_guard.py` | сторож исходника | новое место ранжирования краснит билд сегодня |
| `apps/integrations/ayla/tests/test_recommendation_resolver_client.py`, `apps/marketplace/tests/test_resolver_keys.py`, `apps/miniapp_api/tests/test_recommendations.py`, `apps/miniapp/src/lib/customer-booking.test.ts` | `UNIT_ONE_SIDE` | форму ответа на **рукописном JSON, сочинённом на стороне бота** |
| `CROSS_BOUNDARY` | **отсутствует** | — |
| `LIVE_DATA`, `GOLDEN_MAPPING`, `ADMIN_WORKFLOW`, `INVALIDATION` | **отсутствуют** | — |

### O1. Тест, который краснеет, если `REVIEW_REQUIRED` станет `VERIFIED` — **есть, два, на разных уровнях**

1. `recommendation/tests/test_pipeline.py:215` — `test_nothing_but_verified_is_admitted_whatever_the_settings_say`, параметризован `[UNMAPPED, REVIEW_REQUIRED, UNKNOWN]`, с **нарочно включённым** `@override_settings(RECOMMENDATION_PILOT_MAPPING_OVERRIDE=True)`. Докстринг называет причину прямо: «`REVIEW_REQUIRED` здесь важнее остальных: именно его получат все 206 связей пилота». Настройка выставлена, чтобы доказать, что её не существует, — без этого тест доказывал бы «при выключенном флаге всё как раньше», то есть ничего.
2. `users/tests/test_catalog_recommendations_99.py:507` — `test_only_verified_reaches_the_shelf`, параметризован `[REVIEW_REQUIRED, UNMAPPED]`, идёт **через настоящую ручку на настоящих строках БД**, утверждает `shelf["items"] == []`, наличие `ELIG_EXCLUDED_NOT_RECOMMENDABLE` в кодах и отдельно — что полка 3 жива.

### O2. Положительная стража — есть

`test_verified_is_still_admitted` (`test_pipeline.py:242`), докстринг: «без неё три случая выше зеленели бы и на коде, который отвергает всех подряд». Правило «ноль обязан иметь положительную стражу» соблюдено. Он же единственный проверяет, что свидетельство `CAPABILITY_MAPPING` имеет `strength == CONFIRMED` — но **не проверяет `source_ref`** (J4).

### O3. Фикстуры, которые сами создают связь и потом её же проверяют

`services/tests/test_mapping_status_provenance.py` ставит `mapping_status=VERIFIED` руками и проверяет реакцию констрейнта. Для схемного теста это законно — предмет и есть констрейнт.

Отсюда следует названный пробел: **`VERIFIED` в тестах не возникает ни разу через производственный путь записи, потому что такого пути не существует.** Значит утверждение «`VERIFIED` допускается» доказано только на строке, выставленной рукой теста. Что произойдёт с реально подтверждённой связью, не доказано ничем.

### O4. Cross-boundary — `MISSING`, тот же класс, что стоил 31 кандидата

Ни один тест не строит `SalonService` в каталоге, не прогоняет его настоящей синхронизацией в зеркало бота и не проверяет, что делает с ним поверхность бота. Тесты бота кормятся JSON, написанным автором тестов бота; тесты каталога — строками, написанными автором тестов каталога. Общий golden-файл `ai-bot-platform/tests/fixtures/contracts/recommendations.request.json` — **только запрос и только на стороне бота**.

Ровно эта форма (по 31 объекту с обеих сторон, пересечение ноль) уже один раз прошла все зелёные тесты. Сейчас на границе стоит поле, которого на той стороне нет вовсе (I7), — и ни один тест этого не видит.

---

## 7. Точки интеграции безопасности

Политику не создаю. Safety Architecture v1 — отдельный FINAL FREEZE. Фиксирую швы.

### S1. Safety-семантика наследуется через связь **без учёта статуса связи** — `CONTRADICTS_CANON`

`services/models.py:574`:

```
def resolved_requires_health_check(self) -> bool:
    template_floor = template.requires_health_check if template is not None else False
    return bool(template_floor or salon.requires_health_check or self.requires_health_check)
```

`mapping_status` здесь не спрашивается ни разу. Канон §26: «Если CanonicalService mapping не VERIFIED, нельзя наследовать safety semantics как доказанные».

Направление первого следствия безобидно: OR даёт только эскалацию, поэтому непроверенная связь может лишь **добавить** проверку здоровья.

**Второе следствие — опасное.** При `template is None` (то есть `UNMAPPED` — состояние всех 58 услуг `formula-tela` на 08.09) `template_floor` становится **`False`**, и наружу через `SpecialistServiceInternalSerializer.get_resolved_requires_health_check` уезжает `false`. На той стороне у `MasterService.resolved_requires_health_check` `NULL` означает «никогда не синхронизировали → скрининг нужен» (`apps/catalog/migrations/0012`, `help_text`), а `False` означает «решено: не нужен». То есть **отсутствие канонической связи превращается на границе в положительное утверждение «скрининг не требуется»** — ровно тот приём, который правила исполнителя называют «умолчание, равное осмысленному значению, делает „человек молчал" неотличимым от „человек выбрал"».

Третьего состояния у булева поля нет, а различие между «связи нет» и «связь есть и говорит: не нужно» существует и важно.

### S2. Текст противопоказаний едет в зеркало без статуса связи

`ServiceTemplate.contraindications` → `CatalogService.contraindications` (`apps/catalog/services/http_client.py:827`, `upserter.py:128`). Читается ботом только как «есть текст / нет текста» для лога хендоффа (`apps/skills/booking/skill.py:1353`), не толкуется. Пока безопасно; шов существует.

### S3. `safety_state` объявляется вызывающим на ручке резолвера

См. I9. Опровергается содержанием решения только `NOT_APPLICABLE`.

---

## 8. Подтверждённые противоречия

**C1. Гейт исполняется одной поверхностью из двух.**
RUNTIME: `POST /internal/me/catalog/recommendations/` (Mini App) — VERIFIED-only; `apps/marketplace/discovery.py` (MAX-бот, оркестратор) — собственный порядок по совпадению токенов, статуса связи не видит.
SPEC: контракт §2.1 C1 и OD §53 — порядок даёт резолвер, авторитет один.
CLASS: `PARALLEL_AUTHORITY` / `RUNTIME_BEHIND_CANON` (DRF-1573 / T12 объявлен, не сделан).
CONSEQUENCE: в день, когда связи подтвердят, поверхности разойдутся видимо — Mini App загорится, бот не изменится вовсе. Сегодня расхождение невидимо, потому что Mini App пуста.

**C2. Граница переносит связь и её safety-следствия, но не признак доказанности.**
RUNTIME: `template` и `resolved_requires_health_check` едут в зеркало, `mapping_status` — нет; в зеркале такого поля не существует.
SPEC: §10.1 — `recommendation_eligible = (mapping_status == VERIFIED)`; §26 — без VERIFIED safety-семантику наследовать нельзя.
CLASS: `TRUE_CONTRADICTION`.
CONSEQUENCE: потребитель за границей не может исполнить гейт, даже если захочет, и не может отличить «скрининг не нужен» от «канонической связи нет».

**C3. Отображаемое WHY производится из статуса, а его provenance не читается.**
RUNTIME: `ELIG_CAPABILITY_VERIFIED` → «Делает именно это»; `source_ref` свидетельства берётся из `DraftSalonService`, а не из `SalonService.mapping_source_ref`; колонки provenance не читает никто.
SPEC: §5 — «Displayable WHY требует independent provenance»; §19 — «Не считать `mapping_status` сам по себе explanation».
CLASS: `CONTRADICTS_CANON` + `PARTIAL`.
CONSEQUENCE: `strength=CONFIRMED` при `source_ref=null` — свидетельство, объявляющее силу без предъявления основания. Форма дефекта «Рейтинг 4.9», от которого этот же модуль защищается в другом месте.

**C4. `capability_refs` принимается ручкой и исключает всех.**
RUNTIME: делает нужду «названной», после чего S1 отсеивает каждого по `UNDETERMINED` кодом `ELIG_EXCLUDED_NOT_CAPABLE`.
SPEC: канон объявляет `Capability` уровнем модели.
CLASS: `RUNTIME_BEHIND_CANON` + ловушка формы.
CONSEQUENCE: вызывающий получает «никто не способен» вместо «этим измерением мы не умеем мерить». Имя состояния обвиняет каталог, причина у нас.

**C5. `template` совмещает три роли.**
RUNTIME: онбординговый прайс-лист + каноническая идентичность + носитель health/safety.
CLASS: `TERMINOLOGY_COLLISION`.
CONSEQUENCE: правка шаблона ради UX онбординга меняет каноническую семантику и safety-пол одновременно, и ни одна проверка этого не различит.

---

## 9. Опасности миграции

1. **Первый же `VERIFIED` включает фразу.** В момент подтверждения любой связи на экран уходит «Делает именно это» — со свидетельством `CONFIRMED / source_ref: null` для каждой строки, подтверждённой не через `DraftSalonService`. Разметка 206 связей = 206 показанных утверждений без предъявленного основания.
2. **Констрейнт доказывает наличие строки, не смысл.** `mapping_source_ref` — свободная строка любой непустоты. Массовая проставка `"migration"` пройдёт схему и создаст 206 «доказанных» связей.
3. **Статус может измениться без изменения связи.** Включение `GOAL_RESOLUTION_ENABLED` меняет, какая услуга станет `matched_service_ref` (`_match`, ветка `goal_categories`), а статус спрашивается **у совпавшей**. Другой ответ на тот же запрос при неизменных данных маппинга.
4. **Разметка не изменит поведение бота.** Пока `apps/marketplace/discovery.py` не мигрирован (DRF-1573), MAX-бот продолжит выдавать тот же порядок. Проверять эффект разметки по боту нельзя — он ничего не покажет.
5. **`UNMAPPED` уже сегодня уезжает как `resolved_requires_health_check: false`.** Любая миграция, добавляющая `template` услугам `formula-tela`, одновременно и молча изменит их safety-профиль в зеркале — а причина изменения будет выглядеть как «поправили маппинг».
6. **Единственный красный сторож на удалении настройки-обхода — тест по именам.** `test_boundary_guards.py:324` держит `RECOMMENDATION_PILOT_MAPPING_OVERRIDE` и `mapping_override_enabled` запрещёнными. Возвращение обхода под третьим именем этот сторож не поймает.

---

## 10. Решения владельца — `OD-CSM-N`

Рекомендаций не даю.

### OD-CSM-1 — Является ли поисковая выдача MAX-бота «обычной семантической рекомендацией» или «прямым выбором»?

**Runtime evidence.** `ai-bot-platform` @ `83ed56a9`, `apps/marketplace/discovery.py`: человек пишет «покажи массажистов в пензе», бот режет запрос на токены, стеммит до 6 символов, ищет ILIKE по названиям зеркала и **ранжирует суммой совпавших токенов**. Вызывается из `apps/channels/max/handler.py`, `apps/orchestrator/discovery.py`. Статуса связи в зеркале нет (I7). Файл внесён в `_ALLOWED` сторожа границы с пометкой «DRF-1573 (T12): бот зовёт `resolve()` вместо своего порядка».

**Existing canon.** §4: обычная AI semantic recommendation может использовать TenantOffer через семантику CanonicalService только при VERIFIED. Там же: `UNMAPPED` offer может оставаться доступным при явном direct service selection.

**Why canon does not answer.** Канон различает «семантическую рекомендацию» и «прямой выбор». Выдача бота — ни то, ни другое чисто: человек назвал предмет словами (признак прямого выбора), но система **упорядочила** результат по своей мере похожести и показала первым «лучшего» (признак рекомендации). Эту середину канон не разбирает.

**Option A.** Считать поиск по словам прямым выбором: бот показывает совпавшие по имени услуги в порядке, не выражающем превосходства (алфавит / порядок каталога), гейт не применяется.

**Option B.** Считать упорядоченную выдачу рекомендацией: бот мигрирует на `resolve()` (T12), при `verified=0` отвечает пустым честным «пока нечего подтверждённого», как Mini App.

**Consequence A.** MAX-бот продолжает работать на пилоте. Плата: система показывает первым кандидата по совпадению букв, и для человека это неотличимо от «Ayla считает его лучшим»; порядок остаётся вторым авторитетом.

**Consequence B.** Поиск в MAX-боте на пилоте перестаёт находить кого-либо до разметки связей. Плата: единственная работающая сегодня поверхность поиска гаснет.

**Blocks.** T12 / DRF-1573; смысл разметки 206 связей (при A разметка на бота не влияет вовсе); ответ на вопрос «что увидит человек в MAX после разметки».

---

### OD-CSM-2 — Что означает `resolved_requires_health_check = false`, когда канонической связи нет?

**Runtime evidence.** `services/models.py:574` — при `template is None` пол равен `False`; `services/serializers.py:261` отдаёт это булевым; в зеркале (`apps/catalog/migrations/0012`) `NULL` = «никогда не синхронизировали → скрининг нужен», `False` = «не нужен». На 08.09 все 58 услуг `formula-tela` — `unmapped`.

**Existing canon.** §26: без VERIFIED нельзя наследовать safety-семантику как доказанную. Правило дисциплины отсутствия: отсутствие доезжает отсутствием; у каждого пропуска должно быть имя.

**Why canon does not answer.** Канон запрещает наследовать неподтверждённое как доказанное. Он не говорит, чем **заменить** утверждение, когда наследовать нечего: третьего состояния у поля нет, а два наличных означают «не нужно» и «неизвестно», причём второе на границе уже занято под «не синхронизировали».

**Option A.** Отсутствие канонической связи выражается отсутствием: поле уезжает `null`, зеркало трактует как «скрининг требуется» (fail-closed).

**Option B.** Отсутствие связи остаётся `false`; ответственность за скрининг несут салонное и мастерское поля.

**Consequence A.** Каждая неразмеченная услуга становится медицински гейтированной до подтверждения связи — на `formula-tela` это все 58, то есть у единственного салона с настоящей интеграцией запись начинает требовать анкету. Зато «мы не знаем» перестаёт выглядеть как «мы проверили».

**Consequence B.** Запись работает как сегодня. Плата: услуга с настоящими противопоказаниями, у которой просто нет шаблона, проходит без скрининга, и след этого решения отсутствует.

**Blocks.** Любую массовую разметку (она молча изменит safety-профиль); формулировку safety-шва G6 ↔ Safety Architecture v1; вопрос, гейтить ли пилот `formula-tela`.

---

### OD-CSM-3 — Что человеку разрешено показывать как WHY, когда единственное основание — статус связи?

**Runtime evidence.** `_stages.py` выдаёт `ELIG_CAPABILITY_VERIFIED` из одного условия `mapping_status == VERIFIED`; `customer-booking.ts` переводит его в «Делает именно это»; при неназванной нужде за фразой нет совпавшей услуги вовсе. Настоящее происхождение (`mapping_confirmed_by` / `mapping_confirmed_rule` + `mapping_rule_version` / `mapping_source_ref`) констрейнтом БД обязано быть непустым и **не читается ни одной строкой production-кода**; в свидетельство едет `source_ref` из `DraftSalonService`, обычно `None`.

**Existing canon.** §5: отображаемое WHY требует независимого происхождения; имя поля свидетельства не создаёт; нет displayable WHY → нет блока. §19: `mapping_status` сам по себе не объяснение.

**Why canon does not answer.** Канон запрещает считать статус объяснением и требует происхождения. Он не говорит, считается ли **прочитанное происхождение подтверждённой связи** (кто / какое правило / когда / по какому основанию) достаточным основанием для фразы «делает именно это», или фраза о способности требует отдельного свидетельства о способности.

**Option A.** Статус остаётся допуском, но фразы не даёт: `ELIG_CAPABILITY_VERIFIED` получает `null` в таблице фраз, как `ELIG_SAFETY_CLEARED`. WHY на экране собирается только из семейств `MATCH_*` и `CONTEXT_*`.

**Option B.** Фраза сохраняется, но требует предъявленного происхождения: свидетельство `CAPABILITY_MAPPING` обязано нести непустой `source_ref` из колонок provenance, иначе кандидат идёт без этой фразы.

**Consequence A.** При неназванной нужде кандидат может остаться совсем без displayable WHY и выпасть из блока «Ayla подобрала» по действующему правилу — то есть подтверждённая связь сама по себе показа не даст.

**Consequence B.** Фраза остаётся и становится проверяемой; цена — «делает именно это» продолжает выходить при неназванной нужде, где «это» не определено.

**Blocks.** Что человек увидит в первый день после разметки; смысл `strength=CONFIRMED` в свидетельстве.

---

### OD-CSM-4 — Что делать с измерением `Capability`, которого нет?

**Runtime evidence.** `NeedSpec.capability_refs` и `canonical_service_refs` принимаются ручкой резолвера, доходят до `NeedSpec` и используются **только** в `is_stated`. `MatchLevel.CAPABILITY_ONLY` / `MATCH_CAPABILITY_ONLY` имеют ранг, отображение и готовую фразу «Делает такие услуги» — и ни одного производителя. Запрос только с `capability_refs` исключает всех кандидатов кодом `ELIG_EXCLUDED_NOT_CAPABLE`. В `ayla-knowledge` есть `Ayla Domain Capability Registry` v1.2 (`draft` / `proposed`), но он про бизнес-способности продукта и явно отказывается описывать таблицы и сервисы.

**Existing canon.** §2: не схлопывать уровни `Capability → CanonicalService → TenantOffer`. §17: если Capability отсутствует runtime — `MISSING`; если только в документах — `DECLARED_ONLY`.

**Why canon does not answer.** Канон говорит, что уровень нужен, и как его классифицировать. Он не говорит, что делать с **уже открытым интерфейсом** к несуществующему уровню: сегодня внешний вызывающий может им воспользоваться и получить ответ «никто не способен», который неверен по существу.

**Option A.** Закрыть измерение до появления слоя: ручка отвергает `capability_refs` / `canonical_service_refs` явной ошибкой «измерение не поддерживается».

**Option B.** Оставить приём, но исключить из `is_stated`: эти поля не делают нужду названной, пока их некому сопоставлять.

**Consequence A.** Вызывающий получает честный отказ вместо ложного «никто не способен». Плата: контракт §5 эти поля объявляет, и ручка начнёт отвергать объявленное.

**Consequence B.** Ничего не ломается, ложный ответ исчезает. Плата: поле принимается и не влияет ни на что — тихая пустота, отказ без имени.

**Blocks.** Проектирование слоя Capability; форму контракта резолвера v1.1.

---

## 11. Что решением владельца НЕ является

Инженерные решения при уже определённой семантике; выносить не нужно:

* прочитать `SalonService.mapping_source_ref` / `mapping_confirmed_by` / `mapping_confirmed_rule` в `source_ref` свидетельства вместо `DraftSalonService`;
* добавить `mapping_status` в `SalonServiceInternalSerializer` / `SpecialistServiceInternalSerializer` и колонку в зеркало (аддитивно, обратносовместимо);
* удалить или подключить `MatchLevel.CAPABILITY_ONLY`;
* добавить тест, требующий непустой `source_ref` у `CAPABILITY_MAPPING` со `strength=CONFIRMED`;
* имена таблиц, классов, индексов, номера миграций, размер батча, пагинация админки, механика идемпотентности;
* устройство фикстур и способ, которым cross-boundary тест поднимает обе стороны.

---

## 12. Что не замерено

1. **Живые числа на сегодня** — доступа к контуру у этого окна нет. `UNKNOWN_NOT_REMEASURED`; использован базис 08.09.2026 16:40 MSK с явной датой.
2. **Какой SHA реально крутится на `dev-web-1` / `ayla-bot-staging-web-1`.** `PILOT_MEASUREMENTS.md` §10.2 фиксирует обрыв цепочки выкладки 09.09 и ручную починку. **Из зелени `origin/dev` не следует, что гейт VERIFIED-only исполняется на пилоте.** Не проверял.
3. **`suggested_template`, сиды, матчер, ручная проверка, инвалидация** — предмет групп A и B, не трогал.
4. **`formula-tela` как образец** — предмет главного окна.
5. **Silent overwrite связи** при пересинхронизации — не измерял.
6. **Построчная сверка с `RECOMMENDATION_RESOLVER_CONTRACT_v1.0` / `RECOMMENDATION_BOUNDARY_PLAN_v1.0` / `PLANNING_CONSTRAINTS_CONTRACT_v1.0` / ADR** — предмет главного окна; читал только те §, что процитированы в самом коде.
7. **`ayla-knowledge` целиком** — прочитан один документ (`Ayla Domain Capability Registry`), чтобы установить коллизию термина.

Пустое место здесь честнее выдуманного факта.

---

## 13. Команды воспроизведения

```bash
# базы
git -C ~/PycharmProjects/Ayla/djangoproject   fetch origin && git -C ~/PycharmProjects/Ayla/djangoproject   rev-parse origin/dev
git -C ~/PycharmProjects/Ayla/ai-bot-platform fetch origin && git -C ~/PycharmProjects/Ayla/ai-bot-platform rev-parse origin/dev
git -C ~/PycharmProjects/Ayla/ayla-ai-core    fetch origin && git -C ~/PycharmProjects/Ayla/ayla-ai-core    branch -r   # доказательство: origin/dev нет
git -C ~/PycharmProjects/Ayla/ayla-knowledge  fetch origin && git -C ~/PycharmProjects/Ayla/ayla-knowledge  rev-parse origin/main

# H — канонический слой
grep -rn "class MappingStatus" --include=*.py djangoproject-catalog/
sed -n '78,160p'  djangoproject-catalog/services/models.py          # ServiceTemplate
sed -n '314,500p' djangoproject-catalog/services/models.py          # SalonService + констрейнты
grep -rni "capability" --include=*.py djangoproject-catalog/ | grep -v migrations
grep -rn "capability_refs\|canonical_service_refs" --include=*.py djangoproject-catalog/
grep -rn "CAPABILITY_ONLY" --include=*.py djangoproject-catalog/    # только объявления, ни одного производителя
git -C ayla-ai-core grep -lni "mapping_status\|canonical_service\|capability\|ServiceTemplate" origin/main -- '*.py'   # пусто

# I — гейт и потребитель
grep -n -A 30 "def _mapping_admission" djangoproject-catalog/recommendation/_stages.py
sed -n '392,472p' djangoproject-catalog/users/recommendation_source.py       # _MappingFacts.status / _as_status
grep -rn "mapping_status" --include=*.py djangoproject-catalog/ | grep -v "recommendation/\|recommendation_source\|services/models.py\|migrations\|tests"   # пусто -> прямой выбор не гейтится
grep -rn "RECOMMENDATION_PILOT_MAPPING_OVERRIDE" --include=*.py djangoproject-catalog/                                # только тесты
git -C ai-bot-platform grep -n "resolve_recommendation" origin/dev -- '*.py'                                          # производственных вызовов нет
git -C ai-bot-platform show origin/dev:tests/contracts/test_recommendation_boundary_guard.py | sed -n '40,60p'         # _ALLOWED: discovery.py, handoff.py
git -C ai-bot-platform show origin/dev:apps/catalog/models.py | grep -n "class \|models\." | sed -n '1,60p'            # в зеркале нет mapping_status

# J — WHY и происхождение
sed -n '36,42p'   djangoproject-catalog/recommendation/_serializers.py            # FORBIDDEN_RESPONSE_FIELDS
sed -n '353,372p' djangoproject-catalog/users/catalog_recommendations_api.py      # _evidence_payload
git -C ai-bot-platform show origin/dev:apps/miniapp/src/lib/customer-booking.ts | sed -n '129,180p'        # REASON_PHRASES
git -C ai-bot-platform show origin/dev:apps/miniapp/src/screens/CustomerCatalogScreen.tsx | sed -n '190,215p'
grep -rn "mapping_source_ref\|mapping_confirmed_by\|mapping_confirmed_rule" --include=*.py djangoproject-catalog/ | grep -v tests   # только модель и миграция

# O — тесты
sed -n '198,258p' djangoproject-catalog/recommendation/tests/test_pipeline.py
sed -n '488,536p' djangoproject-catalog/users/tests/test_catalog_recommendations_99.py
grep -n "def test" djangoproject-catalog/services/tests/test_mapping_status_provenance.py
grep -n "def test\|class Test" djangoproject-catalog/users/tests/test_recommendation_source.py

# шов безопасности
grep -n -A 12 "def resolved_requires_health_check" djangoproject-catalog/services/models.py
sed -n '200,268p' djangoproject-catalog/services/serializers.py
git -C ai-bot-platform show origin/dev:apps/skills/booking/skill.py | sed -n '1385,1440p'
```

---

**STOP.** Замер завершён. Кода не менял, статусов не выставлял, гейт не ослаблял, Linear не обновлял, решений владельца не принимал. Временных файлов и worktree не создавал — убирать нечего.
