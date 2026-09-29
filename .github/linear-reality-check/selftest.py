#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Проверка сторожа ПОДМЕНОЙ. Запускается перед каждым живым прогоном.

ЗАЧЕМ ОТДЕЛЬНО ОТ ЖИВОГО ПРОГОНА. Верный код и код, который просто не
сработал, снаружи выглядят одинаково: и тот и другой печатают ноль. Отличить
их можно единственным способом — подменить данные так, чтобы правильный ответ
был заведомо непустым, и посмотреть, покраснеет ли.

ЧТО ПОДМЕНЯЕТСЯ. Двум задачам — DRF-1479 и DRF-1481 — возвращён статус
Backlog, тот, в котором они были 10.09 в 07:52, за минуту до ручного
исправления. Всё остальное в снимке — как в действительности: у DRF-1481
слитый PR #1387 (05.09), у DRF-1485 — #1424 (07.09), у эпика семь закрытых
детей и ни одного своего PR.

Сторож обязан найти обе. Не нашёл — не сторож.

ВТОРАЯ ПОЛОВИНА ПРОВЕРКИ, без которой первая ничего не стоит: тот же сторож
на снимке, где эти же задачи переведены в Done, обязан о них ЗАМОЛЧАТЬ.
Правило, которое срабатывает всегда, — не правило, а совпадение. Файлы
`linear_unrecorded.json` и `linear_recorded.json` отличаются РОВНО статусами
трёх задач, и это здесь тоже проверяется: иначе «замолчал» можно было бы
получить любой другой правкой.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import reality_check as rc  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(HERE, "fixtures")
NOW = datetime(2026, 9, 10, 12, 0, 0, tzinfo=timezone.utc)
DAYS = 5

FLIPPED = ("DRF-1479", "DRF-1481", "DRF-1485")

failures: list[str] = []
checks = 0


def check(condition: bool, what: str, detail: str = "") -> None:
    global checks
    checks += 1
    if condition:
        print(f"  ok   {what}" + (f" — {detail}" if detail else ""))
    else:
        print(f"  FAIL {what}" + (f" — {detail}" if detail else ""))
        failures.append(what)


def load(name: str):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as handle:
        return json.load(handle)


def run(linear_name: str):
    issues = load(linear_name)
    raw = load("github.json")
    prs, other_base = raw["prs"], raw["other_base"]
    probe = next(
        (
            {
                "identifier": i["identifier"],
                "state": {"name": i["state_name"], "type": i["state_type"]},
            }
            for i in issues
            if i["identifier"] == "DRF-1481"
        ),
        None,
    )
    return rc.analyse(issues, prs, other_base, probe, NOW, DAYS)


def ids(rows):
    return {row["identifier"] for row in rows}


def sub_of(rows, identifier):
    return next((row["sub"] for row in rows if row["identifier"] == identifier), None)


# --- 0. сторож читает и только читает ---------------------------------------

print("0. Сторож не может писать в Linear")
with open(os.path.join(HERE, "reality_check.py"), encoding="utf-8") as handle:
    source = handle.read()
# Ищем не слово (оно законно стоит в комментариях про «ни одной mutation»),
# а СИНТАКСИС операции: `mutation` перед именем, скобкой или телом.
written = re.findall(r"\bmutation\s*[\w({]", source, re.IGNORECASE)
check(
    not written,
    "в коде сторожа нет ни одной GraphQL-мутации: он физически не может двинуть статус",
    f"найдено операций записи: {len(written)}",
)

# --- 1. правило места: где номер задачи считается ----------------------------

print("\n1. Где PR называет свою задачу — правило места")
by_number = {pr["number"]: pr for pr in load("github.json")["prs"]}
check(
    rc.ids_from_pr(by_number[1387]) == {"DRF-1481"},
    "#1387 → ровно DRF-1481",
    f"получено {sorted(rc.ids_from_pr(by_number[1387]))}",
)
check(
    rc.ids_from_pr(by_number[1424]) == {"DRF-1485"},
    "#1424 → ровно DRF-1485",
    f"получено {sorted(rc.ids_from_pr(by_number[1424]))}",
)
check(
    rc.ids_from_pr(by_number[1499]) == set(),
    "#1499 не связывается ни с чем: DRF-1479 в его теле — образец, а не предмет",
    f"получено {sorted(rc.ids_from_pr(by_number[1499]))}",
)
check(
    rc.ids_from_pr(by_number[1523]) == {"DRF-1602"},
    "#1523 связывается строкой `Задача:` при пустом на номера заголовке",
    f"получено {sorted(rc.ids_from_pr(by_number[1523]))}",
)

# --- 2. подмена: задачи возвращены в Backlog --------------------------------

print("\n2. ПОДМЕНА: DRF-1479 и DRF-1481 возвращены в Backlog — сторож обязан краснеть")
findings, controls = run("linear_unrecorded.json")

check(
    "DRF-1479" in ids(findings[1]),
    "вопрос 1 нашёл DRF-1479 (эпик без своего PR)",
    f"признак {sub_of(findings[1], 'DRF-1479')}",
)
check(
    "DRF-1481" in ids(findings[1]),
    "вопрос 1 нашёл DRF-1481 (исполнен PR #1387, не переведён)",
    f"признак {sub_of(findings[1], 'DRF-1481')}",
)
check(
    sub_of(findings[1], "DRF-1479") == "1b",
    "DRF-1479 найден именно признаком 1b — по закрытым детям, а не по PR",
)
check(sub_of(findings[1], "DRF-1481") == "1a+1b", "DRF-1481 найден обоими признаками сразу")
check(
    ids(findings[1]) == {"DRF-1479", "DRF-1481", "DRF-1485", "DRF-1602"},
    "вопрос 1 — ровно ожидаемое множество, ничего лишнего",
    f"получено {sorted(ids(findings[1]))}",
)
check(
    ids(findings[2]) == {"DRF-1479", "DRF-1481", "DRF-1601"},
    "вопрос 2 — только Urgent/High в Backlog старше порога",
    f"получено {sorted(ids(findings[2]))}",
)
check(
    "DRF-1600" not in ids(findings[2]), "вопрос 2 не берёт вчерашнюю срочную задачу: порог — порог"
)
check(
    ids(findings[3]) == {"DRF-1571"},
    "вопрос 3 — только задача с ОТКРЫТЫМ блокером",
    f"получено {sorted(ids(findings[3]))}",
)
check("DRF-1570" not in ids(findings[3]), "вопрос 3 не берёт задачу, у которой блокер закрыт")
check(
    ids(findings[4]) == {"DRF-1481"},
    "вопрос 4 — только Urgent/High с оценкой и без исполнителя",
    f"получено {sorted(ids(findings[4]))}",
)
check(
    all(c["ok"] for c in controls),
    "все положительные контроли прошли на подменённых данных",
    f"{sum(1 for c in controls if c['ok'])}/{len(controls)}",
)

# --- 3. обратная сторона: правило обязано уметь молчать ----------------------

print("\n3. ДЕЙСТВИТЕЛЬНОСТЬ: те же задачи переведены в Done — сторож обязан замолчать")
findings_ok, controls_ok = run("linear_recorded.json")

for identifier in FLIPPED:
    check(
        identifier not in ids(findings_ok[1]),
        f"вопрос 1 молчит про {identifier}, когда статус записан",
    )
check(
    ids(findings_ok[1]) == {"DRF-1602"},
    "вопрос 1 сузился ровно на три переведённые задачи",
    f"получено {sorted(ids(findings_ok[1]))}",
)
check(
    ids(findings_ok[2]) == {"DRF-1601"},
    "вопрос 2 сузился до одной",
    f"получено {sorted(ids(findings_ok[2]))}",
)
check(ids(findings_ok[3]) == {"DRF-1571"}, "вопрос 3 не изменился — подмена его не касалась")
check(findings_ok[4] == [], "вопрос 4 стал пустым")
check(
    all(c["ok"] for c in controls_ok),
    "и при ПУСТОМ ответе вопроса 4 его положительный контроль прошёл",
    next(c["evidence"] for c in controls_ok if c["q"] == 4),
)

# --- 4. подмена минимальна ---------------------------------------------------

print("\n4. Подмена минимальна: снимки отличаются ровно статусами трёх задач")
unrecorded = {i["identifier"]: i for i in load("linear_unrecorded.json")}
recorded = {i["identifier"]: i for i in load("linear_recorded.json")}
check(set(unrecorded) == set(recorded), "состав задач в обоих снимках одинаков")
mutable = {"state_name", "state_type", "state_since", "updated_at", "_"}
diverged = set()
for identifier, left in unrecorded.items():
    right = recorded[identifier]
    for field in set(left) | set(right):
        if field in mutable:
            continue
        if left.get(field) != right.get(field):
            diverged.add((identifier, field))
check(
    not diverged, "вне полей статуса снимки совпадают побайтово", f"расхождения: {sorted(diverged)}"
)
changed = {i for i in unrecorded if unrecorded[i]["state_type"] != recorded[i]["state_type"]}
check(changed == set(FLIPPED), "статус изменён ровно у трёх задач", f"изменены: {sorted(changed)}")

# --- 5. сломанный сторож обязан падать --------------------------------------

print("\n5. Сломанный сторож падает громко, а не молчит")


class _Args:
    days = DAYS
    linear_snapshot = os.path.join(FIXTURES, "empty.json")
    github_snapshot = os.path.join(FIXTURES, "github.json")
    now = "2026-09-10T12:00:00+00:00"
    dump_snapshot = None
    out_md = None
    out_json = None


empty_path = os.path.join(FIXTURES, "empty.json")
with open(empty_path, "w", encoding="utf-8") as handle:
    json.dump([], handle)
try:
    code = rc.main(
        [
            "--linear-snapshot",
            empty_path,
            "--github-snapshot",
            os.path.join(FIXTURES, "github.json"),
            "--now",
            "2026-09-10T12:00:00+00:00",
        ]
    )
finally:
    os.remove(empty_path)
check(code == 2, "пустая выборка Linear даёт код выхода 2, а не тихий ноль", f"код {code}")

# --- итог -------------------------------------------------------------------

print()
if failures:
    print(f"ПРОВЕРКА ПОДМЕНОЙ НЕ ПРОЙДЕНА: {len(failures)} из {checks} — " + "; ".join(failures))
    raise SystemExit(1)
print(f"Проверка подменой пройдена: {checks} утверждений, ни одного провала.")
print(
    "Сторож краснеет на подменённых данных и молчит на записанных — "
    "значит, его зелёный что-то значит."
)
