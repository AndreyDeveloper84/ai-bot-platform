"""DRF-2531 — два отказа проверки связи пользователь↔салон требуют решения.

``ingest_tenancy`` отвечает на «нет активной связи» двумя местами:
``no_active_relationship_user_scope`` (событие без тенанта) и
``no_active_relationship`` (событие салона). Оба поднимают голый
``TenantAuthorizationError`` → 500 → отправитель повторяет 8 раз за ~4,5 ч.
Сегодня обе ветки недостижимы: определения класса ``TenantUserRelationship``
в боте нет, ветки открываются только при его появлении (#246). Дефект, если
это дефект, вернётся не правкой, а включением соседней работы.

Постоянен ли отказ по смыслу — вопрос открыт (связь может доехать позже
события: тогда это гонка порядка и 500 верно). Поэтому сторож охраняет не
ответ, а **наличие решения**:

* модель появилась → каждая причина обязана иметь запись в
  ``RELATIONSHIP_REFUSAL_DECISIONS``, иначе красно с названием причины;
* записанное решение обязано совпадать с тем, что ветка **реально** поднимает
  (проверяется прогоном настоящих веток с заглушкой модели), иначе красно;
* пока модели нет — узел не пропускается, а доказывает, что обе ветки и
  правда недостижимы: иначе он был бы проверкой, которая не может провалиться.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import apps.tenancy.models as tenancy_models
from apps.eventbus import ingest_tenancy
from apps.eventbus.ingest_rejection import IngestRejection
from apps.eventbus.ingest_tenancy import (
    RELATIONSHIP_REFUSAL_DECISIONS,
    RELATIONSHIP_REFUSAL_REASONS,
    TenantAuthorizationError,
    assert_envelope_tenant_authorized,
)

TENANT_ID = "11111111-1111-1111-1111-111111111111"
USER_ID = "22222222-2222-2222-2222-222222222222"

#: Какое событие проводит конверт в какую из двух веток.
_ENVELOPES: dict[str, SimpleNamespace] = {
    "no_active_relationship_user_scope": SimpleNamespace(
        event_name="user.profile.updated",  # из _TENANT_NULLABLE_EVENT_NAMES
        user_id=USER_ID,
        tenant_id=None,
        event_id="evt-2531-user-scope",
        correlation_id=None,
    ),
    "no_active_relationship": SimpleNamespace(
        event_name="booking.created",
        user_id=USER_ID,
        tenant_id=TENANT_ID,
        event_id="evt-2531-tenant",
        correlation_id=None,
    ),
}


class _NoRelationship:
    """Заглушка модели #246: связи нет ни у кого."""

    class objects:  # noqa: N801 — повторяет форму менеджера Django
        @staticmethod
        def filter(**_kwargs):  # noqa: ANN205
            return SimpleNamespace(exists=lambda: False)


@pytest.fixture
def model_shipped(monkeypatch):
    """Подмена: модель «появилась» — атрибут на настоящем ``apps.tenancy.models``.

    Снимается сразу после узла, и это проверяется здесь же: соседи в том же
    процессе (``-n 4 --dist worksteal``) не должны увидеть чужую модель.
    """
    with monkeypatch.context() as patch:
        patch.setattr(tenancy_models, "TenantUserRelationship", _NoRelationship, raising=False)
        yield
    assert not hasattr(tenancy_models, "TenantUserRelationship"), "заглушка не снялась"


def _absence_evidence() -> list[str]:
    """Чем установлено, что модели нет: каждая строка — отдельное свидетельство."""
    from django.apps import apps as django_apps

    evidence: list[str] = []
    try:
        from apps.tenancy.models import TenantUserRelationship  # type: ignore[attr-defined]  # noqa: F401
    except ImportError as exc:
        evidence.append(f"проба импорта кода: ImportError ({exc})")
    registered = [
        f"{m._meta.app_label}.{m.__name__}"
        for m in django_apps.get_models()
        if m.__name__ == "TenantUserRelationship"
    ]
    # Модель под этим именем в реестре при отрицательной пробе — значит, её
    # положили не туда, где её ищет код: ветки мертвы по неверной причине.
    assert not registered, (
        f"TenantUserRelationship есть в реестре приложений ({registered}), но проба "
        "кода (`from apps.tenancy.models import …`) его не видит — ветки DRF-2531 "
        "мертвы по неверной причине"
    )
    evidence.append("реестр приложений Django: класса TenantUserRelationship нет")
    return evidence


def _observed(reason: str) -> tuple[str, str]:
    """Прогнать настоящую ветку и сказать, что она поднимает: (класс, сообщение)."""
    try:
        assert_envelope_tenant_authorized(_ENVELOPES[reason])
    except TenantAuthorizationError as exc:
        kind = "permanent" if isinstance(exc, IngestRejection) else "transient"
        return kind, str(exc)
    return "admitted", ""


def _problems(decisions: dict[str, str]) -> list[str]:
    """Причины без решения или с решением, расходящимся с кодом. Пусто — порядок."""
    problems: list[str] = []
    for reason in RELATIONSHIP_REFUSAL_REASONS:
        kind, message = _observed(reason)
        # Прибор мерит ту ветку, про которую говорит: иначе «решение совпало»
        # подтвердилось бы на чужом отказе.
        assert message.startswith(reason), f"{reason}: конверт попал не в ту ветку — {message!r}"
        decided = decisions.get(reason)
        if decided is None:
            problems.append(f"{reason}: решения нет (код сейчас отвечает как {kind})")
        elif decided != kind:
            problems.append(f"{reason}: решено {decided}, а код отвечает как {kind}")
    return problems


def _guard() -> None:
    """Сторож: модель есть → решение по обеим причинам; модели нет → ветки мертвы."""
    if ingest_tenancy._tenant_user_relationship_available():
        # #246 пришёл: две причины стали достижимы и обязаны иметь решение.
        problems = _problems(RELATIONSHIP_REFUSAL_DECISIONS)
        assert not problems, (
            "DRF-2531: TenantUserRelationship появился — по причинам ниже нужно "
            "решение в RELATIONSHIP_REFUSAL_DECISIONS (permanent → 422, "
            "transient → 500) и совпадающий с ним код:\n  " + "\n  ".join(problems)
        )
        return

    # Модели нет — назвать, ЧЕМ это установлено, и доказать, что обе ветки
    # действительно недостижимы, а не молча пропустить узел.
    evidence = _absence_evidence()
    assert len(evidence) == 2, evidence
    print("DRF-2531: модели нет —", "; ".join(evidence))
    for reason in RELATIONSHIP_REFUSAL_REASONS:
        try:
            assert_envelope_tenant_authorized(_ENVELOPES[reason])
        except TenantAuthorizationError as exc:
            assert reason not in str(exc), f"{reason} достижим без модели: {exc}"


@pytest.fixture(autouse=True)
def _closed_allowlist(settings):
    settings.EVENT_INGEST_TENANT_VERIFY_FAIL_OPEN = False
    settings.EVENT_INGEST_ALLOWED_TENANTS = frozenset()
    settings.EVENT_INGEST_ALLOWED_EVENTS = frozenset()


class TestGuardOnTheRealTree:
    def test_decision_required_once_model_exists(self) -> None:
        _guard()


class TestSubstitution:
    def test_guard_goes_red_when_model_ships_and_names_both(self, model_shipped) -> None:
        # Главная подмена: тот же сторож, что на реальном дереве, при «появившейся»
        # модели и сегодняшней пустой записи краснеет и называет ОБЕ причины.
        with pytest.raises(AssertionError) as caught:
            _guard()
        message = str(caught.value)
        assert "no_active_relationship_user_scope: решения нет" in message
        assert "no_active_relationship: решения нет" in message

    def test_probe_sees_the_stub(self, model_shipped) -> None:
        # Рубильник сторожа — та же проба, что у кода: заглушка его переключает.
        assert ingest_tenancy._tenant_user_relationship_available() is True

    def test_empty_decisions_name_both_reasons(self, model_shipped) -> None:
        problems = _problems({})
        joined = "\n".join(problems)
        assert len(problems) == 2, problems
        assert "no_active_relationship_user_scope: решения нет" in joined
        assert "no_active_relationship: решения нет" in joined

    def test_decision_that_disagrees_with_code_is_red(self, model_shipped) -> None:
        # Сегодня обе ветки — голый TenantAuthorizationError (500): «permanent»
        # без правки кода — ложная запись. Сторож обязан назвать ОБЕ, а не
        # первую встреченную.
        problems = _problems(dict.fromkeys(RELATIONSHIP_REFUSAL_REASONS, "permanent"))
        assert problems == [
            "no_active_relationship_user_scope: решено permanent, а код отвечает как transient",
            "no_active_relationship: решено permanent, а код отвечает как transient",
        ]

    def test_one_wrong_decision_names_only_it(self, model_shipped) -> None:
        problems = _problems(
            {
                "no_active_relationship_user_scope": "transient",
                "no_active_relationship": "permanent",
            }
        )
        assert problems == [
            "no_active_relationship: решено permanent, а код отвечает как transient"
        ]

    def test_decision_matching_code_is_green(self, model_shipped) -> None:
        # Положительная пара: запись, совпадающая с кодом, проходит — иначе
        # сторож краснел бы на любом решении, и решение было бы невозможно.
        # Сначала наличие на тех же данных: без записи прибор видит обе причины —
        # значит пустой список ниже означает «решено», а не «прибор слеп».
        assert len(_problems({})) == len(RELATIONSHIP_REFUSAL_REASONS) == 2
        assert _problems(dict.fromkeys(RELATIONSHIP_REFUSAL_REASONS, "transient")) == []

    def test_recorded_decisions_match_code(self, model_shipped) -> None:
        # Что бы ни было записано в RELATIONSHIP_REFUSAL_DECISIONS, оно обязано
        # совпадать с кодом уже сегодня, до появления модели.
        mismatched = [
            p for p in _problems(RELATIONSHIP_REFUSAL_DECISIONS) if "решения нет" not in p
        ]
        assert not mismatched, mismatched
