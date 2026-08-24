"""A booking the person continues by TYPING (DRF-968, DRF-1101).

Both tickets are the same missing wire, measured on this branch against dev
``647d70c``: only a ``cb:book:*`` TAP was ever routed back into tenant T's
booking pipeline, so any typed turn fell through to the concierge — which has
no booking tool and answers with the master list. On the wire that reads as
the funnel starting over.

The measurement that named DRF-1101's cause is
:class:`TestTheMeasuredDefect`: the 14.08 dialogue replayed turn for turn.
Before the fix both date turns reached ``orchestrate_turn`` and the booking
skill saw nothing after the first tap.

Everything here is end to end through ``GlobalMaxHandler``, because the defect
lives in the routing ladder and not in any one function. The booking skill and
the concierge are mocked at their entrypoints: what is under test is WHICH of
them the turn reaches.
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.channels.handlers import GlobalMaxHandler
from apps.channels.max import handler as max_handler
from apps.conversations.services import resolve_active_global_conversation
from apps.identity.services import resolve_or_create_global_bot_user
from apps.orchestrator.booking_context import (
    AWAITING_SCHEDULE,
    AWAITING_SERVICE,
    STATE_KEY,
    BookingContext,
    load_booking_context,
    save_booking_context,
)
from apps.orchestrator.memory import short_term
from apps.orchestrator.time_preference import load_time_preference, parse_explicit_date
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

_USER_ID = 900
_CHAT_ID = 901

_LYMPH = uuid.UUID("aaaaaaaa-0000-0000-0000-00000000000a")
_CLASSIC = uuid.UUID("aaaaaaaa-0000-0000-0000-00000000000b")
_CLASSIC_BACK = uuid.UUID("aaaaaaaa-0000-0000-0000-00000000000c")


def _callback(payload: str) -> dict:
    return {
        "update_type": "message_callback",
        "timestamp": 1731320000000,
        "callback": {
            "callback_id": f"cb-{payload[-8:]}",
            "payload": payload,
            "user": {"user_id": _USER_ID, "name": "Иван"},
        },
        "message": {
            "recipient": {"chat_id": _CHAT_ID, "chat_type": "dialog"},
            "body": {"mid": f"m-{payload[-8:]}", "seq": 1, "text": ""},
        },
    }


def _typed(text: str, mid: str) -> dict:
    return {
        "update_type": "message_created",
        "timestamp": 1731320000000,
        "message": {
            "sender": {"user_id": _USER_ID, "name": "Иван"},
            "recipient": {"chat_id": _CHAT_ID, "chat_type": "dialog"},
            "body": {"mid": mid, "seq": 2, "text": text},
        },
    }


def _raw(payload: dict) -> dict:
    return {"data": json.dumps(payload), "trace_id": str(uuid.uuid4()), "resolved_tenant_id": ""}


@pytest.fixture
def mock_send(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(
        max_handler,
        "send_message",
        lambda *, chat_id, text, attachments=None, timeout=10.0: calls.append(
            {"text": text, "attachments": attachments}
        ),
    )
    return calls


@pytest.fixture
def fake_redis(monkeypatch):
    from apps.orchestrator.memory.tests.test_short_term import _FakeRedis

    fake = _FakeRedis()
    monkeypatch.setattr(short_term, "_redis_client", lambda: fake)
    return fake


class _Wired:
    """The two entrypoints a turn can land on, and what each one saw."""

    def __init__(self) -> None:
        self.booking: list[str] = []
        self.concierge: list[str] = []


@pytest.fixture
def wired(monkeypatch, settings, mock_send, fake_redis):
    settings.STRICT_TENANT_SCOPE = "strict"
    settings.BOOKING_VIA_AYLA_REST = True
    settings.GLOBAL_BOT_ONBOARDING = False

    seen = _Wired()

    from apps.orchestrator.turn_seam import TurnReply
    from apps.skills.base import SkillResult

    def fake_dispatch(ctx):
        seen.booking.append(ctx.message_text)
        return SkillResult(reply_text="Выберите дату:")

    def fake_turn(turn_ctx):
        seen.concierge.append(turn_ctx.text)
        return TurnReply(
            reply_text="Вот мастера, которые могут подойти:",
            action_data=None,
            assistant_persisted=False,
        )

    monkeypatch.setattr("apps.skills.registry.dispatch", fake_dispatch)
    monkeypatch.setattr(max_handler, "orchestrate_turn", fake_turn)
    return seen


@pytest.fixture
def salon():
    """Сазонова Инна as the pilot mirror really holds her, in miniature.

    Three services, and the pair that makes «классический массаж» ambiguous is
    the pair from the live catalog (DRF-970). The lymphatic one is the service
    the DRF-1324 dialogue was about.
    """
    stamp = datetime(2026, 5, 18, tzinfo=timezone.utc)
    tenant = Tenant.objects.create(
        slug="formula-tela", name="Формула тела", timezone="Europe/Moscow", city="Пенза"
    )
    rows: dict[str, CatalogService] = {}
    master = CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_id=1,
        external_updated_at=stamp,
        name="Сазонова Инна",
        specialization="массаж",
        yclients_staff_id=42,
    )
    for slug, name, ayla_id in (
        ("lymph", "Лимфодренажный массаж всего тела", _LYMPH),
        ("classic", "Классический массаж", _CLASSIC),
        ("classic-back", "Классический массаж задней поверхности тела", _CLASSIC_BACK),
    ):
        service = CatalogService.all_tenants.create(
            tenant=tenant,
            slug=slug,
            name=name,
            is_active=True,
            ayla_service_id=ayla_id,
            external_updated_at=stamp,
        )
        MasterService.all_tenants.create(tenant=tenant, master=master, service=service)
        rows[slug] = service
    return tenant, master, rows


def _global_conversation():
    bot_user = resolve_or_create_global_bot_user(
        channel="max", channel_user_id=str(_USER_ID), chat_id=str(_CHAT_ID)
    )
    return resolve_active_global_conversation(bot_user, create_if_missing=False)


def _wire_buttons(sent: dict) -> list[dict]:
    """Every button in an outbound MAX message, flattened out of its rows."""
    payload = (sent["attachments"] or [{}])[0].get("payload") or {}
    return [button for row in payload.get("buttons") or [] for button in row]


def _tap_serviceless(tenant, master) -> None:
    """The tap that leaves the bot asking «какая услуга?» — DRF-968's start."""
    GlobalMaxHandler()(_raw(_callback(f"cb:discover:book:{tenant.id}:{master.id}")))


def _tap_with_service(tenant, master, service_pk) -> None:
    GlobalMaxHandler()(_raw(_callback(f"cb:discover:book:{tenant.id}:{master.id}:{service_pk}")))


# ---------------------------------------------------------------------------
# DRF-1101 — the dialogue that named the ticket
# ---------------------------------------------------------------------------


class TestTheMeasuredDefect:
    """The 14.08 sequence, replayed.

    ```
    09:03:30 user       16.08.2016            ← typo in the year
    09:03:33 assistant  Дата … уже прошла
    09:03:49 user       16 августа 2026       ← correct date
    09:03:51 assistant  <карточка мастеров>   ← СБРОС В НАЧАЛО
    ```

    The ticket suspected the failed attempt before it. The measurement says
    otherwise: nothing about the typo matters and nothing about the date
    matters. Both turns were typed, and a typed turn never reached the
    booking flow at all.
    """

    def test_a_typed_date_reaches_the_booking_flow_not_the_concierge(
        self, wired, salon, mock_send
    ) -> None:
        tenant, master, catalog = salon
        _tap_with_service(tenant, master, catalog["lymph"].id)
        assert wired.booking == [f"cb:book:pick_master:{master.id}:{_LYMPH}"]

        GlobalMaxHandler()(_raw(_typed("16 августа 2026", "mm-correct")))

        # The turn re-entered the picker for the SAME master and service —
        # not a new discovery. Before this fix the concierge got it.
        assert wired.concierge == []
        assert wired.booking[-1] == f"cb:book:pick_master:{master.id}:{_LYMPH}"
        assert "мастера, которые могут подойти" not in mock_send[-1]["text"]

    def test_the_typed_date_becomes_the_request_the_picker_honours(self, wired, salon) -> None:
        """DRF-1325's picker already honours a stored preference; this turns
        the typed date into one instead of inventing a second mechanism."""
        tenant, master, catalog = salon
        _tap_with_service(tenant, master, catalog["lymph"].id)

        today = date.today()
        wanted = today + timedelta(days=9)
        GlobalMaxHandler()(_raw(_typed(wanted.strftime("%d.%m.%Y"), "mm-date")))

        stored = load_time_preference(_global_conversation())
        assert stored is not None
        assert stored.day_offset == 9

    def test_the_year_typo_is_answered_and_the_funnel_stays_open(
        self, wired, salon, mock_send
    ) -> None:
        """«16.08.2016» — the turn the dialogue opens with. It must be named
        as a past date AND leave the person inside the booking, because the
        reset happened on the turn AFTER it."""
        tenant, master, catalog = salon
        _tap_with_service(tenant, master, catalog["lymph"].id)

        GlobalMaxHandler()(_raw(_typed("16.08.2016", "mm-typo")))

        assert mock_send[-1]["text"].startswith("Дата 16.08.2016 уже прошла.")
        assert wired.concierge == []
        assert wired.booking[-1] == f"cb:book:pick_master:{master.id}:{_LYMPH}"
        # …and no past date was smuggled into the picker as a preference.
        assert load_time_preference(_global_conversation()) is None

    def test_the_whole_14_08_sequence_no_longer_resets(self, wired, salon, mock_send) -> None:
        tenant, master, catalog = salon
        _tap_with_service(tenant, master, catalog["lymph"].id)
        GlobalMaxHandler()(_raw(_typed("16.08.2016", "mm-1")))
        GlobalMaxHandler()(_raw(_typed("16 августа 2026", "mm-2")))

        # Three replies, and not one of them is the master list.
        assert wired.concierge == []
        assert all("мастера, которые могут подойти" not in call["text"] for call in mock_send)


class TestATypedTurnThatIsNotAboutTime:
    def test_it_still_belongs_to_the_concierge(self, wired, salon) -> None:
        """A live booking is not a licence to swallow the next turn. «А
        сколько это стоит?» must keep today's routing exactly."""
        tenant, master, catalog = salon
        _tap_with_service(tenant, master, catalog["lymph"].id)

        GlobalMaxHandler()(_raw(_typed("а сколько это стоит?", "mm-price")))

        assert wired.concierge == ["а сколько это стоит?"]
        assert wired.booking == [f"cb:book:pick_master:{master.id}:{_LYMPH}"]

    def test_a_stale_context_is_not_continued(self, wired, salon) -> None:
        """Past the TTL the funnel is over: «завтра» means a new request."""
        tenant, master, catalog = salon
        _tap_with_service(tenant, master, catalog["lymph"].id)

        conversation = _global_conversation()
        state = dict(conversation.skill_state or {})
        stamped = datetime.now(timezone.utc) - timedelta(hours=3)
        state[STATE_KEY] = {**state[STATE_KEY], "at": stamped.isoformat()}
        conversation.skill_state = state
        conversation.save(update_fields=["skill_state"])

        GlobalMaxHandler()(_raw(_typed("давай завтра", "mm-stale")))

        assert wired.concierge == ["давай завтра"]


# ---------------------------------------------------------------------------
# DRF-968 — the answer to «напишите название услуги»
# ---------------------------------------------------------------------------


class TestTheServiceQuestionIsAnswerable:
    def test_the_typed_name_enters_booking_instead_of_redrawing_the_list(
        self, wired, salon, mock_send
    ) -> None:
        """The ticket's own loop:

        ```
        → [тап карточки мастера]
        ← Выберите услугу мастера Сазонова Инна: …
        → Лимфодренажный массаж всего тела
        ← Вот мастера, которые могут подойти: …   ← петля
        ```
        """
        tenant, master, catalog = salon
        _tap_serviceless(tenant, master)
        assert wired.booking == []  # the tap itself only asked

        GlobalMaxHandler()(_raw(_typed("Лимфодренажный массаж всего тела", "mm-svc")))

        assert wired.concierge == []
        assert wired.booking == [f"cb:book:pick_master:{master.id}:{_LYMPH}"]
        assert mock_send[-1]["text"] == "Выберите дату:"

    def test_two_matches_offer_a_choice_rather_than_a_dead_end(
        self, wired, salon, mock_send
    ) -> None:
        """DRF-970's exact pair. «Классический массаж» names two real
        services, so picking one silently is forbidden — but so is stopping.
        """
        tenant, master, catalog = salon
        _tap_serviceless(tenant, master)

        GlobalMaxHandler()(_raw(_typed("классический массаж", "mm-ambig")))

        assert wired.concierge == []
        assert wired.booking == []
        # Read at the OUTBOUND boundary, the way DRF-1070 pins its own
        # keyboard: an envelope `_build_attachments` fails to recognise is
        # dropped silently, and the chat message would then be
        # indistinguishable from the text-only dead end this fixes.
        buttons = _wire_buttons(mock_send[-1])
        assert {button["text"] for button in buttons} == {
            "Классический массаж",
            "Классический массаж задней поверхности тела",
        }
        # Every button carries a service id, so the next tap cannot come back
        # here (the DRF-1070 rule, kept).
        for button in buttons:
            assert button["payload"].startswith(f"cb:discover:book:{tenant.id}:{master.id}:")
            assert button["payload"].rsplit(":", 1)[-1]

    def test_the_choice_is_still_answerable_by_typing(self, wired, salon) -> None:
        """The narrowed question re-arms the pending state under the NEW
        words — otherwise the second answer would loop where the first
        did."""
        tenant, master, catalog = salon
        _tap_serviceless(tenant, master)
        GlobalMaxHandler()(_raw(_typed("классический массаж", "mm-a1")))

        GlobalMaxHandler()(_raw(_typed("Классический массаж задней поверхности тела", "mm-a2")))

        assert wired.booking == [f"cb:book:pick_master:{master.id}:{_CLASSIC_BACK}"]

    def test_a_service_this_master_does_not_offer_goes_to_the_concierge(self, wired, salon) -> None:
        """The live «Кавитация» turn (DRF-962 acceptance, 09.08). Nobody here
        performs it, and the concierge can answer it properly — with the
        masters who do."""
        tenant, master, catalog = salon
        _tap_serviceless(tenant, master)

        GlobalMaxHandler()(_raw(_typed("Кавитация", "mm-cav")))

        assert wired.concierge == ["Кавитация"]

    def test_a_question_is_not_read_as_an_answer(self, wired, salon) -> None:
        """«сколько», «стоит» name no service of this master, so the turn is
        given back — the same residue rule the deterministic fast path uses
        before it claims a turn (DRF-1328)."""
        tenant, master, catalog = salon
        _tap_serviceless(tenant, master)

        GlobalMaxHandler()(_raw(_typed("а сколько это стоит?", "mm-q")))

        assert wired.concierge == ["а сколько это стоит?"]
        assert wired.booking == []


class TestTheContextItself:
    def test_the_ask_leaves_a_service_question_parked(self, wired, salon) -> None:
        tenant, master, catalog = salon
        _tap_serviceless(tenant, master)

        ctx = load_booking_context(_global_conversation())
        assert ctx is not None
        assert ctx.awaiting == AWAITING_SERVICE
        assert ctx.master_id == str(master.id)
        assert ctx.master_name == "Сазонова Инна"

    def test_a_dispatch_leaves_a_schedule_question_parked(self, wired, salon) -> None:
        tenant, master, catalog = salon
        _tap_with_service(tenant, master, catalog["lymph"].id)

        ctx = load_booking_context(_global_conversation())
        assert ctx is not None
        assert ctx.awaiting == AWAITING_SCHEDULE
        assert ctx.native_service_id == str(_LYMPH)

    def test_a_confirm_tap_ends_the_funnel(self, wired, salon) -> None:
        """Past confirm / cancel a typed date is a NEW request, and
        continuing the finished booking would be the stale-context dead-end
        wearing this fix's clothes."""
        tenant, master, catalog = salon
        _tap_with_service(tenant, master, catalog["lymph"].id)

        GlobalMaxHandler()(_raw(_callback(f"cb:book:confirm:{uuid.uuid4()}")))

        assert load_booking_context(_global_conversation()) is None

    def test_a_corrupt_row_is_not_acted_on(self, wired, salon) -> None:
        """``skill_state`` is a shared JSON column. A continuation ACTS on
        what it reads, so an unrecognised ``awaiting`` must read as nothing.
        """
        tenant, master, catalog = salon
        _tap_with_service(tenant, master, catalog["lymph"].id)
        conversation = _global_conversation()
        save_booking_context(
            conversation,
            BookingContext(
                awaiting=AWAITING_SCHEDULE, tenant_id=str(tenant.id), master_id=str(master.id)
            ),
        )
        state = dict(conversation.skill_state or {})
        state[STATE_KEY] = {**state[STATE_KEY], "awaiting": "whatever"}
        conversation.skill_state = state
        conversation.save(update_fields=["skill_state"])

        assert load_booking_context(_global_conversation()) is None


# ---------------------------------------------------------------------------
# The date dialect itself
# ---------------------------------------------------------------------------


class TestExplicitDates:
    TODAY = date(2026, 8, 24)

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("16 августа 2026", date(2026, 8, 16)),
            ("16.08.2026", date(2026, 8, 16)),
            ("16.08.26", date(2026, 8, 16)),
            ("16.08.2016", date(2016, 8, 16)),
            ("2026-09-01", date(2026, 9, 1)),
            ("давай 30 августа", date(2026, 8, 30)),
            # No year, and the day has passed this year → the next one.
            ("16 августа", date(2027, 8, 16)),
            ("1 сентября", date(2026, 9, 1)),
        ],
    )
    def test_it_reads_what_a_person_writes(self, text: str, expected: date) -> None:
        assert parse_explicit_date(text, today=self.TODAY) == expected

    @pytest.mark.parametrize(
        "text",
        [
            "",
            "завтра вечером",
            "хочу массаж",
            "17.30",  # a time written with a dot, not month 30
            "31.02.2026",  # not a date
            "Лимфодренажный массаж 60 минут",
        ],
    )
    def test_it_refuses_everything_else(self, text: str) -> None:
        assert parse_explicit_date(text, today=self.TODAY) is None
