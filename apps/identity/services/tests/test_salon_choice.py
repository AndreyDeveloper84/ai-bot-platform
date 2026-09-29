"""Выбор салона при двух и более рабочих тенантах у одной личности (DRF-1766, срез 5).

Решение владельца (DRF-1705): «выбор салона, не страж „одна личность — один
салон“». До этого среза (DRF-1755) резолвер при ничьей брал старейший тенант
детерминированно и писал WARNING — заглушка «пока экрана нет». Теперь ничья —
не ответ резолвера, а **вопрос человеку**: :class:`SalonChoiceRequired` с
списком тенантов; поверхности отвечают экраном/кнопками, выбор приезжает
заголовком ``X-Salon-Choice`` (Mini App) или кэшем по личности (бот) и
допускается только среди рабочих тенантов — чужой слаг не выбирает ничего.

Замер 12.09 12:53 UTC: на пилоте таких личностей 0 — экран не увидит никто,
пока не появится вторая настоящая роль; но первый WARNING больше не будет
молчаливым выбором за человека.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.channels.bot_registry import BotEntry
from apps.identity.models import BotUser
from apps.identity.services.bot_user_resolver import (
    SALON_CHOICE_HEADER,
    SalonChoiceRequired,
    resolve_bot_user,
    resolve_working_bot_user,
)
from apps.master_api.tests.conftest import BOT_TOKEN, _sign, init_data_header
from apps.tenancy.models import Tenant, TenantStaff

pytestmark = pytest.mark.django_db

CHANNEL_USER_ID = "56005600"


class _Verified:
    def __init__(self, user_id: str, bot_slug: str = "") -> None:
        self.user_id = user_id
        self.bot_slug = bot_slug


@pytest.fixture
def two_salons() -> tuple[Tenant, Tenant]:
    older = Tenant.all_objects.create(slug="choice-older", name="Старший салон")
    younger = Tenant.all_objects.create(slug="choice-younger", name="Младший салон")
    Tenant.all_objects.filter(pk=older.pk).update(created_at=timezone.now() - timedelta(days=30))
    Tenant.all_objects.filter(pk=younger.pk).update(created_at=timezone.now() - timedelta(days=1))
    older.refresh_from_db()
    younger.refresh_from_db()
    return older, younger


@pytest.fixture
def two_roles(two_salons) -> tuple[BotUser, BotUser]:
    older, younger = two_salons
    a = BotUser.all_tenants.create(tenant=older, channel="max", channel_user_id=CHANNEL_USER_ID)
    b = BotUser.all_tenants.create(tenant=younger, channel="max", channel_user_id=CHANNEL_USER_ID)
    TenantStaff.all_tenants.create(
        tenant=older, bot_user=a, role=TenantStaff.Role.ADMIN, created_by=a
    )
    TenantStaff.all_tenants.create(
        tenant=younger, bot_user=b, role=TenantStaff.Role.OWNER, created_by=b
    )
    return a, b


@pytest.fixture
def salon_bot(settings) -> None:
    settings.MAX_BOT_REGISTRY = (
        BotEntry(
            slug="salon",
            webhook_secret="wh-salon",  # pragma: allowlist secret
            api_token="tok-salon",  # pragma: allowlist secret
            tenant_slug="",
            stream="max_salon",
        ),
    )
    settings.MAX_BOT_TENANT_SLUG = ""
    # init_data_header() signs with the master_api conftest token; that
    # conftest's autouse fixture does not reach this package.
    settings.MAX_BOT_TOKEN = BOT_TOKEN


class TestTheTieIsAQuestionNotAnAnswer:
    def test_two_working_rows_ask_for_a_choice(self, two_roles, two_salons):
        """Красный до правки: резолвер отдавал старейший тенант с WARNING."""

        older, younger = two_salons

        with pytest.raises(SalonChoiceRequired) as exc:
            resolve_working_bot_user(CHANNEL_USER_ID, surface="test")

        assert [t.slug for t in exc.value.tenants] == [older.slug, younger.slug]
        assert [t.name for t in exc.value.tenants] == [older.name, younger.name]

    def test_a_choice_among_the_working_rows_is_honoured(self, two_roles, two_salons):
        older, younger = two_salons
        a, b = two_roles

        assert (
            resolve_working_bot_user(CHANNEL_USER_ID, surface="test", chosen_slug=younger.slug) == b
        )
        assert (
            resolve_working_bot_user(CHANNEL_USER_ID, surface="test", chosen_slug=older.slug) == a
        )

    def test_a_foreign_slug_chooses_nothing(self, two_roles, two_salons):
        """Отрицательный контроль: заголовок не может выбрать тенант, где у человека нет роли."""

        Tenant.all_objects.create(slug="choice-foreign", name="Чужой")

        with pytest.raises(SalonChoiceRequired):
            resolve_working_bot_user(CHANNEL_USER_ID, surface="test", chosen_slug="choice-foreign")

    def test_one_working_row_needs_no_choice_even_with_a_wrong_header(self, two_salons):
        """Контроль среза 2: одна рабочая строка — она, заголовок безразличен."""

        older, younger = two_salons
        a = BotUser.all_tenants.create(tenant=older, channel="max", channel_user_id=CHANNEL_USER_ID)
        BotUser.all_tenants.create(tenant=younger, channel="max", channel_user_id=CHANNEL_USER_ID)
        TenantStaff.all_tenants.create(
            tenant=older, bot_user=a, role=TenantStaff.Role.ADMIN, created_by=a
        )

        assert resolve_working_bot_user(CHANNEL_USER_ID, surface="test") == a
        assert (
            resolve_working_bot_user(CHANNEL_USER_ID, surface="test", chosen_slug=younger.slug) == a
        )

    def test_resolve_bot_user_passes_the_question_through(self, salon_bot, two_roles):
        with pytest.raises(SalonChoiceRequired):
            resolve_bot_user(_Verified(CHANNEL_USER_ID, bot_slug="salon"))


class TestTheSurfacesAskTheScreen:
    def test_master_api_answers_409_with_the_tenants(
        self, client, salon_bot, two_roles, two_salons, settings
    ):
        """Красный до правки: 200/401 по старейшему тенанту."""

        older, younger = two_salons
        settings.MAX_BOT_TENANT_SLUG = older.slug

        resp = client.get(
            reverse("master_api:dashboard"), HTTP_AUTHORIZATION=init_data_header(CHANNEL_USER_ID)
        )

        assert resp.status_code == 409, resp.content
        body = resp.json()
        assert body["error"] == "salon_choice_required"
        assert [t["slug"] for t in body["details"]["tenants"]] == [older.slug, younger.slug]

    def test_master_api_with_the_header_serves_the_chosen_salon(
        self, client, salon_bot, two_roles, two_salons, settings
    ):
        older, younger = two_salons
        settings.MAX_BOT_TENANT_SLUG = older.slug

        resp = client.get(
            reverse("master_api:dashboard"),
            HTTP_AUTHORIZATION=init_data_header(CHANNEL_USER_ID),
            **{SALON_CHOICE_HEADER: younger.slug},
        )

        # Not a master in either — the check is that the choice was honoured
        # and the request got PAST the resolver: not_a_master is the master
        # gate answering for the chosen row, not 409.
        assert resp.status_code != 409, resp.content
        assert resp.json()["error"] == "not_a_master"

    def test_me_answers_409_and_then_the_chosen_tenant(
        self, client, salon_bot, two_roles, two_salons, settings
    ):
        """Красный до правки: /me отдавал старейший тенант."""

        import json
        import time as time_module

        older, younger = two_salons
        settings.MAX_BOT_TENANT_SLUG = older.slug
        params = {
            "user": json.dumps({"id": int(CHANNEL_USER_ID), "first_name": "Ира"}),
            "auth_date": str(int(time_module.time())),
        }
        header = f"MaxInitData {_sign(params, token='tok-salon')}"

        first = client.get(reverse("identity:me"), HTTP_AUTHORIZATION=header)
        assert first.status_code == 409, first.content
        assert first.json()["error"] == "salon_choice_required"

        chosen = client.get(
            reverse("identity:me"), HTTP_AUTHORIZATION=header, **{SALON_CHOICE_HEADER: younger.slug}
        )
        assert chosen.status_code == 200, chosen.content
        assert chosen.json()["tenant"]["slug"] == younger.slug
        assert chosen.json()["is_owner"] is True
