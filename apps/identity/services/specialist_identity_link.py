"""Каталожная связь личности мастера, принявшего приглашение (DRF-2442).

Решение владельца §77 п.38 (24.09.2026): «мне не надо участия человека в
регистрации мастеров». До этого листа приём приглашения доводил дело до строки
мастера в каталоге (``catalog_specialist_id``) и до бот-стороны
(``CatalogMaster.linked_bot_user``), но **никто не говорил каталогу, что личность
MAX, принявшая приглашение, — это тот самый мастер**. Поэтому кабинет отвечал
403 ``subject_unresolved``: у прокси-строки пуст ``linked_user_id``, а записать
его мог только человек-оператор в Django Admin каталога — и операторов ноль.

Здесь — вызов новой двери каталога ``POST /internal/specialists/<uuid>/identity/``
(PR-A, ``users.specialist_identity_linking``) сразу после приёма приглашения,
когда токен уже погашен. **Гашение и есть доказательство владения**: ссылка
одноразовая, открыть её мог только тот, кому передали, а второй предъявитель
получает ``wrong_recipient``. Каталожный запрет «no bot-driven binding until a
verified ownership flow exists» не снимается, а удовлетворяется.

Что здесь решено и почему:

* **Ключ идемпотентности детерминирован** — ``uuid5`` от пары
  ``(specialist_id, external_user_id)``. Повтор приёма (двойной тап, сетевой
  обрыв, дозвон подметальщиком, команда починки) даёт тот же ключ, каталог
  отвечает ``replayed`` теми же id и второй связи не создаёт. Другая личность
  на того же мастера — другой ключ, и каталог откажет по имени
  (``identity_already_bound``): перепривязка — не наш способ.
* **Отказ не валит приём приглашения.** Мастер уже принял: строка получила
  ``linked_bot_user``, токен погашен, и откатывать это из-за недоступного
  каталога значило бы терять погашенный токен. Отказ уходит в лог по имени и
  остаётся дозвонить повторно — ровно так же, как ``catalog_unlinked`` живёт
  до починки подметальщиком.
* **Секрет** читает только HTTP-клиент; сюда и в лог он не попадает. Внешний id
  в лог тоже не пишется (``pii_guard``: идентификатор канала — ПД); зацепки —
  pk человека, id профиля и ``correlation_id``, он же в логе каталога.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# ИМЯ. Рядом живёт ``apps.catalog.identity.ensure_catalog_specialist_identity`` —
# и она про ДРУГОЙ предмет: про ``catalog_specialist_id``, то есть «знает ли
# каталог эту строку мастера». Здесь — про личность: «знает ли каталог, что эта
# MAX-личность и есть тот мастер». Один и тот же глагол на два разных факта уже
# однажды стоил нам 403 после «успешной привязки», поэтому имена разные:
# ``bind_master_identity_in_catalog``.

#: Пространство ключа — своё, чтобы ключ не совпал ни с одним другим uuid5.
_IDEMPOTENCY_NAMESPACE = uuid.UUID("9f3b71c4-2442-4c8a-bd11-6e5a0d7c9e42")

#: Метка вызывающего для аудита каталога: не имя человека, а место в коде.
ACTOR_INVITE_ACCEPT = "bot:onboarding_accept"
ACTOR_BACKFILL = "bot:link_master_identities"

#: Слова человеку/оператору по причине — «что сделать», не «что сломалось».
HINTS: dict[str, str] = {
    "token_missing": (
        "в окружении бота не задан AYLA_SPECIALIST_IDENTITY_LINK_TOKEN — задать и повторить"
    ),
    "credential_refused": (
        "каталог не принял секрет бота — сверить AYLA_SPECIALIST_IDENTITY_LINK_TOKEN "
        "в обоих контурах и повторить"
    ),
    "rate_limited": "каталог ограничил частоту — повторить через минуту",
    "specialist_not_found": (
        "в каталоге нет такого профиля мастера — сначала привязка строки к каталогу"
    ),
    "specialist_not_linkable": (
        "профиль в каталоге есть, но его учётная запись не годится в цель "
        "(не мастер, выключена или салон выключен) — разбирается в каталоге"
    ),
    "identity_unknown": (
        "каталог ещё не видел эту MAX-личность — мастеру достаточно открыть кабинет "
        "и повторить приём"
    ),
    "identity_not_proxy": (
        "под этим внешним id в каталоге лежит настоящая учётная запись — "
        "разрешает оператор каталога вручную"
    ),
    "identity_already_bound": (
        "эта MAX-личность уже связана с другой учётной записью каталога — "
        "перепривязку решает оператор каталога"
    ),
    "idempotency_key_reused": "ключ повтора занят другим запросом — написать в техподдержку",
    "bind_refused": "каталог отказал в связывании — смотреть его лог по correlation_id",
    "readback_failed": (
        "каталог записал связь, но кабинет не подтвердил — повторить с тем же ключом"
    ),
    "client_error": "каталог ответил ошибкой запроса — написать в техподдержку с correlation_id",
    "transport_error": "каталог недоступен — повторить позже",
}


class SpecialistIdentityLinkRefused(Exception):
    """Отказ каталога с причиной по имени; связи нет."""

    def __init__(self, reason: str, *, correlation_id: str) -> None:
        super().__init__(reason)
        self.reason = reason
        self.correlation_id = correlation_id

    @property
    def hint(self) -> str:
        return HINTS.get(self.reason, "написать в техподдержку с correlation_id")


@dataclass(frozen=True)
class SpecialistIdentityLinkOutcome:
    specialist_id: uuid.UUID
    ayla_user_id: uuid.UUID
    created: bool
    correlation_id: str
    idempotency_key: str


def idempotency_key_for(specialist_id: Any, external_user_id: str) -> str:
    return str(
        uuid.uuid5(
            _IDEMPOTENCY_NAMESPACE,
            f"specialist-identity-link:{specialist_id}:{external_user_id}",
        )
    )


def bind_master_identity_in_catalog(
    *,
    specialist_id: Any,
    bot_user: Any,
    actor_label: str = ACTOR_INVITE_ACCEPT,
    http_client: Any | None = None,
) -> SpecialistIdentityLinkOutcome:
    """Связать личность этого человека с этим профилем мастера в каталоге.

    Идемпотентно по паре (профиль, личность). Отказ —
    :class:`SpecialistIdentityLinkRefused` с причиной по имени; в каталоге при
    отказе ничего не записано.
    """

    from apps.catalog.services.http_client import (
        CatalogClientError,
        CatalogHttpClient,
        CatalogSpecialistIdentityRefused,
        CatalogSpecialistIdentityTokenMissing,
        CatalogTransportError,
    )
    from apps.integrations.ayla.user_proxy import external_user_id_for

    external_user_id = external_user_id_for(bot_user)
    correlation_id = uuid.uuid4().hex
    idempotency_key = idempotency_key_for(specialist_id, external_user_id)

    def _refuse(reason: str) -> SpecialistIdentityLinkRefused:
        logger.warning(
            "identity.specialist_identity_link.refused reason=%s specialist=%s person=%s "
            "correlation_id=%s",
            reason,
            specialist_id,
            getattr(bot_user, "pk", None),
            correlation_id,
        )
        return SpecialistIdentityLinkRefused(reason, correlation_id=correlation_id)

    try:
        with http_client if http_client is not None else CatalogHttpClient() as http:
            dto = http.link_specialist_identity(
                specialist_id=specialist_id,
                external_user_id=external_user_id,
                actor=actor_label,
                correlation_id=correlation_id,
                idempotency_key=idempotency_key,
            )
    except CatalogSpecialistIdentityTokenMissing:
        raise _refuse("token_missing") from None
    except CatalogSpecialistIdentityRefused as exc:
        raise _refuse(exc.reason) from exc
    except CatalogClientError as exc:
        raise _refuse("client_error") from exc
    except CatalogTransportError as exc:
        raise _refuse("transport_error") from exc

    logger.info(
        "identity.specialist_identity_link.%s specialist=%s person=%s ayla_user_id=%s "
        "correlation_id=%s",
        "created" if dto.created else "replayed",
        specialist_id,
        getattr(bot_user, "pk", None),
        dto.ayla_user_id,
        correlation_id,
    )
    return SpecialistIdentityLinkOutcome(
        specialist_id=dto.specialist_id,
        ayla_user_id=dto.ayla_user_id,
        created=dto.created,
        correlation_id=correlation_id,
        idempotency_key=idempotency_key,
    )


__all__ = [
    "ACTOR_BACKFILL",
    "ACTOR_INVITE_ACCEPT",
    "SpecialistIdentityLinkOutcome",
    "SpecialistIdentityLinkRefused",
    "bind_master_identity_in_catalog",
    "idempotency_key_for",
]
