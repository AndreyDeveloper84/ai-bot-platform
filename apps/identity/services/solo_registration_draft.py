"""Черновик регистрации соло-мастера: имя → город → подтверждение (DRF-1793, M1).

Слово владельца (PROMPT §12): «Я работаю сам» → имя → город → сводка →
«Создать мой профиль» → явное подтверждение → создание. Тенант не
создаётся до подтверждения; имя из MAX — prefill, который человек может
исправить; город — из контролируемого списка обслуживаемых городов, не
свободный текст.

Этот модуль знает ТОЛЬКО про черновик: завести, принять имя, принять
город, отдать сводку, снять. Создание кабинета остаётся в
``create_solo_provider`` — черновик отдаёт ему готовые значения и
удаляется. Никакого LLM: имя — строка после ``strip`` с проверкой длины,
город — выбор кнопкой из списка.

Черновик durable и ключуется личностью ``(channel, channel_user_id)`` —
у незнакомца строки ``BotUser`` нет (DRF-1784), а три шага должны
пережить TTL чата и перезапуск (фриз §19). Протухает по ``expires_at``:
протухший читается как «черновика нет», и человек начинает заново, а не
подтверждает имя трёхнедельной давности.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from apps.identity.models import SoloRegistrationDraft

logger = logging.getLogger(__name__)

NAME_MIN_LEN = 2
NAME_MAX_LEN = 80


class NameRejected(ValueError):
    """Имя не годится; ``reason`` — машинное имя причины для текста ответа."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class CityRejected(ValueError):
    """Город не из контролируемого списка (или список пуст)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class CityOption:
    """Кнопка города: хранимое написание + короткий стабильный код для callback."""

    name: str
    code: str


def served_cities() -> list[str]:
    """Контролируемый список городов — из настроек, хранимые написания."""

    raw = getattr(settings, "SOLO_REGISTRATION_CITIES", None) or []
    seen: list[str] = []
    for city in raw:
        city = (city or "").strip()
        if city and city not in seen:
            seen.append(city)
    return seen


def city_code(name: str) -> str:
    """Стабильный код города для callback-кнопки — от написания, не от позиции в списке.

    Индекс в списке поехал бы при перестановке настроек между двумя
    сообщениями; хеш написания — нет.
    """

    return hashlib.sha256(name.encode("utf-8")).hexdigest()[:8]


def city_options() -> list[CityOption]:
    return [CityOption(name=c, code=city_code(c)) for c in served_cities()]


def resolve_city(code: str) -> str:
    """Код кнопки → хранимое написание; неизвестный код — отказ с причиной."""

    options = city_options()
    if not options:
        raise CityRejected("no_cities_configured")
    for option in options:
        if option.code == code:
            return option.name
    raise CityRejected("unknown_city")


def clean_name(raw: str) -> str:
    """Имя для клиентов: обрезать, проверить длину, отвергнуть кнопки и пустоту."""

    name = " ".join((raw or "").split())
    if not name:
        raise NameRejected("empty")
    if name.startswith("cb:") or name.startswith("/"):
        raise NameRejected("looks_like_a_command")
    if len(name) < NAME_MIN_LEN:
        raise NameRejected("too_short")
    if len(name) > NAME_MAX_LEN:
        raise NameRejected("too_long")
    if not any(ch.isalpha() for ch in name):
        raise NameRejected("no_letters")
    return name


def _ttl() -> timedelta:
    hours = int(getattr(settings, "SOLO_REGISTRATION_DRAFT_TTL_HOURS", 72) or 72)
    return timedelta(hours=hours)


def get_draft(*, channel: str, channel_user_id: str) -> SoloRegistrationDraft | None:
    """Живой черновик личности; протухший снимается и читается как отсутствующий."""

    draft = SoloRegistrationDraft.objects.filter(
        channel=channel, channel_user_id=channel_user_id
    ).first()
    if draft is None:
        return None
    if draft.expires_at <= timezone.now():
        logger.info(
            "identity.solo_draft.expired channel_user_id=%s step=%s", channel_user_id, draft.step
        )
        draft.delete()
        return None
    return draft


def start_draft(
    *, channel: str, channel_user_id: str, chat_id: str = "", prefill_name: str = ""
) -> SoloRegistrationDraft:
    """Новый черновик (перезаписывает прежний): шаг «имя», prefill из MAX."""

    now = timezone.now()
    draft, _created = SoloRegistrationDraft.objects.update_or_create(
        channel=channel,
        channel_user_id=channel_user_id,
        defaults={
            "chat_id": chat_id or "",
            "step": SoloRegistrationDraft.Step.NAME,
            "display_name": _safe_prefill(prefill_name),
            "city": "",
            "expires_at": now + _ttl(),
        },
    )
    return draft


def _safe_prefill(raw: str) -> str:
    try:
        return clean_name(raw)
    except NameRejected:
        return ""


def accept_name(draft: SoloRegistrationDraft, raw: str) -> SoloRegistrationDraft:
    """Принять имя (введённое или подтверждённый prefill) → шаг «город»."""

    draft.display_name = clean_name(raw)
    draft.step = SoloRegistrationDraft.Step.CITY
    draft.expires_at = timezone.now() + _ttl()
    draft.save(update_fields=["display_name", "step", "expires_at", "updated_at"])
    return draft


def accept_city(draft: SoloRegistrationDraft, code: str) -> SoloRegistrationDraft:
    """Принять город по коду кнопки → шаг «подтверждение»."""

    draft.city = resolve_city(code)
    draft.step = SoloRegistrationDraft.Step.CONFIRM
    draft.expires_at = timezone.now() + _ttl()
    draft.save(update_fields=["city", "step", "expires_at", "updated_at"])
    return draft


def back_to_name(draft: SoloRegistrationDraft) -> SoloRegistrationDraft:
    """«Изменить имя» со сводки — назад на шаг имени, город остаётся."""

    draft.step = SoloRegistrationDraft.Step.NAME
    draft.expires_at = timezone.now() + _ttl()
    draft.save(update_fields=["step", "expires_at", "updated_at"])
    return draft


def is_confirmable(draft: SoloRegistrationDraft) -> bool:
    """Подтверждать можно только полный черновик на шаге сводки."""

    return (
        draft.step == SoloRegistrationDraft.Step.CONFIRM
        and bool(draft.display_name)
        and bool(draft.city)
    )


def discard_draft(draft: SoloRegistrationDraft) -> None:
    draft.delete()


__all__ = [
    "CityOption",
    "CityRejected",
    "NameRejected",
    "accept_city",
    "accept_name",
    "back_to_name",
    "city_code",
    "city_options",
    "clean_name",
    "discard_draft",
    "get_draft",
    "is_confirmable",
    "resolve_city",
    "served_cities",
    "start_draft",
]
