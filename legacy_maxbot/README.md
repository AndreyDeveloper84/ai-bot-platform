# mysite/maxbot/ — ⚠ FROZEN as migration source-of-truth

> **🛑 STOP. Read [.FROZEN](./.FROZEN) before opening any PR that touches this directory.**

---

## Status

This directory is the **production MAX bot** для formula_tela (Phase 1 + Phase 2.3 + Phase 2.4 + Phase 3 nutrition tracker), but it is **frozen** as of **2026-05-09**.

All new development happens in [`github.com/AndreyDeveloper84/ai-bot-platform`](https://github.com/AndreyDeveloper84/ai-bot-platform).

This directory will be:
- **Sprint 0–7 (weeks 1-16):** Source-of-truth for migration. Critical security fixes only (cherry-picked to `ai-bot-platform/legacy_maxbot/`).
- **Sprint 8 (weeks 17-18):** Still primary. Platform runs in shadow mode (no outbound).
- **Sprint 9 (weeks 19-20):** Canary cutover 10% → 50% to platform.
- **Sprint 10 (weeks 21-22):** 100% cutover. This directory becomes archive.
- **Post Phase 0:** Deleted in cleanup PR (separately from cutover).

## Why

Multi-tenant AI-bot platform requires a clean foundation. We can't add 16 foundation blocks (tenancy, idempotency, audit, replay, voice, segmentation, prompt registry, experiments, etc.) into existing `mysite/maxbot/` without breaking 8 architectural cycles that took an audit cycle (ln-644) to discover and fix. Hence: clean repo + carry-over + drain.

See [PHASE0_DESIGN.md](../../docs/arch/PHASE0_DESIGN.md) v2 for the full plan.

## How to make a security fix

If you have an emergency change you absolutely must make here:

1. Read [.FROZEN](./.FROZEN) first.
2. Open Linear ticket explaining why.
3. PR title: `[FROZEN-EXEMPT] <description>`.
4. PR body: incident link, cherry-pick plan, risk assessment.
5. Tech lead approval required (enforced by CODEOWNERS).

## What's here (as of freeze date)

- **9 routers:** `start`, `services`, `booking`, `contacts`, `faq`, `reminders`, `ai_callbacks`, `ai_assistant`, `fallback`
- **AI Concierge** (Phase 2.3): `ai_concierge.py`, `ai_tools.py` (6 OpenAI tools), `ai_tool_handlers.py`, `ai_action_service.py`, `ai_context.py`, `ai_prompts.py`, `ai_ui.py`, `ai_yclients.py`
- **Phase 2.4 consultative AI:** voice_examples, returning customer recognition, master criteria filtering, health screening, post-visit follow-up
- **Phase 3 nutrition tracker:** `handlers/nutrition_anketa.py`, `handlers/food_scanner.py`, `handlers/water.py`, `handlers/daily_report.py`, `services/nutrition_client.py` (Ayla integration)
- **N2 reminder system:** `reminders_factory.py`, `tasks.py` (T-24h + T-2h escalation)
- **MCP integration:** `mcp_client.py` (chromadb embeddings via `services/formulatela_mcp/`)
- **YClients webhook:** `yclients_webhook.py` (admin-side bookings sync)

## Getting started (read-only)

```bash
cd mysite/maxbot/
# Read code. Do not edit.
# For new features: see ai-bot-platform/ instead.
```

## Useful links

- Migration plan: [`mysite/docs/arch/PHASE0_DESIGN.md`](../../docs/arch/PHASE0_DESIGN.md)
- Linear project: [ai-bot-platform Phase 0](https://linear.app/drfproject/project/ai-bot-platform-phase-0-87eeee7605dd)
- Source spec: [`mysite/docs/arch/`](../../docs/arch/) (5 PDFs + 2 deep-research + compass skill catalog)
- Target repo: [AndreyDeveloper84/ai-bot-platform](https://github.com/AndreyDeveloper84/ai-bot-platform)
