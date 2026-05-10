# legacy_maxbot/ — AS-IS migration source for ai-bot-platform

> **Read this BEFORE editing anything in this directory.**

**Snapshot of:** `mysite/maxbot/` at commit `a52e4e6` (freeze date 2026-05-09)
**Source repo:** github.com/AndreyDeveloper84/formula_tela
**Migration plan:** `mysite/docs/arch/PHASE0_DESIGN.md` v2 §2

---

## Purpose

This directory is the **read-only migration reference** inside ai-bot-platform.
It is drained sprint-by-sprint into `apps/` per `docs/architecture.md`.

The two files alongside this notice (`.FROZEN` and `README.md`) are the
*original* freeze policy from the source repo — they're preserved here
because they describe the freeze contract that produced this snapshot.
This `MIGRATION_NOTICE.md` is the ai-bot-platform-side companion and
takes precedence for anything specific to this repo.

## Lifecycle

- **Sprint 0:** Frozen snapshot. Imported as a regular Python package
  (DRF-410 will lock down `INSTALLED_APPS`). Excluded from ruff/mypy/CI
  checks via `pyproject.toml` `extend-exclude` and `[tool.mypy] exclude`.
- **Sprint 1+:** Each migrated module gets a tombstone comment in
  legacy + a row in the "drained" table below noting the new home.
- **Sprint 10 (week 21-22):** 100% drained → directory deleted in the
  cleanup PR.

## Do NOT

- ❌ Edit any file here. Fix the new home in `apps/` instead.
- ❌ Add new files. Build the new feature directly in `apps/`.
- ❌ Add `legacy_maxbot` (or any subdirectory) to `INSTALLED_APPS`.
- ❌ Run migrations from here.
- ❌ Add tests for legacy behaviour here.

## OK to

- ✅ `from legacy_maxbot.x import y` in temporary glue while a
  migration is in flight (mark with `# TODO(sprint N): drop after migration`).
- ✅ Read it for reference when porting a feature.
- ✅ Diff a migration PR against legacy to ensure nothing was dropped silently.

## Production parity

The ACTIVE production bot still runs from `mysite/maxbot/` until Sprint 10
cutover. Critical security fixes there must be cherry-picked here in the
same PR window per the source-repo FROZEN-EXEMPT process.

## Drained-into-apps map

Updated as each migration PR lands. Empty in Sprint 0.

| legacy module | new home | sprint | PR |
|---|---|---|---|
| _(none yet)_ | | | |
