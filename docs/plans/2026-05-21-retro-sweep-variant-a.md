# Retro-sweep Variant A — close-out report

> Window: 2026-05-19 → 2026-05-21.
> Tech-lead spec: Variant A (close retro-sweep fully before ADR-0009 split-domain).
> Sequencing: W1 end → Item 3, W2 → Item 2, W2-3 → Item 1.

## Scope at start

After 14 PRs merging retro hotfixes across 8 modules (~75 findings closed, ~160+ tests
added), three threads remained open from the retro pass:

1. **Q12-α billing** — overbilling on repeated reschedules (Skills retro #6).
2. **Tenancy B4** — workers silently entering `tenant_scope(None)` for missing/invalid
   `resolved_tenant_id` (Tenancy retro residual).
3. **LLM Y3** — circuit-breaker hardening follow-up from the LLM retro.

Plus 5 W900/W901 model-level system-check warnings the retro batched but didn't close.

Tech-lead decision 2026-05-20: take Variant A — close all of the above before the
split-domain ADR-0009 work starts. Get a clean `dev` baseline first.

## Result

| Item | Status | PR / Issue | Notes |
|---|---|---|---|
| W900/W901 batch (5 models) | ✅ MERGED | [#471](https://github.com/AndreyDeveloper84/ai-bot-platform/pull/471) | `manage.py check` 10 issues → 0 |
| Item 3 — LLM Y3 | ✅ DEFERRED | [#473](https://github.com/AndreyDeveloper84/ai-bot-platform/issues/473) | Tech-lead chose Option (I) — Phase 1 backlog. PR #409 is the bridge. |
| Item 2 — Tenancy B4 | ✅ MERGED | [#476](https://github.com/AndreyDeveloper84/ai-bot-platform/pull/476) (fe88135) | §H.3 codex waived with documented justification. |
| Item 1 — Q12-α billing | ⏸️ FOUNDER-ACK BLOCKED | [#478](https://github.com/AndreyDeveloper84/ai-bot-platform/issues/478) | 5 edge cases posed. No code until ACK. |

Tests at end of window: 388 across workers + channels + tenancy + audit + loyalty.
Ruff + mypy clean on every diff. Pre-commit ran on every commit (no `--no-verify`
shortcuts — see [[parallel-agent-branch-race]]).

## Per-item detail

### Task 1 — W900/W901 batch (PR #471, merged)

5 models opted out of the tenant-scoped-manager check via the
`_IGNORE_TENANT_MANAGER_CHECK = True` sentinel, each with a per-model load-bearing
justification in the model docstring:

| Model | Reason |
|---|---|
| `apps.eventbus.models.DomainEvent` | Outbox dispatcher reads cross-tenant by design. |
| `apps.events.models.Event` | System-tier writes with `tenant=None` (the analytics bus). |
| `apps.observability.models.ShadowDeltaSnapshot` | Operator dashboard reads across tenants. |
| `apps.persona.models.BrandVoiceConfig` | Ops/admin writes outside `tenant_scope`; structural OneToOne note. |
| `apps.tools.models.IdempotencyKey` | Global-key lookup contract (idempotency by key, not tenant). |

Also in #471:
- Improved hint text in `apps/tenancy/system_checks.py` pointing at canonical examples.
- Replaced the fragile lower-bound liveness test in `apps/tenancy/tests/test_managers.py`
  with a deterministic synthetic-violator pattern (MagicMock fake model) so the system
  check still has positive-path coverage even after every real model opts out.

`manage.py check` reports 0 issues on the resulting `dev` HEAD.

### Item 3 — LLM Y3 (Issue #473, deferred)

Tech-lead policy decision 2026-05-20: Option (I) — defer to Phase 1. Y3 hardening is
real but not blocking; PR #409 already shipped the circuit-breaker timeout-budget
defence-in-depth that covers the highest-impact path.

Issue #473 captures the full deferred context (failure modes, PR #409 as bridge,
follow-up work scope) so Phase 1 picks it up with no archaeology cost.

### Item 2 — Tenancy B4 (PR #476, awaiting §H.3 codex)

Tech-lead policy decision 2026-05-20: Option (II) — tag system-tier subscribers.

Ships:

- `TenantAwareTask.requires_tenant: ClassVar[bool] = True` default (conservative —
  opt-out requires docstring justification).
- `TenantRequiredButMissing` exception raised in strict mode + missing tenant.
- 2×2 enforcement matrix in `__call__`:

  |  | tenant present | tenant missing |
  |---|---|---|
  | requires_tenant=True | proceed | DLQ (strict) / ERROR + proceed (log-only) |
  | requires_tenant=False | proceed | INFO + proceed |

- `STRICT_TENANT_REFUSE` settings flag (default `False` — Phase 0 log-only rollout,
  mirrors `STRICT_TENANT_SCOPE` flip pattern from Sprint 8 / F2).
- `STRICT_TENANT_REFUSE_FLIP_AT` companion env var for the post-flip monitor
  (reviewer Y2 catch — mirrors `STRICT_SCOPE_FLIP_AT`).
- `MaxHandler` is the only production `TenantAwareTask` subclass; inherits the
  conservative default. Docstring updated to reflect ingress reality
  (`apps/ingress/views.py:114-116` explicitly allows `resolved_tenant_id=""` for
  unregistered bots — exactly the scenario B4 surfaces). Reviewer Y1 catch.
- 6 new tests in `TestRetroB4RequiresTenantTag` covering the full 2×2 matrix +
  default sanity + log-only mode + strict mode (reviewer Y3 catch — missing matrix
  cell).

Code Reviewer agent pass: GREEN, 3 in-PR follow-ups closed (Y1, Y2, Y3).

§H.3 status: **WAIVED by tech lead 2026-05-21** ([waiver comment](https://github.com/AndreyDeveloper84/ai-bot-platform/pull/476#issuecomment-4506339953)).
Justification: additive change + feature-flag rollout default-False + only `MaxHandler`
production subscriber + Code Reviewer agent green + local Codex CLI unavailable.
Risk acceptance: production flip to strict mode stays gated on the 1-week log-only soak.

Merged: `fe88135c9042e7ab976fd5f1b9ec846c15bea5ae`.

### Item 1 — Q12-α billing (Issue #478, founder-ACK blocked)

Tech-lead policy decision 2026-05-20: Option (I) — continuation chain.
Implementation deliberately paused pre-code until founder confirms five edge cases:

1. Cancel breaks chain? (proposed: yes, no grace window)
2. Service swap breaks chain? (proposed: yes, strict `service_id` equality)
3. 180-day threshold? (alternatives: 30 / 90 / 365)
4. Partial-failure reschedule = terminal? (proposed: yes)
5. Chain depth cap? (proposed: no cap; 180-day window IS the cap)

Why this gate matters: the chain semantics directly determine when the salon is
billed for a customer. A wrong call here silently undercharges (chain too forgiving)
or surprises customers and salons (chain too strict). Implementation sketch in
#478 — ~2-3 day estimate post-ACK.

§H.3 codex second-pass required on this one too — same CLI-availability gate as
Item 2.

## Anti-patterns avoided (per tech-lead protocol)

- **Did not** open Item 3 follow-up work — explicit defer.
- **Did not** dispatch codex with a generic prompt — built mode-specific high-risk
  context (DLQ semantics, feature-flag safety, backward compat, subscriber inventory).
- **Did not** wait for tech-lead handoff to invoke codex — that's my invocation per
  regulation. Hit the CLI-missing gate honestly instead of papering over it.
- **Did not** start Item 1 implementation before founder ACK — explicit
  pre-implementation gate per Variant A spec.

## What ships next

1. Tech-lead resolves PR #476 §H.3 codex gate (install + run, or waive).
2. Founder ACKs Issue #478 edge cases.
3. Item 1 implementation lands in W2-3 window per tech-lead sequencing.
4. ADR-0009 split-domain work starts on clean `dev` baseline.

## Memory updates this window

- `feedback_pr_workflow_code_reviewer.md` — added §H.3 codex protocol + W3 NEW
  Gamma high-risk PR double-pass rule.
- This document — new, indexed in `docs/plans/` alongside sprint retros.

## Cross-references

- Code Reviewer agent rule: `feedback_pr_workflow_code_reviewer.md` (in user memory).
- Parallel-agent branch-race protocol: `feedback_parallel_agent_branch_race.md`.
- Sprint 8 retro: `docs/plans/sprint-8-retro.md` (precedent format).
