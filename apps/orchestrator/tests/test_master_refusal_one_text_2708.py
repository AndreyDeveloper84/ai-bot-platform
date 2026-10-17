"""DRF-2708 — один текст отказа «к этому мастеру записаться нельзя» (§47.4).

Решение владельца 07.09 (``docs/OPEN_DECISIONS.md`` §47.4) утверждает слова
дословно; слово владельца 02.10 — этот текст один на все поверхности отказа
«мастер недоступен». До правки мини-приложение говорило словами владельца, а
консьерж в чате — своими («К сожалению, запись к этому мастеру сейчас
недоступна — посмотри, что ещё есть в этом салоне»).

Единый текст — только для причины «мастер». Узлы держат обе стороны:

* мастера нет в этом салоне / мастер не подключён к записи → слова §47.4, выход
  прежний (каталог этого салона, DRF-1492), запись не начинается;
* салон не найден — причина ДРУГАЯ, и текст там прежний: правка не перекрасила
  чужое основание словами о мастере из §47.4;
* копия фразы в Python равна копии в мини-приложении и литералу решения.
"""

from __future__ import annotations

import re
from pathlib import Path
from uuid import uuid4

import pytest

from apps.identity.services import resolve_or_create_global_bot_user
from apps.orchestrator import handoff as handoff_mod
from apps.orchestrator.handoff import handoff_to_booking
from apps.orchestrator.tests.test_handoff import _buttons, _master, _tenant

pytestmark = pytest.mark.django_db

OWNER_WORDS = (
    "К этому мастеру сейчас записаться нельзя. Посмотри других — подберём подходящий вариант."
)
REFUSAL_CANON_TS = (
    Path(__file__).resolve().parents[2] / "miniapp" / "src" / "lib" / "refusal-canon.ts"
)


@pytest.fixture
def no_dispatch(monkeypatch) -> list:
    called: list = []
    monkeypatch.setattr("apps.skills.registry.dispatch", lambda ctx: called.append(1))
    return called


def test_a_master_who_is_not_in_this_salon(settings, no_dispatch) -> None:
    settings.STRICT_TENANT_SCOPE = "strict"
    settings.BOOKING_VIA_AYLA_REST = True
    tenant = _tenant("t-2708-absent")
    gbu = resolve_or_create_global_bot_user(channel="max", channel_user_id="27081")

    reply = handoff_to_booking(global_bot_user=gbu, tenant_id=tenant.id, master_id=uuid4())

    assert reply.text == OWNER_WORDS
    assert [b["callback"] for b in _buttons(reply)] == [f"cb:catalog:services:{tenant.id}"]
    assert no_dispatch == []


def test_a_master_who_is_not_connected_to_booking(settings, no_dispatch) -> None:
    settings.STRICT_TENANT_SCOPE = "strict"
    settings.BOOKING_VIA_AYLA_REST = False
    tenant = _tenant("t-2708-unlinked")
    master = _master(tenant, staff_id=None)
    gbu = resolve_or_create_global_bot_user(channel="max", channel_user_id="27082")

    reply = handoff_to_booking(global_bot_user=gbu, tenant_id=tenant.id, master_id=master.id)

    assert reply.text == OWNER_WORDS
    assert [b["callback"] for b in _buttons(reply)] == [f"cb:catalog:services:{tenant.id}"]
    assert no_dispatch == []


def test_an_unknown_salon_is_not_answered_with_the_master_sentence(settings, no_dispatch) -> None:
    """Другая причина — другой текст. §47.4 сюда не распространён."""
    settings.STRICT_TENANT_SCOPE = "strict"
    gbu = resolve_or_create_global_bot_user(channel="max", channel_user_id="27083")

    reply = handoff_to_booking(global_bot_user=gbu, tenant_id=uuid4(), master_id=uuid4())

    assert "посмотри наши салоны" in reply.text
    assert reply.text != OWNER_WORDS
    assert [b["callback"] for b in _buttons(reply)] == ["cb:catalog:salons"]
    assert no_dispatch == []


def test_the_sentence_is_the_owners_word_for_word() -> None:
    assert handoff_mod.MASTER_NOT_BOOKABLE_REFUSAL == OWNER_WORDS


def test_the_mini_app_and_the_chat_hold_the_same_sentence() -> None:
    """Две копии одной фразы — в TypeScript и в Python. Разойтись им нельзя."""
    source = REFUSAL_CANON_TS.read_text(encoding="utf-8")

    found = re.search(r'export const MASTER_NOT_BOOKABLE_REFUSAL\s*=\s*"([^"]+)";', source)

    assert found is not None, "MASTER_NOT_BOOKABLE_REFUSAL не найдена в refusal-canon.ts"
    assert found.group(1) == OWNER_WORDS
    assert found.group(1) == handoff_mod.MASTER_NOT_BOOKABLE_REFUSAL
