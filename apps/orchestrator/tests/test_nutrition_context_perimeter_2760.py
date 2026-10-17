"""DRF-2760 — блок питания для модели уважает чувствительный периметр §7.1.

У диетолога три поверхности. Две — проактивная (``nutrition_proactive.coach``)
и строка наблюдения (``orchestrator.coach_observation``) — молчат для человека
в чувствительном периметре и решают это одним предикатом,
``render.remarks_suppressed``: беременность, ГВ, РПП, цель, снятая Ayla с
дефицита; профиля нет или он не прочитан — тоже молчание.

Третья — этот блок — отдаёт картину питания МОДЕЛИ с разрешением «назови
связь своими словами» и периметра не спрашивала вовсе: гейтами были только
флаг, согласие и медицинская цель.

* a — профиля нет / он не прочитан → блока нет, и дальше в Ayla не ходим;
* b — РПП (флаг или оверрайд) → блока нет; беременность, ГВ, снятый дефицит →
  с DRF-2766 фазы 1в (вариант «б», решение главного окна 04.10) только факты
  дня и запрет советов: без сравнения белка, подсказки сервиса и цели;
* c — обычный профиль → блок есть (контроль: молчание выше — не от стенда);
* d — предикат тот же самый, что у двух поверхностей коуча, а не копия: при
  «закрыт» блок — только факты, при «открыт» — полный;
* e — до согласия и при выключенном флаге профиль не читается;
* f — отказ чтения профиля любого рода — «не знаем», и хода не роняет.

Двери Ayla подменены на уровне КЛИЕНТА (``get_nutrition_client``), а не
функций модуля: так узлы a и b исполняются и на коде до правки — и краснеют
там по предмету (блок построен), а не на отсутствии имени.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest

from apps.integrations.ayla import NutritionAPIError, NutritionUnavailableError
from apps.nutrition_proactive import render
from apps.nutrition_proactive.tests import test_remarks_suppressed_2222 as _profiles
from apps.orchestrator import nutrition_context
from apps.orchestrator.nutrition_context import build_nutrition_context_block

# Помощники соседнего модуля — присваиванием: одна форма профиля на оба файла.
profile = _profiles.profile
with_pending = _profiles.with_pending

_WEEK = SimpleNamespace(
    days_observed=5,
    protein_avg_pct_goal=62.4,
    protein_low_streak_days=4,
    hint="белка стабильно мало",
    fired_keys=["protein_low"],
    raw={},
)


class _Client:
    """Stands where the Ayla nutrition client stands; counts every door."""

    def __init__(self, profile_outcome: Any) -> None:
        self.profile_outcome = profile_outcome
        self.profile_calls = 0
        self.week_calls = 0

    async def get_profile(self, *, external_user_id: str, **_kw: Any) -> Any:
        self.profile_calls += 1
        if isinstance(self.profile_outcome, Exception):
            raise self.profile_outcome
        return self.profile_outcome

    async def weekly_deficits(self, *, external_user_id: str, **_kw: Any) -> Any:
        self.week_calls += 1
        return _WEEK


@pytest.fixture(autouse=True)
def _flag_on(settings) -> None:
    settings.CONCIERGE_NUTRITION_CONTEXT_ENABLED = True


@pytest.fixture
def doors(monkeypatch):
    """Consent open, every Ayla door stubbed. Returns ``wire(profile) -> client``."""
    from apps.nutrition_coach import goals
    from apps.orchestrator import food_history

    monkeypatch.setattr(nutrition_context, "_consent_open", lambda bot_user: True)
    goal = Mock(return_value=None)
    today = Mock(return_value=food_history.UNKNOWN)
    monkeypatch.setattr(goals, "active_goal", goal)
    monkeypatch.setattr(food_history, "read_today", today)
    monkeypatch.setattr("apps.integrations.ayla.external_user_id_for", lambda bot_user: "bot:max:1")

    def wire(profile_outcome: Any) -> _Client:
        client = _Client(profile_outcome)
        client.goal, client.today = goal, today  # type: ignore[attr-defined]
        monkeypatch.setattr("apps.integrations.ayla.get_nutrition_client", lambda: client)
        return client

    return wire


# ── c: the control — an ordinary profile gets the block ──────────────────────


def test_an_ordinary_profile_gets_the_block(doors) -> None:
    client = doors(profile())

    block = build_nutrition_context_block(object())

    assert "Белок: в среднем 62% от ориентира." in block
    assert client.profile_calls == 1
    assert client.week_calls == 1


# ── a: no profile ────────────────────────────────────────────────────────────


def test_no_profile_no_block_and_no_further_reads(doors) -> None:
    """``get_profile`` → ``None``: 404 или ``exists=false``. «Не знаем» — тишина."""
    client = doors(None)

    assert build_nutrition_context_block(object()) == ""
    # Presence first: the profile WAS asked for — the silence is the gate's, not an idle path's.
    assert client.profile_calls == 1
    assert client.week_calls == 0
    assert client.goal.call_count == 0  # type: ignore[attr-defined]
    assert client.today.call_count == 0  # type: ignore[attr-defined]


# ── b: every sign of §7.1 ────────────────────────────────────────────────────

FACTS_ONLY = (
    "Не давай советов о количестве и составе еды, не предлагай компенсировать "
    "съеденное, не оценивай калорийность как хорошую или плохую — отвечай на "
    "вопрос фактами из дневника."
)

_EATING_DISORDER = [
    ("flag-eating-disorder", profile(health_flags={"eating_disorder": True})),
    ("override-eating-disorder", profile(goal_overridden_by="eating_disorder")),
    ("pending-eating-disorder", with_pending("eating_disorder")),
]

_FACTS_ONLY_PERIMETER = [
    ("flag-pregnant", profile(health_flags={"pregnant": True})),
    ("flag-breastfeeding", profile(health_flags={"breastfeeding": True})),
    ("override-bmr-floor", profile(goal_overridden_by="bmr_floor")),
    ("override-pregnancy", profile(goal_overridden_by="pregnancy")),
    ("override-breastfeeding", profile(goal_overridden_by="breastfeeding")),
    ("pending-bmr-floor", with_pending("bmr_floor")),
]


@pytest.mark.parametrize(
    "sensitive", [p for _, p in _EATING_DISORDER], ids=[i for i, _ in _EATING_DISORDER]
)
def test_an_eating_disorder_gets_no_block(doors, sensitive) -> None:
    """DRF-2766 1в, вариант «б»: разговорную модель интейком при РПП не кормим."""
    client = doors(sensitive)

    assert build_nutrition_context_block(object()) == ""
    assert client.profile_calls == 1
    # A closed perimeter needs no picture: the week is not fetched to be thrown away.
    assert client.week_calls == 0


@pytest.mark.parametrize(
    "sensitive", [p for _, p in _FACTS_ONLY_PERIMETER], ids=[i for i, _ in _FACTS_ONLY_PERIMETER]
)
def test_the_rest_of_the_perimeter_gets_facts_only(doors, sensitive) -> None:
    """Беременность, ГВ, снятый дефицит: факты дня и запрет советов, без #3."""
    client = doors(sensitive)

    block = build_nutrition_context_block(object())

    assert client.profile_calls == 1
    assert "Дней с записями за неделю: 5." in block
    assert FACTS_ONLY in block
    for advice in ("Белок", "Белка не хватает", "белка стабильно мало", "ориентир"):
        assert advice not in block
    assert client.goal.call_count <= 1  # type: ignore[attr-defined]


def test_a_flag_that_is_present_and_false_does_not_silence(doors) -> None:
    """Контроль в другую сторону: ключ есть, значение ложно — человек не в периметре."""
    client = doors(profile(health_flags={"pregnant": False, "eating_disorder": False}))

    assert "Белок: в среднем 62% от ориентира." in build_nutrition_context_block(object())
    assert client.week_calls == 1


# ── d: the same predicate, not a copy ────────────────────────────────────────


@pytest.mark.parametrize("verdict", [True, False], ids=["suppressed", "open"])
def test_the_coach_predicate_decides(doors, monkeypatch, verdict: bool) -> None:
    """Один предикат на трёх поверхностях: расширят периметр — закроется и эта."""
    ordinary = profile()
    doors(ordinary)
    asked = Mock(return_value=verdict)
    monkeypatch.setattr(render, "remarks_suppressed", asked)

    block = build_nutrition_context_block(object())

    asked.assert_called_once_with(ordinary)
    # «Закрыт» без РПП — только факты; «открыт» — полный блок.
    assert (FACTS_ONLY in block) is verdict
    assert ("Белок: в среднем 62% от ориентира." in block) is (not verdict)


# ── e: nothing is read before consent or with the flag off ───────────────────


def test_a_closed_consent_does_not_read_the_profile(doors, monkeypatch) -> None:
    client = doors(profile())
    monkeypatch.setattr(nutrition_context, "_consent_open", lambda bot_user: False)

    assert build_nutrition_context_block(object()) == ""
    assert client.profile_calls == 0


def test_the_flag_off_does_not_read_the_profile(doors, settings) -> None:
    client = doors(profile())
    settings.CONCIERGE_NUTRITION_CONTEXT_ENABLED = False

    assert build_nutrition_context_block(object()) == ""
    assert client.profile_calls == 0


# ── f: an unreadable profile is «не знаем», and never a raise ────────────────

_FAILURES = [
    ("unavailable", NutritionUnavailableError("circuit_open")),
    ("api-error", NutritionAPIError("400")),
    ("unexpected", RuntimeError("boom")),
]


@pytest.mark.parametrize("failure", [f for _, f in _FAILURES], ids=[i for i, _ in _FAILURES])
def test_an_unreadable_profile_is_silence_not_a_raise(doors, failure) -> None:
    client = doors(failure)

    assert build_nutrition_context_block(object()) == ""
    assert client.profile_calls == 1
    assert client.week_calls == 0


def test_an_unconfigured_environment_is_silence(doors, monkeypatch) -> None:
    doors(profile())

    def _unconfigured() -> Any:
        raise RuntimeError("NUTRITION_SERVICE_TOKEN is not set")

    monkeypatch.setattr("apps.integrations.ayla.get_nutrition_client", _unconfigured)

    assert build_nutrition_context_block(object()) == ""


# ── DRF-2766 1в: what the facts are, and «Без чисел» stays stronger ───────────


def _with_day_and_goal(client) -> None:
    from apps.orchestrator import food_history

    client.today.return_value = food_history.TodayDiary(  # type: ignore[attr-defined]
        food_history.Status.OK,
        meals=(food_history.Meal(dish="борщ", calories=147, meal_type="lunch"),),
    )
    client.goal.return_value = SimpleNamespace(  # type: ignore[attr-defined]
        text="похудеть на 5 кг к лету", key="lose", source="free_text"
    )


def test_facts_only_names_the_dishes_and_not_the_goal(doors) -> None:
    client = doors(profile(health_flags={"pregnant": True}))
    _with_day_and_goal(client)

    block = build_nutrition_context_block(object())

    assert "Сегодня в дневнике: борщ (147 ккал)." in block
    assert FACTS_ONLY in block
    assert "похудеть" not in block


def test_twin_an_ordinary_profile_gets_the_goal_and_no_facts_only_line(doors) -> None:
    client = doors(profile())
    _with_day_and_goal(client)

    block = build_nutrition_context_block(object())

    assert "Сегодня в дневнике: борщ (147 ккал)." in block
    assert "похудеть на 5 кг к лету" in block
    assert FACTS_ONLY not in block


def test_numbers_hidden_stays_stronger_than_facts_only(doors, monkeypatch) -> None:
    client = doors(profile(health_flags={"breastfeeding": True}))
    _with_day_and_goal(client)
    monkeypatch.setattr(
        "apps.nutrition_proactive.prefs.get_prefs", lambda bot_user: {"numbers_hidden": True}
    )

    block = build_nutrition_context_block(object())

    assert "Сегодня в дневнике: борщ." in block
    assert FACTS_ONLY in block
    assert nutrition_context.NUMBERS_HIDDEN_INSTRUCTION in block
    assert "ккал" not in block.split(nutrition_context.NUMBERS_HIDDEN_INSTRUCTION, 1)[1]
