# REPORT — Memory Domain: восстановление сессии + активация Live Shadow Stage 1

**Дата:** 2026-08-21
**Автор:** окно восстановления (Kimi, wd `PycharmProjects/Ayla`)
**Адресат:** оркестратор
**Статус:** STAGE 1 LIVE на пилоте

---

## 1. Что произошло

Потерянная рабочая сессия по Memory Domain найдена и восстановлена, незавершённый хвост (активация Live Shadow Stage 1 на пилотном box) выполнен. Потерянных изменений в коде нет.

## 2. Восстановление контекста

- Потерянная сессия: Kimi `session_4fb678c9-cb6a-423b-b1a5-af472066b6b0` (окно `ayla-knowledge`, работала по репо `ai-bot-platform`). Пройденный ею путь: Memory Domain Contract v1.0 + Context Resolution Contract v1.0 (canonical, AYLA-DEC-0081) → Migration Plan Steps 2, 2.5, 3, 3B, 3.5, 4 → orchestration seam → shadow infrastructure → Stage 1 config.
- **PR #1221** (`feat/live-shadow-stage1`) смержен в `dev` 2026-08-21 09:52 UTC, коммит `5976749`. CI green (pytest + ruff + mypy, 26m27s; по пути исправлены 11 mypy-ошибок, `4756107`).
- Финальный отчёт потерянной сессии зафиксировал блокер: deploy-канал «git push + CI» неработоспособен (см. §6), Stage 1 оставался NOT ACTIVE.
- Параллельное окно (Claude, салонная админка) выложило вручную **PR #1222** (`d9a3024`, revoke salon access) на пилот через git bundle + scp; вместе с ним на box приехал и код #1221.

## 3. Внесённые изменения (это окно, box `194.87.99.126`)

Изменения только инфраструктурные, код не трогали.

1. **`/home/taximeter/ai-bot-platform-dev/.env`** — создан новый файл (ранее не существовал):
   ```
   ORCHESTRATOR_SHADOW_ENABLED=true
   ORCHESTRATOR_SHADOW_SAMPLE_RATE=0.01
   ORCHESTRATOR_SHADOW_SURFACES=global
   ORCHESTRATOR_SHADOW_MAX_BACKLOG=500
   ORCHESTRATOR_SHADOW_TIMEOUT_MS=2500
   ```
   Конфигурация ровно по OWNER GO из потерянной сессии: Stage 1 только для global tenant-less pilot bot.
2. **Пересозданы контейнеры** `web`, `worker`, `shadow-worker` (compose project `ayla-bot-staging`). `shadow-worker` (celery, очередь `shadow`, concurrency 2) поднят впервые.
3. **Бэкап:** `.env.staging.bak-shadow-stage1-20260821`.

### Важная поправка к инструкции потерянной сессии

Отчёт той сессии предписывал писать флаги в `/etc/ai-bot-platform-dev/.env`. На реальном box этого пути нет, а `.env.staging` **не работает** для этих переменных: `docker-compose.yml:103-107, 152-156, 191-194` задаёт их через подстановку `${VAR:-default}` в секции `environment:`, которая перебивает `env_file`. Первая попытка через `.env.staging` дала `ENABLED=false` в работающих контейнерах, откачена из бэкапа. Рабочий рычаг — project-файл `.env` (источник подстановки compose). Проверено рендером `docker compose config`: все три сервиса получили `true` / `0.01`.

## 4. Проверки после активации (health checks)

| Проверка | Результат |
|---|---|
| `web` | Up, healthy |
| `worker` | Up |
| `shadow-worker` | Up, celery ready, подключён к redis |
| env в web/worker/shadow-worker | `ENABLED=true`, `SAMPLE_RATE=0.01` — во всех трёх |
| `redis-cli LLEN shadow` | 0 (bounded) |
| Traceback/CRITICAL в логах (5 мин) | нет |
| События `orchestrator.shadow.*` | 0 — ожидаемо: sample rate 1%, нужен живой трафик global-бота |

Пилот отвечает штатно (по данным окна выкладки #1222: `d9a3024 / healthy / 200`).

**Kill switch:** `SAMPLE_RATE=0` или `ENABLED=false` в `.env` + recreate `web worker`. Остановка одного shadow-worker — НЕ полный kill switch: продюсер продолжит enqueue до cap 500.

**Следующий контроль:** через 1–2 часа живого трафика проверить `docker logs | grep orchestrator.shadow.attempted/completed`.

## 5. «Зависшие коммиты» в старом клоне `djangoproject`

Ветка `feat/memory-foundation-internal-api` (remote удалён после мержа PR #217). Три локальных коммита 30–31.07.2026, **никуда не запушены, в активном репо `ai-bot-platform` их содержимого нет** (проверено по `git cat-file`/`git log origin/dev`):

| Коммит | Содержимое | Объём |
|---|---|---|
| `7c9910d8` | security(auth): отключение неподтверждённого логина VK/Yandex (`users/social_auth.py`, settings, 188 строк тестов) | +247 |
| `5392ac83` | security(repo): secret-guards — pre-commit config, `.secrets.baseline` (507 строк), `scripts/sensitive_file_guard.py`, CI-шаг | +669 |
| `8711af52` | security(settings): валидация outbound delivery конфигурации, `core/env_strictness.py`, prod env tests | +212 |

Плюс **незакоммиченные правки** (+255/−48): `users/models.py` — модель `PersonalContextProposal` (status/confidence/provenance/expires_at), `users/personal_context_inference.py` — dual-write `_write_legacy`/`_write_proposals`. Это ранний черновик proposal-механизма памяти. В канонической линии (`ai-bot-platform`, `apps/identity/models.py:622` — `MemoryEntry`) proposal запланирован как `MemoryProposal` на Step 4+ (см. `apps/identity/services/memory_inferred.py:7`). Черновик **архитектурно превзойдён** AYLA-DEC-0081 — ценность только справочная.

**Решение требуется:** три security-коммита — реальная неперенесённая работа (VK/Yandex, secret-guards, env strictness). Переносить в `ai-bot-platform` отдельными PR или сознательно закрыть как устаревшие. Черновик proposal — не переносить, заархивировать.

## 6. Открытые инфра-блокеры (не закрыты этим окном)

1. **Auto-trigger `deploy-dev` мёртв с ~10 июня:** на `dev` триггер `workflow_run`, на `main` (default, откуда GitHub регистрирует триггеры) — старый skeleton `on: push`. Deploy на dev два месяца фактически не выполнялся автоматически.
2. **Secret `DEV_SSH_KEY` невалиден** (`Load key .../dev_deploy: error in libcrypto`), ручной dispatch тоже падает. `DEV_HOST` задан.
3. Выкладки сейчас возможны только вручную (bundle + scp), как сделано для #1222.

Фикс требует owner/operator: обновить `DEV_SSH_KEY`, затем `gh workflow run deploy-dev.yml --ref dev`; опционально синхронизировать `deploy-dev.yml` между `dev` и `main`.

## 7. Итоговое состояние

```text
КОД ПАМЯТИ В DEV:        DONE (PR #1221, 5976749, CI green)
КОД НА ПИЛОТНОМ BOX:     DONE (d9a3024, включает #1221 и #1222)
STAGE 1 SHADOW:          LIVE (ENABLED=true, rate 0.01, surfaces=global)
DEPLOY-КАНАЛ CI:         BLOCKED (DEV_SSH_KEY + auto-trigger — нужен operator)
SECURITY-КОММИТЫ (старый клон): NOT PORTED — ждёт решения владельца
```
