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
from apps.skills.base import CLAIM_EVIDENCE_SOURCES, SkillResult, claims_done_of


def _evidence_values(rel: str) -> list[str]:
    """Значения подтверждений одного файла — с раскрытыми константами.

    Часть веток собирает подтверждение из константы модуля
    (``f"{CLAIM_EVIDENCE_BOOKING_CREATE}:record_id"``), и читать это
    выражение как текст значило бы проверять кавычки вместо договора.
    Поэтому константы РАЗВОРАЧИВАЮТСЯ через настоящий импорт модуля:
    переименуют константу — узел упадёт, а не притворится зелёным.

    Пустые подтверждения пропускаются: они заявлены отдельным узлом.
    """
    import importlib

    root = Path(__file__).resolve().parents[3]
    module = importlib.import_module(rel[:-3].replace("/", "."))
    src = (root / rel).read_text(encoding="utf-8")
    out: list[str] = []
    for match in re.finditer(r'claims_done_evidence=(f?)"([^"]*)"', src):
        is_f, raw = match.group(1), match.group(2)
        if not raw:
            continue
        if is_f:
            raw = re.sub(
                r"\{([A-Z_]+)\}",
                lambda m: str(getattr(module, m.group(1))),
                raw,
            )
            assert "{" not in raw, f"{rel}: не развернулось — {match.group(2)}"
        out.append(raw)
    return out


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
        "apps/bookings/callbacks.py",
    )

    #: Ветки, у которых подтверждение ПУСТО — сознательно, с причиной в коде.
    #: Это не послабление узла, а его предмет: пустое подтверждение обязано
    #: быть решением, а не опечаткой, и каждое такое место названо листом.
    DECLARED_WITHOUT_EVIDENCE = {
        "apps/bookings/callbacks.py": 2,  # DRF-2337 (отмена «по возможности»), DRF-2344
    }

    def test_every_claim_here_declares_evidence(self) -> None:
        """У каждого утверждения рядом стоит подтверждение — пусть и пустое."""
        root = Path(__file__).resolve().parents[3]
        seen = 0
        for rel in self.MARKED:
            src = (root / rel).read_text(encoding="utf-8")
            for match in re.finditer(r"claims_done=True,", src):
                seen += 1
                tail = src[match.end() : match.end() + 200]
                assert "claims_done_evidence=" in tail, f"{rel}: подтверждение не объявлено"
        assert seen >= 15, seen

    def test_only_the_declared_defects_have_empty_evidence(self) -> None:
        """Пустых подтверждений ровно столько, сколько объявлено листами.

        Появится новое пустое — узел покраснеет, и это верно: пустое
        подтверждение значит «утверждаем, доказать нечем», то есть четвёртый
        дефект класса, о котором надо сказать сразу.
        """
        root = Path(__file__).resolve().parents[3]
        for rel in self.MARKED:
            src = (root / rel).read_text(encoding="utf-8")
            empty = len(re.findall(r'claims_done_evidence="",', src))
            assert empty == self.DECLARED_WITHOUT_EVIDENCE.get(rel, 0), f"{rel}: пустых {empty}"

    def test_the_evidence_names_a_source_and_what_it_answered(self) -> None:
        """Форма ``источник:что ответил`` — не украшение, а предмет.

        Без источника подтверждение нельзя отличить от нашей же выдумки:
        именно так дефект и маскируется (DRF-2337 — вызов состоялся и ушёл
        в никуда).
        """
        for rel in self.MARKED:
            for evidence in _evidence_values(rel):
                assert ":" in evidence, f"{rel}: {evidence} — нет части «что ответил»"
                source = evidence.split(":", 1)[0]
                assert "." in source, f"{rel}: {evidence} — источник не назван ручкой"


class TestTheSourceListIsAContract:
    """Префикс подтверждения — договор, а не украшение (DRF-2341).

    Признак булев, поэтому «сделал сам» и «передал другому» различает
    только источник в подтверждении. Список закрыт и ведётся руками —
    значит незнакомый префикс обязан КРАСНИТЬ: молчаливое принятие
    превратило бы договор в украшение за одну правку.
    """

    def test_every_evidence_in_the_bot_names_a_listed_source(self) -> None:
        seen = 0
        for rel in TestTheMarkedBranchesNameTheirEvidence.MARKED:
            for evidence in _evidence_values(rel):
                seen += 1
                source = evidence.split(".", 1)[0]
                assert source in CLAIM_EVIDENCE_SOURCES, f"{rel}: незнакомый источник {source!r}"
        assert seen >= 14, seen

    def test_an_unknown_source_would_be_caught(self) -> None:
        """Сторож стражи: выдуманный источник в списке не числится.

        Без этого узла предыдущий был бы зелёным и на пустом множестве —
        и на списке, в который однажды просто всё добавили.
        """
        assert "выдуманный_источник" not in CLAIM_EVIDENCE_SOURCES
        assert CLAIM_EVIDENCE_SOURCES, "положительная пара: список не пуст"

    def test_the_prefixes_used_by_the_booking_gate_are_listed(self) -> None:
        """Ворота записи называют роль исполнителя, а не систему.

        Под флагом действует Ayla, без него YClients, и выбор делается на
        ходу: ``catalogue`` — единственный честный префикс для этой ветки.
        """
        from apps.bookings.callbacks import (
            CLAIM_EVIDENCE_BOOKING_CANCEL,
            CLAIM_EVIDENCE_BOOKING_CREATE,
            CLAIM_EVIDENCE_BOOKING_RESCHEDULE,
        )

        for prefix in (
            CLAIM_EVIDENCE_BOOKING_CREATE,
            CLAIM_EVIDENCE_BOOKING_CANCEL,
            CLAIM_EVIDENCE_BOOKING_RESCHEDULE,
        ):
            assert prefix.split(".", 1)[0] in CLAIM_EVIDENCE_SOURCES, prefix
