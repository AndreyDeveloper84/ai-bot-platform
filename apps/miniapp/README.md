# Customer MAX Mini App — frontend

MAX Mini App webview consumed by salon customers. Phase 0c scaffold —
shell only, no business screens. Talks to `/api/v1/customer/*`
(Phase 0b backend in `apps/miniapp_api/`).

## Stack

- Vite 5 + React 18 + TypeScript strict
- React Router 6 (BrowserRouter)
- CSS variables design tokens (no Tailwind, no CSS-in-JS)
- MAX UI React library — **not yet** added (Phase 1); adapter layer
  in `src/lib/max-sdk.ts` is decoupled from the eventual component lib

## Layout

```
src/
├── main.tsx             Vite entry — mounts BrowserRouter + App
├── App.tsx              Route table
├── vite-env.d.ts        Vite + import.meta typings
├── styles/
│   ├── tokens.css       salon-warmth palette, radii, spacing (DEFAULTS)
│   └── globals.css      reset + .screen + .cta-bar
├── lib/
│   ├── max-sdk.ts       window.WebApp adapter (no-op outside MAX)
│   └── api.ts           fetch client w/ Authorization: MaxInitData
├── hooks/
│   ├── useBackButton.ts        wires MAX BackButton to router
│   ├── useHaptics.ts           selection/notify/impact callbacks
│   └── useClosingConfirmation  enableClosingConfirmation toggle
├── components/
│   ├── ScreenLayout.tsx        title + body + slot for sticky CTA
│   └── StickyCta.tsx           replaces MAX MainButton
└── screens/
    └── HelloScreen.tsx         auth round-trip smoke test
```

## Run locally

```pwsh
cd apps/miniapp
npm install
npm run dev
```

Vite serves on `http://localhost:5173`, proxies `/api/v1/customer/*` to
the Django backend on `:8000`. Start the backend with `python manage.py
runserver 0.0.0.0:8000`.

### Browser-dev without MAX (auth round-trip)

The MAX bridge isn't loaded outside the MAX app, so `WebApp.initData`
is empty. To still exercise the auth flow:

1. Generate a signed `initData` string for a test BotUser. Python
   one-liner using the same algo Django verifies:

   ```python
   import hashlib, hmac, json, time, urllib.parse
   token = "test-bot-token-xyz"  # = settings.MAX_BOT_TOKEN
   params = {
       "user": json.dumps({"id": 12345, "first_name": "Мария"}),
       "auth_date": str(int(time.time())),
   }
   dcs = "\n".join(f"{k}={params[k]}" for k in sorted(params))
   secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
   params["hash"] = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
   print(urllib.parse.urlencode(params))
   ```

2. Drop into `.env.local`:

   ```
   VITE_DEV_INIT_DATA=user=...&auth_date=...&hash=...
   ```

3. Restart `npm run dev`. The `getInitData()` helper falls back to this
   value when `window.WebApp` is absent.

## What's deferred (swap later in one PR)

| Defaulted today | Swap to |
|---|---|
| `--c-*` HEX in `src/styles/tokens.css` | Brand palette HEX |
| No MAX UI lib import | Add `@<scope>/ui-react` + replace ad-hoc CSS components |
| `<script src="https://dev.max.ru/sdk/web-app.js">` in `index.html` | Canonical MAX bridge URL |

The adapter in `src/lib/max-sdk.ts` is intentionally narrow — only the
methods we'll actually use. Extend as Phase 1+ screens need more (e.g.
`requestScreenMaxBrightness` for the success QR, `shareMaxContent` for
"send to a friend").

## Build

```pwsh
npm run build       # outputs dist/, sourcemaps on
npm run preview     # serve dist/ on :4173 for smoke
npm run typecheck   # tsc --noEmit
```

Production builds require `VITE_SUPPORT_DEEPLINK` — the real MAX
support channel URL rendered by the Profile support-entry sheets
(152-ФЗ export/delete + notification prefs). The build fails fast
without it (guard in `vite.config.ts`, #949); dev mode falls back to a
placeholder. See `docs/runbooks/server-deployment.md` §2.6.

## What this scaffold does NOT do

- No state mgmt library (Zustand / Redux) — Phase 1 decides when needed
- No unit-test setup yet (Vitest lands when first non-trivial logic
  goes in components — booking flow Phase 1)
- No i18n — Russian-only MVP per handoff §17 edge case
- No service worker / offline cache — Phase 5 polishes offline states

## Cross-references

- Backend contract: `apps/miniapp_api/views.py`
- Auth spec: `apps/miniapp_api/auth.py`
- UX source of truth: `docs/design/2026-05-18-customer-first-time-handoff.md`
- MAX platform reference:
  `~/.claude/skills/ux-architect/references/platforms/max-mini-apps.md`
