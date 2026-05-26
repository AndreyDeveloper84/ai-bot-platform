# Ayla-Mediated Provider Messaging Policy

| Field | Value |
|---|---|
| **Date** | 2026-05-26 r1 |
| **Status** | STRATEGIC FOUNDATION — founder decision 2026-05-26, complements `ayla-emergency-fallback-policy.md` (Doc #3) |
| **Author** | Tau (UX/Design stream) |
| **Reads** | [`ayla-identity-and-brand.md`](./ayla-identity-and-brand.md), [`ayla-emergency-fallback-policy.md`](./ayla-emergency-fallback-policy.md), [`tenant-as-provider-model.md`](./tenant-as-provider-model.md), [`solo-provider-ux.md`](./solo-provider-ux.md), [`information-architecture.md`](./information-architecture.md), memory `project_ayla_first_strategic_pivot`, memory `project_conversation_ownership_tiers` (revised), memory `project_ayla_personal_ai`, [`../../screens/customer-main-wellness-dashboard.md`](../../screens/customer-main-wellness-dashboard.md), [`../../screens/customer-onboarding-flow.md`](../../screens/customer-onboarding-flow.md) |

> Customer общается **внутри Ayla**, но может передать сообщение конкретному мастеру/салону **по активной записи**. Ayla получает сообщение, добавляет booking context, маршрутизирует мастеру в master mobile inbox или admin в admin UI. Ответ возвращается через Ayla voice. Никаких отдельных чатов с салонами, никакого direct DM master ↔ customer, никакого раскрытия admin identity. Это **customer-initiated operational pattern**, complementary к system-initiated emergency fallback (Doc #3).

---

## 0. Why this exists

### 0.1 The gap

Existing model имеет **две полярности**:
- **Ayla autonomous** — Ayla отвечает на routine queries без человека
- **Emergency fallback** (Doc #3) — system detects серьёзный incident, admin работает в admin UI invisibly

Между ними **дыра**: customer хочет связаться с конкретным мастером по конкретной записи для **операционного** вопроса (опаздываю, не могу найти вход, уточнить подготовку). Это не routine query (не «когда мне записи»), но и не emergency (не payment dispute / no medical emergency).

В реальной жизни эти operational queries — **самые частые**. Без них customer чувствует «Ayla хороша, но не могу связаться с салоном когда нужно».

### 0.2 Why not direct chat customer ↔ master?

Per `project_ayla_first_strategic_pivot` + `project_conversation_ownership_tiers` (revised):

> AI принадлежит пользователю. Ayla = главный бренд. Customer's relationship с Ayla, salon = provider. Zero handoff customer UX.

Direct customer ↔ master chat нарушает эту модель:
- Customer теряет central «one Ayla знает всё» преимущество
- Master receives spam / inappropriate messages without Ayla as moderator (post-pilot AI-moderation, не сейчас)
- Privacy concern — master видит customer's contact info
- Cross-tenant scaling не работает — 5 salons = 5 chats?

### 0.3 The solution — Ayla mediates

Customer initiates message **по конкретной записи** → Ayla relays к мастеру/админу **с booking context** → response returns через Ayla voice.

Customer всегда видит Ayla. Master видит «Анна П. написала по 16:00 записи». Никаких contact info exchange.

### 0.4 The promise

Single source for:
- Concept rules §2
- Entry points (where customer initiates) §3
- Quick reasons taxonomy + sub-options §4
- UX flow happy path §5
- States matrix §6
- Master side flow §7
- Multi-tenant booking card grouping (Variant C) §8
- Voice patterns §9
- Conversation continuation policy §10
- Past booking exclusion §11
- Emergency fallback boundary §12
- Anti-patterns §13
- Backend mapping §14
- Accessibility §15
- Open questions §16
- Cross-doc linkage §17

---

## 1. Scope

### IN
- Customer-initiated messaging по **активной/будущей** записи
- 4 main quick reasons + sub-options для «Опаздываю»
- Draft preview только для free text (not chip-flow)
- Booking context auto-attached к каждому message
- Master mobile inbox + quick reply chips per reason
- Admin (если applicable) responds via admin UI invisibly
- Customer sees response через Ayla voice quoting master
- Adaptive timeout (5 или 10 минут based on proximity к booking)
- Multi-tenant booking card grouping (Variant C smart adaptive)
- Anti-spam limit (3 messages per booking per day)
- Bot DM trigger detection («опаздываю» → context resolution per §3.3)
- Solo provider variant — same flow, message доходит к Olga's master mobile
- Records tab + dashboard booking card both в scope
- Emergency fallback boundary detection

### OUT
- Direct customer ↔ master DM (breaks Ayla-first)
- Free chat без booking context («просто поболтать с Ириной»)
- Past-booking messaging («хочу написать про прошлую неделю»)
- Master proactive messaging customer first (post-MVP — separate scope)
- AI-moderation сообщений мастера (post-MVP)
- Provider messaging center / inbox per provider (post-MVP)
- Promo messages от salon через customer's Ayla chat
- Threading / conversation history per booking (post-MVP)
- Customer ↔ admin direct chat (admin identity hidden)
- Voice messages в этом flow (Phase 2+ orthogonal scope)
- Master proactive «приходите пораньше» / «давайте перенесём» (separate scope)
- Cross-tenant aggregate messaging («написать всем 5 моим мастерам»)
- Anti-abuse detection beyond simple 3/day limit — Phase 4+
- Master responding с rich media (photo of entrance map) — text only MVP

---

## 2. Core concept — Ayla-mediated provider messaging

### 2.1 Foundation rules (non-negotiable)

1. **Customer always sees Ayla voice** — никаких отдельных chat bubbles с master/admin identity. Ayla quotes мастера verbatim в формате `Ирина подтвердила: «Хорошо, жду.»` но remaining chat surface = Ayla.

2. **Booking context required** — каждое сообщение привязано к конкретной активной/будущей записи. Никакого free chat без booking.

3. **Past bookings excluded** — закрытая запись = no messaging. Если customer хочет написать про прошлый визит, fallback в emergency fallback Doc #3 или в bot DM с Ayla обычным образом.

4. **Human identity protection works both ways** — master не видит customer's phone / direct contact, customer не видит master's phone / Telegram handle.

5. **Emergency keywords route to Doc #3** — payment / refund / legal / serious complaint language detected → emergency fallback policy applies, not this messaging.

6. **One-shot exchanges, no threads** — каждый «Сообщить по записи» tap = standalone exchange. No conversation history visible to customer per booking.

### 2.2 Visual placement

```
┌────────────────────────────────────────────────────────────────────┐
│                  CUSTOMER ↔ HUMAN COMMUNICATION                     │
├────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  Routine queries           Operational queries        Serious       │
│  «когда мне записи?»        about specific booking    incidents     │
│                                                                     │
│   ┌────────────┐         ┌──────────────────────┐  ┌─────────────┐ │
│   │ Ayla       │         │ Ayla-mediated         │  │ Emergency   │ │
│   │ autonomous │         │ provider messaging    │  │ fallback    │ │
│   │            │         │ THIS DOC              │  │ (Doc #3)    │ │
│   └────────────┘         └──────────────────────┘  └─────────────┘ │
│   Customer asks,         Customer initiates:        System detects: │
│   Ayla answers           «Сообщить по записи»       payment_dispute │
│                          → quick reason             booking_conflict│
│                          → context routed           integration_err │
│                          to master/admin            legally_sensitive│
│                          → response back via Ayla   → admin works in │
│                                                       admin UI       │
│                                                       invisibly      │
│                                                                     │
│  Common rules: Ayla voice always; human identity hidden;            │
│                no direct customer ↔ master/admin chat               │
└────────────────────────────────────────────────────────────────────┘
```

---

## 3. Entry points

Customer может trigger «Сообщить по записи» из 3 surface:

### 3.1 Dashboard booking card (compact CTA)

```
┌──────────────────────────────────────┐
│  Ближайшая запись                     │
│                                       │
│  Завтра · пт · 16:00                  │
│  Массаж лимфодренаж · 60 мин          │
│  у Ирины · Формула тела               │
│  ул. Тверская 12                       │
│                                       │
│  [ Открыть запись ]                    │
│  [ Перенести ] [ Маршрут ] [ Написать ]│  ← NEW «Написать»
└──────────────────────────────────────┘
```

Button label compact form: `Написать` (booking card horizontal space tight).

### 3.2 Records tab booking detail (full CTA)

```
┌──────────────────────────────────────┐
│  ← Запись                             │
├──────────────────────────────────────┤
│  Завтра · пт · 16:00                  │
│  Массаж лимфодренаж                    │
│  60 минут · 2200 ₽                     │
│                                       │
│  у Ирины Петровой                      │
│  Формула тела                          │
│  ул. Тверская 12                       │
│                                       │
│  Что входит:                          │
│  • Базовый массаж шеи и плеч           │
│  • Лимфодренаж рук и ног               │
│  • Завершение релаксацией              │
│                                       │
│  ─────────────────────────────       │
│                                       │
│  [ 💬 Сообщить по записи ]             │  ← Full CTA
│  [ Перенести ] [ Отменить запись ]     │
│  [ Маршрут до салона ]                 │
└──────────────────────────────────────┘
```

Full label `💬 Сообщить по записи` — more space, prefix emoji for clarity.

### 3.3 Bot DM trigger detection

Customer writes Ayla в чате «опаздываю» / «не могу найти вход» / «куда идти»:

**Logic per Q-AMM-10:**

```
┌──────────────────────────────────────────────────┐
│  Triggers: «опаздыва*», «не могу найти», «вход», │
│            «куда идти», «адрес», «подготов*»     │
└────────────────────┬─────────────────────────────┘
                     │
                     ▼
        ┌──────────────────────────────┐
        │  Resolve booking context      │
        └──────────────┬───────────────┘
                       │
       ┌───────────────┼───────────────┐
       │               │               │
       ▼               ▼               ▼
   0 active       1 active        2+ active
   bookings       booking         bookings
       │               │               │
       ▼               ▼               ▼
  «Активных     Auto-context:   Selector:
  записей не    «Вы про массаж  «По какой
  нашла. Хочешь сегодня в       записи передать
  записаться?»  16:00 у Ирины?» сообщение?»
                Then route per  Customer taps →
                §5 flow         route per §5 flow
```

Voice templates §9.

### 3.4 What's NOT entry point

- ❌ Master mobile not entry for customer messaging (master доступен только для receiving)
- ❌ Past booking detail screen — closed records have no «Написать»
- ❌ Salon profile / catalog browse — no messaging без specific booking
- ❌ Onboarding flow (S1-S7) — no introduction to feature, discovery contextual per Q-AMM-8
- ❌ AI insight cards / nudges — Q-BACK-4 verdict cards removed regardless

---

## 4. Quick reasons taxonomy

### 4.1 Main reasons (4 fixed for MVP)

| Reason | Emoji | Description hint | Flow type |
|--------|-------|------------------|-----------|
| Опаздываю | ⏰ | Customer running late | **Chip flow** (sub-options) |
| Не могу найти вход | 🚪 | Location confusion | **Free text** (preview required) |
| Уточнить подготовку | 📋 | Pre-visit question | **Free text** (preview required) |
| Другое | ✎ | Anything else | **Free text** (preview required) |

### 4.2 Sub-options для «Опаздываю»

Most common reason → fastest path:

```
┌──────────────────────────────────────┐
│  ⏰ Опаздываю                          │
│                                       │
│  На сколько примерно?                 │
│                                       │
│  [ 5 минут ]   [ 10 минут ]           │
│  [ 15 минут ]  [ Больше — напишу сама ]    │
│                                       │
│  [ Назад ]                             │
└──────────────────────────────────────┘
```

«Больше — напишу сама» → opens free text input (customer specifies delay в minutes / hours).

### 4.3 Free text flow for other reasons

«Не могу найти вход» / «Уточнить подготовку» / «Другое» → text input с context-specific prompt:

```
┌──────────────────────────────────────┐
│  🚪 Не могу найти вход                │
│                                       │
│  Опиши где ты сейчас или что видишь   │
│  — Ирина подскажет.                   │
│                                       │
│  ┌──────────────────────────────┐    │
│  │  стою у красной двери,        │    │
│  │  не понимаю это вход или нет  │    │
│  └──────────────────────────────┘    │
│                                       │
│  [ Назад ]   [ Дальше ]                │
└──────────────────────────────────────┘
```

Prompt copy varies per reason:
- **Не могу найти вход:** «Опиши где ты сейчас или что видишь — Ирина подскажет.»
- **Уточнить подготовку:** «Что хочешь узнать про подготовку?»
- **Другое:** «Что хочешь сказать Ирине?»

### 4.4 Reason → master quick chip mapping

For master side response (§7), each reason has predefined chips:

| Customer reason | Master quick reply chips |
|-----------------|--------------------------|
| ⏰ Опаздываю | `Хорошо, жду` / `Смогу подождать 10 минут` / `Не получится` / `Написать своё` |
| 🚪 Не могу найти вход | `Опишу вход` / `Подойду встречу` / `Написать своё` |
| 📋 Уточнить подготовку | Free text only (varies too much for chips) |
| ✎ Другое | Free text only |

---

## 5. UX flow — happy path

### 5.1 Tap «Сообщить по записи»

Customer taps from dashboard booking card OR Records tab booking detail → opens quick reasons modal.

### 5.2 Quick reasons modal

```
┌──────────────────────────────────────┐
│  Сообщить по записи                   │
│                                       │
│  16:00 · массаж · у Ирины             │
│  Формула тела                          │
│                                       │
│  Что хочешь сказать?                  │
│                                       │
│  [ ⏰ Опаздываю ]                      │
│  [ 🚪 Не могу найти вход ]            │
│  [ 📋 Уточнить подготовку ]           │
│  [ ✎ Другое ]                          │
│                                       │
│  [ Отмена ]                            │
└──────────────────────────────────────┘
```

Booking context (time / service / master / salon) shown at top — customer confirms она правильную запись tap'нула.

### 5.3a Chip flow — «Опаздываю» (instant send, no preview per Q-AMM-3)

```
Step 1: Quick reasons → tap «Опаздываю»

Step 2: Sub-option modal (showed earlier in §4.2)

Step 3: Tap «10 минут» → INSTANT SEND (no preview)

Step 4: Confirmation
┌──────────────────────────────────────┐
│  ✓ Передала Ирине из Формулы тела     │
│  что опаздываешь на 10 минут.         │
│                                       │
│  Напишу, когда она ответит.           │
│                                       │
│  [ Ок ]                                │
└──────────────────────────────────────┘

Step 5: Return to dashboard / Records tab
```

**Why no preview для chip flow:** time-sensitive («опаздываю» = customer literally уже late). Extra tap = friction в момент panic.

### 5.3b Free text flow — «Не могу найти вход» (preview required per Q-AMM-3)

```
Step 1: Quick reasons → tap «Не могу найти вход»

Step 2: Text input modal (showed earlier in §4.3)
         Customer types: «стою у красной двери, не понимаю это вход или нет»

Step 3: Tap «Дальше» → Preview screen
┌──────────────────────────────────────┐
│  Передам Ирине:                       │
│                                       │
│  «Стою у красной двери, не понимаю    │
│  это вход или нет»                    │
│                                       │
│  По записи: 16:00 · массаж             │
│                                       │
│  [ ✓ Отправить ]                       │
│  [ Изменить ]                          │
│  [ Отмена ]                            │
└──────────────────────────────────────┘

Step 4: Tap «✓ Отправить» → Confirmation
┌──────────────────────────────────────┐
│  ✓ Передала Ирине из Формулы тела.    │
│                                       │
│  Напишу, когда она ответит.           │
│                                       │
│  [ Ок ]                                │
└──────────────────────────────────────┘

Step 5: Return to dashboard / Records tab
```

**Why preview для free text:** customer might mistype, want to rephrase, or change mind. Free text = higher stakes than chip selection.

### 5.4 Master response received → Ayla relays

When master taps quick chip OR sends free text response, Ayla messages customer в bot DM (or shows toast если customer в Mini App):

**Format:** `{Master first name} подтвердила: «{verbatim response}»` — quoted, brief, Ayla-mediated framing.

```
─────────────────────────────
Ayla:
Ирина подтвердила: «Хорошо, жду.»

[ Открыть запись ]
─────────────────────────────
```

**Voice rule:** «подтвердила» framing maintains Ayla-mediated model (NOT direct chat «Ирина: ...» pattern). Master quoted but customer sees Ayla relaying. Female form «подтвердила» — adapt to master gender per their profile.

**Edge cases:**
- Master responds verbose («да, да, не волнуйтесь, я очень понимаю, опаздывания бывают всегда, не страшно совсем»): Ayla quotes verbatim. Не truncate / summarize.
- Master sends emoji (👍): Ayla quotes verbatim including emoji.
- Master sends только chip («Хорошо, жду»): Ayla quotes the chip text verbatim.

---

## 6. UX states matrix

### 6.1 Loading — sending

Brief state while message being routed. ≤2 sec normally.

```
┌──────────────────────────────────────┐
│                                       │
│            ⏱                          │
│                                       │
│       Передаю Ирине...                │
│                                       │
│            ●                          │
│          ●   ●                        │
│            ●                          │
│                                       │
└──────────────────────────────────────┘
```

### 6.2 Sent, awaiting response

After confirmation (§5.3a step 4 / §5.3b step 4) — customer returns to normal navigation. Sticky badge на booking card shows pending:

```
┌──────────────────────────────────────┐
│  Завтра · пт · 16:00                  │
│  Массаж лимфодренаж · 60 мин          │
│  у Ирины · Формула тела               │
│  ⏳ Жду ответ Ирины                    │  ← pending badge
│  [ Открыть запись ]                    │
└──────────────────────────────────────┘
```

### 6.3 Master responded

Ayla pushes notification в bot DM OR shows toast в Mini App (depends на customer's current context):

```
─────────────────────────────
[Bot DM notification]

Ayla:
Ирина подтвердила: «Хорошо, жду.»

[ Открыть запись ]
─────────────────────────────
```

Booking card badge updates:

```
┌──────────────────────────────────────┐
│  Завтра · пт · 16:00                  │
│  Массаж лимфодренаж · 60 мин          │
│  у Ирины · Формула тела               │
│  ✓ Ирина в курсе                       │  ← responded badge
│  [ Открыть запись ]                    │
└──────────────────────────────────────┘
```

### 6.4 Timeout — adaptive (per Q-AMM-5)

**Adaptive timeout logic:**

```
if booking_starts_in < 30 min:
    timeout = 5 min
else:
    timeout = 10 min
```

After timeout без master response — Ayla messages customer:

```
─────────────────────────────
Ayla:
Ирина пока не ответила. Запись остаётся
актуальной. Если ситуация изменилась —
напиши ещё раз.

[ Написать ещё раз ]   [ Перенести ]
─────────────────────────────
```

**Voice rule:** «Запись остаётся актуальной» — informational, but NOT «можно идти» (could be wrong if customer's delay grew significantly between message and timeout). Customer оценит ситуацию сама.

Booking card badge:
```
│  ⚠ Ирина пока не ответила              │
```

### 6.5 Send failed (network / master deactivated / tenant SUSPENDED)

```
┌──────────────────────────────────────┐
│  Не получилось передать сообщение.    │
│  Попробуй ещё раз или открой запись   │
│  для других действий.                 │
│                                       │
│  [ ↻ Попробовать ещё раз ]            │
│  [ Открыть запись ]                    │
│  [ Закрыть ]                           │
└──────────────────────────────────────┘
```

If tenant в SUSPENDED state → master mobile inbox unavailable. Customer sees: «Салон сейчас на паузе. Если что-то срочно — напиши мне в чат, разберусь.»

### 6.6 Anti-spam limit reached (per Q-AMM-6)

After 3rd message по тому же booking в один календарный день:

```
┌──────────────────────────────────────┐
│  По этой записи уже отправлено        │
│  несколько сообщений.                  │
│                                       │
│  Если вопрос срочный — напиши Ayla,   │
│  я помогу разобраться.                │
│                                       │
│  [ Открыть чат с Ayla ]                │
│  [ Закрыть ]                           │
└──────────────────────────────────────┘
```

Limit resets at midnight customer's local time. Per booking, not per customer (customer with 5 bookings has 15 messages/day budget).

**Why limit:** anti-spam, anti-anxiety. 3 messages в день = enough для realistic operational queries (опаздываю → подтвердили → потом ещё надо уточнить про подготовку). Beyond that — escalate в emergency fallback или в normal chat с Ayla.

---

## 7. Master side flow

### 7.1 Master mobile inbox

Per master-onboarding-m0-m7.md + master-mobile-handoff: master имеет «Мой день» tab + conversations subset.

New incoming customer message по booking → push notification + inbox item:

```
[Master mobile у Ирины — «Сегодня» tab]
┌──────────────────────────────────────┐
│  Сегодня                              │
│                                       │
│  ── Сейчас ──                         │
│  Анна П. (16:00, массаж)              │
│  🔔 «Опаздывает на 10 минут»          │  ← NEW message
│                                       │
│  Быстрый ответ:                       │
│  [ Хорошо, жду ]                       │
│  [ Смогу подождать 10 минут ]         │
│  [ Не получится ]                      │
│  [ ✎ Написать своё ]                  │
│                                       │
│  ── Сегодня далее ──                  │
│  17:30 Мария С. · лимфодренаж         │
│  19:00 Олег И. · мужской маникюр      │
└──────────────────────────────────────┘
```

### 7.2 Quick reply chips per reason (per §4.4)

Chips visible based on incoming message reason. Customer tapped «Опаздываю → 10 минут» → master sees:
```
[ Хорошо, жду ]
[ Смогу подождать 10 минут ]
[ Не получится ]
[ ✎ Написать своё ]
```

Customer tapped «Не могу найти вход» → master sees:
```
[ Опишу вход ]
[ Подойду встречу ]
[ ✎ Написать своё ]
```

Customer free text «Уточнить подготовку» / «Другое» → master sees only:
```
[ ✎ Написать своё ]
```

(no chips because variety too high)

### 7.3 Free text reply

Master taps `✎ Написать своё`:

```
┌──────────────────────────────────────┐
│  Ответ Анне П.                        │
│  По записи: 16:00 · массаж             │
│                                       │
│  ┌──────────────────────────────┐    │
│  │  Перенести получится — есть   │    │
│  │  свободно завтра 14:00. Как?  │    │
│  └──────────────────────────────┘    │
│                                       │
│  [ Отправить ]   [ Отмена ]            │
└──────────────────────────────────────┘
```

Customer sees response через standard quote format: `Ирина подтвердила: «Перенести получится — есть свободно завтра 14:00. Как?»`

### 7.4 Solo provider variant (per Q-AMM-9)

Ольга = self-employed. Per `solo-provider-ux.md` — her surface = solo unified, не split admin/master.

For Ольга, «Сообщить по записи» from customer arrives в her master mobile inbox same way. Olga's surface shows:

```
[Olga's master mobile — solo dashboard]
┌──────────────────────────────────────┐
│  ✨ ayla pro · Студия Ольги           │
│                                       │
│  Сегодня                              │
│  ── Сейчас ──                         │
│  Анна П. (16:00, маникюр)             │
│  🔔 «Опаздывает на 10 минут»          │
│                                       │
│  Быстрый ответ:                       │
│  [ Хорошо, жду ] [ Смогу подождать ]  │
│  [ Не получится ] [ ✎ Написать своё ] │
│                                       │
│  (NO «approve admin» / «team» chrome  │
│  — solo surface per solo-provider-ux  │
│  §4.2 hidden team features)           │
└──────────────────────────────────────┘
```

No difference в flow for solo vs team — both receive message + respond. Solo surface just hides team-only chrome.

---

## 8. Multi-tenant booking card grouping (Variant C — Smart adaptive)

Per Q-AMM-7 — chosen variant. Fallback Variant B (flat chronological with tenant inline) if implementation cost для C высока.

### 8.1 Rules (Variant C)

```
if customer.upcoming_bookings.count == 1:
    show ONE card, no grouping
elif all bookings same tenant:
    show flat chronological list, tenant in header «Ближайшие записи в {{tenant_name}}»
else:  # multi-tenant
    group by tenant, show sections с tenant header
```

### 8.2 Case 1 — Single booking (no grouping)

```
┌──────────────────────────────────────┐
│  Ближайшая запись                     │
│                                       │
│  Завтра · пт · 16:00                  │
│  Массаж лимфодренаж · 60 мин          │
│  у Ирины · Формула тела               │
│  ул. Тверская 12                       │
│                                       │
│  [ Открыть запись ]                    │
│  [ Перенести ] [ Маршрут ] [ Написать ]│
└──────────────────────────────────────┘
```

(Identical to current dashboard booking card pattern.)

### 8.3 Case 2 — Multiple bookings, same tenant (flat chronological)

```
┌──────────────────────────────────────┐
│  Ближайшие записи в Формуле тела      │
│                                       │
│  Завтра · пт · 16:00                  │
│  Массаж лимфодренаж · у Ирины          │
│  [ Открыть ] [ Перенести ] [ Написать ]│
│                                       │
│  Среда · ср · 12:00                   │
│  Маникюр гель-лак · у Карины           │
│  [ Открыть ] [ Перенести ] [ Написать ]│
└──────────────────────────────────────┘
```

Tenant в section header («в Формуле тела»), inline cards chronological. CTA «Написать» компактный.

### 8.4 Case 3 — Multi-tenant 2+ bookings (grouped by provider)

```
┌──────────────────────────────────────┐
│  Ближайшие записи                     │
│                                       │
│  ── Формула тела ──                   │
│  Завтра · пт · 16:00                  │
│  Массаж лимфодренаж · у Ирины          │
│  [ Открыть ] [ Перенести ] [ Написать ]│
│                                       │
│  ── Студия Натали ──                  │
│  Среда · ср · 12:00                   │
│  Маникюр гель-лак · у Карины           │
│  [ Открыть ] [ Перенести ] [ Написать ]│
│                                       │
│  ── Casa Bella ──                     │
│  Пятница · пт · 19:00                 │
│  Брови · у Светы                       │
│  [ Открыть ] [ Перенести ] [ Написать ]│
└──────────────────────────────────────┘
```

Salon header («── Формула тела ──») visually delimits each provider section. Cards within section chronological. CTA «Написать» per card.

### 8.5 Dashboard vs Records tab placement

| Surface | Booking cards shown | Variant C applies |
|---------|---------------------|-------------------|
| Dashboard «Ближайшая запись» block | NEAREST booking only (1 card) | Case 1 only (single booking pattern) |
| Records tab «Предстоящие» | ALL upcoming bookings | Variant C full logic |
| Records tab «Прошедшие» | Past bookings | No «Написать» button (past = no messaging) |

Dashboard остаётся compact (1 nearest booking). Records tab — full multi-tenant view.

### 8.6 Fallback to Variant B if Variant C costly

If implementation cost для smart adaptive (3 layout cases) > 1 day W1 work, fallback to **Variant B — flat chronological with tenant inline**:

```
┌──────────────────────────────────────┐
│  Ближайшие записи                     │
│                                       │
│  Завтра · пт · 16:00                  │
│  Массаж лимфодренаж · 60 мин          │
│  у Ирины · Формула тела               │
│  [ Открыть ] [ Перенести ] [ Написать ]│
│                                       │
│  Среда · ср · 12:00                   │
│  Маникюр гель-лак · 60 мин             │
│  у Карины · Студия Натали              │
│  [ Открыть ] [ Перенести ] [ Написать ]│
│                                       │
│  Пятница · пт · 19:00                 │
│  Брови · 90 мин                        │
│  у Светы · Casa Bella                  │
│  [ Открыть ] [ Перенести ] [ Написать ]│
└──────────────────────────────────────┘
```

Tenant inline per card. No grouping headers. Simpler implementation, less elegant for multi-tenant power users.

---

## 9. Voice patterns

### 9.1 Customer-side messages (Ayla speaking)

**Trigger acknowledgment (chip flow instant):**
```
✓ Передала Ирине из Формулы тела что опаздываешь на 10 минут.
Напишу, когда она ответит.
```

**Trigger acknowledgment (free text):**
```
✓ Передала Ирине из Формулы тела.
Напишу, когда она ответит.
```

**Master response quoting:**
```
Ирина подтвердила: «Хорошо, жду.»
```

```
Ирина ответила: «Перенести получится — есть свободно завтра 14:00. Как?»
```

Pattern: `{Master first name} {подтвердила|ответила}: «{verbatim text}»`.

**Verb choice rule (per Brand Guardian fix):**
- Use **«подтвердила»** when master accepts/agrees (e.g., «Хорошо, жду» / «Не страшно»)
- Use **«ответила»** when master gives counter-question OR conditional reply (e.g., «Перенести получится — как?»)
- Use **«написала»** for purely informational free text без agreement/question

**Timeout message:**
```
Ирина пока не ответила. Запись остаётся актуальной. Если ситуация
изменилась — напиши ещё раз.
```

**Send failed:**
```
Не дошло до Ирины. Попробуй ещё раз или открой запись для других
действий.
```

**Tenant SUSPENDED:**
```
Салон сейчас на паузе. Если что-то срочно — напиши мне в чат,
разберусь.
```

**Anti-spam limit:**
```
По этой записи уже отправлено несколько сообщений. Если вопрос
срочный — напиши Ayla, я помогу разобраться.
```

**Bot DM trigger — 1 active booking:**
```
Ты про массаж сегодня в 16:00 у Ирины?

[ Да, по этой записи ]   [ Нет, по другой ]   [ Отмена ]
```

**Bot DM trigger — 2+ active bookings:**
```
По какой записи передать сообщение?

[ Массаж · сегодня 16:00 · Формула тела ]
[ Маникюр · среда 12:00 · Студия Натали ]
[ Отмена ]
```

**Bot DM trigger — 0 active bookings:**
```
Активной записи не нашла. Хочешь записаться?

[ Найти услугу ]   [ Не сейчас ]
```

### 9.2 Master quick reply chips (master-side voice)

Per §4.4 mapping. All chips use first-person или action-direct:

**Опаздываю reason:**
- `Хорошо, жду` (acceptance, brief)
- `Смогу подождать 10 минут` (specific limit)
- `Не получится` (decline — Ayla will handle reschedule/cancel flow)
- `✎ Написать своё` (free text)

**Не могу найти вход reason:**
- `Опишу вход` (master will follow up with description в free text)
- `Подойду встречу` (master will physically come to customer)
- `✎ Написать своё` (free text)

### 9.3 Voice anti-patterns

- ❌ «Ирина: Хорошо жду» (direct-chat format breaks Ayla-mediated model)
- ❌ «Сообщение доставлено» (sterile system language)
- ❌ «Ваш запрос обработан» (corporate)
- ❌ «Мастер ответил вам:» (third-person framing of customer)
- ❌ «Не волнуйтесь, мы передадим» (Ayla не говорит «мы» — first-person «я передала»)
- ❌ «Ирина сейчас отвечает» (uncertain real-time claim)
- ❌ «Передам в команду» (это emergency fallback voice, not provider messaging)

---

## 10. Conversation continuation policy

### 10.1 No threads, fresh exchange per tap

Per Q-AMM-6 + founder spec:
- Каждый «Сообщить по записи» tap = standalone exchange с fresh booking context
- No conversation history visible to customer per booking
- Master sees latest message с «по 16:00 записи» context, не history
- If customer wants to update («теперь опаздываю на 20 минут вместо 10») — new tap, new exchange

### 10.2 Anti-spam — max 3 messages per booking per day

Per Q-AMM-6:
- Count: customer-sent messages per single booking
- Limit: 3 per day (resets midnight local time)
- Beyond limit → block UI per §6.6 anti-spam screen
- Hint: «Если срочно — напиши Ayla в чат» (routes to normal Ayla chat где она decides if это emergency)

### 10.3 Why no threading в MVP

Founder verdict 2026-05-26:
> Полноценные threads по каждой записи — post-MVP.

Reasons:
- Threading UX более сложный — нужна history view, reply context, read/unread markers
- MVP value — basic operational query response, не sustained conversation
- Risk — threading может attract spam, unclear etiquette
- Scope discipline — pilot 15 July уже плотный

Post-MVP threading evaluation после первых 50 customers' usage patterns.

---

## 11. Past booking messaging — out of MVP scope

### 11.1 Closed bookings have no «Написать»

Once booking `status = COMPLETED` (visit happened) OR `status = CANCELLED` (closed before visit):
- Records tab «Прошедшие» booking detail — **no** «Сообщить по записи» button
- Booking card UI hides messaging CTA

### 11.2 Why excluded

- Past bookings can't be operationally affected («опаздываю» moot if уже прошло)
- Master в master mobile sees past bookings as history — not actionable
- Risk of misuse — «спасибо за визит» / «не понравилось» content belongs в review flow OR emergency fallback (complaint)

### 11.3 Customer wanting to message about past visit

Customer behaviors that may surface this need:
- «Хочу написать Ирине спасибо за вчера» → Ayla acknowledges, offers review form: «Передам Ирине через отзыв. Хочешь оставить?»
- «Хочу пожаловаться на прошлый визит» → emergency fallback Doc #3 `legally_sensitive` OR `payment_dispute` если refund involved
- «Хочу уточнить про вчерашний продукт» → Ayla handles directly, OR if specific product/preparation question requires master input → Ayla relays «по последнему визиту» context (post-MVP Phase 2+)

For MVP — no formal past-booking messaging surface.

---

## 12. Emergency fallback boundary

### 12.1 Keywords routing to Doc #3

Per Q-AMM-10 + emergency policy detection (per `ayla-emergency-fallback-policy.md` §3.1-3.4):

If customer's free text message contains language matching emergency fallback patterns, route to Doc #3, not «Сообщить по записи»:

| Pattern | Routes to (Doc #3 tier) |
|---------|------------------------|
| «верните деньги», «деньги не пришли», «списали неправильно», «вернуть оплату» | `payment_dispute` |
| «жалоба», «обманули», «недовольна», «плохо обслужили» | `payment_dispute` OR `legally_sensitive` based on severity |
| «травма», «обожгли», «больно», «после визита плохо», «вред здоровью» | `legally_sensitive` |
| «куда жаловаться», «суд», «адвокат», «РКН» | `legally_sensitive` |
| «двойная запись», «не моя запись», «занято кем-то» | `booking_conflict` |
| «не вижу запись», «приложение не работает», «оплата не прошла» | `integration_error` |

Detection happens **server-side** при customer's free text submission. If matched → flow rerouted to emergency fallback policy: Ayla acknowledges differently, admin starts work in admin UI invisibly.

### 12.2 What stays в «Сообщить по записи»

Operational queries не emergency:
- Опаздываю
- Не могу найти вход
- Уточнить подготовку
- Бытовые вопросы про визит («можно с собой воду взять?»)
- Specific master questions («какие масла используешь?»)

Если customer's free text **mixes** operational + emergency (e.g., «опаздываю + кстати деньги не вернули за прошлый раз») — both flows trigger: operational message goes к master, emergency tier opens parallel. Customer sees both Ayla messages.

### 12.3 Smooth transition language

If customer started «Сообщить по записи» free text but message matched emergency → Ayla quietly switches voice:

```
Customer types: «верните деньги за вчера»
Ayla detects payment_dispute → switches:

«Поняла, передаю команде салона — разберутся. {{salon_owner_first_name}} обычно
отвечает в течение 48 часов. Напишу как только узнаю.»
```

NO customer-visible mode switch / loading. Just different Ayla message tone (emergency calm vs operational acknowledgment).

---

## 13. Anti-patterns

### 13.1 Direct customer ↔ master DM

❌ Don't:
- Show master's Telegram / WhatsApp / phone in booking card
- Open separate chat thread «с Ириной»
- Pass customer's phone to master mobile profile

### 13.2 Free chat без booking context

❌ Don't:
- Allow «Написать Ирине» button somewhere без specific booking
- Master profile в catalog → «Связаться» button
- Cross-tenant message «всем моим мастерам»

### 13.3 Past-booking messaging

❌ Don't:
- Show «Сообщить по записи» на closed bookings
- Allow «спасибо за вчерашний визит» surface (use review flow instead)

### 13.4 Master proactive customer-first

❌ Don't (MVP scope):
- Master initiates «Анна, освободился слот раньше — перенесём?» — post-MVP
- Master sends promotional messages — never (anti-pattern always)

### 13.5 AI moderation / filtering

❌ Don't (MVP scope):
- AI checks master response for tone violations — post-MVP
- AI rewrites master response to be friendlier — never (master's voice preserved)

### 13.6 Identity exposure

❌ Don't:
- Customer sees admin's name in any spec scenario except via Doc #3 emergency tier (which itself hides identity)
- Master sees customer's full phone / email / surname
- Show «Написал {{admin_name}}» badges anywhere

### 13.7 Threading / history

❌ Don't (MVP scope):
- Customer sees previous «Сообщить по записи» exchanges for this booking
- Master sees customer's other bookings via this message
- Provide «conversation history per provider» surface

### 13.8 Promo / marketing via this channel

❌ Don't:
- Salon promotional messages через this surface
- Cross-sell suggestions in master's response («кстати, у меня новый сервис»)
- AI insertion of promotional content into messages

---

## 14. Backend mapping

### 14.1 New endpoints (W1 / W2 / W4 build)

| Endpoint | Method | Description | Owner |
|----------|--------|-------------|-------|
| `POST /api/v1/bookings/{booking_id}/messages` | POST | Send customer message по booking (reason + sub_option OR free text) | W4 service `apps/messaging/services/` |
| `GET /api/v1/bookings/{booking_id}/messages/latest` | GET | Get latest exchange status (for UI badge updates) | W4 |
| `POST /api/v1/messages/{message_id}/respond` | POST | Master responds (chip slug OR free text) | W4 |
| `GET /api/v1/me/messaging/pending` | GET | Customer's pending message exchanges (for booking card badges) | W4 |

### 14.2 Master mobile integration

Master mobile already has «Сегодня» tab per `2026-05-18-master-mobile-handoff.md`. Extension:
- New inbox section «🔔 Сообщения от клиентов» (when any pending)
- Quick reply chips component reusing master-mobile Bundle A patterns
- Push notification on new customer message via existing master notification preferences

### 14.3 Timeout monitoring

Backend cron job:
- Run every 1 min
- For each pending message: compute `time_since_sent`
- If exceeds adaptive timeout per §6.4 → send Ayla timeout message to customer + flag in master mobile «не ответил вовремя»
- Audit log entry `messaging.timeout_fired`

### 14.4 Bot DM trigger NLU (W2 owns)

For Q-AMM-10 detection:
- Existing intent classifier (per `customer-first-touch-and-mini-app-states.md`) handles «опаздываю» / «не могу найти» / «куда идти» patterns
- New: when matched, query customer's active bookings
- Route to context resolution per §3.3 (auto-context OR selector OR fallback)

### 14.5 Emergency fallback routing detection

Server-side regex / NLU classifier per §12.1 patterns. Lives in W2 messaging service:
```python
def detect_emergency_in_message(text: str) -> EmergencyTier | None:
    """Return emergency tier if text matches; None for normal operational."""
```

If returns non-None → route to emergency fallback policy flow (Doc #3) instead.

### 14.6 Anti-spam check

```python
def can_send_message(customer_id, booking_id) -> bool:
    count = MessageExchange.objects.filter(
        customer_id=customer_id,
        booking_id=booking_id,
        created_at__date=today_local(customer.timezone),
    ).count()
    return count < 3
```

### 14.7 Models needed (new)

```python
class MessageExchange(models.Model):
    customer = FK(BotUser)
    booking = FK(BookingRequest)
    reason_slug = CharField(choices=REASON_CHOICES)  # late, entrance, prep, other
    sub_option_slug = CharField(null=True)  # 5min, 10min, 15min, custom
    free_text = TextField(null=True)
    status = CharField(choices=STATUS_CHOICES)  # sent, awaiting, responded, timed_out, failed
    sent_at = DateTimeField(auto_now_add=True)
    responded_at = DateTimeField(null=True)
    timed_out_at = DateTimeField(null=True)

class MessageResponse(models.Model):
    exchange = FK(MessageExchange, on_delete=CASCADE)
    master_id = FK(BotUser)  # who responded
    chip_slug = CharField(null=True)  # if quick reply
    free_text = TextField(null=True)
    sent_at = DateTimeField(auto_now_add=True)
```

Schema migration W4. PR гейтed by W1/W2 frontend readiness for UI.

---

## 15. Accessibility (WCAG 2.2 AA — inline)

Patterns reuse from `customer-main-wellness-dashboard.md §8`. Specific to this flow:

1. **2.5.8 Target Size** — All chip buttons (`5 минут`, `10 минут`, master quick replies) ≥44×44dp. Quick reasons grid 2x2 layout — each chip ≥140×80dp.

2. **1.4.3 Contrast** — Pending badge «⏳ Жду ответ Ирины» must meet 4.5:1 against booking card background (sage-green not enough — use accent color `#5A8557` or muted blue).

3. **4.1.3 Status Messages** — Send confirmation «✓ Передала Ирине» must be `role="status"` aria-live="polite". Same for timeout / failed states.

4. **1.3.1 Info & Relationships** — Booking context в quick reasons modal header («16:00 · массаж · у Ирины») wrapped с `aria-labelledby` чтобы screen reader announces «По записи: 16 часов, массаж, у Ирины» при focus.

5. **2.4.3 Focus Order** — Modal open: focus moves to first chip («Опаздываю»). After chip tap → next screen first focus. After send → confirmation OK button.

6. **3.3.1 Error Identification** — Send failed state `role="alert"` для immediate screen reader announcement. Retry button focusable.

7. **2.1.1 Keyboard** — All inline buttons (chips, send, cancel) keyboard-navigable. No keyboard traps в text input.

8. **1.4.4 Resize Text** — At 200% zoom on 360dp: quick reasons grid stacks 1-col. Sub-option chips wrap.

9. **2.3.3 Reduced Motion** — Loading dots animation в §6.1 respect `prefers-reduced-motion: reduce` → static dots.

10. **Multi-tenant grouping accessibility** — Salon section headers (Case 3, §8.4) use `<h3>` semantics. Cards within section в `<ul>` / `<li>` structure. Screen reader announces «Раздел: Формула тела, 1 запись, далее: Студия Натали, 1 запись» etc.

---

## 16. Open questions

All 10 Phase B questions (Q-AMM-1 … Q-AMM-10) resolved by founder/tech lead 2026-05-26. Post-MVP followups:

| # | Question | Lean | Phase |
|---|----------|------|-------|
| Q-AMM-POST-1 | Threading per booking — should it ship Phase 2 если customers regularly hit 3-message limit? | Evaluate via analytics. If >30% bookings hit limit → threading needed | Phase 2+ |
| Q-AMM-POST-2 | Master proactive messaging customer-first («слот свободен раньше — перенесём?») | High retention value but moderation infrastructure needed first | Phase 2+ |
| Q-AMM-POST-3 | AI moderation of master responses (anti-shame, anti-vulgar) | Required если master uses free text often inappropriately | Phase 2+ |
| Q-AMM-POST-4 | Provider messaging center / inbox view («все мои сообщения по записям») | Out-of-scope MVP. If customers want history view — Phase 2 surface | Phase 2+ |
| Q-AMM-POST-5 | Past booking messaging («спасибо за вчера»)? | Channel through review flow MVP. Direct messaging post-pilot if value proven | Phase 2+ |
| Q-AMM-POST-6 | Cross-tenant aggregate messaging («написать всем 5 моим мастерам») | Anti-pattern. Don't build | Never |
| Q-AMM-POST-7 | Voice messages в this flow | Phase 2+ — parallel scope с voice across product | Phase 2+ |
| Q-AMM-POST-8 | Rich media в master response (entrance photo) | High value для «Не могу найти вход» reason. Photo upload + display | Phase 2+ |
| Q-AMM-POST-9 | Multi-tenant grouping evolution — если Variant C ships, when consider Variant B → C upgrade for first wave (pilot)? | Ship Variant B if scope tight. Upgrade C in week 4 if scope allows | Pilot week 1-4 |
| Q-AMM-POST-10 | Solo provider (Olga) — does her own «message inbox» need different UI vs team? | Use same master mobile inbox. Solo surface hides only team-specific chrome per `solo-provider-ux.md` §4.2 | Already covered |

---

## 17. Cross-document linkage

### Foundation
- [`ayla-identity-and-brand.md`](./ayla-identity-and-brand.md) — Ayla voice rules, indeclinable «Ayla», salon-as-third-party framing
- [`ayla-emergency-fallback-policy.md`](./ayla-emergency-fallback-policy.md) (Doc #3) — emergency tier complementary to this; emergency keywords route там
- [`tenant-as-provider-model.md`](./tenant-as-provider-model.md) — salon as provider, not owner of Ayla
- [`conversation-ownership-policy.md`](./conversation-ownership-policy.md) (DEPRECATED 2026-05-19, trace only) — historical 3-tier model
- Memory `project_ayla_first_strategic_pivot` — strategic foundation
- Memory `project_conversation_ownership_tiers` (revised) — Ayla always partner

### Affects (re-frame as policy expands)
- [`../../screens/customer-main-wellness-dashboard.md`](../../screens/customer-main-wellness-dashboard.md) — booking card adds «Написать» CTA per §3.1
- [`../../screens/customer-onboarding-flow.md`](../../screens/customer-onboarding-flow.md) — minor: NO onboarding mention per Q-AMM-8 discovery contextual
- [`../handoffs/2026-05-18-master-mobile-handoff.md`](../handoffs/2026-05-18-master-mobile-handoff.md) — master mobile inbox extension per §7
- [`information-architecture.md`](./information-architecture.md) — Records tab booking detail section gets «Сообщить по записи» entry

### Consumed by (downstream implementers)
- **W1** — Booking card refresh с «Написать» CTA + multi-tenant grouping Variant C (§8) — both dashboard compact + Records tab full + Quick reasons modal Mini App component + Master response display formatting
- **W2** — Bot DM trigger detection (§3.3 + §14.4) + emergency fallback routing detection (§12.1 + §14.5) + timeout monitoring cron (§14.3)
- **W4** — Backend service `apps/messaging/services/` + 4 new endpoints (§14.1) + 2 new models MessageExchange + MessageResponse (§14.7)
- **Master mobile stream (Bundle A)** — inbox section «🔔 Сообщения от клиентов» + quick reply chips component per reason (§7)
- **Solo provider** — same flow per `solo-provider-ux.md` §4.2 + Q-AMM-9

### Engineering
- `apps/messaging/` — new app
- `apps/messaging/services/` — exchange/response services
- `apps/messaging/models.py` — 2 new models
- `apps/orchestrator/intent_classifier/` — extend для bot DM trigger detection
- Master mobile UI components in `apps/miniapp_master/`

---

## 18. What this unblocks

- **Customer-initiated provider messaging** — operational queries («опаздываю», «не могу найти вход») resolved without breaking Ayla-first model
- **Multi-tenant booking surface** — customer ходит в N salons, видит everything cleanly grouped or flat
- **Master mobile usefulness** — master sees actionable «🔔 Сообщения от клиентов» beyond static schedule
- **Pilot retention** — operational friction reduced (currently customer has no way to inform master she's late)
- **Solo provider parity** — Ольга получает same messaging benefit per `solo-provider-ux.md`
- **Bot DM context awareness** — Ayla recognizes operational queries and offers right context
- **Foundation для post-MVP threading** — model design supports future thread extension if data shows demand

## 19. What this does NOT unblock

- ❌ Direct customer ↔ master chat — by design rejected (breaks Ayla-first)
- ❌ Cross-tenant messaging aggregation — anti-pattern per §13.2
- ❌ Past booking messaging — Phase 2+
- ❌ Master proactive messages — Phase 2+
- ❌ AI moderation of master content — Phase 2+
- ❌ Promo / marketing via this channel — never
- ❌ Threading / history per booking — Phase 2+ based on data
- ❌ Voice messages в this flow — Phase 2+ across product
- ❌ Rich media (entrance photos from master) — Phase 2+

---

## 20. Sign-off

| Role | Approval | Date |
|---|---|---|
| Founder (foundational decision: customer ↔ provider mediated by Ayla, not direct) | ✅ | 2026-05-26 |
| Tech Lead (Phase B 10 decisions resolved) | ✅ | 2026-05-26 |
| Tau (this doc's author) | ✅ | 2026-05-26 |
| UX Architect | ☐ | (pending review) |
| W4 (backend service + models §14) | ☐ | (pending implementation) |
| W1 (booking card refresh + multi-tenant grouping + Quick reasons modal §3.1, §3.2, §5, §8) | ☐ | (pending implementation) |
| W2 (bot DM trigger detection + emergency routing + timeout monitoring §3.3, §12.1, §14.3) | ☐ | (pending implementation) |
| Master mobile stream (inbox extension §7) | ☐ | (pending implementation) |
| Accessibility Engineer (WCAG 2.2 AA pass per §15) | ☐ | (pending pilot) |
| Brand Guardian (voice review per §9) | ✅ | 2026-05-26 (review applied inline) |

## Last verified
2026-05-26 r1 — Founder decision 2026-05-26 + tech lead Phase B 10 decisions consumed. Brand Guardian voice review applied inline §9. All Q-AMM-1..10 resolved. Q-AMM-POST-1..10 deferred to Phase 2+.
