"""DRF-1467 — история еды читается у Ayla, и своей копии не появляется.

Владелец: «я хочу, чтобы клиент мог сразу записать сфотканное блюдо и его
разбор в память»; на развилку «копия у себя или чтение у Ayla» — «чтением
из Ayla, копию не делаем» (04.09.2026). Поэтому проверка здесь ровно из
двух половин, и вторая — не рассуждение, а счёт строк.

**Положительная.** Ayla доступна: дневник читается там, где до этого
тикета его не читали. Три поверхности, каждая своим вопросом:

* «что я ел сегодня» — ``personal_surface.render_diary`` называет блюда, а
  не одни калории. До DRF-1467 ``summary.entries`` дёргали ради
  ``bool()`` и выбрасывали, так что на вопрос «что» отвечали «сколько»;
* повторное фото того же блюда — карточка сканера говорит, что оно уже в
  дневнике, вместо «Записать в дневник?» на пустом месте;
* блок питания для модели (``nutrition_context``) несёт сегодняшние
  блюда — на нём вырастет диетолог DRF-1464, а недельный процент белка
  диетологу не пища.

**Отрицательная.** Ayla недоступна: человек слышит честный отказ — и
``MemoryEntry`` не прибавилось **ни на одну строку**. Считаем до и после
на всех трёх поверхностях сразу, потому что «мы же нигде не пишем» — это
ровно то утверждение, которое положено проверять счётом.

Плюс два замка, которые тикет запрещает трогать и которые поэтому
прибиты здесь, а не оставлены на добрую волю:

* ``memory.food.note_meal`` по-прежнему отказывается хранить съеденное;
* кеша нет: два обращения — два похода к Ayla. Кеш с любым TTL — та же
  копия под другим именем, только её труднее найти по запросу на удаление.

Согласия настоящие (``consent.health.grant``), а не подменённый предикат:
HEALTH — ворота, и отрицательное «без согласия не читаем» ничего не стоит,
если с согласием тоже не читалось бы.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from apps.consent import health as health_consent
from apps.consent.services import record_global_consent
from apps.identity.models import BotUser, MemoryEntry
from apps.integrations.ayla import (
    NutritionUnavailableError,
    ProfileResponse,
    SummaryResponse,
    WaterTodayResponse,
)
from apps.orchestrator import food_history, nutrition_context, personal_surface
from apps.orchestrator.food_history import Status, read_today
from apps.orchestrator.memory import food as food_memory

pytestmark = pytest.mark.django_db(transaction=True)

CHANNEL_USER_ID = "1467001"


# ─── fixtures ──────────────────────────────────────────────────────────────


def _entry(dish: str, kcal: Any = 0.0, meal_type: str = "lunch") -> dict[str, Any]:
    """Строка ``FoodLogEntrySerializer`` в том виде, в каком её шлёт Ayla."""
    return {
        "id": str(uuid.uuid4()),
        "dish_name": dish,
        "calories": kcal,
        "protein_g": 12.0,
        "fat_g": 9.0,
        "carbs_g": 30.0,
        "meal_type": meal_type,
        "logged_at": "2026-09-04T10:12:00Z",
    }


def _summary(entries: list[dict[str, Any]] | None = None, **over: Any) -> SummaryResponse:
    payload: dict[str, Any] = dict(
        date="2026-09-04",
        calories_total=1210.0,
        calories_goal=1994,
        protein_g=61.0,
        fat_g=44.0,
        carbs_g=130.0,
        entries=[_entry("Борщ", 320.0), _entry("Омлет", 240.0, "breakfast")]
        if entries is None
        else entries,
        raw={},
    )
    payload.update(over)
    return SummaryResponse(**payload)


def _water() -> WaterTodayResponse:
    return WaterTodayResponse(total_ml=900, norm_ml=2400, entries=[], raw={})


def _profile() -> ProfileResponse:
    return ProfileResponse(
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


def _deficits() -> SimpleNamespace:
    return SimpleNamespace(
        days_observed=5,
        protein_avg_pct_goal=62.4,
        protein_low_streak_days=4,
        hint="",
        fired_keys=[],
        raw={},
    )


class _FakeAyla:
    """Настоящая поверхность клиента и ничего сверх неё.

    Не ``Mock()``: тот отвечает на любой метод правдоподобным объектом, и
    рендер, читающий несуществующее поле, «работал бы», печатая repr.
    Именно такую ошибку тут и ловим, поэтому двойник ручной.
    """

    def __init__(self, *, summary=None, water=None, profile=None, raises=None):
        self._summary = summary
        self._water = water
        self._profile = profile
        self._raises = raises
        self.calls: list[str] = []

    async def _answer(self, name: str, value: Any) -> Any:
        self.calls.append(name)
        if self._raises is not None:
            raise self._raises
        return value

    async def daily_summary(self, *, external_user_id, **kw):
        return await self._answer("daily_summary", self._summary)

    async def get_water_today(self, *, external_user_id, **kw):
        return await self._answer("get_water_today", self._water)

    async def get_profile(self, *, external_user_id, **kw):
        return await self._answer("get_profile", self._profile)


@pytest.fixture
def person(db) -> BotUser:
    """Человек с обеими базами 152-ФЗ: PERSONAL_DATA и HEALTH."""
    from apps.identity.services import resolve_or_create_global_bot_user

    bot_user = resolve_or_create_global_bot_user(
        channel="max", channel_user_id=CHANNEL_USER_ID, chat_id="chat-1467"
    )
    record_global_consent(bot_user, source="test:welcome")
    health_consent.grant(bot_user, document_version=health_consent.HEALTH_CONSENT_DOCUMENT_VERSION)
    # Связка с Ayla обязательна, а не декоративна: ``memory.food`` пишет
    # ``MemoryEntry`` по ``ayla_user_id``, и на несвязанной строке он вообще
    # ничего не пишет. Счётчик на такой строке всегда показывал бы ноль и
    # проверка «копии нет» стала бы проверкой ничего.
    bot_user.ayla_user_id = uuid.uuid4()
    bot_user.save(update_fields=["ayla_user_id"])
    return bot_user


@pytest.fixture
def no_health(db) -> BotUser:
    """Тот же человек, но HEALTH не выдан — ворота закрыты."""
    from apps.identity.services import resolve_or_create_global_bot_user

    bot_user = resolve_or_create_global_bot_user(
        channel="max", channel_user_id="1467002", chat_id="chat-1467-b"
    )
    record_global_consent(bot_user, source="test:welcome")
    return bot_user


@pytest.fixture
def ayla(monkeypatch):
    """Ставит двойник клиента; возвращает фабрику для конкретного сценария."""

    def _install(fake: _FakeAyla) -> _FakeAyla:
        import apps.integrations.ayla as ayla_pkg

        monkeypatch.setattr(ayla_pkg, "get_nutrition_client", lambda: fake)
        return fake

    return _install


@pytest.fixture(autouse=True)
def _context_flag(settings):
    settings.CONCIERGE_NUTRITION_CONTEXT_ENABLED = True


def _memory_rows() -> int:
    """Все строки ``MemoryEntry`` в базе, без единого фильтра.

    Считаем ВСЁ, а не «жёлтые про еду у этого человека»: любой фильтр —
    это догадка о том, как назовут копию, если её всё-таки заведут, и
    промах такой догадки выглядит как зелёный тест. Тест
    ``test_the_counter_really_moves`` доказывает, что счётчик способен
    сдвинуться, иначе всё ниже было бы утверждением ни о чём.
    """
    return MemoryEntry.objects.count()


def _green_fact(bot_user: BotUser) -> MemoryEntry:
    """Контрольная строка — ровно того вида, каким была бы копия."""
    from apps.identity.models import UserPersonalContext

    user_id = bot_user.ayla_user_id
    assert user_id is not None  # фикстура ``person`` связывает строку с Ayla
    upc, _ = UserPersonalContext.objects.get_or_create(user_id=user_id)
    return MemoryEntry.objects.create(
        user_id=user_id,
        personal_context=upc,
        sensitivity_zone=MemoryEntry.SENSITIVITY_GREEN,
        source=MemoryEntry.SOURCE_EXPLICIT,
        provenance=MemoryEntry.PROVENANCE_USER_STATED,
        kind="lifestyle",
        content={"key": "diet", "value": "vegan"},
    )


# ─── 1. положительная половина ─────────────────────────────────────────────


class TestAylaUpTheHistoryIsRead:
    """Ayla отвечает — история доезжает до всех трёх поверхностей."""

    def test_read_today_parses_the_rows(self, person, ayla) -> None:
        ayla(_FakeAyla(summary=_summary()))

        diary = read_today(person)

        assert diary.status is Status.OK
        assert diary.dish_names() == ("Борщ", "Омлет")
        assert diary.meals[0].calories == 320
        assert diary.has_dish("борщ")  # нормализация та же, что у ключей памяти
        assert diary.has_dish("  БОРЩ ")
        assert not diary.has_dish("плов")

    def test_the_diary_answer_names_the_dishes(self, person, ayla) -> None:
        """«что я ел» — про «что». До тикета отвечали одними калориями."""
        ayla(_FakeAyla(summary=_summary(), water=_water(), profile=_profile()))

        reply = personal_surface.render_diary(person)

        assert "Борщ" in reply.text
        assert "Омлет" in reply.text
        # И разбор рядом с блюдом, а не только итог за день.
        assert "320 ккал" in reply.text

    def test_the_scan_card_says_the_dish_is_already_logged(self, person, ayla) -> None:
        from apps.skills.food_scanner.skill import ALREADY_LOGGED_LINE, _format_scan_card

        ayla(_FakeAyla(summary=_summary()))
        scan = SimpleNamespace(
            scan_id="s-1",
            dish_name="борщ",
            confidence=0.9,
            portion_g=350,
            nutrition={"calories": 320},
        )

        card = _format_scan_card(scan, None, read_today(person))

        assert ALREADY_LOGGED_LINE in card

    def test_a_new_dish_gets_no_such_line(self, person, ayla) -> None:
        from apps.skills.food_scanner.skill import ALREADY_LOGGED_LINE, _format_scan_card

        ayla(_FakeAyla(summary=_summary()))
        scan = SimpleNamespace(
            scan_id="s-2",
            dish_name="плов",
            confidence=0.9,
            portion_g=300,
            nutrition={"calories": 500},
        )

        card = _format_scan_card(scan, None, read_today(person))

        # Карточка непустая и про то самое блюдо — иначе «строки нет»
        # было бы правдой и о пустой строке.
        assert "плов" in card
        assert "Записать в дневник?" in card
        assert ALREADY_LOGGED_LINE not in card

    def test_the_model_block_carries_todays_dishes(self, person, ayla, monkeypatch) -> None:
        """То, что увидит диетолог DRF-1464: не только процент белка."""
        monkeypatch.setattr(nutrition_context, "_fetch_deficits", lambda _u: _deficits())
        ayla(_FakeAyla(summary=_summary()))

        block = nutrition_context.build_nutrition_context_block(person)

        assert "Борщ" in block
        assert "Омлет" in block
        assert "320 ккал" in block

    def test_the_block_survives_a_week_that_did_not_come_back(
        self, person, ayla, monkeypatch
    ) -> None:
        """Два независимых чтения: упало одно — второе всё равно доезжает."""
        monkeypatch.setattr(nutrition_context, "_fetch_deficits", lambda _u: None)
        ayla(_FakeAyla(summary=_summary()))

        block = nutrition_context.build_nutrition_context_block(person)

        assert "Борщ" in block


# ─── 2. отрицательная половина ─────────────────────────────────────────────


class TestAylaDownHonestRefusalAndNoCopy:
    """Ayla молчит — честный отказ, и в памяти бота ни одной новой строки."""

    def test_unavailable_is_not_an_empty_day(self, person, ayla) -> None:
        """«Ayla не ответила» и «ты ничего не ел» — разные правды."""
        ayla(_FakeAyla(raises=NutritionUnavailableError("network: ConnectError")))

        diary = read_today(person)

        assert diary.status is Status.UNAVAILABLE
        assert diary.ok is False
        assert diary.is_empty is False  # именно так: пусто ≠ неизвестно
        assert diary.has_dish("борщ") is False

    def test_the_open_circuit_is_used_not_routed_around(self, person, ayla) -> None:
        """Предохранитель клиента — обычный источник недоступности."""
        ayla(_FakeAyla(raises=NutritionUnavailableError("circuit_open")))

        assert read_today(person).status is Status.UNAVAILABLE

    def test_the_person_hears_an_honest_refusal(self, person, ayla) -> None:
        ayla(_FakeAyla(raises=NutritionUnavailableError("http_503")))

        reply = personal_surface.render_diary(person)

        assert reply.text.startswith(personal_surface.DIARY_UNAVAILABLE_TEXT)
        # Ни одного придуманного блюда и ни одной цифры дня.
        assert "Борщ" not in reply.text

    def test_the_scan_card_stays_silent_rather_than_guessing(self, person, ayla) -> None:
        from apps.skills.food_scanner.skill import ALREADY_LOGGED_LINE, _format_scan_card

        ayla(_FakeAyla(raises=NutritionUnavailableError("http_503")))
        scan = SimpleNamespace(
            scan_id="s-3",
            dish_name="борщ",
            confidence=0.9,
            portion_g=350,
            nutrition={"calories": 320},
        )

        card = _format_scan_card(scan, None, read_today(person))

        # Карточка про тарелку — она остаётся ответом на заданный вопрос,
        # и это же доказывает, что дальше мы ищем строку в непустом тексте.
        assert "борщ" in card
        assert "Записать в дневник?" in card
        assert ALREADY_LOGGED_LINE not in card

    def test_the_model_block_invents_nothing(self, person, ayla, monkeypatch) -> None:
        monkeypatch.setattr(nutrition_context, "_fetch_deficits", lambda _u: None)
        ayla(_FakeAyla(raises=NutritionUnavailableError("http_503")))

        assert nutrition_context.build_nutrition_context_block(person) == ""

    def test_the_counter_really_moves(self, person) -> None:
        """Контроль над самим счётчиком.

        Без него «строк не прибавилось» доказывало бы только то, что мы
        умеем не писать в базу, в которую и так ничего бы не записалось —
        классическая пустая отрицательная проверка (DRF-1411).
        """
        before = _memory_rows()

        _green_fact(person)

        assert _memory_rows() == before + 1

    def test_not_one_row_of_food_history_lands_in_memory(self, person, ayla, monkeypatch) -> None:
        """Главная проверка тикета — счётом, а не рассуждением.

        Гоняем все три поверхности при упавшей Ayla и сверяем количество
        ``MemoryEntry`` до и после. Копии нет — ни «на всякий случай», ни
        «на пять минут».
        """
        monkeypatch.setattr(nutrition_context, "_fetch_deficits", lambda _u: None)
        ayla(_FakeAyla(raises=NutritionUnavailableError("http_503")))
        before = _memory_rows()

        read_today(person)
        personal_surface.render_diary(person)
        nutrition_context.build_nutrition_context_block(person)
        food_memory.note_meal(person, dish="борщ")

        assert _memory_rows() == before

    def test_not_one_row_lands_when_ayla_is_up_either(self, person, ayla, monkeypatch) -> None:
        """Успешное чтение — тоже не повод завести строку.

        Отрицательная половина ловит «копию на случай сбоя»; эта — «копию,
        раз уж данные всё равно в руках», которая опаснее, потому что на
        счастливом пути её никто не заметит.
        """
        monkeypatch.setattr(nutrition_context, "_fetch_deficits", lambda _u: _deficits())
        ayla(_FakeAyla(summary=_summary(), water=_water(), profile=_profile()))
        before = _memory_rows()

        read_today(person)
        personal_surface.render_diary(person)
        nutrition_context.build_nutrition_context_block(person)
        food_memory.note_meal(person, dish="борщ")

        assert _memory_rows() == before


# ─── 3. согласие HEALTH — ворота ───────────────────────────────────────────


class TestHealthConsentIsTheGate:
    """Без HEALTH до Ayla дело не доходит вовсе."""

    def test_no_health_consent_no_call(self, no_health, ayla) -> None:
        fake = ayla(_FakeAyla(summary=_summary()))

        diary = read_today(no_health)

        assert diary.status is Status.NO_CONSENT
        assert fake.calls == []  # закрытые ворота не стоят даже одного GET

    def test_withdrawal_puts_the_read_back_to_sleep(self, person, ayla) -> None:
        ayla(_FakeAyla(summary=_summary()))
        assert read_today(person).status is Status.OK

        health_consent.withdraw(person)

        assert read_today(person).status is Status.NO_CONSENT

    def test_a_throwing_consent_read_fails_closed(self, person, ayla, monkeypatch) -> None:
        def _boom(*a, **kw):
            raise RuntimeError("db blip")

        fake = ayla(_FakeAyla(summary=_summary()))
        monkeypatch.setattr("apps.consent.services.has_global_consent", _boom)

        assert read_today(person).status is Status.NO_CONSENT
        assert fake.calls == []


# ─── 4. замки, которые тикет запрещает трогать ─────────────────────────────


class TestTheLocksStayShut:
    def test_note_meal_still_refuses_to_store(self, person) -> None:
        """DRF-1467 не открывает ``note_meal`` — оно и не открылось."""
        outcome = food_memory.note_meal(person, dish="Борщ")

        assert outcome is food_memory.Outcome.DROPPED_SENSITIVE
        assert _memory_rows() == 0

    def test_there_is_no_cache_two_reads_are_two_calls(self, person, ayla) -> None:
        """Кеш — та же копия, только названная иначе. Его нет.

        Проверяется единственным наблюдаемым признаком: второе чтение
        снова идёт к Ayla. Кеш с любым TTL провалил бы это.
        """
        fake = ayla(_FakeAyla(summary=_summary()))

        read_today(person)
        read_today(person)

        assert fake.calls == ["daily_summary", "daily_summary"]


# ─── 5. разбор строк — оборона на каждом поле ──────────────────────────────


class TestParsingIsDefensive:
    """``entries`` — единственная часть ответа, которую клиент отдаёт сырой."""

    def test_a_row_without_a_name_is_dropped_not_filled_in(self) -> None:
        meals = food_history.meals_from_summary(
            _summary(entries=[_entry("Борщ", 320.0), {"id": "x", "calories": 100}])
        )

        assert [m.dish for m in meals] == ["Борщ"]

    def test_junk_shapes_do_not_raise(self) -> None:
        # Контроль: на нормальной форме тот же вызов возвращает строки, так
        # что пустота ниже — свойство входа, а не всегда-пустой функции.
        assert len(food_history.meals_from_summary(_summary())) == 2

        # empty-assert-ok: пустота и есть проверяемое свойство — на этих
        # формах читать нечего, и вопрос ровно в том, что вместо исключения
        # или выдумки возвращается пустой кортеж.
        assert food_history.meals_from_summary(_summary(entries=[])) == ()
        assert food_history.meals_from_summary(SimpleNamespace(entries=None)) == ()
        assert food_history.meals_from_summary(SimpleNamespace(entries=["строка", 7])) == ()
        assert food_history.meals_from_summary(object()) == ()

    def test_calories_are_coerced_and_bounded(self) -> None:
        meals = food_history.meals_from_summary(
            _summary(
                entries=[
                    _entry("А", "не число"),
                    _entry("Б", -50.0),
                    _entry("В", 10**9),
                ]
            )
        )

        assert [m.calories for m in meals] == [0, 0, food_history.MAX_MEAL_KCAL]

    def test_the_day_is_capped(self) -> None:
        meals = food_history.meals_from_summary(
            _summary(entries=[_entry(f"Блюдо {i}", 100.0) for i in range(40)])
        )

        assert len(meals) == food_history.MAX_MEALS

    def test_dish_names_are_cleaned_before_they_reach_a_prompt(self) -> None:
        meals = food_history.meals_from_summary(
            _summary(entries=[_entry("  борщ  с\nпампушками  ", 320.0)])
        )

        assert meals[0].dish == "борщ с пампушками"

    def test_a_name_longer_than_the_cap_is_clipped(self) -> None:
        meals = food_history.meals_from_summary(_summary(entries=[_entry("я" * 500, 10.0)]))

        assert len(meals[0].dish) == food_history.MAX_DISH_CHARS


# ─── 6. пустой день — сказан, а не заполнен ────────────────────────────────


class TestAnEmptyDayIsSaid:
    def test_ayla_answered_with_nothing(self, person, ayla) -> None:
        ayla(_FakeAyla(summary=_summary(entries=[])))

        diary = read_today(person)

        assert diary.status is Status.OK
        assert diary.is_empty is True

    def test_the_push_report_is_unchanged_by_this_ticket(self) -> None:
        """Вечерний пуш не начал перечислять съеденное.

        ``include_entries`` по умолчанию False: незваное сообщение,
        зачитывающее человеку список его блюд, — другой поступок, чем
        ответ на его собственный вопрос, и просили только второй.
        """
        from apps.nutrition_proactive.render import render_daily_report

        text = render_daily_report(_summary(), _water(), _profile())

        # Отчёт отрисовался и полон — иначе «блюд в нём нет» было бы
        # правдой и о пустой строке.
        assert "Итоги дня по питанию." in text
        assert "Калории: 1210 из 1994 ккал." in text
        assert "Борщ" not in text
        assert "Омлет" not in text
