# Analytics Dashboard — Developer Handoff Package

| Field | Value |
|---|---|
| **Date** | 2026-05-18 r1 |
| **Designer** | UX-architect skill |
| **Status** | Draft for review |
| **Surfaces** | Web dashboard (primary) + MAX manager-bot (weekly digest push only) |
| **Scope** | Salon-facing analytics — KPIs, attribution-driven breakdowns, insights, alerts, export |
| **Auth** | **Role-gated** per [`conversation-ownership-policy.md`](../policies/conversation-ownership-policy.md) §4 |
| **Screens** | 1 main dashboard + 4 drill-down views + 1 mobile-summary card |

## Foundation references

| Doc | Why it matters |
|---|---|
| [`attribution-policy.md`](../policies/attribution-policy.md) | Source of truth for booking_source enum (5 values) — drives ALL distribution charts |
| [`memory/project_attribution_extensible_model.md`](~/.claude/projects/.../memory/project_attribution_extensible_model.md) | Why analytics ≠ billing — different filters on same data |
| [`memory/project_single_assistant_identity.md`](~/.claude/projects/.../memory/project_single_assistant_identity.md) | UI shows «помощник» NOT «бот» in customer-facing copy |
| [`conversation-ownership-policy.md`](../policies/conversation-ownership-policy.md) §4 | Permissions matrix for role-gated views |
| [`memory/project_pricing_model_hybrid.md`](~/.claude/projects/.../memory/project_pricing_model_hybrid.md) | Billing context for revenue charts (Owner only) |
| [`2026-05-17-conversations-handoff.md`](./2026-05-17-conversations-handoff.md) Screen C4 Learning Queue insights panel | Reference design pattern: growth-not-workload framing |

---

## 0. Overview

### What this module is
The salon's daily window into how the AI-assistant is performing. Owner checks every morning (per Karina persona). Drives **renewal decision** at trial-end and beyond. Connects ALL upstream data — attribution / conversations / learning queue / persona / billing.

### Why this is the single highest-leverage screen for retention
- Salon's PERCEIVED value lives here
- If salon can't see value in 30 seconds → they cancel
- AI-effectiveness numbers prove our pricing
- Trends prove product is compounding (vs static tool)
- Insights feed sales: «расскажу что я узнал у моего салона»

### Persona — «Karina» (owner)
- Opens in morning, before first client (~9:00 local)
- Mobile primary, desktop secondary
- Wants emotional signal first («всё ок / есть проблема»), then proof-points, then drill-down if needed
- 30-second attention budget for fast scan

### Secondary persona — «Anya» (admin)
- Opens during shifts
- Wants operational signals — что требует внимания сегодня
- Limited financial visibility per permissions

### JTBD
> «Когда я открываю dashboard утром, я хочу за 30 секунд понять: помощник работает или нет, есть ли проблемы, и что улучшилось — чтобы спокойно начать день или принять решение о действии.»

### Success metrics

| Metric | Target | Type |
|---|---|---|
| **Daily active owner rate** (% owners opening dashboard ≥1×/day) | ≥ 60% | North Star — proxy for stickiness |
| Median session time on dashboard | 30–90s (not too fast, not too long) | Engagement |
| Drill-down rate (click into chart) | ≥ 20% of sessions | Curiosity depth |
| **Export rate per month per active salon** | ≥ 1 (proves business use) | Proof of value |
| Dashboard → action rate (click insight → fix something) | ≥ 15% of sessions | Activation |
| Trial-end-renewed rate among active dashboard users | should be 2× higher than non-users | Causal hypothesis |

---

## 1. Architecture

### Data sources (read-only joins)
- `BookingRequest` (attribution-policy fields — main source)
- `Conversation` (handoff stats, response time)
- `BillingEvent` (Owner-only)
- `LearningQueueSuggestion` (learning growth)
- `Persona` (persona version + change events)
- `Tenant.created_at` (for «помощник работает N дней»)
- `Master` + `Service` (for breakdowns)

### Aggregation layer
Pre-computed daily snapshots in `AnalyticsSnapshot` table:
- Per-day per-tenant: bookings (by source), revenue, conversation counts, response times, handoff stats, customer counts (new/returning)
- Rolled up nightly via cron
- Reduces query cost for popular ranges

### Real-time vs cached
- Charts: hourly cache TTL
- KPI strip: 5-min cache TTL
- «Today's count» metrics: real-time (no cache)
- Insights: daily refresh

---

## 2. Route + layout

**Route:** `/analytics` (Owner + Admin); `/analytics/master/{id}` (Owner only for cross-master view, Master sees own only via `/master/dashboard`)

### Desktop layout (≥1024px)

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│ Студия Карина     [Setup ✓]    [Karina, owner ▾]   [🔔 3]                       │
├──────────────────────────────────────────────────────────────────────────────────┤
│ Dashboard │Каталог│Диалоги │ Аналитика │Биллинг│Настройки                         │
├──────────────────────────────────────────────────────────────────────────────────┤
│ Аналитика                                                                        │
│                                                                                  │
│ Период: [Последние 30 дней ▾]   Сравнить с: [Прошлый месяц ▾]   [⬇ Экспорт]    │
├──────────────────────────────────────────────────────────────────────────────────┤
│ ──── ВАЖНОЕ СЕГОДНЯ (insights, conditional) ────                                │
│ ┌──────────────────────────────────────────────────────────────────────────┐    │
│ │ 🟢 Помощник вырос: +23% записей через ai_direct за месяц                 │    │
│ │    Это +18 600 ₽ выручки. Сейчас покрывает 87% диалогов.                │    │
│ │                                                       [Подробнее →]      │    │
│ └──────────────────────────────────────────────────────────────────────────┘    │
├──────────────────────────────────────────────────────────────────────────────────┤
│ ──── KPI STRIP ────                                                             │
│ ┌──────────────┬──────────────┬──────────────┬──────────────┬──────────────┐   │
│ │ ЗАПИСИ ЧЕРЕЗ │ ВЫРУЧКА      │ НОВЫХ        │ ПОВТОРНЫХ    │ ВАШ СЧЁТ     │   │
│ │ ПОМОЩНИКА    │ ОТ ПОМОЩНИКА │ КЛИЕНТОВ     │ ВИЗИТОВ      │ ЗА МЕСЯЦ     │   │
│ │              │              │              │              │              │   │
│ │   85         │   164 200 ₽  │   23         │   62         │   2 590 ₽    │   │
│ │ ↑ +23%       │ ↑ +18%       │ ↑ +5         │ ↑ +12%       │  стандартно  │   │
│ │ vs мес назад │ vs мес назад │ vs мес назад │ vs мес назад │  для месяца  │   │
│ └──────────────┴──────────────┴──────────────┴──────────────┴──────────────┘   │
│                                                                                  │
│ ──── ОСНОВНЫЕ ГРАФИКИ ────                                                       │
│ ┌─────────────────────────────────────┬──────────────────────────────────────┐ │
│ │ ЗАПИСИ ПО ДНЯМ — 30 дней            │ ОТКУДА ЗАПИСИ — Распределение         │ │
│ │                                     │                                       │ │
│ │     [bar chart with bars per day]   │  Помощник создал сам ai_direct  85   │ │
│ │     stacked by booking_source:      │  Помощник + команда   ai_assisted  51 │ │
│ │     ai_direct (rust) /              │  Только команда       human_direct 19 │ │
│ │     ai_assisted (rust-light) /      │  Извне (YC, телефон)  external   12  │ │
│ │     human_direct (neutral) /        │                                       │ │
│ │     external (muted)                │  Записей с участием помощника: 73%   │ │
│ │                                     │                                       │ │
│ │ [Развернуть →]                      │ [Подробнее по источникам →]          │ │
│ └─────────────────────────────────────┴──────────────────────────────────────┘ │
│                                                                                  │
│ ┌─────────────────────────────────────┬──────────────────────────────────────┐ │
│ │ ПИКОВЫЕ ЧАСЫ — Heatmap              │ ТОП УСЛУГ                            │ │
│ │                                     │                                       │ │
│ │   ПН ВТ СР ЧТ ПТ СБ ВС              │ 1. Маникюр + гель-лак   29 (2 200 ₽) │ │
│ │  9:                                 │ 2. Маникюр классический 18 (1 200 ₽) │ │
│ │ 11: ■  ■  ■  ■  ■  ▓▓ ■             │ 3. Снятие гель-лака     12 (500 ₽)   │ │
│ │ 13: ▓▓ ▓▓ ▓▓ ▓▓ ▓▓ ▓▓ ▓▓            │ 4. Маникюр + педикюр    10 (4 200₽)  │ │
│ │ 15: ▓▓ ▓▓ ▓▓ ▓▓ ▓▓ ██ ▓▓            │ 5. Френч                 8 (400 ₽)   │ │
│ │ 17: ██ ██ ██ ██ ██ ██ ██            │                                       │ │
│ │ 19: ▓▓ ▓▓ ▓▓ ▓▓ ▓▓                  │ [Все услуги →]                       │ │
│ │                                     │                                       │ │
│ │ █ занято  ▓ есть слоты              │                                       │ │
│ └─────────────────────────────────────┴──────────────────────────────────────┘ │
│                                                                                  │
│ ──── РАЗБИВКА ПО МАСТЕРАМ ────                                                   │
│ ┌──────────────────────────────────────────────────────────────────────────┐    │
│ │ Мастер     │ Записей │ % через помощника │ CSAT │ Handoff │ Доход       │    │
│ │────────────┼─────────┼──────────────────┼──────┼─────────┼──────────────│    │
│ │ Анна Пет.  │  62     │  78% ai_direct   │ 4.8★ │ 3       │ 130 800 ₽   │    │
│ │ Олег Иван. │  38     │  68% ai_direct   │ 4.6★ │ 6       │  78 400 ₽   │    │
│ │ Карина     │  20     │  55% ai_direct   │ 4.9★ │ 1       │  42 000 ₽   │    │
│ └──────────────────────────────────────────────────────────────────────────┘    │
│                                                                                  │
│ ──── ЗДОРОВЬЕ ПОМОЩНИКА (Bot health) ────                                       │
│ ┌──────────────────────┬──────────────────────┬──────────────────────────────┐ │
│ │ HANDOFF RATE         │ ВРЕМЯ ОТВЕТА         │ CSAT (Customer Satisfaction) │ │
│ │ 12%                  │ 1.4 с                │ 4.7 ★                        │ │
│ │ ↓ −2% за месяц       │ ↑ +0.1 с             │ ↑ +0.2★ vs прошлый месяц     │ │
│ │ (good — меньше       │ (терпимо, в норме)   │ (отлично)                    │ │
│ │ передач админу)      │                      │                              │ │
│ └──────────────────────┴──────────────────────┴──────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────────────────┘
```

### Mobile layout (<768px)
Vertical stack, swipeable cards:

```
┌────────────────────────────────────┐
│ ← Аналитика                        │
│ Период: [30 дней ▾]                │
├────────────────────────────────────┤
│ ┌─[ Хорошее сегодня ]───────────┐  │
│ │ 🟢 +23% записей через         │  │
│ │ помощника за месяц            │  │
│ │ [Подробнее]                   │  │
│ └────────────────────────────────┘  │
│                                    │
│ ┌─[ KPI карточки swipeable ]──┐  │
│ │ Записи через помощника    85 │  │
│ │ ↑ +23% vs прошлый месяц     │  │
│ │ ← 1 из 5 →                   │  │
│ └────────────────────────────────┘  │
│                                    │
│ ┌─[ Записи по дням ]────────────┐  │
│ │ [chart inline, simplified]    │  │
│ └────────────────────────────────┘  │
│ ...                                │
└────────────────────────────────────┘
```

---

## 3. KPI strip (top section)

5 primary metrics. Each as KPICard component:

```
KPICard:
  Label (uppercase, small): "ЗАПИСИ ЧЕРЕЗ ПОМОЩНИКА"
  Value (big): 85
  Trend: ↑ +23%
  Comparison: "vs мес назад"
```

### 5 default KPIs

1. **Записи через помощника** (ai_direct + ai_assisted bookings count)
   - Quality: most prominent metric — это is «AI value»
   - Tooltip: «Включает записи, где помощник создал сам OR где он подготовил клиента к записи команде»

2. **Выручка от помощника** (sum of revenue for ai_direct + ai_assisted)
   - Owner-only (Admin sees value count only, no money)
   - Tooltip explains: «Сумма по записям, где помощник участвовал. Не включает walk-ins и звонки»

3. **Новых клиентов** (first-time customer count via bot)
   - Filter: `Customer.first_booking_attributed = True` AND `created_at within period`
   - Tooltip: «Клиенты, чья первая запись пришла через помощника»

4. **Повторных визитов** (returning customer count)
   - Filter: customers with prior bookings who booked again within period

5. **Ваш счёт за месяц** (Owner-only billing)
   - Real-time: current month total (590 base + variable per `billable=True` × 100 ₽)
   - Tooltip: «Цена за этот месяц. Списание X числа»

### KPI variations by role

| Role | KPIs shown |
|---|---|
| Owner | All 5 (default) |
| Admin | KPIs 1, 3, 4 (no money, no billing) |
| Receptionist | KPIs 1, 3, 4 (same as admin) |
| Master | Own performance only (separate `/master/dashboard` route) |

### KPI growth indicators

- ↑ green (with delta number) — improvement
- ↓ red (with delta number) — decline (caveat: not all declines are bad; show note inline if context required)
- Neutral — no significant change
- **NEVER color-only** — always with arrow icon (Lucide `trending-up` / `trending-down`) + delta + comparison text

---

## 4. Main charts (4 default)

### Chart C1 — Записи по дням (Bookings over time)

- **Type**: stacked bar chart
- **X**: day (last 7/30/90 selectable)
- **Y**: bookings count
- **Stacks** (booking_source colors per attribution-policy):
  - `ai_direct` — rust accent (full color)
  - `ai_assisted` — rust at 60% lightness
  - `human_direct` — neutral
  - `external` — muted gray
  - `test_admin` — not shown (excluded from analytics views by default)
- **Comparison overlay**: dotted line for previous-period average

Click bar → drilldown to that day's bookings list (Conversations module filtered).

### Chart C2 — Откуда записи (Source distribution donut)

- **Type**: horizontal stacked bar OR donut chart (donut preferred for visual identity match)
- **Segments**: 4 booking_source values (test_admin excluded)
- **Center number**: «Записей с участием помощника: X%» (`ai_direct + ai_assisted / total`)
- Each segment shows count + small percentage

### Chart C3 — Пиковые часы (Peak hours heatmap)

- **Type**: 7×N grid (days × hours, or just rows of common hours)
- **Color intensity**: number of bookings in that slot
- **Use case**: salon optimizes schedule, sees overflow demand

### Chart C4 — Топ услуг (Top services)

- **Type**: ranked horizontal bar list (or table)
- **Top 5 services by count** + revenue per row
- Click → service detail drilldown

---

## 5. Master performance breakdown (table)

| Column | Notes |
|---|---|
| Master name | Click → master detail |
| Bookings count | Period-filtered |
| % through ai_direct | Bot's effectiveness for this master |
| CSAT (avg rating) | From customer feedback |
| Handoff count | Times conversation escalated |
| Revenue | Owner-only column |

**Sort**: by bookings count desc default; click column header to re-sort.

### Privacy gates
- Admin sees full table EXCEPT Revenue column
- Receptionist sees Master + Bookings + CSAT + Handoff (no revenue, no detailed handoff context)
- Master sees ONLY OWN row (or own dashboard at `/master/dashboard`)

---

## 6. Bot health section (3 metrics)

Operational signals — owner action threshold:

1. **Handoff rate** — % conversations escalated. Healthy < 15%. Spike → check Conversations queue.
2. **Avg response time** — bot latency. Healthy < 2s. Spike → engineering alert.
3. **CSAT** — customer satisfaction avg. Healthy ≥ 4.5★. Drop → check recent persona changes or specific master.

Each metric shows trend ↑/↓ with tooltip explaining «good direction» (e.g., handoff ↓ = good).

---

## 7. Insights (AI-driven, conditional cards)

Surface ABOVE KPI strip, conditional — show only when meaningful.

### Insight types
1. **Growth highlight** — «Помощник вырос: +23% записей за месяц = +18 600 ₽»
2. **Coverage milestone** — «Помощник теперь покрывает 87% диалогов (+9%)»
3. **Persona tune callback** — «После изменения голоса 12 мая, CSAT поднялся +0.2★»
4. **Master pattern** — «У Анны 78% записей через помощника — лучший показатель»
5. **Service gap** — «За неделю 7 раз спросили про лазерную эпиляцию — нет в каталоге. [Добавить услугу →]»
6. **Warning** — «Handoff rate за неделю +5% — возможно, добавить FAQ»

### Frequency rules
- Max 2 insight cards at a time
- Same insight not re-shown for 7 days
- Owner can dismiss × — won't repeat
- Negative insights ALWAYS pair with actionable button («Добавить услугу», «Открыть учёбу»)

### Conditional logic
- New tenant (< 14 days) — show «Помощник работает X дней. Полная статистика через 7 дней.»
- Inactive tenant (no bookings 14 days) — alert «Помощник не получил записей за 14 дней. Проверим что не так?»

---

## 8. Alerts section (conditional banners)

Critical attention items. Shown above insights when present.

### Alert types
- 🔴 **Билинг неуспешен** — credit card failed
- 🔴 **YClients сейчас недоступен** — sync error
- 🟠 **Handoff накопились** — > 5 pending > 30 min
- 🟠 **Платное оспаривание** — dispute filed
- 🟡 **Trial заканчивается через X дней** — convert reminder

Click alert → navigates to relevant section (Conversations / Billing / Settings).

---

## 9. Period selector + Compare mode

### Period options
- Сегодня
- Вчера
- Последние 7 дней
- Последние 30 дней (default)
- Этот месяц
- Прошлый месяц
- Последние 90 дней
- Кастомный диапазон

### Compare-with options
- Прошлый период (default — same length, immediately preceding)
- Прошлый месяц
- Тот же месяц прошлого года (if 12+ months data)
- Без сравнения (clean view)

Charts use overlay or dual-bar to show comparison.

---

## 10. Export

Per [OP5 locked decision](../decisions-log.md): **CSV + JSON** from Settings → Аудит → Экспорт. Plus PDF report (Phase 2).

### Export options
- ⬇ Скачать CSV — flat table per BookingRequest with attribution fields
- ⬇ Скачать JSON — same data structured for programmatic use
- 📄 PDF отчёт (Phase 2) — formatted monthly report for sharing with accountant

Export scope = current filter (period + master + service filters applied).

### Auto-email export (Phase 2)
Schedule monthly email of PDF report to owner — opt-in.

---

## 11. Drill-downs (4 modals)

### Drilldown D1 — Day's bookings (from bar chart click)
Opens filtered conversation list for that day. Shows per-booking attribution + status.

### Drilldown D2 — Master detail (from master table)
Opens `/analytics/master/{id}` route. Per-master:
- All KPIs filtered to this master
- Bookings over time for this master
- Service breakdown for this master
- Top customers
- Handoff history

### Drilldown D3 — Service detail (from top services)
Opens `/analytics/service/{id}` route. Per-service:
- Bookings count over time
- Revenue over time
- Master split for this service
- Cancellation rate
- Average time-to-book for this service

### Drilldown D4 — Source detail (from source donut)
Per-source detailed view — list bookings + filters by source type.

---

## 12. States (Screen A1 — main dashboard)

| State | Behavior |
|---|---|
| Loading | Skeleton: KPI strip ghosts + chart placeholders + master table rows |
| **Empty (day 1, no data)** | Welcome card: «Помощник работает 1 день. Первая статистика появится через 7 дней.» + onboarding hints |
| **Empty (active tenant, no bookings in period)** | «Записей за этот период нет. [Изменить период] [Открыть Диалоги — что происходит]» |
| Populated | Default rendered as above |
| Filtered (period changed) | Charts re-render, KPIs update, loading micro-state during fetch |
| Filtered to zero | «По выбранному периоду / фильтру записей нет» |
| Comparing | Side-by-side or overlay rendering of both periods |
| Error (fetch fails) | Section-scoped retry banners |
| Partial (some metrics fail) | Affected cards show «Данные недоступны, повторить» individually |
| Offline | Cached last data + banner; export disabled |

---

## 13. Components inventory

| Component | Purpose |
|---|---|
| `PeriodSelector` | Dropdown for date range |
| `CompareSelector` | Dropdown for comparison mode |
| `ExportMenu` | CSV / JSON / PDF |
| `KPICard` | Big number + trend arrow + comparison text |
| `KPIStrip` | Horizontal scroll of KPICards (responsive) |
| `BookingsTimeChart` | Stacked bar over time |
| `SourceDistributionChart` | Donut or horizontal stacked bar |
| `PeakHoursHeatmap` | Day × hour intensity grid |
| `TopServicesList` | Ranked horizontal bars |
| `MasterPerformanceTable` | Sortable, role-gated columns |
| `BotHealthMetrics` | 3-card row (handoff / response / CSAT) |
| `InsightCard` | AI-driven contextual card (dismissible) |
| `AlertBanner` | Urgent attention banners |
| `DrillDownModal` | Generic drill-down container |
| `EmptyStateCard` | Various emptiness scenarios |
| `OfflineBanner` | Cached data indicator |

---

## 14. Backend contracts

```
GET /api/v1/analytics/kpis
  Query: ?period=30d&compare=prev_period
  Response: { kpis: { bookings, revenue, new_customers, returning, billing }, comparison: { ... }, period_meta }
  Role-gated: revenue and billing nulled for non-Owner

GET /api/v1/analytics/bookings-time
  Query: ?period=30d&granularity=day&compare=...
  Response: { series: [{ date, sources: {ai_direct, ai_assisted, ...} }], comparison: [...] }

GET /api/v1/analytics/source-distribution
  Query: ?period=30d
  Response: { sources: [{ key, count, percent }], total }

GET /api/v1/analytics/peak-hours
  Query: ?period=30d
  Response: { heatmap: [[day, hour, count], ...] }

GET /api/v1/analytics/top-services
  Query: ?period=30d&limit=5
  Response: { services: [{ id, name, count, revenue }] }

GET /api/v1/analytics/masters
  Query: ?period=30d&sort=bookings
  Response: { masters: [{ id, name, bookings, ai_direct_pct, csat, handoff_count, revenue? }] }
  Note: revenue field omitted per role

GET /api/v1/analytics/bot-health
  Query: ?period=30d&compare=prev_period
  Response: { handoff_rate, response_time_avg, csat_avg, trends: {...} }

GET /api/v1/analytics/insights
  Response: { insights: [{ id, type, text, action_url, dismissible }] }

POST /api/v1/analytics/insights/{id}/dismiss
  Marks dismissed for 7 days

GET /api/v1/analytics/alerts
  Response: { alerts: [{ id, severity, text, action_url }] }

POST /api/v1/analytics/export
  Body: { format: "csv" | "json" | "pdf", period, filters }
  Response: { download_url, expires_at }

GET /api/v1/analytics/master/{id}
  Per-master detail data

GET /api/v1/analytics/service/{id}
  Per-service detail data
```

### Cache strategy
- KPIs: 5-min cache
- Charts: 1-hour cache
- Insights: 24-hour cache (regenerated nightly)
- Alerts: real-time
- Today's metrics: real-time

---

## 15. A11y considerations

- All charts have textual `<table>` alternative for screen readers (toggle button «показать таблицей»)
- KPI cards: `aria-label` reads full context («Записи через помощника: 85, выросло на 23 процента по сравнению с прошлым месяцем»)
- Color is NEVER the only signal — every trend has arrow icon + text
- Tooltip-only info also visible in table view
- Heatmap intensity: hover shows tooltip with exact number
- High contrast mode tested — chart colors use sufficient lightness contrast
- Keyboard nav: Tab through controls; charts focusable for screen readers
- Alert banners: `role="alert"` for critical, `role="status"` for warnings

---

## 16. Edge cases

- **Day-1 tenant** — show «помощник работает N дней» card, hide charts that need ≥7 days of data
- **Salon with single master** — collapse master breakdown table to single row
- **Period with zero bookings** — show empty-period state per chart
- **Tenant has no `ai_direct` ever** (only template / external) — show explanation «Записи пока создаются вручную, помощник развернёт в течение N дней»
- **Master deleted mid-period** — show as «уволенный мастер» row, gray styling
- **Service renamed mid-period** — uses latest name; click drilldown shows rename history
- **Tenant downgrades role mid-session** — refresh shows role-gated view
- **Export request too large** (>10k bookings) — chunked + emailed link instead of inline download
- **Charts data exceeds reasonable size** — virtualize / aggregate
- **Cross-time-zone comparison** (Karina in Москва, opens dashboard in vacation in Бали) — uses tenant's configured timezone, banner clarifies
- **Currency in export** — RUB only (per Q3 locked)
- **Persona changed mid-period** — show version marker on relevant charts «Голос изменён 12 мая»
- **Bot quality degradation** (CSAT drops > 0.3★ in 7 days) → alert + recommendation to review recent persona changes

---

## 17. Anti-slop scan (12-point)

| # | Check | Status |
|---|---|---|
| 1 | Inter default | ✅ MAX UI / system fonts; mono for numeric columns |
| 2 | Purple gradient | ✅ salon-warmth |
| 3 | Glassmorphism | ✅ no glass |
| 4 | Radius scale | ✅ 8/12 |
| 5 | Emoji decoration | ⚠ 🟢🔴🟠🟡 для alert/status — semantic only; на проде Lucide `circle-check` / `circle-alert` / `triangle-alert` |
| 6 | Hero centered+CTA | n/a — dashboard |
| 7 | AI illustrations | ✅ |
| 8 | Gradient overlay | ✅ |
| 9 | Specific copy | ✅ «За неделю 7 раз спросили про лазерную эпиляцию», «Помощник вырос +23%» |
| 10 | Real names | ✅ master names from catalog |
| 11 | Animation restrained | ✅ subtle: KPI number tick-up animation 600ms, chart fade-in 400ms, NO bounces |
| 12 | Slate-on-slate | ✅ warm palette with semantic signals |

**Bonus checks:**
- ❌ 4-quadrant feature grid — n/a
- ❌ Generic «trusted by» logos — n/a
- ✅ Numbers in mono for column alignment (IBM Plex Mono)
- ✅ Insights honest, not vanity («покрывает 87%», not «+1000% growth»)
- ✅ No fake urgency in alerts

**11/12 ✅, 1 fix (emoji → Lucide icons on production).**

---

## 18. Cross-screen integration

| Source | Integration |
|---|---|
| **Conversations C1 Inbox** | Click handoff alert in dashboard → opens Conversations filtered |
| **Learning Queue C4** | Insight «Помощник вырос» links to Learning Queue showing accepted items |
| **Persona Editor** | Persona change events appear as markers on charts; «After tune CSAT» insight links here |
| **Billing screen** | KPI «Ваш счёт за месяц» links to Billing detail |
| **Catalog** | Service gap insight «нет в каталоге» links to add-service flow |
| **MAX manager-bot** | **Weekly digest push** every Monday 09:00 — top-3 KPI + 1 insight + dashboard link |

---

## 19. MAX manager-bot weekly digest template

```
Доброе утро, Karina! Итоги недели в «Студии Карина»:

📊 За 7 дней:
• Записей через помощника: 22 (↑ +3 vs прошлой неделе)
• Выручка от помощника: 42 800 ₽ (↑ +4 200 ₽)
• Новых клиентов: 6
• CSAT: 4.8★

💡 Помощник вырос — теперь покрывает 87% диалогов сам.

[Открыть Аналитику]  [Что нового помощник узнал]
```

Frequency: 1 per week (Monday). Opt-out in Settings → Уведомления.

---

## 20. Phased delivery

### Phase 1 (MVP) — 3 weeks
- KPI strip (5 metrics, role-gated)
- 2 main charts: Bookings over time + Source distribution
- Master breakdown table
- Bot health 3 metrics
- Period selector + Compare mode
- CSV export
- Empty/loading/error states

### Phase 2 — 2 weeks
- Peak hours heatmap
- Top services chart
- AI insights (5 default types)
- Alerts section
- JSON export
- Insight dismissal logic

### Phase 3 — 2 weeks
- PDF monthly report
- Auto-email export
- Drill-down modals (D1–D4)
- Master detail route
- Service detail route
- Weekly digest in MAX manager-bot

### Phase 4 (v1.1)
- Cross-tenant benchmarking (opt-in)
- Anomaly detection alerts
- Predictive insights («через 2 недели обычно низкая активность — запустите акцию?»)
- Custom dashboard widgets
- Saved filters per user

---

## 21. Open questions

| # | Question | Recommendation / lean | Owner | Urgency |
|---|---|---|---|---|
| **Q-AD1** | Time period default — 7 days or 30 days? | 30 days — gives stable patterns; less noise than 7 | PM | 🟢 |
| **Q-AD2** | Show billing projection («ожидается N ₽ к концу месяца»)? | Show but only Owner role. Reduce surprise. Conditional: don't show if low confidence (< 10 days data). | PM | 🟡 |
| **Q-AD3** | Cross-tenant benchmarking — opt-in для салона видеть «вы в топ-20% среди салонов Москвы»? | Defer to v1.1. Privacy + competitive risk; need opt-in flow design | Founder | 🟢 |
| **Q-AD4** | Master sees own analytics from master-mobile-handoff (already designed §M3 schedule) — or own dashboard subset of this? | Master sees own row + own KPIs subset from this design; full owner dashboard inaccessible to master | PM | 🟡 |
| **Q-AD5** | A/B test indicator — should owner see «вы участвуете в эксперименте: X режим помощника»? | NO MVP — keep AI changes behind the scenes. Owner sees results, not the experiment mechanic. | Founder | 🟢 |
| **Q-AD6** | Time-zone handling for tenant in non-default region — show tenant timezone or browser timezone? | Tenant configured timezone (per `Tenant.timezone` field). Banner clarifies if browser differs > 1 hour. | Eng | 🟡 |
| **Q-AD7** | Dashboard auto-refresh frequency — real-time WebSocket OR pull every X minutes? | Pull every 5 min for KPIs; charts on user request (period change). WebSocket overhead not justified for analytics. | Eng | 🟡 |
| **Q-AD8** | Print-friendly view? | NO MVP. PDF export covers print use case in Phase 3. | UX | 🟢 |
| **Q-AD9** | Comparison overlay style — dotted line vs side-by-side bars? | Dotted line for time charts (less cluttered); side-by-side for distribution charts | UX | 🟢 cosmetic |
| **Q-AD10** | Insight dismissal vs hiding for everyone — per-user or per-tenant? | Per-user dismissal (Owner can dismiss but Admin still sees). Important context may need multiple admins. | PM | 🟢 |

---

## 22. Cross-document linkage

- Foundation: [`attribution-policy.md`](../policies/attribution-policy.md) (source of all booking distribution data)
- Foundation: [`memory/project_attribution_extensible_model.md`](~/.claude/projects/.../memory/project_attribution_extensible_model.md)
- Permissions: [`conversation-ownership-policy.md`](../policies/conversation-ownership-policy.md) §4
- Persona context: [`docs/design/assistant-persona.md`](../policies/assistant-persona.md)
- Master own-dashboard: [`2026-05-18-master-mobile-handoff.md`](./2026-05-18-master-mobile-handoff.md) §M3
- Learning Queue insights pattern: [`2026-05-17-conversations-handoff.md`](./2026-05-17-conversations-handoff.md) Screen C4 insights panel
- Billing context: [`memory/project_pricing_model_hybrid.md`](~/.claude/projects/.../memory/project_pricing_model_hybrid.md)
- Decisions log: [`decisions-log.md`](../decisions-log.md) — Q-AD1 to Q-AD10 added

---

## 23. What this UNBLOCKS

- **Trial-end renewal conversion** — dashboard is the value-evidence salon shows themselves
- **Sales conversations** — «посмотрите что вы получите» — share screenshot
- **CSM-led activation** — when CSM helps onboard, dashboard is the «first wow» moment
- **Compounding intelligence proof** — show salon week-over-week growth, not static state
- **Engineering data infrastructure** — `AnalyticsSnapshot` table + aggregation cron — reusable for future analytics features

## 24. Sign-off

| Role | Approval | Date |
|---|---|---|
| Designer | ☐ | |
| Product | ☐ | |
| Engineering (FE) | ☐ | |
| Engineering (BE — aggregation layer) | ☐ | |
| Engineering (Data — analytics queries) | ☐ | |
| QA (chart correctness) | ☐ | |
| Founder (Q-AD3 benchmarking + Q-AD5 A/B mechanics visibility) | ☐ | |
