"""S2-4 — консоль оператора (§2 п.6, §6, §7): межсалонные блоки карточки
только с пропуском платформы и со следом в журнале доступа; очередь
идентичности — PENDING-привязки и живые заявки на удаление в одном месте.

Пары «присутствие/отсутствие» (DRF-1411): каждое «блока нет» стоит рядом
с «у платформенного оператора на тех же данных есть».
"""

from __future__ import annotations

import uuid

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client

from apps.adminconsole.client_access import is_platform_operator
from apps.adminconsole.clients import CROSS_SALON_SCREEN
from apps.adminconsole.models import ClientDataAccessLog
from apps.adminconsole.tests.conftest import make_client_thread
from apps.identity.models import BotUser, SoloIdentityLink, UserPersonalContext
from apps.identity.services import solo_identity_link as link_svc
from apps.identity.services.deletion_gate import mark_deletion_requested
from apps.identity.services.solo_onboarding import create_solo_provider

pytestmark = pytest.mark.django_db

QUEUE_URL = "/admin/console/identity/"
AYLA_ID = uuid.UUID("22222222-3333-4444-5555-666666666666")


def _body(response) -> str:  # noqa: ANN001, ANN202
    return response.content.decode("utf-8", errors="replace")


def _card_url(bot_user: BotUser) -> str:
    return f"/admin/console/clients/{bot_user.pk}/"


@pytest.fixture
def person(salon, other_salon):  # noqa: ANN001, ANN201
    """Один человек в двух салонах."""
    bot_user, *_ = make_client_thread(
        salon, channel_user_id="cid-s24", display_name="Марина", text="привет"
    )
    BotUser.all_tenants.create(
        tenant=other_salon, channel="max", channel_user_id="cid-s24", display_name="Марина"
    )
    return bot_user


@pytest.fixture
def salon_staff_client(db):  # noqa: ANN001, ANN201
    """Учётная запись НЕ платформы: staff с правом на клиентов, но без роли
    ayla-viewer/ayla-editor — так выглядел бы сотрудник салона."""
    user = get_user_model().objects.create_user(
        username="salon-staff",
        password="x",  # pragma: allowlist secret
        is_staff=True,
    )
    user.user_permissions.add(
        Permission.objects.get(codename="view_botuser", content_type__app_label="identity")
    )
    client = Client()
    client.force_login(user)
    return client


class TestPlatformPass:
    def test_predicate_is_roles_or_owner_and_fails_closed(self, login_as, salon_staff_client):
        login_as("p.viewer", "viewer")
        viewer = get_user_model().objects.get(username="p.viewer")
        staff = get_user_model().objects.get(username="salon-staff")
        owner = get_user_model().objects.create_superuser(
            username="own", password="x"
        )  # pragma: allowlist secret
        assert is_platform_operator(viewer) and is_platform_operator(owner)
        assert not is_platform_operator(staff)
        assert not is_platform_operator(None)

    def test_platform_operator_sees_cross_salon_blocks_and_it_is_journaled(
        self, login_as, person, monkeypatch
    ):
        monkeypatch.setattr(
            "apps.integrations.ayla.goals_client.fetch_decision_context",
            lambda **_kw: {"known": {"goal": {"goal_key": "relax"}}},
        )
        client = login_as("p.operator", "viewer")
        resp = client.get(_card_url(person))
        assert resp.status_code == 200
        body = _body(resp)
        assert "Чужой салон" in body  # второй салон того же человека
        assert "Активная цель" in body and "есть" in body
        row = ClientDataAccessLog.objects.get(screen=CROSS_SALON_SCREEN)
        assert row.outcome == ClientDataAccessLog.Outcome.OPENED
        assert row.actor_username == "p.operator"
        assert row.object_id == str(person.pk)

    def test_non_platform_staff_gets_the_card_without_cross_salon_blocks(
        self, person, salon_staff_client, monkeypatch
    ):
        called = []
        monkeypatch.setattr(
            "apps.integrations.ayla.goals_client.fetch_decision_context",
            lambda **kw: called.append(kw) or {"known": {"goal": {"goal_key": "relax"}}},
        )
        resp = salon_staff_client.get(_card_url(person))
        assert resp.status_code == 200
        body = _body(resp)
        # Присутствие: свой салон и имя на месте.
        assert "Салон DRF-1514" in body and "Марина" in body
        # Отсутствие: чужой салон и факт цели не показаны, цель не спрашивалась.
        assert "Чужой салон" not in body
        assert "Активная цель" not in body
        assert called == []
        assert "только с пропуском платформы" in body
        row = ClientDataAccessLog.objects.get(screen=CROSS_SALON_SCREEN)
        assert row.outcome == ClientDataAccessLog.Outcome.DENIED
        assert row.actor_username == "salon-staff"
        assert row.detail == "platform pass required"


class TestDeletionRowOnTheCard:
    def test_card_shows_the_live_deletion_request(self, login_as, person):
        client = login_as("p.del", "viewer")
        person.ayla_user_id = AYLA_ID
        person.save(update_fields=["ayla_user_id"])
        before = _body(client.get(_card_url(person)))
        assert "Марина" in before  # карточка та же — отсутствие ниже о строке, не о странице
        assert "Заявка на удаление" not in before

        request_id = str(uuid.uuid4())
        mark_deletion_requested(AYLA_ID, request_id=request_id)
        after = _body(client.get(_card_url(person)))
        assert "Заявка на удаление" in after and request_id in after


class TestIdentityQueue:
    def test_pending_links_and_deletion_flags_in_one_place(self, login_as, person, monkeypatch):
        # PENDING-привязка соло-мастера — настоящим путём регистрации.
        result = create_solo_provider(
            channel="max", channel_user_id="solo-s24", display_name="Ольга"
        )
        solo_user = BotUser.all_tenants.get(
            tenant=result.tenant, channel="max", channel_user_id="solo-s24"
        )
        link_svc.open_link(result.master, bot_user=solo_user, tenant=result.tenant)
        # Отклонённая — не в очереди.
        rejected_result = create_solo_provider(
            channel="max", channel_user_id="solo-s24-r", display_name="Инна"
        )
        rejected_user = BotUser.all_tenants.get(
            tenant=rejected_result.tenant, channel="max", channel_user_id="solo-s24-r"
        )
        rejected = link_svc.open_link(
            rejected_result.master, bot_user=rejected_user, tenant=rejected_result.tenant
        )
        operator = get_user_model().objects.create_user(
            username="op", password="x"
        )  # pragma: allowlist secret
        link_svc.reject_by_operator(
            rejected, operator=operator, reason=SoloIdentityLink.RejectReason.OTHER
        )
        assert SoloIdentityLink.objects.filter(status=SoloIdentityLink.Status.PENDING).count() == 1

        # Живая заявка на удаление у человека с двумя оболочками.
        person.ayla_user_id = AYLA_ID
        person.save(update_fields=["ayla_user_id"])
        request_id = str(uuid.uuid4())
        mark_deletion_requested(AYLA_ID, request_id=request_id)

        client = login_as("p.queue", "viewer")
        resp = client.get(QUEUE_URL)
        assert resp.status_code == 200
        body = _body(resp)
        assert "Ольга" in body and "solo-s24" in body
        assert "Инна" not in body
        assert request_id in body and "Марина" in body
        assert _card_url(person) in body
        # Телефона нет нигде (DRF-1039) — у мастера в пакете он мог быть.
        assert "+7" not in body

    def test_queue_is_empty_honestly(self, login_as):
        client = login_as("p.empty", "viewer")
        body = _body(client.get(QUEUE_URL))
        assert "Ждущих привязок нет" in body and "Живых заявок нет" in body

    def test_without_the_right_it_is_403(self, db):
        user = get_user_model().objects.create_user(
            username="nobody",
            password="x",
            is_staff=True,  # pragma: allowlist secret
        )
        client = Client()
        client.force_login(user)
        assert client.get(QUEUE_URL).status_code == 403

    def test_flag_without_shells_is_still_listed(self, login_as):
        UserPersonalContext.objects.create(user_id=AYLA_ID)
        mark_deletion_requested(AYLA_ID, request_id=str(uuid.uuid4()))
        client = login_as("p.noshell", "viewer")
        body = _body(client.get(QUEUE_URL))
        assert str(AYLA_ID) in body and "оболочек с ключом Ayla нет" in body
