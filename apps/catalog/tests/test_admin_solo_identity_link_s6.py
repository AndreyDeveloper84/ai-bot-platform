"""Действия оператора на карточке мастера — §6 Phase 0 (контролируемое
действие, не правка БД)."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest
from django.contrib.admin.sites import AdminSite
from django.utils import timezone
from django.contrib.auth import get_user_model

from apps.catalog.admin import CatalogMasterAdmin
from apps.catalog.models import CatalogMaster
from apps.identity.models import BotUser, SoloIdentityLink
from apps.identity.services.solo_identity_link import open_link
from apps.identity.services.solo_onboarding import create_solo_provider

pytestmark = pytest.mark.django_db


@pytest.fixture
def solo(db):
    result = create_solo_provider(channel="max", channel_user_id="solo-adm-1", display_name="Ольга")
    bot_user = BotUser.all_tenants.get(
        tenant=result.tenant, channel="max", channel_user_id="solo-adm-1"
    )
    link = open_link(result.master, bot_user=bot_user, tenant=result.tenant)
    return result, bot_user, link


@pytest.fixture
def admin_request(db):
    request = MagicMock()
    request.user = get_user_model().objects.create_superuser(username="op-adm", password="x")  # noqa: S106
    return request


def _admin() -> CatalogMasterAdmin:
    return CatalogMasterAdmin(CatalogMaster, AdminSite())


def _patch_resolver(monkeypatch, *, ayla_user_id, is_proxy):
    from apps.integrations.ayla import identity_client

    class _Identity:
        pass

    ident = _Identity()
    ident.ayla_user_id = ayla_user_id
    ident.is_proxy = is_proxy
    monkeypatch.setattr(identity_client, "resolve_identity", lambda external_user_id: ident)


class TestConfirmAction:
    def test_links_when_the_catalog_answers_with_a_real_key(self, monkeypatch, solo, admin_request):
        result, _, link = solo
        key = uuid.uuid4()
        _patch_resolver(monkeypatch, ayla_user_id=key, is_proxy=False)
        modeladmin = _admin()
        modeladmin.message_user = MagicMock()

        modeladmin.confirm_solo_identity_link(
            admin_request, CatalogMaster.all_tenants.filter(pk=result.master.pk)
        )

        link.refresh_from_db()
        assert link.status == SoloIdentityLink.Status.LINKED
        assert link.operator_username == "op-adm"
        text = modeladmin.message_user.call_args.args[1]
        assert text.startswith("Связано: 1.")

    def test_keeps_pending_and_tells_the_operator_what_to_do_first(
        self, monkeypatch, solo, admin_request
    ):
        result, _, link = solo
        _patch_resolver(monkeypatch, ayla_user_id=uuid.uuid4(), is_proxy=True)
        modeladmin = _admin()
        modeladmin.message_user = MagicMock()

        modeladmin.confirm_solo_identity_link(
            admin_request, CatalogMaster.all_tenants.filter(pk=result.master.pk)
        )

        link.refresh_from_db()
        assert link.status == SoloIdentityLink.Status.PENDING
        text = modeladmin.message_user.call_args.args[1]
        assert "proxy_identity" in text
        assert "«Связать с Ayla» в админке Ayla" in text

    def test_a_salon_master_without_a_link_is_skipped_not_invented(self, db, admin_request):
        """Не соло-мастер — заявки нет; действие не заводит её задним числом."""
        from apps.tenancy.models import Tenant

        tenant = Tenant.objects.create(slug="adm-salon", name="Салон")
        master = CatalogMaster.all_tenants.create(
            tenant=tenant,
            external_id=-777,
            name="Анна",
            external_updated_at=timezone.now(),
        )
        modeladmin = _admin()
        modeladmin.message_user = MagicMock()

        modeladmin.confirm_solo_identity_link(
            admin_request, CatalogMaster.all_tenants.filter(pk=master.pk)
        )

        assert not SoloIdentityLink.objects.filter(master=master).exists()
        assert "без заявки: 1" in modeladmin.message_user.call_args.args[1]


class TestRejectAction:
    def test_rejects_with_the_default_taxonomy_reason(self, solo, admin_request):
        result, _, link = solo
        modeladmin = _admin()
        modeladmin.message_user = MagicMock()

        modeladmin.reject_solo_identity_link(
            admin_request, CatalogMaster.all_tenants.filter(pk=result.master.pk)
        )

        link.refresh_from_db()
        assert link.status == SoloIdentityLink.Status.REJECTED
        assert link.reject_reason == "identity_unverifiable"
        assert link.operator_username == "op-adm"
