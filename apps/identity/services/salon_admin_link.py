"""Каталожная половина роли ``admin`` — свежая учётка + TUR + связь (DRF-2085).

OWNER RULING 18.09 (вариант А, комментарий в DRF-2085). Роль ``admin`` в
боте (``TenantStaff``) пускала человека в admin Mini App, а каталог на
первом действии отвечал 403: прокси ``bot:max:<id>`` не связан ни с какой
учёткой, у прокси нет ``TenantUserRelationship``. Закрывалось руками в
каталоге («Связать с Ayla → Администратор салона» после
``provision_salon_admin`` по SSH).

Здесь — вызов ручки каталога ``POST /internal/tenants/<slug>/salon-admins/``
**до записи** ``TenantStaff`` из ядра :func:`grant_staff_role`
(``apps/identity/services/staff_invites.py``) при ``role=admin``. Двери —
«Выдать роль в салоне» и «Сменить роль» оператора ``platform_operations``.
**Ввод кода приглашения каталог НЕ зовёт** (``link_catalog=False``):
ruling п.1 — operator-only capability, не пользовательский способ
авторизации; действие приглашённого артефактом оператора не становится.
После кода администратора оператор нажимает «Выдать роль» повторно — и
детерминированный ключ ниже дозаводит каталожную половину без дублей.

Что здесь решено и почему:

* **Ключ идемпотентности детерминирован** — ``uuid5`` от пары
  ``(tenant.id, external_user_id)``. Повторная выдача той же роли тому же
  человеку в том же салоне (после отзыва, после гонки, «на всякий случай»)
  — тот же ключ → каталог отвечает ``replayed`` с теми же id, а не
  ``identity_already_bound``. Другой салон — другой ключ → каталог
  отказывает по имени: одна MAX-личность = одна учётка администратора,
  второй салон разрешает оператор каталога руками (пункт 5 ruling'а).
* **Вызов идёт и когда строка ``TenantStaff`` уже есть**: у администраторов,
  выданных до этого листа, каталожной половины нет, и «Выдать роль»
  повторно — единственный операторский способ её дозавести без хоста.
* **Отказ — по имени, строки нет.** :class:`CatalogAdminLinkRefused`
  наследует ``InviteError`` (семейство отказов ядра): ядро вызывается
  внутри транзакции вызывающего, исключение откатывает всё. Текст «что
  сделать» — :attr:`CatalogAdminLinkRefused.hint`.
* **Аудит на успех** — в той же транзакции, что ``TenantStaff``
  (``STAFF_SALON_ADMIN_LINKED``); **аудит отказа** — после отката,
  вызывающим, через :func:`recording_refusals` (строка внутри
  откатившейся транзакции не пережила бы отказ).
* **Секрет** читает только HTTP-клиент; сюда и в лог он не попадает.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from apps.identity.services.staff_invites import InviteError

logger = logging.getLogger(__name__)

#: Пространство ключа идемпотентности — своё, чтобы ключ не совпал ни с
#: одним другим uuid5 в системе.
_IDEMPOTENCY_NAMESPACE = uuid.UUID("7c1a2d3e-2085-4bcd-9e0f-5a10ad1e4b2c")

#: Слова оператору/человеку по причине — «что сделать», не «что сломалось».
HINTS: dict[str, str] = {
    "token_missing": (
        "в окружении бота не задан AYLA_SALON_ADMIN_LINK_TOKEN — задать его и повторить"
    ),
    "credential_refused": (
        "каталог не принял секрет бота (пуст или не совпадает) — сверить "
        "AYLA_SALON_ADMIN_LINK_TOKEN в обоих контурах и повторить"
    ),
    "rate_limited": "каталог ограничил частоту — повторить через минуту",
    "tenant_not_found": "в каталоге нет салона с таким slug — сначала шаг «Подключить салон»",
    "tenant_inactive": "салон в каталоге выключен — включить его в каталоге и повторить",
    "identity_already_bound": (
        "эта MAX-личность уже связана с учётной записью в каталоге — разрешает "
        "оператор каталога (Users → «Связать с Ayla» / снять связь)"
    ),
    "identity_not_proxy": (
        "под этим внешним id в каталоге лежит настоящая учётная запись — "
        "разрешает оператор каталога вручную"
    ),
    "idempotency_key_reused": "ключ повтора занят другим запросом — написать в техподдержку",
    "bind_refused": "каталог отказал в связывании — написать в техподдержку с correlation_id",
    "readback_failed": (
        "каталог создал учётку, но не подтвердил её на салонной поверхности — "
        "повторить; если повторяется, написать в техподдержку с correlation_id"
    ),
    "client_error": "каталог не понял запрос бота (контракт разошёлся) — написать в техподдержку",
    "transport_error": "каталог недоступен — повторить позже",
}


class CatalogAdminLinkRefused(InviteError):
    """Каталог (или отсутствие секрета) не подтвердил администратора; роль не выдана."""

    slug = "catalog_admin_link_refused"

    def __init__(
        self,
        reason: str,
        *,
        correlation_id: str,
        tenant: Any,
        bot_user: Any,
        actor_label: str,
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.correlation_id = correlation_id
        # Предмет отказа едет с исключением: аудит пишется вызывающим ПОСЛЕ
        # отката его транзакции (см. recording_refusals), где ни tenant, ни
        # bot_user уже не в руках.
        self.tenant = tenant
        self.bot_user = bot_user
        self.actor_label = actor_label

    @property
    def tenant_slug(self) -> str:
        return str(getattr(self.tenant, "slug", ""))

    @property
    def hint(self) -> str:
        return HINTS.get(self.reason, "написать в техподдержку с correlation_id")

    def operator_text(self) -> str:
        return (
            f"Каталог не подтвердил администратора салона {self.tenant_slug}: "
            f"{self.reason} — {self.hint}. Роль в боте не выдана "
            f"(correlation_id {self.correlation_id})."
        )


@dataclass(frozen=True)
class CatalogAdminLinkOutcome:
    ayla_user_id: uuid.UUID
    relationship_id: uuid.UUID
    created: bool
    correlation_id: str
    idempotency_key: str


def idempotency_key_for(tenant_id: Any, external_user_id: str) -> str:
    return str(
        uuid.uuid5(_IDEMPOTENCY_NAMESPACE, f"salon-admin-link:{tenant_id}:{external_user_id}")
    )


def ensure_catalog_salon_admin(
    *,
    tenant: Any,
    bot_user: Any,
    actor_label: str,
    http_client: Any | None = None,
) -> CatalogAdminLinkOutcome:
    """Свежая учётка администратора в каталоге для этого человека в этом салоне.

    Идемпотентно по (салон, личность). Отказ — :class:`CatalogAdminLinkRefused`
    с причиной по имени; ничего не создано ни там, ни здесь.
    """
    from apps.catalog.services.http_client import (
        CatalogAdminLinkRefused as _HttpRefused,
        CatalogAdminLinkTokenMissing,
        CatalogClientError,
        CatalogHttpClient,
        CatalogTransportError,
    )
    from apps.integrations.ayla.user_proxy import external_user_id_for

    external_user_id = external_user_id_for(bot_user)
    correlation_id = uuid.uuid4().hex
    idempotency_key = idempotency_key_for(tenant.id, external_user_id)

    def _refuse(reason: str) -> CatalogAdminLinkRefused:
        # Внешний id — идентификатор канала, в лог не пишется; зацепки —
        # pk человека, slug и correlation_id (он же в логе каталога).
        logger.warning(
            "identity.salon_admin_link.refused reason=%s tenant=%s person=%s correlation_id=%s",
            reason,
            tenant.slug,
            bot_user.pk,
            correlation_id,
        )
        return CatalogAdminLinkRefused(
            reason,
            correlation_id=correlation_id,
            tenant=tenant,
            bot_user=bot_user,
            actor_label=actor_label,
        )

    try:
        with http_client if http_client is not None else CatalogHttpClient() as http:
            dto = http.link_salon_admin(
                tenant_slug=tenant.slug,
                external_user_id=external_user_id,
                actor=actor_label,
                correlation_id=correlation_id,
                idempotency_key=idempotency_key,
            )
    except CatalogAdminLinkTokenMissing:
        raise _refuse("token_missing") from None
    except _HttpRefused as exc:
        raise _refuse(exc.reason) from exc
    except CatalogClientError as exc:
        raise _refuse("client_error") from exc
    except CatalogTransportError as exc:
        raise _refuse("transport_error") from exc

    logger.info(
        "identity.salon_admin_link.%s tenant=%s person=%s ayla_user_id=%s correlation_id=%s",
        "created" if dto.created else "replayed",
        tenant.slug,
        bot_user.pk,
        dto.ayla_user_id,
        correlation_id,
    )
    return CatalogAdminLinkOutcome(
        ayla_user_id=dto.ayla_user_id,
        relationship_id=dto.relationship_id,
        created=dto.created,
        correlation_id=correlation_id,
        idempotency_key=idempotency_key,
    )


def audit_linked(
    outcome: CatalogAdminLinkOutcome, *, tenant: Any, bot_user: Any, actor_label: str
) -> None:
    """Строка аудита успеха — вызывать ВНУТРИ транзакции, пишущей TenantStaff."""
    from apps.audit.services import write_audit
    from apps.events.vocabulary import STAFF_SALON_ADMIN_LINKED
    from apps.tenancy.context import tenant_scope

    with tenant_scope(tenant):
        write_audit(
            STAFF_SALON_ADMIN_LINKED,
            target="identity.BotUser",
            target_id=bot_user.pk,
            payload={
                "surface": actor_label.split(":", 1)[0],
                "actor_label": actor_label,
                "tenant_id": str(tenant.id),
                "person_id": str(bot_user.pk),
                "ayla_user_id": str(outcome.ayla_user_id),
                "relationship_id": str(outcome.relationship_id),
                "correlation_id": outcome.correlation_id,
                "idempotency_key": outcome.idempotency_key,
                "created": outcome.created,
            },
        )


@contextmanager
def recording_refusals() -> Iterator[None]:
    """Обернуть транзакцию вызывающего: отказ каталога — в аудит ПОСЛЕ отката, затем наружу.

    Снаружи ``transaction.atomic`` нарочно: строка, записанная внутри
    откатившейся транзакции, откатилась бы вместе с ней, и отказ не оставил
    бы следа в боте (пункт 10 ruling'а — аудит в обеих системах).
    """
    from apps.audit.services import write_audit
    from apps.events.vocabulary import STAFF_SALON_ADMIN_LINK_REFUSED
    from apps.tenancy.context import tenant_scope

    try:
        yield
    except CatalogAdminLinkRefused as exc:
        with tenant_scope(exc.tenant):
            write_audit(
                STAFF_SALON_ADMIN_LINK_REFUSED,
                target="identity.BotUser",
                target_id=exc.bot_user.pk,
                payload={
                    "surface": exc.actor_label.split(":", 1)[0],
                    "actor_label": exc.actor_label,
                    "tenant_id": str(exc.tenant.id),
                    "person_id": str(exc.bot_user.pk),
                    "reason": exc.reason,
                    "correlation_id": exc.correlation_id,
                },
            )
        raise


__all__ = [
    "HINTS",
    "CatalogAdminLinkOutcome",
    "CatalogAdminLinkRefused",
    "audit_linked",
    "ensure_catalog_salon_admin",
    "idempotency_key_for",
    "recording_refusals",
]
