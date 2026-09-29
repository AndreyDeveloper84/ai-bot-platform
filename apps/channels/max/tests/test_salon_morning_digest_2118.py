"""«Утренний итог» салонного бота — тип 6 DRF-2118 (§50 п.8), PR-2.

Замер на нетронутом ``dev 283e9b2b`` (20.09): сводка владельцу/админу
существует только как приветствие при входе (``salon_greeting.gather`` +
``render_admin``, DRF-2114) — сама по расписанию не приходит; beat-задачи
нет, настройки часа у салона нет (``Tenant.timezone`` есть, часа — нет),
квоты и дедупа по дате — нет.

Сторожа на :mod:`apps.channels.max.salon_digest`:

* p1 — **планировщик** ``plan_morning_digests(now_utc)``: салон в свой час
  (``Tenant.features["morning_digest_hour"]``, иначе :data:`DEFAULT_HOUR` =
  09:00 по ``Tenant.timezone``) → ``send``; другой час → ``not_digest_hour``;
  без активного владельца/админа → ``no_recipients``; ``global_bot`` —
  не кандидат; до 07:00 местного — ``night`` даже при настроенном часе;
* p2 — **узел квоты**: два вызова beat в одну местную дату → одно сообщение
  (дедуп по ``(tenant, date)`` через ``salon_notify``); следующая дата — новое;
* p3 — **содержимое = сводка приветствия 2114**: строки собираются тем же
  ``gather`` и тем же рендером строк, что ``render_admin`` (один источник);
  отказ источника → строка опущена, не «0»;
* p4 — задача и beat: ``salon_notify.send_morning_digests`` есть в
  ``CELERY_BEAT_SCHEDULE``; выключатель ``SALON_MORNING_DIGEST_ENABLED``
  по умолчанию False → задача no-op с именованной причиной.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone as dt_timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from apps.channels.bot_registry import BotEntry

SALON_TOKEN = "token-salon"  # pragma: allowlist secret


@pytest.fixture
def two_bots(settings):
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
            api_token=SALON_TOKEN,
            stream="max_salon",
            miniapp_url="https://app.example",
        ),
    )
    settings.MAX_BOT_TOKEN = "token-client"  # pragma: allowlist secret
    settings.SALON_MORNING_DIGEST_ENABLED = True
    return settings


@pytest.fixture
def capture(monkeypatch):
    seen: list[dict] = []

    def _fake_send(*, text, chat_id=None, user_id=None, attachments=None, **_):
        from apps.channels.max.outbound import _token

        seen.append({"token": _token(), "user_id": user_id, "text": text})
        return {"ok": True}

    monkeypatch.setattr("apps.channels.max.outbound.send_message", _fake_send)
    return seen


@pytest.fixture(autouse=True)
def _clear_cache():
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


def _salon(slug: str, *, tz: str = "Europe/Moscow", with_owner: bool = True, features=None):
    from apps.identity.models import BotUser
    from apps.tenancy.models import Tenant, TenantStaff

    tenant = Tenant.objects.create(
        slug=slug, name=f"Салон {slug}", timezone=tz, features=features or {}
    )
    if with_owner:
        owner = BotUser.all_tenants.create(
            tenant=tenant, channel="max", channel_user_id=f"owner-{slug}", chat_id=f"c-{slug}"
        )
        TenantStaff.all_tenants.create(tenant=tenant, bot_user=owner, role=TenantStaff.Role.OWNER)
    return tenant


def _at(hour_msk: int, minute: int = 50) -> datetime:
    """UTC-момент, когда в Москве ``hour_msk``:``minute`` (MSK = UTC+3)."""
    return datetime(2026, 9, 21, hour_msk - 3, minute, tzinfo=dt_timezone.utc)


def _data(**over):
    from apps.channels.max.salon_greeting import GreetingData

    base = dict(records=7, masters_available=3, attention=0, readiness_problems=0)
    base.update(over)
    return GreetingData(**base)


# ── p1 — планировщик ─────────────────────────────────────────────────


@pytest.mark.django_db
class TestP1Planner:
    def test_send_in_the_salon_hour_only(self, two_bots) -> None:
        from apps.channels.max import salon_digest as sd

        moscow = _salon("msk")
        yerevan = _salon("yer", tz="Asia/Yerevan")  # UTC+4: в 09:50 MSK там 10:50
        with patch.object(sd, "gather_digest", return_value=_data()):
            decisions = {d.tenant_slug: d for d in sd.plan_morning_digests(now_utc=_at(9))}
        assert decisions[moscow.slug].send and decisions[moscow.slug].reason == "send"
        assert not decisions[yerevan.slug].send
        assert decisions[yerevan.slug].reason == "not_digest_hour"

    def test_custom_hour_from_tenant_features(self, two_bots) -> None:
        from apps.channels.max import salon_digest as sd

        early = _salon("early", features={"morning_digest_hour": 8})
        with patch.object(sd, "gather_digest", return_value=_data()):
            at_eight = {d.tenant_slug: d for d in sd.plan_morning_digests(now_utc=_at(8))}
            at_nine = {d.tenant_slug: d for d in sd.plan_morning_digests(now_utc=_at(9))}
        assert at_eight[early.slug].send
        assert at_nine[early.slug].reason == "not_digest_hour"

    def test_night_guard_even_with_a_custom_hour(self, two_bots) -> None:
        from apps.channels.max import salon_digest as sd

        night = _salon("night", features={"morning_digest_hour": 5})
        with patch.object(sd, "gather_digest", return_value=_data()):
            decisions = {d.tenant_slug: d for d in sd.plan_morning_digests(now_utc=_at(5))}
        assert decisions[night.slug].reason == "night" and not decisions[night.slug].send

    def test_no_staff_and_global_bot_are_named_or_skipped(self, two_bots) -> None:
        from apps.channels.max import salon_digest as sd
        from apps.identity.constants import GLOBAL_BOT_TENANT_SLUG

        from apps.tenancy.models import Tenant

        empty = _salon("empty", with_owner=False)
        if not Tenant.objects.filter(slug=GLOBAL_BOT_TENANT_SLUG).exists():
            _salon(GLOBAL_BOT_TENANT_SLUG, with_owner=False)
        assert Tenant.objects.filter(slug=GLOBAL_BOT_TENANT_SLUG).exists()  # положительно
        with patch.object(sd, "gather_digest", return_value=_data()):
            decisions = {d.tenant_slug: d for d in sd.plan_morning_digests(now_utc=_at(9))}
        assert decisions[empty.slug].reason == "no_recipients"
        assert GLOBAL_BOT_TENANT_SLUG not in decisions  # empty-assert-ok: положительная пара выше


# ── p2 — квота 1/день ────────────────────────────────────────────────


@pytest.mark.django_db
class TestP2OnePerDay:
    def test_two_beats_in_one_date_send_once(self, two_bots, capture) -> None:
        from apps.channels.max import salon_digest as sd

        salon = _salon("quota")
        with patch.object(sd, "gather_digest", return_value=_data()):
            first = sd.send_morning_digests.apply(kwargs={"now_utc": _at(9, 5)}).result
            second = sd.send_morning_digests.apply(kwargs={"now_utc": _at(9, 50)}).result
        assert first["sent"] == 1 and second["sent"] == 0
        assert second["already_sent_today"] == 1
        assert [c["user_id"] for c in capture] == [f"owner-{salon.slug}"]
        assert capture[0]["token"] == SALON_TOKEN

    def test_next_date_is_a_new_digest(self, two_bots, capture) -> None:
        from apps.channels.max import salon_digest as sd

        _salon("nextday")
        with patch.object(sd, "gather_digest", return_value=_data()):
            sd.send_morning_digests.apply(kwargs={"now_utc": _at(9)})
            tomorrow = datetime(2026, 9, 22, 6, 50, tzinfo=dt_timezone.utc)
            sd.send_morning_digests.apply(kwargs={"now_utc": tomorrow})
        assert len(capture) == 2

    def test_dedup_date_is_the_salon_date_not_utc(self, two_bots, capture) -> None:
        """Владивосток (UTC+10): 09:50 местного = 23:50 UTC ПРЕДЫДУЩЕГО дня.

        Дата дедупа — в поясе салона; иначе итог за 22-е и за 23-е могли бы
        склеиться (оба в UTC-дате 22-го) или разойтись на один beat.
        """
        from apps.channels.max import salon_digest as sd

        _salon("vlad", tz="Asia/Vladivostok")
        with patch.object(sd, "gather_digest", return_value=_data()):
            # 22.09 09:50 Владивосток = 21.09 23:50 UTC
            first = sd.send_morning_digests.apply(
                kwargs={"now_utc": datetime(2026, 9, 21, 23, 50, tzinfo=dt_timezone.utc)}
            ).result
            # 23.09 09:50 Владивосток = 22.09 23:50 UTC — новая местная дата
            second = sd.send_morning_digests.apply(
                kwargs={"now_utc": datetime(2026, 9, 22, 23, 50, tzinfo=dt_timezone.utc)}
            ).result
        assert first["sent"] == 1 and second["sent"] == 1
        assert len(capture) == 2
        assert "22.09" in capture[0]["text"] and "23.09" in capture[1]["text"]


# ── p3 — содержимое = сводка приветствия ─────────────────────────────


@pytest.mark.django_db
class TestP3ContentIsTheGreetingSummary:
    def test_same_lines_as_render_admin(self, two_bots, capture) -> None:
        from apps.channels.max import salon_digest as sd, salon_greeting as sg

        salon = _salon("content")
        data = _data(records=7, masters_available=3, attention=1, readiness_problems=0)
        with patch.object(sd, "gather_digest", return_value=data):
            sd.send_morning_digests.apply(kwargs={"now_utc": _at(9)})
        text = capture[0]["text"]
        expected = sg.render_summary_lines(salon.name, data)
        assert expected  # положительно: строки собраны
        for line in expected:
            assert line in text, (line, text)
        # Те же строки — в приветствии администратора: один источник.
        greeting = sg.render_admin("Ольга", salon.name, "владелец", data)
        for line in expected:
            assert line in greeting, (line, greeting)

    def test_unavailable_source_drops_the_line(self, two_bots, capture) -> None:
        from apps.channels.max import salon_digest as sd

        _salon("partial")
        with patch.object(
            sd, "gather_digest", return_value=_data(records=None, missing=("records",))
        ):
            sd.send_morning_digests.apply(kwargs={"now_utc": _at(9)})
        text = capture[0]["text"]
        assert "мастер" in text  # положительно: доступная строка на месте
        assert "запис" not in text.lower()  # empty-assert-ok: положительная пара строкой выше
        assert "0 записей" not in text

    def test_all_sources_failed_sends_nothing_named(self, two_bots, capture) -> None:
        """Все источники отказали → пустой «Итог» не уходит: reason source_failed."""
        from apps.channels.max import salon_digest as sd

        salon = _salon("dead")
        empty = _data(
            records=None,
            masters_available=None,
            attention=None,
            readiness_problems=None,
            missing=("records", "masters", "attention", "readiness"),
        )
        with patch.object(sd, "gather_digest", return_value=empty):
            result = sd.send_morning_digests.apply(kwargs={"now_utc": _at(9)}).result
        assert result["source_failed"] == 1 and result["sent"] == 0
        assert capture == []  # empty-assert-ok: source_failed == 1 строкой выше
        # И не считается «отправленным сегодня»: следующий beat попробует снова.
        with patch.object(sd, "gather_digest", return_value=_data()):
            again = sd.send_morning_digests.apply(kwargs={"now_utc": _at(9, 55)}).result
        assert again["sent"] == 1 and capture[0]["user_id"] == f"owner-{salon.slug}"

    def test_gather_is_the_greeting_gather(self) -> None:
        """Сводку собирает ``salon_greeting.gather`` — не копия в дайджесте."""
        from apps.channels.max import salon_digest as sd, salon_greeting as sg

        tenant = SimpleNamespace(pk=uuid.uuid4(), slug="x", timezone="Europe/Moscow")
        with patch.object(sg, "gather", return_value=_data(records=2)) as gather:
            data = sd.gather_digest(tenant)
        assert gather.call_count == 1 and data.records == 2


# ── p4 — задача, beat, выключатель ───────────────────────────────────


class TestP4TaskAndBeat:
    def test_beat_entry_exists(self, settings) -> None:
        entry = settings.CELERY_BEAT_SCHEDULE.get("salon_notify.send_morning_digests")
        assert entry is not None
        assert entry["task"] == "salon_notify.send_morning_digests"

    def test_disabled_by_default_is_a_named_noop(self, settings) -> None:
        from apps.channels.max import salon_digest as sd

        assert getattr(settings, "SALON_MORNING_DIGEST_ENABLED", None) is False
        result = sd.send_morning_digests.apply(kwargs={"now_utc": _at(9)}).result
        assert result == {"disabled": 1}
