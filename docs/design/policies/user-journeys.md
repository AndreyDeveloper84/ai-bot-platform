# User Journeys — 3 Foundational Paths

**Date:** 2026-05-18 r1
**Status:** Foundational — defines paths user takes through states
**Reads:** [`product-ux-vision.md`](./product-ux-vision.md), [`core-user-states.md`](./core-user-states.md), [`core-wellness-profile.md`](./core-wellness-profile.md)

> Each journey traces a path through user states with concrete AI behaviors. New features should fit one of these journeys or articulate why a new journey is needed.

---

## 0. Why 3 journeys

Most product success in beauty/wellness comes from 3 customer paths well-served:

1. **Problem-Seeking** — «у меня болит / я хочу решить → AI помогает → результат» (the WELLNESS path)
2. **Quick Rebook** — «знаю что хочу → AI отвечает быстро → запись» (the LOYAL path)
3. **AI Reactivation** — «давно не виделись → AI бережно возвращает → запись» (the RETENTION path)

All other flows are variants or subsets. Designing these 3 well delivers ~80% of customer value.

---

## 1. Journey 1 — Problem-Seeking (the WELLNESS path)

### Strategic significance
**This is the highest-value journey.** Customer reveals a real need; AI helps with care; trust is built; loyalty seeded. Without this journey, we are a chat-bot. With it, we are a wellness companion.

### Customer JTBD
> «У меня есть проблема (физическая, эмоциональная, эстетическая), и я хочу понять — что мне поможет, кому записаться, и что после делать.»

### Starting state
`PROBLEM_SEEKING` (often from `EXPLORING` or `DISCOVERED`)

### Ending state
`POST_VISIT` (success) → eventually `ACTIVE_REGULAR`

### Touchpoint sequence

#### Step 1 — Concern stated (entry)
**Trigger:** Customer types «болит шея» / «постоянно усталая» / «не сплю» / «отёчность»

**AI behavior:**
- Acknowledge specifically (not generic «понимаю»):
  > «Понимаю — напряжение в шее часто связано с долгим сидением.»
- Brief context-question (not interrogation):
  > «Давно беспокоит? Или ситуативно — после рабочего дня?»
- No service push yet

**Profile updates:**
- Body State Layer: pain_point=«шея»
- AI Memory short-term: current_topic=«neck_pain», intent=«problem_seeking»

#### Step 2 — Context refined
**Trigger:** Customer adds detail («с понедельника после совещаний»)

**AI behavior:**
- Validate observation (don't diagnose):
  > «Это типично — мышцы устают от статичной позы.»
- Offer 2-3 service options (NEVER 7):
  > «У нас обычно помогают: массаж шеи и плечевого пояса (60 мин), расслабляющий массаж всего тела (90 мин), или лимфодренаж (если есть отёчность). Что ближе?»
- Frame as options, not prescription

**Profile updates:**
- Body State Layer: chronicity flag, context («work-related»)
- Goals Layer (inferred): stress_reduction + pain_relief

#### Step 3 — Service chosen
**Trigger:** Customer picks one («массаж шеи и плеч»)

**AI behavior:**
- Confirm choice + introduce master:
  > «Хорошо. Анна делает этот массаж — у неё стаж 7 лет, особенно с напряжением в шее. Свободна в четверг 16:00 или пятницу 11:00.»
- Single Mini App button: «Открыть запись →»
- Pre-filled: service, master, suggested times

**Profile updates:**
- Behavioral Layer: time preference data
- AI Memory: recommended service confirmed

#### Step 4 — Booking completed
**Trigger:** User confirms in Mini App

**AI behavior:**
- Concise confirmation + prep note:
  > «Записала. Четверг 16:00, Анна, массаж шеи и плеч. Анна посоветовала — сделайте короткую растяжку перед визитом, поможет.»
- Single confirmation message, no upsell

**Profile updates:**
- Service History Layer: visit pending entry created
- pre_visit_state_snapshot captured (current Body State)

#### Step 5 — Reminders sequence (per customer first-time §B5-B7)
T-24h, T-2h, T-15min — care-framed, not transactional.

#### Step 6 — Post-visit care (per customer first-time §B9)
**T+2h after end:**
> «Спасибо, что были у нас! После массажа шеи:
> • Первые сутки избегайте резких движений
> • Тёплый душ вечером, не горячий
> • Стакан тёплой воды сейчас полезен
>
> Если завтра почувствуете заметное улучшение — буду рада узнать.»

**Critical UX moment:** AI asks for feedback ABOUT THE WELLNESS OUTCOME, not just the visit quality.

#### Step 7 — Feedback + Wellness validation
**T+24h:**
> «Анна, как себя чувствуете? Шея стала легче?»

Inline keyboard:
- 😌 Гораздо лучше
- 🙂 Чуть лучше
- 😐 Так же
- 😕 Не помогло

**This question is critical:** it validates that the AI's recommendation worked. Each answer trains the system.

**Profile updates:**
- Service History Layer: customer_reaction.felt_better, noted_effects
- Body State Layer: post_visit_state_snapshot

#### Step 8 — Pattern noted
After 2-3 similar Problem-Seeking journeys with positive outcomes:
**AI begins suggesting cadence:**
> «Заметила — после массажа шеи у вас стабильно лучше неделю. Может, повторим через 3 недели?»

This transitions user toward `ACTIVE_REGULAR` with a confirmed wellness benefit.

### Success metrics for Journey 1
- **Recommendation acceptance rate**: customer agrees to suggested service ≥ 70%
- **Outcome positivity**: «гораздо лучше» or «чуть лучше» ≥ 75%
- **Cadence development**: 2nd visit within natural interval ≥ 50%
- **Trust signal**: customer mentions another concern unprompted (revealing more) within 60 days ≥ 30%

### Anti-patterns to prevent
- ❌ AI suggests 5 services at once → paralysis
- ❌ AI diagnoses («у вас точно растяжение мышц») → liability + wrong scope
- ❌ AI pushes promo during problem-seeking («есть скидка!») → wrong tone
- ❌ AI doesn't follow up post-visit → care illusion broken

### Failure modes
- Customer doesn't respond after Step 3 → state regresses to EXPLORING
- Customer says «не помогло» on feedback → HUMAN_LOCKED tier for follow-up
- Customer mentions medical red flags (>3 mo chronic, neurological signs) → HUMAN_LOCKED + suggest medical specialist

---

## 2. Journey 2 — Quick Rebook (the LOYAL path)

### Strategic significance
This is the **efficiency journey**. Regulars want to spend NO mental energy on routine rebooking. Every extra step here erodes loyalty. If we can't do this well, we're worse than a calendar app.

### Customer JTBD
> «Хочу записаться как обычно. Не хочу выбирать из 20 вариантов.»

### Starting state
`ACTIVE_REGULAR` (or `READY_TO_BOOK` if from cadence reminder)

### Ending state
`READY_TO_BOOK` → `POST_VISIT` (loop)

### Touchpoint sequence

#### Step 1 — Trigger
**Either:**
- (A) AI proactively at expected cadence (Layer 5/10):
  > «Анна, прошло 3 недели — обычное время. Анна свободна в четверг 16:00 или пятницу 11:00 (как обычно).»
- (B) Customer initiates:
  > «Запишите как обычно»

#### Step 2 — One-tap confirmation
**AI behavior:**
- Pre-suggests default time based on preferred_days + preferred_window
- Inline keyboard:
  - ✓ Да, четверг 16:00
  - ◯ Пятница 11:00
  - ◯ Другое время
- ONE tap completes the booking

#### Step 3 — Concise confirmation
> «Записано. Четверг 16:00, Анна.»

That's it. No upsell. No prep notes (regular customer knows). No additional buttons unless asked.

#### Step 4 — Standard reminders + post-visit
Same as Journey 1 from Step 5 onward, but tone shifts to terse-familiar (per ACTIVE_REGULAR state):
- T-24h: «Завтра 16:00, Анна. Жду.»
- T-2h: «Через 2 часа.»
- Post-visit: care notes may be skipped if customer has rated this service positively 3+ times («вы знаете режим»)

### Success metrics for Journey 2
- **Time-to-rebook median**: < 30 seconds from trigger
- **One-tap completion rate**: ≥ 80% (rest pick other time)
- **Cadence respect**: customer rebook within expected window ≥ 65%
- **Loyalty signal**: customers in this journey have 3× higher 12-month LTV than non

### Anti-patterns to prevent
- ❌ Asking redundant questions («какая услуга?») — we KNOW
- ❌ Showing full catalog — wrong audience
- ❌ Upsell during rebook («хотите попробовать ещё лимфодренаж?») — wrong moment
- ❌ Multiple confirmation steps — friction

### Variants
- **Same service, different master** (favorite master busy): «Анна занята в обычное время. Попробуете у Олега — он специализируется на тех же техниках?» — choice given, not forced
- **Loyalty redemption**: if balance ≥ 50 points, offer at confirmation:
  > «У вас 234 балла — применить максимум?»

### Trust check
Customer should feel «AI знает меня и экономит моё время». NOT «AI настаивает на повторе».

---

## 3. Journey 3 — AI Reactivation (the RETENTION path)

### Strategic significance
**This is the moat journey.** Salons lose 40-60% of customers within 12 months. AI that brings drifting customers back without feeling spammy = retention superpower. This is what makes the wellness OS sticky for both customer and salon.

### Customer JTBD
> Honestly — they don't have an active JTBD. They drifted. The AI must invite them back gently, give them a reason, and respect their right to say no.

### Starting state
`AT_RISK_DRIFTING` (or `DORMANT` for harder cases)

### Ending state
`POST_VISIT` (success) OR `DORMANT` (silent failure — acceptable)

### Touchpoint sequence

#### Step 1 — Attempt 1, light care-led check-in
**Trigger:** Past expected cadence by 1.5× (e.g., 32 days when typical is 21)

**AI behavior:**
- Care-led, not sales-led:
  > «Анна, последний раз были у нас месяц назад. Если что-то поменялось — расскажите. Я тут, если что нужно.»
- NO discount mention
- NO «срочно» framing
- Open-ended invitation

#### Step 2 — Branch based on response

##### Branch A — User responds positively
> «Привет! Да, занята была, не до этого.»

**AI behavior:**
- Empathy + soft re-engagement:
  > «Понимаю. Если будет момент — Анна свободна в эти выходные. Без давления.»
- No further proactive push for 2 weeks

##### Branch B — User responds with concern
> «Если честно, не очень понравился прошлый массаж.»

**AI behavior:** IMMEDIATE HUMAN_LOCKED escalation (per ownership-policy)
> «Спасибо, что сказали. Передам руководителю салона — она свяжется с вами в течение часа, чтобы разобраться.»

##### Branch C — User silent
Wait 7-10 days. Move to Step 3.

#### Step 3 — Attempt 2 (if Branch C from Step 2)
**Trigger:** No response within 10 days after Attempt 1

**AI behavior:**
- Slightly different framing, still care-led:
  > «Анна, не уходим в спам. Если хотите вернуться — буду рада. А нет — нет, всё ок.»
- Optional: light value reminder («у Анны новый курс по работе с напряжением») — if relevant to past pattern
- Single message, no buttons except «Записаться» (low-pressure)

#### Step 4 — Branch based on Attempt 2

##### Branch A — Response → return to Journey 1 or 2
Customer engages → state moves to `PROBLEM_SEEKING` or `READY_TO_BOOK`

##### Branch B — No response within 14 days → DORMANT
**Final care message:**
> «Скучаем. Если возвращаться надумаете — рада помочь. А если совсем не подходит — спасибо, что были.»

Then **SILENT permanently** (until user re-initiates).

#### Step 5 — DORMANT respect
- Wellness Profile retained in full (anonymization happens per Layer retention)
- No further proactive messages
- If user returns (months later, any reason) → AI greets warmly, treats as PROBLEM_SEEKING or READY_TO_BOOK depending on context, references continuity:
  > «Анна, давно не виделись! Помню, у вас был курс с Анной. Сейчас как — что-то поменялось?»

### Success metrics for Journey 3
- **Reactivation rate** (AT_RISK → POST_VISIT within 60 days): ≥ 25%
- **No-spam signal**: block-bot rate during Journey 3 < 0.5%
- **Opt-out rate** during reactivation: < 3%
- **DORMANT respect**: zero proactive messages after final care message

### Anti-patterns to prevent
- ❌ «Где вы были??» — guilt-tripping
- ❌ «У нас огромная скидка только для вас!» — desperation signal
- ❌ Multiple messages in tight window — destroys trust
- ❌ Asking why they left — feels accusatory
- ❌ Ignoring dormant → re-spamming after months — kills any chance of return

### Variants

#### High-LTV customer reactivation
If Retention Layer LTV > threshold:
- Step 1 message can include personal admin outreach: «Анна, если что — могу попросить владелицу салона позвонить, она часто подключается лично для постоянных клиентов»
- This shifts to **proactive HUMAN_LOCKED tier** with owner notification

#### Wellness-context reactivation (Year 2+ feature)
If Body State + Goals show pattern suggesting current concern likely:
- «Анна, в это время года часто обостряется напряжение в шее. Если беспокоит — у Анны есть свободное время в среду.»
- Demonstrates AI memory + relevance, not just «вы давно не у нас»

---

## 4. Cross-journey events

Some events affect all 3 journeys:

### Birthday
- Customer in any state gets birthday touch (B13 customer first-time)
- For ACTIVE_REGULAR: tier-multiplier bonus + cadence-aware
- For AT_RISK / DORMANT: birthday as light reactivation hook (no «специально для дня рождения скидка», just warm message)
- For DISCOVERED / EXPLORING: skipped (we don't know their birthday yet, and it would be presumptuous if from initData)

### New service launched at salon
- ACTIVE_REGULAR: «появилась новая услуга, думаю вам подойдёт потому что...» — frequency-budget aware
- EXPLORING: surfaces in catalog naturally; no proactive push
- PROBLEM_SEEKING: surfaces if relevant to stated concern
- DORMANT: NOT pushed

### Emergency / disruption (master left, salon closed temporarily)
- Affected ACTIVE_REGULAR: priority direct notification with alternative
- READY_TO_BOOK in flight: notified before they book; alternative offered
- POST_VISIT: not affected
- AT_RISK / DORMANT: not notified (no need to surface bad news for someone disengaging)

---

## 5. Journey health metrics (rollup)

For each journey, salon dashboard shows:

| Metric | Target | Critical Q |
|---|---|---|
| Problem-Seeking journey conversion (concern stated → booked) | ≥ 60% | Is AI helping or just listening? |
| Quick Rebook journey speed | < 30s median | Are we efficient enough? |
| Quick Rebook one-tap rate | ≥ 80% | Are we predicting right? |
| AI Reactivation success rate | ≥ 25% | Is care-led approach working? |
| DORMANT respect (no spam) | 100% | Are we trustworthy? |
| Trust signals across all journeys | block-bot < 0.5% | Aggregate health |

---

## 6. Implementation notes

### Where each journey lives in code
- Journey 1 (Problem-Seeking): driven by `apps/skills/booking/` + NLU intent classifier + Wellness Profile Body State
- Journey 2 (Quick Rebook): driven by `apps/skills/booking/` + cadence engine + Behavioral Layer
- Journey 3 (Reactivation): driven by retention engine + Retention Layer churn_risk + state transitions

### State transitions trigger journey entry
Per [`core-user-states.md`](./core-user-states.md):
- DISCOVERED + concern_signal → Journey 1
- ACTIVE_REGULAR + cadence_reached → Journey 2 (proactive)
- AT_RISK_DRIFTING reached → Journey 3

### Event System (next foundational doc)
The event taxonomy will codify exactly which signals trigger which journey entries.

---

## 7. Open questions

| # | Question | Lean | Owner | Urgency |
|---|---|---|---|---|
| Q-UJ1 | Journey 1 NLU — how confident in intent classification before AI «commits» to wellness journey vs other? | Threshold 0.7 for explicit «problem-seeking» language; below → ask clarifier «расскажите подробнее» | AI/ML | 🟡 |
| Q-UJ2 | Journey 2 «как обычно» trigger phrases — exhaustive list or NLU? | NLU with curated seed phrases («запишите как всегда», «то же что в прошлый раз», «обычное», «как обычно», «повтор», «опять») | AI/ML | 🟢 |
| Q-UJ3 | Journey 3 first reactivation message — single template or persona-tuned per emotional layer? | Per emotional layer if available (Layer 7); default template if profile too thin | PM | 🟡 |
| Q-UJ4 | Journey 3 DORMANT after final care message — should we re-attempt at 12 months or stay silent? | Stay silent; only re-engage on user-initiated contact | Founder | 🟢 |
| Q-UJ5 | Cross-journey memory — AI should reference past journey in current one («как в прошлый раз»)? | YES when high-confidence Layer 8 memory exists; subtle reference, not «помните вы говорили...» | UX | 🟡 |
| Q-UJ6 | New customer's first AI message — should it surface 4 entry buttons or wait for free-text concern? | Both: keyboard available but also accepts free text → routes to PROBLEM_SEEKING if concern words detected | UX | 🟢 |

---

## Cross-document linkage

- Foundational: [`product-ux-vision.md`](./product-ux-vision.md), [`core-wellness-profile.md`](./core-wellness-profile.md), [`core-user-states.md`](./core-user-states.md)
- Voice: [`assistant-persona.md`](./assistant-persona.md) per-state tone modulation
- Ownership: [`conversation-ownership-policy.md`](./conversation-ownership-policy.md) for HUMAN_LOCKED triggers in Journeys 1 and 3
- Customer flows handoff: [`../handoffs/2026-05-18-customer-first-time-handoff.md`](../handoffs/2026-05-18-customer-first-time-handoff.md) — these journeys reframe parts of that handoff
- Next foundational: Event System taxonomy (forthcoming) — codifies triggers

## Last verified
2026-05-18 (founder roadmap step 4)
