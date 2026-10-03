#!/usr/bin/env python3
"""Refuse a commit that carries personal data in plain text (DRF-1269).

# Why detect-secrets is not enough

Both repositories run ``detect-secrets`` in pre-commit and CI. It looks for
*secrets*: entropy, key shapes, tokens. The measurement of 12.09.2026
(``Ayla/docs/MEASUREMENT_PII_IN_GIT_HISTORY_2026-09-11.md``) found on the
head of ``dev`` a person's phone number, a contractor's e-mail address and
the channel identifiers of six real accounts — in documents, not in code —
and every one of them passed detect-secrets, because none of them is a
secret. Personal data is not high-entropy. It needs its own guard.

# The rule

    Phone numbers, e-mail addresses and channel identifiers of people
    appear in the repository ONLY masked or in test ranges.
    Dumps and logs do not appear at all.

Masks that pass: ``+7 9xx xxx-xx-xx``, ``<имя>@example.org``, ``max:831…``.

# What is checked

1. **Forbidden extensions** — database dumps, logs and spreadsheets never
   belong in git, regardless of content: ``FORBIDDEN_SUFFIXES``.
2. **Russian mobile numbers** outside the test ranges (prefix 900/999,
   repeated digits, ``1234``-runs). The 414 numbers in the catalogue's tests
   and every number in this repository's tests are in those ranges; the one
   number that was not was the owner's.
3. **E-mail addresses** whose domain is not an RFC 2606 reserved domain
   (``example.*``, ``*.test``, ``*.local``, ``*.invalid``) and not one of
   our own service domains.
4. **Known channel identifiers** — the six real MAX accounts named in the
   owner's decisions §12. The guard holds their SHA-256, not the values, so
   the guard itself cannot leak them. Any 7–12 digit run whose hash matches
   is refused.
5. **Unmasked channel handles in documents** — ``max:<digits>`` in
   ``docs/`` must be written ``max:123…``.
6. **Channel identifiers by the form of their carrier** (DRF-2744) — outside
   test files: ``user_id=<digits>``, ``"chat_id": <digits>``,
   ``channel_user_id: <digits>`` and the other names in
   :data:`CHANNEL_ID_KEYS`; «MAX-идентификатор <digits>» / «MAX id <digits>»;
   ``max:<digits>`` outside ``docs/`` as well (code, fixtures, configuration);
   and a stand-alone run of exactly twelve digits that is not a UUID tail, a
   timestamp or a number labelled ``job`` / ``run``. Seven to twelve digits;
   masked (``user_id=260…``) and plainly invented values (``1234567``,
   ``7777777``) pass.

   Measured on the day it was added, rule frozen first: 1181 files of five
   other trees it was not built on carried 141 digit runs of 7–12 digits and
   produced no finding. This tree, before it was masked the same day, produced
   26 (25 outside the allowlist) — every one a real-looking identifier written
   as ``user_id=`` / ``chat_id=``.

# What it deliberately does NOT check

* A channel identifier with words between its name and its digits
  («chat_id владельца <digits>»), and a stand-alone identifier shorter than
  twelve digits with no name next to it. By form those are indistinguishable
  from dates, counters and run numbers — and the real identifiers found on
  the day of the measurement were eight and nine digits long. The form rule
  narrows the hole; it does not close it.
* Names. A name is not a pattern.
* Addresses, birth dates, health facts. Same reason.
* History. This guard runs on the files being committed; a branch created
  before the guard exists still carries what it carried. The measurement
  found 25 such branches — those are cleaned by rewriting history, not by
  a hook.

# Allowlist

``pii_guard_allow.txt`` next to this file: one path prefix per line with a
reason after ``#``. Today's entries are named individually; the list can
only shrink — :data:`ALLOW_CEILING` is the number of entries, held by a
node (DRF-2676): lowering it is free, raising it shows in the diff.
Test directories get test-range phones for free — they do NOT get
real-range phones or real e-mails.

# Positive proof

``tests/tools/test_pii_guard.py`` plants one real-range phone in a temp file
and expects exactly one finding; a test-range phone expects zero. A guard
without that test is ``assert True`` with a long name.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ALLOW_FILE = HERE / "pii_guard_allow.txt"

#: Number of entries in pii_guard_allow.txt — the list may only shrink.
ALLOW_CEILING = 26

FORBIDDEN_SUFFIXES = {
    ".sqlite3",
    ".sqlite",
    ".db",
    ".dump",
    ".bak",
    ".har",
    ".jsonl",
    ".log",
    ".xls",
    ".xlsx",
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".csv",
}

#: Files we never read as text (binary or generated).
SKIP_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ico",
    ".woff",
    ".woff2",
    ".ttf",
    ".pdf",
    ".zip",
    ".pyc",
    ".svg",
    ".lock",
    ".wasm",
    ".mp3",
    ".mp4",
    ".ipynb",
}

PHONE = re.compile(
    r"(?<![\d\w])(?:\+7|8|7)[ (-]?(9\d{2})[ )-]?(\d{3})[ -]?(\d{2})[ -]?(\d{2})(?![\d\w])"
)
EMAIL = re.compile(r"(?<![\w.])([A-Za-z0-9._%+-]+)@([A-Za-z0-9.-]+\.[A-Za-z]{2,})(?![\w])")
DIGIT_RUN = re.compile(r"(?<!\d)\d{7,12}(?!\d)")
CHANNEL_HANDLE_IN_DOCS = re.compile(r"\bmax:(\d{7,})\b")

# --- DRF-2744: идентификатор канала по ФОРМЕ НОСИТЕЛЯ, а не по значению -------
#
# До этого листа сторож узнавал идентификатор человека в канале двумя
# способами: шесть известных — по хешу, остальные — только в виде
# ``max:<цифры>`` и только в ``docs/``. Тот же идентификатор, записанный как
# ``user_id=<цифры>``, ``"chat_id": <цифры>`` или «MAX-идентификатор <цифры>»,
# проходил — и на день замера лежал в дереве в 27 строках.
#
# Правило по значению (ещё один хеш в списке) здесь не годится по устройству:
# SHA-256 числа из 7–12 цифр перебирается за минуты, так что список хешей в
# открытом репозитории сам раскрывает то, что прячет. Поэтому — форма.

#: Имена, которыми в коде, журналах и документах называют идентификатор
#: человека или чата в канале. Закрытый список: слово вне его не судится.
CHANNEL_ID_KEYS = (
    "channel_user_id",
    "max_user_id",
    "ext_user_id",
    "ext_user",
    "user_id",
    "chat_id",
    "sender_id",
    "recipient_id",
)

#: ``<имя><до шести знаков-разделителей><7–12 цифр>``: ``user_id=…``,
#: ``user_id: …``, ``"chat_id": "…"``, ``chat_id = …``. Слова между именем и
#: числом («chat_id владельца …») шаблон НЕ ловит — предел, названный в узлах.
KEYED_CHANNEL_ID = re.compile(
    r"(?i)(?<![A-Za-z0-9_])(?:" + "|".join(CHANNEL_ID_KEYS) + r")(?![A-Za-z0-9_])"
    r"[^\w\n]{0,6}(\d{7,12})(?![\d…])"
)

#: «MAX-идентификатор <цифры>», «MAX id <цифры>», «MAX ID: <цифры>».
NAMED_MAX_ID = re.compile(r"(?i)(?<![\w])MAX[- ]?(?:id|ид\w*)[^\w\n]{0,6}(\d{7,12})(?![\d…])")

#: Ровно двенадцать цифр, стоящих отдельно: не хвост UUID (``-`` слева), не
#: часть шестнадцатеричной строки, пути или десятичной дроби.
BARE_TWELVE_DIGITS = re.compile(r"(?<![\w.\-/:#=])\d{12}(?![\w\-…])")

#: Число, подписанное словом job / run, — номер задания или прогона CI. На день
#: замера все четыре отдельно стоящих двенадцатизначных числа в документах
#: были именно ими («в логе job …»), и ни одно — идентификатором.
_LABELLED_CI_NUMBER = re.compile(r"(?i)(?<![\w])(?:job|run)s?\W{0,4}$")

#: ГГГГММДДЧЧММ — отметка времени, а не идентификатор.
_TIMESTAMP_12 = re.compile(
    r"20\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])(?:[01]\d|2[0-3])[0-5]\d"
)

#: RFC 2606 / RFC 6761 reserved domains and our own operator domains.
ALLOWED_DOMAIN_SUFFIXES = (
    ".example",
    ".test",
    ".local",
    ".invalid",
    ".localhost",
    "example.com",
    "example.org",
    "example.net",
    "example.ru",
    "gobeauty.site",
    "penza.taxi",
    "formulatela.ru",
    "formulatela58.ru",
    "beautygo.ru",
    "anthropic.com",
    "github.com",
    "noreply.github.com",
    "users.noreply.github.com",
    "gserviceaccount.com",
    "iam.gserviceaccount.com",
)
#: Placeholder domains used by redaction tests and design hand-offs.
ALLOWED_DOMAINS_EXACT = {
    "localhost",
    "test.com",
    "host.tld",
    "domain.com",
    "domain.ru",
    "salon.ru",
    "studio.ru",
    "platform.ru",
    "b.co",
    "b.io",
    "x.io",
    "domain.io",
    "evil.com",
    "company.com",
    "sub.example.co",
    "sub.example.co.uk",
    "subdomain.example.co.uk",
}

#: SHA-256 of the six real MAX ids from the owner's decisions §12 (11.09.2026).
#: Hashes, not values: the guard must not be the leak. Regenerate with
#: ``python -c "import hashlib;print(hashlib.sha256(b'<id>').hexdigest())"``.
KNOWN_ID_HASHES = frozenset(
    {
        "20b03aa0c288ea81466dd582a81f0ebda10ef44e463d9e28799c714e12b4543d",  # pragma: allowlist secret
        "2f3dae3bfa039bb025e89618c7f8cfe68b7afbe6e889635d0873a12d67c0d7ca",  # pragma: allowlist secret
        "3501a5e5b6d68c34d1ac73eb0a69b927b15f516dcd5dcb767bea91cdac1cfdb5",  # pragma: allowlist secret
        "64e6bf61c846260ab60e5e1937293d37db26eef59dc30a48950d969e6c842023",  # pragma: allowlist secret
        "7a11a8a4ca320df2e8eeacfb57f86104183ca1e273ccde69394bf38d5eebc4a7",  # pragma: allowlist secret
        "b981a1539bfe2c4e5d54bc24524cd13d0933d9f8928a648736ba1d3de264fb64",  # pragma: allowlist secret
    }
)


def _h(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def is_test_phone(prefix: str, rest: str) -> bool:
    digits = prefix + rest
    if prefix in ("900", "999"):
        return True
    if re.search(r"(\d)\1{3,}", digits):
        return True
    if "1234" in digits or "0000" in rest:
        return True
    # aabbcc-хвост (55-66-77) — фикстурный номер, встречается в пяти тестах
    if re.fullmatch(r"(\d)\1(\d)\2(\d)\3", rest[-6:]):
        return True
    return False


def is_synthetic_id(digits: str) -> bool:
    """Заведомо выдуманный идентификатор — тем же приёмом, что тестовый телефон.

    Четыре одинаковые цифры подряд, счётные ряды ``1234`` / ``4321`` и нули —
    то, чем люди пишут примеры. Настоящий идентификатор сюда попадёт редко, а
    пример в документации перестанет требовать маски.
    """
    if re.search(r"(\d)\1{3,}", digits):
        return True
    return any(run in digits for run in ("1234", "4321", "0000"))


def is_test_path(rel: str) -> bool:
    """Файл тестов: там идентификаторы — фикстуры, и правило формы их не судит.

    Известные реальные идентификаторы (по хешу) ловятся и в тестах — это
    правило от пути не зависит.
    """
    name = rel.rsplit("/", 1)[-1]
    return (
        rel.startswith("tests/")
        or "/tests/" in rel
        or "/__tests__/" in rel
        or name.startswith("test_")
        or name == "conftest.py"
        or ".test." in name
    )


def domain_allowed(domain: str) -> bool:
    d = domain.lower()
    if d in ALLOWED_DOMAINS_EXACT:
        return True
    return any(
        d == s.lstrip(".") or d.endswith(s) or d.endswith("." + s.lstrip("."))
        for s in ALLOWED_DOMAIN_SUFFIXES
    )


def load_allowlist() -> list[tuple[str, str]]:
    if not ALLOW_FILE.exists():
        return []
    out = []
    for line in ALLOW_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        path, _, reason = line.partition("#")
        out.append((path.strip().replace("\\", "/"), reason.strip()))
    return out


def is_allowed(rel: str, allow: list[tuple[str, str]]) -> bool:
    return any(
        rel == p or rel.startswith(p.rstrip("/") + "/") or (p.endswith("/") and rel.startswith(p))
        for p, _ in allow
    )


def scan_text(rel: str, text: str, *, known_hashes: frozenset[str] = KNOWN_ID_HASHES) -> list[str]:
    """Return findings for one file's text. Never includes the matched value."""
    findings: list[str] = []
    judge_form = not is_test_path(rel)
    for lineno, line in enumerate(text.splitlines(), 1):
        for m in PHONE.finditer(line):
            if not is_test_phone(m.group(1), m.group(2) + m.group(3) + m.group(4)):
                findings.append(
                    f"{rel}:{lineno}: телефон вне тестового диапазона (маска: +7 9xx xxx-xx-xx)"
                )
        for m in EMAIL.finditer(line):
            if not domain_allowed(m.group(2)):
                findings.append(
                    f"{rel}:{lineno}: адрес почты на домене {m.group(2).lower()} (маска: <имя>@example.org)"
                )
        for m in DIGIT_RUN.finditer(line):
            if _h(m.group(0)) in known_hashes:
                findings.append(
                    f"{rel}:{lineno}: идентификатор реального аккаунта из §12 (маска: первые 3 цифры + «…»)"
                )
        if rel.startswith("docs/"):
            for m in CHANNEL_HANDLE_IN_DOCS.finditer(line):
                if _h(m.group(1)) not in known_hashes:  # известные уже названы выше
                    findings.append(
                        f"{rel}:{lineno}: незамаскированный идентификатор канала max:<цифры> в документе (маска: max:123…)"
                    )
        if judge_form:
            # DRF-2744 — идентификатор по форме носителя. Известные (по хешу)
            # уже названы выше; заведомо выдуманные не судятся.
            reported: set[int] = set()
            for pattern, shape in (
                (KEYED_CHANNEL_ID, "<имя>=<цифры>"),
                (NAMED_MAX_ID, "«MAX-идентификатор <цифры>»"),
            ):
                for m in pattern.finditer(line):
                    digits = m.group(1)
                    if _h(digits) in known_hashes or is_synthetic_id(digits):
                        continue
                    reported.add(m.start(1))
                    findings.append(
                        f"{rel}:{lineno}: идентификатор канала в форме {shape} "
                        "(маска: первые 3 цифры + «…»)"
                    )
            if not rel.startswith("docs/"):
                # ``max:<цифры>`` вне документов: в ``docs/`` его судит правило
                # выше (строже — без скидки на выдуманные значения), в коде,
                # фикстурах и конфигурации — здесь.
                for m in CHANNEL_HANDLE_IN_DOCS.finditer(line):
                    digits = m.group(1)
                    if _h(digits) in known_hashes or is_synthetic_id(digits):
                        continue
                    reported.add(m.start(1))
                    findings.append(
                        f"{rel}:{lineno}: идентификатор канала max:<цифры> (маска: max:123…)"
                    )
            for m in BARE_TWELVE_DIGITS.finditer(line):
                digits = m.group(0)
                if m.start() in reported or _h(digits) in known_hashes:
                    continue
                if is_synthetic_id(digits) or _TIMESTAMP_12.fullmatch(digits):
                    continue
                if _LABELLED_CI_NUMBER.search(line[: m.start()]):
                    continue
                findings.append(
                    f"{rel}:{lineno}: двенадцать цифр подряд — похоже на идентификатор канала "
                    "(маска: первые 3 цифры + «…»)"
                )
    return findings


def scan_file(path: Path, rel: str, *, known_hashes: frozenset[str] = KNOWN_ID_HASHES) -> list[str]:
    suffix = path.suffix.lower()
    if suffix in FORBIDDEN_SUFFIXES:
        return [f"{rel}: файлы {suffix} в репозиторий не попадают (дампы/логи/выгрузки) — DRF-1269"]
    if suffix in SKIP_SUFFIXES or not path.is_file():
        return []
    data = path.read_bytes()
    if b"\0" in data[:8000]:
        return []
    return scan_text(rel, data.decode("utf-8", "ignore"), known_hashes=known_hashes)


def tracked_files(root: Path) -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True
    ).stdout
    return [p.decode("utf-8", "surrogateescape") for p in out.split(b"\0") if p]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("paths", nargs="*", help="files to check (pre-commit passes the staged ones)")
    ap.add_argument("--all", action="store_true", help="check every tracked file (CI)")
    ap.add_argument("--root", default=None, help="repository root (default: git toplevel)")
    args = ap.parse_args(argv)

    root = (
        Path(args.root).resolve()
        if args.root
        else Path(
            subprocess.run(
                ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True
            ).stdout.strip()
        )
    )
    allow = load_allowlist()
    rels = tracked_files(root) if args.all else [str(Path(p)) for p in args.paths]

    findings: list[str] = []
    checked = 0
    for r in rels:
        rel = Path(r).as_posix()
        if rel.startswith(str(root).replace("\\", "/") + "/"):
            rel = rel[len(str(root).replace("\\", "/")) + 1 :]
        if is_allowed(rel, allow):
            continue
        p = root / rel
        if not p.exists():
            continue
        checked += 1
        findings.extend(scan_file(p, rel))

    if findings:
        print("pii-guard: персональные данные открытым текстом (DRF-1269):", file=sys.stderr)
        for f in findings:
            print("  " + f, file=sys.stderr)
        print(
            f"\n{len(findings)} находок в {checked} проверенных файлах. Маскируйте (см. докстринг), "
            "не вносите дампы; исключение — строка в tools/lint/pii_guard_allow.txt с причиной.",
            file=sys.stderr,
        )
        return 1
    print(f"pii-guard: чисто, проверено файлов: {checked}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
