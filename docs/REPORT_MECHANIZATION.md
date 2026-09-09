# REPORT: механизация инвариантов

Пишет только исполнитель. Новые секции — сверху.

---

## 2026-08-16, секция 3 — L0 проверен на пилоте: флаг ON, дефект спит

**По просьбе владельца проверил `BOOKING_VIA_AYLA_REST` на пилоте.** VERIFIED дважды, независимо: `docker exec ayla-bot-staging-web-1 env | grep BOOKING_VIA_AYLA_REST` → `true`; `settings.BOOKING_VIA_AYLA_REST` через `manage.py shell` в том же контейнере → `True`. Контейнер подтверждён верный (порт `127.0.0.1:8014->8000`, канонический health-адрес из `project_pilot_host_deploy_mechanics`).

**Это меняет severity находки L0 (не отменяет её).** `apps/skills/booking/tools.py:2058` — `if _booking_via_ayla(): return _execute_reschedule_ayla(...)`, безусловная проверка глобального `settings.BOOKING_VIA_AYLA_REST` (`_booking_via_ayla()`, `tools.py:3308-3317`), без per-tenant override. При `True` каждый вызов `execute_reschedule` уходит по ветке Ayla REST и **не доходит** до баговой `select_for_update().select_related("service")` (nullable FK → LEFT JOIN → Postgres отказывает в `FOR UPDATE`).

**Вывод: дефект реальный и воспроизводимый (VERIFIED тестами на Postgres), но сегодня на пилоте не исполняется — спящий, не активный инцидент.** Снимаю рекомендацию Urgent из предыдущей секции. Остаётся кандидатом L0 в Linear с той же оценкой (1-2 SP), но приоритет — по усмотрению главного окна, не аварийный: код неверен и обязательно сработает первым, кто выключит флаг (или на любом будущем окружении, где флаг случайно OFF), плюс это мёртвый-неверный код, который стоит убрать по гигиене, а не потому что он сейчас кого-то ломает.

---

## 2026-08-15, секция 2 — все четыре задачи закрыты, эскалация по DRF-1121

**Итог одной строкой:** DRF-1109, DRF-1115, DRF-1120 — готово, проверено прогоном, закоммичено. DRF-1121 — готово, но с находкой, которая крупнее самой задачи (см. «Эскалация»), и с решением (таймаут CI 15→45 мин), которое прошу подтвердить или отменить.

**Коммиты:**
- `ai-bot-platform-mech` (`chore/mechanization`): `bb0bac0` (DRF-1109), `5eb1c77` (DRF-1121). Не запушено.
- `beautygo_backend-mech` (`chore/mechanization`): `8a39ead` (DRF-1120), `e9f002a` (DRF-1115). Не запушено.

---

### DRF-1109 — G9 контракт линтера (2 SP, ГОТОВО)

Файл: `tools/lint/import_boundaries.py`. Новый `Contract` `G9-booking-request-outside-owner` (source=`apps/`, exclude=`apps/booking/`, forbidden=`apps.booking.models.BookingRequest`). Понадобилось обобщить механизм: раньше `Contract` знал только позитивный `source_prefixes`, для «везде кроме X» добавил `exclude_prefixes`.

**Критерий готовности (§6 брифа) — что именно поймало правило, замерено:**
- Сняв baseline-запись `apps/bookings/tasks.py` и прогнав контракт с пустым baseline → правило флагает **ровно** `apps/bookings/tasks.py:424` (`detect_completed_bookings`, DRF-1108). Дефект **живой, не почат** — намеренно, читай ниже почему.
- Тот же прогон флагает `apps/miniapp_api/views.py` (4 сайта) — находка про слоты Mini App. Она уже почата в `dev` (`0860183`, до старта этого окна); `_collect_occupied` теперь легитимно живёт только в flag-OFF ветке. AST-линтер не видит рантайм-ветвление, поэтому файл всё равно в BASELINE — но осознанно, с этим объяснением в комментарии, а не молча.

**Побочная находка, крупнее задачи.** Полный скан `apps/` с пустым G9-baseline дал 22 нарушения в **18** production-файлах (тесты/миграции не считаются). Флаг `BOOKING_VIA_AYLA_REST` упоминается только в 3 из них. Остальные **14** читают `BookingRequest` без единого упоминания флага — включая пять master-facing поверхностей `master_api/services/{dashboard,schedule,customers,conversations,conversation_detail}.py`. Тот же класс риска, что A1 (слоты Mini App), потенциально на кабинете мастера — **не проверял легитимность**, значение флага на пилоте UNKNOWN (как и в самом архитектурном ревью). Все 18 занесены в BASELINE тремя группами (a — flag-gated подтверждено, b — DRF-1108 живой дефект явно помечен как такой, c — непроверенные) — полный список с обоснованием в коде, кандидаты продублированы в разделе ниже.

**Опровержение постановки (норма §7) — уточнение, не отказ.** Бриф просил «поймать два известных экземпляра» — подтвердилось. Слабая форма оказалась значительно шире: вскрыла ещё 14 непроверенных мест того же класса. Это не ложные срабатывания (все 18 — реальные импорты вне `apps/booking/`), а неучтённый объём. Не чинил (§4), зафиксировал как candidates.

**Тесты:** `tests/tools/test_import_boundaries.py` 38/38, включая `test_baseline_matches_reality` (регрессия: baseline == находки с пустым baseline побайтово). `python tools/lint/import_boundaries.py apps/` → exit 0.

---

### DRF-1120 — `IN_PROGRESS` недостижим (1 SP, ГОТОВО)

Постановка подтвердилась. Решение по брифу принято однозначно (ревью склонялось к удалению, владелец решения о новом статусе не утверждал) → **удалил** `Appointment.Status.IN_PROGRESS` (`appointments/models.py`).

Единственная точка, которая упала бы на импорте после удаления: `records_api.py:63` — `_UPCOMING_STATUSES` ссылался на `Appointment.Status.IN_PROGRESS` как на атрибут enum (не строку), это `AttributeError` при старте приложения, не тихая деградация. Поправил тут же — это «минимум, без которого правка не проходит», а не расширение объёма.

`records_status.py` (строки 83, 131-132) сравнивает со строкой `"in_progress"` напрямую, не с атрибутом enum — не упадёт, но теперь мёртвый код (ветка недостижима). **Не трогал** — не нужно для прохождения проверки, кандидат ниже.

Добавлен `appointments/tests/test_status_enum_sync.py`: `Appointment.Status ⊆ BookingStatus` — двумя способами (сравнение множеств значений + попытка сконструировать `BookingStatus` из каждого ORM-значения, ровно то место, которое падает в проде через `Appointment.booking_status`).

**Верификация:** `appointments/` + `users/` полностью — **964/964 зелёных** (после исправлений и до коммита). Целевые тесты (DRF-1115+1120) отдельно — 21/21.

---

### DRF-1115 — расхождение списков исключений (1 SP, ГОТОВО)

Постановка подтвердилась: `/api/v1/payments/webhook/` есть в (модульном) `EXCLUDED_PATH_PREFIXES` у `AppTypeMiddleware`, отсутствует в `TenantContextMiddleware.EXCLUDED_PATH_PREFIXES` (оба — `users/middleware.py`).

**Что сравнивать — решил и обосновал** (брифом было отдано на моё усмотрение): симметричная разность двух списков. Списки отвечают на разные вопросы (нужен ли X-App-Type / нужен ли X-Tenant) и легитимно могут расходиться — поэтому не требую равенства, требую **осознанности**: реестр `ACKNOWLEDGED_EXCLUSION_DIVERGENCE` (dict «префикс → почему только в одном списке») рядом с обоими списками + `TestExclusionListDivergence` (2 теста): падает на новом/неучтённом расхождении **и** на устаревшей записи реестра (расхождение исчезло, а строка осталась — тот же принцип, что stale-baseline у линтера).

`/api/v1/payments/webhook/` внесён в реестр как **неподтверждённо легитимный долг** (не «исключение с обоснованием») — по цепочке рассуждения он ДОЛЖЕН быть в обоих списках (YooKassa не шлёт ни X-App-Type, ни X-Tenant), и при `MULTI_TENANT_STRICT=True` (сегодня не дефолт) вебхук получит 400 до вью. Не правил списки — по постановке брифа это не входит в DRF-1115.

**Верификация:** целевые тесты 21/21; `appointments/` + `users/` — 964/964 (общий прогон покрывает оба репо-файла).

---

### DRF-1121 — CI 41/410 (2 SP, ГОТОВО + ЭСКАЛАЦИЯ)

**Постановка подтвердилась и оказалась глубже, чем сформулировано.** Два независимых открытия:

**1. SQLite врёт.** На локальном дефолте (SQLite, `config/settings/local.py`) весь `apps/` (7246 тестов) даёт только 3 падения — известный кластер `apps/miniapp_api` (DRF-1044). Но CI гоняет Postgres (`services.postgres` в `ci.yml`, уже настроен). Прогнал тот же прогон на реальном Postgres (отдельный контейнер `postgres:16-alpine`, тот же `DATABASES`, что в CI) — **27 падений**, ровно предсказано моей памятью `project_test_db_sqlite_vs_postgres`.

**2. Из этих 27 — 23 это ОДИН новый живой дефект, не тестовая хрупкость:**

`apps/skills/booking/tools.py`, `execute_reschedule` (локальный, flag-OFF путь реального reschedule, вызывается из `apps/bookings/callbacks.py:685` на реальном тапе кнопки в чате):
```python
BookingRequest.all_tenants.select_for_update().select_related("service").get(pk=booking.pk)
```
`BookingRequest.service` — `null=True` (`apps/booking/models.py:302`). `select_related` на nullable FK — LEFT OUTER JOIN. Postgres **отказывается** блокировать (`FOR UPDATE`) через LEFT JOIN: `psycopg.errors.FeatureNotSupported: FOR UPDATE cannot be applied to the nullable side of an outer join`. SQLite такого ограничения не знает — поэтому 23 теста (все — reschedule: `apps/booking/tests/test_reschedule.py` ×10, `apps/skills/booking/tests/test_tools_reschedule.py` ×5, `test_tools_q12a.py` ×3, `test_flow_continuation.py` ×4, `apps/bookings/tests/test_booking_callbacks.py` ×1) годами зелёные локально и падают детерминированно на Postgres.

**Это VERIFIED как код и как воспроизводимое поведение теста. Живой ли эффект на пилоте — UNKNOWN**: путь срабатывает только при `BOOKING_VIA_AYLA_REST=OFF` (при ON — ранний возврат через `_execute_reschedule_ayla`), а значение флага на пилоте не читал (тот же UNKNOWN, что и в архитектурном ревью 15.08). **Если флаг OFF на пилоте — каждый тап «подтвердить перенос» в этом пути падает с 500 сегодня.** Это первое, что стоит проверить перед следующим шагом.

Плюс 1 новый: `apps/skills/payment_failed/tests/test_skill.py::TestMasterDMDispatch::test_happy_path_full_chain_dispatches_master_dm` — `NumericValueOutOfRange: integer out of range` на Postgres от значения, которое SQLite молча принял (нетипизированная колонка).

**Что сделал:** не чинил (вне объёма DRF-1121, §4 брифа) — задеселектил все 27 в новом CI-шаге `pytest apps/ (full suite)` с группировкой по первопричине в комментарии (a — DRF-1044 известное, b — 23 reschedule/FOR UPDATE новое, c — 1 integer-range новое). Полный `apps/` теперь идёт в CI при каждом пуше.

**3. Таймаут — независимая проблема, не только падения.** Полный прогон `apps/` на Postgres занял **~30 минут** локально (7246 тестов). Текущий `timeout-minutes: 15` для всего CI-джоба этого физически не вместит — даже с нулём падений. Поднял до **45**. Это меняет время обратной связи по каждому PR с нескольких минут до ~35-40 — решение сделал, но прошу подтвердить или отменить отдельно: альтернативы (параллелить через `pytest-xdist`, отдельный неблокирующий job для полного прогона) не реализовывал — не входили в 2 SP, обе зафиксированы кандидатами ниже.

---

## Эскалация

**Снято 2026-08-16 (секция 3):** `BOOKING_VIA_AYLA_REST=True` на пилоте — проверено. `execute_reschedule` не доходит до баговой строки на боевом пути. Дефект реальный, но не Urgent — L0 остаётся обычным кандидатом в Linear (см. таблицу выше), не аварией. Пункты 1-3 ниже закрыты, оставлены как есть для истории.

<details>
<summary>Исходная эскалация от 2026-08-15 (archive)</summary>

**Кандидат уровня «до пилота», не «после»** — тот же класс, что находка A1 архитектурного ревью (слоты Mini App отдавали занятое время как свободное): **reschedule на flag-OFF пути детерминированно падает на Postgres.** Прошу главное окно:
1. Проверить значение `BOOKING_VIA_AYLA_REST` на пилотном хосте.
2. Если OFF — завести Linear-задачу с приоритетом **Urgent** (по образцу C3/A1 в архитектурном ревью), не «после пилота».
3. Если ON — дефект существует в коде, но не на боевом пути; приоритет по усмотрению главного окна.

</details>

**Решение, требующее подтверждения (без изменений):** `timeout-minutes: 15 → 45` в `ci.yml` — сделал, не жду ответа для остального объёма (§2 протокола: «вопрос задал — работу не бросай»), но не мержил бы это в одиночку на месте главного окна.

---

## Кандидаты в Linear

| # | Суть | Источник | Оценка |
|---|---|---|---|
| **L0** | **`execute_reschedule` (flag-OFF, apps/skills/booking/tools.py): `select_for_update().select_related("service")` падает на Postgres со 100% детерминизмом — `BookingRequest.service` nullable → LEFT JOIN → `FOR UPDATE` запрещён Postgres. Живой ли эффект на пилоте зависит от `BOOKING_VIA_AYLA_REST` (UNKNOWN)** | DRF-1121, 2026-08-15 | не оценивал; вероятно 1-2 SP (снять `select_related`, добрать `service` вторым запросом или через `select_for_update(of=("self",))`) |
| L1 | `apps/skills/payment_failed/tests/test_skill.py::TestMasterDMDispatch::test_happy_path_full_chain_dispatches_master_dm` — `integer out of range` на Postgres, тестовая фикстура или реальный тип поля | DRF-1121 | не оценивал |
| L2 | 14 непроверенных production-чтений `BookingRequest` вне `apps/booking/`, включая 5 master-facing поверхностей (`master_api/services/*`) — триаж каждого: легитимно или тот же класс, что A1 | DRF-1109, BASELINE группа «c» в `import_boundaries.py` | per-file триаж |
| L3 | `apps/bookings/tasks.py:424` (`detect_completed_bookings`, DRF-1108) — по-прежнему не флаг-гейтится; теперь явно помечен в BASELINE как живой дефект | подтверждение существующего DRF-1108 | уже заведено |
| L4 | `/api/v1/payments/webhook/` отсутствует в `TenantContextMiddleware.EXCLUDED_PATH_PREFIXES` (бэкенд) — под `MULTI_TENANT_STRICT=True` вебхук YooKassa получит 400 | DRF-1115 | 0.5 SP (добавить префикс) |
| L5 | `records_status.py` — ветка `"in_progress"` (строки 83, 131-132) теперь мёртвый код после удаления `IN_PROGRESS` из ORM enum | DRF-1120 побочный эффект | косметика |
| L6 | Полный `apps/` на CI занимает ~30 минут — рассмотреть `pytest-xdist` (параллелизация) или отдельный неблокирующий job вместо простого `timeout-minutes` 15→45 | DRF-1121 | не оценивал |

---

## 2026-08-15, секция 1 — §5 (постановка по дереву), архив

<details>
<summary>Первая секция (стартовые one-liner подтверждения по всем 4 задачам, до завершения) — свёрнута, содержание вошло в секцию 2 выше</summary>

Блокеры окружения (оба worktree — свежие, без venv) были пройдены: бот — `uv sync --extra dev --frozen` + `uv pip install -e ../ayla-ai-core`; бэкенд — `uv venv .venv` + `uv pip install -r requirements.txt` (первая попытка упёрлась в orphaned uv-lock после случайного `taskkill` — не повторялось при повторных попытках).

</details>
