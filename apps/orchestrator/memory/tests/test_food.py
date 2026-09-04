"""Food-scanner memory — zones, provenance, perimeter (DRF-1454).

Five properties are locked here, and the last three matter as much as the first:

1. The dish NAME a person typed is stored 🟢 green, ``explicit``, and read back
   on the next turn — the whole point of the ticket.
2. Re-correcting the same dish supersedes rather than accumulates: one dish
   never has two current names.
3. Weight and macros are **not** stored at all (owner decision 2026-09-04,
   variant А): the nutrition diary is Ayla's by the ADR-0009 ownership matrix,
   ``nutrition_client`` has no update endpoint until DRF-825, and a local copy
   would leave two numbers for one meal. ``remember_correction`` answers
   ``NOT_REMEMBERED`` and writes nothing.
4. «Что ел» and «что не подошло» are classified and **not** stored. The count of
   ``MemoryEntry`` rows after those calls is zero, and that zero is the assertion
   — a perimeter that quietly stores is worse than no perimeter.
5. Only a source in :data:`ayla_ai_core.STATED_SOURCES` comes back to the person
   as their own correction. A derived row with the same key stays invisible.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from django.utils import timezone

from ayla_ai_core import SOURCE_INFERRED as CORE_SOURCE_INFERRED
from apps.consent.models import ConsentRecord
from apps.consent.services import record_global_consent
from apps.identity.models import MemoryEntry
from apps.identity.services import resolve_or_create_global_bot_user
from apps.identity.services.memory_reader import get_or_create_personal_context
from apps.identity.services.memory_writer import write_entry
from apps.integrations.ayla.identity_client import ResolvedIdentity
from apps.orchestrator.memory import food as food_memory

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def resolver(monkeypatch: pytest.MonkeyPatch):
    """Stub the Ayla identity read-back so memory has a subject to key on."""

    state: dict[str, Any] = {"uuid": uuid.uuid4()}

    def _fake(external_user_id: str) -> ResolvedIdentity:
        return ResolvedIdentity(ayla_user_id=state["uuid"], is_proxy=True)

    monkeypatch.setattr(
        "apps.integrations.ayla.identity_client.resolve_identity", _fake, raising=True
    )
    return state


def _consented_user(uid: str, settings, *, memory_green: bool = True):
    settings.STRICT_TENANT_SCOPE = "strict"
    bot_user = resolve_or_create_global_bot_user(channel="max", channel_user_id=uid)
    record_global_consent(bot_user, source="welcome")
    if memory_green:
        record_global_consent(
            bot_user,
            consent_type=ConsentRecord.ConsentType.MEMORY_GREEN.value,
            source="welcome",
        )
    return bot_user


def _green_rows(user_id: uuid.UUID) -> int:
    return MemoryEntry.objects.filter(
        user_id=user_id,
        sensitivity_zone=MemoryEntry.SENSITIVITY_GREEN,
        soft_deleted_at__isnull=True,
    ).count()


# ─── зона 🟢: «что уже уточнял» ───────────────────────────────────────────


class TestClarificationIsRemembered:
    def test_correction_is_written_green_explicit_and_read_back(self, settings, resolver) -> None:
        bot_user = _consented_user("drf1454-1", settings)

        outcome = food_memory.remember_correction(
            bot_user, dish="Борщ", field=food_memory.FIELD_NAME, value="борщ по-домашнему"
        )

        assert outcome is food_memory.Outcome.WRITTEN
        assert _green_rows(resolver["uuid"]) == 1
        entry = MemoryEntry.objects.get(user_id=resolver["uuid"])
        assert entry.sensitivity_zone == MemoryEntry.SENSITIVITY_GREEN
        assert entry.source == MemoryEntry.SOURCE_EXPLICIT
        # The sanctioned writer stamps canonical provenance off `explicit`.
        assert entry.provenance == MemoryEntry.PROVENANCE_USER_STATED
        assert entry.consent_at is None  # green: service-contract basis

        bot_user.refresh_from_db()
        recall = food_memory.recall_corrections(bot_user, dish="борщ")
        assert recall.dish_name == "борщ по-домашнему"
        assert recall.has(food_memory.FIELD_NAME)

    def test_dish_key_is_normalised_not_case_sensitive(self, settings, resolver) -> None:
        bot_user = _consented_user("drf1454-2", settings)
        food_memory.remember_correction(
            bot_user,
            dish="  Плов   узбекский ",
            field=food_memory.FIELD_NAME,
            value="плов по-фергански",
        )
        bot_user.refresh_from_db()

        recall = food_memory.recall_corrections(bot_user, dish="ПЛОВ УЗБЕКСКИЙ")
        assert recall.dish_name == "плов по-фергански"

    def test_memory_is_per_dish(self, settings, resolver) -> None:
        bot_user = _consented_user("drf1454-3", settings)
        food_memory.remember_correction(
            bot_user, dish="борщ", field=food_memory.FIELD_NAME, value="борщ по-домашнему"
        )
        bot_user.refresh_from_db()

        assert food_memory.recall_corrections(bot_user, dish="плов").is_empty()

    def test_only_the_name_is_kept_of_the_three_answers(self, settings, resolver) -> None:
        """Три вопроса задаются, ответ хранится один — имя блюда.

        Решение владельца от 04.09.2026, вариант А: вес и БЖУ принадлежат
        дневнику Ayla (матрица владения ADR-0009), эндпоинта обновления нет до
        DRF-825, и локальная копия дала бы две цифры на один приём пищи. Этот
        тест раньше требовал обратного — «все три поля кладутся в память»; он
        изменён, а не удалён, потому что это ровно то место, которое стало
        блокером слияния.
        """
        bot_user = _consented_user("drf1454-4", settings)

        assert (
            food_memory.remember_correction(
                bot_user, dish="борщ", field=food_memory.FIELD_NAME, value="плов"
            )
            is food_memory.Outcome.WRITTEN
        )
        for field, value in (
            (food_memory.FIELD_GRAMS, 500),
            (food_memory.FIELD_MACROS, "12/8/32"),
        ):
            assert (
                food_memory.remember_correction(bot_user, dish="борщ", field=field, value=value)
                is food_memory.Outcome.NOT_REMEMBERED
            )
        bot_user.refresh_from_db()

        assert _green_rows(resolver["uuid"]) == 1  # the name, and nothing else
        recall = food_memory.recall_corrections(bot_user, dish="борщ")
        assert recall.dish_name == "плов"
        assert not recall.has(food_memory.FIELD_GRAMS)
        assert not recall.has(food_memory.FIELD_MACROS)

    def test_repeated_identical_correction_does_not_duplicate(self, settings, resolver) -> None:
        bot_user = _consented_user("drf1454-5", settings)
        food_memory.remember_correction(
            bot_user, dish="борщ", field=food_memory.FIELD_NAME, value="борщ по-домашнему"
        )
        bot_user.refresh_from_db()

        outcome = food_memory.remember_correction(
            bot_user, dish="борщ", field=food_memory.FIELD_NAME, value="борщ по-домашнему"
        )

        assert outcome is food_memory.Outcome.DUPLICATE
        assert _green_rows(resolver["uuid"]) == 1

    def test_re_correction_supersedes_instead_of_contradicting(self, settings, resolver) -> None:
        bot_user = _consented_user("drf1454-6", settings)
        food_memory.remember_correction(
            bot_user, dish="борщ", field=food_memory.FIELD_NAME, value="борщ по-домашнему"
        )
        bot_user.refresh_from_db()
        food_memory.remember_correction(
            bot_user, dish="борщ", field=food_memory.FIELD_NAME, value="борщ постный"
        )

        # History is kept (2 rows), but only the current value is surfaced.
        assert _green_rows(resolver["uuid"]) == 2
        superseded = MemoryEntry.objects.filter(status=MemoryEntry.STATUS_SUPERSEDED)
        assert superseded.count() == 1
        superseded_row = superseded.first()
        assert superseded_row is not None
        assert superseded_row.supersession_reason == MemoryEntry.SUPERSESSION_CORRECTED
        assert food_memory.recall_corrections(bot_user, dish="борщ").dish_name == "борщ постный"


# ─── провенанс ────────────────────────────────────────────────────────────


class TestProvenance:
    def test_derived_row_is_never_returned_as_the_persons_own_words(
        self, settings, resolver
    ) -> None:
        """A row with the same key but a non-stated source must stay invisible.

        The failure this pins is the asymmetric one (`ayla_ai_core.memory`):
        showing a guess as «ты поправил» is what the product avoids on purpose.
        """
        bot_user = _consented_user("drf1454-7", settings)
        user_id = uuid.UUID(str(bot_user.ayla_user_id)) if bot_user.ayla_user_id else None
        if user_id is None:
            # Nothing written yet → mint the link the same way a write would.
            from apps.identity.services.ayla_link import ensure_ayla_link

            user_id = ensure_ayla_link(bot_user, trigger="test")
            bot_user.refresh_from_db()
        assert user_id is not None

        write_entry(
            user_id=user_id,
            personal_context=get_or_create_personal_context(user_id),
            sensitivity_zone=MemoryEntry.SENSITIVITY_GREEN,
            source=MemoryEntry.SOURCE_INFERRED,
            kind="preference",
            content={
                "key": "food_dish_name:борщ",
                "value": "борщ по-домашнему",
                "dish": "борщ",
                "field": "name",
            },
            request_id=uuid.uuid4(),
            purpose="test:inferred",
            last_inferred_at=timezone.now(),
        )

        assert _green_rows(user_id) == 1  # the row exists…
        assert food_memory.recall_corrections(bot_user, dish="борщ").is_empty()  # …and is silent

    def test_the_stated_dictionary_is_the_shared_one(self) -> None:
        """The rule is «only explicit is a quote», sourced from ayla-ai-core."""
        from typing import cast

        try:
            from ayla_ai_core import STATED_SOURCES
        except ImportError:
            pytest.skip("pinned ayla-ai-core predates STATED_SOURCES (see food.py)")

        stated = cast(frozenset[str], STATED_SOURCES)
        assert MemoryEntry.SOURCE_EXPLICIT in stated
        assert MemoryEntry.SOURCE_INFERRED not in stated
        assert MemoryEntry.SOURCE_SIGNAL not in stated
        assert CORE_SOURCE_INFERRED not in stated


# ─── зоны 🟡 / 🔴: перимeтр ───────────────────────────────────────────────


class TestPerimeterStoresNothing:
    def test_meal_history_is_classified_yellow_and_not_stored(self, settings, resolver) -> None:
        bot_user = _consented_user("drf1454-8", settings)

        outcome = food_memory.note_meal(bot_user, dish="Борщ")

        assert outcome is food_memory.Outcome.DROPPED_SENSITIVE
        assert MemoryEntry.objects.count() == 0

    @pytest.mark.parametrize(
        "text",
        [
            "у меня аллергия на орехи",
            "непереносимость лактозы",
        ],
    )
    def test_intolerance_is_red_and_not_stored(self, settings, resolver, text: str) -> None:
        bot_user = _consented_user(f"drf1454-red-{abs(hash(text)) % 999}", settings)

        assert food_memory.classify_refusal(text) == MemoryEntry.SENSITIVITY_RED
        assert (
            food_memory.note_refusal(bot_user, text=text) is food_memory.Outcome.DROPPED_SENSITIVE
        )
        assert MemoryEntry.objects.count() == 0

    @pytest.mark.parametrize(
        "text",
        [
            "я не ем мясо",
            "мне нельзя сладкое",
            "молочное я не пью",
        ],
    )
    def test_plain_exclusion_is_yellow_and_not_stored(self, settings, resolver, text: str) -> None:
        bot_user = _consented_user(f"drf1454-yel-{abs(hash(text)) % 999}", settings)

        assert food_memory.classify_refusal(text) == MemoryEntry.SENSITIVITY_YELLOW
        assert (
            food_memory.note_refusal(bot_user, text=text) is food_memory.Outcome.DROPPED_SENSITIVE
        )
        assert MemoryEntry.objects.count() == 0

    def test_medical_marker_wins_over_plain_exclusion(self) -> None:
        assert (
            food_memory.classify_refusal("я не ем молочное, у меня непероносимость")
            == MemoryEntry.SENSITIVITY_YELLOW
        )
        assert (
            food_memory.classify_refusal("я не ем молочное, у меня непереносимость лактозы")
            == MemoryEntry.SENSITIVITY_RED
        )

    @pytest.mark.parametrize(
        "text",
        [
            "500",
            "борщ",
            "было больше",
            "",
            # A drink is ordered «без сахара»; that is a description, not a
            # refusal. Answering it with the sensitive-perimeter script put the
            # refusal explanation in front of somebody who refused nothing.
            "Кофе без сахара",
            "Латте без лактозы",
        ],
    )
    def test_ordinary_answer_is_not_a_refusal(self, text: str) -> None:
        # Presence first: the classifier is alive on a genuine refusal.
        assert food_memory.classify_refusal("у меня непереносимость лактозы") != ""

        assert food_memory.classify_refusal(text) == ""

    def test_recognition_rejection_is_not_memory(self, settings, resolver) -> None:
        """«Не то» says the recogniser was wrong — never «он это не ест»."""
        bot_user = _consented_user("drf1454-9", settings)

        food_memory.note_recognition_rejected(bot_user, scan_id="scan-1")

        assert MemoryEntry.objects.count() == 0


class TestSensitiveStatementsNeverPassTheDishFilter:
    """Ревью DRF-1454, ось correctness, MUST_FIX_PRE_PILOT.

    Периметр держался только на ``аллерг|непереносимост`` и ``не (ем|пью)`` —
    всё остальное падало в ветку «имя блюда» и писалось в зелёную зону.
    Воспроизводящий вход из находки: карточка «Салат» → ✏️ → «название» →
    «у меня диабет» → зелёная строка «блюдо „салат“ называет „у меня диабет“».
    Докстрока модуля обещает, что диагноз и исключение никогда не попадут в
    green — эти девять строк и есть обещание, прогнанное против regex.
    """

    @pytest.mark.parametrize(
        "text",
        [
            "у меня диабет",
            "у меня гастрит",
            "у меня целиакия",
            "я беременна",
            "кормлю грудью",
        ],
    )
    def test_a_diagnosis_or_health_state_is_red(self, text: str) -> None:
        assert food_memory.classify_refusal(text) == MemoryEntry.SENSITIVITY_RED

    @pytest.mark.parametrize(
        "text",
        [
            "не кушаю мясо",
            "я вегетарианец",
            "халяль",
            "пощусь",
        ],
    )
    def test_a_plain_exclusion_is_yellow(self, text: str) -> None:
        assert food_memory.classify_refusal(text) == MemoryEntry.SENSITIVITY_YELLOW

    @pytest.mark.parametrize(
        "text",
        [
            "у меня диабет",
            "я беременна",
            "я вегетарианец",
            "халяль",
            "пощусь",
        ],
    )
    def test_none_of_them_is_written_to_the_green_zone(self, settings, resolver, text: str) -> None:
        bot_user = _consented_user(f"drf1454-sens-{abs(hash(text)) % 999}", settings)

        assert (
            food_memory.note_refusal(bot_user, text=text) is food_memory.Outcome.DROPPED_SENSITIVE
        )
        assert MemoryEntry.objects.count() == 0


# ─── согласие ─────────────────────────────────────────────────────────────


class TestConsentGates:
    def test_no_personal_data_consent_stores_nothing_and_mints_no_identity(
        self, settings, resolver
    ) -> None:
        settings.STRICT_TENANT_SCOPE = "strict"
        bot_user = resolve_or_create_global_bot_user(channel="max", channel_user_id="drf1454-10")

        outcome = food_memory.remember_correction(
            bot_user, dish="борщ", field=food_memory.FIELD_NAME, value="борщ по-домашнему"
        )

        assert outcome is food_memory.Outcome.NO_CONSENT
        assert MemoryEntry.objects.count() == 0
        bot_user.refresh_from_db()
        assert bot_user.ayla_user_id is None  # J-O3: nothing stored → nothing minted

    def test_memory_green_is_required_on_top_of_personal_data(self, settings, resolver) -> None:
        """The gate the ticket names: global-by-ayla_user_id, not tenant-scoped."""
        bot_user = _consented_user("drf1454-11", settings, memory_green=False)

        outcome = food_memory.remember_correction(
            bot_user, dish="борщ", field=food_memory.FIELD_NAME, value="борщ по-домашнему"
        )

        assert outcome is food_memory.Outcome.NO_CONSENT
        assert MemoryEntry.objects.count() == 0

    def test_read_is_gated_on_memory_green_too(self, settings, resolver) -> None:
        bot_user = _consented_user("drf1454-12", settings)
        food_memory.remember_correction(
            bot_user, dish="борщ", field=food_memory.FIELD_NAME, value="борщ по-домашнему"
        )
        bot_user.refresh_from_db()
        recall = food_memory.recall_corrections(bot_user, dish="борщ")
        assert recall.dish_name == "борщ по-домашнему"

        ConsentRecord.all_tenants.filter(
            bot_user=bot_user,
            consent_type=ConsentRecord.ConsentType.MEMORY_GREEN,
        ).update(withdrawn_at=timezone.now())

        assert food_memory.recall_corrections(bot_user, dish="борщ").is_empty()

    def test_forgotten_user_never_accretes_new_memory(self, settings, resolver) -> None:
        bot_user = _consented_user("drf1454-13", settings)
        food_memory.remember_correction(
            bot_user, dish="борщ", field=food_memory.FIELD_NAME, value="борщ по-домашнему"
        )
        bot_user.refresh_from_db()
        upc = get_or_create_personal_context(resolver["uuid"])
        upc.forget_all_requested_at = timezone.now()
        upc.save(update_fields=["forget_all_requested_at"])

        outcome = food_memory.remember_correction(
            bot_user, dish="плов", field=food_memory.FIELD_NAME, value="плов с бараниной"
        )

        assert outcome is food_memory.Outcome.FORGOTTEN
        assert _green_rows(resolver["uuid"]) == 1  # only the pre-forget row


# ─── парсинг ──────────────────────────────────────────────────────────────


class TestParsing:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("500", 500),
            ("500 г", 500),
            ("около 250 грамм", 250),
            ("0", None),
            ("99999", None),
            ("без числа", None),
        ],
    )
    def test_grams(self, text: str, expected: int | None) -> None:
        assert food_memory.parse_correction_value(food_memory.FIELD_GRAMS, text) == expected

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("12/8/32", "12/8/32"),
            ("Б12 / Ж8 / У32", "12/8/32"),
            ("12-8-32", None),
            # A date typed into the macros prompt is not this person's macros.
            ("12/08/2026", None),
            ("9999/9999/9999", None),
            ("0/0/0", None),
        ],
    )
    def test_macros(self, text: str, expected: str | None) -> None:
        assert food_memory.parse_correction_value(food_memory.FIELD_MACROS, text) == expected

    @pytest.mark.parametrize(
        "text,expected",
        [
            # Case is the person's, not ours: the KEY is normalised, the value
            # comes back to them exactly as they typed it.
            ("Плов", "Плов"),
            ("   Плов   Узбекский ", "Плов Узбекский"),
            ("500", None),  # a bare number answers the grams question, not name
            ("", None),
        ],
    )
    def test_name(self, text: str, expected: str | None) -> None:
        assert food_memory.parse_correction_value(food_memory.FIELD_NAME, text) == expected

    def test_unknown_field_parses_to_nothing(self) -> None:
        assert food_memory.parse_correction_value("calories", "500") is None


# ─── деградация ───────────────────────────────────────────────────────────


class TestNeverBreaksTheTurn:
    def test_recall_swallows_a_broken_backend(
        self, settings, resolver, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        bot_user = _consented_user("drf1454-14", settings)
        food_memory.remember_correction(
            bot_user, dish="борщ", field=food_memory.FIELD_NAME, value="борщ по-домашнему"
        )
        bot_user.refresh_from_db()

        def _boom(*_a: Any, **_kw: Any) -> Any:
            raise RuntimeError("db is on fire")

        monkeypatch.setattr(food_memory, "read_current_view", _boom, raising=True)

        assert food_memory.recall_corrections(bot_user, dish="борщ").is_empty()

    def test_write_swallows_a_broken_backend(
        self, settings, resolver, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        bot_user = _consented_user("drf1454-15", settings)

        def _boom(*_a: Any, **_kw: Any) -> Any:
            raise RuntimeError("db is on fire")

        monkeypatch.setattr(food_memory, "write_entry", _boom, raising=True)

        outcome = food_memory.remember_correction(
            bot_user, dish="борщ", field=food_memory.FIELD_NAME, value="борщ по-домашнему"
        )

        assert outcome is food_memory.Outcome.ERROR

    def test_unusable_dish_or_value_is_dropped_without_a_write(self, settings, resolver) -> None:
        bot_user = _consented_user("drf1454-16", settings)

        assert (
            food_memory.remember_correction(
                bot_user, dish="", field=food_memory.FIELD_NAME, value="борщ по-домашнему"
            )
            is food_memory.Outcome.UNPARSED
        )
        # An unknown field is not ours to keep either — the ownership gate is
        # the first one, so it answers before the key is even built.
        assert (
            food_memory.remember_correction(bot_user, dish="борщ", field="calories", value=500)
            is food_memory.Outcome.NOT_REMEMBERED
        )
        assert MemoryEntry.objects.count() == 0

    def test_unlinked_reader_returns_empty_without_minting_identity(self, settings) -> None:
        settings.STRICT_TENANT_SCOPE = "strict"
        bot_user = resolve_or_create_global_bot_user(channel="max", channel_user_id="drf1454-17")

        # No resolver stub installed: any resolve attempt would hit the network.
        assert food_memory.recall_corrections(bot_user, dish="борщ").is_empty()
        bot_user.refresh_from_db()
        assert bot_user.ayla_user_id is None


# ─── прозрачность: показать и забыть ──────────────────────────────────────


class TestTheRowIsVisibleToThePerson:
    """The silent-remember ruling (2026-08-23) lets the bot store without asking
    only because the person can see and forget what was stored. A row that is
    unrenderable is invisible — and a row we had no right to write."""

    def test_a_stored_correction_renders_in_the_memory_list(self, settings, resolver) -> None:
        from apps.persona.memory_commands import render_memory_summary

        bot_user = _consented_user("drf1454-vis-1", settings)
        food_memory.remember_correction(
            bot_user, dish="Борщ", field=food_memory.FIELD_NAME, value="борщ по-домашнему"
        )
        bot_user.refresh_from_db()

        summary = render_memory_summary(bot_user, user_id=resolver["uuid"])

        assert "блюдо «Борщ» называет «борщ по-домашнему»" in summary

    def test_the_list_keeps_the_persons_spelling(self, settings, resolver) -> None:
        """Мелкая находка ревью: в чате «Куриная грудка», в списке памяти
        «куриная грудка» — написание расходилось. Ключ нормализован, а
        написание — человека."""
        bot_user = _consented_user("drf1454-case", settings)
        food_memory.remember_correction(
            bot_user, dish="Куриная грудка", field=food_memory.FIELD_NAME, value="грудка на гриле"
        )
        bot_user.refresh_from_db()

        entry = MemoryEntry.objects.get(user_id=resolver["uuid"])
        assert "Куриная грудка" in entry.content["display"]
        assert entry.content["key"] == "food_dish_name:куриная грудка"

    def test_the_stored_field_has_a_phrase_not_raw_json(self, settings, resolver) -> None:
        """Одно поле — одна фраза (см. REMEMBERED_FIELDS).

        Раньше тест перебирал три поля; вес и БЖУ больше не пишутся, так что
        перебирать нечего — рендерится ровно то, что хранится.
        """
        from apps.persona.memory_surface import describe_green_content

        bot_user = _consented_user("drf1454-vis-name", settings)
        food_memory.remember_correction(
            bot_user, dish="борщ", field=food_memory.FIELD_NAME, value="плов"
        )
        bot_user.refresh_from_db()

        entry = MemoryEntry.objects.get(user_id=resolver["uuid"])
        assert describe_green_content(entry.content) == "блюдо «борщ» называет «плов»"

    def test_forget_all_takes_the_food_rows_with_it(self, settings, resolver) -> None:
        """The one erase verb that DOES reach these rows today (152-ФЗ)."""
        from apps.identity.services.forget_all_sweep import sweep_forget_all

        bot_user = _consented_user("drf1454-vis-2", settings)
        food_memory.remember_correction(
            bot_user, dish="борщ", field=food_memory.FIELD_NAME, value="борщ по-домашнему"
        )
        bot_user.refresh_from_db()
        assert _green_rows(resolver["uuid"]) == 1

        upc = get_or_create_personal_context(resolver["uuid"])
        upc.forget_all_requested_at = timezone.now()
        upc.save(update_fields=["forget_all_requested_at"])
        sweep_forget_all(resolver["uuid"])

        assert _green_rows(resolver["uuid"]) == 0
        assert food_memory.recall_corrections(bot_user, dish="борщ").is_empty()


# ─── ревью DRF-1454: регрессии ────────────────────────────────────────────


class TestReturningToAnEarlierValue:
    """A → Б → A. The dedup used to compare against every row ever written,
    including the dead one it had just superseded: the third turn answered
    «Запомнила» and left Б as the value the next card would print. A DUPLICATE
    verdict must mean «this is already what we would tell you», never «we once
    heard this»."""

    def test_a_person_can_go_back_to_a_value_they_had_before(self, settings, resolver) -> None:
        bot_user = _consented_user("drf1454-back-1", settings)
        for name in ("борщ по-домашнему", "борщ постный"):
            food_memory.remember_correction(
                bot_user, dish="борщ", field=food_memory.FIELD_NAME, value=name
            )
            bot_user.refresh_from_db()
        assert food_memory.recall_corrections(bot_user, dish="борщ").dish_name == "борщ постный"

        outcome = food_memory.remember_correction(
            bot_user, dish="борщ", field=food_memory.FIELD_NAME, value="борщ по-домашнему"
        )

        assert outcome is food_memory.Outcome.WRITTEN
        recall = food_memory.recall_corrections(bot_user, dish="борщ")
        assert recall.dish_name == "борщ по-домашнему"

    def test_duplicate_is_still_reported_for_the_current_value(self, settings, resolver) -> None:
        bot_user = _consented_user("drf1454-back-2", settings)
        food_memory.remember_correction(
            bot_user, dish="борщ", field=food_memory.FIELD_NAME, value="борщ по-домашнему"
        )
        bot_user.refresh_from_db()

        assert (
            food_memory.remember_correction(
                bot_user, dish="борщ", field=food_memory.FIELD_NAME, value="борщ по-домашнему"
            )
            is food_memory.Outcome.DUPLICATE
        )


class TestTheStoreStaysBounded:
    """The same argument that keeps «что ел» out of the store, applied to the
    store: an unbounded dish namespace in a zone with no TTL slowly becomes the
    nutrition profile this module refused to build."""

    def test_a_new_dish_past_the_cap_is_refused_and_the_old_ones_survive(
        self, settings, resolver, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(food_memory, "_MAX_DISHES", 2, raising=True)
        bot_user = _consented_user("drf1454-cap", settings)
        for dish in ("борщ", "плов"):
            food_memory.remember_correction(
                bot_user, dish=dish, field=food_memory.FIELD_NAME, value=f"{dish} по-домашнему"
            )
            bot_user.refresh_from_db()

        outcome = food_memory.remember_correction(
            bot_user, dish="окрошка", field=food_memory.FIELD_NAME, value="окрошка на квасе"
        )

        assert outcome is food_memory.Outcome.CAP_REACHED
        assert _green_rows(resolver["uuid"]) == 2
        # Refusal, not eviction: what was remembered is still remembered.
        recall = food_memory.recall_corrections(bot_user, dish="борщ")
        assert recall.dish_name == "борщ по-домашнему"

    def test_the_cap_never_blocks_a_dish_already_remembered(
        self, settings, resolver, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(food_memory, "_MAX_DISHES", 1, raising=True)
        bot_user = _consented_user("drf1454-cap-2", settings)
        food_memory.remember_correction(
            bot_user, dish="борщ", field=food_memory.FIELD_NAME, value="борщ по-домашнему"
        )
        bot_user.refresh_from_db()

        outcome = food_memory.remember_correction(
            bot_user, dish="борщ", field=food_memory.FIELD_NAME, value="борщ постный"
        )

        assert outcome is food_memory.Outcome.WRITTEN
        assert food_memory.recall_corrections(bot_user, dish="борщ").dish_name == "борщ постный"


class TestRollbackSwitch:
    def test_flag_off_writes_nothing_and_recalls_nothing(self, settings, resolver) -> None:
        bot_user = _consented_user("drf1454-flag", settings)
        # Presence first: with the switch ON this exact call stores and reads back.
        assert (
            food_memory.remember_correction(
                bot_user, dish="борщ", field=food_memory.FIELD_NAME, value="борщ по-домашнему"
            )
            is food_memory.Outcome.WRITTEN
        )
        bot_user.refresh_from_db()
        recall = food_memory.recall_corrections(bot_user, dish="борщ")
        assert recall.dish_name == "борщ по-домашнему"

        settings.FOOD_SCANNER_MEMORY_ENABLED = False

        assert (
            food_memory.remember_correction(
                bot_user, dish="плов", field=food_memory.FIELD_NAME, value="плов с бараниной"
            )
            is food_memory.Outcome.DISABLED
        )
        assert _green_rows(resolver["uuid"]) == 1  # nothing new
        assert food_memory.recall_corrections(bot_user, dish="борщ").is_empty()


class TestTheConciergePromptIsNotAFoodSurface:
    """Ревью DRF-1454, две оси независимо + дыра в откате: накопленные строки
    продолжали рендериться в системный промпт консьержа и после выключения
    флага — «False restores the pre-DRF-1454 behaviour exactly» было неверно
    для накопленных данных."""

    def test_accumulated_rows_never_reach_the_prompt_even_with_the_flag_off(
        self, settings, resolver
    ) -> None:
        from apps.persona.memory_surface import render_current_personal_context

        bot_user = _consented_user("drf1454-prompt", settings)
        food_memory.remember_correction(
            bot_user, dish="Борщ", field=food_memory.FIELD_NAME, value="борщ по-домашнему"
        )
        bot_user.refresh_from_db()

        settings.FOOD_SCANNER_MEMORY_ENABLED = False

        block = render_current_personal_context(resolver["uuid"])
        assert block is None or "борщ" not in block.lower()


class TestProvenanceDictionaryDoesNotDrift:
    def test_the_stated_dictionary_matches_the_library(self) -> None:
        """The fallback exists only until the ayla-ai-core pin carries the name.

        When the bump lands this test starts comparing against the library and
        fails if the two ever disagree — which is what makes the transitional
        fallback safe to keep until then, and obvious to delete after.
        """
        try:
            from ayla_ai_core import STATED_SOURCES as LIBRARY
        except ImportError:
            pytest.skip("pinned ayla-ai-core predates STATED_SOURCES (see food.py)")

        assert food_memory.STATED_SOURCES == LIBRARY

    def test_the_medical_perimeter_agrees_with_the_green_extractor(self) -> None:
        """Same 152-ФЗ ст. 10 stems in two apps — drift means a term that is red
        on one path and green on the other (DRF-1290)."""
        from apps.persona.memory_extract import _ALLERGY_RE

        for text in ("у меня аллергия на орехи", "непереносимость лактозы"):
            assert _ALLERGY_RE.search(text)
            assert food_memory.classify_refusal(text) == MemoryEntry.SENSITIVITY_RED
