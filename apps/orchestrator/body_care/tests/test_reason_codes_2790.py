"""BOT-9 (DRF-2790) / BOT-9b (DRF-2800) — реестр body-care ``reason_codes``.

Набор сверяется ЛИТЕРАЛАМИ контракта §21 (v0.1 — двадцать, v0.2 — плюс
шесть), а не самим перечислением: узел, построенный из константы, не поймает
смену константы.
"""

from __future__ import annotations

#: Контракт ``AYLA_BODY_CARE_RUNTIME_CONTRACT_v0.1`` §21, дословно и по порядку.
CONTRACT_V01_SECTION_21 = (
    "OPEN_WOUND",
    "SUNBURN",
    "ACTIVE_RASH",
    "ACTIVE_IRRITATION",
    "PRODUCT_REACTION_HISTORY",
    "KNOWN_ALLERGY",
    "RESPIRATORY_REACTION_HISTORY",
    "CURRENT_RESPIRATORY_EMERGENCY",
    "MEDICATION_PROTOCOL_REVIEW",
    "PREVIOUS_PROCEDURE_RESTRICTION",
    "PREGNANCY_PROTOCOL_REVIEW",
    "LACTATION_PROTOCOL_REVIEW",
    "CONFIGURATION_INCOMPLETE",
    "SOURCE_CONFLICT",
    "SPA_TRANSITION_UNAPPROVED",
    "AC_CLASS_UNKNOWN",
    "AC_NOT_IN_INITIAL_CANON",
    "ADVERSE_REACTION_R1",
    "ADVERSE_REACTION_R2",
    "S1_ESCALATION",
)

#: Префиксы каталожного реестра рекомендаций (beautygo_backend
#: ``recommendation/_reason_codes.py`` ``OWNED_PREFIXES``). Каталог из бота не
#: импортируется — список повторён здесь; пересечение означало бы, что два
#: реестра независимо версионируют одно имя.
CATALOG_RECOMMENDATION_PREFIXES = (
    "SCOPE_",
    "ELIG_",
    "MATCH_",
    "EXEC_",
    "CONTEXT_",
    "QUALITY_",
    "TIE_",
)


#: Контракт v0.2 §21 — шесть кодов, добавленных к v0.1, дословно и по порядку.
CONTRACT_V02_ADDED = (
    "LEGAL_CLASSIFICATION_REQUIRED",
    "MEDICAL_LICENSE_NOT_VERIFIED",
    "LICENSE_SCOPE_MISMATCH",
    "LICENSE_ADDRESS_MISMATCH",
    "PRACTITIONER_QUALIFICATION_NOT_VERIFIED",
    "MEDICAL_AD_CLAIM_REVIEW_REQUIRED",
)


def test_r1_exactly_the_twenty_six_codes_of_section_21_v02():
    from apps.orchestrator.body_care.reason_codes import ALL_CODES, ReasonCode

    expected = CONTRACT_V01_SECTION_21 + CONTRACT_V02_ADDED
    assert tuple(code.value for code in ReasonCode) == expected
    assert ALL_CODES == frozenset(expected)
    assert len(ALL_CODES) == 26


def test_r1b_the_first_twenty_are_v01_unchanged():
    """BOT-9b добавляет, не правит: первые двадцать — ровно v0.1, по порядку."""

    from apps.orchestrator.body_care.reason_codes import ReasonCode

    assert tuple(code.value for code in ReasonCode)[:20] == CONTRACT_V01_SECTION_21


def test_r2_value_equals_name():
    from apps.orchestrator.body_care.reason_codes import ReasonCode

    for code in ReasonCode:
        assert code.value == code.name


def test_r3_the_registry_is_versioned():
    from apps.orchestrator.body_care.reason_codes import REGISTRY_VERSION

    assert REGISTRY_VERSION == "0.2.0"


def test_r4_no_overlap_with_the_bots_decision_readiness_registry():
    from apps.orchestrator.body_care.reason_codes import ALL_CODES
    from apps.orchestrator.decision_readiness import reason_codes as track_a

    assert track_a.ALL_CODES  # положительный контроль: сравниваем с непустым
    assert ALL_CODES.isdisjoint(track_a.ALL_CODES)
    track_a_prefixes = tuple({code.split("_", 1)[0] + "_" for code in track_a.ALL_CODES})
    # Положительный контроль предиката: собственный код трека A им ловится.
    assert "STATE_READY".startswith(track_a_prefixes)
    for code in ALL_CODES:
        # empty-assert-ok: проверка непересечения; предикат проверен строкой выше
        assert not code.startswith(track_a_prefixes), code


def test_r5_no_overlap_with_the_catalog_recommendation_prefixes():
    from apps.orchestrator.body_care.reason_codes import ALL_CODES

    assert len(ALL_CODES) == 26  # присутствие: проверяем непустой реестр
    # Положительный контроль предиката: код каталожного реестра им ловится.
    assert "ELIG_SAFETY_CLEARED".startswith(CATALOG_RECOMMENDATION_PREFIXES)
    for code in ALL_CODES:
        # empty-assert-ok: проверка непересечения; предикат проверен строкой выше
        assert not code.startswith(CATALOG_RECOMMENDATION_PREFIXES), code


def test_r6_the_v02_legal_codes_came_with_version_020():
    """Шесть кодов v0.2 §21 пришли вместе с версией 0.2.0, не тихой правкой."""

    from apps.orchestrator.body_care.reason_codes import ALL_CODES, REGISTRY_VERSION

    assert set(CONTRACT_V02_ADDED) <= ALL_CODES
    assert REGISTRY_VERSION == "0.2.0"
