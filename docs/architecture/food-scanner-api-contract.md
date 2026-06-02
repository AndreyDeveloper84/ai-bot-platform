# Food Scanner API contract (W4 backend ↔ W1 Mini App)

**Status:** Locked 2026-06-02 with the food-scanner backend Веха 2 PR.
**Consumers:** `apps/miniapp/src/lib/food-scanner.ts` (W1 swap stub→real).
**Owner:** W4 backend stream.

This document is the single source of truth for the request/response
shapes of the customer-facing food-scanner endpoints. W1 swaps its
stubs (`StubNotWiredError`) onto these endpoints verbatim — any drift
between this doc and the implementation is a bug; fix the
implementation, not the contract.

---

## Identity & auth

Every endpoint requires the Mini App's signed initData:

```
Authorization: MaxInitData <raw_init_data_string>
```

The `require_init_data` decorator resolves the calling `BotUser` +
`Tenant` from the HMAC; the client's session secret never leaves
bot-platform. Outbound calls into Ayla use the standard service
pattern: `X-Service-Token` + `X-External-User-ID: bot:{channel}:{channel_user_id}`.

---

## Two-gate + consent enforcement

Every endpoint (except `food/consent`) checks, in order:

1. `settings.NUTRITION_ENABLED` — master switch for the RU-side
   surface. False → `503 nutrition_disabled`.
2. `settings.FOOD_PHOTO_SCAN_ENABLED` — **only** the `food/scan`
   endpoint consults this. False → `503 photo_scan_disabled`.
3. `BotUser.food_scanner_consent_at IS NOT NULL` — feature-specific
   152-ФЗ consent. Missing → `428 consent_required`.

The `food/consent` endpoint deliberately bypasses (1) and (3) so the
Mini App can record the user's acknowledgement before anything else
is reachable.

`health-flags` requires (1) + (3) but **not** (2) — ED-mode hints are
served from a profile read, not a photo call.

---

## Error envelope

All errors share the shape:

```json
{
  "error": "<machine_slug>",
  "detail": "<human_readable_ru>"
}
```

| Status | `error` slug             | Meaning                                                              |
|--------|--------------------------|----------------------------------------------------------------------|
| `400`  | `bad_request`            | Malformed body, missing field, invalid type.                         |
| `401`  | `unauthorized`           | `require_init_data` rejected the signature.                          |
| `413`  | `photo_too_large`        | Photo bytes exceed the 10 MiB ceiling.                               |
| `415`  | `unsupported_media_type` | Photo content-type not in {`image/jpeg`,`image/png`,`image/webp`}.   |
| `422`  | `food_not_recognized`    | Ayla returned `FOOD_NOT_RECOGNIZED`. Frontend → manual entry CTA.    |
| `428`  | `consent_required`       | `food_scanner_consent_at` is NULL. Frontend → F0 consent gate.       |
| `502`  | `ayla_error`             | Other Ayla 4xx/unexpected response — surfaced as a transient error.  |
| `503`  | `nutrition_disabled`     | `NUTRITION_ENABLED=False`. Frontend → feature-off card.              |
| `503`  | `photo_scan_disabled`    | `FOOD_PHOTO_SCAN_ENABLED=False`. Frontend → manual-entry hint.       |
| `503`  | `ayla_unavailable`       | Ayla circuit open / timeout / network. Frontend → retry-later card.  |

The frontend matches on the slug, never on the human text.

---

## ED-mode redaction

When the customer's nutrition profile signals an eating-disorder
context, **the backend** strips all numeric nutrition fields from
responses BEFORE the wire — defence-in-depth so a client bug or
older Mini App version cannot accidentally render counts.

**Source of truth (OR of two signals):**

- `ProfileResponse.goal_overridden_by == "eating_disorder"` — server
  applied the goal override.
- `ProfileResponse.health_flags.eating_disorder == true` — user
  declared the condition in the anketa.

If **either** is true, ED-mode is active. Every food endpoint
(`scan`, `log`, `diary`) replaces the redacted fields with `null`
and adds the marker:

```json
{ "ed_mode": true,  ... fields below with nutrition zeroed ... }
```

When ED-mode is inactive the field is `false` and the numeric fields
carry their real values.

**Redacted fields (replaced with `null` and `ed_mode=true`):**

| Endpoint        | Redacted fields                                                                  |
|-----------------|----------------------------------------------------------------------------------|
| `food/scan`     | `nutrition` (whole object → `null`)                                              |
| `food/log`      | `calories`                                                                       |
| `food/diary`    | `calories_total`, `calories_goal`, `protein_g`, `fat_g`, `carbs_g`; each entry's `nutrition`; `ai_comment` (prose may embed counts) |

`dish_name`, `portion_g`, timestamps, meal types — preserved
(this is the «neutral meal log» the founder asked for).

### Nutrition-payload allowlist

The `nutrition` object — wherever it appears (`food/scan`,
`food/diary` entries) — is server-filtered to the explicit
allowlist `{calories, protein_g, fat_g, carbs_g}` **before** going
to the wire, regardless of ED-mode. If Ayla adds new keys later
(`deficit_surplus`, `score`, `kcal_from_beverages`, …) they are
dropped server-side until this contract is amended. The numeric
examples below are illustrative — actual values come from Ayla
and are rounded half-to-even.

---

## Endpoints

### `POST /api/v1/customer/food/consent`

Records the user's 152-ФЗ acknowledgement. Bypasses the nutrition
gate so the F0 frontend gate can call this before anything else.

**Request body:**

```json
{ "accepted": true }
```

`accepted=true` sets `BotUser.food_scanner_consent_at = now()` if it
is currently NULL (idempotent — re-calls with `true` are no-ops, the
original timestamp is preserved). `accepted=false` is reserved for a
future withdrawal flow (currently returns `400 bad_request` — the
F0 gate has no «refuse» button).

**Response 200:**

```json
{ "accepted_at": "2026-06-02T08:12:39.444Z" }
```

ISO 8601 UTC timestamp. Always reflects the column value, not the
request time, so re-reads after a re-call are stable.

---

### `GET /api/v1/customer/food/consent`

Polls the consent state. Used by the Mini App on mount to decide
whether to surface F0 or skip straight to F1.

**Response 200:**

```json
{ "accepted_at": "2026-06-02T08:12:39.444Z" }
```

or

```json
{ "accepted_at": null }
```

`null` means the user has not yet consented.

---

### `POST /api/v1/customer/food/scan`

Cross-border photo recognition. **Gated by both flags + consent.**

**Request:** `multipart/form-data` with field `image` carrying the
photo bytes. Optional `portion_multiplier` form field (float
`0.25…4.0`) for «½ porции / 2× porции» buttons.

**Headers:** `Content-Type: multipart/form-data; boundary=…`
(standard browser fetch form upload).

**Photo constraints:**

- Max size: **10 MiB** (server enforces; client should pre-validate
  to avoid the round-trip).
- Allowed MIME types: `image/jpeg`, `image/png`, `image/webp`.
- EXIF metadata: client SHOULD strip (W1 does per #957); the server
  performs a defence-in-depth strip in Веха 3.

**Response 200 (ED inactive):**

```json
{
  "scan_id": "ed4f6e3a-…",
  "dish_name": "Борщ",
  "confidence": 0.86,
  "portion_g": 320,
  "nutrition": {
    "calories": 250,
    "protein_g": 12,
    "fat_g": 8,
    "carbs_g": 32
  },
  "provider": "openai-gpt-4o",
  "ed_mode": false
}
```

**Response 200 (ED active):**

```json
{
  "scan_id": "ed4f6e3a-…",
  "dish_name": "Борщ",
  "confidence": 0.86,
  "portion_g": 320,
  "nutrition": null,
  "provider": "openai-gpt-4o",
  "ed_mode": true
}
```

**Failure mapping:**

- `503 photo_scan_disabled` — feature gate.
- `503 nutrition_disabled` — master gate.
- `428 consent_required` — consent missing.
- `400 bad_request` — missing `image`, malformed multipart.
- `413 photo_too_large` — exceeds 10 MiB.
- `415 unsupported_media_type` — content-type not allowed.
- `422 food_not_recognized` — Ayla `FOOD_NOT_RECOGNIZED`.
- `503 ayla_unavailable` — circuit / network / 5xx.
- `502 ayla_error` — other Ayla 4xx.

---

### `POST /api/v1/customer/food/log`

Records an explicit food log entry — both photo-scan confirmations
(Mini App passes `scan_id`) and manual entries (Mini App passes the
free-form fields). RU-side, no cross-border traffic; gated by master
switch + consent only.

**Request body (scan confirmation):**

```json
{
  "scan_id": "ed4f6e3a-…",
  "meal_type": "lunch"
}
```

**Request body (manual entry):**

```json
{
  "dish_name": "Овсянка с ягодами",
  "meal_type": "breakfast",
  "portion_multiplier": 1.0
}
```

`meal_type` ∈ `{breakfast, lunch, dinner, snack, other}`. Default is
`other`. Either `scan_id` OR `dish_name` must be present — `400
bad_request` if neither. `portion_multiplier` is optional, default
`1.0`, accepted range `[0.25, 4.0]`; the backend forwards it to
Ayla which scales the canonical portion size for that dish. The
Mini App does NOT pass a raw gram count — Ayla owns portion sizing.

**Response 200 (ED inactive):**

```json
{
  "log_id": "f3b7…",
  "dish_name": "Борщ",
  "meal_type": "lunch",
  "calories": 250,
  "ed_mode": false
}
```

**Response 200 (ED active):**

```json
{
  "log_id": "f3b7…",
  "dish_name": "Борщ",
  "meal_type": "lunch",
  "calories": null,
  "ed_mode": true
}
```

**Failure mapping:**

- `503 nutrition_disabled` — master gate.
- `428 consent_required` — consent missing.
- `400 bad_request` — neither `scan_id` nor manual fields.
- `503 ayla_unavailable` / `502 ayla_error` — passthrough.

**Idempotency:** the backend derives an idempotency key from
`(bot_user_id, scan_id or manual-payload-hash, meal_type)` and
forwards it to Ayla so re-taps return the same `log_id`.

---

### `GET /api/v1/customer/food/diary?date=YYYY-MM-DD`

Day's food log + daily roll-up. Used by the Mini App `/дневник`
screen and the dashboard summary card.

**Query:**

- `date` — optional. Format `YYYY-MM-DD` in the customer's timezone
  (`BotUser.timezone`, defaults to `Europe/Moscow`). Defaults to
  today in that timezone when omitted. Invalid → `400 bad_request`.

**Response 200 (ED inactive):**

```json
{
  "date": "2026-06-02",
  "calories_total": 1240,
  "calories_goal": 2100,
  "protein_g": 65,
  "fat_g": 40,
  "carbs_g": 121,
  "ai_comment": "Хорошее утро!",
  "entries": [
    {
      "log_id": "f3b7…",
      "dish_name": "Овсянка с ягодами",
      "meal_type": "breakfast",
      "logged_at": "2026-06-02T07:25:00Z",
      "nutrition": {
        "calories": 320,
        "protein_g": 9,
        "fat_g": 7,
        "carbs_g": 56
      }
    }
  ],
  "ed_mode": false
}
```

**Response 200 (ED active):**

```json
{
  "date": "2026-06-02",
  "calories_total": null,
  "calories_goal": null,
  "protein_g": null,
  "fat_g": null,
  "carbs_g": null,
  "ai_comment": null,
  "entries": [
    {
      "log_id": "f3b7…",
      "dish_name": "Овсянка с ягодами",
      "meal_type": "breakfast",
      "logged_at": "2026-06-02T07:25:00Z",
      "nutrition": null
    }
  ],
  "ed_mode": true
}
```

All numeric fields → `null`; `ai_comment` dropped because its
free-form prose can embed counts; meal log timestamps + names
preserved.

---

### `GET /api/v1/customer/health-flags`

Cross-cutting profile signal — used by the Mini App to pre-style
nutrition/wellness surfaces (hide vs show calories) without needing
to interpret a scan response first. Cheap O(1) call backed by the
Ayla profile read.

**Gates:** master switch + consent. `FOOD_PHOTO_SCAN_ENABLED` is
**not** required (this read does not cross the border).

**Response 200:**

```json
{
  "eating_disorder": true,
  "pregnancy": false,
  "breastfeeding": false,
  "ed_mode": true
}
```

- `eating_disorder` — OR of the two profile signals (see ED-mode
  section above).
- `pregnancy` / `breastfeeding` — pulled from
  `ProfileResponse.health_flags` for the Mini App's «безопасные
  советы» surface (Veха 2 ships read-only; consumer logic is W1).
- `ed_mode` — alias of `eating_disorder` so the Mini App can use
  the same field name across all four food endpoints.

If the profile read fails (Ayla unavailable, profile not yet
created), the endpoint returns `503 ayla_unavailable`. The frontend
should treat ED as **inactive** in that case ONLY for the styling
layer; food endpoints will still enforce their own redaction.

---

## Field-level invariants (frontend-grade contract)

1. Every food endpoint's success response carries the `ed_mode`
   boolean. The frontend reads this flag — never the per-field
   nullability — to decide rendering mode.
2. `scan_id` is server-issued and opaque (UUID-shaped); the frontend
   never parses it.
3. `meal_type` enum values are lowercase Latin and stable across
   versions. Localization happens client-side.
4. Timestamps are ISO 8601 UTC with `Z` suffix. The frontend converts
   to `BotUser.timezone` for display.
5. `confidence` is a float in `[0, 1]`. The frontend can use the
   `< 0.6` threshold to hedge phrasing (mirroring the MAX-channel
   skill behaviour).

---

## Out of scope for Веха 2

- Water tracking endpoints (`/customer/water/*`) — folded into the
  pre-existing `/customer/wellness/today` composition for now.
- `/дневник` bot command handler — Веха 3.
- Server-side EXIF strip — Веха 3 (W1 client already strips per #957;
  Веха 3 wires the backstop).
- Withdrawal of consent (DELETE shape) — post-pilot.
- Cross-domain insights / nudges / reports — Phase 2.

---

## Change log

- **2026-06-02** — V1 published with Веха 2 PR. Locked for W1 swap.
