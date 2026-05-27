# Ayla — Design Tokens

| Field | Value |
|---|---|
| **Date** | 2026-05-27 r1 |
| **Status** | P0 PRE_PILOT — foundational visual reference for all customer + provider surfaces |
| **Stream** | Sigma (Visual Design) |
| **Audience** | W1 / Iota frontend implementers · UX Architect · Brand Guardian · all subsequent visual work |
| **Foundation** | [`ayla-identity-and-brand.md`](../policies/ayla-identity-and-brand.md) (§2.1 wordmark · §3 personality · §4.4 sage-green canon · §7.1 surface naming) · [`customer-main-wellness-dashboard.md`](../../screens/customer-main-wellness-dashboard.md) (§7 brand notes + §8 WCAG blockers) · [`customer-records-flow.md`](../../screens/customer-records-flow.md) (§6 status vocabulary) · [`customer-booking-flow.md`](../../screens/customer-booking-flow.md) (§11 a11y patterns) · [`customer-food-scanner-flow.md`](../../screens/customer-food-scanner-flow.md) (§1 voice foundation) · [`master-solo-surface.md`](../../screens/master-solo-surface.md) (provider-side) |
| **Memory refs** | `project_ayla_brand_hybrid_usage` (2026-05-27 hybrid usage canon) · `project_ayla_personal_ai` (lowercase «ayla» + voice) · `project_pilot_scope_discipline` (locked scope) · `project_records_voice_principles` (status taxonomy) · `project_ayla_active_streams` (Sigma stream) |

> Canonical visual reference. Every customer-facing surface (Mini App customer Ayla, dashboard, booking flow, records, food scanner, push notifications, in-app headers) and provider-facing surface (Ayla Pro for solo Ольгу + team) consumes from this doc. W1 implementation references these tokens. Color, typography, spacing, radius, shadow, motion, icon strategy, component specs, wordmark, bot/channel asset distinction — single source.

---

## § 0 · Why this exists

Five Tau-shipped customer PRs + 1 provider PR (Tau design phase 100 % per memory `project_tau_design_phase_complete`) repeated the same anchor values inline: sage-green `#7ba478`, 12dp radius, 4.5:1 contrast, 44dp touch targets, ☀/🌿 emoji set. Each spec mentioned them but no canonical single source existed.

Sigma collects these anchors, fills the gaps (spacing scale, shadow scale, animation tokens, component specs), and resolves three carry-over Tau questions:
- Q-TAU-D2 (emoji vs SVG icons) — § 10 Lucide adoption
- Q-TAU-D4 (Услуги tab beauty-stereotype 💅) — § 10 Lucide swap
- Q-TAU-D5 (quick action 🎯 collision с pulse 🎯) — § 10 monogram strategy

Per memory `project_pilot_scope_discipline` — Sigma stays within locked scope: tokens doc + experiment SVG + typography wordmark + README docs. No new design flows; no scope expansion.

---

## § 1 · Scope

### IN
- Color palette (sage primary + warm neutral + semantic) with WCAG ratios
- Typography (Manrope primary + Onest fallback + scale + weights)
- Spacing scale (4dp baseline)
- Border radius scale
- Shadow / elevation tokens
- Animation tokens (duration + easing + reduced-motion)
- Icon strategy (Lucide adoption)
- Component visual specs (button / card / input / nav / list / modal / bottom sheet / badge / toast)
- Wordmark canonical (typography-based lowercase «ayla»)
- Bot/channel logo assets section (hybrid usage rule)
- AYLA folder structure + asset distribution rules

### OUT
- Voice copy / tone rules (in `ayla-identity-and-brand.md` + `conversational-ux-framework.md`)
- Screen layouts / IA decisions (in `docs/screens/*` Tau-shipped)
- Engineering implementation details (W1 / Iota scope)
- Final canonical Ayla logo asset (pending founder designer commission)
- Marketing copy / landing page / App Store (separate scope)
- TTS / STT voice / pronunciation (Phase 2+)
- Photo / illustration system (Phase 2+)
- Brand guidelines beyond UI tokens (logos in print, business cards, etc.)

---

## § 2 · Color — Sage primary scale

Primary canon per `ayla-identity-and-brand.md §4.4`. Anchor `#7ba478` (existing Tau dashboard §8) preserved as **sage-400 decorative**. WCAG-safe darker `#5a8557` preserved as **sage-500 text-safe**. Scale derived around these two anchors.

| Token | Hex | Purpose | WCAG vs white | WCAG vs sage-50 |
|---|---|---|---|---|
| `sage-50`  | `#f3f7f3` | App background tint, badge subtle fill | — (background) | — |
| `sage-100` | `#e3ede1` | Success badge background, hover state subtle | 1.13:1 | — |
| `sage-200` | `#c7d9c4` | Hairline border (emphasis), progress track filled, divider accent | 1.59:1 | 1.41:1 |
| `sage-300` | `#a3bf9e` | Disabled state primary, decorative subdued | 2.36:1 | 2.09:1 |
| `sage-400` | `#7ba478` | **Brand decorative primary** — pulse fills, sparkle accents, decorative only. **Fails 1.4.11 3:1 for UI state boundaries** — do NOT use for input borders, focus rings, or active-state indicators (use sage-500 instead) | **2.99:1** ⚠ decorative only | 2.65:1 |
| `sage-500` | `#5a8557` | **Text-safe primary + UI boundary safe** — body links, button fill, status badge text, brand wordmark, input focus border, nav active indicator | **4.65:1** ✅ AA body | 4.11:1 |
| `sage-600` | `#4a6e47` | Hover/pressed for sage-500, headings on white | **6.39:1** ✅ AAA body | 5.66:1 |
| `sage-700` | `#3a5638` | High-emphasis text, success badge text on sage-100 fill | **9.21:1** ✅ AAA | 8.16:1 |
| `sage-800` | `#2a3f29` | Dark surface text, max emphasis | **13.42:1** | 11.89:1 |
| `sage-900` | `#1c2a1b` | (rare — primary text alternative if higher contrast required) | **17.81:1** | — |

### Usage anchors (per Tau docs cross-ref)

| Anchor | Token | Source |
|---|---|---|
| Dashboard pulse fill (decorative) | `sage-400` | `customer-main-wellness-dashboard.md §8.2` |
| Body text / button label on sage fill | white on `sage-500` | dashboard §8.2 — fixes 2.9:1 fail |
| Wordmark «ayla» в Mini App header | `sage-500` | identity §4.4 + dashboard §7 |
| Success status badge (Подтверждена / Прошла / Возврат завершён) | `sage-700` on `sage-100` fill | records §6 |
| Brand accent decorative (sparkle ✨ placeholder) | `sage-400` | dashboard §7 |
| Empty progress-bar track | `warm-300` (not sage) | dashboard §8 BLOCKER 6 |
| Hover state for sage-500 button | `sage-600` | this doc |
| Focus ring base | `sage-600` (≥3:1 non-text) — see § 8 `elev-focus` | a11y 1.4.11 / 2.4.11 |
| Input border resting | `warm-500` (4.59:1 non-text) — see § 11 Input | a11y 1.4.11 |
| Input focus border | `sage-500` (4.65:1) | a11y 1.4.11 |
| Bottom-nav active indicator | `sage-500` underline (4.65:1) | a11y 1.4.11 |

### Anti-patterns

- ❌ Sage-400 для body text (2.99:1 fails AA 4.5:1)
- ❌ Sage-400 для UI state boundaries (input borders, focus rings, active-state underlines) — fails 1.4.11 3:1 by 0.01. Use sage-500.
- ❌ Sage-300 для anything text-bearing (disabled placeholder OK, не label)
- ❌ Pure sage gradient on white (AI-cliché — flat sage с hairline border preferred)
- ❌ Saturating sage further (current values calibrated против warm neutrals)
- ❌ Mixing sage с purple bot avatar palette в same surface (§ 13 mix prohibition)

---

## § 3 · Color — Warm neutral scale

Warm-tinted, NOT cool blue-grey (cool greys feel sterile / corporate — opposite of «подруга-эксперт»). Calibrated to harmonize с sage.

| Token | Hex | Purpose | WCAG vs white |
|---|---|---|---|
| `warm-50`  | `#fafaf8` | App paper background (the dominant background) | — |
| `warm-100` | `#f3f2ee` | Card background subtle, hover state ghost button | 1.07:1 |
| `warm-200` | `#e8e6df` | Hairline border (the canonical card border), divider | 1.27:1 |
| `warm-300` | `#d4d1c6` | Progress-bar track empty, input border resting | 1.71:1 |
| `warm-400` | `#a8a499` | Placeholder text, muted icons, decorative chrome | 2.84:1 ⚠ non-text ok |
| `warm-500` | `#7a766c` | Secondary text, caption text, supporting metadata | **4.59:1** ✅ AA body |
| `warm-600` | `#54514a` | Long-form body text, dense paragraphs | **7.61:1** ✅ AAA |
| `warm-700` | `#3a3833` | Primary body text default (the «ink» token alt for less stark feel than pure black) | **10.62:1** |
| `warm-800` | `#25241f` | **Primary ink** — headings, dense data, max emphasis | **14.34:1** |
| `warm-900` | `#161510` | Reserve — print, dark-mode flip if Phase 2+ | **17.69:1** |

### Semantic aliases

```
--paper:         warm-50  (background)
--ink:           warm-800 (primary text)
--ink-soft:      warm-600 (body text relaxed)
--ink-muted:     warm-500 (caption / secondary)
--ink-faint:     warm-400 (placeholder / decorative)
--hairline:      warm-200 (1dp decorative borders, dividers — paired with non-color affordance only)
--border-input:  warm-500 (4.59:1 — input borders, control boundaries needing 1.4.11)
--border-focus:  sage-500 (4.65:1 — focus state borders)
--track-empty:   warm-300 (progress track unfilled — fixes dashboard §8 BLOCKER 6)
```

**Important (a11y):** `--hairline` (warm-200 = 1.27:1 vs white) is permitted only as decorative reinforcement — never as the SOLE visual boundary of an interactive UI component. Use `--border-input` (warm-500) for input / control boundaries, or pair `--hairline` with shadow / fill differentiation.

### Anti-patterns

- ❌ Pure black `#000` (too stark for «подруга-эксперт» voice)
- ❌ Cool grey `#666` / `#999` (sterile — breaks warm voice)
- ❌ Pure white `#fff` for cards (use `warm-50` for paper, white only for card surfaces sitting on `warm-50`)

---

## § 4 · Color — Semantic palette

Status semantics with explicit cross-reference to Records § R4 vocabulary and Tau-shipped tints.

### Named tokens (promoted from inline)

| Token | Hex | WCAG vs white | Use |
|---|---|---|---|
| `amber-50`  | `#fbf6e9` | — (bg) | Warning subtle |
| `amber-100` | `#f5ecd9` | 1.05:1 | Warning fill |
| `amber-700` | `#7a5a1a` | 5.59:1 ✅ AA | Warning text |
| `rose-50`   | `#fdf0ed` | — (bg) | Error subtle |
| `rose-100`  | `#fbe6e3` | 1.05:1 | Error fill |
| `rose-600`  | `#9a3320` | 6.48:1 ✅ AAA | Destructive button bg (white text passes 3.24:1 large only; pair with confirm modal — never primary tap) |
| `rose-700`  | `#7a2516` | 9.43:1 ✅ AAA | Error text on rose-100 |

### Semantic mapping

| Semantic | Fill | Text | Used for |
|---|---|---|---|
| **Positive** | `sage-100` | `sage-700` | Подтверждена · Прошла · Возврат завершён · success toasts |
| **Neutral lifecycle** | `warm-100` | `warm-600` | Отменена · Перенесена · Не состоялась · Возврат в обработке |
| **Warning** | `amber-100` | `amber-700` | Отменена салоном · offline banner · stale data badge |
| **Error (form-only)** | `rose-100` | `rose-700` | Input validation errors only — never use red for status badges per records §10.6 anti-pattern |
| **Info** | `warm-100` | `warm-600` | Generic informational banner, partial-failure card |

### Critical: NO red for status

Per `customer-records-flow.md §10.6` and `§6.2`:
> «Use «Отменена», «Не состоялась» (neutral lifecycle ends) — NOT red badges. Red = error, cancelled = neutral end. NEVER color-only.»

Red is reserved for form input validation only — and even там paired с error icon + descriptive text. Cancellation / no-show / refund-pending — `warm-100/600` neutral.

### Provider-cancelled badge — explicit founder refinement

Per `project_records_voice_principles` (2026-05-26 founder review):
> «provider_cancelled = neutral/warning, NOT sage-green positive — провайдер отменил это не success».

Token: `#f5ecd9 → #7a5a1a` (warm-amber semantic), accompanied by `⚠` icon + text label «Отменена салоном».

### Anti-patterns

- ❌ Red for cancelled / no-show (anxiety, color-only error meaning)
- ❌ Sage-green positive on provider-cancelled (wrong emotional valence — founder F2)
- ❌ Color-only badge без icon + Russian label (WCAG 1.4.1)
- ❌ Punitive red for any informational state

---

## § 5 · Typography

### Locked: Manrope primary, Onest fallback

Selection rationale documented в `docs/design/assets/AYLA/EXPERIMENTS/README.md` после full Cyrillic stress test (10-check verdict — Manrope PASSES all).

**Why Manrope:** geometric modern sans designed by Mikhail Sharanda (native Russian designer); excellent Cyrillic shapes (й / ё / щ / ц / ъ / ь); production-mature; full weight range 200–800; variable font support; tabular numerals (`tnum`); proper «ёлочки» / em dash / ₽ geometry.

**Why Onest is the backup:** also native Russian designer (Anatoly Kashin), slightly warmer character, wider hooks — reserved if Manrope renders surprise in MAX webview / iOS Safari edge cases.

```css
--ff-display: 'Manrope', system-ui, sans-serif;
--ff-body:    'Manrope', system-ui, sans-serif;
--ff-mono:    ui-monospace, 'SF Mono', Menlo, monospace;

/* Backup swap (single line — keep these aliases stable): */
/* --ff-display: 'Onest', system-ui, sans-serif; */
/* --ff-body:    'Onest', system-ui, sans-serif; */
```

Load via Google Fonts с `&display=swap` and `&font-display: swap` to avoid FOIT (flash of invisible text — Cyrillic worst case is several hundred ms на slow MAX webview).

### Type scale (8 steps)

Calibrated на 360dp Mini App viewport, 1.45 default body line-height. All values in `dp`.

| Token | Size | Weight | Line-height | Letter-spacing | Use |
|---|---|---|---|---|---|
| `text-display` | 48 | 600 | 1.05 | −3.5% | Hero, marketing-adjacent (rare in Mini App; reserve for splash / onboarding) |
| `text-h1`      | 32 | 600 | 1.15 | −2.5% | Page-level greeting («Привет, Анна!»), screen header |
| `text-h2`      | 24 | 600 | 1.25 | −1.5% | Section header («Ближайшая запись»), modal title |
| `text-h3`      | 20 | 500 | 1.30 | −1.0% | Subsection («Что сделаем сейчас»), card title large |
| `text-h4`      | 17 | 500 | 1.35 | 0     | Card title default, primary action label |
| `text-body`    | 15 | 400 | 1.50 | 0     | Long-form, reminders, paragraph copy |
| `text-caption` | 13 | 400 | 1.40 | 0     | Metadata, sublabels, status secondary text |
| `text-micro`   | 11 | 500 | 1.35 | +4%   | Bottom nav labels, tag chips, badge text uppercase |

### Weight scale

- `weight-regular: 400` — body default
- `weight-medium: 500` — buttons, nav labels, h3 / h4 / micro labels
- `weight-semibold: 600` — h1 / h2, status badge text, emphasized inline
- `weight-bold: 700` — reserved для emphasis на body 15px (rare)

200/300/800 available но не in primary token set (use only с explicit a11y review).

### Font feature anchors

```css
font-feature-settings: "kern", "liga", "calt";       /* default everywhere */
font-feature-settings: "kern", "liga", "calt", "tnum"; /* numerals — pulse, prices, times, dates */
```

`tnum` (tabular numerals) **required** для:
- Booking price («1 800 ₽»)
- Time («16:00»)
- Wellness pulse («1 240 / 2 100 ккал · 59 %»)
- Phone numbers
- Tabular data anywhere

### Russian-specific rules

- `<html lang="ru">` required — affects browser default font fallback + screen reader pronunciation
- **Latin-script wrap rule (mandatory):** Any Latin-script token ≥ 3 chars rendered inline in RU text MUST be wrapped `<span lang="en">…</span>`. Known cases: «Ayla», «MAX», «YooKassa», «Beauty Place», «Casa Bella», «Telegram», all Lucide icon names в tooltips. Prevents RU TTS pronouncing «Айла» (dashboard §8 BLOCKER 5)
- «ёлочки» (« ») NOT straight quotes (" ") в body copy
- Em dash (—) for inline asides, NOT hyphen (-) per typography conventions
- Non-breaking space (` `) before «₽», «г», «мл», «ккал», «мин» («1 800 ₽», not «1 800 ₽» wrap)

### Composite aria-label pattern (resolves dashboard § 8 BLOCKER 4)

The wellness pulse renders dense numeric data — «1 240 / 2 100 ккал · 59 % · Б 65 · Ж 40 · У 120 г». Without composite aria-label, VoiceOver / TalkBack reads orphan tokens («one thousand two hundred forty slash two thousand one hundred kkal dot fifty-nine percent»), unparsable for blind users.

**W1 implementation pattern:**

```html
<div role="group" aria-label="Питание: 1240 из 2100 килокалорий, 59 процентов цели. Белки 65, жиры 40, углеводы 120 граммов.">
  <span class="pulse-icon" aria-hidden="true">🍽</span>
  <span class="pulse-label">Питание</span>
  <span class="pulse-value">1 240 / 2 100 ккал · 59 %</span>
  <span class="pulse-macros">Б 65 · Ж 40 · У 120 г</span>
  <div class="pulse-bar" aria-hidden="true">▓▓▓▓▓▓▓░░░░░░░</div>
</div>
```

**Rule:** Any numeric soup with units (water dots, calorie bars, progress percentages, time slots, prices) gets a composite `role="group" aria-label="..."` parent. Individual children get `aria-hidden="true"`. Aria-label uses spelled-out words («килокалорий» not «ккал»), commas separate clauses, period ends.

Same pattern applies to:
- Water dots: `aria-label="Вода: 4 из 8 стаканов"`
- Goal progress: `aria-label="Цель: меньше стресса, третья неделя, 78 процентов выполнено"`
- Time slot chips: `aria-label="Четверг 29 мая, 16:00, свободно"`
- Price: `aria-label="1800 рублей"` (decimal spelled, ₽ → «рублей»)

### Anti-patterns

- ❌ Inter / Roboto / Arial / system-ui as primary (AI-cliché, generic, no Russian personality)
- ❌ Mixing display + body fonts (one family across the scale)
- ❌ Bold 700 для всего headings (Manrope 600 carries enough weight)
- ❌ Center-aligned long paragraphs (left-align canonical, Cyrillic reads better left-aligned)
- ❌ All-caps for body (decorative only — `text-micro` tag chips OK)
- ❌ Letter-spacing > 0 на body text (reduces legibility)
- ❌ Line-height < 1.4 on body text (Cyrillic ascenders/descenders need room)

---

## § 6 · Spacing — 4dp baseline

8-step scale на 4dp baseline grid. Use these tokens exclusively in W1 component code.

| Token | dp | Use |
|---|---|---|
| `space-0`  | 0   | Zero / reset |
| `space-1`  | 4   | Hairline gap, icon-to-glyph offset |
| `space-2`  | 8   | Tight inline (chip internal padding y) |
| `space-3`  | 12  | Compact stack (form-field internal padding) |
| `space-4`  | 16  | **Canonical card padding · button padding y** |
| `space-5`  | 20  | Comfortable stack (paragraph gap) |
| `space-6`  | 24  | Section internal padding, modal padding |
| `space-7`  | 32  | Section vertical separator, hero padding |
| `space-8`  | 40  | Page-level vertical rhythm |
| `space-9`  | 48  | Section-to-section large break |
| `space-10` | 64  | Hero margins, decorative break |

### Component padding anchors

| Element | Internal padding | Inter-element gap |
|---|---|---|
| Button primary (≥48dp height) | `space-3` y / `space-5` x | — |
| Button secondary / ghost | `space-3` y / `space-4` x | — |
| Card (canonical) | `space-4` all sides | `space-3` between rows |
| Input | `space-3` y / `space-4` x | — |
| Modal | `space-6` body, `space-7` outer | `space-4` between sections |
| Bottom sheet | `space-4` x, `space-6` top, safe-area bottom | `space-4` between sections |
| List item | `space-3` y / `space-4` x | hairline divider |
| Status badge | `space-1` y / `space-3` x | — |
| Section header to content | `space-4` | — |
| Card stack (records list) | — | `space-3` between cards |

### Anti-patterns

- ❌ Custom dp values outside scale (`13dp`, `27dp` — break the grid)
- ❌ `space-1` (4dp) для page margins (too tight; min `space-4`)
- ❌ Inconsistent gap inside identical components (always use the same token for the same role)

---

## § 7 · Border radius

| Token | dp | Use |
|---|---|---|
| `radius-0`     | 0   | Sharp (rare — full-bleed sections, dividers) |
| `radius-sm`    | 4   | Tag chip, badge inline, small input |
| `radius-md`    | 8   | Input default, secondary button, small card |
| `radius-lg`    | 12  | **Canonical card · primary button · modal** |
| `radius-xl`    | 16  | Modal large, hero card |
| `radius-2xl`   | 24  | Bottom sheet top corners (radius applied to top-left + top-right only) |
| `radius-full`  | 999 | Pill button, avatar, status badge with text |

### Anti-patterns

- ❌ Hard-coded `border-radius: 10px` (uses `radius-lg = 12` instead)
- ❌ Pill `radius-full` для cards (kills tabular feel, looks marketing-y)
- ❌ Mixing `radius-sm` and `radius-lg` siblings within same card layout

---

## § 8 · Shadow / elevation

Flat sage philosophy is the default — most surfaces use **hairline border** (`1px var(--hairline)`) NOT shadow. Shadow reserved для true elevation (modals, popovers, bottom sheets, toasts).

Warm-tinted shadows, NOT cool grey/black (consistent с warm-tone palette).

| Token | Value | Use |
|---|---|---|
| `elev-0` | `none` | Default (flat with hairline border) |
| `elev-1` | `0 1px 2px rgba(60, 56, 50, 0.04), 0 1px 1px rgba(60, 56, 50, 0.06)` | Subtle lift — sticky banner, suggested-slot card on F3 |
| `elev-2` | `0 4px 12px rgba(60, 56, 50, 0.08), 0 2px 4px rgba(60, 56, 50, 0.04)` | Floating action button, dropdown menu, popover |
| `elev-3` | `0 12px 32px rgba(60, 56, 50, 0.12), 0 4px 8px rgba(60, 56, 50, 0.06)` | Modal, bottom sheet (top edge), toast |
| `elev-focus` | `0 0 0 2px white, 0 0 0 4px var(--sage-600)` (solid 2dp sage-600 ring with 2dp white inset) | Focus ring on any interactive — **mandatory always**, never `outline: none` without replacement (a11y 1.4.11 / 2.4.11; sage-600 = 6.39:1 vs white) |

### Usage rules

- Default cards = `elev-0` + `1px var(--hairline)` border
- Modals always = `elev-3` + radius-xl
- Bottom sheets = `elev-3` + radius-2xl top corners only
- Focus = `elev-focus` always (a11y 1.4.11 + 2.4.11) — mandatory on EVERY interactive, never `outline: none` without replacement
- Never mix multiple shadow tokens on same element

### Anti-patterns

- ❌ Cool grey shadow (`rgba(0,0,0,0.15)` looks sterile)
- ❌ Multiple stacked shadows for "depth" (one token per role)
- ❌ Shadow on every card (loses meaning — flat with hairline is canonical)
- ❌ Inset shadows on inputs (modern flat preferred)

---

## § 9 · Animation tokens

Calm wellness motion — never urgent, never jumpy. Per `customer-food-scanner-flow.md §3` reduced-motion fallback rules + dashboard §5 skeleton shimmer constraints.

### Duration

| Token | ms | Use |
|---|---|---|
| `motion-instant` | 0   | No animation — accessibility max + `prefers-reduced-motion` fallback |
| `motion-fast`    | 150 | Micro-interactions: button press, checkbox toggle, hover tint |
| `motion-base`    | 250 | Default — most state transitions, card hover, tab switch |
| `motion-slow`    | 400 | Modal open, bottom sheet open, page transition |
| `motion-very-slow` | 600 | Splash / onboarding only — rarely used |

### Easing

| Token | Function | Use |
|---|---|---|
| `ease-out`         | `cubic-bezier(0.16, 1, 0.3, 1)`     | Default for enters / fade-in / appearing UI |
| `ease-in-out`      | `cubic-bezier(0.65, 0, 0.35, 1)`    | Symmetric state changes (toggle, tab indicator) |
| `ease-out-sharp`   | `cubic-bezier(0.4, 0, 0.2, 1)`      | Micro-interactions (button press) |
| `ease-in`          | `cubic-bezier(0.7, 0, 0.84, 0)`     | Exits / fade-out (use sparingly) |

### Reduced-motion fallback (mandatory)

```css
@media (prefers-reduced-motion: reduce) {
  *,
  *::before,
  *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
    scroll-behavior: auto !important;
  }
  /* Skeleton shimmer → static placeholder per dashboard §5 BLOCKER 9 */
  .skeleton-shimmer { animation: none; background: var(--warm-100); }
  /* Food scanner F2 pulsing dots → static dots per food-scanner §3 */
  .pulse-dots { animation: none; }
}
```

### Specific micro-interaction anchors

| Element | Duration | Easing | Property |
|---|---|---|---|
| Button hover tint | `motion-fast` | `ease-out-sharp` | `background-color` |
| Button press | `motion-fast` | `ease-out-sharp` | `transform: scale(0.98)` |
| Tab switch underline | `motion-base` | `ease-in-out` | `transform: translateX` |
| Card hover lift | `motion-base` | `ease-out` | `box-shadow` |
| Modal open | `motion-slow` | `ease-out` | `opacity` + `transform: scale(0.96 → 1)` |
| Bottom sheet open | `motion-slow` | `ease-out` | `transform: translateY` |
| Toast enter | `motion-base` | `ease-out` | `opacity` + `transform: translateY(8 → 0)` |
| Skeleton shimmer | 1500ms | linear infinite | `background-position` |
| Pulse dot (food scanner F2) | 1200ms | ease-in-out infinite | `opacity` |

### Anti-patterns

- ❌ Bouncy easing (`cubic-bezier(0.68, -0.55, 0.27, 1.55)`) — childish, off-brand
- ❌ Duration > 600ms for state changes (feels sluggish)
- ❌ Animating box-shadow without `will-change` hint (jank)
- ❌ Forgetting `prefers-reduced-motion` (a11y blocker)
- ❌ Auto-playing decorative motion (background loops, parallax)

---

## § 10 · Icon strategy

**Lucide library adopted** as canonical icon set, replacing emoji in app chrome.

### Why Lucide

- 1300+ icons covering all UI needs
- Tree-shakable, ~1kb per icon
- Stroke-based (consistent visual weight), matches calm minimal aesthetic
- Customizable stroke-width (we anchor `1.5`)
- Free, MIT license, active maintenance
- Per `frontend-design` skill — preferred icon library избегания cookie-cutter Material/Heroicons looks

### Anchors

| Spec | Value |
|---|---|
| Default size | `24dp` (matches `space-6`) |
| Compact size | `20dp` (used in dense rows, badges, inline) |
| Stroke width | `1.5` (default Lucide) |
| Color (default) | `currentColor` (inherits text color) |
| Active state color | `sage-500` |
| Disabled color | `warm-400` |

### Resolves Tau open questions

**Q-TAU-D2 (emoji vs SVG icons in pulse / quick actions) — VERDICT: Lucide SVG.**

| Tau emoji | Lucide replacement | Token |
|---|---|---|
| 🍽 (Питание) | `utensils` | `icon-nutrition` |
| 💧 (Вода) | `droplet` | `icon-water` |
| 🎯 (Цель) | `target` | `icon-goal` |
| 📸 (Сфотографируй) | `camera` | `icon-camera` |
| 📅 (Найди услугу / Записи tab) | `calendar` | `icon-bookings` |
| 🌿 (greeting emoji) | (decorative — keep emoji OK in greeting copy per identity §3.6 max 1) | — |
| ✨ (wordmark accent — placeholder for ☽) | (decorative — kept as emoji until Phase 2+ crescent moon ships) | — |

**Q-TAU-D4 (Услуги tab 💅 beauty-stereotype) — VERDICT: Lucide `sparkles` icon, sage-500 stroke.**

Per UI Designer Tau review: 💅 stereotypically beauty-only, не wellness-OS. Lucide `sparkles` neutralizes the stereotype while preserving "discovery / try something" semantic.

**Q-TAU-D5 (quick action 🎯 collision с pulse 🎯) — VERDICT: pulse keeps `target`, quick action becomes `compass` (`icon-direction`) — disambiguation "find your direction" rather than "more of the same goal".**

### Bottom nav resolved (5 tabs)

| Tab | Lucide icon | Token |
|---|---|---|
| Главная | `home` | `icon-home` |
| День | `sun` | `icon-day` |
| Записи | `calendar` | `icon-bookings` |
| Услуги | `sparkles` | `icon-services` |
| Я | `user` | `icon-me` |

Active state: stroke `sage-500`, label `sage-500`, indicator underline 3dp `sage-400`.
Resting: stroke `warm-500`, label `warm-500`.

### Status badge icons (Records §6 vocabulary)

| Status | Lucide icon | Pairs with |
|---|---|---|
| Подтверждена | `check` | `sage-100 / sage-700` |
| Перенесена | `refresh-cw` | `warm-100 / warm-600` |
| Отменена | `x` | `warm-100 / warm-600` |
| Отменена салоном | `alert-triangle` | `warm-amber-100 / warm-amber-700` |
| Прошла | `check-circle` | `sage-100 / sage-700` |
| Не состоялась | `minus-circle` | `warm-100 / warm-600` |
| Возврат в обработке | `clock` | `warm-100 / warm-600` |
| Возврат завершён | `check` | `sage-100 / sage-700` |

### Anti-patterns

- ❌ Mixing emoji + Lucide in same component (consistent choice per surface)
- ❌ Filled icons in primary nav (Lucide is stroke-based; filled breaks visual rhythm)
- ❌ Custom icon stroke-width (always 1.5)
- ❌ Color-coded icons standalone without label (a11y)
- ❌ Material Icons / Heroicons fallback (cookie-cutter, generic)

---

## § 11 · Component visual specs

Pixel-level reference. W1 implementations must consume tokens above, not hard-coded values.

### Button

**Primary** — for the dominant action (Записаться, ✓ Записаться, Открыть запись).

```
Min height: 48dp
Padding y: space-3 (12dp)
Padding x: space-5 (20dp)
Radius: radius-lg (12dp)
Background: sage-500
Hover: sage-600
Pressed: sage-700, scale(0.98)
Text: white, text-h4 weight-medium
Disabled: warm-300 bg, warm-600 text (4.0:1 — conservative for low-vision; WCAG 1.4.3 disabled-state exemption applies but we still want disabled labels readable)
Focus: elev-focus ring (mandatory)
Min tap target: 44×44dp (per WCAG 2.5.8 — confirmed dashboard §8 BLOCKER 1)
```

**Secondary** — supporting action (Сообщить по записи, Открыть, Оставить отзыв).

```
Same dimensions as primary
Background: transparent
Border: 1px sage-500 (4.65:1 — non-text 1.4.11 safe)
Text: sage-500
Hover: sage-50 bg
Pressed: sage-100 bg
Focus: elev-focus ring (mandatory)
```

**Ghost** — tertiary action (Перенести, Отменить, Назад).

```
Same dimensions
Background: transparent
Border: 1px warm-200
Text: warm-600
Hover: warm-100 bg
Pressed: warm-200 bg
```

**Destructive** — used sparingly (final cancel confirmation, delete account).

```
Same dimensions as primary
Background: rose-600 (#9a3320)
Hover: rose-700 (#7a2516)
Always paired with confirm modal — never direct destructive on single tap
```

### Card (canonical)

```
Background: white
Border: 1px warm-200 (hairline canonical — decorative reinforcement; cards are non-interactive surfaces so 1.4.11 doesn't apply, but interactive cards need a paired affordance — fill / shadow / hover)
Radius: radius-lg (12dp)
Padding: space-4 (16dp) all sides
Shadow: elev-0 (none — flat sage philosophy)
Inter-row gap inside card: space-3 (12dp)
Inter-card gap in lists: space-3 (12dp)
```

Per dashboard §7 — canonical card = 12dp radius + 1dp `warm-200` hairline + 16dp padding. `sage-200` is reserved для **emphasis cards** (booking detail success state, confirmed records) — not the default.

**Emphasis card** (booking detail success, confirmed records):

```
Border: 1px sage-200
Background: sage-50 OR white (designer choice per surface)
Otherwise same as canonical
```

### Input (text)

```
Min height: 48dp
Padding y: space-3
Padding x: space-4
Background: white
Border: 1px warm-500 (4.59:1 — non-text 1.4.11 safe; uses --border-input semantic)
Radius: radius-md (8dp)
Text: text-body (15dp) warm-800
Placeholder: warm-400
Focus border: 2px sage-500 (4.65:1) + elev-focus ring outside
Error border: 2px rose-600 + rose-100 background tint
Disabled: warm-100 bg, warm-500 text (4.59:1 — WCAG 1.4.3 disabled exemption applies but we keep readable)
```

**Error a11y (WCAG 3.3.1 + 4.1.3):**

```html
<div class="input-group">
  <label for="phone">Телефон</label>
  <input id="phone" type="tel" aria-invalid="true" aria-describedby="phone-err">
  <div id="phone-err" role="alert">
    <span aria-hidden="true">⚠</span>
    Введи номер в формате +7 912 345-67-89
  </div>
</div>
```

Rules:
- `aria-invalid="true"` on the input when in error state
- Error text container `role="alert"` (assertive announcement on appearance)
- Error text linked via `aria-describedby="…-err"`
- Error icon `aria-hidden="true"` (text already conveys meaning)
- Error text uses rose-700 on rose-100 (9.43:1 AAA)

### Bottom nav

```
Container height: 56dp (per dashboard §3 ASCII)
Background: white
Top border: 1px warm-200
Safe-area bottom: env(safe-area-inset-bottom)
Tabs: 5 equal flex columns
Icon: 24dp Lucide above label
Label: text-micro (11dp) weight-medium
Active label color: sage-500
Active icon stroke: sage-500
Active indicator: 3dp sage-500 underline at top of tab (4.65:1 — non-text 1.4.11 safe). Paired with weight + color shift for redundant non-color affordance.
Resting: warm-500 icon + label
Hit target: 44dp height min (interior padding pushes effective tap area, even if visual 56dp container)
aria-current="page" on active tab (per dashboard §8 IMPORTANT 13)
```

### List item

```
Min height: 56dp
Padding y: space-3
Padding x: space-4
Background: white
Bottom border: 1px warm-200 (last child: no border)
Tap state: warm-50 background tint
Title: text-h4 weight-medium warm-800
Subtitle: text-caption warm-500
Right chevron: Lucide chevron-right 20dp warm-400
```

**Multi-target rule (WCAG 2.5.8 spacing exception):** If a list row exposes TWO independent tap targets (e.g., card body + right-side action button), each independently must be ≥44×44dp with ≥8dp visual separation. Otherwise collapse to single full-row tap with secondary action available via overflow menu / detail screen.

### Modal

```
Background: white
Radius: radius-xl (16dp)
Padding: space-7 (32dp)
Shadow: elev-3
Max width: 360dp (mobile-first)
Header: text-h2 weight-semibold warm-800
Body: text-body warm-700
Backdrop: rgba(60, 56, 50, 0.45)
Backdrop click closes (if non-destructive) — confirm-required modals require explicit cancel button
Open: motion-slow + ease-out + opacity + scale(0.96 → 1)
```

**A11y mandatory (WCAG 2.4.3 + 2.1.2 + 4.1.2):**

```html
<div role="dialog" aria-modal="true" aria-labelledby="m-title" aria-describedby="m-body">
  <h2 id="m-title">Заголовок</h2>
  <div id="m-body">…</div>
  <button class="close" aria-label="Закрыть">×</button>
</div>
```

- `role="dialog"` + `aria-modal="true"` + `aria-labelledby` (header) + `aria-describedby` (body)
- **Focus trap on open** — Tab / Shift+Tab cycles only within modal
- **Escape key closes** (unless destructive confirm flow)
- **Focus returns to invoking element** on close (record the trigger before opening)
- First focusable element receives focus on open (typically close button or first form input)
- Close button has accessible name «Закрыть» / `aria-label="Закрыть"`

### Bottom sheet

```
Background: white
Top corners radius: radius-2xl (24dp top-left + top-right only)
Bottom corners: 0 (flush with viewport bottom)
Padding: space-4 horizontal, space-6 top, safe-area-inset-bottom
Drag handle: 36dp × 4dp warm-300 pill, centered top, space-2 from top
Shadow: elev-3 (top-edge primary)
Backdrop: rgba(60, 56, 50, 0.45)
Open: motion-slow + ease-out + translateY (100 % → 0)
Swipe-down dismiss (touch handler) + visible close button "×" or "Закрыть" (keyboard-accessible alternative)
```

**A11y mandatory (WCAG 2.1.1 + 2.4.3):**
- Swipe-dismiss is not keyboard-operable — must provide visible close button (e.g., «×» button top-right or «Закрыть» button at bottom)
- **Escape key closes** the sheet
- **Focus trap** within sheet
- **Focus returns to invoking element** on close
- `role="dialog" aria-modal="true" aria-labelledby="…"` like Modal

### Status badge

```
Padding: space-1 vertical, space-3 horizontal
Radius: radius-full (pill)
Text: text-caption (13dp) weight-medium
Icon: Lucide 16dp inline before text (space-1 gap)
Background + text color: per § 4 semantic palette
Min height: 24dp (small) — never below
```

### Toast

```
Container max-width: 320dp
Padding: space-4
Background: white (or sage-100 for positive, amber-100 for warning, rose-100 for error)
Border: 1px warm-200 (or sage-200 for positive, amber-700/30% for warning, rose-600/30% for error)
Radius: radius-lg
Shadow: elev-3
Text: text-body weight-regular warm-700 (sage-700 / amber-700 / rose-700 for semantic variants)
Icon: Lucide 20dp before text per semantic (check / alert-triangle / x-circle / etc.)
Enter: motion-base + ease-out + opacity + translateY(8 → 0)
Position: bottom 24dp center mobile, top-right desktop
```

**A11y mandatory (WCAG 4.1.3 + 2.2.1):**

| Toast severity | ARIA | Live region |
|---|---|---|
| Positive / Info | `role="status"` | `aria-live="polite"` |
| Warning | `role="status"` | `aria-live="polite"` |
| Error / destructive | `role="alert"` | `aria-live="assertive"` |

- **Auto-dismiss control:** default 5000ms for positive/info; ≥10000ms for warning/error; **pause on hover / focus** (do not advance timer); **always provide explicit «×» close button** (keyboard `Esc` closes focused toast)
- Critical errors should be persistent (no auto-dismiss) until user dismisses
- Single toast at a time (queue subsequent; do not stack visually)

### Skeleton (loading)

Per dashboard §5 State 1 + reduced-motion fallback:

```
Background: warm-100
Animation: skeleton-shimmer 1500ms linear infinite
  → linear-gradient(90deg, warm-100 0 %, warm-200 50 %, warm-100 100 %) translated across
Reduced-motion: static warm-100, no animation
```

---

## § 12 · Wordmark — canonical typography-based

Per `ayla-identity-and-brand.md §2.1` (proper noun, indeclinable) + §4.4 (lowercase wordmark direction) + memory `project_ayla_brand_hybrid_usage` (canonical app wordmark = typography-based until designer commission).

### Canonical: typography wordmark

```
Text: "ayla" (lowercase, indeclinable per identity §2.3)
Font: Manrope (var(--ff-display))
Weight: 500 (medium — confident but not aggressive)
Color: sage-500 (var(--sage-500))
Letter-spacing: -0.04em (tight, geometric confidence)
Line-height: 1
Sizes (per surface):
  Mini App header:     20dp
  Onboarding splash:   32dp
  Push sender label:   inherits body
  Settings header:     20dp
  «Что Ayla знает»:    inherits h2 (24dp)
```

```html
<span class="ayla-wordmark" lang="en">ayla</span>
```

```css
.ayla-wordmark {
  font-family: var(--ff-display);
  font-weight: 500;
  color: var(--sage-500);
  letter-spacing: -0.04em;
  line-height: 1;
}
```

Note `lang="en"` per dashboard §8 BLOCKER 5 — prevents Russian TTS from pronouncing «Айла».

### Clearspace

Minimum clearspace = height of the lowercase "a" stem on all four sides. For 20dp wordmark, that's ~14dp.

### Minimum size

| Context | Min wordmark height |
|---|---|
| Mini App / browser UI | 16dp (below this — illegible Cyrillic-adjacent kerning) |
| Push notification label | 12dp (system constraint) |
| Email signature | 14dp |
| Print | 8mm |

### Anti-patterns

- ❌ ALL CAPS «AYLA» in Mini App / customer UI (reserved для bot avatar only per § 13)
- ❌ Italic / oblique «ayla»
- ❌ Per-tenant wordmark variation («Помощница Карина» — banned per identity §4.3)
- ❌ Drop shadow / glow / decoration на wordmark
- ❌ Russian transliteration «Айла» (indeclinable per identity §2.3)
- ❌ Wordmark in any color outside sage-500 (or sage-700 for AAA contrast surfaces if needed; never warm-grey, never white-on-sage)

### Pending: final canonical logo

Founder will commission a designer for the final canonical logo — lowercase, minimal, optionally с crescent moon ☽ over the «a» per identity §2.4 etymology + Phase 2+ reservation. **Until that ships, the typography wordmark above is the canonical primary** for all Mini App / customer / Ayla Pro provider surfaces.

---

## § 13 · Bot/channel logo assets

Per founder verdict 2026-05-27 (memory `project_ayla_brand_hybrid_usage`), Ayla uses **hybrid asset distribution**: typography canonical для app surfaces (above) + purple AYLA asset pack для bot/channel avatar contexts.

### Purple AYLA pack — location

```
docs/design/assets/
├── logo/
│   ├── logo.svg                              ← master vector (purple #7d63ef)
│   └── logo.png                              ← master raster
└── AYLA/
    ├── README.md                             ← asset pack canonical README
    ├── logo.png + logo-1.png ... logo-41.png ← 42 raster variants
    └── EXPERIMENTS/
        ├── README.md                         ← experiment outcomes
        ├── typography-manrope-preview.html
        ├── logo-sage-experiment.svg          ← color swap (NOT canonical primary)
        └── logo-sage-render.html
```

### ✅ Allowed surfaces (purple AYLA)

- **MAX bot avatar** — primary use (~256×256+ chat tile rendering)
- **Telegram bot avatar** — same role
- **Channel chat-list logo** — small-size identity
- **Temporary bot-channel identity** — until founder commissions final canonical logo

Reasons purple wins at chat-list scale:
- Strong contrast at small sizes (chat tile 64–256dp range)
- Visible against both light/dark chat themes
- Distinguishable from system sage-green palette (avoids "where does platform end, bot begin" ambiguity)

### ❌ Forbidden surfaces (purple AYLA)

- Mini App UI / customer dashboard / design tokens primary
- Customer-facing in-app headers / nav / settings
- Primary app branding (favicon, splash, App Store icon)
- Same surface as sage-green app chrome (mix prohibition below)

### Mix prohibition (founder explicit)

> «Do not mix purple bot branding and sage-green app chrome в same app surface unless separately approved.»

**Purple = channel identity. Sage-green = app UI identity.** Each lives in its own context. The only case где both appear adjacently is MAX rendering chat list (bot avatar adjacent to user's other chats) — that's not Ayla mixing colors, that's MAX UI.

### Sage color experiment

`AYLA/EXPERIMENTS/logo-sage-experiment.svg` — purple → sage color swap working artefact. **Not canonical primary** — still ALL CAPS «AYLA» + sparkles + marketing styling. Pending founder designer commission of final lowercase / minimal / no-sparkles canonical logo.

### Purple AYLA contrast guidance

| Element | Contrast vs purple `#7d63ef` |
|---|---|
| White «AYLA» wordmark text | 4.51:1 ✅ AA (barely — keep weight ≥600 to stabilize) |
| White sparkle marks | non-text decorative — ≥3:1 ✅ |
| Any sage element overlay on purple | **forbidden** per mix prohibition |
| Any sub-text overlay | minimum 4.5:1 vs purple — use white only |

No text smaller than 24dp should appear directly on the purple tile (chat-list rendering at small sizes loses sub-pixel detail). Wordmark and decorative marks only.

### Anti-patterns

- ❌ Purple AYLA in Mini App headers / customer surfaces
- ❌ ALL CAPS «AYLA» in customer-facing copy (identity §2 — indeclinable lowercase)
- ❌ Purple + sage on same rendered surface
- ❌ Treating purple AYLA as canonical primary (it's bot avatar only)
- ❌ Re-coloring purple AYLA without re-evaluation (sage swap = experiment, not asset)

---

## § 14 · Open questions

| # | Severity | Question | Owner |
|---|---|---|---|
| Q-SIG-1 | 🟢 | Font face self-hosting vs Google Fonts CDN for production (CDN simpler, self-host avoids 3P request) | W1 / Iota frontend |
| Q-SIG-2 | 🟡 | Dark-mode flip (Phase 2+) — sage palette в dark needs separate token set (rough thinking: shift `--paper` to `warm-800`, swap text/bg) | Phase 2+ Sigma follow-up |
| Q-SIG-3 | 🟢 | Brand sparkle ✨ → crescent moon ☽ migration timeline (per identity §2.4 Phase 2+) | Brand owner |
| Q-SIG-4 | 🟢 | Decorative emoji в greeting copy («🌿» per dashboard) — keep as emoji or migrate to Lucide leaf icon? | Recommend keep emoji (decorative, copy-embedded, per identity §3.6 max 1) |
| Q-SIG-5 | 🟢 | Provider-side Ayla Pro chrome — confirms **same sage palette** as customer surfaces (per identity § 4 single Ayla brand + memory `project_ayla_brand_hybrid_usage`). Any divergence requires explicit founder approval | RESOLVED — same palette default |
| Q-SIG-6 | 🟢 | Photo system tokens (food scanner photo preview frames, master photos) — borders, radius, fallback placeholder | Defer — handled inline in food-scanner / booking-flow specs |
| Q-SIG-7 | 🟢 | Russian non-breaking space enforcement в copy — manual vs CSS `white-space: nowrap` wrapper component | W1 implementation pattern |
| Q-SIG-8 | 🟡 | Sage gradient explicit ban OR allowed для one specific surface (splash background)? | Lean: allow ONLY splash, never primary content |

---

## § 15 · Anti-patterns summary (consolidated)

Anchored from individual section anti-patterns. Brand Guardian + Accessibility Auditor verify against this list pre-merge.

### Color
- ❌ Sage-400 для body text (fails AA 4.5:1)
- ❌ Pure black / cool grey (sterile, breaks warm voice)
- ❌ Red badge для cancelled / no-show (red = error only)
- ❌ Color-only state without icon + label
- ❌ Purple anywhere in Mini App primary surfaces
- ❌ Sage gradient on white as primary (AI-cliché)

### Typography
- ❌ Inter / Roboto / Arial / system-ui (generic)
- ❌ Letter-spacing > 0 на body
- ❌ Line-height < 1.4 on body (Cyrillic ascenders need room)
- ❌ ALL CAPS body / multi-word headings
- ❌ Bold 700 для всего headings (600 sufficient)
- ❌ Russian transliteration «Айла» of wordmark

### Spacing / radius
- ❌ Custom dp outside 4dp baseline scale
- ❌ Pill radius cards (looks marketing-y)
- ❌ Mixing radius scales siblings within layout

### Shadow / motion
- ❌ Multiple stacked shadows on same element
- ❌ Cool grey shadow color
- ❌ Bouncy easing (off-brand for calm wellness)
- ❌ Forgetting reduced-motion fallback
- ❌ Auto-play decorative motion

### Icons
- ❌ Mixed emoji + Lucide в same component
- ❌ Filled icons (Lucide stroke canonical)
- ❌ Custom stroke-width
- ❌ Color-only icons без accompanying text

### Brand
- ❌ Purple AYLA в Mini App
- ❌ ALL CAPS «AYLA» in customer copy
- ❌ Purple + sage same surface
- ❌ Per-tenant wordmark customization
- ❌ Marketing tone ("wellness experience awaits!", exclamation chains)
- ❌ Generic wellness stock photo language
- ❌ Cookie-cutter component layouts

---

## § 16 · Cross-document references

### Tau-shipped specs that consume these tokens
- `docs/screens/customer-main-wellness-dashboard.md` (canonical Compact Hero v2 layout)
- `docs/screens/customer-booking-flow.md` (3-layer ranking F1-F5)
- `docs/screens/customer-records-flow.md` (R1-R6 + 8-status vocabulary)
- `docs/screens/customer-food-scanner-flow.md` (F1-F4 + voice rules)
- `docs/screens/customer-cancellation-reschedule-flow.md`
- `docs/screens/customer-onboarding-flow.md`
- `docs/screens/customer-profile-tab.md`
- `docs/screens/master-solo-surface.md` (provider-side, Variant B 5+Ещё nav)

### Foundational policy docs (read these alongside)
- `docs/design/policies/ayla-identity-and-brand.md` (§ 2 wordmark, § 3 voice, § 4 brand co-presence, § 7 surface naming)
- `docs/design/policies/conversational-ux-framework.md` (tone modulation)
- `docs/design/policies/solo-provider-ux.md` (provider-side universal UI rules)
- `docs/design/policies/ayla-mediated-messaging.md` (booking/customer thread tone)

### ADRs
- `docs/adr/ADR-0008-role-detection-and-staff-model.md` (multi-role identity — affects Ayla Pro chrome)
- `docs/adr/ADR-0009-ayla-split-domain-architecture.md` (booking domain lives in Ayla djangoproject — irrelevant к visual tokens but contextual)

---

## § 17 · Implementation notes for W1 / Iota

1. **Consume tokens as CSS custom properties** (not Tailwind theme-extend, not utility classes — keep canonical names visible in DevTools).
2. **`:root` declarations** для все color / spacing / radius / shadow / motion / typography tokens. Use semantic aliases (`--paper`, `--ink`) for the most common refs.
3. **Manrope load** through Google Fonts CDN with `&display=swap`. Self-host as Q-SIG-1 follow-up if 3P request unacceptable.
4. **`<html lang="ru">`** on every Mini App page. Wrap «Ayla» / «Beauty Place» / Latin loanwords в `<span lang="en">…</span>` per dashboard §8 BLOCKER 5.
5. **`prefers-reduced-motion: reduce`** media query applies the override block in § 9 to ALL animations globally — implement this once, не per-component.
6. **`prefers-color-scheme`** — Phase 2+ dark mode out of scope; use stable tokens above for now.
7. **Touch targets ≥ 44dp** enforced via `min-height` / `min-width` on interactive elements. Use transparent padding to push hit area beyond visual icon size где needed (per dashboard §8 BLOCKER 1 fix).
8. **`font-feature-settings: "tnum"`** on pulse, prices, times, dates — apply class или CSS variable.
9. **Focus visible** — never `outline: none` without replacement focus ring (`elev-focus`).
10. **Lucide React** or **Lucide Vue** depending on Mini App stack. Tree-shake imports.

---

## § 18 · Status next steps

- [x] Phase A — read 9 priority files + logo inventory
- [x] Phase B — plan submitted + tech lead approve (founder verdict in `handoffs/sigma.txt` 2026-05-27)
- [x] Phase C — main draft sections § 0–§ 19, typography exploration HTML preview, logo color experiment SVG, AYLA README, EXPERIMENTS README
- [x] Phase F — adversarial review: Brand Guardian (6 findings F1-F6, 0 blockers) + Accessibility Auditor (15 findings, 4 BLOCKERS B1-B4) + frontend-design AI-cliché self-check PASS + mix prohibition self-check PASS. Findings summary § 18a below.
- [x] Phase G — Phase F findings applied inline:
  - **B1 (focus ring 1.4.11 fail)** — § 8 `elev-focus` rewritten to solid 2dp sage-600 ring with white inset (6.39:1)
  - **B2 (input border + nav underline 1.4.11)** — § 11 Input border resting → `warm-500` (4.59:1) + new `--border-input` semantic alias; Bottom-nav active underline → `sage-500` (4.65:1)
  - **B3 (composite aria-label)** — § 5 new subsection «Composite aria-label pattern» with worked example for wellness pulse, water dots, goal progress, time slot chips, prices
  - **B4 (modal/toast/input/bottom-sheet a11y)** — § 11 each spec gained explicit ARIA + focus-trap + Escape + focus-return + role-split + auto-dismiss control patterns
  - **F1** wordmark wording «over the «a»» (aligned с identity §2.4 verbatim)
  - **F3** Toast positive bg → sage-100 (consistent с § 4 semantic palette)
  - **F4** Card section Q-TAU-D3 misread footnote cleanup
  - **F5** Hex casing normalized lowercase throughout
  - **F6 / Q-SIG-5** Provider-side palette = same sage (founder-locked default)
  - **Findings 5/8/10-15** addressed inline (disabled state contrast, lang="en" scope expanded, list multi-target rule, toast pause-on-hover, amber + rose promoted to named tokens, purple AYLA contrast guidance)
- [ ] Phase I — save (this file) + EXPERIMENTS folder + AYLA README — all committed in single PR
- [ ] Phase J — handoff block for tech lead + W1 / Iota frontend
- [ ] Phase K — `git rebase origin/dev` + push + `gh pr create --base dev` + CI green + self-merge (NON-§H.3 docs)

### § 18a — Phase F adversarial review findings (audit trail)

**Brand Guardian subagent** (audit ID `a8dce40fee542cbf7`):
- 0 BLOCKERS · 6 FINDINGS (F1 wording / F2 records refund-pending framing / F3 toast bg inconsistency / F4 card misread cleanup / F5 hex casing / F6 Q-SIG-5 reframe)
- All applied · doc PASSING brand checks per audit list (sage scale + warm neutrals + voice + hybrid distinction + anti-patterns)

**Accessibility Auditor subagent** (audit ID `a037b12759974cae3`):
- 4 BLOCKERS · 15 FINDINGS · contrast claims mathematically verified (sage-500 = 4.65:1, warm-500 = 4.59:1, etc.)
- BLOCKERS B1-B4 all applied inline · pre-pilot findings (5/8/10-15) also applied inline rather than deferred
- WCAG 2.2 AA — pre-merge audit verdict: passes after fixes

**frontend-design skill self-check:** PASS — no Inter/Roboto default, no purple gradients в primary, no cookie-cutter patterns, distinctive Manrope (Russian-native designer) + restrained-minimal aesthetic POV cohesive throughout.

**Mix prohibition self-check:** PASS — § 13 separated from primary tokens; explicit allowed-list + forbidden-list; founder mix-prohibition quoted verbatim; sage color experiment correctly labeled «not canonical primary»; § 15 anti-patterns reinforce ALL CAPS / purple-in-Mini-App / purple+sage-same-surface forbidden.

**Severity:** P0 PRE_PILOT — foundational visual reference; W1 frontend implementation consistency depends on these tokens.

**Streams unblocked after merge:**
- W1 / Iota — frontend implementation of customer Mini App surfaces (consume CSS custom properties)
- Tau support mode — voice review / consistency patches reference these tokens
- Brand Guardian subagent — uses § 15 anti-patterns checklist
- Accessibility Auditor — verifies § 2/§ 3 contrast claims + § 9 motion rules

---

## § 19 · Sign-off

| Role | Approval | Date |
|---|---|---|
| Founder (brand hybrid usage canon — memory `project_ayla_brand_hybrid_usage`) | ✅ | 2026-05-27 |
| Tech Lead (Phase B plan approve, scope locked) | ✅ | 2026-05-27 |
| Sigma (author) | ✅ | 2026-05-27 |
| Brand Guardian subagent | ✅ | 2026-05-27 (6 findings F1-F6 applied) |
| Accessibility Auditor subagent | ✅ | 2026-05-27 (4 BLOCKERS B1-B4 + 11 findings applied) |
| frontend-design skill AI-cliché check | ✅ | 2026-05-27 (self-check PASS) |
| UX Architect | ☐ | (pending review) |
| W1 / Iota (frontend implementation reference) | ☐ | (pending impl) |

## Last verified

2026-05-27 r1 — initial draft. Manrope typography + Onest fallback. Sage primary + warm neutral + semantic palette. Lucide icon adoption resolves Q-TAU-D2 / D4 / D5. Hybrid logo distribution per memory `project_ayla_brand_hybrid_usage` — typography canonical wordmark for app, purple AYLA pack for bot/channel avatar only. Mix prohibition explicit.
