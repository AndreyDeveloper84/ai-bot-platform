"""Один вход для сообщений персоналу салона — от салонного бота (DRF-2128).

Замер 19.09.2026 (бот ``dev 7a21f884``): :func:`~apps.channels.max.outbound.send_message`
берёт токен из ``bot_scope``, иначе — ``settings.MAX_BOT_TOKEN`` (legacy).
``bot_scope(salon)`` ставили два места (``booking/master_notify``,
``internal_chat/notify``); ещё девять писали персоналу без scope — и на
стенде legacy-токен **равен клиентскому**. Заявка мастера на график,
эскалация напоминания, «перенос не завершён», решение по заявке, каскад
деактивации — всё это уходило от клиентского аватара: персонал видел
рабочие сообщения не в рабочем боте, а кто клиентскому боту не писал —
не получал вовсе.

Здесь — единственная точка, где решается, **от кого** и **кому** уходит
сообщение персоналу:

* **от кого** — салонный бот, найденный по потоку ``max_salon``
  (:func:`~apps.channels.bot_registry.resolve_by_stream`; DRF-1705 —
  салонный бот салону не принадлежит, выбор по потоку, не по тенанту).
  Записи нет → отправка идёт legacy-токеном, как до этого листа, но с
  ``ERROR`` в логе: молчание и сделало этот дефект невидимым;
* **кому** — либо :data:`MANAGER` (управляющие салона: активные
  ``TenantStaff`` владелец/админ как люди по ``channel_user_id`` плюс
  ``manager_address(tenant)``, дедуп по значению), либо конкретный
  адрес — мастер по ``user_id``.

Провод один — ``outbound.send_message``, и он берётся атрибутом модуля,
а не именем: тесты соседей подменяют именно ``apps.channels.max.outbound.send_message``.

Текст сюда приходит готовым и не меняется: форматы — предмет DRF-2118,
отсутствие телефона клиента в них — DRF-2129 (сторож переехал на этот
вход: ``test_staff_outbound_2128`` p5).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from apps.channels.bot_context import bot_scope
from apps.channels.max import outbound
from apps.channels.max.addressing import MaxAddress, manager_address

logger = logging.getLogger(__name__)


class _Manager:
    """Сентинел «управляющим салона» — адресаты решаются здесь, не у вызывающего."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "MANAGER"


MANAGER = _Manager()


@dataclass(frozen=True)
class StaffSendResult:
    """Что вышло: сколько адресатов нашли, скольким ушло, скольким нет.

    ``recipients == 0`` — именованное отсутствие (в логе ``no_recipients``),
    а не сбой; ``failed > 0`` при ``sent == 0`` — сбой доставки, и
    вызывающий волен откатить своё состояние так же, как откатывал на
    ``MaxAPIError`` до этого листа.
    """

    recipients: int
    sent: int
    failed: int

    @property
    def delivered(self) -> bool:
        return self.sent > 0


def salon_bot():
    """Салонный бот платформы, или ``None``, когда его в реестре нет.

    Тот же выбор, что у ``booking/master_notify._salon_bot_for`` и
    ``internal_chat/notify._salon_bot_for``: по потоку, не по тенанту.
    Исключение реестра не ломает отправку — идентичность не дороже
    сообщения, но и не тихо: ``ERROR`` с причиной.
    """

    from apps.channels.bot_registry import SALON_STREAM, effective_registry, resolve_by_stream

    try:
        return resolve_by_stream(SALON_STREAM, effective_registry())
    except Exception:  # noqa: BLE001 — identity must never break the send
        logger.exception("channels.max.staff_outbound.registry_unavailable")
        return None


def manager_recipients(tenant: Any) -> tuple[MaxAddress, ...]:
    """Адреса управляющих салона: активные владелец/админ + адрес менеджера.

    Люди, не диалоги: ``TenantStaff.bot_user.channel_user_id`` верен для
    любого нашего бота, тогда как ``Tenant.manager_chat_id`` — диалог с
    тем ботом, из чьей переписки его скопировали (DRF-1559), и от
    салонного бота даст 404. Он всё же остаётся в списке: салон, у
    которого настроен только он, иначе замолчал бы в день выкладки, а
    404 в логе виднее тишины.

    ``TenantStaff.all_tenants`` намеренно: сюда заходят beat-задачи и
    ``on_commit``-хуки без тенантного контекста, а под ``strict``
    тенантный менеджер там бросает. Фильтр по ``tenant`` явный.
    Лёгкий двойник без ``pk`` (тесты, пара сервисов) — только
    ``manager_address``.
    """

    seen: set[str] = set()
    addresses: list[MaxAddress] = []

    def _add(address: MaxAddress) -> None:
        if address and address.value not in seen:
            seen.add(address.value)
            addresses.append(address)

    if getattr(tenant, "pk", None) is not None:
        from apps.tenancy.models import TenantStaff

        rows = (
            TenantStaff.all_tenants.filter(
                tenant=tenant,
                role__in=(TenantStaff.Role.OWNER, TenantStaff.Role.ADMIN),
                deactivated_at__isnull=True,
            )
            .select_related("bot_user")
            .order_by("role", "created_at")
        )
        for row in rows:
            _add(MaxAddress.resolve(user_id=getattr(row.bot_user, "channel_user_id", "")))

    _add(manager_address(tenant))
    return tuple(addresses)


def send_to_staff(
    tenant: Any,
    recipient: _Manager | MaxAddress,
    text: str,
    attachments: list[dict[str, Any]] | None = None,
    *,
    timeout: float = 10.0,
    propagate: bool = False,
) -> StaffSendResult:
    """Отправить ``text`` персоналу салона от салонного бота.

    Args:
      tenant: салон — для адресатов :data:`MANAGER` и для лога. Может быть
        ``None``, когда адрес уже известен (DM мастеру из задачи, где
        тенанта в kwargs нет).
      recipient: :data:`MANAGER` — управляющим салона
        (:func:`manager_recipients`); :class:`MaxAddress` — этому человеку.
      text: готовый текст; здесь не меняется.
      attachments: вложения в формате провода MAX (кнопки), как у
        :func:`~apps.channels.max.outbound.send_message`.

    Не бросает: сбой на одном адресе не отменяет остальных, а итог — в
    :class:`StaffSendResult`. Пустой адрес — ``recipients=0`` с
    ``WARNING no_recipients``, не исключение. Исключение —
    ``propagate=True``: исходная ошибка провода поднимается наружу как
    есть, для вызывающего со своей машиной повторов (Celery-задача с
    ``autoretry_for=(MaxAPIError,)`` обязана видеть именно
    ``MaxAPIError``, а не его пересказ).
    """

    slug = getattr(tenant, "slug", "-") if tenant is not None else "-"
    if isinstance(recipient, _Manager):
        addresses = manager_recipients(tenant)
    else:
        addresses = (recipient,) if recipient else ()

    if not addresses:
        logger.warning("channels.max.staff_outbound.no_recipients tenant=%s", slug)
        return StaffSendResult(recipients=0, sent=0, failed=0)

    bot = salon_bot()
    if bot is None:
        # Legacy как до листа — но громко: именно тишина держала дефект
        # невидимым. Токен по умолчанию на стенде — клиентский.
        logger.error(
            "channels.max.staff_outbound.no_salon_bot tenant=%s recipients=%d — "
            "нет записи max_salon в реестре, отправка legacy-токеном",
            slug,
            len(addresses),
        )

    sent = failed = 0
    with bot_scope(bot):
        for address in addresses:
            try:
                outbound.send_message(
                    **address.send_kwargs(), text=text, attachments=attachments, timeout=timeout
                )
                sent += 1
            except Exception:  # noqa: BLE001 — один адрес не отменяет остальных
                failed += 1
                logger.warning(
                    "channels.max.staff_outbound.send_failed tenant=%s %s",
                    slug,
                    address,
                    exc_info=True,
                )
                if propagate:
                    raise
    return StaffSendResult(recipients=len(addresses), sent=sent, failed=failed)


__all__ = ["MANAGER", "StaffSendResult", "manager_recipients", "salon_bot", "send_to_staff"]
