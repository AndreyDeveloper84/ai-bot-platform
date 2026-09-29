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

# What it deliberately does NOT check

* Names. A name is not a pattern.
* Addresses, birth dates, health facts. Same reason.
* History. This guard runs on the files being committed; a branch created
  before the guard exists still carries what it carried. The measurement
  found 25 such branches — those are cleaned by rewriting history, not by
  a hook.

# Allowlist

``pii_guard_allow.txt`` next to this file: one path prefix per line with a
reason after ``#``. Today's entries are named individually; the list can
only shrink. Test directories get test-range phones for free — they do NOT
get real-range phones or real e-mails.

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
