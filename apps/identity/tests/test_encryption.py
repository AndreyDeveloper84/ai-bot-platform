"""MemoryEntry.content encryption test (spec §13 row 14.4).

Per ADR-0006 + spec §1 + ADR-0011 §6: `MemoryEntry.content` uses
`django-cryptography-django5` `encrypt(JSONField)` — Fernet-encrypted at rest.

Verify: raw DB column contains ciphertext (NOT readable plaintext JSON).
ORM access returns decrypted plaintext.
"""

from __future__ import annotations

import uuid

import pytest
from django.db import connection

from apps.identity.models import MemoryEntry, UserPersonalContext

pytestmark = pytest.mark.django_db


class TestMemoryEntryContentEncryption:
    def test_content_round_trips_through_orm(self):
        """ORM-level: stored and retrieved JSON content is identical."""
        upc = UserPersonalContext.objects.create(user_id=uuid.uuid4())
        original = {"diet": "vegetarian", "allergies": ["nuts", "shellfish"], "n": 42}
        entry = MemoryEntry.objects.create(
            user_id=upc.user_id,
            personal_context=upc,
            sensitivity_zone="green",
            source="explicit",
            content=original,
        )
        entry.refresh_from_db()
        assert entry.content == original

    def test_raw_db_column_is_not_plaintext_json(self):
        """Raw DB read shows ciphertext, NOT plaintext JSON.

        Test 14.4 — verifies that someone reading the raw column (via
        `pg_dump`, raw SQL, OS-level db file inspection) cannot recover
        the JSON without the Fernet key. The ciphertext should NOT contain
        the plaintext strings.

        Note on entry.pk: we look up the row by user_id (UPC PK == user_id;
        MemoryEntry is keyed by UUID but we filter via the FK to avoid
        any UUID-string mismatch on the raw SELECT).
        """
        upc = UserPersonalContext.objects.create(user_id=uuid.uuid4())
        original = {
            "marker_alpha": "PLAINTEXT_LEAK_CANARY_AAA",
            "marker_beta": "PLAINTEXT_LEAK_CANARY_BBB",
        }
        entry = MemoryEntry.objects.create(
            user_id=upc.user_id,
            personal_context=upc,
            sensitivity_zone="green",
            source="explicit",
            content=original,
        )

        # Read the raw column bypassing the ORM's decryption. Test DB is
        # isolated per test (pytest-django flushes), so we expect exactly
        # one row — no WHERE needed.
        with connection.cursor() as cur:
            cur.execute("SELECT content FROM identity_memoryentry LIMIT 1")
            row = cur.fetchone()

        assert row is not None, f"Entry created (pk={entry.pk}) but raw SELECT returned no row."
        raw_value = row[0]

        # The raw value is bytes (encrypted blob) OR a base64-encoded string,
        # depending on django-cryptography backend config + DB driver. Either
        # way it MUST NOT contain our plaintext markers.
        if isinstance(raw_value, (bytes, bytearray, memoryview)):
            raw_str = bytes(raw_value).decode("latin-1", errors="ignore")
        else:
            raw_str = str(raw_value)

        assert "PLAINTEXT_LEAK_CANARY_AAA" not in raw_str, (
            f"Plaintext marker found in raw DB column — encryption is NOT "
            f"applied. Raw value (first 200 chars): {raw_str[:200]!r}"
        )
        assert "PLAINTEXT_LEAK_CANARY_BBB" not in raw_str

    def test_default_empty_dict_round_trips(self):
        """The default `{}` content round-trips correctly through encryption."""
        upc = UserPersonalContext.objects.create(user_id=uuid.uuid4())
        entry = MemoryEntry.objects.create(
            user_id=upc.user_id,
            personal_context=upc,
            sensitivity_zone="green",
            source="explicit",
            # Don't pass content — should default to {}
        )
        entry.refresh_from_db()
        assert entry.content == {}
