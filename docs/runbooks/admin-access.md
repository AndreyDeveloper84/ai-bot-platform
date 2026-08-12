# Runbook: Admin access (Django admin on a deployed contour)

> Status: **draft**
> Last exercised: _never (written with DRF-1023; first exercise = DRF-1023 deploy)_
> Target completion sprint: Controlled Pilot
> Owner: Platform Lead

## Purpose

Get a working login to the Django admin (`/admin/`) on an HTTPS contour
(staging/pilot: `https://api-dev.gobeauty.site/admin/`) and create an
operator account — without ever putting a password into code, git,
reports, or logs.

## ⚠ Cross-tenant warning — read before handing out ANY account

**The whole admin is cross-tenant.** Admin classes deliberately use
``all_tenants`` querysets (`apps/handoff/admin.py`,
`apps/conversations/admin.py`, plus audit / booking / catalog / consent /
experiments). Any account with admin access sees the data of **every
salon** — and `MessageAdmin` shows and searches client message **text**.

Therefore:

- Accounts are issued to the **internal team only**. NEVER to salon
  staff — they would get every other salon's tasks and client dialogs.
- Tenant-restricted operator access is a separate task (**DRF-1022**,
  operator endpoint). Until it lands, this warning is also shown as a
  banner on the AdminTask / Conversation / Message changelists.

## Trigger / when to run

- First admin login on a fresh contour (user table is empty).
- «Ошибка проверки CSRF. Запрос отклонён» on the login form (the
  DRF-1023 symptom — means `DJANGO_CSRF_TRUSTED_ORIGINS` /
  `DJANGO_BEHIND_TLS_PROXY` are not set on the contour).
- Rotating or adding an internal operator account.

## Prerequisites

- SSH access to the contour host.
- The contour runs the docker compose project (`ayla-bot-staging` on the
  pilot) with a `web` service.
- Env vars (below) present in the contour's env file
  (`.env.staging` on the pilot) and the stack restarted after editing.

## Configuration (env vars, DRF-1023)

All four default to OFF / empty — behaviour unchanged for local dev and
CI. Set on HTTPS contours only:

| Var | Pilot value | Why |
|---|---|---|
| `DJANGO_CSRF_TRUSTED_ORIGINS` | `https://api-dev.gobeauty.site` | Django requires the POST `Origin` to be trusted for HTTPS requests; empty = every admin POST 403s. Strict parsing: a malformed value refuses to boot. |
| `DJANGO_BEHIND_TLS_PROXY` | `true` | nginx terminates TLS and sets `X-Forwarded-Proto`; this pins `SECURE_PROXY_SSL_HEADER` to that header so Django knows requests are HTTPS. |
| `DJANGO_SESSION_COOKIE_SECURE` | `true` | Session cookie only over HTTPS. |
| `DJANGO_CSRF_COOKIE_SECURE` | `true` | CSRF cookie only over HTTPS. |

Deliberately NOT set:

- `SECURE_SSL_REDIRECT` — nginx already 301s 80 → 443; a Django-level
  redirect would also 301 the container's own healthcheck
  (`docker-compose.staging.yml` curls `http://localhost:8000/healthz/`
  with no `X-Forwarded-Proto`), flipping the container unhealthy.
- `ALLOWED_HOSTS` tightening — the contour runs
  `DJANGO_ALLOWED_HOSTS=*`. Verified safe to tighten to
  `api-dev.gobeauty.site,localhost,127.0.0.1` (callers: nginx with
  `Host: api-dev.gobeauty.site`, container healthcheck with
  `Host: localhost`, host-side probes to `127.0.0.1:8014`) — but do it
  as a separate, deliberate config change with a healthcheck right
  after; rollback = restore the previous value.

## Step-by-step procedure

1. **Set the env vars** in the contour's env file (pilot:
   `.env.staging` next to the compose files), then
   `docker compose -p ayla-bot-staging up -d web` (and `worker` /
   `celery-worker` / `celery-beat` if the env file is shared — it is).
   Expected: containers restart cleanly; a malformed
   `DJANGO_CSRF_TRUSTED_ORIGINS` would fail the boot — check
   `docker compose -p ayla-bot-staging logs --tail=50 web`.
2. **Verify the login form**: open
   `https://api-dev.gobeauty.site/admin/` in a browser → HTTP 200, login
   form renders (not the CSRF error page).
3. **Create the operator account** — password is entered by the account
   owner at the prompt, never written anywhere. Django's built-in
   `createsuperuser --noinput` reads the credentials from the
   environment; pass them into the container with `-e` so they never
   land in the repo, the env file, or shell history beyond this command:

   ```bash
   read -rsp "Admin username: " ADMIN_USER; echo
   read -rsp "Admin email: " ADMIN_EMAIL; echo
   read -rsp "Admin password: " ADMIN_PASS; echo
   docker compose -p ayla-bot-staging exec -T \
     -e DJANGO_SUPERUSER_USERNAME="$ADMIN_USER" \
     -e DJANGO_SUPERUSER_EMAIL="$ADMIN_EMAIL" \
     -e DJANGO_SUPERUSER_PASSWORD="$ADMIN_PASS" \
     web python manage.py createsuperuser --noinput
   unset ADMIN_USER ADMIN_EMAIL ADMIN_PASS
   ```

   Expected output: `Superuser created successfully.`
   If the username already exists the command fails with
   `CommandError: Error: That username is already taken.` — this is
   normal for re-runs; to change a password use
   `python manage.py changepassword <username>` (interactive) in the same
   container.
4. **Log in** at `https://api-dev.gobeauty.site/admin/` with the new
   account.

## Verification

- Login form → 200, login succeeds, admin index renders.
- The AdminTask changelist (`/admin/handoff/admintask/`) shows the
  yellow cross-tenant warning banner.
- `https://api-dev.gobeauty.site/healthz/` → 200 after the restart.
- Closing a task via the admin returns the conversation to the bot
  (DRF-980 service path — status RESOLVED/CANCELLED, conversation back
  to IDLE).

## Escalation contacts

| Severity | Who | How to reach |
|---|---|---|
| P0 | Platform Lead | per `on-call.md` |
| P1 | Release owner (главное окно) | file-bus REPORT/REPLY per `WINDOW_PROTOCOL.md` |

## Post-mortem template

Used after every non-trivial run.

- **What happened.**
- **What was the trigger.**
- **What did we expect — what actually happened.**
- **How long did it take to detect / mitigate / resolve.**
- **What we learned.**
- **Action items** (owner + deadline).

## Changelog

- _2026-08-12_ — DRF-1023 executor window — initial version (admin login
  fix: CSRF trusted origins + TLS-proxy flag + Secure cookies; account
  bootstrap via env-driven `createsuperuser --noinput`; cross-tenant
  warning documented and surfaced in the UI).
