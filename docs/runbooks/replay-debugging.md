# Runbook: Replay debugging

> Status: **skeleton**
> Last exercised: _never_
> Target completion sprint: **Sprint 5** — when `apps/replay/` ships recorder + redactor + fixture types (golden / adversarial / voice).
> Owner: Dev1

## Purpose

When a bot reply is wrong (factual error, wrong skill chosen, voice off-brand, tool call malformed), reproduce the exact request → response trace and identify which step in the orchestrator broke. Without this, every bug becomes a guessing game.

## Trigger / when to run

- A user / QA reports a wrong answer with `trace_id` from the message metadata.
- A scheduled replay diff shows a regression vs the production baseline.
- Game day exercise rehearsing the procedure.

## Prerequisites

- _TBD Sprint 5._ Will include: replay UI URL or CLI invocation, S3 bucket for fixtures, access to the per-tenant chromadb collection at the time of the trace.

## Step-by-step procedure

1. _TBD Sprint 5._ Will cover: locate the trace by `trace_id` in `apps.replay`; rehydrate inputs (user message, tenant_id, conversation history snapshot, retrieved KB chunks, prompt revision, model + parameters); replay against the same revision and assert the historical response is reproducible.
2. _TBD Sprint 5._ Will cover: re-run with the *current* code/prompts and diff the response — identify which step (skill resolution / RAG / LLM call / tool dispatch / voice rewrite) is the regression source.
3. _TBD Sprint 5._ Will cover: capture the failing fixture into `apps.replay/fixtures/adversarial/` with a clear name and the bug report number, so future CI catches the regression.

## Verification

_TBD Sprint 5._ Will include: the new fixture is reproducible across two consecutive runs; CI replay job picks up the fixture and asserts the bug-fix patch resolves it.

## Escalation contacts

_TBD Sprint 5._

## Post-mortem template

Standard 7-bullet template — see [`_template.md`](_template.md).

## Changelog

- 2026-05-10 — Dev1 — skeleton committed (DRF-414)
