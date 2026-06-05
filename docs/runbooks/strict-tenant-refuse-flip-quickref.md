# STRICT_TENANT_REFUSE flip — operator quick-reference

> **One-page condensed brief** for flip day. Skim this; deep dive in
> [`strict-tenant-refuse-flip.md`](strict-tenant-refuse-flip.md) +
> [`strict-tenant-refuse-d2-ceilings-checklist.md`](strict-tenant-refuse-d2-ceilings-checklist.md)
> only when something doesn't match.
>
> **Earliest flip:** 2026-05-28. **HARD GATE:** all 4 D-2 ceilings wired + staging drill green.

---

## Decision tree

```
Pre-flip drill (staging) green?
    │
    ├─ NO  → fix the missing ceiling, re-run. DO NOT FLIP.
    │
    └─ YES → continue ↓

7-day soak clean (zero legitimate worker.tenant_required_missing)?
    │
    ├─ NO  → triage the legitimate handler, fix upstream, extend soak.
    │
    └─ YES → continue ↓

Subscriber audit inventory matches expectations?
    │
    ├─ NO  → tag opt-out handlers OR fix ingress, re-soak.
    │
    └─ YES → FLIP NOW. Continue ↓ to «Flip sequence».
```

---

## Flip sequence (T = flip moment)

### T-24h — staging drill
```sh
docker compose --env-file /etc/ai-bot-platform/.env exec web \
  uv run python manage.py d2_flood_drill --format json | jq '.passed'
```
Expect `true`. If `false` → STOP, fix missing ceiling, re-run.

### T-1h — subscriber inventory check
```sh
docker compose --env-file /etc/ai-bot-platform/.env exec web \
  uv run python -c "
from apps.events.models import Event
row = Event.objects.filter(event_type='worker.subscriber_audit').latest('created_at')
for h in row.payload['handlers']:
    print(h['stream'], h['handler_class'], 'requires_tenant=' + str(h['requires_tenant']))
"
```
Expect MaxHandler with `requires_tenant=True`. Any unknown handler with
`requires_tenant=False` outside the documented opt-out list → STOP.

### T-0 — flip + restart

```sh
# 1. Set BOTH env vars in one edit:
sudo tee -a /etc/ai-bot-platform/.env <<EOF
STRICT_TENANT_REFUSE=true
STRICT_TENANT_REFUSE_FLIP_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
PEL_REAPER_ENABLED=true
EOF

# 2. STOP-ALL-THEN-START-ALL (not rolling restart — see «Why» below).
sudo systemctl stop ai-bot-workers@*

# 3. Wait for PEL to drain (or accept cutover blast radius).
redis-cli XPENDING ingress:max consumers
# Expect first field = 0 (or accept current count as transient).

# 4. Start all workers in one invocation.
sudo systemctl start ai-bot-workers@*

# 5. Verify workers picked up the new flag value.
journalctl -u 'ai-bot-workers@*' -n 50 | grep STRICT_TENANT_REFUSE
```

**Why stop-all-then-start-all**: during a rolling restart, half the
pool runs old flag + half new. Same Redis stream group + same PEL →
identical entries treated inconsistently (one consumer logs+ACKs, the
other refuses+leaves-in-PEL). Auditors see noise.

### T+1m, +5m, +15m, +1h, +24h — health pulses

```sh
# Pulse — run after each interval above.

# (a) PEL not growing unbounded:
redis-cli XPENDING ingress:max consumers | head -1
# Healthy: ≤1000. Warning at ≥1000 (auto-paged via §1). Page at ≥5000.

# (b) Audit table growth rate vs baseline:
sudo -u postgres psql -d ai_bot_platform -tAc \
  "SELECT COUNT(*) - $(grep ^rows /etc/ai-bot-platform/audit-baseline.txt | cut -f2) FROM apps_audit_event"
# Healthy: delta proportional to time elapsed; >2× baseline rate = §3 auto-page.

# (c) Latest worker.tenant_required_missing events — investigate handler:
sudo -u postgres psql -d ai_bot_platform -tAc \
  "SELECT created_at, payload->>'handler' FROM apps_audit_event
   WHERE event_type='worker.tenant_required_missing'
   ORDER BY created_at DESC LIMIT 5"
# Each row = an ingress webhook missing resolved_tenant_id. Investigate upstream.
```

---

## Decision matrix during flip

| Observation | Severity | Action |
|---|---|---|
| Drill green, soak clean, inventory clean | OK | Proceed with flip |
| PEL count rising fast (>1000 within 5 min post-flip) | 🔴 | **ROLLBACK** — see «Rollback» below. Investigate ingress before re-flipping. |
| Audit growth >2× baseline at T+24h | 🟡 | Investigate handler from latest events; consider rate-limit tuning. |
| New `worker.tenant_required_missing` for known handler | 🟡 | Tag handler `requires_tenant=False` with docstring justification (separate PR) OR fix upstream to resolve tenant. |
| Same entry repeatedly in PEL after XAUTOCLAIM reaper | 🟡 | Inspect `<stream>:dlq`; replay or XDEL per operator triage. |
| Workers won't start (env var typo) | 🔴 | `systemctl status` shows error; fix `.env`, restart. |

---

## Rollback (full, < 60s)

```sh
# 1. Flip the flag back.
sudo sed -i 's/^STRICT_TENANT_REFUSE=true/STRICT_TENANT_REFUSE=false/' /etc/ai-bot-platform/.env
sudo sed -i 's/^PEL_REAPER_ENABLED=true/PEL_REAPER_ENABLED=false/' /etc/ai-bot-platform/.env

# 2. Stop-all-then-start-all (mirror the flip pattern).
sudo systemctl stop ai-bot-workers@*
sudo systemctl start ai-bot-workers@*

# 3. Verify reverted.
journalctl -u 'ai-bot-workers@*' -n 20 | grep STRICT_TENANT_REFUSE
```

Entries already in PEL from the strict window stay there. Drain via
XCLAIM/XACK manually OR let the next strict-mode attempt + reaper
handle. Audit table rows from the strict window are forensic — keep.

---

## DLQ triage (post-flip)

```sh
# Show last 10 reaped entries:
redis-cli XREVRANGE ingress:max:dlq + - COUNT 10

# Replay one entry after fixing upstream:
redis-cli XADD ingress:max '*' \
  data '{...original payload...}' \
  trace_id '<original>' \
  resolved_tenant_id '<corrected>'

# Forget a terminal entry:
redis-cli XDEL ingress:max:dlq <entry_id>
```

---

## Post-flip cleanup (T+24h)

```sh
# Disable the 24h-window audit growth alert:
sudo systemctl disable --now audit-growth-alert.timer

# Confirm soak monitor still running:
sudo systemctl status pel-length-alert.timer
```

---

## Communications template (T-5min announce)

> «Через 5 минут флипаем `STRICT_TENANT_REFUSE=true` + `PEL_REAPER_ENABLED=true`.
> Stop-all-then-start-all всех workers. Ожидаемое окно — 30-60 сек.
> Признак успеха: `journalctl -u ai-bot-workers@*` показывает новое
> значение флага после рестарта. Откат — единственная команда `sed`
> назад + рестарт. Runbook: `strict-tenant-refuse-flip-quickref.md`.»

---

## Companion docs

- Full flip runbook → [`strict-tenant-refuse-flip.md`](strict-tenant-refuse-flip.md)
- D-2 ceilings wire-up → [`strict-tenant-refuse-d2-ceilings-checklist.md`](strict-tenant-refuse-d2-ceilings-checklist.md)
- Issues — #499 (reaper, merged), #500 (ceilings — HARD GATE), #502 (subscriber audit, merged)
- Memory — `strict-tenant-refuse-soak`
