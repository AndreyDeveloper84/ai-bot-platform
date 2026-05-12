# Plan — Sprint 6: ClientProfile/RFM + Orchestrator pipeline (F0.9 + F0.11 partial)

> Theme: full turn() pipeline wiring all layers + ClientProfile/RFM scoring.
> Reference: `mysite/docs/arch/PHASE0_DESIGN.md` §2.3 Sprint 6 (lines 367-374), §3.2, §5.
> Baseline: `main @ c0e41d2` — Sprint 5 closed (replay infra live + 80 fixtures + CI strict gate).
> Linear epic: **TBD** — `[Sprint 6] ClientProfile + Orchestrator (week 13-14)`.

## Context

Sprints 1-4 shipped the foundation (tenancy + ingress + skills + prompts + voice). Sprint 5 shipped the replay/safety net. Sprint 6 finally wires the **production pipeline** — `apps/orchestrator/pipeline.py::turn()` — that orchestrates every layer (memory → intent router → safety → skill → tools → safety → composer) end-to-end. Without this, every previous sprint's component is plumbing nobody calls.

In parallel: F0.9 ClientProfile + RFM scoring. Daily Celery recompute + signal-based real-time updates. Phase 0 wires the **signal contract** (no real Booking facts yet — synthetic data); Phase 1 hooks actual Booking events.

## Scope from design doc — Sprint 6

- **F0.9 ClientProfile + RFM**
  - `apps/identity/services/`: rfm.py, ltv.py, churn.py, tier.py
  - Daily Celery `recompute_profiles` task
  - Signal-based real-time updates on Booking events (contract only — Phase 1 hooks real Booking)
- **F0.11 Orchestrator pipeline (partial; finished in Sprint 7)**
  - `apps/orchestrator/pipeline.py::turn(channel_message)` — 19-step pipeline per §5.1
  - `apps/orchestrator/intent_router.py` — gpt-4o-mini structured JSON output
  - `apps/orchestrator/safety/pre_check.py + post_check.py` — keyword regex guards
- **Exit gate**: ClientProfile populated from synthetic visit data; orchestrator pipeline runs end-to-end with FAQ stub skill.

## Cleanup deliverable — Sprint 5 carry-overs

Before opening Sprint 6 work, address the carry-overs documented on DRF-525:

- **Swap `_build_default_pipeline_fn`** in `apps/replay/__main__.py` to wrap the new `apps.orchestrator.pipeline.turn` (this is the natural endpoint of Sprint 6 itself — see W3 below).
- **Optional `--isolated` flag** for the replay CLI to dodge Sprint 3 skill destructive side-effects when running against the legacy dispatcher.

These land naturally as part of W3 / I3.

## Decomposition — 28 sub-tasks across 4 tracks

### Track P — ClientProfile + RFM (F0.9) — 8 tasks

- **P1** — `apps/identity/models.py::ClientProfile`: OneToOne(BotUser, primary_key=True), tenant FK PROTECT, all RFM/LTV/risk/behavior/loyalty fields per §3.2. Migration + admin (read-only, computed-data view). Auto-create on BotUser save via signal.
- **P2** — `apps/identity/services/rfm.py::compute_rfm(bot_user, *, as_of=None) -> RFMResult`: pure function from a `BookingFact`-shaped iterable (Phase 0 = synthetic generator; Phase 1 = real Booking). Returns recency_days, frequency_visits, monetary_total, rfm_segment ∈ {champion, loyal, at_risk, hibernating, new}. Segment thresholds in `settings.RFM_THRESHOLDS` so we can tune without code.
- **P3** — `apps/identity/services/ltv.py::compute_ltv(bot_user, *, as_of=None) -> LTVResult`: total_spend + predicted_ltv_12m (Phase 0 = simple frequency × avg_check linear projection; Phase 1 plugs ML model behind same signature).
- **P4** — `apps/identity/services/churn.py::compute_churn_risk(bot_user, *, as_of=None) -> float`: 0..1 score based on recency_days vs avg_visit_interval_days (Phase 0 heuristic; Phase 1 ML). + `lifecycle_stage` derivation (new/active/lapsing/churned).
- **P5** — `apps/identity/services/tier.py::compute_tier(profile) -> str`: bronze/silver/gold/platinum from monetary_total + frequency_visits thresholds (settings).
- **P6** — `apps/identity/services/recompute.py::recompute_profile(bot_user)`: orchestrates P2-P5, writes/upserts ClientProfile under tenant_scope, emits `profile_recomputed` event (B6 vocab — add to vocabulary.py if missing).
- **P7** — Celery beat `recompute_profiles_daily` (03:30 UTC, after audit cleanup): iterate active bot_users (last_seen ≥ 90d), call `recompute_profile`. Bulk-friendly (batch of 500, prefetch related). Audit row per batch.
- **P8** — Signal contract: `apps/identity/signals.py::on_booking_completed(sender, bot_user, ...)` connects to a `booking_completed` Django signal. Phase 0 wires the signal **as a stub** — synthetic test data fires it; real Booking model in Phase 1 fires it from `Booking.save()`.

### Track O — Orchestrator pipeline (F0.11 partial) — 10 tasks

- **O1** — `apps/orchestrator/pipeline.py::turn(channel_message: ChannelMessage) -> TurnResult`: 19-step function per §5.1. Each step is a small helper in a sibling module; pipeline glues them. Includes outer try/except → `pipeline_error` event + fallback message.
- **O2** — `apps/orchestrator/intent_router.py::classify(text, memory_snapshot, brand_voice) -> IntentDecision`: gpt-4o-mini call with structured JSON output (response_format), 200-token cap. Returns `{intent, skill, confidence, risk_level, missing_slots, reply_mode, needs_rag, needs_tool}`. Circuit-breaker-wrapped via Sprint 1 breaker.
- **O3** — `apps/orchestrator/safety/pre_check.py::pre_check(text, intent_decision) -> SafetyVerdict`: regex keyword guard (medical / acute symptoms / forbidden topics). Returns allow|clarify|block|handoff. Patterns live in `BrandVoiceConfig.safety_patterns` (already shipped Sprint 4).
- **O4** — `apps/orchestrator/safety/post_check.py::post_check(response_text, ctx) -> SafetyVerdict`: forbidden patterns regex + brand voice validator (delegates to existing `safety/voice_check.py`). Returns allow|revise|block.
- **O5** — `apps/orchestrator/composer.py::compose(skill_result, brand_voice) -> ComposedReply`: final text + UI keyboard render. Phase 0 = template-based; Phase 1 may add LLM polish layer.
- **O6** — `apps/orchestrator/memory/coordinator.py::load_snapshot(conversation) -> MemorySnapshot`: glues `memory.short_term.load(conversation)` + `memory.long_term.load(bot_user)` → unified snapshot for intent_router. Already-existing memory modules are reused.
- **O7** — `apps/orchestrator/tool_invoker.py::invoke_tool_calls(skill_result, ctx)`: iterates `skill_result.tool_calls_made`, looks up tool in registry, asserts skill permission, invokes with idempotency_key. Returns list of ToolResult; failures bubble to skill (skill decides retry/handoff).
- **O8** — Wire ReplayRecorder into pipeline step 18 — `recorder.capture(trace_id, steps)` after composer. Reuses Sprint 5 recorder; no new code in apps/replay.
- **O9** — Channel outbound integration: `apps/channels/<channel>/outbound.py::send_message(...)` called as step 19 with composed reply. Phase 0 = MAX only.
- **O10** — `tests/integration/test_pipeline_turn.py`: end-to-end through `turn()` with synthetic FAQ skill stub + mocked LLM. Validates 19-step contract (each step emits the expected event/audit row).

### Track I — Integration + replay polish — 5 tasks

- **I1** — Stub FAQ skill `apps/skills/faq/skill.py`: minimal `matches(text)` + `handle(ctx) -> SkillResult` returning canned response. Sprint 7 swaps for real KB-driven FAQ. Lets Sprint 6 exit-gate run end-to-end without waiting on F0.14.
- **I2** — Synthetic visit data fixture `tests/fixtures/synthetic_visits.py`: generates `BookingFact`-shaped records for N bot_users with realistic distribution (champions, loyal, at_risk, hibernating). Used by RFM tests + exit-gate demo.
- **I3** — Swap replay CLI default pipeline_fn → `apps.orchestrator.pipeline.turn` (closes Sprint 5 carry-over). Update `apps/replay/__main__.py::_build_default_pipeline_fn` + remove the SkillContext synthetic wiring.
- **I4** — Optional `--isolated` flag for replay CLI: each fixture gets fresh BotUser+Conversation in a savepoint. Closes the destructive-side-effects gap from Sprint 5.
- **I5** — Replay fixture authors land **5 new orchestrator-specific fixtures** under `apps/replay/fixtures/golden/orchestrator/`: intent routing, memory injection, post-check revise, tool-call chain, error path. Validates the pipeline against real golden traces in CI.

### Track G — Gates — 5 tasks

- **G1** — Cross-tenant leakage scanner: extend `_MODEL_REQUIRED_FIELDS` + sanity assertion for `ClientProfile`. 14 tenant-scoped models total.
- **G2** — `tests/e2e/test_orchestrator_e2e.py`: spin up minimal pipeline, drive a real channel message through `turn()` end-to-end, assert: Conversation created, Message rows written, ReplayTrace captured, ClientProfile recomputed, no strict_tenant_scope violation.
- **G3** — `apps/orchestrator/health.py`: pipeline health check (intent_router LLM reachable, breaker closed, FAQ skill responding) — wired into `/readyz/`. Sprint 8 observability builds on this.
- **G4** — Pipeline latency SLO test: 100 synthetic messages through `turn()` with mocked LLM at fixed latency; assert p95 ≤ budget per §5.2 (4000ms goal).
- **G5** — Sprint 6 epic close-out: Linear status flips + roll-up comment with sub-issue IDs + Sprint 5 carry-over receipt.

**Total: 28 sub-tasks** (8 P + 10 O + 5 I + 5 G).

## Decisions baked

| # | Decision | Choice | Why |
|---|---|---|---|
| 1 | Phase 0 ML for RFM/LTV/churn | Heuristic / linear projection | F0.9 says "signal-based real-time updates" — model quality not the bar; signature is. Phase 1 swaps ML behind same interface |
| 2 | BookingFact source | Phase 0 = synthetic generator in tests; Phase 1 = real `apps/booking/models.py::Booking` | Sprint 6 ships the contract, not the Booking model (Phase 1 lifts booking from mysite/) |
| 3 | RFM segment thresholds | `settings.RFM_THRESHOLDS` dict | Tunable without redeploy; PromptRegistry-adjacent governance (Sprint 4 pattern) |
| 4 | Intent router model | gpt-4o-mini structured JSON | §5.2 latency budget 1500ms; gpt-4o-mini cap |
| 5 | Safety pre+post check engine | Regex Phase 0 | NER deferred Phase 1 (same call as Sprint 5 replay redactor) |
| 6 | FAQ skill in Sprint 6 | Stub (canned response) | Real FAQ + KB lands Sprint 7 (F0.14); pipeline e2e doesn't block on KB |
| 7 | Recompute schedule | Daily 03:30 UTC | After audit cleanup (03:00) + replay cleanup (04:00) — avoid conflict |
| 8 | ChannelMessage shape | Reuse Sprint 2 `apps/channels/<channel>/inbound.ChannelMessage` | Don't reinvent; pipeline.turn signature pins this type |

## Critical files

### New
- `apps/identity/models.py` — `ClientProfile` (extend existing module)
- `apps/identity/services/rfm.py`, `ltv.py`, `churn.py`, `tier.py`, `recompute.py`
- `apps/identity/signals.py` — booking_completed signal handler
- `apps/identity/tasks.py` — `recompute_profiles_daily` Celery task
- `apps/identity/migrations/0XXX_clientprofile.py`
- `apps/identity/tests/test_rfm.py`, `test_ltv.py`, `test_churn.py`, `test_recompute.py`
- `apps/orchestrator/pipeline.py` — `turn(channel_message)`
- `apps/orchestrator/intent_router.py`
- `apps/orchestrator/safety/pre_check.py`, `post_check.py`
- `apps/orchestrator/composer.py`
- `apps/orchestrator/memory/coordinator.py`
- `apps/orchestrator/tool_invoker.py`
- `apps/orchestrator/health.py`
- `apps/orchestrator/tests/test_pipeline_turn.py`, `test_intent_router.py`, `test_pre_check.py`, `test_post_check.py`, `test_composer.py`
- `apps/skills/faq/skill.py` (stub)
- `tests/fixtures/synthetic_visits.py`
- `tests/integration/test_pipeline_turn.py`
- `tests/e2e/test_orchestrator_e2e.py`
- `apps/replay/fixtures/golden/orchestrator/*.yaml` (5 fixtures)

### Modified
- `apps/replay/__main__.py::_build_default_pipeline_fn` → wrap `orchestrator.pipeline.turn`
- `config/settings/base.py` — RFM_THRESHOLDS, TIER_THRESHOLDS, recompute_profiles_daily beat schedule
- `apps/events/vocabulary.py` — add `profile_recomputed`, `intent_classified`, `safety_pre_check`, `safety_post_check` if not already present
- `apps/orchestrator/urls.py` — `/readyz/` aggregator includes pipeline health
- `tests/integration/test_cross_tenant_leakage.py` — ClientProfile factory + sanity

### Reused (no modification needed)
- `apps.tenancy.context::current_tenant` / `tenant_scope`
- `apps.audit.services::write_audit`
- `apps.events.services::emit` + B6 vocabulary
- `apps.orchestrator.safety.voice_check::validate_voice` (Sprint 4 / C4)
- `apps.persona` — BrandVoiceConfig
- `apps.promptreg` — PromptRegistry for intent prompt + safety patterns
- `apps.experiments` — for routing decisions
- `apps.replay.recorder` — Sprint 5 — capture in pipeline step 18
- `apps.identity.BotUser`
- `apps.conversations.Conversation, Message`
- Sprint 1 breaker, idempotency, audit
- Sprint 2 ingress + channel adapter + worker
- Sprint 3 skill registry + dispatcher + PrivacyConsentSkill + HumanHandoffSkill

## Verification per task

- Each commit: pytest green (no regression), mypy + ruff clean, CI green on push, Linear status Done with acceptance comment.
- Sprint exit gate:
  - `tests/e2e/test_orchestrator_e2e.py` drives one ChannelMessage through `turn()` end-to-end → all 19 steps fire, all events emitted, ReplayTrace captured, ClientProfile recomputed.
  - `recompute_profiles_daily` populates ClientProfile from synthetic visit data; admin shows non-empty rows.
  - `python -m apps.replay run --tenant formula-tela --fixture-set golden` (with new default pipeline_fn) passes 30/30 — closes Sprint 5 carry-over.

## Risks

1. **Intent router latency / cost** — gpt-4o-mini at 1500ms budget per turn × 24h → daily cost spike if traffic surges. Mitigate: settings-driven model fallback (gpt-4o-mini default, gpt-4o for high_risk only via `risk_level` heuristic from BrandVoiceConfig).
2. **Synthetic BookingFact ≠ real shape** — Phase 0 RFM thresholds tuned against synthetic data may produce wrong segments on real visits. Mitigate: P2 returns explicit RFMResult dataclass, segments derive from settings — Phase 1 retunes thresholds without code change.
3. **Memory load (long_term) p95** — design budget <100ms but real bot_users with 100s of messages may exceed. Mitigate: O6 caps history to last N messages (settings.MEMORY_HISTORY_LIMIT, default 20) + Redis cache layer.
4. **Pipeline crash isolation** — one skill bug shouldn't break the whole turn. O1 outer try/except handles this; observability via Sentry (Sprint 8 wires this) catches the rest.
5. **Replay CLI default-pipeline_fn swap (I3) breaks existing tests** — Sprint 5 G2 test uses synthetic pipeline_fn explicitly, so it's safe. CLI smoke against live skills was already broken pre-Sprint-6 (Sprint 5 carry-over documented). Watch for tests/e2e/test_replay_golden.py — keep its synthetic path; new tests use the real path.
6. **Recompute task scope** — daily iteration over all bot_users is O(N); Sprint 7 may need partitioning. Mitigate: P7 batches 500 + index on `(tenant, last_seen)` already exists per §3.2 model spec.

## Outputs

1. Linear: 28 sub-tasks under the new Sprint 6 epic + 3 close-out comments for Sprint 5 carry-overs (DRF-525).
2. Commits: atomic per task on `main`, CI green per commit.
3. Test growth: ~700 unit tests → ~850; 3 e2e → 5.
4. New artefact: `apps/orchestrator/pipeline.py::turn` live; ClientProfile populated from synthetic data; replay CLI runs golden 30/30 against real pipeline.
5. Sprint 5 carry-over closure: replay CLI default pipeline_fn now real; `--isolated` flag shipped.
