# Marketing Campaigns — Developer Handoff Package

| Field | Value |
|---|---|
| **Date** | 2026-05-18 r1 |
| **Designer** | UX-architect skill |
| **Status** | Draft for review |
| **Surfaces** | Web dashboard (owner setup primary) + Customer MAX bot DM (delivery) + MAX manager-bot (campaign results push) |
| **Scope** | Owner-side: campaign creation, segmentation, scheduling, performance tracking. Customer-side: receive campaign messages (extends B14 template). |
| **Auth** | Owner + Admin (per Q-MM-equivalent — Owner full, Admin can run but not configure) |
| **Screens** | 6 (4 owner setup + 2 analytics) |

## Foundation references

| Doc | Why it matters |
|---|---|
| [`2026-05-18-customer-first-time-handoff.md`](./2026-05-18-customer-first-time-handoff.md) | B14 promo template — this design provides the owner-side UI to power that |
| [`2026-05-18-loyalty-system-handoff.md`](./2026-05-18-loyalty-system-handoff.md) | Tier-based segmentation; campaign types like «активация Любимых» |
| [`assistant-persona.md`](../policies/assistant-persona.md) | Campaign messages MUST be persona-conformed |
| [`memory/project_single_assistant_identity.md`](~/.claude/projects/.../memory/project_single_assistant_identity.md) | Campaigns delivered as «помощник студии», never as marketing «бот» |
| [`memory/project_conversation_ownership_tiers.md`](~/.claude/projects/.../memory/project_conversation_ownership_tiers.md) | Campaign messages = bot proactive; respect customer opt-outs |
| [`conversation-ownership-policy.md`](../policies/conversation-ownership-policy.md) | Customer opt-out from proactive (Q-CX9) MUST be respected |

---

## 0. Strategic context

### What this module solves
Salon's third highest revenue driver after bot bookings + loyalty retention: **proactive engagement**. B14 promo template exists in customer first-time UX but has NO owner-facing setup UI. This handoff designs that UI.

### Critical constraints
1. **MAX has no push beyond chat** — all campaigns delivered as bot DMs
2. **Frequency policy is strict** — max 1 promo/month per customer; opt-out everywhere
3. **Customer trust is brittle** — over-messaging = block = lost forever
4. **Anti-spam regulation** (RU advertising law 38-ФЗ) — explicit consent + clear sender + opt-out
5. **Single-assistant identity** — campaigns from «помощник студии», not external marketing voice

### What we are NOT building
- ❌ A/B testing infrastructure (Q-PE2 deferred — same logic)
- ❌ Multi-channel campaigns (email/SMS) — MAX bot DMs only MVP
- ❌ Visual creative editor (no images/banners in MAX bot messages — text + emoji + inline buttons only)
- ❌ Real-time delivery (campaigns scheduled, not immediate-broadcast)
- ❌ Cross-tenant campaign sharing — privacy + competitive risk
- ❌ Pixel tracking — MAX doesn't support; we track clicks on inline buttons only

### What we ARE building
- ✅ Campaign creation form (template + targeting + schedule)
- ✅ Audience segmentation (tier, inactivity, service history)
- ✅ Frequency cap enforcement (cross-campaign + global per customer)
- ✅ Persona quality check before send (auto-applied)
- ✅ Performance analytics (sends / opens / clicks / bookings attributed)
- ✅ Compliance guards (opt-out respect, anti-spam alerts)

---

## 1. Persona JTBDs

### Owner (Karina) JTBD
> «Когда у меня есть свободные слоты на следующей неделе или новая услуга, я хочу за 5 минут отправить целевое предложение нужным клиентам — чтобы заполнить расписание без обзвона.»

### Owner monitoring JTBD
> «Когда я запустила акцию, я хочу через неделю увидеть сколько она принесла записей и не разозлила ли клиентов — чтобы понять стоило ли продолжать.»

### Customer perspective (passive)
> «Когда мне приходит сообщение от салона, я ожидаю что это полезное предложение, а не спам — иначе блокирую помощника.»

---

## 2. Success metrics

| Metric | Target | Type |
|---|---|---|
| **Campaign open rate** (customer opened bot DM with campaign) | ≥ 70% (MAX shows preview by default; opens = engagement) | Engagement |
| Click-through rate (clicked inline button) | ≥ 12% | Conversion |
| **Booking conversion** (clicked → actually booked within 7 days) | ≥ 3% (median across industries) | Outcome |
| Opt-out rate per campaign | < 2% | Hygiene |
| **Block-bot rate after campaign** | < 0.5% (key trust metric) | Safety |
| Campaign creation time (owner UX) | < 5 min from start to send | UX efficiency |
| Frequency cap violations | 0 (hard enforcement) | Compliance |
| Persona check fail rate | < 5% of campaigns (high = bad UX guiding owner) | UX quality |

---

## 3. Architecture

### Data model

```python
class Campaign(Model):
    tenant = FK
    name = CharField()  # internal owner label
    status = CharField(choices=[
        "draft", "scheduled", "sending", "completed", "cancelled", "paused"
    ])
    campaign_type = CharField(choices=[
        "promo_discount",       # «-20% на ламинирование на этой неделе»
        "new_service_announce", # «появилась новая услуга: ботокс»
        "loyalty_milestone",    # «вы Любимый — спецпредложение»
        "re_engage_dormant",    # «давно не виделись, скидка 15%»
        "filler_open_slots",    # «у Анны свободно завтра 14:00»
        "seasonal",             # «к 8 марта»
    ])

    audience_segment = JSONField()  # see segmentation below
    estimated_recipients = IntegerField()  # computed at draft save

    message_body = TextField(max_length=400)  # persona-conformed
    inline_button_label = CharField(max_length=40)  # «Записаться со скидкой»
    inline_button_action = CharField()  # «book_with_promo:CAMPAIGN_ID»
    optional_clipboard_payload = CharField(blank=True)  # promo code if any

    scheduled_send_at = DateTimeField()
    actual_send_started_at = DateTimeField(null=True)
    completed_at = DateTimeField(null=True)

    persona_check_passed = BooleanField()
    persona_check_violations = JSONField(default=list)

    created_by = FK(User)
    created_at = DateTimeField()


class CampaignDispatch(Model):
    """Per-recipient delivery record."""
    campaign = FK(Campaign)
    customer = FK(Customer)
    status = CharField(choices=[
        "queued", "sent", "delivered", "opened", "clicked",
        "opted_out", "blocked_bot", "booking_attributed", "failed"
    ])
    sent_at = DateTimeField(null=True)
    opened_at = DateTimeField(null=True)
    clicked_at = DateTimeField(null=True)
    attributed_booking_id = FK(BookingRequest, null=True)
    error_reason = CharField(blank=True)


class CampaignAudience(Model):
    """Cached audience preview at draft time."""
    # exists for preview-recipient-list at draft stage
    # actual send re-evaluates segment at scheduled_send_at
```

### State machine
```
DRAFT → SCHEDULED → SENDING (cron picks up, dispatches in batches) →
  ├─ COMPLETED (all dispatches resolved)
  └─ CANCELLED (owner aborts before send)

PAUSED — owner can pause SENDING mid-flight
```

---

## 4. Segmentation rules

Customer segment defined as logical AND of filters:

**Tier-based:**
- Стартовый / Постоянный / Любимый (single or multi-select)

**Activity-based:**
- Активные (visited within last X days, configurable)
- Спящие (no visit in 60+ days)
- Очень спящие (no visit in 120+ days)
- Новые (first visit was within last 30 days)

**Service history:**
- Делали услугу X (one or many)
- НЕ делали услугу X (потенциал кросс-продажи)

**Booking value:**
- Высокий чек (avg LTV per visit > threshold)
- Регулярные (visit frequency > X/month)

**Demographic (where available):**
- Возрастной диапазон (if customer provided in profile)
- Любимый мастер (master X)
- День рождения в текущем месяце

**Compliance (auto-applied filters):**
- НЕ opted-out from this campaign type
- НЕ blocked bot
- НЕ already received campaign in last 30 days (frequency cap)
- НЕ in active HUMAN_LOCKED conversation (don't spam during dispute)
- Has consent timestamp (signed up after May 2026 = legal consent included)

**Estimated recipient count** updates live as filters change.

---

## 5. Screen specs

### Screen MC1 — Campaign list (entry point)

`/marketing/campaigns` route. Owner + Admin access.

```
┌────────────────────────────────────────────────────────────────────────────┐
│ Маркетинг → Кампании                                                       │
├────────────────────────────────────────────────────────────────────────────┤
│ Фильтр: [Все ▾]    Период: [Последние 90 дней ▾]    [+ Новая кампания]    │
├────────────────────────────────────────────────────────────────────────────┤
│ ── Активные (1) ────                                                       │
│ ┌────────────────────────────────────────────────────────────────────┐    │
│ │ 🟢 Активная: Ламинирование −20% на неделе                          │    │
│ │ Отправлено: 23 / 45      Открыли: 18      Записались: 4            │    │
│ │ Сегмент: Активные клиенты, делали Ресницы                          │    │
│ │ [Подробнее]  [Пауза]  [Прекратить]                                  │    │
│ └────────────────────────────────────────────────────────────────────┘    │
│                                                                            │
│ ── Запланировано (1) ────                                                  │
│ ┌────────────────────────────────────────────────────────────────────┐    │
│ │ ⏰ Завтра 09:00: Поздравление с 8 марта                            │    │
│ │ Получателей: 142                                                    │    │
│ │ [Редактировать]  [Удалить]                                         │    │
│ └────────────────────────────────────────────────────────────────────┘    │
│                                                                            │
│ ── Завершённые (5) ────                                                    │
│ ┌────────────────────────────────────────────────────────────────────┐    │
│ │ Re-engage dormant — отправлено 12 мая                               │    │
│ │ Получили: 32 • Открыли: 28 (88%) • Записались: 5 (16%)             │    │
│ │ Привело: +14 400 ₽ выручки  • Opt-out: 0  • Блок-бот: 0            │    │
│ │ [Подробнее]  [Создать повтор]                                      │    │
│ └────────────────────────────────────────────────────────────────────┘    │
│                                                                            │
│ ── Черновики (2) ────                                                      │
│ [Drafts list]                                                              │
└────────────────────────────────────────────────────────────────────────────┘
```

### States
- **Empty (no campaigns ever)**: «Маркетинговые кампании помогут заполнить пустые слоты и вернуть спящих клиентов. [Создать первую кампанию]» с примерами use-cases
- **Populated**: as above
- **Loading**: skeleton list
- **Error**: section retry

### Screen MC2 — Create campaign (multi-step wizard)

`/marketing/campaigns/new` — wizard with 4 steps + preview.

**Step 1 — Type & template**

```
┌──────────────────────────────────────────────────────────────────────┐
│ ← Новая кампания                                          Шаг 1 из 4 │
│ ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━                                   │
├──────────────────────────────────────────────────────────────────────┤
│ Что хотите сделать?                                                  │
│                                                                      │
│ ⦿ 💰 Акция со скидкой                                                │
│    «-20% на услугу X на этой неделе» — для заполнения слотов        │
│                                                                      │
│ ◯ ✨ Объявить новую услугу                                           │
│    «У нас появилась услуга Y» — повысить осведомлённость             │
│                                                                      │
│ ◯ 🌹 Поздравить Любимых клиентов                                     │
│    Спецпредложение для постоянных — повысить лояльность              │
│                                                                      │
│ ◯ 💌 Вернуть спящих клиентов                                         │
│    «Давно не виделись» — реактивация                                 │
│                                                                      │
│ ◯ 📅 Заполнить пустые слоты                                          │
│    «У Анны завтра свободно» — таргетинг по мастеру/дате              │
│                                                                      │
│ ◯ 🎉 Сезонная                                                        │
│    8 марта, Новый год, и т.д.                                       │
│                                                                      │
│                                              [Далее: Кому отправлять →] │
└──────────────────────────────────────────────────────────────────────┘
```

**Step 2 — Audience**

```
┌──────────────────────────────────────────────────────────────────────┐
│ ← Новая кампания                                          Шаг 2 из 4 │
│ ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━                                      │
├──────────────────────────────────────────────────────────────────────┤
│ Кому отправлять?                                                     │
│                                                                      │
│ ── По активности ──                                                  │
│ ☑ Активные клиенты (визит в последние 60 дней)                       │
│ ☐ Спящие (60–120 дней без визита)                                    │
│ ☐ Очень спящие (120+ дней)                                           │
│ ☐ Новые (первый визит в последние 30 дней)                           │
│                                                                      │
│ ── По уровню лояльности ──                                           │
│ ☐ Стартовый  ☑ Постоянный  ☑ Любимый                                │
│                                                                      │
│ ── По истории услуг ──                                               │
│ Делали хотя бы одну из услуг:                                        │
│ [Маникюр гель-лак ×] [+ Добавить услугу]                            │
│                                                                      │
│ НЕ делали:                                                           │
│ [Ламинирование ресниц ×] [+ Добавить услугу]                        │
│                                                                      │
│ ── Дополнительно ──                                                  │
│ ☐ Любимый мастер: [Все мастера ▾]                                    │
│ ☐ Возрастной диапазон: [—]                                            │
│ ☐ День рождения в текущем месяце                                     │
│                                                                      │
│ ── Автоматические фильтры (нельзя отключить) ──                      │
│ ✓ Подписаны на проактивные сообщения (opt-in)                        │
│ ✓ Не получали кампанию в последние 30 дней                           │
│ ✓ Не в активном споре с салоном                                      │
│                                                                      │
│ Получателей: ~47 клиентов                                            │
│ [Показать список →]                                                  │
│                                                                      │
│                              [← Назад]  [Далее: Сообщение →]         │
└──────────────────────────────────────────────────────────────────────┘
```

**Step 3 — Message composition**

```
┌──────────────────────────────────────────────────────────────────────┐
│ ← Новая кампания                                          Шаг 3 из 4 │
│ ━━━━━━━━━━━━━━━━━━━━━━━━━━━                                          │
├──────────────────────────────────────────────────────────────────────┤
│ Что отправляем?                                                      │
│                                                                      │
│ Шаблоны:  [Стандартный ▾]  [Использовать прошлую кампанию ▾]        │
│                                                                      │
│ Текст сообщения (max 400 символов):                                  │
│ ┌────────────────────────────────────────────────────────────────┐  │
│ │ На этой неделе у нас 20% скидка на ламинирование ресниц.       │  │
│ │ Если хотите попробовать — записаться можно прямо сейчас.       │  │
│ │ Промокод: LASH20                                                │  │
│ └────────────────────────────────────────────────────────────────┘  │
│                                                          234 / 400   │
│                                                                      │
│ Кнопка действия (inline keyboard):                                   │
│ Текст: [Записаться со скидкой                              ]         │
│ Действие: [Открыть запись со скидкой ▾]                              │
│   ⦿ Открыть запись со скидкой (auto-apply promo at booking)         │
│   ◯ Открыть мини-аппа на услугу                                     │
│   ◯ Просто текст без кнопки                                          │
│                                                                      │
│ Добавить кнопку «Скопировать промокод»? ☑                            │
│  Будет использован clipboard button MAX → одна tap копирует промо    │
│                                                                      │
│ ── Превью для клиента ──                                             │
│ ┌─[ Помощница студии Карина ]──────────────────────────┐            │
│ │ На этой неделе у нас 20% скидка на ламинирование    │            │
│ │ ресниц. Если хотите попробовать — записаться можно   │            │
│ │ прямо сейчас. Промокод: LASH20                       │            │
│ │                                                       │            │
│ │ [📅 Записаться со скидкой]                            │            │
│ │ [📋 Скопировать промокод LASH20]                      │            │
│ └───────────────────────────────────────────────────────┘            │
│                                                                      │
│ ⚠ Проверка персоны:                                                  │
│  ✓ Соответствует тону «тёплый»                                       │
│  ✓ Нет запрещённых фраз                                              │
│  ✓ Длина в норме                                                     │
│  ⚠ Слово «скидка» в запретных у вашего салона —                      │
│    но это маркетинговая кампания, exception разрешён. [Изменить?]    │
│                                                                      │
│                              [← Назад]  [Далее: Время →]             │
└──────────────────────────────────────────────────────────────────────┘
```

**Step 4 — Schedule & confirm**

```
┌──────────────────────────────────────────────────────────────────────┐
│ ← Новая кампания                                          Шаг 4 из 4 │
│ ━━━━━━━━━━━━━━━━━━━━━━━━━━━━                                         │
├──────────────────────────────────────────────────────────────────────┤
│ Когда отправлять?                                                    │
│                                                                      │
│ ⦿ Сейчас                                                             │
│ ◯ В конкретное время:                                                │
│   Дата: [22.05.2026]   Время: [10:00]                                │
│   ⓘ Рекомендуем 10:00 — 12:00 рабочие дни (выше open rate)           │
│                                                                      │
│ ── Сводка ──                                                         │
│ • Тип: Акция со скидкой                                              │
│ • Получателей: ~47 клиентов                                          │
│ • Сегмент: Активные + Постоянные/Любимые + делали Маникюр гель-лак   │
│ • Сообщение: 234 символа, 2 кнопки                                   │
│ • Промокод: LASH20 (clipboard кнопка включена)                       │
│ • Доставка: завтра 10:00                                             │
│                                                                      │
│ Ожидаемый эффект (на основе прошлых кампаний):                       │
│ • Откроют: ~33 человека (70%)                                        │
│ • Перейдут к записи: ~6 человек (12%)                                │
│ • Запишутся: ~2 человека (3%)                                        │
│ • Примерная выручка: 3 500 – 6 000 ₽                                 │
│                                                                      │
│ Что я понимаю:                                                       │
│ ☐ Кампания учитывает opt-out клиентов                                │
│ ☐ Соответствует Закону о рекламе (38-ФЗ)                             │
│ ☐ Согласен(а) на использование промо-фразы «скидка»                   │
│                                                                      │
│                              [← Назад]  [Запланировать кампанию]     │
└──────────────────────────────────────────────────────────────────────┘
```

**Critical UX:**
- All 3 checkboxes required before activation (legal compliance ack)
- «Ожидаемый эффект» builds owner confidence + expectation calibration
- Recommended timing based on past tenant data (or platform median day-1)

---

### Screen MC3 — Campaign detail (running/completed view)

`/marketing/campaigns/{id}`

```
┌──────────────────────────────────────────────────────────────────────┐
│ ← Кампании → Re-engage dormant 12 мая                                │
├──────────────────────────────────────────────────────────────────────┤
│ Статус: ✓ Завершена 14 мая 14:23                                     │
│                                                                      │
│ ── Воронка ──                                                         │
│ Запланировано:  32 получателя                                        │
│ Доставлено:     32 (100%)                                            │
│ Открыли:        28 (88%)                                             │
│ Кликнули:        5 (16%)                                             │
│ Записались:      5 (16%)                                             │
│ ─────────                                                            │
│ Выручка:    14 400 ₽                                                  │
│                                                                      │
│ ── Безопасность ──                                                   │
│ Opt-out на кампанию:  0 человек                                      │
│ Заблокировали бота:   0 человек ✓                                    │
│                                                                      │
│ ── Список получателей ──                                             │
│ ┌────────────────────────────────────────────────────────────────┐  │
│ │ Имя           │ Открыли │ Кликнули │ Запись  │ Сумма          │  │
│ │ ──────────────┼─────────┼──────────┼─────────┼─────────────── │  │
│ │ Мария И.      │ ✓       │ ✓        │ ✓       │ 2 200 ₽        │  │
│ │ Анна П.       │ ✓       │ ✓        │ ✓       │ 3 200 ₽        │  │
│ │ Ольга К.      │ ✓       │ -        │ -       │                │  │
│ │ ...                                                            │  │
│ └────────────────────────────────────────────────────────────────┘  │
│ [Экспорт CSV]                                                        │
│                                                                      │
│ [Создать похожую]    [Архивировать]                                  │
└──────────────────────────────────────────────────────────────────────┘
```

---

### Screen MC4 — Campaign template library

`/marketing/templates`

Pre-built templates for common scenarios:

```
┌──────────────────────────────────────────────────────────────────────┐
│ ← Маркетинг → Шаблоны кампаний                                       │
├──────────────────────────────────────────────────────────────────────┤
│ ──── Заполнить слоты ────                                            │
│ ┌──────────────────────────────────────────────┐                     │
│ │ 📅 «У Анны есть свободные слоты завтра»     │                     │
│ │ Шаблон с автоподстановкой имени мастера и    │                     │
│ │ времени. Сегмент: любимые клиенты Анны.      │                     │
│ │ [Использовать →]                              │                     │
│ └──────────────────────────────────────────────┘                     │
│                                                                      │
│ ──── Вернуть спящих ────                                             │
│ ┌──────────────────────────────────────────────┐                     │
│ │ 💌 «Давно не виделись» (60+ дней)            │                     │
│ │ Лёгкое возвращение с небольшой скидкой       │                     │
│ │ [Использовать →]                              │                     │
│ └──────────────────────────────────────────────┘                     │
│                                                                      │
│ ┌──────────────────────────────────────────────┐                     │
│ │ 💌 «Скучаем» (120+ дней)                     │                     │
│ │ Более тёплое сообщение с бонусом             │                     │
│ │ [Использовать →]                              │                     │
│ └──────────────────────────────────────────────┘                     │
│                                                                      │
│ ──── Сезонные ────                                                   │
│ ┌──────────────────────────────────────────────┐                     │
│ │ 🌷 8 марта                                    │                     │
│ │ Тёплое поздравление + предложение            │                     │
│ │ [Использовать →]                              │                     │
│ └──────────────────────────────────────────────┘                     │
│                                                                      │
│ ── Кастомные шаблоны салона ──                                       │
│ ┌──────────────────────────────────────────────┐                     │
│ │ Моя «Студия Карина — годовщина»               │                     │
│ │ Сохранена из кампании 15 марта 2026          │                     │
│ │ [Использовать]  [Удалить]                    │                     │
│ └──────────────────────────────────────────────┘                     │
└──────────────────────────────────────────────────────────────────────┘
```

Tenant's own saved templates appear at bottom.

---

### Screen MC5 — Marketing analytics widget

Added to Analytics dashboard (extends Analytics Dashboard Screen A1).

```
┌──[ Маркетинг ]──────────────────────────────────┐
│ За 30 дней:                                     │
│ Запущено кампаний:  4                           │
│ Доставлено:         147 сообщений               │
│ Открыли:            89% (130)                   │
│ Конверсия в запись: 12% (18)                    │
│                                                 │
│ Выручка от кампаний: 51 400 ₽                    │
│                                                 │
│ Здоровье:                                       │
│ Opt-out rate:       1.4% (норма < 2%)           │
│ Block-bot rate:     0.0% (норма < 0.5%) ✓       │
│                                                 │
│ [Подробнее в Маркетинге →]                       │
└─────────────────────────────────────────────────┘
```

---

### Screen MC6 — MAX manager-bot campaign result push

Triggered 24h after campaign completed.

```
Помощник студии:
Итоги кампании «Re-engage dormant 12 мая»:

📊 Из 32 получателей:
• Открыли:    28
• Записались: 5 (16%)
• Выручка:    14 400 ₽

✅ Никто не заблокировал бота. Это отличный результат.

[Подробнее в дашборде]
```

---

## 6. Compliance & anti-spam

### Auto-applied rules (cannot be disabled by owner)
1. **Opt-out respect**: customers with `proactive_opt_out=True` excluded automatically
2. **Frequency cap**: max 1 campaign per 30 days per customer (across ALL campaign types)
3. **Bot-blocked filter**: customers who blocked bot excluded
4. **Active dispute filter**: customers in HUMAN_LOCKED tier excluded
5. **Consent timestamp**: customer must have `consent_at` set (registered post-May 2026 = compliant)
6. **Sender identification**: bot DM clearly from «Помощник студии Карина» (per single-assistant identity)
7. **Inline opt-out**: every campaign message includes opt-out chip in keyboard

### Anti-pattern guards (UI-level)
- **Persona check** on every message before send (per Persona Editor §3.4)
- **Forbidden phrases** auto-flagged + require owner override
- **Very large audience** (>500 in single campaign) → CSM alert + slower rollout
- **Repeated bounces** (campaign #N blocked by 5+ customers) → auto-pause + investigate
- **Spam pattern** (3+ campaigns to same segment in 30 days, even if different types) → block creation + warning

### Customer-facing inline opt-out
Every campaign message includes inline button:
- `[😴 Без таких сообщений]` — granular: «без промо» specifically (per Q-CX9 single toggle)
- Tap → confirms «больше не присылать рекламные сообщения» → opt_out flag set

### Reporting to compliance
Owner can export campaign delivery report (CSV) for any audit needs — shows recipient hash + delivery status + opt-out timestamps.

---

## 7. Campaign types — detailed templates

### Type 1: Promo Discount
Template variables: `{service_name}`, `{discount_percent}`, `{promo_code}`, `{duration_days}`
Example: «На этой неделе у нас 20% скидка на ламинирование ресниц. Промокод: LASH20»

### Type 2: New Service Announce
Variables: `{service_name}`, `{description}`
Example: «У нас появилась новая услуга: гель-лак с эффектом стекла. Длится 3-4 недели, цена 2 500 ₽. Записываться?»

### Type 3: Loyalty Milestone
Auto-personalized with tier:
- Постоянный milestone: «Спасибо что выбираете нас уже 4 раза. Для постоянных — бонус 50 баллов в любую запись.»
- Любимый milestone: «12 визитов — вы Любимая клиентка! Подарок: бесплатный SPA-ритуал к следующему маникюру.»

### Type 4: Re-engage Dormant
Variables: `{days_gap}`, `{master_name}`
- 60+ days: «Давно не были у нас. Если что-то изменилось — расскажите. А пока 15% скидка к следующему визиту.»
- 120+ days: «Скучаем! Хотим вернуть вас особым предложением: первый визит со скидкой 30%.»

### Type 5: Filler Open Slots
Variables: `{master_name}`, `{date}`, `{time_slots[]}`
Example: «У Анны завтра свободны 14:00 и 16:30. Записаться?»
Targets: customers who booked with this master before.

### Type 6: Seasonal
Templates per holiday + custom date trigger
- 8 марта: «С праздником! От нас — подарок: 20% на любую услугу до конца недели.»
- Новый год: «С наступающим! Записывайтесь заранее на праздничный маникюр.»

---

## 8. Backend contracts

```
GET /api/v1/marketing/campaigns
  Query: ?status=active|scheduled|completed|draft&period=...
  Response: { campaigns: [Campaign with stats] }

POST /api/v1/marketing/campaigns
  Body: { type, audience_segment, message_body, button_config, schedule, persona_check_override?, compliance_acks }
  Response: 201 { campaign }
  Validation:
    - Persona check on message_body
    - Audience size estimation
    - Compliance flags requirement

PATCH /api/v1/marketing/campaigns/{id}
  Body: partial updates (only allowed in DRAFT or SCHEDULED status)
  Response: 200

POST /api/v1/marketing/campaigns/{id}/launch
  Body: { override_persona_warning?: bool }
  Transitions to SCHEDULED if not «send now», else SENDING

POST /api/v1/marketing/campaigns/{id}/pause
  For SENDING campaign

POST /api/v1/marketing/campaigns/{id}/resume

DELETE /api/v1/marketing/campaigns/{id}
  Cancel + archive

GET /api/v1/marketing/campaigns/{id}/recipients
  Query: ?status=opened|clicked|booked
  Response: paginated recipient list with status

GET /api/v1/marketing/campaigns/{id}/preview-audience
  Body: { audience_segment }
  Response: { estimated_count, sample_recipients: [up to 5 anonymized] }

POST /api/v1/marketing/templates
  Save tenant-custom template from current draft

GET /api/v1/marketing/templates
  Returns platform + tenant templates

GET /api/v1/marketing/analytics
  Query: ?period=30d
  Response: aggregate stats across all campaigns

POST /api/v1/marketing/customer/{id}/opt-out
  Triggered by customer-side inline button
  Per-customer flag persists across future campaigns
```

### Dispatch engine
- Cron picks up SCHEDULED campaigns at `scheduled_send_at`
- Batched dispatch (e.g., 10 messages/sec to MAX bot API — respects rate limit 30 rps)
- Per-recipient status tracking
- Failed delivery retries (3× with exponential backoff)

### Attribution
- Customer clicks campaign button → booking flow → if booking completed within 7 days → `CampaignDispatch.attributed_booking_id` set
- Revenue calculation: sum of `BookingRequest.price_at_booking` for attributed bookings

---

## 9. A11y considerations

- Wizard steps with `aria-current="step"` for progress
- Form sections labeled with `<fieldset><legend>`
- Live audience count: `aria-live="polite"` so SR announces updates
- Persona check results: structured `<ul>` with role icons + text
- Compliance checkboxes: `aria-required="true"`, errors before submit explained inline
- Preview pane has `role="region"` with descriptive label
- Mobile: wizard collapses cleanly, each step full-screen

---

## 10. Edge cases

- **Audience updates between draft and send time** — recompute at send time; banner if size changed dramatically
- **Owner edits campaign after partial send** — only SCHEDULED editable; SENDING locked
- **Customer opts out mid-campaign** — campaign respects in real-time; current dispatch may have already sent but won't count toward this customer's frequency
- **MAX API failures mid-dispatch** — retry with backoff; mark `failed` after 3 attempts; surface to owner in detail view
- **Persona check fails on owner's custom message** — block send until edited OR explicit «override warning» (audit logged)
- **Promo code conflicts with loyalty redemption** — Stack? Replace? **Lean**: customer choose at booking confirmation, single discount only
- **Frequency cap pre-empts campaign for some recipients** — show in audience preview «12 из 47 уже получили кампанию в этом месяце, исключены»
- **Salon disables loyalty mid-campaign** — campaign about loyalty stays valid; customer redemption flow falls back to no-loyalty
- **Customer has multiple master preferences** — segment OR logic (received if matches any master they've visited)
- **Receptionist creates campaign** — allowed but Owner gets notification (audit + approval — per Q-MC2 below)
- **Massive audience (>1000)** — slow rollout over hours to avoid MAX rate limits + monitor block rate; abort if block rate spikes
- **Customer just blocked bot, message in flight** — dispatch checks immediately before send; status changes to `blocked_bot` if relevant

---

## 11. Anti-slop scan (12-point)

| # | Check | Status |
|---|---|---|
| 1 | Inter default | ✅ |
| 2 | Purple gradient | ✅ |
| 3 | Glassmorphism | ✅ |
| 4 | Radius scale | ✅ |
| 5 | Emoji decoration | ⚠ 💰✨🌹💌📅🎉 в template selection (Step 1) — на проде Lucide equivalents (`piggy-bank`, `sparkles`, `flower-2`, `mail`, `calendar`, `party-popper`). KEEP в customer preview (sparing) for clarity. |
| 6 | Centered+CTA | n/a |
| 7 | AI illustrations | ✅ |
| 8 | Gradient overlay | ✅ |
| 9 | Specific copy | ✅ «У Анны завтра свободны 14:00 и 16:30» (concrete); «Получателей: ~47» (real count); «Ожидаемая выручка: 3 500 – 6 000 ₽» (modelled) |
| 10 | Avatars | n/a |
| 11 | Animation restrained | ✅ wizard step transitions 200ms, audience count update fades |
| 12 | Slate-on-slate | ✅ |

**11/12 ✅, 1 fix (template emoji → Lucide on production).**

---

## 12. Cross-screen integration

| Source | Integration |
|---|---|
| Customer first-time §B14 | This module is owner-side UI for that template |
| Loyalty system | Tier-based segmentation; «Loyalty milestone» campaign type |
| Analytics dashboard | Marketing widget added |
| Persona Editor | Persona check applied to message before send |
| Customer profile preferences | Opt-out flags read here |
| Conversations C2 | If customer asks about promo, AI surfaces «у вас активная кампания LASH20 действительна 5 дней» (admin context only) |
| MAX manager-bot | Campaign result push 24h after completion |

---

## 13. Phased delivery

### Phase 1 (MVP) — 3 weeks
- MC1 list, MC2 wizard (4 steps), MC3 detail
- 3 campaign types: Promo, Re-engage, Filler slots
- Basic segmentation (activity + tier + service history)
- Frequency cap enforcement
- Persona check before send
- Inline opt-out

### Phase 2 — 2 weeks
- MC4 template library (platform + tenant)
- 3 more types: New service, Loyalty milestone, Seasonal
- MC5 analytics widget
- MC6 campaign result push

### Phase 3 — 2 weeks
- Recurring campaigns («каждый месяц по 8-му числу»)
- Advanced segmentation (custom filters + saved segments)
- A/B testing prep (NOT MVP — exploratory)

### Phase 4 (v1.1)
- Email channel (multi-channel)
- Visual creative (image/banner) IF MAX supports
- Cross-tenant campaign performance benchmarking (opt-in)

---

## 14. Open questions

| # | Question | Recommendation | Owner | Urgency |
|---|---|---|---|---|
| **Q-MC1** | Frequency cap default — 1/30d strict or relaxed (2/30d, 3/30d)? | **1/30d strict MVP** — over-messaging is biggest trust killer. Salons can argue for relaxation post-data. | PM | 🟡 |
| **Q-MC2** | Receptionist creates campaign — needs Owner approval? | YES — Owner gets approve/reject push via MAX manager-bot. Audit + double-check on customer-facing messages. | PM | 🟡 |
| **Q-MC3** | Promo code + loyalty redemption stacking allowed? | NO MVP — single discount choice at booking (per Loyalty Q-L). Stacking would amplify margin damage. | Founder | 🟡 |
| **Q-MC4** | Customer-side inline opt-out specificity — per-campaign-type or single «без проактивных»? | Per Q-CX9: SINGLE toggle. Customer chooses «без проактивных» = all promo + retention proactive. Granular per-type adds complexity for marginal benefit. | PM | ✅ (confirmed via Q-CX9) |
| **Q-MC5** | Audience preview shows actual names or anonymized hashes? | Show actual names for Owner (privacy: it's their data); use «Klient #4567» format if shown to receptionist (per permissions matrix). | PM | 🟡 |
| **Q-MC6** | Recurring campaigns — natively scheduled OR cloned each time? | Phase 3 only. MVP: clone-and-edit. Recurring infrastructure adds complexity. | PM | 🟢 |
| **Q-MC7** | Customer-side opt-out impact — campaign-level or category-level? | Per-customer flag for ALL promo campaigns. Per-campaign opt-out doesn't make sense (no «opt out of LASH20» — just opt out of all). | PM | 🟢 |
| **Q-MC8** | Persona check fail — soft warning (with override) or hard block? | **Soft warning with override** MVP. Audit if override used. Build trust in persona check before hard-blocking. | PM | 🟡 |
| **Q-MC9** | A/B testing for campaigns — Phase 3 spec now or defer? | Defer MVP design. Phase 4. Owner needs more data before subdividing audience makes sense. | Founder | 🟢 |
| **Q-MC10** | Multi-language campaign content — per P3 RU only — but when KZT/BY launches? | When KZT/BYN customers exist: tenant configures per-customer-language template. Defer per P3. | PM | 🟢 |
| **Q-MC11** | Attribution window for campaign → booking conversion | 7 days default. Customer click → booking within 7d = attributed. Configurable? Lean: fixed MVP. | PM | 🟢 |
| **Q-MC12** | Promo code expiration — auto-track and stop? | YES — campaign has optional `expires_at`. Customer using expired code → graceful «акция завершена, но есть похожее предложение?». Auto-disable button after expiration. | PM | 🟡 |

---

## 15. Cross-document linkage

- Customer template extension: [`2026-05-18-customer-first-time-handoff.md`](./2026-05-18-customer-first-time-handoff.md) §B14
- Persona integration: [`2026-05-18-persona-editor-handoff.md`](./2026-05-18-persona-editor-handoff.md)
- Loyalty integration: [`2026-05-18-loyalty-system-handoff.md`](./2026-05-18-loyalty-system-handoff.md)
- Analytics integration: [`2026-05-18-analytics-dashboard-handoff.md`](./2026-05-18-analytics-dashboard-handoff.md)
- Permissions: [`conversation-ownership-policy.md`](../policies/conversation-ownership-policy.md) §4
- Decisions log: [`decisions-log.md`](../decisions-log.md) — Q-MC1 to Q-MC12 added

---

## 16. What this UNBLOCKS

- B14 customer promo template now has owner-side UI to power it
- **Empty slots monetization** — owner can target available slots
- **Dormant customer reactivation** — measurable retention lever
- **Seasonal revenue spikes** (8 марта, новогодние) — operational tool
- **Loyalty connection** — Loyalty milestone campaigns drive tier engagement
- **Performance visibility** — owner can measure marketing effectiveness

## 17. Sign-off

| Role | Approval | Date |
|---|---|---|
| Designer | ☐ | |
| Product | ☐ | |
| Engineering (FE) | ☐ | |
| Engineering (BE — dispatch engine + segmentation queries) | ☐ | |
| QA (anti-spam scenarios + compliance edge cases) | ☐ | |
| Legal (38-ФЗ compliance ack flow) | ☐ | |
| Founder (Q-MC1 frequency strict + Q-MC2 receptionist approval + Q-MC3 stacking) | ☐ | |
