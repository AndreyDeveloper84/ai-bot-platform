"""Согласие на ИИ-оценку еды и гейт перед каждым вызовом (DRF-2845).

Решение владельца 07.10.2026: внешняя модель оценивает калории только с
отдельного добровольного согласия; разрешение проверяется ПЕРЕД каждым
внешним вызовом, после отзыва новых отправок нет; без согласия справочник и
дневник работают; никому согласие не выдаётся автоматически.

Реестр здесь НАСТОЯЩИЙ — предикат не подменяется: подменённый доказал бы
только, что его зовут, а не что отозванное согласие закрывает отправку.

Узлы:

* c1 — согласия нет ни у кого: ни миграцией, ни вместе с ``personal_data``,
  ни вместе с согласием дневника;
* c2 — выдача только под показанной версией; чужая версия ничего не пишет;
* c3 — отзыв закрывает, строка остаётся в журнале, новая выдача открывает;
* c4 — выдача под прежним текстом не считается;
* c5 — согласие человека, а не строки: вторая оболочка видит выдачу и отзыв;
* c6 — отзыв ``personal_data`` снимает и это согласие; обратного нет;
* p1 — предикат отправки: механизм выключен → можно всем (как до листа);
  включён → только при согласии; «Без чисел» → нельзя; сбой чтения → нельзя;
* g1 — перепись мест вызова ПО КЛАССУ: каждый вызов ``estimate_dish`` и
  ``log_meal`` несёт ``ai_estimate_allowed`` прямым вызовом предиката;
* g2 — у клиента аргумент обязателен и без умолчания;
* g3 — у выдачи нет ни одной производственной поверхности.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from unittest.mock import patch

import pytest

from apps.consent import ai_food_estimation
from apps.consent.ai_food_estimation import (
    AI_FOOD_ESTIMATION,
    AI_FOOD_ESTIMATION_DOCUMENT_VERSION,
    UnknownDisclosureVersionError,
    estimate_permitted,
    grant,
    is_granted,
    withdraw,
)
from apps.consent.models import ConsentRecord
from apps.consent.nutrition import FOOD_DIARY_CONSENT_DOCUMENT_VERSION, grant_diary
from apps.consent.services import (
    has_person_consent,
    record_person_consent,
    withdraw_personal_data_for_bot_users,
)
from apps.identity.models import BotUser
from apps.integrations.ayla.nutrition_client import NutritionClient
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

SOURCE = "test:2845"
PERSONAL_DATA = ConsentRecord.ConsentType.PERSONAL_DATA.value
GATED_METHODS = ("estimate_dish", "log_meal")
PREDICATE = "estimate_permitted"


@pytest.fixture
def bot_user(db) -> BotUser:
    tenant = Tenant.objects.create(slug="ai-food-2845", name="AI food 2845")
    user = BotUser.all_tenants.create(
        tenant=tenant, channel="max", channel_user_id="92845", display_name="Клиент"
    )
    record_person_consent(user, consent_type=PERSONAL_DATA, source=SOURCE)
    return user


@pytest.fixture
def required(settings):
    settings.AI_FOOD_ESTIMATION_CONSENT_REQUIRED = True


def _grant(bot_user: BotUser) -> None:
    assert (
        grant(bot_user, document_version=AI_FOOD_ESTIMATION_DOCUMENT_VERSION, source=SOURCE) is True
    )


def _rows(bot_user: BotUser) -> list[ConsentRecord]:
    return list(
        ConsentRecord.all_tenants.filter(bot_user=bot_user, consent_type=AI_FOOD_ESTIMATION)
    )


def _types(bot_user: BotUser) -> list[str]:
    """Типы всех строк согласий человека — что в журнале есть на самом деле."""
    return sorted(
        ConsentRecord.all_tenants.filter(bot_user=bot_user).values_list("consent_type", flat=True)
    )


class TestNobodyGetsItUnasked:
    def test_c1_the_migration_only_widens_the_choices(self) -> None:
        """Миграция типа не пишет строк: в ней одна операция, и это ``AlterField``."""
        from importlib import import_module

        from django.db import migrations

        migration = import_module(
            "apps.consent.migrations.0008_ai_food_estimation_consent_type"
        ).Migration

        assert [type(operation) for operation in migration.operations] == [migrations.AlterField]

    def test_c1_personal_data_does_not_bring_it(self, bot_user) -> None:
        # Положительная пара: основание выдано по-настоящему.
        assert has_person_consent(bot_user, PERSONAL_DATA) is True
        assert is_granted(bot_user) is False
        # В журнале человека ровно одна строка — основание; нового типа нет.
        assert _types(bot_user) == [PERSONAL_DATA]

    def test_c1_the_diary_consent_does_not_bring_it(self, bot_user) -> None:
        assert (
            grant_diary(bot_user, document_version=FOOD_DIARY_CONSENT_DOCUMENT_VERSION) is not None
        )

        assert is_granted(bot_user) is False
        assert _rows(bot_user) == []


class TestGrantAndWithdraw:
    def test_c2_granted_under_the_shown_version(self, bot_user) -> None:
        _grant(bot_user)

        (row,) = _rows(bot_user)
        assert row.document_version == AI_FOOD_ESTIMATION_DOCUMENT_VERSION
        assert row.source == SOURCE
        assert is_granted(bot_user) is True

    def test_c2_a_foreign_version_writes_nothing(self, bot_user) -> None:
        with pytest.raises(UnknownDisclosureVersionError):
            grant(bot_user, document_version="ai-food-estimation-v999", source=SOURCE)

        assert _types(bot_user) == [PERSONAL_DATA]
        assert is_granted(bot_user) is False

    def test_c2_a_second_grant_makes_no_second_row(self, bot_user) -> None:
        _grant(bot_user)
        _grant(bot_user)

        assert len(_rows(bot_user)) == 1

    def test_c3_withdrawal_closes_and_stays_in_the_journal(self, bot_user) -> None:
        _grant(bot_user)

        assert withdraw(bot_user, source=SOURCE) == 1

        (row,) = _rows(bot_user)
        assert row.withdrawn_at is not None
        assert is_granted(bot_user) is False
        # Повторный отзыв ничего не снимает и не падает.
        assert withdraw(bot_user, source=SOURCE) == 0

    def test_c3_a_new_grant_after_withdrawal_opens_again(self, bot_user) -> None:
        _grant(bot_user)
        withdraw(bot_user, source=SOURCE)

        _grant(bot_user)

        assert is_granted(bot_user) is True
        assert len(_rows(bot_user)) == 2

    def test_c3_withdrawal_leaves_the_other_consents_alone(self, bot_user) -> None:
        _grant(bot_user)

        withdraw(bot_user, source=SOURCE)

        assert has_person_consent(bot_user, PERSONAL_DATA) is True

    def test_c4_a_grant_under_an_older_text_does_not_count(self, bot_user) -> None:
        record_person_consent(
            bot_user,
            consent_type=AI_FOOD_ESTIMATION,
            source=SOURCE,
            document_version="ai-food-estimation-draft-v0",
        )

        # Положительная пара: строка есть и не отозвана — не признана версия.
        (row,) = _rows(bot_user)
        assert row.withdrawn_at is None
        assert is_granted(bot_user) is False

    def test_c5_the_person_not_the_row(self, bot_user) -> None:
        other_tenant = Tenant.objects.create(slug="ai-food-2845-b", name="AI food 2845 b")
        shell = BotUser.all_tenants.create(
            tenant=other_tenant, channel="max", channel_user_id="92845", display_name="Клиент"
        )

        _grant(bot_user)
        assert is_granted(shell) is True

        withdraw(bot_user, source=SOURCE)
        assert is_granted(shell) is False

    def test_c6_withdrawing_personal_data_takes_it_along(self, bot_user) -> None:
        _grant(bot_user)

        withdraw_personal_data_for_bot_users([bot_user], source=SOURCE)

        assert has_person_consent(bot_user, PERSONAL_DATA) is False
        assert is_granted(bot_user) is False


class TestThePredicateBeforeSending:
    def test_p1_switched_off_everyone_may_as_before(self, bot_user) -> None:
        assert ai_food_estimation.consent_required() is False
        assert is_granted(bot_user) is False

        assert estimate_permitted(bot_user) is True

    def test_p1_required_and_not_granted(self, bot_user, required) -> None:
        assert estimate_permitted(bot_user) is False

    def test_p1_required_and_granted(self, bot_user, required) -> None:
        _grant(bot_user)

        assert estimate_permitted(bot_user) is True

    def test_p1_withdrawal_closes_the_very_next_question(self, bot_user, required) -> None:
        _grant(bot_user)
        assert estimate_permitted(bot_user) is True

        withdraw(bot_user, source=SOURCE)

        assert estimate_permitted(bot_user) is False

    def test_p1_numbers_hidden_sends_nothing_even_with_consent(self, bot_user, required) -> None:
        _grant(bot_user)
        assert estimate_permitted(bot_user) is True

        with patch("apps.nutrition_proactive.prefs.numbers_hidden_for", return_value=True):
            assert estimate_permitted(bot_user) is False

    def test_p1_a_failed_read_is_not_a_consent(self, bot_user, required) -> None:
        _grant(bot_user)
        assert estimate_permitted(bot_user) is True

        with patch.object(ai_food_estimation, "is_granted", side_effect=RuntimeError("db down")):
            assert estimate_permitted(bot_user) is False


def _gated_calls() -> dict[str, list[tuple[str, ast.Call]]]:
    """Вызовы ``estimate_dish`` / ``log_meal`` вне тестов и самого клиента."""
    root = Path(__file__).resolve().parents[3]
    found: dict[str, list[tuple[str, ast.Call]]] = {name: [] for name in GATED_METHODS}
    for path in sorted((root / "apps").rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if "/tests/" in rel or rel.endswith("nutrition_client.py"):
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            name = getattr(node.func, "attr", None) if isinstance(node, ast.Call) else None
            if isinstance(node, ast.Call) and name in GATED_METHODS:
                found[name].append((rel, node))
    return found


def _asks_directly(call: ast.expr) -> bool:
    assert isinstance(call, ast.Call)
    for keyword in call.keywords:
        if keyword.arg != "ai_estimate_allowed":
            continue
        value = keyword.value
        return isinstance(value, ast.Call) and getattr(value.func, "attr", None) == PREDICATE
    return False


class TestEveryCallAsksFirst:
    def test_g1_the_census_sees_the_known_callers(self) -> None:
        calls = _gated_calls()

        assert sorted(rel for rel, _ in calls["estimate_dish"]) == [
            "apps/miniapp_api/views.py",
            "apps/skills/food_clarify/text_entry.py",
            "apps/skills/food_clarify/text_entry.py",
        ]
        assert sorted(rel for rel, _ in calls["log_meal"]) == [
            "apps/miniapp_api/views.py",
            "apps/miniapp_api/views.py",
            "apps/skills/food_clarify/text_entry.py",
            "apps/skills/food_clarify/text_entry.py",
            "apps/skills/food_scanner/skill.py",
        ]

    def test_g1_every_call_carries_the_predicate_itself(self) -> None:
        """Прямой вызов предиката в аргументе — не переменная, прочитанная раньше."""
        offenders = [
            f"{rel}:{call.lineno}"
            for calls in _gated_calls().values()
            for rel, call in calls
            if not _asks_directly(call)
        ]

        assert offenders == []

    def test_g1_the_census_tells_a_stale_value_from_a_fresh_question(self) -> None:
        """Сторож сам: значение из переменной и литерал он не принимает."""
        stale, literal, fresh = (
            ast.parse(source, mode="eval").body
            for source in (
                "c.estimate_dish(ai_estimate_allowed=allowed)",
                "c.estimate_dish(ai_estimate_allowed=True)",
                "c.estimate_dish(ai_estimate_allowed=m.estimate_permitted(u))",
            )
        )

        assert _asks_directly(stale) is False
        assert _asks_directly(literal) is False
        assert _asks_directly(fresh) is True

    @pytest.mark.parametrize("method", GATED_METHODS)
    def test_g2_the_client_argument_has_no_default(self, method: str) -> None:
        parameter = inspect.signature(getattr(NutritionClient, method)).parameters[
            "ai_estimate_allowed"
        ]

        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
        assert parameter.default is inspect.Parameter.empty

    def test_g3_nothing_in_production_grants_it(self) -> None:
        """Поверхности выдачи появятся с утверждённым текстом — и поправят этот узел."""
        root = Path(__file__).resolve().parents[3]
        importers: list[str] = []
        callers: list[str] = []
        for path in sorted((root / "apps").rglob("*.py")):
            rel = path.relative_to(root).as_posix()
            if "/tests/" in rel or rel == "apps/consent/ai_food_estimation.py":
                continue
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.ImportFrom) and (node.module or "").endswith(
                    "ai_food_estimation"
                ):
                    if any(alias.name == "grant" for alias in node.names):
                        importers.append(rel)
                if (
                    isinstance(node, ast.Call)
                    and getattr(node.func, "attr", None) == "grant"
                    and getattr(getattr(node.func, "value", None), "id", None)
                    == "ai_food_estimation"
                ):
                    callers.append(f"{rel}:{node.lineno}")

        assert importers == []
        assert callers == []
