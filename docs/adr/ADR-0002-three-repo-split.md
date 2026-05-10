# ADR-0002: Three-repo split — `mysite/`, `ayla-ai-core/`, `ai-bot-platform/`

**Status:** Accepted — 2026-05-07

## Context

`mysite/` is the salon's public website + catalog + SEO + payments. `ayla-ai-core` is a pure AI library used by both the formula_tela bot and the Ayla nutrition app. The bot itself currently lives inside `mysite/maxbot/` and creates 8 architectural cycles between Django apps. We need a place where the bot can grow into a multi-tenant platform without coupling its release cadence to the salon site.

## Decision

Three separate repositories:

| Repo | Role |
|---|---|
| `mysite/` (existing) | Salon website, catalog source-of-truth, SEO/marketing agents, payments, public Django Admin. Stays alive forever. |
| `ayla-ai-core/` (existing, ≥0.6.0) | Pure AI primitives — voice configs, tool schemas, anti-hallucination utilities. No Django, no DB. |
| `ai-bot-platform/` (new) | The bot platform. Depends on `ayla-ai-core`. Pulls catalog from `mysite/` via `apps/catalog` sync (F0.17). |

## Consequences

- **Easier:** clear boundary — bot deploys are decoupled from salon site deploys.
- **Easier:** each repo has its own CODEOWNERS, branch protection, and release cadence.
- **Acceptable:** one more repo to maintain (CI, deploys, dependency updates). Worth it.
- **Harder:** catalog sync is now a critical path — needs monitoring and on-call (Sprint 7 lands the alert).
- **Harder:** `ayla-ai-core` is a shared dependency — breaking changes require bumping in two places. We pin via `git+...@vX.Y.Z` and gate upgrades through PR review.

## Sprint 0 corrections to the original draft

- **`platform/` → `config/`.** The original draft named the Django project package `platform/`. That shadows Python's stdlib `platform` module which Django itself imports for system-info detection. Renamed to `config/` (cookiecutter-django convention) in DRF-403. No other behavioural change.
- **Private-repo deps split into extras.** Both repos are private. `ayla-ai-core` cannot be pulled by GitHub Actions without a `GH_DEPLOY_TOKEN`. Until that secret is configured, the dep moved from core `[project] dependencies` to `[project.optional-dependencies] ai-core`. Local dev opts in via `uv sync --extra dev --extra ai-core`. CI uses `--extra dev` only.

## Alternatives considered

- **Single monorepo.** Rejected. `mysite/` has 4 years of accreted coupling; the bot platform deserves a fresh skeleton with the layering enforced from day one.
- **Bot as a second Django app inside `mysite/`.** Rejected. Recreates the cycle problem; tenant_id cannot scope cleanly across an app boundary that already has 8 cycles.
