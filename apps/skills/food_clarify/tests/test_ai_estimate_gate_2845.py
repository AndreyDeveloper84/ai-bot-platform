"""DRF-2845 — чат и Mini App спрашивают согласие ПЕРЕД каждым вызовом каталога.

Каталог согласия не видит: бот читает его сам и передаёт ``ai_estimate_allowed``
в оценку и в запись. Здесь проверяется сквозной путь — что в каталог УШЛО, —
на настоящем реестре согласий: выдача и отзыв меняют следующий же вызов.

* f1 — чат, одно блюдо: нет согласия → ``False``; выдано → ``True``; отозвано
  → снова ``False``;
* f2 — чат, несколько позиций: вопрос задаётся на каждую позицию;
* f3 — чат, запись: согласие отозвано между карточкой и «В дневник» — запись
  уходит с ``False``;
* f4 — механизм выключен: всем ``True``, как до листа;
* f5 — Mini App: оценка и запись несут то же поле;
* f6 — справочник и дневник без согласия работают: карточка и запись есть.
"""

# ruff: noqa: F811 -- fixtures are imported by name from the neighbouring test module
from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from apps.consent import ai_food_estimation
from apps.identity.models import BotUser
from apps.miniapp_api.tests.test_food_text_2091 import (  # noqa: F401 — фикстуры
    LOG_BODY,
    _bot_token,
    _patch_client,
    _post,
)
from apps.skills.base import SkillContext
from apps.skills.food_clarify import text_entry
from apps.skills.food_clarify.skill import FoodClarifySkill
from apps.skills.food_clarify.tests.test_text_entry import _Catalogue
from apps.skills.food_clarify.text_entry import ParsedFood
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

SOURCE = "test:2845"


@pytest.fixture(autouse=True)
def _nutrition_on(settings):
    settings.NUTRITION_ENABLED = True
    settings.MAX_BOT_TENANT_SLUG = "ai-gate-2845"


@pytest.fixture(autouse=True)
def diary_open():
    """Ворота дневника открыты: предмет узлов — согласие на ИИ-оценку, не они."""
    with (
        patch(
            "apps.orchestrator.personal_surface.personal_records_consent_open", return_value=True
        ),
        patch("apps.consent.nutrition.diary_is_granted", return_value=True),
    ):
        yield


@pytest.fixture
def required(settings):
    settings.AI_FOOD_ESTIMATION_CONSENT_REQUIRED = True


@pytest.fixture
def bot_user(db) -> BotUser:
    tenant = Tenant.objects.create(slug="ai-gate-2845", name="AI gate 2845")
    return BotUser.all_tenants.create(
        tenant=tenant, channel="max", channel_user_id="92846", display_name="Клиент"
    )


@pytest.fixture
def conversation():
    return SimpleNamespace(id="conv-2845", skill_state={})


def _grant(bot_user: BotUser) -> None:
    assert ai_food_estimation.grant(
        bot_user,
        document_version=ai_food_estimation.AI_FOOD_ESTIMATION_DOCUMENT_VERSION,
        source=SOURCE,
    )


class _Recording(_Catalogue):
    """Каталог, который помнит, что ему разрешили, — по каждому вызову."""

    def __init__(self) -> None:
        super().__init__()
        self.estimate_allowed: list[Any] = []
        self.log_allowed: list[Any] = []

    async def estimate_dish(
        self, *, external_user_id, dish_name, portion_g=None, ai_estimate_allowed=None
    ):
        self.estimate_allowed.append(ai_estimate_allowed)
        return await super().estimate_dish(
            external_user_id=external_user_id, dish_name=dish_name, portion_g=portion_g
        )

    async def log_meal(self, **kwargs):
        self.log_allowed.append(kwargs.get("ai_estimate_allowed"))
        return await super().log_meal(**kwargs)


def _turn(bot_user: BotUser, conversation, text: str, catalogue: _Catalogue):
    context = SkillContext(conversation=conversation, bot_user=bot_user, message_text=text)
    skill = FoodClarifySkill()
    assert skill.matches(context), text
    with patch("apps.skills.food_clarify.text_entry.get_nutrition_client", return_value=catalogue):
        return skill.handle(context)


def _card(bot_user: BotUser, conversation, catalogue: _Catalogue):
    _turn(bot_user, conversation, "борщ 300г", catalogue)
    return _turn(bot_user, conversation, "cb:food:diary", catalogue)


class TestTheChat:
    def test_f1_no_consent_then_granted_then_withdrawn(
        self, bot_user, conversation, required
    ) -> None:
        catalogue = _Recording()

        _card(bot_user, conversation, catalogue)
        _grant(bot_user)
        _card(bot_user, conversation, catalogue)
        ai_food_estimation.withdraw(bot_user, source=SOURCE)
        _card(bot_user, conversation, catalogue)

        assert catalogue.estimate_allowed == [False, True, False]

    def test_f2_each_position_is_asked_for(self, bot_user, conversation, required) -> None:
        catalogue = _Recording()
        context = SkillContext(conversation=conversation, bot_user=bot_user, message_text="")
        positions = [ParsedFood(dish="борщ", grams=None), ParsedFood(dish="кофе", grams=None)]
        answers = iter([True, False])

        with (
            patch(
                "apps.skills.food_clarify.text_entry.get_nutrition_client", return_value=catalogue
            ),
            patch.object(
                ai_food_estimation, "estimate_permitted", side_effect=lambda _user: next(answers)
            ),
        ):
            text_entry.show_items(context, positions)

        # Два вызова — два вопроса: ответ первой позиции на вторую не переносится.
        assert catalogue.estimate_allowed == [True, False]

    def test_f3_withdrawn_between_the_card_and_the_log(
        self, bot_user, conversation, required
    ) -> None:
        catalogue = _Recording()
        _grant(bot_user)
        card = _card(bot_user, conversation, catalogue)
        assert card.meta["reply_kind"] == "food_text_estimate_card"

        ai_food_estimation.withdraw(bot_user, source=SOURCE)
        _turn(bot_user, conversation, "cb:food:text_log", catalogue)

        assert catalogue.estimate_allowed == [True]
        assert catalogue.log_allowed == [False]

    def test_f3_the_log_with_consent_carries_true(self, bot_user, conversation, required) -> None:
        catalogue = _Recording()
        _grant(bot_user)
        _card(bot_user, conversation, catalogue)

        _turn(bot_user, conversation, "cb:food:text_log", catalogue)

        assert catalogue.log_allowed == [True]

    def test_f4_switched_off_everyone_is_allowed_as_before(self, bot_user, conversation) -> None:
        catalogue = _Recording()

        _card(bot_user, conversation, catalogue)
        _turn(bot_user, conversation, "cb:food:text_log", catalogue)

        assert catalogue.estimate_allowed == [True]
        assert catalogue.log_allowed == [True]

    def test_f6_without_consent_the_reference_card_and_the_diary_still_work(
        self, bot_user, conversation, required
    ) -> None:
        catalogue = _Recording()

        card = _card(bot_user, conversation, catalogue)
        logged = _turn(bot_user, conversation, "cb:food:text_log", catalogue)

        # Справочное число на месте — согласие на ИИ его не касается.
        assert card.meta["reply_kind"] == "food_text_estimate_card"
        assert "ккал" in card.reply_text
        assert len(catalogue.logs) == 1
        assert logged.meta["reply_kind"] == "food_text_logged"


class TestTheMiniApp:
    def _sent(self, client, bot_user, route: str, body: dict, method: str) -> Any:
        patcher, catalog = _patch_client()
        with patcher:
            resp = _post(client, bot_user, route, body)
        assert resp.status_code in (200, 201), resp.content
        return getattr(catalog, method).await_args.kwargs["ai_estimate_allowed"]

    def test_f5_the_estimate_follows_the_consent(self, client, bot_user, required) -> None:
        body = {"text": "борщ 250"}

        before = self._sent(client, bot_user, "customer_food_estimate", body, "estimate_dish")
        _grant(bot_user)
        granted = self._sent(client, bot_user, "customer_food_estimate", body, "estimate_dish")
        ai_food_estimation.withdraw(bot_user, source=SOURCE)
        after = self._sent(client, bot_user, "customer_food_estimate", body, "estimate_dish")

        assert (before, granted, after) == (False, True, False)

    def test_f5_the_log_follows_the_consent(self, client, bot_user, required) -> None:
        before = self._sent(client, bot_user, "customer_food_log", LOG_BODY, "log_meal")
        _grant(bot_user)
        granted = self._sent(
            client,
            bot_user,
            "customer_food_log",
            {**LOG_BODY, "idempotency_key": "k-2"},
            "log_meal",
        )

        assert (before, granted) == (False, True)

    def test_f4_switched_off_the_mini_app_allows_as_before(self, client, bot_user) -> None:
        sent = self._sent(
            client, bot_user, "customer_food_estimate", {"text": "борщ 250"}, "estimate_dish"
        )

        assert sent is True
