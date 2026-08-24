# Runbook: M0 — MAX pilot tenant provisioning

> Status: **draft** (prep by S5 / ops-backstop, 2026-06-04)
> Owner: ops / operator-on-box. S5 prepares; **the live steps are run by ops** with vault credentials.
> Milestone: EPIC #1014 → **M0**. Refs: #1010 (worker), #419 (secrets), `tenant-onboarding.md`, `solo-provider-bootstrap.md`, `server-deployment.md`.
> Acceptance (M0): *the existing bot responds and is provisioned for the pilot tenants in MAX.*

## What this runbook is

A single checklist that wires the pilot salons into MAX. It does **not** restate
the full per-tenant procedure — it sequences the existing runbooks and pins the
corrections found in the 2026-06-04 pilot-readiness pass. Detailed per-tenant
steps live in [`tenant-onboarding.md`](tenant-onboarding.md) (multi-staff) and
[`solo-provider-bootstrap.md`](solo-provider-bootstrap.md) (solo).

> **This work cannot be run from a CI/dev container.** It needs production MAX
> bot tokens + webhook secrets (from the vault, per #419), prod DB access, and
> on-box `systemctl` access. Everything below is for the operator on the box.

---

## ⚠️ Corrections to the assignment brief (verified against `origin/dev`)

1. **Env-var names.** Use exactly these — two names that circulated in the
   tasking were wrong:

   | Use (correct, in code) | Do NOT use (silently ignored) |
   |---|---|
   | `MAX_WEBHOOK_SECRET` | ~~`MAX_BOT_WEBHOOK_SECRET`~~ |
   | `MAX_MINIAPP_URL` | ~~`MAX_BOT_MINIAPP_URL`~~ |
   | `MAX_BOT_TOKEN` | — |
   | `MAX_BOT_TENANT_SLUG` (single-bot) / `CHANNEL_TOKEN_TO_TENANT_SLUG` (multi-bot) | — |

   A wrong name reads as empty: ingress then rejects every webhook
   (`MAX_WEBHOOK_SECRET` empty) and the welcome keyboard has no Mini App URL.

2. **Webhook registration is a management command, not `curl`.** See Step 3.
   A hand-rolled `POST /subscriptions` that omits `update_types` drops
   `message_callback` → keyboard taps do nothing (dev incident 2026-05-21).

3. **Onboarding path depends on the salon model** (mixed for the pilot — decide
   per salon, Step 2).

---

## Step 0 — decide the tenant model per salon

| Salon shape | Path | Tool |
|---|---|---|
| Multi-master salon (several staff under one brand) | operator pre-creates the tenant row | `create_tenant` mgmt command (Step 2a) |
| Solo self-employed master | self-service: master DMs the dedicated solo bot → worker calls `create_solo_provider` | `solo-provider-bootstrap.md` (Step 2b) |

There is **no CLI wrapper for `create_solo_provider`** — by design it runs
inside the worker's `tenant_scope(bootstrap_tenant)` off an inbound MAX event,
deriving the tenant from the DMing user. Don't synthesize it from the shell.

## Step 1 — secrets + env (from the vault, per #419)

Per tenant, in 1Password under `ai-bot-platform / tenants / {slug}`, then into
`/etc/ai-bot-platform/tenants/{slug}.env`:

- `MAX_BOT_TOKEN` — the tenant's MAX bot access token.
- `MAX_WEBHOOK_SECRET` — webhook auth secret (the `X-Max-Bot-Api-Secret` value).
- `MAX_MINIAPP_URL` — Mini App **origin** for the welcome keyboard fallback:
  scheme + host, no path and no trailing `/customer` (DRF-1326 — the whole
  client path comes from `MINIAPP_ROUTES` in `apps/skills/welcome/skill.py`,
  so a base with a path doubles the prefix). On the pilot the customer
  screens answer on `proapp`, not `miniapp`.
  (And `MAX_BOT_WEB_APP` if the bot has a registered Mini App username for
  native `open_app` — that one is an app identifier on the MAX side, not a
  URL.)
- `MAX_BOT_TENANT_SLUG` (single-bot) **or** add `secret=slug` to
  `CHANNEL_TOKEN_TO_TENANT_SLUG` (multi-bot ingress).

Never paste secrets into git, tickets, or this runbook.

## Step 2a — provision a multi-staff tenant (idempotent)

```bash
python manage.py create_tenant --slug <salon-slug> --name "<Salon name>"
# --dry-run to preview; re-runs are safe (get_or_create on slug).
```

## Step 2b — provision a solo master

Follow [`solo-provider-bootstrap.md`](solo-provider-bootstrap.md). One-time ops
prerequisites (fail-loud `BootstrapTenantMissing` if skipped):
the dedicated solo bot, its token in `CHANNEL_TOKEN_TO_TENANT_SLUG`, and the
`ayla_solo_bootstrap` bootstrap tenant. Masters then self-register by DMing the
solo bot.

## Step 3 — register the MAX webhook (idempotent, includes update_types)

```bash
# On the box, tenant env loaded (MAX_BOT_TOKEN + MAX_WEBHOOK_SECRET set).
python manage.py max_subscribe_webhook \
  --url https://api.gobeauty.site/api/v1/ingress/max/
# dev: --url https://api-dev.gobeauty.site/api/v1/ingress/max/
```

DELETE+re-POSTs; always sends `message_created, message_callback, bot_started`.
Tenant is resolved by the **secret**, not the URL — single endpoint
`/api/v1/ingress/max/`, secret → slug via the map in Step 1.

## Step 4 — confirm the DM consumer (#1010) is actually running ⚠️

The MAX **DM** path needs `python -m apps.workers.consumer` draining the
`ingress:max` Redis stream. Without it, webhooks are accepted + journaled but
never processed — the bot goes silent. (The Mini App booking path is synchronous
HTTP and does **not** need this.)

**Confirmed gap — verify on the box.** Under systemd the `*-worker.service` unit
runs **Celery** (`celery -A config worker`) and `*-beat.service` is Celery beat —
neither is the MAX DM drainer. The drainer has its own unit *template*
(`infra/systemd/ai-bot-platform-consumer.service.template`, ExecStart
`python -m apps.workers.consumer --forever`), **but `server-deployment.md`
installs/enables only `web worker beat`** (`for svc in web worker beat`;
`systemctl enable …-dev{,-worker,-beat}`) — the **`consumer` unit is not
deployed**. On a box built per that runbook the DM path is dark.

```bash
systemctl is-active ai-bot-platform-dev-consumer.service   # likely: not-found / inactive
ps -ef | grep -F 'apps.workers.consumer' | grep -v grep    # expect ≥1; none → DM path dark
# Fix: render infra/systemd/ai-bot-platform-consumer.service.template into
#   /etc/systemd/system/ai-bot-platform-{ENV}-consumer.service and:
#   sudo systemctl enable --now ai-bot-platform-{ENV}-consumer
```

> **NB — deploy model affects which process is missing.** In the docker-compose
> model the `worker` service *is* the consumer (so the consumer runs, but there's
> no Celery worker/beat → reminders/scheduled tasks don't fire). Under systemd
> it's the reverse. Confirm the box's actual model first (see Open items) and fix
> whichever background process is absent.

## Step 5 — acceptance smoke

1. From a test MAX account, send `/start` (or `bot_started`) to the tenant's bot
   → canonical welcome with brand voice within ~3 s.
2. Tap a welcome inline-keyboard button → it responds (proves `message_callback`
   is flowing — the 2026-05-21 regression guard).
3. Send a FAQ query (e.g. «когда работаете») → tenant-scoped answer. If it cites
   **another** tenant's data → STOP, Sev1 (`incident-response.md`).
4. Open the Mini App from the welcome button → loads (init-data auth path).

Full master/admin Mini App smoke: `pilot-deployment-part-3-smoke-tests.md`.

M0 is done when Steps 1–5 pass for every pilot tenant.

---

## What S5 prepared vs what ops must do

- **Prepared (code/docs):** this runbook; the `tenant-onboarding.md` §3a fix
  (curl → command). The worker is already wired in `docker-compose.yml`; no
  code change needed there.
- **Ops-only (needs vault + box + live MAX):** populate secrets, run
  `create_tenant` / solo bootstrap, run `max_subscribe_webhook`, verify the
  consumer unit (Step 4), run the acceptance smoke.

## Open items for the orchestrator

- **#1010 cb2 (confirmed gap).** The systemd `*-worker.service` is Celery; the
  MAX consumer has a template (`infra/systemd/ai-bot-platform-consumer.service.template`)
  but is **absent from `server-deployment.md`'s install/enable list** (`web worker
  beat`). On a systemd box the DM drainer isn't running. Fix: add `consumer` to
  the unit install + `systemctl enable` list (and to the deploy workflow).
- **Deploy model conflict (vs #1039).** `server-deployment.md` + the full
  `infra/systemd/*.template` set describe a **systemd** dev box (gunicorn, host
  PG/Redis, "no new containers"). `deploy-dev.yml` (#1039) instead does
  `docker compose -p ai-bot-platform-dev up -d … web worker` — a parallel compose
  stack. The two models diverge on which background processes run (Celery vs
  consumer). The box is the tiebreaker: `systemctl list-units 'ai-bot-platform-dev*'`
  vs `docker compose -p ai-bot-platform-dev ps`. Resolve before activating
  auto-deploy.
