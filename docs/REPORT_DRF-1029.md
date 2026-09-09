# REPORT DRF-1029 — уведомления об эскалации в MAX

**Окно-исполнитель, веду правками. Доказательность: VERIFIED / INFERRED / CLAIMED / UNKNOWN.**

---

## 2026-08-13 — старт окна

- Бриф прочитан, протокол §1–§5 принят (VERIFIED — `docs/WINDOW_PROTOCOL.md`).
- Монитор на `REPLY_DRF-1029.md` поставлен (md5, sleep 30, фоновая задача; VERIFIED — задача запущена, доставку проверю при первом обновлении файла).
- Репозиторий: `git fetch origin` выполнен, `origin/dev` = `02a61a9d9efa1bf9159e1660b6240fe9c9223912` — совпадает с базой брифа (VERIFIED).
- Ветка `feat/drf1029-handoff-notify-max` создана от `origin/dev` (VERIFIED).
- Файлы окна DRF-911 (`apps/orchestrator/handoff.py`, `apps/channels/max/handler.py`, `apps/channels/tests/test_global_booking_lookup.py`) не трогаю.

Следующий шаг: изучение кода, RED-тесты по §3.

---

## 2026-08-13 — реализация (TDD, красный→зелёный)

**Изменения (ветка `feat/drf1029-handoff-notify-max`):**

- `apps/handoff/notify.py` (новый) — переиспользуемый модуль уведомлений: `send_max_notification` (fan-out best-effort, изоляция получателей, без ретраев, таймаут ≤ 5 с), `build_admin_task_notification` (минимум ПДн), `notify_admin_task_created` (on_commit-точка входа, никогда не бросает).
- `apps/handoff/services.py` — одна врезка в `create_admin_task`: `transaction.on_commit(lambda: notify_admin_task_created(task))` (VERIFIED, покрывает все 5 поводов эскалации одной точкой).
- `config/settings/base.py` — `HANDOFF_NOTIFY_MAX_CHAT_IDS` (CSV из env, пусто = полностью выключен) и `HANDOFF_ADMIN_BASE_URL` (база для прямой ссылки в админку).
- `.env.example` — задокументированы обе переменные (закомментированы, дефолт выключен).
- `apps/handoff/tests/test_notify.py` (новый) — 13 тестов по всем критериям §3.

**Красный до фикса (VERIFIED):** все 13 тестов падали с `ModuleNotFoundError: No module named 'apps.handoff.notify'`.

**Зелёный после (VERIFIED):** 13/13 новых тестов проходят; регресс `apps/handoff` + `apps/skills/human_handoff` — 58/58; `ruff check` и `ruff format` чисто; `mypy apps/handoff/notify.py apps/handoff/services.py` — no issues.

**Решение «синхронно, не Celery» (позиция главного окна подтверждаю):** механизм обязан работать и когда очередь встала — именно тогда эскалаций больше всего; синхронная отправка с таймаутом 5 с и без ретраев укладывается в бюджет consumer'а. Таймаут зафиксирован тестом (`call["timeout"] <= 5.0`).

**Состав текста уведомления (дословно, NORMAL-приоритет):**

```
📋 Эскалация к человеку
Салон: <tenant.name>
Причина: <task_type label> — <reason, обрезана до 200>
Время: <дд.мм.гггг чч:мм, локальная TZ сервера>
Задача: <uuid>
Диалог: <uuid>
Открыть: <HANDOFF_ADMIN_BASE_URL>/admin/handoff/admintask/<uuid>/change/
```

HIGH/URGENT: первая строка `🚨 Эскалация к человеку — ПРИОРИТЕТ HIGH` (или `URGENT`). Телефона клиента и транскрипта в тексте нет — зафиксировано тестом. `Открыть:` отсутствует, если база не настроена.

**Неудача отправки:** per-recipient изоляция (падение на одном не отменяет остальных), warning-лог + аудит `handoff.notify_failed` (target=AdminTask); любое исключение внутри notify глушится полностью — задача создаётся, диалог уходит в HUMAN_HANDOFF (зафиксировано тестами, включая неожиданный RuntimeError).

**Файлы окна DRF-911 не тронуты** (`apps/orchestrator/handoff.py`, `apps/channels/max/handler.py`, `apps/channels/tests/test_global_booking_lookup.py`) — VERIFIED по `git status`.

Дальше: расширенный регресс (booking/orchestrator, вызывающие create_admin_task), затем коммит и PR в dev.

---

## 2026-08-13 — расширенный регресс: разбор падений

Расширенный прогон (booking + orchestrator) сначала показал ~16 падений в `apps/orchestrator/tests/test_pipeline.py`. **Разбор (VERIFIED по traceback):** все падения — `redis.exceptions.ConnectionError: Error 10061 connecting to localhost:6379` в `_update_short_term` → локальный Redis не был запущен. К моему изменению отношения не имеет (путь `apps/orchestrator/memory/short_term.py`, до handoff-уведомлений не доходит).

**Действие:** поднял Redis проектным способом — `docker compose up -d redis` (контейнер `ai-bot-platform-redis-1`, порт 6379; штатный dev-сервис из `docker-compose.yml`). Перепрогон `test_pipeline.py` — 40/40 зелёные (VERIFIED). `apps/booking/tests` — 112/112 (VERIFIED). Полный свип (handoff + human_handoff + global_human_handoff + booking + orchestrator) идёт, результат допишу.

---

## 2026-08-13 — PR создан

- Коммит `a77b11d61528573b1a85f195d84a4f953452e338` (5 файлов, +475; pre-commit хуки все зелёные — VERIFIED).
- Полный регресс-свип зелёный (VERIFIED): `apps/handoff` (45, включая 13 новых), `apps/skills/human_handoff`, `apps/channels/tests/test_global_human_handoff.py`, `apps/booking/tests` (112), `apps/orchestrator/tests` — с поднятым локальным Redis. Единственное промежуточное падение `test_pipeline_latency` оказалось флаки окружения (реальные 401-ответы OpenAI раздувают wall-clock SLO; на ветке и на базе по отдельности тест зелёный — VERIFIED обоими прогонами).
- PR #1170 в `dev`: https://github.com/AndreyDeveloper84/ai-bot-platform/pull/1170 — CI запущен, жду зелёный. После зелёного CI — обычный merge (предразрешён, НЕ squash).

---

## 2026-08-13 — CI зелёный, PR смержен. ГОТОВО К ФАЗЕ C (жду GO + значение chat_id)

### SHA и эвиденс (VERIFIED)

- Merge-коммит в `dev`: `b0849ed4017b28e790f3da342d669d53534c5b2b` (Merge pull request #1170, обычный merge — НЕ squash).
- Рабочий коммит: `a77b11d61528573b1a85f195d84a4f953452e338`.

`gh pr checks 1170`:

```
pytest + ruff + mypy	pass	2m16s	…/runs/31667485539/job/94345043536
replay fixtures (golden + adversarial + voice)	pass	1m2s	…/runs/31667485532/job/94345043337
replay (bypassed via prompt-regression-accepted)	skipping	0	…/runs/31667485532/job/94345043797
pytest + ruff + mypy	pass	2m28s	…/runs/31667460924/job/94344972662
```

`gh run list --commit a77b11d…` — все три прогона `completed success` (ci push, ci pull_request, replay pull_request). Ловушки docs-skip с `fail` в этом PR нет; единственный `skipping` — штатный bypass replay-гейта.

### Тесты «красный до / зелёный после» (VERIFIED)

- До: 13/13 `apps/handoff/tests/test_notify.py` — `ModuleNotFoundError: No module named 'apps.handoff.notify'`.
- После: 13/13 зелёные; регресс `apps/handoff` (45) + `apps/skills/human_handoff` + `test_global_human_handoff` + `apps/booking/tests` (112) + `apps/orchestrator/tests` — зелёные локально (с `docker compose up -d redis`); CI `pytest + ruff + mypy` — pass.

### Обоснование синхронной отправки (против Celery)

Подтверждаю позицию главного окна: механизм обязан работать в том числе когда очередь встала — именно тогда эскалаций больше всего; зависеть от Celery для сигнала о том, что Celery/consumer в беде, — циклическая хрупкость. Цена синхронного пути ограничена жёстко: таймаут 5 с, ретраев нет, per-recipient изоляция; худший добавочный latency на эскалацию — 5 с × N адресатов (на пилоте N=1). Замер в CI: полный прогон pytest не деградировал (2m16s против типичных ~2m по соседним PR — INFERRED по истории runs).

### Состав текста уведомления (дословно, NORMAL)

```
📋 Эскалация к человеку
Салон: <tenant.name>
Причина: <task_type label> — <reason, ≤200 символов>
Время: <дд.мм.гггг чч:мм, TZ сервера>
Задача: <uuid задачи>
Диалог: <uuid диалога>
Открыть: <HANDOFF_ADMIN_BASE_URL>/admin/handoff/admintask/<uuid>/change/
```

HIGH/URGENT: первая строка `🚨 Эскалация к человеку — ПРИОРИТЕТ HIGH|URGENT`. Без `HANDOFF_ADMIN_BASE_URL` строка `Открыть:` опускается. Телефон и транскрипт отсутствуют — закреплено тестом `test_text_has_no_phone_no_transcript`.

### Границы (VERIFIED по `git show --stat`)

В diff только: `apps/handoff/notify.py` (новый), `apps/handoff/tests/test_notify.py` (новый), `apps/handoff/services.py` (+4 строки), `config/settings/base.py` (+15), `.env.example` (+11). Файлы DRF-911 (`apps/orchestrator/handoff.py`, `apps/channels/max/handler.py`, `apps/channels/tests/test_global_booking_lookup.py`) не тронуты. DRF-988/989/997/998/1004/1005/980/1007/1015/1023, PII-обёртка (DRF-1010), backend Ayla — не тронуты. Секретов и chat_id владельца в репозитории нет. Linear не мутировал. Деплой не начинал.

### Фаза C — готовность

Жду в REPLY: «GO НА ФАЗУ C» + значение `HANDOFF_NOTIFY_MAX_CHAT_IDS` для `.env.staging`. План по §4: `.bak` `.env.staging` → добавить `HANDOFF_NOTIFY_MAX_CHAT_IDS` и `HANDOFF_ADMIN_BASE_URL=https://api-dev.gobeauty.site` → bundle `1ea47b9..origin/dev` → scp → checkout → пересборка → `up -d` → проверка env в web/worker → `df -h /` до/после → health `/healthz/` на 8014 + логи worker'а 5 минут. Мутаций у пилотного тенанта не будет; live-проверка уведомления — владельцем («позови менеджера»).

### Кандидаты в Linear (→ в Linear)

1. **Флаки `test_pipeline_latency` вне CI-окружения:** `intent_router` делает реальный LLM-вызов (`intent_router.production_llm_error` в логах) даже в «замоканных» latency-тестах; при невалидном `OPENAI_API_KEY` 401-раундтрипы раздувают wall-clock и `test_100_turns_under_60_seconds` падает. Воспроизводится локально, к DRF-1029 отношения не имеет (на базе и на ветке ведёт себя одинаково). Предложение: замокать production-LLM путь в latency/e2e-тестах или пометить тест маркером окружения.
2. **Локальный Redis для orchestrator-тестов:** `apps/orchestrator/tests` падают с `ConnectionError localhost:6379`, если не поднят `docker compose up -d redis`. Кандидат на строку в README/CONTRIBUTING (или автоматический skip/fixture) — мелочь, но стоит мне 20 минут разбора при каждом свежем окне.

---

## 2026-08-13 — follow-up выполнен: тест на ноль запросов в dev (PR #1171)

По REPLY №1–№4. Ветка `feat/drf1029-notify-zero-queries` от `b0849ed`. **Правился только** `apps/handoff/tests/test_notify.py` (+26 строк); `notify.py` в финальном состоянии идентичен тому, что в dev (VERIFIED: `git checkout --` после эксперимента, в diff его нет). Чужой WIP окна DRF-911 (`apps/channels/tests/test_global_booking_lookup.py`, лежал в общем рабочем дереве) в коммит не попал — добавлял файлы точечно.

**Тест:** `TestBuildNotificationNoQueries::test_build_text_makes_zero_db_queries` — `build_admin_task_notification(task)` внутри `django_assert_num_queries(0)`, задача создаётся через `create_admin_task` (продовая форма вызова).

**Недекоративность — два эксперимента (оба VERIFIED прогонами):**

1. `task.bot_user.display_name` (как указано в REPLY) → тест **остался зелёным**. Причина: `bot_user` передан экземпляром в `AdminTask.objects.create(...)` и закэширован в `fields_cache` ровно так же, как `tenant` — одношаговые FK из `create_admin_task` запросов не делают. То есть конкретный сценарий «пустое имя на проде» для тёплого инстанса не воспроизводится; защита нужна от другого класса изменений.
2. `task.conversation.messages.count()` (related manager — именно то, что следующий разработчик реально может добавить: «покажи оператору число сообщений») → тест **красный** (`django_assert_num_queries` поймал запрос). После отката — снова зелёный.

Вывод: тест не декоративный; он сторожит появление **любых** незакэшированных обращений (related managers, двухшаговые связи, refetch) в форматтере.

**Побочное наблюдение из прогона №2 (VERIFIED):** в тесте без мока `send_message` реальный вызов упал с `MAX API status=0: MAX_BOT_TOKEN is not configured` — и создание задачи прошло штатно, warning залогирован. Best-effort на живом (немоканом) пути работает как задумано.

**CI и merge (VERIFIED):**

```
gh pr checks 1171:
pytest + ruff + mypy	pass	2m15s	…/runs/31672354831/job/94359474747
replay fixtures (golden + adversarial + voice)	pass	50s	…/runs/31672354887/job/94359475625
replay (bypassed via prompt-regression-accepted)	skipping	0	…/runs/31672354887/job/94359475647
pytest + ruff + mypy	pass	2m21s	…/runs/31672321756/job/94359372584
```

Перед merge перечитал REPLY (требование №4) — новых секций не было. Merge обычный, НЕ squash: `origin/dev` = `565af20f6d8f839fa59611f75b815ba743c26678` (Merge PR #1171). Рабочий коммит: `30971267cd9b8707980bad871a35d973e8a5f658`.

**Готовность к общему деплою:** моя часть полностью в `dev` (`b0849ed` + `565af20`). Жду GO НА ФАЗУ C и значение `HANDOFF_NOTIFY_MAX_CHAT_IDS` здесь, в REPLY. План деплоя — по §4 брифа (`.bak` `.env.staging`, bundle `1ea47b9..origin/dev`, scp, пересборка, `up -d`, проверка env в web/worker, `df -h /` до/после, health 8014, логи worker'а 5 минут; пилотный тенант — только чтение).

---

## 2026-08-13 — проверка монитора REPLY (по запросу)

- Монитор (md5, sleep 30, фоновая задача, pid 21460) работает со старта окна и **поймал все 5 обновлений** REPLY (№0 создание → №4): 5 строк «ОБНОВЛЁН REPLY» в логе задачи (VERIFIED по output-логу).
- Ограничение честно: бесконечный цикл в фоне не завершается, поэтому его echo не приходят в мой диалог сами — доставка была «по опросу». Дополнил доставку cron-проверкой каждые 10 минут (id `a57be994`): сравнение md5 с `scratchpad/.reply_1029_md5`, при изменении — чтение верхней секции и действие по ней. Текущий md5 записан: `dc4f09b812a514c63e780bb557c3b285`.

---

## 2026-08-13 — ФАЗА C ВЫПОЛНЕНА. ГОТОВО К LIVE-ПРИЁМКЕ

Деплой по GO из REPLY №5/№6 (деплоил я, за оба окна; окно DRF-911 на хост не ходило). Все значения ниже — **VERIFIED** (замеры на `taximeter@194.87.99.126`, каталог `/home/taximeter/ai-bot-platform-dev`).

1. **Диск:** `df -h /` **до** — 26G свободно; **после** — 23G свободно. Порог 10 ГБ не пересечён, `docker builder prune` не потребовался. Операций с томами не было.
2. **Bundle:** `drf1029-deploy.bundle` (`1ea47b9..origin/dev` → `4373bb5`), `git bundle verify` → «is okay», prerequisite `1ea47b9…` (rollback-точка). scp → `/tmp/` OK. (Заметка: `git bundle create` на Windows отказывался принимать форму с голыми SHA (`<sha> ^<sha>`) как «empty bundle» при непустом rev-list; форма `1ea47b9..origin/dev` с ref-именем отработала — поведение как у DRF-1023.)
3. **Checkout:** fetch bundle → `refs/tmp/drf1029` → `git checkout 4373bb5…` → `git rev-parse HEAD` = `4373bb547549d0e66b0ee91b4346a49fa105229a` ✔ (до этого HEAD = `1ea47b9` = rollback-точка ✔). Дерево на хосте было чистое (только untracked-бэкапы).
4. **Конфиг:** `.env.staging` → бэкап `.env.staging.bak-drf1029` (chmod 600) **до** правки. Предсуществующих `HANDOFF_*` не было (grep до правки = 0). Добавлены:
   - `HANDOFF_NOTIFY_MAX_CHAT_IDS=-77378770496155`
   - `HANDOFF_ADMIN_BASE_URL=https://api-dev.gobeauty.site`
5. **Build:** `docker compose -p ayla-bot-staging -f docker-compose.yml -f docker-compose.staging.yml -f docker-compose.staging.local.yml build` → BUILD_EXIT=0 (web, worker, celery-worker, celery-beat — Built). **up -d** → UP_EXIT=0.
6. **Минус доехал целым (особая проверка из REPLY №5):** чтение Django-настроек внутри контейнеров:
   - **worker**: `HANDOFF_NOTIFY_MAX_CHAT_IDS = ['-77378770496155']`, `HANDOFF_ADMIN_BASE_URL = 'https://api-dev.gobeauty.site'`
   - **web**: те же значения.
   Отрицательный chat_id распарсился одним элементом списка, знак на месте.
7. **Контейнеры:** web `Up (healthy)`; worker, celery-worker, celery-beat — Up; postgres/redis/minio — Up (healthy).
8. **Health:** `/healthz/` на `127.0.0.1:8014` → **HTTP 200**.
9. **Логи worker'а 5+ минут:** после фильтрации известного шума — 0 строк. Старт чистый: все ingest-хендлеры зарегистрированы, `workers.consume_forever.starting streams=['ingress:max','ingress:max_global'] group=consumers` — consumer слушает оба потока.
10. **Границы соблюдены:** задач/записей у тенанта `b32a057a-…` не создавал, живую бронь 14 августа не трогал, smoke только read-only. Секреты в отчёт не попадали (chat_id группы — не секрет, он в REPLY главного окна).

**Процессная правка по REPLY №6:** cron-проверка пересоздана (id `c769cc82`) — маркер md5 теперь снимается ПОСЛЕ прочтения и отработки секции, а не в момент обнаружения. Причина слепого пятна №5 устранена.

**Rollback (предразрешён):** код — `git checkout 1ea47b915090cedb56698e42e1db122473965392` + build + `up -d`; конфиг — `cp .env.staging.bak-drf1029 .env.staging` + `up -d`.

### Сценарий live-приёмки для владельца (2 шага)

1. **DRF-911 (записи на глобальном пути):** написать пилотному боту в MAX про свои записи (например, «когда я записан?») — бот должен показать бронь на 14 августа («УЗ-кавитация»).
2. **DRF-1029 (уведомление об эскалации):** написать боту «позови менеджера» — в группу «IT Ayla» в MAX должна прийти карточка эскалации: салон, причина, время, id задачи и диалога, ссылка на задачу в админке. Клиентский сценарий при этом как раньше: диалог глушится для бота, задача видна в `/admin/handoff/admintask/`.

Done по §6 ставит главное окно после вашей приёмки.
