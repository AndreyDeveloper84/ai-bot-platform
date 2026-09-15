"""DRF-1950 — «забудь всё» в чате, когда удаление в Ayla поставлено в задание.

Решение владельца M3: до authoritative readback не писать «забыла/удалено».
Если стирание в Ayla не подтверждено чтением каталога — человек слышит, что
удаление запущено и завершится в установленный срок, а не «до анкеты не
достучалась, напиши ещё раз» (повтор теперь делает задание, а не человек).
У несвязанного с Ayla удалять там нечего — «запущено» было бы ложью.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

from apps.identity.models import BotUser
from apps.persona import memory_commands
from apps.persona.memory_commands import FORGET_ALL_PROMPT, handle_memory_command
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

STARTED = "Удаление запущено. Оно завершится в установленный срок."
CONFIRMED = {
    "erased": True,
    "identities": [{"kind": "account", "context_row": "tombstone", "erased": True}],
}
NOT_CONFIRMED = {
    "erased": False,
    "identities": [{"kind": "account", "context_row": "holds_values", "erased": False}],
}


class _Ayla:
    def __init__(self, statuses: list) -> None:
        self.statuses = statuses
        self.calls: list[str] = []

    def delete_personal_data(self, *, ayla_user_id: str, external_user_id: str) -> None:
        self.calls.append("delete")

    def get_erasure_status(self, *, ayla_user_id: str, external_user_id: str) -> dict:
        self.calls.append("status")
        return self.statuses[0]

    def close(self) -> None:
        pass


@pytest.fixture(autouse=True)
def _flag_and_open_gate(settings, monkeypatch):
    settings.AYLA_ERASURE_RETRY_ENABLED = True
    # Проверяется ветка стирания, а не гейт памяти (§2.4): гейт открыт.
    monkeypatch.setattr(memory_commands, "_closed_to", lambda bot_user: False)


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="forget-started", name="Forget Started")


def _user(tenant: Tenant, *, linked: bool) -> BotUser:
    return BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id="828282",
        chat_id="828282",
        ayla_user_id=uuid.uuid4() if linked else None,
    )


def _confirm(bot_user: BotUser, ayla: _Ayla):
    with patch(
        "apps.identity.services.personal_context.PersonalContextHttpClient", return_value=ayla
    ):
        return handle_memory_command(
            user_id=bot_user.ayla_user_id or uuid.uuid4(),
            text="удалить",
            last_assistant_text=FORGET_ALL_PROMPT,
            bot_user=bot_user,
        )


def test_an_unconfirmed_erasure_says_the_deletion_is_started(tenant) -> None:
    bot_user = _user(tenant, linked=True)
    ayla = _Ayla([NOT_CONFIRMED])

    result = _confirm(bot_user, ayla)

    assert result is not None
    assert STARTED in result.text
    assert "не достучалась" not in result.text
    assert "забыла всё, что о тебе помнила" not in result.text
    assert ayla.calls == ["delete", "status"]


def test_a_confirmed_erasure_says_done_only_after_the_readback(tenant) -> None:
    bot_user = _user(tenant, linked=True)
    ayla = _Ayla([CONFIRMED])

    result = _confirm(bot_user, ayla)

    assert result is not None
    assert result.text.startswith("Готово — я забыла всё, что о тебе помнила.")
    assert ayla.calls == ["delete", "status"]


def test_an_unlinked_person_is_not_told_the_deletion_is_started(tenant) -> None:
    """Сторож: несвязанному — прежний честный частичный ответ, без «запущено»."""
    bot_user = _user(tenant, linked=False)
    ayla = _Ayla([NOT_CONFIRMED])

    result = _confirm(bot_user, ayla)

    assert result is not None
    assert "не достучалась" in result.text
    assert STARTED not in result.text
    assert ayla.calls == []
