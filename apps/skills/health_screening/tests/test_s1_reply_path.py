"""Сторож пути ответа на S1-сообщение (DRF-1999, S-3a, вариант i).

Сторож S-1c (``test_s1_group_guard``) проверяет детекторы: поймал ли S1 гейт
``pre_check`` или классификатор. Этот сторож проверяет, что человек на S1 получает
в ответ: живой per-tenant ход идёт гейтом (``apps/channels/max/handler.py``,
``evaluate_inbound``), затем навыками в порядке регистрации
(``apps/skills/registry.py`` — первый ``matches()`` выигрывает).

Для каждой положительной S1-фикстуры ответ — либо ответ гейта (навыки не
вызываются), либо ``RED_FLAG_REPLY`` навыка health_screening. Никогда не
уточняющий вопрос ``SOFT_PAIN_REPLY`` и никогда не ответ другого навыка, стоящего
раньше. Так закрыта буква S-3a — «S1 без повторного вопроса» — на пути ответа.

Чего этот сторож НЕ проверяет — названо в PR отдельным разделом: S1 не держится
через ход. Памятка скрининга пишет только SOFT, reply_kind «health_red_flag» никто не
читает, причина передачи ``MEDICAL_RED_FLAG`` не пишется нигде. Следующая реплика
«запишите на массаж» идёт в запись (T-S1-03 и V4 в рантайме не выполняются).

Навыки впереди health_screening в ``matches()`` читают только текст реплики, поэтому
сторож вызывает их без базы. Если впереди встанет другой навык, первым краснеет
структурный тест.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest

from apps.orchestrator.safety.gate import evaluate_inbound
from apps.skills import registry
from apps.skills.base import SkillContext
from apps.skills.health_screening.skill import RED_FLAG_REPLY, SOFT_PAIN_REPLY
from apps.skills.health_screening.tests.s1_fixtures import FIXTURES, KNOWN_MISSES, S1Fixture

#: Навыки, зарегистрированные раньше health_screening (``apps/skills/apps.py:14, :23, :24``).
#: Все три в ``matches()`` читают только текст реплики.
AHEAD_OF_HEALTH_SCREENING: tuple[str, ...] = (
    "privacy_consent",
    "proactive_opt_out",
    "human_handoff",
)

#: Фикстуры, которые гейт ``pre_check`` останавливает до любого навыка (HANDOFF →
#: ``CRISIS_REPLY_TEXT``). Кардиальная фраза ловится только гейтом: классификатор на
#: «сердечный приступ» даёт NONE — важно для S-2 (DRF-2000).
GATE_STOPS: frozenset[tuple[str, str, str]] = frozenset({("G3", "explicit", "сердечный приступ")})

#: Счётчики пути ответа (замер 15.09 на ``62b0186c``). Меняются только вместе с фикстурами,
#: число объявляется в PR до прогона.
GATE_STOPS_COUNT = 1
RED_FLAG_REPLIES_COUNT = 44
QUESTIONS_COUNT = 0

#: Положительные фикстуры вне реестра известных пропусков.
POSITIVES: list[S1Fixture] = [
    f for f in FIXTURES if f.expected_detected and f.key not in KNOWN_MISSES
]


def _context(text: str) -> SkillContext:
    return cast(
        SkillContext,
        SimpleNamespace(
            message_text=text,
            conversation=None,
            bot_user=None,
            intent=None,
            has_attachments=False,
            trace_id="",
        ),
    )


def _reply_path(fixture: S1Fixture) -> str:
    """Что получит человек: gate / red_flag / soft / ahead:<навык> / unmatched."""

    if not evaluate_inbound(fixture.text).allowed:
        return "gate"
    skills = registry.registered()
    names = [skill.name for skill in skills]
    index = names.index("health_screening")
    if tuple(names[:index]) != AHEAD_OF_HEALTH_SCREENING:
        return "ahead-changed"
    context = _context(fixture.text)
    for skill in skills[:index]:
        if skill.matches(context):
            return f"ahead:{skill.name}"
    health = skills[index]
    if not health.matches(context):
        return "unmatched"
    reply = health.handle(context).reply_text
    if reply == RED_FLAG_REPLY:
        return "red_flag"
    if reply == SOFT_PAIN_REPLY:
        return "soft"
    return "unmatched"


def _fixture_id(fixture: S1Fixture) -> str:
    return f"{fixture.group}-{fixture.kind}-{fixture.core}"


def test_the_skills_ahead_of_health_screening_are_known_and_booking_comes_later() -> None:
    names = [skill.name for skill in registry.registered()]

    assert tuple(names[: names.index("health_screening")]) == AHEAD_OF_HEALTH_SCREENING
    assert names.index("health_screening") < names.index("booking")


@pytest.mark.parametrize("fixture", POSITIVES, ids=_fixture_id)
def test_an_s1_message_gets_the_gate_or_the_red_flag_reply_never_a_question(
    fixture: S1Fixture,
) -> None:
    expected = "gate" if fixture.key in GATE_STOPS else "red_flag"

    assert _reply_path(fixture) == expected


def test_the_reply_path_is_counted() -> None:
    paths = [_reply_path(fixture) for fixture in POSITIVES]

    assert paths.count("gate") == GATE_STOPS_COUNT
    assert paths.count("red_flag") == RED_FLAG_REPLIES_COUNT
    assert paths.count("soft") == QUESTIONS_COUNT
    assert len(paths) == GATE_STOPS_COUNT + RED_FLAG_REPLIES_COUNT
