"""Canonical catalog identity строки зеркала — одна идемпотентная операция.

Разрыв, который закрывается: «специалист заведён → catalog identity
гарантирована». Двадцать один читатель зовёт каталог только через
``apps/catalog/specialist_ref.py`` и на пустой колонке отказывает по имени,
а колонку до недавнего времени никто на салонном пути не заполнял:
приглашение создавало ``CatalogMaster`` со своим ``uuid4`` и
``catalog_specialist_id`` не трогало вовсе.

**С DRF-2379 трогает.** Приглашение зовёт эту операцию сразу после того, как
строка легла (``views_invite._link_to_catalog``), а не добитую привязку
добивает подметальщик (``apps.catalog.tasks.link_unlinked_salon_masters``).
Разрыв закрыт с обеих сторон: один вызов на живом пути и один сторож на
случай, когда каталог в этот момент был недоступен.

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
``create``  дверей две, по одной на путь: соло — ``POST /internal/
            tenants/solo-workspaces/``, салон — ``POST /internal/tenants/
            salon-specialists/`` (DRF-2379). Обе идемпотентны по
            ``external_user_id`` (201 завёл / 200 уже был), поэтому повтор
            безопасен и возвращает тот же ``specialist_id``;
``refuse``  именованное исключение. Никогда ``None``, никогда «частично».

### Салонный мастер: дверь построена в DRF-2379 (а раньше её не было)

**Прежнее состояние, и оно было правдой, а не дефектом.** До DRF-2379 этот
раздел говорил: «из продуктового пути (Mini App, приглашение салона) двери
нет; существующие двери — операторские». Так и было:
``api/v1/internal/specialists/`` — ``ReadOnlyModelViewSet`` (стена в
РОДИТЕЛЕ), а профиль в каталоге рождался только сигналом на ``post_save``
каталожного ``User`` с ``role='specialist'``, то есть у провижининга соло, у
сидов и у админки. Салонный мастер получал ``creation_unavailable``, и это
был **штатный исход**: имя честно называло отсутствие двери.

**Нынешнее состояние.** Решение владельца §77 п.27 (24.09): привязка мастера
к каталогу должна происходить **сама**, когда салон заводит мастера. Дверь
построена — ``POST /internal/tenants/salon-specialists/`` заводит специалиста
в УЖЕ существующем салоне (тенант ищется, а не создаётся) и возвращает
``specialist_id``. Поэтому у салонного мастера ``creation_unavailable``
**перестал быть нормой**: теперь он означает либо «тенанта у строки нет»,
либо «каталог не знает этой ручки» — то есть наша половина ещё не выложена.

Отсюда важное для выкладки свойство: **отсутствие ручки ведёт себя ровно как
прежде**. Никакого третьего поведения не заведено, заведение мастера не
падает, состояние ``catalog_unlinked`` студия видит, как и раньше.

**Что дверь НЕ делает.** Она не решает, кто этот человек. ``linked_bot_user``
(ADR-0008) и ``catalog_specialist_id`` (DRF-1933) — два разных факта, и
путаница между ними и есть причина, по которой часы отвечали 403
``CatalogSpecialistUnresolved`` после «успешной» привязки личности.

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


#: Пространство claim салонного провижининга (DRF-2379).
#:
#: **Ключ идемпотентности, не личность.** У соло claim — человек
#: (``bot:max:<id>``), потому что соло-кабинет заводит сам мастер. Салонного
#: мастера заводит салон, и на этот момент MAX-id ещё нет: приглашение не
#: принято, в строке лежит ``max_handle`` (имя в MAX), а не числовой id. Ключ
#: строки зеркала есть всегда и не меняется.
#:
#: Каталог кладёт его в ``provisioned_external_user_id`` как **провенанс** —
#: «для какой строки бота это заведено». Резолверы личности это поле не
#: читают, и на стороне каталога сторож закрытого списка читателей это
#: доказывает. Личность приезжает позже и отдельно, через ``linked_bot_user``
#: (ADR-0008) — второго ребра личности здесь не появляется.
SALON_CLAIM_PREFIX: Final = "bot:master:"


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


def salon_provisioning_claim(master: Any) -> str:
    """Ключ идемпотентности салонного провижининга для этой строки зеркала.

    См. :data:`SALON_CLAIM_PREFIX`: это **ключ строки, а не личность**.
    Одно место ответа, чтобы подметальщик и приглашение не разошлись в
    написании — разойдясь, они завели бы второго специалиста на одного
    человека.
    """
    return f"{SALON_CLAIM_PREFIX}{getattr(master, 'pk', None)}"


def _create_for_salon(
    master: Any,
    *,
    tenant: Any,
    http_client: Any | None,
    master_pk: Any,
) -> CatalogIdentity:
    """Завести специалиста в каталоге для салонной строки зеркала (DRF-2379).

    Дверь: ``POST /internal/tenants/salon-specialists/``. Идемпотентна по
    claim — повтор возвращает того же специалиста, поэтому подметальщику
    можно звать её без опаски.

    ``specialist_id`` берётся из **ответа** (readback), а не «мы послали,
    значит получилось»: тот же довод, что у соло-пути, и тот же запрет на
    частичный успех.

    **Двери нет** (наша половина каталога ещё не выложена) —
    ``creation_unavailable``, ровно то имя и ровно то поведение, что было у
    салонного мастера до этого листа. Второго исхода на этот случай не
    заводится: три поведения там, где нужно два, — источник расхождений.
    """
    from apps.catalog.services.http_client import (
        CatalogClientError,
        CatalogHttpClient,
        CatalogProvisioningRefused,
        CatalogProvisioningTokenMissing,
        CatalogSalonSpecialistDoorAbsent,
        CatalogSalonSpecialistRefused,
        CatalogTransportError,
    )

    claim = salon_provisioning_claim(master)
    reason: str
    try:
        with http_client if http_client is not None else CatalogHttpClient() as http:
            dto = http.provision_salon_specialist(
                tenant_id=tenant.id,
                external_user_id=claim,
                display_name=getattr(master, "name", "") or "",
            )
    except CatalogSalonSpecialistDoorAbsent:
        # НЕ отказ каталога: каталог такой ручки не знает. Своя строка
        # журнала, потому что чинит это выкладка, а не оператор; слитая с
        # отказами, она через неделю читается как «у нас всё сломано».
        logger.info(
            "catalog.identity.salon_door_absent master=%s tenant=%s — "
            "каталог не знает ручку салонного провижининга; строка остаётся "
            "catalog_unlinked, это незавершённое раскатывание, а не отказ",
            master_pk,
            getattr(tenant, "id", None),
        )
        raise CatalogIdentityUnavailable(REASON_CREATION_UNAVAILABLE, master_pk) from None
    except CatalogProvisioningTokenMissing:
        reason = REASON_TOKEN_MISSING
    except CatalogProvisioningRefused:
        reason = REASON_REFUSED
    except CatalogSalonSpecialistRefused as exc:
        if exc.reason == "claim_bound_elsewhere":
            # Находка, а не штатный исход: claim выведен из первичного ключа
            # строки зеркала, и занять его может только вторая строка с тем
            # же ключом. Отдельная громкая строка — это приглашение
            # посмотреть, а не шум.
            logger.error(
                "catalog.identity.salon_claim_bound_elsewhere master=%s claim=%s — "
                "ключ строки зеркала уже привязан к специалисту ДРУГОГО салона; "
                "так не бывает при исправных данных",
                master_pk,
                claim,
            )
        else:
            logger.warning(
                "catalog.identity.salon_refused master=%s tenant=%s reason=%s",
                master_pk,
                getattr(tenant, "id", None),
                exc.reason,
            )
        raise CatalogIdentityUnavailable(f"{CONFLICT_PREFIX}{exc.reason}"[:64], master_pk) from None
    except CatalogClientError:
        reason = REASON_CLIENT_ERROR
    except CatalogTransportError:
        reason = REASON_TRANSPORT_ERROR
    else:
        master.catalog_specialist_id = dto.specialist_id
        master.save(update_fields=["catalog_specialist_id"])
        logger.info(
            "catalog.identity.salon_provisioned master=%s tenant=%s specialist=%s created=%s",
            master_pk,
            getattr(tenant, "id", None),
            dto.specialist_id,
            dto.created,
        )
        return CatalogIdentity(
            specialist_id=str(dto.specialist_id), created=True, master_pk=master_pk
        )

    logger.warning(
        "catalog.identity.salon_unavailable master=%s tenant=%s reason=%s",
        master_pk,
        getattr(tenant, "id", None),
        reason,
    )
    raise CatalogIdentityUnavailable(reason, master_pk)


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

    # 3. create — теперь дверей две, по одной на путь. Салонная появилась
    #    в DRF-2379; до неё этот блок целиком отказывал салонному мастеру
    #    именем ``creation_unavailable``, и то был не сбой, а правда о
    #    системе: «из продуктового пути двери нет» (см. докстринг выше).
    tenant = getattr(master, "tenant", None)
    person = bot_user if bot_user is not None else getattr(master, "linked_bot_user", None)
    if tenant is None:
        raise CatalogIdentityUnavailable(REASON_CREATION_UNAVAILABLE, master_pk)

    # 3a. салон — связи соло нет, и это НОРМАЛЬНОЕ состояние салонной строки,
    #     а не её недостача (см. :func:`_link_of`).
    if link is None:
        return _create_for_salon(
            master, tenant=tenant, http_client=http_client, master_pk=master_pk
        )

    # 3b. соло — прежняя ветвь без изменений. Связь есть, а человека нет:
    #     провижинингу нечем назвать claim, и это по-прежнему
    #     ``creation_unavailable``. В салонную ветвь такую строку не
    #     отправляем: её тенант — соло-кабинет, каталог ответил бы
    #     ``tenant_is_solo``, и мы бы обменяли честное имя на чужое.
    if person is None:
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
    "SALON_CLAIM_PREFIX",
    "ensure_catalog_specialist_identity",
    "salon_provisioning_claim",
]
