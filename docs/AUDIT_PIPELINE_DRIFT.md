# Аудит расхождения: `apps/orchestrator/pipeline.py` против живого пути MAX

Дата: 2026-08-20
Дерево измерения: `C:/Users/user/PycharmProjects/ai-bot-platform-admin` (чистое, HEAD `9aa1fad`, `apps/orchestrator/pipeline.py` **побайтово совпадает с `origin/dev`** — проверено `git diff origin/dev -- apps/orchestrator/pipeline.py` = пусто).
Режим: только чтение. Ни одного изменения в репозиториях, ветки не переключались, git-индекс не тронут.

Классы доказательств: **VERIFIED** — запущено, вывод приведён; **INFERRED** — вывод из кода, указан файл:строка; **CLAIMED** — так написано в комментарии/доке; **UNKNOWN** — установить не удалось.

---

## Итог в пяти строках

1. Конвейер **технически живой**: импортируется без ошибок, все 92 его теста зелёные за 99 секунд (VERIFIED). Ни одна зависимость не исчезла и не сменила имя.
2. Конвейер **никогда не вызывался из ingress — ни одного дня за всю историю репозитория** (VERIFIED: `git log -S` по всем веткам не находит ни одного коммита, добавляющего или удаляющего такой вызов в `apps/channels/`, `apps/ingress/`, `apps/workers/`). Это не откат: подключения не было.
3. Причина найдена и задокументирована: 2026-07-02 в `docs/plans/2026-07-02-MVP_GAP_MAP.md:198` развилка поставлена явно — «**вкрутить или портировать**», и выбран порт. Три PR (S1-A/B/C) перенесли safety и handoff на живой путь, S1-D (`92f009b`) закрепил это парити-тестом с формулировкой «deliberately no risky refactor of live safety code».
4. Конвейер **заморожен с 2026-06-02**: 0 коммитов, 1537 строк как было. Живой `handler.py` за тот же период — 20 коммитов, 449 → 1172 строки (VERIFIED). Расхождение не «накопилось», оно росло в одну сторону при живом наблюдателе.
5. Подключать по-прежнему **нечего в смысле пилота**: пилотный бот — глобальный (tenant-less), а `pipeline.turn` при `tenant is None` выходит на шаге 1 с `unknown_tenant` (`pipeline.py:439-444`). Конвейер структурно не умеет обслуживать тот самый бот, ради которого его хотели бы оживить.

---

## Где постановка задачи оказалась неверна

Ставлю первым пунктом, как и просили.

**(1) «Может быть незаконченная работа, а может сознательный откат» — ни то, ни другое.**
Это **третий случай**: сознательный выбор *альтернативы*, принятый в письменном виде, исполненный четырьмя PR и закрытый эпиком. Ни отката (кода не удаляли), ни забытой недоделки (задача не висит в «In Progress»). Конвейер не отключали — его **обошли**, а его собственную ценность (safety) вынули и пересадили. Формулировка «не подключён» верна, но подразумеваемая ею вина отсутствует.

**(2) «Шаг 6 — классификация намерения через gpt-4o-mini» — в текущем конвейере она почти ничего не решает, но её включение ломает роутинг целиком.**
Постановка предполагает, что шаг 6 — дорогая опция, которую можно выключить флагом. На деле:
- шаг 10 (`pipeline.py:738-746` → `_dispatch_skill` `:1061-1086`) диспетчеризует **тем же самым** `apps.skills.registry.dispatch` с тем же «первый, чей `matches()` истинен». `intent_decision` не выбирает скилл — он кладётся в `SkillContext.intent` и используется в `pre_check` для эскалации риска (`safety/pre_check.py:273-289`);
- **но** сам факт `ctx.intent is not None` переворачивает `matches()` у половины реестра: `booking` (`apps/skills/booking/skill.py:280-282`) и `faq` (`apps/skills/faq/skill.py:125-127`) переключаются с ключевых слов на `intent.intent == …`, а `MenuSkill` (`apps/skills/menu/skill.py:105-106`) и `HelpSkill` (`apps/skills/menu/help_skill.py:44-47`) **полностью самоустраняются** — вся пилотная меню-поверхность `PILOT_CONVERSATIONAL_UX` (DRF-963) выключится в момент подключения.
- Это прямо зафиксировано в коде: `apps/skills/apps.py:78-81` — «production webhook dispatch does NOT set `ctx.intent` (unlike the orchestrator pipeline), so the legacy keyword fallbacks drive live routing and **this order is load-bearing**».

Вывод: «выключить шаг 6 флагом» — не смягчение, а **обязательное условие** любого поэтапного подключения; и одновременно оно обнуляет главную заявленную выгоду конвейера.

**(3) «Из 19 шагов какие дали бы новое поведение» — вопрос поставлен как «конвейер богаче». Он беднее.**
Новое поведение дают 6 шагов из 19, ещё 6 — частично, 8 живой путь уже делает (часто **теми же модулями**). Против этого конвейер **теряет** не менее 20 живых возможностей, включая идемпотентность вебхука, inline-клавиатуры, фото, callback-роутинг, онбординг/консент 152-ФЗ, команды памяти, мульти-ботовость и barge-guard над оператором. Подключение «как есть» — не апгрейд, а регресс.

**(4) «Устарел настолько, что подключать нечего» — тоже неверно.**
Ни одна зависимость не исчезла, ни одна публичная сигнатура не переименована (VERIFIED сравнением `4917e16` ↔ HEAD по 10 модулям). Расхождение **аддитивное**: вокруг конвейера выросли новые контракты, которые он не читает. Причём часть из них — `SkillResult.should_close_conversation`, `SkillResult.new_state`, `SkillContext.has_attachments` — существовали **уже в день рождения конвейера** (`git show 4917e16:apps/skills/base.py`) и не были учтены **никогда**. Это не устаревание, а неполнота с рождения.

**(5) «Не вызывается в проде» — уточнение.**
Формально верно, но живой путь **исполняет часть конвейера**: `safety/gate.py:95` зовёт тот же `pre_check`, `handler.py:1064-1072` — тот же `registry.dispatch`, `handler.py:1127-1131` — тот же `short_term`. Мёртва **композиция** (функция `turn` и её порядок), а не слои. Это меняет цену обеих ветвей развилки в меньшую сторону.

---

## Развилка

### Ветка A — оживлять конвейер

**Что это значит на деле:** заменить тело `MaxHandler.handle` (`apps/channels/handlers.py:52-56`) на `async_to_sync(turn)(ChannelMessage(...))` и затем **перенести в конвейер 1172 строки живого поведения**, иначе пилот теряет функции.

Цена:
- каркас (адаптер `CanonicalEvent` → `ChannelMessage`, sync/async мост): **1-2 чел.-дня** (понятно);
- перенос потерянного (список в таблице B1): идемпотентность, клавиатуры, фото, callback-и, онбординг/консент, команды памяти, `should_close_conversation`/`new_state`, barge-guard, crisis-копия с горячей линией, `bot_scope`, `reply_kind`: **12-20 чел.-дней** (понятно по составу, не по стоимости каждого);
- глобальный (tenant-less) путь: **архитектурная переделка шага 1** — конвейер построен на `tenant_scope`, пилот работает без тенанта. **5-10 чел.-дней, оценка ненадёжна** (UNKNOWN — зависит от того, как решать sentinel-тенант);
- салонный бот (`salon_handler.py`) конвейер не покрывает и не должен — остаётся третьим входом навсегда;
- тесты: **119 тест-функций** в 14 файлах, драйвящих живые хендлеры (VERIFIED подсчётом), плюс `test_handler_safety_parity.py` (6 тестов), который специально существует, чтобы **ловить именно такую переделку**;
- канареечный вывод по `docs/runbooks/canary-ramp.md`: минимум 6 дней при 5 зелёных критериях.

**Итого A: 20-35 чел.-дней + канарейка.** Риски: одномоментный переворот роутинга (п. 2 выше) — нельзя выкатывать по кусочку; регресс safety на пилотном боте в момент переезда; LLM-вызов на каждый ход (латентность/стоимость, бюджет p95 ≤ 4000 мс из докстринга `pipeline.py:44-47` никогда не проверялся на живом трафике — UNKNOWN).

### Ветка B — узаконить нынешнее меню и чинить по канону

**Что это значит:** объявить `handler.py` каноническим входом (он уже им является де-факто и де-юре по `MVP_GAP_MAP:198`), пометить `pipeline.py` как «инструмент replay/эталон контракта, не прод», и **перенести на живой путь те 6 шагов, что реально дают новое поведение** — по одному, каждый со своим флагом.

Цена (по шагам, каждый отдельно поставляемый):
- шаг 12 `post_check` исходящего текста: **1-2 чел.-дня** (модуль готов, `safety/post_check.py:127`, вызывается только из конвейера);
- шаг 18 replay-recorder на живом пути: **2-3 чел.-дня** (`replay/recorder` сегодня зовёт только конвейер и оффлайн-раннер);
- `AIRequestMetric` / `record_ai_request` на живом пути: **1-2 чел.-дня** (сейчас на живом пути метрика не пишется вовсе — `pipeline.py:218-312`);
- OTel root-span + step-события на живом пути: **1-2 чел.-дня**;
- шаг 10.5 confidence-floor (`pipeline.py:162-215`): **1 чел.-день**;
- шаг 5 единый memory-snapshot в concierge (`memory/coordinator.py:67`): **2-4 чел.-дня**;
- шаг 9 (AdminTask по safety-вердикту на per-tenant пути) — **уже сделан** в S1-C (`handler.py:325-376`), закрывать нечего;
- плюс уже стоящая в бэклоге строка S1.6 «de-drift двух MAX-хендлеров» — **8 SP** (`docs/plans/2026-07-02-MVP_DELIVERY_TRACKER.md:105`).

**Итого B: 10-15 чел.-дней, инкрементально, без канарейки и без единой точки отказа.**
Риски: LLM-роутинг не появляется — качество маршрутизации остаётся на потолке ключевых слов, а порядок объявлений в `apps/skills/apps.py` остаётся несущей конструкцией (это признано в самом файле, строки 60-100, и это **хрупко**: любой новый скилл, вставленный не туда, тихо перехватывает чужие ходы). Второй риск: `pipeline.py` продолжает висеть как 1537 строк, которые CI гоняет 99 секунд каждый прогон, и как ловушка для следующего аудитора.

### Рекомендация измерителя

Развилка не симметрична. Ветка A стоит вдвое-втрое дороже, требует канарейки, ломает пилотное меню в момент включения и **не решает главную проблему пилота** (глобальный бот вне досягаемости конвейера). Ветка B дешевле, дробится на поставляемые куски и не трогает живой пилот.

Но у ветки B есть обязательное дополнение, иначе она вырождается: **явно закрыть судьбу `pipeline.py`** — либо переместить в `apps/replay/` как эталон контракта, либо пометить `DEPRECATED` в докстринге. Сегодня в файле **ни одного маркера TODO/FIXME/DEPRECATED** (VERIFIED грепом по `apps/orchestrator/`), а его докстринг (`pipeline.py:3-4`) до сих пор утверждает, что он берёт сообщения «from the ingress consumer». Это ложное утверждение в коде дороже самого кода.

---

## A. Жив ли конвейер технически на текущем HEAD

### A.1 Импорт — **VERIFIED, успешно**

```
$ DJANGO_SETTINGS_MODULE=config.settings.local python -c "import django; django.setup(); import apps.orchestrator.pipeline as p; print('IMPORT OK', p.__file__); print('turn:', p.turn)"
...
IMPORT OK C:\Users\user\PycharmProjects\ai-bot-platform-admin\apps\orchestrator\pipeline.py
turn: <function turn at 0x0000014F4EFDCA40>
```

### A.2 Собственные тесты — **VERIFIED, все зелёные**

Способ запуска взят из `.github/workflows/ci.yml:87-166` — CI гоняет `uv run pytest` прямо на раннере (не в докере; `Makefile:test` с докером — для dev-стека). Воспроизведено локальным `.venv` того же дерева.

```
$ python -m pytest apps/orchestrator/tests/test_pipeline.py \
    apps/orchestrator/tests/test_pipeline_ai_metric_emission.py \
    apps/orchestrator/tests/test_pipeline_latency.py \
    apps/orchestrator/tests/test_sentry_pipeline_capture.py \
    apps/orchestrator/tests/test_shadow_short_circuit.py \
    apps/orchestrator/tests/test_pii_pipeline_integration.py \
    apps/orchestrator/tests/test_otel.py apps/orchestrator/tests/test_otel_spans.py \
    apps/orchestrator/tests/test_faq_latency.py \
    tests/integration/test_pipeline_turn.py \
    tests/e2e/test_orchestrator_e2e.py tests/e2e/test_faq_e2e.py \
    tests/e2e/test_observability_stack.py \
    apps/ingress/tests/test_shadow_header.py -p no:cacheprovider --no-header

........................................................................ [ 78%]
....................                                                     [100%]
92 passed in 99.21s (0:01:39)
PYTEST_EXIT=0
```

Ограничение (честно): прогон был на SQLite — дефолт `config.settings.local`. Против Postgres я не гонял **намеренно**: свободного postgres-контейнера нет, а два запущенных (`pg-drf1144`, `ayla-e2e-backend-db-1`) принадлежат чужой живой работе. Косвенное подтверждение по Postgres — **CLAIMED**: `.github/workflows/ci.yml:223-248` перечисляет 26 deselect'ов полного прогона `apps/` против Postgres, и **ни одного** из `apps/orchestrator/` или `tests/` среди них нет.

### A.3 Дрейф зависимостей с DRF-535 — **исчезнувших нет, сигнатуры целы**

Сравнение публичных символов `4917e16` (DRF-535, 2026-05-12) ↔ HEAD по 10 модулям (INFERRED, `git show 4917e16:<file>` против рабочего файла):

| Модуль | Что изменилось | Ломает конвейер? |
|---|---|---|
| `apps/orchestrator/composer.py` | ничего | нет |
| `apps/orchestrator/memory/coordinator.py` | ничего | нет |
| `apps/orchestrator/safety/post_check.py` | ничего | нет |
| `apps/orchestrator/safety/pre_check.py` | те же символы, сдвиг строк | нет |
| `apps/skills/registry.py` | `dispatch(context)` без изменений | нет |
| `apps/orchestrator/intent_router.py` | `classify()` та же сигнатура + 2 внутренних пути (`_classify_production_path` `:172`, `_classify_legacy_path` `:323`) | нет — конвейер уже мигрирован (`pipeline.py:650-655`) |
| `apps/skills/base.py` | `SkillContext.intent` добавлено (было использовано конвейером) | нет |
| `apps/conversations/services.py` | +`write_skill_state` `:378`, +`resolve_active_global_conversation` `:448`, +`record_global_message` `:536` | нет, но конвейер их **не знает** |
| `apps/orchestrator/channel_registry.py` | **новый модуль** (`18ac5fc`, 2026-05-14); конвейер на него переведён | нет |
| `apps/identity/services/bot_user_resolver.py` | новый | нет |

**Ноль исчезнувших. Ноль переименованных.** Настоящий дрейф — не в сигнатурах, а в том, что мимо конвейера выросли три новых входа (`ingress:max_global`, `ingress:max_salon`) и ~20 новых возможностей (раздел B).

### A.4 Скорость расхождения — **VERIFIED**

```
handler.py коммитов с 2026-06-02:   20
pipeline.py коммитов с 2026-06-03:   0
handler.py строк: 449 → 1172
pipeline.py строк: 1537 → 1537
```

Последний коммит в `pipeline.py` — `eaf8dbf` от **2026-06-02** («#975 intent_router production LLMProvider + audit row»). Всего 16 коммитов за жизнь файла, все — «навесить наблюдаемость на то, что никто не зовёт».

### A.5 Кто импортирует конвейер — **VERIFIED**

Точный греп по формам импорта даёт 18 мест: **17 тестов** + `apps/replay/__main__.py:85`. Постановка подтверждена. (`apps/replay/runner.py` конвейер **не** импортирует — он принимает `pipeline_fn` через DI, `runner.py:100-117`.)

---

## B. Что живой путь делает мимо конвейера, и наоборот

### B.1 Появилось/живёт на живом пути, в конвейере отсутствует

Все строки — INFERRED (чтение кода, проверено грепом на отсутствие в `pipeline.py`).

| Возможность | Живой путь (файл:строка) | В `pipeline.py` | Чем оборачивается подключение «как есть» |
|---|---|---|---|
| Идемпотентность вебхука (`with_idempotency`/`AlreadyClaimed`) | `apps/channels/max/handler.py:492-512`, `:555-575`; `salon_handler.py:141-165` | **нет** (грепа 0) | PEL-ретрай задвоит Message, память и отправку |
| Tolerate-and-skip на `ParseError` | `handler.py:461-478`, `:543-551` | **нет** — `turn()` принимает готовый `ChannelMessage` (`pipeline.py:356-384`) | lifecycle-апдейты штормят PEL |
| Глобальный (tenant-less) путь | `handler.py:515-575`, `:578-890`; регистрация `apps/channels/handlers.py:59-83` (`requires_tenant=False`) | **структурно невозможен** — `pipeline.py:439-444` → `unknown_tenant` | **пилотный бот не обслуживается вовсе** |
| Салонный/staff бот (панель, без LLM и скиллов) | `salon_handler.py:194-256`; `handlers.py:87-113` | **нет** | третий вход остаётся снаружи |
| Кризисная копия с горячей линией 8-800-2000-122 | `apps/orchestrator/safety/gate.py:50-58` | **другая копия** — `_FALLBACK_BLOCK` `pipeline.py:152`, `_FALLBACK_HANDOFF` `:158` | ответ на суицид теряет номер линии |
| CLARIFY **не** короткозамыкается (осознанно) | `gate.py:16-20`, `:115-116` | **обратное поведение** — `pipeline.py:689-710` | «почему болит спина» не дойдёт до health_screening/booking |
| Barge-guard: не перебивать живого оператора | `handler.py:992` (`state != HUMAN_HANDOFF`) | **нет** | канонированный кризисный ответ поверх оператора |
| Loud-alert при провале доставки кризисного ответа | `handler.py:277-322`, вызовы `:866`, `:1003` | **нет** | молчаливая потеря |
| PII-safe emit о срабатывании safety | `handler.py:259-274` | **нет** | |
| Mute глобального пути, пока ведёт оператор | `handler.py:633-642` → `apps/orchestrator/handoff.py:616` | **нет** | |
| `should_close_conversation` (privacy/удаление данных) | `handler.py:1104`, `:1117`, `:1141-1146` | **нет никогда** (поле есть в `SkillResult` с 2026-05-12) | запись в удалённую беседу |
| `new_state` (переход состояния по запросу скилла) | `handler.py:1138-1140` | **нет никогда** | |
| Полное молчание при `should_send=False` | `handler.py:1093-1099`; `registry.py:105-113` | **частично, с дефектом** — `pipeline.py:842-852` всё равно пишет пустой assistant-Message и short_term | мусор в истории |
| Фото: скачивание, SSRF-guard, лимит 10 MiB, no-redirect | `handler.py:1031-1052`; `apps/channels/max/photo.py:65`, `:176-250` | **нет** (грепа `photo` = 0) | food_scanner слеп |
| `has_attachments` в `SkillContext` | `handler.py:1070` | **нет** — `pipeline.py:1079-1085` не передаёт | attachment-ветки скиллов мертвы |
| Inline-клавиатуры в исходящем | `handler.py:198-256` (`_build_attachments`), отправка `:1156` | **теряются** — `channel_registry.send(channel, chat_id, text)` (`channel_registry.py:91`), адаптер `apps/channels/apps.py:28-47` не принимает `attachments` | **все кнопки пропадают** |
| Мульти-бот (`bot_scope` / выбор токена) | `salon_handler.py:216-233`; `apps/channels/bot_context.py:31`; `outbound.py:87-94` (`bot=`) | **нет** — адаптер `apps/channels/apps.py:44` зовёт `send_message(chat_id=, text=)` | staff получит ответ клиентским аватаром |
| `mark_seen` / `typing_on` до тяжёлой работы | `handler.py:948-952` | **нет** | |
| Онбординг + журнал согласия 152-ФЗ | `handler.py:724-727`; `global_onboarding.py:105-188` | **нет** | |
| Консент-гейт памяти (`can_store_green_memory`) | `handler.py:744-750`; `apps/consent/memory.py:47` | **нет** | |
| Чат-команды 152-ФЗ («покажи что знаешь» / «забудь») | `handler.py:754-771`; `apps/persona/memory_commands.py:129` | **нет** | |
| Персональный контекст в промпт (consent-gated) | `handler.py:803-822`; `apps/orchestrator/memory_block.py:72` | **другая память** — `load_snapshot` без консент-гейта | |
| Проактивный вопрос памяти (weave) | `handler.py:781`, `:843` | **нет** | |
| Concierge LLM (ayla-ai-core) + `reply.persisted` | `handler.py:826-839`, `:854-862` | **нет** — своя пара `classify`+`compose` | две несовместимые архитектуры генерации |
| Роутинг callback-ов `cb:discover:*` / `cb:book:*` / `cb:visit:*` / `cb:welcome:*` | `handler.py:700-736`, `:893-936` | **нет** (грепа `cb:` = 0) | нажатия кнопок уйдут в LLM как текст |
| Booking-callback не пишется в историю | `handler.py:598-607` | **нет** — `pipeline.py:624-625` пишет всегда | |
| Rate-limit на попытки invite-кода | `salon_handler.py:359-360`; `apps/identity/services/staff_invites.py:217-255` | **нет** | |
| Аналитика `reply_kind` | `handler.py:379-415`, emit `:1158-1166` | **нет** — `MESSAGE_SENT` только с `intent` (`pipeline.py:1101-1111`) | |
| Аварийный echo-fallback при пустом реестре | `handler.py:418-429`, `:1101` | **обратное** — `_FALLBACK_CLARIFY` + `ok=False` (`pipeline.py:747-770`) | |

**Флаги.** Живой путь читает `GLOBAL_BOT_ONBOARDING`, `CONCIERGE_MEMORY_ENABLED`, `BOOKING_VIA_AYLA_REST`, `MAX_BOT_REGISTRY`/`GLOBAL_BOT_TOKENS`/`MAX_BOT_<SLUG>_TENANT_SLUG`, `MAX_MINIAPP_URL`, `RFM_THRESHOLDS`, `PILOT_CONVERSATIONAL_UX`, `NUTRITION_ENABLED`, `FOOD_PHOTO_SCAN_ENABLED`, `CERTIFICATE_PAYMENT_ENABLED` и др. Конвейер знает **два своих** (`SKILL_CONFIDENCE_HANDOFF_THRESHOLD` `pipeline.py:198`, `AI_CONFIDENCE_HANDOFF_THRESHOLD` `:205`) — их живой путь не читает вообще. Пересечение флагов между двумя мирами: `SAFETY_PATTERNS`, `SHORT_TERM_MEMORY_*`/`REDIS_URL` и косвенно `MAX_BOT_TOKEN`. Всё.

### B.2 Шаги конвейера против живого пути

| Шаг | Что делает (`pipeline.py`) | Аналог на живом пути | Новое поведение? |
|---|---|---|---|
| 1 | tenant resolution (`:436`, хелпер `:962`) | резолв раньше, на ingress: `apps/ingress/views.py:126-142`, скоуп `apps/workers/base.py:432` | нет |
| 2 | `resolve_or_create_bot_user` (`:596`) | `handler.py:954-958`, `:581-585`, `salon_handler.py:209-214` | нет |
| 3 | conversation (`:600`) | `handler.py:959`, `:586`; у салонного бота Conversation нет by design | частично (`is_shadow`, `:993-1003`) |
| 4 | save user Message (`:623`) | `handler.py:965-970`, `:604-607` — `record_message` даже богаче (эмит `conversations.message.stored`, `services.py:277`) | нет |
| 5 | memory snapshot (`:627`) | **не нашёл** на per-tenant пути; на global только `short_term.recall` (`handler.py:590`) | **да** |
| 6 | intent classification (`:631`) | **нет** — прямо задокументировано в `safety/gate.py:91-93` | **да, но см. раздел «где постановка неверна», п. 2** |
| 7 | safety pre-check (`:660`) | **тот же модуль**: `handler.py:991`, `:685` → `gate.py:95` → `pre_check` | нет |
| 8 | BLOCK/CLARIFY short-circuit (`:666`) | BLOCK/HANDOFF есть (`handler.py:992-1015`); CLARIFY осознанно нет (`gate.py:115-116`) | частично |
| 9 | handoff → AdminTask по safety (`:712`) | на per-tenant пути по safety-вердикту AdminTask **не** создаётся (`handler.py:977-983`) | **да** |
| 10 | skill dispatch (`:738`) | **тот же** `registry.dispatch` (`handler.py:1064-1072`) | нет |
| 10.5 | post-skill handoff + confidence-floor (`:772`) | `should_handoff` есть (`handler.py:1085-1087`, `:325-376`); confidence-floor нет | частично |
| 11 | tool invocation (`:828`) | заглушка в обоих; скиллы зовут tools сами (`apps/skills/booking/skill.py:495`, `:525`, `:628`) | нет |
| 12 | safety post-check (`:831`) | **нет** — `post_check` зовётся только из конвейера | **да** |
| 13 | compose (`:837`) | **нет** — живой путь берёт `skill_result.reply_text` (`handler.py:1101`) + `_build_attachments` (`:198-256`) | **да**, но клавиатуры живой путь делает лучше |
| 14 | save assistant Message (`:841`) | `handler.py:1118-1126`, `:855-862`, `:354-361` | нет |
| 15 | short-term memory (`:850`) | `handler.py:1127-1131`, `:863`, `:362` | нет |
| 16 | emit `message_sent` (`:854`) | не канонический — `channels.max.outbound.sent` (`handler.py:1158-1166`) | частично |
| 17 | сводный audit `pipeline.turn.completed` (`:860`) | сводки на ход нет; пишут смежные слои (`apps/conversations/services.py:165`, `apps/llm/router.py:246`) | частично |
| 18 | replay `recorder.capture` (`:867`) | **нет** — рекордер зовут только конвейер и оффлайн-раннер (`apps/replay/runner.py:163`) | **да** |
| 19 | outbound + retry-3x + DLQ + shadow (`:879`) | отправка есть (`handler.py:1156`); ретраев/DLQ/shadow нет — надёжность на PEL консьюмера (`handler.py:50-59`) | частично |

**Сводка:** новое поведение дают **6 шагов** (5, 6, 9, 12, 13, 18); **6 частично** (3, 8, 10.5, 16, 17, 19); **8 живой путь уже делает** (1, 2, 4, 7, 10, 11, 14, 15), причём шаги 7 и 10 — буквально теми же функциями.

Вне 19 шагов, но всплывает: PII-скоуп `_pii_enter(conversation.id)` (`pipeline.py:621`) на живом пути не ставится, и `record_ai_request`/`AIRequestMetric` (`pipeline.py:218-312`) на живом пути не пишется **вовсе**.

---

## C. Почему не подключён

### C.1 Вызова из ingress не было **никогда** — VERIFIED

```
$ git log --all --oneline -S "orchestrator.pipeline" -- apps/channels/ apps/ingress/ apps/workers/ apps/skills/ config/
9561e81 feat(ingress): X-Shadow header threads through enqueue (DRF-701) (#46)

$ git show --stat 9561e81
 apps/ingress/tests/test_shadow_header.py | 139 ++++++
 apps/ingress/views.py                    |   8 ++
 apps/orchestrator/pipeline.py            |   6 ++
```

Единственный коммит, где строка `orchestrator.pipeline` вообще касалась ingress, добавил её **в тест**, не в продовый код. `git log -S "pipeline.turn" --all` и `git log -S "from apps.orchestrator.pipeline" --all` дают 25 и 17 коммитов соответственно — все в `apps/orchestrator/`, `apps/replay/`, `tests/`, `docs/`.

**Вывод: искать «коммит, который убрал вызов» бессмысленно — вызова не существовало ни минуты.**

### C.2 Почему его не появилось: план Sprint 6 его не содержал — INFERRED/CLAIMED

`docs/plans/sprint-6-orchestrator-rfm.md:10` заявляет: «Sprint 6 finally wires the **production pipeline** … Without this, every previous sprint's component is plumbing nobody calls». Но в декомпозиции Track O (задачи O1-O10, строки 48-58) **нет задачи «перевести ingress-консьюмер на turn()»**. Единственная задача «swap … → turn()» во всём спринте — **I3 / DRF-547: «Swap replay CLI default `pipeline_fn` → `orchestrator.turn`»** (`sprint-6-orchestrator-rfm.md:65`; в Linear — DRF-547, статус **Done**).

Хронология объясняет разрыв:
- **DRF-471** «Wire skill dispatcher into `channels/max/handler.py`» (Linear, Done, 2026-05-12) — живой путь получил диспетчер скиллов **до** того, как конвейер был написан;
- **DRF-535** (Done, оценка **2 SP**) — конвейер написан **в тот же день**;
- ни одна задача не соединила первое со вторым.

Конвейер был построен как **параллельная реализация того же самого**, а не как замена. Exit-gate спринта (строка 24: «orchestrator pipeline runs end-to-end with FAQ stub skill») проверялся e2e-тестом, а не продом — и был честно закрыт.

### C.3 Решение о судьбе принято 2026-07-02 и исполнено — VERIFIED (документ + 4 коммита)

`docs/plans/2026-07-02-MVP_GAP_MAP.md:198`:

> «Судьба `pipeline.turn()`: **вкрутить или портировать** pre_check/should_handoff в оба хендлера, убрать дрейф (#1053).»

и строка 77:

> «Safety pre/post-check — в `pipeline.turn()` — **dead code в проде** 🔴 — Вкрутить pre_check в оба MAX-хендлера.»

и строка 406: «ai-bot-platform #1053 | S1 | pipeline.turn() dead code (safety)».

Выбран **порт**. Исполнение (VERIFIED по истории):

| Коммит | Дата | Что |
|---|---|---|
| `9b9535f` / `8a0710b` (PR #1084) | 2026-07-03 / 07-04 | **S1-B**: `safety/gate.py::evaluate_inbound()` — обёртка `pre_check` для живых хендлеров + barge-guard над оператором + расширение регексов суицида (#1081) |
| `136936c` (PR #1089) | — | **S1-C**: `should_handoff` → AdminTask + HUMAN_HANDOFF mute (#1047) |
| `92f009b` (PR #1102) | 2026-07-05 | **S1-D**: кросс-хендлерный парити-тест `apps/channels/tests/test_handler_safety_parity.py` |
| `bf80c41` | — | «G-Safety CLOSED — S1 epic complete (S1-D #1102 merged)» |

Формулировка решения — в теле `92f009b`, дословно:

> «De-drift assessment (**deliberately no risky refactor of live safety code**): the safety/handoff detection is already unified via the shared helpers; the remaining split … is intrinsic to tenant vs tenant-less persistence and is NOT merged — forcing it would risk subtle bugs. **The parity test is the safe, CI-enforced de-drift guarantee.**»

Это и есть искомый след: **сознательный выбор дешёвой ветки под давлением пилота**, с явной оценкой риска альтернативы.

### C.4 Что осталось незакрытым

- `docs/plans/2026-07-02-MVP_DELIVERY_TRACKER.md:105` — строка **S1.6 «de-drift двух MAX-хендлеров», #1053, 8 SP, W6, статус `Backlog`**. Единственный живой хвост темы.
- В Linear задачи «подключить конвейер к ingress» **не существует** (VERIFIED поиском по `pipeline.turn orchestrator` и `de-drift MAX handler дрейф`: DRF-400 Backlog, DRF-535 Done 2 SP, DRF-706/717/542/553/598 Done, DRF-633 Todo — все про сам конвейер и его тесты, ни одна про интеграцию).
- `docs/runbooks/canary-ramp.md:84` до сих пор задаёт критерий канарейки как «`pipeline.turn` span p95» — **CLAIMED, и это утверждение ложно**: на живом трафике такого спана не будет никогда. Раннбук канареечного вывода пилота опирается на метрику, которой не существует.

### C.5 Следов, которых **нет** — тоже результат

- **ни одного** маркера `TODO` / `FIXME` / `DEPRECATED` / `XXX` / `HACK` во всём `apps/orchestrator/` и во всём `apps/ingress/`, `apps/channels/max/`, `apps/workers/` (VERIFIED грепом);
- в `CLAUDE.md` про конвейер **ни слова** (VERIFIED);
- ADR, объявляющего `handler.py` каноническим входом, **нет** — статус живого пути держится только на строках в планах от 2026-07-02;
- докстринг `pipeline.py:1-8` до сих пор описывает конвейер как принимающий сообщения «from the ingress consumer» — **не помечен как устаревший**.

---

## D. Цена подключения

### D.1 Минимум, чтобы ingress звал `turn()`

Точка врезки — `apps/channels/handlers.py:52-56`:

```python
def handle(self, payload: dict[str, Any]) -> None:
    trace_id = current_trace_id()
    max_handler.handle_max_event(payload, trace_id=trace_id)
```

Нужно (INFERRED):
1. **Адаптер `CanonicalEvent` → `ChannelMessage`.** `parser.CanonicalEvent` (`apps/channels/max/parser.py:73-88`) не имеет `tenant_slug` и `display_name`, зато имеет `channel_message_id`, которого нет в `ChannelMessage` (`pipeline.py:378-385`). `tenant_slug` придётся доставать из `current_tenant()`, а `channel_message_id` — **терять**, вместе с идемпотентностью.
2. **Sync→async мост.** `TenantAwareTask.handle` синхронный, `turn()` — `async` (`pipeline.py:411`). Нужен `async_to_sync`, что в ASGI-воркере даёт свой набор вопросов (UNKNOWN — как это ляжет на `apps/workers/base.py`).
3. **Сохранить `with_idempotency`** снаружи вызова, иначе PEL-ретрай задвоит всё.
4. **Расширить `ChannelSender`** до `(chat_id, text, attachments, bot)` — иначе клавиатуры и мульти-бот теряются (`channel_registry.py:91`, `apps/channels/apps.py:28-47`).

Каркас без п.4 и без переноса функций — **1-2 чел.-дня**. Работоспособного пилота это не даёт.

### D.2 Можно ли выключить шаг 6 флагом

**Готового флага нет — VERIFIED** (греп по `INTENT_ROUTER|INTENT_CLASSIF|ENABLE.*INTENT` в `config/` и `apps/` даёт только словарь событий `apps/events/vocabulary.py:39`).

Выключить **технически легко**: `classify()` при ошибке/открытом брейкере уже возвращает `_FALLBACK_INTENT` (`intent_router.py:127-133`, `:371`), а `pipeline.py:650-655` терпит `intent_decision is None`. Достаточно `if settings.INTENT_ROUTER_ENABLED: … else: intent_decision = None` — **полдня**.

**Но флаг обязан быть в положении OFF, иначе ломается роутинг** (см. «где постановка неверна», п. 2): при `ctx.intent is not None` `MenuSkill` и `HelpSkill` самоустраняются, `booking` и `faq` переключаются с ключевых слов на метку намерения. Это не постепенная деградация, а мгновенный переворот.

Следствие для оценки: **безопасное подключение = подключение с выключенным шагом 6**, то есть без LLM-роутинга. А с выключенным шагом 6 конвейер даёт ровно 5 новых шагов из 19 — и все пять (5, 9, 12, 13, 18) переносятся на живой путь по отдельности дешевле, чем переезд целиком.

### D.3 Какие тесты сломаются

- **119 тест-функций** в 14 файлах драйвят живые хендлеры напрямую (VERIFIED подсчётом `def test_`): `apps/channels/max/tests/test_handler*.py` (48), `apps/channels/tests/test_global*.py` + `test_handler_safety_parity.py` (62), `tests/e2e/test_max_echo.py` + `test_privacy_skill.py` (5). Все они станут либо невалидными, либо будут тестировать мёртвый код.
- Всего в `apps/channels/*/tests/` — **304 тест-функции** (VERIFIED); сколько из них переживёт переезд — **UNKNOWN**, зависит от того, сохраняется ли `handler.py` как слой.
- `apps/channels/tests/test_handler_safety_parity.py` (6 тестов) написан **специально**, чтобы падать при односторонних изменениях safety-поведения хендлеров — то есть он сработает как задумано и заблокирует такую переделку до её осознанного пересмотра.
- 92 теста самого конвейера **не сломаются** — они уже зелёные и от подключения не зависят.

### D.4 Оценка в человеко-днях

**Понятно (можно планировать):**

| Работа | Дней |
|---|---|
| Каркас врезки (адаптер + async-мост + сохранение идемпотентности) | 1-2 |
| Флаг на шаг 6 (`INTENT_ROUTER_ENABLED`, дефолт OFF) | 0.5 |
| Расширение `ChannelSender` до attachments + bot_scope | 1-2 |
| Перенос в конвейер: фото, callback-роутинг, `should_close_conversation`/`new_state`, barge-guard, crisis-копия, `reply_kind`, `mark_seen/typing` | 6-9 |
| Перенос: онбординг + консент 152-ФЗ + команды памяти + consent-гейт памяти | 4-6 |
| Переписать/перенацелить 119 хендлерных тестов | 3-5 |
| Канарейка по `docs/runbooks/canary-ramp.md` (5 критериев, 4 ступени) | 6 календарных дней, ~2 чел.-дня |
| **Подытог** | **18-27 чел.-дней** |

**Неизвестно (нельзя планировать без отдельного решения):**

- **Глобальный tenant-less путь.** Конвейер построен вокруг `tenant_scope`; пилотный бот работает без тенанта. Либо переделка шага 1 под sentinel-тенант, либо конвейер обслуживает только per-tenant ботов, а пилот остаётся на `handler.py` — **и тогда вся затея не решает задачу пилота**. Оценка: **5-10 дней или бесконечность**, зависит от продуктового решения. **UNKNOWN.**
- **Латентность и стоимость LLM-классификации на живом трафике.** Бюджет p95 ≤ 4000 мс (`pipeline.py:44-47`) проверялся только SLO-тестом с моком. Реальный трафик — **UNKNOWN**.
- **Продуктовое решение по `PILOT_CONVERSATIONAL_UX`**: меню-поверхность и LLM-роутинг взаимоисключающи в текущем коде. Кто из них канон — **вопрос не к инженерии**. **UNKNOWN.**
- **Салонный бот** (`salon_handler.py`) — вне конвейера by design (`apps/channels/handlers.py:106-108`), останется третьим входом. Дрейф трёх входов вместо двух.

**Итого ветка A: 20-35 чел.-дней при закрытых «неизвестных», иначе не оценивается.**
**Итого ветка B: 10-15 чел.-дней, дробится на 6 независимых поставок.**

---

## Методика и ограничения

- Все измерения — на дереве `ai-bot-platform-admin` (чистое, `git status --porcelain` пуст на момент начала). Основное дерево `ai-bot-platform` не трогалось: в нём 8 изменённых и 10+ новых файлов чужой живой работы.
- `pipeline.py` в этом дереве **идентичен `origin/dev`**; дерево отстаёт от `origin/dev` на 7 коммитов, ни один из них конвейер не касается (последнее изменение конвейера — 2026-06-02).
- Тесты запущены на SQLite (дефолт `config.settings.local`). Postgres-прогон **не выполнялся намеренно** — свободного контейнера нет, а занимать чужие рискованно. Замена: CI-конфиг подтверждает, что в Postgres-прогоне ни один orchestrator-тест не исключён.
- В репозиторий ничего не записано. Единственный созданный файл — этот отчёт, вне репозитория бота.
- Запуск pytest создал `__pycache__` (подавлен `PYTHONDONTWRITEBYTECODE=1`) и не создавал `.pytest_cache` (`-p no:cacheprovider`).
