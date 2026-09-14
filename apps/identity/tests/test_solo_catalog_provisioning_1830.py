"""DRF-1830 (M29): соло-кабинет в боте заводит каталожный workspace и записывает исход.

Что стережётся:

* регистрация (``_register_solo_provider``) зовёт provisioning с UUID, slug,
  названием и городом ТЕНАНТА БОТА, внешней личностью и именем;
* успех пишется из ответа каталога (readback) на ``SoloIdentityLink``;
* сбой каталога не отменяет кабинет в боте и не падает наружу — причина
  записана машинным именем, и имена разные для разных мест починки;
* подтверждённый workspace повторно не заводится; неподтверждённый —
  повторяется из повторной регистрации и из действия оператора;
* без подмены (токен пуст в тестовых настройках) — ``token_missing`` и ни
  одного HTTP-запроса: соседние тесты двери не ходят в сеть.

Красный до правки: весь файл (модуля, полей и вызова нет).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from django.contrib.auth import get_user_model

from apps.catalog.services.http_client import (
    CatalogProvisioningRefused,
    CatalogSoloProvisioningRefused,
    CatalogTransportError,
    ProvisionedSoloWorkspaceDTO,
)
from apps.channels.max import salon_handler
from apps.identity.models import SoloIdentityLink
from apps.identity.services import solo_catalog_provisioning
from apps.identity.services import solo_identity_link as link_svc
from apps.identity.services.solo_onboarding import create_solo_provider

pytestmark = pytest.mark.django_db

CHANNEL_USER_ID = "solo-cat-1830"


class _Event:
    def __init__(self, text: str = salon_handler.SOLO_CONFIRM_CALLBACK) -> None:
        self.text = text
        self.chat_id = "555"
        self.channel = "max"
        self.channel_user_id = CHANNEL_USER_ID
        self.raw = {"message": {"sender": {"name": "Ольга"}}}


class _FakeCatalog:
    """Подмена ``CatalogHttpClient``: записывает вызовы, отвечает заданным исходом."""

    def __init__(self, outcome: Any = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.outcome = outcome

    def __enter__(self) -> "_FakeCatalog":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def provision_solo_workspace(self, **kwargs: Any) -> ProvisionedSoloWorkspaceDTO:
        self.calls.append(kwargs)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return ProvisionedSoloWorkspaceDTO(
            tenant_id=uuid.UUID(str(kwargs["tenant_id"])),
            slug=kwargs["slug"],
            specialist_id=uuid.UUID("5a1c0000-0000-4000-8000-000000001830"),
            user_id=uuid.UUID("5a1c0000-0000-4000-8000-000000009999"),
            status="draft",
            created=True,
        )


@pytest.fixture
def said(monkeypatch):
    out: list[dict] = []
    monkeypatch.setattr(
        salon_handler, "_reply", lambda event, text, attachments=None: out.append({"text": text})
    )
    return out


@pytest.fixture
def catalog(monkeypatch) -> _FakeCatalog:
    fake = _FakeCatalog()
    monkeypatch.setattr(solo_catalog_provisioning, "CatalogHttpClient", lambda: fake)
    return fake


def _link_for_channel_user() -> SoloIdentityLink:
    return SoloIdentityLink.objects.get(channel_user_id=CHANNEL_USER_ID)


class TestRegistrationProvisionsTheCatalogWorkspace:
    def test_the_bot_tenant_uuid_and_the_person_go_to_the_catalog(self, said, catalog):
        salon_handler._register_solo_provider(
            _Event(), entry=None, display_name="Ольга Петрова", city="Пенза"
        )

        link = _link_for_channel_user()
        tenant = link.master.tenant
        assert len(catalog.calls) == 1
        call = catalog.calls[0]
        assert str(call["tenant_id"]) == str(tenant.id)
        assert call["slug"] == tenant.slug and call["slug"].startswith("solo-")
        assert call["name"] == tenant.name
        assert call["city"] == "Пенза"
        assert call["external_user_id"] == f"bot:max:{CHANNEL_USER_ID}"
        assert call["display_name"] == "Ольга Петрова"

        # Readback: из ответа каталога, причина пуста.
        assert link.catalog_specialist_id == uuid.UUID("5a1c0000-0000-4000-8000-000000001830")
        assert link.catalog_provisioned_at is not None
        assert link.catalog_provisioning_refusal == ""
        assert said[-1]["text"] == salon_handler.SOLO_CREATED_PENDING

    def test_a_catalog_failure_keeps_the_bot_workspace_and_names_the_reason(self, said, catalog):
        catalog.outcome = CatalogTransportError("down")

        salon_handler._register_solo_provider(
            _Event(), entry=None, display_name="Ольга", city="Пенза"
        )

        link = _link_for_channel_user()
        assert link.master.tenant.slug.startswith("solo-")  # кабинет в боте есть
        assert link.catalog_provisioned_at is None
        assert link.catalog_provisioning_refusal == "transport_error"
        assert said[-1]["text"] == salon_handler.SOLO_CREATED_PENDING

    def test_a_repeat_after_a_failure_retries_and_after_success_does_not_call(self, said, catalog):
        catalog.outcome = CatalogProvisioningRefused("403")
        salon_handler._register_solo_provider(
            _Event(), entry=None, display_name="Ольга", city="Пенза"
        )
        assert _link_for_channel_user().catalog_provisioning_refusal == "refused"

        catalog.outcome = None
        salon_handler._register_solo_provider(
            _Event(), entry=None, display_name="Ольга", city="Пенза"
        )
        assert _link_for_channel_user().catalog_provisioned_at is not None
        calls_after_success = len(catalog.calls)

        salon_handler._register_solo_provider(
            _Event(), entry=None, display_name="Ольга", city="Пенза"
        )
        assert len(catalog.calls) == calls_after_success  # подтверждённый не заводится снова


class TestThePilotTodayTokenMissingIsVisibleAndHarmless:
    """Пилот 15.09: ``AYLA_TENANT_PROVISIONING_TOKEN`` пуст в обоих контурах (A3 не сделано).

    Живой исход каждой регистрации до «токен задан» — ``token_missing``. Это
    не баг, и тест держит три свойства, чтобы его не приняли за баг:
    кабинет в боте создан и человек получает тот же текст; причина видна на
    ``SoloIdentityLink`` и в логе; в логе нет ни одного секрета.
    """

    def test_registration_without_a_token_keeps_the_cabinet_and_names_the_reason(
        self, said, settings, caplog, httpx_mock
    ):
        settings.AYLA_TENANT_PROVISIONING_TOKEN = ""
        settings.AYLA_INTERNAL_API_TOKEN = "runtime-secret-must-not-leak-1830"  # noqa: S105

        with caplog.at_level("WARNING", logger="apps.identity.services.solo_catalog_provisioning"):
            salon_handler._register_solo_provider(
                _Event(), entry=None, display_name="Ольга", city="Пенза"
            )

        link = _link_for_channel_user()
        # Кабинет в боте не пострадал.
        assert link.master.tenant.slug.startswith("solo-")
        assert said[-1]["text"] == salon_handler.SOLO_CREATED_PENDING
        # Причина — на связи, машинным именем; запросов в сеть нет.
        assert link.catalog_provisioning_refusal == "token_missing"
        assert link.catalog_provisioned_at is None
        assert httpx_mock.get_requests() == []
        # В логе — причина и идентификаторы, без секретов.
        lines = [r.getMessage() for r in caplog.records if "solo_catalog" in r.getMessage()]
        assert any("refusal=token_missing" in line for line in lines), lines
        assert all("runtime-secret-must-not-leak-1830" not in line for line in lines)
        assert all("Bearer" not in line for line in lines)


class TestEveryRefusalHasItsOwnName:
    @pytest.mark.parametrize(
        "outcome, reason",
        [
            (CatalogProvisioningRefused("403"), "refused"),
            (CatalogSoloProvisioningRefused("409", reason="slug_taken"), "conflict:slug_taken"),
            (CatalogTransportError("502"), "transport_error"),
        ],
    )
    def test_outcome_is_recorded_not_raised(self, catalog, outcome, reason):
        result = create_solo_provider(
            channel="max", channel_user_id=CHANNEL_USER_ID, display_name="Ольга", city="Пенза"
        )
        link = link_svc.open_link(result.master, bot_user=result.bot_user, tenant=result.tenant)
        catalog.outcome = outcome

        refusal = solo_catalog_provisioning.provision_catalog_workspace(
            link, tenant=result.tenant, bot_user=result.bot_user, display_name="Ольга"
        )

        assert refusal == reason
        link.refresh_from_db()
        assert link.catalog_provisioning_refusal == reason
        assert link.catalog_provisioned_at is None

    def test_without_a_token_nothing_goes_to_the_network(self, settings, httpx_mock):
        """Тестовые настройки: токен пуст → token_missing до любого запроса."""
        settings.AYLA_TENANT_PROVISIONING_TOKEN = ""
        result = create_solo_provider(
            channel="max", channel_user_id=CHANNEL_USER_ID, display_name="Ольга", city="Пенза"
        )
        link = link_svc.open_link(result.master, bot_user=result.bot_user, tenant=result.tenant)

        refusal = solo_catalog_provisioning.provision_catalog_workspace(
            link, tenant=result.tenant, bot_user=result.bot_user, display_name="Ольга"
        )

        assert refusal == "token_missing"
        assert httpx_mock.get_requests() == []


class TestTheOperatorRetriesTheCatalogWorkspace:
    def test_confirm_by_operator_provisions_when_the_first_attempt_failed(
        self, catalog, monkeypatch
    ):
        from apps.integrations.ayla import identity_client

        result = create_solo_provider(
            channel="max", channel_user_id=CHANNEL_USER_ID, display_name="Ольга", city="Пенза"
        )
        link = link_svc.open_link(result.master, bot_user=result.bot_user, tenant=result.tenant)
        link.catalog_provisioning_refusal = "token_missing"
        link.save(update_fields=["catalog_provisioning_refusal"])

        # Каталог по-прежнему отвечает прокси — связь не складывается, но
        # workspace оператор заводит.
        class _ProxyIdentity:
            # Тот же двойник, что в S6: каталог отвечает прокси — связь не
            # складывается, автосвязь отказывает ``proxy_identity``.
            ayla_user_id = uuid.uuid4()
            is_proxy = True

        monkeypatch.setattr(
            identity_client, "resolve_identity", lambda external_user_id: _ProxyIdentity()
        )
        operator = get_user_model().objects.create_user(username="op-1830", password="x")  # noqa: S106

        link_svc.confirm_by_operator(link, bot_user=result.bot_user, operator=operator)

        link.refresh_from_db()
        assert len(catalog.calls) == 1
        assert link.catalog_provisioned_at is not None
        assert link.catalog_provisioning_refusal == ""
