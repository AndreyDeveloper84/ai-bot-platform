# Pre-pilot security audit — pre-flip pass

> **Auditor:** W3 (Zeta, security backstop)
> **Date:** 2026-06-02
> **Scope:** pre-flip pass (subset). A separate post-flip delta-pass will follow within 24-48h after `STRICT_TENANT_REFUSE=True` is rolled to production.
> **Methodology:** static analysis against `origin/dev` HEAD, cross-referenced with the ADR-0009 G1–G10 import-edge contracts, `docs/runbooks/strict-tenant-refuse-flip.md`, ADR-0009, and currently-open security follow-ups.
> **Audit branch:** `phase2/w3/pre-flip-security-audit`

> ### ⚠️ Correction (S5, 2026-06-03) — read before §3
> This audit's original text treated `.importlinter.baseline` as an existing, enforcing artifact. **It is not. No `.importlinter*` file exists or has ever existed in the repo** (verified across full git history of all three repos). The G1–G10 "contracts" are real ADR-0009 invariants drawn from the Codex integration audit + roadmap item A11 — but A11 (which proposed import-linter) **never shipped**, so nothing auto-enforces these edges today. The original methodology line citing `.importlinter.baseline` was a *planned-as-done* error; see the S5 provenance trace on #968. The §3 finding below (7 uncovered write-path sites) **remains valid** — it is a real ADR-0009 §5 breadth gap; only its attribution to a "baseline contract file" is corrected. Enforcement is moving to the `tools/lint/` AST-linter (Option B per orchestrator 2026-06-03), where these edges become CI-blocking.

## Executive summary

| § | Area | Verdict | Action |
|---|------|---------|--------|
| 1 | `requires_tenant=False` exemption sweep (silent-bypass post-flip) | 🟢 GREEN | None — zero production handlers declare `False`. Defence-in-depth verified. |
| 2 | `STRICT_TENANT_REFUSE` read-site coverage (stale-cache risk) | 🟢 GREEN | None — single runtime read-site, worker-restart contract documented and load-bearing. |
| 3 | Canonical-state mutator imports from upstream surfaces (G5.1 breadth check) | 🟡 YELLOW | Adversarial pass refuted initial GREEN — the triaged G5.1 edge scope was too narrow (see correction; not an enforced linter contract). 7 additional write-path sites in `transitions` + `feedback` not covered. New tracker #968 filed. |
| 4 | Audit log durability under flip (`worker.tenant_required_missing` emission contract) | 🟢 GREEN | None — dual-path emit (log line always fires, DB emit conditionally), operator has fallback. |
| 5 | PEL reaper readiness for post-flip drainage | 🟢 GREEN (runbook already enforces) | Verified — `docs/runbooks/strict-tenant-refuse-flip.md` lines 147/157/167 enforce `PEL_REAPER_ENABLED=true` as pre-flip blocker. |

**Overall pre-flip security posture: 🟡 GREEN-with-one-YELLOW.**

Four of five audit dimensions pass. §3 was downgraded from 🟢 GREEN to 🟡 YELLOW by the in-house adversarial pass on this audit doc: the triaged G5.1 edge scope (see correction — it is a documented invariant, not an enforced linter contract) was found to be narrower than the underlying ADR-0009 §5 invariant, and the auditor's first §3 grep mirrored that narrow scope and missed 7 production write-path imports of `apps.booking.services.transitions` + `apps.booking.services.feedback` from `miniapp_api` + `admin_api`. New tracker #968 filed; flip-day risk remains bounded (sync HTTP paths, JWT-resolved, not stream consumers under STRICT_TENANT_REFUSE).

The §5 procedural dependency (PEL reaper enablement) is already enforced by the flip runbook as a pre-flip blocker checkbox (verified at lines 147/157/167 of `strict-tenant-refuse-flip.md`).

Out-of-scope for this pre-flip pass (deferred to post-flip delta-pass):

- Real PEL backlog behaviour with flag=True
- Real DLQ routing observability under load
- Operator-side ceiling trip behaviour (PEL alert, rate budget, audit baseline, alert dedup) — drilled pre-flip but not yet exercised under live flip-True conditions
- 24-48h observability soak
- W2 PII tokenization PR #842 cross-stream review (PR is still DRAFT on 2026-05-26 — not yet ready-for-review)

---

## §1 — `requires_tenant=False` exemption sweep

### Threat model

Post-flip (`STRICT_TENANT_REFUSE=True`), only `TenantAwareTask` subclasses with `requires_tenant=True` refuse to run when the stream entry's `resolved_tenant_id` is missing. Handlers declaring `requires_tenant=False` are explicitly exempt — they proceed against `tenant_scope(None)` regardless of flip state.

**Silent-bypass vector:** if any production handler erroneously declares `requires_tenant=False`, post-flip it continues running on phantom tenant while the rest of the fleet refuses. Pre-flip log-only mode hides this because all handlers proceed equally.

### Evidence

Grep pattern: `requires_tenant\s*=\s*False` across `apps/` excluding tests.

Result: **0 production sites.** The only hits are within `apps/workers/base.py` itself — docstring examples and inline comments illustrating the anti-pattern the framework guards against.

```
apps/workers/base.py:204     requires_tenant = False  # silent bypass     ← docstring example
apps/workers/base.py:216     ``requires_tenant=False`` then ...           ← docstring
apps/workers/base.py:232     Runtime mutation of ``cls.requires_tenant = False``  ← comment
apps/workers/base.py:317     post-creation mutation attacks ...           ← comment
```

### Defence-in-depth verified

`apps/workers/base.py` ships three layers of hardening:

1. **Snapshot-based resolution** (`_RESOLVED_REQUIRES_TENANT` frozen at class creation via `__init_subclass__`, line 272; consumed in `__call__` at line 343 after the fallback path at 326–342). Runtime mutation `Cls.requires_tenant = False` is neutralised — the call-site reads the frozen snapshot, not the live attribute.
2. **MRO walk via `_resolve_requires_tenant_trusted`** (line 77–106). Walks ancestor classes in MRO order; first own-`__dict__` declaration wins; non-bool values raise `TypeError`. Mixin tricks (`SystemTask` with `requires_tenant=False` as ancestor) are guarded against by an explicit mixin-conflict check (lines 248–258) which raises at class-creation if any ancestor between `cls` and `TenantAwareTask` declares the flag — forces explicit override at the leaf class.
3. **Boot-time subscriber audit** (`apps/workers/subscriber_audit.py:125`, `emit_subscriber_audit`, issue #502). Emits one `worker.subscriber_audit` event per process boot capturing every registered handler's `(handler_name, requires_tenant, stream)`. Operator runbook query: latest `worker.subscriber_audit` row → confirm no `False` entries before flip.

### Verdict

🟢 **GREEN.** No exposure. The silent-bypass vector is closed both by zero production declarations AND by three layers of framework defence. The boot-time audit gives the operator a programmatic pre-flip check.

### Recommendation

Pre-flip checklist already includes the subscriber audit query. Reaffirm explicit verification step: «query latest `worker.subscriber_audit` row, assert every entry has `requires_tenant=true`».

---

## §2 — `STRICT_TENANT_REFUSE` read-site coverage

### Threat model

Stale reads of the flag = inconsistent behaviour during/after flip. If any code path caches the value at module-load time without acknowledging the worker-restart contract, post-flip-without-restart that path silently retains the pre-flip behaviour.

### Evidence

Grep across `apps/` and `config/` for `STRICT_TENANT_REFUSE` excluding tests.

**One production runtime read-site:**

```
apps/workers/base.py:358    strict = bool(getattr(settings, "STRICT_TENANT_REFUSE", False))
```

Read via `getattr(settings, ...)` on every `TenantAwareTask.__call__`. Comments at lines 351–357 explicitly document the design: «STRICT_TENANT_REFUSE is read from `settings` each call — but `settings.STRICT_TENANT_REFUSE` itself is...» (frozen at Django boot). Worker restart is the recovery mechanism.

**Source of truth:**

```
config/settings/base.py:184    STRICT_TENANT_REFUSE = os.environ.get(
                                   "STRICT_TENANT_REFUSE", "false").lower() == "true"
```

Read once at Django module-load. Immutable thereafter without process restart.

**All other references** (drill commands, reaper docstring, ceilings module, channels handler docstring) are either:

- Documentation/comments (no runtime semantics)
- Management commands that read `getattr(settings, ...)` fresh per invocation (no caching)
- Test code

No caching layer. No `@lru_cache`. No module-level `STRICT = settings.STRICT_TENANT_REFUSE` snapshots.

### Verdict

🟢 **GREEN.** Single, fresh, per-call read against an immutable settings object. Worker-restart contract is documented as load-bearing in the flip runbook («⚠ Flip requires worker restart»). No stale-cache vector.

### Recommendation

None. Re-emphasize the worker-restart step in operator briefing on flip day.

---

## §3 — Canonical-state mutator imports breadth (G5.1)

### Threat model

ADR-0009 rule 5 forbids bot-platform from DB-writing booking, payment, or catalog state. Surfaces authenticated by client-equivalent credentials (`miniapp_api`, `master_api`, `channels`, `skills`) writing canonical state = potential cross-tenant leak vector.

The W3 Block A triage notes describe **one** such violation under the `G5-projection-writes-via-consumers` edge (→ `apps/miniapp_api/views.py:668`) — but as a documented invariant, not an enforced linter contract (see correction above). Pre-flip audit: confirm this is the FULL scope, not just one tracked site.

### Evidence — initial pass (incomplete)

Grep scope: `apps/` excluding `tests/`.

Grep for direct imports of `apps.booking.services.{create,reschedule}` from any non-owner app:

```
apps/miniapp_api/views.py:668   from apps.booking.services.create import (...)
```

Grep for direct imports of `apps.{ayla_payments,payments}.services` from non-owner apps:

```
(no results)
```

The initial verdict marked this 🟢 GREEN with «zero undiscovered breaches». **The in-house adversarial pass refuted that verdict.**

### Adversarial refutation

The G5.1 edge as written in the W3 Block A triage notes only enumerates `apps.booking.services.create` + `apps.booking.services.reschedule` as forbidden modules. The auditor's initial §3 grep mirrored that narrow scope, missing the actual production cancel/confirm/feedback mutator modules:

- `apps/booking/services/transitions.py` — direct `row.status = ...` writes at lines 201, 241, 282, 332, 363; `BookingRequest.objects.create(...)` at line 477. Exposes `commit_cancel`, `request_cancel`, `commit_reschedule`.
- `apps/booking/services/feedback.py` — `submit_feedback` mutates booking.

Production write-path imports from non-owner apps (verified on `origin/dev`, write-path only — read-only constant imports excluded):

```
apps/miniapp_api/views.py:898   from apps.booking.services.transitions import (... request_cancel ...)
apps/miniapp_api/views.py:934   from apps.booking.services.transitions import (...)
apps/miniapp_api/views.py:967   from apps.booking.services.transitions import (...)
apps/miniapp_api/views.py:999   from apps.booking.services.transitions import (...)
apps/miniapp_api/views.py:1051  from apps.booking.services.transitions import (...)
apps/miniapp_api/views.py:1177  from apps.booking.services.feedback import (...)
apps/admin_api/services/master_deactivation.py:67  from apps.booking.services.transitions import (... commit_cancel, request_cancel)
```

Seven additional write-path import sites NOT covered by the triaged G5.1 edge, NOT tracked by #925 (which targets only `views.py:668`).

(Note: `apps/miniapp_api/views.py:727` imports the `UNDO_WINDOW_SECONDS` constant from `transitions` — read-only, harmless, excluded from this count.)

### Verdict

🟡 **YELLOW.** The triaged G5.1 edge scope is narrower than the underlying ADR-0009 §5 invariant. Production cancel/confirm/feedback canonical writes from `miniapp_api` (client-equivalent surface) and `admin_api` (admin surface) bypass Ayla's canonical gate on 7 sites that **no linter blocks today** (the planned import-linter never shipped; Option B AST-linter will cover them — see correction).

**Flip-day risk:** **Bounded.** These are sync HTTP paths protected by JWT at the view boundary, not stream consumers subject to `STRICT_TENANT_REFUSE`. The flip is safe today. The risk is pre-pilot architectural debt: same SoR violation as #925, larger surface.

**Pre-pilot risk:** Material. Same character as #925 — canonical-write bypass from client-equivalent authentication surface. Cross-tenant leak vector exists for the same reason: the view-layer trust boundary doesn't verify catalog/master/booking tenant-scope before mutating.

### Cross-link

- #925 — original G5.1 single-site tracker (the `create` import case)
- #968 — NEW: G5.1 contract expansion — `transitions` + `feedback` writes from non-owner apps
- Fix spec posted as comment on #925 (2026-06-02 W3 pre-fix adversarial pass) — same Ayla REST migration pattern applies to the 7 additional sites

### Recommended actions

1. **Encode the G5.1 edge in the `tools/lint/` AST-linter (Option B)** to cover `transitions` + `feedback` in addition to `create` + `reschedule`. New violations hard-fail CI; the existing 7 sites are ticket-tracked (#968), not pinned in a baseline file (no `.importlinter*` exists — see correction).
2. **Phase 2.2 scope expansion** in `unified-maintainability-roadmap.md` — `execute_reschedule` Ayla REST migration grows to also cover the cancel/confirm/feedback paths from `miniapp_api` + `admin_api/master_deactivation`.

Both actions tracked under #968.

---

## §4 — Audit log durability under flip

### Threat model

Post-flip, when a handler refuses, the audit trail of WHY it refused MUST be durable. If the audit emission is best-effort and silently swallows DB errors, post-flip incident triage loses ground truth.

### Evidence

`apps/workers/base.py` audit emission for the refuse path (lines 386–445):

```
386    logger.error(
387        "worker.tenant_required_missing handler=%s trace=%s ...",
388        ...
394    emit(
395        "worker.tenant_required_missing",
396        ...
```

And the alternative path (lines 408–416):

```
408    logger.error(
409        "worker.tenant_required_missing handler=%s trace=%s ..."
410        ...
416    emit(
417        "worker.tenant_required_missing",
```

**Dual-path emission:** every refuse event fires both a `logger.error` (stdout/aggregated logs) AND a DB `emit()` (audit table).

The comment at line 365 — «only the DB-writing emit() is gated» — confirms the log line ALWAYS fires regardless of `emit()` failure, giving the operator a grep-based fallback if the audit DB write fails.

### Verdict

🟢 **GREEN.** Defence-in-depth audit trail. Operator retains visibility even if the audit table or its write path is degraded.

### Recommendation

None. Reaffirm in operator briefing that post-flip log grep for `worker.tenant_required_missing` is the authoritative immediate signal; audit-table query is the secondary structured view.

---

## §5 — PEL reaper readiness for post-flip drainage

### Threat model

`STRICT_TENANT_REFUSE=True` makes the consumer NOT-XACK refused entries (per `apps/workers/reaper.py:7-12`). Without an automatic drainer, refused entries accumulate in the per-consumer-group PEL forever. Operator must `XCLAIM` manually = unbounded toil.

The PEL reaper (issue #499) is the automatic drainer that calls `XAUTOCLAIM` per beat tick, claims entries idle past a threshold, classifies them, and routes terminal entries to `<stream>:dlq`.

### Evidence

`apps/workers/reaper.py` is fully implemented:

- `XAUTOCLAIM`-based drainer per registered `ingress:*` stream
- Idle threshold gate (`PEL_REAPER_IDLE_SECONDS`, default 3600s)
- Batch size cap (`PEL_REAPER_BATCH_SIZE`, default 100)
- Classification → terminal-DLQ or replay (terminal default today)
- Every reaped entry emits `worker.pel_reaped` audit
- Reaper runs as named consumer `reaper` in the same group — no extra setup

**Critical gate:** `PEL_REAPER_ENABLED` defaults to `False`.

```
apps/workers/reaper.py:480
    if not bool(getattr(settings, "PEL_REAPER_ENABLED", False)):
        logger.debug("workers.reaper.disabled — PEL_REAPER_ENABLED=False")
```

Module docstring (line 40):

> **Opt-in via `settings.PEL_REAPER_ENABLED`** (default False during rollout). The Celery task no-ops when disabled so adding the beat schedule entry is safe before the flip.

### Verdict

🟢 **GREEN — runbook already enforces.** Code is ready; flag defaults to off intentionally (safe-to-deploy posture); runbook treats enablement as a pre-flip blocker.

### Runbook verification

Cross-checked `docs/runbooks/strict-tenant-refuse-flip.md` on `origin/dev`:

- Line 147 — pre-flip checklist item: «**Issue #499 (XAUTOCLAIM reaper) merged** — see §«Automatic DLQ» above. Opt-in via `PEL_REAPER_ENABLED`; flip alongside» (checked ✓)
- Line 157 — flip-day action checkbox: «At flip time: `PEL_REAPER_ENABLED=true` set in [env]» (unchecked — operator action, expected)
- Line 167 — explicit framing: «is not advisory — it's a pre-flip blocker. XAUTOCLAIM reaper (#499 — PR #508) merged…»

Operator flip-day ordering is enforced by the unchecked checkbox at line 157 — the runbook makes this the action item rather than an aspiration.

### Verification recommendation

On flip day, the operator confirms checkbox at line 157 BEFORE flipping the strict flag. No additional audit action.

### Cross-link

- #499 — PEL reaper tracker
- #500 — D-2 operator ceilings (the PEL alert ceiling depends on having a working drainer)
- `apps/workers/reaper.py` module docstring

---

## Pre-flip audit conclusion

The code surface for the `STRICT_TENANT_REFUSE` flip itself is **hardened and ready** under the documented threat model. §§1, 2, 4, 5 all pass — the flag flip is safe on flip-day operator readiness.

§3 was downgraded from 🟢 GREEN to 🟡 YELLOW during the in-house adversarial pass on this audit. The finding is **not a flip-day blocker** (the 7 unblocked sites are sync HTTP paths protected by JWT at the view boundary, not stream consumers subject to `STRICT_TENANT_REFUSE`) but **is a pre-pilot architectural debt item** of the same character as #925. Tracker #968 filed.

This is exactly the value of adversarial framing per memory `feedback_h3_waiver_pattern` N=5 instance: a friendly review would have approved the initial 🟢 §3 verdict because the grep matched the documented contract. The adversarial pass refuted the contract scope itself.

W3 will deliver a separate **post-flip delta-pass** within 24–48h after the flip is executed, exercising:

- Real PEL backlog observability under load
- DLQ routing behaviour with live refused entries
- Operator ceilings trip behaviour at production scale
- W2 PII tokenization PR #842 cross-stream review (once ready-for-review)

---

## Findings filed

This pre-flip pass surfaced **one new tracker** via the adversarial pass on §3:

- **#968** — G5.1 edge too narrow; 7 additional write-path imports of `transitions` + `feedback` from `miniapp_api` + `admin_api` not covered. To be enforced via the `tools/lint/` AST-linter (Option B). **Severity:** MUST_FIX_PRE_PILOT (not flip-day blocker).

§§1, 2, 4, 5 verified against `origin/dev` with no new findings. §5 procedural dependency already enforced by the flip runbook.

Existing tracker landscape relevant to this audit: #499 (PEL reaper), #500 (D-2 ceilings), #502 (subscriber audit), #925 (G5.1 single-site), #927 (G6.2 idempotency).

— W3 (Zeta, security backstop)
