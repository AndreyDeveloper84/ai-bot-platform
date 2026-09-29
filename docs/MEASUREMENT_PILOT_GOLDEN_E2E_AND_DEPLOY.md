# Замер: Golden E2E suite (п.41) и Deployment / Rollback (п.42)

**Режим:** MEASURE-FIRST, read-only. Ничего не чинилось, PR не открывался,
Linear не трогался, в живой контур не ходил.
**Дата замера:** 09.09.2026.
**Свод:** `docs/BRIEF_PILOT_READINESS_COMMON.md`.

---

## 1. Базы замера

| repo | ветка | фактический SHA (сверен `git ls-remote`) | совпал с брифом | чем снято |
|---|---|---|---|---|
| `ai-bot-platform` | `origin/dev` | `b1a119bdfb26765bc75ce35dfbf1dd82227183d0` | да | `git show` / `git grep` по рефу, `gh run view` |
| `djangoproject-catalog` | `origin/dev` | `95c917e684652476feef3ae9d790fb2c8d277378` | да | `git show` / `git grep` по рефу, `gh run view` |
| `ayla-ai-core` | `origin/main` | `d72a5de451f985d118d9449d2b17ce51bf0a6e25` | да | сверка SHA (в предмет не входил) |
| `ayla-knowledge` | `origin/main` | `207eeb638580e529aaff66e0d1412aa6b559c0a3` | да | сверка SHA (в предмет не входил) |

Рабочие чекауты разошлись с origin и в замер **не** попали:
`ai-bot-platform` стоит на `feat/recommendation-boundary-client`,
`ayla-ai-core` — на `fix/memory-origin-vocabulary`,
`ayla-knowledge` — на `drf-1148/canon-under-version-control`.
Всё читалось через `git show <ref>:<путь>`.

Живые числа CI взяты из прогонов **09.09.2026**:

| что | workflow | run id | время (UTC) | коммит |
|---|---|---|---|---|
| `ci` (бот) | 274201556 | 34324588828 | 07:35–08:13 | `db601b0b` |
| `replay` (бот) | 274201559 | 34324588856 | 07:35–07:37 | `db601b0b` |
| `deploy-dev` (бот) | 274201557 | 34327958994 | 08:13 | по `workflow_run` |
| `Smoke tests on dev VPS` (каталог, ЖИВОЙ контур) | 271127394 | 34331023938 | 08:47 | `dev` |

---

## 2. Executive verdict — по строке на ответ

**п.41 — GOLDEN E2E SUITE**

1. Обязательного golden-набора из 24 journey **не существует**: `apps/replay/fixtures/golden/` — это 81 фикстура о другом (FAQ, вода, еда, анкета, приватность, handoff, callback'и), и ни одного каталога `recommendation/`, `goal/` или `safety/` в ней нет.
2. Премиса владельца «golden ходит через DEPRECATED `pipeline.py`» — **верна для CLI и для четырёх тест-файлов, но НЕ верна для того, что гоняет CI сегодня**: с DRF-1373 golden-фикстуры исполняются через `apps.channels.max.handler.handle_max_event`, то есть через настоящий клиентский обработчик (`apps/replay/tests/test_golden_gate.py`).
3. Что golden-фикстуры доказывают сегодня, числом из сегодняшнего прогона: **81 исполнено, 48 проверено полностью, 33 не проверено ничем** — и все 33 непроверенных это ровно те, которым нужна модель или Ayla API.
4. Следствие из (3): **на стыке бот↔Ayla golden-набор не доказывает ничего по построению** — `NetworkTripwire` отказывает в TCP-соединении, и любая фикстура, дошедшая до Ayla, автоматически уходит в непроверенные.
5. CROSS-BOUNDARY-теста, проверяющего стык реальным вызовом, **в CI нет ни одного**. Единственный настоящий межпроцессный тест (`tests/e2e/test_ayla_integration.py`) самоотключается без `AYLA_BASE_URL`; переменной нет ни в одном workflow обоих репозиториев, «ночного прогона», на который он ссылается, не существует.
6. Заявление «общие контрактные фикстуры читают оба репозитория» **опровергнуто**: в `djangoproject-catalog@origin/dev` нет ни одной ссылки на `tests/fixtures/contracts/**`. Обе стороны построил один автор в одном репозитории.
7. Единственный автоматический тест против **живого** контура — `Smoke tests on dev VPS` каталога: 84 passed сегодня в 08:47 UTC, но его предмет — `nutrition/tests/smoke`, то есть дневник/вода/скан. Рекомендации, брони и стык с ботом там не проверяются.
8. Клиент, который обязан валидировать контракт резолвера на границе (`recommendation_resolver_client.resolve_recommendation`), **не вызывается из production ни разу** — единственный импортёр во всём репозитории это его собственный тест. Живой путь зовёт транзитный `fetch_recommendations`, который форму не проверяет по своему же докстрингу.

**п.42 — DEPLOYMENT / ROLLBACK**

9. Ветка выкладки пилота — **`dev` в обоих репозиториях**; `main` в боте не выкладывался с 19.05.2026, и все семь последних прогонов `deploy` длились 7–12 секунд (эпоха пустого скелета). «Стейджинг» и «пилот» — не два контура, а один.
10. Триггеры разные и это важно: у бота `deploy-dev` подписан на `workflow_run: workflows:["ci"]`, у каталога `deploy` — это job внутри `CI/CD` с `if: github.ref == 'refs/heads/dev' && github.event_name == 'push'`.
11. **Разрыв цепочки 09.09 подтверждён на живых данных**: `ci` (274201556) несёт `paths-ignore: docs/**, **/*.md`, головной коммит `origin/dev` бота **`b1a119bd` — документный**, прогона `ci` на нём нет, `deploy-dev` на нём не запускался. У каталога `paths-ignore` нет, там документный коммит выкладку запускает — асимметрия между репозиториями.
12. **Выкладка не привязана к проверенному SHA**: `deploy-dev` делает `git pull --ff-only origin dev` и `actions/checkout@v4 with: ref: dev`, то есть берёт подвижную голову ветки, а не тот коммит, который прошёл `ci`. При 38-минутном `ci` это окно, в котором на пилот уезжает непроверенный код.
13. **Пути отката нет.** Документированный Path B (`docs/runbooks/rollback-procedure.md:170`) требует `IMAGE_TAG=<old-SHA>` в `.env`; строка `IMAGE_TAG` во всём репозитории встречается **только в самом рунбуке** — ни один compose-файл её не читает, образов с тегами не существует, выкладка собирает образ на месте из рабочего дерева.
14. Необратимых миграций в смысле «`RunPython` без reverse» — **ноль в обоих репозиториях** (проверены все 30 вызовов: 22 в боте, 8 в каталоге). Но **у 9 из 30 reverse — `noop`**: формально обратимы, фактически данные назад не возвращаются. Процедуры обратной миграции не описано нигде.
15. **Бэкапа базы на пути выкладки пилота нет.** `pg_dump` с проверкой размера стоит только в `deploy.yml` (ветка `main`, не запускалась с мая). В `deploy-dev.yml` его нет; в каталоге слова `pg_dump` нет во всём репозитории, а миграции применяются автоматически в `entrypoint.sh` до всякой проверки.
16. Health-check есть с обеих сторон и он содержательный: у каталога `/api/v1/health/ready/` отдаёт 200 только при `SELECT 1` + round-trip кэша + пустом плане миграций; у бота `/readyz/` опрашивает postgres/redis/chromadb/minio и здоровье пайплайна. Оба включены в смоук выкладки.
17. Алерты есть, но однобокие: у каталога — телеграм-пинг **только на `failure()`** и с раннера (RKN режет `api.telegram.org` с VPS); у бота — `smoke_alert` через контейнер и `continue-on-error: true`, то есть упавший алерт не виден.
18. Конкурентность релизов разведена **внутри** каждого репозитория (`concurrency: deploy-dev` и `deploy-dev-vps`, оба `cancel-in-progress: false`), но **не между ними** — бот и каталог живут на одной машине и могут собираться одновременно.
19. Быстрое выключение AI-рекомендации по пути выкладки: **деплой не нужен**, все флаги читаются из окружения (`os.environ` в `config/settings/base.py` бота, `djangoProject/settings/base.py` каталога). Нужен SSH на машину, правка `.env.staging` / `.env` и `docker compose up -d --force-recreate` прикладного яруса. Кнопки в админке и `workflow_dispatch`-пути для этого нет; SSH есть только у окна-заказчика.
20. **Самое опасное:** пилотный стек нельзя восстановить из репозитория — `deploy-dev.yml` во всех пяти вызовах compose подключает `docker-compose.staging.local.yml`, которого в `ai-bot-platform@origin/dev` не существует.

---

## 3. Сводка по классам числом

**CURRENT STATE — 29 находок §5–§6** (журнеи считаются отдельно, в §4):

| класс | число | находки |
|---|---|---|
| `EXISTS` | **8** | 5.7-фильтр, 6.1, 6.4, 6.5-MiniApp, 6.7-обратимость, 6.9, 6.13, 6.14 |
| `PARTIAL` | **4** | 5.2, 5.8, 6.10, 6.11 |
| `MISSING` | **6** | 5.3, 5.4, 6.6, 6.7-процедура, 6.8, 6.12 |
| `CONTRADICTS_CANON` | **2** | 5.5, 6.2 |
| `STALE_SPEC` | **2** | 5.1-докстринги, 6.5-PathB |
| `DEAD_CODE` | **3** | 5.1-`pipeline.turn`, 5.6, 5.7-реестр |
| `UNKNOWN_NOT_MEASURED` | **4** | 6.5-PathA, 6.6-содержимое, 6.8-cron, 6.9-chromadb |

8+4+6+2+2+3+4 = 29.

**PILOT IMPACT** (моя оценка, решает владелец):

| | число | находки |
|---|---|---|
| `STOP` | **7** | 5.4 (нет CROSS-BOUNDARY), 5.5 (фикстура «общая» только на бумаге), 6.3 (выкладывается непроверенный SHA), 6.5 (отката нет), 6.6 (стек не восстановим из репозитория), 6.7 (нет процедуры обратной миграции), 6.8 (нет бэкапа пилота) |
| `DEGRADED` | **9** | 5.2, 5.3, 5.6, 5.8, 6.2, 6.10, 6.11, 6.12, 6.14 |
| `POST_PILOT` | **3** | 5.1, 5.7-реестр, 6.13 |

Оставшиеся 10 находок — подтверждения работающего, риска не несут.

**TEST STATUS** по 24 journey (§4): `E2E_GREEN` — 4, `CONTRACT_ONLY` — 13,
`UNIT_ONLY` — 4, `MISSING` — 3, `CROSS_BOUNDARY` — **0**.

---

## 4. GOLD-01…GOLD-24 — обязательная таблица

Столбец «SHA» — коммит, на котором снят зелёный цвет: `b1a119bd` = бот
`origin/dev`, `95c917e6` = каталог `origin/dev`. «Зелёный» означает
«прогон 09.09 зелёный», а не «journey работает».

**Как читать столбцы.**
`EXISTS?` — есть ли автотест на предмет journey (не «работает ли journey»).
`уровень` — по шкале свода: `UNIT` (модуль, всё замокано) / `CONTRACT`
(форма запроса-ответа либо вызов класса-скилла с рукотворной фикстурой) /
`CROSS-BOUNDARY` (настоящий вызов между ботом и каталогом) / `E2E` (через
входную точку обработчика сообщения) / `LIVE` (живой контур).
`зелёный` — прогон, в котором тест зеленел 09.09.2026.
`SHA` — коммит прогона. `db601b0b` — последний **кодовый** коммит бота;
голова `origin/dev` бота `b1a119bd` документная и кода не меняет.
`95c917e6` — голова `origin/dev` каталога, прогон `CI/CD` 34308664506.

| # | journey | EXISTS? | уровень | зелёный | SHA | блокер |
|---|---|---|---|---|---|---|
| GOLD-01 | «Хочу массаж» | `PARTIAL` | CONTRACT (вызов `BookingSkill().handle()`, tool-call от LLM подан готовым) | да, `ci` 34324588828 | `db601b0b` | нет, но не тот путь: ранжирует свой `_relevance_score` поверх YClients, а не резолвер каталога |
| GOLD-02 | «Хочу расслабиться» | `MISSING` | — | — | — | **да** |
| GOLD-03 | «Не знаю, выбери сама» | `PARTIAL` | CONTRACT (DRF `APIClient` на `/goals/select/`) | да, `CI/CD` 34308664506 | `95c917e6` | **да** — покрыт канал Mini App-анкеты, чат не покрыт |
| GOLD-04 | «Хочу другого мастера» | `MISSING` | — (`test_rotation.py` — про справедливость между людьми, порядок внутри разговора наоборот стабилен) | — | — | **да** |
| GOLD-05 | provider unavailable | `EXISTS` | CONTRACT (каталог, DRF view) + UNIT (бот, YClients) | да, обе стороны | `95c917e6` / `db601b0b` | нет |
| GOLD-06 | stale slot | `EXISTS` | CONTRACT (`BookingSkill().handle()` на `cb:book:pick_slot`) | да, `ci` 34324588828 | `db601b0b` | нет |
| GOLD-07 | `requires_health_check` без `SafetyResult` | `EXISTS` | UNIT (`resolve()` со `StaticSource`) | да, `CI/CD` 34308664506 | `95c917e6` | нет (у бота — свой, несвязанный гейт) |
| GOLD-08 | Safety CLARIFY | `EXISTS` | UNIT | да, `ci` 34324588828 | `db601b0b` | нет; в каталоге `SafetyState.CLARIFY` не покрыт ни одним тестом |
| GOLD-09 | Safety STOP / BLOCK / HANDOFF | `EXISTS` | **E2E** (`handle_global_max_event`, live-path gate) | да, `replay` 34324588856 | `db601b0b` | нет; покрыты 3 кризисные фикстуры из ~20 adversarial |
| GOLD-10 | нет VERIFIED-кандидата | `EXISTS` | UNIT + CONTRACT (каталог) | да, `CI/CD` 34308664506 | `95c917e6` | нет на стороне источника; что покажет клиент — не покрыто |
| GOLD-11 | создание Goal | `EXISTS` | CONTRACT (DRF `APIClient`) | да, `CI/CD` 34308664506 | `95c917e6` | нет |
| GOLD-12 | continuity Goal | `PARTIAL` | CONTRACT | да, `CI/CD` 34308664506 | `95c917e6` | **да** — `fetch_decision_context` не зовётся ни из одного `apps/skills/**` |
| GOLD-13 | отвеченный вопрос не повторяется | `PARTIAL` | CONTRACT (реальный диспетчер + БД, домен food) | да, `ci` 34324588828 | `db601b0b` | **да** для анкеты и уточнений брони |
| GOLD-14 | free text против тапа | `EXISTS` | **E2E** (`GlobalMaxHandler()`) | да, `ci` 34324588828 | `db601b0b` | нет |
| GOLD-15 | stale callback | `EXISTS` | CONTRACT (skill + строка БД) + E2E через golden-фикстуру | да, `ci` + `replay` | `db601b0b` | нет |
| GOLD-16 | рекомендация → Mini App | `MISSING` | — (механизма переноса контекста рекомендации в deep link нет) | — | — | **да** |
| GOLD-17 | бронь завершена | `EXISTS` | CONTRACT (рукотворный envelope `booking.completed`) | да, `ci` 34324588828 | `db601b0b` | нет |
| GOLD-18 | бронь отменена | `EXISTS` | CONTRACT (`FakeYClients`) | да, `ci` 34324588828 | `db601b0b` | нет |
| GOLD-19 | вывод ассистента ≠ USER evidence | `PARTIAL` | UNIT (словарь и рендер) | да, `ci` 34324588828 | `db601b0b` | **да** — теста-атаки нет, словарь значения «сказал ассистент» не содержит |
| GOLD-20 | выдуманный планировочный интервал | `EXISTS` | UNIT (`evaluate_outbound`, подключён к живому обработчику в 3 местах) | да, `ci` 34324588828 | `db601b0b` | нет для формы; реестр `apps/planning_rules/**` при этом мёртв (§5.7) |
| GOLD-21 | прямая бронь без рекомендации | `EXISTS` | **E2E** (`handle_global_max_event`) | да, `ci` 34324588828 | `db601b0b` | нет |
| GOLD-22 | истёкший `PendingBookingIntent` | `PARTIAL` | UNIT (`assert PENDING_INTENT_TTL_SECONDS == 600`) | да, `ci` 34324588828 | `db601b0b` | **да** — истечение не воспроизведено ни на клиенте, ни на сервере |
| GOLD-23 | цена/доступность изменились до брони | `PARTIAL` | CONTRACT (доступность) / нет теста (цена) | да, `ci` + `CI/CD` | `db601b0b` / `95c917e6` | **да** для цены |
| GOLD-24 | WHY без evidence подавляется | `EXISTS` | CONTRACT (`resolve()` + запрет полей в сериализаторе) | да, `CI/CD` 34308664506 | `95c917e6` | нет |

**Счёт по 24 journey**

| класс | число | какие |
|---|---|---|
| `EXISTS` | **14** | GOLD-05, 06, 07, 08, 09, 10, 11, 14, 15, 17, 18, 20, 21, 24 |
| `PARTIAL` | **7** | GOLD-01, 03, 12, 13, 19, 22, 23 |
| `MISSING` | **3** | GOLD-02, 04, 16 |

14 + 7 + 3 = 24.

**Счёт по уровням** (у journey берётся сильнейший найденный уровень)

| уровень | число | какие |
|---|---|---|
| `LIVE` | **0** | — |
| `CROSS-BOUNDARY` | **0** | — |
| `E2E` (через входную точку обработчика) | **4** | GOLD-09, 14, 15, 21 |
| `CONTRACT` | **13** | GOLD-01, 03, 05, 06, 07, 10, 11, 12, 13, 17, 18, 23, 24 |
| `UNIT` | **4** | GOLD-08, 19, 20, 22 |
| нет уровня | **3** | GOLD-02, 04, 16 |

4 + 13 + 4 + 3 = 24. **Ни одного `CROSS-BOUNDARY`, ни одного `LIVE`.**

**Опровергнутое буквальное прочтение.** GOLD-01…GOLD-04 в формулировке
«клиент пишет боту → отвечает движок рекомендаций каталога» **не
существуют как путь**, а не только как тест. Разговорный путь бота отвечает
на «хочу массаж» через `apps/skills/booking/tools.py` со своим
`_relevance_score` поверх YClients, и статический гард
`ai-bot-platform:tests/contracts/test_recommendation_boundary_guard.py:44-52`
прямо это фиксирует:

```
_ALLOWED = {
    "apps/marketplace/discovery.py": "DRF-1573 (T12): бот зовёт resolve() вместо своего порядка",
    "apps/orchestrator/handoff.py": "DRF-1573 (T12): та же миграция, вторая поверхность бота",
    "apps/skills/booking/tools.py": "не кандидаты рекомендации Ayla: релевантность поверх YClients (ничьи — DRF-1529)",
}
```

Первые две записи — обещание на будущее («T12»), третья — прямое заявление,
что живой путь брони к рекомендациям Ayla **не относится**. Движок каталога
вызывается только из Mini App-ручек и только через непроверяющий транзит
(§5.6).

---

## 5. Находки — п.41 (Golden E2E)

### 5.1. Премиса про DEPRECATED pipeline: проверена, разделена надвое

`ai-bot-platform:apps/orchestrator/pipeline.py:1`

```
"""Orchestrator pipeline — turn() (DRF-535 / Sprint 6 / O1). DEPRECATED.
### DEPRECATED — this is not the path that answers anyone (DRF-1216)
... `turn()` has not been called from ingress on any day of this
repository's history, and it has no caller outside tests today except
`apps/replay/__main__.py` (`_default_pipeline`, imported lazily)
```

Подтверждено грепом: `_build_default_pipeline_fn` в
`apps/replay/__main__.py:58` — «Default pipeline = thin wrapper around
``apps.orchestrator.pipeline.turn``».

**Что через мёртвый путь действительно ходит:** CLI
`python -m apps.replay run --fixture-set …/golden`, а также
`tests/e2e/test_orchestrator_e2e.py`, `tests/integration/test_pipeline_turn.py`,
`apps/orchestrator/tests/test_faq_latency.py`, `test_otel.py`, `test_otel_spans.py`.
Файл `tests/e2e/test_orchestrator_e2e.py:3` называет себя
«orchestrator end-to-end exit-gate» и «every component sprints 1-6 built must
be reachable from one production call» — при том, что `turn()` не production-call.
Класс: `STALE_SPEC` + `DEAD_CODE`.

**Что через мёртвый путь НЕ ходит:** сегодняшний CI. Шаги задания
`replay fixtures (golden + adversarial + voice)` (run 34324588856) —
только pytest, CLI не вызывается ни разу. `apps/replay/tests/test_golden_gate.py`
гоняет golden через `apps.channels.max.handler.handle_max_event`, а
`apps/channels/handlers.py:56` вызывает `max_handler.handle_max_event`
в production. Класс: `EXISTS`.

Вывод: **старый golden replay действительно доказательством не является, но
CI на него больше и не опирается.** Опасность сместилась: она теперь не в
мёртвом пути, а в том, ЧТО именно golden-набор проверяет (§5.2) и о чём он
молчит (§5.3).

### 5.2. Что golden-фикстуры доказывают сегодня — числом

Прогон `replay` 34324588856, 09.09.2026 07:37 UTC, коммит `db601b0b`:

```
[golden gate] 81 fixtures on disk, 81 executed
[golden gate] 48/81 asserted in full
[golden gate] 33/81 need a model or the Ayla API:
```

Итог задания: `136 passed, 33 skipped in 28.60s`.
Соседний шаг live-path gate: `64 passed, 149 skipped`.

Механика честная и заслуживает быть названной: `test_coverage_is_reported`
(`apps/replay/tests/test_golden_gate.py:480`) печатает раскладку и падает,
если числа перестанут сходиться, — «a skip is not a pass». Но
**коэффициент покрытия не гейтится**:

```
# Not a ratio target — that would freeze today's number.
assert asserted, "no golden fixture was asserted in full — the gate checks nothing"
```

То есть 1 проверенная фикстура из 81 прошла бы этот гейт так же зелено, как 48.
Класс: `PARTIAL`.

**Ключевое.** Из 33 непроверенных 8 помечены `net ayla-api.invalid:80` —
это ровно те фикстуры, что идут в Ayla. `NetworkTripwire`
(`apps/replay/golden_path.py`) подменяет socket-слой и отказывает в любом
исходящем TCP. Значит **любая фикстура, дотянувшаяся до стыка бот↔Ayla,
по построению уходит в «не проверено»**. Golden-набор не может доказать
ничего о стыке — не потому, что плохо написан, а потому, что так устроен.

### 5.3. О чём golden-набор молчит: состав каталогов

`apps/replay/fixtures/golden/` (81 файл) делится на:
`booking/` (4), `cross_domain/` (5), `faq/` (16), `food_clarify/` (5),
`food_correction/` (6), `food_scanner/` (5), `handoff/` (10),
`health_screening/` (5), `nutrition_anketa/` (5), `orchestrator/` (5),
`privacy/` (10), `water/` (5).

Каталогов `recommendation/`, `goal/`, `safety/` в наборе **нет**. Двадцать
четыре journey владельца в набор не отображаются: пересечение — это форма
устаревшего callback (`food_correction/cb_stale_card.yaml`,
`cross_domain/cb_missing_shown_id.yaml`) и красный флаг здоровья в
текстовом скрининге (`health_screening/neck_radiation_red_flag.yaml`),
но в других доменах. Класс: `MISSING`.

### 5.4. CROSS-BOUNDARY: ни одного в CI

Проверены все кандидаты.

| кандидат | что это на самом деле | уровень |
|---|---|---|
| `ai-bot-platform:tests/e2e/test_ayla_integration.py` | настоящий HTTP в Ayla staging | LIVE, **выключен** |
| `ai-bot-platform:apps/integrations/ayla/tests/test_contract_route_table.py` | in-memory transport, сверка с рукотворной таблицей | CONTRACT |
| `ai-bot-platform:tests/contracts/test_recommendation_boundary_guard.py` | грep по исходникам на `order_by/sorted` | UNIT (статический) |
| `djangoproject-catalog:recommendation/tests/test_wire_contract.py` | «без HTTP и без базы» — сериализатор проверяет сам себя | UNIT |
| `djangoproject-catalog:recommendation/tests/test_resolve_endpoint.py` | Django test client внутри каталога | CONTRACT (одна сторона) |
| `djangoproject-catalog:.github/workflows/smoke-on-dev.yml` | pytest **на живой машине** | LIVE, работает, но предмет — `nutrition/` |

Первый выключен: `git grep -rn "AYLA_BASE_URL" origin/dev -- .github` в боте
даёт **только комментарий** в `ci.yml:478`. Никакого «ночного прогона» нет —
в репозитории всего два расписания (`miniapp-drift`, `mirror-base-image`),
и ни одно не про Ayla.

Второй сам объявляет свой предел —
`apps/integrations/ayla/tests/test_contract_route_table.py:30-36`:

```
**Scope + what green does NOT prove.** ... it proves client↔table
*consistency*, NOT table↔Ayla *correctness*: a row that is wrong in a way
the client is also wrong about passes here. The Ayla-side authority is the
nightly staging round-trip (``tests/e2e``, #1079).
```

Названный «Ayla-side authority» не существует как автоматика.

Единственный настоящий LIVE-гейт — смоук каталога, run 34331023938,
09.09.2026 08:47 UTC: `84 passed, 11 warnings in 26.42s`, `pytest exit=0`,
предмет `nutrition/tests/smoke`. К рекомендациям, броням и стыку с ботом
он не относится.

### 5.5. Подтверждённое противоречие: «общая фикстура» общая только на бумаге

`ai-bot-platform:tests/fixtures/contracts/README.md:3-6`

```
Canonical, byte-stable payloads for the events and requests that cross
the Ayla ⇄ ai-bot-platform boundary. **This directory is the single
source of truth.** Both repos load these exact bytes so their tests
can't quietly disagree about what a `payment.captured` looks like
```

Другая сторона:

```
$ git -C djangoproject-catalog grep -rn "recommendations.request.json\|MANIFEST.sha256\|contracts/booking.created" origin/dev
(пусто)
```

Каталог этих байтов не читает. Обе половины «общего контракта» лежат в одном
репозитории и написаны одним автором — та самая конфигурация, в которой был
пропущен дефект конверта `{"data": …}` и расхождение ключей кандидатов.
Класс: `CONTRADICTS_CANON`, PILOT IMPACT: `STOP`.

### 5.6. Валидатор контракта резолвера не вызывается

`ai-bot-platform:apps/integrations/ayla/recommendation_resolver_client.py:86`
объявляет `resolve_recommendation(...) -> ResolveOutcome` с тремя исходами
(`OK` / `UNAVAILABLE` / `CONTRACT_VIOLATION`) и полной проверкой формы.

```
$ git -C ai-bot-platform grep -n "recommendation_resolver_client import" origin/dev
apps/integrations/ayla/tests/test_recommendation_resolver_client.py:19
apps/integrations/ayla/tests/test_recommendation_resolver_client.py:20
```

Единственный импортёр — собственный тест. Класс: `DEAD_CODE`.

Живой путь — другой: `apps/miniapp_api/views.py:2601,2622` зовёт
`fetch_recommendations` из транзитного `recommendations_client`, и тот же
докстринг признаёт, что форму не проверяет:

```
Пропуск формы как есть сохранён намеренно (см.
`recommendations_client.fetch_recommendations`); валидацию несёт
`apps.integrations.ayla.recommendation_resolver_client`, который
разводит три исхода.
```

Валидацию «несёт» модуль, который никто не зовёт. На стороне каталога
резолвер при этом **на живом пути стоит**:
`djangoproject-catalog:users/catalog_recommendations_api.py:229` вызывает
`resolve(...)` из `recommendation.api`. То есть источник решение принимает,
а потребитель его форму не проверяет. PILOT IMPACT: `DEGRADED`.

### 5.7. Реестр планировочных правил принимает, но не запрещает (GOLD-20)

`apps/planning_rules/` содержит `registry.py`, `check.py`,
`planning-rules-registry.yaml`. Проверено:

```
$ git grep -n "planning_rules" origin/dev -- "apps/**" | grep -v "apps/planning_rules/"   → пусто
$ git grep -nE "load_registry|PlanningRule" origin/dev -- "apps/**" "config/**" | grep -v "apps/planning_rules/"   → пусто
$ git grep -n "planning_rules" origin/dev -- "config/settings/base.py"   → пусто
```

Модуль не в `INSTALLED_APPS` и не вызывается ниоткуда. Коммит `fd41ff10`
называет себя «точка приёма реестра планировочных правил (D-1)» —
и это ровно то, чем он является: приёмник, не гейт. Класс: `DEAD_CODE`
как средство блокировки; `EXISTS` как реестр.

**Но journey GOLD-20 при этом закрыт — другим механизмом, и его надо
назвать, чтобы не оставить ложный минус.** Блокирует выдуманный интервал
исходящий фильтр `apps/orchestrator/safety/outbound.py::evaluate_outbound`,
категория `planning`, и он **подключён к живому клиентскому пути** —
`apps/channels/max/handler.py:784, 2337, 2910` вызывают `guard_outbound`,
который зовёт `evaluate_outbound` без послаблений
(`apps/orchestrator/safety/gate.py:70`). Тесты:
`apps/orchestrator/safety/tests/test_outbound.py:349
test_invented_planning_is_stopped` (параметризован «Между курсами нужно
три-четыре недели», «Повторять следует каждые две недели», «Приходить нужно
каждый день») и `:380
test_the_discriminator_is_the_modality_not_the_interval` — «Приходите через
месяц, если понравится» проходит, «Приходить нужно через месяц»
блокируется.

Разница, которую нельзя схлопывать: фильтр режет **по модальности
долженствования**, а не сверяет интервал с реестром. Он не знает, какие
интервалы правильные, — он запрещает любое директивное утверждение о них.
Докстринг класса это признаёт прямо: «Инвентарь Трека D показал, чем это
утверждать: 11 из 13 типов ограничений в каноне — `UNKNOWN`. Нечем.»
То есть **правильный интервал произнести тоже нельзя**. Для пилота это
скорее хорошо, но это не то, что описывает journey «выдуманный интервал
блокируется» — блокируются все.

### 5.8. Provenance памяти: словаря для «сказал ассистент» нет (GOLD-19)

`ai-bot-platform:apps/identity/models.py:803`

```
PROVENANCE_USER_STATED = "user_stated"
PROVENANCE_USER_CONFIRMED_INFERENCE = "user_confirmed_inference"
```

Оба значения — про пользователя; значения «сказал ассистент» в словаре нет,
поле `null=True, default=None`. Штамп ставится в
`apps/identity/services/memory_writer.py:189`:

```
if source == MemoryEntry.SOURCE_EXPLICIT:
    canonical = {"provenance": MemoryEntry.PROVENANCE_USER_STATED, ...}
```

То есть «это факт от пользователя» решает **вызывающий**, выставив
`source=SOURCE_EXPLICIT`; писатель ему верит. Запрет «вывод ассистента не
может стать USER evidence» на уровне схемы не выразим — он держится на
дисциплине вызывающих. Класс: `PARTIAL`.

### 5.9. Доказательная база под таблицей §4 — где именно лежит каждый тест

Пути проверены на канонических рефах; спорные (GOLD-04, GOLD-20, GOLD-21)
я открыл и прочитал сам, остальные сняты поиском и приведены с номерами
строк, чтобы владелец мог открыть их без меня.

| # | тест |
|---|---|
| 01 | `ai-bot-platform:apps/skills/booking/tests/test_skill.py:359,377,421`; `apps/skills/booking/tests/test_tools.py:271` |
| 02 | нет. `djangoproject-catalog:recommendation/_stages.py` читает `request.need.is_stated`, но `git grep is_stated recommendation/tests/` → 0 |
| 03 | `djangoproject-catalog:goals/tests/test_goal_anketa.py:579`; `goals/tests/test_goal_layer.py:262` |
| 04 | нет. `djangoproject-catalog:recommendation/tests/test_rotation.py:21,32,39` — про стабильность порядка внутри разговора и различие между людьми; полей `exclude`/`seen_ids` в `RecommendationRequest` нет |
| 05 | `djangoproject-catalog:recommendation/tests/test_resolve_endpoint.py:175`; `ai-bot-platform:apps/skills/booking/tests/test_tools.py:252,258`; `test_skill.py:3242` |
| 06 | `ai-bot-platform:apps/skills/booking/tests/test_skill.py:875,906,955` |
| 07 | `djangoproject-catalog:recommendation/tests/test_safety_not_applicable.py:72,98`; отдельный несвязанный гейт бота — `ai-bot-platform:apps/skills/booking/skill.py:1436` + `tests/test_skill.py:1063` |
| 08 | `ai-bot-platform:apps/orchestrator/safety/tests/test_pre_check.py:75,79`; `safety/tests/test_gate.py:199` |
| 09 | `ai-bot-platform:apps/replay/tests/test_live_path_gate.py:207,219` + фикстуры `apps/replay/fixtures/adversarial/crisis_*.yaml`; `djangoproject-catalog:recommendation/tests/test_pipeline.py:260,478` |
| 10 | `djangoproject-catalog:recommendation/tests/test_pipeline.py:198,215`; `test_resolve_endpoint.py:127` |
| 11 | `djangoproject-catalog:goals/tests/test_goal_anketa.py:157` |
| 12 | `djangoproject-catalog:goals/tests/test_goal_anketa.py:599,611` |
| 13 | `ai-bot-platform:apps/orchestrator/tests/test_food_memory_flow.py:138,178`; `apps/skills/food_correction/tests/test_skill.py:287` |
| 14 | `ai-bot-platform:apps/channels/tests/test_global_booking_continuation.py:290,306,336,353` |
| 15 | `ai-bot-platform:apps/bookings/tests/test_booking_callbacks.py:323,593`; `apps/orchestrator/tests/test_dead_ends_drf1492.py:303`; фикстура `apps/replay/fixtures/golden/food_correction/cb_stale_card.yaml` |
| 16 | нет. `apps/miniapp/src/lib/max-sdk.ts:88-260` `parseStartRoute()` знает только статические слаги и два payload'а (`master_invite_*`, `reschedule_*`); `pending-booking-intent.ts:50-91` — `entry_point` только атрибуция, «never parsed back into a route» |
| 17 | `ai-bot-platform:apps/eventbus/tests/test_booking_consumer.py:361,399`; `apps/bookings/tests/test_followups.py:934` |
| 18 | `ai-bot-platform:apps/bookings/tests/test_booking_callbacks.py:474,497`; `apps/skills/booking/tests/test_lookup_routing.py:908,1055,1139` |
| 19 | `ai-bot-platform:apps/identity/models.py:803-807`, `apps/identity/services/memory_writer.py:189`; `ayla-ai-core:tests/test_memory.py:265,278` |
| 20 | `ai-bot-platform:apps/orchestrator/safety/tests/test_outbound.py:299,349,380`; подключение — `apps/channels/max/handler.py:784,2337,2910` |
| 21 | `ai-bot-platform:apps/channels/tests/test_global_onboarding.py:460,473` |
| 22 | `ai-bot-platform:apps/miniapp_api/pending_intent.py:59`, `apps/miniapp_api/tests/test_pending_intent.py:224` (только сверка константы `== 600`), `apps/miniapp/src/lib/pending-booking-intent.test.ts:96` |
| 23 | `ai-bot-platform:apps/skills/booking/tests/test_skill.py:875`; `djangoproject-catalog:appointments/tests/test_internal_booking_rest_1016.py:238`. По цене — теста нет: `apps/miniapp_api/views.py:3520` обещает «Amounts never come from the client (C7.1)», а `git grep C7.1 apps/miniapp_api/tests` → 0 |
| 24 | `djangoproject-catalog:recommendation/tests/test_evidence_strength.py:69`; `recommendation/tests/test_pipeline.py:89,107`; `recommendation/_serializers.py:40` `FORBIDDEN_RESPONSE_FIELDS` |

---

## 6. Находки — п.42 (Deployment / Rollback)

### 6.1. Двойники CI: подтверждено, и они безопаснее, чем звучит

`ci.yml:1` → `name: ci`, id **274201556**.
`ci-docs-skip.yml:23` → тоже `name: ci`, id **279530142**.
`replay.yml:114` → `name: replay`, id **274201559**.
`replay-docs-skip.yml:7` → тоже `name: replay`, id **279530140**.
`deploy-dev.yml:31` → id **274201557**. `deploy.yml:39` → id **277826312**.
Имена заданий у двойников совпадают дословно с настоящими
(`pytest + ruff + mypy`, `miniapp (typecheck + vitest)`,
`replay fixtures (golden + adversarial + voice)`) — это и есть смысл двойника:
удовлетворить required-check на документном PR.

Ложного зелёного на смешанном PR они не дают: оба несут шаг
«Mixed PR — self-cancel», который через `gh run cancel` снимает собственный
прогон, а `miniapp` в `ci-docs-skip.yml` стоит на `needs: test`, поэтому при
самоотмене не стартует вовсе. Класс: `EXISTS`.

### 6.2. `paths-ignore` и оборванная цепочка — подтверждено на живых данных

`ci.yml:28-42`:

```
on:
  push:
    branches: [main, dev]
    paths-ignore:
      - "docs/**"
      - "**/*.md"
```

`deploy-dev.yml:33-38`:

```
on:
  workflow_run:
    workflows: ["ci"]
    types: [completed]
    branches: [dev]
```

Головной коммит `origin/dev` бота на момент замера — **`b1a119bd`,
`docs(nutrition): …`, изменены ровно два файла: `docs/AUDIT_NUTRITION_TARGETS.md`
и `docs/PLAN_NUTRITION_TARGETS.md`**. Последний прогон `ci` на `dev` —
34324588828 на `db601b0b` (07:35 UTC). Прогона на `b1a119bd` и на
`83ed56a9` (тоже документный) нет. Утверждение брифа **подтверждается**:
документная голова означает, что `ci` не запустится, а `deploy-dev`
подписан на `ci`.

**У каталога этого нет:** `djangoproject-catalog:.github/workflows/ci.yml:3-7`
— `on: push: branches: [main, dev]` без `paths-ignore`. Документный коммит
там запускает полный `CI/CD` вместе с job `deploy`. Два репозитория ведут
себя противоположно на одном и том же событии. Класс: `CONTRADICTS_CANON`
(между собой), PILOT IMPACT: `DEGRADED`.

**Механический риск, который я вывел из двух файлов и НЕ наблюдал:**
`workflow_run` сопоставляет по **имени** «ci», а `branches: [dev]` смотрит на
`head_branch` запускающего прогона. У PR `dev → main` `head_branch` равен
`dev`. Документный PR `dev → main` даёт зелёный прогон `ci-docs-skip`
(имя «ci», ветка «dev», ~4 с) — и он удовлетворяет условию запуска
`deploy-dev`. Это выложило бы голову `dev` на пилот без единого настоящего
теста. Не наблюдал ни одного такого прогона; утверждаю механизм, а не факт.

### 6.3. Выкладка не привязана к проверенному коммиту — **наблюдено сегодня**

`deploy-dev.yml` берёт подвижную голову ветки дважды:

```
git pull --ff-only origin dev            # строка внутри «Pull + rebuild + restart dev services»
- name: Checkout dev (для сборки Mini App)
  uses: actions/checkout@v4
  with:
    ref: dev
```

Ни `github.event.workflow_run.head_sha`, ни `github.sha` в чекаут не
подставляются — `github.sha` используется только в тексте телеграм-алерта.

Замер 09.09.2026:

```
ci run 34324588828 head_sha: db601b0b829612a3f66c8ab5cd9044a8d4f329ab  event=push
deploy-dev 34327958994 head_sha: 83ed56a94eb0a96d9599c632f0e6ba2d48c6c5b7  created=2026-09-09T08:13:40Z
```

Прогон `ci` был на `db601b0b`; выкладка, которую он запустил, ушла с
`83ed56a9`. Сегодня дельта оказалась документной и вреда не принесла — но
механизм доказан, а не предположен: при 38-минутном `ci` любой коммит,
приземлившийся в окно, уезжает на пилот непроверенным. Класс: `EXISTS`
(дефект), PILOT IMPACT: `STOP`.

Тот же разрыв внутри одной выкладки: Python-ярус собирается из `git pull`
на машине, Mini App — из `actions/checkout ref: dev` на раннере. Это два
независимых чтения подвижной ветки, они могут разойтись между собой.

### 6.4. Паритет «стейджинга» и пилота: их не два, а один

`deploy.yml` (`main`, прод) — последние прогоны:

```
success  Merge pull request #146 ... deploy  main  push  26072780032  9s  2026-05-19T02:38:07Z
success  Merge pull request #141 ...                     26050483710  9s  2026-05-18T17:48:41Z
... (все семь — 7–12 секунд)
```

Девять секунд — это эпоха скелета, описанная в
`docs/HANDOFF_DEPLOY_AND_RECOVERY.md`. Машинерия включена только 08.09
(коммит `DRF-1578 + DRF-1581`), и с тех пор в `main` не сливали. **Прод
не выкладывался с 19.05.2026, шаг бэкапа ни разу не исполнялся.**

Пилот живёт на `dev`. По `docs/MIGRATION_INVENTORY.md` бот и каталог стоят
на **одной машине** (`194.87.99.126`, пользователь `taximeter`), проекты
`ayla-bot-staging` и `dev`; живые данные пилота — том `dev_postgres_data`,
7.5 МБ на 02.09. Отдельного стейджинга, на котором можно отрепетировать
откат, не существует. `docs/runbooks/rollback-procedure.md:4` это признаёт:
«Last exercised: _staging X-rollback drill pending (DRF-872)_».

### 6.5. Откат: документированного пути нет ни одного

**Path B рунбука** (`docs/runbooks/rollback-procedure.md:170`):

```
sudo nano /etc/ai-bot-platform/.env
# Change: IMAGE_TAG=<old-SHA>
```

Проверка:

```
$ git -C ai-bot-platform grep -n IMAGE_TAG origin/dev
docs/runbooks/rollback-procedure.md:170:# Change: IMAGE_TAG=<old-SHA>
```

**Одно вхождение во всём репозитории — сама эта строка.** Ни
`docker-compose.yml`, ни `docker-compose.staging.yml`, ни `.env.staging.template`
переменной не читают; у сервисов `web`/`worker`/`shadow-worker` стоит
`build:`, а не `image:` — тегированных образов не существует в принципе.
Класс: `STALE_SPEC`, PILOT IMPACT: `STOP`.

**Path A рунбука** — снятие канареечного процента правкой `split_clients`
в nginx на `app.penza.taxi`. Это другая машина и другая схема, чем описанный
в `MIGRATION_INVENTORY.md` пилот (`api-dev.gobeauty.site` на `194.87.99.126`).
Применимость к пилоту не замерена (нет ssh). Класс: `UNKNOWN_NOT_MEASURED`.

**Что откатом фактически является.** Единственный откат, который выдержит
проверку, — ручной: ssh, `git checkout <старый SHA>`, пересборка, `up -d`.
Пересборка на пилоте измерена в самом репозитории —
`djangoproject-catalog:.github/workflows/ci.yml` ставит шагу сборки
`command_timeout: 20m` с пометкой «9m05s на 2026-08-24 и растёт»;
`MIGRATION_INVENTORY.md` называет 3.5 часа при деградации диска. То есть
«откат ≤ 5 мин» из рунбука к этой машине не относится.

**Единственный настоящий откат в коде** — у Mini App, и он там есть
(`deploy-dev.yml`, комментарий перед шагом «Ship Mini App to the box and swap»):

```
# Прежний остаётся в dist.prev для отката:
#   mv dist dist.broken && mv dist.prev dist
```

Класс: `EXISTS` — но только для статики Mini App.

### 6.6. Пилотный стек не восстанавливается из репозитория

`deploy-dev.yml` подключает `-f docker-compose.staging.local.yml` в **пяти**
вызовах compose (строки 106, 114, 139, 171, 284). Файла в
`ai-bot-platform@origin/dev` нет:

```
$ git -C ai-bot-platform ls-tree -r --name-only origin/dev | grep staging
.env.staging.template
config/settings/staging.py
docker-compose.staging.yml
tests/test_staging_stack_completeness.py
```

Он существует только на машине. Следствия, каждое из которых стоит отдельно:
— по репозиторию нельзя узнать, какой командой на самом деле запущен `web`
(базовый `docker-compose.yml:126` задаёт `python manage.py runserver`,
что для пилота заведомо переопределено где-то в оверрайдах);
— `git checkout` старого SHA **не** возвращает конфигурацию контейнеров;
— поднять копию пилота на второй машине из одного репозитория нельзя.
Плюс `.env.staging` и, по `MIGRATION_INVENTORY.md`, **22 файла**
`.env.staging.bak-*` рядом с ним. Класс: `MISSING`, PILOT IMPACT: `STOP`.

### 6.7. Миграции

Проверены **все** вызовы `migrations.RunPython` в обоих репозиториях
разбором аргументов (22 в боте, 8 в каталоге).

**Необратимых в смысле «нет reverse» — ноль.** У каждого вызова второй
позиционный аргумент или `reverse_code=` присутствует.

**Но у девяти reverse — `noop`**, то есть данные назад не возвращаются:

| repo | файл:строка | reverse |
|---|---|---|
| бот | `apps/booking/migrations/0004_backfill_attribution.py:31` | `reverse_noop` |
| бот | `apps/booking/migrations/0018_remotebookingproxy_announcement_claims.py:87` | `RunPython.noop` |
| бот | `apps/booking/migrations/0019_…normalize_awaiting_payment.py:59` | `RunPython.noop` |
| бот | `apps/catalog/migrations/0003_backfill_master_invite_status.py:23` | `reverse_noop` |
| бот | `apps/catalog/migrations/0015_backfill_master_accepted_at.py:109` | `_noop` |
| бот | `apps/catalog/migrations/0017_master_invite_default_pending.py:89` | `RunPython.noop` |
| бот | `apps/handoff/migrations/0002_…silence_notice.py:200` | `RunPython.noop` |
| бот | `apps/tenancy/migrations/0010_tenant_city.py:40` | `_noop_reverse` |
| каталог | `services/migrations/0017_mapping_status_backfill.py:34` | `RunPython.noop` |
| каталог | `users/migrations/0012_tenantuserrelationship.py:167` | `reverse_noop` |
| каталог | `appointments/migrations/0008_backfill_appointment_tenant.py:82` | `reverse_noop` |

Плюс необратимые по данным схемные операции: `apps/orders/migrations/0002_drop_orders_tables.py:108-109`
(`DeleteModel("PaymentEvent")`, `DeleteModel("Order")`),
`apps/identity/migrations/0020_drop_userpreferences_allergies.py:35`,
`djangoproject-catalog:appointments/migrations/0006_remove_payment_state.py:32,36`,
`goals/migrations/0005_remove_clientgoal_tenant.py:40`,
`ai/migrations/0004_conversation_tenant_fk.py:50`. Django их «откатит»,
создав пустые таблицы; данных это не вернёт.

Пять миграций бота идут с `atomic = False` (`CREATE INDEX CONCURRENTLY`,
NOT VALID/VALIDATE): `apps/audit/0003`, `apps/booking/0010`, `apps/booking/0012`,
`apps/identity/0007`, `apps/identity/0008`. Частично применённая такая
миграция не откатывается транзакцией.

**Процедуры обратной миграции не существует.** Поиск по
`docs/MIGRATION_RUNBOOK.md`, `docs/HANDOFF_DEPLOY_AND_RECOVERY.md` и
`ai-bot-platform:docs/runbooks/**` на `manage.py migrate <app> <номер>` даёт
пусто. Рунбук отката прямо запрещает трогать базу —
`docs/runbooks/rollback-procedure.md`, таблица решений:
«`IntegrityError` on `Message` row → **B** — DO NOT roll DB». То есть код
откатывают, схему оставляют вперёд, и совместимость «новая схема + старый
код» ничем не проверяется. Класс: `MISSING`, PILOT IMPACT: `STOP`.

**Где миграции применяются:** у бота — отдельным шагом выкладки
(`run --rm web python manage.py migrate --noinput`, между сборкой и `up`);
у каталога — **автоматически в `entrypoint.sh`** при старте `web`
(«Applying migrations…» → `collectstatic` → gunicorn), то есть до любого
человеческого решения и без предварительного дампа.

### 6.8. Бэкапы базы

| контур | бэкап на пути выкладки | доказательство |
|---|---|---|
| бот, `main` (прод) | **есть**, `pg_dump` + проверка размера ≥1024 байт до всякого изменения | `deploy.yml`, шаг «Postgres backup before deploy» |
| бот, `dev` (**пилот**) | **нет** | в `deploy-dev.yml` шага с `pg_dump` не существует |
| каталог, `dev` (**пилот**) | **нет** | `git grep -rln "pg_dump\|pg_basebackup" origin/dev` → пусто |

Скрипты бэкапа в боте есть (`scripts/backup/pg_base_backup.sh`,
`pg_archive_wal.sh`, `check_backup_freshness.sh`, `restore_pitr.sh`), но они
операторские и ставятся руками: README §Quick start — «`scp` the four `.sh`
files to the prod VM». Ни один workflow их не устанавливает и не проверяет.
Рунбук `docs/runbooks/disaster-recovery.md:3`:

```
> Status: **draft**
> Last exercised: _never_ — first quarterly drill scheduled after deploy (DRF-852)
> Target completion sprint: _Phase 1 / PI2_
```

Единственные найденные копии данных — ручные, на диске разработчика:
`PycharmProjects/Ayla/migration-backup/` (`bot.sql` 17.5 МБ, `backend.sql`
0.98 МБ, `envs.tgz`, `envs-be.tgz`, `nginx.tgz`), все с меткой **03.09.2026**.
На день замера им шесть суток. Работают ли скрипты по cron на самой машине —
`UNKNOWN_NOT_MEASURED` (ssh нет). Класс: `MISSING`, PILOT IMPACT: `STOP`.

### 6.9. Health-checks

**Каталог.** `djangoProject/health.py` — `liveness` и `readiness`;
`readiness` даёт 200 только когда `_check_db` (`SELECT 1`), `_check_cache`
(round-trip) и `_check_migrations` (`len(plan) == 0`) зелёные, иначе 503 с
телом по каждой проверке. Смоук выкладки (шаг 4/4) ждёт 200 до 12 минут
(120 × 6 с) — окно поднято после ложного покраснения 30.08 при живом пилоте.

**Бот.** `apps/orchestrator/views.py:43` — `readyz` асинхронно опрашивает
postgres, redis, chromadb, minio плюс `pipeline_health`; 200/503. Смоук
`deploy-dev` — 5 попыток `curl http://127.0.0.1:8014/readyz/`.

Оговорка: `MIGRATION_INVENTORY.md` фиксирует, что `ayla-bot-staging-chromadb-1`
на 03.09 был **в состоянии `Created`, не запущен**. Если это состояние
сохранилось, `/readyz/` должен быть красным — либо оверрайд убирает chromadb
из проверки. Живого значения нет: `UNKNOWN_NOT_MEASURED`.

Healthcheck'и в compose объявлены только у инфраструктурных сервисов
(`postgres`, `redis`, `chromadb`, `minio`); у `web`/`worker` в базовом
`docker-compose.yml` их нет, а что добавляет
`docker-compose.staging.local.yml` — неизвестно (§6.6).

### 6.10. Алерты

**Каталог:** шаг «Notify on failure (Telegram, runner-side)», `if: failure()`,
с раннера — потому что «RKN egress filtering blocks api.telegram.org from the
RU VPS». Об **успешной** выкладке не сообщается; тихо пропускается при
отсутствии секретов.

**Бот:** «Telegram smoke alert (informational)» через
`manage.py smoke_alert` внутри контейнера, `continue-on-error: true` и `|| true`.
То есть алерт бота отправляется **с той самой машины**, про которую каталог
пишет, что телеграм с неё режут, и его провал не виден нигде. Класс:
`PARTIAL`, PILOT IMPACT: `DEGRADED`.

Дополнительно у каталога есть шаг «Diagnose and bring anything that is down
back up» на `if: failure()` — `docker compose up -d --no-recreate`, то есть
поднять упавшее, ничего не пересоздавая. Это не откат, но это единственная
автоматическая реакция на неудачную выкладку в обоих репозиториях.

### 6.11. Логи и correlation ID (кратко — предмет соседнего окна)

Бот пишет JSON-строку с полями `tenant_id`, `trace_id`, `span_id`,
`pipeline_step` (`apps/observability/logging.py:76,107`). Через границу в
событиях Ayla едет **другое** имя — `correlation_id`
(`djangoproject-catalog:appointments/infrastructure/outbox/envelope.py:186`),
и бот его продолжает цепочкой в
`apps/eventbus/consumers/booking.py:707`. Связки `correlation_id ↔ trace_id`
в боте нет: `git grep -n "correlation_id" origin/dev -- "apps/**" | grep -i trace`
даёт пусто. Один инцидент двумя системами по одному идентификатору не
прослеживается. Подробности — за соседним сабагентом по наблюдаемости.

### 6.12. Конкурентность релизов

Внутри репозиториев разведена и разведена правильно:
`deploy-dev.yml:40` — `group: deploy-dev`, `cancel-in-progress: false`;
`deploy.yml:47` — `group: deploy-prod-${{ github.ref }}`, `cancel-in-progress: false`
(«NEVER cancel a prod deploy in flight»);
`djangoproject-catalog` job `deploy` — `group: deploy-dev-vps`,
`cancel-in-progress: false` с объяснением, почему очередь, а не отмена.

**Между репозиториями не разведена вовсе.** Это две разные группы
concurrency в двух разных репозиториях, а машина одна: выкладка бота и
выкладка каталога могут одновременно делать `docker compose build` на
диске, у которого измеренная задержка записи 3.5 с. Класс: `MISSING`,
PILOT IMPACT: `DEGRADED`.

Отдельно: у `ci` бота 09.09 в 03:50 прогон **отменён**
(`cancelled`, run 34308679711, 1h0m20s), и следующая выкладка в 04:52 —
`workflow_dispatch`, то есть руками. Автоматика в этом месте цепочку не
удержала.

### 6.13. Защита веток

Обе ветки `dev` защищены — подтверждено через API.

| | `ai-bot-platform` | `beautygo_backend` |
|---|---|---|
| required checks | `pytest + ruff + mypy`, `miniapp (typecheck + vitest)`, `replay fixtures (golden + adversarial + voice)` | `lint`, `test` |
| `strict` (ветка актуальна) | да | да |
| требуемых аппрувов | **0** | **0** |
| `enforce_admins` | **false** | **false** |
| force push / удаление | запрещены | запрещены |
| `required_conversation_resolution` | да | да |

Утверждение брифа подтверждено. Две оговорки, которые «защищена» скрывает:
ревью человеком не требуется ни в одном репозитории, и администратор не
подчиняется правилам (`enforce_admins: false`). У каталога job `deploy` в
required-checks **не входит** — красная выкладка не мешает следующему
слиянию.

### 6.14. Путь выключения AI-рекомендации (только путь, не флаг)

Флаги обоих сервисов читаются из окружения на старте процесса
(`os.environ.get(...)` — 160 вхождений в `ai-bot-platform:config/settings/base.py`;
`BOOKING_AUTO_COMPLETE_ENABLED`, `GOAL_RESOLUTION_ENABLED`,
`CROSS_DOMAIN_ENABLED` и др. в `djangoproject-catalog:djangoProject/settings/base.py`).

Следовательно: **выкладка не нужна.** Нужен ssh на машину, правка
`.env.staging` (бот) или `.env` (каталог) и
`docker compose up -d --force-recreate --no-deps web worker …`. Порядок
минут при живом диске.

Четыре оговорки:
1. Ключи `SENTRY_DSN`, `SENTRY_ENVIRONMENT`, `SENTRY_RELEASE`, `DJANGO_ENV`,
   `FIREBASE_CREDENTIALS_PATH` каталога **перезаписываются каждой выкладкой**
   (шаг 1/4 делает `sed -i` + append). Ручная правка именно этих ключей живёт
   до следующего merge в `dev`. Остальные ключи выкладка не трогает.
2. `workflow_dispatch`-пути, который переключил бы флаг без ssh, нет ни в
   одном из шести workflow.
3. Кнопки в админке нет — тот же класс дефекта, что DRF-1581
   (`docs/HANDOFF_DEPLOY_AND_RECOVERY.md`): оператор видит состояние и не
   может на него повлиять.
4. Ssh есть только у окна-заказчика. Для всех остальных путь выключения —
   не «переменная окружения», а «попросить владельца ssh».

Какой именно флаг гасит рекомендацию и каково его живое значение —
предмет соседнего сабагента; здесь замерен только путь.

---

## 6a. Подтверждённые противоречия — обе стороны дословно

### П-1. «Общая фикстура» читается только одной стороной

Сторона А — `ai-bot-platform:tests/fixtures/contracts/README.md:5`:

```
**This directory is the single source of truth.** Both repos load these
exact bytes so their tests can't quietly disagree about what a
`payment.captured` looks like
```

Сторона Б — `djangoproject-catalog@95c917e6`, полный поиск:

```
$ git grep -rn "recommendations.request.json\|MANIFEST.sha256\|contracts/booking.created" origin/dev
(ничего)
```

### П-2. Валидацию «несёт» модуль без вызывающих

Сторона А — `ai-bot-platform:apps/miniapp_api/views.py:2580`:

```
Пропуск формы как есть сохранён намеренно (см.
`recommendations_client.fetch_recommendations`); валидацию несёт
`apps.integrations.ayla.recommendation_resolver_client`, который
разводит три исхода.
```

Сторона Б:

```
$ git grep -n "recommendation_resolver_client import" origin/dev
apps/integrations/ayla/tests/test_recommendation_resolver_client.py:19
apps/integrations/ayla/tests/test_recommendation_resolver_client.py:20
```

### П-3. Откат по тегу образа при отсутствии тегов образов

Сторона А — `ai-bot-platform:docs/runbooks/rollback-procedure.md:170`:

```
sudo nano /etc/ai-bot-platform/.env
# Change: IMAGE_TAG=<old-SHA>
```

Сторона Б — `ai-bot-platform:docker-compose.yml:114`:

```
  web:
    build:
      context: .
      dockerfile: Dockerfile
```

`build:`, а не `image:`; `git grep -n IMAGE_TAG origin/dev` даёт одну
строку — саму цитату из рунбука.

### П-4. «Ayla-side authority» — прогон, которого нет

Сторона А — `ai-bot-platform:apps/integrations/ayla/tests/test_contract_route_table.py:33`:

```
The Ayla-side authority is the nightly staging round-trip
(``tests/e2e``, #1079).
```

Сторона Б — `git grep -rn "AYLA_BASE_URL" origin/dev -- .github` даёт одну
строку, и та в комментарии `ci.yml:478`; расписаний в репозитории два
(`miniapp-drift`, `mirror-base-image`), Ayla ни в одном.

### П-5. Два репозитория противоположно реагируют на документный коммит

`ai-bot-platform:.github/workflows/ci.yml:33`:

```
    paths-ignore:
      - "docs/**"
      - "**/*.md"
```

`djangoproject-catalog:.github/workflows/ci.yml:3`:

```
on:
  push:
    branches: [main, dev]
```

Один и тот же документный merge в `dev` у бота не запускает ни `ci`, ни
выкладку; у каталога запускает полный `CI/CD` вместе с `deploy` на живую
машину.

---

## 7. Реальность тестов и что НЕ замерено

### 7.1. Что покрыто и каким уровнем

| гейт | что реально проверяет | уровень | сегодняшнее число |
|---|---|---|---|
| `ci` / `pytest tests/` | сквозные тесты минус smoke, 38 штук вычеркнуто `--deselect` | UNIT + локальный E2E | `741 passed, 11 skipped, 38 deselected` |
| `ci` / `pytest tests/smoke/` | загрузка Django, конфиг | UNIT | `73 passed` |
| `ci` / `pytest apps/` | основной корпус | UNIT | **числа нет** (см. 7.3) |
| `replay` / live-path gate | adversarial + voice через глобальный обработчик | E2E (одна система) | `64 passed, 149 skipped` |
| `replay` / golden gate | golden через клиентский обработчик | E2E (одна система) | `136 passed, 33 skipped`; `48/81 asserted` |
| каталог `CI/CD` / `lint`+`test` | flake8, detect-secrets, pytest на Postgres | UNIT + CONTRACT | зелёный 09.09 03:50 |
| каталог `smoke-on-dev` | pytest **на живой машине пилота** | **LIVE** | `84 passed in 26.42s` |

### 7.2. Чего теста нет — поимённо

* Нет теста, делающего настоящий вызов бот→каталог в CI (§5.4).
* Нет теста на форму ответа резолвера у потребителя на живом пути (§5.6).
* Нет теста, запрещающего вывод ассистента как USER evidence на уровне схемы (§5.8).
* Нет теста, блокирующего планировочный интервал вне реестра (§5.7).
* Нет теста отката: ни образа, ни миграции, ни compose-конфигурации (§6.5, §6.7).
* Нет теста, что бэкап пилота существует и восстанавливается (§6.8).
* Нет теста, что выложенный на пилот SHA равен проверенному (§6.3).
* Вычеркнуты из CI и потому не защищают ничего:
  `tests/e2e/test_max_echo.py::test_max_webhook_to_echo_happy_path`,
  `tests/e2e/test_privacy_skill.py::test_delete_data_triggers_privacy_skill_and_wipes_user`,
  `tests/e2e/test_handoff_skill.py::test_handoff_flow_creates_task_silences_followup_resumes_on_resolve`
  (`ci.yml:551-553`) — три из них это именно клиентские journey.

### 7.3. Ловушка §3a, найденная в самом конвейере

`ci.yml:487` — комментарий авторов workflow:

```
# No `-q` here on purpose. `addopts` in pyproject.toml is already
# `-q -m "not smoke"`; a second `-q` makes it `-qq`, which
# suppresses the final `N passed` line — a failure then reads like
# a success. (The `pytest apps/ -q` step below has exactly that
# problem today. Out of scope for this ticket, worth its own.)
```

Проверено по логу прогона 34324588828: строки `N passed` у шага
`pytest apps/` в выводе **нет**. Самый большой шаг конвейера не печатает
итог. Это ровно правило «верить только строке `N passed / M failed`»,
и здесь верить нечему.

### 7.4. Тесты локально НЕ запускались — и почему это честнее

Я не запускал ни одного теста. Причины, по правилу §3a:

1. Обе половины предмета уже имеют **сегодняшние** числа из прогонов
   34324588828, 34324588856 и 34331023938 — на канонических SHA, на
   Postgres, в окружении, зафиксированном `uv sync --frozen`. Локальный
   прогон на Windows дал бы другое окружение и другую базу, то есть
   более слабое доказательство при большем шуме.
2. Половина предмета (LIVE-смоук каталога, `deploy-dev`) требует чужих
   контейнеров и записи в живой контур, что режимом запрещено.
3. Ключевые утверждения этого отчёта — про **отсутствие** вызова, файла,
   шага и переменной. Они доказываются грепом по рефу, а не прогоном:
   зелёный прогон отсутствие вызова не показал бы.

Гре́пы, на которых стоят утверждения, приложены дословно в §8.

### 7.5. Что НЕ замерено — честный список

* **Живые значения на пилоте.** Ни одна переменная из `.env.staging` / `.env`
  не прочитана: ssh только у окна-заказчика. Запущен ли `chromadb`,
  какой командой поднят `web`, включён ли cron бэкапа — `UNKNOWN_NOT_MEASURED`.
* **Содержимое `docker-compose.staging.local.yml`.** Файл существует только
  на машине; чем он переопределяет базовый compose — неизвестно.
* **Применимость Path A отката к пилоту.** Рунбук описывает `split_clients`
  на `app.penza.taxi`; есть ли этот nginx-контур на пилотной машине — не проверено.
* **Работает ли `mv dist.prev dist` на самом деле.** Не исполнялось.
* **Не наблюдал** запуска `deploy-dev` от прогона `ci-docs-skip` (§6.2) —
  утверждается механизм из двух файлов, не факт.
* **Реальное поведение 24 journey.** Замерено наличие и уровень тестов,
  а не то, что клиент увидит. Это разные утверждения.
* **Актуальность машины.** `MIGRATION_INVENTORY.md`/`MIGRATION_RUNBOOK.md`
  от 03.09 описывают переезд на новую машину; состоялся он или нет — не проверено.
* **`ayla-ai-core` и `ayla-knowledge`** — SHA сверены, содержимое в предмет
  пунктов 41 и 42 не входило.
* **TTL.** `apps/bookings/pending_actions.py:74` задаёт
  `PENDING_ACTION_TTL = timedelta(minutes=10)`. Тот ли это объект, к
  которому канон v1.1 предъявляет 2 часа, я не устанавливал — это предмет
  окна по канону, а не мой.

---

## 8. Точные команды воспроизведения

```
git -C ai-bot-platform ls-remote origin dev
git -C djangoproject-catalog ls-remote origin dev
git -C ai-bot-platform show origin/dev:.github/workflows/ci.yml
git -C ai-bot-platform show origin/dev:.github/workflows/deploy-dev.yml
git -C ai-bot-platform show origin/dev:.github/workflows/deploy.yml
git -C ai-bot-platform show origin/dev:.github/workflows/ci-docs-skip.yml
git -C djangoproject-catalog show origin/dev:.github/workflows/ci.yml
git -C djangoproject-catalog show origin/dev:.github/workflows/smoke-on-dev.yml
git -C djangoproject-catalog show origin/dev:entrypoint.sh
git -C ai-bot-platform grep -n IMAGE_TAG origin/dev
git -C ai-bot-platform grep -n "staging.local" origin/dev
git -C djangoproject-catalog grep -rln "pg_dump\|pg_basebackup" origin/dev
git -C ai-bot-platform grep -n "RunPython" origin/dev -- "*/migrations/*.py"
git -C djangoproject-catalog grep -n "RunPython" origin/dev -- "*/migrations/*.py"
git -C ai-bot-platform grep -n "recommendation_resolver_client import" origin/dev
git -C djangoproject-catalog grep -rn "recommendations.request.json\|MANIFEST.sha256" origin/dev
git -C ai-bot-platform grep -rn "AYLA_BASE_URL" origin/dev -- .github
gh api repos/AndreyDeveloper84/ai-bot-platform/branches/dev/protection
gh api repos/AndreyDeveloper84/beautygo_backend/branches/dev/protection
gh run view 34324588856 --log | grep "golden gate\]"
gh run view 34324588828 --log | grep -E "passed|deselected"
gh run view 34331023938 --log | grep -E "passed|pytest exit"
gh run list --workflow 274201557 --limit 12
gh run list --workflow 277826312 --limit 8
```

Замечание про Windows: перед `git show <ref>:<путь>` в Git Bash нужен
`export MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*'`, иначе двоеточие в
аргументе превращается в `\;` и команда падает.

---

## 9. Убрать за собой

Временных файлов, веток, worktree и контейнеров **не создавалось**.
Ни один тест локально не запускался (см. §7). Единственный созданный
артефакт — этот файл отчёта.
