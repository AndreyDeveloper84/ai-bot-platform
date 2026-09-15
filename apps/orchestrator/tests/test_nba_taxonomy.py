"""Таксономия выбора NBA (DRF-1932): коды, пустые словари, признаки — данные, не код.

* коды H5/B9 байт в байт с решением владельца и именами констант каталога —
  ``TestCodesAreTheOwnersBytes``;
* боевые словари J1/J2 — ровно утверждённый список владельца (DRF-1945) —
  ``TestProductionDictionariesAreTheOwnerList``;
* неизвестное значение — отказ, не выдумка; сочетания не ограничены (I1 а) —
  ``TestValidation``;
* признаки боли (I2) и health-контекста (п.6) — ``TestSignals``.
"""

from __future__ import annotations

import pytest

from apps.orchestrator import nba_taxonomy as tx


class TestCodesAreTheOwnersBytes:
    # Пара с beautygo_backend/recommendation/tests/test_pilot_taxonomy.py
    # ::TestCodesAreTheOwnersBytes (#468) — менять вместе.
    def test_targets_h5(self):
        # recommendation.models.Target (каталог, DRF-1922)
        assert tx.TARGETS == ("FACE_FRESHNESS", "PUFFINESS_REDUCTION", "RELAXATION", "BACK_COMFORT")

    def test_action_types_h5(self):
        # recommendation.models.ActionType (каталог, DRF-1922)
        assert tx.ACTION_TYPES == ("PROVIDER_SESSION", "SELF_CARE", "OBSERVE", "PLAN")

    def test_families_b9(self):
        # Recommendation.Family (каталог, B9)
        assert tx.FAMILIES == ("ADDRESS", "SUPPORT", "RECOVER", "OBSERVE")

    def test_roles_are_the_catalog_spelling(self):
        # Recommendation.Role (каталог): primary | alternative
        assert (tx.ROLE_PRIMARY, tx.ROLE_ALTERNATIVE) == ("primary", "alternative")

    def test_version_label_fits_the_catalog_and_names_the_owner_ruling(self):
        assert tx.TAXONOMY_VERSION == "h5-j1j2:owner-2026-09-15"
        assert len(tx.TAXONOMY_VERSION) <= 32


#: J2 владельца (§4) — дословно.
OWNER_J2 = {
    "FACE_FRESHNESS": ("ADDRESS", "PROVIDER_SESSION"),
    "PUFFINESS_REDUCTION": ("ADDRESS", "PROVIDER_SESSION"),
    "RELAXATION": ("SUPPORT", "PROVIDER_SESSION"),
    "BACK_COMFORT": ("RECOVER", "PROVIDER_SESSION"),
}

#: J1 владельца (§3) — дословно, только явный список. «Близкие косметические
#: формулировки» к FACE_FRESHNESS: пусто, ждёт списка владельца (главное окно 15.09).
OWNER_J1 = {
    "хочу выглядеть свежее": "FACE_FRESHNESS",
    "хочу снять напряжение": "RELAXATION",
    "хочу расслабить спину": "BACK_COMFORT",
    "спина напряжена": "BACK_COMFORT",
    "хочу снять зажимы": "BACK_COMFORT",
    "устала спина после работы": "BACK_COMFORT",
}


class TestProductionDictionariesAreTheOwnerList:
    """DRF-1945: боевой словарь = утверждённый список, строкой. Любая фраза сверх
    списка или правка умолчания краснит здесь — вместе со сменой TAXONOMY_VERSION."""

    def test_phrase_map_is_exactly_the_owner_j1(self):
        assert dict(tx.TARGET_PHRASES) == OWNER_J1

    def test_defaults_are_exactly_the_owner_j2(self):
        assert dict(tx.TARGET_DEFAULTS) == OWNER_J2


class TestOwnerPhrasesJ1:
    @pytest.mark.parametrize("phrase, target", sorted(OWNER_J1.items()))
    def test_each_owner_phrase_gives_its_target(self, phrase, target):
        assert tx.read_turn_needs(phrase.capitalize()).recognized_targets == (target,)


class TestValidation:
    def test_unknown_target_in_a_phrase_map_is_refused(self):
        with pytest.raises(tx.TaxonomyError):
            tx.read_turn_needs("хочу расслабиться", phrases={"расслабиться": "CALM"})

    @pytest.mark.parametrize(
        "pair",
        [("CALM", "PLAN"), ("SUPPORT", "MASSAGE"), ("support", "PLAN"), ("SUPPORT",)],
    )
    def test_unknown_family_or_action_type_is_refused(self, pair):
        with pytest.raises(tx.TaxonomyError):
            tx.triples_for(("RELAXATION",), defaults={"RELAXATION": pair})

    def test_a_phrase_without_words_is_refused(self):
        with pytest.raises(tx.TaxonomyError):
            tx.validate({" — ": "RELAXATION"}, {})

    @pytest.mark.parametrize("family", tx.FAMILIES)
    @pytest.mark.parametrize("action_type", tx.ACTION_TYPES)
    def test_every_combination_of_valid_codes_is_accepted(self, family, action_type):
        """I1 (а): таблицы сочетаний нет; OBSERVE × OBSERVE не связаны и не запрещены."""
        (triple,) = tx.triples_for(
            ("BACK_COMFORT",), defaults={"BACK_COMFORT": (family, action_type)}
        )
        assert triple == tx.Triple("BACK_COMFORT", family, action_type)

    def test_record_projection_uses_the_catalog_field_names(self):
        triple = tx.Triple("RELAXATION", "SUPPORT", "PROVIDER_SESSION")
        assert triple.as_record(tx.ROLE_PRIMARY) == {
            "role": "primary",
            "target": "RELAXATION",
            "family": "SUPPORT",
            "action_type": "PROVIDER_SESSION",
        }


class TestSignals:
    @pytest.mark.parametrize(
        "text",
        [
            "ноет спина",
            "болит спина после работы",
            "простреливает в пояснице",
            "онемение в руке",
            "отдаёт в ногу",
            "слабость и хочу массаж",
        ],
    )
    def test_owner_pain_words_are_a_signal(self, text):
        assert tx.read_turn_needs(text).pain_signal is True

    @pytest.mark.parametrize(
        "text",
        [
            "хочу расслабить спину",
            "напряжение в спине",
            "хочу снять зажимы",
            "устала спина после работы",
        ],
    )
    def test_owner_examples_without_pain_are_not_a_signal(self, text):
        needs = tx.read_turn_needs(text)
        assert needs.pain_signal is False
        assert needs.health_context is False

    @pytest.mark.parametrize("text", ["у меня отёки", "Меня беспокоят отёки", "Беспокоят отёки"])
    def test_swelling_said_about_oneself_is_health_context_without_a_target(self, text):
        needs = tx.read_turn_needs(text, phrases={"отеки": "PUFFINESS_REDUCTION"})
        assert needs.health_context is True
        assert needs.recognized_targets == ()

    def test_a_service_name_with_swelling_is_not_health_evidence(self):
        """Пакет 3 п.6: «снятие отёков» само по себе не заявленный симптом."""
        needs = tx.read_turn_needs("запишите на снятие отёков")
        assert needs.health_context is False
        assert needs.pain_signal is False

    def test_several_targets_come_in_h5_order_not_phrase_order(self):
        phrases = {"спину": "BACK_COMFORT", "свежее": "FACE_FRESHNESS"}
        needs = tx.read_turn_needs("хочу расслабить спину и выглядеть свежее", phrases=phrases)
        assert needs.recognized_targets == ("FACE_FRESHNESS", "BACK_COMFORT")


# --------------------------------------------------------------------------- #
# DRF-1945 — боевые словари J1/J2 по решению владельца 15.09                   #
# (docs/PROMPT_ORCHESTRATOR_AYLA_CONTROLLED_PILOT_NEXT_WAVE.md §3–§4)          #
# --------------------------------------------------------------------------- #

#: §3 NO TARGET: тексты кнопок первого хода (``apps/channels/max/quick_actions.py``)
#: и краткие формы владельца.
NO_TARGET_PHRASES = (
    "Беспокоят отёки",
    "Последнее время сильно устаю",
    "Хочу больше времени уделять себе",
    "Готовлюсь к важному событию",
    "Сильно устаю",
    "Время себе",
    "Важное событие",
)


class TestOwnerDefaultsJ2:
    @pytest.mark.parametrize("target", tx.TARGETS)
    def test_each_target_gets_the_owner_triple(self, target):
        (triple,) = tx.triples_for((target,))
        assert triple == tx.Triple(target, *OWNER_J2[target])


class TestOwnerNoTargetPhrases:
    @pytest.mark.parametrize("text", NO_TARGET_PHRASES)
    def test_no_target_is_assigned(self, text):
        assert tx.words(text), text
        # empty-assert-ok: отсутствие цели — предмет теста (§3 NO TARGET)
        assert tx.read_turn_needs(text).recognized_targets == ()

    def test_no_owner_phrase_reaches_puffiness(self):
        """J: PUFFINESS_REDUCTION на пилоте из утверждённых фраз недостижима — это честно."""
        assert tx.TARGET_PHRASES, "словарь J1 пуст"
        assert "PUFFINESS_REDUCTION" not in set(tx.TARGET_PHRASES.values())
