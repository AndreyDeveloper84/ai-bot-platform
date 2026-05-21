# Secrets vault — operator setup

Closes the secrets-hygiene leg of issue #419 (Phase 0 / Sprint 1 Foundation, Bucket 3).

## TL;DR

Two categories of secrets live in this repo's runtime; neither is committed:

| Where | What | Local source | Production source |
|---|---|---|---|
| `.env` (project root) | Server runtime (Postgres URL, OpenAI key, MAX bot token, Redis URL, webhook secret, …) | `.env.example` → fill from 1Password "Engineering / dev.env" item | `/etc/ai-bot-platform/dev.env` populated from 1Password Connect by the deploy runbook |
| `.mcp.json` (project root) | Claude Code MCP server tokens (Figma, Notion) — only matters to engineers running Claude Code locally | `.mcp.json.example` → fill from 1Password individual MCP items | N/A — MCP runs on developer machines, never on prod servers |

Both files are git-ignored. The `*.example` siblings are committed and carry only placeholder strings.

## First-time setup (engineer onboarding)

### 1. Install 1Password CLI (or use the GUI)

```bash
# macOS
brew install 1password-cli

# Linux (per https://developer.1password.com/docs/cli/get-started)
op --version  # verify
```

Sign in to the Engineering vault:

```bash
op signin
```

### 2. Populate `.env`

```bash
cp .env.example .env
# Open .env in editor, fill values. For each placeholder, the
# corresponding 1Password item is in the "Engineering" vault under
# "ai-bot-platform / dev.env". Quick path via CLI:
op item get "ai-bot-platform / dev.env" --vault Engineering --reveal --format json
```

Each placeholder in `.env.example` has a comment pointing to the 1Password field. If a placeholder lacks a comment, the value is non-secret (e.g., `MAX_API_BASE`) and the example value is the production value.

### 3. Populate `.mcp.json` (only if you use Claude Code MCP servers locally)

```bash
cp .mcp.json.example .mcp.json
```

Replace the `REPLACE_WITH_1PASSWORD_OP://…` placeholders with the real tokens. With the CLI:

```bash
# Figma access token
op read "op://Engineering/Figma MCP/access_token"

# Notion API key
op read "op://Engineering/Notion MCP/api_key"
```

Paste each result into the corresponding `env` slot in `.mcp.json`. Save.

> **Skip this step entirely** if you don't use Figma/Notion MCP integrations. Claude Code works fine without `.mcp.json`; missing `mcpServers` entries are no-ops.

### 4. Verify

```bash
# Confirm git won't pick up your secrets.
git status --porcelain | grep -E '\.env$|\.mcp\.json$'  # must print NOTHING

# Confirm detect-secrets baseline is clean.
uv run detect-secrets-hook --baseline .secrets.baseline .env .mcp.json
```

## Production secret rotation

The dev/prod runtime env files live in `/etc/ai-bot-platform/{dev,prod}.env` on the VPS. They are populated by the deploy runbook (`docs/runbooks/deploy-dev.md` — TBD as part of #417 DNS flip work). Rotation flow:

1. Generate new value in the source service (e.g., MAX bot dashboard → rotate token).
2. Update the 1Password item in vault "Engineering / ai-bot-platform / {dev,prod}.env".
3. SSH to host, edit `/etc/ai-bot-platform/{dev,prod}.env`, paste new value.
4. Restart affected systemd units:
   ```bash
   sudo systemctl restart ai-bot-platform-{dev,prod}{,-consumer,-beat}
   ```
5. Verify with a smoke (e.g., for MAX_BOT_TOKEN: ingress a `/start` and confirm bot replies).
6. Revoke the old value at the source service.

> 1Password Connect server-side sync is on the Phase 1 roadmap (issue TBD). Until then, rotation is a manual sequence — the value goes through a human, not an API.

## What's gitignored

Listed for grep convenience. Authoritative source is `.gitignore`.

- `.env`, `.env.local`, `.env.*.local`
- `.mcp.json`, `.mcp.local.json`

Allow-listed `*.example` siblings ARE committed:

- `.env.example`
- `.mcp.json.example`

## Pre-commit safety net

`detect-secrets` (Yelp) runs on every staged change via the pre-commit hook (`.pre-commit-config.yaml`). The `.secrets.baseline` file pins known false positives so new high-entropy strings get flagged immediately.

If detect-secrets blocks your commit:

1. **It found a real secret** → remove it, source from 1Password instead, re-commit.
2. **It flagged a false positive** (test fixture, example, public ID) → either inline-allowlist (`# pragma: allowlist secret`) OR add to baseline:
   ```bash
   uv run detect-secrets scan --baseline .secrets.baseline
   git add .secrets.baseline
   ```

Never bypass the hook with `--no-verify` to ship a secret. If you genuinely must, file an issue first.

## Historical note

Per issue #419 audit: `.mcp.json` was suspected to contain Figma + Notion plaintext tokens at some prior point. As of this PR there is no `.mcp.json` in `HEAD` or in any commit on `main` / `dev` reachable from this repo's history (`git log --all --diff-filter=A -- '.mcp.json'` returns empty). No history-rewrite needed. The preventive infrastructure here (`.gitignore`, `.mcp.json.example`, pre-commit) closes the loop so the file can never sneak back in unnoticed.
