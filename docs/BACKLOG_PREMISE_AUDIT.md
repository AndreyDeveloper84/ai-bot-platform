# Замер посылок бэклога DRF

**Дата замера:** 2026-08-29
**Метод:** чтение `origin/dev` (`git show origin/dev:<path>`, `git grep <pattern> origin/dev`) после `git fetch origin`. Содержимое файлов из рабочих деревьев не читалось ни разу.

Кластер «тесты и CI» замерен **прогонами**, а не чтением: отдельный worktree на `origin/dev` в скрэтчпаде, Postgres 16 и Redis 7 в изолированных контейнерах, своя база `test_prem_b4` на нестандартных портах (общая `test_beautygo` не использовалась). Worktree за собой убран, основные чекауты не трогались.

Исключение — три задачи, чей **предмет и есть состояние хоста**, а не код: `DRF-1136` (записи в `git stash`, наличие чужого чекаута), `DRF-979` (`.git/config` локальных клонов), `DRF-1195` (некоммиченные файлы решений владельца). Там замерялось фактическое состояние диска, только на чтение и без печати значений секретов.

**Срез кода на момент замера:**

| репозиторий | `origin/dev` | дата |
|---|---|---|
| `ai-bot-platform` | `c3ae663` | 2026-08-26 |
| `djangoproject` | `d400e130` | 2026-08-26 |
| `ayla-knowledge` | `origin/main` | — |

**Оба рабочих дерева стояли на чужих ветках** (`docs/ux-canon-reconciliation` и `feat/memory-foundation-internal-api`). Замер по рабочему дереву дал бы неверный ответ — предупреждение из брифа подтвердилось на практике.

**Правки не вносились.** Ни одного файла в репозиториях, ни одной ветки, ни одной мутации в Linear. Единственный записанный файл — этот отчёт. Оба репозитория оставлены ровно в том состоянии, в каком найдены: на своих ветках, со своими незакоммиченными изменениями чужих окон, `git status` не изменился.

**Linear-MCP в этой сессии не поднялся** (`CONNECT_TIMEOUT`). Бэклог получен прямым GraphQL к `https://api.linear.app/graphql`, только на чтение; ключ читался в переменную и нигде не печатался.

---

## Что в бэклоге

Открытых задач DRF (не Done, не Canceled): **617**.
Из них с последним обновлением **старше 7 дней** (до 2026-08-22): **421**.

| состояние | шт. |
|---|---|
| Backlog | 314 |
| Duplicate | 51 |
| Todo | 41 |
| In Progress | 15 |

Замерено **поштучно: 97**. Остальные **~324** разобраны классами (см. «Что замерено классом, а не поштучно»).

---

## Сводка по поштучным замерам

| вердикт | шт. |
|---|---|
| **ЖИВА** | 50 |
| **СМЕСТИЛАСЬ** | 25 |
| **ПРОТУХЛА** | 15 |
| **НЕ ЗАМЕРЯЕТСЯ ЧТЕНИЕМ КОДА** | 7 |

Протухшие плюс сместившиеся — **40 из 97 (41 %)**. Правило, ради которого затевался замер, подтверждается: **каждая вторая-третья задача старше недели описывает не то, что есть на `origin/dev` сегодня.**

Причём **сместившихся больше, чем протухших** — и это важнее самой доли. Протухшая задача обнаруживает себя быстро: исполнитель открывает файл, файла нет, он идёт спрашивать. Сместившаяся выглядит рабочей до самого конца: файл на месте, симптом похож, и человек чинит не то. Три примера из этого замера:

- **DRF-1024** просит сузить `ALLOWED_HOSTS` правкой `.env.staging` — а значение захардкожено в `settings/dev.py:34` и из env вообще не читается. Правка не даст ничего, и понять почему можно только прочитав другой файл.
- **DRF-1053** описывает отсутствие тай-брейка в курсоре. Тай-брейк добавлен — но в сортировку, не в курсор. Читатель увидит комментарий «Secondary key on id for deterministic tie-break» и закроет задачу как сделанную. Дефект останется.
- **DRF-1122** верно утверждает, что `has_capability` не вызывается нигде, и неверно выводит из этого, что роль receptionist недостижима. Она достижима — через вручную написанные проверки. Работа по описанию означала бы переписывание работающего гейта.

### Оговорка о качестве самого замера

Кластер «тесты и CI» замерен **прогонами**; остальные — чтением `origin/dev`. Разница оказалась не косметической.

По **DRF-1044** я поставил «ЖИВА», прочитав комментарий в `.github/workflows/ci.yml:571-572`, где прямым текстом написано «3 pre-existing miniapp_api failures». Прогон дал «41 passed»: падения починили коммитом `9a71ba5` 2026-08-18, а комментарий и три `--deselect` остались. **Я замерил посылку по документу — и документ сам оказался протухшим.**

Отсюда поправка к методу, которая стоит дороже любой строки таблицы: **комментарий в коде — такой же источник посылки, как тикет, и протухает так же.** Там, где посылка проверяется прогоном, читать про неё нельзя.

Обратная сторона того же: `ci.yml` **был** отличным источником по DRF-1217 и DRF-1131 — там, где он описывает первопричины, он точен и полезен. Ненадёжен он именно в утверждениях «это сломано сейчас».

---

## PII, безопасность, тенантность

| задача | посылка одной строкой | вердикт | чем замерено | что на самом деле (если сместилась) |
|---|---|---|---|---|
| DRF-1010 | PII-скоуп не выставляется на маршруте глобального консьержа — сообщения уходят в LLM прозрачным пробросом | **ЖИВА** | `apps/llm/pii_protected_provider.py:163-186` — no-op pass-through + `logger.warning("pii_protected_provider.no_active_scope")` при `current_conversation_id() is None`; `apps/llm/router.py:227` подтверждает обёртку. Скоуп ставится только в `apps/orchestrator/pipeline.py:621` и `apps/master_api/services/ai_drafts.py:675`; в `concierge.py` и `channels/max/handler.py` — ноль вызовов | — |
| DRF-1011 | Согласия HEALTH и PHOTO_BIOMETRIC объявлены, но не запрашиваются и не проверяются | **СМЕСТИЛАСЬ** | `apps/orchestrator/nutrition_context.py:20-40,177-190` (`_consent_open`) | HEALTH **уже проверяется** — fail-closed гейт через `has_global_consent` (добавлено DRF-1284). Но гейт всегда закрыт: грантить консент по-прежнему негде, и докстринг честно это фиксирует. PHOTO_BIOMETRIC как enum действительно мёртв; у food-scanner есть параллельный небиометрический `BotUser.food_scanner_consent_at`, который ставится только из `legacy_maxbot/`, не подключённого к живому пути. `@consent_required` объявлен и нигде не применён |
| DRF-1036 | Знание UUID само по себе даёт доступ к ПДн на четырёх s2s-маршрутах | **ЖИВА** | `users/internal_users_urls.py:57-87` — все 4 вью на `permission_classes = [IsInternalBearer]`; `users/permissions.py:210-244` сверяет только статический токен, UUID из пути не трогает. Докстринг самого класса говорит, что он для «catalog-shaped», а не «on-behalf-of-user» эндпоинтов — код документирует, что применён не туда | — |
| DRF-1037 | Связывание прокси с настоящим аккаунтом переносит только резолюцию, история теряется | **ЖИВА** | `users/services.py:229-235` — цитата из тикета совпадает дословно | — |
| DRF-1038 | 152-ФЗ: удаление и экспорт ПДн работают по одному `user_id` и не обходят связанные прокси | **ЖИВА** | `users/services.py:238-249` — дословное совпадение, ссылка на AYLA-DEC-0016 §4 сохранена | — |
| DRF-1132 | Вебхук YooKassa исключён из `AppTypeMiddleware`, но не из `TenantContextMiddleware` — 400 при включении строгого режима | **ЖИВА** (и формализована) | `users/middleware.py:67-105` vs `:226-241`; с 15.08 расхождение зафиксировано явно в `ACKNOWLEDGED_EXCLUSION_DIVERGENCE` (`:373-390`) + тест-страж `TestExclusionListDivergence` | — |
| DRF-1024 | На пилоте `ALLOWED_HOSTS=['*']`, значение приезжает из `.env.staging` — сузить | **СМЕСТИЛАСЬ** | `djangoProject/settings/dev.py:34` (`ALLOWED_HOSTS = ["*"]`); `docker-compose.dev.yml:28,48,63` — пилот стартует на `settings.dev` | `['*']` **захардкожено в `settings/dev.py`**, а не читается из env. `DJANGO_ALLOWED_HOSTS` читается только в `settings/prod.py:11`, который на пилоте не используется. **Предложенное в тикете действие — правка `.env.staging` — не даст эффекта вообще.** Исполнитель по описанию потратит время впустую |
| DRF-1116 | 468 обходов тенантного скоупа через `all_tenants` в проде; начинать с `provider.py:230` | **ЖИВА** | `apps/skills/booking/provider.py:239,275` — код тот же (`RemoteBookingProxy.all_tenants.get(...)` без tenant-фильтра), строка сдвинулась 230 → 239. Свежий подсчёт `.all_tenants.` вне tests/migrations даёт **623**, а не 468 — цифра устарела за 11 дней, направление подтверждено | — |
| DRF-1152 | Новый префикс `/api/v1/*` обязан попасть в `STRICT_OPT_OUT_PREFIXES`, три случая подряд; нужны правило в ADR и тест-страж | **ЖИВА** | `apps/tenancy/middleware.py:60-105`, `:135-141`; регрессионные тесты на все три случая есть (`test_middleware.py`), но **обобщённого теста-стража по всем зарегистрированным `/api/v1/*` нет**, и правило в `ADR-0001` не сформулировано. Оба пункта «что сделать» не выполнены | — |

---

## Биллинг, оплаты, миграции

**Важная поправка по атрибуции продукта — см. раздел «Ловушка одноимённых репозиториев» ниже: DRF-952 и DRF-1177 относятся к магазину proff58.ru, а не к Ayla.**

| задача | посылка одной строкой | вердикт | чем замерено | что на самом деле (если сместилась) |
|---|---|---|---|---|
| DRF-952 | Нет 30-минутного резерва товара и автоотмены неоплаченного заказа | **НЕ ЗАМЕРЯЕТСЯ ЧТЕНИЕМ КОДА** | Задача про cart/checkout магазина **proff58.ru**. Кодовой базы магазина на машине нет: `itsolve` (GitLab) — статический лендинг (`index.html`, `styles.css`), последний коммит 2026-07-23, ветки `dev` нет | В репозиториях Ayla есть **одноимённый** `apps/orders`, но он retired (PR #739, миграция `0002_drop_orders_tables.py`, runbook `orders-yookassa-retirement-deploy.md` со статусом complete). Замер по нему даёт ложное «ПРОТУХЛА» — это другой продукт |
| DRF-1177 | В `OrderAdmin.list_display` не видно способ оплаты | **НЕ ЗАМЕРЯЕТСЯ ЧТЕНИЕМ КОДА** | То же: `apps/orders/admin.py` — файл магазина proff58.ru, не Ayla. В репозиториях Ayla `OrderAdmin` отсутствует, потому что `apps/orders` ретирнут, а не потому что задача сделана | — |
| DRF-1142 | Починка DRF-1108 молча выключит начисление баллов: loyalty ищет `booking_id` как PK `BookingRequest` | **ЖИВА** | `apps/loyalty/subscribers.py:113,174` — точное совпадение строк; `config/settings/production.py:99-100` — `DOMAIN_EVENT_SUBSCRIBERS` содержит только `AuditSubscriber`; `tools/lint/import_boundaries.py:438,1018` («Unfixed here on purpose» про DRF-1108); докстринг `subscribers.py:29-33` — «Booking.completed wire-up — NOT YET» | — |
| DRF-1221 | `AYLA_BASE_URL` не проверяется при старте — пустой URL проходит выкладку и падает на первой брони | **ЖИВА** (номер строки сместился) | `config/settings/production.py` — fail-fast есть для `AYLA_INTERNAL_API_TOKEN`, `SENTRY_DSN`, `CHROMA_AUTH_TOKEN`, `MYSITE_WEBHOOK_HMAC_SECRET`, `AYLA_BASE_URL` не встречается нигде. Runtime-`ValueError` теперь на `booking_client.py:650`, а не `:611` | — |
| DRF-1125 | Тест отката миграций оставляет `payments/0003` снятой и ломает последующие тесты прогона | **ПРОТУХЛА** | `payments/tests/test_table_rename_migration.py` на `origin/dev` содержит `autouse`-фикстуру `_restore_payments_schema`, мигрирующую `payments` до leaf-узлов после каждого теста; докстринг фикстуры прямо описывает инцидент PR #227 и говорит «the autouse teardown is what actually restores the schema now» | — |
| DRF-1006 | Снять временный allowlist health-check gate после появления `resolved_requires_health_check` | **СМЕСТИЛАСЬ** | Поле появилось: `apps/catalog/migrations/0012_masterservice_resolved_health_check.py`, `apps/catalog/models.py:541`; читается в `apps/skills/booking/skill.py::_resolved_health_check_for_edge` (DRF-1353) | Условие снятия наступило, но allowlist (`BOOKING_HEALTH_CHECK_GATE_DISABLED_TENANTS`, `skill.py:1243-1249`, вызов на `:1642`) **оставлен намеренно** как fallback для `None` (edge ещё не синхронизирован). Это уже не «забытая уборка», а отдельное решение — задачу надо переформулировать, а не выполнять как написано |
| DRF-1222 | Нет уникального констрейнта `CatalogMaster(tenant, ayla_user_id)` — дубли мастеров ничем не запрещены | **ЖИВА** | `apps/catalog/models.py` — `class CatalogMaster` `Meta.unique_together = (("tenant", "external_id"),)`, `ayla_user_id` не участвует. Путь создания дублей подтверждён: `apps/admin_api/views_invite.py:603` — `CatalogMaster.all_tenants.create(...)` безусловно, idempotency-проба только по name+contact и только для `mode=invite` | Мелочь: `__str__` печатает `...@None` (external_id для Ayla-мастеров `NULL`), а не пустой хвост, как написано в тикете. Суть та же |
| DRF-1158 | `hash()` в `IntegerField` — плавающее переполнение, видимое только на Postgres | **ПРОТУХЛА** | `apps/skills/payment_failed/tests/test_skill.py` — хелпер `_stable_external_id` (SHA-256) на строке 142, используется на 233 и 255; сырого `hash() & 0xFFFFFFFF` нет. `tools/lint/import_boundaries.py:1182` называет этот хелпер эталоном; `.github/workflows/ci.yml:596` — «Fixed at the root in the fixture» | — |

---

## Машина состояний визита, роли, словари акторов

| задача | посылка одной строкой | вердикт | чем замерено | что на самом деле (если сместилась) |
|---|---|---|---|---|
| DRF-1064 | `complete()`/`mark_no_show()` гейтятся на `is_specialist`+владение → салон закрыть визит не может; нет автозакрытия | **СМЕСТИЛАСЬ** | `appointments/views.py:414-476`, `appointments/authz.py` (`may_operate_on_bookings`, `resolve_booking_operator`), `appointments/application/services/completion.py`, `appointments/tasks.py:274-537` (`auto_complete_elapsed_bookings`), `djangoProject/settings/base.py:900` (beat-запись), коммиты `b163e11a`/`59b25643` (2026-08-15), `8a39eadc`/`e2b218ba` (DRF-1120) | **Все три «VERIFIED» блокера тикета устранены коммитами того же дня, что и написан тикет.** (1) `OperationalActor{CLIENT,SPECIALIST,SALON,SYSTEM}` + `resolve_booking_operator` позволяют админу тенанта закрыть визит; (2) `auto_complete_elapsed_bookings` в Celery beat (флаг `BOOKING_AUTO_COMPLETE_ENABLED`, дефолт **false**) закрывает истёкшие брони и проставляет `completed_by`; (3) `IN_PROGRESS` удалён из `Appointment.Status` целиком. **Открытым остаётся только слой 2** — «Ayla спрашивает клиента и закрывает по «да»» — и решение владельца о безопасном умолчании |
| DRF-1133 | Мёртвая ветка `in_progress` в `records_status` после удаления статуса | **ЖИВА** | `appointments/records_status.py:83` (`"in_progress"` в `DERIVED_STATUSES`) и `:131-132` (`if status == "in_progress"`) — номера строк совпадают с тикетом | — |
| DRF-1112 | Легаси-обработчик переноса пишет без версионного гарда параллельно каноническому | **ЖИВА** | `apps/eventbus/consumers/booking.py:1091-1180` (легаси — только dedup по `last_synced_event_id`) vs `:1337-1420` (канонический — полная машина `last_applied_appointment_version`); оба зарегистрированы на `:1828,1830`. Докстринг канонического сам называет эту асимметрию | — |
| DRF-1117 | `initiator_role` закрыт в `{client,specialist,system}`; молчаливый `else "user"` делает неизвестного актора клиентом | **СМЕСТИЛАСЬ** | `appointments/domain/value_objects.py:120-153` (`OperationalActor.SALON`), `appointments/application/dto.py:88-106` (`CancelBookingDTO.__post_init__` → `ValueError`, DRF-1156), `dto.py:112-130` | Салонный актор существует, но называется `salon`, а не `salon_staff`. `raise` вместо молчаливого дефолта добавлен **только для отмены**; `RescheduleBookingDTO` не имеет `__post_init__` и по-прежнему молча дефолтит `initiator_role="client"`. Теста на полноту перевода нет |
| DRF-1122 | `has_capability` не вызывается нигде вопреки ADR-0008; роль receptionist недостижима | **СМЕСТИЛАСЬ** | `apps/identity/services/role_resolver.py:216` — определение; вне собственного теста и `__all__` — **0 продовых вызывающих**. Но `role_resolver.py:306,314,323,340` резолвит `is_receptionist` как настоящий булев; `apps/admin_api/auth.py` (`require_admin_role`) исключает receptionist из owner/admin-эндпоинтов (403); `apps/channels/max/salon_handler.py:276`, `staff_menu.py:98,129` ветвятся на `is_receptionist` | Первая половина посылки верна дословно (0 вызывающих — реальный дрейф ADR-vs-код). **Следствие в тикете неверно: receptionist сегодня достижим и гейтится корректно** — вручную написанными булевыми проверками, а не матрицей возможностей. Правка нужна намного уже, чем описано |
| DRF-1123 | `MasterNotificationPrefs`: модель, CRUD и аудит есть, читателей среди отправителей ноль | **ЖИВА** | Единственный читатель вне CRUD-модуля — `apps/master_api/views.py:1447` (эндпоинт настроек, не отправитель); `apps/skills/payment_failed/skill.py:276-291` прямо в докстринге: «NO MasterNotificationPrefs gate… we don't gate on prefs anyway»; grep `.personal_message`/`.quiet_hours` вне CRUD/тестов — 0 | — |
| DRF-1082 | `create_solo_provider` написан и покрыт тестами, но нигде не подключён | **ЖИВА** | `apps/identity/services/solo_onboarding.py:256` — определение; все прочие хиты — собственный тест либо проза в runbook. `apps/identity/views.py:111` импортирует соседний `is_solo_provider`, не `create_solo_provider` | — |
| DRF-1045 | В словаре канонических событий нет префикса `identity.*` — предупреждения на каждом сообщении | **ЖИВА** (строки сдвинулись) | `apps/events/vocabulary.py:333-401` — `CANONICAL_EVENTS` перечислен целиком, `identity.` нет; `apps/identity/services/resolver.py:130,228` эмитит эти события безусловно; предупреждение теперь на `apps/events/services.py:113-118`, а не `:31-36` | — |
| DRF-986 | Модель не поддерживает услуги с двумя мастерами на один слот | **ЖИВА** (кодовая часть) | `appointments/models.py:51` — `specialist` единственный FK; `services/models.py:402-460` — `SpecialistService` с `UniqueConstraint(["specialist","salon_service"])`, одна строка на пару, без понятия группы | Утверждение тикета про «0 активных рёбер» в БД не проверялось — это данные |
| DRF-987 | Нет флага «снято с продажи» у `SalonService` — снятые услуги видимы в каталоге | **ЖИВА** | `services/models.py:314-360` — полный список полей: `is_active` единственный булев, связанный с видимостью | — |
| DRF-983 | `channels.max.outbound.sent` не в `CANONICAL_EVENTS` | **ЖИВА** | Событие эмитится на `apps/channels/max/handler.py:2285`; в `apps/events/vocabulary.py` строки `channels.max.outbound` нет. Тот же класс, что DRF-1045 | — |
| DRF-982 | `cb:menu:*` определён вне `keyboards.py` и разорван между двумя скиллами | **СМЕСТИЛАСЬ** | Перевод `cb:menu:*` в каноническую фразу собран в одном месте — `apps/channels/max/quick_actions.py:295-370` (DRF-1051). Модулей `keyboards.py` в репозитории три (`apps/bookings/`, `apps/channels/telegram/`, `apps/orchestrator/ui/`), и ни один из них не про MAX-меню | Разрыв «между двумя скиллами» больше не наблюдается; остаётся вопрос о том, где такому месту положено быть — это уже другая задача, чем написанная |
| DRF-1052 | Нет единой проверки пригодности повтора (`can_prefill_booking`) | **ЖИВА** | `can_prefill_booking` встречается только в спецификации `docs/screens/customer-records-flow.md:618` — в коде обоих репозиториев реализации нет | — |

---

## API-контракты и данные

| задача | посылка одной строкой | вердикт | чем замерено | что на самом деле (если сместилась) |
|---|---|---|---|---|
| DRF-1016 | Легаси-эндпоинты `internal/services/` отдают `200 OK` с `count: 0` молча | **ЖИВА** | `djangoProject/urls.py:112` — маршрут смонтирован; `services/internal_api.py:26,35` — обслуживается легаси-моделью `Service`; `docs/CATALOG_INTERNAL_API_CONTRACT.md:11` и `services/internal_catalog_urls.py:5` прямым текстом: «legacy `/api/v1/internal/services/` … **unchanged and remains available** during the strangler-fig transition». Ни `410`, ни `501`, ни удаления | — |
| DRF-1001 | `AVAILABLE_DATES_WINDOW_DAYS` зашита в код, вынести в настройки | **ЖИВА** | `apps/integrations/ayla/booking_client.py:80` — `AVAILABLE_DATES_WINDOW_DAYS = 14`, модульная константа, не `settings`. Появился клэмп `MAX_AVAILABLE_DATES_WINDOW_DAYS = 31` (`:90`, применяется на `:950`) и параметр `window_days` в сигнатуре (`:925`), но все три живых вызова (`:1000`, `:1042`, `:1076`) передают ту же константу | — |
| DRF-1053 | Курсор `me/bookings` без тай-брейка по `id` — потеря записей на границе страницы | **ЖИВА** (и это половинчатая починка, опаснее целой) | `appointments/records_api.py:254` — **сортировка тай-брейк получила**: `order_by(order, "id" if ascending else "-id")`, с комментарием «Secondary key on id for deterministic tie-break». Но **сам курсор — нет**: `:271-273` кладёт в него только `start_datetime.isoformat()`, а `:264,266` фильтрует строгим `start_datetime__gt` / `__lt`. Записи с той же секундой на границе страницы по-прежнему выпадают | Читатель, увидев комментарий про тай-брейк на строке 252-254, решит, что задача сделана. Сделана половина: детерминированный порядок есть, курсор его не переживает |
| DRF-1017 | Контрактного описания внутренних `internal/catalog/*` в репозитории нет | **СМЕСТИЛАСЬ** | Контракт **существует** — `djangoproject/docs/CATALOG_INTERNAL_API_CONTRACT.md`, 210 строк, и покрывает ровно то, что ревьюер не смог сверить: фильтры `tenant`, `template`, `is_active`, форма ответа, пагинация (`PageNumberPagination`, `PAGE_SIZE=20`). В репозитории **бота** его нет: `docs/architecture/` содержит `ayla-booking-rest-contract.md`, `event-contract.md`, `jwt-contract.md`, `contract-matrix.md` — каталожного нет | Документ есть, но в другом репозитории. Задача сжимается с «написать контракт» до «сослаться на существующий из `contract-matrix.md`». Автор тикета просто не знал, что он есть — что само по себе довод в пользу перекрёстной ссылки |
| DRF-1127 | Сортировка связей тенанта без тай-брейка — нестабильный порядок и потеря на границе страницы | **ЖИВА** (номер строки совпадает) | `users/tenant_relationships_api.py:64` — `.order_by("-granted_at")`, вторичного ключа нет; `granted_at` — `auto_now_add` | — |
| DRF-1018 | Две параллельные реализации обхода страниц с разной семантикой | **ЖИВА** | `apps/integrations/ayla/booking_client.py:840` (`_get_all_rows`, raise при расхождении `count`) и `apps/catalog/services/http_client.py:326` (`_fetch_all_checked`, возвращает флаг полноты) — обе на месте, семантика по-прежнему разная | — |
| DRF-976 | `intake_confirm --staff` батч-глобальный — нет гарда от декартова произведения | **ЖИВА** | `services/management/commands/intake_confirm.py:37-39` — флаг `--staff` без ограничений; `CommandError` в файле поднимается только на ненайденный tenant и category, гарда «`--staff` + более одного драфта» нет | — |
| DRF-978 | CSV-интейк молча принимает пустой `staff_ids` | **ЖИВА** | `services/integrations/intake/sources.py` — `staff_ids` собирается split'ом по `;` и кладётся в `raw`, ни одного `warning` на пустое значение | — |
| DRF-961 | `Tenant.city` пуст, из-за чего discovery возвращает 0 кандидатов; нужен автозаполнитель или валидация | **ЖИВА** | `apps/tenancy/models.py:248-255` — поле есть (миграция `0010_tenant_city`), комментарий прямо говорит «**Blank until backfilled**». Ни валидации, ни management-команды бэкфилла, ни проверки в админке в `apps/tenancy/` нет | — |
| DRF-964 | `_render_master_cards`: текст режется по `_MAX_REPLY_CHARS`, кнопки — нет; текст и клавиатура расходятся | **ЖИВА** | `apps/orchestrator/concierge.py:1019` собирает карточки, `:1023` режет только текст (`"\n".join(lines)[:_MAX_REPLY_CHARS]`); `action_data` не трогается. Ещё два вызова `_render_master_cards` — `:1526`, `:1685` | — |
| DRF-965 | Двойное применение `_service_match_q` — ILIKE-матчинг `MasterService` выполняется дважды за запрос | **СМЕСТИЛАСЬ** | `apps/marketplace/discovery.py:998` (`_service_match_q`), применяется и в `_bookable_qs`, и в `_matched_services` (`:1097`) — двойное применение подтверждено. Но `:1015-1016` и `:1054` теперь **прямо объясняют это как инвариант корректности**: «shared … so the two can never drift apart» | Из «лишней работы, которую надо убрать» посылка превратилась в «осознанную цену за то, что фильтр и резолвер не могут разойтись». Оптимизация здесь ломает то, что комментарий защищает — задачу надо переформулировать в «замерить цену», а не «убрать дубль» |
| DRF-966 | Architectural note: discovery-слой marketplace зависит от booking-флага `BOOKING_VIA_AYLA_REST` | **ЖИВА** (как заметка) | `apps/marketplace/discovery.py:1144` — `if not bool(getattr(settings, "BOOKING_VIA_AYLA_REST", False))`; `:1127` объясняет почему. Флаг объявлен в `config/settings/base.py:707`, дефолт `false` | Это не дефект, а зафиксированная связь. Замерять тут нечего сверх того, что она на месте |
| DRF-990 | Сырые callback-payload'ы (`cb:anketa:*` и др.) сохраняются как user-сообщения и загрязняют LLM-контекст | **СМЕСТИЛАСЬ** | `apps/channels/max/handler.py:1099-1105` — inbound-ход **не persist'ится**, если это booking-callback, catalog-callback или clarify-redraw; комментарий `:1082-1083` прямо называет DRF-988 («raw `cb:clarify:…` reaching the concierge is precisely the DRF-988 defect»). `cb:anketa:*` обработан отдельно — `:1488` (DRF-1268, структурированные nutrition-ходы) | Названные в тикете случаи закрыты последующими тикетами. **Остаток, который я не замерил:** покрыты ли подавлением ВСЕ префиксы `cb:*` — списки `BOOKING_CALLBACK_PREFIXES`/`CATALOG_CALLBACK_PREFIXES` я не разворачивал. Задача сжимается до «перечислить префиксы и убедиться, что список полон» |
| DRF-1019 | Ветка `get_services(specialist_id=...)` не проверялась против живого backend | **НЕ ЗАМЕРЯЕТСЯ ЧТЕНИЕМ КОДА** | Посылка по конструкции про живой прогон, а не про код: тикет сам говорит, что ветка покрыта контрактными тестами на моках и на пути Ayla её никто не вызывает. Чтение репозитория не может ни подтвердить, ни опровергнуть «не прогонялась против живого backend» | — |

---

## Инфраструктура, деплой, CI

| задача | посылка одной строкой | вердикт | чем замерено | что на самом деле (если сместилась) |
|---|---|---|---|---|
| DRF-1208 | Runbook канареечного вывода ссылается на метрику, которой не существует | **ЖИВА** | `docs/runbooks/canary-ramp.md:84` требует `pipeline.turn` span p95; `apps/orchestrator/pipeline.py:158` этот спан создаёт, но вызовы `pipeline.turn` — только из тестов/replay/докстрингов, ни одного из `apps/channels/max/handler.py`. Реальная прод-метрика хода — `AIRequestMetric.latency_total_ms` | — |
| DRF-1246 | `environment:` в compose молча перебивает `env_file` — правка `.env.staging` не действует | **ЖИВА** | `docker-compose.yml:103-107,152-156,191-194` задают `ORCHESTRATOR_SHADOW_*` через `${VAR:-default}` из project `.env`; `docker-compose.staging.yml:43-121` переопределяет `environment: *staging_app_env` (без этих ключей) + `env_file: .env.staging` | Пересечение реально есть сейчас для `ORCHESTRATOR_SHADOW_ENABLED`, `_SAMPLE_RATE`, `_SURFACES`, `_MAX_BACKLOG`, `_TIMEOUT_MS` — у `web` и `worker`. Дополнительно: `shadow-worker` в staging.yml не переопределён вовсе, у него нет ни `env_file: .env.staging`, ни staging-профиля — **дефект шире, чем описано в задаче** |
| DRF-1192 | Сертификат не мог продлиться: ACME-проверка перехватывалась редиректом | **СМЕСТИЛАСЬ** | `infra/nginx/ai-bot-platform-api.conf.template:17-19`, `infra/nginx/miniapp.conf.template:14-16` | Инцидент устранён **только на живом хосте**. В репозитории шаблоны nginx **по-прежнему без исключения `/.well-known/acme-challenge/`** — `return 301` безусловный. При пересоздании хоста из шаблона баг вернётся. Двух недостающих проверок (срок сертификата, внешняя https-доступность) в репозитории нет нигде |
| DRF-1193 | Фронт не собирается при выкладке — интерфейс у людей отставал на 12 дней | **СМЕСТИЛАСЬ** | `.github/workflows/deploy-dev.yml` собирает только `web worker shadow-worker`, Mini App не упомянут; появился `.github/workflows/miniapp-drift.yml` (DRF-1257) | Появилась **детекция** дрейфа (триггер на `push` в `apps/miniapp/**` + `schedule` + сверка сорсмапов) — молчаливый дрейф стал громким. Но обязательным шагом деплоя сборка фронта **не стала**; пункт «что стоит сделать сверх записи» не выполнен |
| DRF-1194 | Команда подписки вебхука не знает про реестр ботов — второй бот подписан вручную | **ПРОТУХЛА** | `apps/channels/management/commands/max_subscribe_webhook.py` — флаг `--bot <slug>` реализован, резолвит токен и секрет из `apps.channels.bot_registry.effective_registry()`, дефолтное поведение без `--bot` сохранено; докстринг ссылается на DRF-1092 | Сделано другим тикетом (DRF-1092) |
| DRF-1154 | Выкладка отчитывается успешной, когда её не было — три признака слепы к одному отказу | **НЕ ЗАМЕРЯЕТСЯ ЧТЕНИЕМ КОДА** | `docker-compose.staging.local.yml` не найден в `git ls-tree origin/dev` и не упомянут в `.gitignore` — хост-специфичный некоммиченный файл. Монитор пилота (`healthz=200`, `StartedAt`) не найден трекнутым ни в одном репозитории | Пункт 4 замерить удалось: других вариантов имени compose-файла в репо нет. Пункты 1-3 живут вне git |
| DRF-1026 | Healthcheck контейнера не переживёт включение `SECURE_SSL_REDIRECT` | **ЖИВА** (вплоть до строки) | `docker-compose.staging.yml:71` — `test: ["CMD","curl","-f","http://localhost:8000/healthz/"]` без заголовка; `config/settings/base.py:104-109` — комментарий прямым текстом подтверждает, что `SECURE_SSL_REDIRECT` намеренно не подключён к env именно поэтому | — |
| DRF-1027 | Docs-skip прогоны CI выглядят красными в `gh pr checks` | **ЖИВА** | `.github/workflows/ci-docs-skip.yml` — текст аннотации совпадает 1:1; самоотмена через `gh run cancel "${{ github.run_id }}"` даёт статус `cancelled`, красный в `gh pr checks` | — |
| DRF-1047 | Рецепт деплоя в шапке `docker-compose.staging.yml` неполон — стоил 5 минут простоя пилота | **ЖИВА** | `docker-compose.staging.yml:1-5` — комментарий-рецепт по-прежнему называет два файла `-f` из трёх нужных | Компенсирующий контроль (`WINDOW_PROTOCOL.md` §5.1.1) существует, но в докс-репо, не в самом файле |
| DRF-979 | GitHub PAT в открытом виде в `.git/config` на хосте | **НЕ ЗАМЕРЯЕТСЯ ЧТЕНИЕМ КОДА** | Проверены `.git/config` локальных чекаутов на этой машине — во всех remote url без встроенного токена | Задача про **отдельный Linux-хост пилота** и путь `/home/taximeter/beautygo/dev/`. Локальная проверка ничего не говорит о состоянии удалённого сервера. Нужен SSH-доступ на чтение |
| DRF-1000 | Ayla booking client синхронный: ретрай 429 через `time.sleep` блокирует однопоточный consumer | **ЖИВА** (построчно) | `apps/integrations/ayla/booking_client.py:768` — `time.sleep(wait)`; константы `RATE_LIMIT_MAX_RETRIES=2` (`:67`), `RATE_LIMIT_MAX_WAIT_S=1.5` (`:69`) совпадают с текстом. `apps/workers/consumer.py:165` — `handler(decoded)` синхронно инлайн в единственном цикле | — |
| DRF-1084 | `monitor_pel` знает только поток `ingress:max` — с третьим потоком нужен явный флаг | **ЖИВА** (вплоть до строки) | `apps/workers/management/commands/monitor_pel.py:76` — `_DEFAULT_STREAM = "ingress:max"`, задаётся флагом `--stream` (не списком, без auto-discovery); `apps/channels/bot_registry.py:325` подтверждает наличие как минимум `max_global` помимо `ingress:max` | — |

---

## Тесты и CI

**Этот кластер замерен прогонами, а не чтением** — отдельный worktree на `origin/dev` (c3ae663) в скрэтчпаде, Postgres 16 и Redis 7 в изолированных контейнерах, своя база `test_prem_b4` на нестандартных портах. Основные чекауты не трогались, worktree убран.

Замер прогоном оказался важен: **по одной задаче он опроверг мой собственный вердикт, поставленный по чтению.** См. DRF-1044 ниже — я прочитал комментарий в `ci.yml`, комментарий утверждал, что дефект жив, и сам комментарий оказался протухшим. Это ровно тот механизм, против которого написан весь отчёт, — и я в него попал.

| задача | посылка одной строкой | вердикт | чем замерено | что на самом деле (если сместилась) |
|---|---|---|---|---|
| DRF-1044 | Три предсуществующих падения в `apps/miniapp_api` на `origin/dev` | **ПРОТУХЛА** | `pytest apps/miniapp_api/tests/test_c7_payments.py apps/miniapp_api/tests/test_create_booking_ayla.py -v` на origin/dev → **«41 passed»**; три точных node ID из `ci.yml` отдельно → **«3 passed in 22.68s»**. Починено коммитом `9a71ba5` (#1206, «K-6 unrot three tests») 2026-08-18 | **Мой первый вердикт был «ЖИВА» — по комментарию `ci.yml:571-572`, который до сих пор гласит «3 pre-existing miniapp_api failures». Комментарий устарел, а `--deselect` на `:608-610` стали мёртвым кодом.** Прогон показал обратное |
| DRF-1217 | `tests/e2e` не гоняется в CI и содержит 4 предсуществующих падения | **СМЕСТИЛАСЬ** (обе половины) | `pytest tests/e2e/ -v` с Redis → **«3 failed, 24 passed, 9 skipped in 35.56s»**. С 2026-08-22 (`44aeb9d`, DRF-1253) `tests/e2e` гоняется в CI как часть шага «pytest tests/ (cross-cutting)» | Падений **три**, не четыре, в трёх файлах с тремя разными первопричинами: `test_max_echo` и `test_handoff_skill:197` ассертят verbatim-эхо, отменённое DRF-963 (в `test_handoff_skill` падает только последняя строка — сам handoff-флоу проходит); `test_privacy_skill` ассертит hard-delete по чату, намеренно заменённый редиректом в Mini App (DRF-956 / T-05). Все три — правки на стороне тестов. `ci.yml:465-471` это уже документирует специально для того, кто возьмёт задачу, — а тикет не поправили |
| DRF-1131 | Тест master DM падает на Postgres: `integer out of range` — фикстура или тип поля | **ПРОТУХЛА** | `pytest apps/skills/payment_failed/tests/test_skill.py -v` на Postgres → **«14 passed»**; целевой тест с `PYTHONHASHSEED=0..4` → **5/5 passed**. Фикстура уже `_stable_external_id` (SHA-256 в signed-32-bit), `test_skill.py:127-144` | Починено коммитом `229d06d` (#1189) **2026-08-16 — за два дня до того, как тикет был заведён (2026-08-18)**. Заодно снят вопрос из формулировки «фикстура или тип поля»: фикстура. И это тот же дефект, что DRF-1158 |
| DRF-1143 | Две записи BASELINE линтера G9 (`dashboard.py`, `schedule.py`) устарели — правило упадёт на них | **ПРОТУХЛА** | `pytest tests/tools/test_import_boundaries.py -k baseline_matches_reality` → **«4 passed»**; `python tools/lint/import_boundaries.py apps/` (точная команда CI) → **exit 0**. `tools/lint/import_boundaries.py:529-533` подтверждает удаление | Записи удалены коммитом `229d06d` (#1189) 2026-08-16 |
| DRF-1191 | Тест подделки токена портит `token[-3]`, а проверяет `token[-1]` — флак ~1/64 | **ПРОТУХЛА** | `test_auth.py:65-84` — уже `token[-3] != "0"` плюс добавленный `assert tampered != token`; **10/10 прогонов зелёные**. Починено `ff66a97` (#1284, DRF-1379) 2026-08-25 | — |
| DRF-1230 | Двойной `-q` в pytest съедает итоговую строку — провал выглядит как успех | **ЖИВА** (доказано эмпирически) | Прогон без явного `-q` (только `addopts`) печатает «4 passed in 23.79s»; тот же прогон с явным `-q` (итого `-qq`) **итоговой строки не печатает вовсе**. `pyproject.toml:217`, `ci.yml:607` — тот самый шаг, который сам `ci.yml:441-445` называет проблемным | — |
| DRF-1137 | `test_discovery_live_regression` покрывает мёртвый код — зелёный файл без покрытия | **ЖИВА** | `discovery.generate_discovery_reply` вызывается только из тестов и комментариев; `concierge.py:5` в докстринге прямо пишет «instead of `discovery.generate_discovery_reply`». На SQLite тест **скипается** (сам требует Postgres), на Postgres → «6 passed in 34.78s» — зелёный тест, не покрывающий живой путь | — |
| DRF-1218 | Тест обхода лимитера флакует на границе минутного окна | **ЖИВА** (воспроизведено детерминированно) | Обычные прогоны флак ловят редко, поэтому механизм воспроизведён точно: `django_ratelimit/core.py::_get_window` даёт жёсткое окно с джиттером, `freezegun` в тесте отсутствует. Заморозка времени ровно на джиттер-границе (jitter=32 для 127.0.0.1) между 2-м и 3-м запросом → **третий вернул 500 вместо 429, лимитер обойдён** | — |
| DRF-999 | Флаки-тест `test_distinct_ips_each_get_one_audit` случайно краснит CI | **ЖИВА** (воспроизведено детерминированно) | 10 обычных прогонов — 10/10 зелёные (флак редкий). Тот же механизм: заморозка на джиттер-границе (jitter=47 для 10.0.0.1) между 1-м и 2-м запросом одного IP → **второй вернул 500 вместо 429, audit rows = 0 вместо 1** | — |
| DRF-1028 | Контрактный тест с зашитой датой ломается со временем | **СМЕСТИЛАСЬ** | `pytest tests/contracts/test_event_idempotency.py::…test_booking_created_idempotent -v` на Postgres → **FAILED, «assert 0 == 2»**, в логе явно `skip_backdated_reminder … scheduled_at=2026-05-21 now=2026-08-29` | Две поправки. (1) `tests/contracts` **гоняется** в CI с 2026-08-22 (DRF-1253), тест явно задеселектен. (2) **Собственный диагноз `ci.yml` (причина «d») выглядит неверным**: он объясняет падение отсутствием allowlist, но прогон показывает, что `EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN` это успешно обходит — реальная причина именно протухшая дата |
| DRF-1134 | Полный прогон CI занимает 30 минут — рассмотреть параллелизацию вместо роста таймаута | **СМЕСТИЛАСЬ** | `git show origin/dev:uv.lock` + grep `pytest-xdist` → ничего; `ci.yml` — таймаут поднимался **дважды после** заведения тикета: до 45 (DRF-1253), затем до 60 (`0838f6e`, DRF-1381) по измерению 60 реальных прогонов | Не 30 минут, а **медиана 42.5**, и 37 % прогонов упирались в прежний потолок 45. Тикет описывает и меньшую цифру, и уже пройденную развилку: «рост таймаута вместо параллелизации» случился дважды. Не изменилось ровно одно — **xdist по-прежнему отсутствует**, и это единственная живая часть задачи |

**Побочная находка, которой нет в Linear.** Тот же блок комментариев, `.github/workflows/ci.yml:573-588`, описывает **23 падения с одной первопричиной**, заведённых как deselect и явно помеченных «Candidate for Linear, not fixed here»:

> `BookingRequest.all_tenants.select_for_update().select_related("service")` в `apps/skills/booking/tools.py::execute_reschedule` — `service` nullable, `select_related` по nullable FK даёт LEFT OUTER JOIN, а Postgres отказывает: `FOR UPDATE cannot be applied to the nullable side of an outer join`. На SQLite невидимо.

И далее дословно: «Whether this fires in the live pilot depends on `BOOKING_VIA_AYLA_REST` (UNKNOWN, not read this session) — **if the flag is OFF there, every reschedule tap on that path 500s today**».

Это дефект **без тикета**, способный ронять перенос записи на живом пилоте. Срабатывает ли он — чтением кода не установить, нужно значение флага на пилоте. Стоит завести и проверить раньше большинства того, что в бэклоге уже лежит.

**Две правки в самом `ci.yml`, которые следуют из этих замеров** (обе — уборка, не поведение): снять три мёртвых `--deselect` для DRF-1044 (`:608-610`) и поправить диагноз причины «d», указывающий не на ту первопричину.

---

## Документы, канон, наблюдаемость живого пути

| задача | посылка одной строкой | вердикт | чем замерено | что на самом деле (если сместилась) |
|---|---|---|---|---|
| DRF-1075 | CLAUDE.md устарел в двух местах и вводит в заблуждение при планировании | **ЖИВА** | `djangoproject/CLAUDE.md:77-78` на `origin/dev` по-прежнему утверждает «Outbox worker не запущен» и «LocMemCache вместо django-redis». Факт: `djangoProject/settings/base.py:880,890` — `dispatch_outbox_events` и `publish_outbox_events_to_bot` в beat; `:998,1001` — `django_redis.cache.RedisCache`. `LocMemCache` остался только в `settings/test.py:49`, где он уместен | Мелкий дрейф в самом тикете: он ссылается на `config/settings/base.py`, а модуль называется `djangoProject/settings/base.py` |
| DRF-1195 | Двенадцать утверждённых решений владельца существуют вне git и вне журнала решений | **ЖИВА** (подтверждена дословно) | `ayla-knowledge/UX Agents/decisions/` — 12 файлов на диске (`BOT-001-*`, `BOT-003-*`, индексы); `git ls-tree -r origin/main \| grep '^UX Agents/'` → **0** (каталог не в .gitignore, просто не закоммичен). `00 Foundation/Canon Governance/OWNER_DECISION_REGISTER.md` существует, но пуст («Пуст при инициализации», 0 строк-записей); `BOT-001`/`BOT-003` в `02 Strategy/Ayla Decision Log.md` — 0 упоминаний | — |
| DRF-1251 | 5876 строк `globals.css` без границ между поверхностями | **ЖИВА** (стало хуже) | `apps/miniapp/src/styles/globals.css` на `origin/dev` — **6139 строк**, на 263 больше, чем в замере тикета | — |
| DRF-1107 | Навигация мастера: четыре вкладки вместо утверждённой IA «Сегодня \| Расписание \| Ayla» | **ЖИВА** | `apps/miniapp/src/components/MasterTabBar.tsx:106,112,119,126` — вкладки `Дом`, `Расписание`, `Диалоги`, `Профиль`. Утверждённая IA существует: `ayla-knowledge/07 UX/Ayla Master Schedule UX Contract.md` | — |
| DRF-1165 | «Все запросы рассмотрены» при нуле запросов за всё время | **ЖИВА** (строки сдвинулись) | `apps/miniapp/src/screens/admin/AdminTeamScreen.tsx:410-412` — условие и текст совпадают дословно; тикет цитирует `:391-393` | Утверждение «в `scheduling_schedulechangerequest` 0 строк» — данные, не проверялось |
| DRF-1159 | Линтер границ не видит гейт кадром выше — baseline читается как список живых дефектов | **ЖИВА** (частично закрыта) | `tools/lint/import_boundaries.py` существует; импорт `BookingRequest` в `apps/miniapp_api/views.py:393` на месте. Ограничение **уже записано в самом инструменте** — `import_boundaries.py:248` прямым текстом ссылается на DRF-1159 | Пункт «записать ограничение» выполнен внутри кода; пункт «пометить в baseline записи, чьи пути гейчены флагом» — нет |
| DRF-1212 | Шаг 18: replay-recorder зовёт только конвейер и оффлайн-раннер, живой путь не пишется | **СМЕСТИЛАСЬ** | `apps/channels/max/handler.py:519-550` — живой путь **уже зовёт** `apps.replay.recorder.capture`, докстринг прямо говорит «Until now… was called only by the DEPRECATED…»; есть тест `apps/channels/tests/test_live_path_replay_capture.py` | Код написан и влит. Осталось только фактическое значение флага `REPLAY_LIVE_CAPTURE_ENABLED` (дефолт в `config/settings/base.py:434` — `false`), а оно живёт в `/etc/ai-bot-platform/.env` на пилоте — **вне репозитория**. Задача из «написать код» превратилась в «проверить один флаг» |
| DRF-1213 | OTel root-span и step-события на живом пути отсутствуют | **ЖИВА** | `git grep -nE 'otel\|span\|tracer' origin/dev -- apps/channels/max/handler.py` → **пусто**. Конвейер размечен (`apps/observability/otel.py`, `apps/orchestrator/pipeline.py`), живой путь — нет | — |
| DRF-1214 | Шаг 10.5: confidence-floor на живом пути отсутствует | **СМЕСТИЛАСЬ** | `apps/channels/max/handler.py:650` — `_confidence_floor_reason` реализована, вызов на `:2117`, гейт `SKILL_CONFIDENCE_FLOOR_LIVE_ENABLED` | Как и DRF-1212: код на живом пути **есть**, дефолт флага — `false` (`config/settings/base.py:404-405`), фактическое значение на пилоте не в репозитории. «Самая дешёвая из шести» уже сделана |
| DRF-1215 | Шаг 5: живой путь собирает контекст памяти по кускам вместо единого снимка | **ЖИВА** | `git grep 'load_snapshot\|memory_snapshot' origin/dev -- apps/channels/max/handler.py` → пусто; `memory_snapshot` есть только в `apps/orchestrator/intent_router.py:99,109,157,166,176,236` (путь конвейера) | — |
| DRF-1197 | Свободный текст на первом контакте читается и отбрасывается (`welcome/skill.py:340`, `:355-363`) | **СМЕСТИЛАСЬ** | По указанным строкам сейчас константы (`RETURNING_TEXT`, `ASK_PROMPT`) — скилл переписан после подачи тикета. `apps/skills/welcome/skill.py:387-406` — комментарий, **исправленный тикетом DRF-1205**, прямо разбирает эту посылку: welcome зарегистрирован четырнадцатым, booking — двенадцатым, поэтому «хочу записаться на маникюр» уходит в booking, и welcome об этом ходе не спрашивают | Отбрасывается не всякий свободный текст, а только тот, который не забрал ни один скилл выше. Комментарий явно предупреждает: «исполнитель, который "починит" код под такой комментарий, соответствие уничтожит» |
| DRF-1198 | Current Focus не существует — фокус разговора переключается молча | **ЖИВА** | `git grep -rln 'current_focus\|CurrentFocus\|Current Focus' origin/dev -- '*.py'` → **0 совпадений** | — |
| DRF-900 | Валидатор знаний не исполняет `conditional_rules.domain_context` и не проверяет уникальность `adr_id` | **СМЕСТИЛАСЬ** | `ayla-knowledge/.knowledge/schema.yaml` — есть и `conditional_rules.domain_context`, и `uniqueness_rules` с `adr_id`. `scripts/validate_knowledge.py` — читает **только** `uniqueness_rules` (`check_uniqueness`, `:321`, со скоупом Active Canon через `_is_active_canonical`, `:311`); `conditional_rules` не упоминается нигде | **Половина сделана:** уникальность `adr_id` проверяется, и её скоуп («Active Canon») формализован. Не сделана только первая половина — исполнение `domain_context`. Зависимость DRF-899 (решение владельца) закрыта. Задачу надо ужать вдвое, а не брать целиком |
| DRF-1223 | Решение владельца: цена у салона или у пары мастер-услуга | **НЕ ЗАМЕРЯЕТСЯ ЧТЕНИЕМ КОДА** (решение владельца) | Фактическая половина посылки подтверждена: `apps/catalog/models.py` — `MasterService` не имеет поля цены; `CatalogService.price_from` (`:113`) единственное ценовое поле. «Цена у салона» действует умолчанием, а не решением | — |
| DRF-1136 | 20 записей в stash `ai-bot-platform` и 21 незакоммиченное изменение в чекауте `ai-bot-platform-codex` | **ПРОТУХЛА** | `git -C ai-bot-platform stash list` → **0 записей** (было 20). Каталог `ai-bot-platform-codex` на диске отсутствует | Обе половины посылки разрешились сами. **Оговорка:** тикет назвал только репозиторий бота; в `djangoproject` сейчас **5 записей в stash** — тот же класс, но другой репозиторий, и в тикете он не фигурирует. Если задачу закрывать, стоит завести отдельную |
| DRF-179 | В флоу отмены записи отсутствует refund-логика при P0-оплате | **ПРОТУХЛА** (кодовая часть) | `payments/services.py:284` — `refund_payment`; `appointments/domain/policies.py:42-254` — `get_refund_percent` с тремя политиками (клиентская шкала, полный возврат при отмене мастером); `appointments/application/services/cancel_reschedule_service.py:143,153` — вызов в самом флоу отмены; вебхук возврата покрыт тестом `payments/tests/test_payments_api.py:669` | Как задача на код — нечего делать. Если остаток в том, что не нарисован Figma-флоу, — это другой предмет, и формулировку надо менять |
| DRF-882 | Retire `mysite/maxbot/.FROZEN` policy | **СМЕСТИЛАСЬ** | Пути `mysite/maxbot/.FROZEN` в репозитории нет; маркер живёт как `legacy_maxbot/.FROZEN` и содержателен (заморозка от 2026-05-09 действует). `README.md:195` по-прежнему ссылается на старый путь в третьем репозитории (`formula_tela`) | Файл переименован, политика жива. Задача про снятие заморозки не выполнена, но адрес в ней неверен |
| DRF-869 | Prod `STRICT_TENANT_SCOPE=strict` env flip | **НЕ ЗАМЕРЯЕТСЯ ЧТЕНИЕМ КОДА** | `config/settings/base.py:246` — дефолт `"audit"`; `config/settings/staging.py:27` — жёстко `"strict"`; `.env.example:80` — `audit`. Прод-значение читается из `/etc/ai-bot-platform/.env` на хосте (сказано прямо в комментарии `base.py:319`) | — |
| DRF-628 / DRF-629 / DRF-630 | Track M: построить DRF ViewSets `/api/v1/catalog/{services\|masters\|faqs\|help-articles}/`, service-token auth в `mysite/core/auth/service_token.py`, composite-index миграцию и `test_catalog_api.py` | **СМЕСТИЛАСЬ** (все три) | Внутренний каталожный API **существует**, но по другому адресу и в другой форме: `services/internal_catalog_urls.py` монтирует `/api/v1/internal/catalog/{salon-services,specialist-services}/`; auth — `IsInternalBearer` (`users/permissions.py`), а не `service_token.py` (такого файла нет, как нет и каталога `mysite/` — проект называется `djangoProject`); контракт задокументирован в `docs/CATALOG_INTERNAL_API_CONTRACT.md`; тесты — `services/tests/test_internal_catalog_api_s3a.py`, `users/tests/test_internal_catalog_1016.py`, а не `test_catalog_api.py` | Работа сделана эпиком S3A (`#1044`/`#200`) под другими номерами. Ни один файл, названный в этих трёх тикетах, не существует |
| DRF-667 | C6 — `apps/catalog/admin.py` read-only + force-resync action | **СМЕСТИЛАСЬ** | `apps/catalog/admin.py:6` — «controlled mutation (force resync) lands in **C6 (DRF-576)**» | Тот же C6 заведён под номером **DRF-576**. Это дубль, а не отдельная работа |

---

## Что замерено классом, а не поштучно

332 задачи из 421 не измерялись по одной. Не потому, что «руки не дошли», а потому что у класса общая посылка, и она замеряется один раз.

### 1. `Duplicate`-состояние — 51 задача

Состояние `Duplicate` в Linear имеет тип `duplicate`, а не `canceled` — поэтому эти задачи **числятся открытыми** и попадают в любой обзор бэклога.

Все проверенные из них — тикеты треков Sprint 7 (F/K/G/L/C/O), чьи артефакты **существуют на `origin/dev`**: `apps/kb/{models,chromadb_client,tasks,admin}.py`, `apps/kb/services/{chunker,ingester,retriever}.py`, `apps/catalog/{models,tasks,admin}.py`, `apps/catalog/services/{sync,upserter,http_client}.py`, `apps/skills/faq/skill.py`, `apps/audit/models.py`, `tests/e2e/test_faq_e2e.py`.

Два из проверенных файлов не нашлись по указанному в тикете пути и обнаружились переименованными: `apps/tools/search_knowledge_base.py` → живёт как инструмент, зовётся из `apps/skills/faq/skill.py` и покрыт `test_search_knowledge_base_cache.py`; `apps/orchestrator/llm/router.py` → слой провайдеров переехал в `apps/llm/providers/{openai,anthropic}_provider.py`.

**Вердикт класса: ПРОТУХЛА.** Работа сделана, задачи остались открытыми из-за состояния, которое Linear не считает закрытым.

### 2. Тикеты треков Phase 0, DRF-300…899 — 105 задач

Тот же класс, что и `Duplicate`, но в состоянии `Backlog`/`Todo`. Здесь я не полагался на общее впечатление, а взял случайную выборку и замерил её — потому что «наверное, всё сделано» это ровно та посылка, ради проверки которых затевался отчёт.

Выборка из 5 (плюс 3 проверенных ранее):

| задача | что заявлено | что на `origin/dev` | вердикт |
|---|---|---|---|
| DRF-646 | `[EPIC]` Track K — KB/RAG: models + chromadb + chunker + retriever + ingester | `apps/kb/` укомплектован: `models.py`, `chromadb_client.py`, `services/{chunker,ingester,retriever}.py`, `admin.py`, `tasks.py` | **ПРОТУХЛА** |
| DRF-321 | `[M2-5]` Confidence scoring + low-confidence routing | `apps/orchestrator/pipeline.py:180-205` — `_confidence_floor_reason` с порогом и диагностическим слагом | **ПРОТУХЛА** |
| DRF-343 | `[M6-5]` Periodic re-sync scheduler (Celery beat) | `config/settings/base.py:1167-1173` — beat-запись `catalog_sync_every_15min` → `apps.catalog.tasks.sync_catalog_for_all_tenants` (`apps/catalog/tasks.py:50`) | **ПРОТУХЛА** |
| DRF-363 | `[Q-3]` Routing layer в `on_free_text` — fast-lane → classifier → specialized | `on_free_text` существует только в `legacy_maxbot/handlers/ai_assistant.py:87` — замороженном модуле. `docs/architecture/e0-1-ai-llm-cluster-migration-coverage.md:172` фиксирует замену на `pipeline.py::turn` как **PARTIAL** и перечисляет потерянные боковые ветки | **СМЕСТИЛАСЬ** — правка адресована в замороженный модуль; работа переехала в конвейер, но не целиком |
| DRF-855 | `[Phase 1 / PI5]` Load testing — Locust или k6 | Ни одного файла с `locust`/`k6` в дереве | **ЖИВА** |
| DRF-632 | 8 новых replay-фикстур FAQ (5 golden + 3 adversarial) | `apps/replay/fixtures/golden/faq/` — **16** файлов | **ПРОТУХЛА** |
| DRF-633 | Обновить `tests/integration/test_pipeline_turn.py` под реальный FAQ-скилл | Файл существует | **ПРОТУХЛА** (требует сверки содержимого) |
| DRF-637 | Health check FAQ-скилла и KB-ретривера в `/readyz/` | `apps/orchestrator/health.py` существует и агрегирует пробы | **ПРОТУХЛА** (требует сверки содержимого) |

Обратите внимание на два переименования, каждое из которых в одиночку способно дать ложное «ЖИВА»: задача просит `sync_catalog_all_tenants`, а в коде `sync_catalog_for_all_tenants`; задача просит `apps/tools/search_knowledge_base.py`, а инструмент живёт под другим путём и зовётся из `apps/skills/faq/skill.py`.

**Вердикт класса: смешанный, поштучная сверка обязательна.** В выборке 5-6 из 8 протухли, но `DRF-855` жива целиком, а `DRF-363` адресована в замороженный модуль — это разные виды бесполезности, и обращаться с ними надо по-разному. Общее правило для класса: **прежде чем брать, проверь существование названного файла на `origin/dev` и поищи его под соседним именем.**

### 3. Задачи до DRF-300 (март-апрель, до пивота) — 86 задач

Написаны под BeautyGO/двухприложенческую архитектуру до пивота на Ayla. Четыре из них начинаются с `[CANCELLED]` прямо в заголовке (`DRF-44`, `DRF-54`, `DRF-126`, `DRF-164`) — и всё равно числятся открытыми в состоянии `Backlog`. Остальные описывают экраны и эндпоинты продукта, которого уже нет.

**Вердикт класса: ПРОТУХЛА по конструкции.** Это не бэклог, а археология.

### 4. Design/Figma — 20 задач

Предмет — макет в Figma. Ни один репозиторий его не содержит.
**Вердикт класса: НЕ ЗАМЕРЯЕТСЯ ЧТЕНИЕМ КОДА.**

### 5. Кластер `[UX AUDIT]` — 7 задач в `In Progress` с апреля 2026

`DRF-175`, `176`, `177`, `180`, `181`, `182`, `183`. Предмет — user flow как документ. В `ayla-knowledge/07 UX/` на `origin/main` лежат только мастерские контракты (`Ayla Master Schedule UX Contract.md` и соседние) — клиентских флоу оплаты, памяти и push там нет.

**Вердикт класса: НЕ ЗАМЕРЯЕТСЯ ЧТЕНИЕМ КОДА** (артефакт вне git), с оговоркой: в каноне их действительно нет. **Отдельно: `DRF-179` из этого кластера замерена поштучно и протухла** — refund-логика в коде есть целиком.

Четыре месяца в `In Progress` — сам по себе сигнал: либо работа встала, либо статус не отражает действительность.

### 6. Sprint-10 canary cutover — 13 задач (`DRF-861`, `868`-`877`, `882`, `883`, `891`)

План раскатки MAX-трафика 5 % → 100 % от мая 2026. Runbook (`docs/runbooks/canary-ramp.md`) существует и помечен `Status: complete`, `README.md:138-195` описывает двухуровневый деплой-флоу как действующий.

Но **фактический процент раскатки — состояние прода**, оно не читается из репозитория. И вопрос глубже технического: пилот сейчас идёт на `api-dev.gobeauty.site` в рамках `Controlled Pilot`, а не как MAX-канарейка `formula_tela`.

**Вердикт класса: НЕ ЗАМЕРЯЕТСЯ ЧТЕНИЕМ КОДА + требует решения владельца о том, актуален ли план целиком.**

### 7. Кластер магазина proff58.ru — см. следующий раздел

---

## Давность в Linear ≠ отсутствие работы

Отбор «старше 7 дней» шёл по `updatedAt`. Проверка по веткам показала, что это ненадёжный признак заброшенности: у части «протухших» задач есть **живые локальные ветки**, часть — прямо на кончике `origin/dev`, то есть созданы недавно.

| задача | `updatedAt` в Linear | ветка |
|---|---|---|
| DRF-990 | 2026-08-10 | `fix/drf990-anketa-history` (на `c3ae663` = кончик `origin/dev`) |
| DRF-1019 | 2026-08-12 | `fix/drf1019-specialist-services` (на кончике `origin/dev`) |
| DRF-1064 | 2026-08-15 | `feat/drf1064-salon-ops` (в `djangoproject`) |
| DRF-1129 | — | `fix/drf1129-bookingrequest-readers` (на кончике `origin/dev`) |
| DRF-968 | — | `fix/drf968-service-handoff-continuation` |
| DRF-1048 | — | `fix/drf1048-visit-auto-completion` |

Последние три — ровно те задачи, на которых выводилось правило про протухшие посылки. Работа по ним идёт, а тикеты выглядят покинутыми.

**Практический вывод:** прежде чем считать задачу заброшенной по дате в Linear, посмотри `git worktree list` и ветки в обоих репозиториях. Дата обновления тикета не связана с тем, пишет ли кто-то по нему код прямо сейчас, — а взяться за уже занятую задачу дороже, чем взяться за протухшую.

---

## Ловушка одноимённых репозиториев

Отдельным пунктом, потому что на ней сломался один из замеров и сломается следующий исполнитель.

**В команде DRF живут два разных продукта.** Кроме Ayla, в бэклоге есть кластер магазина инструмента **proff58.ru**: `DRF-951` (checkout), `DRF-952` (онлайн-оплата и 30-минутный резерв), `DRF-953` (релиз на proff58.ru), `DRF-991`/`996` (PLP, тип инструмента), `DRF-1008` (кабинет YooKassa для ИП), `DRF-1009`-`1014` (страницы «О компании», «Оплата и доставка», «Гарантийный ремонт»), `DRF-1166` (P0, фильтр на странице поиска), `DRF-1168`-`1173` (дизайн информационных страниц), `DRF-1177` (способ оплаты в админке заказов).

**Кодовой базы этого магазина на машине нет.** Единственный кандидат — `itsolve` (GitLab, `origin/main`) — оказался статическим лендингом (`index.html`, `styles.css`, `script.js`), последний коммит 2026-07-23, ветки `dev` нет.

**Ловушка:** в `ai-bot-platform` есть **одноимённый** `apps/orders` с моделями `Order`/`PaymentEvent` и интеграцией YooKassa. Он ретирнут (PR #739, миграция `apps/orders/migrations/0002_drop_orders_tables.py` дропает таблицы, runbook `orders-yookassa-retirement-deploy.md` — `Status: complete`). Замер `DRF-952`/`DRF-1177` по нему даёт уверенное и **неверное** «ПРОТУХЛА»: `OrderAdmin` действительно не существует — но в другом продукте.

**Практический вывод:** прежде чем мерить посылку задачи, установи, о каком продукте она. Признак proff58: слова «заказ», «корзина», «товар», «доставка», `apps/orders`. Признак Ayla: «запись», «визит», «мастер», «салон».

---

## Задачи, которые можно закрыть прямо сейчас как неактуальные

Закрывать не надо — Linear меняет только главное окно. Ниже довод по каждой.

**Поштучно замеренные:**

1. **DRF-1125** — фикс уже в коде. `payments/tests/test_table_rename_migration.py` содержит `autouse`-фикстуру `_restore_payments_schema`, восстанавливающую схему `payments` до leaf-миграций после каждого теста; её докстринг прямо описывает инцидент PR #227 как прошедший. Чинить нечего.
2. **DRF-1158 и DRF-1131 — вместе, это один дефект под двумя номерами.** Фикс в коде, канонизирован и **подтверждён прогоном**: `pytest apps/skills/payment_failed/tests/test_skill.py` → 14 passed, целевой тест с `PYTHONHASHSEED=0..4` → 5/5. Фикстура — `_stable_external_id` (SHA-256), сырого `hash()` нет; `tools/lint/import_boundaries.py:1182` называет хелпер образцом. Отдельно стоит заметить: починка (`229d06d`, 2026-08-16) опередила заведение DRF-1131 (2026-08-18) на два дня — тикет родился уже протухшим.
3. **DRF-1044** — **подтверждено прогоном**, а не чтением: `pytest` по трём точным node ID из `ci.yml` → «3 passed», по обоим файлам целиком → «41 passed». Починено `9a71ba5` (#1206) 2026-08-18. Закрывая, снимите заодно три мёртвых `--deselect` в `ci.yml:608-610` и поправьте комментарий `:571-572` — иначе следующий читатель поверит ему, как поверил я.
4. **DRF-1143** — записи BASELINE уже удалены (`229d06d`, #1189, 2026-08-16). Точная команда CI `python tools/lint/import_boundaries.py apps/` → exit 0; `test_import_boundaries.py -k baseline_matches_reality` → 4 passed.
5. **DRF-1191** — починено `ff66a97` (#1284, DRF-1379) 2026-08-25: тест теперь портит и проверяет один и тот же символ, плюс добавлен `assert tampered != token`. 10/10 прогонов зелёные.
6. **DRF-1194** — сделано тикетом DRF-1092. `max_subscribe_webhook.py` имеет флаг `--bot <slug>`, резолвит токен и секрет из `effective_registry()`, дефолт сохранён. Требование задачи выполнено буквально.
7. **DRF-1136** — посылка разрешилась сама. `git stash list` в `ai-bot-platform` пуст (было 20), чекаут `ai-bot-platform-codex` с диска удалён. Оба предмета решения исчезли.
8. **DRF-179** — как задача на код мертва. Refund-политика реализована целиком: `payments/services.py:284`, три политики в `appointments/domain/policies.py`, вызов внутри флоу отмены (`cancel_reschedule_service.py:143,153`), вебхук возврата покрыт тестом. Если остаток — Figma-флоу, это надо переписать в другую задачу, а не оставлять как есть.
9. **DRF-667** — дубль. `apps/catalog/admin.py:6` прямым текстом называет ту же работу «C6 (DRF-576)». Два номера на один C6.
10. **DRF-628, DRF-629, DRF-630** — Track M сделан эпиком S3A под другими номерами. Ни один названный в них файл не существует: нет `mysite/core/auth/service_token.py`, нет каталога `mysite/`, нет `test_catalog_api.py`, нет пути `/api/v1/catalog/{services|masters|faqs|help-articles}/`. Есть `/api/v1/internal/catalog/{salon-services,specialist-services}/` с `IsInternalBearer` и контрактом `docs/CATALOG_INTERNAL_API_CONTRACT.md`. Закрывать как «сделано иначе», а не «сделано».

**Классом:**

11. **51 задача в состоянии `Duplicate`** — все проверенные ссылаются на код, который существует на `origin/dev`. Их «открытость» — артефакт того, что Linear не считает тип `duplicate` закрытым. Это самая дешёвая уборка в бэклоге: 51 задача одним движением, без единого замера сверх уже сделанных.
12. **Четыре задачи с `[CANCELLED]` в заголовке** — `DRF-44` («устарело при пивоте на Ayla»), `DRF-54` («не нужен в Ayla MVP»), `DRF-126`, `DRF-164` («дубль DRF-171»). Отменены явно, самим заголовком, довод отмены записан там же — и всё равно числятся в `Backlog`.
13. **86 задач до DRF-300** — написаны под BeautyGO до пивота на Ayla; описывают экраны и эндпоинты продукта, которого нет. Закрывать пачкой как «pre-pivot», а не разбирать поштучно.

**Отдельно — не закрывать, но переписать до того, как по ним начнут действовать:**

- **DRF-1064** — раздел «Технические ограничения (VERIFIED)» устарел целиком: все три блокера устранены коммитами того же дня. Читатель, доверившийся документу, построит заново `appointments/authz.py`, `completion.py` и `auto_complete_elapsed_bookings`. Открытый остаток реален (слой «Ayla спрашивает клиента»), но описан не тем текстом.
- **DRF-1024** — техническая посылка неверна: `ALLOWED_HOSTS=['*']` захардкожено в `settings/dev.py:34`, а не приезжает из `.env.staging`. Предложенное действие не даст эффекта.
- **DRF-1122** — первая половина верна (0 вызывающих `has_capability`), следствие неверно (receptionist достижим и гейтится корректно вручную). Правка вчетверо уже, чем описано.
- **DRF-900** — половина сделана (уникальность `adr_id` проверяется, скоуп формализован), зависимость DRF-899 закрыта. Осталось `conditional_rules.domain_context`.
- **DRF-1011** — HEALTH уже проверяется fail-closed; переформулировать в «негде выдать консент».
- **DRF-1006** — условие снятия наступило, но allowlist оставлен намеренно как fallback. Это уже решение, а не забытая уборка.
- **DRF-1217** — обе цифры в тикете неверны: `tests/e2e` **гоняется** в CI, падений **три в трёх файлах с тремя разными первопричинами**, а не четыре. Причём CI уже написал это в комментарии специально для того, кто возьмёт задачу (`ci.yml:468-471`) — а тикет не поправили.
- **DRF-1053** — переписать так, чтобы было видно: тай-брейк добавлен в сортировку, но не в курсор. Иначе следующий читатель закроет задачу как сделанную.
- **DRF-965** — двойное применение `_service_match_q` теперь объявлено инвариантом корректности в комментарии (`discovery.py:1015-1016,1054`). Задача из «убрать дубль» превращается в «замерить цену и решить, платим ли».
- **DRF-1017** — контракт написан, но лежит в бэкенд-репозитории. Задача сжимается до перекрёстной ссылки.
- **DRF-1028** — задача останется живой (тест правда падает на протухшей дате), но два утверждения вокруг неё неверны: `tests/contracts` **гоняется** в CI с 2026-08-22, а собственный диагноз `ci.yml` (причина «d») называет не ту первопричину. Чинить надо дату, а не allowlist.
- **DRF-1134** — цифра устарела вдвойне: не 30 минут, а медиана 42.5, и таймаут с тех пор поднимали **дважды** (до 45, затем до 60). Живой остаток ровно один — `pytest-xdist` так и не внедрён. Переписать в «внедрить параллелизацию», сняв ссылки на пройденную развилку.

---

## Где цена работы по протухшему описанию самая высокая

Порядок — по цене ошибки, не по приоритету в Linear.

1. **DRF-952 — биллинг на живых деньгах.** Если исполнитель реализует написанное буквально (sweep-джоба «отменить неоплаченный заказ через 30 минут»), она может отменять брони, у которых холд YooKassa ещё валиден (TTL до ~2 ч), — против реальных оплат живых клиентов. Плюс задача, скорее всего, вообще про другой продукт (см. «Ловушка одноимённых репозиториев»). Двойной риск: не тот код и не та логика.
2. **DRF-1064 и DRF-1117 — машина состояний визита.** Самый дорогой класс: правка машины состояний по устаревшему описанию либо дублирует существующую авторизацию салонного актора, либо ломает её. Оба тикета описывают состояние, исправленное 15.08 — на следующий день после того, как они были написаны.
3. **DRF-1222 — миграция против боевой базы пилота.** Уникальный констрейнт `CatalogMaster(tenant, ayla_user_id)` накатывается на базу, где уже есть салон с мастерами, связанными по этому полю. Тикет сам предупреждает «проверить дубли перед миграцией» — предупреждение в силе, и проверить это чтением кода нельзя.
4. **DRF-1010, DRF-1036, DRF-1038 — пути с персональными данными.** Все три живы. `DRF-1010` — ПДн уходят в LLM без токенизации на маршруте глобального консьержа; `DRF-1036` — знание UUID даёт доступ к профилю, экспорту и удалению ПДн; `DRF-1038` — право на забвение не обходит связанные прокси. Цена неверной посылки здесь — не потерянный час, а нарушение 152-ФЗ на пилоте с реальными пользователями.
5. **DRF-1132 — вебхук оплаты + тенантный middleware.** Заминировано на включение строгого режима: вебхук YooKassa получит 400 до входа во вью. Срабатывает не при правке, а при флипе флага — то есть тогда, когда никто не будет искать причину здесь.
6. **DRF-1142 — начисление баллов лояльности.** Мина: починка DRF-1108 молча выключит начисление. Класс «правка в одном месте ломает биллингоподобное поведение в другом».

---

## Чего этот метод не покрывает

Честно и по пунктам — чтобы «замерено» не прочиталось как «замерено всё».

**1. Фактические значения флагов на пилоте.** Читались дефолты в коде и значения в `settings/staging.py`, но реальные значения живут в `/etc/ai-bot-platform/.env` на хосте — репозиторий содержит только `.env.example` и `.env.staging.template`. Не замерены: `STRICT_TENANT_SCOPE`, `MULTI_TENANT_STRICT`, `REPLAY_LIVE_CAPTURE_ENABLED`, `SKILL_CONFIDENCE_FLOOR_LIVE_ENABLED`, `BOOKING_AUTO_COMPLETE_ENABLED`, переменные YooKassa. Для `DRF-1212` и `DRF-1214` это меняет вердикт целиком: код написан, вопрос свёлся к одному флагу.

**2. Всё, что видно только на живых данных.** Не проверялись: есть ли дубли `CatalogMaster` по `ayla_user_id` в боевой базе (`DRF-1222`); 0 ли строк в `scheduling_schedulechangerequest` (`DRF-1165`); есть ли пилотные пользователи с открытым HEALTH-консентом (`DRF-1011`); появлялся ли статус `completed` в базе (`DRF-1064`); 0 ли активных рёбер у названных услуг (`DRF-986`/`987`); срабатывает ли сегодня `pii_protected_provider.no_active_scope` в логах (`DRF-1010`).

**3. Удалённые хосты.** `DRF-979` (PAT в `.git/config` на `/home/taximeter/beautygo/dev/`) требует SSH на сервер пилота. Локальные чекауты чисты, но это ничего не говорит о том хосте. Туда же — nginx.conf и серверный healthcheck (`DRF-1024`), монитор выкладки (`DRF-1154`), реальная система алертинга по сертификатам (`DRF-1192`).

**4. Кодовая база магазина proff58.ru.** Её нет на машине. ~18 задач кластера не замерены вовсе — не «не успел», а «нечего читать».

**5. Артефакты вне git.** Figma-макеты (20 задач Design), user flow из кластера `[UX AUDIT]` (7 задач), рабочие каталоги окон. Замерить можно только косвенно — по отсутствию в каноне.

**6. Решения владельца.** `DRF-1223` (цена у салона или у пары мастер-услуга), `DRF-1189` (reconciliation канона по OD-1), безопасное умолчание в `DRF-1064`, актуальность плана Sprint 10 целиком. Код показывает, какое умолчание действует сегодня, но не отвечает, верное ли оно.

**7. Прогоны — только по одному кластеру.** Прогонами замерен кластер «тесты и CI» (11 задач): изолированные Postgres 16 и Redis 7, своя база `test_prem_b4`, отдельный worktree на `origin/dev`. Все остальные вердикты поставлены **чтением**, и один из них прогон уже опроверг (DRF-1044). Значит и среди непрогнанных возможны такие же — там, где я опирался на комментарий в коде, а не на исполнение.

Полный `apps/` (7246 тестов, ~40 минут) не гонялся — слишком дорого для разведки; проверялись целевые файлы. Флаки `DRF-999` и `DRF-1218` не пойманы на реальной границе минуты: вместо ожидания механизм воспроизведён детерминированно заморозкой времени на джиттер-границе `django_ratelimit`. Это доказательнее случайного ожидания, но это репродукция механизма, а не наблюдение флака в CI.

**8. ~324 задачи разобраны классом, а не поштучно.** Класс отвечает на вопрос «стоит ли брать эту пачку», но не заменяет замер конкретной задачи перед взятием. Выборка из пачки DRF-300…899 показала смесь (5-6 протухших из 8, но `DRF-855` жива целиком) — значит для этой пачки «протухла» презумпция, а не результат, и каждую надо сверять отдельно.

**9. Полнота там, где я остановился на первом подтверждении.** Например, в `DRF-990` я убедился, что booking-, catalog- и clarify-callback'и не persist'ятся, но не разворачивал списки `BOOKING_CALLBACK_PREFIXES` / `CATALOG_CALLBACK_PREFIXES` — то есть не проверил, что подавлением покрыты **все** префиксы `cb:*`. Вердикт «сместилась» там опирается на закрытые случаи, а не на доказанную полноту.
