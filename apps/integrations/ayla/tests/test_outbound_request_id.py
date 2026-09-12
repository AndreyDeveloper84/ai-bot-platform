"""X-Request-ID на каждом исходящем вызове бот → каталог (DRF-1616, B-7.2).

Каталог принимает заголовок с апреля (``users.middleware.RequestIDMiddleware``:
честь входящему значению, лог и ответ под ним). Бот до этого PR его не слал —
ход пользователя жил под двумя несвязанными id. Здесь стережётся:

1. значение — ``trace_id`` хода; вне хода — свежий uuid на вызов;
2. заголовок реально уходит в провод (MockTransport) у трёх клиентов с
   инжектируемым транспортом: booking, billing, catalog-зеркало;
3. **каждый** словарь заголовков с ``Authorization`` / ``X-Service-Token`` во
   всех модулях бота, которые ходят в Ayla, проходит через
   ``with_request_id`` — читается AST, а не текст. Одного клиента мимо
   достаточно, чтобы корреляция на его маршрутах молчала, и никакой
   функциональный тест этого не заметит: у него транспорт свой.
"""

from __future__ import annotations

import ast
import uuid
from pathlib import Path

import httpx
import pytest

from apps.integrations.ayla import booking_client as bc
from apps.integrations.ayla.billing_client import AylaBillingClient
from apps.integrations.ayla.request_id import (
    REQUEST_ID_HEADER,
    outbound_request_id,
    with_request_id,
)
from apps.tenancy.context import trace_id_scope

REPO_ROOT = Path(__file__).resolve().parents[4]
AUTH_KEYS = {"Authorization", "X-Service-Token"}
WRAPPER = "with_request_id"


# ─── 1. значение ─────────────────────────────────────────────────────────────


def test_inside_a_turn_the_header_is_the_turns_trace_id() -> None:
    with trace_id_scope("trace-abc"):
        assert outbound_request_id() == "trace-abc"
        assert with_request_id({"Accept": "x"})[REQUEST_ID_HEADER] == "trace-abc"


def test_outside_a_turn_each_call_gets_a_fresh_uuid() -> None:
    with trace_id_scope(None):
        first, second = outbound_request_id(), outbound_request_id()

    assert first != second
    uuid.UUID(first)
    uuid.UUID(second)


@pytest.mark.parametrize("bad", ["", "   ", "x" * 129, "line\nbreak"])
def test_an_unusable_trace_id_falls_back_to_a_fresh_uuid(bad: str) -> None:
    """Каталог кладёт значение в лог как есть. Перевод строки в trace_id —
    строка лога, которой не было; сверхдлинное — не наш id."""
    with trace_id_scope(bad):
        value = outbound_request_id()

    assert value != bad
    uuid.UUID(value)


def test_a_caller_supplied_id_is_kept_and_the_input_is_not_mutated() -> None:
    original = {REQUEST_ID_HEADER: "retry-7", "Accept": "x"}
    with trace_id_scope("trace-abc"):
        out = with_request_id(original)

    assert out[REQUEST_ID_HEADER] == "retry-7"
    assert out is not original


# ─── 2. провод ───────────────────────────────────────────────────────────────


def _capture() -> tuple[list[httpx.Request], httpx.MockTransport]:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"count": 0, "next": None, "results": []})

    return seen, httpx.MockTransport(handler)


def test_booking_client_sends_the_turns_trace_id(db) -> None:
    from apps.tenancy.context import tenant_scope
    from apps.tenancy.models import Tenant

    seen, transport = _capture()
    client = bc.AylaBookingHTTPClient(
        base_url="https://ayla.test", api_token="t", transport=transport
    )
    tenant = Tenant.objects.create(slug="xrid", name="T")
    with tenant_scope(tenant), trace_id_scope("trace-booking"):
        client.get_services()

    assert [r.headers.get(REQUEST_ID_HEADER) for r in seen] == ["trace-booking"]


def test_billing_client_sends_the_turns_trace_id() -> None:
    seen, transport = _capture()
    client = AylaBillingClient(
        base_url="https://ayla.test", token="t", http_client=httpx.Client(transport=transport)
    )
    with trace_id_scope("trace-billing"):
        client.get_billing_status(specialist_id="s1")

    assert [r.headers.get(REQUEST_ID_HEADER) for r in seen] == ["trace-billing"]


def test_catalog_mirror_client_sends_the_turns_trace_id() -> None:
    from apps.catalog.services.http_client import CatalogHttpClient

    seen, transport = _capture()
    client = CatalogHttpClient(
        base_url="https://ayla.test", token="t", http_client=httpx.Client(transport=transport)
    )
    with trace_id_scope("trace-mirror"):
        client.fetch_salon_services(tenant_id="tid")

    assert seen, "запрос не ушёл — измерялся не тот предмет"
    assert {r.headers.get(REQUEST_ID_HEADER) for r in seen} == {"trace-mirror"}


# ─── 3. сторож: ни один словарь с auth-заголовком мимо обёртки ──────────────


def _ayla_modules() -> list[Path]:
    """Все не-тестовые модули бота, которые ходят в Ayla.

    Признак — они строят URL через ``AylaUrlBuilder`` или читают
    ``AYLA_BASE_URL``. Сам url_builder и пакетный __init__ исключены: они
    URL строят, но не зовут.
    """
    out: list[Path] = []
    for path in sorted((REPO_ROOT / "apps").rglob("*.py")):
        posix = path.as_posix()
        if "/tests/" in posix or path.name.startswith("test_") or "/migrations/" in posix:
            continue
        if path.name in {"url_builder.py", "__init__.py", "request_id.py"}:
            continue
        src = path.read_text(encoding="utf-8")
        if "AylaUrlBuilder" in src or "AYLA_BASE_URL" in src:
            out.append(path)
    return out


def _is_auth_dict(node: ast.AST) -> bool:
    return isinstance(node, ast.Dict) and any(
        isinstance(k, ast.Constant) and k.value in AUTH_KEYS for k in node.keys
    )


def _bare_auth_dicts(source: str) -> list[int]:
    """Строки, где словарь с auth-ключом НЕ является прямым аргументом WRAPPER."""
    tree = ast.parse(source)
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    bare: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict) or not _is_auth_dict(node):
            continue
        parent = parents.get(node)
        wrapped = (
            isinstance(parent, ast.Call)
            and isinstance(parent.func, ast.Name)
            and parent.func.id == WRAPPER
            and node in parent.args
        )
        if not wrapped:
            bare.append(node.lineno)
    return bare


def _auth_dicts_total(source: str) -> int:
    return sum(1 for n in ast.walk(ast.parse(source)) if _is_auth_dict(n))


def test_every_auth_header_dict_in_every_ayla_module_goes_through_the_wrapper() -> None:
    modules = _ayla_modules()
    census = {
        m.relative_to(REPO_ROOT).as_posix(): _auth_dicts_total(m.read_text(encoding="utf-8"))
        for m in modules
    }
    # Сначала — что предмет найден. Ноль модулей или ноль словарей прошли бы
    # проверку ниже молча и значили бы «не искали», а не «всё обёрнуто».
    assert census, "ни одного модуля, ходящего в Ayla, не найдено — измерялся не тот предмет"
    assert sum(census.values()) >= 20, census
    assert any(k.endswith("booking_client.py") for k in census), census

    offenders = {
        m.relative_to(REPO_ROOT).as_posix(): lines
        for m in modules
        if (lines := _bare_auth_dicts(m.read_text(encoding="utf-8")))
    }
    assert offenders == {}, (
        f"словарь заголовков с Authorization / X-Service-Token идёт мимо {WRAPPER}: "
        f"{offenders}. Каталог на этих маршрутах получит свой случайный id, и "
        "сопоставить его лог с trace_id бота будет нечем."
    )


@pytest.mark.parametrize(
    ("snippet", "expected"),
    [
        ('h = {"Authorization": "Bearer x"}', [1]),
        ('h = with_request_id({"Authorization": "Bearer x"})', []),
        ('h = with_request_id(headers={"Authorization": "Bearer x"})', [1]),
        ('h = {"X-Service-Token": t, "X-External-User-ID": u}', [1]),
        ('h = {"Accept": "application/json"}', []),
        ('h = other({"Authorization": "Bearer x"})', [1]),
    ],
)
def test_the_scanner_flags_bare_dicts_and_passes_wrapped_ones(
    snippet: str, expected: list[int]
) -> None:
    """Положительный контроль в обе стороны: находит голый словарь, не трогает
    обёрнутый и словарь без auth-ключа. Kwarg-форма ``headers=`` внутри
    обёртки считается голой — обёртка принимает позиционно."""
    assert _bare_auth_dicts(snippet) == expected
