"""DRF-2725 — лицензия знания доезжает до первой проверки ответа и переживает её.

Ответ консьержа проверяется дважды: консьержем — до записи в историю, и
каналом — перед отправкой. Знание читает только консьерж. Если лицензия
останется его локальной переменной, канальная проверка будет судить вслепую
(контракт DRF-2718, §5.2). Поэтому лицензия — поле результата хода и едет
рядом с трассой инструментов: ``DiscoveryReply`` → ``TurnReply`` → канал.

Здесь — первая половина пути: шов объявляет поле переносимым, консьерж
отдаёт лицензию своему вызову хука и сохраняет её на ответе даже при замене
текста. Вторая половина — канал, сквозь настоящий шов —
``apps/channels/tests/test_handler_knowledge_licence_2725.py``.

Читателя знания в этом листе нет — лицензию создаёт сам узел.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from apps.orchestrator import concierge
from apps.orchestrator.concierge import generate_concierge_reply
from apps.orchestrator.discovery import DiscoveryReply
from apps.orchestrator.knowledge_licence import (
    KnowledgeLicence,
    LicensedClaim,
    LicensedSubject,
    SubjectState,
)
from apps.orchestrator.safety import gate as gate_mod
from apps.orchestrator.safety.outbound import REPLACEMENT_TEXT
from apps.orchestrator.turn_seam import DISCOVERY_TO_TURN, TurnReply

_CLEAN = "Массаж спины у Дениса стоит 3 500 ₽ за час. Записать на завтра в 18:00?"
_FORBIDDEN = "Я гарантирую результат уже после первого сеанса."

LICENCE = KnowledgeLicence(
    read_at=datetime(2026, 10, 2, 12, 0, 0, tzinfo=UTC),
    subjects=(
        LicensedSubject(
            template_id="tpl-massage",
            state=SubjectState.KNOWN,
            claims=(LicensedClaim(claim_id="claim-1", kind="capability", key="relaxation"),),
        ),
    ),
)


def _spy_on_guard(monkeypatch) -> list[dict]:
    """Записывать аргументы хука, не подменяя его работу.

    Консьерж импортирует хук внутри функции — из модуля ``gate``; туда и
    ставится наблюдатель.
    """
    real = gate_mod.guard_outbound
    seen: list[dict] = []

    def spy(text, **kwargs):
        seen.append({"text": text, **kwargs})
        return real(text, **kwargs)

    monkeypatch.setattr(gate_mod, "guard_outbound", spy)
    return seen


class TestTheSeamDeclaresTheField:
    def test_the_licence_is_in_the_carried_map(self):
        """Шов копирует поля ПЕРЕЧИСЛЕНИЕМ; поле вне перечисления теряется."""
        assert DISCOVERY_TO_TURN["knowledge_licence"] == "knowledge_licence"

    def test_both_carriers_default_to_no_licence(self):
        assert DiscoveryReply(text="x").knowledge_licence is None
        assert TurnReply(matched=True).knowledge_licence is None


@pytest.mark.django_db(transaction=True)
class TestTheConciergeHandsTheLicenceToTheGuard:
    def _bot_user_and_conversation(self, uid: str):
        from apps.conversations.services import resolve_active_global_conversation
        from apps.identity.services import resolve_or_create_global_bot_user

        bot_user = resolve_or_create_global_bot_user(
            channel="max", channel_user_id=uid, chat_id=uid
        )
        return bot_user, resolve_active_global_conversation(bot_user)

    def _turn_returns(self, monkeypatch, reply: DiscoveryReply) -> None:
        monkeypatch.setattr(concierge, "_concierge_turn", lambda *a, **kw: reply)

    def test_the_guard_receives_the_turns_licence(self, monkeypatch):
        self._turn_returns(monkeypatch, DiscoveryReply(text=_CLEAN, knowledge_licence=LICENCE))
        seen = _spy_on_guard(monkeypatch)
        bot_user, conversation = self._bot_user_and_conversation("lic-concierge-1")

        reply = generate_concierge_reply(
            "что даёт массаж", bot_user=bot_user, conversation=conversation
        )

        assert len(seen) == 1
        assert seen[0]["surface"] == "concierge"
        assert seen[0]["knowledge"] is LICENCE
        assert reply.text == _CLEAN
        assert reply.knowledge_licence is LICENCE

    def test_a_turn_without_a_reader_hands_over_none(self, monkeypatch):
        self._turn_returns(monkeypatch, DiscoveryReply(text=_CLEAN))
        seen = _spy_on_guard(monkeypatch)
        bot_user, conversation = self._bot_user_and_conversation("lic-concierge-2")

        generate_concierge_reply("привет", bot_user=bot_user, conversation=conversation)

        assert len(seen) == 1
        assert seen[0]["knowledge"] is None

    def test_a_replaced_reply_keeps_the_licence_for_the_channel(self, monkeypatch):
        """Замена текста не отменяет того, что в этом ходу читали."""
        self._turn_returns(monkeypatch, DiscoveryReply(text=_FORBIDDEN, knowledge_licence=LICENCE))
        bot_user, conversation = self._bot_user_and_conversation("lic-concierge-3")

        reply = generate_concierge_reply("поможет ли", bot_user=bot_user, conversation=conversation)

        assert reply.text == REPLACEMENT_TEXT
        assert reply.knowledge_licence is LICENCE
