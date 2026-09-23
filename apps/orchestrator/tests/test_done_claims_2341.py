"""DRF-2341 шаг 3 — разметка «утверждаю выполненное» читается одинаково.

Признак живёт на двух носителях: поле у нашего класса ответа и ключ
``meta`` там, где поля нет. Читатель обязан быть ОДИН и давать один и тот
же ответ с обоих — иначе сторож, который на нём вырастет, будет видеть
половину веток и молчать про вторую.

Разметка ничего не меняет в поведении: тексты, кнопки и ходы те же. Эти
узлы сторожат именно это — что объявление не стало правкой.
"""

from __future__ import annotations

from apps.orchestrator.done_claims import EVIDENCE_KEY, KIND_KEY, DoneClaim, done_claim
from apps.persona.memory_commands import MemoryCommandResult
from apps.skills.base import SkillResult


class TestOneReaderTwoCarriers:
    def test_the_field_and_the_meta_key_read_the_same(self) -> None:
        """Носителя два, и оба настоящие.

        ``meta`` — у ответов навыков: его шов переносит целиком, а отдельное
        поле умерло бы на шве молча (сторож шва, DRF-1419, это и ловит).
        Поле — у ``MemoryCommandResult``, у которого ``meta`` нет вовсе и
        который через шов не ходит.
        """
        by_field = MemoryCommandResult(
            text="Записала 250 мл 💧",
            claims_done="water_logged",
            claims_done_evidence="ayla.entry_id",
        )
        by_meta = SkillResult(
            reply_text="Записала 250 мл 💧",
            meta={KIND_KEY: "water_logged", EVIDENCE_KEY: "ayla.entry_id"},
        )

        assert done_claim(by_field) == DoneClaim("water_logged", "ayla.entry_id")
        assert done_claim(by_field) == done_claim(by_meta)

    def test_a_reply_that_claims_nothing_reads_as_nothing(self) -> None:
        """Положительная пара к пустоте: не всё подряд считается утверждением."""
        assert done_claim(SkillResult(reply_text="Что ешь сейчас?")) is None
        assert done_claim(SkillResult(reply_text="…", meta={"reply_kind": "x"})) is None

    def test_our_own_result_class_carries_it_too(self) -> None:
        """Память — наш класс ответа, носитель тот же."""
        erased = MemoryCommandResult(
            text="Забыла всё.",
            claims_done="memory_erased",
            claims_done_evidence="bridge.erase_outcome",
        )

        assert done_claim(erased) == DoneClaim("memory_erased", "bridge.erase_outcome")

    def test_a_claim_without_evidence_is_readable_not_hidden(self) -> None:
        """Ветка утверждает и доказать не может — это видно, а не проглочено.

        Сегодня таких три (DRF-2337, DRF-2338, DRF-2344), и сторож должен
        будет их найти. Читатель не подставляет доказательство и не роняет
        ход: он отдаёт утверждение с пустым доказательством.
        """
        claim = done_claim(
            SkillResult(reply_text="Подтверждено", meta={KIND_KEY: "visit_confirmed"})
        )

        assert claim is not None
        assert claim.kind == "visit_confirmed"
        assert claim.evidence == ""


class TestTheMarkedBranchesAgree:
    def test_every_mark_in_the_bot_names_its_evidence(self) -> None:
        """Размеченные ветки бота доказательство называют — все до одной.

        Предел узла назван: он читает ИСХОДНИК, потому что вызвать каждую
        ветку здесь нельзя. Пустое доказательство в коде — не опечатка, а
        заявление о дефекте, и такое заявление должно делаться сознательно;
        сегодня в размеченных ветках его нет.
        """
        import re
        from pathlib import Path

        apps = Path(__file__).resolve().parents[2]
        marked = 0
        for path in apps.rglob("*.py"):
            if "tests" in path.parts or "migrations" in path.parts:
                continue
            src = path.read_text(encoding="utf-8")
            for match in re.finditer(r'"?claims_done"?[=:] ?"([^"]+)"', src):
                marked += 1
                tail = src[match.end() : match.end() + 400]
                assert "claims_done_evidence" in tail, f"{path.name}: {match.group(1)}"
        assert marked >= 10, marked
