# ruff: noqa: F811 — фикстуры набора согласий импортируются и принимаются параметрами
"""DRF-2783 — отзыв согласия Ф4 через ручку Mini App стирает производную память.

Решение владельца 05.10: «при отзыве согласия Ф4 — сразу прекратить новые
выводы и их использование, удалить производную память по процедуре». Узлы на
исходе, через ту же ручку, что нажимает человек:

* выводы (``source='inferred'``) человека — по всем его оболочкам — сняты с
  причиной ``withdrawal``; явные факты целы; ``personal_data`` действует;
* повторный отзыв дочищает выводы, появившиеся после первого;
* сбой стирания не откатывает отзыв — согласие снято всё равно.

Сама функция стирания — ``apps/identity/tests/test_memory_inference_withdrawal_2783.py``.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.consent import preference_inference
from apps.consent.models import ConsentRecord
from apps.consent.services import has_global_consent
from apps.identity.models import BotUser, MemoryEntry, UserPersonalContext
from apps.miniapp_api.tests.test_customer_consents import (  # noqa: F401 — fixtures
    _bot_token,
    _init_data_header,
    _no_ayla_link,
    auth,
    bot_user,
    tenant,
)
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

PD = ConsentRecord.ConsentType.PERSONAL_DATA.value
VERSION = "preference-inference-draft-v1"


@pytest.fixture
def pi_url() -> str:
    return reverse("miniapp_api:customer_preference_inference_consent")


@pytest.fixture
def person(bot_user: BotUser) -> uuid.UUID:
    """Связанный человек: память ключуется ``ayla_user_id``."""
    key = uuid.uuid4()
    BotUser.all_tenants.filter(pk=bot_user.pk).update(ayla_user_id=key)
    bot_user.refresh_from_db()
    UserPersonalContext.objects.create(user_id=key)
    return key


def _row(user_id: uuid.UUID, *, source: str = MemoryEntry.SOURCE_INFERRED) -> MemoryEntry:
    explicit = source == MemoryEntry.SOURCE_EXPLICIT
    return MemoryEntry.objects.create(
        user_id=user_id,
        personal_context=UserPersonalContext.objects.get(user_id=user_id),
        sensitivity_zone=MemoryEntry.SENSITIVITY_GREEN,
        source=source,
        provenance=MemoryEntry.PROVENANCE_USER_STATED if explicit else None,
        last_inferred_at=None if explicit else timezone.now() - timedelta(days=1),
        content={"key": "preferred_time_slots", "value": "evening"},
    )


def _grant(client: Client, url: str, auth: dict) -> None:
    response = client.post(
        url, data={"document_version": VERSION}, content_type="application/json", **auth
    )
    assert response.status_code == 200, response.content


def _live(entry: MemoryEntry) -> bool:
    entry.refresh_from_db()
    return entry.soft_deleted_at is None


def test_withdrawal_erases_the_inferences_and_keeps_the_rest(
    client: Client, bot_user: BotUser, person: uuid.UUID, pi_url: str, auth: dict
) -> None:
    _grant(client, pi_url, auth)
    inferred = _row(person)
    stated = _row(person, source=MemoryEntry.SOURCE_EXPLICIT)

    response = client.delete(pi_url, **auth)

    assert response.status_code == 200, response.content
    assert preference_inference.is_granted(bot_user) is False
    assert _live(stated)
    assert has_global_consent(bot_user, PD) is True
    inferred.refresh_from_db()
    assert inferred.deletion_reason == "withdrawal"
    assert not _live(inferred)


def test_inferences_under_the_chat_shell_go_too(
    client: Client, bot_user: BotUser, pi_url: str, auth: dict
) -> None:
    """Пилотный случай: оболочка Mini App связки с Ayla не имеет, а чатовая
    оболочка того же человека (тот же канальный ключ, тенант-сентинел) — имеет,
    и выводы лежат под её ключом. Отзыв из Mini App обязан дойти и до них."""
    assert bot_user.ayla_user_id is None
    sentinel = Tenant.objects.create(slug="pi-2783-global", name="Global")
    chat_key = uuid.uuid4()
    BotUser.all_tenants.create(
        tenant=sentinel,
        channel="max",
        channel_user_id=bot_user.channel_user_id,
        chat_id=f"chat-{bot_user.channel_user_id}",
        ayla_user_id=chat_key,
    )
    UserPersonalContext.objects.create(user_id=chat_key)
    stranger_key = uuid.uuid4()
    UserPersonalContext.objects.create(user_id=stranger_key)
    stranger = _row(stranger_key)
    _grant(client, pi_url, auth)
    own = _row(chat_key)

    client.delete(pi_url, **auth)

    assert _live(stranger)
    assert not _live(own)


def test_a_repeated_withdrawal_cleans_what_appeared_after_the_first(
    client: Client, bot_user: BotUser, person: uuid.UUID, pi_url: str, auth: dict
) -> None:
    _grant(client, pi_url, auth)
    client.delete(pi_url, **auth)
    late = _row(person)
    assert _live(late)

    response = client.delete(pi_url, **auth)

    assert response.status_code == 200, response.content
    assert not _live(late)


def test_a_failed_erase_does_not_undo_the_withdrawal(
    client: Client, bot_user: BotUser, person: uuid.UUID, pi_url: str, auth: dict
) -> None:
    _grant(client, pi_url, auth)
    inferred = _row(person)

    with patch(
        "apps.identity.services.memory_deleter.soft_delete_inferences_for_withdrawal",
        side_effect=RuntimeError("store is down"),
    ):
        response = client.delete(pi_url, **auth)

    assert response.status_code == 200, response.content
    assert _live(inferred)
    assert preference_inference.is_granted(bot_user) is False
