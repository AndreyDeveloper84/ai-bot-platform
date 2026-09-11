#!/usr/bin/env python3
"""Count objects in the deploy tree that the deploy user does not own.

# The defect

DRF-1646. The pilot deploy tree — `/home/taximeter/ai-bot-platform-dev` on
`ruvds-o1mqo` / 176.119.159.141, the box `api-dev.gobeauty.site` resolves to —
holds **3410 objects the deploy user does not own**, and it cannot overwrite
them: `touch staticfiles/x` as `taximeter` is refused.

**Three** identities write into that one tree, all within the same deploy run
of 2026-09-11 03:24 UTC: `root` (73 files), `taximeter` (16), and uid `1001`
(6) — which has no `passwd` entry on the box at all, being a container uid
arriving through the `./:/app` bind mount. Nobody knew there were three,
because no step names its actor. That is point 1 of the ticket, stated by the
filesystem rather than by argument.

This is not "will bite one day". Among the root-owned objects are git's own
bookkeeping files, including `.git/logs/refs/remotes/origin/measure/…` dated
2026-09-10. The next `git fetch` that needs to update one of those refs as
`taximeter` fails with `Permission denied` **inside a git internal file** —
which reads as a corrupted repository, not as a permissions problem. The
deploy then fails for a reason that points at the wrong thing.

# Two producers, and only one of them is in this repository

Measured 2026-09-11T03:31Z on the pilot box, 12430 objects walked, none
unreadable. Breakdown of the 3410:

    .git/           2206
    other           1029   ← includes ordinary source files:
                           tests/test_admin_humanize.py,
                           tests/support/migration_graph.py,
                           tests/contracts/test_master_address_has_no_readers.py
    staticfiles/     175
    __pycache__        0

The first version of this file carried 3315, measured on **a different
machine** — `ruvds-l2wyz` / 194.87.99.126 holds a checkout at the same path,
with the same contour names, abandoned since 2026-09-03. The path is not an
address: both boxes have it. What settles it is asking the live contour where
it was launched from (`docker inspect ... project.working_dir`) and resolving
the pilot's DNS name — the subject naming itself, rather than being assumed.

**The container producer is the small one.** `docker-compose.staging.yml`
mounts `./:/app`, the image declares no `USER` (the Dockerfile says so
outright: "Production hardening (multi-stage, non-root user, healthcheck)
lands in Sprint 9"), and `.github/workflows/deploy-dev.yml` runs compose under
`sudo`. So everything a container writes inside `/app` lands on the host owned
by root — and `deploy-dev.yml` runs `collectstatic` exactly that way. That
accounts for `staticfiles/`, 174 objects, and it is fixable in this repository.

**The large one is not in this repository at all.** Git bookkeeping plus
working copies of source files means `git` ran as root in this tree, and not
once. No workflow invokes git under elevation — `deploy-dev.yml:88-90` and
`deploy.yml:125-127` both run `fetch`/`pull` as `${DEV_USER}`. A person did
it, repeatedly.

## What this guard therefore does NOT cover — named, because a silent limit
## reads as a closed border

It counts. It cannot attribute, and it cannot prevent. A green run here means
"no new foreign objects appeared since the baseline", not "only one account
writes to this tree". The second producer is a human with a shell, and the
only thing that reaches them is a deploy step that names its actor out loud —
which is a different piece of work (DRF-1646, point 1) in a file this guard
does not touch.

Stating this here rather than in a commit message is deliberate. A guard whose
limit is not written down gets read as proof of the thing it merely samples.

# A ratchet, not a verdict

The baseline starts at the measured 3315 because that is what is there. The
guard fails when the count **grows**; shrinking it is the repair work, and the
baseline is then lowered by hand. There is no "known issues" one-liner: the
breakdown is per category, so a repair that fixes `staticfiles/` and a
regression that adds to `.git/` cannot cancel out into a green total.

# Why the walk and the judgement are separate functions

`classify()` is pure — it takes `(path, uid)` pairs and the expected uid, and
returns the breakdown. `walk()` does the `stat`. That split is not tidiness:
on Windows `os.stat().st_uid` is 0 for everything, so a guard that measured
and judged in one pass could only ever be tested on the box it guards. The
pure half is testable anywhere, including the positive control that matters —
feed it an expected uid that owns nothing and every entry must come back
foreign. Without that control, "0 foreign" is equally consistent with "clean"
and "cannot tell foreign from own".
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

#: Buckets, in the order the report prints them. `OTHER` is last and is not a
#: leftover: it is where source files landed, and it is the bucket whose
#: producer this repository does not explain.
GIT = ".git/"
STATICFILES = "staticfiles/"
PYCACHE = "__pycache__/"
OTHER = "other"

CATEGORIES: tuple[str, ...] = (GIT, STATICFILES, PYCACHE, OTHER)

BASELINE_PATH = Path(__file__).with_name("deploy_tree_ownership_baseline.txt")


@dataclass(frozen=True)
class Entry:
    """One filesystem object and the uid that owns it."""

    path: str
    uid: int


def categorise(path: str) -> str:
    """Which bucket a path belongs to. Pure, and deliberately not clever.

    Matching is on path segments rather than substrings: a directory named
    `staticfiles_old` is not `staticfiles/`, and a source file whose name
    contains `.git` is not git bookkeeping.
    """

    parts = PurePosixPath(path).parts
    if ".git" in parts:
        return GIT
    if "__pycache__" in parts or path.endswith(".pyc"):
        return PYCACHE
    if parts and parts[0] == "staticfiles":
        return STATICFILES
    return OTHER


def classify(entries: list[Entry], *, expected_uid: int) -> dict[str, int]:
    """Count entries NOT owned by `expected_uid`, per category.

    Returns every category, including the ones at zero: a report that omits
    empty buckets cannot be told apart from a report that never looked at them.
    """

    counted: Counter[str] = Counter()
    for entry in entries:
        if entry.uid != expected_uid:
            counted[categorise(entry.path)] += 1
    return {category: counted.get(category, 0) for category in CATEGORIES}


def walk(root: Path) -> list[Entry]:
    """Every object under `root`, with its owning uid, paths relative to root.

    Directories are counted as objects too — a root-owned directory is exactly
    what stops the deploy user creating a file inside it, which is the failure
    this whole thing is about.
    """

    entries: list[Entry] = []
    for dirpath, dirnames, filenames in os.walk(root):
        for name in list(dirnames) + filenames:
            full = Path(dirpath) / name
            try:
                uid = full.lstat().st_uid
            except OSError:
                # Unreadable is not "fine": say so and keep going, so one bad
                # object cannot silence the count for the whole tree.
                print(f"::warning::не удалось прочитать владельца: {full}", file=sys.stderr)
                continue
            entries.append(Entry(path=full.relative_to(root).as_posix(), uid=uid))
    return entries


def read_baseline(path: Path = BASELINE_PATH) -> dict[str, int]:
    """The recorded counts. A missing or unparsable file is an error, not zero.

    Reading an absent baseline as all-zeros would turn "nobody has measured
    this yet" into "the tree is clean" — the loudest possible version of the
    defect this file exists to prevent.
    """

    if not path.exists():
        raise SystemExit(f"baseline не найден: {path}")
    recorded: dict[str, int] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        category, _, count = line.partition("=")
        recorded[category.strip()] = int(count.strip())
    missing = [c for c in CATEGORIES if c not in recorded]
    if missing:
        raise SystemExit(f"в baseline нет категорий: {missing}")
    return recorded


def report(found: dict[str, int], baseline: dict[str, int]) -> tuple[str, bool]:
    """Render the comparison and say whether it is a failure.

    Per category, never on the total: a repair in one bucket and a regression
    in another must not cancel into a green sum.
    """

    lines = [f"{'категория':<16}{'сейчас':>8}{'храповик':>10}{'':>4}"]
    grew: list[str] = []
    for category in CATEGORIES:
        now, was = found[category], baseline[category]
        mark = ""
        if now > was:
            mark = "  ВЫРОСЛО"
            grew.append(f"{category}: {was} → {now}")
        elif now < was:
            mark = "  меньше — опустите храповик"
        lines.append(f"{category:<16}{now:>8}{was:>10}{mark}")
    lines.append(f"{'ВСЕГО':<16}{sum(found.values()):>8}{sum(baseline.values()):>10}")
    if grew:
        lines.append("")
        lines.append("::error::чужих объектов стало больше: " + "; ".join(grew))
        lines.append(
            "Дерево выкладки пишут две учётки. Новый чужой объект — это либо "
            "контейнер под sudo (см. docker-compose.staging.yml: ./:/app), либо "
            "человек, работавший в дереве под root."
        )
    return "\n".join(lines), bool(grew)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="корень дерева выкладки")
    # Обязательный, без умолчания «тот, под кем запущено». Умолчание звучало
    # бы удобно и сравнивало бы дерево пилота с учётной записью того, кто
    # запустил проверку, — на раннере это чужой uid, и сторож объявил бы чужим
    # ВСЁ дерево, зелено отчитавшись о работе. Владельца называют вслух.
    parser.add_argument(
        "--expected-uid",
        type=int,
        required=True,
        help="uid учётки выкладки — того, кто ДОЛЖЕН владеть деревом",
    )
    args = parser.parse_args(argv)

    expected_uid = args.expected_uid
    # Печатаем предмет рядом с результатом: «проверка отработала» и «проверка
    # смотрела на то дерево и на ту учётку» — разные утверждения.
    print(f"дерево: {args.root}   ожидаемый владелец uid={expected_uid}")

    entries = walk(args.root)
    if not entries:
        print(f"::error::под {args.root} не найдено ни одного объекта — мерили не то дерево")
        return 1

    found = classify(entries, expected_uid=expected_uid)
    text, failed = report(found, read_baseline())
    print(f"объектов всего: {len(entries)}")
    print(text)
    return 1 if failed else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
