# Sprint 8 Retro — Observability + Shadow mode

> Cycle window: 2026-05-13 → 2026-05-14 (cycle `791bbff1`).
> Repo end-state: `main @ 254af69`.
> Linear epic: [DRF-699](https://linear.app/drfproject/issue/DRF-699).
> Plan: [`sprint-8-observability-shadow.md`](sprint-8-observability-shadow.md).

## Quantitative

| Metric | Plan | Actual | Δ |
|---|---|---|---|
| In-repo sub-issues | 35 | 33 Done | −2 (M-track, F1 out by design) |
| Cross-repo (M-track) | 3 | 1 In Progress / 2 Todo | parallel agent owns |
| PR merges | ~30 | **30 substantive** (#47..#75, ex. 7 ayla bumps) | on plan |
| Working hours | ~10 days @ 3-4/day | ~2 calendar days @ AI velocity | scope-velocity match |
| Code-review fix-waves | not planned | 6 PRs (#68/#70/#71/#72/#73 + parts of #75) | added mid-sprint |
| Test suite | ≥267 (Sprint 2.5 baseline) | passing across all merged PRs | green |

## What worked

1. **38-task decomposition with 10-track grouping** survived contact. Tracks didn't bleed — N never reached into S; G-gates stayed orthogonal to T-instrumentation.
2. **Mid-sprint code-review audit (ln-640..647)** — 5 fix-waves landed inside the sprint without slipping scope. Catching `P0` (Redactor surface), `P1` cycles (audit↔tenancy, orchestrator→channels), and dead-mass `_TurnRow` merge **before** Sprint 9 staging soak was the right call. Cycles found after F1 flip would have been mid-incident debug.
3. **Conditional-skip pattern for G3** — `pytest.mark.skipif` + `TestGroundTruthSkipReason` (always-runs documentation test) surfaces skip reason in CI summary. Reusable for any test gated on captured fixture.
4. **F2 monitor armed-by-env-var** — `STRICT_SCOPE_FLIP_AT` lets ops trigger 24h watch declaratively at the F1 flip moment, no code deploy. Self-rescheduling Celery (`apply_async(countdown=900)`) avoids a separate watchdog process.
5. **Callback-registry pattern** — broke audit↔tenancy and orchestrator→channels cycles without forcing a layer move. Foundation/domain/feature pyramid clean; pattern likely reusable for any future feature→domain back-reference.
6. **Parallel agent for M-track** — splitting cross-repo `[FROZEN-EXEMPT]` work off the main sprint flow avoided the agent on this thread waiting on mysite review. Critical-path discipline.

## What slowed us

1. **Linear API instability** (Cloudflare 502s + rate-limit 2500/h). Several batch updates threw `Fetch failed` while the underlying mutation landed — surfaced existing memory `feedback_verify_linear_state_after_update.md`. Adding `getIssueById` re-check after every batch is now the steady-state pattern.
2. **mypy mismatches snuck through local pytest** — F2 fixture annotation (`-> None` with `yield`) and G3 conditional import (`apps.replay.runner.run_fixture_set`) only failed in CI mypy step. Local `uv run mypy <path>` before push prevents the round-trip; memory `feedback_check_ci_after_push.md` already captures the principle but it slipped here.
3. **ruff format reformatting at commit time** caused `gh pr create` warnings about uncommitted changes — cosmetic but distracting. Adding `uv run ruff format` to the pre-push muscle memory closes the gap.
4. **OTel processor pollution across test modules** — process-global `set_tracer_provider` collision required per-module `_PROCESSOR_INSTALLED` guard. Sprint 9 OTel work should standardise an `_otel_test_fixtures` helper to avoid re-deriving the pattern per test file.

## Carry-overs in motion (not Sprint 9 backlog — already owned)

| Item | Owner | Path |
|---|---|---|
| **M1** DRF-724 mysite catalog viewsets | Parallel agent | mysite cross-repo PR, `[FROZEN-EXEMPT]` |
| **M2** DRF-725 X-Service-Token mw | Parallel agent | chains on M1 |
| **M3** DRF-726 delta-push webhook | Parallel agent | chains on M2 |
| **F1** DRF-730 prod `STRICT_TENANT_SCOPE=strict` flip | Lead | gated on 7-day clean shadow → Sprint 9 staging |

These are **active**, not deferred. Sprint 9 plan must account for them landing mid-sprint and the C-track (consumer-side) needing to wire against them.

## Surprises

1. **Track-G G3 skeleton + skip is more valuable than the full test.** Activates on N4 fixture capture without code change. Sprint 9 only needs to drop the JSON.
2. **F2 monitor is cheap** — 50 LOC + 8 tests for a 24h post-flip watchdog. Doesn't justify a separate service; Celery beat is fine.
3. **Audit suite (ln-640..647) found cycles we knew about but had not prioritised.** Running it inside Sprint 8 instead of after Sprint 10 saved staging-soak debug time. Worth scheduling once per phase, not once at the end.

## Recommendations for Sprint 9

1. **Sprint 9 = staging soak + M-track integration + F1 cutover + 5-25% MAX canary**. Don't try to push past 25% in this cycle; the gate before 50% needs at least one full week of canary data.
2. **Pre-canary smoke runbook**: define ahead of cutover, not during. `tenant-onboarding.md` and `incident-response.md` runbook completion is gating, not nice-to-have.
3. **On-call rotation** must establish *before* canary turn-up. Even 2-person rotation (Lead + Dev) is sufficient for 5% canary, but rotation needs to exist.
4. **G3 fixture capture early** — N4 game-day is a 1-2h job. Schedule it Day 1 of Sprint 9 so the gate is active throughout the soak.
5. **F2 monitor dry-run on staging** before prod arm. Verify Telegram alert lands when seeded violation row is inserted.
6. **Replay sampling tune-down (IM-3)** stays in Sprint 9 plan but is **last** — only after 25% canary stable for 3+ days. Don't lower sampling before canary data is proven.

## Going-into-Sprint-9 baseline

- `main @ 254af69` — all in-repo Sprint 8 code shipped.
- F2 monitor in repo, armed by `STRICT_SCOPE_FLIP_AT` env var (currently unset).
- G3 test in repo, skipped pending `tests/fixtures/mysite_ground_truth/golden_faq_replies.json`.
- Shadow-mode infra live: nginx tee config (N1), `Tenant.shadow_mode` flag (S1), daily delta + Telegram digest (S3/S4), admin dashboard (D1-D3).
- Observability live: OTel SDK + Sentry + JSON logs + trace_id propagation across pipeline.
- `/readyz/` extended with chromadb + audit probes (G4 / DRF-735).
- 6 runbook docs in repo (rollback, shadow-mode-launch, strict-scope-flip drafts).
- 2 runbooks still skeletons: `tenant-onboarding.md`, `incident-response.md`. `security-incident.md` partial.
