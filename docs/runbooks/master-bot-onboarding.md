# Runbook — Master M0 onboarding (manual test + ops)

Status: living document (PR 1 / M0). Update as later master PRs land.

## What this covers

- How to manually exercise the master M0 onboarding flow against a real
  dev environment.
- How to recover a stuck invite (expired / lost link).
- Why there is **no dev-mode init-data bypass** in this PR.

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
  "fallback_link": "http://localhost:5173/onboarding/master?token=<token>"
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
   - `deeplink` — `max://bot/<bot>?start=master_invite_<token>` (paste to
     the tester via the bot DM)
   - `web URL` — `http://localhost:5173/onboarding/master?token=<token>`
     (for local Vite dev; override host with `settings.SITE_DOMAIN`)

   Idempotent on (tenant, name): re-running keeps the same token unless
   you pass `--regenerate`.

2. **Open the deeplink in MAX** — the bot DM opens the Mini App at
   `/onboarding/master?token=…`. Verify each step:

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

## Why no dev-mode init-data bypass in this PR

Considered: a `X-Dev-Init-Data` header bypass gated behind
`DEBUG=True` so the master Mini App could be exercised in a plain
browser without a MAX wrapper.

**Decision: deferred.**

Cost-benefit:
- The bypass touches `apps/miniapp_api/auth.extract_init_data` (the
  customer surface) AND adds a parallel branch through
  `require_init_data_only` + `require_master_init_data`. That's two
  more test surfaces with security-sensitive invariants (DEBUG-gating,
  loud logging, never-in-prod).
- The same dev-flow is already achievable with **a real MAX dev bot**
  in the test tenant — `seed_dev_formula_tela --max-user-id <your_id>`
  + the deeplink from `create_test_master_invite` gives a working
  end-to-end flow in ~30 seconds.
- Risk: any dev-mode bypass eventually leaks into a misconfigured prod
  env. The cost of a single bypass-test-leak is higher than the
  convenience savings.

Workaround for browser-only dev (when MAX is unreachable):
1. Set `VITE_DEV_INIT_DATA` in `apps/miniapp/.env.local` to a
   pre-signed initData string. The customer flow already supports this
   (`apps/miniapp/src/lib/max-sdk.ts::getInitData`). Generate one via
   `apps/master_api/tests/conftest.py::_sign` (copy the helper to a
   throwaway script, sign with your local `MAX_BOT_TOKEN`).
2. Open `http://localhost:5173/onboarding/master?token=<token>` from
   `create_test_master_invite`.

If we discover this workaround is too painful in practice (e.g. the
test runner needs to refresh init-data hourly), revisit the dev-shim
decision in a follow-up PR with explicit DEBUG-gating + a permission
guard.

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
