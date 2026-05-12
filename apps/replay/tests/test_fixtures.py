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
