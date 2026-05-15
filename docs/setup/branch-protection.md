# Branch protection rules — `main` + `dev`

> Sprint 10 / DRF-891 task 3 — operator setup procedure.
> Status: ready to apply. Run the `gh api` commands below from a shell
> authenticated as repo owner OR set the rules via the GitHub UI.

## Why we need this

Before Sprint 10 X-5pct (5% of real MAX traffic), every merge to `main`
must go through dev validation. Branch protection enforces this at the
GitHub level so nobody can accidentally `git push` straight to `main`.

Without this, the dev-flow we set up is a convention, not a guarantee.

## Target state

| Branch | Protection | Direct push | PR required | Approvals | Required checks |
|---|---|---|---|---|---|
| `main` | strict | **forbidden** | yes, from `dev` only | 1 (Lead approval) | `ci`, `replay` |
| `dev` | loose | allowed for Lead | yes for non-Lead contributors | 0 (CI only) | `ci`, `replay` |

`dev` allows direct push for the Lead because the whole point of dev is
to be a fast-iteration playground. PR-only on dev would defeat that.

## Apply via `gh api` — portable across cmd / PowerShell / bash

The rule payload lives in two JSON files in this folder:

* `docs/setup/protection-main.json`
* `docs/setup/protection-dev.json`

This avoids shell-specific heredoc syntax (which doesn't work in
Windows `cmd.exe` or PowerShell). Pass the file via `--input <path>`.
The commands below work identically in any shell.

### Protect `main`

From the repo root:

```
gh api -X PUT repos/AndreyDeveloper84/ai-bot-platform/branches/main/protection --input docs/setup/protection-main.json
```

### Protect `dev`

```
gh api -X PUT repos/AndreyDeveloper84/ai-bot-platform/branches/dev/protection --input docs/setup/protection-dev.json
```

### Rule payload contents (for reference)

`protection-main.json`:

```json
{
  "required_status_checks": {
    "strict": true,
    "contexts": ["ci / pytest + ruff + mypy", "replay / replay regression gate"]
  },
  "enforce_admins": false,
  "required_pull_request_reviews": {
    "required_approving_review_count": 1,
    "dismiss_stale_reviews": true,
    "require_code_owner_reviews": false
  },
  "restrictions": null,
  "required_linear_history": true,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "required_conversation_resolution": true
}
```

`protection-dev.json`:

```json
{
  "required_status_checks": {
    "strict": false,
    "contexts": ["ci / pytest + ruff + mypy", "replay / replay regression gate"]
  },
  "enforce_admins": false,
  "required_pull_request_reviews": null,
  "restrictions": null,
  "required_linear_history": false,
  "allow_force_pushes": true,
  "allow_deletions": false,
  "required_conversation_resolution": false
}
```

### Why each field matters

- `enforce_admins: false` — Lead can still force-merge during
  emergencies (e.g. critical security patch). Set to `true` once the
  team has 2+ humans in Phase 1.
- `required_linear_history: true` (main) forces squash-or-rebase merges
  (no merge commits cluttering main).
- `dismiss_stale_reviews: true` (main) — re-approval needed if more
  commits land after the approval. Otherwise a "approved + then I
  sneak in a bad commit" pattern is possible.
- `required_pull_request_reviews: null` (dev) — no PR required, Lead
  can direct-push for fast iteration.
- `allow_force_pushes: true` (dev) — dev is allowed to be force-pushed
  (rebase + reset workflows). `main` is not.
- CI/replay still required on every push to dev so a broken dev branch
  can't poison the dev MAX-bot.

### bash heredoc alternative (Linux/macOS/Git-Bash only)

If you prefer to inline the JSON and you're in a POSIX shell:

<details>
<summary>Click to expand bash heredoc form</summary>

```bash
gh api -X PUT repos/AndreyDeveloper84/ai-bot-platform/branches/main/protection \
  --input - <<'JSON'
{
  "required_status_checks": {
    "strict": true,
    "contexts": ["ci / pytest + ruff + mypy", "replay / replay regression gate"]
  },
  "enforce_admins": false,
  "required_pull_request_reviews": {
    "required_approving_review_count": 1,
    "dismiss_stale_reviews": true,
    "require_code_owner_reviews": false
  },
  "restrictions": null,
  "required_linear_history": true,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "required_conversation_resolution": true
}
JSON
```

```bash
gh api -X PUT repos/AndreyDeveloper84/ai-bot-platform/branches/dev/protection \
  --input - <<'JSON'
{
  "required_status_checks": {
    "strict": false,
    "contexts": ["ci / pytest + ruff + mypy", "replay / replay regression gate"]
  },
  "enforce_admins": false,
  "required_pull_request_reviews": null,
  "restrictions": null,
  "required_linear_history": false,
  "allow_force_pushes": true,
  "allow_deletions": false,
  "required_conversation_resolution": false
}
JSON
```

Note: `<<'JSON'` and unterminated single-quoted strings are bash
features. They will fail in `cmd.exe` and PowerShell. Use the
`--input <file>` form above on Windows.

</details>

## Apply via GitHub UI (fallback)

1. Open https://github.com/AndreyDeveloper84/ai-bot-platform/settings/branches
2. **Add rule** for `main`:
   - Branch name pattern: `main`
   - Require a pull request before merging: **ON**
   - Require approvals: **1**
   - Dismiss stale pull request approvals: **ON**
   - Require status checks before merging: **ON**
   - Required status checks: `ci / pytest + ruff + mypy`, `replay / replay regression gate`
   - Require branches to be up to date: **ON**
   - Require linear history: **ON**
   - Require conversation resolution: **ON**
   - Do not allow bypassing the above settings: **OFF** (Lead emergency-override)
   - Allow force pushes: **OFF**
3. **Add rule** for `dev`:
   - Branch name pattern: `dev`
   - Require a pull request before merging: **OFF** (Lead direct-push OK)
   - Require status checks before merging: **ON**
   - Required status checks: `ci`, `replay`
   - Require branches to be up to date: **OFF**
   - Allow force pushes: **ON** (specify "Specific people" → Lead only)

## Verify

After applying, test the rules:

```bash
# Should succeed: PR-based merge
git checkout -b feat/test-protection
git commit --allow-empty -m "test"
git push -u origin feat/test-protection
gh pr create --base dev --head feat/test-protection --title test --body test

# Should FAIL: direct push to main
git checkout main
git commit --allow-empty -m "test direct push"
git push origin main
# Expected: ! [remote rejected] main -> main (protected branch hook declined)
```

## Re-apply after rule drift

If somebody disables a rule via the UI, re-run the `gh api` command —
it's idempotent and resets to the documented state. The JSON in this
file is the source of truth for the rule shape.

## Related

- [`docs/setup/dev-environment.md`](dev-environment.md) — how the dev
  MAX-bot + dev platform instance is set up (DRF-891 tasks 1, 5, 6)
- CLAUDE.md § Git workflow — operator-facing rules
- `.github/workflows/deploy.yml` — what triggers on `main` push
- `.github/workflows/deploy-dev.yml` — what triggers on `dev` push
