"""DRF-2678, мини-апп: котировка берёт у ребра РАЗРЕШЁННУЮ длительность.

Контракт каталога (``beautygo_backend/docs/CATALOG_INTERNAL_API_CONTRACT.md`` §2):
``duration_minutes`` — «raw specialist override (may be null)»,
``resolved_duration`` — «effective … Use this». Котировка читала сырое поле.

У мастера без собственного переопределения сырое поле — ``null``, и ответ
падал на длительность из зеркала услуги. Каталог же поставит на запись
разрешённую (мастер → салон → шаблон). Пока зеркало свежее, числа совпадают;
разошлись — человек видит одно, а запись получает другое, и создание отвечает
``409 QUOTE_CHANGED`` на то, что мини-апп сам же и показал.

В фикстуре три разных числа — зеркало 60, разрешённое 45, переопределение 30, —
чтобы ни один узел не прошёл совпадением.
"""

from __future__ import annotations

from typing import Any

import pytest

from apps.miniapp_api.tests import test_quote_changed_1708 as _quote_module

# Фикстуры и помощники соседнего модуля — присваиванием, как в узлах DRF-1933.
_quote = _quote_module._quote
_settings = _quote_module._settings
bot_user = _quote_module.bot_user
master = _quote_module.master
service = _quote_module.service
stub = _quote_module.stub
tenant = _quote_module.tenant

pytestmark = pytest.mark.django_db

MIRROR = 60
_ABSENT = object()


def _edge(*, raw: Any, resolved: Any = _ABSENT) -> dict[str, Any]:
    """Строка ребра по примеру §2 контракта; ``resolved`` можно не слать вовсе —
    каталог старше этого поля."""
    row: dict[str, Any] = {
        "id": "a4e00000-0000-4000-8000-000000002678",
        "name": "Маникюр классический",
        "category_slug": "manicure",
        "duration_minutes": raw,
        "price": "1700.00",
        "buffer_after_minutes": 0,
        "is_active": True,
    }
    if resolved is not _ABSENT:
        row["resolved_duration"] = resolved
    return row


def _quoted(client, service, master, stub, edge: dict[str, Any]) -> dict[str, Any]:
    assert service.duration_min == MIRROR
    stub.edges = [edge]
    r = _quote(client, service, master)
    assert r.status_code == 200, r.content
    return r.json()["quote"]


def test_a_master_without_an_override_is_quoted_the_resolved_duration(
    client, bot_user, master, service, stub
):
    """Живой случай: переопределения нет, каталог разрешил 45, в зеркале 60."""
    quote = _quoted(client, service, master, stub, _edge(raw=None, resolved=45))

    assert quote == {"price": "1700.00", "duration_minutes": 45, "source": "edge"}


def test_the_resolved_field_wins_over_the_raw_one(client, bot_user, master, service, stub):
    """Два разных числа держат выбор поля. Сегодняшний каталог такой пары не
    шлёт (переопределение и есть разрешённое значение)."""
    quote = _quoted(client, service, master, stub, _edge(raw=30, resolved=45))

    assert quote["duration_minutes"] == 45


def test_an_override_is_quoted_as_before(client, bot_user, master, service, stub):
    quote = _quoted(client, service, master, stub, _edge(raw=30, resolved=30))

    assert quote["duration_minutes"] == 30


def test_a_catalog_without_the_field_still_quotes_the_raw_one(
    client, bot_user, master, service, stub
):
    quote = _quoted(client, service, master, stub, _edge(raw=30))

    assert quote["duration_minutes"] == 30


def test_an_unknown_edge_duration_keeps_the_mirror(client, bot_user, master, service, stub):
    """Каталог сказал «не знаю» — остаётся зеркало, как до правки."""
    quote = _quoted(client, service, master, stub, _edge(raw=None, resolved=None))

    assert quote == {"price": "1700.00", "duration_minutes": MIRROR, "source": "edge"}
