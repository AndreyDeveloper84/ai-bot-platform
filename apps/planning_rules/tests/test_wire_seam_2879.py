"""Шов «реестр правил бота → сборка плана в каталоге» — половина бота (DRF-2879).

Каталог (``wellness/plan_compose.py::parse_compose_request``) отвергает запрос
ЦЕЛИКОМ, если реестр в теле не конформен. Форма не должна разойтись молча
между двумя репозиториями, поэтому здесь:

* тело строится из НАСТОЯЩЕЙ вендоренной копии реестра, не из выдуманной;
* оно проверяется по правилам каталога, переписанным в этом файле с его кода
  заново (импортировать каталог неоткуда — это другой репозиторий);
* sha256 файла сверяется с записанным в ``source.json``: когда источник
  сменится, покраснеет это число — то же, которое сторожит узел каталога на
  своей копии того же файла.

Это не заменяет сквозной прогон на стенде: правила каталога здесь — пересказ
его кода на дату листа, а не сам код.

* w1 — тело настоящего реестра проходит правила каталога;
* w2 — поля правила — закрытый перечень, ``note`` не уходит;
* w3 — ``as_of`` — строка ISO, тело сериализуется в JSON без помощников;
* w4 — правил столько же, сколько в реестре: пустой и частичный реестр не
  получаются;
* w5 — сторож сам: правила каталога отвергают то, что должны.
"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

import pytest

from apps.planning_rules.registry import DATA_PATH, PACKAGE_DIR, load_registry
from apps.planning_rules.wire import WIRE_RULE_FIELDS, registry_wire_body

# ── правила каталога, переписанные с wellness/plan_compose.py (PR #679) ──────

_KINDS = frozenset(
    {
        "CAPABILITY_SEMANTICS",
        "SERVICE_CAPABILITY_MAPPING",
        "DURATION",
        "EVENT_WINDOW",
        "MIN_INTERVAL",
        "MAX_INTERVAL",
        "REPETITION",
        "SEQUENCE",
        "PRECONDITION",
        "COMPATIBILITY",
        "INCOMPATIBILITY",
        "RECOVERY_WINDOW",
        "SAFETY_CONSTRAINT",
    }
)
_STATUSES = frozenset({"KNOWN", "UNKNOWN", "INTENTIONALLY_UNSUPPORTED"})
_UNKNOWN_REASONS = frozenset(
    {
        "NO_RULE_EXISTS",
        "RULE_NOT_APPLICABLE",
        "SOURCE_UNREACHABLE",
        "SOURCE_STALE",
        "MAPPING_MISSING",
        "POLICY_WITHHELD",
    }
)
_CAPABILITY_LEVEL_KINDS = ("CAPABILITY_SEMANTICS", "SERVICE_CAPABILITY_MAPPING")


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def catalog_refusal(registry: Any) -> str | None:
    """Почему каталог отверг бы ``rules_registry`` — или ``None``, если принял бы."""
    if not isinstance(registry, dict) or not _text(registry.get("registry_version")):
        return "rules_registry_missing"
    rules = registry.get("rules")
    if not isinstance(rules, list):
        return "rules_registry_missing"
    for rule in rules:
        if not isinstance(rule, dict):
            return "rule_not_object"
        if not _text(rule.get("rule_id")):
            return "rule_id_missing"
        if rule.get("kind") not in _KINDS:
            return "rule_kind_unknown"
        status = rule.get("status")
        if status not in _STATUSES:
            return "rule_status_unknown"
        provenance = rule.get("provenance")
        if (
            not isinstance(provenance, dict)
            or not _text(provenance.get("source"))
            or not _text(provenance.get("version"))
        ):
            return "rule_provenance_missing"
        applicability = rule.get("applicability")
        if (
            not isinstance(applicability, dict)
            or not _text(applicability.get("scope"))
            or not _text(applicability.get("subject_kind"))
            or not isinstance(applicability.get("subject_ids", []), list)
        ):
            return "rule_applicability_missing"
        value = rule.get("value")
        if status == "UNKNOWN":
            if (
                not isinstance(value, dict)
                or value.get("reason") not in _UNKNOWN_REASONS
                or not _text(value.get("asked_source"))
                or not _text(str(value.get("as_of") or ""))
            ):
                return "rule_unknown_value_malformed"
        elif status == "KNOWN" and value is None:
            return "rule_known_value_missing"
    ids = [rule["rule_id"] for rule in rules]
    if len(set(ids)) != len(ids):
        return "rule_id_duplicate"
    present = {r["kind"] for r in rules if r["applicability"]["subject_kind"] == "capability"}
    if any(kind not in present for kind in _CAPABILITY_LEVEL_KINDS):
        return "rules_registry_incomplete"
    return None


@pytest.fixture(scope="module")
def body() -> dict[str, Any]:
    return registry_wire_body(load_registry())


class TestTheRealRegistryOnTheWire:
    def test_w0_the_vendored_copy_is_the_recorded_one(self) -> None:
        """Число, на котором обе стороны шва краснеют вместе."""
        recorded = json.loads((PACKAGE_DIR / "data" / "source.json").read_text(encoding="utf-8"))

        assert hashlib.sha256(DATA_PATH.read_bytes()).hexdigest() == recorded["sha256"]

    def test_w1_the_catalog_would_accept_it(self, body) -> None:
        # Положительная пара к отказам ниже: правил в теле не ноль.
        assert len(body["rules"]) >= 13
        assert catalog_refusal(body) is None

    def test_w2_a_rule_carries_exactly_the_wire_fields(self, body) -> None:
        for rule in body["rules"]:
            assert tuple(rule) == WIRE_RULE_FIELDS

    def test_w2_the_note_stays_at_home(self, body) -> None:
        registry = load_registry()
        # Положительная пара: примечания в реестре есть — их не шлёт провод.
        assert any(rule.note for rule in registry.rules)
        assert all("note" not in rule for rule in body["rules"])

    def test_w3_as_of_is_an_iso_string(self, body) -> None:
        unknown = [rule for rule in body["rules"] if rule["status"] == "UNKNOWN"]
        assert unknown, "в реестре 0.1 неизвестные значения есть — иначе узел слеп"
        for rule in unknown:
            as_of = rule["value"]["as_of"]
            assert isinstance(as_of, str)
            assert len(as_of) == 10 and as_of[4] == "-" and as_of[7] == "-"

    def test_w3_the_body_is_plain_json(self, body) -> None:
        """Без ``default=``: объект даты уронил бы сериализацию запроса."""
        assert json.loads(json.dumps(body)) == body

    def test_w4_every_rule_of_the_registry_is_sent(self, body) -> None:
        registry = load_registry()

        assert body["registry_version"] == registry.registry_version
        assert [rule["rule_id"] for rule in body["rules"]] == [r.rule_id for r in registry.rules]


class TestTheCatalogRulesInThisFileBite:
    """Сторож сам: переписанные правила отвергают то, что обязаны."""

    def _broken(self, body: dict[str, Any], mutate) -> str | None:
        broken = copy.deepcopy(body)
        mutate(broken)
        return catalog_refusal(broken)

    def test_w5_an_empty_registry_is_refused(self, body) -> None:
        assert self._broken(body, lambda b: b.update(rules=[])) == "rules_registry_incomplete"

    def test_w5_a_missing_version_is_refused(self, body) -> None:
        assert (
            self._broken(body, lambda b: b.update(registry_version="")) == "rules_registry_missing"
        )

    def test_w5_a_fourteenth_kind_is_refused(self, body) -> None:
        def mutate(b: dict[str, Any]) -> None:
            b["rules"][0]["kind"] = "PLAN_CADENCE"

        assert self._broken(body, mutate) == "rule_kind_unknown"

    def test_w5_a_rule_without_provenance_is_refused(self, body) -> None:
        def mutate(b: dict[str, Any]) -> None:
            b["rules"][0]["provenance"] = {}

        assert self._broken(body, mutate) == "rule_provenance_missing"

    def test_w5_an_unknown_without_a_reason_is_refused(self, body) -> None:
        def mutate(b: dict[str, Any]) -> None:
            rule = next(r for r in b["rules"] if r["status"] == "UNKNOWN")
            rule["value"] = {"asked_source": "x", "as_of": "2026-09-08"}

        assert self._broken(body, mutate) == "rule_unknown_value_malformed"

    def test_w5_a_registry_without_capability_rules_is_refused(self, body) -> None:
        def mutate(b: dict[str, Any]) -> None:
            b["rules"] = [r for r in b["rules"] if r["kind"] != "CAPABILITY_SEMANTICS"]

        assert self._broken(body, mutate) == "rules_registry_incomplete"
