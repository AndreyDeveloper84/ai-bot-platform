"""``GET /api/v1/admin/readiness/`` — салонная готовность поимённо (DRF-2117).

Прокси для admin Mini App (карточка «Готовность — N проблем» на «Сегодня»,
ayla-85) поверх :func:`apps.admin_api.services.salon_readiness.check_salon_readiness`:
каталог (``/internal/salons/<slug>/readiness/``) плюс зеркало
(``sale_block``). Только владелец / администратор — ``require_admin_role``:
ресепшну тройка закрыта (DRF-2115), и чтение готовности с ней.

Ответ: ``{ready, unknown, source_problem, checked_at, masters_total,
problems: [{master: {id, name}, code, text, origin}], limits: []}``. Источник
недоступен — ``200`` с ``ready=false``, ``unknown=true`` и одной строкой
``source`` (UNKNOWN = проблема, не сбой ручки): карточка обязана сказать
«не удалось проверить», а не пустой список.
"""

from __future__ import annotations

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from apps.admin_api.auth import require_admin_role
from apps.admin_api.services.salon_readiness import check_salon_readiness


@csrf_exempt
@require_http_methods(["GET"])
@require_admin_role
def salon_readiness(request: HttpRequest) -> HttpResponse:
    """Готовность салона поимённо — каталог + зеркало."""
    tenant = request.tenant  # type: ignore[attr-defined]
    return JsonResponse(check_salon_readiness(tenant).as_dict())
