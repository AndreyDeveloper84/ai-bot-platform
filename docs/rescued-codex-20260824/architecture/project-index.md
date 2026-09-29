# Project Architecture Index

Snapshot date: 2026-05-28.

Branch/base: `codex` at `730eaaf`, tracking `origin/dev`.

This document is a navigation index for architecture review. It describes the
current code shape, not the desired target state.

## Repository Shape

The project is a Django 5.2 / DRF backend for a multi-tenant AI bot platform.
It includes bot channels, customer/master/admin mini-app APIs, AI skills,
booking and scheduling support, catalog mirrors, event ingestion, observability,
and legacy source snapshots used during migration.

Top-level areas:

| Path | Purpose |
| --- | --- |
| `config/` | Django project package: settings, URL root, WSGI/ASGI, Celery app. |
| `apps/` | Main Django application code, split by platform/domain capability. |
| `tests/` | Cross-cutting smoke, contract, integration, and guard tests. |
| `docs/` | ADRs, architecture, product/design specs, runbooks, QA, setup docs. |
| `infra/` | Deployment and operational infrastructure templates/scripts. |
| `scripts/` | Operator/developer utility scripts. |
| `tools/` | Repository tooling, including custom lint guards. |
| `legacy_maxbot/` | Read-only migration source from the old MAX bot. |
| `legacy_formulatela_mcp/` | Read-only migration source for legacy MCP/RAG code. |
| `legacy_notifications/` | Read-only migration source for notification code. |

Dependency/runtime anchors:

| File | Notes |
| --- | --- |
| `pyproject.toml` | Python 3.12, Django/DRF, Celery/Redis, ChromaDB, OpenAI, Anthropic, Sentry, OTel, pytest, ruff, mypy. |
| `uv.lock` | Deterministic dependency lock. |
| `docker-compose.yml` | Local stack: web, PostgreSQL 16, Redis 7, ChromaDB, MinIO. |
| `.github/workflows/ci.yml` | CI runs `uv sync --extra dev --extra ai-core --frozen`, ruff, format check, red-zone guard, `manage.py check`, smoke tests, mypy. |

## Architecture Layers

The original `docs/architecture.md` defines an L0-L4 target graph. The current
code has grown additional API, event, and product-surface modules. For review,
use this practical layering:

### L0 Runtime And Configuration

Owns process wiring, environment settings, root routes, Celery boot, and deploy
runtime.

Main code:

- `config/settings/base.py`
- `config/settings/local.py`
- `config/settings/staging.py`
- `config/settings/production.py`
- `config/urls.py`
- `config/celery.py`
- `Dockerfile`
- `docker-compose.yml`
- `infra/`

Review focus:

- environment defaults and production fail-fast behavior
- root URL ownership
- Celery/beat schedule safety
- database/cache/storage assumptions

### L1 Core Platform Foundation

Owns tenant context, identity, consent, audit, events, and shared control-plane
primitives used by higher layers.

Apps:

- `tenancy`
- `identity`
- `consent`
- `audit`
- `events`
- `eventbus`
- `tools`
- `observability`

Key models:

- `Tenant`, `TenantStaff`
- `BotUser`, `UserPreferences`, `ClientProfile`, `UserPersonalContext`,
  `MemoryEntry`, `RedZoneAccessLog`
- `ConsentRecord`
- `AuditLog`
- `Event`
- `DomainEvent`, `IngestDedupe`, `IngestDLQ`, `HandlerFailureTracker`,
  `PaymentTerminalDedupe`, `ReviewProcessedDedupe`
- `IdempotencyKey`
- `ShadowDeltaSnapshot`, `AIRequestMetric`, `AIDailyMetricSummary`

Review focus:

- tenant isolation and scoped managers
- sensitive memory/red-zone access
- audit trail consistency
- event contracts, idempotency, DLQ behavior
- observability and alerting boundaries

### L2 Domain Data And Domain Services

Owns durable business state and domain-level operations. These modules should be
usable without knowing about HTTP views or bot channels.

Apps:

- `conversations`
- `booking`
- `bookings`
- `catalog`
- `scheduling`
- `promotions`
- `kb`
- `notifications`
- `loyalty`
- `internal_chat`
- `handoff`
- `experiments`
- `promptreg`
- `persona`
- `replay`
- `orders` retired shell

Key models:

- `Conversation`, `Message`, `AiDraft`
- `BookingRequest`, `BookingReminder`, `PendingBookingAction`,
  `RemoteBookingProxy`
- `CatalogService`, `CatalogMaster`, `MasterService`
- `WorkingHours`, `ScheduleException`, `TimeBlock`,
  `ScheduleChangeRequest`, `SlotConfig`
- `Promotion`
- `KbDocument`
- `MasterNotificationPrefs`
- `LoyaltyAccount`, `LoyaltyEvent`, `LoyaltyReferral`
- `MasterAdminThread`, `MasterAdminMessage`,
  `MasterAdminMessageAttachment`
- `AdminTask`
- `Experiment`, `UserAssignment`, `Holdout`
- `PromptVersion`, `ThresholdConfig`, `DisclaimerLibrary`
- `BrandVoiceConfig`
- `ReplayTrace`

Review focus:

- which system is source of truth vs mirror/cache
- booking lifecycle ownership and state transitions
- schedule/catalog duplication vs Ayla canonical state
- data retention and privacy
- domain services vs business logic in views/tasks

### L3 Application Workflows

Owns user journeys and orchestration across multiple domains.

Apps/modules:

- `orchestrator`
- `orchestrator/llm`
- `orchestrator/memory`
- `orchestrator/safety`
- `skills`
- `skills/booking`
- `skills/faq`
- `skills/welcome`
- `skills/privacy_consent`
- `skills/health_screening`
- `skills/water`
- `skills/food_scanner`
- `skills/food_clarify`
- `skills/food_correction`
- `skills/nutrition_anketa`
- `skills/cross_domain`
- `skills/payment_failed`
- `master_api`
- `admin_api`
- `miniapp_api`

Review focus:

- orchestration boundaries and side effects
- LLM provider access through approved routers/breakers
- skill registry ordering and intent matching
- AI guardrails for wellness and health-sensitive flows
- API workflow ownership vs domain service ownership

### L4 Interface Adapters

Owns external-facing transports, webhooks, channel parsing/outbound adapters,
and third-party clients.

Apps/modules:

- `ingress`
- `channels/max`
- `channels/telegram`
- `integrations/yclients`
- `integrations/ayla`
- `integrations/ayla_payments`
- `integrations/yookassa_retired`
- `catalog/webhooks`
- `kb/webhooks.py`
- `eventbus/views.py`
- `adminconsole`

Root HTTP surfaces from `config/urls.py`:

| Prefix | Owner |
| --- | --- |
| `/admin/observability/` | `apps.observability` |
| `/admin/` | Django admin |
| `/` | `apps.orchestrator` health/ready |
| `/api/v1/ingress/` | `apps.ingress` |
| `/api/v1/catalog/` | `apps.catalog.webhooks` |
| `/api/v1/yclients/` | `apps.integrations.yclients` |
| `/api/v1/yookassa/` | `apps.integrations.yookassa_retired` |
| `/api/v1/internal/events/` | `apps.eventbus` |
| `/api/v1/channels/telegram/` | `apps.channels.telegram` |
| `/api/v1/customer/` | `apps.miniapp_api` |
| `/api/v1/` | `apps.identity` shared `/me` surface |
| `/api/v1/master/` | `apps.master_api` |
| `/api/v1/admin/` | `apps.admin_api` |
| `/api/v1/internal-chat/` | `apps.internal_chat` |
| `/api/v1/salon-knowledge/` | `apps.kb` |

Review focus:

- request auth and tenant resolution per transport
- external retry/idempotency handling
- data redaction before logs/DLQ/audit
- integration-specific logic leaking into domains

### L5 Background Execution And Operations

Owns asynchronous execution, periodic work, Redis Streams consumers, cleanup,
and operational jobs.

Main modules:

- `apps/workers/*`
- `apps/eventbus/dispatcher.py`
- `apps/eventbus/cleanup_tasks.py`
- `apps/eventbus/consumers/*`
- `apps/bookings/tasks.py`
- `apps/bookings/followups.py`
- `apps/bookings/escalation.py`
- `apps/audit/tasks.py`
- `apps/tools/tasks.py`
- `apps/observability/tasks.py`
- `apps/identity/tasks.py`
- `apps/kb/tasks.py`
- `apps/catalog/tasks.py`
- `apps/replay/tasks.py`
- `apps/loyalty/tasks.py`
- `apps/master_api/tasks.py`
- `apps/admin_api/tasks.py`
- `apps/integrations/yclients/tasks.py`

Review focus:

- idempotency and exactly-once/at-least-once assumptions
- tenant propagation into workers
- beat schedule operational load
- retry behavior and DLQ contracts
- notification frequency caps and quiet hours

## App Index

| App | Layer | Primary responsibility |
| --- | --- | --- |
| `admin_api` | L3/L4 | Admin REST API for master roster, services mapping, availability decisions, invites, deactivation. |
| `adminconsole` | L4 | Reserved Django/admin console surface. |
| `audit` | L1/L5 | Audit log storage, audit writing service, retention cleanup. |
| `booking` | L2 | Booking request data, reminders, pending actions, cancel/reschedule/create/feedback services. |
| `bookings` | L2/L5 | Reminder dispatch, followups, reminder callbacks, escalation flows. |
| `catalog` | L2/L4/L5 | Mirrored service/master catalog, catalog webhook, sync/upsert services. |
| `channels` | L4/L5 | MAX and Telegram adapters: parse inbound, send outbound, register worker handlers. |
| `consent` | L1 | Consent records, grant/withdraw services, consent guards. |
| `conversations` | L2/L5 | Conversations, messages, AI drafts, conversation retention tasks. |
| `eventbus` | L1/L4/L5 | Cross-service event ingest, validation, dispatch, consumers, dedupe, DLQ. |
| `events` | L1 | Internal analytics/event vocabulary and fanout. |
| `experiments` | L2 | Experiments, assignments, holdouts. |
| `handoff` | L2/L3 | Admin task/human handoff model and services. |
| `identity` | L1 | Bot user identity, role/profile/memory/personal context, `/api/v1/me`. |
| `ingress` | L4/L5 | MAX webhook entry and Redis Streams ingress journal/queue. |
| `integrations` | L4 | External clients/webhooks: YClients, Ayla, Ayla payments, retired YooKassa endpoint. |
| `internal_chat` | L2/L3/L4 | Master-admin support/chat threads, messages, master/admin APIs. |
| `kb` | L2/L4/L5 | Knowledge documents, ChromaDB client, retriever, GDocs import, webhook ingestion, indexing tasks. |
| `llm` | L3 | Shared LLM protocol/router/provider helpers used by KB/skills/orchestrator paths. |
| `loyalty` | L2/L5 | Loyalty accounts/events/referrals and inactivity downgrade task. |
| `master_api` | L3/L4/L5 | Master mini-app onboarding, profile, dashboard, schedule, conversations, customers, catalog. |
| `miniapp` | L4 | Mini app package placeholder/support area. |
| `miniapp_api` | L3/L4 | Customer mini-app API: auth, slots, services, masters, bookings, feedback, recommendations. |
| `notifications` | L2 | Master notification preferences and quiet-hours settings. |
| `observability` | L1/L4/L5 | Shadow dashboard, AI quality metrics, Sentry/OTel/logging, alerting and monitoring tasks. |
| `orchestrator` | L3 | Bot pipeline, intent routing, LLM breaker, safety checks, memory, UI keyboards, channel registry. |
| `orders` | L2 | Retired order/payment tables shell retained for migration history. |
| `persona` | L2 | Brand voice configuration. |
| `promotions` | L2 | Promotion model and promo validation/formatting. |
| `promptreg` | L2 | Prompt versions, thresholds, disclaimers. |
| `replay` | L2/L5 | Replay traces, redaction, recorder, cleanup. |
| `scheduling` | L2 | Working hours, exceptions, blocks, schedule requests, slot resolver. |
| `skills` | L3 | Skill registry and concrete AI/bot skills. |
| `tenancy` | L1 | Tenant model, staff model, middleware, context, scoped managers. |
| `tools` | L1/L3/L5 | Idempotency keys and tool registry/task cleanup support. |
| `voice` | L3 | Voice-related assistant support. |
| `workers` | L5 | Redis Streams worker registry, tenant-aware task base, consumer, PEL reaper, operator commands. |

## Public API Surface Index

Customer mini-app:

- `GET/POST /api/v1/customer/*` in `apps/miniapp_api`
- Covers auth verify, slots, services, masters, booking create/list/detail,
  cancel/reschedule flows, profile, feedback, recommendations proxy.

Master mini-app:

- `GET/POST/PATCH /api/v1/master/*` in `apps/master_api`
- Covers onboarding claim/accept/reject/profile, dashboard, schedule,
  availability, conversations, customers, catalog, notification prefs.

Admin API:

- `GET/POST/PATCH /api/v1/admin/*` in `apps/admin_api`
- Covers master roster, services mapping, invites, availability approval,
  deactivation, admin operations.

Bot/channel webhooks:

- `POST /api/v1/ingress/max/`
- `POST /api/v1/channels/telegram/<tenant_slug>/webhook/`

Integration webhooks:

- `POST /api/v1/catalog/webhook/`
- `POST /api/v1/yclients/webhook/`
- `POST /api/v1/internal/events/ingest`
- `POST /api/v1/salon-knowledge/webhook/approved/`
- `POST /api/v1/yookassa/webhook/` returns retired `410 Gone`.

Operations/admin:

- `/healthz/`, `/readyz/`
- `/admin/observability/shadow/`
- `/admin/observability/ai-quality/`
- Django admin under `/admin/`

## Cross-Cutting Review Threads

Use these threads for the next architecture review:

1. Tenant boundary: `tenancy`, default managers, request middleware, worker
   tenant propagation, event ingest tenancy checks.
2. Source-of-truth boundary: ADR-0009 says Ayla owns canonical booking,
   schedule, catalog, reviews, profile, and payments, while this repo mirrors
   or routes through REST/events. Review whether current `booking`, `catalog`,
   `scheduling`, `miniapp_api`, and `skills/booking` follow that rule.
3. AI safety boundary: `orchestrator`, `llm`, `skills`, `identity` memory,
   `kb`, `observability`, and wellness skills.
4. Event architecture: distinction between `events` and `eventbus`, event
   versions, idempotency, DLQ, subscriber activation.
5. Notification and retention: `bookings`, `notifications`, `loyalty`,
   `master_api`, `admin_api`, quiet hours, caps, task retries.
6. Sensitive data: phone/email/profile/wellness/food/body data, red-zone
   memory, logs, audit, replay, Sentry, DLQ redaction.
7. View/service separation: high-traffic APIs in `miniapp_api`, `master_api`,
   `admin_api`, `internal_chat`, `integrations/yclients`, `eventbus`.
8. Legacy drain: ensure no production imports from `legacy_*`; ruff TID251 is
   configured to block accidental imports.

## Existing Architecture And Operations Docs

Start points:

- `docs/architecture.md`
- `docs/architecture/event-contract.md`
- `docs/architecture/event-consumers.md`
- `docs/architecture/jwt-contract.md`
- `docs/adr/`
- `docs/design/policies/`
- `docs/design/handoffs/`
- `docs/runbooks/`
- `docs/setup/dev-environment.md`
- `docs/setup/branch-protection.md`

## Test And Guard Index

Configured gates:

- `uv run ruff check .`
- `uv run ruff format --check .`
- `uv run python tools/lint/red_zone_guard.py apps/`
- `uv run python manage.py check`
- `uv run pytest tests/smoke/ -v`
- `uv run mypy apps config tests`

Test locations:

- `tests/` for smoke, integration, contract, and cross-cutting tests.
- `apps/<app>/tests/` for app-level tests.
- `pyproject.toml` excludes `legacy_*` from test recursion.

