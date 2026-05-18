# Core User States — 7-State Taxonomy

**Date:** 2026-05-18 r1
**Status:** Foundational — defines how UX adapts to user lifecycle
**Reads:** [`product-ux-vision.md`](./product-ux-vision.md), [`core-wellness-profile.md`](./core-wellness-profile.md)

> Every UX surface must answer: «which user state(s) does this serve, and how does it behave differently per state?»

---

## 0. Why states matter

UX that's the same for all users feels generic. UX that adapts to where the user IS in their journey feels personal. The 7 states define WHERE a user is, so AI + UI can respond appropriately.

### State is not «role» — it's «moment»
- Role = persistent identifier (customer, owner, master)
- State = current life position (just discovered us / regular client / drifting away)

Same user moves through states over time. UX morphs accordingly.

### State drives 5 things
1. **Tone**: new users get explanation; regulars get terseness
2. **Content density**: new users see less; regulars see more
3. **Recommendations**: state determines what's suggested
4. **Frequency**: how often AI proactively reaches
5. **Default home screen**: changes layout per state

---

## 1. The 7 states overview

```
DISCOVERED ─→ EXPLORING ─→ PROBLEM_SEEKING ─→ READY_TO_BOOK ─→ POST_VISIT
                  │                                                  │
                  └─────────────────┐                                 ▼
                                    │                          ACTIVE_REGULAR
                                    │                                 │
                                    │                                 │
                                    │              cycle → return to PROBLEM/READY
                                    │                                 │
                                    │                          drift  ▼
                                    └────────────── AT_RISK_DRIFTING ─┘
                                                          │
                                                          ▼
                                                       DORMANT
                                                       (re-entry on response)
```

Names use **descriptive English** for engineering; user-facing surfaces never reference state directly.

### State summary

| # | State key | Russian label (internal) | Trigger | Default home behavior |
|---|---|---|---|---|
| 1 | `DISCOVERED` | Новый | First bot encounter, no actions | Generic warm greeting + 4 entry buttons |
| 2 | `EXPLORING` | Исследует | Has browsed catalog/masters, no booking yet | Catalog forward + soft «помощь?» |
| 3 | `PROBLEM_SEEKING` | Ищет решение | Stated concern («болит шея», «стресс») | Recommendations forward + service match |
| 4 | `READY_TO_BOOK` | Готов к записи | Clear booking intent | Mini App opens with pre-fill |
| 5 | `POST_VISIT` | После процедуры | Within 7 days post-visit | Care + feedback + retention prep |
| 6 | `ACTIVE_REGULAR` | Постоянный | 3+ visits, cadence established | Continuity-led («как обычно?») |
| 7 | `AT_RISK_DRIFTING` | Дрейфует | Past expected cadence by 1.5× | Care-led check-in, NOT promotional |
|  →| `DORMANT` | Спящий (subset of AT_RISK) | Past cadence by 3×+ | Single reactivation attempt, then silent |

State 6 is the asymptotic «good» — high-LTV, low-effort to retain.
State 7 (with its DORMANT sub-state) is reversible — re-engagement returns user to earlier state.

---

## 2. State 1 — DISCOVERED (Новый)

### Trigger
- First-time arrival via any entry point (deeplink, share, QR, bot search, channel mention)
- No prior conversation, no booking history
- Wellness Profile: only Layer 1 (Identity) minimally populated from MAX initData

### What the user is thinking
- «Что это вообще?»
- «Это бот для записи или что-то ещё?»
- «Стоит ли тратить тут время?»
- Decision window: 30 seconds → 3 messages max

### UX response

#### Tone
Warm, welcoming, low-pressure. Honest about what we are.

#### Greeting template (B1 baseline)
> «Здравствуйте! Я помощник студии Карина. Помогу записаться, расскажу о ценах и услугах. С чего начнём?»

(Per assistant-persona policy — no «бот» word, persona-conformed per tenant.)

#### Default inline keyboard (4 entry options, equal weight)
- 📅 Записаться
- 💅 Услуги и цены
- 👤 Наши мастера
- 📍 Где мы?

Plus optional 5th: ❓ Какой-то вопрос (open-ended → routes to AI free conversation)

#### Frequency policy
- NO proactive messages until user responds to first greeting
- After 7 days no response: ONE follow-up «всё в силе, если что — я тут», then silent
- After 30 days no response: archived (returns to DISCOVERED on any re-entry)

#### Wellness Profile growth
At this state, AI captures:
- Identity Layer: language, timezone (auto), initData fields (verified)
- Behavioral Layer (just starting): preferred_channel = max
- AI Memory short-term: session intent (browse vs book vs question)

### Exit criteria
- → `EXPLORING` if user opens catalog or asks general question
- → `PROBLEM_SEEKING` if user states specific concern
- → `READY_TO_BOOK` if user immediately wants to book (warm via E2 deeplink)
- → Stays `DISCOVERED` if no action

---

## 3. State 2 — EXPLORING (Исследует)

### Trigger
- Has browsed catalog OR masters OR asked informational questions
- No booking initiated yet
- Wellness Profile: Identity + initial Behavioral patterns emerging

### What the user is thinking
- «Что у них есть?»
- «Сколько стоит?»
- «Какие мастера?»
- Curious but not committed

### UX response

#### Tone
Informative, helpful, transparent on pricing. Not pushy.

#### Pattern: low-pressure information
- Show full catalog, full prices, no «скидка-now» pressure
- Show master photos + ratings + specializations
- AI answers factual questions concisely
- Sparingly: «если что-то приглянулось — записывать?» (after 3+ exchanges, not every message)

#### Bot behavior
- No proactive messages in this state (user is exploring, don't interrupt)
- Catalog browse summaries: «Видел вы смотрели маникюр и педикюр — может, комплекс?» (only if visible browsing pattern)

#### Wellness Profile growth
- Service interest hints: «browsed manicure category» → Behavioral pattern data
- Initial Goals inference IF user asks something goal-suggestive («есть ли расслабляющий массаж?» → tentative goal: stress_reduction, confidence 0.5)

### Exit criteria
- → `PROBLEM_SEEKING` if user states concern
- → `READY_TO_BOOK` if explicit booking intent
- → Stays `EXPLORING` indefinitely (some users browse for weeks before deciding — that's fine)
- → `DISCOVERED` (rare regression) if all data cleared

---

## 4. State 3 — PROBLEM_SEEKING (Ищет решение)

### Trigger
- User explicitly mentions concern: «болит шея», «постоянно усталая», «отёчность», «после работы напряжение», «плохо сплю»
- This is the **highest-value entry state** — user reveals what AI can solve

### What the user is thinking
- «Кто мне поможет?»
- «Что мне подходит?»
- «Можно ли решить без визита к врачу?»
- Vulnerable + seeking help

### UX response

#### Tone
Empathetic, expert, careful. NOT preachy or medical-diagnostic.

> «Понимаю. Напряжение в шее часто связано с долгим сидением. У нас есть несколько вариантов — массаж шеи и плечевого пояса, расслабляющий массаж, или лимфодренаж. Что вам сейчас ближе?»

#### Pattern: state acknowledgment first, recommendation second
1. Acknowledge concern (without minimizing)
2. Frame as solvable (without overpromising)
3. Offer 2-3 options (not 7 — paralysis)
4. Ask follow-up to refine («давно беспокоит?» «утром хуже?»)

#### Critical safety bar
- NEVER diagnose («у вас точно растяжение»)
- NEVER prescribe («вам нужен массаж X раз в неделю»)
- ALWAYS route to specialist if medical signals («это уже больше месяца? стоит проконсультироваться с врачом — массаж дополнит, но не заменит»)
- HUMAN_LOCKED tier if user mentions: chronic pain duration >3 months, neurological symptoms, post-injury

#### Wellness Profile growth (rich)
- Body State Layer activated: pain_points, fatigue_level (if mentioned)
- Goals Layer inferred + confirmed: stress_reduction / pain_relief / sleep_improvement
- AI Memory long-term: chronic concern noted (with verification later)

### Exit criteria
- → `READY_TO_BOOK` if user agrees to specific service
- → Stays `PROBLEM_SEEKING` if multi-message dialogue continues
- → HUMAN_LOCKED tier (per ownership-policy) if sensitive medical context

---

## 5. State 4 — READY_TO_BOOK (Готов записаться)

### Trigger
- Explicit booking intent: «хочу записаться», «запишите», «когда свободно», «можно завтра?»
- OR clear from preceding state (PROBLEM_SEEKING + user agreed)

### What the user is thinking
- «Когда подходит?»
- «Сколько займёт?»
- «Подтвердить и пойти дальше»
- Transactional + efficiency-focused

### UX response

#### Tone
Efficient, confident, concise. No upsell. No delay.

> «Открываю мини-приложение — выберите время.»

#### Default behavior
- Bot opens Mini App with maximum pre-fill:
  - Service: from PROBLEM_SEEKING context or recent browse
  - Master: favorite if known, else best-match
  - Date: «завтра» or «на этой неделе»
- Mini App shows date picker first (assume service+master decided)

#### Frequency
- ZERO proactive interruptions during booking flow
- After booking: ONE confirmation message
- Then T-24h, T-2h, T-15min reminders (per customer first-time §6)

#### Wellness Profile growth
- Behavioral Layer: booking_pattern_time, preferred_days update
- Service History Layer: pre_visit_state_snapshot captured

### Exit criteria
- → `POST_VISIT` once visit completes
- → `EXPLORING` (rare) if user cancels without rebooking
- → `READY_TO_BOOK` (loop) if user reschedules

---

## 6. State 5 — POST_VISIT (После процедуры)

### Trigger
- Within 7 days post-visit
- BookingRequest.status = COMPLETED

### What the user is thinking
- «Как я себя чувствую?»
- «Нужно ли что-то делать?»
- «Стоит ли возвращаться?»
- Reflective + open to feedback

### UX response

#### Tone
Caring, attentive, low-pressure. Genuine interest in their experience.

#### Sequence (per customer first-time §B9-B11)
- **T+2h**: aftercare instructions (service-specific from Catalog)
- **T+24h**: feedback ask («оцените, пожалуйста»)
- **T+7d**: light check-in IF rating was ≤4 or no rating

#### Pattern: no upsell in this window
- Aftercare message MUST NOT push next booking aggressively
- «Записать на коррекцию через 2 недели» button OK (low-key, informational)
- NO «у нас новая акция!» messages — wrong tone

#### Wellness Profile growth
- Service History Layer: rating, customer_reaction, post_visit_state_snapshot
- Body State Layer: «как себя чувствуете?» → updates
- AI Memory: response to recovery questions → long-term fact promotion (if recurring pattern)

#### Critical safety
- If user reports concerning effects («покраснение», «боль»): HUMAN_LOCKED tier immediately
- If rating ≤3: HUMAN_LOCKED tier per ownership-policy §7

### Exit criteria
- → `ACTIVE_REGULAR` if this was 3rd+ completed visit with positive pattern
- → `READY_TO_BOOK` (loop) if user books next within window
- → `EXPLORING` if user goes quiet but profile retained
- → `AT_RISK_DRIFTING` if no engagement past expected cadence

---

## 7. State 6 — ACTIVE_REGULAR (Постоянный)

### Trigger
- 3+ completed visits with established cadence pattern
- Wellness Profile: rich Service History + emerging Behavioral patterns + Goals confirmed

### What the user is thinking
- «Это мой салон.»
- «Хочу как обычно.»
- «Если что-то новое — может, попробую.»
- Comfortable, trust earned

### UX response

#### Tone
Terse, familiar, trust-confirming. Skip explanations they've heard.

> «Анна, как обычно лимфодренаж в четверг? Анна свободна 16:00 и 18:30.»

#### Pattern: continuity over novelty
- AI references shared history: «после прошлого массажа у вас улучшался сон»
- One-tap rebooking: «да, как обычно» works
- Birthday + tier celebrations (per loyalty system)
- Selective new-service intro: «появилась новая услуга — думаю, вам подойдёт, потому что [reason]»

#### Frequency
- Higher tolerance for proactive messages (this is highest-trust state)
- Up to 2 proactive per month: cadence reminders, tier celebrations, careful promos
- Still respect frequency caps and opt-outs

#### Wellness Profile growth
- Long-term facts solidify (chronic concerns, preferences, reactions)
- Goals: status updates («снижение стресса — устойчивый прогресс»)
- Recommendations get personal and predictive

### Exit criteria
- → `READY_TO_BOOK` (frequent loop) on each new cycle
- → `AT_RISK_DRIFTING` if cadence breaks 1.5×
- → Stays `ACTIVE_REGULAR` (good)

---

## 8. State 7 — AT_RISK_DRIFTING (Дрейфует)

### Trigger
- Past expected cadence by 1.5× (e.g., expected 21 days, now 32 days)
- Retention Layer churn_risk_score > 0.5
- May overlap with DORMANT (3×+ past cadence)

### What the user is thinking
- Many possibilities — life got busy, lost interest, tried elsewhere, illness, money tight, dissatisfied silently
- The AI does NOT know which — must approach without assumptions

### UX response

#### Tone
Care-led, not sales-led. Curious, gentle, NOT demanding.

> «Анна, последний раз были у нас месяц назад. Если что-то поменялось — расскажете? Я тут, помогу с чем нужно.»

#### Pattern: open the door, don't push through
- ONE reactivation message at 1.5× cadence
- If no response: ONE more at 2× cadence with slightly different framing
- If no response: silent for 30 days
- After 30 days no response → DORMANT sub-state

#### What NOT to do
- ❌ «У нас скидка специально для вас!» — feels desperate + slimy
- ❌ Multiple chasing messages — destroys trust faster than silence
- ❌ Guilt («вы давно не у нас») — anti-care framing

#### Wellness Profile growth
- Retention Layer: re-engagement attempts logged
- AI Memory: «attempted reactivation YYYY-MM-DD, outcome=...»

### DORMANT sub-state behavior
- Past cadence 3× (e.g., 63 days when expected 21)
- ONE final care message: «Скучаем. Если возвращаться надумаете — буду рада помочь. А если совсем не подходит — спасибо, что были.»
- Then SILENT permanently (until user re-initiates)
- Profile retained (Layer 1-10) for personalization on return

### Exit criteria
- → `POST_VISIT` if user re-engages and books
- → `EXPLORING` if user responds but doesn't book
- → `DORMANT` if no response in window
- → `ACTIVE_REGULAR` (rare full recovery)

---

## 9. State transitions — full diagram

```
┌──────────────┐
│ DISCOVERED   │ first-time, no action
└──────┬───────┘
       │ browses / asks
       ▼
┌──────────────┐
│ EXPLORING    │ informational, low-intent
└──┬─────────┬─┘
   │         │ states concern
   │         ▼
   │   ┌─────────────────┐
   │   │ PROBLEM_SEEKING │ revealed need, AI helps
   │   └────────┬────────┘
   │            │ agrees to service
   │            ▼
   │   ┌──────────────┐
   │   │ READY_TO_BOOK│ transactional intent
   │   └──────┬───────┘
   │          │ books
   │          ▼
   │   ┌──────────────┐
   │   │ POST_VISIT   │ within 7d of visit
   │   └─┬──────────┬─┘
   │     │          │ 3+ visits established
   │     │          ▼
   │     │  ┌──────────────────┐
   │     │  │ ACTIVE_REGULAR   │ trusted, terse, recurring
   │     │  └──┬──────────┬────┘
   │     │     │          │ cadence breaks 1.5×
   │     │     ▼          ▼
   │     │ (loops to READY_TO_BOOK each cycle)
   │     │              │
   │     ▼              │
   │  drifts to →       ▼
   │             ┌──────────────────┐
   │             │ AT_RISK_DRIFTING │  care-led reactivation
   │             └──────┬───────────┘
   │                    │ 3× cadence + no response
   │                    ▼
   │             ┌──────────────┐
   │             │ DORMANT      │  one final care msg, then silent
   │             └──────┬───────┘
   │                    │ user re-initiates
   │                    ▼
   │             (back to PROBLEM_SEEKING or READY_TO_BOOK)
   │
   └──> stale → can return any time
```

---

## 10. UX adaptations per state — quick reference

| State | Greeting | Recommendations | Bot tone | Frequency | Mini App home |
|---|---|---|---|---|---|
| 1 DISCOVERED | Full intro | None until response | Warm-formal | Reactive only | Generic 4-button menu |
| 2 EXPLORING | Soft check-in | Catalog browse aids | Informative | Reactive | Catalog forward |
| 3 PROBLEM_SEEKING | n/a (mid-conversation) | 2-3 service matches | Empathetic-expert | Reactive | Symptom-to-service match screen |
| 4 READY_TO_BOOK | n/a | Pre-filled booking | Efficient-confident | Reactive | Date/time picker pre-filled |
| 5 POST_VISIT | n/a | Aftercare + correction booking | Caring-attentive | T+2h, T+24h, T+7d | Care notes + rating prompt |
| 6 ACTIVE_REGULAR | «Анна, как обычно?» | Continuity-driven | Terse-familiar | Up to 2/month proactive | Quick-rebook + state widget |
| 7 AT_RISK_DRIFTING | Care-led check-in | Soft repeat-cycle | Gentle-curious | One reactivation msg | Same as ACTIVE but with «как вы?» banner |
| DORMANT | n/a (silent) | None | Care-final | Single final message | Same; profile preserved |

---

## 11. Implementation notes

### Computing user state

State is **derived**, not stored. Algorithm reads Wellness Profile + recent activity:

```
def compute_state(user):
    profile = user.wellness_profile
    last_visit = profile.layer_4.visits.latest()
    expected_cadence = profile.layer_4.service_preferences.typical_cadence_days

    if not profile.layer_4.visits.exists():
        if profile.layer_8_memory_short.session_intent == "informational":
            return EXPLORING
        if profile.layer_8_memory_short.current_topic == "stating_concern":
            return PROBLEM_SEEKING
        if profile.layer_8_memory_short.current_topic == "booking_intent":
            return READY_TO_BOOK
        return DISCOVERED

    days_since_last = (now() - last_visit.date).days

    if days_since_last < 7:
        return POST_VISIT
    if days_since_last < expected_cadence * 1.5 and profile.layer_4.visits.count() >= 3:
        return ACTIVE_REGULAR
    if days_since_last < expected_cadence * 3:
        return AT_RISK_DRIFTING
    return DORMANT
```

### Re-computation
- On every conversation event (cheap query)
- Cached 5 min per user
- Re-derived on Wellness Profile update

### State transitions are not announced to user
User never sees «вы теперь ACTIVE_REGULAR клиент». State is INTERNAL UX adaptation signal, not a status the user manages.

(Loyalty tier — visible to user — is separate from state. State changes much more often than tier.)

---

## 12. Open questions

| # | Question | Lean | Owner | Urgency |
|---|---|---|---|---|
| Q-US1 | Threshold for ACTIVE_REGULAR — 3 visits OR LTV-based? | 3 visits (simpler, observable) | PM | 🟢 |
| Q-US2 | EXPLORING state with no exit for 30+ days — pull back to DISCOVERED or stay? | Stay (no aggressive re-classification) | PM | 🟢 |
| Q-US3 | Multi-tenant — state per (user, tenant) or per user globally? | Per (user, tenant) — different relationships per salon | PM | 🟡 |
| Q-US4 | DORMANT customer responds after 6 months — state jumps directly to ACTIVE_REGULAR? | NO — back through PROBLEM_SEEKING or READY_TO_BOOK with «давно не виделись» framing | PM | 🟢 |
| Q-US5 | State transitions should fire events that engine acts on? | YES — Event System (next doc) defines state-transition events | PM | 🟡 |

---

## Cross-document linkage

- Foundational: [`product-ux-vision.md`](./product-ux-vision.md)
- Data model: [`core-wellness-profile.md`](./core-wellness-profile.md)
- Voice per state: [`assistant-persona.md`](./assistant-persona.md)
- Ownership policy: [`conversation-ownership-policy.md`](./conversation-ownership-policy.md) (HUMAN_LOCKED gating in PROBLEM_SEEKING / POST_VISIT)
- Next: [`user-journeys.md`](./user-journeys.md) — paths between these states
- Event System (forthcoming): state transitions as events that drive UX

## Last verified
2026-05-18 (founder roadmap step 2)
