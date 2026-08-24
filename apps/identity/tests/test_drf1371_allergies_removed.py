"""DRF-1371 — `UserPreferences.allergies` must not exist anywhere.

Owner decision 2026-08-25, verbatim: «мастеру противопоказания видеть не
должен». The column was free-text health data — a special category under
152-ФЗ ст. 10 — stored in a plain ``TextField`` outside ``MemoryEntry``'s
zone / consent / TTL machinery, under a caption that promised the value
reached the master. No code on the master side ever read it, so the
promise was untrue and the collection had no realised purpose (ст. 5 ч. 2)
on top of having no lawful basis (ст. 10).

These tests are the proof the field is gone from every surface a value
could enter or leave by, and that the migration is reversible:

* the model                                  — :class:`TestModel`
* the profile snapshot + PATCH allowlist     — :class:`TestProfileService`
* the ``GET``/``PATCH /api/v1/me`` payloads  — :class:`TestMeEndpoint`
* the Mini App screen and its API types      — :class:`TestMiniAppSources`
* the personal-field registry (DRF-1365)     — :class:`TestPersonalFieldRegistry`
* migration 0020 apply + rollback            — :class:`TestMigration`

:class:`TestBordersHeld` pins the two things this change deliberately did
NOT do: ``ConsentType.HEALTH`` stays declared, and the DRF-1290 extraction
ban stays in force.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time as time_module
import uuid
from pathlib import Path
from urllib.parse import urlencode

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import Client
from django.urls import reverse

from apps.identity.models import BotUser, UserPreferences
from apps.identity.services.profile import (
    ProfileUpdateError,
    get_profile,
    update_profile,
)
from apps.tenancy.models import Tenant


pytestmark = pytest.mark.django_db

_REPO_ROOT = Path(__file__).resolve().parents[3]
BOT_TOKEN = "test-bot-token-drf1371"


# --------------------------------------------------------------------------
# Fixtures / helpers
# --------------------------------------------------------------------------


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="drf1371", name="DRF-1371", timezone="Europe/Moscow")


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="12345",
        chat_id="12345",
        ayla_user_id=uuid.uuid4(),
        display_name="Мария",
        client_name="Мария Иванова",
        phone="+79991234567",
    )


@pytest.fixture
def _bot_token(settings) -> None:
    settings.MAX_BOT_TOKEN = BOT_TOKEN
    settings.MAX_BOT_TENANT_SLUG = "drf1371"


def _init_data_header(user_id: str = "12345") -> str:
    params = {
        "user": json.dumps({"id": int(user_id), "first_name": "Мария"}),
        "auth_date": str(int(time_module.time())),
    }
    data_check_string = "\n".join(f"{k}={params[k]}" for k in sorted(params))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return f"MaxInitData {urlencode({**params, 'hash': digest}, doseq=False)}"


def _read(*parts: str) -> str:
    return (_REPO_ROOT.joinpath(*parts)).read_text(encoding="utf-8")


# --------------------------------------------------------------------------


class TestModel:
    def test_field_is_gone_from_the_model(self) -> None:
        names = {f.name for f in UserPreferences._meta.get_fields()}
        assert "allergies" not in names

    def test_no_column_in_the_table(self) -> None:
        """Schema-level, not just Python-level: the column itself is dropped."""
        with connection.cursor() as cur:
            columns = {
                c.name
                for c in connection.introspection.get_table_description(
                    cur, UserPreferences._meta.db_table
                )
            }
        assert "allergies" not in columns

    def test_no_help_text_still_promises_the_master(self) -> None:
        """The false promise lived in help_text as well as in the UI caption."""
        for field in UserPreferences._meta.fields:
            assert "аллерг" not in str(field.help_text).lower()
            assert "allerg" not in str(field.help_text).lower()


class TestProfileService:
    def test_snapshot_has_no_allergies_key(self, bot_user) -> None:
        snap = get_profile(bot_user)
        assert "allergies" not in snap.preferences
        # The rest of F4 is untouched — this is a removal, not a rewrite.
        assert set(snap.preferences) == {
            "notify_reminders",
            "notify_retention",
            "notify_promo",
            "notify_birthday",
            "birthday_date",
        }

    def test_patching_allergies_is_rejected_loudly(self, bot_user) -> None:
        """Not silently dropped — a writer must learn the field is gone."""
        with pytest.raises(ProfileUpdateError) as exc:
            update_profile(bot_user, {"allergies": "аллергия на латекс"})
        assert "allergies" in str(exc.value)

    def test_rejection_does_not_write_anything(self, bot_user) -> None:
        with pytest.raises(ProfileUpdateError):
            update_profile(bot_user, {"notify_promo": True, "allergies": "латекс"})
        prefs = UserPreferences.all_tenants.filter(bot_user_id=bot_user.id).first()
        # Unknown keys are checked before any write, so the legal key in the
        # same body must not have landed either.
        assert prefs is None or prefs.notify_promo is False

    def test_ordinary_preferences_still_save(self, bot_user) -> None:
        snap = update_profile(bot_user, {"notify_promo": True, "birthday_date": "1990-05-17"})
        assert snap.preferences["notify_promo"] is True
        assert snap.preferences["birthday_date"] == "1990-05-17"


class TestMeEndpoint:
    """No `allergies` anywhere in the bytes the Mini App actually receives."""

    def test_get_me_response_has_no_allergies(self, _bot_token, bot_user) -> None:
        resp = Client().get(
            reverse("miniapp_api:me"),
            HTTP_AUTHORIZATION=_init_data_header(),
        )
        assert resp.status_code == 200
        assert "allergies" not in resp.content.decode("utf-8")
        assert "allergies" not in json.loads(resp.content)["preferences"]

    def test_patch_me_rejects_allergies(self, _bot_token, bot_user) -> None:
        resp = Client().patch(
            reverse("miniapp_api:me"),
            data=json.dumps({"allergies": "аллергия на латекс"}),
            content_type="application/json",
            HTTP_AUTHORIZATION=_init_data_header(),
        )
        assert resp.status_code == 400
        # And nothing was persisted under any name.
        prefs = UserPreferences.all_tenants.filter(bot_user_id=bot_user.id).first()
        if prefs is not None:
            assert "латекс" not in json.dumps(
                {k: str(v) for k, v in prefs.__dict__.items() if not k.startswith("_")},
                ensure_ascii=False,
            )


class TestMiniAppSources:
    """The screen and the client types, read as source.

    A vitest run proves the rendered screen; this proves the strings are
    not in the repo at all, which is what stops the caption coming back in
    a copy-paste.
    """

    def test_profile_screen_has_no_allergies_input(self) -> None:
        src = _read("apps", "miniapp", "src", "screens", "ProfileScreen.tsx")
        assert "Аллергии" not in src
        assert "Передадим мастеру" not in src
        assert "preferences.allergies" not in src

    def test_no_screen_references_preferences_allergies(self) -> None:
        # Property access / object key / type member — not the word inside a
        # comment or a filename, which is how this very test is referenced.
        # `*.test.tsx` is out of scope on purpose: ProfileScreen.test.tsx
        # asserts the key is never sent, and a fixture that re-added it
        # would fail `tsc --noEmit` against the `Preferences` type anyway.
        needles = (
            ".allergies",
            "allergies:",
            "allergies?",
            "[allergies",
            "'allergies'",
            '"allergies"',
        )
        src_root = _REPO_ROOT / "apps" / "miniapp" / "src"
        offenders = [
            str(p.relative_to(_REPO_ROOT))
            for p in src_root.rglob("*.ts*")
            if ".test." not in p.name
            if any(n in p.read_text(encoding="utf-8") for n in needles)
        ]
        assert offenders == []

    def test_api_types_have_no_allergies(self) -> None:
        src = _read("apps", "miniapp", "src", "lib", "api.ts")
        # The comment naming the ticket is allowed; a type member is not.
        assert "allergies: string" not in src


class TestPersonalFieldRegistry:
    """DRF-1365's guard reads this registry; a stale entry is a CI failure."""

    def test_entry_is_removed(self) -> None:
        from apps.identity.personal_fields import NOT_PERSONAL, PERSONAL_FIELDS

        key = "identity.UserPreferences.allergies"
        assert key not in {f.site for f in PERSONAL_FIELDS}
        assert key not in NOT_PERSONAL

    def test_registry_and_model_agree(self) -> None:
        """No declaration survives the field it described."""
        from apps.identity.personal_fields import PERSONAL_FIELDS

        declared = {
            f.site.rsplit(".", 1)[1]
            for f in PERSONAL_FIELDS
            if f.site.startswith("identity.UserPreferences.")
        }
        actual = {f.name for f in UserPreferences._meta.get_fields()}
        assert declared <= actual


class TestMigration:
    """0020 drops the column and `migrate identity 0019` puts it back."""

    _PREV = ("identity", "0019_memoryentry_lifecycle_constraints")
    _HEAD = ("identity", "0020_drop_userpreferences_allergies")

    @pytest.mark.django_db(transaction=True)
    def test_rollback_and_reapply(self) -> None:
        # Fresh executor per migrate(): the loader caches applied state at
        # init, so a reused one mis-plans the second leg. The finally always
        # returns the test DB to head — later tests write through the
        # runtime model, which has no `allergies`.
        try:
            executor = MigrationExecutor(connection)
            executor.migrate([self._PREV])
            old = executor.loader.project_state([self._PREV]).apps.get_model(
                "identity", "UserPreferences"
            )
            assert "allergies" in {f.name for f in old._meta.fields}, (
                "rollback must restore the column, otherwise a deploy cannot "
                "be undone without a hand-written migration"
            )

            executor = MigrationExecutor(connection)
            executor.migrate([self._HEAD])
            new = executor.loader.project_state([self._HEAD]).apps.get_model(
                "identity", "UserPreferences"
            )
            assert "allergies" not in {f.name for f in new._meta.fields}
        finally:
            MigrationExecutor(connection).migrate([self._HEAD])

    def test_no_model_changes_left_unmigrated(self) -> None:
        """`makemigrations --check` for identity, in-process."""
        from django.apps import apps as django_apps
        from django.db.migrations.autodetector import MigrationAutodetector
        from django.db.migrations.loader import MigrationLoader
        from django.db.migrations.questioner import NonInteractiveMigrationQuestioner
        from django.db.migrations.state import ProjectState

        loader = MigrationLoader(None, ignore_no_migrations=True)
        autodetector = MigrationAutodetector(
            loader.project_state(),
            ProjectState.from_apps(django_apps),
            NonInteractiveMigrationQuestioner(specified_apps={"identity"}),
        )
        changes = autodetector.changes(graph=loader.graph, trim_to_apps={"identity"})
        assert "identity" not in changes, f"unmigrated identity changes: {changes}"


class TestBordersHeld:
    """What this change was explicitly not allowed to touch."""

    def test_consent_type_health_still_declared(self) -> None:
        """Kept as headroom: a master seeing contraindications is a new feature."""
        from apps.consent.models import ConsentRecord

        assert ConsentRecord.ConsentType.HEALTH.value == "health"

    def test_drf1290_extraction_ban_still_in_force(self) -> None:
        """Chat phrases about allergies are still dropped before memory."""
        src = _read("apps", "persona", "memory_extract.py")
        assert "_ALLERGY_RE" in src and "аллерг" in src.lower(), (
            "DRF-1290 drops allergy phrases at extraction; removing the "
            "profile field makes that ban consistent, it does not lift it"
        )
