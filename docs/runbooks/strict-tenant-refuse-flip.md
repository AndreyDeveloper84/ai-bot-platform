# Runbook — STRICT_TENANT_REFUSE flip

> Tenancy retro B4 (PR #476 — `requires_tenant` tag + `STRICT_TENANT_REFUSE` flag).
> Follow-up adversarial-pass blockers fixed in branch `phase0/zeta/476-blockers-pre-flip`.
> Earliest planned flip: **2026-05-28** (1-week log-only soak after PR #476 merged 2026-05-21 / commit `fe88135`).

## What this controls

`STRICT_TENANT_REFUSE` gates how `apps.workers.base.TenantAwareTask`
handlers behave when a Redis Streams entry arrives with empty / invalid
`resolved_tenant_id` AND the handler's `requires_tenant` is `True`
(the conservative default for every subclass).

| Mode | Value | Behaviour |
|---|---|---|
| Log-only (Phase 0 default) | `False` | Handler proceeds against `tenant_scope(None)`. ERROR logged. `worker.tenant_required_missing` event audited. Same effective behaviour as pre-B4 (handlers ran against phantom tenant) — just loud. |
| Strict (post-soak) | `True` | Handler refuses to run. `TenantRequiredButMissing` raises. The consumer does not XACK; entry stays in the PEL until the XAUTOCLAIM reaper (issue #499, opt-in via `PEL_REAPER_ENABLED`) routes it to `<stream>:dlq`. See §«Automatic DLQ» below. |

## ⚠ Flip requires worker restart

`config/settings/base.py` reads:

```python
STRICT_TENANT_REFUSE = os.environ.get("STRICT_TENANT_REFUSE", "false").lower() == "true"
```

**at module import time only.** After that, `settings.STRICT_TENANT_REFUSE`
is a static attribute on the settings module. The per-message read in
`apps.workers.base.TenantAwareTask.__call__` (`getattr(settings,
"STRICT_TENANT_REFUSE", False)`) returns the **import-frozen** value,
NOT the live env var.

Changing `STRICT_TENANT_REFUSE` in `/etc/ai-bot-platform/.env` therefore
has **no effect on running workers** until they are restarted. This is
the same pattern as Sprint 8 / F2 `STRICT_TENANT_SCOPE` — operationally
familiar but easy to miss for the B4 flag because the surrounding code
LOOKS hot-reloadable (per-message read of `settings.X`).

## Automatic DLQ — XAUTOCLAIM reaper (issue #499)

A periodic XAUTOCLAIM-based reaper runs every 5 minutes via Celery
beat (`apps.workers.tasks.reap_pel`). It claims entries idle past
`settings.PEL_REAPER_IDLE_SECONDS` (default 1h) and routes them out
of the source-stream PEL:

- **Source-stream entries** → `<stream>:dlq` (e.g. `ingress:max:dlq`)
  with forensic headers (`_reaped_from`, `_reaped_entry_id`,
  `_reaped_classification`). Source entry is XACK'd.
- **Audit row** `worker.pel_reaped` per entry with `classification`
  (`tenant_required_missing` for B4 strict-mode refusals,
  `handler_failure` for tenant-known dispatch failures) and `decision`
  (`terminal` today; `replay` reserved for future classifier hooks).

### Opt-in via `PEL_REAPER_ENABLED`

Default `False`. The beat task no-ops while disabled, so the schedule
entry is safe to ship before the flip. Enable on the same env-var
flip as `STRICT_TENANT_REFUSE`:

```
PEL_REAPER_ENABLED=true
PEL_REAPER_IDLE_SECONDS=3600     # default 1h
PEL_REAPER_BATCH_SIZE=100        # default — cap per-tick work
```

### Operator triage from the DLQ stream

```bash
# Inspect what's been reaped (last 10 entries in DLQ).
redis-cli XREVRANGE ingress:max:dlq + - COUNT 10

# Replay an entry after fixing ingress: re-XADD to source.
redis-cli XADD ingress:max '*' \
  data '{...original payload...}' \
  trace_id '<original>' \
  resolved_tenant_id '<corrected>'

# Forget a terminal entry permanently.
redis-cli XDEL ingress:max:dlq <entry_id>
```

The reaper never auto-replays — operator decides per entry. Auto-replay
with the same payload would just re-fail at the dispatch layer.

## Flip sequence (operator)

When the pre-flip checklist below is satisfied:

1. Set in `/etc/ai-bot-platform/.env`:
   ```
   STRICT_TENANT_REFUSE=true
   STRICT_TENANT_REFUSE_FLIP_AT=<ISO 8601 UTC, exactly now>
   ```
2. **Stop ALL workers BEFORE starting any.** Not a rolling restart.
   ```
   systemctl stop ai-bot-workers@*
   # wait for XPENDING to drain to zero (or accept the cutover blast
   #   radius): redis-cli XPENDING ingress:max consumers
   systemctl start ai-bot-workers@*
   ```
   **Why stop-all-then-start-all (adversarial-pass D-1):** during a
   rolling restart, half the pool runs with the old flag value and
   half with the new. Same stream group, same PEL. An entry with
   empty `resolved_tenant_id` assigned to an old-mode worker → logs
   and ACKs. Identical entry on a new-mode worker → raises and stays
   in PEL. Different treatment per entry purely by which worker
   happened to claim it. Auditors then see inconsistent
   `worker.tenant_required_missing` patterns for identical inputs.
3. Verify with `journalctl -u ai-bot-workers@* | grep STRICT_TENANT_REFUSE`
   that workers picked up the new value (or via a `worker.consumed`
   event audit query after the restart timestamp).
4. Watch `worker.tenant_required_missing` audit events for the 24h
   post-flip window. Any legitimate handler that surfaces here must
   either gain a `requires_tenant=False` opt-out (with docstring
   justification) OR the upstream ingress must learn to resolve its
   tenant before enqueuing.

## Pre-flip checklist (must pass before 2026-05-28)

- [x] PR `phase0/zeta/476-blockers-pre-flip` (#487, `dc065a8c47`) merged.
- [x] PR `phase0/zeta/487-adversarial-followup` (#496, `5975c08`) merged.
- [ ] At least 7 consecutive days of `worker.tenant_required_missing`
      events triaged — zero legitimate handlers in the list.
- [ ] All registered `TenantAwareTask` subclasses audited for their
      effective `requires_tenant` value. **Issue #502 shipped 2026-05-22**
      — query the latest `worker.subscriber_audit` event for the live
      inventory instead of `grep`:

      ```python
      from apps.events.models import Event
      latest = Event.objects.filter(
          event_type="worker.subscriber_audit"
      ).latest("created_at")
      for h in latest.payload["handlers"]:
          print(h["stream"], h["handler_class"], h["requires_tenant"])
      ```

      Verify (a) MaxHandler is present with `requires_tenant=True`,
      (b) no unexpected handlers with `requires_tenant=False` outside
      the documented opt-out list, (c) MRO chain on each handler
      doesn't show external-mixin shadowing.
- [x] **Issue #499 (XAUTOCLAIM reaper) merged** — see §«Automatic DLQ»
      above. Opt-in via `PEL_REAPER_ENABLED`; flip alongside
      `STRICT_TENANT_REFUSE` so DLQ drain is active from the first
      strict-mode refusal.
- [ ] **Issue #500** (D-2 operator-side ceilings) — HARD GATE.
      Tech-lead directive 2026-05-22: do NOT flip until all 4
      ceilings below are wired (see «HARD GATE» section).
      Specific thresholds: PEL alert warn N=1000 / page N=5000;
      handler rate budget ≤100/min; audit table 2× baseline alert;
      alert dedup on `(handler, hour)`.
- [ ] At flip time: `PEL_REAPER_ENABLED=true` set in
      `/etc/ai-bot-platform/.env` alongside `STRICT_TENANT_REFUSE=true`
      (same worker restart picks both up).
- [ ] Dev-team comms about the **worker-restart-required** flip
      semantics so nobody thinks the env-var flip is hot.

### ⚠ HARD GATE — D-2 operational ceilings (issue #500)

Tech-lead directive 2026-05-22: **`STRICT_TENANT_REFUSE=true` MUST NOT
be flipped until ALL 4 ceilings below are wired and verified.** This
is not advisory — it's a pre-flip blocker. XAUTOCLAIM reaper (#499 —
merged) and observability dashboard are separate, do NOT satisfy this
gate.

Without these, strict mode + a misbehaving ingress = unbounded PEL
growth + unbounded audit-table growth + alert flood.

- [x] **PEL length alert at N=1000.** Shipped: `python manage.py
      monitor_pel --warning 1000 --page 5000` (exit 0/1/2). Wire to
      the monitoring stack with a cron / systemd-timer at 1-min
      interval. JSON output via `--format json` for Prometheus /
      Grafana ingestion. Drain via XCLAIM / manual-claim runbook (or
      the XAUTOCLAIM reaper from #499 once `PEL_REAPER_ENABLED=true`).
- [x] **`worker.tenant_required_missing` per-handler rate budget.**
      Shipped: `apps/workers/ceilings.py::should_emit_tenant_missing`
      gates the emit at both call sites (strict + log-only) in
      `apps/workers/base.py`. Default 100 emits per (handler, hour)
      via `WORKER_TENANT_MISSING_RATE_LIMIT`. Set to 0 to disable
      (diagnostic escape hatch). One WARNING fires when the ceiling
      first triggers each window (grep `tenant_missing_rate_exceeded`).
      Fail-open WARNING logs (`redis_unavailable` / `incr_failed`) are
      deduped on a 60-second per-worker window — a sustained Redis
      outage logs once per minute per worker rather than once per emit.
- [x] **Audit-table size baseline.** Shipped: `python manage.py
      audit_table_baseline --format json` captures row count + total
      / heap / index sizes for `apps_audit_event`. Operator runs once
      pre-flip; alert config compares against 2× the baseline-delta
      24h post-flip. Postgres-only (vendor check raises clearly).
- [ ] **Synthetic flood drill in staging** (AS6 + AS6-NEW, tech-lead
      pass PR #528 R3). Ceilings code landed AFTER the soak started —
      the defense is theoretical until exercised at production volume.

      **AS6-NEW (round-3): positive assertions REQUIRED.** Without them
      the drill silently no-ops: if entries XADD'd to `ingress:max` are
      never consumed (worker stopped, handler not registered), the
      ceiling code never runs and the absence-only criteria
      ("audit ≤ 100") trivially pass with `0 ≤ 100`. Drill "succeeds"
      while exercising nothing. Memory N=17 insight #2: drill specs
      MUST include positive assertions ("X must appear"), not only
      absence assertions ("Y not exceed").

      Pre-drill positive setup checks (all must pass):

      - **Consumer running:** `ps aux | grep "workers.consumer"` returns
        at least one row; `systemctl is-active ai-bot-platform-dev-consumer`
        prints `active`.
      - **Handler registered:** the handler whose entries you XADD MUST
        appear in the dispatcher's installed-handler list. For
        `ingress:max`, that's `MaxHandler` (`apps.channels.max.handler`).
        Confirm via boot log line `workers.consumer.handler_registered
        name=MaxHandler` from a recent worker restart.
      - **Tenant-missing path live:** add an explicit
        `resolved_tenant_id=` (empty) to the synthetic entries; the
        `requires_tenant=True` value on `MaxHandler` is what triggers
        the ceiling code path. Drill is meaningless without it.

      Drill steps:

      1. Capture baseline: `python manage.py audit_table_baseline --format json`
      2. Capture PEL baseline: `python manage.py monitor_pel --format json`
      3. Synthesise 200 tenant-missing entries over ~5 min via
         `redis-cli XADD ingress:max '*' resolved_tenant_id ''` in a
         loop (use ``for i in $(seq 1 200); do redis-cli XADD …; sleep 1.5; done``).
      4. **Wait for drain** — let the consumer process all 200 (verify
         PEL ≤ 5 via `monitor_pel`) BEFORE checking assertions. Without
         this wait, "1× WARNING per handler" can mean "ceiling not
         exercised yet", not "ceiling working".
      5. **Positive assertions** (all must hold):

         - `grep "tenant_missing_rate_exceeded" /var/log/ai-bot-platform/worker.log
           | wc -l` returns ≥ 1 (ceiling fired at least once)
         - `grep "worker.tenant_required_missing handler=MaxHandler"
           /var/log/ai-bot-platform/worker.log | wc -l` returns ≥ 50
           (handler actually executed the path — not zero)
         - Audit row count delta (`audit_table_baseline` post-drill
           vs baseline) is **between 80 and 100** — proves emits fired
           AND ceiling capped them. (≤ 100 alone is consistent with 0.)
      6. **Absence assertions** (all must hold):

         - Audit row count delta ≤ 100
         - `monitor_pel --warning 1000 --page 5000` exits 0
      7. Drain residual PEL via reaper or manual XCLAIM.

      **STOP the flip** if any of: (a) pre-drill setup check fails;
      (b) positive assertion fails (ceiling never exercised); (c)
      absence assertion fails (ceiling failed to cap). File a
      follow-up issue before proceeding.
- [ ] **Post-Redis-incident worker restart** (AS2, tech-lead pass PR #528).
      `apps.ingress.streams._client` is lru_cached. The ceiling code
      now clears the cache on `ConnectionError` so transient blips
      self-heal. BUT for a sustained outage where the dead-pool blip
      isn't classified as ConnectionError (e.g. pod NetworkPolicy
      drop, DNS poison), the cache stays poisoned. After any Redis
      incident in the post-flip window:

      ```
      sudo systemctl restart ai-bot-platform-dev-consumer ai-bot-platform-prod-consumer
      ```

      This rebuilds the connection pool fresh.
- [x] **Alert suppression / dedup wired.** The rate-budget above
      also dedups: once the per-(handler, hour) budget is exhausted,
      subsequent emits drop silently. A single bad ingress emits at
      most 100 audit rows per handler per hour → on-call page can't
      flood from this code path.

## STRICT_TENANT_REFUSE × STRICT_TENANT_SCOPE coupling

> Adversarial-pass design issue #5 (PR #476 follow-up).

The two flags interact at the handler boundary:

- `requires_tenant=False` handlers run with `tenant_scope(None)`.
- Under `STRICT_TENANT_SCOPE=strict`, any ORM read from inside
  `tenant_scope(None)` against a tenant-scoped manager (the default
  `Model.objects` on a `tenancy.TenantScopedManager`-backed model)
  raises `CrossTenantError`.

⇒ **Mandate:** every `requires_tenant=False` handler MUST use the
`Model.all_tenants` escape-hatch manager for its DB reads, not
`Model.objects`. This is documented at the W900/W901 system check
sites; the existing `_IGNORE_TENANT_MANAGER_CHECK = True` opt-out
pattern (see `apps/eventbus/models.py`, `apps/events/models.py`,
`apps/tools/models.py`) is the canonical reference.

Timeline coupling: **do not** flip `STRICT_TENANT_REFUSE` to True
inside the same 24h window as a `STRICT_TENANT_SCOPE` flip. Soak
one, flip it, observe for 24h, then move to the other. The post-flip
monitor windows (`STRICT_SCOPE_FLIP_AT` and `STRICT_TENANT_REFUSE_FLIP_AT`)
must not overlap.

## Rollback

Revert `STRICT_TENANT_REFUSE=false` in `/etc/ai-bot-platform/.env`,
restart workers. No data migration. Any entries already in the PEL
from the strict window remain — either drain manually via XCLAIM /
XACK, or wait for the XAUTOCLAIM reaper.

## Related

- ADR-0001 — multi-tenant scope.
- `docs/runbooks/strict-tenant-scope-flip.md` — sibling runbook for the
  Sprint 8 `STRICT_TENANT_SCOPE` flag (similar restart-required
  semantics).
- PR #476 — Tenancy B4 implementation.
- PR `phase0/zeta/476-blockers-pre-flip` — this runbook + the 4
  blockers found by adversarial Code Reviewer.
