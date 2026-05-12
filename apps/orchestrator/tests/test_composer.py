"""Composer tests (DRF-539 / Sprint 6 / O5)."""

from __future__ import annotations

from types import SimpleNamespace

from apps.orchestrator.composer import ComposedReply, compose
from apps.orchestrator.safety.post_check import PostCheckResult, PostCheckVerdict


def _skill_result(**fields):
    base = {
        "reply_text": "Hello world",
        "should_send": True,
        "action_type": "",
        "action_data": None,
    }
    base.update(fields)
    return SimpleNamespace(**base)


class TestBasic:
    def test_simple_passthrough(self):
        r = compose(_skill_result(reply_text="Hello"))
        assert r.text == "Hello"
        assert r.final_send is True
        assert r.safety_revised is False
        assert r.ui_keyboard == []

    def test_returns_composed_reply(self):
        r = compose(_skill_result())
        assert isinstance(r, ComposedReply)


class TestShouldSendRespected:
    def test_skill_should_send_false_returns_empty(self):
        r = compose(_skill_result(should_send=False, reply_text="ignored"))
        assert r.text == ""
        assert r.final_send is False


class TestPostCheckBlock:
    def test_block_replaces_text_with_disclaimer(self):
        post = PostCheckResult(
            verdict=PostCheckVerdict.BLOCK,
            matched_patterns=["pii_phone"],
        )
        r = compose(_skill_result(reply_text="Call +79991234567"), post_check=post)
        assert "Извините" in r.text
        assert "+79991234567" not in r.text
        assert r.safety_revised is True

    def test_block_skips_brand_voice_signoff(self):
        post = PostCheckResult(verdict=PostCheckVerdict.BLOCK)
        r = compose(
            _skill_result(reply_text="leaked"),
            post_check=post,
            brand_voice={"signoff": "— Алина из Формулы тела"},
        )
        # Safety template should not be polluted with brand signoff.
        assert "Алина" not in r.text


class TestPostCheckRevise:
    def test_revise_prefixes_text(self):
        post = PostCheckResult(verdict=PostCheckVerdict.REVISE)
        r = compose(_skill_result(reply_text="I am certain it helps"), post_check=post)
        assert r.text.startswith("Пожалуйста, обратите внимание")
        assert "I am certain it helps" in r.text
        assert r.safety_revised is True

    def test_revise_skips_brand_voice_signoff(self):
        # Safety-revised replies don't get marketing signoff.
        post = PostCheckResult(verdict=PostCheckVerdict.REVISE)
        r = compose(
            _skill_result(reply_text="something"),
            post_check=post,
            brand_voice={"signoff": "— Алина"},
        )
        assert "Алина" not in r.text


class TestPostCheckAllow:
    def test_allow_passes_through(self):
        post = PostCheckResult(verdict=PostCheckVerdict.ALLOW)
        r = compose(_skill_result(reply_text="normal reply"), post_check=post)
        assert r.text == "normal reply"
        assert r.safety_revised is False


class TestBrandVoiceSignoff:
    def test_signoff_appended(self):
        r = compose(
            _skill_result(reply_text="Здравствуйте!"),
            brand_voice={"signoff": "— Алина из Формулы тела"},
        )
        assert "Здравствуйте!" in r.text
        assert "Алина" in r.text

    def test_signoff_not_duplicated_when_already_in_text(self):
        # Skill already included the signoff (avoid double-append).
        signoff = "— Алина"
        r = compose(
            _skill_result(reply_text=f"Hi! {signoff}"),
            brand_voice={"signoff": signoff},
        )
        # Should appear once, not twice.
        assert r.text.count("Алина") == 1

    def test_no_signoff_when_brand_voice_missing(self):
        r = compose(_skill_result(reply_text="hi"))
        assert r.text == "hi"

    def test_empty_signoff_string_ignored(self):
        r = compose(_skill_result(reply_text="hi"), brand_voice={"signoff": ""})
        assert r.text == "hi"


class TestUIKeyboard:
    def test_buttons_extracted_from_action_data(self):
        r = compose(
            _skill_result(
                reply_text="Pick one",
                action_data={
                    "buttons": [
                        {"label": "Записаться", "callback": "book"},
                        {"label": "Адрес", "callback": "address"},
                    ]
                },
            )
        )
        assert len(r.ui_keyboard) == 2
        assert r.ui_keyboard[0]["label"] == "Записаться"
        assert r.ui_keyboard[1]["callback"] == "address"

    def test_no_buttons_returns_empty_keyboard(self):
        r = compose(_skill_result(reply_text="text only"))
        assert r.ui_keyboard == []

    def test_malformed_buttons_skipped(self):
        # Each button must be a dict with "label". Bad entries dropped.
        r = compose(
            _skill_result(
                reply_text="x",
                action_data={
                    "buttons": [
                        {"label": "Good", "callback": "ok"},
                        "not_a_dict",
                        {"no_label": "x"},
                    ]
                },
            )
        )
        assert len(r.ui_keyboard) == 1
        assert r.ui_keyboard[0]["label"] == "Good"

    def test_callback_optional(self):
        r = compose(
            _skill_result(
                reply_text="x",
                action_data={"buttons": [{"label": "Only label"}]},
            )
        )
        assert r.ui_keyboard[0]["callback"] == ""


class TestActionPassthrough:
    def test_action_type_forwarded(self):
        r = compose(_skill_result(action_type="show_masters"))
        assert r.action_type == "show_masters"

    def test_action_data_forwarded(self):
        data = {"master_ids": [1, 2, 3]}
        r = compose(_skill_result(action_data=data))
        assert r.action_data == data


class TestLatency:
    def test_100_calls_under_50ms(self):
        import time

        start = time.perf_counter()
        for _ in range(100):
            compose(_skill_result(reply_text="standard reply text"))
        elapsed = (time.perf_counter() - start) * 1000
        assert elapsed < 50, f"100 calls took {elapsed:.1f}ms, expected <50ms"
