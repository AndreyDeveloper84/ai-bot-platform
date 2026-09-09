# Обследование: онбординг салона и его «приземление»

**Дата:** 2026-08-20 · **Метод:** статическое чтение кода двух репозиториев + Linear. Живой контур не дёргался, ничего не менялось, ветки не переключались.

**Что читалось.**
Бэкенд — объекты git ветки `origin/dev` (`e2b218b`, 20.08 — то, что стоит на пилоте), плюс рабочее дерево `Ayla/djangoproject-salonops`.
Бот — рабочее дерево `ai-bot-platform` (оно отстаёт от `origin/dev` = `bc49d31`, расхождения помечены по месту).
Соседний репозиторий `PycharmProjects/mysite` — только чтобы установить происхождение каталога.
В занятые деревья (`-admin`, `-conv`, `-codex`, `-b5`, `-drf1061`, `beautygo_backend-conv/-drf1043/-drf1046`) никто не заходил; из последних читались **только объекты git**, рабочие копии не тронуты.

**Какое дерево бэкенда актуально.** Все четыре `Ayla/djangoproject*` — клоны `beautygo_backend`. Свежесть: `djangoproject-salonops` (15.08) > `djangoproject-catalog` (04.08) > `djangoproject` (31.07) > `djangoproject-alpha` (03.06). Но **самый свежий `origin/dev` (20.08) есть только в деревьях `beautygo_backend-*`** — то есть все четыре дерева в `Ayla/` устарели минимум на пять дней. Это само по себе ловушка для новичка.

**Пометки доказательства:** **VERIFIED** — прочитан код, показана строка · **INFERRED** — вывод из кода, дана строка · **CLAIMED** — так написано в докстринге/доке, кодом не проверено · **UNKNOWN**.

> **Дисциплина чтения.** В этом проекте докстринги уже дважды утверждали то, чего нет. В этом обследовании нашлось **ещё пять** таких мест — они перечислены в отдельной таблице в конце. Ниже каждое «есть» подтверждено строкой, каждое «работает» — прослеженным вызовом. Где вызова нет, так и написано.

---

## Итог в пяти строках

1. **Ни одна из четырёх тем не является чистым полем — и ни одна не работает.** Везде построен слой, который никто не вызывает или который гасит флаг. Новичок входит не в чистое поле и не в готовый дом, а в **дом с построенными стенами и неподключёнными коммуникациями**.
2. **Отзывы.** На бэкенде — полный API и 21 тест в CI; отзыв оставить **нельзя** по двум независимым причинам: завершение визита выключено флагом `BOOKING_AUTO_COMPLETE_ENABLED=false`, и `Review.service` — NOT NULL FK на **легаси**-модель, которой у броней пилотного пути не существует. В боте модели отзыва нет вовсе; локальная оценка визита есть, но кнопка к ней на пилотном флаге захардкожена в `False`.
3. **Рейтинг.** Вычисляется из отзывов ровно в одном месте — при создании отзыва — и **хранится полем**. Ни скрытие, ни правка отзыва его не пересчитывают. Отзывов нет ⇒ у всех мастеров `rating = 0.0`, а движок рекомендаций сортирует по нему первым ключом и объясняет выбор фразой «Высокий рейтинг».
4. **Адреса.** Модели адреса нет ни в одном из репозиториев. У **салона** (`Tenant`) на бэкенде нет ни адреса, ни города, ни координат; в боте есть одна плоская строка `Tenant.city`, по которой идёт точное сравнение. Прямого геокодирования нет, геолокация мессенджера не используется, 2GIS не подключён нигде.
5. **Парсер услуг.** Построен на 80 %, и это лучшая новость отчёта. Есть staging-модель, идемпотентный конвейер, два адаптера-источника, confirm-поток и 42 теста в CI. Не хватает ровно двух вещей: **сопоставления внешнего названия с каноном** (поле `suggested_template` не пишет никто) и **любого интерфейса, кроме командной строки**.

---

## Таблица: тема × что есть × что исполняется × чего нет

| Тема | Что есть (код) | Что исполняется (проверено вызовом) | Чего нет |
|---|---|---|---|
| **Парсер услуг** | Ayla: `DraftSalonService`, `ExternalSourceMapping`, конвейер `intake/*`, 2 команды CLI, админка Django, 42 теста в CI. Бот: зеркало `CatalogService`/`MasterService` + beat-синхронизация каждые 15 мин | Живой путь ровно один: `manage.py intake_csv` → `intake_confirm` руками. `YClientsApiSource` **не вызывается нигде вне тестов**. Зеркало в боте синхронизируется автоматически и реально | Сопоставления с каноном (`suggested_template` не пишется), HTTP-ручек, экрана, детектора расхождения по услугам (есть только по рёбрам мастер↔услуга), парсинга сайта, 2GIS |
| **Отзывы** | Ayla: `Review` + 3 миграции, 4 ручки, 21 тест в CI, push-шаблон `review_request`, консьюмер `review.created` в боте. Бот: `BookingRequest.rating/feedback_*`, экран `FeedbackScreen.tsx`, beat-напоминание | Ручки смонтированы и отвечают. **Создать отзыв нельзя** (флаг + несовместимость FK). В боте форма недостижима (`can_rate=False` на Ayla-пути). Beat-напоминание работает — и шлёт отменившим (DRF-1141) | Модерации (`is_hidden` никто не пишет), админки (`reviews/admin.py` нет), отзывов о **салоне**, передачи оценки из бота в Ayla, `Review.tenant` при записи |
| **Рейтинг** | Ayla: `SpecialistProfile.rating`/`reviews_count`, `_recalculate_rating()`. Бот: зеркальные `CatalogMaster.rating`/`review_count` | `_recalculate_rating` вызывается **только** при создании отзыва. Отзывов нет ⇒ поле = 0.0 у всех. Потребителей много, все читают ноль | Пересчёта при скрытии/правке/удалении, рейтинга салона, применения `min_rating_preference` (хранится и отдаётся — **не используется нигде**), обещанного «Bayesian trust-score» |
| **Адреса** | Ayla: `SpecialistProfile.address` (строка), `location_lat/lng` (руками), `reverse_geocode_city` (Yandex). Бот: `Tenant.city` (строка) | Обратное геокодирование вызывается **только** из `services/pricing.py` ради регионального прайса. В боте фильтр `tenant__city__iexact` работает, город достаёт LLM из свободного текста | Модели адреса/филиала, адреса и города у **салона** на бэкенде, прямого геокодирования, нормализации города, геолокации мессенджера, 2GIS |

---

## 1. Парсер услуг

### 1.1. Модель данных — есть, и хорошая

**VERIFIED**, `Ayla/djangoproject-salonops/services/models.py`:

- **`DraftSalonService`** (`:505`) — staging-строка внешнего прайса. Поля: `tenant`, `status` (`pending/confirmed/rejected/superseded`), `external_source` (`yclients/csv`), `external_service_id`, `suggested_template` (FK на канон), `external_name`, `suggested_duration`, `suggested_price`, `raw_payload` (JSON), `confirmed_salon_service`, `confirmed_at`, `confirmed_by`. Ключ идемпотентности — частичный `UniqueConstraint(tenant, external_source, external_service_id)` при непустом внешнем id (`:568`).
- **`ExternalSourceMapping`** (`:586`) — идемпотентный ключ «внешний id ↔ сущность Ayla». Два взаимоисключающих FK (`salon_service` / `specialist`) вместо GenericFK, XOR проверяется в `clean()` и вызывается из `save()` (`:648`).

Миграции: `services/migrations/0012_catalog_domain_s3a.py` и далее. Контейнер гоняет `python manage.py migrate --noinput` на старте (**VERIFIED**, `entrypoint.sh:32`); на пилоте применены — **CLAIMED** (`docs/HANDOFF_MAIN_WINDOW.md` §2: «Пилот (бэкенд) `e2b218b`, миграций 0»).

### 1.2. Конвейер — есть, исполняется наполовину

**VERIFIED**, `services/integrations/intake/`:

| Файл | Что делает | Вызывается? |
|---|---|---|
| `normalize.py` | Чистые функции: `seance_length` (секунды) → минуты, `is_folder` → выбросить, цены → `Decimal` | да, из `sources.py` |
| `sources.py` | `YClientsApiSource` (`:37`) и `CsvSource` (`:85`) под общим `Protocol` | `CsvSource` — да; **`YClientsApiSource` — ни одного вызова вне тестов** |
| `pipeline.py` | `import_catalog()` (`:128`) — идемпотентный upsert черновиков + маппинг персонала; статус, поставленный человеком, при переимпорте **сохраняется** (`:74-76`) | да, из `intake_csv` |
| `confirm.py` | `confirm_draft()` (`:122`) — черновик → `SalonService` + `SpecialistService` + маппинг | да, из `intake_confirm` |

**Две дыры, обе VERIFIED:**

1. **`suggested_template` не пишет никто.** Поиск по коду вне тестов даёт только *чтение* (`confirm.py:73`) и админку. То есть **семантического разбора прайса — сопоставления «Маникюр комбинированный + покрытие» с канонической услугой — не существует**. Каждый черновик приезжает вне таксономии, и `intake_confirm` требует `--category` как заглушку (`confirm.py:77`). Это ровно та работа, которую владелец называет «парсер услуг», и она **не начата**.
2. **`YClientsApiSource` — мёртвая ветка.** Класс написан и покрыт тестами, но команды, которая бы его дёрнула, нет. Живой источник ровно один: CSV.

### 1.3. API и интерфейс

- **HTTP-ручек для intake нет вовсе** — в `djangoProject/urls.py` нет ни одного маршрута к черновикам (**VERIFIED**).
- Внутренний каталог для бота — **только на чтение**: `InternalSalonServiceViewSet` / `InternalSpecialistServiceViewSet` наследуют `viewsets.ReadOnlyModelViewSet` (**VERIFIED**, `services/internal_api.py:43,58`), смонтированы на `/api/v1/internal/catalog/`.
- Админка Django: `DraftSalonServiceAdmin` (`services/admin.py:138`) — список, фильтры, поиск, можно вручную проставить `suggested_template`. **Действия «подтвердить» нет** — `actions` не объявлены (**VERIFIED**).
- Салонной админки каталога нет: в `tenants/urls.py` только расписание, день салона и ручная запись (**VERIFIED**).
- В боте: инвайт мастера умеет прицепить услуги из уже существующего зеркала (`apps/admin_api/views_invite.py`), но завести услугу — нет.

### 1.4. Как услуги попадают в систему сегодня — реальная цепочка

**VERIFIED** (по коду трёх репозиториев):

```
YClients (боевая CRM салона)
  └─ mysite  — собственный сайт салона «Формула тела», Django
       синхронизация мастеров и услуг: mysite/sync_masters_services_from_yclients.py
  └─ mysite/export_pilot_catalog_csv.py           ← коммит 557f6ca, 13.07.2026
       CSV: external_service_id,title,duration_min,price_min,price_max,category,staff_ids
  └─ Ayla: manage.py intake_csv --tenant … --file …          → DraftSalonService
  └─ Ayla: manage.py intake_confirm --tenant … [--category …] → SalonService + SpecialistService
  └─ Ayla: GET /api/v1/internal/catalog/…   (read-only, Bearer)
  └─ бот: CatalogSyncService → CatalogService / CatalogMaster / MasterService
       beat catalog_sync_every_15min (config/settings/base.py:1052)
```

**Пять пересадок.** Детектор расхождения есть ровно на одной — и только для рёбер мастер↔услуга: `apps/catalog/services/sync.py:214`, `reconcile=edge_snapshot.complete`, удаление по отсутствию включается только при полном снапшоте (`:211-213`: «a partial snapshot must not delete»). Для `CatalogService` и `CatalogMaster` удаления по отсутствию **нет вовсе**. Коммит в `mysite` называет себя «one-shot read-only bridge» — мост задумывался **одноразовым**.

Заголовок `apps/catalog/models.py:1-6` в боте всё ещё называет источником `mysite` — это устарело, mysite ретайрнут (`config/settings/base.py:1302-1306`). **Ещё один докстринг, которому нельзя верить.**

### 1.5. Двойное владение рёбрами — уже заложено, и это правильно

**VERIFIED**, `apps/catalog/models.py:357-467` (`MasterService`, поле `ayla_specialist_service_id` на `:430`, разъяснение `:364-397`): если внешний id `NULL` — строку создал оператор через матрицу мастер×услуга, синхронизация её **никогда не трогает**; если non-NULL — владеет синхронизация и может реконсилить. Это единственное место во всём контуре, где вопрос «кто хозяин строки» решён явно. Его стоит взять образцом для остальных тем.

### 1.6. Парсинга сайта и 2GIS нет нигде

**VERIFIED.** В `requirements.txt` бэкенда нет ни `playwright`, ни `beautifulsoup4`, ни `readability`, ни `lxml`. В боте поиск по `apps/` даёт 0 попаданий по `playwright|beautifulsoup|bs4|selenium|ImportJob|2gis|dgis`. Слово «readability» в `apps/kb/tasks.py:229` — обычное английское слово в докстринге, не библиотека. 2GIS упоминается только в документах как «провайдер не выбран» (`docs/ARCHITECTURE_REVIEW.md:353,390`).

**Про `apps/kb`** — это RAG-хранилище, а не импортёр каталога. Есть `KbDocument` (`apps/kb/models.py:66`, 6 типов), три Celery-таски и три ручные seed-команды (Google Docs, mysite, legacy). **Направление потока: каталог → KB, KB в каталог не пишет никогда** (`apps/kb/projectors.py`). И главное: **ни одна kb-таска не стоит в beat-расписании** (**VERIFIED**), хотя докстринг `apps/kb/tasks.py:113-116` **CLAIMED** утверждает, что `sync_catalog_to_kb` цепляется к beat-успеху каталожной синхронизации. В `apps/catalog/tasks.py` нет ни импорта, ни вызова. Проекция каталога в RAG автоматически не происходит; единственный живой вызов — ручное действие в админке (`apps/kb/admin.py:103`).

⇒ **Если DRF-774 «Website scraper connector» и DRF-775 «2GIS connector» стоят в Linear как Done — их кода нет ни в боте, ни в бэкенде.** Либо они в третьем репозитории, либо тикеты закрыты без кода. Это надо выяснить до постановки задачи новичку.

### 1.7. Тесты и CI

**VERIFIED.** Бэкенд: `services/tests/test_csv_intake.py` (8), `test_intake_confirm.py` (12), `test_intake_confirm_command.py`, `test_catalog_models.py` (22), `test_catalog_contract_s3d.py`. CI гоняет `pytest --tb=short -q` целиком без исключений (`.github/workflows/ci.yml:128`) — значит гоняются все.
Бот: тесты каталога есть, но **CI текущего дерева гоняет всего три узких pytest-вызова** (строки 149, 159, 168) — `tests/smoke/`, контракт роутов Ayla и `apps/eventbus/tests/`. Тесты `apps/catalog`, `apps/marketplace`, `apps/booking` — **не гоняются**. На `origin/dev` бота покрытие шире (`pytest apps/`), фронтового (vitest) нет ни в одном из шести workflow.

---

## 2. Отзывы

### 2.1. Модель — есть, полная (только в Ayla)

**VERIFIED**, `reviews/models.py:11` — `Review`: `appointment` (**OneToOne**, один отзыв на визит), `client`, `specialist`, `service`, `rating` (1–5), `text` (≤1000), `is_anonymous`, `specialist_reply`, `is_hidden`, `tenant` (nullable), `created_at`, `updated_at`. Три миграции.

**В боте модели отзыва нет** (**VERIFIED**: единственный класс с «Review» в имени — `ReviewProcessedDedupe`, таблица дедупликации событий).

### 2.2. API — есть, смонтирован

**VERIFIED**, `djangoProject/urls.py:91` → `reviews/urls.py`:

| Метод и путь | Вид | Кто |
|---|---|---|
| `POST /api/v1/reviews/` | `ReviewCreateView` | клиент |
| `PATCH /api/v1/reviews/{id}/` | `ReviewUpdateView` | автор, только текст |
| `POST /api/v1/reviews/{id}/reply/` | `ReviewReplyView` | тот мастер, о ком отзыв |
| `GET /api/v1/specialists/{id}/reviews/` | `SpecialistReviewsView` | публично (`users/specialists_urls.py:21`) |
| `GET /api/v1/reviews/specialists/{id}/` | тот же вид, `deprecated` | публично |

**Внутренней ручки отзывов для бота нет** (**VERIFIED** — в карте `internal/` её не существует). Обратный канал есть: бот слушает событие `review.created` (`apps/eventbus/consumers/reviews.py`, зарегистрирован при старте, `apps/eventbus/apps.py:76-78`) и пишет в `ClientProfile` производные признаки — `last_review_rating`, `last_review_at`, `low_rating_flag` (sticky), `sentiment_score`. **Текст отзыва в бот намеренно не передаётся** (PII §7). Этот кусок — единственное в теме отзывов, что действительно работает.

### 2.3. Исполняется ли — нет, и по трём независимым причинам

**Причина первая: визит не завершается.**
`ReviewCreateView` требует `appointment.status == COMPLETED` (**VERIFIED**, `reviews/views.py:99`). Статус ставится в трёх местах, все три появились 15.08 в DRF-1064 (`appointments/application/services/completion.py`): HTTP-ручка, подметающая beat-задача и разовая команда. Beat-задача `auto_complete_elapsed_bookings` зарегистрирована каждые 15 минут, но **инертна**: ей нужны **оба** ключа — `BOOKING_AUTO_COMPLETE_ENABLED` (по умолчанию `false`) **и** `BOOKING_AUTO_COMPLETE_NOT_BEFORE` (по умолчанию пусто); без любого она логирует причину и не трогает ни строки (**VERIFIED**, `djangoProject/settings/base.py:421-439`, `appointments/tasks.py:284-302`). Значение на пилоте — **UNKNOWN** (env не коммитится). Остаётся ручное закрытие `POST /api/v1/appointments/{id}/complete/` — доступно мастеру в Pro-приложении, **не боту** (`appointments/internal_urls.py` содержит только create/cancel/reschedule/payment).

**Причина вторая, тяжелее первой: `Review.service` несовместим с бронями пилота.**
`Appointment` несёт **ровно одну** ссылку на услугу — легаси `service` (маркетплейс) XOR новый `salon_service` (салонный каталог), это закреплено CHECK-констрейнтом `appointment_exactly_one_service_source` (**VERIFIED**, `appointments/models.py:223-241`). Брони, созданные через резолвер салонного каталога, имеют `service = NULL` (**VERIFIED**, `create_booking_service.py:397-403`). А `Review.service` объявлен **без** `null=True` (**VERIFIED**, `reviews/models.py:32-36`), и вид пишет `service=appointment.service` (`reviews/views.py:121`).
⇒ Попытка оставить отзыв на бронь салонного каталога даёт `IntegrityError` (500). **INFERRED** — вывод из кода, вживую не воспроизводился, но конструкция однозначна. Ни один из 21 теста не покрывает бронь с `salon_service` — поэтому дефект и не всплыл.

Это **тот же класс дефекта, что `BookingRequest` с пустым `master_id`**: модель отзывов написана до раздвоения каталога и осталась пришитой к легаси-половине.

**Причина третья: со стороны бота форма недостижима.**
**VERIFIED**, `apps/miniapp_api/views.py:1141-1143`:
```python
# No rating read model on the Ayla path in pilot.
"rating": None,
"can_rate": False,
```
На Ayla-пути (`BOOKING_VIA_AYLA_REST=True`) список и деталь записей читаются из `RemoteBookingProxy` через `_proxy_booking_to_dict` (`views.py:1103`), где `can_rate` захардкожен в `False`. Все три входа в экран оценки (`CustomerBookingDetailScreen.tsx:295`, `CustomerRecordsScreen.tsx:248`, `MyVisitsScreen.tsx:192`) стоят под этим флагом. ⇒ **Кнопка «Оценить визит» на пилоте не появляется никогда.** Экран `FeedbackScreen.tsx` (198 строк, звёзды + комментарий) и ручка `POST /api/v1/customer/bookings/<id>/feedback` существуют и работают — до них просто нет пути.

И даже если бы был: `submit_feedback()` (`apps/booking/services/feedback.py:78`) пишет **только локально** в `BookingRequest.rating/feedback_comment/feedback_at`, **в Ayla ничего не отправляет** (**VERIFIED** — тело прочитано целиком, HTTP-вызовов нет). При `rating <= 3` заводит `AdminTask` типа COMPLAINT.

### 2.4. Модерации нет

**VERIFIED.** `is_hidden` встречается ровно в четырёх местах: объявление поля, индекс, сериализатор и два фильтра чтения. **Ни одна строка кода его не записывает.** Файла `reviews/admin.py` не существует — в админке Django отзывов нет. То есть **DRF-79 не начат**, а поле — заготовка без исполнителя.

### 2.5. `Review.tenant` не проставляется при создании

**VERIFIED.** Докстринг поля утверждает: «invariant maintained by backfill + service layer» (`reviews/models.py:58-59`). Сервисного слоя нет: `Review.objects.create(...)` в `reviews/views.py:115-123` `tenant` не передаёт. Единственный писатель — разовая команда `tenants/management/commands/backfill_tenants.py:151-159`.
⇒ **Любой новый отзыв родится с `tenant = NULL`**, а индекс `review_tenant_created_idx`, заведённый «чтобы админка маркетплейса тянула отзывы по тенанту», работать не будет.

### 2.6. Запрос отзыва — два механизма, оба с дефектом

**На бэкенде (правильный, но выключенный).** Шаблон `review_request` (`notifications/templates.py:192`), обработчик `handle_booking_completed` (`notifications/outbox_handlers.py:301-317`), **реально зарегистрирован** на топик `booking.completed` (`BOOKING_HANDLERS`, `:455`), причём `_register_notification_handlers()` перекрывает лог-заглушку из `appointments/tasks.py:59` (**VERIFIED**, `tasks.py:94-105`). Цепочка «визит закрыт → просьба об отзыве» собрана целиком и включается тем же тумблером `BOOKING_AUTO_COMPLETE_ENABLED`. Отменённые и неявившиеся сюда по определению не попадают.
Докстринг `handle_booking_completed` («The transition to completed has no writer in the codebase yet») **устарел** с 15.08.

**В боте (работающий, но дефектный) — это и есть DRF-1141. ПОДТВЕРЖДЕНО, VERIFIED.**
`apps/bookings/followups.py:190-194`:
```python
    booking_request = reminder.booking_request
    if booking_request is None:
        # NULL FK — legacy row OR Ayla-path. opt_out + payment_failures
        # gates already cleared above; send. Phase 1 closes this gap.
        return (True, None)
```
При `booking_request is None` функция возвращает «отправлять», **не дойдя** до двух ключевых блокеров ниже: `:196-197` (`completed_at is None` → визит не состоялся) и `:199-200` (статус в `{cancelled, rescheduled}`).
Почему FK на Ayla-пути всегда NULL: `apps/eventbus/consumers/booking.py:283-292` создаёт напоминание через `update_or_create(ayla_appointment_id=…, …, defaults=common_defaults)`, а `common_defaults` (`:271-282`) поля `booking_request` **не содержит**. FK выставляется только на локальных путях (`apps/integrations/yclients/webhooks.py:518`, `apps/booking/services/transitions.py:246,511`).
Добивает: `detect_completed_bookings` (`apps/bookings/tasks.py:392-440`) штампует `completed_at` **только** на `BookingRequest`; у `RemoteBookingProxy` поля `completed_at` вообще нет.
⇒ При `BOOKING_VIA_AYLA_REST=true` из четырёх «консервативных блокеров» реально работают два. Напоминание («Как прошёл вчерашний визит к X?», beat `bookings.send_post_visit_followups`, ежедневно 19:00 МСК) **уходит и отменившим, и не пришедшим**. Кнопки в нём нет — ответ падает свободным текстом в диалог и никуда структурированно не пишется (сентимент-классификация явно отложена, `followups.py:79-87`).

**Замкнутого цикла «попросили отзыв → человек ответил → отзыв сохранился» не существует ни на одном пути.**

### 2.7. Тесты и CI

Бэкенд: `reviews/tests/test_reviews_api.py` — 21 тест, гоняются в CI.
Бот: `apps/booking/tests/test_feedback.py`, `apps/miniapp_api/tests/test_feedback_owner_guard.py` — **в CI текущего дерева не гоняются**; консьюмер `review.created` покрыт (`apps/eventbus/tests/` в CI есть).

---

## 3. Рейтинг

### 3.1. Это поле, а не производная — и в этом суть проблемы

**VERIFIED.** `users/models.py:224-227`: `rating = DecimalField(max_digits=2, decimal_places=1, default=0.0)`, `reviews_count = PositiveIntegerField(default=0)`.

Единственный писатель во всём бэкенде — `_recalculate_rating()` (`reviews/views.py:29-52`): `AVG(rating)` и `COUNT(id)` по `Review` с `is_hidden=False`, синхронно, внутри транзакции создания отзыва. Поиск по `rating=` вне тестов и миграций подтверждает: **больше рейтинг не пишет никто**.

Отсюда — выводы, которые важнее описания:

- **Пересчёта при скрытии отзыва нет.** Скрыть сейчас нельзя вообще, но как только появится модерация (DRF-79), она **обязана** дёргать пересчёт — иначе скрытая единица останется в среднем навсегда.
- **Пересчёта при правке текста нет** — это безопасно, `PATCH` меняет только текст.
- **Удаления отзыва нет** — ручки `DELETE` не существует, значит отсюда рассинхронизации пока нет.
- **Рейтинга салона нет вовсе.** У `Tenant` рейтинга нет ни в каком виде (**VERIFIED**, весь список полей: `id, slug, name, is_active, created_at, updated_at`).

### 3.2. В боте рейтинг ничем не вычисляется — это зеркало зеркала

**VERIFIED.** `apps/catalog/models.py:229-239`: `CatalogMaster.rating = DecimalField(3,2, null=True)`, `review_count = PositiveIntegerField(default=0)`.
Единственный писатель: `apps/catalog/services/http_client.py:492-493` (парсинг ответа Ayla) → `apps/catalog/services/upserter.py:168-169`. Никакого агрегата, `AVG()`, пересчёта из `BookingRequest.rating` или из события `review.created` — нет.

То есть путь рейтинга сегодня: `Review` → `AVG` → поле в Ayla → HTTP → поле в боте. **Три копии одного числа, две из них никогда не пересчитываются самостоятельно.**

### 3.3. Кто читает рейтинг — и что читает сегодня

**VERIFIED.** Потребителей много, и все читают ноль:

| Где | Что делает |
|---|---|
| `ai/application/services/recommendation_engine.py:124` | `WEIGHT_RATING * self.rating` в скоринге |
| то же, `:294` | `qs.order_by("-rating", "-reviews_count")[: limit * 3]` — **первичная сортировка кандидатов** |
| то же, `:330-339` | насыщение `reviews_count / (reviews_count + 10)` |
| то же, `:135` | объяснение выбора пользователю: **«Высокий рейтинг»** |
| `ai/tools_handlers.py:102` | отдаёт `rating` в инструмент консьержа |
| `search/views.py:36,73` | поле выдачи поиска |
| `services/serializers.py:29-33`, `:206-217` | отдаёт `rating`/`reviews_count` боту в зеркало |
| бот: `apps/marketplace/discovery.py:406`, `apps/orchestrator/discovery.py:198` (`★ {card.rating}`), `apps/miniapp_api/views.py:569`, `apps/admin_api/views.py:263`, `apps/kb/projectors.py:107` | карточки мастера в чате, Mini App, админке и RAG |

**INFERRED**: пока отзывов нет, ранжирование по рейтингу — шум, а объяснение «Высокий рейтинг» — неправда, которую система говорит пользователю. Показ `★ 0.00` или `★ None` в карточке мастера — прямое следствие.
Отдельно: **ранжирование в discovery бота рейтинг не учитывает вовсе** — `apps/marketplace/discovery.py:275` сортирует `.order_by("name", "id")`, по алфавиту.

### 3.4. `min_rating_preference` — хранится, отдаётся, не применяется

**VERIFIED.** Бэкенд: поле есть (`users/models.py:595`), валидируется 0.0–5.0 (`users/personal_context_views.py:92-97`), отдаётся во внутреннем API персонального контекста как вопрос «Важен минимальный рейтинг мастера?» (`users/internal_personal_context_api.py:46`), покрыто тестами. **Ни один запрос, фильтр или скоринг его не читает.**
Бот: единственное вхождение — парсер значения из памяти (`apps/orchestrator/memory_ask.py:291`); модели с этим полем в дереве бота нет, фильтрации по минимальному рейтингу нет.
⇒ Предпочтение собирается у пользователя и никуда не идёт.

### 3.5. Обещанного «Bayesian trust-score» не существует

**VERIFIED.** Хелптекст `apps/catalog/models.py:234-238` ссылается на «Bayesian trust-score (#1060)». Поиск по `apps/` даёт 0 попаданий на `bayes|trust_score`. **`review_count` в боте сегодня не читает никто.**

---

## 4. Адреса

### 4.1. Модели адреса нет. Модели филиала нет. Ни в одном репозитории

**VERIFIED.**

Бэкенд:
- `Tenant` (салон) — **ни адреса, ни города, ни координат, ни телефона**. Полный список полей: `id, slug, name, is_active, created_at, updated_at` (`tenants/models.py:56-79`). Проверено на актуальном `origin/dev` (`e2b218b`).
- Адрес есть у **мастера**: `SpecialistProfile.address = CharField(500, blank=True)` (`users/models.py:200`), плюс `location_lat`/`location_lng` — `DecimalField(9,6)` (`:201-204`).
- У клиента: `UserProfile.city = CharField(100)` (`:155`) и `default_location_lat/lng` (`:157-160`).

Бот:
- Единственное гео-поле — `Tenant.city = CharField(120, blank=True, default="")` (`apps/tenancy/models.py:251-257`), с честным комментарием: «Minimal geo for the Penza pilot: a plain city string, no PostGIS». Миграция `0010_tenant_city.py` бэкфиллит все пустые в `"Пенза"`.
- Ни `latitude`/`longitude`, ни `django.contrib.gis`, ни `PointField`, ни `Address`/`Branch`.

Отдельной модели филиала, справочника городов, связи «салон → адреса» **не существует нигде**. В Linear поиск по «филиал» тоже даёт ноль задач.

### 4.2. Кто заполняет адрес

**VERIFIED.** Мастер сам, свободным текстом, через `users/serializers.py:164-201` («Step 2+: update profile (address, location, bio, etc)»), и он же руками вводит широту и долготу. Плюс админка (`users/admin.py:140`). Валидации адреса нет, нормализации нет. `Tenant.city` в боте — тоже вручную.

### 4.3. Геокодирование — только обратное и только ради цены

**VERIFIED.** `services/geocoding.py:21` — `reverse_geocode_city(lat, lon) -> str | None` через Yandex Geocoder, `kind=locality`, таймаут 3 с. Без `YANDEX_GEOCODER_API_KEY` возвращает `None` **молча** (`:31-33`) — то есть это флаг, гасящий ветку, замаскированный под отсутствие ключа.
Вызывающий ровно один: `services/pricing.py:53` — определение регионального ключа для `RegionalPricing` («penza» / «moscow» / «default»). К адресам салона отношения не имеет.
**Прямого геокодирования (строка адреса → координаты) не существует.** ⇒ Мастер, вбивший адрес и не вбивший координаты, из гео-поиска выпадает.

### 4.4. Откуда бот берёт город — полная цепочка

**VERIFIED:**

1. LLM-tool-spec `apps/orchestrator/discovery.py:65-89` — `SHOW_MASTERS_TOOL_SPEC` с параметром `"city": {"type": "string"}`. **Город из свободного текста пользователя вытаскивает языковая модель.**
2. `apps/orchestrator/concierge.py:359-360` (живой путь) и `apps/orchestrator/discovery.py:260` (легаси) читают `args.get("city")` и передают **как есть**.
3. `apps/marketplace/discovery.py:279` — `qs.filter(tenant__city__iexact=city)`. **Точное совпадение**, регистронезависимое. Ни транслитерации, ни ё→е, ни склонений, ни справочника, ни фаззи-матчинга.
4. Второй вход: `apps/marketplace/views.py:76` — `request.GET.get("city")` на публичной неаутентифицированной `GET /api/v1/providers/`.

Экрана выбора города нет ни в Mini App, ни в клавиатурах — город спрашивается репликой модели (`apps/orchestrator/concierge.py:277-282`).
**Геолокация мессенджера не используется вообще**: `apps/channels/max/parser.py:72-158` носит `attachments` непрозрачно, тип `location` нигде не разбирается; кнопки `request_location` нет ни в одной клавиатуре (есть только `cb:request_contact` — телефон).
Легаси `legacy_maxbot/handlers/contacts.py` содержит `yandex_maps_link` и хардкод «г. Пенза, ул. Пушкина, 45» — код мёртв, исключён из линтеров как «read-only migration reference», ни один модуль из `apps/` его не импортирует (**VERIFIED**).

⇒ **INFERRED**: «Пенза» работает потому, что все тенанты забэкфиллены в «Пенза» и модель обычно возвращает именно это слово. Второй город, «Пенза.» с точкой, «пенза» с опечаткой или «Питер» вместо «Санкт-Петербург» дадут пустую выдачу молча.

### 4.5. Поиск по городу на бэкенде

**VERIFIED**: его нет. Есть только гео-сортировка по расстоянию, если клиент передал `lat`/`lon` (`search/views.py:52-58,175-180`; `users/specialists_api.py:34-39` и bounding box `:463-464`). Город фигурирует в одном месте — `ai/concierge_factory.py:120`, берётся из профиля клиента в контекст консьержа и на выборку мастеров не влияет.
Задача **DRF-961** «Auto-populate or validate `Tenant.city`» (Todo, без ассайни) прямо признаёт, что поля на бэкенде нет.

---

## 5. Онбординг салона — что уже есть

| Кусок | Где | Работает? |
|---|---|---|
| Создание тенанта | `apps/tenancy/management/commands/create_tenant.py` (бот) | да, вручную |
| Провижининг администратора салона | `users/management/commands/provision_salon_admin.py` (Ayla, DRF-1062) | **VERIFIED**, идемпотентна, отказывается воскрешать отозванный доступ. Телефон — аргумент, не литерал в репозитории |
| Инвайт **мастера** | `apps/admin_api/views_invite.py` → `POST /api/v1/admin/masters/invite/` | **да, живой**: в одной транзакции `CatalogMaster` + `WorkingHours` (пресет Пн–Пт 10–19) + `MasterService` + аудит, затем DM в MAX с диплинком. Идемпотентность 7 дней, токен одноразовый (`CatalogMaster.invite_token`, `invite_expires_at`) |
| Приём инвайта мастером | `apps/master_api/urls.py:15-18`, экран `MasterOnboardingScreen.tsx` | да |
| Инвайт **администратора/ресепшена** салона | — | **нет**, явно вырезано из скоупа |
| Коды приглашения **салона** | — | **нет нигде** (поиск по `invite|invitation|приглашен` в бэкенде даёт 0 попаданий вне несвязанного докстринга) |
| «Салонный бот» для self-employed | `apps/identity/services/solo_onboarding.py` | **МЁРТВ**: `create_solo_provider()` (`:256`) не вызывается ниоткуда, кроме тестов. Из модуля в проде живёт только read-only признак `is_solo_provider` |
| Глобальный маркетплейс-бот (онбординг без тенанта) | `apps/channels/max/global_onboarding.py` | за флагом `GLOBAL_BOT_ONBOARDING` (default `false`) |
| Салонная поверхность (DRF-1061) | дерево `ai-bot-platform-drf1061` | **активно строится прямо сейчас** |
| Салонный админ-API | `tenants/urls.py` (Ayla) | расписание, день салона, ручная запись, отзыв доступа. Каталога, профиля салона и отзывов там **нет** |

---

## 6. Кто хозяин данных — сводная таблица

| Тема | Где физически лежит | Как синхронизируется | Детектор расхождения | Кто создаёт | Флаг, переключающий источник |
|---|---|---|---|---|---|
| **Услуги** | **В обоих.** Канон — Ayla (`SalonService`/`SpecialistService`); зеркало — бот (`CatalogService`). Первоисточник данных — **YClients**, через `mysite` | Ayla→бот: beat каждые 15 мин (`catalog_sync_every_15min`) + событие `service.updated` (бампает мёртвый `cache_version`). YClients→Ayla: **разовый CSV руками** | Только по рёбрам `MasterService` и только при полном снапшоте (`sync.py:211-214`). По услугам и мастерам — **нет** | Внешний источник (YClients) → импорт → **человек подтверждает** каждый черновик | `BOOKING_VIA_AYLA_REST` — гасит `_matched_services` (`discovery.py:211`) и весь booking-провайдер |
| **Отзывы** | **В Ayla** — текст и канон (`Review`). **В боте** — производные признаки в `ClientProfile` и *отдельная* локальная оценка `BookingRequest.rating`, которая наружу не уходит | Ayla→бот: событие `review.created` (без текста, PII §7). бот→Ayla: **никак** | нет | Клиент — но ни один из двух путей ввода сегодня не доступен | `BOOKING_VIA_AYLA_REST` (гасит `can_rate`), `BOOKING_AUTO_COMPLETE_ENABLED` (гасит саму возможность отзыва) |
| **Рейтинг** | **В трёх местах**: `Review` (истина), `SpecialistProfile.rating` (агрегат-поле), `CatalogMaster.rating` (копия копии) | `Review`→поле: синхронно при создании отзыва. Поле→бот: та же beat-синхронизация каталога | нет | **Вычисление** — но только в одну сторону и только один раз | тот же, что у отзывов |
| **Адреса** | **Нигде.** Есть строка у мастера в Ayla и строка `city` у тенанта в боте — две несвязанные сущности | никак: `Tenant.city` в боте и `SpecialistProfile.address` в Ayla не связаны и не синхронизируются | нет | Человек руками, в двух разных системах | нет |

---

## 7. Территориальные риски

| Риск | Кто там | Насколько горячо | Что делать |
|---|---|---|---|
| **`feat/booking-customer-review` — ложная тревога** | дерево `ai-bot-platform-admin` | **низкий, но перепроверить** | **VERIFIED**: имя ветки обманывает. `review` здесь = «экран **проверки** записи», не «отзыв клиента». SHA `9aa1fad`, 19.08, **1 коммит** поверх merge-base `0d03153`, в dev не влит. Пять файлов: `apps/admin_api/views_availability_slots.py` (+5, добавлено поле `timezone` в ответ `GET /api/v1/admin/booking-slots/`), его тест, `apps/miniapp/src/lib/admin-api.ts` (+60), `AdminNewBookingScreen.tsx` (+143/−13) и его тест. Моделей 0, миграций 0, новых ручек 0, флагов 0. **К теме отзывов отношения не имеет.** Территория исполнителя — админский экран новой записи |
| **Салонный бот, DRF-1061** | дерево `ai-bot-platform-drf1061`, статус In Progress, 20 SP, критический путь | **высокий** | Это **та же территория, что «онбординг салона»**. Новичку нельзя трогать `apps/admin_api/`, `apps/master_api/`, `apps/miniapp/src/screens/admin/`, `apps/tenancy/` без согласования. Онбординг каталога придётся стыковать с их поверхностью, а не строить параллельную |
| **DRF-1064 / завершение визита** | влито в `dev` 15.08, дерево `beautygo_backend-*` (окно расписания) | **средний** | Отзывы физически зависят от этого кода. Флаг `BOOKING_AUTO_COMPLETE_ENABLED` взводит **ops**, а не разработчик, и первый тик подметёт весь бэклог, начислив по нему комиссии. Взводить только вместе с `_NOT_BEFORE` и только по решению владельца |
| **Каталог: DRF-661/628/630** | ассайни **Андрей Тихонов**, статус Todo, Urgent | **высокий** | «Track C — Catalog Sync platform side» и ViewSets `/api/v1/catalog/*` — это ровно тема парсера услуг со стороны платформы. Пересечение почти полное |
| **Импортёры: DRF-304 (эпик), DRF-783** | ассайни **Иван** | **высокий** | Весь слой M1-*/M6-* (TXT/DOCX/PDF/Sheets/YClients/website/2GIS) имеет **Done-двойников** у Ивана: DRF-745/746/747/750/773/**774**/**775**/776. Backlog-задачи DRF-311/312/313/316/339/**340**/**341**/342 — старый нереконсилированный слой планирования. **Но кода этих коннекторов нет ни в боте, ни в бэкенде.** До постановки задачи новичку это надо выяснить у Ивана — иначе он либо перепишет сделанное, либо получит задачу, чьё решение лежит в репозитории, о котором он не знает |
| **Дизайн каталога: DRF-67, DRF-1180** | ассайни **beroy@bk.ru** | средний | Экран каталога и карточка мастера с отзывами — их макеты. Не рисовать свои |
| **Свободная территория** | — | — | **Reviews & Ratings целиком** (DRF-95 Urgent без ассайни и без подзадач, DRF-79, DRF-1141), **Salon Onboarding целиком** (DRF-908 и 936–941, все Backlog/Medium без ассайни), **город/гео** (DRF-961, DRF-154) |

**Отдельно про Linear.** Из семи названных задач **ни у одной нет ассайни, ни одного комментария и ни одного привязанного PR**. Поле `gitBranchName` у всех заполнено — это автогенерация Linear, а не признак работы. DRF-95 висит с 23.03 нетронутым и **пуст**: реальные review-задачи (DRF-96 Reviews API, DRF-129 форма отзыва) висят под DRF-11 и давно Done. То есть **эпик, помеченный P0/Urgent, — пустая коробка, а работа по нему уже сделана и сломалась молча.**

---

## 8. Архитектурные развилки

### 8.1. Рейтинг — производная, которую хранят полем в трёх местах

**Что сейчас (VERIFIED):** истина — строки `Review`; из них считается `AVG` и **записывается** в `SpecialistProfile.rating`; оттуда копируется в `CatalogMaster.rating`. Пересчёт происходит **ровно в одной точке кода** — при создании отзыва. Скрытие, правка и удаление отзыва рейтинг не трогают.

**Почему это неправильно по построению.** Рейтинг — чистая функция от множества видимых отзывов. Храня его полем, мы заводим рассинхронизацию не как баг, а как свойство: любое изменение множества отзывов, кроме «добавили один», немедленно делает поле ложным. Модерация (DRF-79) — именно такое изменение, и она сейчас в бэклоге как отдельная задача, никак не связанная с пересчётом.

| Вариант | Цена | Что даёт |
|---|---|---|
| **A. Оставить поле, добавить пересчёт во все точки записи** | 1–2 SP | Дёшево. Но каждая новая точка (импорт отзывов, удаление, автомодерация) обязана помнить про пересчёт. Долг растёт линейно |
| **B. Считать на лету, поле убрать** | 3 SP + риск | Правильно семантически. Ломает сортировку `order_by("-rating")` в движке рекомендаций (`recommendation_engine.py:294`) и требует агрегата в запросе — по нынешним объёмам пилота это бесплатно, на маркетплейсе — нет |
| **C. (рекомендую) Поле как материализованный кэш с единственным владельцем** | 2–3 SP | Один сервис `ratings.recalculate(specialist)`, вызываемый **из сигнала на `Review`** (`post_save`/`post_delete`) — а не из вида. Плюс management-команда сверки `ratings_drift --fix`, которую можно гонять по расписанию. Поле остаётся, но у него появляется **один** хозяин и **детектор расхождения** |

Отдельно: **`CatalogMaster.rating` в боте не должен пересчитываться никогда** — он копия, и это уже правильно. Но у копии нет детектора устаревания: `cache_version` бампается и никем не читается (это же зафиксировано в DRF-1078). Дешёвая замена — `synced_at` на строке зеркала и алерт на возраст.

**Развилка, которую надо решить владельцу, а не разработчику:** **рейтинг мастера или рейтинг салона?** Сейчас есть только первый, а онбордится — салон. Если клиент выбирает салон, а рейтинг есть только у мастеров, витрина салона будет пустой. Это влияет на модель данных, а не на вёрстку.

### 8.2. Услуги — копия или первоисточник

**Ответ на прямой вопрос: сегодня каталог салона в Ayla — это КОПИЯ, притом копия копии.** Первоисточник — YClients, боевая CRM салона. Промежуточное звено — `mysite`. Мост от YClients к Ayla — **разовый CSV-экспорт**, названный своим автором «one-shot read-only bridge».

Но структурно Ayla **готова быть первоисточником**: `SalonService.source` различает `manual`/`yclients`/`seed`, а `MasterService` в боте уже решил вопрос двойного владения через nullable внешний id. Полдороги пройдено.

Отсюда **три разных задачи, и это принципиально разные объёмы**:

| Вариант | Что означает | Цена парсера | Риск |
|---|---|---|---|
| **A. Одноразовый импорт при заведении** | Салон приносит прайс (CSV/Excel/скриншот сайта), мы разбираем его один раз, человек подтверждает, дальше **Ayla — первоисточник**, YClients забыт | **~5 SP**: остаётся дописать сопоставление с каноном и экран подтверждения. Конвейер уже есть | Салон продолжит вести прайс в YClients ⇒ через месяц данные разойдутся молча |
| **B. Постоянная синхронизация с YClients** | YClients остаётся первоисточником навсегда, Ayla зеркалит непрерывно | **~15 SP**: разбор конфликтов (кто победил при расхождении цены), политика удалений, переживание ручных правок в Ayla, лицензия YClients, детектор расхождения | Втрое дороже. Зато честно отражает реальность салона |
| **C. (рекомендую для пилота) A с якорем** | Одноразовый импорт, но **`ExternalSourceMapping` сохраняется**, и рядом стоит команда сверки «что изменилось в источнике с момента импорта» — без автоприменения | **~7 SP** | Позволяет начать с A и переехать в B, не переписывая. Якорь уже построен — `ExternalSourceMapping` ровно для этого и существует |

**Что надо решить владельцу:** будет ли салон после онбординга вести прайс **в Ayla** или **в YClients**. От этого ответа зависит, 5 SP задача или 15. Пока ответа нет, задачу «парсер услуг» нельзя честно оценить.

**Что сделать в любом из вариантов** (это не зависит от развилки):
- дописать сопоставление `external_name` → `ServiceTemplate` (нормализация уже зафиксирована контрактом C6: lower, trim, ё→е, схлопывание пробелов, снятие «кавычек» — `services/serializers.py:184-192`);
- дать confirm-потоку HTTP-ручку и экран, потому что CLI не отдашь салону;
- убрать `Service` (легаси) из пути или явно объявить его вымирающим — иначе следующая тема (отзывы) будет спотыкаться о него снова.

### 8.3. Отзывы — где им жить

**Что сейчас:** канон в Ayla, производные признаки в боте, **плюс третья независимая оценка** `BookingRequest.rating` в боте, которая никуда не уезжает. Это уже раздвоение — того же класса, что `BookingRequest` / `RemoteBookingProxy`.

| Вариант | Цена | Комментарий |
|---|---|---|
| **A. Ayla — единственный дом отзыва, бот только транспорт** | ~5 SP | Бот собирает оценку и **сразу постит** её во внутреннюю ручку Ayla; локальное поле `BookingRequest.rating` объявляется легаси и перестаёт писаться. Требует новой внутренней ручки `POST /api/v1/internal/reviews/` — её сегодня нет |
| **B. Бот — дом, Ayla зеркалит** | ~8 SP | Противоречит уже работающему потоку `review.created` (Ayla→бот) и правилу PII §7. Разворачивать поток дороже, чем достроить |
| **C. Оставить как есть** | 0 SP | Гарантированная рассинхронизация: два числа с именем «оценка визита», ни одно не является истиной |

**Рекомендация — A**, и это не вопрос вкуса: поток `review.created` из Ayla в бот **уже работает**, а обратного не существует. Достроить обратный дешевле, чем развернуть прямой.

**Три вещи, которые придётся починить в любом варианте (все VERIFIED):**
1. **`Review.service` → `null=True` + `salon_service` FK**, зеркально `Appointment`. Без этого отзыв на пилотную бронь физически невозможен.
2. **`Review.tenant` проставлять при создании**, а не разовым бэкфиллом.
3. **`is_hidden` дать писателя** (админка) **и связать со сценарием пересчёта рейтинга** — эти две задачи нельзя делать порознь.

### 8.4. Адреса — кому принадлежит место

**Что сейчас:** места нет ни у кого. Адрес есть у мастера строкой; город — у тенанта в **боте**, а не в Ayla, где живёт весь остальной канон. Это единственная тема, где зеркало есть, а оригинала нет.

| Вариант | Цена | Комментарий |
|---|---|---|
| **A. `Tenant.city` + `Tenant.address` в Ayla, бот зеркалит** | ~3 SP | Симметрично всему остальному контуру. Закрывает DRF-961. Но не решает филиалы |
| **B. Отдельная модель `SalonLocation` (филиал)** | ~8 SP | `tenant → 1..N локаций`, у каждой адрес, координаты, часы, телефон. Мастер привязывается к локации. Правильно на горизонте, избыточно для одного пилотного салона |
| **C. (рекомендую) A сейчас, B заложить в схему** | ~4 SP | Поля на `Tenant`, но **адрес — не строка, а маленький value-object** (`city`, `street`, `building`, `lat`, `lon`, `raw`), чтобы переезд в `SalonLocation` был переносом полей, а не переписыванием потребителей. Геокодирование — одна функция «строка → координаты», вызываемая при сохранении, с сохранением `raw`-ответа |

**Отдельное решение, дешёвое и важное:** город сейчас матчится точным сравнением строки, которую **вытащила из речи языковая модель**. Это ненадёжно по построению. Минимальная починка — нормализация (lower, ё→е, trim) плюс справочник синонимов на десяток городов, ~1 SP. Без неё любой второй город будет молча отдавать пустую выдачу.

---

## 9. Докстринги, противоречащие коду

Собрано в одном месте, потому что это системная проблема репозитория, а не случайность.

| Файл:строка | Что утверждает | Что на самом деле |
|---|---|---|
| `reviews/models.py:58-59` | «invariant maintained by backfill + **service layer**» | Сервисного слоя нет; `tenant` не пишется при создании |
| `notifications/outbox_handlers.py:303-306` | «The transition to completed has no writer in the codebase yet» | Устарело с 15.08 — писателей три (DRF-1064) |
| `apps/kb/tasks.py:113-116` | «Called from the C4 `sync_catalog_for_all_tenants` beat success handler — chained via `.delay()`» | В `apps/catalog/tasks.py` нет ни импорта, ни вызова. Проекция в RAG автоматически не происходит |
| `apps/catalog/models.py:234-238` | «Bayesian trust-score (#1060)» | Не существует; `review_count` не читает никто |
| `apps/catalog/models.py:1-6` | mysite как источник каталога | mysite ретайрнут (`config/settings/base.py:1302-1306`) |
| `apps/identity/services/solo_onboarding.py` | описывает поток заведения self-employed | `create_solo_provider()` вызывается только из тестов |

**Правило для новичка:** в этом репозитории докстринг — это заявка о намерении на момент написания, а не описание поведения. Проверять вызовом.

---

## 10. С чего начать новичку

Порядок — от независимого к связанному. Каждый шаг даёт работающий результат сам по себе.

**Шаг 0 (до кода, полдня). Три вопроса владельцу.** Без ответов ни одну из четырёх тем нельзя честно оценить:
1. После онбординга салон ведёт прайс **в Ayla** или **в YClients**? (§8.2 — разница 5 SP против 15)
2. Рейтинг нужен **у мастера**, **у салона** или у обоих? (§8.1 — влияет на модель)
3. Взводить ли `BOOKING_AUTO_COMPLETE_ENABLED` на пилоте и с какой даты `_NOT_BEFORE`? (§2.3 — без этого отзывы мертвы, а с этим первый тик начислит комиссии по бэклогу)

Параллельно — **выяснить у Ивана, где код DRF-774/775** (§1.6). Если он есть, половина парсерной темы уже написана и её надо не строить, а подключать.

---

**Шаг 1. Адреса салона — самая независимая тема.** ~4 SP.
Ни с кем не пересекается, никем не занята (DRF-961 свободна), не зависит от отзывов, рейтинга и каталога. Даёт салону витрину, которой сегодня нет вовсе.
Что делать: поля адреса на `Tenant` в Ayla (§8.4, вариант C), прямое геокодирование при сохранении, отдача в зеркало бота, нормализация города (~1 SP отдельно и сразу — она чинит молчаливо пустую выдачу).
Почему первым: это единственная тема, где нечего ломать. Здесь новичок изучит оба репозитория и путь синхронизации Ayla→бот на задаче, у которой нет наследия.

---

**Шаг 2. Починить отзывы там, где они уже построены.** ~3 SP.
Не строить новое — **разминировать**:
- `Review.service` → `null=True` + FK на `salon_service`, зеркально `Appointment` (§2.3, причина вторая);
- `Review.tenant` проставлять при создании (§2.5);
- тест на отзыв к брони салонного каталога — тот, которого нет и из-за которого дефект не всплыл.
Почему вторым: дёшево, снимает дефект, который иначе всплывёт на первом же живом отзыве, и заставляет разобраться в раздвоении каталога — знании, без которого дальше нельзя.
**Территория свободна** (DRF-95 без ассайни), но код трогает `appointments/` — согласовать с окном расписания.

---

**Шаг 3. Модерация отзывов + честный рейтинг, одной задачей.** ~4 SP. DRF-79 + §8.1 вариант C.
`reviews/admin.py` с действиями hide/show, писатель для `is_hidden`, и **один** сервис пересчёта, вызываемый из сигнала на `Review`, плюс команда сверки `ratings_drift`.
Почему одной задачей: модерация без пересчёта — это гарантированная ложь в рейтинге. Разделять их нельзя.

---

**Шаг 4. Парсер услуг — сопоставление с каноном.** 5 SP (вариант A) или 7 SP (вариант C), зависит от ответа на вопрос 1 шага 0.
Дописать то, чего не хватает конвейеру: `external_name` → `ServiceTemplate` по зафиксированной нормализации, с честной оценкой уверенности и явным «не знаю» вместо угадывания. Конвейер, staging-модель, идемпотентность и confirm — **уже есть и работают**, их писать не надо.
Почему четвёртым: **самая занятая территория** (DRF-661/628/630 — Андрей Тихонов; DRF-304 — Иван). Заходить сюда стоит уже разобравшись в контуре и обязательно после разговора с ними.

---

**Шаг 5. Экран онбординга каталога.** ~5 SP.
HTTP-ручки поверх `intake_*` + экран подтверждения черновиков. Отдавать салону командную строку нельзя.
Почему последним: это **прямое пересечение с DRF-1061** (салонная поверхность, In Progress, критический путь). Экран обязан жить внутри их поверхности, а не рядом. Раньше, чем DRF-1061 закроет блок 2, сюда лучше не заходить.

---

**Чего не делать сразу, как бы ни хотелось:**
- не взводить `BOOKING_AUTO_COMPLETE_ENABLED` «чтобы проверить» — первый тик подметёт бэклог и выставит счета;
- не строить `SalonLocation` и филиалы, пока салон один;
- не трогать `apps/admin_api/`, `apps/master_api/`, `apps/miniapp/src/screens/admin/`, `apps/tenancy/` без согласования с окном DRF-1061;
- не верить ни одному докстрингу из таблицы §9.
