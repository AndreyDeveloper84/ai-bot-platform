"""DRF-2552 — перепись «обещаний без исполнителей», первое приближение.

Запуск из корня репозитория: python census2552.py <out.json>
(DJANGO_SETTINGS_MODULE снаружи). Только чтение: django.setup() ради
реестра моделей; дальше — текст исходников из `git ls-files`, без тестов
и миграций. Один проход по строкам строит индекс, поля ищутся в нём.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections import defaultdict

sys.path.insert(0, os.getcwd())
import django  # noqa: E402

django.setup()

from django.apps import apps  # noqa: E402
from django.conf import settings  # noqa: E402
from django.db import models  # noqa: E402

ROOT = os.path.normcase(os.getcwd())


def tracked_py() -> list[str]:
    out = subprocess.run(["git", "ls-files", "*.py"], capture_output=True, text=True, check=True).stdout
    keep = []
    for p in out.splitlines():
        lp = p.replace("\\", "/")
        base = lp.split("/")[-1]
        if "/migrations/" in lp or "/tests/" in lp or base.startswith("test_") or base == "conftest.py":
            continue
        keep.append(lp)
    return keep


FILES = tracked_py()
WRITE = defaultdict(list)   # имя -> места присваивания / kwarg / ключа словаря
QUOTED = defaultdict(list)  # строка в кавычках -> места
CONST = defaultdict(list)   # .CONST -> места
DECL = set()                # (path, line, name) — объявления полей
ALLTEXT = {}

RX_ASSIGN = re.compile(r"(?<![\w.])\.?(\w+)\s*(?<![=!<>+\-*/%&|^:])=(?!=)")
RX_DICTKEY = re.compile(r"[\"']([\w\-.]+)[\"']\s*:")
RX_QUOTED = re.compile(r"[\"']([^\"'\n]{1,80})[\"']")
RX_CONST = re.compile(r"\.([A-Z][A-Z0-9_]+)\b")
RX_DECL = re.compile(r"^\s*(\w+)\s*[:=].*(models\.\w+\(|encrypt\(|Field\()")

for path in FILES:
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except (UnicodeDecodeError, OSError):
        continue
    ALLTEXT[path] = text
    for i, line in enumerate(text.splitlines(), 1):
        loc = f"{path}:{i}"
        m = RX_DECL.match(line)
        if m:
            DECL.add((path, i, m.group(1)))
        for m in RX_ASSIGN.finditer(line):
            WRITE[m.group(1)].append((path, i))
        for m in RX_DICTKEY.finditer(line):
            WRITE[m.group(1)].append((path, i))
        for m in RX_QUOTED.finditer(line):
            QUOTED[m.group(1)].append(loc)
        for m in RX_CONST.finditer(line):
            CONST[m.group(1)].append(loc)


def own(model) -> bool:
    f = getattr(sys.modules.get(model.__module__), "__file__", "") or ""
    return os.path.normcase(os.path.abspath(f)).startswith(ROOT)


result = {"root": ROOT, "files_scanned": len(ALLTEXT), "models": 0, "fields": 0, "choices": 0,
          "fields_no_writer": [], "fields_quoted_only": [], "choices_unassigned": [], "choices_not_assigned_inline": [],
          "prose": [], "tasks_unwired": [], "settings_unread": []}

for model in apps.get_models():
    if not own(model):
        continue
    result["models"] += 1
    label = model._meta.label
    consts = defaultdict(list)
    for k, v in vars(model).items():
        if isinstance(v, str) and k.isupper():
            consts[v].append(k)
        # вложенные TextChoices/IntegerChoices: присваивают как Model.Status.DONE
        if isinstance(v, type) and issubclass(v, models.Choices):
            for member in v:
                consts[str(member.value)].append(member.name)
    for f in model._meta.concrete_fields:
        if f.primary_key or f.auto_created or getattr(f, "auto_now", False) or getattr(f, "auto_now_add", False):
            continue
        result["fields"] += 1
        names = {f.name, f.attname}
        writes = [f"{p}:{i}" for n in names for (p, i) in WRITE.get(n, []) if (p, i, n) not in DECL]
        quoted = [loc for n in names for loc in QUOTED.get(n, [])]
        entry = {"field": f"{label}.{f.name}", "type": type(f).__name__, "null": f.null,
                 "default": f.has_default(), "writes": len(writes), "quoted": len(quoted),
                 "sample": (writes or quoted)[:4]}
        if not writes and not quoted:
            result["fields_no_writer"].append(entry)
        elif not writes:
            result["fields_quoted_only"].append(entry)

        if f.choices:
            for value, _l in f.flatchoices:
                if value in ("", None):
                    continue
                result["choices"] += 1
                sval = str(value)
                lit = QUOTED.get(sval, [])
                # объявления: CONST = "value" и строки кортежа choices сами не «присваивание»
                lit = [loc for loc in lit if not re.match(
                    rf"^\s*([A-Z_0-9]+\s*=\s*|\(\s*)[\"']{re.escape(sval)}[\"']",
                    ALLTEXT[loc.rsplit(":", 1)[0]].splitlines()[int(loc.rsplit(":", 1)[1]) - 1])]
                cref = [loc for c in consts.get(sval, []) for loc in CONST.get(c, [])]
                if not lit and not cref:
                    result["choices_unassigned"].append({"field": f"{label}.{f.name}", "value": sval,
                                                         "consts": consts.get(sval, [])})
                else:
                    # второй уровень: есть ли хоть одна строка, где значение стоит рядом
                    # с присваиванием ЭТОГО поля (field=…, .field = …, "field": …)
                    arx = re.compile(rf"{re.escape(f.name)}s*(=(?!=)|[\"']s*:)|[\"']{re.escape(f.name)}[\"']s*:")
                    inline = []
                    for loc in lit + cref:
                        pth, ln = loc.rsplit(":", 1)
                        line = ALLTEXT[pth].splitlines()[int(ln) - 1]
                        if arx.search(line):
                            inline.append(loc)
                    if not inline:
                        result["choices_not_assigned_inline"].append({
                            "field": f"{label}.{f.name}", "value": sval,
                            "consts": consts.get(sval, []), "mentions": (lit + cref)[:5]})

PROSE = re.compile(r"used by|boosts?\b|capped|retrievable|consumed by|read by|feeds (the|into)|drives the|"
                   r"показыва|ограничен|используется (в|для|кросс)|кросс-доменн", re.I)
for path, text in ALLTEXT.items():
    for i, line in enumerate(text.splitlines(), 1):
        if PROSE.search(line):
            result["prose"].append(f"{path}:{i}: {line.strip()[:160]}")

WORDS = defaultdict(int)
for text in ALLTEXT.values():
    for w in re.findall(r"\b\w+\b", text):
        WORDS[w] += 1
beat = str(getattr(settings, "CELERY_BEAT_SCHEDULE", {})) + str(getattr(settings, "CELERY_BEAT_SCHEDULE_EXTRA", {}))
task_rx = re.compile(r"@(shared_task|app\.task|celery_app\.task|\w+\.task)\b")
for path, text in ALLTEXT.items():
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if task_rx.search(line):
            for j in range(i + 1, min(i + 8, len(lines))):
                m = re.match(r"\s*(async\s+)?def\s+(\w+)", lines[j])
                if m:
                    fn = m.group(2)
                    if WORDS.get(fn, 0) <= 1 and fn not in beat:
                        result["tasks_unwired"].append(f"{path}:{j + 1} {fn}")
                    break

import django.conf.global_settings as gs  # noqa: E402

defaults = set(dir(gs))
for n in sorted(x for x in dir(settings) if x.isupper()):
    if n in defaults:
        continue
    readers = [p for p, t in ALLTEXT.items() if "settings" not in p.split("/")[-2:][0] and n in t]
    if not readers:
        result["settings_unread"].append(n)

with open(sys.argv[1], "w", encoding="utf-8") as fh:
    json.dump(result, fh, ensure_ascii=False, indent=1)
print(json.dumps({k: (len(v) if isinstance(v, list) else v) for k, v in result.items()}, ensure_ascii=False))
