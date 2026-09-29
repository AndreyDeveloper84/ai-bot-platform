"""Отмена записи из Django-админки — через ту же машину состояний, что и API (DRF-1498).

### Почему это обёртка, а не новый код перехода

Правило эпика DRF-75: «Admin вызывает те же services что и API». Экран
админки не правит ``status`` руками и не знает допустимых переходов —
он зовёт :func:`cancel_booking_from_admin`, а та проходит ровно тот же
путь, что Mini App API: :func:`~apps.booking.services.transitions.request_cancel`
→ :func:`~apps.booking.services.transitions.commit_cancel`. События
шины (``booking.cancel_requested`` / ``booking.cancelled``), строки
аудита и отмена ожидающих напоминаний получаются теми же самыми,
потому что порождаются тем же самым кодом.

Новых переходов здесь нет и быть не может: админке доступно ровно то,
что уже умеет машина состояний.

### Актер

Машина состояний работает от лица владельца записи (``BotUser``) —
переход «клиент отменил свой визит». Сотрудник админки не является
``BotUser``, поэтому переход выполняется от имени владельца записи,
а автор-человек (кто нажал кнопку и зачем) фиксируется в журнале
отдельной строкой ``admin.booking.cancelled`` — с логином сотрудника
и обязательной причиной. У записи без привязанного клиента
(``bot_user=None``, например после GDPR-стирания) отменить через
админку нельзя: машине состояний некем идти по переходу, а изобретать
«админский» переход задача запрещает.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from apps.audit.services import write_audit
from apps.booking.models import BookingRequest
from apps.booking.services.transitions import commit_cancel, request_cancel

if TYPE_CHECKING:  # pragma: no cover - только для аннотаций
    from django.contrib.auth.models import AbstractUser

#: Глагол журнала для отмены, сделанной руками через админку.
AUDIT_ADMIN_CANCEL = "admin.booking.cancelled"


class AdminCancelError(Exception):
    """Отмена из админки невозможна.

    Сообщение безопасно показать сотруднику как есть — без трассировки
    и без внутренних деталей.
    """


def cancel_booking_from_admin(
    booking: BookingRequest,
    *,
    staff_user: "AbstractUser",
    reason_text: str | None,
) -> BookingRequest:
    """Отменить запись действием из админки. Возвращает обновлённую строку.

    Args:
      booking: запись (догрузка и блокировка — внутри transitions).
      staff_user: сотрудник, нажавший «Отменить». Попадает в журнал.
      reason_text: причина отмены. Обязательна: пустая причина —
        это отмена, которую потом невозможно объяснить.

    Raises:
      AdminCancelError: причина пуста или у записи нет владельца
        (``bot_user=None``) — см. модуль-docstring.
      InvalidBookingTransition: запись в состоянии, из которого машина
        отмену не разрешает (уже отменена, визит завершён и т.п.).
    """
    reason = (reason_text or "").strip()
    if not reason:
        raise AdminCancelError("Причина отмены обязательна.")

    actor = booking.bot_user
    if actor is None:
        raise AdminCancelError(
            "У записи нет привязанного клиента — машина состояний не может "
            "выполнить отмену. Такие записи разбираются вручную."
        )

    # Тот же путь, что и Mini App API: request → commit. События шины,
    # строки аудита transitions и отмена pending-напоминаний — из него.
    row = request_cancel(booking, actor=actor, reason_class="other", reason_text=reason)
    row = commit_cancel(row, actor=actor)

    write_audit(
        AUDIT_ADMIN_CANCEL,
        target="BookingRequest",
        target_id=row.pk,
        payload={
            "actor_pk": staff_user.pk,
            "actor_username": staff_user.get_username(),
            "booking_id": str(row.pk),
            "reason": reason,
            "source": "django_admin",
        },
    )
    return row


__all__ = [
    "AUDIT_ADMIN_CANCEL",
    "AdminCancelError",
    "cancel_booking_from_admin",
]
