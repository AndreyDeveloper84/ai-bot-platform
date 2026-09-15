"""Отказ транспорта Mini App — одно имя на все поверхности (DRF-1893).

Решение владельца (раздел U, канон 1319-D): пользователь идентифицирован,
когда MAX передал валидный initData. Пустой, испорченный, чужой или
просроченный initData — невалидный транспортный вход, а не анонимный клиент:
Mini App закрывается экраном «Открой Ayla из MAX».

До этого модуля отображение исключений проверки в ответ было скопировано в
четыре декоратора (клиент, мастер ×2, администратор) и давало три кода и два
статуса на одно состояние: 400 ``malformed``, 401 ``bad_signature``, 401
``stale``. Для человека разницы нет — действие одно (открыть из MAX), — а
различие нужно только разбирающему инцидент. Поэтому:

* наружу — **401 ``no_init_data``**, одинаково на всех поверхностях;
* внутрь — строка лога ``miniapp.auth.transport_refused surface=… reason=…``,
  где ``reason`` ∈ ``missing | empty | malformed | bad_signature | stale |
  future | invalid``. Значение initData и id человека в лог не пишутся.

``InitDataNotConfigured`` — не транспорт, а поломка сервера: остаётся 500
``server_misconfigured``.

Каждый декоратор Mini App ставит на обёртку метку :data:`GUARD_ATTR` —
по ней census-сторож (``tests/test_transport_refusal_1893.py``) проверяет,
что ни один маршрут Mini App не открыт без проверки initData.
"""

from __future__ import annotations

import logging

from django.http import HttpRequest, JsonResponse

from apps.miniapp_api.auth import (
    HEADER_PREFIX,
    InitDataBadSignature,
    InitDataError,
    InitDataFromFuture,
    InitDataMalformed,
    InitDataNotConfigured,
    InitDataStale,
    VerifiedInitData,
    extract_init_data,
    verify_init_data,
)

logger = logging.getLogger(__name__)

#: Слаг отказа транспорта — тот же, что у клиента (`lib/identity.ts`).
NO_INIT_DATA = "no_init_data"
#: Метка на обёртке декоратора Mini App; значение — имя поверхности.
GUARD_ATTR = "__init_data_guard__"

_DETAIL = "Mini App opens only from MAX with valid initData"


def _reason(header: str, exc: InitDataError) -> str:
    if not header:
        return "missing"
    if header.startswith(HEADER_PREFIX) and not header[len(HEADER_PREFIX) :]:
        return "empty"
    if isinstance(exc, InitDataStale):
        return "stale"
    if isinstance(exc, InitDataFromFuture):
        return "future"
    if isinstance(exc, InitDataBadSignature):
        return "bad_signature"
    if isinstance(exc, InitDataMalformed):
        return "malformed"
    return "invalid"


def verify_request_init_data(
    request: HttpRequest, *, surface: str
) -> tuple[VerifiedInitData | None, JsonResponse | None]:
    """Проверить initData запроса: ``(verified, None)`` либо ``(None, отказ)``."""

    header = request.headers.get("Authorization", "")
    try:
        raw = extract_init_data(header)
        return verify_init_data(raw), None
    except InitDataNotConfigured:
        logger.error("%s.auth.not_configured", surface)
        return None, JsonResponse(
            {"error": "server_misconfigured", "detail": "MAX bot token not configured"},
            status=500,
        )
    except InitDataError as exc:
        logger.warning(
            "miniapp.auth.transport_refused surface=%s reason=%s",
            surface,
            _reason(header, exc),
        )
        return None, JsonResponse({"error": NO_INIT_DATA, "detail": _DETAIL}, status=401)
