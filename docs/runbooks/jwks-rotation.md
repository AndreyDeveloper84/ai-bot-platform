# Runbook: JWKS key rotation drill (Ayla ⇄ bot-platform)

> Status: **draft**
> Last exercised: _never_
> Target completion sprint: pre-pilot 2026-07-15 (issue #565 / NS2)
> Owner: Security stream (S2) → on-call at rotation time

## Purpose

Rotate an RS256 signing keypair on either side of the JWT contract
(Ayla issuer key A, or bot-platform service-to-service key B) **without a
401 outage**. The contract requires every rotation to ride a
dual-`kid` coexistence window so the verifying side never rejects a
token signed by a key it has not yet cached.

This runbook is the **planned-rotation SOP** referenced by
[`docs/architecture/jwt-contract.md`](../architecture/jwt-contract.md)
§4.3 + §4.4. For a *compromise-driven* emergency rotation, this runbook's
timing is deliberately inverted — see
[Emergency rotation](#emergency-rotation-compromise) below and
[`security-incident.md`](security-incident.md).

> **Scope note (ADR-0009).** bot-platform is the **verifier** side of the
> Ayla→bot-platform contract and the **publisher** of its own key B.
> The Ayla-side signing-key mechanics (key A storage in Vault, the
> `GET /.well-known/jwks.json` issuer endpoint) are **Ayla-canonical** and
> live in the Ayla djangoproject — this runbook documents the
> cross-repo *coordination* and the bot-platform-side verification +
> telemetry, it does **not** own the Ayla key store. Steps that mutate
> Ayla state are run by the Ayla on-call, flagged **[AYLA-SIDE]** below.

> **Blocked-by note.** The bot-platform-side concrete commands (JWKS
> cache-hit/miss telemetry queries, the `/.well-known/jwks.json`
> publisher for key B) depend on the JWT verifier middleware landing
> (#258 foundation + #569). Until then, the **procedure, timing, and
> checklist are authoritative**; the exact `manage.py` invocations marked
> _PENDING #258_ are placeholders to be filled when the middleware ships.

## Trigger / when to run

- **Quarterly planned rotation** — new `kid` (e.g. `ayla-2026-Q3`) created
  14 days before cutover per §4.3. This runbook covers the **cutover day**
  procedure, after the new key has already been published in JWKS.
- **Manual / ad-hoc planned rotation** — e.g. rotating key B because an
  operator left, or a precautionary rotation that is *not* a live
  compromise (a live compromise → [Emergency rotation](#emergency-rotation-compromise)).
- **NOT for**: a confirmed private-key leak. That path drops the old
  `kid` in ≤60s and accepts the brief outage — see security-incident.md.

## Prerequisites

- **Access:**
  - **[AYLA-SIDE]** Ayla secret manager (Vault) access for key A; the
    Ayla djangoproject deploy pipeline.
  - bot-platform secret manager access for key B; bot-platform deploy
    pipeline; read access to bot-platform observability dashboards.
- **State pre-checks (must be true before T=0):**
  - The **new `kid` is already published** in the rotating side's
    `/.well-known/jwks.json` alongside the old `kid` (both keys live).
  - The new `kid` has been published for **≥10 minutes** so every
    verifier has had at least one 5-minute cache-refresh cycle to see it
    (§4.4: inverse side's 5-min cache TTL + 5-min safety margin). This is
    the **pre-rotation checklist gate** — do not start signing-cutover
    before it passes.
  - JWKS cache-hit/miss telemetry is being scraped on the verifying side
    and the miss-rate alert is armed (see [Verification](#verification)).
  - A staging rehearsal of this exact procedure has completed cleanly
    within the current rotation cycle (see Step 0).
- **Communication:** post in `#ops` + `#security` *before* T=0 with the
  rotation window, the old/new `kid`, and which side is rotating. Ping
  both Ayla on-call and bot-platform on-call.

## Step-by-step procedure

### Step 0 — Staging rehearsal (one-time per rotation cycle, BEFORE prod)

Run the **entire** cutover on staging first, end-to-end, and confirm no
401 spike for 30 minutes post-cutover. Same key-management commands, same
restart pattern, same verification queries. A rehearsal that skips the
verification window does not count.

```sh
# [AYLA-SIDE] (staging) publish new kid alongside old, then after the
# 10-min coexistence gate, cut signing over to the new kid.
# bot-platform (staging): confirm verifier accepts BOTH kids during the
# window and the cache force-refresh fires on first unseen-kid token.
```

Gate: staging shows **zero** `jwt_verify_failed{reason="kid_not_found"}`
beyond the first cache-refresh cycle, for 30 minutes. If not → stop, fix,
re-rehearse. Do **not** proceed to prod.

### Step 1 — Confirm dual-`kid` coexistence gate (T−10min)

Verify both keys are live in the rotating side's JWKS and the new `kid`
has been published ≥10 minutes.

```sh
curl -s https://<rotating-side>/.well-known/jwks.json | jq '.keys[].kid'
# Expect BOTH the old and new kid in the output.
```

Decision branch: if only one `kid` is present, or the new `kid` has been
up <10 min → **abort**, wait out the window, restart at Step 1. Signing
cutover before the gate = guaranteed verifier 401s on the cache-stale
inverse side.

### Step 2 — Signing cutover (T=0)

The rotating side switches to signing **new** tokens with the **new**
`kid`. The old `kid` stays published in JWKS but signs nothing new.

```sh
# [AYLA-SIDE] for key A rotation — flip Ayla's active signing kid:
#   (Ayla-canonical command — run by Ayla on-call)
#
# For bot-platform key B rotation — flip the s2s signing kid:
#   PENDING #258/#569 — e.g. manage.py rotate_s2s_signing_key --activate <new-kid>
```

Record the exact cutover timestamp (ISO 8601 UTC) — it arms the
post-cutover verification window.

### Step 3 — Hold the coexistence window (T=0 → T+10min)

Both `kid`s remain in JWKS. The verifier MUST accept tokens signed by
**any** `kid` currently in JWKS (never "only the latest"). Do nothing but
watch telemetry (see [Verification](#verification)).

### Step 4 — Drop the old `kid` (T+10min)

Only after the full 10-minute window — every consumer has had ≥1
cache-refresh cycle — remove the old `kid` from JWKS.

```sh
# [AYLA-SIDE] key A: remove old kid from Ayla JWKS (Ayla-canonical).
# bot-platform key B: PENDING #258/#569 — manage.py retire_s2s_signing_key <old-kid>
curl -s https://<rotating-side>/.well-known/jwks.json | jq '.keys[].kid'
# Expect ONLY the new kid now.
```

For **planned** rotation the old `kid` may legitimately linger up to the
§4.3 14-day overlap (refresh tokens cross rotation boundaries); the
10-minute minimum is the *floor* for the verifier-cache safety, not a
mandate to drop at exactly T+10.

## Verification

How to confirm the rotation did **not** cause a verification outage.

- **JWKS cache hit/miss telemetry (bot-platform side, NS2 item 2):**
  miss-rate should show one brief, expected bump as caches refresh onto
  the new `kid`, then return to baseline. A **sustained** miss-rate spike
  = verifiers cannot find the signing `kid` = misconfigured rotation.
  - _PENDING #258_: metric `jwks_cache_fetch_total{result="hit|miss"}`
    and `jwt_verify_failed_total{reason="kid_not_found"}`.
  - **Alert**: page if `kid_not_found` > 0 sustained for >1 cache TTL
    (5 min) after T=0, or if JWKS miss-rate stays elevated >10 min.
- **No 401 spike:** the inverse side's `401` rate for
  `s2s_*` / token-verification reasons stays flat through T=0 → T+10min.
- **Time-to-stable target:** all verification indicators back to baseline
  within **10 minutes** of cutover (one cache-refresh cycle + margin).

If a sustained `kid_not_found` spike appears: **roll back** by re-publishing
the old `kid` (it was still live until Step 4) and re-cutting signing to
the old `kid`, then investigate before retrying.

## Emergency rotation (compromise)

This is the **inverted** path — do NOT use the 10-minute coexistence
window. Per §4.3 emergency rotation:

- Immediate new `kid`; **old `kid` removed from JWKS within 60 seconds.**
- Accept the worst-case ≤5-minute window where tokens forged with the
  leaked key remain valid (until verifier caches expire).
- Trigger the §7 refresh-token blacklist: mass-blacklist all refresh
  `jti` issued before the compromise window.
- This runbook's planned-rotation timing does NOT apply; follow
  [`security-incident.md`](security-incident.md) and treat the brief
  401 window as acceptable collateral vs. continued exposure.

## Escalation contacts

| Severity | Who | How to reach |
|---|---|---|
| P0 — verification outage (sustained 401/`kid_not_found` spike) | bot-platform on-call + Ayla on-call jointly | `#ops` page + Telegram critical alert |
| P0 — suspected key compromise mid-rotation | Security stream (S2) + tech lead | `#security` + security-incident.md |
| P1 — rotation drill anomaly on staging | Security stream (S2) | `#security` |
| Vendor — Vault / secret-manager outage | Platform Lead | per on-call.md vendor table |

## Post-mortem template

Used after every non-trivial run (and every emergency rotation).

- **What happened.** (planned or emergency; which side; old→new `kid`.)
- **What was the trigger.**
- **What did we expect — what actually happened.** (miss-rate curve, any 401s.)
- **How long did it take to detect / mitigate / resolve.**
- **What we learned.**
- **Action items** (owner + deadline).

## Changelog

- _2026-06-03_ — S2 security stream — initial draft (#565 / NS2 item 1).
  Procedure, dual-`kid` timing, and pre-rotation checklist authoritative;
  bot-platform-side telemetry commands marked _PENDING #258_ until the JWT
  verifier middleware + `/.well-known/jwks.json` publisher (#569) land.
