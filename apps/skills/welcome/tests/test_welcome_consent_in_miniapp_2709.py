"""DRF-2709 — лист повторной выдачи согласия в Mini App показывает текст приветствия дословно.

Повторная выдача записывается версией ``welcome-s2-v1`` — значит, человек
обязан увидеть именно тот текст, что принимают под этой версией в чате
(решение владельца 04.10, вариант А). Mini App держит копию текста в
``apps/miniapp/src/lib/welcome-consent.ts``; этот узел не даёт копии
разойтись с источником. Разошлись — либо копия неверна, либо текст в чате
изменился без новой версии документа: и то и другое нужно увидеть.
"""

from __future__ import annotations

import json
from pathlib import Path

from apps.channels.max.global_onboarding import CONSENT_DOCUMENT_VERSION
from apps.consent.customer import DATA_STORAGE_REGRANT_DOCUMENT_VERSION
from apps.skills.welcome.skill import S2_CONSENT_TEXT, S2A_DETAILS_TEXT

WELCOME_CONSENT_TS = (
    Path(__file__).resolve().parents[3] / "miniapp" / "src" / "lib" / "welcome-consent.ts"
)


def _ts_literal(text: str) -> str:
    """Python-строка в виде TS-литерала в двойных кавычках (``\\n`` — экранированием)."""
    return json.dumps(text, ensure_ascii=False)


def test_the_miniapp_carries_the_welcome_text_verbatim() -> None:
    source = WELCOME_CONSENT_TS.read_text(encoding="utf-8")

    # Presence first: the file is the one the sheet imports, and it is not empty.
    assert "export const WELCOME_CONSENT_TEXT" in source
    assert _ts_literal(S2_CONSENT_TEXT) in source
    assert _ts_literal(S2A_DETAILS_TEXT) in source


def test_the_miniapp_names_the_version_the_text_is_recorded_under() -> None:
    source = WELCOME_CONSENT_TS.read_text(encoding="utf-8")

    assert CONSENT_DOCUMENT_VERSION == DATA_STORAGE_REGRANT_DOCUMENT_VERSION == "welcome-s2-v1"
    assert f'WELCOME_CONSENT_DOCUMENT_VERSION = "{CONSENT_DOCUMENT_VERSION}"' in source
