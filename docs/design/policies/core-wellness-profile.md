# Core Wellness Profile — Strategic Foundation Document

| Field | Value |
|---|---|
| **Date** | 2026-05-19 r2 (Ayla-first voice-sweep) |
| **Status** | Strategic foundation — wellness profile = Ayla's memory of user (cross-tenant); see Doc #2 for memory operational model |
| **Type** | Architectural foundation (not a handoff — это spec для всех будущих handoffs) |
| **Scope** | Define what Ayla knows about a person, how it's stored, how it's used, how it influences UX |
| **Replaces** | «Customer profile» (transactional) → «Wellness profile» (relational) → «Ayla memory» (Ayla-first model 2026-05-19) |

## ⚠ r2 Ayla-first voice-sweep note

Per [`project_ayla_first_strategic_pivot`](./ayla-identity-and-brand.md) memory 2026-05-19: Wellness Profile IS **Ayla's memory of user** per [`ayla-memory-and-personalization §3-9`](./ayla-memory-and-personalization.md). 10-layer model maps to MemoryEntry vocabulary. Cross-tenant persistent; tenant CANNOT see per [`tenant-as-provider-model §4.4`](./tenant-as-provider-model.md). 3-zone sensitivity framework (🟢🟡🔴) overlay applies per [`ayla-identity-and-brand §8`](./ayla-identity-and-brand.md). Terminology updated: «AI помощник» → «Ayla».

## What this document IS

A **10-layer data + behavior specification** for the central entity of the AI Wellness OS — the digital wellness-model of a human. Every screen, every bot message, every recommendation, every retention touch flows through this profile.

## What this document is NOT

- ❌ A database schema (Phase 1 implementation derives this)
- ❌ Privacy policy or consent legal text (separate doc, downstream)
- ❌ Specific UX screens (those are downstream handoffs)
- ❌ MVP launch blocker — Profile starts MINIMAL и evolves through phases

---

## 0. Why this exists

### The product reframing

**OLD framing**: «Salon AI booking platform»
**NEW framing**: «AI Wellness Operating System with salon services as one delivery channel»

These produce fundamentally different products:

| Aspect | OLD (booking-first) | NEW (wellness-OS) |
|---|---|---|
| Customer relationship | transactional, per-visit | relational, persistent |
| AI memory scope | last few conversations | physical / behavioral / emotional / lifetime |
| Main screen | catalog / book / pay | «как вы себя чувствуете сегодня?» + AI state |
| AI behavior | reactive (responds to user) | **proactive** (notices, suggests, supports) |
| Retention loop | reminders + promos | observe → recommend → support → analyze → improve |
| Customer value | «бот для записи» | «Ayla — мой AI-помощник, который меня помнит» |
| Switching cost | low | high (12+ months of personal data) |
| LTV | medium | 3-5× higher |
| Pricing model | salon pays platform | platform charges salon AND optionally customer |

### What stays foundational from existing work

This pivot **does not invalidate** existing handoffs. Booking / Conversations / Billing / Attribution / Persona / Loyalty become **infrastructure** for the wellness OS, not the product.

Single-assistant identity ([memory: single-assistant-identity]) is even MORE important now — the assistant is the user's continuous wellness companion across all touchpoints.

### Reading prerequisites

1. [`memory/project_single_assistant_identity.md`](~/.claude/projects/.../memory/project_single_assistant_identity.md) — foundational
2. [`memory/project_conversation_ownership_tiers.md`](~/.claude/projects/.../memory/project_conversation_ownership_tiers.md) — for HUMAN_LOCKED gating
3. [`memory/project_attribution_extensible_model.md`](~/.claude/projects/.../memory/project_attribution_extensible_model.md) — visits feed Service History layer
4. [`assistant-persona.md`](./assistant-persona.md) — adapts per Emotional Layer

---

## 1. The 10 Layers — overview

```
┌─────────────────────────────────────────────────────────┐
│        CORE WELLNESS PROFILE — one per user             │
├─────────────────────────────────────────────────────────┤
│  1. Identity         — basic facts, locale, age, gender │
│  2. Goals            — what the user wants              │
│  3. Body State       — current physical state           │
│  4. Service History  — what we've done together         │
│  5. Behavioral       — patterns of engagement           │
│  6. Nutrition        — food / water / supplements       │
│  7. Emotional        — communication preferences        │
│  8. AI Memory        — short-term + long-term context   │
│  9. Recommendations  — current AI suggestions           │
│ 10. Retention        — churn risk + reengagement signals│
└─────────────────────────────────────────────────────────┘
```

Every layer has:
- **Storage** — what fields exist
- **Source** — how data enters
- **Confidence** — how trustworthy is each value
- **UX impact** — how this layer changes what user sees
- **Privacy class** — what consent + retention applies
- **MVP scope** — what's in Phase 1 vs later

---

## 2. Layer 1 — Identity

### Storage
```json
{
  "user_id": "uuid",
  "name_first": "Анна",
  "name_last": "Иванова",
  "display_name_preference": "Анна",   // how AI addresses
  "gender_self_identified": "female" | "male" | "other" | "prefer_not_say",
  "year_of_birth": 1992,
  "city": "Москва",
  "timezone": "Europe/Moscow",
  "language": "ru-RU",
  "phone_masked": "+7 ••• ••• 14 67",  // masked for display
  "phone_hash": "<HMAC>",                // for matching, not display
  "communication_preferred_time": "evening",
  "first_contact_at": "ISO",
  "consent_marketing": true,
  "consent_wellness_features": true,    // separate from marketing
  "consent_at": "ISO"
}
```

### Source
- Signup form (Phase 2 onboarding for salon admin, Mini App profile for customer)
- MAX initData (verified phone)
- Inferred (timezone from MAX platform, language from initData)

### Confidence
- ✅ HMAC-verified: phone hash, user_id, MAX-side fields
- ⚠ Self-reported: name, year, gender, city, preference

### UX impact
- AI address: «Анна, ...» — never «уважаемая клиент»
- Language: all messages in `language` (RU MVP, later KZ/BY)
- Time-of-day: don't push 09:00 to «evening» customer
- Gender-aware grammar: «помогла» vs «помог» in Russian (verbs agree)
- Age-relevant suggestions: «anti-age» только если 30+ self-declared
- City: travel time estimation; salon match if multi-location chain

### Privacy class
- **PII**: yes (phone, name)
- **Retention**: Layer 1 per ownership-policy §6 (deletable on customer request)
- **Anonymization**: name → «Клиент #UUID» after retention period

### MVP scope (Phase 1)
- Required: user_id, phone_hash, language, consent_marketing
- Optional but encouraged: name, year_of_birth, gender, city, communication_preferred_time
- Filled in by Mini App profile form (per customer first-time §F4 already designed)

---

## 3. Layer 2 — Wellness Goals

### Storage
```json
{
  "goals": [
    {
      "id": "stress_reduction",
      "priority": 1,
      "added_at": "ISO",
      "status": "active" | "achieved" | "paused"
    },
    {
      "id": "weight_management",
      "priority": 2,
      "added_at": "ISO",
      "status": "active"
    }
  ],
  "goals_taxonomy_version": 1
}
```

### Available goals taxonomy (curated, Russian-vertical baseline)
1. `stress_reduction` — снижение стресса
2. `sleep_improvement` — улучшение сна
3. `swelling_reduction` — снижение отёчности
4. `weight_management` — управление весом
5. `skin_health` — здоровье кожи
6. `body_shape` — коррекция фигуры
7. `pain_relief` — снижение боли (общая)
8. `anti_aging` — anti-age (кожа / тело)
9. `confidence_appearance` — уверенность в себе через внешность
10. `recovery_after_event` — восстановление после нагрузки
11. `chronic_condition_support` — поддержка при хроническом состоянии (osteo, etc.)
12. `lifestyle_maintenance` — поддержание формы

Customer can have 1-3 active goals. Goals can be paused or archived (not deleted — they're history).

### Source
- **Explicit**: customer answers «какая ваша главная цель?» in onboarding (or later — never first-message)
- **Inferred**: bot conversations / service history hints («жалуюсь на спину 3 визита подряд» → suggests pain_relief)
- **Acknowledgement check**: AI proposes inferred goal, customer confirms or rejects

### Confidence
- Explicit answer: 0.9
- Inferred but acknowledged: 0.8
- Inferred not confirmed: 0.5 (low — used as soft suggestion only)

### UX impact
- **Main screen reorganizes** around top goal: «Рекомендации для снижения стресса»
- AI recommendations filtered by goal alignment
- Retention messages tied to goals: «месяц назад вы хотели улучшить сон — как сейчас?»
- Service catalog presentation reorders («для вашей цели» chip)
- Adaptive content: stress-reduction customer gets calming tone; weight-management gets factual

### Privacy class
- Wellness data (medical-adjacent): higher sensitivity per Q-C3 retention Layer 4
- Cannot share cross-tenant without explicit opt-in
- Customer can clear all goals → AI reverts to generic mode

### MVP scope (Phase 1)
- **NOT in Phase 1** as explicit user input — too early friction
- BUT collected implicitly from B11 feedback («что чаще всего важно?»)
- Active goal display starts Phase 2 (when AI has ≥3 visits of data)

---

## 4. Layer 3 — Body State (current)

### Storage
```json
{
  "current_state": {
    "energy_level": 5,           // 0-10 scale
    "stress_level": 8,           // self-reported or inferred
    "sleep_quality_last_week": 4, // 0-10
    "fatigue_level": 7,
    "swelling": true | false,
    "pain_points": ["шея", "поясница"],
    "mood": "стрессовый" | "спокойный" | "энергичный" | etc.,
    "last_updated_at": "ISO",
    "updated_source": "conversation" | "form" | "post_visit_feedback"
  },
  "state_history": [
    /* time-series of state snapshots, retention per Layer 4 sensitive */
  ],
  "chronic_conditions": [        // long-term, separate from current
    "shoulder_tension",
    "lymphatic_drainage_needed"
  ]
}
```

### Source
- Post-visit feedback (B11) — «как себя чувствуете?»
- Direct conversation («у меня болит шея» → AI captures)
- Self-reported in Mini App «состояние» section (Phase 3+)
- Inferred from service history (3 visits booked for massage neck = «pain_points: шея»)

### Confidence
- Self-reported: 0.9
- Conversation-inferred: 0.7 (could be passing comment, not chronic)
- Service-history-inferred: 0.6

### UX impact
- **Recommendations**: stress_level high → suggest relaxation services first
- **Tone modulation**: high-stress state → tone slider auto-shifts to «сдержанный/заботливый» per Persona Editor
- **Frequency throttle**: high stress → reduce proactive messages (per ownership-policy)
- **Service ordering**: «у вас болит шея — массаж шеи и спины наверху каталога»
- **Skip-the-pitch**: customer in obvious distress → AI doesn't suggest upsell, just empathy + handoff

### Privacy class
- **Sensitive** (Layer 4 per Q-C3)
- Structured flags preferred over full text («pain_zone=neck» not «у меня после совещаний болит шея с понедельника»)
- Higher retention restrictions; medical role gate (per ownership-policy §4)

### MVP scope (Phase 1)
- Minimal: post-visit feedback collects mood ("как себя чувствуете?" 1-5 scale)
- Phase 2: introduce pain_points as structured tags
- Phase 3+: full state monitoring with Mini App «состояние» section

---

## 5. Layer 4 — Service History

### Storage
```json
{
  "visits": [
    {
      "booking_request_id": "uuid",
      "service": "лимфодренаж",
      "service_category": "массаж",
      "master": "Анна Петрова",
      "date": "2026-05-01",
      "duration_minutes": 90,
      "price_paid_rub": 2200,
      "attribution": "ai_direct",  // links to attribution-policy
      "customer_reaction": {
        "rating": 5,                 // from B11 feedback
        "felt_better": true,
        "noted_effects": ["лучше сплю", "меньше отёки"],
        "would_repeat": true
      },
      "pre_visit_state_snapshot": { /* Body State at booking time */ },
      "post_visit_state_snapshot": { /* Body State T+24h after */ }
    }
  ],
  "service_preferences": {
    "preferred_categories": ["массаж", "косметология"],
    "avoided_categories": [],
    "favorite_masters": ["Анна Петрова"],
    "typical_cadence_days": 21  // ~3 weeks between massages
  }
}
```

### Source
- Every COMPLETED `BookingRequest` adds a visit entry
- Customer reaction from B11 feedback
- State snapshots from Body State Layer
- Cadence computed from visit timestamps

### Confidence
- Visit happened: 1.0 (system of record)
- Reaction: 0.9 if rated, 0.5 if inferred from re-booking behavior
- Effects: 0.7 if customer explicitly mentioned in conversation

### UX impact
- **Repeat suggestion**: «после лимфодренажа у вас лучше спалось → повторим курс?»
- **Cadence reminder**: «обычно вы каждые 3 недели — пора»
- **Master continuity**: «Анна свободна в четверг — записать как обычно?»
- **Cross-service correlation**: «массаж + косметология вместе вам помогает — забронировать день?»
- **Avoidance respect**: don't suggest categories customer marked as «не подходит»

### Privacy class
- Booking records: Layer 3 per Q-C3 (7 years retention for tax)
- Customer reactions: Layer 1 transcripts (180 days), then anonymized into aggregate
- Effects mentions (if include medical context): Layer 4 sensitive

### MVP scope (Phase 1)
- Every BookingRequest with `status=COMPLETED` adds entry automatically
- Reaction captured via B11 feedback (already in customer first-time §B11)
- Cadence computed from existing data
- Effects mentions begin Phase 2 (AI extraction from conversation transcripts)

---

## 6. Layer 5 — Behavioral

### Storage
```json
{
  "activity_pattern": {
    "engagement_score": 82,      // 0-100, last 30d
    "booking_pattern_time": "evening",
    "preferred_days": ["thu", "fri"],
    "preferred_window": "16:00-19:00",
    "advance_booking_days_avg": 5,
    "cancellation_rate_pct": 8,
    "no_show_rate_pct": 0,
    "response_latency_to_bot_avg_seconds": 45,
    "preferred_channel": "max",
    "session_count_30d": 4
  },
  "engagement_history": [/* time series for graphs */]
}
```

### Source
- Computed continuously from conversation + booking events
- Aggregated by ML behind the scenes

### Confidence
- Behavioral data: 1.0 (observed)
- Predictive («prefers Thursday»): 0.7-0.9 depending on sample size

### UX impact
- **Frequency policy enforcement**: per behavioral patterns, don't push more than tolerated
- **Smart timing**: «вечерний клиент» gets retention messages 18:00-21:00, not 09:00
- **Day preference**: Filler-slot campaigns target customer's typical day
- **Channel routing**: «preferred_channel=max» → all proactive via MAX, not email
- **Engagement-based proactive throttle**: high engagement → can send more; low engagement → preserve trust

### Privacy class
- Behavioral data: aggregate-only after retention period
- Per-customer individual patterns: Layer 1 PII

### MVP scope (Phase 1)
- Basic: preferred_days, booking_pattern_time, advance_booking_days_avg (cheap computes)
- Phase 2: engagement_score + ML-driven predictions
- Phase 3+: full predictive layer

---

## 7. Layer 6 — Nutrition (Phase 3+, opt-in)

### Storage
```json
{
  "consent_active": true,
  "tracking_started_at": "ISO",
  "metrics_7d_avg": {
    "calories": 2100,
    "protein_g": 65,
    "water_ml": 1400,
    "sleep_hours": 6.5
  },
  "patterns": [
    "low_water_workdays",
    "late_dinner_thursday",
    "irregular_breakfast"
  ],
  "last_log_at": "ISO"
}
```

### Source
- Customer logs in Mini App «питание / вода / сон» section
- Optional integrations: HealthKit / Google Fit
- Inferred from photo scans (Phase 4+)

### Confidence
- Self-logged: 0.7 (forgetfulness)
- Sensor-synced: 0.95

### UX impact
- **Holistic recommendations**: «Вчера было мало воды → отёчность сегодня может усилиться → лимфодренаж?»
- **Behavior connections**: «после массажа лучше спите → подкрепляйте дополнительно»
- **Proactive nudges**: «не пили воду 6 часов — поставить напоминание?»
- **Cross-system improvements**: «зачастую тревожность по средам совпадает с поздним ужином»

### Privacy class
- **Highest sensitivity** — health data adjacent
- Explicit opt-in required
- Layer 4 retention rules apply

### MVP scope (Phase 1)
- **NOT in MVP**. Phase 3+ feature.
- Placeholder consent flag in profile schema (forward-compatible).

---

## 8. Layer 7 — Emotional / Communication Style

### Storage
```json
{
  "communication_style": "supportive",  // supportive | direct | analytical | warm | playful
  "preferred_message_length": "short",  // short | medium | detailed
  "stress_reactivity": "high",          // affects retention message tone
  "decision_style": "intuitive",        // intuitive | rational | hesitant
  "humor_tolerance": "low",             // low | medium | high
  "formality": "informal",              // informal | formal
  "preferred_emoji_density": "minimal", // none | minimal | regular
  "last_adapted_at": "ISO"
}
```

### Source
- **Inferred** from conversation behavior:
  - Short responses → preferred_message_length=short
  - Direct questions → decision_style=rational
  - Emoji in customer messages → emoji_density=regular
- **Explicit opt-in**: customer chooses style in profile («как со мной общаться»)

### Confidence
- Inferred with ≥5 conversations: 0.8
- Explicit: 0.95

### UX impact
- **Persona modulates per customer**: same salon's assistant talks differently to Анна vs Олег
- **Message length adapts**: short-preference customer gets «Записать на завтра 14:00?» not «Хотите я предложу несколько вариантов времени на завтрашний день?»
- **Tone shifts**: supportive customer gets «понимаем как непросто», analytical gets «вот варианты по эффективности»
- **Anti-spam respect**: high-stress-reactivity customer sees fewer promo messages

### Privacy class
- Emotional model: sensitive — inferred but personally identifying
- Customer can reset / opt-out

### MVP scope (Phase 1)
- **Not active** in Phase 1 — uses single persona setting
- Phase 2: introduce adaptive style on per-customer basis (within tenant's overall persona)

---

## 9. Layer 8 — AI Memory (short + long term)

### Storage
```json
{
  "short_term_memory": {
    "last_conversation_topic": "боль в шее",
    "last_recommendation": "массаж спины и шеи 90 мин",
    "current_session_intent": "rescheduling",
    "recent_messages_context": [/* last 5-10 messages */],
    "session_started_at": "ISO",
    "ttl_until": "ISO"  // expires 24h after last interaction
  },
  "long_term_memory": {
    "established_facts": [
      "chronic_neck_pain",
      "prefers_anna_for_manicure",
      "allergic_to_acrylic",
      "uses_evening_appointments_only",
      "responded_well_to_lymphatic_drainage"
    ],
    "rejected_suggestions": [
      "early_morning_appointments",  // customer said «нет» strongly
      "category_skincare_aggressive"
    ],
    "important_dates": [
      {"type": "birthday", "date": "06-12"},
      {"type": "wedding_anniversary", "date": "09-15", "discovered": "from_conversation"}
    ],
    "personal_notes": [
      "two_children",
      "works_remote_friday"
    ]
  },
  "conversation_summaries": [
    {
      "conversation_id": "uuid",
      "summary": "Mария спросила про массаж шеи. Признала повышенный стресс. Рекомендован еженедельный курс. Записалась на пятницу.",
      "extracted_facts": ["chronic_neck_pain", "high_stress_period"],
      "summarized_at": "ISO"
    }
  ]
}
```

### Source
- Short-term: current session messages, recent intent
- Long-term: extracted facts from conversation analysis (LLM-driven)
- Promoted facts: short-term observation appears 3+ times → becomes long-term «established fact»

### Confidence
- LLM-extracted: 0.7 — flag for confirmation
- Customer-confirmed: 0.95
- Long-tenure observed: 0.9

### UX impact
- **Continuity**: «как в прошлый раз — то же время?» (uses long-term)
- **Contextual responses**: customer mentions «эта проблема» → AI knows the chronic_neck_pain context
- **Personal touch**: «как ваш сын после школы?» (from notes, with consent)
- **Avoidance**: don't repeat rejected suggestions
- **Time-relevant**: nearer birthday → birthday flow activates

### Privacy class
- Long-term memory is RICHEST personal data we hold
- Customer can view all stored facts in profile
- Customer can remove specific facts («забыть»)
- Hard-deletion respects GDPR-like per OP6

### MVP scope (Phase 1)
- Short-term memory: ALWAYS active (last 5-10 messages of conversation)
- Long-term memory: STARTS minimal Phase 1 — only manually entered «notes» from admin (per Master mobile §M6 master notes)
- AI auto-extraction → Phase 2 with explicit customer consent + verification flow

---

## 10. Layer 9 — Recommendations (current)

### Storage
```json
{
  "active_recommendations": [
    {
      "id": "rec_uuid",
      "type": "service_repeat" | "service_new" | "habit_change" | "self_care",
      "title": "Записаться на лимфодренаж к Анне",
      "rationale": "Прошло 3 недели, у вас типичный цикл",
      "confidence": 0.87,
      "valid_until": "ISO",
      "action_url": "/booking?master=anna&service=lymph",
      "shown_count": 0,
      "responded": null,  // null | accepted | rejected | snoozed
      "source": "service_cadence_pattern"
    }
  ],
  "rejected_recommendations": [/* archive of what didn't work */],
  "next_best_action": {
    "type": "repeat_visit",
    "confidence": 0.87,
    "computed_at": "ISO"
  }
}
```

### Source
- Recommendation engine reads Layers 2-8 and produces ranked suggestions
- Updates daily (or on significant state change)

### Confidence
- High (>0.8): show prominently
- Medium (0.5-0.8): show as secondary
- Low (<0.5): suppress

### UX impact
- **Main screen**: top recommendation shown as featured card
- **Bot DMs**: bot includes recommendation in proactive messages
- **Mini App home**: «Рекомендации сегодня» section
- **Personalization signal**: «потому что у вас типично 3 недели цикл»

### Privacy class
- Recommendations themselves: not sensitive (derived from sensitive layers)
- Show transparently why: «потому что» — never as opaque «AI говорит надо»

### MVP scope (Phase 1)
- Simple rules-based recommendations (cadence + service repeat)
- No ML model — heuristics on visit history + behavioral patterns
- Phase 2: ML-driven with confidence scores

---

## 11. Layer 10 — Retention

### Storage
```json
{
  "last_visit_days_ago": 18,
  "expected_cadence_days": 21,
  "days_until_predicted_visit": 3,
  "churn_risk_score": 0.18,        // 0-1, higher = more at risk
  "best_reengagement_type": "care_message",  // care_message | promo | personal_outreach | escalate
  "previous_reengagement_attempts": [
    {"date": "ISO", "type": "care_message", "outcome": "responded_booked"}
  ],
  "trust_score": 0.92,  // based on consistent positive history
  "lifetime_value_rub": 47800,
  "tier_loyalty": "regular"  // from loyalty system
}
```

### Source
- Computed from all layers + service history
- Churn model: gradient boost on behavioral signals
- Trust score: positive history without disputes

### Confidence
- Behavioral signals: 1.0 observed
- Churn prediction: 0.7-0.9 depending on history length

### UX impact
- **Smart re-engagement**: customer at 0.18 risk vs 0.72 → different message types
- **Reactivation prioritization**: high LTV + high risk → personal admin outreach (HUMAN_LOCKED tier)
- **Loyalty bonus targeting**: high trust + medium risk → loyalty milestone push
- **Avoid wasted touch**: customer who responds well to «care» messages gets those, not promo

### Privacy class
- Churn risk + LTV: business-sensitive (Owner sees, admin role gated)
- Customer doesn't see their churn score (anti-pattern)

### MVP scope (Phase 1)
- Last_visit_days_ago + expected_cadence: simple compute
- Churn_risk: ML-driven Phase 2
- Trust_score: behavioral aggregate Phase 2

---

## 12. Cross-layer interactions (the magic)

The profile is more than 10 isolated layers — the **connections** between layers produce the wellness OS magic:

### Goals × Service History → Personalized recommendations
```
Goal: stress_reduction (Layer 2)
Service History: 3 massages, all positive (Layer 4)
Body State: stress_level=8 currently (Layer 3)
→ Recommendation: relaxation massage, urgent priority
```

### Body State × Behavioral → Timing intelligence
```
Body State: fatigue_level=7 (Layer 3)
Behavioral: prefers evening slots, low energy mornings (Layer 5)
→ AI suggests evening slot, frames as «после рабочего дня — расслабиться»
```

### Service History × Nutrition × Body State → Holistic insight
```
After 3 lymphatic drainage visits + tracked low water intake + sleep poor
→ AI: «Заметил, что массажи помогают, особенно когда сон хороший. Может попробуем поправить воду — и эффект будет глубже?»
```

### Emotional × Recommendations → Adaptive presentation
```
Emotional: stress_reactivity=high (Layer 7)
Recommendations: high confidence «не было давно» (Layer 9)
→ Frame as care: «давно не виделись — как ваше самочувствие?» NOT «у вас просрочка — запишитесь»
```

### AI Memory × Retention × Long-term → Profound continuity
```
Long-term memory: «two children, works remote Friday» (Layer 8)
Retention: 21 days since last visit (Layer 10)
→ «Анна, как обычно в пятницу свободно — забронируем массаж?»
```

This is what makes the wellness OS feel like a friend, not a tool.

---

## 13. Evolution path from MVP customer profile

### Phase 0 (current state, before this doc)
Customer profile = thin: name, phone, language, past bookings.

### Phase 1 — Foundation (Months 1-3, with MVP launch)
Add to schema:
- ✅ Layer 1 — Identity (already exists, extend with `display_name_preference`, `communication_preferred_time`, `consent_wellness_features`)
- ✅ Layer 8 (short-term) — session memory (already implicitly there)
- ✅ Layer 8 (long-term) — start collecting admin notes (per Master Mobile §M6)
- ✅ Layer 4 — service history (auto-populated from BookingRequest)
- ✅ Layer 5 — behavioral basics (preferred_days computed from history)
- ✅ Layer 9 — basic rules-based recommendations
- ✅ Layer 10 — basic retention signals

NOT yet:
- Goals (Layer 2)
- Body State (Layer 3)
- Nutrition (Layer 6)
- Emotional (Layer 7) — uses tenant persona globally
- LLM-extracted long-term facts

### Phase 2 — Activation (Months 4-6)
Adds:
- Layer 2 — Goals (introduced via gentle B11 feedback questions)
- Layer 3 — Body State (post-visit «как себя чувствуете?» captures structured state)
- Layer 7 — Emotional inference (adaptive style starts)
- Layer 9 — ML-driven recommendations
- Layer 8 — LLM-extracted long-term facts (with verification flow)

### Phase 3 — Wellness OS (Months 6-12)
Adds:
- Layer 6 — Nutrition (opt-in section in Mini App)
- Layer 3 — Full body state monitoring
- Wellness dashboard
- Cross-layer correlation engine
- Event system fires UX changes

### Phase 4 — Companion (12+ months)
- Predictive wellness suggestions
- Deep personalization
- Cross-procedure correlation analysis
- Adaptive Mini App (different layout per customer state)

---

## 14. Data model summary

### Tables required
```
WellnessProfile  (1 per customer per tenant)
  user_id (FK)
  layer_1_identity JSONB
  layer_2_goals JSONB
  layer_3_body_state JSONB  (with retention rules)
  layer_4_service_history_summary JSONB
  layer_5_behavioral JSONB
  layer_6_nutrition JSONB  (opt-in)
  layer_7_emotional JSONB
  layer_8_memory_short JSONB (TTL 24h)
  layer_8_memory_long JSONB
  layer_9_recommendations JSONB
  layer_10_retention JSONB
  last_updated_at
  schema_version

WellnessEvent  (immutable event log)
  id, user_id, tenant_id
  event_type
  payload JSONB
  source
  occurred_at
  confidence

WellnessMemoryFact  (for long-term memory)
  id, user_id, tenant_id
  fact_type
  fact_value
  confidence
  verified (bool)
  source_conversation_id
  created_at
  last_referenced_at

WellnessRecommendation  (current active rec for user)
  id, user_id, type, title, rationale,
  confidence, valid_until,
  shown_count, responded, response_outcome
  source_algorithm
```

### Storage strategy
- WellnessProfile: indexed JSONB per layer (PostgreSQL)
- Layers updated independently (no global lock)
- WellnessEvent: append-only event log for replay/audit
- Soft-delete on customer GDPR per OP6 — anonymizes but preserves aggregates

---

## 15. Cross-document impact

This document changes how existing handoffs are understood:

| Existing handoff | Reframing |
|---|---|
| Customer first-time | F4 profile becomes Wellness Profile editor (Layer 1 + opt-in for Layers 2/6/7) |
| Loyalty | Tier multipliers + retention messages use Layer 10 churn risk to time better |
| Persona Editor | Per-customer adaptive style (Layer 7) layered on top of tenant default persona |
| Conversations | Long-term memory (Layer 8) visible to admin during conversations |
| Analytics dashboard | New «Wellness» tab shows aggregate goal achievement, body state trends |
| Schedule | Customer cadence (Layer 4 + 5) informs «обычно вы в четверг» suggestion |
| Master mobile | Master sees own customers' Layer 4 reactions + Layer 3 state for prep |
| Marketing campaigns | Audience segmentation uses Layer 2/3/10 in addition to existing filters |
| Customer GDPR | Per-layer deletion granularity option |

**Engineering implication**: every new feature should ask «which layer does this read or write?»

---

## 16. Privacy & consent architecture

### Layered consent
- **Basic consent** (signup): Layer 1, 4, 5, 8 (short-term), 10 — operational necessity
- **Wellness consent** (opt-in, gentle ask): Layer 2, 3, 8 (long-term)
- **Health tracking consent** (explicit opt-in): Layer 6
- **Adaptive AI consent** (opt-in): Layer 7
- **Recommendation consent** (default on, can opt-out): Layer 9
- **Retention analytics** (default on, anonymized): Layer 10

### Customer control
- View what's stored: Profile screen shows all layers (with sensitive marked)
- Forget specific facts: Layer 8 long-term has «забыть» action per fact
- Delete entire layer: customer can clear goals / nutrition / etc.
- Hard delete: per OP6 customer-deletion flow

### Per-layer retention
- Layer 1: per customer-deletion request
- Layer 2: same as Layer 1
- Layer 3: structured 6 months full, then anonymized aggregates
- Layer 4: 7 years (booking financial data)
- Layer 5: aggregate after 1 year
- Layer 6: 6 months full, then aggregate
- Layer 7: until customer changes or deletes
- Layer 8 short: 24h TTL
- Layer 8 long: customer-controlled (delete per fact)
- Layer 9: 90 days then archive
- Layer 10: rolling 1 year history

---

## 17. UX impact summary

| Without Wellness Profile | With Wellness Profile |
|---|---|
| «Здравствуйте! Чем помочь?» | «Анна, помню вы хотели заняться спиной — Анна сегодня свободна» |
| Booking takes 5 messages | Booking takes 1 («да, как обычно») |
| Bot doesn't know customer | Bot remembers chronic concerns, preferences, recent reactions |
| Generic recommendations | «После лимфодренажа вы лучше спали — повторим?» |
| Mass-blast retention | Tailored per customer state and churn risk |
| Same tone for all | Adaptive (rational customer gets facts, anxious gets care) |
| Customer churns silently | Layer 10 predicts + preempts |

---

## 18. Open questions (Q-WP prefix — Wellness Profile)

| # | Question | Lean | Owner | Urgency |
|---|---|---|---|---|
| Q-WP1 | When to introduce «Goals» (Layer 2)? After first visit? After 3 visits? When customer asks? | After 3 visits — enough data to make Goals feel relevant, not friction | PM | 🟡 |
| Q-WP2 | Nutrition tracking (Layer 6) — Phase 3 OR earlier as «Beta» opt-in? | Phase 3 default; Beta to first 100 customers if data shows demand | PM | 🟢 |
| Q-WP3 | Adaptive Persona (Layer 7) — per-customer style override of tenant persona — does the salon owner approve? | NO — adaptive style is *within* tenant's persona bounds; not a separate identity | PM | 🟡 |
| Q-WP4 | LLM extraction of long-term facts (Layer 8) — automatic or human-in-loop verification? | Human-in-loop MVP: AI proposes fact, admin or customer confirms before storage | PM + Legal | 🟡 |
| Q-WP5 | Cross-tenant wellness data — if customer uses bot at 2 salons, separate profiles or merged? | Per Q-CO5: separate profiles per tenant; v1.1 explicit opt-in for «один профиль везде» | Founder | 🟢 |
| Q-WP6 | «Forget» action — can customer remove specific service from history? | YES for display/personalization, NO from BookingRequest itself (financial record) | Legal | 🟡 |
| Q-WP7 | Confidence display to customer — show «I'm 87% confident you'd like this»? | NO — feels robotic. Use natural language: «обычно вам подходит» | Design | 🟢 |
| Q-WP8 | Recommendation engine MVP — pure rules or simple ML from start? | Pure rules MVP (transparent, explainable); ML Phase 2 | Eng | 🟡 |
| Q-WP9 | Behavioral data inference — what's the consent disclosure? Ayla collects implicitly while customer interacts | Honest mention in onboarding: «Ayla замечает паттерны и запоминает, чтобы рекомендовать точнее», customer can opt-out per [`ayla-memory-and-personalization §10.9`](./ayla-memory-and-personalization.md). RESOLVED for r2 via 3-zone framework + memory transparency surface. | PM + Legal | ✅ |
| Q-WP10 | Wellness Goals — fixed taxonomy of 12 OR customer can add custom? | Fixed MVP (curated); custom in v1.2+ | PM | 🟢 |

---

## 19. What this UNBLOCKS strategically

- **AI Wellness OS positioning** — clear vision document for stakeholders, investors, team
- **Engineering data model** — clear schema target for Phase 1 implementation
- **Roadmap clarity** — phased layer activation, not «build everything»
- **Customer relationship moat** — every layer adds switching cost
- **Cross-tenant licensing potential** (v2+) — if customer opts to «один профиль везде», we become the bridge
- **Persona Editor extensions** — adaptive customer style (Layer 7)
- **Marketing campaigns** — segment by Layer 2 goals + Layer 10 churn risk

## 20. What this does NOT unblock

- ❌ Replace MVP work — booking, conversations, billing all still required
- ❌ Skip onboarding/persona/loyalty handoffs — they're still relevant infrastructure
- ❌ Auto-build the wellness dashboard — that's downstream of this profile
- ❌ Bypass legal review — wellness data adjacent to medical, RU юрист must review Layers 3/6

## 21. Next steps (right order)

Per user's earlier guidance on correct UX startup sequence:

1. ✅ **Core Wellness Profile** (this doc) — foundational
2. **Product UX Vision** — 1-pager: what we are, what we aren't (next, smaller doc)
3. **Core User States** — 7 states from new customer to dormant (next, medium doc)
4. **User Journeys** — 3 main journeys (problem → AI helps, quick book, AI reengages)
5. **Event System taxonomy** — 10 high-impact events that drive UX
6. **Conversational UX** — tone evolution + message architecture
7. **Information Architecture** — Mini App restructure around states (not catalogs)
8. Refactor existing handoffs — incrementally align with new vector
9. **Wireframes for wellness dashboard** — Phase 3 surface
10. **UI System** — design tokens (mostly extend existing)

## 22. Cross-document linkage

- Foundational: [`memory/project_single_assistant_identity.md`](~/.claude/projects/.../memory/project_single_assistant_identity.md)
- Operational: [`memory/project_conversation_ownership_tiers.md`](~/.claude/projects/.../memory/project_conversation_ownership_tiers.md)
- Attribution: [`memory/project_attribution_extensible_model.md`](~/.claude/projects/.../memory/project_attribution_extensible_model.md)
- Pricing context: [`memory/project_pricing_model_hybrid.md`](~/.claude/projects/.../memory/project_pricing_model_hybrid.md) — wellness OS framing enables customer-side pricing (v2)
- Voice: [`assistant-persona.md`](./assistant-persona.md) — Layer 7 extends per-customer
- All current handoffs become «infrastructure for wellness OS», not «booking platform»

---

## 23. Sign-off

| Role | Approval | Date |
|---|---|---|
| Designer | ☐ | |
| Product / Founder (vision approval) | ☐ | |
| Engineering (data model feasibility) | ☐ | |
| Legal (per-layer consent + RU юрист sensitive data) | ☐ | |
| AI/ML lead (recommendation engine + confidence framework) | ☐ | |
