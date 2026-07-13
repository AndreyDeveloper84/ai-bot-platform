"""calc_price tool tests (DRF-842 / Phase 1 / B6).

Drives the tool handler + the skill dispatcher with a stub YClients
client + an in-test CatalogService + Promotion fixture set. Covers the
spec's 12 required behaviours plus an LLM round-trip through the skill.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.utils import timezone

from apps.catalog.models import CatalogService
from apps.conversations.models import Conversation
from apps.identity.models import BotUser
from apps.integrations.yclients import Service
from apps.llm.protocol import CompletionResult, ToolCall
from apps.llm.router import reset_router_cache
from apps.promotions.models import Promotion
from apps.skills.base import SkillContext
from apps.skills.booking.skill import BookingSkill
from apps.skills.booking.tools import (
    CALC_PRICE_TOOL_SPEC,
    CalcPriceResult,
    calc_price,
)
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant


pytestmark = pytest.mark.django_db(transaction=True)


NBSP = "\xa0"  # U+00A0 non-breaking space — Russian thousands separator


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path: Path, settings):  # type: ignore[no-untyped-def]
    settings.BASE_DIR = tmp_path
    settings.LLM_PROVIDER = "openai"
    settings.SKILL_LLM_PROVIDER = {}
    reset_router_cache()
    cache.clear()
    yield
    cache.clear()
    reset_router_cache()


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="calc-price", name="Calc Price")


@pytest.fixture
def tenant_other(db) -> Tenant:
    return Tenant.objects.create(slug="calc-price-other", name="Calc Price Other")


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="cp-1",
        chat_id="cp-1",
        phone="79991234567",
        client_name="Anna",
    )


@pytest.fixture
def catalog_service(tenant: Tenant) -> CatalogService:
    return CatalogService.all_tenants.create(
        tenant=tenant,
        external_id=101,
        external_updated_at=timezone.now(),
        slug="massage",
        name="Массаж спины",
        price_from=Decimal("1500.00"),
        duration_min=60,
    )


@pytest.fixture
def catalog_service_no_price(tenant: Tenant) -> CatalogService:
    return CatalogService.all_tenants.create(
        tenant=tenant,
        external_id=202,
        external_updated_at=timezone.now(),
        slug="custom",
        name="Услуга по запросу",
        price_from=None,
        duration_min=60,
    )


def _allowed_ids(*services: CatalogService) -> set[int]:
    # external_id is nullable since the S3B re-key; these test services all
    # set it, so guard the None for mypy and skip any unset row.
    return {int(s.external_id) for s in services if s.external_id is not None}


def _service_lookup(*services: CatalogService) -> dict[int, str]:
    return {int(s.external_id): s.name for s in services if s.external_id is not None}


# ---------------------------------------------------------------------------
# Tool spec sanity
# ---------------------------------------------------------------------------


class TestToolSpec:
    def test_spec_shape(self) -> None:
        assert CALC_PRICE_TOOL_SPEC["name"] == "calc_price"
        params = CALC_PRICE_TOOL_SPEC["parameters"]
        assert params["type"] == "object"
        assert "service_id" in params["properties"]
        assert "promo_code" in params["properties"]
        assert params["required"] == ["service_id"]


# ---------------------------------------------------------------------------
# Handler — happy paths
# ---------------------------------------------------------------------------


class TestCalcPriceHappyPaths:
    def test_no_promo_returns_original_as_final(
        self, tenant: Tenant, catalog_service: CatalogService
    ) -> None:
        result = calc_price(
            tenant=tenant,
            arguments={"service_id": catalog_service.external_id},
            allowed_service_ids=_allowed_ids(catalog_service),
            service_lookup=_service_lookup(catalog_service),
        )
        assert result.error == ""
        price = result.price
        assert price is not None
        assert price.original_price == Decimal("1500.00")
        assert price.final_price == Decimal("1500.00")
        assert price.discount_percent is None
        assert price.promo_status == "not_supplied"
        # Reply must include 1 500 ₽ in Russian format (NBSP thousands).
        assert f"1{NBSP}500{NBSP}₽" in result.text

    def test_valid_promo_applies_discount(
        self, tenant: Tenant, catalog_service: CatalogService
    ) -> None:
        Promotion.all_tenants.create(
            tenant=tenant,
            code="MAY10",
            discount_percent=10,
        )
        result = calc_price(
            tenant=tenant,
            arguments={"service_id": catalog_service.external_id, "promo_code": "MAY10"},
            allowed_service_ids=_allowed_ids(catalog_service),
            service_lookup=_service_lookup(catalog_service),
        )
        price = result.price
        assert price is not None
        assert price.original_price == Decimal("1500.00")
        assert price.final_price == Decimal("1350.00")
        assert price.discount_percent == 10
        assert price.promo_status == "ok"
        # Both prices must appear.
        assert f"1{NBSP}500{NBSP}₽" in result.text
        assert f"1{NBSP}350{NBSP}₽" in result.text

    def test_case_insensitive_promo_code(
        self, tenant: Tenant, catalog_service: CatalogService
    ) -> None:
        Promotion.all_tenants.create(
            tenant=tenant,
            code="MAY10",
            discount_percent=10,
        )
        # LLM emits the lower-cased version.
        result = calc_price(
            tenant=tenant,
            arguments={"service_id": catalog_service.external_id, "promo_code": "may10"},
            allowed_service_ids=_allowed_ids(catalog_service),
            service_lookup=_service_lookup(catalog_service),
        )
        assert result.price is not None
        assert result.price.promo_status == "ok"
        assert result.price.discount_percent == 10

    def test_empty_applicable_service_ids_applies_to_all(
        self, tenant: Tenant, catalog_service: CatalogService
    ) -> None:
        Promotion.all_tenants.create(
            tenant=tenant,
            code="ALL",
            discount_percent=20,
            applicable_service_ids=[],
        )
        result = calc_price(
            tenant=tenant,
            arguments={"service_id": catalog_service.external_id, "promo_code": "ALL"},
            allowed_service_ids=_allowed_ids(catalog_service),
            service_lookup=_service_lookup(catalog_service),
        )
        assert result.price is not None
        assert result.price.promo_status == "ok"
        assert result.price.final_price == Decimal("1200.00")  # 1500 - 20%


# ---------------------------------------------------------------------------
# Handler — promo failure modes
# ---------------------------------------------------------------------------


class TestCalcPricePromoFailures:
    def test_promo_not_found_keeps_original_price(
        self, tenant: Tenant, catalog_service: CatalogService
    ) -> None:
        result = calc_price(
            tenant=tenant,
            arguments={"service_id": catalog_service.external_id, "promo_code": "TYPO"},
            allowed_service_ids=_allowed_ids(catalog_service),
            service_lookup=_service_lookup(catalog_service),
        )
        price = result.price
        assert price is not None
        assert price.promo_status == "not_found"
        assert price.final_price == price.original_price
        assert "промокод" in result.text.lower()

    def test_promo_inactive(self, tenant: Tenant, catalog_service: CatalogService) -> None:
        Promotion.all_tenants.create(
            tenant=tenant,
            code="DEAD",
            discount_percent=10,
            is_active=False,
        )
        result = calc_price(
            tenant=tenant,
            arguments={"service_id": catalog_service.external_id, "promo_code": "DEAD"},
            allowed_service_ids=_allowed_ids(catalog_service),
            service_lookup=_service_lookup(catalog_service),
        )
        assert result.price is not None
        assert result.price.promo_status == "inactive"
        assert result.price.final_price == result.price.original_price

    def test_promo_not_started(self, tenant: Tenant, catalog_service: CatalogService) -> None:
        Promotion.all_tenants.create(
            tenant=tenant,
            code="FUTURE",
            discount_percent=10,
            valid_from=timezone.now() + timedelta(days=7),
        )
        result = calc_price(
            tenant=tenant,
            arguments={"service_id": catalog_service.external_id, "promo_code": "FUTURE"},
            allowed_service_ids=_allowed_ids(catalog_service),
            service_lookup=_service_lookup(catalog_service),
        )
        assert result.price is not None
        assert result.price.promo_status == "not_started"
        assert "начнёт действовать" in result.text

    def test_promo_expired(self, tenant: Tenant, catalog_service: CatalogService) -> None:
        Promotion.all_tenants.create(
            tenant=tenant,
            code="PAST",
            discount_percent=10,
            valid_to=timezone.now() - timedelta(days=7),
        )
        result = calc_price(
            tenant=tenant,
            arguments={"service_id": catalog_service.external_id, "promo_code": "PAST"},
            allowed_service_ids=_allowed_ids(catalog_service),
            service_lookup=_service_lookup(catalog_service),
        )
        assert result.price is not None
        assert result.price.promo_status == "expired"
        assert "истёк" in result.text

    def test_promo_wrong_service(self, tenant: Tenant, catalog_service: CatalogService) -> None:
        # Promo applies only to a different service UUID.
        other_uuid = uuid.uuid4()
        Promotion.all_tenants.create(
            tenant=tenant,
            code="OTHERSVC",
            discount_percent=10,
            applicable_service_ids=[str(other_uuid)],
        )
        result = calc_price(
            tenant=tenant,
            arguments={
                "service_id": catalog_service.external_id,
                "promo_code": "OTHERSVC",
            },
            allowed_service_ids=_allowed_ids(catalog_service),
            service_lookup=_service_lookup(catalog_service),
        )
        assert result.price is not None
        assert result.price.promo_status == "wrong_service"
        assert "не подходит" in result.text


# ---------------------------------------------------------------------------
# Handler — edge cases
# ---------------------------------------------------------------------------


class TestCalcPriceEdgeCases:
    def test_price_from_none_returns_indiv_price_copy(
        self, tenant: Tenant, catalog_service_no_price: CatalogService
    ) -> None:
        result = calc_price(
            tenant=tenant,
            arguments={"service_id": catalog_service_no_price.external_id},
            allowed_service_ids=_allowed_ids(catalog_service_no_price),
            service_lookup=_service_lookup(catalog_service_no_price),
        )
        price = result.price
        assert price is not None
        assert price.original_price is None
        assert price.final_price is None
        assert "индивидуальному прайсу" in result.text

    def test_invalid_service_id_triggers_handoff_signal(
        self, tenant: Tenant, catalog_service: CatalogService
    ) -> None:
        # LLM hallucinated id 9999 — not in allowed set.
        result = calc_price(
            tenant=tenant,
            arguments={"service_id": 9999},
            allowed_service_ids=_allowed_ids(catalog_service),
            service_lookup=_service_lookup(catalog_service),
        )
        assert result.error == "price_invalid_service_id"
        assert result.price is None

    def test_cross_tenant_promo_invisible(
        self,
        tenant: Tenant,
        tenant_other: Tenant,
        catalog_service: CatalogService,
    ) -> None:
        # Promo lives on the OTHER tenant.
        Promotion.all_tenants.create(
            tenant=tenant_other,
            code="LEAK",
            discount_percent=50,
        )
        result = calc_price(
            tenant=tenant,
            arguments={"service_id": catalog_service.external_id, "promo_code": "LEAK"},
            allowed_service_ids=_allowed_ids(catalog_service),
            service_lookup=_service_lookup(catalog_service),
        )
        # Tenant A asking about a tenant-B promo sees "not_found".
        assert result.price is not None
        assert result.price.promo_status == "not_found"

    def test_catalog_missing_falls_back_to_indiv_price(self, tenant: Tenant) -> None:
        # service_id is in the YClients allow-set but no CatalogService
        # mirror row exists (catalog sync hasn't run yet).
        allowed = {303}
        lookup = {303: "New service"}
        result = calc_price(
            tenant=tenant,
            arguments={"service_id": 303},
            allowed_service_ids=allowed,
            service_lookup=lookup,
        )
        assert result.price is not None
        assert result.price.original_price is None
        assert "индивидуальному прайсу" in result.text


# ---------------------------------------------------------------------------
# Skill-level integration: dispatcher routes calc_price through the LLM loop
# ---------------------------------------------------------------------------


class _FakeYClients:
    def __init__(self, services: list[Service]) -> None:
        self.services_rows = services
        self.staff_rows: list[Any] = []

    def get_services(self, **_: Any) -> list[Service]:
        return list(self.services_rows)

    def get_staff(self, **_: Any) -> list[Any]:
        return []


def _completion(*, text: str = "", tool_calls: list[ToolCall] | None = None) -> CompletionResult:
    return CompletionResult(
        text=text,
        tool_calls=tool_calls or [],
        prompt_tokens=10,
        completion_tokens=20,
        model="gpt-4o-mini-mock",
        provider="openai",
        finish_reason="stop" if not tool_calls else "tool_calls",
    )


def _patch_provider_complete(side_effects: list[CompletionResult]) -> Any:
    from apps.llm.providers.openai_provider import OpenAIProvider

    return patch.object(OpenAIProvider, "complete", side_effect=side_effects)


def _patch_yclients(client: _FakeYClients) -> Any:
    return patch("apps.integrations.yclients.get_yclients_client", return_value=client)


class TestCalcPriceSkillIntegration:
    def test_skill_dispatches_calc_price_and_returns_text(
        self, tenant: Tenant, bot_user: BotUser, catalog_service: CatalogService
    ) -> None:
        Promotion.all_tenants.create(
            tenant=tenant,
            code="MAY10",
            discount_percent=10,
        )
        # YClients knows the service (external id 101) — matches catalog.
        yc_service = Service(
            id=cast(int, catalog_service.external_id),
            title=catalog_service.name,
            price_min=1500.0,
            price_max=1500.0,
            duration_s=3600,
            category_id=None,
            raw={},
        )
        client = _FakeYClients([yc_service])

        tc = ToolCall(
            id="c1",
            name="calc_price",
            arguments={"service_id": cast(int, catalog_service.external_id), "promo_code": "MAY10"},
        )
        completions = [
            _completion(tool_calls=[tc]),
            _completion(text="1 500 ₽ → 1 350 ₽ с промокодом."),
        ]

        conv = Conversation.all_tenants.create(tenant=tenant, bot_user=bot_user)
        ctx = SkillContext(
            conversation=conv,
            bot_user=bot_user,
            message_text="сколько стоит массаж с промокодом MAY10?",
        )

        with _patch_yclients(client), _patch_provider_complete(completions):
            with tenant_scope(tenant):
                result = BookingSkill().handle(ctx)

        assert result.should_handoff is False
        assert "1 350" in result.reply_text or "1 500" in result.reply_text

    def test_skill_handoff_on_invalid_service_id(
        self, tenant: Tenant, bot_user: BotUser, catalog_service: CatalogService
    ) -> None:
        yc_service = Service(
            id=cast(int, catalog_service.external_id),
            title=catalog_service.name,
            price_min=1500.0,
            price_max=1500.0,
            duration_s=3600,
            category_id=None,
            raw={},
        )
        client = _FakeYClients([yc_service])

        # LLM emits a service_id the catalog doesn't know.
        tc = ToolCall(
            id="c1",
            name="calc_price",
            arguments={"service_id": 9999},
        )
        completions = [_completion(tool_calls=[tc])]

        conv = Conversation.all_tenants.create(tenant=tenant, bot_user=bot_user)
        ctx = SkillContext(
            conversation=conv,
            bot_user=bot_user,
            message_text="сколько стоит услуга 9999?",
        )

        with _patch_yclients(client), _patch_provider_complete(completions):
            with tenant_scope(tenant):
                result = BookingSkill().handle(ctx)

        assert result.should_handoff is True
        assert result.handoff_reason == "price_invalid_service_id"


# ---------------------------------------------------------------------------
# CalcPriceResult DTO sanity
# ---------------------------------------------------------------------------


class TestCalcPriceResultDTO:
    def test_frozen_dataclass_immutable(self) -> None:
        result = CalcPriceResult(
            service_name="Test",
            original_price=Decimal("100"),
            final_price=Decimal("90"),
            discount_percent=10,
            promo_status="ok",
        )
        with pytest.raises(Exception):  # FrozenInstanceError
            result.service_name = "Other"  # type: ignore[misc]
