# Runbook: Pilot — Customer Data Request (access / delete) — manual dual-system procedure

> Status: **draft** (pilot-blocking-soft per #937)
> Last exercised: _never_
> Target completion sprint: 2026-07-15 pilot launch (Penza)
> Owner: Tau (UX + customer-facing wording) + on-call operator (execution)

## Purpose

During the pilot, the in-app delete/export UI is deferred (per `customer-profile-flow.md` R2/R3 → deferred-state, Variant 3; cross-service delete is unwired per ADR-0015 epic). 152-ФЗ subject-access and erasure rights are honored through a **manual operator procedure spanning two backends**: bot-platform (this repo) and beautygo djangoproject (Ayla). This runbook tells on-call how to execute and answer the customer honestly.

## Trigger / when to run

- Customer writes to support (MAX bot DM «удалить мои данные», «выгрузить мои данные», direct email to `support@gobeauty.site`, in-app Профиль → support tap routing to SupportEntrySheet)
- Founder/legal forwarded a 152-ФЗ subject-access request to ops
- Roskomnadzor inquiry (escalate to legal first; do NOT execute without legal sign-off)

## Prerequisites

- [ ] Operator has Django admin access to **both** backends: bot-platform admin + beautygo admin
- [ ] Operator has `manage.py shell` access on bot-platform prod (for `privacy_consent` skill invocation if needed)
- [ ] Founder approval intent recorded for the specific request (per `customer-privacy-data-closure-ux.md` Q-CP10) — capture in audit log
- [ ] Customer identity verified — MAX OAuth identity OR email-on-file match (no anonymous request execution)
- [ ] No active legal hold / anti-fraud investigation on the customer (check with founder before proceeding)
- [ ] Open dispute / pending future booking check (per `customer-privacy-data-closure-ux.md` §11) — if blockers present, do NOT execute; route customer to dispute resolution first

## Verified backend reality (2026-06-01 recon, per #937)

### bot-platform (this repo)

- **Delete:** `apps/skills/privacy_consent` `data_delete` action = **immediate hard-delete**, bot-platform scope only. Drops `BotUser`, `UserPersonalContext`, conversation memory, RFM/sentiment, audit. Does **not** touch beautygo.
- **Export:** `data_export` action = inline-JSON returned to bot DM. Bot-platform scope only. Does **not** include beautygo data.

### beautygo djangoproject (Ayla, separate repo)

- **Delete:** `DELETE /api/v1/auth/users/me/` = **soft-delete + anonymize**. User row marked deleted; PII fields anonymized.
  - **Appointments + Payments PROTECT** — preserved with `customer_id` retained, `customer_name` → «Удалённый клиент». Required by consumer-protection law minimum 3 years (ФЗ-2300-1 ст.10).
  - **Reviews + Nutrition logs + Favorites CASCADE** — fully removed.
  - **No S2S hook** — beautygo deletion does NOT signal bot-platform.
  - **No export endpoint** — operator must pull data manually via Django admin or `manage.py shell`.

### Cross-service implication

Neither side knows about the other's delete. Operator MUST execute both to fulfill a single customer request. Audit on both sides.

---

## Step-by-step procedure

### Procedure A — Manual delete (full customer erasure)

1. **Identity + blocker check** (Prerequisites above). If blocker present → stop, route customer to resolution.

2. **Record founder approval intent.** Open ticket / audit row with: customer ID (both systems), request type «delete», requestor verification method, founder approval timestamp.

3. **Execute beautygo-side delete.**
   - Method A (preferred): impersonate-as-customer via Django admin, hit `DELETE /api/v1/auth/users/me/` with customer's session.
   - Method B (fallback): via `manage.py shell` invoke the corresponding view/serializer with the customer's user object. Confirm row marked deleted + PII anonymized.
   - **Verify:** Appointments + Payments rows exist with `customer_name = "Удалённый клиент"` (anonymized but retained). Reviews / Nutrition / Favorites CASCADE'd (gone).
   - Record beautygo audit: timestamp + operator ID + customer ID.

4. **Execute bot-platform-side delete.**
   - Invoke `privacy_consent` skill's `data_delete` action for the customer. Two options:
     - via `manage.py shell` with the customer's `BotUser`: `from apps.skills.privacy_consent import PrivacyConsentSkill; PrivacyConsentSkill().delete(bot_user)`.
     - via Django admin: drop `BotUser` + cascaded `UserPersonalContext` + memory + audit rows. (Skill path preferred — single entry point, audit-traced.)
   - **Verify:** `BotUser` row gone, `UserPersonalContext` gone, conversation memory gone, `OutboxEvent` for the user purged or dropped per audit-immutability rule.
   - Record bot-platform audit row.

5. **Reply to customer (use wording from §"Customer reply templates" below).** Be honest about what was retained (booking/payment history, anonymized) and why (legal retention).

6. **Cross-audit.** Within 24h, a second operator (or founder) verifies both audit rows are present + customer reply was sent. Capture sign-off.

### Procedure B — Manual export (subject-access request)

1. **Identity + verification.** Same as Procedure A step 1.

2. **Founder approval intent** recorded.

3. **bot-platform side export.**
   - Invoke `privacy_consent` skill's `data_export` action. Output is inline JSON returned to the customer's MAX DM by default — for an SAR you may want to capture it as a file: `manage.py shell` invocation, dump JSON to disk, attach to the response channel agreed with the customer.
   - **Contents include:** `BotUser` profile, `UserPersonalContext` (memory layers per ADR-0011 privacy zones — zone classifications honored), conversation summaries, RFM/sentiment artifacts, audit history.
   - **Does NOT include:** beautygo bookings, payments, services catalog, reviews — they live in the other backend.

4. **beautygo side export (manual pull).**
   - Via Django admin or `manage.py shell` on beautygo, dump:
     - User profile (`Customer` row, anonymized fields excluded if applicable)
     - Appointments history (date, time, service, master, status — see `customer-privacy-data-closure-ux.md` §13 for canonical field list)
     - Payments history (amount, status, refund if any, masked PAN)
     - Reviews authored (text + rating + appointment ref)
     - Nutrition logs (if customer used food scanner)
     - Loyalty events (earn/burn)
     - Active favorites + saved masters
   - Format: JSON (matches bot-platform export style for consistency)

5. **Bundle + deliver.** Combine both JSON outputs into one archive. Deliver via the channel customer chose (MAX DM attachment if size permits, OR email).

6. **Reply with §"Customer reply templates" wording.**

7. **Cross-audit.** Within 48h SLA per §"What is retained and why".

---

## Customer reply templates (Ayla voice — for support operator to send)

### Reply A1 — Delete completed (typical)

```
Привет, Анна.

Удалила всё, что могла удалить полностью:
— переписку с тобой
— твои заметки и предпочтения, что я о тебе знала
— записи о настроении, воде, еде и других модулях
— твою историю с favorites и обзорами

Что осталось — записи на услуги и оплаты по ним. Это закон —
салон обязан хранить такие документы 3 года, даже если клиент
ушёл. Имя в этих записях заменила на «Удалённый клиент» —
ни тебя, ни твоих контактов там больше нет.

Если захочешь вернуться — начнём с чистого листа.

— Ayla
```

### Reply A2 — Delete blocked (open dispute / pending booking)

```
Привет, Анна.

Перед тем как удалить данные, нужно закрыть пару вопросов —
у тебя сейчас:
— {{open_dispute_summary}}
— {{pending_booking_summary}}

Давай сначала с ними разберёмся, потом вернёмся к удалению.
Так будет правильно и для тебя, и по закону.

— Ayla
```

### Reply B — Export delivered

```
Привет, Анна.

Собрала всё, что у меня про тебя есть. В архиве — два файла:
— bot-platform.json  (заметки, переписка, дневник питания)
— ayla.json          (записи на услуги, оплаты, отзывы)

Это всё. Если что-то непонятно — спроси, объясню.

— Ayla
```

### Reply C — Identity not verified

```
Привет.

Чтобы отдать или удалить данные, мне нужно убедиться, что
запрос от тебя. Напиши с того же аккаунта MAX, через который
ты со мной общалась, или подтверди почту на файле.

Это закон — мы не отдаём чужие данные посторонним.

— Ayla
```

---

## What is retained and why (honest customer-facing wording)

| What | Retained as | Why | Source |
|---|---|---|---|
| Booking / appointment history | Anonymized — `customer_name = "Удалённый клиент"`, `customer_id` retained for ledger integrity | ФЗ-2300-1 ст.10 (consumer protection — salon must prove service was rendered for 3 years) | beautygo `Appointment` |
| Payment / refund history | Anonymized same way | ФЗ-54 (cash register law) + accounting requirements | beautygo `Payment` |
| Consent log | Operator audit row (delete request, founder approval, execution timestamp) | 152-ФЗ ст.18 + ст.21 (lawful basis demonstrably documented) | dual-system audit |
| Aggregated analytics | Already anonymized at write-time; no PII | Legitimate interest basis | both sides |

**NOT retained (gone on delete):**
- bot-platform `BotUser` + identity link
- `UserPersonalContext` memory layers (Identity / Goals / Behavioral / Episodic / Symptom / Reactions / Preferences)
- Conversation history + summaries
- RFM scores + sentiment artifacts
- Customer's authored reviews (CASCADE on beautygo)
- Food / water / sleep / mood logs (CASCADE on beautygo `Nutrition`)
- Favorites + saved masters (CASCADE on beautygo)

---

## SLA + escalation

| Request type | Target SLA | Escalation if missed |
|---|---|---|
| Delete (acknowledgement) | 24h | Founder ping |
| Delete (execution) | 7 days | Founder + legal advisor |
| Export (acknowledgement) | 24h | Founder ping |
| Export (delivery) | 7 days for routine, 30 days if SAR with complexity (per 152-ФЗ ст.20) | Founder + legal advisor |
| Identity verification failure | Inform customer same day | None — wait for customer |

| Severity | Who | How to reach |
|---|---|---|
| P0 (Roskomnadzor / legal escalation) | Founder + legal advisor | `+7 ${{founder_phone}}` + `legal@gobeauty.site` |
| P1 (customer dispute escalation post-delete) | Founder | Direct DM |
| P2 (routine request) | On-call operator + cross-audit reviewer | `#ops` channel |

---

## Post-mortem template

Used after every non-trivial run (any deletion that hit blocker review, any SAR > 7 days, any cross-audit miss).

- **What happened.**
- **What was the trigger.**
- **What did we expect — what actually happened.**
- **How long did it take to detect / mitigate / resolve.**
- **What we learned.**
- **Action items** (owner + deadline).

---

## Sources + cross-refs

- Policy: `docs/design/policies/customer-privacy-data-closure-ux.md` §11 (blockers) + §13 (retained field list) + Q-CP10 (founder approval)
- ADR: ADR-0015 (post-pilot epic — cross-service privacy lifecycle)
- ADR: ADR-0011 (UserPersonalContext privacy zones — informs what bot-platform export contains)
- Customer-facing: `docs/screens/customer-profile-flow.md` R2/R3 deferred-state (Variant 3) — where this manual path is referenced from the UI
- Support entry: `customer-profile-flow.md` SupportEntrySheet (where deferred R2/R3 route customers)
- Pattern lineage: mirrors "A2 — document dual-source booking state" (Tau-owned)

---

## Changelog

- 2026-06-02 — Tau — initial draft per #937 (pilot-blocking-soft, 152-ФЗ mitigation for deferred R2/R3)
