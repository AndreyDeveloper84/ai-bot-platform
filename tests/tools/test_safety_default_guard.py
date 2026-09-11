"""Тесты для tools/lint/safety_default_guard.py — вердикт не подставляется умолчанием.

Обе стороны, как у остальных сторожей в этой папке: что он ловит и что
оставляет в покое. Вторая половина важнее — сторож, краснеющий на честном
коде, снимут через неделю вместе с защитой.

Предмет — решение владельца 11.09 §3: «``payload.get("safety") or
NOT_APPLICABLE`` запрещена и охраняется AST-тестом», и §18.2 «нет данных ≠
``NOT_APPLICABLE``». Плюс §18.7: у сторожа есть положительный тест на
искусственно внесённое нарушение — не на образце, а на живом файле дерева.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT / "tools" / "lint"))

import safety_default_guard as guard  # type: ignore[import-not-found]  # noqa: E402


def _scan(body: str) -> list[str]:
    return [v.detail for v in guard.scan_source(body, path="sample.py")]


class TestItCatchesTheClass:
    """Не одно написание из §3, а класс «умолчание вместо вердикта»."""

    def test_the_owners_exact_spelling(self) -> None:
        hits = _scan('state = payload.get("safety") or NOT_APPLICABLE\n')
        assert hits == ["or-умолчание: payload.get('safety') or NOT_APPLICABLE"]

    def test_or_with_an_enum_member(self) -> None:
        hits = _scan('state = payload.get("safety") or SafetyState.NORMAL\n')
        assert len(hits) == 1 and hits[0].startswith("or-умолчание")

    def test_or_on_an_attribute(self) -> None:
        hits = _scan("verdict = state.safety or SafetyState.NORMAL\n")
        assert len(hits) == 1 and hits[0].startswith("or-умолчание")

    def test_default_argument_of_get(self) -> None:
        hits = _scan('state = payload.get("safety", SafetyState.NORMAL)\n')
        assert hits == ["умолчание в get: payload.get('safety', SafetyState.NORMAL)"]

    def test_default_argument_of_getattr(self) -> None:
        hits = _scan('state = getattr(obj, "safety_state", "normal")\n')
        assert len(hits) == 1 and hits[0].startswith("умолчание в get")

    def test_if_else_default(self) -> None:
        hits = _scan("state = safety if safety else SafetyState.STOP\n")
        assert hits == ["if-else умолчание: safety if safety else SafetyState.STOP"]

    def test_a_substituted_refusal_is_also_a_substitution(self) -> None:
        """`or STOP` не даёт жалоб — и не даёт происхождения. Тот же класс."""
        assert _scan('v = payload.get("safety_verdict") or "stop"\n')


class TestItLeavesHonestCodeAlone:
    def test_unknown_is_the_honest_default(self) -> None:
        assert _scan('state = payload.get("safety") or SafetyState.UNKNOWN\n') == []

    def test_not_evaluated_is_the_honest_default(self) -> None:
        assert _scan('v = payload.get("safety", SafetyResult.not_evaluated())\n') == []

    def test_none_default_is_absence_not_an_answer(self) -> None:
        assert _scan('v = payload.get("safety", None)\n') == []

    def test_a_state_default_on_an_unrelated_key_is_not_ours(self) -> None:
        """Сторож про вердикт безопасности, не про слово `normal` вообще."""
        assert _scan('mode = payload.get("render_mode") or "normal"\n') == []

    def test_the_mapping_itself_is_not_a_default(self) -> None:
        """A-1 строит вердикт по таблице — это ответ, а не умолчание."""
        src = "_MAPPING = {SafetyVerdict.ALLOW: (SafetyState.NORMAL, Handoff.NONE)}\n"
        # The same NORMAL, written as a fallback, IS caught — so the silence
        # below is the scanner declining, not the scanner not looking.
        assert _scan('x = payload.get("safety") or SafetyState.NORMAL\n')
        assert _scan(src) == []

    def test_a_syntax_error_is_not_a_violation(self) -> None:
        assert _scan("def (:\n") == []


class TestTheLiveTree:
    """§18.7 — положительная проба на настоящем файле, не на образце.

    Контроль присутствия: всё выше прошло бы в мире, где сканер сломан и
    не читает файлы вовсе. Здесь он читает `apps/` целиком (ноль) и тот же
    файл с одной внесённой строкой (один).
    """

    _TARGET = _PROJECT_ROOT / "apps" / "orchestrator" / "decision_readiness" / "state.py"

    def test_apps_is_clean_today(self) -> None:
        found = []
        for f in sorted((_PROJECT_ROOT / "apps").rglob("*.py")):
            found.extend(guard.scan_file(f, repo_root=_PROJECT_ROOT))
        assert found == [], [v.format() for v in found]

    def test_one_injected_default_is_found_by_line(self, tmp_path: Path) -> None:
        source = self._TARGET.read_text(encoding="utf-8")
        anchor = "    encoded_safety = _encode_safety(state.safety)\n"
        assert source.count(anchor) == 1, "anchor moved — the probe would test nothing"
        broken = source.replace(
            anchor,
            "    encoded_safety = _encode_safety(state.safety or SafetyState.NORMAL)\n",
        )
        hits = guard.scan_source(broken, path="apps/orchestrator/decision_readiness/state.py")
        assert len(hits) == 1, [v.format() for v in hits]
        expected_line = source[: source.index(anchor)].count("\n") + 1
        assert hits[0].line == expected_line
        assert guard.scan_source(source, path="apps/orchestrator/decision_readiness/state.py") == []

    def test_the_allowlist_is_only_the_guard_and_its_tests(self) -> None:
        assert guard.ALLOWED == {
            "tools/lint/safety_default_guard.py",
            "tests/tools/test_safety_default_guard.py",
        }

    @pytest.mark.parametrize("name", sorted(guard.STATE_NAMES))
    def test_unknown_is_never_a_forbidden_default(self, name: str) -> None:
        assert name != "UNKNOWN"
