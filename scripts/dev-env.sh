#!/usr/bin/env bash
# Build (or repair) this worktree's virtualenv so it matches the pins in
# pyproject.toml / uv.lock. DRF-1384.
#
# This is THE command for a fresh worktree:
#
#     git worktree add ../ai-bot-platform-drfNNNN -b fix/drfNNNN origin/dev
#     cd ../ai-bot-platform-drfNNNN
#     scripts/dev-env.sh
#     uv run pytest apps/identity -q      # green
#
# It is idempotent — re-run it whenever `ayla-ai-core` is re-pinned, or
# whenever the guard in tools/env_guard.py tells you the environment is
# behind. Typical cost on a warm uv cache: ~15s.
#
# Why not just `uv sync`: the README's `uv sync --extra dev` omits
# `--extra ai-core`, which leaves every `from ayla_ai_core import ...`
# in apps/ unresolvable. And without `--frozen`, uv is free to re-resolve
# and silently move off the locked revision. Both flags are load-bearing.
#
# PowerShell equivalent: scripts/dev-env.ps1

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if ! command -v uv >/dev/null 2>&1; then
    cat >&2 <<'EOF'
ERROR: `uv` is not on PATH.

uv is the project's package manager and must live OUTSIDE .venv, because
it is what creates .venv. Install it, then re-run this script:

    irm https://astral.sh/uv/install.ps1 | iex     # Windows PowerShell
    curl -LsSf https://astral.sh/uv/install.sh | sh # macOS / Linux
    python -m pip install --user uv                 # fallback
EOF
    exit 1
fi

echo "==> uv sync --extra dev --extra ai-core --frozen   (in $REPO_ROOT)"
# `ai-core` pulls a private GitHub repo. Locally that needs your usual git
# auth (ssh key or credential helper); CI rewrites the URL with
# GH_DEPLOY_TOKEN. If this step fails on auth, the environment is NOT
# usable for anything touching apps/orchestrator — do not paper over it by
# dropping the extra.
if ! uv sync --extra dev --extra ai-core --frozen; then
    cat >&2 <<'EOF'

ERROR: `uv sync` failed.

If it failed fetching ayla-ai-core (a PRIVATE repo), your git auth cannot
reach github.com/AndreyDeveloper84/ayla-ai-core. Fix the auth — do not
retry without `--extra ai-core`, and do not copy another worktree's
.venv: one `pip install` in a borrowed venv breaks it for its owner.
EOF
    exit 1
fi

echo "==> verifying the environment against the pins"
uv run --no-sync python tools/env_guard.py

cat <<'EOF'

Environment is current. Next:

    uv run pytest apps/identity -q

Always run tests through `uv run` — the system interpreter carries a
different Django and fails in ways that look like code defects.
EOF
