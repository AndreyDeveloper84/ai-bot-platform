# REPORT окна-исполнителя: DRF-980 + DRF-1007 — разблокировка эксплуатации пилота

**Окно:** исполнитель по брифу `IMPL_BRIEF_DRF-980-1007.md`
**Репозиторий:** `C:\Users\user\PycharmProjects\ai-bot-platform`
**Ветка:** `fix/drf980-1007-pilot-operations` (создана от `origin/dev` @ `20e065d9031f08a9cec5d78f587375143df87364` — сверено после `git fetch`, VERIFIED)
**Статус:** **ЗАДЕПЛОЕНО на пилот** (merge-SHA `d44ea17…`, §№3) + env-переменная активна в web/worker/celery-*. Готов к live-приёмке владельца. Done ставит главное окно после live-приёмки.

---

## №3 (2026-08-12): фаза C — деплой выполнен (GO = REPLY №2)

**Действия (все VERIFIED по выводу команд):**

1. Очередь `ingress:max_global` ДО: `pending=0`, `consumers=20`, `last-delivered-id=1786513666534-0`, `lag=0`.
2. Bundle `drf980-1007-deploy.bundle` (`20e065d..origin/dev` → `d44ea17…`): scp → `/tmp/` — OK. `git fetch` из bundle → `git checkout d44ea1717f3a3bba2d9f8ebbd8b43b600cb903a7` → `git rev-parse HEAD` = `d44ea17…` (до — `20e065d9`, он же rollback-точка).
3. **Env-переменная:** бэкап `.env.staging → .env.staging.bak.20260812_drf980-1007`; добавлена строка `BOOKING_NO_PREPAYMENT_TENANTS=b32a057a-56c7-4bf0-ae50-e11e76ab44be` (grep count = 1).
4. `docker compose -p ayla-bot-staging -f docker-compose.yml -f docker-compose.staging.yml -f docker-compose.staging.local.yml build` → **BUILD_EXIT=0** (web, worker, celery-worker, celery-beat); `up -d` → **UP_EXIT=0**, все сервисы Started. (Первая попытка с одним `-f docker-compose.staging.yml` упала на `invalid compose project` — ничего не пересобралось и не перезапустилось; каноничный набор из трёх `-f` взят из REPORT DRF-989-997-998.)
5. **Health:** `/healthz/` на `127.0.0.1:8014` → **HTTP 200** (первый запрос сразу после `up -d` дал 000 — web ещё стартовал; через ~25 с — 200). Контейнеры: web `Up (healthy)`, worker/celery-worker/celery-beat `Up`, postgres/redis/minio `Up (healthy)`.
6. **Переменная видна ВСЕМ процессам (VERIFIED, `manage.py shell` в каждом контейнере):**

```
web:           NO_PREPAY = frozenset({'b32a057a-56c7-4bf0-ae50-e11e76ab44be'})
worker:        NO_PREPAY = frozenset({'b32a057a-56c7-4bf0-ae50-e11e76ab44be'})
celery-worker: NO_PREPAY = frozenset({'b32a057a-56c7-4bf0-ae50-e11e76ab44be'})
celery-beat:   NO_PREPAY = frozenset({'b32a057a-56c7-4bf0-ae50-e11e76ab44be'})
```

7. **Smoke read-only (VERIFIED, worker-контейнер, скрипт `/tmp/smoke_drf980_1007.py`):** все мутации внутри транзакции с откатом — в БД пилота ничего не осталось; пилотный тенант только читался. Вывод:

```
SMOKE1007 setting = frozenset({'b32a057a-56c7-4bf0-ae50-e11e76ab44be'})
SMOKE1007 pilot = False (expect False)
SMOKE1007 other = True (expect True) [global_bot]
SMOKE1007 explicit_true_overrides = True (expect True)
SMOKE980 resolve state = idle (expect idle)
SMOKE980 resave state = idle (expect idle)
SMOKE980 first_of_two state = human_handoff (expect human_handoff)
SMOKE980 last_of_two state = idle (expect idle)
SMOKE980 rollback OK — no synthetic rows persisted
```

8. **Логи worker'а 5+ мин после старта:** 0 tracebacks; вне известного шума (`worker.subscriber_audit`, `pii_protected_provider.no_active_scope`, `events.emit.non_canonical`) ошибок нет.
9. Очередь `ingress:max_global` ПОСЛЕ: `pending=0`, `consumers=21` (новый экземпляр worker'а), `last-delivered-id=1786513666534-0` (без изменений) — зависших сообщений нет.

**Rollback:** код — `git checkout 20e065d…` + rebuild (предразрешён); конфигурация — убрать строку из `.env.staging` (→ прежнее поведение с предоплатой, предразрешено). Не понадобилось.

## №3.1 ГОТОВО К LIVE-ПРИЁМКЕ

**Да, готово (2026-08-12).** Live-приёмку в MAX делает только владелец.

**Сценарий для владельца (две части):**

1. **DRF-1007 (без предоплаты):** записаться через бота (услуга → мастер → дата → время → подтвердить). Ожидание: запись создаётся и в админке/backend видна в статусе `confirmed`, а НЕ `awaiting_payment`; строки Payment со `status=pending` без `provider_payment_id` быть не должно. Допустимый лог-след в worker: `booking.no_prepayment.applied tenant=b32a057a-… payment_required=False` — это аудит переключателя, не сбой.
2. **DRF-980 (возврат бота):** эскалировать к оператору (написать «оператор»/«человек») → в админке закрыть задачу (статус RESOLVED или CANCELLED) → написать боту снова. Ожидание: бот отвечает в том же диалоге. Раньше диалог оставался немым навсегда.

**Что задеплоено:** merge-SHA `d44ea1717f3a3bba2d9f8ebbd8b43b600cb903a7` (PR #1166: `28e0321` DRF-980 + `9b32fbc` DRF-1007). Rollback-точка `20e065d` (откат предразрешён при проблемах).

---

## №2 (2026-08-12): CI зелёный, merge выполнен, фаза C подготовлена read-only

**CI (VERIFIED, цитата `gh pr checks 1166`):**

```
pytest + ruff + mypy	pass	2m16s	https://github.com/AndreyDeveloper84/ai-bot-platform/actions/runs/31571569786/job/94034489282
replay fixtures (golden + adversarial + voice)	pass	53s	https://github.com/AndreyDeveloper84/ai-bot-platform/actions/runs/31571569818/job/94034489852
replay (bypassed via prompt-regression-accepted)	skipping	0	https://github.com/AndreyDeveloper84/ai-bot-platform/actions/runs/31571569818/job/94034490618
pytest + ruff + mypy	pass	2m18s	https://github.com/AndreyDeveloper84/ai-bot-platform/actions/runs/31571544958/job/94034418048
```

Флакер DRF-999 не проявился — перезапуск не понадобился.

**Merge (разрешён REPLY №1, обычный merge, не squash):** merge-SHA **`d44ea1717f3a3bba2d9f8ebbd8b43b600cb903a7`** («Merge pull request #1166»), `origin/dev` обновлён: `20e065d..d44ea17` (VERIFIED `git fetch` + `git log`).

**Фаза C, read-only подготовка (VERIFIED):**

- Bundle `drf980-1007-deploy.bundle` (`20e065d..origin/dev`) создан в корне репо, как `drf1004-deploy.bundle` / `drf1005-deploy.bundle` ранее. `git bundle verify` → «is okay», требует `20e065d…`, несёт `d44ea17…`.
- Rollback-точка на хосте (read-only ssh `taximeter@194.87.99.126`): `/home/taximeter/ai-bot-platform-dev` сейчас на `20e065d9` (= rollback-точка = текущий деплой), объект `20e065d…` присутствует (`git cat-file -t` → `commit`).
- `grep -c BOOKING_NO_PREPAYMENT_TENANTS .env.staging` → `0`: переменной на хосте пока нет, добавлю в фазе C (`BOOKING_NO_PREPAYMENT_TENANTS=b32a057a-56c7-4bf0-ae50-e11e76ab44be`, с .bak-копией).

**Деплой НЕ начинал** — жду секцию «GO НА ФАЗУ C» в REPLY. Монитор REPLY активен.

---

## №1 (2026-08-12): реализация и тесты

### DRF-980 — закрытие задачи возвращает бота (коммит `28e0321`)

**Что сделано:**

1. `AdminTaskAdmin.save_model` больше не штампует `resolved_at` руками. Переход в `RESOLVED` идёт через `resolve_admin_task`, в `CANCELLED` — через новый `cancel_admin_task` (status + `resolution_note`, `resolved_at` остаётся NULL — семантика «работа не выполнена» сохранена, аудит `handoff.cancelled`). Сервис вызывается внутри `tenant_scope(obj.tenant)` — тенант берётся из задачи, не из запроса (админка кросс-тенантная, VERIFIED: `get_queryset` → `all_tenants`).
2. **Ловушка идемпотентности — решение.** Тонкое место: form-bound `obj` уже несёт НОВЫЙ статус, поэтому сервис вызывается на свежем инстансе из БД (`AdminTask.all_tenants.get(pk=...)`), иначе проверка `status == RESOLVED` в сервисе сработала бы на объекте формы и вышла бы молча. Сам ранний выход в `resolve_admin_task` расширен: на уже закрытой задаче он теперь вызывает `release_conversation_to_bot(task)` — инвариант «закрытая задача не держит диалог» лечится, а не игнорируется. Обоснование: отдельная проверка только в админке оставила бы дыру для любого другого вызывающего (shell, будущий операторский эндпоинт); лечение на стороне сервиса закрывает класс целиком. Это ровно тот случай, на котором главное окно обожглось вручную — покрыт тестом `test_resave_of_resolved_task_unmutes`.
3. **`CANCELLED` возвращает бота** — тест `test_returns_conversation_to_bot_without_resolved_at`.
4. **Массовые действия.** В `AdminTaskAdmin` bulk-действий над статусом НЕТ (VERIFIED чтением `apps/handoff/admin.py`: атрибут `actions` не определён, действует дефолтный `delete_selected`, который статус не трогает). Добавлять не стал — вне объёма брифа.
5. **Диагностика.** Лог в `save_model`: `handoff.admin_close actor=<user pk> task=… conversation=… from=<old> to=<new> tenant=…`. Плюс сервисные: `handoff.resolved` / `handoff.cancelled` / `handoff.conversation_release.ok` / `…deferred reason=another_open_task`.

**Изменение поведения сервиса (обоснование):** возврат диалога в `IDLE` теперь идёт через `release_conversation_to_bot` — условный `update(state=HUMAN_HANDOFF → IDLE)` с защитой «другая открытая задача на этом диалоге держит mute». Раньше `resolve_admin_task` сбрасывал состояние безусловно: при двух открытых задачах на одном диалоге закрытие первой преждевременно включало бота. Теперь диалог отпускает последняя закрываемая задача (тест `test_conversation_released_only_when_last_task_closes`).

**Краевой случай (зафиксирован, не чинил — вне брифа):** рекласс `RESOLVED → CANCELLED` через админку сохранит старый `resolved_at` (поле readonly, сервис отказ отменять resolved логирует `handoff.cancel.refused`). Кандидат в Linear №2 ниже.

**Тесты «красный до / зелёный после» (VERIFIED, прогон локально):**

- Новый файл `apps/handoff/tests/test_admin.py`, 8 тестов. На старом коде (stash исходников): 6 FAILED из 8 (все ключевые: resolve/cancel/re-save/cross-tenant/multi-task). После фикса: 8/8 зелёные; весь пакет `apps/handoff/` — 28/28.

### DRF-1007 — пилот без предоплаты (коммит `9b32fbc`)

**Что сделано:**

1. **Настройка `BOOKING_NO_PREPAYMENT_TENANTS`** в `config/settings/base.py` (сразу после блока DRF-1005), читается из env, по умолчанию пусто → поведение не меняется (`payment_required=True`). Парсер — тот же `parse_tenant_allowlist` (T-02): кривое значение → `ImproperlyConfigured` на старте, молча пустого allowlist не бывает.
2. **Прокидка:** новый `_resolve_payment_required(tenant, payload)` в `apps/skills/booking/tools.py`. Приоритет: явный `payment_required` в payload > настройка > дефолт `True`. Вызов в `execute_confirm` (бывшая строка `bool(payload.get("payment_required", True))`). Лог при срабатывании: `booking.no_prepayment.applied tenant=… payment_required=False`; fail-closed при кривом значении, подкинутом мимо загрузки настроек (`booking.no_prepayment.allowlist_malformed` → `True`).
3. **Путь переноса записи (решение, обоснование).** Flag-ON (конфигурация пилота) использует нативный Ayla-move (`_execute_reschedule_ayla`) — appointment сохраняется, включая платёжные условия, `create_record` там не вызывается: менять нечего. Legacy flag-OFF ветка (`tools.py`, cancel+create) теперь тоже идёт через `_resolve_payment_required`: перенесённая запись пилотного тенанта не должна молча становиться `awaiting_payment` — напоминания приходят только по CONFIRMED, обоснование то же, что у создания.
4. MiniApp уже шлёт `payment_required=False` (`apps/miniapp_api/views.py:846` — default FALSE, явный pass-through, VERIFIED чтением) — не трогал.

**Свип всех мест создания брони (бриф §3.3), VERIFIED grep + чтением:**

- `tools.py` confirm — исправлено (коммит `9b32fbc`).
- `tools.py` legacy-reschedule (flag-OFF cancel+create) — исправлено там же.
- `miniapp_api/views.py:747` — MiniApp-эндпоинт, `payment_required` уже явный параметр с дефолтом `False`; не трогал.
- `provider.py:199` — сам адаптер, пробрасывает параметр как есть; не трогал.
- `integrations/yclients/tasks.py:146` — legacy-push BookingRequest → YClients напрямую (не через Ayla); статус `awaiting_payment` — концепт backend'а Ayla, у legacy-клиента YClients параметра `payment_required` нет. Вне дефекта, не трогал.

**Тесты (VERIFIED, прогон локально):**

- `test_no_prepayment_settings.py` (4 теста, зеркало DRF-1005): дефолт пусто; unset env → пусто; валидный CSV парсится; кривое значение → отказ загрузки.
- `test_no_prepayment.py` (7 тестов): тенант в allowlist → на шину уходит `payment_required=False` (через реальный `AylaYClientsAdapter` + in-memory fake, assert по kwargs `create_appointment`); вне allowlist → `True`; пустой allowlist → `True`; явный `True` в payload перекрывает allowlist; явный `False` работает вне allowlist; кривая настройка → fail-closed `True`.
- Красный до: stash исходников → 4 FAILED (settings) + collection ERROR (helper отсутствовал). Зелёный после: 11/11; весь пакет `apps/skills/booking/` — все тесты зелёные; `apps/bookings/` + `apps/booking/` — зелёные.

### Фаза C — подготовка (деплой НЕ выполнялся, жду GO)

- Рецепт известен (bundle → scp → checkout → rebuild → `up -d` проекта `ayla-bot-staging`), rollback-точка `20e065d`.
- **Место env-переменной** (по REPORT DRF-1005 §8, VERIFIED тем окном read-only ssh): `/home/taximeter/ai-bot-platform-dev/.env.staging`, подхват через `env_file` в `docker-compose.staging.yml` для web/worker/celery-worker/celery-beat. Добавлю `BOOKING_NO_PREPAYMENT_TENANTS=b32a057a-56c7-4bf0-ae50-e11e76ab44be` с .bak-копией. Доказательство видимости — `manage.py shell` в web И worker контейнерах (требование брифа §4 учтено).
- Smoke DRF-980 — на синтетических объектах в shell, без данных пилотного тенанта.

### Монитор REPLY

Поставлен при старте окна: проверка md5 `REPLY_DRF-980-1007.md` каждые 5 минут (cron-задача сессии, state в `scratchpad/.reply_980_1007_md5`). **Доставка подтверждена (VERIFIED):** секция №1 REPLY обнаружена монитором и прочитана по его уведомлению.

### Границы

Фиксы DRF-988/989/997/998/1004/1005 не тронуты (diff: `apps/handoff/{admin,services}.py`, `apps/handoff/tests/test_admin.py`, `config/settings/base.py`, `apps/skills/booking/tools.py`, 2 новых тестовых файла). Операторский эндпоинт не строил. Backend и механику оплаты не трогал. Секретов в отчёте/коде нет.

### Кандидаты в Linear

1. **Bulk-close в админке AdminTask.** Сейчас массовых действий над статусом нет; если операторам понадобится — добавить action, идущий через сервисный слой (не `queryset.update`).
2. **Рекласс RESOLVED→CANCELLED в админке** сохраняет старый `resolved_at` (поле readonly). Либо запретить переход валидатором формы, либо определить семантику очистки.
3. **Операторский эндпоинт закрытия задачи** (из описания DRF-980) — осознанно вне объёма этого окна.

### Следующий шаг

Push ветки + PR в `dev`, цитата `gh pr checks` — следующей секцией. Деплой — только после «GO НА ФАЗУ C».
