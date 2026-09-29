# REPORT DRF-1005 — health-check gate закрывает воронку записи

**Окно:** исполнитель (сессия Kimi Code, ai-bot-platform)
**Ветка:** `fix/drf1005-health-check-gate-pilot-toggle`
**База:** `origin/dev` @ `186448896eb4235894916a2d7950768e2d637a15` — сверено после `git fetch origin` (VERIFIED, совпадает с брифом).
**Статус:** **ЗАДЕПЛОЕНО на пилот** (merge-SHA `20e065d…`, §9) + env-переменная активна. Готов к live-приёмке владельца (§10). Done ставит главное окно после live-приёмки.

---

## 1. Что сделано (по §3 брифа)

### 3.1 Переключатель

- **Настройка `BOOKING_HEALTH_CHECK_GATE_DISABLED_TENANTS`** в `config/settings/base.py` (рядом с `BOOKING_VIA_AYLA_REST`), читается из env, по умолчанию пусто → гейт закрыт для всех (поведение по умолчанию не изменилось). VERIFIED тестами.
- **Парсинг — переиспользован строгий T-02 парсер** `apps/eventbus/ingest_allowlist.py::parse_tenant_allowlist` (образец из брифа, `EVENT_INGEST_ALLOWED_TENANTS`). В парсер добавлен keyword-параметр `setting_name` (default сохраняет прежнее поведение/сообщения для ingest). Невалидное значение → `AllowlistConfigurationError` → `ImproperlyConfigured` на загрузке settings: процесс отказывается стартовать. VERIFIED тестом `test_malformed_refuses_boot` (import-сценарий, как в `test_ingest_settings.py`).
- **Гейт:** `_service_requires_health_check` (`apps/skills/booking/skill.py`) на ветке `_booking_via_ayla()`: если активный тенант в allowlist → `False`, иначе прежнее `True`. Идентификатор тенанта — из активного `tenant_scope` через `current_tenant()` с lazy-import, паттерн DRF-997/1004 (`apps/integrations/ayla/booking_client.py::_require_tenant_id`). Нет tenant_scope → fail-closed. Невалидное значение, подкинутое мимо загрузки settings (override/reload), → fail-closed + warning-лог (не крашит ход клиента, не расширяет доступ).
- **Аудит:** каждое срабатывание «гейт выключен» пишет `logger.info("booking.health_check_gate.disabled tenant=… service=…")` **и** `write_audit("booking.health_check_gate_disabled", target="BookingSkill", payload={tenant_id, service_id})` (тенант подставляется самим `write_audit` из `current_tenant()`). VERIFIED тестом `test_gate_disabled_writes_audit`.
- **Докстринг переписан честно:** fail-closed остаётся дефолтом; allowlist — временная мера Controlled Pilot по решению владельца 2026-08-12 (вариант 3); канонический путь — `resolved_requires_health_check` по паре мастер×услуга (S3B PR-2), после его появления allowlist подлежит выводу.

### 3.2 Наблюдаемость

- `booking.pick_slot.health_check_required tenant=… master=… service=…` перед handoff (было «немым»). VERIFIED.
- `booking.confirm.health_check_required tenant=… service=…` на LLM-ветке (тоже была «немой»). VERIFIED.
- Остальные «немые» `_handoff(...)` в `skill.py` получили логи: `booking.unknown_tool`, `booking.tool_handoff` (вердикт тулза), `booking.show_masters.missing_service_context`, `booking.show_slots.missing_context`, `booking.pick_slot.confirm_failed`. Ветки с уже существующими warning-логами не трогал.

### 3.3 Текст пользователю

- Новый `_HEALTH_CHECK_HANDOFF_TEXT` = «Для этой услуги нужна консультация — передаю менеджеру, он поможет с записью.» — на обеих health-check-ветках (pick_slot и LLM confirm). `_FALLBACK_HANDOFF_TEXT` оставлен сбоям. `SCHEDULE_UNAVAILABLE_TEXT` (DRF-997) не тронут. VERIFIED тестами `test_health_check_handoff_uses_policy_text_*`.

## 2. Тесты «красный до / зелёный после»

Новые тесты (11 в `TestHealthCheckGateAllowlist`, `apps/skills/booking/tests/test_skill.py`; 4 в `apps/skills/booking/tests/test_health_gate_settings.py`):

- До реализации: **9 FAILED** (allowlist-открытие, аудит, pick_slot до подтверждения, текст ×2, settings ×4). VERIFIED прогоном.
- После: **15/15 PASS**. VERIFIED.
- Критерии приёмки §3: тенант в allowlist → `False`, pick_slot доходит до карточки «Подтверждаете?», `AdminTask` не создаётся (VERIFIED `test_pick_slot_allowlisted_tenant_reaches_confirmation`); тенант не в allowlist → прежний handoff (VERIFIED `test_pick_slot_non_allowlisted_tenant_still_handoffs` + direct-тесты); пустая/невалидная настройка → дефолт закрыт / отказ старта (VERIFIED); новый текст (VERIFIED); флаг-OFF (YClients) путь не затронут — весь файл `test_skill.py` зелёный (VERIFIED).

## 3. Локальные прогоны (VERIFIED, свои глаза)

- `pytest apps/skills/booking apps/eventbus/tests -q` — все зелёные (после `ruff format`).
- `ruff check` + `ruff format --check` по изменённым файлам — чисто; pre-commit хуки (ruff, red-zone guard, import-boundary guard, detect-secrets) — Passed на обоих коммитах.
- `mypy apps/skills/booking/skill.py` — чисто.
- `manage.py check` — 0 issues.

### Предсуществующие локальные фейлы (НЕ мой регресс — VERIFIED воспроизведением на чистом `git stash` дереве):

- `tests/smoke/test_ayla_import.py::TestAylaAllowList::test_package_sha_pinned` — падает и на базе `1864488` без моих изменений.
- `manage.py makemigrations --check --dry-run` → `InconsistentMigrationHistory` — состояние локальной dev-БД (applied-миграции в другом порядке), воспроизводится на чистой базе; в CI БД свежая.

## 4. Коммиты

- `a4cdf61` feat(booking): DRF-1005 BOOKING_HEALTH_CHECK_GATE_DISABLED_TENANTS allowlist setting
- `8fd1b73` fix(booking): DRF-1005 pilot health-check gate toggle with audit + observability

## 5. Фаза C — подготовка (read-only, VERIFIED 2026-08-12; деплой НЕ начат)

- **Место env-переменной (VERIFIED read-only ssh `taximeter@194.87.99.126`):** `/home/taximeter/ai-bot-platform-dev/.env.staging`. Стек подхватывает её через `env_file: - .env.staging` в `docker-compose.staging.yml` (4 сервиса: строки 48–49, 80–81, 91–92, 112–113 — web, worker, celery-worker, celery-beat). Там же уже живут `BOOKING_VIA_AYLA_REST` и `EVENT_INGEST_ALLOWED_TENANTS` — паттерн подтверждён. Ключа `BOOKING_HEALTH_CHECK_GATE_DISABLED_TENANTS` в `.env.staging` пока нет (grep count = 0).
- **План записи:** добавить строку `BOOKING_HEALTH_CHECK_GATE_DISABLED_TENANTS=b32a057a-56c7-4bf0-ae50-e11e76ab44be` в `.env.staging` (с .bak-копией, как уже принято на хосте — `.env.staging.bak.*` существуют).
- **Доказательство видимости процессу (требование REPLY №1):** после `up -d` — в контейнере `manage.py shell`: значение `settings.BOOKING_HEALTH_CHECK_GATE_DISABLED_TENANTS` = `frozenset({"b32a057a-…"})`; цитата будет в §9.
- **HEAD хоста сейчас:** `1864488…` (= задеплоенный merge PR #1164 = rollback-точка брифа §4). VERIFIED `git rev-parse HEAD` на хосте.
- **Деплой-цель:** merge-SHA `20e065d9031f08a9cec5d78f587375143df87364` (= `origin/dev` после merge PR #1165). Рецепт прежний (§9 REPORT_DRF-1004): bundle `1864488..origin/dev` → scp → `git fetch … :refs/tmp/drf1005` → checkout `20e065d…` → build → `up -d` проекта `ayla-bot-staging` → `/healthz/` на `127.0.0.1:8014` → логи worker'а 5 мин.
- **Smoke read-only — обе стороны (требование REPLY №1):** в скоупе тенанта `b32a057a-56c7-4bf0-ae50-e11e76ab44be` `_service_requires_health_check(tenant, "a4f31641-8d1c-4dce-bd57-aae85b4e4ef8")` → `False`; для тенанта вне allowlist → `True` (доказывает, что дефолт остался закрытым). Мутаций данных пилота не будет.
- **Rollback:** точка `1864488` (откат предразрешён); rollback конфигурации = удалить строку из `.env.staging` (→ fail-closed).

## 6. Границы (соблюдены)

`resolved_requires_health_check` не дотягивал; backend (Ayla) не трогал; DRF-988/989/997/998/1004 не затронуты (их тесты зелёные); `pii_protected_provider.no_active_scope` / `worker.subscriber_audit` / флакер DRF-999 не чинил; данных у тенанта не создавал; секретов в отчёте/коде нет.

## 7. Кандидаты в Linear (мутации — только главное окно)

1. **DRF-1005 follow-up:** вывод allowlist после появления `resolved_requires_health_check` (MasterService) — завести/связать с S3B PR-2; иначе временная мера рискует стать постоянной.
2. **Tech-debt:** `tests/smoke/test_ayla_import.py::test_package_sha_pinned` падает локально на чистой базе — проверить, зелёный ли он в CI; если локальный артефакт окружения — задокументировать.
3. Замечено: `manage.py makemigrations --check` чувствителен к состоянию локальной БД (InconsistentMigrationHistory на dev-базах со старой историей) — кандидат на заметку в runbook, не баг кода.

## 8. CI / PR

- **PR #1165** → `dev`: https://github.com/AndreyDeveloper84/ai-bot-platform/pull/1165
- **CI зелёный** (VERIFIED своими глазами, `gh pr checks 1165`, 2026-08-12):

```
pytest + ruff + mypy	pass	2m13s	.../actions/runs/31565741761/job/94017053349
replay fixtures (golden + adversarial + voice)	pass	53s	.../actions/runs/31565741793/job/94017053314
replay (bypassed via prompt-regression-accepted)	skipping	.../actions/runs/31565741793/job/94017053801
pytest + ruff + mypy	pass	2m12s	.../actions/runs/31565707250/job/94016949627
```

- Флакер `test_distinct_ips_each_get_one_audit` (DRF-999) в этих прогонах **не проявился** — ссылка на прогоны выше, если проявится при повторных запусках.
- Merge делает главное окно. Деплой (фаза C) не начат — жду секцию «GO НА ФАЗУ C» в REPLY.

### Merge (REPLY №1 — разрешён, выполнен самим окном, обычный merge НЕ squash)

- **Merge-SHA: `20e065d9031f08a9cec5d78f587375143df87364`** — `origin/dev` после merge (VERIFIED `git fetch` + `git log`).
- **Финальная цитата `gh pr checks 1165` (после merge, VERIFIED):**

```
pytest + ruff + mypy	pass	2m13s	https://github.com/AndreyDeveloper84/ai-bot-platform/actions/runs/31565741761/job/94017053349
replay fixtures (golden + adversarial + voice)	pass	53s	https://github.com/AndreyDeveloper84/ai-bot-platform/actions/runs/31565741793/job/94017053314
replay (bypassed via prompt-regression-accepted)	skipping	0	https://github.com/AndreyDeveloper84/ai-bot-platform/actions/runs/31565741793/job/94017053801
pytest + ruff + mypy	pass	2m12s	https://github.com/AndreyDeveloper84/ai-bot-platform/actions/runs/31565707250/job/94016949627
```

- Post-merge CI на `dev` (push `20e065d`): **оба прогона success** (VERIFIED `gh run view`): `ci` run 31566399405 (`pytest + ruff + mypy` — success), `replay` run 31566399384 — success.

---

## 9. Фаза C — деплой (VERIFIED, 2026-08-12 ~05:35Z UTC)

**GO:** REPLY №2, владелец дал явное GO на деплой merge-коммита PR #1165.

**Действия (все VERIFIED по выводу команд):**

1. Очередь `ingress:max_global` ДО: `pending=0`, `consumers=19`, `last-delivered-id=1786508318740-0`, `lag=0`.
2. Bundle `drf1005-deploy.bundle` (`1864488..origin/dev` → `20e065d…`): `git bundle verify` → «is okay», requires ref `1864488…` (rollback-точка). `scp → taximeter@194.87.99.126:/tmp/` — OK.
3. **Env-переменная:** бэкап `.env.staging → .env.staging.bak.20260812_drf1005`; добавлена строка `BOOKING_HEALTH_CHECK_GATE_DISABLED_TENANTS=b32a057a-56c7-4bf0-ae50-e11e76ab44be` (grep count = 1). Файл: `/home/taximeter/ai-bot-platform-dev/.env.staging`; подхват — `env_file: - .env.staging` в `docker-compose.staging.yml` для web/worker/celery-worker/celery-beat.
4. `git fetch /tmp/drf1005-deploy.bundle refs/remotes/origin/dev:refs/tmp/drf1005` → `git checkout 20e065d9031f08a9cec5d78f587375143df87364` → `git rev-parse HEAD` = `20e065d…` (до — `1864488`, он же rollback-точка).
5. `docker compose -p ayla-bot-staging … build` — **BUILD_EXIT=0** (web, worker, celery-worker, celery-beat пересобраны).
6. `up -d` — все сервисы Started; postgres/redis/minio остались `Up (healthy)`.

**Переменная видна процессу (VERIFIED, контейнер `ayla-bot-staging-web-1`, требование REPLY №1/№2):**

```
GATE_SETTING = frozenset({'b32a057a-56c7-4bf0-ae50-e11e76ab44be'})
```

**Health (VERIFIED):**

- `/healthz/` на `127.0.0.1:8014` → **HTTP 200**.
- Контейнеры: web `Up (healthy)`, worker/celery-worker/celery-beat `Up`, redis/postgres/minio `Up (healthy)`.
- Логи worker'а за 5+ мин после старта: **0 tracebacks**; вне известного шума — только предсуществующий стартовый `RuntimeWarning: Accessing the database during app initialization` (как при деплое DRF-1004, не поломка). Известный шум (`worker.subscriber_audit`, `pii_protected_provider.no_active_scope`, `events.emit.non_canonical`, `proxy_trust_risky`) отфильтрован при подсчёте.
- Очередь `ingress:max_global` ПОСЛЕ: `pending=0`, `consumers=20` (новый consumer-экземпляр worker'а), `last-delivered-id=1786508318740-0` (без изменений), `lag=0`. Зависших сообщений нет.

**Smoke read-only — обе стороны (VERIFIED, `manage.py shell` в `ayla-bot-staging-web-1`):**

```
SMOKE pilot_gate_open = False (expect False)
SMOKE other_tenant = global_bot gate = True (expect True)
SMOKE audit_row = {'tenant_id': 'b32a057a-56c7-4bf0-ae50-e11e76ab44be', 'service_id': 'a4f31641-8d1c-4dce-bd57-aae85b4e4ef8'}
```

- Тенант `b32a057a-…` → `_service_requires_health_check(tenant, "a4f31641-…")` = **False** — гейт открыт для пилота.
- Другой тенант (`global_bot`) → **True** — дефолт остался закрытым.
- `write_audit` реально пишет: запись `booking.health_check_gate_disabled` с payload `{tenant_id, service_id}` присутствует (запрошено REPLY №2 п.5; это аудит-след самой smoke-проверки, бизнес-данных пилота не создавалось, записей не создавалось, live-приёмка не имитировалась).

**Rollback:** код — `git checkout 1864488…` + rebuild (предразрешён); конфигурация — удалить строку из `.env.staging` (→ fail-closed, предразрешено). Не понадобилось.

## 10. ГОТОВО К LIVE-ПРИЁМКЕ

**Да, готово (2026-08-12 ~05:40Z UTC).** Live-приёмку в MAX делает только владелец.

**Сценарий для владельца:**

1. Написать боту → «услуга → мастер → дата → время».
2. **Тап по времени** — раньше этот шаг уходил в «Не получилось оформить запись — переключаю на менеджера» (`booking_health_check_required`); теперь ожидаем **карточку подтверждения** с услугой/мастером/временем.
3. Подтвердить → ✅ запись создана (автоматическая бронь, без handoff).
4. В логах worker'а на шаге 2 допустима строка `booking.health_check_gate.disabled tenant=b32a057a-… service=…` (аудит-след переключателя) — это НЕ сбой, а запрошенный владельцем след. Строки `booking.pick_slot.health_check_required` для пилотного тенанта быть НЕ должно; для любого другого тенанта гейт по-прежнему закрыт и текст политики («Для этой услуги нужна консультация…») остаётся корректным поведением.

**Что задеплоено:** merge-SHA `20e065d9031f08a9cec5d78f587375143df87364` (= merge PR #1165: `a4cdf61` настройка + `8fd1b73` гейт/аудит/наблюдаемость). Rollback-точка `1864488` (откат предразрешён при проблемах).

---

## 11. Живая воронка после деплоя (VERIFIED по логам worker'а, read-only)

2026-08-12 05:47:44–05:47:46 UTC, тенант `b32a057a-…` (живой пользовательский ход, не мой smoke):

```
05:47:44 booking.health_check_gate.disabled tenant=b32a057a-… service=a4f31641-…   ← гейт открылся, аудит-след есть
05:47:44 bookings.pending.created  token=79052661-… kind=confirm                  ← карточка подтверждения создана
05:47:44 skills.dispatch.result name=booking tools=['confirm_booking']
05:47:46 bookings.pending.consumed token=79052661-… kind=confirm                  ← пользователь подтвердил
```

Ранее этот шаг гарантированно уходил в `booking_health_check_required` handoff («Не получилось оформить запись…»). Теперь: гейт открылся → превью подтверждения → подтверждение потреблено за 2.5 с. Строк `booking.pick_slot.health_check_required` для пилотного тенанта в логах нет; handoff на этом ходе не было (единственная строка `marketplace.handoff.entered` в 05:47:36 — до тапа по слоту, известный класс событий, отдельный от health-check-ветки).

Повторный health через ~40 мин после деплоя: `/healthz/` → 200, 0 tracebacks в логах worker'а.
