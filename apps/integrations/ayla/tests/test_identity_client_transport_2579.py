"""DRF-2579: общий клиент личностей называет КАЖДЫЙ транспортный отказ.

``resolve_identity`` ловил только ``TimeoutException`` и ``NetworkError``.
``httpx.RemoteProtocolError`` — сервер закрыл соединение на полуслове (рестарт
воркера каталога) — выходил сырым, и ``attempt_solo_link``, который по
докстрингу исключений из-за недоступности Ayla не выпускает, ронял
регистрацию соло-мастера.

Узлы стоят на обрыве соединения, а не на таймауте: таймаут ловился и до
правки, и узел только на нём прошёл бы при этом дефекте. Рядом — таймаут как
пара: у него своё прежнее имя, и правка его не трогает.
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from apps.integrations.ayla import identity_client
from apps.integrations.ayla.identity_client import IdentityResolveError, resolve_identity

EXTERNAL = "bot:max:2579001"


@pytest.fixture(autouse=True)
def _configured(settings, monkeypatch):
    settings.AYLA_BASE_URL = "http://catalog.test"
    settings.AYLA_INTERNAL_API_TOKEN = "test-token-2579"  # noqa: S105
    # Размыкатель — состояние модуля: свежий на каждый узел, иначе отказы
    # одного узла открыли бы его для следующего.
    monkeypatch.setattr(identity_client, "_circuit", identity_client._Circuit())


def _transport_raising(exc: Exception, monkeypatch) -> None:
    real_client = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    def client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(identity_client.httpx, "Client", client)


class TestEveryTransportFailureIsNamed:
    def test_a_dropped_connection_is_a_named_refusal(self, monkeypatch) -> None:
        _transport_raising(httpx.RemoteProtocolError("Server disconnected"), monkeypatch)

        with pytest.raises(IdentityResolveError) as exc:
            resolve_identity(EXTERNAL)

        # Имя постоянное; тип исключения в причину не попадает.
        assert str(exc.value) == "network: transport_failure"
        assert "RemoteProtocolError" not in str(exc.value)

    def test_a_timeout_keeps_its_own_name(self, monkeypatch) -> None:
        """Пара: таймаут ловился и до правки — у него прежнее имя."""
        _transport_raising(httpx.ReadTimeout("slow"), monkeypatch)

        with pytest.raises(IdentityResolveError) as exc:
            resolve_identity(EXTERNAL)

        assert str(exc.value) == "network: ReadTimeout"

    def test_a_dropped_connection_counts_toward_the_breaker(self, monkeypatch) -> None:
        """Обрыв — недоступность, как таймаут: размыкатель его считает."""
        _transport_raising(httpx.RemoteProtocolError("Server disconnected"), monkeypatch)
        recorded: list[float] = []
        monkeypatch.setattr(
            identity_client._circuit, "record_failure", lambda *, now: recorded.append(now)
        )

        with pytest.raises(IdentityResolveError):
            resolve_identity(EXTERNAL)

        assert len(recorded) == 1


class TestSoloRegistrationSurvivesADroppedConnection:
    def test_attempt_solo_link_returns_a_name_instead_of_raising(self, monkeypatch) -> None:
        """Красное листа: до правки исключение уходило из ``attempt_solo_link``
        насквозь и роняло регистрацию соло-мастера."""
        from apps.identity.services.solo_link_attempt import AYLA_UNREACHABLE, attempt_solo_link

        _transport_raising(httpx.RemoteProtocolError("Server disconnected"), monkeypatch)
        bot_user = SimpleNamespace(pk=1, channel="max", channel_user_id="2579001")
        master = SimpleNamespace(pk=1)

        assert attempt_solo_link(master, bot_user) == AYLA_UNREACHABLE
