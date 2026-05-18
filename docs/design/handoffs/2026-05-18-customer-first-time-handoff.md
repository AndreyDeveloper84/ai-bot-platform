# Customer First-Time Experience — Developer Handoff Package

| Field | Value |
|---|---|
| **Date** | 2026-05-18 r1 |
| **Designer** | UX-architect skill |
| **Status** | Draft for review |
| **Surfaces** | MAX bot DM (primary) + MAX Mini App (transactional) + Telegram bot (parallel channel) |
| **Scope** | Customer-facing UX from first contact through first retention touch (the ACQUISITION funnel) |
| **Screens** | 4 bot-message templates + 8 Mini App screens + 7 proactive bot-message templates |
| **Out of scope** | Loyalty/discount system (Volna 4), Wellness companion (Volna 5), Multi-salon experience (v1.1) |

## Foundation references (read first)

| Doc | Why it matters here |
|---|---|
| [`memory/project_single_assistant_identity.md`](~/.claude/projects/.../memory/project_single_assistant_identity.md) | Customer always sees ONE «помощник студии» — no «bot» word |
| [`memory/project_conversation_ownership_tiers.md`](~/.claude/projects/.../memory/project_conversation_ownership_tiers.md) | Tier model affects what AI replies vs handoff |
| [`memory/project_max_platform_capabilities.md`](~/.claude/projects/.../memory/project_max_platform_capabilities.md) | MAX constraints: no push, no Mini App location, deeplink limits |
| [`memory/project_attribution_extensible_model.md`](~/.claude/projects/.../memory/project_attribution_extensible_model.md) | Each booking creation tagged for billing/analytics |
| [`docs/design/assistant-persona.md`](../policies/assistant-persona.md) | Voice/tone for every customer-facing message |
| [`~/.claude/skills/ux-architect/references/platforms/max-mini-apps.md`](~/.claude/skills/ux-architect/references/platforms/max-mini-apps.md) | Full MAX platform reference |

---

## 0. Overview

### What this module is
The customer's first encounter with the salon's AI-assistant — from initial contact through their first booking + visit + post-visit care + first retention touch (~30–60 days). This is the **acquisition funnel** — make-or-break for retention.

### Why this is critical and under-designed
- The bot's first 3 messages decide if customer ever comes back
- Post-visit care = what makes customers RECOMMEND the salon = our compounding moat
- Proactive retention touches = primary engagement loop (MAX has no push beyond chat)
- Salon's PERCEIVED value is mostly what their customers tell them about the bot
- Previously only one flow designed (Mini App booking) — all other customer touchpoints not designed

### Primary persona — «Мария»
- 25–45, female, customer at the salon
- MAX is one of her main messengers
- Heard about bot from salon receptionist / QR / Instagram link / friend
- Decides in seconds: «работает или нет, помогает или раздражает»
- Mobile-only realistically
- Tolerates 2–3 proactive bot messages per month before blocking

### Secondary persona — «Олег»
- Male, 30–50, less frequent salon customer
- Same product but different categories (barbershop, эпиляция, мужская косметология)
- Pattern: less browsing, more direct booking
- Same UX largely

### JTBD framing

**Primary**:
> «Когда мне нужно записаться к мастеру, я хочу за 30 секунд найти подходящее время и подтвердить запись — чтобы не звонить и не ждать ответа администратора.»

**Secondary**:
> «Когда я готовлюсь к визиту, я хочу заранее знать что нужно сделать (не есть за 2 часа, что взять, противопоказания) — чтобы не приехать впустую.»

> «После визита у меня иногда возникают вопросы — я хочу быстро получить совет от помощника, не звоня и не ища статью в интернете.»

**Anti-JTBD (what we should NOT optimize for)**:
- Multi-step research / comparison shopping — customer chose this salon already
- Group decision-making — most customers book alone
- Complex payment flows — payment happens at salon (cash/card on site)

### Success metrics

| Metric | Target | Type |
|---|---|---|
| **Time-to-first-booking** (first message → confirmed booking) | median < 90s returning / < 180s first-time | North Star |
| First-booking completion rate (entered intent → confirmed) | ≥ 60% | Quality |
| **Return rate within 60 days** (rebook after first visit) | ≥ 40% | Loyalty (key retention) |
| Customer satisfaction post-visit (5★ scale) | ≥ 4.5 average | Quality |
| Bot block rate (customer blocked our bot) | < 3% | Hygiene — frequency policy alarm |
| Bot mute rate | < 8% | Hygiene |
| Handoff rate to admin (customer → human) | < 20% | Bot capability proxy |
| Post-visit care delivery rate | ≥ 95% | Operational |
| Feedback collection rate (rated after visit) | ≥ 35% | Engagement |

---

## 1. Architecture: customer lifecycle state machine

```
DISCOVERED → FIRST_TOUCH →
  ┌─ BROWSING (info-only) → eventually → INTENT
  └─ INTENT (ready to book) →
      ↓
    BOOKING_IN_PROGRESS →
      ↓
    BOOKED (active future visit)
      │
      ├─ T-24h: REMINDED_DAY_BEFORE
      ├─ T-2h:  REMINDED_NEAR
      ├─ T-0:   ARRIVED (optional check-in)
      │   ↓
      │ IN_SERVICE (uncovered by us)
      │   ↓
      └─ COMPLETED →
          ├─ POST_VISIT_CARE_SENT (auto, T+2h after end)
          ├─ FEEDBACK_REQUESTED (T+24h)
          │   ├─ REVIEWED (left rating/comment) →
          │   └─ SILENT (no response)
          │
          ├─ T+14d to T+45d: RETENTION_TOUCH_1 (proactive «come back»)
          ├─ Birthday: BIRTHDAY_TOUCH (proactive)
          ├─ Promotional: PROMO_TOUCH (max 1/month, opt-out)
          │
          └─ Customer responds → cycle restarts at INTENT
              OR
              No response 90+ days → DORMANT
              OR
              Blocked bot → CHURNED
```

**Cancellation/reschedule** can happen at any point from BOOKING_IN_PROGRESS through T-0. Triggers new branch returning to INTENT.

### Channel split per state

| State | Primary channel | Reason |
|---|---|---|
| DISCOVERED → FIRST_TOUCH | Bot DM | Where conversation starts |
| BROWSING | Bot DM | Conversational |
| INTENT (simple) | Bot DM | Light booking |
| INTENT (complex) | Mini App via `open_app` button | Visual catalog/calendar |
| BOOKED | Bot DM (confirmation) | One source of truth |
| REMINDED_* | Bot DM | Push not available |
| ARRIVED | Bot DM | Lightweight |
| POST_VISIT_CARE | Bot DM (text) + Mini App (rating form) | Conversational care, structured rating |
| RETENTION_TOUCH | Bot DM | Proactive |
| Profile / History | Mini App | Visual data |
| Cancel/Reschedule | Bot DM (simple) OR Mini App (complex) | Hybrid |

---

## 2. Entry points (5 variants — first encounter)

### Variant E1 — Bot search/share (warm)
User clicks shared link `https://max.ru/karina_studia_bot` from Instagram / friend / salon's website.
- Lands in bot DM, sees default greeting
- Has no context (no `start_param`)
- Bot greets generally

### Variant E2 — Salon-shared deeplink (warm-er)
Salon shares `https://max.ru/karina_studia_bot?startapp=service-456` (specific service link)
- Mini App opens directly with pre-selected service
- Skip browsing → go to date picker
- 60% faster booking time vs E1

### Variant E3 — QR code at salon (in-person discovery)
QR code at salon walls/business cards links to `https://max.ru/karina_studia_bot?startapp=qr_offline_v1`
- Bot DM opens with «Здравствуйте! Я помощник студии Карина. Видела вас в студии?»
- Context for follow-up (referral from offline visit)

### Variant E4 — Bot username search
Customer searches in MAX «студия Карина» → finds bot → clicks Start
- No `start_param`
- Triggers `bot_started` event
- Bot greets default

### Variant E5 — Bot mention in group / channel
Salon channel mentions «записаться через помощника @karina_studia_bot»
- Customer taps mention → bot DM opens
- Same flow as E4

### UX rule: same greeting variants, different context

For E1, E2, E4, E5 — generic first greeting. For E3 (QR offline), special framing.

---

## 3. Bot message templates (first 4 — critical)

These are the bot's voice in DM. Per `assistant-persona.md` §3 — «помощник студии», не «бот».

### Template B1 — First greeting (no prior context)

```
Помощник студии:
Здравствуйте! Я помощник студии Карина. Помогу записаться,
расскажу о ценах и услугах. С чего начнём?

[Inline keyboard]
  [📅 Записаться] [callback: book_start]
  [💅 Услуги и цены] [callback: browse_services]
  [👤 Наши мастера] [callback: browse_masters]
  [📍 Где мы?] [callback: location]
```

Length: 2 lines body + 4 buttons (Lucide icons на проде вместо emoji).
Tone check: тёплый, не сюсюкающий, не buzzword.

### Template B2 — First greeting (E2 — deeplink with service)

```
Помощник студии:
Здравствуйте! Помогу записаться на маникюр гель-лак.
Открою мини-приложение — выберите удобное время.

[Inline keyboard]
  [open_app] Выбрать время → (opens Mini App on date picker for service 456)
  [callback: switch_service] Другая услуга
```

Skip the 4-button browse menu — they've stated intent already.

### Template B3 — First greeting (E3 — QR / offline)

```
Помощник студии:
Здравствуйте! Я помощник студии Карина. Видели нас лично?
Помогу записаться на следующий визит или ответить на вопросы.

[Inline keyboard]
  [📅 Записаться] [callback: book_start]
  [💅 Прайс] [callback: browse_services]
  [❓ У меня вопрос] [callback: general_question]
```

Slight warmth bump («видели нас лично») — referral acknowledgement.

### Template B4 — Generic Q&A fallback (when intent unclear)

After customer's free-text message that doesn't match any intent:

```
Помощник студии:
Понял ваш вопрос. Уточните пожалуйста:
вам нужно записаться, узнать цену, или что-то другое?

[Inline keyboard]
  [📅 Записаться] [💅 Цены] [❓ Другое]
```

NOT «я не понял» — instead positive acknowledgement + clarification.

---

## 4. Browse mode (info-only, no booking intent)

### Screen F1 — Service catalog (Mini App)

Launched via `open_app` from B1 button «💅 Услуги и цены».

```
┌────────────────────────────────────┐
│ ← Услуги студии Карина             │  ← BackButton wired
├────────────────────────────────────┤
│ 🔎 [ Поиск услуги             ]   │
│ Фильтр: [Все категории ▾]          │
├────────────────────────────────────┤
│  💅 НОГТИ                          │
│  ─────────────                     │
│  Маникюр классический              │
│   60 мин • 1 200 ₽          [→]   │
│                                    │
│  Маникюр + гель-лак                │
│   90 мин • 2 200 ₽          [→]   │
│                                    │
│  Снятие гель-лака                  │
│   30 мин • 500 ₽            [→]   │
│  ...                               │
│                                    │
│  💆 МАССАЖ                         │
│  ─────────────                     │
│  ...                               │
├────────────────────────────────────┤
│  [   Записаться к мастеру       ] │  ← sticky CTA — primary
└────────────────────────────────────┘
```

Tap on service row → expanded detail view:

```
┌────────────────────────────────────┐
│ ← Маникюр + гель-лак              │
├────────────────────────────────────┤
│  [фото услуги — full width]        │
│                                    │
│  Маникюр + гель-лак                │
│  90 мин • 2 200 ₽                  │
│                                    │
│  Что входит:                       │
│  • Снятие старого покрытия         │
│  • Аппаратная обработка            │
│  • Покрытие гель-лак (40+ цветов)  │
│                                    │
│  Подготовка к визиту:              │
│  • Не наносите крем за 2 часа       │
│                                    │
│  Доступные мастера:                │
│   👤 Анна (★★★★★)  [→]            │
│   👤 Олег (★★★★☆)  [→]            │
│                                    │
├────────────────────────────────────┤
│  [   Записаться на это             ] │
└────────────────────────────────────┘
```

### Screen F2 — Masters browse

Same pattern, list of masters with photo / rating / specialization.

```
┌────────────────────────────────────┐
│ ← Наши мастера                     │
├────────────────────────────────────┤
│  ┌────┐  Анна Петрова              │
│  │ AP │  Мастер маникюра, педикюра │
│  └────┘  ★★★★★ 4.9 • 230 отзывов  │
│           «Делаю минимум 7 лет…»   │
│           [Записаться к Анне  →]   │
│  ──────────────────────────        │
│  ┌────┐  Олег Иванов               │
│  │ OI │  Мастер маникюра, наращ.   │
│  └────┘  ★★★★☆ 4.7 • 145 отзывов  │
│           «Специализация: френч…»  │
│           [Записаться к Олегу →]   │
│  ──────────────────────────        │
└────────────────────────────────────┘
```

Tap master → detail (photo gallery, services, schedule preview, reviews snippet).

### States (browse mode)
- Loading: skeleton 5 rows
- Empty (no services configured — shouldn't happen post-onboarding): «Каталог скоро появится»
- Filtered to zero: «Нет услуг в этой категории. [Сбросить фильтр]»
- Error: section retry
- Offline: cached + banner

### Anti-pattern check (browse)
- ❌ Force booking flow when user just wants info — must have «closed» browse mode
- ❌ Hide prices behind «уточните» — show real prices from catalog
- ❌ Generic stock photos — own salon photos OR no photo

---

## 5. Booking flow (links to existing design)

The full Mini App booking flow was designed in the earlier ux-architect skill test (booking screen, see chat history). Key recap:
- Master/Service picker → Date picker → Time slot grid → Confirmation → Success (with QR + share)
- Uses `execute_confirm` bot tool → creates BookingRequest with `attribution_metadata` per attribution-policy
- Sticky CTA bottom (per MAX no-MainButton rule)
- `HapticFeedback` on selections, success, error
- `enableClosingConfirmation` on confirmation step
- `requestScreenMaxBrightness` on success QR

**Difference from earlier design (now informed by MAX deep dive):**
- Use MAX UI React lib (`Avatar.Image`, `CellList`, `Typography.Title`, `Button`) for native iOS/Android feel
- For phone collection (if needed): bot-side `request_contact` button OR Mini App `WebApp.requestContact()` — same UX, different surfaces
- Add post-confirmation prep notes from service catalog
- Add address with `request_geo_location` callback option «Построить маршрут?»

---

## 6. Pre-visit reminders sequence

All via bot DM (MAX has no push beyond chat).

### Template B5 — T-24h reminder

Sent 24h before visit start time.

```
Помощник студии:
Напоминаю — завтра в 15:30 у вас маникюр гель-лак у Анны.
Адрес: ул. Тверская 12.

Подготовка:
• Не наносите крем за 2 часа до визита
• Если опаздываете больше чем на 15 минут — напишите, перенесём

[Inline keyboard]
  [✅ Подтверждаю]                       [callback: confirm_T24]
  [📅 Перенести]                         [callback: reschedule]
  [❌ Отменить]                          [callback: cancel]
  [request_geo_location] Построить маршрут
```

**Behavioral rules:**
- If customer confirms → no T-2h reminder (already confirmed)
- If customer doesn't respond → T-2h reminder fires
- If customer cancels → process per Q15 refund window
- If customer requests reschedule → opens Mini App with reschedule pre-filled

### Template B6 — T-2h reminder (only if T-24h not confirmed)

```
Помощник студии:
Через 2 часа жду вас на маникюре у Анны (15:30, ул. Тверская 12).

[Inline keyboard]
  [✅ Уже еду] [callback: arriving]
  [⏰ Задержусь] [callback: delay]
  [❌ Не смогу] [callback: cancel_last_minute]
  [request_geo_location] Маршрут
```

### Template B7 — T-15min final pre-visit

```
Помощник студии:
До встречи через 15 минут. Анна ждёт.

[Inline keyboard]
  [📍 Маршрут] [request_geo_location]
  [☎ Позвонить в салон] [link: tel:+7XXX]
```

This is the FIRST time we offer a phone call escape — only at the last moment.

### Frequency policy
- 3 messages per booking (T-24h, T-2h, T-15min) — never more, never sooner
- If multiple bookings same day — group into single morning message
- If customer marks reminders «too much» — drop T-2h, keep only T-24h + T-15min

---

## 7. Day-of arrival (optional check-in)

### Template B8 — Customer messages bot «я пришла» / similar

Customer initiates. Bot doesn't proactively ask «вы пришли?» — feels surveillance-y.

```
Customer: я уже в салоне
Помощник студии:
Прекрасно! Анна сейчас выйдет за вами.
Если что-то нужно подождать — напишите.

(Optionally if salon has digital check-in feature)
[Inline keyboard]
  [open_app] 📋 Заполнить анкету (Mini App with consent + medical questionnaire for some services)
```

For most salons (basic services) — no check-in needed.

---

## 8. Post-visit care (the unique value moment)

### Template B9 — Care notes (T+2h after visit end)

Auto-sent. Service-specific content (from catalog `aftercare` field).

```
Помощник студии:
Спасибо, что были у нас! Чтобы маникюр прослужил дольше:

• Первые 2 часа избегайте горячей воды
• Используйте перчатки при уборке
• Маслом для кутикулы — 1 раз в день, 2 недели

[Inline keyboard]
  [callback: have_question] У меня вопрос
  [callback: book_next] Записать на коррекцию через 2 недели
```

### Why this is critical
This is the moment that **makes customers recommend the salon**. Most salons don't do this. We do it automatically + persona-conformed. Differentiator.

### Template B10 — Service-specific examples

**For manicure**: see B9 above

**For косметология (peeling)**:
```
Помощник студии:
Уход после пилинга:
• 48 часов без солнца — обязательно SPF50
• Не трите кожу, дайте ей восстановиться
• Если появятся вопросы или беспокойство — обязательно напишите

[Inline keyboard]
  [callback: have_question] Беспокоит что-то
```

For sensitive procedures, the LAST button is prominent — proactive medical-handoff trigger.

**For массаж**:
```
Помощник студии:
Чтобы массаж принёс максимум пользы:
• 1 час избегайте холодного душа
• Стакан воды сейчас, ещё один за день
• Лёгкие потягивания утром

Хорошего вам вечера!
```

Tone calibration per service.

---

## 9. Post-visit feedback collection

### Template B11 — Feedback prompt (T+24h)

```
Помощник студии:
Хочу узнать как прошёл вчерашний визит к Анне.
Если есть 30 секунд — оцените, пожалуйста:

[Inline keyboard, 5 buttons]
  [⭐] [⭐⭐] [⭐⭐⭐] [⭐⭐⭐⭐] [⭐⭐⭐⭐⭐]
  (callbacks: rate_1 through rate_5)
```

After rating:

```
[If rating ≥ 4]
Помощник студии:
Спасибо! Будем рады видеть снова.

[Inline keyboard]
  [callback: leave_review] Оставить отзыв на сайте
  [callback: share_friend] Поделиться с подругой
```

```
[If rating ≤ 3]
Помощник студии:
Жаль, что не оправдали ожидания. Расскажите подробнее —
передам руководителю салона, разберёмся.

[Inline keyboard]
  [callback: details_negative] Расскажу
  [callback: privacy] Не хочу обсуждать
```

### Critical UX rule (per persona doc + ownership policy)
Rating ≤ 3 triggers **HUMAN_LOCKED tier** automatically — admin must respond personally (not assistant). Reframes as «передам руководителю» (per ownership-policy §7 framing for complaint contexts).

### Anti-patterns avoided
- ❌ Forcing 5-star rating: «оцените от 4 до 5» (Yandex Eats anti-pattern)
- ❌ Multiple repeat prompts if not answered (one prompt, then silent)
- ❌ Public review begging before private resolution

---

## 10. Retention proactive (first touch)

### Template B12 — «Time to come back» (T+30d for most services, custom per service)

```
Помощник студии:
Месяц назад вы были на маникюре — уже время обновить покрытие.
Анна свободна на этой неделе. Записать?

[Inline keyboard]
  [📅 Записаться] [callback: rebook_quick]
  [📅 Другое время] [open_app: booking with master=anna]
  [callback: not_now] Не сейчас
  [callback: opt_out] Не присылайте такие напоминания
```

### Frequency policy
- One «come back» message per customer per service-cycle (30d for nails, 45d for hair, 60d for facials)
- Customers who clicked «opt_out» — silenced permanently
- Customers who didn't respond to 2 consecutive proactive touches — silenced 90 days (avoid spam)

### Template B13 — Birthday (per customer's known birthday)

```
Помощник студии:
С днём рождения! От нас — скидка 15% на любую услугу в течение
недели. Промокод: BIRTHDAY15

[Inline keyboard]
  [clipboard] Скопировать промокод BIRTHDAY15
  [📅 Записаться со скидкой] [callback: birthday_book]
  [callback: thanks_quietly] Спасибо, без подарка
```

Uses `clipboard` button — one-tap copy. Per MAX bot API capability.

### Template B14 — Salon promo (max 1/month, opt-out by default after first)

```
Помощник студии:
На этой неделе у нас 20% скидка на ламинирование ресниц.
Бронировать?

[Inline keyboard]
  [📅 Подробнее] [callback: promo_details]
  [callback: not_interested] Не интересно
  [callback: opt_out_promo] Без рекламы
```

**Frequency hard cap**: 1 promo/month/customer. «не интересно» = no promo for 60 days. «opt_out_promo» = no promo ever (only transactional).

---

## 11. Cancel / reschedule (customer-initiated)

### Template B15 — Customer initiates cancel via free text or button

```
Customer: отмени мою запись на завтра
Помощник студии:
Понятно, отменяю запись:
• 22 мая, 15:30
• Маникюр + гель-лак у Анны

[Inline keyboard]
  [✅ Подтвердить отмену] [callback: cancel_confirm:RECORD_ID]
  [✗ Не отменять] [callback: cancel_abort]
  [📅 Перенести вместо отмены] [open_app: reschedule with record_id]
```

After confirm:

```
Помощник студии:
Запись отменена. Если поменяются планы — пишите, помогу записаться снова.
```

### Refund logic (per Q15 in decisions log)
- Cancel <1h after creation → auto-credit −100₽ to salon (customer notification not relevant — billing is salon-side)
- Cancel >24h after creation → no refund
- Customer doesn't see attribution mechanics

### Template B16 — Reschedule via Mini App

Same conversation can route to Mini App with `open_app` + record_id in start_param:
- Mini App opens at date picker pre-filled with same master+service
- Customer picks new slot → confirm → bot DM gets new confirmation

---

## 12. My visits / My profile (Mini App tabs)

Lightweight. Lives as sub-screens of the booking Mini App.

### Screen F3 — My visits

```
┌────────────────────────────────────┐
│ ← Мои визиты                       │
├────────────────────────────────────┤
│ [Предстоящие] [Прошедшие] [Все]   │
├────────────────────────────────────┤
│ ━━ Предстоящие (1) ━━              │
│                                    │
│ ┌──────────────────────────────┐  │
│ │ 22 мая, 15:30                │  │
│ │ Маникюр гель-лак • Анна      │  │
│ │ ул. Тверская 12              │  │
│ │ [Подробнее] [Перенести]      │  │
│ └──────────────────────────────┘  │
│                                    │
│ ━━ Прошедшие ━━                    │
│                                    │
│ ┌──────────────────────────────┐  │
│ │ 15 апр, 14:00                │  │
│ │ Маникюр • Анна               │  │
│ │ ★★★★★ ваша оценка            │  │
│ │ [Повторить эту запись]        │  │
│ └──────────────────────────────┘  │
│ ...                                │
└────────────────────────────────────┘
```

«Повторить эту запись» = one-tap to book same master+service, opens date picker.

### Screen F4 — My profile

```
┌────────────────────────────────────┐
│ ← Профиль                          │
├────────────────────────────────────┤
│  Мария Иванова                     │
│  +7 ••• ••• 14 67                  │
│                                    │
│  Любимый мастер: Анна              │
│  Любимая услуга: Маникюр гель-лак  │
│                                    │
│  ── Настройки помощника ──         │
│  ☑ Напоминания о визитах           │
│  ☑ Уведомления «время обновить»    │
│  ☐ Промо-предложения               │
│  ☑ Поздравления с днём рождения    │
│                                    │
│  День рождения:                    │
│  [ 12.06.1992 ▾ ]                  │
│                                    │
│  Аллергии / противопоказания:      │
│  [ ввод…                       ]   │
│  (передадим мастеру для безопасности)│
│                                    │
│  [Сохранить]                       │
│                                    │
│  ── Приватность ──                 │
│  [Удалить все мои данные]          │
└────────────────────────────────────┘
```

Per single-assistant-identity: customer doesn't see «bot» anywhere — «помощник».

Per attribution policy: customer doesn't see attribution mechanics — that's salon-side.

Per Q-C3 retention: «Удалить все мои данные» triggers email to support@ flow (manual CSM verification per OP6).

---

## 13. MAX-specific patterns leveraged

### Pattern P1 — `request_geo_location` for directions
At reminder messages (T-24h, T-2h, T-15min) — bot button «Построить маршрут». User taps → MAX returns LocationAttachment → bot replies with map link + estimated time.

### Pattern P2 — `clipboard` for promo codes
Birthday and promo messages use `clipboard` button — one tap, code is copied. Toast appears. User pastes wherever. Per MAX bot API §3 of skill ref.

### Pattern P3 — `request_contact` (Mini App OR bot)
For phone collection if missing — bot-side button preferred (works without opening Mini App).

### Pattern P4 — `open_app` button to launch Mini App with state
- For booking: `open_app + start_param=master-X_service-Y_date-Z`
- For reschedule: `open_app + start_param=reschedule_record-N`
- For feedback long form: `open_app + start_param=feedback_record-N`

### Pattern P5 — User mentions in salon team chat
When customer message arrives, salon team chat gets:
```
Помощник студии: [Анна](max://user/123), новая запись от Марии И.
на 22 мая 15:30
```
Per MAX bot API mention syntax. (This is salon-internal, not customer-facing.)

### Pattern P6 — No native push for proactive
All proactive (B12, B13, B14) are bot DMs. Strict frequency policy.

---

## 14. Components inventory (delta from existing)

| Component | Where used | Notes |
|---|---|---|
| `BotMessageBubble` | All bot DM templates | Persona-conformed; markdown support |
| `InlineKeyboard` | Every bot template | Per MAX Bot API §3 |
| `ServiceCard` | Browse F1, F2 | Catalog row + price + duration |
| `MasterCard` | Browse F2 | Photo + rating + specialization |
| `BookingCard` | My visits F3 | Past/upcoming visit row |
| `RatingButtons` | Feedback B11 | 5-star button set |
| `ProfileForm` | F4 | User settings + preferences toggles |
| `OptOutToggle` | F4 + after each promo message | Persistent opt-out tracking |
| `MaxUiAvatar`, `MaxUiButton`, `MaxUiCellList`, `MaxUiTypography` | All Mini App screens | Use MAX UI lib (per platform recommendation) |

---

## 15. Backend contracts

### Bot DM templates / proactive scheduling
```
POST /api/v1/customer/proactive/schedule
  Body: { customer_id, template_id (B12/B13/B14), scheduled_at, context }
  Schedules a proactive bot message

POST /api/v1/customer/proactive/cancel
  Body: { customer_id, opt_out: bool, scope: "promo" | "retention" | "all" }

GET /api/v1/customer/{id}/preferences
  Returns notification preferences

PATCH /api/v1/customer/{id}/preferences
  Body: { reminders, retention, promo, birthday: bool }

GET /api/v1/customer/{id}/visits
  Query: ?status=upcoming|past|all
  Returns booking list

POST /api/v1/customer/{id}/feedback
  Body: { record_id, rating: 1-5, comment?, attributed_to_bot: bool }
  rating ≤ 3 triggers HUMAN_LOCKED conversation tier

POST /api/v1/customer/{id}/data-deletion-request
  Triggers OP6 customer-deletion workflow (CSM email + verification)
```

### Bot webhook handlers
```
on('message_callback', payload) → route by callback prefix:
  book_start, browse_*, rate_*, confirm_T24, reschedule, cancel,
  opt_out, opt_out_promo, rebook_quick, birthday_book, ...

on('message_created') → free text → NLU intent classify → route
on('bot_started') → first-time greeting (E1/E4/E5 variants)
```

### Frequency tracking
```
ProactiveDispatchLog table:
  customer_id, template_id, sent_at, responded, response_type, opt_out_at
```

Frequency policy enforcement before send: count past N days, suppress if exceeded.

---

## 16. A11y considerations

- All bot messages plain text (screen readers read naturally)
- Inline keyboard buttons have clear labels (not icon-only)
- Mini App: standard a11y rules apply (per accessibility.md ref)
- Star ratings: each star is a button with `aria-label="оценка X из 5"`
- Customer with motor difficulty: large touch targets (48dp+); bot-side simple flows preferred over Mini App complex
- Customer with cognitive load: short messages, one action at a time

---

## 17. Edge cases registry

- **Customer never opens bot after first message** → no follow-up; cold lead, dispatch CSM if salon enabled CSM-revival
- **Customer abandons mid-booking in Mini App** → after 1h, bot DM: «Не выбрали время? Хотите я предложу?» (one nudge, then silent)
- **Customer accidentally double-books** (intent classifier failure) → second booking warns «уже есть запись на этот день — что-то изменилось?»
- **Customer asks «вы бот?»** → truthful answer per persona doc §4
- **Customer asks for human** → handoff per ownership-policy tier (explicit_human_request)
- **Customer sends voice message** → MAX bot AudioAttachment received; transcribe via whisper → process as text. NOT MVP (per Q-C6 voice messages not MVP) — fallback: «Голос пока не распознаю, напишите текстом, пожалуйста.»
- **Customer sends image** (e.g., screenshot of issue) → ImageAttachment received; for now route to HUMAN_LOCKED (admin reviews image manually)
- **Customer sends location proactively** → LocationAttachment received; bot: «Спасибо! Это поможет если хотите построить маршрут.»
- **Salon offline (bot down or YClients unreachable)** → bot: «Помощник временно молчит. Можете позвонить в салон: +7…» (link button with tel: URL)
- **Customer's bot blocked the platform** → bot can't send messages; reminders not delivered; salon must re-acquire via fresh deeplink
- **Customer's MAX account banned** → bot stops trying; CSM notifies salon
- **Customer's language is not RU** → MVP RU only per P3; fallback message «Пока работаю только на русском, передам ваш вопрос команде»
- **Customer responds to year-old reminder** → bot: «Прошло много времени! Помочь записаться снова?»
- **Customer marks promo as «opt_out_promo»** → suppress promo forever (DB flag); transactional messages still allowed
- **Customer in multiple salons via MAX** → per Q-CO5: separate profiles per salon, no cross-leak

---

## 18. Anti-slop scan (12-point)

| # | Check | Status | Note |
|---|---|---|---|
| 1 | Не Inter default | ✅ MAX UI lib uses platform-native (SF Pro / Roboto) |
| 2 | Не purple gradient | ✅ salon-warmth palette |
| 3 | Glassmorphism intentional | ✅ no glass anywhere customer-facing |
| 4 | Radius scale | ✅ MAX UI lib + 8/12 в Mini App |
| 5 | Нет emoji decoration | ⚠ В bot inline keyboard используются 📅 💅 👤 📍 ⭐ — на проде заменить на Lucide-equivalent text labels: "[Записаться]" "[Услуги]" "[Мастера]" "[Маршрут]" — emoji можно оставить как accent **внутри** bubble text per persona doc (медицинский salon: убрать), но не в button labels |
| 6 | Hero не centered+single-CTA | ✅ n/a (нет landing page для customer) |
| 7 | AI illustrations | ✅ нет |
| 8 | Gradient overlay | ✅ нет |
| 9 | Copy specific | ✅ «Анна свободна на этой неделе», «месяц назад вы были» |
| 10 | Avatars real / initials | ✅ MAX UI Avatar component with initials fallback |
| 11 | Animation restrained | ✅ MAX UI defaults + selection haptics |
| 12 | Не shadcn slate-on-slate | ✅ |

**11/12 ✅, 1 fix (emoji в button labels → text labels на проде).**

---

## 19. Open questions

| # | Question | Recommendation/lean | Owner | Urgency |
|---|---|---|---|---|
| **Q-CX1** | Customer onboarding tour — нужен ли при первом старте бота explicit «как работает помощник» tour? | Lean NO — value should be self-evident in first 3 messages. If feedback shows confusion, add later. | PM | 🟡 |
| **Q-CX2** | Voice messages — accept and transcribe (Whisper) или sticky-decline? | Per Q-C6: not MVP. Decline with «голос пока не распознаю» fallback. | PM | ✅ closed via Q-C6 |
| **Q-CX3** | Photo attachments — accept and route to HUMAN_LOCKED tier? | Yes — customer sends screenshot of problem → admin reviews. Don't try to AI-interpret images on MVP. | PM | 🟡 |
| **Q-CX4** | Birthday data collection — required field or optional in profile? | Optional. Asked once at profile completion. If skipped, no birthday touch. | PM | 🟢 |
| **Q-CX5** | Service-specific aftercare templates — fixed by platform or per-tenant customizable? | Platform-curated 11-category defaults (per catalog vertical memory) + tenant can override per-service. | PM + content | 🟡 |
| **Q-CX6** | Retention timing per service category — fixed (30d nails, 45d hair, 60d facials) or per-service customizable? | Fixed by category MVP. Custom v1.1. | PM | 🟢 |
| **Q-CX7** | «Поделиться с подругой» feature in B11 (post-visit) — referral link with tracking? | Yes via `shareMaxContent` — referrer customer flagged for future loyalty credit (when loyalty ships) | PM + Eng | 🟡 |
| **Q-CX8** | Negative rating (≤3) routing — to salon owner OR mastered involved OR both? | To salon owner only (privacy); master sees own ratings via permissions later (master mobile handoff) | PM | 🟡 |
| **Q-CX9** | Customer can opt-out ALL bot proactive messages (just transactional reminders) — UX in profile clear? | Yes — make «без проактивных» a single toggle. Transactional (reminders for confirmed bookings) always on. | PM | 🟡 |
| **Q-CX10** | First-time customer questionnaire — anything we want to capture (preferences, allergies) before first booking? | Skip on MVP. Ask at profile-completion prompt after first visit. Cold-form on first visit = friction. | PM | 🟢 |
| **Q-CX11** | Bot persona name greeting — does customer see salon's persona name («Помощница студии Карина») on first message, or generic? | YES — per assistant-persona.md tenant configures name in onboarding; surfaces in first greeting. Strong product differentiation. | Confirmed | ✅ |
| **Q-CX12** | If salon has multiple locations — does the customer choose location in first interaction, or one default per bot? | Per chain UX (5-bot org limit per Max docs): single bot with location encoded in start_param. Customer chooses location in F1 «Услуги» as filter. | PM | 🟡 |

---

## 20. Cross-document linkage

- Foundation: [`memory/project_single_assistant_identity.md`](~/.claude/projects/.../memory/project_single_assistant_identity.md)
- Foundation: [`memory/project_max_platform_capabilities.md`](~/.claude/projects/.../memory/project_max_platform_capabilities.md)
- Voice: [`docs/design/assistant-persona.md`](../policies/assistant-persona.md)
- Operational: [`docs/design/conversation-ownership-policy.md`](../policies/conversation-ownership-policy.md)
- Booking flow (Mini App detail): chat history — earlier «test booking screen» design
- Onboarding parent: [`docs/design/2026-05-17-salon-onboarding-handoff.md`](./2026-05-17-salon-onboarding-handoff.md)
- Conversations module (admin side): [`docs/design/2026-05-17-conversations-handoff.md`](./2026-05-17-conversations-handoff.md)
- Attribution: [`docs/design/attribution-policy.md`](../policies/attribution-policy.md)
- Decisions log: [`decisions-log.md`](../decisions-log.md) — Q-CX* added

---

## 21. What this UNBLOCKS

- ✅ Customer-side product launch readiness
- ✅ Bot script library (B1–B16 templates) for engineering
- ✅ Service-specific aftercare content sourcing (Q-CX5 needs PM/content)
- ✅ Frequency policy enforcement (clear from this doc)
- ✅ Customer profile + preferences screen
- ✅ Feedback collection pipeline
- ✅ Retention proactive scheduling

## 22. What this does NOT cover (future docs)

- **Loyalty system** (Volna 4)
- **Wellness companion / Ayla layer** (Volna 5)
- **Multi-salon experience** (v1.1)
- **Wait list** (v1.1)
- **Gift certificates** (v1.1+)
- **Group bookings** (v1.1+)
- **Subscriptions** (recurring monthly facials etc — v1.2+)

---

## 23. Sign-off

| Role | Approval | Date |
|---|---|---|
| Designer | ☐ | |
| Product | ☐ | |
| Engineering (FE) | ☐ | |
| Engineering (BE) | ☐ | |
| Content/persona (aftercare templates per Q-CX5) | ☐ | |
| QA | ☐ | |
| CSM (escalation flows) | ☐ | |

---

## 24. Next steps

1. **PM ratifies** open questions Q-CX1 through Q-CX12 (most are 🟡 or 🟢)
2. **Content team** sources aftercare templates per Q-CX5 — 11-category baseline (per salon catalog vertical memory) × 3–5 services each
3. **Engineering** picks up:
   - Bot template library (B1–B16)
   - Frequency policy + ProactiveDispatchLog table
   - Customer preferences API + Mini App F4 profile
   - Feedback API (rating + HUMAN_LOCKED routing for ≤3)
4. **Founder** ratifies via decisions log entries Q-CX1 through Q-CX12
