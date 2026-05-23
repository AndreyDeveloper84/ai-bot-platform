<!--
PR template for ai-bot-platform — Phase 0 onwards.

Sections marked "(consumer PRs only)" apply to event-ingest consumer
handlers (#442-#446 + future). Other PRs may delete those sections.
-->

## Summary

<!-- 1-3 sentences — what changes, why now. -->

## Acceptance

<!-- Mirror the GH issue's acceptance checkboxes here so reviewers can
verify item-by-item. -->

- [ ]
- [ ]

## Test plan

<!-- What was run, what was observed. Not a generic template — paste
actual command output for the touched scope. -->

```
$
```

## Out of scope

<!-- What this PR intentionally does NOT do, with a pointer to the
follow-up that will. -->

---

## Security checklist (consumer PRs only — #442-#446 + future event-ingest handlers)

Required for any PR that registers a handler via
`apps.eventbus.ingest_dispatcher.register(...)`. Delete this section
otherwise.

- [ ] **Handler calls `apps.eventbus.ingest_tenancy.assert_envelope_tenant_authorized(envelope)` BEFORE any side-effect.** Per PR #507 adversarial-pass A3 / ADR-0009 §Hard rule #6: HMAC verifies signature only, NOT tenant authority. A compromised Ayla worker or debug script could mint an HMAC-valid envelope with `tenant_id=<victim_tenant>`. The canonical helper is the one place the lint test (`tests/contracts/test_consumer_tenant_verification_mandate.py`) scans for.
- [ ] **Handler is idempotent on `event_id`** per `event-contract.md` §5.1. Replay-3×-runs-once test included.
- [ ] **No free-text PII in handler logs** per `event-contract.md` §7. Log IDs only; fetch display fields on-demand from Ayla REST.
- [ ] **Side-effects happen inside the dispatcher's `transaction.atomic()` block** — never in `transaction.on_commit` callbacks for any state that must roll back on handler exception. (External-API calls and bus emissions are the exception — use `on_commit`.)

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
