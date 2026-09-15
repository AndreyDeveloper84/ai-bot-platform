# Runbook: ручная настройка соло-мастера оператором (запасной ход)

> Status: **draft**
> Last exercised: _never_
> Target completion sprint: Pilot 2026-09-15 (DRF-1925, родитель DRF-1503)
> Owner: _Platform Lead_

Сверено с кодом: бот `dev` 8fc76a7c, каталог `dev` 1de8fde (15.09.2026). Каждая
ссылка ниже — файл и функция, а не пересказ. Прежний
[`solo-provider-bootstrap.md`](solo-provider-bootstrap.md) (май 2026) описывает
модель до M4/M8/M21 и для этой процедуры не источник.

## Purpose

Решение владельца 15.09 (DRF-1349): соло-мастера пилота настраиваются **сами** в
мастерской (экраны 02–08). Если на раннем этапе это идёт долго, оператор проводит
мастера от регистрации до публикации руками — **теми же путями каталога, что и
мастерская**: те же ручки под сторожем субъекта
(`IsInternalBearerForSpecialistSubject`), тот же журнал §96
(`privacy_audit.mixins.AuditedPersonalDataAccess`), те же ворота готовности
(`users/publication.py::publication_readiness`). Обходов нет ни одного; где у
мастерской пути ещё нет, это названо в шаге.

## Trigger / when to run

- Соло-мастер зарегистрировался в MAX-боте, но за оговорённый срок не дошёл до
  публикации, и главное окно / владелец дали слово настроить его вручную.
- Мастер сам просит помочь и **сам сообщил** данные: услуги, цены, длительности,
  адрес, часы, фото, текст «О себе». Без этого процедуру не начинать (см.
  «Что оператор НЕ делает»).

## Prerequisites

**Доступ.** Хост пилота `ruvds-o1mqo` (не близнец `ruvds-l2wyz` — там путь и имена
compose те же, а база замёрзшая). Контейнеры: бот `ayla-bot-staging-web-1`,
каталог `dev-web-1` (`Ayla/docs/HANDOFF_MAIN_WINDOW.md`, таблица контуров).
Админки: бот `api-dev.gobeauty.site/admin/`, каталог `dev.gobeauty.site/admin/`.
В каталоге для связывания нужно право `users.change_user`
(`users/admin_actions.py::PendingExternalIdentityAdmin.has_link_permission`).

**Секреты не печатать.** Только имя переменной, SET/EMPTY и длина.

**A3 — `AYLA_TENANT_PROVISIONING_TOKEN`.** Последний замер: **пуст в обоих контурах,
13.09.2026 13:20** (HANDOFF_MAIN_WINDOW). Замер стареет — переснять перед началом:

```bash
docker exec -i ayla-bot-staging-web-1 python manage.py shell -c \
  "from django.conf import settings as s; v=getattr(s,'AYLA_TENANT_PROVISIONING_TOKEN','') or ''; print('bot AYLA_TENANT_PROVISIONING_TOKEN', 'SET' if v else 'EMPTY', len(v))"
docker exec -i dev-web-1 python manage.py shell -c \
  "from django.conf import settings as s; v=getattr(s,'AYLA_TENANT_PROVISIONING_TOKEN','') or ''; print('catalog AYLA_TENANT_PROVISIONING_TOKEN', 'SET' if v else 'EMPTY', len(v))"
```

Бот читает переменную в `apps/catalog/services/http_client.py::provision_solo_workspace`,
каталог — в `users/permissions.py::IsTenantProvisioningBearer`. Задаёт токен
владелец; оператор его не заводит и не копирует между контурами.

**Оболочка бота для шагов 3–7.** Все запросы мастерской оператор делает тем же
клиентом, что и кабинет (`apps/integrations/ayla/booking_client.py`): Bearer
`AYLA_INTERNAL_API_TOKEN` и `X-External-User-ID` мастера клиент подставляет сам,
токен в оболочку не попадает.

```bash
docker exec -it ayla-bot-staging-web-1 python manage.py shell
```

```python
import uuid
from apps.identity.models import BotUser, SoloIdentityLink
from apps.integrations.ayla.booking_client import get_ayla_booking_client
from apps.integrations.ayla.user_proxy import external_user_id_for

BOT_MASTER_ID = "<CatalogMaster.id из админки бота>"
link = SoloIdentityLink.objects.select_related("master__tenant").get(master_id=BOT_MASTER_ID)
bot_user = BotUser.all_tenants.get(
    channel=link.channel,
    channel_user_id=link.channel_user_id,
    tenant_id=link.tenant_id_snapshot,
)
S = dict(
    specialist_id=str(link.catalog_specialist_id), external_user_id=external_user_id_for(bot_user)
)
c = get_ayla_booking_client()


def missing():
    return [m["code"] for m in c.get_publication_readiness(**S)["missing"]]
```

`specialist_id` — **`SoloIdentityLink.catalog_specialist_id`** (id профиля в
каталоге из ответа провижининга), а **не** `CatalogMaster.id`: у соло-мастера это
разные UUID (см. «Пределы», п. 1). `S` и `external_user_id` не печатать — там
MAX-идентификатор человека.

## Step-by-step procedure

Порядок 3–6 свободный: до связи (шаг 7) ручки пускают владельца своего
provisioned DRAFT-профиля (DRF-1874). Публикация (шаг 8) требует связи.
После каждого шага — `missing()`; коды из `users/publication.py`:

| код | раздел | закрывает шаг |
|---|---|---|
| `photo_missing`, `display_name_missing` | profile | 4 |
| `no_configured_service` | services | 3 |
| `location_not_assigned`, `location_inactive` | location | 5 |
| `no_working_day` | hours | 6 |
| `identity_not_linked` | identity | 7 |

### 1. Регистрация — делает мастер сам

Мастер пишет в MAX-бот и проходит регистрацию соло: `apps/channels/max/salon_handler.py`
→ `solo_onboarding.create_solo_provider` (Tenant, BotUser, CatalogMaster) →
`solo_identity_link.open_link` (`SoloIdentityLink` PENDING) →
`solo_catalog_provisioning.provision_catalog_workspace` → `solo_link_attempt.attempt_solo_link`.

**Проверка** — карточка `identity.SoloIdentityLink` в админке бота или в оболочке:

```python
print(
    link.status,
    link.catalog_provisioned_at,
    link.catalog_provisioning_refusal or "-",
    link.last_attempt_refusal or "-",
    link.master.tenant.slug,
)
```

**Откат** — нет: регистрацию за человека не делаем и не отменяем.

### 2. Каталожный workspace (Tenant + DRAFT-профиль)

Если `link.catalog_provisioned_at` заполнен — шаг пройден, дальше.

Иначе по `catalog_provisioning_refusal` (`solo_catalog_provisioning.py`, таблица в докстринге):

| причина | что делать |
|---|---|
| `token_missing` | у бота пуст A3. Владелец задаёт токен (в обоих контурах) → в админке бота раздел «Каталог салона», мастера (`CatalogMasterAdmin`), выбрать мастера, действие **«Соло: проверить связь с Ayla и подтвердить (§6)»** — `confirm_by_operator` повторит провижининг. |
| `refused` | каталог 403: у каталога токен пуст или не совпадает — владелец. |
| `conflict:<reason>` | 409 `claim_bound_elsewhere` / `tenant_id_taken` / `slug_taken` / `username_taken` (`tenants/solo_provisioning.py`) — стоп, в главное окно. |
| `client_error`, `transport_error` | повторить тем же действием позже. |

**Запасной ход без A3 — только по слову главного окна.** Тот же сервис, что за
ручкой `POST /api/v1/internal/tenants/solo-workspaces/` (`tenants/internal_api.py`),
из оболочки каталога. Он идемпотентен по claim и ничего не обновляет на
существующих строках. Значения — из бота (оболочка бота:
`t = link.master.tenant; print(t.id, t.slug, t.city)`; имя тенанта и отображаемое имя
ввести в каталоге вручную, не копируя в тикет).

```bash
docker exec -it dev-web-1 python manage.py shell
```

```python
from uuid import UUID
from tenants.solo_provisioning import provision_solo_workspace

ws = provision_solo_workspace(
    tenant_id=UUID("<bot Tenant.id>"),
    slug="<bot tenant slug>",
    name="<bot tenant name>",
    city="<bot tenant city>",
    external_user_id="bot:max:<channel_user_id>",
    display_name="<имя мастера>",
)
print(ws.created, ws.profile.id, ws.profile.status, ws.tenant.kind)
```

Ожидается `True <uuid> draft solo` (повтор — `False` с тем же id). В этом случае
`link.catalog_specialist_id` у бота останется пустым: в оболочке бота для шагов 3–8
подставить `specialist_id=str(ws.profile.id)` вручную. Поле связи руками **не
писать**; когда A3 задан, «Проверить связь» запишет его штатно (повтор
провижининга вернёт тот же workspace).

**Проверка** (оболочка каталога):

```python
from users.models import SpecialistProfile

print(
    list(
        SpecialistProfile.objects.filter(
            provisioned_external_user_id="bot:max:<channel_user_id>"
        ).values_list("id", "status", "tenant__slug", "tenant__kind")
    )
)
```

**Откат** — удалением не делается. DRAFT-профиль клиентам не виден; удаление
workspace (Tenant, User `solo:<slug>`, профиль) — отдельная операция по решению
владельца, не этот runbook.

### 3. Услуги, цены, длительности (M8)

Шаблоны — из канона каталога (`services.models.ServiceTemplate`). Чтение в
оболочке каталога: `ServiceTemplate.objects.filter(name__icontains="<название>").values_list("id", "name", "category__name")`.
Какие услуги — сообщает мастер.

```python
st = c.select_services(**S, template_ids=["<template uuid>", "..."])
for s in st["services"]:
    print(s["salon_service_id"], s["name"], s["mapping_status"], s["configured"])

c.put_service_offer(
    **S, salon_service_id="<salon_service_id>", price="1500.00", duration_minutes=60
)
```

Выбор идемпотентен (`services/offer_selection.py::select_templates`); цена ≥ 1,
длительность 5…480 (`services/internal_offer_api.py::_OfferBody`). Отказы:
409 `SERVICE_SELECTION_REFUSED` с `reason`, 400 `template_not_found` /
`service_not_selected`.

**Проверка:** `st = c.get_service_selection(**S); print(st["selected"], st["configured"])` —
`configured ≥ 1`; в `missing()` нет `no_configured_service`.

**Откат:** `c.remove_service(**S, salon_service_id="<id>")` — ответ `removal`:
`deleted` или `deactivated` (строку держат записи или решение модератора);
409 `HAS_APPOINTMENTS` с `count` — будущие записи, не трогать. Цену вернуть тем же
`put_service_offer` с прежними значениями (записать их до изменения из
`get_service_selection`).

### 4. Профиль: имя, «О себе», фото (M21)

```python
p = c.patch_specialist_profile(
    **S, display_name="<как мастер себя называет>", bio="<текст мастера, ≤ 500>"
)
print(bool(p["display_name"]), len(p["bio"] or ""), bool(p["avatar_url"]))

with open("/tmp/avatar.jpg", "rb") as fh:  # файл прислал сам мастер
    p = c.upload_specialist_avatar(
        **S, filename="avatar.jpg", content=fh.read(), content_type="image/jpeg"
    )
print(bool(p["avatar_url"]))
```

Лимиты — у каталога (`users/internal_specialist_profile_api.py`): «О себе» ≤ 500,
фото квадратное, ≤ 5 МБ; отказ 400 с `details.reason` (`not_square`,
`unsupported_type`, …). Ручки пишут журнал §96 (`WRITE_SPECIALIST_PROFILE`,
`UPLOAD_MEDIA`) сами. Файл фото после загрузки удалить из контейнера (`rm /tmp/avatar.jpg`).

Портфолио («работы») в готовность не входит; метода у клиента бота нет до M22
(DRF-1814) — оператор его не загружает.

**Проверка:** в `missing()` нет `photo_missing` / `display_name_missing`.

**Откат:** текст — тем же `patch_specialist_profile` с прежними значениями; фото —
заменить новым файлом от мастера. Удаление фото (`DELETE …/media/avatar/`, операция
`DELETE_MEDIA` журнала — fail-closed) у клиента бота метода не имеет: до M22 не
удалять; очистка поля `avatar` в админке каталога **запрещена** (мимо журнала).

### 5. Место оказания услуг (§9) и геокод

Отдельной ручки мастерской для места нет (выезд / «весь город» — M11, экран места —
M16), поэтому путь — админка каталога и команда геокодера; оба — штатные пути
каталога со своими сторожами.

1. **Место.** Админка каталога → «Места оказания услуг» (`ServiceLocationAdmin`) →
   добавить: `tenant` = UUID соло-тенанта, `label`, `address`, `city` — адрес,
   который **подтвердил мастер**.
2. **Подтверждение.** `status = confirmed` и все три поля происхождения:
   `confirmed_by` (свой пользователь), `confirmed_at`, `confirmed_source_ref`
   (основание без персданных, например `DRF-1925: адрес подтверждён мастером в MAX <дата>`).
   Без трёх полей не пропустит `clean()` модели, а `update()` мимо формы — ограничение
   `servicelocation_confirmed_requires_provenance`.
3. **Геокод.** Координаты руками не вводятся (поля только на чтение).

   ```bash
   docker exec -i dev-web-1 python manage.py geocode_locations --provider <dadata|nominatim> --slug <solo-slug>
   docker exec -i dev-web-1 python manage.py geocode_locations --provider <dadata|nominatim> --slug <solo-slug> --apply
   ```

   Сначала сухой прогон, потом `--apply`. Провайдер готов, если задано: `dadata` —
   ключ DaData в окружении каталога (`core/geocoding/providers/dadata.py::check`), `nominatim` — `NOMINATIM_BASE_URL`; `yandex` отказывает всегда
   (лицензия). Выход 2 — провайдер не готов, отказал посреди прогона или строк больше
   50; выход 1 — есть `ambiguous` («ЖДУТ ЧЕЛОВЕКА»): поверхности подтверждения
   координат нет — стоп, в главное окно. `--overwrite-ok` не использовать.
4. **Привязка мастера.** Админка каталога → профиль специалиста → поле `works_at`
   (raw id) = id места. Больше ничего на форме профиля не менять (см. «НЕ делает»).

**Проверка:** в `missing()` нет `location_not_assigned` / `location_inactive`.
С DRF-1957 готовность «к проверке» координат и подтверждения места не требует: место
в `review_required` отправляется. Одобрение модератором (`approve_specialists`) само
подтверждает своё место мастера (`confirmed`, «модерация профиля <id>»); не хватает
только этого — в отказе модератору `location_not_confirmed`. Расстояние до места
считается после геокода: `confirmed` ∧ `geocode_status ∈ {ok, confirmed}` ∧ обе
координаты ∧ не (0, 0) (`tenants/distance.py::participating_place_q`).

**Откат:** очистить `works_at` на профиле; место — `status = inactive` (не удалять).

### 6. Рабочие часы (M23)

Часы сообщает мастер. Все 7 дней обязательны (`users/schedule_api.py::SchedulePutSerializer`),
0 = пн … 6 = вс; перерыв внутри смены.

```python
before = c.get_working_hours(**S)["schedule"]  # сохранить для отката
week = [
    {
        "day_of_week": d,
        "is_working_day": d < 5,
        "start_time": "10:00" if d < 5 else None,
        "end_time": "19:00" if d < 5 else None,
    }
    for d in range(7)
]
print(c.put_working_hours(**S, schedule=week)["timezone"])
```

Отказ 409 `HAS_ACTIVE_APPOINTMENTS` — сокращение оставило бы живые записи вне часов
(`users/internal_schedule_api.py`).

**Проверка:** в `missing()` нет `no_working_day`.

**Откат:** `c.put_working_hours(**S, schedule=[{k: v for k, v in d.items() if k != "day_name"} for d in before])`.

### 7. Связь личности (LINKED)

Двумя действиями, оба штатные (`apps/identity/services/solo_identity_link.py`, докстринг):

1. **Каталог.** Админка → «Внешние личности без связи с Ayla (ждут оператора)» (`PendingExternalIdentity`) →
   выбрать **одну** строку `bot:max:<id>` (сверить с `SETUP_PENDING` / карточкой
   `SoloIdentityLink` в админке бота) → действие **«Связать с Ayla (выбрать мастера)»**
   → выбрать профиль рабочего аккаунта `solo:<slug>` → подтвердить. Сервис —
   `users.services.bind_external_identity_by_operator`, актор — вы, пишется в журнал.
   Отказы по имени: «Человек не найден», «Уже связан», «Нет прокси-строки».
2. **Бот.** Админка → раздел «Каталог салона», мастера (`CatalogMasterAdmin`) → выбрать мастера → **«Соло: проверить связь с
   Ayla и подтвердить (§6)»**. Ответ «Связано: 1» или «Остались в ожидании — …: <причина>»
   (`proxy_identity` — шаг 1 не сделан).

**Проверка:** `link.refresh_from_db(); print(link.status, link.provenance)` → `IDENTITY_LINKED OPERATOR_VERIFIED`;
в `missing()` нет `identity_not_linked`.

**Откат:** каталог, оболочка —
`from users.services import unlink_external_identity; print(unlink_external_identity("bot:max:<id>")[1])`
(аудит в той же транзакции; HTTP-ручки нет намеренно). В боте действия снятия LINKED нет:
`reject_solo_identity_link` пропускает связанных, а `CatalogMaster.ayla_user_id` остаётся
(`solo_ayla_link.py`). После снятия в каталоге ворота вернут `identity_not_linked`, и
публикация / одобрение откажут. Состояние бота — в главное окно, руками не править.

### 8. Публикация (M4/M5) и одобрение модератором

Только когда мастер **явно** согласился выйти к клиентам (сообщение мастера — основание в тикете).

```python
print(missing())  # ожидается []
cmd = str(uuid.uuid4())
print("command_id", cmd)  # записать в тикет
print(c.publish(**S, command_id=cmd))  # DRAFT → PENDING
print(c.get_publication_status(**S))
```

Повтор с тем же `command_id` безопасен (та же строка, без перехода). Отказы:
409 `PUBLICATION_NOT_READY` с `missing`; 409 `PUBLICATION_REFUSED` (`command_id_reused`,
`salon_publication_owner_managed`, `no_workspace_tenant`).

**Одобрение** — модератор (лучше не тот же человек): админка каталога → профили
специалистов → **«✅ Подтвердить мастеров (→ active)»** (`users/admin.py::approve_specialists`).
Для соло действие само проверяет ту же готовность (`users/publication.py::approval_refusal`);
неготовый остаётся в статусе с сообщением «Не подтверждены — соло-мастер не готов…».

**Зеркало бота:**

```bash
docker exec -i ayla-bot-staging-web-1 python manage.py sync_catalog --tenant <solo-slug> --dry-run
docker exec -i ayla-bot-staging-web-1 python manage.py sync_catalog --tenant <solo-slug>
```

**Проверка:** статус профиля `active`; в оболочке бота
`CatalogMaster.all_tenants.filter(tenant__slug="<solo-slug>").values_list("id", "is_active")` —
**одна** строка, `is_active=True` (зеркало ставит его только при `status == active`,
`apps/catalog/services/http_client.py::_parse_specialist`). Две строки — стоп, в главное
окно (класс DRF-1507, склейка по `ayla_user_id`).

**Откат:** модератор — **«❌ Отклонить мастеров (→ draft)»** (`reject_specialists`),
затем `sync_catalog --tenant <solo-slug>`.

## Что оператор НЕ делает за мастера

- **Не регистрирует и не принимает за него.** Регистрация — только из MAX мастера. Живое
  приглашение с токеном принять за человека нельзя даже суперпользователю
  (`apps/catalog/services/verification.py::has_live_invite`, решение владельца 08.09).
- **Не придумывает данные.** Услуги, цены, длительности, адрес, часы, фото и «О себе» —
  только сообщённые мастером. `confirmed_source_ref` называет подтверждение мастера, а не
  «так было в анкете». Пресет **«🕘 Поставить расписание Пн–Пт 10:00–19:00»** соло-мастеру
  не ставить, если мастер не назвал ровно эти часы.
- **Не публикует без явного согласия мастера** и не одобряет свою же публикацию, если
  есть второй модератор.
- **Не обходит ворота.** Поле `status` на форме профиля специалиста в админке каталога не
  править (ACTIVE — только действием одобрения, оно проверяет готовность); никаких
  `update()` / `save()` в оболочке по `SpecialistProfile`, `SpecialistWorkingHours`,
  `SalonService`, `SpecialistService`, `ServiceLocation`, `SoloIdentityLink`,
  `CatalogMaster`.
- **Не обходит субъекта и журнал.** Не зовёт ручки мастерской чужим Bearer или без
  `X-External-User-ID`; не чистит `avatar` / портфолио в админке (мимо `DELETE_MEDIA`
  журнала §96); не выгружает и не пересылает фото и тексты мастера за пределы процедуры.
- **Не подтверждает координаты руками** (`ambiguous` — стоп) и не запускает геокодер
  шире `--slug` соло-тенанта.
- **Не связывает чужую личность.** Одна строка `bot:max:<id>` — один мастер, сверка с
  `SoloIdentityLink`; перепривязка — только через `unlink_external_identity`.
- **Не трогает секреты.** A3 и прочие токены задаёт владелец; в тикет, чат и лог — только
  имя переменной, SET/EMPTY и длина. Dev-bypass (`DEBUG`) на пилоте не существует и не
  используется.
- **Не пишет персданные в тикет.** В Linear — id мастера в боте, slug, коды `missing`,
  `command_id`, время шагов; не телефон, не MAX-id, не адрес, не текст «О себе».

## Verification

Процедура закончена, когда одновременно:

1. `c.get_publication_readiness(**S)` → `{"status": "READY", "missing": []}`;
2. `c.get_publication_status(**S)` → профиль `active`;
3. в зеркале бота одна строка мастера соло-тенанта, `is_active=True`;
4. в тикете DRF-1925 (или листе мастера) записаны: шаги с временем, `command_id`, кто одобрил.

## Пределы (что runbook не закрывает)

1. **Мастерская соло-мастера шлёт в каталог id строки бота.** Прокси кабинета
   (`apps/master_api/views.py`: M8, M21, M23, M5) передают `specialist_id=str(master.id)`,
   а строка соло-мастера в боте заведена с `uuid4`
   (`solo_onboarding.create_solo_provider`), id каталожного профиля лежит только в
   `SoloIdentityLink.catalog_specialist_id`. Вывод по коду 15.09, не замер и не тест;
   передан главному окну. Поэтому оператор здесь подставляет `catalog_specialist_id` явно,
   а не зовёт ручки кабинета.
2. Портфолио и удаление фото — после M22 (DRF-1814).
3. Место «выезд / весь город» — после M11; экран места — M16.
4. Снятие LINKED в боте — действия нет (шаг 7, откат).

## Escalation contacts

| Severity | Who | How to reach |
|---|---|---|
| Стоп-условие шага (409 conflict, `ambiguous`, две строки зеркала, отказ ворот после одобрения) | Главное окно | сообщение с кодом отказа и шагом |
| Секреты (A3, ключи геокодера) | Владелец | DRF-1349 |
| Персданные / журнал §96 | Владелец | DRF-1349 |

## Post-mortem template

- **Мастер** (id в боте, slug) и **почему руками**, а не сам.
- **Какие шаги**, сколько заняло, какие отказы по имени.
- **Что не совпало с runbook** — править runbook тем же PR, что и находку.
- **Action items** (owner + deadline).

## Changelog

- _2026-09-15_ — ayla-96 — первая версия по коду бот `dev` 8fc76a7c / каталог `dev` 1de8fde (DRF-1925).
