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
from apps.orchestrator.memory_announce import (
    ANNOUNCE_TAIL,
    announce_line,
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
        assert line == f"Запомнила: ты придерживается веганского питания. {ANNOUNCE_TAIL}"
        assert "забудь" in line

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
        assert "Запомнила: ты придерживается веганского питания." in out.text
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
