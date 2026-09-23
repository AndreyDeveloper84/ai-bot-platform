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

## Runtime env vars

- `VITE_SUPPORT_DEEPLINK` — support channel URL used by the profile
  privacy sheets (#949). Falls back to the pilot placeholder
  `https://max.me/aylasupport` when unset. Set it at deploy time once
  ops decides the real support channel handle.

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

## What this scaffold does NOT do

- No state mgmt library (Zustand / Redux) — Phase 1 decides when needed
- No unit-test setup yet (Vitest lands when first non-trivial logic
  goes in components — booking flow Phase 1)
- No i18n — Russian-only MVP per handoff §17 edge case
- No service worker / offline cache — Phase 5 polishes offline states

## Тесты: полный прогон мигает под нагрузкой (DRF-2377)

**Практика: упавший узел полного прогона перепроверяется в одиночку, и в тело
PR пишутся ОБА результата.** Одиночное красное на полном прогоне поломку не
доказывает — измерено ниже. Без этой перепроверки исполнитель тратит заход на
чужую тень; за сутки 23–24.09 это случилось трижды.

### Что измерено (24.09, два полных прогона, 205 файлов, 2049 узлов)

Срок в `vite.config.ts` не задан — значит умолчание vitest, **5 с на узел**.

| | покой | 4 занятых ядра |
|---|---|---|
| падений | 0 | 0 |
| замедление узлов | — | медиана ×1,5, худшее ×9,7 |

Набор здоровый: **1800 узлов быстрее 0,5 с**, дольше 2 с — девять.

Семь файлов, которые мигали у двух окон, делятся по **запасу до срока**:

| файл | самый долгий узел в покое | во сколько раз до 5 с |
|---|---|---|
| `MasterAylaScreen` | 4,54 с | ×1,1 |
| `admin/SalonPilotAylaScreen` | 2,69 с | ×1,9 |
| `GoalSelectScreen` | 2,27 с | ×2,2 |
| `CustomerWellnessDashboardScreen` | 1,86 с | ×2,7 |
| `App.soloTrio2127` | **0,72 с** | ×7 |
| `App.salonPilotShell` | **0,59 с** | ×8,5 |
| `ServiceDetailScreen.price` | **0,63 с** | ×7,9 |

Первые четыре объясняются нагрузкой: при медиане ×1,5 они у черты. **Последние
три — нет**: провал в восемь раз это не замедление, а остановка воркера
(сборка мусора, вытеснение памяти, простой процесса). Их предмет — DRF-2382,
и **срок им намеренно не поднят**: это спрятало бы то, что ещё не понято.

### Почему срок поднят поимённо, а не глобально

Глобальные 5 с остаются для 2040 узлов: срок ловит и настоящие зависания, и
общее послабление их спрятало бы. Свой срок получили **только измеренные**
узлы — 15 с, то же число, что уже стоит у `CustomerProfileScreen.test.tsx`
(а у `lib/dev-init-data.build.test.ts` — 240 с: сборка боевого пакета). Форма
домашняя — третий аргумент `it(…, 15_000)`.

Числа выше записаны здесь именно затем, чтобы через месяц было видно, **откуда
взялся срок**: он не «на глаз», а от измеренного времени с запасом ×3…×8.

### Долг, который мы этим НЕ закрыли

**Узел, который в покое идёт 4,5 секунды, подозрителен сам по себе.** Поднятый
срок лечит мигание, а не причину медленности. «Он медленный, поэтому дадим ему
больше времени» не должно однажды стать ответом на вопрос «почему тесты идут
полчаса»: сегодня девять узлов дольше 2 с из 2049, и это число стоит держать
на глазах.

## Cross-references

- Backend contract: `apps/miniapp_api/views.py`
- Auth spec: `apps/miniapp_api/auth.py`
- UX source of truth: `docs/design/2026-05-18-customer-first-time-handoff.md`
- MAX platform reference:
  `~/.claude/skills/ux-architect/references/platforms/max-mini-apps.md`
