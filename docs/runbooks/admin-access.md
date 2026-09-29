# Runbook: Admin access (Django admin on a deployed contour)

> Status: **draft**
> Last exercised: _never (written with DRF-1023; first exercise = DRF-1023 deploy)_
> Target completion sprint: Controlled Pilot
> Owner: Platform Lead

## Purpose

Get a working login to the Django admin (`/admin/`) on an HTTPS contour
(staging/pilot: `https://api-dev.gobeauty.site/admin/`) and create an
operator account — without ever putting a password into code, git,
reports, or logs.

## ⚠ Cross-tenant warning — read before handing out ANY account

**The whole admin is cross-tenant.** Admin classes deliberately use
``all_tenants`` querysets (`apps/handoff/admin.py`,
`apps/conversations/admin.py`, plus audit / booking / catalog / consent /
experiments). Any account with admin access sees the data of **every
salon** — and `MessageAdmin` shows and searches client message **text**.

Therefore:

- Accounts are issued to the **internal team only**. NEVER to salon
  staff — they would get every other salon's tasks and client dialogs.
- Tenant-restricted operator access is a separate task (**DRF-1022**,
  operator endpoint). Until it lands, this warning is also shown as a
  banner on the AdminTask / Conversation / Message changelists.

## Trigger / when to run

- First admin login on a fresh contour (user table is empty).
- «Ошибка проверки CSRF. Запрос отклонён» on the login form (the
  DRF-1023 symptom — means `DJANGO_CSRF_TRUSTED_ORIGINS` /
  `DJANGO_BEHIND_TLS_PROXY` are not set on the contour).
- Rotating or adding an internal operator account.

## Prerequisites

- SSH access to the contour host.
- The contour runs the docker compose project (`ayla-bot-staging` on the
  pilot) with a `web` service.
- Env vars (below) present in the contour's env file
  (`.env.staging` on the pilot) and the stack restarted after editing.

## Configuration (env vars, DRF-1023)

All four default to OFF / empty — behaviour unchanged for local dev and
CI. Set on HTTPS contours only:

| Var | Pilot value | Why |
|---|---|---|
| `DJANGO_CSRF_TRUSTED_ORIGINS` | `https://api-dev.gobeauty.site` | Django requires the POST `Origin` to be trusted for HTTPS requests; empty = every admin POST 403s. Strict parsing: a malformed value refuses to boot. |
| `DJANGO_BEHIND_TLS_PROXY` | `true` | nginx terminates TLS and sets `X-Forwarded-Proto`; this pins `SECURE_PROXY_SSL_HEADER` to that header so Django knows requests are HTTPS. |
| `DJANGO_SESSION_COOKIE_SECURE` | `true` | Session cookie only over HTTPS. |
| `DJANGO_CSRF_COOKIE_SECURE` | `true` | CSRF cookie only over HTTPS. |

Deliberately NOT set:

- `SECURE_SSL_REDIRECT` — nginx already 301s 80 → 443; a Django-level
  redirect would also 301 the container's own healthcheck
  (`docker-compose.staging.yml` curls `http://localhost:8000/healthz/`
  with no `X-Forwarded-Proto`), flipping the container unhealthy.
- `ALLOWED_HOSTS` tightening — the contour runs
  `DJANGO_ALLOWED_HOSTS=*`. Verified safe to tighten to
  `api-dev.gobeauty.site,localhost,127.0.0.1` (callers: nginx with
  `Host: api-dev.gobeauty.site`, container healthcheck with
  `Host: localhost`, host-side probes to `127.0.0.1:8014`) — but do it
  as a separate, deliberate config change with a healthcheck right
  after; rollback = restore the previous value.

## Step-by-step procedure

1. **Set the env vars** in the contour's env file (pilot:
   `.env.staging` next to the compose files), then
   `docker compose -p ayla-bot-staging up -d web` (and `worker` /
   `celery-worker` / `celery-beat` if the env file is shared — it is).
   Expected: containers restart cleanly; a malformed
   `DJANGO_CSRF_TRUSTED_ORIGINS` would fail the boot — check
   `docker compose -p ayla-bot-staging logs --tail=50 web`.
2. **Verify the login form**: open
   `https://api-dev.gobeauty.site/admin/` in a browser → HTTP 200, login
   form renders (not the CSRF error page).
3. **Create the operator account** — password is entered by the account
   owner at the prompt, never written anywhere. Django's built-in
   `createsuperuser --noinput` reads the credentials from the
   environment; pass them into the container with `-e` so they never
   land in the repo, the env file, or shell history beyond this command:

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

   Expected output: `Superuser created successfully.`
   If the username already exists the command fails with
   `CommandError: Error: That username is already taken.` — this is
   normal for re-runs; to change a password use
   `python manage.py changepassword <username>` (interactive) in the same
   container.
4. **Log in** at `https://api-dev.gobeauty.site/admin/` with the new
   account.

## Verification

- Login form → 200, login succeeds, admin index renders.
- The AdminTask changelist (`/admin/handoff/admintask/`) shows the
  yellow cross-tenant warning banner.
- `https://api-dev.gobeauty.site/healthz/` → 200 after the restart.
- Closing a task via the admin returns the conversation to the bot
  (DRF-980 service path — status RESOLVED/CANCELLED, conversation back
  to IDLE).

## Escalation contacts

| Severity | Who | How to reach |
|---|---|---|
| P0 | Platform Lead | per `on-call.md` |
| P1 | Release owner (главное окно) | file-bus REPORT/REPLY per `WINDOW_PROTOCOL.md` |

## Post-mortem template

Used after every non-trivial run.

- **What happened.**
- **What was the trigger.**
- **What did we expect — what actually happened.**
- **How long did it take to detect / mitigate / resolve.**
- **What we learned.**
- **Action items** (owner + deadline).


---

## Роли, выдача и отзыв (DRF-1495)

Раздел заменяет шаг 3 выше для всех, кроме первой записи владельца.
`createsuperuser` остаётся ровно для одного случая: завести первого
суперпользователя на пустом контуре. Все остальные записи заводятся в
роли — суперпользователей на контуре должно быть столько, сколько
человек имеет право менять права, то есть один.

### Две роли

| Роль | Группа | Что может |
|---|---|---|
| смотрящий | `ayla-viewer` | `view_*` на всё, что есть в админке: очередь handoff, каталог, журнал. Ни одной кнопки «сохранить». Переписка и профиль клиента — только по пропуску (см. «Доступ к переписке и профилю клиента»). |
| правящий | `ayla-editor` | всё, что видит смотрящий, плюс правку прикладных данных: каталог, брони, задачи handoff, база знаний, персона, промо, расписание. Пропуск к переписке нужен и ему. |

Ни одна роль не видит вовсе:

* `auth` — пользователей и группы. Правящий с `auth.change_user`
  дописал бы себя в суперпользователи за один запрос; вдобавок форма
  пользователя Django показывает хеш пароля.
* `django_celery_beat` — расписание воркеров это эксплуатация, а не
  данные. Экран сторонний и несёт действия (`run_tasks`, `enable_tasks`)
  без объявленных прав, а такие Django отдаёт всякому, кто открыл
  страницу. Чужой пакет мы не правим — закрыли экран целиком.

Ни одна роль не правит (видит, но не меняет):

* журналы — `audit`, `admin.logentry`, `adminconsole`, `events`,
  `eventbus`, `ingress`, `replay`, `consent`: след, который можно
  править, не след;
* `tenancy.Tenant` — там токен бота и вебхук-секрет;
* `conversations.Message` — переписка клиента;
* `promptreg` — системные промпты, пороги роутера и библиотека
  дисклеймеров: это конфигурация платформы и юридические тексты, а не
  прикладные данные салона.

Определения — `apps/adminconsole/roles.py`; они же и есть источник
правды, документация лишь пересказывает. Набор правимого закреплён
тестом `test_roles.py::test_editor_writable_set_is_pinned`: новый экран
в админке красит сборку, и тот, кто его завёл, решает осознанно — дать
правящему право или нет.

### Секреты в интерфейсе

`apps/adminconsole/secrets_policy.py` держит список «какие поля каких
моделей — секреты» и снимает их с формы у всех, кто не имеет права эту
модель править. Сегодня там токен бота и вебхук-секрет тенанта и
одноразовый `invite_token` мастера.

Это не украшение, а заплатка на настоящую дыру: Django рисует форму
изменения двумя способами, и при отсутствии права на правку он кладёт
все поля в read-only и печатает **значения из модели**, не спрашивая
виджет. То есть подмены поля на «звёздочки» недостаточно — read-only
отрисовка её обходит.

Новое поле-секрет добавляется одной строкой в `SECRET_FIELDS`.

### Admin-действия

Действие без `permissions=` Django отдаёт любому, кто открыл экран.
Поэтому каждое действие обязано объявить права; тест
`test_admin_actions.py::test_every_admin_action_declares_its_permissions`
проверяет это разом по всем экранам, которые видят роли.

> **Предупреждение про кросс-тенантность выше остаётся в силе для всего,
> кроме переписки и профиля клиента.** Роли делят *действия*, а не
> *салоны*: очередь обращений, каталог, расписание и журналы смотрящий
> по-прежнему видит по всем тенантам. Переписку и профиль с DRF-1514
> видно только по пропуску на конкретного клиента — см. следующий
> раздел. Полное разграничение по тенанту — отдельная задача (DRF-1022).

### Доступ к переписке и профилю клиента (DRF-1514)

Решение владельца от 05.09.2026: широкого доступа смотрящего больше нет.

* **Общего списка переписок всех салонов нет.**
  `/admin/conversations/message/`, `/admin/conversations/conversation/`,
  `/admin/identity/botuser/`, `/admin/identity/clientprofile/`,
  `/admin/consent/consentrecord/` без пропуска отвечают 403 со
  страницей-объяснением, а не пустым списком.
* **Пропуск выдаётся по обращению.** Сотрудник находит в
  `/admin/handoff/admintask/` задачу, с которой работает, открывает её
  карточку, по ссылке из отказа попадает на форму «Указать причину и
  открыть доступ» с уже подставленным обращением, пишет причину и
  возвращается на обращение. Клиент и салон берутся из обращения —
  выбрать их отдельно нельзя.
* **Причина обязательна и вводится до открытия.** Короче 12 символов —
  отказ формы.
* **Пропуск живёт час** (`ADMINCONSOLE_CLIENT_ACCESS_TTL_MINUTES`,
  минуты). Дальше — новый пропуск с новой причиной.
* **Очередь обращений остаётся открытой** — иначе работать нельзя. В
  списке только метаданные; переписка лежит в карточке, и карточка
  закрыта пропуском.
* **Журнал доступа** — `/admin/adminconsole/clientdataaccesslog/`: кто,
  когда, какой экран, какого клиента, с какой причиной, плюс каждый
  отказ. Это **не** журнал изменений: `/admin/admin/logentry/` отвечает
  на вопрос «кто что правил», а просмотр ничего не правит. Не-владелец
  видит в журнале доступа только свой след.
* **Владелец (суперпользователь) ходит как ходил.**

Не показывается никому, кроме владельца, ни при какой причине:

* телефон клиента (`identity.BotUser.phone`, DRF-1039);
* `identity.BotUser.context` — там настройки проактивных сообщений про
  питание, то есть данные о здоровье (152-ФЗ ст. 10);
* сырые полезные нагрузки: `ingress.WebhookJournal.raw_payload`,
  `events.Event.payload`, `eventbus.DomainEvent.data` / `metadata` /
  `actor`, `replay.ReplayTrace.pipeline_steps`. Сузить их пропуском
  нельзя — привязки к клиенту у строки нет. Списки этих экранов
  остаются открытыми: канал, идентификатор события, салон, `trace_id`,
  время — разбор инцидента идёт по ним.

### Завести роли на контуре

Идемпотентно, гоняется на каждый деплой. Учётных записей не создаёт.

```bash
docker compose -p ayla-bot-staging exec -T web   python manage.py sync_admin_roles
```

Ожидаемый вывод — две строки вида `ayla-viewer: N прав`. Команду надо
повторять после появления новых экранов в админке: права выставляются
`set()`, поэтому прогон и добавляет новое, и убирает исчезнувшее.

### Завести учётную запись

Записи заводятся **на человека**. Общие («salon», «ops», «team»)
команда отклоняет: журнал честно скажет, что правку сделал этот логин,
и не скажет, кто это был.

```bash
read -rsp "Пароль новой учётной записи: " AYLA_ADMIN_PASSWORD; echo
docker compose -p ayla-bot-staging exec -T   -e AYLA_ADMIN_PASSWORD="$AYLA_ADMIN_PASSWORD"   web python manage.py admin_account_grant     --username i.petrova --role viewer --actor "$USER"
unset AYLA_ADMIN_PASSWORD
```

Пароль не принимается аргументом намеренно: аргумент виден в `ps` и
остаётся в истории оболочки.

Без `AYLA_ADMIN_PASSWORD` запись заведётся с непригодным паролем —
войти нельзя, пока владелец не задаст его
`manage.py changepassword <username>`. Это рабочий путь, если пароль
задаёт сам человек, а не выдающий.

Повторный запуск с другой `--role` переводит запись в другую роль
(прежняя снимается, не накапливается).

### Отозвать доступ

```bash
docker compose -p ayla-bot-staging exec -T web   python manage.py admin_account_revoke --username i.petrova --actor "$USER"
```

Команда снимает `is_active` и `is_staff`, снимает роли и **удаляет живые
сессии этого человека**. Без последнего отзыв не отзывал бы: у уже
вошедшего в куке лежит валидный ключ сессии, и он работал бы до
истечения срока.

Строку пользователя команда не удаляет — на неё ссылается журнал, и
удаление превратило бы прошлые записи в «кто-то».

Суперпользователя команда трогать отказывается: иначе первый же отзыв
мог бы оставить контур без администратора. Понизить или отозвать
владельца — осознанная операция через shell.

### Журнал действий

Каждое добавление, изменение и удаление, сделанное руками через
`/admin/`, попадает в `apps.audit.AuditLog` действиями
`admin.object.created` / `admin.object.updated` / `admin.object.deleted`.
Групповые операции («удалить выбранные») дают строку на объект, а не
одну на всю пачку.
В payload — автор (имя и id), модель, id объекта и перечень имён
изменённых полей. Значений полей там нет.

Выдача и отзыв учётных записей пишутся туда же:
`admin.account.granted` / `admin.account.revoked`.

Где смотреть:

* `/admin/audit/auditlog/` — общий журнал платформы (туда же пишут
  сервисы), фильтры и поиск по payload;
* `/admin/admin/logentry/` — только действия через админку, короткий
  ответ на «что правили руками».

Оба экрана строго на чтение.

Срок хранения: `AuditLog` чистится задачей
`apps.audit.tasks.cleanup_old_audit_logs` по `AUDIT_LOG_RETENTION_DAYS`
(по умолчанию 90 дней). `admin.LogEntry` не чистится ничем — если разбор
уходит глубже трёх месяцев, смотреть надо там.

### Проверка после выдачи

1. Под записью-смотрящим: `/admin/handoff/admintask/` открывается (200),
   на форме задачи нет кнопки сохранения, POST на изменение → 403.
2. Под записью-правящим: то же изменение проходит.
3. `/admin/auth/user/` под обеими → 403.
4. После правки под правящим в `/admin/admin/logentry/` появилась
   строка с его именем.
5. После отзыва открытая вкладка отозванного при следующем переходе
   уводит на форму входа.
6. Под записью-смотрящим открыть `/admin/tenancy/tenant/<id>/change/`:
   видно «задан (…хвост)», самого токена в странице нет (проверить
   поиском по исходному коду страницы).
7. Под записью-смотрящим открыть `/admin/conversations/message/` → 403 и
   страница «Общий список переписок закрыт» со ссылками на очередь и на
   форму доступа.
8. Оттуда: очередь → карточка обращения (403 со ссылкой) → форма →
   причина → возврат на обращение → карточка, разговор, сообщения и
   профиль открываются.
9. В `/admin/adminconsole/clientdataaccesslog/` появились строки: одна
   «доступ открыт», по одной «экран открыт» на каждый открытый экран и
   «отказано» на первую попытку.
10. На карточке клиента под смотрящим нет телефона и нет блока
    «Personalisation» с `context` (проверить поиском по исходному коду
    страницы).

## Changelog

- _2026-09-05_ — DRF-1514 — широкий доступ смотрящего закрыт: общего
  списка переписок нет, переписка и профиль открываются пропуском по
  обращению с обязательной причиной, каждый просмотр и каждый отказ
  пишутся в отдельный журнал доступа. Телефон, `BotUser.context` и
  сырые полезные нагрузки не показываются ни при какой причине. После
  выката обязателен прогон `sync_admin_roles` — у ролей появились права
  на два новых экрана.
- _2026-09-05_ — DRF-1495, вторая итерация после ревью — политика
  сокрытия полей-секретов (read-only отрисовка Django обходила подмену
  виджета и показывала ролям полный токен), журнал переехал с сигнала
  на обёртку `log_actions` (групповое удаление в него не попадало),
  admin-действия обязаны объявлять права, `django_celery_beat` и
  `promptreg` закрыты от ролей.
- _2026-09-05_ — DRF-1495 (эпик DRF-75) — две роли вместо «каждая
  запись суперпользователь», команды выдачи и отзыва, журнал действий
  админки в `audit.AuditLog` + видимый экран `admin.LogEntry`, секреты
  тенанта убраны из формы изменения. Учётные записи на боевом контуре
  этой задачей не заводились — только механизм.
- _2026-08-12_ — DRF-1023 executor window — initial version (admin login
  fix: CSRF trusted origins + TLS-proxy flag + Secure cookies; account
  bootstrap via env-driven `createsuperuser --noinput`; cross-tenant
  warning documented and surfaced in the UI).

---

## Проверка состояния перед DRF-1495 (2026-09-05)

Проверялся код на `origin/dev@a7fa4fa`, не тикет DRF-1023. Что найдено.

**Что работает.** Механика входа, починенная DRF-1023, на месте и не
сломана: `_parse_trusted_origins` + `DJANGO_CSRF_TRUSTED_ORIGINS` /
`DJANGO_BEHIND_TLS_PROXY` / `DJANGO_SESSION_COOKIE_SECURE` /
`DJANGO_CSRF_COOKIE_SECURE` читаются в `config/settings/base.py:42-102`.
Аутентификация — сессионная (`SessionMiddleware` +
`AuthenticationMiddleware` в `MIDDLEWARE`), JWT в контуре админки нет.
`/admin/` смонтирован в `config/urls.py`; 19 приложений регистрируют
`ModelAdmin`.

**Чего нет — и это дыры, а не недоделки.**

1. **Ролей нет вообще.** Во всём репозитории ни одной `Group`, ни одной
   выдачи `user_permissions`. Единственный документированный способ
   завести учётную запись — `createsuperuser` (шаг 3 этого раннбука),
   то есть **любая заведённая запись — суперпользователь**. «Смотрящий»
   и «правящий» не различимы: тот, кому дали посмотреть очередь
   handoff, может править каталог, тенантов и чужие салоны.
2. **Отзыва нет.** Ни команды, ни процедуры. Раннбук описывает только
   выдачу и смену пароля. Ушедший человек остаётся с активной сессией
   и правами суперпользователя.
3. **Журнала действий админки нет.** `apps.audit.AuditLog` +
   `write_audit()` существуют и хороши (DRF-426), но **ни один
   `ModelAdmin` в них не пишет** — единственное исключение
   `MasterServiceAdmin` (DRF-975), и то через provenance-контекст
   каталога, а не как общий журнал. Django свой `admin.LogEntry` пишет,
   но он **нигде не зарегистрирован в админке** — то есть невидим.
   Правка через `/admin/` над живыми данными сегодня не оставляет
   следа, который можно прочитать.
4. **Секреты видны в интерфейсе.** `TenantAdmin.fieldsets`
   (`apps/tenancy/admin.py`) отдаёт `telegram_bot_token` и
   `telegram_webhook_secret` обычными текстовыми полями формы
   изменения. Маскируется только колонка списка
   (`telegram_bot_token_masked`); полное значение обоих секретов
   уезжает в HTML страницы редактирования каждому, кто её открыл.
5. `apps.adminconsole` — пустой каркас (`apps.py` + `__init__.py`),
   зарезервированный под «Django admin chrome»
   (`config/settings/base.py:140,178`). Дом для этой работы есть, он
   просто не заселён.

**Живых учётных записей проверить нельзя и не нужно:** на боевой контур
эта задача не ходит (правило DRF-75). Механизм заводится здесь, записи
заводит владелец.
