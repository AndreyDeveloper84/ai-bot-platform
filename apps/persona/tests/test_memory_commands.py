"""Chat memory command tests (M-B4 / #1113)."""

from __future__ import annotations

import uuid

import pytest

from apps.identity.models import MemoryEntry, UserPersonalContext
from apps.persona.memory_commands import FORGET_ALL_PROMPT, handle_memory_command

pytestmark = pytest.mark.django_db(transaction=True)


def _upc_with_green(value="vegan"):
    upc = UserPersonalContext.objects.create(user_id=uuid.uuid4())
    MemoryEntry.objects.create(
        user_id=upc.user_id,
        personal_context=upc,
        sensitivity_zone=MemoryEntry.SENSITIVITY_GREEN,
        source=MemoryEntry.SOURCE_EXPLICIT,
        provenance=MemoryEntry.PROVENANCE_USER_STATED,  # CHECK 5 (DRF-1263)
        kind="lifestyle",
        content={"key": "diet", "value": value},
    )
    return upc


class TestNonCommand:
    def test_returns_none_for_ordinary_text(self):
        assert handle_memory_command(user_id=uuid.uuid4(), text="хочу маникюр") is None

    def test_returns_none_for_empty(self):
        assert handle_memory_command(user_id=uuid.uuid4(), text="") is None


class TestShow:
    def test_show_empty(self):
        res = handle_memory_command(user_id=uuid.uuid4(), text="покажи что знаешь обо мне")
        assert res is not None
        assert "ничего" in res.text.lower()

    def test_show_with_facts(self):
        upc = _upc_with_green("vegan")
        res = handle_memory_command(user_id=upc.user_id, text="что ты обо мне знаешь?")
        assert res is not None
        assert "веганского питания" in res.text


class TestForgetField:
    def test_forget_matching_field_deletes(self):
        upc = _upc_with_green("vegan")
        res = handle_memory_command(user_id=upc.user_id, text="забудь что я веган")
        assert res is not None
        assert "забыла" in res.text.lower()
        # Entry soft-deleted.
        e = MemoryEntry.objects.get(user_id=upc.user_id)
        assert e.soft_deleted_at is not None
        assert e.deletion_reason == MemoryEntry.DELETION_REASON_USER_DELETE

    def test_forget_with_no_memory(self):
        res = handle_memory_command(user_id=uuid.uuid4(), text="забудь мою диету")
        assert res is not None
        assert "нечего" in res.text.lower()

    def test_forget_no_match_clarifies(self):
        upc = _upc_with_green("vegan")
        res = handle_memory_command(user_id=upc.user_id, text="забудь мой адрес")
        assert res is not None
        assert "не совсем поняла" in res.text.lower()
        # Nothing deleted.
        assert MemoryEntry.objects.get(user_id=upc.user_id).soft_deleted_at is None

    def test_demonstrative_target_falls_through(self):
        # «забудь это» is filler, not a memory-field command → discovery handles it.
        upc = _upc_with_green("vegan")
        assert handle_memory_command(user_id=upc.user_id, text="забудь это") is None

    def test_account_removal_phrasing_falls_through(self):
        # «удали меня из рассылки» is account/newsletter, not memory → not a command.
        upc = _upc_with_green("vegan")
        assert handle_memory_command(user_id=upc.user_id, text="удали меня из рассылки") is None


class TestForgetAll:
    def test_request_returns_prompt_without_deleting(self):
        upc = _upc_with_green("vegan")
        res = handle_memory_command(user_id=upc.user_id, text="забудь всё")
        assert res is not None
        assert res.action_type == "memory_forget_all_prompt"
        assert "удалить" in res.text.lower()
        # NOT executed yet.
        upc.refresh_from_db()
        assert upc.forget_all_requested_at is None
        assert MemoryEntry.objects.get(user_id=upc.user_id).soft_deleted_at is None

    def test_confirm_word_with_pending_executes(self):
        upc = _upc_with_green("vegan")
        res = handle_memory_command(
            user_id=upc.user_id, text="удалить", last_assistant_text=FORGET_ALL_PROMPT
        )
        assert res is not None
        assert "забыла" in res.text.lower()
        upc.refresh_from_db()
        assert upc.forget_all_requested_at is not None

    def test_confirm_word_without_pending_is_not_a_command(self):
        upc = _upc_with_green("vegan")
        res = handle_memory_command(user_id=upc.user_id, text="удалить")
        assert res is None
        upc.refresh_from_db()
        assert upc.forget_all_requested_at is None

    def test_forget_vse_variant_folds_yo(self):
        upc = _upc_with_green("vegan")
        res = handle_memory_command(user_id=upc.user_id, text="забудь все")
        assert res is not None
        assert res.action_type == "memory_forget_all_prompt"
