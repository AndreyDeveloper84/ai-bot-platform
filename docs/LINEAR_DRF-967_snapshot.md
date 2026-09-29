# LINEAR SNAPSHOT: DRF-967

> Снапшот сделан 2026-08-09 главным окном для окна-исполнителя (без доступа к Linear).
> Источник: Linear GraphQL API, issue `DRF-967` (uuid `19382fe8-1cb4-45ce-a9bd-b7211f667e3c`).

## Шапка

- **Title:** Миррор данных formula-tela: декартово произведение MasterService (4 мастера × 58 услуг) — discovery не фильтрует по услуге
- **State:** In Progress (на момент выборки был Todo; переведён в In Progress этим же снапшот-проходом 2026-08-09)
- **Priority:** 2 (High)
- **Labels:** `NOW · Controlled Pilot`
- **Relations:**
  - blocks → **DRF-945** «Acceptance — Pilot Readiness Validation»
  - related → **DRF-962** «Wave 1 — P1 Discovery→Booking handoff теряет service context (stale-context dead-end)»
  - duplicate (inverse) ← **DRF-969** «Wave 1 — P1 Каталожный миррор связал каждого мастера со ВСЕМИ услугами (58/58) — discovery не фильтрует по услуге» (закрыт как дубль)

## Description (дословно)

## Симптом

Глобальный бот выдаёт **один и тот же список из 4 мастеров на любой запрос** — «классический массаж», «RF-лифтинг — Лицо/шея/декольте», без услуги вообще. Услуга не сужает выбор мастера, «need-first booking» не работает.

## Evidence (live-приёмка DRF-962, 09.08.2026, read-only проверки)

Пилотный контур `api-dev.gobeauty.site`, SHA `699639c`. Тенант formula-tela `b32a057a-56c7-4bf0-ae50-e11e76ab44be`.

Факт из БД пилота:

```
CatalogService  total: 58
CatalogMaster   total: 4
MasterService: у КАЖДОГО мастера ровно 58 связей из 58 услуг

Архипкин Денис      | services linked: 58
Сазонова Инна       | services linked: 58
Татьяна Паламарчук  | services linked: 58
Тихонова Ольга      | services linked: 58
```

Миррор налил декартово произведение master × service вместо реальных specialist-services.

* Прямые вызовы discovery: `specialization='RF-лифтинг — Лицо/шея/декольте'` → `service_id=8c393f42…` у всех 4 карточек; `specialization='классический массаж'` → `service_id=None` у всех 4.
* Косвенное подтверждение из живого диалога: мастеру «Тихонова Ольга» бот предлагает «Must Have (подмышки+ глубокое бикини)» и «RF-лифтинг — Лицо/шея» на запрос про **массаж** — прямая дезинформация клиента.

## Почему это блокер пилота

1. Выбор мастера по услуге бессмысленен — вернутся все.
2. Из-за (1) гард неоднозначности DRF-962 срабатывает почти всегда: бытовой запрос («классический массаж») матчит несколько услуг у каждого мастера → карточка остаётся без услуги → запись не стартует (см. DRF-970 про in-skill service-picker).
3. Бот предлагает мастеру услуги, которых тот реально не оказывает.

## Код (как формируются связи MasterService)

* `apps/catalog/services/sync.py:189-210` — два шага: `fetch_specialist_services` (per-tenant snapshot) → `upsert_master_services`.
* `apps/catalog/services/upserter.py:190-287` — `upsert_master_services`: sync-протокол связей; ownership определяется по `ayla_specialist_service_id` (NULL — ручные/seed-записи, non-NULL — sync). **Sync удаляет только свои записи** — ручные/seed-связи им не вычищаются.
* `apps/catalog/services/http_client.py:258` — `fetch_specialist_services`, источник снапшота услуг мастера.
* `apps/catalog/management/commands/seed_dev_formula_tela.py:168-173` — seed-команда создаёт `MasterService` по списку `service_external_ids` без `ayla_specialist_service_id` (ручные записи) — вероятный источник декартова произведения: такие связи sync удалить не может.

## Что проверить

* Определить, какой шаг налил декартово произведение: seed-команда vs upstream-данные Ayla vs логика синка (три-endpoint sync `MasterService`, PR #1128, ключ upsert `(master, service)`) — не подставляется ли весь каталог тенанта вместо specialist-services конкретного мастера.
* Ayla-сторона: что реально отдаёт specialist-services endpoint для этих 4 мастеров.
* Нужен ли backfill/очистка ложных связей на пилотном контуре перед запуском.

## Definition of Done

* У каждого мастера в мирроре только реально оказываемые услуги.
* `discover_masters(city='Пенза', specialization='классический массаж')` возвращает подмножество мастеров, а не всех.
* Регресс-тест на «мастер без запрошенной услуги не попадает в выдачу».

## Статус

**Блокер live-приёмки DRF-962 и чекпоинта GO (DRF-945).**

---

Evidence собран 2026-08-09 на пилотном контуре, все проверки read-only. Полный разбор — в комментарии к DRF-962. Дубль DRF-969 закрыт, уникальный контент перенесён сюда.

## Комментарии

### 1. Андрей Тихонов (tikhonovmaksoft) — 2026-08-09T09:29:49.885Z

Описание и заголовок пересобраны: исходный текст был повреждён кодировкой при создании (вся кириллица сохранилась как «?»). Контент объединён с дублем DRF-969 (закрыт); код-граундинг из исходного описания сохранён.

### 2. Андрей Тихонов (tikhonovmaksoft) — 2026-08-09 (добавлен этим же проходом при снятии снапшота)

Работа началась по брифу docs/IMPL_BRIEF_DRF-967.md (репо Ayla). Исполнитель без доступа к Linear — все апдейты через главное окно. Поправка по коду: миррор рёбер MasterService смержен в dev коммитом e791333 как PR #1157 (в описании/истории упоминался PR #1128 — устарело). Код-граундинг исполнителя: seed-гипотеза арифметически не подтверждается (seed создаёт 5 рёбер на 2 dev-мастеров, наблюдается ~232 на 4 реальных); главные кандидаты — MM4-матрица оператора (apps/admin_api/views_services_mapping.py:475, bulk-PUT с NULL-провенансом) и invite-seeding (apps/admin_api/views_invite.py:335-346). Вердикт — после диагностики данных на хосте.
