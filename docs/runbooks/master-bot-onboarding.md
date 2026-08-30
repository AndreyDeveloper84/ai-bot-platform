# Runbook — Master M0 onboarding (manual test + ops)

Status: living document (PR 1 / M0). Update as later master PRs land.

## What this covers

- How to open the master/customer Mini App in a plain Chrome tab using the
  **DEBUG-only init-data bypass** (new — replaces the prior "no bypass"
  policy).
- How to manually exercise the master M0 onboarding flow against a real
  dev environment.
- How to recover a stuck invite (expired / lost link).

## ⚠️ Local browser dev (dev-bypass) — SECURITY-CRITICAL

> **Bypass работает только в DEBUG=True. Никогда не используется в prod.
> Не коммитьте `.env.local`.**
>
> The dev-bypass is a **DEBUG-only, header-opt-in, loudly-logged** path that
> lets developers open `/onboarding/master?token=...`,
> `/master/dashboard`, and customer screens in a regular Chrome tab. In
> production (`DEBUG=False`) the bypass code path is **dead code** —
> `apps/miniapp_api/dev_bypass.py::try_dev_bypass` exits before reading any
> header. Every successful bypass writes a `WARNING` log line; rejected
> bypass attempts also log. There is no `ALLOW_DEV_BYPASS` setting — DEBUG
> is the canonical gate.

### Step-by-step

1. **Start the backend with DEBUG=True**:
   ```
   DJANGO_DEBUG=True uv run python manage.py runserver
   ```

2. **Create a PENDING invite + placeholder BotUser** in one shot. The
   `--bootstrap-bot-user` flag resolves the chicken-and-egg problem
   below (M0 `/claim` needs a BotUser to exist):
   ```
   python manage.py create_test_master_invite \
       --tenant formula-tela \
       --name "Анна Петрова" \
       --max-handle anna_styl \
       --bootstrap-bot-user
   ```

   Output includes a ready-to-paste `.env.local` snippet:
   ```
   VITE_DEV_BYPASS_USER_ID=<placeholder_bot_user_uuid>
   VITE_DEV_BYPASS_TENANT_SLUG=formula-tela
   ```

3. **Paste the snippet into `apps/miniapp/.env.local`**
   (copy `apps/miniapp/.env.local.example` for the header comments).
   `.env.local` is `.gitignore`d — do not commit.

4. **Start the Vite dev server**:
   ```
   cd apps/miniapp && npm run dev
   ```

5. **Open the web URL** from step 2 in Chrome:
   `http://localhost:5173/onboarding/master?token=<token>`

   The frontend (`apps/miniapp/src/lib/dev-bypass.ts`) detects:
   - `import.meta.env.DEV === true` (Vite dev mode)
   - No real MAX initData in `window.WebApp` (plain Chrome tab)
   - Both `VITE_DEV_BYPASS_*` env vars present

   Then injects three headers on every API request:
   - `X-Dev-Bypass: 1`
   - `X-Dev-User-Id: <bot_user_uuid>`
   - `X-Dev-Tenant-Slug: <tenant_slug>`

   The backend resolves the BotUser + Tenant from these headers and skips
   HMAC verification. Each request emits a `WARNING [DEV-BYPASS] init-data
   validation skipped: ...` log line so the bypass can never be quietly
   running.

6. **Complete M0 onboarding** (steps 1/2/3) → the master row's
   `linked_bot_user` is now the placeholder BotUser from step 2.

7. **Refresh `.env.local` to use the linked BotUser** (optional —
   placeholder works too, but this snaps the dev environment to the
   real linked identity):
   ```
   python manage.py print_master_dev_env <master_id>
   ```
   Paste the printed snippet into `apps/miniapp/.env.local`, restart
   `npm run dev`. Subsequent requests use the linked BotUser id.

### Catch-22 nuance (M0 bootstrap)

The `/api/v1/master/onboarding/claim` endpoint runs through
`require_init_data_only`, which still needs to resolve a BotUser from the
init-data — or, with the bypass, from the dev headers. Before M0,
the master has no `linked_bot_user`, and typically no BotUser row exists
at all for a fresh dev environment.

Two resolutions documented above:

- `create_test_master_invite --bootstrap-bot-user` (recommended for fresh
  dev tenants) — pre-creates a placeholder BotUser
  (`channel_user_id="dev-bypass-<master_id>"`) so the dev-bypass headers
  resolve immediately.
- `print_master_dev_env <master_id>` (post-M0) — emits the snippet using
  the actual `linked_bot_user`, useful after the master has accepted.

### Header precedence (real init-data vs dev headers)

When both a valid `Authorization: MaxInitData` header AND
`X-Dev-Bypass: 1` are present (rare — happens if you open the dev URL
inside MAX), **the dev-bypass wins**. Rationale: the caller explicitly
opted into dev mode by sending the bypass header. The opposite policy
would mean "init-data silently overrides the bypass", which breaks the
"headers always work in dev" invariant. Pinned by
`apps/miniapp_api/tests/test_dev_bypass.py::test_real_init_data_plus_dev_headers_bypass_wins`.

### Admin endpoints

The bypass is wired into `require_init_data` (customer surface) and
`require_master_init_data` / `require_init_data_only` (master surface).
**Admin endpoints (`apps/admin_api/auth.py`) are NOT bypassed** —
strict HMAC even in DEBUG, so accidentally testing owner/admin flows
without proper MAX session is impossible. If you need to dev the admin
Mini App, use the `VITE_DEV_INIT_DATA` workaround in
`apps/miniapp/src/lib/max-sdk.ts::getInitData` with a hand-signed
init-data string.

## Real admin invite flow (preferred — PR 3 / MM2)

This is the production-shape flow. Prefer this over the test management
command (see deprecated section below) — it exercises the audit trail,
the MAX DM dispatch, and the idempotency guard the same way a real owner
would in the admin Mini App.

Prerequisites:
- A tenant exists in the dev DB.
- The calling MAX user has a `TenantStaff` row with `role=owner` OR
  `role=admin` for this tenant (or run `seed_dev_formula_tela
  --max-user-id <your_max_id>` which sets up an owner row).
- A real MAX `initData` header for HMAC verification — easiest path is
  to call from the admin Mini App; for raw curl during dev you can
  re-use the helper at `apps/admin_api/tests/conftest.py::_sign`.

Endpoint: `POST /api/v1/admin/masters/invite`

```bash
curl -X POST http://localhost:8000/api/v1/admin/masters/invite \
    -H "Authorization: MaxInitData <signed-initdata-string>" \
    -H "Content-Type: application/json" \
    -d '{
      "name": "Анна Петрова",
      "contact_method": "max_username",
      "contact_value": "@anna_styl",
      "services": ["<service_uuid>", "..."],
      "schedule_preset": "default_mon_fri_10_19",
      "mode": "invite"
    }'
```

Response on success (HTTP 201):

```json
{
  "master_id": "<uuid>",
  "invite_token": "<uuid>",
  "invite_expires_at": "2026-05-27T17:00:00+00:00",
  "max_dm_delivery": "queued",
  "fallback_link": "http://localhost:5173/onboarding/master?token=<token>",
  "invite_link": "https://max.ru/<salon_bot>?start=master_invite_<token>"
}
```

Side effects (all rolled back atomically on any failure):

- `CatalogMaster` created — `invite_status=pending`, 7-day TTL.
- `WorkingHours` rows for Mon-Fri 10:00-19:00 (when
  `schedule_preset=default_mon_fri_10_19`).
- `MasterService` rows for every UUID in `services[]`.
- `master.invited` audit row.
- After commit: MAX bot DM dispatched to `contact_value` carrying the
  deeplink + the `fallback_link` web URL. `master.invite_dispatched`
  audit row reflects the outcome (`queued` / `failed` / `skipped`).

### The three links, and which one you can actually send (DRF-1424)

They are not interchangeable, and two of the three fail in ways that
look like nothing happened:

| Field | Where it works | Where it does not |
|---|---|---|
| `invite_link` | anywhere — chat, SMS, another messenger, read aloud | — |
| `fallback_link` | inside MAX's own webview | a browser: no `initData`, «MAX не передал данные для входа» |
| the DM's button | the chat it was sent to | requires a known `max_username` and an existing chat |

**`invite_link` is the one to hand over.** Opening it starts the salon
bot, which receives `bot_started` with `payload=master_invite_<token>`
(MAX delivers `?start=` there — confirmed on the pilot 30.08 in
`ingress:max_salon`) and replies into the chat that now exists with the
same «Принять приглашение» button. That is what makes the delivery
guaranteed rather than dependent on already knowing the person's handle.

It names the **salon** bot on purpose: only `ingress:max_salon` reaches
the handler that reads invitations
(`apps/channels/max/salon_handler.py`). A link to the client bot would
deliver the token to the conversational pipeline, which drops it.

`invite_link` is `""` when the tenant has no salon bot in the registry,
or that bot has no `web_app` (`MAX_BOT_<SLUG>_WEB_APP`) — in which case
the bot could not build the button on arrival either, so an empty field
is the honest answer rather than a link leading to an apology. The
`admin_api.invite.no_start_link` WARNING names what to set.

> **Pilot prerequisite, as of 30.08.** `MAX_BOT_SALON_WEB_APP` is
> commented out in `.env.staging`. It was set that morning and rolled
> back the same day: with a `web_app` present, the staff menu starts
> building its own `open_app` button whose payload is
> `cb:staff:open_app`, and MAX rejects a payload containing `:` with
> HTTP 400 `proto.payload` — every menu reply failed. Guard 3 in
> `apps/channels/max/outbound.py` screens `=`, `&` and `?` but not `:`,
> and the comment at `staff_menu.py:64` asserting that colons are legal
> is wrong. Until that is fixed, uncommenting the variable trades a dark
> invite link for a broken staff menu; `invite_link` therefore returns
> `""` on the pilot today and the invitation goes out by DM only.

Opening the link never spends the invitation: the bot validates and
delivers, and the token is consumed only by `/onboarding/accept` inside
a verified Mini App session. So a forwarded link cannot burn an
invitation the rightful master has not accepted yet.

Idempotency: a second call within 7 days with the same
`(tenant, name, contact_value)` and `invite_status=pending` returns the
EXISTING row (HTTP 200, `X-Idempotent: true`) instead of creating a
duplicate.

Scope notes (separate PRs):
- `role` parameter NOT accepted yet — master is the only role this
  endpoint creates. Admin / Receptionist invites write the
  `TenantStaff` table (different model + lifecycle).
- `email` `contact_method` deferred (needs SMTP backend).
- `schedule_preset=custom` deferred (needs the schedule-editor UI).
- Re-invite / cancel-invite endpoints are separate PRs.

---

## Manual M0 dry-run

Prerequisites:
- A tenant exists in the dev DB (use `seed_dev_formula_tela` for
  `formula-tela`).
- You have a real MAX account that has DM'd the salon's MAX bot at
  least once — so a `BotUser` row exists. If not, run
  `python manage.py seed_dev_formula_tela --max-user-id <your_max_id>`.

Steps:

1. **Create a PENDING invite** for the tester. The preferred path is
   the real flow above (`POST /api/v1/admin/masters/invite`). For the
   no-HTTP path use the management command:
   ```
   python manage.py create_test_master_invite \
       --tenant formula-tela \
       --name "Анна Петрова" \
       --max-handle anna_styl
   ```
   **NOTE: deprecated** — emits a stderr warning. This bypasses the
   audit + DM dispatch side effects of the real flow and exists only
   for backend tests + the very-first-tenant bootstrap before any
   admin can sign in.

   The command prints:
   - `master_id` — the `CatalogMaster.id`
   - `invite_token` — fresh UUID, 7 days TTL
   - `open_app payload` — `master_invite_<token>`, the payload the real
     invite DM carries on its «Принять приглашение» button
   - `web URL` — `http://localhost:5173/onboarding/master?token=<token>`
     (for **local Vite dev with the dev bypass only**; override host with
     `settings.SITE_DOMAIN`)

   Idempotent on (tenant, name): re-running keeps the same token unless
   you pass `--regenerate`.

   > **DRF-1349 — the address is not a way in.** This step used to print
   > a `deeplink` line, `max://bot/<bot>?start=master_invite_<token>`,
   > «paste to the tester via the bot DM». MAX does not implement the
   > `max://` scheme: on a real device it answers «Не удалось открыть
   > ссылку. Установите браузер на устройстве». The `web URL` is no
   > substitute on a phone either — opened in a browser the Mini App gets
   > no `initData`, shows «MAX не передал данные для входа», and the
   > backend cannot check the token at all, because
   > `validate_invite_token` resolves it through the tenant of the
   > session's `BotUser`. A Mini App is entered **only** from an
   > `open_app` button on a MAX message. Testing the invite for real
   > means sending it for real (`POST /api/v1/admin/masters/invite`) with
   > `MAX_BOT_WEB_APP` set.

2. **Open the invitation from MAX** — tap «Принять приглашение» on the
   bot DM; MAX passes the payload as `start_param` and the Mini App opens
   at `/onboarding/master?token=…`.

   Or, when there is no DM to tap (no known `max_username`, or you are
   testing the handover route): open the `invite_link` from the create
   response, `https://max.ru/<salon_bot>?start=master_invite_<token>`.
   The bot starts, receives `bot_started` with the token in `payload`,
   and answers with the same button. Then verify each step below
   identically — from here on the two routes are the same flow.

   Checking what actually arrived, when a start link seems to do
   nothing — read the stream, not `docker compose logs`, which is lost
   whenever the containers are recreated:

   ```
   docker compose -p ayla-bot-staging exec -T redis \
     redis-cli XREVRANGE ingress:max_salon + - COUNT 5
   ```

   Verify each step:

   | Step | Expected screen | Spec line |
   |------|-----------------|-----------|
   | Step 1 | «Здравствуйте, Анна!» + identity card + «Это я, продолжить» | §M0 178-198 |
   | Step 2 | «Что вы увидите» / «Что вы НЕ увидите» + «Понятно» | §M0 204-225 |
   | Step 3 | Photo picker + bio textarea + services list + «Сохранить и продолжить» | §M0 230-258 |
   | Done   | `/master/dashboard` placeholder | this runbook |

3. **Error paths**: replay with a stale token, a token from another
   tenant's row, or after the master already accepted. Expected:
   - 404 / 410 → «Ссылка устарела. Попросите Карину прислать новую.»
   - 409 → «Вы уже подключены — открыть рабочий стол» (→ /master/dashboard)
   - 403 → «Сообщите Карине — возможно, ссылку отправили не туда.»

## Recovering a stuck invite

- **Expired (PENDING + past invite_expires_at)**: run the management
  command again with `--regenerate` — same row, fresh token, fresh
  expiry. The old deeplink dies (uniqueness on `invite_token` is
  enforced).
- **Accidentally cancelled** (rejected via «Это не я»): the row is now
  CANCELLED. A future MM2 endpoint will re-issue; for now, the
  recovery path is via the Django admin (`InviteStatus = PENDING`
  + fresh token) or by deleting the row and re-running this command
  with a different `--name` if name conflict.

## Audit trail

Each onboarding event writes to `apps.audit`:
- `master.onboarding_started` — claim succeeded
- `master.onboarding_accepted` — accept succeeded
- `master.onboarding_rejected` — reject succeeded
- `master.profile_initialized` — profile patch succeeded with at least
  one field populated

Query for a specific master:
```
SELECT * FROM audit_auditlog
WHERE target_id = '<master_uuid>'
ORDER BY created_at DESC;
```
