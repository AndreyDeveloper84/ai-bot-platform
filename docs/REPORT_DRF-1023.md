# REPORT окна-исполнителя: DRF-1023 — админка бота непригодна к использованию

**Окно запущено:** 2026-08-12
**Ветка:** `fix/drf1023-admin-access` (от `origin/dev` @ `8da14ada25650c9326b25b8ea17ca351a4be821b`) — **VERIFIED** (`git rev-parse HEAD` после `git fetch origin`)
**Монитор REPLY:** поставлен (md5, 30 с); механизм проверен probe-файлом `scratchpad/.monitor_probe` — изменение детектируется — **VERIFIED**

## Статус

Реализация §4 завершена, локальные проверки зелёные. Эскалаций нет. Рекомендуемый объём §3 принят (вход чиним, учётка внутренняя, кросс-тенантность фиксируем документально, тенантную изоляцию не трогаем).

## Секция 1 (2026-08-12): реализация §4 — что сделано

**Ветка:** `fix/drf1023-admin-access` от `origin/dev` @ `8da14ada…`.

### 1.1. Блок настроек безопасности (VERIFIED тестами)

Размещение: `config/settings/base.py` (а не `staging.py`) — обоснование: все директивы читаются из env с дефолтами «поведение не меняется», поэтому блок универсален для любого HTTPS-контура; staging.py оставляем тонким, как сейчас. Новый парсер — `config/security.py` (stdlib-only, по образцу `apps/eventbus/ingest_allowlist.py` из T-02/DRF-1005).

По каждой директиве:

- **`CSRF_TRUSTED_ORIGINS`** — включено, env `DJANGO_CSRF_TRUSTED_ORIGINS` (CSV), дефолт `[]`. Парсинг строгий: пустое/отсутствующее → `[]`; кривое значение (нет схемы, путь, wildcard, порт вне 1–65535, лишняя запятая) → `ImproperlyConfigured` при загрузке настроек (отказ старта). Покрыто тестами `tests/test_web_security_settings.py` (валидное/пустое/кривое + wiring).
- **`SECURE_PROXY_SSL_HEADER`** — включено через булев флаг `DJANGO_BEHIND_TLS_PROXY=true` → `("HTTP_X_FORWARDED_PROTO", "https")`. Булев флаг вместо свободного кортежа: имя заголовка pinned к тому, что реально шлёт nginx (`X-Forwarded-Proto $scheme`, `infra/nginx/ai-bot-platform-api.conf.template:62` — VERIFIED по конфигу), свободное значение добавляло бы ось misconfiguration без пользы. Дефолт выкл.
- **`SESSION_COOKIE_SECURE` / `CSRF_COOKIE_SECURE`** — включено, env `DJANGO_SESSION_COOKIE_SECURE` / `DJANGO_CSRF_COOKIE_SECURE`, дефолт `false` (поведение не меняется), на пилоте выставить `true`.
- **`SECURE_SSL_REDIRECT`** — НЕ включено. Обоснование (VERIFIED по конфигам): (1) nginx уже делает 301 80→443 (`ai-bot-platform-api.conf.template:17-19`); (2) Django-редирект сработал бы и на собственный healthcheck контейнера — `docker-compose.staging.yml:71` curl'ит `http://localhost:8000/healthz/` без `X-Forwarded-Proto`, получил бы 301 и контейнер стал бы unhealthy. Сначала надо чинить probe — вне объёма DRF-1023.
- **`ALLOWED_HOSTS = ['*']`** — в коде не трогал (уже env-driven через `DJANGO_ALLOWED_HOSTS`; `'*'` приезжает из `.env.staging`). Анализ вызывающих (VERIFIED по репо): nginx проксирует с `Host: api-dev.gobeauty.site`; healthcheck контейнера — `Host: localhost`; хостовые пробы — `127.0.0.1:8014`; обращений по имени сервиса (`web:8000`) нет. Сужение до `api-dev.gobeauty.site,localhost,127.0.0.1` безопасно, но это правка `.env.staging` на контуре — предлагаю сделать на фазе C отдельным осознанным шагом с health-проверкой сразу после (откат = вернуть значение). Зафиксировано в runbook; кандидат в Linear — см. §1.5.

### 1.2. Учётная запись (VERIFIED)

Новый механизм НЕ изобретал: используется встроенный Django `createsuperuser --noinput`, который читает `DJANGO_SUPERUSER_USERNAME` / `DJANGO_SUPERUSER_EMAIL` / `DJANGO_SUPERUSER_PASSWORD` из окружения. Владелец вводит пароль сам через `read -s`, переменные передаются в контейнер через `docker compose exec -e` — пароль не попадает ни в код, ни в `.env.staging`, ни в отчёт. Процедура — в `docs/runbooks/admin-access.md`. Кастомной user-модели в проекте нет (`AUTH_USER_MODEL` не задан — стандартный `auth.User`).

### 1.3. Предупреждение о кросс-тенантности (VERIFIED тестом)

Жёлтый баннер (`messages.warning`) на changelist-страницах `AdminTaskAdmin`, `ConversationAdmin`, `MessageAdmin` — текст: «админка кросс-тенантная… учётку выдаём только внутренней команде». Тест `apps/handoff/tests/test_admin_cross_tenant_warning.py` проверяет наличие баннера на всех трёх страницах. Попутно VERIFIED: changelist-страницы рендерятся под `STRICT_TENANT_SCOPE=strict` (автофикстура тестов) — 500 после починки входа не будет.

### 1.4. Документация (VERIFIED)

`docs/runbooks/admin-access.md` (по шаблону `_template.md`, статус draft): кросс-тенантное предупреждение, таблица env-переменных, процедура создания учётки, верификация, эскалации. Добавлен в индекс `docs/runbooks/README.md`. Новые переменные задокументированы в `.env.example`.

### 1.5. Кандидаты в Linear

1. **Сузить `ALLOWED_HOSTS` на пилоте** до `api-dev.gobeauty.site,localhost,127.0.0.1` — анализ безопасности в §1.1; отдельным шагом ПОСЛЕ подтверждения работающего входа (порядок согласован REPLY №1/№3).
2. **Тенантно-ограниченный доступ в админку** (из брифа §3, связано с DRF-1022) — админка кросс-тенантная во всех приложениях; до появления ролевой модели учётки наружу не выдавать.
3. **Починка healthcheck-probe под TLS** (REPLY №1): `docker-compose.staging.yml` curl'ит `http://localhost:8000/healthz/` без `X-Forwarded-Proto` — до починки probe включать `SECURE_SSL_REDIRECT` нельзя.
4. **Date-bomb `tests/contracts/test_event_idempotency.py::test_booking_created_idempotent`** — падает на чистом `origin/dev` (фикстуры 2026-05 против реального now); в CI не гоняется, локально мешает.

### 1.6. Локальные проверки (VERIFIED)

- `pytest tests/test_web_security_settings.py` — 31 тест зелёный (парсер, wiring, логин-форма 200/403 через `Client(enforce_csrf_checks=True)` с симуляцией proxy-HTTPS).
- `pytest apps/handoff/tests` — 11 зелёных (8 из DRF-980 не тронуты + 3 новых на баннер).
- `pytest apps/conversations apps/eventbus/tests/test_ingest_settings.py apps/skills/booking/tests/test_health_gate_settings.py tests/test_dotenv_autoload.py` — зелёные.
- `ruff check` + `ruff format --check` по изменённым файлам — чисто; `mypy` по изменённым файлам — чисто; pre-commit хуки (ruff, detect-secrets, red-zone guard, import-boundary guard) — зелёные.
- `manage.py check` под `config.settings.staging` (с REDIS/DJANGO_ENV-заглушками локального запуска) — 0 issues.
- Полный прогон `tests/` (без e2e/acceptance): 4 падения, ВСЕ воспроизводятся на чистом `origin/dev` @ 8da14ad (проверено отдельным worktree) — НЕ мой регресс:
  - `tests/contracts/test_event_idempotency.py::...test_booking_created_idempotent` — date-bomb (фикстуры 2026-05 против реального now);
  - `tests/integration/test_pipeline_turn.py` (2 теста), `tests/smoke/test_ayla_import.py::test_package_sha_pinned` — локальное окружение (.venv).
  - Внимание главного окна: известный флакер из брифа (`test_distinct_ips_each_get_one_audit`, DRF-999) — это другое; date-bomb в contracts — потенциальный кандидат в Linear (в CI tests/contracts не гоняется, но локально мешает).

## Секция 2 (2026-08-12): коммит, PR, CI, MERGED

- Коммит `d468998` на ветке `fix/drf1023-admin-access` (pre-commit хуки зелёные; detect-secrets потребовал `# pragma: allowlist secret` на тестовом пароле-заглушке и обновил line-number в `.secrets.baseline` — оба изменения без секретов).
- PR: https://github.com/AndreyDeveloper84/ai-bot-platform/pull/1168 (base `dev`).
- CI (цитата `gh pr checks 1168`):
  - `pytest + ruff + mypy  pass  2m10s  …/actions/runs/31614568571/…` — реальный ci.yml, VERIFIED.
  - `pytest + ruff + mypy  fail  16s …/runs/31614633912` и `replay fixtures  fail  15s …/runs/31614633798` — это **само-отменённые дубли docs-skip воркфлоу** (аннотация прогона: «Mixed PR — non-docs files detected. Real ci.yml will run; cancelling this docs-skip run to avoid duplicate-check false-positive»), не тестовые падения. VERIFIED по логам обоих прогонов.
  - `replay (bypassed via prompt-regression-accepted)  skipping` — replay не применим (промпты не тронуты).
  - Разбор по REPLY №2 (VERIFIED главным окном, `gh run list` + аннотации check-run'ов) — по коммиту `d468998`:

    | workflow | conclusion |
    |---|---|
    | ci | cancelled (docs-skip, само-отмена по замыслу) |
    | replay | cancelled (docs-skip, само-отмена по замыслу) |
    | replay | **success** |
    | ci | **success** |
    | ci | **success** |

    `gh pr checks` показывает отменённые прогоны как fail — ложная краснота; настоящие ci/replay — success. Кандидат в Linear регистрирует главное окно.
- **MERGED** (обычный merge, предразрешён §0): merge-коммит `1ea47b915090cedb56698e42e1db122473965392` в `dev`; `origin/dev` теперь `1ea47b9` = `8da14ad` + `d468998` (VERIFIED `git fetch` + `gh pr view`).
- **Done не ставлю** (NO FALSE SUCCESS): Done — после runtime-приёмки владельцем (вход в админку + закрытие тестовой задачи), §7 брифа.

## Секция 3 (2026-08-12): готовность к фазе C — ЖДУ «GO НА ФАЗУ C»

План фазы C (по §5 брифа), без действий до GO:

1. `df -h /` до; bundle `8da14ad..origin/dev` → scp → checkout `1ea47b9` → пересборка → `up -d` проекта `ayla-bot-staging`.
2. Добавить в `.env.staging`: `DJANGO_CSRF_TRUSTED_ORIGINS=https://api-dev.gobeauty.site`, `DJANGO_BEHIND_TLS_PROXY=true`, `DJANGO_SESSION_COOKIE_SECURE=true`, `DJANGO_CSRF_COOKIE_SECURE=true` (таблица — в `docs/runbooks/admin-access.md`).
3. Проверки: `df -h /` после; `/healthz/` → 200 на 127.0.0.1:8014; форма логина → 200 (не CSRF-ошибка); логи worker'а 5 минут (известный шум: `worker.subscriber_audit`, `pii_protected_provider.no_active_scope`, `events.emit.non_canonical`).
4. Учётку создаёт владелец сам по runbook §Step-by-step (пароль я не вижу). Дальше — live-приёмка владельцем: вход + закрытие тестовой задачи.
5. Rollback: код — `8da14ad` (предразрешён); конфиг — убрать 4 переменные из `.env.staging` + `up -d`.

Опционально на фазе C (решение за главным окном/владельцем): сужение `DJANGO_ALLOWED_HOSTS` до `api-dev.gobeauty.site,localhost,127.0.0.1` (анализ в §1.1; откат — вернуть `*`).

## Секция 4 (2026-08-12): ФАЗА C — деплой выполнен (по GO из REPLY №3)

Все значения ниже — **VERIFIED** (замеры на хосте `taximeter@194.87.99.126`, если не сказано иное).

1. **Диск:** `df -h /` **до** — 26G свободно; **после** — 25G свободно. Порог 10 ГБ не пересечён, `docker builder prune` не потребовался. Операций с томами не было.
2. **Bundle:** `drf1023-deploy.bundle` (`8da14ad..origin/dev` → `1ea47b9`), `git bundle verify` → «is okay», требует rollback-точку `8da14ad…`. scp → `/tmp/` — OK.
3. **Checkout:** `git fetch` bundle → `refs/tmp/drf1023` → `git checkout 1ea47b915090cedb56698e42e1db122473965392` → `git rev-parse HEAD` = `1ea47b9…` ✔ (до этого HEAD = `8da14ad` = rollback-точка ✔).
4. **Конфиг:** `.env.staging` → бэкап `.env.staging.bak-drf1023` (chmod 600) → добавлены 4 переменные (`DJANGO_CSRF_TRUSTED_ORIGINS=https://api-dev.gobeauty.site`, `DJANGO_BEHIND_TLS_PROXY=true`, `DJANGO_SESSION_COOKIE_SECURE=true`, `DJANGO_CSRF_COOKIE_SECURE=true`). Предсуществующих вхождений не было (проверено grep до правки). `DJANGO_ALLOWED_HOSTS` НЕ трогал — по REPLY №3 п.3.
5. **Build:** `docker compose -p ayla-bot-staging -f docker-compose.yml -f docker-compose.staging.yml -f docker-compose.staging.local.yml build` → BUILD_EXIT=0 (web, worker, celery-worker, celery-beat — Built).
6. **up -d** → UP_EXIT=0.
7. **Особая проверка (REPLY №3 п.6): web-контейнер `Up (healthy)`** — healthcheck не сломан включённым `SECURE_PROXY_SSL_HEADER`/secure-cookie. Остальные: worker/celery-worker/celery-beat Up, postgres/redis/minio Up (healthy).
8. **Health:** `/healthz/` на `127.0.0.1:8014` → **HTTP 200**.
9. **Настройки видны в контейнере** (доказательство по REPLY №3 п.2, чтение Django-настроек в web-контейнере):
   - `CSRF_TRUSTED_ORIGINS = ['https://api-dev.gobeauty.site']`
   - `SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')`
   - `SESSION_COOKIE_SECURE = True`, `CSRF_COOKIE_SECURE = True`
   - `SECURE_SSL_REDIRECT = False` (как задумано)
10. **Форма входа:** `GET https://api-dev.gobeauty.site/admin/login/` → **200** (не CSRF-ошибка). Живой POST-пробник с валидным `Origin: https://api-dev.gobeauty.site` и заведомо неверными кредами (`username=drf1023-csrf-probe`) → **200** (форма перерисована с ошибкой входа), а НЕ 403 — CSRF-гейт пропускает доверенный origin. Записей в БД не создаёт (неудачный логин).
11. **Логи worker'а 5 минут** — чисто: после фильтрации известного шума (`worker.subscriber_audit`, `pii_protected_provider.no_active_scope`, `events.emit.non_canonical`) не осталось ни одной строки (VERIFIED, `logs --since 5m worker`).
12. **Финальный статус (через ~7 мин после up -d):** все контейнеры `ayla-bot-staging-*` Up, web `Up (healthy)`, `/healthz/` → 200 (VERIFIED).

Данные пилота не тронуты; задач/записей у тенанта `b32a057a-…` не создавал. Учётку не создавал (REPLY №3 п.8).

## Секция 5 (2026-08-12): ГОТОВО К LIVE-ПРИЁМКЕ

Контур в состоянии «процедура из runbook отработает». Сценарий для владельца (по `docs/runbooks/admin-access.md`, выполняется на хосте `taximeter@194.87.99.126`, каталог `/home/taximeter/ai-bot-platform-dev`):

1. **Создать учётку** (пароль вводится вами, в файлы/отчёты/логи не попадает):

   ```bash
   read -rsp "Admin username: " ADMIN_USER; echo
   read -rsp "Admin email: " ADMIN_EMAIL; echo
   read -rsp "Admin password: " ADMIN_PASS; echo
   docker compose -p ayla-bot-staging exec -T \
     -e DJANGO_SUPERUSER_USERNAME="$ADMIN_USER" \
     -e DJANGO_SUPERUSER_EMAIL="$ADMIN_EMAIL" \
     -e DJANGO_SUPERUSER_PASSWORD="$ADMIN_PASS" \
     web python manage.py createsuperuser --noinput
   unset ADMIN_USER ADMIN_EMAIL ADMIN_PASS
   ```

   Ожидаемый вывод: `Superuser created successfully.`
2. **Войти:** `https://api-dev.gobeauty.site/admin/` → форма входа (уже проверено: отдаёт 200 и принимает POST) → логин новой учёткой.
3. **Закрыть тестовую задачу через интерфейс:** `/admin/handoff/admintask/` → открыть задачу → статус RESOLVED → сохранить. Ожидаемое поведение (DRF-980): разговор возвращается боту (state → IDLE), пишется audit. На странице списка виден жёлтый баннер о кросс-тенантности — это ожидаемо, он напоминает, что учётку нельзя выдавать сотрудникам салона.

**Done по §7 ставит главное окно** после вашего входа и закрытия тестовой задачи.

Rollback (предразрешён): код — `git checkout 8da14ada…` + build + `up -d`; конфиг — `cp .env.staging.bak-drf1023 .env.staging` + `up -d`.

_(дальнейшие секции будут добавляться правками по ходу работы)_
