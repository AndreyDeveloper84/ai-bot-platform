"""Анонс памяти — «Запомнила: …» при первом сохранении зелёного факта (DRF-1292).

Решение владельца 19.09 (§52 В3): при первой записи зелёного факта — и только
один раз на факт; при supersede — снова — Ayla добавляет к ответу одну строку
«Запомнила: ты …. Скажи „забудь“, если не надо.» Формулировка факта — та же,
что в «покажи, что знаешь» и на экране Mini App (``describe_green_content``).

Сторожа:
* о выведенном (``source != explicit``) не объявляется — ложный вход → красный;
* не чаще одной служебной строки на ход: анонс ИЛИ вопрос памяти
  (``memory_ask``), никогда оба;
* факт, уже записанный, второй раз не объявляется (дедуп писателя);
* новое значение одиночного ключа (supersede) — объявляется снова;
* ход-команда памяти («забудь…») ничего не пишет и не объявляет.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from django.utils import timezone

from apps.identity.models import MemoryEntry, UserPersonalContext
from apps.orchestrator.discovery import DiscoveryReply
from apps.orchestrator.memory.write_sink import WriteSink, link_within_budget
from apps.orchestrator.memory_announce import (
    ANNOUNCE_TAIL,
    announce_line,
    guard_service_line,
    weave_service_line,
)

pytestmark = pytest.mark.django_db


def _upc():
    return UserPersonalContext.objects.create(user_id=uuid.uuid4())


def _entry(upc, *, source=MemoryEntry.SOURCE_EXPLICIT, content=None, **over):
    kwargs = dict(
        user_id=upc.user_id,
        personal_context=upc,
        sensitivity_zone=MemoryEntry.SENSITIVITY_GREEN,
        source=source,
        provenance=MemoryEntry.PROVENANCE_USER_STATED
        if source == MemoryEntry.SOURCE_EXPLICIT
        else None,
        last_inferred_at=None if source == MemoryEntry.SOURCE_EXPLICIT else timezone.now(),
        kind="lifestyle",
        content=content or {"key": "diet", "value": "vegan"},
    )
    kwargs.update(over)
    return MemoryEntry.objects.create(**kwargs)


class TestAnnounceLine:
    def test_explicit_fact_is_announced_in_the_chat_wording(self):
        upc = _upc()
        line = announce_line([_entry(upc)])
        # Та же фраза, что «Помню, что ты …» в чате и label на экране.
        assert (
            line
            == "Запомнила: ты придерживаешься веганского питания. Скажи «забудь про питание», если не надо."
        )
        assert ANNOUNCE_TAIL not in line  # доменная подсказка, не голое «забудь»

    def test_inferred_fact_is_never_announced(self):
        """Ложный вход: выведенное — не сказанное. Строки нет вовсе."""
        upc = _upc()
        inferred = _entry(
            upc,
            source=MemoryEntry.SOURCE_INFERRED,
            content={"key": "preferred_time_slots", "value": "evening"},
        )
        assert announce_line([inferred]) is None

    def test_mixed_list_announces_only_the_explicit_part(self):
        upc = _upc()
        explicit = _entry(upc)
        inferred = _entry(
            upc,
            source=MemoryEntry.SOURCE_INFERRED,
            content={"key": "preferred_time_slots", "value": "evening"},
        )
        line = announce_line([inferred, explicit])
        assert line is not None
        assert "веганского" in line
        assert "вечер" not in line

    def test_two_explicit_facts_one_line(self):
        upc = _upc()
        a = _entry(upc)
        b = _entry(upc, content={"key": "price_range", "max": "3000.00"})
        line = announce_line([a, b])
        assert line is not None
        assert line.count("Запомнила") == 1
        assert "веганского" in line and "3 000" in line
        assert "«забудь про питание» или «забудь про бюджет»" in line

    def test_unrenderable_fact_gives_no_line(self):
        upc = _upc()
        weird = _entry(upc, content={"key": "unknown_key", "value": "???"})
        assert announce_line([weird]) is None

    def test_empty_gives_no_line(self):
        assert announce_line([]) is None


class TestOneServiceLinePerTurn:
    def _reply(self):
        return DiscoveryReply(text="Конечно, подберу.", action_data=None, persisted=False)

    def test_announce_wins_and_the_question_is_not_asked(self):
        upc = _upc()
        weave = MagicMock(side_effect=lambda _c, _b, r: DiscoveryReply(text=r.text + "\n\nВопрос?"))
        out = weave_service_line(
            SimpleNamespace(id=uuid.uuid4()),
            SimpleNamespace(id=1),
            self._reply(),
            written=[_entry(upc)],
            allow_question=True,
            weave_question=weave,
        )
        assert out.text.startswith("Конечно, подберу.")
        assert "Запомнила: ты придерживаешься веганского питания." in out.text
        assert "Вопрос?" not in out.text
        weave.assert_not_called()

    def test_nothing_written_falls_through_to_the_question(self):
        weave = MagicMock(side_effect=lambda _c, _b, r: DiscoveryReply(text=r.text + "\n\nВопрос?"))
        out = weave_service_line(
            SimpleNamespace(id=uuid.uuid4()),
            SimpleNamespace(id=1),
            self._reply(),
            written=[],
            allow_question=True,
            weave_question=weave,
        )
        assert out.text.endswith("Вопрос?")
        assert "Запомнила" not in out.text
        weave.assert_called_once()

    def test_inferred_only_written_still_allows_the_question(self):
        """Выведенное не объявляем — строка уходит вопросу, не пропадает впустую."""
        upc = _upc()
        inferred = _entry(
            upc,
            source=MemoryEntry.SOURCE_INFERRED,
            content={"key": "preferred_time_slots", "value": "evening"},
        )
        weave = MagicMock(side_effect=lambda _c, _b, r: r)
        weave_service_line(
            SimpleNamespace(id=uuid.uuid4()),
            SimpleNamespace(id=1),
            self._reply(),
            written=[inferred],
            allow_question=True,
            weave_question=weave,
        )
        weave.assert_called_once()

    def test_question_not_allowed_and_nothing_written_leaves_reply_alone(self):
        weave = MagicMock()
        reply = self._reply()
        out = weave_service_line(
            SimpleNamespace(id=uuid.uuid4()),
            SimpleNamespace(id=1),
            reply,
            written=[],
            allow_question=False,
            weave_question=weave,
        )
        assert out is reply
        weave.assert_not_called()

    def test_persisted_flag_and_keyboard_survive(self):
        upc = _upc()
        reply = DiscoveryReply(text="Ок.", action_data={"k": 1}, persisted=True)
        out = weave_service_line(
            SimpleNamespace(id=uuid.uuid4()),
            SimpleNamespace(id=1),
            reply,
            written=[_entry(upc)],
            allow_question=False,
            weave_question=MagicMock(),
        )
        assert out.action_data == {"k": 1}
        assert out.persisted is True


class TestSaidFactAnnounce:
    def test_city_from_said_memory_gets_its_domain_hint(self):
        """said_memory пишет display во 2-м лице; подсказка — «забудь про город»."""
        upc = _upc()
        entry = _entry(
            upc,
            kind="preference",
            content={
                "key": "city",
                "value": "Пенза",
                "origin": "conversation",
                "display": "ищешь мастеров в городе Пенза",
            },
        )
        line = announce_line([entry])
        assert (
            line
            == "Запомнила: ты ищешь мастеров в городе Пенза. Скажи «забудь про город», если не надо."
        )


class TestLinkBudget:
    def test_none_after_the_budget_is_a_timeout(self):
        sink = WriteSink()
        import time

        link_within_budget(sink, time.monotonic() - 1.0, 1.0, None)
        assert sink.link_timed_out is True

    def test_none_well_before_the_budget_is_ayla_down_not_a_timeout(self):
        sink = WriteSink()
        import time

        link_within_budget(sink, time.monotonic() - 0.1, 1.0, None)
        assert sink.link_timed_out is False

    def test_resolved_or_no_budget_never_marks(self):
        import time

        sink = WriteSink()
        link_within_budget(sink, time.monotonic() - 5.0, 1.0, uuid.uuid4())
        link_within_budget(sink, time.monotonic() - 5.0, None, None)
        link_within_budget(None, time.monotonic() - 5.0, 1.0, None)
        assert sink.link_timed_out is False


class TestGuardedServiceLine:
    def _pair(self):
        before = DiscoveryReply(text="Ответ.", action_data={"k": 1}, persisted=True)
        after = DiscoveryReply(
            text="Ответ.\n\nЗапомнила: ты ешь халяль.", action_data={"k": 1}, persisted=True
        )
        return before, after

    def test_allowed_tail_ships(self):
        before, after = self._pair()
        seen: list[str] = []

        def guard(tail):
            seen.append(tail)
            return SimpleNamespace(blocked=False)

        assert guard_service_line(before, after, guard) is after
        # Гард видит ТОЛЬКО дописанное — ответ он уже проверял.
        assert seen == ["Запомнила: ты ешь халяль."]

    def test_blocked_tail_is_dropped_and_the_reply_stays_as_approved(self):
        before, after = self._pair()
        out = guard_service_line(before, after, lambda tail: SimpleNamespace(blocked=True))
        assert out is before

    def test_guard_failure_drops_the_line_not_the_reply(self):
        before, after = self._pair()

        def boom(tail):
            raise RuntimeError("guard down")

        assert guard_service_line(before, after, boom) is before

    def test_unchanged_reply_is_not_guarded_again(self):
        before, _ = self._pair()
        guard = MagicMock()
        assert guard_service_line(before, before, guard) is before
        guard.assert_not_called()
