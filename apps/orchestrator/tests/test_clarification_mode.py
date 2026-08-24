"""DRF-1362 — the clarification MODE contract and the choose_many screen.

Two things are pinned here, and the second matters more than the first:

1. A clarification now says how it is meant to be answered, and a consumer
   can read that back off the rendered reply. Before this, ``confirm_one``
   and ``free`` were indistinguishable downstream — the same question with
   the same buttons — so nothing could branch on them.

2. **Every clarification that already ships is byte-identical.** The mode is
   additive metadata on a key no channel renderer reads. If adding it changed
   the wire shape of a turn the pilot is already serving, that would be the
   defect, not the feature.
"""

from __future__ import annotations

import pytest

from apps.orchestrator import discovery
from apps.orchestrator.discovery import (
    ASK_CLARIFICATION_TOOL_SPEC,
    CLARIFICATION_MODE_CHOOSE_MANY,
    CLARIFICATION_MODE_CONFIRM_ONE,
    CLARIFICATION_MODE_FREE,
    CLARIFICATION_MODES,
    CLARIFY_NONE_CALLBACK,
    CLARIFY_SUBMIT_PREFIX,
    CLARIFY_TOGGLE_PREFIX,
    apply_clarify_toggle,
    clarification_mode_of,
    normalize_clarification_mode,
    parse_clarify_callback,
    render_multiselect_clarification,
    selected_clarification_options,
)


class TestToolSpecCarriesMode:
    def test_mode_is_an_optional_enum_over_the_three_modes(self):
        props = ASK_CLARIFICATION_TOOL_SPEC["parameters"]["properties"]
        assert props["mode"]["enum"] == list(CLARIFICATION_MODES)
        # Optional: a model that never fills it must keep working.
        assert "mode" not in ASK_CLARIFICATION_TOOL_SPEC["parameters"]["required"]

    def test_option_ceiling_is_not_quietly_raised(self):
        """maxItems 5 sits beside the five-button limit from DRF-1200.

        Raising one without the other is how a keyboard loses its tail
        silently, so both this and the renderer's own cap are pinned.
        """
        options = ASK_CLARIFICATION_TOOL_SPEC["parameters"]["properties"]["options"]
        assert options["maxItems"] == 5
        assert discovery._MAX_CLARIFICATION_OPTIONS == 5


class TestNormalizeMode:
    @pytest.mark.parametrize("mode", CLARIFICATION_MODES)
    def test_an_explicit_valid_mode_is_taken_as_given(self, mode):
        assert normalize_clarification_mode(mode, ["a", "b"]) == mode

    def test_case_and_whitespace_are_tolerated(self):
        assert (
            normalize_clarification_mode("  Choose_Many ", ["a"]) == CLARIFICATION_MODE_CHOOSE_MANY
        )

    @pytest.mark.parametrize("junk", [None, "", "pick_one", 7, [], {"mode": "free"}])
    def test_junk_never_escapes_the_enum(self, junk):
        assert normalize_clarification_mode(junk, ["a"]) in CLARIFICATION_MODES

    def test_options_present_derives_confirm_one(self):
        assert normalize_clarification_mode(None, ["маникюр"]) == CLARIFICATION_MODE_CONFIRM_ONE

    def test_no_options_derives_free(self):
        assert normalize_clarification_mode(None, []) == CLARIFICATION_MODE_FREE

    def test_choose_many_is_never_inferred(self):
        """Multi-select changes what a tap DOES.

        Deriving it from "there is a list" would silently rewrite the meaning
        of every pick-one clarification the pilot already serves, so it has
        to be asked for by name.
        """
        for options in ([], ["a"], ["a", "b", "c", "d", "e"]):
            assert normalize_clarification_mode(None, options) != CLARIFICATION_MODE_CHOOSE_MANY


class TestConsumerReadsTheMode:
    """The third proof from the brief: by metadata, not by eye."""

    def test_confirm_one_and_free_render_the_same_keyboard(self):
        confirm = discovery._render_ask_clarification(
            "Что именно?", ["Маникюр", "Педикюр"], "confirm_one"
        )
        free = discovery._render_ask_clarification("Что именно?", ["Маникюр", "Педикюр"], "free")
        assert confirm.text == free.text
        assert confirm.action_data["attachments"] == free.action_data["attachments"]

    def test_but_the_consumer_still_tells_them_apart(self):
        confirm = discovery._render_ask_clarification(
            "Что именно?", ["Маникюр", "Педикюр"], "confirm_one"
        )
        free = discovery._render_ask_clarification("Что именно?", ["Маникюр", "Педикюр"], "free")
        assert clarification_mode_of(confirm.action_data) == CLARIFICATION_MODE_CONFIRM_ONE
        assert clarification_mode_of(free.action_data) == CLARIFICATION_MODE_FREE

    @pytest.mark.parametrize("junk", [None, {}, {"buttons": []}, {"clarification": "free"}, "nope"])
    def test_reader_returns_none_for_anything_that_is_not_a_tappable_clarification(self, junk):
        assert clarification_mode_of(junk) is None

    def test_reader_rejects_a_mode_outside_the_enum(self):
        assert clarification_mode_of({"clarification": {"mode": "confirm_two"}}) is None


class TestExistingClarificationsAreUnchanged:
    """The fourth proof, and the one worth the most.

    The pilot already serves many clarifications. Adding a mode must not
    rewrite a single byte any of them puts on the wire.
    """

    def test_option_less_clarification_still_carries_no_action_data(self):
        reply = discovery._render_ask_clarification(discovery.NO_CRITERIA_QUESTION, [])
        assert reply.action_data is None
        assert reply.text == discovery.NO_CRITERIA_QUESTION

    def test_canon_prescribed_no_criteria_reply_is_untouched(self):
        assert discovery.render_no_criteria_clarification().action_data is None
        assert discovery.render_no_service_criteria_clarification().action_data is None

    def test_keyboard_is_identical_with_and_without_the_new_argument(self):
        """The pre-DRF-1362 call signature and the new one must agree."""
        before = discovery._render_ask_clarification("Какой город?", ["Москва", "Казань"])
        after = discovery._render_ask_clarification("Какой город?", ["Москва", "Казань"], None)
        assert before.action_data["attachments"] == after.action_data["attachments"]

    def test_button_callback_is_still_the_option_text_itself(self):
        """The "tap == typed answer" contract predates this ticket."""
        reply = discovery._render_ask_clarification("Что?", ["Маникюр", "Стрижка"], "confirm_one")
        buttons = reply.action_data["attachments"][0]["payload"]["buttons"]
        assert [b["callback"] for b in buttons] == ["Маникюр", "Стрижка"]

    def test_mode_metadata_is_invisible_to_the_channel_renderer(self):
        """_build_attachments reads attachments/buttons/button_rows only.

        Pinned against the real function, not a restatement of it: this is
        the claim that the extra key cannot reach MAX.
        """
        from apps.channels.max.handler import _build_attachments

        with_mode = discovery._render_ask_clarification("Что?", ["A", "B"], "free")
        plain = {"attachments": with_mode.action_data["attachments"]}
        assert _build_attachments(with_mode.action_data) == _build_attachments(plain)

    def test_a_long_model_option_is_still_clipped_to_forty_chars(self):
        reply = discovery._render_ask_clarification("Что?", ["я" * 200], "confirm_one")
        button = reply.action_data["attachments"][0]["payload"]["buttons"][0]
        assert len(button["label"]) == 40
        # …but the callback keeps the full text — clipping the answer would
        # be a different bug from clipping the label.
        assert button["callback"] == "я" * 200

    def test_a_sixth_option_is_dropped_not_rendered(self):
        reply = discovery._render_ask_clarification(
            "Что?", [f"o{i}" for i in range(9)], "confirm_one"
        )
        buttons = reply.action_data["attachments"][0]["payload"]["buttons"]
        assert len(buttons) == 5


class TestMultiselectCallbackGrammar:
    def test_toggle_roundtrip(self):
        tap = parse_clarify_callback(f"{CLARIFY_TOGGLE_PREFIX}5:2")
        assert tap is not None
        assert (tap.kind, tap.mask, tap.index) == ("toggle", 5, 2)

    def test_submit_carries_the_accumulated_mask(self):
        tap = parse_clarify_callback(f"{CLARIFY_SUBMIT_PREFIX}9")
        assert tap is not None
        assert (tap.kind, tap.mask) == ("submit", 9)

    def test_none_option_closes_with_nothing_selected(self):
        tap = parse_clarify_callback(CLARIFY_NONE_CALLBACK)
        assert tap is not None
        assert (tap.kind, tap.mask) == ("none", 0)

    @pytest.mark.parametrize(
        "junk",
        [
            "",
            "cb:catalog:services:abc",
            "Маникюр",
            "cb:clarify:tg:5",  # missing index
            "cb:clarify:tg:5:2:9",  # too many parts
            "cb:clarify:tg:x:2",  # non-numeric mask
            "cb:clarify:tg:5:9",  # index past the five-option cap
            "cb:clarify:tg:64:1",  # mask past the five-bit cap
            "cb:clarify:ok:99",
            "cb:clarify:ok:",
            "cb:clarify:whatever",
            None,
            123,
        ],
    )
    def test_anything_malformed_is_not_a_clarify_tap(self, junk):
        """None, never an exception — the caller's prefix ladder keeps going."""
        assert parse_clarify_callback(junk) is None

    def test_payload_contains_no_character_max_rejects(self):
        """Guard 3 (`outbound._button_to_max`): no `=`, `&`, `?` in a payload."""
        reply = render_multiselect_clarification("Что?", ["A", "B", "C"], mask=6)
        for row in reply.action_data["button_rows"]:
            for button in row:
                assert not set(button["callback"]) & set("=&?")

    def test_toggle_flips_exactly_one_bit(self):
        assert apply_clarify_toggle(0, 2) == 4
        assert apply_clarify_toggle(4, 2) == 0
        assert apply_clarify_toggle(5, 1) == 7

    def test_selected_options_come_back_in_offer_order(self):
        options = ["Маникюр", "Педикюр", "Стрижка"]
        assert selected_clarification_options(options, 0b101) == ["Маникюр", "Стрижка"]
        assert selected_clarification_options(options, 0) == []


class TestMultiselectRender:
    def test_two_taps_accumulate_into_one_mask(self):
        """The brief's live proof, at the level this module owns.

        Tap «Маникюр», then tap «Стрижка»: the mask the second tap SUBMITS
        holds both. Whether that redraw reaches MAX as an edit or as a new
        message is the channel's job (``edit_message_or_send``); what is
        pinned here is that neither tap loses the other's choice.
        """
        options = ["Маникюр", "Педикюр", "Стрижка"]
        screen = render_multiselect_clarification("Что нужно?", options, mask=0)

        first = parse_clarify_callback(screen.action_data["button_rows"][0][0]["callback"])
        assert first is not None
        mask = apply_clarify_toggle(first.mask, first.index)

        screen = render_multiselect_clarification("Что нужно?", options, mask=mask)
        third = parse_clarify_callback(screen.action_data["button_rows"][2][0]["callback"])
        assert third is not None
        mask = apply_clarify_toggle(third.mask, third.index)

        screen = render_multiselect_clarification("Что нужно?", options, mask=mask)
        submit = parse_clarify_callback(screen.action_data["button_rows"][-2][0]["callback"])
        assert submit is not None and submit.kind == "submit"
        assert selected_clarification_options(options, submit.mask) == ["Маникюр", "Стрижка"]

    def test_selected_options_are_marked_and_unselected_are_not(self):
        reply = render_multiselect_clarification("Что?", ["A", "B"], mask=0b01)
        rows = reply.action_data["button_rows"]
        assert rows[0][0]["label"].startswith(discovery.CLARIFY_MARK_ON)
        assert rows[1][0]["label"].startswith(discovery.CLARIFY_MARK_OFF)

    def test_mark_is_a_prefix_so_truncation_cannot_eat_it(self):
        """MAX clips a label at the tail; a trailing mark is lost first."""
        reply = render_multiselect_clarification("Что?", ["я" * 200], mask=1)
        label = reply.action_data["button_rows"][0][0]["label"]
        assert label.startswith(discovery.CLARIFY_MARK_ON)
        assert len(label) == 40

    def test_last_two_rows_are_continue_and_none(self):
        reply = render_multiselect_clarification("Что?", ["A", "B"], mask=0)
        rows = reply.action_data["button_rows"]
        assert rows[-2][0]["label"] == discovery.CLARIFY_SUBMIT_LABEL
        assert rows[-1][0]["callback"] == CLARIFY_NONE_CALLBACK

    def test_mode_reads_back_as_choose_many(self):
        reply = render_multiselect_clarification("Что?", ["A"], mask=0)
        assert clarification_mode_of(reply.action_data) == CLARIFICATION_MODE_CHOOSE_MANY

    def test_the_channel_renders_the_rows_as_a_real_keyboard(self):
        from apps.channels.max.handler import _build_attachments

        reply = render_multiselect_clarification("Что?", ["A", "B"], mask=0b10)
        attachments = _build_attachments(reply.action_data)
        assert attachments is not None
        buttons = attachments[0]["payload"]["buttons"]
        # 2 options + Продолжить + Ни один вариант, one per row.
        assert len(buttons) == 4
        assert all(len(row) == 1 for row in buttons)
        assert buttons[1][0]["text"].startswith(discovery.CLARIFY_MARK_ON)

    def test_no_options_degrades_to_a_bare_question(self):
        reply = render_multiselect_clarification("Что нужно?", [], mask=0)
        assert reply.action_data is None
        assert reply.text == "Что нужно?"

    def test_a_sixth_option_never_reaches_the_keyboard(self):
        reply = render_multiselect_clarification("Что?", [f"o{i}" for i in range(9)], mask=0)
        assert len(reply.action_data["button_rows"]) == 5 + 2


class TestModeSurvivesTheConciergeDispatch:
    """The wire from the model's tool call to the rendered reply.

    Before DRF-1362 the contract, the normaliser and the reader could all be
    right while the live turn still got a DERIVED mode, because the two links
    in between dropped the field: ``_dispatch_tool`` never read it off the
    arguments and the render call never passed it on. Both are one line, and
    both are the reason the ticket existed.
    """

    @staticmethod
    def _tc(name: str, arguments: str):
        from types import SimpleNamespace

        return SimpleNamespace(function=SimpleNamespace(name=name, arguments=arguments))

    def test_dispatch_carries_the_models_mode_through(self):
        from apps.orchestrator.concierge import _dispatch_tool

        result = _dispatch_tool(
            self._tc(
                "ask_clarification",
                '{"question": "Что нужно?", "options": ["A", "B"], "mode": "choose_many"}',
            ),
            None,
        )
        assert result.action_data["mode"] == "choose_many"
        assert result.action_data["options"] == ["A", "B"]

    def test_dispatch_leaves_mode_absent_when_the_model_omits_it(self):
        from apps.orchestrator.concierge import _dispatch_tool

        result = _dispatch_tool(
            self._tc("ask_clarification", '{"question": "Что нужно?", "options": ["A"]}'),
            None,
        )
        assert result.action_data["mode"] is None
        # …and the renderer then derives it, which is the pre-DRF-1362 meaning.
        rendered = discovery._render_ask_clarification(
            result.action_data["question"],
            result.action_data["options"],
            result.action_data["mode"],
        )
        assert clarification_mode_of(rendered.action_data) == CLARIFICATION_MODE_CONFIRM_ONE

    def test_an_unrecognised_mode_from_the_model_never_escapes_the_enum(self):
        """The dispatcher passes the string through unvalidated on purpose;
        the renderer is where an untrusted value stops."""
        from apps.orchestrator.concierge import _dispatch_tool

        result = _dispatch_tool(
            self._tc(
                "ask_clarification",
                '{"question": "Что?", "options": ["A"], "mode": "pick_whatever"}',
            ),
            None,
        )
        assert result.action_data["mode"] == "pick_whatever"
        rendered = discovery._render_ask_clarification(
            result.action_data["question"],
            result.action_data["options"],
            result.action_data["mode"],
        )
        assert clarification_mode_of(rendered.action_data) in CLARIFICATION_MODES

    def test_free_survives_end_to_end(self):
        """The case the whole contract exists for: options ARE offered, and
        typing something else is still a valid answer. Indistinguishable from
        confirm_one without the mode."""
        from apps.orchestrator.concierge import _dispatch_tool

        result = _dispatch_tool(
            self._tc(
                "ask_clarification",
                '{"question": "Например?", "options": ["A", "B"], "mode": "free"}',
            ),
            None,
        )
        rendered = discovery._render_ask_clarification(
            result.action_data["question"],
            result.action_data["options"],
            result.action_data["mode"],
        )
        assert clarification_mode_of(rendered.action_data) == CLARIFICATION_MODE_FREE
