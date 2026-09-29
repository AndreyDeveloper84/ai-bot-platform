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
        "registry_version": "0.1",
        "compatible_contract_version": "1.0",
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


class TestPackagedArtifact:
    def test_vendored_registry_loads(self):
        registry = load_registry()
        assert registry.registry_version == "0.1"
        assert len(registry.rules) == len(CLOSED_KINDS) == 13

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
        assert unsupported == {"REPETITION", "COMPATIBILITY", "INCOMPATIBILITY"}
        assert len(unknown) == 10
        # presence перед отсутствием (negative_assert_guard): «нет KNOWN»
        # осмысленно только когда записи вообще есть.
        assert len(registry.rules) == 13
        assert not any(rule.status == "KNOWN" for rule in registry.rules)

    def test_unknown_carries_reason_shape_not_value(self):
        registry = load_registry()
        for rule in registry.rules:
            if rule.status == "UNKNOWN":
                assert set(rule.value) == {"reason", "asked_source", "as_of"}
                assert rule.value["reason"] == "NO_RULE_EXISTS"
            elif rule.status == "INTENTIONALLY_UNSUPPORTED":
                assert rule.value is None

    def test_loader_invents_nothing(self):
        registry = load_registry()
        for rule in registry.rules:
            assert rule.status != "KNOWN"  # ни одного выдуманного KNOWN
            assert rule.provenance["source"]
            assert rule.provenance["version"]

    def test_registry_version_is_observable(self):
        # Контракт §9: пересчёт запускает Plan Engine; точка приёма обязана
        # сделать смену версии наблюдаемой.
        registry = load_registry()
        assert isinstance(registry.registry_version, str)
        major = int(registry.registry_version.split(".", 1)[0])
        assert major == 0

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
        artifact = _write(tmp_path, _document(registry_version="0.2"))
        registry = load_registry(artifact)
        assert registry.registry_version == "0.2"

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

    def test_fourteenth_kind_rejected(self, tmp_path):
        document = _document()
        document["rules"][0]["kind"] = "MADE_UP_KIND"
        artifact = _write(tmp_path, document)
        with pytest.raises(PlanningRegistryInvalidError, match="MADE_UP_KIND"):
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
