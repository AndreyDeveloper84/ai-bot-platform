# AYLA — brand asset pack

Per founder verdict 2026-05-27 (memory `project_ayla_brand_hybrid_usage`), Ayla brand uses **hybrid asset distribution**:

| Asset | Where canonical | Where forbidden |
|---|---|---|
| **Typography-based lowercase «ayla»** (sage-500 Manrope) — see `design-tokens.md §12` | Mini App headers, customer dashboard, in-app branding, push sender label, customer settings, design system primary | — |
| **Purple AYLA pack** (this folder, `logo-*.png` — 42 raster variants + `../logo/logo.svg` master) | MAX bot avatar, Telegram bot avatar, channel chat-list logo, temporary bot-channel identity | Mini App UI · customer dashboard · design tokens primary palette · customer-facing in-app headers · primary app branding |

---

## Folder contents

```
docs/design/assets/
├── AYLA/                              ← this folder (purple bot avatar pack)
│   ├── README.md                      ← this file
│   ├── logo.png                       ← master raster (1 of 42 variants)
│   ├── logo-1.png ... logo-41.png     ← 41 size/treatment variants
│   └── EXPERIMENTS/
│       ├── README.md                  ← typography + logo experiment outcomes
│       ├── typography-manrope-preview.html
│       ├── logo-sage-experiment.svg   ← purple → sage color swap (working iteration)
│       └── logo-sage-render.html      ← side-by-side preview
│
└── logo/
    ├── logo.svg                       ← master vector source (purple #7D63EF)
    └── logo.png                       ← master raster preview
```

---

## Usage rules

### ✅ Purple AYLA pack — allowed surfaces

- **MAX bot avatar** — primary use case, ~256×256+ chat tile rendering
- **Telegram bot avatar** — same
- **Channel chat-list logo** — small-size identity
- **Temporary bot-channel identity** — until founder commissions final canonical logo

Reasons purple wins at chat-list scale:
- Strong contrast at small sizes (~64–256dp)
- Visible против both light and dark chat themes
- Distinguishable from system sage-green palette (avoids "where does platform end and bot begin")

### ❌ Purple AYLA — forbidden surfaces

- ❌ Mini App UI (any surface customer sees inside the app)
- ❌ Customer dashboard (main wellness home)
- ❌ Design tokens primary palette
- ❌ Customer-facing in-app headers / nav / settings
- ❌ Primary app branding (favicon, splash, App Store icon — these use canonical typography wordmark or pending designer asset)
- ❌ Same surface as sage-green app chrome (mix prohibition — see below)

### ⚠ Mix prohibition (founder explicit, 2026-05-27)

> «Do not mix purple bot branding and sage-green app chrome в same app surface unless separately approved.»

**Purple = channel identity. Sage-green = app UI identity.** Each lives in its own context, never side-by-side in a single rendered viewport (except: chat-list где MAX itself shows bot avatar adjacent to user's other chats — that's not Ayla mixing colors, that's MAX rendering chat list).

---

## Final canonical logo — pending

Founder will commission a final canonical brand logo:

- **Lowercase** «ayla» (per identity §2.1 indeclinable proper noun + §4.4 lowercase wordmark)
- **Minimal** — no sparkles, no decorative plate
- **No marketing styling** — no gradient, no overlays
- Possibly с crescent moon ☽ over «a» per identity §2.4 etymology + §4.4 reserved Phase 2+
- Sage-green primary OR commissioned designer palette decision

**Until then:** use typography-based wordmark per `design-tokens.md §12`. Sage color experiment (`EXPERIMENTS/logo-sage-experiment.svg`) — *not* a placeholder for the final asset, just an exploration artefact.

---

## Memory + foundation references

- `project_ayla_brand_hybrid_usage` (2026-05-27 founder canonical hybrid rule)
- `ayla-identity-and-brand.md` (§2.1, §2.4, §4.4, §7.1)
- `customer-main-wellness-dashboard.md §7` (sage-green anchor + lowercase wordmark direction)
- Sigma `design-tokens.md` §12 (canonical wordmark) + §13 (bot/channel logo assets section)
