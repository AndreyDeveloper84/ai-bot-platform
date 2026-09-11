"""A recognisable, read-only card for one messenger identity (owner 11.09 §12).

Owner §12, in order: nothing is deleted by a list of ids; first a
recognisable read-only card — masked name/phone, role, tenant, first/last
seen, linked context as counts; if that cannot be shown safely, each person
gets ``/whoami``; until a specific person is confirmed the status is
``BLOCKED_BY_IDENTITY``; freeing an account is only ever the two-sided
command (#1597 / #346).

The measurement of 11.09 (``docs/MEASUREMENT_MAX_ACCOUNTS_IDENTITY_CARD_2026-09-11.md``)
found that name and phone are EMPTY for every one of the six real ids, on
both sides. So the card is not built from them; it is built from context —
tenant, role, master card, dates, counts — and the name/phone slots are
kept so that the day they are filled, they show up masked rather than raw.

### Two audiences, one card, two renderings

The operator's command (``identity_card``) sees tenants by slug and every
personal value masked: a first letter and a length, a phone as presence
and length (DRF-1039: the phone is never handed to the operator). The person
themselves (``/whoami``) sees their own values whole — it is their data —
but only the shell of the bot they are talking to, and other salons as a
NUMBER: listing a client's other salons by name would draw the catalogue's
structure for anyone who asked.

### What this module does not know

Goals, questionnaire runs, food logs and confirmed appointments live in the
catalog. Bookings too: the bot's BookingRequest is a mirror that diverges from
Ayla REST once BOOKING_VIA_AYLA_REST is on (G9), so it is not counted here
either. This card carries the bot's own counts (dialogues, consents, memory
entries); the catalog half is the catalog's command. Named here so the card is not read as complete.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from apps.identity.models import BotUser, MemoryEntry

#: §12.4 — the status every one of these accounts carries until a person is
#: confirmed. The reset commands do not read it (their gate is the allowlist);
#: it is printed on the card so the operator sees the rule next to the data.
BLOCKED_BY_IDENTITY = "BLOCKED_BY_IDENTITY"

#: The typed command, exact match after strip — the same discipline as `/start`.
WHOAMI_COMMAND = "/whoami"


def mask_name(value: str | None) -> str:
    """``Анна`` → ``А… (4)``; empty → ``нет``."""
    value = (value or "").strip()
    if not value:
        return "нет"
    return f"{value[0]}… ({len(value)})"


def mask_phone(value: str | None) -> str:
    """Presence and length only — never a digit (DRF-1039)."""
    value = (value or "").strip()
    if not value:
        return "нет"
    return f"есть, длина {len(value)}"


@dataclass(frozen=True)
class Shell:
    """One ``BotUser`` — one (channel, id) inside one tenant."""

    bot_user_id: uuid.UUID
    tenant_slug: str
    display_name: str
    phone: str
    first_seen: datetime
    last_seen: datetime
    has_ayla_link: bool
    roles: tuple[str, ...]
    has_master_card: bool
    conversations: int
    last_message_at: datetime | None
    consents: int


@dataclass(frozen=True)
class IdentityCard:
    channel: str
    channel_user_id: str
    shells: tuple[Shell, ...]
    ayla_user_ids: tuple[uuid.UUID, ...]
    memory_entries: int
    status: str = BLOCKED_BY_IDENTITY
    #: What this card deliberately does not carry.
    not_here: tuple[str, ...] = field(
        default=(
            "цели и прогоны анкеты — каталог",
            "записи еды — каталог",
            "записи на приём и заявки — каталог (Ayla REST — источник; зеркало бота расходится с ним, G9)",
        )
    )

    @property
    def found(self) -> bool:
        return bool(self.shells)


def _shell_of(bu: BotUser) -> Shell:
    from apps.catalog.models import CatalogMaster
    from apps.consent.models import ConsentRecord
    from apps.conversations.models import Conversation, Message
    from apps.tenancy.models import TenantStaff

    conversations = Conversation.all_tenants.filter(bot_user_id=bu.id)
    last_message = (
        Message.all_tenants.filter(conversation__in=conversations)
        .order_by("-created_at")
        .values_list("created_at", flat=True)
        .first()
    )
    roles = tuple(
        TenantStaff.all_tenants.filter(bot_user_id=bu.id, deactivated_at__isnull=True)
        .order_by("role")
        .values_list("role", flat=True)
    )
    return Shell(
        bot_user_id=bu.id,
        tenant_slug=bu.tenant.slug,
        display_name=bu.display_name or "",
        phone=bu.phone or "",
        first_seen=bu.first_seen,
        last_seen=bu.last_seen,
        has_ayla_link=bu.ayla_user_id is not None,
        roles=roles,
        # Person-level by construction (see the MKT1 pin in tools/lint):
        # the card is about one messenger identity across every salon it
        # appears in, looked up by that identity's own key.
        has_master_card=CatalogMaster.all_tenants.filter(linked_bot_user_id=bu.id).exists(),
        conversations=conversations.count(),
        last_message_at=last_message,
        consents=ConsentRecord.all_tenants.filter(bot_user_id=bu.id).count(),
    )


def build_card(channel: str, channel_user_id: str) -> IdentityCard:
    """Read-only. Every shell of one messenger identity, across tenants."""
    shells = [
        _shell_of(bu)
        for bu in BotUser.all_tenants.filter(channel=channel, channel_user_id=channel_user_id)
        .select_related("tenant")
        .order_by("first_seen", "id")
    ]
    ayla_ids = tuple(
        sorted(
            {
                bu.ayla_user_id
                for bu in BotUser.all_tenants.filter(
                    channel=channel, channel_user_id=channel_user_id
                )
                if bu.ayla_user_id is not None
            }
        )
    )
    memory = MemoryEntry.objects.filter(user_id__in=ayla_ids).count() if ayla_ids else 0
    return IdentityCard(
        channel=channel,
        channel_user_id=channel_user_id,
        shells=tuple(shells),
        ayla_user_ids=ayla_ids,
        memory_entries=memory,
    )


# --- renderings ---------------------------------------------------------------


def _d(value: datetime | None) -> str:
    return value.date().isoformat() if value else "—"


def render_for_operator(card: IdentityCard) -> str:
    """Masked, tenants by slug, status on top. What the command prints."""
    if not card.found:
        return f"{card.channel}:{card.channel_user_id} — оболочек BotUser нет; карточки нет"
    lines = [
        f"{card.channel}:{card.channel_user_id}    статус: {card.status}",
        f"оболочек: {len(card.shells)}    ключей Ayla: {len(card.ayla_user_ids)}"
        f"    записей памяти: {card.memory_entries}",
        "",
        f"{'тенант':18} {'имя':12} {'телефон':16} {'первый':10} {'последний':10} "
        f"{'Ayla':5} {'роли':12} {'карточка':8} {'диалог':6} {'посл.сообщ':10} {'согл':4}",
    ]
    for s in card.shells:
        lines.append(
            f"{s.tenant_slug:18} {mask_name(s.display_name):12} {mask_phone(s.phone):16} "
            f"{_d(s.first_seen):10} {_d(s.last_seen):10} {'да' if s.has_ayla_link else 'нет':5} "
            f"{','.join(s.roles) or '—':12} {'да' if s.has_master_card else 'нет':8} "
            f"{s.conversations:6} {_d(s.last_message_at):10} {s.consents:4}"
        )
    lines.append("")
    lines.append("не в этой карточке: " + "; ".join(card.not_here))
    return "\n".join(lines)


def render_for_person(card: IdentityCard, *, tenant_slug: str | None) -> str:
    """The person's own card, unmasked, for ``/whoami``.

    ``tenant_slug`` is the bot they are talking to: the salon bot shows the
    shell in that salon; the client bot (``None``) shows the global shell.
    Every other salon is a number, never a name.
    """
    if not card.found:
        return "Я вас пока не знаю: в этом боте у вас нет ни одной записи."
    mine = [s for s in card.shells if (s.tenant_slug == tenant_slug if tenant_slug else True)]
    if tenant_slug is not None:
        mine = mine[:1]
    others = len(card.shells) - len(mine)
    lines = ["Что я о вас знаю:"]
    for s in mine:
        lines.append(f"• имя: {s.display_name or 'не указано'}")
        lines.append(f"• телефон: {s.phone or 'не указан'}")
        if tenant_slug is not None:
            lines.append(f"• роль здесь: {', '.join(s.roles) or 'клиент'}")
            lines.append(f"• карточка мастера: {'есть' if s.has_master_card else 'нет'}")
        lines.append(f"• впервые: {_d(s.first_seen)}, последний раз: {_d(s.last_seen)}")
        lines.append(f"• диалогов: {s.conversations}")
    if tenant_slug is None:
        lines.append(f"• записей в памяти: {card.memory_entries}")
    if others:
        lines.append(f"• ещё в салонах: {others}")
    return "\n".join(lines)


__all__ = [
    "BLOCKED_BY_IDENTITY",
    "WHOAMI_COMMAND",
    "IdentityCard",
    "Shell",
    "build_card",
    "mask_name",
    "mask_phone",
    "render_for_operator",
    "render_for_person",
]
