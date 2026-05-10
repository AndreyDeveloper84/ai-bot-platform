# ADR-0003: tenant_id propagation via TenantContext (ContextVar)

**Status:** Accepted — 2026-05-07

## Context

`tenant_id` must be available in every code path: middleware, model managers, cache wrappers, Celery tasks, replay recorder, audit writer. Passing it as a parameter through every function signature is unsustainable — it's viral and one missed call leaks data. Threadlocal storage breaks under async (Django 5 + ASGI + Celery's prefork worker). We need one primitive that works for both sync and async without forcing every function in the codebase to grow a `tenant=` parameter.

## Decision

Use Python's `contextvars.ContextVar`.

- `apps.tenancy.context.TENANT: ContextVar[Tenant | None]` — the canonical store.
- `set_tenant(tenant)` returns a `Token`; `reset_tenant(token)` is paired in `try/finally`.
- Set at request entry by `apps.tenancy.middleware`, and at worker job entry by `apps.workers.consumer`.
- Direct module-level access is forbidden — only the public helpers `current_tenant()` / `tenant_scope()` are used.

ContextVar propagates correctly across `await`, `asyncio.gather`, and Django's `sync_to_async` / `async_to_sync` boundaries — unlike `threading.local`, which is per-OS-thread and silently empties out on coroutine resumption.

## Consequences

- **Easier:** any function inside a request/task can do `current_tenant()` without growing a parameter.
- **Easier:** scoped managers (`Model.objects.filter(...)`) read the same `ContextVar` and apply `tenant=` automatically.
- **Acceptable:** Celery tasks must read `tenant_id` from the task payload and call `set_tenant()` themselves. Standardised in `apps.workers.base.TenantAwareTask`.
- **Acceptable:** tests must enter `with tenant_scope(t):` or use the `tenant_scope` pytest fixture.

## Alternatives considered

- **Threadlocal storage (`threading.local`).** Rejected. Breaks under asyncio because each coroutine resumption can land on a different OS thread. Would silently leak tenant context cross-request under load.
- **Pass `tenant` through every function signature.** Rejected. Viral — a 1500-call codebase becomes a 1500-call refactor, and one missed call is a silent leak.
- **URL-based routing (`/tenants/<slug>/`).** Rejected. MAX webhooks and YClients callbacks don't carry the tenant slug — only a channel token. Resolution has to be from the token to the tenant, which middleware does once and then `set_tenant()`s.
- **Header-only propagation (`X-Tenant-Slug`).** Rejected. Same problem — webhooks don't add custom headers.
