# Plan — Sprint 9: Skill-port for feature parity (week 19) — pivot from canary

> Theme: port 7 nutrition/health skills from `legacy_maxbot/handlers/` to
> `apps/skills/` so the platform reaches feature parity with `mysite/maxbot/`
> BEFORE any canary cutover. Canary work moves to Sprint 10.
> Reference: `docs/plans/sprint-8-retro.md` + `mysite/docs/arch/PHASE0_DESIGN.md`.
> Baseline: `main @ 254af69` (Sprint 8 closed, 33/38 in-repo Done).
> Linear epic: [DRF-785](https://linear.app/drfproject/issue/DRF-785).

## PIVOT — what changed (2026-05-14)

Original Sprint 9 plan (staging soak + canary 5% → 50%) was **cancelled** after discovering:

1. **mysite бот сам отрабатывал запросы некорректно** — Gating delta vs mysite ground truth gives a negative signal. Comparing the new platform to a broken baseline rewards matching the broken behavior.
2. **Трафик остановлен** — There's no live MAX traffic to canary against. The shadow-mode pipeline still runs, but the upstream is dry.
3. **`ai-bot-platform/apps/` is missing 7 key skills** that exist in `legacy_maxbot/handlers/`: food_scanner, water, nutrition_anketa, food_clarify (DRF-358), food_correction, cross_domain, health_screening.

Without feature parity, a canary at 5% — let alone 50% — would route real users into a system where the food/water/nutrition flows simply don't exist. The best part of the bot's UX is missing.

**New Sprint 9 goal:** port 7 skills + Ayla integration. Canary scope moves to Sprint 10 in its entirety.

The 32 sub-issues from the original plan (DRF-786..817) are all in `Canceled` state on Linear. New Sprint 9 backlog: DRF-818..835.

## Carry-overs in flight (NOT new work)

- **M1/M2/M3** DRF-724/725/726 — mysite catalog viewsets + X-Service-Token + delta-push webhook (parallel agent, `[FROZEN-EXEMPT]`). Still active. Platform-side consumer (was Sprint 9 / C-track) moves to Sprint 10.
- **F1** DRF-730 — prod STRICT flip — superseded by Sprint 10 F-flip.

## Architecture (decision baked)

**Гибрид:**
- **Platform** = skill state machines + диалог + LLM-промпты + UI keyboards
- **Ayla** (`dev.gobeauty.site`) = food recognition (photo → nutrition), norm calc, food/water diary persistence
- Bot ↔ Ayla через `apps/integrations/ayla/nutrition_client.py` + `X-Service-Token` (schema fix per `reference_ayla_backend.md`: `norms.*` nesting)

Re-use as much of `legacy_maxbot/handlers/*.py` as possible (~1800 LOC across the 7 handlers), but rewrite to Sprint 6 RFM pattern + Sprint 7 skill framework. DRF-358 fixes (already in `mysite/` PR #145) are part of P4 + P7 — port the tactical fix, not the old buggy behavior.

## What ломалось в food scanner (DRF-358 root cause)

1. `parse_beverage` regex слабый — пропускал «Сок 0,5л» (decimal comma), «Кофе с молоком»
2. Когда text похож на еду но parser пропустил — бот выдавал холодное «не могу с заказом» вместо тёплой clarification card
3. На упоминание физической боли — мгновенный tool-call с пустыми options ВМЕСТО diagnostic-first консультации (1-2 вопроса)
4. Red-flag симптомы не уводили к врачу

DRF-358 уже сделан в `mysite/` (PR #145). P4 (`food_clarify`) + P7 (`health_screening`) повторяют этот fix на платформе.

## Decomposition — 18 sub-tasks across 5 tracks

### P-track — Skill ports (7 tasks)

| ID | Skill | Source (legacy_maxbot) | Estimate | Notes |
|---|---|---|---|---|
| [DRF-818](https://linear.app/drfproject/issue/DRF-818) | **P1** food_scanner | `handlers/food_scanner.py` (554 LOC) | 1.5d | Photo → Ayla recognize → diary log |
| [DRF-819](https://linear.app/drfproject/issue/DRF-819) | **P2** water | `handlers/water.py` (460 LOC) | 1d | Text parser + DRF-358 bug fixes |
| [DRF-820](https://linear.app/drfproject/issue/DRF-820) | **P3** nutrition_anketa | `handlers/nutrition_anketa.py` (823 LOC) | 2d | 8-step FSM (largest port) |
| [DRF-821](https://linear.app/drfproject/issue/DRF-821) | **P4** food_clarify | `mysite/maxbot/handlers/food_clarify.py` | 0.8d | DRF-358 fallback card |
| [DRF-822](https://linear.app/drfproject/issue/DRF-822) | **P5** food_correction | `handlers/food_correction.py` | 0.8d | Post-recognition correction |
| [DRF-823](https://linear.app/drfproject/issue/DRF-823) | **P6** cross_domain | `handlers/cross_domain.py` | 0.8d | Mixed-domain message routing |
| [DRF-824](https://linear.app/drfproject/issue/DRF-824) | **P7** health_screening | `handlers/health_screening.py` + DRF-358 T04 | 1d | Diagnostic-first + red-flag |

### I-track — Ayla integration (3 tasks)

| ID | Task | Estimate | Notes |
|---|---|---|---|
| [DRF-825](https://linear.app/drfproject/issue/DRF-825) | **I1** nutrition_client port + schema fix | 1d | 4 endpoints + `norms.*` unwrap |
| [DRF-826](https://linear.app/drfproject/issue/DRF-826) | **I2** ayla_user_proxy port | 0.5d | get_or_create + BotUser.ayla_user_id |
| [DRF-827](https://linear.app/drfproject/issue/DRF-827) | **I3** CR-3 breaker + retry | 0.4d | 5-in-60s open; 30s half-open |

### Q-track — Quality + smoke (3 tasks)

| ID | Task | Estimate |
|---|---|---|
| [DRF-828](https://linear.app/drfproject/issue/DRF-828) | **Q1** Internal smoke plan + harness | 0.5d |
| [DRF-829](https://linear.app/drfproject/issue/DRF-829) | **Q2** Golden replay fixtures per skill (40-50 total) | 1d |
| [DRF-830](https://linear.app/drfproject/issue/DRF-830) | **Q3** E2E suite vs `dev.gobeauty.site` | 0.5d |

### D-track — Domain support (3 tasks)

| ID | Task | Estimate | Notes |
|---|---|---|---|
| [DRF-831](https://linear.app/drfproject/issue/DRF-831) | **D1** Voice examples + DRF-358 prompts | 0.5d | Blocks P7 |
| [DRF-832](https://linear.app/drfproject/issue/DRF-832) | **D2** Keyboards/UI builders | 0.5d | Blocks P1, P3, P4, P5 |
| [DRF-835](https://linear.app/drfproject/issue/DRF-835) | **D3** FSM state-machine pattern | 1d | Blocks P3 (anketa) |

### G-track — Close-out (2 tasks)

| ID | Task | Estimate |
|---|---|---|
| [DRF-833](https://linear.app/drfproject/issue/DRF-833) | **G1** Sprint 9 close-out — roll-up + memory | 0.2d |
| [DRF-834](https://linear.app/drfproject/issue/DRF-834) | **G2** Sprint 10 plan kickoff (canary-only) | 0.3d |

**Total estimate:** ~13 effective working days at AI velocity, expected calendar 7-9 days at Sprint 8 cadence (~3-4 tasks/day).

## Dependency graph

```
D1 (voice examples) ──┬─→ P7 (health_screening)
                      ├─→ P1, P2, P3, P4, P5 (sharper prompts)
                      └─→ P6 (cross_domain LLM splitter)

D2 (keyboards) ───────┬─→ P1, P3, P4, P5
                      └─→ ad-hoc UI in any future skill

D3 (FSM pattern) ─────┬─→ P3 (anketa, biggest)
                      └─→ P5 (correction state)

I1 (nutrition_client) ┬─→ P1, P2, P3, P5
                      └─→ I2, I3, Q3

I2 (user_proxy) ──────→ P3 (anketa needs Ayla user_id)

I3 (breaker) ────────→ wraps I1, I2 (post-port hardening)

P1..P5 ───────────────→ P6 (cross_domain integrates all)

All P + Q2 ──────────→ Q1 (smoke after skills land)

All ─────────────────→ G1, G2 (close-out)
```

**Critical path:** D3 → P3 (anketa is biggest single task at 2d). Q2 + D1 + D2 run in parallel with P-track.

## Exit gate (all must hold)

- [ ] **7 skills live** в `apps/skills/` под Sprint 6 RFM pattern
- [ ] **Ayla integration green** — `nutrition_client` against `dev.gobeauty.site`, все 4 endpoint'а отвечают
- [ ] **Internal smoke** — каждый из 7 skills проверен вручную end-to-end через Telegram dev-bot test harness
- [ ] **Golden replay fixtures** — Q2 fixtures committed для каждого skill (40-50 total)
- [ ] **E2E suite green** в CI when `AYLA_BASE_URL` + `AYLA_SERVICE_TOKEN` set
- [ ] **DRF-358 parity** — food_clarify + health_screening работают как в `mysite/maxbot` (PR #145)
- [ ] **Zero P1 blockers** from Q1 smoke; P2 fixes either resolved in-sprint or explicitly deferred to Sprint 10

## Decisions baked (8)

| # | Decision | Choice | Why |
|---|---|---|---|
| 1 | Skill-port architecture | **Hybrid: platform = state-machine + LLM + UI, Ayla = nutrition data** | Ayla nutrition backend уже работает на `dev.gobeauty.site`. Не дублировать food-recognition / norm-calc на платформе. |
| 2 | Full port vs wrapper | **Full port** (all 7 skills) | Wrapper-only оставит multi-step anketa в Sprint 10, что блокирует часть real-user testing. Лучше один спринт боли. |
| 3 | DRF-358 parity | **Replicate fix** in P4 + P7 (not port buggy version) | mysite PR #145 already proved the fix. Don't carry parser bugs forward. |
| 4 | FSM state storage | **`Conversation.skill_state` JSON field** (D3) | Survives bot restart; one schema per skill; mirrors Sprint 6 SkillContext pattern. |
| 5 | Cross-domain routing | **Serial** (одно за другим) | Parallel (2 ответа одновременно) confusing UX. Serial = simpler state, easier to debug. |
| 6 | Tests | **3 layers: unit + golden + E2E gated** | Unit (no network) on every commit; golden on every PR; E2E manual/nightly with creds. |
| 7 | Photo storage | **Ayla holds blobs; platform stores only Message refs** | Avoids GDPR/152-ФЗ blob-replication; aligns with Ayla as data SoT. |
| 8 | Voice examples | **Port `mysite/maxbot/voice_examples.py` as-is** + add nutrition categories | Brand voice continuity. No regression in tone. |

## Freeze policy update

CLAUDE.md `mysite/maxbot/.FROZEN` policy currently states freeze lifts "when Sprint 10 / week 21-22 of Phase 0 completes". After this pivot:

- **Freeze lift moved to week 22** (one-week buffer; canary work compressed in Sprint 10).
- Update needed in `CLAUDE.md` lines 5-8 + `mysite/maxbot/.FROZEN` — to be done as part of G2 / Sprint 10 kickoff.

## Risks

1. **`nutrition_anketa` port complexity** — 823 LOC of legacy code with multi-step FSM is the riskiest single task. *Mitigation:* D3 ships FSM helper first; P3 uses pattern. Budget 2 days dedicated.
2. **Ayla API schema drift** (`norms.*` nesting + possibly others) may surface more mismatches as port progresses. *Mitigation:* I1 acceptance includes smoke against live `dev.gobeauty.site`; Q3 E2E suite catches schema-drift regressions.
3. **DRF-358 parser edge cases** — legacy parser had at least 4 known bugs from 2026-05-08 smoke. There may be more not yet found. *Mitigation:* Q2 golden fixtures explicitly include known DRF-358 bug repros; food_clarify (P4) is the safety net for any miss.
4. **No real user traffic for validation** — all testing is internal/synthetic. *Mitigation:* Sprint 10 starts with limited beta-group of ~5-10 testers before canary; AI velocity helps catch many regressions in Q1 smoke.
5. **Sprint 9 spillover** → Sprint 10 canary delayed → freeze lift past week 22. *Mitigation:* G-track explicitly notes "Done if all P+I+Q+D green; In Progress with carry-over if any skill slipped". Honest about state.

## Scope warning

18 tasks but several are heavy (P3 = 2d, P1 = 1.5d). At AI velocity (~3-4 tasks/day for small tasks, 1 task/day for heavy), effective length 7-9 calendar days. Tight for cycle 9 (May 17-24).

Contingency picks (in order):
- **G2** Sprint 10 plan kickoff → drop to Sprint 10 Day 1
- **Q3** E2E suite vs Ayla → defer if I1 staging-smoke is enough confidence
- **P5** food_correction → defer (food_scanner P1 minus correction is still usable)
- **P6** cross_domain → defer (skills work independently; cross_domain is polish)

Hard-gated (cannot defer):
- **D1, D2, D3** support layer (blocks 3+ P-tasks each)
- **I1, I2, I3** Ayla integration (blocks all P-tasks)
- **P1, P2, P3, P4, P7** core nutrition + health skills + DRF-358 fixes
- **Q1, Q2** smoke + golden fixtures (gate the exit)

## Sprint 9 → Sprint 10 hand-off (by design)

By design, the following move to Sprint 10:

- **Canary 5% → 100% ramp** (all the work cancelled from this sprint)
- **F1 STRICT flip + F2 monitor arm**
- **PagerDuty setup + dual-channel alerting**
- **All R-runbooks** (tenant-onboarding, incident-response, security-incident, on-call)
- **Mysite catalog C-track integration** (consumer for M1/M2/M3 webhook + viewsets)
- **N4 ground-truth capture + G3 replay-diff activation** (only relevant once we have a stable target to capture against)
- **`mysite/maxbot/.FROZEN` lift** (after 100% cutover holds for the required soak window)

Sprint 10 plan kickoff is G2 / DRF-834.

## References

- `docs/plans/sprint-8-retro.md` — what we learned
- `legacy_maxbot/handlers/` — the source code being ported
- `mysite/maxbot/services/nutrition_client.py` — the Ayla client to port
- `mysite/maxbot/voice_examples.py` — voice examples source
- `mysite/maxbot/handlers/food_clarify.py` — DRF-358 fix source
- Memory: `reference_ayla_backend.md` (schema drift), `feedback_decompose_then_close_parents.md`, `feedback_verify_linear_state_after_update.md`
