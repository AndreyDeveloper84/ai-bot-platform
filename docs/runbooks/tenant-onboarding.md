# Runbook: Tenant onboarding

> Status: **skeleton**
> Last exercised: _never_
> Target completion sprint: **Sprint 9** — when the first additional tenant beyond `formula-tela` is onboarded.
> Owner: Lead

## Purpose

Bring a new tenant from "we have a contract" to "their bot is live in MAX/Telegram/Web" without losing data, leaking another tenant's data, or breaking production for existing tenants.

## Trigger / when to run

- Sales has a signed contract + technical onboarding kickoff date.
- Tenant has confirmed channel credentials (MAX bot token, Telegram bot, web widget) and YClients access (if applicable).

## Prerequisites

- _TBD Sprint 9._ Will include: admin console URL, encryption key access, MinIO bucket pattern, monitoring template, Linear project template for tenant-specific issues.

## Step-by-step procedure

1. _TBD Sprint 9._ Will cover: create `Tenant` row + slug + display name; provision per-tenant chromadb collections; load FAQ knowledge base; configure brand voice; set channel webhook URLs; first end-to-end smoke (send "/start" through MAX webhook → assert response).
2. _TBD Sprint 9._ Will cover: enable monitoring + alerts scoped to the tenant; add to incident-response on-call list; communication to tenant on go-live + escalation channels.

## Verification

_TBD Sprint 9._ Will include: replay a canonical fixture against the new tenant and assert outputs match expected schema; verify no `apps.audit` cross-tenant warnings during smoke.

## Escalation contacts

_TBD Sprint 9 (after on-call rotation is established)._

## Post-mortem template

Standard 7-bullet template — see [`_template.md`](_template.md).

## Changelog

- 2026-05-10 — Lead — skeleton committed (DRF-414)
