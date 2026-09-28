"""DRF-2450 (А): соло-мастер связывается сразу после провижининга, в том же ходе.

§77 п.38 владельца: «мне не надо участия человека в регистрации мастеров».
У соло-мастера приглашения нет; доказательство владения — провижининг, и
каталожная дверь сверяет личность с его claim (каталожный #591,
``claim_mismatch``).

**Предел этих узлов — назван, чтобы зелень не читалась шире.** Каталог здесь
подставлен (:class:`_FakeCatalog`). Узлы доказывают, что бот зовёт дверь в
нужный момент, в нужном порядке, с нужным актором и переживает её отказ.
Что каталог в самом деле сверяет claim — доказывают узлы каталога
(``users/tests/test_solo_claim_check_2450.py``), а не эти.

Подставной каталог держит три факта настоящего, от которых зависит порядок:

* прокси-строка появляется только после представления личности
  (``resolve_identity``); дверь её не создаёт и отвечает ``identity_unknown``;
* дверь отказывает ``claim_mismatch``, если личность не та, для которой
  заведён workspace;
* до связи личность резолвится в прокси, после — в настоящую учётку.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from apps.channels.max import salon_handler
from apps.identity.models import SoloIdentityLink
from apps.identity.services import specialist_identity_link as door_client
from apps.identity.services.specialist_identity_link import (
    ACTOR_SOLO_PROVISIONING,
    SpecialistIdentityLinkRefused,
)
from apps.integrations.ayla import identity_client
from apps.integrations.ayla.identity_client import IdentityResolveError, ResolvedIdentity
from apps.integrations.ayla.user_proxy import external_user_id_for

pytestmark = pytest.mark.django_db

SPECIALIST_ID = uuid.UUID("24500000-0000-4000-8000-000000000001")
PROXY_ID = uuid.UUID("24500000-0000-4000-8000-0000000000aa")
REAL_ID = uuid.UUID("24500000-0000-4000-8000-0000000000bb")


class _Event:
    def __init__(self, text=salon_handler.SOLO_REGISTER_CALLBACK, channel_user_id="solo-2450-1"):
        self.text = text
        self.chat_id = "2450"
        self.channel = "max"
        self.channel_user_id = channel_user_id
        self.raw = {}


def _event() -> Any:
    """Стоит на месте ``CanonicalEvent``: дверь читает только эти поля."""
    return _Event()


class _FakeCatalog:
    """Каталог в одном месте: провижининг, представление личности, дверь."""

    def __init__(self) -> None:
        self.claim: str | None = None
        self.seen: set[str] = set()
        self.bound: set[str] = set()
        self.calls: list[str] = []
        self.door_actors: list[str] = []
        self.door_specialists: list = []
        self.provisioning_refusal: str | None = None
        self.resolve_fails = False
        self.resolve_raises: Exception | None = None
        self.door_refusal: str | None = None

    def provision(self, link, *, tenant, bot_user, display_name, http_client=None):
        self.calls.append("provision")
        if self.provisioning_refusal:
            return self.provisioning_refusal
        if link.catalog_provisioned_at is None:
            from django.utils import timezone

            self.claim = external_user_id_for(bot_user)
            link.catalog_specialist_id = SPECIALIST_ID
            link.catalog_provisioned_at = timezone.now()
            link.save(update_fields=["catalog_specialist_id", "catalog_provisioned_at"])
            link.master.catalog_specialist_id = SPECIALIST_ID
            link.master.save(update_fields=["catalog_specialist_id"])
        return None

    def resolve(self, external_user_id, *, timeout_s=None):
        self.calls.append("resolve")
        if self.resolve_raises is not None:
            raise self.resolve_raises
        if self.resolve_fails:
            raise IdentityResolveError("catalog down")
        self.seen.add(external_user_id)
        if external_user_id in self.bound:
            return ResolvedIdentity(ayla_user_id=REAL_ID, is_proxy=False)
        return ResolvedIdentity(ayla_user_id=PROXY_ID, is_proxy=True)

    def door(self, *, specialist_id, bot_user, actor_label, http_client=None):
        self.calls.append("door")
        self.door_actors.append(actor_label)
        self.door_specialists.append(specialist_id)
        external = external_user_id_for(bot_user)
        corr = "corr-2450"
        if self.door_refusal:
            raise SpecialistIdentityLinkRefused(self.door_refusal, correlation_id=corr)
        if external != self.claim:
            raise SpecialistIdentityLinkRefused("claim_mismatch", correlation_id=corr)
        if external not in self.seen:
            raise SpecialistIdentityLinkRefused("identity_unknown", correlation_id=corr)
        self.bound.add(external)


@pytest.fixture(autouse=True)
def _fresh_stranger_counter():
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def catalog(monkeypatch) -> _FakeCatalog:
    fake = _FakeCatalog()
    monkeypatch.setattr(
        "apps.identity.services.solo_catalog_provisioning.provision_catalog_workspace",
        fake.provision,
    )
    monkeypatch.setattr(identity_client, "resolve_identity", fake.resolve)
    monkeypatch.setattr(door_client, "bind_master_identity_in_catalog", fake.door)
    return fake


@pytest.fixture
def said(monkeypatch):
    out = []
    monkeypatch.setattr(
        salon_handler, "_reply", lambda event, text, attachments=None: out.append(text)
    )
    return out


@pytest.fixture
def emitted(monkeypatch):
    out = []
    monkeypatch.setattr(
        salon_handler, "emit", lambda name, payload=None, **kw: out.append((name, payload))
    )
    return out


def _link() -> SoloIdentityLink:
    return SoloIdentityLink.objects.get(channel="max", channel_user_id="solo-2450-1")


def _registered(emitted) -> dict:
    return next(p for name, p in emitted if name == "channels.max.salon.solo_registered")


# ─── пара: связь в том же ходе против прежнего исхода без неё ───────────────


class TestTheMasterIsLinkedInTheSameTurn:
    def test_registration_ends_linked_without_an_operator(self, catalog, said, emitted) -> None:
        salon_handler._register_solo_provider(_event(), entry=None)

        link = _link()
        assert link.status == SoloIdentityLink.Status.LINKED
        assert link.provenance == SoloIdentityLink.Provenance.CATALOG_RESOLVED
        assert link.operator_id is None  # человека в этом ходе не было
        link.master.refresh_from_db()
        assert link.master.ayla_user_id == REAL_ID
        assert _registered(emitted)["identity_link_refusal"] is None

    def test_without_the_bind_the_same_registration_stays_pending(
        self, catalog, said, emitted, monkeypatch
    ) -> None:
        """Подмена: вызов двери выключен — исход прежний, PENDING и прокси.
        Узел выше держит именно вызов, а не что-то соседнее."""
        monkeypatch.setattr(
            door_client, "bind_solo_identity_after_provisioning", lambda *a, **k: "disabled"
        )

        salon_handler._register_solo_provider(_event(), entry=None)

        link = _link()
        assert link.status == SoloIdentityLink.Status.PENDING
        assert link.last_attempt_refusal == "proxy_identity"
        assert "door" not in catalog.calls

    def test_the_door_is_called_with_the_provisioned_profile_and_its_actor(
        self, catalog, said, emitted
    ) -> None:
        salon_handler._register_solo_provider(_event(), entry=None)

        assert catalog.door_specialists == [SPECIALIST_ID]
        assert catalog.door_actors == [ACTOR_SOLO_PROVISIONING]

    def test_the_identity_is_presented_before_the_door(self, catalog, said, emitted) -> None:
        """Дверь не создаёт прокси: без представления она ответила бы
        ``identity_unknown`` (так ведёт себя подставной каталог)."""
        salon_handler._register_solo_provider(_event(), entry=None)

        assert catalog.calls.index("provision") < catalog.calls.index("resolve")
        assert catalog.calls.index("resolve") < catalog.calls.index("door")


# ─── лучшая попытка: отказ двери регистрацию не валит ──────────────────────


class TestTheDoorsRefusalIsSurvived:
    def test_claim_mismatch_leaves_a_pending_link_and_the_honest_text(
        self, catalog, said, emitted
    ) -> None:
        catalog.door_refusal = "claim_mismatch"

        salon_handler._register_solo_provider(_event(), entry=None)

        assert _link().status == SoloIdentityLink.Status.PENDING
        assert said == [salon_handler.SOLO_CREATED_PENDING]
        assert _registered(emitted)["identity_link_refusal"] == "claim_mismatch"

    def test_a_catalog_that_does_not_answer_is_named_and_the_door_is_not_called(
        self, catalog, said, emitted
    ) -> None:
        catalog.resolve_fails = True

        salon_handler._register_solo_provider(_event(), entry=None)

        assert "door" not in catalog.calls
        assert _registered(emitted)["identity_link_refusal"] == "identity_unreachable"
        assert _link().status == SoloIdentityLink.Status.PENDING

    def test_a_dropped_connection_is_named_not_raised(self, catalog) -> None:
        """Сырой ``RemoteProtocolError`` из подставленного клиента (рестарт
        воркера каталога). С DRF-2579 настоящий ``resolve_identity`` сам называет
        его отказом; помощник держит обещание «не выпускает исключений» и без
        этого — узел проверяет вторую линию, а не клиент."""
        import httpx

        from apps.identity.services.specialist_identity_link import (
            bind_solo_identity_after_provisioning,
        )

        catalog.resolve_raises = httpx.RemoteProtocolError("Server disconnected")

        class _Link:
            catalog_specialist_id = SPECIALIST_ID

        class _BotUser:
            pk = 1
            channel = "max"
            channel_user_id = "solo-2450-1"

        reason = bind_solo_identity_after_provisioning(_Link(), bot_user=_BotUser())

        assert reason == "identity_unreachable"
        assert "door" not in catalog.calls

    def test_an_unprovisioned_workspace_is_not_linked(self, catalog, said, emitted) -> None:
        """Без подтверждённого профиля дверь сверять не с чем — её не зовут."""
        catalog.provisioning_refusal = "token_missing"

        salon_handler._register_solo_provider(_event(), entry=None)

        assert "door" not in catalog.calls
        assert _registered(emitted)["identity_link_refusal"] is None
        assert _link().status == SoloIdentityLink.Status.PENDING


# ─── повтор: дозвон для PENDING, никогда — против решения оператора ────────


class TestTheRepeatTap:
    def test_a_failed_first_bind_is_completed_by_the_second_tap(
        self, catalog, said, emitted
    ) -> None:
        catalog.door_refusal = "transport_error"
        salon_handler._register_solo_provider(_event(), entry=None)
        assert _link().status == SoloIdentityLink.Status.PENDING

        catalog.door_refusal = None
        salon_handler._register_solo_provider(_event(), entry=None)

        assert _link().status == SoloIdentityLink.Status.LINKED
        assert catalog.calls.count("door") == 2

    def test_an_operators_rejection_is_never_overridden(self, catalog, said, emitted) -> None:
        from django.contrib.auth import get_user_model

        from apps.identity.services.solo_identity_link import reject_by_operator

        catalog.door_refusal = "transport_error"
        salon_handler._register_solo_provider(_event(), entry=None)
        operator = get_user_model().objects.create_user(username="op-2450", password="x")  # noqa: S106
        reject_by_operator(_link(), operator=operator, reason="fraud_suspected")
        catalog.door_refusal = None
        doors_before = catalog.calls.count("door")

        salon_handler._register_solo_provider(_event(), entry=None)

        assert catalog.calls.count("door") == doors_before
        assert _link().status == SoloIdentityLink.Status.REJECTED
