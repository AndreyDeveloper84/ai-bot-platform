"""D2 — стоп персонализации по живой заявке, бот-половина (§7, DRF-1699).

Три класса читателей — память (`memory_reader`), рекомендации (оба
клиента), проактив (`followups._should_send_b11`) — при живой заявке
отвечают отказом С ИМЕНЕМ `deletion_requested` и номером, не пустотой.

Сторож главного окна: `forget_all_requested_at` и `deletion_requested_at`
не спорят — при обоих флагах побеждает заявка на удаление, по имени.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from django.utils import timezone

from apps.identity.models import BotUser, MemoryEntry, UserPersonalContext
from apps.identity.services.deletion_gate import (
    DELETION_REQUESTED,
    clear_deletion_flag,
    deletion_gate,
    mark_deletion_requested,
)
from apps.identity.services.memory_reader import (
    read_green_entries,
    read_personal_context,
)
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

AYLA_ID = uuid.UUID("0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0")
REQUEST_ID = "7a1b2c3d-0000-4000-8000-000000001699"


@pytest.fixture
def tenant(db):
    return Tenant.objects.create(slug="gate-1699", name="Gate")


@pytest.fixture
def bot_user(tenant):
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="1699200",
        chat_id="chat-1699200",
        display_name="Анна",
        ayla_user_id=AYLA_ID,
    )


@pytest.fixture
def remembered(bot_user):
    """Живая память: UPC с summary и одна зелёная запись."""
    upc = UserPersonalContext.objects.create(user_id=AYLA_ID, summary="любит чай")
    MemoryEntry.objects.create(
        user_id=AYLA_ID,
        personal_context=upc,
        kind="preference",
        sensitivity_zone=MemoryEntry.SENSITIVITY_GREEN,
        content={"drink": "tea"},
        source=MemoryEntry.SOURCE_EXPLICIT,
        # Postgres-сторож memory_entry_explicit_requires_provenance: у явного
        # факта обязано быть происхождение. sqlite локально его не держит —
        # шард CI на Postgres поймал.
        provenance=MemoryEntry.PROVENANCE_USER_STATED,
    )
    return upc


# ---------------------------------------------------------------------------
# Предикат и флаг
# ---------------------------------------------------------------------------


class TestTheGateItself:
    def test_open_when_no_flag_or_no_key(self):
        assert deletion_gate(None).blocked is False
        assert deletion_gate("not-a-uuid").blocked is False
        assert deletion_gate(AYLA_ID).blocked is False

    def test_marked_then_blocked_with_the_name_and_number(self):
        mark_deletion_requested(AYLA_ID, request_id=REQUEST_ID)

        gate = deletion_gate(AYLA_ID)

        assert gate.blocked is True
        assert gate.reason == DELETION_REQUESTED
        assert gate.request_id == REQUEST_ID

    def test_mark_is_idempotent_and_clear_reopens(self):
        mark_deletion_requested(AYLA_ID, request_id=REQUEST_ID)
        first = UserPersonalContext.objects.get(user_id=AYLA_ID).deletion_requested_at
        mark_deletion_requested(AYLA_ID, request_id=REQUEST_ID)
        assert UserPersonalContext.objects.get(user_id=AYLA_ID).deletion_requested_at == first

        assert clear_deletion_flag(AYLA_ID) is True
        assert deletion_gate(AYLA_ID).blocked is False
        assert clear_deletion_flag(AYLA_ID) is False


# ---------------------------------------------------------------------------
# Память
# ---------------------------------------------------------------------------


class TestMemoryRefusesByName:
    def test_view_carries_the_refusal_not_an_empty_view(self, remembered):
        # Положительная стража впереди: без флага память жива.
        live = read_personal_context(AYLA_ID)
        assert live.summary == "любит чай"
        assert len(live.green_facts) == 1
        assert live.refusal is None
        assert len(read_green_entries(AYLA_ID)) == 1

        mark_deletion_requested(AYLA_ID, request_id=REQUEST_ID)
        view = read_personal_context(AYLA_ID)

        assert view.refusal == DELETION_REQUESTED
        assert view.refusal_request_id == REQUEST_ID
        assert view.summary is None
        assert view.green_facts == []
        assert read_green_entries(AYLA_ID) == []

    def test_forget_all_and_deletion_do_not_argue(self, remembered):
        """Сторож главного окна: оба флага → отказ с deletion_requested, не пустой вид."""
        remembered.forget_all_requested_at = timezone.now()
        remembered.save(update_fields=["forget_all_requested_at"])
        # Один forget-all — прежнее поведение: пустой вид без имени.
        only_forget = read_personal_context(AYLA_ID)
        assert only_forget.refusal is None
        assert only_forget.summary is None

        mark_deletion_requested(AYLA_ID, request_id=REQUEST_ID)
        both = read_personal_context(AYLA_ID)

        assert both.refusal == DELETION_REQUESTED
        assert both.refusal_request_id == REQUEST_ID


# ---------------------------------------------------------------------------
# Рекомендации — оба клиента, в каталог не ходим
# ---------------------------------------------------------------------------


class TestRecommendationsRefuseByName:
    def test_resolver_classifies_a_423_as_refused_with_the_number(self, settings):
        """Транспорт чист: 423 → refused, без базы и без флага."""
        from apps.integrations.ayla import recommendation_resolver_client as rc

        settings.AYLA_BASE_URL = "https://ayla.test"
        settings.AYLA_INTERNAL_API_TOKEN = "t"
        response = rc.httpx.Response(
            423,
            json={"error": {"code": "DELETION_IN_PROGRESS", "details": {"request_id": REQUEST_ID}}},
        )
        with patch.object(rc.httpx, "Client") as client_cls:
            client_cls.return_value.__enter__.return_value.post.return_value = response
            outcome = rc.resolve_recommendation(
                external_user_id="bot:max:1699200", payload={"request_id": "x"}
            )

        assert outcome.state == "refused"
        assert outcome.is_ok is False
        assert outcome.detail == DELETION_REQUESTED
        assert outcome.request_id == REQUEST_ID

    def test_a_5xx_is_still_unavailable_not_refused(self, settings):
        """Положительная стража: refused — только 423."""
        from apps.integrations.ayla import recommendation_resolver_client as rc

        settings.AYLA_BASE_URL = "https://ayla.test"
        settings.AYLA_INTERNAL_API_TOKEN = "t"
        with patch.object(rc.httpx, "Client") as client_cls:
            client_cls.return_value.__enter__.return_value.post.return_value = rc.httpx.Response(
                503
            )
            outcome = rc.resolve_recommendation(
                external_user_id="bot:max:1699200", payload={"request_id": "x"}
            )
        assert outcome.state == "unavailable"


# ---------------------------------------------------------------------------
# Проактив
# ---------------------------------------------------------------------------


class TestProactiveIsBlockedFirst:
    def test_followup_gate_names_deletion_before_consent(self, bot_user):
        from apps.bookings import followups

        class _Reminder:
            tenant = bot_user.tenant
            booking_request = None

        # Положительная стража: без флага причина — чья угодно, но не наша.
        _, reason_before = followups._should_send_b11(_Reminder(), bot_user)
        assert reason_before != DELETION_REQUESTED

        mark_deletion_requested(AYLA_ID, request_id=REQUEST_ID)
        send, reason = followups._should_send_b11(_Reminder(), bot_user)

        assert send is False
        assert reason == DELETION_REQUESTED


# ---------------------------------------------------------------------------
# Флаг ставится в момент заявки и догоняет каталог
# ---------------------------------------------------------------------------


class TestTheRequestPathSetsTheFlag:
    def _wire(self, **over):
        base = {
            "created": True,
            "request_id": REQUEST_ID,
            "status": "DELETION_REQUESTED",
            "requested_at": "2026-09-11T14:00:00+00:00",
            "deadline_at": "2026-10-11T14:00:00+00:00",
            "completed_at": None,
            "is_open": True,
        }
        base.update(over)
        return base

    def test_accepting_marks_the_person_before_returning(self, bot_user):
        from apps.identity.services.deletion_request import request_account_deletion

        class _Client:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return None

            def create_deletion_request(self_, **_kw):
                # В момент ответа каталога флага ещё нет…
                assert deletion_gate(AYLA_ID).blocked is False
                return self._wire()

        with patch(
            "apps.integrations.ayla.identity_client.resolve_identity", side_effect=RuntimeError
        ):
            view = request_account_deletion(bot_user, client=_Client())

        # …а при возврате (= до показа успеха) — уже есть.
        assert view.request_id == REQUEST_ID
        assert deletion_gate(AYLA_ID).request_id == REQUEST_ID

    def test_reading_current_catches_up_both_ways(self, bot_user):
        from apps.identity.services.deletion_request import current_account_deletion

        class _Client:
            def __init__(self, current):
                self.current = current

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return None

            def get_current_deletion_request(self, **_kw):
                return self.current

        with patch(
            "apps.integrations.ayla.identity_client.resolve_identity", side_effect=RuntimeError
        ):
            # Заявка из приложения → флаг ставится.
            current_account_deletion(bot_user, client=_Client(self._wire()))
            assert deletion_gate(AYLA_ID).blocked is True
            # Завершена → флаг снимается.
            current_account_deletion(
                bot_user,
                client=_Client(self._wire(status="DELETION_COMPLETED", is_open=False)),
            )
            assert deletion_gate(AYLA_ID).blocked is False
            # Заявок не было → флага нет.
            mark_deletion_requested(AYLA_ID, request_id=REQUEST_ID)
            current_account_deletion(bot_user, client=_Client(None))
            assert deletion_gate(AYLA_ID).blocked is False


# ---------------------------------------------------------------------------
# Полка рекомендаций (Mini App) — гейт у вызывающего, в каталог не ходим
# ---------------------------------------------------------------------------


class TestTheShelfRefusesWithoutCallingAyla:
    """Гейт стоит у единственного вызывающего резолвера — в view, где есть
    bot_user. Транспорт остаётся чистым (без базы), а лишний запрос по
    человеку, просившему себя не обрабатывать, не уходит."""

    @staticmethod
    def _auth(bot_user):
        import hashlib
        import hmac
        import json
        import time as time_module
        from urllib.parse import urlencode

        token = "test-bot-token-gate-1699"  # noqa: S105
        params = {
            "user": json.dumps({"id": int(bot_user.channel_user_id), "first_name": "Анна"}),
            "auth_date": str(int(time_module.time())),
        }
        check = "\n".join(f"{k}={params[k]}" for k in sorted(params))
        secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
        digest = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        return token, f"MaxInitData {urlencode({**params, 'hash': digest})}"

    def _post(self, client, bot_user, settings):
        from django.urls import reverse

        token, header = self._auth(bot_user)
        settings.MAX_BOT_TOKEN = token
        settings.MAX_BOT_TENANT_SLUG = bot_user.tenant.slug
        settings.AYLA_BASE_URL = "https://ayla.test"
        settings.AYLA_INTERNAL_API_TOKEN = "t"
        return client.post(
            reverse("miniapp_api:customer_recommendations"), HTTP_AUTHORIZATION=header
        )

    def test_flagged_person_gets_423_and_the_resolver_is_not_called(
        self, client, bot_user, settings
    ):
        from apps.integrations.ayla.recommendation_resolver_client import ResolveOutcome

        mark_deletion_requested(AYLA_ID, request_id=REQUEST_ID)
        with (
            patch(
                "apps.integrations.ayla.recommendation_resolver_client.resolve_recommendation",
                return_value=ResolveOutcome("ok", decision={"ordered": []}),
            ) as resolver,
            patch(
                "apps.integrations.ayla.identity_client.resolve_identity", side_effect=RuntimeError
            ),
        ):
            res = self._post(client, bot_user, settings)

        assert res.status_code == 423, res.content
        assert res.json()["error"] == DELETION_REQUESTED
        assert res.json()["request_id"] == REQUEST_ID
        resolver.assert_not_called()

    def test_a_refused_outcome_from_ayla_is_423_and_mirrors_the_flag(
        self, client, bot_user, settings
    ):
        """Заявка из приложения: каталог знает раньше — отражаем флаг тем же ходом."""
        from apps.integrations.ayla.recommendation_resolver_client import ResolveOutcome

        assert deletion_gate(AYLA_ID).blocked is False
        with (
            patch(
                "apps.integrations.ayla.recommendation_resolver_client.resolve_recommendation",
                return_value=ResolveOutcome(
                    "refused", detail=DELETION_REQUESTED, request_id=REQUEST_ID
                ),
            ),
            patch(
                "apps.integrations.ayla.identity_client.resolve_identity", side_effect=RuntimeError
            ),
        ):
            res = self._post(client, bot_user, settings)

        assert res.status_code == 423, res.content
        assert res.json()["request_id"] == REQUEST_ID
        assert deletion_gate(AYLA_ID).request_id == REQUEST_ID

    def test_an_open_person_reaches_the_resolver(self, client, bot_user, settings):
        """Положительная стража: без флага резолвер зовётся."""
        from apps.integrations.ayla.recommendation_resolver_client import ResolveOutcome

        with (
            patch(
                "apps.integrations.ayla.recommendation_resolver_client.resolve_recommendation",
                return_value=ResolveOutcome("unavailable", detail="circuit_open"),
            ) as resolver,
            patch(
                "apps.integrations.ayla.identity_client.resolve_identity", side_effect=RuntimeError
            ),
        ):
            res = self._post(client, bot_user, settings)

        assert res.status_code != 423
        resolver.assert_called_once()
