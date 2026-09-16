"""Canonical catalog identity строки зеркала — одна идемпотентная операция.

Разрыв, который закрывается: «специалист заведён → catalog identity
гарантирована». Сегодня между этими двумя фактами нет ни одного вызова —
приглашение (``apps/admin_api/views_invite.py:1018``) создаёт
``CatalogMaster`` со своим ``uuid4`` и колонку ``catalog_specialist_id`` не
трогает, а двадцать один читатель зовёт каталог только через
``apps/catalog/specialist_ref.py`` и на пустой колонке отказывает по имени.

### Контракт владельца

Canonical identity уже есть → **reuse**. Нет и создание допустимо →
**create**. Создание невозможно → **явный именованный отказ**. Скрытого
частичного успеха не бывает: onboarding имеет право на ``SUCCESS`` ТОЛЬКО
когда identity подтверждена ответом каталога.

### Почему это обобщение, а не вторая система

``apps.identity.services.solo_catalog_provisioning.provision_catalog_workspace``
уже делает reuse / create / explicit failure для соло-пути, и делает
правильно: ``specialist_id`` берётся из **ответа** каталога (readback), а
не «мы послали, значит получилось». Здесь та же операция, поднятая до
уровня строки зеркала и дополненная ветвью **heal**. Второй catalog
identity system не заводится — это первый запрет задачи.

### Четыре ветви, и почему heal — не частный случай create

``reuse``   колонка зеркала непуста — каталог звать не за чем;
``heal``    факт есть на ``SoloIdentityLink``, а зеркало его потеряло. Это
            **reuse**, а не create: id уже выдан каталогом, и повторный
            вызов заводил бы второй workspace под тот же
            ``external_user_id``. Ветвь существует не «на всякий случай»:
            сегодня оба хранилища пишутся двумя ``save()`` подряд
            (``solo_catalog_provisioning:88-102``) без общей транзакции, и
            падение между ними оставляет link со значением, а зеркало —
            пустым. Без heal операция ответила бы ``creation_unavailable``
            там, где identity **существует**, — устойчивая неправда;
``create``  дверь есть только у соло-пути (``POST /internal/tenants/
            solo-workspaces/``). Каталог идемпотентен по
            ``external_user_id`` (201 завёл / 200 уже был), поэтому повтор
            безопасен и возвращает тот же ``specialist_id``;
``refuse``  именованное исключение. Никогда ``None``, никогда «частично».

### Салонный мастер: двери нет НА ЭТОМ ПУТИ (а не «в системе»)

``api/v1/internal/specialists/`` — ``ReadOnlyModelViewSet``
(``users/specialists_api.py:465``, стена в РОДИТЕЛЕ); ``def create`` в файле
ноль. Профиль в каталоге рождается только сигналом
``users/signals.py:12`` на ``post_save`` каталожного ``User`` с
``role='specialist'``.

**Формулировка важна, и первая моя редакция была слишком сильной.** Я
censused прямого создателя (``SpecialistProfile.objects.create``) и
заключил «двери нет вовсе». Дверью, однако, является всё, что заводит
ТРИГГЕРНУЮ строку ``User(role='specialist')``, а таких в рабочем коде три:
провижининг соло (``tenants/solo_provisioning.py:190``), сиды и **админка**
(``users/admin.py:315`` — там прямо сказано, что профиль заводится сам).
Перепись по прямому создателю их не видит, потому что они создают не
профиль, а пользователя.

Верно поэтому вот что: **из продуктового пути (Mini App, приглашение
салона) двери нет; существующие двери — операторские.** Для оператора
``creation_unavailable`` читается не как «никогда», а как «**не этим путём;
заводится оператором в админке**» — и текст восстановления должен говорить
именно так. Нужен ли продуктовый маршрут или достаточно ручной операции —
вопрос владельца и лежит за границей этой операции.

### Одно состояние, три отказа — и три имени, которые НЕ надо сливать

У «у этой строки нет catalog identity» три имени, потому что отказывают
три разных актора в трёх разных действиях. Предмет один, отказы разные:

===========================  =========================================
``catalog_unlinked``         гейт продажи (``master_state.sale_block``),
(наружу ``master_catalog_    наружу — «продавать нельзя»
unlinked``)
``catalog_specialist_        аксессор ``specialist_ref`` — «звать каталог
unresolved``                 не с чем» (``CatalogSpecialistUnresolved``)
``creation_unavailable``     эта операция — «завести identity нечем»
                             (двери создания на этом пути нет)
===========================  =========================================

Читать их надо как **одно состояние**, а чинить — в трёх местах: гейт
перестаёт обещать продажу, аксессор перестаёт звать, операция называет
причину. Переименовывать ничего не нужно и не следует: имена описывают
РАЗНЫЕ действия над одним фактом. Связь названа здесь явно, чтобы через
месяц три имени не прочитались как три предмета — на этом мы сегодня
спотыкались девять раз.

### Защита от дубля — три слоя, и код владеет только первым

1. короткое замыкание ниже: при непустой колонке вызова нет вовсе;
2. идемпотентность каталога по ``external_user_id``;
3. в базе — партиальный ``UniqueConstraint`` ``(tenant, catalog_specialist_id)``
   (``apps/catalog/models.py:629-633``).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Final, Literal, get_args

logger = logging.getLogger(__name__)

#: Машинные имена отказов. Пять первых — СУЩЕСТВУЮЩИЙ словарь
#: ``solo_catalog_provisioning`` (``:17-23``): они переиспользуются, а не
#: вводятся заново. ``creation_unavailable`` добавлен ПОСЛЕ проверки
#: словаря и называет отсутствие двери создания, а не сбой.
IdentityReason = Literal[
    "token_missing",
    "refused",
    "conflict",
    "client_error",
    "transport_error",
    "creation_unavailable",
]

REASON_TOKEN_MISSING: Final = "token_missing"
REASON_REFUSED: Final = "refused"
REASON_CONFLICT: Final = "conflict"
REASON_CLIENT_ERROR: Final = "client_error"
REASON_TRANSPORT_ERROR: Final = "transport_error"
REASON_CREATION_UNAVAILABLE: Final = "creation_unavailable"

#: Перечень с узлом полноты — по образцу ``ALL_SALE_BLOCKS``
#: (``apps/booking/services/master_gate.py:79``). Четыре потока кодируют
#: против этого набора; без сторожа на полноту он разъехался бы молча.
ALL_IDENTITY_REASONS: Final[tuple[str, ...]] = get_args(IdentityReason)

#: Каталог возвращает 409 с собственным хвостом: ``conflict:slug_taken`` и
#: т. п. Хвост несёт каталог, голова наша — поэтому в перечне только голова.
CONFLICT_PREFIX: Final = "conflict:"


@dataclass(frozen=True)
class CatalogIdentity:
    """Подтверждённая catalog identity строки зеркала.

    ``created`` означает **«этот вызов выполнял провижининг»**, а не «каталог
    завёл строку именно сейчас»: провижининг возвращает причину-или-``None``
    и различить 201 от 200 не может. Сказано точно, потому что четыре
    потока читают это поле.
    """

    specialist_id: str
    created: bool
    master_pk: Any


class CatalogIdentityUnavailable(Exception):
    """Identity не подтверждена — вызывающий не имеет права на успех.

    Несёт машинное имя причины: разные имена чинятся в разных местах, и
    одно слово на все случаи вернуло бы нас к «SETUP_PENDING», который уже
    разводили по именам на ``SoloIdentityLink``.
    """

    def __init__(self, reason: str, master_pk: Any = None) -> None:
        super().__init__(f"{reason}: catalog identity unavailable for master {master_pk}")
        self.reason = reason
        self.master_pk = master_pk


def _link_of(master: Any) -> Any | None:
    """``SoloIdentityLink`` строки зеркала — или ``None``, когда её нет.

    ``SoloIdentityLink.master`` — ``OneToOneField(related_name="identity_link")``,
    поэтому обратный доступ бросает, когда связи нет. Отсутствие связи —
    обычное состояние салонного мастера, а не сбой.
    """
    try:
        return master.identity_link
    except Exception:  # noqa: BLE001 — RelatedObjectDoesNotExist и родня
        return None


def ensure_catalog_specialist_identity(
    master: Any,
    *,
    bot_user: Any | None = None,
    http_client: Any | None = None,
) -> CatalogIdentity:
    """Гарантировать canonical catalog identity для строки зеркала.

    ``bot_user`` нужен ТОЛЬКО ветви create: у ``SoloIdentityLink`` нет FK на
    ``BotUser`` (личность там — ``channel`` + ``channel_user_id``), а
    провижининг зовёт ``external_user_id_for(bot_user)``. Не передали —
    берём ``master.linked_bot_user``, то самое поле, которым считает людей
    ``is_solo_provider``.

    Возвращает :class:`CatalogIdentity` либо бросает
    :class:`CatalogIdentityUnavailable` с именем причины. Промежуточного
    исхода нет: продолжить «с оговоркой» вызывающий не может.
    """
    from apps.identity.services import solo_catalog_provisioning as solo

    master_pk = getattr(master, "pk", None)

    # 1. reuse — колонка зеркала уже несёт id каталога.
    own = getattr(master, "catalog_specialist_id", None)
    if own:
        return CatalogIdentity(specialist_id=str(own), created=False, master_pk=master_pk)

    link = _link_of(master)

    # 2. heal — id выдан каталогом и лежит на связи, зеркало его потеряло.
    #    Каталог НЕ зовём: id уже существует.
    linked_id = getattr(link, "catalog_specialist_id", None) if link is not None else None
    if linked_id:
        master.catalog_specialist_id = linked_id
        master.save(update_fields=["catalog_specialist_id"])
        logger.info(
            "catalog.identity.healed master=%s specialist=%s — "
            "the mirror lost a fact the link kept",
            master_pk,
            linked_id,
        )
        return CatalogIdentity(specialist_id=str(linked_id), created=False, master_pk=master_pk)

    # 3. create — только там, где дверь есть: соло-кабинет.
    tenant = getattr(master, "tenant", None)
    person = bot_user if bot_user is not None else getattr(master, "linked_bot_user", None)
    if link is None or tenant is None or person is None:
        raise CatalogIdentityUnavailable(REASON_CREATION_UNAVAILABLE, master_pk)

    # Вызов НЕ обёрнут в транзакцию намеренно: провижининг записывает
    # машинную причину отказа на связь (``:112-113``), и откат стёр бы
    # ровно ту запись, ради которой оператор туда смотрит. Согласованность
    # успешной записи закрыта внутри самого провижининга.
    refusal = solo.provision_catalog_workspace(
        link,
        tenant=tenant,
        bot_user=person,
        display_name=getattr(person, "display_name", "") or getattr(master, "name", "") or "",
        http_client=http_client,
    )
    if refusal:
        raise CatalogIdentityUnavailable(refusal, master_pk)

    # Зеркало пишет сам провижининг (``:101-102``) — второй раз не пишем,
    # читаем то, что действительно легло.
    master.refresh_from_db(fields=["catalog_specialist_id"])
    specialist_id = getattr(master, "catalog_specialist_id", None)
    if not specialist_id:
        # Провижининг сказал «успех», а в зеркале пусто — это и есть
        # частичный успех. Называем отказом, а не продолжаем.
        raise CatalogIdentityUnavailable(REASON_TRANSPORT_ERROR, master_pk)

    return CatalogIdentity(specialist_id=str(specialist_id), created=True, master_pk=master_pk)


__all__ = [
    "ALL_IDENTITY_REASONS",
    "CONFLICT_PREFIX",
    "REASON_CLIENT_ERROR",
    "REASON_CONFLICT",
    "REASON_CREATION_UNAVAILABLE",
    "REASON_REFUSED",
    "REASON_TOKEN_MISSING",
    "REASON_TRANSPORT_ERROR",
    "CatalogIdentity",
    "CatalogIdentityUnavailable",
    "IdentityReason",
    "ensure_catalog_specialist_identity",
]
