"""Заявка на удаление аккаунта — бот-половина среза D1 (§7 свода, DRF-1699).

§7: после нажатия «Удалить аккаунт и личные данные» система немедленно
показывает, что запрос принят, точную крайнюю дату, ``request_id`` и
статус; **устойчивый ``DeletionRequest`` создаётся до показа успеха**;
**ошибка обязана явно говорить, что удаление не началось**.

Носитель заявки — каталог (``users.DeletionRequest``, beautygo_backend#356):
там живёт человек как источник истины, там и его заявка. Бот здесь —
рука, которая её заводит, и экран, который её показывает. Локальной
копии нет намеренно: два носителя одной заявки разошлись бы по статусу,
и человек увидел бы на одном экране «в работе», на другом «завершено».

### Чего здесь нет

Стирания. Этот модуль не зовёт ни :func:`privacy.delete_personal_data`,
ни каталожный ``DELETE …/personal-data/``. Исполнитель — срез D3, и до
него заявка честно висит в ``DELETION_REQUESTED``. Тест
``test_request_erases_nothing`` стережёт это отдельно: срез, который
«заодно» стёр бы, вернул §7 к прежнему «нажал → стёрто» без следа.

### Почему «не началось» — одно слово на все отказы

У отказа три причины (нет связи с Ayla, две связи, Ayla не ответила), и
у каждой своё машинное имя для лога и ``detail``. Но человеку сообщается
одно: удаление НЕ НАЧАЛОСЬ. Это не упрощение, а требование §7: прежний
ответ ``502 partial`` означал «часть сделана», и человек не знал, в каком
состоянии его данные. Здесь состояние всегда одно из двух — заявка есть
или её нет, — и слово «не началось» ровно это и говорит.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from apps.identity.models import BotUser
from apps.identity.services.privacy import resolve_person_link

logger = logging.getLogger(__name__)

#: Причины отказа — машинные имена для лога и ``detail`` ответа.
NOT_LINKED = "not_linked"
IDENTITY_CONFLICT = "identity_conflict"
UPSTREAM_UNAVAILABLE = "upstream_unavailable"

#: Текст для человека — один на все причины (см. докстринг модуля).
NOT_STARTED_TEXT = (
    "Удаление не началось. Ваши данные в прежнем состоянии — ничего не "
    "удалено и не изменено. Попробуйте ещё раз позже."
)


class DeletionNotStarted(Exception):
    """Заявка не заведена — ничего не произошло.

    ``reason`` — машинная причина; ``retryable`` — поможет ли повтор
    (сеть — да; отсутствие связи с Ayla — нет, пока её не установят).
    """

    def __init__(self, reason: str, *, retryable: bool) -> None:
        super().__init__(NOT_STARTED_TEXT)
        self.reason = reason
        self.retryable = retryable


@dataclass(frozen=True)
class DeletionRequestView:
    """То, что видит человек: номер, срок, статус — как отдал каталог."""

    request_id: str
    status: str
    requested_at: str
    deadline_at: str
    completed_at: str | None
    is_open: bool
    created: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "status": self.status,
            "requested_at": self.requested_at,
            "deadline_at": self.deadline_at,
            "completed_at": self.completed_at,
            "is_open": self.is_open,
        }


def _from_wire(data: dict[str, Any], *, created: bool) -> DeletionRequestView:
    try:
        return DeletionRequestView(
            request_id=str(data["request_id"]),
            status=str(data["status"]),
            requested_at=str(data["requested_at"]),
            deadline_at=str(data["deadline_at"]),
            completed_at=str(data["completed_at"]) if data.get("completed_at") else None,
            is_open=bool(data.get("is_open", True)),
            created=created,
        )
    except KeyError as exc:
        # Ответ без номера или срока — не заявка: человеку нечего показать,
        # а показать «принято» без номера значило бы обещать то, чего нет.
        raise DeletionNotStarted(UPSTREAM_UNAVAILABLE, retryable=True) from exc


def request_account_deletion(bot_user: BotUser, *, client: Any = None) -> DeletionRequestView:
    """Завести заявку в каталоге и вернуть то, что показать человеку.

    Человек определяется тем же способом, что и при стирании
    (:func:`privacy.resolve_person_link`): иначе заявка легла бы на одну
    учётную запись, а каскад D3 прошёл бы по другой.
    """
    from apps.integrations.ayla.personal_context_client import (
        PersonalContextError,
        PersonalContextHttpClient,
    )
    from apps.integrations.ayla.user_proxy import external_user_id_for

    link = resolve_person_link(bot_user)
    if link.conflict:
        logger.error(
            "identity.deletion_request.not_started reason=%s bot_user=%s",
            IDENTITY_CONFLICT,
            bot_user.id,
        )
        raise DeletionNotStarted(IDENTITY_CONFLICT, retryable=False)
    if link.ayla_user_id is None:
        logger.warning(
            "identity.deletion_request.not_started reason=%s bot_user=%s",
            NOT_LINKED,
            bot_user.id,
        )
        raise DeletionNotStarted(NOT_LINKED, retryable=False)

    client = client or PersonalContextHttpClient()
    try:
        with client as http:
            data = http.create_deletion_request(
                ayla_user_id=str(link.ayla_user_id),
                external_user_id=external_user_id_for(bot_user),
                initiator="bot",
            )
    except PersonalContextError as exc:
        logger.warning(
            "identity.deletion_request.not_started reason=%s bot_user=%s error=%s",
            UPSTREAM_UNAVAILABLE,
            bot_user.id,
            exc.__class__.__name__,
        )
        raise DeletionNotStarted(UPSTREAM_UNAVAILABLE, retryable=True) from exc

    # ``created`` берётся из тела: клиент отдаёт JSON без HTTP-статуса, а
    # «принято» и «уже принято» человеку — разное (второе нажатие).
    view = _from_wire(data, created=bool(data.get("created", False)))
    # D2 (§7): персонализация прекращается СРАЗУ — флаг ставится здесь, до
    # возврата (то есть до показа успеха), на уровне человека.
    from apps.identity.services.deletion_gate import mark_deletion_requested

    mark_deletion_requested(link.ayla_user_id, request_id=view.request_id)
    logger.info(
        "identity.deletion_request.accepted bot_user=%s request_id=%s deadline=%s status=%s",
        bot_user.id,
        view.request_id,
        view.deadline_at,
        view.status,
    )
    return view


def current_account_deletion(
    bot_user: BotUser, *, client: Any = None
) -> DeletionRequestView | None:
    """Текущая заявка человека для профиля, или ``None``.

    Отказы здесь не поднимаются: профиль без сведений о заявке — не
    ошибка экрана, а отсутствие строки. Несвязанный человек заявки иметь
    не может — ``None`` без похода в Ayla.
    """
    from apps.integrations.ayla.personal_context_client import (
        PersonalContextError,
        PersonalContextHttpClient,
    )
    from apps.integrations.ayla.user_proxy import external_user_id_for

    link = resolve_person_link(bot_user)
    if link.conflict or link.ayla_user_id is None:
        return None
    client = client or PersonalContextHttpClient()
    try:
        with client as http:
            data = http.get_current_deletion_request(
                ayla_user_id=str(link.ayla_user_id),
                external_user_id=external_user_id_for(bot_user),
            )
    except PersonalContextError as exc:
        logger.warning(
            "identity.deletion_request.read_failed bot_user=%s error=%s",
            bot_user.id,
            exc.__class__.__name__,
        )
        return None
    from apps.identity.services.deletion_gate import (
        clear_deletion_flag,
        mark_deletion_requested,
    )

    if data is None:
        # Каталог говорит «заявок не было» — и у нас флага быть не должно.
        clear_deletion_flag(link.ayla_user_id)
        return None
    try:
        view = _from_wire(data, created=False)
    except DeletionNotStarted:
        return None
    # Догоняем каталог в обе стороны: заявка из приложения ставит флаг,
    # завершённая — снимает.
    if view.is_open:
        mark_deletion_requested(link.ayla_user_id, request_id=view.request_id)
    else:
        clear_deletion_flag(link.ayla_user_id)
    return view
