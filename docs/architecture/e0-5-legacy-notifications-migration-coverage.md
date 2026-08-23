# E0.5 — `legacy_notifications/` Migration Coverage Audit

**Date:** 2026-05-31
**Auditor:** general-purpose agent (E0.5)
**Scope:** `legacy_notifications/**/*.py` (243 LOC, 2 files)
**Verdict:** FULL_COVERAGE (with one deliberate live shim that may stay or be cut over at zero risk)
**Pilot-blocking?** NO

---

## Method

1. Listed the directory — only `__init__.py` (168 LOC) and `max_bot.py` (75 LOC) plus `MIGRATION_NOTICE.md`. Read both files in full given the small size.
2. Read the `MIGRATION_NOTICE.md` freeze policy: this is the AS-IS snapshot of `mysite/notifications/` at commit `a52e4e6` from `formula_tela`; migration target is `apps/channels/`.
3. Grepped repo-wide for every public symbol (`send_notification_telegram`, `send_notification_email`, `send_certificate_email`, `get_notification_recipients`, `send_max_message`) and for module-level references (`legacy_notifications`, `from legacy_notifications`).
4. Confirmed `services_app`, `payments`, `website` (the legacy Django siblings that imported these functions) **do not exist in this repo** — they belong to the original `formula_tela` mysite codebase.
5. Read the candidate ports: `apps/notifications/{__init__,models,apps}.py`, `apps/channels/max/outbound.py`, `apps/orchestrator/llm/telegram_alert.py`, `apps/orchestrator/llm/breaker.py` (alert-call site).
6. Cross-checked against E0.2 (MAX handlers) coverage doc which already classified `max_bot.py` as FULL coverage delete-ready.
7. Checked the `pyproject.toml` (ruff `flake8-tidy-imports.banned-api`, TID251) / `.pre-commit-config.yaml` / `.secrets.baseline` posture for the legacy package. (An earlier draft listed a `.importlinter` file here — no such file exists in the repo; see the line 125 correction.)

---

## Files in scope

Total files: 2 (`.py`) + 1 README. Total LOC: 243.

### Per-file breakdown

| File | LOC | Purpose | Primary cluster |
|---|---|---|---|
| `legacy_notifications/__init__.py` | 168 | Three admin-side notification helpers: (a) `send_notification_telegram(text)` — admin Telegram alerts via `api.telegram.org/bot{token}/sendMessage` with optional `TELEGRAM_PROXY` / `OPENAI_PROXY`; (b) `send_notification_email(subject, message)` — admin email blast via `django.core.mail.send_mail`, recipients from `SiteSettings.notification_emails` or `ADMIN_NOTIFICATION_EMAIL` fallback (`get_notification_recipients()`); (c) `send_certificate_email(order, cert, pdf_bytes)` — buyer email with the certificate code + optional PDF attachment. The module docstring (`__init__.py:14-17`) explicitly states that all `payments.views.yookassa_webhook` + `payments.tasks.fulfill_*` callers were **deleted in PR #739 #427+#428** when YooKassa lifecycle moved to Ayla per ADR-0009. | **legacy-orphan** (mysite admin alerts, not nudges, not channel — see classification table) |
| `legacy_notifications/max_bot.py` | 75 | `send_max_message(*, chat_id, text, attachments=None, timeout=10)` — sync MAX REST push using `requests.post("https://botapi.max.ru/messages", headers={"Authorization": token}, params={"chat_id": chat_id}, json=payload)`. Serialises maxapi `Attachment` pydantic objects (`model_dump` / `dict` / raw dict) so admin Django actions can post inline keyboards. Returns `bool`, swallows exceptions (`max_bot.py:73-75`). | **MAX-channel-overlap** (E0.2) |

---

## Coverage table

| Legacy symbol | LOC | Current equivalent | Coverage | Evidence | Pilot risk if deleted |
|---|---|---|---|---|---|
| `__init__.py::send_notification_telegram` | ~37 (L32-68) | **NOT PORTED** — admin Telegram alerts on customer enquiries (`api_wizard_booking`, `api_bundle_request`, `api_certificate_request`) | UNUSED in this repo (callers don't exist) | `Grep send_notification_telegram` returns 0 hits across `apps/` (only the legacy file itself). The Telegram-alert path used today is `apps/orchestrator/llm/telegram_alert.py::send_breaker_alert` — **but that targets the MAX bot, not api.telegram.org**, and is for breaker state transitions only, not customer enquiries. | None — no live caller. Customer enquiry alerts belong to Ayla per ADR-0009 (booking lifecycle = Ayla SoR). |
| `__init__.py::get_notification_recipients` | ~20 (L71-90) | **NOT PORTED** — depends on `services_app.models.SiteSettings` which lives in mysite, not in this repo | UNUSED | `SiteSettings` not importable in this repo (`services_app` directory absent). Only callers in this repo are `send_notification_email` and `send_certificate_email` callers — both unreachable. | None |
| `__init__.py::send_certificate_email` | ~51 (L93-143) | **NOT PORTED** — buyer-facing certificate-code email after YooKassa fulfilment | UNUSED | Memory `certificate_payment_post_pilot`: certificate flow is DEFERRED post-pilot with `CERTIFICATE_PAYMENT_ENABLED=False`. Per memory `payment_creation_ownership`, bot-platform does NOT create payments; Ayla canonical only. The original caller chain `payments.tasks.fulfill_certificate → send_certificate_email` was removed in PR #739/#427/#428 (per legacy docstring L14-17). | None for pilot — feature DEFERRED. **Post-pilot:** when Ayla ships the certificate flow, the buyer email is Ayla's responsibility (transactional payment-side notification), not bot-platform's. |
| `__init__.py::send_notification_email` | ~23 (L146-168) | **NOT PORTED** — admin email blast on customer enquiries | UNUSED | Same as `send_notification_telegram`: no live caller, target is Ayla. | None |
| `max_bot.py::send_max_message` | 75 | `apps/channels/max/outbound.py::send_message` (300 LOC); same wire shape `POST https://botapi.max.ru/messages?chat_id=… Authorization: <raw token>`, body `{"text": …, "attachments": […]}` (`outbound.py:101-115`). Httpx instead of requests; raises `MaxAPIError` instead of returning `False`. | FULL — equivalent wire call | E0.2 §`legacy_notifications/max_bot.py` row already classifies as `FULL` (see `docs/architecture/e0-2-max-handlers-cluster-migration-coverage.md:83`). Independently verified: query param `chat_id`, raw `Authorization: token` (not `Bearer`), attachment list serialised to wire-format dicts — all preserved in `outbound.py`. | None — but **one live runtime caller remains** via importlib shim (see §Live shim below). |

### Live shim: `apps/orchestrator/llm/telegram_alert.py`

`send_breaker_alert` (LLM circuit-breaker state-transition admin alert, DRF-455 / Sprint 2 / E2) imports `legacy_notifications.max_bot::send_max_message` **at runtime via `importlib.import_module`** (`telegram_alert.py:77-91`) rather than statically importing. The docstring at L8-19 is explicit:

> The legacy `notifications/max_bot.py::send_max_message` is the only working MAX REST sender in the repo today (running in prod since 2026-04). It uses `requests` (not httpx — pre-Sprint-1 stack) and takes int chat_id. We don't port the whole thing into apps/ — that's Sprint 7+ when we generalise alerting. Instead this module imports it lazily … If the legacy module is later drained, this shim is the only thing to update.

This is a **deliberate, documented dependency** — not an accidental remnant. The wire call is functionally identical to `apps/channels/max/outbound.py::send_message`; the only delta is `requests` vs `httpx` and a `bool` return vs raising `MaxAPIError`. Cutover would be ~5 lines in `telegram_alert.py` (change `importlib.import_module("legacy_notifications.max_bot")` → `from apps.channels.max.outbound import send_message` + adapt return-value handling) plus updating 6 mock targets in `apps/orchestrator/llm/tests/test_telegram_alert.py`.

The breaker docstring at `apps/orchestrator/llm/breaker.py:147-152` documents the alert path's I/O-after-unlock pattern with explicit reference to the importlib hop. The shim choice is intentional.

---

## Cross-phase classification

| Legacy file/symbol | Primary E0.X bucket | Why |
|---|---|---|
| `max_bot.py::send_max_message` | **E0.2 (MAX handlers)** | Pure MAX REST wire wrapper. Already audited and classified as FULL coverage in E0.2 (`e0-2-max-handlers-cluster-migration-coverage.md:83`). The one remaining live caller (`telegram_alert.py`) is a documented importlib shim. |
| `__init__.py::send_notification_telegram` | **E0.4 misc / out-of-scope orphan** | Targets `api.telegram.org` (Telegram, not MAX). bot-platform is MAX-only pilot per memory `max_only_pilot`. No nudge logic (no schedule), no AI logic — pure HTTP wrapper for a missing caller. |
| `__init__.py::send_notification_email` + `get_notification_recipients` | **E0.4 misc / out-of-scope orphan** | Admin email blast. Depends on `services_app.SiteSettings` model that doesn't exist in this repo. Caller chain was deleted with YooKassa migration to Ayla. |
| `__init__.py::send_certificate_email` | **Out-of-scope (Ayla domain)** | Per memory `payment_creation_ownership`: bot-platform does NOT create payments. Per memory `certificate_payment_post_pilot`: certificate feature DEFERRED post-pilot, `CERTIFICATE_PAYMENT_ENABLED=False`. Transactional buyer email belongs to Ayla. |

**No overlap with E0.3 (nudges).** None of the legacy_notifications functions decide WHEN to fire — they're all synchronously called by an existing endpoint or task. The nudge-scheduling stack (state machines, due-time evaluation, dedup) lives elsewhere.

---

## Behavior parity audits

### Template rendering (legacy vs current)
- **Legacy `send_certificate_email`** uses a Python f-string template inline (`__init__.py:113-125`) with hardcoded studio contact info `Студия «Формула тела» — 8 (8412) 39-34-33, Пенза, ул. Пушкина, 45`. Tenant-specific — does NOT scale to multi-tenant Ayla.
- **Current:** No equivalent buyer-email template in `apps/*`. Buyer-facing post-payment email is Ayla's responsibility per ADR-0009. **Not a bot-platform regression.**

### Dedup / send-history (legacy vs current)
- **Legacy:** No dedup. Each function fires a one-shot HTTP call. No send-history table.
- **Current:** Per E0.2, MAX inbound has `with_idempotency` keyed on callback_id/message_id (`apps/channels/max/handler.py:240-260`, TTL 86 400s). Outbound has no message-level dedup but the orchestrator handles single-fire per inbound event. **Not weaker than legacy.**

### Retry / dead-letter (legacy vs current)
- **Legacy:** No retry. Failure → log warning + return `False` (`__init__.py:62-68`, `max_bot.py:67-75`). Caller responsible.
- **Current:** `apps/workers/consumer.py` PEL retention + `apps/workers/reaper.py` XAUTOCLAIM → `<stream>:dlq` for inbound. Outbound (`apps/channels/max/outbound.py::send_message`) raises `MaxAPIError` instead of returning `False` — caller (`apps/channels/max/handler.py`) decides not-to-ACK so PEL retains for retry. **Stronger than legacy.**

### Opt-out respect (legacy vs current)
- **Legacy:** No opt-out check. Admin alerts always fire if env vars set.
- **Current:** `apps/notifications/models.py::MasterNotificationPrefs` introduces per-master toggles + quiet-hours + urgent-forced-on CheckConstraint (M7 master-mobile screen, ships from PR pending). Customer-facing notifications go through the orchestrator path which respects MAX subscription state. **Stronger than legacy** (legacy had no opt-out model).

### Admin / operator alerts (legacy vs current)
- **Legacy admin Telegram alert** (`send_notification_telegram`): hit `api.telegram.org` for customer-enquiry posts. Required `TELEGRAM_PROXY` because Telegram is RKN-blocked in RU. **No current bot-platform analogue** — customer-enquiry admin alerts are Ayla's responsibility.
- **Current operator alert path:** `apps/orchestrator/llm/telegram_alert.py::send_breaker_alert` fires to a MAX chat (`settings.ADMIN_MAX_CHAT_ID`), not Telegram, only on LLM circuit-breaker state transitions. Different scope (system-health, not customer-enquiry) but is the only live admin-alert path in bot-platform.
- **Memory `payment_failed_dm_threshold`** specifies threshold-gated DMs (N=3 consecutive failures) for `payment.failed` events. That DM path is **owned by W2 α-mode skill integration** — not in `legacy_notifications/` and not affected by deleting it. Verified by grepping `payment_failed` across `legacy_notifications/` → 0 hits.

---

## Gaps requiring action

**None pilot-blocking.** Items below are post-pilot hygiene only.

1. **`apps/orchestrator/llm/telegram_alert.py` importlib shim cutover**
   - What: Replace `importlib.import_module("legacy_notifications.max_bot")` + `send_max_message(chat_id=int, text=str)` with `from apps.channels.max.outbound import send_message; send_message(chat_id=str(chat_id), text=text)`. Update the 6 `patch("legacy_notifications.max_bot.send_max_message")` mocks in `apps/orchestrator/llm/tests/test_telegram_alert.py` to patch `apps.channels.max.outbound.send_message`.
   - Recommended action: **PORT_POST_PILOT** (DELETE_DELIBERATE pending).
   - Why: Shim is documented as "Sprint 7+ when we generalise alerting" per `telegram_alert.py:14`. Cutover is small (~5 LOC + 6 mock paths) and unblocks deletion of the entire legacy package. Founder constraint says we cannot delete legacy — this is preparation work, not deletion.
   - Effort: ~30 minutes.
   - Owner: any stream comfortable touching `apps/orchestrator/llm/`.

2. **No `__init__.py` callers** (Telegram admin alerts, certificate email)
   - What: Three functions in `legacy_notifications/__init__.py` have **zero live callers** in this repo. Their original mysite callers (api_wizard_booking, payments.tasks, services_app.admin) live in `formula_tela`, not here. Per ADR-0009, those responsibilities are Ayla's.
   - Recommended action: **INVESTIGATE_FURTHER** (do not delete per founder constraint, but document as orphan).
   - Why: Founder rule «нельзя удалять» applies. Post-pilot, when Ayla ships its own admin-alert + certificate-buyer email flows, this entire `__init__.py` becomes provably dead and can be retired through the standard legacy-drain process.
   - Effort: zero now; ~15 minutes post-pilot to verify Ayla coverage + add a deprecation banner in `MIGRATION_NOTICE.md`.

---

## Files safe to delete

Per founder constraint **«legacy код нельзя удалять, надо проверить все ли перенесено»**, this section records *delete-readiness* only — not a recommendation to actually delete.

| File | Delete-readiness | Confidence | Blocker (if any) |
|---|---|---|---|
| `legacy_notifications/__init__.py` | YES (zero live callers in apps/) | **HIGH** | None — but per founder rule, keep. Defer actual deletion until Ayla side-by-side proves it can serve the same admin-alert + certificate-email flows. |
| `legacy_notifications/max_bot.py` | YES once `telegram_alert.py` shim is cut over (~30 min work) | **HIGH** (after shim cutover) | One live importlib caller in `apps/orchestrator/llm/telegram_alert.py:80`. Functional equivalent already exists at `apps/channels/max/outbound.py::send_message`. |
| `legacy_notifications/MIGRATION_NOTICE.md` | Keep until both `.py` files are retired. | — | Documentation of the freeze policy. |

Posture today: the ruff `[tool.ruff.lint.flake8-tidy-imports.banned-api]` rule (TID251) in `pyproject.toml` already enforces that `apps/*` **must not statically import** `legacy_notifications` (the G1.2 ADR-0009 import-edge). The importlib hop in `telegram_alert.py` is the documented escape hatch; the ruff banned-api rule is intact.

> **Correction (S5, 2026-06-03):** an earlier revision cited a `.importlinter:21,42-48` file as the enforcement mechanism. **No `.importlinter*` file exists or has ever existed in this repo** — that was a *planned-as-done* error (roadmap item A11, which proposed import-linter, never shipped; see the S5 provenance trace on #968). Legacy_* import bans are enforced by ruff TID251 today. Broader G1–G10 ADR-0009 edges are moving to the `tools/lint/` AST-linter (Option B per orchestrator 2026-06-03), not import-linter.

---

## Investigations needed

1. **Verify Ayla side-by-side** — does Ayla djangoproject's `notifications/` (or equivalent) own (a) admin Telegram alerts on customer enquiries and (b) certificate-buyer email after YooKassa fulfilment? Confirm before post-pilot deletion. Cross-repo question; ask tech lead or Alpha stream.
2. **`apps/notifications/` future scope** — module docstring at `apps/notifications/__init__.py:1-8` says "consumer-side notification dispatchers (DM scheduler, morning brief / evening summary, urgent-channel gating) will read from this app's services in subsequent PRs." When those ship, confirm they use `apps/channels/max/outbound.py::send_message` (not the legacy importlib path) — otherwise we'd grow new dependencies on the legacy package.

---

## Appendix: searches performed

- `Glob legacy_notifications/**/*.py` (ripgrep timeout, fell back to `ls`)
- `ls legacy_notifications/` → `__init__.py`, `max_bot.py`, `MIGRATION_NOTICE.md`, `__pycache__/`
- `wc -l legacy_notifications/*.py` → 168 + 75 = 243 total
- `Grep send_notification_telegram|send_notification_email|send_certificate_email|get_notification_recipients|send_max_message` (full repo)
- `Grep legacy_notifications` (full repo, excluding `.claude/`)
- `Grep "from legacy_notifications|import legacy_notifications"` → only one match in `apps/`: the documented importlib shim in `telegram_alert.py`
- `Grep certificate|SiteSettings|notification_emails -i` (apps/) → 15 files, all unrelated to admin email recipients (booking certificate skills, kb seeds, adversarial fixtures)
- `ls services_app payments website` (repo root) → none exist (confirms legacy callers are out-of-repo)
- Read full content of: `legacy_notifications/__init__.py`, `legacy_notifications/max_bot.py`, `legacy_notifications/MIGRATION_NOTICE.md`, `apps/orchestrator/llm/telegram_alert.py`, `apps/channels/max/outbound.py`, `apps/notifications/__init__.py`, `apps/notifications/models.py`, `apps/notifications/apps.py`, plus relevant slices of `apps/orchestrator/llm/breaker.py` and `docs/architecture/e0-2-max-handlers-cluster-migration-coverage.md`.

---

## ADR-0009 boundary check

- **Transactional state ownership:** `legacy_notifications/__init__.py::send_certificate_email` reads `order.client_email`, `cert.code`, `cert.nominal`, `cert.bundle`, `cert.valid_until` — all transactional certificate state. Per memory `certificate_payment_post_pilot` + ADR-0009 §5 amendment per memory `payment_creation_ownership`, certificate creation + buyer notification belong to Ayla, not bot-platform. Legacy file is dormant; **no current code in this repo writes booking/payment/catalog directly** via this path.
- **`max_bot.py::send_max_message` + `apps/channels/max/outbound.py::send_message`:** Pure transport. No transactional state. Compliant.
- **`apps/orchestrator/llm/telegram_alert.py`:** Pure operator-alert path. No transactional state. Compliant.
- **`apps/notifications/MasterNotificationPrefs`:** Per-master preferences for bot-platform-owned DM channel (AI conversations + master DMs). Within bot-platform's stated ADR-0009 ownership (AI/observability/multi-tenant runtime). Compliant.

No ADR-0009 violations found.
