# Runbook: заведение нового салона оператором (operator-assisted onboarding)

> Status: **draft**
> Last exercised: _never_
> Target completion sprint: Controlled Pilot, волна 15.09.2026 (раздел Q решений владельца)
> Owner: _Platform Lead_

Сверено с кодом: каталог `dev` `b9d1906`, бот `dev` `8bf99f7c` (15.09.2026). Каждая
ссылка — файл и строка/функция, а не пересказ. Источник решения —
`Ayla/docs/OWNER_QUESTIONS_2026-09-12.md`, разделы Q и S; аудит —
`Ayla/docs/AUDIT_SALON_ONBOARDING_ADMIN_2026-09-15.md`.

**Не использовать:** [`connect-five-salons-drf1510.md`](connect-five-salons-drf1510.md)
для нового салона. Он устарел в двух местах: шаг 1 заводит тенант в боте руками с
`--id <uuid>` (сейчас UUID приходит из каталога на экране «Подключить салон»,
`apps/tenancy/admin.py::SalonConnectForm`), и строки 208-210 утверждают, что
`CatalogMaster.invite_status` по умолчанию `accepted` — сейчас `PENDING`
(`apps/catalog/models.py:424-437`), и бронируемость требует ещё `LINKED_TO_AYLA`
(`apps/catalog/master_state.py:380`).

## Purpose

Завести салон с мастерами так, чтобы выполнился критерий владельца: салон связан с
ботом → администратор реально действует → мастера активны → услуги VERIFIED → часы
читает движок записи → клиент находит услугу, видит реального мастера, получает
слот, создаёт запись → салон видит эту запись. Порядок из 13 шагов — владельца,
менять его нельзя. Это «управляемый Pilot-процесс до появления цельного сценария»,
не wizard: новых форм runbook не заводит.

## Trigger / when to run

- Владелец или главное окно назвали салон, который заводится в пилот.
- Салон сам сообщил: название, город, адрес, мастеров, услуги с ценами и
  длительностями, часы работы, ответ «нужна ли проверка здоровья» по каждой
  услуге без канонического шаблона. Без этих данных не начинать — ничего не
  додумывать (цена 0 ₽ не продажная, 1 ₽ не ставить).

## Prerequisites

**Доступ.** Хост пилота `ruvds-o1mqo`. Контейнеры: каталог `dev-web-1`, бот
`ayla-bot-staging-web-1`. Админки: каталог `dev.gobeauty.site/admin/`, бот
`api-dev.gobeauty.site/admin/` (экран «Подключить салон» — только superuser,
`apps/tenancy/admin.py:436-440`). Команды ниже —
`docker exec -i dev-web-1 python manage.py …` для каталога и
`docker exec -i ayla-bot-staging-web-1 python manage.py …` для бота.

**Секреты не печатать.** Только имя переменной, SET/EMPTY и длина.

**Персональные данные не печатать.** Readback ниже печатает только числа, id и
статусы. Телефоны, имена, MAX id в заметки и тикеты не переносить.

**Блокеры на 15.09.2026** (решения главного окна и владельца, раздел S R4):

| Шаг | Блокер | Что снимает |
|---|---|---|
| 10, привязка личности администратора | Операторская привязка «Связать с Ayla» принимает только `role=specialist` (`users/services.py:250`, `users/admin_actions.py:136-139`); S2S — только `client` (`:249`). `provision_salon_admin` заводит `role=admin` (`provision_salon_admin.py:68-74`). Без привязки администратор из бота получает 403. | **DRF-1987**. Обход «завести владельца как specialist» **запрещён** (подмена роли). |
| 12, сотрудники нового салона в салонный бот | На пилоте стоит `MAX_BOT_SALON_TENANT_SLUG=formula-tela`, срез 4c DRF-1705 (DRF-1785) не выложен: сотрудник второго салона молча попадёт в formula-tela. | Выкладка DRF-1785 и снятие переменной (правило владельца R4 (а)). |

Шаги 1–9, 11 (UUID-сверка «Подключить салон») и 13 выполнять можно.

**DaData.** Ключа нет — это не блокер. Место остаётся без координат, расстояние и
«рядом» для салона не работают и не выдаются как функция (решение владельца,
раздел Q). `geocode_locations --provider dadata` без ключа завершается кодом 2 и ничего не
запрашивает (`core/geocoding/providers/dadata.py:94-98` → `tenants/management/commands/geocode_locations.py:103-108`); без `--provider` код 2 по другой причине — флаг обязателен (`:93-97`).

## Step-by-step procedure

Каждый шаг: путь → что проверяет форма/команда → readback. Следующий шаг — только
после readback с ожидаемыми числами.

### Шаг 1. User (role=specialist) на каждого мастера

**Путь.** Каталог → Users → Add: username, пароль, **Role = specialist**, телефон
(`users/admin.py:455-457`). Сигнал создаёт `Profile` и `SpecialistProfile`
(`users/signals.py:8-18`).

**Проверка.** Подсказка `USER_ROLE_HELP`: с другой ролью мастер виден в каталоге,
но не входит в кабинет (`users/admin.py:84-90`). Владелец/администратор салона на
этом шаге мастером **не** заводится (см. блокер шага 10).

**Readback.**
```bash
docker exec -i dev-web-1 python manage.py shell -c \
  "from users.models import SpecialistProfile as P; print('без салона', P.objects.filter(user__role='specialist', tenant__isnull=True).count())"
```
Число выросло ровно на количество заведённых мастеров.

### Шаг 2. Tenant

**Путь.** Каталог → «Тенанты» → Add (`tenants/admin.py:15-48`): `slug`, `name`,
`is_active`; раздел «Адрес» — `city`, `address`.

- `name` — ровно так, как его введут в боте на шаге 11: другое название по тому же
  slug бот отклонит (`apps/tenancy/onboarding.py:543-549`).
- `address` обязателен для шага 3: `promote_tenant_location` берёт адрес отсюда и
  без него завершается кодом 2 (`promote_tenant_location.py`, «пустой адрес —
  переносить нечего»).
- `city` — без него салон не попадает ни в один городской ответ поиска.

**Проверка.** `slug` — `^[a-z0-9][a-z0-9_-]{1,49}$`, уникален
(`tenants/models.py:384-390`). `kind` в форме нет, по умолчанию `salon`.
Часового пояса у тенанта каталога нет.

**Ловушка.** `slug` после создания в форме остаётся редактируемым
(`tenants/admin.py:28`, лист DRF-1978). **Не менять** — по нему бот находит салон.

**Readback.**
```bash
docker exec -i dev-web-1 python manage.py shell -c \
  "from tenants.models import Tenant; t=Tenant.all_objects.get(slug='<slug>'); print(t.id, t.kind, t.is_active, 'city', bool(t.city.strip()), 'address', bool(t.address.strip()))"
```
Ожидаемо: `salon True city True address True`. `t.id` записать — он сверяется на
шаге 11.

### Шаг 3. ServiceLocation

**Путь (рекомендуемый — сразу с подтверждением, см. шаг 4).** Если места ещё нет,
шаги 3 и 4 делаются одной командой. Если оператор подтверждать не готов:
```bash
docker exec -i dev-web-1 python manage.py promote_tenant_location --slug <slug>           # сухой прогон
docker exec -i dev-web-1 python manage.py promote_tenant_location --slug <slug> --apply   # место review_required
```

**Путь (форма).** «Места оказания услуг» → Add (`tenants/admin.py:68-115`):
`tenant`, `label`, `address`, `city`; `status` оставить `review_required`.

**Проверка.** `clean()` отказывает: пустой адрес, половина пары координат, (0, 0),
confirmed без кто/когда/основание (`tenants/service_location.py:204-225`). В базе —
`servicelocation_confirmed_requires_provenance` (`:142-152`).

**Не делать.** Не ставить `status=confirmed` из инлайна «Места оказания услуг» на форме тенанта (`tenants/admin.py:55-62`) —
500 (см. «Известные 500»).

**Readback.**
```bash
docker exec -i dev-web-1 python manage.py shell -c \
  "from collections import Counter; from tenants.models import ServiceLocation as L; q=L.objects.filter(tenant__slug='<slug>'); print(q.count(), Counter(q.values_list('status', flat=True)), Counter(q.values_list('geocode_status', flat=True)))"
```

### Шаг 4. Подтверждение места

**Путь, если места нет:**
```bash
docker exec -i dev-web-1 python manage.py promote_tenant_location --slug <slug> --confirm --by <username оператора> --source-ref "<основание>"
docker exec -i dev-web-1 python manage.py promote_tenant_location --slug <slug> --confirm --by <username оператора> --source-ref "<основание>" --apply
```
Команда ставит `confirmed_by`, `confirmed_at=now`, `confirmed_source_ref` и зовёт
`full_clean()`.

**Путь, если место уже есть** (команда на существующий адрес печатает «уже есть
место с этим адресом … — дубль не создаётся» и **не** подтверждает): отдельная
страница места → раздел «Подтверждение места (§9)» → `status=confirmed`,
`confirmed_by`, `confirmed_at`, `confirmed_source_ref` — все три поля
(`tenants/admin.py:98-105`).

Действия «Подтвердить место (я)» в админке каталога нет.

**Координаты.** Без DaData место подтверждается без координат: ограничение базы
требует только кто/когда/основание. Такое место `participates_in_distance=False`
(`service_location.py:194-202`) — после DRF-1977 итог `tenant_readiness` для такого салона — `READY_WITHOUT_DISTANCE` (на `b9d1906` команды ещё нет, см. шаг 13).

**Readback.**
```bash
docker exec -i dev-web-1 python manage.py shell -c \
  "from tenants.models import ServiceLocation as L; q=L.objects.filter(tenant__slug='<slug>', status='confirmed', confirmed_by__isnull=False, confirmed_at__isnull=False).exclude(confirmed_source_ref=''); print('confirmed', q.count(), 'with_coords', q.filter(latitude__isnull=False, longitude__isnull=False).count())"
```
Ожидаемо: `confirmed ≥ 1`.

### Шаг 5. SpecialistProfile → works_at

**Путь.** Форма тенанта → инлайн «Мастера салона» (`users/admin.py:390-428`):
`user` (мастер из шага 1), `display_name`, `experience_years`; «Кого видит
клиент» — `status`, `is_available`, `is_booking_enabled`; «Где работает» —
`works_at` (id подтверждённого места из шага 4); «Приём записей» — `timezone`,
`booking_source`. Инлайн подхватывает профиль, созданный сигналом (`:349-387`).

`status` оставить «Черновик» до конца шага 9; публикация — действием
«✅ Подтвердить мастеров (→ active)» (`users/admin.py:111`) на шаге 9. Действие есть только в списке профилей мастеров (`/admin/users/specialistprofile/`, `users/admin.py:490`), не на форме тенанта.

**Проверка.** Профиль, уже принадлежащий другому салону, — отказ (`:363-372`);
`works_at` чужого салона — отказ «Это место принадлежит другому салону…»
(`users/models.py:380-400`). Ворот готовности у мастера салона нет
(`users/publication.py:262-273` — только для `kind=solo`).

**Ловушка.** `timezone` — свободная строка без проверки (`users/models.py:283-286`):
опечатка проходит молча. Readback считает невалидные.

**Readback.**
```bash
docker exec -i dev-web-1 python manage.py shell -c \
  "from zoneinfo import available_timezones as az; from users.models import SpecialistProfile as P; M=P.objects.filter(tenant__slug='<slug>'); z=az(); print('masters', M.count(), 'works_at_own', M.filter(works_at__tenant__slug='<slug>').count(), 'role_specialist', M.filter(user__role='specialist').count(), 'bad_tz', sum(m.timezone not in z for m in M))"
```
Ожидаемо: `masters = works_at_own = role_specialist`, `bad_tz 0`.

### Шаг 6. Рабочие часы

**Путь (рекомендуемый — пресет).** Список профилей мастеров (`/admin/users/specialistprofile/`) → отметить
мастеров → действие **«🕘 Поставить расписание Пн–Пт 10:00–19:00»**
(`users/admin.py:209`). Первая отправка показывает, у кого часы уже есть; запись —
только после подтверждения; перезапись — отдельная галочка, по умолчанию выключена.
Пишет 7 строк (Пн–Пт 10–19, Сб/Вс выходные) и сбрасывает кэш слотов.

**Путь (инлайн).** Форма профиля → «Рабочие часы» (`appointments/admin.py:173-196`)
— когда часы салона не Пн–Пт 10–19.

**Проверка.** `WorkingHoursInlineForm.clean()`: у выходного нет времён; у рабочего
дня есть начало и конец; начало раньше конца; правила перерыва
(`appointments/admin.py:122-170`).

**Не делать.** «Рабочие часы → Добавить» — 500 (см. «Известные 500»).

Исключения по датам и закрытия салона — форм в админке нет; пишутся через
салонный API `/api/v1/tenants/me/…` (нужен шаг 10).

**Readback.**
```bash
docker exec -i dev-web-1 python manage.py shell -c \
  "from appointments.models import SpecialistWorkingHours as W; from users.models import SpecialistProfile as P; M=P.objects.filter(tenant__slug='<slug>'); ok=W.objects.filter(specialist__in=M, is_working_day=True, start_time__isnull=False, end_time__isnull=False).values('specialist').distinct().count(); print('masters', M.count(), 'with_working_day', ok)"
```
Ожидаемо: `with_working_day = masters`. Движок записи читает эти же строки
каталога (`users/internal_catalog_api.py:248-272`); проверка чтения — на шаге 13.

### Шаг 7. SalonService

**Путь.** «Salon services» → Add (`services/admin.py:193-218, 310-337`): `tenant`,
`template` (id канонического шаблона, если есть), `category`, `name`,
`duration_minutes`, `base_price`, **`requires_health_check`**, `is_active`.

**Проверка.** Без `template` нужна `category` (`services/models.py:893-897`);
`duration_minutes` 5–480; `base_price` ≥ 1 или пусто; уникальность
`(tenant, template, name)`.

**Цена 0 ₽.** Не продажная цена (раздел Q). `base_price` оставить пустым, 1 ₽ не
ставить. Сохранённый 0 ломает действия ревью маппинга (`services/mapping_review.py:75-86`).

**Readback.**
```bash
docker exec -i dev-web-1 python manage.py shell -c \
  "from services.models import SalonService as S; q=S.objects.filter(tenant__slug='<slug>', is_active=True); print('active', q.count(), 'price0', q.filter(base_price=0).count(), 'rhc_null', q.filter(requires_health_check__isnull=True).count(), 'no_template', q.filter(template__isnull=True).count())"
```
Ожидаемо: `price0 0`.

### Шаг 8. SpecialistService

**Путь.** Форма услуги салона → инлайн (`services/admin.py:156-166`): `specialist`
(id профиля мастера **этого** салона), `duration_minutes`, `price`,
`requires_health_check`, `buffer_after_minutes`, `is_active`.

**Проверка.** `price` обязателен, ≥ 1; длительность должна определяться
(«An active bookable service needs a resolvable duration.»,
`services/models.py:1070-1074`); уникальность `(specialist, salon_service)`.

**Ловушка.** Что мастер ребра — из этого же салона, **не проверяется**
(лист DRF-1976); `SpecialistServiceAdmin` вдобавок даёт править `tenant`
(`services/admin.py:516-526`). Ребро заводить только из инлайна услуги, мастера
сверять readback'ом.

**Readback.**
```bash
docker exec -i dev-web-1 python manage.py shell -c \
  "from django.db.models import F; from services.models import SpecialistService as E; q=E.objects.filter(salon_service__tenant__slug='<slug>'); print('edges', q.count(), 'cross_tenant_master', q.exclude(specialist__tenant_id=F('salon_service__tenant_id')).count(), 'tenant_mismatch', q.exclude(tenant_id=F('salon_service__tenant_id')).count(), 'price_below_1', q.filter(price__lt=1).count())"
```
Ожидаемо: `cross_tenant_master 0`, `tenant_mismatch 0`, `price_below_1 0`.

### Шаг 9. VERIFIED построчно, `requires_health_check` явно

**Путь (форма, построчно).** Открыть услугу салона:
- `template` — канонический шаблон; `mapping_status = verified`;
- `mapping_confirmed_by` — свой пользователь, `mapping_confirmed_at`,
  `mapping_source_ref` — основание;
- `mapping_confirmed_rule` и `mapping_rule_version` — **пусто**;
- **`requires_health_check` — «Да» или «Нет» явно**.

При сохранении пишется синоним шаблона с тем же провенансом
(`services/admin.py:436-513`). Форма отказывает без даты, основания, автора, при
«кто» и «правило» одновременно и при verified без шаблона (`:246-305`).

**Путь (с резолвером).** «Пересчитать резолвером (dry-run, в файл отчёта; базу не
трогает)» → «Подтвердить связь с ЕДИНСТВЕННЫМ кандидатом резолвера (VERIFIED, я —
подтверждающий)» (`services/admin.py:391, 428`). Действие **не выставляет**
`requires_health_check` (`services/mapping_review.py:131-140`) — после него
открыть форму и выставить явно.

**Не делать.** `map_salon_services --apply` (отказ по построению,
`map_salon_services.py:8-11`); `verify_pilot_slice` (зашит на formula-tela).

**Проверка здоровья.** `SpecialistService.resolved_requires_health_check()`
(`services/models.py:998-1068`) возвращает `None`, если шаблона нет и никто не
ответил; такое ребро запись отклоняет `HEALTH_CHECK_UNKNOWN`
(`appointments/application/services/_booking_guards.py:49-125`). Явный ответ
услуги салона закрывает `None` и без шаблона. Ворота читают шаблон и флаги, а не
`mapping_status`.

Затем — публикация мастеров: список профилей мастеров (`/admin/users/specialistprofile/`) → отметить мастеров салона → действие «✅ Подтвердить мастеров (→ active)» (`users/admin.py:490`).

**Readback.**
```bash
docker exec -i dev-web-1 python manage.py shell -c \
  "from collections import Counter; from services.models import SalonService as S, SpecialistService as E; t='<slug>'; q=S.objects.filter(tenant__slug=t, is_active=True); print(Counter(q.values_list('mapping_status', flat=True)), 'verified_no_template', q.filter(mapping_status='verified', template__isnull=True).count()); print('rhc', Counter(e.resolved_requires_health_check() for e in E.objects.filter(salon_service__tenant__slug=t, is_active=True).select_related('salon_service__template')))"
```
Ожидаемо: нужные услуги `verified`, `verified_no_template 0`, в `rhc` нет `None`.

### Шаг 10. Роль администратора (F10) и привязка личности

**10a. Роль — выполнять можно.**
```bash
docker exec -i dev-web-1 python manage.py provision_salon_admin --phone <телефон> --tenant <slug> --dry-run
docker exec -i dev-web-1 python manage.py provision_salon_admin --phone <телефон> --tenant <slug>
```
Нет пользователя с телефоном — заводит `role=admin`; есть — оставляет его роль.
Пишет `TenantUserRelationship(role=admin, is_active=True)` или повышает активную.
Идемпотентна; при отозванном доступе отказывает; тенант должен быть активен.
Привязку личности и строку `TenantStaff` в боте **не** создаёт.

**10b. Привязка личности — БЛОКЕР: нет пути identity binding для admin — DRF-1987.**
Салонный API бот зовёт с Bearer и `X-External-User-ID`
(`apps/integrations/ayla/salon_client.py:215-265`); каталог разрешает его в
пользователя (`users/authentication.py`), `IsTenantAdmin` ищет активную роль admin
у **этого** пользователя (`users/permissions.py:414-451`). Пока прокси-строка MAX
личности не связана с аккаунтом администратора — 403. Операторская привязка
admin-аккаунт не принимает (см. Prerequisites). Обход через `role=specialist` —
запрещён.

**Readback (10a).**
```bash
docker exec -i dev-web-1 python manage.py shell -c \
  "from users.models import TenantUserRelationship as R; print('active_admins', R.objects.filter(tenant__slug='<slug>', role='admin', is_active=True).count())"
```
После DRF-1987 — дополнить числом связанных прокси.

### Шаг 11. Подключение салона к боту

**Путь.** Админка бота → «Салоны» (`apps/tenancy/models.py:384`) → **«Подключить салон»** (`connect/`,
`apps/tenancy/admin.py:395-403`): «Slug», «Название салона», «Город» — те же, что
в шаге 2. Экран находит салон в каталоге по slug, заводит тенант бота с тем же
UUID, сразу запускает синк и показывает оценку (`apps/tenancy/onboarding.py:464-591`).

**Отказы.** Предпроверка токена провижининга (`provisioning_token_missing`,
`provisioning_token_equals_internal`, `onboarding.py:170-227`); `SETUP_PENDING`
на 403 каталога; другое название по тому же slug (409); UUID уже занят другим
slug. Ошибка синка подключение не откатывает.

Мастера приезжают `invite_status=PENDING`. Кнопка на карточке «Верифицировать N
мастеров» (`change_form.html:28`) — только для мастеров со связью с Ayla.

**Не делать.** Второй салонный бот не заводить: реестр отказывает при двух
`max_salon` (`apps/channels/bot_registry.py:268-282`, DRF-1705, решение владельца
R4 (а)). `MAX_BOT_SALON_TENANT_SLUG` не перенастраивать на новый салон.

**Readback (UUID с двух сторон).**
```bash
docker exec -i ayla-bot-staging-web-1 python manage.py shell -c \
  "from apps.tenancy.models import Tenant; t=Tenant.all_objects.get(slug='<slug>'); print(t.id, 'synced', t.last_catalog_sync_ok_at is not None)"
docker exec -i dev-web-1 python manage.py shell -c \
  "from tenants.models import Tenant; print(Tenant.all_objects.get(slug='<slug>').id)"
docker exec -i ayla-bot-staging-web-1 python manage.py sync_catalog --status
```
Ожидаемо: id совпадают, `synced True`. На карточке «Связь с Ayla» = «связан».

### Шаг 12. Приглашения сотрудников

**БЛОКЕР до выкладки DRF-1785 и снятия `MAX_BOT_SALON_TENANT_SLUG` (правило
владельца R4 (а)).** До этого коды сотрудникам нового салона не выдавать и в
салонный бот их не звать.

**Путь после снятия блокера.** Первый владелец — с хоста:
```bash
docker exec -i ayla-bot-staging-web-1 python manage.py issue_staff_invite --tenant <slug> --role owner --note "<для кого>"
```
Роли `owner | admin | receptionist | master`; для `master` — `--master-id`
существующего мастера каталога. Код `AYLA-XXXX` печатается **один раз**, хранится
только хэш; 7 дней; 5 попыток в час (`apps/identity/services/staff_invites.py`).
Человек вводит код в салонном боте. Дальше владелец выдаёт коды из Mini App
(`staff_invite_create`); код владельца — только владелец. `masters/invite/` для
мастеров каталога не использовать — он заводит нового `CatalogMaster`.
`--list-masters` не запускать в общий лог — печатает имена.

**Readback.**
```bash
docker exec -i ayla-bot-staging-web-1 python manage.py shell -c \
  "from collections import Counter; from apps.tenancy.models import Tenant, TenantStaff as S; t=Tenant.all_objects.get(slug='<slug>'); print(Counter(S.all_tenants.filter(tenant=t, deactivated_at__isnull=True).values_list('role', flat=True)))"
```

### Шаг 13. Готовность и readback

**После слияния DRF-1977:**
```bash
docker exec -i dev-web-1 python manage.py tenant_readiness --tenant <slug>
```
Итог — `READY`, `READY_WITHOUT_DISTANCE` (без DaData — ожидаемый итог) или
`NOT_READY` с названными причинами. Привязку к боту команда из каталога не видит и
пишет это строкой.

**До DRF-1977 — проекция руками (только числа):**
```bash
docker exec -i dev-web-1 python manage.py shell -c "
from collections import Counter
from django.db.models import F
from tenants.models import Tenant, ServiceLocation as L
from users.models import SpecialistProfile as P, TenantUserRelationship as R
from users.sellable import sellable_q
from appointments.models import SpecialistWorkingHours as W
from services.models import SalonService as S, SpecialistService as E
t=Tenant.all_objects.get(slug='<slug>'); print('tenant', t.is_active, t.kind, 'city', bool(t.city.strip()))
l=L.objects.filter(tenant=t); print('places', Counter(l.values_list('status', flat=True)))
m=P.objects.filter(tenant=t); s=m.filter(sellable_q(), user__is_active=True); print('masters', m.count(), 'sellable', s.count(), 'with_working_day', W.objects.filter(specialist__in=s, is_working_day=True, start_time__isnull=False, end_time__isnull=False).values('specialist').distinct().count())
q=S.objects.filter(tenant=t, is_active=True); print('services', q.count(), 'verified', q.filter(mapping_status='verified').count())
e=E.objects.filter(salon_service__tenant=t, is_active=True, salon_service__is_active=True, price__gte=1).filter(sellable_q('specialist')).select_related('salon_service__template'); print('edges', e.count(), 'rhc', Counter(x.resolved_requires_health_check() for x in e))
print('admins', R.objects.filter(tenant=t, role='admin', is_active=True).count())"
```

**Бот (без сетевого вызова):**
```bash
docker exec -i ayla-bot-staging-web-1 python manage.py shell -c \
  "from apps.tenancy.models import Tenant; from apps.tenancy.onboarding import assess_salon; t=Tenant.all_objects.get(slug='<slug>'); a=assess_salon(t); print(a.active_services, a.bookable_masters, a.masters_total, a.masters_awaiting_verification, a.reasons)"
```

## Verification — критерий владельца

| Звено | Что наблюдать |
|---|---|
| Салон связан с ботом | id тенанта в боте = id в каталоге; «Связь с Ayla» = «связан»; `sync_catalog --status` свежий |
| Администратор действует | вызов салонного API (`GET /api/v1/tenants/me/day/`) от его MAX-личности — 200, не 403. **Ждёт DRF-1987** |
| Мастера активны | каталог: `status=active`, `is_available`, `is_booking_enabled`; бот: «Бронируемых мастеров: N» |
| Услуги VERIFIED | readback шага 9: `verified`, без `None` в `rhc` |
| Часы читает движок записи | `GET /api/v1/internal/specialists/{id}/slots/?service_id=&date=` (`users/internal_catalog_api.py:248-272`) — непустые слоты в рабочий день |
| Клиент находит услугу | `/api/v1/internal/catalog/salon-services/?tenant=` и `…/specialist-services/`; в боте `CatalogService` активна |
| Видит реального мастера | `/api/v1/internal/specialists/?tenant=<uuid>`; у `CatalogMaster` бота `ayla_user_id` = user id каталога |
| Получает слот | непустые `slots` для `service_id` этого ребра |
| Создаёт реальную запись | `POST /api/v1/internal/appointments/` → строка `Appointment`; в логе нет `booking.health_gate.refused` |
| Салон видит запись | `GET /api/v1/tenants/me/day/?date=` и экран дня в Mini App администратора. **Ждёт DRF-1987 и DRF-1785** |

## Известные 500 и безопасные пути

1. **«Рабочие часы → Добавить».** `SpecialistWorkingHoursAdmin.form =
   WorkingHoursInlineForm`, в `Meta.fields` нет `specialist`
   (`appointments/admin.py:117-120, 199-205`) → сохранение без мастера → NOT NULL →
   500. Безопасно: пресет или инлайн на форме профиля (шаг 6).
2. **`status=confirmed` у места из инлайна тенанта.** В инлайне нет полей
   `confirmed_*` (`tenants/admin.py:60`); `clean()` вешает ошибки на отсутствующие
   поля → `ValueError` → 500. Безопасно: `promote_tenant_location --confirm` или
   отдельная страница места (шаг 4).

## Ловушки без 500

- `slug` редактируем после создания — не менять (DRF-1978).
- Мастер ребра из чужого салона не проверяется; `tenant` ребра правится в
  `SpecialistServiceAdmin` (DRF-1976).
- Действие резолвера не выставляет `requires_health_check`.
- `base_price = 0` ломает действия ревью маппинга.
- `timezone` мастера — без проверки IANA.
- `promote_tenant_location` на существующий адрес не подтверждает место.

## Escalation contacts

| Severity | Who | How to reach |
|---|---|---|
| P0 (блокер шага, 500, чужой салон задет) | Главное окно | чат главного окна |
| Решение продукта (роль, бот, цена) | Владелец | через главное окно |

## Post-mortem template

- **Что заводили** (slug, число мастеров/услуг — без имён и телефонов).
- **На каком шаге оператор ошибся или остановился** — это вход в проектирование
  wizard (раздел Q: «измерить, где оператор ошибается»).
- **Что ожидали — что получили** (readback до/после).
- **Время на шаг.**
- **Action items** (лист + владелец).

## Changelog

- 2026-09-15 — ayla-96 — первая версия по разделам Q/S, сверено с каталогом `b9d1906` и ботом `8bf99f7c`; блокеры DRF-1987 (шаг 10b) и DRF-1785 (шаг 12).
- 2026-09-15 — ayla-96 — шесть правок независимой сверки главного окна: раздел «Салоны», итог готовности после DRF-1977, инлайн «Места оказания услуг», `buffer_after_minutes`, где искать «Подтвердить мастеров», флаг `--provider dadata`.
