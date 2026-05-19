# Schedule Editor — Wireframes (S2 + S3)

| Field | Value |
|---|---|
| **Date** | 2026-05-18 r1 |
| **Owner** | UX-architect skill (Chief UX Architect supervisory) |
| **Status** | Draft for engineering — implementation handoff |
| **Surfaces** | S2 (Owner schedule editor, Mini App) + S3 (Master mobile schedule view + editor) |
| **Implementation horizon** | 5–10 days |
| **Source of truth** | [`../handoffs/2026-05-18-schedule-management-handoff.md`](../handoffs/2026-05-18-schedule-management-handoff.md) (fields, states), [`../handoffs/2026-05-18-master-mobile-handoff.md`](../handoffs/2026-05-18-master-mobile-handoff.md) §M3 (master surface) |
| **Locked copy** | [`owner-conversational-templates.md`](./owner-conversational-templates.md) §6.5–6.8, [`master-conversational-templates.md`](./master-conversational-templates.md) §5.11 |
| **Nav constraints** | [`information-architecture.md`](./information-architecture.md) — 5-tab bottom nav, state-adaptive home |

---

## 1. Why this exists + scope boundaries

### Why
Schedule-management-handoff defines fields, state machine, and macro screens (S1–S6) at handoff level. Engineering needs **tap-by-tap wireframes with explicit interactions** for the two surfaces that ship first:

- **S2 — Owner-side weekly editor** (Mini App owner surface; per-master working hours + exceptions + time blocks + slot config + pending change-request inbox)
- **S3 — Master-side mobile view + change-request flow** (master MAX Mini App; read schedule + submit ScheduleChangeRequest)

These are the two interactive editors most-critical for ~40% template-path cohort that has NO YClients — without them, slot computation has no data and the bot is useless for those salons.

### In scope
- ASCII wireframes for every key state of S2 and S3 (empty, populated, editing, modal-open, error, pending inbox)
- Per-wireframe interaction notes (tap targets, save, cancel, validation)
- Reusable patterns: date picker, time picker, modal anatomy
- Edge cases: master on vacation, slot conflicts, multi-master filtering
- A11y baseline (WCAG 2.2 AA)
- Open questions Q-SW1..Q-SWn for PM/eng review

### Out of scope (defer to other docs)
- S1 (main schedule grid week view) — already designed in schedule-management-handoff §3
- S4 (manual booking entry) + S5 (time block standalone modal) + S6 (master change-request approval bot DM) — designed in schedule-management-handoff §4–§7
- YClients sync conflict resolution UI (Phase 3 per handoff §19)
- Recurring exceptions, multi-station, schedule templates (Phase 4 v1.1+)
- Customer-side Mini App impact (in customer-first-time-handoff)
- Backend slot algorithm — engineering owns; we only specify input editor

### Out-of-scope items intentionally CALLED OUT inline
Where a wireframe brushes against an out-of-scope concern (e.g., owner taps «manual booking» FAB on S2) the wireframe shows the entry point but defers the modal anatomy to the source handoff.

---

## 2. S2 — Owner schedule editor: surface tree

S2 lives inside the **owner Mini App** at the «Расписание» tab. It is one route — `/owner/schedule` — with internal modes (Weekly grid / Working hours / Exceptions / Slot config / Requests inbox) selected by a sticky segmented control near the top.

### Route map (Phase 1 MVP)

```
/owner/schedule
  ├─ default mode: ?view=week (Weekly grid)
  ├─ ?view=hours          (Working hours inline editor — per master)
  ├─ ?view=exceptions     (Exception list + add modal)
  ├─ ?view=slots          (SlotConfig — tenant-wide buffer / lead-time)
  ├─ ?view=requests       (Pending ScheduleChangeRequests inbox)
  ├─ modal=add-exception
  ├─ modal=add-time-block
  └─ modal=request-detail&id=<uuid>
```

### Wireframe inventory (S2)

| # | State | Wireframe |
|---|---|---|
| W2-A | Empty — no working hours yet (first-run) | §2.1 |
| W2-B | Populated weekly grid (single master, default) | §2.2 |
| W2-C | Per-master tab/selector — Anna selected | §2.3 |
| W2-D | Working-hours inline editor (full week) | §2.4 |
| W2-E | Add ScheduleException modal | §2.5 |
| W2-F | Add TimeBlock modal | §2.6 |
| W2-G | SlotConfig panel (tenant-wide) | §2.7 |
| W2-H | Pending ScheduleChangeRequests inbox + request card | §2.8 |

---

### 2.1 — W2-A: Empty state, no working hours yet (first-run after onboarding)

```
┌────────────────────────────────────────────┐
│  ← Расписание                       [⋯]    │
├────────────────────────────────────────────┤
│  Мастер: [Анна Петрова ▾]                  │
│                                            │
│  [Неделя] [Часы] [Исключения] [Слоты] [⚠3] │  ← segmented; ⚠3 = pending requests
│                                            │
│  ┌──────────────────────────────────────┐  │
│  │                                      │  │
│  │     🌱                               │  │
│  │                                      │  │
│  │  Часы работы Анны ещё не заданы.    │  │
│  │                                      │  │
│  │  Помощник не сможет предлагать      │  │
│  │  клиентам слоты, пока не настроите. │  │
│  │                                      │  │
│  │  [Настроить часы работы →]          │  │  ← primary, routes to ?view=hours
│  │                                      │  │
│  │  [Скопировать с другого мастера]    │  │  ← secondary, opens BulkApply
│  │                                      │  │
│  └──────────────────────────────────────┘  │
│                                            │
├────────────────────────────────────────────┤
│  [🏠] [📅●] [💬] [📊] [👤]                 │  ← bottom nav (owner Mini App)
└────────────────────────────────────────────┘
```

**Interaction notes:**
- Tap «Настроить часы работы →» → switches to `?view=hours` (W2-D) for the currently selected master
- Tap «Скопировать с другого мастера» → opens BulkApply sheet listing already-configured masters; if none, button is disabled with tooltip «Нет мастеров с настроенным расписанием»
- Master selector at top: tap → bottom-sheet list of all masters; checkmark on currently selected
- ⚠ badge on segmented control = pending ScheduleChangeRequest count (visible only if requests exist)
- Empty state copy adheres to owner voice anchor «Confident + Premium-but-accessible» (§6.8) — no fear-monger, no exclamation

---

### 2.2 — W2-B: Populated weekly grid (single master, default view)

```
┌────────────────────────────────────────────┐
│  ← Расписание                       [⋯]    │
├────────────────────────────────────────────┤
│  Мастер: [Анна Петрова ▾]      [+ Бронь]   │
│  [Неделя ●] [Часы] [Искл] [Слоты] [⚠3]    │
│                                            │
│  ◂  19–25 мая 2026   ▸          [Сегодня]  │
│                                            │
│  Источник: 🌱 шаблон  •  обновлено 2 мин   │
├────────────────────────────────────────────┤
│       Пн19 Вт20 Ср21 Чт22 Пт23 Сб24 Вс25  │
│  ────┼────┼────┼────┼────┼────┼────┼────  │
│  09  │ 🌱 │ 🌱 │ 🌱 │    │    │    │ OFF  │
│  10  │ ░  │ ░  │ ░  │ ░  │ ░  │ ░  │ OFF  │
│  11  │ ▒М │ ░  │ ▒О │ ░  │ ░  │ ▒Ю │ OFF  │
│  12  │ ▒М │ ░  │ ▒О │ ░  │ ░  │ ░  │ OFF  │
│  13  │ 🍴 │ 🍴 │ 🍴 │ 🍴 │ 🍴 │ ░  │ OFF  │  ← lunch (TimeBlock or working_hours.lunch)
│  14  │ ░  │ ░  │ ░  │ ░  │ ░  │ ░  │ OFF  │
│  15  │ ░  │ ▒О │ ░  │ ░  │ ▒С │ ░  │ OFF  │
│  16  │ ▒А │ ░  │ ░  │ ▒А │ ░  │ ░  │ OFF  │
│  17  │ ▒А │ ░  │ ░  │ ▒А │ ░  │ 🌴 │ OFF  │  ← 🌴 = ScheduleException
│  18  │ ░  │ ░  │ ░  │ ░  │ ░  │ 🌴 │ OFF  │
│  19  │ закр│закр│закр│закр│закр│закр│ OFF  │
├────────────────────────────────────────────┤
│  Легенда: ░ свободно  ▒ занято  🍴 обед   │
│           🌴 искл  🌱 не работаем          │
├────────────────────────────────────────────┤
│  [🏠] [📅●] [💬] [📊] [👤]                 │
└────────────────────────────────────────────┘
```

**Interaction notes:**
- Tap any `░` cell → opens manual booking modal (S4, defined elsewhere)
- Tap any `▒` cell → opens BookingDetailSheet (existing, not in scope)
- Tap any `🍴` cell → opens TimeBlock detail with [Изменить] [Удалить] actions
- Tap any `🌴` cell → opens ScheduleException detail with [Изменить] [Удалить]
- Long-press on empty cell (mobile) → quick action menu: [+ Бронь] [+ Блок] [+ Исключение]
- Swipe row left/right → navigate previous/next week (haptic on date-stepper)
- «◂» / «▸» / «Сегодня» = standard PeriodNavigator behavior
- «[+ Бронь]» button top-right → opens S4 manual booking modal
- Master selector dropdown — if owner has multiple masters, current selection is sticky-remembered for session

---

### 2.3 — W2-C: Per-master tab selector (mobile bottom sheet)

When owner taps the master selector at top of W2-A/B, a bottom sheet rises:

```
┌────────────────────────────────────────────┐
│                                            │
│  ╭──────────────────────────────────────╮  │
│  │ Выберите мастера                  ✕  │  │
│  ├──────────────────────────────────────┤  │
│  │                                      │  │
│  │  ⦿ 👤  Анна Петрова                  │  │  ← radio; currently selected
│  │       пн–сб · 09–19                   │  │
│  │                                      │  │
│  │  ○ 👤  Олег Иванов                   │  │
│  │       пн–пт · 10–20                   │  │
│  │                                      │  │
│  │  ○ 👤  Юля Соколова                  │  │
│  │       ⚠ часы не заданы               │  │  ← yellow warning chip
│  │                                      │  │
│  │  ○ 👥  Все мастера (обзор)           │  │  ← combined view
│  │                                      │  │
│  │  ──────────────────────────────────  │  │
│  │  + Добавить мастера                  │  │  ← routes to onboarding Phase 4c
│  │                                      │  │
│  ╰──────────────────────────────────────╯  │
└────────────────────────────────────────────┘
```

**Interaction notes:**
- Tap a master row → sheet dismisses, weekly grid re-renders filtered to that master
- «⚠ часы не заданы» chip is non-tappable visual signal (master row itself is the tap target)
- «Все мастера» multi-master overlay — rows colour-coded per master; readability ≤ 4 masters
- «+ Добавить мастера» → routes out of schedule to onboarding Phase 4c (does NOT inline-add — adding a master is a multi-step flow)
- Sheet supports keyboard navigation: arrow keys move focus, Enter selects, Esc closes

---

### 2.4 — W2-D: Working-hours inline editor (per-master, full week)

```
┌────────────────────────────────────────────┐
│  ← Часы работы — Анна Петрова       [⋯]    │
├────────────────────────────────────────────┤
│  Мастер: [Анна Петрова ▾]                  │
│  [Неделя] [Часы ●] [Искл] [Слоты] [⚠3]    │
│                                            │
│  ── Рабочая неделя ──                      │
│                                            │
│  ┌──────────────────────────────────────┐  │
│  │ Пн  ☑  09:00 — 19:00      [⋯]        │  │
│  │     Обед  13:00 — 14:00              │  │
│  └──────────────────────────────────────┘  │
│  ┌──────────────────────────────────────┐  │
│  │ Вт  ☑  09:00 — 19:00      [⋯]        │  │
│  │     Обед  13:00 — 14:00              │  │
│  └──────────────────────────────────────┘  │
│  ┌──────────────────────────────────────┐  │
│  │ Ср  ☑  [09:00] — [19:00]  [⋯]        │  │  ← editing — time pickers visible
│  │     Обед  [13:00] — [14:00]          │  │
│  │     ☐ Без обеда                      │  │
│  └──────────────────────────────────────┘  │
│  ┌──────────────────────────────────────┐  │
│  │ Чт  ☑  09:00 — 19:00      [⋯]        │  │
│  └──────────────────────────────────────┘  │
│  ┌──────────────────────────────────────┐  │
│  │ Пт  ☑  09:00 — 19:00      [⋯]        │  │
│  └──────────────────────────────────────┘  │
│  ┌──────────────────────────────────────┐  │
│  │ Сб  ☑  10:00 — 17:00      [⋯]        │  │
│  └──────────────────────────────────────┘  │
│  ┌──────────────────────────────────────┐  │
│  │ Вс  ☐  выходной           [⋯]        │  │
│  └──────────────────────────────────────┘  │
│                                            │
│  [Скопировать на других мастеров]          │
│                                            │
│  ────────────────────────────────────────  │
│  [Отмена]              [Сохранить (изм.3)] │  ← sticky bottom; counter = dirty days
└────────────────────────────────────────────┘
```

**Interaction notes:**
- Each day-of-week row collapsed by default; tap row body OR `[⋯]` to expand-edit
- Checkbox `☑` = `is_working` toggle; unchecking sets day to «выходной», collapses time fields
- Time pickers: tapping `[09:00]` opens native time picker (iOS wheel / Android dial / web 24h dropdown); 15-min snap by default (per Q-SC2 lock)
- «Обед» row: two time pickers + `☐ Без обеда` checkbox to clear lunch range
- Validation inline: if `start_time ≥ end_time` → red helper «Конец рабочего дня должен быть позже начала»
- Validation inline: if `lunch_start < start_time` OR `lunch_end > end_time` → red helper «Обед должен быть в рабочее время»
- Validation inline: if `lunch_end ≤ lunch_start` → red helper
- «[Скопировать на других мастеров]» → opens BulkApply sheet with multi-select master list + confirm modal «Заменить расписание у выбранных мастеров?»
- Sticky bottom bar: [Отмена] dismisses with dirty-check (если dirty: «Не сохранять изменения?» confirm); [Сохранить (изм.N)] dispatches PATCH and toasts «Сохранено»
- On save, schedule grid re-fetches and any conflicting bookings get flagged (Mini App refetches — see schedule-management-handoff §16 edge case)
- WebApp.enableClosingConfirmation() while dirty

---

### 2.5 — W2-E: Add ScheduleException modal

Opens from W2-B grid (long-press → +Исключение) OR from `?view=exceptions` tab «+ Добавить».

```
┌────────────────────────────────────────────┐
│  ╭──────────────────────────────────────╮  │
│  │ Добавить исключение                ✕ │  │
│  ├──────────────────────────────────────┤  │
│  │                                      │  │
│  │ Мастер                                │  │
│  │ [Анна Петрова ▾]                     │  │
│  │                                      │  │
│  │ Тип                                   │  │
│  │ ⦿ 🌴 Отпуск                          │  │
│  │ ○ 🏥 Больничный                      │  │
│  │ ○ ⬜ Выходной                         │  │
│  │ ○ 🕐 Особый график                   │  │
│  │ ○ 🎉 Корпоратив / обучение           │  │
│  │                                      │  │
│  │ Период                                │  │
│  │ с [27.05.2026]  по [29.05.2026]      │  │
│  │                                      │  │
│  │ ┌──────── ВИДНО ТОЛЬКО ЕСЛИ ──────┐  │  │
│  │ │ тип = Особый график:            │  │  │
│  │ │   Часы:  с [11:00] до [17:00]   │  │  │
│  │ │   Обед:  с [13:30] до [14:00]   │  │  │
│  │ └──────────────────────────────────┘  │  │
│  │                                      │  │
│  │ Причина (внутри салона)              │  │
│  │ ┌──────────────────────────────────┐ │  │
│  │ │ Поездка в Сочи                   │ │  │
│  │ └──────────────────────────────────┘ │  │
│  │                                      │  │
│  │ ⓘ У Анны на эти даты 2 записи:      │  │
│  │   • 27 мая 10:00 — Мария И.         │  │
│  │   • 28 мая 14:00 — Светлана П.      │  │
│  │                                      │  │
│  │ Что делать с записями:                │  │
│  │ ⦿ Предложить клиенту перенос         │  │
│  │ ○ Автоматически отменить              │  │
│  │ ○ Передать другому мастеру            │  │
│  │                                      │  │
│  ├──────────────────────────────────────┤  │
│  │ [Отмена]              [Сохранить]    │  │
│  ╰──────────────────────────────────────╯  │
└────────────────────────────────────────────┘
```

**Interaction notes:**
- Type radio: choosing «Особый график» reveals time-range fields; other types hide them
- Date range: tapping either field opens native date picker; `по` defaults to `с` value when first set (single-day exception)
- Conflicting-bookings preview is read live as user picks dates (debounced 300 ms) — if no conflicts, that block is hidden
- Action radio default = «Предложить клиенту перенос» (least destructive; matches voice anchor «Calm + Direct, no fear-monger» §6.7)
- «Передать другому мастеру» is disabled with tooltip if no other master offers the same service for those times
- Save validation: end_date ≥ start_date; reason ≤ 280 chars
- On save: POST /api/v1/schedule/exceptions; modal dismisses; toast «Исключение сохранено. Уведомления клиентам ушли.» (or appropriate per action)
- Per Q-SC9 lock, NO «recurring exception» option in MVP; if owner needs multi-week, instructions guide manual repeat
- Cancel: dirty-check confirm if any field touched

---

### 2.6 — W2-F: Add TimeBlock modal (one-off lunch / cleaning / busy)

```
┌────────────────────────────────────────────┐
│  ╭──────────────────────────────────────╮  │
│  │ Заблокировать время                ✕ │  │
│  ├──────────────────────────────────────┤  │
│  │ Мастер                                │  │
│  │ [Анна Петрова ▾]                     │  │
│  │                                      │  │
│  │ Дата    [22.05.2026]                 │  │
│  │ Время   с [13:00] до [14:00]         │  │
│  │                                      │  │
│  │ Причина                               │  │
│  │ ┌──────────────────────────────────┐ │  │
│  │ │ Обед                          ▾ │ │  │
│  │ └──────────────────────────────────┘ │  │
│  │   • Обед                              │  │
│  │   • Уборка                            │  │
│  │   • Подготовка                        │  │
│  │   • Просто занято                     │  │
│  │   • Другое…                           │  │
│  │                                      │  │
│  │ Заметка (необязательно)               │  │
│  │ ┌──────────────────────────────────┐ │  │
│  │ │                                  │ │  │
│  │ └──────────────────────────────────┘ │  │
│  │                                      │  │
│  │ ⓘ Помощник не предложит клиентам    │  │
│  │   это время.                          │  │
│  │                                      │  │
│  │ ⓘ Часто обедает в это время?        │  │
│  │   [Сделать регулярным обедом →]      │  │  ← routes to W2-D Working Hours
│  ├──────────────────────────────────────┤  │
│  │ [Отмена]              [Сохранить]    │  │
│  ╰──────────────────────────────────────╯  │
└────────────────────────────────────────────┘
```

**Interaction notes:**
- Time-range validation: end > start; same-day only (if owner needs multi-day, route to ScheduleException)
- «Сделать регулярным обедом →» tip surfaces only if owner already created ≥ 3 lunch-typed TimeBlocks for same master in last 14 days (heuristic from analytics; deferred to phase 2 if engineering capacity tight — confirm Q-SW6)
- «Причина» dropdown is keyed to preset list; «Другое…» reveals freetext field (max 200 chars)
- On save → POST /api/v1/schedule/time-blocks; cell on weekly grid populates with 🍴 (or 🧹 for cleaning) icon
- If selected time overlaps existing booking → modal blocks save, inline error «На это время уже есть запись (Мария И., 13:30). Удалите её сначала или выберите другое время.»

---

### 2.7 — W2-G: SlotConfig panel (tenant-wide, separate from per-master hours)

```
┌────────────────────────────────────────────┐
│  ← Настройки слотов                 [⋯]    │
├────────────────────────────────────────────┤
│  [Неделя] [Часы] [Искл] [Слоты ●] [⚠3]    │
│                                            │
│  Эти настройки действуют для всего салона. │
│                                            │
│  ── Зазор между записями ──                │
│  ┌──────────────────────────────────────┐  │
│  │ [  5  ] минут                        │  │
│  │  ⓘ Помощник оставит этот зазор      │  │
│  │    после каждой записи на уборку.    │  │
│  └──────────────────────────────────────┘  │
│                                            │
│  ── Минимум до записи ──                   │
│  ┌──────────────────────────────────────┐  │
│  │ [  60 ] минут                        │  │
│  │  ⓘ Клиент не запишется ближе         │  │
│  │    чем за это время до начала.       │  │
│  └──────────────────────────────────────┘  │
│                                            │
│  ── Максимум вперёд ──                     │
│  ┌──────────────────────────────────────┐  │
│  │ [  60 ] дней                         │  │
│  │  ⓘ Помощник не предложит слоты       │  │
│  │    дальше этого срока.               │  │
│  └──────────────────────────────────────┘  │
│                                            │
│  ── Шаг сетки слотов ──                    │
│  ┌──────────────────────────────────────┐  │
│  │ ⦿ 15 мин (рекомендуется)             │  │
│  │ ○ 30 мин                              │  │
│  │ ○ 10 мин                              │  │
│  └──────────────────────────────────────┘  │
│                                            │
│  ────────────────────────────────────────  │
│  [Отмена]                  [Сохранить]     │
└────────────────────────────────────────────┘
```

**Interaction notes:**
- Numeric steppers: tap `[ N ]` opens numeric keypad; bounds enforced (buffer 0–60 min, lead 0–48 h displayed as minutes-or-hours toggle in v1.1, MVP minutes only; max-advance 7–180 days)
- Each setting has inline ⓘ explainer; copy is owner voice anchor «Confident + Concise»
- Slot granularity is salon-wide (per Q-SC4 lock); per-master override deferred to v1.1
- Save is global (no per-master fields here); confirmation toast «Настройки слотов обновлены»
- Access restricted to Owner role only (Admin sees the panel read-only with «Только владелец может изменить» note); enforce per permissions matrix §12

---

### 2.8 — W2-H: Pending ScheduleChangeRequests inbox

```
┌────────────────────────────────────────────┐
│  ← Заявки от мастеров                  3   │
├────────────────────────────────────────────┤
│  [Неделя] [Часы] [Искл] [Слоты] [⚠3 ●]    │
│                                            │
│  Открытые (3)   Решённые                  │
│                                            │
│  ┌──────────────────────────────────────┐  │
│  │ 🕐 Анна Петрова   ·   2 ч назад      │  │
│  │                                      │  │
│  │ Сместить начало среды на 11:00       │  │
│  │ «Утренние йога-занятия по средам»    │  │
│  │                                      │  │
│  │ Затронет 3 записи (см. подробно)     │  │  ← link → request detail
│  │                                      │  │
│  │ [✓ Одобрить]  [✎ Уточнить]  [✗ Отк.] │  │
│  └──────────────────────────────────────┘  │
│                                            │
│  ┌──────────────────────────────────────┐  │
│  │ 🏥 Олег Иванов   ·   вчера 18:32     │  │
│  │                                      │  │
│  │ Больничный 19 мая (1 день)            │  │
│  │ «Простудился»                        │  │
│  │                                      │  │
│  │ Затронет 1 запись                    │  │
│  │                                      │  │
│  │ [✓ Одобрить]  [✎ Уточнить]  [✗ Отк.] │  │
│  └──────────────────────────────────────┘  │
│                                            │
│  ┌──────────────────────────────────────┐  │
│  │ 🌴 Юля Соколова   ·   3 дн назад     │  │
│  │                                      │  │
│  │ Отпуск 1–14 июня                     │  │
│  │ «Заранее планирую отпуск»            │  │
│  │                                      │  │
│  │ Затронет 0 записей                   │  │
│  │                                      │  │
│  │ ⚠ Срок ответа истекает через 8 ч     │  │  ← 72h auto-escalate warning
│  │                                      │  │
│  │ [✓ Одобрить]  [✎ Уточнить]  [✗ Отк.] │  │
│  └──────────────────────────────────────┘  │
│                                            │
├────────────────────────────────────────────┤
│  [🏠] [📅●] [💬] [📊] [👤]                 │
└────────────────────────────────────────────┘
```

**Interaction notes:**
- Card-per-request; sort: oldest-pending first (closest to 72h auto-escalate)
- «✓ Одобрить» → confirm sheet «Одобрить и применить изменения?» → POST /approve → uses locked owner-side copy from §6.5 «Готово. Передал {{master_first_name}}. Записи в этот период автоматически перенесу клиентам или предложу альтернативу.»
- «✗ Отклонить» → opens reason sheet (matches §6.5 reject template): «Передам {{master}}, что пока не подходит. Хотите написать причину? [Просто отклонить] [Добавить причину]»
- «✎ Уточнить» → opens text input that sends master the §5.11.4 «Owner requested clarification» template
- «Затронет N записей» link → routes to request detail with diff visualisation + conflicting-bookings list + per-booking action
- «⚠ Срок ответа истекает через X» chip appears when request older than 64h (8h before 72h auto-escalate per Q-M6 state machine)
- Empty state: «Заявок от мастеров нет.» + 🌿 illustration
- Resolved tab: shows last 30 days of resolved requests, read-only, with resolution_note visible

---

## 3. S3 — Master mobile schedule view + editor: surface tree

S3 lives in the **master MAX Mini App** at the «Расписание» tab (M3 in master-mobile-handoff). The view is **read-only on bookings**; the editor capability is limited to **submitting ScheduleChangeRequests** + **quick-actions for sick/day-off self-mark** (per Q-SC5 lock — sick is emergency, self-mark allowed with audit).

### Route map

```
/master/schedule
  ├─ ?view=day (default)
  ├─ ?view=week
  ├─ ?view=month
  ├─ modal=request-change
  ├─ modal=sick-today
  └─ ?tab=my-requests (own pending/resolved requests)
```

### Wireframe inventory (S3)

| # | State | Wireframe |
|---|---|---|
| W3-A | Day view (default, populated) | §3.1 |
| W3-B | Week ahead glanceable view | §3.2 |
| W3-C | Submit ScheduleChangeRequest flow (modal) | §3.3 |
| W3-D | Pending own-requests inbox | §3.4 |
| W3-E | Quick action: «Я болен сегодня» (self-mark sick) | §3.5 |

---

### 3.1 — W3-A: Today day-view (default, populated)

Reused from master-mobile-handoff M3 day-view; reproduced here with explicit tap targets and new entry points to ScheduleChangeRequest flow.

```
┌─────────────────────────────────────┐
│  ← Расписание         [Сегодня]     │
├─────────────────────────────────────┤
│  [День ●] [Неделя] [Месяц]          │
│                                     │
│  ◂  Среда, 21 мая  ▸                │
│                                     │
│  6 клиентов · 3 окна · до 19:00     │  ← summary line
│                                     │
│  09:00 ─── рабочий день начат       │
│                                     │
│  10:00 ┌──────────────────────────┐ │
│        │ 10:00 — М. И.            │ │  ← booking row
│  11:00 │ маникюр гель-лак · 90м   │ │
│        │ [Открыть диалог →]       │ │
│  12:00 └──────────────────────────┘ │
│                                     │
│  12:00 ─── окно 60 мин ───          │  ← free window styled subtly
│                                     │
│  13:00 ┌──── обед 13–14 ──────────┐ │
│  14:00 └──────────────────────────┘ │
│                                     │
│  14:00 ┌──────────────────────────┐ │
│        │ ● 14:30 — С. П.          │ │  ← active in-progress (red dot)
│  15:00 │ наращивание · 120м       │ │
│        │ заканчивается ≈ 16:30    │ │
│  16:00 └──────────────────────────┘ │
│                                     │
│  16:30 ┌──── окно 30 мин ───       │
│                                     │
│  17:00 ┌──────────────────────────┐ │
│        │ 17:00 — А. К.            │ │
│  18:00 │ маникюр · 60м            │ │
│        └──────────────────────────┘ │
│                                     │
│  19:00 ─── рабочий день окончен ─── │
│                                     │
│  ────────────────────────────────── │
│  [+ Запросить изменение]            │  ← primary CTA
│  [🏥 Я болен сегодня]               │  ← quick action (Q-SC5)
├─────────────────────────────────────┤
│  [🏠] [📅●] [💬] [👤]               │
└─────────────────────────────────────┘
```

**Interaction notes:**
- Booking row text: time + customer first-name + last-initial («М. И.» = Мария И.) + service short + duration; per ownership-policy §4 master sees no price
- Tap booking row → opens conversation with that customer (deep-link to /conversations/<id>)
- Tap free-window slot → no-op (master cannot self-book; only owner/admin can fill); long-press → tooltip «Окно — клиент может записаться»
- Tap «обед» block → read-only sheet «Обед 13:00–14:00 — задан Кариной. Изменить → запросить»
- «[+ Запросить изменение]» → opens W3-C modal pre-filled with selected date if invoked from grid context
- «[🏥 Я болен сегодня]» → opens W3-E confirm sheet (self-mark, immediate effect, audit log)
- Active booking «●» pulse animation: subtle 1.2 Hz red dot (reduced-motion → static)
- Swipe left/right → previous/next day; haptic light on day-step

---

### 3.2 — W3-B: Week ahead glanceable view

```
┌─────────────────────────────────────┐
│  ← Расписание                       │
│  [День] [Неделя ●] [Месяц]          │
│  ◂  19–25 мая  ▸                    │
│                                     │
│   Пн   Вт   Ср   Чт   Пт   Сб   Вс  │
│  19   20   21   22   23   24   25   │
│  ──   ──   ●●   ──   ──   🌴   OFF │  ← ● = today; 🌴 = exception
│  3    4    6    5    7    8    —   │  ← clients per day
│                                     │
│  ────────────────────────────────── │
│                                     │
│  СРЕДА, 21 МАЯ · сегодня             │
│  6 клиентов · 3 окна (60, 30, 30м)  │
│  [Открыть день ›]                   │
│                                     │
│  ЧЕТВЕРГ, 22 МАЯ                    │
│  5 клиентов · 1 окно 14:00–15:00    │
│  [Открыть день ›]                   │
│                                     │
│  ПЯТНИЦА, 23 МАЯ                    │
│  7 клиентов · загружено             │
│  [Открыть день ›]                   │
│                                     │
│  СУББОТА, 24 МАЯ                    │
│  🌴 Отпуск (по плану)               │  ← exception summarised
│                                     │
│  ВОСКРЕСЕНЬЕ, 25 МАЯ                │
│  Выходной                            │
│                                     │
│  ────────────────────────────────── │
│  [+ Запросить изменение]            │
├─────────────────────────────────────┤
│  [🏠] [📅●] [💬] [👤]               │
└─────────────────────────────────────┘
```

**Interaction notes:**
- Each day-card tappable → routes to day view for that date
- Day-of-week dots: `●●` = today; `──` = working; `🌴` = exception of any kind (collapsed); `OFF` = working_hours.is_working=false
- Master sees own bookings only; multi-master overlay is owner-only (S2 W2-C «Все мастера» option)
- Tapping a 🌴 exception day shows read-only sheet with exception type + reason (set by owner)
- «[+ Запросить изменение]» entry persists at bottom (same as W3-A)

---

### 3.3 — W3-C: Submit ScheduleChangeRequest modal

Triggered by «[+ Запросить изменение]» button on W3-A / W3-B / W3-D.

```
┌─────────────────────────────────────┐
│  ╭───────────────────────────────╮  │
│  │ Запрос изменения             ✕│  │
│  ├───────────────────────────────┤  │
│  │                               │  │
│  │ Что хотите изменить?           │  │
│  │                               │  │
│  │ ⦿ Выходной на конкретный день  │  │
│  │ ○ Сместить часы дня            │  │
│  │ ○ Отпуск (диапазон дат)        │  │
│  │ ○ Больничный (вперёд)          │  │
│  │ ○ Другое                       │  │
│  │                               │  │
│  ├───────────────────────────────┤  │
│  │ Дата                           │  │
│  │ [27.05.2026]                  │  │
│  │                               │  │
│  │ ┌── ВИДНО ЕСЛИ «отпуск» ──┐  │  │
│  │ │ по [02.06.2026]          │  │  │
│  │ └──────────────────────────┘  │  │
│  │                               │  │
│  │ ┌── ВИДНО ЕСЛИ «сместить» ─┐  │  │
│  │ │ Новые часы:              │  │  │
│  │ │ с [11:00] до [19:00]      │  │  │
│  │ │ Обед [13:00]–[14:00]      │  │  │
│  │ └──────────────────────────┘  │  │
│  │                               │  │
│  │ Причина                        │  │
│  │ ┌────────────────────────────┐│  │
│  │ │ Утренние йога-занятия по   ││  │
│  │ │ средам                     ││  │
│  │ └────────────────────────────┘│  │
│  │                               │  │
│  │ ⓘ На этот период у вас 3      │  │
│  │   записи. Карина решит, как   │  │
│  │   поступить.                  │  │
│  │                               │  │
│  ├───────────────────────────────┤  │
│  │ [Отмена]    [Отправить запрос]│  │
│  ╰───────────────────────────────╯  │
└─────────────────────────────────────┘
```

**Interaction notes:**
- Kind radio at top drives form shape: «Выходной» = single date; «Сместить часы» = single date + new hours + optional lunch; «Отпуск» = date range; «Больничный (вперёд)» = single date or range (for known-in-advance sickness — vs same-day W3-E quick action); «Другое» = freetext-only
- Reason field required ≥ 5 chars, ≤ 280; voice anchor «Empathetic-mild + Calm» for placeholder copy
- Conflicting-bookings ⓘ block renders live as date(s) change
- Submit → POST /api/master/availability (or schedule/change-requests, per master-mobile-handoff §M3 endpoints); modal dismisses; toast + push uses §5.11.1 locked copy: «Запрос отправлен: {{change_summary}}. {{owner_name}} рассмотрит и ответит. Обычно — в течение дня.»
- Cancel: dirty-check confirm if any field touched
- If master already has 3+ pending requests → warning chip «У вас уже 3 открытых запроса. Дождитесь решения по предыдущим.» — submit still allowed (no hard block in MVP), but informational
- Per Q-SC5, «Больничный» here is for **forward-planned** absence (e.g., scheduled medical procedure); same-day sick uses W3-E quick-action self-mark

---

### 3.4 — W3-D: Pending own-requests inbox

Accessed from W3-A/B via `?tab=my-requests` (top-of-schedule chip «Мои заявки (2)»).

```
┌─────────────────────────────────────┐
│  ← Мои заявки                       │
├─────────────────────────────────────┤
│  [Открытые (2)]  [Решённые]         │
│                                     │
│  ┌─────────────────────────────┐    │
│  │ 🕐 На рассмотрении           │    │
│  │ Сместить начало среды 21.05 │    │
│  │ «Утренние йога-занятия»     │    │
│  │ Отправлен 2 ч назад          │    │
│  │ [Отозвать]                   │    │  ← master can withdraw while PENDING
│  └─────────────────────────────┘    │
│                                     │
│  ┌─────────────────────────────┐    │
│  │ 🕐 Уточняется                │    │
│  │ Отпуск 1–14 июня             │    │
│  │                              │    │
│  │ Карина спрашивает:           │    │
│  │ «Сможете передать клиентов  │    │
│  │  Олегу?»                     │    │
│  │                              │    │
│  │ [Ответить →]                 │    │  ← opens reply input
│  └─────────────────────────────┘    │
│                                     │
│  ────────────────────────────────── │
│  Решённые → 12 за последний месяц   │
│                                     │
└─────────────────────────────────────┘
```

**Interaction notes:**
- Status chips: 🕐 «На рассмотрении» (PENDING), 🕐 «Уточняется» (PENDING + owner asked clarification), ✓ «Одобрено», ✗ «Отклонено», ⚠ «Автоэскалация» (after 72h)
- «Отозвать» → POST cancel → status CANCELLED, master notification «Запрос отозван»
- «Ответить →» → text input that delivers master's reply back to owner via §5.11.4 chain; resets request countdown
- Resolved tab: read-only history with owner resolution_note visible per §5.11.2 («✅ {{owner}} одобрил(а): {{summary}}. Записи в этот период автоматически перенесу клиентам или перенаправлю — расскажу как будет.») or §5.11.3 reject template
- Empty state: «У вас нет открытых заявок.» + button [+ Запросить изменение] → W3-C
- Per master-mobile-handoff M3 backend: `GET /api/master/availability/pending`

---

### 3.5 — W3-E: Quick action «Я болен сегодня» (self-mark sick)

Per Q-SC5 lock — sick is emergency, master can self-mark with audit + auto-notify owner. Max 3 self-marks per quarter before requires owner approval.

```
┌─────────────────────────────────────┐
│  ╭───────────────────────────────╮  │
│  │ Отметить больничный          ✕│  │
│  ├───────────────────────────────┤  │
│  │                               │  │
│  │ 🏥                             │  │
│  │                               │  │
│  │ Сегодня болен — не выйду?     │  │
│  │                               │  │
│  │ Карина сразу узнает. Записи   │  │
│  │ на сегодня помощник предложит │  │
│  │ перенести клиентам.            │  │
│  │                               │  │
│  │ У вас на сегодня:              │  │
│  │   • 10:00 — М. И.             │  │
│  │   • 14:30 — С. П.             │  │
│  │   • 17:00 — А. К.             │  │
│  │   (всего 3 записи)             │  │
│  │                               │  │
│  │ Самочувствие (необязательно): │  │
│  │ ┌───────────────────────────┐ │  │
│  │ │ Простуда + температура    │ │  │
│  │ └───────────────────────────┘ │  │
│  │                               │  │
│  │ ⓘ Это 1-й больничный в этом   │  │
│  │   квартале (макс. 3 без       │  │
│  │   подтверждения).             │  │
│  │                               │  │
│  ├───────────────────────────────┤  │
│  │ [Отмена]  [Подтвердить]       │  │
│  ╰───────────────────────────────╯  │
└─────────────────────────────────────┘
```

**Interaction notes:**
- Confirmation requires explicit tap on [Подтвердить] — never auto-submitted from main view (prevent fat-finger from a glanceable surface)
- Reason field is **optional** (master may not want to disclose); placeholder voice «Empathetic-mild + Calm»
- On confirm: server creates ScheduleException(type=sick_leave, date=today, full-day); audit event `schedule.sick_self_marked`; owner bot DM via §6.5-style template; conflicting bookings flagged with action = «Предложить клиенту перенос» (default; owner can override later)
- HapticFeedback.notificationOccurred('warning') on confirm
- If quarter-counter already at 3: button changes to «[Подтвердить] → нужно подтверждение Карины» — submits as regular ScheduleChangeRequest(kind=sick_leave) instead of immediate exception; copy adapts: «Карина подтвердит — обычно в течение дня.»
- Once today's sick is recorded, the «🏥 Я болен сегодня» button on W3-A is replaced by «🏥 Отметка о болезни активна → [Отменить отметку]» (lets master undo within 4 hours if it was a mistake)

---

## 4. Reusable patterns

### 4.1 Date picker
- Native picker on iOS (UIDatePicker wheel) / Android (MaterialDatePicker) / Web (`<input type="date">`)
- Locale = ru-RU; week starts Monday
- Past dates allowed only for read-only views (schedule history); inputs in S2/S3 forms default-disable past dates
- Range picker: same component invoked twice (с / по)
- Min/max constraints respect SlotConfig.max_advance_days

### 4.2 Time picker
- 15-min snap default (per Q-SC2); 5-min snap for SlotConfig buffer field
- 24-hour display always (ru-RU convention)
- Validation messages inline (red text below field, never modal-blocking until Save)
- Empty/invalid state: field outlined red, helper text replaces normal hint
- A11y: each picker labelled by adjacent text label + role=spinbutton

### 4.3 Modal anatomy
All modals share this skeleton:
```
┌────────────────────────────┐
│ ╭────────────────────────╮ │
│ │ Title                ✕ │ │  ← header: title (24/600), close (✕) on right
│ ├────────────────────────┤ │
│ │                        │ │  ← scrollable body, generous 16px padding
│ │   …form…               │ │
│ │                        │ │
│ ├────────────────────────┤ │
│ │ [Secondary] [Primary]  │ │  ← sticky footer; primary right
│ ╰────────────────────────╯ │
└────────────────────────────┘
```
- Primary action right per RU/EN reading order
- Destructive primaries use voice anchor §6.7 — no red exclamation prefixes
- Backdrop tap = same as «Отмена» (with dirty-check)
- Esc key = «Отмена»
- Enter on last field = primary action (only if all required fields valid)
- WebApp.enableClosingConfirmation() while modal dirty

### 4.4 Inline-edit card pattern (W2-D rows)
- Collapsed by default; tap row to expand
- Expanded card shows full editor inline (no separate page route)
- Sticky bottom save bar with dirty-counter («Сохранить (изм.3)»)
- Single Save persists all dirty rows in one PATCH (transactional)

### 4.5 Empty / loading / error patterns
- **Loading**: skeleton matching the layout (no spinners on whole screen)
- **Empty**: illustration (🌱 or context-specific) + 1-sentence why + 1 primary CTA + optional secondary
- **Error**: inline banner top of view + retry button; never blank screen
- **Offline**: cached data + persistent banner «Нет сети — изменения сохранятся при подключении»; queue mutations in IndexedDB (Mini App constraint)

### 4.6 Bottom sheet vs modal
- **Bottom sheet**: list-selection (master picker, action menu) — feels native on mobile
- **Modal**: form entry (exception, time-block, change-request) — needs more vertical space and explicit Save/Cancel
- Both follow §4.3 anatomy; sheet adds drag-handle and partial-height variant

---

## 5. Edge cases

| ID | Case | Behaviour |
|---|---|---|
| EC-S2-1 | Master is on vacation when owner opens grid filtered to that master | Grid renders week as usual; days inside exception shown with 🌴 overlay + tooltip «Отпуск до DD.MM»; manual booking attempt on those days → modal blocks with «У Анны на эту дату отпуск. Сначала измените исключение.» |
| EC-S2-2 | Owner edits working hours; existing booking falls outside new hours | On Save, dialog «Запись Марии И. 11 мая 09:00 выходит за новые часы. Что делать? [Связаться с клиентом] [Отменить запись] [Назад]» |
| EC-S2-3 | Two admins edit working hours simultaneously | Optimistic save + version check; second-saver gets toast «Кто-то ещё обновил часы Анны — обновите экран» + refresh button |
| EC-S2-4 | Multi-master overlay (Все мастера) on weekly grid | Cells colour-coded per master; if >4 masters, show «N+ ещё» chip instead of overlapping cells; clicking chip filters to that master |
| EC-S2-5 | Slot conflict: manual booking would overlap TimeBlock | Modal blocks save with inline error + offers «Удалить блок и записать?» quick action |
| EC-S2-6 | YClients-connected salon opens S2 | Working-hours tab is read-only with banner «Часы синхронизируются из YClients. Менять — в YClients.»; exceptions still editable our-side (overlay) |
| EC-S2-7 | Owner approves request that conflicts with already-approved exception | Server returns 409; UI shows «Конфликт: этот период уже занят другим исключением (отпуск Анны 1–14 июня). Объединить или отклонить?» |
| EC-S3-1 | Master views schedule while on their own vacation | Day view shows 🌴 banner top «У вас сейчас отпуск до DD.MM. Записей нет.»; «[🏥 Я болен сегодня]» button hidden (no contradiction) |
| EC-S3-2 | Master submits change-request for date when they're already in exception | Form pre-warns «У вас уже отпуск в эти даты. Уточнить или отозвать предыдущее?» |
| EC-S3-3 | Owner rejected master's request, master taps «Ответить» | Re-opens W3-C pre-filled with previous values (so master can adjust and resubmit) |
| EC-S3-4 | Master tries 4th self-sick in quarter | W3-E behaves as regular ScheduleChangeRequest (not immediate); copy adapts; counter visible |
| EC-S3-5 | Master view + owner editing simultaneously | Master sees stale data; if master taps stale slot for change-request, server returns 409 → toast «График обновился — посмотрите ещё раз» + auto-refresh |
| EC-S3-6 | Master tries to submit change-request for past date | Form disables past dates; if attempted via deep-link, server returns 400 with copy «Прошедшие даты изменить нельзя» |
| EC-S3-7 | Master self-sick after some bookings already started | Today's exception applied only to remaining-day slots; bookings already in progress untouched; owner DM mentions «Уже начатые записи не тронуты» |

---

## 6. Accessibility — WCAG 2.2 AA baseline

### Structural / semantic
- Schedule grid (S2 W2-B): `<table>` with `<th scope="col">` for day headers + `<th scope="row">` for time slots; each cell carries human-readable summary in textContent (not just colour)
- Forms (W2-D, W2-E, W2-F, W2-G, W3-C, W3-E): every input has `<label for>` + `aria-describedby` pointing to helper/error text
- Buttons: never icon-only without `aria-label` (e.g., `[⋯]` overflow → `aria-label="Дополнительные действия"`)
- Modals: trap focus inside, restore focus to invoker on close, `aria-modal="true"`, focus-on-mount = first interactive element (not close button)

### Visual
- Colour contrast ≥ 4.5:1 for body text, ≥ 3:1 for large text and meaningful UI shapes
- Never rely on colour alone for status: master-colour cells (multi-master overlay) ALSO carry initials; «booked» vs «free» cells use pattern fill in addition to colour
- Focus indicator ≥ 2px outline with ≥ 3:1 contrast against adjacent colour
- Min tap target 44×44 px (iOS) / 48×48 px (Android); cells in dense grids enforce min via padded hitbox even if visual cell smaller

### Motion
- Reduced-motion preference (`prefers-reduced-motion: reduce`) disables active-booking pulse, modal slide-in, date-stepper haptic; uses fade instead
- Animations restrained per anti-slop §17

### Keyboard
- Tab order: top nav → segmented control → grid (arrow keys move focus inside cells, Enter opens cell) → bottom actions
- Esc closes modals / sheets
- Date-stepper: PageUp/PageDown = previous/next week; Home = today

### Screen reader
- Time labels: `aria-label="среда, 21 мая, девять часов утра"` (full phrasing, not «09:00»)
- Status chips: `role="status"` so SR users hear «На рассмотрении» when state changes
- Live regions: `aria-live="polite"` on the dirty-counter and on the conflicting-bookings preview block in W2-E (so SR users hear updates as they pick dates)

### Localisation safe-zone
- All copy in ru-RU; layout tolerates ≤ +30% text growth (en-US fallback ready for partner-test scenarios)
- Date format: ru-RU long form in headers («Среда, 21 мая 2026»); short form in tight cells («Ср 21»)

---

## 7. Open questions

| # | Question | Lean | Owner | Urgency |
|---|---|---|---|---|
| **Q-SW1** | S2 default landing tab on first open after onboarding — Weekly grid or Working-hours editor? | Weekly grid if any master has hours set; otherwise Working-hours editor for first-unset master (auto-route to setup task) | PM + UX | 🟢 |
| **Q-SW2** | Multi-master weekly overlay (Все мастера) — readable threshold? | ≤ 4 masters render inline colour-coded; > 4 collapses to per-master chips with «открыть» | UX | 🟡 |
| **Q-SW3** | TimeBlock «Сделать регулярным обедом» heuristic threshold (3 lunches in 14d) — keep in MVP or defer to Phase 2? | Defer to Phase 2 — heuristic adds backend cost; MVP shows the hint always when reason=обед | Eng | 🟡 |
| **Q-SW4** | Master quick-action «Я болен сегодня» reachable from where besides schedule tab? | Also from M1 dashboard top-card («Сегодня 6 клиентов · [🏥 не выхожу]») — saves a tap on a bad day | PM + UX | 🟢 |
| **Q-SW5** | Master self-sick quarter counter — visible to master always or only when nearing limit? | Always visible in W3-E modal; not surfaced in main schedule view (avoid stigma) | UX + PM | 🟢 |
| **Q-SW6** | When master submits ScheduleChangeRequest with conflicting bookings, who proposes the customer reshuffle — owner or master? | Owner decides (current Q-M6 lock); master sees count only, not customer details (privacy + reduce master decision-burden) | UX | 🟢 |
| **Q-SW7** | Should W2-D Working-hours editor support copy-row-to-row («apply Mon to all weekdays»)? | YES — small icon `[⋯]` per row → menu «Применить эти часы на пн–пт» / «Применить на все дни» | UX | 🟢 confirm w/ eng |
| **Q-SW8** | SlotConfig — should slot_granularity_minutes be visible to Admin role read-only or hidden entirely? | Visible read-only with «Только владелец может изменить» — transparency > confusion | PM | 🟢 |
| **Q-SW9** | Withdraw own request (W3-D «Отозвать») — should it also remove owner's pending notification, or leave audit trail visible to owner? | Owner sees «Запрос отозван мастером» note in their inbox; do NOT silently disappear (transparency between roles) | PM | 🟢 |
| **Q-SW10** | Master sick-self-mark — should it surface in owner's MAX bot DM immediately, or batch with morning brief? | Immediate (sick is operational) — uses §6.5-style real-time escalation template | UX | 🟢 |
| **Q-SW11** | When YClients-connected salon's owner opens S2, do we still show pending ScheduleChangeRequests tab? | YES — change-requests are our-side concept regardless of YC sync direction; banner clarifies «Применятся к нашей надстройке, не пушим в YClients» | PM + Eng | 🟡 |
| **Q-SW12** | Mini App offline edit queue — how long do we hold queued mutations before warning master/owner? | 60s soft toast «изменения ждут сети»; 5min persistent banner; 24h drop with notification | Eng | 🟢 later |

---

## 8. Cross-document linkage

- **Source of truth (fields, state machine, permissions, API):** [`../handoffs/2026-05-18-schedule-management-handoff.md`](../handoffs/2026-05-18-schedule-management-handoff.md)
- **Master surface context (S3 sits inside master mobile UX):** [`../handoffs/2026-05-18-master-mobile-handoff.md`](../handoffs/2026-05-18-master-mobile-handoff.md) §M3
- **Locked copy (owner side):** [`./owner-conversational-templates.md`](./owner-conversational-templates.md) §6.5 (real-time escalation), §6.7 (destructive confirms), §6.8 (settings voice)
- **Locked copy (master side):** [`./master-conversational-templates.md`](./master-conversational-templates.md) §5.11 (ScheduleChangeRequest dialog chain)
- **IA constraints (5-tab bottom nav, state-adaptive home):** [`./information-architecture.md`](./information-architecture.md) §1, §7
- **Permissions matrix (who can do what):** [`./conversation-ownership-policy.md`](./conversation-ownership-policy.md) §4
- **Attribution for manual bookings created from S2:** [`./attribution-policy.md`](./attribution-policy.md) — `human_direct` source
- **Customer impact when owner edits hours:** [`../handoffs/2026-05-18-customer-first-time-handoff.md`](../handoffs/2026-05-18-customer-first-time-handoff.md) (reschedule UX)
- **Decisions log entries to add:** Q-SW1..Q-SW12 (this doc), referenced once locked
- **Foundation:** `memory/project_salon_catalog_vertical.md` (template-path is core value — S2 is the editor that makes template-path work), `memory/project_ux_architect_charter.md` (this doc is a UX-architect-owned policy artifact)

---

## 9. Sign-off

| Role | Approval | Date | Notes |
|---|---|---|---|
| UX-architect (Chief, supervisory) | ☐ |  | Owner of this doc |
| Designer (executing) | ☐ |  |  |
| Product Manager | ☐ |  | Confirm Q-SW1..Q-SW12 leans |
| Engineering — Frontend (Mini App) | ☐ |  | Confirm 5–10 day implementation horizon realistic |
| Engineering — Backend (slot algorithm + APIs) | ☐ |  | Confirm endpoints from handoff §14 still authoritative |
| QA | ☐ |  | Edge cases EC-S2-1..EC-S3-7 covered in test plan |
| Founder | ☐ |  | Q-SW5 (self-sick counter visibility) — strategic |

---

**End of schedule-editor-wireframes.md.**
