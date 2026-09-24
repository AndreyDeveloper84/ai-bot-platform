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

## Узел обязан уметь краснеть — и это же ловит правку не того файла (DRF-2367)

**Практика: узел, написанный на уже работающий код, проверяется подменой.**
Сломать в коде ровно то свойство, которое узел держит, прогнать, восстановить.
Красным должен стать **именно этот** узел. Отдельно проверить **улучшающую**
правку: на ней узел обязан остаться зелёным — иначе он стережёт деталь
реализации, а не предмет.

Зачем, на живом примере: в DRF-2367 первая редакция узлов на два маршрута
кабинета проходила **все три** настоящие поломки — метку, уехавшую на чужую
строку; полностью выброшенный ответ сервера; жёсткий `status=all` вместо
выбранного фильтра. Все три были зелёными. Причины повторимы:

- принадлежность внутри элемента проверялась **на уровне страницы** — строка
  есть, а у кого, неизвестно;
- контрольное значение совпало с **запасной подписью экрана**, и «показали
  ответ сервера» стало неотличимо от «напечатали заготовку»;
- положительное утверждение о запросе удовлетворялось **соседним** вызовом той
  же ручки — закрылось только отрицательным («ни одного захода за „всеми“»).

### Побочное свойство, ради которого это стоит делать всегда

Подмены ловят не только слабые узлы, но и **подменённый артефакт**: правишь не
тот файл, а прогон зелёный. В том же листе оборвавшаяся команда не записала
второй файл узлов; число строк совпало, прогон был зелёный, и старая редакция
уехала бы в PR под видом исправленной. Поймалось тем, что подмены покраснели
**не там, где должны**.

Ничем другим этот класс ошибки не ловится: ни прогон, ни типы, ни линтер не
знают, какую версию файла вы собирались проверить.

### Как держать

Подмены — скриптом (ставит → прогоняет → восстанавливает, печатает
ждали/получили), а не руками: руками забывается восстановление. Контрольное
значение брать такое, которое код выдумать не может.

## Линтер: три правила, все ошибкой (DRF-2388, DRF-2391)

`npm run lint` — обязательный шаг CI. Правила: `react-hooks/rules-of-hooks`,
`react-hooks/exhaustive-deps`, `no-console` с разрешёнными `warn`/`error`, плюс
`eslint-comments/require-description`. Причины каждого решения — в
`eslint.config.mjs`; там же именованный список файлов с долгом, который **может
только сокращаться**.

До 24.09 конфигурации ESLint здесь не было вовсе, и замер при включении дал
число, которое стоит помнить: по двум правилам, у которых подавления были,
**нашлось 46 мест против 26 подавлений** (`exhaustive-deps` 21 против 15,
`no-console` 25 против 11). Команда подавляла то, что случайно заметила, —
двадцать мест не видел никто. Это измеряет не «правило некому проверить», а
**«проверка мнилась там, где её не было»**.

У `rules-of-hooks` подавлений не было вовсе: его 8 нарушений в 3 файлах никто не
прятал, потому что никто их не видел. Два места починены тогда же (DRF-2388),
шесть остались в именованном списке долга.

### Подавление без причины запрещено машинно

Форма: `// eslint-disable-next-line <правило> -- <причина>`. Проверяет
`require-description`, а не договорённость: договорённость держится до первого
спешащего. Причина нужна потому, что из 21 находки `exhaustive-deps`
шестнадцать безвредны по замыслу, а в одной **совет правила сломал бы замысел** —
и следующий человек должен видеть «почему», а не только «что». Остальные четыре
находки — настоящие предметы на трёх листах (DRF-2394, DRF-2395, DRF-2396).

Директив 20, а находок 21: в `AdminDeactivationFlowScreen` одна строка даёт две
находки, и обе покрыты одним подавлением.

### Мёртвая директива — ошибка

`reportUnusedDisableDirectives: "error"`. Это и был предмет DRF-2391: 26
подавлений, не действовавших ни дня. Теперь новая мёртвая директива не появится
молча.

## Зависимости: lock генерировать npm 10, как в CI

CI ставит node из `.nvmrc` (**22**, то есть npm **10**), а локально здесь может
стоять node 24 с npm **11**. **Разные мажоры npm пишут разный
`package-lock.json`.** Установка npm 11 на Windows выбрасывает из lock записи,
нужные Linux (`@emnapi/runtime`, `esbuild`, пакеты `@esbuild/*`), и `npm ci` в
CI падает с `EUSAGE … Missing: … from lock file` — задание умирает **на
установке**, до линтера, типов и узлов.

Добавляя зависимость:

```bash
git checkout origin/dev -- package-lock.json
npx --yes npm@10 install --package-lock-only --save-dev --save-exact <пакеты>
npx --yes npm@10 ci --dry-run      # та же ошибка, что в CI, но node_modules цел
```

**Почему `--dry-run`, а не `npm ci`:** проверять надо ту версию, которой
проверяет CI, и на **чистом** дереве. Локальный `npm run lint` в уже собранном
`node_modules` был зелёным, когда lock был сломан: проверка пакетного менеджера
без чистой установки не проверяет пакетный менеджер. `--dry-run` даёт ту же
ошибку, не снося `node_modules`, которым в этот момент может пользоваться
чей-то прогон.

## Cross-references

- Backend contract: `apps/miniapp_api/views.py`
- Auth spec: `apps/miniapp_api/auth.py`
- UX source of truth: `docs/design/2026-05-18-customer-first-time-handoff.md`
- MAX platform reference:
  `~/.claude/skills/ux-architect/references/platforms/max-mini-apps.md`
