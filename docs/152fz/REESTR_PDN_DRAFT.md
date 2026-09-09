# Реестр персональных данных проекта Ayla — ЧЕРНОВИК для юриста (152-ФЗ)

**Статус:** черновик, составлен по результатам read-only аудита исходного кода.
**Дата аудита:** 2026-08-12.
**Назначение:** входной документ для юридической консультации по 152-ФЗ. Документ фиксирует ФАКТЫ из кода. Юридических выводов о законности не содержит — их даёт юрист.

## Как читать этот документ

Каждое утверждение помечено классом доказательности:

| Метка | Значение |
|---|---|
| **VERIFIED** | Наблюдалось непосредственно в коде, указан якорь `файл:строка` |
| **INFERRED** | Логически следует из прочитанного кода, но само поведение не наблюдалось (не запускалось, не проверялось на проде) |
| **UNKNOWN** | Установить по доступным материалам не удалось |

**Обследованные репозитории:**

| Код | Путь | Что это |
|---|---|---|
| `bot:` | `C:\Users\user\PycharmProjects\ai-bot-platform` | Бот-платформа (Django): каналы, диалоги, скиллы, LLM-роутер |
| `ayla:` | `C:\Users\user\PycharmProjects\Ayla\djangoproject` | Backend Ayla (Django): канонический пользователь, записи, платежи, питание |

Ветка бота на момент аудита: `fix/drf980-1007-pilot-operations`, HEAD `28e0321` (2026-08-12) — **VERIFIED**.

**Ограничения аудита:** реальные значения ПДн в документ не переносились; секреты не выписывались. Продакшн-база и продакшн-логи не обследовались — только исходный код и конфигурация.

---

## 0. ГЛАВНЫЙ ВЫВОД (для быстрого чтения)

**Механизм псевдонимизации ПДн перед отправкой в зарубежный LLM в коде реализован, но на основном пользовательском маршруте пилота (глобальный консьерж в мессенджере MAX) он НЕ АКТИВИРУЕТСЯ — текст диалога уходит в OpenAI в исходном виде, без маскирования.** Подробное доказательство — раздел 3.1. Это ключевой факт для юридической оценки трансграничной передачи.

---

## 1. Категории субъектов персональных данных

| № | Категория субъекта | Где видно в коде | Класс |
|---|---|---|---|
| 1 | **Клиенты салона / конечные пользователи бота** — физические лица, пишущие боту в MAX (и потенциально Telegram), пользователи Mini App | `bot:apps/identity/models.py:47` — модель `BotUser` (идентичность пользователя канала); `bot:apps/identity/models.py:110` — поле `channel` со слагами `max`/`telegram`/`whatsapp`/`web` | **VERIFIED** |
| 2 | **Клиенты как канонические пользователи Ayla** — та же физическая личность, но в бекенде Ayla, куда бот проксирует записи, платежи и дневник питания | `bot:apps/identity/models.py:79` — `BotUser.ayla_user_id` (мост на канонического User в Ayla); `bot:apps/integrations/ayla/booking_client.py:524` — заголовок `X-External-User-ID` | **VERIFIED** |
| 3 | **Мастера (специалисты салонов)** — исполнители услуг; их данные зеркалируются в бота из каталога | `bot:apps/catalog/models.py:203` — `CatalogMaster`; поля `name` (`:225`), `specialization` (`:226`), `bio` (`:227`), `experience` (`:228`), `photo_url` (`:291`), `ayla_user_id` (`:248`), `raw` — сырой снимок из источника (`:266`) | **VERIFIED** |
| 4 | **Мастера как пользователи внутреннего чата и мастер-кабинета** — переписываются с администратором, получают AI-черновики ответов | `bot:apps/internal_chat/models.py:379` — `MasterAdminMessage`; `:410` `sender_user`; `:418` `sender_admin_signed_name`; `bot:apps/conversations/models.py:487` — `AiDraft`, `:557` поле `master` | **VERIFIED** |
| 5 | **Сотрудники салона / персонал арендатора (tenant staff)** — администраторы, привязанные к салону | `bot:apps/tenancy/models.py:353` — `TenantStaff`; `:397` `bot_user`; `:409` `role`; `:417` `created_by` | **VERIFIED** |
| 6 | **Сотрудники — субъекты кадрового/графикового учёта** — запросы на изменение расписания с текстовыми причинами | `bot:apps/scheduling/models.py:425` — `ScheduleChangeRequest`; `:521` `reason_text`; `:540` `resolution_note`; `:574` FK на `BotUser` | **VERIFIED** |
| 7 | **Операторы платформы / администраторы** — лица, чьи действия фиксируются в аудите и в журнале доступа к чувствительным данным | `bot:apps/audit/models.py:83` — `AuditLog.actor_id`; `bot:apps/identity/models.py:901` — `RedZoneAccessLog.accessor_principal`; `:894` `accessor_role` | **VERIFIED** |
| 8 | **Несовершеннолетние (<18)** — выделены как отдельный режим обработки | `bot:apps/identity/models.py:600` — `UserPersonalContext.minor_lock`; `bot:apps/identity/services/memory_writer.py:56` — `_check_minor_protection()`; `bot:apps/identity/models.py:679` — `DELETION_REASON_MINOR_PROTECTION`; анкета питания принимает возраст от 14 лет — `bot:apps/skills/nutrition_anketa/fsm.py:53` | **VERIFIED** |

**Замечание для юриста.** Категория 8 существенна: код допускает диалог с лицами от 14 лет (анкета питания), при этом определение возраста опирается на данные Ayla — `bot:apps/identity/models.py:600` и ADR `bot:docs/adr/ADR-0011-user-personal-context-privacy.md:299` («Minor age determination», §10.1–10.2). Требует оценки: достаточность механизма получения согласия законного представителя.

---

## 2. Состав собираемых данных

### 2.1 Сводная таблица

| Категория данных | Конкретные поля / модели | Где хранится | Якорь | Класс |
|---|---|---|---|---|
| **Идентификаторы каналов** | `BotUser.channel_user_id` (внешний user_id канала), `BotUser.chat_id` (адрес доставки), `BotUser.channel` | PostgreSQL, таблица `identity_botuser` | `bot:apps/identity/models.py:114`, `:128`, `:110` | **VERIFIED** |
| **Идентификатор в Ayla** | `BotUser.ayla_user_id` (UUID канонического пользователя) | PostgreSQL | `bot:apps/identity/models.py:79` | **VERIFIED** |
| **Телефон** | `BotUser.phone` (E.164, `db_index=True`), хранится **в открытом виде** | PostgreSQL | `bot:apps/identity/models.py:120` | **VERIFIED** |
| **Имя** | `BotUser.display_name` (имя из канала), `BotUser.client_name` (имя, введённое клиентом) | PostgreSQL, открытый вид | `bot:apps/identity/models.py:136`, `:142` | **VERIFIED** |
| **Аватар** | `BotUser.avatar_url` | PostgreSQL | `bot:apps/identity/models.py:98` | **VERIFIED** |
| **Email** | Отдельного поля email в моделях бота **нет**. Email фигурирует только транзитно как `buyer_email` для чека при оплате сертификата | не хранится в боте | `bot:apps/skills/booking/tools.py:3104`, `:3120`; `bot:apps/integrations/ayla_payments/client.py:288-294` | **VERIFIED** |
| **Дата рождения** | `UserPreferences.birthday_date` | PostgreSQL, открытый вид | `bot:apps/identity/models.py:328` | **VERIFIED** |
| **Часовой пояс, произвольный контекст** | `BotUser.timezone`, `BotUser.context` (JSON) | PostgreSQL | `bot:apps/identity/models.py:226`, `:220` | **VERIFIED** |
| **Содержимое диалогов** | `Message.content` (тело сообщения, в т.ч. пользовательского), `Message.rendered_text` (что реально увидел пользователь) — **plaintext, без шифрования** | PostgreSQL, таблица сообщений | `bot:apps/conversations/models.py:425`, `:432` | **VERIFIED** |
| **Метаданные диалога** | `Conversation.state`, `outcome`, `skill_state` (JSON — незавершённые шаги анкет), `tier_locked_reason_text` (свободный текст, 500 симв.) | PostgreSQL | `bot:apps/conversations/models.py:107`, `:114`, `:161`, `:287` | **VERIFIED** |
| **Сырые tool-вызовы LLM** | `Message.tool_call` (JSON, «for forensic audit»), `Message.action_data` | PostgreSQL | `bot:apps/conversations/models.py:450`, `:445` | **VERIFIED** |
| **Черновики AI-ответов** | `AiDraft.content` — текст, сгенерированный LLM для отправки клиенту | PostgreSQL | `bot:apps/conversations/models.py:563` | **VERIFIED** |
| **Данные записи (booking)** | `BookingRequest.client_name`, `client_phone`, `comment` (свободный текст клиента), `category_name`, `service_name`, `master_name`, `visit_at` | PostgreSQL, открытый вид | `bot:apps/booking/models.py:142`, `:143`, `:145`, `:139-141`, `:290` | **VERIFIED** |
| **Отзывы** | `BookingRequest.rating`, `feedback_comment` | PostgreSQL | `bot:apps/booking/models.py:359`, `:365` | **VERIFIED** |
| **Напоминания** | `BookingReminder.chat_id` (снимок), `master_name`, `service_name` | PostgreSQL | `bot:apps/booking/models.py:557`, `:588`, `:589` | **VERIFIED** |
| **Поведенческий / финансовый профиль клиента** | `ClientProfile`: RFM-сегмент, LTV, прогноз LTV, риск оттока, стадия жизненного цикла, любимая услуга/мастер, уровень лояльности, оценка тональности | PostgreSQL | `bot:apps/identity/models.py:359`, поля `:397`–`:490` | **VERIFIED** |
| **Кросс-канальная AI-память (сводка о человеке)** | `UserPersonalContext.summary` — свободный текст «кто этот пользователь», **не шифруется** (явно указано в help_text) | PostgreSQL | `bot:apps/identity/models.py:584`, help_text `:588-590` | **VERIFIED** |
| **Отдельные факты о человеке (зонированные)** | `MemoryEntry` с зоной green/yellow/red; `content` — **единственное поле в репозитории бота, зашифрованное at-rest** (Fernet) | PostgreSQL (зашифровано) | `bot:apps/identity/models.py:622`, `:705` (`sensitivity_zone`), `:740` (`content = encrypt(...)`), импорт `:42` | **VERIFIED** |
| **Лояльность и реферальные связи** | `LoyaltyAccount.balance`, `LoyaltyReferral` (граф «кто кого привёл») | PostgreSQL | `bot:apps/loyalty/models.py:50`, `:73`, `:268`, `:315`, `:323` | **VERIFIED** |
| **Эскалация к человеку** | `AdminTask.transcript_snapshot` (JSON) — **замороженная копия переписки + профиля клиента** на момент эскалации; `reason`, `resolution_note` | PostgreSQL | `bot:apps/handoff/models.py:46`, `:108`, `:132`, `:137` | **VERIFIED** |
| **Внутренний чат мастер↔админ** | `MasterAdminMessage.body` (4000 симв., plaintext; в help_text прямо сказано, что фильтр клиентских ПДн ещё не реализован); вложения-файлы | PostgreSQL + файловое хранилище | `bot:apps/internal_chat/models.py:426`, `:471`, `:513`, `:534` | **VERIFIED** |
| **Данные сотрудников (мастеров)** | `CatalogMaster` — ФИО, специализация, био, стаж, фото | PostgreSQL | `bot:apps/catalog/models.py:225-228`, `:291` | **VERIFIED** |

### 2.2 Данные о состоянии здоровья — СПЕЦИАЛЬНАЯ КАТЕГОРИЯ (ст. 10 152-ФЗ)

Это ключевой блок. Данные о здоровье в системе собираются как минимум **шестью** разными путями, и защищены они **неоднородно**.

#### 2.2.1 Зонированная память с явным признанием спецкатегории

| Факт | Якорь | Класс |
|---|---|---|
| Модель `MemoryEntry` в докстроке прямо называет red-зону «152-ФЗ §10 special category — health» | `bot:apps/identity/models.py:622`, зоны `:647-654` | **VERIFIED** |
| Виды фактов включают `contraindication` (противопоказание), `symptom` (симптом), `lifestyle` | `bot:apps/identity/models.py:665-673` | **VERIFIED** |
| Содержимое факта шифруется (Fernet), для red-зоны дополнительно хеш-перец | `bot:apps/identity/models.py:740`; ADR `bot:docs/adr/ADR-0011-user-personal-context-privacy.md:163` (§6) | **VERIFIED** |
| Для yellow/red запись без `consent_at` запрещена | `bot:apps/identity/services/memory_writer.py:147`; поле `bot:apps/identity/models.py:766` | **VERIFIED** |
| Каждое чтение red-зоны пишет строку в `RedZoneAccessLog` (INSERT-only, срок 7 лет), обязателен параметр `purpose` | `bot:apps/identity/models.py:841`, `:925`; `bot:apps/identity/services/red_zone_reader.py:73`, `:84` | **VERIFIED** |
| На уровне БД действуют RLS/триггеры для red-зоны | `bot:apps/identity/migrations/0008_red_zone_db_security.py` | **VERIFIED** |
| Срок хранения red = 90 дней от последнего использования, yellow = 365 дней | ADR `bot:docs/adr/ADR-0011-user-personal-context-privacy.md:150-151` | **VERIFIED (как политика)**; фактическая реализация sweep-задачи — см. раздел 4 |

#### 2.2.2 Свободный текст про аллергии/противопоказания — БЕЗ шифрования

| Факт | Якорь | Класс |
|---|---|---|
| `UserPreferences.allergies` — обычный `TextField`, свободный текст противопоказаний/аллергий; help_text: показывается мастеру перед каждой записью | `bot:apps/identity/models.py:334-339` | **VERIFIED** |
| Поле **не шифруется** (в отличие от `MemoryEntry.content`) — асимметрия защиты медданных между двумя хранилищами | `bot:apps/identity/models.py:334` vs `:740` | **VERIFIED** |
| Пользователь вводит аллергии сам в Mini App | `bot:apps/miniapp/src/screens/ProfileScreen.tsx:196-201`; API — `bot:apps/miniapp_api/views.py:1545-1578` | **VERIFIED** |
| Значение попадает в снимок профиля и обрезается до 2000 символов при записи | `bot:apps/identity/services/profile.py:103-104`, `:163-166` | **VERIFIED** |
| Код сам квалифицирует это поле как медданные: «`allergies` is free-text health data» | `bot:apps/identity/services/privacy.py:278` | **VERIFIED** |

#### 2.2.3 Диалоговый опрос о боли и симптомах (health screening)

| Факт | Якорь | Класс |
|---|---|---|
| Скилл `HealthScreeningSkill` классифицирует текст пользователя на «красные флаги» и «мягкую боль» | `bot:apps/skills/health_screening/skill.py:73`, `:79-83`; классификатор `bot:apps/skills/health_screening/classifier.py` | **VERIFIED** |
| Бот **задаёт уточняющие вопросы о симптомах** («Где именно болит…») | `bot:apps/skills/health_screening/skill.py:57-61` (`SOFT_PAIN_REPLY`) | **VERIFIED** |
| Для красных флагов — редирект к врачу | `bot:apps/skills/health_screening/skill.py:65-69` | **VERIFIED** |
| Скилл зарегистрирован в рабочем порядке скиллов канала MAX | `bot:apps/channels/max/handler.py:377` | **VERIFIED** |
| В лог пишется только id разговора, без текста симптомов | `bot:apps/skills/health_screening/skill.py:86-89` | **VERIFIED** |
| **Однако** сам текст пользователя про боль/симптомы сохраняется в `Message.content` в открытом виде, вне red-зонного контура | `bot:apps/conversations/models.py:425` | **VERIFIED** |

#### 2.2.4 Факт выбора медицинской/косметологической услуги

| Факт | Якорь | Класс |
|---|---|---|
| У услуги есть флаг `requires_health_check` («требует медконсультации») и поле `contraindications` | `bot:apps/catalog/models.py:120`, `:121` | **VERIFIED** |
| При подтверждении записи на такую услугу срабатывает гейт → перевод на человека | `bot:apps/skills/booking/skill.py:720-737`, проверка `:1063-1126` | **VERIFIED** |
| Гейт можно отключить флагом (есть событие `booking.health_check_gate_disabled`) | `bot:apps/skills/booking/skill.py:213` | **VERIFIED** |
| Противопоказания услуги и метка «Требует медконсультации» **проецируются в базу знаний** → попадают в эмбеддинги и в контекст LLM | `bot:apps/kb/projectors.py:92-95` | **VERIFIED** |
| Противопоказания отдаются в API Mini App и показываются пользователю | `bot:apps/miniapp_api/views.py:558`; `bot:apps/miniapp/src/screens/ServiceDetailScreen.tsx:121-124` | **VERIFIED** |
| Категории услуг включают «Уход за лицом», «Массаж», «Эпиляция» | `bot:apps/master_api/services/catalog.py:72-75` | **VERIFIED** |
| Название выбранной услуги и категории сохраняется в записи **в открытом виде** — то есть факт «этот человек записался на такую-то процедуру» хранится нешифрованным | `bot:apps/booking/models.py:139-140` | **VERIFIED** |
| Отдельной категории «медицина» в каталоге нет; признаком медицинского характера служит `requires_health_check` + `contraindications` | `bot:apps/catalog/models.py:120-121` | **INFERRED** |
| Классификация обращения включает значение `medical` | `bot:apps/conversations/models.py:265` (`tier_reason_class`), значения `:271` | **VERIFIED** |

#### 2.2.5 Анкета питания — антропометрия и цели (квази-медицинские данные)

| Факт | Якорь | Класс |
|---|---|---|
| FSM-анкета собирает: **пол → возраст → рост → вес → цель** | `bot:apps/skills/nutrition_anketa/fsm.py:44-73`; пол `:45-52`, возраст `:53-57` (диапазон 14–90), рост `:58-62`, вес `:63-67`, цель `:68-72` | **VERIFIED** |
| Шаги про аллергии и лекарства в анкете **пока не реализованы** (помечены как Phase 1) | `bot:apps/skills/nutrition_anketa/fsm.py:12` | **VERIFIED** |
| Промежуточное состояние анкеты хранится в JSON на разговоре | `bot:apps/conversations/models.py:150-154` (`skill_states`) | **VERIFIED** |
| Ответы уходят в Ayla через `upsert_profile`; контракт включает `age`, `height_cm`, `weight_kg`, `goal`, `pace`, **`health_flags`** | `bot:apps/integrations/ayla/nutrition_client.py:626-650`, парсер `:680-698` | **VERIFIED** |

#### 2.2.6 Фото еды / дневник питания

| Факт | Якорь | Класс |
|---|---|---|
| Скилл `FoodScannerSkill` извлекает байты фото и отправляет их во внешний сервис Ayla | `bot:apps/skills/food_scanner/skill.py:111`, `:152`, `:163-170` | **VERIFIED** |
| Байты фото в БД бота **не сохраняются** — только в памяти запроса (`conversation.last_photo_bytes`), затем сбрасываются | `bot:apps/skills/food_scanner/skill.py:284-292`; `bot:apps/channels/max/handler.py:937`, сброс `:946`, `:954` | **VERIFIED** |
| Фото скачивается с CDN мессенджера с ограничением 10 МиБ и SSRF-защитой | `bot:apps/channels/max/photo.py:248`, `:65`, `:203-210` | **VERIFIED** |
| Три последовательных гейта: `NUTRITION_ENABLED` → `FOOD_PHOTO_SCAN_ENABLED` (трансграничный гейт) → `BotUser.food_scanner_consent_at` | `bot:apps/skills/food_scanner/skill.py:295-365`, проверка согласия `:353`; поле `bot:apps/identity/models.py:196` | **VERIFIED** |
| Дневник блюд и воды ведётся во внешнем сервисе Ayla | `bot:apps/integrations/ayla/nutrition_client.py:406` (`log_meal`), `:718` (`add_water`) | **VERIFIED** |
| Тип согласия `PHOTO_BIOMETRIC` в комментарии кода прямо описан как спецкатегория по 152-ФЗ и покрывает фото еды и фото «до/после» для косметологии | `bot:apps/consent/models.py:69`, докстрока `:32-36` | **VERIFIED** |

### 2.3 Платёжные данные

| Факт | Якорь | Класс |
|---|---|---|
| Отдельного приложения `payments/` в боте **нет**. Платежи владеет бекенд Ayla | `bot:apps/integrations/ayla_payments/client.py:1-40` (архитектурное решение по ADR-0009) | **VERIFIED** |
| В боте хранится только зеркало платежа: `PaymentMirror` с полями `payment_id` (UUID), `capture_state`, `amount`, `appointment_id` | `bot:apps/booking/models.py:914`, `:945`, `:949`, `:955`, `:960` | **VERIFIED** |
| Номера карт в боте **не хранятся**. Событийная шина отклоняет события с ключами `credit_card`, `card_number`, `cvv`, `phone`, `email`, `raw_text`, `client_name` | `bot:apps/eventbus/validation.py:32-45`, регексы значений `:55-56` | **VERIFIED** |
| Коды ошибок YooKassa маппятся в enum именно чтобы свободный текст ошибки (потенциально с хвостом карты) не попал в БД | `bot:apps/conversations/models.py:202`, help_text `:210-213`; `bot:apps/eventbus/consumers/payment.py:74-113` | **VERIFIED** |
| Прямая интеграция с ЮKassa из бота **выведена из эксплуатации**: старый вебхук отвечает `410 Gone`, тело не сохраняется | `bot:apps/integrations/yookassa_retired/views.py:1-20` | **VERIFIED** |
| В Ayla при создании платежа уходят: сумма, описание, вид, **`recipient_name`** (имя получателя сертификата) и **`buyer_email`** (email плательщика для чека по 54-ФЗ) | `bot:apps/integrations/ayla_payments/client.py:288-294` | **VERIFIED** |
| Клиент Ayla умеет читать `last4`/`brand` карты, но это встречено только в тестовой фикстуре; запись в БД бота не наблюдалась | `bot:apps/integrations/ayla/tests/test_payments_client.py:138-144` | **VERIFIED (как факт наличия кода)** / **INFERRED (что в БД не пишется)** |

### 2.4 Технические логи, аудит и сырые payload'ы

#### 2.4.1 `WebhookJournal.raw_payload` — сырой payload вебхука

Это отдельный и, вероятно, самый крупный «теневой» массив ПДн.

| Факт | Якорь | Класс |
|---|---|---|
| Модель `WebhookJournal` — одна строка на каждый входящий вебхук | `bot:apps/ingress/models.py:25` | **VERIFIED** |
| Поле `raw_payload` (JSON) — «Full untouched webhook payload», то есть **необработанный payload целиком** | `bot:apps/ingress/models.py:37` | **VERIFIED** |
| Фактическое содержимое для канала MAX: `message.sender.user_id`, `message.sender.name` (**имя человека**), `message.recipient.chat_id`, `message.body.text` (**полный текст сообщения пользователя**), `attachments`, `callback.user` | схема — `bot:apps/channels/max/parser.py:14-21`, `:227-238` | **VERIFIED** |
| Через кнопку «поделиться контактом» в payload может прийти **телефон** | `bot:apps/channels/max/outbound.py:315` (`request_contact`) | **VERIFIED** |
| Запись идёт без редакции/маскирования | `bot:apps/ingress/views.py:109` → `bot:apps/ingress/services.py:152` | **VERIFIED** |
| В модели **нет** полей TTL/срока хранения (`expires_at`, `retention` и т.п.) | `bot:apps/ingress/models.py:25-73` — полей нет | **VERIFIED** |
| `raw_payload` выведен в Django-админку | `bot:apps/ingress/admin.py:31` | **VERIFIED** |

#### 2.4.2 Аудит

| Факт | Якорь | Класс |
|---|---|---|
| `AuditLog` содержит `actor_id`, `action`, `target`, `target_id`, `payload` (JSON) | `bot:apps/audit/models.py:56`, `:83`, `:88`, `:92`, `:98`, `:99` | **VERIFIED** |
| Правило «Never store raw PII; store IDs and hashes» — это **help_text**, не ограничение БД и не валидация | `bot:apps/audit/models.py:102` | **VERIFIED** |
| Срок хранения аудита — `AUDIT_LOG_RETENTION_DAYS`, по умолчанию 90 дней | `bot:apps/audit/models.py:1-19` (докстрока) | **VERIFIED** |

#### 2.4.3 ПДн в логах приложения

| Факт | Якорь | Класс |
|---|---|---|
| Фильтр `PIIRedactingFilter` подключён к логированию в настройках | `bot:config/settings/base.py:1484` | **VERIFIED** |
| Он вырезает **российские телефоны, email и номера карт** (карты — с проверкой по Луну) | `bot:apps/observability/pii_filter.py:34-44` | **VERIFIED** |
| **Имена он НЕ редактирует** — сознательное решение, NER отложен | `bot:apps/observability/pii_filter.py:46-50` | **VERIFIED** |
| Идентификаторы (`*_id`, `*_uuid`) не редактируются намеренно | `bot:apps/observability/pii_filter.py:51-53` | **VERIFIED** |
| Сырой текст пользователя попадает в лог: `logger.info("bookings.callback.malformed text=%r", text)` | `bot:apps/bookings/callbacks.py:170` | **VERIFIED** |
| То же во втором месте | `bot:apps/bookings/callbacks.py:503` | **VERIFIED** |
| В лог пишется `chat_id` + тело ответа мессенджера (может содержать эхо текста) | `bot:apps/channels/max/outbound.py:126-130` | **VERIFIED** |
| Тело ответа Telegram при ошибке алертинга | `bot:apps/observability/alerting.py:203-206`; `bot:apps/observability/tasks.py:324-327` | **VERIFIED** |
| Сырой payload из Redis pub/sub при ошибке разбора | `bot:apps/promptreg/cache.py:283` | **VERIFIED** |
| Факт нутри-активности пользователя логируется с его внешним id | `bot:apps/skills/food_scanner/skill.py:177`, `:183`, `:257`, `:263` | **VERIFIED** |
| Хороший паттерн (для контраста): каналы логируют `channel_user_id` + **длину** текста, не сам текст | `bot:apps/channels/max/handler.py:464-467`, `:593-597`; `bot:apps/channels/telegram/handler.py:124-128` | **VERIFIED** |
| Телефон отображается и доступен для поиска в Django-админке **без маскирования** | `bot:apps/identity/admin.py:140` (`search_fields`), `:153` | **VERIFIED** |
| Для сравнения: в мастер-API телефон маскируется на сервере | `bot:apps/master_api/services/customers.py:82` (`_mask_phone`), `:268`, обоснование `:27-29` | **VERIFIED** |

#### 2.4.4 Прочие журналы с пользовательским содержимым

| Факт | Якорь | Класс |
|---|---|---|
| `ReplayTrace.pipeline_steps` — снимки шагов пайплайна, включая входящее сообщение; заявлена редакция и жёсткий TTL | `bot:apps/replay/models.py:44`, `:68`, `:78`, `:83`, `:95` | **VERIFIED** |
| Редактор replay работает только по регексам (телефон/email/карта/OTP/URL-токен); русский NER отложен | `bot:apps/replay/redactor.py:1-13` | **VERIFIED** |
| События шины: `DomainEvent.data`/`metadata` с правилом «NEVER raw PII» и отклонением на emit | `bot:apps/eventbus/models.py:30`, `:85`, `:90` | **VERIFIED** |
| Метрика AI-запросов хранит только **длину** текста сообщения, не текст | `bot:apps/observability/models.py:210` | **VERIFIED** |
| Sentry подключён, есть скраббер событий | `bot:config/settings/base.py:1193-1195`, скраббер `:1468-1473`; обязателен в prod — `bot:config/settings/production.py:46-50` | **VERIFIED** |

### 2.5 Что зашифровано, а что нет — сводка

| Данные | Шифрование at-rest | Якорь |
|---|---|---|
| `MemoryEntry.content` (факты о человеке, включая red-зону) | **ДА** (Fernet; red — дополнительно хеш-перец) | `bot:apps/identity/models.py:740` |
| `BotUser.phone` | НЕТ | `bot:apps/identity/models.py:120` |
| `UserPreferences.allergies` (медданные) | НЕТ | `bot:apps/identity/models.py:334` |
| `UserPersonalContext.summary` | НЕТ (явно указано в help_text) | `bot:apps/identity/models.py:584`, `:588-590` |
| `Message.content` / `rendered_text` (весь текст диалога) | НЕТ | `bot:apps/conversations/models.py:425`, `:432` |
| `BookingRequest.client_name` / `client_phone` / `comment` | НЕТ | `bot:apps/booking/models.py:142-145` |
| `WebhookJournal.raw_payload` (сырой payload с текстом и именем) | НЕТ | `bot:apps/ingress/models.py:37` |
| `AdminTask.transcript_snapshot` (копия переписки) | НЕТ | `bot:apps/handoff/models.py:108` |

**Класс:** **VERIFIED** — по каждой строке проверено наличие/отсутствие обёртки `encrypt(...)`; единственное вхождение `encrypt` в моделях бота — `bot:apps/identity/models.py:740`.

### 2.6 Состав данных в бекенде Ayla (`ayla:`)

Бекенд — канонический владелец пользователя, записей, платежей и дневника питания. Все якоря ниже относительны каталога `C:\Users\user\PycharmProjects\Ayla\djangoproject`.

#### 2.6.1 Пользователь и профиль

| Данные | Поля | Якорь | Класс |
|---|---|---|---|
| Идентификация | `User.phone` (unique) — основной идентификатор; `role` (client/specialist/admin); `deleted_at` | `ayla:users/models.py:9`, `:17`, `:16`, `:46` | **VERIFIED** |
| Email, имя, фамилия | унаследованы от `AbstractUser`, используются в коде | `ayla:users/services.py:793-796` | **VERIFIED** |
| Профиль | `Profile.full_name` (ФИО), `avatar`, `bio`, `city` | `ayla:users/models.py:80`, `:86`, `:87`, `:88`, `:89` | **VERIFIED** |
| **Геолокация** | `default_location_lat` / `default_location_lng` | `ayla:users/models.py:91-96` | **VERIFIED** |
| **Коды подтверждения** | `OTPCode.phone`, `OTPCode.code` — **SMS-код хранится в БД в открытом виде** | `ayla:users/models.py:267`, `:270`, `:271` | **VERIFIED** |
| Соцсети | `SocialAccount.provider_uid`, `extra_data` (JSON — сырой ответ VK/Google/Apple/Yandex, может содержать имя и email) | `ayla:users/models.py:295`, `:311`, `:312` | **VERIFIED** |
| Устройства | `DeviceToken` — FCM-токен устройства; `AnonymousSession.device_id` | `ayla:users/models.py:325`, `:342`, `:550`, `:563` | **VERIFIED** |
| AI-память | `UserPersonalContext`: предпочитаемые районы, слоты, бюджет, `diet_type`, **`skin_sensitivities`**, район работы/дома, любимые мастера, занятые дни, провенанс данных | `ayla:users/models.py:412`, `:463`, `:470`, `:477-482`, `:485`, `:490`, `:504`, `:511`, `:512`, `:520`, `:534` | **VERIFIED** |
| Дата рождения клиента | отдельного поля **не найдено** (возраст только в анкете питания) | `ayla:users/models.py` — поля нет | **VERIFIED (отсутствие)** |

#### 2.6.2 Мастера и сотрудники

| Данные | Якорь | Класс |
|---|---|---|
| `SpecialistProfile`: `display_name`, `avatar`, `bio`, **`address`** (адрес работы, до 500 симв.), `location_lat`/`location_lng`, внешние id YClients | `ayla:users/models.py:102`, `:126`, `:127`, `:130`, `:134`, `:135-140`, `:188`, `:195` | **VERIFIED** |
| `SpecialistPortfolio.image` — фото работ | `ayla:users/models.py:228`, `:250` | **VERIFIED** |
| `TenantUserRelationship` — роли, `granted_at`, `revoked_at`, `revoke_reason`; в комментарии помечено как «152-ФЗ access-log» | `ayla:users/models.py:593`, `:653`, `:659`, `:662`, `:671` | **VERIFIED** |
| `SpecialistTimeOff.reason` — свободный текст (отпуск / больничный) → потенциально данные о здоровье сотрудника | `ayla:appointments/models.py:335`, `:346` | **VERIFIED** |
| Паспорт, ИНН, СНИЛС, сканы документов, банковские реквизиты — **не найдено** | обследованы модели `ayla:users/`, `ayla:payments/` | **VERIFIED (отсутствие)** |

#### 2.6.3 Записи, отзывы, аналитика

| Данные | Якорь | Класс |
|---|---|---|
| `Appointment`: клиент, специалист, услуга, время начала/конца, снимок названия услуги и цены, `is_first_visit`, **`notes`** (свободный текст клиента), **`cancellation_reason`** (до 500 симв.) | `ayla:appointments/models.py:19`, `:36`, `:41`, `:59`, `:65-66`, `:83-99`, `:102`, `:105`, `:106` | **VERIFIED** |
| `IdempotencyKey.response_payload` — кешированные ответы API, могут содержать данные записи | `ayla:appointments/models.py:560`, `:614` | **VERIFIED** |
| `Review.text` (свободный отзыв до 1000 симв.), `is_anonymous`, `specialist_reply` | `ayla:reviews/models.py:11`, `:41`, `:43`, `:47` | **VERIFIED** |
| `AnalyticsEvent.payload` (JSON), `actor`, `anonymous_session_id` | `ayla:analytics/models.py:37`, `:49`, `:52`, `:60` | **VERIFIED** |

#### 2.6.4 ⚠ Данные о здоровье в бекенде — значительно больший объём, чем в боте

| Факт | Якорь | Класс |
|---|---|---|
| `NutritionProfile` (1:1 с пользователем) — фактически медицинская анкета: пол, возраст, рост, вес, коэффициент активности, цель, темп, диетические предпочтения | `ayla:nutrition/models.py:353`, `:402`, `:405`, `:406`, `:407`, `:408`, `:415`, `:416`, `:419`, `:422` | **VERIFIED** |
| **`NutritionProfile.health_flags` (JSON)** — ключевое поле спецкатегории | `ayla:nutrition/models.py:425` | **VERIFIED** |
| **Состав допустимых значений `health_flags`: беременность, грудное вскармливание, диабет 1 и 2 типа, преддиабет, расстройство пищевого поведения, приём лекарств, аллергии** | `ayla:nutrition/serializers.py:428-433`, валидация `:480` | **VERIFIED** |
| Расчётные показатели: BMR, суточные нормы КБЖУ, воды, микронутриентов (витамин D, B12, C, железо, кальций, магний, омега-3, клетчатка) | `ayla:nutrition/models.py:428`, `:430-433`, `:438-445` | **VERIFIED** |
| Аудит корректировок цели: `goal_overridden_by`, `bmi_warning_overridden_at`, `last_overrides_applied` (почему цель скорректирована — беременность / ГВ / РПП) | `ayla:nutrition/models.py:448`, `:449`, `:450` | **VERIFIED** |
| Фиксация ознакомления с дисклеймером: `disclaimer_acked` (JSON: ts / version / screen) | `ayla:nutrition/models.py:453` | **VERIFIED** |
| Обработка беременности / ГВ / РПП в бизнес-логике (надбавки калорий и белка, отдельная ветка для РПП) | `ayla:nutrition/services/nutrition_profile_service.py:146`, `:157-171`, `:357-368` | **VERIFIED** |
| Правила «нутрициология → бьюти-услуга» с исключениями по флагам здоровья + история показанных персональных рекомендаций | `ayla:nutrition/models.py:706`, `:754-755`, `:816`, `:855`, `:871` | **VERIFIED** |
| Данные здоровья отображаются в Django-админке (`health_flags`, `disclaimer_acked`, антропометрия) | `ayla:nutrition/admin.py:93`, `:96`, `:106` | **VERIFIED** |
| **`FoodScan.image` — фото еды ХРАНИТСЯ** в объектном хранилище по пути `food-scans/<user_id>/<scan_id>.jpg`; плюс `dish_name`, `ingredients`, `nutrition`, `raw_response` (сырой ответ провайдера) | `ayla:nutrition/models.py:27`, `:50`, `:23-24`, `:53`, `:56`, `:59`, `:72` | **VERIFIED** |
| Срок хранения фото еды — 30 дней, но **только через lifecycle-политику бакета**; удаления на стороне Django нет | `ayla:nutrition/models.py:11-13` | **VERIFIED** |
| Дневник питания и напитков: `FoodLog` (калории, БЖУ, микронутриенты, тип приёма пищи, время), `WaterLog` / `WaterEntry` (включая `sugar_g`, `caffeine_mg` и категорию «Алкоголь») | `ayla:nutrition/models.py:100`, `:157-179`, `:181-182`, `:209`, `:495`, `:554`, `:555`, `:642` | **VERIFIED** |
| Полный текст переписки с ИИ хранится в бекенде; редактируются в нём только телефон и email — фамилии и адреса не вычищаются (признано в коде) | `ayla:ai/models.py:34`, `:101`, `:115`, `:8`; редактор `ayla:ai/redaction.py:37`, `:7-10` | **VERIFIED** |
| `diet_type` и **`skin_sensitivities`** (аллергии/чувствительности кожи) подставляются в системный промпт LLM | `ayla:ai/personal_context_hint.py:103`, `:107` | **VERIFIED** |
| **Флаги здоровья (беременность, ГВ, диабет, гипертония, проблемы ЖКТ) вставляются в текст промпта и уходят в OpenAI** | `ayla:nutrition/services/ai_comment_service.py:193-202`, сборка промпта `:181`, вызов `:151-158`; ветка РПП `:80`, `:115` | **VERIFIED** |

#### 2.6.5 Медицинские и косметологические услуги в каталоге

| Факт | Якорь | Класс |
|---|---|---|
| У услуги есть `requires_health_check` и `contraindications` («мед. профиль, разрешение врача») | `ayla:services/models.py:113`, `:117` | **VERIFIED** |
| Канонический каталог — 1223 услуги; загрузчик маппит поле `note` в `contraindications` | `ayla:services/seeds/canonical_catalog_2026-07.json`; `ayla:services/management/commands/seed_canonical_catalog.py:131`, флаг `:10` | **VERIFIED** |
| Категории медицинского/косметологического характера: «Инъекционная косметология» (69 услуг, все с флагом медконсультации), «Медико-эстетические и смежные услуги» (20, все с флагом), «Аппаратная косметология лица» (46), «Косметология лица» (145), «Лазерная эпиляция и удаление волос» (71), «Массаж тела» (139), «Аппаратный массаж и коррекция фигуры» (87), «Педикюр и подология» (44), «Тату, пирсинг и удаление» (28), «Перманентный макияж и татуаж» (40), «Солярий и загар» (11) | `ayla:services/seeds/canonical_catalog_2026-07.json:1120`, `:1135`, `:1150` | **VERIFIED** |
| Всего услуг с `requires_health_check=true` — 102 (из них 85 по признаку `category-medical`) | там же | **VERIFIED** |
| Выбранная услуга фиксируется в записи навсегда — FK + снимок названия | `ayla:appointments/models.py:59`, `:83` | **VERIFIED** |

**Замечание для юриста.** Сам факт записи на услугу из категорий «Инъекционная косметология» или «Медико-эстетические услуги» — это, по существу, сведения, из которых можно сделать вывод о состоянии здоровья и о получаемых медицинских манипуляциях. Хранится этот факт в открытом виде и бессрочно. Требует оценки: является ли это обработкой специальной категории по ст. 10 152-ФЗ.

#### 2.6.6 Платежи в бекенде

| Факт | Якорь | Класс |
|---|---|---|
| `Payment`: сумма, статус, доход/комиссия/возврат, провайдер, `provider_payment_id`, `provider_client_secret`, id последнего вебхука. **Номера карт (PAN) не хранятся** | `ayla:payments/models.py:23`, `:51`, `:55`, `:60-68`, `:71-74`, `:75`, `:80` | **VERIFIED** |
| **В чек для ЮKassa (54-ФЗ) кладутся телефон и email плательщика** | `ayla:payments/services.py:210` (`build_appointment_receipt`), `:236-241`, блок `customer` `:258`, добавление в payload `:106-107` | **VERIFIED** |
| При пустых контактных данных подставляется хардкод-заглушка телефона | `ayla:payments/services.py:247-248` | **VERIFIED** |
| Точки вызова: создание платежа и второй путь | `ayla:payments/views.py:332-346`, `ayla:payments/services.py:369-386`, `ayla:payments/views.py:705` | **VERIFIED** |
| В исходящее событие `payment.failed` имена/email/номера карт не кладутся | `ayla:payments/views.py:630-633` | **VERIFIED** |

#### 2.6.7 Логи бекенда с ПДн

| Факт | Якорь | Класс |
|---|---|---|
| В dev-режиме в лог уходит **телефон + полный текст SMS, включая код подтверждения** | `ayla:users/sms.py:37` | **VERIFIED** |
| Телефон в INFO при отправке SMS и при ошибке провайдера | `ayla:users/sms.py:79`, `:85-86` | **VERIFIED** |
| Телефон в открытом виде: `logger.info("Phone %s bound to user %s", ...)` | `ayla:users/social_auth.py:321` | **VERIFIED** |
| В stub-режиме пуш логируется целиком (`title`/`body` — имя мастера, дата, адрес) | `ayla:notifications/services/push.py:110` | **VERIFIED** |
| В лог пишутся первые 200 символов ответа модели по фото еды | `ayla:nutrition/providers/openai_vision.py:132`; аналогично для Yandex — `ayla:nutrition/providers/yandex.py:126` | **VERIFIED** |
| Связка «пользователь ↔ факт скана еды» в логе ошибок | `ayla:nutrition/views.py:166-168` | **VERIFIED** |
| Лог удаления аккаунта с `user_id` и **свободным текстом причины** | `ayla:users/services.py:787-789` | **VERIFIED** |

---

## 3. Передача третьим лицам

### 3.1 OpenAI и Anthropic — ГЛАВНЫЙ ВОПРОС ДОКУМЕНТА

#### 3.1.1 Что передаётся и куда

| Факт | Якорь | Класс |
|---|---|---|
| Подключены два LLM-провайдера: OpenAI и Anthropic | `bot:apps/llm/router.py:202-209` | **VERIFIED** |
| Модели по умолчанию OpenAI: `gpt-4o-mini` (генерация), `text-embedding-3-small` (эмбеддинги) | `bot:apps/llm/providers/openai_provider.py:73`, `:74` | **VERIFIED** |
| Модели по умолчанию Anthropic: `claude-haiku-4-5`, `claude-sonnet-4-6` | `bot:apps/llm/providers/anthropic_provider.py:89`, `:90` | **VERIFIED** |
| **`base_url` нигде не переопределяется** → запросы идут на дефолтные хосты SDK: `api.openai.com` и `api.anthropic.com` (США) | `bot:apps/llm/providers/openai_provider.py:331`, `:348`; `bot:apps/llm/providers/anthropic_provider.py:300`, `:314` | **VERIFIED** |
| Используется HTTP-прокси для обхода блокировки этих хостов с российских серверов (`OPENAI_PROXY`, `ANTHROPIC_PROXY`) | `bot:apps/llm/providers/openai_provider.py:97`, `:341-347`, комментарий `:38`; `bot:apps/llm/providers/anthropic_provider.py:125-129`, комментарий `:51-53` | **VERIFIED** |
| Российского LLM-вендора (GigaChat, YandexGPT) в коде **не найдено** | поиск по репозиторию бота | **VERIFIED (отсутствие)** |
| Эмбеддинги реализованы только у OpenAI: у Anthropic метод объявлен, но в докстроке прямо сказано «Anthropic exposes no embeddings API» → векторизация текстов всегда идёт в OpenAI | `bot:apps/llm/providers/openai_provider.py:74`; `bot:apps/llm/providers/anthropic_provider.py:141`, `:147` | **VERIFIED** |
| В LLM передаётся содержимое **всех** сообщений диалога (`messages[*].content`), включая system-промпт | `bot:apps/llm/pii_protected_provider.py:31-36` | **VERIFIED** |

#### 3.1.2 Механизм псевдонимизации — как он устроен

| Факт | Якорь | Класс |
|---|---|---|
| Существует декоратор `PIITokenizingProvider`, который оборачивает **любого** провайдера в момент его создания роутером | `bot:apps/llm/pii_protected_provider.py:113`; обёртка — `bot:apps/llm/router.py:227-229` | **VERIFIED** |
| Токенизируются 5 категорий: PHONE, EMAIL, CC (карта), OTP, URL_TOKEN — **только по регексам**; распознавание русских имён (NER) отложено | `bot:apps/llm/pii_tokenizer.py:16-22` | **VERIFIED** |
| Формат токена `<{КАТЕГОРИЯ}_{NONCE}_{ИНДЕКС}>`, карта токенов живёт в Redis с TTL, в PostgreSQL не пишется | `bot:apps/llm/pii_tokenizer.py:30-42`, `:44-56` | **VERIFIED** |
| Ответ модели детокенизируется обратно, включая строковые аргументы tool-вызовов | `bot:apps/llm/pii_protected_provider.py:193`, `:210-218` | **VERIFIED** |
| **Аргументы инструментов (`tools`) не токенизируются** — задокументированный разрыв | `bot:apps/llm/pii_protected_provider.py:38-39` | **VERIFIED** |
| Активируется декоратор **не автоматически**, а через ContextVar-скоуп `pii_context(conversation_id)` | `bot:apps/llm/pii_tokenizer.py:111`; чтение скоупа — `bot:apps/llm/pii_protected_provider.py:152` | **VERIFIED** |

#### 3.1.3 ⚠ КЛЮЧЕВОЙ ФАКТ: что происходит при отсутствии активного скоупа

**Ответ: персональные данные НЕ маскируются. Сообщения уходят в OpenAI в исходном виде.**

```
conversation_id = pii_tokenizer.current_conversation_id()
if conversation_id is None:
    logger.warning("pii_protected_provider.no_active_scope provider=%s messages_count=%d. "
                   "Caller may be missing pii_context() wrapper, OR this is a known background flow.", ...)
    return await self._wrapped.complete(messages, ...)   # ← БЕЗ токенизации
```

| Факт | Якорь | Класс |
|---|---|---|
| При `conversation_id is None` декоратор **логирует WARNING и передаёт сообщения дальше без изменений** | `bot:apps/llm/pii_protected_provider.py:152-170` (сам ранний `return` — `:164-170`) | **VERIFIED** |
| Это поведение задокументировано как намеренное: «Decorator is a no-op pass-through», WARNING — «policy guidance, NOT a hard block» | `bot:apps/llm/pii_protected_provider.py:12-17`, `:26-29` | **VERIFIED** |
| Текст WARNING в коде **дословно совпадает** с сообщением, которое наблюдается в логах пилота на каждом ходе консьержа (`pii_protected_provider.no_active_scope provider=openai messages_count=N`, «Caller may be missing pii_context() wrapper») | `bot:apps/llm/pii_protected_provider.py:157-163` | **VERIFIED** |
| В этой же ветке **не пишется аудит-строка** `llm.call_completed` — ранний `return` стоит до вызова `_emit_call_audit_async` | `bot:apps/llm/pii_protected_provider.py:164-170` vs `:187-192` | **VERIFIED** |
| Следствие: вызовы без скоупа не оставляют следа в журнале, который задуман как доказательство псевдонимизации по 152-ФЗ | `bot:apps/llm/pii_protected_provider.py:86-89`, `:281-291` | **INFERRED** |

#### 3.1.4 Где скоуп есть, а где его нет

| Маршрут | Скоуп `pii_context` | Якорь | Класс |
|---|---|---|---|
| **Основной тенантный пайплайн** (per-tenant диалог) | **ЕСТЬ** — `_pii_enter(conversation.id)` выставляется сразу после резолва разговора и до любого шага, ведущего к LLM | `bot:apps/orchestrator/pipeline.py:621`, обоснование `:604-620` | **VERIFIED** |
| **Глобальный консьерж (tenant-less DM в MAX)** — **основной пользовательский маршрут пилота** | **НЕТ** | вызов LLM: `bot:apps/orchestrator/concierge.py:145` (`provider.complete(...)`), резолв провайдера `:142`; точка вызова из канала: `bot:apps/channels/max/handler.py:728`. Поиск `pii_` по всему `handler.py` — **ни одного совпадения** | **VERIFIED** |
| **Legacy discovery** | **НЕТ** | `bot:apps/orchestrator/discovery.py:246`, `:249` | **VERIFIED** |
| **Эмбеддинги базы знаний** (текст KB, включая телефоны мастеров и противопоказания услуг) | **НЕТ** | `bot:apps/kb/services/retriever.py:246`; `bot:apps/kb/services/ingester.py:254`; содержимое KB — `bot:apps/kb/projectors.py:92-95` | **VERIFIED** |
| **AI-черновики ответов мастера** | **ЕСТЬ** | `bot:apps/master_api/services/ai_drafts.py:649`, `:651` | **VERIFIED** |
| **Классификация намерения** (внутри тенантного пайплайна) | **ЕСТЬ** (унаследован от пайплайна) | `bot:apps/orchestrator/pipeline.py:650-655`, комментарий `:633-641` | **VERIFIED** |
| Скиллы booking/faq (внутри тенантного пайплайна) | **ЕСТЬ** (унаследован) | `bot:apps/skills/booking/skill.py:583`, `:763`; `bot:apps/skills/faq/skill.py:199`, `:257` | **INFERRED** — наследование ContextVar через `sync_to_async`/`asyncio.run` следует из модели контекстных переменных, но на исполнении не проверялось |
| Единственные не-тестовые вызовы `pii_context` во всём репозитории — два | `bot:apps/orchestrator/pipeline.py:621` и `bot:apps/master_api/services/ai_drafts.py:651` | **VERIFIED** |

#### 3.1.5 Дополнительно: глобальный выключатель

| Факт | Якорь | Класс |
|---|---|---|
| Есть настройка `PII_TOKENIZER_ENABLED`, по умолчанию включена (`"1"`) | `bot:config/settings/base.py:350`, комментарий `:341` | **VERIFIED** |
| В локальной конфигурации разработки токенизация **выключена** (`PII_TOKENIZER_ENABLED = False`) | `bot:config/settings/local.py:24` | **VERIFIED** |
| Фактическое значение переменной окружения на продакшн-хосте пилота | — | **UNKNOWN** (продакшн-конфигурация не обследовалась) |

#### 3.1.6 Что означает вывод 3.1.3–3.1.4 на практике

**INFERRED (следует из связки фактов выше):** на пути глобального консьержа — том самом, по которому идёт основной трафик пилота, — в OpenAI (США) уходит:

- полный текст сообщений пользователя, включая упоминания боли, симптомов, лекарств (сам консьерж-промпт предусматривает медицинскую тему — `bot:apps/orchestrator/concierge.py:293-298`);
- system-промпт, в который подмешивается **блок памяти о пользователе** (`memory_block`) и **персональный контекст** (`extra_system`) — `bot:apps/channels/max/handler.py:719`, `:736-739`; `bot:apps/orchestrator/concierge.py:301-304`;
- история последних сообщений из таблицы `Message` — `bot:apps/orchestrator/concierge.py:201-214` (`load_recent_history`, лимит 10);
- всё это — **без замены телефона/email на токены**, поскольку скоуп не активирован.

Ранее подготовленная внутренняя записка для юриста прямо оговаривает, что вся её правовая позиция построена на допущении «PII токенизируется до вызова LLM» и что при несоблюдении этого допущения текст записки нужно пересматривать: `bot:docs/legal/2026-06-02-cross-border-legal-review-brief.md:63-67`, `:240`. **VERIFIED** (как факт наличия такой оговорки в документе).

### 3.2 Полный перечень получателей

| # | Получатель | Что передаётся | Юрисдикция | Якорь | Класс |
|---|---|---|---|---|---|
| 1 | **OpenAI** | Текст диалога (`messages[*].content`), system-промпт с памятью о пользователе, история сообщений; эмбеддинги текстов КБ | **США** (`api.openai.com`, через прокси) | `bot:apps/llm/providers/openai_provider.py:73-74`, `:331`, `:341-347` | **VERIFIED** |
| 2 | **Anthropic** | Текст диалога (когда роутер выбирает этого провайдера) | **США** (`api.anthropic.com`, через прокси) | `bot:apps/llm/providers/anthropic_provider.py:89-90`, `:300`, `:125-129` | **VERIFIED** |
| 3 | **MAX (VK)** — канал доставки | Исходящий текст ответа бота, вложения; `chat_id` в query-параметре; токен бота в заголовке | РФ (`https://botapi.max.ru`) | `bot:apps/channels/max/outbound.py:42`, `:97-99`, `:106`, `:109`, `:148-150`; настройка `bot:config/settings/base.py:393` | **VERIFIED** |
| 4 | **Telegram** — второй канал + канал операторских алертов | Текст сообщений (канал), тексты алертов (операторский бот) | **зарубежная инфраструктура** (`https://api.telegram.org`, через прокси) | `bot:apps/channels/telegram/outbound.py:60`, `:115`; прокси `bot:apps/channels/telegram/proxy.py:49`, `bot:config/settings/base.py:1284`; алерты `bot:config/settings/base.py:1265-1266` | **VERIFIED** |
| 5 | **Backend Ayla** (свой контур) | `X-External-User-ID`; при записи — **только идентификаторы** (`client_id`, `specialist_id`, `service_id`, `start_datetime`); профиль — `display_name` + `avatar_url`; **байты фото еды**; антропометрия и `health_flags`; декларируемые предпочтения | РФ (`AYLA_BASE_URL`) | `bot:apps/integrations/ayla/booking_client.py:524-531`, `:816-822`; `bot:apps/integrations/ayla/profile_client.py:145-159` и запрет на phone/email/birthday `:5`, `:30`; `bot:apps/integrations/ayla/nutrition_client.py:313-341`, `:626-650`; `bot:apps/integrations/ayla/personal_context_client.py:209` | **VERIFIED** |
| 6 | **ЮKassa** | **Напрямую из бота — не передаётся** (интеграция retired, вебхук отвечает `410 Gone`). Платёж создаётся через Ayla, в Ayla уходят `amount_rub`, `description`, `kind`, `recipient_name`, `buyer_email` | РФ | `bot:apps/integrations/yookassa_retired/views.py:1-20`; `bot:apps/integrations/ayla_payments/client.py:288-294` | **VERIFIED** |
| 7 | **YClients** (внешняя booking-система салона) | В коде клиента `create_record()` отправляет `phone`, `fullname`, `email`, `comment`, `notify_by_email` — **самый чувствительный исходящий payload в репозитории**. **Однако вызовов этого метода из скиллов/хендлеров не найдено** — интеграция выглядит спящей | РФ | `bot:apps/integrations/yclients/client.py:584-596`, базовый URL `:61`; настройки `bot:config/settings/base.py:699-702` | **VERIFIED** (наличие кода) / **INFERRED** (что не вызывается в проде) |
| 8 | **Sentry** (мониторинг ошибок) | Трейсы исключений; применяется скраббер PII | **UNKNOWN** — регион/self-hosted не определён (значение `SENTRY_DSN` не выписывалось) | `bot:config/settings/base.py:1193-1195`, скраббер `:1468-1473`; обязателен в prod `bot:config/settings/production.py:46-50` | **VERIFIED** (факт подключения) |
| 9 | **Google Docs** (источник базы знаний) | Только `doc_id`, экспорт документа; пользовательских данных не уходит | США | `bot:apps/kb/services/gdocs_client.py:71`, `:68` | **VERIFIED** |
| 10 | **OpenTelemetry-коллектор** | Трейсы; по умолчанию не настроен (пустой endpoint = no-op) | **UNKNOWN** | `bot:config/settings/base.py:1185` | **VERIFIED** |

**Не найдено в репозитории бота:** SMTP/email-рассылка (отложена — `bot:apps/admin_api/views_invite.py:29`, `:112`), SMS-провайдеры (Twilio/SMSC), Firebase/FCM/push, внешняя веб-аналитика (PostHog/Amplitude/Метрика/GA), boto3. **VERIFIED (отсутствие).**

### 3.3 Инфраструктура и хостинг

| Факт | Якорь | Класс |
|---|---|---|
| PostgreSQL, Redis, ChromaDB, MinIO — **свои контейнеры**, не managed-сервисы | `bot:docker-compose.yml:8-16`, `:25-29`, `:38-42`, `:64-73` | **VERIFIED** |
| В staging порты БД/Redis/Chroma **не публикуются наружу** | `bot:docker-compose.staging.yml:127-141` | **VERIFIED** |
| Деплой — systemd-юниты на собственном сервере, nginx перед gunicorn на `127.0.0.1` | `bot:infra/systemd/*.template`; `bot:infra/nginx/ai-bot-platform-api.conf.template:18-24`, `:50` | **VERIFIED** |
| Указаний на публичное облако (AWS/GCP/Azure/Yandex Cloud) и на регион размещения в инфраструктурных файлах **не найдено** | обследованы `bot:docker-compose.yml`, `bot:docker-compose.staging.yml`, `bot:infra/` | **VERIFIED (отсутствие)** |
| Физическое местонахождение серверов пилота, юрисдикция дата-центра, наличие договора с хостером как с обработчиком | — | **UNKNOWN** |

### 3.4 Трансграничная передача — сводка для юриста

**За пределы РФ (по коду) уходят:**

| Направление | Данные | Класс |
|---|---|---|
| OpenAI (США) | Текст диалогов пользователей, включая упоминания здоровья; блок памяти о пользователе в system-промпте; **на маршруте консьержа — без псевдонимизации** | **VERIFIED** (маршрут и отсутствие скоупа) / **INFERRED** (состав конкретных полей в конкретном запросе) |
| OpenAI (США) | Эмбеддинги содержимого базы знаний (телефоны мастеров, адреса салонов, противопоказания услуг) | **VERIFIED** |
| Anthropic (США) | Текст диалогов — когда роутер выбирает этого провайдера | **VERIFIED** |
| Telegram | Тексты сообщений канала и операторских алертов | **VERIFIED** |
| Google (США) | Только идентификатор документа базы знаний | **VERIFIED** |
| OpenAI (США) — через Ayla | Фото еды (по внутренней записке — распознавание идёт в OpenAI Vision) | **VERIFIED (по документу** `bot:docs/legal/2026-06-02-cross-border-legal-review-brief.md:51-57`**)**; подтверждение в коде бекенда — см. раздел 3.5 |

**Что видит пользователь.** Текст, который реально показывается в Mini App:

> «Основные данные хранятся на серверах в России (152-ФЗ).»
> «Для понимания твоих сообщений Ayla может использовать AI-обработку через внешних поставщиков (включая Anthropic). Передача защищена шифрованием.»

Якорь: `bot:apps/miniapp/src/components/DisclosureSheet.tsx:36-37`. **VERIFIED.**

**Расхождения между этим текстом и кодом (факты, без правовой оценки):**

1. Назван только Anthropic; фактический провайдер в логах пилота — `provider=openai`, а эмбеддинги технически возможны только у OpenAI (`bot:apps/llm/providers/openai_provider.py:74`). **VERIFIED.**
2. Страна получателя (США) в тексте не названа. **VERIFIED.**
3. Слова «передаём только текст сообщения — без имени, телефона и других контактов» в шипнутой версии нет; в предложенной редакции r2 они есть (`bot:docs/legal/2026-06-02-cross-border-legal-review-brief.md:166-168`), и при отсутствии активного скоупа это утверждение кодом не обеспечивается. **VERIFIED.**
4. Строка про 180-дневную анонимизацию сообщений была **удалена** из текста 2026-07-19 с формулировкой, что бекенд-задача не реализована (`bot:apps/miniapp/src/components/DisclosureSheet.tsx:26-29`). **VERIFIED.**

### 3.5 Получатели данных со стороны бекенда Ayla

| # | Получатель | Что передаётся | Юрисдикция | Якорь | Класс |
|---|---|---|---|---|---|
| 1 | **OpenAI** | Текст чата (после вычистки телефона и email); персональный контекст — тип диеты и **чувствительности кожи**; **фото еды** (OpenAI Vision); **флаги здоровья в тексте промпта** (беременность, ГВ, диабет, гипертония, ЖКТ) | **США** (`api.openai.com`, через прокси) | `ayla:djangoProject/settings/base.py:421-429`; клиент `ayla:ai/services/llm_client.py:20-35`; редакция `ayla:ai/application/services/chat_service.py:100`; контекст `ayla:ai/personal_context_hint.py:103`, `:107`; фото `ayla:nutrition/providers/openai_vision.py:33`; здоровье `ayla:nutrition/services/ai_comment_service.py:193-202` | **VERIFIED** |
| 2 | **Yandex Cloud Vision** | Фото еды (резервный провайдер) | РФ | `ayla:djangoProject/settings/base.py:520-526`, `:530-531`; `ayla:nutrition/providers/yandex.py` | **VERIFIED** |
| 3 | **Anthropic** | **В коде бекенда не найден** — фактический LLM-клиент только OpenAI | — | обследованы `ayla:ai/`, `ayla:nutrition/providers/` | **VERIFIED (отсутствие)** |
| 4 | **SMS.RU** | Телефон + текст сообщения, включая **коды подтверждения** и шаблоны с именем мастера и адресом | РФ | `ayla:users/sms.py:10`, `:47-68`, `:90-93`; настройки `ayla:djangoProject/settings/base.py:380-383`; шаблоны `ayla:notifications/templates.py:86` | **VERIFIED** |
| 5 | **Firebase Cloud Messaging (Google)** | Токен устройства, `title`/`body` пуша с **именем мастера/клиента**, датой, адресом; `data` с идентификаторами | **США** | `ayla:djangoProject/settings/base.py:452-459`; `ayla:notifications/services/push.py:126-137`; тексты `ayla:notifications/templates.py:66`, `:74`, `:85`, `:157`, `:165`, `:181`, `:189` | **VERIFIED** |
| 6 | **Sentry** | Трейсы; выставлено `send_default_pii=False`, есть `before_send` | **UNKNOWN** (регион) | `ayla:djangoProject/settings/base.py:821-856`, `:853`, `:841` | **VERIFIED (факт подключения)** |
| 7 | **VK / Google / Apple / Yandex ID** (соцавторизация) | Обмен email, именем, uid | смешанная (Apple и Google — США) | `ayla:djangoProject/settings/base.py:386-410`; `ayla:users/social_auth.py` | **VERIFIED** |
| 8 | **ЮKassa** | Сумма, идентификаторы + **чек по 54-ФЗ с телефоном и email плательщика** | РФ | `ayla:payments/services.py:236-241`, `:258`, `:106-107` | **VERIFIED** |
| 9 | **MAX-бот (исходящий вебхук)** | События питания, паттернов и профиля с `external_user_id` | свой контур | `ayla:nutrition/models.py:250`, `:283`, `:287`; `ayla:nutrition/webhook_delivery.py` | **VERIFIED** |
| 10 | **S3 / MinIO** (django-storages) | Аватары, портфолио мастеров, **фото еды** | самостоятельный хостинг | `ayla:djangoProject/settings/base.py:53` | **VERIFIED** |

**Локализация до трансграничной передачи.** В коде есть явная реализация принципа «сначала запись в российскую БД, потом отправка фото в OpenAI», со ссылкой на ст. 18 п. 5 152-ФЗ: `ayla:nutrition/views.py:134-143`, `:265`. **VERIFIED.**

**Сводка по трансграничной передаче (оба репозитория):**

| Получатель за пределами РФ | Что уходит | Есть ли псевдонимизация |
|---|---|---|
| OpenAI (США) — из бота | Текст диалога, память о пользователе в system-промпте, история сообщений, эмбеддинги базы знаний | **На маршруте консьержа — НЕТ** (раздел 3.1.3–3.1.4) |
| OpenAI (США) — из бекенда | Текст чата, фото еды, **флаги здоровья в промпте**, чувствительности кожи | Телефон и email вычищаются (`ayla:ai/redaction.py:37`); **фамилии, адреса и флаги здоровья — нет** (`ayla:ai/redaction.py:7-10`) |
| Anthropic (США) — из бота | Текст диалога (когда выбран этот провайдер) | Зависит от того же скоупа |
| Firebase / Google (США) | Токен устройства + текст пуша с именем и адресом | нет |
| Apple / Google (США) — соцавторизация | email, имя, uid | нет |
| Telegram | Тексты сообщений и алертов | нет |

**Класс:** **VERIFIED** по каждому получателю (якоря выше); **INFERRED** — в части того, какие именно поля попадают в конкретный запрос в конкретном сценарии.

---

## 4. Сроки хранения и удаление

### 4.1 Что чистится автоматически — бот

Расписание задач — `bot:config/settings/base.py:948` и далее. **VERIFIED.**

| Что чистится | Задача | Срок | Расписание | Якорь |
|---|---|---|---|---|
| Журнал аудита | `cleanup_old_audit_logs` | `AUDIT_LOG_RETENTION_DAYS` = **90 дней**, режим `hard`/`soft` | 03:00 UTC | `bot:apps/audit/tasks.py:42`, `:64`, `:65`; настройки `bot:config/settings/base.py:256`, `:273`; расписание `:952` |
| Ключи идемпотентности | `cleanup_old_idempotency_keys` | **7 дней** или истёкший `expires_at` | ежечасно | `bot:apps/tools/tasks.py:29`, `:47`, `:72`; настройка `bot:config/settings/base.py:257`; расписание `:958` |
| AI-черновики (терминальные) | `purge_old_ai_drafts` | **30 дней**; активные черновики не трогаются | 03:15 UTC | `bot:apps/conversations/tasks.py:61`, `:53`, докстрока `:29-34`; расписание `bot:config/settings/base.py:1100` |
| Трассы replay | `cleanup_expired_replay_traces` | per-row `expires_at`, **30 дней** | 04:00 UTC | `bot:apps/replay/tasks.py:39`, `:58`; настройка `bot:config/settings/base.py:338`; расписание `:992` |
| DLQ шины событий | `eventbus.cleanup_ingest_dlq` | **90 дней** (реплейнутые — 30) | 04:45 UTC | `bot:apps/eventbus/cleanup_tasks.py:142`, `:102`, `:108`; расписание `bot:config/settings/base.py:1070` |
| Дедупликация шины | `eventbus.cleanup_ingest_dedupe` | **120 дней** | 04:50 UTC | `bot:apps/eventbus/cleanup_tasks.py:184`, `:111`; расписание `bot:config/settings/base.py:1077` |
| Вторичные регистры шины | `eventbus.cleanup_ingest_secondary_ledgers` | **120 дней** | 04:55 UTC | `bot:apps/eventbus/cleanup_tasks.py:252`, `:118`; расписание `bot:config/settings/base.py:1087` |

Дополнительно: содержимое AI-черновика обнуляется немедленно при переходе в терминальный статус (`bot:apps/master_api/services/ai_drafts.py`) — **VERIFIED**. Есть health-check «не сломался ли sweep аудита» — `bot:apps/orchestrator/health.py:175` — **VERIFIED**.

### 4.2 TTL в Redis (бот)

| Что | Срок | Якорь | Класс |
|---|---|---|---|
| Краткосрочная память диалога | `SHORT_TERM_MEMORY_TTL_SECONDS`, по умолчанию **24 ч** | `bot:apps/orchestrator/memory/short_term.py:78`, применение `:119`; настройка `bot:config/settings/base.py:390` | **VERIFIED** |
| Карта PII-токенов | `PII_TOKENMAP_TTL_SECONDS`, по умолчанию **25 ч** (24 ч + 1 ч запаса); в PostgreSQL не пишется никогда | `bot:apps/llm/pii_tokenizer.py:272`, `:282-283`, обновление `:480`, `:486`, сброс при закрытии диалога `:427`, докстрока `:27` | **VERIFIED** |
| Отложенный вопрос памяти | **24 ч** | `bot:apps/orchestrator/memory_ask.py:53`, `:89` | **VERIFIED** |
| Сессия мастера | `MASTER_SESSION_TTL_DAYS` = 30 дней | `bot:apps/master_api/auth.py:162` | **VERIFIED** |
| Приглашение администратора | 7 дней | `bot:apps/admin_api/views_invite.py:105` | **VERIFIED** |
| Отложенное действие бронирования | 10 минут | `bot:apps/bookings/pending_actions.py:74` | **VERIFIED** |

### 4.3 ⚠ Что НЕ чистится вообще

| Данные | Состояние | Якорь | Класс |
|---|---|---|---|
| **`WebhookJournal.raw_payload`** — сырые вебхуки с именами, текстами сообщений, возможно телефонами | **Механизма очистки НЕ НАЙДЕНО.** В `apps/ingress/` нет `tasks.py`, нет management-команд, нет записи в расписании celery beat, нигде нет `WebhookJournal…delete()`. Хранилище растёт неограниченно | `bot:apps/ingress/models.py:25`, `:37-40`; отсутствие — по всему `bot:apps/ingress/` и `bot:config/settings/base.py:948+` | **VERIFIED (отсутствие)** |
| **`Conversation` / `Message`** — весь текст диалогов | **Retention НЕ НАЙДЕН.** В `apps/conversations/tasks.py` есть только очистка AI-черновиков | `bot:apps/conversations/tasks.py:61` — единственная задача в модуле | **VERIFIED (отсутствие)** |
| **`MemoryEntry`** — зонированные факты, включая red-зону (здоровье) | Причина удаления `ttl_purge` **объявлена** (`DELETION_REASON_TTL_PURGE`), политика сроков зафиксирована в ADR (green — без TTL, yellow — 365 д., red — 90 д.), но **самой sweep-задачи не существует**: единственная периодическая задача в `apps/identity` — пересчёт профилей | объявление `bot:apps/identity/models.py:678`, `:685`; политика ADR `bot:docs/adr/ADR-0011-user-personal-context-privacy.md:150-151`; отсутствие задачи — `bot:apps/identity/tasks.py:85` (единственный `shared_task`) | **VERIFIED (отсутствие)** |
| **`forget_all_requested_at`** — отметка «забудь всё» | Отметка ставится (`bot:apps/identity/services/memory_deleter.py:76`), но **асинхронного стирателя, который её обрабатывает, не найдено** | `bot:apps/identity/services/memory_deleter.py:76`; отсутствие — `bot:apps/identity/tasks.py` | **VERIFIED (отсутствие)** |
| `AIRequestMetric`, основной регистр шины событий | Sweep не найден | `bot:apps/observability/models.py:114` | **VERIFIED (отсутствие)** |
| `ConsentRecord` | Не чистится намеренно (append-only журнал согласий) | `bot:apps/consent/models.py:56` | **VERIFIED** |
| `RedZoneAccessLog` | Хранится 7 лет намеренно, без FK CASCADE | `bot:apps/identity/models.py:841`; ADR `bot:docs/adr/ADR-0011-user-personal-context-privacy.md:179` (§7) | **VERIFIED** |
| Задача очистки платёжных событий (`PAYMENT_EVENT_RETENTION_DAYS = 90`) | Настройка есть, но **задача выведена из эксплуатации** | `bot:config/settings/base.py:278`, `:994-998` | **VERIFIED** |

### 4.4 Retention в бекенде Ayla

| Что | Состояние | Якорь | Класс |
|---|---|---|---|
| `WaterEntry` (удалённые) | Чистятся, **> 90 дней** | `ayla:nutrition/tasks.py:20-34`; расписание `ayla:djangoProject/settings/base.py:762-763` | **VERIFIED** |
| Ключи идемпотентности | TTL 24 ч | `ayla:appointments/tasks.py:219-233`; расписание `ayla:djangoProject/settings/base.py:786-787` | **VERIFIED** |
| **`FoodScan` / фото еды** | Django-стороннего удаления **нет**; заявлен TTL 30 дней **только через lifecycle-политику бакета** объектного хранилища (проверить фактическую настройку бакета в коде нельзя) | `ayla:nutrition/models.py:11-13`, `:27`, `:50` | **VERIFIED (отсутствие кода)** / **UNKNOWN (фактическая настройка бакета)** |
| **`ai.Message`** (переписка с ИИ) | Retention **не найден**; в модели заявлено бессрочное хранение | `ayla:ai/models.py:8`, `:101` | **VERIFIED (отсутствие)** |
| `Appointment`, `Notification`, `AnalyticsEvent` | Retention **не найден** | обследованы `ayla:appointments/tasks.py`, `ayla:notifications/tasks.py`, `ayla:analytics/` | **VERIFIED (отсутствие)** |
| «Retention» в `ayla:notifications/tasks.py:247+` | Это **маркетинговый** retention (напоминания клиентам), а не удаление данных — не путать | `ayla:notifications/tasks.py:247` | **VERIFIED** |

### 4.5 Удаление по запросу субъекта

| Механизм | Что делает | Якорь | Класс |
|---|---|---|---|
| **Mini App: `DELETE /api/v1/customer/me/personal-data/`** | Требует подтверждения — в теле запроса токен `УДАЛИТЬ`. Каскад: удаление в Ayla → стирание green-памяти + отметка forget-all → отзыв согласий → стирание идентификаторов бота (`phone`, `display_name`, `client_name`, `avatar_url`, `context`) и удаление `UserPreferences` (включая **аллергии** и дату рождения). Частичный отказ → `502` со списком проваленных шагов | маршрут `bot:apps/miniapp_api/urls.py:62`, вью `bot:apps/miniapp_api/views.py:1656`, проверка токена `:1691`; сервис `bot:apps/identity/services/privacy.py:403`, шаги `:417-505`, стирание `:269-289`; токен `bot:apps/identity/services/profile.py:190` | **VERIFIED** |
| **Mini App: `POST /me/delete`** | Мягкое удаление (второй, более старый путь) — тот же токен подтверждения | `bot:apps/miniapp_api/urls.py:53`; `bot:apps/identity/services/profile.py:198` | **VERIFIED** |
| **Чат-бот: «удали мои данные»** | Распознаёт фразу, но **сам ничего не удаляет** — отвечает, что подтвердить в чате нельзя, и перенаправляет в Mini App | триггеры `bot:apps/skills/privacy_consent/skill.py:66-71`, ответ `:79-83`, матчер `:103`, `:111`, `:114` | **VERIFIED** |
| **Чат-бот: команды памяти** | «Что ты обо мне знаешь», «забудь {X}», «забудь всё» — **исполняются**, двухшаговое подтверждение словом «удалить» | `bot:apps/persona/memory_commands.py:129`, `:57`, `:73`, `:78`, `:53`, `:148`; вызов из канала `bot:apps/channels/max/handler.py:669` | **VERIFIED** |
| **Бекенд Ayla: `DELETE /users/me`** | Мягкое удаление + анонимизация: обнуляются телефон, email, имя, аватар, `full_name`, деактивируется профиль специалиста, refresh-токены в чёрный список | `ayla:users/views.py:639-655`; `ayla:users/services.py:774`, `:793-799`, `:805-810`, `:812-820` | **VERIFIED** |
| **Бекенд Ayla: удаление персонального контекста** | Удаление отдельного поля или всего контекста | `ayla:users/personal_context_views.py:8-9`, `:183`, `:196` | **VERIFIED** |
| Слэш-команды `/delete`, `/mydata`, `/забыть` | **Не найдено** — единственная слэш-команда в каналах это `/start` | `bot:apps/channels/max/handler.py:406`; `bot:apps/channels/max/global_onboarding.py:130` | **VERIFIED (отсутствие)** |

### 4.6 ⚠ Что удаление НЕ затрагивает

Из разбора каскада (`bot:apps/identity/services/privacy.py:417-505`) и кода бекенда (`ayla:users/services.py:774-820`) следует, что при удалении по запросу субъекта **остаются**:

| Данные | Якорь | Класс |
|---|---|---|
| `Conversation` / `Message` — весь текст переписки в боте | шаги каскада `bot:apps/identity/services/privacy.py:417-505` — шага для сообщений нет | **VERIFIED (отсутствие шага)** |
| `BookingRequest.client_name` / `client_phone` / `comment` — имя, телефон и комментарий в записях | там же | **VERIFIED (отсутствие шага)** |
| `WebhookJournal.raw_payload` — сырой вебхук с именем и текстом | там же | **VERIFIED (отсутствие шага)** |
| `AdminTask.transcript_snapshot` — замороженная копия переписки | там же | **VERIFIED (отсутствие шага)** |
| Yellow- и red-зонные `MemoryEntry` — каскад стирает только **green**-записи | `bot:apps/identity/services/privacy.py:464-466` (`read_green_entries` → `soft_delete_green_entries`) | **VERIFIED** |
| В бекенде: записи, платежи, фото еды, сообщения ИИ | `ayla:users/services.py:774-820` — этих шагов в удалении нет | **VERIFIED (отсутствие шага)** |
| Строка `BotUser` физически сохраняется (erase-in-place), поскольку на неё ссылаются `on_delete=PROTECT` из метрик, задач и назначений персонала | обоснование зафиксировано в `bot:DRF-956-privacy-prepilot-report.md:61-73` | **VERIFIED** |

**Известное ограничение, зафиксированное в самом проекте.** Внутренний отчёт прямо перечисляет открытые пункты, в т.ч. «Транскрипты `Conversation`/`Message` не стирает никто; у `forget_all` нет sweep-задачи» и «Export резолвит субъекта по строке, delete — по человеку»: `bot:DRF-956-privacy-prepilot-report.md:198-212`. **VERIFIED.**

**Ограничения стирания в резервных копиях.** Проектная политика фиксирует окно: живые строки — мгновенно, кэши — до 1 ч, WAL — до 24 ч, ночные бэкапы — до 30 дней, холодные квартальные — до 90 дней; и прямо формулирует это как «erasure = более не используется, а не физически невосстановимо»: `bot:docs/adr/ADR-0011-user-personal-context-privacy.md:351` и далее (§11.1). **VERIFIED (как политика).** Фактическая настройка бэкапов на проде — **UNKNOWN**.

---

## 5. Согласия

### 5.1 Что реализовано

| Факт | Якорь | Класс |
|---|---|---|
| Единая таблица согласий `ConsentRecord` (append-only): тип согласия, признак `granted` (False = явный отказ), источник, **версия документа**, дата фиксации, дата отзыва (строка не удаляется) | `bot:apps/consent/models.py:56`, `:97`, `:102`, `:107`, `:114`, `:121`, `:122` | **VERIFIED** |
| Связь с пользователем — FK с `on_delete=CASCADE` (при полном удалении пользователя история согласий уходит вместе с ним) | `bot:apps/consent/models.py:87` | **VERIFIED** |
| Общий штамп `BotUser.consent_at` | `bot:apps/identity/migrations/0011_botuser_consent_at.py` | **VERIFIED** |
| Отдельный фича-флаг `BotUser.food_scanner_consent_at` — **вне** `ConsentRecord` | `bot:apps/identity/models.py:196` | **VERIFIED** |
| Сервисы: `grant()`, `record_global_consent()` (tenant-less, атомарно штампует `consent_at`), `withdraw()`, `has_consent()`, `has_global_consent()`, `get_consents()` | `bot:apps/consent/services.py:53`, `:146`, `:216`, `:256`, `:367`, `:403`, `:545` | **VERIFIED** |
| Гейт зонированной памяти: `has_memory_consent()` (кросс-тенантно по `ayla_user_id`), `withdraw_personal_data()`, `withdraw_personal_data_for_bot_users()` | `bot:apps/consent/services.py:439`, `:447`, `:462`, `:496`, `:514` | **VERIFIED** |
| `can_store_green_memory()` сводится к согласию `PERSONAL_DATA` | `bot:apps/consent/memory.py:47`, `:55` | **VERIFIED** |

### 5.2 Объявленные типы согласия и их реальное использование

| Константа | Строка | Реально используется? | Якорь | Класс |
|---|---|---|---|---|
| `PERSONAL_DATA` | `personal_data` | **ДА** — основной рабочий тип | `bot:apps/consent/models.py:63-77`; `bot:apps/consent/memory.py:55` | **VERIFIED** |
| `MARKETING` | `marketing` | Только UI-заглушка в Mini App | `bot:apps/miniapp/src/lib/customer-profile.ts:245` | **VERIFIED** |
| **`PHOTO_BIOMETRIC`** | `photo_biometric` | **НЕТ** — объявлен, но нигде не гранится и не проверяется | `bot:apps/consent/models.py:69` (объявление); вызовов не найдено | **VERIFIED (отсутствие)** |
| **`HEALTH`** | `health` | **НЕТ** — объявлен, но нигде не гранится и не проверяется | `bot:apps/consent/models.py:70` (объявление); вызовов не найдено | **VERIFIED (отсутствие)** |
| `MEMORY_GREEN` | `memory_green` | В маппинге есть, активного сбора нет | `bot:apps/consent/services.py:439` | **VERIFIED** |
| `MEMORY_YELLOW` | `memory_yellow` | Фундамент, сбора нет | `bot:apps/consent/models.py:63-77` | **VERIFIED** |
| `MEMORY_RED` | `memory_red` | Фундамент, сбора нет | `bot:apps/consent/models.py:63-77` | **VERIFIED** |
| Декоратор `@consent_required(consent_type)` — при отказе выбрасывает `ConsentDenied` | Реализован, но **ни одного применения в продакшн-коде не найдено** (только тесты) | `bot:apps/consent/decorators.py:61`; `bot:apps/consent/tests/test_decorators.py` | **VERIFIED (отсутствие)** |

### 5.3 Где согласие реально запрашивается у человека

| Точка | Что происходит | Якорь | Класс |
|---|---|---|---|
| **Экран S2 приветствия в боте** (главный путь) | Текст согласия + три кнопки: «Да, продолжим» / «Узнать что хранится» / «Не сейчас» | `bot:apps/skills/welcome/skill.py:151` (текст), `:569`, `:582-584` (кнопки), `:171` (отказ), `:262-311` (обработка), `:365` (штамп) | **VERIFIED** |
| **Глобальный онбординг MAX** (маршрут пилота) | Тот же экран; согласие журналируется на сервере с версией документа `welcome-s2-v1` | `bot:apps/channels/max/global_onboarding.py:96`, `:102`, `:185`, `:219` | **VERIFIED** |
| Онбординг включается флагом `GLOBAL_BOT_ONBOARDING` и работает как **«мягкий гейт»**: приветствие и сбор согласия происходят, но **диалог на нём не блокируется** | | `bot:apps/channels/max/handler.py:636-639`, комментарий `:616-619` | **VERIFIED** |
| **Экран согласия фото-сканера в Mini App** | «Можно показать тебе фото-скан?» → «Хорошо, разрешаю» / «Не сейчас». **Сохраняется в `localStorage` браузера, а не на сервере** | `bot:apps/miniapp/src/screens/FoodScannerCaptureScreen.tsx:337-365`; хранение `bot:apps/miniapp/src/lib/food-scanner.ts:446`, `:457` | **VERIFIED** |
| Серверный гейт фото-сканера в боте (отдельный путь) | Читает `food_scanner_consent_at`; при отсутствии — отказ | `bot:apps/skills/food_scanner/skill.py:353` | **VERIFIED** |
| **Экран согласий в профиле Mini App** | Тумблер «Акции и предложения» + информационная строка «Хранение данных» с датой. **Работает на клиентских заглушках**: `fetchConsents()` и `setMarketingConsent()` обёрнуты в `guardProd`, состояние живёт в памяти; серверных эндпоинтов `GET/POST /api/v1/me/consents` **не найдено** | UI `bot:apps/miniapp/src/screens/CustomerProfileScreen.tsx:304`, `:318`, компонент `bot:apps/miniapp/src/components/ConsentRow.tsx:49`; заглушки `bot:apps/miniapp/src/lib/customer-profile.ts:235`, `:245`, `:153`; отсутствие маршрутов — `bot:apps/miniapp_api/urls.py` | **VERIFIED** |
| **Согласие на сохранение банковских карт** | Чекбоксы есть, но версия оферты — плейсхолдер: `"offer-client-cards-0.0-todo-legal"` и `"offer-0.0-todo-legal"`; юридического текста нет | `bot:apps/miniapp/src/lib/cards.ts:30`; `bot:apps/miniapp/src/lib/master-billing.ts:120`; чекбоксы `bot:apps/miniapp/src/screens/CustomerCardsScreen.tsx:205`, `bot:apps/miniapp/src/screens/MasterBillingScreen.tsx:295` | **VERIFIED** |
| **Бекенд Ayla** | Отдельной модели/поля фиксации согласия на обработку ПДн **не найдено**. Единственная «фиксация» — неявная: комментарий в коде «Booking IS the consent gesture», грант доступа тенанту с `granted_by=SELF`; журнал грантов/отзывов помечен как «152-ФЗ access-log» | `ayla:appointments/application/services/create_booking_service.py:181-182`, `:242`; `ayla:users/models.py:653`, `:659`, `:662`, `:671` | **VERIFIED** |
| Дисклеймер по питанию в бекенде | `disclaimer_acked` (ts / version / screen) — единственная реальная фиксация ознакомления в бекенде | `ayla:nutrition/models.py:453` | **VERIFIED** |

### 5.4 Полный текст согласия, который видит пользователь

Ниже — дословный текст из кода (`bot:apps/skills/welcome/skill.py:151-156` и `:161-166`). **VERIFIED.**

> **S2 (основной экран согласия):**
> «Прежде чем начать — короткое слово.
> Я буду помнить о тебе только то, что поможет рекомендовать точнее. Хранится безопасно. Удалить можно в любой момент.
>
> Продолжим?»

> **S2a (разворачивается по кнопке «Узнать что хранится»):**
> «Запоминаю: твои сообщения мне, выбранные цели, питание и вода если решишь логировать, записи к мастерам. Не делюсь с салонами без твоего разрешения. Подробнее в Профиле → „Данные обо мне" когда зайдёшь.»

**Наблюдаемые факты об этом тексте (без правовой оценки):**

1. Не назван оператор персональных данных (наименование юридического лица, адрес). **VERIFIED.**
2. Не указана цель обработки в терминах закона, не перечислены категории данных как категории ПДн. **VERIFIED.**
3. **Не упомянута передача данных третьим лицам и трансграничная передача** — ни OpenAI, ни Anthropic, ни США. **VERIFIED.**
4. **Не упомянуты данные о здоровье** и нет отдельного согласия на специальную категорию. **VERIFIED.**
5. Нет ссылки на политику конфиденциальности или иной документ. **VERIFIED.**
6. Нет срока действия согласия и порядка отзыва в тексте самого согласия. **VERIFIED.**

### 5.5 Документ, на который ссылается `document_version`

| Факт | Якорь | Класс |
|---|---|---|
| `document_version` хранит только строку версии (`"welcome-s2-v1"`); ни текста, ни URL документа в репозиториях **не найдено** | `bot:apps/channels/max/global_onboarding.py:102`; поле `bot:apps/consent/models.py:114` | **VERIFIED (отсутствие)** |
| Единственное упоминание «Политики конфиденциальности» — это сид базы знаний, а не показываемый в согласии документ | `bot:apps/kb/management/commands/seed_kb_from_mysite.py:123` | **VERIFIED** |
| В бекенде Ayla политика конфиденциальности / оферта / версия согласия **не найдены** | обследованы `ayla:users/`, `ayla:djangoProject/` | **VERIFIED (отсутствие)** |

### 5.6 Техническая надёжность фиксации согласия

| Факт | Якорь | Класс |
|---|---|---|
| `record_global_consent` не защищён от гонки — нет частичного уникального индекса; это признано в докстроке самого кода | `bot:apps/consent/services.py:146`, комментарий `:173-178` | **VERIFIED** |

---

## 6. Права субъекта персональных данных

| Право | Реализовано? | Как именно / чего не хватает | Якорь | Класс |
|---|---|---|---|---|
| **Доступ к своим данным (что хранится)** | **Частично** | В боте есть чат-команда «что ты обо мне знаешь» — показывает только **green**-память | `bot:apps/persona/memory_commands.py:57`, `:129` | **VERIFIED** |
| **Экспорт (переносимость)** | **Частично** | `GET /api/v1/customer/me/personal-data/export/` отдаёт JSON-вложение. Состав: секция Ayla (с бекенда), **только green**-память, согласия. **НЕ включает**: текст диалогов (`Message`), записи (`BookingRequest`), телефон/имя (`BotUser`), настройки (`UserPreferences`, включая аллергии), yellow/red-память | маршрут `bot:apps/miniapp_api/urls.py:56`, вью `bot:apps/miniapp_api/views.py:1624`; сервис и состав ответа `bot:apps/identity/services/privacy.py:303`, `:345-359`, `:362-375`, `:387-395` | **VERIFIED** |
| Экспорт в бекенде Ayla | **Нет** | Эндпоинта DSAR/экспорта в бекенде **не найдено**; бот получает экспорт через внутренний клиент | `ayla:` — не найдено; клиент бота `bot:apps/integrations/ayla/personal_context_client.py:261` | **VERIFIED (отсутствие в бекенде)** |
| **Исправление (уточнение)** | **Частично** | Пользователь может править профиль (в т.ч. аллергии и дату рождения) через Mini App. Отдельного эндпоинта исправления факта памяти (`PATCH /memory/{id}`) **нет** — в проектной документации помечен как нереализованный follow-up | `bot:apps/miniapp_api/views.py:1545-1578`; ADR `bot:docs/adr/ADR-0011-user-personal-context-privacy.md:202` (§8, строка «Right to rectify … Not yet ticketed») | **VERIFIED** |
| **Удаление** | **Да, но неполно** | См. раздел 4.5 и 4.6: каскад работает, но не затрагивает диалоги, записи, сырые вебхуки, снимки переписки в задачах эскалации, yellow/red-память, а также записи/платежи/фото еды в бекенде | `bot:apps/identity/services/privacy.py:403-505`; `ayla:users/services.py:774-820` | **VERIFIED** |
| **Отзыв согласия** | **Частично** | Отзыв реализован как шаг каскада удаления (`withdraw_personal_data_for_bot_users`) и как сервис `withdraw()`. **Отдельного пользовательского интерфейса «отозвать согласие, не удаляя аккаунт» нет** — экран согласий в Mini App работает на клиентских заглушках без серверных эндпоинтов | `bot:apps/consent/services.py:256`, `:514`; каскад `bot:apps/identity/services/privacy.py:477`; заглушки `bot:apps/miniapp/src/lib/customer-profile.ts:235`, `:245` | **VERIFIED** |
| **Возражение против обработки** | **Нет отдельного механизма** | В ADR помечено как нереализованный follow-up (§13.3) | `bot:docs/adr/ADR-0011-user-personal-context-privacy.md:202` (§8, строка «Right to object … Follow-up ticket §13.3») | **VERIFIED** |
| **Сведения об автоматизированной обработке** | **Частично** | В модели памяти есть поле провенанса (`source`: явно указано / выведено / сигнал), в бекенде — `data_sources`. Пользовательской поверхности, где это показывается, в коде подтвердить не удалось | `bot:apps/identity/models.py:712`; `ayla:users/models.py:534` | **VERIFIED (поля)** / **UNKNOWN (UI)** |
| **Расхождение субъекта между экспортом и удалением** | — | Экспорт резолвит субъекта по строке `BotUser`, удаление — по «человеку» (по `ayla_user_id` + сиблингам канала). Расхождение зафиксировано в самом проекте как открытый пункт | `bot:DRF-956-privacy-prepilot-report.md:205` | **VERIFIED** |

**Существенный контекст.** В проекте зафиксировано, что поле связи `BotUser.ayla_user_id` **в проде никем не заполняется**, из-за чего удаление возвращает честный `502 partial` вместо ложного «удалено», а локальные данные при этом стираются: `bot:DRF-956-privacy-prepilot-report.md:93-103`, `:85-89`. Проверить актуальность этого утверждения на текущем HEAD не удалось — **UNKNOWN**.

---

## 7. Пробелы и риски — для юриста

Оценка серьёзности — мнение технического аудитора, не юридическая квалификация. Шкала: **КРИТИЧНО** / **ВЫСОКИЙ** / **СРЕДНИЙ** / **НИЗКИЙ**.

| № | Пробел | Серьёзность | Обоснование и якорь |
|---|---|---|---|
| 1 | **На основном маршруте пилота (глобальный консьерж MAX) псевдонимизация ПДн перед отправкой в OpenAI не активируется** — текст уходит в исходном виде; аудит-строка при этом тоже не пишется | **КРИТИЧНО** | `bot:apps/llm/pii_protected_provider.py:152-170`; отсутствие скоупа на маршруте — `bot:apps/orchestrator/concierge.py:145` + `bot:apps/channels/max/handler.py:728` (в файле нет ни одного вхождения `pii_`) |
| 2 | **Внутренняя записка для юриста от 2026-06-02 построена на допущении, что псевдонимизация работает**; это допущение на маршруте консьержа не выполняется, значит правовая позиция записки требует пересмотра | **КРИТИЧНО** | `bot:docs/legal/2026-06-02-cross-border-legal-review-brief.md:63-67`, `:240` |
| 3 | **Данные о здоровье передаются за границу в открытом виде из бекенда**: флаги беременности, ГВ, диабета, гипертонии, ЖКТ подставляются в текст промпта OpenAI | **КРИТИЧНО** | `ayla:nutrition/services/ai_comment_service.py:193-202`, `:181`, `:151-158` |
| 4 | **Согласия на обработку специальных категорий (здоровье) не существует**: тип `HEALTH` объявлен, но нигде не запрашивается и не проверяется. То же для `PHOTO_BIOMETRIC` | **КРИТИЧНО** | `bot:apps/consent/models.py:70`, `:69` — только объявления; вызовов не найдено |
| 5 | **Текст согласия не раскрывает передачу третьим лицам и трансграничную передачу**, не называет оператора, не ссылается на политику конфиденциальности, не упоминает данные о здоровье | **ВЫСОКИЙ** | `bot:apps/skills/welcome/skill.py:151-156`, `:161-166` |
| 6 | **Текста политики конфиденциальности не существует ни в одном репозитории**; `document_version` хранит только строку `"welcome-s2-v1"` | **ВЫСОКИЙ** | `bot:apps/consent/models.py:114`; `bot:apps/channels/max/global_onboarding.py:102` |
| 7 | **Публичное раскрытие называет только Anthropic**, тогда как фактически данные идут в OpenAI; страна получателя не названа | **ВЫСОКИЙ** | `bot:apps/miniapp/src/components/DisclosureSheet.tsx:36-37` vs `bot:apps/llm/providers/openai_provider.py:73-74` |
| 8 | **`WebhookJournal.raw_payload` копится бессрочно** — сырые вебхуки с именами, полным текстом сообщений, возможно телефонами; очистки нет; поле выведено в админку | **ВЫСОКИЙ** | `bot:apps/ingress/models.py:37-40`; `bot:apps/ingress/admin.py:31`; отсутствие задачи очистки — по всему `bot:apps/ingress/` |
| 9 | **Текст всех диалогов (`Message.content`) хранится бессрочно, без шифрования и без retention** — включая рассказы о боли и симптомах из скилла health screening | **ВЫСОКИЙ** | `bot:apps/conversations/models.py:425`, `:432`; отсутствие retention — `bot:apps/conversations/tasks.py:61` |
| 10 | **Удаление по запросу субъекта не затрагивает диалоги, записи, сырые вебхуки, снимки переписки, yellow/red-память, а в бекенде — записи, платежи, фото еды и переписку с ИИ** | **ВЫСОКИЙ** | `bot:apps/identity/services/privacy.py:417-505`; `ayla:users/services.py:774-820` |
| 11 | **Заявленные сроки хранения памяти (red — 90 дней, yellow — 365) не реализованы**: sweep-задачи не существует; `forget_all_requested_at` ставится, но обработчика нет | **ВЫСОКИЙ** | политика `bot:docs/adr/ADR-0011-user-personal-context-privacy.md:150-151`; отсутствие — `bot:apps/identity/tasks.py:85`, `bot:apps/identity/services/memory_deleter.py:76` |
| 12 | **Аллергии и противопоказания (`UserPreferences.allergies`) хранятся без шифрования**, тогда как аналогичные факты в `MemoryEntry` шифруются — неоднородная защита одних и тех же по существу данных | **ВЫСОКИЙ** | `bot:apps/identity/models.py:334` vs `:740` |
| 13 | **В бекенде код подтверждения (OTP) хранится в БД в открытом виде**, а в dev-режиме телефон и полный текст SMS с кодом пишутся в лог | **ВЫСОКИЙ** | `ayla:users/models.py:271`; `ayla:users/sms.py:37` |
| 14 | **Экран управления согласиями в Mini App — клиентская заглушка**: серверных эндпоинтов `GET/POST /me/consents` не существует, состояние живёт в памяти браузера. То есть управление согласиями пользователю фактически недоступно | **ВЫСОКИЙ** | `bot:apps/miniapp/src/lib/customer-profile.ts:235`, `:245`, `:153`; отсутствие маршрутов — `bot:apps/miniapp_api/urls.py` |
| 15 | **Согласие на фото-сканер в Mini App хранится в `localStorage`** — доказательства согласия на стороне оператора нет (серверный флаг — отдельный путь бота) | **ВЫСОКИЙ** | `bot:apps/miniapp/src/lib/food-scanner.ts:446`, `:457` vs `bot:apps/identity/models.py:196` |
| 16 | **Факт записи на медицинскую/косметологическую услугу хранится бессрочно и в открытом виде** (102 услуги помечены как требующие медконсультации, в т.ч. вся «Инъекционная косметология») | **ВЫСОКИЙ** | `ayla:services/seeds/canonical_catalog_2026-07.json:1120`, `:1135`, `:1150`; хранение — `ayla:appointments/models.py:59`, `:83`; в боте — `bot:apps/booking/models.py:139-140` |
| 17 | **Фото еды хранится в объектном хранилище; удаления на стороне приложения нет**, заявленный TTL 30 дней держится только на lifecycle-политике бакета, проверить которую по коду нельзя | **ВЫСОКИЙ** | `ayla:nutrition/models.py:11-13`, `:50` |
| 18 | **Онбординг с согласием — «мягкий гейт»**: диалог (и, соответственно, отправка текста в LLM) не блокируется отсутствием согласия; сам онбординг ещё и за фича-флагом | **ВЫСОКИЙ** | `bot:apps/channels/max/handler.py:636-639`, комментарий `:616-619` |
| 19 | **Согласие на сохранение банковских карт ссылается на версию-плейсхолдер** `"offer-...-0.0-todo-legal"` — юридического текста оферты нет | **СРЕДНИЙ** | `bot:apps/miniapp/src/lib/cards.ts:30`; `bot:apps/miniapp/src/lib/master-billing.ts:120` |
| 20 | **Имена не редактируются в логах** ни в боте (сознательное решение), ни в бекенде (фамилии и адреса) | **СРЕДНИЙ** | `bot:apps/observability/pii_filter.py:46-50`; `ayla:ai/redaction.py:7-10` |
| 21 | **Сырой текст пользователя попадает в лог** в нескольких местах | **СРЕДНИЙ** | `bot:apps/bookings/callbacks.py:170`, `:503`; `bot:apps/promptreg/cache.py:283` |
| 22 | **Телефон доступен для поиска и отображается в Django-админке без маскирования** (в мастер-API маскирование есть — значит требование осознано, но применено не везде) | **СРЕДНИЙ** | `bot:apps/identity/admin.py:140`, `:153` vs `bot:apps/master_api/services/customers.py:82`, `:268` |
| 23 | **Эмбеддинги базы знаний уходят в OpenAI без PII-скоупа**, а в базу знаний проецируются противопоказания услуг и контакты мастеров | **СРЕДНИЙ** | `bot:apps/kb/services/retriever.py:246`, `bot:apps/kb/services/ingester.py:254`; содержимое `bot:apps/kb/projectors.py:92-95` |
| 24 | **Экспорт данных неполон** — не содержит диалогов, записей, телефона/имени, настроек (включая аллергии) и yellow/red-памяти; субъект экспорта и субъект удаления определяются по-разному | **СРЕДНИЙ** | `bot:apps/identity/services/privacy.py:387-395`; `bot:DRF-956-privacy-prepilot-report.md:205` |
| 25 | **Права на исправление факта памяти и на возражение против обработки не реализованы** | **СРЕДНИЙ** | `bot:docs/adr/ADR-0011-user-personal-context-privacy.md:202` (§8) |
| 26 | **Аргументы tool-вызовов LLM не токенизируются** — задокументированный разрыв; при этом телефон клиента фигурирует как параметр инструмента, который подставляет модель | **СРЕДНИЙ** | `bot:apps/llm/pii_protected_provider.py:38-39`; параметр `bot:apps/skills/booking/tools.py:207-209` |
| 27 | **Внутренний чат мастер↔админ хранит plaintext-сообщения**, статус PII-сканирования вложений есть в схеме, но пайплайн сканирования не реализован | **СРЕДНИЙ** | `bot:apps/internal_chat/models.py:426`, `:534` |
| 28 | **Декоратор `@consent_required` не применён ни к одному продакшн-вызову** — механизм проверки согласия существует, но не включён | **СРЕДНИЙ** | `bot:apps/consent/decorators.py:61`; применений вне тестов не найдено |
| 29 | **Фиксация согласия не защищена от гонки** (нет уникального индекса) — возможны дубли или потеря записи | **НИЗКИЙ** | `bot:apps/consent/services.py:146`, `:173-178` |
| 30 | **В боте есть код интеграции с YClients, который отправляет телефон, ФИО, email и комментарий клиента**; вызовов из рабочего кода не найдено, но код присутствует и активируется переменными окружения | **НИЗКИЙ** (условно) | `bot:apps/integrations/yclients/client.py:584-596` |
| 31 | **Диалог допускается с лицами от 14 лет** (анкета питания), механизм согласия законного представителя в коде не найден | **ВЫСОКИЙ** | `bot:apps/skills/nutrition_anketa/fsm.py:53`; защита несовершеннолетних `bot:apps/identity/models.py:600` |
| 32 | **Локальная конфигурация разработки полностью выключает токенизацию** (`PII_TOKENIZER_ENABLED = False`); фактическое значение на проде не проверялось | **СРЕДНИЙ** / **UNKNOWN** | `bot:config/settings/local.py:24` |

### 7.1 Вопросы, на которые должен ответить именно юрист

1. Достаточна ли текущая схема согласия (экран S2) как правовое основание для **передачи содержимого диалогов в зарубежный LLM** (OpenAI, США), с учётом того, что текст согласия трансграничную передачу не упоминает.
2. Меняется ли ответ на вопрос 1, если псевдонимизация **фактически не применяется** на основном маршруте (раздел 3.1.3).
3. Требуется ли **отдельная письменная форма согласия** на обработку данных о состоянии здоровья (ст. 10 152-ФЗ) — с учётом того, что в системе есть: свободный текст аллергий и противопоказаний, флаги беременности/ГВ/диабета/РПП, опрос о боли и симптомах, антропометрия, фото еды.
4. Является ли **сам факт записи на услугу из категории «Инъекционная косметология» / «Медико-эстетические услуги»** обработкой специальной категории по ст. 10.
5. Требуется ли **уведомление Роскомнадзора о трансграничной передаче** и по каким получателям (OpenAI, Anthropic, Firebase/Google, Apple, Telegram); подано ли оно.
6. Достаточно ли реализованного принципа «сначала запись в РФ, потом отправка за рубеж» (`ayla:nutrition/views.py:134-143`) для соблюдения требования о локализации.
7. Приемлемо ли, что **удаление по запросу субъекта не затрагивает переписку, записи и платежи**, и какая часть этого удержания правомерна как обязательная ретенция (потребительское законодательство, бухгалтерский учёт, 54-ФЗ).
8. Какие **сроки хранения** должны быть установлены нормативно для: текста диалогов, сырых вебхуков, фото еды, анкеты питания, зонированной памяти.
9. Достаточно ли механизма отзыва согласия «отозвать = удалить аккаунт», или требуется гранулярный отзыв по каждой цели обработки.
10. Кто является **оператором** персональных данных, а кто — обработчиком, в схеме «платформа ↔ салон-арендатор ↔ мастер», и как это должно быть отражено в документах и в тексте согласия.
11. Правомерна ли обработка данных лиц **от 14 до 18 лет** в текущем виде и какой механизм согласия законного представителя требуется.
12. Требуется ли договор поручения обработки (или DPA) с OpenAI, и совместим ли типовой DPA OpenAI с требованиями 152-ФЗ; аналогично для Firebase, Sentry, SMS-провайдера, хостера.
13. Какой объём данных должен покрывать **экспорт по запросу субъекта**, чтобы считаться исполнением права на доступ.
14. Требуется ли **оценка воздействия на приватность / модель угроз и аттестация ИСПДн**, и какой уровень защищённости применим при обработке специальных категорий.
15. Правомерно ли хранение **кода подтверждения (OTP) в открытом виде** и логирование телефона с текстом SMS.

---

## 8. Вопросы к юристу — готовый список для первой консультации

**Блок A. Основания обработки и согласия**

1. Кто у нас оператор ПДн, а кто обработчик — платформа, салон-арендатор, мастер? Как это должно быть оформлено между сторонами?
2. Нужно ли уведомление в Роскомнадзор об обработке ПДн, и подано ли оно? В какой срок?
3. Достаточно ли одного экрана согласия в чате, или нужна отдельная форма (в т.ч. письменная / с усиленной идентификацией)?
4. Что обязательно должно быть в тексте согласия по 152-ФЗ, чего сейчас нет: наименование и адрес оператора, перечень категорий данных, цели, перечень действий, срок действия, порядок отзыва, сведения о передаче третьим лицам и за рубеж?
5. Нужен ли отдельный документ «Политика в отношении обработки персональных данных», где он должен публиковаться и как на него ссылаться из бота и Mini App?
6. Допустима ли текущая схема, при которой диалог с ботом не блокируется отсутствием согласия («мягкий гейт»)?
7. Как правильно фиксировать факт получения согласия, чтобы он был доказуемым: что хранить, какая версия документа, как долго?

**Блок B. Специальные категории (здоровье)**

8. Какие из наших данных вы квалифицируете как данные о состоянии здоровья: свободный текст аллергий; флаги беременности/ГВ/диабета/преддиабета/РПП/приёма лекарств; ответы на вопросы о боли и симптомах; рост/вес/ИМТ; фото еды; факт записи на инъекционную косметологию?
9. Требуется ли отдельное согласие в письменной форме на каждую из этих категорий, и можно ли объединить их в одно?
10. Есть ли для нас ограничения по обработке данных о здоровье как для не-медицинской организации? Меняется ли это, если мы просто передаём противопоказания мастеру?
11. Что делать с уже собранными данными о здоровье, если выяснится, что основание для их обработки было недостаточным?

**Блок C. Трансграничная передача**

12. Требуется ли уведомление о трансграничной передаче, и по каким получателям: OpenAI (США), Anthropic (США), Google/Firebase (США), Apple (США), Telegram?
13. Достаточно ли согласия субъекта как основания трансграничной передачи в страну, не обеспечивающую адекватную защиту, или требуются дополнительные условия?
14. Меняет ли оценку тот факт, что данные передаются **не псевдонимизированными** (телефон, имя, флаги здоровья идут в промпте как есть)?
15. Если мы внедрим полную псевдонимизацию телефона и email — снимет ли это вопрос, с учётом того, что имена и медицинские флаги останутся в тексте?
16. Достаточно ли реализованной схемы «сначала запись в российскую БД, потом отправка за рубеж» для требования о локализации (ст. 18 ч. 5)?
17. Какой договор нужен с OpenAI и другими зарубежными сервисами? Подходит ли их типовой DPA?
18. Нужно ли раскрывать пользователю конкретного получателя и страну, или достаточно формулировки «зарубежные AI-сервисы»?

**Блок D. Сроки хранения и удаление**

19. Какие сроки хранения нужно установить нормативно для: текста диалогов, сырых webhook-payload'ов, фото еды, анкеты питания, зонированной памяти, аудита?
20. Какие данные мы **обязаны** хранить и не вправе удалить по запросу субъекта (записи и платежи — потребительское законодательство, бухучёт, 54-ФЗ)? Какой срок?
21. Приемлемо ли, что удаление по запросу не затрагивает переписку и сырые вебхуки? Если нет — какой минимальный объём должен удаляться?
22. Достаточно ли «стирания значений при сохранении технической строки» (erase-in-place) как исполнения права на удаление?
23. Что делать с резервными копиями: приемлемо ли окно 30–90 дней до фактического исчезновения данных из бэкапов?
24. Достаточно ли механизма «отозвать согласие = удалить аккаунт», или нужен гранулярный отзыв по целям?

**Блок E. Права субъекта**

25. Какой объём данных должен покрывать экспорт по запросу, чтобы считаться исполнением права на доступ? Нужен ли формат и срок ответа?
26. Обязаны ли мы реализовать право на исправление отдельного факта и право на возражение против обработки, и в какой форме?
27. Какой срок ответа на обращение субъекта установлен и что должно быть в ответе?
28. Нужно ли назначить ответственного за организацию обработки ПДн и оформить внутренние документы (приказ, перечень ПДн, регламент реагирования на инциденты)?

**Блок F. Несовершеннолетние**

29. Правомерна ли обработка данных лиц 14–18 лет в нашем сценарии? Какой механизм согласия законного представителя требуется и как его технически реализовать?
30. Что делать, если возраст неизвестен — обязаны ли мы его выяснять?

**Блок G. Безопасность и организационные требования**

31. Какой уровень защищённости ИСПДн применим при обработке специальных категорий в нашем масштабе? Нужна ли модель угроз и аттестация?
32. Обязательно ли шифрование данных о здоровье at-rest, или достаточно организационных мер?
33. Правомерно ли хранение кода подтверждения (OTP) в открытом виде и логирование телефона вместе с текстом SMS?
34. Какие требования к договору с хостинг-провайдером как с обработчиком? Обязательно ли размещение в РФ и подтверждение этого документально?
35. Что считать инцидентом, требующим уведомления Роскомнадзора, и в какой срок?

---

## 9. Что осталось UNKNOWN

| № | Вопрос | Почему не установлено |
|---|---|---|
| 1 | Фактическое значение `PII_TOKENIZER_ENABLED` и `OPENAI_PROXY` на продакшн-хосте пилота | Продакшн-конфигурация и переменные окружения не обследовались (аудит read-only по репозиториям) |
| 2 | Физическое местонахождение серверов, юрисдикция дата-центра, наличие договора с хостером как с обработчиком | В инфраструктурных файлах указаний на облако и регион нет |
| 3 | Регион и режим хранения данных в Sentry (облако или self-hosted) | Значение `SENTRY_DSN` — секрет, не выписывалось |
| 4 | Фактическая lifecycle-политика бакета с фото еды (заявленные 30 дней) | Настройка бакета вне кода |
| 5 | Наличие и содержание договоров/DPA с OpenAI, Anthropic, Google/Firebase, SMS-провайдером, хостером | Документы вне репозиториев |
| 6 | Подано ли уведомление в Роскомнадзор (об обработке и о трансграничной передаче) | Вне репозиториев |
| 7 | Актуальность утверждения «`BotUser.ayla_user_id` в проде никем не заполняется» на текущем HEAD | Требует проверки на живой базе |
| 8 | Фактический объём данных, уже накопленных в `WebhookJournal`, `Message`, `FoodScan` | Продакшн-база не обследовалась |
| 9 | Реально ли используется код интеграции YClients в пилоте | Вызовов в коде не найдено, но активация зависит от переменных окружения |
| 10 | Настройки резервного копирования и фактические сроки ротации бэкапов | Вне репозиториев |
| 11 | Проходит ли трафик к OpenAI/Anthropic через прокси в юрисдикции, отличной от США | Значение переменных прокси — секрет, не выписывалось |

---

## Приложение. Первоочередные технические действия (не юридические)

Для полноты картины — перечень технических пробелов, которые целесообразно закрыть независимо от вердикта юриста. Приоритет — мнение технического аудитора.

| Приоритет | Действие | Якорь |
|---|---|---|
| P0 | Обернуть маршрут глобального консьержа в `pii_context(conversation.id)` — либо в `bot:apps/channels/max/handler.py` перед вызовом, либо внутри `generate_concierge_reply` | `bot:apps/orchestrator/concierge.py:308-351`; `bot:apps/channels/max/handler.py:728` |
| P0 | Сделать отсутствие скоупа при наличии пользовательского контента ошибкой (fail-closed) вместо WARNING, либо явно проаудировать и пометить все легитимные фоновые вызовы | `bot:apps/llm/pii_protected_provider.py:152-170` |
| P0 | Убрать флаги здоровья из текста промпта в бекенде или псевдонимизировать их | `ayla:nutrition/services/ai_comment_service.py:193-202` |
| P1 | Ввести retention для `WebhookJournal` | `bot:apps/ingress/models.py:25` |
| P1 | Ввести retention для `Conversation`/`Message` | `bot:apps/conversations/tasks.py` |
| P1 | Реализовать TTL-sweep для `MemoryEntry` и обработчик `forget_all_requested_at` | `bot:apps/identity/tasks.py` |
| P1 | Реализовать серверные эндпоинты управления согласиями и убрать клиентские заглушки | `bot:apps/miniapp/src/lib/customer-profile.ts:235`, `:245` |
| P1 | Перенести согласие фото-сканера из `localStorage` на сервер | `bot:apps/miniapp/src/lib/food-scanner.ts:446` |
| P2 | Расширить экспорт до полного объёма и выровнять определение субъекта с удалением | `bot:apps/identity/services/privacy.py:303` |
| P2 | Зашифровать `UserPreferences.allergies` наравне с `MemoryEntry.content` | `bot:apps/identity/models.py:334` |
| P2 | Замаскировать телефон в Django-админке | `bot:apps/identity/admin.py:140`, `:153` |
| P2 | Убрать сырой текст пользователя из логов | `bot:apps/bookings/callbacks.py:170`, `:503` |
| P2 | Хешировать OTP в бекенде и убрать телефон/код из логов | `ayla:users/models.py:271`; `ayla:users/sms.py:37` |

---

*Документ подготовлен автоматизированным аудитом исходного кода. Реальные персональные данные и секреты в него не переносились. Все утверждения о наличии или отсутствии механизмов подтверждены якорями на строки кода и подлежат перепроверке перед использованием в юридически значимых документах.*
