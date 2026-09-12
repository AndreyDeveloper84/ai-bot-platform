# Вебхук салонного бота без тенанта записи: замер до кода (DRF-1757, срез 4 DRF-1705)

**Дата:** 12.09.2026. **База:** `origin/dev` `25f1bb84` (после DRF-1755). **Только чтение
кода.** Решение владельца **D2 (OD-SALON-STRANGER → б)**: `BotUser` незнакомцу **не
создаётся** до доказанного пути — код приглашения либо «Я работаю сам».

Продолжение `SOLO_PATH_INPUTS_MEASUREMENT_DRF1705.md` §4 (срез 4). Срезы 1–3 (выходы
по потоку, резолвер Mini App «личность → рабочий тенант», кнопка «Открыть кабинет»)
закрыты или в PR; здесь — **вход вебхука**.

## Коротко

1. Тенант вебхука сегодня приходит из **трёх** мест подряд: `MAX_BOT_SALON_TENANT_SLUG`
   → `entry.tenant_slug` → `ingress/services._resolve_tenant` → `resolved_tenant_id`
   на стриме → `SalonMaxHandler.requires_tenant=True` → `tenant_scope(salon)` →
   `salon_handler` создаёт `BotUser` **в этом тенанте любому написавшему** (шаг 2
   замера соло-пути). Снять одну переменную — значит перевернуть все три ступени и
   **девять** мест в обработчике, которым нужен `bot_user` или `tenant` до того, как
   человек что-либо доказал.
2. Правило после D2: тенант — **от человека, не от записи**. Сначала рабочая строка
   личности (`resolve_working_bot_user`, уже есть после DRF-1755) → её тенант →
   прежний штатный поток. Нет рабочей строки → **путь незнакомца без `BotUser`**:
   код → тенант приглашения (по самому коду, `StaffInvite.all_tenants`) → строка
   создаётся **в тенанте кода**; «Я работаю сам» → `create_solo_provider` (строку
   и сегодня не требует); иначе — текст «введите код / Я работаю сам» и **ни одной
   строки**.
3. Прецедент уже в коде: национальный бот (`ingress:max_global`) — `requires_tenant =
   False`, `tenant_scope(None)`, личность под sentinel-тенантом `global_bot`. Вариант
   (а) «приёмная» был бы этим sentinel'ом; владелец выбрал (б), и sentinel салонному
   боту **не нужен**.
4. **6 SP** тремя срезами: 4a вход и тенант-от-человека (3), 4b путь незнакомца без
   строки (2), 4c снятие переменной, реестр, докстринги и стражи (1).

## 1. Где сегодня живёт тенант записи

| # | где | что делает | после D2 |
|---|---|---|---|
| 1 | `MAX_BOT_SALON_TENANT_SLUG` (env пилота) → `bot_registry._parse_bot_registry` → `entry.tenant_slug` | единственный источник | переменная снимается; запись салонного бота становится `is_tenant_less` |
| 2 | `apps/ingress/services.py:121-132` `_resolve_tenant` | `bot.tenant_slug` → `Tenant` → `resolved_tenant_id` стрима | для `max_salon` возвращает `None` (как для глобального) |
| 3 | `apps/channels/handlers.py:85-110` `SalonMaxHandler.requires_tenant = True`; докстринг «tenant-bound by construction» | база входит в `tenant_scope(salon)`; без тенанта — `TenantRequiredButMissing` при `STRICT_TENANT_REFUSE` | `requires_tenant = False`; `tenant_scope` входит сам обработчик **после** определения тенанта от человека |
| 4 | `salon_handler.py:512-518` | `current_tenant() is None` → ERROR `no_tenant_scope`, молчание | ветка исчезает; эталон переворачивается с датой |
| 5 | `salon_handler.py:520-525` `resolve_or_create_bot_user(...)` | строка в `current_tenant()` **любому** | вызывается только внутри `tenant_scope(тенант человека)` — для рабочей строки её не нужно создавать, для кода — в тенанте кода |
| 6 | `bot_registry.py:139` `is_tenant_less` = «национальный бот, тенант позже» | семантика маршрутизации | «тенант позже» для двух потоков; текст и стражи — 29 упоминаний `TENANT_SLUG`/«tenant-bound» в тестах `channels`/`ingress` (grep 12.09), переворачивать поимённо |
| 7 | `bot_user_resolver.py:74-75` (Mini App) | `entry.tenant_slug` → шаг 1 резолвера | шаг 0 (рабочая строка) уже впереди; для незнакомца — шаг 2 (`MAX_BOT_TENANT_SLUG` клиентского бота) → клиент салона, как сегодня |
| 8 | `admin_api/auth.py:154-160` 503 «Bot tenant not configured» | если ни записи, ни настройки | остаётся только как «реестр пуст вовсе» |

## 2. Девять мест обработчика, которым нужен `bot_user`/`tenant` до доказательства

`_handle_salon_event_inner` (`salon_handler.py:505-600`) сегодня получает `bot_user`
на шаге 5 и дальше раздаёт его всем. После D2 до доказательства пути строки **нет**:

| место | сегодня | после D2 |
|---|---|---|
| `_handle_master_invite(event, token, bot_user, tenant, entry)` `:396` | приглашение мастера ищется в `tenant_scope(salon)`; строка нужна для привязки | токен = `CatalogMaster.invite_token` (UUID), сегодня `validate_invite_token(token, tenant)` (`master_api/auth.py:224`) требует тенант; после D2 — тот же файл (он в реестре MKT1) отдаёт карточку по токену **без** тенанта, тенант = `master.tenant`; строка создаётся в нём **при принятии** |
| `/whoami` → `build_card(channel, channel_user_id)` | уже по личности; `tenant_slug=tenant.slug` для «в этом салоне» | без тенанта — карточка личности без раздела «здесь»; при рабочей строке — её тенант |
| `resolve_role(bot_user)` `:568` | роли строки в её тенанте | вызывается только для рабочей строки (`resolve_working_bot_user`) |
| `_is_button_tap` / `SOLO_REGISTER_CALLBACK` `:580-586` | нужна только `bot_user` для `_register_solo_provider` | по событию |
| `_register_solo_provider(event, bot_user, entry)` `:680` | читает `bot_user.channel/channel_user_id/display_name/id`; `attempt_solo_link(result.master, bot_user)` берёт `external_user_id_for(bot_user)` | всё есть в событии; `attempt_solo_link` получает `result.bot_user` (соло-строка) — это и правильнее: связь про кабинет, а не про салонную строку |
| `_ask_for_code_with_solo_offer(event, bot_user, entry)` `:652` → `_has_a_master_card_here(bot_user)` | карточка **в этом салоне** по строке | по личности: для каждой строки личности `CatalogMaster.objects` в `tenant_scope(row.tenant)` — тенантно-пиновое чтение, MKT1 не трогаем; без строк — `False` по построению |
| `_already_has_a_solo_workspace`, `_solo_identity_rejected` | уже по личности | без изменений (принимают `channel, channel_user_id`) |
| `_redeem_and_greet(event, bot_user, code, tenant, entry)` `:1034` → `redeem_staff_invite(code, bot_user, tenant)` | `StaffInvite.all_tenants.filter(code_hash, tenant=tenant)`; `TenantStaff(tenant, bot_user=A)`; `_check_rate_limit(bot_user)` | тенант — **из кода**: `filter(code_hash=…)` без `tenant`, тенант = `invite.tenant`; строка `BotUser` создаётся в нём **перед** `TenantStaff`; лимит — по `(channel, channel_user_id)`. Довод докстринга «код салона B в боте салона A» (`staff_invites.py:332-343`) держался на «бот принадлежит салону» — снимается решением 12.09 |
| `_send_menu(event, role_ctx, tenant, entry)`, `_handle_talk/_handle_button(…, tenant, …)` | `tenant` из scope | тенант рабочей строки |

Что **не** меняется: `bot_scope(entry)` и выбор бота (срез 1), `create_solo_provider`
(уже без строки), Mini App клиентской поверхности (`require_init_data`), `/me`.

## 3. Стражи и эталоны, которые перевернутся (с датой)

* `test_salon_handler.py::…no_tenant_scope…` (молчание без тенанта) → «без тенанта
  записи ответ уходит, строки нет».
* `test_salon_solo_door.py` — фикстура `bot_user` в тенанте салона больше не образ
  незнакомца; появляется фикстура «личность без строк».
* `staff_invites` тесты на «код чужого салона не выкупается» → «код выкупается в
  тенанте кода; строка создаётся там».
* `ingress` тесты на `resolved_tenant_id` салонного бота → `None`.
* Докстринги `SalonMaxHandler`, `bot_registry` (шапка, `is_tenant_less`),
  `staff_invites.redeem_staff_invite`, `base.py:1922-1926` (пример env).
* Положительная стража нового правила: сообщение незнакомца салонному боту →
  ответ есть, `BotUser.all_tenants.filter(channel_user_id=…).count() == 0`
  (сегодня `1` — красный до правки); соло-мастер пишет боту → строк у личности
  по-прежнему **одна** (соло), салонной не появляется.

## 4. Срезы и SP

| срез | что | где | SP | зависит |
|---|---|---|---|---|
| **4a** DRF-1757 | вход: `requires_tenant=False`; ingress → `None` для `max_salon`; обработчик определяет тенант **от человека** (`resolve_working_bot_user` → `tenant_scope(row.tenant)`), штатный поток без изменений; без рабочей строки — передача в путь незнакомца | `handlers.py`, `ingress/services.py`, `salon_handler.py:505-600` | **3** | DRF-1755 ✅ |
| **4b** | путь незнакомца **без `BotUser`**: код → тенант из `StaffInvite`/`MasterInvite` по самому коду, строка создаётся в тенанте кода при выкупе; «Я работаю сам» → `create_solo_provider` из события; лимит попыток по личности; `_has_a_master_card_here` по личности; тексты — старые | `salon_handler.py`, `identity/services/staff_invites.py` | **2** | 4a |
| **4c** | снять `MAX_BOT_SALON_TENANT_SLUG` на пилоте (env, руками владельца/главного окна после выкладки 4a+4b), `is_tenant_less` = «тенант позже», докстринги и 29 упоминаний в тестах перевернуть с датой, `admin_api` 503 — только «реестр пуст» | `bot_registry.py`, `base.py`, тесты `channels`/`ingress`, `admin_api/auth.py` | **1** | 4a, 4b |

Порядок 4a → 4b → 4c одним стеком; **переменную снимать последней** — пока она
стоит, старое поведение (строка в салоне) и новое (тенант от человека) дают один
ответ для всех, у кого есть рабочая строка, и различаются только для незнакомца.
Это и есть окно для замера на пилоте: число новых строк `customer` в
`formula-tela` от салонного бота за сутки после 4a+4b должно стать **0**.

## 5. Владельцу — ничего нового

D2 закрывает единственный вопрос среза. Одно следствие назвать: после 4c
`/whoami` незнакомцу отвечает карточкой личности **без** раздела «в этом салоне»
(салона у него ещё нет) — это не регресс, а точное описание его состояния.
