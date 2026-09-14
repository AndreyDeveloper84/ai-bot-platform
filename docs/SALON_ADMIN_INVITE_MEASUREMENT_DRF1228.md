# Замер до кода: приглашение администратора салона — роль в боте и в Ayla (DRF-1228)

**Окно:** ayla-89, 12.09.2026, по поручению главного окна («замер до кода, границы с M27 по provisioning не пересекать; если упирается в tenant-токен (A3) — назвать и остановиться на замере + листах»).
**База замера:** ai-bot-platform `dev adb5bafc` (#1709), beautygo_backend `dev a78782a7` (#425).
**Исход одной строкой:** половина «бот» из DRF-1228 уже построена другими задачами (DRF-1061/1160/1505/1227); половина «Ayla» не начата и **упирается в A3 и в M27** — здесь остановка на замере и листах.

Тикет написан 21.08 и описывает мир до DRF-1061 (block 2.4), DRF-1160, DRF-1227, DRF-1505, DRF-1784. Ниже — что из него уже есть, что есть в другой форме, чего нет, и где именно проходит граница, которую нельзя пересекать.

---

## 1. Половина «бот» — что уже есть (по коду, не по тикету)

| Пункт тикета | Состояние на `adb5bafc` | Где |
|---|---|---|
| «`views_invite.py:247` возвращает 400 на любую роль кроме master» | **Устарело как описание, верно как факт**: мастерская ручка по-прежнему принимает только `master`, но 400 теперь **направляет** на другую ручку: «use POST /api/v1/admin/staff/invite/ with the desired role» | `apps/admin_api/views_invite.py:465-470` |
| Приглашение администратора | **Есть.** `POST /api/v1/admin/staff/invite/`, роли `owner | admin | receptionist | master` (`ALLOWED_ROLES` = все `StaffInvite.Role`), 201 `{code, role, expires_at, invite_id, code_is_shown_once, invite_link}` | `apps/admin_api/views_staff_invite.py:90,160-165`; `apps/admin_api/urls.py:111-113` |
| Срок жизни 7 дней (Q-MM2) | **Есть.** `INVITE_TTL_DAYS = 7`, `expires_at = now + ttl_days` | `apps/identity/services/staff_invites.py:71,228-236` |
| Диплинк + запасная ссылка при незаданном `SITE_DOMAIN` | **Есть в другой форме.** Приглашение — **код** `AYLA-XXXX` (не токен в ссылке), `invite_link` — deep link бота с кодом (DRF-1505). Веб-запасной ссылки у кода нет и не нужно: код вводится в боте руками. Для мастерского приглашения запасная ссылка и её отказ по имени — DRF-1079 (#1712) | `views_staff_invite.py:45-48,260`; `staff_invites.py:68-71` |
| Идемпотентность 7 дней по `(tenant, name, contact_value)` | **Не применима**: у `StaffInvite` нет ни имени, ни контакта — код без адресата. Второй вызов выдаёт **второй код**; оба живы до срока или до погашения. Это осознанная модель DRF-1061 (код показывается один раз), а не пропуск | `apps/tenancy/models.py:612-690` |
| Погашение создаёт `TenantStaff(role=admin)` | **Есть.** `_grant_staff_role`: find-or-create активной строки `(tenant, bot_user, role)`, гонка → `already_had_role`, второй владелец → `OwnerAlreadyExists`. Путь без `BotUser` (незнакомец в салонном боте) — `redeem_staff_invite_by_identity` создаёт строку в тенанте кода (DRF-1784) | `staff_invites.py:538-597`; `:348-436` |
| Отзыв (DRF-1227) — «`deactivated_at` не выставляет никто» | **Устарело.** `revoke_staff_access` ставит `deactivated_at`, пишет audit внутри транзакции; владельца отозвать нельзя (`OwnerRevokeRefused`) | `apps/identity/services/staff_revoke.py:101-175` (#1222) |
| Отзыв «в живом пути» — деактивированный получает отказ на мутациях | **Есть на стороне бота.** `role_resolver` считает роли только по `deactivated_at IS NULL`; `require_admin_role` на ручках админки читает его | `apps/identity/services/role_resolver.py:309-316`; `apps/admin_api/auth.py` |
| Граница «receptionist не входит, сохранить 400» | **Нарушена молча**: `receptionist` **принимается** ручкой staff-invite и создаёт `TenantStaff(role=receptionist)`. Вилка `receptionist → staff` в тикете названа нерешённой владельцем. Это не дефект кода DRF-1061 (он ставил задачу иначе), но расхождение с текстом DRF-1228, которое надо назвать владельцу, а не выбирать за него | `views_staff_invite.py:90` |

**Обращений к Ayla из этого контура — ноль.** `grep -in "ayla" apps/identity/services/staff_invites.py apps/identity/services/staff_revoke.py` = 0 совпадений по коду (только слово `AYLA` в префиксе кода). Погашение и отзыв живут целиком в таблицах бота.

## 2. Половина «Ayla» — что есть и почему бот до неё не дотягивается

### 2.1 Кто в Ayla читает `TenantUserRelationship(role=admin)`

`IsTenantAdmin` (`users/permissions.py:414-452`): активная TUR `role=admin` у `request.user` в `request.tenant` (`X-Tenant`). Потребители — вся салонно-административная запись:

| Поверхность Ayla | Файл | Кто зовёт из бота (все строят `X-External-User-ID = bot:{channel}:{id}` через `external_user_id_for(bot_user)`) |
|---|---|---|
| создать/перенести/отменить/завершить запись | `tenants/appointments_api.py` (7 упоминаний) | `apps/admin_api/views_booking_create.py`, `views_booking_cancel.py`, `views_booking_complete.py` |
| день салона | `tenants/day_api.py` (7) | `views_salon_frame.py` |
| поиск клиента | `tenants/…customer-lookup` | `views_customers.py` |
| расписание/исключения/влияние | `users/schedule_admin_api.py` (`IsTenantAdminOrPlatformAdmin`), `tenants-master-schedule*` | `views_master_exceptions.py`, `views_schedule_impact.py` |
| отзыв связи | `tenants/relationships_admin_api.py` | (из бота не зовётся) |

Как Ayla узнаёт «кто нажал»: `IsBotServiceWithVerifiedClient` (`users/permissions.py:137-165`) — Bearer бота + `X-External-User-ID` → `resolve_external_user` (`users/services.py:92-128`): **прокси-`User` `bot:max:<id>` создаётся лениво при первом обращении**; если у прокси стоит `linked_user` — возвращается настоящая учётка. Затем `IsTenantAdmin` проверяет TUR **у того `User`, которого вернул резолвер**.

Обоснование «именно `IsTenantAdmin`, не платформенный админ» записано в самом файле (`tenants/appointments_api.py:100-135`): единственный shared Bearer без второго фактора пускал бы держателя в любой тенант через `X-Tenant`; атрибуция (`initiator_user_id`, `salon.booking_*`) — это `request.user` = администратор.

### 2.2 Кто в Ayla пишет `TUR(role=admin)`

`grep -rn "TenantUserRelationship.objects.\(create\|get_or_create\|update_or_create\)"` вне тестов и миграций — **5 мест**, ни одно не достижимо из бота:

| Писатель | Что пишет | Достижимо из бота |
|---|---|---|
| `users/management/commands/provision_salon_admin.py:121` | `TUR(admin)` **настоящему пользователю по телефону**; отказывает при отозванной строке | нет — ssh, оператор |
| `users/signals.py:74` | мост `user.role + user.tenant → TUR` (`staff`, не `admin` — T7 карты онбординга) | нет |
| `appointments/…/create_booking_service.py:593` | `TUR(customer)` при первой записи (#1014) | косвенно, и только `customer` |
| `appointments/management/commands/bootstrap_tech_tenant.py:385`, `bootstrap_e2e_wave1.py:92` | посев | нет |

**Внутренней ручки «выдать/снять TUR по внешней личности» в Ayla нет** (`djangoProject/urls.py:33-165`: `internal/tenants/` заводит салон, `internal/users/` — личность и субъектные чтения, `internal/specialists/*` — мастера). `M27 DRF-1828` (ayla-7b) добавляет `POST /internal/solo-workspaces/`, который создаёт TUR admin — **для соло и внутри своей ручки**, не общий грант.

### 2.3 Следствие — состояние на пилоте (по построению, до SQL)

Человек, погасивший `AYLA-XXXX` с ролью `admin`, получает `TenantStaff(admin)` в боте и **проходит** `require_admin_role` в Mini App. Но каждая из семи ручек бота из таблицы 2.1 несёт его `bot:max:<id>` в Ayla, где у прокси-`User` **нет** TUR admin → `IsTenantAdmin` → **403**. Иными словами: **приглашение админа в боте сегодня открывает экраны, но не действия** — ровно тот класс «Mini App построен, выложен и непригоден за пустой таблицей», который назван в докстринге `provision_salon_admin`.

Единственный обход — `provision_salon_admin --phone` **и** привязка прокси к этому телефону (`linked_user`): тогда `resolve_external_user` вернёт настоящую учётку, у которой TUR есть. Привязок на пилоте 12.09 — **0** (замер DRF-1790, `ayla_user_id_is_proxy`/`linked_user`), OTP-фаза 1 — DRF-1787, ждёт OD-OTP-PHASE1.

**Что снять SQL (главное окно, `taximeter@176.119.159.141`, оба контейнера, печатать hostname и `now()` рядом):**

```sql
-- бот: кто админ по кодам
SELECT t.slug, s.role, count(*) FROM tenancy_tenantstaff s
  JOIN tenancy_tenant t ON t.id = s.tenant_id
 WHERE s.deactivated_at IS NULL GROUP BY 1,2 ORDER BY 1,2;

-- каталог: кто админ по TUR и на какой учётке (прокси / привязанная / настоящая)
SELECT t.slug, r.role, u.is_proxy, (u.linked_user_id IS NOT NULL) AS linked, count(*)
  FROM users_tenantuserrelationship r
  JOIN tenants_tenant t ON t.id = r.tenant_id
  JOIN users_user u ON u.id = r.user_id
 WHERE r.is_active GROUP BY 1,2,3,4 ORDER BY 1,2,3,4;
```

Ожидание по построению: в боте ≥1 `admin`/`owner` на `formula-tela`; в каталоге TUR `admin` только у `is_proxy = false` (выданные `provision_salon_admin`), у прокси — ноль. Расхождение и есть число «прав, разошедшихся между двумя системами» из тикета. (Имена таблиц — по умолчанию Django `<app>_<model>`; `db_table` в моделях не задан — проверено grep.)

## 3. Где проходит граница, которую нельзя пересечь

### 3.1 A3 — tenant-токен

Любая новая внутренняя ручка Ayla, дающая роль в салоне, должна стоять под каким-то Bearer. Варианты и почему каждый упирается:

| Bearer | Почему нет / почему ждёт |
|---|---|
| `IsInternalBearer` (общий s2s) | тот самый «общий Bearer заводит роли» — класс, который каталог отвергает на старте для tenant-токена (`users.E002`, `users/checks.py:49-56`); утечка одного секрета = админ в любом салоне. Решение C1 владельца (`docs/C1_PROVISIONING_TOKEN_RESOLUTION_2026-09-11.md`) разводит силы по токенам — грант роли под общим Bearer идёт против него |
| `IsTenantProvisioningBearer` (A3) | по силе подходит (заводит салон → заводит его администратора), но **на пилоте пуст** в обоих контурах (C1 §, 11.09; #425 снял переходное правило 12.09) — ручка мертва до исполнения A3 владельцем; и это токен, под которым **M27** заводит соло-рабочие места — совместное владение классом `IsTenantProvisioningBearer` с ayla-7b |
| `IsIdentityProvisioningBearer` | боту запрещён явно (OPEN_DECISIONS §151), A3 требует убрать его из контура бота |
| четвёртый токен | против духа C1 (три уже путаются: «задан» без имени переменной — урок 11.09) |

Вывод: **выбор Bearer — решение владельца, не окна**; пока A3 не исполнен, ни один вариант не работает на пилоте.

### 3.2 M27 — provisioning соло (ayla-7b, DRF-1828…1831)

M27 создаёт `Tenant(kind=solo) + User W + SpecialistProfile + TUR admin` одной идемпотентной ручкой под `IsTenantProvisioningBearer` и снимает переходное правило (T6 карты онбординга). Пересечения с DRF-1228:

* **один и тот же класс права** (`IsTenantProvisioningBearer`) и **один и тот же побочный эффект** (TUR admin) — если DRF-1228 заведёт свою ручку гранта под тем же Bearer, у каталога станет два писателя TUR admin из бота с разной семантикой (соло — вместе с User W; салон — на прокси);
* **T7 карты**: мост `users/signals.py` даёт `staff`, а не `admin` — M27 обязан «явно создать TUR admin в той же транзакции»; тот же вывод для DRF-1228.

Правило дня от главного окна — не пересекать. Значит: **ручку гранта TUR для салонного администратора не заводить, пока M27 не слит**, а форму ручки согласовать с ayla-7b (общий сервис `grant_tenant_admin(user, tenant)` в каталоге, два вызова — из M27 и из DRF-1228).

### 3.3 Проблема, которой в тикете нет: TUR на прокси исчезает при привязке

`resolve_external_user` возвращает `linked_user`, если он есть. Если DRF-1228 выдаст TUR admin **прокси** `bot:max:X` (единственная учётка, которую бот может назвать до OTP), то в день, когда оператор/OTP привяжет прокси к настоящей учётке W, `request.user` станет W — **у которой этой TUR нет** → 403 снова, молча, у человека, у которого «вчера работало». Это тот же класс, что DRF-1790 (бот держал прокси-ключ навсегда): грант обязан **переезжать вместе с личностью** или выдаваться тому `User`, которого резолвер вернёт **после** привязки. Без этого «право, созданное в двух местах» разойдётся ещё и **внутри** Ayla.

Отсюда: детектор рассинхронизации из тикета («хуже отсутствующих прав») — не опция, а часть определения готовности; и он должен считать не «TenantStaff admin ↔ TUR admin», а «TenantStaff admin ↔ TUR admin **у того User, которого сегодня вернёт `resolve_external_user`**».

## 4. Листья (предложение; SP — оценка окна, очерёдность — владелец)

Не создаю в Linear до слова главного окна: три из пяти зависят от решений владельца.

| # | Лист | Репо | SP | Зависит от |
|---|---|---|---|---|
| L1 | **OD-1228-BEARER** — под каким Bearer каталог принимает грант/снятие роли салона по внешней личности; и **OD-1228-RECEPTIONIST** — вилка `receptionist → staff` (сегодня принимается молча) | реестр OD | — | владелец |
| L2 | Каталог: сервис `grant_tenant_role(user, tenant, role)` / `revoke_tenant_role(...)` — один писатель TUR из бота, идемпотентный, отказ при отозванной строке как в `provision_salon_admin`; ручка `POST/DELETE /api/v1/internal/tenants/<id>/roles/` под Bearer из L1; субъект — `X-External-User-ID` через `resolve_external_user` | beautygo | 3 | L1, **A3**, **после M27** (общий сервис с ним) |
| L3 | Бот: `_grant_staff_role` / `revoke_staff_access` → вызов L2 **в том же действии**, с именованным исходом: `TenantStaff.ayla_grant_state ∈ {granted, pending:<reason>}` (SETUP_PENDING по образцу DRF-1525, не тихий успех); экран Mini App показывает «роль в каталоге не выдана: <причина>» | ai-bot-platform | 3 | L2 |
| L4 | Переезд гранта при привязке: обработчик события `identity.rebound` (DRF-1790) повторяет L2 для настоящей учётки и снимает с прокси | ai-bot-platform + beautygo | 2 | L2, DRF-1787 / операторская привязка |
| L5 | Детектор рассинхронизации: чтение `GET /internal/tenants/<id>/roles/` и сверка с `TenantStaff` активными по `resolve_external_user`-семантике; строка в сводке здоровья контура (DRF-1500) и тест «роль в боте есть, в каталоге нет → названо» | ai-bot-platform + beautygo | 2 | L2 |

Итого ≈ 10 SP против 5 в тикете: половина «бот» уже сделана чужими задачами (−), но добавились L4 (проблема §3.3, которой 21.08 не было) и общий сервис с M27 (+).

**Что можно делать уже сейчас, не пересекая границ:** ничего кодом. Замер SQL §2.3 — главное окно; OD-1228-BEARER и OD-1228-RECEPTIONIST — в реестр; после ответа и после слияния M27 — L2.

## 5. Что этот замер не делал

* Не запускал SQL на пилоте (ssh закрыт для этого окна) — §2.3 даёт запросы, ожидание и что печатать рядом.
* Не открывал `nutrition/views.py:206-218` (прецедент «путь Б» из тикета): сегодняшний путь Б — это `IsBotServiceWithVerifiedClient` + `resolve_external_user`, он уже стоит на всех семи поверхностях §2.1; тикет описывает его же более старыми словами.
* Не проверял, что в боте делает роль `receptionist` в `role_resolver` — вопрос владельцу (OD-1228-RECEPTIONIST) предшествует.
