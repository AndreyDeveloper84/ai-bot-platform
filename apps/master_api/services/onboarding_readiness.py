"""Готовность онбординга мастера — проекция по фактам, без хранимого состояния (DRF-1794, M2).

Экран 01 макета («[имя], всё готово!») обещает чек-лист «Осталось настроить»
и «Настройку можно прервать и продолжить позже — всё сохранится». Слово
владельца (PROMPT §19): readiness — проекция, ``onboarding_completed=true``
не хранится, если его можно вычислить из authoritative-фактов.

Здесь пять пунктов, и у каждого — свой источник истины и своё «не знаю»:

===============  ==========================================================  ==========================
пункт            факт                                                        когда ``unknown``
===============  ==========================================================  ==========================
``services``     активные строки ``MasterService`` с ценой и длительностью   —
``hours``        недельный шаблон из ``load_day_frame`` (Ayla при флаге)     канон не читается (DRF-1111)
``profile``      ``name`` и ``photo_url`` у ``CatalogMaster``                —
``location``     места работы — возможности ещё нет (M11/M19)                всегда ``unavailable``
``identity``     ``SoloIdentityLink.status`` соло-мастера / столбец ключа    —
===============  ==========================================================  ==========================

Три правила, ради которых модуль не свёрнут в четыре булева:

* **Незнание — не «нет».** Когда Ayla не отвечает, пункт ``hours`` получает
  ``state="unknown"`` с именем причины, а не ``missing``: «настройте
  расписание» человеку, который его настроил, — ложь, и та же ложь в
  обратную сторону (DRF-1111: отказываться, а не гадать).
* **Несуществующая возможность — не «сделано» и не «не сделано».** Пункт
  ``location`` отвечает ``unavailable`` с ``capability_not_built``: экран
  честно не рисует то, чего некуда сохранить, а ``ready`` не становится
  истиной по умолчанию — ``blocking`` называет причину.
* **Один гейт продажи.** ``setup_state``/``sale_block`` берутся из
  :func:`apps.catalog.master_state.sale_block` — того же, что читают
  витрина, ростер и бронь; второго определения «опубликован» здесь нет.

Числа в ответе — только те, что посчитаны здесь из строк (§3 карты:
«счётчики — только от сервера»); процентов и «шаг N из M» нет намеренно.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date as date_cls
from datetime import timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from django.utils import timezone

from apps.catalog.master_state import IDENTITY_LINKED, SaleBlock, sale_block
from apps.catalog.models import CatalogMaster
from apps.integrations.ayla.salon_client import SalonAPIError, SalonNotConfigured, SalonUnavailable
from apps.master_api.services.catalog import list_master_services
from apps.master_api.services.schedule_frame import load_day_frame

logger = logging.getLogger(__name__)

ItemState = Literal["done", "missing", "unknown", "unavailable"]

#: Пункты, без которых ``ready`` не бывает истиной. ``identity`` сюда не
#: входит: связь — условие ПУБЛИКАЦИИ (ruling 6), а не настройки; она
#: отдаётся отдельным полем, чтобы экран показал «ожидает оператора», не
#: смешивая с тем, что мастер может сделать сам.
REQUIRED_ITEMS: tuple[str, ...] = ("services", "location", "hours", "profile")

#: Куда ведёт каждый пункт — маршруты соло-поверхности (``App.tsx`` /solo/*).
DEEP_LINKS: dict[str, str] = {
    "services": "/solo/services",
    "location": "/solo/settings",
    "hours": "/solo/schedule",
    "profile": "/solo/profile",
}


@dataclass(frozen=True)
class ReadinessItem:
    key: str
    state: ItemState
    detail: dict[str, Any] = field(default_factory=dict)
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "state": self.state,
            "detail": dict(self.detail),
            "reason": self.reason,
            "deep_link": DEEP_LINKS[self.key],
        }


@dataclass(frozen=True)
class Readiness:
    items: tuple[ReadinessItem, ...]
    identity: dict[str, Any]
    sale_block: SaleBlock | None

    @property
    def blocking(self) -> list[str]:
        """Пункты, из-за которых ``ready`` ложно — с состоянием, не только именем."""

        return [
            f"{item.key}:{item.state}"
            for item in self.items
            if item.key in REQUIRED_ITEMS and item.state != "done"
        ]

    @property
    def ready(self) -> bool:
        return not self.blocking

    @property
    def setup_state(self) -> str:
        return "READY" if self.sale_block is None else "SETUP_PENDING"

    def as_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "blocking": self.blocking,
            "items": [item.as_dict() for item in self.items],
            "identity": dict(self.identity),
            "setup_state": self.setup_state,
            "sale_block": self.sale_block,
        }


def build_readiness(master: CatalogMaster) -> Readiness:
    """Собрать проекцию по живым фактам. Ничего не пишет."""

    return Readiness(
        items=(
            _services_item(master),
            _location_item(),
            _hours_item(master),
            _profile_item(master),
        ),
        identity=identity_facts(master),
        sale_block=sale_block(master),
    )


# ─── пункты ──────────────────────────────────────────────────────────────────


def _services_item(master: CatalogMaster) -> ReadinessItem:
    rows = list_master_services(master=master)
    configured = [r for r in rows if r["price_rub"] is not None and (r["duration_min"] or 0) > 0]
    detail = {"selected": len(rows), "configured": len(configured)}
    return ReadinessItem("services", "done" if configured else "missing", detail)


def _location_item() -> ReadinessItem:
    # M11/M19: мест работы у мастера из бота пока некуда сохранить — ни
    # ручки, ни зеркала. Это не «не настроено» (мастер ничего не мог
    # сделать) и не «настроено».
    return ReadinessItem("location", "unavailable", {}, reason="capability_not_built")


def _hours_item(master: CatalogMaster) -> ReadinessItem:
    today = timezone.now().date()
    tz = ZoneInfo(getattr(master.tenant, "timezone", None) or "Europe/Moscow")
    try:
        weekly, _exceptions, _blocks = load_day_frame(
            master,
            from_date=today,
            to_date=_a_week_from(today),
            tz=tz,
        )
    except (SalonNotConfigured, SalonUnavailable, SalonAPIError) as exc:
        # Канон не читается — отказ, не догадка (DRF-1111).
        reason = type(exc).__name__
        logger.info("master.readiness.hours_unknown master=%s reason=%s", master.id, reason)
        return ReadinessItem("hours", "unknown", {}, reason=reason)

    working_days = sorted(
        weekday for weekday, row in weekly.items() if bool(getattr(row, "is_working", False))
    )
    detail = {"working_days": working_days}
    return ReadinessItem("hours", "done" if working_days else "missing", detail)


def _profile_item(master: CatalogMaster) -> ReadinessItem:
    has_name = bool((master.name or "").strip())
    has_photo = bool((master.photo_url or "").strip())
    detail = {"name": has_name, "photo": has_photo, "bio": bool((master.bio or "").strip())}
    # Фото обязательно для публикации, не для сохранения (P57) — но пункт
    # «Профиль» чек-листа закрыт только когда есть и имя, и фото.
    return ReadinessItem("profile", "done" if has_name and has_photo else "missing", detail)


def identity_facts(master: CatalogMaster) -> dict[str, Any]:
    """Состояние связи — то, что читает гейт продажи, словами для экрана.

    Соло-мастер несёт строку ``SoloIdentityLink``; у мастера салона её нет,
    и вопрос решает столбец ``ayla_user_id`` (как в ``master_state``).
    """

    link = getattr(master, "identity_link", None)
    if link is None:
        state = "linked" if master.ayla_user_id is not None else "unlinked"
        return {"state": state, "link_status": None}
    if link.status == IDENTITY_LINKED:
        state = "linked"
    elif link.status == "IDENTITY_LINK_REJECTED":
        state = "rejected"
    else:
        state = "pending"
    return {"state": state, "link_status": link.status}


def _a_week_from(day: date_cls) -> date_cls:
    return day + timedelta(days=6)


__all__ = ["Readiness", "ReadinessItem", "REQUIRED_ITEMS", "build_readiness", "identity_facts"]
