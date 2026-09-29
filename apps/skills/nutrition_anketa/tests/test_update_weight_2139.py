"""«Обнови вес» — короткий путь без полной анкеты (DRF-2139, Анкета-2, В3).

Пересчёт ориентира был только полной анкетой заново (семь вопросов).
Каталог пересчитывает на каждом ``upsert_profile``, входы лежат в
``targets_input_snapshot`` — значит, один новый вес плюс прежние входы дают
новое предложение, а действующим оно становится тем же подтверждением
(§5.1). Стоп-состояния не пересматриваются: возраст и скрининг заново не
спрашиваются; снимок старше 365 дней — анкета заново (возраст мог смениться).

* w1 — узел: профиль ``ayla_calculated`` (снимок: вес 70) → «мой вес 65» →
  один POST, тело — РОВНО шесть полей из снимка с новым весом + утверждение
  согласия M → карточка «Предлагаю ориентиры… вес — 65 кг» с кнопкой
  подтверждения → подтвердить → ``confirm_targets``;
* w2 — без анкеты (профиля нет / снимок пуст / источник ``none``) → «Сначала
  пройдём анкету — так я посчитаю точно» + кнопка анкеты; POST нет;
* w3 — ``user_entered`` → вес пишется (каталог #525, DRF-2193), ориентир
  человека остаётся; тело — только вес и утверждение. До #525 здесь стояло
  отступление «POST нет»: каталог не умел записать вес без пересчёта;
* w4 — ложный вход: «вес 20» / «вес 400» → «проверь число», POST нет;
  «68,5» — переспрос целым числом;
* w5 — «обнови вес» / кнопка → вопрос веса → «68» → карточка;
* w6 — снимок старше 365 дней → «давно не обновляли анкету — пройдём заново»
  + кнопка анкеты; POST нет; дата — из ``raw.targets_provenance.computed_at``,
  без даты — не проверяется (предел, назван);
* w7 — нет утверждения согласия M → отказ по имени (как у анкеты), POST нет;
* w8 — чип «Обновить вес» в пост-анкетных чипах при ``ayla_calculated`` /
  ``ayla_proposed``; при ``none`` / ``user_entered`` его нет;
* w9 — не наше: «68», «вес», «весна пришла», «вес ребёнка 20 кг», «мой вес
  был 90 в прошлом году»; наше: «мой вес 65», «вешу 65», «обнови вес»;
* w10 — каталог недоступен → «сервис не отвечает», состояние снято;
* w11 — в снимке нет активности / роста → анкета заново, ничего не
  подставляется (нет числа за человека).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import Mock, patch

import pytest

from apps.consent.personal_calculation import (
    NOT_GRANTED,
    ConsentAttestation,
    ConsentAttestationUnavailable,
)
from apps.integrations.ayla.nutrition_client import NutritionUnavailableError
from apps.skills.base import SkillContext
from apps.skills.nutrition_anketa.skill import (
    CB_CONFIRM_TARGETS,
    UPDATE_WEIGHT_BUTTON,
    UPDATE_WEIGHT_CALLBACK,
    UPDATE_WEIGHT_STATE_KEY,
    NutritionAnketaSkill,
    _post_anketa_chips,
)
from apps.skills.nutrition_anketa.tests.test_skill import (
    _ATTESTATION_LOOKUP,
    _profile,
    _StatefulConversation,
)

_ATTESTATION = ConsentAttestation(
    type="personal_calculation", document_version="personal-calculation-v1"
)
_PD_OPEN = "apps.orchestrator.personal_surface.personal_records_consent_open"

_SNAPSHOT = {
    "gender": "female",
    "age": 28,
    "height_cm": 168,
    "weight_kg": 70,
    "activity_coefficient": 1.375,
    "goal": "maintain",
}


def _calculated(
    *,
    computed_at: datetime | None = None,
    snapshot: dict | None = _SNAPSHOT,
    source: str = "ayla_calculated",
):
    computed = computed_at or datetime.now(UTC) - timedelta(days=30)
    raw = {
        "targets_provenance": {
            "source": source,
            "computed_at": computed.isoformat(),
            "input_snapshot": dict(snapshot or {}),
        }
    }
    snap = dict(snapshot or {})
    return replace(
        _profile(),
        targets_source=source,
        targets_input_snapshot=snap,
        # Названные цель и темп — поля профиля (вопрос 59): до ступени пола
        # BMR они совпадают со снимком.
        goal=str(snap.get("goal") or ""),
        goal_pace=str(snap.get("pace") or ""),
        raw=raw,
    )


def _catalog_after_2192(weight: int, *, acting_kcal: int = 1800):
    """Ответ каталога на пересчёт ПОДТВЕРЖДЁННОГО расчёта — форма после #525.

    Один источник для мока: до этой правки мок отдавал старую форму
    (``ayla_proposed`` на месте), и w1 был зелёным, пока каталог после #525
    отвечал уже иначе — мок пинил контракт, которого больше не было.
    """
    return replace(
        _profile(),
        weight_kg=weight,
        daily_kcal=acting_kcal,
        targets_source="ayla_calculated",
        targets_input_snapshot=dict(_SNAPSHOT),
        targets_method_versions={"calories": "mifflin_st_jeor_v2"},
        raw={
            "norms": {"daily_kcal": acting_kcal},
            "targets_provenance": {
                "source": "ayla_calculated",
                "input_snapshot": dict(_SNAPSHOT),
                "pending_proposal": {
                    "kinds": ["calories"],
                    "daily_kcal": 1700,
                    "daily_protein_g": 110,
                    "daily_fat_g": 60,
                    "daily_carbs_g": 180,
                    "input_snapshot": {**_SNAPSHOT, "weight_kg": weight},
                    "method_versions": {"calories": "mifflin_st_jeor_v2"},
                    "computed_at": "2026-09-21T09:00:00.000Z",
                    "goal": "maintain",
                    "pace": "moderate",
                    "goal_overridden_by": None,
                    "overrides_applied": [],
                },
            },
        },
    )


def _catalog_answer(profile: Any, weight: int):
    """Что каталог (после #525) отвечает на вес — по источнику до POST."""
    if getattr(profile, "targets_source", "") == "ayla_calculated":
        return _catalog_after_2192(weight)
    if getattr(profile, "targets_source", "") == "user_entered":
        return replace(profile, weight_kg=weight)
    return _proposed(weight)


def _proposed(weight: int):
    return replace(
        _profile(),
        weight_kg=weight,
        targets_source="ayla_proposed",
        targets_input_snapshot={**_SNAPSHOT, "weight_kg": weight},
        targets_method_versions={"calories": "mifflin_st_jeor_v2"},
        raw={"norms": {"daily_kcal": 1700}},
    )


class _Run:
    def __init__(
        self,
        state: dict | None = None,
        *,
        profile: Any = None,
        upsert: Any = None,
        attestation: Any = _ATTESTATION,
    ) -> None:
        self.conversation = _StatefulConversation(state)
        self.posted: list[dict] = []
        self.confirmed = 0
        self.attestation = attestation
        client = Mock()

        async def _get_profile(**kwargs):
            return profile

        async def _upsert(**kwargs):
            self.posted.append(kwargs)
            if isinstance(upsert, Exception):
                raise upsert
            if upsert is not None:
                return upsert
            return _catalog_answer(profile, kwargs["data"]["weight_kg"])

        async def _confirm(**kwargs):
            self.confirmed += 1
            return replace(_proposed(65), targets_source="ayla_calculated"), "confirmed"

        client.get_profile = _get_profile
        client.upsert_profile = _upsert
        client.confirm_targets = _confirm
        self._client = client
        self.skill = NutritionAnketaSkill()

    def ctx(self, text: str) -> SkillContext:
        return SkillContext(
            conversation=self.conversation,  # type: ignore[arg-type]
            bot_user=Mock(channel="max", channel_user_id="12345"),
            message_text=text,
        )

    def matches(self, text: str) -> bool:
        return self.skill.matches(self.ctx(text))

    def turn(self, text: str):
        lookup = (
            Mock(side_effect=self.attestation)
            if isinstance(self.attestation, Exception)
            else Mock(return_value=self.attestation)
        )
        with (
            patch(
                "apps.skills.nutrition_anketa.skill.get_nutrition_client",
                return_value=self._client,
            ),
            patch(_ATTESTATION_LOOKUP, lookup),
            patch(_PD_OPEN, return_value=True),
        ):
            return self.skill.handle(self.ctx(text))

    @property
    def state(self) -> dict | None:
        bucket = self.conversation.skill_state.get(UPDATE_WEIGHT_STATE_KEY)
        if bucket is None:
            return None
        return {k: v for k, v in bucket.items() if k != "asked_at"}


def _labels(result) -> list[str]:
    return [b["label"] for b in (result.action_data or {}).get("buttons") or []]


def _callbacks(result) -> list[str]:
    return [b["callback"] for b in (result.action_data or {}).get("buttons") or []]


class TestW1SeventyToSixtyFive:
    def test_the_whole_path(self) -> None:
        run = _Run(profile=_calculated())
        card = run.turn("мой вес 65")
        assert run.posted == [
            {
                "external_user_id": "bot:max:12345",
                "data": {
                    "gender": "female",
                    "age": 28,
                    "height_cm": 168,
                    "weight_kg": 65,
                    "goal": "maintain",
                    "activity_coefficient": 1.375,
                    "consent": {
                        "type": "personal_calculation",
                        "document_version": "personal-calculation-v1",
                    },
                },
            }
        ]
        assert card.meta["reply_kind"] == "anketa_update_weight_proposed"
        assert "Предлагаю ориентиры" in card.reply_text
        assert "вес — 65 кг" in card.reply_text
        assert CB_CONFIRM_TARGETS in _callbacks(card)
        assert run.state is None
        # Подтверждение — тем же путём, что у анкеты.
        done = run.turn(CB_CONFIRM_TARGETS)
        assert run.confirmed == 1
        assert done.meta["reply_kind"] == "anketa_targets_confirmed"


class TestW2WithoutAnAnketa:
    @pytest.mark.parametrize(
        "profile",
        [None, _calculated(snapshot={}), _calculated(source="none", snapshot={})],
        ids=["no-profile", "empty-snapshot", "source-none"],
    )
    def test_go_to_the_anketa_first(self, profile: Any) -> None:
        run = _Run(profile=profile)
        result = run.turn("мой вес 65")
        assert result.reply_text.startswith("Сначала пройдём анкету — так я посчитаю точно")
        assert "/anketa" in _callbacks(result)
        assert run.posted == []
        assert run.state is None


class TestW3UserEntered:
    def test_weight_is_written_and_the_target_stays(self) -> None:
        """До #525 тест пинил «POST нет» — отступление, которое каталог снял."""
        run = _Run(profile=_calculated(source="user_entered", snapshot={}))
        result = run.turn("мой вес 65")
        assert "ориентир от специалиста" in result.reply_text.lower()
        assert "остаётся прежним" in result.reply_text
        assert [p["data"]["weight_kg"] for p in run.posted] == [65]
        assert result.meta["reply_kind"] == "anketa_update_weight_manual_saved"


class TestW4CheckTheNumber:
    @pytest.mark.parametrize("text", ["вес 20", "мой вес 400", "вешу 29", "вес 301"])
    def test_out_of_range_is_reasked(self, text: str) -> None:
        run = _Run(profile=_calculated())
        assert run.matches(text)
        result = run.turn(text)
        assert "проверь число" in result.reply_text.lower()
        assert run.posted == []
        assert run.state == {"step": "weight"}

    def test_decimal_is_reasked_as_a_whole_number(self) -> None:
        run = _Run(profile=_calculated())
        run.turn("обнови вес")
        result = run.turn("68,5")
        assert result.meta["reply_kind"] == "anketa_update_weight_invalid"
        assert run.posted == []


class TestW5OneQuestion:
    @pytest.mark.parametrize("entry", ["обнови вес", "Обновить вес", UPDATE_WEIGHT_CALLBACK])
    def test_ask_then_card(self, entry: str) -> None:
        run = _Run(profile=_calculated())
        asked = run.turn(entry)
        assert asked.reply_text == "Какой текущий вес в килограммах?"
        assert run.state == {"step": "weight"}
        assert run.posted == []
        card = run.turn("68")
        assert run.posted[0]["data"]["weight_kg"] == 68
        assert "вес — 68 кг" in card.reply_text


class TestW6StaleSnapshot:
    def test_older_than_a_year_goes_to_the_anketa(self) -> None:
        run = _Run(profile=_calculated(computed_at=datetime.now(UTC) - timedelta(days=366)))
        result = run.turn("мой вес 65")
        assert result.reply_text.startswith("Давно не обновляли анкету — пройдём заново")
        assert "/anketa" in _callbacks(result)
        assert run.posted == []

    def test_a_year_minus_a_day_is_fresh(self) -> None:
        run = _Run(profile=_calculated(computed_at=datetime.now(UTC) - timedelta(days=364)))
        run.turn("мой вес 65")
        assert len(run.posted) == 1

    def test_no_date_is_not_checked(self) -> None:
        """Предел назван: без даты в ответе каталога проверки нет."""
        profile = _calculated()
        profile.raw["targets_provenance"].pop("computed_at")
        run = _Run(profile=profile)
        run.turn("мой вес 65")
        assert len(run.posted) == 1


class TestW7ConsentM:
    def test_no_attestation_refuses_by_name_and_sends_nothing(self) -> None:
        run = _Run(
            profile=_calculated(),
            attestation=ConsentAttestationUnavailable(NOT_GRANTED),
        )
        result = run.turn("мой вес 65")
        assert result.meta["reply_kind"] == "anketa_consent_required"
        assert run.posted == []


class TestW8TheChip:
    @pytest.mark.parametrize("source", ["ayla_calculated", "ayla_proposed"])
    def test_present_when_there_is_a_calculation(self, source: str) -> None:
        chips = _post_anketa_chips(replace(_profile(), targets_source=source))
        assert {"label": UPDATE_WEIGHT_BUTTON, "callback": UPDATE_WEIGHT_CALLBACK} in chips

    @pytest.mark.parametrize("source", ["none", "user_entered"])
    def test_absent_without_one(self, source: str) -> None:
        chips = _post_anketa_chips(replace(_profile(), targets_source=source))
        assert chips  # присутствие: чипы на месте
        assert UPDATE_WEIGHT_CALLBACK not in [c["callback"] for c in chips]

    def test_absent_without_a_profile(self) -> None:
        chips = _post_anketa_chips()
        assert chips
        assert UPDATE_WEIGHT_CALLBACK not in [c["callback"] for c in chips]


class TestW9Matcher:
    @pytest.mark.parametrize(
        "text",
        [
            "мой вес 65",
            "Вешу 65",
            "обнови вес",
            "вес 65 кг",
            "мой вес — 72",
            "я вешу 65",
            "сейчас вешу 65",
            "мой вес сейчас 65",
            "новый вес 65",
            "вес теперь 65",
        ],
    )
    def test_ours(self, text: str) -> None:
        assert _Run().matches(text)

    @pytest.mark.parametrize(
        "text",
        [
            "68",
            "вес",
            "весна пришла",
            "вес ребёнка 20 кг",
            "мой вес был 90 в прошлом году",
            "перевес багажа 65",
            "вес 65 и 70",
        ],
    )
    def test_not_ours(self, text: str) -> None:
        run = _Run()
        assert run.matches("мой вес 65")  # присутствие: матчер живой
        assert not run.matches(text)


class TestW10CatalogueDown:
    def test_says_so_and_clears(self) -> None:
        run = _Run(profile=_calculated(), upsert=NutritionUnavailableError("network"))
        result = run.turn("мой вес 65")
        assert result.meta["reply_kind"] == "anketa_ayla_down"
        assert run.state is None


class TestW11NoSubstitution:
    @pytest.mark.parametrize(
        "missing", ["activity_coefficient", "height_cm", "gender", "age", "goal"]
    )
    def test_a_snapshot_without_an_input_goes_to_the_anketa(self, missing: str) -> None:
        snapshot = {k: v for k, v in _SNAPSHOT.items() if k != missing}
        run = _Run(profile=_calculated(snapshot=snapshot))
        result = run.turn("мой вес 65")
        assert result.reply_text.startswith("Сначала пройдём анкету")
        assert run.posted == []


# ─── ревью #1912 ──────────────────────────────────────────────────────────


class TestR1AnketaKeepsItsAnswer:
    def test_phrase_on_the_anketa_weight_step_is_the_steps_answer(self) -> None:
        """Блокер ревью: «вешу 65» посреди анкеты — ответ шагу, не короткий
        путь со СТАРЫМ снимком; POST нет, FSM жив."""
        run = _Run(profile=_calculated())
        run.turn("/anketa")
        run.turn("cb:anketa:choice:gender:female")
        run.turn("30")
        run.turn("cb:anketa:choice:screening:none")
        run.turn("170")
        result = run.turn("вешу 65")
        assert run.posted == []
        assert run.state is None
        # Анкета на месте: валидатор шага переспросил или принял — но это её ход.
        assert result.action_type.startswith("anketa_step_")
        assert run.conversation.skill_state["nutrition_anketa"]["current_step"] in (
            "weight",
            "activity",
        )


class TestR2OpenQuestionsClearEachOther:
    def test_weight_question_then_manual_phrase(self) -> None:
        from apps.skills.nutrition_anketa.skill import MANUAL_STATE_KEY

        run = _Run(profile=_calculated())
        run.turn("обнови вес")
        assert run.state == {"step": "weight"}
        card = run.turn("ориентир от специалиста 1800")
        assert card.meta["reply_kind"] == "anketa_manual_target_card"
        assert run.state is None
        assert MANUAL_STATE_KEY in run.conversation.skill_state

    def test_manual_card_then_weight_phrase(self) -> None:
        from apps.skills.nutrition_anketa.skill import MANUAL_STATE_KEY

        run = _Run(profile=_calculated())
        run.turn("ориентир от специалиста 1800")
        assert MANUAL_STATE_KEY in run.conversation.skill_state
        card = run.turn("мой вес 65")
        assert card.meta["reply_kind"] == "anketa_update_weight_proposed"
        assert MANUAL_STATE_KEY not in run.conversation.skill_state
        # «70» теперь ничьё, не поправка ккал.
        assert not run.matches("70")


class TestR3SnapshotTypes:
    def test_float_and_string_values_are_coerced(self) -> None:
        snapshot = {**_SNAPSHOT, "age": 28.0, "height_cm": "168", "activity_coefficient": "1.375"}
        run = _Run(profile=_calculated(snapshot=snapshot))
        run.turn("мой вес 65")
        body = run.posted[0]["data"]
        assert body["age"] == 28 and body["height_cm"] == 168
        assert body["activity_coefficient"] == 1.375 and isinstance(
            body["activity_coefficient"], float
        )

    def test_junk_in_the_snapshot_goes_to_the_anketa(self) -> None:
        run = _Run(profile=_calculated(snapshot={**_SNAPSHOT, "age": "двадцать"}))
        result = run.turn("мой вес 65")
        assert result.reply_text.startswith("Сначала пройдём анкету")
        assert run.posted == []


class TestR4Exits:
    @pytest.mark.parametrize("text", ["/anketa", "cb:anketa:start", "Рассчитать мои нормы"])
    def test_anketa_entries_drop_the_weight_question(self, text: str) -> None:
        run = _Run(profile=_calculated())
        run.turn("обнови вес")
        assert run.state == {"step": "weight"}
        result = run.turn(text)
        assert run.state is None
        assert result.action_type == "anketa_step_gender"

    def test_not_a_number_is_not_check_the_number(self) -> None:
        run = _Run(profile=_calculated())
        run.turn("обнови вес")
        result = run.turn("что я ел сегодня")
        assert result.meta["reply_kind"] == "anketa_update_weight_invalid"
        assert "проверь число" not in result.reply_text.lower()
