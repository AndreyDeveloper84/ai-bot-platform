# Runbook: ChromaDB Bearer-auth (Sprint 7 / M4 / DRF-595)

> Status: **draft**
> Last exercised: _never (first staging rollout)_
> Target completion sprint: Sprint 7
> Owner: Platform Lead

## Purpose

Stand up ChromaDB with token-gated access on staging / production, and
rotate the token without dropping the FAQ skill (DRF-589) or the K-track
ingester (DRF-562 / DRF-564).

Without this gate, anything on the docker network can read or wipe a
tenant's `tenant_<uuid>` collection — the entire KB corpus is exposed.

## Trigger / when to run

- First-time staging or production rollout (one-shot bootstrap).
- Quarterly rotation per the org's secret-rotation policy.
- Suspected token compromise (any time the value was logged, pasted to a
  third-party tool, or leaked to a former staff member).

## Prerequisites

- Shell on the ops bastion with `docker compose` access.
- `1Password` / `vault` admin role to mint + store the new token.
- `web` service down for the rotation window (≈ 60 seconds) — clients
  reconnect with the new token on next boot. Schedule outside peak hours
  if the FAQ skill is live.
- Production env file located at `/etc/ai-bot-platform/.env`.

## Step-by-step procedure

### A. First-time rollout

1. Mint a 32-char random token:
   ```sh
   openssl rand -hex 16
   ```
   Expected output: 32 hex chars, e.g. `9a7b…`.
2. Store it in 1Password under `ai-bot-platform / CHROMA_AUTH_TOKEN`.
3. Add to the production env file (single source of truth — both the
   chromadb container AND the web/worker processes read it):
   ```sh
   echo "CHROMA_AUTH_TOKEN=<token>" >> /etc/ai-bot-platform/.env
   ```
4. Restart the stack:
   ```sh
   docker compose --env-file /etc/ai-bot-platform/.env up -d --force-recreate chromadb web worker
   ```
5. Verify (step "Verification" below).

### B. Rotation

1. Mint the new token (same `openssl rand` command).
2. Update 1Password.
3. **Plan a single restart window**: ChromaDB does not support
   simultaneous valid tokens. The platform must restart chromadb and web
   workers in the same `docker compose up -d` invocation.
4. Replace `CHROMA_AUTH_TOKEN` in `/etc/ai-bot-platform/.env`.
5. Restart:
   ```sh
   docker compose --env-file /etc/ai-bot-platform/.env up -d --force-recreate chromadb web worker
   ```
6. Verify.
7. Invalidate the old token in 1Password's history.

## Verification

1. Heartbeat allows anonymous (chromadb token middleware allow-lists it):
   ```sh
   curl -fsS http://chromadb.internal:8001/api/v2/heartbeat
   # → {"nanosecond heartbeat": <epoch_ns>}
   ```
2. Unauthenticated tenant request now 401s:
   ```sh
   curl -i http://chromadb.internal:8001/api/v2/collections
   # → HTTP/1.1 401
   ```
3. Authenticated request 200s:
   ```sh
   curl -fsS \
     -H "Authorization: Bearer <token>" \
     http://chromadb.internal:8001/api/v2/collections
   ```
4. FAQ skill smoke through the platform — replays a golden fixture
   end-to-end (ingester re-reads + tool reads → grounded answer):
   ```sh
   docker compose exec web uv run pytest \
     tests/e2e/test_faq_e2e.py::test_faq_e2e_happy_path -v
   ```
   All 3 tests pass within 10s.
5. The `web` boot logs MUST contain neither `ImproperlyConfigured`
   (missing token) nor `chromadb 401` (wrong token).

## Failure modes

| Symptom | Cause | Fix |
|---|---|---|
| `web` won't boot: `ImproperlyConfigured: CHROMA_AUTH_TOKEN is required in production` | Missing or empty env var | Re-source the env file; confirm `CHROMA_AUTH_TOKEN` is set before re-running `docker compose up` |
| FAQ skill returns `handoff` for all queries | `chromadb` rejects platform requests with 401 → empty hits → low-confidence path | Token mismatch between server (`CHROMA_SERVER_AUTHN_CREDENTIALS`) and client (`CHROMA_AUTH_TOKEN`); set them identical and `--force-recreate` both services |
| K-track ingester logs `chromadb.errors.AuthError` | Same as above for celery worker | `docker compose up -d --force-recreate worker` after correcting `.env` |

## Escalation contacts

| Severity | Who | How to reach |
|---|---|---|
| P0 (FAQ down, all tenants) | Platform Lead | PagerDuty rotation |
| P1 (one tenant only) | Tenant ops | #ai-bot-ops Slack |
| Vendor | Chroma support | <https://discord.gg/MMeYNTmh3x> |

## Post-mortem template

See `_template.md` — required after any production rotation that
required a rollback or extended the planned window.

## Changelog

- 2026-05-13 — Platform Lead — initial draft (M4 / DRF-595 rollout).
