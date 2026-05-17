# Runbook: Onboard a Telegram bot to a tenant

> Status: **draft**
> Last exercised: _never (initial port)_
> Target completion sprint: _Phase 1 / CH1 (DRF-848)_
> Owner: _Platform Ops_

## Purpose

Connect a Telegram bot (one per tenant) to the platform's Telegram
channel adapter so customers can chat with the tenant's salon
assistant on Telegram. Configures the per-tenant BotFather token, the
webhook URL with secret-token auth, and verifies end-to-end delivery.

## Trigger / when to run

- New tenant onboarding that wants Telegram alongside MAX
- Rotating a compromised Telegram bot token
- Migrating a tenant's bot from the legacy `mysite/agents/telegram.py`
  loop to the platform adapter

## Prerequisites

- Telegram account with permission to create / manage the tenant's bot
- Production shell access on the platform host (or Django management
  shell access via Conductor)
- `TELEGRAM_PROXY` (or `OPENAI_PROXY` as fallback) configured in the
  platform's environment — `api.telegram.org` is blocked from Russian
  IPs and outbound calls fail silently without a proxy
- The tenant row already exists in `apps_tenancy_tenant` (use
  `manage.py create_tenant` if not)
- Telegram bot is NOT registered with any other webhook URL (long-poll
  or competing webhook) — Telegram silently drops conflicting registrations

## Step-by-step procedure

### 1. Get a bot token from @BotFather

Open Telegram, talk to `@BotFather`:

```
/newbot
<bot display name>
<bot username, must end in 'bot'>
```

BotFather replies with a token shaped like `1234567:ABCdefGhi...`.
Treat this string as a production credential — never paste it into
Slack / Linear / git.

Optional (recommended): set the bot's description, about, profile
photo, and commands via `/setdescription`, `/setabouttext`,
`/setuserpic`, `/setcommands`. None affect platform behaviour, but
they shape first-impression UX.

### 2. Generate a webhook secret and store both on the tenant row

```shell
# On the production host (or `make manage shell` locally):
python manage.py shell
```

```python
from apps.tenancy.models import Tenant
import secrets

t = Tenant.all_objects.get(slug="formula_tela")  # ← tenant's slug
t.telegram_bot_token = "1234567:ABCdefGhi..."     # ← from BotFather
t.telegram_webhook_secret = secrets.token_urlsafe(32)
t.save()

# Capture the secret for step 3 — it's needed for setWebhook.
print(t.telegram_webhook_secret)
```

**SECURITY**: `telegram_bot_token` is masked in the Django admin's
list view (last 4 chars only) and in `Tenant.__repr__`. Do not paste
it into Sentry, audit notes, or post-mortems.

### 3. Register the webhook with Telegram

Build the public-facing webhook URL:

```
https://<platform-host>/api/v1/channels/telegram/webhook/<tenant-slug>/
```

Then call Telegram's `setWebhook` (via curl through the proxy):

```shell
TOKEN="1234567:ABCdefGhi..."                  # bot token
SECRET="<paste secret from step 2>"            # webhook secret
URL="https://platform.example.com/api/v1/channels/telegram/webhook/formula_tela/"
PROXY="$TELEGRAM_PROXY"                        # or $OPENAI_PROXY

curl -x "$PROXY" -s \
  "https://api.telegram.org/bot${TOKEN}/setWebhook" \
  -H 'Content-Type: application/json' \
  -d "$(cat <<EOF
{
  "url": "${URL}",
  "secret_token": "${SECRET}",
  "max_connections": 40,
  "allowed_updates": ["message", "callback_query"]
}
EOF
)"
```

Expected output: `{"ok":true,"result":true,"description":"Webhook was set"}`.

Why `allowed_updates`: Telegram defaults to sending every update type
(channel_post, edited_message, poll, chat_member, etc.); narrowing to
the two we handle reduces incoming traffic ~5x and avoids waking the
handler for updates it would discard anyway.

### 4. Verify the webhook is healthy via getWebhookInfo

```shell
curl -x "$PROXY" -s \
  "https://api.telegram.org/bot${TOKEN}/getWebhookInfo"
```

Expected response shape:

```json
{
  "ok": true,
  "result": {
    "url": "https://platform.example.com/api/v1/channels/telegram/webhook/formula_tela/",
    "has_custom_certificate": false,
    "pending_update_count": 0,
    "last_error_date": <absent or 0>,
    "max_connections": 40,
    "allowed_updates": ["message", "callback_query"]
  }
}
```

Decision branch:

- `last_error_date` set + `last_error_message` mentions 403 → the
  webhook secret doesn't match (re-check step 2 and step 3 used the
  SAME `secret_token`).
- `last_error_message` mentions SSL / connection → the public URL
  isn't reachable from Telegram's network (check DNS, certificate,
  nginx routing).
- `pending_update_count` > 0 and growing → handler is broken or slow;
  see step 6 troubleshooting.

### 5. Test `/start` from a real Telegram client

Open Telegram, find the bot by username, tap **Start**. Within ~3
seconds you should see the platform's welcome reply (skill-dispatched
through the LLM concierge / FAQ skill / echo fallback depending on
configured skills).

If no reply arrives:

- Check the platform logs for the request: search for `tenant=<id>`
  + `channels.telegram.handler.received`.
- Check `last_error_message` via `getWebhookInfo` (step 4).
- Verify `TELEGRAM_PROXY` is set in the worker / web container env.

### 6. Troubleshooting

| Symptom                                                       | Likely cause                                                                 | Fix                                                                                                                       |
| ------------------------------------------------------------- | ---------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| Telegram client shows the spinner on every button forever     | `answerCallbackQuery` failing (no proxy or wrong token)                      | Check `TELEGRAM_PROXY`. Confirm token is the same on tenant row as registered with `setWebhook`.                          |
| Webhook returns 403 in `getWebhookInfo`                       | `X-Telegram-Bot-Api-Secret-Token` header mismatch                            | Audit query: `SELECT * FROM apps_audit_auditlog WHERE action='telegram.webhook.bad_signature' ORDER BY created_at DESC;`  |
| Webhook returns 404 in `getWebhookInfo`                       | Tenant slug typo in URL, OR tenant has empty `telegram_bot_token`/`secret`   | Verify the Tenant row in admin — both fields must be non-empty.                                                           |
| Replies don't arrive but no errors in logs                    | Missing `TELEGRAM_PROXY` in production                                       | `kubectl exec` / SSH and check `env \| grep TELEGRAM_PROXY` on the worker container. Roskomnadzor blocks `api.telegram.org`. |
| Outbound logs `channels.telegram.outbound.http_error`         | Token revoked / bot blocked by user                                          | Inspect the `body=` field in the log line for Telegram's error description.                                               |
| Handler dispatches reply but Telegram still retries           | Webhook view returned non-200                                                | Should be impossible — view returns 200 even on handler crash. Check nginx / WSGI logs for 5xx between Telegram and Django. |

## Verification

The bot is healthy when ALL of the following hold:

1. `getWebhookInfo` shows `pending_update_count: 0` and no
   `last_error_date`.
2. A `/start` from a real client elicits a reply within 3 seconds.
3. A button tap (e.g. on a reminder card) shows the spinner clearing
   within 1 second AND triggers the booking-flow follow-up.
4. The audit log shows `identity.bot_user.created` for the test user
   with `channel="telegram"`.

Time-to-stable target: under 15 minutes from the BotFather token in
hand to a working `/start`.

## Rotation procedure (token compromised)

If a bot token leaks:

1. In Telegram, message `@BotFather`, `/revoke`, pick the bot. Old
   token is invalidated immediately.
2. BotFather replies with a new token.
3. Repeat step 2 + step 3 of this runbook with the new token. Do NOT
   reuse the old `telegram_webhook_secret` — generate a new one with
   `secrets.token_urlsafe(32)`.
4. Audit log audit: `SELECT created_at, action, payload FROM apps_audit_auditlog WHERE action LIKE 'telegram.%' AND created_at > now() - interval '7 days' ORDER BY created_at DESC;`

## Escalation contacts

| Severity | Who                       | How to reach                |
| -------- | ------------------------- | --------------------------- |
| P0       | Platform on-call          | `#platform-oncall` Telegram |
| P1       | Tenant ops lead           | tenant Slack channel        |
| Vendor   | Telegram (no human SLA)   | https://telegram.org/support |

## Post-mortem template

Used after every non-trivial run.

- **What happened.**
- **What was the trigger.**
- **What did we expect — what actually happened.**
- **How long did it take to detect / mitigate / resolve.**
- **What we learned.**
- **Action items** (owner + deadline).

## Changelog

- 2026-05-17 — Initial draft for the Phase 1 / CH1 (DRF-848) Telegram
  channel adapter port.
