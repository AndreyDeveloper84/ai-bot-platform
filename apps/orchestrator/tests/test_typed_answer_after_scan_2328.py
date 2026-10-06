"""Набранный ответ на «просто напиши, что было» доходит до справочника без модели (DRF-2328).

Живой проход владельца 22.09: фото не распознано, бот сам предложил
«…или просто написать, что было?», человек написал, что ел, — и получил
«Сейчас у меня временные трудности с подключением». Сканер не взводил
никакого ожидания, свободный текст структурным не бывает, поэтому
набранное ушло модели, а модель лежала. Дорога, названная выходом, вела в
ту же стену.

Почему не ``expect_food`` (замер на dev): ``parse_food_text`` принимает что
угодно — «спасибо» → блюдо «спасибо», «запиши меня к мастеру» → блюдо
«меня к мастеру». С взведённым ожиданием любой ответ после нераспознанного
фото ушёл бы в оценку и получил «Не нашла…». А детектор «похоже на еду»
говорит False ровно на фразе владельца. Поэтому отметка мягкая, а судья —
справочник: отвечаем только его карточкой, всё остальное идёт дальше к
модели, как шло.

Узлы:

* сканер ставит отметку на обоих «просто напиши» — на скане и на записи;
* после отметки «гречка» (без граммов — их ярлык DRF-2078 не берёт) даёт
  карточку оценки из детерминированного слоя, до консьержа;
* контроль «не-еда не застревает»: вопрос, «спасибо», неизвестное
  справочнику, недоступный справочник, напиток — ``None``, и ход идёт к
  модели; «Не нашла» не звучит;
* отметка одноразовая, стареет, и структурный путь её не видит;
* без отметки поведение прежнее.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, patch

import pytest

from apps.integrations.ayla import (
    DishEstimate,
    FoodNotRecognizedError,
    NutritionUnavailableError,
)
from apps.orchestrator.nutrition_global import (
    is_structured_nutrition_turn,
    try_handle_structured_nutrition_turn,
)
from apps.skills.base import SkillContext
from apps.skills.food_clarify import text_entry
from apps.skills.food_scanner.skill import NOT_RECOGNIZED_FALLBACK, FoodScannerSkill

pytestmark = pytest.mark.django_db(transaction=True)

KNOWN = {"гречка", "лепешка роти"}


class _Catalogue:
    """Двойник ``internal/food-estimate/`` в форме ответа каталога на dev.

    Форма взята из ``nutrition/views.py::InternalFoodEstimateView`` каталога,
    а не придумана: с DRF-2371 ручка **не отказывает ни на какое имя**.
    Блюдо из справочника — число в ``kcal``; чего в справочнике нет —
    ``matched_dish`` = названное человеком, ``kcal`` = ``None``, а при живой
    модели каталога число приходит отдельно, в ``kcal_ai_estimate``
    (DRF-2761). ``model_estimates=True`` — модель каталога отвечает и на
    «спасибо»: худший случай, ровно тот, от которого стережёт ярлык.
    """

    def __init__(self, *, down: bool = False, model_estimates: bool = False) -> None:
        self.down = down
        self.model_estimates = model_estimates
        self.estimates: list[str] = []

    async def estimate_dish(self, *, external_user_id, dish_name, portion_g=None):
        self.estimates.append(dish_name)
        if self.down:
            raise NutritionUnavailableError("down")
        grams = 100.0 if portion_g is None else float(portion_g)
        known = dish_name in KNOWN
        return DishEstimate(
            matched_dish=dish_name,
            portion_g=grams,
            portion_estimated=portion_g is None,
            kcal=1.2 * grams if known else None,
            protein_g=None,
            fat_g=None,
            carbs_g=None,
            raw={
                "source": "seed_ru" if known else ("ai_estimate" if self.model_estimates else None)
            },
            kcal_ai_estimate=None if known or not self.model_estimates else 0.5 * grams,
        )


@pytest.fixture(autouse=True)
def _nutrition_on(settings):
    settings.NUTRITION_ENABLED = True
    settings.FOOD_PHOTO_SCAN_ENABLED = True


@pytest.fixture(autouse=True)
def _consent():
    with (
        patch(
            "apps.orchestrator.personal_surface.personal_records_consent_open", return_value=True
        ),
        patch("apps.consent.nutrition.diary_is_granted", return_value=True),
    ):
        yield


@pytest.fixture
def catalogue():
    fake = _Catalogue()
    with patch("apps.skills.food_clarify.text_entry.get_nutrition_client", return_value=fake):
        yield fake


def _bot_user() -> Mock:
    bot_user = Mock()
    bot_user.channel = "max"
    bot_user.channel_user_id = "2328"
    return bot_user


def _conversation(skill_state: dict[str, Any] | None = None, **extra: Any) -> Any:
    """Двойник разговора — как в соседнем узле DRF-2078: ``skill_state`` словарём."""
    return SimpleNamespace(id="conv-2328", skill_state=dict(skill_state or {}), **extra)


def _mark(conversation) -> None:
    ctx = SkillContext(conversation=conversation, bot_user=_bot_user(), message_text="")
    text_entry.mark_after_scan(ctx)


def _turn(text: str, conversation):
    return try_handle_structured_nutrition_turn(
        text=text,
        attachments=None,
        bot_user=_bot_user(),
        conversation=conversation,
        trace_id="t-2328",
    )


# ─── сканер ставит отметку на обоих «просто напиши» ─────────────────────────


def test_scan_not_recognized_marks_the_conversation() -> None:
    conversation = _conversation(last_photo_bytes=b"jpeg")
    ctx = SkillContext(
        conversation=conversation, bot_user=_bot_user(), message_text="", has_attachments=True
    )
    client = Mock()

    async def _scan(**kwargs):
        raise FoodNotRecognizedError("low_confidence")

    client.scan_photo = _scan
    with patch("apps.skills.food_scanner.skill.get_nutrition_client", return_value=client):
        result = FoodScannerSkill().handle(ctx)

    assert result.reply_text == NOT_RECOGNIZED_FALLBACK
    assert conversation.skill_state["food_text"]["after_scan"] is True


def test_log_not_recognized_marks_the_conversation_too() -> None:
    conversation = _conversation(skill_state={"food_scan_grams": {"scan-1": {"grams": 250}}})
    ctx = SkillContext(
        conversation=conversation, bot_user=_bot_user(), message_text="cb:food:to_diary:scan-1"
    )
    client = Mock()

    async def _log(**kwargs):
        raise FoodNotRecognizedError("nutrition_missing")

    client.log_meal = _log
    with (
        patch("apps.skills.food_scanner.skill.get_nutrition_client", return_value=client),
        patch("apps.conversations.services.write_skill_state"),
    ):
        result = FoodScannerSkill().handle(ctx)

    assert result.reply_text == NOT_RECOGNIZED_FALLBACK
    assert conversation.skill_state["food_text"]["after_scan"] is True


# ─── набранная еда доходит до справочника без модели ────────────────────────


def test_typed_food_after_the_scan_gets_the_estimate_card(catalogue) -> None:
    conversation = _conversation()
    _mark(conversation)

    result = _turn("гречка", conversation)

    assert result is not None
    assert result.meta["reply_kind"] == "food_text_estimate_card"
    assert catalogue.estimates == ["гречка"]


def test_without_the_mark_the_same_text_still_goes_to_the_model(catalogue) -> None:
    """Положительная стража: ярлык берёт только ответ на вопрос сканера."""
    assert _turn("гречка", _conversation()) is None
    assert catalogue.estimates == []


# ─── не-еда не застревает: всё, кроме карточки, — дальше к модели ───────────


@pytest.mark.parametrize("text", ["а почему не распозналось?", "это что, нельзя?"])
def test_a_question_is_not_sent_to_the_reference_book(catalogue, text) -> None:
    conversation = _conversation()
    _mark(conversation)

    assert _turn(text, conversation) is None
    assert catalogue.estimates == []


@pytest.mark.parametrize("model_estimates", [False, True], ids=["no-number", "model-number"])
@pytest.mark.parametrize("text", ["спасибо", "запиши меня к мастеру", "не помню"])
def test_what_the_reference_book_does_not_know_goes_on_to_the_model(text, model_estimates) -> None:
    """Каталог отвечает карточкой на любое имя — но число справочника есть только у еды.

    Без этой стражи «спасибо» после нераспознанного фото получило бы
    «Я распознала так: спасибо» — а с живой моделью каталога ещё и калории.

    DRF-2768: ярлык отвечает теперь любой карточкой, в том числе «Записать без
    расчёта?», поэтому не-еду отсекает закрытый список ДО каталога — и
    запроса оценки нет вовсе (было: один запрос и отказ по числу).
    """
    conversation = _conversation()
    _mark(conversation)
    fake = _Catalogue(model_estimates=model_estimates)

    with patch("apps.skills.food_clarify.text_entry.get_nutrition_client", return_value=fake):
        assert _turn(text, conversation) is None
    assert fake.estimates == []
    assert "food_text" not in conversation.skill_state, "неотвеченная карточка оставила состояние"


def test_a_legacy_refusal_goes_on_to_the_model_too() -> None:
    """400 FOOD_NOT_RECOGNIZED каталога до DRF-2371 — тоже не ответ ярлыка."""
    conversation = _conversation()
    _mark(conversation)

    class _Legacy(_Catalogue):
        async def estimate_dish(self, **kwargs):
            raise FoodNotRecognizedError("not_in_reference")

    with patch("apps.skills.food_clarify.text_entry.get_nutrition_client", return_value=_Legacy()):
        assert _turn("гречка", conversation) is None


def test_an_unavailable_reference_book_goes_on_to_the_model() -> None:
    conversation = _conversation()
    _mark(conversation)
    down = _Catalogue(down=True)

    with patch("apps.skills.food_clarify.text_entry.get_nutrition_client", return_value=down):
        assert _turn("гречка", conversation) is None
    assert down.estimates == ["гречка"]


def test_a_drink_is_left_to_the_water_path(catalogue) -> None:
    conversation = _conversation()
    _mark(conversation)

    assert _turn("кофе с молоком", conversation) is None
    assert catalogue.estimates == []


def test_nothing_changes_when_nutrition_is_off(settings, catalogue) -> None:
    settings.NUTRITION_ENABLED = False
    conversation = _conversation()
    _mark(conversation)

    assert _turn("гречка", conversation) is None
    assert catalogue.estimates == []


# ─── отметка: одноразовая, стареет, структурный путь её не видит ────────────


def test_the_mark_is_spent_by_the_first_reply(catalogue) -> None:
    """Первым — вопрос: он до справочника не доходит и состояния не пишет.

    Будь первым «спасибо», состояние стёрла бы неотвеченная карточка, и
    узел зеленел бы и без расхода отметки.
    """
    conversation = _conversation()
    _mark(conversation)

    assert _turn("а почему не распозналось?", conversation) is None
    assert _turn("гречка", conversation) is None
    assert catalogue.estimates == []


def test_a_stale_mark_is_ignored(catalogue) -> None:
    conversation = _conversation()
    old = datetime.now(timezone.utc) - timedelta(seconds=text_entry.PENDING_TTL_SECONDS + 5)
    conversation.skill_state["food_text"] = {"after_scan": True, "at": old.isoformat()}

    assert _turn("гречка", conversation) is None
    assert catalogue.estimates == []


def test_the_mark_does_not_make_free_text_structured() -> None:
    """Иначе вопрос человека забрал бы навык еды — ровно ловушка ``expect_food``."""
    conversation = _conversation()
    _mark(conversation)

    assert text_entry.has_pending_text_entry(conversation) is False
    assert text_entry.claims_text(conversation, "спасибо") is False
    assert (
        is_structured_nutrition_turn(
            text="спасибо", has_attachments=False, conversation=conversation
        )
        is False
    )
