# PR #842 — PII Tokenizer Investigation

**Date:** 2026-06-01
**Auditor:** general-purpose agent (background)
**Subject:** `feat([phase0/epsilon]): PII tokenization at LLM exit boundary (Tier-A #1 P0)`
**Branch:** `phase0/epsilon/pii-tokenizer` (commit `33d725d`, single commit)
**Status at audit time:** OPEN DRAFT, last updated 2026-05-26T19:06Z (5 days stale)

---

## Summary verdict

**Completeness:** **~85% library-side complete / ~40% integration complete.**
Tokenizer + decorator + tests + ADR are written to production quality, but
the activation contract (`with pii_context(conversation_id): ...`) is
**never invoked anywhere in the codebase** — no skill, no orchestrator,
no handler sets the conversation scope. With this PR merged as-is, the
decorator runs in pass-through mode на every LLM call, logs a WARNING,
and zero tokenization actually happens. Library is shipped; the wire is
loose.

**Stall reason (most likely):** CI red on mypy (4 type errors in 2 test files)
PLUS the W2 stream pivoted к Tier-A #3 (#924) and Tier-A #4 (#894) without
finishing the integration step. Per `feedback_ci_fail_fast_masks_mypy`,
ruff/mypy stage failure prevents pytest from running, masking whether
the 47 PII + 184 LLM suite advertised в the PR body actually passes. The
errors are 10-15 min fixes (type narrowing on `_FakeRedis` fixtures and
generator-return annotations) — so the stall is not technical difficulty;
it's «handed off без a closer» combined с the cross-stream review
window (29-31 May) that the PR explicitly blocks on.

**Recommendation:** **Path A — complete the draft** (with one critical
addition: actually wire `pii_context` at the orchestrator/skill boundary,
otherwise the PR ships dead code и compliance evidence is illusory).

**Effort to ship:** **6-10 hours** of agent work (see Path A below).

**Pre-pilot blocker?** **YES** — 152-ФЗ §6 transit pseudonymisation для
external LLM vendors is the founder-locked Tier-A #1 entry в pilot
scope discipline (memory: `project_ai_concierge_doc_extracts` Tier-A #3
PII tokenization). Pilot deploys 5-10 Penza salons с real customer phone
numbers and без a pseudonymisation layer, OpenAI/Anthropic see raw PII
in every prompt — regulator-relevant и legal-audit-prep-blocker.

---

## Branch inventory

* **Commits:** 1 (`33d725d`, author `AndreyDeveloper84` / Co-Authored-By
  Claude Opus 4.7, 2026-05-26T22:04 +0300).
* **Files changed:** 7 (3 new code, 2 new test, 1 modified code, 1 doc).
* **LOC:** +1829 / −18.

| File | Status | LOC |
|---|---|---|
| `apps/llm/pii_tokenizer.py` | NEW | +452 |
| `apps/llm/pii_protected_provider.py` | NEW | +274 |
| `apps/llm/router.py` | MODIFIED | +14 / −4 |
| `apps/llm/tests/test_pii_tokenizer.py` | NEW | +448 |
| `apps/llm/tests/test_pii_protected_provider.py` | NEW | +516 |
| `apps/llm/tests/test_router.py` | MODIFIED | +28 / −14 |
| `docs/adr/ADR-0011-user-personal-context-privacy.md` | MODIFIED §11.4 | +115 |

---

## PR description analysis

PR body is exemplary: 6 locked Phase B verdicts (D1–D6), Phase G adversarial
defence (NONCE format), audit row contract, 47-test breakdown, severity
classification (P0 PRE_PILOT 152-ФЗ), and 6-item reviewer checklist для
W3 cross-stream pass 29-31 May. Closes «#85» (note: GH issue #85 in this
repo is an unrelated MERGED Sprint 9 PR — the «#85» reference points
к internal task tracker, not the GH issue numbering. Memory
`feedback_tech_lead_task_terminology` flagged this exact ambiguity 2026-05-26).

Scope notes:
* «Draft mode pending W3 cross-stream adversarial pass 29-31 May per
  `feedback_h3_waiver_pattern` empirical N=4.» — W3 review window is
  acknowledged but no review record exists in the PR (`reviews: []`,
  `reviewRequests: []`).
* «Tool-call arguments NOT tokenized — documented Phase 0 gap.» — accepted limitation.

---

## CI status

`pytest + ruff + mypy` workflow: **FAILURE** (2026-05-26T19:07Z, exit 1
on mypy stage).

Mypy errors (4, in 2 files):

```
apps/llm/tests/test_pii_tokenizer.py:101  error: Incompatible types in assignment (expression has type "str | int", target has type "str")  [assignment]
apps/llm/tests/test_pii_tokenizer.py:112  error: Argument 3 to "hset" of "_FakeRedis" has incompatible type "str | int"; expected "str"  [arg-type]
apps/llm/tests/test_pii_tokenizer.py:123  error: The return type of a generator function should be "Generator" or one of its supertypes  [misc]
apps/llm/tests/test_pii_protected_provider.py:84  error: The return type of a generator function should be "Generator" or one of its supertypes  [misc]
```

Per `feedback_ci_fail_fast_masks_mypy`: the combined job exits на ruff/mypy
BEFORE pytest runs → the «47/47 PII + 184/184 LLM suite green» claim в
the PR body **was verified locally only**, never re-confirmed by CI.

`replay fixtures` workflow: SUCCESS (golden + adversarial + voice).

---

## Implementation state by file

### `apps/llm/pii_tokenizer.py` — COMPLETE (production-ready)

* 5 regex categories reused from `apps/replay/redactor.py` (PHONE, EMAIL, CC, OTP, URL_TOKEN).
* `pii_context()` `contextmanager` setter for ContextVar-based scope.
* `tokenize()` / `detokenize()` / `clear_conversation()` public API.
* Token format `<{CAT}_{NONCE}_{INDEX}>` с per-conversation 8-hex NONCE seeded
  via Lua `HSETNX` (precedent `apps.workers.ceilings`).
* Russian phone canonicalisation (`+7 ≡ 8`) and email lowercase normalisation.
* Fallback non-atomic path для test fakes without `register_script`.
* Hallucinated tokens log WARNING (no exception).
* TTL = `SHORT_TERM_MEMORY_TTL_SECONDS + 1h grace` (~25h default), refreshed на every write.
* Module docstring documents D1-D6 verdicts inline.

No TODOs / FIXMEs. Code is idiomatic, type-annotated (`Final`,
`Pattern[str]`, etc.), and reuses existing infra.

### `apps/llm/pii_protected_provider.py` — COMPLETE (production-ready)

* `PIITokenizingProvider` wraps any `LLMProvider`; preserves `.name`.
* `complete()`: tokenize all message contents → wrapped → detokenize `result.text`.
* `embedding()`: tokenize input → wrapped → return vector (no detokenize, vectors irreversible).
* Audit emit (`llm.call_completed`) inside the decorator — single emit point;
  payload contains `pii_category_counts` (counts of `<CAT_NONCE_IDX>` matches in tokenized stream),
  never raw user PII. Uses `sync_to_async(write_audit, thread_sensitive=False)` to bridge sync ORM.
* No-scope path: pass-through с WARNING log — explicitly documented в module docstring
  as «policy guidance not hard block» so internal background flows are permitted.
* No TODOs / FIXMEs.

### `apps/llm/router.py` — COMPLETE (integration point wired correctly)

`_load_provider()` wraps every newly-instantiated `OpenAIProvider` /
`AnthropicProvider` в `PIITokenizingProvider(raw_provider)` before caching
в `self._providers[name]`. Future providers auto-protected; single
review surface confirmed.

### `apps/llm/tests/test_pii_tokenizer.py` — PARTIAL (mypy red)

448 LOC, claims 31 tests. Two mypy issues (10-min fix):
* L101/112: `_FakeScript.__call__` signature `args: list[str | int]` leaks
  через dict ops into `_FakeRedis.hset(... value: str)` — narrow с
  `assert isinstance(...)` or split `args` into typed locals.
* L123: `@pytest.fixture` уses `yield` → return type must be
  `Iterator[_FakeRedis]` not `_FakeRedis`.

### `apps/llm/tests/test_pii_protected_provider.py` — PARTIAL (mypy red)

516 LOC, claims 16+5 tests. One mypy issue:
* L84: same `Iterator[_FakeRedis]` annotation fix.

### `apps/llm/tests/test_router.py` — MODIFIED

Adjusted to expect `PIITokenizingProvider` wrap. 14 lines added.

### `docs/adr/ADR-0011-user-personal-context-privacy.md` §11.4 — COMPLETE

115-line «engineering pre-commitment doc для legal audit» section.
Covers why-this-exists, contract scope, enforcement boundary, audit row
shape, limitations (names skipped / addresses skipped / tool-args not
tokenized / crypto-shred boundary deferred), and cross-references.
Suitable for direct citation in a 152-ФЗ compliance brief.

---

## Integration analysis

### Where tokenization wired into LLM call path
`apps/llm/router.py::_load_provider` — every provider is wrapped в the
decorator at instantiation time. This is the correct enforcement point
(future providers auto-protected).

### Where `pii_context()` activation scope is set
**NOWHERE.** `git grep` across the entire branch returns zero
non-test references к `pii_context` outside `apps/llm/`. Skills
(`apps/skills/booking/skill.py`, `apps/skills/faq/skill.py`) and
the orchestrator call `provider.complete(...)` directly without
wrapping в `with pii_context(conversation_id): ...`.

**Net effect if merged as-is:** decorator activates on every call,
sees `current_conversation_id() is None`, logs WARNING `pii_protected_provider.no_active_scope`,
and pass-through-forwards raw user PII к OpenAI/Anthropic verbatim.
**Compliance posture would be cosmetic only.**

### Entity types covered
PHONE, EMAIL, CC, OTP, URL_TOKEN (5 regex categories).
**Names skipped** (regex inadequate для Russian morphology; natasha NER deferred к Phase 1).
**Addresses skipped** (precision too low).
**Tool-call arguments not tokenized** (documented Phase 0 gap).

### Token format + reversal mechanism
`<{CATEGORY}_{NONCE}_{INDEX}>` (e.g. `<PHONE_a8f2c1d4_1>`). Per-conversation
NONCE defends against user-supplied literal-shape attacks. Reverse via
Redis HGET on `rev:{token}` field. Hallucinated tokens (LLM emits an
unknown one) log WARNING and remain in-place.

### Vault storage choice
Redis HASH per `conversation_id`, key `pii_tokenmap:{cid}`. TTL ≈ 25h
(short-term-memory TTL + 1h grace). Explicit `clear_conversation()` для
conversation-close cleanup. **NEVER** persisted to Postgres or audit log.

### Per-tenant vs global
**Per-conversation, not per-tenant.** Cross-conversation tokens never collide
because both NONCE and HASH key namespace are scoped к conversation_id.

---

## Tests state

* Test files present, ~964 LOC across two files.
* PR body claims 47 PII + 184 LLM suite green локально.
* **CI never ran pytest** because mypy failed first (4 errors → exit 1).
* No way to confirm test claims без either fixing mypy or running locally.
* Test design covers: 5-category detection, Russian-name skip lock, idempotent
  same-token, Russian phone canon, per-conversation namespace isolation,
  ephemeral TTL + clear, Lua + fallback paths, NONCE adversarial defence,
  hallucination logged, mutation ignored, prefix collision PHONE_1 vs PHONE_10,
  audit emit + regression guard scanning JSON для raw PII substrings.

---

## Stall diagnosis

**Concrete blockers found:**
1. **CI red on mypy** (4 errors, 2 files) — easy fix but blocks PR ready-flag.
2. **No `pii_context()` activation** anywhere в production code paths.
   Skills and orchestrator never set the scope → decorator inert.
3. **No W3 cross-stream adversarial review** completed (review window
   29-31 May, PR body required it but `reviews: []` and `reviewRequests: []`).
4. **GH-issue cross-reference confusion** — «Closes #85» points к an
   unrelated old PR; internal task #85 vs GH issue #85 collision per
   memory `feedback_tech_lead_task_terminology`.

**Most likely root cause:** W2 (Epsilon) opened the PR в draft mode
explicitly gated on the 29-31 May W3 review window, then between 2026-05-26
and 2026-06-01 pivoted к shipping Tier-A #3 (PR #924, merged 2026-05-30)
and Tier-A #4 (PR #894, merged earlier) — both are P1 and would normally
follow #842 в the priority order. The «pivot then forget» pattern fits
memory `feedback_tau_branch_push_discipline` («docs не push к dev → cross-stream block»).
Adding к the stall: CI fail-fast masks the mypy errors per
`feedback_ci_fail_fast_masks_mypy`, so it's plausible the author saw
local tests green and didn't re-check CI after push.

---

## Three paths

### Path A — Complete the draft (RECOMMENDED)

**Concrete tasks:**

1. Fix 4 mypy errors in `apps/llm/tests/test_pii_tokenizer.py`
   (lines 101/112/123) and `apps/llm/tests/test_pii_protected_provider.py`
   (line 84). Annotation tweaks: `Iterator[_FakeRedis]` for yield-fixtures;
   tighten `_FakeScript.__call__` arg types or assert-narrow inside.
   **Effort: 15-30 min.**

2. **Wire `pii_context()` activation в the skill / orchestrator boundary.**
   Audit call sites of `get_router().get_provider(...).complete(...)`:
   * `apps/skills/booking/skill.py:336` and `:451`
   * `apps/skills/faq/skill.py:191` and `:249` (плюс embedding line 315)
   * Any orchestrator-level call sites (`apps/orchestrator/intent_router.py`,
     `apps/orchestrator/pipeline.py` if it dispatches LLM directly).
   Wrap with `with pii_context(conversation_id): ...`. The conversation_id
   must be the same identifier used downstream by the memory layer (likely
   `conversation.id` or `message.conversation_id` UUID).
   **Effort: 2-4h** (search + 3-6 wrap sites + integration tests confirming
   the scope is active when LLM is called from real skill paths).

3. Add an integration test in `apps/skills/.../tests/` confirming
   `pii_context` is set when skill processes a user turn carrying PII —
   tests should observe `pii_tokenizer.current_conversation_id() is not None`
   inside the skill's LLM call. **Effort: 1-2h.**

4. (Optional but recommended) Add an AST-lint rule в the repo's lint config
   that flags any new `provider.complete()` call NOT wrapped в
   `pii_context()`. Mirrors precedent from `apps/workers/ceilings.py`
   audit lint. **Effort: 1-2h.**

5. Push, re-trigger CI, confirm green, request W3 cross-stream review
   (Phase G adversarial pass per PR body). **Effort: review-window time, ~1-2 days wall.**

**Total agent effort: 6-10 hours.**

**Risks:**
* If `pii_context()` is set too high in the call stack, internal
  background flows may inadvertently get tokenization → benign but adds
  Redis overhead. Mitigation: scope at the per-turn handler level, не at
  the channel/webhook entry.
* If conversation_id isn't a stable UUID across turns (e.g. it's a per-message
  id), the tokenizer's coreference promise breaks. Verify against
  short-term-memory's conversation_id semantics.

### Path B — Fresh restart

**Would scratch be cheaper? NO.**
The library work (~1100 LOC of code + 964 LOC of test) is production-quality:
proper Lua atomicity, ContextVar scope, Russian phone canon, NONCE adversarial
defence, ADR §11.4 legal-audit pre-commitment, audit-row regression guard.
Throwing it away to redo would cost ~30-40h of agent work plus re-derivation
of Phase B/G verdicts. The only thing missing is integration wiring (Path A
task 2) and 4 mypy fixes — both atomic.

### Path C — Split scope

**Minimum viable subset для pre-pilot 152-ФЗ compliance:**
* Ship Path A as-is BUT initially wire `pii_context()` only at the **booking
  skill** call site (the highest-PII surface — clients give phone numbers
  для confirmations). Skip FAQ skill и embedding flows для Phase 0.
* Defer the AST-lint rule (task 4) к post-pilot.
* **Effort: 3-5h.**
* **Deferred features:** AST-lint enforcement, FAQ-skill wiring, embedding-flow
  wiring, tool-call argument tokenization (already a documented Phase 0 gap).

This gives concrete 152-ФЗ evidence для legal audit на the booking flow —
the highest-PII surface — while explicitly carving FAQ + embeddings into
Phase 1. Acceptable если pilot legal posture is risk-tiered by skill;
not acceptable if «no raw PII to vendor ever» is the founder commitment.

---

## Recommendation

**Path A.** The work is 85% done; cost-to-finish (6-10h) is far below
cost-to-rebuild. The critical addition is task 2 — actually invoking
`pii_context()` at the skill boundary — without which the merged PR
ships compliance theater. Pair task 2 с the integration test in task 3
to prove the wire is live. CI mypy fixes (task 1) are trivial. Tasks 4
(AST-lint) and 5 (W3 review) bring it к merge-ready and lock in the
no-regression guarantee.

If pilot deadline pressure forces a triage, Path C (booking-only wiring)
is acceptable as a known-narrower-scope variant — but the W2 stream
должен explicitly file the FAQ + embedding wiring as a P0 follow-up
issue, not as a quiet deferral.

---

## Constraint respected

Read-only audit. No commits, no branch switches, no modifications к
working tree. All inspection performed via `git show origin/phase0/epsilon/pii-tokenizer:<path>` and `gh pr view 842`. Current branch remained `codex` throughout.
