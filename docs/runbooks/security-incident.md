# Runbook: Security incident

> Status: **complete**
> Last exercised: _never_ — drill scheduled Sprint 10 / O3 smoke window
> Owner: Lead

## Purpose

Respond to a credible security incident — leaked credential, data exfiltration,
prompt-injection exploit reaching outbound, supply-chain compromise,
cross-tenant data leak — without making it worse and without leaving customers
in the dark.

This runbook is the **specialised dispatch target** when
[`incident-response.md`](incident-response.md) classifies a Sev1 as security.
Open this in parallel; the general IR runbook coordinates, this one drives the
forensic + legal track.

---

## Trigger / when to run

Any of:

* A credential / token / API key is suspected to have leaked (committed by
  mistake, screenshot in support ticket, exposed in CI log, public channel)
* A user reports the bot revealed information from another tenant
  (cross-tenant data leak — **Sev1 critical**)
* A dependency advisory (CVE) lands on a package the platform uses
* Unusual traffic spike from a single IP / user-agent / tenant (possible
  enumeration / scraping)
* Anomalous access in `apps.audit` (e.g. `tenant_scope_violation` rows
  post-STRICT-flip; admin-action on rows the admin doesn't own)
* Prompt-injection signature in outbound (LLM response includes content from
  an upstream that shouldn't be there)

If you're reading this AND none of the above happened — you're not in a
security incident. Routine credential rotation is its own runbook (TBD).

---

## Severity classification (within security)

| Sev | Trigger | Examples |
|---|---|---|
| **SEC-1** | Confirmed data leak OR active exploitation | Cross-tenant exposure observed; production credential confirmed public; live data exfiltration |
| **SEC-2** | Credible threat without confirmed exposure | Token committed but force-push within 5 min; CVE on transitive dep without exploit path |
| **SEC-3** | Hardening surface | Old token in dev env; weak password in legacy script; informational CVE |

**SEC-1 maps to Sev1 in `incident-response.md`** — open the war-room
immediately. SEC-2 maps to Sev2 with same-day fix window. SEC-3 is queued for
the next sprint.

---

## Prerequisites

* Access to 1Password (`ai-bot-platform` vault)
* GitHub admin (to revoke deploy tokens, rotate workflow secrets, force-push
  if needed)
* Database admin (psql via SSH to prod) — needed for audit log forensics
* Telegram contact for legal counsel
* Statement template (below) ready to fill

---

## Step-by-step procedure

### 1. Acknowledge + contain (within 15 min of credible signal)

**Don't investigate yet.** The first 15 minutes are about stopping the
bleeding. Investigation comes after containment.

1. Open war-room per `incident-response.md` step 2 with `SEV: 1` if SEC-1
2. **Rotate the affected credential immediately.** Don't wait for confirmation
   that it was actually exploited:
   * GitHub token → GitHub Settings → Personal access tokens → revoke + issue
     new
   * OpenAI / Anthropic key → respective console → revoke + new
   * MAX bot token → `botapi.max.ru` → reissue
   * YClients tokens → YClients admin → reissue
   * AYLA_SERVICE_TOKEN → coordinate with Ayla team for revocation
   * DB password → `ALTER USER ... WITH PASSWORD '<new>'` + update env
3. If the leak path is a **public commit**:
   * Rotate first (point 2 above) — assume the secret is harvested
   * Then force-push to remove the bad commit from history
   * **Never** rely on git history rewrite as the primary defense — bots
     scrape public repos within minutes

If the issue is **cross-tenant data leak** (not credential leak):
1. Immediately flip `STRICT_TENANT_SCOPE=audit → strict` if not already
   (per [`strict-scope-flip.md`](strict-scope-flip.md)) so further violations
   crash loudly
2. Suspend the affected tenant if there's evidence outgoing data continues:
   `tenant.is_active=False` — stops dispatch
3. Then proceed to assessment

### 2. Assess scope

Once contained, figure out what was exposed and to whom.

```sql
-- via psql on prod
-- Audit window for the leaked credential — what was done in its name?
SELECT action, target, target_id, payload, created_at
FROM audit_auditlog
WHERE created_at >= '<credential-created-or-suspected-leak-time>'
  AND created_at <= NOW()
  AND (
    user_agent LIKE '%<suspected ua>%'
    OR ip_address = '<suspected ip>'
    OR action LIKE '%<credential-suspected-feature>%'
  )
ORDER BY created_at;
```

For cross-tenant leak — find the cross-tenant accesses:

```sql
SELECT tenant_id, action, target, target_id, payload, created_at
FROM audit_auditlog
WHERE action = 'tenant_scope_violation'
  AND created_at >= NOW() - INTERVAL '24 hours'
ORDER BY created_at;
```

Document the **blast radius**:
* Which tenants are impacted?
* Which data fields were exposed? (PII / non-PII)
* How many distinct users / records?
* Is there evidence of exploitation, or only of access?

### 3. Notify

Russian law (152-ФЗ) imposes obligations on data leaks. **The notification
clock starts when you confirm a leak, not when you fix it.**

#### Internal first (within 1h)
* Lead (via PagerDuty + Telegram)
* Legal counsel (Telegram + email — Sprint 10 / Phase 1 task: get this
  contact wired to PD as an escalation target)

#### External notification (within 24h of confirmation)
* **Affected tenants** — use the customer notification template below
* **Affected end-users** (if PII exposed) — via the tenant's manager
  channel; we don't message end-users directly without consent

#### Regulatory (per 152-ФЗ)
* **Roskomnadzor** — within **24 hours** of detection if PII of >1000 subjects
  potentially affected; within 72h regardless of scale
* Form: [official template](https://eais.rkn.gov.ru) — Lead files; legal
  reviews before submit
* **Банковские регуляторы** (if payments data touched — even via YooKassa
  webhook history): notify within 48h
* Cross-border data transfer scenarios: separate notification (only if Ayla
  hosts data outside RF — currently it doesn't)

### 4. Customer notification template

Use this scaffold (bilingual) and have legal review before send. Don't
improvise wording for security incidents.

```
[RU]
Уважаемые клиенты,

Мы обнаружили инцидент безопасности, который затронул вашу учётную запись.

Что произошло: [одно предложение фактов — никаких догадок, никаких
"возможно"]

Что данных могло быть затронуто: [конкретные категории — телефон, имя,
история записей. НЕ упоминать данные которые НЕ затронуты — это запутывает]

Что мы сделали: [конкретные действия — ротация ключей, аудит]

Что мы рекомендуем вам: [конкретные действия пользователя — обычно ничего
если данные не давали злоумышленнику доступ к их аккаунтам в других сервисах]

Если у вас вопросы — пишите в [contact channel]. Мы ответим в течение 24
часов.

С уважением,
Команда ai-bot-platform
Инцидент №[ID] от [date]

---

[EN]
Dear customers,

We have identified a security incident affecting your account.

What happened: [one-sentence fact — no speculation, no "possibly"]

What data may have been affected: [specific categories — phone, name,
appointment history. Don't list data NOT affected — it confuses the reader]

What we have done: [specific actions — key rotation, audit]

What we recommend you do: [specific user actions — usually none unless the
breach gives attackers access to their accounts on other services]

Questions? Please write to [contact channel]. We'll respond within 24 hours.

Sincerely,
ai-bot-platform team
Incident #[ID] of [date]
```

**Do not** include in the message:
* Internal incident IDs that map to source code (file paths, function names)
* Technical specifics of HOW the leak happened (attackers read these too)
* Apologetic language that could be construed as admission of negligence in
  later litigation. Stick to facts.

### 5. Preserve audit trail

The audit log is your **legal defense + improvement input** — protect it.

```bash
# On prod, immediately after containment — pg_dump the audit window
ssh prod 'pg_dump -U taximeter -t audit_auditlog \
  --where="created_at >= '"'"'<leak-window-start>'"'"'" \
  > /tmp/incident-<ID>-audit-$(date +%Y%m%d).sql'
# Then SCP to long-term storage (NOT same host as prod)
```

Also pull related data:
* `apps.replay` traces in the window (`apps_replay.replaytrace` table)
* nginx access logs (`/var/log/nginx/access.log*`)
* Application logs (`docker compose logs web worker --since <start>`)
* `mysite/maxbot/` AuditLog (cross-correlation if the leak path involved the
  legacy bot)

Store the archive offline (NOT in S3 / cloud) — paper-trail integrity.

### 6. Investigate (after containment)

Now reproduce the leak path in a controlled environment. Goals:
* Confirm root cause class:
  * **Human error** (committed secret, misconfigured permission)
  * **Supply-chain** (compromised dependency)
  * **Weak control** (missing rate limit, weak password, no MFA)
  * **Misconfiguration** (PII in log fields, wrong CORS, exposed admin endpoint)
* Identify what needs fixing beyond the immediate credential rotation
* Capture forensic evidence (logs, screenshots, repro steps)

### 7. Post-mortem (within 5 business days)

Use the template in [`_template.md`](_template.md) **plus** security-specific
sections:

* **Timeline:** Detection → containment → notification → investigation
  (timestamped to the minute for the regulatory file)
* **Root cause class** (one of the four above)
* **Follow-up actions** (with Linear tickets + deadlines + owners):
  * Technical fix (the obvious one)
  * Detection improvement (would we have caught this earlier with better
    monitoring? File a ticket.)
  * Prevention (would a different control prevent recurrence? File a ticket.)
  * Process change (does on-call / onboarding / review need updating?)
* **Regulatory filing** confirmation + reference numbers
* **Customer comms** sent + responses + escalations

Publish in `docs/postmortems/security/YYYY-MM-DD-<slug>.md`. Mark security
post-mortems as restricted (Lead-only access until legal sign-off).

---

## Key rotation — order matters

If the suspected leaked credential is one of several related keys, rotate in
this order to avoid cascading downtime:

1. **External-facing first** — anything an attacker can use to call our APIs:
   MAX webhook secret, AYLA_SERVICE_TOKEN (we hold it, but Ayla validates)
2. **Outbound credentials** — keys we use to call external services: OpenAI,
   Anthropic, YooKassa, YClients
3. **Internal-only credentials** — DB password, deploy tokens, internal HMAC
   secrets
4. **Long-tail** — SSH keys, GitHub deploy tokens, monitoring API keys

After each rotation, smoke-test that the dependent system still works before
moving to the next. **Don't rotate everything at once** — debugging a
multi-service outage during a security incident is the worst combination.

---

## Verification

You can call the security incident **handled** when **all** of:

* New credentials live; old credentials reject all calls (smoke against
  issuer's API)
* Audit logs show no further suspicious access in the credential's window
* All instances of the leaked credential scrubbed from public surfaces
  (GitHub, Slack archive, Telegram exports, screenshots in support tickets)
* Customer notification sent + acknowledged (or no response received within
  72h, which is acceptable per template)
* Regulatory filing reference number recorded (if applicable)
* Post-mortem scheduled in Linear
* Audit-trail archive verified accessible from long-term storage

---

## Escalation contacts

| Severity | Who | How to reach |
|---|---|---|
| SEC-1 (active leak / exposure) | Lead + Legal | PagerDuty critical page + direct call |
| SEC-2 (credible threat) | Lead | PagerDuty error page + Telegram |
| SEC-3 (hardening) | Lead | Telegram (next-business-day OK) |

Phase 1 carry-over (DRF-859 PII filter) — formalise per-vendor security
contacts here (OpenAI security@, Anthropic, YooKassa fraud, MAX security).

---

## Anti-patterns — don't do these

1. **Investigate before contain.** Looking for "how did it leak" while the
   credential is still valid lets the attacker keep accessing. Rotate FIRST.
2. **Public apology before legal review.** Especially under 152-ФЗ — phrasing
   matters legally. Stick to the template, get sign-off.
3. **"Probably not exploited" decision-making.** If you confirmed access, you
   confirmed exposure. Treat as exploited until forensics show otherwise.
4. **Rotating in parallel without smoke.** Cascade outages during security
   incidents are catastrophic — careful, sequential rotation.
5. **Skipping the regulatory clock.** 152-ФЗ has hard deadlines (24h / 72h).
   Late filing turns a security incident into a regulatory incident with
   fines.

---

## Related runbooks

* [`incident-response.md`](incident-response.md) — general incident dispatcher
  (security incidents flow through here for war-room + comms)
* [`strict-scope-flip.md`](strict-scope-flip.md) — emergency STRICT flip if
  cross-tenant leak detected pre-flip
* [`tenant-onboarding.md`](tenant-onboarding.md) — per-tenant secret
  provisioning; rotation procedure mirrors onboarding
* [`on-call.md`](on-call.md) — escalation routing (Sprint 10 / O3)
* [`rollback-procedure.md`](rollback-procedure.md) — if the leak was
  introduced by a recent deploy

---

## Related Phase 1 backlog

* DRF-859 PII filter — defense-in-depth for log emission (reduces blast radius
  of future leaks)
* DRF-852 Postgres backups + PITR — needed for "restore to pre-leak state" in
  worst case
* DRF-860 Token / cost ceiling per tenant — detection signal for runaway
  leaked credential abuse

---

## Changelog

* 2026-05-10 — Lead — partial skeleton (reporting + containment filled) (DRF-414)
* 2026-05-14 — Lead — full version (Sprint 10 / O6 / DRF-867)
