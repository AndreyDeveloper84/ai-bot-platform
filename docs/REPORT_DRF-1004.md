# ОТЧЁТ окна-исполнителя: DRF-1004 — booking skill читает мёртвый каталог услуг

Исполнитель: окно DRF-1004 (модель Opus 5). База: `origin/dev` @ `0d94aac` (совпадение проверено при старте, VERIFIED).
Ветка: `fix/drf1004-canonical-service-catalog` (создана от `0d94aac`).
PR: **#1164** → `dev`. Коммиты: `768a85b` (клиент) → `6f9205a` (тесты) → `3390b72` (миноры ревью). HEAD: `3390b72`.
Монитор REPLY: cron `a516a5b9`, md5-проверка каждые 2 мин (нечётные минуты), state-файл `.superpowers/sdd/reply_drf1004_last.md5`. Доставка первого огня подтверждена в сессии.

Классы доказательности: VERIFIED / INFERRED / CLAIMED / UNKNOWN.

---

## 1. Что сделано (соответствие §4 брифа)

1. **`get_services()` переведён на канонический каталог** — `apps/integrations/ayla/booking_client.py:648-676`. Эндпоинт `catalog/salon-services/`, параметры `tenant=<id активного tenant_scope>` и `is_active=true`. Тенант берётся из `current_tenant()` — того же источника, что `_tenant_id_for_cache` (DRF-997). Без тенанта в скоупе — `BookingBadRequestError("tenant_scope_required")` ДО любого wire-вызова (`_require_tenant_id`, `booking_client.py:985-997`). VERIFIED (тест `test_get_services_reads_canonical_catalog`, `test_get_services_requires_tenant_scope`).
2. **Маппинг полей** — `_service_from_wire` (`booking_client.py:362-381`): цена `base_price` (канон) с fallback на легаси `price`; сравнение через `is not None`, чтобы числовой ноль не проваливался в fallback. Длительность `duration_minutes`, название `name`, категория `category` — как было. `price_min == price_max == base_price`. VERIFIED (тест `test_get_services_base_price_preferred_over_legacy_price`).
3. **Пагинация** — `_get_all_rows` (`booking_client.py:678-716`): обход по `page=N` до исчерпания `next`; advertised `count` снимается с первой страницы (с коэрсией `int(count)`); потолок `MAX_CATALOG_PAGES=100`; расхождение `count != len(rows)` или срыв потолка → `logger.warning` + `BookingUnavailableError("catalog_incomplete")`. Молча усечённый каталог невозможен. На уровне скилла это маппится в `SCHEDULE_UNAVAILABLE_TEXT` (честный «сервис недоступен, повторите»), а не в stale-context. VERIFIED (тесты `test_get_services_walks_pagination` — 58 услуг при странице 25 → 58 объектов, страницы [1,2,3]; `test_get_services_incomplete_catalog_raises`).
4. **Ветка `specialist_id`** — переведена: `catalog/specialist-services/?tenant=<tid>&specialist=<id>&is_active=true` → join по `salon_service` UUID с полным каталогом salon-services; возвращаются полные `AylaService`. На пути Ayla ветка СЕЙЧАС НЕ ИСПОЛЬЗУЕТСЯ (VERIFIED: единственный вызов на пути скилла — `apps/skills/booking/skill.py:360`, `yclients.get_services()` без аргументов; адаптер `provider.py:117-127` пробрасывает `staff_id`, но никто его не передаёт). Ветку перевёл, а не убил — honest-fail оставлять не потребовалось. VERIFIED (тест `test_get_services_for_specialist_uses_bookable_edges`).
5. **Потребители каталога** — `provider.py:117-124` (адаптер) и `skill.py:360/380` (префетч + `build_service_lookup`) не менялись: интерфейс `get_services()` и форма `AylaService` сохранены. Матчинг по названию (substring/ambiguous в `TestShowMastersFlow`) — тесты зелёные. VERIFIED (суиты ниже).
6. **Кэш** — нового кэширования каталога НЕ добавлено (бриф §4.6 — условный пункт). Существующие slot/dates-кэши DRF-997 с тенантом в ключе не тронуты. VERIFIED (diff).

**Route-table mirror** (`test_contract_route_table.py`): строки `internal/services/` и `internal/specialists/{id}/services/` заменены на `internal/catalog/salon-services/` и `internal/catalog/specialist-services/` (биекция клиент↔таблица сохранена; `_exercise_booking` теперь работает в `tenant_scope`). VERIFIED.

## 2. Красный до / зелёный после (VERIFIED)

- Красное состояние зафиксировано прогоном до фикса: `test_get_services_reads_canonical_catalog` падал на старом коде (`assert '/api/v1/internal/services/' == '/api/v1/internal/catalog/salon-services/'`). Остальные новые тесты на старом коде падают по построению (старый код: нет tenant-фильтра, нет обхода страниц, читает `price`, specialist-ветка бьёт в мёртвый nested-path, skill-тест получает 404 от handler'а → handoff).
- Зелёное состояние после фикса (локаль, `config.settings.local`):
  - `apps/integrations/ayla/tests/` — все зелёные (вкл. `test_booking_client.py` 55, `test_contract_route_table.py` 3);
  - `apps/skills/booking/tests/` — все зелёные (вкл. `test_skill.py` 75, новый `test_pick_slot_with_canonical_catalog_service_accepted`);
  - `ruff check` — чисто; `mypy apps/integrations/ayla/booking_client.py` — чисто; pre-commit хуки (ruff, secrets, AST-гарды) — Passed на всех трёх коммитах.

## 3. CI

- `gh pr checks 1164` на HEAD `3390b72` (2026-08-11, VERIFIED — прогон виден своими глазами через `--watch`):

```
pytest + ruff + mypy	pass	2m26s	https://github.com/AndreyDeveloper84/ai-bot-platform/actions/runs/31531119536/job/93911109656
replay fixtures (golden + adversarial + voice)	pass	45s	https://github.com/AndreyDeveloper84/ai-bot-platform/actions/runs/31531119531/job/93911109480
replay (bypassed via prompt-regression-accepted)	skipping	0	https://github.com/AndreyDeveloper84/ai-bot-platform/actions/runs/31531119531/job/93911111202
pytest + ruff + mypy	pass	3m22s	https://github.com/AndreyDeveloper84/ai-bot-platform/actions/runs/31531072727/job/93910960968
```

- Все проверки **pass** (replay — штатный skip через label `prompt-regression-accepted`). Флакер `test_distinct_ips_each_get_one_audit` в этих прогонах не проявился; красных проверок нет.

## 4. Независимое ревью ветки (VERIFIED)

Проведено ревью всего diff'а (`0d94aac..HEAD`) отдельным ревьюером: **Approved**, spec-compliant по всем пунктам §4, критичных/важных находок нет. Миноры, закрытые коммитом `3390b72`: docstring `BookingUnavailableError` (catalog_incomplete не трипает брейкер — осознанно), zero-price guard, коэрсия `count`. Не закрыто (осознанно): `page_size` не запрашивается (3 запроса на 58 услуг — приемлемо); UUID-сравнение без `.lower()` (обе стороны — DRF UUIDSerializer, нижний регистр гарантирован).

Два ⚠️ ревьюера по апстрим-контракту разрешены брифом: поддержка `is_active`/`specialist` фильтров и `filterset_fields` viewset'ов — VERIFIED главным окном в §3 брифа (`services/internal_api.py:41-60`) + рантайм-замеры (`is_active=true` → count=58). Клиентская перестраховка по row-level `is_active` НЕ добавлена: она бы ломала инвариант count==len(rows) (false `catalog_incomplete`). INFERRED — если у тенанта появятся `is_active=false` услуги, параметр фильтра отработает на backend.

## 5. Границы (VERIFIED по diff'у)

- Backend не тронут: изменены только `apps/integrations/ayla/booking_client.py` + 3 тестовых файла бота.
- Фиксы DRF-988/989/997/998 не затронуты (429-ретраи, slot/dates кэш, дедуп вебхуков — вне diff'а).
- Данные пилота не создавались; мутаций Linear не было; секреты в отчёте/коде отсутствуют.
- Деплой (фаза C) НЕ выполнялся — жду секцию «GO НА ФАЗУ C» в REPLY. Rollback-точка `0d94aac` подтверждена.

## 6. Известные ограничения

- `catalog_incomplete` на живом тенанте теоретически возможен при конкурентной вставке услуги между страницами (неуникальная сортировка upstream) — пользователь увидит честный «сервис недоступен», в логах `booking_client.catalog_incomplete`. Класс риска тот же, что задокументирован в `apps/catalog/services/http_client.py` (`snapshot_incomplete`).
- Ветка `specialist_id` покрыта контрактными тестами, но не прогнана против живого backend (на пути Ayla не используется).

## 7. Кандидаты в Linear (→ главное окно)

1. **Депрекейтнуть/удалить легаси-эндпоинты** `internal/services/` и `internal/specialists/<id>/services/` на backend (queryset пуст глобально) или сделать их fail-loud — сейчас это мина для любого следующего потребителя.
2. **Задокументировать фильтры** `tenant/is_active/specialist` канонических catalog-эндпоинтов в контрактном доке: `docs/CATALOG_INTERNAL_API_CONTRACT.md` в репо бота ОТСУТСТВУЕТ (UNKNOWN где живёт актуальная версия), ревьюер не смог сверить фильтры с документом — только с брифом.
3. **Общий pagination-helper** для `booking_client._get_all_rows` и `catalog/services/http_client._fetch_all_checked` — две параллельные реализации обхода страниц с разной семантикой (raise vs complete-flag); стоит сойтись на одной.
4. **Прогон specialist-branch против staging** при первом реальном потребителе `get_services(specialist_id=...)` (сейчас покрытие только моками).

---

## 8. Merge + подготовка фазы C (2026-08-11, по REPLY №1)

**Merge:** PR #1164 влит в `dev` обычным merge (НЕ squash), выполнен этим окном по разрешению REPLY №1. Merge-SHA на `dev`: **`186448896eb4235894916a2d7950768e2d637a15`** (`1864488 Merge pull request #1164`). VERIFIED (`git rev-parse origin/dev` после fetch).

**Финальная цитата `gh pr checks 1164` (после merge, VERIFIED):**

```
pytest + ruff + mypy	pass	2m26s	https://github.com/AndreyDeveloper84/ai-bot-platform/actions/runs/31531119536/job/93911109656
replay fixtures (golden + adversarial + voice)	pass	45s	https://github.com/AndreyDeveloper84/ai-bot-platform/actions/runs/31531119531/job/93911109480
replay (bypassed via prompt-regression-accepted)	skipping	0	https://github.com/AndreyDeveloper84/ai-bot-platform/actions/runs/31531119531/job/93911111202
pytest + ruff + mypy	pass	3m22s	https://github.com/AndreyDeveloper84/ai-bot-platform/actions/runs/31531072727/job/93910960968
```

**Фаза C, read-only подготовка (VERIFIED):**

- Bundle: `C:\Users\user\PycharmProjects\ai-bot-platform\drf1004-deploy.bundle` — `0d94aac..origin/dev` → содержит `refs/remotes/origin/dev` = `1864488…`; `git bundle verify` → **«drf1004-deploy.bundle is okay»**, requires ref `0d94aac…` (rollback-точка).
- Rollback-точка на хосте перепроверена read-only ssh (`taximeter@194.87.99.126`): HEAD `/home/taximeter/ai-bot-platform-dev` = **`0d94aaca23f78cdd3a3939ab15df5f9756f7de9d`** (= rollback-точка брифа §5, = задеплоенный merge PR #1163). Откат предразрешён брифом.
- Рецепт деплоя подтверждён по §7–§9 `REPORT_DRF-989-997-998.md` (scp bundle → fetch в refs/tmp → checkout SHA → build → `up -d` проекта `ayla-bot-staging` → `/healthz/` на `127.0.0.1:8014` → логи worker'а 5 мин → smoke read-only в контейнере: `get_services()` в скоупе тенанта `b32a057a-56c7-4bf0-ae50-e11e76ab44be` возвращает 58 услуг, среди них `a4f31641-8d1c-4dce-bd57-aae85b4e4ef8`, цена ненулевая).

**Деплой НЕ начат** — жду секцию «GO НА ФАЗУ C» в REPLY (монитор активен, cron `a516a5b9`).

---

## 9. Фаза C — деплой (VERIFIED, 2026-08-12 ~04:00Z UTC)

**GO:** REPLY №2, владелец дал явное GO на деплой `1864488`.

**Действия (все VERIFIED по выводу команд):**

1. Очередь `ingress:max_global` ДО: `pending=0`, `consumers=18`, `last-delivered-id=1786475722806-0`, `lag=0`.
2. `scp drf1004-deploy.bundle → taximeter@194.87.99.126:/tmp/` — OK.
3. На хосте `/home/taximeter/ai-bot-platform-dev`: `git fetch /tmp/drf1004-deploy.bundle refs/remotes/origin/dev:refs/tmp/drf1004` → `git checkout 186448896eb4235894916a2d7950768e2d637a15` → `git rev-parse HEAD` = `1864488…` (ранее был `0d94aac`, он же rollback-точка).
4. `docker compose -p ayla-bot-staging … build` — **BUILD_EXIT=0** (web, worker, celery-worker, celery-beat пересобраны).
5. `up -d` — все сервисы Started; postgres/redis/minio остались healthy.

**Health (VERIFIED):**

- `/healthz/` на `127.0.0.1:8014` → **HTTP 200**.
- Контейнеры: web `Up (healthy)`, worker/celery-worker/celery-beat `Up`, redis/postgres/minio `Up (healthy)`.
- Логи worker'а за 10 мин после старта: **0 tracebacks**; вне списка известного шума — только предсуществующий стартовый `RuntimeWarning: Accessing the database during app initialization` (шум инициализации Django, не поломка). Известный шум (`worker.subscriber_audit`, `pii_protected_provider.no_active_scope`, `events.emit.non_canonical`, `proxy_trust_risky`) отфильтрован при подсчёте.
- Очередь `ingress:max_global` ПОСЛЕ: `pending=0`, `consumers=19` (новый consumer-экземпляр воркера), `last-delivered-id=1786475722806-0` (без изменений), `lag=0`. Зависших сообщений нет.

**Smoke read-only (VERIFIED, контейнер `ayla-bot-staging-web-1`, `manage.py shell`, мутаций данных пилота нет):**

```
GET …/api/v1/internal/catalog/salon-services/?tenant=b32a057a-…&is_active=true&page=1 → 200
GET …&page=2 → 200
GET …&page=3 → 200
SMOKE services_count = 58
SMOKE allowed_service_ids_nonempty = True size = 58
SMOKE target_present = True
SMOKE target_title = УЗ-кавитация — 1 зона
SMOKE target_price_min = 1000.0
```

- `get_services()` в скоупе тенанта `b32a057a-56c7-4bf0-ae50-e11e76ab44be` вернул **58 услуг**; пагинация отработала вживую (дефолтная страница upstream < 58, клиент прошёл page=1→2→3).
- Целевая услуга `a4f31641-8d1c-4dce-bd57-aae85b4e4ef8` («УЗ-кавитация — 1 зона») присутствует, **цена 1000.0 — ненулевая** (маппинг `base_price` работает).
- `allowed_service_ids` на уровне скилла непусто (58) — условие, которое ранее валило любой `pick_slot` в `unknown_service`, снято.

**Rollback-точка `0d94aac`** — не понадобилась; откат остаётся предразрешённым.

## 10. ГОТОВО К LIVE-ПРИЁМКЕ

**Да, готово (2026-08-12 ~04:10Z UTC).** Live-приёмку в MAX делает только владелец.

**Сценарий для владельца:**

1. Написать боту → пройти «услуга → мастер → дата».
2. Тапнуть по дате → пикер «Выберите время:» с кнопками времени.
3. **Тапнуть по времени** — ранее этот шаг стабильно давал «Контекст записи устарел» (`booking.pick_slot.unknown_service`); теперь ожидаем **карточку подтверждения** с услугой/мастером/временем.
4. Подтвердить → ✅ запись создана.
5. В логах worker'а на шаге 3 НЕ должно быть `booking.pick_slot.unknown_service`; допустимы лишь известные шумы из §9.

**Что задеплоено:** SHA `186448896eb4235894916a2d7950768e2d637a15` (= merge PR #1164: `768a85b` клиент + `6f9205a` тесты + `3390b72` миноры ревью). Rollback-точка `0d94aac` (откат предразрешён при проблемах).
