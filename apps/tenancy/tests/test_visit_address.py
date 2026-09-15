"""DRF-1952 — фразы пустого адреса: один источник правды с Mini App.

Бот и Mini App говорят человеку одно и то же про адрес салона. Тест читает
константы из ``apps/miniapp/src/lib/visit-address.ts`` и сверяет их с
двойником на Python; правило трёх состояний — то же.
"""

from __future__ import annotations

import re
from pathlib import Path

TS = Path(__file__).resolve().parents[3] / "apps" / "miniapp" / "src" / "lib" / "visit-address.ts"


def _ts_const(name: str) -> str:
    text = TS.read_text(encoding="utf-8")
    match = re.search("export const " + name + ' = "([^"]*)";', text)
    assert match, name
    return match.group(1)


def test_phrases_and_rule_match_the_mini_app() -> None:
    from apps.tenancy.visit_address import ADDRESS_SAID_NONE, ADDRESS_UNKNOWN, visit_address_text

    assert ADDRESS_UNKNOWN == _ts_const("ADDRESS_UNKNOWN")
    assert ADDRESS_SAID_NONE == _ts_const("ADDRESS_SAID_NONE")
    assert visit_address_text(None) == ADDRESS_UNKNOWN
    assert visit_address_text("") == ADDRESS_SAID_NONE
    assert visit_address_text("ул. Карпинского, 33А") == "ул. Карпинского, 33А"


def test_the_address_line_never_says_address_twice() -> None:
    """«Адрес не указан» — сама строка, без «Адрес: » перед ней (ревью DRF-1952)."""
    from types import SimpleNamespace

    from apps.tenancy.visit_address import tenant_address_line

    assert (
        tenant_address_line(SimpleNamespace(address="ул. Карпинского, 33А"))
        == "Адрес: ул. Карпинского, 33А"
    )
    assert tenant_address_line(SimpleNamespace(address=None)) == "Адрес: Уточните адрес в салоне"
    assert tenant_address_line(SimpleNamespace(address="")) == "Адрес не указан"
