# AYLA — design experiments

Working iteration artefacts for the Sigma visual design stream. **Not canonical** — these files document exploration, not shipped brand assets.

| File | Purpose |
|---|---|
| `typography-manrope-preview.html` | Manrope rendering на curated русской UI-копии (Tau-shipped samples) + Cyrillic stress lab + Manrope ↔ Onest side-by-side |
| `logo-sage-experiment.svg` | Color swap experiment — purple `#7D63EF` → sage `#5A8557` |
| `logo-sage-render.html` | Side-by-side preview оригинальной purple SVG ↔ sage swap (open в браузере) |

---

## Typography experiment outcome

**Verdict: Manrope locked as primary display + body font.**

Evaluated на shipped Tau customer-facing copy: dashboard greeting, booking flow detail, wellness captions, button labels, bottom nav, 8-status badges, B5/B6/B11 reminder long-form.

**Cyrillic stress checklist:**

| Check | Manrope | Onest (backup) |
|---|---|---|
| «й» / «ё» / hooks readable на 13–15dp | ✅ | ✅ |
| «щ» / «ц» / «ъ» / «ь» — equal proportions | ✅ | ✅ wider hooks |
| Wide Cyrillic не разрывает 44dp кнопки | ✅ | ✅ |
| Weight contrast 400 ↔ 600 различим на body 15px | ✅ | ✅ |
| Tabular numerals (`font-feature-settings: "tnum"`) | ✅ | ✅ |
| ₽ currency symbol — proper height + spacing | ✅ | ✅ |
| «ёлочки» — углы corner-style, не straight quotes | ✅ | ✅ |
| Em dash длина + kerning | ✅ | ✅ |
| Display 32–48px с letter-spacing −2.5/−3.5% не схлопывает | ✅ | ✅ |
| Variable font + Google Fonts CDN availability | ✅ | ✅ |
| Designed by native Russian speaker | ✅ (Mikhail Sharanda) | ✅ (Anatoly Kashin) |

**Why Manrope wins:**

- Geometric modern sans с deliberate Cyrillic design (Sharanda — Russian native)
- Tighter, more "tech-confident" feel — aligns с calm-but-precise «подруга-эксперт» voice
- Production maturity, широкое adoption, full weight range 200–800
- Variable font support — single file для all weights, faster load

**Why Onest is the kept backup:**

- Slightly warmer character, wider Cyrillic hooks
- Reserved if Manrope renders сюрприз на field testing (MAX webview / iOS Safari edge cases)
- Drop-in replacement через single CSS var: `--ff-display: 'Onest'` / `--ff-body: 'Onest'`

---

## Logo color experiment outcome

**Verdict: working experiment — NOT canonical primary.**

- Original `logo.svg` = purple `#7D63EF` bot/channel avatar asset (current state, founder-approved для MAX/Telegram avatar only per memory `project_ayla_brand_hybrid_usage`)
- `logo-sage-experiment.svg` = sage `#5A8557` color swap — technically works, contrast preserved, but styling всё ещё marketing-heavy (sparkles, ALL CAPS «AYLA», decorative plate)
- **NOT canonical primary** — still ALL CAPS + sparkles + marketing styling
- **Pending founder final canonical logo** (lowercase + minimal + no sparkles, designer commission)

**Until then, canonical wordmark = typography-based** (lowercase «ayla» в Manrope, sage-500) per `design-tokens.md §12 Wordmark`.

---

## Memory + foundation references

- `project_ayla_brand_hybrid_usage` (2026-05-27 founder verdict — hybrid usage rule)
- `ayla-identity-and-brand.md §3` (sage-green primary palette canon)
- `ayla-identity-and-brand.md §4.4 + §7.1` (lowercase «ayla» wordmark, indeclinable in Russian)
- `ayla-identity-and-brand.md §2.4` (crescent moon over «a» reserved Phase 2+ brand pass)
- `customer-main-wellness-dashboard.md §7 + §8` (Tau sage-green anchor `#7BA478` + WCAG-safe `#5A8557`)
