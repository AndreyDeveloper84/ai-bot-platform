# Runbook: On-call rotation + alerting

> Status: **complete**
> Owner: Lead
> Sprint 10 / O3 (DRF-864) — depends on O2 (alerting library, DRF-863)
> Last exercised: 2026-05-15 — smoke command verified locally

## Purpose

Define how operational pages reach the on-call human (and what an AI
backup can / cannot do). When something fires in prod at 03:00, this
runbook tells you:

1. Where the page lands and on what device.
2. How fast you must acknowledge.
3. What an AI backup is allowed to touch.
4. How to verify the pipeline is alive (smoke test).

This is the contract for Phase 0; it gets a 2-person rotation in
Phase 1 when a second human joins.

---

## Decision: Telegram-only, no PagerDuty

Sprint 10 / O1 (DRF-862) was originally specified as "set up PagerDuty
account + integration key." On 2026-05-15 the decision was made to
**skip PagerDuty entirely** for Phase 0 + Phase 1 and route all alerts
through a dedicated **Telegram channel** instead.

### Why PagerDuty was skipped

* PagerDuty's signup requires a credit-card-backed billing account
  even on free tier — operator does not have one available.
* International payment from RU is friction we don't need at Phase 0
  scale (1 tenant, 1 on-call human).
* The unique PD features (autocall, structured escalation timer,
  ack-or-page-next-person) only earn their place when there's a
  second human to escalate **to**. Phase 0 has only Lead.

### What we lose vs PD

| PD feature | Telegram-only substitute |
|---|---|
| Autocall on critical | Telegram channel with "always-notify, custom alarm sound" overrides DND on Android/iOS. Effect is ~95% of autocall when configured right. |
| Structured dedup with custom rules | Redis-backed dedup in `apps.observability.alerting._is_duplicate`. Critical bypasses dedup. |
| Ack/escalate timer | None. Phase 0 has 1 on-call. Phase 1 adds 2nd human + this runbook gets updated. |
| Incident timeline UI | Telegram channel scroll + audit log query (`AuditLog.all_tenants.filter(action__startswith="observability.alert.")`). |
| Mobile app branded for alerts | Telegram channel with custom sound + always-notify; or use ntfy.sh if you want a dedicated app (out of scope Phase 0). |

### When to revisit

Re-evaluate PD (or self-hosted alternative) when **any** of:

* On-call grows to 2+ humans (Phase 1) — escalation matters.
* Pages-per-week exceeds ~5 (operator burns out reading every Telegram
  ping with adrenaline).
* Operator misses a critical page because phone was on silent — that's
  the "we lost something" signal; PD's autocall earns its keep then.

---

## Page receipt path

### Where alerts land

1. **Telegram channel** `🚨 ai-bot-platform alerts` — primary, all
   severities. Operator's phone with channel set to "always notify,
   bypass DND, custom sound" for critical.
2. **Sentry** — automatic for `severity=critical|error`. Goes to the
   project's Sentry inbox with `alert.severity=critical|error|warning`
   tag. Operator subscribes via Sentry email/Slack integration.
3. **AuditLog** — every page (sent OR deduped OR rejected) writes a
   row: `observability.alert.paged` / `observability.alert.deduped`.
   This is the durable archive for post-mortem.

### Severity matrix

| Severity | Triggers (examples) | Telegram | Sentry | DND bypass |
|---|---|---|---|---|
| **critical** | F2 scope violations, X-criteria breach >1h, P0 Sentry events | ✅ 🚨🚨🚨 prefix | ✅ fatal | ✅ |
| **error** | Skill dispatch crash, catalog sync 3+ misses, Sentry errors | ✅ 🔴 prefix | ✅ error | ❌ |
| **warning** | Capacity headroom, slow catalogs, soft-budget breach | ✅ 🟡 prefix, `disable_notification=True` | — | ❌ |

The 🚨🚨🚨 prefix is intentional — Telegram phone-side notification
rules can be set to trigger an alarm sound only when message contains
that exact emoji sequence. Without PD's autocall, this is how
critical events override silent mode.

### Setup checklist (one-time per operator)

1. **Create channel** (private): `🚨 ai-bot-platform alerts`. Add the
   platform bot (`TELEGRAM_BOT_TOKEN` holder) as admin.
2. **Get chat_id**: send any message via the bot, then
   `curl "https://api.telegram.org/bot<TOKEN>/getUpdates"` — channel
   chat_id is the negative integer in `chat.id`.
3. **Set env vars** in `/etc/ai-bot-platform/.env`:
   ```
   TELEGRAM_BOT_TOKEN=<existing>
   ALERTS_TELEGRAM_CHAT_ID=-1001234567890   # YOUR channel id
   TELEGRAM_PROXY=http://user:pass@host:port   # MUST set in RU prod  # pragma: allowlist secret
   ```
4. **Phone setup**:
   * Open channel in Telegram → tap channel name → Notifications.
   * "Mute" → **OFF**.
   * Tap "Sound" → pick a non-default loud sound (or upload a custom
     alarm — Telegram allows .mp3/.ogg uploads via Saved Messages →
     forward → set as notification sound).
   * Android: long-press → Notification settings → "Override Do Not
     Disturb" → on. iOS: Notifications → Allow Critical Alerts (if
     channel supports it; falls back to standard banner if not).
5. **Smoke test** (see § Smoke test below) — required before any
   operator goes on-call.

---

## Acknowledge SLA

| Severity | Ack target | Action target |
|---|---|---|
| critical | 15 min | rollback / mitigation within 1h |
| error | 30 min | investigate during business hours |
| warning | no SLA | review at next standup |

"Ack" here is informal — Telegram has no ack button. Convention:
operator replies to the Telegram message with **`ack <reason>`** so
the channel scroll doubles as an incident timeline. Phase 1 with a
2nd human, this becomes a real ack-or-escalate mechanism.

### What if I miss a page?

Telegram doesn't tell anyone you didn't read the message. Phase 0
mitigations:

1. The audit log + Sentry inbox both still record the event. Morning
   triage catches missed overnight pages.
2. If `severity=critical` AND F2 scope-violation: the worker is still
   running in **strict mode** so the violation already raised — bot
   responses for the affected request crashed back to handoff. User
   impact is contained; you fix when you wake.
3. Investigate root cause: was DND wrong? was phone silent? was the
   Telegram bot kicked from the channel? Audit log `paged=True` +
   missed-on-phone = client-side problem; `paged=False` =
   server-side problem (token/proxy/channel).

---

## Escalation

Phase 0: **no escalation path exists** beyond the Lead. This is
acknowledged risk, not a bug. Mitigations:

* Lead's phone always has the alert channel set to bypass DND.
* If Lead is unreachable >30 min for critical events, the system
  degrades gracefully — F2 monitor keeps writing audit rows; rollback
  authority is documented in `canary-ramp.md` and could be exercised
  manually by anyone with SSH + the rollback procedure.
* Lead announces planned unavailability in advance via the Telegram
  admin chat (separate from the alert channel).

Phase 1 (when 2nd human is added): this section gets a real
escalation timer + handoff procedure. Tracked in DRF-864 comments;
runbook bumps to version 2.

---

## AI backup role

When a Claude/Codex agent is on-call (autonomous-loop scenarios), its
boundary is **read-only diagnostics**:

| ✅ AI backup CAN | ❌ AI backup CANNOT |
|---|---|
| Read audit logs, Sentry events, Telegram channel | Run prod migrations |
| Query DB read replicas for diagnostic data | Restart workers / web pods |
| Page the Lead with summary + suggested action | Edit `.env` on prod |
| Write incident-response notes / draft PRs | Rollback the canary (nginx edit) |
| File follow-up Linear tickets | Send messages to customers |
| Run `smoke_alert --severity=warning` to test paths | Send `--severity=critical` (would page real on-call) |
| Investigate via `python manage.py` read-only commands | Run anything writing to prod state |

The boundary exists because destructive prod actions need human
judgment + accountability. An AI executing `git push origin main`
with `--force` at 03:00 is worse than a missed page; a human reading
the AI's diagnostic write-up and then deciding is the design.

This boundary is **also** documented in CLAUDE.md ("Executing actions
with care" + risky-action confirmation). The runbook is the
domain-specific instance.

---

## Smoke test

Required before:

* Promoting any change to `apps/observability/alerting.py`
* Rotating `TELEGRAM_BOT_TOKEN`
* Changing `ALERTS_TELEGRAM_CHAT_ID`
* Onboarding a new on-call operator

### Command

```bash
# Default (warning, no DND bypass — won't wake anyone)
python manage.py smoke_alert

# Test critical path (this WILL wake the on-call if they're set up right)
python manage.py smoke_alert --severity=critical --title="O3 smoke test"

# Dry-run (just print what would be sent)
python manage.py smoke_alert --severity=error --dry-run
```

### Expected results within 60 seconds

| Where to look | What you should see |
|---|---|
| Telegram alert channel | New message with severity prefix + bold title + body |
| Sentry project inbox | New event with `alert.severity` tag (for error/critical) |
| `AuditLog` | One row `observability.alert.paged` with `telegram_sent: True` |
| Phone notification | Sound + banner for critical; silent for warning |

### Failure modes

| Symptom | Likely cause | Fix |
|---|---|---|
| Command exits 1 with "no sink delivered" | Token or chat_id unset | Check `.env`; restart worker |
| Telegram silent, Sentry green | Bot not in channel OR wrong chat_id | Re-invite bot as channel admin; re-grab chat_id |
| Both silent, command exits 0 | DSN/token "look set" but invalid | Check `audit_log` for `telegram_sent: False` + worker logs |
| Telegram delayed > 60s | `TELEGRAM_PROXY` slow/broken | Test proxy health: `curl --proxy $TELEGRAM_PROXY https://api.telegram.org` |

---

## Related runbooks

* [`incident-response.md`](incident-response.md) — what to do after a critical page arrives
* [`canary-ramp.md`](canary-ramp.md) — defines which X-track events page on critical
* [`strict-scope-flip.md`](strict-scope-flip.md) — F2 monitor that pages on tenant_scope_violation
* [`security-incident.md`](security-incident.md) — security-class pages (Sev1)
* [`rollback-procedure.md`](rollback-procedure.md) — what an on-call does after acking a critical page

---

## Changelog

* 2026-05-15 — Lead — initial complete version (Sprint 10 / O3 / DRF-864).
  Telegram-only routing decision documented; PD skipped (DRF-862 → Won't Do).
  Smoke command shipped at `apps/observability/management/commands/smoke_alert.py`.
