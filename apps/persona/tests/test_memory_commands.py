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


def _add_green(upc, value):
    """Append one more live green diet row (the write path never supersedes).

    Every existing row is backdated by a day first, so «which one is newer» is
    deterministic rather than a microsecond race between two inserts.
    """
    from datetime import timedelta

    from django.utils import timezone

    MemoryEntry.objects.filter(user_id=upc.user_id).update(
        created_at=timezone.now() - timedelta(days=1)
    )
    return MemoryEntry.objects.create(
        user_id=upc.user_id,
        personal_context=upc,
        sensitivity_zone=MemoryEntry.SENSITIVITY_GREEN,
        source=MemoryEntry.SOURCE_EXPLICIT,
        provenance=MemoryEntry.PROVENANCE_USER_STATED,
        kind="lifestyle",
        content={"key": "diet", "value": value},
    )


def _upc_with_food_rows(upc=None):
    """Green rows with food-scanner keys (DRF-1454): the key carries the dish,
    so they cannot be listed in _KEY_KEYWORDS verbatim.

    Сегодня память сканера пишет только ``food_dish_name:*`` — вес и БЖУ
    принадлежат дневнику Ayla и не хранятся до DRF-825 (решение владельца
    04.09.2026, вариант А). Две другие строки сохранены в фикстуре намеренно:
    стирание по домену — страж на префикс, и оно обязано забрать любую строку
    food_*, включая ту, которую DRF-825 вернёт. Тест ловит регрессию в день
    возврата, а не в день, когда её кто-то заметит."""
    upc = upc or UserPersonalContext.objects.create(user_id=uuid.uuid4())
    for key, value, display in (
        ("food_portion:борщ", 500, "порция «борщ» — 500 г"),
        ("food_macros:борщ", "12/8/32", "БЖУ для «борщ» — 12/8/32"),
        ("food_dish_name:борщ", "Свекольник", "блюдо «борщ» называет «Свекольник»"),
    ):
        MemoryEntry.objects.create(
            user_id=upc.user_id,
            personal_context=upc,
            sensitivity_zone=MemoryEntry.SENSITIVITY_GREEN,
            source=MemoryEntry.SOURCE_EXPLICIT,
            provenance=MemoryEntry.PROVENANCE_USER_STATED,
            kind="preference",
            content={"key": key, "value": value, "dish": "борщ", "display": display},
        )
    return upc


class TestFoodScannerRowsBelongToThePitanieDomain:
    """Ревью DRF-1454, ось architecture, MUST_FIX_PRE_PILOT: ключи food_* не
    были зарегистрированы в _KEY_KEYWORDS/_DOMAIN_LABELS — «забудь всё про
    питание» удаляла только строки ключа diet, отвечала «Готово — забыла всё,
    что знала: питание», а строки food_* оставались живы. Кнопки «Забыть» у них
    тоже не было. Это право на стирание по 152-ФЗ и ADR-0011 §8: стирание,
    рапортующее успех, обязано стирать."""

    def test_domain_forget_takes_the_food_rows_with_it(self):
        upc = _upc_with_food_rows()
        assert (
            MemoryEntry.objects.filter(user_id=upc.user_id, soft_deleted_at__isnull=True).count()
            == 3
        )

        res = handle_memory_command(user_id=upc.user_id, text="забудь всё про питание")

        assert res is not None
        assert "забыла" in res.text.lower()
        alive = MemoryEntry.objects.filter(user_id=upc.user_id, soft_deleted_at__isnull=True)
        assert list(alive) == []
        # Tombstoned, not hard-deleted — audit rows stay.
        assert MemoryEntry.objects.filter(user_id=upc.user_id).count() == 3

    def test_domain_forget_covers_diet_and_food_rows_together(self):
        """Строка diet и строки food_* — один домен «питание», а не два
        неоднозначных (иначе команда уходила бы в clarify)."""
        upc = _upc_with_food_rows(_upc_with_green("vegan"))
        assert (
            MemoryEntry.objects.filter(user_id=upc.user_id, soft_deleted_at__isnull=True).count()
            == 4
        )

        res = handle_memory_command(user_id=upc.user_id, text="забудь всё про моё питание")

        assert res is not None
        assert "забыла" in res.text.lower()
        assert "Не совсем поняла" not in res.text
        alive = MemoryEntry.objects.filter(user_id=upc.user_id, soft_deleted_at__isnull=True)
        assert list(alive) == []

    def test_the_memory_list_offers_a_forget_chip_for_food_rows(self):
        from apps.persona.memory_commands import memory_show_chips

        upc = _upc_with_food_rows()

        chips = memory_show_chips(None, user_id=upc.user_id)
        assert any("питание" in chip["label"] for chip in chips)


class TestShowDoesNotContradictItself:
    """DRF-1262 — «покажи, что знаешь обо мне» must show the CURRENT fact set.

    A changed fact lands as a new live row and the old row stays live
    (memory_key_policy module docstring). The prompt is already collapsed by
    `read_current_view`; this surface was not, so the человек was shown «ты
    веган; ты на кето» while Ayla herself acted on «кето» alone. The system
    was showing the person something other than what it uses.
    """

    def test_show_renders_only_the_current_value_of_a_single_valued_key(self):
        upc = _upc_with_green("vegan")
        _add_green(upc, "vegetarian")

        res = handle_memory_command(user_id=upc.user_id, text="что ты обо мне знаешь")

        assert res is not None
        assert "вегетарианского питания" in res.text
        assert "веганского питания" not in res.text, (
            "SHOW surfaced both values of one single-valued key — the person "
            "is shown a contradiction the prompt never sees."
        )

    def test_show_still_renders_a_lone_fact(self):
        upc = _upc_with_green("vegan")
        res = handle_memory_command(user_id=upc.user_id, text="что ты обо мне знаешь")
        assert res is not None
        assert "веганского питания" in res.text

    def test_domain_forget_deletes_every_row_of_the_key(self):
        """DRF-1261 proof step 4 — «забудь всё про моё питание» removes the
        whole diet domain (all live rows, including the stale vegan one),
        and nothing else. Replaces the pre-DRF-1261 «clarify» behaviour: a
        domain word is not ambiguity, it is a target."""
        upc = _upc_with_green("vegan")
        _add_green(upc, "vegetarian")

        res = handle_memory_command(user_id=upc.user_id, text="забудь всё про моё питание")

        assert res is not None
        assert "забыла" in res.text.lower()
        assert res.action_type != "memory_forget_all_prompt", (
            "«забудь всё про питание» is a DOMAIN forget — forget-all must "
            "not fire (it would nuke every domain)"
        )
        remaining = MemoryEntry.objects.filter(user_id=upc.user_id, soft_deleted_at__isnull=True)
        assert list(remaining) == []
        # History is NOT hard-deleted — tombstoned rows stay for the audit.
        assert MemoryEntry.objects.filter(user_id=upc.user_id).count() == 2

    def test_domain_forget_leaves_other_domains(self):
        upc = _upc_with_green("vegan")
        MemoryEntry.objects.create(
            user_id=upc.user_id,
            personal_context=upc,
            sensitivity_zone=MemoryEntry.SENSITIVITY_GREEN,
            source=MemoryEntry.SOURCE_EXPLICIT,
            provenance=MemoryEntry.PROVENANCE_USER_STATED,
            kind="preference",
            content={"key": "preferred_time_slots", "value": "evening"},
        )

        res = handle_memory_command(user_id=upc.user_id, text="забудь всё про питание")

        assert res is not None
        assert "забыла" in res.text.lower()
        alive = MemoryEntry.objects.filter(user_id=upc.user_id, soft_deleted_at__isnull=True)
        assert [e.content["key"] for e in alive] == ["preferred_time_slots"]

    def test_clarify_summary_is_also_conflict_resolved(self):
        """The «не поняла, что забыть» fallback renders the same view."""
        upc = _upc_with_green("vegan")
        _add_green(upc, "vegetarian")

        res = handle_memory_command(user_id=upc.user_id, text="забудь мой адрес")

        assert res is not None
        assert "Не совсем поняла" in res.text
        assert "веганского питания" not in res.text

    def test_forget_still_reaches_a_superseded_row(self):
        """Deletion is NOT narrowed to the current view.

        152-ФЗ erasure targets what is STORED, not what is surfaced: «забудь,
        что я веган» must still erase the stale vegan row. Narrowing the
        matcher to the current view would also resurrect it — deleting the
        winning keto row would put vegan back in front of the person.
        """
        upc = _upc_with_green("vegan")
        current = _add_green(upc, "vegetarian")

        res = handle_memory_command(user_id=upc.user_id, text="забудь что я веган")

        assert res is not None
        assert "Готово" in res.text
        assert (
            MemoryEntry.objects.filter(user_id=upc.user_id, soft_deleted_at__isnull=True)
            .values_list("id", flat=True)
            .first()
            == current.id
        )
