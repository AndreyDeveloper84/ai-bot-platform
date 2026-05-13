"""Fixture schema + loader tests (DRF-508/509 / Sprint 5 / C1+C2)."""

from __future__ import annotations

import pytest

from apps.replay.fixtures.schema import Fixture, from_dict, to_dict


class TestFixtureShape:
    def test_minimal(self):
        f = Fixture(
            name="t",
            description="d",
            input={"text": "hello"},
        )
        assert f.must_pass == []
        assert f.forbidden == []
        assert f.voice_check == {}
        assert f.expected_action_type is None
        assert f.cosmetologist_reviewed is False

    def test_frozen(self):
        from dataclasses import FrozenInstanceError

        f = Fixture(name="t", description="d", input={"text": "hi"})
        with pytest.raises(FrozenInstanceError):
            f.name = "tampered"  # type: ignore[misc]

    def test_default_factory_isolation(self):
        a = Fixture(name="a", description="", input={"text": "x"})
        b = Fixture(name="b", description="", input={"text": "y"})
        a.must_pass.append({"intent": "faq"})  # type: ignore[union-attr]
        assert b.must_pass == []  # different list instance


class TestFromDict:
    def test_minimal_round_trip(self):
        data = {
            "name": "t",
            "description": "d",
            "input": {"text": "hi"},
        }
        f = from_dict(data)
        assert f.name == "t"
        assert f.input == {"text": "hi"}

    def test_round_trip_via_to_dict(self):
        f = Fixture(
            name="full",
            description="full fixture",
            input={"channel": "max", "text": "оператор"},
            must_pass=[{"skill_used": "human_handoff"}],
            forbidden=[{"response_contains_any": ["не могу"]}],
            voice_check={"max_length": 600, "forbidden_phrases": [r"\bguarantee\b"]},
            expected_action_type=None,
            cosmetologist_reviewed=True,
        )
        roundtrip = from_dict(to_dict(f))
        assert roundtrip == f

    def test_unknown_key_rejected(self):
        with pytest.raises(ValueError, match="unknown top-level keys"):
            from_dict(
                {
                    "name": "t",
                    "description": "d",
                    "input": {"text": "x"},
                    "wat": "bogus",
                },
                source="test",
            )

    def test_missing_required_key(self):
        with pytest.raises(ValueError, match="missing required key 'name'"):
            from_dict({"description": "d", "input": {"text": "x"}}, source="test")

    def test_missing_input_text(self):
        with pytest.raises(ValueError, match="must be a mapping with at least 'text'"):
            from_dict({"name": "t", "description": "d", "input": {}}, source="test")

    def test_non_dict_top_level(self):
        with pytest.raises(ValueError, match="top-level must be a mapping"):
            from_dict("not a dict", source="test")  # type: ignore[arg-type]


class TestLoader:
    def test_load_fixture(self, tmp_path):
        from apps.replay.fixtures.loader import load_fixture

        p = tmp_path / "f.yaml"
        p.write_text(
            "name: t\ndescription: d\ninput:\n  text: hi\n",
            encoding="utf-8",
        )
        f = load_fixture(p)
        assert f.name == "t"

    def test_load_fixture_set_recursive(self, tmp_path):
        from apps.replay.fixtures.loader import load_fixture_set

        sub = tmp_path / "sub"
        sub.mkdir()
        (tmp_path / "a.yaml").write_text(
            "name: a\ndescription: ''\ninput:\n  text: x\n", encoding="utf-8"
        )
        (sub / "b.yaml").write_text(
            "name: b\ndescription: ''\ninput:\n  text: y\n", encoding="utf-8"
        )
        result = load_fixture_set(tmp_path)
        names = [f.name for f in result]
        # Sorted by path → "a" comes before "sub/b".
        assert names == ["a", "b"]

    def test_load_fixture_malformed_yaml(self, tmp_path):
        from apps.replay.fixtures.loader import load_fixture

        p = tmp_path / "bad.yaml"
        p.write_text("name: [unclosed", encoding="utf-8")
        with pytest.raises(ValueError, match="malformed YAML"):
            load_fixture(p)

    def test_load_fixture_set_non_directory(self, tmp_path):
        from apps.replay.fixtures.loader import load_fixture_set

        with pytest.raises(ValueError, match="not a directory"):
            load_fixture_set(tmp_path / "nonexistent")

    def test_load_fixture_unknown_key_path_in_error(self, tmp_path):
        from apps.replay.fixtures.loader import load_fixture

        p = tmp_path / "bad-key.yaml"
        p.write_text("name: t\ndescription: d\ninput:\n  text: x\nbogus: 1\n", encoding="utf-8")
        with pytest.raises(ValueError) as exc_info:
            load_fixture(p)
        assert str(p) in str(exc_info.value)
        assert "bogus" in str(exc_info.value)


class TestGoldenFixtureSet:
    """C3 + I5 + I1 — sanity for golden YAMLs.

    * Sprint 5 / C3 shipped 30 fixtures across {privacy, handoff, faq}.
    * Sprint 6 / I5 added 5 orchestrator-specific fixtures → 35 total.
    * Sprint 7 / I1 added 6 KB-driven FAQ scenarios → 41 total.
    """

    def test_all_41_load(self):
        from pathlib import Path

        from apps.replay.fixtures.loader import load_fixture_set

        root = Path(__file__).resolve().parents[1] / "fixtures" / "golden"
        fixtures = load_fixture_set(root)
        assert len(fixtures) == 41, f"expected 41 golden fixtures, got {len(fixtures)}"

    def test_balanced_per_category(self):
        from pathlib import Path

        from apps.replay.fixtures.loader import load_fixture_set

        root = Path(__file__).resolve().parents[1] / "fixtures" / "golden"
        privacy = load_fixture_set(root / "privacy")
        handoff = load_fixture_set(root / "handoff")
        faq = load_fixture_set(root / "faq")
        orchestrator = load_fixture_set(root / "orchestrator")
        assert len(privacy) == 10
        assert len(handoff) == 10
        # Sprint 5 / C3 shipped 10 baseline FAQ; Sprint 7 / I1 added 6
        # KB-driven scenarios (kb_happy_hours, kb_happy_price,
        # kb_handoff_unknown, kb_multi_doctype, kb_empty_collection,
        # kb_low_confidence) → 16 total.
        assert len(faq) == 16
        assert len(orchestrator) == 5  # Sprint 6 / I5

    def test_every_fixture_has_at_least_one_assertion(self):
        from pathlib import Path

        from apps.replay.fixtures.loader import load_fixture_set

        root = Path(__file__).resolve().parents[1] / "fixtures" / "golden"
        for f in load_fixture_set(root):
            assert f.must_pass or f.forbidden, (
                f"{f.name}: golden fixture must have at least one must_pass or forbidden rule"
            )

    def test_every_fixture_has_voice_check(self):
        from pathlib import Path

        from apps.replay.fixtures.loader import load_fixture_set

        root = Path(__file__).resolve().parents[1] / "fixtures" / "golden"
        for f in load_fixture_set(root):
            assert f.voice_check, f"{f.name}: golden fixture must have voice_check"


class TestAdversarialFixtureSet:
    """C4 — 30 adversarial fixtures; cosmetologist_reviewed=false (Phase-1 audit flips).

    Adversarial fixtures use forbidden-only assertions targeting affirmative
    bot phrases (medical advice, PII leaks, prompt-injection compliance) that
    a healthy bot must never produce. They pass against current Sprint 3
    echo dispatch (echo mirrors user input verbatim, which doesn't contain
    these affirmative bot-voice phrases) and stay meaningful through Sprint 6+.
    """

    def test_all_load(self):
        from pathlib import Path

        from apps.replay.fixtures.loader import load_fixture_set

        root = Path(__file__).resolve().parents[1] / "fixtures" / "adversarial"
        fixtures = load_fixture_set(root)
        assert len(fixtures) == 30, f"expected 30 adversarial fixtures, got {len(fixtures)}"

    def test_all_marked_unreviewed(self):
        """Per Sprint 5 plan #7: ship as cosmetologist_reviewed=false; Phase-1 expert flips."""
        from pathlib import Path

        from apps.replay.fixtures.loader import load_fixture_set

        root = Path(__file__).resolve().parents[1] / "fixtures" / "adversarial"
        for f in load_fixture_set(root):
            assert f.cosmetologist_reviewed is False, (
                f"{f.name}: adversarial fixtures must ship with cosmetologist_reviewed=false; "
                "Phase-1 expert audit flips per fixture"
            )

    def test_all_have_forbidden_rules(self):
        """Adversarial = forbidden-only assertions (safety boundary)."""
        from pathlib import Path

        from apps.replay.fixtures.loader import load_fixture_set

        root = Path(__file__).resolve().parents[1] / "fixtures" / "adversarial"
        for f in load_fixture_set(root):
            assert f.forbidden, f"{f.name}: adversarial fixture must have forbidden rules"

    def test_all_pass_against_echo_baseline(self):
        """Every adversarial fixture must pass against Sprint 3 echo dispatch.

        Simulates trace: response_text = input.text (echo verbatim). If a
        fixture fails here, its forbidden phrases overlap user input — needs
        re-wording so forbidden targets bot-voice affirmations, not user words.
        """
        from pathlib import Path

        from apps.replay.assertions import evaluate, evaluate_voice
        from apps.replay.fixtures.loader import load_fixture_set

        root = Path(__file__).resolve().parents[1] / "fixtures" / "adversarial"
        for f in load_fixture_set(root):
            text = f.input.get("text", "")
            trace = {
                "intent": "",
                "skill_used": "",
                "safety_decision": "allow",
                "tool_calls": [],
                "response_text": text,
            }
            failures = evaluate(trace, f.must_pass, f.forbidden)
            voice_failures = evaluate_voice(text, f.voice_check)
            assert not failures and not voice_failures, (
                f"{f.name}: forbidden/voice clashes with echo baseline: {failures + voice_failures}"
            )


class TestVoiceFixtureSet:
    """C5 — 20 voice regression fixtures targeting brand-voice drift.

    Each fixture pairs a benign user prompt with forbidden bot-voice phrases
    (guarantees, corporate speak, clinical tone, fake personalization, etc.)
    that must never appear in an outbound assistant reply. Patterns drawn
    from legacy_maxbot/voice_examples.py + Sprint 4 / C4 brand voice config.
    """

    def test_all_load(self):
        from pathlib import Path

        from apps.replay.fixtures.loader import load_fixture_set

        root = Path(__file__).resolve().parents[1] / "fixtures" / "voice"
        fixtures = load_fixture_set(root)
        assert len(fixtures) == 20, f"expected 20 voice fixtures, got {len(fixtures)}"

    def test_all_have_forbidden_or_voice_check(self):
        from pathlib import Path

        from apps.replay.fixtures.loader import load_fixture_set

        root = Path(__file__).resolve().parents[1] / "fixtures" / "voice"
        for f in load_fixture_set(root):
            has_forbidden = bool(f.forbidden) or bool(
                (f.voice_check or {}).get("forbidden_phrases")
            )
            assert has_forbidden, (
                f"{f.name}: voice fixture must declare at least one forbidden rule"
            )

    def test_all_pass_against_echo_baseline(self):
        from pathlib import Path

        from apps.replay.assertions import evaluate, evaluate_voice
        from apps.replay.fixtures.loader import load_fixture_set

        root = Path(__file__).resolve().parents[1] / "fixtures" / "voice"
        for f in load_fixture_set(root):
            text = f.input.get("text", "")
            trace = {
                "intent": "",
                "skill_used": "",
                "safety_decision": "allow",
                "tool_calls": [],
                "response_text": text,
            }
            failures = evaluate(trace, f.must_pass, f.forbidden)
            voice_failures = evaluate_voice(text, f.voice_check)
            assert not failures and not voice_failures, (
                f"{f.name}: forbidden/voice clashes with echo baseline: {failures + voice_failures}"
            )
