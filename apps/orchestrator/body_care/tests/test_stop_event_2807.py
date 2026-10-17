"""BOT-7 (DRF-2807) — стоп-событие, контракт v0.2 §15.

Список симптомов сверяется ЛИТЕРАЛАМИ контракта, не самим перечислением.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

T0 = datetime(2026, 10, 6, 15, 0, tzinfo=UTC)

#: v0.2 §15 «Candidate symptoms», дословно и по порядку.
CONTRACT_V02_SECTION_15 = (
    "PAIN",
    "BURNING",
    "STINGING",
    "SWELLING",
    "BLISTERING",
    "VISIBLE_INJURY",
    "NEW_REACTION",
    "RESPIRATORY_SYMPTOMS",
    "LOSS_OF_CONSCIOUSNESS",
)


def _event(**over):
    from apps.orchestrator.body_care.stop_event import StopEvent, Symptom

    kw = {
        "event_type": "client_reported",
        "severity": None,
        "symptoms": (Symptom.BURNING,),
        "offering_id": "offering-1",
        "component_id": None,
        "occurred_at": T0,
        "reported_by": "master",
    }
    kw.update(over)
    return StopEvent(**kw)


def test_s1_exactly_the_candidate_symptoms_of_section_15():
    from apps.orchestrator.body_care.stop_event import Symptom

    assert tuple(s.value for s in Symptom) == CONTRACT_V02_SECTION_15
    for symptom in Symptom:
        assert symptom.value == symptom.name


def test_s2_the_registry_is_versioned_as_a_candidate():
    from apps.orchestrator.body_care.stop_event import SYMPTOM_REGISTRY_VERSION

    assert SYMPTOM_REGISTRY_VERSION == "0.2.0-candidate"
    assert _event().as_record()["symptom_registry_version"] == "0.2.0-candidate"


def test_s3_symptoms_are_deduplicated_and_in_registry_order():
    from apps.orchestrator.body_care.stop_event import Symptom

    event = _event(symptoms=[Symptom.SWELLING, Symptom.PAIN, Symptom.SWELLING])

    assert event.symptoms == (Symptom.PAIN, Symptom.SWELLING)
    assert event == _event(symptoms=(Symptom.PAIN, Symptom.SWELLING))


def test_s4_unclassified_severity_is_not_mild():
    """Тяжесть ждёт D-4/D-5: None — «не оценено», и тип так и говорит."""

    unrated = _event(severity=None)
    rated = _event(severity="anything-the-policy-names")

    assert unrated.is_classified is False
    assert unrated.as_record()["severity"] is None
    assert rated.is_classified is True


def test_s5_event_type_and_severity_stay_open_strings():
    """Контракт значений не задаёт — любые непустые строки принимаются."""

    event = _event(event_type="whatever-policy-defines", severity="R-something")

    assert (event.event_type, event.severity) == ("whatever-policy-defines", "R-something")


@pytest.mark.parametrize(
    "over",
    [
        {"symptoms": ("PAIN",)},  # строка вместо члена реестра
        {"symptoms": "PAIN"},  # одна строка вместо последовательности
        {"event_type": ""},
        {"offering_id": " "},
        {"reported_by": ""},
        {"severity": ""},  # пустая — не «не оценено»; для этого None
        {"component_id": ""},
        {"occurred_at": datetime(2026, 10, 6, 15, 0)},  # без пояса
    ],
)
def test_s6_invariants_refuse(over):
    from apps.orchestrator.body_care.stop_event import StopEventError

    with pytest.raises(StopEventError):
        _event(**over)


def test_s7_component_is_for_spa_steps_only_and_optional():
    assert _event(component_id=None).component_id is None
    assert _event(component_id="spa-step-2").as_record()["component_id"] == "spa-step-2"


def test_s8_the_record_carries_every_field_of_section_15_in_order():
    assert list(_event().as_record()) == [
        "event_type",
        "severity",
        "symptoms",
        "offering_id",
        "component_id",
        "occurred_at",
        "reported_by",
        "symptom_registry_version",
    ]
