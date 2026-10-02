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


# Синтетические идентификаторы, собранные из частей: сплошной цифровой ряд в
# дереве не лежит, и «выдуманными» по правилу сторожа они не считаются (нет
# четырёх одинаковых цифр подряд, нет 1234 / 4321 / 0000) — как настоящие.
_ID9 = "58" + "20" + "69" + "137"
_ID12 = "584" + "206" + "913" + "775"

#: Каждая форма носителя из листа DRF-2744 — и ещё три, найденные в дереве в
#: день замера (JSON с кавычками и без, запись через «=» с пробелами).
_CARRIER_FORMS = [
    ("user_id: {id}", _ID9),
    ("user_id={id}", _ID9),
    ("channel_user_id={id}", _ID9),
    ('"channel_user_id": "{id}"', _ID9),
    ("recipient.chat_id = {id}", _ID9),
    ('{{"chat_type": "dialog", "chat_id": {id}}}', _ID9),
    ("POST botapi.max.ru/messages?chat_id={id} → 404", _ID9),
    ("ext_user={id}", _ID9),
    ("MAX-идентификатор {id}", _ID9),
    ("MAX id {id}", _ID9),
    ("MAX ID: {id}", _ID12),
    ("пользователь {id} написал боту", _ID12),
]

#: Безобидные числа тех родов, что встретились на ОТЛОЖЕННЫХ деревьях (каталог,
#: handoffs, architecture-prompts, ayla-knowledge, beautygo-mobile — 1181 файл,
#: 141 цифровой ряд длиной 7–12, ни одного срабатывания; на них правило не
#: настраивалось). Здесь — по одному представителю рода, собранному из частей.
_BENIGN = [
    "correlation_id: c4d5e6f7-a8b9-0123-cdef-" + "456" + "789" + "012" + "345",  # хвост UUID
    "выгрузка от " + "2026" + "0915" + "1730",  # ГГГГММДДЧЧММ
    "настоящий вывод git в логе job " + "104" + "882" + "615" + "937",  # номер задания CI
    "run " + "344" + "827" + "444" + "36" + ", SHA e0640f3",  # номер прогона CI
    "timestamp: " + "17313" + "20000",  # unix-время, 10 цифр
    "дата " + "2026" + "09" + "15",  # 8 цифр
    "цена " + "1" + "250" + "000" + " ₽ за курс",  # 7 цифр
    "строк в журнале: " + "8" + "675" + "309",  # счётчик
    "user_id человека берётся из initData",  # имя без числа
    "user_id=260…",  # маска
    "chat_id=<id>",  # заполнитель
    "tenant_id=" + "58" + "20" + "69" + "137",  # имя вне закрытого списка
]


class TestChannelIdForms:
    """DRF-2744 — идентификатор канала ловится по форме носителя, а не только как ``max:<цифры>``.

    До листа сторож видел идентификатор человека в канале двумя способами:
    шесть известных — по хешу, остальные — только в виде ``max:<цифры>`` и
    только в ``docs/``. Тот же идентификатор в виде ``user_id=<цифры>`` или
    ``"chat_id": <цифры>`` проходил — и в день замера лежал в дереве.
    """

    @pytest.mark.parametrize(("form", "ident"), _CARRIER_FORMS)
    @pytest.mark.parametrize("rel", ["docs/report.md", "apps/channels/max/outbound.py"])
    def test_each_carrier_form_is_exactly_one_finding(self, rel, form, ident):
        text = "строка до\n" + form.format(id=ident) + "\nстрока после\n"

        findings = g.scan_text(rel, text)

        assert len(findings) == 1, findings
        assert findings[0].startswith(f"{rel}:2:")
        assert ident not in findings[0], "сторож не должен печатать значение"

    @pytest.mark.parametrize("line", _BENIGN)
    def test_benign_numbers_of_the_held_out_kinds_pass(self, line):
        # сначала присутствие: тот же вызов на идентификаторе находку ДАЁТ —
        # иначе «пусто» ниже верно и для выключенного правила
        assert len(g.scan_text("docs/report.md", "user_id=" + _ID9)) == 1
        assert len(g.scan_text("apps/x.py", "user_id=" + _ID9)) == 1
        assert g.scan_text("docs/report.md", line) == [], line
        assert g.scan_text("apps/x.py", line) == [], line

    def test_the_benign_list_is_seen_by_the_patterns(self):
        """Положительная стража: молчание выше — решение правила, а не слепота шаблона."""
        uuid_tail, stamp, job = _BENIGN[0], _BENIGN[1], _BENIGN[2]
        # отметка времени и номер задания шаблон ВИДИТ и отпускает по правилу
        assert g.BARE_TWELVE_DIGITS.search(stamp) is not None
        assert g.BARE_TWELVE_DIGITS.search(job) is not None
        # хвост UUID не виден по построению: слева дефис
        assert g.BARE_TWELVE_DIGITS.search(uuid_tail) is None
        # то же число задания без подписи — уже находка
        unlabelled = job.replace("в логе job ", "в логе ")
        assert len(g.scan_text("docs/report.md", unlabelled)) == 1

    def test_masked_and_synthetic_ids_pass(self):
        seen = g.KEYED_CHANNEL_ID.search("user_id=" + "1234567")
        assert seen is not None, "шаблон обязан видеть выдуманный идентификатор"
        # присутствие: та же форма с невыдуманным значением — находка
        assert len(g.scan_text("docs/x.md", "user_id=" + _ID9)) == 1
        assert g.scan_text("docs/x.md", "user_id=" + "1234567") == []
        assert g.scan_text("docs/x.md", "chat_id: " + "7" * 9) == []
        assert g.scan_text("docs/x.md", "channel_user_id=" + _ID9[:3] + "…") == []

    def test_tests_are_not_judged_by_form(self):
        """В тестах идентификаторы — фикстуры; по хешу известные ловятся и там."""
        line = "channel_user_id=" + _ID9
        assert len(g.scan_text("apps/x.py", line)) == 1
        for rel in (
            "apps/channels/tests/test_handler.py",
            "tests/tools/test_x.py",
            "apps/miniapp/src/App.test.tsx",
            "apps/identity/conftest.py",
        ):
            assert g.is_test_path(rel), rel
            assert g.scan_text(rel, line) == [], rel
        hashes = frozenset({hashlib.sha256(_ID9.encode()).hexdigest()})
        known = g.scan_text("apps/channels/tests/test_handler.py", line, known_hashes=hashes)
        assert len(known) == 1 and "§12" in known[0]

    def test_a_known_id_is_named_once_not_twice(self):
        hashes = frozenset({hashlib.sha256(_ID9.encode()).hexdigest()})
        findings = g.scan_text("docs/x.md", "user_id=" + _ID9, known_hashes=hashes)
        assert len(findings) == 1 and "§12" in findings[0]

    def test_the_key_list_is_the_decided_one(self):
        """Закрытый список литералом: новое имя — правка здесь, а не побочный эффект."""
        assert set(g.CHANNEL_ID_KEYS) == {
            "channel_user_id",
            "max_user_id",
            "ext_user_id",
            "ext_user",
            "user_id",
            "chat_id",
            "sender_id",
            "recipient_id",
        }

    def test_what_the_form_rule_does_not_see_is_named(self):
        """Пределы правила, названные узлом, — чтобы их не приняли за покрытие.

        1. Слова между именем и числом: «chat_id владельца <цифры>».
        2. Отдельно стоящее число короче двенадцати цифр без имени рядом: такие
           ряды в дереве — даты, счётчики, номера прогонов; по форме их от
           идентификатора не отличить, а настоящие идентификаторы в день замера
           были как раз восьми- и девятизначными.
        """
        assert g.DIGIT_RUN.search(_ID9) is not None
        assert g.scan_text("docs/x.md", "chat_id владельца на пилоте " + _ID9) == []
        assert g.scan_text("docs/x.md", "написал человек " + _ID9 + " вчера") == []


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

    def test_the_list_may_only_shrink(self):
        """Raising the ceiling must show in the diff; removing an entry lowers it (DRF-2676)."""
        entries = g.load_allowlist()
        assert len(entries) == g.ALLOW_CEILING, (
            f"в списке {len(entries)} строк, потолок {g.ALLOW_CEILING}: новое исключение — "
            "это решение, и его видно только если потолок поднят в том же диффе; "
            "убранная строка — опустите потолок"
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
