"""Public marketplace directory HTTP views (#249, #250).

The bot-side surface of the Ayla provider directory (EPIC #1014): anonymous,
nationwide browsing of bookable masters across ALL pilot salons. "Provider"
is the existing catalog master surfaced via the public :class:`MasterCard`
DTO (founder decision #1018 — Provider ≡ Master).

Endpoints (mounted at ``/api/v1/providers/`` from :mod:`config.urls`)
---------------------------------------------------------------------

* ``GET /`` (#249) — page-based list of bookable masters. Optional
  ``?city=`` / ``?specialization=`` filters, ``?page=`` / ``?page_size=``
  pagination (default + max clamp, applied in
  :func:`apps.marketplace.discovery.discover_masters_page`).
* ``GET /<uuid>/`` (#250) — a single master's public card; 404 if unknown
  or not bookable. Contract-first (no current bot-side consumer); returns
  the exact same public card as the list — the DTO is NOT extended.

Both are PUBLIC (no auth decorator) and emit JSON. Serialization is
hand-rolled from the :class:`MasterCard` DTO — which already carries public
fields only — so commercial / identity catalog state can never leak
(JSON-guard, asserted in tests). Errors use the platform shape
``{"error": "<slug>", "detail": "<message>"}``.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.http import require_http_methods

from apps.marketplace.discovery import MasterCard, discover_masters_page, get_master


def _error(slug: str, detail: str, status: int) -> JsonResponse:
    return JsonResponse({"error": slug, "detail": detail}, status=status)


def _card_to_dict(card: MasterCard) -> dict[str, Any]:
    """Serialize the public DTO. Enumerated by hand (no ``**vars``) so the
    JSON surface stays exactly the sanctioned public fields — the projection
    already happened in ``discovery._to_card``; this is the wire shape."""
    return {
        "tenant_id": str(card.tenant_id),
        "master_id": str(card.master_id),
        "name": card.name,
        "specialization": card.specialization,
        "rating": str(card.rating) if card.rating is not None else None,
        "photo_url": card.photo_url,
        "city": card.city,
    }


def _parse_positive_int(raw: str | None, default: int) -> int | None:
    """Parse an optional positive-int query param. ``None``/absent → default;
    a non-integer or non-positive value → ``None`` (signals 400 at caller)."""
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value >= 1 else None


@require_http_methods(["GET"])
def providers_list(request: HttpRequest) -> HttpResponse:
    """GET /api/v1/providers/ — public, page-based master directory (#249)."""
    page = _parse_positive_int(request.GET.get("page"), 1)
    page_size = _parse_positive_int(request.GET.get("page_size"), 0)  # 0 → discovery default
    if page is None or page_size is None:
        return _error("bad_request", "page and page_size must be positive integers", 400)

    city = request.GET.get("city") or None
    specialization = request.GET.get("specialization") or None

    # page_size==0 means "unset" → let discovery apply its default.
    cards, meta = discover_masters_page(
        city=city,
        specialization=specialization,
        page=page,
        **({"page_size": page_size} if page_size else {}),
    )
    return JsonResponse(
        {
            "providers": [_card_to_dict(c) for c in cards],
            "page": meta.page,
            "page_size": meta.page_size,
            "total_count": meta.total_count,
            "num_pages": meta.num_pages,
        }
    )


@require_http_methods(["GET"])
def provider_detail(request: HttpRequest, provider_id: UUID) -> HttpResponse:
    """GET /api/v1/providers/<uuid>/ — public single master card (#250)."""
    card = get_master(provider_id)
    if card is None:
        return _error("not_found", "provider not found or not bookable", 404)
    return JsonResponse({"provider": _card_to_dict(card)})
