"""DRF-1325 — the global bot hears the time and says so.

The pilot dialogue this guards (2026-08-23 17:48:36): «хочу на массаж ЗАВТРА
ВЕЧЕРОМ» → a list of masters with not a word about time. Two things had to
start happening on that turn, and they are tested separately because they
fail separately:

* the preference is **stored**, so the booking flow two taps later can honour
  it (that half is exercised end to end in
  ``apps/skills/booking/tests/test_time_chips.py``);
* the preference is **acknowledged**, so a person can see their request
  landed instead of guessing.

The acknowledgement is deliberately NOT universal, and the boundary is worth
pinning: a concierge turn is written to the Message table by its own store
before control returns to the handler, so prefixing its text there would send
one thing and record another — and the replay fixtures read the record.
"""

from __future__ import annotations

import pytest

from apps.channels.max.handler import _offers_booking, _remember_time_preference
from apps.orchestrator.discovery import CALLBACK_DISCOVER_BOOK_PREFIX, DiscoveryReply
from apps.orchestrator.time_preference import PART_EVENING, load_time_preference


class _Conversation:
    def __init__(self) -> None:
        self.skill_state: dict = {}

    def save(self, update_fields=None):  # noqa: ANN001, ANN202
        return None


class _BotUser:
    tenant = None


def _cards_reply(text: str = "Вот мастера, которые могут подойти:") -> DiscoveryReply:
    return DiscoveryReply(
        text=text,
        action_data={
            "attachments": [
                {
                    "type": "inline_keyboard",
                    "payload": {
                        "buttons": [
                            {
                                "label": "Записаться к Сазонова Инна",
                                "callback": f"{CALLBACK_DISCOVER_BOOK_PREFIX}t-1:m-1:s-1",
                            }
                        ]
                    },
                }
            ]
        },
    )


pytestmark = pytest.mark.django_db


class TestAcknowledgement:
    def test_the_live_turn_no_longer_drops_the_time(self) -> None:
        conv = _Conversation()
        reply = _remember_time_preference(
            conv, _BotUser(), "хочу на массаж ЗАВТРА ВЕЧЕРОМ", _cards_reply()
        )
        assert reply.text.startswith("Поняла: завтра вечером.")
        # The master cards themselves are untouched — the acknowledgement is
        # a line above them, not a replacement for them.
        assert "Вот мастера" in reply.text
        assert reply.action_data == _cards_reply().action_data

    def test_the_preference_is_stored_for_the_taps_that_follow(self) -> None:
        conv = _Conversation()
        _remember_time_preference(
            conv, _BotUser(), "запиши на массаж завтра вечером", _cards_reply()
        )
        stored = load_time_preference(conv)
        assert stored is not None
        assert stored.day_offset == 1 and stored.part == PART_EVENING

    def test_a_turn_without_a_time_is_byte_identical(self) -> None:
        """The happy path must not move. «запиши на массаж» is the ticket's
        own second scenario and it has to reach the day chips unchanged."""
        conv = _Conversation()
        original = _cards_reply()
        reply = _remember_time_preference(conv, _BotUser(), "запиши на массаж", original)
        assert reply is original
        assert conv.skill_state == {}

    def test_stored_but_not_announced_when_the_reply_offers_no_booking(self) -> None:
        """A price answer that happens to contain «завтра» must not sprout a
        line about picking times — but the preference is still worth keeping
        for the moment the person does start booking."""
        conv = _Conversation()
        plain = DiscoveryReply(text="Классический массаж стоит 3000 ₽.")
        reply = _remember_time_preference(conv, _BotUser(), "а завтра вечером сколько?", plain)
        assert reply is plain
        assert load_time_preference(conv) is not None

    def test_an_already_persisted_reply_is_never_rewritten(self) -> None:
        """Sending one thing and recording another is worse than a missing
        line: the Message table is what replay reads back."""
        conv = _Conversation()
        cards = _cards_reply()
        persisted = DiscoveryReply(text=cards.text, action_data=cards.action_data, persisted=True)
        reply = _remember_time_preference(conv, _BotUser(), "завтра вечером", persisted)
        assert reply is persisted
        assert reply.text == _cards_reply().text
        # Stored all the same — the picker reads it back one tap later.
        assert load_time_preference(conv) is not None

    def test_a_broken_conversation_costs_the_hint_not_the_turn(self) -> None:
        class _Exploding:
            @property
            def skill_state(self):  # noqa: ANN202
                raise RuntimeError("db down")

        original = _cards_reply()
        reply = _remember_time_preference(_Exploding(), _BotUser(), "завтра вечером", original)
        assert reply.text.startswith("Поняла: завтра вечером.")


class TestOffersBooking:
    """Which replies may carry the acknowledgement."""

    def test_true_for_a_master_card_keyboard(self) -> None:
        assert _offers_booking(_cards_reply()) is True

    @pytest.mark.parametrize(
        "reply",
        [
            DiscoveryReply(text="просто текст"),
            DiscoveryReply(text="x", action_data={}),
            DiscoveryReply(text="x", action_data={"attachments": []}),
            DiscoveryReply(
                text="x",
                action_data={
                    "attachments": [
                        {
                            "type": "inline_keyboard",
                            "payload": {
                                "buttons": [{"label": "Услуги", "callback": "cb:catalog:services"}]
                            },
                        }
                    ]
                },
            ),
        ],
    )
    def test_false_for_everything_else(self, reply: DiscoveryReply) -> None:
        assert _offers_booking(reply) is False

    def test_malformed_action_data_is_not_an_exception(self) -> None:
        assert (
            _offers_booking(DiscoveryReply(text="x", action_data={"attachments": [None]})) is False
        )
        assert _offers_booking(DiscoveryReply(text="x", action_data={"attachments": [{}]})) is False
