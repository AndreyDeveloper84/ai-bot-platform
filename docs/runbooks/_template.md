# Runbook: <Title>

> Status: **skeleton | partial | draft | complete**
> Last exercised: _<date or "never">_
> Target completion sprint: _<sprint>_
> Owner: _<role>_

## Purpose

What this runbook accomplishes in one or two sentences. If you can't say it cleanly, the runbook isn't ready.

## Trigger / when to run

- Specific symptom #1
- Specific alert / monitor URL
- Manual trigger (planned activity, e.g. canary rollout)

## Prerequisites

- Access required (env, tools, credentials)
- Pre-checks before starting (state must look like X)
- Communication: who to ping in #ops *before* you start

## Step-by-step procedure

1. _Step 1 — copy-pasteable command, expected output_
2. _Step 2 — verification before proceeding_
3. …

Each step: command + expected output + decision branch (if exit ≠ 0 → goto X).

## Verification

How to confirm the procedure worked. Specific monitors, log queries, or a probe request that should now return 200. Time-to-stable target (e.g. all 5 SLO indicators green within 10 minutes).

## Escalation contacts

| Severity | Who | How to reach |
|---|---|---|
| P0 | … | … |
| P1 | … | … |
| Vendor | … | … |

## Post-mortem template

Used after every non-trivial run.

- **What happened.**
- **What was the trigger.**
- **What did we expect — what actually happened.**
- **How long did it take to detect / mitigate / resolve.**
- **What we learned.**
- **Action items** (owner + deadline).

## Changelog

- _YYYY-MM-DD_ — _author_ — _what changed_
