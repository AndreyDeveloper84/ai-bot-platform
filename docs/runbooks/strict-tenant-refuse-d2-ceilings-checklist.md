# Runbook: D-2 ceilings — operator checklist (pre-flip HARD GATE)

> Status: **first staging exercise**
> Last exercised: _never_
> Target completion: **before 2026-05-28** (`STRICT_TENANT_REFUSE=true` flip)
> Owner: W3 / security backstop
> Companion runbook: [`strict-tenant-refuse-flip.md`](strict-tenant-refuse-flip.md)
> Tracking issue: **#500**

## Purpose

Per the strict-flip runbook HARD GATE, `STRICT_TENANT_REFUSE=true` MUST
NOT flip until all 4 operational ceilings are wired and verified:

1. PEL length alert (warning N=1000, page N=5000)
2. `worker.tenant_required_missing` per-handler rate budget (≤100/min)
3. Audit-table baseline snapshot + 2× growth alert
4. Alert dedup on `(handler, hour)`

This runbook gives concrete commands + apply order for each. After all 4
boxes are checked, the operator can proceed with the flip sequence in the
companion runbook.

## Prerequisites

- Shell on the ops bastion with `docker compose` + `redis-cli` access.
- Postgres superuser via `sudo -u postgres psql` (matches the disaster-
  recovery runbook).
- Webhook URL for the operator's pager (PagerDuty, OpsGenie, Slack —
  whichever surface oncall watches). Referenced below as
  `${PAGER_WEBHOOK}`; store in `1Password` under
  `ai-bot-platform / PEL_ALERT_WEBHOOK`.
- The flip date target — synthetic flood drill MUST run in staging at
  least 24h before flip (see §«Synthetic drill» at the end).

---

## §1 — PEL length alert (warning N=1000, page N=5000)

### What it does

Polls `XPENDING ingress:max consumers IDLE 0` once per minute; the
returned count is the size of the Pending Entries List for the worker
consumer group. The XAUTOCLAIM reaper (#499) drains entries past the
idle threshold, but if entries arrive faster than the reaper drains
(misbehaving ingress + strict mode = unbounded growth), this alert is
the operator's first signal.

### Wire-up

1. **Add the polling script** to the ops bastion at
   `/opt/ai-bot-platform/bin/pel_length_alert.sh`:

   ```sh
   #!/usr/bin/env bash
   # PEL length alert — D-2 ceiling #1
   # Reads XPENDING count; pages oncall when above threshold.
   set -euo pipefail

   STREAM="${1:-ingress:max}"
   GROUP="${2:-consumers}"
   WARN_AT="${PEL_WARN:-1000}"
   PAGE_AT="${PEL_PAGE:-5000}"
   WEBHOOK="${PAGER_WEBHOOK:?must export PAGER_WEBHOOK}"

   # XPENDING returns: [<count>, <smallest-id>, <largest-id>, [<consumers>]]
   # First field is the count.
   COUNT=$(
     docker compose --env-file /etc/ai-bot-platform/.env exec -T redis \
       redis-cli XPENDING "$STREAM" "$GROUP" \
       | head -n 1 | tr -d '"'
   )

   if [ -z "$COUNT" ] || ! [[ "$COUNT" =~ ^[0-9]+$ ]]; then
     echo "ERROR: bad XPENDING output: '$COUNT'" >&2
     exit 1
   fi

   if [ "$COUNT" -ge "$PAGE_AT" ]; then
     SEVERITY="page"
   elif [ "$COUNT" -ge "$WARN_AT" ]; then
     SEVERITY="warning"
   else
     # Healthy — exit 0 quietly.
     exit 0
   fi

   curl -sS -X POST "$WEBHOOK" \
     -H 'Content-Type: application/json' \
     -d "$(printf '{"severity":"%s","stream":"%s","group":"%s","count":%d,"warn_at":%d,"page_at":%d,"runbook":"strict-tenant-refuse-d2-ceilings-checklist.md#1"}' \
           "$SEVERITY" "$STREAM" "$GROUP" "$COUNT" "$WARN_AT" "$PAGE_AT")"

   echo "ALERT $SEVERITY count=$COUNT (warn=$WARN_AT page=$PAGE_AT)"
   ```

2. **Make executable** + add a systemd timer (NOT a cron entry — systemd
   gives us journald visibility automatically):

   ```sh
   sudo chmod +x /opt/ai-bot-platform/bin/pel_length_alert.sh

   sudo tee /etc/systemd/system/pel-length-alert.service > /dev/null <<'EOF'
   [Unit]
   Description=PEL length alert (D-2 ceiling #1)
   [Service]
   Type=oneshot
   EnvironmentFile=/etc/ai-bot-platform/.env
   ExecStart=/opt/ai-bot-platform/bin/pel_length_alert.sh
   EOF

   sudo tee /etc/systemd/system/pel-length-alert.timer > /dev/null <<'EOF'
   [Unit]
   Description=PEL length alert — 1 min cadence
   [Timer]
   OnBootSec=2min
   OnUnitActiveSec=1min
   AccuracySec=10s
   [Install]
   WantedBy=timers.target
   EOF

   sudo systemctl daemon-reload
   sudo systemctl enable --now pel-length-alert.timer
   ```

3. **Add `PEL_ALERT_WEBHOOK` to `/etc/ai-bot-platform/.env`** (single
   source of truth, matches the chromadb-auth runbook pattern):

   ```sh
   echo "PAGER_WEBHOOK=<webhook-url-from-1password>" >> /etc/ai-bot-platform/.env
   ```

### Verify (positive assertion)

```sh
# Inject 1500 entries into the PEL via redis-cli; wait one timer tick.
# Expect the next pel-length-alert.service run to POST «warning» to the
# webhook. Reverse the test by draining back below 1000 and confirm
# next run is silent.
for i in $(seq 1 1500); do
  docker compose --env-file /etc/ai-bot-platform/.env exec -T redis \
    redis-cli XADD ingress:max '*' data '{}' trace_id "drill-$i" resolved_tenant_id ''
done

# Trigger immediate run (don't wait for timer):
sudo systemctl start pel-length-alert.service
sudo journalctl -u pel-length-alert.service --since '1 minute ago'
# EXPECTED: «ALERT warning count=1500 ...» in the journal
# AND: a POST landed at $PAGER_WEBHOOK with severity='warning'
```

If the journal line OR the webhook POST is absent → alert is NOT wired;
do NOT flip.

---

## §2 — `worker.tenant_required_missing` rate budget (≤100/min)

### What it does

Caps the audit emit rate at 100 events per minute per `(handler,
strict_mode)` tuple. A misbehaving ingress firing 5000/h tenant-missing
entries would otherwise produce 5000 audit rows/h. The cap bounds the
audit table growth and prevents alert flood.

### Where it lives

This is the ONLY D-2 item that requires an **application-level code
change** (not pure ops wiring). The change is a 15-line patch to
`apps/workers/base.py` adding a Redis-backed token bucket around the
`emit("worker.tenant_required_missing", ...)` call.

### Patch (apply via PR before the flip)

Create a follow-up PR with this diff. The change is small enough to
self-merge under §H.3 rules (category 1 — exploitable production today
if NOT applied; rate-limited audit emit is the load-bearing defence
against audit-table blow-up):

```python
# apps/workers/base.py — inside __call__, around the existing emit
# for worker.tenant_required_missing.

# Token-bucket rate cap (D-2 #500): 100 events per minute per (handler, strict).
# Implementation: Redis INCR with EXPIRE 60 on the per-(handler, strict_mode, minute) key.
# When the count exceeds 100, swallow the emit + bump a separate
# `worker.tenant_required_missing_dropped` counter so ops can see the drop.

from apps.ingress.streams import _client as _redis_client

_RATE_LIMIT_PER_MINUTE = int(getattr(settings, "TENANT_REQUIRED_MISSING_RATE_LIMIT", 100))

def _audit_emit_allowed(handler_name: str, strict_mode: bool) -> bool:
    bucket = f"audit_rate:{handler_name}:{int(strict_mode)}:{int(time.time() // 60)}"
    try:
        r = _redis_client()
        count = r.incr(bucket)
        if count == 1:
            r.expire(bucket, 70)  # 60s window + 10s grace for clock skew
        return count <= _RATE_LIMIT_PER_MINUTE
    except Exception:
        # Redis unavailable → fail-open (let the emit through; better
        # to over-audit than to silently drop during outage).
        return True
```

Then at each `emit("worker.tenant_required_missing", ...)` site, wrap
with the gate:

```python
if _audit_emit_allowed(type(self).__name__, strict):
    emit("worker.tenant_required_missing", payload={...})
else:
    # Dropped — bump a counter so ops sees the spike + which handler.
    emit("worker.tenant_required_missing_dropped", payload={
        "handler": type(self).__name__,
        "strict_mode": strict,
    })
```

### Wire-up

Apply the patch via PR, merge under §H.3 self-merge discipline. After
merge:

```sh
# Confirm settings flag value (default 100):
docker compose --env-file /etc/ai-bot-platform/.env exec web \
  uv run python -c "from django.conf import settings; print(settings.TENANT_REQUIRED_MISSING_RATE_LIMIT)"
```

### Verify (positive assertion)

Run the synthetic flood drill (§«Synthetic drill» below). Expected:
audit table grows by ≤100 rows in the first 60s of the burst, NOT 200+.
A `worker.tenant_required_missing_dropped` row appears AT LEAST ONCE in
the same window.

---

## §3 — Audit-table baseline + 2× growth alert

### What it does

Snapshot the `apps_audit_event` row count + table size pre-flip.
Compares post-flip growth rate against baseline; alerts at 2× (the
ratio that surfaces a runaway before the table doubles).

### Wire-up

1. **Take the baseline snapshot** (≤24h before flip):

   ```sh
   # Run on the ops bastion. Captures row count + relpages + index size
   # for the audit table at flip time T-1d.

   sudo -u postgres psql -d ai_bot_platform <<'SQL' > /etc/ai-bot-platform/audit-baseline.txt
   SELECT
     'rows' AS metric,
     COUNT(*)::text AS value
   FROM apps_audit_event
   UNION ALL
   SELECT 'relpages', relpages::text FROM pg_class WHERE relname = 'apps_audit_event'
   UNION ALL
   SELECT 'table_size_bytes', pg_total_relation_size('apps_audit_event')::text
   UNION ALL
   SELECT 'snapshot_at', now()::text;
   SQL
   ```

2. **Add the 2× growth poll** as a systemd timer (every 5 min during
   the 24h post-flip window — afterward the timer can be disabled):

   ```sh
   sudo tee /opt/ai-bot-platform/bin/audit_growth_alert.sh > /dev/null <<'EOF'
   #!/usr/bin/env bash
   # D-2 ceiling #3 — audit table 2× growth alert (24h post-flip window).
   set -euo pipefail

   BASELINE_FILE=/etc/ai-bot-platform/audit-baseline.txt
   if [ ! -r "$BASELINE_FILE" ]; then
     echo "ERROR: baseline not snapshotted at $BASELINE_FILE" >&2
     exit 1
   fi

   BASELINE_ROWS=$(grep -m1 '^rows' "$BASELINE_FILE" | cut -f2)
   BASELINE_SNAPSHOT_AT=$(grep -m1 '^snapshot_at' "$BASELINE_FILE" | cut -f2-)
   CURRENT_ROWS=$(
     sudo -u postgres psql -d ai_bot_platform -tAc \
       "SELECT COUNT(*) FROM apps_audit_event"
   )

   # Hours elapsed since baseline:
   ELAPSED_HOURS=$(
     sudo -u postgres psql -d ai_bot_platform -tAc \
       "SELECT EXTRACT(EPOCH FROM (now() - '$BASELINE_SNAPSHOT_AT'::timestamptz)) / 3600.0"
   )

   # Baseline rate (rows / hour) = baseline rows / hours-existed.
   # We approximate «baseline growth rate» as baseline_rows / 720h (30d
   # of accumulated data — adjust if your retention is different).
   BASELINE_RATE=$(awk -v r="$BASELINE_ROWS" 'BEGIN { print r / 720.0 }')
   CURRENT_RATE=$(
     awk -v cur="$CURRENT_ROWS" -v base="$BASELINE_ROWS" -v hrs="$ELAPSED_HOURS" \
         'BEGIN { if (hrs > 0) print (cur - base) / hrs; else print 0 }'
   )

   # 2× threshold:
   RATIO=$(awk -v c="$CURRENT_RATE" -v b="$BASELINE_RATE" \
               'BEGIN { if (b > 0) print c / b; else print 0 }')

   if awk -v r="$RATIO" 'BEGIN { exit !(r > 2.0) }'; then
     curl -sS -X POST "${PAGER_WEBHOOK:?}" \
       -H 'Content-Type: application/json' \
       -d "$(printf '{"severity":"page","metric":"audit_growth_2x","baseline_rate":%s,"current_rate":%s,"ratio":%s,"runbook":"strict-tenant-refuse-d2-ceilings-checklist.md#3"}' \
             "$BASELINE_RATE" "$CURRENT_RATE" "$RATIO")"
     echo "ALERT audit growth ratio=$RATIO baseline_rate=$BASELINE_RATE current_rate=$CURRENT_RATE"
   fi
   EOF

   sudo chmod +x /opt/ai-bot-platform/bin/audit_growth_alert.sh
   ```

3. **Add the systemd timer** (5-minute cadence; disable 24h post-flip):

   ```sh
   sudo tee /etc/systemd/system/audit-growth-alert.service > /dev/null <<'EOF'
   [Unit]
   Description=Audit table 2x growth alert (D-2 #3, 24h post-flip)
   [Service]
   Type=oneshot
   EnvironmentFile=/etc/ai-bot-platform/.env
   ExecStart=/opt/ai-bot-platform/bin/audit_growth_alert.sh
   EOF

   sudo tee /etc/systemd/system/audit-growth-alert.timer > /dev/null <<'EOF'
   [Unit]
   Description=Audit growth — 5 min cadence
   [Timer]
   OnBootSec=2min
   OnUnitActiveSec=5min
   [Install]
   WantedBy=timers.target
   EOF

   sudo systemctl daemon-reload
   sudo systemctl enable --now audit-growth-alert.timer

   # Auto-disable after 24h post-flip:
   #   echo "$(date -u +%FT%TZ) -- disable audit-growth-alert.timer" >> /etc/ai-bot-platform/post-flip-todo.txt
   ```

### Verify (positive assertion)

Run the synthetic drill (§«Synthetic drill»). After the drill, the
baseline row count delta should be visible via:

```sh
sudo -u postgres psql -d ai_bot_platform -tAc \
  "SELECT COUNT(*) - $(grep ^rows /etc/ai-bot-platform/audit-baseline.txt | cut -f2) FROM apps_audit_event"
```

Expected: delta ≤ 100 (because of the rate budget from §2). If delta is
>200, the rate budget is NOT effective; do NOT flip.

---

## §4 — Alert dedup on `(handler, hour)`

### What it does

Suppresses repeat alerts for the same handler in the same hour so a
single misbehaving ingress doesn't flood oncall with 60+ pages.
Aggregation is at the alert-routing layer — there's no app-level code
change needed (the rate budget at §2 already caps the audit emit; this
layer caps the page-out).

### Wire-up — depends on the operator's alerting stack

**If using Alertmanager / Prometheus:** add to `alertmanager.yml`:

```yaml
route:
  group_by:
    - alertname
    - handler   # ← label set by §1 + §3 webhook payloads
  group_wait: 30s
  group_interval: 3600s   # ← dedup window = 1h
  repeat_interval: 4h
```

**If using a webhook + custom router (no Alertmanager):** wrap the
webhook POST with a Redis-backed dedup gate. Add to the ops bastion at
`/opt/ai-bot-platform/bin/_dedup_post.sh`:

```sh
#!/usr/bin/env bash
# Dedup-aware webhook POST. Called by the §1 + §3 scripts.
set -euo pipefail
HANDLER="${1:?handler}"
HOUR=$(date -u +%Y%m%d-%H)
DEDUP_KEY="alert_dedup:${HANDLER}:${HOUR}"

# SET NX returns 1 if key was created (= first alert this hour), 0 if exists.
WAS_FIRST=$(
  docker compose --env-file /etc/ai-bot-platform/.env exec -T redis \
    redis-cli SET "$DEDUP_KEY" 1 EX 3700 NX
)

if [ "$WAS_FIRST" = "OK" ]; then
  curl -sS -X POST "${PAGER_WEBHOOK:?}" -H 'Content-Type: application/json' --data-binary "${2:-{}}"
else
  echo "DEDUP: skip alert for $HANDLER in $HOUR (already paged this hour)"
fi
```

Then update §1 + §3 scripts to call `_dedup_post.sh <handler> '<payload>'`
instead of `curl -X POST $PAGER_WEBHOOK` directly.

### Verify (positive assertion)

```sh
# Fire two alerts in quick succession for the same handler.
/opt/ai-bot-platform/bin/_dedup_post.sh MaxHandler '{"test":1}'  # → posts
/opt/ai-bot-platform/bin/_dedup_post.sh MaxHandler '{"test":2}'  # → silenced

# Check journald — expect one POST + one «DEDUP: skip» message.
journalctl --since '5 minutes ago' | grep -E 'DEDUP|test'
```

If the second call ALSO triggers a POST → dedup is NOT wired; do NOT flip.

---

## §Synthetic drill (MANDATORY — at least 24h before flip)

### Purpose

Verify all 4 ceilings fire correctly under load. Positive assertions
only — «нужный сигнал точно был», NOT «отсутствие проблем». A green
drill is a precondition for flip.

### Script — `/opt/ai-bot-platform/bin/d2_flood_drill.sh`

```sh
#!/usr/bin/env bash
# D-2 ceiling synthetic flood drill — 200 tenant-missing entries in 5 minutes.
# MUST run in staging (not prod). MUST run after all 4 ceilings wired.
set -euo pipefail

echo "[1/3] Recording baselines..."
BASELINE_AUDIT_ROWS=$(
  sudo -u postgres psql -d ai_bot_platform -tAc \
    "SELECT COUNT(*) FROM apps_audit_event WHERE event_type='worker.tenant_required_missing'"
)
echo "    baseline audit rows: $BASELINE_AUDIT_ROWS"

echo "[2/3] Flooding 200 tenant-missing entries over 5 minutes (40/min)..."
for i in $(seq 1 200); do
  docker compose --env-file /etc/ai-bot-platform/.env exec -T redis \
    redis-cli XADD ingress:max '*' \
      data '{}' \
      trace_id "drill-$(date -u +%s)-$i" \
      resolved_tenant_id ''
  sleep 1.5
done

echo "[3/3] Verifying 3 positive assertions..."
FAIL=0

# Assertion 1: WARNING fired
sudo systemctl start pel-length-alert.service
sleep 5
if journalctl -u pel-length-alert.service --since '6 minutes ago' | grep -q 'ALERT warning'; then
  echo "    ✓ PEL warning alert fired"
else
  echo "    ✗ PEL warning alert MISSING — ceiling #1 not wired"
  FAIL=1
fi

# Assertion 2: audit growth ≤100 rows (rate budget effective)
CURRENT_AUDIT_ROWS=$(
  sudo -u postgres psql -d ai_bot_platform -tAc \
    "SELECT COUNT(*) FROM apps_audit_event WHERE event_type='worker.tenant_required_missing'"
)
DELTA=$((CURRENT_AUDIT_ROWS - BASELINE_AUDIT_ROWS))
echo "    audit delta: $DELTA rows (expected ≤100 with rate budget)"
if [ "$DELTA" -le 100 ]; then
  echo "    ✓ Rate budget effective (audit grew by $DELTA, ≤100/min cap)"
else
  echo "    ✗ Rate budget NOT effective — audit grew by $DELTA — ceiling #2 not wired"
  FAIL=1
fi

# Assertion 3: dropped-counter row exists (proves the cap actually engaged)
DROPPED_ROWS=$(
  sudo -u postgres psql -d ai_bot_platform -tAc \
    "SELECT COUNT(*) FROM apps_audit_event WHERE event_type='worker.tenant_required_missing_dropped' AND created_at > now() - interval '6 minutes'"
)
if [ "$DROPPED_ROWS" -ge 1 ]; then
  echo "    ✓ Drop-counter row present ($DROPPED_ROWS) — cap engaged"
else
  echo "    ✗ NO drop-counter — rate budget either off OR drill didn't exceed threshold"
  FAIL=1
fi

if [ "$FAIL" -ne 0 ]; then
  echo ""
  echo "DRILL FAILED. DO NOT FLIP STRICT_TENANT_REFUSE."
  exit 1
fi

echo ""
echo "DRILL PASSED. All 3 positive assertions held."
echo "Flip may proceed per strict-tenant-refuse-flip.md."
```

### Run

```sh
sudo chmod +x /opt/ai-bot-platform/bin/d2_flood_drill.sh
/opt/ai-bot-platform/bin/d2_flood_drill.sh
```

A non-zero exit blocks the flip. Re-wire the missing ceiling, re-run.

---

## Apply order summary

```
1. §1  PEL alert script + systemd timer        ← ops-only
2. §2  Rate budget patch via PR + merge        ← app code change
3. §3  Baseline snapshot + growth-alert timer  ← ops-only (depends on §2 cap being active)
4. §4  Dedup wrapper (chosen variant)          ← ops-only
5. §SD Synthetic flood drill in staging        ← gates flip
```

All 4 + synthetic drill MUST be green before the operator runs the flip
sequence in [`strict-tenant-refuse-flip.md`](strict-tenant-refuse-flip.md#flip-sequence-operator).

## Rollback per ceiling

Each ceiling is independently disable-able if it misfires post-flip:

- §1: `sudo systemctl disable --now pel-length-alert.timer`
- §2: `TENANT_REQUIRED_MISSING_RATE_LIMIT=999999` in `.env` + restart
- §3: `sudo systemctl disable --now audit-growth-alert.timer`
- §4: revert `alertmanager.yml` OR remove `_dedup_post.sh` wrapper

After rollback, **re-flip the strict flag back to log-only mode** if
the underlying issue is uncertain — strict mode without ceilings is
the unbounded-PEL scenario we built these ceilings to prevent.

## Related

- [`strict-tenant-refuse-flip.md`](strict-tenant-refuse-flip.md) — companion flip runbook
- Issue **#500** — D-2 ceilings tracker
- Issue **#499** — XAUTOCLAIM reaper (merged, PR #508)
- Issue **#502** — Boot-time `worker.subscriber_audit` (merged, PR #536)
- Memory `strict-tenant-refuse-soak` — soak state + pre-flip lineage
