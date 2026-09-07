"""DRF-1464 T6 — строка наблюдения диетолога при открытии дневника.

Owner decision Q-NUTRITION-05 (Б с поправкой окна): строка прошенная —
не тратит недельный лимит ``coach_hint`` и не накручивает ignore-стрик,
но у неё свой предел: не чаще раза в сутки и не повторять неизменившееся
наблюдение. Что прибито здесь, в порядке цены ошибки:

``TestDiaryIntegration`` — обязательная отрицательная пара тикета: два
открытия дневника подряд → строка пришла один раз, а дневник без строки
байт-в-байт тот же, что был до T6.

``TestLadder`` — гейты в порядке лестницы: флаг, HEALTH, sensitive-
периметр (тот же ``remarks_suppressed``, что в T5), цель, триггер,
outbound-гард. «Нет триггера — нет строки».

``TestOwnLimit`` — свой предел: вторая сутки с изменившимся содержимым →
строка снова допустима; то же содержимое → пропуск в любые сутки.

``TestJournal`` — маркер прошенного в outbox: запись есть, а
``weekly_cap_reason`` и ``surface_ignored_streak`` её не видят.

``TestWelcomeOutsideLimits`` — OPEN_DECISIONS §39: приветственное слово
после выдачи согласия показывается, но находится ВНЕ системы лимитов.
Обязательный сценарий приёмки — ВТОРОЙ заход в те же сутки, а не первый:
первый зелёный при любой реализации и сам по себе не доказывает ничего.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from typing import Any

import pytest

from apps.consent import health as health_consent
from apps.consent.services import record_global_consent
from apps.identity.models import BotUser
from apps.integrations.ayla import ProfileResponse, SummaryResponse, WaterTodayResponse
from apps.nutrition_coach import copy as coach_copy
from apps.nutrition_coach.goals import Goal
from apps.nutrition_coach.history import DayPicture, WeekPicture, WeekStatus
from apps.nutrition_proactive import antinag, coach, prefs
from apps.orchestrator import personal_surface
from apps.orchestrator.coach_observation import (
    OBSERVATION_STATE_KEY,
    SURFACE,
    Cadence,
    Observation,
    decide_observation,
    persist_observation,
)
from apps.orchestrator.food_history import Meal, Status, TodayDiary

pytestmark = pytest.mark.django_db(transaction=True)

#: 12:00 в Москве — «сегодня» для предела «раз в сутки»: 2026-08-23.
NOON = datetime(2026, 8, 23, 9, 0, tzinfo=dt_timezone.utc)
NEXT_DAY = NOON + timedelta(days=1)

SLEEP_GOAL = Goal(key="sleep_better", text=None)


# ─── fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def coach_enabled(settings) -> None:
    """Флаг — первый гейт; открыт по умолчанию, закрывается явно в тесте про него."""
    settings.NUTRITION_COACH_ENABLED = True


def _person(uid: str, *, health: bool = True) -> BotUser:
    """Человек глобального пути с базами 152-ФЗ: PERSONAL_DATA всегда,
    HEALTH — по параметру (ворота наблюдения)."""
    from apps.identity.services import resolve_or_create_global_bot_user

    bot_user = resolve_or_create_global_bot_user(
        channel="max", channel_user_id=uid, chat_id=f"{uid}-chat"
    )
    record_global_consent(bot_user, source="test:welcome")
    if health:
        health_consent.grant(
            bot_user, document_version=health_consent.HEALTH_CONSENT_DOCUMENT_VERSION
        )
    bot_user.ayla_user_id = uuid.uuid4()
    bot_user.save(update_fields=["ayla_user_id"])
    return bot_user


def _profile(**over: Any) -> ProfileResponse:
    payload: dict[str, Any] = dict(
        gender="female",
        age=31,
        height_cm=168,
        weight_kg=62,
        goal="lose",
        daily_kcal=1994,
        protein_g=128,
        fat_g=66,
        carbs_g=221,
        water_ml=2400,
        bmr=1400,
        health_flags={},
        disclaimer_acked=None,
        raw={},
    )
    payload.update(over)
    return ProfileResponse(**payload)


def _dinner_day(offset: int) -> DayPicture:
    from datetime import date

    dinner = Meal(
        dish="Ужин",
        calories=400,
        meal_type="dinner",
        logged_at="2026-09-04T18:30:00Z",  # 21:30 MSK — поздний
    )
    return DayPicture(
        date=(date.today() - timedelta(days=offset)).isoformat(),
        diary=TodayDiary(Status.OK, (dinner,)),
    )


def _late_dinner_week(days: int = 3) -> WeekPicture:
    return WeekPicture(
        status=WeekStatus.OK,
        days=tuple(_dinner_day(i) for i in range(days)),
    )


def _goal_reader(goal: Goal | None):
    return lambda _bot_user: goal


def _week_reader(week: WeekPicture):
    return lambda _bot_user: week


def _decide(person: BotUser, **kwargs: Any) -> Observation | None:
    defaults: dict[str, Any] = dict(
        profile=_profile(),
        now_utc=NOON,
        fetch_goal=_goal_reader(SLEEP_GOAL),
        fetch_history=_week_reader(_late_dinner_week()),
    )
    defaults.update(kwargs)
    return decide_observation(person, **defaults)


# ─── the ladder ─────────────────────────────────────────────────────────────


class TestLadder:
    def test_the_full_path_yields_the_observation(self) -> None:
        obs = _decide(_person("due-1"))
        assert obs is not None
        assert obs.text == coach_copy.OBSERVATION_TEXTS["late_dinner"]
        assert obs.content_key == "late_dinner:3"
        assert obs.local_date == "2026-08-23"

    def test_flag_off_means_no_line(self, settings) -> None:
        person = _person("flag-1")
        # Контроль присутствия: при открытом флаге тот же человек строку
        # получает — иначе «нет строки» доказывало бы сломанный путь.
        assert _decide(person) is not None
        settings.NUTRITION_COACH_ENABLED = False
        assert _decide(person) is None

    def test_no_health_consent_means_no_line(self) -> None:
        assert _decide(_person("gate-1", health=False)) is None

    def test_sensitive_perimeter_means_no_line(self) -> None:
        person = _person("sens-1")
        sensitive = _profile(health_flags={"eating_disorder": True})
        assert _decide(person, profile=sensitive) is None

    def test_an_unreadable_profile_reads_as_silence(self) -> None:
        assert _decide(_person("sens-2"), profile=None) is None

    def test_no_goal_means_no_line(self) -> None:
        assert _decide(_person("goal-1"), fetch_goal=_goal_reader(None)) is None

    def test_no_trigger_means_no_line(self) -> None:
        person = _person("trig-1")
        # Контроль присутствия: с неделей, где паттерн есть, строка есть.
        assert _decide(person) is not None
        assert _decide(person, fetch_history=_week_reader(_late_dinner_week(days=2))) is None

    def test_an_unavailable_week_means_no_line(self) -> None:
        hole = WeekPicture(status=WeekStatus.UNAVAILABLE, days=(_dinner_day(0),))
        assert _decide(_person("trig-2"), fetch_history=_week_reader(hole)) is None

    def test_a_blocked_template_is_silence_and_no_journal(self, monkeypatch) -> None:
        from apps.orchestrator.safety.outbound import OutboundVerdict

        person = _person("guard-1")
        # Контроль присутствия, на тех же данных и раньше: журнал этого
        # человека способен прирасти. Дата/ключ контроля — не сегодняшние,
        # чтобы свой предел не молчал за гард.
        persist_observation(
            person,
            Observation(text="контроль", content_key="control:0", local_date="2026-08-22"),
            now_utc=NOON,
        )
        stored = prefs.get_prefs(BotUser.all_tenants.get(pk=person.pk))
        assert len(prefs.outbox_entries(stored)) == 1

        monkeypatch.setattr(
            "apps.orchestrator.coach_observation.evaluate_outbound",
            lambda _text: OutboundVerdict(allowed=False, text="", categories=("nag",)),
        )
        assert _decide(person) is None
        stored = prefs.get_prefs(BotUser.all_tenants.get(pk=person.pk))
        # Тишина гарда — без новой записи: осталась ровно контрольная.
        assert prefs.outbox_entries(stored) == [
            {
                "surface": "coach_hint",
                "sent_at": NOON.isoformat(),
                "solicited": True,
            }
        ]


# ─── the own limit ──────────────────────────────────────────────────────────


class TestOwnLimit:
    def test_the_same_day_repeat_is_skipped(self) -> None:
        """Раз в сутки: решение + журнал, и тот же день строки больше нет."""
        person = _person("limit-1")
        obs = _decide(person)
        assert obs is not None
        persist_observation(person, obs, now_utc=NOON)
        assert _decide(person) is None

    def test_the_next_day_with_changed_content_may_speak(self) -> None:
        """Вторая сутки + неделя сдвинулась (дней с паттерном стало больше)
        → содержимое другое, строка снова допустима."""
        person = _person("limit-2")
        obs = _decide(person)
        assert obs is not None
        persist_observation(person, obs, now_utc=NOON)
        again = _decide(
            person,
            now_utc=NEXT_DAY,
            fetch_history=_week_reader(_late_dinner_week(days=4)),
        )
        assert again is not None
        assert again.content_key == "late_dinner:4"
        assert again.local_date == "2026-08-24"

    def test_an_unchanged_observation_is_not_repeated(self) -> None:
        """Тот же ключ содержимого — пропуск, даже на другие сутки:
        «не повторять неизменившееся наблюдение»."""
        person = _person("limit-3")
        obs = _decide(person)
        assert obs is not None
        persist_observation(person, obs, now_utc=NOON)
        assert _decide(person, now_utc=NEXT_DAY) is None

    def test_a_different_trigger_is_new_content(self) -> None:
        person = _person("limit-4")
        obs = _decide(person)
        assert obs is not None
        persist_observation(person, obs, now_utc=NOON)
        breakfast_goal = Goal(key="more_energy", text=None)
        breakfast = Meal(dish="Омлет", calories=240, meal_type="breakfast")
        week = WeekPicture(
            status=WeekStatus.OK,
            days=tuple(
                DayPicture(date=day.date, diary=TodayDiary(Status.OK, (breakfast,)))
                for day in _late_dinner_week().days
            ),
        )
        again = _decide(
            person,
            now_utc=NEXT_DAY,
            fetch_goal=_goal_reader(breakfast_goal),
            fetch_history=_week_reader(week),
        )
        assert again is not None
        assert again.content_key == "breakfasts:3"


# ─── the journal ────────────────────────────────────────────────────────────


class TestJournal:
    def test_the_shown_observation_is_journaled_as_solicited(self) -> None:
        person = _person("journal-1")
        obs = _decide(person)
        assert obs is not None
        persist_observation(person, obs, now_utc=NOON)

        stored = prefs.get_prefs(BotUser.all_tenants.get(pk=person.pk))
        assert prefs.outbox_entries(stored) == [
            {
                "surface": "coach_hint",
                "sent_at": NOON.isoformat(),
                "solicited": True,
            }
        ]
        assert stored[OBSERVATION_STATE_KEY] == {
            "date": "2026-08-23",
            "key": "late_dinner:3",
        }

    def test_the_budget_and_the_streak_do_not_see_it(self) -> None:
        person = _person("journal-2")
        obs = _decide(person)
        assert obs is not None
        persist_observation(person, obs, now_utc=NOON)

        stored = prefs.get_prefs(BotUser.all_tenants.get(pk=person.pk))
        assert prefs.weekly_cap_reason(stored, surface=SURFACE, now_utc=NOON) is None
        assert antinag.surface_ignored_streak(person, stored, surface=SURFACE) == 0

    def test_the_surface_name_matches_the_proactive_planner(self) -> None:
        """Одна поверхность — два вида сообщений; различие несёт маркер,
        а не второе имя, поэтому литералы прибиты друг к другу."""
        assert SURFACE == coach.SURFACE


# ─── through render_diary ───────────────────────────────────────────────────


class _FakeAyla:
    """Ручной двойник клиента (та же дисциплина, что в test_personal_surface:
    только настоящая поверхность, никакого Mock)."""

    def __init__(self, *, summary=None, water=None, profile=None):
        self._summary = summary
        self._water = water
        self._profile = profile

    async def daily_summary(self, *, external_user_id, **kw):
        return self._summary

    async def get_water_today(self, *, external_user_id, **kw):
        return self._water

    async def get_profile(self, *, external_user_id, **kw):
        return self._profile


def _install_ayla(monkeypatch, fake: _FakeAyla) -> None:
    import apps.integrations.ayla as ayla_pkg

    monkeypatch.setattr(ayla_pkg, "get_nutrition_client", lambda: fake)


def _install_coach_reads(monkeypatch, week: WeekPicture | None = None) -> None:
    monkeypatch.setattr("apps.nutrition_coach.goals.active_goal", lambda _u: SLEEP_GOAL)
    monkeypatch.setattr(
        "apps.nutrition_coach.history.week_picture",
        lambda _u: week if week is not None else _late_dinner_week(),
    )


def _diary_summary() -> SummaryResponse:
    return SummaryResponse(
        date="2026-08-23",
        calories_total=1210.0,
        calories_goal=1994,
        protein_g=61.0,
        fat_g=44.0,
        carbs_g=130.0,
        entries=[{"id": "e1"}],
        raw={},
    )


def _diary_water() -> WaterTodayResponse:
    return WaterTodayResponse(total_ml=900, norm_ml=2400, entries=[{"id": "w1"}], raw={})


class TestDiaryIntegration:
    def test_opening_the_diary_twice_shows_the_line_once(self, monkeypatch) -> None:
        """Обязательная отрицательная пара: два открытия подряд → одна строка."""
        person = _person("int-1")
        _install_ayla(
            monkeypatch,
            _FakeAyla(summary=_diary_summary(), water=_diary_water(), profile=_profile()),
        )
        _install_coach_reads(monkeypatch)
        line = coach_copy.OBSERVATION_TEXTS["late_dinner"]

        first = personal_surface.render_diary(person)
        assert first.text.endswith(line)
        # Чипы дневника строка не съедает — клавиатура прежняя.
        first_buttons = (first.action_data or {}).get("buttons") or []
        assert first_buttons

        second = personal_surface.render_diary(person)
        assert line not in second.text
        # Байт-в-байт: без строки дневник — ровно тот же текст и те же чипы.
        assert second.text == first.text[: -len(line) - 2]
        assert second.action_data == first.action_data

    def test_the_diary_is_byte_identical_when_no_line_is_due(self, monkeypatch, settings) -> None:
        person = _person("int-2")
        _install_ayla(
            monkeypatch,
            _FakeAyla(summary=_diary_summary(), water=_diary_water(), profile=_profile()),
        )
        # Контроль присутствия, на тех же данных и раньше: поверхность
        # жива — при положенной строке она показывается и журналируется.
        _install_coach_reads(monkeypatch)
        due = personal_surface.render_diary(person)
        assert coach_copy.OBSERVATION_TEXTS["late_dinner"] in due.text
        stored = prefs.get_prefs(BotUser.all_tenants.get(pk=person.pk))
        assert len(prefs.outbox_entries(stored)) == 1

        settings.NUTRITION_COACH_ENABLED = False
        without_coach = personal_surface.render_diary(person)

        settings.NUTRITION_COACH_ENABLED = True
        monkeypatch.setattr("apps.nutrition_coach.goals.active_goal", lambda _u: None)
        no_line = personal_surface.render_diary(person)

        assert no_line.text == without_coach.text
        assert no_line.action_data == without_coach.action_data
        # Ни ветка «флаг закрыт», ни ветка «строка не положена» ничего в
        # журнал не добавила: осталась ровно контрольная запись.
        stored = prefs.get_prefs(BotUser.all_tenants.get(pk=person.pk))
        assert len(prefs.outbox_entries(stored)) == 1

    def test_a_broken_observation_path_never_breaks_the_diary(self, monkeypatch) -> None:
        person = _person("int-3")
        _install_ayla(
            monkeypatch,
            _FakeAyla(summary=_diary_summary(), water=_diary_water(), profile=_profile()),
        )

        def _explode(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("coach layer on fire")

        monkeypatch.setattr("apps.orchestrator.coach_observation.decide_observation", _explode)
        reply = personal_surface.render_diary(person)
        # Дневник отвечает как ни в чём не бывало — строка никогда не стоит
        # потерянного ответа на заданный вопрос.
        assert "1210" in reply.text


# ─── §39: приветственное слово вне системы лимитов ─────────────────────────


class TestWelcomeOutsideLimits:
    """Решение владельца §39 (07.09): «не считать это слотом диетолога».

    Возврат к дневнику после выдачи согласия (§37 п.5) отрисовывает дневник
    и может показать строку наблюдения. Владелец решил, что эта строка —
    отклик на действие человека, а не наблюдение, и потому лимитов не
    тратит: суточный слот остаётся целым для захода, который человек
    сделает сам.
    """

    def test_the_welcome_line_is_shown_and_the_slot_survives(self, monkeypatch) -> None:
        """Сценарий приёмки §39 целиком, и решает его ВТОРОЙ заход.

        непустая неделя → приветственное слово показано → второй заход в те
        же сутки → настоящее наблюдение пришло, слот не был потрачен.
        """
        person = _person("w39-1")
        _install_ayla(
            monkeypatch,
            _FakeAyla(summary=_diary_summary(), water=_diary_water(), profile=_profile()),
        )
        _install_coach_reads(monkeypatch)
        line = coach_copy.OBSERVATION_TEXTS["late_dinner"]

        welcome = personal_surface.render_diary(person, cadence=Cadence.UNTRACKED)
        assert welcome.text.endswith(line)

        # Потолок не поставлен: участок лимитов не тронут ни на запись...
        stored = prefs.get_prefs(BotUser.all_tenants.get(pk=person.pk))
        assert OBSERVATION_STATE_KEY not in stored

        # ...ни, следовательно, на чтение — человек приходит сам в те же
        # сутки и получает СВОЁ наблюдение, а не тишину.
        own = personal_surface.render_diary(BotUser.all_tenants.get(pk=person.pk))
        assert own.text.endswith(line)

        # И вот теперь слот потрачен — обычный заход ведёт себя как прежде.
        # Дату не прибиваем: render_diary идёт по реальному «сейчас», в него
        # now_utc не передаётся. Прибит ключ содержания и сам факт отметки.
        stored = prefs.get_prefs(BotUser.all_tenants.get(pk=person.pk))
        assert stored[OBSERVATION_STATE_KEY]["key"] == "late_dinner:3"
        assert stored[OBSERVATION_STATE_KEY]["date"]

        # И третий заход в те же сутки уже молчит — потолок работает как
        # работал, §39 снял слот только с приветствия.
        third = personal_surface.render_diary(BotUser.all_tenants.get(pk=person.pk))
        assert line not in third.text

    def test_the_first_open_alone_proves_nothing(self, monkeypatch) -> None:
        """Страж НЕДОСТАТОЧНОСТИ, а не поведения.

        Проверка, ограниченная первым заходом, проходит одинаково и при
        верной реализации §39, и при той, что тратит слот: обе показывают
        строку. Тест прибивает именно эту неразличимость, чтобы следующий
        исполнитель не «проверил §39» первым заходом и не успокоился.
        """
        line = coach_copy.OBSERVATION_TEXTS["late_dinner"]
        seen = []
        for uid, cadence in (("w39-2a", Cadence.UNTRACKED), ("w39-2b", Cadence.TRACKED)):
            person = _person(uid)
            _install_ayla(
                monkeypatch,
                _FakeAyla(summary=_diary_summary(), water=_diary_water(), profile=_profile()),
            )
            _install_coach_reads(monkeypatch)
            first = personal_surface.render_diary(person, cadence=cadence)
            seen.append(first.text)

        # Первые заходы совпадают буква в букву — по ним §39 неотличим от
        # его нарушения. Отличает только второй заход, см. тест выше.
        assert seen[0].endswith(line)
        assert seen[0] == seen[1]

    def test_the_welcome_is_still_journaled(self, monkeypatch) -> None:
        """§39 про лимиты, а не про журнал: «журналируется всё» в силе.

        Иначе мы потеряли бы способ узнать, что приветствие вообще уходило.
        """
        person = _person("w39-3")
        _install_ayla(
            monkeypatch,
            _FakeAyla(summary=_diary_summary(), water=_diary_water(), profile=_profile()),
        )
        _install_coach_reads(monkeypatch)

        personal_surface.render_diary(person, cadence=Cadence.UNTRACKED)

        stored = prefs.get_prefs(BotUser.all_tenants.get(pk=person.pk))
        entries = prefs.outbox_entries(stored)
        assert len(entries) == 1
        assert entries[0].get("surface") == SURFACE
        assert entries[0].get("solicited") is True

    def test_being_outside_limits_grants_no_permission(self) -> None:
        """Вне лимитов — не значит вне ворот.

        Приветствие не даёт права сказать то, чего нельзя было бы сказать
        обычным заходом: остальная лестница проходится одинаково.
        """
        # Контроль присутствия: на тех же данных с HEALTH строка есть.
        assert _decide(_person("w39-4"), cadence=Cadence.UNTRACKED) is not None
        assert _decide(_person("w39-5", health=False), cadence=Cadence.UNTRACKED) is None
        assert (
            _decide(
                _person("w39-6"),
                cadence=Cadence.UNTRACKED,
                fetch_goal=_goal_reader(None),
            )
            is None
        )
        assert (
            _decide(
                _person("w39-7"),
                cadence=Cadence.UNTRACKED,
                profile=_profile(health_flags={"eating_disorder": True}),
            )
            is None
        )

    def test_the_default_is_tracked(self, monkeypatch) -> None:
        """Умолчание — обычный заход.

        Сторона, которая ничего не знает про §39, не может случайно
        оказаться вне лимитов: это и есть причина, по которой ``T6``
        безопасен в любом порядке слияния с DRF-1547.
        """
        person = _person("w39-8")
        _install_ayla(
            monkeypatch,
            _FakeAyla(summary=_diary_summary(), water=_diary_water(), profile=_profile()),
        )
        _install_coach_reads(monkeypatch)

        personal_surface.render_diary(person)

        stored = prefs.get_prefs(BotUser.all_tenants.get(pk=person.pk))
        assert OBSERVATION_STATE_KEY in stored

    def test_the_limit_state_lives_only_in_the_cadence_section(self) -> None:
        """Страж на будущее: второй лимит обязан лечь в тот же участок.

        Категория :class:`Cadence` держит §39 ровно до тех пор, пока
        решение «считается ли это событием» принимается в ОДНОМ месте.
        Лимит, положенный мимо участка, отменит §39 молча — поэтому
        участок прибит здесь, а не оставлен на дисциплину.
        """
        import ast
        import inspect

        from apps.orchestrator import coach_observation

        tree = ast.parse(inspect.getsource(coach_observation))
        touching = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            for inner in ast.walk(node):
                if isinstance(inner, ast.Name) and inner.id == "OBSERVATION_STATE_KEY":
                    touching.add(node.name)

        assert touching == {"_cadence_blocks", "_cadence_record"}, (
            "Состояние лимита читается или пишется вне участка каданса: "
            f"{sorted(touching)}. Новый лимит кладётся в _cadence_blocks / "
            "_cadence_record, иначе приветственное слово §39 снова начнёт "
            "тратить слот."
        )
