#!/usr/bin/env bash
# Pre-commit hook companion (.pre-commit-config.yaml::forbid-mcp-json).
# Refuses any commit that stages `.mcp.json` at repo root. The file is
# gitignored (see issue #419) and must never enter version control —
# it carries Figma / Notion MCP server tokens.
#
# Pre-commit passes the matched file paths as positional args. We just
# print a directive and exit non-zero; pre-commit aborts the commit.

set -euo pipefail

echo "ERROR: Refusing to commit .mcp.json — contains MCP server tokens."
echo "       Copy .mcp.json.example → .mcp.json (gitignored) and source"
echo "       real tokens from 1Password."
echo "       See docs/setup/secrets-vault.md and issue #419."
exit 1
