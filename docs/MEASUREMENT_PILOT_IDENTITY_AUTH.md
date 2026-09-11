# Замер: IDENTITY / AUTH (п.1), MAX CONVERSATION ENTRY (п.2), приватность-шов

**Предмет:** пункты 1 и 2 матрицы готовности Controlled Pilot + пилот-критичные швы приватности (§20 промпта владельца).
**Режим:** MEASURE-FIRST, read-only. Ничего не чинилось, не коммитилось, PR не создавался, Linear не трогался.
**Дата замера:** 09.09.2026.

---

## 1. Базы замера

| repo | ветка | фактический SHA (сверен) | дата | чем снято |
|---|---|---|---|---|
| `ai-bot-platform` | `origin/dev` | `b1a119bdfb26765bc75ce35dfbf1dd82227183d0` | 09.09.2026 | `git show origin/dev:<путь>`, `git grep -n <шаблон> origin/dev -- <пути>` |
| `djangoproject-catalog` | `origin/dev` | `95c917e684652476feef3ae9d790fb2c8d277378` | 09.09.2026 | то же |
| `ayla-ai-core` | `origin/main` | не читался — вне предмета | — | — |
| `ayla-knowledge` | `origin/main` | не читался — вне предмета | — | — |

SHA обоих замеряемых репозиториев **совпали** с общим сводом (`docs/BRIEF_PILOT_READINESS_COMMON.md` §2).
Головной коммит каталога: `fix(recommendation): кандидат называется ключом ПОЛЬЗОВАТЕЛЯ… (DRF-1598) (#303)`, Wed Sep 9 06:50:27 2026.

Рабочие деревья **не читались**: у `ai-bot-platform` чекаут стоит на чужой ветке
`feat/recommendation-boundary-client`. Всё цитируемое взято из `origin/dev`.

**Живые значения переменных окружения не замерены нигде** — у окна нет ssh. Умолчания
выписаны из кода; список того, что надо снять на контуре, — §9.

---

## 2. Executive verdict — по строке на вопрос

### п.1 IDENTITY / AUTH

1. **Единого механизма опознания человека в контуре нет — их четыре**, и они не сводятся друг к другу: (а) секрет вебхука MAX, (б) MAX `initData` HMAC, (в) пароль + сессия Django в админконсоли, (г) один общий Bearer на весь внутренний контур Ayla.
2. **На поверхностях MAX и Telegram личность человека не подтверждается ничем** — подтверждается подлинность *канала*. `sender.user_id` берётся из тела вебхука как есть; кто держит секрет вебхука, тот назначает личность.
3. **Mini App / мастерский кабинет / админский Mini App опознают человека честно:** HMAC-подпись `initData` ключом токена бота, `hmac.compare_digest`, TTL **3600 с**, проверка на каждый запрос, серверной сессии нет.
4. **Один человек across surfaces — НЕТ, не один.** Штатно несколько идентификаторов: `BotUser.id` на каждый тенант, общий `BotUser.ayla_user_id`, а у мастера ещё пара `SpecialistProfile.id` ≠ `SpecialistProfile.user_id`, из которой уже вырос замеренный дефект «0 из 31».
5. **Случай «0 из 31» на канонической голове ИСПРАВЛЕН** — `djangoproject-catalog:users/recommendation_source.py:258` отдаёт `specialist.user_id`. Но структура, породившая дефект, осталась: у мастера два ключа, и граница выбирает один из них руками.
6. **Найден второй, НЕ закрытый случай двух идентификаторов на одного человека:** `CatalogMaster.id` — это `SpecialistProfile.id` для строк из синхронизации и **свой `uuid4`** для строк, заведённых приглашением. DRF-1507 научил синхронизацию не плодить дубль, но строку на канонический ключ **не перекладывает**.
7. **Класс `IsBotServiceWithVerifiedClient` не проверяет «verified client».** Он проверяет общий Bearer и синтаксис заголовка `X-External-User-ID`, который выбирает сам вызывающий; `resolve_external_user` при отсутствии субъекта **создаёт** его. Имя обещает верификацию, код выполняет провижининг.
8. **Да, один человек может прочитать и записать данные другого — при владении `AYLA_INTERNAL_API_TOKEN`.** Найдено 8 групп внутренних ручек каталога, где объект берётся просто по id из URL под `IsInternalBearer`: экспорт телефона / email / ФИО, чтение-запись-удаление персонального контекста, стирание персданных, финансы мастера и **списание долга с его карты**. `has_object_permission` не реализован **ни в одном** из 12 permission-классов каталога.
9. **Это известно команде и записано в коде как принятый на пилот риск** — DRF-1036, `djangoproject-catalog:users/internal_identity_api.py:46-56`.
10. **Секреты сервис-в-сервис: один общий Bearer без ролей и без ротации.** Все чтения — `getattr(settings, "<ОДНО_ИМЯ>", "")`; списка допустимых ключей нет, окна «старый + новый» нет; ротация `AYLA_INTERNAL_API_TOKEN` = разрыв связи. Сравнения — `hmac.compare_digest` везде, где сравнивается вход атакующего. Единственная честная сепарация — `AYLA_IDENTITY_PROVISIONING_TOKEN`.

### п.2 MAX CONVERSATION ENTRY

11. **Подлинность входящего подтверждается заголовком `X-Max-Bot-Api-Secret`**, сверенным `hmac.compare_digest` против каждой записи реестра ботов без раннего выхода; нет совпадения → 401. **IP-аллоулиста на этой ручке нет** (в отличие от eventbus-ingest, где он есть).
12. **Тенант резолвится из того же секрета:** `TENANT_SLUG` записи реестра → фолбэк на карту `CHANNEL_TOKEN_TO_TENANT_SLUG` → `None`.
13. **Неизвестный тенант приём НЕ отклоняет.** Событие журналируется с `resolved_tenant=NULL`, ставится в очередь, пишется аудит `ingress.webhook_unknown_tenant`, MAX получает `200`. Дальше при `STRICT_TENANT_REFUSE` (умолчание **False**) обработчик всё равно запускается и падает на `ValueError`; запись остаётся в PEL, автоматического DLQ-ретрая нет.
14. **Идемпотентность приёма есть, но дырявая.** Дедуп на `UniqueConstraint(channel, external_event_id)` работает; но `record_webhook` **коммитит запись журнала ДО** `enqueue`, а `enqueue` ничем не обёрнут. Падение Redis между ними даёт: журнал есть, в очередь не попало, повтор от MAX даёт `created=False` → **сообщение потеряно молча и навсегда**.
15. **`WebhookJournal.processed_at` — фикция.** `help_text` обещает «Set when the consumer XACKs the matching stream entry»; **ни одна строка production-кода его не пишет**. Восстановительный проход по «журналировано, но не заехало» невозможен даже вручную — поле всегда NULL.
16. **«Отсутствие BotUser» на входе MAX не наступает** — обработчик делает `get_or_create`. Отсутствие наступает на Mini App: там строка **лениво создаётся** после проверки подписи.

### п.3 ПРИВАТНОСТЬ (пилот-критичные швы)

17. **Медданные в каталоге лежат открытым текстом.** `NutritionProfile.health_flags` — обычный `JSONField` с ключами `pregnant`, `diabetes_t1/t2`, `eating_disorder`, `meds`, `allergies`. **Field-level шифрования в `djangoproject-catalog` не существует ни для одного поля** (греп по всему репозиторию — ноль совпадений). Поле правится из Django admin любым staff.
18. **Реестра согласий в каталоге НЕТ вовсе.** Единственный класс с «Consent» — `billing.BillingConsent` (про платежи). Медданные пишутся и читаются на стороне каталога без понятия «согласие».
19. **Поверхность удаления персданных по 152-ФЗ на стороне Ayla = ровно ОДНА модель.** `users/personal_context_erasure.py` импортирует только `UserPersonalContext`. Известный факт про `ClientGoal` **подтверждён**; найдено ещё десять сущностей, переживающих удаление, — §7.
20. **Экспорт и удаление по одному и тому же протоколу C5 не симметричны:** `phone`, `email`, `full_name`, `bio`, `city` объявлены персданными на выгрузке и **не входят в поверхность удаления**.
21. **`@consent_required` — DEAD_CODE.** Механизм жёсткого отказа не подключён ни к одной ручке; все реальные гейты — тихая деградация.
22. **Полный сырой текст каждого сообщения человека лежит в `ingress_webhookjournal.raw_payload` вечно:** менеджер не тенантный, TTL нет, задачи очистки нет, в поверхность удаления запись не входит. `Message.content` не чистится вообще ни в одном репозитории.
23. **PII-скраббер в логах есть только у бота и только для телефона / email / карт.** У каталога скраббера нет; телефон в открытом виде уходит в лог как минимум из трёх мест.

---

## 3. Сводка по классам

| Класс | Число находок |
|---|---|
| `EXISTS` | 8 |
| `PARTIAL` | 13 |
| `MISSING` | 10 |
| `CONTRADICTS_CANON` | 0 |
| `STALE_SPEC` | 5 |
| `DEAD_CODE` | 6 |
| `LLM_ONLY` | 0 |
| `PROMPT_ONLY` | 0 |
| `UNKNOWN_NOT_MEASURED` | 10 |

Итого 42 находки + 10 явных пробелов. Каждая пронумерована ниже и несёт класс.

---

## 4. Находки — идентификация и авторизация

### 4.1 Поверхности опознания личности

#### F-01. MAX webhook подтверждает канал, не человека — `PARTIAL`, PILOT IMPACT `DEGRADED`

`ai-bot-platform:apps/ingress/views.py:90-102`:

```python
    secret_got = request.headers.get("X-Max-Bot-Api-Secret", "")
    bot = resolve_bot(secret_got)
    if bot is None:
        logger.warning("channels.max.webhook.unauthorized header_present=%s", bool(secret_got))
        return JsonResponse({"error": "unauthorized"}, status=401)
```

`ai-bot-platform:apps/channels/bot_registry.py:378-386` — сверка timing-safe, без раннего выхода:

```python
    encoded = secret.encode("utf-8")
    found: BotEntry | None = None
    for entry in registry:
        if hmac.compare_digest(encoded, entry.webhook_secret.encode("utf-8")):
            found = entry
    return found
```

Личность человека берётся из **тела запроса** — `ai-bot-platform:apps/channels/max/parser.py:132-133,151`:

```python
    if not isinstance(sender, dict) or "user_id" not in sender:
        raise ParseError("MAX payload missing required field: message.sender.user_id")
...
        channel_user_id=str(sender["user_id"]),
```

Вся личность на канале MAX держится на одном транспортном секрете. Подписи на уровне
пользователя нет и по устройству MAX-вебхука быть не может. Это свойство поверхности,
а не дефект реализации — но оно означает, что утечка `MAX_BOT_*_WEBHOOK_SECRET` даёт
возможность говорить от имени **любого** человека.

#### F-02. IP-аллоулиста на `/api/v1/ingress/max/` нет — `MISSING`, PILOT IMPACT `DEGRADED`

Греп `git grep -in "allowlist\|allowed_ips\|ip_whitelist\|REMOTE_ADDR" origin/dev -- 'apps/**/*.py' 'config/**/*.py'`
даёт по IP единственный модуль — `apps/eventbus/ingest_ip.py` (ручка приёма событий от Ayla).
На ingress MAX ни `REMOTE_ADDR`, ни `X-Real-IP` не читаются.

По правилу «чужие ограничения не выводят из своего кода»: фильтрация **на edge nginx**
этим замером не проверялась и не опровергнута. На уровне приложения — `MISSING`, на
уровне периметра — `UNKNOWN_NOT_MEASURED`. Команда проверки — §9.

#### F-03. Mini App клиента: initData, TTL 3600 с, сессии нет — `EXISTS`

`ai-bot-platform:apps/miniapp_api/auth.py:154-166`:

```python
def _expected_hash(token: str, data_check_string: str) -> str:
    """MAX/Telegram WebApp two-stage HMAC for one candidate bot token."""
    secret_key = hmac.new(
        key=b"WebAppData",
        msg=token.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).digest()
    return hmac.new(
        key=secret_key,
        msg=data_check_string.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).hexdigest()
```

`ai-bot-platform:apps/miniapp_api/auth.py:240-248` — `hmac.compare_digest`, цикл по всем
токенам реестра **без раннего выхода** (комментарий на :237-239 объясняет, что ранний
выход утёк бы позицией совпадения через тайминг).

`ai-bot-platform:apps/miniapp_api/auth.py:55`:

```python
AUTH_DATE_MAX_AGE_SECONDS = 60 * 60  # 60 min — matches MAX recommendation.
```

TTL — **3600 секунд**. Проверка `auth_date` идёт **после** HMAC (`:243` → `:250`), то есть
протухание не даёт обойти подпись. Отрицательный skew (`auth_date` из будущего) не отсекается.
Сессии/токена нет — проверка на каждый запрос.

Поверхность **только MAX**: заголовок жёстко `HEADER_PREFIX = "MaxInitData "` (`auth.py:57`),
канал захардкожен `channel="max"`. **Telegram Mini App-поверхности не существует** — греп
`TelegramInitData|TgInitData` даёт ноль; `TELEGRAM_BOT_TOKEN` (`config/settings/base.py:2069`)
по собственному комментарию `base.py:2082-2085` служит только каналу алертов оператору.

#### F-04. Клиентская Mini App резолвит тенант ИНАЧЕ, чем мастерская и админская — `PARTIAL`, PILOT IMPACT `DEGRADED`

`ai-bot-platform:apps/miniapp_api/views.py:180-183`:

```python
        bot_tenant_slug = getattr(settings, "MAX_BOT_TENANT_SLUG", "")
        bot_tenant = (
            Tenant.objects.filter(slug=bot_tenant_slug).first() if bot_tenant_slug else None
        )
```

Подпись приняли по **любому** боту реестра, а тенант назначили **одной настройкой**.
`verified.bot_slug` на этой поверхности не читается вовсе.

Мастерская и админская поверхности ходят через общий резолвер
`ai-bot-platform:apps/identity/services/bot_user_resolver.py:47-65`, где приоритет обратный —
тенант подписавшего бота первым, настройка второй. Модуль сам объясняет, зачем появился
(`bot_user_resolver.py:16-19`):

> «Two surfaces resolving identity by two rules is how they drift apart (DRF-1128 is the same family), so the rule now lives in one place and both call it.»

Клиентская поверхность на этот резолвер **не переведена**. Два следствия:

* человек, открывший клиентскую Mini App из салонного или общероссийского бота, будет найден
  или **заведён** в тенанте `MAX_BOT_TENANT_SLUG` — `views.py:226` `bot_user = _lazy_register_bot_user(bot_tenant, verified)`;
* при незаданном `MAX_BOT_TENANT_SLUG` клиентская Mini App отвечает `500 server_misconfigured`
  **всем** (`views.py:216-225`), потому что и поиск, и ленивое создание завязаны на непустой tenant.

Умолчание — `ai-bot-platform:config/settings/base.py:595` `MAX_BOT_TENANT_SLUG = os.environ.get("MAX_BOT_TENANT_SLUG", "")`,
и `production.py` его **не требует** (в отличие от `AYLA_INTERNAL_API_TOKEN`, который требует).
Живое значение — `UNKNOWN_NOT_MEASURED`.

#### F-05. Кросс-тенантный фолбэк резолвера личности — `PARTIAL`

`ai-bot-platform:apps/identity/services/bot_user_resolver.py:77-92`:

```python
    qs = BotUser.all_tenants.filter(channel="max", channel_user_id=verified.user_id)
    if tenant_slug:
        scoped = qs.filter(tenant__slug=tenant_slug).select_related("tenant").first()
        if scoped is not None:
            return scoped
        # Fall through rather than 404: a person may have been created
        # under a different tenant and linked there. Better to answer with
        # the row we can find than to deny someone who is genuinely staff.
        logger.info(...)

    return qs.select_related("tenant").order_by("-last_seen").first()
```

Если в тенанте подписавшего бота строки нет, берётся строка того же человека **из любого
другого тенанта** по свежести `last_seen`, и дальше мастерская / админская поверхность
работает в **её** тенанте. Эскалации привилегий это не даёт (`apps/admin_api/auth.py:151-153`
резонно замечает, что `resolve_role` читает `TenantStaff` в собственном тенанте строки), но
означает, что тенант сеанса выбирается не подписью, а состоянием базы.

#### F-06. Dev-bypass есть на клиентской и мастерской поверхностях, на админской нет — `PARTIAL`, PILOT IMPACT `DEGRADED`

`ai-bot-platform:apps/miniapp_api/dev_bypass.py:94-98`:

```python
    if not settings.DEBUG:
        return None

    if request.META.get(DEV_BYPASS_HEADER) != "1":
        return None
```

Заголовки: `X-Dev-Bypass: 1`, `X-Dev-User-Id`, `X-Dev-Tenant-Slug` (`dev_bypass.py:65-72`).
Вызывается **до** проверки подписи и имеет приоритет: `apps/miniapp_api/views.py:153-159`,
`apps/master_api/auth.py:321-327`. В `apps/admin_api/auth.py` не импортируется вовсе.

Умолчания: `config/settings/base.py:60` `DEBUG = os.environ.get("DJANGO_DEBUG", "False").lower() == "true"`;
`production.py:21` и `staging.py:24` — жёстко `DEBUG = False`; `local.py:5` — `True`.

По правилу «флаг с умолчанием — не реализованная возможность» это **мёртвая ветка при
правильном settings-модуле**. Живое значение `DJANGO_SETTINGS_MODULE` / `DJANGO_DEBUG` на
`api-dev.gobeauty.site` — `UNKNOWN_NOT_MEASURED`. Отдельного флага вида `MINIAPP_SKIP_INIT_DATA`
не существует (греп).

#### F-07. Мастерский `session_token` выпускается и никем не проверяется — `DEAD_CODE`

`ai-bot-platform:apps/master_api/auth.py:145-176` — выпуск на `django.core.signing.TimestampSigner`,
ключ `MASTER_SESSION_SECRET` с фолбэком на `SECRET_KEY` (`auth.py:141`), TTL
`MASTER_SESSION_TTL_DAYS`, умолчание **30 дней** (`config/settings/base.py:631-632`).

`decode_master_session_token` вызывается **только из тестов**; выдача — в теле ответа
`/onboarding/accept` (`views.py:611,619,753,761`); фронт кладёт токен в сторадж и в
заголовке не шлёт — все клиенты шлют `MaxInitData`. Фактический TTL доступа мастера —
**3600 с** initData, а не 30 дней. Докстринг (`auth.py:20-21`) это признаёт честно, что
редкость: имя и код здесь не расходятся.

#### F-08. Админконсоль: пароль + сессия Django, суперпользователь вне ограничений — `PARTIAL`

Роли — две группы, `ai-bot-platform:apps/adminconsole/roles.py:62-69` (`ayla-viewer`,
`ayla-editor`); запретные списки — `roles.py:72-115`, включая `tenancy.tenant` в
`EDITOR_DENIED_MODELS` («там лежат токен бота и вебхук-секрет тенанта»).

Пароль из `AYLA_ADMIN_PASSWORD` (`accounts.py:53`), выдача и отзыв — только management-командами
из-под суперпользователя, никогда не `is_superuser` (`accounts.py:186-187`); отзыв гасит
живые сессии (`accounts.py:110-128`); общие логины запрещены списком (`accounts.py:60-82`).

Доступ к данным клиента — пропуск с TTL **60 минут** (`apps/adminconsole/client_access.py:64-70`),
причём настройки `ADMINCONSOLE_CLIENT_ACCESS_TTL_MINUTES` в `config/settings/` **нет** (греп по
всему репозиторию: только сам модуль, тест и раннбук) → действует умолчание.

Суперпользователь не ограничен ничем — `client_access.py:97-101` возвращает `True` по
`is_superuser`, а `client_scope.HIDDEN_FIELDS` прячет `identity.botuser.phone` и `.context`
от всех **кроме** суперпользователя.

`SESSION_COOKIE_AGE` в репозитории не задан (греп: ноль совпадений) → умолчание Django,
две недели. Живое — `UNKNOWN_NOT_MEASURED`.

#### F-09. Capability `view_customer_phone_audited` не пишет аудит — `STALE_SPEC`, PILOT IMPACT `DEGRADED`

`ai-bot-platform:apps/identity/services/role_resolver.py:135-138`:

```python
# TODO(role-audit-PR): receptionists with ``view_customer_phone_audited``
# must trigger an audit row at the call site. Until that PR lands the
# gate passes but no audit is written. Tracked separately because the
# audit slug + receiver wiring is a meaningful chunk of code.
```

Capability называется `view_customer_phone_audited` (`role_resolver.py:192`). Аудита нет.
Ресепшн видит телефон клиента без следа.

#### F-10. Staff-инвайт: TTL 7 дней, хеш вместо кода, рейт-лимит fail-open — `PARTIAL`

`ai-bot-platform:apps/identity/services/staff_invites.py:56-65`:

```python
CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"  # pragma: allowlist secret
CODE_PREFIX = "AYLA"
CODE_BODY_LEN = 4
INVITE_TTL_DAYS = 7

MAX_ATTEMPTS = 5
ATTEMPT_WINDOW_SECONDS = 3600
```

Хранится только SHA-256 (`staff_invites.py:145-146`). Энтропия признана недостаточной самим
кодом (31⁴ ≈ 924 тыс.), безопасность держится на одноразовости + 7 днях + рейт-лимите. Но
рейт-лимит **fail-open** — `staff_invites.py:245-247`:

```python
    except Exception as exc:  # noqa: BLE001 — brake, not a gate
        logger.warning("identity.staff_invite.rate_limit_unavailable exc=%s", exc)
        return
```

При недоступном Redis перебор ничем не ограничен. Мастерский инвайт-токен —
`invite_expires_at` nullable, при `None` **бессрочен** (`apps/master_api/auth.py:224-269`);
кто и на сколько его ставит — `UNKNOWN_NOT_MEASURED`.

### 4.2 Один ли человек across surfaces

#### F-11. У человека штатно несколько локальных идентификаторов — `EXISTS` (по замыслу), PILOT IMPACT `DEGRADED`

`ai-bot-platform:apps/identity/models.py:323` — `unique_together = (("tenant", "channel", "channel_user_id"),)`;
докстринг (`models.py:43-46`) прямо говорит: «Same Telegram user can sign up to two different
tenant deployments and that's two BotUsers, by design».

Склейка наверх — `BotUser.ayla_user_id`, веером по всем тенантам,
`ai-bot-platform:apps/identity/services/ayla_link.py:112-130`:

```python
    shells = BotUser.all_tenants.filter(
        channel=bot_user.channel,
        channel_user_id=bot_user.channel_user_id,
    )
    ...
        if current is None:
            shell.ayla_user_id = resolved
            shell.save(update_fields=["ayla_user_id"])
```

Инварианты честные: никогда не перезаписывает; конфликт (2+ разных id на человека) выносится
наружу событием `identity.ayla_link.conflict` и fail-close в `privacy._resolve_person_link`,
а не «примиряется» молча.

Ключ на стороне Ayla — детерминированная строка,
`ai-bot-platform:apps/integrations/ayla/user_proxy.py:46`: `return f"bot:{bot_user.channel}:{bot_user.channel_user_id}"`,
глобально уникальная как `users_user.username`. То есть **на стороне Ayla человек один**,
на стороне бота — столько строк, сколько тенантов.

#### F-12. Случай «0 из 31» на канонической голове ИСПРАВЛЕН — `EXISTS`

`djangoproject-catalog:users/recommendation_source.py:258`:

```python
            ref=CandidateRef(CandidateKind.PROVIDER, specialist.user_id),
```

Ключ пользователя, не ключ профиля — совпадает с тем, что зеркало бота хранит в
`CatalogMaster.ayla_user_id`.

**Осторожно с цитированием числа:** «31 из 31» — боевой замер от 09.09.2026
(`ai-bot-platform:docs/PILOT_MEASUREMENTS.md:112-117`), у него есть срок годности. Этим
замером доказано только то, что **код на канонической голове называет кандидата ключом
пользователя**.

#### F-13. НЕ закрытый второй случай двух идентификаторов на одного мастера — `PARTIAL`, PILOT IMPACT `DEGRADED`

Структура на стороне Ayla, `djangoproject-catalog:users/models.py:170-181` — **два UUID на
одного мастера**:

```python
class SpecialistProfile(models.Model):
    """Profile for specialists (masters) in BeautyGO Pro."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ...
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="specialist_profile",
    )
```

Внутренние ссылки Ayla используют **профильный** ключ —
`djangoproject-catalog:appointments/models.py:51-55`:

```python
    specialist = models.ForeignKey(
        'users.SpecialistProfile',
        on_delete=models.PROTECT,
        related_name='appointments',
    )
```

Граница рекомендаций отдаёт **пользовательский** (F-12). Оба ключа живут одновременно.

Зеркало бота повторяет ту же двойственность,
`ai-bot-platform:apps/catalog/services/upserter.py:241-249`:

```python
                    obj = CatalogMaster.objects.filter(pk=dto.ayla_master_id).first()
                    if obj is None and dto.user_id:
                        obj = CatalogMaster.objects.filter(ayla_user_id=dto.user_id).first()
                        if obj is not None:
                            logger.info(
                                "catalog.upsert.master_deduped model=CatalogMaster "
                                "ayla_master_id=%s matched_row=%s ayla_user_id=%s "
```

И тут же (`upserter.py:234-240`) сказано, что строка **на канонический ключ не перекладывается**:

> «Строка НЕ перекладывается на канонический ``id``: на ``CatalogMaster`` смотрят внешние ключи из booking, scheduling, conversations, notifications и internal_chat, и смена первичного ключа — это слияние дублей, отдельный обоснованный шаг, а не побочный эффект синхронизации.»

Итог: `CatalogMaster.id` — это `SpecialistProfile.id` для строк синхронизации и **произвольный
`uuid4`** для строк, заведённых приглашением. Любой потребитель, который отправит
`CatalogMaster.id` в Ayla как `specialist_id`, для инвайт-строк промахнётся. Это ровно тот же
класс, что «0 из 31», и он **открыт**.

Проверяется на контуре одной командой — §9.

### 4.3 Классы разрешений и объектные проверки

#### F-14. `IsBotServiceWithVerifiedClient` не проверяет клиента — `STALE_SPEC` / `PARTIAL`, PILOT IMPACT `STOP`

`djangoproject-catalog:users/permissions.py:173-207` (тело целиком):

```python
    def has_permission(self, request: Any, view: Any) -> bool:
        from users.services import (
            InvalidExternalUserIDError,
            resolve_external_user,
        )

        expected = getattr(settings, "AYLA_INTERNAL_API_TOKEN", "") or ""
        if not expected:
            return False

        auth_header = request.META.get("HTTP_AUTHORIZATION", "")
        prefix = "Bearer "
        if not auth_header.startswith(prefix):
            return False
        provided = auth_header[len(prefix):].strip()
        if not provided or not compare_digest(provided, expected):
            return False

        external_user_id = request.META.get("HTTP_X_EXTERNAL_USER_ID", "")
        if not external_user_id:
            return False
        try:
            user = resolve_external_user(external_user_id)
        except InvalidExternalUserIDError:
            return False

        request.user = user
        return True
```

Построчно: (1) общий Bearer, constant-time; (2) заголовок непустой; (3) `resolve_external_user`
проверяет **только регулярку** и при отсутствии субъекта **создаёт** его —
`djangoproject-catalog:users/services.py:123-127`:

```python
    user, created = User.objects.select_related("linked_user").get_or_create(
        username=external_user_id,
        defaults={"role": "client", "is_proxy": True, "is_guest": False},
    )
```

Регулярка — `users/services.py:69`: `_EXTERNAL_USER_ID_RE = re.compile(r"^[a-z][a-z0-9_-]*(?::[A-Za-z0-9_-]{1,64})+$")`.

**Никакой верификации человека в классе нет.** Заявленный «второй фактор» — сверка `client_id`
тела с `request.user.id` — живёт **во вьюхе**, не в классе, и на GET-ручках отсутствует по
построению. Больше того, сам второй фактор добывается **тем же токеном**:
`GET /api/v1/internal/me/identity/` под тем же Bearer и подставленным `X-External-User-ID`
возвращает `ayla_user_id` — `djangoproject-catalog:users/internal_identity_api.py:183-186`.

Оговорка о границе риска: если внешняя личность **не связана** через `bind_external_identity`,
подмена заголовка даёт изолированную прокси-строку (пустой результат). Если **связана** —
резолвер вернёт реальный аккаунт (`users/services.py:140-147`). Используется ли binding в бою —
`UNKNOWN_NOT_MEASURED`, §9.

#### F-15. `has_object_permission` не реализован ни в одном permission-классе каталога — `MISSING`, PILOT IMPACT `STOP`

Греп `git grep -n "has_object_permission" origin/dev -- '*.py'` вне тестов — **ноль попаданий**.
Все 12 классов живут в `djangoproject-catalog:users/permissions.py`; динамических
переопределений нет (`git grep -n "def get_permissions"` без тестов — пусто). Объектная
авторизация целиком отдана вьюхам, и в части вьюх её нет.

#### F-16. Ручки, где объект берётся просто по id из URL под общим Bearer — `MISSING`, PILOT IMPACT `STOP`

Все под `permission_classes = [IsInternalBearer]`, `authentication_classes: list = []`,
`request.user` анонимен, ни актора, ни тенанта.

`djangoproject-catalog:users/permissions.py:240-251` — что проверяет `IsInternalBearer`:

```python
    def has_permission(self, request: Any, view: Any) -> bool:
        expected = getattr(settings, "AYLA_INTERNAL_API_TOKEN", "") or ""
        if not expected:
            return False
        auth_header = request.META.get("HTTP_AUTHORIZATION", "")
        prefix = "Bearer "
        if not auth_header.startswith(prefix):
            return False
        provided = auth_header[len(prefix):].strip()
        if not provided or not compare_digest(provided, expected):
            return False
        return True
```

Ровно одно: предъявлен ли общий секрет.

| Ручка | Файл:строка | Что делает по чужому id |
|---|---|---|
| `GET /internal/users/{user_id}/personal-data/export/` | `users/personal_data_api.py:79`, `:99-114` | отдаёт `phone`, `email`, `full_name`, `bio`, `city` |
| `DELETE /internal/users/{user_id}/personal-data/` | `users/personal_data_api.py:140`, `:162-172` | стирает персданные |
| `GET/PATCH/DELETE /internal/users/{ayla_user_id}/personal-context/` | `users/internal_personal_context_api.py:125`, `:128,146,185` | читает, перезаписывает 12 полей, стирает профиль |
| `.../personal-context/ask-eligibility/ \| mark-asked/ \| skip/` | `:202`, `:237`, `:253` | то же семейство |
| `GET /internal/users/{user_id}/` | `users/internal_users_api.py:119`, `:144-150` | `display_name` + `avatar_url` — ограничение только формой ответа |
| `GET/POST /internal/billing/specialists/{specialist_id}/status \| card-setup \| pay-debt/` | `billing/internal_api.py:40,90,177` | **списывает долг с сохранённой карты мастера** |
| `GET /internal/specialists/{specialist_id}/payout-preview/` | `payments/views.py:1251`, `:1261-1288` | финансовая история мастера |
| `GET /internal/payments/{payment_id}/` | `payments/views.py:1537`, `:1547-1553` | статус платежа; `payment_id` объявлен capability-токеном |

Самый тяжёлый — денежный. `djangoproject-catalog:billing/internal_api.py:197-203` (сверено дословно):

```python
    def post(self, request: Request, specialist_id: UUID) -> Response:
        specialist = (
            SpecialistProfile.objects
            .filter(user_id=specialist_id)
            .select_related("user", "tenant")
            .first()
        )
```

…далее `billing/internal_api.py:230` — `result = pay_debt(subscription=subscription, return_url=return_url)`.
Ни `X-External-User-ID`, ни тенанта, ни владения.

#### F-17. Ручки, где владение ПРОВЕРЕНО правильно — `EXISTS` (контраст, нужен для честности картины)

* `djangoproject-catalog:appointments/internal_api.py:308-311` — `Appointment.objects.filter(client=request.user).get(pk=booking_id)`.
* `djangoproject-catalog:payments/views.py:1579-1589` — `_check_user_scope`: путь-`ayla_user_id` обязан совпасть с резолвнутым актором, иначе 403 `CLIENT_MISMATCH`.
* `djangoproject-catalog:tenants/appointments_api.py:137-140` — салонная запись требует **двух** классов: `[IsBotServiceWithVerifiedClient, IsTenantAdmin]`, то есть названный в заголовке человек обязан реально быть админом названного в `X-Tenant` салона. **Это единственный настоящий второй фактор в контуре.**
* `djangoproject-catalog:users/internal_schedule_api.py:141-148` — фильтр по паре `(id, tenant_id)`; но `tenant_id` приходит из тела, и сам код называет его «claim, not a credential» — это сужение перебора, не граница владения.

#### F-18. `DEFAULT_PERMISSION_CLASSES` в каталоге не задан — `MISSING`, PILOT IMPACT `DEGRADED`

Блок `REST_FRAMEWORK` в `djangoproject-catalog:djangoProject/settings/base.py:95-113` ключа не
содержит → дефолт DRF = `AllowAny`. Любая новая вьюха без явного `permission_classes` окажется
публичной. Это не гипотетика: см. F-19.

#### F-19. `ProfileDetailView` — заряженное ружьё — `DEAD_CODE`

`djangoproject-catalog:users/views.py:555-558`:

```python
class ProfileDetailView(generics.RetrieveAPIView):
    queryset = Profile.objects.all()
    serializer_class = ProfileSerializer
    permission_classes = [permissions.AllowAny]
```

Маршрута нет (греп `ProfileDetailView` вне тестов — единственное попадание, само определение).
Первый же `path(...)` открывает публичное чтение чужих профилей.

#### F-20. Публичные отзывы могут отдать телефон в имени — `PARTIAL`, PILOT IMPACT `DEGRADED`, TEST STATUS `MISSING`

`djangoproject-catalog:reviews/serializers.py:47-52` — фолбэк `return full or user.username`;
а `username` при регистрации по телефону — `djangoproject-catalog:users/services.py:1060-1065` —
`f"user_{phone.replace('+', '')}"`. Неанонимный отзыв клиента без заполненного имени публично
отдаёт `user_79991234567`.

Честная оговорка: эксплуатируемость грепом не доказана — нужен прогон на контуре (§9).
Классифицирую как **вероятную** утечку, а не как подтверждённый факт.

### 4.4 Секреты сервис-в-сервис

#### F-21. Один общий Bearer без ролей и без ротации — `MISSING` (ротация), PILOT IMPACT `STOP`

Сами значения секретов нигде не печатались и в отчёте отсутствуют.

| Секрет | Заголовок | Где читается | Сравнение | Умолчание |
|---|---|---|---|---|
| `AYLA_INTERNAL_API_TOKEN` | `Authorization: Bearer` | catalog `users/permissions.py:181,241`, `users/authentication.py:72`; bot `config/settings/base.py:735` | `hmac.compare_digest` | `""`; обязателен в проде с обеих сторон (catalog `settings/prod.py:42`, bot `settings/production.py:35`) |
| `AYLA_IDENTITY_PROVISIONING_TOKEN` | `Authorization: Bearer` | catalog `users/permissions.py:288` | `compare_digest`; отдельно **отвергает совпадение** с общим токеном (`:292`) | `""` |
| `NUTRITION_SERVICE_TOKEN` | `X-Service-Token` | catalog `users/permissions.py:124-131`; bot `base.py:758-762` (фолбэк на `AYLA_SERVICE_TOKEN`) | `compare_digest` | `""` |
| `MAX_BOT_*_WEBHOOK_SECRET` / `MAX_WEBHOOK_SECRET` | `X-Max-Bot-Api-Secret` | bot `apps/channels/bot_registry.py:384` | `compare_digest` | `""` |
| `Tenant.telegram_webhook_secret` | `X-Telegram-Bot-Api-Secret-Token` | bot `apps/channels/telegram/webhook.py:99-102` | `compare_digest` | на строке тенанта, не в settings |
| `EVENT_INGEST_HMAC_SECRET` | `X-Ayla-Event-Signature` | bot `apps/eventbus/ingest_security.py:124-130` | `compare_digest` + окно 300 с | `""` (`base.py:2138`) |
| `MYSITE_WEBHOOK_HMAC_SECRET` | `X-Signature` | bot `apps/catalog/webhooks/views.py:169-178` | `compare_digest`, **анти-реплея нет** | `""`; обязателен в проде |
| `SALON_KNOWLEDGE_WEBHOOK_SECRET` | `X-Signature-256` | bot `apps/kb/webhooks.py:251-261` | `compare_digest`, **анти-реплея нет** | `""` (`base.py:2116`) |
| `MASTER_SESSION_SECRET` | — | bot `apps/master_api/auth.py:141` | `TimestampSigner`; **не потребляется** (F-07) | `""` → фолбэк на `SECRET_KEY` |

**Ротации нет нигде.** Все чтения — одно имя настройки, одно значение. Ни списка допустимых
ключей, ни `key_id`, ни окна перекрытия. Слово «rotate» встречается только в комментариях-инструкциях
ops: catalog `djangoProject/settings/base.py:741,748-749,762,771-773` и
bot `apps/eventbus/ingest_security.py:6` («Quarterly rotation»). Это **обещание в докстринге
без механизма**: ротация `AYLA_INTERNAL_API_TOKEN` — окно, в котором одна из сторон отвергает другую.

Fail-closed при пустом секрете есть везде в permission-классах и в аутентификаторе.
Единственное исключение — Basic-auth биллингового вебхука: `djangoproject-catalog:billing/webhooks.py:41-42`
возвращает `True` при незаданных `YOOKASSA_WEBHOOK_BASIC_AUTH_USER/PASS`, а в `_REQUIRED_PROD_ENV`
(`prod.py:38-43`) их нет. Остаётся только IP-аллоулист. Живые значения — `UNKNOWN_NOT_MEASURED`.

---

## 5. Находки — MAX conversation entry

#### F-22. Журнал коммитится ДО постановки в очередь — сообщение теряется молча — `PARTIAL`, PILOT IMPACT `STOP`

`ai-bot-platform:apps/ingress/services.py:179-195` — запись журнала в **собственной**
`transaction.atomic()`:

```python
        with transaction.atomic():
            row = WebhookJournal.objects.create(
                channel=channel,
                external_event_id=external_event_id,
                raw_payload=raw_payload,
                resolved_tenant=tenant,
                trace_id=trace_id,
            )
        created = True
    except IntegrityError:
        row = WebhookJournal.objects.get(channel=channel, external_event_id=external_event_id)
        created = False
```

`ai-bot-platform:apps/ingress/views.py:136-157` — постановка в очередь **после** и без обёртки:

```python
    journal_row, created = record_webhook(
        channel="max",
        external_event_id=external_event_id,
        raw_payload=payload,
        channel_token=secret_got,
    )

    if created:
        ...
        enqueue(
            channel=bot.stream,
            payload=payload,
            tenant_id=resolved_tenant_id,
        )
```

Если `enqueue` бросит (Redis недоступен, `XGROUP CREATE` упал), вьюха отдаст 500; MAX повторит
доставку; `record_webhook` на повторе вернёт `created=False`; ветка `enqueue` не выполнится, а
`else` просто залогирует `dedup`. **Сообщение исчезает без следа для получателя и без ошибки
для отправителя.**

Теста на этот сценарий нет — §6.

#### F-23. `WebhookJournal.processed_at` никто не пишет — `DEAD_CODE` / `STALE_SPEC`

`ai-bot-platform:apps/ingress/models.py:57-61`:

```python
    processed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Set when the consumer XACKs the matching stream entry.",
    )
```

Греп `git grep -n "WebhookJournal" origin/dev -- 'apps/**/*.py'` (без тестов и миграций) даёт:
`apps/ingress/models.py`, `apps/ingress/admin.py`, `apps/ingress/services.py`, `apps/ingress/views.py`
и три упоминания в докстрингах `apps/conversations/models.py`. **Ни одной записи в
`processed_at`.** Восстановительный проход по «журналировано, но не заехало» невозможен даже
вручную по этому полю — оно всегда NULL. Обещанной «Sprint 5 replay infra» на этом поле нет.

#### F-24. Неизвестный тенант: приём не отклоняется, обработчик падает — `PARTIAL`, PILOT IMPACT `DEGRADED`

`ai-bot-platform:apps/ingress/services.py:107-144` — `_resolve_tenant` возвращает `None`, когда
токена нет ни в реестре, ни в карте, **и** когда для объявленного бота `Tenant.DoesNotExist`
(с `logger.warning("ingress.bot_tenant_unknown ...")`).

Далее `ai-bot-platform:apps/channels/handlers.py:24-49`: `MaxHandler` — `requires_tenant=True`
(унаследованное умолчание), но enforcement под флагом.
`ai-bot-platform:config/settings/base.py:283`:

```python
STRICT_TENANT_REFUSE = os.environ.get("STRICT_TENANT_REFUSE", "false").lower() == "true"
```

При умолчании `False` — «log-only»: ERROR в лог, обработчик **всё равно запускается** с
`tenant_scope(None)`, и `resolve_or_create_bot_user` бросает `ValueError`
(`apps/identity/services/resolver.py:81-86`). Запись остаётся в PEL; автоматического DLQ-ретрая
нет — `apps/workers/base.py:143-146` говорит прямым текстом: «PEL retention is the contract; do
not rely on automatic DLQ retry». `PEL_REAPER_ENABLED` — умолчание `false` (`base.py:314`).

`STRICT_TENANT_SCOPE` — умолчание `audit` (`base.py:256`); `config/settings/production.py` **ни
одного** из трёх флагов не переопределяет. Живые значения — `UNKNOWN_NOT_MEASURED`, §9.

Отмечаю сразу: общий свод (§3) утверждает, что «на пилоте `STRICT_TENANT_SCOPE='strict'`».
Этим замером это **не подтверждено и не опровергнуто** — в коде умолчание `audit`, в
`production.py` переопределения нет, `strict` жёстко стоит только в `staging.py:27`. Это
расхождение между сводом и кодом надо снять на контуре (§9), а не считать решённым.

#### F-25. Telegram: идемпотентности приёма нет — `MISSING`, PILOT IMPACT `DEGRADED`

Аутентификация честная — `ai-bot-platform:apps/channels/telegram/webhook.py:99-102`:

```python
    expected_secret = (tenant.telegram_webhook_secret or "").encode("utf-8")
    got_secret = (request.headers.get(_SECRET_HEADER, "") or "").encode("utf-8")

    if not expected_secret or not hmac.compare_digest(expected_secret, got_secret):
```

Тенант — из slug в URL; неизвестный / неактивный / ненастроенный отдают одинаковый 404 без
утечки существования slug (`webhook.py:91-97,169-191`). Секрет — на строке тенанта, не в
глобальных настройках.

Дедупа по `update_id` **нет**: греп `update_id` по `apps/` даёт только докстринг парсера и
тесты; греп `dedup|idempot|already_processed` по `apps/channels/telegram/` — ноль. Повторная
доставка обрабатывается повторно.

#### F-26. Catalog- и KB-вебхуки без анти-реплея — `PARTIAL`

`ai-bot-platform:apps/catalog/webhooks/views.py:169-178` и `apps/kb/webhooks.py:251-261` — HMAC по
сырому телу через `compare_digest`, но ни timestamp, ни nonce. Подписанный запрос воспроизводим
бесконечно. У catalog-вебхука отказ вдобавок отдаётся как `200` — различить его можно только по
аудиту `catalog.webhook.rejected`.

Для контраста, eventbus-ingest окно имеет — `apps/eventbus/ingest_security.py:31`
`TIMESTAMP_WINDOW_S: Final[float] = 300.0`, — плюс IP-резолвер, рейт-лимит и дедуп по `event_id`
(`IngestDedupe`). Это самая защищённая входная точка контура; MAX-ingress рядом с ней выглядит
беднее (F-02).

---

## 6. Реальность тестов

**Что покрыто:**

| Предмет | Тесты | Уровень |
|---|---|---|
| Реестр ботов, дедуп секретов, constant-time | `bot:apps/channels/tests/test_bot_registry.py` | `UNIT_ONLY` |
| MAX-ingress: 401/400/200, дедуп | `bot:apps/ingress/tests/*`, `apps/channels/tests/test_max_subscribe_webhook.py` | `UNIT_ONLY` |
| initData: подпись, TTL, malformed | `bot:apps/miniapp_api/tests/*`, `apps/master_api/tests/test_auth.py` | `UNIT_ONLY` |
| Telegram webhook: секрет, 403/404 | `bot:apps/channels/telegram/tests/test_webhook.py` | `UNIT_ONLY` |
| `IsTenantMember` | `catalog:tenants/tests/test_middleware_and_permission.py:169-261` | `UNIT_ONLY` — **единственный** permission-класс каталога с прямым тестом |
| Остальные 11 классов каталога | косвенно, через тесты вьюх | `CONTRACT_ONLY` |
| Паритет двух MAX-обработчиков по безопасности | `bot:apps/channels/tests/test_handler_safety_parity.py` | `UNIT_ONLY`, но пинит расхождение — редкий честный случай |
| Инвариант `ayla_user_id` ↔ память | `bot:apps/identity/tests/test_ayla_user_id_memory_invariant.py` | `UNIT_ONLY` |
| Удаление `UserPreferences.allergies` | `bot:apps/identity/tests/test_drf1371_allergies_removed.py` | `UNIT_ONLY`, сторож |
| Рассинхрон колонки согласия и реестра | `bot:tools/lint/consent_column_guard.py` | линт, не тест — но ловит класс, который стрелял трижды |
| Роут-таблица салонной поверхности против схемы Ayla | `bot:apps/integrations/ayla/tests/test_contract_route_table.py` | `CROSS_BOUNDARY` (нужна живая Ayla) |

**Чего теста НЕТ — важнее списка выше:**

1. **Нет теста на потерю сообщения при падении `enqueue` после коммита журнала** (F-22). Он делается замоканным `enqueue`, бросающим исключение, повтором запроса и проверкой, что событие всё-таки ушло в стрим. Такого теста нет.
2. **Нет ни одного теста, который отправил бы во внутреннюю ручку каталога ЧУЖОЙ `user_id` и убедился в 403.** `catalog:users/tests/test_personal_context_api.py` и `test_personal_data_internal_api.py` ходят под правильным id — они проверяют, что ручка **работает**, а не что она **защищена**.
3. **Нет теста, доказывающего, что `X-External-User-ID` нельзя подменить.** Есть обратный — что подмена работает; это и есть контракт.
4. **Нет теста, что `IsBotServiceWithVerifiedClient` кого-то отвергает по признаку «клиент не тот»** — потому что класс этого не делает.
5. **Нет дедупа Telegram — соответственно и теста на повторную доставку.**
6. **Нет теста на анти-реплей catalog- и KB-вебхуков** (нечего тестировать).
7. **Нет теста, что после «удалить мои данные» в `WebhookJournal` не остаётся текста человека** (F-27).
8. **Замечание по правилу «фикстура, а не система»:** тест роут-таблицы салонной поверхности — единственная проверка, где обе стороны не построены одним автором. Но она пришпилена к **Ayla dev `d20efa56`** (`bot:apps/integrations/ayla/salon_surface.py:30-32`), а канон замера — `95c917e6`. Совпадают ли они, этим замером не проверено.

Тесты **не запускались**: замер read-only. Ни одной цифры «N passed» в этом отчёте нет и быть
не должно.

---

## 7. Находки — приватность

### 7.1 Согласия

#### F-27. Перечень типов согласия — `EXISTS`

`ai-bot-platform:apps/consent/models.py:63-77` (дословно):

```python
    class ConsentType(models.TextChoices):
        # Sprint 3 ships 4 types per PHASE0_DESIGN §3.7. New types added
        # in later sprints follow the same ADR-0007-style alter_choices
        # migration recipe.
        PERSONAL_DATA = "personal_data", "Personal data (152-ФЗ)"
        MARKETING = "marketing", "Marketing"
        PHOTO_BIOMETRIC = "photo_biometric", "Photo / biometric"
        HEALTH = "health", "Health"
        # Memory zones (MEMORY_CONSENT_SPEC, founder 2026-07-03). Global per
        # ayla_user_id — checked cross-tenant via has_memory_consent(), not the
        # tenant-scoped has_consent(). green активна в пилоте; yellow/red —
        # фундамент, активный сбор за отдельным flow.
        MEMORY_GREEN = "memory_green", "Memory — green zone"
        MEMORY_YELLOW = "memory_yellow", "Memory — yellow zone"
        MEMORY_RED = "memory_red", "Memory — red zone (special category)"
```

Миграция `apps/consent/migrations/0002_alter_consentrecord_consent_type.py:16-24` содержит те же
семь значений.

#### F-28. Где стоят гейты и что происходит без согласия — `EXISTS` / `PARTIAL`

15 живых гейтов. Все **fail-closed** (`except Exception → False`) и все — **тихая деградация**,
не отказ:

| Ручка / обработчик | Тип | Без согласия |
|---|---|---|
| `bot:apps/orchestrator/food_history.py:291` — чтение дневника у Ayla | PERSONAL_DATA **и** HEALTH | `TodayDiary(Status.NO_CONSENT)`, HTTP-вызов не делается |
| `bot:apps/skills/food_scanner/skill.py:375` | PERSONAL_DATA + HEALTH | сканер работает, история не подмешивается |
| `bot:apps/nutrition_proactive/coach.py:125` (celery beat) | PERSONAL_DATA + HEALTH | `Decision(..., False, "no_health_consent")` — не отправляется |
| `bot:apps/orchestrator/coach_observation.py:190` | HEALTH | `return None` |
| `bot:apps/orchestrator/personal_surface.py:379` | PERSONAL_DATA | показывается `CONSENT_CLOSED_TEXT` |
| `bot:apps/notifications/proactive.py:185` — любое проактивное сообщение | PERSONAL_DATA + `required_consents` | slug-причина, отправка блокируется |
| `bot:apps/channels/max/global_onboarding.py:451` | PERSONAL_DATA | экран согласия |
| `bot:apps/channels/max/handler.py:1876` — память на ход | PERSONAL_DATA | `ayla_user_id = None`, ход не ломается |
| `bot:apps/identity/services/memory_inferred.py:96` | PERSONAL_DATA | `return 0` + лог |
| `bot:apps/identity/services/personal_context.py:82` | MEMORY_GREEN (кросс-тенантно) | `GateStatus.BLOCKED_CONSENT` |
| `bot:apps/orchestrator/memory/food.py:471,567,576` | MEMORY_GREEN / PERSONAL_DATA | тихий пропуск |
| `bot:apps/orchestrator/memory/personal_context.py:57` | PERSONAL_DATA | тихий пропуск |
| `bot:apps/orchestrator/memory_block.py:188` — подмешивание в промпт | PERSONAL_DATA | `return` |
| `bot:apps/consent/health.py:125` | HEALTH | булев для экрана |

#### F-29. `@consent_required` — `DEAD_CODE`, PILOT IMPACT `DEGRADED`

Декоратор `apps/consent/decorators.py:61-95` бросает `ConsentDenied` и эмитит `safety_triggered`.
Греп `consent_required` по всему репозиторию (проверено лично):

```
origin/dev:apps/consent/decorators.py:3, :54, :61        — определение
origin/dev:apps/consent/tests/test_decorators.py:1,7,28,35,90 — тесты
origin/dev:apps/skills/food_scanner/skill.py:472         — строка "food_scanner_consent_required", к декоратору отношения не имеет
```

**Production-вызывающего нет.** `ConsentDenied` в бою не поднимается никогда. Механизм жёсткого
отказа существует, покрыт тестами и не подключён ни к одной ручке.

#### F-30. `PHOTO_BIOMETRIC` объявлен, но не читается и не пишется — `DEAD_CODE` (тип) / `PARTIAL` (гейт), PILOT IMPACT `STOP`

Греп `photo_biometric|PHOTO_BIOMETRIC` (проверено лично): только `models.py:32,69`, две миграции,
докстринг декоратора `decorators.py:66` и тесты. **Ноль записей, ноль чтений в проде.**

При этом фото пользователя реально загружаются и уходят на распознавание
(`apps/skills/food_scanner/skill.py:187 _handle_photo` → `NutritionClient.scan_photo`). Гейт
стоит не на реестре, а на голой колонке-таймстемпе —
`ai-bot-platform:apps/skills/food_scanner/skill.py:463-473`:

```python
    consent_at = getattr(context.bot_user, "food_scanner_consent_at", None)
    if not isinstance(consent_at, _datetime):
        logger.info(
            "food_scanner.gate.consent_missing kind=%s conv=%s",
            kind,
            getattr(context.conversation, "id", None),
        )
        return SkillResult(
            reply_text=CONSENT_REQUIRED_FALLBACK,
            meta={"reply_kind": "food_scanner_consent_required"},
        )
```

Это ровно тот shortcut, который докстринг реестра запрещает (`apps/consent/models.py:4-7`:
«there is no "consent is implied by event X" shortcut»). Собственный линт репозитория это
фиксирует — `ai-bot-platform:tools/lint/consent_column_guard.py:176-179`:

```python
    ``food_scanner_consent_at`` — a different field, with no
    ``ConsentRecord`` behind it and therefore nothing to reconcile it
    against — does not match.
```

**Последствие:** отзыв согласия через реестр (`_PERSONAL_DATA_CASCADE`, `apps/consent/services.py:473-480`)
**не гасит** `food_scanner_consent_at` — колонка в каскад не входит. Человек отозвал согласие,
а фото-сканер продолжает считать себя разрешённым. Тот же линт документирует, что именно такая
рассинхронизация уже стреляла трижды (DRF-1301 / 1307 / 1314, `consent_column_guard.py:18-25`).

#### F-31. `MARKETING` пишется, но ни один путь отправки его не читает — `PARTIAL`, PILOT IMPACT `DEGRADED`

Запись: `bot:apps/consent/customer.py:399 _apply_marketing`, `:436 set_marketing`. Каскад отзыва:
`apps/consent/services.py:476`. Чтение как гейта — только у одного вызывающего,
`apps/wellness_proactive/tasks.py:270`. Ни один промо / рассылочный путь
`has_global_consent(..., MARKETING)` не вызывает; у follow-up'ов это сказано явно —
`apps/bookings/followups.py:122`: «Deliberately NOT gated on ``ConsentType.MARKETING``».

#### F-32. `MEMORY_YELLOW` / `MEMORY_RED` недостижимы; вся red-zone machinery работает на пустой таблице — `DEAD_CODE`

`has_memory_consent(..., "yellow"|"red")` поддерживается (`apps/consent/services.py:450-454`), но
все три production-вызова передают `"green"`. Запись жёлтого и красного запрещена на уровне
writer'а — `ai-bot-platform:apps/identity/services/memory_writer.py:58-75` `_check_minor_protection`
всегда бросает `MinorProtectionLookupFailed`.

Следствие: `RedZoneReader`, `RedZoneAccessLog`, DB-триггер `migrations/0008_red_zone_db_security.py`
и AST-линт `tools/lint/red_zone_guard.py` сегодня охраняют **пустую таблицу**. Это не дефект —
это факт, который нельзя путать с «защита медданных работает».

#### F-33. Анкета питания шлёт профиль в Ayla без проверки согласия — `MISSING`, PILOT IMPACT `STOP`

`ai-bot-platform:apps/skills/nutrition_anketa/skill.py:180-190` POST'ит профиль без единой
проверки. Греп `consent` по `apps/skills/nutrition_anketa/*.py` даёт ровно одну строку — и это
комментарий, `skill.py:55`:

```python
* Consent screen — assumed handled at tenant onboarding; not per-skill.
```

Тип `HEALTH` определён (F-27), но здесь не спрашивается.

#### F-34. Реестра согласий в каталоге НЕТ — `MISSING`, PILOT IMPACT `STOP`

`git grep -n "class .*Consent" origin/dev -- '*/models.py'` по `djangoproject-catalog`
(проверено лично) даёт **единственное** попадание:

```
origin/dev:billing/models.py:299:class BillingConsent(models.Model):
```

— и это про платежи. Греп `consent` по `nutrition/views.py` и `nutrition/services/*.py` — **ноль
совпадений**. То есть медданные (§7.2) пишутся и читаются на стороне каталога вообще без понятия
«согласие»; вся машинерия согласий живёт только в боте, а хранилище — в каталоге.

### 7.2 Данные о здоровье

#### F-35. `NutritionProfile.health_flags` — спецкатегория 152-ФЗ ст.10 открытым текстом — `MISSING` (шифрование), PILOT IMPACT `STOP`

`djangoproject-catalog:nutrition/models.py:424-425` (сверено лично):

```python
    # Health flags + skipped markers + allergies
    health_flags = models.JSONField(default=dict, blank=True)
```

Закрытый список ключей — `djangoproject-catalog:nutrition/serializers.py:427-434`:

```python
_HEALTH_FLAG_KEYS = {
    "pregnant", "breastfeeding",
    "diabetes_t1", "diabetes_t2", "prediabetes",
    "hypertension", "gi_problems", "thyroid", "menopause",
    "eating_disorder", "meds",
    "allergies", "allergies_vague",
    "gender_skipped", "age_skipped", "height_skipped", "weight_skipped",
}
```

**Шифрования нет.** Греп `git grep -nEi "Encrypted|encrypt\(|fernet|pgcrypto" origin/dev -- '*.py'`
по всему `djangoproject-catalog` (выполнен лично) → **ноль совпадений**. Field-level шифрования в
каталоге не существует ни для одного поля.

Кто читает: мобильный клиент (`nutrition/views.py:96,375,517,557,602,643`, гейт
`[IsAuthenticated, IsClientApp, IsClient]`), бот (`[IsServiceAccount]`, ~18 ручек) — **и Django
admin без какого-либо ограничения роли**, `djangoproject-catalog:nutrition/admin.py:96`:

```python
        ("Health flags", {"fields": ("health_flags",)}),
```

Поле не в `readonly_fields` (`admin.py:82-88`) — любой staff с доступом к админке каталога видит
и правит беременность, РПП, диабет, лекарства.

Рядом — `djangoproject-catalog:users/models.py:569-573` `UserPersonalContext.skin_sensitivities`
(«Free-form list of allergens / sensitivities»), тоже без шифрования.

#### F-36. На стороне бота медданные ШИФРУЮТСЯ — `EXISTS` (контраст)

`ai-bot-platform:apps/identity/models.py:860-868`:

```python
    content = encrypt(
        models.JSONField(
            default=dict,
            help_text="The fact payload. Encrypted at rest with the Fernet "
            "key via django-cryptography-django5 EncryptedJSONField (per "
            "ADR-0006). Red entries additionally hash-pepper'd before "
            "encryption — pepper management tracked in ADR-0011 §13.5.",
        )
    )
```

`KIND_CHOICES` включает `contraindication` и `symptom` (`models.py:743-751`). Красная зона
читается только через `apps/identity/services/red_zone_reader.py` с обязательной записью
`RedZoneAccessLog` (`red_zone_reader.py:175-180`).

**Но:** см. F-32 — красных строк в пилоте физически нет. И **не шифруется** прозаический профиль:
`apps/identity/models.py:655` — «Application-side capped at 8 KB. NOT encrypted at storage layer».

Асимметрия между репозиториями — самое неприятное здесь: одна и та же категория данных
шифруется у бота и лежит открытой у каталога, а хранит её именно каталог.

#### F-37. TTL памяти объявлен, но не исполняется никем — `DEAD_CODE`, PILOT IMPACT `DEGRADED`

Докстринг `ai-bot-platform:apps/identity/models.py:705-714` обещает: yellow — «365-day TTL from
last use», red — «90-day TTL». Поля есть (`ttl_days` :880, `expires_at` :951), пишутся в
`memory_writer.py:195`. Греп `ttl_purge|DELETION_REASON_TTL_PURGE` (выполнен лично) даёт:

```
origin/dev:apps/identity/migrations/0007_user_personal_context.py:429
origin/dev:apps/identity/migrations/0012_alter_userpreferences_managers_and_more.py:96
origin/dev:apps/identity/models.py:756  DELETION_REASON_TTL_PURGE = "ttl_purge"
origin/dev:apps/identity/models.py:763  (DELETION_REASON_TTL_PURGE, "Auto-purged by TTL sweep")
```

**Ни одного производителя.** Ни одной задачи, читающей `expires_at` у `MemoryEntry`
(`expires_at__lt` встречается только у `apps/replay/tasks.py:58` и `apps/tools/tasks.py:76`).
Причина удаления без механизма удаления.

#### F-38. `UserPreferences.allergies` удалена — `EXISTS` (положительная находка)

`ai-bot-platform:apps/identity/migrations/0020_drop_userpreferences_allergies.py` + обоснование в
`apps/identity/models.py:345-354`: спецкатегория 152-ФЗ ст.10 вне machinery зон / согласий / TTL,
и владелец постановил, что мастер противопоказаний видеть не должен. Сторож —
`apps/identity/tests/test_drf1371_allergies_removed.py`.

#### F-39. `legacy_maxbot/` не подключён — `DEAD_CODE`

`legacy_maxbot/handlers/health_screening.py:66-69` пишет `bot_user.health_flags`,
`legacy_maxbot/ai_concierge.py:310` читает. Но `git grep -n "legacy_maxbot" origin/dev -- 'apps/' 'config/'`
даёт только комментарии и докстринги, ни одного `import`; в `INSTALLED_APPS` строки нет; поля
`health_flags` на `identity.BotUser` не существует. Весь каталог — не подключённый код. Важно для
раздела «логи»: самые грубые утечки текста живут именно там и сегодня не исполняются.

### 7.3 Удаление персданных (152-ФЗ)

#### F-40. Поверхность удаления на стороне Ayla — ровно ОДНА модель — `PARTIAL`, PILOT IMPACT `STOP`

`djangoproject-catalog:users/personal_context_erasure.py` прочитан целиком (166 строк). Список
импортов моделей (сверено лично):

```
40:from users.models import UserPersonalContext
41:from users.personal_context_events import emit_personal_data_deleted
```

Больше ни одной модели файл не импортирует. Стирание — `personal_context_erasure.py:100-107`:

```python
def _tombstone(ctx: UserPersonalContext) -> None:
    """Reset every declared field and mark the whole row erased."""
    for name in declared_fields():
        setattr(ctx, name, default_for(name))
    ctx.data_sources = {name: ERASED for name in declared_fields()}
    ctx.skipped_questions = {}
    ctx.last_asked_at = {}
    ctx.save()
```

Сильная сторона: список полей выведен из модели (`_meta.concrete_fields`, `:71`) — новое поле
`UserPersonalContext` стирается автоматически. Слабая: **это и есть весь периметр**.

Вызывающие (все четыре живые): `personal_context_views.py:193` (`initiator="app"`),
`personal_data_api.py:172` (`"internal_api"` — сюда стучится бот по C5.2),
`internal_personal_context_api.py:189` (`"bot_forget_all"`), `users/services.py:1228`
(`"account_delete"`).

#### F-41. `ClientGoal` не входит в удаление — ПОДТВЕРЖДЕНО, PILOT IMPACT `STOP`

`djangoproject-catalog:goals/models.py:44-64` (сверено лично):

```python
    client = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="client_goals",
    )
    ...
    # Дословная формулировка пользователя. Хранится даже при распознанном
    # ключе — это будущий датасет формулировок (OD-2).
    goal_text = models.TextField(
        null=True,
        blank=True,
        help_text="Дословный свободный ввод пользователя; не нормализуется",
    )
```

`on_delete=PROTECT` — даже hard-delete `User` был бы заблокирован. Ни одного `.delete()` по
`ClientGoal` в production-коде: единственная мутация — `goals/api.py:144`
`ClientGoal.objects.filter(client=client, is_active=True).update(...)`, деактивация старой цели.

#### F-42. Что ЕЩЁ переживает удаление — прямой ответ на вопрос — `MISSING`, PILOT IMPACT `STOP`

| Сущность / поле | Файл | Почему переживает |
|---|---|---|
| `goals.ClientGoal.goal_text` | catalog `goals/models.py:60` | нет удаления; FK PROTECT |
| `goals.GoalAnketaRun` | catalog `goals/models.py:121` | FK PROTECT, нет удаления |
| `goals.GoalAnketaAnswer.answer_text` («Дословный свободный ввод; не нормализуется») | catalog `goals/models.py:184` | CASCADE от Run, но Run не удаляется никогда |
| `nutrition.NutritionProfile` целиком, **включая `health_flags`** | catalog `nutrition/models.py:353` | ни одного `.delete()`; User только soft-delete |
| `nutrition.FoodLog` / `FoodScan` / `WaterLog` / `WaterEntry` — весь пищевой дневник | catalog `nutrition/models.py:100,27,209,495` | то же |
| `users.Profile.bio`, `users.Profile.city` | catalog `users/models.py:154-155` | `delete_account` обнуляет только `avatar` и `full_name` |
| `wellness.PersonalPlan` / `ProgressObservation` / `DesiredOutcome` | catalog `wellness/models.py:93,207,23` | нет удаления |
| `reviews.Review` | catalog `reviews/models.py:11` | нет удаления |
| `ai.Message.content` / `ai.Conversation` | catalog `ai/models.py:103,34` | нет удаления (§7.4) |
| `identity.ClientProfile` (RFM, LTV, churn_risk, sentiment_score, last_review_rating) | bot | не в каскаде `privacy.py` |
| `loyalty.LoyaltyAccount` | bot | не в каскаде |
| `handoff.AdminTask.transcript_snapshot` | bot `apps/handoff/models.py:132` | явно вне периметра |
| `ingress.WebhookJournal.raw_payload` — **сырой текст всех сообщений** | bot `apps/ingress/models.py:37` | см. F-45 |
| `conversations.ArchivedMessage.body` | bot | по решению владельца хранится 90 дней после «удалить всё» |

`djangoproject-catalog:users/services.py:1152-1190` — «удаление аккаунта» полностью:

```python
    def delete_account(user, reason: str = "") -> None:
        """
        Soft delete user account: anonymize PII, deactivate, schedule cleanup.
...
        user.phone = None
        user.email = ""
        user.first_name = "Удалён"
        user.last_name = ""
...
        if hasattr(user, 'profile'):
            profile = user.profile
            profile.avatar = None
            profile.full_name = "Удалён"
            profile.save(update_fields=["avatar", "full_name"])
```

Докстринг обещает «schedule cleanup» — **в теле нет никакого scheduling**, и греп
`deleted_at__lt|deleted_at__lte|cleanup_deleted` отложенной чистки удалённых пользователей не
находит. `bio`, `city`, координаты по умолчанию не трогаются. Ещё одно расхождение докстринга с кодом.

#### F-43. Экспорт и удаление по одному протоколу C5 не симметричны — `PARTIAL`, PILOT IMPACT `STOP`

`djangoproject-catalog:users/personal_data_api.py:108-114` — **экспорт** отдаёт:

```python
        profile_data = {
            "phone": user.phone or "",
            "email": user.email or "",
            "full_name": (profile.full_name if profile else "") or "",
            "bio": (profile.bio if profile else "") or "",
            "city": (profile.city if profile else "") or "",
        }
```

`djangoproject-catalog:users/personal_data_api.py:162-172` — **удаление** по тому же C5:

```python
    def delete(self, request: Request, user_id: UUID) -> Response:
        user = _get_user_for_erasure(user_id)
        ...
        scope = erase_personal_context(user, initiator="internal_api")
```

Те же `phone`, `email`, `full_name`, `bio`, `city` объявлены персданными на выгрузке и **не
входят в поверхность удаления по тому же протоколу**. Это единственная пара ручек, где
асимметрия видна на одном экране кода.

#### F-44. Обещание пользователю расходится с фактом — `STALE_SPEC`, PILOT IMPACT `STOP`

`ai-bot-platform:apps/consent/customer.py:108-111`:

```python
#: Что отзыв НЕ трогает и почему. Транзакционные записи хранятся по
#: обязанности оператора, а не по согласию, поэтому отзыв согласия их не
#: удаляет — и человеку это должно быть сказано до нажатия, а не после.
DATA_STORAGE_REVOCATION_RETAINED = ("bookings", "payments")
```

На экране человеку названы **только** бронирования и платежи. Реально переживают ещё
четырнадцать позиций из таблицы F-42, включая `health_flags` и дословные формулировки целей.

Честная часть: `ai-bot-platform:apps/identity/export_coverage.py:153-200` `NON_REGISTRY_STORES`
**сама** объявляет пользователю часть этих пробелов в выгрузке — в частности
`conversations.ArchivedMessage.body` («он просил удалить, и часть текста осталась», :172-173) и
жёлтую / красную память. Это единственный найденный механизм, не дающий пробелу быть молчаливым;
у каталога аналога нет.

Все четыре модуля бота (`privacy.py`, `forget_all_sweep.py`, `memory_deleter.py`,
`export_coverage.py`) имеют production-вызывающих — **ни один не мёртв**. Beat-запись дословно,
`ai-bot-platform:config/settings/base.py:1288-1291`:

```python
    "identity_forget_all_sweep": {
        "task": "apps.identity.tasks.forget_all_sweep",
        "schedule": crontab(minute="50"),
    },
```

Стирание PII на стороне бота узкое — `apps/identity/services/privacy.py:263` + `:340`:

```python
_PII_FIELDS: tuple[str, ...] = ("phone", "display_name", "client_name", "avatar_url")
...
        BotUser.all_tenants.filter(id__in=ids).update(context={}, **dict.fromkeys(_PII_FIELDS, ""))
        UserPreferences.all_tenants.filter(bot_user_id__in=ids).delete()
```

`ClientProfile` и `LoyaltyAccount` здесь отсутствуют, хотя оба объявлены персданными в реестре
`apps/identity/personal_fields.py:285-539`.

### 7.4 Сырые транскрипты

#### F-45. Сырой текст каждого сообщения хранится вечно и переживает удаление — `MISSING` (ретеншн), PILOT IMPACT `STOP`

`ai-bot-platform:apps/ingress/views.py:105` — в журнал кладётся **полное тело вебхука**:

```python
        payload: dict[str, Any] = json.loads(request.body or b"{}")
```

`ai-bot-platform:apps/ingress/models.py:37-41`:

```python
    raw_payload = models.JSONField(
        default=dict,
        blank=True,
        help_text="Full untouched webhook payload. Preserved for replay (Sprint 5).",
    )
```

Менеджер — **не тенантный** (`models.py:66` `objects = models.Manager()`).

Что доказано грепом:

* **TTL нет** — в `config/settings/base.py` есть `AUDIT_LOG_RETENTION_DAYS` (90),
  `IDEMPOTENCY_KEY_RETENTION_DAYS` (7), `PAYMENT_EVENT_RETENTION_DAYS` (90),
  `ANONYMIZED_DIALOGUE_RETENTION_DAYS` (90), `REPLAY_RETENTION_DAYS` (30) — и **ничего** про `WebhookJournal`.
* **Задачи очистки нет** — в `CELERY_BEAT_SCHEDULE` (`base.py:1199-1400`) записи про ingress нет.
* **В поверхность удаления не входит** — греп `WebhookJournal` по `apps/identity/services/privacy.py`,
  `forget_all_sweep.py`, `memory_deleter.py`, `apps/conversations/erasure.py`: ноль попаданий.

Итог: человек нажимает «удалить мои данные», каскад `privacy.delete_personal_data` отрабатывает
шесть шагов, а **дословный текст всех его сообщений остаётся в `ingress_webhookjournal.raw_payload`
навсегда** — доступный любому с доступом к БД или к read-only экрану `apps/ingress/admin.py`.

#### F-46. Карта ретеншна транскриптов — `PARTIAL`

| Repo | Модель | Файл:строка | Сырой текст | TTL | Реальная чистка |
|---|---|---|---|---|---|
| bot | `Message` | `apps/conversations/models.py:395` | `content`, `rendered_text` | нет | **НЕТ** — только анонимизация по запросу |
| bot | `ArchivedMessage` | `apps/conversations/models.py:517` | `body`, `rendered_body` | `retention_until` (90 д.) | **ЕСТЬ** |
| bot | `StaffAssistantMessage` | `apps/conversations/models.py:916` | `content` | нет | **НЕТ** |
| bot | `MasterAdminMessage` | `apps/internal_chat/models.py:379` | `body` | нет | **НЕТ** |
| bot | `AdminTask.transcript_snapshot` | `apps/handoff/models.py:132` | JSON-снимок сообщений | нет | **НЕТ**, явно вне erasure |
| bot | `WebhookJournal.raw_payload` | `apps/ingress/models.py:37` | полное тело вебхука | нет | **НЕТ** (F-45) |
| bot | `ReplayTrace.pipeline_steps` | `apps/replay/models.py:44` | redacted input/final | `expires_at` (30 д.) | **ЕСТЬ** |
| bot | Redis short-term | `apps/orchestrator/memory/short_term.py` | окно сообщений | 24 ч | **ЕСТЬ** |
| catalog | `ai.Message` | `ai/models.py:103` | `content` | нет | **НЕТ** |
| catalog | `ai.Conversation` | `ai/models.py:34` | — | `deleted_at` (soft, по запросу) | **НЕТ** |

Единственный реальный ретеншн диалоговых тел — `ai-bot-platform:apps/conversations/models.py:643-645`:

```python
    retention_until = models.DateTimeField(
        db_index=True,
        help_text="The named term. purge_expired_archived_messages "
        "hard-deletes the row past this instant.",
    )
```

и задача `apps/conversations/tasks.py:115-155`, стоящая в beat (`base.py`, `purge_expired_archived_messages`,
03:20 UTC). То есть **чистится только уже анонимизированная копия**, а оригинал `Message.content`
не чистится вообще.

`djangoproject-catalog:djangoProject/settings/base.py:955` `CELERY_BEAT_SCHEDULE` — **ни одной
записи, ссылающейся на `ai.`**. Модуль сам это признаёт, `catalog:ai/models.py:7-9`: «History
retention: all messages persisted; LLM context truncated to last 10 in chat_service».

Отдельно: `ai-bot-platform:apps/conversations/erasure.py:97-109` цитирует реестр ПДн строкой
«`Conversation/Message как «Retention НЕ НАЙДЕН»`». Сам файл `docs/152fz/REESTR_PDN_DRAFT.md` в
`origin/dev` **отсутствует** (`git show` → `fatal: path does not exist`) →
`UNKNOWN_NOT_MEASURED` по статусу самого реестра.

### 7.5 Логи

#### F-47. PII-скраббер: у бота есть и узкий, у каталога нет — `PARTIAL` / `MISSING`, PILOT IMPACT `DEGRADED`

`ai-bot-platform:config/settings/base.py:2290-2318`:

```python
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "pii_redactor": {
            "()": "apps.observability.pii_filter.PIIRedactingFilter",
        },
        "context": {
            "()": "apps.observability.logging.ContextFilter",
        },
    },
    ...
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "level": "INFO",
            "filters": ["pii_redactor", "context"],
            "formatter": "json",
        },
    },
```

Фильтр стоит **до** форматтера — правильное место. Но редактирует он **только телефон (RU),
email и карты (Luhn)**; собственный докстринг: «### What is NOT redacted (deliberately, in this
PR) → * **Names** — NER … has high false-positive risk». Свободный текст сообщения, ФИО,
аллергии, цели проходят насквозь.

`djangoproject-catalog:djangoProject/settings/base.py:1149-1189` — единственный фильтр:

```python
    "filters": {
        "request_id": {
            "()": "core.log_filters.RequestIDFilter",
        },
    },
```

`core/log_filters.py:34-41` — только проставляет `request_id`. **PII-скраббера в каталоге нет.**
Модуль `ai/redaction.py::redact_pii` существует, но применяется только перед отправкой в OpenAI
(`ai/application/services/chat_service.py:100`), в `LOGGING` не подключён.

Sentry: у бота `apps/observability/sentry.py:96-109` — `send_default_pii=False` +
`before_send=scrub_event`. У каталога `djangoProject/settings/base.py:1119-1133` —
`send_default_pii=False`, но `_sentry_before_send` — **`return event`, no-op** (собственный
комментарий: «Today: no-op»).

#### F-48. Опасные строки логирования — `PARTIAL`

Живой код, по убыванию риска:

1. `djangoproject-catalog:users/sms.py:37` (сверено лично) —
   `logger.info("SMS [dev mode] to=%s: %s", phone, message)` — полный телефон + полный текст SMS,
   **включая OTP**. Ветка срабатывает, когда `settings.SMS_ENABLED` ложно.
2. `djangoproject-catalog:users/sms.py:84-88` (сверено лично) —
   `logger.error("SMS.RU error to=%s code=%s text=%s", phone, status_code, status_text,)` — полный
   телефон, **на боевом пути отправки**, независимо от `SMS_ENABLED`.
3. `djangoproject-catalog:users/social_auth.py:325` — `logger.info("Phone %s bound to user %s", phone, user.pk)`.
4. `ai-bot-platform:apps/bookings/callbacks.py:339` — `logger.info("bookings.callback.malformed text=%r", text)` — сырой входящий текст без обрезки.
5. `ai-bot-platform:apps/bookings/callbacks.py:674` — то же, `bookings.gate.malformed`.
6. `ai-bot-platform:apps/skills/menu/skill.py:156-160` — `payload=%s` сырым; соседний блок на `:152` специально пишет только длину («Length only, never content») — то есть автор знал правило и в одной строке его применил, а в другой нет.
7. `ai-bot-platform:apps/channels/max/handler.py:2585,2598` — `payload=%r`; payload может содержать base64-кодированные (не хэшированные) стемы поискового запроса (`apps/orchestrator/discovery.py:915 encode_query_ref`).
8. `ai-bot-platform:apps/channels/max/outbound.py:270-275` — `body=%r`, `response.text[:200]`.
9. `djangoproject-catalog:nutrition/providers/yandex.py:126` — `logger.warning("nutrition.yandex.parse_failed text=%s", text[:200])` — данные о питании.
10. `djangoproject-catalog:appointments/application/services/create_booking_service.py:325-332` — свободный текст сотрудника.

Формально более грубые утечки (`text=%r, user_text[:80]`) живут в `legacy_maxbot/handlers/*` —
но этот код не подключён (F-39).

Образцовая строка, задающая планку, — `ai-bot-platform:apps/orchestrator/safety/outbound.py:301`:
`logger.warning("safety.outbound.blocked categories=%s len=%d", ...)` с комментарием «Logging the
sentence would copy the thing we just decided not to show anyone.»

---

## 8. Подтверждённые противоречия (обе стороны дословно)

### П-1. Имя класса против его кода

Имя: `IsBotServiceWithVerifiedClient` (`catalog:users/permissions.py:134`).
Обещание, `users/permissions.py:148-152`:

> «``X-External-User-ID`` (e.g. ``bot:12345``) resolves via ``resolve_external_user`` to an Ayla ``User`` (lazily created as a proxy on first call…)»

Код, `catalog:users/services.py:123-127`:

```python
    user, created = User.objects.select_related("linked_user").get_or_create(
        username=external_user_id,
        defaults={"role": "client", "is_proxy": True, "is_guest": False},
    )
```

«Verified» в имени = «мы завели строку под ту строку, которую вы прислали».

### П-2. Обещание поля против кода

`bot:apps/ingress/models.py:60` — `help_text="Set when the consumer XACKs the matching stream entry."`
Греп по всему репозиторию: ни одной записи в `processed_at`. Поле всегда NULL.

### П-3. Обещание capability против кода

`bot:apps/identity/services/role_resolver.py:192` — capability называется `"view_customer_phone_audited"`.
`role_resolver.py:135-138` — «Until that PR lands the gate passes but no audit is written».

### П-4. Обещание ротации против механизма

`bot:apps/eventbus/ingest_security.py:6` — «Shared secret stored in vault … **Quarterly rotation**».
`ingest_security.py:91,124-130` — ровно одно значение секрета, ни `key_id`, ни списка, ни окна
перекрытия. То же для `AYLA_INTERNAL_API_TOKEN` в обоих репозиториях.

### П-5. Два правила резолва тенанта на одной системе

`bot:apps/identity/services/bot_user_resolver.py:16-19` — «Two surfaces resolving identity by two
rules is how they drift apart (DRF-1128 is the same family), so the rule now lives in one place
and both call it».
`bot:apps/miniapp_api/views.py:180-183` — клиентская поверхность этот модуль **не вызывает**.

### П-6. Запрет shortcut'а против гейта фото-сканера

`bot:apps/consent/models.py:4-7` — «there is no "consent is implied by event X" shortcut».
`bot:apps/skills/food_scanner/skill.py:463-473` — гейт на голой колонке `food_scanner_consent_at`,
без `ConsentRecord`, вне каскада отзыва. Собственный линт (`tools/lint/consent_column_guard.py:176-179`)
это фиксирует и не чинит.

### П-7. Обещание «schedule cleanup» против тела функции

`catalog:users/services.py:1154-1155` — «Soft delete user account: anonymize PII, deactivate,
**schedule cleanup**.»
`users/services.py:1156-1190` — в теле нет ни одного вызова планировщика; отложенной чистки
удалённых пользователей грепом не найдено.

### П-8. Обещание пользователю против периметра удаления

`bot:apps/consent/customer.py:110` — `DATA_STORAGE_REVOCATION_RETAINED = ("bookings", "payments")`.
Фактически переживают ещё четырнадцать позиций (F-42), включая `health_flags` и
`ClientGoal.goal_text`.

### П-9. Свод против кода по `STRICT_TENANT_SCOPE`

`docs/BRIEF_PILOT_READINESS_COMMON.md` §3 — «на пилоте `STRICT_TENANT_SCOPE='strict'`».
`bot:config/settings/base.py:256` — `STRICT_TENANT_SCOPE = os.environ.get("STRICT_TENANT_SCOPE", "audit")`;
`config/settings/production.py` его не переопределяет; `strict` жёстко стоит только в
`config/settings/staging.py:27`. Расхождение не разрешено — снимать на контуре (§9).

---

## 9. Прошу снять на контуре

Всё ниже — read-only, кроме одной явно помеченной проверки. Команды дословно.

**Флаги и умолчания:**
```
ssh <bot-host> "grep -E '^(STRICT_TENANT_SCOPE|STRICT_TENANT_REFUSE|PEL_REAPER_ENABLED|DJANGO_DEBUG|DJANGO_SETTINGS_MODULE|MAX_BOT_TENANT_SLUG|MAX_BOTS)=' /etc/ai-bot-platform/.env"
```
```
ssh <bot-host> "docker exec ayla-bot-web python manage.py shell -c \"from django.conf import settings; print('STRICT_TENANT_SCOPE',settings.STRICT_TENANT_SCOPE); print('STRICT_TENANT_REFUSE',settings.STRICT_TENANT_REFUSE); print('DEBUG',settings.DEBUG); print('MAX_BOT_TENANT_SLUG',repr(settings.MAX_BOT_TENANT_SLUG)); print('registry',[(e.slug,e.tenant_slug,e.stream) for e in settings.MAX_BOT_REGISTRY]); print('SESSION_COOKIE_AGE',settings.SESSION_COOKIE_AGE)\""
```

**Дырка в идемпотентности (F-22, F-23):**
```
ssh <bot-host> "docker exec ayla-bot-web python manage.py shell -c \"from apps.ingress.models import WebhookJournal as W; print('rows',W.objects.count()); print('oldest',W.objects.order_by('received_at').values_list('received_at',flat=True).first()); print('processed_at_set',W.objects.filter(processed_at__isnull=False).count()); print('tenant_null',W.objects.filter(resolved_tenant__isnull=True).count())\""
```
```
ssh <bot-host> "docker exec ayla-bot-redis redis-cli XPENDING ingress:max consumers; docker exec ayla-bot-redis redis-cli XPENDING ingress:max_global consumers; docker exec ayla-bot-redis redis-cli XPENDING ingress:max_salon consumers"
```
```
ssh <bot-host> "docker exec ayla-bot-web python manage.py shell -c \"from apps.audit.models import AuditLog as A; print(A.objects.filter(action='ingress.webhook_unknown_tenant').count())\""
```

**Два идентификатора на мастера (F-13):**
```
ssh <bot-host> "docker exec ayla-bot-web python manage.py shell -c \"from apps.catalog.models import CatalogMaster as M; rows=list(M.all_tenants.values_list('id','ayla_user_id','tenant__slug')); print('total',len(rows)); print('id_ne_user_id',[(str(a),str(b),t) for a,b,t in rows if b and str(a)!=str(b)])\""
```

**Личность across surfaces (F-11):**
```
ssh <bot-host> "docker exec ayla-bot-web python manage.py shell -c \"from apps.identity.models import BotUser as B; from collections import Counter; c=Counter((b.channel,b.channel_user_id) for b in B.all_tenants.all()); print('people',len(c)); print('multi_tenant_people',sum(1 for v in c.values() if v>1)); print('ayla_link_null',B.all_tenants.filter(ayla_user_id__isnull=True).count()); print('consent_at_set',B.all_tenants.filter(consent_at__isnull=False).count()); print('food_consent_set',B.all_tenants.filter(food_scanner_consent_at__isnull=False).count())\""
```

**Секреты и binding (F-14, F-21):**
```
ssh <ayla-host> "docker exec ayla-web python manage.py shell -c \"from django.conf import settings; print('PROVISIONING_SET',bool(settings.AYLA_IDENTITY_PROVISIONING_TOKEN)); print('SAME_AS_INTERNAL',bool(settings.AYLA_IDENTITY_PROVISIONING_TOKEN) and settings.AYLA_IDENTITY_PROVISIONING_TOKEN==settings.AYLA_INTERNAL_API_TOKEN); print('YOOKASSA_BASIC_SET',bool(getattr(settings,'YOOKASSA_WEBHOOK_BASIC_AUTH_USER','')))\""
```
```
ssh <ayla-host> "docker exec ayla-web python manage.py shell -c \"from users.models import User; print('proxies',User.objects.filter(is_proxy=True).count()); print('bound_proxies',User.objects.filter(is_proxy=True, linked_user__isnull=False).count())\""
```

**Приватность: масштаб того, что переживает удаление (F-42, F-45):**
```
ssh <ayla-host> "docker exec ayla-web python manage.py shell -c \"from goals.models import ClientGoal, GoalAnketaAnswer; from nutrition.models import NutritionProfile, FoodLog; print('goals',ClientGoal.objects.count()); print('goal_text_nonempty',ClientGoal.objects.exclude(goal_text__isnull=True).exclude(goal_text='').count()); print('anketa_answers',GoalAnketaAnswer.objects.count()); print('nutrition_profiles',NutritionProfile.objects.count()); print('profiles_with_health_flags',NutritionProfile.objects.exclude(health_flags={}).count()); print('food_logs',FoodLog.objects.count())\""
```
```
ssh <bot-host> "docker exec ayla-bot-web python manage.py shell -c \"from apps.conversations.models import Message; from apps.ingress.models import WebhookJournal as W; print('messages',Message.all_tenants.count()); print('journal_rows',W.objects.count()); print('journal_bytes_approx', W.objects.count() and 'см. pg_total_relation_size ниже')\""
```
```
ssh <bot-host> "docker exec ayla-bot-db psql -U <user> -d <db> -c \"SELECT pg_size_pretty(pg_total_relation_size('ingress_webhookjournal'));\""
```

**Подтверждение DRF-1036 живьём — только на стенде, не на боевой базе, только чтением:**
```
ssh <ayla-host> "curl -s -o /dev/null -w '%{http_code}\n' -H \"Authorization: Bearer \$AYLA_INTERNAL_API_TOKEN\" http://127.0.0.1:8000/api/v1/internal/users/<ЧУЖОЙ-UUID>/personal-data/export/"
```
Ожидание по канону: **200**. Если 200 — DRF-1036 подтверждён живьём. Если 403 — на контуре есть
слой, которого нет в каноне, и это отдельная находка.

**Утечка телефона в публичных отзывах (F-20)** — нужен один реальный `specialist_id`:
```
ssh <ayla-host> "curl -s http://127.0.0.1:8000/api/v1/specialists/<ID>/reviews/ | python -c \"import sys,json; d=json.load(sys.stdin); print([r.get('client_name') for r in (d.get('results') or d)])\""
```
Ожидание: ни одного значения вида `user_7XXXXXXXXXX`.

**Периметр (взгляд снаружи контура, ssh не нужен) — F-02:**
```
curl -s -o /dev/null -w '%{http_code}\n' -X POST https://api-dev.gobeauty.site/api/v1/ingress/max/ -H 'Content-Type: application/json' -d '{}'
```
Ожидание: **401** (секрета нет). Если 403 / 404 от nginx — значит edge-фильтр есть, и F-02 смягчается.

---

## 10. Что НЕ замерено — честный список

1. **Все живые значения окружения.** У окна нет ssh. Не замерены: `STRICT_TENANT_SCOPE`, `STRICT_TENANT_REFUSE`, `PEL_REAPER_ENABLED`, `DJANGO_SETTINGS_MODULE` / `DJANGO_DEBUG`, `MAX_BOT_TENANT_SLUG`, `MAX_BOTS` и состав реестра, `SESSION_COOKIE_AGE`, `ADMINCONSOLE_CLIENT_ACCESS_TTL_MINUTES`, `AYLA_IDENTITY_PROVISIONING_TOKEN` (задан ли), `YOOKASSA_WEBHOOK_BASIC_AUTH_*`.
2. **Расхождение свода и кода по `STRICT_TENANT_SCOPE`** (П-9) не разрешено.
3. **Сетевая изоляция `/api/v1/internal/*`.** `catalog:core/ayla_urls.py:16-24` утверждает «no public exposure», но это комментарий в коде, а не конфигурация ingress.
4. **Фильтрация на edge nginx перед `/api/v1/ingress/max/`** — на уровне приложения её нет, периметр не проверялся.
5. **Используется ли `bind_external_identity` в бою** — от этого зависит, даёт ли подмена `X-External-User-ID` доступ к **реальному** аккаунту жертвы или к изолированной прокси-строке.
6. **Умолчание `invite_expires_at` для мастерского инвайта** — `apps/admin_api/views_invite.py` не читался; поле nullable, при `None` токен бессрочен.
7. **Обязательность `EVENT_INGEST_HMAC_SECRET` и `SALON_KNOWLEDGE_WEBHOOK_SECRET` в проде** — целиком `production.py` не вычитан.
8. **Статус реестра ПДн:** `docs/152fz/REESTR_PDN_DRAFT.md` в `origin/dev` отсутствует, хотя код на него ссылается (`apps/conversations/erasure.py:97-109`).
9. **Совпадает ли Ayla `d20efa56`, к которой пришпилена роут-таблица салонной поверхности, с каноном `95c917e6`.**
10. **Тесты не запускались.** Замер read-only; ни одной цифры «N passed» в отчёте нет.
11. **Отдельный проход по `raise` с f-строками, содержащими PII**, не делался — а именно они попадают в Sentry каталога, где `before_send` — no-op.
12. **`ayla-ai-core` и `ayla-knowledge` не читались** — вне предмета замера.

---

## 11. Точные команды воспроизведения замера

```
git -C C:/Users/user/PycharmProjects/Ayla/ai-bot-platform rev-parse origin/dev
git -C C:/Users/user/PycharmProjects/Ayla/djangoproject-catalog rev-parse origin/dev
git -C .../ai-bot-platform show origin/dev:apps/ingress/views.py
git -C .../ai-bot-platform show origin/dev:apps/ingress/services.py
git -C .../ai-bot-platform show origin/dev:apps/ingress/models.py
git -C .../ai-bot-platform show origin/dev:apps/channels/bot_registry.py
git -C .../ai-bot-platform show origin/dev:apps/channels/handlers.py
git -C .../ai-bot-platform show origin/dev:apps/channels/max/parser.py
git -C .../ai-bot-platform show origin/dev:apps/channels/telegram/webhook.py
git -C .../ai-bot-platform show origin/dev:apps/identity/models.py
git -C .../ai-bot-platform show origin/dev:apps/identity/services/resolver.py
git -C .../ai-bot-platform show origin/dev:apps/identity/services/ayla_link.py
git -C .../ai-bot-platform show origin/dev:apps/identity/services/bot_user_resolver.py
git -C .../ai-bot-platform show origin/dev:apps/identity/services/role_resolver.py
git -C .../ai-bot-platform show origin/dev:apps/identity/services/privacy.py
git -C .../ai-bot-platform show origin/dev:apps/identity/services/staff_invites.py
git -C .../ai-bot-platform show origin/dev:apps/miniapp_api/auth.py
git -C .../ai-bot-platform show origin/dev:apps/miniapp_api/views.py
git -C .../ai-bot-platform show origin/dev:apps/miniapp_api/dev_bypass.py
git -C .../ai-bot-platform show origin/dev:apps/master_api/auth.py
git -C .../ai-bot-platform show origin/dev:apps/admin_api/auth.py
git -C .../ai-bot-platform show origin/dev:apps/adminconsole/client_access.py
git -C .../ai-bot-platform show origin/dev:apps/adminconsole/roles.py
git -C .../ai-bot-platform show origin/dev:apps/consent/models.py
git -C .../ai-bot-platform show origin/dev:apps/consent/services.py
git -C .../ai-bot-platform show origin/dev:apps/eventbus/ingest_security.py
git -C .../ai-bot-platform show origin/dev:apps/integrations/ayla/identity_client.py
git -C .../ai-bot-platform show origin/dev:apps/integrations/ayla/user_proxy.py
git -C .../ai-bot-platform show origin/dev:apps/catalog/services/upserter.py
git -C .../ai-bot-platform show origin/dev:tools/lint/consent_column_guard.py
git -C .../ai-bot-platform show origin/dev:config/settings/base.py
git -C .../ai-bot-platform grep -n "WebhookJournal" origin/dev -- 'apps/**/*.py'
git -C .../ai-bot-platform grep -n "processed_at" origin/dev -- 'apps/**/*.py'
git -C .../ai-bot-platform grep -in "allowlist|allowed_ips|ip_whitelist|REMOTE_ADDR" origin/dev -- 'apps/**/*.py' 'config/**/*.py'
git -C .../ai-bot-platform grep -in "rotat" origin/dev -- 'apps/**/*.py' 'config/**/*.py'
git -C .../ai-bot-platform grep -n "STRICT_TENANT_REFUSE|STRICT_TENANT_SCOPE" origin/dev -- 'config/**' 'apps/workers/**' 'apps/tenancy/**'
git -C .../ai-bot-platform grep -n "consent_required" origin/dev -- '*.py'
git -C .../ai-bot-platform grep -n "photo_biometric|PHOTO_BIOMETRIC" origin/dev -- '*.py'
git -C .../ai-bot-platform grep -n "ttl_purge|DELETION_REASON_TTL_PURGE" origin/dev -- '*.py'
git -C .../ai-bot-platform grep -n "resolve_or_create_bot_user|resolve_or_create_global_bot_user" origin/dev -- 'apps/**/*.py'
git -C .../djangoproject-catalog show origin/dev:users/permissions.py
git -C .../djangoproject-catalog show origin/dev:users/services.py
git -C .../djangoproject-catalog show origin/dev:users/internal_identity_api.py
git -C .../djangoproject-catalog show origin/dev:users/internal_users_api.py
git -C .../djangoproject-catalog show origin/dev:users/personal_context_erasure.py
git -C .../djangoproject-catalog show origin/dev:users/personal_data_api.py
git -C .../djangoproject-catalog show origin/dev:users/sms.py
git -C .../djangoproject-catalog show origin/dev:billing/internal_api.py
git -C .../djangoproject-catalog show origin/dev:users/models.py
git -C .../djangoproject-catalog show origin/dev:goals/models.py
git -C .../djangoproject-catalog show origin/dev:nutrition/models.py
git -C .../djangoproject-catalog show origin/dev:appointments/models.py
git -C .../djangoproject-catalog show origin/dev:tenants/appointments_api.py
git -C .../djangoproject-catalog grep -n "has_object_permission" origin/dev -- '*.py'
git -C .../djangoproject-catalog grep -n "permission_classes" origin/dev -- '*.py'
git -C .../djangoproject-catalog grep -nEi "Encrypted|encrypt\(|fernet|pgcrypto" origin/dev -- '*.py'
git -C .../djangoproject-catalog grep -n "class .*Consent" origin/dev -- '*/models.py'
git -C .../djangoproject-catalog grep -n "specialist.user_id" origin/dev -- users/recommendation_source.py
```

---

## 12. Убрано за собой

Веток, worktree и контейнеров **не создавалось** — вся работа шла через `git show` / `git grep`
по `origin/dev`. Рабочие деревья обоих репозиториев не изменялись и не читались; `git status
--porcelain` в обоих показывает только предсуществующие untracked-пути (`.claude/worktrees/*`,
`.claude/drf1462-shots/`, `AGENTS.md`), к которым замер не прикасался.

Снято за собой: три вспомогательных прохода создавали `mu.py`, `mv.py`, `/tmp/abp_hits1.txt`,
`/tmp/dpc_hits1.txt` и удалили их.

**Не снято, потому что не моё:** в скретчпаде сессии лежат `envelope.py`, `envelope2.py`,
`envelope3.py`, `pm_addition.md`, `pm_add13.md`, `pm_add14.md`, `pm_add15.md` (метки времени
19:42–19:50). Этим замером они не создавались — судя по именам, это черновики дополнений к
`docs/PILOT_MEASUREMENTS.md` от соседней работы. Чужие файлы не удаляю; фиксирую факт, чтобы
владелец увидел их и решил сам.

Единственный записанный артефакт замера — **этот файл**, `docs/MEASUREMENT_PILOT_IDENTITY_AUTH.md`.
