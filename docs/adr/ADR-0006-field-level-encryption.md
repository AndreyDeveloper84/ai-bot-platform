# ADR-0006: Field-level encryption via `django-cryptography-django5`

**Status:** Accepted — 2026-05-09 (added in PHASE0_DESIGN.md v2)

## Context

Tenant-level secrets — channel bot tokens, OpenAI API keys, third-party integration credentials — must be encrypted at rest. Postgres' `pgcrypto` extension works at the SQL layer but doesn't integrate cleanly with the Django ORM (manual `RawSQL`, awkward for JSONB). Django Field-level encryption via Fernet (AES-128 + HMAC-SHA256) gives us transparent encryption with key rotation, while keeping the ORM ergonomic.

## Decision

Use `django-cryptography-django5==2.2`. Add `EncryptedJSONField` for:

- `Tenant.channel_tokens` — MAX/Telegram/Web bot secrets.
- `Tenant.openai_api_key` — when per-tenant key support arrives in Phase 1.
- Future: any per-tenant credential.

The encryption key is the Django setting `CRYPTOGRAPHY_KEY` (the name `django-cryptography` reads), set from the environment variable `DJANGO_CRYPTOGRAPHY_KEY` (secret manager) in `config/settings/base.py`. The library derives the Fernet key as `PBKDF2(CRYPTOGRAPHY_KEY or SECRET_KEY)`.

> **⚠ `SECRET_KEY` on a server with data is NOT rotated.** Today rotating it loses every encrypted value, and `DJANGO_CRYPTOGRAPHY_KEY` does not change that. Each encrypted value depends on `SECRET_KEY` twice:
> 1. the AES key — `PBKDF2(CRYPTOGRAPHY_KEY or SECRET_KEY)` (`django_cryptography/conf.py`) — decoupled by setting `DJANGO_CRYPTOGRAPHY_KEY` (DRF-2555);
> 2. the HMAC signature — `FernetSigner` uses the **raw** `settings.SECRET_KEY` (`django_cryptography/core/signing.py`), and `decrypt()` checks it **first**. No setting decouples it; the field builds `FernetBytes(key)` with the default signer. Decoupling is DRF-2562 (a signer with its own key, or a library with multi-key support and re-encryption — a decision to take before that change).
>
> **Correction (DRF-2555, 2026-09-26).** Until DRF-2555 this paragraph said the key lived in `settings.DJANGO_CRYPTOGRAPHY_KEY` — a setting nothing read — and no `CRYPTOGRAPHY_KEY` was set, so the AES key was derived from `SECRET_KEY` and a leak of one was a leak of the other. It also claimed rotation via Fernet's multi-key bundle: `django-cryptography` holds a single `KEY`; multi-key rotation is **not implemented**. The first value of `DJANGO_CRYPTOGRAPHY_KEY` on a server with data must be the **current** `SECRET_KEY` value, so the derived AES key stays identical and existing rows stay readable. Nodes: `apps/identity/tests/test_crypto_key_from_env_2555.py` pin both dependencies — the first gone, the second alive.

Audit logging in `apps.audit` captures token *fingerprints* (SHA-256 of the value), never plaintext.

## Sprint 0 correction to the original draft

The original draft (PHASE0_DESIGN.md v1) named the package `django-cryptography==2.2`. That package on PyPI is **abandoned at version 1.1** and does not support Django 5. The actively-maintained Django-5 fork ships under the name `django-cryptography-django5`. Same code lineage, same `Fernet`-backed API; only the install name changes. Caught and corrected during DRF-405 (`uv lock` failed on the abandoned package).

## Consequences

- **Easier:** ORM stays ergonomic — fields look like normal `JSONField`.
- **Easier:** key rotation is supported via Fernet's multi-key bundle.
- **Acceptable:** small CPU overhead per read/write (~microseconds per field).
- **Harder:** backup/restore must include the key material — without it, the data is unrecoverable. Mitigated by storing the key in the secret manager + a documented runbook.
- **Harder:** tests do not set `DJANGO_CRYPTOGRAPHY_KEY`; the key is then derived from the test `SECRET_KEY`, which is deterministic, so this is invisible day-to-day. (Corrected in DRF-2555: there is no separate test-fixture key.) `apps/identity/tests/test_crypto_key_from_env_2555.py` pins both paths — with and without the variable.

## Alternatives considered

- **`pgcrypto`.** Rejected. Leaks plaintext through the ORM unless every read/write is wrapped manually; awkward for JSONB.
- **AWS KMS / Yandex Lockbox secret references.** Rejected for Phase 0 — extra moving part and DPA effort. Reconsider in Phase 2 when multi-region.
- **Vault sidecar.** Rejected. Operational complexity disproportionate to one-tenant scale.
