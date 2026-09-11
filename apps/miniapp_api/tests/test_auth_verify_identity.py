"""DRF-1319 B+E — ``/auth/verify`` называет, кто перед нами, в словаре §124.

Решение владельца §124: различать ``identified channel user`` (MAX
``initData`` достоверно назвал человека) и ``registered Ayla subject``
(доменная сущность). Онбординг начинается с первого и создаёт или
привязывает второе. «Аноним» как понятие в пилотном канале не нужен.

Что проверяется:

* блок ``identity`` есть всегда и несёт ровно три ключа;
* ``channel``: ``identified`` при верифицированном initData,
  ``dev_bypass`` на DEBUG-обходе — канал человека не называл, и ответ
  этого не скрывает;
* ``subject``: привязка делается здесь, при входе, через
  ``ensure_ayla_link`` (E: регистрация = привязка субъекта, не экран);
  ``linked`` с ``ayla_user_id``, когда Ayla ответила; ``unlinked`` и
  всё равно 200, когда нет — вход не ломается;
* положительная стража: уже привязанный пользователь в сеть не ходит.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time as time_module
import uuid
from typing import Any
from urllib.parse import urlencode

import pytest
from django.test import Client, override_settings
from django.urls import reverse

from apps.identity.models import BotUser
from apps.integrations.ayla.identity_client import IdentityResolveError, ResolvedIdentity
from apps.tenancy.models import Tenant

BOT_TOKEN = "test-bot-token-identity"


def _sign(params: dict[str, str]) -> str:
    data_check_string = "\n".join(f"{k}={params[k]}" for k in sorted(params))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode({**params, "hash": digest}, doseq=False)


def _header(user_id: str = "777001") -> str:
    params = {
        "user": json.dumps({"id": int(user_id), "first_name": "Ира"}),
        "auth_date": str(int(time_module.time())),
    }
    return f"MaxInitData {_sign(params)}"


@pytest.fixture(autouse=True)
def _settings(settings) -> None:
    settings.MAX_BOT_TOKEN = BOT_TOKEN
    settings.MAX_BOT_TENANT_SLUG = "identity-test"


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="identity-test", name="Identity", timezone="Europe/Moscow")


@pytest.fixture
def bot_user(tenant: Tenant) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant, channel="max", channel_user_id="777001", display_name="Ира", chat_id="777001"
    )


@pytest.fixture
def stub_resolve(monkeypatch) -> Any:
    """Подмена HTTP-плеча ``resolve_identity`` — там же, где его импортирует
    ``ensure_ayla_link`` (лениво, из ``identity_client``)."""
    calls: list[str] = []
    state: dict[str, Any] = {"uuid": uuid.uuid4(), "error": None}

    def _fake(external_user_id: str) -> ResolvedIdentity:
        calls.append(external_user_id)
        if state["error"] is not None:
            raise state["error"]
        return ResolvedIdentity(ayla_user_id=state["uuid"], is_proxy=True)

    monkeypatch.setattr(
        "apps.integrations.ayla.identity_client.resolve_identity", _fake, raising=True
    )
    return type("Stub", (), {"calls": calls, "state": state})()


def _verify(client: Client) -> dict[str, Any]:
    resp = client.post(reverse("miniapp_api:auth_verify"), HTTP_AUTHORIZATION=_header())
    assert resp.status_code == 200, resp.content
    return resp.json()


@pytest.mark.django_db
class TestIdentityBlock:
    def test_identified_and_linked_on_first_launch(
        self, client: Client, bot_user: BotUser, stub_resolve
    ) -> None:
        data = _verify(client)

        assert set(data["identity"]) == {"channel", "subject", "ayla_user_id"}
        assert data["identity"]["channel"] == "identified"
        assert data["identity"]["subject"] == "linked"
        assert data["identity"]["ayla_user_id"] == str(stub_resolve.state["uuid"])
        # Привязка — durable: следующий вход её не повторяет.
        bot_user.refresh_from_db()
        assert str(bot_user.ayla_user_id) == str(stub_resolve.state["uuid"])
        assert stub_resolve.calls == ["bot:max:777001"]

    def test_ayla_down_means_unlinked_not_an_error(
        self, client: Client, bot_user: BotUser, stub_resolve
    ) -> None:
        """Вход не ломается: ``unlinked`` — состояние, а не 5xx."""
        stub_resolve.state["error"] = IdentityResolveError("network: ReadTimeout")

        data = _verify(client)

        assert data["identity"] == {
            "channel": "identified",
            "subject": "unlinked",
            "ayla_user_id": None,
        }
        bot_user.refresh_from_db()
        assert bot_user.ayla_user_id is None

    def test_already_linked_user_does_not_hit_ayla(
        self, client: Client, bot_user: BotUser, stub_resolve
    ) -> None:
        """Положительная стража на идемпотентность: кеш по ``ayla_user_id``."""
        known = uuid.uuid4()
        bot_user.ayla_user_id = known
        bot_user.save(update_fields=["ayla_user_id"])

        data = _verify(client)

        assert data["identity"]["subject"] == "linked"
        assert data["identity"]["ayla_user_id"] == str(known)
        assert stub_resolve.calls == [], "привязанный не должен ходить в Ayla повторно"

    def test_unlinked_is_retried_on_next_launch(
        self, client: Client, bot_user: BotUser, stub_resolve
    ) -> None:
        """Из ``unlinked`` есть выход: следующий вход пробует снова (§122)."""
        stub_resolve.state["error"] = IdentityResolveError("server: HTTP 502")
        assert _verify(client)["identity"]["subject"] == "unlinked"

        stub_resolve.state["error"] = None
        assert _verify(client)["identity"]["subject"] == "linked"
        assert len(stub_resolve.calls) == 2

    def test_no_guest_vocabulary_in_the_contract(
        self, client: Client, bot_user: BotUser, stub_resolve
    ) -> None:
        """Слов «anonymous» / «guest» в ответе нет — их нет в модели §124."""
        raw = json.dumps(_verify(client)).lower()
        # Присутствие впереди: словарь §124 в ответе есть — и только он.
        assert '"channel": "identified"' in raw
        assert '"subject": "linked"' in raw
        assert "anonymous" not in raw
        assert "guest" not in raw


@pytest.mark.django_db
class TestDevBypassIsNamedAsSuch:
    @override_settings(DEBUG=True)
    def test_channel_is_dev_bypass_not_identified(
        self, client: Client, tenant: Tenant, bot_user: BotUser, stub_resolve
    ) -> None:
        """DEBUG-обход: человека канал не называл — и ответ так и говорит."""
        resp = client.post(
            reverse("miniapp_api:auth_verify"),
            HTTP_X_DEV_BYPASS="1",
            HTTP_X_DEV_USER_ID=str(bot_user.id),
            HTTP_X_DEV_TENANT_SLUG=tenant.slug,
        )
        assert resp.status_code == 200, resp.content
        identity = resp.json()["identity"]
        assert identity["channel"] == "dev_bypass"
        # Субъект при этом привязывается как обычно: обход — про канал, не про Ayla.
        assert identity["subject"] == "linked"
