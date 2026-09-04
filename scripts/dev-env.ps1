# Build (or repair) this worktree's virtualenv so it matches the pins in
# pyproject.toml / uv.lock. DRF-1384.
#
# This is THE command for a fresh worktree:
#
#     git worktree add ..\ai-bot-platform-drfNNNN -b fix/drfNNNN origin/dev
#     cd ..\ai-bot-platform-drfNNNN
#     powershell -File scripts\dev-env.ps1
#     uv run pytest apps/identity -q      # green
#
# Idempotent -- re-run it whenever `ayla-ai-core` is re-pinned, or whenever
# the guard in tools/env_guard.py says the environment is behind. Typical
# cost on a warm uv cache: ~15s.
#
# git-bash / WSL / macOS / Linux equivalent: scripts/dev-env.sh

$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $RepoRoot

if ($null -eq (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Error @'
`uv` is not on PATH.

uv is the project's package manager and must live OUTSIDE .venv, because
it is what creates .venv. Install it, then re-run this script:

    irm https://astral.sh/uv/install.ps1 | iex
    python -m pip install --user uv        # fallback
'@
    exit 1
}

Write-Host "==> uv sync --extra dev --extra ai-core --frozen   (in $RepoRoot)"
# `ai-core` pulls ayla-ai-core from a separate PUBLIC repo (owner's
# decision 04.09.2026), so this normally works with no git auth at all.
# CI and Docker still rewrite the URL with GH_DEPLOY_TOKEN when that
# variable is non-empty, for the day the visibility is closed again. If
# this step fails, the environment is NOT usable for anything touching
# apps/orchestrator -- do not paper over it by dropping the extra.
uv sync --extra dev --extra ai-core --frozen
if ($LASTEXITCODE -ne 0) {
    Write-Error @'
`uv sync` failed.

If it failed fetching ayla-ai-core, you cannot reach
github.com/AndreyDeveloper84/ayla-ai-core. That repo is public, so this is
usually network/proxy rather than auth -- check plain
`git ls-remote https://github.com/AndreyDeveloper84/ayla-ai-core.git`
first. Fix it -- do not retry without `--extra ai-core`, and do not copy
another worktree's .venv: one `pip install` in a borrowed venv breaks it
for its owner.
'@
    exit 1
}

Write-Host '==> verifying the environment against the pins'
uv run --no-sync python tools/env_guard.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host @'

Environment is current. Next:

    uv run pytest apps/identity -q

Always run tests through `uv run` -- the system interpreter carries a
different Django and fails in ways that look like code defects.
'@
