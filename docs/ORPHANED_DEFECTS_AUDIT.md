# Аудит осиротевших дефектов — от кода к задачам

**Дата:** 08.09.2026
**Метод:** обратный аудит по §68 `OPEN_DECISIONS.md`. Не «задача → предмет», а
**«упоминание номера в комментарии → предмет фразы»**.
**База чтения:** `origin/dev` обоих репозиториев, не рабочее дерево.

| репозиторий | git-корень | `origin/dev` HEAD |
|---|---|---|
| бот | `C:/Users/user/PycharmProjects/Ayla/ai-bot-platform` | `c75048c5` (08.09.2026 06:34) |
| бэкенд (`nutrition/`) | `C:/Users/user/PycharmProjects/Ayla/djangoproject` | `90b6f68f` (08.09.2026 06:34) |

Внешняя папка `Ayla` — не репозиторий; `djangoproject-alpha` / `djangoproject-catalog` —
worktree того же бэкенда, читались через общий `origin/dev`.

---

## Что собрано

| величина | число |
|---|---|
| сырых совпадений `DRF-\d+` / `BOT-\d+` (`git grep` по `origin/dev`) | **7 053** (5 915 бот + 1 138 бэкенд) |
| из них в **комментариях и докстрингах** (без `.md`) | **5 871** |
| из них в **не-тестовом** коде | **3 685** |
| различных номеров в не-тестовых комментариях | **562** (557 `DRF-*` + `BOT-001`, `ADR-*`) |
| статусы получены из Linear прямым GraphQL | **557 / 557**, пропусков нет |
| **закрытых** номеров | **442** (436 `Done` + 5 `Canceled` + 1 `Duplicate`) — 79 % |
| открытых | 115 (90 Backlog, 14 Todo/Unstarted, 11 In Progress) |
| строк-кандидатов, разобранных поимённо | **176** (154 по семантике долга + 22 со ссылкой на Canceled/Duplicate) |
| **ОСИРОТЕВШИХ ДЕФЕКТОВ** | **7** |
| ложных тревог, снятых наличием живой строки | 2 (`DRF-1000`, `DRF-869`) |

Отбор кандидатов — три прохода по семантике фразы, а не по номеру:
долговая («TODO», «until», «workaround», «deferred», «parked», «placeholder»),
половинчатая («half», «partial», «out of scope», «незавершённая»),
будущая («lands», «ships in», «arrives», «never built», «has not»).
Остальные ~3 500 упоминаний — историческая формулировка вида
«Until DRF-X это была одна безусловная ветка» / «the other half of DRF-X»:
предмет фразы описывает **уже сделанное**, вердикт `СОГЛАСОВАНО` без разбора поимённо.

---

## Таблица разбора

Вердикты: `СОГЛАСОВАНО` — задача закрыта и предмет фразы сделан;
`ОСИРОТЕВШИЙ ДЕФЕКТ` — задача закрыта, предмет фразы **не** исполнен и своей строки нет;
`НЕЯСНО` — по фразе нельзя понять, о чём она;
`ЕСТЬ СВОЯ СТРОКА` — предмет не исполнен, но у него **есть живая задача** (не осиротел; отдельно
помечено, потому что для очереди это принципиально другой случай).

### Находки

| файл:строка | номер | статус | цитата | вердикт | доказательство |
|---|---|---|---|---|---|
| `apps/orchestrator/memory/food.py:169-176`<br>`apps/skills/food_correction/skill.py:30`<br>`apps/skills/food_correction/skill.py:613` | DRF-825 | **Done** | «``nutrition_client`` has no update endpoint yet — **it arrives with DRF-825** … When DRF-825 lands, the portion correction goes to Ayla»; «the weight correction **lands properly with DRF-825**»; «the diary is Ayla's and **the write lands with DRF-825**» | **ОСИРОТЕВШИЙ ДЕФЕКТ** | Предмет DRF-825 по её собственному заголовку — «[Sprint 9 / I1] `apps/integrations/ayla/nutrition_client.py` — port + schema fix», ручки обновления записи там не было **никогда**. Замер: в `apps/integrations/ayla/nutrition_client.py` есть `log_meal` (POST), `daily_summary`, `weekly_deficits`, `add_water`, `undo_water`, `upsert_profile` — и **ни одного** update/patch для записи дневника. На бэкенде `nutrition/urls.py:41,57` — только `FoodLogCreateView` и `internal-food-log`; PATCH/PUT-маршрута к `food-log` нет ни одного. Контраст: у воды **есть** `undo_water`, у профиля — `upsert_profile`; у дневника еды — только запись. Следствие живое: `REMEMBERED_FIELDS = (FIELD_NAME,)` (`memory/food.py:177`), и `DEFERRED_ACK` (`skill.py:113`) отвечает человеку дословно «Поняла: {value} г. **Вес пока не запоминаю**». Поиск в Linear по «коррекция порции вес дневник» / «обновление записи дневника Ayla» не даёт ни одной задачи на эту ручку |
| `apps/kb/tasks.py:27` (и реализация `:214-220`) | DRF-587 | **Done** | «Sprint 7 / L5 router (DRF-587) lands a per-tenant router; **once it does, K6 will read from it instead of hard-wiring OpenAI**» | **ОСИРОТЕВШИЙ ДЕФЕКТ** | Роутер приехал: `apps/llm/router.py` существует, его собственный докстринг говорит «Centralised so skill code **never** instantiates a provider directly» и «Call sites never have to special-case this; **they always call** `get_provider(op="embedding")`». K6 из него не читает: `_build_default_provider()` возвращает `OpenAIProvider()` напрямую. **Доказательство контрастом:** тринадцать вызовов `get_router().get_provider(...)` в `concierge.py:397`, `discovery.py:2787`, `intent_router.py:222`, `skills/booking/skill.py:572`, `skills/faq/skill.py:201`, `master_api/services/assistant.py:148`, `ai_drafts.py:661` — и **единственный в репозитории** вызов `get_provider(op="embedding")` стоит в `skills/faq/skill.py:320`, то есть на **читающей** стороне той же коллекции ChromaDB. Пишущая сторона (`apps/kb/tasks.py`) провайдера не спрашивает. Своей строки нет: DRF-564 (K6) закрыта, DRF-857 «Embedding model versioning в KB» — про версионирование модели, не про роутер |
| `.github/workflows/deploy.yml:19-26, 57` | DRF-891 | **Done** | «This is the **bootstrap version** of the workflow. **Real machinery activates when DRF-891 closes** … Until then, workflow exercises trigger graph + prints what WOULD deploy»; «`─── ACTIVATE AFTER DRF-891 SECRETS LAND ───`» | **ОСИРОТЕВШИЙ ДЕФЕКТ** | DRF-891 закрыта. Машинерия по-прежнему закомментирована целиком: строки 58-126 — SSH, **бэкап Postgres перед деплоем**, `pull + up -d --force-recreate`, смоук `/readyz/`, телеграм-алерт. **Доказательство контрастом:** брат-файл `deploy-dev.yml` (236 строк) несёт **10 живых шагов** и ноль закомментированных, включая тот же `Configure SSH` и смоуки; заявленный блокер («секреты могут быть не заданы») там решён живым шагом `Guard — no-op if deploy secrets are unset`. То есть решение уже написано в соседнем файле и не перенесено. Своей строки нет: DRF-1350 и DRF-1538 — обе про `deploy-dev.yml`, не про прод |
| `apps/catalog/admin.py:6` | DRF-576 | **Done** | «Admin is view-only for forensics; **controlled mutation (force resync) lands in C6 (DRF-576)** as an admin action» | **ОСИРОТЕВШИЙ ДЕФЕКТ** | DRF-576 по заголовку — «`apps/catalog/admin.py` — **read-only** admin for mirrors»; force-resync в её объём не входил. Он был описан в **DRF-667** («read-only + force-resync action», раздел «Force-resync workflow» с кнопкой «Force resync NOW»), а DRF-667 закрыта как **Duplicate** DRF-576 — то есть половина предмета уехала вместе с дублем. Замер: `git grep resync` по `apps/` даёт только комментарии; `actions` в `apps/catalog/admin.py:158` — архив/разархив мастеров, не синхронизация. **Доказательство контрастом:** у зеркала KB такая кнопка **есть** — `apps/kb/tasks.py:5,67,75` `reindex_tenant_kb` прямо описан как «admin "force resync" surface (K9 button)». Цена уже заплачена: DRF-1494 («Каталог бота не синхронизировался 12 дней») — про то самое зеркало, у которого кнопки нет |
| `apps/nutrition_coach/history.py:21-23` | DRF-1467 | **Done** | «**TODO(DRF-1467)**: when `food_history` grows a native weekly read, switch the loop below to it — one round-trip instead of N, and Ayla's own day boundaries instead of ours. Until then the week is what the days say» | **ОСИРОТЕВШИЙ ДЕФЕКТ** | Нативного недельного чтения нет: в `apps/orchestrator/food_history.py` объявлены ровно две публичные функции — `read_consent_open` и `read_today`; `week_picture` (`history.py:78-92`) вызывает `read_today` в цикле. Тут же, строки 25-28: «Day-boundary skew against Ayla's timezone is a **known approximation**». То есть TODO живой, а номер, на котором он висит, закрыт. Поиск в Linear («недельное чтение истории еды») не даёт задачи на нативную недельную ручку — DRF-1464 про проактивную еженедельную подсказку, это другое |
| `apps/bookings/followups.py:77-84` | DRF-846 | **Done** | «### Sentiment classification — Marked "Optional" in the DRF-846 spec; **deferred to a future ticket** (R3.1 or follow-up) … Phase 1 is "send the nudge"; **sentiment ships later**» | **ОСИРОТЕВШИЙ ДЕФЕКТ** | «Future ticket» не заведён. Замер: `grep -i sentiment` по не-тестовому коду даёт только `apps/eventbus/consumers/reviews.py` — вывод настроения **из оценки отзыва** (`rating → sentiment_score`), а не из текста ответа на пост-визитный опрос; в `apps/bookings/` кроме этого докстринга слова нет. DRF-846 закрыта; строки «R3.1» в Linear не существует |
| `notifications/templates.py:243-245` (бэкенд) | DRF-174 | **Done** | «`booking_suggestion` template is **intentionally absent until UserPersonalContext expands** (DRF-174 follow-up). The trigger heuristic without context is too noisy» | **ОСИРОТЕВШИЙ ДЕФЕКТ** | DRF-174 («UserPersonalContext — модель + API + PersonalizationEngine») закрыта. Шаблона в словаре `TEMPLATES` по-прежнему нет: `grep booking_suggestion` по всему `origin/dev` бэкенда даёт **одну** строку — сам этот комментарий. «Follow-up» не заведён |

### Ложные тревоги — предмет не исполнен, но строка есть

| файл:строка | номер | статус | цитата | вердикт | доказательство |
|---|---|---|---|---|---|
| `apps/integrations/ayla/booking_client.py:719-721` | DRF-997 | Done | «The proper long-term fix is to make the client async or run it in a thread pool; **that refactor is out of scope for DRF-997**» | **ЕСТЬ СВОЯ СТРОКА** | Предмет действительно не исполнен (`time.sleep` в синхронном клиенте, вызываемом из async-кода), но он покрыт **DRF-1000 [Backlog]** «Ayla booking client синхронный: ретрай 429 через `time.sleep` блокирует однопоточный consumer» — с ссылкой на `apps/workers/consumer.py:143`. Заводить нечего |
| `apps/observability/constants.py:15` | DRF-730 | **Canceled** | «Sprint 8 exit-gate floor for **production strict-scope flip** (F1 / DRF-730)» | **ЕСТЬ СВОЯ СТРОКА** | DRF-730 отменена, DRF-796 (та же формулировка, Sprint 9) тоже отменена, но **DRF-869 [Todo]** «[Sprint 10 / F-flip] Prod `STRICT_TENANT_SCOPE=strict` env flip» открыта. Комментарий ссылается на отменённый номер — это стоит поправить, но долг очередь видит. Отдельно: `STRICT_TENANT_SCOPE` в `.env.example:107` = `audit`, и несколько докстрингов («the pilot's audit mode», `master_notify.py:195`) описывают пилот как audit-режим — **это замер конфигурации репозитория, не пилота**; фактический режим на `api-dev.gobeauty.site` здесь не измерялся |

### Разобрано и согласовано (выборка — самые «подозрительные» формулировки)

| файл:строка | номер | статус | цитата | вердикт | доказательство |
|---|---|---|---|---|---|
| `apps/kb/chromadb_client.py:23` | DRF-595 | Done | «Bearer auth header is added **once `CHROMA_AUTH_TOKEN` lands in M4 (DRF-595)**» | СОГЛАСОВАНО | Приехало. `config/settings/base.py:1982` читает переменную, `chromadb_client.py:138-146` собирает `TokenAuthClientProvider`, а `config/settings/production.py:63-65` **падает при пустом токене** («CHROMA_AUTH_TOKEN is required in production»). Формулировка устарела, но не врёт |
| `apps/kb/chromadb_client.py:135` | DRF-595 | Done | «M4 (DRF-595) ships Bearer-auth wiring. **Until then HTTP client falls back to unauthenticated mode**» | СОГЛАСОВАНО | То же доказательство; ветка без токена осталась только для локальной разработки |
| `nutrition/services/nutrition_summary_service.py:341-346` | DRF-265 | Done | «**After DRF-265 lands, profile-side value takes over automatically** because we read it first» | СОГЛАСОВАНО | Приехало: `nutrition/services/nutrition_profile_service.py:219` пишет `daily_vitamin_d_iu=rda["vitamin_d_iu"]`, `profile_upsert_service.py:166` переносит норму в профиль, `nutrition/models.py:435-444` — колонки с комментарием «recomputed on every upsert … via `compute_rda`» |
| `legacy_maxbot/services/nutrition_client.py:8-10` | DRF-247, DRF-248 | Done | «POST `/internal/food-log/` — DRF-247 (**placeholder**)»; «GET `/internal/deficits/` — DRF-248 (**placeholder**)» | СОГЛАСОВАНО | Не placeholder: `log_meal` (:331), `daily_summary` (:376), `weekly_deficits` (:463) реализованы полностью, с разбором ответов. Устаревшая шапка модуля, поведение соответствует |
| `apps/admin_api/views_customers.py:89` | DRF-1231 | Done | «Our credential, not this person's rights (**DRF-1231 until it ships**)» | СОГЛАСОВАНО | DRF-1231 («Ayla отдаёт 401 на служебный токен: салонные ручки не принимают путь Б») закрыта, её приёмка — предмет фразы. Оговорка «until it ships» устарела |
| `apps/catalog/services/sync.py:8-12`<br>`apps/catalog/management/commands/sync_catalog.py:4-5` | DRF-576 | Done | «(Until DRF-1494 this line promised an admin "force resync" action instead. **There has never been one — C6/DRF-576 was never built**)» | СОГЛАСОВАНО (фраза честна) | Фраза не обещает починки, а фиксирует отсутствие — и она верна. Сам **долг** вынесен отдельной строкой выше (`apps/catalog/admin.py:6`), где он подан как обещание |
| `apps/orchestrator/health.py:80` | DRF-544 | Done | «**FAQ is the Sprint 6 stub** (DRF-544 / I1). If it's missing, app boot didn't fire `@register` decorators» | СОГЛАСОВАНО | Описатель устарел (FAQ давно KB-driven, DRF-589), но предмет фразы — проверка «`faq` зарегистрирован» — исполняется: `health.py:88-95` |
| `apps/voice/rewriter.py:1` | DRF-490 | Done | «Voice rewriter — **Phase-0 passthrough stub** (DRF-490 / Sprint 4 / C3)» | СОГЛАСОВАНО | Заголовок DRF-490 — «`apps/voice/rewriter.py`: Phase-0 **stub** `rewrite_to_voice()`». Заглушка **и была** предметом задачи |
| `apps/orchestrator/discovery.py:1114-1128` | DRF-1312 | Done | «### **Partial coverage** (DRF-1312)» | СОГЛАСОВАНО | Реализовано: `missing_services` выносятся первой строкой ответа, до карточек |
| `apps/orchestrator/llm/telegram_alert.py:3` | DRF-388 | **Canceled** | «per CR-3 (**DRF-388**) original spec, breaker state transitions should alert admins via Telegram» | СОГЛАСОВАНО | DRF-388 отменена, но предмет фразы приехал под DRF-455 — сам этот модуль и есть телеграм-сторона брейкера |
| `apps/skills/welcome/skill.py:1164` | DRF-1206 | **Canceled** | «DRF-1206 — **DELIBERATELY unconditioned by status. Do not "fix" this**» | СОГЛАСОВАНО | Комментарий объясняет, почему задача отменена, и предостерегает от «починки». Это ровно тот случай, ради которого правило §68 и написано, но с правильной стороны |
| `tools/lint/import_boundaries.py` (11 упоминаний) | DRF-1158 | **Duplicate** | «DRF-1158 — a builtin `hash()` value flowing into a stored sink» | СОГЛАСОВАНО | Правило живое и работает: `:753` — блок реализации, `:909` — baseline, `:1771` — «runs on production AND test files, deliberately» |
| `.env.example:110` | DRF-593 | **Canceled** | «Service-token (must match the token configured on mysite via **M2 / DRF-593**)» | СОГЛАСОВАНО | Ручка служебного токена существует — её приёмкой была DRF-1231 (401 на служебный токен), закрытая |
| `nutrition/services/cross_domain_engine.py:99-104` | DRF-288 | Done | «DRF-288 — internal-only gate. **Until the global rollout flag `CROSS_DOMAIN_ENABLED` flips True**, only users in the internal allowlist see recommendations» | СОГЛАСОВАНО (**ЗА ВОРОТАМИ**) | Гейт — и есть предмет DRF-288 («Track E (cross-domain) internal-only feature gate»). Ворота: `djangoProject/settings/base.py:845` `CROSS_DOMAIN_ENABLED` по умолчанию `0`; `_user_in_rollout_allowlist` (`:55-70`) читает флаг и падает в allowlist. Код реализован, но **невидим**, пока флаг не поднят — это состояние задумано, а не забыто |
| `appointments/migrations/0004_appointment_tenant_fk.py:3`<br>`nutrition/migrations/0005_foodscan_tenant_fk.py:3`<br>`users/models.py:65` | DRF-242 | Done | «Pure additive — null=True, **no backfill yet** (DRF-242.4)» | СОГЛАСОВАНО | Backfill приехал: `tenants/management/commands/backfill_tenants.py` + миграция `tenants/migrations/0003_seed_default_tenants.py`. Фразы — исторический слепок момента миграции |
| `apps/bookings/followups.py:88-91` | DRF-1301 | Done | «**This section used to say there was no consent gate and none was possible. Both halves were wrong**, and the paragraph outlived the code by two months» | СОГЛАСОВАНО | Образцовое исправление того же класса, уже проведённое вручную. Гейт живой: `enabled()` (:176), `vet_outbound()` (:185), страж `tools/lint/consent_column_guard.py` |
| ещё ~90 строк вида «Until DRF-X …», «the other half of DRF-X», «before DRF-X», «no longer … (DRF-X)» | разные | Done | — | СОГЛАСОВАНО (гуртом) | Все они описывают состояние **до** починки либо вторую половину уже приехавшей. Проверялись по формуле: фраза в прошедшем времени + ссылка на закрытый номер = историческая. Ни одна не обещает будущего |

### НЕЯСНО

| файл:строка | номер | статус | цитата | вердикт | доказательство |
|---|---|---|---|---|---|
| `ai/personal_context_hint.py:23` (бэкенд) | DRF-248 | Done | «Composability with **future DRF-248 hints**: callers concatenate strings» | НЕЯСНО | DRF-248 закрыта, поэтому «future DRF-248 hints» — не адрес. Из фразы нельзя понять, обещаны ли ещё какие-то подсказки сверх приехавшего дефицитного моста, или это просто пережиток формулировки времён разработки. Под первые два вердикта не подгонялось |
| `.github/workflows/smoke-on-dev.yml:86` (бэкенд) | DRF-160 | **Canceled** | «`- analytics/` — DRF-160 added to INSTALLED_APPS;» | НЕЯСНО | Строка внутри закомментированного списка; DRF-160 («EPIC-B · LLM Benchmark — качество русского языка») отменена. Что именно должно было быть в смоуке и должно ли — из фразы не следует |

---

## Осиротевшие дефекты — по цене для человека

**1. Человек исправляет вес порции, а дневник остаётся с чужим числом.**
`apps/skills/food_correction/skill.py:30,613`, `apps/orchestrator/memory/food.py:169-176` → **DRF-825 (Done)**.
Бот отвечает дословно «Поняла: 500 г. **Вес пока не запоминаю** — в следующий раз уточню снова»,
и калории с БЖУ за этот приём остаются посчитанными по неверному весу — навсегда, потому что
ручки обновления записи в дневнике нет ни в клиенте бота, ни на бэкенде, а единственная ссылка на
её появление стоит на закрытом номере.

**2. База знаний индексируется мимо роутера — ответ FAQ может молча перестать находиться.**
`apps/kb/tasks.py:27,214-220` → **DRF-587 (Done)**.
Пишущая сторона ChromaDB жёстко берёт OpenAI, читающая (`skills/faq/skill.py:320`) спрашивает роутер.
Как только роутер отдаст читателю другого вендора — по тенантному override, по `embedding_fallback`
или по исчерпанию квоты, — вопрос и документы окажутся в разных векторных пространствах, и человек
получит «не знаю» на вопрос, ответ на который в базе лежит. Плюс: у индексации нет квотного
отката, который есть у всех остальных вызовов.

**3. Прод-выкладка ничего не выкладывает и не делает бэкап базы перед деплоем.**
`.github/workflows/deploy.yml:19-26,57` → **DRF-891 (Done)**.
Задание `deploy-prod` печатает уведомление и завершается зелёным. Первый же push в `main` даст
владельцу зелёную галочку и ноль изменений на сервере; закомментированным лежит в том числе
`Postgres backup before deploy` с проверкой размера дампа. Соседний `deploy-dev.yml` — живой,
все десять шагов работают, и заявленный блокер там уже решён шагом-стражем.

**4. Каталог протухает — и нажать «пересинхронизировать» негде.**
`apps/catalog/admin.py:6` → **DRF-576 (Done)**, кнопка описана в **DRF-667 (Duplicate)**.
Оператор, увидевший в админке отставание синхронизации, не может её запустить: остаётся
`manage.py sync_catalog` с shell-доступом на сервере. Это уже стоило пилоту двенадцати дней
ответов клиентам по устаревшему каталогу (DRF-1494). У зеркала KB такая кнопка есть.

**5. Недельная картина еды собирается семью запросами по нашим суткам, а не по суткам Ayla.**
`apps/nutrition_coach/history.py:21-23` → **DRF-1467 (Done)**.
Приём пищи у границы суток может попасть не в тот день недели — человек увидит еду,
которой в тот день не ел, или не увидит ту, что ел. Плюс семь round-trip'ов на один вопрос:
любой из них может вернуть UNAVAILABLE и оставить в неделе дырку.

**6. Ответ клиента на пост-визитный опрос никто не размечает.**
`apps/bookings/followups.py:77-84` → **DRF-846 (Done)**.
Владелица получает рассылку-напоминание, но не получает ответа на вопрос «а как приняли».
Обещанный «future ticket (R3.1)» не заведён — потери тихие, отложенные.

**7. Уведомления не умеют предложить запись.**
`notifications/templates.py:243-245` (бэкенд) → **DRF-174 (Done)**.
Шаблона `booking_suggestion` нет, и «follow-up», под который его отложили, не существует.
Человек не получает предложения записаться там, где остальные шаблоны его уже нашли бы.

---

## Побочное наблюдение

Правило §68 («после исправления комментарий обязан сослаться на новый номер») нарушено
и в обратную сторону: `apps/observability/constants.py:15` ссылается на **дважды отменённый**
номер (DRF-730 → DRF-796), хотя живая строка DRF-869 существует. Отменённые и слитые-как-дубль
номера — отдельный подкласс: их в комментариях **22 упоминания на 6 номеров**, и именно среди них
нашёлся самый неочевидный сирота (DRF-667 → DRF-576, кнопка пересинхронизации уехала вместе
с дублем). При закрытии как Duplicate стоит проверять, что объём дубля **целиком** входит
в объём приёмника.

Реестр `DRF-1420` («Реестр ложных комментариев: имя или описание обещает больше, чем делает
код», Backlog) — соседний по духу, но другой класс: там комментарий врёт о **настоящем**,
здесь — о **будущем**.
