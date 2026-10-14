"""DRF-2967 — досылка отзывов согласия на хранение в каталог.

Каталог до 09.10 принимал события ``personal_data`` и не хранил их (вид не
входил в обрабатываемые). Отзывы, случившиеся до выкладки его правки, ему
неизвестны, и вторая линия гейта плана на таких людях слепа: её правило
«отзыв позже присланной даты согласия → отказ» сравнивать не с чем.

Досылается только ТЕКУЩЕЕ состояние «отозвано»: человек, у которого
согласие сейчас не действует, а отзыв когда-то был. Действующее согласие не
досылается — его каталогу на каждом вызове утверждает сам бот
(:func:`apps.orchestrator.plan_gate.plan_consent_basis`).

Субъект каталогу называет ``X-External-User-ID`` — он один на человека
(канал + идентификатор в канале), поэтому событие одно на человека, а не на
оболочку. ``granted_at`` — момент последнего отзыва, как в живом событии
отзыва (:func:`apps.consent.services.withdraw`). ``event_id`` выводится из
человека и момента отзыва: повторный прогон каталог узнаёт как ``duplicate``.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime

from apps.consent.models import ConsentRecord

PERSONAL_DATA = ConsentRecord.ConsentType.PERSONAL_DATA.value
GRANTED_VIA = "resend:drf-2967"

_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "ayla:consent-resend:personal_data")


@dataclass(frozen=True)
class WithdrawnPerson:
    """Человек, чьё согласие на хранение сейчас отозвано."""

    external_user_id: str
    withdrawn_at: datetime

    @property
    def event_id(self) -> str:
        return str(
            uuid.uuid5(_NAMESPACE, f"{self.external_user_id}|{self.withdrawn_at.isoformat()}")
        )

    def body(self) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "consent_type": PERSONAL_DATA,
            "granted": False,
            "granted_at": self.withdrawn_at.isoformat(),
            "granted_via": GRANTED_VIA,
        }


def withdrawn_people() -> Iterator[WithdrawnPerson]:
    """Люди с отозванным согласием на хранение — по одному на человека.

    Правило «отозвано» — то же, что у
    :func:`apps.consent.services.has_person_consent`, читается его же
    глазами: отзыв был, и действующего согласия позже него нет. Человек без
    идентификатора в канале каталогу не называется — пропускается.
    """
    from apps.consent.services import has_person_consent
    from apps.identity.models import BotUser
    from apps.integrations.ayla.user_proxy import external_user_id_for

    seen: set[tuple[str, str]] = set()
    shells = (
        BotUser.all_tenants.filter(
            consents__consent_type=PERSONAL_DATA,
            consents__withdrawn_at__isnull=False,
        )
        .distinct()
        .order_by("pk")
    )
    for shell in shells.iterator():
        channel = (shell.channel or "").strip()
        channel_user_id = (shell.channel_user_id or "").strip()
        if not channel or not channel_user_id or (channel, channel_user_id) in seen:
            continue
        seen.add((channel, channel_user_id))
        if has_person_consent(shell, PERSONAL_DATA):
            continue
        latest = (
            ConsentRecord.all_tenants.filter(
                bot_user__channel=channel,
                bot_user__channel_user_id=channel_user_id,
                consent_type=PERSONAL_DATA,
                withdrawn_at__isnull=False,
            )
            .order_by("-withdrawn_at")
            .values_list("withdrawn_at", flat=True)
            .first()
        )
        if latest is None:
            continue
        yield WithdrawnPerson(external_user_id=external_user_id_for(shell), withdrawn_at=latest)


__all__ = ["GRANTED_VIA", "PERSONAL_DATA", "WithdrawnPerson", "withdrawn_people"]
