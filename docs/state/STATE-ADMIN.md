# СОСТОЯНИЕ · Админка, заведение салонов и мастеров

Формат и правила — `docs/state/README.md`. Проверить всё:
`python tools/state_check.py --file admin`.

**Два разных места.** Салон и мастера заводятся **в Ayla**
(`djangoproject-catalog` / `beautygo_backend`, Django + Unfold). Админка бота
(`ai-bot-platform`) — read-only зеркало плюс платформенные действия. Поэтому
часть фактов ниже снята в одном репозитории, часть в другом; репозиторий назван
в поле `снято`.

---

## Работает

### Ayla заводит салон и мастеров одной формой (DRF-1596)
статус:   РАБОТАЕТ
где:      tenants/admin.py:23 — `inlines = (TenantMastersInline,)`
проверка: git grep -c "TenantMastersInline" origin/dev -- tenants/admin.py
ожидание: >0
снято:    2026-09-08 @ ab76e6a1 (djangoproject-catalog)
задача:   DRF-1596

### Inline мастера обходит сигнал создания профиля
статус:   РАБОТАЕТ
где:      users/admin.py:214 — `class TenantMastersInline(admin.StackedInline)`
проверка: git grep -c "class TenantMastersInline" origin/dev -- users/admin.py
ожидание: =1
снято:    2026-09-08 @ ab76e6a1 (djangoproject-catalog)
задача:   DRF-1596

### Адрес и город принадлежат салону (DRF-1587)
статус:   РАБОТАЕТ
где:      tenants/admin.py — раздел «Адрес» (`city`, `address`)
проверка: git grep -c "address" origin/dev -- tenants/admin.py
ожидание: >0
снято:    2026-09-08 @ ab76e6a1 (djangoproject-catalog)
задача:   DRF-1587

### Бот: экран подключения салона к контуру
статус:   РАБОТАЕТ
где:      apps/tenancy/admin.py:98 — `SalonConnectForm` (slug, name, Ayla Tenant UUID, city)
проверка: git grep -c "SalonConnectForm" origin/dev -- apps/tenancy/admin.py
ожидание: >0
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   DRF-1525

### Экран подключения показывает названную причину невидимости салона
статус:   РАБОТАЕТ
где:      apps/tenancy/admin.py:130 — read-only поле `salon_visibility_state`
проверка: git grep -c "salon_visibility_state" origin/dev -- apps/tenancy/admin.py
ожидание: >0
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   DRF-1511

### Кнопка верификации мастеров живёт прямо на экране подключения
статус:   РАБОТАЕТ
где:      apps/tenancy/admin.py:184 — `VERIFY_FIELD = "verify_masters_of"`
проверка: git grep -c "VERIFY_FIELD" origin/dev -- apps/tenancy/admin.py
ожидание: >0
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   DRF-1553

### Подключение салона доступно только суперпользователю
статус:   РАБОТАЕТ
где:      apps/tenancy/admin.py:368 — `if not request.user.is_superuser: raise PermissionDenied`
проверка: git grep -c "is_superuser" origin/dev -- apps/tenancy/admin.py
ожидание: =1
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   DRF-1525

### Гейт продажи мастера — одно определение на весь код
статус:   РАБОТАЕТ
где:      apps/catalog/master_state.py:278
проверка: git grep -c "AVAILABLE = ADMITTED & LINKED_TO_AYLA" origin/dev -- apps/catalog/master_state.py
ожидание: =1
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   DRF-1540

### Построчная причина «почему не продаётся» — тоже одна
статус:   РАБОТАЕТ
где:      apps/catalog/master_state.py:417 — `sale_block()`
проверка: git grep -c "def sale_block" origin/dev -- apps/catalog/master_state.py
ожидание: =1
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   DRF-1506

### Синхронизация НИКОГДА не пишет `invite_status`
статус:   РАБОТАЕТ
где:      apps/catalog/services/upserter.py:160 — упоминание есть только в комментарии
проверка: git grep -c "\"invite_status\"" origin/dev -- apps/catalog/services/upserter.py
ожидание: 0
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   DRF-1496

### Синхронизация ПИШЕТ `ayla_user_id` — мост до Ayla держится ею
статус:   РАБОТАЕТ
где:      apps/catalog/services/upserter.py:204
проверка: git grep -c "\"ayla_user_id\": dto.user_id" origin/dev -- apps/catalog/services/upserter.py
ожидание: =1
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   DRF-1540

### Одноразовый токен приглашения не печатается в админке
статус:   РАБОТАЕТ
где:      apps/catalog/admin.py:228 — `exclude = ("invite_token",)`
проверка: git grep -c "exclude = (\"invite_token\",)" origin/dev -- apps/catalog/admin.py
ожидание: =1
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   DRF-1515

### Граница согласия мастера проведена по строке, а не по экрану
статус:   РАБОТАЕТ
где:      apps/catalog/services/verification.py:169 — `live_invite_q()`, тот же предикат в SQL
проверка: git grep -c "def live_invite_q" origin/dev -- apps/catalog/services/verification.py
ожидание: =1
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   DRF-1597

### Решение владельца о согласии процитировано в коде дословно
статус:   РАБОТАЕТ
где:      apps/catalog/services/verification.py:56
проверка: git grep -c "Согласие остаётся обязательным только для приглашённых извне" origin/dev -- apps/catalog/services/verification.py
ожидание: =1
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   DRF-1597

### Подтверждение владелицей салона пишет отдельный аудит-след
статус:   РАБОТАЕТ
где:      apps/catalog/services/verification.py:113 — `master.invite_verified_by_salon`
проверка: git grep -c "master.invite_verified_by_salon" origin/dev -- apps/catalog/services/verification.py
ожидание: >0
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   DRF-1597

### Очередь «ждут подтверждения» доехала до Mini App
статус:   РАБОТАЕТ
где:      apps/miniapp/src/screens/admin/AdminTeamScreen.tsx — экран «Команда»
проверка: git grep -c "getMastersAwaitingVerification" origin/dev -- apps/miniapp/src/screens/admin/AdminTeamScreen.tsx
ожидание: >0
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   DRF-1597

### Кнопка принудительной пересинхронизации каталога
статус:   РАБОТАЕТ
где:      apps/catalog/admin.py:59 — `actions = ("force_resync_selected_tenants",)`
проверка: git grep -c "force_resync_selected_tenants" origin/dev -- apps/catalog/admin.py
ожидание: >0
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   DRF-1581

### Кнопка пересинхронизации накрыта тестами
статус:   РАБОТАЕТ
где:      apps/catalog/tests/test_admin_force_resync_drf1581.py
проверка: git ls-tree -r --name-only origin/dev -- apps/catalog/tests/test_admin_force_resync_drf1581.py
ожидание: есть
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   DRF-1581

### Кнопка пересинхронизации ВЫЛОЖЕНА на боевой пилот
статус:   РАБОТАЕТ
где:      deploy-dev.yml, прогон 34232918815 на `8c276fa0`
проверка: gh run list --workflow deploy-dev.yml --limit 1 --json headSha,conclusion
ожидание: 8c276fa0e39e42110d3642d7bc1947b05e17c900
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   DRF-1581

### Утверждённый текст приглашения нового мастера существует
статус:   РАБОТАЕТ
где:      apps/miniapp/src/components/InviteMessage.tsx
проверка: git ls-tree -r --name-only origin/dev -- apps/miniapp/src/components/InviteMessage.tsx
ожидание: есть
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   §44.2

---

## Сломано или отсутствует

### Мастера, приехавшего синхронизацией, клиент не видит, пока человек не нажмёт кнопку
статус:   СЛОМАНО (латентно — на пилоте ещё не выстрелило)
где:      apps/catalog/migrations/0017_master_invite_default_pending.py — умолчание `pending`
проверка: git ls-tree -r --name-only origin/dev -- apps/catalog/migrations/0017_master_invite_default_pending.py
ожидание: есть
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   DRF-1496

### Завести мастера в боте нельзя вовсе — все зеркала запрещают добавление
статус:   СЛОМАНО (по замыслу, но выхода из него нет)
где:      apps/catalog/admin.py:45 — `has_add_permission` → `False`
проверка: git grep -c "def has_add_permission" origin/dev -- apps/catalog/admin.py
ожидание: =1
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   DRF-1496

### Завести салон без суперпользователя можно только через CLI на сервере
статус:   СЛОМАНО
где:      apps/tenancy/management/commands/create_tenant.py — второй и единственный путь
проверка: git ls-tree -r --name-only origin/dev -- apps/tenancy/management/commands/create_tenant.py
ожидание: есть
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   DRF-1525

### `SpecialistProfile.address` остался на форме мастера и дублирует салонный
статус:   СЛОМАНО
где:      users/admin.py:352 — `'fields': ('experience_years', 'address', 'location_lat', 'location_lng')`
проверка: git grep -c "'experience_years', 'address'" origin/dev -- users/admin.py
ожидание: =1
снято:    2026-09-08 @ ab76e6a1 (djangoproject-catalog)
задача:   DRF-1589

### Текста приглашения сотруднику по коду доступа нет — шов до сих пор `null`
статус:   СЛОМАНО
где:      apps/miniapp/src/screens/admin/AddPersonAccessCodeSection.tsx:74
проверка: git grep -c "INVITE_MESSAGE_TEMPLATE: ((r: StaffInviteResponse) => string) | null" origin/dev -- apps/miniapp/src/screens/admin/AddPersonAccessCodeSection.tsx
ожидание: =1
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   §30

### Ссылка-приглашение не привязана к приглашённому
статус:   СЛОМАНО (свойство механизма, признано в тексте владельца)
где:      apps/admin_api/views_invite.py — `master_invite_create`, привязка к первой пришедшей сессии
проверка: git grep -c "def master_invite_create" origin/dev -- apps/admin_api/views_invite.py
ожидание: =1
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   —

---

## В работе

### По направлению «админка / салон / мастер» открытых PR нет
статус:   В РАБОТЕ (пусто — и это факт, а не отсутствие данных)
где:      AndreyDeveloper84/ai-bot-platform, открытые PR
проверка: gh pr list --state open --limit 30
ожидание: ci/drf1605-deploy-guards
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   DRF-1605

### Сторож дрейфа выкладки (#1486) ещё не слит
статус:   В РАБОТЕ
где:      PR #1486, ветка `ci/drf1605-deploy-guards`, draft
проверка: gh pr view 1486 --json state
ожидание: OPEN
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   DRF-1605

---

## Ждёт владельца

### Из трёх условий готовности мастера реализовано одно — имя
статус:   ЖДЁТ ВЛАДЕЛЬЦА
где:      apps/catalog/master_state.py:385 — `_profile_is_filled()` проверяет только `name`
проверка: git grep -c "def _profile_is_filled" origin/dev -- apps/catalog/master_state.py
ожидание: =1
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   OD-MASTER-BOOKABLE / DRF-1521 п.6

### Признак «расписание подтверждено человеком» выразить сегодня нечем
статус:   ЖДЁТ ВЛАДЕЛЬЦА
где:      apps/catalog/master_state.py:397 — код сам это признаёт словами «выразить нечем»
проверка: git grep -c "выразить нечем" origin/dev -- apps/catalog/master_state.py
ожидание: =1
снято:    2026-09-08 @ 8c276fa (ai-bot-platform)
задача:   DRF-1521 п.6

### Правило старшинства адреса (салон / соло-мастер / профиль) не принято
статус:   ЖДЁТ ВЛАДЕЛЬЦА
проверка: нечем
почему:   отсутствие принятого решения нельзя доказать командой над кодом. Косвенный след — что `address` всё ещё на форме мастера (см. блок выше); прямого артефакта у решения нет ни в одном репозитории.
снято:    2026-09-08 @ ab76e6a1 (djangoproject-catalog)
задача:   DRF-1589

---

## Замер не снят

### Раскладка мастеров на боевом пилоте
статус:   НЕ ЗАМЕРЕНО
где:      боевая база `ayla-bot-staging-web-1`
проверка: нечем
почему:   доступа к прод-БД у этого слоя нет, а `git` состояние строк не видит. Цифры «34 мастера, все `accepted`, `AVAILABLE 31`» — чужой замер 08.09 из `bus/MASTER-INVITE-note-consent-mechanism.md`; у боевых замеров есть срок годности, цитировать как сегодняшний факт нельзя.
снято:    2026-09-08 (замер чужой, не воспроизводим отсюда)
задача:   —

### Живой проход подключения салона на бою
статус:   НЕ ЗАМЕРЕНО
где:      `api-dev.gobeauty.site`, экран `connect/`
проверка: нечем
почему:   это прогон человеком по боевому контуру, а не свойство кода. Записан незакрытым в `bus/ADMIN-note-DRF-1525-live-pass-pending.md`; проверяется только повторным проходом.
снято:    2026-09-08 (не проводился)
задача:   DRF-1525

### Учётка ресепшн на пилоте
статус:   НЕ ЗАМЕРЕНО
где:      `tenancy.TenantStaff` на боевой базе
проверка: нечем
почему:   замер 07.09 (одна строка, роль `owner`) снят чужим окном и относится к содержимому боевой базы. DRF-1552 проверена только тестами; заведение упёрлось в защиту от записи в боевую базу.
снято:    2026-09-07 (замер чужой)
задача:   DRF-1552

### Состояния трёх слитых задач в Linear
статус:   НЕ ЗАМЕРЕНО
где:      Linear, DRF-1581 / DRF-1596 / DRF-1597
проверка: нечем
почему:   Linear не читается командой из белого списка, а 08.09 рабочее пространство упёрлось в лимит 2500 запросов в час. По замеру того окна все три лежали в `Backlog` при слитом коде — но это снимок, не проверяемый отсюда.
снято:    2026-09-08 (замер чужой, частичный)
задача:   DRF-1581 / DRF-1596 / DRF-1597
