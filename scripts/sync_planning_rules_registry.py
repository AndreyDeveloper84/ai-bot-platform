#!/usr/bin/env python3
"""Выгрузка реестра планировочных правил из ayla-knowledge при сборке (D-1).

Источник истины — ``ayla-knowledge``; в рантайм реестр попадает **только**
через этот скрипт: он забирает курируемый файл по закреплённому ``ref``,
проверяет целостность и кладёт в ``apps/planning_rules/data/`` рядом с
``source.json``. Образец — канонический каталог
``services/seeds/canonical_catalog_2026-07.json`` + seed-команда.

Режимы:

* ``sync`` (по умолчанию) — забрать реестр из источника и обновить
  вендоренную копию + ``source.json``. Источник недоступен → ненулевой код,
  вендоренная копия **не трогается** (лучше упасть, чем тихо записать
  обрезок).
* ``--check`` — сравнить вендоренную копию с источником по ``ref`` из
  ``source.json``. Разошлись → ненулевой код: правило, изменённое
  владельцем, не должно тихо не доехать.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = REPO_ROOT / "apps" / "planning_rules" / "data" / "planning-rules-registry.yaml"
SOURCE_PATH = REPO_ROOT / "apps" / "planning_rules" / "data" / "source.json"

RAW_URL = "https://raw.githubusercontent.com/{repository}/{ref}/{path}"


def _fetch_registry(repository: str, ref: str, path: str) -> bytes:
    url = RAW_URL.format(
        repository=repository,
        ref=urllib.parse.quote(ref, safe=""),
        path=urllib.parse.quote(path, safe="/"),
    )
    request = urllib.request.Request(url, headers={"User-Agent": "ayla-planning-rules-sync"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def _load_source() -> dict:
    return json.loads(SOURCE_PATH.read_text(encoding="utf-8"))


def sync(source: dict) -> int:
    try:
        payload = _fetch_registry(source["repository"], source["ref"], source["path"])
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(
            f"FAIL: источник недоступен ({exc}). Вендоренная копия не тронута — "
            f"отказ выгрузки лучше молчаливой выкладки обрезка.",
            file=sys.stderr,
        )
        return 1
    if b"404: Not Found" in payload[:64] or not payload.strip():
        print(
            f"FAIL: источник отдал пустой/404 ответ по {source['ref']}:{source['path']}. "
            f"Вендоренная копия не тронута.",
            file=sys.stderr,
        )
        return 1
    digest = hashlib.sha256(payload).hexdigest()
    DATA_PATH.write_bytes(payload)
    updated = dict(source, sha256=digest, synced_at=_today())
    SOURCE_PATH.write_text(
        json.dumps(updated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"OK: выгружено {len(payload)} байт из {source['repository']}@{source['ref']}, "
        f"sha256={digest}"
    )
    return 0


def check(source: dict) -> int:
    try:
        payload = _fetch_registry(source["repository"], source["ref"], source["path"])
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"FAIL: источник недоступен для сверки ({exc})", file=sys.stderr)
        return 1
    upstream = hashlib.sha256(payload).hexdigest()
    if upstream != source["sha256"]:
        print(
            f"FAIL: реестр в источнике изменился ({source['repository']}@{source['ref']}): "
            f"upstream sha256={upstream}, вендорено {source['sha256']}. "
            f"Запустите sync и завезите изменение явным коммитом.",
            file=sys.stderr,
        )
        return 1
    print(f"OK: вендоренная копия совпадает с источником ({upstream})")
    return 0


def _today() -> str:
    import datetime

    return datetime.date.today().isoformat()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="только сверить вендоренную копию с источником, ничего не писать",
    )
    args = parser.parse_args()
    source = _load_source()
    return check(source) if args.check else sync(source)


if __name__ == "__main__":
    sys.exit(main())
