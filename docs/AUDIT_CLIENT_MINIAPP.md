# Аудит клиентского мини-приложения Ayla

**Дата:** 04.09.2026
**Что проверялось:** клиентская поверхность MAX Mini App — какие экраны живы, что по канону, каким экранам хватает данных.
**Ничего не менялось ни в одном репозитории.** Единственный записанный файл — этот.

---

## 0. Поправка к вводным, без которой отчёт был бы неверным

Рабочий чекаут `C:\Users\user\PycharmProjects\Ayla\ai-bot-platform` стоит **не на `dev`**:

```
git rev-parse --abbrev-ref HEAD   ->  docs/ux-canon-reconciliation
git log -1 --date=iso             ->  fb1aab5  2026-08-25 19:36
git log --oneline -1 dev          ->  91ec947  (локальная dev, 25.08)
git log --oneline -1 origin/dev   ->  93ee628  (#1370)
```

Локальная `dev` отстала от `origin/dev` **на десять дней**. `git diff --stat fb1aab5 origin/dev -- apps/miniapp/src apps/miniapp_api apps/skills/welcome` — 82 файла, +8794 строки.

**Весь отчёт построен на `origin/dev`**, прочитанном через `git show origin/dev:<путь>`; рабочее дерево не трогалось. Где ниже стоит номер строки — это строка в `origin/dev`.

Первый проход разведки на устаревшем дереве дал неверную картину (например «анкеты нет, она в отдельном worktree» — на `origin/dev` она смержена). Это стоит держать в голове: **любой вывод про мини-апп, сделанный в этом клоне без явной сверки с `origin/dev`, следует считать устаревшим.**

Оговорка по спецификациям: `docs/screens/*` между `fb1aab5` и `origin/dev` изменились только в двух файлах (`admin-surface-spec.md` — новый, `customer-food-scanner-flow.md` +407 строк). Остальные клиентские спеки идентичны.

---

## 1. Таблица по экранам

**32 клиентских экрана** (не 31 — на `origin/dev` добавился `CustomerEntryScreen`).
Все пути — от `C:\Users\user\PycharmProjects\Ayla\ai-bot-platform\apps\miniapp\src\screens\`.
Маршруты — `apps\miniapp\src\App.tsx`, функция `CustomerRoutes()` (строки 1116–1262 на `origin/dev`).

| # | Экран | Маршрут | Живой | Поколение | Спека | Расходится | Данные | Чем подтверждено |
|---|---|---|---|---|---|---|---|---|
| 1 | CustomerEntryScreen | `/` | **да, корень** | новое | BOT-001 §24 A-1 | нет | реальные — `GET /decision-context` | `App.tsx:1133`; `CustomerEntryScreen.tsx:70` |
| 2 | GoalSelectScreen | `/customer/goal-select` + монтируется на `/` | да | новое | BOT-001 §24, C-1..C-5 | **частично**, см. §3.1 | реальные — `/decision-context`, `/goals/select` | `lib/customer-goals.ts:68,83`; `App.tsx:1163`; `GoalInviteCard.tsx:130` |
| 3 | HelloScreen | fallback `CustomerEntryScreen` + catch-all `*` | да | старое | нет спеки | **да**, см. §3.2 | реальные — `POST /auth/verify` | `App.tsx:1260`; `CustomerEntryScreen.tsx:95`; `HelloScreen.tsx:172,176` |
| 4 | CatalogScreen | `/catalog` | да | старое | нет спеки (спека F1 — про `CustomerCatalogScreen`) | — | реальные — `GET /services` | `lib/api.ts:126`; входы: `ServiceDetailScreen.tsx:89,132` (**с основного пути**), `HelloScreen.tsx:172`, `MyVisitsScreen.tsx:90`, `MasterPickerScreen.tsx:45,86`, `RescheduleScreen.tsx:186` |
| 5 | CustomerCatalogScreen | `/customer/catalog` | да | новое | `customer-booking-flow.md` §3 (F1), `customer-catalog-empty-states-spec.md` | **да**, см. §3.3 | реальные — `/services`, `/masters`, `POST /recommendations` | `lib/customer-booking.ts:73`; вход — слаг бота `open_catalog`, нижняя навигация |
| 6 | ServiceDetailScreen | `/catalog/:serviceId` | да, **общий узел обеих веток** | старое | нет спеки | — | реальные — `GET /services/:id` + `is_bookable` (DRF-1164) | `ServiceDetailScreen.tsx:55,107`; входы: `CatalogScreen.tsx:72`, `CustomerCatalogScreen.tsx:158,182`, `CustomerWellnessDashboardScreen.tsx:700` |
| 7 | MasterPickerScreen | `/book/master` | да | старое | нет спеки | — | реальные — `GET /masters?service_id` | `ServiceDetailScreen.tsx:61` |
| 8 | BookingWhenScreen | `/book/when` | да | старое | нет спеки | — | реальные — `GET /slots` | `MasterPickerScreen.tsx:54`; уходит в **новый** confirm: `BookingWhenScreen.tsx:91` |
| 9 | CustomerMasterDetailScreen | `/customer/masters/:masterId` | да | новое | `customer-booking-flow.md` §4 (F2) | не установил | реальные — `GET /masters/:id` | `CustomerCatalogScreen.tsx:198` |
| 10 | CustomerSlotsScreen | `/customer/masters/:masterId/slots` | да | новое | `customer-booking-flow.md` §5 (F3) | не установил | реальные — `GET /slots` | `CustomerMasterDetailScreen.tsx:77` |
| 11 | BookingConfirmScreen | `/book/confirm` | **НЕТ — мёртвый** | старое | нет спеки | — | реальные (недостижимы) | ни один `navigate` в репозитории не ведёт на `/book/confirm` |
| 12 | CustomerBookingConfirmScreen | `/customer/booking/confirm` | да, **вход обеих веток** | новое | `customer-booking-flow.md` §6, `customer-booking-confirm-registration-spec.md` | **да**, см. §3.4 | реальные — `POST /bookings`, `POST /me/payments/` | `BookingWhenScreen.tsx:91`; `CustomerSlotsScreen.tsx:172` |
| 13 | BookingSuccessScreen | `/book/success/:bookingId` | **НЕТ — мёртвый** | старое | нет спеки | — | из `location.state` | единственный вход — `BookingConfirmScreen.tsx:50`, а он сам мёртв |
| 14 | CustomerBookingSuccessScreen | `/customer/booking/success/:bookingId` | да | новое | `customer-booking-flow.md` §7 (F5) | нет | из `location.state` | `CustomerBookingConfirmScreen.tsx:244` |
| 15 | CustomerRecordsScreen | `/customer/main` **и** `/customer/records` | да, **домашний экран** | новое | `customer-records-flow.md` §3 (R1) | нет | реальные — `GET /bookings/list` | `App.tsx:1152,1188`; `lib/customer-records.ts` |
| 16 | MyVisitsScreen | `/my-visits` | да | старое | нет спеки | — | реальные — `GET /bookings/list` | входы: `HelloScreen.tsx:176`, `FeedbackScreen.tsx:69` |
| 17 | CustomerBookingDetailScreen | `/customer/records/:bookingId` | да | новое | `customer-records-flow.md` §5 (R3) | **да**, см. §3.5 | реальные — `GET /bookings/:id` | `CustomerRecordsScreen.tsx:229,245` |
| 18 | MyVisitDetailScreen | `/my-visits/:bookingId` | да | старое | нет спеки | — | реальные, **но беднее нового**, см. §2.3 | входы: `MyVisitsScreen.tsx:106,180`, `RescheduleScreen.tsx:133` |
| 19 | RescheduleScreen | `/my-visits/:bookingId/reschedule` | да, **единственный, общий для обеих веток** | старое пространство имён | `customer-cancellation-reschedule-flow.md` R1-R4 | **да**, см. §3.5 | реальные — `/bookings/:id/reschedule(+/confirm)` | `MyVisitDetailScreen.tsx:143`; `CustomerBookingDetailScreen.tsx:272`; `CustomerRecordsScreen.tsx:237` |
| 20 | FeedbackScreen | `/feedback/:bookingId` | да, общий | старое | спеки нет (records-flow выносит в другой документ) | **да**, см. §2.2 | реальные — `POST /bookings/:id/feedback` | `MyVisitsScreen.tsx:192`; `CustomerRecordsScreen.tsx:258`; `CustomerBookingDetailScreen.tsx:295` |
| 21 | CustomerProfileScreen | `/customer/profile` | да | новое | `customer-profile-flow.md` R1-R6 | нет (спека сама велит deferred) | **ЧАСТИЧНО ЗАГЛУШКА**, см. §4.1 | `lib/customer-profile.ts:215-273`; `CustomerProfileScreen.tsx:129-133` |
| 22 | ProfileScreen | `/me` | **НЕТ — мёртвый** | старое | нет спеки | — | **реальные и полные** — `GET/PATCH /me`, `POST /me/delete` | ни один `navigate("/me")` в репозитории; `open_profile` ведёт на `/customer/profile` (`lib/max-sdk.ts`) |
| 23 | CustomerNotificationSettingsScreen | `/customer/notification-settings` | да | новое | только как deep-link из profile-flow | нет | реальные — `GET/PATCH /me` | `CustomerNotificationSettingsScreen.tsx:30`; вход `CustomerProfileScreen.tsx:531` |
| 24 | CustomerCardsScreen | `/customer/cards` | да | новое | **спеки нет** | — | реальные — `/me/cards/*` (прокси в Ayla, банковские карты) | `lib/cards.ts:38,48,59`; вход `CustomerProfileScreen.tsx:522` |
| 25 | CustomerWellnessDashboardScreen | `/customer/wellness` | **в проде НЕТ** — подменяется заглушкой | новое | `customer-main-wellness-dashboard.md` | **да**, см. §4.2 | **данные РЕАЛЬНЫЕ**, но экран закрыт гейтом | `CustomerWellnessDashboardScreen.tsx:110`; `lib/feature-flags.ts:23`; `lib/customer-wellness.ts:316,331,455` |
| 26 | PilotComingSoonScreen | не маршрут — подмена №25 | новое | нет спеки | — | статичный текст by design | `PilotComingSoonScreen.tsx:29-43` |
| 27 | FoodScannerCaptureScreen | `/customer/food-scanner/capture` | **практически нет**, см. §5.2 | новое | `customer-food-scanner-flow.md` F1 | — | локально, сети не трогает | вход с №25 (за гейтом) + снятый слаг бота |
| 28 | FoodScannerProcessingScreen | `/customer/food-scanner/processing` | практически нет | новое | спека F2 | — | **ЗАГЛУШКА, в проде бросает** | `lib/food-scanner.ts:180-189,345` |
| 29 | FoodScannerResultScreen | `/customer/food-scanner/result` | практически нет | новое | спека F3 | — | ЗАГЛУШКА | `lib/food-scanner.ts:433` |
| 30 | FoodScannerSavedScreen | `/customer/food-scanner/saved` | практически нет | новое | спека F4 | — | ЗАГЛУШКА | `lib/food-scanner.ts:401` |
| 31 | FoodScannerDiaryScreen | `/customer/food-scanner/diary` | практически нет | новое | только концептуально | — | ЗАГЛУШКА | `lib/food-scanner.ts:401` |
| 32 | FoodScannerManualScreen | `/customer/food-scanner/manual` | практически нет | новое | спека §10 | — | ЗАГЛУШКА | `lib/food-scanner.ts:370-373` |

---

## 2. Два поколения — какое настоящее

### 2.1 Развилка проходит по одному экрану

На `origin/dev` корень `/` больше не `HelloScreen`, а `CustomerEntryScreen` (`App.tsx:1133`). Он читает `decision-context` и делает ровно три вещи (`CustomerEntryScreen.tsx:70-78`):

```
missing непуст   -> монтирует GoalSelectScreen прямо на корне   (новое поколение)
missing пуст     -> Navigate to /customer/main                  (новое поколение)
ручка упала      -> рисует HelloScreen                          (СТАРОЕ поколение)
```

И отдельно `<Route path="*" element={<HelloScreen />} />` (`App.tsx:1260`) — любой неизвестный адрес.

**`HelloScreen` — единственная дверь в старое поколение, и она открыта.** Его кнопки:

```
HelloScreen.tsx:172   «Записаться»   -> /catalog          старое
HelloScreen.tsx:176   «Мои записи»   -> /my-visits        старое
HelloScreen.tsx:179   «Профиль»      -> /customer/profile новое
```

Все три ведут в разные поколения, и одна кнопка из трёх — в новое.

**Ответ на вопрос «есть ли путь в старое поколение сегодня»: да, два.** Отказ `GET /decision-context` (сеть, 5xx, таймаут прокси в Ayla) и любой промах по адресу. Оба ведут на экран, который дальше раздаёт старую ветку.

### 2.2 Поколения перепутаны на четырёх переходах

Это не «две параллельные ветки», а один граф, сшитый крест-накрест:

| Откуда | Строка | Куда | Пересечение |
|---|---|---|---|
| `BookingWhenScreen` (старое F3) | `BookingWhenScreen.tsx:91` | `/customer/booking/confirm` (**новое** F4) | старая воронка вливается в новую |
| `CustomerCatalogScreen` (новое F1) | `CustomerCatalogScreen.tsx:158,182` | `/catalog/:id` (**старое**) → `/book/master` → `/book/when` | новая воронка уходит в старую и возвращается |
| `FeedbackScreen`, закрытие после оценки | `FeedbackScreen.tsx:69` | `/my-visits` (**старый** список) | человек, оценивший визит из новых «Записей», приземляется в старый список |
| `RescheduleScreen`, после успешного переноса | `RescheduleScreen.tsx:133` | `/my-visits/:id` (**старая** карточка) | то же самое после переноса |
| `ServiceDetailScreen`, «Другие услуги» при `!is_bookable` и при 404 | `ServiceDetailScreen.tsx:132`, `:89` | `/catalog` (**старый** каталог) | человек, пришедший из нового каталога и ткнувший в услугу без исполнителя, уходит в старый каталог |
| `RescheduleScreen`, orphan-состояние | `RescheduleScreen.tsx:186` | `/catalog` (**старый** каталог) | то же |

Один такой переход **уже чинили**: `CustomerBookingSuccessScreen` вёл на `/my-visits/:id` и был переведён на `/customer/records/:id` — с прямым объяснением в докстринге (`CustomerBookingSuccessScreen.tsx:12-19`): «dropping a customer who had just booked onto the old surface». Четыре оставшихся перехода (`FeedbackScreen:69`, `RescheduleScreen:133,186`, `ServiceDetailScreen:89,132`) — **тот же дефект, той же природы, не починенный**.

**Отсюда следует важное:** старая ветка достижима не только через `HelloScreen`. Человек, ни разу не видевший `HelloScreen`, попадает в старый каталог через `ServiceDetailScreen` (услуга без исполнителя — обычное дело, гейт `is_bookable` для того и заведён DRF-1164), а в старый список записей — через `FeedbackScreen`, то есть **сразу после того, как поставил оценку**. Это не редкие ветки: обе на основном пути.

### 2.3 Расхождение в поведении между парами — есть, и оно измеримо

`/my-visits/:id` (`MyVisitDetailScreen`) против `/customer/records/:id` (`CustomerBookingDetailScreen`) — читают **одну и ту же** ручку `GET /bookings/<id>`, но рисуют разное:

| Что | старая карточка | новая карточка |
|---|---|---|
| Статус оплаты | нет | `CustomerBookingDetailScreen.tsx:225` `<PaymentStatusBadge>` |
| Сумма оплаты | нет | `:244-247` |
| Показ выставленной оценки | нет | `:250-253` |
| Кнопка «Оценить визит» (`can_rate`) | **нет** | `:290-297` |
| Отмена с окном отмены | есть | есть |
| Перенос | есть | есть |

То есть человек, попавший на старую карточку (после переноса — `RescheduleScreen.tsx:133`), **теряет кнопку «Оценить визит»** и не видит оплату. Ровно тот класс дефекта, что ловили вчера: одна ветка знает про поле, другая нет.

Остальные пары:

- `/catalog` против `/customer/catalog`: старый — плоский список услуг, `ScreenLayout title="Услуги студии"`. Новый — поиск, «Ayla подобрала», секция «Мастера». Обе ведут в один `ServiceDetailScreen`.
- `/my-visits` против `/customer/records`: старый — чипы «Предстоящие/История». Новый — вкладки со счётчиками, `GoalInviteCard`, пустое состояние, офлайн-баннер, нижняя навигация.
- `/me` против `/customer/profile`: **инверсия** — мёртвый старый работает на настоящих данных, живой новый на заглушках. См. §4.1.
- `/book/confirm` против `/customer/booking/confirm`: старый мёртв.
- `/book/success/:id` против `/customer/booking/success/:id`: старый мёртв.
- `/book/when` против `/customer/masters/:id/slots`: **оба живы**, каждый в своей воронке.

### 2.4 Обоснование, на котором держится старое поколение, больше не верно

Комментарий в `App.tsx:1141-1146`:

> «The legacy routes stay reachable for deep-links from bot DMs and reschedule flows.»

Проверено grep'ом по `origin/dev`:

```
git grep -n -E '/my-visits|/catalog|"/me"|/book/' origin/dev -- apps/notifications apps/skills apps/channels apps/booking
```

Единственное попадание — `apps/skills/welcome/skill.py:172` `"open_catalog": "customer/catalog"`, то есть **новое** пространство имён. **Ни один продюсер в этом репозитории не выдаёт ссылок на `/my-visits`, `/catalog`, `/me`, `/book/*`.** Слаги бота переведены на `/customer/*` ещё DRF-1326 (`lib/max-sdk.ts`, комментарий в `_ROUTE_MAP`).

Из двух названных причин осталась одна: **reschedule** — `RescheduleScreen` действительно единственный экран переноса и живёт в старом пространстве имён, им пользуются обе ветки.

### 2.5 Что с этим делать

Решения владельца по двум поколениям **нет**. Проверено: в `docs\OPEN_DECISIONS.md` вопроса о дублировании клиентских экранов нет ни в открытых, ни в закрытых. **Вопрос ему не задавался.**

Порядок работ, вытекающий из фактов выше (не решение — предложение):

1. **Сначала два перехода**, а не удаление экранов: `FeedbackScreen.tsx:69` и `RescheduleScreen.tsx:133` → на `/customer/records...`. Это чинит потерю кнопки «Оценить визит», и это одна строка на каждый.
2. **`HelloScreen`** — заменить три кнопки на `/customer/catalog`, `/customer/records`, `/customer/profile`. После этого в старое поколение не ведёт ни одна кнопка.
3. **`RescheduleScreen`** — перевесить на `/customer/records/:id/reschedule`, оставив старый маршрут алиасом. Это снимает последнее живое основание держать старое пространство имён.
4. Только после 1–3 — сносить `BookingConfirmScreen`, `BookingSuccessScreen`, `ProfileScreen`, `CatalogScreen`, `MyVisitsScreen`, `MyVisitDetailScreen`, `MasterPickerScreen`, `BookingWhenScreen`.

Оговорка честно: `ServiceDetailScreen` — **не** старое поколение в смысле «лишнее». Его докстринг (`ServiceDetailScreen.tsx:107-113`) прямо называет его «the ONE door into the booking flow for a service», и в него ведут обе ветки плюс дашборд. Его надо не сносить, а переносить.

---

## 3. Что по канону, а что нет

Свежие решения владельца учтены: расхождение, которое владелец уже отменил, ниже не считается расхождением.

**Что владелец уже закрыл и в коде выполнено:**

- **§21-ter, фиолетовая палитра.** Выполнено на `origin/dev`: `apps\miniapp\src\styles\tokens.css` — `--c-accent: #4452ff` («← #5B68FF, L− до 4.5:1»), полный вывод с борда DRF-1181 в шапке файла. На локальном чекауте здесь всё ещё терракота `#c47b6c` — ещё одна причина не верить этому клону.
- **§22, «нет displayable WHY → нет блока „Ayla подобрала"».** Выполнено: `CustomerCatalogScreen.tsx:144-167` и `CustomerWellnessDashboardScreen.tsx:678-700` — секция рендерится только пока `picks` непуст, а `getCatalogBrowse` кладёт туда только объяснённые. Спека `customer-booking-flow.md` §2.1 требует reasoning_text — она этим решением и перекрыта, расхождением не считается.
- **OD §27, «кнопку сканировать еду снять».** Выполнено: `apps\skills\welcome\skill.py` — в `_s5_first_action_buttons()` кнопки «📸 Сфотографировать еду» больше нет ни в `web_app`, ни в `url` ветке; маршрут и слаг оставлены намеренно, регрессию сторожит `tests/test_skill.py::TestFoodScanButtonRemoved`.
- **BOT-001 §13 / §19 non-goal #2 (запрет анкеты).** Поправлен самим каноном: `BOT-001 First Contact Specification.md:678-753` — поправка A-1, §24. Расхождением не считается.

Ниже — то, что расхождением остаётся.

### 3.1 Условие C-2 поправки A-1 держится сервером, а не экраном — экран может стать воротами молча

Канон, `BOT-001:707-712`:

> **C-2 — Not a gate.** … Naming an already-known service — as free text or by picking an offered option — is available on the surface itself, alongside the questions, and reaches service selection with **zero** questions answered. … **Free-text input remains available and unblocked** (AC-1.2, AC-1.3).

Код, `GoalSelectScreen.tsx:357`:

```tsx
{formulateOwnLabel && (
  <section aria-labelledby="goal-select-own">
    …textarea…
```

где `formulateOwnLabel = intentLabel("formulate_own")` (`:224`), то есть **поле свободного ввода рисуется только если сервер прислал намерение `formulate_own`**. Пришлёт документ без него — поле исчезнет, и первый экран станет ровно тем, что C-2 запрещает: воротами из вопросов без выхода. Экран этого не заметит и ошибку не покажет.

Тест `GoalSelectScreen.anketa.test.tsx` (блок `describe("анкета — НЕ ворота (условие C-2)")`, `:251`) это условие проверяет — но на документе, который `formulate_own` **содержит** (фикстура `:42`). Случая «сервер прислал документ без `formulate_own`» нет ни в коде, ни в тестах. **То есть C-2 сегодня — обещание сервера, ничем не подстрахованное на клиенте.**

То же и с выходом: «дальше» рисуется только когда сервер прислал `next`, и только для одного известного идентификатора — `NEXT_ROUTES = { browse_catalog: "/customer/catalog" }` (`GoalSelectScreen.tsx:222`). Пришлёт сервер `next` с другим id — кнопки не будет.

**Это не обвинение экрана: C-1 требует, чтобы решения принимал сервер, и экран правильно их не подменяет.** Но условие C-2 — про поверхность, а не про документ, и сегодня его ничто не удерживает на клиенте. Проверки «пришёл документ без `formulate_own` — это нарушение A-1» нет нигде.

Чего **не установил**: доходит ли на самом деле человек, назвавший услугу словами, до подбора. `goal_text` уходит на сервер (`GoalSelectScreen.tsx:236`), дальше решает Ayla / `beautygo_backend` PR #285 — этот путь я не читал.

### 3.2 `HelloScreen` — экран без спеки, стоящий на двух входах

`HelloScreen` ловит и отказ `decision-context`, и любой промах по адресу (`App.tsx:1260`). Спецификации у него нет ни одной — `customer-reminders-voice.md:75` упоминает файл только чтобы пометить «❌ NOT customer scope».

При этом он единственный, кто раздаёт старое поколение (§2.1), и его текст — «Помогу записаться в студию {tenant.name}» (`HelloScreen.tsx:174`) — это позиционирование «календаря», а не wellness-ассистента. Канон, `BOT-001` §4 P3, требует контекстного приветствия; экран, который рисуется в двух совершенно разных ситуациях (сбой ручки и опечатка в ссылке) одним и тем же текстом, этому не отвечает.

### 3.3 `customer-catalog-empty-states-spec.md` не реализована ни в одной части, кроме FIX'а текста

Спека (`docs\screens\customer-catalog-empty-states-spec.md`), статус «решения по пилоту зафиксированы, 2026-07-02 (founder)»:

- §2 требует контракт `empty_reason ∈ {search_no_match, region_empty, booking_unavailable}` — «добавить сейчас… ради расширяемого контракта».
  **Проверено:** `git grep -n 'empty_reason' origin/dev -- apps/miniapp/src apps/miniapp_api` → **ноль попаданий**. Нет ни на сервере, ни на клиенте.
- §1 требует три контекстных состояния с разными текстами и CTA.
  **В коде** — одно общее (`CustomerCatalogScreen.tsx:137-142`): «Пока здесь пусто. Загляни позже — покажу варианты.»
- §4 требовал убрать «Сегодня ничего безопасного не нашла».
  **Выполнено** — строки в репозитории нет.

Попутная находка того же места, уже не про спеку: условие показа пустого состояния — `allEmpty = visibleServices.length === 0 && masters.length === 0` (`CustomerCatalogScreen.tsx:121`). Поиск, не давший ни одной услуги, при непустом списке мастеров **не показывает ничего** — ни результатов, ни сообщения. Спека называет этот случай `search_no_match` и требует CTA «Посмотреть все услуги».

### 3.4 `customer-booking-confirm-registration-spec.md`: два поля снимка намерения не заведены

Спека §2, решение владельца 02.07: семантическое ядро pending-intent — шесть полей, из них **`tenant_id` и `entry_point` помечены «добавить»**, и прямо сказано «добавить и в клиент `PendingBookingIntent`, и в сервер `ServerPendingBookingIntent`».

**Проверено на `origin/dev`:**
`apps\miniapp\src\lib\pending-booking-intent.ts:28-46` — интерфейс содержит `master_id, service_id, slot_iso, price_rub?, note?, loyalty_choice?, service_name?, master_name?`. **`tenant_id` и `entry_point` отсутствуют.** В `lib/api.ts` (`ServerPendingBookingIntent`) их тоже нет.

Что при этом **выполнено**: запрет `0` как sentinel для цены (`pending-booking-intent.ts:33-39` — прямой комментарий про P0-контракт), отсутствие `coupon`/`step`/`goal`.

### 3.5 Спеки отмены и переноса требуют ручек, которых нет

`docs\screens\customer-cancellation-reschedule-flow.md:815-819` перечисляет как контракт:

```
GET  /api/v1/customer/bookings/{id}/cancel_policy_preview
GET  /api/v1/customer/bookings/{id}/reschedule_options?date=...
POST /api/v1/customer/notifications/salon_side_cancel/{booking_id}/respond
```

Ни одной из трёх нет в `apps\miniapp_api\urls.py` на `origin/dev`. Спека требует показывать явную сумму штрафа и сроки возврата до подтверждения отмены — показать нечего.

Код это признаёт честно: докстринг `CustomerBookingDetailScreen.tsx:6-10` прямо говорит, что заглушка рисовала «cancellation-policy text / refund amounts», и что «NONE of those fields exist in the real `GET /bookings/<id>` payload». То есть **расхождение не в коде, а между спекой и тем, что построено**: экран честен, спека не реализована.

### 3.6 `customer-food-scanner-flow.md` — спека без единой ручки

`docs\screens\customer-food-scanner-flow.md` §12 «Backend mapping (final)» называет `PATCH /api/v1/customer/me/food-photo-consent` и семейство nutrition-ручек. В `apps\miniapp_api\urls.py` на `origin/dev` **нет ни одного пути `customer/food/*`**. Подробнее — §4.3.

### 3.7 Что каноном не покрыто вовсе

Из 32 экранов **спеки нет** у: `HelloScreen`, `CatalogScreen`, `ServiceDetailScreen`, `MasterPickerScreen`, `BookingWhenScreen`, `BookingConfirmScreen`, `BookingSuccessScreen`, `MyVisitsScreen`, `MyVisitDetailScreen`, `ProfileScreen`, `FeedbackScreen`, `CustomerCardsScreen`, `PilotComingSoonScreen`, `FoodScannerDiaryScreen`.

Из них двенадцать — старое поколение, и их непокрытость логична. **Но `FeedbackScreen` и `CustomerCardsScreen` живы, в новом графе, и не описаны нигде.** `CustomerCardsScreen` при этом работает с привязкой банковских карт — поверхность, у которой отсутствие спеки стоит дороже прочих.

### 3.8 Про палитру канон молчит — и это стоит знать

`BOT-001` §2.2 прямо исключает «Detailed visual design of Mini App screens» из своей области. `Ayla Conversation Product Map` относит layout и channel UX к UX-канону, не к продуктовому. То есть **§21-ter — единственный источник палитры**, и сверять фиолетовую перекраску с BOT-001 не с чем. Это не пробел работы, это устройство канона.

---

## 4. Каким экранам хватает данных

### 4.1 Худший случай: живой профиль на заглушках, мёртвый профиль на настоящих данных

**`/customer/profile` — экран, на который ведёт кнопка бота «👤 Профиль» и кнопка «Профиль» с `HelloScreen`.**

`apps\miniapp\src\lib\customer-profile.ts` — модуль заглушек. `guardProd()` (`:215-224`) бросает `StubNotWiredError` в проде, и его вызывают **все пять** функций: `fetchMe:227`, `fetchConsents:236`, `setMarketingConsent:248`, `fetchProactivePrefs:258`, `setProactiveOptOut:269`.

Экран это обходит (`CustomerProfileScreen.tsx:129-133`):

```tsx
if (!STUB_SURFACES_ENABLED) {
  setStatus({ kind: "ready", me: null, consents: null, proactive: null });
  return;
}
```

**Что видит человек в проде.** Секции условны — `{status.me && …}` (`:356`), `{status.consents && …}` (`:359`), `{status.proactive && …}` (`:462`). При `null` они просто не рисуются. То есть на экране «Профиль» **нет имени, нет ни одной строки согласий из четырёх, нет тумблера подсказок Ayla**. Остаются: заголовок «Профиль», строка «Питание и здоровье» (реальная, DRF-1453), карточка «Что Ayla помнит — скоро», кнопки «Запросить данные» / «Удалить аккаунт» (реальные, 152-ФЗ), «Мои карты», «Уведомления».

**А `/me` — `ProfileScreen`, старое поколение — работает на настоящих данных:** `fetchProfile` → `GET /me`, `updateProfile` → `PATCH /me`, `deleteAccount` → `POST /me/delete` (`lib/api.ts:316,321,324`). Имя, четыре тумблера предпочтений, день рождения, двухшаговое удаление.

**И на `/me` не ведёт ни одна кнопка и ни один слаг.**

Это и есть инверсия: настоящий профиль отключён от навигации, заглушечный подключён. Заметить это беглым просмотром нельзя — в проде заглушечный экран не врёт, он просто молча короче.

Оговорка: спека `customer-profile-flow.md` §0 сама переводит часть профиля в deferred, так что **спеку это не нарушает**. Нарушает оно ожидание владельца, а не документ.

### 4.2 Второй по цене: полностью подключённый дашборд, закрытый устаревшим гейтом

`CustomerWellnessDashboardScreen.tsx:110`:

```tsx
if (!STUB_SURFACES_ENABLED) {
  return <PilotComingSoonScreen surface="home" />;
}
```

`STUB_SURFACES_ENABLED = import.meta.env.DEV` (`lib/feature-flags.ts:23`). В проде — всегда заглушка «Скоро здесь будет твой день».

**Но заглушек за гейтом больше нет.** На `origin/dev` `lib/customer-wellness.ts` ходит в реальные ручки:

```
:316  request<WellnessToday>("/wellness/today")
:331  request<RecentActivity>("/recent-activity")
:455  request<WaterLogResult>("/wellness/water", POST)
:476  request<void>(`/wellness/water/${id}`, DELETE)
```

Заглушки остались только за явным `?stub=`, и `pickStubVariant` возвращает `null` вне DEV (`:182`).

Более того — **условие, которым владелец сам обусловил снятие гейта, выполнено.** `docs\OPEN_DECISIONS.md` §37, поправка 29.08: «Гейт снимать только после неё» — после перевода «ближайшей записи» с `BookingRequest` на `RemoteBookingProxy`. На `origin/dev` это сделано: `apps\miniapp_api\views.py` — `_recent_activity_from_mirror` (:2601) и `_recent_activity_from_local` (:2644), выбор по `BOOKING_VIA_AYLA_REST` (DRF-1349).

**Довод «за гейтом выдумка» отпал, и препятствие, названное владельцем, снято. Гейт остался.**

Комментарий у самого гейта при этом устарел и вводит в заблуждение: «the wellness dashboard is a stub surface» (`:105-109`) — она больше не stub. То же в `lib/feature-flags.ts:16-21`, где перечислены как загейченные `/customer/catalog` и секции профиля; на деле `CustomerCatalogScreen` не загейчен вовсе (`App.tsx:1168` монтирует его напрямую), а профиль гейтит себя иначе.

**Что за гейтом всё же не готово** — назвать честно, иначе снятие гейта даст правдоподобную ложь, которой владелец опасался:

- `wellness/today.active_goals` — жёстко `[]` (`views.py:2355`, «Layer-2 Goals system has no REST endpoint yet»). Дашборд отрисует «Цель не выбрана» (`CustomerWellnessDashboardScreen.tsx:890`) **человеку, который только что прошёл анкету и цель выбрал**. Это не заглушка, это противоречие между двумя поверхностями.
- `recent-activity.weekly_progress` — нули захардкожены (`views.py:2807-2811`). Блок «Прогресс недели» спасён случайно: он гейтится на `active_days_count >= 3` (`:327`), а там ноль, поэтому блок не рисуется никогда. Уберут гейт — начнёт врать.
- Сна и шагов в payload нет вовсе — но это и есть решение владельца §20 от 25.08 («сон и шаги в пилот НЕ берём»), расхождением не является.

### 4.3 Сканер еды: шесть экранов, ноль ручек

`apps\miniapp\src\lib\food-scanner.ts:180-189` — `guardProd()`, бросающий `StubNotWiredError` в проде. Его зовут все четыре сетевые функции: `scanPhoto:345`, `logMeal:373`, `fetchDailySummary:401`, `fetchHealthFlags:433`.

`git grep 'customer/food' origin/dev -- apps/miniapp_api/urls.py` → **ноль**. Ручек `POST /customer/food/scan`, `POST /customer/food/log`, `GET /customer/food/daily` не существует.

Разница со случаями выше — здесь **нет верхнеуровневой заглушки**. Каждый экран ловит исключение своим обычным `catch` и рисует обычную ошибку. Человек, дошедший сюда, увидит не «раздел в работе», а «Сервис недоступен» — то есть поломку, а не незавершённость. Именно это и было основанием решения владельца §27 снять кнопку из бота (DRF-1417).

Кнопка снята. **Маршруты, слаг `open_food_scan` в `MINIAPP_ROUTES` и `_ROUTE_MAP` оставлены намеренно** — значит старое сообщение бота в истории переписки человека по-прежнему ведёт сюда. Это осознанный остаток, а не пропуск: снос маршрута пришлось бы откатывать.

### 4.4 Кнопка бота «💧 + стакан воды» ведёт на заглушку

`apps\skills\welcome\skill.py` — в стартовой сетке остаётся `{"label": "💧 + стакан воды", "callback": "open_water_add_250"}`, слаг ведёт на `customer/wellness` (`MINIAPP_ROUTES:182`), а `/customer/wellness` в проде — `PilotComingSoonScreen`.

Комментарий в самом файле это признаёт: «`open_water_add_250` остаётся на месте намеренно: тот же `guardProd` стоит и за ним, но решение по воде владельцем ещё не принято, и эта правка его не предвосхищает».

**Комментарий уже неточен.** `guardProd` за водой больше не стоит — `postWaterLog`/`undoWaterLog` ходят в реальные `POST/DELETE /wellness/water` (`lib/customer-wellness.ts:455,476`), а ручки существуют (`apps\miniapp_api\views.py:2377,2496`, DRF-1402, с проброской `X-Idempotency-Key` для офлайн-очереди). Мешает **только гейт §4.2**.

То есть: контур воды достроен целиком, снизу доверху, и не работает по одной строке `feature-flags.ts:23`.

### 4.5 Где данных хватает — без оговорок

Каталог, услуга, мастера, слоты, создание записи, список записей, карточка записи, отмена с окном отмены, перенос, оценка визита, настройки уведомлений, банковские карты, согласие на медданные, цель/анкета. Все ручки существуют и отдают то, что экран рисует. Инвентарь — `apps\miniapp_api\urls.py` + `views.py` на `origin/dev`.

Два условных места:

- `POST /recommendations` отдаёт `{service_id, score}` без объяснений, поэтому «Ayla подобрала» **не показывается никогда**. Это выполнение решения владельца §22, а не пробел.
- `bookings/{id}/reschedule` и `submit_feedback` при `BOOKING_VIA_AYLA_REST=True` теперь честно отвечают 409 `invalid_state` вместо прежнего «booking not found» (`views.py`, DRF-1349). То есть на Ayla-пути перенос и оценка **не работают**, и это теперь видно, а не спрятано.

---

## 5. Что мёртвое

### 5.1 Мертво окончательно — три экрана

| Экран | Маршрут | Почему |
|---|---|---|
| `BookingConfirmScreen` | `/book/confirm` | ни один `navigate` в репозитории не ведёт на этот адрес |
| `BookingSuccessScreen` | `/book/success/:bookingId` | единственный вход — `BookingConfirmScreen.tsx:50`, а он сам мёртв |
| `ProfileScreen` | `/me` | ни один `navigate("/me")`; слаг `open_profile` переведён на `/customer/profile` (DRF-1326) |

Метод: полный grep по `origin/dev` всех `navigate("…")` и `navigate(\`…\`)` в `apps/miniapp/src/screens` и `.../components`, минус admin/master/solo. Ни одного попадания на эти три адреса.

`ProfileScreen` — самая дорогая потеря из трёх: это единственный экран, где человек может **отредактировать своё имя и предпочтения на настоящих данных** (§4.1).

### 5.2 Практически мертво — шесть экранов сканера еды

`/customer/food-scanner/*` — маршруты смонтированы, но:

- вход с дашборда (`CustomerWellnessDashboardScreen.tsx:231`) закрыт гейтом в проде;
- кнопка бота снята решением владельца §27;
- остаётся только старое сообщение в истории переписки со слагом `open_food_scan`.

То есть в проде туда попадает только человек, пролиставший переписку назад, — и попадает на ошибку (§4.3).

### 5.3 Живо, но недостижимо в проде — один экран

`CustomerWellnessDashboardScreen` — гейт §4.2. Формально живой (маршрут, входы из четырёх нижних навигаций, слаг бота), фактически в проде его не видел никто.

### 5.4 Что мёртвым НЕ является, хотя выглядит

- **`ServiceDetailScreen`** — общий узел обеих веток и трёх входов, докстринг называет его «the ONE door into the booking flow for a service» (`:107`).
- **`RescheduleScreen`** — единственный экран переноса, им пользуются обе ветки.
- **`GoalSelectScreen`** — до 30.08 в него вела только стартовая сетка бота; теперь есть `GoalInviteCard.tsx:130` с домашнего экрана и монтирование прямо на корне (`CustomerEntryScreen.tsx:95`).
- **`PilotComingSoonScreen`** — не маршрут, но в проде это и есть то, что видно по адресу `/customer/wellness`.

---

## 6. Чего не установил

Пишу отдельно, чтобы не выдать догадку за факт.

1. **Доходит ли человек, назвавший услугу словами на анкете, до подбора.** `goal_text` уходит в `POST /goals/select`, дальше решает Ayla / `beautygo_backend` (PR #285). Эти репозитории для этого вопроса не читал.
2. **Соответствие `CustomerMasterDetailScreen` и `CustomerSlotsScreen` спеке `customer-booking-flow.md` §4/§5** — экраны прочитаны, построчной сверки с требованиями спеки не делал.
3. **Расходится ли каталог мини-аппа с решением §23** («поиск услуги всегда глобальный, по прайсам всех салонов»). Факт: `GET /services` тенант-скоупный — `@with_request_tenant` на `services_list` (`views.py`), поиск в `CustomerCatalogScreen` — клиентский фильтр по этому списку. Но §23 сформулировано про распознавание услуги из обращения, а не про витрину каталога. **Кому из двух принадлежит правило — вопрос владельцу, а не вывод.**
4. **Что происходит на боевом пилоте.** Всё выше — чтение кода `origin/dev`. На `176.119.159.141` не ходил.
5. **Смержено ли `origin/dev` на боевой стенд.** Не проверял.

---

## 7. Вопросы, которые стоит задать владельцу

Не решения — только то, что упирается в него.

1. **Гейт wellness-дашборда.** Условие, которое владелец сам поставил в §37 («снимать только после перевода на `RemoteBookingProxy`»), **выполнено**. Снимать? Если да — вместе с этим решается и кнопка «стакан воды», сегодня ведущая на заглушку. Держит: `active_goals: []` и нулевой `weekly_progress` (§4.2).
2. **Профиль.** Живой экран профиля в проде без имени и без согласий, а полноценный — отключён от навигации (§4.1). Чинить подключением заглушек к реальным ручкам или временно вернуть навигацию на `/me`?
3. **Два поколения.** Вопрос владельцу не задавался ни разу — в реестре его нет. Сводить в одно (§2.5) или оставить как есть?
4. **Спека пустых состояний каталога** (`empty_reason`, решение владельца 02.07) не реализована ни на сервере, ни на клиенте (§3.3). В силе или снята?
5. **`tenant_id` и `entry_point` в снимке намерения** (решение владельца 02.07) не заведены (§3.4). В силе?
6. **§23 и витрина каталога** — глобальный поиск относится к каталогу мини-аппа или только к распознаванию услуги в разговоре (§6, п. 3)?

---

*Составлено чтением `origin/dev` репозитория `ai-bot-platform`, канона `ayla-knowledge` и `docs/OPEN_DECISIONS.md`. Ни один файл в этих репозиториях не изменён.*
