# Runbook: Incident response

> Status: **skeleton**
> Last exercised: _never_
> Target completion sprint: **Sprint 9** — when on-call rotation is established and shadow + canary cutover begins.
> Owner: Lead

## Purpose

Define how the team responds to a production incident — from "monitor fired" to "post-mortem written", inclusive — so that on-call doesn't reinvent the process at 02:00.

## Trigger / when to run

- Any P0 / P1 alert from the monitoring stack (latency SLO breach, error budget drain, complete bot down, integration failure).
- Manual trigger by team member observing a problem (user complaint, customer escalation).

## Prerequisites

- _TBD Sprint 9._ Will include: PagerDuty (or equivalent) account, on-call rotation, status page URL, war-room channel template, severity matrix.

## Step-by-step procedure

1. _TBD Sprint 9._ Will cover: acknowledge the alert; declare severity; create incident channel; appoint Incident Commander + Communications Lead + Subject-Matter Engineer.
2. _TBD Sprint 9._ Will cover: stabilise (apply mitigation per the matching specialised runbook — `rollback-procedure.md`, `replay-debugging.md`, etc.); communicate to stakeholders on a fixed cadence (every 30 min or per severity); avoid investigation-during-mitigation.
3. _TBD Sprint 9._ Will cover: declare resolved when SLOs return to green for N minutes; close the channel; schedule post-mortem within 5 business days.

## Verification

_TBD Sprint 9._ Will include: SLO indicators green for sustained period; no fresh symptoms; customer impact statement signed off by the IC.

## Escalation contacts

_TBD Sprint 9 (post-mortem matrix per service)._

## Post-mortem template

Standard 7-bullet template — see [`_template.md`](_template.md). Post-mortem must be blameless and produce trackable Linear action items.

## Changelog

- 2026-05-10 — Lead — skeleton committed (DRF-414)
