"""Тесты точки приёма реестра планировочных правил.

Targeted proof инвариантов D-1: неизвестная major отвергается до разбора
тела, отсутствие артефакта — fail-closed, подмена артефакта ломает
проверку целостности, UNKNOWN не схлопывается в значение, загрузчик
ничего не порождает.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from apps.planning_rules import check as integrity_check
from apps.planning_rules.check import SOURCE_PATH
from apps.planning_rules.registry import (
    CLOSED_KINDS,
    DATA_PATH,
    PlanningRegistryInvalidError,
    PlanningRegistryUnavailableError,
    PlanningRegistryVersionError,
    load_registry,
)


def _document(**overrides):
    document = {
        "registry": "ayla.planning-rules-registry",
        "registry_version": "1.0",
        "compatible_contract_version": "1.1",
        "status": "draft",
        "updated": "2026-09-08",
        "rules": [
            {
                "rule_id": "PR-DURATION-0001",
                "kind": "DURATION",
                "status": "UNKNOWN",
                "value": {
                    "reason": "NO_RULE_EXISTS",
                    "asked_source": "ayla-knowledge:03 AI System/Contracts/x.yaml",
                    "as_of": "2026-09-08",
                },
                "unit": None,
                "applicability": {
                    "scope": "GENERAL",
                    "subject_kind": "canonical_service",
                    "subject_ids": [],
                    "conditions": [],
                },
                "provenance": {"source": "ayla-knowledge:x.yaml", "version": "0.1"},
            }
        ],
    }
    document.update(overrides)
    return document


def _write(tmp_path: Path, document: dict) -> Path:
    artifact = tmp_path / "registry.yaml"
    artifact.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")
    return artifact


#: Таблица владельца «цель → план» (AYLA-DEC-0089): семь целей — ровно
#: столько правил PLAN_CADENCE; каждое — носитель шаблона, не курс услуги.
PLAN_CADENCE_GOALS = {
    "body_shape",
    "event",
    "new_look",
    "recharge",
    "relax",
    "self_care",
    "skin_care",
}


class TestPackagedArtifact:
    def test_vendored_registry_loads(self):
        registry = load_registry()
        assert registry.registry_version == "1.0"
        assert len(CLOSED_KINDS) == 14
        # 13 правил о типах услуг + 7 PLAN_CADENCE по таблице §51.
        assert len(registry.rules) == 20

    def test_vendored_registry_covers_every_kind(self):
        registry = load_registry()
        kinds = {rule.kind for rule in registry.rules}
        assert kinds == CLOSED_KINDS

    def test_statuses_are_honest_slice(self):
        registry = load_registry()
        unsupported = {
            rule.kind for rule in registry.rules if rule.status == "INTENTIONALLY_UNSUPPORTED"
        }
        unknown = {rule.kind for rule in registry.rules if rule.status == "UNKNOWN"}
        known = [rule for rule in registry.rules if rule.status == "KNOWN"]
        assert unsupported == {"REPETITION", "COMPATIBILITY", "INCOMPATIBILITY"}
        assert len(unknown) == 10
        # presence перед отсутствием (negative_assert_guard): «KNOWN только у
        # PLAN_CADENCE» осмысленно только когда KNOWN вообще есть.
        assert len(known) == 7
        assert {rule.kind for rule in known} == {"PLAN_CADENCE"}
        # Ни одного KNOWN о курсах, интервалах или совместимости услуг.
        assert not any(
            rule.status == "KNOWN" and rule.applicability.get("subject_kind") == "canonical_service"
            for rule in registry.rules
        )

    def test_plan_cadence_is_owner_table_only(self):
        # AYLA-DEC-0093: PLAN_CADENCE — только регулярность шаблона плана,
        # источник — решение владельца AYLA-DEC-0089, по одной цели на правило.
        registry = load_registry()
        cadences = registry.rules_by_kind("PLAN_CADENCE")
        assert len(cadences) == 7
        assert {tuple(rule.applicability["subject_ids"]) for rule in cadences} == {
            (goal,) for goal in PLAN_CADENCE_GOALS
        }
        for rule in cadences:
            assert rule.applicability["subject_kind"] == "plan_template"
            assert rule.provenance["source"].endswith(":AYLA-DEC-0089")
            assert rule.value["actions"], rule.rule_id

    def test_unknown_carries_reason_shape_not_value(self):
        registry = load_registry()
        for rule in registry.rules:
            if rule.status == "UNKNOWN":
                assert set(rule.value) == {"reason", "asked_source", "as_of"}
                assert rule.value["reason"] == "NO_RULE_EXISTS"
            elif rule.status == "INTENTIONALLY_UNSUPPORTED":
                assert rule.value is None

    def test_loader_invents_nothing(self):
        # Загрузчик отдаёт записи как есть: значение KNOWN — ровно из
        # артефакта, у каждой записи адрес и версия источника.
        registry = load_registry()
        raw = yaml.safe_load(DATA_PATH.read_bytes())
        by_id = {item["rule_id"]: item for item in raw["rules"]}
        for rule in registry.rules:
            assert rule.value == by_id[rule.rule_id]["value"]
            assert rule.provenance["source"]
            assert rule.provenance["version"]

    def test_registry_version_is_observable(self):
        # Контракт §9: пересчёт запускает Plan Engine; точка приёма обязана
        # сделать смену версии наблюдаемой.
        registry = load_registry()
        assert isinstance(registry.registry_version, str)
        major = int(registry.registry_version.split(".", 1)[0])
        assert major == 1

    def test_packaged_artifact_matches_source_pin(self):
        source = json.loads(SOURCE_PATH.read_text(encoding="utf-8"))
        import hashlib

        digest = hashlib.sha256(DATA_PATH.read_bytes()).hexdigest()
        assert digest == source["sha256"]


class TestVersionRejection:
    def test_unknown_major_rejected_before_body_parsed(self, tmp_path):
        # Тело намеренно мусорное: если версия отвергается до разбора,
        # мусор не имеет значения. Если разбор начнётся — упадёт иначе.
        artifact = _write(tmp_path, _document(registry_version="9.0", rules="мусор"))
        with pytest.raises(PlanningRegistryVersionError, match="Разбирать неизвестное запрещено"):
            load_registry(artifact)

    def test_same_major_newer_minor_accepted(self, tmp_path):
        artifact = _write(tmp_path, _document(registry_version="1.1"))
        registry = load_registry(artifact)
        assert registry.registry_version == "1.1"

    def test_previous_major_rejected(self, tmp_path):
        # Реестр 0.x (13 типов) после перехода на 1.0 не разбирается.
        artifact = _write(tmp_path, _document(registry_version="0.1"))
        with pytest.raises(PlanningRegistryVersionError, match="Разбирать неизвестное запрещено"):
            load_registry(artifact)

    def test_missing_version_rejected(self, tmp_path):
        document = _document()
        del document["registry_version"]
        artifact = _write(tmp_path, document)
        with pytest.raises(PlanningRegistryVersionError):
            load_registry(artifact)


class TestFailureBehavior:
    def test_missing_artifact_is_fail_closed(self, tmp_path):
        with pytest.raises(PlanningRegistryUnavailableError):
            load_registry(tmp_path / "нет-такого-файла.yaml")

    def test_fifteenth_kind_rejected(self, tmp_path):
        document = _document()
        document["rules"][0]["kind"] = "MADE_UP_KIND"
        artifact = _write(tmp_path, document)
        with pytest.raises(PlanningRegistryInvalidError, match="MADE_UP_KIND"):
            load_registry(artifact)

    def test_plan_cadence_on_service_rejected(self, tmp_path):
        # AYLA-DEC-0093: регулярность привычки не описывает услугу как курс.
        document = _document()
        document["rules"][0].update(
            rule_id="PR-PLAN_CADENCE-X",
            kind="PLAN_CADENCE",
            status="KNOWN",
            value={
                "actions": [
                    {"action_type": "book_service", "cadence": "per_week", "target_count": 1}
                ]
            },
            unit="plan_action_count_per_cadence",
        )
        artifact = _write(tmp_path, document)
        with pytest.raises(PlanningRegistryInvalidError, match="PLAN_CADENCE допустим только"):
            load_registry(artifact)

    def test_plan_template_cannot_carry_service_rule(self, tmp_path):
        document = _document()
        document["rules"][0]["kind"] = "MIN_INTERVAL"
        document["rules"][0]["applicability"]["subject_kind"] = "plan_template"
        artifact = _write(tmp_path, document)
        with pytest.raises(PlanningRegistryInvalidError, match="допустим только у PLAN_CADENCE"):
            load_registry(artifact)

    def test_unknown_without_value_is_artifact_error_not_unknown(self, tmp_path):
        document = _document()
        del document["rules"][0]["value"]
        artifact = _write(tmp_path, document)
        with pytest.raises(PlanningRegistryInvalidError, match="ошибка артефакта, а не UNKNOWN"):
            load_registry(artifact)

    def test_invented_value_in_unsupported_kind_rejected(self, tmp_path):
        document = _document()
        document["rules"][0].update(
            kind="COMPATIBILITY", status="INTENTIONALLY_UNSUPPORTED", value=3
        )
        artifact = _write(tmp_path, document)
        with pytest.raises(PlanningRegistryInvalidError, match="value: null"):
            load_registry(artifact)

    def test_wrong_registry_name_rejected(self, tmp_path):
        artifact = _write(tmp_path, _document(registry="someone.else"))
        with pytest.raises(PlanningRegistryInvalidError, match="ayla.planning-rules-registry"):
            load_registry(artifact)


class TestIntegrityCheck:
    def test_verify_packaged_registry_is_clean(self):
        assert integrity_check.verify_packaged_registry() == []

    def test_main_returns_zero(self, capsys):
        assert integrity_check.main() == 0
        assert "OK" in capsys.readouterr().out

    def test_tampered_artifact_fails(self, monkeypatch, tmp_path):
        tampered = tmp_path / "tampered.yaml"
        tampered.write_text("registry: ayla.planning-rules-registry\n", encoding="utf-8")
        monkeypatch.setattr(integrity_check, "DATA_PATH", tampered)
        problems = integrity_check.verify_packaged_registry()
        assert any("подменён" in problem for problem in problems)
