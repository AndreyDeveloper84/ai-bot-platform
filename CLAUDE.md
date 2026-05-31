# CLAUDE.md

Guidance for working in this repository. Read `docs/architecture.md` and `docs/adr/` first for deep context.

## What this is

`ai-bot-platform` — multi-tenant AI bot platform for salons. Django 5.2 + DRF backend.
Despite the README still saying "Sprint 0 scaffold", most apps now contain real code
(Phase 1). Python ≥3.12, package manager is **uv** (deterministic locks via `uv.lock`).

## Related repositories (three-repo split)

This repo is one of three. It does NOT live alone:

```
              ai-bot-platform  (this repo — Django bot platform)
                /                          \
   (1) pip dependency                (2) network HTTP
       ayla-ai-core                      beautygo_backend
   pure AI library                  unified external backend, two roles:
   (voice/prompts/tools)            • Ayla nutrition REST API
                                    • salon services catalog source
```

### 1. `ayla-ai-core` — Python library dependency
- Pure AI primitives (voice configs, tool schemas, anti-hallucination, `render_system_prompt`). No Django, no DB.
- Pinned in `pyproject.toml` → `[project.optional-dependencies].ai-core` by **commit SHA** (not tag — tags are force-pushable). Currently v0.8.1.
- Install: `uv sync --extra dev --extra ai-core` (needs GitHub auth; repo is private).
- Safety boundary lives in `apps/orchestrator/ayla_adapter.py` (brace-escape, control-char strip, `<<<UNTRUSTED_CONTEXT>>>` wrapping, replay clock).
- Used across `apps/skills/*`, `apps/voice/`, `apps/promptreg/`.

### 2. `beautygo_backend` — external network service (two subsystems)
The repo name does NOT yet appear in code; code still uses the historical env names below.

| Subsystem | Code references | Client / integration |
|---|---|---|
| **Ayla nutrition API** | `AYLA_BASE_URL` + `AYLA_SERVICE_TOKEN`; endpoints `/api/v1/nutrition/internal/{scan,food-log,summary,water,profile,deficits,insights/cross_domain}/` (auth: `X-Service-Token` + `X-External-User-ID`) | `apps/integrations/ayla/nutrition_client.py`, `user_proxy.py` |
| **Salon services catalog** | `MYSITE_CATALOG_BASE_URL` (currently `https://formulatela58.ru`); webhook `/api/v1/catalog/webhook/`; sync every 15 min | `apps/catalog/*` |

> ⚠️ **Two distinct "Ayla"s** — don't confuse them:
> - `ayla-ai-core` = in-process Python **library**.
> - "Ayla nutrition backend" = a **network REST service** hosted inside `beautygo_backend`.
>
> ⚠️ `ADR-0002` (three-repo split: `mysite` + `ayla-ai-core` + `ai-bot-platform`) predates the
> `beautygo_backend` naming. Env names `AYLA_*` and `MYSITE_CATALOG_BASE_URL`/`formulatela58.ru`
> still point at what is now `beautygo_backend`.

## Tech stack

- **Core**: Django 5.2, DRF 3.15+, Celery[redis] 5.4+, Redis 5+, psycopg 3 (Postgres)
- **AI**: OpenAI ≥1.50, Anthropic ≥0.40, `ayla-ai-core` (extra)
- **RAG**: ChromaDB ≥0.5
- **Security**: django-cryptography-django5 (field-level encryption)
- **Observability**: OpenTelemetry (1.26.x), Sentry SDK 2.x (with PII scrubber)
- **HTTP/utils**: httpx, python-dotenv, transliterate
- **Dev**: pytest (+django/asyncio/cov), ruff, mypy + django-stubs, pre-commit, detect-secrets, freezegun, model-bakery

## Layout

```
config/        Django project package: settings/{base,local,staging,production}.py, urls.py, celery.py
apps/          ~32 platform apps (most have real code; adminconsole is scaffold-only)
tests/         Cross-cutting: smoke/ integration/ e2e/ tenancy/ fixtures/ tools/
docs/          architecture.md, adr/ (7), runbooks/ (14), setup/, plans/, design/, qa/
legacy_*/      Read-only migration sources — see below
infra/         nginx shadow-mode config, secrets placeholder
scripts/backup Postgres PITR automation
```

### Apps map (by role)
- **Core**: `tenancy` (X-Tenant + ContextVar), `identity` (BotUser), `conversations` (state machine), `orchestrator` (intent routing/LLM/safety/pipeline + `ayla_adapter`), `observability`
- **Knowledge/LLM**: `kb` (ChromaDB RAG), `llm` (provider-agnostic, breaker, token caps), `skills` (registry: booking/faq/food_scanner/nutrition/water/handoff/…), `tools` (idempotency)
- **Domain data**: `booking`/`bookings`, `catalog`, `orders`, `promotions`, `scheduling`
- **Channels/interfaces**: `channels` (MAX + Telegram), `ingress` (webhook mux), `miniapp` (Vite+React+TS) / `miniapp_api`
- **Integrations**: `integrations/{ayla,yclients,yookassa}`
- **System**: `audit`, `consent`, `experiments`, `handoff`, `persona`, `promptreg`, `voice`, `events`, `replay`, `workers`

### Legacy directories (read-only, drained sprint-by-sprint into `apps/`)
- `legacy_maxbot/` — frozen snapshot of production bot (`mysite/maxbot/`)
- `legacy_formulatela_mcp/` — KB/RAG MCP server → drains into `apps/kb/`
- `legacy_notifications/` — channel adapters → drains into `apps/channels/`
- **Banned from import** via ruff TID251. Build new code in `apps/`. Transitional glue needs `# noqa: TID251` + `# TODO(sprint N)`.

## Common commands

```bash
uv sync --extra dev                      # install (no AI core; CI default)
uv sync --extra dev --extra ai-core      # install with ayla-ai-core (needs GH auth)
uv run python manage.py check
uv run python manage.py migrate
uv run pytest                            # default suite (excludes smoke)
uv run pytest tests/smoke/
uv run pytest -m e2e                     # needs Redis + Postgres
uv run pre-commit run --all-files        # ruff + detect-secrets + file hygiene
```

- pytest settings: `config.settings.local`; asyncio mode strict (`@pytest.mark.asyncio` required); markers `e2e`/`smoke`/`slow`.
- ruff line length 100, target py312.

## Conventions / gotchas

- **Multi-tenancy is load-bearing**: tenant resolved via X-Tenant header → ContextVar (ADR-0001/0003). `STRICT_TENANT_SCOPE` is tri-value (audit/strict/off). The cross-tenant leakage test is critical — never weaken it.
- **Celery beat** has ~11 scheduled jobs (cleanups, catalog sync, reminders, shadow-delta) in `config/settings/base.py`.
- Untrusted external text (e.g. Ayla cross-domain hints) is sanitized before reaching prompts — see `_sanitize_hint` in `nutrition_client.py` and `ayla_adapter.py`.

## Git workflow

- Feature work targets `dev`, never `main`. `main` is reached only via reviewed PR from `dev`.
- Local pre-push hook blocks pushing to `main` — `git config core.hooksPath .githooks`.
- See README "Git workflow" + `docs/setup/branch-protection.md`.
