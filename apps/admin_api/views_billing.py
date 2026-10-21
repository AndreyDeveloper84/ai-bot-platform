"""Подписка салона на админской поверхности Mini App (DRF-2988, PR-B3).

До B3 биллинг жил только в Mini App мастера (``master_api`` ``billing/*``), а
B2 закрывает при заморозке всю админскую поверхность. Владелец салона часто
не мастер — заплатить ему было неоткуда, вопреки В-4 («экран Биллинг доступен
при заморозке»). Здесь — пять ручек поверх C3 каталога
(``internal/billing/tenants/{id}/…``); все пять стоят в ``OPEN_WHEN_FROZEN``
(``apps.tenancy.billing_gate``).

Кто что может (решение исполнителя, владельцем не утверждено):

* статус, привязка карты, оплата долга — владелец и админ: снять заморозку
  должен уметь тот, кто ведёт салон, а не только его хозяин;
* отмена подписки и переход соло → салон — только владелец: это решения о
  деньгах салона, а не операционная работа.

Каталог проверяет ещё раз: card-setup / cancel / convert-to-salon требуют,
чтобы ``X-External-User-ID`` был активным admin тенанта **в каталоге**.
Владелец в боте не обязан им быть (две базы) — тогда каталог отвечает 404,
и ручка говорит это словами, а не «не найдено».

``pay-debt`` и ``status`` каталог субъекта не проверяет — роль держит
только эта дверь.

Всё за ``BILLING_ENABLED`` бота: выключен — 404 ``billing_disabled`` без
похода в каталог.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable
from functools import wraps
from typing import Any

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from apps.admin_api.auth import require_admin_role
from apps.integrations.ayla.billing_client import (
    AylaBillingClient,
    BillingChargesDisabledError,
    BillingClientError,
    BillingConflictError,
    BillingDisabledError,
    BillingNotFoundError,
    BillingProxyError,
)
from apps.integrations.ayla.user_proxy import external_user_id_for
from apps.tenancy.billing_gate import billing_enabled

logger = logging.getLogger(__name__)

#: 409 каталога, которые экран различает. Прочие коды — ``billing_conflict``.
_CONFLICT_SLUGS = {
    "NO_DEBT": "no_debt",
    "NO_TENANT_ADMIN": "no_tenant_admin",
    "CONSENT_VERSION_MISMATCH": "consent_version_mismatch",
    "PRICE_MISMATCH": "price_mismatch",
    "ALREADY_SALON": "already_salon",
}


def _billing_client() -> AylaBillingClient:
    return AylaBillingClient()


def _error(
    slug: str, detail: str, status: int, details: dict[str, Any] | None = None
) -> JsonResponse:
    body: dict[str, Any] = {"error": slug, "detail": detail}
    if details:
        body["details"] = details
    return JsonResponse(body, status=status)


def _body(request: HttpRequest) -> tuple[dict[str, Any] | None, JsonResponse | None]:
    try:
        body = json.loads(request.body or b"{}")
    except ValueError:
        return None, _error("malformed", "body is not valid JSON", 400)
    if not isinstance(body, dict):
        return None, _error("malformed", "body must be a JSON object", 400)
    return body, None


def _string_field(body: dict[str, Any], name: str) -> tuple[str, JsonResponse | None]:
    # DRF-2681: значение уходит в платёжный контур каталога — нестроковое
    # получает отказ этой двери, а не становится текстом "{'a': 1}".
    value = body.get(name)
    if value is not None and not isinstance(value, str):
        return "", _error("validation_error", f"{name} must be a string", 400)
    return str(value or "").strip(), None


def _https_url(name: str, value: str) -> JsonResponse | None:
    # Адрес возврата из платёжной формы: только https (каталог требует того же).
    if value and not value.startswith("https://"):
        return _error("validation_error", f"{name} must be an https URL", 400)
    return None


def _billing_on(view: Callable[..., HttpResponse]) -> Callable[..., HttpResponse]:
    """``BILLING_ENABLED`` бота выключен — 404 до разбора тела и без каталога."""

    @wraps(view)
    def wrapper(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        if not billing_enabled():
            return _error("billing_disabled", "billing is not enabled", 404)
        return view(request, *args, **kwargs)

    return wrapper


#: 404 каталога, которые значат «нет подписки», а не «вы не admin» (C3).
_MISSING_SUBSCRIPTION_CODES = frozenset({"SUBSCRIPTION_NOT_FOUND"})


def _owner_only(request: HttpRequest) -> JsonResponse | None:
    if request.role_context.is_owner:  # type: ignore[attr-defined]
        return None
    return _error("owner_only", "только владелец салона может это сделать", 403)


def _call(
    request: HttpRequest,
    action: str,
    fn: Callable[[AylaBillingClient], dict[str, Any]],
    *,
    second_factor: bool = False,
) -> JsonResponse:
    """Позвать каталог и перевести его ответ в отказ этой поверхности."""

    tenant = request.tenant  # type: ignore[attr-defined]
    try:
        with _billing_client() as client:
            data = fn(client)
    except BillingDisabledError:
        return _error("billing_disabled", "billing is not enabled in the catalog", 404)
    except BillingNotFoundError as exc:
        if second_factor and exc.code not in _MISSING_SUBSCRIPTION_CODES:
            # Каталог прячет «вы не admin этого тенанта» за 404 (C3).
            return _error(
                "billing_subject_not_admin",
                "в каталоге ваша учётная запись не админ этого салона",
                404,
            )
        return _error("billing_not_found", "subscription or salon not found in the catalog", 404)
    except BillingConflictError as exc:
        code = exc.code or ""
        return _error(
            _CONFLICT_SLUGS.get(code, "billing_conflict"),
            f"catalog refused: {code or 'conflict'}",
            409,
            exc.details,
        )
    except BillingChargesDisabledError:
        return _error("billing_charges_disabled", "charges are not enabled yet", 503)
    except BillingClientError as exc:
        # 4xx каталога — чаще всего расхождение контракта самого бота: в журнал.
        logger.warning(
            "admin_api.billing.rejected action=%s tenant=%s error=%s",
            action,
            tenant.pk,
            str(exc)[:200],
        )
        return _error("validation_error", "request rejected by billing upstream", 400)
    except BillingProxyError as exc:
        logger.warning(
            "admin_api.billing.upstream action=%s tenant=%s error=%s",
            action,
            tenant.pk,
            type(exc).__name__,
        )
        return _error("billing_upstream_unavailable", "billing upstream is unavailable", 502)
    logger.info("admin_api.billing.%s tenant=%s", action, tenant.pk)
    return JsonResponse({"data": data})


@require_http_methods(["GET"])
@require_admin_role
@_billing_on
def billing_status(request: HttpRequest) -> HttpResponse:
    """Подписка салона из каталога — ``{tenant_id, subscription|null}`` verbatim."""

    tenant_id = str(request.tenant.pk)  # type: ignore[attr-defined]
    return _call(request, "status", lambda c: c.get_tenant_status(tenant_id=tenant_id))


@csrf_exempt
@require_http_methods(["POST"])
@require_admin_role
@_billing_on
def billing_card_setup(request: HttpRequest) -> HttpResponse:
    """Привязать карту салона. Body: ``{return_url, consent_version?}`` → ``{confirmation_url}``."""

    body, refusal = _body(request)
    if refusal is not None:
        return refusal
    assert body is not None
    return_url, refusal = _string_field(body, "return_url")
    if refusal is not None:
        return refusal
    consent_version, refusal = _string_field(body, "consent_version")
    if refusal is not None:
        return refusal
    if not return_url:
        return _error("validation_error", "return_url is required", 400)
    refusal = _https_url("return_url", return_url)
    if refusal is not None:
        return refusal

    tenant_id = str(request.tenant.pk)  # type: ignore[attr-defined]
    subject = external_user_id_for(request.bot_user)  # type: ignore[attr-defined]
    return _call(
        request,
        "card_setup",
        lambda c: c.tenant_card_setup(
            tenant_id=tenant_id,
            external_user_id=subject,
            return_url=return_url,
            consent_version=consent_version,
        ),
        second_factor=True,
    )


@csrf_exempt
@require_http_methods(["POST"])
@require_admin_role
@_billing_on
def billing_pay_debt(request: HttpRequest) -> HttpResponse:
    """Оплатить долг салона. Body: ``{return_url?}`` — нужен, если карты нет."""

    body, refusal = _body(request)
    if refusal is not None:
        return refusal
    assert body is not None
    return_url, refusal = _string_field(body, "return_url")
    if refusal is not None:
        return refusal
    refusal = _https_url("return_url", return_url)
    if refusal is not None:
        return refusal

    tenant_id = str(request.tenant.pk)  # type: ignore[attr-defined]
    return _call(
        request,
        "pay_debt",
        lambda c: c.tenant_pay_debt(tenant_id=tenant_id, return_url=return_url),
    )


@csrf_exempt
@require_http_methods(["POST"])
@require_admin_role
@_billing_on
def billing_cancel(request: HttpRequest) -> HttpResponse:
    """Отменить подписку салона (только владелец). → ``{status, effective_at}``."""

    refusal = _owner_only(request)
    if refusal is not None:
        return refusal
    body, refusal = _body(request)
    if refusal is not None:
        return refusal
    assert body is not None
    key, refusal = _string_field(body, "idempotency_key")
    if refusal is not None:
        return refusal

    tenant_id = str(request.tenant.pk)  # type: ignore[attr-defined]
    subject = external_user_id_for(request.bot_user)  # type: ignore[attr-defined]
    idempotency_key = key or uuid.uuid4().hex
    return _call(
        request,
        "cancel",
        lambda c: c.tenant_cancel(
            tenant_id=tenant_id, external_user_id=subject, idempotency_key=idempotency_key
        ),
        second_factor=True,
    )


@csrf_exempt
@require_http_methods(["POST"])
@require_admin_role
@_billing_on
def billing_convert_to_salon(request: HttpRequest) -> HttpResponse:
    """Перейти с «Соло» на «Салон» (В-2, только владелец).

    Body: ``{confirmed_price: "990.00", idempotency_key?}`` — цена, которую
    владелец увидел на экране; каталог сверяет её (409 ``price_mismatch`` с
    ``details.current_price``). → ``{tariff: "salon", effective_from}``.
    """

    refusal = _owner_only(request)
    if refusal is not None:
        return refusal
    body, refusal = _body(request)
    if refusal is not None:
        return refusal
    assert body is not None
    confirmed_price, refusal = _string_field(body, "confirmed_price")
    if refusal is not None:
        return refusal
    key, refusal = _string_field(body, "idempotency_key")
    if refusal is not None:
        return refusal
    if not confirmed_price:
        return _error("validation_error", "confirmed_price is required", 400)

    tenant_id = str(request.tenant.pk)  # type: ignore[attr-defined]
    subject = external_user_id_for(request.bot_user)  # type: ignore[attr-defined]
    idempotency_key = key or uuid.uuid4().hex
    return _call(
        request,
        "convert_to_salon",
        lambda c: c.tenant_convert_to_salon(
            tenant_id=tenant_id,
            external_user_id=subject,
            idempotency_key=idempotency_key,
            confirmed_price=confirmed_price,
        ),
        second_factor=True,
    )


__all__ = [
    "billing_cancel",
    "billing_card_setup",
    "billing_convert_to_salon",
    "billing_pay_debt",
    "billing_status",
]
