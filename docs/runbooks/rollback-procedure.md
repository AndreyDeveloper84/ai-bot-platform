# Runbook: Rollback procedure

> Status: **skeleton**
> Last exercised: _never_
> Target completion sprint: **Sprint 8** — when shadow mode begins and we have a baseline to roll back *to*.
> Owner: Lead

## Purpose

Revert production to a known-good state when a release introduces a regression that monitoring or canary diff catches. The default move during Sprint 8–10 is "roll back to `mysite/maxbot/`" because that stack remains the parallel production reference until 100% cutover.

## Trigger / when to run

- Canary diff alert (Sprint 9): platform response disagrees with production beyond the noise threshold.
- Hard alert: complete bot down for >2 minutes.
- Manual: pre-emptive revert before customer-impact reports start arriving.

## Prerequisites

- _TBD Sprint 8._ Will include: deploy access, traffic-routing controls (nginx config + restart commands or cloud equivalent), rollback decision tree, last-known-good commit SHA + image tag.

## Step-by-step procedure

1. _TBD Sprint 8._ Will cover: confirm the symptom matches a known regression class; declare incident if not yet declared.
2. _TBD Sprint 8._ Will cover: route traffic 100% back to `mysite/maxbot/` via the channel webhook configuration; verify production is healthy.
3. _TBD Sprint 8._ Will cover: revert the platform image to the previous tag; redeploy; smoke against shadow traffic; only then re-enable platform traffic.

## Verification

_TBD Sprint 8._ Will include: end-to-end smoke from each channel; 5-minute SLO check; replay diff between platform and `mysite/maxbot/` returns to baseline.

## Escalation contacts

_TBD Sprint 8._

## Post-mortem template

Standard 7-bullet template — see [`_template.md`](_template.md). Specifically capture: how long was the regression in production; how was it detected; could canary diff have caught it earlier.

## Changelog

- 2026-05-10 — Lead — skeleton committed (DRF-414)
