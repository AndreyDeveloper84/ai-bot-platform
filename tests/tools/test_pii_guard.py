"""Positive proof for tools/lint/pii_guard.py (DRF-1269).

A guard that was never seen going red is ``assert True`` with a long name.
Each test plants ONE synthetic violation and expects EXACTLY one finding —
not «at least one» — so an over-eager pattern (which would flag the
surrounding prose as well) fails here too. The values planted are built at
test time from digits/letters, never copied from a real person.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT / "tools" / "lint"))
import pii_guard as g  # type: ignore[import-not-found]  # noqa: E402

# Real-range number assembled from parts so the literal never sits in the
# tree as a phone: prefix 9-1-2 is a live operator range, tail is irregular.
_REAL_RANGE_PHONE = "+7 " + "912" + " 384-" + "71-" + "26"
_TEST_RANGE_PHONE = "+7 900 123-45-67"
_PAIRS_FIXTURE_PHONE = "+7 916 555-66-77"


def _scan(text: str, rel: str = "docs/x.md") -> list[str]:
    return g.scan_text(rel, text)


class TestPhones:
    def test_real_range_phone_is_exactly_one_finding(self):
        text = (
            "Решение владельца: его телефон — "
            + _REAL_RANGE_PHONE
            + ".\nДругая строка без номера.\n"
        )
        findings = _scan(text)
        assert len(findings) == 1, findings
        assert "телефон" in findings[0]
        assert _REAL_RANGE_PHONE not in findings[0], "сторож не должен печатать значение"

    def test_test_range_and_masked_pass(self):
        # положительная стража: шаблон ВИДИТ оба номера — молчание сторожа ниже не от слепоты
        assert g.PHONE.search(_TEST_RANGE_PHONE) is not None
        assert g.PHONE.search(_PAIRS_FIXTURE_PHONE) is not None
        assert _scan("тестовый " + _TEST_RANGE_PHONE) == []
        assert _scan("фикстура " + _PAIRS_FIXTURE_PHONE) == []
        assert _scan("маска +7 9xx xxx-xx-xx") == []
        assert _scan("повтор +7 911 111-11-11") == []


class TestEmails:
    def test_public_domain_is_one_finding(self):
        # адрес собирается из частей — иначе сторож поймает сам тест
        addr = "someone.real@" + "gma" + "il.com"
        findings = _scan("ассайни: " + addr + " — средний")
        assert len(findings) == 1 and "gmail.com" in findings[0]

    def test_reserved_and_own_domains_pass(self):
        for addr in (
            "test@example.com",
            "ops@gobeauty.site",
            "x@stg.penza.taxi",
            "a@b.test",
            "noreply@anthropic.com",
        ):
            assert g.EMAIL.search(addr) is not None, addr  # шаблон видит адрес
            assert _scan("адрес " + addr) == [], addr

    def test_cyrillic_mask_is_outside_the_pattern_by_design(self):
        # «<имя>@example.org» шаблон не считает адресом вовсе — маска и не должна
        # выглядеть адресом; сторож молчит не потому, что домен разрешён.
        assert g.EMAIL.search("<имя>@example.org") is None
        assert (
            _scan("адрес <имя>@example.org") == []
        )  # empty-assert-ok: маска вне шаблона намеренно


class TestChannelIds:
    def test_known_id_is_caught_by_hash_only(self):
        fake_id = "12345678"
        hashes = frozenset({hashlib.sha256(fake_id.encode()).hexdigest()})
        findings = g.scan_text("apps/x.py", "user_id = " + fake_id, known_hashes=hashes)
        assert len(findings) == 1 and "§12" in findings[0]
        assert fake_id not in findings[0]
        # без хэша тот же текст чист — значений в стороже нет
        assert g.DIGIT_RUN.search(fake_id) is not None  # цифровой ряд на месте
        assert g.scan_text("apps/x.py", "user_id = " + fake_id, known_hashes=frozenset()) == []

    def test_unmasked_handle_in_docs_only(self):
        assert len(_scan("клиент max:26071234567", rel="docs/report.md")) == 1
        assert _scan("клиент max:260…", rel="docs/report.md") == []
        # в коде голая ручка не проверяется — только в docs/
        assert g.scan_text("apps/x.py", "handle = 'max:26071234567'") == []


class TestFiles:
    def test_forbidden_extension_is_one_finding(self, tmp_path: Path):
        dump = tmp_path / "db.sqlite3"
        dump.write_bytes(b"SQLite format 3\0" + b"\0" * 64)
        findings = g.scan_file(dump, "db.sqlite3")
        assert len(findings) == 1 and ".sqlite3" in findings[0]

    def test_binary_and_clean_text_pass(self, tmp_path: Path):
        img = tmp_path / "x.bin"
        img.write_bytes(b"\0\1\2" + _REAL_RANGE_PHONE.encode())
        assert img.read_bytes()  # файл не пуст
        assert g.scan_file(img, "x.bin") == []
        doc = tmp_path / "note.md"
        doc.write_text("ничего личного, только 2026-09-12 и DRF-1269\n", encoding="utf-8")
        assert doc.read_text(encoding="utf-8")  # файл не пуст
        assert g.scan_file(doc, "note.md") == []


class TestAllowlist:
    def test_every_allow_entry_names_a_reason_and_an_existing_path(self):
        entries = g.load_allowlist()
        assert entries, "аллоулист пуст — либо файл пропал, либо его стёрли"
        for path, reason in entries:
            assert reason, f"{path}: исключение обязано называть причину"
            assert (_PROJECT_ROOT / path).exists(), (
                f"{path}: исключению нечего прикрывать — путь исчез, удалите строку"
            )

    def test_allow_prefix_semantics(self):
        allow = [("legacy_maxbot/", "frozen"), ("docs/catalog/MARKET_PENZA.md", "public")]
        assert g.is_allowed("legacy_maxbot/handlers/x.py", allow)
        assert g.is_allowed("docs/catalog/MARKET_PENZA.md", allow)
        assert not g.is_allowed("docs/catalog/MARKET_PENZA.md.bak", allow)
        assert not g.is_allowed("legacy_maxbot_new/x.py", allow)


@pytest.mark.slow
def test_whole_tree_is_clean():
    """CI runs ``--all``; this pins the same contract from pytest so a regression
    shows up in the test job even if the CI step is ever dropped."""
    assert g.main(["--all", "--root", str(_PROJECT_ROOT)]) == 0
