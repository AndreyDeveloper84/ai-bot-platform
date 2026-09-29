# АУДИТ ГОТОВНОСТИ ПИЛОТА — сверка Urgent-задач вехи с кодом

**Дата:** 2026-08-22. **Эталоны:** бот `ai-bot-platform@origin/dev = ce9c298`, бэкенд `beautygo_backend@origin/dev = 38679bb`.
**Метод:** только чтение через `git show origin/dev:<path>` и `git grep origin/dev`. Ни одной мутации: ни в коде, ни в БД, ни в Linear.
**Рабочее дерево бэкенда:** `beautygo_backend-drf1043` (чистое, ветку не переключал). Каталоги `-admin`, `-conv`, `-drf1061`, `-master`, `-codex`, `-salonadmin`, `-drf1046` не тронуты.

---

## Итог в пяти строках

1. **Из восемнадцати задач живых — одиннадцать, устранённых — пять, две не определяются по коду.** Задач **восемнадцать, а не девятнадцать**: в вехе ровно 18 незакрытых Urgent, и это твоя таблица строка в строку.
2. **Весь мастерский кластер входа устранён и никем не закрыт в трекере** — DRF-1104, DRF-1135, DRF-1105 починены чужой работой (`31b07e5`, `655e5c2`+`00eb1d4`, эпик DRF-1061). Туда же DRF-1102 и DRF-1250.
3. **Пилот держит одна цепочка из двух задач:** DRF-1248 → DRF-1228 → merge PR #1219. За ней стоят салонное закрытие визита, ручная запись, отмена и поиск клиента — на поверхности, которой уже пользуется живой человек.
4. **DRF-1148 живёт не в тех репозиториях, что в постановке.** Канон лежит в **третьем** репозитории `ayla-knowledge`, работа сделана, но висит в **открытом PR #15**: в `origin/main` под `07 UX` — ноль файлов, и BOT-001 там не существует.
5. **Самая дешёвая и самая дорогая по последствиям — DRF-1050.** Это данные, а не код: строки графика-заглушки продают закрытое время. Правится SQL-ем, а стоит первой же брони, на которую никто не придёт.

---

## Таблица: задача × класс × чем доказано

| Задача | Класс | Чем доказано |
|---|---|---|
| **DRF-1048** Визиты не помечаются состоявшимися | **ЖИВОЙ** | Sweep построен, но выключен по умолчанию и нигде не включается: `settings/base.py:421-422`, `433-434`; `appointments/tasks.py:284`, `287-300`. Обе ручные ручки требуют роли `admin`, которой в `formula-tela` нет |
| **DRF-1228** Приглашение администратора салона | **ЖИВОЙ** | В боте механизм есть целиком, в Ayla — только SSH-команда, и **между ними ни одного вызова**: `git grep -i ayla -- apps/identity/services/staff_invites.py` даёт только префикс кода |
| **DRF-1138** Кабинет мастера: диалоги/клиенты/черновики пусты | **ЖИВОЙ** | Тенантный фильтр против бестенантного пилота: `master_api/services/conversations.py:671-675`, `690-699`; `customers.py:215-219` + пустой `BookingRequest.master_id` |
| **DRF-1243** Ayla без секретных стражей | **ЖИВОЙ** | В корне `origin/dev` бэкенда нет `.pre-commit-config.yaml`, `.secrets.baseline`, `.githooks/`; в `ci.yml` только flake8. **Репозитории публичны с 19.08** |
| **DRF-1225** `/anketa` не диспетчеризуется | **ЖИВОЙ** | `apps/orchestrator/turn_seam.py:101-104` разводит пути; `:159-176` — глобальная ветка не импортирует реестр навыков вовсе |
| **DRF-1220** Канон чинился на тенантном пути | **ЖИВОЙ** | `apps/channels/max/global_onboarding.py:300-304` подменяет все четыре welcome-kind на один текст и одну кнопку; `:306-307` выбрасывает сетку первых действий |
| **DRF-1135** Мастерские ручки отдают 401 | **УСТРАНЁН** | `655e5c2` + `00eb1d4`: `apps/identity/services/bot_user_resolver.py:47-65`, `75-92` — `BotUser` резолвится по боту, подписавшему initData, а не по свежести |
| **DRF-1126** Расписание читает локальное зеркало | **ЖИВОЙ** | `master_api/services/schedule.py:589-593`, `605-611`; `visit_source.py:202-205` — чтение `RemoteBookingProxy` и локальных `WorkingHours`, ни одного HTTP к Ayla |
| **DRF-1119** Ответ мастера не отправляется | **ЖИВОЙ** | `master_api/services/conversation_detail.py:533-555` — `record_message` + аудит + `emit`, отправки в канал нет; у события `CONVERSATION_MASTER_REPLIED` ноль подписчиков |
| **DRF-1105** Нет кода связи мастера с ботом | **УСТРАНЁН** | Эпик DRF-1061: `apps/identity/services/staff_invites.py:29-36`, `452-465` — ветка `master` привязывает `linked_bot_user` к существующей строке, а не создаёт новую |
| **DRF-1104** Мастерский API отдаёт TENANT_REQUIRED | **УСТРАНЁН** | `31b07e5`: `apps/tenancy/middleware.py:89` (`/api/v1/master/`), `:136` (`/api/v1/me` точным путём); регрессы `tests/test_middleware.py:289-345` |
| **DRF-1102** Не пускает в воронку на «массаж» | **УСТРАНЁН** | `f6e25a2`: `apps/channels/max/handler.py:790-799` → `orchestrator/concierge.py:434-474`; e2e-регресс `test_tenant_less_discovery_e2e.py:154-188` падает, если ход дошёл до LLM |
| **DRF-1250** Салон закрывает визит | **УСТРАНЁН** | `38679bb`: `tenants/urls.py:75-79`, `tenants/appointments_api.py:137-140`, `624`, `709`, `722`; `users/middleware.py:104`. **Вызывающего нет** — он в незамерженном PR #1219 |
| **DRF-1248** `has_revoked` не отличает смену роли от бана | **ЖИВОЙ** | `create_booking_service.py:342-350` и `users/signals.py:52-65` смотрят только `is_active`, хотя `revoke_reason` существует (`users/models.py:741`) и заполняется (`users/services.py:636`) |
| **DRF-1148** Канон не в git | **ЖИВОЙ** | Третий репозиторий `ayla-knowledge`: в `origin/main` под `07 UX` **ноль** файлов; 29 файлов, включая BOT-001, лежат в **открытом PR #15** |
| **DRF-1103** Зеркало не хранит услугу | **ЖИВОЙ** | `appointments/serializers.py:59,86` не сериализует `salon_service` → `tools.py:3442` пишет `None`; `eventbus/consumers/booking.py:694` делает `return` до применения полей |
| **DRF-1066** Mini App: нет экрана успеха | **НУЖЕН ЖИВОЙ ЗАМЕР** | Экран, маршрут, переход и защита от дубля существуют **с 26 мая**, то есть 14.08 код был тот же. Коммит `1c62346`: «This does not repair the success screen» |
| **DRF-1050** График-заглушка 10:00–19:00 | **НУЖЕН ЖИВОЙ ЗАМЕР** | Производивший заглушку код удалён (`views_invite.py:509-516`), но **миграции-уборки нет ни в одном репозитории** — уже созданные строки живут дальше |

**Счёт: ЖИВЫХ — 11, УСТРАНЁННЫХ — 5, НУЖЕН ЖИВОЙ ЗАМЕР — 2, НЕПРИМЕНИМЫХ — 0.**

---

## Разделы по каждой живой задаче

### DRF-1048 — визиты никогда не помечаются состоявшимися

Писателей статуса `completed` сегодня три, и все зовут один общий `close_booking`. На пилоте не работает ни один.

**Автоматический sweep построен, но выключен по умолчанию и нигде не включается.**

- `beautygo_backend/djangoProject/settings/base.py:421-422` — `BOOKING_AUTO_COMPLETE_ENABLED = os.environ.get(..., "false").lower() == "true"`
- `beautygo_backend/djangoProject/settings/base.py:433-434` — `BOOKING_AUTO_COMPLETE_NOT_BEFORE = os.environ.get(..., "")`
- `beautygo_backend/appointments/tasks.py:284` — `if not ...BOOKING_AUTO_COMPLETE_ENABLED...: return None`
- `beautygo_backend/appointments/tasks.py:287-300` — при пустом floor задача пишет `booking.auto_complete.misconfigured` и **отказывается подметать**
- `beautygo_backend/djangoProject/settings/base.py:869-872` — beat-запись каждые 900 с; комментарий на `:866`: «inert until ops deliberately switches it on»

**Ни одна из двух переменных не задана ни в одном конфиге репозитория** — `.env.example` и `.env.prod.example` их не упоминают. Развёртывание по собственным конфигам даёт beat, который тикает вхолостую.

**Обе ручные ручки требуют роли, которой на пилоте нет.** Путь к `OperationalActor.SALON` — только через активный `TenantUserRelationship(role=admin)`: `beautygo_backend/appointments/authz.py:56-62`. По твоим данным таких связей в `formula-tela` ноль.

Лекарство в коде уже лежит: `beautygo_backend/users/management/commands/provision_salon_admin.py:1-18` — команда написана прямо под этот случай («the pilot salon has none — the audit of 2026-08-14 found zero administrators for it»).

### DRF-1228 — приглашение администратора салона

Разведение двух версий, которое ты просил: **«кода нет» неверно для бота и верно для Ayla, а главный дефект — третий: роли не согласованы вообще.**

**В боте механизм есть целиком:**
- `ai-bot-platform/apps/tenancy/models.py:529-541` — `StaffInvite.Role` включает `admin`
- `ai-bot-platform/apps/admin_api/urls.py:54-56` — `POST admin/staff/invite/`
- `ai-bot-platform/apps/identity/services/staff_invites.py:358`, `378-383` — `_grant_staff_role` → `TenantStaff.all_tenants.create(..., role=invite.role, ...)`

**В Ayla приглашения нет ни в каком виде** — единственный способ появиться администратору это `provision_salon_admin` по SSH (`beautygo_backend/users/management/commands/provision_salon_admin.py:121-127`).

**Между ними ни одной связи.** Погашение инвайта в боте не делает ни одного вызова в Ayla: `git grep -i ayla -- apps/identity/services/staff_invites.py` возвращает только константу префикса кода `AYLA-7K3M` (`:58`). При этом салонные поверхности Ayla авторизуются именно по TUR `role=admin` (`beautygo_backend/users/permissions.py:339-344`, `internal_api.py:563-568`). **Человек может быть админом в боте и получать 404 от Ayla.**

Почему в `formula-tela` нет ни одной admin-связи — теперь объяснимо без гадания: код погашения живёт **только в салонном боте** (`apps/channels/max/salon_handler.py:395`), который не обработал ни одного сообщения, а UI выдачи кода во фронте отсутствует — `git grep 'staff/invite' -- apps/miniapp` пусто. Дверь есть, войти в неё физически было нельзя.

**Это головная задача пилота.** Она держит merge PR #1219 (`docs/REPORT_SALON_ADMIN.md` §42.1: «#1219 по-прежнему не мержу — ждёт DRF-1228»), а PR #1219 — единственный вызывающий салонного `complete/`.

### DRF-1248 — `has_revoked` не отличает смену роли от бана

Поле причины **существует и заполняется**, но предикат его не читает — в этом весь дефект, и поэтому починка дешёвая.

- `beautygo_backend/users/models.py:741-743` — `revoke_reason = models.CharField(max_length=128, blank=True, default="")`, без `choices`
- `beautygo_backend/users/services.py:636`, `650` — `tur.revoke_reason = reason` при отзыве и в событии
- **Но:** `beautygo_backend/appointments/application/services/create_booking_service.py:342-350`:
  ```python
  has_revoked = any(not row.is_active for row in existing)
  if has_revoked:
      raise NotFound("Specialist not found.")
  ```
- И `beautygo_backend/users/signals.py:52-65` — та же подмена: `filter(..., is_active=False).exists()`.

Роли в Ayla взаимоисключающие, значит выдача `admin` гасит строку `customer` → `has_revoked=True` → человек **навсегда** теряет возможность записаться в свой салон, и получает 404, а не внятный отказ. Ровно поэтому решением OD-6 от 21.08 запрещено делать DRF-1228 раньше этой задачи.

Сцепка, которую стоит знать: `provision_salon_admin.py:92-97` отказывается действовать при любой отозванной связи — то есть после одной смены роли администратора нельзя назначить даже командой.

### DRF-1243 — Ayla без секретных стражей

Заголовок подтверждается буквально, но решающий факт в нём не назван: **репозитории стали публичными 19.08.**

- В корне `beautygo_backend@origin/dev` нет `.pre-commit-config.yaml`, `.secrets.baseline`, `.githooks/`
- `beautygo_backend/.github/workflows/ci.yml` — три job (`lint` с одним flake8, `test`, `deploy`); ни `detect-secrets`, ни `gitleaks`, ни `trufflehog`
- В боте оба файла есть (`ai-bot-platform/.pre-commit-config.yaml:35-49`, `.secrets.baseline:1-3`), но **в CI бота они тоже не гоняются** — это добровольный локальный хук, обходимый `--no-verify`

**Лекарство лежит готовым и проверено мной.** Зависший коммит цел:

```
каталог: C:/Users/user/PycharmProjects/Ayla/djangoproject   (старый клон бэкенда)
коммит:  5392ac83  2026-07-30  "security(repo): add secret and sensitive-file guards"
несёт:   .pre-commit-config.yaml (+38), .secrets.baseline (+507),
         scripts/sensitive_file_guard.py (+84), .github/workflows/ci.yml (+21), .gitignore (+20)
```

Оговорка из самой задачи, и она верна: baseline от 30.07 за месяц устарел. **Принять его без перегенерации — значит узаконить утечку вместо того, чтобы её показать.**

### DRF-1225 — `/anketa` не диспетчеризуется на глобальном пути

Оба пути идут через один шов `orchestrate_turn`, но шов разводит их по разным мозгам.

- `ai-bot-platform/apps/orchestrator/turn_seam.py:101-104` — `if context.surface == SURFACE_GLOBAL: reply = _global_legacy_adapter(context)`
- `ai-bot-platform/apps/orchestrator/turn_seam.py:159-176` — глобальная ветка зовёт `generate_concierge_reply` и **не импортирует `apps.skills.registry` вовсе**
- `ai-bot-platform/apps/orchestrator/turn_seam.py:132` — `from apps.skills.registry import dispatch` только внутри тенантного адаптера
- `ai-bot-platform/apps/channels/max/handler.py:832-834` — пилотный бот входит именно с `SURFACE_GLOBAL`
- `ai-bot-platform/apps/skills/nutrition_anketa/skill.py:102` — единственное место разбора `/anketa` во всём репозитории, и оно недостижимо

### DRF-1220 — канон чинился на тенантном пути

**Поправка к ходу рассуждения, которая меняет вывод.** Первое подозрение — что глобальное приветствие выключено флагом — неверно: `GLOBAL_BOT_ONBOARDING=true` включён владельцем 20.08 и проверен живым проходом (`docs/WINDOWS.md:8`). Дефект держится на другом, и это проверено глазами в файле:

- `ai-bot-platform/apps/channels/max/global_onboarding.py:300-304` — для **всех четырёх** welcome-kind текст и клавиатура подменяются на `GLOBAL_WELCOME_TEXT` + одну кнопку «▶️ Начать»
- `ai-bot-platform/apps/channels/max/global_onboarding.py:306-307` — на S5 «drop the wellness first-action grid entirely»
- `ai-bot-platform/apps/channels/max/global_onboarding.py:96-97` — код сам признаёт: «Behaviour on this path is therefore unchanged by DRF-1202»

Значит пять быстрых действий DRF-1200 и три состояния приветствия DRF-1202 до пилотной поверхности не доходят. Это же объясняет твой известный факт «тенантное приветствие обслужило ноль разговоров»: глобальный путь ходит в `global_onboarding.py`, а не в `apps/skills/welcome/skill.py`.

### DRF-1138 — кабинет мастера: диалоги, клиенты и черновики пусты

Две независимые причины, каждой достаточно.

**A. Тенантный фильтр против бестенантного пилота.** Разговоры пилота живут под сентинел-тенантом `global_bot`, а сервисы фильтруют по `formula-tela`:
- `ai-bot-platform/apps/master_api/services/conversations.py:690-699` — `Conversation.all_tenants.filter(tenant_id=master.tenant_id, ...)`
- `ai-bot-platform/apps/master_api/services/customers.py:215-219`, `260-263` — `BotUser` по тому же тенанту, иначе строка молча пропускается через `continue`

**B. Пустой `BookingRequest.master_id`.** Предикат «диалог касается этого мастера» — `Exists(BookingRequest ... master_id=master.id)`:
- `ai-bot-platform/apps/master_api/services/conversations.py:671-675`
- `ai-bot-platform/apps/master_api/services/visit_source.py:6-9` — замер в самом коде: «On the pilot that table holds 4 rows and `master_id` is NULL on **all four**»
- Причина: `ai-bot-platform/apps/skills/booking/tools.py:1249-1254` — бот пишет `master_name` строкой и **не заполняет FK** `master`

### DRF-1126 — мастерский экран расписания читает локальное зеркало

- `ai-bot-platform/apps/master_api/services/schedule.py:589-593` — визиты через `master_visits()`
- `ai-bot-platform/apps/master_api/services/visit_source.py:202-205` — а это `RemoteBookingProxy`, и `:33` честно говорит «is a mirror, not a snapshot»
- `ai-bot-platform/apps/master_api/services/schedule.py:605-611` — рабочие часы из локальных `WorkingHours` бота, у которых после `99f8da6` не осталось ни одного продуктового писателя

Клиентская поверхность этот же дефект уже пережила и вылечена (`apps/miniapp_api/views.py:423-446`, «Slot starts read from Ayla, the system of record»). Мастерский экран правки не получил.

**Важно для планирования:** канонической ручки чтения дня специалиста в Ayla сегодня **нет** — `beautygo_backend/appointments/internal_urls.py:16-43` содержит только create/cancel/reschedule/payment и чтение одной брони. Значит закрытие 1126 требует новой ручки на бэкенде, а не правки бота. Это самая дорогая задача из одиннадцати.

### DRF-1119 — ответ мастера сохраняется, но не отправляется

Все три пути отправки заканчиваются на запись в БД.

- `ai-bot-platform/apps/master_api/services/conversation_detail.py:533-555` — `record_message(...)` + `write_audit(...)` + `emit(...)`, и всё; в докстринге `:496-502` шага «отправить в канал» просто нет
- `ai-bot-platform/apps/master_api/services/ai_drafts.py:1018-1030` («send-as-me») и `:1169-1182` («release-to-ai») — то же самое
- У события `CONVERSATION_MASTER_REPLIED` (объявлено `apps/events/vocabulary.py:203`, эмитится `conversation_detail.py:555`) **ноль подписчиков** во всём боте

Шаблон доставки в кодовой базе есть — внутренний чат мастер↔админ его получил (`apps/internal_chat/notify.py`, коммит `239b93d`). К этому пути он не подключён.

### DRF-1148 — каноническая база знаний не в git

**Задача живёт в третьем репозитории, которого нет в постановке аудита:** `C:/Users/user/PycharmProjects/Ayla/ayla-knowledge` (github `AndreyDeveloper84/ayla-knowledge`).

Работа **сделана**: пять коммитов (`f3c9d97`, `1361afd`, `c106161`, `ac05ab8`, `43c0324`) на ветке `drf-1148/canon-under-version-control`, ветка запушена. **Но она не в `main`:**

```
git ls-tree -r --name-only origin/main -- "07 UX"    →  0 файлов
git rev-list --left-right --count origin/main...HEAD →  0  5
PR #15 — ОТКРЫТ, 29 файлов
```

Среди 29 файлов — все шесть UX-контрактов и `01 Product/BOT-001 First Contact Specification.md`. **То есть BOT-001, по которому судят весь эпик канона (DRF-1196/1220/1225), в `main` не существует.** Сверх того локально висят 9 изменённых и 9 неотслеживаемых файлов канона, включая `CANON_INDEX.md` и `OWNER_DECISION_REGISTER.md`.

Починка: смержить PR #15 и закоммитить дрейф.

### DRF-1103 — зеркало броней не хранит услугу для записей из диалога

Корень глубже, чем «поле не заполняется», и объясняет асимметрию «диалог против Mini App».

**Путь Mini App мирор не пишет** — его создаёт консюмер события `booking.created`, а backend всегда кладёт в payload `str(dto.service_id)` (`beautygo_backend/appointments/application/services/create_booking_service.py:453`).

**Путь из диалога пишет мирор сам**, до прихода события, и берёт услугу из **тела HTTP-ответа Ayla на create**. А ответ создания — это `AppointmentDetailSerializer`, где сериализуется только маркетплейсный FK `service`; салонный `salon_service` в нём **отсутствует вовсе**:
- `beautygo_backend/appointments/serializers.py:59`, `86` — `service = AppointmentServiceSerializer(read_only=True)`, и в `fields` только `service`
- `beautygo_backend/appointments/models.py:236-248` — CHECK `appointment_exactly_one_service_source`: `service` XOR `salon_service`
- `ai-bot-platform/apps/skills/booking/provider.py:437`, `444` — `"service_id": (service or {}).get("id") or raw.get("service_id")` → `None` для салонной услуги
- `ai-bot-platform/apps/skills/booking/tools.py:3442` — `"service_id": _as_uuid(raw.get("service_id"))` записывает этот `None`

**И это уже не чинится никогда:** когда приходит настоящее `booking.created`, консюмер видит проксю в статусе CONFIRMED и уходит в ветку `advanced_state_noop`:
- `ai-bot-platform/apps/eventbus/consumers/booking.py:662`, `694` — `if proxy.status in _CREATED_ADVANCED_STATUSES: ... return`
- `ai-bot-platform/apps/eventbus/consumers/booking.py:695-696` — `update(**update_fields)` с `service_id` на этом пути **недостижим**

Уведомление салона по этой ветке уже пропатчили (`_announce_booking_created`, DRF-1069) — а применение полей так и осталось за `return`.

Видимые следствия: `miniapp_api/views.py:1240-1254` (`service_name` пустая строка), `admin_api/services/salon_day.py:214`, `booking/master_notify.py:180-192` («Услуга: —»).

---

## Нужен живой замер — готовые команды

Доступы: `ssh taximeter@194.87.99.126`; бот `/home/taximeter/ai-bot-platform-dev`, бэкенд `/home/taximeter/beautygo/dev`.
Базы: Ayla — `docker exec dev-db-1 psql -U beautygo -d beautygo`; бот — `docker exec ayla-bot-staging-postgres-1 psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"`.

### 1. DRF-1050 — класс определяется только замером

Заглушка живёт в двух местах с **разной** значимостью, и лечение у них разное.

```sql
-- БД Ayla — это то, что реально отдаёт слоты клиенту
SELECT sp.id, sp.display_name, w.day_of_week, w.is_working_day, w.start_time, w.end_time
FROM appointments_specialistworkinghours w
JOIN users_specialistprofile sp ON sp.id = w.specialist_id
JOIN tenants_tenant t ON t.id = sp.tenant_id
WHERE t.slug = 'formula-tela'
ORDER BY sp.display_name, w.day_of_week;
```

**Ожидание дефекта:** семь строк на мастера, `10:00`/`19:00`, `is_working_day=true` на все семь дней.
**Если так — задача живая и чинится данными:** через салонную schedule-поверхность Ayla (`beautygo_backend/tenants/urls.py:12-20`), с бэкапом таблицы до правки.

```sql
-- БД бота — влияет только на строку в карточке мастера, не на слоты
SELECT m.name, w.day_of_week, w.is_working, w.start_time, w.end_time
FROM scheduling_workinghours w
JOIN catalog_catalogmaster m ON m.id = w.master_id
JOIN tenancy_tenant t ON t.id = w.tenant_id
WHERE t.slug = 'formula-tela' ORDER BY m.name, w.day_of_week;
```

Эти строки можно просто удалить: производивший их код снят (`apps/admin_api/views_invite.py:509-516`), а миграции-уборки не написал никто.

**Побочная находка, живая и не заведённая:** экран приглашения мастера обещает администратору «График (применится автоматически — можно изменить) / Пн-Пт 10:00–19:00», хотя сервер с DRF-1062 не создаёт ничего, а кнопка «Изменить график» `disabled` — `apps/miniapp/src/screens/admin/AdminInviteMasterScreen.tsx:283`, `798-805`, `810`. Новый мастер получает не заглушку, а пустоту, при этом UI утверждает обратное.

### 2. DRF-1066 — код невиновен, нужен снимок с хоста

Экран, маршрут, переход и блокировка кнопки существуют **с 26 мая** (`cca9bfe`), то есть 14.08 код был ровно такой же, а экран не показался. Коммит `1c62346` это признаёт дословно: «This does not repair the success screen». Найденная механика: **деплой никогда не собирает Mini App** — `.github/workflows/deploy-dev.yml:91-92` пересобирает только `web worker shadow-worker`, а `dist/` в `.gitignore` и отдаётся nginx напрямую.

```bash
# насколько отстал собранный бандл
ls -l --time-style=full-iso /home/taximeter/ai-bot-platform-dev/apps/miniapp/dist/index.html
cd /home/taximeter/ai-bot-platform-dev && git log -1 --format='%h %ai %s' origin/dev

# есть ли в отданном бандле сам экран и защита от дубля
B=$(curl -s https://miniapp-dev.gobeauty.site/ | grep -oE 'index-[A-Za-z0-9_-]+\.js' | head -1)
curl -s "https://miniapp-dev.gobeauty.site/assets/$B" | grep -c "customer/booking/success"
curl -s "https://miniapp-dev.gobeauty.site/assets/$B" | grep -c "Записываю…"
```

Ноль хотя бы в одной строке — диагноз «протух `dist`», лечится пересборкой и добавлением шага во `deploy-dev.yml`. Обе найдены — значит рантайм-ошибка, и нужен снимок консоли на живом проходе.

### 3. DRF-1048 — единственное, что может опровергнуть мой вывод

```bash
cd /home/taximeter/beautygo/dev
docker compose exec web python manage.py shell -c "
from django.conf import settings as s
print('ENABLED=', s.BOOKING_AUTO_COMPLETE_ENABLED, 'NOT_BEFORE=', repr(s.BOOKING_AUTO_COMPLETE_NOT_BEFORE))"
docker compose ps celery-beat
docker compose logs --tail=200 celery-beat | grep -i auto_complete
```

Масштаб и есть ли кому нажимать кнопку:

```sql
SELECT status, count(*) FROM appointments_appointment GROUP BY status ORDER BY 2 DESC;
SELECT r.role, r.is_active, r.revoke_reason, count(*)
FROM users_tenantuserrelationship r JOIN tenants_tenant t ON t.id = r.tenant_id
WHERE t.slug = 'formula-tela' GROUP BY 1,2,3;
```

### 4. DRF-1103 — какого каталога пилот

Дефект гарантирован для салонного каталога и отсутствует для маркетплейсного:

```sql
-- Ayla
SELECT count(*) FROM services_salonservice ss
JOIN tenants_tenant t ON t.id = ss.tenant_id WHERE t.slug = 'formula-tela';
-- бот
SELECT source, count(*) FILTER (WHERE service_id IS NULL) AS без_услуги, count(*) AS всего
FROM booking_remotebookingproxy GROUP BY source;
```

Ненулевой первый ответ плюс `source='automation'` с `service_id IS NULL` — дефект живой в полном объёме.

### 5. DRF-1138 — цифры для приёмки будущей правки

```bash
cd /home/taximeter/ai-bot-platform-dev
docker compose exec web python manage.py shell -c "
from django.db.models import Count
from apps.conversations.models import Conversation
from apps.booking.models import BookingRequest, RemoteBookingProxy
print('диалоги по тенантам:', list(Conversation.all_tenants.values('tenant__slug').annotate(n=Count('id'))))
print('BookingRequest всего:', BookingRequest.all_tenants.count(),
      '| с заполненным master FK:', BookingRequest.all_tenants.filter(master__isnull=False).count())
print('зеркало по тенантам:', list(RemoteBookingProxy.all_tenants.values('tenant__slug').annotate(n=Count('id'))))"
```

**Ожидание:** диалоги под `global_bot`, `master FK` = 0. Подтвердит обе причины разом.

### 6. DRF-1135 / DRF-1105 — устранены по коду, но зависят от данных

```bash
docker compose exec web python manage.py shell -c "
from apps.catalog.models import CatalogMaster
for m in CatalogMaster.all_tenants.filter(tenant__slug='formula-tela'):
    print(m.id, m.name, 'active=', m.is_active, 'status=', m.invite_status, 'linked=', m.linked_bot_user_id)"
```

`linked` NULL у всех четверых означает, что код исправен, но связь никто не создал — это данные, а не регресс.

---

## Что действительно блокирует запуск пилота, а что нет

Здесь я не пересказываю приоритеты трекера. Ниже мой счёт, и он расходится с ним в обе стороны.

### Блокирует — пять задач

**1. DRF-1050 — блокирует, и это самое дешёвое из всего списка.** Единственный дефект в перечне, который причиняет ущерб *постороннему человеку*: клиент записывается в час, когда мастер не работает, приходит, и никого нет. Салон при этом выглядит обманщиком. Стоимость починки — один `UPDATE` после звонка в салон. Соотношение «цена бездействия / цена починки» тут лучшее в списке с большим отрывом. **Делать первой, до всякого кода.**

**2. DRF-1248 → DRF-1228 — блокируют как одна работа.** Это голова единственной цепочки, которая держит продукт: пока роли `admin` нет, PR #1219 не мержится, а вместе с ним не приезжают ручная запись, отмена, поиск клиента и закрытие визита — на поверхность, **которой уже пользуется живой человек**. Порядок внутри пары не косметический: выдать `admin` до починки `has_revoked` значит навсегда лишить владельца салона возможности записаться к себе же, и откатить это штатными средствами будет нельзя.

**3. DRF-1048 — блокирует не запуск, а смысл пилота.** Формально люди могут записываться и с незакрытыми визитами. Но на завершении визита висят комиссия, захват платежа, запрос отзыва и RFM — то есть **всё, ради чего контролируемый пилот и проводится**. Пилот, который не закрывает визиты, соберёт ноль отзывов, ноль рейтинга и ноль выручки, и по его итогам нельзя будет принять ни одного решения. Починка почти бесплатная: две переменные окружения, `provision_salon_admin` и разовый `complete_elapsed_backlog`. Дороже работы здесь только решение «включать sweep или закрывать руками» — оно твоё, а не инженерное.

**4. DRF-1243 — блокирует, и я ставлю её выше, чем трекер.** Единственная задача в списке с неограниченным сверху ущербом. Репозитории публичны с 19.08, в бэкенде платёжные и персональные данные, и **ни одного стража**. Все остальные дефекты стоят одной испорченной брони; этот может стоить всего. Работа — cherry-pick готового коммита плюс перегенерация baseline, полдня. Формально «не мешает людям записываться» — и именно поэтому её будут откладывать, пока не станет поздно.

**5. DRF-1119 — блокирует, если мастера входят в пилот.** Мастер отвечает клиенту, видит своё сообщение в кабинете и уверен, что оно доставлено. Клиент не получает ничего. Это хуже неработающей функции: неработающую видно, а эту нет, и обнаружится она обиженным клиентом. Если кабинет мастера в контур пилота не входит (см. развилку ниже), задача мгновенно перестаёт быть блокирующей.

### Не блокирует, несмотря на Urgent — четыре задачи

**DRF-1225 (`/anketa`) — не блокирует.** Дефект настоящий и доказан, но `/anketa` — команда пищевого дневника, а пилот продаёт запись в бьюти-салон. Никто из трёх-пяти пилотных пользователей её не наберёт. Чинить надо, потому что за ней стоит общий изъян — на живом пути навыков нет вовсе, — но не ради этого пилота.

**DRF-1220 — не блокирует.** Глобальное приветствие работает и проверено живым проходом 20.08. Речь о том, что вместо пяти быстрых действий человек видит одну кнопку: хуже задуманного, но не сломано. Настоящая ценность задачи не в UX: **эпик канона правит код, которым пилот не ходит**, то есть работа над ним тратится впустую прямо сейчас. Останавливать эпик — да; держать ради него запуск — нет.

**DRF-1126 и DRF-1138 — не блокируют, и обе дороже, чем выглядят.** У 1126 закрытие требует новой канонической ручки на стороне Ayla, которой сегодня нет. У 1138 — переезда мастерских сервисов с тенантной фильтрации на бестенантную реальность пилота. Это недели, а не дни.

**DRF-1148 — не блокирует запуск, но блокирует корректность работы, и стоит один клик.** Пока BOT-001 не в `main`, любой исполнитель сверяется либо с несуществующим документом, либо с копией на чьём-то диске. Смержить PR #15 — минута; отложить — значит и дальше принимать решения по канону, которого в общем доступе нет.

**DRF-1066 — не блокирует до замера.** Если `dist` протух, это не дефект экрана, а дефект конвейера, и он один закрывает разом целый класс задач (см. ниже).

### Ключевая развилка, которую надо решить до всего остального

**Входит ли кабинет мастера в контролируемый пилот?**

Шесть из восемнадцати Urgent-задач относятся только к нему: 1104, 1105, 1135 — устранены; 1138, 1126, 1119 — живы. Если пилот это 3–5 **клиентов** плюс салон, весь кластер откладывается целиком, и живых задач остаётся восемь вместо одиннадцати. Если мастера в пилоте есть — DRF-1119 переходит в блокирующие, а 1126 и 1138 становятся самой дорогой работой в вехе. **Это единственное решение в списке, которое меняет объём вдвое, и оно продуктовое, а не инженерное.**

### Неурочное, что блокирует, а в списке не значится

**Конвейер выкладки фронта — по-моему счёту это блокер №0, и задачи на него нет.** `deploy-dev.yml:91-92` пересобирает только Python-сервисы; `apps/miniapp/dist` в `.gitignore` и собирается руками. Это уже стоило двенадцати дней, когда владелец смотрел на августовские экраны поверх свежего бэкенда, и это же — лучшая гипотеза по DRF-1066. **Пока шаг сборки не в конвейере, любая фронтовая правка из этого отчёта может быть «сделана» и не существовать для людей.** Один шаг в workflow снимает целый класс ложных выводов.

**DRF-1219 закрыта, но её обход не в коде.** MAX допускает одного бота на один адрес вебхука и второй молча выбивает первого. Сейчас боты разведены по доменам, и это держится на договорённости, а не на коде — то есть повторится при первой же подписке, сделанной по памяти. Задача в статусе Done, риск в силе. Цена реализовавшегося риска уже измерена: сутки молчания клиентского бота.

**Выкладка бота ручная и без шага миграций (DRF-1058, High, Backlog).** Автодеплой сломан с 8 августа. Пока живые задачи выше не выложены, они не существуют для людей — а ручная выкладка без `migrate` уже однажды оставила пилот в состоянии хуже, чем до неё. Любой план запуска, где не написано «после выкладки проверить `showmigrations` и пересобрать `dist`», на этом контуре невыполним.

---

## Где постановка разошлась с фактом

1. **Задач восемнадцать, а не девятнадцать.** В вехе `Controlled Pilot — 3–5 users` незакрытых Urgent ровно 18, и они совпадают с таблицей строка в строку. Ничего не потеряно.
2. **Репозиториев не два, а три.** DRF-1148 целиком живёт в `ayla-knowledge`; по двум названным репозиториям она выглядит нерешаемой.
3. **Linear читается.** Прямое чтение через MCP работает; писать, как велено, не стал. Описания оттуда заметно повысили точность по DRF-1050, DRF-1066, DRF-1228, DRF-1243 и DRF-1250 — в частности, только из Linear стало видно, что репозитории публичны с 19.08.
4. **DRF-1250 — ретроспективный номер.** PR #236 помечен в git как DRF-1234 (номер занят дизайнерским эпиком). Работа смержена и на проде, задача в Linear числится Backlog. Ссылки в PR и отчётах стоит поправить.
5. **«Опирайся на заголовок и на код» едва не дало ложный вывод дважды.** По DRF-1066 заголовок «нет экрана» прямо противоречит коду — экран есть с 26 мая; вывод «устранено» был бы неверен, а верный ответ нашёлся в теле коммита и в конвейере выкладки. По DRF-1228 заголовок указывает на приглашение, а дефект — в отсутствии моста между двумя системами.
6. **Ловушка `| head` не понадобилась ни разу, а `MSYS_NO_PATHCONV` — дважды**, оба раза на `.pre-commit-config.yaml` и `.secrets.baseline` в DRF-1243. Предупреждение было точным.
