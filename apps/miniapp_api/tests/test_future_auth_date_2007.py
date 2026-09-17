"""initData с датой подписи из будущего — тот же отказ транспорта (DRF-2007).

``verify_init_data`` проверял у ``auth_date`` только «не старше 3600 с». Нижней
границы не было: подпись, датированная будущим, проходила и оставалась «свежей»
до ``now + 3600``, то есть срок жизни подписанного initData не был ограничен.

Допуск рассинхрона часов — **300 с** (``AUTH_DATE_FUTURE_SKEW_SECONDS``):
``auth_date`` ставит сервер MAX при подписи, значит важен только рассинхрон
часов MAX и нашего хоста (оба на NTP); то же окно ±300 с уже принято в
репозитории для подписанных событий (event-contract §6.2). Срок жизни
становится ограниченным: 3600 + 300 с.

Здесь закрепляется:

* ``auth_date`` дальше 300 с в будущем → 401 ``no_init_data`` на каждом
  декораторе Mini App, вью не вызывается;
* ровно +300 с проходит транспорт (положительная стража на границе);
* причина в логе — ``future``; проверка поднимает ``InitDataFromFuture``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time as time_module
from collections.abc import Callable
from urllib.parse import urlencode

import pytest
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.test import RequestFactory

from apps.admin_api.auth import require_admin_or_reception_read, require_admin_role
from apps.master_api.auth import require_init_data_only, require_master_init_data
from apps.miniapp_api import auth as miniapp_auth
from apps.miniapp_api.views import require_init_data

pytestmark = pytest.mark.django_db

BOT_TOKEN = "test-bot-token-2007"  # pragma: allowlist secret
SKEW = 300

DECORATORS: dict[str, Callable[[Callable[..., HttpResponse]], Callable[..., HttpResponse]]] = {
    "customer": require_init_data,
    "master": require_master_init_data,
    "master_onboarding": require_init_data_only,
    "admin": require_admin_role,
    "admin_or_reception_read": require_admin_or_reception_read,
}


@pytest.fixture(autouse=True)
def _bot_token(settings) -> None:
    settings.MAX_BOT_TOKEN = BOT_TOKEN


def _signed(auth_date: int) -> str:
    params = {
        "user": json.dumps({"id": 200700, "first_name": "Проба"}),
        "auth_date": str(auth_date),
    }
    data_check_string = "\n".join(f"{k}={params[k]}" for k in sorted(params))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode({**params, "hash": digest})


def _decorated(decorator) -> tuple[Callable[..., HttpResponse], list[int]]:
    calls: list[int] = []

    def view(request: HttpRequest) -> HttpResponse:
        calls.append(1)
        return JsonResponse({"ok": True})

    return decorator(view), calls


def _json(resp: HttpResponse) -> dict:
    return json.loads(resp.content.decode() or "{}")


@pytest.mark.parametrize("surface", list(DECORATORS))
def test_auth_date_from_the_future_is_a_transport_refusal(surface):
    view, calls = _decorated(DECORATORS[surface])
    header = f"MaxInitData {_signed(int(time_module.time()) + SKEW + 1)}"

    resp = view(RequestFactory().get("/", HTTP_AUTHORIZATION=header))

    assert (resp.status_code, _json(resp).get("error")) == (401, "no_init_data"), surface
    assert calls == []


def test_auth_date_exactly_at_the_skew_passes_the_transport_check():
    """Положительная стража на границе: +300 с — допустимый рассинхрон, не отказ транспорта."""
    now = int(time_module.time())
    verified = miniapp_auth.verify_init_data(_signed(now + SKEW), bot_token=BOT_TOKEN, now=now)

    assert verified.auth_date == now + SKEW


def test_future_refusal_is_logged_with_reason_future(caplog):
    view, _calls = _decorated(require_init_data)
    header = f"MaxInitData {_signed(int(time_module.time()) + SKEW + 1)}"

    with caplog.at_level(logging.INFO):
        view(RequestFactory().get("/", HTTP_AUTHORIZATION=header))

    refused = [
        r.getMessage() for r in caplog.records if "miniapp.auth.transport_refused" in r.getMessage()
    ]
    assert refused, "отказ транспорта не записан в лог"
    assert any("reason=future" in m for m in refused), refused


def test_verifier_raises_a_named_future_error():
    now = int(time_module.time())
    named = getattr(miniapp_auth, "InitDataFromFuture", None)
    assert named is not None, "нет исключения InitDataFromFuture"

    with pytest.raises(named):
        miniapp_auth.verify_init_data(_signed(now + SKEW + 1), bot_token=BOT_TOKEN, now=now)
