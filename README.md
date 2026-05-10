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

## Migration context

This repo replaces `formula_tela/mysite/maxbot/` (frozen since 2026-05-09 — see `mysite/maxbot/.FROZEN`). The frozen `maxbot/` is copied AS-IS into `legacy_maxbot/` in Sprint 0 / C7 (`DRF-409`) and drained sprint-by-sprint until 100% cutover in Sprint 10.

## Linear

[`ai-bot-platform Phase 0`](https://linear.app/drfproject/project/ai-bot-platform-phase-0-87eeee7605dd)
