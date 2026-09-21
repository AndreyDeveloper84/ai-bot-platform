"""Вид рабочего пространства — ``Tenant.kind`` каталога (DRF-2254).

Единственный источник ответа на вопрос «чьё место и кто ведёт услуги —
сам мастер (``solo``) или владелец салона (``salon``)». Бот этот признак не
выводит и не хранит: читает у каталога (ADR-0009 — каталог SoR тенанта) и
отдаёт в ``/me`` как ``workspace_kind``. ``is_solo_provider`` (подсчёт людей,
:mod:`apps.identity.services.solo_onboarding`) остаётся только подсказкой
раскладки Mini App.

Числа — решение главного окна DRF-2254, не выдуманы здесь:

* таймаут чтения — 1.5 с, как у пробы DRF-2225, и мимо общего breaker брони:
  тормоз каталога не должен задерживать загрузку Mini App;
* кэш — 10 минут на тенант. ``kind`` пишет только provisioning каталога и
  сейчас не меняется; когда его начнут менять (заполнение до G4, переход
  соло→команда — решения владельца), добавят сброс по событию.

Незнание — ``None``, и потребители ведут себя «как сейчас»: экраны
самообслуживания показаны, авторитетен отказ каталога. ``None`` не кэшируется —
следующая загрузка спросит снова.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from django.conf import settings
from django.core.cache import cache

from apps.integrations.ayla.booking_client import get_ayla_booking_client

logger = logging.getLogger(__name__)

WorkspaceKind = Literal["salon", "solo"]

#: Закрытый список вида — как ``Tenant.Kind`` каталога.
WORKSPACE_KINDS: frozenset[str] = frozenset({"salon", "solo"})

#: Таймаут чтения в ``/me`` — решение DRF-2254 (как проба DRF-2225).
WORKSPACE_KIND_TIMEOUT_S = 1.5

#: Кэш на тенант — решение DRF-2254: 10 минут.
WORKSPACE_KIND_CACHE_TTL_S = 600


def _cache_key(tenant_id: Any) -> str:
    return f"identity:workspace_kind:{tenant_id}"


def workspace_kind(tenant_id: Any) -> WorkspaceKind | None:
    """``salon`` | ``solo`` каталога для тенанта, либо ``None`` — не знаю."""

    if not getattr(settings, "AYLA_BASE_URL", "") or not getattr(
        settings, "AYLA_INTERNAL_API_TOKEN", ""
    ):
        return None

    key = _cache_key(tenant_id)
    try:
        cached = cache.get(key)
    except Exception:  # noqa: BLE001 — кэш упал: читаем у каталога
        cached = None
    if cached in WORKSPACE_KINDS:
        return cached  # type: ignore[return-value]

    try:
        kind = get_ayla_booking_client().get_tenant_kind(
            tenant_id=tenant_id,
            timeout_s=WORKSPACE_KIND_TIMEOUT_S,
            feeds_circuit=False,
        )
    except Exception as exc:  # noqa: BLE001 — любой отказ: «не знаю», как сейчас
        logger.warning("identity.workspace_kind.unavailable err=%s", type(exc).__name__)
        return None

    if kind not in WORKSPACE_KINDS:
        if kind is not None:
            logger.warning("identity.workspace_kind.unexpected value=%r", kind)
        return None

    try:
        cache.set(key, kind, WORKSPACE_KIND_CACHE_TTL_S)
    except Exception:  # noqa: BLE001 — не закэшировали: следующий раз спросим снова
        logger.warning("identity.workspace_kind.cache_set_failed")
    return kind  # type: ignore[return-value]


__all__ = [
    "WORKSPACE_KINDS",
    "WORKSPACE_KIND_CACHE_TTL_S",
    "WORKSPACE_KIND_TIMEOUT_S",
    "WorkspaceKind",
    "workspace_kind",
]
