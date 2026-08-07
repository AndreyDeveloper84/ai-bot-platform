"""C5 privacy tests — export aggregation + delete cascade + the two views.

The Ayla wire is stubbed at the client level. Covers the frozen-contract
obligations: one-JSON export with attachment, idempotent delete cascade
(repeat → 200), per-step honest partials (502), audit rows without
personal values, consent/memory_green interplay.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time as time_module
import uuid
from datetime import date
from urllib.parse import urlencode

import pytest
from django.test import Client as DjangoClient

from apps.consent.models import ConsentRecord
from apps.identity.models import BotUser, MemoryEntry
from apps.identity.services.memory_inferred import (
    InferredGreenFact,
    record_inferred_green_facts,
)
from apps.identity.services.privacy import (
    PrivacyUpstreamError,
    delete_personal_data,
    export_personal_data,
)
from apps.identity.services.profile import DELETE_CONFIRMATION_TOKEN
from apps.integrations.ayla.personal_context_client import (
    PersonalContextNotFoundError,
    PersonalContextTransportError,
)
from apps.tenancy.models import Tenant


pytestmark = pytest.mark.django_db

BOT_TOKEN = "test-bot-token-xyz"
CT = ConsentRecord.ConsentType


def _sign(params: dict[str, str]) -> str:
    data_check_string = "\n".join(f"{k}={params[k]}" for k in sorted(params))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode({**params, "hash": digest}, doseq=False)


def _init_data_header(user_id: str = "12345") -> str:
    params = {
        "user": json.dumps({"id": int(user_id), "first_name": "Мария"}),
        "auth_date": str(int(time_module.time())),
    }
    return f"MaxInitData {_sign(params)}"


@pytest.fixture(autouse=True)
def _bot_token(settings) -> None:
    settings.MAX_BOT_TOKEN = BOT_TOKEN
    # miniapp_api auth resolves the request tenant by this slug.
    settings.MAX_BOT_TENANT_SLUG = "priv-test"


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="priv-test", name="Privacy Test")


@pytest.fixture
def ayla_user_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def bot_user(tenant, ayla_user_id) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="12345",
        chat_id="12345",
        ayla_user_id=ayla_user_id,
    )


def _grant(bu: BotUser, ctype: str) -> ConsentRecord:
    return ConsentRecord.all_tenants.create(
        tenant=bu.tenant,
        bot_user=bu,
        consent_type=ctype,
        granted=True,
        source="test",
    )


def _delete_request(
    client: DjangoClient,
    *,
    user_id: str = "12345",
    confirmation: str | None = DELETE_CONFIRMATION_TOKEN,
):
    """DELETE the C5 endpoint. ``confirmation=None`` sends an empty JSON object
    (for a request with no body at all, call ``client.delete`` directly)."""
    body = {} if confirmation is None else {"confirmation": confirmation}
    return client.delete(
        "/api/v1/customer/me/personal-data/",
        data=json.dumps(body),
        content_type="application/json",
        HTTP_AUTHORIZATION=_init_data_header(user_id),
    )


def _seed_memory(bu: BotUser, ayla_user_id: uuid.UUID) -> None:
    _grant(bu, CT.PERSONAL_DATA)
    record_inferred_green_facts(
        bu,
        [InferredGreenFact(kind="diet", content={"key": "diet_type", "value": "vegan"})],
    )


class _StubPCClient:
    """Personal-context client stand-in (export/delete legs)."""

    def __init__(self, *, export_payload=None, delete_exc: Exception | None = None) -> None:
        self.export_payload = export_payload or {
            "profile": {"display_name": "Мария"},
            "context": {"diet_type": "vegan"},
        }
        self.delete_exc = delete_exc
        self.calls: list[tuple[str, str]] = []
        self.closed = False

    def get_personal_data_export(self, *, ayla_user_id: str):
        self.calls.append(("export", ayla_user_id))
        return self.export_payload

    def delete_personal_data(self, *, ayla_user_id: str) -> None:
        self.calls.append(("delete", ayla_user_id))
        if self.delete_exc:
            raise self.delete_exc

    def close(self) -> None:
        self.closed = True


class TestExport:
    def test_aggregates_three_sections(self, bot_user, ayla_user_id) -> None:
        _seed_memory(bot_user, ayla_user_id)
        _grant(bot_user, CT.MEMORY_GREEN)

        payload = export_personal_data(bot_user, client=_StubPCClient())  # type: ignore[arg-type]

        assert payload["subject"]["ayla_user_id"] == str(ayla_user_id)
        assert payload["ayla"]["profile"]["display_name"] == "Мария"
        assert [m["content"]["value"] for m in payload["memory"]] == ["vegan"]
        consent_types = {c["consent_type"] for c in payload["consents"]}
        assert {CT.PERSONAL_DATA, CT.MEMORY_GREEN} <= consent_types
        assert payload["generated_at"]

    def test_audit_row_written(self, bot_user, ayla_user_id) -> None:
        from apps.audit.models import AuditLog

        export_personal_data(bot_user, client=_StubPCClient())  # type: ignore[arg-type]

        log = AuditLog.all_tenants.filter(action="privacy.personal_data_exported").first()
        assert log is not None
        assert "vegan" not in json.dumps(log.payload)

    def test_upstream_failure_raises(self, bot_user) -> None:
        class _Failing(_StubPCClient):
            def get_personal_data_export(self, *, ayla_user_id: str):
                raise PersonalContextTransportError("http_500")

        with pytest.raises(PrivacyUpstreamError):
            export_personal_data(bot_user, client=_Failing())  # type: ignore[arg-type]

    def test_unlinked_user_exports_bot_side_only(self, tenant) -> None:
        bu = BotUser.all_tenants.create(
            tenant=tenant, channel="max", channel_user_id="777", ayla_user_id=None
        )

        payload = export_personal_data(tenant and bu)  # no client needed

        assert payload["subject"]["ayla_user_id"] is None
        assert payload["ayla"] is None
        assert payload["memory"] == []


class TestDelete:
    def test_full_cascade(self, bot_user, ayla_user_id) -> None:
        _seed_memory(bot_user, ayla_user_id)
        _grant(bot_user, CT.MEMORY_GREEN)
        client = _StubPCClient()

        result = delete_personal_data(bot_user, client=client)  # type: ignore[arg-type]

        assert result.all_ok
        assert client.calls == [("delete", str(ayla_user_id))]
        # Memory gone from the read surface.
        assert not MemoryEntry.objects.filter(
            user_id=ayla_user_id, soft_deleted_at__isnull=True
        ).exists()
        # Consents withdrawn (both bases).
        assert not ConsentRecord.all_tenants.filter(
            bot_user=bot_user, withdrawn_at__isnull=True
        ).exists()

    def test_idempotent_repeat(self, bot_user, ayla_user_id) -> None:
        _seed_memory(bot_user, ayla_user_id)
        client = _StubPCClient()

        first = delete_personal_data(bot_user, client=client)  # type: ignore[arg-type]
        second = delete_personal_data(bot_user, client=client)  # type: ignore[arg-type]

        assert first.all_ok and second.all_ok

    def test_upstream_404_counts_as_deleted(self, bot_user) -> None:
        client = _StubPCClient(delete_exc=PersonalContextNotFoundError("gone"))
        result = delete_personal_data(bot_user, client=client)  # type: ignore[arg-type]
        assert result.all_ok
        ayla_step = next(s for s in result.steps if s.step == "ayla_delete")
        assert ayla_step.detail == "already_deleted"

    def test_upstream_5xx_is_honest_partial(self, bot_user, ayla_user_id) -> None:
        _seed_memory(bot_user, ayla_user_id)
        client = _StubPCClient(delete_exc=PersonalContextTransportError("http_500"))

        result = delete_personal_data(bot_user, client=client)  # type: ignore[arg-type]

        assert not result.all_ok
        assert result.failed_steps == ["ayla_delete"]
        # Local steps still ran (user's legal right doesn't wait on upstream).
        assert not MemoryEntry.objects.filter(
            user_id=ayla_user_id, soft_deleted_at__isnull=True
        ).exists()

    def test_audit_has_no_values(self, bot_user, ayla_user_id) -> None:
        from apps.audit.models import AuditLog

        _seed_memory(bot_user, ayla_user_id)
        delete_personal_data(bot_user, client=_StubPCClient())  # type: ignore[arg-type]

        log = AuditLog.all_tenants.filter(action="privacy.personal_data_deleted").first()
        assert log is not None
        assert "vegan" not in json.dumps(log.payload)
        assert set(log.payload["scope"]) == {
            "ayla_delete",
            "memory_delete",
            "consent_withdraw",
            "profile_pii_erase",
        }


class TestViews:
    def test_export_attachment(
        self, client: DjangoClient, bot_user, ayla_user_id, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            "apps.identity.services.privacy.PersonalContextHttpClient",
            lambda: _StubPCClient(),
        )
        resp = client.get(
            "/api/v1/customer/me/personal-data/export/",
            HTTP_AUTHORIZATION=_init_data_header("12345"),
        )
        assert resp.status_code == 200
        assert resp["Content-Disposition"] == 'attachment; filename="personal-data-export.json"'
        body = resp.json()
        assert body["subject"]["ayla_user_id"] == str(ayla_user_id)

    def test_export_upstream_502(self, client: DjangoClient, bot_user, monkeypatch) -> None:
        class _Failing(_StubPCClient):
            def get_personal_data_export(self, *, ayla_user_id: str):
                raise PersonalContextTransportError("http_500")

        monkeypatch.setattr(
            "apps.identity.services.privacy.PersonalContextHttpClient",
            lambda: _Failing(),
        )
        resp = client.get(
            "/api/v1/customer/me/personal-data/export/",
            HTTP_AUTHORIZATION=_init_data_header("12345"),
        )
        assert resp.status_code == 502
        assert resp.json()["error"] == "upstream_unavailable"

    def test_delete_200_and_repeat(
        self, client: DjangoClient, bot_user, ayla_user_id, monkeypatch
    ) -> None:
        _seed_memory(bot_user, ayla_user_id)
        monkeypatch.setattr(
            "apps.identity.services.privacy.PersonalContextHttpClient",
            lambda: _StubPCClient(),
        )
        for _ in range(2):
            resp = _delete_request(client)
            assert resp.status_code == 200
            assert resp.json()["status"] == "deleted"

    def test_delete_partial_502(self, client: DjangoClient, bot_user, monkeypatch) -> None:
        monkeypatch.setattr(
            "apps.identity.services.privacy.PersonalContextHttpClient",
            lambda: _StubPCClient(delete_exc=PersonalContextTransportError("http_500")),
        )
        resp = _delete_request(client)
        assert resp.status_code == 502
        assert resp.json()["failed_steps"] == ["ayla_delete"]

    def test_auth_required(self, client: DjangoClient) -> None:
        assert client.get("/api/v1/customer/me/personal-data/export/").status_code in (
            400,
            401,
            403,
        )
        assert client.delete("/api/v1/customer/me/personal-data/").status_code in (
            400,
            401,
            403,
        )


# ---------------------------------------------------------------------------
# DRF-956 / T-05 — profile PII erasure on the confirmed Mini App path
# ---------------------------------------------------------------------------

_PII = {"phone": "+79991234567", "display_name": "Мария", "client_name": "Маша"}


def _with_pii(bu: BotUser) -> BotUser:
    """Give a shell the three confirmed-blocker PII values + an avatar."""
    for field_name, value in _PII.items():
        setattr(bu, field_name, value)
    bu.avatar_url = "https://cdn.example/u/mariya.jpg"
    # Real content in `context` too, so the "nothing survives" assertion
    # below is checking something rather than an always-empty dict.
    bu.context = {"referred_by_name": "Маша", "note": _PII["phone"]}
    bu.save(update_fields=[*_PII, "avatar_url", "context"])
    return bu


def _make_ai_metric(bu: BotUser):
    """A PROTECT-ing AIRequestMetric — every real user accumulates these."""
    from apps.observability.models import AIRequestMetric

    return AIRequestMetric.all_tenants.create(
        tenant=bu.tenant,
        bot_user=bu,
        request_id=uuid.uuid4(),
        message_text_length=12,
        latency_total_ms=250,
        outcome=AIRequestMetric.OUTCOME_SUCCESS,
    )


class TestProfilePiiErase:
    """The three fields DRF-956 confirmed survive today must not survive."""

    def test_confirmed_delete_erases_phone_name_client_name(self, bot_user) -> None:
        _with_pii(bot_user)

        result = delete_personal_data(bot_user, client=_StubPCClient())  # type: ignore[arg-type]

        assert result.all_ok
        bot_user.refresh_from_db()
        assert bot_user.phone == ""
        assert bot_user.display_name == ""
        assert bot_user.client_name == ""
        assert bot_user.avatar_url == ""

    def test_old_values_do_not_survive_anywhere_on_the_row(self, bot_user) -> None:
        """Not just "the fields are blank" — the literal old strings are gone."""
        _with_pii(bot_user)

        delete_personal_data(bot_user, client=_StubPCClient())  # type: ignore[arg-type]

        bot_user.refresh_from_db()
        row = json.dumps(
            {
                "phone": bot_user.phone,
                "display_name": bot_user.display_name,
                "client_name": bot_user.client_name,
                "avatar_url": bot_user.avatar_url,
                "context": bot_user.context,
            },
            ensure_ascii=False,
        )
        for value in (*_PII.values(), "mariya"):
            assert value not in row

    def test_in_memory_instance_is_scrubbed_too(self, bot_user) -> None:
        """The view renders from the instance — it must not hold stale PII."""
        _with_pii(bot_user)

        delete_personal_data(bot_user, client=_StubPCClient())  # type: ignore[arg-type]

        # No refresh_from_db() on purpose.
        assert bot_user.phone == ""
        assert bot_user.display_name == ""
        assert bot_user.client_name == ""

    def test_survives_protected_ai_request_metric(self, bot_user) -> None:
        """The DRF-956 blocker-A crash must not reappear on this path."""
        from apps.observability.models import AIRequestMetric

        _with_pii(bot_user)
        metric = _make_ai_metric(bot_user)

        result = delete_personal_data(bot_user, client=_StubPCClient())  # type: ignore[arg-type]

        assert result.all_ok
        # The metric row survives intact — forensic/billing integrity.
        metric.refresh_from_db()
        assert metric.bot_user_id == bot_user.id
        assert AIRequestMetric.all_tenants.filter(pk=metric.pk).exists()
        # And the shell it points at is still there, just empty.
        bot_user.refresh_from_db()
        assert bot_user.phone == ""

    def test_shell_row_is_retained(self, bot_user) -> None:
        _with_pii(bot_user)

        delete_personal_data(bot_user, client=_StubPCClient())  # type: ignore[arg-type]

        assert BotUser.all_tenants.filter(pk=bot_user.pk).exists()

    def test_ayla_binding_retained(self, bot_user, ayla_user_id) -> None:
        """ayla_user_id is the memory-tombstone key — erasure must keep it."""
        _with_pii(bot_user)

        delete_personal_data(bot_user, client=_StubPCClient())  # type: ignore[arg-type]

        bot_user.refresh_from_db()
        assert bot_user.ayla_user_id == ayla_user_id
        # Channel routing key of the retained shell also survives.
        assert bot_user.channel_user_id == "12345"

    def test_erases_every_shell_of_the_person_cross_tenant(self, bot_user, ayla_user_id) -> None:
        """One person, two tenants — the phone lives on both rows."""
        other = Tenant.objects.create(slug="priv-test-2", name="Other")
        sibling = _with_pii(
            BotUser.all_tenants.create(
                tenant=other,
                channel="max",
                channel_user_id="12345",
                ayla_user_id=ayla_user_id,
            )
        )
        _with_pii(bot_user)

        delete_personal_data(bot_user, client=_StubPCClient())  # type: ignore[arg-type]

        sibling.refresh_from_db()
        assert sibling.phone == ""
        assert sibling.display_name == ""
        assert sibling.client_name == ""

    def test_erases_unlinked_sibling_shell(self, bot_user) -> None:
        """The production case: sibling shell with ayla_user_id NULL.

        Nothing in production writes ``BotUser.ayla_user_id``, and the Mini
        App resolves a different tenant's shell than the chat does. Keyed on
        ``ayla_user_id`` alone this row would keep the phone while the API
        reported "deleted".
        """
        sentinel = Tenant.objects.create(slug="global-bot-like", name="Global")
        sibling = _with_pii(
            BotUser.all_tenants.create(
                tenant=sentinel,
                channel="max",
                channel_user_id="12345",  # same channel account
                ayla_user_id=None,  # never linked — the production state
            )
        )
        _with_pii(bot_user)

        delete_personal_data(bot_user, client=_StubPCClient())  # type: ignore[arg-type]

        sibling.refresh_from_db()
        assert sibling.phone == ""
        assert sibling.display_name == ""
        assert sibling.client_name == ""

    def test_erases_preferences(self, tenant, bot_user) -> None:
        """allergies is free-text health data; the sheet promises «настройки»."""
        from apps.identity.models import UserPreferences

        UserPreferences.all_tenants.create(
            bot_user=bot_user,
            tenant=tenant,
            allergies="аллергия на латекс",
            birthday_date=date(1990, 5, 17),
        )
        _with_pii(bot_user)

        delete_personal_data(bot_user, client=_StubPCClient())  # type: ignore[arg-type]

        assert not UserPreferences.all_tenants.filter(bot_user_id=bot_user.id).exists()
        # And it is not readable through the profile surface either.
        from apps.identity.services.profile import get_profile

        snap = get_profile(bot_user)
        assert snap.preferences["allergies"] == ""
        assert snap.preferences["birthday_date"] is None

    def test_does_not_touch_another_person(self, tenant, bot_user) -> None:
        stranger = _with_pii(
            BotUser.all_tenants.create(
                tenant=tenant,
                channel="max",
                channel_user_id="99999",
                ayla_user_id=uuid.uuid4(),
            )
        )
        _with_pii(bot_user)

        delete_personal_data(bot_user, client=_StubPCClient())  # type: ignore[arg-type]

        stranger.refresh_from_db()
        assert stranger.phone == _PII["phone"]
        assert stranger.display_name == _PII["display_name"]
        assert stranger.client_name == _PII["client_name"]

    def test_unlinked_user_local_data_erased_and_result_is_truthful(self, tenant) -> None:
        """NULL ayla_user_id: local data really goes, and we don't claim success.

        Owner ruling §3-§6. Local state (profile PII, consents) is owned via
        the BotUser FK, so it is erased regardless of linkage. The upstream
        Ayla step could not even be addressed, so it must NOT be reported as
        an idempotent success — the cascade is honestly partial.
        """
        bu = _with_pii(
            BotUser.all_tenants.create(
                tenant=tenant, channel="max", channel_user_id="555", ayla_user_id=None
            )
        )
        _grant(bu, CT.PERSONAL_DATA)

        result = delete_personal_data(bu)

        # Local legs really ran.
        pii_step = next(s for s in result.steps if s.step == "profile_pii_erase")
        assert pii_step.ok and not pii_step.detail
        assert next(s for s in result.steps if s.step == "consent_withdraw").ok
        assert not ConsentRecord.all_tenants.filter(bot_user=bu, withdrawn_at__isnull=True).exists()
        # Memory genuinely cannot exist without the key — green is truthful.
        memory_step = next(s for s in result.steps if s.step == "memory_delete")
        assert memory_step.ok and memory_step.detail == "no_state"
        # The unaddressable upstream step is NOT a success.
        assert not result.all_ok
        assert result.failed_steps == ["ayla_delete"]

        bu.refresh_from_db()
        assert bu.phone == ""
        assert bu.display_name == ""
        assert bu.client_name == ""

    def test_unlinked_user_consents_are_withdrawn_not_skipped(self, tenant) -> None:
        """Ruling §5 — ConsentRecord ownership is local, not ayla-keyed."""
        bu = BotUser.all_tenants.create(
            tenant=tenant, channel="max", channel_user_id="556", ayla_user_id=None
        )
        _grant(bu, CT.PERSONAL_DATA)
        _grant(bu, CT.MEMORY_GREEN)
        assert ConsentRecord.all_tenants.filter(bot_user=bu, withdrawn_at__isnull=True).count() == 2

        delete_personal_data(bu)

        assert not ConsentRecord.all_tenants.filter(bot_user=bu, withdrawn_at__isnull=True).exists()

    def test_repeat_delete_is_idempotent(self, bot_user, ayla_user_id) -> None:
        """Second pass sees a real upstream 404, as production would."""
        _seed_memory(bot_user, ayla_user_id)
        _with_pii(bot_user)

        class _GoneOnRepeat(_StubPCClient):
            def delete_personal_data(self, *, ayla_user_id: str) -> None:
                already = ("delete", ayla_user_id) in self.calls
                self.calls.append(("delete", ayla_user_id))
                if already:
                    raise PersonalContextNotFoundError("gone")

        client = _GoneOnRepeat()
        first = delete_personal_data(bot_user, client=client)  # type: ignore[arg-type]
        second = delete_personal_data(bot_user, client=client)  # type: ignore[arg-type]

        assert first.all_ok and second.all_ok
        assert next(s for s in second.steps if s.step == "ayla_delete").detail == "already_deleted"
        bot_user.refresh_from_db()
        assert bot_user.phone == ""

    def test_pii_erase_runs_even_when_upstream_fails(self, bot_user) -> None:
        """A 502 from Ayla must not leave our own copy of the phone behind."""
        _with_pii(bot_user)
        client = _StubPCClient(delete_exc=PersonalContextTransportError("http_500"))

        result = delete_personal_data(bot_user, client=client)  # type: ignore[arg-type]

        assert not result.all_ok
        assert result.failed_steps == ["ayla_delete"]
        pii_step = next(s for s in result.steps if s.step == "profile_pii_erase")
        assert pii_step.ok
        bot_user.refresh_from_db()
        assert bot_user.phone == ""

    def test_audit_payload_carries_no_pii_values(self, bot_user) -> None:
        from apps.audit.models import AuditLog

        _with_pii(bot_user)

        delete_personal_data(bot_user, client=_StubPCClient())  # type: ignore[arg-type]

        log = AuditLog.all_tenants.filter(action="privacy.personal_data_deleted").first()
        assert log is not None
        blob = json.dumps(log.payload, ensure_ascii=False)
        for value in _PII.values():
            assert value not in blob
        assert "profile_pii_erase" in blob


class TestReOnboardingAfterErase:
    """§8 invariant: the next legitimate contact must not resurrect PII."""

    def test_next_turn_resolves_same_shell_without_restoring_pii(self, settings) -> None:
        """The live global path supplies only chat_id — nothing to blank-fill."""
        from apps.identity.services import resolve_or_create_global_bot_user

        settings.STRICT_TENANT_SCOPE = "strict"
        uid = uuid.uuid4()
        bu = resolve_or_create_global_bot_user(
            channel="max", channel_user_id="reonb-1", ayla_user_id=uid
        )
        _with_pii(bu)

        delete_personal_data(bu, client=_StubPCClient())  # type: ignore[arg-type]

        # Next legitimate contact — production passes chat_id only, see
        # apps/channels/max/handler.py::_handle_global_max_event_inner.
        again = resolve_or_create_global_bot_user(
            channel="max", channel_user_id="reonb-1", chat_id="reonb-1"
        )

        assert again.id == bu.id  # same shell, no phantom identity
        assert again.phone == ""
        assert again.display_name == ""
        assert again.client_name == ""
        # Technical binding intact ⇒ the forget_all tombstone still applies.
        assert again.ayla_user_id == uid

    def test_erased_user_is_not_locked_out(self, settings) -> None:
        """C5 erasure is not account closure — deleted_at stays NULL."""
        from apps.identity.services import resolve_or_create_global_bot_user

        settings.STRICT_TENANT_SCOPE = "strict"
        bu = resolve_or_create_global_bot_user(
            channel="max", channel_user_id="reonb-2", ayla_user_id=uuid.uuid4()
        )
        _with_pii(bu)

        delete_personal_data(bu, client=_StubPCClient())  # type: ignore[arg-type]

        bu.refresh_from_db()
        assert bu.deleted_at is None


class TestServerSideDeleteConfirmation:
    """Owner ruling §1-2 — the C5 endpoint verifies the token itself.

    A client-side sheet is not a confirmation: before this, any single
    authenticated DELETE ran the whole cascade.
    """

    @pytest.fixture(autouse=True)
    def _stub_upstream(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "apps.identity.services.privacy.PersonalContextHttpClient",
            lambda: _StubPCClient(),
        )

    def _assert_untouched(self, bot_user) -> None:
        bot_user.refresh_from_db()
        assert bot_user.phone == _PII["phone"]
        assert bot_user.display_name == _PII["display_name"]
        assert bot_user.client_name == _PII["client_name"]
        assert ConsentRecord.all_tenants.filter(
            bot_user=bot_user, withdrawn_at__isnull=True
        ).exists()

    def test_missing_confirmation_rejected_without_mutation(
        self, client: DjangoClient, bot_user
    ) -> None:
        _with_pii(bot_user)
        _grant(bot_user, CT.PERSONAL_DATA)

        resp = _delete_request(client, confirmation=None)

        assert resp.status_code == 400
        assert resp.json()["error"] == "confirmation_mismatch"
        self._assert_untouched(bot_user)

    def test_bare_delete_with_no_body_rejected(self, client: DjangoClient, bot_user) -> None:
        """The exact shape of the pre-ruling call: DELETE, no body at all."""
        _with_pii(bot_user)
        _grant(bot_user, CT.PERSONAL_DATA)

        resp = client.delete(
            "/api/v1/customer/me/personal-data/",
            HTTP_AUTHORIZATION=_init_data_header("12345"),
        )

        assert resp.status_code == 400
        self._assert_untouched(bot_user)

    @pytest.mark.parametrize("bad", ["удалить", "DELETE", "УДАЛИТЬ ", "", "yes", "Удалить"])
    def test_wrong_confirmation_rejected_without_mutation(
        self, client: DjangoClient, bot_user, bad
    ) -> None:
        _with_pii(bot_user)
        _grant(bot_user, CT.PERSONAL_DATA)

        resp = _delete_request(client, confirmation=bad)

        assert resp.status_code == 400
        assert resp.json()["error"] == "confirmation_mismatch"
        self._assert_untouched(bot_user)

    def test_malformed_body_rejected_without_mutation(self, client: DjangoClient, bot_user) -> None:
        _with_pii(bot_user)

        resp = client.delete(
            "/api/v1/customer/me/personal-data/",
            data="not json",
            content_type="application/json",
            HTTP_AUTHORIZATION=_init_data_header("12345"),
        )

        assert resp.status_code == 400
        bot_user.refresh_from_db()
        assert bot_user.phone == _PII["phone"]

    def test_correct_confirmation_performs_erase(
        self, client: DjangoClient, bot_user, ayla_user_id
    ) -> None:
        _seed_memory(bot_user, ayla_user_id)
        _with_pii(bot_user)

        resp = _delete_request(client)

        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"
        bot_user.refresh_from_db()
        assert bot_user.phone == ""
        assert bot_user.display_name == ""
        assert bot_user.client_name == ""

    @pytest.mark.parametrize("body", ["[]", "null", '"УДАЛИТЬ"', "5", "true"])
    def test_non_object_body_rejected_without_mutation(
        self, client: DjangoClient, bot_user, body
    ) -> None:
        _with_pii(bot_user)

        resp = client.delete(
            "/api/v1/customer/me/personal-data/",
            data=body,
            content_type="application/json",
            HTTP_AUTHORIZATION=_init_data_header("12345"),
        )

        assert resp.status_code == 400
        bot_user.refresh_from_db()
        assert bot_user.phone == _PII["phone"]

    @pytest.mark.parametrize("value", [5, True, ["УДАЛИТЬ"], {"v": "УДАЛИТЬ"}, None])
    def test_non_string_confirmation_rejected(self, client: DjangoClient, bot_user, value) -> None:
        _with_pii(bot_user)

        resp = client.delete(
            "/api/v1/customer/me/personal-data/",
            data=json.dumps({"confirmation": value}),
            content_type="application/json",
            HTTP_AUTHORIZATION=_init_data_header("12345"),
        )

        assert resp.status_code == 400
        assert resp.json()["error"] == "confirmation_mismatch"
        bot_user.refresh_from_db()
        assert bot_user.phone == _PII["phone"]

    def test_token_is_the_sibling_primitive_not_a_second_one(self) -> None:
        """Ruling §2 — reuse the existing primitive, don't invent a second.

        Compared against the literal, not against a re-import of the same
        symbol: a view that grew its own ``_C5_TOKEN = "УДАЛИТЬ"`` would
        satisfy an identity check while quietly forking the primitive.
        """
        import inspect

        from apps.miniapp_api import views

        assert DELETE_CONFIRMATION_TOKEN == "УДАЛИТЬ"
        source = inspect.getsource(views.personal_data_delete)
        assert "DELETE_CONFIRMATION_TOKEN" in source
        assert "УДАЛИТЬ" not in source  # no hardcoded fork of the token

    def test_frontend_constant_matches_backend_token(self) -> None:
        """The Mini App hardcodes the token; drift would 400 every request."""
        from pathlib import Path

        ts = Path("apps/miniapp/src/lib/personal-data.ts").read_text(encoding="utf-8")
        assert f'DELETE_CONFIRMATION_TOKEN = "{DELETE_CONFIRMATION_TOKEN}"' in ts


class TestResultTruthfulness:
    """Ruling §3 — never answer `deleted` when a mandatory step was skipped."""

    def test_unlinked_user_gets_partial_not_deleted(
        self, client: DjangoClient, tenant, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            "apps.identity.services.privacy.PersonalContextHttpClient",
            lambda: _StubPCClient(),
        )
        bu = _with_pii(
            BotUser.all_tenants.create(
                tenant=tenant,
                channel="max",
                channel_user_id="12345",  # what _init_data_header resolves to
                ayla_user_id=None,
            )
        )
        _grant(bu, CT.PERSONAL_DATA)

        resp = _delete_request(client)

        assert resp.status_code == 502
        body = resp.json()
        assert body["status"] == "partial"
        assert body["status"] != "deleted"
        assert body["failed_steps"] == ["ayla_delete"]
        # ...and the local data really is gone despite the honest partial.
        bu.refresh_from_db()
        assert bu.phone == ""
        assert not ConsentRecord.all_tenants.filter(bot_user=bu, withdrawn_at__isnull=True).exists()


class TestPersonLevelAylaResolution:
    """Round-3 P1-1 — the subject is the person, not the requesting row.

    Only `resolve_or_create_global_bot_user` ever stamps `ayla_user_id`, and
    it stamps the `global_bot` sentinel shell — while the Mini App request
    resolves the `MAX_BOT_TENANT_SLUG` shell. Reading the id off the
    requesting row makes a linked person look unlinked, which would declare
    their live memory "no state" and never fire the forget_all tombstone.
    """

    def _sibling_pair(self, tenant, ayla_user_id):
        """(miniapp shell: unlinked, sentinel shell: linked) — same person."""
        sentinel = Tenant.objects.create(slug="global-bot-sib", name="Global")
        linked = BotUser.all_tenants.create(
            tenant=sentinel,
            channel="max",
            channel_user_id="12345",
            ayla_user_id=ayla_user_id,
        )
        requesting = BotUser.all_tenants.create(
            tenant=tenant,
            channel="max",
            channel_user_id="12345",  # same channel account
            ayla_user_id=None,  # the Mini App shell is never stamped
        )
        return requesting, linked

    def test_memory_is_erased_via_sibling_shell(self, tenant, ayla_user_id) -> None:
        requesting, linked = self._sibling_pair(tenant, ayla_user_id)
        _seed_memory(linked, ayla_user_id)
        assert MemoryEntry.objects.filter(
            user_id=ayla_user_id, soft_deleted_at__isnull=True
        ).exists()

        result = delete_personal_data(requesting, client=_StubPCClient())  # type: ignore[arg-type]

        # The live memory really goes — not reported as "no_state".
        memory_step = next(s for s in result.steps if s.step == "memory_delete")
        assert memory_step.ok
        assert memory_step.detail != "no_state"
        assert not MemoryEntry.objects.filter(
            user_id=ayla_user_id, soft_deleted_at__isnull=True
        ).exists()

    def test_upstream_is_addressed_via_sibling_shell(self, tenant, ayla_user_id) -> None:
        requesting, _ = self._sibling_pair(tenant, ayla_user_id)
        client = _StubPCClient()

        result = delete_personal_data(requesting, client=client)  # type: ignore[arg-type]

        # Ayla was reachable after all — addressed with the person's id.
        assert client.calls == [("delete", str(ayla_user_id))]
        assert result.all_ok

    def test_still_honest_when_no_shell_is_linked(self, tenant) -> None:
        BotUser.all_tenants.create(
            tenant=Tenant.objects.create(slug="global-bot-sib2", name="G2"),
            channel="max",
            channel_user_id="12345",
            ayla_user_id=None,
        )
        requesting = BotUser.all_tenants.create(
            tenant=tenant, channel="max", channel_user_id="12345", ayla_user_id=None
        )

        result = delete_personal_data(requesting)

        assert not result.all_ok
        assert result.failed_steps == ["ayla_delete"]


class TestUnretryablePartialShape:
    """Round-3 P2-1 — a structural failure must not read as 'retry me'."""

    def test_502_body_carries_the_reason_slug(
        self, client: DjangoClient, tenant, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            "apps.identity.services.privacy.PersonalContextHttpClient",
            lambda: _StubPCClient(),
        )
        _with_pii(
            BotUser.all_tenants.create(
                tenant=tenant, channel="max", channel_user_id="12345", ayla_user_id=None
            )
        )

        resp = _delete_request(client)

        assert resp.status_code == 502
        body = resp.json()
        assert body["failed_steps"] == ["ayla_delete"]
        # Without this the sheet offers an infinite, hopeless retry.
        assert body["failed_details"] == {"ayla_delete": "not_linked"}

    def test_transient_failure_carries_no_structural_slug(
        self, client: DjangoClient, bot_user, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            "apps.identity.services.privacy.PersonalContextHttpClient",
            lambda: _StubPCClient(delete_exc=PersonalContextTransportError("http_500")),
        )

        resp = _delete_request(client)

        body = resp.json()
        assert body["failed_steps"] == ["ayla_delete"]
        # A 5xx IS worth retrying — it must not be tagged not_linked.
        assert body.get("failed_details", {}).get("ayla_delete") != "not_linked"
