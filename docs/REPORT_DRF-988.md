# ОТЧЁТ окна-исполнителя → главное окно: DRF-988

Окно: исполнитель DRF-988. Бриф: `docs/IMPL_BRIEF_DRF-988.md`. Протокол: `docs/WINDOW_PROTOCOL.md`.
Монитор `REPLY_DRF-988.md` (md5, §2) поднят при старте, persistent.

---

## 2026-08-10 20:30 MSK — Фаза A: диагностика (завершена)

### A.1 — синхронизация и якоря

- Локальный клон `ai-bot-platform` синхронизирован с `origin/dev`. `origin/dev` = `9cd0a9ec450a9509afd79e5dbbe9f91c39fb8941` (VERIFIED, `git fetch` + `rev-parse`).
- SHA на хосте: `git -C /home/taximeter/ai-bot-platform-dev rev-parse HEAD` → `9cd0a9ec450a9509afd79e5dbbe9f91c39fb8941` — совпадает с ожиданием брифа и с `origin/dev` (VERIFIED, ssh read-only). Отслеживаемых изменений на хосте нет (только untracked .env-бэкапы).
- Якоря §3 брифа перепроверены на SHA `9cd0a9e`:
  - `apps/orchestrator/concierge.py:242` `build_concierge_system_prompt()` — текущей даты/таймзоны в системном промпте НЕТ (VERIFIED, чтение кода на origin/dev).
  - `legacy_maxbot/ai_concierge.py:316` — legacy-путь передаёт `today=timezone.localdate()` (VERIFIED).
  - `ayla-ai-core` (`prompts.py:93` «Сегодня: {today}», `composer.py`) — поддержка `today` есть, но глобальный путь использует свой `_renderer` в `generate_concierge_reply` и её обходит (VERIFIED).

### A.2 — путь реплики про даты (как было на пилоте 2026-08-10, приёмка владельца ~18:34 MSK)

Источник: логи `ayla-bot-staging-worker-1` (UTC в ts) + чтение БД (таблица сообщений, conversation `e8312259-3810-446f-8fa8-3166d4721fe3`, read-only).

Таймлайн (UTC; MSK = +3):

| Время (UTC) | Событие | Источник |
|---|---|---|
| 15:33:52 | user «Пенза» → GlobalMaxHandler → консьерж | лог |
| 15:33:55 | `ai_concierge action=show_masters` → `orchestrator.concierge.show_masters count=1` → карточка «Записаться к Тихонова Ольга» | лог |
| 15:34:01 | callback `cb:discover:book:b32a057a…:a8f3608c…:9af6afa4…` (с service, 127 симв.) → `_discovery_handoff_reply` → `skills.dispatch.match name=booking` | лог + БД |
| 15:34:03 | booking skill → assistant «Выберите дату:» + клавиатура дат **11–23 авг** | лог + БД |
| 15:34:08 | callback `cb:book:pick_date:a8f3608c-34d6-4085-a078-abbf613c9941:2026-08-11:a4f31641-8d1c-4dce-bd57-aae85b4e4ef8` пришёл **в GlobalMaxHandler и сохранён как обычное user-сообщение глобального диалога** (дословно, сырым payload'ом) | лог + БД |
| 15:34:11 | `ai_concierge action=text` → assistant: «К сожалению, я не могу записать вас на дату в 2026 году. Пожалуйста, выберите более ближайшую дату для записи на кавитацию.» | лог + БД |

Ответы на вопросы брифа:

1. **Кто предлагает даты 11–23 авг** — per-tenant booking skill (тенант formula-tela `b32a057a-…`), детерминированный date-picker (`_DATE_PICK_PROMPT` = «Выберите дату:», до 14 кнопок), даты — реальные свободные даты из YClients `get_available_dates(staff_id, service_ids)` (`apps/skills/booking/tools.py`, `show_slots`). НЕ LLM-текст. (VERIFIED)
2. **Кто отказывает** — глобальный LLM-консьерж (`apps/orchestrator/concierge.py` → `AIConcierge`, единственный tool = `show_masters`). Строки отказа нет ни в одном `.py` репо (`git grep` по origin/dev) — это сгенерированный моделью текст. (VERIFIED)
3. **Серверная валидация дат / booking-инструмент на глобальном пути** — ОТСУТСТВУЕТ. На глобальном пути нет ни booking-инструмента, ни какой-либо валидации дат; тап по дате до booking skill в этом ходу не дошёл (см. эскалацию ниже). Серверная валидация 2026 год НЕ отвергает. (VERIFIED)

### A.3 — вердикт о причине

**Причина отказа — отсутствие текущей даты в системном промпте глобального консьержа (базовая гипотеза брифа ПОДТВЕРЖДЕНА).** Модель получила user-сообщение `cb:book:pick_date:…:2026-08-11:…`, и, не зная, что сегодня 2026-08-10 (пилот MSK), трактовала 2026 год как далёкое будущее от своего training cutoff → сгенерировала отказ. (VERIFIED по связке: код промпта без даты + сырой payload в БД + текст отказа в БД; сама мотивация модели — INFERRED, но альтернативных механизмов в коде нет.)

Серверная валидация как причина ОПРОВЕРГНУТА (VERIFIED).

### Наблюдение по side-note DRF-989 (не чинить — зафиксировано)

`pii_protected_provider.no_active_scope provider=openai` воспроизводится на КАЖДОМ ходе глобального консьержа (логи 15:33:52 `messages_count=11`, 15:34:08 `messages_count=10` и др.) — вызов консьержа идёт без `pii_context()`. Детали: logger `apps.llm.pii_protected_provider`, уровень WARNING, провайдер openai. (VERIFIED). Не трогаю, per §5 брифа.

---

## 2026-08-10 20:30 MSK — ЭСКАЛАЦИЯ №1: тап по дате не маршрутизируется обратно в booking skill

**Факт (VERIFIED):** после успешного handoff (booking skill отдал date-picker) тап по дате `cb:book:pick_date:*` приходит в глобальный MAX-хендлер, который маршрутизирует дальше ТОЛЬКО `cb:discover:book:*` (`apps/channels/max/handler.py:621`); всё остальное уходит обычным текстом в консьерж. В `apps/orchestrator/` и `apps/channels/max/` нет никакой обработки `cb:book:*` (`git grep`). Docstring `apps/orchestrator/handoff.py` прямо фиксирует: «The full multi-turn booking session routed back through the global bot is a follow-up (after the P0 Ayla reground)».

**Почему это важно для DRF-988:** фикс промпта (дата в системный промпт) уберёт формулировку отказа «не могу записать в 2026 году», но НЕ даст «следующий шаг брони» из критерия приёмки §4 фазы D: у консьержа нет инструментов слотов/подтверждения, детерминированный slot-picker отрисуется только если тап дойдёт до booking skill. После prompt-only фикса ответ консьержа на тап даты станет недетерминированным LLM-текстом (в худшем случае — галлюцинированное «вы записаны» без реальной брони — риск для live-приёмки владельцем).

**Варианты на решение главного окна:**

- **Вариант 1 (минимальный, рекомендую):** в `_handle_global_max_event_inner` добавить ветку ДО консьержа: `cb:book:pick_date:` / `cb:book:pick_slot:` / `cb:book:confirm:` / `cb:book:cancel:` → тот же механизм, что в `_discovery_handoff_reply` (tenant_scope → resolve per-tenant bot_user/conversation → `skills.registry.dispatch` booking skill с payload как `message_text`). Без новых абстракций: один dispatcher-адаптер, ~40–60 строк + тесты. Booking skill сам детерминированно обработает pick_date/pick_slot (short-circuits уже есть). Это закрывает воронку до слотов и confirm.
- **Вариант 2 (строго по букве брифа):** только блок даты в `build_concierge_system_prompt()`. Отказ «2026 год» исчезнет, но следующий шаг брони останется LLM-текстом; приёмка «выбор даты → следующий шаг брони» под риском.
- **Вариант 3:** дата в промпт + заградительный детерминированный ответ на `cb:book:*` в глобальном хендлере («нажмите кнопку ещё раз / начните запись заново») — честно, но воронка не завершается, приёмку DRF-962 не разблокирует.

Действую по протоколу §2 («вопрос задал — работу не бросай»): **Фазу B в объёме брифа (блок даты + регресс-тест + PR) делаю сразу, не дожидаясь ответа.** Решение по Варианту 1 (маршрутизация) жду в REPLY — без него в объём не включу.

---

## 2026-08-10 20:30 MSK — Кандидаты в Linear (не создаю, per §5)

1. **Маршрутизация post-handoff callback'ов глобального бота** (`cb:book:pick_date/pick_slot/confirm/cancel` → tenant booking skill) — «multi-turn booking session routed back through the global bot», сейчас зафиксировано как follow-up в docstring `handoff.py`. Блокер воронки брони через глобального бота. (Если главное окно выберет Вариант 1 — это станет частью DRF-988; иначе нужна отдельная задача.)
2. **Сырые callback-payload'ы сохраняются как user-сообщения глобального диалога** и попадают в LLM-контекст консьержа (примеры в БД: `cb:anketa:start`, `cb:anketa:choice:gender:male`, `cb:book:pick_date:…`) — засоряют историю и провоцируют галлюцинации; стоит фильтровать/нормализовать на ingress.
3. (уже известно, DRF-989 side-note) консьерж вызывается без `pii_context()` — `pii_protected_provider.no_active_scope` на каждом ходе; наблюдение подтверждено логами.

---

## 2026-08-10 21:55 MSK — Фаза B: фикс и PR (в объёме брифа)

**Ветка:** `fix/drf988-concierge-today` (от `origin/dev` = `9cd0a9e`). **SHA фикса:** `7e1da0a`. **PR:** #1162 → `dev` (https://github.com/AndreyDeveloper84/ai-bot-platform/pull/1162). (VERIFIED)

**Изменение (минимальное, 2 файла):**

- `apps/orchestrator/concierge.py` — `build_concierge_system_prompt()` получает блок «Сегодня: YYYY-MM-DD (день недели), часовой пояс …» вторым абзацем системного промпта. Дата — параметр `today` (default `timezone.localdate()`, как в legacy-пути `ai_concierge.py:316`; день недели — локале-независимый массив; таймзона — `timezone.get_current_timezone()`). Остальной текст промпта (границы W5, no-sales, медицина) НЕ менялся. `ayla-ai-core` НЕ тронут.
- `apps/orchestrator/tests/test_concierge.py` — 3 регресс-теста: блок даты при явном `today`, default из часов, end-to-end (системный промпт, уходящий в LLM, содержит «Сегодня: …»).

**Проверки локально (VERIFIED):**

- `pytest apps/orchestrator/tests/test_concierge.py` — 16/16 зелёные.
- Per-file прогон всех `apps/orchestrator/tests/` (28 файлов, `-m "not slow"`): зелёные, кроме предсуществующих фейлов `test_otel.py` (1), `test_pipeline.py` (4), `test_pipeline_ai_metric_emission.py` (1), `test_shadow_short_circuit.py` (1) — **идентично падают на чистом `origin/dev`** (прогон baseline на detached HEAD, VERIFIED). К моему изменению отношения не имеют (concierge.py там не импортируется).
- `test_faq_latency.py` / `test_pipeline_latency.py` — `pytest.mark.slow`, в стандартный прогон CI не входят (локально висят >2 мин — окружение, не регресс).
- `ruff check` + `ruff format --check` — чисто; `mypy apps/orchestrator/concierge.py` — чисто; AST-гарды (`red_zone_guard.py`, `import_boundaries.py`) — чисто; `manage.py check` — 0 issues.

**CI PR #1162 (2026-08-10 22:05 MSK): ЗЕЛЁНЫЙ (VERIFIED, `gh pr checks 1162`)** — «pytest + ruff + mypy» pass (2m20s и 2m16s, оба прогона), «replay fixtures» pass (52s), «replay» skipped by design (prompt-regression-accepted bypass).

**Известная оговорка (INFERRED):** на пилоте `TIME_ZONE=UTC` (settings не переопределяют), поэтому «Сегодня» считается по UTC; расхождение с MSK возможно только в окне 21:00–24:00 MSK. Соответствует букве брифа (способ legacy-пути) и поведению booking skill (`now_local` = `timezone.localtime()`). Если главное окно хочет жёстко Europe/Moscow для пилота — отдельное решение (settings/tenant config), в этот PR не включал.

**Напоминание по эскалации №1:** PR убирает формулировку отказа, но «следующий шаг брони» (slot-picker после выбора даты) требует маршрутизации `cb:book:*` → booking skill (Вариант 1). Жду решение в REPLY.

---

## 2026-08-10 22:40 MSK — Вариант 1 реализован (GO владельца из чата)

**Решение:** владелец в чате окна выбрал **Вариант 1** (маршрутизация post-handoff callback'ов). (CLAIMED — решение получено устно в чате; дублирую здесь для шины.)

**Коммит:** `8d5391b` в ту же ветку `fix/drf988-concierge-today` (PR #1162, тело PR обновлено). (VERIFIED)

**Изменение (4 файла):**

- `apps/orchestrator/handoff.py` — новый `route_booking_callback()`: резолв тенанта из callback'а (master UUID через санкционированный carve-out `apps.marketplace.discovery.get_master` для `pick_master/pick_date/pick_slot`; `PendingBookingAction` token для `confirm/cancel`) → `tenant_scope(T)` → мост identity → `skills.registry.dispatch` с сырым payload (тот же entrypoint, что в handoff; booking skill НЕ модифицирован — его детерминированные short-circuits pick_date/pick_slot и gate confirm/cancel делают работу). Нерезолвимый тап → детерминированный «Контекст записи устарел. Начните выбор услуги заново.», никогда LLM. Post-dispatch handoff (#1047, AdminTask) симметричен handoff_to_booking. Событие `marketplace.booking_callback.routed` для воронки.
- `apps/channels/max/handler.py` — ветка `elif event.text.startswith(BOOKING_CALLBACK_PREFIXES)` ДО консьержа в `_handle_global_max_event_inner`.
- Тесты: 3 в `test_handoff.py` (dispatch в tenant_scope(T) с сырым payload; резолв тенанта по токену; отсев unknown/forged/int/garbage без dispatch) + 1 в `test_global_max_handler.py` (`cb:book:*` не уходит в консьерж).

**Проверки (VERIFIED):** `test_handoff.py` + `test_global_max_handler.py` 21/21; пакет channels + concierge + discovery 95/95; ruff/format/mypy чисто; **pre-commit import-boundary гард поймал `CatalogMaster.all_tenants` (MKT1) в первой версии — переписано на `marketplace.get_master`, гард Passed**. **CI PR #1162 (2026-08-10 22:55 MSK): ЗЕЛЁНЫЙ (VERIFIED, `gh pr checks 1162`)** — «pytest + ruff + mypy» pass ×2 (2m26s / 2m15s), «replay fixtures» pass (46s), replay skipped by design.

**Статус:** PR #1162 (коммиты `7e1da0a` + `8d5391b`) полностью готов к merge в `dev`. Жду решение главного окна: merge (кто и когда) + GO на фазу C (деплой) — по протоколу через REPLY.

**Границы изменения:** текстовые (не-кнопочные) ответы после handoff'а по-прежнему идут в консьерж (multi-turn TEXT booking через глобального бота — отдельная тема, не в объёме); flag-off (YClients int ids) тапы осознанно не резолвятся (int staff id не уникален между тенантами; пилот на BOOKING_VIA_AYLA_REST=True — VERIFIED на хосте).

---

## 2026-08-11 02:05 MSK — Merge в dev + подготовка фазы C (read-only)

**REPLY №1 и №2 получены и выполнены:**

- **№1/3 (не-persistence):** тапы `cb:book:*` больше не сохраняются как user-сообщения глобального диалога (skip `record_global_message` + `short_term.append` для booking-namespace; assistant-ответ записывается как раньше). Посторонний `cb:foo` — прежнее поведение (тест). Регресс `cb:discover:book:*` — ветка стоит после него, существующие handoff-тесты зелёные. Коммит `220a2f7`. (VERIFIED)
- **CI PR #1162 по всем трём коммитам: ЗЕЛЁНЫЙ** (VERIFIED, `gh pr checks`).
- **MERGE выполнен (№2.2):** обычный merge, НЕ squash — `gh pr merge 1162 --merge`. **Merge-SHA на `dev`: `d0ff7de897f7a38c2d3985a4794d502ca26f9fe7`** (`Merge pull request #1162`), содержит `7e1da0a` + `8d5391b` + `220a2f7`. (VERIFIED, `git log origin/dev`)
- **Монитор шины:** прежний bash-loop не доставлял алерты в сессию (вывод фоновых задач доходит только по завершении) — заменён на cron-проверку md5 каждые 2 мин. (CLAIMED о причине пропуска №1/№2)

**Подготовка фазы C (read-only, per №2.3):**

- Rollback-точка перепроверена: хост `/home/taximeter/ai-bot-platform-dev` на `9cd0a9ec450a9509afd79e5dbbe9f91c39fb8941` (VERIFIED, 2026-08-11 02:00 MSK).
- Bundle сформирован локально и проверен: `/tmp/drf988.bundle` (= `C:/Users/user/AppData/Local/Temp/drf988.bundle`), диапазон `9cd0a9e..origin/dev`, head `d0ff7de` (ref `refs/remotes/origin/dev`), `git bundle verify` — OK. Целевой SHA деплоя: `d0ff7de`.

**Деплой НЕ начинал** — жду секцию «GO НА ФАЗУ C» в REPLY (№2.3). Cron-монитор активен, среагирую сразу.

---

## 2026-08-11 08:45 MSK — Фаза C: деплой выполнен (GO №3)

**Действия (bundle-workflow, VERIFIED):**

1. Rollback-точка перед деплоем: хост на `9cd0a9ec450a9509afd79e5dbbe9f91c39fb8941` (VERIFIED 08:30 MSK). Откат предразрешён №3.2 — не понадобился.
2. `scp /tmp/drf988.bundle → хост:/tmp/` → `git fetch /tmp/drf988.bundle refs/remotes/origin/dev:refs/tmp/drf988` → `git checkout d0ff7de897f7a38c2d3985a4794d502ca26f9fe7`. **SHA на хосте: `d0ff7de`** (VERIFIED, `git rev-parse HEAD`).
3. `docker compose -p ayla-bot-staging -f docker-compose.yml -f docker-compose.staging.yml -f docker-compose.staging.local.yml build` → BUILD_EXIT=0 (web + worker). `up -d` — все сервисы Started.

**Health (VERIFIED, 08:35–08:42 MSK):**

- Контейнеры: web Up (healthy), worker / celery-worker / celery-beat Up; redis/postgres/minio не трогались (Up 3 days). `/healthz/` на :8014 → HTTP 200.
- Worker: 0 tracebacks за 5 мин; consumer жив: `workers.consume_forever.starting streams=['ingress:max', 'ingress:max_global'] group=consumers`.
- Web: 2 traceback за 5 мин — оба `events.emit_failed event=worker.subscriber_audit` (SynchronousOnlyOperation) = известный предсуществующий шум из брифа §4C.4, НЕ признак сломанного деплоя.

## 2026-08-11 08:45 MSK — Фаза D: smoke (своими силами, read-only, VERIFIED)

Прогон в контейнере `ayla-bot-staging-web-1` (python manage.py shell, только чтение):

1. **Промпт:** `build_concierge_system_prompt()` → «Сегодня: 2026-08-11 (вторник), часовой пояс UTC. Используй эту дату для парсинга относительных („завтра“, „послезавтра“) и конкретных дат записи.» — date-grounding жив в рантайме.
2. **Маршрутизация на реальных данных пилота:** `_resolve_booking_callback_tenant("cb:book:pick_date:a8f3608c-34d6-4085-a078-abbf613c9941:2026-08-11:a4f31641-…")` (точный payload провальной приёмки) → tenant `formula-tela` (`b32a057a-56c7-4bf0-ae50-e11e76ab44be`) — резолв через `marketplace.get_master` работает.
3. **Отсев мусора:** `cb:book:confirm:00000000-…` → None → детерминированный «Контекст записи устарел…», без dispatch.

Мутаций данных пилота не было (§5 брифа соблюдён, всё read-only).

---

## ГОТОВО К LIVE-ПРИЁМКЕ

**Да, готово (2026-08-11 08:45 MSK).** Для владельца (live-приёмка в MAX, только он):

Сценарий: кавитация → Пенза → «Записаться к Тихонова Ольга» → выбор предложенной даты (11+ авг) → **ожидаем: slot-picker «Выберите время:» с кнопками времени БЕЗ отказа «2026 год»** → выбор времени → карточка подтверждения (✅/❌) → ✅ → бронь создана.

Что задеплоено: SHA `d0ff7de` (= merge PR #1162: `7e1da0a` date-grounding + `8d5391b` маршрутизация cb:book:* + `220a2f7` не-persistence callback'ов). Rollback-точка `9cd0a9e` (откат предразрешён при проблемах). Done по DRF-988 — после вашей успешной приёмки (NO FALSE SUCCESS).
