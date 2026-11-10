"""Удаление аккаунта закрывает свой соло-кабинет; вернувшемуся заводят новый.

До правки (живой прогон 09.11.2026): карточка соло стёрта и в архиве, а
кабинет жил дальше — тенант активен, роль владельца на месте. Вернувшийся
человек был «владельцем» пустого кабинета: дверь регистрации ему не
открывалась, а ``create_solo_provider`` на его личность упирался в тот же
слаг и падал с ``PartialStateError``.

Решение владельца 10.11.2026: роль владельца у своего соло-кабинета снимать,
кабинет закрывать (``Tenant.is_active=False``), при возврате заводить НОВЫЙ
кабинет под следующим суффиксом слага (вариант «а»): закрытый остаётся
надгробием для аудита, новый чист с нуля.

Узлы:

* c1 — свой соло-кабинет: роли (владельца тоже) сняты, связь с карточкой снята,
  тенант закрыт; шаг это называет;
* c2 — кабинет, где есть второй человек, не закрывается — прежнее правило, роль
  владельца остаётся;
* c3 — владелец обычного салона: роль остаётся (``owner_role_kept``), салон открыт;
* c4 — повтор ничего не меняет;
* c5 — отзыв без ``allow_owner`` по-прежнему отказывает владельцу;
* c6 — ``is_own_solo_slug``: база, ``-2``, ``-10`` — свои; ``-1``, ``-0``, ``-x``,
  чужая база, ``base-2-3`` — нет;
* c7 — после закрытия саморегистрация заводит ``base-2`` с новыми строками, старый
  кабинет не тронут; после второго закрытия — ``base-3``;
* c8 — открытый кабинет (в т.ч. суффиксный) — идемпотентный ответ, не новый тенант;
* c9 — два открытых кабинета одной личности — ``PartialStateError``, не третий;
* c10 — дверь салон-бота: удалённому владельцу предлагают кабинет, а не «уже есть»;
  регистрация даёт ``base-2`` и текст «кабинет заведён»;
* c11 — в журнале шага нет имени человека;
* c12 — сбой внутри транзакции шага после отзыва ролей: повтор находит кабинет и
  карточку (по оболочкам и ключу саморегистрации, не по снятым ролям и связям),
  закрывает и стирает; до правки повтор отчитывался «готово» с именем на карточке;
* c13 — отклонённая оператором личность (§6): «удалить и вернуться» дверь не
  открывает — тот же текст с путём в поддержку;
* c14 — закрытие пишет строку аудита в тенанте кабинета;
* c15 — следующий суффикс длиннее 50 знаков — отказ по имени, не ошибка базы.
"""

from __future__ import annotations

import uuid
from typing import Any, cast

import pytest

from apps.catalog.models import CatalogMaster
from apps.channels.max import salon_handler
from apps.channels.max.parser import CanonicalEvent
from apps.audit.models import AuditLog
from apps.events.vocabulary import SOLO_CABINET_CLOSED
from apps.identity.models import BotUser, SoloIdentityLink
from apps.identity.services import account_deletion
from apps.identity.services.account_deletion import STAFF_ACCESS_STEP, execute_bot_half
from apps.identity.services.solo_onboarding import (
    SoloOnboardingError,
    SoloOnboardingPartialStateError,
    _next_solo_slug,
    _solo_tenant_slug,
    active_solo_tenant,
    create_solo_provider,
    is_own_solo_slug,
    solo_tenant_family,
)
from apps.identity.services.staff_revoke import OwnerRevokeRefused, revoke_staff_access
from apps.integrations.ayla.user_proxy import external_user_id_for
from apps.tenancy.models import Tenant, TenantStaff

pytestmark = pytest.mark.django_db

PERSON_NAME = "Ольга Петрова"


@pytest.fixture
def quiet_cascade(settings: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Остальной каскад — не предмет: подменён успешным, шаг доступа настоящий."""
    settings.STRICT_TENANT_SCOPE = "off"

    class _Done:
        steps: list = []
        failed_steps: list = []

    monkeypatch.setattr(account_deletion, "delete_personal_data", lambda *a, **kw: _Done())
    monkeypatch.setattr(account_deletion, "clear_deletion_flag", lambda ayla_user_id: True)


def _identity(label: str) -> tuple[str, str]:
    return "max", f"{label}-{uuid.uuid4().hex[:8]}"


def _register(channel: str, channel_user_id: str):
    return create_solo_provider(
        channel=channel, channel_user_id=channel_user_id, display_name=PERSON_NAME
    )


def _delete(bot_user: BotUser) -> account_deletion.AccountDeletionOutcome:
    return execute_bot_half(
        ayla_user_id=uuid.uuid4(),
        external_user_ids=[external_user_id_for(bot_user)],
        request_id="cabinet-closure",
    )


def _detail(outcome: account_deletion.AccountDeletionOutcome) -> str:
    (step,) = [s for s in outcome.steps if s["step"] == STAFF_ACCESS_STEP]
    assert step["ok"] is True
    return str(step["detail"])


def _active_roles(bot_user: BotUser) -> list[str]:
    return sorted(
        TenantStaff.all_tenants.filter(bot_user=bot_user, deactivated_at__isnull=True).values_list(
            "role", flat=True
        )
    )


def _tenant(pk: Any) -> Tenant:
    return Tenant.all_objects.get(pk=pk)


# ─── закрытие ────────────────────────────────────────────────────────────────


def test_c1_the_own_solo_cabinet_is_closed_with_the_account(quiet_cascade: None) -> None:
    """Красный до правки: роли оставались, тенант был активен."""
    made = _register(*_identity("c1"))

    outcome = _delete(made.bot_user)

    assert _detail(outcome) == "roles=2 cards=0 solo_cards_erased=1 solo_cabinets_closed=1"
    # ПРИСУТСТВИЕ впереди: строки ролей остались как история — и все сняты.
    rows = TenantStaff.all_tenants.filter(bot_user=made.bot_user)
    assert sorted(rows.values_list("role", flat=True)) == ["admin", "owner"]
    assert rows.filter(deactivated_at__isnull=True).count() == 0
    tenant = _tenant(made.tenant.pk)
    assert tenant.is_active is False
    card = CatalogMaster.all_tenants.get(pk=made.master.pk)
    assert card.linked_bot_user_id is None
    assert card.archived_at is not None
    # Закрытый кабинет — не кабинет: ни для двери, ни для саморегистрации.
    assert active_solo_tenant(made.bot_user.channel, made.bot_user.channel_user_id) is None


def test_c2_a_cabinet_shared_with_someone_stays_open(quiet_cascade: None) -> None:
    made = _register(*_identity("c2"))
    other = BotUser.all_tenants.create(
        tenant=made.tenant, channel="max", channel_user_id="c2-other", display_name="Анна"
    )
    TenantStaff.all_tenants.create(tenant=made.tenant, bot_user=other, role=TenantStaff.Role.ADMIN)

    outcome = _delete(made.bot_user)

    assert _detail(outcome) == "roles=0 cards=0 owner_role_kept=1 solo_shared=1"
    assert _active_roles(made.bot_user) == ["admin", "owner"]
    assert _tenant(made.tenant.pk).is_active is True


def test_c3_a_salon_owner_keeps_the_salon(quiet_cascade: None) -> None:
    salon = Tenant.objects.create(slug=f"salon-{uuid.uuid4().hex[:8]}", name="Салон")
    owner = BotUser.all_tenants.create(
        tenant=salon, channel="max", channel_user_id="c3-owner", display_name=PERSON_NAME
    )
    TenantStaff.all_tenants.create(tenant=salon, bot_user=owner, role=TenantStaff.Role.OWNER)

    outcome = _delete(owner)

    assert _detail(outcome) == "roles=0 cards=0 owner_role_kept=1"
    assert _active_roles(owner) == ["owner"]
    assert _tenant(salon.pk).is_active is True


def test_c4_a_second_run_changes_nothing(quiet_cascade: None) -> None:
    made = _register(*_identity("c4"))
    _delete(made.bot_user)
    before = _tenant(made.tenant.pk).updated_at

    again = _delete(made.bot_user)

    assert again.all_ok is True
    assert _detail(again) == "roles=0 cards=0"
    assert _tenant(made.tenant.pk).is_active is False
    assert _tenant(made.tenant.pk).updated_at == before


def test_c5_revoke_still_refuses_the_owner_unless_told_otherwise() -> None:
    made = _register(*_identity("c5"))

    with pytest.raises(OwnerRevokeRefused):
        revoke_staff_access(tenant=made.tenant, bot_user=made.bot_user, reason="probe")
    assert _active_roles(made.bot_user) == ["admin", "owner"]

    result = revoke_staff_access(
        tenant=made.tenant, bot_user=made.bot_user, reason="probe", allow_owner=True
    )

    assert result.roles_revoked == ("admin", "owner")
    assert result.master_unlinked is True
    assert _active_roles(made.bot_user) == []


# ─── слаги ───────────────────────────────────────────────────────────────────


def test_c6_which_slugs_are_ones_own() -> None:
    channel, channel_user_id = _identity("c6")
    base = _solo_tenant_slug(channel, channel_user_id)
    stranger = _solo_tenant_slug(channel, "somebody-else")

    mine = [base, f"{base}-2", f"{base}-10"]
    not_mine = [
        f"{base}-1",
        f"{base}-0",
        f"{base}-02",
        f"{base}-²",
        f"{base}-x",
        f"{base}-",
        f"{base}-2-3",
        stranger,
    ]

    assert [is_own_solo_slug(s, channel, channel_user_id) for s in mine] == [True] * 3
    assert [is_own_solo_slug(s, channel, channel_user_id) for s in not_mine] == [False] * 8


# ─── возврат ─────────────────────────────────────────────────────────────────


def test_c7_a_returning_person_gets_a_fresh_cabinet_under_the_next_suffix(
    quiet_cascade: None,
) -> None:
    """Красный до правки: ``PartialStateError`` на том же слаге."""
    channel, channel_user_id = _identity("c7")
    first = _register(channel, channel_user_id)
    _delete(first.bot_user)

    second = _register(channel, channel_user_id)

    assert second.created is True
    assert second.tenant.slug == f"{first.tenant.slug}-2"
    assert second.tenant.pk != first.tenant.pk
    assert second.bot_user.pk != first.bot_user.pk
    assert second.master.pk != first.master.pk
    assert second.tenant.name == "Студия Ольга"
    assert _active_roles(second.bot_user) == ["admin", "owner"]
    # Надгробие не тронуто.
    assert _tenant(first.tenant.pk).is_active is False
    assert _tenant(first.tenant.pk).name == "Студия"
    open_now = active_solo_tenant(channel, channel_user_id)
    assert open_now is not None and open_now.pk == second.tenant.pk

    _delete(second.bot_user)
    third = _register(channel, channel_user_id)

    assert third.tenant.slug == f"{first.tenant.slug}-3"
    assert [t.slug for t in solo_tenant_family(channel, channel_user_id)] == [
        first.tenant.slug,
        second.tenant.slug,
        third.tenant.slug,
    ]


def test_c8_an_open_cabinet_is_answered_idempotently_even_with_a_suffix(
    quiet_cascade: None,
) -> None:
    channel, channel_user_id = _identity("c8")
    _delete(_register(channel, channel_user_id).bot_user)
    second = _register(channel, channel_user_id)
    assert second.tenant.slug.endswith("-2")

    again = _register(channel, channel_user_id)

    assert again.created is False
    assert again.tenant.pk == second.tenant.pk
    assert (
        Tenant.all_objects.filter(
            slug__startswith=_solo_tenant_slug(channel, channel_user_id)
        ).count()
        == 2
    )


def test_c9_two_open_cabinets_are_a_partial_state_not_a_third() -> None:
    channel, channel_user_id = _identity("c9")
    first = _register(channel, channel_user_id)
    Tenant.objects.create(slug=f"{first.tenant.slug}-2", name="Второй открытый")

    with pytest.raises(SoloOnboardingPartialStateError):
        _register(channel, channel_user_id)

    assert Tenant.all_objects.filter(slug__startswith=first.tenant.slug).count() == 2


# ─── дверь салон-бота ────────────────────────────────────────────────────────


class _Event:
    """Ровно те поля, что читает дверь (как в ``test_salon_solo_door.py``)."""

    def __init__(self, text: str, channel_user_id: str) -> None:
        self.text = text
        self.chat_id = "555"
        self.channel = "max"
        self.channel_user_id = channel_user_id
        self.raw = {"message": {"sender": {"name": PERSON_NAME}}}


def _event(text: str, channel_user_id: str) -> CanonicalEvent:
    return cast(CanonicalEvent, _Event(text, channel_user_id))


@pytest.fixture
def said(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    out: list[dict] = []
    monkeypatch.setattr(
        salon_handler,
        "_reply",
        lambda event, text, attachments=None: out.append(
            {"text": text, "attachments": attachments}
        ),
    )
    monkeypatch.setattr(salon_handler, "_client_bot_link_button", lambda: None)
    from django.core.cache import cache

    cache.clear()
    return out


def test_c10_the_door_offers_a_deleted_owner_a_new_cabinet(
    quiet_cascade: None, said: list[dict]
) -> None:
    channel, channel_user_id = _identity("c10")
    salon_handler._register_solo_provider(
        _event(salon_handler.SOLO_REGISTER_CALLBACK, channel_user_id), entry=None
    )
    first = active_solo_tenant(channel, channel_user_id)
    assert first is not None
    shell = BotUser.all_tenants.get(tenant=first, channel=channel, channel_user_id=channel_user_id)
    _delete(shell)
    said.clear()

    salon_handler._ask_for_code_with_solo_offer(_event("привет", channel_user_id), entry=None)

    assert len(said) == 1
    assert salon_handler.SOLO_OFFER.strip() in said[0]["text"]
    assert said[0]["text"] != salon_handler.SOLO_ALREADY_REGISTERED

    said.clear()
    salon_handler._register_solo_provider(
        _event(salon_handler.SOLO_REGISTER_CALLBACK, channel_user_id), entry=None
    )

    assert said and said[0]["text"] == salon_handler.SOLO_CREATED_PENDING
    second = active_solo_tenant(channel, channel_user_id)
    assert second is not None
    assert second.slug == f"{first.slug}-2"


def test_c11_the_log_carries_no_name(quiet_cascade: None, caplog: pytest.LogCaptureFixture) -> None:
    made = _register(*_identity("c11"))

    with caplog.at_level("INFO"):
        _delete(made.bot_user)

    closed = [r for r in caplog.records if "solo_cabinet_closed" in r.getMessage()]
    assert closed, "закрытие кабинета должно попасть в журнал"
    assert all(PERSON_NAME not in r.getMessage() for r in caplog.records)
    assert all("Ольга" not in r.getMessage() for r in caplog.records)


def test_c12_a_retry_after_a_failure_still_finds_and_closes_the_cabinet(
    quiet_cascade: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Красный до правки: повтор отчитывался «roles=0 cards=0», кабинет открыт, имя на карточке."""
    made = _register(*_identity("c12"))
    real_erase = account_deletion._erase_solo_card
    blown = {"once": False}

    def erase_once_failing(card: Any) -> int:
        if not blown["once"]:
            blown["once"] = True
            raise RuntimeError("database down")
        return real_erase(card)

    monkeypatch.setattr(account_deletion, "_erase_solo_card", erase_once_failing)

    first = _delete(made.bot_user)

    assert first.all_ok is False
    # Отзыв ролей прошёл своей транзакцией (строки — история, активных нет);
    # стирание и закрытие откатились.
    rows = TenantStaff.all_tenants.filter(bot_user=made.bot_user)
    assert sorted(rows.values_list("role", flat=True)) == ["admin", "owner"]
    assert rows.filter(deactivated_at__isnull=True).count() == 0
    assert _tenant(made.tenant.pk).is_active is True
    assert CatalogMaster.all_tenants.get(pk=made.master.pk).name == PERSON_NAME

    second = _delete(made.bot_user)

    assert second.all_ok is True
    assert _detail(second) == "roles=0 cards=0 solo_cards_erased=1 solo_cabinets_closed=1"
    assert _tenant(made.tenant.pk).is_active is False
    card = CatalogMaster.all_tenants.get(pk=made.master.pk)
    assert card.archived_at is not None
    assert card.name != PERSON_NAME
    # И возврат после этого — новый кабинет, а не ``PartialStateError``.
    channel, channel_user_id = made.bot_user.channel, made.bot_user.channel_user_id
    assert _register(channel, channel_user_id).tenant.slug == f"{made.tenant.slug}-2"


def test_c13_a_rejected_identity_cannot_return_through_deletion(
    quiet_cascade: None, said: list[dict]
) -> None:
    """Красный до правки: после закрытия кабинета отклонённому снова предлагали завести его."""
    from django.contrib.auth import get_user_model

    from apps.identity.services.solo_identity_link import (
        REJECTED_RECOVERY_TEXT,
        reject_by_operator,
    )

    channel, channel_user_id = _identity("c13")
    salon_handler._register_solo_provider(
        _event(salon_handler.SOLO_REGISTER_CALLBACK, channel_user_id), entry=None
    )
    link = SoloIdentityLink.objects.get(channel=channel, channel_user_id=channel_user_id)
    operator = get_user_model().objects.create_user(username="op-c13", password="x")  # noqa: S106
    reject_by_operator(link, operator=operator, reason="identity_unverifiable")
    opened = active_solo_tenant(channel, channel_user_id)
    assert opened is not None
    shell = BotUser.all_tenants.get(tenant=opened, channel=channel, channel_user_id=channel_user_id)
    _delete(shell)
    assert active_solo_tenant(channel, channel_user_id) is None  # кабинет закрыт
    said.clear()

    salon_handler._ask_for_code_with_solo_offer(_event("привет", channel_user_id), entry=None)

    assert len(said) == 1
    assert said[0]["text"] == REJECTED_RECOVERY_TEXT
    assert salon_handler.SOLO_OFFER.strip() not in said[0]["text"]


def test_c14_the_closure_is_audited_in_the_cabinets_tenant(quiet_cascade: None) -> None:
    made = _register(*_identity("c14"))

    _delete(made.bot_user)

    rows = list(AuditLog.objects.filter(tenant=made.tenant, action=SOLO_CABINET_CLOSED))
    assert len(rows) == 1
    assert rows[0].target_id == made.tenant.id
    assert rows[0].payload["request_id"] == "cabinet-closure"
    assert rows[0].payload["surface"] == "account_deletion"
    assert PERSON_NAME not in str(rows[0].payload)


def test_c15_a_suffix_over_the_slug_limit_is_refused_by_name() -> None:
    base_48 = "x" * 48

    assert _next_solo_slug(base_48, {base_48}) == f"{base_48}-2"
    with pytest.raises(SoloOnboardingError):
        _next_solo_slug("x" * 49, {"x" * 49})
