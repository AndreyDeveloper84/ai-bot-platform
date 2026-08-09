# Runbooks

Operational playbooks for the platform. Each runbook follows a 7-section template (see [`_template.md`](_template.md) — TBD) so on-call can scan a familiar structure under pressure.

## Why skeletons now (Sprint 0)?

When the time comes to actually run a procedure (rolling back the canary at 02:30, debugging a replay diff before launch, onboarding tenant #2), nobody has time to *also* design the procedure document. Skeleton = 80% structure already there; the remaining 20% is the muscle memory of the engineer who fills it in. Writing the skeleton now also forces us to predict every step — and discover the missing tools.

## Index

| Runbook | Status | Filled in sprint | Owner |
|---|---|---|---|
| [`tenant-onboarding.md`](tenant-onboarding.md) | **complete** | Sprint 10 / O4 (DRF-865) | Lead |
| [`replay-debugging.md`](replay-debugging.md) | skeleton | Sprint 5 | Dev1 |
| [`incident-response.md`](incident-response.md) | **complete** | Sprint 10 / O5 (DRF-866) | Lead |
| [`rollback-procedure.md`](rollback-procedure.md) | **complete** | Sprint 10 polish (X-rollback / DRF-872) | Lead |
| [`security-incident.md`](security-incident.md) | **complete** | Sprint 10 / O6 (DRF-867) | Lead |
| [`chromadb-auth.md`](chromadb-auth.md) | draft | Sprint 7 (M4 / DRF-595) | Platform Lead |
| [`strict-scope-flip.md`](strict-scope-flip.md) | **complete** | Sprint 10 polish (F-dry / DRF-868) | Lead |
| [`shadow-mode-launch.md`](shadow-mode-launch.md) | draft | Sprint 8 (N4 / DRF-703) | Platform Lead |
| [`canary-ramp.md`](canary-ramp.md) | **complete** | Sprint 10 / X-criteria (DRF-871) | Lead |
| [`on-call.md`](on-call.md) | **complete** | Sprint 10 / O3 (DRF-864) | Lead |
| [`disaster-recovery.md`](disaster-recovery.md) | draft | Phase 1 / PI2 (DRF-852) | Lead |
| [`miniapp-acceptance.md`](miniapp-acceptance.md) | **complete** | Phase 5 (customer Mini App) | PI Track |
| [`m6-auto-draft-suppress-tuning.md`](m6-auto-draft-suppress-tuning.md) | draft | Pilot 2026-07-15 (issue #690) | W1 Delta |
| [`jwks-rotation.md`](jwks-rotation.md) | partial | Pre-pilot 2026-07-15 (issue #565 / NS2) | Security stream (S2) |

## Setup (one-time procedures)

These live under `docs/setup/` rather than `docs/runbooks/` — they're
**one-time bootstrap** procedures rather than **recurring response**
procedures.

| Doc | Status | Sprint | Purpose |
|---|---|---|---|
| [`../setup/branch-protection.md`](../setup/branch-protection.md) | **complete** | Sprint 10 / DRF-891 | Apply main/dev branch protection rules via `gh api` |
| [`../setup/dev-environment.md`](../setup/dev-environment.md) | **complete** | Sprint 10 / DRF-891 | Create `@ai_bot_platform_dev` bot + dev instance (8 steps) |

`status` values:

- **skeleton** — sections exist, content is TBD markers tied to a future sprint
- **partial** — at least one section is real, the rest are still TBD
- **draft** — all sections written but not exercised in a real incident
- **complete** — survived at least one real exercise (game day or live incident)

## Non-engineer operational docs

Not every operational document is a runbook for on-call. These live in
[`docs/operations/`](../operations/) and are written for the people running the
business, not the platform:

| Doc | Audience | Covers |
|---|---|---|
| [`pilot-bot-operator-guide.md`](../operations/pilot-bot-operator-guide.md) | Salon administrator on the pilot | What the bot understands, which buttons map to which phrases, and — the part that bites — that a handoff mutes the bot in that conversation and it does not come back on its own (DRF-963) |

## When you fill a skeleton

1. Drop the TBD blocks for sections you're filling.
2. Bump status in this README.
3. Add a "Last exercised" line near the top (date + link to incident / game-day notes).
4. Keep a *Changelog* at the bottom — every meaningful edit a one-liner.

## When you run a runbook

1. Open it on one screen, terminal on the other. Follow it literally.
2. Note any deviation as you go (sticky note / Slack DM to yourself).
3. After the dust settles, write a one-line *Post-mortem* in the runbook's Changelog plus a full incident report if anything broke.
