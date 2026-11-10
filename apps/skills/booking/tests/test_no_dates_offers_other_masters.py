"""«Нет свободных дат» предлагает других мастеров той же услуги (09.11.2026).

Приёмка пилота: бот нашёл трёх мастеров, первый тап — «У выбранного мастера
нет свободных дат в ближайшее время» и ни одной кнопки. На стенде у 24 из
33 продаваемых мастеров нет рабочих часов в Ayla, так что тупик был типичным
исходом. Теперь при пустом списке дат бот показывает кнопки других
продаваемых мастеров услуги; кого уже проверили, помнит в ``skill_state`` и
не предлагает снова; когда проверены все — говорит об этом.

Красный до правки: текст прежний и кнопок нет при любом наборе мастеров.
"""

from __future__ import annotations

import itertools
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, cast

import pytest
from django.utils import timezone as dj_timezone

from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.conversations.models import Conversation
from apps.identity.models import BotUser
from apps.skills.booking.provider import AylaYClientsAdapter
from apps.skills.booking.skill import (
    _DATE_PICKER_FALLBACK_NO_DATES,
    _NO_DATES_NOBODY_LEFT,
    _NO_DATES_OTHER_MASTERS_PROMPT,
    _NO_DATES_STATE_KEY,
    _render_date_picker,
)
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

SERVICE_AYLA_ID = uuid.UUID("0c5f0000-0000-4000-8000-0000000000da")
OTHER_SERVICE_AYLA_ID = uuid.UUID("0c5f0000-0000-4000-8000-0000000000db")
STAMP = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)
_EXTERNAL_IDS = itertools.count(9100)


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="no-dates", name="No dates", timezone="Europe/Moscow")


@pytest.fixture
def conversation(tenant: Tenant) -> Conversation:
    bot_user = BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="no-dates-u1",
        chat_id="no-dates-u1",
        welcomed_at=dj_timezone.now(),
    )
    return Conversation.all_tenants.create(tenant=tenant, bot_user=bot_user)


@pytest.fixture
def service(tenant: Tenant) -> CatalogService:
    return _service(tenant, external_id=9001, slug="massage", ayla_id=SERVICE_AYLA_ID)


def _service(tenant: Tenant, *, external_id: int, slug: str, ayla_id: uuid.UUID) -> CatalogService:
    return CatalogService.all_tenants.create(
        tenant=tenant,
        external_id=external_id,
        external_updated_at=STAMP,
        slug=slug,
        name=slug.capitalize(),
        duration_min=60,
        is_active=True,
        ayla_service_id=ayla_id,
    )


def _master(
    tenant: Tenant, name: str, *services: CatalogService, sellable: bool = True
) -> CatalogMaster:
    row = CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_id=next(_EXTERNAL_IDS),
        external_updated_at=STAMP,
        name=name,
        is_active=True,
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
        ayla_user_id=uuid.uuid4(),
    )
    CatalogMaster.all_tenants.filter(pk=row.pk).update(catalog_specialist_id=uuid.uuid4())
    row.refresh_from_db()
    for svc in services:
        edge = MasterService.all_tenants.create(
            tenant=tenant, master=row, service=svc, resolved_requires_health_check=False
        )
        if not sellable:
            MasterService.all_tenants.filter(pk=edge.pk).update(
                sellable=False, unsellable_reason="price_below_minimum"
            )
    return row


class _NoDates:
    """Каталог Ayla, у которого ни у кого нет свободных дат."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    def get_available_dates(self, *, specialist_id: str, service_id: str) -> list[str]:
        self.asked.append(specialist_id)
        return []


def _adapter(tenant: Tenant) -> AylaYClientsAdapter:
    return AylaYClientsAdapter(
        client=cast(Any, _NoDates()),
        external_user_id="bot:max:no-dates",
        client_id="c-nd",
        tenant=tenant,
    )


def _tap(
    tenant: Tenant,
    master: CatalogMaster,
    conversation: Conversation | None,
    *,
    service_id: uuid.UUID = SERVICE_AYLA_ID,
    yclients: Any = None,
    by_catalog_id: bool = False,
) -> Any:
    """Тап по мастеру. По умолчанию колбэк несёт pk зеркала (кнопка консьержа
    и кнопка этой же правки); ``by_catalog_id`` — id каталога, как у карточек
    после поиска в боте салона (``_to_yc_staff``)."""
    master_id = master.catalog_specialist_id if by_catalog_id else master.pk
    with tenant_scope(tenant):
        return _render_date_picker(
            master_id=str(master_id),
            service_id=str(service_id),
            yclients=yclients or _adapter(tenant),
            tenant_id=str(tenant.id),
            tenant=tenant,
            conversation=conversation,
        )


def _offered(result: Any, *, service_id: uuid.UUID = SERVICE_AYLA_ID) -> list[str]:
    """pk мастеров из кнопок ``cb:book:pick_master:<pk>:<service>``."""
    out: list[str] = []
    for attachment in (result.action_data or {}).get("attachments", []):
        for button in attachment.get("payload", {}).get("buttons", []):
            callback = button.get("callback", "")
            assert callback.startswith("cb:book:pick_master:"), callback
            master_pk, _, service = callback[len("cb:book:pick_master:") :].partition(":")
            assert service == str(service_id)
            out.append(master_pk)
    return out


def test_other_masters_of_the_service_are_offered(tenant, service, conversation, settings):
    settings.BOOKING_VIA_AYLA_REST = True
    tapped = _master(tenant, "Анна", service)
    colleague = _master(tenant, "Борис", service)
    _master(
        tenant,
        "Вера",
        _service(tenant, external_id=9002, slug="other", ayla_id=OTHER_SERVICE_AYLA_ID),
    )

    result = _tap(tenant, tapped, conversation)

    assert result.reply_text == _NO_DATES_OTHER_MASTERS_PROMPT
    assert _offered(result) == [str(colleague.pk)]


def test_unsellable_and_unavailable_colleagues_are_not_offered(
    tenant, service, conversation, settings
):
    settings.BOOKING_VIA_AYLA_REST = True
    tapped = _master(tenant, "Анна", service)
    _master(tenant, "Борис", service, sellable=False)
    gone = _master(tenant, "Вера", service)
    CatalogMaster.all_tenants.filter(pk=gone.pk).update(is_active=False)
    kept = _master(tenant, "Глеб", service)

    result = _tap(tenant, tapped, conversation)

    assert _offered(result) == [str(kept.pk)]


def test_the_sole_master_keeps_the_old_reply_without_buttons(
    tenant, service, conversation, settings
):
    settings.BOOKING_VIA_AYLA_REST = True
    only = _master(tenant, "Анна", service)

    result = _tap(tenant, only, conversation)

    assert result.reply_text == _DATE_PICKER_FALLBACK_NO_DATES
    assert result.action_data is None


def test_masters_already_found_empty_are_not_offered_again(tenant, service, conversation, settings):
    settings.BOOKING_VIA_AYLA_REST = True
    first = _master(tenant, "Анна", service)
    second = _master(tenant, "Борис", service)
    third = _master(tenant, "Вера", service)

    after_first = _tap(tenant, first, conversation)
    conversation.refresh_from_db()
    after_second = _tap(tenant, second, conversation)
    conversation.refresh_from_db()
    after_third = _tap(tenant, third, conversation)

    assert _offered(after_first) == [str(second.pk), str(third.pk)]
    assert _offered(after_second) == [str(third.pk)]
    assert after_third.reply_text == _NO_DATES_NOBODY_LEFT
    assert after_third.action_data is None
    remembered = conversation.skill_state[_NO_DATES_STATE_KEY]
    assert remembered["service_id"] == str(SERVICE_AYLA_ID)
    assert remembered["masters"] == [str(first.pk), str(second.pk), str(third.pk)]


def test_memory_is_per_service(tenant, service, conversation, settings):
    settings.BOOKING_VIA_AYLA_REST = True
    other = _service(tenant, external_id=9002, slug="other", ayla_id=OTHER_SERVICE_AYLA_ID)
    both = _master(tenant, "Анна", service, other)
    colleague = _master(tenant, "Борис", service, other)

    _tap(tenant, colleague, conversation)
    conversation.refresh_from_db()
    result = _tap(tenant, both, conversation, service_id=OTHER_SERVICE_AYLA_ID)

    assert _offered(result, service_id=OTHER_SERVICE_AYLA_ID) == [str(colleague.pk)]
    assert result.reply_text == _NO_DATES_OTHER_MASTERS_PROMPT


def test_a_tap_by_catalog_id_excludes_that_master_and_mixes_with_pk_memory(
    tenant, service, conversation, settings
):
    """Карточки после поиска в боте салона несут id каталога, кнопки этой
    правки — pk. Один и тот же мастер под разными ключами — один мастер."""
    settings.BOOKING_VIA_AYLA_REST = True
    tapped = _master(tenant, "Анна", service)
    colleague = _master(tenant, "Борис", service)

    first = _tap(tenant, tapped, conversation, by_catalog_id=True)
    conversation.refresh_from_db()
    second = _tap(tenant, colleague, conversation)
    conversation.refresh_from_db()
    again = _tap(tenant, tapped, conversation)

    assert _offered(first) == [str(colleague.pk)]
    assert second.reply_text == _NO_DATES_NOBODY_LEFT
    assert again.reply_text == _NO_DATES_NOBODY_LEFT
    assert conversation.skill_state[_NO_DATES_STATE_KEY]["masters"] == [
        str(tapped.catalog_specialist_id),
        str(colleague.pk),
        str(tapped.pk),
    ]


def test_a_colleague_without_a_catalog_id_is_not_offered(tenant, service, conversation, settings):
    """Кнопка ведёт за датами в каталог Ayla; мастер без ключа каталога там
    не находится, и тап кончился бы передачей менеджеру."""
    settings.BOOKING_VIA_AYLA_REST = True
    tapped = _master(tenant, "Анна", service)
    unlinked = _master(tenant, "Борис", service)
    CatalogMaster.all_tenants.filter(pk=unlinked.pk).update(catalog_specialist_id=None)

    result = _tap(tenant, tapped, conversation)

    assert result.reply_text == _DATE_PICKER_FALLBACK_NO_DATES
    assert result.action_data is None


def test_expired_memory_starts_over(tenant, service, conversation, settings):
    settings.BOOKING_VIA_AYLA_REST = True
    tapped = _master(tenant, "Анна", service)
    colleague = _master(tenant, "Борис", service)
    stale = {
        "service_id": str(SERVICE_AYLA_ID),
        "masters": [str(colleague.pk)],
        "expires_at": (dj_timezone.now() - timedelta(minutes=1)).isoformat(),
    }
    Conversation.all_tenants.filter(pk=conversation.pk).update(
        skill_state={_NO_DATES_STATE_KEY: stale}
    )
    conversation.refresh_from_db()

    result = _tap(tenant, tapped, conversation)

    assert _offered(result) == [str(colleague.pk)]
    conversation.refresh_from_db()
    assert conversation.skill_state[_NO_DATES_STATE_KEY]["masters"] == [str(tapped.pk)]


def test_the_offer_survives_a_failed_state_write(
    tenant, service, conversation, settings, monkeypatch
):
    settings.BOOKING_VIA_AYLA_REST = True
    tapped = _master(tenant, "Анна", service)
    colleague = _master(tenant, "Борис", service)

    def boom(*_: Any, **__: Any) -> None:
        raise RuntimeError("no state for you")

    monkeypatch.setattr("apps.conversations.services.write_skill_state", boom)

    result = _tap(tenant, tapped, conversation)

    assert _offered(result) == [str(colleague.pk)]
    conversation.refresh_from_db()
    assert _NO_DATES_STATE_KEY not in (conversation.skill_state or {})


def test_without_a_conversation_the_offer_is_still_made(tenant, service, settings):
    settings.BOOKING_VIA_AYLA_REST = True
    tapped = _master(tenant, "Анна", service)
    colleague = _master(tenant, "Борис", service)

    result = _tap(tenant, tapped, None)

    assert _offered(result) == [str(colleague.pk)]


def test_with_the_rest_flag_off_the_reply_is_unchanged(tenant, service, conversation, settings):
    settings.BOOKING_VIA_AYLA_REST = False
    tapped = _master(tenant, "Анна", service)
    _master(tenant, "Борис", service)

    class _Legacy:
        def get_available_dates(self, *, staff_id: Any, service_ids: Any) -> list[str]:
            return []

    result = _tap(tenant, tapped, conversation, yclients=_Legacy())

    assert result.reply_text == _DATE_PICKER_FALLBACK_NO_DATES
    assert result.action_data is None
    conversation.refresh_from_db()
    assert _NO_DATES_STATE_KEY not in (conversation.skill_state or {})


def test_the_sole_master_leaves_the_dialog_state_alone(tenant, service, conversation, settings):
    settings.BOOKING_VIA_AYLA_REST = True
    only = _master(tenant, "Анна", service)

    _tap(tenant, only, conversation)

    conversation.refresh_from_db()
    assert _NO_DATES_STATE_KEY not in (conversation.skill_state or {})


def test_found_dates_forget_the_masters_checked_before(tenant, service, conversation, settings):
    """Расписание могли дописать минуту назад: как только у мастера нашлись
    даты, список «проверенных без дат» стирается, и при следующем тупике
    эти мастера предлагаются снова."""
    settings.BOOKING_VIA_AYLA_REST = True
    tapped = _master(tenant, "Анна", service)
    colleague = _master(tenant, "Борис", service)

    class _Dates:
        def get_available_dates(self, *, specialist_id: str, service_id: str) -> list[str]:
            return ["2026-11-20", "2026-11-21"]

    _tap(tenant, colleague, conversation)
    conversation.refresh_from_db()
    assert conversation.skill_state[_NO_DATES_STATE_KEY]["masters"] == [str(colleague.pk)]

    with_dates = AylaYClientsAdapter(
        client=cast(Any, _Dates()),
        external_user_id="bot:max:no-dates",
        client_id="c-nd",
        tenant=tenant,
    )
    _tap(tenant, tapped, conversation, yclients=with_dates)
    conversation.refresh_from_db()

    assert _NO_DATES_STATE_KEY not in (conversation.skill_state or {})
