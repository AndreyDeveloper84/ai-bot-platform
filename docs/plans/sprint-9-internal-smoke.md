# Sprint 9 Internal Smoke Plan

> Sprint 9 / Q1 (DRF-828). Manual end-to-end smoke for the 7 ported skills.

## Goal

Walk every skill's happy path + the most-important failure path through
a real Telegram dev-bot session against Ayla staging
(`dev.gobeauty.site`). Catch integration-layer issues that unit tests
miss — wire-up between skill / I1 client / channel adapter / Ayla
backend.

## Harness

* **Dev tenant** — `tenant=sprint-9-smoke` in local Django shell, with
  Telegram bot token in `MAX_BOT_TOKEN`, `MAX_BOT_MODE=polling`.
* **Ayla** — staging via `AYLA_BASE_URL=https://dev.gobeauty.site` +
  `AYLA_SERVICE_TOKEN` from 1Password.
* **Local run** — `make run` + `make worker` + `make bot-polling`
  (the last command launches the dev-bot in long-polling mode against
  the staging tenant).
* **Time budget** — 1.5–2 hours for the full pass, on the assumption
  that no scenario reveals a P1 blocker. P1 blockers stop the smoke
  and become Sprint 9 hotfix PRs.

Result log goes to `docs/qa/sprint-9-smoke-results.md` — per scenario
pass/fail, observed text/screenshot, ticket if a regression.

## Scenarios

Each scenario has a **Trigger** (what to type/click in the dev-bot)
and an **Expected** outcome. P1 = blocker, P2 = ship-fix-after.

### food_clarify (P4, DRF-358 fix)

| # | Trigger | Expected | Severity if broken |
|---|---|---|---|
| 1 | `Борщ 300г` | 2-button card «📔 В дневник» / «❌ Опечатка» | P1 |
| 2 | `Сок 0,5л` | Same card | P1 (DRF-358 regression) |
| 3 | Click «В дневник» on (1)'s card | "Скинь фото блюда…" | P1 |
| 4 | Click «Опечатка» on (1)'s card | "Поняла 🙂" silent ack | P2 |
| 5 | `Здравствуйте` (control) | NOT a food card — FAQ skill takes turn | P1 |

### water (P2)

| # | Trigger | Expected | Severity if broken |
|---|---|---|---|
| 6 | `стакан воды` | "Записала 250 мл" + daily total | P1 |
| 7 | `чашка кофе` | Records 200 ml; reply mentions water-coefficient (`~190 мл в счёт воды`) | P2 |
| 8 | `бокал вина` | Records, water_ml=0, "может стакан воды?" hint | P2 |
| 9 | `0,5 л воды` | DRF-358 decimal-comma; records 500 ml | P1 |
| 10 | Ayla offline (kill staging) | Graceful "попробуй через минуту" | P1 |

### food_scanner + food_correction (P1 + P5)

| # | Trigger | Expected | Severity if broken |
|---|---|---|---|
| 11 | Send photo of a meal | Recognition card with dish + macros + 3 buttons | P1 |
| 12 | Click ✅ В дневник | "Записала: <dish> — N ккал" | P1 |
| 13 | Click ✏️ Уточнить on (11) | P5 prompt "Что не так? Грамм / Название / Макросы" | P1 |
| 14 | Send blurry / non-food photo | Friendly "не разобралась, переснять" | P2 |
| 15 | Click ❌ Не то | "Поняла, не записываю…" ack | P2 |

### health_screening (P7, DRF-358 T04)

| # | Trigger | Expected | Severity if broken |
|---|---|---|---|
| 16 | `Болит спина` | Diagnostic question, NOT a service list | P1 |
| 17 | `Болит шея, отдаёт в руку` | Red-flag redirect to doctor | P1 |
| 18 | `Онемение в ноге` | Red-flag redirect | P1 |
| 19 | `Плечи тянет к вечеру` | Soft pain — diagnostic question | P2 |

### nutrition_anketa (P3)

| # | Trigger | Expected | Severity if broken |
|---|---|---|---|
| 20 | `/anketa` | Gender step with 2-button keyboard | P1 |
| 21 | Click «Женский» | Age prompt (text input) | P1 |
| 22 | Type `28` | Height prompt | P1 |
| 23 | Type `150` (out of range, mid-anketa) | Re-asks age with range hint | P2 |
| 24 | Continue to goal, click «Поддержать» | Norms summary card | P1 |
| 25 | Restart bot mid-anketa → type next answer | Resume from saved step | P2 |

### cross_domain (P6)

| # | Trigger | Expected | Severity if broken |
|---|---|---|---|
| 26 | Manually emit insight via Django shell → click "Не интересно" | Ayla `dismiss` POST + soft ack | P2 |
| 27 | Click "Записаться" on insight | "Передаю менеджеру…" reply | P2 |
| 28 | Click "Я увидел" (telemetry) | Silent — no user-facing reply | P2 |

### Cross-skill integration

| # | Trigger | Expected | Severity if broken |
|---|---|---|---|
| 29 | Send photo → click ✏️ Уточнить → type "250 г" | Currently: AI/echo takes the next turn (P5 apply path is Phase 1) | NOT tested |
| 30 | `/anketa` mid-flow + send food photo | Photo flow takes turn (food_scanner above echo) — anketa state preserved | P2 |

## Exit criteria

* All P1 scenarios pass.
* P2 scenarios either pass OR have a Sprint 10 ticket filed.
* Result log committed at `docs/qa/sprint-9-smoke-results.md`.

## Known limitations

* No automated smoke runner — these scenarios require a real Telegram
  client. The Q2 golden fixtures cover the structural shape; this
  smoke is the integration sanity check.
* food_scanner photo flow requires the channel adapter to stash
  `last_photo_bytes` on the conversation row. The MAX adapter handles
  this path; the Telegram channel adapter is Phase 1 (DRF-848).
* food_correction "apply" path (user types correction value) is
  Sprint 9 cut to prompt-only — the apply leg lands in Phase 1
  (DRF-822 follow-up).
