# Cross-Border + Retention — Legal Review Brief

| Field | Value |
|---|---|
| **Date** | 2026-06-02 |
| **Author** | Tau (UX/Design) — assembled for legal/compliance |
| **Tracking** | #947 (cross-border disclosure), #950 R-1 (retention), #842 (PII pseudonymisation) |
| **Status** | LEGAL VERDICT REQUESTED — pilot-pre-ship gate (P-6) |
| **Source spec** | `docs/screens/customer-profile-flow.md` §4.2.1 / §4.2.2 / §4.2.3 / §4.2.6 |
| **Audience** | Legal / Compliance advisor (152-ФЗ) |

---

## 1. Purpose

We are requesting a **152-ФЗ and cross-border data-transfer legal verdict** before we open the pilot.

- **Pilot scope:** 5–10 beauty salons in Penza, **real client data**.
- **Target ship date:** **2026-07-15**.
- **What we need from you:** a yes / no / edit verdict on the customer-facing cross-border disclosure copy (r1 shipped vs r2 proposed), the data-retention wording, and the 8 specific points enumerated in §4 below.

This brief states **facts and questions only**. It does **not** assert legal conclusions. Where a claim depends on a vendor contract or backend reality not yet verified, that is flagged explicitly so legal can condition the verdict.

---

## 2. What data leaves Russia, and to whom

**Primary data residency:** Core customer data is stored on servers **in Russia** (152-ФЗ data-localisation).

**The cross-border path is the AI processing call:**

- To understand a customer's chat messages, Ayla sends **message text** to **Claude**, an LLM provided by **Anthropic (US-based provider)**, and receives the model's response.
- Transport is encrypted (the proposed copy commits to TLS 1.3 — see point 2 below for whether to keep the specific version).

**Data categories that leave Russia (to Anthropic):**

- **Message text** — the content of the customer's chat with Ayla, sent for understanding/intent recognition.

**Data categories that do NOT leave Russia (claimed in r2 — requires confirmation):**

- **Food / nutrition-diary photos** — the proposed copy states food-photo recognition runs **inside the Russian perimeter** and is **not** sent to Anthropic. (Confirm true at pilot ship — see point 4 below.)
- **Raw phone numbers / direct PII** — see §3: once #842 lands, PII is tokenised before any LLM call, so Anthropic receives **tokens, not raw personal data**.

---

## 3. Mitigation in place / in progress

**PII pseudonymisation — issue #842 (top pre-pilot blocker).**

- Before any LLM call, **phone numbers and other PII are tokenised** (pseudonymised). The downstream LLM provider (Anthropic) therefore receives **tokens, not raw personal data**.
- #842 is the **top pre-pilot blocker currently being completed**.
- **The legal position in this brief assumes #842 ships before the pilot opens.** If #842 slips, the "Anthropic receives tokens, not raw PII" assumption no longer holds and the cross-border copy must be re-reviewed.

**Encryption in transit:** transfer to Anthropic is encrypted (TLS 1.3 in the proposed copy).

---

## 4. The 8 points needing a verdict

Verbatim from `customer-profile-flow.md` §4.2.3 (#947). Pre-draft r2 (see §5) is the Tau starting point; please verify and mark up each point.

1. **Anthropic + USA framing**
   - r2: «AI-сервис от компании Anthropic (США)»
   - Question: is the country mention required at this level of disclosure, or is it sufficient to say «иностранный сервис» with detail in the privacy policy? 152-ФЗ ст.12 (transborder transfer) interpretation.
   - Alternative phrasing to consider: «зарубежный AI-сервис (Anthropic, США)» — more precise about who and where.

2. **TLS 1.3 mention**
   - r2: «шифрованием TLS 1.3»
   - Question: do we want a specific protocol version in customer-facing copy (commits us technically) or generic «современное шифрование»?
   - Tau lean: generic is friendlier; specific is more credible to a technical reviewer.

3. **Anthropic non-retention claim**
   - r2: «Anthropic обрабатывает текст в момент ответа и не хранит твою переписку у себя»
   - Question: does this match the actual Anthropic Data Processing Agreement / our contract terms? If we have a zero-retention tier, this is honest; if we use default retention, this overpromises.
   - **MUST verify with vendor contract before shipping.**

4. **Photo separation claim**
   - r2: «Не передаём фото из дневника питания на Anthropic — распознавание блюд работает внутри российского контура»
   - Question: is this true at pilot ship (food scanner uses an internal vision pipeline, no Anthropic photo path)? If a photo path adds Anthropic post-pilot, this becomes a breach — needs updating before that change.
   - Tau lean: ship the claim if true at pilot; flag a follow-up to legal if the photo-pipeline architecture changes.

5. **«никому со стороны» as replacement for «третьим лицам»**
   - Per adversarial CR (Profile Phase B PR agent `a93d90bebc68bba10`): «третьим лицам» reads as §14 legal jargon.
   - r2: «Не продаём данные никому со стороны»
   - Question: is «никому со стороны» legally equivalent to «третьим лицам» under 152-ФЗ? Or must the legal term stay?
   - Tau lean: prefer the friendly phrasing if compliance allows.

6. **Retention specifics**
   - 180 days for messages: matches `STRICT_TENANT_REFUSE` runbook + memory-layer policy. ⚠ See §6 retention audit — code-level grep shows the policy exists but **no anonymizer job is implemented**.
   - 7 years for bookings/payments: matches consumer-protection law minimum (ФЗ-2300-1 ст.10) + accounting law. ⚠ See §6 — Alpha-side reality not yet verified.
   - Memory «пока ты не удалишь»: matches `apps/skills/privacy_consent` skill behavior.
   - Audit «храним по закону»: matches 152-ФЗ ст.18 + ст.21.
   - Question: are these numbers accurate AND legally minimal (we are not retaining longer than necessary)?

7. **Tone**
   - Tau wrote in Ayla voice (warm, «ты», first-person where natural). This may need to shift to a more formal legal voice for the cross-border section specifically.
   - Question: does legal want this rewritten in third-person formal Russian («Ayla обрабатывает...»), or is first-person acceptable in this context?

8. **Withdrawal mechanics**
   - r2: «Полный отзыв = удаление аккаунта»
   - Question: under 152-ФЗ ст.9 ч.5, must we offer a more granular withdrawal path (per-purpose consent)? Or is the locked-on / toggle-off + full-delete combination sufficient?

---

## 5. Current vs proposed disclosure copy

The collapsed «Подробнее о данных» disclosure. **r1 is what customers see in the current shipped build** (`DisclosureSheet.tsx`). **r2 is the proposed expansion** pending this review.

**The question for legal: may we ship r2?** (Or: keep r1, ship r2, or ship an edited r2?)

### 5.1 r1 — shipped copy (`DisclosureSheet.tsx`)

```
Где и как обрабатываются данные

Основные данные хранятся на серверах в России (152-ФЗ).

Для понимания твоих сообщений Ayla может использовать
AI-обработку через внешних поставщиков (включая Anthropic).
Передача защищена шифрованием.

Что мы НЕ делаем:
• Не продаём данные третьим лицам
• Не используем для рекламы вне Ayla
• Не отдаём салонам без твоего разрешения

Сколько храним:
• Сообщения — 180 дней (потом анонимизируется)
• Записи и оплаты — 7 лет (требование закона)

[ Закрыть ]
```

### 5.2 r2 — Tau pre-draft (proposed, for legal review)

```
Где и как обрабатываются данные

Основные данные хранятся на серверах в России —
так требует 152-ФЗ.

Чтобы понимать твои сообщения, Ayla отправляет их в
AI-сервис от компании Anthropic (США). Передача
защищена шифрованием TLS 1.3, ответ возвращается тоже
шифрованным. Anthropic обрабатывает текст в момент
ответа и не хранит твою переписку у себя.

Что мы делаем со ВНЕШНИМИ передачами:
• Передаём только текст сообщения — без имени,
  телефона и других контактов
• Шифруем передачу TLS 1.3
• Не передаём фото из дневника питания на Anthropic —
  распознавание блюд работает внутри российского
  контура

Что мы НЕ делаем:
• Не продаём данные никому со стороны
• Не используем твою переписку для рекламы
• Не отдаём салонам ничего, кроме того, что нужно
  для записи (имя, услуга, время)
• Не показываем мастеру память Ayla или wellness-логи

Сколько храним:
• Сообщения с Ayla — 180 дней, потом анонимизируется
• Записи на услуги и оплаты — 7 лет (требует ФЗ-2300-1
  и ФЗ-54 — закон не даёт удалить раньше)
• Память Ayla про тебя — пока ты сама не удалишь
• Аудит факта удаления — храним по закону, но без
  твоего имени

Согласие можно отозвать. Полный отзыв = удаление
аккаунта (это можно сделать в разделе «Удалить
аккаунт»). Частичный — управляй переключателями
выше.

[ Закрыть ]
```

---

## 6. Retention wording to confirm (R-1)

**Background (#950 R-1):** A code-level audit found that the **180-day message-anonymisation policy exists but the backend anonymizer job is NOT implemented**. As written, the shipped r1 claim «Сообщения — 180 дней (потом анонимизируется)» is not yet backed by code — messages would be retained indefinitely. This is a truthfulness risk.

**Decision (tech-lead recommendation, founder verdict pending):** ship **honest copy now** that reflects actual customer-controlled deletion behavior, and implement the real 180-day anonymiser **post-pilot**.

**Proposed retention wording to confirm (replaces the «Сколько храним» block):**

```
Сколько храним:
• Сообщения с Ayla — пока активен твой аккаунт. Когда
  удалишь аккаунт — удаляются вместе с ним.
• Записи на услуги и оплаты — 7 лет (этого требует закон,
  записи и оплаты не удаляются раньше).
• Память Ayla про тебя — пока ты не очистишь или не
  удалишь аккаунт.
• Аудит факта удаления — храним по закону, но без
  твоего имени.
```

**Plain-English summary of the retention statement:** messages are kept while the account is active and deleted on account deletion (no fixed-period auto-anonymisation during the pilot); the 180-day anonymiser is a post-pilot follow-up.

**Question for legal:** Is this retention wording — "messages kept while account active, deleted on account deletion" (honest copy now, 180-day anonymiser post-pilot) — **152-ФЗ-acceptable**? Specifically, is right-to-erasure via customer-controlled account deletion a defensible basis here, and is the 7-year bookings/payments retention both required and not over-retained?

---

## 7. Decisions requested from legal

Please return a **yes / no / edit** verdict on each:

1. **Anthropic + USA framing** — is naming Anthropic + «(США)» in customer-facing copy required, sufficient, or should it be «иностранный/зарубежный сервис» with detail in the privacy policy? (152-ФЗ ст.12)
2. **TLS 1.3 wording** — keep the specific protocol version, or switch to generic «современное шифрование»?
3. **Anthropic non-retention claim** — may we state «не хранит твою переписку у себя»? (Conditional on confirming the vendor DPA / zero-retention tier.)
4. **Photo separation claim** — may we state food photos are not sent to Anthropic and stay inside the Russian perimeter? (Conditional on this being true at pilot ship.)
5. **«никому со стороны» vs «третьим лицам»** — is the friendly phrasing legally equivalent under 152-ФЗ, or must «третьим лицам» stay?
6. **Tone** — is first-person Ayla voice acceptable for the cross-border section, or must it be third-person formal Russian?
7. **Withdrawal mechanics** — is locked-on / toggle-off + full-delete sufficient under 152-ФЗ ст.9 ч.5, or is a granular per-purpose withdrawal path required?
8. **Retention wording (R-1)** — is "messages kept while account active, deleted on account deletion" (honest copy now, 180-day anonymiser post-pilot) 152-ФЗ-acceptable, including the 7-year bookings/payments basis?
9. **Ship verdict on r2** — may we ship the r2 expansion (§5.2) as customer-facing copy, keep r1 (§5.1), or ship an edited r2?

---

*Assumptions: this brief assumes PII pseudonymisation (#842) ships before the pilot opens. If #842 slips, the cross-border data-category claims in §2–§3 must be re-reviewed.*
