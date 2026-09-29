# БРИФ — DRF-967 «Миррор MasterService formula-tela: декартово произведение»

**Дата брифа:** 2026-08-09
**Автор:** Chief Architect agent (главное окно)
**Для:** отдельное окно-исполнитель (Opus-оркестратор). Прочитай файл целиком до первого действия.
**Критический путь пилота:** DRF-967 → повторная live-приёмка DRF-962 → DRF-945 (Go/No-Go пилота).

---

## 0. Правила обмена и отчётности (обязательно)

- Итоговый и промежуточные отчёты пиши в `C:\Users\user\PycharmProjects\Ayla\docs\REPORT_DRF-967.md` (секции с датой/временем). НЕ полагайся на пасту в чат.
- Вопросы-эскалации владельцу — туда же, секцией «Эскалация».
- Новые задачи в Linear создаёт ТОЛЬКО главное окно — кандидатов фиксируй в отчёте. Комментировать существующие (DRF-967, DRF-962) — можно и нужно.
- Кириллица в GraphQL-мутациях Linear из PowerShell превращается в «?» (испорчены были DRF-967/968 при создании). Все мутации с русским текстом — только с ASCII-escaped JSON (`\uXXXX`), после записи верифицировать чтением. В bash обратные кавычки съедают SHA в тексте — экранировать.

## 1. Цель (одно предложение)

Восстановить у тенанта formula-tela реальные рёбра «мастер ↔ услуга» (убрать декартово произведение) и устранить источник мусора, чтобы discovery снова фильтровал по услуге и стала возможна повторная приёмка DRF-962.

## 2. Контекст (VERIFIED, live-диагностика 09.08.2026)

- Пилотный контур: хост `api-dev.gobeauty.site` (194.87.99.126), репо на хосте `/home/taximeter/ai-bot-platform-dev`, docker compose `-p ayla-bot-staging` (3 файла: base + .staging + .staging.local), web 127.0.0.1:8014, health `/healthz/`. Задеплоен SHA `699639c` (фикс DRF-962), rollback-точка `e6460bd7ca8709c256007a83c69e74e96cf5e960`. Известный шум в логах: предсуществующий startup-ERROR `worker.subscriber_audit` — не признак поломки.
- Хост НЕ видит GitHub: код доставляется git-bundle по scp (процедура: `docs/DEPLOY_BRIEF_DRF-962.md` §4).
- Тенант: formula-tela, UUID `b32a057a-56c7-4bf0-ae50-e11e76ab44be`. Флаг `BOOKING_VIA_AYLA_REST=ON`.
- Симптом: каждый из 4 мастеров тенанта связан со ВСЕМИ 58 услугами каталога («все умеют всё») → discovery-фильтрация по услуге инертна, на любой запрос один и тот же список мастеров; гард неоднозначности DRF-962 срабатывает почти всегда → карточки без service_id → запись не стартует. Сам фикс DRF-962 работает по спецификации (однозначная услуга → service_id проставляется, проверено прямым вызовом).

## 3. Гипотеза об источнике (INFERRED — проверить, не принимать на веру)

Код-граундинг (репо `ai-bot-platform`, состояние origin/dev):
- `apps/catalog/management/commands/seed_dev_formula_tela.py:168-173` — seed создаёт рёбра MasterService БЕЗ `ayla_specialist_service_id` → главный кандидат.
- `apps/catalog/services/upserter.py:190-287` — `upsert_master_services`: ownership по `ayla_specialist_service_id`; строки без него синк считает «операторскими» и при реконсиляции НЕ трогает → мусор от seed живёт вечно.
- `apps/catalog/services/sync.py:189-210` — этап синка рёбер (`fetch_specialist_services` → `upsert_master_services`); `apps/catalog/services/http_client.py:258` — `fetch_specialist_services`.
- Альтернативные кандидаты из DoD задачи: PR #1128 (backfill), specialist-services endpoint backend'а. Проверить и их.

## 4. Linear

- Задача **DRF-967** (issueId получи по identifier; team DRF `9caf1205-31eb-4163-a1e9-424602c3522a`, project Ayla `e36401cf-2331-4133-aff0-0041b5f3eca3`). Доступ: ключ в `C:\Users\user\PycharmProjects\Ayla\.mcp.json` env `LINEAR_API_KEY`, POST `https://api.linear.app/graphql`, заголовок `Authorization: <ключ>` БЕЗ Bearer. Ключ — секрет, в отчёты не выводить.
- Перед стартом прочитай описание и DoD DRF-967 (там перенесён Definition of Done из диагностики деплой-окна). Расхождение брифа с Linear → Linear главнее, но расхождение отметь в отчёте.
- Статус In Progress на время работы; evidence-комментарии по ходу. **NO FALSE SUCCESS: Done только после верифицированной чистки данных + merge фикса кода.**

## 5. План работ

### 5.1. Диагностика на хосте (read-only)
В web-контейнере (`docker compose -p ayla-bot-staging exec web python manage.py shell`) для тенанта `b32a057a…`:
- Счётчики MasterService: всего / с `ayla_specialist_service_id IS NULL` / non-NULL; распределение по мастерам.
- Сверка с backend-истиной: что реально возвращает specialist-services endpoint (`fetch_specialist_services`) по каждому из 4 мастеров.
- Вывод в отчёт: подтверждён ли seed как источник; если нет — фактический источник.

### 5.2. Фикс кода (репо ai-bot-platform, локально)
- Ветка `feat/wave1-drf967-masterservice-mirror` от `origin/dev` (свежий worktree; worktree `C:\Users\user\PycharmProjects\ai-bot-platform-drf962` не переиспользовать — только читать).
- Починить источник: seed не должен создавать декартово произведение (создавать рёбра по реальным данным или помечать так, чтобы синк мог реконсилировать). Ownership-логику `upsert_master_services` НЕ менять — если окажется, что без её изменения не обойтись, остановись и эскалируй.
- Management-команда чистки `cleanup_orphan_master_services`: по умолчанию dry-run (печатает, что удалит), `--apply` для применения, обязательный дамп удаляемых строк в json-файл до удаления. Юнит-тесты на seed-фикс и команду.
- PR в `dev`; планка: независимое ревью, P0/P1 = 0, CI зелёный; squash, SHA в отчёт.

### 5.3. GO-гейт владельца (обязательная остановка)
Чистка данных на пилоте = мутация runtime. Перед применением запиши в отчёт: сколько строк удаляется, у каких мастеров что останется, путь к дампу-бэкапу — и **дождись GO владельца**. Без GO ничего на хосте не менять.

### 5.4. Чистка и реконсиляция на хосте (после GO)
1. Доставить код с командой чистки bundle-workflow'ом (`DEPLOY_BRIEF_DRF-962.md` §4), rebuild, health `/healthz/` 200.
2. Dry-run команды → сверить с планом из 5.3 → `--apply` (дамп сохранить на хосте и продублировать содержимое/путь в отчёт).
3. Прогнать синк каталога → рёбра реконсилируются с backend-истиной.
4. Верификация: у каждого мастера набор услуг совпадает с backend; прямой вызов discovery (как в диагностике 09.08): `specialization='RF-лифтинг — Лицо/шея/декольте'` → карточки с service_id; `'классический массаж'` → осмысленный (не «все 4») набор мастеров; неоднозначность, если осталась, — честный ask-service, это норма.

### 5.5. Финал
Evidence-комментарий в DRF-967 (счётчики до/после, SHA, дамп) — с учётом правила кириллицы из §0. Комментарий в DRF-962: «данные миррора починены, готово к повторной live-приёмке» (саму приёмку НЕ проводить — она требует владельца в MAX, координирует главное окно).

## 6. Side-quest (read-only, ~5 минут, для другой задачи)

На хосте проверь: входит ли `MAX_WEBHOOK_SECRET` пилотного MAX-бота в `GLOBAL_BOT_TOKENS` (env/compose-конфиги, settings в web-контейнере). Запиши ответ в `REPORT_DRF-967.md` отдельной секцией **«Путь пилотного бота»** (точные значения секретов не выводить — только вердикт «входит/не входит» и где смотрел). Это решает предпосылку DRF-963: global-путь = LLM-консьерж (keyword-матчер не работает), per-tenant = keyword-диспатч. Ничего не менять.

## 7. Откат

- Данные: восстановление из json-дампа (обратная вставка) + повторный синк.
- Код на хосте: `git checkout <SHA до деплоя>` → rebuild → health (точка фиксируется перед 5.4.1).

## 8. Границы

- НЕ трогать: код DRF-962 (`apps/orchestrator/handoff.py`, `apps/orchestrator/discovery.py`, `apps/marketplace/discovery.py`), booking skill (`apps/skills/booking/` — S1 anti-touch), backend `beautygo_backend`, EventBus-конфиги, ветку DRF-963.
- Никаких миграций схемы; понадобилась — стоп и эскалация.
- Любая правка данных/кода на хосте — только после GO (§5.3).

## 9. Тестовая среда (проверенные грабли)

- `uv sync`, затем `uv pip install -e ../ayla-ai-core`.
- `PYTHONNOUSERSITE=1 uv run pytest -p no:pylama …` (иначе системный Django 6 / сломанный pylama).
- Postgres-тесты: контейнер `ayla-e2e-bot-postgres-1`, 127.0.0.1:15433, db `ai_bot_platform`, user `platform`, пароль: `docker exec ayla-e2e-bot-postgres-1 sh -c 'printf %s "$POSTGRES_PASSWORD"'`.
- Pre-commit ruff-format модифицирует файлы и валит коммит: `git add -u` → повторный коммит → проверить `git log --oneline -1`.

## 10. Дополнение (после отчёта деплой-окна) — механика хоста, проверенная 09.08

Полный разбор: `docs/REPORT_DRF-962.md` §5. Ключевое для этой задачи:
- **`MasterService` на задеплоенной схеме НЕ имеет поля `is_active`** (есть `ayla_specialist_service_id`, `master`, `service`, `tenant`) — не используй его в диагностических запросах §5.1.
- Bundle: ref внутри бандла называется `refs/remotes/origin/dev`, не `dev`. Рабочая последовательность: локально `git bundle create x.bundle <host-SHA>..origin/dev` → `scp x.bundle taximeter@194.87.99.126:/tmp/` → на хосте `git fetch /tmp/x.bundle refs/remotes/origin/dev:refs/tmp/<slug> && git checkout <SHA>`.
- Репо на хосте живёт в detached HEAD — это норма. В `git status` постоянно висят `??` (бэкапы `.env.staging.*`, `.chromadb/`, `data/`) — критерий чистоты «нет tracked-изменений», а не «пусто».
- Compose поднимать всеми тремя файлами (`docker-compose.yml` + `.staging.yml` + `.staging.local.yml`, третий untracked и есть только на хосте): запуск без него даст не тот стек.
- Health: `/healthz/` (именно так; `/health/` → 404 — норма). Точка отсчёта на хосте сейчас `699639c`, НЕ `e6460bd`.

## 11. Эскалация

Любое из: источник мусора не seed (гипотеза §3 не подтвердилась и фикс требует менять ownership синка или backend); чистка затрагивает строки с непустым `ayla_specialist_service_id`; расхождение backend-истины со здравым смыслом (у мастера 0 услуг); ревью держит P0/P1 два раунда — **стоп**, состояние в `REPORT_DRF-967.md` секцией «Эскалация» + комментарий в DRF-967, вернуть вопрос владельцу.
