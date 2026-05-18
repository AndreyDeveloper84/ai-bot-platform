# Schedule Management — Developer Handoff Package

| Field | Value |
|---|---|
| **Date** | 2026-05-18 r1 |
| **Designer** | UX-architect skill |
| **Status** | Draft for review |
| **Surfaces** | Web dashboard (primary) + MAX manager-bot (quick approvals + master change-requests) |
| **Scope** | Working hours setup, recurring schedule, exceptions, manual bookings, time blocks, multi-master concurrency, YC sync conflict handling |
| **Auth** | Role-gated per [`conversation-ownership-policy.md`](../policies/conversation-ownership-policy.md) §4 |
| **Screens** | 1 main schedule view (3 layouts) + 5 modals/sub-screens |
| **Critical for** | **Template-path tenants (no YClients)** — without this, bot cannot offer slots |

## Foundation references

| Doc | Why it matters |
|---|---|
| [`memory/project_salon_catalog_vertical.md`](~/.claude/projects/.../memory/project_salon_catalog_vertical.md) | YClients is one of many catalog sources; ~40% of MVP cohort uses template-path |
| [`conversation-ownership-policy.md`](../policies/conversation-ownership-policy.md) §4 | Master role can request changes, not directly edit (per Q-M6 lock) |
| [`memory/project_attribution_extensible_model.md`](~/.claude/projects/.../memory/project_attribution_extensible_model.md) | Manual bookings via this UI = `human_direct` source, NOT billable |
| [`decisions-log.md`](../decisions-log.md) | Q-M6 closed — master change-requests via bot DM with inline approve/decline |
| [`2026-05-17-salon-onboarding-handoff.md`](./2026-05-17-salon-onboarding-handoff.md) | Schedule setup is part of Phase 4c Masters tab (mentioned but not designed) |
| Existing booking skill: `apps/skills/booking/tools.py::show_slots` | The bot's slot computation must read from this schedule data |

---

## 0. Overview

### What this module is
The salon's operational interface for setting and maintaining master schedules. **Critical for ~40% of MVP cohort that doesn't use YClients** — without this UI, those salons cannot use the bot at all (bot needs to know when masters are free).

For YC-connected salons: read-only sync from YClients with manual override flagging.

### Why this matters
- No schedule = no bot bookings = product useless для template-path salons
- Master sick day not reflected → bot promises non-existent slot → trust killed
- Walk-in customer not entered → bot offers occupied slot → double-booking
- Schedule data feeds `apps/skills/booking/tools.py::show_slots` — engineering already exists, needs data

### Primary persona — Karina + Anya (split duty)
- **Karina (Owner)**: sets working hours once at onboarding; rarely revisits
- **Anya (Admin)**: daily operational use — manual bookings, mark exceptions, approve master requests

### JTBDs

**Setup JTBD:**
> «Когда я настраиваю салон без YClients, я хочу один раз задать расписание мастеров и не возвращаться к этому каждый день — чтобы помощник знал свободные слоты автоматически.»

**Operational JTBDs:**
> «Когда мастер заболел / опаздывает, я хочу быстро обновить расписание чтобы помощник не обещал клиентам несуществующие слоты.»

> «Когда клиент приходит с улицы записаться вне бота, я хочу занять этот слот чтобы помощник не предложил его кому-то ещё.»

> «Когда мастер просит изменить свой график, я хочу быстро одобрить или отклонить из MAX-бота, не открывая web.»

### Success metrics

| Metric | Target | Type |
|---|---|---|
| **Schedule setup completion rate** (Phase 4c salons) | ≥ 90% (block before publish) | Activation |
| Time to setup all masters | < 10 min for 1-3 master salon | Efficiency |
| **Schedule freshness** — % salons with no exceptions in 7+ days | > 30% = unhealthy (means stale) | Health |
| Manual booking creation rate | ≥ 5/week for active template-path salons | Engagement |
| Double-booking incidents (bot + manual conflict) | < 0.5% of bookings | Quality |
| Master change-request approval median latency | < 4 hours | Operational |
| YC sync conflict resolution rate | ≥ 80% of conflicts resolved within 24h | Operational |

---

## 1. Architecture — data model + state machine

### Data model (engineering input)

```python
class WorkingHours(Model):
    """Recurring weekly schedule per master."""
    master = ForeignKey(Master)
    day_of_week = IntegerField()  # 0=Monday ... 6=Sunday
    start_time = TimeField()       # 09:00
    end_time = TimeField()         # 19:00
    is_working = BooleanField()    # False = day off
    # constraint: unique (master, day_of_week)


class ScheduleException(Model):
    """Specific date override."""
    master = ForeignKey(Master)
    date = DateField()
    type = CharField(choices=[
        ("vacation", "Отпуск"),
        ("sick_leave", "Больничный"),
        ("day_off", "Выходной"),
        ("custom_hours", "Изменён график"),
        ("event", "Корпоратив / обучение"),
    ])
    start_time = TimeField(null=True)  # null = full day off
    end_time = TimeField(null=True)
    reason = TextField(blank=True)
    created_by = ForeignKey(User)
    # Per-day, master can have at most one exception. New exception overwrites.


class TimeBlock(Model):
    """One-off blocked slot (lunch, prep, after-walk-in-no-booking)."""
    master = ForeignKey(Master)
    start_at = DateTimeField()
    end_at = DateTimeField()
    reason = CharField(max_length=200)  # "обед", "уборка кабинета", "просто занято"
    created_by = ForeignKey(User)
    # Different from ScheduleException — blocks have duration < day


class ScheduleChangeRequest(Model):
    """Master proposing change; owner approves."""
    master = ForeignKey(Master)
    requested_change = JSONField()  # diff structure
    status = CharField(choices=["pending", "approved", "rejected", "cancelled"])
    reason = TextField()
    created_at = DateTimeField()
    resolved_at = DateTimeField(null=True)
    resolved_by = ForeignKey(User, null=True)
    resolution_note = TextField(blank=True)


# Existing BookingRequest already has the link to master + datetime
# Slot computation: WorkingHours - ScheduleExceptions - TimeBlocks - BookingRequests
```

### State machine for ScheduleChangeRequest

```
PENDING (master submitted) →
  ├─ owner taps Approve → APPROVED → diff applied to WorkingHours/ScheduleException
  ├─ owner taps Reject → REJECTED (with reason)
  ├─ master cancels → CANCELLED
  └─ 72h no decision → AUTO_ESCALATED (notification to founder/CSM)
```

### Slot computation contract

Existing bot tool `apps/skills/booking/tools.py::show_slots(master_id, service_id, date)`:
- Input: master, service (for duration), target date
- Reads: WorkingHours for that day-of-week + ScheduleException for that date + TimeBlocks + existing Bookings
- Output: array of available slots (15-min granularity default) where service_duration fits
- Buffer between bookings: configurable per-tenant (default 5 min)

UI is the **input editor**; slot computation is engineering (separate work).

---

## 2. Routes + permissions

| Route | Purpose | Access |
|---|---|---|
| `/schedule` | Main schedule view (week default) | Owner, Admin, Receptionist (read+write); Master (own only) |
| `/schedule/master/{id}` | Single-master detailed view | Owner, Admin, Receptionist (any master); Master (self only) |
| `/schedule/working-hours` | Edit recurring weekly hours per master | Owner, Admin only |
| `/schedule/exceptions` | Manage specific date overrides | Owner, Admin, Receptionist (with restrictions on type) |
| `/schedule/requests` | Pending change requests from masters | Owner, Admin |
| `/schedule/requests/{id}` | Single request detail | Owner, Admin (request creator master) |

---

## 3. Screen S1 — Main schedule view (week layout)

### Desktop (≥1024px)

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│ Студия Карина   [Setup ✓]   [Karina, owner ▾]                                    │
├──────────────────────────────────────────────────────────────────────────────────┤
│ Dashboard │Каталог│Диалоги│Аналитика│ Расписание │Биллинг│Настройки               │
├──────────────────────────────────────────────────────────────────────────────────┤
│ Расписание                                                                       │
│                                                                                  │
│ Вид: [День][Неделя][Месяц][По мастеру]   Период: ◂ 19-25 мая ▸   [Сегодня]      │
│ Мастер: [Все ▾]                                          [+ Бронь]  [⚙ Настр.] │
│                                                                                  │
│ Источник: 🌱 шаблон (нет YClients)  •  3 заявки от мастеров [→]                  │
├──────────────────────────────────────────────────────────────────────────────────┤
│        │ ПН 19   │ ВТ 20   │ СР 21   │ ЧТ 22   │ ПТ 23   │ СБ 24   │ ВС 25      │
│────────┼─────────┼─────────┼─────────┼─────────┼─────────┼─────────┼────────────│
│  9:00  │ 🌱 закр │ 🌱 закр │ 🌱 закр │         │         │         │           │
│ 10:00  │ Анна    │ Анна    │ Анна    │ Анна    │ Анна    │ Анна    │           │
│   :30  │ ▒▒ Мар. │ ░░ своб │ ▒▒ Окс. │ ░░      │ ░░      │ ▒▒ Юля  │ выходной  │
│ 11:00  │ ▒▒ Мар. │ Олег:10 │ ▒▒ Окс. │ ░░      │ ░░      │ Юля:30  │           │
│   :30  │ ░░      │ ░░      │ Анна:12 │ Анна:11 │ ░░      │ ░░      │           │
│ 12:00  │ ░░      │ ░░      │ ▒▒ обед │ ▒▒ обед │ ▒▒ обед │ ░░      │           │
│   :30  │ Олег:30 │ ░░      │ ▒▒ обед │ ▒▒ обед │ ▒▒ обед │ Анна:42 │           │
│ ...    │         │         │         │         │         │         │           │
│ 18:00  │ ░░      │         │         │         │         │         │           │
│ 19:00  │ закр.   │ закр.   │ закр.   │ закр.   │ закр.   │ ✓ закр  │           │
└──────────────────────────────────────────────────────────────────────────────────┘
Легенда: 🌱 не работаем  ░░ свободно  ▒▒ занято  цвет = мастер
```

Per-cell display:
- Empty cell `░░` = available slot
- Booked cell `▒▒` shows: master initials + service abbrev («Мар.» = Маникюр)
- Click cell → slot detail (Booking modal или Time block modal)
- Hover slot → tooltip with full info: «11:00–12:30, Мария И., маникюр + гель-лак, Анна»

### Mobile (<768px)

```
┌────────────────────────────────────┐
│ ← Расписание                       │
│ Вид: [День ▾]   ◂ Чт 22 мая ▸     │
│ Мастер: [Все ▾]                    │
│                            [+]     │  ← FAB-style add
├────────────────────────────────────┤
│  9:00  │  🌱 не работаем           │
│ 10:00  │  ░░ Анна — свободно        │
│   :30  │  ▒▒ Мария И., маникюр→Анна│
│ 11:00  │  ▒▒ продолжение           │
│ 11:30  │  ░░ Анна — свободно        │
│ 12:00  │  ▒▒ обед — Анна            │
│ 12:30  │  ░░ свободно               │
│ 13:00  │  ░░                        │
│ 14:00  │  ▒▒ Олег:31, гель-лак      │
│   :30  │  ▒▒ продолжение           │
│ 15:00  │  ░░                        │
│ ...    │                            │
└────────────────────────────────────┘
```

Mobile defaults to **day view** (most common operational use). Swipe left/right to navigate days.

### Sub-tabs / controls

- **Вид**: День / Неделя / Месяц / По мастеру
- **Период**: ‹ Today › navigation; «Сегодня» jump-back button
- **Мастер filter**: «Все» (default) / select one
- **Источник badge**:
  - 🌱 «шаблон (нет YClients)» — for template-path
  - 🔄 «YClients (синк X мин назад)» — for YC-connected
  - ⚠ «N конфликтов с YClients» — when sync drift detected (click → resolve)
- **3 заявки от мастеров [→]** banner — show pending ScheduleChangeRequest count
- **[+ Бронь]** primary action — manual booking entry
- **[⚙ Настройки]** → routes to `/schedule/working-hours`

### States
| State | Behavior |
|---|---|
| Loading | Skeleton week grid |
| **Empty (day 1, no working hours set)** | Big card: «Настройте часы работы мастеров — без этого помощник не сможет предлагать слоты. [Настроить часы работы]» |
| Populated | As above |
| Filtered (one master) | Other masters' rows hidden |
| Conflict pending (YC sync drift) | Banner top with count + «Разрешить» |
| Master request pending | Banner «3 заявки от мастеров [Открыть]» |
| Offline | Cached + banner; can view, edits queued |

---

## 4. Screen S2 — Working hours editor

`/schedule/working-hours` — for setting recurring weekly schedule per master. Used during onboarding Phase 4c and post-launch tuning.

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│ ← Расписание → Часы работы                                                       │
├──────────────────────────────────────────────────────────────────────────────────┤
│ Мастер: [Анна Петрова ▾]                                                         │
│                                                                                  │
│ ── Часы работы Анны ──                                                            │
│                                                                                  │
│ ☑ Понедельник     с [09:00] до [19:00]   обед [13:00] до [14:00]                │
│ ☑ Вторник         с [09:00] до [19:00]   обед [13:00] до [14:00]                │
│ ☑ Среда           с [09:00] до [19:00]   обед [13:00] до [14:00]                │
│ ☑ Четверг         с [09:00] до [19:00]   обед [13:00] до [14:00]                │
│ ☑ Пятница         с [09:00] до [19:00]   обед [13:00] до [14:00]                │
│ ☑ Суббота         с [10:00] до [17:00]   обед [13:00] до [13:30]                │
│ ☐ Воскресенье     выходной                                                       │
│                                                                                  │
│ ── Настройки слотов ──                                                            │
│                                                                                  │
│ Минимальный интервал между записями: [5] минут                                   │
│   ⓘ Помощник оставит этот зазор между бронями для уборки/подготовки             │
│                                                                                  │
│ Минимальное время до записи: [1] час                                              │
│   ⓘ Клиент не сможет записаться менее чем за этот срок до начала                │
│                                                                                  │
│ Максимальное время вперёд: [60] дней                                             │
│   ⓘ Помощник не предложит слоты дальше этого срока                              │
│                                                                                  │
│                                                          [Отмена]  [Сохранить]   │
└──────────────────────────────────────────────────────────────────────────────────┘
```

### Multi-master quick setup
At onboarding Phase 4c, after master added, default working hours auto-applied (10:00–19:00 weekdays, 11:00–17:00 Saturday, closed Sunday). Owner can adjust per master.

### Bulk «Apply to all» button
For salon with many masters with same schedule:
- «Скопировать график Анны для:» [other-masters multi-select]
- Confirms before overwriting existing schedules

### Slot parameters
- **Buffer between bookings**: 5 min default. 0 = back-to-back. Range 0–60.
- **Minimum lead time**: 1 hour default. Range 0–48h.
- **Maximum advance booking**: 60 days default. Range 7–180.

---

## 5. Screen S3 — Exception calendar

`/schedule/exceptions` — for marking specific date overrides (vacation, sick day, holiday).

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│ ← Расписание → Исключения                                                        │
├──────────────────────────────────────────────────────────────────────────────────┤
│ Мастер: [Все ▾]    Тип: [Все ▾]    Период: ◂ Май 2026 ▸                          │
│                                                          [+ Добавить исключение] │
├──────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│      Май 2026                                                                    │
│  Пн  Вт  Ср  Чт  Пт  Сб  Вс                                                      │
│  -   -   -   1   2   3   4                                                       │
│  5   6   7   8   9  10  11                                                       │
│ 12  13  14  15  16  17  18                                                       │
│ 19  20  21  22  23  24  25                                                       │
│ 26 [27🌴27🌴27🌴] 30  31                                                          │
│                                                                                  │
│ Цветные точки:  🌴 отпуск    🏥 больничный    📅 особый график  🎉 корпоратив     │
├──────────────────────────────────────────────────────────────────────────────────┤
│ Список исключений на май 2026:                                                  │
│                                                                                  │
│ ┌──────────────────────────────────────────────────────────────────────────┐    │
│ │ 27–29 мая • Анна Петрова                                                  │    │
│ │ 🌴 Отпуск                                                                  │    │
│ │ «Поездка в Сочи»                                                          │    │
│ │                                            [Изменить]  [Удалить]         │    │
│ └──────────────────────────────────────────────────────────────────────────┘    │
│                                                                                  │
│ ┌──────────────────────────────────────────────────────────────────────────┐    │
│ │ 18 мая • Олег Иванов                                                      │    │
│ │ 🏥 Больничный (1 день)                                                    │    │
│ │                                            [Изменить]  [Удалить]         │    │
│ └──────────────────────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────────────────────┘
```

### Add exception modal

```
┌────────────────────────────────────────────────────┐
│ Добавить исключение                            ✕   │
├────────────────────────────────────────────────────┤
│ Мастер: [Анна Петрова ▾]                          │
│                                                    │
│ Период:  с [27.05.2026] по [29.05.2026]           │
│  ☐ Только часть дня                                │
│  (when checked: с [HH:MM] до [HH:MM])             │
│                                                    │
│ Тип:                                               │
│ ⦿ 🌴 Отпуск                                        │
│ ◯ 🏥 Больничный                                    │
│ ◯ 📅 Особый график                                 │
│ ◯ 🎉 Корпоратив / обучение                         │
│ ◯ ⛔ Другая причина                                │
│                                                    │
│ Комментарий (виден внутри салона):                 │
│ ┌────────────────────────────────────────────────┐│
│ │ Поездка в Сочи                                 ││
│ └────────────────────────────────────────────────┘│
│                                                    │
│ ⓘ У Анны на эти даты уже есть 2 записи:           │
│   • 27 мая 10:00 — Мария И.                       │
│   • 28 мая 14:00 — Светлана П.                    │
│                                                    │
│ Что делать с существующими записями:               │
│ ⦿ Уведомить клиентов и оставить отмену на меня    │
│ ◯ Автоматически отменить (с уведомлением)         │
│ ◯ Перенести на другого мастера (если возможно)    │
│                                                    │
│                              [Отмена]  [Сохранить] │
└────────────────────────────────────────────────────┘
```

**Critical UX**: when exception conflicts with existing bookings, system shows conflict + asks how to handle. NEVER silently breaks bookings.

---

## 6. Screen S4 — Manual booking entry

Click `[+ Бронь]` from S1 (when admin needs to enter walk-in or phone-call booking).

```
┌────────────────────────────────────────────────────┐
│ Добавить запись вручную                        ✕   │
├────────────────────────────────────────────────────┤
│                                                    │
│ Клиент:                                            │
│ ⦿ Найти существующего                              │
│   [🔎 Имя или телефон               ]              │
│ ◯ Новый клиент                                     │
│   Имя: [_________________________]                 │
│   Телефон: [+7 ___ ___ ____]                       │
│                                                    │
│ Услуга:                                            │
│ [Маникюр + гель-лак ▾] 90 мин • 2 200 ₽            │
│                                                    │
│ Мастер: [Анна Петрова ▾]                           │
│                                                    │
│ Дата: [22.05.2026]   Время: [15:30]                │
│                                                    │
│ ⓘ Доступные слоты для Анны 22 мая:                │
│   10:00, 10:30, 11:30, [15:30 ✓], 17:00            │
│                                                    │
│ Источник: [Звонок ▾]                               │
│   ⦿ Звонок                                         │
│   ◯ Пришёл в салон                                │
│   ◯ Через сайт                                    │
│   ◯ Соцсети                                       │
│   ◯ Другое                                        │
│                                                    │
│ Комментарий (внутренний):                          │
│ [_____________________________________________]    │
│                                                    │
│ ⓘ Эта запись будет помечена как «human_direct»    │
│   и не учитывается в счёте за помощника            │
│                                                    │
│                              [Отмена]  [Создать]   │
└────────────────────────────────────────────────────┘
```

### Attribution (per attribution-policy)
- Manual bookings via this UI: `booking_source = "human_direct"`, `actor_type = admin/owner/receptionist` (who created)
- NOT billable (per Q12 lock)
- `attribution_metadata.created_by = "admin_manual"`
- Transparent to salon — explicit message «не учитывается в счёте»

### Conflict detection
- If selected time has no available slot → error inline «Это время недоступно. Доступные слоты: ...»
- If master has exception that day → error «У Анны 22 мая отпуск. Выберите другого мастера или дату.»
- If time block in slot → error «На это время отмечена занятость («обед»). Удалить блок?»

---

## 7. Screen S5 — Time block (one-off slot block)

For marking «Анна обедает 13:00–14:00» or «занято до окончания работы». Lighter weight than exception (which is full-day or scheduled day-part).

```
┌────────────────────────────────────────────────────┐
│ Заблокировать время                            ✕   │
├────────────────────────────────────────────────────┤
│ Мастер: [Анна Петрова ▾]                           │
│                                                    │
│ Дата: [22.05.2026]                                 │
│ Время: с [13:00] до [14:00]                        │
│                                                    │
│ Причина (для команды):                             │
│ [Обед                                          ▾]  │
│   • Обед                                           │
│   • Уборка                                         │
│   • Подготовка                                     │
│   • Просто занято                                  │
│   • Другое...                                      │
│                                                    │
│ ⓘ Помощник не будет предлагать клиентам это        │
│   время. Снять блок: клик на блоке в расписании.   │
│                                                    │
│                              [Отмена]  [Сохранить] │
└────────────────────────────────────────────────────┘
```

Common pattern: «Anya marks Анна's lunch break 13:00–14:00 every workday» — propose «Сделать обедом регулярно?» → routes to Working Hours editor with lunch fields pre-filled.

---

## 8. Screen S6 — Master change-request approval (bot DM, per Q-M6)

Per Q-M6 locked decision — approve/decline via bot DM matches owner mobile habit.

### MAX manager-bot message template

```
Помощник студии:
Анна Петрова просит изменить график:

Было:
  Среда 09:00 — 19:00 (обед 13:00 — 14:00)

Станет:
  Среда 11:00 — 19:00 (обед 14:00 — 15:00)

Причина:
«Утренние йога-занятия по средам»

[Inline keyboard]
[✓ Одобрить] [✎ Изменить детали] [✗ Отклонить]
[📄 Подробнее в дашборде]
```

### Web detail view `/schedule/requests/{id}` (если admin click «Подробнее»)

Shows full diff visualization + impact analysis:
- «Существующие записи на эти часы: 3 записи будут затронуты. Действие: предложить перенос автоматически.»
- Owner can edit details OR set conditional approval («одобряю если перенесёшь существующие сама»)

### State machine
- PENDING → admin tap «Одобрить» → APPROVED → diff auto-applied, master notified
- PENDING → admin tap «Отклонить» → REJECTED (with optional reason text) → master notified
- PENDING → 72h no decision → AUTO_ESCALATED to CSM (or founder for cohort #1-50)

---

## 9. View types (S1 variations)

### Day view
- 1-column timeline (today, hourly slots)
- All masters' bookings in single column with master-color coding OR per-master collapsible swim lanes
- Default mobile

### Week view (default desktop)
- 7-column grid (days), rows by time
- Cells show booking briefs
- Click cell → detail modal

### Month view
- Calendar grid (5–6 weeks)
- Each day shows occupancy %: «12 записей» or color heat
- Click day → switch to day view for that date

### Per-master view (`/schedule/master/{id}`)
- Single-master full-detail timeline
- Shows working hours, exceptions, all that master's bookings
- Better for master themselves to view their schedule

---

## 10. YClients sync conflict handling

When salon has YClients connected, schedule data syncs FROM YC. Our UI is mostly **read-only** for working hours (YC is authoritative). Manual bookings via S4 are pushed TO YC.

### Conflict scenarios
1. **YC schedule changed after our manual booking** — show «⚠ В YClients больше нет слота» on affected booking
2. **YC booking created we didn't know about** — pulled in via sync, appears in our schedule
3. **Our manual booking conflicts with YC sync** — banner «Конфликт между YClients и нашей записью» + «Использовать YClients» / «Использовать нашу запись»

### Sync direction
- Working Hours: YC → us (read-only in our UI for YC-connected tenants)
- Bookings: bidirectional, our manual bookings push to YC, YC bookings pull in
- Exceptions: YC → us (we don't push exceptions to YC)
- Time Blocks: our-side only, NOT synced to YC (it's an internal operational tool)

### UI signal
Top of S1 banner shows source:
- 🌱 «шаблон (нет YClients)» — manual everything
- 🔄 «YClients (синк 2 мин назад)» — most data from YC
- ⚠ «N конфликтов с YClients» — needs resolution

---

## 11. States (all 5 standard + edge)

| State | Behavior |
|---|---|
| Loading | Skeleton week grid + master picker spinner |
| **Empty (no working hours configured)** | Big setup card: «Настройте часы работы [Открыть настройки]» |
| **Empty (configured but no bookings)** | Schedule grid shows availability, hint: «Записей пока нет. [+ Добавить вручную]» |
| Populated | As designed |
| Filtered (one master) | Other masters hidden, banner «Только Анна» + clear filter |
| Conflict (YC sync) | Banner with count + «Разрешить» button |
| Master request pending | Banner with count + «Открыть» |
| Offline | Cached view + banner; edits queued in IndexedDB; sync on reconnect |
| Concurrent edit (2 admins) | Optimistic update + toast «Кто-то ещё редактирует» if race detected |

---

## 12. Permissions enforcement (per ownership-policy §4)

| Action | Owner | Admin | Receptionist | Master |
|---|---|---|---|---|
| View own schedule | ✅ | ✅ | ✅ | ✅ |
| View other masters' schedules | ✅ | ✅ | ✅ | ❌ |
| Edit working hours (recurring) | ✅ | ✅ | ❌ | ❌ |
| Add date exception | ✅ | ✅ | ✅ (limited types: sick_leave, day_off; NOT vacation/event) | ❌ |
| Block time slot | ✅ | ✅ | ✅ | ❌ |
| Create manual booking | ✅ | ✅ | ✅ | only own |
| Cancel/reschedule booking | ✅ | ✅ | ✅ (with notification) | only own |
| Approve master change requests | ✅ | ✅ | ❌ | ❌ |
| Submit change request | n/a | n/a | n/a | ✅ |
| Settings (slot params, lead time) | ✅ | ❌ | ❌ | ❌ |

---

## 13. Components inventory

| Component | Purpose |
|---|---|
| `ScheduleGrid` | Week/Day/Month grid view component |
| `MasterColorLegend` | Visual key for master color-coding |
| `ScheduleCell` | Single slot — booked / blocked / free / closed states |
| `BookingDetailPopover` | Hover info on booked slot |
| `WorkingHoursForm` | Per-master 7-day editor |
| `BulkApplyToAllModal` | Copy schedule to multiple masters |
| `SlotParamsForm` | Buffer, lead time, max advance settings |
| `ExceptionCalendar` | Month calendar with exception markers |
| `AddExceptionModal` | Date range + type + conflict handling |
| `ManualBookingForm` | Walk-in / phone-call booking entry |
| `TimeBlockForm` | One-off slot block |
| `ChangeRequestCard` | Master request with diff + actions (web) |
| `MaxChangeRequestMessage` | Bot DM template (per Q-M6) |
| `ConflictBanner` | YC sync drift indicator |
| `ConflictResolver` | Per-conflict «Use YC / Keep ours» modal |
| `MasterSelector` | Dropdown with avatar + name |
| `PeriodNavigator` | ‹ Today › with «Сегодня» jump |
| `SourceBadge` | 🌱 / 🔄 / ⚠ indicator on top |

---

## 14. Backend contracts

```
GET /api/v1/schedule/working-hours
  Query: ?master_id=X
  Response: { master_id, hours_per_day: [{day_of_week, start, end, is_working, lunch_start, lunch_end}], slot_params: {...} }

PATCH /api/v1/schedule/working-hours/{master_id}
  Body: { hours_per_day, slot_params }
  Response: 200 updated

POST /api/v1/schedule/working-hours/bulk-apply
  Body: { source_master_id, target_master_ids: [int] }
  Response: 200 { applied_to: N }

GET /api/v1/schedule/exceptions
  Query: ?master_id=X&start=DATE&end=DATE&type=...
  Response: { exceptions: [Exception] }

POST /api/v1/schedule/exceptions
  Body: { master_id, date_range, type, time_range?, reason, conflict_action: "notify"|"auto_cancel"|"reassign" }
  Response: 201 + { conflicting_bookings: [...], action_taken }

PATCH /api/v1/schedule/exceptions/{id}
  Body: partial
  Response: 200

DELETE /api/v1/schedule/exceptions/{id}
  Response: 204

GET /api/v1/schedule/time-blocks
  Query: ?master_id=X&date=DATE
  Response: { blocks: [TimeBlock] }

POST /api/v1/schedule/time-blocks
  Body: { master_id, start_at, end_at, reason }

DELETE /api/v1/schedule/time-blocks/{id}

GET /api/v1/schedule/bookings
  Query: ?period=...&master_id=...
  Response: { bookings: [BookingRequest with full schedule context] }

POST /api/v1/schedule/bookings/manual
  Body: { customer (existing or new), service_id, master_id, datetime, source_channel, internal_note }
  Response: 201 BookingRequest with booking_source=human_direct + actor_type set + billable=False

GET /api/v1/schedule/change-requests
  Query: ?status=pending
  Response: { requests: [ChangeRequest] }

POST /api/v1/schedule/change-requests/{id}/approve
  Body: { resolution_note?: str }
  Response: 200, applies diff

POST /api/v1/schedule/change-requests/{id}/reject
  Body: { resolution_note?: str }
  Response: 200, notifies master

GET /api/v1/schedule/conflicts
  Response: { conflicts: [{ source: "yc_sync", description, options: [...] }] }

POST /api/v1/schedule/conflicts/{id}/resolve
  Body: { resolution: "use_yc"|"keep_local"|"manual" }
  Response: 200
```

### Real-time updates
WebSocket events for active dashboard sessions:
- `schedule.exception_added`
- `schedule.booking_created` / `_modified` / `_cancelled`
- `schedule.conflict_detected`
- `schedule.change_request_submitted`

---

## 15. A11y considerations

- ScheduleGrid: `<table>` semantically с proper `<th>` for days + `<th scope="row">` for times
- Cells with bookings: `<td>` content includes textual booking summary (visible to SR)
- Time block / exception markers: text labels not color-only
- Keyboard navigation: Arrow keys move focus between cells; Enter opens cell detail
- Form fields in modals: proper labels + tooltips for slot params
- High contrast: schedule cells distinguishable in high-contrast mode (not relying on master-color alone)
- Mobile day view: simpler list pattern for SR linear reading
- Time format: `aria-label` includes full time («понедельник, девять часов утра»), not just «09:00»

---

## 16. Edge cases

- **Salon switches from template-path to YC mid-flight** — schedule data migrates; conflicts surface as banner; admin resolves per-record
- **Salon disconnects YC** — last sync stays as data; UI switches to fully editable; banner «Источник: ваши данные (YClients отключён)»
- **Master invited but no hours set** — UI prompts on master add: «Часы работы Анны не настроены — настроить сейчас?»
- **DST transitions** — RU no DST currently, but stamps stored UTC + tenant TZ; banner if calendar appears shifted
- **Schedule extends past midnight** — supported (e.g., 22:00 → 02:00 next day); UI represents as «через полночь» tag
- **Same master has overlapping exceptions** — newer overwrites; UI shows history of overrides
- **Manual booking conflicts with existing one** — surface conflict modal; cannot save without resolving
- **Schedule changed mid-booking** (admin removes hours while customer in Mini App booking flow) — Mini App refetches, shows «слот стал недоступен, выберите другой»
- **YC has different schedule structure** (e.g., YC supports breaks as service interruptions) — sync best-effort, flag unmapped fields
- **Two admins create conflicting bookings simultaneously** — first write wins; second gets «slot just got booked» error, refresh
- **Bot in middle of booking flow when admin manually books same slot** — race condition; LLM tool returns «slot now taken» — bot apologizes and offers alternatives
- **Master vacation spans into new month** — exception calendar shows on both month views
- **Master deletes self / leaves salon** — schedule history preserved; bookings reassign or cancel per owner choice
- **Inactive masters** (`status="inactive"`) — schedule hidden from default views, available in archive filter

---

## 17. Anti-slop scan (12-point)

| # | Check | Status |
|---|---|---|
| 1 | Inter default | ✅ MAX UI / system; mono for time/numbers |
| 2 | Purple gradient | ✅ |
| 3 | Glassmorphism | ✅ |
| 4 | Radius scale | ✅ |
| 5 | Emoji decoration | ⚠ 🌱🌴🏥📅🎉⛔🔄 — semantic for exception types. На проде Lucide: `palm-tree`, `cross`, `calendar-clock`, `party-popper`, `octagon`, `refresh-cw`. Cells legend ░░ ▒▒ — replace with subtle background patterns (diagonal stripes for blocked, solid for booked) |
| 6 | Hero + CTA | n/a |
| 7 | AI illustrations | ✅ |
| 8 | Gradient overlay | ✅ |
| 9 | Copy specific | ✅ «Утренние йога-занятия по средам», «Поездка в Сочи» |
| 10 | Real names / initials | ✅ master initials in cells |
| 11 | Animation restrained | ✅ subtle: smooth slide-in for modals; conflict banner fade-in |
| 12 | Slate-on-slate | ✅ |

**11/12 ✅, 1 fix (emoji exception markers → Lucide on production).**

---

## 18. Cross-screen integration

| Source | Integration |
|---|---|
| **Onboarding Phase 4c Masters tab** | Direct entry to S2 Working Hours editor for newly added masters |
| **Conversations C2 detail** | Quick action «Открыть слот в расписании» — links to S1 with booking selected |
| **Customer Mini App booking flow** | Reads slot availability from this data; conflict detection in real-time |
| **Analytics dashboard peak hours heatmap** | Drill-down into S1 filtered by time range |
| **Master mobile UX §M3 schedule view** | Master sees own schedule from this data, can request changes via Q-M6 flow |
| **MAX manager-bot** | Change-request approval (Q-M6); morning brief mentions today's bookings |

---

## 19. Phased delivery

### Phase 1 (MVP, ~3 weeks) — required for template-path tenants to function
- S1 main view: week + day layout
- S2 Working Hours editor (per master, weekly pattern)
- S4 Manual booking entry
- S5 Time block
- Basic conflict detection (single source — manual + bot)
- Permissions enforcement
- Empty/loading/error states

### Phase 2 (~2 weeks)
- S3 Exception calendar + add exception modal with conflict-handling options
- Bulk «apply to all» for working hours
- Slot params (buffer, lead time, max advance)
- Month view
- S6 Master change-request approval flow (bot DM + web detail per Q-M6)

### Phase 3 (~2 weeks)
- YC sync conflict resolution UI
- Per-master view (`/schedule/master/{id}`)
- Real-time WebSocket updates
- Sync direction documentation surface in UI

### Phase 4 (v1.1+)
- Recurring exceptions («every other Tuesday»)
- Multi-station support (master uses different chairs)
- Schedule templates (apply «summer hours» bulk)
- Predictive demand (link to analytics — «обычно в это время мало клиентов»)
- Auto-suggest slot consolidation (gaps between bookings)

---

## 20. Open questions

| # | Question | Recommendation / lean | Owner | Urgency |
|---|---|---|---|---|
| **Q-SC1** | Default working hours for newly added master | 10:00–19:00 Mon-Fri, 11:00–17:00 Sat, closed Sun. Owner can edit immediately. | PM | 🟢 |
| **Q-SC2** | Slot granularity — 15 min, 30 min, or configurable per tenant? | 15 min default (covers most beauty services); configurable per-tenant in slot params v1.1 | PM + Eng | 🟡 |
| **Q-SC3** | Buffer time default — 5 min, 10 min, or 0? | 5 min default. Allows quick station prep without making slots feel too sparse. | PM | 🟢 |
| **Q-SC4** | Should slot params be salon-wide or per-master? | Salon-wide MVP (one buffer rule for all). Per-master v1.1 if demand. | PM | 🟢 |
| **Q-SC5** | Master can self-mark «сегодня болен» via bot DM (no owner approval)? | YES with audit + auto-notify owner. Sick is emergency; can't wait for approval. Limit: max 3 self-marks per quarter per master before requires owner. | PM | 🟡 |
| **Q-SC6** | Auto-reassign bookings when exception declared — same service same time other master? | Offer choice in exception modal (designed). If chosen «Перенести на другого мастера», ask customer first via bot DM. | PM + UX | 🟡 |
| **Q-SC7** | Cancel-policy display — should customer see «бесплатная отмена до 24h» in their booking? | YES — show in confirmation message + reminder. Already in customer first-time §6 reminders. | PM | 🟢 |
| **Q-SC8** | Working hours schedule version history? | YES audit changes, show in Settings → Аудит. Rollback v1.1 if demand. | PM | 🟢 |
| **Q-SC9** | Multi-week recurring exception (e.g., «every Monday for 4 weeks dentist appointments»)? | NO MVP — manual per-date. Recurring exceptions v1.1+. | PM | 🟢 |
| **Q-SC10** | Block time can affect multiple masters at once? («корпоратив весь салон закрыт») | YES — option to select multiple masters in block creation modal. Or «весь салон» quick action. | PM | 🟡 |
| **Q-SC11** | If template-path tenant later connects YC, what happens to existing manual data? | Migrate to YC where possible; flag unmapped data; offer side-by-side view for owner to reconcile. | PM + Eng | 🟡 |
| **Q-SC12** | Time-off requests — should master be able to request «vacation», not just current-day sick? | YES via S6 change-request flow. Different urgency: sick = self-mark (Q-SC5); vacation = owner approval (current Q-M6 flow). | PM | 🟡 |

---

## 21. Cross-document linkage

- Foundation: [`memory/project_salon_catalog_vertical.md`](~/.claude/projects/.../memory/project_salon_catalog_vertical.md) (template-path is core value)
- Permissions: [`conversation-ownership-policy.md`](../policies/conversation-ownership-policy.md) §4
- Attribution: [`attribution-policy.md`](../policies/attribution-policy.md) (manual booking = human_direct)
- Master change-request flow: [`2026-05-18-master-mobile-handoff.md`](./2026-05-18-master-mobile-handoff.md) per Q-M6 lock
- Customer impact: [`2026-05-18-customer-first-time-handoff.md`](./2026-05-18-customer-first-time-handoff.md) cancel/reschedule
- Analytics: [`2026-05-18-analytics-dashboard-handoff.md`](./2026-05-18-analytics-dashboard-handoff.md) peak hours heatmap
- Decisions log: [`decisions-log.md`](../decisions-log.md) — Q-SC1 to Q-SC12 added

---

## 22. What this UNBLOCKS

- **Template-path tenants can ACTUALLY use the bot** (without this UI, bot cannot offer slots — product useless for ~40% MVP cohort)
- **Operational salon use** — walk-ins, phone bookings, sick days handled in product
- **Master self-service** for change requests (Q-M6 integration)
- **Slot algorithm engineering** has clear data inputs to consume
- **Onboarding Phase 4c Masters tab** has concrete workflow
- **YC sync conflict resolution** has UI surface

## 23. Sign-off

| Role | Approval | Date |
|---|---|---|
| Designer | ☐ | |
| Product | ☐ | |
| Engineering (FE) | ☐ | |
| Engineering (BE — slot algorithm) | ☐ | |
| QA (conflict scenarios) | ☐ | |
| Founder (Q-SC5 self-sick limit policy) | ☐ | |
