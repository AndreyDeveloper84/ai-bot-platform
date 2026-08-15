"""Multi-bot webhook gate and routing (DRF-1061).

One endpoint, several bots, told apart by the ``X-Max-Bot-Api-Secret``
header. The header is simultaneously the credential and the routing
discriminator, so these tests pin both halves:

* **the gate** — a secret that belongs to no registered bot is 401, and a
  registered one is not;
* **the routing** — each bot's update lands on *its* stream with *its*
  tenant, not on a shared default.

The dedup-namespace test is the subtle one. The journal is unique on
``(channel, external_event_id)`` and ``channel`` is ``"max"`` for every bot
here, so without a per-bot namespace two bots receiving the same upstream
id would collide — and the loser would be dropped silently, with a 200 and
a ``dedup`` flag that looks entirely normal.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from django.test import Client

from apps.channels.bot_registry import BotEntry
from apps.tenancy.models import Tenant

CLIENT_SECRET = "wh-secret-client"  # pragma: allowlist secret
SALON_SECRET = "wh-secret-salon"  # pragma: allowlist secret
STRANGER_SECRET = "wh-secret-stranger"  # pragma: allowlist secret

REGISTRY = (
    BotEntry(
        slug="client",
        webhook_secret=CLIENT_SECRET,
        api_token="tok-client",  # pragma: allowlist secret
        stream="max_global",
    ),
    BotEntry(
        slug="salon",
        webhook_secret=SALON_SECRET,
        api_token="tok-salon",  # pragma: allowlist secret
        tenant_slug="formula-tela",
        stream="max_salon",
    ),
)


def _payload(*, update_id: int = 1, text: str = "привет") -> dict:
    return {
        "update_type": "message_created",
        "update_id": update_id,
        "timestamp": 1_700_000_000_000,
        "message": {
            "sender": {"user_id": 777, "name": "Владелец", "is_bot": False},
            "recipient": {"chat_id": 555, "user_id": 999, "chat_type": "dialog"},
            "body": {"mid": f"mid-{update_id}", "seq": 1, "text": text, "attachments": []},
        },
    }


def _post(secret: str, payload: dict):
    return Client().post(
        "/api/v1/ingress/max/",
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_X_MAX_BOT_API_SECRET=secret,
    )


@pytest.fixture(autouse=True)
def _registry(settings):
    settings.MAX_BOT_REGISTRY = REGISTRY
    # Prove routing comes from the registry, not from the legacy settings.
    settings.MAX_WEBHOOK_SECRET = ""
    settings.GLOBAL_BOT_TOKENS = ""


@pytest.fixture
def _enqueue():
    with patch("apps.ingress.views.enqueue") as mock:
        yield mock


@pytest.mark.django_db
class TestGate:
    def test_registered_salon_secret_is_accepted(self, _enqueue):
        assert _post(SALON_SECRET, _payload()).status_code == 200

    def test_registered_client_secret_is_accepted(self, _enqueue):
        assert _post(CLIENT_SECRET, _payload()).status_code == 200

    def test_unregistered_secret_is_rejected(self, _enqueue):
        response = _post(STRANGER_SECRET, _payload())

        assert response.status_code == 401
        _enqueue.assert_not_called()

    def test_missing_header_is_rejected(self, _enqueue):
        response = Client().post(
            "/api/v1/ingress/max/",
            data=json.dumps(_payload()),
            content_type="application/json",
        )

        assert response.status_code == 401
        _enqueue.assert_not_called()

    def test_prefix_of_a_real_secret_is_rejected(self, _enqueue):
        assert _post(SALON_SECRET[:-2], _payload()).status_code == 401
        _enqueue.assert_not_called()


@pytest.mark.django_db
class TestRouting:
    def test_salon_update_goes_to_its_own_stream_with_its_tenant(self, _enqueue):
        tenant = Tenant.objects.create(slug="formula-tela", name="Формула тела")

        _post(SALON_SECRET, _payload())

        kwargs = _enqueue.call_args.kwargs
        assert kwargs["channel"] == "max_salon"
        assert kwargs["tenant_id"] == str(tenant.id)

    def test_client_update_goes_to_the_tenant_less_global_stream(self, _enqueue):
        _post(CLIENT_SECRET, _payload())

        kwargs = _enqueue.call_args.kwargs
        assert kwargs["channel"] == "max_global"
        assert kwargs["tenant_id"] is None

    def test_salon_tenant_missing_from_db_does_not_crash_ingest(self, _enqueue):
        # The tenant row is absent (never seeded). Ingest must still accept
        # and enqueue — losing the update would be worse than losing scope.
        response = _post(SALON_SECRET, _payload())

        assert response.status_code == 200
        assert _enqueue.call_args.kwargs["channel"] == "max_salon"
        assert _enqueue.call_args.kwargs["tenant_id"] is None


@pytest.mark.django_db
class TestDedupNamespace:
    def test_same_update_id_from_two_bots_is_not_deduped(self, _enqueue):
        # THE point of the namespace. Same upstream id, two different bots:
        # both must be enqueued. Without namespacing the second would return
        # dedup=True and never reach a worker — a silently dropped message
        # that looks like a normal replay in the logs.
        Tenant.objects.create(slug="formula-tela", name="Формула тела")

        first = _post(CLIENT_SECRET, _payload(update_id=42))
        second = _post(SALON_SECRET, _payload(update_id=42))

        assert first.json()["dedup"] is False
        assert second.json()["dedup"] is False
        assert _enqueue.call_count == 2
        assert {c.kwargs["channel"] for c in _enqueue.call_args_list} == {
            "max_global",
            "max_salon",
        }

    def test_same_bot_still_dedups_its_own_replay(self, _enqueue):
        # Namespacing must not defeat real dedup within one bot.
        Tenant.objects.create(slug="formula-tela", name="Формула тела")

        first = _post(SALON_SECRET, _payload(update_id=7))
        second = _post(SALON_SECRET, _payload(update_id=7))

        assert first.json()["dedup"] is False
        assert second.json()["dedup"] is True
        assert _enqueue.call_count == 1


@pytest.mark.django_db
class TestLegacyDeploymentUnchanged:
    """No registry declared → behave exactly as before DRF-1061."""

    def test_legacy_secret_routes_to_global_when_listed(self, settings, _enqueue):
        settings.MAX_BOT_REGISTRY = ()
        settings.MAX_WEBHOOK_SECRET = CLIENT_SECRET
        settings.GLOBAL_BOT_TOKENS = CLIENT_SECRET

        _post(CLIENT_SECRET, _payload())

        assert _enqueue.call_args.kwargs["channel"] == "max_global"
        assert _enqueue.call_args.kwargs["tenant_id"] is None

    def test_legacy_secret_routes_to_per_tenant_when_not_listed(self, settings, _enqueue):
        settings.MAX_BOT_REGISTRY = ()
        settings.MAX_WEBHOOK_SECRET = CLIENT_SECRET
        settings.GLOBAL_BOT_TOKENS = ""

        _post(CLIENT_SECRET, _payload())

        assert _enqueue.call_args.kwargs["channel"] == "max"

    def test_legacy_journal_id_is_not_namespaced(self, settings, _enqueue):
        # Existing deployments must keep writing the journal rows they always
        # wrote; a namespace here would orphan every prior dedup key.
        from apps.ingress.models import WebhookJournal

        settings.MAX_BOT_REGISTRY = ()
        settings.MAX_WEBHOOK_SECRET = CLIENT_SECRET
        settings.GLOBAL_BOT_TOKENS = CLIENT_SECRET

        _post(CLIENT_SECRET, _payload(update_id=99))

        row = WebhookJournal.objects.get()
        assert row.external_event_id == "99"

    def test_wrong_legacy_secret_still_401(self, settings, _enqueue):
        settings.MAX_BOT_REGISTRY = ()
        settings.MAX_WEBHOOK_SECRET = CLIENT_SECRET

        assert _post(STRANGER_SECRET, _payload()).status_code == 401
