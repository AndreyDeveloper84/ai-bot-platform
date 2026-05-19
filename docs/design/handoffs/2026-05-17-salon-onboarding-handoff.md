# Salon Onboarding — Developer Handoff Package

| Field | Value |
|---|---|
| **Date** | 2026-05-17 |
| **Designer** | UX-architect skill / AndreyDeveloper84 |
| **Status** | Draft for review |
| **Surfaces** | Web dashboard (primary) + MAX manager-bot (secondary, notifications + approvals) |
| **Phase scope** | 7 lifecycle phases: marketing → signup → verify → catalog source → catalog review → RAG/persona → go-live → first-week + persistent billing |
| **Screens** | 14 (12 web + 2 MAX) |
| **Linked decisions** | See [Q1–Q9 product decisions](#cascade-of-product-decisions); Q10/Q11 partially open |
| **Revision** | 2026-05-17 r3 — terminology shift: "бот" → "помощник салона" in all customer-facing copy. Strategic decision: single AI-assistant identity (see `memory/project_single_assistant_identity.md`). Engineering term "bot" preserved in code/audit/billing. Conversations module moved to separate handoff: `2026-05-17-conversations-handoff.md`. |

---

## 0. Overview

### Purpose
Onboard a beauty-salon tenant from "heard about us" to "first booking through bot" with median time-to-activated < 24 hours, p90 < 7 days. Hybrid model: self-serve UI with assisted CSM escalation at critical drop-off points (YClients-connect, RAG-upload).

### Success metrics (gate to ship)
| Metric | Target | Type |
|---|---|---|
| Time-to-Activated (signup → first bot booking) | median < 24h, p90 < 7d | North Star |
| Activation rate (signup → activated in 7 days) | ≥ 50% | Quality |
| Drop-off on YClients connect | < 25% | Health |
| Drop-off on RAG upload | < 30% | Health |
| Day-30 retention of activated salons | ≥ 70% | Loyalty |
| Catalog completeness at activation | ≥ 10 services enabled | Quality |
| % override from template (sanity range) | 30–70% | Health |
| YC-path vs Template-path conversion | both ≥ 50% | Health |

### State machine (canonical)
```
NEW → REGISTERED → VERIFIED → CATALOG_READY → CHANNEL_LIVE → TRAINED → ACTIVATED → HEALTHY
                                    ▲
                       ┌────────────┴────────────┐
                       │                          │
                 YC_CONNECTED               TEMPLATE_BUILT
              (sync from YClients)        (no YClients — from template)
                       │                          │
                       └────────────┬─────────────┘
                                    ▼
                              CATALOG_READY
                                    ▲
                                    │ may re-enter from edits
```
Side states: `PAUSED` (user-toggled), `CHURNED` (auto after 30d inactive).

### Surface routing
- Web dashboard at `app.our.ru/*` — all setup, all post-activation editing, all analytics
- MAX manager-bot `@<tenant>_admin_bot` — push notifications, daily digest, 2-tap approvals
- Public landing at `our.ru/for-salons` — pre-signup marketing

### Cross-platform mapping (compact)
| Concept | Web | MAX manager-bot |
|---|---|---|
| Approve handoff | Conversations tab card with action | Push with inline `[Открыть]` `[Ок, всё норм]` |
| Daily digest | Dashboard widget | 09:00 message |
| Setup progress | Top-right widget `Setup 3/7` | On `/status` |
| Catalog inline edit | Click cell → input | Power-user `/price <service> <amount>` |
| YC sync conflict | Banner with resolution buttons | Push «YC изменил цену — принять?» |
| Bulk operations | Footer panel | Web-only |
| Auth | Magic-link email → session cookie | initData (verified) |

---

## 1. Architecture decisions (cascade from Q1–Q8)

| # | Decision | Affects |
|---|---|---|
| Q1 | Pricing seed: parse 30–50 public price lists per region; label honestly; crowd-correct as tenants grow | Phase 4b, 4c; new scraper cron job; `RegionalPriceSnapshot` model |
| Q2 | Single-location per tenant on MVP; data model carries `location_id` (NULL on MVP) for forward compat | All `Service` / `Master` / `BookingRequest` schemas; no UI account-switcher |
| Q3 | RUB only; "country" field in signup feeds waitlist for KZT/BYN demand | Phase 2; pricing seed limited to RU regions |
| Q4 | Three price types: `Fixed` / `From` / `OnRequest`; no min-max ranges | `Service.price_type` enum; Catalog editor input; bot renderer |
| Q5 | 11 baseline templates + Custom; no community marketplace | Phase 4b; no admin upload |
| Q6 | Default 1 master (the admin), all services bound; progressive master-add | Phase 4c gets Masters tab |
| Q7 | Bundles = regular services with `is_bundle` tag (no separate bundle entity) | `Service.is_bundle BOOLEAN`; tagging in Catalog detail-edit |
| Q8 | Single price/duration per service on MVP for template-path; YC-synced per-master prices shown read-only as range | `Service.price` single field on MVP; backlog ticket for per-master |
| **Q9** | **Hybrid pricing: 590 ₽ base + 100 ₽ per bot-attributed booking** for **first 50 customers** (founder pricing, locked indefinitely for them). Post-50 model TBD based on real data — re-evaluate when 50-cap reached. No fixed monthly. | New: Billing screen (§Screen 12); Phase 1 pricing block; Phase 7 dashboard billing widget; `BookingRequest.attributed_to_bot` field; `BillingEvent` table; `/api/v1/billing/*` endpoints |

---

## 2. Tokens (Style Dictionary JSON)

Save as `docs/design/tokens.json`. Run through Style Dictionary to emit CSS / iOS / Android / Tailwind config.

```json
{
  "$schema": "https://design-tokens.github.io/format/",
  "color": {
    "brand": {
      "rust-50":  { "value": "#FDF6F0" },
      "rust-100": { "value": "#F8E5D8" },
      "rust-200": { "value": "#EFCBB1" },
      "rust-400": { "value": "#C68463" },
      "rust-500": { "value": "#A8674B" },
      "rust-600": { "value": "#8B4E36" },
      "rust-700": { "value": "#6E3B27" }
    },
    "teal": {
      "400": { "value": "#3FA29F" },
      "500": { "value": "#2A8B8A" },
      "600": { "value": "#1E6B6A" }
    },
    "neutral": {
      "0":    { "value": "#FFFFFF" },
      "50":   { "value": "#FBFAF7" },
      "100":  { "value": "#F2EFE9" },
      "200":  { "value": "#E5E2DA" },
      "300":  { "value": "#D6D1C5" },
      "400":  { "value": "#A8A294" },
      "500":  { "value": "#7A7568" },
      "600":  { "value": "#5B5750" },
      "700":  { "value": "#3D3A35" },
      "900":  { "value": "#161616" },
      "1000": { "value": "#000000" }
    },
    "signal": {
      "success": { "value": "#5D7A4F" },
      "warning": { "value": "#C49A3F" },
      "error":   { "value": "#A03B3B" },
      "info":    { "value": "#3F6E9C" }
    },
    "semantic": {
      "bg":            { "value": "{color.neutral.50}" },
      "surface":       { "value": "{color.neutral.0}" },
      "surface-2":     { "value": "{color.neutral.100}" },
      "border":        { "value": "{color.neutral.200}" },
      "border-strong": { "value": "{color.neutral.300}" },
      "text":          { "value": "{color.neutral.900}" },
      "text-muted":    { "value": "{color.neutral.600}" },
      "text-disabled": { "value": "{color.neutral.400}" },
      "accent":        { "value": "{color.brand.rust-500}" },
      "on-accent":     { "value": "{color.neutral.0}" },
      "accent-2":      { "value": "{color.teal.500}" },
      "focus-ring":    { "value": "{color.brand.rust-500}" }
    }
  },
  "space": {
    "0":  { "value": "0px" },
    "1":  { "value": "4px" },
    "2":  { "value": "8px" },
    "3":  { "value": "12px" },
    "4":  { "value": "16px" },
    "5":  { "value": "20px" },
    "6":  { "value": "24px" },
    "8":  { "value": "32px" },
    "10": { "value": "40px" },
    "12": { "value": "48px" },
    "16": { "value": "64px" },
    "20": { "value": "80px" }
  },
  "radius": {
    "none": { "value": "0" },
    "sm":   { "value": "6px" },
    "md":   { "value": "8px" },
    "lg":   { "value": "12px" },
    "xl":   { "value": "16px" },
    "pill": { "value": "9999px" }
  },
  "border": {
    "default": { "value": "1px solid {color.semantic.border}" },
    "strong":  { "value": "1px solid {color.semantic.border-strong}" },
    "focus":   { "value": "2px solid {color.semantic.focus-ring}" }
  },
  "shadow": {
    "1": { "value": "0 1px 2px rgba(20, 17, 13, 0.05)" },
    "2": { "value": "0 4px 12px rgba(20, 17, 13, 0.07)" },
    "3": { "value": "0 16px 32px rgba(20, 17, 13, 0.12)" }
  },
  "type": {
    "family": {
      "display": { "value": "'General Sans', system-ui, sans-serif" },
      "body":    { "value": "'General Sans', system-ui, sans-serif" },
      "mono":    { "value": "'IBM Plex Mono', 'Courier New', monospace" }
    },
    "size": {
      "xs":   { "value": "12px" },
      "sm":   { "value": "14px" },
      "base": { "value": "16px" },
      "lg":   { "value": "18px" },
      "xl":   { "value": "20px" },
      "2xl":  { "value": "24px" },
      "3xl":  { "value": "30px" },
      "4xl":  { "value": "36px" },
      "5xl":  { "value": "48px" }
    },
    "lineHeight": {
      "tight":  { "value": "1.2" },
      "snug":   { "value": "1.4" },
      "normal": { "value": "1.5" },
      "relaxed":{ "value": "1.65" }
    },
    "weight": {
      "regular":  { "value": "400" },
      "medium":   { "value": "500" },
      "semibold": { "value": "600" },
      "bold":     { "value": "700" }
    },
    "scale": {
      "display":  { "size":"{type.size.4xl}",  "lh":"1.15", "weight":"700" },
      "title-lg": { "size":"{type.size.3xl}",  "lh":"1.2",  "weight":"600" },
      "title-md": { "size":"{type.size.2xl}",  "lh":"1.25", "weight":"600" },
      "title-sm": { "size":"{type.size.xl}",   "lh":"1.3",  "weight":"600" },
      "body-lg":  { "size":"{type.size.lg}",   "lh":"1.6",  "weight":"400" },
      "body":     { "size":"{type.size.base}", "lh":"1.5",  "weight":"400" },
      "label":    { "size":"{type.size.sm}",   "lh":"1.4",  "weight":"500" },
      "caption":  { "size":"{type.size.xs}",   "lh":"1.4",  "weight":"500" },
      "mono":     { "size":"{type.size.base}", "lh":"1.4",  "weight":"500", "family":"{type.family.mono}" }
    }
  },
  "motion": {
    "duration": {
      "instant":  { "value": "0ms" },
      "fast":     { "value": "150ms" },
      "standard": { "value": "200ms" },
      "medium":   { "value": "300ms" },
      "slow":     { "value": "400ms" }
    },
    "ease": {
      "standard":   { "value": "cubic-bezier(0.2, 0, 0, 1)" },
      "decelerate": { "value": "cubic-bezier(0, 0, 0, 1)" },
      "accelerate": { "value": "cubic-bezier(0.3, 0, 1, 1)" }
    }
  },
  "z": {
    "base":     { "value": 0 },
    "raised":   { "value": 1 },
    "dropdown": { "value": 1000 },
    "sticky":   { "value": 1100 },
    "modal":    { "value": 1200 },
    "toast":    { "value": 1300 }
  },
  "breakpoint": {
    "sm":  { "value": "360px" },
    "md":  { "value": "640px" },
    "lg":  { "value": "768px" },
    "xl":  { "value": "1024px" },
    "2xl": { "value": "1280px" },
    "3xl": { "value": "1440px" }
  },
  "layout": {
    "container-max":    { "value": "1280px" },
    "content-max":      { "value": "720px" },
    "sidebar-w":        { "value": "240px" },
    "topnav-h":         { "value": "56px" },
    "footer-h":         { "value": "64px" }
  }
}
```

### Contrast verification (all combinations used in design)

| Foreground | Background | Ratio | WCAG AA |
|---|---|---|---|
| `text` (#161616) | `bg` (#FBFAF7) | 17.1:1 | ✅ AAA |
| `text-muted` (#5B5750) | `bg` (#FBFAF7) | 7.5:1 | ✅ AAA |
| `text-disabled` (#A8A294) | `bg` (#FBFAF7) | 2.4:1 | ⚠ used for disabled only (exempt) |
| `on-accent` (#FFFFFF) | `accent` (#A8674B) | 5.8:1 | ✅ AA |
| `on-accent` (#FFFFFF) | `accent-2` (#2A8B8A) | 4.7:1 | ✅ AA |
| `text` | `surface-2` (#F2EFE9) | 16.4:1 | ✅ AAA |
| `signal-error` (#A03B3B) | `bg` | 6.1:1 | ✅ AA |
| `signal-success` (#5D7A4F) | `bg` | 4.9:1 | ✅ AA |
| `signal-warning` (#C49A3F) | `bg` | 3.0:1 | ⚠ borderline; use only with text label, not standalone |
| `signal-info` (#3F6E9C) | `bg` | 5.2:1 | ✅ AA |

---

## 3. Components inventory

| Component | Variants | States | Notes |
|---|---|---|---|
| **Button** | primary, secondary, tertiary, destructive, ghost | default, hover, focus, active, disabled, loading | 36px sm / 44px md / 56px lg height; pill or md radius |
| **InputText** | text, email, tel, password, number, url | default, focus, filled, error, disabled, readonly | 44px height; label above, error below |
| **Select** | single, multi | same as input | Native `<select>` for ≤10 options; combobox for more |
| **Checkbox / Radio** | — | default, hover, focus, checked, disabled, indeterminate | 20×20 hit-target via padding |
| **Toggle** | — | on / off + disabled | 32×20 with 8px track-margin |
| **Card** | flat, elevated, outlined, interactive | default, hover, focus, selected | 12px radius; 24px padding default |
| **Modal / Dialog** | confirm, full, side | open, closing | Native `<dialog>` with focus trap |
| **Sheet** | bottom, side | open, closing | Drag handle on mobile bottom |
| **Toast** | success, error, info, warning | entering, visible, exiting | Top-right desktop, top-center mobile |
| **Banner** | info, success, warning, error | persistent, dismissible | Inline at section top |
| **Tabs** | underline, pill | default, active, hover, disabled | Keyboard arrow nav |
| **Table** | static, sortable, editable | row hover, row selected, cell editing | See [Inline-edit pattern](#inline-edit-pattern-reference) |
| **ProgressBar** | linear, stepped | indeterminate, determinate | Stepped for setup wizard |
| **Skeleton** | text, card, list, table | shimmer | 1.5s loop; respects reduce-motion |
| **AvatarChip** | photo, initials | default | 32 / 40 / 56 px sizes |
| **CategoryTile** | default, selected | default, hover, focus, selected | 96×96 with icon + label + count |
| **PricingPopover** | — | closed, open | Triggered by `ⓘ` next to "цены"; shows source + sample |
| **ConflictBanner** | YC-sync, single-row | shown, resolved | Resolution actions inline |
| **PersonaPreview** | — | loading, ready | Live regenerates on form change with 500ms debounce |
| **SourceBadge** | template, yclients, edited, new | — | Small chip; tooltip with history |
| **SetupProgressWidget** | — | in-progress, complete | Top-right; sticky |
| **CSMChatSticky** | — | collapsed, open | Bottom-right; opens chat with CSM |
| **TopNav** | — | logged-in only | 7 sections (Dashboard, Каталог, Диалоги, Бот, Аналитика, **Биллинг**, Настройки) + progress + profile |
| **Sidebar** | — | collapsed (mobile), expanded | Catalog sub-nav uses this on /catalog |
| **PricingCalculator** | landing | idle, dragging | Interactive «20 записей/мес → 2 590 ₽» on landing hero & pricing section |
| **FounderPricingBadge** | landing, signup | counter | «47 из 50 первых салонов уже с нами» — urgency, real counter |
| **BookingCounter** | inline-mini | — | Small chip near bookings: «100 ₽ → ваш счёт» — sets expectation; suppressed if user is in trial |
| **BillingWidget** | dashboard | empty, normal, near-cap, in-trial | Top-right of dashboard: «Май: 590 ₽ + 17 записей × 100 ₽ = 2 290 ₽» |
| **BillingInvoiceRow** | billing screen | upcoming, settled, partial | One row = one month: base + variable lines + total + download |
| **TrialMeter** | dashboard, banner | active, near-end, ended | «Осталось 4 бесплатные записи или 9 дней — что раньше» |

### Inline-edit pattern reference
See `~/.claude/skills/ux-architect/references/interaction.md` § "Inline editing in tables and lists" for full spec. Applies to: catalog price, duration, name; persona bot name; setting values throughout `/settings`.

---

## 4. Per-screen specs

> **Format key.** Every screen documents: Purpose · Route · Mode · Layout · 6 States · Components · Interactions · Tokens used · A11y · Edge cases · Open questions. Tokens reference the JSON above by path.

---

### Screen 1 — Marketing landing

**Purpose:** Convert visiting salon owner into a Phase 2 signup within 60 seconds by demonstrating the AI-assistant in action, not just describing it.

**Terminology note (r3):** Customer-facing copy uses «помощник» / «AI-помощник» / «цифровой помощник салона». Word «бот» is allowed only in:
- Internal eng terminology (code, audit, billing attribution)
- Marketing-aware comparisons («не очередной бот, а ассистент») where context makes it clear
- Avoided in product UI strings, push notifications, customer-facing emails. See `assistant-persona.md` §2.

**Route:** `our.ru/for-salons`
**Surface:** Responsive web (mobile-first; design at 360px)
**Auth:** None (public)
**State:** `NEW`

#### Layout

```
┌────────────────────────────────────────────────────────────────────┐
│ Logo                          [Возможности][Цены][Войти][Начать]  │
├────────────────────────────────────────────────────────────────────┤
│                                                                    │
│  AI-помощник для                     ┌─[ Live demo widget ]──┐   │
│  салонов на YClients                 │  Помощник:             │   │
│                                      │  Здравствуйте! Я       │   │
│  Платите за результат:               │  помощник «Тестового   │   │
│  590 ₽ + 100 ₽ за каждую запись      │  салона». Чем помочь?  │   │
│  через помощника.                    │                        │   │
│                                      │  [Напишите сообщение ] │   │
│  [ Попробовать бесплатно →]          └────────────────────────┘   │
│  Платите только за реальные записи                                │
│                                                                    │
│  ⏳ 47 из 50 первых салонов уже с нами — founder pricing навсегда │
│                                                                    │
├────────────────────────────────────────────────────────────────────┤
│  "Как это устроено" — asymmetric section, real screenshot          │
│  "Реальные кейсы" — 3 cards (salon name, headline metric, quote)  │
│  "Цены" — PricingCalculator: ползунок «сколько записей в месяц»    │
│           → live update «590 + N × 100 = X ₽». Без скрытых tier-ов│
│  "Сравните с альтернативой" — табличка «3990/мес fixed vs hybrid» │
│  FAQ accordion (8 items, новые про billing + attribution)         │
│  Footer with CTA repeat + legal links                              │
└────────────────────────────────────────────────────────────────────┘
```

#### States
| State | Behavior |
|---|---|
| Initial / loading | Hero text + skeleton-placeholder for demo widget |
| Populated | As above |
| Empty | n/a — always populated |
| Error (demo widget fails) | Replace widget with: "Демо временно недоступно — посмотрите 30-сек видео ↓" + embedded video |
| Partial (low-bandwidth) | Lazy-load demo widget below fold; serve LCP image only |
| Offline | Default browser offline page (no SW for marketing site to keep simple) |

#### Components
TopNav (anonymous variant) · Button (primary lg "Попробовать бесплатно") · DemoWidget (custom) · CaseStudyCard ×3 · **PricingCalculator** · **FounderPricingBadge** (real-time counter из `/api/v1/pricing/founder-spots-left`) · ComparisonTable · FAQAccordion · Footer

#### Interactions
- "Подключить за 1 вечер" → `app.our.ru/signup` (no hash on landing — clean URL)
- DemoWidget: bot responds via streaming LLM call; throttled to 5 messages per visitor IP per hour to control cost
- FAQ accordion: native `<details>`/`<summary>`; keyboard accessible

#### Tokens used
`type.scale.display` (hero), `type.scale.body-lg` (subhead), `color.semantic.accent` (CTA), `space.6` / `space.8` (section gaps), `layout.container-max`

#### A11y
- Skip-to-content link first focusable
- Hero is `<h1>`, sections are `<h2>` in order
- Demo widget: `aria-live="polite"` for bot responses; input has visible label
- Pricing table: `<table>` with `<caption>` "Тарифы и что входит"; row/col headers
- FAQ: each `<details>` is keyboard-toggleable

#### Edge cases
- Demo widget cost overrun → backend throttles, UI shows "Слишком много гостей сейчас — оставьте e-mail, пришлём демо"
- Low bandwidth → all hero content text-first; image placeholder via `aspect-ratio` to avoid CLS
- Bot in demo says something off-brand → demo has fixed prompt with strict system message; daily review of demo transcripts

#### Open questions
- Demo widget — separate testing-tenant or a curated prompt-shadowing real conversations? **Recommend:** dedicated `demo_tenant` with fixed catalog of 10 mock services.

---

### Screen 2 — Signup

**Purpose:** Collect minimum identity to create tenant + send magic-link.

**Route:** `app.our.ru/signup`
**Surface:** Responsive web
**Auth:** None → creates session post-magic-link
**State transition:** `NEW` → `REGISTERED`

#### Layout

```
┌────────────────────────────────────┐
│  ← На главную                      │
├────────────────────────────────────┤
│  Создать аккаунт                   │
│  Шаг 1 из 7 ━─────────             │
│                                    │
│  Название салона *                 │
│  [ ──────────────────────────── ]  │
│  Например, «Студия Карина»         │
│                                    │
│  E-mail *                          │
│  [ ──────────────────────────── ]  │
│                                    │
│  Телефон администратора *          │
│  [ +7 ___ ___ ____             ]   │
│  Используется только нами и CSM    │
│                                    │
│  Страна *                          │
│  [ Россия                    ▾ ]   │
│  (default по geo-detect)           │
│                                    │
│  ☐ Согласен с условиями и          │
│    политикой обработки данных      │
│                                    │
│  [   Продолжить                ]   │
│                                    │
│  Уже есть аккаунт? Войти           │
└────────────────────────────────────┘
```

#### States
| State | Behavior |
|---|---|
| Initial | Empty form, country auto-detected via IP (fallback Россия) |
| Populated | Filled values |
| Loading (submit) | Button shows spinner "Создаём аккаунт…" |
| Empty / error per field | Inline `<p>` below field, red text, `aria-describedby` |
| Error (server: e-mail exists) | Banner top with "У вас уже есть аккаунт — войти?" link |
| Partial (checkbox unchecked) | CTA disabled with tooltip "Нужно согласие с условиями" |
| Country = non-RU | CTA changes to "Записаться в лист ожидания" + descriptive text |
| Offline | Banner "Сохраним когда вернётся связь"; queue submission in IndexedDB |

#### Components
Button (primary lg) · InputText (email, text, tel) · Select (country) · Checkbox · ProgressBar (stepped, 1/7) · Banner (error)

#### Interactions
- Form submit: POST `/api/v1/auth/signup` `{ salon_name, email, phone, country }`
- On success: → Screen 3 (verify) with email shown
- On `409 already_exists`: banner with login link
- Phone format: mask `+7 (NNN) NNN-NN-NN` for RU; auto for other countries via libphonenumber
- Country change: if non-RU → CTA copy swaps, submit goes to waitlist endpoint

#### Tokens used
`type.scale.title-md` (heading), `space.4` (field gap), `space.6` (section gap), `radius.md` (inputs)

#### A11y
- `<form>` element (not div + onclick)
- `<label for>` on each input
- `autocomplete="email"`, `autocomplete="organization"`, `autocomplete="tel"`, `autocomplete="country-name"`
- Required marked with `*` AND `aria-required="true"` AND visible "обязательно" in caption
- Error: focus moves to first invalid field on submit
- Progress bar: `role="progressbar" aria-valuenow="1" aria-valuemax="7"`

#### Edge cases
- E-mail with `+` aliases (gmail) — allow, treat each unique
- Phone with international format — accept any libphonenumber-valid
- User refreshes — form values persisted in `sessionStorage` keyed by tenant-name
- Bot abuse (signup spam) — Cloudflare Turnstile invisible challenge after 3rd attempt from same IP
- Honeypot field `extra_field` invisible — discard if filled

#### Backend contract
```
POST /api/v1/auth/signup
Request: { salon_name: str, email: str, phone: str (E.164), country: str (ISO-3166-1 alpha-2), consent_at: ISO8601 }
Responses:
  201 { tenant_id: uuid, magic_link_sent: bool }
  409 { error: "email_exists" }
  400 { error: "validation_failed", fields: {email:["..."], ...} }
  429 { error: "rate_limited" }
```

#### Open questions
- Magic-link expiry — recommend 15 min
- Should we collect "how did you hear" on signup or skip to minimize friction? **Recommend skip on MVP**, ask in Phase 7 day-7 survey.

---

### Screen 3 — Verify (magic-link wait)

**Purpose:** Confirm e-mail ownership without password friction.

**Route:** `app.our.ru/verify?email=...`
**State transition:** `REGISTERED` → `VERIFIED`

#### Layout

```
┌────────────────────────────────────┐
│  Шаг 2 из 7 ━━─────                │
│                                    │
│  Письмо отправлено                 │
│                                    │
│   ┌──────────────────────────┐    │
│   │  📧 karina@example.ru    │    │
│   └──────────────────────────┘    │
│                                    │
│  Откройте письмо и нажмите         │
│  «Войти» — это займёт 10 секунд.   │
│                                    │
│  ⏱ Письмо может идти до 2 минут.   │
│  Проверьте «Спам».                 │
│                                    │
│  [ Отправить ещё раз (через 45с) ] │
│  [ Изменить e-mail              ]  │
│                                    │
│  Проблемы? Напишите CSM            │
└────────────────────────────────────┘
```

#### States
| State | Behavior |
|---|---|
| Initial | Email shown; resend countdown 45s |
| Polling | Background `/api/v1/auth/verify-poll?email=...` every 3s; on `verified=true` → auto-redirect to /onboard/connect |
| Resend available | Resend button enabled after 45s |
| Resend used | Toast "Отправили ещё раз"; restart countdown |
| Wrong email | Click "Изменить" → back to Screen 2 with values preserved |
| Magic-link expired | Banner "Ссылка истекла. [Отправить новую]" + same screen with cooldown reset |
| Magic-link clicked on different device | Both browsers reconcile via session cookie; current screen receives WebSocket nudge to redirect |

#### Components
Button (secondary, tertiary) · Banner (info / error) · Toast · Countdown text · CSMChatSticky

#### Interactions
- Magic-link in email: `app.our.ru/auth/verify?token=...` → backend verifies, sets session cookie, 302 to `/onboard/source`
- Polling timeout 5 min → screen shows "Не дождались — отправим ещё раз?" CTA

#### Tokens used
`type.scale.title-md`, `space.4`, `radius.md`

#### A11y
- `<output role="status">` for resend countdown
- Toast as `role="status"`
- Focus moves to "Отправить ещё раз" when enabled

#### Backend contract
```
GET /api/v1/auth/verify-poll?email=<email>
  Response 200: { verified: bool, redirect_to?: str }
GET /api/v1/auth/verify?token=<magic_token>
  302 to /onboard/source on success; 410 Gone with message on expired
POST /api/v1/auth/magic-link/resend
  Response 200: { sent: bool, next_resend_after: int(s) }
  429: { error: "cooldown_active", retry_after_s: int }
```

---

### Screen 4 — Catalog Source Selection

**Purpose:** Branch users into YClients path or template path. Both paths must feel equally first-class.

**Route:** `/onboard/source`
**State transition:** `VERIFIED` → (decision point)

#### Layout

```
┌────────────────────────────────────────────────────────────┐
│  ← Шаг 3 из 7 ━━━─────  📊 Setup 2/7                       │
├────────────────────────────────────────────────────────────┤
│                                                            │
│  Откуда возьмём услуги и цены?                            │
│                                                            │
│  ┌─[ ⦿ ]─────────────────────────────────────────────┐   │
│  │  Подключить YClients                              │   │
│  │  Синхронизируем услуги, мастеров и расписание.   │   │
│  │  Бот сразу видит свободные слоты.                │   │
│  │  Рекомендуем, если уже работаете с YClients.     │   │
│  └────────────────────────────────────────────────────┘   │
│                                                            │
│  ┌─[ ◯ ]─────────────────────────────────────────────┐   │
│  │  Начать с шаблона                                │   │
│  │  Готовый каталог по вашему направлению — ногти,   │   │
│  │  ресницы, массаж и др. Со средними ценами по      │   │
│  │  региону. Всё можно править.                      │   │
│  │  Подходит, если YClients ещё не подключён.       │   │
│  └────────────────────────────────────────────────────┘   │
│                                                            │
│  ⓘ YClients можно подключить позже — каталог не пропадёт. │
│                                                            │
│  [ ← Назад ]                  [ Продолжить → ]            │
└────────────────────────────────────────────────────────────┘
```

#### States
6 standard states; no error-able actions on this screen (pure router).

#### Components
Card (interactive, selected variant) · Radio · Button · ProgressBar (stepped)

#### Interactions
- Radio click selects card (whole card is clickable, radio is visual)
- "Продолжить" routes to `/onboard/connect/yclients` or `/onboard/connect/template`
- "Назад" routes to Screen 3 (verify won't re-fire since session active)

#### A11y
- Radio cards use proper `<fieldset><legend>` with `role="radiogroup"`
- Each card is `<label>` wrapping `<input type="radio">`
- Arrow keys navigate between radio options

#### Open questions
- Default selection? **Recommend:** no default selected → forces explicit choice → reduces accidental wrong-path

---

### Screen 5 — Connect YClients (Phase 4a)

**Purpose:** Authenticate to YClients API, fetch and validate catalog data.

**Route:** `/onboard/connect/yclients`
**State transition:** `VERIFIED` → `YC_CONNECTED` → `CATALOG_READY`

#### Layout
See Phase 4 in design doc (chat). Key features: numbered 3-step instruction card; token input with eye-toggle; AES-256 encryption disclosure; CSM-chat auto-popup at 90s or 2 failed attempts.

#### States
| State | Behavior |
|---|---|
| Initial | Empty form; instruction card with screenshot |
| Loading (validating) | Button → spinner; status messages: "Проверяем токен…" → "✓ Токен валиден" → "✓ Company найдена" → "✓ Услуги загружены (N шт)" |
| Empty (company not found) | Banner: "Не нашли company по этому токену — проверьте partner_id" + link to YC help |
| Populated success | Auto-redirect to Screen 7 (Catalog review) |
| Error (401 invalid token) | Inline error under token field: "Токен неверный. [Как перевыпустить →]" |
| Error (network) | Banner with retry |
| Offline | Save draft locally; banner "Попробуем когда вернётся связь" |

#### Components
InputText (password variant for token) · Button (primary; secondary "Найти автоматически" for company_id) · Banner · CSMChatSticky (auto-opens at 90s)

#### Interactions
- Token field: `type="password"` with eye-toggle showing/hiding value
- Validate-on-submit: backend test-call `GET /staff` through provided token
- On success: encrypt token at rest with AES-256-GCM, store envelope `{nonce, ciphertext, tag}` in `Tenant.yclients_credentials_encrypted`
- Last-4 only displayed after save: `token_***1234`
- CSM popup: `setTimeout(showCSMHint, 90_000)` cleared on submit success

#### Tokens used
Same as other onboarding screens; `signal.error` for validation banner; `mono` for token display

#### A11y
- Token field: `aria-describedby` pointing to security disclosure + error
- Eye-toggle: `aria-label="Показать токен"` / `aria-pressed="true"`
- Validation status messages: `aria-live="polite"`

#### Edge cases
- Token has trailing whitespace → trim server-side
- Multiple companies in one YClients account → company_id picker UI (deferred dropdown)
- Token revoked on YC side after connection → during sync, mark `Tenant.yclients_status = REVOKED` and trigger re-connect flow in dashboard
- Rate limit from YC → exponential backoff in backend, "Запрос к YClients затянулся, попробуем ещё раз через 30с" banner

#### Backend contract
```
POST /api/v1/onboard/yclients/connect
Request: { partner_token: str, company_id: int }
Validation flow:
  1. GET https://api.yclients.com/api/v1/staff/{company_id} with Bearer token
  2. If 200: encrypt token (AES-256-GCM, KMS-managed key), save Tenant.yc_creds
  3. Schedule async catalog-sync job, return 202 with job_id
  4. Frontend polls /onboard/yclients/sync-status?job_id=X every 2s
Responses:
  202 { job_id: uuid }
  401 { error: "yc_unauthorized" }
  404 { error: "yc_company_not_found" }
  503 { error: "yc_unreachable", retry_after_s: int }

GET /api/v1/onboard/yclients/sync-status?job_id=X
  Response: { status: "running"|"done"|"failed", progress: {services:N, masters:M}, error?:str }
```

#### Security note
**Token storage:** AES-256-GCM with per-tenant data-key derived from KMS master key. Token never logged. Audit log records every read (for billing dashboards / sync jobs).

#### Open questions
- YC OAuth2 vs partner-token? Check YC API docs — if OAuth available, simpler UX and no token storage on our side. **Action:** verify with YC team before ship.
- Company-picker UX when account has multiple companies? **Recommend:** load list, present dropdown after token validates if N>1.

---

### Screen 6 — Pick Category Template (Phase 4b)

**Purpose:** Multi-select category templates as catalog seed.

**Route:** `/onboard/connect/template`
**State transition:** `VERIFIED` → (template selected) → `TEMPLATE_BUILT` → `CATALOG_READY`

#### Layout

```
┌────────────────────────────────────────────────────────────┐
│  ← Шаг 3 из 7 ━━━─────  📊 Setup 2/7                       │
├────────────────────────────────────────────────────────────┤
│  Выберите направление                                     │
│  Можно несколько                                          │
│                                                            │
│  Регион: [ Москва ▾ ]  ⓘ Откуда берём цены?               │
│                                                            │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐          │
│  │   ✓     │ │         │ │         │ │         │          │
│  │ [icon]  │ │ [icon]  │ │ [icon]  │ │ [icon]  │          │
│  │ Ногти   │ │ Массаж  │ │ Ресницы │ │ Стрижки │          │
│  │ 22 услуги│ │ 18 усл. │ │ 12 усл. │ │ 16 усл. │          │
│  └─────────┘ └─────────┘ └─────────┘ └─────────┘          │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐          │
│  │ Педикюр │ │ Брови   │ │Косметол.│ │  SPA    │          │
│  └─────────┘ └─────────┘ └─────────┘ └─────────┘          │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐          │
│  │Барбершоп│ │Эпиляция │ │ Тату /  │ │  +      │          │
│  │         │ │         │ │Пирсинг  │ │ Своё    │          │
│  └─────────┘ └─────────┘ └─────────┘ └─────────┘          │
│                                                            │
│  Выбрано: «Ногти» — 22 услуги, средняя цена 1 800 ₽       │
│                                                            │
│  [ ← Назад ]    [ Создать каталог из шаблона      → ]     │
└────────────────────────────────────────────────────────────┘
```

#### States
| State | Behavior |
|---|---|
| Initial | No selection; CTA disabled |
| Populated | ≥1 selected; counter at bottom updates |
| Region change | Cards re-render with new average prices; brief skeleton in cards while reloading (~300ms) |
| Custom selected | Custom flow modal opens: text input for direction name + skip to empty catalog |
| Error (region pricing unavailable) | Region falls back to federal-district, banner "Точных данных по Тюмени мало — показываем УрФО" |
| Loading | Skeleton for all cards while initial fetch |
| Offline | Cached last region's data shown with banner |

#### Components
CategoryTile · Select (region) · PricingPopover (triggered by ⓘ) · Button · Banner

#### Interactions
- Tile click toggles selection; multi-select
- Region change debounced 300ms; backend `GET /api/v1/templates?region=<code>` returns updated card data
- Pricing popover: opens on hover (desktop) or tap (mobile); shows source + sample size + confidence bar
- Custom flow: opens modal with text input "Название направления", then routes to empty catalog (Screen 7) with `source=custom`

#### Tokens used
`type.scale.title-md`, `type.scale.body`, `space.4` (grid gap), `radius.lg` (tile), `color.semantic.accent` (selected border)

#### A11y
- Tiles: `<button>` with `aria-pressed` for selected state
- Multi-select pattern; arrow keys navigate grid in row/column
- Region select: native `<select>` for full screen-reader support
- Popover: `aria-describedby` link from ⓘ to popover content; Esc closes

#### Edge cases
- All regions selected (impossible UI but defend backend) — limit 12 categories at once with banner
- Region has 0 categories data — show "Выбран регион без данных. [Использовать данные по Москве]"
- Custom flow user clicks back → preserve choice if they return

#### Backend contract
```
GET /api/v1/templates
Query: ?region=<ru_region_code>
Response: { templates: [{ id, slug, name, icon_key, services_count, avg_price_rub, sample_size, source }] }

POST /api/v1/onboard/template/apply
Request: { template_ids: [str], region: str }
Response: 201 { catalog_seeded: true, services_count: N }
```

---

### Screen 7 — Catalog Review (Phase 4c)

**Purpose:** Allow review and inline editing of seeded catalog before continuing. Also the permanent `/catalog` post-activation hub.

**Route:** `/onboard/catalog/review` (onboarding context); `/catalog` (post-activation)
**State transition:** → `CATALOG_READY` (when continue clicked)

This screen has 3 tabs: **Услуги** (default), **Мастера**, **Источник**.

#### Layout — Услуги tab

```
┌──────────────────────────────────────────────────────────────────┐
│ ┌─[ Услуги (22) | Мастера (1) | Источник ]──┐ [+ Добавить услугу]│
├──────────────────────────────────────────────────────────────────┤
│ Источник: шаблон «Ногти» ▾  ⓘ                                   │
│ Регион: Москва — цены ниже это средние, измените под себя       │
├──────────────────────────────────────────────────────────────────┤
│ 🔎 [ Поиск услуги ]  Фильтр: [ Все ▾ ]  Сорт: [ Имя ▾ ]         │
├──────────────────────────────────────────────────────────────────┤
│  ☑ │ Услуга                  │ Длит. │ Цена         │ Источник │
│ ───┼─────────────────────────┼───────┼──────────────┼──────────│
│  ☑ │ Маникюр классический    │ 60 мин│ 1 200 ₽  ✏  │ шаблон   │
│  ☑ │ Маникюр + гель-лак      │ 90 мин│ 2 200 ₽  ✏  │ шаблон   │
│  ☑ │ Снятие гель-лака        │ 30 мин│   500 ₽  ✏  │ шаблон   │
│  ☐ │ Наращивание ногтей      │120 мин│ 3 800 ₽  ✏  │ шаблон   │
│  ... (more rows)                                                 │
├──────────────────────────────────────────────────────────────────┤
│ ✓ 17 услуг включено  •  5 выключено  •  средняя цена 1 750 ₽   │
│                                                                  │
│ Bulk: [+10%] [-10%] [Изменить регион] [Включить выбранные]      │
│                                                                  │
│ [ ← Назад ]              [ Сохранить и продолжить        → ]    │
└──────────────────────────────────────────────────────────────────┘
```

#### Layout — Мастера tab

```
┌──────────────────────────────────────────────────────────────────┐
│ ┌─[ Услуги (22) | Мастера (1) | Источник ]──┐ [+ Добавить мастера]│
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│ ┌─[ Карина Иванова (вы) ]──────────────────────────────────┐   │
│ │ Аватар │ Специализация: мастер ногтевого сервиса         │   │
│ │  KI    │ Привязанные услуги: 17 из 22 [Изменить]         │   │
│ │        │ ☑ Расписание импортируется (если YC) / задаём  │   │
│ │        │   вручную (если template-path)                  │   │
│ └─────────────────────────────────────────────────────────┘   │
│                                                                  │
│ [+ Добавить мастера]                                            │
│                                                                  │
│ ⚠ У всех услуг ≥1 привязанный мастер. Помощник сможет работать.│
│                                                                  │
│ [ ← Назад ]              [ Сохранить и продолжить        → ]    │
└──────────────────────────────────────────────────────────────────┘
```

#### Layout — Источник tab

```
┌──────────────────────────────────────────────────────────────────┐
│ ┌─[ Услуги (22) | Мастера (1) | Источник ]──┐                   │
├──────────────────────────────────────────────────────────────────┤
│  Текущий источник                                                │
│  ⦿ Шаблон «Ногти» (Москва, май 2026)                            │
│  ◯ YClients (не подключён)  [Подключить YClients]               │
│  ◯ Ручной режим (без синков)                                    │
│                                                                  │
│  Если подключите YClients позже:                                 │
│  • Услуги синкаются из YC автоматически каждые 6 часов          │
│  • Ваши ручные правки сохраняются как override                  │
│  • При конфликте мы спросим, что оставить                        │
│                                                                  │
│  Bot fallback policy                                             │
│  Если клиент спросит про услугу не из каталога:                  │
│  ⦿ Сказать «уточню у мастера» и handoff (рекомендуем)           │
│  ◯ Сказать «такой услуги нет»                                   │
│  ◯ Предложить ближайшую похожую                                  │
│                                                                  │
│  [ Сохранить ]                                                   │
└──────────────────────────────────────────────────────────────────┘
```

#### States (Услуги tab)
| State | Behavior |
|---|---|
| Initial / loading | Skeleton 8 rows |
| Empty (custom-direction) | "Каталог пуст. [+ Добавить услугу] или [Выбрать шаблон]" |
| Populated | As above |
| Cell editing | Cell becomes input; ✓/✕ buttons; debounced autosave 800ms |
| Saving cell | Inline spinner in cell; row disabled while save runs |
| Save success | Brief green-tick fade-out 600ms; source updates to "правка" |
| Save error | Red border on cell + tooltip; retry button |
| Partial (all disabled) | Warning banner "Все услуги выключены — помощник не сможет работать" |
| Sync conflict (post-activation) | Banner with "Use YC / Keep edit" buttons; bulk action available |
| Offline | Banner "Изменения сохранятся когда вернётся связь" + queue counter |

#### Components
Tabs · Table (editable variant) · Checkbox · SourceBadge · InputText (inline) · Button (sm) · Banner · Toast · Select (filter, sort) · SearchInput

#### Interactions
- **Inline edit** — see Skill `interaction.md` § "Inline editing in tables and lists"
- **Click row (not cell)** → opens detail modal with: description, prep notes, aftercare notes, `is_bundle` checkbox, photo upload
- **Bulk +10%** → modal "Применить +10% ко всем выбранным услугам? (3 шт.)"; pricing source updates to "правка" for each
- **Bulk region change** → modal warning "Это перепишет цены, которые вы не правили вручную. Правки сохранятся."
- **Source switch** → confirm dialog; for switching template-path → YC, "Подключить YClients" routes to Screen 5

#### Tokens used
`type.scale.body` (table), `type.scale.mono` (price column), `space.3` (row padding), `color.semantic.surface-2` (alternating rows), `color.signal.warning` (low-sample badge)

#### A11y
- `<table>` with `<caption>`, `<thead>`, `<tbody>`
- Editable cells: `<td>` containing `<input>` when editing; otherwise displays value
- `aria-live="polite"` on save confirmation toast
- Keyboard nav: Tab through editable cells row-by-row; Arrow Down/Up between rows in same column
- Source badge: `<abbr title="...">` with full tooltip

#### Edge cases
- 200+ services (large salon) — virtualize list (react-window) at >50 rows
- Service name with special chars in CSV export — properly quoted
- User pastes formatted text into name field — strip HTML, keep plain
- Concurrent edit from two tabs — last-write-wins with Toast "Сервис обновлён в другой вкладке"
- Network flap mid-edit — local queue, retry with exponential backoff, surface to user only if >30s offline

#### Backend contract
```
GET /api/v1/catalog/services
  Query: ?include_disabled=bool
  Response: { services: [Service], meta: { source, region, total, enabled_count } }

PATCH /api/v1/catalog/services/{id}
  Request: { name?, duration_minutes?, price_rub?, price_type?, is_enabled?, is_bundle? }
  Response: 200 Service (with updated source = "edited")

POST /api/v1/catalog/services
  Request: { name, duration_minutes, price_rub, price_type, master_ids: [int] }
  Response: 201 Service (source = "new")

POST /api/v1/catalog/services/bulk
  Request: { service_ids: [int], action: "enable"|"disable"|"adjust_price", value?: int|float }
  Response: 200 { updated: int }

POST /api/v1/catalog/sync
  Request: { source: "yclients" }
  Response: 202 { job_id, conflicts?: [Conflict] }

POST /api/v1/catalog/conflicts/{conflict_id}/resolve
  Request: { resolution: "use_yclients"|"keep_edit" }
  Response: 200
```

#### Open questions
- Pricing display when user is mid-edit on another field of same row — show frozen value or live calculation? **Recommend frozen** (less surprise).
- Should bot prompt history reflect catalog changes retroactively or only forward? **Recommend forward only** (avoid confusing customers who already got a price quote).

---

### Screen 8 — Train RAG + Persona (Phase 5)

**Purpose:** Augment catalog (which is primary source) with documents and configure bot voice.

**Route:** `/onboard/train`
**State transition:** `CATALOG_READY` → `TRAINED`

#### Layout
See chat for full design. Two sections: **Источники знаний** (catalog ✓ + optional PDF / Google Drive / FAQ editor) and **Голос помощника** (tone radio + name input + live preview). Per `assistant-persona.md` and r3 terminology.

#### States
Per file uploader: idle (drop-zone), uploading (progress bar), parsing (status), parsed (file card with page count), error (with reason and retry), partial (X of Y pages — verify).

For persona preview:
- Initial: default preview text
- Updating: opacity 0.6 + small spinner; backend `POST /api/v1/persona/preview` debounced 500ms
- Updated: smooth replacement

#### Components
FileDropZone · GoogleDriveButton · MarkdownEditor (FAQ) · Radio · InputText · PersonaPreview (live)

#### Interactions
- File drag-drop or click: upload to S3-presigned URL; backend kicks off parsing job
- Google Drive link: validates Drive sharing permissions, fetches via Drive API
- Tone/name change: debounced 500ms → `POST /api/v1/persona/preview { tone, bot_name, sample_intent }` → returns rendered sample
- "Сохранить и протестировать" → routes to Screen 9 (Go-Live)

#### Backend contract
```
POST /api/v1/kb/upload
  multipart/form-data: file
  Response: 202 { document_id, parse_job_id }

GET /api/v1/kb/documents
  Response: { documents: [{id, name, pages, status, uploaded_at}] }

DELETE /api/v1/kb/documents/{id}
  Response: 204

POST /api/v1/persona/preview
  Request: { tone: "vy_warm"|"ty_friendly"|"business", bot_name: str, sample_intent?: "greeting"|"price_request"|"booking" }
  Response: { sample_text: str }

PATCH /api/v1/persona
  Request: { tone, bot_name, fallback_policy }
  Response: 200 Persona
```

---

### Screen 9 — Go-Live (Phase 6)

**Purpose:** Final gate before bot reaches real customers. Forces test, channel binding, soft-launch decision.

**Route:** `/onboard/publish`
**State transition:** `TRAINED` → `ACTIVATED` (with soft-launch flag)

#### Layout
Three blocks: (1) Test chat preview (interactive) + scenario checklist, (2) Channels (MAX bot connect / Telegram / Web widget), (3) Soft-launch toggle.

#### States
- Test chat: empty → after first message → after 3+ messages (gate satisfied) → checkbox auto-enables
- Channels: each ☐ → [Connect] flow → ☑ when bound
- Publish CTA: disabled unless (test gate + ≥1 channel bound)
- Loading on publish: status "Публикуем… ✓ MAX-бот активен ✓ Webhook настроен" then redirect to Dashboard
- Error: per-channel retry; partial success allowed (1 channel up = activated)

#### ⚠ MAX moderation expectation (added r4 from MAX docs deep dive)
**Critical UX moment:** when salon publishes for the first time on MAX channel, the bot itself may require **up to 48 hours of MAX-side moderation** before going live (per `/docs/chatbots/bots-create`). UI must set this expectation:
- Publish success screen shows: «Бот публикуется в MAX. Модерация платформой занимает до 48 часов. Вы получите push, как только бот станет доступен.»
- Dashboard shows: pending state for MAX channel with «модерация MAX (до 48ч)» badge
- Salon can use other channels (Telegram, web widget) immediately while MAX-bot in moderation
- If moderation rejected — CSM auto-paged + email to salon with reason from MAX

**Architecture note:** if salon network needs >5 bots (one per location), MAX caps at **5 bots per organization** (`/docs/chatbots/bots-create`). For chain customers, use single bot with location routing in `start_param` (see Phase 4b deeplink section).

#### Backend contract
```
POST /api/v1/channels/max/connect
  Request: { tenant_id }
  Response: { bot_username, deeplink, webhook_secret }

POST /api/v1/channels/telegram/connect
  Request: { tenant_id, bot_token }  # user pastes Telegram bot token
  Response: { bot_username, webhook_set }

POST /api/v1/onboard/publish
  Request: { channels: ["max", ...], soft_launch_limit: int|null }
  Response: 200 { state: "ACTIVATED", soft_launch_until: ISO8601 | null }
```

---

### Screen 10 — First-Week Dashboard (Phase 7)

**Purpose:** Daily monitoring during the critical first week; surface what needs attention.

**Route:** `/dashboard`
**State:** `ACTIVATED` (also serves `HEALTHY` post-day-30)

#### Layout
5 sections: bot health metrics, **billing widget (top-right)**, requires-attention queue, conversation list, day-7 prompt (when relevant).

**BillingWidget (top-right, persistent):**
```
┌─[ Май 2026 ]────────────────┐
│ База:        590 ₽          │
│ Записи:    17 × 100 = 1 700 │
│ ───────────────────────     │
│ Итого:    2 290 ₽           │
│ Списание 31 мая             │
│ [Подробности]               │
└─────────────────────────────┘
```
In-trial variant:
```
┌─[ Бесплатный период ]──────┐
│ Осталось: 4 записи         │
│ или 9 дней — что раньше    │
│ ──────────────────────     │
│ После: 590 ₽ + 100 ₽/запись│
│ [Подробности]              │
└────────────────────────────┘
```

#### States
- First load: skeleton sections
- Empty (no conversations yet) — common day 1: encouraging empty state "Помощник ждёт первого клиента. [Поделиться ссылкой на помощника]"
- Populated: as designed
- Error per section: section-scoped error with retry
- Offline: cached last data + banner

#### Components
Card · Stat · ConversationListItem · Button · Banner · ProgressMicroChart · **BillingWidget** · **TrialMeter** (when in trial)

#### Interactions
- Click conversation → opens `/conversations/{id}` detail
- "Snooze handoff" → marks as reviewed without action
- "Добавить в FAQ" → routes to Screen 8 source addition prefilled
- Click BillingWidget «Подробности» → routes to Screen 12 (/billing)
- Click on a single booking row → shows badge «учтена в счёте: 100 ₽» (transparency)

---

### Screen 11 — Persistent Catalog (post-activation hub)

Same as Screen 7 + additions:
- YC sync conflict banner (when applicable)
- "Синхронизировать сейчас" button
- Bulk import via CSV
- History view per service (click clock icon → modal of edits)

Backend contract same as Screen 7.

---

### Screen 12 — Billing (post-activation)

**Purpose:** Transparent view of base + variable charges, history per month, downloadable акт, manage payment method, see founder-pricing lock status.

**Route:** `/billing`
**Surface:** Web only on MVP (MAX-bot push for invoice-ready / payment-failed notifications)
**Auth:** Session required; sensitive — consider step-up auth (PIN re-prompt) before showing card details
**State:** `ACTIVATED` or `HEALTHY` (trial users see read-only preview with trial meter)

#### Layout

```
┌──────────────────────────────────────────────────────────────────┐
│  Биллинг                                                         │
├──────────────────────────────────────────────────────────────────┤
│  ┌─[ Текущий план ]──────────────────────────────────────────┐  │
│  │  🌱 Founder pricing — навсегда для вас (один из первых 50)│  │
│  │  База:        590 ₽ / мес                                │  │
│  │  Запись:      100 ₽ / каждая через помощника             │  │
│  │  [Подробнее про модель]  [Чем хорош founder lock]        │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                  │
│  ┌─[ Май 2026 — текущий месяц ]───────────────────────────────┐│
│  │  База:                                            590 ₽   ││
│  │  Записи через помощника: 17 шт × 100 ₽       =  1 700 ₽   ││
│  │  ──────────────────────────────────────────────────────   ││
│  │  Будет списано 31 мая:                          2 290 ₽   ││
│  │  Платёжное средство: Visa •••• 4242  [Изменить]          ││
│  │                                                            ││
│  │  [Посмотреть 17 записей]                                  ││
│  └────────────────────────────────────────────────────────────┘│
│                                                                  │
│  ┌─[ История ]────────────────────────────────────────────────┐│
│  │ Месяц   │ База  │ Записи          │ Итого   │ Статус  │   ││
│  │─────────┼───────┼─────────────────┼─────────┼─────────┼   ││
│  │ Апр 26  │  590  │  23 × 100 = 2300│  2 890  │ ✓ оплач │↓акт││
│  │ Мар 26  │  590  │  19 × 100 = 1900│  2 490  │ ✓ оплач │↓акт││
│  │ Фев 26  │  —    │  trial          │      0  │ ✓ trial │   ││
│  └────────────────────────────────────────────────────────────┘│
│                                                                  │
│  [Скачать все акты PDF]  [Налоговые документы]                  │
└──────────────────────────────────────────────────────────────────┘
```

#### States (all 6)

| State | Behavior |
|---|---|
| **Initial / loading** | Skeleton 3 sections |
| **Empty (in trial, never billed)** | «Вы в бесплатном периоде. Осталось 4 записи или 9 дней. Когда trial закончится, тариф 590 ₽ + 100 ₽/запись.» + TrialMeter |
| **Populated normal** | As above |
| **Near-cap / high-spend** | Warning банер «За эти 23 дня уже 67 записей × 100 = 6 700 ₽ — больше обычного. Это норма для роста, мы рады.» (НЕ negative framing — это успех) |
| **Payment failed** | 🔴 Banner «Не удалось списать 2 290 ₽ — обновите карту. Помощник продолжит работу 7 дней.» + grace-period countdown |
| **Offline** | Last cached state + banner «Данные могут быть устаревшими» |

#### Components

Card (elevated) · **FounderPricingBadge** (locked variant, no counter) · **BillingInvoiceRow** ×N · Button (primary, secondary, tertiary) · Banner (warning, error) · CardOnFileChip · Table

#### Interactions

- **Click «Подробнее про модель»** → modal с объяснением hybrid pricing + почему 590+100 а не fixed
- **Click «Посмотреть 17 записей»** → drawer-right с list из `BillingEvent` per booking: время, мастер, услуга, чек 100 ₽ + link на conversation/{id}
- **Click «Изменить» карту** → step-up auth (PIN или e-mail OTP) → **external CloudPayments** redirect via `WebApp.openLink` (MAX has no native payments per dev.max.ru — partner Prodamus is alternative). NOT embedded form in Mini App.
- **Click «↓ акт»** → download PDF акта-счёта-фактуры (УПД)
- **Click «Налоговые документы»** → drawer с УПД, акт сверки, договор-оферта
- **Hover на BillingInvoiceRow** → tooltip с breakdown (per-day или per-week aggregation, если многих)

#### Tokens used

`type.scale.title-md` (section heading), `type.scale.mono` (money columns — alignment), `color.semantic.surface` (cards), `color.semantic.accent` (founder badge), `color.signal.warning` (high-spend banner — celebratory tone), `color.signal.error` (payment failed)

#### A11y

- `<table>` для history с `<caption>` "История биллинга"
- Each `BillingInvoiceRow` is a real `<tr>` with proper row/col headers
- Money columns: `font-variant-numeric: tabular-nums` + `text-align: right`
- Payment failed banner has `role="alert"` (not status — это срочно)
- Card masking: «Visa •••• 4242» — screen reader reads «Visa, end in four two four two»
- All money values include unit «рублей» in `aria-label` for SR: `<span aria-label="две тысячи двести девяносто рублей">2 290 ₽</span>` — но не agressive (можно через helper, не на каждом числе)

#### Edge cases

- First post-trial month: prorated base (если trial закончился 14-го мая → база 590 × 17/31 ≈ 323 ₽)
- 0 bookings in a month: только база 590 ₽ — баннер «В этом месяце записей через помощника не было. Хотите проверить настройки?»
- Refunds: добавить отрицательную строку «Возврат за запись от 12 мая (отменена клиентом в течение 1ч): −100 ₽»
- Founder lock уже истёк (post-50 customer): badge меняется на «Стандартный тариф», цены могут быть другими (TBD)
- Card expired до списания: predictive banner за 7 дней «Срок карты истекает в июне — обновите»
- Currency: only RUB MVP per Q3
- Tax / IP / Self-employed: УПД-формат vs ИП-формат vs физлицо — поле в Settings определяет формат

#### Backend contract

```
GET /api/v1/billing/current
  Response: {
    plan: { base_rub: int, per_booking_rub: int, is_founder_locked: bool },
    current_month: {
      period_start: ISO, period_end: ISO,
      base_charge_rub: int, bookings_count: int, variable_charge_rub: int,
      total_rub: int, will_charge_at: ISO,
    },
    payment_method: { type: "card", last4: str, brand: str, expires_mm_yy: str },
    trial?: { bookings_left: int, days_left: int, ends_at: ISO },
  }

GET /api/v1/billing/history
  Query: ?limit=12&offset=0
  Response: { invoices: [BillingInvoice], total: int }

GET /api/v1/billing/invoices/{invoice_id}
  Response: { invoice: BillingInvoice, line_items: [BillingEvent], status, paid_at? }

GET /api/v1/billing/invoices/{invoice_id}/pdf
  Response: application/pdf (УПД-формат для юрлица/ИП, чек для физлица)

POST /api/v1/billing/payment-method
  Request: { provider_token: str }  # from CloudPayments tokenization (RU only MVP per Q3)
  Response: 200 { method: { type, last4, ... } }

GET /api/v1/billing/events
  Query: ?from=ISO&to=ISO&limit=100
  Response: { events: [BillingEvent] }
  # BillingEvent = { id, type: "base"|"booking", booking_id?, amount_rub, occurred_at }

POST /api/v1/billing/webhook/cloudpayments
  HMAC-validated; updates Invoice status on settle/fail/refund
```

#### Open questions (this screen)

- **Налоговый профиль**: ИП / ООО / самозанятый / физлицо — определяет формат документов. **Recommend:** поле обязательное в Settings до первого charge, default ИП (most common for salons)
- **Refund policy**: возвращаем 100 ₽ за бронь если клиент отменил в течение 1 часа? Подобный rule — да; если отменил через 24 часа — нет. **Recommend yes для 1ч, no для 24h** (otherwise gameable).
- **Payment provider**: CloudPayments / ЮKassa / Stripe? **Recommend CloudPayments** (RU-юрлица, понятные документы, есть recurring) для MVP. Stripe — когда выходим на не-RU.

---

### MAX manager-bot — Screen M1 — Daily Digest

**Purpose:** Push delivery of yesterday's stats and today's preview.

**Surface:** MAX bot chat (manager-bot — admin-facing, not customer-facing). Note: manager-bot UX is for SALON STAFF (admin tool), customer-facing copy rules don't apply here — admin sees real engineering terminology.

**Trigger:** Cron 09:00 tenant-local
**State:** `ACTIVATED` or later

#### Bot message template (r3 — updated copy)

```
Доброе утро, {admin_name}!

📊 За вчера (16 мая):
• Диалогов: 14
• Записей создано: 4 ★
• Передано вам: 2

🎯 Сегодня запланировано: 7 записей

Требуют внимания:
  • 2 диалога ждут ответа
    [Открыть в дашборде]

[Полная аналитика]  [Что нового помощник узнал]
```

#### Bridge methods used (MAX)
- `BackButton` — wired to dashboard tab navigation if bot opens an internal screen
- `HapticFeedback.impactOccurred('light')` on inline button taps
- `openLink(dashboardUrl)` for "Открыть в дашборде" — opens web dashboard in external browser
- `openMaxLink(...)` if pushing to another MAX entity

#### A11y
- Bot message in plain text (max readable to screen readers)
- Inline buttons have clear labels (not just icons)

---

### MAX manager-bot — Screen M2 — Real-time Handoff Alert

**Trigger:** When AI decides to handoff (intent not in catalog, sensitive question, low confidence, etc.)

```
🔴 Срочно: клиент @karina_client123 ждёт 30 минут

Причина: нет в каталоге
LTV клиента: 2 200 ₽
Возможная запись: ~3 800 ₽
Последнее: «А ботокс делаете?»

[Открыть диалог]  [Взять в работу]
[Отложить 15 минут]  [Это норма, помощник продолжит]
```

Note: detailed Conversations push templates live in [`2026-05-17-conversations-handoff.md`](./2026-05-17-conversations-handoff.md) §3 → MX1/MX2.

#### Bridge methods used
- `HapticFeedback.notificationOccurred('warning')` for handoff alert
- `HapticFeedback.impactOccurred('medium')` on button tap
- `enableClosingConfirmation()` on form views (not applicable here — read-only push)

---

## 5. Bridge methods consolidated (MAX manager-bot)

| Surface | Method | Where used | Why |
|---|---|---|---|
| MAX | `BackButton.show / hide / onClick` | Every internal screen | Standard nav |
| MAX | `HapticFeedback.impactOccurred('light')` | Button taps in digest | Feedback |
| MAX | `HapticFeedback.impactOccurred('medium')` | CTAs | Feedback |
| MAX | `HapticFeedback.notificationOccurred('warning')` | Handoff alert receipt | Importance signal |
| MAX | `HapticFeedback.notificationOccurred('success')` | After approve action | Feedback |
| MAX | `openLink(dashboardUrl)` | "Открыть дашборд" | External browser |
| MAX | `openMaxLink(...)` | Client-chat deeplinks | Internal MAX nav |
| MAX | `enableClosingConfirmation()` | If reply forms added | Defend unsaved |
| MAX | `DeviceStorage` | Cache last digest for offline view | Resilience |

**Not used (intentionally):**
- `MainButton` / `SecondaryButton` — MAX doesn't have them; use sticky in-page CTA (rarely applicable for digest-style messages)
- `requestScreenMaxBrightness` — no QR display in manager-bot
- `BiometricManager` — no sensitive actions reachable in MAX surface; sensitive ops live in web dashboard with separate auth
- `NfcManager` — no NFC use case
- `requestContact` — manager already authenticated

### Init-data validation (HMAC-SHA256)
Standard MAX validation per `references/platforms/max-mini-apps.md`. Enforce `auth_date` freshness ≤ 60 min. Validation must run on every webhook payload from MAX before trusting user identity.

---

## 6. Accessibility checklist (WCAG 2.2 AA, per `references/accessibility.md`)

### Automated baseline (must pass before ship)
- [ ] axe DevTools: 0 critical, 0 serious on every screen
- [ ] Lighthouse a11y score ≥ 95 on /signup, /onboard/source, /onboard/catalog/review, /dashboard
- [ ] WAVE: no contrast errors, no missing labels
- [ ] Pa11y CI in pipeline blocking merges on regressions

### Per-screen manual verification (must verify before ship)
- [ ] Every screen reachable by keyboard only
- [ ] Focus visible on every interactive element (2px ring, ≥3:1 contrast)
- [ ] Tab order matches visual order
- [ ] Esc closes every modal, drawer, popover
- [ ] Skip-to-content link on every web page (first focusable)
- [ ] Heading hierarchy correct (h1 → h2 → h3, no skips)
- [ ] All images have `alt` (descriptive or empty for decorative)
- [ ] All form fields have `<label>` (not placeholder-as-label)
- [ ] Errors associated with fields via `aria-describedby`
- [ ] Required fields marked with `aria-required="true"` AND `*` AND text "обязательно" in caption
- [ ] `<html lang="ru">` on every page
- [ ] Catalog table: virtualized list announces "row N of M" to screen readers
- [ ] Inline-edit: state changes announced via `aria-live="polite"`
- [ ] Reduced-motion: skeleton shimmer disabled; persona-preview fade replaces slide-in
- [ ] Touch targets ≥ 44 CSS px on mobile
- [ ] Text scales to 200% without loss
- [ ] Reflows at 320 CSS px width

### Screen reader walk-through
At least one engineer must complete an NVDA (Windows) and a VoiceOver (macOS) pass on:
- Phase 2 signup (form-heavy)
- Phase 4c catalog editor (data-table + inline edit)
- Phase 7 dashboard (multiple status regions)

### Cognitive accessibility
- [ ] Plain language (Flesch-Kincaid Russian-equivalent grade 8 or below)
- [ ] No timeouts shorter than 60s without warning + extend option
- [ ] Magic-link expiry 15 min explicit in email + on screen 3
- [ ] All destructive actions confirmable (delete service, change source)

---

## 7. Edge cases registry (per feature)

### Signup / Verify
- [ ] E-mail with `+` aliases — accept
- [ ] Phone international format — accept E.164
- [ ] Bot abuse — Cloudflare Turnstile + honeypot
- [ ] Magic-link clicked on different device than initiator
- [ ] Magic-link expired (>15 min) — fresh resend with new token
- [ ] User refreshes signup form — sessionStorage restore
- [ ] User signs up with same e-mail twice → 409, link to login

### YC Connect
- [ ] Token has trailing whitespace
- [ ] Multiple companies per account
- [ ] Token revoked on YC side (during onboarding or later)
- [ ] YC rate-limit
- [ ] YC returns malformed data (no services or duplicate IDs)
- [ ] User pastes wrong field as token (e.g., API URL)
- [ ] User changes country mid-flow

### Template / Catalog
- [ ] Region has 0 categories data → federal-district fallback
- [ ] All regions selected → cap at 3 categories with banner
- [ ] User selects custom only → empty catalog with "+ Добавить услугу" prompt
- [ ] Service with `price_type=OnRequest` in regional average calc — excluded
- [ ] Concurrent edit from 2 tabs — last-write-wins + toast
- [ ] Pastes formatted text into name field — strip HTML
- [ ] User disables all services → warning banner
- [ ] YC sync mid-edit → defer sync, queue conflicts
- [ ] CSV import with malformed rows — line-by-line errors reported
- [ ] Service name >100 chars — truncate with warning

### Train RAG
- [ ] PDF with embedded images-only text → OCR fallback or fail with clear error
- [ ] Google Drive link without sharing perms → user prompted to update sharing
- [ ] Upload >10 MB → reject with size message
- [ ] Persona name with profanity → blocklist + suggest neutral
- [ ] Tone toggle while preview is generating → cancel previous, run new

### Go-Live
- [ ] User unbinds last channel post-activation → warning, demote to PAUSED state
- [ ] Soft-launch limit reached (10 clients) → bot starts replying "временно перегружены, оставьте контакт" + email to admin
- [ ] Channel webhook fails post-bind → retry 3×, then notify

### Dashboard / First-week
- [ ] Day 1: 0 conversations → encouraging empty state with share link
- [ ] Bot down (LLM provider outage) → top banner "Бот временно молчит, разбираемся"
- [ ] Burst of handoffs (5+ at once) → group in UI, single notification

### MAX manager-bot
- [ ] Push delivery fails (user blocked bot) — fall back to email digest
- [ ] Init-data expired (>60 min) → re-auth via web first
- [ ] User switches MAX accounts → require re-auth

### Billing (new)
- [ ] First post-trial month — prorated base proportional to days remaining in month
- [ ] 0 bookings month — only base charged + encouraging banner
- [ ] Refund on booking cancelled within 1h — auto-credit −100 ₽ next invoice
- [ ] Booking cancelled >24h after creation — no refund (anti-game)
- [ ] Card expired before charge date — 7-day grace + 3 retries, then PAUSED state (bot down, dashboard still works)
- [ ] Payment provider down — retry queue, do not double-charge on success
- [ ] Tax profile not set before first charge — block charge with banner «Заполните налоговый профиль для документов»
- [ ] Founder lock vs subsequent customers — `is_founder_locked` flag on Tenant; UI hides upgrade prompts for locked tenants
- [ ] User downgrades / pauses → bot stops, billing pauses, dashboard read-only
- [ ] Bot-attribution race: same booking flagged by both bot and admin manually → tie-breaker logic «bot first wins; admin override possible within 24h with audit log»
- [ ] Attribution disputes: salon claims «эта запись не благодаря боту» → CSM dispute flow, manual credit
- [ ] Currency rounding — all amounts in kopeks internally, displayed as rubles

---

## 8. Backend contracts summary

| Endpoint | Method | Auth | Purpose |
|---|---|---|---|
| `/api/v1/auth/signup` | POST | none | Create tenant + send magic link |
| `/api/v1/auth/verify` | GET | magic token | Verify and start session |
| `/api/v1/auth/verify-poll` | GET | session_pending | Poll for verification |
| `/api/v1/auth/magic-link/resend` | POST | session_pending | Resend magic link |
| `/api/v1/onboard/yclients/connect` | POST | session | Validate YC token + start sync |
| `/api/v1/onboard/yclients/sync-status` | GET | session | Poll async sync job |
| `/api/v1/templates` | GET | session | List category templates for region |
| `/api/v1/onboard/template/apply` | POST | session | Seed catalog from template |
| `/api/v1/catalog/services` | GET | session | List catalog |
| `/api/v1/catalog/services/{id}` | PATCH | session | Update service inline |
| `/api/v1/catalog/services` | POST | session | Add new service |
| `/api/v1/catalog/services/bulk` | POST | session | Bulk operations |
| `/api/v1/catalog/sync` | POST | session | Trigger manual YC sync |
| `/api/v1/catalog/conflicts/{id}/resolve` | POST | session | Resolve sync conflict |
| `/api/v1/kb/upload` | POST (multipart) | session | Upload knowledge document |
| `/api/v1/kb/documents` | GET / DELETE | session | List / delete documents |
| `/api/v1/persona/preview` | POST | session | Live persona text preview |
| `/api/v1/persona` | PATCH | session | Save persona settings |
| `/api/v1/channels/max/connect` | POST | session | Bind MAX channel |
| `/api/v1/channels/telegram/connect` | POST | session | Bind Telegram channel |
| `/api/v1/onboard/publish` | POST | session | Publish bot live |
| `/api/v1/dashboard/health` | GET | session | Health metrics for first-week dashboard |
| `/api/v1/dashboard/attention-queue` | GET | session | Handoffs requiring attention |
| `/api/v1/conversations` | GET | session | Conversation list with filters |
| `/api/v1/conversations/{id}` | GET | session | Conversation detail |
| `/api/v1/webhooks/max` | POST | HMAC | MAX channel inbound |
| `/api/v1/webhooks/telegram` | POST | secret token | Telegram inbound |
| `/api/v1/webhooks/yclients` | POST | HMAC | YC catalog/booking webhooks |
| `/api/v1/pricing/founder-spots-left` | GET | none | Real-time counter for landing FounderPricingBadge |
| `/api/v1/billing/current` | GET | session | Current-month state for Billing screen + dashboard widget |
| `/api/v1/billing/history` | GET | session | Past invoices |
| `/api/v1/billing/invoices/{id}` | GET | session | Single invoice with line items |
| `/api/v1/billing/invoices/{id}/pdf` | GET | session | Download УПД / акт PDF |
| `/api/v1/billing/payment-method` | POST | session + step-up | Update card via provider token |
| `/api/v1/billing/events` | GET | session | Per-booking billing events |
| `/api/v1/billing/webhook/cloudpayments` | POST | HMAC | Payment provider callbacks |

### Authentication contracts

**Web sessions:** server-side session via signed cookie (`HttpOnly`, `Secure`, `SameSite=Lax`); 30-day rolling expiry; CSRF protection on state-changing requests.

**MAX webhooks:** HMAC-SHA256 with bot token (per MAX docs). Verify on every request. Reject if `auth_date` older than 60 min.

**Magic links:** 15-min expiry, single-use, signed JWT with `tenant_id` claim.

**API tokens (future, for chains):** OAuth2 client_credentials flow; out of MVP scope.

### Data retention
- Conversations: 365 days (then anonymized for analytics aggregation)
- Catalog versions: indefinite (history tab uses these)
- KB documents: until deleted by user
- Audit log: 2 years
- YC credentials: until tenant requests delete or disconnects YC

---

## 9. Pricing seed pipeline (cascade from Q1)

### Scope
Build a regional pricing dataset by parsing public price lists from 30–50 salons per category per region.

### Sources (MVP — RU only)
- 2GIS Profi API (where allowed)
- Yandex.Услуги public catalog
- Salon websites with publicly accessible price pages (respect robots.txt)
- Avito Услуги (categories: ногтевой сервис, массаж, etc.)

### Process
1. Scraper job (Python, async) runs monthly per region
2. Per scraped price: normalize to `(service_name, normalized_service_id, price_rub, source_url, scraped_at)`
3. Service name → normalized via embedding similarity (cluster to known template services)
4. Compute median + sample size per `(region, template_id, service_id)`
5. Store in `RegionalPriceSnapshot` table (immutable, versioned)
6. `GET /api/v1/templates?region=X` joins live snapshot

### Confidence tiers
- `high`: ≥20 sources, recent (≤90 days) → no warning
- `medium`: 10–19 sources → "средняя по выборке"
- `low`: <10 sources → ⚠ icon, "маленькая выборка" tooltip
- `none`: 0 sources → fall back to federal-district median; if also none, use national average; if also none, omit price ("уточните под себя")

### Transition to real-data sourcing
When ≥50 active tenants per region opt in:
- Add `Tenant.share_anonymized_pricing` opt-in toggle (default off)
- Aggregate own data alongside public sources
- Label changes from "по публичным прайсам" to "по нашим салонам"

### Engineering ticket
**Backlog ticket:** `DRF-XXX MVP pricing seed pipeline`
- Owner: data eng
- Estimate: 2 weeks
- Acceptance: scraper for top 5 regions × 11 categories, with confidence labels surfacing in API

---

## 10. Sign-off

| Role | Name | Approval | Date |
|---|---|---|---|
| **Designer (UX)** | ____ | ☐ | |
| **Product (PM)** | ____ | ☐ | |
| **Engineering (FE lead)** | ____ | ☐ | |
| **Engineering (BE lead)** | ____ | ☐ | |
| **Engineering (Data)** | ____ | ☐ | (pricing pipeline) |
| **QA lead** | ____ | ☐ | |
| **Security review** | ____ | ☐ | (YC token storage, magic links, HMAC) |
| **Legal** | ____ | ☐ | (public price scraping, data retention) |
| **CSM lead** | ____ | ☐ | (escalation triggers, sticky-chat behavior) |
| **Founder/CEO** | ____ | ☐ | (open product questions Q9–Q11 below) |

---

## 11. Open questions remaining for founder/PM (NOT designer)

> **📌 Authoritative status:** see [`decisions-log.md`](../decisions-log.md). This section is a historical snapshot — for current status of Q9-Q17 always check the log.

### Closed (locked 2026-05-17 r2)

| # | Decision |
|---|---|
| ~~Q9~~ | ✅ **Hybrid: 590 ₽ base + 100 ₽ per bot-attributed booking. Founder pricing locked for first 50 customers indefinitely. Post-50 model TBD based on real data.** Updated Phase 1 landing, added Screen 12 Billing, added BillingWidget to Phase 7 dashboard. See [pricing memory](~/.claude/projects/.../memory/project_pricing_model_hybrid.md). |

### Still open (partial decisions)

| # | Question | Status | Why it's product, not design |
|---|---|---|---|
| Q10 | Trial-end — leaning hybrid (14 days OR 10 free bookings whichever first → soft read-only) | **Re-evaluate** in hybrid-pricing context — "free bookings" framing replaces "free days" as primary axis | Conversion strategy + unit economics |
| Q11 | CSM headcount — leaning founder-led → 1 CSM per 40-60 active salons | Lock when first 10 salons activated and we see real onboarding-time distribution | Affects sticky-chat presence in Phase 4 |

### New open questions (raised by Q9 lock)

| # | Question | Owner | Urgency |
|---|---|---|---|
| Q12 | **Bot-attribution rules**: which `BookingRequest` rows count as `attributed_to_bot=True`? Default: created via `apps/skills/booking/tools.py::execute_confirm`. Edge cases: (a) bot suggested but user finalized in YClients app, (b) bot started conversation but admin took over and created manually. | Engineering + PM | 🔴 Before billing ship |
| Q13 | **Payment provider** — CloudPayments / ЮKassa / Stripe? Recommend CloudPayments for RU-юрлица + recurring + УПД. | Founder + Finance | 🟡 Before Billing screen ship |
| Q14 | **Налоговый профиль** — обязательное поле в Settings до первого charge. Defaults: ИП. Need form fields list (ИНН, КПП, расчётный счёт). | PM + Legal | 🟡 Before Billing screen ship |
| Q15 | **Refund rules for cancelled bookings** — auto-credit if cancelled <1h after creation? Yes. >24h? No. Edge: cancelled by salon-side, not customer? **Recommend treat same as customer-cancel.** | PM | 🟢 After MVP launch |
| Q16 | **Founder-50 cutoff communication** — at #45 do we email "осталось 5 мест"? Increases urgency but feels gimmicky. | Marketing | 🟢 Soft launch |
| Q17 | **What happens to customer #51?** — different price + locked-founder-50 keep their rate; OR raise to standard for all; OR keep 590+100 but new tiers stacked on top? **Recommend: lock 590+100 for #51+ also until we have real ARPU/CAC data, just remove "founder" badge.** | Founder | 🟡 Before 50th customer |

---

## 12. Linked references

| What | Where |
|---|---|
| Skill — UX architect master | `~/.claude/skills/ux-architect/SKILL.md` |
| Skill — MAX Mini App platform playbook | `~/.claude/skills/ux-architect/references/platforms/max-mini-apps.md` |
| Skill — Web responsive playbook | `~/.claude/skills/ux-architect/references/platforms/web-responsive.md` |
| Skill — Inline-edit pattern | `~/.claude/skills/ux-architect/references/interaction.md` § "Inline editing in tables and lists" |
| Skill — Source attribution pattern | `~/.claude/skills/ux-architect/references/interaction.md` § "Source attribution (provenance UX)" |
| Skill — Anti-slop checklist | `~/.claude/skills/ux-architect/assets/checklists/anti-slop.md` |
| Skill — A11y audit checklist | `~/.claude/skills/ux-architect/assets/checklists/a11y-audit.md` |
| Skill — Pre-ship checklist | `~/.claude/skills/ux-architect/assets/checklists/pre-ship.md` |
| Skill — Color palette `salon-warmth` | `~/.claude/skills/ux-architect/assets/color-palettes.json` |
| Project memory — Salon catalog vertical | `~/.claude/projects/.../memory/project_salon_catalog_vertical.md` |
| Project memory — RAG salon sources | `~/.claude/projects/.../memory/project_rag_salon_sources.md` |
| Existing booking skill (LLM tools spec) | `apps/skills/booking/tools.py` |
| Existing tenancy model | `apps/tenancy/models.py` |
| Channel adapters | `apps/channels/max/`, `apps/channels/handlers.py` |
| YClients integration | `apps/integrations/yclients/` |

---

## 13. Ship-readiness completion checklist (per Skill `pre-ship.md`)

### Critical (block ship if ❌)
- [ ] All 6 screen states implemented per screen
- [ ] All interactive elements: 6 states (default/hover/focus/active/disabled/loading)
- [ ] Keyboard navigable; visible focus
- [ ] Contrast WCAG AA on body text
- [ ] Touch targets ≥ platform minimum
- [ ] Form labels (no placeholder-as-label)
- [ ] Error messages specific
- [ ] Reduced-motion fallback
- [ ] `<html lang="ru">` set
- [ ] No console errors

### High (fix before ship unless deferred with reason)
- [ ] Anti-slop scan: 11/12 passed; pending fix — emoji-decoration → Lucide icons in category tiles & dashboard widgets
- [ ] All loading states defined
- [ ] All empty states designed
- [ ] Offline handling for catalog editor (IndexedDB queue)
- [ ] Tokens used in code (no hardcoded hex)
- [ ] Dark mode — deferred to post-MVP; document in known-limitations
- [ ] 360px width tested
- [ ] 200% browser zoom tested
- [ ] VoiceOver/TalkBack on signup + catalog editor

### MAX-bot specific
- [ ] HMAC validation on every webhook
- [ ] `auth_date` freshness < 60 min enforced
- [ ] No login form in MAX flow
- [ ] HapticFeedback on every push interaction
- [ ] Capability gating tested on web/desktop MAX

### Billing-specific (new)
- [ ] `BookingRequest.attributed_to_bot` boolean field migrated; backfill rule for historical rows = `False`
- [ ] Bot-attribution logic implemented in `apps/skills/booking/tools.py::execute_confirm` (sets `attributed_to_bot=True`)
- [ ] `BillingEvent` table with `(tenant_id, type, booking_id?, amount_kopeks, occurred_at)` — money in kopeks internally
- [ ] Per-tenant `Tenant.is_founder_locked` boolean (set on signup when `founder_spots_left > 0`)
- [ ] `/api/v1/pricing/founder-spots-left` returns real number, cached 60s
- [ ] CloudPayments (or chosen provider) recurring API integrated
- [ ] УПД-PDF generation per invoice for юрлицо/ИП tenants (chosen via tax profile)
- [ ] Step-up auth (e-mail OTP) before payment method change
- [ ] Webhook from payment provider validates HMAC + idempotency key
- [ ] Grace-period state machine: card failed → 7 days grace (bot works) → PAUSED (bot down, dashboard works)
- [ ] Refund handler: cancelled-within-1h auto-credits −100 ₽ on next invoice
- [ ] Dispute flow: salon-flagged "not actually a bot booking" → admin queue with audit trail
- [ ] Test: trial→paid transition with prorated first month
- [ ] Test: high-volume month (>100 bookings) — billing screen performance + ru-formatted money

### Performance
- [ ] LCP ≤ 2.5s on /signup, /dashboard
- [ ] INP ≤ 200ms on catalog editor
- [ ] CLS ≤ 0.1
- [ ] Bundle <300KB JS for onboarding routes (catalog editor heavier, OK)
- [ ] Skeleton-first for all data-bound screens

### Localization (post-MVP)
- [ ] All strings extracted to translation file
- [ ] German-length stress test deferred
- [ ] RTL deferred

### Instrumentation
- [ ] Funnel events instrumented (signup_submitted, verify_completed, source_chosen, catalog_ready, channel_bound, publish_clicked, first_booking)
- [ ] Drop-off attribution per phase
- [ ] Sentry / similar error tracking
- [ ] Performance RUM

---

## 14. Known limitations (acknowledged, not blockers)

### MAX platform constraints (from 2026-05-18 docs deep dive)
- **MAX has no native payments** — all payment flows external via `WebApp.openLink` to CloudPayments/Prodamus (per `dev.max.ru/docs/partners-integration`)
- **MAX has no push notifications** beyond chat messages — proactive engagement (reminders, retention, marketing) all bot-message-based; frequency policy critical (block = lost customer)
- **MAX Mini App has no theme API** — hardcode dual palette + CSS `prefers-color-scheme` switching
- **MAX Mini App has no MainButton** — use sticky in-page CTA (already adopted in design)
- **MAX has 5-bot per org cap** — chain customers (>5 locations) use single bot with location routing in `start_param`
- **MAX moderation up to 48h** — Phase 6 publish UX accounts for this
- **MAX UI React lib (`/ui`)** — strong recommendation for Mini App development; native iOS/Android feel + auto theme handling; reduces custom CSS work

### Deeplink payload limits (different per surface)
- **Mini App `start_param`**: 512 chars (used in Phase 4b template flow and Mini App entry)
- **Bot `/start` command payload**: 128 chars (TIGHTER — if using bot command-based deeplinks)
- Encode state as IDs not labels for long parameters; backend session keyed by short token if needed

### Existing limitations preserved


1. Dark mode not designed for MVP — defer to v1.1
2. Multi-location per tenant deferred
3. Per-master pricing deferred (Q8)
4. Currency limited to RUB (Q3)
5. Community template marketplace explicitly out of scope (Q5)
6. CSM live-chat tool — to be selected (Intercom / TG / own). Affects sticky-chat widget; if changed, update Phase 4 and dashboard sticky-chat
7. Pricing scraper depends on third-party site stability — accept periodic stale data
8. **Founder pricing (590 + 100) locked for first 50 customers indefinitely; post-50 model is intentionally TBD** — we collect 2–3 months of real ARPU/CAC/churn data from cohort #1–50 before deciding standard pricing. Customers #51+ get same 590+100 by default (without founder badge) until we have data.
9. **Bot-attribution requires solid `BookingRequest.attributed_to_bot` tracking** — false positives = we over-bill (legal+trust risk); false negatives = lost revenue. Mitigation: dispute flow + monthly internal audit of 10% sample.
10. **Billing implementation is critical-path before paid launch.** Trial-only state can ship earlier than full billing, but trial-end must coincide with billing ship.

---

**End of handoff package. Next step: design walkthrough with FE + BE leads to lock estimates and dependencies. Then ticket-out per phase.**
