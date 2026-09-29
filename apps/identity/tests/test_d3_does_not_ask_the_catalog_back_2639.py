"""DRF-2639: the D3 bot half does not ask the catalog to erase what it erased.

Live on the pilot: ``DELETE /internal/users/<id>/personal-data/`` → 403 every
~15 min for one subject, ``identity.privacy.ayla_delete_failed``; the catalog
names it ``internal.subject_authz.unknown_actor``. The catalog's deletion
executor erases its half and renames the proxy (``deleted:<pk>``) BEFORE it
asks the bot; the bot's cascade then asked the catalog back with
``bot:max:<id>`` — unknown by then, 403 — reported ``all_ok=False``, and the
request stayed ``PROCESSING`` for the next tick (900 s), forever.

The catalog here answers as the real one does on that path — 403 «acting
subject is unknown» (``users/permissions.py``) — not with a success: the old
fake catalog of DRF-1725 answered 200 and kept the loop invisible.

The pair, on the same catalog and the same person: the D3 bot half is all_ok
without a request; «forget everything» started by the person still reports
the 403 as a failed step.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import httpx
import pytest

from apps.identity.models import BotUser
from apps.identity.services.account_deletion import execute_bot_half
from apps.identity.services.deletion_gate import deletion_gate, mark_deletion_requested
from apps.identity.services.privacy import delete_personal_data
from apps.integrations.ayla.identity_client import ResolvedIdentity
from apps.integrations.ayla.personal_context_client import PersonalContextHttpClient
from apps.tenancy.models import Tenant

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("ingress_streams_empty")]

AYLA_ID = uuid.UUID("26392639-0000-4000-8000-000000002639")
REQUEST_ID = str(uuid.uuid4())


class _CatalogAfterD3:
    """The catalog after its own D3 pass: the proxy is renamed, every
    personal-data call is refused as an unknown actor."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def client(self, *_a, **_k) -> PersonalContextHttpClient:
        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return httpx.Response(403, json={"detail": "acting subject is unknown"})

        return PersonalContextHttpClient(
            base_url="https://ayla.example",
            token="internal-token-under-test",  # pragma: allowlist secret
            retries=1,
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        )


@pytest.fixture
def person(db) -> BotUser:
    tenant = Tenant.objects.create(slug="del-2639", name="Del")
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="2639001",
        chat_id="chat-2639001",
        display_name="Анна",
        ayla_user_id=AYLA_ID,
    )


def _step(steps, name: str):
    (found,) = [s for s in steps if s.step == name]
    return found


class TestTheSame403TwoAnswers:
    def test_the_d3_bot_half_is_done_without_asking_the_catalog_back(self, person) -> None:
        mark_deletion_requested(AYLA_ID, request_id=REQUEST_ID)
        assert deletion_gate(AYLA_ID).blocked  # presence: the gate is closed before
        catalog = _CatalogAfterD3()

        with patch("apps.identity.services.privacy.PersonalContextHttpClient", catalog.client):
            out = execute_bot_half(
                ayla_user_id=AYLA_ID, external_user_ids=["bot:max:2639001"], request_id=REQUEST_ID
            )

        assert out.all_ok, out.failed_steps
        assert {"step": "ayla_delete", "ok": True, "detail": "erased_by_catalog"} in out.steps
        assert catalog.requests == []
        assert not deletion_gate(AYLA_ID).blocked
        person.refresh_from_db()
        assert person.display_name == ""  # the bot's own half did run

    def test_forget_everything_by_the_person_still_names_the_403(self, person) -> None:
        catalog = _CatalogAfterD3()

        with patch("apps.identity.services.privacy.PersonalContextHttpClient", catalog.client):
            result = delete_personal_data(person)

        step = _step(result.steps, "ayla_delete")
        assert (step.ok, step.detail) == (False, "")
        assert [(r.method, r.headers["x-external-user-id"]) for r in catalog.requests] == [
            ("DELETE", "bot:max:2639001")
        ]
        assert "ayla_delete" in result.failed_steps


class TestD3DoesNotRecreateTheProxy:
    """An unlinked shell: outside D3 the cascade resolves ``bot:max:<id>``
    in the catalog (DRF-1035) — in D3 that would CREATE a fresh proxy after
    the catalog renamed the old one, and the external id would outlive the
    deletion. The count of resolutions is the node, not the step's outcome
    (``not_linked`` stays whatever its rule says)."""

    @pytest.fixture
    def unlinked(self, person) -> BotUser:
        BotUser.all_tenants.filter(pk=person.pk).update(ayla_user_id=None)
        person.refresh_from_db()
        return person

    @pytest.fixture
    def resolutions(self):
        seen: list[str] = []

        def resolve(external_user_id, **_kw):
            seen.append(external_user_id)
            # the catalog would hand out a fresh proxy
            return ResolvedIdentity(ayla_user_id=uuid.uuid4(), is_proxy=True)

        with patch("apps.integrations.ayla.identity_client.resolve_identity", resolve):
            yield seen

    def test_d3_resolves_nothing(self, unlinked, resolutions) -> None:
        catalog = _CatalogAfterD3()
        with patch("apps.identity.services.privacy.PersonalContextHttpClient", catalog.client):
            out = execute_bot_half(
                ayla_user_id=AYLA_ID, external_user_ids=["bot:max:2639001"], request_id=REQUEST_ID
            )

        assert out.shells == 1  # presence: the shell was found and the cascade ran
        # failed_steps deliberately unchecked: whether an unlinked D3 is ok is
        # the owner's ruling on ``not_linked``; this node must hold either way.
        assert resolutions == []
        assert catalog.requests == []

    def test_forget_everything_still_resolves(self, unlinked, resolutions) -> None:
        catalog = _CatalogAfterD3()
        with patch("apps.identity.services.privacy.PersonalContextHttpClient", catalog.client):
            delete_personal_data(unlinked)

        assert resolutions == ["bot:max:2639001"]


class TestTheAuditNamesWhoActed:
    """DRF-2651: «actor» in ``privacy.personal_data_deleted`` names who acted.
    Before, both paths wrote ``"customer"`` and the D3 row could not be told
    from the person's own «forget everything». Expected values are literals,
    not the code's constants — a vocabulary change must go red here."""

    @staticmethod
    def _deleted_row() -> dict:
        from apps.audit.models import AuditLog

        # all_tenants: the default manager hides rows — it read empty once.
        (row,) = AuditLog.all_tenants.filter(action="privacy.personal_data_deleted")
        return {k: v for k, v in row.payload.items() if k in ("actor", "initiator", "request_id")}

    def test_d3_names_the_catalog_executor(self, person) -> None:
        catalog = _CatalogAfterD3()
        with patch("apps.identity.services.privacy.PersonalContextHttpClient", catalog.client):
            execute_bot_half(
                ayla_user_id=AYLA_ID, external_user_ids=["bot:max:2639001"], request_id=REQUEST_ID
            )

        assert self._deleted_row() == {
            "actor": "system",
            "initiator": "deletion_executor",
            "request_id": REQUEST_ID,
        }

    def test_d3_names_the_request_for_an_unlinked_shell_too(self, person) -> None:
        """The one shell in 28 without ``ayla_user_id``: without the request
        id on this row «who asked» could only be matched by time."""
        BotUser.all_tenants.filter(pk=person.pk).update(ayla_user_id=None)
        catalog = _CatalogAfterD3()
        with (
            patch("apps.identity.services.privacy.PersonalContextHttpClient", catalog.client),
            patch("apps.integrations.ayla.identity_client.resolve_identity") as resolve,
        ):
            execute_bot_half(
                ayla_user_id=AYLA_ID, external_user_ids=["bot:max:2639001"], request_id=REQUEST_ID
            )

        assert resolve.call_count == 0  # presence of the D3 path, not a resolved link
        assert self._deleted_row()["request_id"] == REQUEST_ID

    def test_forget_everything_names_the_person(self, person) -> None:
        catalog = _CatalogAfterD3()
        with patch("apps.identity.services.privacy.PersonalContextHttpClient", catalog.client):
            delete_personal_data(person)

        assert self._deleted_row() == {"actor": "customer"}
