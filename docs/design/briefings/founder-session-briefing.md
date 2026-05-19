# Founder Session Briefing

**Date drafted:** 2026-05-18
**Session length:** ~30 minutes
**Pre-read time:** ~10 minutes
**Outcome:** 5 decisions ratified, unblocks billing implementation and CSM strategy

This document is a focused pre-read for a single 30-min founder session covering 5 product decisions. All have strong recommended answers — session is about **founder ratification + edge case sanity check**, not open exploration. Each item has detailed reasoning in [`decisions-log.md`](../decisions-log.md) r4 and [`attribution-policy.md`](../policies/attribution-policy.md) r2.

---

## Decision 1 — Q11 — CSM headcount model

**Question:** When do we hire our first Customer Success Manager? How many active salons per CSM?

**Recommendation:**
- **Founder-led onboarding for first 25 active salons.** No CSM hire unless founder physically can't keep up.
- At 25 active salons — **trigger metrics review**, not calendar review:
  - Avg onboarding time per salon > 2 hours founder-time → CSM needed
  - Activation rate <60% in 14 days → product/wizard issue
  - Churn first 30d > 15% → CSM or activation rework
  - Support requests >3/week/salon → FAQ/CSM gap
  - KB-incomplete share >30% → onboarding-assist needed
- **CSM capacity working assumption**: 1 CSM per **15–25 salons if CSM-heavy manual ops**; per **40–60 only if automated onboarding + self-serve KB**

**Why this matters:** CSM cost (80–120 тыс. ₽/мес) eats margin fast. Hiring too early kills unit economics. Hiring too late kills activation and retention. Metrics-triggered hiring is more conservative than calendar-driven.

**Action:** ✅ Ratify or modify the trigger thresholds. Confirm working assumption proceeds.

**Time:** 5 minutes

---

## Decision 2 — Q13 — Payment provider

**Question:** Which payment provider — CloudPayments / ЮKassa / Stripe?

**Recommendation:** **CloudPayments primary, ЮKassa fallback, Stripe later for non-RU.**

**Conditional**: deep integration BLOCKED on 1–2 page provider checklist by finance. Must verify CloudPayments supports:
- Recurring base payment (590 ₽/мес subscription)
- Variable per-event charges (100 ₽ per billable booking)
- Refunds (full + partial for no-show / cancel)
- Webhook HMAC validation
- Фискализация per ФЗ-54
- УПД documents for ИП/ООО
- Sandbox availability
- Go-live timeline
- Commission % at our projected volume

If CloudPayments fails any → fallback to ЮKassa with same checklist.

**Why this matters:** Wrong choice = expensive migration mid-flight. Right choice = smooth billing operations, happy auditor, fast УПД issuance.

**Action:** ✅ Ratify «CloudPayments primary» working assumption. ✅ Approve finance to spend 1–2 hours on provider checklist before eng starts integration.

**Time:** 5 minutes

---

## Decision 3 — Q12-α — Reschedule billing

**Question:** Should we charge 100 ₽ when bot creates a rescheduled booking (`execute_reschedule`)?

**Recommendation:** **NO — 0 billable for reschedule.**

**Reasoning:**
- Reschedule = retention, not acquisition
- Salon didn't gain a new customer; they kept an existing one
- Taking 100 ₽ from salon for retention = «мелочный биллинг», salon will feel nickel-and-dimed
- We track reschedule in analytics as value metric (bot saved a booking from being lost)

**Counter-argument:** «Bot did work, should be paid». Counter-counter: friendly billing for cohort #1–50 builds trust faster than nickel-and-diming. Revenue lost: ~5–10% of bookings are reschedules.

**Action:** ✅ Ratify reschedule = 0 billable. Locked in договор-оферта clause.

**Time:** 3 minutes

---

## Decision 4 — Q12-β — No-show auto-refund

**Question:** If customer doesn't show up (`status=NO_SHOW` from YClients), do we automatically refund 100 ₽ to salon?

**Recommendation:** **YES — auto-refund + anti-fraud monitoring.**

**Reasoning:**
- Salon lost revenue (planned the slot for nothing)
- Charging them 100 ₽ on top = anti-incentive to use bot at all
- Trust matters more than per-booking revenue

**Anti-fraud caveat:** salon could mark NO_SHOW falsely to avoid 100 ₽ fee. Mitigation:
- Anomaly detection: salon's NO_SHOW rate > 15% → CSM review
- Sudden spike → audit trigger
- Pattern: individual salon repeatedly cancelling bot fees → manual review

**Action:** ✅ Ratify no-show = auto refund. ✅ Approve anti-fraud monitoring threshold (15%).

**Time:** 4 minutes

---

## Decision 5 — Q12-δ — Pre-launch attribution audit owner

**Question:** Who manually reviews the first 50 attributed bookings before commercial billing fires?

**Recommendation:** **Founder reviews first 50.** Same Quality Reviewer role as Q-CO3/LQ5 (founder for cohort #1–50, CSM lead after).

**Reasoning:**
- Founder on cohort #1–50 understands every edge case best
- Sees disputed logic, vague rules, where salon will be unhappy
- Calibrates rules before scale
- 2 hours of manual review = trust saved at scale

**Process:**
1. After first 50 `billable=True` bookings, pause automated billing
2. Founder reviews each: was this correctly classified?
3. Fix bugs surfaced
4. Resume automated billing

**Action:** ✅ Ratify founder = audit owner for cohort #1–50.

**Time:** 3 minutes

---

## Session checklist

- [ ] Q11 — founder-led first 25 + trigger metrics ratified
- [ ] Q13 — CloudPayments primary working assumption + finance checklist approved
- [ ] Q12-α — reschedule = 0 billable ratified
- [ ] Q12-β — no-show auto-refund + 15% anti-fraud threshold ratified
- [ ] Q12-δ — founder audits first 50 attributions ratified

**All 5 ratified → 5 items move from r4-locked-by-designer to founder-ratified → engineering proceeds with full mandate.**

---

## Out-of-scope for this session (don't get distracted)

- **Q14 / Q-C3 / Q12-ε** — RU legal review (separate consult, see [`legal-consult-briefing.md`](./legal-consult-briefing.md))
- **V1–V5** — validation tasks (continuous, doesn't need ratification, just resourcing)
- **Tiered pricing (V5)** — hypothesis to test post-launch, not decision now

---

## What this session UNBLOCKS

- Engineering: `BookingRequest` schema migration can ship; `execute_confirm` tagging logic can ship
- Billing screen UX: can finalize (only payment provider integration still blocks on Q13 checklist)
- CSM strategy: founder-led era confirmed, no premature CSM hire
- договор-оферта draft: 4 of 5 attribution rules confirmed (5th = Q12-γ engineering audit, parallel)
- Sales conversations: clear answer to «как вы измеряете эффективность» — strict billing on `ai_direct` only

## Linked artifacts

- [`decisions-log.md`](../decisions-log.md) — canonical decision tracking
- [`attribution-policy.md`](../policies/attribution-policy.md) — full attribution spec
- [`memory/project_attribution_extensible_model.md`](~/.claude/projects/.../memory/project_attribution_extensible_model.md) — strategic foundation
- [`memory/project_pricing_model_hybrid.md`](~/.claude/projects/.../memory/project_pricing_model_hybrid.md) — pricing context
