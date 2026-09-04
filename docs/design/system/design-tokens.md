# Ayla — Design Tokens

| Field | Value |
|---|---|
| **Date** | 2026-06-02 r2 |
| **Status** | P0 PRE_PILOT — foundational visual reference for all customer + provider surfaces |
| **Stream** | Sigma (Visual Design) |
| **Audience** | W1 / Iota frontend implementers · UX Architect · Brand Guardian · all subsequent visual work |
| **Foundation** | [`ayla-identity-and-brand.md`](../policies/ayla-identity-and-brand.md) (§2.1 wordmark · §3 personality · §4.4 sage-green canon · §7.1 surface naming) · [`customer-main-wellness-dashboard.md`](../../screens/customer-main-wellness-dashboard.md) (§7 brand notes + §8 WCAG blockers) · [`customer-records-flow.md`](../../screens/customer-records-flow.md) (§6 status vocabulary) · [`customer-booking-flow.md`](../../screens/customer-booking-flow.md) (§11 a11y patterns) · [`customer-food-scanner-flow.md`](../../screens/customer-food-scanner-flow.md) (§1 voice foundation) · [`master-solo-surface.md`](../../screens/master-solo-surface.md) (provider-side) |
| **Memory refs** | `project_ayla_brand_hybrid_usage` (2026-05-27 hybrid usage canon) · `project_ayla_personal_ai` (lowercase «ayla» + voice) · `project_pilot_scope_discipline` (locked scope) · `project_records_voice_principles` (status taxonomy) · `project_ayla_active_streams` (Sigma stream) |

> **Поправка 04.09.2026 (DRF-1462).** §2–§4 переписаны: палитра переехала
> на фиолетовую с борда DRF-1181 (решение владельца §21-ter), и HEX из
> этого документа удалены — единственный источник цвета теперь
> `apps/miniapp/src/styles/tokens.css`. Остальные секции не трогались;
> старые имена токенов в §5 и §11 читаются по карте перехода в §2.5.

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

## § 2 · Цвет — один источник

> **Палитра больше не живёт в этом файле.** Единственный источник —
> `apps/miniapp/src/styles/tokens.css`. Здесь только происхождение значений,
> пересчитанные контрасты и карта перехода со старых имён.
>
> Так сделано намеренно. Аудит `docs/AUDIT_SOLO_MASTER_AND_COLORS.md` §2.1
> нашёл палитру в **пяти** местах сразу — рантайм, этот документ, `DESIGN.md`
> соседнего репозитория и два разных набора в Linear, — и с любым каноном
> совпадали 2 переменные из 14. Держать здесь вторую копию HEX значило бы
> договориться синхронизировать её вручную; ровно этого договора никто не
> сдержал за три месяца. Таблица ниже — производная, а не источник, и её
> цифры пересчитывает `tools/lint/miniapp_token_contrast.py` на каждом
> прогоне CI.

### 2.1 · Откуда взяты значения

Канон — борд задачи **DRF-1181** (Master App), решение владельца **§21-ter**
от 04.09.2026 (`docs/OPEN_DECISIONS.md`). Оно отменяет §21-bis от 25.08
(«Sage visual canon — RESTORED / CONFIRMED»): sage `#5a8557` каноном быть
перестал. Область — **все три поверхности**: клиент, салон, мастер; своих HEX
у клиентских и салонных бордов нет ни одного.

На борде подписаны восемь значений:

| Роль на борде | Подпись |
|---|---|
| Основной | `#5B68FF` |
| Акцентный | `#22C55E` |
| Предупреждение | `#F59E0B` |
| Ошибка | `#EF4444` |
| Текст | `#111827` |
| Второстепенный | `#6B7280` |
| Фон | `#F8FAFC` |
| Граница | `#E5E7EB` |

**За истину берётся подпись, не пиксель.** Борд машинно сгенерирован: в
плоской кнопке 1128 различных цветов при однородности 0.5 %, и свотч,
подписанный `#5B68FF`, отрисован как `#041FFA`. Пиксели с бордов не берутся
ни при каких обстоятельствах.

### 2.2 · Правило вывода — одно на всю палитру

Ролей в коде четырнадцать, подписей на борде восемь. Разницу закрывает одно
правило, применённое механически и одинаково в обеих темах:

> тон (H) и насыщенность (S) подписанного значения сохраняются; светлота (L)
> двигается на минимум, достаточный, чтобы дать **4.5:1** к худшей
> поверхности своей темы.

Подписанное значение, которое проходит как есть, стоит в `tokens.css`
дословно. Сдвинутое помечено там же стрелкой на исходное — например
`--c-accent: #4452ff; /* ← #5B68FF */`.

Сдвиг понадобился, потому что подписи и доступность расходятся не на грани:
`#22C55E` даёт 2.28:1 на белом, `#F59E0B` — 2.15:1, `#EF4444` — 3.76:1, а
все три красят текст в `globals.css`. Сам основной `#5B68FF` даёт 4.31:1 —
не проходит AA для основного текста, промахиваясь на 0.19. У sage такая
работа была проделана и записана; у фиолетового её не было, и §21-ter п. 2
прямо относит её к объёму задачи, а не к следующей.

Шесть ролей подписи не имеют вовсе и выведены:

| Роль | Как выведена |
|---|---|
| `--c-surface-1` | белый — карточка на подписанном фоне; так же на самом борде |
| `--c-surface-2` | шаг ряда, из которого взяты три подписанных нейтрали (Граница = `gray-200`, Второстепенный = `gray-500`, Текст = `gray-900`) |
| `--c-overlay` | подписанный «Текст» с альфой |
| `--c-text-on-accent` | белый в светлой теме, подписанный «Текст» — в тёмной |
| `--c-accent-pressed` | основной + 18 % чёрного (светлая) / белого (тёмная) |
| `--c-accent-subtle` | основной 12 % на белом (светлая) / 18 % на фоне (тёмная) |

### 2.3 · Тёмная тема — выведена, не перенесена

Тёмной темы нет ни в каноне, ни на одном из 24 бордов, а в коде её
четырнадцать переменных существуют и работают: MAX не даёт API темы, и
`prefers-color-scheme` — единственный способ не выдать белый экран ночью.
Сверять не с чем, поэтому значения выведены по правилу §2.2 плюс одно
дополнительное:

> два нейтральных полюса меняются местами. Фон тёмной темы — подписанный
> «Текст» `#111827`; основной текст тёмной темы — подписанный «Фон»
> `#F8FAFC`. Промежуточные поверхности берутся из того же ряда `gray`, из
> которого взяты подписанные нейтрали.

Побочный результат этого правила стоит назвать: на тёмном фоне подписанный
«Акцентный» `#22C55E` проходит AA **дословно** — то есть в тёмной теме цвет
борда стоит ровно как подписан, а сдвинут он только в светлой.

Второй результат: `--c-text-on-accent` в тёмной теме перестаёт быть белым.
Осветлённый основной `#9da5ff` даёт с белым 2.27:1; текст на нём — тёмный.

**Это решение исполнителя, а не владельца.** Оно вынесено отдельным
вопросом — `docs/OPEN_DECISIONS.md` §21-quater, вопрос 1.

### 2.4 · Контрасты — пересчитаны

Все пары взяты из `globals.css`, а не придуманы: каждая поверхность реально
стоит в `background`, каждая роль — в `color`. Порог один — **WCAG 2.2 AA
4.5:1** для основного текста: токен не знает, каким кеглем им покрасят.

| Текст | На фоне | Светлая | Тёмная | WCAG AA 4.5:1 |
|---|---|---|---|---|
| `--c-text-primary` | `--c-bg` | 16.96:1 | 16.96:1 | ✅ обе |
| `--c-text-primary` | `--c-surface-1` | 17.74:1 | 14.03:1 | ✅ обе |
| `--c-text-primary` | `--c-surface-2` | 16.12:1 | 9.85:1 | ✅ обе |
| `--c-text-secondary` | `--c-bg` | 4.76:1 | 7.78:1 | ✅ обе |
| `--c-text-secondary` | `--c-surface-1` | 4.98:1 | 6.44:1 | ✅ обе |
| `--c-text-secondary` | `--c-surface-2` | 4.52:1 | 4.52:1 | ✅ обе |
| `--c-accent` | `--c-bg` | 5.15:1 | 7.82:1 | ✅ обе |
| `--c-accent` | `--c-surface-1` | 5.39:1 | 6.47:1 | ✅ обе |
| `--c-accent` | `--c-surface-2` | 4.90:1 | 4.54:1 | ✅ обе |
| `--c-accent-pressed` | `--c-bg` | 6.94:1 | 9.19:1 | ✅ обе |
| `--c-accent-pressed` | `--c-surface-1` | 7.26:1 | 7.60:1 | ✅ обе |
| `--c-accent-pressed` | `--c-surface-2` | 6.60:1 | 5.34:1 | ✅ обе |
| `--c-success` | `--c-bg` | 4.79:1 | 7.79:1 | ✅ обе |
| `--c-success` | `--c-surface-1` | 5.01:1 | 6.44:1 | ✅ обе |
| `--c-success` | `--c-surface-2` | 4.56:1 | 4.52:1 | ✅ обе |
| `--c-warning` | `--c-bg` | 5.42:1 | 9.27:1 | ✅ обе |
| `--c-warning` | `--c-surface-1` | 5.67:1 | 7.67:1 | ✅ обе |
| `--c-warning` | `--c-surface-2` | 5.16:1 | 5.39:1 | ✅ обе |
| `--c-danger` | `--c-bg` | 5.62:1 | 9.33:1 | ✅ обе |
| `--c-danger` | `--c-surface-1` | 5.88:1 | 7.72:1 | ✅ обе |
| `--c-danger` | `--c-surface-2` | 5.34:1 | 5.42:1 | ✅ обе |
| `--c-text-primary` | `--c-accent-subtle` | 14.93:1 | 12.18:1 | ✅ обе |
| `--c-accent` | `--c-accent-subtle` | 4.54:1 | 5.62:1 | ✅ обе |
| `--c-accent-pressed` | `--c-accent-subtle` | 6.11:1 | 6.60:1 | ✅ обе |
| `--c-text-on-accent` | `--c-accent` | 5.39:1 | 7.82:1 | ✅ обе |
| `--c-text-on-accent` | `--c-accent-pressed` | 7.26:1 | 9.19:1 | ✅ обе |
| `--c-text-on-accent` | `--c-success` | 5.01:1 | 7.79:1 | ✅ обе |
| `--c-text-on-accent` | `--c-warning` | 5.67:1 | 9.27:1 | ✅ обе |
| `--c-text-on-accent` | `--c-danger` | 5.88:1 | 9.33:1 | ✅ обе |
| `--c-warning` | подложка 10 % на `--c-bg` | 4.73:1 | 7.78:1 | ✅ обе |
| `--c-warning` | подложка 10 % на `--c-surface-1` | 4.95:1 | 6.34:1 | ✅ обе |
| `--c-warning` | подложка 10 % на `--c-surface-2` | 4.52:1 | 4.51:1 | ✅ обе |
| `--c-danger` | подложка 10 % на `--c-bg` | 4.74:1 | 7.79:1 | ✅ обе |
| `--c-danger` | подложка 10 % на `--c-surface-1` | 4.94:1 | 6.28:1 | ✅ обе |
| `--c-danger` | подложка 10 % на `--c-surface-2` | 4.50:1 | 4.52:1 | ✅ обе |
| `--c-divider` | `--c-surface-1` | 1.24:1 | 1.94:1 | ❌ — только декоративная линия, см. ниже |

**Граница — только декоративная линия.** `--c-divider` даёт 1.24:1 и
использоваться единственной границей интерактивного элемента не может
(WCAG 1.4.11 требует 3:1). Это ограничение унаследовано: у прежней палитры
хайрлайн давал 1.34:1, у sage-канона — 1.27:1. Подпись борда сохранена как
есть, а не поднята до 3:1, потому что подъём границы до тёмно-серой изменил
бы вид каждой карточки — это уже не цвет токена, а решение по виду, и оно
вынесено владельцу отдельным вопросом (`OPEN_DECISIONS.md` §21-quater,
вопрос 2).

**Цветной текст на подложке своего же цвета.** Шесть правил `globals.css`
(`.callout--danger`, `.m6-bubble--failed`, `.unbookable-badge`,
`.m-card__chip--warning`, `.admin-chip--warn`, `.m-notif__banner-warning`)
кладут цветной текст на `color-mix` из него самого. Доли были 14 %, 18 % и
20 %; все приведены к **10 %**. Иначе AA пришлось бы добирать затемнением
самих токенов, и основной ушёл бы от подписи вдвое дальше — дешевле сделать
подложку светлее, чем увести цвет владельца.

### 2.5 · Карта перехода со старых имён

Секции §2 (sage), §3 (warm) и §4 (amber / rose) удалены. Имена из них
встречаются ниже по документу — в §5 и §11; читать их следует так:

| Было | Стало | Замечание |
|---|---|---|
| `sage-400` / `sage-500` / `sage-600` | `--c-accent` / `--c-accent-pressed` | декоративного и текстового вариантов больше нет: одна роль, и она AA |
| `sage-100` | `--c-accent-subtle` | |
| `sage-700` | `--c-success` | |
| `warm-50` | `--c-bg` | |
| `warm-100` | `--c-surface-2` | |
| `warm-200` / `--hairline` | `--c-divider` | по-прежнему только декоративная линия |
| `warm-300` / `--track-empty` | `--c-divider` | |
| `warm-400` | `--c-text-secondary` | плейсхолдер и вторичный текст слились в одну роль |
| `warm-500` / `--ink-muted` / `--border-input` | `--c-text-secondary` | |
| `warm-600` / `--ink-soft` | `--c-text-secondary` | |
| `warm-700` / `warm-800` / `--ink` | `--c-text-primary` | |
| `amber-100` / `amber-700` | `--c-warning` (подложка 10 % / текст) | |
| `rose-100` / `rose-600` / `rose-700` | `--c-danger` (подложка 10 % / заливка / текст) | |
| `--paper` | `--c-bg` | |
| `--border-focus` | `--c-accent` | |

### 2.6 · Что осталось верным из прежних §2–§4

Эти правила про смысл, а не про HEX, и переживают смену палитры:

- ❌ **Красный не используется для статуса.** «Отменена», «Не состоялась» —
  нейтральный конец жизненного цикла, а не ошибка
  (`customer-records-flow.md` §10.6). `--c-danger` — только валидация формы.
- ❌ **«Отменена салоном» — предупреждение, не успех.** Решение владельца от
  26.05 (`project_records_voice_principles`): «провайдер отменил — это не
  success». Токен `--c-warning` плюс значок ⚠ и словесная подпись.
- ❌ **Плашка одним только цветом, без значка и русской подписи** —
  нарушение WCAG 1.4.1.
- ❌ **Градиент на белом** — признак сгенерированного макета; плоская
  заливка с хайрлайном предпочтительна.
- ❌ **Чистый чёрный `#000`** для текста.

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
| 🖼 (Из галереи / image picker fallback) | `image` | `icon-image` |
| (photo failed-to-load placeholder) | `image-off` | `icon-image-off` |
| ⚡ (offline banner) | `wifi-off` | `icon-offline` |
| ⏱ (API down / timeout state) | `clock` | `icon-clock` |
| ↻ (retry button) | `refresh-cw` | `icon-retry` |
| ✎ (edit / write manually) | `pencil` | `icon-edit` |
| ⚖ (weight / portion) | `scale` | `icon-weight` |
| − / + (portion stepper) | `minus` / `plus` | `icon-minus` / `icon-plus` |
| ⋯ (overflow menu) | `more-horizontal` | `icon-more` |
| × (modal close / dismiss) | `x` | `icon-close` |
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
Background: rose-600 -> --c-danger
Hover: rose-700 -> --c-danger (pressed state via opacity)
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

### Choice chip (meal-type, mutually-exclusive group)

Used on `customer-food-scanner-flow.md` F1 / F3 / manual entry (4-chip meal-type row: Завтрак / Обед / Ужин / Перекус). Default = current local time bucket per F1 spec.

```
Min size: 64dp wide × 64dp tall (≥44dp touch target — WCAG 2.5.8)
Padding: space-3 all sides
Layout: emoji-or-icon above label (vertical flex)
Background (default): warm-100
Background (selected): sage-100
Border (default): 1dp warm-200
Border (selected): 1dp sage-500 (4.65:1 — 1.4.11 safe)
Selected non-color affordance: ● indicator (6dp sage-500 dot) at bottom-center OR right of label — required (color-only forbidden)
Text (default): text-caption (13dp) weight-medium warm-700
Text (selected): text-caption weight-semibold sage-700
Icon/emoji size: 20dp above label, space-1 gap to label
Radius: radius-md (8dp)
Gap between chips: space-2 (horizontal flex)
Focus: elev-focus ring per chip
```

**A11y mandatory (WCAG 1.3.1 + 1.4.1 + 4.1.2):**

```html
<div role="radiogroup" aria-label="Когда ешь">
  <button role="radio" aria-checked="false" tabindex="-1">🌅 Завтрак</button>
  <button role="radio" aria-checked="true"  tabindex="0">🥗 Обед</button>
  <button role="radio" aria-checked="false" tabindex="-1">🍽 Ужин</button>
  <button role="radio" aria-checked="false" tabindex="-1">🍎 Перекус</button>
</div>
```

- `role="radiogroup"` parent with `aria-label`
- Each chip `role="radio" aria-checked` (NOT `aria-selected`)
- Roving tabindex pattern — only selected chip is `tabindex="0"`; arrows cycle selection
- Keyboard: ← / → / ↑ / ↓ cycles selection within group; Tab leaves group
- Selected state communicated via `aria-checked`, not color alone

**Anti-patterns:**
- ❌ Color-only selected state without ● indicator + aria-checked
- ❌ `role="button"` instead of `role="radio"` (loses semantic group meaning)
- ❌ Using for non-mutually-exclusive multi-select (use Tag/Toggle chip pattern — out of scope MVP)
- ❌ Disabled chip without `aria-disabled="true"`

### Photo capture zone (empty-state CTA)

Used on food-scanner F1 — large tap target (~240dp) inviting customer to take or pick a meal photo. Two-CTA stack pattern.

```
Min height: 240dp
Width: 100% container
Background: white
Border: 1.5dp dashed warm-300 OR 1dp solid warm-200 (designer choice per surface — dashed signals "drop/pick", solid signals "card")
Radius: radius-lg (12dp)
Padding: space-6 all sides
Layout: vertical flex, center-aligned
Icon: Lucide `camera` 48dp warm-500 stroke (centered top)
Helper: text-body warm-600 «Сделай фото или выбери из галереи» (centered)
CTA stack: 2 Secondary buttons vertical, space-3 gap, max-width 280dp
  CTA 1: Lucide `camera` + «Сделать фото» — triggers <input type="file" accept="image/*" capture="environment">
  CTA 2: Lucide `image` + «Из галереи» — triggers <input type="file" accept="image/*">
```

**A11y mandatory:**
- Zone container is NON-interactive (`<div>`, not `<button>`) — CTAs are the only tap targets (simpler focus model than nested-interactive)
- Each CTA is a real `<button>` (or `<label for="…">` wrapping hidden `<input type="file">`) — keyboard accessible
- Helper text linked to CTAs via `aria-describedby` if it provides decision context

**Anti-patterns:**
- ❌ Whole zone clickable + nested CTAs (double-tap-target conflict)
- ❌ `<input type="file">` styled as the only visible CTA without `<label>` wrapper (no keyboard activation)
- ❌ Drag-and-drop only on mobile (touch can't drag — provide tap CTAs always)

### Photo preview frame (resolves Q-SIG-6)

Used on F2 (140dp preview while «Узнаю что на фото») / F3 (80dp thumbnail above recognition result) / future surfaces (booking provider photos, master gallery — Phase 2+).

```
Sizes: 80dp (thumbnail), 140dp (medium preview), 100% width (cover photo Phase 2+)
Aspect ratio: 1:1 square (meal photos) — `aspect-ratio: 1 / 1`
Background: warm-100 (placeholder fill — visible while image loads)
Border: 1dp warm-200
Radius: radius-md (8dp)
Object-fit: cover (crop overflow, never distort)
Fallback (image failed / no src): Lucide `image-off` 32dp warm-400 centered
Loading: skeleton-shimmer pattern (per Skeleton spec above) — same warm-100 base
```

**A11y mandatory (WCAG 1.1.1):**

```html
<!-- Preferred — descriptive alt if dish_name known after scan -->
<img src="..." alt="Фото блюда: гречка с курицей">
<!-- Fallback — generic alt while scan pending or for unknown dishes -->
<img src="..." alt="Фото блюда">
<!-- Decorative-only (rare — photo IS the content here, alt always required) -->
```

- `alt` attribute **mandatory** — never empty for meal scanner photos (photo IS the recognition subject)
- If image fails to load and fallback icon shows: parent `aria-label="Не удалось загрузить фото"` + `role="img"`
- Loading state: `aria-busy="true"` on parent while pending

**Anti-patterns:**
- ❌ `<div>` with `background-image` (loses alt + semantic — use `<img>`)
- ❌ Empty alt `alt=""` on meal photo (photo IS content per scanner UX)
- ❌ `object-fit: contain` (creates letterboxing — cover canonical for square meal frame)

### Portion stepper (−/+ numeric control)

Used on F3 to adjust portion multiplier (steps 50 % / 75 % / 100 % / 125 % / 150 % / 175 % / 200 %).

```
Layout: horizontal flex, space-4 gap, align-center
Button (− / +): 44×44dp circular (radius-full) OR radius-md
Button background: warm-100
Button text: text-h3 (20dp) weight-medium warm-700
Button press: warm-200 bg + scale(0.96)
Button disabled (at min/max): warm-200 bg + warm-400 text + cursor not-allowed
Center label: text-h3 weight-semibold warm-800
Center label width: min 80dp (accommodates «200 %»)
Center label font-feature-settings: "tnum" (tabular numerals — number doesn't jump width)
Focus: elev-focus ring per button (− and + each)
```

**A11y mandatory (WCAG 1.4.3 + 4.1.2 + 4.1.3):**

```html
<div role="group" aria-label="Размер порции, сейчас 100 процентов, минимум 50, максимум 200">
  <button aria-label="Уменьшить порцию">−</button>
  <span class="portion-value" role="status" aria-live="polite">100 %</span>
  <button aria-label="Увеличить порцию">+</button>
</div>
```

- Group container `role="group"` with composite `aria-label` (includes current + range)
- − and + buttons get `aria-label="Уменьшить порцию"` / «Увеличить порцию» (visible «−/+» glyphs are NOT screen-reader-readable as actions)
- Center label `role="status" aria-live="polite"` — VoiceOver announces new value on change («сто двадцать пять процентов»)
- Disabled at min/max: button `disabled` attribute + visual disabled state
- Keyboard: Space / Enter activates; native button focus order

**Anti-patterns:**
- ❌ − / + buttons without `aria-label` (screen reader hears «minus» / «plus» as math, not action)
- ❌ Center value as plain `<span>` (change not announced)
- ❌ Underlying grams change without announcing in label (e.g., "150g → 113g" silent — include grams in aria-label of group, refresh on change)

### Pulsing dots loader (F2 scanner-specific)

Used on F2 «Узнаю что на фото» state. Animation already anchored in § 9 (1200ms ease-in-out infinite, `.pulse-dots` class targeted by reduced-motion fallback). This adds the component visual spec.

```
Container: horizontal flex, space-2 gap, align-center
Dot count: 3 (per F2 ASCII)
Dot size: 8dp diameter
Dot background: sage-500
Dot radius: radius-full
Animation: pulse-dot 1200ms ease-in-out infinite
  Staggered delays: 0ms / 200ms / 400ms
  Property: opacity 0.3 → 1.0 → 0.3
Reduced-motion: static dots at opacity 0.6, no animation (per § 9 `.pulse-dots` rule — already global)
```

**A11y mandatory (WCAG 1.4.13 + 4.1.3):**

```html
<div class="pulse-dots" aria-hidden="true">
  <span></span><span></span><span></span>
</div>
<p role="status" aria-live="polite">Узнаю что на фото</p>
```

- Dots themselves `aria-hidden="true"` (decorative)
- Adjacent status text MUST carry the announcement — `role="status" aria-live="polite"`
- After 3s (timeout fallback per F2 spec): additional helper text + cancel button surface — do NOT re-trigger live region (would re-announce)

**Anti-patterns:**
- ❌ Dots as the ONLY loading signal (no status text — screen reader silent)
- ❌ `role="status"` on the dots themselves (announces "blank" repeatedly)
- ❌ Spinner emoji (⏳ ⌛) instead of dots (loses sage-green brand, off-tone)

### Persistent banner (offline / stale data)

Used on F1 + scanner offline state. Distinct from Toast — banner is **persistent until state changes**, no dismiss button, sticky position.

```
Position: sticky top (z-index above content, below MAX header chrome)
Container: 100 % width
Padding: space-2 y / space-4 x
Min height: 36dp
Background: amber-100 (offline / warning) | warm-100 (neutral info)
Text: text-caption (13dp) weight-medium amber-700 (offline) | warm-600 (info)
Icon: Lucide 16dp inline before text, space-2 gap
  Offline: `wifi-off`
  Stale data: `clock`
  Info: `info`
Border-bottom: 1dp amber-700/20% (offline) | warm-200 (info)
No dismiss button (persistent — defeats purpose if dismissable)
```

**A11y mandatory (WCAG 4.1.3):**
- Banner on appearance: `role="status" aria-live="polite"` (offline state change — assertive too jarring for routine connectivity blip)
- For critical state (e.g., session-expired — out of scope MVP): `role="alert" aria-live="assertive"`
- Icon `aria-hidden="true"` (text conveys meaning)

**Anti-patterns:**
- ❌ Persistent banner with × dismiss button (dismissable = use Toast, not Banner)
- ❌ Red banner for offline (offline ≠ error — neutral degraded state, amber per § 4 warning semantic)
- ❌ Banner replacing toast for transient events (use Toast — Banner is sticky)
- ❌ Multiple banners stacked (queue → single visible at a time, priority: alert > warning > info)

### Progress bar (linear)

Used on F4 daily total «1 720 / 2 100 ккал · 82 %», dashboard wellness pulse fills, goal progress. Fixes dashboard § 8 BLOCKER 6 (empty track was sage-300, now `--track-empty` = warm-300).

```
Track: warm-300 bg (semantic --track-empty)
Fill: sage-500 bg (semantic --border-focus / brand primary)
Height: 8dp
Radius: radius-full (pill — applies to both track + fill)
Width: 100 % container
Width transition on value change: motion-base + ease-out + width
```

**A11y mandatory — composite aria-label pattern (per § 5):**

```html
<div role="group" aria-label="Питание: 1720 из 2100 килокалорий, 82 процента дневной цели">
  <span class="value-readable" aria-hidden="true">1 720 / 2 100 ккал</span>
  <div class="progress-bar" aria-hidden="true">
    <div class="progress-fill" style="width: 82%"></div>
  </div>
  <span class="percent" aria-hidden="true">82 %</span>
</div>
```

OR native:

```html
<progress value="1720" max="2100" aria-label="Питание: 1720 из 2100 килокалорий"></progress>
```

- Composite group preferred when visual stack includes text + bar + percent (3 elements, group label combines)
- Native `<progress>` acceptable for standalone bars (no surrounding text)
- Bar element itself ALWAYS `aria-hidden="true"` when wrapped in composite group (parent label is canonical)

**Anti-patterns:**
- ❌ Sage-300 track (fails 1.4.11 3:1 between filled vs empty per dashboard § 8 BLOCKER 6 — must be warm-300)
- ❌ Red/amber fill for "over goal" (anxiety — wellness voice neutral, use sage-600 darker or simply don't visually flag overshoot)
- ❌ Percentage-only label («82 %» alone — screen reader missing context; always include value/max in aria-label)
- ❌ Sub-1dp animation steps (jank — use width transition motion-base)

### Section header divider («── Когда ──»)

Used on F1 / F3 / F4 / manual entry to label sub-sections («Когда», «Фото», «Дата», «Примерно», «Сегодня»). Horizontal hairlines flank an inline label.

```
Container: flex horizontal align-center, gap space-3
Layout: hairline-grow · label · hairline-grow
Hairline: flex 1, 1dp warm-200, border-top OR background-color (height 1dp)
Label: text-caption (13dp) weight-medium warm-500, sentence case (NOT uppercase)
Vertical margin: space-5 above, space-3 below
No bottom border on parent
```

```html
<h3 class="section-divider">
  <span class="hairline" aria-hidden="true"></span>
  Когда
  <span class="hairline" aria-hidden="true"></span>
</h3>
```

**A11y mandatory:**
- Use semantic heading element (`<h3>` / `<h4>` per outline depth) — NOT plain `<div>` (screen reader navigation by heading required)
- Hairline spans `aria-hidden="true"` (decorative only)
- ASCII em-dash literals «── Когда ──» MUST NOT appear in DOM text — use CSS hairlines (otherwise screen reader reads «dash dash Когда dash dash»)

**Anti-patterns:**
- ❌ Em-dash literals in DOM (screen reader gibberish)
- ❌ ALL CAPS label («КОГДА» — sentence case canonical per § 5)
- ❌ `<div>` with visual h3 styling but no semantic role (heading navigation broken)
- ❌ Single hairline below label (looks like underline, not divider — both sides required)

### Confidence indicator (low-conf scan state)

Visual state on F3 «✏️ Уточнить» Secondary button when scan confidence <0.6 per `skill.py`. NOT a separate component — state override on Secondary button + paired tooltip.

```
Override on Secondary button:
  Border: 1.5dp sage-300 (subtle — overrides default 1dp sage-500)
  Background: sage-50 (very subtle tint — overrides transparent)
  Otherwise inherits Secondary spec
Lead copy on F3 (not button-internal): «Похоже на: …» (vs «Узнала: …» for ≥0.6)
Tooltip on hover/focus: «Прикинула приблизительно — давай уточним вместе»
```

**A11y mandatory:**
- Visual border tint MUST NOT be sole signal — paired with:
  1. Lead copy «Похоже на:» (hedge wording per skill.py)
  2. Tooltip via `aria-describedby` (see Tooltip below)
- Tooltip MUST be touch-accessible (not hover-only)

**Anti-patterns:**
- ❌ Pulse animation on low-conf button (anxiety-inducing — violates voice «calm / supportive» per food-scanner § 1)
- ❌ Red / amber tint (low confidence ≠ error — wellness voice keeps neutral approximate)
- ❌ Icon-only signal without copy adjustment (color/icon blind users miss it)

### Tooltip

Used on F3 confidence indicator (above) + future inline help. Touch-first behavior (tap-toggle, NOT hover-only).

```
Container max-width: 240dp
Padding: space-2 y / space-3 x
Background: warm-700 -> --c-text-primary
Text: text-caption (13dp) weight-regular white
Radius: radius-sm (4dp)
Shadow: elev-2
Arrow: 6dp triangle pointing to anchor (warm-700 fill)
Position: above anchor (auto-flip below if viewport overflow)
Enter: motion-fast + ease-out + opacity
Auto-dismiss on touch: 5000ms (or tap elsewhere)
```

**A11y mandatory (WCAG 1.4.13):**

```html
<button aria-describedby="conf-tip">✏️ Уточнить</button>
<div role="tooltip" id="conf-tip">Прикинула приблизительно — давай уточним вместе</div>
```

- Anchor element: `aria-describedby="<tooltip-id>"`
- Tooltip element: `role="tooltip"` + matching `id`
- Touch behavior: tap-toggle (NOT hover — hover unavailable on touch); first tap on anchor opens tooltip without triggering button action, second tap activates button (or provide explicit «?» help icon as separate anchor)
- Keyboard: focus on anchor shows tooltip; Escape dismisses
- Hover desktop: 300ms delay before show; immediate hide on leave

**Anti-patterns:**
- ❌ Hover-only trigger (touch users locked out)
- ❌ Critical information in tooltip (lost on touch / screen reader skip — duplicate in body copy)
- ❌ Click-to-action conflict (tooltip + button on same element with single-tap behavior — use explicit «?» icon or separate inline help)
- ❌ Tooltip wider than 240dp (becomes paragraph — promote to inline help text)

### Textarea (multi-line input)

Extension of Input. Used on F3 «Заметка (необязательно)» free-text field.

```
Min height: 80dp (~3 visible lines on 13dp body)
Max height: 200dp (then internal scroll)
Padding: space-3 all sides (uniform — Input has space-3 y / space-4 x; textarea square pad reads cleaner for paragraph text)
Otherwise inherits Input spec entirely:
  Border: 1dp warm-500 (--border-input, 4.59:1 — 1.4.11 safe)
  Radius: radius-md (8dp)
  Background: white
  Text: text-body (15dp) warm-800
  Placeholder: warm-400
  Focus: 2dp sage-500 border + elev-focus ring
  Error: 2dp rose-600 border + rose-100 bg
  Disabled: warm-100 bg + warm-500 text
Resize handle: hidden on mobile (`resize: none`), visible vertical on desktop (`resize: vertical`)
```

**A11y mandatory (WCAG 1.3.1 + 3.3.2):**

```html
<label for="note">Заметка (необязательно)</label>
<textarea id="note" rows="3" aria-describedby="note-helper" maxlength="500"></textarea>
<div id="note-helper">До 500 символов · <span aria-live="polite">0 / 500</span></div>
```

- Visible `<label for>` mandatory — placeholder is NOT a label
- Helper text linked via `aria-describedby`
- Character counter (if applied): visible counter + `aria-live="polite"` announces near limit (last 20 chars)
- Optional fields: «(необязательно)» in label text (NOT placeholder) — screen reader hears it

**Anti-patterns:**
- ❌ Placeholder as the only label («Заметка...» disappears on focus — accessibility blocker)
- ❌ No character counter on `maxlength` field (silent truncation surprise)
- ❌ Fixed height that can't grow (long notes scroll-trap user)

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

> **Поправка 04.09.2026 (DRF-1462).** Правило ниже построено на том, что интерфейс приложения — sage, а фиолетовый принадлежит только аватару бота. §21-ter это основание убрал: интерфейс теперь тоже фиолетовый, но **другой** — `--c-accent` `#4452ff` против `#7d63ef` у аватара. Запрет «не смешивать» потерял смысл, а два несогласованных фиолетовых рядом — новый вопрос, и он вынесен владельцу (`OPEN_DECISIONS.md` §21-quater, вопрос 3). До ответа ассеты аватара не трогаются.

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
| Q-SIG-6 | ✅ | Photo system tokens (food scanner photo preview frames, master photos) — borders, radius, fallback placeholder | **RESOLVED 2026-06-02** — § 11 Photo preview frame component spec (80/140dp/cover sizes, warm-100 placeholder, Lucide `image-off` fallback, mandatory alt rules) covers food_scanner F2/F3. Booking provider photos + master gallery (Phase 2+) inherit same spec. |
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
- [x] Phase I — save (this file) + EXPERIMENTS folder + AYLA README — all committed in single PR (shipped 2026-05-27 PR #916, merged into dev)
- [x] Phase J — handoff block for tech lead + W1 / Iota frontend (Q-SIG-1/3/8 + F2 surfaced)
- [x] Phase K — `git rebase origin/dev` + push + `gh pr create --base dev` + CI green + self-merge (NON-§H.3 docs) (PR #916 merged 2026-05-27)
- [x] **r2 follow-up 2026-06-02** — W1 #164 food_scanner token-coverage gap-fill (P0 support):
  - §11 — 11 new component specs (Choice chip, Photo capture zone, Photo preview frame, Portion stepper, Pulsing dots loader, Persistent banner, Progress bar, Section header divider, Confidence indicator, Tooltip, Textarea) — each with explicit ARIA + anti-patterns
  - §10 — Lucide map extended (11 new icon aliases: image, image-off, wifi-off, clock, refresh-cw, pencil, scale, minus, plus, more-horizontal, x)
  - §14 — Q-SIG-6 (photo system tokens) RESOLVED inline by §11 Photo preview frame spec
  - DoD: W1 #164 has complete token set for all 5 food_scanner screens (F1 / F2 / F3 / F3-Clarify modal / F4) + 4 error states (Not Recognized / API Down / Photo Upload Failed / Offline) + Manual Entry Fallback. No remaining token gaps.

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

2026-06-02 r2 — W1 #164 food_scanner token-coverage gap-fill (P0 support per `project_ayla_active_streams`). Added 11 component specs to §11 (Choice chip, Photo capture zone, Photo preview frame, Portion stepper, Pulsing dots loader, Persistent banner, Progress bar, Section header divider, Confidence indicator, Tooltip, Textarea — each with composite ARIA pattern + anti-patterns). Extended §10 Lucide map with 11 food_scanner-relevant aliases. Resolved Q-SIG-6 (photo system tokens) inline by §11 Photo preview frame. DoD: W1 has complete token set for all 5 food_scanner screens + 4 error states + manual entry fallback.
