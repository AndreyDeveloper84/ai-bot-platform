"""Доставка смены согласия в каталог — ``POST /api/v1/internal/me/consent-events/`` (DRF-2776).

Решение владельца 05.10 (D): смену согласия получают системы, чьё поведение
от неё зависит; журнала мало. Каталог хранит данные под согласиями
``personal_calculation`` (параметры тела) и ``health`` (health_flags) и
стирает их при отзыве; по ``food_diary_processing`` перестаёт слать
бьюти-инсайт. Реестр согласий живёт в боте, поэтому весть должна уехать
отсюда. Ручку объявил каталог (окно ayla-00); этот модуль — клиент к ней.

### Контракт, как его читает бот

Аутентификация — как у остальных internal: ``Authorization: Bearer
{AYLA_INTERNAL_API_TOKEN}`` + ``X-External-User-ID: bot:{channel}:{id}``.
**Субъект — только из заголовка.** В событии бота ``customer_id`` — UUID
``BotUser``; каталогу этот ключ ни о чём не говорит, поэтому в тело он не
кладётся. Каталог разрешает заголовок БЕЗ создания пользователя.

Тело: ``event_id``, ``consent_type``, ``granted``, ``granted_at`` и
необязательный ``granted_via``.

Исходы — три, и они не сливаются:

* ``200 {"data": {"outcome": …}}`` с любым исходом каталога —
  ``applied`` / ``duplicate`` / ``ignored`` / ``stale`` / ``no_subject`` — это
  **доставлено**; исход и имена стёртых полей уходят в журнал;
* ``400`` / ``401`` / ``403`` — :class:`ConsentEventRejected`, постоянный
  отказ: ретраить бесполезно, но диспетчер всё равно сделает свои попытки и
  положит строку в DLQ с текстом ответа — это и есть «результат доставки»;
* ``5xx``, ``429``, сеть, таймаут — :class:`ConsentEventUnavailable`,
  временный. ``429`` — своё ведро ручки (300/мин, ``consent_events_internal``):
  разбор накопленного ящика может в него упереться, и это не повод класть
  отзыв в DLQ как отвергнутый;
* ``200`` без конверта ``data.outcome`` — :class:`ConsentEventContractViolation`.
  Иначе дрейф формы каталога молча пометил бы отзыв «доставленным».

Все три — исключения: подписчик их не ловит, и диспетчер считает строку
недоставленной. Предохранителя здесь нет намеренно: вызов идёт из воркера
по одной строке ящика, ретраи и DLQ уже даёт диспетчер, а предохранитель
поверх них превратил бы минуту простоя каталога в пачку ложных «временно».
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Final

import httpx
from django.conf import settings

from apps.integrations.ayla.log_ref import external_user_log_ref
from apps.integrations.ayla.request_id import with_request_id
from apps.integrations.ayla.url_builder import AylaUrlBuilder, AylaUrlError

logger = logging.getLogger(__name__)

PATH: Final = "internal/me/consent-events/"

#: Исходы каталога, которые значат «доставлено». Набор закрыт: незнакомый
#: исход — нарушение контракта, а не успех по умолчанию.
DELIVERED_OUTCOMES: Final = frozenset({"applied", "duplicate", "ignored", "stale", "no_subject"})

#: Вызов идёт из воркера, человек ответа не ждёт; но и держать воркер на
#: висящем каталоге дольше, чем тик диспетчера, незачем.
CONNECT_TIMEOUT_S: Final = 5.0
READ_TIMEOUT_S: Final = 15.0


class ConsentEventError(Exception):
    """База — подписчик пропускает её наверх, диспетчер считает строку неудачной."""


class ConsentEventRejected(ConsentEventError):
    """Каталог отказал по существу (``400``/``401``/``403``) — постоянный отказ."""

    def __init__(self, status: int, body: Any) -> None:
        super().__init__(f"catalog rejected the consent event: HTTP {status}: {str(body)[:300]}")
        self.status = status
        self.body = body


class ConsentEventUnavailable(ConsentEventError):
    """Каталог не ответил — сеть, таймаут, ``5xx``, не настроен адрес."""


class ConsentEventContractViolation(ConsentEventError):
    """Каталог ответил ``200``, но не в объявленной форме."""


@dataclass(frozen=True)
class ConsentEventReceipt:
    """Квитанция каталога: что он сделал с событием."""

    event_id: str
    outcome: str
    erased: tuple[str, ...] = field(default_factory=tuple)


def post_consent_event(*, external_user_id: str, body: dict[str, Any]) -> ConsentEventReceipt:
    """Отправить одну смену согласия; вернуть квитанцию или бросить исключение."""

    base = getattr(settings, "AYLA_BASE_URL", "")
    token = getattr(settings, "AYLA_INTERNAL_API_TOKEN", "")
    if not base or not token:
        raise ConsentEventUnavailable("AYLA_BASE_URL or AYLA_INTERNAL_API_TOKEN not configured")
    try:
        url = AylaUrlBuilder(base).build(PATH)
    except AylaUrlError as exc:
        raise ConsentEventUnavailable(f"invalid AYLA_BASE_URL: {exc}") from exc

    headers = with_request_id(
        {
            "Authorization": f"Bearer {token}",
            "X-External-User-ID": external_user_id,
            "Content-Type": "application/json",
        }
    )
    ref = external_user_log_ref(external_user_id)
    timeout = httpx.Timeout(READ_TIMEOUT_S, connect=CONNECT_TIMEOUT_S)
    try:
        with httpx.Client(timeout=timeout) as http:
            resp = http.post(url, headers=headers, json=body)
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        logger.warning(
            "consent_events.network_failure ext_ref=%s event=%s exc=%s",
            ref,
            body.get("event_id"),
            type(exc).__name__,
        )
        raise ConsentEventUnavailable(f"network: {type(exc).__name__}") from exc

    if resp.status_code >= 500:
        raise ConsentEventUnavailable(f"server: HTTP {resp.status_code}")
    if resp.status_code == 429:
        retry_after = resp.headers.get("Retry-After", "")
        logger.warning(
            "consent_events.throttled ext_ref=%s event=%s retry_after=%s",
            ref,
            body.get("event_id"),
            retry_after,
        )
        raise ConsentEventUnavailable(f"throttled: HTTP 429 retry_after={retry_after}")
    if resp.status_code in (400, 401, 403):
        try:
            detail: Any = resp.json()
        except ValueError:
            detail = resp.text[:300]
        level = logging.ERROR if resp.status_code in (401, 403) else logging.WARNING
        logger.log(
            level,
            "consent_events.rejected ext_ref=%s event=%s status=%d",
            ref,
            body.get("event_id"),
            resp.status_code,
        )
        raise ConsentEventRejected(resp.status_code, detail)
    if resp.status_code != 200:
        raise ConsentEventContractViolation(f"undeclared status: HTTP {resp.status_code}")

    try:
        payload = resp.json()
    except ValueError as exc:
        raise ConsentEventContractViolation(f"malformed json: {exc}") from exc
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        raise ConsentEventContractViolation(f"no data envelope in body: {str(payload)[:300]}")
    outcome = data.get("outcome")
    if outcome not in DELIVERED_OUTCOMES:
        raise ConsentEventContractViolation(f"no declared outcome in body: {str(payload)[:300]}")
    erased = data.get("erased") or ()
    if not isinstance(erased, list | tuple) or not all(isinstance(x, str) for x in erased):
        raise ConsentEventContractViolation(f"erased is not a list of field names: {erased!r}")
    return ConsentEventReceipt(
        event_id=str(data.get("event_id") or body.get("event_id") or ""),
        outcome=outcome,
        erased=tuple(erased),
    )
