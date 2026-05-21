# JWT Contract — Ayla djangoproject (issuer) → ai-bot-platform (verifier)

> **Status:** v1 — draft 2026-05-21
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
| `aud` | string array | yes | Audience: `["ayla-djangoproject", "ai-bot-platform"]` for user tokens, OR a single-element array for service-to-service. Verifiers MUST reject tokens whose `aud` doesn't include their service name. |
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
| `ayla.device_id` | string \| null | optional | Mobile device identifier, used for device-binding signals (rate limiting, suspicious-session detection). Not security-load-bearing — informational. |

### 3.3 Full envelope example — `access` token

```json
{
  "iss": "https://api.ayla.app",
  "sub": "f1a2b3c4-d5e6-4789-9abc-def012345678",
  "aud": ["ayla-djangoproject", "ai-bot-platform"],
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
    "device_id": "ios-9c3a7e1b-4d52"
  }
}
```

### 3.4 Full envelope example — `anonymous` token

```json
{
  "iss": "https://api.ayla.app",
  "sub": "01HEXAMPLE0000000000000ULD",
  "aud": ["ayla-djangoproject", "ai-bot-platform"],
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
    "device_id": "ios-9c3a7e1b-4d52"
  }
}
```

Note: `sub` and `ayla.anon_session_id` reference the same `AnonymousSession` row. `tenant_id` is null (anonymous sessions are not tenant-scoped). `scope` is intentionally narrow — green-zone memory writes only (per ADR-0011 §10 + Epic #223 #285 anonymous-mode memory write guard).

---

## 4. Signing + key management

### 4.1 Algorithm

**RS256.** Public-key signatures only. HS256 (HMAC) is **forbidden** for user-facing tokens because:

- HS256 secret rotation requires synchronized key roll across both backends — operationally fragile.
- HS256 verification requires bot-platform to hold the signing secret, which means bot-platform can also FORGE tokens. RS256 lets bot-platform verify with the public key only.
- The «algorithm confusion» attack (a token with `alg: HS256` signed using the public key as a shared secret) is mitigated only if verifiers HARDCODE `alg: RS256` and reject HS256 entirely. This contract mandates hardcoding.

For service-to-service (§5.4), HS256 with a shared secret IS allowed because the trust boundary is symmetric (both sides issue and verify).

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

### 4.4 Service-to-service shared secret

For internal REST calls (bot-platform → Ayla, Ayla → bot-platform), a separate `service_to_service` token type uses HS256 with a shared secret. The secret lives in both repos' deploy configs. Rotation is coordinated (both sides updated in lockstep). Token lifetime is 5 minutes — long enough for one request, short enough to limit leak damage.

---

## 5. tenant_id semantics (ADR-0009 §Hard rule #6)

This is the most consequential rule in the contract. Get it wrong and tenant scoping silently fails.

### 5.1 «Active tenant», not «owner»

`ayla.tenant_id` in a user-facing token represents the **currently-active tenant context** for the request the user is making — NOT the tenant that «owns» the user. A customer can have N relationships with N tenants; the JWT carries one of them at any moment.

Mobile selects the active tenant via the provider switcher (Epic #222 #252). On switch, Mobile calls Ayla `POST /api/v1/auth/switch-tenant` with the target tenant_id; Ayla validates the relationship + issues a new access token with the updated `ayla.tenant_id` claim.

### 5.2 Verifier MUST «trust but verify»

`ayla.relationships` in the token is a **snapshot at issuance time**. It may be stale (relationship revoked after the token was issued — but before it expired). bot-platform MUST re-verify against its own `TenantUserRelationship` mirror table on every tenant-scoped request:

```python
# Bot-platform middleware (simplified)
tenant_id = jwt['ayla']['tenant_id']
user_id = jwt['sub']
if tenant_id is not None:
    rel = TenantUserRelationship.objects.filter(
        user_id=user_id,
        tenant_id=tenant_id,
        is_active=True,
    ).first()
    if rel is None:
        raise TenantScopeViolation("active relationship not found")
```

If the in-token `relationships` array CLAIMS a relationship but the DB doesn't have it → 401, and a `worker.tenant_required_missing` audit event fires (the soak telemetry per `project_strict_tenant_refuse_soak`).

bot-platform's `TenantUserRelationship` mirror is fed by the `provider.relationship_formed` event (Epic #222 #254). If the event-bus is lagging, a freshly-formed relationship may not yet exist in bot-platform when the JWT arrives — see §10.4 for the «relationship-not-yet-mirrored» failure mode.

### 5.3 `tenant_id = null` — global scope

`ayla.tenant_id` MAY be `null` only for tokens used in global-scope endpoints — memory layer queries that span all tenants the user has relationships with. Examples:

- `GET /api/v1/users/me/memory` — returns memory entries across all source_tenant_ids.
- `POST /api/v1/users/me/memory/forget-all` — nukes everything regardless of tenant.
- `GET /api/v1/users/me/providers` — lists the user's tenant relationships (cross-tenant by definition).

For any tenant-scoped endpoint (chat with a specific provider's salon, booking at a specific tenant, etc.), `tenant_id` MUST be non-null. Verifier rejects null-tenant tokens on tenant-scoped endpoints with HTTP 403 «tenant context required».

The token type for these global-scope endpoints is still `access` (not a special type). The distinction is purely the `tenant_id` claim value.

### 5.4 service_to_service tokens

For cross-repo REST calls (e.g. bot-platform calls Ayla `GET /api/v1/users/{user_id}/dob` for the minor-protection lookup per ADR-0011 §10.2), a `service_to_service` token is used:

```json
{
  "iss": "ai-bot-platform",
  "sub": "service-account/memory-writer",
  "aud": ["ayla-djangoproject"],
  "exp": 1747929600,
  "iat": 1747929300,
  "jti": "...",
  "ayla": {
    "token_type": "service_to_service",
    "tenant_id": null,
    "scope": ["user:dob:read"],
    "user_role": "service",
    "user_on_behalf_of": "f1a2b3c4-d5e6-4789-9abc-def012345678"
  }
}
```

Note `iss` is the calling service identity, NOT a user. `sub` is the service-account name. `user_on_behalf_of` (new claim only on service-to-service tokens) records which user the call is for — audit logs use this to attribute the action.

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
- Pruned 30 days after `jti.exp` — at which point the token would be expired anyway, so blacklist redundancy is harmless.

bot-platform does NOT have a blacklist mirror (it only verifies access tokens, which are short-lived enough that blacklisting would be operational overhead). If a refresh is blacklisted, the next access token won't be issued, and the 15-minute window of remaining access-token validity is acceptable risk.

### 7.3 Logout

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
7. Validate `ayla.tenant_id`:
   - If endpoint is tenant-scoped AND `tenant_id IS NULL` → reject with HTTP 403 `tenant_required`.
   - If `tenant_id IS NOT NULL` → look up `TenantUserRelationship(user_id=sub, tenant_id=ayla.tenant_id, is_active=True)`. If absent → 401, fire `worker.tenant_required_missing` audit event.
8. Validate `ayla.scope` against endpoint requirements. If scope insufficient → 403 `insufficient_scope`.
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

**Mitigation:** the verifier MAY (not MUST) treat the in-token `ayla.relationships` array as authoritative for the SPECIFIC tenant claimed in `ayla.tenant_id`, IF the relationship's `granted_at` is within the last 60 seconds AND the in-token relationship matches a TUR row that exists in bot-platform with `is_active=False` (transient lag, will catch up). If both conditions hold, the request proceeds + a `worker.tenant_relationship_lag` event fires for telemetry.

If the relationship is older than 60 seconds and bot-platform doesn't have it, that's a real out-of-sync incident — reject + alert.

### 10.5 JWKS endpoint unreachable

bot-platform cannot fetch Ayla's JWKS to verify a new `kid`. Cache miss + fetch failure = HTTP 503 from bot-platform with `error="auth_unavailable"`. Mobile retries with exponential backoff.

Mitigation: bot-platform retains the LAST-KNOWN-GOOD JWKS as a stale cache valid for 1 hour after Ayla becomes unreachable. New `kid`s are unverifiable but pre-rotation tokens still validate. After 1 hour, all token verification fails — Ayla MUST be up for auth.

### 10.6 Refresh-token reuse (potential compromise)

Same refresh `jti` presented twice within the rotation window is a red flag — Ayla blacklists the ENTIRE refresh-token chain rooted at this `jti` (every descendant rotation) + returns 401. The mobile app sees this as «sign out and re-authenticate» which is the right safety default. Operator alert fires.

### 10.7 Anonymous token on registered-only endpoint

HTTP 403 `error="anonymous_not_allowed"` with hint header `X-Ayla-Auth-Required: registered`. Mobile catches this + triggers the OAuth gate (Epic #223 #260 «Записаться» modal). Counter `jwt_verify_failed_total{reason="anon_scope_denied"}`.

### 10.8 Service-to-service token used as user token

If a `service_to_service` token reaches an endpoint expecting a user token (or vice versa), the verifier rejects with 401 `error="invalid_token"`, `error_description="wrong token type"`. This is a misconfiguration, not an attack — fix the calling code.

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
| **Q-JWT1** | Should service-to-service tokens use RS256 (asymmetric, no shared secret) instead of HS256, for consistency? | infra + security | 🟡 HS256 MVP, RS256 if multi-region |
| **Q-JWT2** | Where does the JWKS endpoint live during DNS deferral (gobeauty.site era)? Currently spec says `https://api.ayla.app/.well-known/jwks.json` placeholder — pre-domain-decision Phase 1+ | infra | 🟡 substitute `api-dev.gobeauty.site` until domain finalized |
| **Q-JWT3** | Per-relationship scopes — should `ayla.scope` differ across the user's tenant relationships? E.g. customer at tenant A, master at tenant B — what scope is in the active-tenant=A token? | Eng + PM | 🟡 Phase 2+; MVP uses union of user scopes |
| **Q-JWT4** | Anonymous token revocation — currently «expires in 30d, blacklisted on merge». Should there be an explicit «void this anonymous session» endpoint for cleanup of stale device tokens? | Eng | 🟡 Phase 2+ if data shows accumulation |
| **Q-JWT5** | Cross-device merge with conflicting anonymous state — handled by Q-AN9 (resolved 2026-05-20: medical-routing + register-encouragement). Verify JWT contract is consistent with that resolution. | Privacy + Eng | 🟢 consistent — anonymous tokens don't carry red/yellow scope |

These are tracked but DO NOT block this doc — they refine v2+ of the contract.

---

## Last verified

2026-05-21 — initial draft. v1 of the cross-service JWT contract; ships ahead of #258/#257/#259 implementation as the spec they build against.
