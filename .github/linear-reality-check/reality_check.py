#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Сторож расхождения Linear с действительностью.

ЗАЧЕМ. 04.09.2026 заведён эпик DRF-1479 «Client Surface Reconciliation», Urgent.
10.09 главное окно доложило владельцу «пакет не начинался — шесть дней ноль
движения», посмотрев на статус эпика. Было наоборот: шесть детей из семи
закрыты, 21 SP сделан, а седьмой исполнен и не переведён — его код влит 05.09
(PR #1387) и 07.09 (PR #1424).

Работа была сделана. Не записана. Это опаснее забытой задачи: бэклог, полный
сделанного, выглядит горой работы, и по нему нельзя планировать. Именно так
главное окно шесть раз подряд раздавало исполнителям предметы, закрытые днями
раньше.

ЧЕГО ЭТОТ ФАЙЛ НЕ ДЕЛАЕТ. Не меняет статусы в Linear. Только читает и
докладывает: автоматическое закрытие задач — отдельное решение владельца,
которого нет. Все запросы к Linear здесь `query`, ни одной `mutation`.

ЧЕТЫРЕ ВОПРОСА, каждый числом и списком:

  1  РАБОТА БЕЗ ЗАПИСИ     задача не закрыта, а её код уже в dev
  2  РАБОТА БЕЗ ДВИЖЕНИЯ   Urgent/High в Backlog без смены состояния > N дней
  3  НАРУШЕННЫЙ ПОРЯДОК    задача взята в работу, а её блокер ещё открыт
  4  БЕСХОЗНОЕ             есть estimate, нет assignee, > N дней, Urgent/High

ПОЛОЖИТЕЛЬНЫЙ КОНТРОЛЬ НА КАЖДОМ ПУСТОМ ОТВЕТЕ. `grep -c` на упавшей команде
возвращает 0, неотличимый от «искали и не нашли». Главное окно 10.09 дважды на
этом обожглось и один раз завело задачу с неверным утверждением. Поэтому у
каждого вопроса есть контроль: тот же путь данных обязан показать заведомо
существующее. Контроль не прошёл — прогон падает, и падает громко. Сторож,
который молчит потому, что сломался, хуже отсутствующего: он создаёт ложное
спокойствие.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

# --- граница предмета -------------------------------------------------------

TEAM_KEY = "DRF"
PROJECT_NAME = "Ayla"
BASE_BRANCH = "dev"
REPO_OWNER = "AndreyDeveloper84"
REPO_NAME = "ai-bot-platform"

# Типы состояний Linear. `duplicate` и `canceled` — осознанные закрытия, а не
# болезнь: задачу не забыли, её сняли. Открытыми считаются остальные.
CLOSED_TYPES = frozenset({"completed", "canceled", "duplicate"})

PRIORITY_LABEL = {0: "No priority", 1: "Urgent", 2: "High", 3: "Medium", 4: "Low"}
HOT_PRIORITIES = frozenset({1, 2})  # Urgent и High

LINEAR_ENDPOINT = "https://api.linear.app/graphql"

# --- как PR называет свою задачу --------------------------------------------
#
# РАЗНЫЕ МЕСТА — РАЗНЫЕ ПРАВИЛА, и это не педантизм, а замер.
#
# В ЗАГОЛОВКЕ засчитывается любое `DRF-NNNN`. Заголовок PR — его собственный
# предмет по построению; сослаться в нём на чужую задачу как на пример нельзя.
# Замер по 732 слитым в `dev` PR: заголовок несёт номер у 290, и это основная
# форма дома — `feat(...): ... (DRF-1477)` либо `DRF-1481: ...`.
#
# В ТЕЛЕ засчитываются только привязанные к месту формы. Правило куплено живым
# случаем, найденным до включения сторожа ссылки: PR про бэкап ПИЛОТА ссылался
# на `DRF-1578` как на ОБРАЗЕЦ («на боевой тропе этот шаг есть с DRF-1578»).
# Свободное вхождение в теле сдвинуло бы чужую задачу.
#
# Тот же класс ошибки живёт в нашем корпусе прямо сейчас: тело PR #1424 пишет
# «Эпик DRF-1479, пакет §24, шаг 8» — это контекст, а не предмет. Тело шести
# слитых PR упоминает DRF-1479, и ни один из них не является работой по эпику.
# Правило места отбрасывает все шесть — а сам эпик сторож находит другим
# признаком (см. question_1, ветка 1b).
TITLE_ID_RE = re.compile(r"\bDRF-(\d+)\b", re.IGNORECASE)
BODY_LINK_RE = re.compile(
    r"(?:^|\s)(?:Задача:|Task:|Closes|Closed|Close|Fixes|Fixed|Fix|Resolves|Resolved|Resolve)"
    r"\s+#?\s*DRF-(\d+)\b",
    re.IGNORECASE | re.MULTILINE,
)
# Заголовок раздела в теле — тоже предмет, а не ссылка: `## DRF-1485 — ...`.
BODY_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s*\[?DRF-(\d+)\b", re.IGNORECASE | re.MULTILINE)
# Ссылка на PR в вложении Linear (когда интеграция подключена).
ATTACH_PR_RE = re.compile(r"github\.com/([^/\s]+)/([^/\s]+)/pull/(\d+)", re.IGNORECASE)


class WatchmanBroken(RuntimeError):
    """Сторож не смог посмотреть. Это не «чисто», это «сломан»."""


# --- Linear -----------------------------------------------------------------

LINEAR_ISSUES_QUERY = """
query($after: String, $teamKey: String!, $project: String!) {
  issues(
    first: 50
    after: $after
    filter: { team: { key: { eq: $teamKey } }, project: { name: { eq: $project } } }
  ) {
    pageInfo { hasNextPage endCursor }
    nodes {
      identifier
      title
      url
      priority
      estimate
      createdAt
      updatedAt
      state { name type }
      assignee { name }
      parent { identifier }
      children(first: 50) { nodes { identifier state { type } } }
      attachments(first: 25) { nodes { url } }
      inverseRelations(first: 25) {
        nodes { type issue { identifier title state { type } } }
      }
      stateHistory(last: 1) { nodes { startedAt state { name } } }
    }
  }
}
"""

# Точечный запрос для положительного контроля вопроса 1. Он идёт ОТДЕЛЬНЫМ
# запросом, не из общей выборки: иначе контроль подтверждал бы сам себя.
LINEAR_PROBE_QUERY = """
query($id: String!) { issue(id: $id) { identifier state { name type } } }
"""


def linear_call(token: str, query: str, variables: dict) -> dict:
    payload = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    request = urllib.request.Request(
        LINEAR_ENDPOINT,
        data=payload,
        headers={"Content-Type": "application/json", "Authorization": token},
    )
    try:
        raw = urllib.request.urlopen(request, timeout=60).read()
    except urllib.error.HTTPError as exc:
        # Ключ уходит заголовком и в тело ответа не попадает; всё равно
        # печатаем только код и обрезанное начало тела.
        raise WatchmanBroken(
            f"Linear ответил HTTP {exc.code}; начало тела: {exc.read()[:200]!r}"
        ) from None
    except Exception as exc:  # noqa: BLE001
        raise WatchmanBroken(f"Linear недоступен: {type(exc).__name__}") from None
    body = json.loads(raw)
    if body.get("errors"):
        messages = "; ".join(e.get("message", "?") for e in body["errors"])
        raise WatchmanBroken(f"Linear вернул ошибки: {messages}")
    return body["data"]


def normalize_issue(node: dict) -> dict:
    state = node.get("state") or {}
    spans = (node.get("stateHistory") or {}).get("nodes") or []
    state_since = spans[-1]["startedAt"] if spans else node.get("createdAt")
    blockers = [
        {
            "identifier": rel["issue"]["identifier"],
            "title": rel["issue"].get("title") or "",
            "state_type": (rel["issue"].get("state") or {}).get("type") or "unknown",
        }
        for rel in ((node.get("inverseRelations") or {}).get("nodes") or [])
        if rel.get("type") == "blocks" and rel.get("issue")
    ]
    return {
        "identifier": node["identifier"],
        "title": node.get("title") or "",
        "url": node.get("url") or "",
        "priority": node.get("priority") if node.get("priority") is not None else 0,
        "estimate": node.get("estimate"),
        "created_at": node.get("createdAt"),
        "updated_at": node.get("updatedAt"),
        "state_name": state.get("name") or "?",
        "state_type": state.get("type") or "unknown",
        "assignee": (node.get("assignee") or {}).get("name"),
        "parent": (node.get("parent") or {}).get("identifier"),
        "children": [
            {
                "identifier": child["identifier"],
                "state_type": (child.get("state") or {}).get("type") or "unknown",
            }
            for child in ((node.get("children") or {}).get("nodes") or [])
        ],
        "attachment_urls": [
            a.get("url") or "" for a in ((node.get("attachments") or {}).get("nodes") or [])
        ],
        "blocked_by": blockers,
        "state_since": state_since,
    }


def fetch_linear_issues(token: str) -> list[dict]:
    after, out, pages = None, [], 0
    while True:
        data = linear_call(
            token,
            LINEAR_ISSUES_QUERY,
            {"after": after, "teamKey": TEAM_KEY, "project": PROJECT_NAME},
        )
        page = data["issues"]
        out.extend(normalize_issue(node) for node in page["nodes"])
        pages += 1
        if not page["pageInfo"]["hasNextPage"]:
            break
        after = page["pageInfo"]["endCursor"]
        if pages > 200:
            raise WatchmanBroken("Linear: свыше 200 страниц — похоже на цикл пагинации.")
    return out


def probe_linear_issue(token: str, identifier: str) -> dict | None:
    return linear_call(token, LINEAR_PROBE_QUERY, {"id": identifier}).get("issue")


# --- GitHub -----------------------------------------------------------------

GH_QUERY = """
query($owner: String!, $name: String!, $after: String) {
  repository(owner: $owner, name: $name) {
    pullRequests(states: MERGED, first: 100, after: $after,
                 orderBy: { field: CREATED_AT, direction: DESC }) {
      pageInfo { hasNextPage endCursor }
      nodes { number title body mergedAt baseRefName url }
    }
  }
}
"""


def gh_call(query: str, variables: dict) -> dict:
    args = ["gh", "api", "graphql", "-f", "query=" + query]
    for key, value in variables.items():
        if value is None:
            continue
        args += ["-f", f"{key}={value}"]
    proc = subprocess.run(args, capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0:
        raise WatchmanBroken(
            f"gh api graphql вернул {proc.returncode}: {(proc.stderr or '')[:300]}"
        )
    body = json.loads(proc.stdout)
    if body.get("errors"):
        messages = "; ".join(e.get("message", "?") for e in body["errors"])
        raise WatchmanBroken(f"GitHub вернул ошибки: {messages}")
    return body["data"]


def fetch_merged_prs() -> tuple[list[dict], int]:
    """Слитые PR. Возвращает (слитые в BASE_BRANCH, отброшено по другой базе).

    СОДЕРЖИМЫМ, А НЕ ПРЕДКАМИ. Проверять «код в dev» через
    `git merge-base --is-ancestor` здесь нельзя: дом сливает squash'ем, и
    коммиты ветки после слияния предками `dev` не являются — ancestry соврала
    бы «не влито» на каждой второй задаче. Устойчивый к squash признак — флаг
    самого GitHub `state: MERGED`, который ставится при слиянии независимо от
    того, как переписана история.
    """
    after, out, other_base, pages = None, [], 0, 0
    while True:
        data = gh_call(GH_QUERY, {"owner": REPO_OWNER, "name": REPO_NAME, "after": after})
        page = data["repository"]["pullRequests"]
        for node in page["nodes"]:
            if node.get("baseRefName") == BASE_BRANCH:
                out.append(node)
            else:
                other_base += 1
        pages += 1
        if not page["pageInfo"]["hasNextPage"]:
            break
        after = page["pageInfo"]["endCursor"]
        if pages >= 60:
            raise WatchmanBroken(
                "GitHub: 60 страниц слитых PR и конца нет — выборка не полна, "
                "а неполная выборка молча занижает ответ на вопрос 1."
            )
    return out, other_base


def ids_from_pr(pr: dict) -> set[str]:
    found = set()
    for match in TITLE_ID_RE.finditer(pr.get("title") or ""):
        found.add("DRF-" + match.group(1))
    body = pr.get("body") or ""
    for regex in (BODY_LINK_RE, BODY_HEADING_RE):
        for match in regex.finditer(body):
            found.add("DRF-" + match.group(1))
    return found


def build_pr_index(prs: list[dict]) -> dict[str, list[dict]]:
    index: dict[str, list[dict]] = {}
    for pr in prs:
        for identifier in ids_from_pr(pr):
            index.setdefault(identifier, []).append(
                {
                    "number": pr["number"],
                    "title": pr.get("title") or "",
                    "merged_at": pr.get("mergedAt"),
                    "url": pr.get("url") or "",
                }
            )
    for entries in index.values():
        entries.sort(key=lambda entry: entry["number"])
    return index


def prs_from_attachments(issue: dict, merged_numbers: set[int]) -> list[dict]:
    """PR, привязанные к задаче вложением Linear, — если интеграция включена.

    Вложение засчитывается только когда PR действительно слит: открытый PR в
    вложении означает «работа идёт», а не «работа сделана и не записана».
    """
    out = []
    for url in issue["attachment_urls"]:
        match = ATTACH_PR_RE.search(url or "")
        if not match:
            continue
        if match.group(1).lower() != REPO_OWNER.lower():
            continue
        if match.group(2).lower() != REPO_NAME.lower():
            continue
        number = int(match.group(3))
        if number in merged_numbers:
            out.append(
                {"number": number, "title": "(вложение Linear)", "merged_at": None, "url": url}
            )
    return out


# --- время ------------------------------------------------------------------


def parse_ts(value):
    if not value:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def days_since(value, now: datetime):
    stamp = parse_ts(value)
    if stamp is None:
        return None
    return (now - stamp).total_seconds() / 86400.0


def is_open(issue: dict) -> bool:
    return issue["state_type"] not in CLOSED_TYPES


def _num(identifier: str) -> int:
    try:
        return int(identifier.split("-")[1])
    except (IndexError, ValueError):
        return 0


# --- четыре вопроса ---------------------------------------------------------


def question_1(issues, pr_index, merged_numbers):
    """РАБОТА БЕЗ ЗАПИСИ.

    Два разных признака под одним именем: наружу одно число, внутрь —
    раздельные счётчики, потому что лечатся они по-разному.

    1a. У открытой задачи есть слитый PR. Прямой случай DRF-1481: PR #1387
        влит 05.09, задача осталась открытой.
    1b. У открытой задачи есть дети, и ВСЕ они закрыты. Случай самого эпика
        DRF-1479: собственного PR у него нет и быть не может — эпик не пишут
        кодом, — но шесть детей из семи были закрыты, а он висел в Backlog.
        Без этого признака сторож не увидел бы ровно ту задачу, из-за которой
        он существует; замер по корпусу: заголовок ни одного слитого PR не
        содержит DRF-1479, а шесть тел упоминают его как контекст.
    """
    rows = []
    for issue in issues:
        if not is_open(issue):
            continue
        prs = list(pr_index.get(issue["identifier"], []))
        known = {pr["number"] for pr in prs}
        for attached in prs_from_attachments(issue, merged_numbers):
            if attached["number"] not in known:
                prs.append(attached)
        children = issue["children"]
        children_all_closed = bool(children) and all(
            child["state_type"] in CLOSED_TYPES for child in children
        )
        if not prs and not children_all_closed:
            continue
        reasons, sub = [], []
        if prs:
            sub.append("1a")
            numbers = ", ".join(f"#{pr['number']}" for pr in sorted(prs, key=lambda p: p["number"]))
            reasons.append(f"слитый PR {numbers}")
        if children_all_closed:
            sub.append("1b")
            reasons.append(f"все дети закрыты ({len(children)}/{len(children)})")
        rows.append(
            {
                "identifier": issue["identifier"],
                "state": issue["state_name"],
                "title": issue["title"],
                "url": issue["url"],
                "detail": "; ".join(reasons),
                "sub": "+".join(sub),
            }
        )
    rows.sort(key=lambda row: -_num(row["identifier"]))
    return rows


def question_2(issues, now, days):
    """РАБОТА БЕЗ ДВИЖЕНИЯ: Urgent/High лежит в Backlog дольше порога.

    Отсчёт — от входа в ТЕКУЩЕЕ состояние (`stateHistory`), а не от
    `updatedAt`. `updatedAt` сдвигает любой комментарий и любая правка
    описания: задача, о которой неделю спорят и ничего не делают, по
    `updatedAt` выглядит свежей — поле, которым удобно мерить, измеряет ровно
    не то.
    """
    rows = []
    for issue in issues:
        if issue["state_type"] != "backlog" or issue["priority"] not in HOT_PRIORITIES:
            continue
        age = days_since(issue["state_since"], now)
        if age is None or age <= days:
            continue
        rows.append(
            {
                "identifier": issue["identifier"],
                "state": issue["state_name"],
                "title": issue["title"],
                "url": issue["url"],
                "detail": f"{PRIORITY_LABEL.get(issue['priority'], '?')}, в Backlog {age:.0f} дн.",
                "age": age,
            }
        )
    rows.sort(key=lambda row: -row["age"])
    return rows


def question_3(issues):
    """НАРУШЕННЫЙ ПОРЯДОК: задача взята в работу, а её блокер ещё открыт.

    `started` — это и `In Progress`, и `In Review`: обе означают «за предмет
    взялись». Блокер берётся из `inverseRelations` с типом `blocks`: если A
    блокирует B, то у B это ребро приходит именно так.
    """
    rows = []
    for issue in issues:
        if issue["state_type"] != "started":
            continue
        open_blockers = [
            blocker for blocker in issue["blocked_by"] if blocker["state_type"] not in CLOSED_TYPES
        ]
        if not open_blockers:
            continue
        rows.append(
            {
                "identifier": issue["identifier"],
                "state": issue["state_name"],
                "title": issue["title"],
                "url": issue["url"],
                "detail": "открытые блокеры: "
                + ", ".join(f"{b['identifier']} ({b['state_type']})" for b in open_blockers),
            }
        )
    rows.sort(key=lambda row: -_num(row["identifier"]))
    return rows


def question_4(issues, now, days):
    """БЕСХОЗНОЕ: оценка есть, исполнителя нет, задача не свежая, приоритет высок.

    Возраст считается от `createdAt`, а не от `updatedAt`: по `updatedAt`
    задача, которую регулярно трогают и никому не отдают, не состарится
    никогда — сторож про неё просто забудет.
    """
    rows = []
    for issue in issues:
        if not is_open(issue):
            continue
        if issue["estimate"] is None or issue["assignee"] is not None:
            continue
        if issue["priority"] not in HOT_PRIORITIES:
            continue
        age = days_since(issue["created_at"], now)
        if age is None or age <= days:
            continue
        rows.append(
            {
                "identifier": issue["identifier"],
                "state": issue["state_name"],
                "title": issue["title"],
                "url": issue["url"],
                "detail": (
                    f"{PRIORITY_LABEL.get(issue['priority'], '?')}, "
                    f"{issue['estimate']:g} SP, без исполнителя {age:.0f} дн."
                ),
                "age": age,
            }
        )
    rows.sort(key=lambda row: -row["age"])
    return rows


# --- положительный контроль -------------------------------------------------

# Пары «PR — задача», существование которых установлено вручную и не зависит
# от того, что сторож посчитает. #1387 влит 05.09 и озаглавлен «DRF-1481: ...»;
# #1424 влит 07.09 и озаглавлен «... (DRF-1485)». Если индекс их не находит —
# сломан путь данных, а не бэклог чист.
KNOWN_PR_PAIRS = ((1387, "DRF-1481"), (1424, "DRF-1485"))


def build_controls(issues, pr_index, prs, other_base, probe, days):
    """Каждому вопросу — свидетельство, что тот же путь данных видит живое.

    Контроль отвечает не «правильно ли посчитано», а «посчитано ли вообще».
    Пустой список без контроля читается как «чисто», а означать может что
    угодно, вплоть до упавшего запроса.
    """
    open_issues = [i for i in issues if is_open(i)]
    closed_issues = [i for i in issues if not is_open(i)]
    hot = [i for i in issues if i["priority"] in HOT_PRIORITIES]
    with_estimate = [i for i in issues if i["estimate"] is not None]
    with_assignee = [i for i in issues if i["assignee"] is not None]
    started = [i for i in issues if i["state_type"] == "started"]
    backlog = [i for i in issues if i["state_type"] == "backlog"]
    with_state_since = [i for i in issues if i["state_since"]]
    blocks_edges = sum(len(i["blocked_by"]) for i in issues)
    carrying_id = sum(1 for pr in prs if ids_from_pr(pr))
    pair_ok = [
        any(entry["number"] == number for entry in pr_index.get(identifier, []))
        for number, identifier in KNOWN_PR_PAIRS
    ]
    probe_state = (probe or {}).get("state") or {}

    return [
        {
            "q": 1,
            "name": "индекс PR→задача не пуст и находит заведомо известные пары",
            "ok": bool(pr_index) and all(pair_ok),
            "evidence": (
                f"слитых PR в `{BASE_BRANCH}`: {len(prs)} "
                f"(ещё {other_base} слито в другие базы и в счёт не идёт); "
                f"из них несут номер задачи: {carrying_id}; "
                f"различных задач в индексе: {len(pr_index)}; "
                + "; ".join(
                    f"#{number}→{identifier}: {'найдена' if ok else 'НЕ НАЙДЕНА'}"
                    for (number, identifier), ok in zip(KNOWN_PR_PAIRS, pair_ok)
                )
            ),
        },
        {
            "q": 1,
            "name": "живой Linear отвечает на точечный запрос про DRF-1481",
            "ok": bool(probe) and probe.get("identifier") == "DRF-1481",
            "evidence": (
                "отдельным запросом, не из общей выборки: DRF-1481 → "
                f"{probe_state.get('name', '—')} ({probe_state.get('type', '—')}); "
                f"задач в выборке: {len(issues)}, открытых {len(open_issues)}, "
                f"закрытых {len(closed_issues)}"
            ),
        },
        {
            "q": 2,
            "name": "приоритет и время входа в состояние прочитаны, а не просто непусты",
            "ok": bool(hot) and bool(with_state_since) and bool(backlog),
            "evidence": (
                f"Urgent/High в выборке: {len(hot)}; известно время входа в текущее "
                f"состояние у {len(with_state_since)} задач; в Backlog сейчас: "
                f"{len(backlog)}; порог {days} дн."
            ),
        },
        {
            "q": 3,
            "name": "связи `blocks` вообще доезжают из Linear",
            "ok": blocks_edges > 0,
            "evidence": (
                f"рёбер blockedBy в выборке: {blocks_edges}; задач в состоянии started: "
                f"{len(started)}; из них с любыми блокерами: "
                f"{sum(1 for i in started if i['blocked_by'])}"
            ),
        },
        {
            "q": 4,
            "name": "estimate и assignee читаются как значения, а не как признак заполненности",
            "ok": bool(with_estimate) and bool(with_assignee),
            "evidence": (
                f"задач с estimate: {len(with_estimate)}; с исполнителем: "
                f"{len(with_assignee)}; открытых без исполнителя: "
                f"{sum(1 for i in open_issues if i['assignee'] is None)}"
            ),
        },
    ]


# --- отчёт ------------------------------------------------------------------

QUESTIONS = (
    (1, "РАБОТА БЕЗ ЗАПИСИ", "задача не закрыта, а её код уже в " + BASE_BRANCH),
    (2, "РАБОТА БЕЗ ДВИЖЕНИЯ", "Urgent/High в Backlog без смены состояния"),
    (3, "НАРУШЕННЫЙ ПОРЯДОК", "задача в работе, а её блокер ещё открыт"),
    (4, "БЕСХОЗНОЕ", "есть estimate, нет assignee, приоритет высок"),
)
ROW_CAP = 40


def render(findings, controls, meta) -> str:
    lines = ["# Сторож расхождения Linear с действительностью", ""]
    lines.append(
        f"Снимок {meta['now']} · команда `{TEAM_KEY}` · проект `{PROJECT_NAME}` · "
        f"база `{BASE_BRANCH}` · порог {meta['days']} дн."
    )
    lines += ["", "| # | вопрос | найдено |", "|---|--------|---------|"]
    for number, name, _ in QUESTIONS:
        lines.append(f"| {number} | {name} | **{len(findings[number])}** |")
    lines += [
        "",
        "Сторож ничего не меняет в Linear: только чтение и этот отчёт. "
        "Автоматическое закрытие задач — отдельное решение владельца, его нет.",
        "",
    ]

    for number, name, gloss in QUESTIONS:
        rows = findings[number]
        lines += [f"## {number} · {name} — {len(rows)}", "", f"_{gloss}._", ""]
        if rows:
            lines += [
                "| задача | статус | что видно | заголовок |",
                "|--------|--------|-----------|-----------|",
            ]
            for row in rows[:ROW_CAP]:
                title = (row["title"] or "").replace("|", "\\|")
                if len(title) > 90:
                    title = title[:87] + "…"
                link = f"[{row['identifier']}]({row['url']})" if row["url"] else row["identifier"]
                lines.append(f"| {link} | {row['state']} | {row['detail']} | {title} |")
            if len(rows) > ROW_CAP:
                lines.append(
                    f"| … | | ещё {len(rows) - ROW_CAP}; полный список — в артефакте прогона | |"
                )
        else:
            lines.append("Пусто.")
        lines.append("")
        suffix = " (ответ пуст — тем более обязателен)" if not rows else ""
        lines += [f"**Положительный контроль**{suffix}:", ""]
        for control in [c for c in controls if c["q"] == number]:
            mark = "OK" if control["ok"] else "СЛОМАН"
            lines.append(f"- `{mark}` {control['name']} — {control['evidence']}")
        lines.append("")

    broken = [c for c in controls if not c["ok"]]
    lines += ["## Итог прогона", ""]
    if broken:
        lines.append(
            "**Прогон КРАСНЫЙ: сторож сломан.** Не прошли контроли: "
            + "; ".join(f"вопрос {c['q']} — {c['name']}" for c in broken)
            + ". Числа выше читать нельзя: пустой ответ сломанного сторожа "
            "неотличим от чистого бэклога."
        )
    else:
        total = sum(len(findings[number]) for number, _, _ in QUESTIONS)
        lines += [
            f"Прогон ЗЕЛЁНЫЙ: прошли все {len(controls)} контроля — сторож смотрел. "
            f"Расхождений найдено: **{total}**.",
            "",
            "Непустой список сам по себе прогон не роняет, и это выбор, а не упущение. "
            "Красный по чужому бэклогу каждое утро перестают читать через неделю — "
            "ровно так умерла панель здоровья с 2018 неудачными проверками подряд. "
            "Красным здесь становится одно: сторож не смог посмотреть.",
        ]
    lines.append("")
    return "\n".join(lines)


# --- сборка -----------------------------------------------------------------


def read_token() -> str:
    """Ключ приходит извне и нигде не печатается — ни в логах, ни в отчёте."""
    token = os.environ.get("LINEAR_API_KEY") or os.environ.get("LINEAR_API_TOKEN")
    if token and token.strip():
        return token.strip()
    local = os.path.expanduser("~/.claude.json")
    if os.path.exists(local):
        try:
            with open(local, encoding="utf-8") as handle:
                token = json.load(handle)["mcpServers"]["linear"]["env"]["LINEAR_API_TOKEN"]
            if token and token.strip():
                return token.strip()
        except Exception:  # noqa: BLE001
            pass
    raise WatchmanBroken(
        "Нет ключа Linear. В Actions он приходит секретом репозитория LINEAR_API_KEY; "
        "локально — из ~/.claude.json."
    )


def analyse(issues, prs, other_base, probe, now, days):
    pr_index = build_pr_index(prs)
    merged_numbers = {pr["number"] for pr in prs}
    findings = {
        1: question_1(issues, pr_index, merged_numbers),
        2: question_2(issues, now, days),
        3: question_3(issues),
        4: question_4(issues, now, days),
    }
    controls = build_controls(issues, pr_index, prs, other_base, probe, days)
    return findings, controls


def load_snapshots(args):
    if args.linear_snapshot:
        with open(args.linear_snapshot, encoding="utf-8") as handle:
            issues = json.load(handle)
        probe = next(
            (
                {
                    "identifier": issue["identifier"],
                    "state": {"name": issue["state_name"], "type": issue["state_type"]},
                }
                for issue in issues
                if issue["identifier"] == "DRF-1481"
            ),
            None,
        )
    else:
        token = read_token()
        issues = fetch_linear_issues(token)
        probe = probe_linear_issue(token, "DRF-1481")

    if args.github_snapshot:
        with open(args.github_snapshot, encoding="utf-8") as handle:
            raw = json.load(handle)
        prs, other_base = raw["prs"], raw.get("other_base", 0)
    else:
        prs, other_base = fetch_merged_prs()
    return issues, probe, prs, other_base


def emit(args, text, payload):
    if args.out_md:
        os.makedirs(os.path.dirname(os.path.abspath(args.out_md)), exist_ok=True)
        with open(args.out_md, "w", encoding="utf-8") as handle:
            handle.write(text)
    if args.out_json:
        os.makedirs(os.path.dirname(os.path.abspath(args.out_json)), exist_ok=True)
        with open(args.out_json, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=1)
    sys.stdout.write(text + "\n")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Сторож расхождения Linear с действительностью")
    parser.add_argument("--days", type=int, default=5, help="порог застоя в днях")
    parser.add_argument("--linear-snapshot", help="читать задачи из файла, а не из Linear")
    parser.add_argument("--github-snapshot", help="читать слитые PR из файла, а не из GitHub")
    parser.add_argument("--now", help="точка отсчёта ISO-8601 для воспроизводимых проверок")
    parser.add_argument("--dump-snapshot", help="каталог: сохранить снимок обеих сторон")
    parser.add_argument("--out-md", help="куда положить отчёт (по умолчанию только stdout)")
    parser.add_argument("--out-json", help="куда положить машинный результат")
    args = parser.parse_args(argv)

    now = parse_ts(args.now) if args.now else datetime.now(timezone.utc)

    try:
        issues, probe, prs, other_base = load_snapshots(args)
        if not issues:
            raise WatchmanBroken(
                f"Из Linear пришло НОЛЬ задач по команде {TEAM_KEY} / проекту {PROJECT_NAME}. "
                "Пустая выборка — это не пустой бэклог, а неотвеченный вопрос."
            )
        if not prs:
            raise WatchmanBroken(
                f"Из GitHub пришло НОЛЬ слитых PR в `{BASE_BRANCH}`. "
                "Вопрос 1 на такой выборке всегда даст ноль, и этот ноль ничего не значит."
            )
        findings, controls = analyse(issues, prs, other_base, probe, now, args.days)
    except WatchmanBroken as exc:
        emit(
            args,
            "# Сторож расхождения Linear с действительностью\n\n"
            f"**Прогон КРАСНЫЙ: сторож не смог посмотреть.**\n\n{exc}\n\n"
            "Это не «расхождений нет». Это «ответа нет».\n",
            {"broken": True, "reason": str(exc)},
        )
        return 2

    if args.dump_snapshot:
        os.makedirs(args.dump_snapshot, exist_ok=True)
        with open(os.path.join(args.dump_snapshot, "linear.json"), "w", encoding="utf-8") as handle:
            json.dump(issues, handle, ensure_ascii=False, indent=1)
        with open(os.path.join(args.dump_snapshot, "github.json"), "w", encoding="utf-8") as handle:
            json.dump({"prs": prs, "other_base": other_base}, handle, ensure_ascii=False, indent=1)

    text = render(
        findings, controls, {"now": now.strftime("%Y-%m-%d %H:%M UTC"), "days": args.days}
    )
    payload = {
        "broken": any(not control["ok"] for control in controls),
        "generated_at": now.isoformat(),
        "days": args.days,
        "counts": {str(number): len(findings[number]) for number, _, _ in QUESTIONS},
        "findings": {str(number): findings[number] for number, _, _ in QUESTIONS},
        "controls": controls,
        "population": {
            "linear_issues": len(issues),
            "merged_prs_into_base": len(prs),
            "merged_prs_other_base": other_base,
        },
    }
    emit(args, text, payload)
    return 2 if payload["broken"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
