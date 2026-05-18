# Attribution Policy

**Date:** 2026-05-18 r2
**Status:** v1.1 (locked product decision; engineering implementation pending)

**Changelog:**
- **r2 (2026-05-18 evening):** Schema refined per user product review — `booking_source` enum extended to 5 values (added `test_admin`; renamed `human` → `human_direct`); added stored `billable: bool` and `billing_reason: str` fields for transparency + dispute defense; `actor_type` enum added to `attribution_metadata` as mandatory key; Q12-α/β/γ/δ/ε all locked.
**Scope:** How every `BookingRequest` row is tagged for billing AND analytics. Source of truth for engineering, finance, sales, CSM.

Foundation: [`memory/project_attribution_extensible_model.md`](~/.claude/projects/.../memory/project_attribution_extensible_model.md). Read it first.

## 1. Core principle

**Attribution is multi-dimensional, not binary.**

We reject `attributed_to_bot: bool` as a schema choice because it creates a false dichotomy and blocks future analytics. Instead we capture 3 complementary fields at booking creation:

- **`booking_source`** — categorical (4-value enum)
- **`ai_assist_score`** — continuous (0.00–1.00, internal/analytics-only)
- **`attribution_metadata`** — JSON bag with full audit context

**Billing** is computed from a strict subset (`ai_direct` only). **Analytics** uses the full model. This separation is intentional and load-bearing.

## 2. The schema (r2 — refined per user product review)

### `BookingRequest` additions

```python
class BookingRequest(Model):
    # ... existing fields ...

    booking_source = CharField(
        max_length=20,
        choices=[
            ("ai_direct",    "AI создал запись напрямую"),
            ("ai_assisted",  "AI участвовал, человек завершил"),
            ("human_direct", "Только человек (sales/команда салона) без AI"),
            ("external",     "Запись пришла извне (YClients UI, телефон)"),
            ("test_admin",   "Тестовая запись или создана админом/мастером"),
        ],
        db_index=True,
        help_text="Categorical attribution. Drives billing (ai_direct only) and analytics breakdowns. test_admin combines test_mode + admin-created scenarios — both NOT billable.",
    )
    ai_assist_score = DecimalField(
        max_digits=3,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="0.00–1.00 estimate of AI contribution. Internal analytics only — not exposed in billing or customer-facing UI.",
    )

    # NEW r2: stored billable + billing_reason for transparency and dispute defense
    billable = BooleanField(
        default=False,
        db_index=True,
        help_text="Computed at insertion: True iff this booking should fire a BillingEvent. Stored (not recomputed) for audit clarity. Salon can see this in Catalog detail view.",
    )
    billing_reason = CharField(
        max_length=200,
        default="",
        help_text="Human-readable explanation: 'ai_direct customer booking' or 'NOT billable: actor_type=admin (test/admin)' or 'NOT billable: execute_reschedule (retention not acquisition)'. Surfaces in dispute UI.",
    )

    attribution_metadata = JSONField(
        default=dict,
        help_text="Audit context. Extensible — new fields added without migration. Required keys see §4.",
    )
    conversation_id = ForeignKey(
        "conversations.Conversation",
        null=True,
        blank=True,
        on_delete=SET_NULL,
        related_name="bookings",
        help_text="Source conversation if any. Null for external/human creations.",
    )
```

### Why stored `billable` + `billing_reason` (not just computed)
1. **Transparency to salon** — in Catalog/Billing detail view we show «Платная: ✓ / нет» with exact reason. Salon can read why and dispute specific cases.
2. **Dispute defense** — frozen reason at insertion is legally defensible («billing decision was made at this timestamp with this logic»). Recomputed values invite «the logic changed» disputes.
3. **Query performance** — `WHERE billable=True` indexed query is way faster than recomputing per row for billing reports.
4. **Audit trail simplicity** — change `billable` requires explicit admin action with audit event, not silent re-evaluation.

### Migration
One-time at schema-add. Backfill rule for existing rows:
- All pre-migration `BookingRequest` rows → `booking_source="external"`, `ai_assist_score=0.00`, `billable=False`, `billing_reason="backfilled: pre-attribution-policy"`, `attribution_metadata={"backfilled": True}`.
- Going forward all inserts must explicitly set the 5 fields. Code-review rule: any new `BookingRequest.create()` without these fails review.

## 3. `booking_source` semantics (5 values — r2)

### `ai_direct`
**Definition**: Assistant fully created the booking via `apps/skills/booking/tools.py::execute_confirm`, with no admin intervention in the conversation before booking creation.

**Recognition rule**:
- `execute_confirm` is the only call site that sets this
- `attribution_metadata.created_by = "execute_confirm"`
- No admin replies in conversation between conversation start and booking_created_at

**Examples**: Customer DMs bot → bot tool-calls → booking created. Customer in Mini App → flow lands at execute_confirm → booking created.

### `ai_assisted`
**Definition**: Assistant had meaningful presence (≥2 substantive replies OR ≥1 tool call) AND admin manually created the booking (admin via API, dashboard, or YClients UI).

**Recognition rule**:
- Either:
  - `created_by = "admin_manual"` with linked conversation having ≥2 bot replies, OR
  - `created_by = "yc_webhook"` with linked conversation having ≥1 tool call within 24h window AND `bot_replies_count >= 2`
- `attribution_metadata.handoff_occurred = True` typical but not required
- Score range: 0.4–0.9 by heuristic

**Examples**: Customer messages bot → bot collects details → handoff to admin (HUMAN_LOCKED) → admin creates in YClients UI within hours.

### `human_direct` (was `human` in r1)
**Definition**: Booking was created by admin/salon staff with NO prior AI conversation, OR with AI conversation present but irrelevant (e.g., bot only answered hours info, then customer called salon directly).

**Recognition rule**:
- `created_by` ∈ {`admin_manual`, `yc_webhook`} AND no recent (24h) bot conversation with ≥2 substantive replies
- OR: conversation existed but had no tool calls and ≤1 reply
- AND actor_type ∈ {`customer`, `system`} (NOT admin/test — that's `test_admin`)

**Examples**: Salon receptionist enters booking from a phone call. Walk-in customer.

### `external`
**Definition**: Booking ingested from outside our system without our infrastructure creating it. May have had loose AI touch (customer asked bot something days ago) but causality is weak.

**Recognition rule**:
- `created_by = "yc_webhook"` AND prior bot conversation within 7 days with ≤1 tool call
- OR: ingested from any 3rd-party source we don't control
- Score range: 0.0–0.3

**Examples**: Customer had a price-question conversation 4 days ago, then booked via YClients web. Customer used external booking site we sync from.

### `test_admin` (NEW r2)
**Definition**: Booking was created in test mode OR by salon team member (owner/admin/receptionist/master) regardless of which tool fired. **Never billable** — combining test_mode and admin scenarios into one explicit categorical value (was implicit metadata flag in r1).

**Recognition rule**:
- `attribution_metadata.actor_type` ∈ {`owner`, `admin`, `receptionist`, `master`}, OR
- `attribution_metadata.test_mode = True` (Phase 6 onboarding test chat), OR
- Both

**Examples**: Owner Karina tests the bot during Phase 6 onboarding. Admin Anya creates a booking through Mini App pretending to be customer. Receptionist tests a flow during shift handover.

**Why explicit enum vs metadata flag**: salon can see «test_admin» chip directly in Catalog and immediately understand «не платная: это была наша тестовая запись». Reduces dispute surface and trust friction. Cleaner reporting.

### Decision tree (engineering — r2)
```
booking creation event arrives
  │
  ├─ actor_type ∈ {owner, admin, receptionist, master} OR test_mode=True?
  │   └─ booking_source = "test_admin"
  │      billable = False
  │      billing_reason = "NOT billable: actor_type={X} or test_mode"
  │      (handled FIRST — overrides any other classification)
  │
  ├─ source = our execute_confirm tool? (customer actor)
  │   └─ booking_source = "ai_direct", score = 1.0
  │      billable = True
  │      billing_reason = "ai_direct: customer-initiated, created via execute_confirm"
  │
  ├─ source = execute_reschedule? (customer actor)
  │   └─ booking_source = "ai_direct", score = 1.0
  │      billable = False  ← Q12-α (reschedule = retention not acquisition)
  │      billing_reason = "NOT billable: execute_reschedule (retention not acquisition, Q12-α)"
  │
  ├─ source = admin manual API? (admin-driven create, NOT in test_admin path)
  │   ├─ linked conversation has ≥2 bot replies AND ≥1 tool call?
  │   │   └─ booking_source = "ai_assisted", score = 0.5–0.8
  │   │      billable = False
  │   │      billing_reason = "NOT billable: ai_assisted (admin completed bot-started flow)"
  │   └─ Else: booking_source = "human_direct", score = 0.0
  │      billable = False
  │      billing_reason = "NOT billable: human_direct (admin created without AI)"
  │
  └─ source = YClients webhook (salon-side or 3rd-party)?
      ├─ matching customer has bot conversation in last 24h with ≥2 bot replies AND ≥1 tool call?
      │   └─ booking_source = "ai_assisted", score = 0.4–0.6
      │      billable = False
      │      billing_reason = "NOT billable: ai_assisted (YC sync, prior bot involvement)"
      ├─ matching customer has bot conversation in last 7 days (lighter)?
      │   └─ booking_source = "external", score = 0.1–0.3
      │      billable = False
      │      billing_reason = "NOT billable: external (YC sync, weak bot touch)"
      └─ Else: booking_source = "human_direct", score = 0.0
          billable = False
          billing_reason = "NOT billable: human_direct (YC sync, no prior bot)"
```

**Special handling — no-show post-billing event (Q12-β):**
- When YClients webhook fires `status=NO_SHOW` on a previously-billable booking:
  - Create offsetting `BillingEvent` with amount = −100 ₽
  - Reason: «no-show auto-refund»
  - Update `BookingRequest.billing_reason` appending «refunded: no-show {date}»
  - Fire anti-fraud check: if salon's NO_SHOW rate >15% OR sudden spike → CSM review trigger

## 4. `attribution_metadata` schema

### Required keys (always populated)
```json
{
  "conversation_id": "uuid or null",
  "started_by": "customer" | "admin" | "system",
  "actor_type": "customer" | "owner" | "admin" | "receptionist" | "master" | "system",
  "created_by": "execute_confirm" | "execute_reschedule" | "yc_webhook" | "admin_manual",
  "test_mode": false,
  "admin_role_active": false,
  "booking_created_at": "ISO datetime"
}
```

**`actor_type` is the new mandatory field (r2)** — promoted from implicit detection to explicit storage. Required for billing rule `billable = (actor_type == "customer")`. If null/missing → engineering bug → reject insertion in `BookingRequest.save()` validator.

### Conditional keys (populated when relevant)
```json
{
  "tool_calls_count": int,
  "bot_replies_count": int,
  "human_replies_count_before_create": int,
  "handoff_occurred": bool,
  "handoff_reason": "complaint_sentiment" | "out_of_catalog" | ... | null,
  "redirect_used": bool,
  "redirect_destination": "yclients_web" | "mini_app" | "external_url" | null,
  "elapsed_seconds_first_to_create": int,
  "first_message_at": "ISO datetime",
  "ownership_tier_at_create": "AI_CONTINUITY" | "HUMAN_SUPERVISED" | "HUMAN_LOCKED" | null
}
```

### Extensibility rule
- JSON field — new keys added without migration
- Engineering convention: snake_case keys, primitive values or short nested arrays
- Document new keys in this file when added

## 5. `ai_assist_score` heuristic (MVP rule-based)

Range: 0.00–1.00 (DecimalField 3,2). Internal-only — never shown in billing or to customer.

### Computation rules (executed at booking creation)

```python
def compute_assist_score(booking_source, metadata):
    if booking_source == "ai_direct":
        return Decimal("1.00")

    if booking_source == "human":
        return Decimal("0.00")

    if booking_source == "ai_assisted":
        tool_calls = metadata.get("tool_calls_count", 0)
        bot_replies = metadata.get("bot_replies_count", 0)
        human_replies = metadata.get("human_replies_count_before_create", 0)

        base = Decimal("0.5")
        # Stronger bot involvement increases score
        if tool_calls >= 3:
            base += Decimal("0.2")
        elif tool_calls >= 1:
            base += Decimal("0.1")
        # More human work decreases score
        if human_replies >= 5:
            base -= Decimal("0.2")
        elif human_replies >= 2:
            base -= Decimal("0.1")
        # More bot work increases
        if bot_replies >= 5:
            base += Decimal("0.1")

        return max(Decimal("0.30"), min(Decimal("0.90"), base))

    if booking_source == "external":
        bot_replies = metadata.get("bot_replies_count", 0)
        if bot_replies == 0:
            return Decimal("0.00")
        if bot_replies <= 2:
            return Decimal("0.10")
        if bot_replies <= 5:
            return Decimal("0.20")
        return Decimal("0.30")

    return Decimal("0.00")
```

### When to revise this heuristic
- After 1000+ labeled bookings collected → ML-based scoring
- If sales explicitly demand higher resolution
- If salons start to query «why is this 0.6 and that one 0.7»

Until then: simple, deterministic, low-dispute.

## 6. Billing rule (the strict subset — r2)

```python
def compute_billable(booking_source, attribution_metadata) -> tuple[bool, str]:
    """The ONE function that decides if BillingEvent fires.
    Returns (billable, billing_reason) — both stored on BookingRequest.
    """
    actor = attribution_metadata.get("actor_type")
    test_mode = attribution_metadata.get("test_mode", False)
    created_by = attribution_metadata.get("created_by")

    # actor_type is mandatory — if missing, fail closed (not billable + alert)
    if actor is None:
        return (False, "NOT billable: actor_type missing (engineering bug — alert)")

    # test_admin overrides everything
    if booking_source == "test_admin":
        return (False, f"NOT billable: test_admin (actor={actor}, test_mode={test_mode})")

    # Only ai_direct is billable
    if booking_source != "ai_direct":
        return (False, f"NOT billable: booking_source={booking_source}")

    # ai_direct + customer actor
    if actor != "customer":
        return (False, f"NOT billable: actor_type={actor} (not customer)")

    # ai_direct + customer + reschedule → not billable (Q12-α)
    if created_by == "execute_reschedule":
        return (False, "NOT billable: execute_reschedule (retention not acquisition, Q12-α)")

    # ai_direct + customer + execute_confirm → billable
    return (True, "ai_direct: customer-initiated, created via execute_confirm")


# At BookingRequest.create():
booking_source = classify_source(...)  # per §3 decision tree
billable, billing_reason = compute_billable(booking_source, attribution_metadata)
BookingRequest.objects.create(
    ...,
    booking_source=booking_source,
    ai_assist_score=compute_score(booking_source, attribution_metadata),
    billable=billable,
    billing_reason=billing_reason,
    attribution_metadata=attribution_metadata,
)
# BillingEvent fires asynchronously only if billable=True
```

**Default outcome by source × actor**:
| `booking_source` | actor_type | Billable? | billing_reason |
|---|---|---|---|
| `ai_direct` | `customer` (execute_confirm) | ✅ Yes | "ai_direct: customer-initiated, created via execute_confirm" |
| `ai_direct` | `customer` (execute_reschedule) | ❌ No | "NOT billable: execute_reschedule (retention not acquisition, Q12-α)" |
| `test_admin` | any | ❌ No | "NOT billable: test_admin (actor=X, test_mode=Y)" |
| `ai_assisted` | any | ❌ No (analytics-only on MVP) | "NOT billable: ai_assisted (...)" |
| `human_direct` | any | ❌ No | "NOT billable: human_direct (...)" |
| `external` | any | ❌ No | "NOT billable: external (...)" |
| any | `actor_type missing` | ❌ No + ALERT | "NOT billable: actor_type missing (engineering bug — alert)" |

Plus refund rules per [Q15](../decisions-log.md):
- Cancelled <1h after creation → auto-credit −100 ₽
- Cancelled >24h → no refund
- No-show via YC webhook → auto-credit −100 ₽ (Q12-c)
- Window 1h–24h → CSM-discretion, audited

## 7. Edge case decision matrix

20 scenarios from Q12 brainstorm, fully resolved:

| # | Scenario | `booking_source` | `ai_assist_score` | Billed? | Notes |
|---|---|---|---|---|---|
| 1 | Pure bot success in chat | `ai_direct` | 1.00 | ✅ | Reference case |
| 2 | Bot suggested → customer goes to YClients web | `external` | 0.10–0.30 | ❌ | UTM tracking later may upgrade to `ai_assisted` |
| 3 | Bot → Mini App → execute_confirm | `ai_direct` | 1.00 | ✅ | All our infra |
| 4 | Bot started → admin took over → manual YC | `ai_assisted` | 0.40–0.70 | ❌ | Bot earned analytics credit, not bill |
| 5 | Admin start (HUMAN_LOCKED) → manual YC | `human` | 0.00 | ❌ | No bot involvement |
| 6 | Customer ghosted bot → phone-booked next day | `human` or `external` | 0.00–0.20 | ❌ | Depends on whether bot conversation existed |
| 7 | Bot info-only → walk-in booking | `external` | 0.10 | ❌ | Light AI touch |
| 8 | Bot create → cancel <1h | `ai_direct` | 1.00 | ✅ then refunded | Q15 refund auto |
| 9 | Bot create → late cancel >24h | `ai_direct` | 1.00 | ✅ | No refund |
| 10 | Bot reschedule (`execute_reschedule`) | `ai_direct` | 1.00 | ❌ | Retention not acquisition (Q12-b) |
| 11 | Multi-touch: 3 conversations → eventual booking | depends on last touch | varies | depends on last source | Last-touch rule for source assignment |
| 12 | FAQ-only conversations → YC webhook booking later | `external` | 0.10–0.20 | ❌ | Weak causality |
| 13 | YClients-side creation (salon enters in YC) | `human` | 0.00 | ❌ | No bot involvement |
| 14 | Bot status-check (no booking event) | n/a | n/a | n/a | No `BookingRequest` row at all |
| 15 | Admin uses Mini App as customer | **`test_admin`** | 0.00 | ❌ | r2: explicit test_admin enum (was implicit flag) |
| 16 | Test booking (Phase 6 test chat) | **`test_admin`** | 0.00 | ❌ | r2: explicit test_admin enum (was implicit flag) |
| 17 | Multi-service in one chat (2 bookings) | each `ai_direct` | 1.00 each | ✅ each (2 events) | Per-booking |
| 18 | Mini App start_param deep-link → execute_confirm | `ai_direct` | 1.00 | ✅ | Our infra end-to-end |
| 19 | Group booking | `ai_direct` | 1.00 | ✅ | 1 event per `BookingRequest` |
| 20 | No-show (YC marks `NO_SHOW`) | as original | as original | ✅ then refunded | Q12-c refund auto |

## 8. Implementation requirements

### Schema
- [ ] Migration: add `booking_source`, `ai_assist_score`, `attribution_metadata`, `conversation_id` to `BookingRequest`
- [ ] Backfill existing rows: `external` + 0.00 + `{"backfilled": True}`
- [ ] DB index on `booking_source` (for analytics queries)

### Set at insertion
- [ ] `apps/skills/booking/tools.py::execute_confirm` — set `ai_direct` + 1.00 + metadata
- [ ] `apps/skills/booking/tools.py::execute_reschedule` — set `ai_direct` + 1.00 + `created_by="execute_reschedule"` (billing skips via rule)
- [ ] `apps/integrations/yclients/webhooks.py` — set per decision tree (§3)
- [ ] Admin booking API (when created) — set per decision tree
- [ ] Add `is_billable()` helper in `apps/billing/attribution.py`

### Tests (mandatory)
- [ ] Unit: each `booking_source` value gets correct `is_billable` result
- [ ] Unit: heuristic score computation for 20 edge cases
- [ ] Integration: `execute_confirm` correctly tags `ai_direct` with full metadata
- [ ] Integration: `yc_webhook` ingestion with prior bot conversation correctly marks `ai_assisted` vs `external`
- [ ] Integration: test_mode flag prevents billing despite `ai_direct`
- [ ] E2E: full booking flow tags correctly and BillingEvent fires once per billable

### Audit
- [ ] Sample audit script: pull random 50 bookings per week, manual review for first 3 months
- [ ] Attribution rate dashboard: % of bookings by source (sanity bounds)
- [ ] Dispute pattern: salons disputing > 10% → CSM review trigger

## 9. Analytics use cases enabled

### Salon ROI dashboard
```
За май 2026:
• 34 AI-direct       (бот создал сам)
• 51 AI-assisted     (бот участвовал, команда завершила)
• 19 Human           (только команда)
• 12 External        (YClients UI, телефон)

Общая выручка: 248 400 ₽
Из них через AI (direct + assisted): 156 800 ₽ (63%)
```

### Sales pitch (prospect)
> «В среднем салоны через нашу платформу делают 40-60% бронирований с участием AI-ассистента. Из них половина — полностью автоматически, остальные — assisted, когда ваш админ финализирует.»

(Note: «с участием AI» honest, weeks of `ai_assisted` data behind it. NOT «AI created 60%» which would be overclaim.)

### Bot quality monitoring
- Look for `ai_direct` → cancelled <1h pattern (means bot is creating bookings customers immediately regret)
- Look for `ai_assisted` with high `human_replies_count_before_create` (means handoff was needed, bot underperformed)

### Future: commission tiers / pricing experiments
- Per-tenant: weighted billing by `ai_assist_score` (e.g., bill 50 ₽ for `ai_assisted` strong cases) — TBD post-cohort#50 data

## 10. Customer-facing surfaces (what they see)

### Customer (booking client)
- **Nothing about attribution.** They see a booking confirmation with the assistant identity.
- No mention of «бот», «attribution», «score». Per [single-assistant identity](~/.claude/projects/.../memory/project_single_assistant_identity.md).

### Salon admin (Карина / Аня)
- **Catalog detail of a booking** shows source as chip:
  - `🤖 Помощник` for `ai_direct`
  - `🤖+👤 Помощник + команда` for `ai_assisted`
  - `👤 Команда` for `human`
  - `📥 Извне` for `external`
- Click chip → opens audit drawer with metadata (admin role only)
- **Billing screen** shows breakdown: «X счёт-bookings из Y total»

### Customer-facing language
Never «attribution», «AI-direct», «assisted». In Russian admin UI:
- «Создал помощник» (ai_direct)
- «Помощник + команда» (ai_assisted)
- «Команда» (human)
- «Извне» / «Из YClients» (external)

## 11. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Heuristic for `ai_assist_score` disputed by salons | Score not exposed in billing; categorical source enum is. Refine via ML when labeled. |
| Engineering shortcut: someone re-introduces `attributed_to_bot: bool` | Code review checklist + migration constraint + this doc |
| Salon claims `ai_assisted` is actually `ai_direct` for billing | Source determined at insertion deterministically; no retroactive recompute. Dispute flow handles. |
| Customer GDPR delete kills `conversation_id` link | `booking_source` + key metadata preserved; `conversation_id` may go null. Attribution defensible. |
| New booking source (e.g., voice channel) added to platform | Add new enum value via migration + this doc. Reject ad-hoc strings. |
| Pressure to bill `ai_assisted` to boost revenue | This doc is the contract. Changes require founder + legal review. |

## 12. Open sub-questions (locked Q12 sub-decisions)

| # | Question | Recommendation | Founder sign-off needed? |
|---|---|---|---|
| **Q12-α** | Confirm `ai_direct` + `execute_reschedule` excluded from billing | Yes (Q12-b) | ✅ awaiting founder |
| **Q12-β** | Confirm no-show auto-refund | Yes (Q12-c) | ✅ awaiting founder |
| **Q12-γ** | Engineering: how to detect `admin_role_active` at booking time | Check `bot_user.role in {Owner, Admin, Receptionist, Master}`; require eng feasibility audit | ✅ eng audit |
| **Q12-δ** | Pre-launch attribution audit: who reviews sample 50? | Quality Reviewer (founder for cohort #1-50 per Q-CO3/LQ5) | ✅ awaiting founder |
| **Q12-ε** | Договор-оферта clause must reflect categorical attribution + refund rules | Draft in §13 below | ✅ legal review |

## 13. Draft договор-оферта clause (8 mandatory elements per Q12-ε)

Per user product review (2026-05-18 r2), the oferta clause MUST contain 8 elements:

1. **Definition of `ai_direct`** — what counts as billable
2. **What's NOT billable** — explicit list of non-billable categories
3. **No-show refund auto** — when applied
4. **Cancel <1h refund auto** — when applied
5. **Cancel 1h–24h CSM discretion** — process
6. **Cancel >24h no refund** — rule
7. **Dispute process** — channel (e-mail/dashboard), SLA (48h CSM response)
8. **30-day dispute window** — final-decision authority (CSM lead, escalation to founder)

### Full draft (RU)

> **«Раздел N. Расчёт за Брони.**
>
> **N.1.** Платформа взимает с Заказчика 100 (сто) рублей за каждую Бронь Клиента, классифицированную системой Платформы как `ai_direct` — то есть Бронь, созданную автоматизированным ассистентом Платформы через программную функцию `execute_confirm` без прямого участия персонала Заказчика, по инициативе Клиента (роль `customer`).
>
> **N.2.** Не подлежат оплате следующие категории Броней:
> - `ai_assisted` — Бронь, где ассистент участвовал в подготовке, но финальное создание выполнил персонал Заказчика;
> - `human_direct` — Бронь, созданная персоналом Заказчика без участия ассистента;
> - `external` — Бронь, поступившая из сторонних систем (YClients UI, телефонный звонок и пр.);
> - `test_admin` — тестовые Брони или Брони, созданные пользователями с ролями владельца/администратора/мастера Заказчика;
> - Перенос существующей Брони (классификация `ai_direct` + действие `execute_reschedule`) — не считается новой Бронью.
>
> **N.3.** При статусе «не пришёл» (`NO_SHOW`), переданном из системы записи Заказчика (YClients), Платформа автоматически возвращает Заказчику 100 (сто) рублей за соответствующую Бронь в следующем счёте.
>
> **N.4.** При отмене Брони Клиентом в течение 1 (одного) часа с момента создания, Платформа автоматически возвращает 100 (сто) рублей в следующем счёте.
>
> **N.5.** При отмене Брони в период от 1 (одного) часа до 24 (двадцати четырёх) часов с момента создания, решение о возврате принимается службой Customer Success Платформы по обращению Заказчика. Решение фиксируется в журнале аудита.
>
> **N.6.** При отмене Брони позднее 24 (двадцати четырёх) часов с момента создания возврат не производится.
>
> **N.7.** Заказчик имеет право оспорить классификацию Брони или начисление в течение 30 (тридцати) календарных дней с даты выставления счёта. Спор подаётся через личный кабинет Платформы или электронную почту support@. Служба Customer Success Платформы рассматривает спор в течение 48 (сорока восьми) часов и принимает решение: оставить в силе / частичный возврат / полный возврат. Все решения фиксируются в журнале аудита.
>
> **N.8.** В случае несогласия Заказчика с решением Customer Success, спор эскалируется к руководителю Customer Success или к Платформе (founder-level) — финальное решение принимается в течение 14 (четырнадцати) дополнительных календарных дней. После этого срока решение Платформы является окончательным для целей расчётов.»

### Legal review action items
- Validate compliance with **ФЗ-54** (онлайн-кассы, чеки, ОФД)
- Validate compliance with **ФЗ-152** (персональные данные — особенно если в `attribution_metadata` хранится customer info)
- Validate compliance with **ГК РФ** (договор-оферта формат)
- Cross-check terminology vs Q14 tax profile entities (ИП/ООО/самозанятый — разные формулировки могут потребоваться)
- Confirm 30-day dispute window не противоречит ФЗ о защите прав потребителей (Заказчик — юр.лицо или ИП, не потребитель, поэтому ФЗоЗПП не применяется, но проверить)

**Batch with Q14 + Q-C3** — одна 2–4-часовая консультация закроет 3 legal items.

## 14. Cross-document linkage

- Strategic foundation: [`memory/project_attribution_extensible_model.md`](~/.claude/projects/.../memory/project_attribution_extensible_model.md)
- Pricing context: [`memory/project_pricing_model_hybrid.md`](~/.claude/projects/.../memory/project_pricing_model_hybrid.md)
- Billing UX: [`2026-05-17-salon-onboarding-handoff.md`](../handoffs/2026-05-17-salon-onboarding-handoff.md) Screen 12
- Conversations UX (where conversation_id linkage lives): [`2026-05-17-conversations-handoff.md`](../handoffs/2026-05-17-conversations-handoff.md)
- Decisions log entry: [`decisions-log.md`](../decisions-log.md) Q12
- Booking skill tools: `apps/skills/booking/tools.py` (engineering)
- YClients webhook: `apps/integrations/yclients/webhooks.py` (engineering)
