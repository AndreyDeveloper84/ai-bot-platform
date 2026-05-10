# Runbook: Security incident

> Status: **partial** — reporting flow + initial triage filled in Sprint 0; full IR plan in Sprint 9.
> Last exercised: _never_
> Target completion sprint: **Sprint 9** — full IR plan with forensics + customer comms.
> Owner: Lead

## Purpose

Respond to a credible security incident — leaked credential, data exfiltration, prompt-injection exploit reaching outbound, supply-chain compromise — without making it worse and without leaving customers in the dark.

## Trigger / when to run

- A credential / token / API key is suspected to have leaked (committed by mistake, screenshot, exposed in logs, public Slack channel).
- A user reports the bot has revealed information from another tenant.
- A dependency advisory (CVE) hits a package the platform uses.
- An unusual traffic spike from a single IP / user-agent / tenant.
- Anomalous access in audit logs (`apps.audit`).

## Prerequisites

- Access to the secret manager + ability to rotate keys.
- GitHub repo admin (to revoke deploy tokens / rotate workflow secrets).
- Communication channels: Telegram alerts via `notifications.send_notification_telegram`, email to legal counsel (deferred Sprint 9).

## Step-by-step procedure

1. **Acknowledge.** Within 15 minutes of credible signal: open incident in the war-room channel, assign Incident Commander.
2. **Contain.** Rotate the affected credential immediately. Revoke the leaked token in the issuer (GitHub, OpenAI, MAX, YClients, AWS-equivalent). If the leak path is a public commit, also force-push a removal *and* start a key rotation — assume the secret is already harvested.
3. **Assess scope.** What was the credential's blast radius? Could it modify data? Could it read other tenants? Was anything created/exfiltrated using it? `apps.audit` query for any actions in the credential's window.
4. **Notify.** Tenants impacted by data exposure must be told within the contractual window (Sprint 9 will define exact timeline). Use the templated outbound message (TBD Sprint 9).
5. **Investigate (after containment).** Reproduce the leak path, capture forensic evidence, write the post-mortem.

## Verification

- New credentials live; old credentials reject all calls (smoke against the issuer's API).
- Audit logs show no further suspicious access in the credential's window.
- All instances of the leaked credential are scrubbed from public surfaces.
- Customer comms acknowledged.

## Escalation contacts

| Severity | Who | How to reach |
|---|---|---|
| Token leak (limited blast radius) | Lead | Telegram alert, then call |
| Data exposure across tenants | Lead + legal | Telegram + phone |
| Supply-chain compromise | Lead | Telegram + email vendors |

_Full matrix lands in Sprint 9 with on-call rotation._

## Post-mortem template

Standard 7-bullet template — see [`_template.md`](_template.md). Plus mandatory: timeline of detection → containment → notification → investigation; root cause class (human error / supply-chain / weak control / misconfiguration); follow-up Linear actions with deadlines.

## Changelog

- 2026-05-10 — Lead — partial skeleton committed (reporting + containment filled, full IR Sprint 9) (DRF-414)
