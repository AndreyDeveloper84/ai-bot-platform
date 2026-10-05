"""DRF-2769 (фаза 2) — утренний итог приходит и мастерам: каждому его день и его кнопки.

До правки адресаты итога — только управляющие (``MANAGER``: активные
владелец/администратор); мастер о своём дне узнавал, лишь сам открыв бота.
Решение владельца: «мастерам слать». Своя половина — под своим выключателем
``SALON_MASTER_DIGEST_ENABLED`` (False по умолчанию): где итог управляющим уже
включён, выкладка не должна сама начать писать всем мастерам.

* m1 — выключатель мастеров закрыт: итог только управляющему (как до правки);
* m2 — открыт: мастеру — его день (строки приветствия мастера, один источник)
  и его кнопки; управляющему — прежний итог;
* m3 — мастер, который сам владелец, получает одно сообщение — управляющего;
* m4 — один итог в день и мастеру: второй тик в ту же дату — без дубля;
* m5 — день мастера не прочитан: итог ему не уходит (пустой «Итог» хуже
  молчания), управляющему — уходит;
* m6 — салон без управляющих, но с мастером: итог мастеру уходит; при
  закрытом выключателе — ``no_recipients``, как раньше;
* m7 — итог мастера идёт только ему: управляющему его копия не приходит.
"""

from __future__ import annotations

import itertools
import uuid
from collections import Counter
from unittest.mock import patch

import pytest
from django.utils import timezone

from apps.channels.bot_registry import BotEntry
from apps.channels.max.tests import test_salon_morning_digest_2118 as _digest

pytestmark = pytest.mark.django_db

_at = _digest._at
_data = _digest._data


def _registry(settings, *, masters: bool) -> None:
    settings.MAX_BOT_REGISTRY = (
        BotEntry(
            slug="client",
            webhook_secret="wh-client",  # pragma: allowlist secret
            api_token="token-client",  # pragma: allowlist secret
            stream="max_global",
        ),
        BotEntry(
            slug="salon",
            webhook_secret="wh-salon",  # pragma: allowlist secret
            api_token=_digest.SALON_TOKEN,
            stream="max_salon",
            miniapp_url="https://app.example",
        ),
    )
    settings.MAX_BOT_TOKEN = "token-client"  # pragma: allowlist secret
    settings.SALON_MORNING_DIGEST_ENABLED = True
    settings.SALON_MASTER_DIGEST_ENABLED = masters


@pytest.fixture(autouse=True)
def _clear_cache():
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def sent(monkeypatch) -> list[dict]:
    seen: list[dict] = []

    def _fake_send(*, text, chat_id=None, user_id=None, attachments=None, **_):
        buttons = [
            b
            for a in (attachments or [])
            for row in a.get("payload", {}).get("buttons", [])
            for b in row
        ]
        seen.append({"user_id": str(user_id), "text": text, "buttons": buttons})
        return {"ok": True}

    monkeypatch.setattr("apps.channels.max.outbound.send_message", _fake_send)
    return seen


_EXTERNAL_IDS = itertools.count(2769001)


def _master(tenant, *, channel_user_id: str, also_owner: bool = False):
    from apps.catalog.models import CatalogMaster
    from apps.identity.models import BotUser
    from apps.tenancy.models import TenantStaff

    user = BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id=channel_user_id,
        chat_id=f"c-{channel_user_id}",
        display_name="Анна Петрова",
    )
    CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_id=-next(_EXTERNAL_IDS),  # уникален в прогоне, не hash() (DRF-1158)
        external_updated_at=timezone.now(),
        name="Анна Петрова",
        linked_bot_user=user,
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        is_active=True,
        accepted_at=timezone.now(),
        ayla_user_id=uuid.uuid4(),
    )
    if also_owner:
        TenantStaff.all_tenants.create(tenant=tenant, bot_user=user, role=TenantStaff.Role.OWNER)
    return user


def _fake_gather(tenant, role_ctx, *, now=None):
    """День мастера — 3 записи; управляющему — обычная сводка."""
    if getattr(role_ctx, "is_master", False) and not getattr(role_ctx, "is_owner", False):
        return _data(my_records=3)
    return _data()


def _tick(**patches) -> dict:
    from apps.channels.max import salon_digest as sd, salon_greeting as sg

    with (
        patch.object(sd, "gather_digest", return_value=_data()),
        patch.object(sg, "gather", patches.get("gather", _fake_gather)),
    ):
        return sd.send_morning_digests.apply(kwargs={"now_utc": _at(9)}).get()


def _to(sent: list[dict], user_id: str) -> list[dict]:
    return [m for m in sent if m["user_id"] == user_id]


class TestM1MastersSwitchClosed:
    def test_only_the_manager_gets_the_digest(self, settings, sent) -> None:
        _registry(settings, masters=False)
        salon = _digest._salon("m1")
        _master(salon, channel_user_id="master-m1")

        _tick()

        # Presence first: the manager's digest went out.
        assert len(_to(sent, "owner-m1")) == 1
        assert _to(sent, "master-m1") == []


class TestM2MasterGetsHisDay:
    def test_master_digest_is_his_day_and_his_buttons(self, settings, sent) -> None:
        from apps.channels.max import salon_greeting as sg

        _registry(settings, masters=True)
        salon = _digest._salon("m2")
        _master(salon, channel_user_id="master-m2")

        counters = _tick()

        to_master = _to(sent, "master-m2")
        assert len(to_master) == 1, sent
        text = to_master[0]["text"]
        assert text.startswith("Доброе утро! Итог на ")
        for line in sg.render_master_summary_lines(_data(my_records=3)):
            assert line in text, (line, text)
        assert "Сегодня у вас 3 записи." in text  # литералом: строка мастера из приветствия
        assert "В салоне" not in text  # не сводка управляющего
        labels = [b.get("text") for b in to_master[0]["buttons"]]
        assert labels == ["📅 Мой день", "Открыть кабинет", "Расписание", "Спросить Ayla"], labels
        callbacks = [
            b.get("payload") for b in to_master[0]["buttons"] if b.get("type") == "callback"
        ]
        assert callbacks == ["cb:staff:day"]
        # Управляющему — прежний итог, одно сообщение.
        assert len(_to(sent, "owner-m2")) == 1
        assert counters["masters_sent"] == 1
        assert counters["sent"] == 1


class TestM3MasterWhoIsOwner:
    def test_gets_one_message_the_managers(self, settings, sent) -> None:
        _registry(settings, masters=True)
        salon = _digest._salon("m3", with_owner=False)
        _master(salon, channel_user_id="boss-m3", also_owner=True)

        counters = _tick()

        to_boss = _to(sent, "boss-m3")
        assert len(to_boss) == 1, sent
        assert "Сегодня у вас" not in to_boss[0]["text"]  # итог управляющего, не мастерский
        assert counters["masters_sent"] == 0


class TestM4OnePerDay:
    def test_second_tick_same_date_sends_the_master_nothing(self, settings, sent) -> None:
        _registry(settings, masters=True)
        salon = _digest._salon("m4")
        _master(salon, channel_user_id="master-m4")

        first = _tick()
        second = _tick()

        assert len(_to(sent, "master-m4")) == 1
        assert first["masters_sent"] == 1
        assert second["masters_sent"] == 0


class TestM5UnreadDay:
    def test_master_with_an_unread_day_gets_nothing_manager_still_does(
        self, settings, sent
    ) -> None:
        _registry(settings, masters=True)
        salon = _digest._salon("m5")
        _master(salon, channel_user_id="master-m5")

        def _unread(tenant, role_ctx, *, now=None):
            return _data(my_records=None) if getattr(role_ctx, "is_master", False) else _data()

        counters = _tick(gather=_unread)

        assert len(_to(sent, "owner-m5")) == 1  # positive: the tick did run
        assert _to(sent, "master-m5") == []
        assert counters["masters_source_failed"] == 1


class TestM6SalonWithoutManagers:
    def test_the_master_still_gets_his_digest(self, settings, sent) -> None:
        _registry(settings, masters=True)
        salon = _digest._salon("m6", with_owner=False)
        _master(salon, channel_user_id="master-m6")

        counters = _tick()

        assert len(_to(sent, "master-m6")) == 1
        assert counters["masters_sent"] == 1

    def test_with_the_switch_closed_it_is_no_recipients_as_before(self, settings, sent) -> None:
        _registry(settings, masters=False)
        salon = _digest._salon("m6b", with_owner=False)
        _master(salon, channel_user_id="master-m6b")

        counters = _tick()

        assert sent == []
        assert counters["no_recipients"] == 1


class TestM7MasterDigestGoesToTheMasterOnly:
    def test_the_manager_does_not_receive_the_masters_copy(self, settings, sent) -> None:
        _registry(settings, masters=True)
        salon = _digest._salon("m7")
        _master(salon, channel_user_id="master-m7")
        _master(salon, channel_user_id="master-m7b")

        _tick()

        per_user = Counter(m["user_id"] for m in sent)
        assert per_user == {"owner-m7": 1, "master-m7": 1, "master-m7b": 1}, per_user
        assert all("Сегодня у вас" not in m["text"] for m in _to(sent, "owner-m7"))
