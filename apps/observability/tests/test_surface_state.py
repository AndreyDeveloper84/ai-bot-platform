"""`surface_state` бота: каждое число — против фикстуры с известным составом.

Команда читает базу и печатает числа. Тест не может сказать, что на
пилоте; он может сказать, что команда считает **то, что обещает
подпись**, и что её нули отличимы от «посчитали не то». Поэтому фикстура
собрана вручную с известным составом, и каждая строка сверяется с ним
дословно — вместе с источником (таблица.поле).
"""

from __future__ import annotations

import uuid
from io import StringIO

import pytest
from django.core.exceptions import FieldDoesNotExist
from django.core.management import call_command
from django.utils import timezone

from apps.catalog.models import CatalogMaster
from apps.conversations.models import Conversation, Message
from apps.identity.models import BotUser
from apps.identity.services.solo_onboarding import BOOTSTRAP_TENANT_SLUG, create_solo_provider
from apps.observability.management.commands.surface_state import (
    _FLAG_W,
    FILE_HEADER,
    FLAGS,
    _row,
)
from apps.observability.measurement_subject import PULSE_ANCHORS, Anchor, gather_pulse
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


def _run(**kwargs) -> str:
    out = StringIO()
    call_command("surface_state", stdout=out, **kwargs)
    return out.getvalue()


def _block(report: str, start: str, end: str | None = None) -> str:
    body = report.split(f"\n{start}", 1)[1]
    return body.split(f"\n{end}", 1)[0] if end else body


@pytest.fixture
def surface():
    """База с известным составом. Числа ниже — не «какие-то», а ровно эти.

    Тенанты существуют ДО фикстуры (миграции сеют системные строки), и
    их число не угадывается, а снимается до фикстуры: новая сидовая
    миграция сдвинет ожидание, а не сломает тест непонятным «5 != 3».
    """
    seeded = Tenant.all_objects.count()
    seeded_active = Tenant.all_objects.filter(is_active=True).count()
    seeded_system = Tenant.all_objects.filter(is_system=True).count()
    # `global_bot` из миграций приезжает С адресом — и это снято, а не
    # угадано: ожидание ниже прибавляет фикстуру к сидовому.
    seeded_city = Tenant.all_objects.exclude(city="").count()
    seeded_address = Tenant.all_objects.filter(address__isnull=False).exclude(address="").count()
    seeded_address_null = Tenant.all_objects.filter(address__isnull=True).count()
    seeded_synced = Tenant.all_objects.filter(last_catalog_sync_ok_at__isnull=False).count()
    assert Tenant.all_objects.filter(slug__startswith="solo-").count() == 0
    assert CatalogMaster.all_tenants.count() == 0
    assert BotUser.all_tenants.count() == 0

    # -- тенанты: салон с городом и адресом (синхронизировался), выключенный
    #    салон без города, бутстрап соло + два соло-тенанта от регистраций
    salon = Tenant.all_objects.create(
        slug="salon-a",
        name="Салон А",
        city="Пенза",
        address="Московская 1",
        last_catalog_sync_ok_at=timezone.now(),
    )
    Tenant.all_objects.create(slug="salon-off", name="Салон выкл", is_active=False)
    Tenant.all_objects.create(slug=BOOTSTRAP_TENANT_SLUG, name="Ayla Solo — Registration")
    solo_pending = create_solo_provider(
        channel="max", channel_user_id="solo-1", display_name="Ольга"
    )
    solo_ready = create_solo_provider(channel="max", channel_user_id="solo-2", display_name="Анна")
    # Второй соло-мастер доведён до продаваемого: sale_block → None
    master = solo_ready.master
    master.invite_status = CatalogMaster.InviteStatus.ACCEPTED
    master.is_active = True
    master.ayla_user_id = uuid.uuid4()
    master.save()
    assert solo_ready.setup_state.value == "ready", solo_ready.blocked_by

    # -- салонные карточки: одна из синхронизации (без BotUser, с ayla_user_id),
    #    одна в архиве
    CatalogMaster.all_tenants.create(
        tenant=salon,
        external_id=101,
        external_updated_at=timezone.now(),
        name="Синхронизированная",
        ayla_user_id=uuid.uuid4(),
    )
    CatalogMaster.all_tenants.create(
        tenant=salon,
        external_id=102,
        external_updated_at=timezone.now(),
        name="В архиве",
        archived_at=timezone.now(),
    )

    # -- пользователи бота: 2 соло (от регистраций) + 1 клиент салона
    client = BotUser.all_tenants.create(
        tenant=salon, channel="max", channel_user_id="c-1", display_name="Клиентка"
    )
    conversation = Conversation.all_tenants.create(tenant=salon, bot_user=client)
    Message.all_tenants.create(
        tenant=salon,
        conversation=conversation,
        role=Message.Role.USER,
        content="привет",
        rendered_text="привет",
    )

    return {
        "tenants": {
            "total": seeded + 5,
            "active": seeded_active + 4,
            "system": seeded_system,
            "solo": 2,
            "city": seeded_city + 1,
            "address": seeded_address + 1,
            "address_null": seeded_address_null + 4,  # salon-off, бутстрап, 2 соло
            "synced": seeded_synced + 1,
        },
        "solo_pending": solo_pending,
    }


# --------------------------------------------------------------------------- #
# Положительная стража: на непустой базе команда печатает не нули
# --------------------------------------------------------------------------- #


def test_tenants_are_counted_from_all_rows_not_the_active_manager(surface):
    report = _run()
    block = _block(report, "тенанты бота", "пользователи бота")
    e = surface["tenants"]
    assert e["total"] > e["active"] > e["solo"] > 0 < e["city"]
    assert (
        _row("всего", e["total"], "tenancy.Tenant (все строки, включая is_active=false)") in block
    )
    assert _row("активных", e["active"], "tenancy.Tenant.is_active = true") in block
    assert _row("системных", e["system"], "tenancy.Tenant.is_system = true") in block
    assert _row("соло (slug solo-*)", 2, "tenancy.Tenant.slug LIKE 'solo-%'") in block
    assert _row("с городом", e["city"], "tenancy.Tenant.city <> ''") in block
    assert _row("с адресом", e["address"], "tenancy.Tenant.address IS NOT NULL AND <> ''") in block
    # NULL и "" — разные новости у этой модели, и команда их не сливает.
    assert _row("  адрес не известен", e["address_null"], "tenancy.Tenant.address IS NULL") in block
    assert _row("  адреса нет", 0, "tenancy.Tenant.address = ''") in block
    assert (
        _row(
            "синхронизировались", e["synced"], "tenancy.Tenant.last_catalog_sync_ok_at IS NOT NULL"
        )
        in block
    )


def test_bot_users_and_distinct_tenants(surface):
    report = _run()
    block = _block(report, "пользователи бота", "карточки мастеров")
    assert _row("всего", 3, "identity.BotUser (все арендаторы)") in block
    assert _row("тенантов с людьми", 3, "identity.BotUser.tenant_id (distinct)") in block


def test_master_cards_linked_and_by_invite_status(surface):
    report = _run()
    block = _block(report, "карточки мастеров", "соло-мастера: setup_state")
    assert _row("всего", 4, "catalog.CatalogMaster (все арендаторы)") in block
    assert (
        _row("связаны с BotUser", 2, "catalog.CatalogMaster.linked_bot_user IS NOT NULL") in block
    )
    assert _row("с ayla_user_id", 2, "catalog.CatalogMaster.ayla_user_id IS NOT NULL") in block
    assert _row("в архиве", 1, "catalog.CatalogMaster.archived_at IS NOT NULL") in block
    for status, _label in CatalogMaster.InviteStatus.choices:
        assert f"  {status:<24}:" in block, status


def test_solo_setup_state_uses_the_single_sale_block_definition(surface):
    """Готовность считается тем же `sale_block`, что и у витрины (DRF-1506)."""
    report = _run()
    block = _block(report, "соло-мастера: setup_state")
    assert (
        _row(
            "карточек в соло-тенантах",
            2,
            "catalog.CatalogMaster JOIN tenancy.Tenant.slug LIKE 'solo-%'",
        )
        in block
    )
    assert _row("ready", 1, "sale_block(row) IS NULL (apps/catalog/master_state.py)") in block
    assert _row("setup_pending", 1, "sale_block(row) IS NOT NULL") in block
    reason = surface["solo_pending"].blocked_by
    assert reason is not None
    assert _row(f"  {reason}", 1, f"sale_block(row) = {reason!r}") in block
    # Все причины SaleBlock напечатаны, включая нулевые.
    for name in (
        "pending",
        "revoked",
        "ayla_unlinked",
        "profile_incomplete",
        "schedule_unconfirmed",
    ):
        assert f"  {name:<24}:" in block, name


# --------------------------------------------------------------------------- #
# Рубильники (§138)
# --------------------------------------------------------------------------- #


def test_every_listed_flag_exists_in_settings():
    """Список флагов команды — не пересказ: каждый обязан быть в settings."""
    from django.conf import settings

    missing = [name for name in FLAGS if not hasattr(settings, name)]
    assert missing == [], missing


def test_flags_print_live_value_and_env_origin(settings, monkeypatch):
    settings.FOOD_PHOTO_SCAN_ENABLED = False
    monkeypatch.delenv("FOOD_PHOTO_SCAN_ENABLED", raising=False)
    settings.BOOKING_VIA_AYLA_REST = True
    monkeypatch.setenv("BOOKING_VIA_AYLA_REST", "true")

    report = _run()
    block = _block(report, "== РУБИЛЬНИКИ", "== СОСТОЯНИЕ ПОВЕРХНОСТИ ==")
    assert (
        _row(
            "FOOD_PHOTO_SCAN_ENABLED",
            "ЗАПЕРТО",
            "settings.FOOD_PHOTO_SCAN_ENABLED = False; env не задан → умолчание кода",
            label_w=_FLAG_W,
        )
        in block
    )
    assert (
        _row(
            "BOOKING_VIA_AYLA_REST",
            "открыт",
            "settings.BOOKING_VIA_AYLA_REST = True; env BOOKING_VIA_AYLA_REST='true'",
            label_w=_FLAG_W,
        )
        in block
    )


def test_a_foreign_flag_is_named_not_faked():
    """`GOAL_RESOLUTION_ENABLED` живёт в каталоге: бот не печатает ему
    значения и не молчит — говорит, где его снимать."""
    report = _run()
    block = _block(report, "== РУБИЛЬНИКИ", "== СОСТОЯНИЕ ПОВЕРХНОСТИ ==")
    line = [ln for ln in block.splitlines() if "GOAL_RESOLUTION_ENABLED" in ln]
    assert len(line) == 1, line
    assert ": не setting бота" in line[0]
    assert "КАТАЛОГА" in line[0]
    assert "ЗАПЕРТО" not in line[0] and "открыт" not in line[0]


# --------------------------------------------------------------------------- #
# Шапка предмета, предел, порядок
# --------------------------------------------------------------------------- #


def test_subject_limit_flags_and_numbers_come_in_that_order(surface):
    report = _run()
    subject = report.index("== ПРЕДМЕТ: кто отвечает на этот замер ==")
    limit = report.index("== ПРЕДЕЛ: что эта команда НЕ показывает ==")
    flags = report.index("== РУБИЛЬНИКИ")
    numbers = report.index("== СОСТОЯНИЕ ПОВЕРХНОСТИ ==")
    assert subject < limit < flags < numbers
    assert "СТАРТ ПРОЦЕССА БД" in report
    assert "время снятия" in report
    assert "изнутри НЕ видно имени контейнера и метки compose" in report
    assert "docker inspect" in report
    assert "ДАННЫЕ и ЗНАЧЕНИЯ РУБИЛЬНИКОВ, а не ПОВЕДЕНИЕ" in report
    assert "sale_block(row) IS NULL" in report


def test_every_number_carries_its_table_and_field(surface):
    """Подпись читатель проверить не может, `таблица.поле` — может."""
    report = _run()
    body = report.split("== СОСТОЯНИЕ ПОВЕРХНОСТИ ==", 1)[1]
    rows = [
        ln for ln in body.splitlines() if ln.startswith("  ") and ":" in ln and "число" not in ln
    ]
    assert len(rows) >= 20, len(rows)
    for row in rows:
        source = row.split(":", 1)[1].split(None, 1)[1]
        assert "." in source or "sale_block(" in source, row


def test_pulse_is_live_on_a_populated_base(surface):
    report = _run()
    assert "ПУЛЬС (новейшая запись)" in report
    assert "НИ ОДНА опора не ответила" not in report
    for anchor in PULSE_ANCHORS:
        assert f"    {anchor.label:<26}:" in report, anchor.label


# --------------------------------------------------------------------------- #
# Пульс: молчание всех — «не подтверждено»; опечатка — исключение
# --------------------------------------------------------------------------- #


def test_silence_of_all_anchors_is_not_confirmed_not_green():
    report = _run()
    assert "НИ ОДНА опора не ответила" in report
    assert "свежесть НЕ подтверждена" in report
    assert _row("всего", 0, "catalog.CatalogMaster (все арендаторы)") in report


def test_anchor_set_resolves_to_real_models_and_fields():
    pulses = gather_pulse()
    assert [p.label for p in pulses] == [a.label for a in PULSE_ANCHORS]
    assert all(p.error is None for p in pulses), [p.error for p in pulses]


def test_a_typo_in_an_anchor_raises_instead_of_going_silent():
    with pytest.raises(LookupError):
        gather_pulse(anchors=(Anchor("опечатка модели", "conversations.Mesage", "created_at"),))
    with pytest.raises(FieldDoesNotExist):
        gather_pulse(anchors=(Anchor("опечатка поля", "conversations.Message", "creatd_at"),))


def test_the_boot_event_of_the_measuring_process_is_not_a_pulse():
    """`worker.subscriber_audit` пишется при КАЖДОМ старте процесса — и
    процесса `surface_state` тоже. Опора, которую наполняет сам замер,
    сделала бы любую машину «живой»; здесь она вычитается."""
    from apps.events.models import Event

    Event._base_manager.create(
        event_type="worker.subscriber_audit", event_name="worker.subscriber_audit"
    )
    Event._base_manager.create(event_type="dialog.turn", event_name="dialog.turn")
    pulses = {p.label: p for p in gather_pulse()}
    assert pulses["события"].at is not None  # настоящее событие видно
    Event._base_manager.filter(event_name="dialog.turn").delete()
    pulses = {p.label: p for p in gather_pulse()}
    assert pulses["события"].at is None  # один boot-след пульсом не считается


def test_pulse_reads_through_the_tenant_scope(surface):
    """`Message.objects` — TenantScopedManager: вне контекста арендатора он
    отдаёт пусто, и пульс по нему замерил бы видимость, а не запись."""
    assert Message.objects.count() == 0 < Message.all_tenants.count()
    pulses = {p.label: p for p in gather_pulse()}
    assert pulses["сообщения диалога"].at is not None


# --------------------------------------------------------------------------- #
# --write
# --------------------------------------------------------------------------- #


def test_write_replaces_the_file_with_the_report_and_a_warning_header(surface, tmp_path):
    target = tmp_path / "docs" / "SURFACE_STATE.md"
    target.parent.mkdir()
    target.write_text("правленный руками текст\n", encoding="utf-8")

    report = _run(write=str(target))
    written = target.read_text(encoding="utf-8")

    assert written.startswith(FILE_HEADER)
    assert "правка руками превращает замер в мнение" in written
    assert "правленный руками текст" not in written
    assert "== ПРЕДМЕТ: кто отвечает на этот замер ==" in written
    assert (
        _row("связаны с BotUser", 2, "catalog.CatalogMaster.linked_bot_user IS NOT NULL") in written
    )
    assert f"записано: {target}" in report
