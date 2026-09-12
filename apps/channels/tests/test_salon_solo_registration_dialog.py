"""Регистрация соло-мастера: имя → город → сводка → «Создать мой профиль» (DRF-1793, M1).

Слово владельца 12.09 (PROMPT §12): кабинет не создаётся до явного
подтверждения; имя из MAX — prefill, который можно исправить; город — из
контролируемого списка; черновик — по личности, без ``BotUser``.

Красный до правки (называется в каждом тесте): «Я работаю сам» заводил
кабинет одной кнопкой, имени и города не спрашивал, ``Tenant.city`` не
писался.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.channels.max import salon_handler
from apps.identity.models import BotUser, SoloRegistrationDraft
from apps.identity.services import solo_registration_draft as drafts
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

IDENTITY = "solo-dialog-1"


class _Event:
    def __init__(self, text: str, *, name: str | None = "Ольга"):
        self.text = text
        self.chat_id = "555"
        self.channel = "max"
        self.channel_user_id = IDENTITY
        self.raw = {"message": {"sender": {"name": name}}} if name else {}


@pytest.fixture(autouse=True)
def _cities(settings):
    settings.SOLO_REGISTRATION_CITIES = ["Пенза", "Саранск"]
    settings.SOLO_REGISTRATION_DRAFT_TTL_HOURS = 72


@pytest.fixture
def said(monkeypatch):
    out: list[dict] = []

    def _capture(event, text, attachments=None):
        out.append({"text": text, "attachments": attachments})

    monkeypatch.setattr(salon_handler, "_reply", _capture)
    return out


def _buttons(reply: dict) -> list[dict]:
    if not reply["attachments"]:
        return []
    rows = reply["attachments"][0]["payload"]["buttons"]
    return [button for row in rows for button in row]


def _callbacks(reply: dict) -> list[str]:
    return [b["payload"] for b in _buttons(reply)]


def _labels(reply: dict) -> list[str]:
    return [b["text"] for b in _buttons(reply)]


def _tap(callback: str, *, name: str | None = "Ольга") -> None:
    assert salon_handler._solo_registration_step(_Event(callback, name=name), entry=None) is True


def _say(text: str) -> bool:
    return salon_handler._solo_registration_takes_name(_Event(text))


def _solo_tenants() -> list[Tenant]:
    return list(Tenant.all_objects.filter(slug__startswith="solo-"))


def _city_callback(reply: dict, city: str) -> str:
    for b in _buttons(reply):
        if b["text"] == city:
            return b["payload"]
    raise AssertionError(f"no button for {city!r}: {_labels(reply)}")


class TestNothingIsCreatedBeforeConfirmation:
    def test_the_button_starts_a_draft_not_a_workspace(self, said):
        """Красный до правки: одна кнопка заводила тенант."""

        _tap(salon_handler.SOLO_REGISTER_CALLBACK)

        assert _solo_tenants() == []
        assert BotUser.all_tenants.filter(channel_user_id=IDENTITY).count() == 0
        draft = SoloRegistrationDraft.objects.get(channel="max", channel_user_id=IDENTITY)
        assert draft.step == SoloRegistrationDraft.Step.NAME
        assert draft.display_name == "Ольга"  # prefill из MAX, ещё не подтверждён
        assert "Оставить «Ольга»" in said[0]["text"]
        assert salon_handler.SOLO_NAME_KEEP_CALLBACK in _callbacks(said[0])

    def test_name_and_city_still_create_nothing(self, said):
        _tap(salon_handler.SOLO_REGISTER_CALLBACK)
        _tap(salon_handler.SOLO_NAME_KEEP_CALLBACK)
        city_reply = said[-1]
        assert city_reply["text"] == salon_handler.SOLO_ASK_CITY
        assert _labels(city_reply)[:2] == ["Пенза", "Саранск"]

        _tap(_city_callback(city_reply, "Саранск"))
        summary = said[-1]
        assert "Имя: Ольга" in summary["text"] and "Город: Саранск" in summary["text"]
        assert salon_handler.SOLO_CONFIRM_CALLBACK in _callbacks(summary)

        assert _solo_tenants() == []

    def test_confirmation_creates_the_workspace_with_name_and_city(self, said):
        """Положительная половина: явное подтверждение — и ровно тогда кабинет есть."""

        _tap(salon_handler.SOLO_REGISTER_CALLBACK)
        assert _say("Ольга Петрова") is True  # исправленное имя вместо prefill
        _tap(_city_callback(said[-1], "Пенза"))
        _tap(salon_handler.SOLO_CONFIRM_CALLBACK)

        tenants = _solo_tenants()
        assert len(tenants) == 1
        assert tenants[0].city == "Пенза"
        assert tenants[0].name == "Студия Ольга"
        row = BotUser.all_tenants.get(channel_user_id=IDENTITY)
        assert row.display_name == "Ольга Петрова"
        assert said[-1]["text"] == salon_handler.SOLO_CREATED_PENDING
        # Черновик отработал и снят.
        assert not SoloRegistrationDraft.objects.filter(channel_user_id=IDENTITY).exists()

    def test_cancel_discards_the_draft_and_creates_nothing(self, said):
        _tap(salon_handler.SOLO_REGISTER_CALLBACK)
        _tap(salon_handler.SOLO_NAME_KEEP_CALLBACK)
        _tap(salon_handler.SOLO_CANCEL_CALLBACK)

        assert said[-1]["text"] == salon_handler.SOLO_CANCELLED
        assert not SoloRegistrationDraft.objects.filter(channel_user_id=IDENTITY).exists()
        assert _solo_tenants() == []

    def test_a_second_confirmation_does_not_duplicate(self, said):
        _tap(salon_handler.SOLO_REGISTER_CALLBACK)
        _tap(salon_handler.SOLO_NAME_KEEP_CALLBACK)
        _tap(_city_callback(said[-1], "Пенза"))
        _tap(salon_handler.SOLO_CONFIRM_CALLBACK)
        _tap(salon_handler.SOLO_CONFIRM_CALLBACK)  # черновика уже нет

        assert len(_solo_tenants()) == 1
        assert said[-1]["text"] == salon_handler.SOLO_DRAFT_MISSING


class TestTheNameIsEditableAndValidated:
    def test_prefill_is_kept_only_when_the_person_says_so(self, said):
        _tap(salon_handler.SOLO_REGISTER_CALLBACK)
        assert _say("Мастер Оля") is True
        draft = SoloRegistrationDraft.objects.get(channel_user_id=IDENTITY)
        assert draft.display_name == "Мастер Оля"
        assert draft.step == SoloRegistrationDraft.Step.CITY

    def test_without_a_max_name_the_bot_asks_for_one_and_offers_no_keep_button(self, said):
        _tap(salon_handler.SOLO_REGISTER_CALLBACK, name=None)
        assert said[0]["text"] == salon_handler.SOLO_ASK_NAME
        assert salon_handler.SOLO_NAME_KEEP_CALLBACK not in _callbacks(said[0])
        assert salon_handler.SOLO_CANCEL_CALLBACK in _callbacks(said[0])

    @pytest.mark.parametrize(
        "raw, reason",
        [("", "empty"), ("О", "too_short"), ("x" * 81, "too_long"), ("12345", "no_letters")],
    )
    def test_a_bad_name_is_refused_with_a_named_reason(self, said, raw, reason):
        _tap(salon_handler.SOLO_REGISTER_CALLBACK)
        assert _say(raw) is True
        assert said[-1]["text"] == salon_handler.SOLO_NAME_REJECTED[reason]
        draft = SoloRegistrationDraft.objects.get(channel_user_id=IDENTITY)
        assert draft.step == SoloRegistrationDraft.Step.NAME  # не продвинулись

    def test_edit_name_from_the_summary_goes_back_and_keeps_the_city(self, said):
        _tap(salon_handler.SOLO_REGISTER_CALLBACK)
        _tap(salon_handler.SOLO_NAME_KEEP_CALLBACK)
        _tap(_city_callback(said[-1], "Пенза"))
        _tap(salon_handler.SOLO_NAME_EDIT_CALLBACK)
        assert "Оставить «Ольга»" in said[-1]["text"]
        assert _say("Оля") is True
        summary_city_reply = said[-1]
        assert summary_city_reply["text"] == salon_handler.SOLO_ASK_CITY
        draft = SoloRegistrationDraft.objects.get(channel_user_id=IDENTITY)
        assert draft.display_name == "Оля" and draft.city == "Пенза"

    def test_free_text_without_a_draft_is_not_a_name(self, said):
        """Стража: обычное «привет» незнакомца по-прежнему не имя."""

        assert _say("привет") is False
        assert said == []


class TestTheCityIsAControlledList:
    def test_an_unknown_city_code_is_refused(self, said):
        _tap(salon_handler.SOLO_REGISTER_CALLBACK)
        _tap(salon_handler.SOLO_NAME_KEEP_CALLBACK)
        _tap(f"{salon_handler.SOLO_CITY_CALLBACK_PREFIX}deadbeef")
        assert said[-1]["text"].startswith(salon_handler.SOLO_CITY_UNKNOWN)
        assert _labels(said[-1])[:2] == ["Пенза", "Саранск"]
        assert SoloRegistrationDraft.objects.get(channel_user_id=IDENTITY).city == ""

    def test_an_empty_city_list_fails_closed_with_a_name(self, said, settings, caplog):
        settings.SOLO_REGISTRATION_CITIES = []
        _tap(salon_handler.SOLO_REGISTER_CALLBACK)
        _tap(salon_handler.SOLO_NAME_KEEP_CALLBACK)
        assert said[-1]["text"] == salon_handler.SOLO_NO_CITIES
        assert _solo_tenants() == []

    def test_city_codes_are_stable_across_list_order(self):
        assert drafts.city_code("Пенза") == drafts.city_code("Пенза")
        assert drafts.city_code("Пенза") != drafts.city_code("Саранск")


class TestTheDraftIsDurableButNotEternal:
    def test_an_expired_draft_reads_as_none_and_the_button_says_start_over(self, said):
        _tap(salon_handler.SOLO_REGISTER_CALLBACK)
        SoloRegistrationDraft.objects.filter(channel_user_id=IDENTITY).update(
            expires_at=timezone.now() - timedelta(minutes=1)
        )
        assert drafts.get_draft(channel="max", channel_user_id=IDENTITY) is None
        _tap(salon_handler.SOLO_NAME_KEEP_CALLBACK)
        assert said[-1]["text"] == salon_handler.SOLO_DRAFT_MISSING
        assert salon_handler.SOLO_REGISTER_CALLBACK in _callbacks(said[-1])

    def test_a_returning_owner_is_not_asked_again(self, said):
        """После создания «Я работаю сам» — дверь в кабинет, не новый диалог."""

        _tap(salon_handler.SOLO_REGISTER_CALLBACK)
        _tap(salon_handler.SOLO_NAME_KEEP_CALLBACK)
        _tap(_city_callback(said[-1], "Пенза"))
        _tap(salon_handler.SOLO_CONFIRM_CALLBACK)

        _tap(salon_handler.SOLO_REGISTER_CALLBACK)
        assert said[-1]["text"] == salon_handler.SOLO_ALREADY_REGISTERED
        assert not SoloRegistrationDraft.objects.filter(channel_user_id=IDENTITY).exists()
