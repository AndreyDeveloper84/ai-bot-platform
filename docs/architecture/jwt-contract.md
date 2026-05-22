# JWT Contract — Ayla djangoproject (issuer) → ai-bot-platform (verifier)

> **Status:** v1.2 — round-3 amendments 2026-05-22 (addresses 6 NEW NS blockers from PR #523 adversarial pass N=9; chain framing revised — reshaped not severed)
> **Owner:** Phase 0 Stream Beta (W4)
> **Authority:** ADR-0009 §Hard rule #6 — this doc is the wire spec for the «`tenant_id` claim = `active_tenant_id`» rule the ADR establishes.
> **Sibling spec:** [`docs/architecture/event-contract.md`](./event-contract.md) — same pattern (issuer is Ayla, consumer is bot-platform), different transport (HTTP request header instead of HMAC-signed POST).
> **Scope:** Cross-service authentication + authorization tokens issued by **Ayla djangoproject** and verified by **ai-bot-platform**. Mobile Expo apps obtain tokens from Ayla and present them to both backends via the unified API gateway. Internal in-process Django auth on either repo is out of scope.
> **Audience:** Auth implementer (Ayla djangoproject — issuer side, #258 OAuth callback + #257 anonymous session), token-verification middleware author (bot-platform — verifier side, in `apps/identity` scope), API Gateway operator.
> **PREREQUISITE for:** #258, #257, #259 (anonymous-to-registered merge), and any bot-platform endpoint that requires authenticated user context.

---

## 1. Purpose

Per ADR-0009 (Variant A — split-domain architecture), **canonical User identity + PII live in Ayla djangoproject**. Ayla owns auth (OTP, social, anonymous), issues JWTs, and rotates refresh tokens. ai-bot-platform never stores credentials, never issues tokens — it only verifies the tokens Ayla issues, looks up the active tenant relationship, and applies its tenancy-scoped data access.

Without a locked JWT contract:

- bot-platform middleware would invent its own claim names (e.g. `tenantId` vs `tenant_id`), diverging from Ayla → 401 storm.
- Token signing-algorithm confusion (HS256 vs RS256) is a known attack class — locking RS256 + JWKS is the only safe path for cross-service verification.
- Anonymous-JWT scope (per Epic #223 anonymous-to-registered gate) needs explicit boundary: which endpoints accept anonymous tokens, which require registered tokens.
- Refresh-token rotation policy must be agreed (lifetime, blacklist on logout) so both sides treat «valid refresh» the same way.

This doc locks the wire format, claim semantics, signing, verification flow, and failure modes so the two backends produce a coherent auth experience.

**The contract is asymmetric:** Ayla issues + signs; bot-platform verifies + looks up tenant relationship. bot-platform never issues tokens; if it needs a service-to-service call to Ayla, it uses an internal-service JWT (§5.4) signed by a separate key.

### 1.1 Token presentation channel (S11 fix)

**Tokens MUST travel in the `Authorization: Bearer <jwt>` HTTP header. ONLY.**

Verifiers (Ayla djangoproject + bot-platform + the API Gateway):

- MUST extract the token from `Authorization: Bearer ...` only.
- MUST reject (HTTP 400 `invalid_token_location`) tokens presented in:
  - URL query string (`?access_token=...`, `?token=...`, `?jwt=...`) — gets logged by NGINX, mobile crash reporters, browser history.
  - POST body parameters — same logging risk + leaks to error trackers.
  - Custom non-standard headers (`X-Token`, `X-Auth-JWT`, etc.) — bypass standard middleware sanitization.
  - Cookies (no cookie auth in this contract).

Anonymous JWTs are 30 days lived; leakage via URL is a long-lived credential exposure. The header-only rule applies uniformly across all four token types (§2) — for HTTP requests.

### 1.2 WebSocket / SSE / long-poll exception (NS5 fix)

Bearer-only (§1.1) is correct for HTTP. **For streaming protocols (WebSocket upgrades, Server-Sent Events, long-polling chat connections), browsers cannot set the `Authorization` header on cross-origin upgrade requests.** Without an exception, ai-bot-platform's chat streaming endpoints are silently broken under §1.1, OR the codebase grows ad-hoc exceptions outside this contract → unreviewed security escape.

Two explicit, allowed mechanisms for WebSocket auth:

1. **`Sec-WebSocket-Protocol` subprotocol token** — the client sends `Sec-WebSocket-Protocol: bearer.<base64url(jwt)>` during the upgrade handshake. The server extracts the JWT from the subprotocol value, verifies per §8, and accepts the upgrade by echoing the protocol name. **Pros:** standard mechanism, browser-supported. **Cons:** subprotocol header IS logged by some reverse proxies — minimize by configuring NGINX to drop the header from access logs (runbook entry).
2. **Short-lived ticket exchange** — client first makes a normal Bearer-authed HTTP POST to `POST /api/v1/streaming/ticket` and receives a single-use, 10-second-lived ticket. Client opens the WebSocket with `?ticket=<value>` in the URL — this IS logged but the ticket is dead after 10s and one use. Server-side `streaming_tickets` table tracks consumption.

**Choice for MVP (recommended):** option 2 (ticket exchange) — clearer auth boundary, no JWT-in-subprotocol-header logging concern, simpler to audit. Option 1 reserved for if a streaming partner needs the standard subprotocol mechanism.

Neither mechanism exposes the long-lived JWT in URL. Both mechanisms work with anonymous JWTs (the ticket exchange is a normal Bearer-authed POST, so anonymous tokens that can do anonymous chat can request a ticket).

---

## 2. Token taxonomy

Four distinct token types travel the wire. Each has a different lifetime, scope, and refresh story.

| Token type | Issued by | Lifetime | Used for | Refresh? |
|---|---|---|---|---|
| `access` | Ayla djangoproject | **15 minutes** | Authenticated mobile + bot-platform calls | Yes — via `refresh` |
| `refresh` | Ayla djangoproject | **90 days** | Acquiring a new `access` token | Rotates on use (blacklist after rotation) |
| `anonymous` | Ayla djangoproject | **30 days** | Mobile Mini App browse-only flow (per Epic #223) | No — replaced on user OAuth |
| `service_to_service` | bot-platform OR Ayla | **5 minutes** | Internal REST calls between backends | No — new token per request |

The first three are user-facing tokens travelling on mobile and chat channels. The fourth is internal infrastructure plumbing (cross-repo REST calls) and uses a separate key.

---

## 3. JWT envelope (claims schema)

Every token MUST conform to the envelope below. Claim names match `python-jose` / `PyJWT` / `simplejwt` conventions where possible (`sub`, `exp`, `iat`, `iss`, `aud`, `jti`). Ayla-specific claims live under the explicit `ayla` namespace prefix.

### 3.1 Standard claims (RFC 7519)

| Claim | Type | Required | Description |
|---|---|---|---|
| `iss` | string | yes | Issuer: `"https://api.ayla.app"` (or whatever the canonical Ayla auth host is — currently `api-dev.gobeauty.site` per DNS deferral, see ADR-0009 §Mobile API split). Frozen across token rotation. |
| `sub` | string (UUID) | yes | Subject: canonical Ayla `User.id`. For `anonymous` tokens, sub = `AnonymousSession.id` (different UUID space — see §6). |
| `aud` | string array | yes | Audience. **One element per token issuance — NOT a multi-target dual-audience array.** (S1 fix.) For an access token usable at bot-platform: `aud: ["ai-bot-platform"]`. For an access token usable at Ayla's own endpoints: `aud: ["ayla-djangoproject"]`. Mobile holds BOTH tokens (one per backend) and presents the right one per request based on routing. The API Gateway MAY strip+reissue tokens at the routing layer if a unified-mobile-storage model is required — design tradeoff for the gateway implementer. Verifiers reject tokens whose `aud[0]` ≠ their service name. |
| `exp` | integer (unix timestamp) | yes | Expiration. Hard cap per token type (§2). |
| `iat` | integer (unix timestamp) | yes | Issued-at. |
| `nbf` | integer (unix timestamp) | yes | Not-before. Equal to `iat` in practice. |
| `jti` | string (UUID) | yes | Unique token ID. Used for blacklist lookup on refresh rotation + revocation. |

### 3.2 Ayla namespace claims

All Ayla-specific data sits under the `ayla` claim as a nested object, so future changes don't collide with future RFC reservations.

| Claim path | Type | Required | Description |
|---|---|---|---|
| `ayla.token_type` | enum string | yes | One of: `access`, `refresh`, `anonymous`, `service_to_service`. Verifier rejects unknown values. |
| `ayla.tenant_id` | string (UUID) \| null | **yes (presence required)** | **Active** tenant context for THIS request, NOT permanent ownership. `null` MEANS «global user scope, not tenant-scoped» — used for memory layer queries per ADR-0009 §Hard rule #6. See §5 for full semantics. |
| `ayla.relationships` | array of objects | yes (may be empty) | Snapshot of the user's `TenantUserRelationship` rows at issuance time. Each: `{tenant_id, role, granted_at}`. Bot-platform uses this for cheap «is user X allowed in tenant Y» checks without a round-trip to Ayla. NOT authoritative — see §5.2 for the «trust but verify» rule. |
| `ayla.scope` | array of strings | yes | Authorization scope strings, e.g. `["user:profile:read", "memory:write", "booking:create"]`. Verifier checks scope against endpoint requirements. |
| `ayla.user_role` | enum string | yes | One of: `customer`, `master`, `tenant_owner`, `admin`. Drives RBAC at the verifier. |
| `ayla.anon_session_id` | string (UUID) \| null | conditional | Present only when `token_type='anonymous'`. References `AnonymousSession.id` per Epic #223 #255. |
| `ayla.device_id` | string \| null | optional | Mobile device identifier, used for device-binding signals (rate limiting, suspicious-session detection). **Informational only — never use for authorization decisions** (S10 fix: prior draft said «not security-load-bearing» but ALSO «used for suspicious-session detection» — self-contradictory; this revision picks: informational, never security-load-bearing). |
| `ayla.contract_version` | string | **yes (S13 fix)** | The JWT contract version this token was issued under. Frozen on v1 for MVP: `"1"`. Verifier MUST reject tokens with unknown contract_version (prevents rollback-drift attacks where Ayla downgrades issuance + verifier silently enforces older policy without noticing missing claims). |

### 3.3 Full envelope example — `access` token

```json
{
  "iss": "https://api.ayla.app",
  "sub": "f1a2b3c4-d5e6-4789-9abc-def012345678",
  "aud": ["ai-bot-platform"],
  "exp": 1747930200,
  "iat": 1747929300,
  "nbf": 1747929300,
  "jti": "6d7e8f9a-0b1c-4d2e-3f4a-5b6c7d8e9f0a",
  "ayla": {
    "token_type": "access",
    "tenant_id": "9c3a7e1b-4d52-4f8e-b3a1-7c2d8e1f0a5c",
    "relationships": [
      {
        "tenant_id": "9c3a7e1b-4d52-4f8e-b3a1-7c2d8e1f0a5c",
        "role": "customer",
        "granted_at": "2026-05-15T10:24:11Z"
      },
      {
        "tenant_id": "3d5f7e1c-8a2d-4e6f-b9c0-1d2e3f4a5b6c",
        "role": "customer",
        "granted_at": "2026-04-02T18:33:45Z"
      }
    ],
    "scope": ["user:profile:read", "memory:read", "memory:write", "booking:create", "booking:read"],
    "user_role": "customer",
    "anon_session_id": null,
    "device_id": "ios-9c3a7e1b-4d52",
    "contract_version": "1"
  }
}
```

### 3.4 Full envelope example — `anonymous` token

```json
{
  "iss": "https://api.ayla.app",
  "sub": "01HEXAMPLE0000000000000ULD",
  "aud": ["ai-bot-platform"],
  "exp": 1750521300,
  "iat": 1747929300,
  "nbf": 1747929300,
  "jti": "00000000-0000-4000-8000-00000000beef",
  "ayla": {
    "token_type": "anonymous",
    "tenant_id": null,
    "relationships": [],
    "scope": ["provider:directory:read", "memory:write:green_zone_only"],
    "user_role": "customer",
    "anon_session_id": "01HEXAMPLE0000000000000ULD",
    "device_id": "ios-9c3a7e1b-4d52",
    "contract_version": "1"
  }
}
```

Note: `sub` and `ayla.anon_session_id` reference the same `AnonymousSession` row. `tenant_id` is null (anonymous sessions are not tenant-scoped). `scope` is intentionally narrow — green-zone memory writes only (per ADR-0011 §10 + Epic #223 #285 anonymous-mode memory write guard).

---

## 4. Signing + key management

### 4.1 Algorithm — RS256 EVERYWHERE (S2 fix)

**RS256. Public-key signatures only — for ALL four token types, including service_to_service.**

HS256 (HMAC) is **forbidden** across the board:

- HS256 secret rotation requires synchronized key roll across both backends — operationally fragile.
- HS256 verification requires the verifier to hold the signing secret, which means the verifier can also FORGE tokens. With RS256, each side verifies with the other's public key only.
- The «algorithm confusion» attack (a token with `alg: HS256` signed using the public key as a shared secret) is mitigated only if verifiers HARDCODE `alg: RS256` and reject HS256 entirely. This contract mandates hardcoding.

**Why HS256 for service_to_service was rejected (was a v1 draft option):** in the draft, bot-platform held Ayla's HS256 secret to forge s2s tokens. Combined with bot-platform's broader ingress surface (MAX, Telegram, future channels), a single bot-platform compromise gave an attacker the ability to mint s2s tokens with arbitrary `user_on_behalf_of` and exfiltrate red-zone PII from Ayla. **This was THE security-boundary failure of ADR-0009 split-domain architecture.** RS256 each-side-own-keypair is the only audit-defensible answer.

**New layout under S2 fix:**

- Ayla djangoproject has keypair A (signing key A-private + public A in Ayla's JWKS).
- ai-bot-platform has keypair B (signing key B-private + public B in bot-platform's JWKS).
- Ayla issues user tokens signed with A-private. Bot-platform verifies with public A.
- Bot-platform issues s2s tokens (when calling Ayla) signed with B-private. Ayla verifies with public B.
- Ayla issues s2s tokens (when calling bot-platform) signed with A-private. Bot-platform verifies with public A.
- Q-JWT1 (was deferred Phase 2+) is now **resolved 🟢** in this contract.

### 4.2 Key distribution — JWKS

Ayla djangoproject exposes `GET /.well-known/jwks.json` with the active signing key(s). Format follows RFC 7517:

```json
{
  "keys": [
    {
      "kty": "RSA",
      "kid": "ayla-2026-Q2",
      "use": "sig",
      "alg": "RS256",
      "n": "<base64url-modulus>",
      "e": "AQAB"
    }
  ]
}
```

Bot-platform fetches and caches this endpoint. Cache TTL = 5 minutes (matches access-token lifetime upper bound). On cache miss, fetch + cache. On signature-validation failure with `kid` not in cache, force-refresh the JWKS (handles mid-rotation scenarios).

### 4.3 Key rotation

- **Rotation cadence:** quarterly. New `kid` (e.g. `ayla-2026-Q3`) created 14 days before the cutover.
- **Overlap window:** 14 days. During overlap, Ayla signs new tokens with the new `kid` but still publishes both keys in JWKS. Tokens signed with the old `kid` remain valid until they naturally expire (15 minutes for access; 90 days for refresh — so refresh tokens cross rotation boundaries fine).
- **Emergency rotation** (compromise suspected): immediate new `kid`, old `kid` removed from JWKS within 60 seconds. Bot-platform caches expire within 5 min, so worst-case access tokens forged with the leaked key are valid for 5 minutes. Refresh tokens signed by the leaked key MUST be force-revoked via the blacklist (§7) — same incident triggers a mass blacklist of all refresh `jti` issued before the compromise window.
- **Key storage:** private signing key in Ayla djangoproject's secret manager (Vault / equivalent). NEVER in source control. Bot-platform NEVER sees the private key.

### 4.4 Service-to-service signing (RS256 + own JWKS)

Per S2 fix in §4.1: each side has its own RSA keypair and publishes its own JWKS.

- ai-bot-platform exposes `GET /.well-known/jwks.json` (same RFC 7517 format as Ayla's). Ayla fetches and caches with the same 5-minute TTL + emergency rotation handling as bot-platform's view of Ayla's JWKS.
- Rotation is INDEPENDENT per side — bot-platform can rotate keypair B without coordinating with Ayla (and vice versa). Each side just re-fetches the other's JWKS on cache miss.
- Token lifetime stays 5 minutes (§2 — short to limit leak damage; new token per request acceptable cost).

**NS2 fix — coordinated dual-`kid` coexistence (mandatory during rotation):**

«Rotation independent per side» (v1.1 wording) is operationally unsafe. If Ayla rotates keypair A while bot-platform's JWKS cache is stale (up to 5 min per §4.2), bot-platform rejects valid new-A Ayla tokens → routine rotation = guaranteed outage window. Same on the reverse side.

The hard rule for v1.2:

- Each side's JWKS MUST publish **TWO active `kid`s simultaneously during a rotation window of at least 10 minutes** (the inverse side's 5-min cache TTL + 5-min safety margin).
- The publishing side signs new tokens with the new `kid` from rotation start (T=0); the old `kid` remains in JWKS but not used for new tokens.
- At T+10min, the publishing side may drop the old `kid` from JWKS (all consumers have had at least one cache-refresh cycle to see the new `kid`).
- The verifier MUST accept tokens signed by ANY `kid` currently in JWKS — never «only the latest».

**Rotation drill SOP:** before any planned rotation, the operator runs the rotation in staging end-to-end, verifies no 401 spike for 30 minutes post-cutover, and only then rotates in production. SOP lives in `docs/runbooks/jwks-rotation.md` (filed as follow-up §13.X — to be created).

**Bot-platform compromise impact analysis under this layout:** if bot-platform's private key B leaks, attacker can mint s2s tokens claiming `iss: ai-bot-platform`. Ayla MUST further gate s2s requests on `user_on_behalf_of` consent binding (§5.4 below). The chain is **reshaped** (NS1-bounded to a 60s replay window), not severed — see §5.4 honest framing.

---

## 5. tenant_id semantics (ADR-0009 §Hard rule #6)

This is the most consequential rule in the contract. Get it wrong and tenant scoping silently fails.

### 5.1 «Active tenant», not «owner»

`ayla.tenant_id` in a user-facing token represents the **currently-active tenant context** for the request the user is making — NOT the tenant that «owns» the user. A customer can have N relationships with N tenants; the JWT carries one of them at any moment.

Mobile selects the active tenant via the provider switcher (Epic #222 #252). On switch, Mobile calls Ayla `POST /api/v1/auth/switch-tenant` with the target tenant_id; Ayla validates the relationship + issues a new access token with the updated `ayla.tenant_id` claim.

### 5.2 Verifier MUST «trust but verify»

`ayla.relationships` in the token is a **snapshot at issuance time**. It may be stale (relationship revoked after the token was issued — but before it expired). bot-platform MUST re-verify against its own `TenantUserRelationship` mirror table on every tenant-scoped request:

```python
# Bot-platform middleware (simplified — S5 fix: User.is_active also checked
# for null-tenant tokens, otherwise a disabled user could nuke their memory
# in the 15-minute access-token window after their disable)
tenant_id = jwt['ayla']['tenant_id']
user_id = jwt['sub']

# Layer 1: User.is_active live check — runs FOR ALL TOKEN TYPES, including
# null-tenant. Caches against a kill-list refreshed from Ayla every 30s.
if not is_user_active(user_id):  # consults local kill-list mirror
    raise UserDisabled("user account disabled")

# Layer 2: tenant-scoped relationship check, only when tenant_id is non-null
if tenant_id is not None:
    rel = TenantUserRelationship.objects.filter(
        user_id=user_id,
        tenant_id=tenant_id,
        is_active=True,
    ).first()
    if rel is None:
        raise TenantScopeViolation("active relationship not found")
```

**`is_user_active(user_id)`** consults a kill-list mirror table maintained from Ayla's `user.profile.updated` events (the deactivation event includes a flag). The mirror is refreshed every 30 seconds; worst-case lag is the residual exposure for a freshly-disabled user (much smaller than the 15-minute access-token natural expiry).

If the in-token `relationships` array CLAIMS a relationship but the DB doesn't have it → 401, and a `worker.tenant_required_missing` audit event fires (the soak telemetry per `project_strict_tenant_refuse_soak`).

bot-platform's `TenantUserRelationship` mirror is fed by the `provider.relationship_formed` event (Epic #222 #254). If the event-bus is lagging, a freshly-formed relationship may not yet exist in bot-platform when the JWT arrives — see §10.4 for the «relationship-not-yet-mirrored» failure mode.

### 5.3 `tenant_id = null` — global scope

`ayla.tenant_id` MAY be `null` only for tokens used in global-scope endpoints — memory layer queries that span all tenants the user has relationships with. Examples:

- `GET /api/v1/users/me/memory` — returns memory entries across all source_tenant_ids.
- `POST /api/v1/users/me/memory/forget-all` — nukes everything regardless of tenant.
- `GET /api/v1/users/me/providers` — lists the user's tenant relationships (cross-tenant by definition).

For any tenant-scoped endpoint (chat with a specific provider's salon, booking at a specific tenant, etc.), `tenant_id` MUST be non-null. Verifier rejects null-tenant tokens on tenant-scoped endpoints with HTTP 403 «tenant context required».

The token type for these global-scope endpoints is still `access` (not a special type). The distinction is purely the `tenant_id` claim value.

**S4 fix — endpoint-scoping mechanism named explicitly:**

«Tenant-scoped» is determined at the DRF view layer by the **explicit `@global_scope` decorator opt-in**. The middleware default is FAIL-CLOSED:

- Every endpoint is treated as tenant-scoped UNLESS its view (class or method) carries `@global_scope` from `apps.identity.decorators`.
- The decorator signals to the JWT verifier middleware «null-tenant tokens are acceptable here»; without it, null-tenant → 403 immediately.
- The middleware iterates the URL resolver's resolved view + checks for the decorator presence at request time.
- Adding a new endpoint without thinking about scope defaults to «tenant required» — the safer of the two errors (false 403 is recoverable; false access grant is not).

**Allowlist of global-scope endpoints (kept in `apps.identity.global_scope_registry`):**

- `GET /api/v1/users/me/memory` — cross-tenant memory read
- `DELETE /api/v1/users/me/memory/{entry_id}` — cross-tenant delete
- `POST /api/v1/users/me/memory/forget-all` — cross-tenant nuke
- `GET /api/v1/users/me/providers` — relationship listing
- `GET /api/v1/users/me/profile` (read of user-global PII) — cross-tenant by definition

This list is updated explicitly per PR via the registry; CI test asserts that any view carrying `@global_scope` is also listed in the registry (and vice versa), preventing «forgot to flag this endpoint» drift.

**NS6 fix — scope catalog for `tenant_id=null` requests (CRITICAL — chain landing zone):**

S12 (v1.1) specified that `ayla.scope` is filtered to the active tenant's relationship for the endpoint scope-check. **That filter is undefined when `tenant_id=null`** (global-scope endpoints) — exactly the surface where the S1+S2+S3 chain (red-zone memory exfiltration) lands.

Three default behaviours all fail:

- **Union of all scopes:** re-enables original Q-JWT3 privilege escalation for memory endpoints (master-at-B's `master:*` scopes leak into a customer-at-A memory call).
- **Intersection of all scopes:** locks the user out of their own memory.
- **No filtering (passthrough):** identical to union → escalation.

**Resolution:** introduce a third scope-catalog namespace `user_global:*` that is the ONLY valid scope set for `tenant_id=null` requests. All other scopes (tenant-specific) are filtered OUT entirely for null-tenant requests.

Scopes in the `user_global:*` catalog (frozen for MVP):

- `user_global:memory:read` — `GET /api/v1/users/me/memory`
- `user_global:memory:delete_entry` — `DELETE /api/v1/users/me/memory/{entry_id}`
- `user_global:memory:forget_all` — `POST /api/v1/users/me/memory/forget-all`
- `user_global:providers:list` — `GET /api/v1/users/me/providers`
- `user_global:profile:read` — `GET /api/v1/users/me/profile`

Token-issuance side (Ayla): when generating an access token, Ayla MUST include the `user_global:*` scopes the user is entitled to (effectively all of them for any logged-in user, since these are personal-scope rights — but the explicit naming preserves the «scope catalog» pattern + future-proofs subset configurability).

Verifier side: when handling `tenant_id=null` request, the verifier filters `ayla.scope` to only `user_global:*` entries, then checks endpoint requirement against that filtered set. Tenant-specific scopes (e.g. `master:*`, `customer:bookings:create`) are dropped from consideration.

This closes the chain-landing-zone hole: a compromised bot-platform that mints s2s tokens with arbitrary scope cannot escalate via `tenant_id=null` global-scope requests, because the `user_global:*` namespace is the gate and it's tightly enumerated.

**NS4 fix — decorator coverage gaps:** the v1.1 spec assumed Django ViewSet method introspection works uniformly. Adversarial review found three gaps:

1. **Function-based views (FBVs):** Django FBVs are plain callables — the `@global_scope` decorator works (wraps the function), but the middleware's reflection-via-URL-resolver MUST verify it found a callable, not a class. CI test: dispatch a sample FBV with `@global_scope` and a sample without; assert the middleware finds the decorator marker in both reflection paths.
2. **Async views + custom WSGI handlers:** ASGI views bypass standard `urls.py` resolver in some custom setups (custom routing middleware, Channels). The middleware MUST run on the OUTERMOST middleware layer (before any custom routing) AND the registry check MUST also run as a startup-time check: enumerate every URL pattern + every view function + every ViewSet method via `urls.get_resolver()`, build the full set, cross-reference with the registry. Mismatch at startup → bot-platform refuses to boot with `IncompleteGlobalScopeRegistry` error.
3. **Matcher rule for paths in the registry:** EXACT path match only. **NOT prefix.** Normalize trailing slash + percent-encoding before comparison. The v1.1 wording «allowlist registry» was open to interpretation — explicit now: registry entries are exact paths (regex match for URL parameters allowed but documented inline), and `/users/me/memory-bypass-X` does NOT inherit privileges of `/users/me/memory`. The startup enumeration catches drift; the path-match rule prevents prefix-shadow attacks.

These controls ship together in the middleware PR (follow-up §13.X).

### 5.4 service_to_service tokens — with consent binding (S3 fix)

For cross-repo REST calls (e.g. bot-platform calls Ayla `GET /api/v1/users/{user_id}/dob` for the minor-protection lookup per ADR-0011 §10.2), a `service_to_service` token is used. **The token MUST embed the original user's access JWT as a nested claim** — Ayla re-verifies it on receipt, proving the user actually has a live session at the moment the s2s call is made.

```json
{
  "iss": "ai-bot-platform",
  "sub": "service-account/memory-writer",
  "aud": ["ayla-djangoproject"],
  "exp": 1747929600,
  "iat": 1747929300,
  "jti": "00000000-0000-4000-8000-00000000s2s1",
  "ayla": {
    "token_type": "service_to_service",
    "tenant_id": null,
    "scope": ["user:dob:read"],
    "user_role": "service",
    "user_on_behalf_of": "f1a2b3c4-d5e6-4789-9abc-def012345678",
    "user_token": "<the original user's access JWT, signed by Ayla — verbatim string>",
    "contract_version": "1"
  }
}
```

**S3 chain-break mechanism (verifier side, Ayla):**

1. Validate the outer s2s token signature (RS256 with bot-platform's public key — §4.4).
2. Extract `ayla.user_token` and validate it as a normal user access token (signature with Ayla's own public key, `exp` check, `iss` check).
3. Cross-check: outer `ayla.user_on_behalf_of` MUST equal inner user-token's `sub`. Mismatch → 401 `s2s_consent_binding_violation`.
4. Cross-check: outer `ayla.scope` MUST be a SUBSET of inner user-token's `ayla.scope`. The user cannot delegate more privilege than they have. Wider scope → 401 same error.
5. Cross-check: inner user-token's `exp` MUST be in the future AND `iat` MUST be within the last 60 seconds (NS1 fix — replay-window cap). A user-token whose `iat` is older than 60s → reject with `s2s_inner_token_stale`. Without this cap, a compromised bot-platform could replay a single captured user-token in fresh s2s envelopes for the inner token's full lifetime (up to 15 min) — see §5.4 «Compromise impact» for the chain analysis. Alternative implementation: Ayla maintains a `s2s_seen_inner_jti` LRU dedup table with 60s TTL — same effect with strict single-use semantics.
6. For sensitive reads (DOB, red-zone memory, payment history): Ayla additionally consults the **consent record** — a row in `users_consents` table — that records the user explicitly granted bot-platform the right to read this specific data category. Without a matching consent record, 403 `consent_required`.

**Compromise impact under S3 fix — honest framing (revised v1.2 per adversarial N=9):**

The chain is **RESHAPED, not severed**. Under ADR-0009 split-domain architecture, bot-platform inherently sees user tokens on ingress and MUST call Ayla on the user's behalf — this means bot-platform compromise has SOME PII-exfiltration capability for the session lifetime. v1.1's claim «full PII exfiltration prevented» was too strong; v1.2 corrects to «bounded by session lifetime + consent surface».

If bot-platform's private key B leaks, attacker can mint s2s tokens with valid outer signature. The attacker additionally needs:

- A live user-token signed by Ayla (real user logged in within the user-token lifetime) — capture window.
- The user-token's `sub` matches `user_on_behalf_of` — attacker cannot inject arbitrary user IDs.
- A consent record exists for that user × data category — attacker cannot exfiltrate categories the user hasn't consented to.

**Residual attack (NS1):** for users with broad red-zone consent (e.g. memory-read consent covering all entries), a compromised bot-platform that captures a single user-token can wrap it in fresh s2s envelopes throughout the user-token's lifetime. Each s2s has a new `jti` (outer); refresh-blacklist doesn't catch the user-token. The window is therefore the inner user-token's remaining lifetime — up to 15 minutes for access tokens — PLUS amplification across many endpoints.

**NS1 mitigation:** require inner user-token's `iat` to be within the last 60 seconds at s2s verification time. This caps the replay window to 60 seconds regardless of inner token's `exp`. Implementation:

```python
# Ayla-side s2s verifier (NS1 step)
inner_iat = inner_user_token['iat']
if (server_now - inner_iat) > 60:
    raise S2sFreshnessViolation(
        "inner user-token too old; must be issued within 60s of s2s call"
    )
```

Alternative implementation (chosen): Ayla maintains a **`s2s_seen_inner_jti` LRU dedup table** keyed by `inner_user_token.jti`, TTL 60s. Same effect (only «recent» inner tokens valid) with strict single-use semantics if desired. Either approach is acceptable; consumer ticket (#258 or follow-up) picks one.

**Real bound under NS1 fix:** 60-second replay window per captured user-token, multiplied by the rate at which bot-platform can pump s2s calls inside that window. This is materially better than 15 minutes; it's still NOT zero. The audit-defensible framing is «60-second exfiltration window + consent-surface limit per category», NOT «severed».

**S1+S2+S3 chain status (v1.2):** RESHAPED. Bot-platform compromise = bounded PII exfiltration during sessions that overlap the compromise window, capped at the consent surface. Mitigated by: (a) shortest practical inner-token freshness (NS1, 60s); (b) consent-record gating per data category; (c) outbound bot-platform → Ayla traffic anomaly detection (operator-side observability, not in this doc's scope); (d) prompt key rotation per §4.3 emergency-rotation protocol on suspected compromise.

---

## 6. Anonymous JWT (Epic #223)

Anonymous tokens enable the Mini App «browse-before-register» flow. Customer opens the Mini App without registering, gets an anonymous JWT, can browse providers + ask Ayla questions + accumulate green-zone memory entries. On `«Записаться»` tap → MAX OAuth → user JWT issued → anonymous session merged.

### 6.1 Issuance

- **Endpoint:** `POST /api/v1/anonymous/session` (Ayla djangoproject — per Epic #223 #257).
- **No credentials** required. Endpoint is rate-limited per IP (10/min).
- **Output:** anonymous JWT + `device_token` + session ID.
- **Lifetime:** 30 days (matches `AnonymousSession.expires_at` per Epic #223 #255).

### 6.2 Scope

Anonymous tokens carry a narrow scope:

- `provider:directory:read` — list/view providers.
- `memory:write:green_zone_only` — Ayla can write green-zone facts; yellow/red blocked at the writer (per ADR-0011 §10).
- `chat:anonymous` — Ayla chat without provider-confidential context.

Anonymous tokens MUST NOT carry:

- `booking:create` — would let an anonymous user create a booking without payment binding.
- `payment:*` — no payment scope; YooKassa wants a registered user.
- `memory:write:yellow` / `memory:write:red` — sensitivity prevents anonymous writes.

### 6.3 Conversion to registered

When the customer completes MAX OAuth (Epic #223 #258), Ayla:

1. Validates the MAX OAuth response.
2. Looks up or creates the `User` row.
3. Looks up the `AnonymousSession` by device_token presented in the OAuth callback body.
4. Calls the merge service (Epic #223 #258 + #259):
   - Promotes green-zone memory entries from `SoftPersonalContext` to `UserPersonalContext` + `MemoryEntry` rows.
   - Sets `AnonymousSession.merged_into_user_id` + `merged_at`.
5. Issues a NEW user JWT pair (`access` + `refresh`).
6. The old anonymous JWT is added to the blacklist by `jti` (§7) — anonymous JWTs after merge are dead.

### 6.4 Cross-device merge

When a registered user signs into Mobile on a second device that has its own anonymous session, the merge endpoint (Epic #223 #259 `POST /api/v1/users/me/merge-anonymous`) merges that device's anonymous session into the existing user. The anonymous JWT is blacklisted; the user's existing tokens stay valid.

---

## 7. Refresh tokens

### 7.1 Lifetime + rotation

- **Lifetime:** 90 days (matches Ayla djangoproject's current `simpleJWT` config).
- **Rotation:** every use produces a NEW refresh JTI + access pair, and the old refresh JTI is added to the blacklist. Reusing a refresh token returns 401 (and signals possible compromise — see §10.6).

### 7.2 Blacklist

Ayla maintains a `RefreshTokenBlacklist` table indexed by `jti`. Rows:

- Inserted on rotation (old `jti` blacklisted as new pair is issued).
- Inserted on explicit logout (`POST /api/v1/auth/logout` adds the current refresh `jti`).
- Inserted on incident response (mass-blacklist during key compromise per §4.3).
- Pruned **90 days** after `jti.exp` (S8 fix — was 30d in draft; typical security-incident detection lag is ~200d, so 30d-post-exp pruning closes a real forensic window. 90d retains long enough for most breach-detection cycles to catch a pre-pruning revocation).

bot-platform does NOT have a blacklist mirror (it only verifies access tokens, which are short-lived enough that blacklisting would be operational overhead). If a refresh is blacklisted, the next access token won't be issued, and the 15-minute window of remaining access-token validity is acceptable risk.

### 7.3 Logout

**S9 — `/api/v1/auth/refresh` rate limit:** Ayla's refresh endpoint MUST be rate-limited per-`jti` (max 1 refresh per minute per token) AND per-user (max 30 refreshes per hour). The per-`jti` cap blocks reuse-amplification; the per-user cap blocks brute-force enumeration of stolen `jti`s.

`POST /api/v1/auth/logout` (Ayla djangoproject):

- Body: current refresh token.
- Effect: adds refresh `jti` to blacklist; ALSO returns a header `Ayla-Logout-Cascade: yes` that signals the gateway / mobile to drop any cached access tokens.
- No effect on the existing 15-minute access token (it expires naturally). If forced-immediate-logout is needed (account takeover suspicion), call the mass-blacklist incident endpoint (§4.3 emergency rotation also covers this).

---

## 8. Verification contract (bot-platform side)

Bot-platform middleware verifies every incoming JWT. The verifier MUST:

### 8.1 Standard JWT validation

1. Extract the `kid` from the JWT header. Look up the public key in the JWKS cache (refresh on miss).
2. Verify the signature using **RS256 only**. If the JWT header advertises any other `alg`, reject with HTTP 401 `algorithm_unsupported`. **Do NOT trust the `alg` header to decide the algorithm — hardcode RS256.**
3. Validate `exp`, `nbf`, `iat`. Clock skew tolerance: ±60 seconds (matches event-contract.md §6.2 NTP requirement).
4. Validate `iss` — MUST be `"https://api.ayla.app"` (or current Ayla auth host).
5. Validate `aud` — MUST contain `"ai-bot-platform"` for tokens reaching bot-platform.

### 8.2 Ayla-claim validation

6. Validate `ayla.token_type` IN (`access`, `anonymous`, `service_to_service`). Reject `refresh` — refresh tokens are used only on Ayla; they MUST NOT reach bot-platform endpoints.

   **S14 fix — fail-closed timing:** JWT verification fails CLOSED (reject 401) from day-1 of bot-platform's JWT middleware (#435), INDEPENDENT of the `STRICT_TENANT_REFUSE` flag. The STRICT_TENANT_REFUSE soak (per `project_strict_tenant_refuse_soak` and ADR-0011 §9.1) governs WORKER-side tenant scoping, not auth-layer JWT validation. Confusing these two is a known attractor — call out the distinction:
   - `STRICT_TENANT_REFUSE=False` (log-only soak): WORKER finds a request missing a tenant context → log event, don't refuse.
   - JWT auth-layer validation: always fail-closed. A malformed/missing/invalid JWT is rejected at HTTP 401 from day-1; never log-only.
7. Validate `ayla.tenant_id`:
   - If endpoint is tenant-scoped AND `tenant_id IS NULL` → reject with HTTP 403 `tenant_required`.
   - If `tenant_id IS NOT NULL` → look up `TenantUserRelationship(user_id=sub, tenant_id=ayla.tenant_id, is_active=True)`. If absent → 401, fire `worker.tenant_required_missing` audit event.
8. Validate `ayla.scope` against endpoint requirements. **S12 fix:** the `ayla.scope` claim is the UNION of the user's scopes across all relationships at issuance time. The verifier MUST FILTER this union to scopes applicable to the active `ayla.tenant_id` BEFORE the endpoint scope-check. Specifically: take the user's role+scope at `ayla.tenant_id` from the verified relationships array, and check the endpoint requirement against that filtered set. Without filtering, a user who is a `customer` at tenant A and a `master` at tenant B presents an active-tenant=A token carrying `master:*` scopes and gets master endpoints at A — cross-tenant privilege escalation. (Q-JWT3 was originally deferred Phase 2+; promoted to 🟢 resolved here.) If scope insufficient post-filter → 403 `insufficient_scope`.
9. For anonymous tokens (`ayla.token_type='anonymous'`): the endpoint MUST be on the anonymous-allowed allow-list. Reject anonymous on registered-only endpoints with 403.

### 8.3 Cache hierarchy

- **JWKS cache:** 5-minute TTL, in-memory (no DB roundtrip).
- **`TenantUserRelationship` lookup:** uncached at the middleware layer (single indexed query — `~100µs`). NOT cached because relationship status changes (revocation) must take effect immediately on the next request.
- **Blacklist:** N/A on bot-platform (per §7.2).

---

## 9. Revocation

Three revocation pathways:

1. **Natural expiration.** Access tokens expire in 15 minutes. The default revocation mechanism.
2. **Refresh-token blacklist.** Adds a `jti` to Ayla's blacklist. Subsequent refresh attempts → 401. The currently-valid access token (max 15 minutes remaining) is NOT immediately invalidated.
3. **Mass-blacklist incident.** During key compromise or account-takeover incident, Ayla bulk-inserts all suspect `jti` into the blacklist + (separately) rotates signing keys per §4.3.

There is NO «logout from all devices» endpoint that revokes all access tokens immediately. The 15-minute access-token window is the residual exposure. Tightening this requires either shorter tokens (operational cost on Mobile + bot-platform: more frequent refresh round-trips) or session-state tracking in bot-platform (rejected — bot-platform stays stateless on auth).

---

## 10. Failure modes

### 10.1 Invalid signature

Verifier returns HTTP 401 with `WWW-Authenticate: Bearer error="invalid_token", error_description="signature verification failed"`. No retry, no information leak (don't tell the caller WHY the signature failed). Counter `jwt_verify_failed_total{reason="signature"}` increments.

### 10.2 Expired token

HTTP 401, `error="invalid_token"`, `error_description="token expired"`. Mobile detects this status + refreshes via Ayla `POST /api/v1/auth/refresh`. Counter `jwt_verify_failed_total{reason="expired"}` increments.

### 10.3 Algorithm-confusion attack

If a caller sends a JWT with `alg: HS256` (or `none`), the verifier rejects with HTTP 401 immediately (hardcoded RS256 — §8.1.2). Counter `jwt_verify_failed_total{reason="algorithm"}`. Threshold alert: any non-zero rate of `reason=algorithm` failures → page (this is almost always an active attack).

### 10.4 Relationship not yet mirrored

The bot-platform `TenantUserRelationship` mirror is fed by Ayla events (per event-contract.md §3 + Epic #222). If a relationship was created in Ayla 200ms ago and the event hasn't reached bot-platform yet, a fresh JWT with the new `tenant_id` will fail bot-platform's lookup → 401.

**Mitigation (S6-tightened):** the verifier MAY (not MUST) treat the in-token `ayla.relationships` array as authoritative for the SPECIFIC tenant claimed in `ayla.tenant_id`, IF ALL of:

1. The relationship's `granted_at` in the token is within the last 60 seconds (per server clock — NOT trusting client clock).
2. The in-token relationship matches a TUR row that exists in bot-platform with `is_active=False` (transient lag, will catch up).
3. The TUR row's `granted_at` (on bot-platform's mirror, fed by `provider.relationship_formed` events) is within ±5 seconds of the in-token `granted_at`. **S6 fix:** without this delta-match, a post-key-compromise attacker could re-issue tokens claiming any old relationship's `granted_at` within the 60s «grace» window, bypassing revocation. The delta-match anchors token-claimed timestamp to a real event the consumer also saw.

If all 3 hold, request proceeds + `worker.tenant_relationship_lag` event fires for telemetry.

If the in-token `granted_at` is older than 60 seconds OR bot-platform doesn't have the TUR row OR the timestamps diverge by >5s → reject + alert.

**NS3 fix — TUR rapid grant-revoke-grant bypass:** the ±5s delta-match (condition 3 above) is insufficient if a compromised tenant admin can revoke + immediately re-grant a relationship to refresh `granted_at`. Defensive controls:

- **Rate-limit grants per (user, tenant): max 1 per hour.** A second grant within 60 minutes of an existing or recently-revoked relationship → 429 Too Many Requests + audit event `provider.relationship_grant_throttled`.
- **Alert on revoke→grant <60s:** any revoke followed by a re-grant of the same `(user, tenant)` within 60 seconds fires a high-priority `provider.relationship_churn_suspicious` alert and pages on-call. This is almost always either an admin mistake or active attack.
- These controls live in Ayla djangoproject's grant API; bot-platform's TUR mirror inherits them automatically because mirror updates only on observed Ayla events.

### 10.5 JWKS endpoint unreachable

bot-platform cannot fetch Ayla's JWKS to verify a new `kid`. Cache miss + fetch failure = HTTP 503 from bot-platform with `error="auth_unavailable"`. Mobile retries with exponential backoff.

Mitigation (S7-reconciled with §4.3): bot-platform retains the LAST-KNOWN-GOOD JWKS as a stale cache. **The stale-cache validity window is 5 MINUTES, matching §4.3's «emergency rotation worst-case 5 min» bound**. (Earlier draft said 1 hour — direct contradiction. 1 hour creates a forgery window during BGP/DNS poisoning. 5 min is the floor: if Ayla is unreachable longer than 5 min, all token verification fails — Ayla MUST be up for auth, no exceptions.)

Concretely:

- 0–5 min Ayla unreachable: stale cache valid; pre-rotation tokens still validate; new `kid`s unverifiable (their tokens fail).
- >5 min Ayla unreachable: bot-platform returns HTTP 503 `auth_unavailable` for all token-protected endpoints. Mobile retries with exponential backoff.

This is a real availability trade — bot-platform's auth is hard-coupled to Ayla's uptime. The trade was accepted in ADR-0009 (Ayla owns canonical identity; bot-platform is stateless on auth). Document the SLO requirement: Ayla auth must hit 99.95% availability or the platform degrades to read-only / cached responses.

### 10.6 Refresh-token reuse (potential compromise)

Same refresh `jti` presented twice within the rotation window is a red flag — Ayla blacklists the ENTIRE refresh-token chain rooted at this `jti` (every descendant rotation) + returns 401. The mobile app sees this as «sign out and re-authenticate» which is the right safety default. Operator alert fires.

### 10.7 Anonymous token on registered-only endpoint

HTTP 403 `error="anonymous_not_allowed"` with hint header `X-Ayla-Auth-Required: registered`. Mobile catches this + triggers the OAuth gate (Epic #223 #260 «Записаться» modal). Counter `jwt_verify_failed_total{reason="anon_scope_denied"}`.

### 10.8 Service-to-service token used as user token

If a `service_to_service` token reaches an endpoint expecting a user token (or vice versa), the verifier rejects with 401 `error="invalid_token"`, `error_description="wrong token type"`. This is a misconfiguration, not an attack — fix the calling code.

### 10.9 Token presented in query string / body (S11 enforcement)

HTTP 400 `error="invalid_token_location"`. Mobile + service callers MUST present JWTs in `Authorization: Bearer` header only. If a token arrives via URL or body, the verifier rejects without inspecting the token contents (prevents the token from being logged at any tier that processes query strings or bodies — NGINX, mobile crash reporters, browser history). Counter `jwt_verify_failed_total{reason="bad_location"}`. Threshold alert: non-trivial rate suggests a buggy client or active probing.

### 10.10 s2s token consent-binding violation (S3 chain-break)

HTTP 401 `error="s2s_consent_binding_violation"` with sub-reasons:
- `outer_sub_mismatch_inner_sub` — `user_on_behalf_of` ≠ inner user-token `sub`.
- `outer_scope_exceeds_inner` — outer scope is wider than the user actually has.
- `inner_user_token_expired` — replay of an old user-token.
- `consent_record_missing` — Ayla `users_consents` row absent for this user × data category.

Counter `jwt_verify_failed_total{reason="s2s_consent_<sub>"}`. Threshold alert at ANY non-zero rate — these are either a misconfiguration or an active attempt to escalate via compromised bot-platform key.

---

## 11. References

- **Authority:** [`docs/adr/ADR-0009-ayla-split-domain-architecture.md`](../adr/ADR-0009-ayla-split-domain-architecture.md) §Hard rule #6 + §Mobile API split + §Memory model.
- **Sibling spec:** [`docs/architecture/event-contract.md`](./event-contract.md) — the other cross-service contract (events, not auth) using the same issuer/consumer pattern.
- **ADR-0006:** [`docs/adr/ADR-0006-field-level-encryption.md`](../adr/ADR-0006-field-level-encryption.md) — key custody pattern that informs §4 JWKS approach.

### Issues that reference this doc

- Spec for #258 — OAuth callback (Ayla djangoproject — token issuance side).
- Spec for #257 — anonymous session bootstrap (Ayla djangoproject).
- Spec for #259 — anonymous-to-registered merge (Ayla djangoproject).
- Spec for bot-platform JWT verification middleware (`apps/identity` scope — ticket filed post-merge).

---

## 12. Open questions (track here; resolve before each follow-up consumer ticket)

| # | Question | Owner | Status |
|---|---|---|---|
| **Q-JWT1** | Should service-to-service tokens use RS256 (asymmetric, no shared secret) instead of HS256, for consistency? | infra + security | 🟢 **RESOLVED v1.1** — RS256 each side own keypair (S2 fix) |
| **Q-JWT2** | Where does the JWKS endpoint live during DNS deferral (gobeauty.site era)? Currently spec says `https://api.ayla.app/.well-known/jwks.json` placeholder — pre-domain-decision Phase 1+ | infra | 🟡 substitute `api-dev.gobeauty.site` until domain finalized |
| **Q-JWT3** | Per-relationship scopes — should `ayla.scope` differ across the user's tenant relationships? E.g. customer at tenant A, master at tenant B — what scope is in the active-tenant=A token? | Eng + PM | 🟢 **RESOLVED v1.1** — scope is the union at issuance time; verifier MUST filter to active tenant's relationship before endpoint scope-check (S12 fix in §8.2.8) |
| **Q-JWT4** | Anonymous token revocation — currently «expires in 30d, blacklisted on merge». Should there be an explicit «void this anonymous session» endpoint for cleanup of stale device tokens? | Eng | 🟡 Phase 2+ if data shows accumulation |
| **Q-JWT5** | Cross-device merge with conflicting anonymous state — handled by Q-AN9 (resolved 2026-05-20: medical-routing + register-encouragement). Verify JWT contract is consistent with that resolution. | Privacy + Eng | 🟢 consistent — anonymous tokens don't carry red/yellow scope |
| **Q-JWT6** (NS7) | Contract-version lockout during v2 rollout — verifier rejecting unknown `contract_version` causes 24h 401 window between Ayla v2 deploy and bot-platform v2 deploy. Solution: verifier accepts `{"1", "2"}` during a declared migration window via feature flag `JWT_ACCEPTED_CONTRACT_VERSIONS`. Window opens 7 days before the planned Ayla v2 cutover; closes 7 days after. | Infra | 🟢 RESOLVED v1.2 |
| **Q-JWT7** (NS8) | Bot-platform JWKS publication auth — public endpoint vs internal-only. **Resolution:** `https://api-bot.<hostname>/.well-known/jwks.json` is **publicly accessible** (standard JWKS pattern — public keys are public by design; rotation patterns leak is acceptable). Endpoint MUST set `Cache-Control: public, max-age=300` to match the 5-min cache TTL on the consumer side. NO authentication required. | Infra + Security | 🟢 RESOLVED v1.2 |

These are tracked but DO NOT block this doc — they refine v2+ of the contract.

---

## Last verified

2026-05-22 — v1.2 round-3 amendments addressing 6 new NS blockers + 2 nice-to-haves from PR #523 adversarial pass (N=9). Critical: S1+S2+S3 chain framing revised — RESHAPED not severed; bot-platform compromise has bounded (60s replay window) PII-exfiltration capability per ADR-0009 architecture inherent. NS1 inner user-token 60s freshness cap; NS2 dual-`kid` 10min coexistence + rotation drill SOP; NS3 grant rate-limit + revoke→grant alert; NS4 @global_scope coverage (FBV / async / exact-match); NS5 WebSocket §1.2 ticket-exchange exception; NS6 `user_global:*` scope catalog for tenant_id=null (closes chain landing zone). NS7/NS8 resolved (contract_version migration window + bot-platform JWKS public).

2026-05-22 — v1.1 round-2 amendments addressing 11 adversarial blockers + 3 nice-to-haves from PR #516 review. Critical security boundary fixes (S1+S2+S3 chain — bot-platform compromise → forge identity → exfiltrate red-zone PII via Ayla REST): aud segregation, RS256 each-side-own-keypair, s2s token MUST embed nested user-token + consent-record check. Other fixes: token-location (Bearer header only), tenant-scope endpoint allowlist mechanism, User.is_active for null-tenant, lag-grace 5s delta-match, JWKS stale window reconciled to 5min, scope filtered to active tenant, contract_version claim, fail-closed day-1 independent of STRICT_TENANT_REFUSE flag, refresh blacklist 90d, refresh rate-limiting, device_id informational-only.

2026-05-21 — initial draft. v1 of the cross-service JWT contract; landed on dev via PR #508 PEL reaper bundling (W3 work picked up W4 doc as collateral). v1.1 fixes the security-boundary issues that the bundled draft did not address.
