# Screen: customer-main-wellness-dashboard

| Field | Value |
|---|---|
| **Audience** | customer (Анна, ACTIVE_REGULAR state) |
| **Phase** | P0 — pilot 15 July 2026 (Penza) |
| **Status** | draft — Phase A–G done, awaiting tech lead sign-off + frontend handoff to W1/Iota |
| **Channel** | MAX webview (Mini App inside MAX messenger) |
| **Stream** | Tau (UX/Design) |
| **Date** | 2026-05-25 r1 |
| **Severity** | P0 BLOCKER — без этого экрана пилот не запускается |
| **Selected variant** | **Variant B — Compact Hero v2** (Variant A — Pure Compact — held as alt) |

---

## 1. Контекст

Customer Анна открывает MAX-мессенджер, переходит в Mini App Ayla (через deeplink из bot DM или через Mini App tab). Этот экран — первое что она видит при каждом открытии после initial first-touch.

**Откуда пришёл:** bot DM с Ayla (вошёл по ссылке `start_param=home`) ИЛИ прямой запуск Mini App из MAX UI. Per `customer-first-touch-and-mini-app-states.md` §6: state-машина resolve'ит уже существующего customer'а и рендерит ACTIVE_REGULAR Home (это наш экран).

**Куда уходит:**
- Tap по pulse Питание → drill-down meal log (вкладка День → Питание)
- Tap по pulse Вода → drill-down water log (вкладка День → Вода)
- Tap по pulse Цель → goal review (вкладка День → Цели)
- Tap по quick action 📸 → camera UI (food scanner skill)
- Tap по quick action 💧 → inline log (no navigation)
- Tap по quick action 🎯 → goal editor / selector
- Tap по quick action 📅 → catalog (вкладка Услуги)
- Tap по booking card «Открыть запись» → booking detail (вкладка Записи)
- Tap по «Все записи →» → bookings list (вкладка Записи)
- Tap по «Поговорим» / Ayla wordmark в header → bot DM с context
- Tap по nav → переключение вкладок (Главная / День / Записи / Услуги / Я)

---

## 2. Founder pivot 2026-05-25 — IA r1 deviation

> **Этот экран расширяет existing IA r1 §2.6 «Home for ACTIVE_REGULAR»** per founder decision 2026-05-25 (см. memory `project_variant_b_wellness_mvp`).

**Что было в IA r1:** Главная для ACTIVE_REGULAR = wellness summary + cadence rebook + AI recommendation. Food / water / goals были вынесены во вкладку «Самочувствие» (Phase 2-3 фича).

**Что меняет founder pivot:** Главная = полноценный wellness dashboard прямо с MVP. Food + water + goals + bookings + weekly progress — всё на главной. AI insight cards deferred post-pilot per Q-BACK-4 2026-05-25 (safety filter not in production, sample rule already medical-adjacent).

**Резолюция:** Tau рисует под founder pivot. IA r2 обновляется ретроактивно после approve этого экрана. Принципы IA сохранены:
- 5 surfaces в bottom nav (Главная / День / Записи / Услуги / Я)
- Salon как third-party reference (Ayla не подчинена ни одному из салонов)
- 3-zone privacy framework сохраняется (БЖУ + cал — 🟢, нет жёлто-/красно-зонных данных на главной)
- Ayla brand identity сохраняется (lowercase wordmark, first-person voice)

**Action item:** trigger IA r2 ticket после approve этого handoff. Owner: UX Architect.

---

## 3. ASCII layout — Variant B (Compact Hero v2) — MAIN

**Viewport:** 360–414dp width × 640dp visible · MAX system bars ~80dp top
**Above-the-fold:** 56 (header) + 70 (greeting + one-liner) + 180 (pulse) + 24 (quick actions header) + 120 (quick actions 2×2) ≈ 450dp.
**After system bars:** 560dp доступно → запас 110dp = первые 2 кнопки (📸 + 💧) точно видны выше скролла.

```
┌──────────────────────────────────────────────┐
│  ayla ✨   спросить̲           👤  ⚙          │  Header 56dp
│  ─────────────────────────────────────       │
│                                               │
│  Доброе утро, Анна 🌿                         │  Greeting 28dp
│  Хороший старт дня. Давай мягко               │  Human one-liner 42dp
│  доберём воду.                                │  context-aware, brand moat
│                                               │
│  ┌──────────────────────────────────────┐   │  Pulse strip 180dp
│  │  🍽  Питание                          │   │  Conditional БЖУ:
│  │  1 240 / 2 100 ккал · 59 %            │   │  показывается только
│  │  Б 65 · Ж 40 · У 120 г                │   │  если pfc != null
│  │  ▓▓▓▓▓▓▓░░░░░░░                       │   │  (скрываем строку, не
│  ├──────────────────────────────────────┤   │   показываем прочерки)
│  │  💧  Вода                              │   │
│  │  4 / 8 стаканов                        │   │
│  │  ●●●●○○○○                              │   │
│  ├──────────────────────────────────────┤   │
│  │  🎯  Меньше стресса · 3-я неделя       │   │
│  │  ▓▓▓▓▓▓▓▓▓▓▓▓░░░  78 %                │   │
│  └──────────────────────────────────────┘   │
│                                               │
│  Что сделаем сейчас                           │  24dp header
│                                               │
│  ┌─────────────────┐ ┌─────────────────┐   │  Quick actions 120dp
│  │  📸              │ │  💧              │   │  2×2 grid, унифицирован
│  │  Сфотографируй   │ │  + стакан        │   │  imperative voice
│  │  еду             │ │  250 мл          │   │  (Brand Guardian fix)
│  └─────────────────┘ └─────────────────┘   │
│  ┌─────────────────┐ ┌─────────────────┐   │
│  │  🎯              │ │  📅              │   │
│  │  Моя цель        │ │  Найди           │   │  «Моя цель» если есть,
│  │                  │ │  услугу          │   │  «Выбери цель» если нет
│  └─────────────────┘ └─────────────────┘   │
│                                               │
│  ─ ─ ─ ─ ─ ─ ─ scroll ~450dp ─ ─ ─ ─ ─       │
│                                               │
│  Цели сегодня                                 │  Block 4 — text actions
│                                               │  без progress bars
│  💧 Ещё 4 стакана до цели                     │
│  🍽 Добрать белок · ещё 35 г                  │
│                                               │
│  ───────────────────────────────────────     │
│                                               │
│  Ближайшая запись                             │  Block 5 + multi-record
│                                               │
│  ┌──────────────────────────────────────┐   │
│  │  Завтра · пт · 16:00                  │   │
│  │  Массаж лимфодренаж · 60 мин          │   │
│  │  у Ирины · Формула тела               │   │  salon = third-party
│  │  ул. Тверская 12                       │   │
│  │                                        │   │
│  │  Ещё 2 записи на этой неделе          │   │  ← critical для
│  │                                        │   │     Penza multi-tenant
│  │  [ Открыть запись ]                    │   │     pilot
│  │  [ Перенести ] [ Маршрут ]             │   │
│  │  [ Все записи → ]                      │   │  ← collapsed if 1 record
│  └──────────────────────────────────────┘   │
│                                               │
│  ───────────────────────────────────────     │
│                                               │
│  Прогресс недели                              │  Block 6 — text summary
│                                               │  + cold-start gate
│  💧 Вода: 4 из 7 дней                         │  показывается ТОЛЬКО
│  🍽 Питание: 5 из 7 дней                      │  если ≥3 дней с logs
│  📅 Активность: 5 дней                        │  на текущей неделе
│                                               │
│  [ Подробнее в Дне ]                          │
│                                               │
├──────────────────────────────────────────────┤
│  🏠      ☀        📅      💅      👤         │  Block 7 — Bottom nav 56dp
│ Главная   День    Записи  Услуги   Я          │  «Самочувств.» → «День»
│ ▔▔▔▔▔                                        │  emoji ☀ заменяет 🌿
└──────────────────────────────────────────────┘  (Q-BACK-4 2026-05-25:
                                                   AI insight card REMOVED
                                                   — re-introduce post-pilot
                                                   после Alpha safety audit)
```

---

## 4. ASCII layout — Variant A (Pure Compact) — ALT held

Альтернатива для post-pilot ABL если данные покажут что power users хотят более dense view. Не main для pilot.

```
┌──────────────────────────────────────────────┐
│  ayla ✨   спросить̲           👤  ⚙          │  Header 56dp
│  ─────────────────────────────────────       │
│                                               │
│  Доброе утро, Анна · чт 23 мая                │  Single-line greeting
│                                               │
│  ┌────────┬────────┬─────────────────────┐   │  Pulse 3-column 120dp
│  │ 🍽      │ 💧      │ 🎯                  │   │
│  │ 1240   │ 4/8 ст. │ Меньше стресса      │   │
│  │ /2100  │         │ 3-я нед             │   │
│  │ 59%    │ ●●●●○○○○│ 78%                 │   │
│  │ Б65 Ж40│         │                     │   │
│  │ У120 г │         │                     │   │
│  └────────┴────────┴─────────────────────┘   │
│                                               │
│  [📸 Еда] [💧 +Стакан] [🎯 Моя цель] [📅 Услуга]│  Pill row 56dp
│                                               │
│  💧 Ещё 4 стакана  ·  🍽 Добрать белок 35 г  │  Goals one-liner
│                                               │
│  📅 Завтра 16:00 · Массаж лимфодренаж         │  Booking compact
│  у Ирины · Формула тела                       │
│  [ Открыть ] [ Перенести ] [ +2 записи ]      │
│                                               │
│  Неделя: 💧 4/7  ·  🍽 5/7  ·  📅 5 дн.        │  Weekly one-liner
│                                               │  (AI insight inline REMOVED
│                                               │   per Q-BACK-4 2026-05-25)
│                                               │
├──────────────────────────────────────────────┤
│  🏠      ☀        📅      💅      👤         │
│ Главная   День    Записи  Услуги   Я          │
│ ▔▔▔▔▔                                        │
└──────────────────────────────────────────────┘

Total height ~600dp — почти всё в один viewport
```

### Trade-off B vs A

| Aspect | Variant B (Compact Hero, MAIN) | Variant A (Pure Compact, alt) |
|---|---|---|
| Hero | Greeting + human one-liner (70dp) | One-line greeting (24dp) |
| Pulse | Vertical stack 3 строки (180dp) | 3-column grid (120dp) |
| Quick actions | 2×2 grid большие targets (120dp) | Pill row 4-in-row (56dp) |
| Goals | Header + 2 строки | Inline one-liner |
| Weekly | Text 3 строки + CTA | One-liner inline |
| Эмоциональность | ⭐⭐⭐⭐ warm | ⭐⭐ functional |
| Density | Medium | High |
| Scroll required | Yes (booking/insight/weekly) | Минимальный |
| Best for | Standard user (Anna persona) | Power user / data-junkie |

**Selected for pilot:** Variant B (warm + brand-differentiated + WCAG-friendlier touch targets).

---

## 5. States покрытие

### State 1 — Loading skeleton (200-800ms)

Header + bottom nav кешированы, рендерятся 0ms. Greeting рендерится ~100ms (Layer 1 Identity cached). Pulse + quick actions = shimmer skeleton. Прогрессивная загрузка остальных блоков после first paint.

```
┌──────────────────────────────────────────────┐
│  ayla ✨   спросить̲           👤  ⚙          │  Real (cached)
│  ─────────────────────────────────────       │
│  ░░░░░░░░░░░░░░░                              │  Greeting skeleton
│  ░░░░░░░░░░░░░░░░░░░░                         │  One-liner skeleton
│                                               │
│  ┌──────────────────────────────────────┐   │
│  │  ░░░░░░░░░░░░░░░░░░                   │   │  Pulse skeleton
│  │  ░░░░░░░░░░░░░░░░░░░░░                │   │  (shimmer animation)
│  │  ░░░░░░░░░░                           │   │
│  ├──────────────────────────────────────┤   │
│  │  ░░░░░░░░░░░░░░░░░░                   │   │
│  ├──────────────────────────────────────┤   │
│  │  ░░░░░░░░░░░░░░░░░░░░░                │   │
│  └──────────────────────────────────────┘   │
│                                               │
│  ░░░░░░░░░░░░░░░░░                            │
│  ┌─────────────────┐ ┌─────────────────┐   │
│  │   ░░░            │ │   ░░░            │   │  Quick actions
│  │  ░░░░░░░░        │ │  ░░░░░░░░        │   │  skeleton
│  └─────────────────┘ └─────────────────┘   │
└──────────────────────────────────────────────┘
```

**Reduced motion:** при `prefers-reduced-motion: reduce` shimmer заменяется на статичный placeholder без анимации (a11y WCAG 2.3.3).

### State 2 — Empty (first-time user, anketa не пройдена)

```
┌──────────────────────────────────────────────┐
│  ayla ✨   спросить̲           👤  ⚙          │
│  ─────────────────────────────────────       │
│                                               │
│  Привет, Анна 🌿                              │  First introduction
│  Я Ayla — помогу разобраться с собой каждый  │  per ayla-identity §7.1
│  день.                                        │
│                                               │
│  ┌──────────────────────────────────────┐   │  Onboarding card
│  │  Начнём с малого?                      │   │  (dismissable, 7d snooze
│  │  Сфотографируй первый приём, добавь    │   │   + auto-hide после
│  │  воду или выбери цель.            [×] │   │   первого logged action)
│  │                                        │   │
│  │  [ Еда ]  [ Вода ]  [ Цель ]          │   │
│  └──────────────────────────────────────┘   │
│                                               │
│  Сегодня                                      │
│  ┌──────────────────────────────────────┐   │
│  │  🍽  Питание                          │   │
│  │  Ещё ничего не залогировано           │   │
│  ├──────────────────────────────────────┤   │
│  │  💧  Вода                              │   │
│  │  0 стаканов сегодня                    │   │
│  │  ○○○○○○○○                              │   │
│  ├──────────────────────────────────────┤   │
│  │  🎯  Цель не выбрана                   │   │
│  │  Расскажи о себе — точнее советую     │   │
│  └──────────────────────────────────────┘   │
│                                               │
│  Что сделаем сейчас                           │
│  [2×2 grid as main]                           │
│                                               │
│  Ближайших записей пока нет                   │
│  ┌──────────────────────────────────────┐   │
│  │  Подобрать услугу под твою цель —      │   │
│  │  расскажу что подойдёт.                │   │
│  │  [ Найди услугу ]                      │   │
│  └──────────────────────────────────────┘   │
│                                               │
│  AI insight скрыт                             │
│  Прогресс недели скрыт                        │
│                                               │
├──────────────────────────────────────────────┤
│  Bottom nav (cached)                          │
└──────────────────────────────────────────────┘
```

### State 3 — Error (API недоступен)

Header + nav работают всегда. Top warning banner показывает one-line про сбой. Per-block stale badges. Quick actions работают локально (localStorage queue).

```
┌──────────────────────────────────────────────┐
│  ayla ✨ спросить̲              👤  ⚙          │
│  ─────────────────────────────────────       │
│  Доброе утро, Анна 🌿                         │  Greeting OK (cached)
│  Хороший старт дня. Давай мягко               │
│  доберём воду.                                │
│                                               │
│  ┌──────────────────────────────────────┐   │  Warning banner
│  │  ⚠ Не получается обновить данные      │   │  calm tone
│  │  Показываю последние сохранённые.     │   │  (per tech lead fix)
│  │  [ Обновить ]                          │   │
│  └──────────────────────────────────────┘   │
│                                               │
│  ┌──────────────────────────────────────┐   │
│  │  🍽  Питание                          │   │  Stale badges
│  │  1 240 / 2 100 ккал · обновлено вчера │   │  per card
│  │  Б 65 · Ж 40 · У 120 г                │   │
│  ├──────────────────────────────────────┤   │
│  │  💧  Вода                              │   │
│  │  4 / 8 стаканов · обновлено вчера     │   │
│  ├──────────────────────────────────────┤   │
│  │  🎯  Меньше стресса · 3-я неделя       │   │
│  └──────────────────────────────────────┘   │
│                                               │
│  Quick actions работают normally              │
│  Booking может загрузиться отдельно (retry)   │
│  AI insight скрыт (нет свежих данных)         │
└──────────────────────────────────────────────┘
```

### State 4 — Offline (сеть отключена)

Persistent top banner. Sync queue indicator для localStorage actions (вода). Photo action НЕ обещаем offline upload (нет file queue в codebase per tech lead Q2).

```
┌──────────────────────────────────────────────┐
│  ⚡ Без сети — показываю последние данные    │  Persistent banner
├──────────────────────────────────────────────┤
│  ayla ✨ спросить̲              👤  ⚙          │
│  ─────────────────────────────────────       │
│  Доброе утро, Анна 🌿                         │
│  Хороший старт дня. Давай мягко               │
│  доберём воду.                                │
│                                               │
│  ┌──────────────────────────────────────┐   │
│  │  🍽  Питание                          │   │
│  │  1 240 / 2 100 ккал                   │   │
│  │  ⏱ обновлено 2 ч назад                │   │
│  ├──────────────────────────────────────┤   │
│  │  💧  Вода                              │   │
│  │  5 / 8 стаканов                        │   │
│  │  ●●●●●○○○                              │   │
│  │  ⏱ +1 стакан ждёт синхронизации       │   │  localStorage queue
│  └──────────────────────────────────────┘   │  (per Q-MAS9, 24h TTL)
│                                               │
│  Что сделаем сейчас                           │
│  ┌─────────────────┐ ┌─────────────────┐   │
│  │  📸              │ │  💧              │   │  💧 OK offline
│  │  Сфотографируй   │ │  + стакан        │   │  📸 disabled
│  │  еду             │ │  250 мл          │   │  (нет offline queue)
│  │  (нужна сеть)    │ │                  │   │
│  └─────────────────┘ └─────────────────┘   │
│                                               │
│  Tap по 📸 в offline → toast:                 │
│  «Для распознавания еды нужна сеть.           │
│   Попробуйте позже.»                          │
└──────────────────────────────────────────────┘
```

### State 5 — Partial (часть API ответила, часть нет)

Per-block isolation. Failed card показывает `—` + retry. Соседи рендерятся normally. Без top-level banner (локальная проблема).

```
┌──────────────────────────────────────┐
│  🍽  Питание                          │  OK
│  1 240 / 2 100 ккал · 59 %            │
│  ▓▓▓▓▓▓▓░░░░░░░                       │
├──────────────────────────────────────┤
│  💧  Вода                              │  Failed
│  — / 8 стаканов                        │  ░░░░░░░░
│  [ ↻ Обновить ]                        │
├──────────────────────────────────────┤
│  🎯  Меньше стресса · 3-я неделя       │  OK
│  ▓▓▓▓▓▓▓▓▓▓▓▓░░░  78 %                │
└──────────────────────────────────────┘
```

### Cold-start UX для Weekly Progress

Per tech lead caveat (мной упущено в первичной версии):

- **Day 1–2 customer:** блок «Прогресс недели» СКРЫТ полностью
- **Day 3+ с ≥3 днями logs:** показывается text summary
- **Day 7+:** full data
- **Customer пропустил 5+ дней на неделе:**
  ```
  ┌──────────────────────────────────────┐
  │  На этой неделе тихо. Если что-то    │
  │  изменилось — расскажи. Я рядом.     │
  │  [ Залогировать сегодня ]            │
  └──────────────────────────────────────┘
  ```

---

## 6. Backend data needs

### Endpoints используемые

| Endpoint / method | Откуда | Куда (UI element) | Сценарий |
|---|---|---|---|
| `NutritionClient.daily_summary()` | `apps/integrations/ayla/nutrition_client.py` | Pulse Питание + Цели сегодня | Calories + БЖУ (pfc может быть null → conditional hide) |
| `NutritionClient.get_water_today()` | same | Pulse Вода + Цели сегодня + Weekly progress | Стаканы выпитые сегодня |
| `NutritionClient.add_water(volume_ml)` | same | Quick action `+ стакан 250 мл` | POST log entry, localStorage fallback offline |
| `NutritionClient.scan_photo(photo)` | same | Quick action `📸 Сфотографируй еду` | Photo recognition + meal log |
| `NutritionClient.log_meal(meal)` | same | Post photo confirm | Save recognized food |
| `NutritionClient.weekly_deficits()` | same | Weekly progress block (cold-start gate) | 7-day aggregate. **AI insight template usage REMOVED** per Q-BACK-4 — post-pilot re-introduction after Alpha safety templates |
| `Layer 1 Identity` (Wellness Profile) | `apps/identity/` | Greeting (display_name_preference) | Имя для приветствия |
| `Layer 2 Goals` (Wellness Profile) | same | Pulse Цель + Quick action 🎯 | Active goal + progress |
| `Layer 5 Behavioral` (Wellness Profile) | same | Greeting context («спокойный четверг») | Day pattern hint |
| `BookingRequest` (status=CONFIRMED, next visit_at) | Ayla djangoproject (split-domain ADR-0009) | Booking card | Ближайшая запись |
| `BookingRequest.count(status=CONFIRMED, this_week)` | same | «Ещё 2 записи на этой неделе» | Multi-record indicator |

### Полей которых может НЕ быть в первый запуск

- **pfc (БЖУ)** — null если nutrition_anketa не пройдена → скрыть строку «Б · Ж · У»
- **Layer 2 Goals** — пусто если customer не выбрал цель → quick action `Выбери цель`
- **Layer 5 Behavioral** — нет паттерна если customer first-week → fallback на neutral greeting line «Сегодня — обычный четверг»
- **Booking next visit** — нет если customer не бронировал → booking card заменяется на «Подобрать услугу под твою цель»

### Caching strategy

- Layer 1 Identity → cached client-side 24h (для greeting render 0ms)
- Pulse data (daily_summary + water) → fetched on each open, stale-while-revalidate 30s
- Bookings → fetched on open, stale-while-revalidate 60s
- Weekly aggregate → fetched daily, cached till midnight customer TZ
- Bottom nav surface state → cached forever (5 tabs не меняются)

---

## 7. Brand notes

### Voice consistency check

| Element | Voice rule | Status |
|---|---|---|
| Greeting «Доброе утро, Анна 🌿» | First-person Ayla не нужен (это header), single emoji ≤1 | ✅ |
| Human one-liner «Хороший старт дня. Давай мягко доберём воду.» | Подруга-эксперт, на «ты», действующая, caring imperative | ✅ |
| Pulse «Питание / Вода / Цель» | System labels — neutral voice OK | ✅ |
| Quick actions imperative («Сфотографируй / + стакан / Моя цель / Найди услугу») | Unified imperative voice per Brand Guardian fix | ✅ |
| Booking «у Ирины · Формула тела» | Salon third-party, master named first | ✅ |
| Empty state «Привет, Анна 🌿 Я Ayla — помогу разобраться с собой каждый день. Начнём с малого?» | First-touch tone, AI honest disclosure («Я Ayla»), single emoji, action question | ✅ |
| Error «Не получается обновить данные. Показываю последние сохранённые.» | Calm tone, sensitive register («grounded, не drama») | ✅ |
| Offline «⚡ Без сети — показываю последние данные» | Calm, informational, не алярм | ✅ |

### Wordmark + emoji

- Header wordmark **lowercase «ayla»** preserved per ayla-identity §7.1
- Греющий emoji **🌿** (sage sprig = wellness vibe, brand-tied)
- ✨ next to wordmark = placeholder for crescent moon ☽ (Phase 2+)
- **День tab uses ☀** (NOT 🌿 — избежали emoji collision per UI Designer finding)

### Salon co-presence

- «у Ирины · Формула тела» — master first, salon как venue
- НЕТ «помощник Формулы тела» / «AI салона X» — Ayla не подчинена
- Booking адрес показывается строкой ниже («ул. Тверская 12»)
- Customer видит salon как provider, Ayla = личный AI

---

## 8. Accessibility (WCAG 2.2 AA)

Полный audit от Accessibility Auditor subagent. ASCII не показывает pixel-level контраст / touch sizes / aria — frontend implementation должна учесть:

### BLOCKERS (must fix перед pilot 15 July)

1. **2.5.8 Target Size (Minimum)** — `Все записи →`, header `👤 ⚙` визуально <24dp.
   **Fix:** transparent padding до 44×44dp hit area. Visual glyph может остаться маленьким.

2. **1.4.3 Contrast Minimum** — Sage-green `#7BA478` на white = ~2.9:1, fails 4.5:1.
   **Fix:** body text + icons использовать darker `#5A8557` (≥4.6:1). Brand sage оставить ТОЛЬКО для decorative fills (≥3:1).

3. **1.1.1 Non-text Content + 1.4.1 Use of Color** — Water dots `●○` и macro bars передают state через color/fill только.
   **Fix:** `role="progressbar" aria-valuenow="4" aria-valuemax="8" aria-label="Вода: 4 из 8 стаканов"`. Скрыть dots с `aria-hidden="true"`.

4. **1.3.1 Info & Relationships** — Pulse «1 240 / 2 100 ккал · 59 % · Б 65 · Ж 40 · У 120 г» = math soup для screen reader.
   **Fix:** composite `aria-label="Питание: 1240 из 2100 килокалорий, 59 процентов. Белки 65, жиры 40, углеводы 120 граммов"`.

5. **3.1.1 Language of Page + 3.1.2 Parts** — Declare `<html lang="ru">`. Wrap «Ayla» в `<span lang="en">Ayla</span>` для правильной RU TTS произношения (избежать «Айла»).

6. **1.4.11 Non-text Contrast** — Empty progress-bar track (light grey) и quick-action borders вероятно <3:1.
   **Fix:** track `#9A9A9A`, borders `#C7C7C7` (verified ≥3:1).

### IMPORTANT

7. **2.4.1 Bypass Blocks + 2.4.3 Focus Order** — Skip link «К основному содержимому» в начале page. Focus order: header → greeting → pulse → quick actions → goals → booking → weekly → nav.

8. **1.4.4 Resize Text + 1.4.10 Reflow** — При 200% zoom на 360dp quick action 2×2 grid и 4-button booking CTA overflow.
   **Fix:** booking actions collapse to primary («Открыть запись») + overflow menu («Перенести / Маршрут / Все записи» в `⋯`). Quick actions stack 1-col при zoom.

9. **2.3.3 Animation from Interactions** — Skeleton shimmer должен respect `prefers-reduced-motion: reduce` — replace с static placeholder.

10. **4.1.3 Status Messages** — Offline banner, «обновлено вчера» badges, partial-failure «— / 8 стаканов» — `role="status"` для announce без focus shift.

11. **3.3.1 Error Identification** — Disabled photo button `aria-disabled="true"` + `aria-describedby` указывает на full explanation (не visual only).

### MINOR

12. Decorative emoji (🌿✨🍽💧🎯📅) → `aria-hidden="true"` если text уже передаёт meaning (избежать double announce).
13. Active bottom-nav state → `aria-current="page"` в дополнение к underline marker.
14. «Ещё 4 стакана до цели» → accessible name «Добрать ещё 4 стакана воды» (linked к water log).

**Estimated fix effort:** 1.5–2 dev-days + один screen-reader pass (NVDA + VoiceOver iOS в MAX webview).

---

## 9. Variants considered

| Variant | Status | Rationale |
|---|---|---|
| **Variant B — Compact Hero v2** | ✅ MAIN | Brand differentiation через human one-liner, warm + structured, touch targets a11y-friendly |
| Variant A — Pure Compact | ⏸ ALT held | Высокая density, post-pilot ABL candidate if data shows demand |
| Variant C — Conversational Ribbon (top 25% = daily Ayla nudge) | ⏸ post-pilot | Требует daily LLM generation infrastructure (cron + safety guardrails + observability) — out of pilot scope per Q-TAU-4 |

---

## 10. Open questions для tech lead

| # | Severity | Question | Lean |
|---|---|---|---|
| Q-TAU-D1 | 🟢 | БЖУ row UX: всегда показывать (если pfc есть) или collapsed default с chevron expand? | Per UI Designer recommendation — collapsed по умолчанию, expand on tap. Освобождает 14dp вертикально |
| Q-TAU-D2 | 🟢 | Pulse 🍽💧🎯 emoji vs SVG icons в production. Brand Guardian рекомендует monochrome SVG | Recommend SVG sage-green for consistency |
| Q-TAU-D3 | 🟡 | Card system unification: pulse internal dividers vs booking/insight bordered containers — какой паттерн default? | Recommend per UI Designer: 12dp radius + 1dp sage-200 border + 16dp padding для всех cards. Pulse = single bordered container с hairline internal dividers |
| Q-TAU-D4 | 🟢 | Услуги tab emoji: 💅 stereotypically beauty-only, не wellness-OS. Заменить? | Per UI Designer: `✂` или spa-neutral icon. Production = Lucide `sparkles` |
| Q-TAU-D5 | 🟢 | Quick action 🎯 в block conflict с pulse 🎯. Replace? | Per UI Designer: quick action → `🧭` (направление) или `🎯` оставить если решим что это intentional «цель» semantic |

---

## 11. Open items для frontend (W1 / Iota implementer)

Эти items не нужно решать перед save файла — они инструкции для разработчика.

1. **БЖУ conditional rendering:** if `daily_summary().pfc === null` → hide string row entirely. NEVER render «Б — · Ж — · У —» placeholder strokes (выглядит как поломка).
2. **«Моя цель» / «Выбери цель» context-aware:** check `Layer 2 Goals.active.length > 0` → render «Моя цель», else «Выбери цель».
3. **«Все записи →» conditional:** if `BookingRequest.count(status=CONFIRMED, this_week) > 1` → show «Ещё N записей на этой неделе» + «[ Все записи → ]» button. If = 1 → hide.
4. **Weekly progress cold-start gate:** show block only if `customer.logged_days_this_week >= 3`. Else hide completely.
6. **Onboarding card hide:** localStorage `onboarding_card_dismissed_at` timestamp. Hide on dismiss OR auto-hide after first logged action (water / meal / goal).
7. **Photo offline:** disable 📸 button visually + show «(нужна сеть)». Tap shows toast «Для распознавания еды нужна сеть. Попробуйте позже.» — NO offline file queue (не строим в MVP).
8. **Water offline:** localStorage queue 24h TTL per Q-MAS9. Show indicator «+N стакан ждёт синхронизации». Auto-flush при reconnect.
9. **Greeting time-sensitivity:** `Доброе утро` (4-12) / `Добрый день` (12-18) / `Добрый вечер` (18-22) / `Спокойной ночи` (22-04) по customer TZ.
10. **Human one-liner generation:** context-aware fallback chain:
    - Утро + good water/food progress: `Хороший старт дня. Давай мягко доберём воду.`
    - Утро + no logs: `Доброе утро. Начнём день?`
    - Середина дня + ahead of goal: `Хорошо идёшь. Запись завтра — всё на месте.`
    - Вечер + close to goal: `Почти всё что хотели. Допей воду перед сном.`
    - Вечер + drop-off: `Тихий день. Если что-то нужно — расскажи.`
    - Fallback: `Что нужно сегодня?`
    Generation = static template selection based on `daily_summary` state, NOT LLM (per Q-TAU-4).

---

## 12. Skills used (subagent review trail)

| Skill / Subagent | Phase | Findings summary |
|---|---|---|
| `frontend-design` (Anthropic skill) | C–E | Avoided AI cliché (purple gradients, Inter default). Sage-green + lowercase «ayla» wordmark + warm tone per Ayla brand |
| `awesome-claude-design` (community recipes) | E | Considered `warm` + `data-dense` families. Outcome: warm family wins for Anna persona, data-dense reserved for Variant A alt |
| `UI Designer` subagent | F (friendly) | 3 top issues: Pulse БЖУ density / Emoji collisions / Card system inconsistency. Verdict: «4 hours polish to ship-ready» |
| `Brand Guardian` subagent | F (friendly) | 3 important fixes applied: Quick Actions verb unification / `Обсудить с Ayla` → `Поговорим` / Pulse icons should be SVG not emoji. Verdict: «brand-aligned with touch-ups» |
| `Accessibility Auditor` subagent | G (adversarial) | 6 blockers (contrast, target size, color-only meaning, ARIA labels, lang, non-text contrast), 5 important, 3 minor. Verdict: «1.5–2 dev-days fix effort before pilot» |

---

## 13. Status next steps

- [x] Phase A — read mandatory docs corpus
- [x] Phase B — plan structure with 8 blocks + 3 variants direction
- [x] Phase C — ASCII skeleton main
- [x] Phase D — detail + 5 states matrix
- [x] Phase E — Variant A (Pure Compact) drawn for compare
- [x] Phase F — friendly review (UI Designer + Brand Guardian subagents)
- [x] Phase G — adversarial review (Accessibility Auditor subagent)
- [ ] **Phase H — HTML preview (skip per tech lead — save tokens)**
- [x] Phase I — save to `docs/screens/customer-main-wellness-dashboard.md`
- [ ] Phase J — handoff block для tech lead

**Severity результирующего экрана:** P0 BLOCKER для pilot 15 July 2026.

**Following streams to engage after sign-off:**
- W1 / Iota — frontend implementation per items 11 above
- UX Architect — IA r2 retroactive update (Q-TAU-1 resolution)
- AI Engineering (post-pilot) — safe AI insight template generation after Alpha safety audit; Q-BACK-4 verdict 2026-05-25 removed insight cards from MVP
- Accessibility Engineer — WCAG 2.2 AA pass per blockers section 8

---

**Last verified:** 2026-05-25 r1
**Tau (UX/Design stream)**
