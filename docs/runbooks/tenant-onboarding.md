# Runbook: Tenant onboarding

> Status: **complete**
> Last exercised: _never_ — first non-`formula-tela` onboarding planned Phase 1
> Owner: Lead

## Purpose

Bring a new tenant from "we have a signed contract" to "their bot is live in
MAX (and other channels)" — **without** losing data, leaking another tenant's
data, or breaking production for existing tenants.

Target end-state after this runbook: tenant can `/start` the bot from MAX and
receive the canonical welcome reply, scoped to their brand voice + catalog +
KB.

Time budget: **< 30 min** for a salon with structured catalog source (mysite
admin or YClients export). Up to a few hours for salons that require manual
catalog import (Phase 5 importers — out of scope here).

---

## Trigger / when to run

* Sales has a signed contract + technical onboarding kickoff date
* Tenant has confirmed channel credentials (MAX bot token, optional Telegram
  bot token, optional web widget allowlist)
* Tenant has YClients API credentials if booking integration is in scope
* Tenant has agreed to 152-ФЗ data-processing terms

If any of the above is missing — **don't start**. Onboarding without consent
or credentials creates orphan rows and a security audit headache.

---

## Prerequisites

* Platform admin access (`is_staff=True`)
* `prod.env` — secrets vault (1Password "ai-bot-platform / prod")
* MinIO bucket pattern: `tenant-{slug}-media` (for photo storage — Phase 1
  channel adapter integration)
* Linear project template for tenant-specific issues
* This runbook's checklist (below)

---

## Step-by-step procedure

### 1. Provision the Tenant row

```python
# Django shell on prod
from apps.tenancy.models import Tenant
tenant = Tenant.objects.create(
    slug="acme-salon",          # URL-safe, immutable; this is the audit anchor
    display_name="ACME Beauty",
    is_active=True,             # explicit, not default
    shadow_mode=False,          # only for canary observability scenarios
)
```

**Rules:**
* `slug` is immutable. Pick wisely — it appears in audit rows, log lines,
  ChromaDB collection names, MinIO bucket names. Changing it later is a
  multi-day migration.
* `slug` must match `^[a-z0-9-]+$` (lowercase, digits, hyphens). No
  underscores, no Unicode.
* `is_active=True` by default — the row is queryable from day 1. To stage a
  silent provision use `is_active=False` then flip after smoke.

### 2. Provision secrets

For each tenant, create the following in 1Password under
`ai-bot-platform / tenants / {slug}`:

| Secret | Where used | Rotation |
|---|---|---|
| `MAX_BOT_TOKEN` | `apps.channels.max` outbound | per tenant; rotate annually |
| `MAX_WEBHOOK_SECRET` | nginx auth header `X-Max-Bot-Api-Secret` | per tenant; rotate annually |
| `YCLIENTS_PARTNER_TOKEN` | booking client (Phase 1 / DRF-837) | when YClients rotates |
| `YCLIENTS_USER_TOKEN` | same | same |
| `YCLIENTS_COMPANY_ID` | same | static |
| `AYLA_SERVICE_TOKEN` | nutrition client (`apps.integrations.ayla`) | rotate when revoked |

Set via per-tenant env file `/etc/ai-bot-platform/tenants/{slug}.env`. The
platform settings module reads tenant-scoped env via the slug → file mapping
(implemented in Sprint 1 / DRF-410).

### 3. Configure channels

#### 3a. MAX (primary for Phase 0)

Register the webhook with the **management command — never a hand-rolled
`curl`**. The command DELETE+re-POSTs (idempotent) and *always* sends
`update_types`. A raw `POST /subscriptions` that omits `update_types` makes MAX
deliver only a subset of events — `message_callback` updates are silently
dropped, so inline-keyboard taps appear to do nothing (dev incident
2026-05-21). It reads `MAX_BOT_TOKEN` (auth) + `MAX_WEBHOOK_SECRET` from the
tenant's loaded env and refuses to run if either is empty.

```bash
# On the box, with the tenant's env loaded (MAX_BOT_TOKEN + MAX_WEBHOOK_SECRET).
python manage.py max_subscribe_webhook \
  --url https://api.gobeauty.site/api/v1/ingress/max/
# dev box:   --url https://api-dev.gobeauty.site/api/v1/ingress/max/
# --secret defaults to settings.MAX_WEBHOOK_SECRET; pass --secret to override.
# default update_types: message_created, message_callback, bot_started.
```

Tenant resolution is by **secret, not URL path.** There is a single ingress
endpoint, `/api/v1/ingress/max/` (no per-tenant slug in the path). MAX sends
the `X-Max-Bot-Api-Secret` header on every webhook; `apps/ingress/views.py`
matches it against `MAX_WEBHOOK_SECRET` and resolves the tenant from
`CHANNEL_TOKEN_TO_TENANT_SLUG` (multi-bot map) or `MAX_BOT_TENANT_SLUG`
(single-bot mode). So the per-tenant binding lives in the secret→slug map, not
the webhook URL.

#### 3b. Telegram (Phase 1 / DRF-848)

Skip in Phase 0 — Telegram channel adapter ships Phase 1.

#### 3c. Web widget (Phase 1 / DRF-850)

Skip in Phase 0.

### 4. Yandex Maps profile (free acquisition funnel)

If the salon has a Yandex Maps business listing:

1. Open Yandex Business admin
2. Section "О компании" → "Данные" → block "Сайт и социальные сети"
3. Add MAX profile link: `https://max.ru/{your-bot-handle}`
4. Save

This makes the bot discoverable from Yandex Maps AI chat (per
2026-05-14 Yandex update). One-time setup, ongoing free traffic.

### 5. Seed the KB

For Phase 0 / `formula-tela` tenant, seed from mysite import (Sprint 7 /
DRF-619 migration command). For a fresh salon:

* If global KB scope is live (Phase 1 / DRF-886+) — every new tenant
  automatically inherits the curated procedure/contraindication KB
* Salon-specific FAQ chunks (hours, address, parking) — load via
  `apps/adminconsole/` per-tenant KB editor

For Phase 1+ this step uses Phase 5 importers (Excel/PDF/Sheets) — out of
scope here.

### 6. Configure brand voice (optional)

Default voice: `FORMULA_TELA_VOICE` (warm, terse, no corporate-speak). For
salons that want a different voice, set in
`apps/promptreg/` admin → BrandVoiceConfig per tenant.

Sprint 9 / D1 ships only the default voice; per-tenant overrides land in
Phase 1.

### 7. Run smoke test

**Without this step, onboarding is not complete.** The smoke proves the
end-to-end path works for THIS specific tenant.

#### 7a. Send `/start` via MAX

Use the tenant's MAX bot from any test account (your own works). Expected
reply within 3 s: canonical welcome text with brand voice.

If the bot times out or replies with the wrong tenant's content — **abort and
debug before proceeding**. Common causes:

* Webhook URL has wrong slug → check nginx route
* `MAX_WEBHOOK_SECRET` mismatch → check the auth header
* Tenant's `is_active=False` → flip to True
* No active Conversation → check `apps.conversations` migration

#### 7b. Send a FAQ query

Type: "когда работаете". Expected: response cites tenant hours. If it cites
ANOTHER tenant's hours — **STOP IMMEDIATELY**, this is a STRICT scope
violation. File a Sev1 per `incident-response.md`.

#### 7c. Send a callback simulation

Use the admin panel to inject a test message simulating `cb:request_contact`.
Confirm the response is the tenant's branded message.

### 8. Enable monitoring + alerts

* Add tenant slug to PagerDuty alert filters (`apps/observability/alerting.py`
  routes based on slug per the dedup_key field)
* Subscribe Lead Telegram + the tenant's manager Telegram (if provided) to
  per-tenant alert chats — see `on-call.md` for the escalation chain
* Verify the tenant appears in `/api/agents/health/` per-tenant slice

### 9. Communicate go-live to the tenant

Send the tenant manager:

1. Their MAX bot link (handle + QR code)
2. The 5-minute "first walkthrough" template:
   * Open the bot in MAX
   * Type "/start" — see the welcome
   * Try "когда работаете" — see hours
   * Try "цены на массаж" — see catalog
   * Try sending a photo of a dish (if nutrition flow is enabled)
3. Escalation contacts: who do they call when the bot misbehaves at 22:00?
   (See `on-call.md`.)

---

## Verification

You can call onboarding **complete** when **all** of:

* `/start` smoke green (step 7a)
* FAQ smoke green AND scoped to this tenant (step 7b)
* Callback smoke green (step 7c)
* `apps.audit` log shows **zero** cross-tenant warnings during the smoke
  session (replay last 100 audit rows via admin filter)
* Tenant manager has acknowledged go-live message + escalation contacts
* Tenant is added to PagerDuty + Telegram alert filters
* Linear project (if applicable) has tenant onboarding ticket marked Done

---

## Rollback / de-provisioning

**Soft path** (preferred — preserves audit trail):

```python
tenant = Tenant.objects.get(slug="acme-salon")
tenant.is_active = False
tenant.save()
```

This stops dispatch immediately (the middleware filters on `is_active`) but
keeps the data for forensic audit. Channel webhook still receives messages
but the orchestrator short-circuits with "tenant is inactive" + audit row.

**Hard path** (only after legal sign-off — 152-ФЗ right to be forgotten):

1. Soft-delete first (above), wait 30 days
2. Run `python manage.py purge_tenant --slug acme-salon --confirm`
3. The command:
   * CASCADE-deletes BotUsers + Conversations + Messages (per Sprint 1 FK)
   * Drops ChromaDB collection `tenant-{slug}-{model_version}`
   * Wipes MinIO bucket `tenant-{slug}-media`
   * Removes Tenant row
   * Writes a single audit row `tenant.purged` for the forensic trail
4. Rotate ANY shared secrets touched by this tenant (paranoid; the secrets
   were tenant-scoped, but rotation defends against compromised admin
   credentials)

**Never** hard-delete without the 30-day soft-delete window. The audit log is
needed for legal disputes + 152-ФЗ compliance.

### Channel-side cleanup

Independent of platform de-provisioning:

* MAX: revoke the bot token + delete the webhook on `botapi.max.ru`
* Telegram / WhatsApp: revoke per-channel
* Yandex Maps: remove MAX link from business profile
* YClients: revoke `partner_token` / `user_token`

---

## Escalation contacts

Provisioning failures during onboarding → **Lead** (PagerDuty page or direct
Telegram). Don't open an incident-response runbook for an onboarding failure
unless it's actively affecting OTHER tenants — onboarding is internal-only
until smoke green.

After go-live, escalations follow `on-call.md` (per-tenant alert routing).

---

## Anti-patterns — don't do these

1. **Skipping smoke** because "tenant config matches another tenant we did
   before". Smoke is the only thing that catches webhook misroute, secret
   mismatch, or tenant data leak. **Always smoke.**
2. **Reusing slugs**. Even after hard-delete, slugs stay reserved for
   90 days (audit trail anchor). Pick a new slug.
3. **Provisioning + go-live in same business day**. Soak the provision
   overnight where possible — find issues during low-traffic hours.
4. **Sharing secrets across tenants**. Every tenant gets its own MAX bot
   token, its own AYLA_SERVICE_TOKEN, its own YClients credentials. No
   "let's just use Формула тела's token for now".

---

## Related runbooks

* [`on-call.md`](on-call.md) — per-tenant alert routing post-go-live
* [`incident-response.md`](incident-response.md) — what to do when a live
  tenant breaks
* [`security-incident.md`](security-incident.md) — if cross-tenant leak
  observed during smoke (Sev1, immediate)
* [`strict-scope-flip.md`](strict-scope-flip.md) — STRICT mode is mandatory
  for multi-tenant; never onboard with `audit` mode in prod
* [`shadow-mode-launch.md`](shadow-mode-launch.md) — for shadow mode
  observability tenants

---

## Changelog

* 2026-05-10 — Lead — skeleton committed (DRF-414)
* 2026-05-14 — Lead — full version (Sprint 10 / O4 / DRF-865)
