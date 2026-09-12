"""proxy → real: the one overwrite ``ayla_link`` permits (DRF-1790).

Until 12.09.2026 the module had two rules that together made a linked person
invisible to the bot forever:

* ``ensure_ayla_link`` short-circuited on ANY stored key — including the
  proxy key the catalog answers before a binding exists — so it never asked
  the catalog again;
* ``_persist`` never overwrote — so even when asked, the real key would have
  been counted as a «conflict» and dropped.

The catalog binds proxy → real once (operator, §6 #1663; later OTP, Phase 1)
and from then on answers with the REAL key. The bot kept the proxy key, and
after B-2.1/B-2.2 every subject-bound call it made — export, personal
context, the profile card — carried a URL subject that no longer matched
its own header: 403 everywhere, and ``user.profile.updated`` for the real
key found no row at all. Found while drawing Phase 1 OTP boundaries
(``MEASUREMENT_PHASE1_OTP_SELF_LINK_2026-09-12.md`` §6).

Rule now: «never overwrite» is loosened by exactly one transition — a
stored key whose sort is proxy (``True``) or unknown (``NULL``) may be
replaced by a REAL key (``is_proxy=False``), with ``identity.rebound``
carrying old and new; a key of known-real sort is cached and never
replaced; proxy → proxy and real → anything different stay conflicts. A
stored proxy key is not a cache hit any more: every dependent action asks
the catalog again until the answer is real — the cost of one HTTP call per
dependent action for an unlinked person, which J-O3 permits (dependent
action, never «hello»).
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import patch

import pytest

from apps.identity.models import BotUser
from apps.identity.services.ayla_link import ensure_ayla_link
from apps.integrations.ayla.identity_client import ResolvedIdentity
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

CHANNEL = "max"
CHANNEL_USER_ID = "rebound-1790"


@pytest.fixture
def stub_resolve(monkeypatch: pytest.MonkeyPatch) -> Any:
    calls: list[str] = []
    state: dict[str, Any] = {"uuid": uuid.uuid4(), "is_proxy": True}

    def _fake(external_user_id: str) -> ResolvedIdentity:
        calls.append(external_user_id)
        return ResolvedIdentity(ayla_user_id=state["uuid"], is_proxy=state["is_proxy"])

    monkeypatch.setattr(
        "apps.integrations.ayla.identity_client.resolve_identity", _fake, raising=True
    )
    return type("Stub", (), {"calls": calls, "state": state})()


@pytest.fixture
def person() -> BotUser:
    from apps.identity.services import resolve_or_create_global_bot_user

    return resolve_or_create_global_bot_user(channel=CHANNEL, channel_user_id=CHANNEL_USER_ID)


def _rows() -> list[BotUser]:
    return list(BotUser.all_tenants.filter(channel=CHANNEL, channel_user_id=CHANNEL_USER_ID))


class TestProxyThenRealIsTheOnePermittedOverwrite:
    def test_the_real_key_replaces_the_proxy_key(self, person, stub_resolve):
        """Красный до правки: второй вызов — cache hit, ключ прокси навсегда."""

        proxy_key = stub_resolve.state["uuid"]
        assert ensure_ayla_link(person, trigger="booking") == proxy_key
        person.refresh_from_db()
        assert person.ayla_user_id_is_proxy is True

        real_key = uuid.uuid4()
        stub_resolve.state.update(uuid=real_key, is_proxy=False)

        with patch("apps.identity.services.ayla_link.emit") as emitted:
            resolved = ensure_ayla_link(person, trigger="booking")

        assert resolved == real_key
        person.refresh_from_db()
        assert person.ayla_user_id == real_key
        assert person.ayla_user_id_is_proxy is False
        rebound = [c for c in emitted.call_args_list if c.args[0] == "identity.rebound"]
        assert len(rebound) == 1, [c.args[0] for c in emitted.call_args_list]
        props = rebound[0].kwargs["properties"]
        assert props["old_ayla_user_id"] == str(proxy_key)
        assert props["new_ayla_user_id"] == str(real_key)
        assert props["trigger"] == "booking"

    def test_a_stored_proxy_key_is_not_a_cache_hit(self, person, stub_resolve):
        """Красный до правки: AC-4 «один резолв, потом cache hit» распространялся и на прокси."""

        ensure_ayla_link(person, trigger="booking")
        ensure_ayla_link(person, trigger="memory_write")

        assert len(stub_resolve.calls) == 2

    def test_a_known_real_key_is_cached(self, person, stub_resolve):
        """Что осталось от AC-4: настоящий ключ — cache hit, каталог не спрашивается."""

        stub_resolve.state["is_proxy"] = False
        first = ensure_ayla_link(person, trigger="booking")
        second = ensure_ayla_link(person, trigger="memory_write")

        assert first == second
        assert len(stub_resolve.calls) == 1

    def test_rebound_reaches_every_shell_of_the_person(self, person, stub_resolve):
        """Fan-out, как у первичной записи: обе строки личности получают настоящий ключ."""

        tenant = Tenant.objects.create(slug="rebound-salon", name="Салон")
        BotUser.all_tenants.create(tenant=tenant, channel=CHANNEL, channel_user_id=CHANNEL_USER_ID)
        ensure_ayla_link(person, trigger="booking")
        real_key = uuid.uuid4()
        stub_resolve.state.update(uuid=real_key, is_proxy=False)

        ensure_ayla_link(person, trigger="booking")

        assert {str(r.ayla_user_id) for r in _rows()} == {str(real_key)}
        assert {r.ayla_user_id_is_proxy for r in _rows()} == {False}

    def test_an_unknown_sort_learns_it_without_changing_the_key(self, person, stub_resolve):
        """NULL-сорт (строки до DRF-1649): тот же ключ → сорт записывается, ключ на месте.

        Без этого строка с неизвестным сортом переспрашивала бы каталог на
        каждом действии вечно.
        """

        key = stub_resolve.state["uuid"]
        BotUser.all_tenants.filter(pk=person.pk).update(
            ayla_user_id=key, ayla_user_id_is_proxy=None
        )
        person.refresh_from_db()

        assert ensure_ayla_link(person, trigger="booking") == key
        person.refresh_from_db()
        assert person.ayla_user_id == key
        assert person.ayla_user_id_is_proxy is True
        assert len(stub_resolve.calls) == 1


class TestAFailedReAskNeverUnlinks:
    def test_the_stored_proxy_key_survives_an_unreachable_catalog(
        self, person, stub_resolve, monkeypatch
    ):
        """Переспрос упал → возвращается сохранённый ключ, не ``None``.

        Иначе перепроверка прокси на каждом действии превратила бы любой
        сбой каталога в «человек не привязан» — и бронирование ушло бы в
        AdminTask у того, у кого ключ уже был. Красный на первой редакции
        этой правки: шесть соседних тестов памяти/бронирования поймали это.
        """
        from apps.integrations.ayla.identity_client import IdentityResolveError

        proxy_key = ensure_ayla_link(person, trigger="booking")

        def _down(external_user_id):
            raise IdentityResolveError("network: ReadTimeout")

        monkeypatch.setattr("apps.integrations.ayla.identity_client.resolve_identity", _down)

        assert ensure_ayla_link(person, trigger="memory_write") == proxy_key
        person.refresh_from_db()
        assert person.ayla_user_id == proxy_key

    def test_a_person_with_no_key_still_gets_none(self, person, monkeypatch):
        """Отрицательный контроль: без сохранённого ключа сбой — по-прежнему ``None``."""
        from apps.integrations.ayla.identity_client import IdentityResolveError

        def _down(external_user_id):
            raise IdentityResolveError("network: ReadTimeout")

        monkeypatch.setattr("apps.integrations.ayla.identity_client.resolve_identity", _down)

        assert ensure_ayla_link(person, trigger="booking") is None


class TestEverythingElseIsStillAConflict:
    def test_a_real_key_is_never_replaced(self, person, stub_resolve):
        """Настоящий ключ — константа: другой настоящий ключ = конфликт, не переезд."""

        stub_resolve.state["is_proxy"] = False
        first = ensure_ayla_link(person, trigger="booking")
        BotUser.all_tenants.filter(pk=person.pk).update(
            ayla_user_id_is_proxy=None
        )  # force a re-ask
        person.refresh_from_db()
        stub_resolve.state["uuid"] = uuid.uuid4()

        with patch("apps.identity.services.ayla_link.emit") as emitted:
            ensure_ayla_link(person, trigger="booking")

        person.refresh_from_db()
        # Rebound is permitted only from a KNOWN proxy (sort True). A stored
        # key of unknown sort that differs from a real answer is a conflict,
        # as before: the module cannot tell a legacy real key from a legacy
        # proxy key, and guessing «proxy» would let a real identity be moved.
        assert person.ayla_user_id == first
        assert any(c.args[0] == "identity.ayla_link.conflict" for c in emitted.call_args_list)

    def test_proxy_to_a_different_proxy_is_a_conflict(self, person, stub_resolve):
        """Цепочек прокси→прокси нет (каталог их не создаёт); появление — конфликт."""

        first = ensure_ayla_link(person, trigger="booking")
        stub_resolve.state["uuid"] = uuid.uuid4()  # still is_proxy=True

        with patch("apps.identity.services.ayla_link.emit") as emitted:
            ensure_ayla_link(person, trigger="booking")

        person.refresh_from_db()
        assert person.ayla_user_id == first
        assert any(c.args[0] == "identity.ayla_link.conflict" for c in emitted.call_args_list)
        assert not any(c.args[0] == "identity.rebound" for c in emitted.call_args_list)
