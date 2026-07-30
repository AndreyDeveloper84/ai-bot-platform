#!/usr/bin/env python3
"""Sensitive-filename guard (W0-X1 secret containment).

Refuses credential-bearing filenames even when entropy scanning
(detect-secrets) would not flag them — e.g. a Firebase service-account
JSON with low-entropy formatting, or an empty `.env` placeholder that
someone later fills in place. Complements the existing
`forbid-mcp-json` hook (issue #419), which stays as the specialised
root-`.mcp.json` message; this guard covers the full W0-X1 denylist.

The guard inspects PATHS ONLY. It never opens file contents.

Modes:
  (default)   scan staged files  (pre-commit: git diff --cached)
  --all       scan tracked files (CI: git ls-files)

Allowlist: any path ending in `.example` (`.env.example`,
`apps/miniapp/.env.local.example`, `client_secret.json.example`, ...)
is considered a deliberate placeholder template and is allowed.
Everything else matching the denylist is refused.
"""

from __future__ import annotations

import re
import subprocess
import sys

# Denylist from the W0-X1 sensitive filename policy. Patterns are matched
# against the repo-relative path (forward slashes).
DENYLIST: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(^|/)\.env$"), ".env"),
    (re.compile(r"(^|/)\.env\.[^/]+$"), ".env.*"),
    (re.compile(r"(^|/)\.mcp\.json$"), ".mcp.json"),
    (re.compile(r"(^|/)db\.sqlite3(-(journal|wal|shm))?$"), "db.sqlite3"),
    (re.compile(r"\.pem$", re.IGNORECASE), "*.pem"),
    (re.compile(r"\.key$", re.IGNORECASE), "*.key"),
    (re.compile(r"\.p12$", re.IGNORECASE), "*.p12"),
    (re.compile(r"\.pfx$", re.IGNORECASE), "*.pfx"),
    (re.compile(r"(^|/)id_rsa$"), "id_rsa"),
    (re.compile(r"(^|/)id_ed25519$"), "id_ed25519"),
    (re.compile(r"firebase-adminsdk[^/]*\.json$", re.IGNORECASE), "*firebase-adminsdk*.json"),
    (re.compile(r"(^|/)firebase-admin\.json$", re.IGNORECASE), "firebase-admin.json"),
    (re.compile(r"service[-_]account[^/]*\.json$", re.IGNORECASE), "*service-account*.json"),
    (re.compile(r"(^|/)credentials\.json$", re.IGNORECASE), "credentials.json"),
    (re.compile(r"(^|/)client_secret[^/]*\.json$", re.IGNORECASE), "client_secret*.json"),
]

ALLOW_SUFFIX = ".example"


def list_paths(scan_all: bool) -> list[str]:
    if scan_all:
        cmd = ["git", "ls-files"]
    else:
        cmd = ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    return [line.strip() for line in out.splitlines() if line.strip()]


def main(argv: list[str]) -> int:
    scan_all = "--all" in argv
    violations: list[tuple[str, str]] = []
    for path in list_paths(scan_all):
        if path.endswith(ALLOW_SUFFIX):
            continue
        for pattern, label in DENYLIST:
            if pattern.search(path):
                violations.append((path, label))
                break

    if not violations:
        return 0

    print("ERROR: sensitive filename(s) must not be committed:", file=sys.stderr)
    for path, label in violations:
        print(f"  {path}  (matches denylist pattern: {label})", file=sys.stderr)
    print(
        "These names are credential-bearing by policy (W0-X1). If this is a\n"
        "placeholder template, rename it with an `.example` suffix instead.\n"
        "Source real tokens from 1Password — see docs/setup/secrets-vault.md.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
