# Developer Agent Workflow — Phase 0 (and beyond)

> **Status:** Active 2026-05-21
> **Applies to:** all developer agents (Phase 0 Streams Alpha/Beta/Gamma + Sprint 1 Track A + Phase 1 future agents).
> **Companion docs:** ADR-0009, parallel-agent runbook, sprint plan, the three Phase 0 startup prompts.
> **Purpose:** Mandatory phase-by-phase regulation for every ticket. Design before code, self-review before Code Reviewer, no shortcuts on commits. Skip a phase only with explicit tech-lead approval.

## Phase A — Understand the ticket

**Before opening an editor.**

1. **A.1** Read the GH issue body in full: `gh issue view <NNN> --repo AndreyDeveloper84/ai-bot-platform`. Capture acceptance checkboxes verbatim.
2. **A.2** Read your stream section in the runbook (`docs/plans/2026-05-20-phase-0-parallel-agent-runbook.md`) — confirm this ticket is yours.
3. **A.3** Read the referenced ADR/sections cited by the issue body (e.g. ADR-0009 §Hard rule #5 for any tool that touches booking).
4. **A.4** Check the GH issue's `Blocked by:` field. If a blocker is open — STOP, don't start. Re-route to a non-blocked ticket from your Week N table.
5. **A.5** If the issue is ambiguous after reading the above, invoke skill `superpowers:brainstorming` — explore intent with the tech lead before any plan. Better to ask now than discover gap mid-PR.

## Phase B — Design before code

**Mandatory for any ticket touching ≥2 files OR introducing new public interfaces.** Skip only for one-line fixes / typo PRs.

1. **B.1** Invoke skill `superpowers:writing-plans`. Output: a numbered task list with:
   - File paths to be created/modified (concrete).
   - Test scenarios per acceptance checkbox.
   - Migration strategy (if DB touched) — **two-step where possible** (additive first, drop later).
   - Failure modes considered (what breaks if this PR is bad).
2. **B.2** For high-risk tickets (schema migrations, payment flow, security boundaries, event contracts), additionally invoke the **Plan** sub-agent (`Agent` tool with `subagent_type=Plan`) for an architectural-grade second pass on the plan. Output goes to the PR body as the "Plan" section.
3. **B.3** Save the plan inline in your scratchpad OR as `docs/plans/phase-0/<stream>/<ticket>-plan.md` if the tech lead asks for traceability. Default: inline.
4. **B.4** Re-read ADR-0009 hard rules #1–#7. Confirm your plan does not violate any:
   - No duplicate canonical state.
   - No direct cross-repo DB access.
   - No new MVP features merge during Phase 0.
   - bot-platform does not grow new transactional domains.
   - Transactional tools = REST wrappers (bot-platform never DB-writes booking/payment/catalog).
   - `tenant_id` claim = `active_tenant_id`; verify `TenantUserRelationship`.
   - Every event has `event_version`; consumers idempotent.

## Phase C — Pre-implementation setup

1. **C.1** Sync: `git fetch origin && git checkout dev && git pull` in the repo your ticket targets.
2. **C.2** Branch: `git switch -c phase0/<stream>/<NN>-<slug>` — naming exactly per runbook §Hard rules.
3. **C.3** If the branch checkout misbehaves (this repo has worktree-related race conditions, memory `parallel-agent-branch-race`), accept detached HEAD; later push via `git push HEAD:refs/heads/<branch>`.
4. **C.4** **Selective staging rule starts now.** From this point you NEVER run `git add .`, `git add -A`, or `git commit -a`. Always name specific files. The user has parallel WIP in their working tree that must not get bundled into your commits.
5. **C.5** Confirm your anti-touch list one more time. If your plan in B.1 lists a file outside your stream's roots — STOP and ask.

## Phase D — Implementation (TDD-first)

**For any ticket with testable behaviour (most of them).**

1. **D.1** Invoke skill `superpowers:test-driven-development`. The discipline is:
   - Write failing test → run → confirm RED.
   - Implement minimum code → run → confirm GREEN.
   - Refactor with tests passing.
   - Move to next acceptance checkbox.
2. **D.2** Test location follows repo convention: `apps/<app>/tests/` in ai-bot-platform; `<app>/tests.py` or `<app>/tests/` in Ayla djangoproject.
3. **D.3** For event consumers (#442–#446) — write the idempotency test BEFORE the consumer body. Same event 3× → exactly one side-effect.
4. **D.4** For migrations (#420, #424, #426, #439) — write the migration test BEFORE writing the migration. Test both forward and reverse.
5. **D.5** Do NOT write end-of-implementation "polish" code that isn't on the acceptance list. Phase 0 freeze applies: if it's not in the ticket, it's out of scope.

## Phase E — Self-review (BEFORE asking Code Reviewer)

**Cheaper iteration: catch your own slips here, save a full Code Reviewer cycle.**

1. **E.1** Run the test suite for the affected scope: `pytest apps/<your-app>` (bot-platform) or `python manage.py test <your-app>` (Ayla). Must be green.
2. **E.2** Run linters / type checks where the repo has them configured:
   - bot-platform: `ruff check`, `mypy` (per `pre-commit` config).
   - Ayla djangoproject: per its (forthcoming) `settings/test.py` setup.
3. **E.3** Run `git diff --staged` and read it line by line. Ask yourself: did I edit anything outside my stream's roots? If yes — STOP, unstage, restart Phase C.5.
4. **E.4** Invoke skill `simplify` — finds dead code, repeated logic, unnecessary abstractions. Apply or document why you kept the complexity.
5. **E.5** Invoke skill `superpowers:verification-before-completion` — re-read the GH issue's acceptance checkboxes and verify each one factually (not vibes). If you can't check a box truthfully — back to Phase D.
6. **E.6** For destructive or hard-to-reverse work (force push, drop table, deleting files, schema downgrades) — invoke `careful` or `guard` skill BEFORE running the command.

## Phase F — Commit

1. **F.1** Stage selectively: `git add <file1> <file2>` — name every file.
2. **F.2** Commit message structure:
   - Subject: `[phase0/<stream>] <type>(<scope>): <subject>` (Conventional Commits).
     - Types: `feat`, `fix`, `refactor`, `chore`, `docs`, `test`, `build`, `ci`.
     - Scopes: app name (`events`, `orders`, `tenancy`, `payments`, `kb`, `auth`, etc.).
   - Body: explain WHY, not WHAT (per CLAUDE.md). Reference issue (`Closes #NNN.`), reference ADR sections, name the failure mode you prevent.
   - Footer trailer: `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>` for AI-assisted commits.
3. **F.3** **Never `git commit --amend` on a published commit.** If the pre-commit hook fails: fix the root cause (the failing check, not the check itself), re-stage, create a NEW commit. Per memory feedback: amend after hook failure can lose work because the original commit didn't actually happen.
4. **F.4** **Never bypass hooks.** Do not use `--no-verify`, `--no-gpg-sign`, `-c commit.gpgsign=false` unless tech lead explicitly authorizes. If a hook is wrong, fix the hook in a separate PR; don't ship around it.
5. **F.5** Confirm via `git log --oneline -1` that the commit landed on YOUR branch, not on `dev` or detached.

## Phase G — Push + open PR

1. **G.1** Race-safe push: `git push origin HEAD:refs/heads/phase0/<stream>/<NN>-<slug>`. The `HEAD:refs/heads/...` form works even from detached HEAD (memory `parallel-agent-branch-race`).
2. **G.2** Open PR: `gh pr create --base dev --title "[phase0/<stream>] #<NN> <short desc>" --body "..."`.
3. **G.3** PR body MUST include:
   - One-line `## Summary`.
   - Acceptance checkbox list mirrored from the GH issue (so reviewer can verify item-by-item).
   - `## Test plan` — what was run and what was observed (not generic templates).
   - `## Out of scope` — what intentionally NOT done.
   - `Closes #<NNN>.`
4. **G.4** **PR target is `dev`. NEVER `main`.** Memory `feedback_pr_base_branch`.

## Phase H — Code Reviewer (MANDATORY)

1. **H.1** Immediately after opening PR, dispatch the **Code Reviewer** sub-agent (`Agent` tool with `subagent_type=Code Reviewer`). Memory `feedback_pr_workflow_code_reviewer` — non-negotiable on every PR diff.
2. **H.2** Prompt template:
   ```
   Review PR #<NNN> in AndreyDeveloper84/ai-bot-platform (branch
   <branch>, against dev). <One sentence on what this PR does.>
   Verify: (1) acceptance criteria met, (2) no anti-touch violations
   per runbook §Stream <X>, (3) ADR-0009 hard rules respected, (4)
   tests cover failure modes, (5) any silent failure modes I missed.
   ```
3. **H.3** For HIGH-RISK PRs (schema migrations, payment lifecycle, security boundaries, event contracts) — also invoke skill `codex` (`/codex review`) as an independent second opinion. Reserve for risk; don't waste on every commit.
4. **H.4** Wait for verdict:
   - `Approve` → proceed to Phase I.
   - `Approve with comments` → decide which to fix in this PR vs file a follow-up issue. Fix here only if 5-line or smaller; otherwise follow-up.
   - `Request changes` → back to Phase D for the flagged items. Re-loop through E → F → G → H.
5. **H.5** Invoke skill `superpowers:receiving-code-review` to discipline how you respond to feedback. Resist the urge to argue; default to incorporating.

## Phase I — Pre-merge gates

1. **I.1** CI green: `gh pr checks <NNN>` — all checks pass.
2. **I.2** If your PR depends on a Sync point (per runbook): confirm Sync N is acknowledged in your stream window. Without that ack, merge violates dependency order.
3. **I.3** No merge conflicts with `dev`: `gh pr view <NNN> --json mergeable` returns `MERGEABLE`.
4. **I.4** Branch is up-to-date with dev (rebase or merge dev → branch if drift).

## Phase J — Merge + cleanup

1. **J.1** Merge: `gh pr merge <NNN> --merge` (or `--squash` if PR has many noisy commits — default is `--merge` to preserve atomic commits).
2. **J.2** Invoke skill `superpowers:finishing-a-development-branch` to close out: delete local branch, archive scratchpad notes, confirm GH issue auto-closed by the `Closes #NNN` trailer.
3. **J.3** **Closure summary** as comment on the closed GH issue: one paragraph — what shipped, what didn't, follow-up issues opened (if any), Sync handshake (if applicable).
4. **J.4** **End-of-day status post** in your stream window (Alpha/Beta/Gamma) per runbook:
   ```
   Closed today: #X, #Y.
   In progress: #Z (status, % done).
   Blocked: #W (reason).
   Tomorrow: #A, #B.
   ```
5. **J.5** If your merge unblocks a Sync handshake (e.g. you just landed #441 event-contract.md) — **explicit announcement** in your window per runbook §Sync handshakes. Tech lead in main window propagates to other stream windows.

## Special cases

### When debugging a regression / bug

1. Invoke skill `superpowers:systematic-debugging` (and `investigate` skill if structural).
2. **Iron Law: no fixes without root cause.** Don't patch a symptom without proving the cause.
3. Add a regression test FIRST that demonstrates the bug — fail → fix → green.
4. Commit message body explains the root cause + why the fix is the minimal correct one.

### When the ticket forces you across an anti-touch boundary

1. STOP. Do not edit.
2. Post in your stream window:
   ```
   HALT: ticket #<NN> requires editing <path>, which is <other stream>'s
   territory per runbook §<Stream Y>. Need ruling: do I (a) coordinate
   with <other stream> via Sync, (b) re-scope my ticket, or (c) hand
   off to them?
   ```
3. Tech lead in main window decides.

### When pre-commit hook fails

1. Read the failure carefully. The hook ran and the commit did NOT happen.
2. Fix the root cause (the failing check, in your code).
3. Re-stage with `git add <files>`.
4. Create a NEW commit (not `--amend` — the previous commit doesn't exist).
5. If the hook is broken (false positive), fix the hook in a separate `chore(precommit): ...` PR.

### When facing schema migrations

1. Two-step pattern: additive first (new column nullable), then in a follow-up PR or migration, switch reads/writes, then drop the old. Never big-bang.
2. Test both forward and reverse migration.
3. For Ayla `Payment` refactor (#426): create new `payments.Payment`, add FK on `Appointment`, data migration moves rows, switch reads, switch writes, only later drop old. Run booking + pay + cancel + refund smoke after each step.

### When facing hard-to-reverse / destructive actions

1. Invoke `careful` or `guard` skill.
2. Confirm with tech lead before:
   - `git push --force`, `git reset --hard` on shared branches.
   - `DROP TABLE`, `DELETE FROM` without `WHERE`.
   - `rm -rf` on anything outside `/tmp/` or your own scratch directory.
   - Any change to CI/CD pipelines or production secrets.

### When facing high-risk PRs (security, payment, migration, event contract)

1. Phase B.2: invoke **Plan** sub-agent.
2. Phase D.5: extra failure-mode coverage in tests.
3. Phase H.3: invoke `codex` for independent second opinion.
4. Phase J.5: detailed closure summary including rollback procedure if regression detected post-merge.

## Anti-patterns (never do these)

- ❌ Commit straight to `dev` or `main`.
- ❌ `git add .` or `git add -A` or `git commit -a` in a worktree that has other agents' WIP.
- ❌ `--no-verify` to bypass hooks.
- ❌ `--amend` on a commit that has been pushed.
- ❌ Force-push to a shared branch without explicit tech-lead approval.
- ❌ Edit a file in another stream's anti-touch list (even one line).
- ❌ Implement a consumer for an event before the event-contract.md spec for it lands (Phase 0 Bucket 7).
- ❌ Merge a PR that has unresolved `Request changes` Code Reviewer findings.
- ❌ Claim a ticket done without verifying each acceptance checkbox factually.
- ❌ Patch a symptom without finding the root cause.
- ❌ Skip the end-of-day status post.

## TL;DR cycle for a typical ticket

```
A.1 read GH issue
A.5 brainstorm if ambiguous
B.1 write plan (skill: writing-plans)
B.2 Plan sub-agent if high-risk
C.1 sync repo, C.2 branch, C.4 confirm selective staging
D.1 TDD: failing test → implement → green → refactor
E.1-E.5 self-review (tests, lint, diff, simplify, verify)
F.1-F.5 selective commit, conventional message, never amend pushed
G.1-G.4 race-safe push, PR against dev, full body
H.1 dispatch Code Reviewer (MANDATORY)
H.3 dispatch codex if high-risk
H.4 incorporate findings, loop if needed
I.1-I.4 CI green, sync acked, no conflicts
J.1 merge
J.2-J.5 cleanup, closure summary, status post, Sync handshake
```

10 phases. Skip a phase only with tech-lead authorization recorded in this window.

## Loading this regulation into agent context

Each Phase 0 startup prompt (Alpha/Beta/Gamma) currently lists ad-hoc procedural rules. To load this regulation:

1. Tech lead announces: "Workflow regulation v1 at `docs/plans/2026-05-21-developer-agent-workflow.md` — read in full as part of your boot sequence."
2. Future startup-prompt revision will reference this doc explicitly in the "Read these documents first" list.

Until then, the existing prompts remain authoritative on stream-specific anti-touch lists and Week 1 ticket order; this regulation extends them with the universal phase-by-phase discipline that applies to ALL streams.
