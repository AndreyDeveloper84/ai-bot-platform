"""Утверждение о согласии на персональный расчёт — для границы каталога (DRF-1658, N-a3).

Каталог (PR beautygo_backend#324, §92 срез N-a2) перестаёт принимать
параметры тела «на слово»: POST ``/internal/profile/`` с любым из полей
``gender / age / height_cm / weight_kg / weight_range /
activity_coefficient / goal`` обязан нести **утверждение** вызывающего —
какого вида согласие и под какой версией текста получено:

    "consent": {"type": "personal_calculation", "document_version": "<v>"}

Без него граница отвечает ``422 CONSENT_REQUIRED``. Каталог согласия не
хранит и не судит о нём — реестр один и живёт здесь, в
:class:`apps.consent.models.ConsentRecord`. Этот модуль — вторая
половина того же контракта: бот берёт действующую запись реестра и
превращает её в утверждение той формы, которую ждёт граница. Форма
скопирована из #324 дословно, а не придумана: два конца одного контракта
должны совпасть байт в байт.

### Почему версия, а не флаг

«Согласен» без версии через месяц не докажет, на что человек соглашался.
§92 п.6 требует хранить версию текста — и граница отвергает пустую. Поэтому
запись с ``granted=True`` и пустым ``document_version`` здесь **не
основание**: она даёт отказ :data:`NO_DOCUMENT_VERSION`, отдельный от
:data:`NOT_GRANTED`. Слить их значило бы чинить «дай согласие» там, где
чинить надо «под какой текст».

### Почему строка, а не член перечисления

Тип ``personal_calculation`` вводит в ``ConsentRecord.ConsentType`` PR
ai-bot-platform#1523 (срез N-a), ещё не слитый. Здесь он назван строкой
намеренно, чтобы не стековать PR: значение — контракт границы (константа
``PERSONAL_CALCULATION`` в ``nutrition/services/personal_calculation_consent.py``
каталога), а не имя из модели. **Когда #1523 сольётся — заменить строку на
``ConsentRecord.ConsentType.PERSONAL_CALCULATION.value``, не заводить
вторую константу.**

### Предел

Писателя для ``personal_calculation`` нет ни на ``dev``, ни в #1523 —
экрана согласия ещё нет. Значит до экрана каждый вызов
:func:`current_attestation` кончается :data:`NOT_GRANTED`, и анкета
остаётся закрытой — но закрытой **по названной причине** на нашей
стороне, а не 422 из каталога. Цепочка зависимостей:

    экран согласия (нет)  →  N-a3 (этот модуль)  →  #324 (граница)

Этот модуль анкету не воскрешает; он делает так, что воскресит её экран,
а не правка транспорта.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from apps.consent.models import ConsentRecord

if TYPE_CHECKING:
    from apps.identity.models import BotUser

logger = logging.getLogger(__name__)

#: Вид согласия — контракт границы каталога (#324), не имя из модели.
#: TODO(#1523): после слияния заменить на
#: ``ConsentRecord.ConsentType.PERSONAL_CALCULATION.value``.
PERSONAL_CALCULATION = "personal_calculation"

#: Ключ утверждения в теле POST профиля — как его читает
#: ``NutritionProfileUpsertSerializer.consent`` в каталоге.
PAYLOAD_KEY = "consent"

#: Версия текста, который человеку показывают перед анкетой. Меняется
#: ВМЕСТЕ с текстом: версия — снимок того, на что согласились, а не
#: украшение. При следующей редакции константа поднимается, старые
#: согласия перестают её предъявлять, и человека спрашивают заново.
#:
#: Та же дисциплина, что у ``HEALTH_CONSENT_DOCUMENT_VERSION``
#: (``apps/consent/health.py``) — форма взята оттуда намеренно, чтобы два
#: согласия не расходились в устройстве.
PERSONAL_CALCULATION_DOCUMENT_VERSION = "personal-calculation-v1"

#: Откуда пришла выдача и отзыв. Свободная форма по контракту модели.
GRANT_SOURCE = "max:nutrition_anketa_consent"
WITHDRAW_SOURCE = "max:nutrition_anketa_consent_withdraw"

# ── причины отказа ────────────────────────────────────────────────────────
# Три разных «нет», и у каждого свой адрес починки: первое чинится
# согласием человека, второе — текстом, под которым его взяли, третье —
# инфраструктурой. Одно имя на троих заставило бы читателя лога гадать.

#: Действующей записи реестра нет: не выдавалось или отозвано.
NOT_GRANTED = "not_granted"
#: Запись есть, но без версии текста — граница такую отвергнет, и мы тоже.
NO_DOCUMENT_VERSION = "no_document_version"
#: Реестр не ответил. Fail-closed: неизвестное основание — не основание.
LOOKUP_FAILED = "lookup_failed"


@dataclass(frozen=True)
class ConsentAttestation:
    """Что бот утверждает границе: вид согласия и версия текста.

    Поля названы как в ``ConsentAttestationSerializer`` каталога (#324).
    """

    type: str
    document_version: str

    def as_payload(self) -> dict[str, str]:
        """Форма для тела POST — ровно два ключа, которые ждёт граница."""
        return {"type": self.type, "document_version": self.document_version}


class ConsentAttestationUnavailable(Exception):
    """Утверждение собрать нельзя; ``reason`` — одна из трёх констант выше."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def _active_record(bot_user: BotUser) -> ConsentRecord | None:
    """Последняя действующая запись типа :data:`PERSONAL_CALCULATION`.

    ``all_tenants`` по той же причине, что у ``has_global_consent``:
    анкета живёт на глобальном пути, где ``current_tenant()`` пуст, а
    ``bot_user`` уже привязан ровно к одному тенанту. Вторичный ключ по
    ``id`` — чтобы при равном ``captured_at`` выбор не зависел от того,
    в каком порядке БД отдала строки.
    """
    return (
        ConsentRecord.all_tenants.filter(
            bot_user=bot_user,
            consent_type=PERSONAL_CALCULATION,
            granted=True,
            withdrawn_at__isnull=True,
        )
        .order_by("-captured_at", "-id")
        .first()
    )


def current_attestation(bot_user: BotUser) -> ConsentAttestation:
    """Утверждение о действующем согласии — или отказ с названной причиной.

    Raises:
      ConsentAttestationUnavailable: с ``reason`` из
        :data:`NOT_GRANTED` / :data:`NO_DOCUMENT_VERSION` /
        :data:`LOOKUP_FAILED`. Тихого ``None`` здесь нет намеренно:
        вызывающий обязан либо приложить утверждение, либо не слать
        параметры тела вовсе.
    """
    try:
        record = _active_record(bot_user)
    except Exception:  # noqa: BLE001 — неизвестное основание = нет основания
        logger.warning(
            "consent.personal_calculation.lookup_failed bot_user=%s",
            getattr(bot_user, "id", None),
            exc_info=True,
        )
        raise ConsentAttestationUnavailable(LOOKUP_FAILED) from None

    if record is None:
        raise ConsentAttestationUnavailable(NOT_GRANTED)

    version = (record.document_version or "").strip()
    if not version:
        raise ConsentAttestationUnavailable(NO_DOCUMENT_VERSION)

    return ConsentAttestation(type=PERSONAL_CALCULATION, document_version=version)


def attach(payload: dict[str, Any], attestation: ConsentAttestation) -> dict[str, Any]:
    """Тело POST с утверждением. Исходный словарь не меняется."""
    return {**payload, PAYLOAD_KEY: attestation.as_payload()}


class UnknownDisclosureVersionError(ValueError):
    """Показали одну версию текста, записать просят другую.

    Не педантизм: ``document_version`` — единственное доказательство
    того, ЧТО именно человеку показали в момент согласия. Принять чужую
    строку значит записать в юридический журнал непроверяемое
    утверждение, а граница каталога (#324) требует версию именно затем,
    чтобы через полгода согласие можно было отличить от согласия на
    другой текст.
    """


def grant(bot_user: BotUser, *, document_version: str) -> bool:
    """Записать согласие на персональный расчёт. Идемпотентно.

    Возвращает ``True``, если после вызова действующее согласие есть, —
    проверкой ЧТЕНИЕМ, а не оптимистичным «мы же только что записали».
    Читающая сторона (`current_attestation`) и пишущая обязаны сойтись,
    иначе экран скажет «разрешено», а анкета продолжит отказывать.

    Raises:
      UnknownDisclosureVersionError: версия не та, что показывали.
    """
    from apps.consent.services import record_person_consent

    if document_version != PERSONAL_CALCULATION_DOCUMENT_VERSION:
        raise UnknownDisclosureVersionError(document_version)

    record_person_consent(
        bot_user,
        consent_type=PERSONAL_CALCULATION,
        source=GRANT_SOURCE,
        document_version=PERSONAL_CALCULATION_DOCUMENT_VERSION,
    )
    return is_granted(bot_user)


def withdraw(bot_user: BotUser) -> int:
    """Отозвать согласие. Идемпотентно; строки не удаляются.

    §92 требует хранить отзыв, а не забывать его: ``withdrawn_at``
    проставляется, append-only журнал остаётся целиком.
    """
    from apps.consent.services import withdraw_person_consent

    return withdraw_person_consent(
        bot_user,
        consent_type=PERSONAL_CALCULATION,
        source=WITHDRAW_SOURCE,
    )


def is_granted(bot_user: BotUser) -> bool:
    """Есть ли действующее согласие СЕЙЧАС — тем же чтением, что у границы.

    Намеренно выражено через :func:`current_attestation`, а не своим
    запросом: экран не должен уметь сказать «разрешено» в случае, когда
    утверждение собрать нельзя. Один предикат — одна правда.
    """
    try:
        current_attestation(bot_user)
    except ConsentAttestationUnavailable:
        return False
    return True
