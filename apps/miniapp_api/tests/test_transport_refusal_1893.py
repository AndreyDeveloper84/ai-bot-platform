"""Пустой или невалидный initData — один отказ транспорта (DRF-1893).

Решение владельца (раздел U, канон 1319-D): пустой initData — невалидный
транспортный вход, а не анонимный клиент. Mini App работает только из MAX с
валидным initData; без него — fail closed, экран «Открой Ayla из MAX».

До этого листа одно состояние «MAX не передал достоверные данные входа»
отвечало тремя кодами и двумя статусами (400 ``malformed``, 401
``bad_signature``, 401 ``stale``), а пустое значение без настроенного токена —
500. Отображение исключений в ответ было скопировано в четыре декоратора.

Здесь закрепляется:

* нет заголовка / пустое значение / испорченный заголовок / чужая подпись /
  просроченный ``auth_date`` → **401 ``no_init_data``** на каждом декораторе
  Mini App (клиент, мастер, онбординг мастера, администратор, ресепшн);
* причина различается только в логе, значение initData в лог не попадает;
* валидный initData проходит транспорт (положительная стража);
* каждый маршрут Mini App несёт метку проверки initData (census-сторож).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time as time_module
from collections.abc import Callable, Iterator
from urllib.parse import urlencode

import pytest
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.test import RequestFactory
from django.urls import URLPattern, URLResolver, get_resolver

from apps.admin_api.auth import require_admin_or_reception_read, require_admin_role
from apps.master_api.auth import require_init_data_only, require_master_init_data
from apps.miniapp_api.views import require_init_data

pytestmark = pytest.mark.django_db

BOT_TOKEN = "test-bot-token-1893"  # pragma: allowlist secret
GUARD_ATTR = "__init_data_guard__"

DECORATORS: dict[str, Callable[[Callable[..., HttpResponse]], Callable[..., HttpResponse]]] = {
    "customer": require_init_data,
    "master": require_master_init_data,
    "master_onboarding": require_init_data_only,
    "admin": require_admin_role,
    "admin_or_reception_read": require_admin_or_reception_read,
}

#: URL-модули, которые зовёт Mini App. ``apps.marketplace.urls`` (``/providers``)
#: публичен по замыслу и Mini App его не вызывает — поэтому не здесь.
MINIAPP_URL_PREFIXES = (
    "api/v1/customer/",
    "api/v1/master/",
    "api/v1/admin/",
    "api/v1/internal-chat/",
)
MINIAPP_EXACT_ROUTES = ("api/v1/me",)

#: Снятые маршруты: отвечают 410 всем и не читают ни строки, поэтому личность
#: не разбирают — 401 вместо 410 отправил бы звонящего чинить не то. Список
#: красный в обе стороны: маршрут обязан существовать и без initData отвечать
#: именно 410, иначе исключение прикрыло бы живую ручку
#: (``test_retired_routes_really_are_retired``).
RETIRED_ROUTES: dict[str, str] = {
    "api/v1/master/^conversations(?:/.*)?$": (
        "DRF-1528: переписка мастер↔клиент снята (OD-7), девять ручек — 410"
    ),
}


@pytest.fixture(autouse=True)
def _bot_token(settings) -> None:
    settings.MAX_BOT_TOKEN = BOT_TOKEN


def _sign(params: dict[str, str], *, token: str = BOT_TOKEN) -> str:
    data_check_string = "\n".join(f"{k}={params[k]}" for k in sorted(params))
    secret_key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode({**params, "hash": digest})


def _params(*, auth_date: int | None = None) -> dict[str, str]:
    return {
        "user": json.dumps({"id": 189300, "first_name": "Проба"}),
        "auth_date": str(auth_date if auth_date is not None else int(time_module.time())),
    }


INPUTS: dict[str, Callable[[], dict[str, str]]] = {
    "missing": lambda: {},
    "empty": lambda: {"HTTP_AUTHORIZATION": "MaxInitData "},
    "bad_signature": lambda: {
        "HTTP_AUTHORIZATION": f"MaxInitData {_sign(_params(), token='wrong')}"
    },
    "stale": lambda: {
        "HTTP_AUTHORIZATION": f"MaxInitData {_sign(_params(auth_date=int(time_module.time()) - 7200))}"
    },
}


def _decorated(decorator) -> tuple[Callable[..., HttpResponse], list[int]]:
    calls: list[int] = []

    def view(request: HttpRequest) -> HttpResponse:
        calls.append(1)
        return JsonResponse({"ok": True})

    return decorator(view), calls


def _json(resp: HttpResponse) -> dict:
    return json.loads(resp.content.decode() or "{}")


# ── один отказ транспорта ─────────────────────────────────────────


@pytest.mark.parametrize("surface", list(DECORATORS))
@pytest.mark.parametrize("case", list(INPUTS))
def test_transport_refusal_is_one_code_on_every_surface(surface, case):
    view, calls = _decorated(DECORATORS[surface])

    resp = view(RequestFactory().get("/", **INPUTS[case]()))

    assert (resp.status_code, _json(resp).get("error")) == (401, "no_init_data"), (surface, case)
    assert calls == []


def test_garbled_header_is_the_same_refusal():
    view, calls = _decorated(require_init_data)

    resp = view(RequestFactory().get("/", HTTP_AUTHORIZATION="Bearer something"))

    assert (resp.status_code, _json(resp).get("error")) == (401, "no_init_data")
    assert calls == []


def test_empty_init_data_is_a_transport_refusal_even_without_a_bot_token(settings):
    """Пустое значение — отказ транспорта при любой конфигурации: раньше без токена это было 500."""
    settings.MAX_BOT_TOKEN = ""
    view, calls = _decorated(require_init_data)

    resp = view(RequestFactory().get("/", HTTP_AUTHORIZATION="MaxInitData "))

    assert (resp.status_code, _json(resp).get("error")) == (401, "no_init_data")
    assert calls == []


# ── валидный initData проходит транспорт ──────────────────────────


@pytest.mark.parametrize("surface", list(DECORATORS))
def test_valid_init_data_passes_the_transport_check(surface):
    """Положительная стража: дальше может быть 404/403/500 по роли или настройке — но не отказ транспорта."""
    view, _calls = _decorated(DECORATORS[surface])
    header = f"MaxInitData {_sign(_params())}"

    resp = view(RequestFactory().get("/", HTTP_AUTHORIZATION=header))

    assert _json(resp).get("error") != "no_init_data"
    assert _json(resp).get("error") not in {"malformed", "bad_signature", "stale"}


# ── причина — только в логе ───────────────────────────────────────


def test_refusal_reason_is_logged_without_the_init_data_value(caplog):
    view, _calls = _decorated(require_init_data)
    signed = _sign(_params(), token="wrong")
    stale = _sign(_params(auth_date=int(time_module.time()) - 7200))
    requests = {
        "missing": {},
        "empty": {"HTTP_AUTHORIZATION": "MaxInitData "},
        "malformed": {"HTTP_AUTHORIZATION": "Bearer something"},
        "bad_signature": {"HTTP_AUTHORIZATION": f"MaxInitData {signed}"},
        "stale": {"HTTP_AUTHORIZATION": f"MaxInitData {stale}"},
    }

    with caplog.at_level(logging.INFO):
        for headers in requests.values():
            view(RequestFactory().get("/", **headers))

    messages = [r.getMessage() for r in caplog.records]
    refused = [m for m in messages if "miniapp.auth.transport_refused" in m]
    assert refused, "отказ транспорта не пишется в лог"
    logged_reasons = {
        part.split("=", 1)[1] for m in refused for part in m.split() if part.startswith("reason=")
    }
    assert logged_reasons == set(requests)
    for raw in (signed, stale):
        assert all(raw not in m for m in messages), "значение initData попало в лог"


# ── census: каждый маршрут Mini App проверяет initData ────────────


def _walk(patterns, prefix: str = "") -> Iterator[tuple[str, Callable]]:
    for entry in patterns:
        route = prefix + str(entry.pattern)
        if isinstance(entry, URLResolver):
            yield from _walk(entry.url_patterns, route)
        elif isinstance(entry, URLPattern):
            yield route, entry.callback


def _miniapp_routes() -> list[tuple[str, Callable]]:
    return [
        (route, callback)
        for route, callback in _walk(get_resolver().url_patterns)
        if route.startswith(MINIAPP_URL_PREFIXES) or route in MINIAPP_EXACT_ROUTES
    ]


def _unguarded(routes: list[tuple[str, Callable]]) -> list[str]:
    return sorted(
        route
        for route, callback in routes
        if not getattr(callback, GUARD_ATTR, None) and route not in RETIRED_ROUTES
    )


def test_every_miniapp_route_carries_the_init_data_guard():
    routes = _miniapp_routes()
    assert (
        len(routes) >= 130
    )  # перепись dev 8bf99f7c: 40 + 43 + 32 + 10 + /me (126) + 3 маршрута памяти DRF-2133 + last-topic DRF-2144
    assert _unguarded(routes) == []


def test_retired_routes_really_are_retired(client):
    """Исключение из переписи не прикрывает живую ручку: маршрут есть и отвечает 410."""
    routes = dict(_miniapp_routes())
    for route in RETIRED_ROUTES:
        assert route in routes, f"снятый маршрут {route!r} пропал из URLconf — убери исключение"
    response = client.get("/api/v1/master/conversations")
    assert response.status_code == 410, response.status_code


def test_guard_catches_a_route_without_the_marker():
    """Положительная стража сторожа: вью без метки — поймана, с меткой — нет."""

    def bare(request):  # pragma: no cover - never called
        return JsonResponse({})

    def marked(request):  # pragma: no cover - never called
        return JsonResponse({})

    setattr(marked, GUARD_ATTR, "customer")

    assert _unguarded([("api/v1/customer/x", bare), ("api/v1/customer/y", marked)]) == [
        "api/v1/customer/x"
    ]
