# ai-bot-platform

[![ci](https://github.com/AndreyDeveloper84/ai-bot-platform/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/AndreyDeveloper84/ai-bot-platform/actions/workflows/ci.yml)

Multi-tenant AI bot platform — Phase 0.

> Status: **Sprint 0 / A1 scaffold.** Not production-ready. Boots Django with 20 empty apps. Real implementation lands sprint-by-sprint over weeks 1–22.

## Service responsibility

`ai-bot-platform` — отдельный backend-сервис для AI-бота салона/платформы. Отвечает за:

- AI-консультации;
- маршрутизацию намерений пользователя;
- RAG по базе знаний;
- интеграции с MAX, сайтом, YClients и будущим мобильным приложением;
- хранение диалогового состояния;
- аналитику сценариев.

## Where to read first

- [`docs/architecture.md`](./docs/architecture.md) — condensed architecture (ported from `mysite/docs/arch/PHASE0_DESIGN.md` v2 in DRF-412).
- [`docs/adr/`](./docs/adr/) — Architecture Decision Records 0001–0006 (DRF-413).
- Source materials: `mysite/docs/arch/` in the `formula_tela` repo (5 PDFs + 2 deep-research + compass skill catalog + full design doc).

## Repo layout (Sprint 0)

```
ai-bot-platform/
├── config/                  ← Django project package (settings, urls, wsgi/asgi, celery)
├── apps/                    ← 20 platform apps (empty in Sprint 0)
├── tests/smoke/             ← "Django boots" smoke test
├── pyproject.toml
└── manage.py
```

The 20 apps and the sprint that puts code in each:

| App | Sprint |
|---|---|
| tenancy, identity | 2 |
| conversations, orchestrator | 1 |
| skills, tools | 3 |
| kb | 7 (chromadb migration from `formulatela_mcp`) |
| channels, ingress, workers | 4 |
| consent, audit | 2 |
| events | 1 |
| experiments | 6 |
| voice | 6 |
| catalog | 7 (F0.17 sync from `mysite/services_app/`) |
| replay | 5 |
| promptreg | 6 |
| adminconsole | 8 |
| handoff | 4 |

## Quickstart (Sprint 0)

```powershell
# 1. Install deps (uv expected on PATH — see "Install uv" below)
uv sync --extra dev

# 2. Install pre-commit hooks (one-time per clone)
uv run pre-commit install

# 3. Verify the scaffold
uv run python manage.py check
uv run python manage.py migrate
uv run pytest tests/smoke/
```

Full Postgres / Redis / chromadb / MinIO stack lives in Sprint 0 / A2 (`docker compose up`). The host-side flow above uses SQLite.

### Install uv

uv is the project's package manager (PEP 735, deterministic locks). It must live outside `.venv` because it creates the venv:

```powershell
# Astral installer (preferred):
irm https://astral.sh/uv/install.ps1 | iex

# Or via pipx:
pipx install uv

# Or via system Python:
python -m pip install --user uv
```

### Pre-commit hooks

After `uv run pre-commit install`, every `git commit` runs locally:
- `ruff check` + `ruff format`
- `detect-secrets` (block accidental token commits — bypass with `pragma: allowlist secret` if false positive)
- `check-yaml`, `check-toml`, `check-json`, `trailing-whitespace`, `end-of-file-fixer`, `check-merge-conflict`, `check-added-large-files`

To run all hooks against the whole repo on demand:

```powershell
uv run pre-commit run --all-files
```

## Git workflow

> Sprint 10 / DRF-891 introduces a **two-tier deploy flow** with a dev MAX-bot. Aligns with the formula_tela pattern of "dev bot for debugging" but enforced via branch protection.

### Branch model

```
feature/X
    │ PR (CI must be green)
    ▼
dev ──────────────► @id583403546770_1_bot (dev MAX-bot)
    │ manual test on dev-bot ≥1h (24h before canary bumps)
    │ PR dev → main (1 approval, CI green, linear history)
    ▼
main ─────────────► @ai_bot_platform (prod MAX-bot, X-track canary)
```

| Branch | Direct push | PR required | Approvals | Use |
|---|---|---|---|---|
| `feat/*`, `fix/*`, `chore/*` | yes (own branches) | yes (target: `dev`) | 0 | feature work |
| `dev` | Lead only | for non-Lead PRs | 0 | dev-bot validation |
| `main` | **forbidden** | yes (only from `dev`) | 1 (Lead) | prod-bot rollout |

Branch protection enforces this — see [`docs/setup/branch-protection.md`](docs/setup/branch-protection.md) for the `gh api` commands that apply the rules.

### Rules

1. **All feature work targets `dev`**, never `main`. CI must be green to merge.
2. **Code must spend ≥1h on dev-bot before merging to `main`** (the "did you actually try it?" gate). For changes during X-track canary windows: ≥24h on dev-bot before any `dev → main` PR. The 24h soak is documented in [`docs/runbooks/canary-ramp.md`](docs/runbooks/canary-ramp.md).
3. **Force-pushes** allowed on `dev` (rebase workflows), forbidden on `main` (history is canonical).
4. **Hotfix path** for emergencies (e.g. critical security patch): cherry-pick to `dev`, smoke on dev-bot in ≥15 min, then PR to `main` with `hotfix` label + Lead emergency approval. Skips the 1h gate but NOT the dev-bot exposure entirely.
5. **Direct push to `main` is rejected at the GitHub level** — branch protection forbids it. The only way code reaches `main` is via reviewed PR from `dev`.

### Setup

After cloning, activate the pre-push hook (one-time per clone):

```
git config core.hooksPath .githooks
```

This blocks `git push origin main` locally — the dev-flow is enforced
at the local git level since the repo is private + free-tier (GitHub
branch protection requires Pro). See [`docs/setup/branch-protection.md`](docs/setup/branch-protection.md) § Phase 0 state for rationale + Phase 1 upgrade triggers.

Dev environment + MAX-bot creation: [`docs/setup/dev-environment.md`](docs/setup/dev-environment.md) (operator setup, ~4-6h one-time).

Deploy workflows:
- `.github/workflows/deploy-dev.yml` — fires on push to `dev`
- `.github/workflows/deploy.yml` — fires on push to `main`

Both are bootstrap-skeleton until DRF-891 completes; uncomment the SSH+deploy steps after GitHub secrets (DEV_HOST, PROD_HOST, etc.) are populated.

### Why this matters now

Before X-5pct (DRF-874 — 5% of real MAX traffic on platform), there's no risk: nobody's reading the code. After X-5pct, every merge to `main` is a partial production rollout within ~minutes. Dev-flow + branch protection are the cheapest way to keep the canary as a rollback safety net rather than the first-line bug filter.

## Migration context

This repo replaces `formula_tela/mysite/maxbot/` (frozen since 2026-05-09 — see `mysite/maxbot/.FROZEN`). The frozen `maxbot/` is copied AS-IS into `legacy_maxbot/` in Sprint 0 / C7 (`DRF-409`) and drained sprint-by-sprint until 100% cutover in Sprint 10.

## Linear

[`ai-bot-platform Phase 0`](https://linear.app/drfproject/project/ai-bot-platform-phase-0-87eeee7605dd)
