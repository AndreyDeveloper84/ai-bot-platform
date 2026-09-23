"""DRF-2341 шаг 3 — разметка «утверждаю выполненное» в ветках питания и памяти.

Форма признака задана в #2012 и здесь только применяется: ``claims_done``
булев, ``claims_done_evidence`` — короткое машинное слово вида
``источник:что он ответил``. Читатель один на весь бот —
:func:`apps.skills.base.claims_done_of`; своего здесь нет и быть не должно.

Разметка ничего не меняет в поведении: тексты, кнопки и ходы те же. Эти
узлы сторожат именно это — что объявление не стало правкой, и что
подтверждение названо там, где оно есть.
"""

from __future__ import annotations

import re
from pathlib import Path

from apps.persona.memory_commands import MemoryCommandResult
from apps.skills.base import SkillResult, claims_done_of


class TestOneReaderTwoCarriers:
    def test_the_field_and_the_meta_key_read_the_same(self) -> None:
        """Носителя два, и оба настоящие.

        Поле — у наших классов ответа (``SkillResult``,
        ``MemoryCommandResult``); ключ ``meta`` — там, где объекта-ответа
        нет вовсе. Читатель обязан давать один ответ с обоих, иначе сторож
        класса увидит половину веток и промолчит про вторую.
        """
        by_field = SkillResult(
            reply_text="Записала 250 мл 💧",
            claims_done=True,
            claims_done_evidence="ayla.water.add:entry_id",
        )
        by_meta = SkillResult(
            reply_text="Записала 250 мл 💧",
            meta={"claims_done": True, "claims_done_evidence": "ayla.water.add:entry_id"},
        )

        assert claims_done_of(by_field) == (True, "ayla.water.add:entry_id")
        assert claims_done_of(by_field) == claims_done_of(by_meta)

    def test_our_memory_result_carries_it_in_the_same_shape(self) -> None:
        """У ``MemoryCommandResult`` ``meta`` нет — носитель поле, форма та же."""
        erased = MemoryCommandResult(
            text="Забыла всё.",
            claims_done=True,
            claims_done_evidence="bridge.erase:erased",
        )

        assert claims_done_of(erased) == (True, "bridge.erase:erased")

    def test_a_reply_that_claims_nothing_reads_as_nothing(self) -> None:
        """Положительная пара к пустоте: не всё подряд считается утверждением."""
        assert claims_done_of(SkillResult(reply_text="Что ешь сейчас?")) == (False, "")
        assert claims_done_of(MemoryCommandResult(text="Вот что помню.")) == (False, "")


class TestTheMarkedBranchesNameTheirEvidence:
    """Размеченные ветки питания и памяти подтверждение называют — все до одной.

    Предел узла назван: он читает ИСХОДНИК, потому что вызвать здесь каждую
    ветку нельзя. Пустое подтверждение в коде — не опечатка, а заявление о
    дефекте (так объявлена отмена «по возможности» в #2012), и делаться оно
    должно сознательно; в ветках питания и памяти его сегодня нет.
    """

    MARKED = (
        "apps/skills/water/skill.py",
        "apps/skills/food_clarify/text_entry.py",
        "apps/skills/food_scanner/skill.py",
        "apps/skills/nutrition_anketa/skill.py",
        "apps/skills/welcome/skill.py",
        "apps/persona/memory_commands.py",
    )

    def test_every_claim_here_carries_evidence(self) -> None:
        root = Path(__file__).resolve().parents[3]
        seen = 0
        for rel in self.MARKED:
            src = (root / rel).read_text(encoding="utf-8")
            for match in re.finditer(r"claims_done=True,", src):
                seen += 1
                tail = src[match.end() : match.end() + 200]
                assert re.search(r'claims_done_evidence="[^"]+"', tail), f"{rel}: без подтверждения"
        assert seen >= 11, seen

    def test_the_evidence_names_a_source_and_what_it_answered(self) -> None:
        """Форма ``источник:что ответил`` — не украшение, а предмет.

        Без источника подтверждение нельзя отличить от нашей же выдумки:
        именно так дефект и маскируется (DRF-2337 — вызов состоялся и ушёл
        в никуда).
        """
        root = Path(__file__).resolve().parents[3]
        for rel in self.MARKED:
            src = (root / rel).read_text(encoding="utf-8")
            for match in re.finditer(r'claims_done_evidence="([^"]+)"', src):
                evidence = match.group(1)
                assert ":" in evidence, f"{rel}: {evidence} — нет части «что ответил»"
                source = evidence.split(":", 1)[0]
                assert "." in source, f"{rel}: {evidence} — источник не назван ручкой"
