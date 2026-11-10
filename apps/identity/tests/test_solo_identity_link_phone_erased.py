"""Телефон на записи о привязке соло-кабинета стирается вместе с телефоном оболочки.

``SoloIdentityLink`` — аудит-пакет того, как карточка соло-мастера получила
ключ Ayla; при открытии он копирует ``BotUser.phone``. Строка привязана к
карточке, а не к человеку, поэтому реестр личных полей её не видит — и до
правки каскад стирал телефон на оболочке, а тот же номер оставался на записи
(найдено на живом прогоне 09.11.2026).

Узлы:

* p1 — удаление аккаунта соло-мастера: телефон на записи пуст, остальное
  (статус, канал, id в канале, оператор, ключ) — на месте;
* p2 — карточка уже отвязана (так бывает у не-владельца: доступ снимается до
  каскада) — запись найдена по личности канала и телефон стёрт всё равно;
* p3 — сосед не тронут;
* p4 — повтор ничего не меняет;
* p5 — без телефона на записи — нет записи в счётчике (и нет лишнего UPDATE);
* p6 — при конфликте личности ключ канала не применяется, только связь карточки.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

from apps.catalog.models import CatalogMaster
from apps.identity.models import BotUser, SoloIdentityLink
from apps.identity.services import privacy
from apps.identity.services.account_deletion import execute_bot_half
from apps.identity.services.solo_identity_link import open_link
from apps.identity.services.solo_onboarding import create_solo_provider
from apps.integrations.ayla.user_proxy import external_user_id_for

pytestmark = pytest.mark.django_db

PHONE = "+79990001122"


class _Catalog:
    """Каталог за фальшивым клиентом: каскад к нему не ходит (DRF-2639), но имя импортировано."""

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return None

    def close(self):
        return None

    def delete_personal_data(self, **kw):
        raise AssertionError("каскад не должен звать каталог: он уже стёр свою половину")


@pytest.fixture
def quiet_catalog(settings):
    settings.STRICT_TENANT_SCOPE = "off"
    with patch(
        "apps.identity.services.privacy.PersonalContextHttpClient", lambda *a, **k: _Catalog()
    ):
        yield


def _solo(label: str) -> tuple[BotUser, CatalogMaster, SoloIdentityLink]:
    made = create_solo_provider(
        channel="max",
        channel_user_id=f"{label}-{uuid.uuid4().hex[:8]}",
        display_name="Ольга Петрова",
        phone=PHONE,
    )
    link = open_link(made.master, bot_user=made.bot_user, tenant=made.tenant, phone=PHONE)
    link.status = SoloIdentityLink.Status.LINKED
    link.ayla_user_id = uuid.uuid4()
    link.operator_username = "operator_1"
    link.save(update_fields=["status", "ayla_user_id", "operator_username"])
    return made.bot_user, made.master, link


def _delete(bot_user: BotUser):
    return execute_bot_half(
        ayla_user_id=uuid.uuid4(),
        external_user_ids=[external_user_id_for(bot_user)],
        request_id="link-phone",
    )


def _pii_step_ok(outcome) -> bool:
    """Предмет — шаг ``profile_pii_erase``; соседние шаги каскада (каталог, Redis) — не он."""
    (step,) = [s for s in outcome.steps if s["step"] == "profile_pii_erase"]
    return bool(step["ok"])


def test_p1_account_deletion_blanks_the_phone_and_keeps_the_audit_trail(quiet_catalog):
    """Красный до правки: оболочка без телефона, запись — с ним."""
    bot_user, _master, link = _solo("p1")
    before = {
        "status": link.status,
        "channel": link.channel,
        "channel_user_id": link.channel_user_id,
        "operator_username": link.operator_username,
        "ayla_user_id": link.ayla_user_id,
    }

    outcome = _delete(bot_user)

    assert _pii_step_ok(outcome), outcome.steps
    bot_user.refresh_from_db()
    link.refresh_from_db()
    assert bot_user.phone == ""
    assert link.phone == ""
    after = {
        "status": link.status,
        "channel": link.channel,
        "channel_user_id": link.channel_user_id,
        "operator_username": link.operator_username,
        "ayla_user_id": link.ayla_user_id,
    }
    assert after == before


def test_p2_a_link_whose_card_is_already_unlinked_is_found_by_channel_identity(quiet_catalog):
    bot_user, master, link = _solo("p2")
    master.linked_bot_user = None
    master.save(update_fields=["linked_bot_user"])

    outcome = _delete(bot_user)

    assert _pii_step_ok(outcome), outcome.steps
    link.refresh_from_db()
    assert link.phone == ""


def test_p3_the_neighbours_link_is_untouched(quiet_catalog):
    bot_user, _master, link = _solo("p3")
    _other, _other_master, other_link = _solo("p3n")

    _delete(bot_user)

    link.refresh_from_db()
    other_link.refresh_from_db()
    assert (link.phone, other_link.phone) == ("", PHONE)


def test_p4_a_second_run_changes_nothing(quiet_catalog):
    bot_user, _master, link = _solo("p4")
    _delete(bot_user)

    again = _delete(bot_user)

    assert _pii_step_ok(again), again.steps
    link.refresh_from_db()
    assert link.phone == ""


def test_p5_the_helper_counts_only_rows_it_changed():
    bot_user, _master, link = _solo("p5")
    other, _om, _ol = _solo("p5x")

    no_conflict = privacy._PersonLink()

    assert privacy._erase_solo_link_phone({bot_user.id}, no_conflict) == 1
    assert privacy._erase_solo_link_phone({bot_user.id}, no_conflict) == 0
    assert privacy._erase_solo_link_phone({other.id}, no_conflict) == 1
    link.refresh_from_db()
    assert link.phone == ""


def test_p6_in_an_identity_conflict_only_the_card_key_is_used():
    """Сужение при конфликте личности — то же, что у оболочек: по каналу не раскидываемся."""
    bot_user, master, link = _solo("p6")
    master.linked_bot_user = None
    master.save(update_fields=["linked_bot_user"])

    assert privacy._erase_solo_link_phone({bot_user.id}, privacy._PersonLink(conflict=True)) == 0
    link.refresh_from_db()
    assert link.phone == PHONE

    assert privacy._erase_solo_link_phone({bot_user.id}, privacy._PersonLink()) == 1
