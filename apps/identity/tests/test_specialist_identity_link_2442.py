"""Бот говорит каталогу «эта личность — тот мастер» (DRF-2442, PR-B).

Решение владельца §77 п.38 (24.09): человека в регистрации мастеров нет.
Красное: мастер принимал приглашение, получал сессию — и не попадал в кабинет,
потому что каталогу никто не сообщал о его личности. Здесь проводная форма
новой двери каталога (beautygo_backend #568) и её место в приёме приглашения.

Сеть — ``pytest-httpx``: каталог не заглушка, а путь, пятый секрет, тело и
разбор ответов.

Узлы, названные в теле PR:

* **приём приглашения зовёт дверь** — путь, тело, ключ идемпотентности,
  метка вызывающего;
* **отказ каталога не ломает приём** — сессия выдана, токен остался
  погашенным, причина названа в логе (иначе недоступный каталог сжигал бы
  одноразовые приглашения);
* **секрет не в логах** — ни на успехе, ни на отказе;
* **нет ``catalog_specialist_id`` — отдельная ветвь**, не «каталог отказал»;
* **команда починки печатает три числа и вердикт**, сухой прогон по умолчанию;
* **повтор команды не создаёт вторых связей** — ключ идемпотентности тот же,
  и каталог видит ``replayed`` (у двери свой узел, у пачки — свой).
"""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from io import StringIO

import pytest
from django.core.management import call_command
from pytest_httpx import HTTPXMock

from apps.catalog.master_state import ACCEPTED
from apps.catalog.models import CatalogMaster
from apps.identity.models import BotUser
from apps.identity.services.specialist_identity_link import (
    ACTOR_BACKFILL,
    ACTOR_INVITE_ACCEPT,
    SpecialistIdentityLinkRefused,
    bind_master_identity_in_catalog,
    idempotency_key_for,
)
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

BASE = "https://ayla.test"
DOOR_TOKEN = "specialist-identity-secret-2442"  # noqa: S105
GENERAL_TOKEN = "general-bearer-2442"  # noqa: S105
SPECIALIST = "c0a1c2d3-0000-4000-8000-000000002442"
AYLA_USER = "d0a1c2d3-0000-4000-8000-000000002442"

LOGGERS = (
    "apps.catalog.services.http_client",
    "apps.identity.services.specialist_identity_link",
    "apps.master_api.views",
    "httpx",
)


@pytest.fixture(autouse=True)
def _settings(settings):
    settings.AYLA_BASE_URL = BASE
    settings.AYLA_INTERNAL_API_TOKEN = GENERAL_TOKEN
    settings.AYLA_SPECIALIST_IDENTITY_LINK_TOKEN = DOOR_TOKEN


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(slug="idlink-2442", name="Салон 2442")


@pytest.fixture
def person(tenant) -> BotUser:
    return BotUser.all_tenants.create(tenant=tenant, channel="max", channel_user_id="2442001")


def _url(specialist_id: str = SPECIALIST) -> str:
    return f"{BASE}/api/v1/internal/specialists/{specialist_id}/identity/"


def _ok(*, status_code: int = 201) -> dict:
    return {
        "data": {
            "specialist_id": SPECIALIST,
            "ayla_user_id": AYLA_USER,
            "status": "created" if status_code == 201 else "replayed",
        }
    }


def _refusal(reason: str) -> dict:
    return {
        "error": {
            "code": "SPECIALIST_IDENTITY_LINK_REFUSED",
            "message": "x",
            "details": {"reason": reason},
        }
    }


def _master(
    tenant: Tenant, person: BotUser, *, specialist_id: str | None = SPECIALIST
) -> CatalogMaster:
    from django.utils import timezone

    return CatalogMaster.all_tenants.create(
        tenant=tenant,
        name="Мария",
        linked_bot_user=person,
        invite_status=ACCEPTED,
        catalog_specialist_id=specialist_id,
        is_active=True,
        external_updated_at=timezone.now(),
    )


@contextmanager
def _capturing(caplog):
    handlers = [logging.getLogger(name) for name in LOGGERS]
    for target in handlers:
        target.addHandler(caplog.handler)
    try:
        with caplog.at_level(logging.DEBUG):
            yield
    finally:
        for target in handlers:
            target.removeHandler(caplog.handler)


# ─── проводная форма двери ──────────────────────────────────────────────────


class TestTheDoorIsCalledAsAgreed:
    def test_the_request_carries_the_fifth_secret_the_key_and_the_actor(
        self, httpx_mock: HTTPXMock, tenant, person
    ) -> None:
        httpx_mock.add_response(method="POST", url=_url(), json=_ok(), status_code=201)

        outcome = bind_master_identity_in_catalog(specialist_id=SPECIALIST, bot_user=person)

        request = httpx_mock.get_requests()[0]
        assert request.headers["Authorization"] == f"Bearer {DOOR_TOKEN}"
        body = json.loads(request.content)
        assert body["actor"] == ACTOR_INVITE_ACCEPT
        assert body["idempotency_key"] == idempotency_key_for(
            SPECIALIST,
            body["external_user_id"],
        )
        assert outcome.created is True
        assert str(outcome.ayla_user_id) == AYLA_USER

    def test_the_key_is_the_same_for_the_same_pair_and_differs_per_pair(self, person) -> None:
        first = idempotency_key_for(SPECIALIST, "bot:max:2442001")
        again = idempotency_key_for(SPECIALIST, "bot:max:2442001")
        other_identity = idempotency_key_for(SPECIALIST, "bot:max:2442002")

        assert first == again  # повтор — тот же ключ, каталог отвечает replayed
        assert first != other_identity  # другая личность — другой запрос

    def test_a_replay_is_not_reported_as_created(
        self, httpx_mock: HTTPXMock, tenant, person
    ) -> None:
        httpx_mock.add_response(
            method="POST", url=_url(), json=_ok(status_code=200), status_code=200
        )

        outcome = bind_master_identity_in_catalog(specialist_id=SPECIALIST, bot_user=person)

        assert outcome.created is False

    @pytest.mark.parametrize(
        ("status_code", "reason"),
        (
            (403, "credential_refused"),
            (404, "identity_unknown"),
            (409, "identity_already_bound"),
            (500, "readback_failed"),
        ),
    )
    def test_a_refusal_comes_back_by_name(
        self, httpx_mock: HTTPXMock, person, status_code: int, reason: str
    ) -> None:
        httpx_mock.add_response(
            method="POST",
            url=_url(),
            json=_refusal(reason),
            status_code=status_code,
        )

        with pytest.raises(SpecialistIdentityLinkRefused) as caught:
            bind_master_identity_in_catalog(specialist_id=SPECIALIST, bot_user=person)

        assert caught.value.reason == ("credential_refused" if status_code == 403 else reason)
        assert caught.value.hint  # человеку сказано, что делать

    def test_an_empty_secret_is_its_own_named_refusal(self, settings, person) -> None:
        settings.AYLA_SPECIALIST_IDENTITY_LINK_TOKEN = ""

        with pytest.raises(SpecialistIdentityLinkRefused) as caught:
            bind_master_identity_in_catalog(specialist_id=SPECIALIST, bot_user=person)

        assert caught.value.reason == "token_missing"

    def test_the_secret_never_reaches_the_logs(self, httpx_mock: HTTPXMock, person, caplog) -> None:
        httpx_mock.add_response(method="POST", url=_url(), json=_ok(), status_code=201)

        with _capturing(caplog):
            bind_master_identity_in_catalog(specialist_id=SPECIALIST, bot_user=person)

        assert "identity.specialist_identity_link.created" in caplog.text  # лог есть
        assert DOOR_TOKEN not in caplog.text

    def test_the_secret_never_reaches_the_logs_on_a_refusal(
        self, httpx_mock: HTTPXMock, person, caplog
    ) -> None:
        httpx_mock.add_response(
            method="POST",
            url=_url(),
            json=_refusal("bind_refused"),
            status_code=409,
        )

        with _capturing(caplog):
            with pytest.raises(SpecialistIdentityLinkRefused):
                bind_master_identity_in_catalog(specialist_id=SPECIALIST, bot_user=person)

        assert "reason=bind_refused" in caplog.text  # отказ назван
        assert DOOR_TOKEN not in caplog.text


# ─── место в приёме приглашения ────────────────────────────────────────────


class TestAcceptanceIsNeverBrokenByTheCatalog:
    def test_a_refusal_leaves_acceptance_intact_and_names_the_reason(
        self, httpx_mock: HTTPXMock, tenant, person, caplog
    ) -> None:
        """Мастер уже принял: откат потерял бы погашенное одноразовое приглашение."""
        from apps.master_api.views import _link_identity_in_catalog

        master = _master(tenant, person)
        httpx_mock.add_response(
            method="POST",
            url=_url(),
            json=_refusal("transport_error"),
            status_code=500,
        )

        with _capturing(caplog):
            _link_identity_in_catalog(master, person)  # не поднимает

        master.refresh_from_db()
        assert master.invite_status == ACCEPTED  # приём на месте
        assert master.linked_bot_user_id == person.pk
        assert master.invite_token is None
        assert "identity_link_refused" in caplog.text

    def test_a_row_without_a_catalog_id_is_its_own_branch(self, tenant, person, caplog) -> None:
        """«Связывать не с чем» — не «каталог отказал»: разные строки журнала."""
        from apps.master_api.views import _link_identity_in_catalog

        master = _master(tenant, person, specialist_id=None)

        with _capturing(caplog):
            _link_identity_in_catalog(master, person)

        assert "identity_link_skipped reason=catalog_unlinked" in caplog.text
        assert "identity_link_refused" not in caplog.text

    def test_a_linked_row_reports_the_link(
        self, httpx_mock: HTTPXMock, tenant, person, caplog
    ) -> None:
        from apps.master_api.views import _link_identity_in_catalog

        master = _master(tenant, person)
        httpx_mock.add_response(method="POST", url=_url(), json=_ok(), status_code=201)

        with _capturing(caplog):
            _link_identity_in_catalog(master, person)

        assert "identity_linked" in caplog.text


# ─── команда починки ───────────────────────────────────────────────────────


class TestBackfillCommand:
    def _run(self, *args: str) -> str:
        out = StringIO()
        call_command("link_master_identities", *args, stdout=out)
        return out.getvalue()

    def test_dry_run_is_the_default_and_calls_nothing(
        self, httpx_mock: HTTPXMock, tenant, person
    ) -> None:
        _master(tenant, person)

        text = self._run()

        assert "DRY-RUN pending=1 linked=0 left=1 verdict=dry_run" in text
        assert httpx_mock.get_requests() == []  # каталог не звонился

    def test_an_empty_scope_is_named_not_called_clean(self, tenant) -> None:
        text = self._run()

        assert "pending=0" in text
        assert "verdict=nothing_to_link" in text  # ноль верен и ничего не доказывает

    def test_apply_links_the_pending_rows_and_says_linked_all(
        self, httpx_mock: HTTPXMock, tenant, person
    ) -> None:
        _master(tenant, person)
        httpx_mock.add_response(method="POST", url=_url(), json=_ok(), status_code=201)

        text = self._run("--apply")

        assert "APPLIED pending=1 linked=1 left=0 verdict=linked_all" in text
        request = httpx_mock.get_requests()[0]
        assert json.loads(request.content)["actor"] == ACTOR_BACKFILL

    def test_a_refusal_lands_in_left_with_its_reason(
        self, httpx_mock: HTTPXMock, tenant, person
    ) -> None:
        _master(tenant, person)
        httpx_mock.add_response(
            method="POST",
            url=_url(),
            json=_refusal("identity_already_bound"),
            status_code=409,
        )

        text = self._run("--apply")

        assert "pending=1 linked=0 left=1 verdict=partial" in text
        assert "reasons=identity_already_bound=1" in text

    def test_a_repeat_creates_no_second_link(self, httpx_mock: HTTPXMock, tenant, person) -> None:
        """Идемпотентность пачки: тот же ключ, каталог отвечает replayed."""
        _master(tenant, person)
        httpx_mock.add_response(method="POST", url=_url(), json=_ok(), status_code=201)
        httpx_mock.add_response(
            method="POST",
            url=_url(),
            json=_ok(status_code=200),
            status_code=200,
        )

        first = self._run("--apply")
        second = self._run("--apply")

        assert "linked=1 left=0 verdict=linked_all" in first
        assert "linked=1 left=0 verdict=linked_all" in second  # состояние, не новая запись
        keys = {json.loads(r.content)["idempotency_key"] for r in httpx_mock.get_requests()}
        assert len(keys) == 1  # один и тот же ключ — второй связи не будет

    def test_a_row_without_a_catalog_id_is_not_pending(
        self, httpx_mock: HTTPXMock, tenant, person
    ) -> None:
        _master(tenant, person, specialist_id=None)

        text = self._run("--apply")

        assert "pending=0" in text
        assert httpx_mock.get_requests() == []

    def test_an_archived_row_is_not_pending(self, httpx_mock: HTTPXMock, tenant, person) -> None:
        from django.utils import timezone

        master = _master(tenant, person)
        CatalogMaster.all_tenants.filter(pk=master.pk).update(archived_at=timezone.now())

        text = self._run("--apply")

        assert "pending=0" in text
