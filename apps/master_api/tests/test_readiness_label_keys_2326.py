"""DRF-2326 — у каждого пункта готовности, который отдаёт сервер, есть подпись
в Mini App, и наоборот.

Экран 01 и карточка «Моего дня» подписывают пункт через
``READINESS_ITEM_LABELS[item.key] ?? item.key``
(``apps/miniapp/src/screens/MasterSetupLandingScreen.tsx``). Пока наборы
совпадают, запасной ход ``?? item.key`` не срабатывает; первый новый ключ на
сервере без подписи выведет мастеру служебный слаг. Сегодня совпадают 4 из 4 —
дефект скрытый, поэтому держим равенство множеств, а не правим вид.

Узел — pytest, а не vitest: подмена, которую он ловит, — правка только
Python-стороны, а задача ``miniapp`` в CI при нетронутом ``apps/miniapp``
пропускается.

Ключи сервера — по конструктору, а не по одному списку: ``deep_link`` пункта
индексирует ``DEEP_LINKS[key]`` (кроме ведомых вне приложения), так что любой
выдаваемый ключ обязан быть там; плюс ``REQUIRED_ITEMS``, ``SALON_MANAGED_ITEMS``
и литералы ``ReadinessItem("…")`` в исходнике.
"""

from __future__ import annotations

import re
from pathlib import Path

from apps.master_api.services import onboarding_readiness as readiness

REPO = Path(__file__).resolve().parents[3]
LABELS_TS = REPO / "apps/miniapp/src/screens/MasterSetupLandingScreen.tsx"
READINESS_PY = Path(readiness.__file__)

_LABELS_BLOCK = re.compile(
    r"export const READINESS_ITEM_LABELS: Record<string, string> = \{(?P<body>.*?)\};",
    re.S,
)
_LABEL_KEY = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:", re.M)
_CONSTRUCTOR_KEY = re.compile(r'ReadinessItem\(\s*"([a-z_]+)"')


def _label_keys(source: str) -> set[str]:
    block = _LABELS_BLOCK.search(source)
    assert block, "в MasterSetupLandingScreen.tsx не найден READINESS_ITEM_LABELS"
    return set(_LABEL_KEY.findall(block.group("body")))


def _server_keys() -> set[str]:
    return (
        set(readiness.DEEP_LINKS)
        | set(readiness.REQUIRED_ITEMS)
        | set(readiness.SALON_MANAGED_ITEMS)
        | set(_CONSTRUCTOR_KEY.findall(READINESS_PY.read_text(encoding="utf-8")))
    )


def test_parser_sees_both_sides() -> None:
    # Положительный контроль: пустое множество с обеих сторон дало бы
    # «равенство» без предмета.
    labels = _label_keys(LABELS_TS.read_text(encoding="utf-8"))
    assert {"services", "location", "hours", "profile"} <= labels
    assert {"services", "location", "hours", "profile"} <= _server_keys()


def test_parser_names_a_key_without_label() -> None:
    planted = (
        "export const READINESS_ITEM_LABELS: Record<string, string> = {\n"
        '  services: "Услуги и цены",\n'
        "};\n"
    )
    assert _label_keys(planted) == {"services"}


def test_every_server_key_has_a_label_and_back() -> None:
    labels = _label_keys(LABELS_TS.read_text(encoding="utf-8"))
    server = _server_keys()
    # Присутствие раньше отсутствия: пустые множества дали бы пустые разности.
    assert "services" in labels
    assert "services" in server
    assert sorted(server - labels) == [], "ключи сервера без подписи — на экран выйдет слаг"
    assert sorted(labels - server) == [], "подписи без ключа сервера — мёртвые строки"
