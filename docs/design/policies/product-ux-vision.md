# Product UX Vision

**Date:** 2026-05-18 r1
**Status:** Foundational — anchors all UX work going forward
**Authority:** Founder strategic direction 2026-05-18 + Chief UX Architect charter

> Read this BEFORE designing any new screen, writing any bot message, or proposing any feature.

---

## 1. What we are building

We are building an **AI Wellness Operating System** — a persistent personal AI companion that understands a user's body, habits, goals, emotional state, and history, and uses that understanding to support their wellness journey through curated services, proactive guidance, and continuous care.

Salon services (beauty, massage, cosmetology, wellness procedures) are **one delivery channel** for outcomes, not the product itself.

The product is **the relationship between the user and their AI wellness companion**, instantiated through a specific salon's brand but conceptually portable.

## 2. What we are NOT building

To avoid scope creep and product confusion:

- ❌ **«Маркетплейс мастеров»** — we don't aggregate competing salons
- ❌ **«Чат-бот для записи»** — a transactional bot is too small a value
- ❌ **«CRM для салона»** — admin tools are infrastructure, not the product
- ❌ **«Каталог услуг»** — services are decisions outputs, not entry points
- ❌ **«Медицинский диагност»** — we don't diagnose; we observe and route to specialists
- ❌ **«Универсальный ChatGPT»** — we're domain-bounded to wellness + body + lifestyle
- ❌ **«Соцсеть про красоту»** — no public profiles, no comparing, no influencer dynamics
- ❌ **«Просто loyalty система»** — loyalty is a mechanic, not the value
- ❌ **«Multi-channel marketing tool»** — campaigns are background, not center
- ❌ **«Тренировочный план / sports tracker»** — adjacent domain, not us (yet)

## 3. Who this is for

### Primary: customer of a wellness-oriented salon
- Female-leaning (75-85% of beauty market) but male-inclusive
- 25-45 years old typically
- Has at least one wellness goal beyond «look good» (stress, sleep, body, recovery)
- Uses MAX as a primary messenger
- Tolerates 2-3 proactive messages per month from trusted sources
- Wants to feel **cared for**, not **sold to**

### Secondary: the salon owner / team
- Wants retention and operational efficiency
- Brand-conscious — voice consistency matters
- Limited tech-savviness, but understands AI's value
- Sees the platform as a **digital team member**, not just software

### Tertiary: the master / practitioner
- Wants to focus on craft, not admin
- Needs visibility into their own customers and schedule
- Treats AI as a **calendar + assistant**, not as a replacement

## 4. The core principle

**AI knows the user. The product is the consequence of that knowledge.**

Not «AI generates a chat response.»
Not «AI books an appointment.»
But: **AI accumulates a coherent understanding of a human across time, and uses it to support, guide, and connect them to wellness outcomes.**

Every feature must answer:
- What layer of the [Core Wellness Profile](./core-wellness-profile.md) does this read or write?
- How does this make the AI's understanding richer or more useful?
- Does this feel like care, or like a transaction?

## 5. Strategic positioning

### Against no-code competitors (Salebot, Boterra, Cleversite)
They sell tools. We sell relationships. They are commodity bots; we are a persistent companion.

### Against beauty marketplaces (PROFI.RU services, YClients self-serve)
They optimize for transaction speed. We optimize for trust depth. They're searched once; we're talked to weekly.

### Against wellness apps (Headspace, Calm, Oura, WHOOP)
They give data without action. We give action without data overhead. They expect users to interpret; we interpret and recommend.

### Our unique position
**The only AI that knows your beauty/wellness history AND can act on it — book a service, suggest a habit change, prepare you for a procedure, recover with you afterward.**

## 6. The 5 UX shifts (from transactional to relational)

### Shift 1: Main screen
- ❌ «Услуги / Акции / Каталог» (transactional)
- ✅ «Как вы себя чувствуете сегодня?» + state + AI recommendations (relational)

### Shift 2: AI behavior
- ❌ Reactive (waits for user action)
- ✅ Proactive (notices, suggests, supports — within frequency policy)

### Shift 3: Memory horizon
- ❌ Last conversation
- ✅ Lifetime relationship (with explicit forget controls)

### Shift 4: Conversation flow
- ❌ Catalog → service → book
- ✅ State → AI understands context → suggests action → supports → analyzes → corrects path

### Shift 5: Salon's role
- ❌ Product publisher (lists services)
- ✅ Service deliverer (the AI brings clients with context to them)

## 7. Tone and voice principles

The AI speaks like a **caring informed friend**, not like:
- ❌ Marketing copy
- ❌ Customer service script
- ❌ Medical authority
- ❌ Tech jargon
- ❌ Overly casual gen-Z slang

Per [`assistant-persona.md`](./assistant-persona.md): warm, calm, attentive, confident, concise, empathetic, premium-but-accessible.

The AI never says «бот» about itself. It is **«помощник студии»** (instantiated per tenant). It is honest if asked directly about being AI.

## 8. Success — what we measure

### North star
**Wellness engagement retention** — % of users who interact (book, ask, respond) with their AI assistant ≥1× per month, 6 months post-first-touch.
- Target: ≥ 50% at month 6
- Industry baseline for booking apps: ~15%
- We aim for 3× that because we deliver relational value, not transactional

### Layered metrics
1. **Trust signals**: block-bot rate < 0.5%, opt-out rate < 2%
2. **Activation depth**: % users with ≥3 Wellness Profile layers populated within 60 days
3. **Compounding value**: median customer LTV at 12 months ≥ 2× LTV at 3 months
4. **Salon-side**: tenant retention ≥ 70% year-1
5. **AI quality**: persona violation rate < 2%, customer satisfaction ≥ 4.5★ rolling

## 9. The 5-year vision (where this evolves)

### Year 1 (now → +12 months): Booking-first MVP with Wellness Profile foundation
- Salon AI assistant launches for 50-200 salons
- Customer profile evolves into 4-5 Wellness Profile layers active
- Loyalty + marketing + analytics built on attribution-extensible model
- Trust earned through small consistent care moments

### Year 2: Wellness Profile activation
- All 10 layers actively used
- Customer-side wellness dashboard
- Predictive recommendations (ML-tuned)
- Cross-procedure correlation insights
- Customer-pays tier (premium personal AI)

### Year 3: Multi-domain expansion
- Adjacent verticals: fitness, nutrition coaching, mental wellness
- Same AI companion now spans salon + gym + nutritionist
- One assistant identity, multiple service providers
- B2C marketplace for verified wellness services

### Year 4: AI-native wellness ecosystem
- Wearable integration (Oura, Apple Watch, etc.)
- Habit modules (water, sleep, movement) integrated
- Predictive intervention (AI proactively prevents flare-ups, burnout)
- Network effects: best-in-class customer outcomes attract best-in-class services

### Year 5: Platform play
- The «AI Wellness Profile» becomes a portable customer-owned identity
- Customer brings their profile to any salon / clinic / coach
- We're the operating system; everyone else is an app
- White-label for enterprise wellness (corporate wellness programs)

## 10. Constraints we accept

### Permanent
- Customer trust > short-term revenue (every UX decision passes this filter)
- AI honesty > engagement metrics (no dark patterns, never lie about being AI)
- Single-assistant identity > flexibility (we don't fragment the experience)

### Until proven otherwise
- RU market first; expansion when waitlist supports it
- MAX as primary channel; Telegram as secondary parallel
- Salon as B2B customer; customer-pays explored Year 2+
- Beauty/wellness vertical only; adjacent verticals Year 3+

### Cultural
- We do not chase virality. We compound through depth.
- We do not optimize for short sessions. We optimize for long relationships.
- We do not sell features. We deliver care.

## 11. What this Vision blocks

If a proposed feature, screen, or message contradicts this Vision, it does not ship. The Chief UX Architect (per charter) flags conflicts and escalates.

Common rejection patterns:
- «Add a referral landing page» — wrong; referral is integrated into existing wellness flow, not a separate marketing surface
- «Show customer how to upgrade tier» — wrong; tier is a passive status signal, not a goal to optimize
- «AI suggests 5 services at once» — wrong; AI suggests ONE most relevant thing at a time
- «Customer fills out long onboarding form» — wrong; profile grows organically through conversation
- «Promotional carousel on home screen» — wrong; home screen is state-centric, not sales-centric

## 12. What this Vision unlocks

- **Strategic clarity** for every roadmap decision
- **Filter** for which features to build vs reject
- **Differentiation language** for sales conversations
- **Investor narrative** beyond «we have a chatbot»
- **Talent attraction** — designers and engineers want to build wellness AI, not yet-another-booking-bot
- **Pricing power** — the relationship has 3-5× LTV multiplier vs transactional

---

## Cross-document linkage

This Vision sits at the apex of design hierarchy:

```
            product-ux-vision.md  ←── THIS DOC (what we are)
                    │
        ┌───────────┼───────────────────┐
        ▼           ▼                   ▼
core-wellness-      core-user-          (existing policies:
profile.md          states.md            persona, attribution,
(data model)        (taxonomy)           ownership)
        │           │
        └───────┬───┘
                ▼
         user-journeys.md
         (paths through states)
                │
                ▼
        handoffs/*.md
        (per-feature specs)
                │
                ▼
         engineering
```

- [`core-wellness-profile.md`](./core-wellness-profile.md) — data model that makes vision actionable
- [`core-user-states.md`](./core-user-states.md) — 7 states users move through
- [`user-journeys.md`](./user-journeys.md) — paths between states
- [`assistant-persona.md`](./assistant-persona.md) — voice that delivers the vision
- All handoffs in `../handoffs/` must trace back to a Vision principle

## Last verified
2026-05-18 (founder strategic direction, locked)
