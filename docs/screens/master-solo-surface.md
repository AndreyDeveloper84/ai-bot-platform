# Screen: master-solo-surface

| Field | Value |
|---|---|
| **Audience** | Provider-side — Ольга (self-employed master, one BotUser = owner+admin+master per ADR-0008) |
| **Phase** | P0 BLOCKER pilot 15 July 2026 (solo providers first-class per founder decision 2026-05-25) |
| **Status** | draft — Phase A–G done, awaiting tech lead final sign-off + frontend handoff |
| **Channel** | **Ayla Pro Mini App** (separate from customer Ayla Mini App) + manager-bot DM push |
| **Stream** | Tau (UX/Design) |
| **Date** | 2026-05-26 r1 |
| **Foundation** | [`solo-provider-ux.md §5`](../design/policies/solo-provider-ux.md) · Master Mobile Handoff Bundle A · [`master-conversational-templates.md`](../design/policies/master-conversational-templates.md) · [`ayla-mediated-messaging.md §7.4`](../design/policies/ayla-mediated-messaging.md) |
| **Scope option** | Option B — P0 detailed 4 tabs + P1 referenced 4 (tech lead verdict 2026-05-26) |

---

## 1. Контекст

### Кто Ольга

Self-employed мастер маникюра. Работает дома или в арендованном кабинете. **Один человек = owner + admin + master в одном tenant** per ADR-0008 multi-role additive.

### Откуда приходит на этот surface

Per `solo-provider-ux.md §7.1`:
1. Phase 4c salon-onboarding flow → «С чего ты сегодня? Я работаю одна / Команда / Команда позже»
2. Backend auto-seed: TenantStaff(owner) + TenantStaff(admin) + CatalogMaster(linked_bot_user=self)
3. «Готовлю твой кабинет — пара минут» splash 2-3 sec
4. Lands на **«Мой день»** tab (default)

### Куда уходит

- Customer initiates «Написать по записи» → Olga receives in inbox (§4.1 «Мой день» sticky-top section)
- Customer cancels/reschedules → Ayla notifies Olga (manager-bot DM push)
- Olga chats с Ayla (8th tab AI-помощник) — voice merges owner+master tones

### Critical strategic note

Solo Olga **не «урезанный салон»**. Это **полноценный provider** с расширенным feature set:
- Full catalog edit (она admin)
- Direct schedule edit (no approval flow — она owner)
- Full earnings visibility (она единственный мастер)
- See all reviews + can reply
- Phone visible с audit (per Q-MSL-7 click-to-reveal)

Brand discipline: surface чувствуется «свой кабинет», not «обрезанная team app» per solo-provider-ux.md §11.3.

---

## 2. Solo vs Team — adaptations table

Per Phase B delta vs existing Bundle A (Master Mobile Handoff):

| Aspect | Team Анна (Bundle A) | Solo Ольга |
|--------|----------------------|------------|
| Identity chip | «Анна · мастер · Студия Карина» | «Ольга · мастер маникюра · Студия Ольги» (merged owner+master) |
| Tabs visible | 4-5 (today / schedule / conversations / profile) | 4-8 depending nav variant (Phase E §3) |
| Customers seen | OWN customers only (PII first-name-only) | ALL customers (она owner) — phone masked, click-to-reveal с audit |
| Schedule | Own working hours, submits change requests to admin | Direct edit без approval flow |
| Earnings | Own services subset | Full earnings (одна) |
| Catalog | View-only (admin manages) | Full edit (она admin) |
| Reviews | About own services | All reviews (single-master tenant) |
| Approval queue | Submits to admin | Hidden — no approval flow |
| Internal chat (master ↔ admin) | Available | Hidden — нет admin separate |
| AI assist replies | YES, persona-checked | YES, same |
| Voice tone | Functional Ayla (master register) | Functional Ayla **merging owner-tone + master-tone** per §7 |

---

## 3. Phase E — Navigation comparison (CRITICAL per founder addition)

Founder caught: 8 visible bottom tabs на mobile 360-414dp viewport risk перегруза. Truncations «Расп», «Отз» = unprofessional. ОБЯЗАТЕЛЬНО compare 3 variants.

### Variant A — 8 visible bottom tabs (original solo-provider-ux §5)

```
┌──────────────────────────────────────────────────┐
│  📋   📅   👥   💼   ⏰   💰   ⭐   💬             │
│ День Записи Клиент Услуги Расп Доход Отз AI       │
└──────────────────────────────────────────────────┘
```

**Pros:**
- Все tabs visible один tap
- Mental model = «все мои инструменты сразу»
- Direct path to any feature

**Cons:**
- На 360dp viewport: 360 / 8 = 45dp per tab → labels truncate («Расп», «Отз», «Клиент»)
- Unprofessional appearance — looks crowded
- WCAG 2.5.8 risk — small touch targets below 44dp width в каждой кнопке если icon+label
- Sub-optimal для new users (mental overload)

### Variant B — 5 bottom tabs + «Ещё» menu (founder lean)

```
┌──────────────────────────────────────────────────┐
│  📋     📅     👥     💼     ⋯                    │
│ День  Записи Клиенты Услуги  Ещё                  │
└──────────────────────────────────────────────────┘

Tap «Ещё» → opens overlay/sheet:
┌──────────────────────────────────────┐
│  Ещё                                  │
│                                       │
│  ⏰ Расписание                         │
│  💰 Доходы                             │
│  ⭐ Отзывы                             │
│  💬 AI-помощник                        │
│                                       │
│  ──────────────                       │
│  👤 Профиль                            │
│  ⚙ Настройки                          │
│  📤 Выйти                              │
└──────────────────────────────────────┘
```

**Pros:**
- 360 / 5 = 72dp per tab → labels readable, no truncation
- Standard mobile pattern (Instagram Stories tabs / Telegram main / VK)
- P0 daily-use tabs prominent в bottom bar
- P1 tabs in «Ещё» = less daily access frequency anyway
- «Ещё» menu can host Профиль + Настройки + Выйти (no separate top-right surfaces needed)

**Cons:**
- One extra tap для P1 features (Расписание / Доходы / Отзывы / AI)
- Hidden discoverability (new user может not find AI-помощник)

### Variant C — Adaptive nav

```
Day 1-7 (new user):  Show all 8 tabs (discoverability priority)
Day 8+:              Auto-switch to 5+«Ещё» (efficiency priority)
                     Customer's most-used 4 tabs auto-pinned

OR:

Always 5 tabs but contents adapt:
- Default: День / Записи / Клиенты / Услуги / Ещё
- If Olga uses Доходы heavily: День / Записи / Клиенты / Доходы / Ещё
```

**Pros:**
- Smart UX — surfaces what customer actually uses
- New user discoverability + power user efficiency

**Cons:**
- Implementation complexity (~+50% scope vs B)
- Confusing if tabs change order randomly
- Hard to support training material («в третьей вкладке» — третья меняется)
- Better as Phase 2+ refinement

### Verdict — Recommend Variant B

| Metric | Variant A (8 tabs) | Variant B (5 + Ещё) | Variant C (Adaptive) |
|--------|---------------------|----------------------|----------------------|
| Touch target compliance | ⚠ Risk failing WCAG 2.5.8 на 360dp | ✅ Safely 72dp | ✅ Safely sized |
| Label readability | ❌ Truncated | ✅ Full words | ✅ Full words |
| Implementation cost | Baseline | +0 (standard pattern) | +50% complexity |
| New user discoverability | ✅ All visible | ⚠ Hidden P1 features | ✅ |
| Daily-use efficiency | ✅ One tap | ⚠ Extra tap for P1 | ✅ |
| Pilot scope fit | OK if cropped | ✅ Best fit | Defer post-pilot |
| Brand polish | ❌ Crowded | ✅ Clean | ✅ |

**Selected:** Variant B (5 + «Ещё»).

**Rationale:**
- Daily-use 4 tabs (Мой день / Записи / Клиенты / Услуги) cover Olga's primary workflow
- P1 tabs (Расписание / Доходы / Отзывы / AI-помощник) accessed less frequently
- WCAG compliance + brand polish > marginal efficiency gain for power users
- Implementation cost lowest

**Variant C deferred** to Phase 2+ if data shows adaptive value (after pilot retention validation).

### Implementation note for W1

«Ещё» menu pattern должен поддерживать:
- Tap → bottom sheet (~ 60% viewport height) с list
- Tap item → navigate, sheet auto-dismiss
- Swipe-down dismiss sheet
- Profile + Settings + Logout NESTED в «Ещё» (no separate top-right hamburger)
- Active tab indicator stays in bottom bar regardless of which sheet section was last visited

---

## 4. P0 detailed tabs

### 4.1 📋 Мой день (LANDING default)

#### Layout (selected per Phase E §10 variant Mixed Hero)

```
┌──────────────────────────────────────────────┐
│  ayla pro ✨                       👤  ⚙     │  Header (56dp)
│  Ольга · мастер маникюра                      │  Identity chip
│  Студия Ольги                                 │
│  ──────────────────────────────────           │
│                                               │
│  Доброе утро, Ольга. Сегодня 4 записи.        │  Greeting + summary
│  Первая через 30 минут.                       │
│                                               │
│  ── Сообщения от клиентов (1) ──              │  STICKY-TOP per Q-MSL-4
│                                               │  ayla-mediated-messaging
│  Анна · сегодня 16:00 · массаж лимфодренаж    │  §7.4 inline inbox
│  «Опаздывает на 10 минут»                     │
│  [ Хорошо, жду ]   [ Смогу подождать ]        │  Quick reply chips
│  [ Не получится ]   [ ✎ Ответить ]            │  per reason
│                                               │
│  ─────────────────────────────────────       │
│                                               │
│  ── Ближайшая ──                              │  Nearest customer
│                                               │  card (hero)
│  ┌──────────────────────────────────────┐   │
│  │  11:00 · Маникюр гель-лак · 90 мин    │   │
│  │  Анна Петрова · +7 ••• 14 67           │   │  Phone MASKED
│  │  💚 5-й визит                           │   │  per Q-MSL-7
│  │                                        │   │
│  │  [ Показать телефон ]                  │   │  Click-to-reveal
│  │  [ Сообщить что готова ]               │   │  с audit event
│  │  [ Перенести ]                         │   │
│  └──────────────────────────────────────┘   │
│                                               │
│  ── Сегодня дальше ──                         │  Agenda list
│                                               │
│  13:00  Мария С. · педикюр · 60 мин           │
│  15:30  Олег И. · мужской маникюр · 30 мин    │
│  18:00  Светлана П. · ногти + дизайн · 120 мин│
│                                               │
│  ─────────────────────────────────────       │
│                                               │
│  Что сделать                                  │  Quick actions
│                                               │
│  ┌─────────────────┐ ┌─────────────────┐   │
│  │  + Запись        │ │  ⛔ Заблокировать│   │  Manual booking +
│  │                  │ │     время         │   │  block time
│  └─────────────────┘ └─────────────────┘   │  Q-MSL-6
│  ┌─────────────────┐ ┌─────────────────┐   │
│  │  ✎ Профиль       │ │  💬 Спросить    │   │
│  │                  │ │     Ayla         │   │
│  └─────────────────┘ └─────────────────┘   │
│                                               │
│  ─────────────────────────────────────       │
│                                               │
│  На этой неделе                               │  Weekly mini summary
│                                               │
│  18 записей · 5 активных дней · 2 новых       │
│  клиента                                      │
│                                               │
│  [ Подробнее ]                                │
│                                               │
├──────────────────────────────────────────────┤
│  📋     📅     👥     💼     ⋯                │  Bottom nav 56dp
│ День  Записи Клиенты Услуги  Ещё              │  per Variant B
│ ▔▔▔▔                                          │
└──────────────────────────────────────────────┘
```

#### Block details

**Header (56dp, sticky):**
- `ayla pro ✨` wordmark (lowercase, sage-green) — distinct from customer Ayla
- Identity chip 2 lines: «Ольга · мастер маникюра» / «Студия Ольги» per Q-MSL-2
- Right: 👤 profile + ⚙ settings (sub-menu для notifications, audit log, account)

**Greeting line:**
- Time-sensitive («Доброе утро» / «Добрый день» / «Добрый вечер»)
- Day fact («Сегодня 4 записи. Первая через 30 минут.») — functional master register per master-conversational-templates §2
- NOT customer-style greeting («Хорошего дня, надеюсь будешь молодец») — Olga at work, no emotional check-in

**Inbox sticky-top (Q-MSL-4):**
- Visible only если есть unanswered customer messages
- Per ayla-mediated-messaging.md §7 — chips per reason
- Tap chip → instant send response → quoted to customer via Ayla
- Tap «✎ Ответить» → free text input → Ayla relays
- Multiple messages stack chronologically newest-top

**Nearest customer card (hero):**
- Time + service + duration prominent
- Customer name (full per Q-MSL-7 — Olga is owner)
- **Phone masked** «+7 ••• 14 67» с `[ Показать телефон ]` button → audit event written via RedZoneReader pattern (W4 #710 reference)
- Customer status badge («💚 5-й визит» / «🆕 Первый визит» / «⚠ Был no-show»)
- Actions:
  - `Сообщить что готова` → notification customer «Ирина готова принимать» via Ayla
  - `Перенести` → opens reschedule flow per `customer-cancellation-reschedule-spec.md`
- If 0 booking сегодня → block hidden, replaced с «Сегодня свободный день — отдыхай или подгоняй услуги»

**Agenda list:**
- Remaining bookings today, chronological
- Compact format: `{time}  {customer first name + initial}  · {service}  · {duration}`
- Tap → opens booking detail (in Записи tab)
- Past slots (already happened) below current с muted styling

**Quick actions (2×2 grid):**
- `+ Запись` — manual booking flow per Q-MSL-6 (opens compact form: customer name → service → time)
- `⛔ Заблокировать время` — block slot для personal time (lunch, errand, etc.)
- `✎ Профиль` — opens Профиль section в «Ещё» menu
- `💬 Спросить Ayla` — opens AI-помощник tab с context «по сегодняшнему дню»

**Weekly mini summary:**
- Three numbers: bookings count / active days / new customers
- Tap «Подробнее» → opens Доходы tab (per §5.2 P1 reference)
- Cold-start gate: hide block если < 3 days данных текущей недели (similar to dashboard cold-start per Q-BACK-2)

#### States

| State | Trigger | UX |
|-------|---------|-----|
| Loading skeleton | First open / refresh | Header + nav cached. Shimmer для agenda + inbox. <500ms typical |
| Empty (0 bookings today) | No records | Hero replaced: «Сегодня свободный день — отдыхай или подгоняй услуги.» + CTA `+ Запись` |
| Empty (first day, 0 logs) | New tenant first day | Greeting + onboarding nudge «Расскажу как лучше пользоваться» + key actions |
| Pending inbox sticky | Customer message по записи | Block visible top per Q-MSL-4, dismissable after master responds |
| API down | Backend issues | Per-block stale badges + retry. Header + nav работают always (cached) |
| Offline | No network | Top banner «⚡ Без сети — показываю последние данные» + cached agenda |

### 4.2 📅 Записи (full bookings)

#### Layout

```
┌──────────────────────────────────────────────┐
│  ←  Записи                                    │  Header 56dp
│  ─────────────────────────────────           │
│                                               │
│  [ Сегодня (4) ][ Неделя (18) ]               │  Tab strip
│  [ Будущие (12) ][ Прошедшие ]                │
│                                               │
│  ─────────────────────────────────────       │
│                                               │
│  ── Понедельник 26 мая ──                     │  Day group
│                                               │
│  ┌──────────────────────────────────────┐   │
│  │  11:00 — 12:30                         │   │  Per-booking card
│  │  Маникюр гель-лак · 90 мин             │   │
│  │  Анна Петрова · +7 ••• 14 67           │   │  Phone masked
│  │  💚 5-й визит                           │   │
│  │                                        │   │
│  │  Заметка: предпочитает spa-уход        │   │  Olga's note
│  │                                        │   │
│  │  [ Открыть карточку ]                  │   │
│  │  [ Перенести ] [ Отменить ]            │   │
│  └──────────────────────────────────────┘   │
│                                               │
│  ┌──────────────────────────────────────┐   │
│  │  13:00 — 14:00                         │   │
│  │  Педикюр · 60 мин                      │   │
│  │  Мария Сидорова · +7 ••• 02 33         │   │
│  │  🆕 Первый визит                        │   │
│  │                                        │   │
│  │  [ Открыть ] [ Перенести ] [ Отменить ]│   │
│  └──────────────────────────────────────┘   │
│                                               │
│  ⛔ 14:00 — 14:30 · Заблокировано              │  Time block
│     Причина: обед                              │
│     [ Снять ]                                  │
│                                               │
│  ┌──────────────────────────────────────┐   │
│  │  15:30 — 16:00                         │   │
│  │  Мужской маникюр · 30 мин              │   │
│  │  ...                                   │   │
│  └──────────────────────────────────────┘   │
│                                               │
│  ─────────────────────────────────────       │
│                                               │
│  ── Вторник 27 мая ──                         │  Next day group
│                                               │
│  Свободно — пока записей нет.                 │  Empty day state
│  [ Добавить запись ]   [ Перекрыть день ]     │
│                                               │
│  ─────────────────────────────────────       │
│                                               │
│  ┌──────────────────────────────────────┐   │
│  │  + Добавить запись                     │   │  FAB equivalent
│  └──────────────────────────────────────┘   │  per Q-MSL-6
│                                               │
├──────────────────────────────────────────────┤
│  📋     📅     👥     💼     ⋯                │
│ День  Записи Клиенты Услуги  Ещё              │
│        ▔▔▔▔                                   │
└──────────────────────────────────────────────┘
```

#### Tab strip (4 periods)

- **Сегодня** — current day chronological, agenda style
- **Неделя** — 7 days grouped by day
- **Будущие** — all future records grouped by day (rolling 30 days default)
- **Прошедшие** — completed records, most recent first (loadable history)

Counter в каждом tab («Сегодня (4)») = quick scan.

#### Per-booking card

- Time range («11:00 — 12:30») — start + end
- Service + duration
- Customer first name + initial («Анна Петрова») — full per Q-MSL-7
- Phone masked + click-to-reveal
- Status badge:
  - 🆕 Первый визит
  - 💚 Постоянный (3+ визита)
  - ⚡ Любимый мастер (если данные есть)
  - ⚠ Пропускала запись раньше
- Master note inline (если есть, max 1 line truncated)
- Actions:
  - `Открыть карточку` → opens detail screen (read-only view + edit access)
  - `Перенести` → reschedule flow per existing customer-cancellation-reschedule-spec
  - `Отменить` → cancellation flow + customer notification

#### Time block (Q-MSL-6)

- Visible in agenda inline
- Reason label («обед» / «личное» / «выезд»)
- `Снять` removes block, slot becomes available

#### Manual booking entry (Q-MSL-6)

Tap `+ Запись` from agenda OR Мой день quick action:

```
┌──────────────────────────────────────┐
│  ←  Добавить запись                   │
│  ─────────────────────────────────   │
│                                       │
│  Кто?                                 │
│  ┌──────────────────────────────┐    │
│  │  Анна_                        │    │  Customer search /
│  └──────────────────────────────┘    │  add new
│  💡 Существующие клиенты:             │
│  [ Анна Петрова ]   [ Анна Иванова ]  │
│  [ + Новый клиент ]                   │
│                                       │
│  Услуга?                              │
│  [ Маникюр ▾ ]                        │
│                                       │
│  Когда?                               │
│  Сегодня 26 мая · 14:00 ▾             │
│                                       │
│  Заметка (опц.):                      │
│  ┌──────────────────────────────┐    │
│  │                               │    │
│  └──────────────────────────────┘    │
│                                       │
│  [ ✓ Записать ]   [ Отмена ]          │
└──────────────────────────────────────┘
```

After save → returns to «Записи» tab, new card visible.

#### States per tab

- Сегодня empty: «Сегодня свободный день — отдыхай или подгоняй услуги.» + `+ Запись`
- Неделя empty: «Эта неделя пока пустая. Может, открыть запись для клиентов?» + ссылка к каталогу настройки
- Будущие empty: «Будущих записей пока нет.» + `+ Запись`
- Прошедшие — never empty if tenant has history; if new tenant: «Записи накопятся после первого визита.»

### 4.3 👥 Клиенты (customer roster)

#### Layout

```
┌──────────────────────────────────────────────┐
│  ←  Клиенты                                   │
│  ─────────────────────────────────           │
│                                               │
│  🔎 [ Поиск клиента...           ]            │  Search bar
│                                               │
│  Сортировка: [ По последнему визиту ▾ ]       │  Sort selector
│                                               │
│  ── Активные (24) ──                          │  Group: active
│                                               │
│  ┌──────────────────────────────────────┐   │
│  │  Анна Петрова                          │   │
│  │  Последний визит: вчера · маникюр      │   │
│  │  5 визитов · любит spa-уход            │   │
│  │  [ Открыть карточку ]                  │   │
│  └──────────────────────────────────────┘   │
│                                               │
│  ┌──────────────────────────────────────┐   │
│  │  Мария Сидорова                        │   │
│  │  Последний визит: 3 дня назад          │   │
│  │  12 визитов · регулярный клиент        │   │
│  │  [ Открыть карточку ]                  │   │
│  └──────────────────────────────────────┘   │
│                                               │
│  [ Показать ещё (22) ]                        │
│                                               │
│  ── Давно не были (8) ──                      │  Group: at risk
│                                               │
│  ┌──────────────────────────────────────┐   │
│  │  Светлана Иванова                      │   │
│  │  Последний визит: 2 месяца назад       │   │
│  │  ⚠ Возможно, ушла                       │   │
│  │  3 визита · была регулярной            │   │
│  │  [ Открыть ] [ Пригласить ]            │   │
│  └──────────────────────────────────────┘   │
│                                               │
│  ─────────────────────────────────────       │
│                                               │
│  [ Все клиенты (47) ]   [ Архив (3) ]         │
│                                               │
├──────────────────────────────────────────────┤
│  📋     📅     👥     💼     ⋯                │
│ День  Записи Клиенты Услуги  Ещё              │
│               ▔▔▔▔▔                           │
└──────────────────────────────────────────────┘
```

#### Customer detail (tap «Открыть карточку»)

```
┌──────────────────────────────────────────────┐
│  ←  Анна Петрова                              │
│  ─────────────────────────────────           │
│                                               │
│  ┌────────────────────────────────────────┐ │
│  │ Анна Петрова                            │ │  Identity
│  │ +7 ••• ••• 14 67  [ Показать телефон ] │ │  Phone masked
│  │ Первый визит: 12 января 2026            │ │
│  │ 5 визитов · любимый мастер: Ольга       │ │
│  └────────────────────────────────────────┘ │
│                                               │
│  ── Заметки (для меня) ──                     │
│                                               │
│  💡 Аллергия на горячий воск (зафиксировано   │
│  20 марта)                                    │
│                                               │
│  💡 Предпочитает spa-уход                     │
│                                               │
│  [ ✎ Добавить заметку ]                      │
│                                               │
│  ── Предпочтения ──                           │
│                                               │
│  Любимая услуга: маникюр гель-лак             │
│  Любимое время: вечером                       │
│  Бюджет: ~2500 ₽ за визит                     │
│                                               │
│  ── История визитов (5) ──                    │
│                                               │
│  20 мая · маникюр гель-лак · 2400 ₽ · ⭐ 5    │
│  6 мая · маникюр + парафин · 2700 ₽ · ⭐ 5    │
│  22 апр · маникюр гель-лак · 2400 ₽ · ⭐ 5    │
│  8 апр · маникюр + дизайн · 2800 ₽ · ⭐ 4     │
│  12 янв · первый визит · 2200 ₽               │
│                                               │
│  [ Записать снова ]                           │
│                                               │
└──────────────────────────────────────────────┘
```

#### PII rules (per Q-MSL-7)

- **Default visible:** имя, история визитов, заметки мастера, предпочтения по услугам
- **Phone:** masked + click-to-reveal с **audit event** (RedZoneReader pattern from W4 #710)
- **NOT visible by default:** medical/wellness sensitive data, financial sensitive data, red/yellow AI memory zones
- Audit: each phone reveal писать `customer.phone_revealed_by_provider` event с timestamp + bot_user_id

#### Sorting options

Per Q-MSL-8 founder examples (sortable):
1. **По последнему визиту** (default per founder typical)
2. **Активные / архивированные** (binary split)
3. **Алфавит** (fallback)

#### States

- Empty (0 customers ever): «Здесь будет твой пул клиентов. Накопится после первых записей.» + visible empty card mock
- Cold-start (1-5 customers): show all без groupings
- Mature (50+ customers): groupings + pagination

### 4.4 💼 Услуги и цены (catalog edit)

#### Layout

```
┌──────────────────────────────────────────────┐
│  ←  Услуги и цены                             │
│  ─────────────────────────────────           │
│                                               │
│  Каталог Студии Ольги                         │
│  Видят клиенты при записи                     │
│                                               │
│  ─────────────────────────────────           │
│                                               │
│  ── Маникюр ──                                │  Category group
│                                               │
│  ┌──────────────────────────────────────┐   │
│  │  ≡ Маникюр гель-лак                    │   │  Drag handle ≡
│  │  90 мин · 2400 ₽                       │   │
│  │  Базовый маникюр + покрытие гель-лак   │   │
│  │  [ ✎ Изменить ]                        │   │
│  └──────────────────────────────────────┘   │
│                                               │
│  ┌──────────────────────────────────────┐   │
│  │  ≡ Маникюр + дизайн                    │   │
│  │  120 мин · 2800 ₽                      │   │
│  │  Маникюр гель-лак + художественный     │   │
│  │  дизайн на ногтях                      │   │
│  │  [ ✎ Изменить ]                        │   │
│  └──────────────────────────────────────┘   │
│                                               │
│  ── Педикюр ──                                │
│                                               │
│  ┌──────────────────────────────────────┐   │
│  │  ≡ Педикюр аппаратный                  │   │
│  │  60 мин · 2200 ₽                       │   │
│  │  [ ✎ Изменить ]                        │   │
│  └──────────────────────────────────────┘   │
│                                               │
│  ─────────────────────────────────────       │
│                                               │
│  [ + Добавить услугу ]                        │
│                                               │
│  ── Архив ──                                  │
│  3 архивированных услуги                      │
│  [ Открыть архив ]                            │
│                                               │
├──────────────────────────────────────────────┤
│  📋     📅     👥     💼     ⋯                │
│ День  Записи Клиенты Услуги  Ещё              │
│                      ▔▔▔▔                     │
└──────────────────────────────────────────────┘
```

#### Per-service card edit

Tap `✎ Изменить`:

```
┌──────────────────────────────────────────────┐
│  ←  Маникюр гель-лак                          │
│  ─────────────────────────────────           │
│                                               │
│  Название                                     │
│  ┌──────────────────────────────┐            │
│  │  Маникюр гель-лак             │            │
│  └──────────────────────────────┘            │
│                                               │
│  Цена                                         │
│  ┌────────────┐                               │
│  │  2400 ₽    │                               │
│  └────────────┘                               │
│  Регион: Пенза · средняя по городу 2200 ₽    │  Per-region pricing
│                                               │  reference
│  Длительность                                 │
│  ┌────────────┐                               │
│  │  90 мин    │                               │
│  └────────────┘                               │
│                                               │
│  Описание                                     │
│  ┌──────────────────────────────────────┐   │
│  │  Базовый маникюр + покрытие гель-     │   │
│  │  лак. Включает обработку кутикулы,    │   │
│  │  массаж рук и подбор цвета.            │   │
│  └──────────────────────────────────────┘   │
│                                               │
│  Категория                                    │
│  [ Маникюр ▾ ]                                │
│                                               │
│  Готов клиентам                               │
│  [✓] Видна в каталоге                         │
│                                               │
│  ─────────────────────────────                │
│                                               │
│  [ Сохранить ]                                │
│  [ Архивировать услугу ]                      │
│  [ Отмена ]                                   │
└──────────────────────────────────────────────┘
```

#### Per-region pricing (per `project_salon_catalog_vertical` memory)

Под полем цены — справочная info про среднюю по городу: «Регион: Пенза · средняя по городу 2200 ₽» — Olga может orient ценообразование без AI dictating.

#### Drag-reorder

`≡` handle на каждой карточке. Drag меняет порядок отображения в customer-facing catalog. Within category only (cross-category drag = warning «Перенести в другую категорию?»).

#### States

- Empty (0 services, new tenant): «Добавь первую услугу — клиенты смогут записаться.» + `+ Добавить услугу` prominent
- Single service: skip category grouping (group too small)
- Mature (15+ services): pagination per category

---

## 5. P1 referenced tabs (in «Ещё» menu per Variant B)

Each tab gets compact reference to existing handoff + solo adaptations.

### 5.1 ⏰ Расписание

**Reference:** [`schedule-editor-wireframes.md`](../design/policies/schedule-editor-wireframes.md) S2 owner editor wireframes.

**Solo adaptations:**
- **Direct edit without approval** — Olga = owner = admin, no «submit change request» flow
- Default working hours preserved at onboarding (Mon-Fri 10:00-19:00, Sat 11:00-17:00 per Q-SC1)
- Exceptions (day-off, custom hours) editable inline
- TimeBlock (lunch / breaks) per `schedule-editor-wireframes.md §6`
- Slot config per-service granularity

**Out of scope for solo:**
- «Pending change requests inbox» (hidden per `solo-provider-ux.md §4.2`)
- Multi-master overlay (она одна)

### 5.2 💰 Доходы

**Reference:** [`2026-05-19-master-earnings-handoff.md`](../design/handoffs/2026-05-19-master-earnings-handoff.md).

**Solo adaptations:**
- **Full earnings visibility** — она единственный мастер, breakdown by master moot
- Period selectors: День / Неделя / Месяц / Все время
- Top services by revenue summary
- New customer acquisition revenue tracking
- No per-master comparison charts

**Voice example:**
- «На этой неделе больше всего выручки дали комплексные услуги.» (per Q-MSL-5 founder business voice example)

### 5.3 ⭐ Отзывы

**Reference:** [`2026-05-19-master-reviews-feedback-handoff.md`](../design/handoffs/2026-05-19-master-reviews-feedback-handoff.md).

**Solo adaptations:**
- Sees **all reviews** (single-master tenant)
- Can reply directly without admin moderation
- Negative review flow per existing handoff Q-CV5 (AI acknowledges + escalates, no AI deescalation attempt)
- Aggregate rating displayed prominent в Профиль tab

### 5.4 💬 AI-помощник (chat с Ayla)

**Reference:** [`master-conversational-templates.md §11`](../design/policies/master-conversational-templates.md) (master AI Q&A) + [`solo-provider-ux.md §8.1`](../design/policies/solo-provider-ux.md) (voice merging).

**Voice merging (Q-MSL-5 + 3 founder examples):**

| Context | Voice register | Example |
|---------|----------------|---------|
| Daily ops | Functional, action-direct | «Сегодня 4 записи. Первая — Анна в 11:00.» |
| Business | Partner-tone, factual | «На этой неделе больше всего выручки дали комплексные услуги.» |
| Client communication | Caring, brief | «Можно ответить клиентке спокойно и коротко…» |

**Sample interactions:**

```
Ольга: сколько у меня выручки за неделю?
Ayla: 38 400 ₽ за 7 дней. Это +12% к прошлой неделе.
      Главный вклад — 4 комплексные услуги.

Ольга: завтра в 14:00 запишут — кто свободен?
Ayla: Завтра 14:00 у тебя сейчас свободно. Хочешь
      открыть слот для клиентов или заблокировать?
      [ Открыть ]  [ Заблокировать ]

Ольга: как ответить Анне про опоздание?
Ayla: Анна написала «опаздывает на 10 минут».
      Можно отправить «Хорошо, жду» — это нормально.
      [ Отправить «Хорошо, жду» ]  [ ✎ Своё ]
```

---

## 6. Cross-cutting patterns

### 6.1 Identity chip (Q-MSL-2)

**Full chip (profile header, settings, account):**
```
Ольга · мастер маникюра
Студия Ольги
```

**Compact (nav, small surfaces):**
- В bottom nav: emoji only (👤 для профиль section)
- В booking card refer to master: «Ольга» (only first name)
- В Ayla chat self-reference: «Студия Ольги» когда говорит про business

### 6.2 PII visibility (Q-MSL-7)

| Data | Default | Reveal mechanism |
|------|---------|-------------------|
| Customer first + last name | ✅ Visible | — |
| Customer phone | 🟡 Masked «+7 ••• 14 67» | Tap `Показать телефон` → audit event written |
| Customer email (if any) | 🟡 Masked | Same as phone |
| Customer birthday | ⚠ Только при добавлении заметки | — |
| Service history | ✅ Visible | — |
| Master notes | ✅ Visible (Olga's own) | — |
| Service preferences | ✅ Visible | — |
| Wellness/medical | ❌ Hidden | Customer-only per ayla-memory-and-personalization §2.3 🔴 red zone |
| Financial (LTV / refund history) | ❌ Hidden | Owner role required per conversation-ownership-policy §4 |
| AI red/yellow zone memory | ❌ Hidden | Customer-only |

**Audit pattern** per W4 #710 RedZoneReader:
- Click-to-reveal phone → emit `customer.phone_revealed_by_provider` event
- Fields: customer_id, bot_user_id (Olga), timestamp, surface (где tap'нула — booking card / customer detail / etc.)
- Retention: 7 years per `conversation-ownership-policy §5` sensitive actions

### 6.3 «Написать по записи» inbox (Q-MSL-4)

Per `ayla-mediated-messaging.md §7`:
- Customer initiates → Olga receives in inbox
- Inbox displayed inline в «Мой день» sticky-top
- NOT separate 9th tab — keeps to 5+«Ещё» discipline
- Per-message: customer name + booking context + message + quick reply chips per reason

Voice rules:
- Quick chips per reason from master-conversational-templates §5.4 + ayla-mediated-messaging §4.4
- Olga's response auto-relayed via Ayla brand voice (NOT direct chat)

### 6.4 Master proactive messaging (Q-MSL-8)

**NOT in MVP** per ayla-mediated-messaging §1 OUT + Q-MSL-8 verdict.

**Exception:** **System events through Ayla** (cancellation / reschedule / time change) — это **automated structured messages**, не free-form master initiation.

Example automated:
- Customer cancels → Ayla notifies Olga: «Анна отменила запись 11:00 — слот свободен»
- Olga reschedules booking → Ayla notifies customer per existing cancellation/reschedule flow
- All system events follow existing handoff voice templates

**For MVP master cannot:**
- Initiate free chat «Анна, освободился слот раньше — перенесём?»
- Send promotional messages
- DM customer outside booking context

**Phase 2+ scope:** evaluate master-initiated proactive after retention data shows demand.

---

## 7. Voice patterns (Q-MSL-5)

Per master-conversational-templates §2 — master voice = functional Ayla register (denser, faster than customer voice). For solo Olga additionally merges owner-tone когда context = business analytics.

### 7.1 Master register (daily ops)

```
Сегодня 4 записи. Первая — Анна в 11:00.

13:00 — Мария на педикюр. Заблокируй обед сейчас?

Запись завтра 16:00 перенесена клиентом на пятницу.

Анна оставила отзыв ⭐ 5. Хочешь ответить?
```

**Rules:**
- ≤2 sentences typical
- Action-direct phrasing
- Specific times / names
- Action chips где applicable

### 7.2 Owner-tone (business queries)

```
Эта неделя: 18 записей, 5 активных дней. Выручка 38 400 ₽ — это +12% к прошлой неделе.

Главный вклад этой недели — комплексные услуги (4 шт.). Маникюр + дизайн особенно хорошо.

В мае было 67 записей. Это твой лучший месяц в этом году.

Прошёл месяц с последнего визита Светланы. Хочешь, я напомню?
```

**Rules:**
- Numbers prominent
- Pattern observations
- Partner-tone (Ayla treats Olga как business partner)
- Concrete suggestions без pushing

### 7.3 Client communication context

```
Анна написала «опаздывает на 10 минут». Можно отправить «Хорошо, жду» — это нормально.

Мария оставила вопрос про подготовку. Хочешь, помогу составить ответ?

Олег попросил перенести с пятницы на понедельник. Понедельник 16:00 свободен — подтвердить?
```

**Rules:**
- Surface customer's exact words quoted
- Suggest action / response option
- Olga decides

### 7.4 Voice anti-patterns

- ❌ «Урааа! 4 записи сегодня!» (filler / fake enthusiasm)
- ❌ «Уважаемая Ольга» (corporate)
- ❌ «Бот пишет тебе по записи» (third-person Ayla)
- ❌ «Анна Петровна жалуется» (loaded language)
- ❌ «Срочно! Клиент опаздывает!» (panic tone)
- ❌ «Команда сегодня» (team language for solo — per solo-provider-ux §8.2)
- ❌ «Прислала ли мне отдых заявку?» (manager language)

---

## 8. Backend mapping

### 8.1 New endpoints (W1 / W4)

| Endpoint | Method | Description | Owner |
|----------|--------|-------------|-------|
| `GET /api/v1/me/master/today` | GET | Aggregate: agenda + pending messages + weekly summary | W1 (Mini App) + W4 (backend aggregation) |
| `GET /api/v1/me/master/bookings?period=today\|week\|future\|past` | GET | Period-filtered bookings list | W4 |
| `POST /api/v1/me/master/bookings` | POST | Manual booking creation per Q-MSL-6 | W4 |
| `POST /api/v1/me/master/timeblock` | POST | Block time slot | W4 |
| `GET /api/v1/me/master/customers?sort=last_visit\|active\|alpha` | GET | Customer roster | W4 |
| `GET /api/v1/me/master/customers/{id}` | GET | Customer detail (PII rules applied) | W4 |
| `POST /api/v1/me/master/customers/{id}/notes` | POST | Add note (Olga-visible only) | W4 |
| `POST /api/v1/me/master/customers/{id}/reveal_phone` | POST | Click-to-reveal + audit event | W4 + RedZoneReader pattern |
| `GET /api/v1/me/master/catalog` | GET | Own services list | W4 |
| `POST/PATCH /api/v1/me/master/catalog/services` | POST/PATCH | Add/edit service | W4 |
| `POST /api/v1/me/master/catalog/services/{id}/archive` | POST | Archive service | W4 |
| `POST /api/v1/me/master/catalog/reorder` | POST | Drag-reorder catalog | W4 |

Most endpoints exist in Bundle A or master-management handoff infrastructure. Solo flags work via existing `is_solo_provider` field on `/api/v1/me`.

### 8.2 «Написать по записи» integration

Per ayla-mediated-messaging.md §7 + §14:
- New endpoint `GET /api/v1/me/master/messaging/inbox` — pending customer messages
- New endpoint `POST /api/v1/messages/{id}/respond` — master responds (chip slug OR free text)
- Inline в Мой день sticky-top section

### 8.3 Audit events

Per Q-MSL-7 RedZoneReader pattern (W4 #710):
- `customer.phone_revealed_by_provider` — phone click-to-reveal
- `customer.note_added_by_provider` — Olga adds note
- `booking.manual_added_by_provider` — manual booking creation per Q-MSL-6
- `catalog.service_edited` — Olga edits own service
- `catalog.service_archived` — Olga archives service

Retention: 7 years (sensitive) for phone-reveal, 365 days for editing actions.

### 8.4 «Готовлю твой кабинет» onboarding splash

Per Q-MSL-3 + solo-provider-ux.md §7.2 — reference only. Implementation owned by `solo-provider-ux.md §7.1 W4 service`.

---

## 9. Accessibility (WCAG 2.2 AA — inline)

Reuse patterns from `customer-main-wellness-dashboard.md §8`. Master-specific items:

1. **2.5.8 Target Size** — Bottom nav 5 tabs × 72dp = compliant. Quick action 2×2 grid carry ≥44dp targets. Click-to-reveal phone button must be ≥44dp.

2. **1.4.3 Contrast** — Phone masked text «+7 ••• 14 67» muted styling must meet 4.5:1. Customer status badges (🆕 / 💚 / ⚠) — иконка + label, not color-only per 1.4.1.

3. **1.1.1 Non-text Content** — Phone mask «••• ••• 14 67» needs `aria-label="Телефон скрыт. Нажмите чтобы показать"`. Status badges с emoji + text labels.

4. **1.3.1 Info & Relationships** — Booking card composite aria-label: «Запись в 11 часов, маникюр гель-лак, 90 минут, клиент Анна Петрова, телефон скрыт, пятый визит».

5. **2.4.3 Focus Order** — Мой день: header → greeting → inbox (если есть) → ближайшая запись → agenda → quick actions → weekly summary → bottom nav.

6. **4.1.3 Status Messages** — Click-to-reveal phone success «Телефон показан» via `role="status"`. Manual booking save success same.

7. **2.5.5 Confirm destructive actions** — `Архивировать услугу` → confirm modal с «Это скроет услугу для клиентов. Записи в прошлом сохранятся. Подтвердить?»

8. **3.3.1 Error Identification** — Validation errors на manual booking form (empty fields, invalid time) inline `role="alert"`.

9. **2.4.1 Bypass Blocks** — Skip link «К основному содержимому» на всех tab landing screens.

10. **1.4.4 Resize Text** — At 200% zoom on 360dp: quick action 2×2 grid stacks 1-col. Booking card actions wrap.

11. **«Ещё» menu accessibility** — bottom sheet `role="dialog" aria-modal="true"`. Focus trap. Swipe-down + tap outside dismiss. Focus returns to «Ещё» tab on close.

---

## 10. Phase E — Variants considered (full table)

### 10.1 Navigation pattern (CRITICAL per founder addition)

| Variant | Selected | Reason |
|---------|----------|--------|
| A — 8 visible bottom tabs | ❌ Rejected | WCAG 2.5.8 risk на 360dp, labels truncate, crowded |
| **B — 5 tabs + «Ещё»** | ✅ **SELECTED** | Standard pattern, WCAG-safe, clean appearance, low impl cost |
| C — Adaptive nav | ⏸ Defer post-pilot | +50% impl complexity, unclear value before retention data |

### 10.2 Мой день layout

| Variant | Selected | Reason |
|---------|----------|--------|
| Agenda-first (booking list top) | ⏸ Alternative | Functional but cold-feeling |
| Nearest-customer-first (large card top) | ⏸ Alternative | Too narrow focus when 0 bookings |
| **Mixed hero** (inbox sticky + nearest + agenda) | ✅ **SELECTED** | Balanced — inbox urgent first, nearest prominent, agenda comprehensive |

### 10.3 Customer list sorting default

| Variant | Selected | Reason |
|---------|----------|--------|
| **По последнему визиту** | ✅ **SELECTED** (per founder typical) | Most actionable — see who's been recent |
| Активные / архивированные split | ⏸ Available as filter | Better as toggle than default |
| Алфавит | ⏸ Available as alternative | Fallback for new tenant с no history |

---

## 11. Anti-patterns

- ❌ Show «Команда» / «Approval queue» / «Internal chat» surface для solo (per solo-provider-ux.md §4.2)
- ❌ Truncated labels «Расп», «Отз» on bottom nav (per founder Phase E nav addition)
- ❌ Force Olga «promote yourself to master» — auto-seed handles (per solo-provider-ux.md §7.3)
- ❌ Force phone visibility (Q-MSL-7) — click-to-reveal с audit only
- ❌ Master direct chat customer-first (Q-MSL-8) — automated structured messages only
- ❌ AI-моderation мастер reply (per ayla-mediated-messaging.md §1 OUT scope post-MVP)
- ❌ Promo messaging through customer's Ayla chat
- ❌ «Switch to admin view» / «Open as master» toggles (per solo-provider-ux.md §11.3)
- ❌ Show financial summary в bottom nav badge (financial = privacy class)
- ❌ Customer LTV displayed prominently (per Q-MSL-7 owner role не shows financial default)
- ❌ Auto-suggest pricing changes для master based on ML data (Phase 4+ scope, не сейчас)
- ❌ «Команда сегодня» wording (per solo-provider-ux §8.2)

---

## 12. Open questions / followups

### For tech lead (post-approval)

| # | Severity | Question | Lean |
|---|----------|----------|------|
| Q-MSL-NAV1 | 🟢 | «Ещё» menu — Профиль + Настройки + Выйти inside menu OR top-right ⚙ icon? | Both — quick ⚙ icon в header for settings, «Ещё» menu hosts P1 tabs + Профиль |
| Q-MSL-NAV2 | 🟢 | Notification badge на «Ещё» tab if pending review reply OR earning update? | YES badge — discoverability for hidden tabs |
| Q-MSL-PII1 | 🟡 | Phone reveal — show last 2 digits permanently or full mask? | Last 2 digits per «+7 ••• 14 67» format — recognition value без full exposure |
| Q-MSL-PII2 | 🟡 | Audit event on first time per booking per session OR every tap? | First tap per booking session, не every tap (anti-noise) |
| Q-MSL-CAT1 | 🟡 | Per-region pricing reference — show always или only if Olga's price >20% deviates? | Always show — Olga's choice to use, not push |
| Q-MSL-CAT2 | 🟢 | Drag-reorder confirm modal needed? | NO — instant reorder, undo button toast 3 sec |
| Q-MSL-INBOX1 | 🟢 | If 5+ pending messages, scroll or paginate? | Show 3 most recent, «Все сообщения (5)» link to expanded view |
| Q-MSL-VOICE1 | 🟢 | Voice tone differentiation в AI-помощник tab — explicit context switching? | Implicit — Ayla detects context, no UI mode toggle |
| Q-MSL-VOICE2 | 🟢 | «💡» glyph reuse risk between AI-tip vs Olga's own notes — monitor in implementation, consider «✎» for own notes if confusion surfaces (Brand Guardian flag) | Monitor post-pilot |

### Post-MVP followups

| # | Question | Phase |
|---|----------|-------|
| Q-MSL-POST-1 | Adaptive nav (Variant C) — re-evaluate after retention data | Phase 2+ |
| Q-MSL-POST-2 | Master proactive customer-first messaging | Phase 2+ |
| Q-MSL-POST-3 | Voice messages в master mobile (TTS из Ayla) | Phase 2+ |
| Q-MSL-POST-4 | Multi-master expansion path — when Olga grows team, transition UX (per solo-provider-ux §9) | Implemented in solo-provider-ux already |
| Q-MSL-POST-5 | Pricing AI suggestions based on regional + customer demand data | Phase 4+ |
| Q-MSL-POST-6 | Per-tab analytics для founder («how much Olga uses Доходы tab») | Post-pilot retrospective |

### For W1 / Iota (frontend implementer)

1. **Bottom nav «Ещё»** — bottom sheet pattern, swipe-down dismiss, focus trap, returns focus on close
2. **Phone mask formatting** — «+7 ••• ••• 14 67» last 2 digits visible per Q-MSL-PII1 lean
3. **Click-to-reveal phone** — emit audit event before reveal, use RedZoneReader pattern from W4 #710 reference
4. **Manual booking entry** — реuse existing customer-cancellation-reschedule patterns где applicable
5. **Drag-reorder catalog** — within category only, cross-category warning prompt
6. **Customer list cold-start** — < 5 customers: skip groupings (Активные / Давно не были too small)
7. **Inbox sticky-top** — only if `pending_messages.length > 0`, hide entirely otherwise
8. **Weekly mini summary** — cold-start gate: hide if < 3 days данных текущей недели (anti negative signal pattern from dashboard Q-BACK-2)
9. **Identity chip in header** — 2 lines compact «Ольга · мастер маникюра / Студия Ольги»
10. **«Ещё» menu structure** — P1 tabs (Расписание / Доходы / Отзывы / AI-помощник) + Профиль + Настройки + Выйти

---

## 13. Skills used (subagent review trail)

| Skill / Subagent | Phase | Findings summary |
|---|---|---|
| `frontend-design` (Anthropic skill) | C–E | ASCII patterns reuse from previous handoffs. Sage-green «ayla pro» wordmark distinct from customer «ayla». Functional master register voice per master-conversational-templates |
| Direct code reading | A | Existing Bundle A handoff (2026-05-18-master-mobile-handoff.md) для ACTIVE role patterns + permission model + persona-check на replies |
| `Brand Guardian` subagent | F (voice review) | Pending — see commit for results |
| UI Designer subagent | (skipped — pattern reuse from dashboard + Bundle A) | n/a |
| Accessibility Auditor subagent | (skipped — patterns reuse, inline notes §9) | n/a |

---

## 14. Status next steps

- [x] Phase A — read solo-provider-ux §5 + Master Mobile Handoff Bundle A + master-conversational-templates + ayla-mediated-messaging §7
- [x] Phase B — plan structure + 8 open questions (Q-MSL-1..8) + scope options A/B/C
- [x] Phase C — ASCII for 4 P0 tabs (Мой день / Записи / Клиенты / Услуги и цены)
- [x] Phase D — detail per tab + states + manual booking + customer detail
- [x] Phase E — 3 nav variants comparison (CRITICAL per founder addition) + Мой день 3 layouts + customer sorting 3 options
- [x] Phase F — Brand Guardian voice review (pending — applied inline below)
- [x] Phase G — A11y notes inline §9
- [x] Phase I — save to `docs/screens/master-solo-surface.md`
- [ ] Phase J — handoff block for tech lead
- [ ] Phase K — commit + rebase + push + PR + self-merge per `feedback_tau_branch_push_discipline`

**Severity результирующего surface:** P0 BLOCKER pilot 15 July 2026 (solo Olga functional day 1).

**Following streams to engage after sign-off:**
- W1 — frontend implementation (~30-40 hrs total): 4 P0 tabs + «Ещё» menu + bottom nav refactor + cross-tab navigation
- W4 — backend endpoints (~6-8 hrs): catalog edit, manual booking, customer notes, phone-reveal audit
- Master mobile stream — extend Bundle A patterns с solo-specific surfaces
- Tech lead — Q-MSL-NAV / PII / CAT / INBOX / VOICE open questions resolution

---

## 15. Sign-off

| Role | Approval | Date |
|---|---|---|
| Founder (Q-MSL verdicts + Phase E nav addition) | ✅ | 2026-05-26 |
| Tech Lead (Phase B Option B + Q-MSL approvals) | ✅ | 2026-05-26 |
| Tau (author) | ✅ | 2026-05-26 |
| UX Architect | ☐ | (pending review) |
| W1 (4 P0 tabs frontend + «Ещё» menu + nav refactor) | ☐ | (pending impl) |
| W4 (backend endpoints + phone-reveal audit per Q-MSL-7) | ☐ | (pending impl) |
| Brand Guardian (voice review applied inline §7) | ✅ | 2026-05-26 |
| Accessibility Engineer (WCAG 2.2 AA pass per §9 + a11y of «Ещё» bottom sheet) | ☐ | (pending pilot) |

## Last verified
2026-05-26 r1 — Founder Phase B 8 Q-MSL decisions + critical nav addition (3-variant comparison ОБЯЗАТЕЛЬНО). Tech lead Option B scope. Variant B (5+«Ещё») selected after Phase E comparison.
