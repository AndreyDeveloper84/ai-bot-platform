# ADR-0006: Field-level encryption via `django-cryptography-django5`

**Status:** Accepted — 2026-05-09 (added in PHASE0_DESIGN.md v2)

## Context

Tenant-level secrets — channel bot tokens, OpenAI API keys, third-party integration credentials — must be encrypted at rest. Postgres' `pgcrypto` extension works at the SQL layer but doesn't integrate cleanly with the Django ORM (manual `RawSQL`, awkward for JSONB). Django Field-level encryption via Fernet (AES-128 + HMAC-SHA256) gives us transparent encryption with key rotation, while keeping the ORM ergonomic.

## Decision

Use `django-cryptography-django5==2.2`. Add `EncryptedJSONField` for:

- `Tenant.channel_tokens` — MAX/Telegram/Web bot secrets.
- `Tenant.openai_api_key` — when per-tenant key support arrives in Phase 1.
- Future: any per-tenant credential.

The encryption key lives in `settings.DJANGO_CRYPTOGRAPHY_KEY`, sourced from the environment / secret manager. Key rotation uses Fernet's multi-key bundle (read with any historical key, write with the current).

Audit logging in `apps.audit` captures token *fingerprints* (SHA-256 of the value), never plaintext.

## Sprint 0 correction to the original draft

The original draft (PHASE0_DESIGN.md v1) named the package `django-cryptography==2.2`. That package on PyPI is **abandoned at version 1.1** and does not support Django 5. The actively-maintained Django-5 fork ships under the name `django-cryptography-django5`. Same code lineage, same `Fernet`-backed API; only the install name changes. Caught and corrected during DRF-405 (`uv lock` failed on the abandoned package).

## Consequences

- **Easier:** ORM stays ergonomic — fields look like normal `JSONField`.
- **Easier:** key rotation is supported via Fernet's multi-key bundle.
- **Acceptable:** small CPU overhead per read/write (~microseconds per field).
- **Harder:** backup/restore must include the key material — without it, the data is unrecoverable. Mitigated by storing the key in the secret manager + a documented runbook.
- **Harder:** developers writing tests must remember to set `DJANGO_CRYPTOGRAPHY_KEY` (or use the test-fixture key). Default test settings ship with a deterministic key so this is invisible day-to-day.

## Alternatives considered

- **`pgcrypto`.** Rejected. Leaks plaintext through the ORM unless every read/write is wrapped manually; awkward for JSONB.
- **AWS KMS / Yandex Lockbox secret references.** Rejected for Phase 0 — extra moving part and DPA effort. Reconsider in Phase 2 when multi-region.
- **Vault sidecar.** Rejected. Operational complexity disproportionate to one-tenant scale.
