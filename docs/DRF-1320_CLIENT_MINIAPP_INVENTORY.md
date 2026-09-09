# DRF-1320 — Client Mini App: inventory, матрица C05 и gaps

**Исполнено 25.08** по решению главного окна. Код `ai-bot-platform` ветка `dev`, HEAD `91ec947`.
Deliverable по постановке `DRF-1320`: шесть пунктов, ниже в том же порядке.

**Оценка задачи: 3 SP** — сама инвентаризация и матрица. Найденные правки оценены отдельно в §4 и §5.

---

# 0. ПОПРАВКА К МОЕМУ ЖЕ ОТЧЁТУ, БЕЗ КОТОРОЙ ЭТОТ ДОКУМЕНТ ЧИТАЕТСЯ НЕВЕРНО

В `REPORT_SCREENS.md` §XIV я написал: у клиента «макета нет по замыслу». **Это неверно в той форме,
в какой было сказано, и вывод про сканер еды из этого следовал ложный.**

Макетов нет **в Linear**. Но канон существует — в репозитории, и код на него прямо ссылается:

```
docs/screens/customer-booking-flow.md               1049 строк   спека F1-F5
docs/screens/customer-cancellation-reschedule-flow.md  56 КБ
docs/screens/customer-records-flow.md                  60 КБ
docs/screens/customer-profile-flow.md                  74 КБ
docs/screens/customer-food-scanner-flow.md             50 КБ   <- сканер еды СПЕЦИФИЦИРОВАН
docs/screens/customer-main-wellness-dashboard.md       46 КБ
docs/screens/customer-onboarding-flow.md               41 КБ
docs/screens/customer-catalog-empty-states-spec.md
docs/screens/customer-booking-confirm-registration-spec.md
docs/screens/customer-reminders-voice.md
docs/screens/customer-food-scanner-backdate-postpilot.md
```

Плюс `docs/design/handoffs/` — двадцать с лишним handoff-документов мая, `docs/design/policies/`,
`docs/design/decisions-log.md`.

**Что это исправляет:**

- **Сканер еды.** Я написал «шесть экранов работают без единого утверждённого макета». Неверно:
  спека на 50 КБ есть, плюс отдельный документ про backdate. Верно другое и только это —
  **решения владельца о попадании сканера в Controlled Pilot нет**. Это вопрос о scope, не о макете.
- **Метод.** Я сверял код с Linear-макетами августа, тогда как код построен по репозиторному канону мая.
  Это же объясняет и салон, и мастера: не «код отклонился» и не «макет младше», а **два слоя канона,
  которые никто не сводил** — майский в репозитории (по нему построено) и августовский в Linear
  (нарисован заново, майский не учитывает).

Это обстоятельство крупнее любой отдельной строки в моих таблицах. Салон и мастера по второму кругу
не сверял — как договорились, — но `DRF-1320` без этой поправки бессмыслен: его пункт 3
(«список макетов, которые можно оставить как есть») относится именно к этим спекам.

---

# 1. INVENTORY — клиентские экраны и состояния

`CustomerRoutes` (`App.tsx:1006-1114`) — **32 маршрута**, 26 экранных компонентов.

## 1.1 Текущая ветка `/customer/*` — 19 маршрутов

| маршрут | экран | состояния | вход |
|---|---|---|---|
| `/customer/main` | `CustomerRecordsScreen` | loading / ok / error (network·server·other) | бот: `open_home` |
| `/customer/catalog` | `CustomerCatalogScreen` | loading / ok / error | бот: `open_catalog` |
| `/customer/masters/:masterId` | `CustomerMasterDetailScreen` | loading / ok / error | из каталога |
| `/customer/masters/:masterId/slots` | `CustomerSlotsScreen` | loading / ok / error / нет слотов 14 дней | из F2 |
| `/customer/booking/confirm` | `CustomerBookingConfirmScreen` | 2 варианта (registered / anonymous gate) + 7 ошибок | из F3 |
| `/customer/booking/success/:bookingId` | `CustomerBookingSuccessScreen` | полный (state из F4) / минимальный (deep-link) | из F4 |
| `/customer/records` | `CustomerRecordsScreen` | см. выше | бот: `open_visits` |
| `/customer/records/:bookingId` | `CustomerBookingDetailScreen` | loading / ok / error | из списка |
| `/customer/profile` | `CustomerProfileScreen` | секции R1-R6 | бот: `open_profile` |
| `/customer/notification-settings` | `CustomerNotificationSettingsScreen` | — | из профиля |
| `/customer/cards` | `CustomerCardsScreen` | — | из профиля |
| `/customer/wellness` | `CustomerWellnessDashboardScreen` | + офлайн-очередь воды | бот: `open_water_add_250`; из профиля и записей |
| `/customer/goal-select` | `GoalSelectScreen` | — | бот: `open_goal_select` |
| `/customer/food-scanner/capture` | `FoodScannerCaptureScreen` | — | бот: `open_food_scan` |
| `/customer/food-scanner/processing` | `FoodScannerProcessingScreen` | — | из capture |
| `/customer/food-scanner/result` | `FoodScannerResultScreen` | — | из processing |
| `/customer/food-scanner/saved` | `FoodScannerSavedScreen` | — | из result |
| `/customer/food-scanner/diary` | `FoodScannerDiaryScreen` | — | из saved |
| `/customer/food-scanner/manual` | `FoodScannerManualScreen` | — | из capture |

## 1.2 Легаси-ветка — 11 маршрутов, дублирует текущую

| легаси | экран | текущий аналог |
|---|---|---|
| `/` | `HelloScreen` | `/customer/main` |
| `/catalog` | `CatalogScreen` | `/customer/catalog` |
| `/catalog/:serviceId` | `ServiceDetailScreen` | — (аналога нет, см. §4.3) |
| `/book/master` | `MasterPickerScreen` | `/customer/masters/:id` |
| `/book/when` | `BookingWhenScreen` | `/customer/masters/:id/slots` |
| `/book/confirm` | `BookingConfirmScreen` | `/customer/booking/confirm` |
| `/book/success/:bookingId` | `BookingSuccessScreen` | `/customer/booking/success/:id` |
| `/my-visits` | `MyVisitsScreen` | `/customer/records` |
| `/my-visits/:bookingId` | `MyVisitDetailScreen` | `/customer/records/:bookingId` |
| `/me` | `ProfileScreen` | `/customer/profile` |
| `/feedback/:bookingId` | `FeedbackScreen` | — (аналога нет) |
| `*` | `HelloScreen` (catch-all) | — |

Обоснование в коде (`App.tsx:1017-1023`): легаси оставлен ради deep-link из старых DM и потока переноса.

---

# 2. МАТРИЦА `C05 state → screen/component → backend endpoint → gap`

База: `DRF-1271`. Все пути относительно `/api/v1`.

| состояние C05 | экран / компонент | endpoint | gap |
|---|---|---|---|
| **C05.1** Execution Mapping | — (нет экрана) | — | **нет разрыва**: постановка разрешает zero-screen, когда вариант один. Mini App входит уже смапленной |
| **C05.2** Service Choice | `CustomerCatalogScreen` | `GET /services` | **частичный**: экран — каталог целиком, а C05.2 требует «только meaningful choice, не каталог». Нужен scoped-режим |
| — duration / price на карточке | `CustomerCatalogScreen` | `GET /services` | ок — рисуются только фактические (Tau-заглушки удалены в phase 3.1) |
| **C05.3** Provider Choice | `CustomerMasterDetailScreen` | `GET /masters/:id` | **частичный**: имя, услуги, цены, ближайшие слоты есть. **Нет fit reason** («короткая причина соответствия») и trust signals |
| — «Другие специалисты» | — | — | **не построено** |
| **C05.4** Slot Choice — loading | `CustomerSlotsScreen` | `GET /slots?master&service&from&to` | ок |
| — available / selected | `CustomerSlotsScreen` | тот же | ок, окно 14 дней (Tau §10.1) |
| — подсказки по поведению | `CustomerSlotsScreen` | тот же | ок: три варианта заголовка (новый / с историей / 5+ визитов) |
| — no-slots | `CustomerSlotsScreen:294-302` | тот же | **частичный**: состояние есть, но **кандидатов на замену бэкенд не отдаёт** — в коде явный TODO ждать `substitution_candidates` |
| — change-date | `CustomerSlotsScreen` | тот же | ок (окно 14 дней) |
| — change-provider | `CustomerSlotsScreen` | — | **не построено**: копия владельца зафиксирована дословно, данных нет |
| **C05.5** Confirmation | `CustomerBookingConfirmScreen` | — | ок: порядок «что/где/когда/цена → CTA → политика отмены → лояльность → заметка» зафиксирован владельцем |
| — CONFIRM | `StickyCta` «Записаться» | `POST /bookings` | ок: write только после явного подтверждения |
| — CHANGE_TIME / CHANGE_PROVIDER | — | — | **не построено** как действия на экране подтверждения (возврат только назад) |
| — anonymous gate | `CustomerBookingConfirmScreen` §6.2 | MAX OAuth | ок: контекст переживает OAuth через `sessionStorage.pending_booking_intent` |
| **C05.6** Creating | `CustomerBookingConfirmScreen:388-389` | `POST /bookings` | ок: `disabled={submitting}`, «Записываю…», выбор не теряется |
| **C05.7** Success | `CustomerBookingSuccessScreen` | ответ `POST /bookings` | ок: только после authoritative-результата |
| — DONE | «Открыть запись» | — | **дефект маршрута — см. §4.1** |
| — RETURN_TO_CHAT | — | `max-sdk.ts:278` `close()` есть | **не построено**: экран успеха `close()` не зовёт |

## Recovery

| ситуация | реализация | gap |
|---|---|---|
| slot conflict (409) | `kind: "slot_unavailable"` + баннер, возврат к выбору слота | ок, journey не сбрасывается |
| execution unavailable | `kind: "not_bookable"`, `kind: "salon_suspended"` | ок |
| no provider | `kind: "master_unavailable"` | ок |
| no slots | пустое состояние | частичный — см. C05.4 |
| booking error / retry | `kind: "server"`, `kind: "other"` | ок; дубликата не создаёт |
| network | `kind: "network"` | ок |
| expired pending confirmation | — | **не построено** |

**Счёт по матрице:** из 21 проверяемой позиции — **12 закрыто, 6 частично, 3 не построено.**
Ни одна из «не построено» не является блокером happy path.

---

# 3. МАКЕТЫ, КОТОРЫЕ МОЖНО ОСТАВИТЬ КАК ЕСТЬ

Требование `DRF-1320` — переиспользовать, redesign только при подтверждённом gap. Подтверждённых
gap'ов, требующих перерисовки, **нет ни одного**. Оставить без изменений:

| спека | почему |
|---|---|
| `customer-booking-flow.md` §3-§7 (F1-F5) | реализована полностью, включая порядок блоков и тексты, зафиксированные владельцем |
| `customer-booking-confirm-registration-spec.md` | anonymous gate работает, контекст переживает OAuth |
| `customer-catalog-empty-states-spec.md` | пустые состояния каталога на месте |
| `customer-records-flow.md` | список и деталь записи построены |
| `customer-cancellation-reschedule-flow.md` | endpoint'ы отмены и переноса с confirm/undo существуют |
| `customer-profile-flow.md` | R1-R6, экспорт и удаление по 152-ФЗ подключены |
| `customer-reminders-voice.md` | мягкая формулировка напоминания зафиксирована дословно |

**Отдельно: `customer-food-scanner-flow.md` не оценивал.** Спека есть, экраны есть, но вопрос
не в макете, а в scope — см. §5.4.

---

# 4. ТОЧЕЧНЫЕ UX FIXES

## 4.1 После успешной записи клиент попадает на легаси-экран — 1 SP

`CustomerBookingSuccessScreen.tsx:76` — CTA «Открыть запись» ведёт на `/my-visits/{bookingId}`
(`MyVisitDetailScreen`, легаси-ветка). Кнопка бота «Мои записи» ведёт на `/customer/records`
(`CustomerRecordsScreen`) → `/customer/records/{id}` (`CustomerBookingDetailScreen`).

**Один и тот же клиент видит две разные детали одной записи** — свою через успех, чужую через меню.
Это тот же класс ошибки, что чинили в `DRF-1326` для слагов бота: одна кнопка, два назначения.

## 4.2 Вторая CTA на экране успеха отсутствует — оценить после messaging

`CustomerBookingSuccessScreen.tsx:124-129` — «Сообщить по записи» намеренно скрыта до появления
маршрута сообщений. Комментарий честный, тупика нет. Действие: не трогать до messaging UI.

## 4.3 `ServiceDetailScreen` не имеет аналога в текущей ветке — решение, не фикс

`/catalog/:serviceId` живёт только в легаси. `CustomerCatalogScreen` ведёт на него **напрямую**
(докстринг: «→ service detail (`/catalog/:serviceId`, real screen continuing the booking flow)»).
То есть текущая ветка каталога уходит в легаси-ветку в середине пути.

Это не дефект рендера — это **незавершённая миграция**. Решать: достроить `/customer/catalog/:id`
или узаконить переход. **2 SP** на достройку.

## 4.4 Докстринг `parseStartRoute` называет несуществующие маршруты — 2 строки

`max-sdk.ts:145-147`. Уже в списке «готово без его слова», пункт 6.

---

# 5. BACKEND / IDENTITY GAPS — отдельно, как требует постановка

## 5.1 `substitution_candidates` не отдаётся бэкендом

`CustomerSlotsScreen.tsx:298-302` — фронт держит зафиксированную владельцем копию замены мастера
и **не может её показать**: кандидатов нет в ответе `/slots`. Пока это обобщённое «нет слотов».

Цена: пока backend не отдаёт кандидатов, состояние C05.4 change-provider построить нельзя.
Копия при этом уже утверждена и лежит в коде — работа наполовину оплачена.

## 5.2 Fit reason для `ProviderCard` не существует в данных

C05.3 требует «короткую причину соответствия». `GET /masters/:id` её не содержит.
Придумывать на фронте нельзя — постановка прямо запрещает «не придумывать ranking factors».

## 5.3 Expired pending confirmation — нет ни состояния, ни источника

Recovery-пункт `DRF-1271` без реализации и без данных о протухшем подтверждении.

## 5.4 Сканер еды: scope, а не gap

Шесть экранов, спека на 50 КБ, кнопка в стартовой сетке бота (`open_food_scan`, `DRF-1167`).
**Решения владельца о включении в Controlled Pilot нет.** Это единственный вопрос владельцу
по клиенту, и он не про экраны.

## 5.5 Identity handoff MAX DM → Mini App работает

`max-sdk.ts` читает `initData` / `initDataUnsafe.start_param`, `parseStartRoute` разбирает слаг,
`tests/test_miniapp_routes.py` не даёт слагам и ссылкам разойтись. **Разрыва нет.**

---

# 6. REFERENCE BOARD — execution / detail surface

```
MAX DM (primary)                                Mini App (secondary)
─────────────────                               ─────────────────────
C01..C04 диалог
      │
      └── C05.1 mapping ── один вариант? ── да ─┐  zero-screen
                              │ нет             │
                              ▼                 │
                        C05.2 выбор услуги  ────┼──▶ /customer/catalog        GET /services
                              │                 │         │
                              ▼                 │         └──▶ /catalog/:id   (легаси, §4.3)
                        C05.3 выбор мастера ────┼──▶ /customer/masters/:id    GET /masters/:id
                              │                 │      нет fit reason (§5.2)
                              ▼                 │
                        C05.4 выбор слота   ────┼──▶ …/masters/:id/slots      GET /slots
                              │                 │      нет замены мастера (§5.1)
                              ▼                 │
                        C05.5 подтверждение ────┼──▶ /customer/booking/confirm
                              │                 │      7 состояний ошибок
                              ▼                 │
                        C05.6 создание      ────┼──▶ POST /bookings           CTA заблокирована
                              │                 │
                              ▼                 │
                        C05.7 успех         ────┴──▶ /customer/booking/success/:id
                                                          │
                                    «Открыть запись» ─────┴──▶ /my-visits/:id   ← ДЕФЕКТ §4.1
                                                                (должно быть /customer/records/:id)
                                    RETURN_TO_CHAT ───────────▶ не построено
```

---

# ИТОГ

**Happy path от принятой рекомендации до успешной записи проходится без тупиков.** Все acceptance
criteria `DRF-1271`, проверяемые на стороне Mini App, выполняются, кроме возврата в чат.

**Работы найдено на 3 SP правок** (§4.1 — 1 SP, §4.3 — 2 SP) плюс три backend-gap'а, каждый из
которых блокирует ровно одно необязательное состояние и ни один — happy path.

**Владельцу нужно одно решение: сканер еды в Controlled Pilot или нет** (§5.4).
Про экраны решений не требуется — подтверждаю то, что установил в `REPORT_SCREENS.md`.

**И одна поправка ко мне же — §0.** Канон клиента лежит в репозитории, а не в Linear, и вывод
про сканер «без макета» был неверен. Метод сверки «код против Linear» пропускает майский слой,
по которому код и построен.
