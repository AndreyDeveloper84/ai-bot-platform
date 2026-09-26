# DRF-2552 — обещания без исполнителей: перепись

**Деревья:** бот — `ai-bot-platform` `origin/dev` `0a0dd57f`; каталог — `beautygo_backend` `origin/dev` `d0aa7c98`.
**Предмет:** поля без писателя, значения перечислений без присваивания, обещания в прозе без исполнителя, объявленные и не подключённые механизмы (задачи, настройки). **Ничего не правилось**, ни один `help_text` не снят.

## Вердикты — строго два, плюс шум прибора

| Вердикт | Значит | Что с ним делать |
|---|---|---|
| **B — нерождённая работа** | объявлено на будущее; сегодня ничего не утверждает, что это работает | у него **срок рождения**: назван, что его родит |
| **C — обещание лжёт** | `help_text` / docstring / комментарий / документ / читатель утверждает, что работает **сейчас**, а исполнителя нет | **дефект**: либо родить исполнителя, либо снять обещание |
| A — ложное срабатывание прибора | на деле пишется / присваивается / подключено | в таблицу находок не идёт; считается для точности прибора |

## Охват и прибор

Скрипт: `docs/measurements/DRF-2552_census.py` (реестр моделей Django + один проход по тексту `git ls-files *.py` без тестов и миграций). Запуск из корня репозитория с `DJANGO_SETTINGS_MODULE`.

| | Бот | Каталог |
|---|---|---|
| файлов исходников | 965 | 498 |
| моделей / полей / значений перечислений | 71 / 738 / 266 | 82 / 812 / 325 |
| кандидатов: поля без писателя | 16 | 19 |
| кандидатов: значения без упоминания (строгий уровень) | 43 | 38 |
| кандидатов: значения без присваивания в строке (слабый уровень) | 164 | 251 |
| кандидатов: задачи без вызова/расписания | 1 | 3 |
| кандидатов: настройки без читателя | 13 | 25 |
| строк прозы с обещанием (по словарю) | 519 | 215 |

Все кандидаты строгих списков проверены по коду целиком; слабый уровень — выборкой по 15 в каждом репозитории; проза — отфильтрована до утверждений о текущем поведении и проверены 67 самых конкретных.

**Точность прибора (сколько кандидатов оказались настоящими, B или C):**

| Список | Бот | Каталог |
|---|---|---|
| поля без писателя | 13 / 16 | 12 / 19 |
| значения без упоминания | 26 / 43 | 9 / 38 |
| задачи | 0 / 1 | 0 / 3 |
| настройки | 2 / 13 | 5 / 25 |
| значения, слабый уровень (выборка) | 2 / 15 (≈13 %) | 1 / 15 (≈7 %) |

**Чего прибор не видит** (источники ложных срабатываний, названы проверкой): запись через форму `ModelAdmin` без `readonly`; `default=`; присваивание через переменную, кортеж, тело запроса или событие; `TextChoices` уровня модуля; неявное чтение настроек библиотеками (Celery `namespace`, `django-appconf`, corsheaders, sentry, spectacular, unfold); расписание beat по **имени задачи**, а не функции. Слабый уровень как сигнал почти бесполезен — в сторож не идёт.

**Три поломки прибора, пойманные до чтения чисел:** (1) первый прогон бота взял модели из основного клона через venv, а не из worktree — насчитал 0 моделей; (2) не видел членов вложенных `TextChoices` (`Model.Status.DONE`) — 74 и 115 ложных «неприсвоенных» вместо 43 и 38; (3) поиск по литералу засчитывал «присвоенным» значение, которое та же строка несёт в другом смысле (`"withdrawal"` — и причина удаления, и тип доступа) — отсюда слабый уровень.

## Дефекты поведения (C, но хуже обещания — код молча не делает обещанного)

Сообщены главному окну сразу, до таблицы.

| # | Где | Что обещано | Что на деле | Проверено |
|---|---|---|---|---|
| 1 | каталог `nutrition/services/cross_domain_engine.py:317–328` | буст ×1.5 правилу, если в категории есть избранный мастер человека | `FavoriteSpecialist.objects.filter(client=user)` — поля `client` нет (поля: `id, user, specialist, created_at`) → `FieldError` → проглочен `except Exception: pass`. **Буст не применяется никогда**; узлов на буст нет | живой вызов `_meta` и `filter(client=…)` |
| 2 | каталог `nutrition/management/commands/seed_cross_domain_rules.py:14, :76`; `nutrition/models.py:1102` | «Flip both flags via Django admin»; `requires_premium` «toggle in admin» | `CrossDomainRule` **не зарегистрирован в админке** (`nutrition/admin.py`: только Beverage, NutritionProfile, WaterEntry, NutritionOutboxEvent). На стенде shell/ORM запрещены — **правила включить нечем**. `requires_premium=True` прятал бы правило навсегда: движок проверяет `getattr(user, "is_premium", False)` (`cross_domain_engine.py:148`), а `is_premium` нет нигде | grep регистраций |
| 3 | бот `docs/adr/ADR-0006-field-level-encryption.md:17`, `.env.example:115` | «The encryption key lives in `settings.DJANGO_CRYPTOGRAPHY_KEY`, sourced from the environment / secret manager» | в `config/` слово `CRYPTOGRAPHY` не встречается; `settings` не имеет `DJANGO_CRYPTOGRAPHY_KEY`; библиотека читает `CRYPTOGRAPHY_KEY` и при его отсутствии делает `kdf.derive(KEY or settings.SECRET_KEY)` (`django_cryptography/conf.py:36–37`). **Ключ шифрования памяти = производное `SECRET_KEY`**: ротация `SECRET_KEY` сделает зашифрованную память нечитаемой; разделения секретов нет | живой `django.setup` + код библиотеки |
| 4 | каталог `djangoProject/settings/base.py:451` | `OTP_CODE_LENGTH = 6` | код генерирует `random.randint(1000, 9999)` — 4 цифры (`users/services.py:~1324`); настройку никто не читает | grep |

## C — обещание лжёт

### Бот

| Где обещано | Что обещано | Чего нет |
|---|---|---|
| `apps/identity/models.py:754–757` | `UserPersonalContext.summary` — «Ayla's running summary… Application-side capped at 8 KB… retrievable by the LLM» | непустое значение не пишет ни один путь (только `summary=None` в `forget_all_sweep.py:312`); предела 8 КБ нет. Промпт поле **читает** (`memory_reader.py:172` → `memory_surface.py:238`) — читатель есть, писателя нет |
| `apps/identity/models.py:1002` | `MemoryEntry.last_used_count` «Rolled up by the yellow-zone access count rollup job» | задачи нет; «rollup» — только в `help_text` и миграции |
| `apps/conversations/models.py:291` (+ `:307`, `:315`, события, права ролей, уведомление «Срочно (HUMAN_LOCKED) … нельзя выключить») | `Tier.HUMAN_LOCKED` «disables master compose + AI auto-reply» | значение никто не присваивает; веток, проверяющих его, нет. Вокруг — целая поверхность (права `promote_human_locked`, событие `conversation.tier_promoted_to_human_locked`, уведомление с CheckConstraint) |
| `apps/conversations/models.py:327–328` | `last_read_by_master_at` «Used to compute the per-master unread count» | подсчёта нет |
| `apps/conversations/models.py:760–764` | `AiDraft.trigger_message` «Used for the 60s idempotency window» | слой снят DRF-1528 (`models.py:712–713`), `help_text` остался |
| `apps/handoff/models.py:129` | `MEDICAL_RED_FLAG` «ships URGENT» | нигде не создаётся; `safety/gate.py:28` прямо: AdminTask при red flag не заводится |
| `apps/identity/models.py:146, :155` | `BotUser.avatar_url` «Used by bot-platform UI surfaces (mini app, conversation thread, internal-chat)» | только запись, стирание, экспорт; поверхности читают `avatar_url` каталога |
| `apps/identity/models.py:277` | `consent_at` «Used by returning-user query… → soft re-welcome» | возвращение считается по `welcomed_at` (`welcome/skill.py:1464`); запроса нет |
| `apps/identity/models.py:298` | `food_scanner_consent_at` «used by the food_scanner skill + miniapp_api to enforce consent on every turn» | колонку никто не читает — согласие ушло в реестр (DRF-1963); в коде это признано (`consent/nutrition.py:13`), в `help_text` — нет |
| `apps/conversations/models.py:211` | `last_booking_at` «Used by AI grounding» | читает только метрика (`observability/ai_metrics.py`) |
| `apps/conversations/models.py:201`, `:383` | «inactivity-cleanup queries», «+ the inactivity cleanup sweep» | свипа неактивности нет |
| `apps/catalog/models.py:79` | `external_updated_at` «Drives the `?since=` cursor (C2) and last-writer-wins» | `catalog/services/sync.py:38–44`: «no longer drives the fetch»; LWW нет |
| `docs/runbooks/strict-tenant-refuse-flip.md:95, :339–341` | оператор выставляет `STRICT_TENANT_REFUSE_FLIP_AT`, «post-flip monitor windows» | монитора нет — сам `base.py:298–299` признаёт «NOT shipped» |
| `apps/observability/delta.py:5` (слабая) | «consumed by … the strict-scope flip gate (F1)» | программного гейта нет, только раскраска дашборда |
| `apps/conversations/models.py:71–72` (слабая) | правило «добавлять значения вместе с писателем» | `Conversation.state=consulting` объявлен без писателя |

### Каталог

| Где обещано | Что обещано | Чего нет |
|---|---|---|
| `appointments/models.py:852–854, :940–941` | «New publisher writes to `local_processed_at` AND `processed_at` together» | публикатор `local_processed_at` не пишет; диспетчер пишет только `processed_at` |
| `nutrition/models.py:1002–1003` | 8 витаминных полей `Beverage` (D, B12, C, железо, кальций, магний, омега-3, клетчатка) — «juices/milk/broth populate them from USDA + Скурихин» | сид (`data/beverages_seed.py`) их не несёт, админка их не показывает, никто не читает |
| `nutrition/models.py:345–346` | `NutritionOutboxEvent.topic=recognition_completed` — «Domain code writes rows (… scan recognise …)» | у `enqueue_recognition_completed` (`outbox_service.py:118`) нет вызывающих; у `enqueue_pattern_detected` — тоже |
| `ai/models.py:94` | `Conversation.mark_deleted` «Used by the «Очистить историю Ayla» 152-ФЗ workflow» | вызовов нет; workflow переписан инлайном (`ai/views.py:352`) |
| `services/models.py:178` | `is_popular` «Показывать в верхней части списка категории» | публичные списки по нему не сортируют; порядок — только админка и сиды |
| `users/migrations/0009…:26` | `favorite_masters` «Cross-domain engine boosts these» | движок это поле не читает (и свой буст по `FavoriteSpecialist` не применяет — дефект №1) |
| `CLAUDE.md:62`, `docs/DEV_REFERENCE.md:290` | «Split-платёж если `YOOKASSA_AGENT_ID` задан» | `settings/base.py:666–669`: «payments/services.py no longer consults it». Код честен, **лгут документы** |
| `djangoProject/settings/base.py:1267` | ссылается на `LLM_INSIGHT_USER_CAP` | такой настройки нет; настоящий предел — `BEAUTY_INSIGHT_USER_CAP` (исполнитель есть, имя в тексте неверное) |

## B — нерождённая работа (сроки рождения)

**Бот (главное):** `MemoryEntry.kind` symptom/relationship/financial, `deletion_reason` ttl_purge/withdrawal/minor_protection, `status=deletion_pending`, `supersession_reason` consolidated/policy_migration, `provenance=user_confirmed_inference`, `source_event_id`, `derivation_method`, `purpose_tags` — **родит их снятие заглушки #597 и proposal flow (Step 4+)**; это те же условия, что собраны в DRF-2542. `Conversation.tier=human_supervised`, `tier_locked_reason_text`. Статусы `AiDraft` (работа **снята** DRF-1528, раскрыто в docstring). `BookingRequest.booking_source` ai_assisted/test_admin (контракт атрибуции). `LoyaltyEvent` earn_birthday/earn_review/manual_adjust («when birthday bonus ships»). `ImplicitFeedbackSignal.completed_booking_reused` («reserved»). `IngestDLQ.replayed_at` («replay tooling… follow-up»). `ScheduleChangeRequest.requested_change` (устаревшее, читатели считают его отсутствующим). `RedZoneAccessLog.access_type=purge`, `accessor_role=ayla_llm`. `ASGI_APPLICATION` (родит daphne/channels). **Вложения внутреннего чата** — `MasterAdminMessageAttachment` целиком (поля, типы pdf/voice_memo, `pii_scan_status=flagged`): «upload endpoint + PII scan pipeline are deferred» — это и есть пятая находка из DRF-2535.

**Каталог (главное):** `OutboxEvent.external_target`, `bot_delivery_status=acknowledged` (пограничный: `models.py:920–923` описывает переход в настоящем времени, `publisher.py:26–27` — «out of scope for C2»), `Payment.capture_state=settled`, `ai.Message.role=tool`, `FoodScan.provider_used=vit-self-host` (Phase 6), `CrossDomainShownRule.surface=mobile` (Phase 5+), `user_action=explained`, `Tenant.geocode_status=ambiguous`, `DesiredOutcome.direction=increase` (закрыто гейтом D), `RecommendationSet.execution_mode=LIVE` (400 до доказанных порогов — честно), `CrossDomainRule.legal_review_date`, `AI_HISTORY_WINDOW`, `VK_CLIENT_SECRET` / `YANDEX_CLIENT_ID` (провайдеры закрыты, W0-D2).

## Сводка

| | Бот | Каталог |
|---|---|---|
| **дефекты поведения** | 1 (ключ шифрования) | 3 (буст, правила без админки, OTP) |
| **C — обещание лжёт** | 15 (2 слабые) | 8 |
| **B — нерождённая работа** | ≈ 40 | ≈ 17 |

Проза: 519 + 215 строк → ≈ 191 утверждение о текущем поведении → проверено 67 → A 55 / B 4 / C 13. **≈ 124 утверждения не проверены** — в основном «Used by X» в docstring модулей (`replay`, `workers`, `permissions`, `master_api/auth`) и текст про экраны, которые рендерит фронт; их вердикта в этой таблице нет, и отсутствие находок среди них — не ноль.

## Сторож на этот класс впредь — предложение

Строить только на **строгом** уровне прибора (слабый — 7–13 % точности, в сторож не годится).

1. **Перечисления:** узел в каждом репозитории обходит реестр моделей; для каждого значения `choices` требует хотя бы одно упоминание вне объявления (строкой или константой, включая члены вложенных `TextChoices`). Значение без упоминания допустимо **только** из файла-списка `KNOWN_UNBORN_CHOICES` с двумя полями: причина и **что его родит** (лист). Новое значение без писателя и без строки в списке → красное с именем `Model.field=value`. Подмена в узле: подброшенное значение во временной модели краснеет.
2. **Поля:** то же для полей — ни одной записи (`name=`, `.name =`, `"name":`) вне объявления и миграций, и нет в `KNOWN_UNBORN_FIELDS` с причиной → красное. **Писатели через форму `ModelAdmin` учитывать**: поле в редактируемой форме — писатель (иначе ложная краснота, см. Experiment/Promotion).
3. **Проза не сторожится автоматически** — словарь даёт 519 строк, из них ≈ ⅓ утверждений. Вместо сторожа — правило для новых `help_text`: утверждение о потребителе пишется **с именем символа** («Used by `apps.x.y.func`»), и узел проверяет, что символ существует. Это ловит самый частый вид лжи здесь — потребитель удалён, текст остался (`trigger_message`, `mark_deleted`, `external_updated_at`).
4. **Списки `KNOWN_UNBORN_*` — это и есть реестр нерождённой работы**: сегодня он собран этой таблицей; сторож делает его обязательным и видимым, как `KNOWN_UNDECLARED` у соседних сторожей.

## Пределы

- Текст исходников, не стенд: что реально лежит в базе (например, заполнен ли `summary` старым путём), не мерилось.
- Проза: ≈ 124 утверждения без вердикта (см. выше).
- `ai.Message.role=tool` проверялось по локальной копии `ayla-ai-core` `87c461c`, закреплён `d72a5de`.
- Ничего не правилось. Каждое C — дефект для отдельного листа; дефекты поведения 1–4 названы главному окну отдельно.
