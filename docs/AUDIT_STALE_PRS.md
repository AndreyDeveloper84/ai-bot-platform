# Аудит висящих PR — только кодовые

**Дата:** 2026-08-20 · **База сравнения:** `origin/dev` = `bc49d31` · **Метод:** сравнение содержимого (в репозитории squash-merge, `git merge-base --is-ancestor` для вердикта «влито» **не применялся**).

> По решению владельца в разбор вошли только PR, затрагивающие кодовую базу — **#1041** и **#1128**. Остальные шесть (#960, #967, #1007, #1015, #1040, #1126) содержат исключительно `.md` и не разбирались.

## Сводка

| PR | Что делает | Приехало ли в dev | Рекомендация |
|---|---|---|---|
| **#1041** `booking-flip-and-hardening` | Flip `BOOKING_VIA_AYLA_REST` в prod/staging + calc_price на `ayla_service_id` + round-trip smoke | **Да, почти целиком** — 2 из 3 коммитов ветки являются настоящими предками `dev` | **Закрыть** |
| **#1128** `s3b-catalog-master-bookable` | `CatalogMaster` + `MasterService` как зеркало bookable-ребра Ayla | **Да, функционально целиком** — приехало через DRF-945, но с *противоположным* решением по деактивации | **Закрыть** |

Оба PR не просто устарели — **их мерж сейчас нанесёт ущерб** (детали ниже). При этом в каждом есть по 2–3 находки, которые в `dev` отсутствуют и которые нельзя потерять при закрытии.

---

## #1041 — `claude/s1/booking-flip-and-hardening`

### Что он делает
Три коммита: (1) `calc_price` заземляется на `ayla_service_id` + tenant-scoped proxy upsert; (2) smoke-тест полного цикла брони при флаге ON (create→show→reschedule→cancel); (3) включение `BOOKING_VIA_AYLA_REST` по умолчанию в `production.py` и `staging.py` плюс boot-time fail-fast, если флаг ON, а креды Ayla не заданы.

### Приехало ли в dev

**VERIFIED — да, 2 из 3 коммитов физически лежат в истории `dev`.** Здесь редкий случай, когда ветка попала в `dev` не squash'ем: `git merge-base origin/dev origin/claude/s1/booking-flip-and-hardening` возвращает `47071f82` — это **собственный коммит ветки**, а не точка расхождения. Значит `a43bd1d` (calc_price) и `47071f8` (round-trip smoke) — настоящие предки `dev`.

Следствие: 7 из 11 файлов PR уже в `dev` дословно — `apps/integrations/ayla/booking_client.py`, его тесты, `apps/skills/booking/provider.py`, `apps/skills/booking/tools.py`, `test_ayla_write_lifecycle.py`, `test_tools_calc_price.py`, `tests/smoke/test_ayla_booking_roundtrip.py`. **На живом пути броней невыложенного кода нет.**

Не приехал только третий коммит `b95bbb9` — конфигурационный. Остаток PR относительно `dev` ровно 4 файла (`git diff origin/dev...origin/claude/s1/booking-flip-and-hardening --stat` → 75 вставок).

### Актуальность предпосылок

**VERIFIED — предпосылка устарела, и `dev` пошёл другим путём сознательно.** `origin/dev:config/settings/base.py:638` по-прежнему держит флаг `false` по умолчанию, и комментарий над ним прямо описывает выбранный подход:

> `DEFAULT OFF — the flip (#1041) is gated on the ayla_service_id coverage report (#1016/#1034, command: link_ayla_service_ids) and is executed by the orchestrator. ... production flips deliberately, never ad-hoc.`

То есть `dev` **знает про этот PR по номеру** и намеренно оставил флаг выключенным в настройках, управляя им через переменную окружения. На пилоте флаг включён именно так. Отдельно отмечу: устаревшего докстринга здесь нет — версия комментария в `dev` свежее и точнее, чем та, которую PR предлагает («endpoints are not live yet», «client is still a skeleton»).

**VERIFIED — мерж сейчас вызовет регрессию.** `tests/smoke/test_catalog_settings.py` в PR **старше** dev-версии: PR откатывает файл на retired-переменные `MYSITE_CATALOG_*`, тогда как `dev` уже перевёл его на `AYLA_BASE_URL` / `AYLA_INTERNAL_API_TOKEN` (S3B #1044). Мерж вернул бы тест к несуществующим настройкам.

**VERIFIED — большая часть «hardening» уже в dev, причём в более строгом виде.** `origin/dev:config/settings/production.py:33-38` уже содержит безусловный fail-fast по `AYLA_INTERNAL_API_TOKEN` — он жёстче условного guard'а из PR, который срабатывал бы только при флаге ON.

### Что есть ценного и отсутствует в dev

1. **Нет boot-time проверки `AYLA_BASE_URL` в production.** VERIFIED: `git grep AYLA_BASE_URL origin/dev -- config/` даёт только объявление в `base.py:586` со значением по умолчанию `""`. Проверки при старте нет. Частично закрыто на уровне рантайма — `origin/dev:apps/integrations/ayla/booking_client.py:611-612` бросает `ValueError("AYLA_BASE_URL is empty — booking client cannot start")` при конструировании клиента. Разница в цене ошибки: сейчас пустой `AYLA_BASE_URL` проходит деплой и всплывает на первой брони, а не на старте процесса. Это единственный настоящий недовыложенный кусок hardening — маленький, но именно того класса, что живёт месяцами незамеченным.
2. **Формулировка отката без редеплоя** (`BOOKING_VIA_AYLA_REST=false` как аварийный тумблер) — в `dev` она есть в комментарии `base.py`, но не в runbook'е production. Организационная мелочь, не код.

### Рекомендация: **закрыть**

Причина: ценность PR выложена (7 из 11 файлов — настоящие предки `dev`), оставшийся коммит противоречит сознательно выбранной в `dev` схеме управления флагом через окружение, а один из четырёх оставшихся файлов при мерже откатит тест на retired-переменные. Находку №1 (guard `AYLA_BASE_URL`) вынести отдельной задачей на несколько строк.

---

## #1128 — `feat/s3b-catalog-master-bookable`

### Что он делает
Расширяет `MasterService` до полного зеркала bookable-ребра Ayla (`SpecialistService`): добавляет `ayla_specialist_service_id`, `is_active`, `price`, `resolved_duration`, `resolved_requires_health_check`, партиальные unique-констрейнты на `MasterService` и `CatalogMaster`, плюс клиент `fetch_specialist_services`, апсертер и тесты.

### Приехало ли в dev

**VERIFIED — функционально приехало целиком, под именем DRF-945.** В `dev` есть весь механизм:
- `origin/dev:apps/catalog/services/http_client.py` — `CatalogSpecialistServiceDTO`, `EdgeSnapshot`, `fetch_specialist_services()`, `_parse_specialist_service()`;
- `origin/dev:apps/catalog/services/upserter.py` — `upsert_master_services()`, `_upsert_one_master_service()`, `_reconcile_master_services()`;
- `origin/dev:apps/catalog/models.py:430` — поле `ayla_specialist_service_id` и констрейнт `uq_master_service_tenant_ayla_specialist_service_id` — **под тем же именем**, что в PR.

### Миграция — прямое столкновение

**VERIFIED. Последняя миграция каталога в `dev` — `0011_masterservice_ayla_edge_provenance.py`** (сгенерирована 2026-08-08). Миграция PR называется `0011_masterservice_ayla_specialist_service_id_and_more.py`. **Обе зависят от `0010_alter_catalogfaq_external_id_and_more` — это классическая гонка миграций: два узла `0011` с общим родителем.** Django откажется строить план (`Conflicting migrations detected; multiple leaf nodes`).

Хуже того, конфликт не только по номеру. Сравнение операций:

| Операция PR `0011` | Состояние в `dev` |
|---|---|
| `AddField MasterService.ayla_specialist_service_id` | **Уже добавлено** dev-миграцией `0011` |
| `AddConstraint uq_master_service_tenant_ayla_specialist_service_id` | **Уже добавлен** dev-миграцией `0011`, имя совпадает |
| `AddField MasterService.is_active` | Нет — и отвергнуто сознательно (ниже) |
| `AddField MasterService.price` | Нет |
| `AddField MasterService.resolved_duration` | Нет — вынесено в `raw` сознательно |
| `AddField MasterService.resolved_requires_health_check` | Нет — вынесено в `raw` сознательно |
| `AddConstraint uq_catalog_master_tenant_ayla_user_id` | **Нет — настоящий пробел** |

То есть PR попытается создать существующий столбец и существующий констрейнт с тем же именем. Это ровно тот класс инцидента, о котором предупреждали: провалившийся `migrate` под `|| true` промолчал бы, и расхождение схемы жило бы невидимым.

### Актуальность предпосылок

**VERIFIED — центральное проектное решение PR в `dev` рассмотрено и отвергнуто, с объяснением.** Это главный вывод по PR.

PR добавляет `MasterService.is_active` с обоснованием «деактивированное ребро остаётся зеркалированным (upsert-only, без tombstone), но небронируемым». Докстринг `MasterService` в `dev` (`origin/dev:apps/catalog/models.py:389-397`) отвечает на это прямо:

> `Row existence is the contract. ... none of them filters a status column. So there is no is_active flag here on purpose: an edge upstream marks inactive leaves no row, and a vanished edge is deleted. A tombstone would read as "offered" everywhere and would make a non-bookable service bookable.`

Аналогично по `resolved_*` — `origin/dev:apps/catalog/services/http_client.py:135`:

> `resolved_duration / resolved_requires_health_check ride in raw only — they belong to the booking gate (#1034), not to discovery, and mirroring them here would create a fail-open column with no reader.`

Это не «PR устарел» — это «позже приняли обратное решение и записали причину». Мерж вернул бы fail-open поведение, которого в `dev` избегали намеренно.

**VERIFIED — мерж уничтожит институциональную память.** Диффом PR **удаляет** из `MasterService` весь раздел докстринга «Dual ownership (DRF-945 / P1 service discovery)» — тот, где записаны разделение операторских и синковых строк и инцидент с 232 нереконсилируемыми рёбрами на пилотном тенанте (DRF-967). GitHub помечает PR как `CONFLICTING` — механически это подтверждает, но содержательная причина именно эта.

### Что есть ценного и отсутствует в dev

1. **Партиальный unique-констрейнт `uq_catalog_master_tenant_ayla_user_id` на `CatalogMaster(tenant, ayla_user_id)`.** VERIFIED: `git grep uq_catalog_master_tenant_ayla_user_id origin/dev -- apps/` пусто; в `Meta` `CatalogMaster` констрейнтов нет вообще, только `unique_together (tenant, external_id)` и индексы. Почему это важно: `dev` ключует мастеров по `id=dto.ayla_master_id` (SpecialistProfile.id), а `ayla_user_id` пишет как обычное зеркальное поле (`upserter.py:141-186`) — при этом сам же объявляет его «мостом для JOIN `event-payload master_user_id → CatalogMaster`». Два SpecialistProfile с одним `user_id` дадут две строки с одинаковым `ayla_user_id` и сделают этот JOIN неоднозначным. Защиты на уровне БД нет. **Это стоит отдельной задачи.**
2. **`CatalogMaster.__str__` с фолбэком на `ayla_user_id`.** У мастеров, пришедших из Ayla, нет целочисленного `external_id`, поэтому в `dev` они печатаются в админке и логах как `CatalogMaster[Имя@]` с пустым хвостом. Правка на одну строку, чисто эргономика диагностики.
3. **`MasterService.price` — цена на уровне конкретного мастера.** В `dev` цена есть только салонная (`CatalogService.price_from`). Это не техническое устаревание, а продуктовый вопрос.

### Рекомендация: **закрыть**

Причина: функциональность приехала через DRF-945; миграция сталкивается с dev-овской `0011` и по номеру, и по содержимому (дублирует столбец и констрейнт); центральное решение по `is_active`/`resolved_*` в `dev` сознательно отвергнуто с записанной причиной; мерж вдобавок стёр бы докстринг с историей инцидента DRF-967. Находки 1–2 вынести задачами, находку 3 — владельцу.

### Вопрос владельцу

> Нужна ли боту цена на уровне «мастер × услуга» (`MasterService.price`), или салонной `CatalogService.price_from` достаточно?

---

## Что из этого касается нового разработчика

Новый человек заходит на парсер услуг, отзывы, рейтинг и адреса — это территория `apps/catalog/`, ровно там, где лежит #1128. Что ему нужно знать:

1. **Не писать второе зеркало bookable-ребра.** Оно есть и работает: `apps/catalog/services/http_client.py` (`fetch_specialist_services`, `EdgeSnapshot`) → `apps/catalog/services/upserter.py` (`upsert_master_services`, `_reconcile_master_services`) → `apps/catalog/models.py` (`MasterService`). #1128 — предыдущий заход на ту же задачу, он закрыт как поглощённый, продолжать его не нужно.
2. **Два правила `MasterService`, нарушение которых ломает бронирование.** Первое: `ayla_specialist_service_id` — единственный дискриминатор владения строкой (NULL = операторская из MM4-матрицы, sync её не трогает; non-NULL = синковая, подлежит реконсиляции). Второе: **статусной колонки здесь нет намеренно** — исчезнувшее ребро удаляется, а не помечается неактивным. Обе причины записаны в докстринге `MasterService`, прочитать до первой правки. Инцидент с 232 нереконсилируемыми рёбрами на пилоте — про первое правило.
3. **`resolved_duration` / `resolved_requires_health_check` живут в `raw`, а не колонками** — сознательно, чтобы не завести fail-open колонку без читателя. Health-гейт бронирования читает их через `apps/skills/booking/skill.py`, временно в обход через allowlist `BOOKING_HEALTH_CHECK_GATE_DISABLED_TENANTS` (решение владельца 2026-08-12). Allowlist временный и подлежит снятию, когда канонический resolved-источник поедет.
4. **Рейтинг и отзывы уже частично зеркалируются:** `CatalogMaster.rating` и `review_count` пишет `upsert_specialists`, перезаписывая только зеркальные поля; платформенные (`invite_status`, `mode`, `photo_url`, `archived_at`, ...) sync не трогает никогда. Расширять — в этой же рамке.
5. **Про миграции каталога:** последняя — `0011_masterservice_ayla_edge_provenance`. Новую нумеровать от неё. История с двумя `0011` в #1128 — наглядный пример, чего стоит разъехавшаяся ветка; в этом репозитории провал `migrate` уже однажды молчал месяцами.
6. **Наследство от #1041:** boot-time проверки `AYLA_BASE_URL` в production нет, есть только рантайм-`ValueError` при создании booking-клиента. Если новый человек заводит окружение — пустой `AYLA_BASE_URL` пройдёт деплой молча.
