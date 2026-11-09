"""Удаление аккаунта соло-мастера стирает личное с его карточки и названия кабинета.

До правки: соло-мастер — владелец своего кабинета, роль владельца каскад не
снимает, связь с карточкой остаётся — и карточка пропускалась целиком. Аккаунт
удалён, каталог свою половину стёр (профиль «Удалён», кабинет «Студия»), а в
зеркале бота лежат имя, фото, описание и «Студия {имя}».

Решение владельца 09.11.2026: карточка соло — не «запись салона о сотруднике»;
имя, фото и описание стираются, карточка уходит в архив.

Узлы:

* s1 — свой соло-кабинет: карточка обезличена теми же значениями, что у
  каталога, и в архиве; кабинет — «Студия», город пуст; шаг это называет;
* s2 — пара: владелец обычного салона — карточка и имя остаются, как прежде;
* s3 — в соло-кабинете есть живая карточка другого человека → не стирается, ``solo_shared``;
* s4 — в соло-кабинете есть чужая действующая роль → не стирается, ``solo_shared``;
* s5 — «другим человеком» не считаются: ушедший сотрудник, неотвеченное приглашение
  (карточка без привязки) и вторая оболочка того же человека;
* s12 — связь с карточкой снята раньше (строка владельца неактивна) — стирается всё равно;
* s13 — сбой на втором из двух сохранений откатывает первое;
* s6 — повтор ничего не меняет, дата архива не сдвигается;
* s7 — кабинет со слагом чужой личности своим не считается: обычное правило салона;
* s8 — кабинет, заведённый настоящей саморегистрацией, распознаётся и стирается;
* s9 — сбой стирания называется в ответе: каталог не получит ``all_ok``;
* s10 — что названо пределом: роль владельца и связь остаются;
* s11 — в журнале нет имени человека.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import pytest
from django.utils import timezone

from apps.catalog.models import CatalogMaster
from apps.identity.models import BotUser
from apps.identity.services import account_deletion
from apps.identity.services.account_deletion import (
    ERASED_CARD_NAME,
    ERASED_SOLO_TENANT_NAME,
    SOLO_CARD_ARCHIVE_REASON,
    STAFF_ACCESS_STEP,
    execute_bot_half,
)
from apps.identity.services.solo_onboarding import _solo_tenant_slug, create_solo_provider
from apps.integrations.ayla.user_proxy import external_user_id_for
from apps.tenancy.models import Tenant, TenantStaff

pytestmark = pytest.mark.django_db

PERSON_NAME = "Ольга Петрова"
PHOTO = "https://catalog.example/avatars/olga.jpg"


@dataclass
class Solo:
    tenant: Tenant
    shell: BotUser
    card: CatalogMaster
    ayla_user_id: uuid.UUID


def _solo(label: str, *, slug: str | None = None) -> Solo:
    """Соло-кабинет в том виде, в каком он живёт после настройки профиля."""
    ayla_user_id = uuid.uuid4()
    channel_user_id = f"solo-{label}-{uuid.uuid4().hex[:8]}"
    tenant = Tenant.objects.create(
        slug=slug or _solo_tenant_slug("max", channel_user_id),
        name="Студия Ольга",
        city="Пенза",
        address="улица Московская, 1",
    )
    shell = BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id=channel_user_id,
        ayla_user_id=ayla_user_id,
        display_name=PERSON_NAME,
    )
    TenantStaff.all_tenants.create(tenant=tenant, bot_user=shell, role=TenantStaff.Role.OWNER)
    TenantStaff.all_tenants.create(tenant=tenant, bot_user=shell, role=TenantStaff.Role.ADMIN)
    card = CatalogMaster.all_tenants.create(
        tenant=tenant,
        name=PERSON_NAME,
        bio="Делаю массаж десять лет.",
        experience="10 лет",
        photo_url=PHOTO,
        address="Пенза, улица Московская, 1",
        location_lat=Decimal("53.195000"),
        location_lng=Decimal("45.018000"),
        max_handle="olga_handle",
        external_id=-(uuid.uuid4().int % 10**7) - 1,
        external_updated_at=timezone.now(),
        linked_bot_user=shell,
        ayla_user_id=ayla_user_id,
        catalog_specialist_id=uuid.uuid4(),
        raw={"invite_phone": "probe-phone", "display_name": PERSON_NAME},
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
    )
    return Solo(tenant=tenant, shell=shell, card=card, ayla_user_id=ayla_user_id)


def _other_person(tenant: Tenant, *, role: str | None, card: bool, live: bool = True) -> None:
    """Второй человек в кабинете: карточкой, ролью или обоими; ``live=False`` — уже ушедший."""
    shell = BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id=f"other-{uuid.uuid4().hex[:8]}",
        display_name="Анна Другая",
    )
    if role is not None:
        TenantStaff.all_tenants.create(
            tenant=tenant,
            bot_user=shell,
            role=role,
            deactivated_at=None if live else timezone.now(),
        )
    if card:
        CatalogMaster.all_tenants.create(
            tenant=tenant,
            name="Анна Другая",
            external_id=uuid.uuid4().int % 10**7,
            external_updated_at=timezone.now(),
            linked_bot_user=shell,
            invite_status=CatalogMaster.InviteStatus.ACCEPTED,
            archived_at=None if live else timezone.now(),
        )


def _unanswered_invite(tenant: Tenant) -> None:
    """Карточка-приглашение, которое никто не принял: строка есть, человека за ней нет."""
    CatalogMaster.all_tenants.create(
        tenant=tenant,
        name="Приглашённая",
        external_id=uuid.uuid4().int % 10**7,
        external_updated_at=timezone.now(),
        invite_status=CatalogMaster.InviteStatus.EXPIRED,
    )


def _second_shell(person: "Solo") -> BotUser:
    """Вторая оболочка того же человека (тот же ``ayla_user_id``) с ролью в его кабинете."""
    shell = BotUser.all_tenants.create(
        tenant=person.tenant,
        channel="telegram",
        channel_user_id=f"second-{uuid.uuid4().hex[:8]}",
        ayla_user_id=person.ayla_user_id,
        display_name=PERSON_NAME,
    )
    TenantStaff.all_tenants.create(
        tenant=person.tenant, bot_user=shell, role=TenantStaff.Role.RECEPTIONIST
    )
    return shell


@pytest.fixture
def quiet_cascade(settings: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Остальной каскад — не предмет: он подменён успешным, шаг доступа настоящий."""
    settings.STRICT_TENANT_SCOPE = "off"

    class _Done:
        steps: list = []
        failed_steps: list = []

    monkeypatch.setattr(account_deletion, "delete_personal_data", lambda *a, **kw: _Done())
    monkeypatch.setattr(account_deletion, "clear_deletion_flag", lambda ayla_user_id: True)


def _delete(person: Solo) -> account_deletion.AccountDeletionOutcome:
    return execute_bot_half(
        ayla_user_id=person.ayla_user_id, external_user_ids=[], request_id="solo-card"
    )


def _card(person: Solo) -> CatalogMaster:
    return CatalogMaster.all_tenants.get(pk=person.card.pk)


def _tenant(person: Solo) -> Tenant:
    return Tenant.all_objects.get(pk=person.tenant.pk)


def _detail(outcome: account_deletion.AccountDeletionOutcome) -> str:
    (step,) = [s for s in outcome.steps if s["step"] == STAFF_ACCESS_STEP]
    assert step["ok"] is True
    return str(step["detail"])


def _personal(card: CatalogMaster) -> dict[str, Any]:
    """Всё личное на карточке — одним словарём, чтобы сравнивать целиком."""
    return {
        "name": card.name,
        "bio": card.bio,
        "experience": card.experience,
        "photo_url": card.photo_url,
        "address": card.address,
        "location_lat": card.location_lat,
        "location_lng": card.location_lng,
        "max_handle": card.max_handle,
        "ayla_user_id": card.ayla_user_id,
        "raw": card.raw,
    }


ERASED: dict[str, Any] = {
    "name": ERASED_CARD_NAME,
    "bio": "",
    "experience": "",
    "photo_url": "",
    "address": None,
    "location_lat": None,
    "location_lng": None,
    "max_handle": "",
    "ayla_user_id": None,
    "raw": {},
}


def test_s1_the_solo_card_and_the_cabinet_name_are_erased(quiet_cascade: None) -> None:
    """Красный до правки: карточка пропускалась целиком, имя и фото оставались."""
    person = _solo("s1")
    before = timezone.now()

    outcome = _delete(person)

    assert outcome.all_ok is True
    assert _detail(outcome) == "roles=0 cards=0 owner_role_kept=1 solo_cards_erased=1"
    card = _card(person)
    assert _personal(card) == ERASED
    assert card.is_active is False
    assert card.archived_at is not None and card.archived_at >= before
    assert card.archive_reason == SOLO_CARD_ARCHIVE_REASON
    tenant = _tenant(person)
    assert tenant.name == ERASED_SOLO_TENANT_NAME
    assert (tenant.city, tenant.address) == ("", None)


def test_s2_a_salon_owner_card_keeps_its_name(quiet_cascade: None) -> None:
    """Пара к s1: тот же человек и та же карточка, но салон — не свой соло-кабинет."""
    person = _solo("s2", slug=f"salon-{uuid.uuid4().hex[:8]}")
    kept = _personal(person.card)

    outcome = _delete(person)

    assert _detail(outcome) == "roles=0 cards=0 owner_role_kept=1"
    card = _card(person)
    assert _personal(card) == kept
    assert card.archived_at is None
    assert _tenant(person).name == "Студия Ольга"


@pytest.mark.parametrize(
    ("role", "card"),
    [(None, True), (TenantStaff.Role.ADMIN, False), (TenantStaff.Role.ADMIN, True)],
    ids=["s3-another-live-card", "s4-another-active-role", "both"],
)
def test_a_cabinet_shared_with_someone_is_not_erased(
    quiet_cascade: None, role: str | None, card: bool
) -> None:
    person = _solo("shared")
    kept = _personal(person.card)
    _other_person(person.tenant, role=role, card=card)

    outcome = _delete(person)

    assert _detail(outcome) == "roles=0 cards=0 owner_role_kept=1 solo_shared=1"
    assert _personal(_card(person)) == kept
    assert _card(person).archived_at is None
    assert _tenant(person).name == "Студия Ольга"


def test_s5_people_who_already_left_do_not_make_it_shared(quiet_cascade: None) -> None:
    person = _solo("s5")
    _other_person(person.tenant, role=TenantStaff.Role.ADMIN, card=True, live=False)

    outcome = _delete(person)

    assert _detail(outcome) == "roles=0 cards=0 owner_role_kept=1 solo_cards_erased=1"
    assert _personal(_card(person)) == ERASED


def test_s5_an_unanswered_invite_is_not_a_person(quiet_cascade: None) -> None:
    """Каталог к этому мигу кабинет уже обезличил; истёкшее приглашение не причина оставить имя."""
    person = _solo("s5i")
    _unanswered_invite(person.tenant)

    outcome = _delete(person)

    assert _detail(outcome) == "roles=0 cards=0 owner_role_kept=1 solo_cards_erased=1"
    assert _personal(_card(person)) == ERASED
    assert _tenant(person).name == ERASED_SOLO_TENANT_NAME


def test_s5_the_persons_own_second_shell_is_not_someone_else(quiet_cascade: None) -> None:
    person = _solo("s5s")
    _second_shell(person)

    outcome = _delete(person)

    assert _detail(outcome).endswith("owner_role_kept=1 solo_cards_erased=1")
    assert _personal(_card(person)) == ERASED


def test_s12_a_card_unlinked_earlier_is_still_erased(quiet_cascade: None) -> None:
    """Строка владельца неактивна → отзыв снимает связь; карточка всё равно своя."""
    person = _solo("s12")
    TenantStaff.all_tenants.filter(bot_user=person.shell, role=TenantStaff.Role.OWNER).update(
        deactivated_at=timezone.now()
    )

    outcome = _delete(person)

    assert _detail(outcome) == "roles=1 cards=0 solo_cards_erased=1"
    card = _card(person)
    assert card.linked_bot_user_id is None
    assert _personal(card) == ERASED
    assert card.archived_at is not None

    again = _delete(person)

    assert _detail(again) == "roles=0 cards=0"
    assert _personal(_card(person)) == ERASED


def test_s13_a_failure_on_the_cabinet_rolls_the_card_back(
    quiet_cascade: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Два сохранения — одна транзакция: карточка без кабинета не обезличивается."""
    person = _solo("s13")
    kept = _personal(person.card)
    real_save = Tenant.save

    def refuse_rename(self: Tenant, *args: Any, **kwargs: Any) -> None:
        if self.name == ERASED_SOLO_TENANT_NAME:
            raise RuntimeError("database down")
        real_save(self, *args, **kwargs)

    monkeypatch.setattr(Tenant, "save", refuse_rename)

    outcome = _delete(person)

    assert outcome.all_ok is False
    assert outcome.failed_steps == [STAFF_ACCESS_STEP]
    card = _card(person)
    assert _personal(card) == kept
    assert card.archived_at is None
    assert _tenant(person).name == "Студия Ольга"


def test_s6_a_second_run_changes_nothing(quiet_cascade: None) -> None:
    person = _solo("s6")
    assert _detail(_delete(person)).endswith("solo_cards_erased=1")
    archived_at = _card(person).archived_at
    assert archived_at is not None

    again = _delete(person)

    assert again.all_ok is True
    assert _detail(again) == "roles=0 cards=0 owner_role_kept=1"
    assert _card(person).archived_at == archived_at
    assert _personal(_card(person)) == ERASED


def test_s7_a_cabinet_named_after_another_identity_is_not_ones_own(quiet_cascade: None) -> None:
    """Слаг выведен из чужой личности — это не кабинет этого человека, сколько бы «solo-» в нём ни было."""
    person = _solo("s7", slug=_solo_tenant_slug("max", "somebody-else"))
    kept = _personal(person.card)

    outcome = _delete(person)

    assert _detail(outcome) == "roles=0 cards=0 owner_role_kept=1"
    assert _personal(_card(person)) == kept


def test_s8_a_cabinet_made_by_real_self_registration_is_erased(quiet_cascade: None) -> None:
    """Правило «свой кабинет» привязано к настоящему создателю, а не к посеву теста."""
    made = create_solo_provider(
        channel="max", channel_user_id=f"s8-{uuid.uuid4().hex[:8]}", display_name=PERSON_NAME
    )
    ayla_user_id = uuid.uuid4()

    outcome = execute_bot_half(
        ayla_user_id=ayla_user_id,
        external_user_ids=[external_user_id_for(made.bot_user)],
        request_id="solo-card-s8",
    )

    assert outcome.all_ok is True
    assert _detail(outcome) == "roles=0 cards=0 owner_role_kept=1 solo_cards_erased=1"
    card = CatalogMaster.all_tenants.get(pk=made.master.pk)
    assert card.name == ERASED_CARD_NAME
    assert card.archived_at is not None
    assert Tenant.all_objects.get(pk=made.tenant.pk).name == ERASED_SOLO_TENANT_NAME


def test_s9_a_failed_erasure_is_named_and_the_catalog_gets_no_all_ok(
    quiet_cascade: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    person = _solo("s9")
    kept = _personal(person.card)

    def down(card: Any) -> int:
        raise RuntimeError("database down")

    monkeypatch.setattr(account_deletion, "_erase_solo_card", down)

    outcome = _delete(person)

    assert outcome.all_ok is False
    assert outcome.failed_steps == [STAFF_ACCESS_STEP]
    assert _personal(_card(person)) == kept


def test_s10_the_named_limits_hold(quiet_cascade: None) -> None:
    """Роль владельца и связь с карточкой остаются: закрытие кабинета — отдельная задача."""
    person = _solo("s10")

    _delete(person)

    roles = sorted(
        TenantStaff.all_tenants.filter(
            bot_user=person.shell, deactivated_at__isnull=True
        ).values_list("role", flat=True)
    )
    assert roles == ["admin", "owner"]
    card = _card(person)
    assert card.linked_bot_user_id == person.shell.id
    assert card.catalog_specialist_id == person.card.catalog_specialist_id


def test_s11_the_log_line_carries_no_name(
    quiet_cascade: None, caplog: pytest.LogCaptureFixture
) -> None:
    person = _solo("s11")

    with caplog.at_level("INFO"):
        _delete(person)

    lines = [r.getMessage() for r in caplog.records if "solo_card_erased" in r.getMessage()]
    assert len(lines) == 1
    assert f"card={person.card.pk}" in lines[0]
    leaked = [word for word in ("Ольга", "Петрова", "olga") if word in lines[0]]
    assert leaked == []  # empty-assert-ok: строка найдена выше и несёт id карточки
