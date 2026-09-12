"""Стоп персонализации по живой заявке на удаление — бот-половина D2 (§7, DRF-1699).

§7: «персонализация и новая обработка данных прекращаются сразу» — с
момента приёма заявки, не с момента исполнения. Носитель заявки —
каталог (``users.DeletionRequest``); ходить туда на каждом ходу разговора
нельзя, поэтому у бота есть локальный флаг **на уровне человека** —
``UserPersonalContext.deletion_requested_at`` / ``deletion_request_id``
(ключ ``ayla_user_id``, как у ``forget_all_requested_at``).

### Прецедент — и чем он не годится как есть

``forget_all_requested_at`` уже закрывает память в окне «попросил забыть →
свип ещё не прошёл» (:func:`memory_reader.get_personal_context`). Но он
закрывает **пустым видом**: читатель не отличает «человека нет» от
«человек попросил его не помнить». Для заявки на удаление это не годится:
человек на любом экране обязан видеть номер и причину, а пустота
читалась бы как «новый человек» и включила бы сбор заново.

Отсюда форма: один предикат :func:`deletion_gate`, возвращающий
**имя** (:data:`DELETION_REQUESTED`) и ``request_id``; каждый из трёх
классов читателей — память, рекомендации, проактив — спрашивает его и
отвечает отказом с этим именем.

### Два флага не спорят

Если выставлены оба — ``forget_all_requested_at`` и
``deletion_requested_at`` — побеждает заявка на удаление, по имени: она
шире (не только память) и у неё есть номер, который человек видел.
Сторож: ``test_forget_all_and_deletion_do_not_argue``.

### Кто пишет флаг

Только :func:`mark_deletion_requested` — из пути приёма заявки
(``deletion_request.request_account_deletion``, сразу после успешного
POST в каталог и ДО показа успеха) и из чтения текущей заявки (догоняет
заявки, заведённые из приложения). Снимает :func:`clear_deletion_flag` —
исполнитель D3 по ``COMPLETED`` и то же чтение, когда каталог отвечает
«открытой нет».
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from django.utils import timezone

from apps.identity.models import UserPersonalContext

logger = logging.getLogger(__name__)

#: Имя причины — то же, что в каталоге (``users/deletion_requests.py``).
DELETION_REQUESTED = "deletion_requested"


@dataclass(frozen=True)
class DeletionGate:
    """Ответ предиката: закрыт ли человек и чем именно."""

    blocked: bool
    reason: str | None = None
    request_id: str | None = None

    @property
    def open(self) -> bool:
        return not self.blocked


OPEN = DeletionGate(blocked=False)


def deletion_gate(ayla_user_id: uuid.UUID | str | None) -> DeletionGate:
    """Закрыт ли человек живой заявкой на удаление.

    ``None``/невалидный ключ → открыт: несвязанный человек заявки иметь
    не может (она заводится по ``ayla_user_id``), и «закрыть всех, у кого
    нет ключа» остановило бы персонализацию у всех новых людей.
    """
    if ayla_user_id is None:
        return OPEN
    try:
        key = ayla_user_id if isinstance(ayla_user_id, uuid.UUID) else uuid.UUID(str(ayla_user_id))
    except (ValueError, TypeError):
        return OPEN
    row = (
        UserPersonalContext.objects.filter(user_id=key)
        .values_list("deletion_requested_at", "deletion_request_id")
        .first()
    )
    if row is None or row[0] is None:
        return OPEN
    return DeletionGate(
        blocked=True,
        reason=DELETION_REQUESTED,
        request_id=str(row[1]) if row[1] else None,
    )


def mark_deletion_requested(ayla_user_id: uuid.UUID, *, request_id: str) -> None:
    """Поставить флаг — идемпотентно; повтор с тем же номером ничего не меняет."""
    upc, _ = UserPersonalContext.objects.get_or_create(user_id=ayla_user_id)
    if upc.deletion_requested_at is not None and str(upc.deletion_request_id or "") == str(
        request_id
    ):
        return
    upc.deletion_requested_at = timezone.now()
    upc.deletion_request_id = uuid.UUID(str(request_id))
    upc.save(update_fields=["deletion_requested_at", "deletion_request_id"])
    logger.info("identity.deletion_gate.marked user_id=%s request_id=%s", ayla_user_id, request_id)


def clear_deletion_flag(ayla_user_id: uuid.UUID) -> bool:
    """Снять флаг. ``True`` — был и снят; ``False`` — нечего снимать."""
    updated = UserPersonalContext.objects.filter(
        user_id=ayla_user_id, deletion_requested_at__isnull=False
    ).update(deletion_requested_at=None, deletion_request_id=None)
    if updated:
        logger.info("identity.deletion_gate.cleared user_id=%s", ayla_user_id)
    return bool(updated)
