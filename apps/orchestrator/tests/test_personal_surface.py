"""DRF-1302 + DRF-1305 — the person's own diary and the memory list.

Before this, the only «дневник» in the codebase was the button that PUTS a
dish into it; nothing read it back, on any surface, by any route. And on the
global path «что ты про меня помнишь» — the exact phrase in the DRF-1305
acceptance criterion — missed ``memory_commands._SHOW_TRIGGERS`` by one
wording and fell through to whatever the model felt like saying.

Three things are pinned here, in order of how much they would cost to get
wrong:

``TestEveryNumberTraces`` — the DRF-1295 boundary. Every digit the diary
prints must be a value the person logged, a norm from the profile they
filled in, or arithmetic over those two. Copied deliberately from
``apps/nutrition_proactive/tests/test_render.py`` (DRF-1285), because this
surface reuses that renderer and must inherit its guarantee rather than
restate it.

``TestChipsExecute`` — the owner's rule that a chip must lead to something
that runs. Each test takes the callback string of a chip we really ship and
asserts the matcher on the other end really claims it. A chip is a promise
made by this code and kept by somebody else's; nothing but a test spans that
gap.

``TestHonestEmptiness`` — an empty record is said, never filled in. The
failure this prevents is the one that would be hardest to notice in
production and worst to ship: a warm, plausible, invented answer about what
a person ate.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from apps.identity.models import MemoryEntry, UserPersonalContext
from apps.integrations.ayla import (
    DeficitsResponse,
    NutritionUnavailableError,
    ProfileResponse,
    SummaryResponse,
    WaterTodayResponse,
)
from apps.llm.protocol import CompletionResult, ToolCall
from apps.orchestrator import concierge, personal_surface
from apps.orchestrator.concierge import _dispatch_tool, generate_concierge_reply
from apps.orchestrator.personal_surface import (
    CHIP_ANKETA,
    CHIP_DIARY,
    CHIP_WATER,
    CONSENT_CLOSED_TEXT,
    DIARY_UNAVAILABLE_TEXT,
    PERSONAL_TOOL_ACTIONS,
    SHOW_MY_RECORDS_TOOL_SPEC,
    execute_personal_tool,
    looks_like_diary_request,
    render_diary,
    render_memory,
)

pytestmark = pytest.mark.django_db(transaction=True)


# ─── fixtures ──────────────────────────────────────────────────────────────


def _bot_user(prefix: str, *, consent: bool = True, linked: bool = True):
    from apps.consent.services import record_global_consent
    from apps.identity.services import resolve_or_create_global_bot_user

    bot_user = resolve_or_create_global_bot_user(
        channel="max", channel_user_id=f"{prefix}-uid", chat_id=f"{prefix}-chat"
    )
    if consent:
        record_global_consent(bot_user, source="welcome")
    if linked:
        bot_user.ayla_user_id = uuid.uuid4()
        bot_user.save(update_fields=["ayla_user_id"])
    return bot_user


def _conversation(bot_user):
    from apps.conversations.services import resolve_active_global_conversation

    return resolve_active_global_conversation(bot_user)


def _summary(**over) -> SummaryResponse:
    payload = dict(
        date="2026-08-23",
        calories_total=1210.0,
        calories_goal=1994,
        protein_g=61.0,
        fat_g=44.0,
        carbs_g=130.0,
        entries=[{"id": "e1"}],
        raw={},
    )
    payload.update(over)
    return SummaryResponse(**payload)


def _water(**over) -> WaterTodayResponse:
    payload = dict(total_ml=900, norm_ml=2400, entries=[{"id": "w1"}], raw={})
    payload.update(over)
    return WaterTodayResponse(**payload)


def _profile(**over) -> ProfileResponse:
    payload = dict(
        gender="female",
        age=31,
        height_cm=168,
        weight_kg=62,
        goal="lose",
        daily_kcal=1994,
        protein_g=128,
        fat_g=66,
        carbs_g=221,
        water_ml=2400,
        bmr=1400,
        health_flags={},
        disclaimer_acked=None,
        raw={},
    )
    payload.update(over)
    return ProfileResponse(**payload)


class _FakeAyla:
    """A hand-written stand-in, not a bare ``Mock()``.

    A ``Mock`` answers every method with a truthy Mock, so a renderer reading
    a field that does not exist would still «work» and print a Mock repr. The
    only failures worth catching here are exactly that shape, so the double
    has to have the real surface and nothing more.
    """

    def __init__(self, *, summary=None, water=None, profile=None, deficits=None, raises=None):
        self._summary = summary
        self._water = water
        self._profile = profile
        self._deficits = deficits
        self._raises = raises
        self.calls: list[str] = []

    async def _answer(self, name, value):
        self.calls.append(name)
        if self._raises is not None:
            raise self._raises
        return value

    async def daily_summary(self, *, external_user_id, **kw):
        return await self._answer("daily_summary", self._summary)

    async def get_water_today(self, *, external_user_id, **kw):
        return await self._answer("get_water_today", self._water)

    async def get_profile(self, *, external_user_id, **kw):
        return await self._answer("get_profile", self._profile)

    async def weekly_deficits(self, *, external_user_id, **kw):
        return await self._answer("weekly_deficits", self._deficits)


def _install_ayla(monkeypatch, fake: _FakeAyla) -> _FakeAyla:
    import apps.integrations.ayla as ayla

    monkeypatch.setattr(ayla, "get_nutrition_client", lambda: fake)
    return fake


def _chips(reply) -> list[dict[str, str]]:
    """The flat ``action_data["buttons"]`` shape this surface emits — the same
    shape ``_build_attachments`` reads for a ``SkillResult`` and a
    ``DiscoveryReply`` alike, which is why the surface uses one for both."""
    return list((reply.action_data or {}).get("buttons") or [])


def _callbacks(reply) -> list[str]:
    return [chip["callback"] for chip in _chips(reply)]


def _green_fact(user_id, *, key="diet", value="vegan", kind="lifestyle"):
    upc, _ = UserPersonalContext.objects.get_or_create(user_id=user_id)
    return MemoryEntry.objects.create(
        user_id=user_id,
        personal_context=upc,
        sensitivity_zone=MemoryEntry.SENSITIVITY_GREEN,
        source=MemoryEntry.SOURCE_EXPLICIT,
        provenance=MemoryEntry.PROVENANCE_USER_STATED,
        kind=kind,
        content={"key": key, "value": value},
    )


# ─── the boundary ──────────────────────────────────────────────────────────


class TestEveryNumberTraces:
    def test_no_digit_appears_that_the_person_did_not_produce(self, monkeypatch):
        """The DRF-1285 allow-list, applied to the pull surface.

        Extract every integer from the rendered diary and require the set to
        be a subset of the numbers that came IN. Anything else is a number the
        bot made up about a person's food.
        """
        import re

        summary, water, profile = _summary(), _water(), _profile()
        _install_ayla(monkeypatch, _FakeAyla(summary=summary, water=water, profile=profile))
        bot_user = _bot_user("trace-1")

        reply = render_diary(bot_user)

        printed = {int(n) for n in re.findall(r"\d+", reply.text)}
        allowed = {
            round(summary.calories_total),
            summary.calories_goal,
            round(summary.protein_g),
            profile.protein_g,
            round(summary.fat_g),
            profile.fat_g,
            round(summary.carbs_g),
            profile.carbs_g,
            water.total_ml,
            water.norm_ml,
            round(profile.protein_g - summary.protein_g),  # the shortfall remark
        }
        assert printed <= allowed, f"unexplained numbers: {printed - allowed}"

    def test_the_week_prints_only_fields_of_the_aggregate(self, monkeypatch):
        import re

        deficits = DeficitsResponse(
            days_observed=5,
            protein_avg_pct_goal=72.0,
            protein_low_streak_days=3,
            hint="Ayla free-form text 12345 that must not be printed",
            fired_keys=[],
            raw={},
        )
        _install_ayla(monkeypatch, _FakeAyla(profile=_profile(), deficits=deficits))
        bot_user = _bot_user("trace-2")

        reply = render_diary(bot_user, period="week")

        printed = {int(n) for n in re.findall(r"\d+", reply.text)}
        assert printed <= {5, 72, 3}, f"unexplained numbers: {printed}"

    def test_ayla_free_form_hint_never_reaches_the_person(self, monkeypatch):
        """The deficits ``hint`` is written as a signal for a MODEL, not as
        reviewed user copy. Printing it would put an unbounded upstream
        sentence on a screen where every sentence must be accountable."""
        deficits = DeficitsResponse(
            days_observed=4,
            protein_avg_pct_goal=None,
            protein_low_streak_days=0,
            hint="Похоже, у вас дефицит белка — стоит принимать добавки",
            fired_keys=[],
            raw={},
        )
        _install_ayla(monkeypatch, _FakeAyla(profile=_profile(), deficits=deficits))

        reply = render_diary(_bot_user("trace-3"), period="week")

        assert "добавк" not in reply.text.lower()
        assert "дефицит" not in reply.text.lower()

    def test_a_missing_profile_omits_targets_instead_of_inventing_them(self, monkeypatch):
        """No anketa → no norms. The one thing the reply may never do is fill
        the gap with a default: «из 2000» that nobody chose is a fabricated
        target dressed as the person's own."""
        _install_ayla(monkeypatch, _FakeAyla(summary=_summary(), water=_water(), profile=None))

        reply = render_diary(_bot_user("trace-4"))

        assert " из 128 " not in reply.text
        assert "Норм пока нет" in reply.text


# ─── the chip contract ─────────────────────────────────────────────────────


class TestChipsExecute:
    """Every chip we ship, checked against the matcher that must claim it.

    These are the tests that would have caught «кнопка ведёт в „я вас не
    понял"» — a defect no unit test of the renderer alone can see, because
    the renderer is not the half that breaks.
    """

    def test_the_anketa_chip_starts_the_anketa(self):
        from apps.orchestrator.nutrition_global import is_structured_nutrition_turn

        assert is_structured_nutrition_turn(
            text=CHIP_ANKETA["callback"],
            has_attachments=False,
            conversation=SimpleNamespace(skill_state={}),
        )

    def test_the_water_chip_parses_as_a_real_drink(self):
        from apps.skills.water.parser import BeverageMatch, parse_beverage

        parsed = parse_beverage(CHIP_WATER["callback"])
        assert isinstance(parsed, BeverageMatch)
        assert parsed.ml > 0

    def test_the_diary_chip_is_claimed_by_the_diary_matcher(self):
        assert looks_like_diary_request(CHIP_DIARY["callback"]) == "today"

    def test_a_forget_chip_really_forgets_that_domain(self):
        """The «Забыть: питание» callback is plain text — a tap re-enters the
        turn as if typed. So the test taps it the way a person would."""
        from apps.persona.memory_commands import handle_memory_command, memory_show_chips

        bot_user = _bot_user("chip-forget")
        entry = _green_fact(bot_user.ayla_user_id)

        chips = memory_show_chips(bot_user)
        assert chips, "a remembered fact must offer a way to forget it"

        result = handle_memory_command(
            user_id=bot_user.ayla_user_id, text=chips[0]["callback"], bot_user=bot_user
        )

        assert result is not None
        assert "забыла" in result.text.lower()
        entry.refresh_from_db()
        assert entry.soft_deleted_at is not None

    def test_the_memory_chip_is_claimed_by_the_show_triggers(self):
        from apps.persona.memory_commands import handle_memory_command

        from apps.orchestrator.personal_surface import CHIP_MEMORY

        assert handle_memory_command(user_id=uuid.uuid4(), text=CHIP_MEMORY["callback"]) is not None

    def test_no_forget_chip_when_there_is_nothing_to_forget(self):
        from apps.persona.memory_commands import memory_show_chips

        assert memory_show_chips(_bot_user("chip-empty")) == []

    def test_the_diary_offers_the_anketa_only_when_there_is_no_profile(self, monkeypatch):
        _install_ayla(monkeypatch, _FakeAyla(summary=_summary(), water=_water(), profile=None))
        without = render_diary(_bot_user("chip-noprof"))

        _install_ayla(
            monkeypatch, _FakeAyla(summary=_summary(), water=_water(), profile=_profile())
        )
        with_profile = render_diary(_bot_user("chip-prof"))

        assert _callbacks(without) == [CHIP_ANKETA["callback"]]
        assert _callbacks(with_profile) == [CHIP_WATER["callback"]]

    def test_the_anketa_finale_offers_the_two_steps_that_exist(self, monkeypatch):
        """Post-anketa the bot used to hand over five numbers and go quiet.
        The diary chip is conditional because only the GLOBAL path claims its
        callback — off that path it would be a button answering «я вас не
        понял»."""
        from apps.skills.nutrition_anketa.skill import _post_anketa_chips

        monkeypatch.setattr(personal_surface, "diary_is_reachable", lambda: True)
        assert [c["callback"] for c in _post_anketa_chips()] == [
            CHIP_WATER["callback"],
            CHIP_DIARY["callback"],
        ]

        monkeypatch.setattr(personal_surface, "diary_is_reachable", lambda: False)
        assert [c["callback"] for c in _post_anketa_chips()] == [CHIP_WATER["callback"]]


# ─── honesty ───────────────────────────────────────────────────────────────


class TestHonestEmptiness:
    def test_nothing_logged_is_said_not_filled_in(self, monkeypatch):
        _install_ayla(
            monkeypatch,
            _FakeAyla(
                summary=_summary(
                    calories_total=0.0, protein_g=0.0, fat_g=0.0, carbs_g=0.0, entries=[]
                ),
                water=_water(total_ml=0),
                profile=_profile(),
            ),
        )

        reply = render_diary(_bot_user("empty-1"))

        assert "записей не было" in reply.text

    def test_an_empty_memory_says_so(self):
        reply = render_memory(_bot_user("empty-2"))

        assert "ничего" in reply.text.lower()
        assert _chips(reply) == []

    def test_an_unreachable_ayla_names_the_outage_without_a_number(self, monkeypatch):
        import re

        _install_ayla(monkeypatch, _FakeAyla(raises=NutritionUnavailableError("circuit open")))

        reply = render_diary(_bot_user("outage-1"))

        assert reply.text.startswith(DIARY_UNAVAILABLE_TEXT[:20])
        assert not re.findall(r"\d", reply.text)

    def test_a_pull_carries_no_off_switch_footer(self, monkeypatch):
        """``include_opt_out=False``: offering to stop sending a message
        nobody sent reads as the bot mistaking an answer for an intrusion."""
        _install_ayla(
            monkeypatch, _FakeAyla(summary=_summary(), water=_water(), profile=_profile())
        )

        reply = render_diary(_bot_user("pull-1"))

        assert "не пиши мне" not in reply.text.lower()

    def test_the_push_still_carries_it(self):
        from apps.nutrition_proactive.render import OPT_OUT_HINT, render_daily_report

        assert OPT_OUT_HINT in render_daily_report(_summary(), _water(), _profile())


class TestConsentGate:
    def test_without_personal_data_nothing_is_read(self, monkeypatch):
        fake = _install_ayla(
            monkeypatch, _FakeAyla(summary=_summary(), water=_water(), profile=_profile())
        )
        bot_user = _bot_user("consent-1", consent=False)

        reply = render_diary(bot_user)

        assert reply.text == CONSENT_CLOSED_TEXT
        assert fake.calls == [], "consent closed must mean no call left the process"

    def test_closed_consent_is_not_reported_as_an_empty_diary(self, monkeypatch):
        """«Мне нельзя смотреть» and «там пусто» are different truths and the
        person must be able to tell which one they got."""
        _install_ayla(monkeypatch, _FakeAyla())
        reply = render_diary(_bot_user("consent-2", consent=False))

        assert "записей не было" not in reply.text


# ─── the tool ──────────────────────────────────────────────────────────────


class TestToolWiring:
    def test_action_matches_the_spec(self):
        assert PERSONAL_TOOL_ACTIONS == {"show_my_records"}
        assert SHOW_MY_RECORDS_TOOL_SPEC["name"] == "show_my_records"

    def test_dispatch_tool_maps_the_call_without_doing_the_io(self):
        call = SimpleNamespace(
            function=SimpleNamespace(name="show_my_records", arguments='{"section": "diary"}')
        )

        result = _dispatch_tool(call, None)

        assert result.action_type == "show_my_records"
        assert result.action_data == {"arguments": {"section": "diary"}}

    def test_a_garbled_section_still_answers(self, monkeypatch):
        _install_ayla(
            monkeypatch, _FakeAyla(summary=_summary(), water=_water(), profile=_profile())
        )
        bot_user = _bot_user("tool-garbled")

        reply = execute_personal_tool("show_my_records", {"section": "🙂"}, bot_user=bot_user)

        assert reply is not None and reply.text

    def test_section_all_merges_both_without_duplicate_chips(self, monkeypatch):
        _install_ayla(
            monkeypatch, _FakeAyla(summary=_summary(), water=_water(), profile=_profile())
        )
        bot_user = _bot_user("tool-all")
        _green_fact(bot_user.ayla_user_id)

        reply = execute_personal_tool("show_my_records", {"section": "all"}, bot_user=bot_user)

        assert reply is not None
        callbacks = _callbacks(reply)
        assert len(callbacks) == len(set(callbacks))
        assert CHIP_WATER["callback"] in callbacks
        assert any(cb.startswith("забудь") for cb in callbacks)

    def test_the_tool_reaches_the_model(self, monkeypatch):
        captured: dict = {}

        async def _complete(messages, model: str = "", tools=None, **kw):
            captured["tools"] = tools
            return CompletionResult(text="ok")

        provider = AsyncMock()
        provider.complete.side_effect = _complete
        router = Mock()
        router.get_provider.return_value = provider
        monkeypatch.setattr(concierge, "get_router", lambda: router)
        bot_user = _bot_user("tool-model")

        generate_concierge_reply("привет", bot_user=bot_user, conversation=_conversation(bot_user))

        assert "show_my_records" in {t["name"] for t in captured["tools"]}

    def test_a_model_tool_call_returns_the_real_diary_in_one_pass(self, monkeypatch):
        _install_ayla(
            monkeypatch, _FakeAyla(summary=_summary(), water=_water(), profile=_profile())
        )
        provider = AsyncMock()
        provider.complete.return_value = CompletionResult(
            text="",
            tool_calls=[ToolCall(id="t1", name="show_my_records", arguments={"section": "diary"})],
        )
        router = Mock()
        router.get_provider.return_value = provider
        monkeypatch.setattr(concierge, "get_router", lambda: router)
        bot_user = _bot_user("tool-diary")

        reply = generate_concierge_reply(
            "что я ел сегодня",
            bot_user=bot_user,
            conversation=_conversation(bot_user),
        )

        assert "Калории: 1210 из 1994 ккал." in reply.text
        # One pass: the person's own numbers are not worth a rephrasing round
        # trip, and a rephrasing round trip is a chance to round one of them.
        assert provider.complete.await_count == 1


# ─── the deterministic layer ───────────────────────────────────────────────


class TestDeterministicClaim:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("что я ел сегодня", "today"),
            ("Мой дневник", "today"),
            ("покажи дневник за неделю", "week"),
            ("сколько я выпила воды", "today"),
            ("хочу записаться на массаж", None),
            ("что ты про меня помнишь", None),  # memory's claim, not the diary's
            ("", None),
        ],
    )
    def test_the_matcher_claims_only_what_it_owns(self, text, expected):
        assert looks_like_diary_request(text) == expected

    def test_a_diary_ask_is_answered_without_a_model(self, monkeypatch):
        from apps.orchestrator.nutrition_global import try_handle_structured_nutrition_turn

        _install_ayla(
            monkeypatch, _FakeAyla(summary=_summary(), water=_water(), profile=_profile())
        )
        bot_user = _bot_user("det-1")

        result = try_handle_structured_nutrition_turn(
            text="что я ел сегодня",
            attachments=None,
            bot_user=bot_user,
            conversation=_conversation(bot_user),
            trace_id="t",
        )

        assert result is not None
        assert result.action_type == "nutrition_diary_shown"
        assert "Калории" in result.reply_text
        assert result.action_data["buttons"]

    def test_a_photo_turn_is_never_hijacked_by_the_diary(self, monkeypatch):
        """The scanner owns the bytes. Answering «вот твой день» while dropping
        the photo the person just sent is the worse of the two mistakes."""
        from apps.orchestrator import nutrition_global

        bot_user = _bot_user("det-2")

        result = nutrition_global._try_handle_diary_request(
            text="что я ел сегодня",
            has_attachments=True,
            bot_user=bot_user,
            trace_id="t",
        )

        assert result is None

    def test_the_show_trigger_that_used_to_miss(self):
        """DRF-1305's acceptance phrase. Measured 23.08: «что ты про меня
        помнишь» was not in ``_SHOW_TRIGGERS`` — the list had «что ты про меня
        ЗНАЕШЬ» — so the exact sentence the ticket asks about fell through to
        a generic model answer."""
        from apps.persona.memory_commands import handle_memory_command

        assert (
            handle_memory_command(user_id=uuid.uuid4(), text="что ты про меня помнишь?") is not None
        )

    def test_the_show_command_now_carries_its_chips(self):
        from apps.persona.memory_commands import handle_memory_command

        bot_user = _bot_user("det-3")
        _green_fact(bot_user.ayla_user_id)

        result = handle_memory_command(
            user_id=bot_user.ayla_user_id,
            text="что ты про меня помнишь",
            bot_user=bot_user,
        )

        assert result is not None
        assert result.action_data is not None
        assert result.action_data["buttons"][0]["callback"].startswith("забудь")
