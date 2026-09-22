"""Стойкий отказ распознавателя фото в каталоге → страница операторам в MAX (DRF-2318).

Живой проход владельца 22.09: OpenAI ответил каталогу 429 ``billing_not_active``,
резерв тоже отказал, и человек получил «попробуй через минуту». Такой отказ
чинит человек (оплата, ключ, квота), а не ретрай. Каталог (#549) публикует
``system.module.health.degraded`` с ``module_name="nutrition.food_scan.provider"``
и метрикой ``{provider, reason, hour}``, потребитель ``eventbus.consumers.system``
отдаёт её сюда, а :func:`signal_scan_provider_down` поднимает страницу
(``alerting.page``: MAX, маскирование PII). Рельс и дедуп — как у dead-letter
outbox (:mod:`apps.observability.outbox_dead_alert`, DRF-2306).

# Одна страница на провайдер, причину и час
Каталог уже дедуплицирует по UTC-часу; здесь второй рубеж — ключ того же часа.

# Чужие поля не попадают в текст как есть
Провайдер — только из закрытого набора, иначе «?»; причина — только из
закрытого набора, иначе «не указана»; час — только ISO, иначе молчание: он
уходит в ключ дедупа. Людей в метрике нет по построению.

# Недоставленная страница
Час занимается ДО отправки, недоставленная страница час возвращает, исход
``not_delivered`` — вызывающий отказывается принять событие, и outbox
каталога повторит доставку.
"""

from __future__ import annotations

import logging
import re
from typing import Final, Literal

from django.core.cache import cache

from apps.observability.alerting import page

logger = logging.getLogger(__name__)

Outcome = Literal["delivered", "skipped", "not_delivered"]

#: Провайдеры распознавания каталога (``FOOD_SCANNER_PRIMARY`` / ``..._FALLBACK``).
_PROVIDERS: Final = frozenset({"openai", "yandex"})

#: Что значит причина оператору — что чинить.
_REASONS: Final[dict[str, str]] = {
    "billing_not_active": "Счёт у провайдера не активен — нужна оплата.",
    "quota_exhausted": "Квота провайдера исчерпана — пополнить или поднять лимит.",
    # Слово причины, не секрет.
    "invalid_api_key": (  # pragma: allowlist secret
        "Ключ API отвергнут провайдером — проверить ключ в окружении каталога."
    ),
    "auth_rejected": "Провайдер отверг ключ или каталог (401/403) — проверить доступ.",
    "not_configured": "Ключ провайдера не задан в окружении каталога.",
}
_HOUR: Final = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}")
_DEDUP_KEY: Final = "scan_provider_alert:{provider}:{reason}:{hour}"
#: Двое суток: час зашит в ключ, TTL нужен, чтобы пережить опоздавшую доставку.
_DEDUP_TTL_SECONDS: Final = 48 * 60 * 60


def _claim(key: str) -> bool:
    """Занять час. Потеря кэша — «уже звучало»: молчание, а не шквал."""
    try:
        return bool(cache.add(key, 1, timeout=_DEDUP_TTL_SECONDS))
    except Exception as exc:  # noqa: BLE001 — потеря кэша не роняет ingest
        logger.warning("observability.scan_provider.dedup_unavailable err=%s", type(exc).__name__)
        return False


def _release(key: str) -> None:
    try:
        cache.delete(key)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "observability.scan_provider.dedup_release_failed err=%s", type(exc).__name__
        )


def signal_scan_provider_down(*, provider: object, reason: object, hour: object) -> Outcome:
    """Поднять страницу о стойком отказе распознавателя. Никогда не бросает."""
    if not isinstance(hour, str) or not _HOUR.fullmatch(hour):
        logger.warning("observability.scan_provider.hour_invalid")
        return "skipped"
    provider_s = provider if isinstance(provider, str) and provider in _PROVIDERS else "?"
    reason_s = reason if isinstance(reason, str) and reason in _REASONS else None

    key = _DEDUP_KEY.format(provider=provider_s, reason=reason_s or "unknown", hour=hour)
    if not _claim(key):
        return "skipped"

    title = f"Каталог: распознавание фото не работает — {provider_s}"
    body = (
        f"{_REASONS.get(reason_s or '', 'Причина не названа каталогом.')}\n"
        "Через минуту это не пройдёт: людям бот предлагает записать еду словами.\n\n"
        f"провайдер: {provider_s}\n"
        f"причина: {reason_s or 'не указана'}\n"
        f"час (UTC): {hour}"
    )
    try:
        delivered = page(
            "error", title, body, dedup_key=f"scan_provider:{provider_s}:{reason_s}:{hour}"
        )
    except Exception as exc:  # noqa: BLE001 — страж не роняет то, что стережёт
        logger.warning("observability.scan_provider.page_failed err=%s", type(exc).__name__)
        delivered = False
    if not delivered:
        logger.warning(
            "observability.scan_provider.page_not_delivered provider=%s reason=%s hour=%s",
            provider_s,
            reason_s,
            hour,
        )
        _release(key)
        return "not_delivered"
    return "delivered"


__all__ = ["signal_scan_provider_down"]
