"""DRF-2071 — перепись входов контура питания ПО КЛАССУ: не читающих флаг — 0.

Перепись к пробе T-E2E-13 (17.09) шла по именам (grep ``NUTRITION_ENABLED``)
и нашла два входа мимо выключателя DRF-1994: ``GET wellness/today`` и
``cb:food:correct:*``. Перепись по имени флага недосчитывает: вход, который
читает флаг через чужого читателя (``nutrition_enabled()``), она не видит, а
вход, который не читает ничего, — тем более. Здесь перепись идёт по классу
входа, и флаг в ней не упоминается вовсе:

* **ручки Mini App** — каждый маршрут ``miniapp_api`` из четырёх семейств
  контура питания: ``wellness/`` (сводка, вода, запись еды), ``food/``
  (оценка, запись текстом, скан), ``saved-meals`` (избранное), ``diary/``
  (дни). Список берётся из ``urlpatterns`` по префиксу, не из головы; каждый
  маршрут — каждым методом, который он принимает: при OFF — 404
  ``nutrition_disabled`` до разбора тела, и ни одного вызова каталога.
  ``plan-lite`` — не питание (свой флаг), в класс не входит;
* **кнопки и текст в чате** — каждый payload семейств ``cb:food:*`` и
  ``cb:anketa:*`` (формы из ``parse_callback``/регулярок навыков) плюс два
  текстовых входа дневника: каждый навык РЕЕСТРА, который такой ход забирает,
  при OFF отвечает заглушкой или молчит; забирает его хотя бы один навык —
  иначе перепись пуста и зелена ни о чём.

Три литерала заглушки — намеренно три (PR #1800, «Пределы» п.3): сведение —
решение владельца, не побочный эффект выключателя. Три хода по замыслу без
ворот — «Не то» под фото, «Опечатка» и «не записываю»: молчаливый ack,
который стирает открытый вопрос, ничего не пишет и не спрашивает; они
названы поимённо, а не пропущены.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from django.test import Client
from django.urls import reverse

from apps.identity.models import BotUser
from apps.miniapp_api import urls as miniapp_urls
from apps.miniapp_api.tests.test_wellness_today import BOT_TOKEN, _init_data_header
from apps.orchestrator.nutrition_global import try_handle_structured_nutrition_turn
from apps.skills.base import SkillContext
from apps.skills.food_clarify import text_entry
from apps.skills.food_clarify.skill import TYPO_ACK
from apps.skills.food_scanner.skill import NUTRITION_OFF_FALLBACK, REJECTED_ACK
from apps.skills.menu.marketplace import NUTRITION_UNAVAILABLE_TEXT
from apps.skills.nutrition_anketa.skill import (
    CB_CONFIRM_TARGETS,
    CONSENT_DECLINE_CALLBACK,
    CONSENT_GRANT_CALLBACK,
    WITHDRAW_CALLBACK,
    WITHDRAW_CONFIRM_CALLBACK,
    WITHDRAW_KEEP_CALLBACK,
)
from apps.skills.registry import registered
from apps.tenancy.models import Tenant

STUBS = frozenset(
    {NUTRITION_UNAVAILABLE_TEXT, NUTRITION_OFF_FALLBACK, text_entry.NUTRITION_OFF_TEXT}
)


@pytest.fixture
def nutrition_off(settings):
    settings.NUTRITION_ENABLED = False


# ---------------------------------------------------------------------------
# 1. Ручки Mini App — по префиксу маршрута
# ---------------------------------------------------------------------------

METHODS = ("get", "post", "put", "patch", "delete")

#: Семейства маршрутов контура питания в ``miniapp_api``. Новый маршрут с
#: одним из этих префиксов попадает в перепись сам; новое семейство — руками
#: сюда (и ``test_the_census_is_not_empty`` ниже держит нижнюю границу).
NUTRITION_ROUTE_PREFIXES = ("wellness/", "food/", "saved-meals", "diary/")

#: Подстановки для параметров пути — любой непустой id: ворота стоят до
#: обращения к каталогу, так что существование записи не проверяется.
_PATH_ARGS = {"entry_id": "e1", "meal_id": "m1"}


def _nutrition_routes() -> list[tuple[str, str]]:
    """``(name, route)`` для каждого маршрута контура питания из ``urlpatterns``."""
    found: list[tuple[str, str]] = []
    for pattern in miniapp_urls.urlpatterns:
        route = str(getattr(pattern.pattern, "_route", ""))
        if route.startswith(NUTRITION_ROUTE_PREFIXES):
            assert pattern.name, route  # безымянный маршрут не reverse'ится — в перепись не войдёт
            found.append((pattern.name, route))
    return found


def _url(name: str, route: str) -> str:
    kwargs = {k: v for k, v in _PATH_ARGS.items() if f"<str:{k}>" in route}
    return reverse(f"miniapp_api:{name}", kwargs=kwargs)


@pytest.mark.django_db
class TestEveryNutritionRouteRefusesWhenOff:
    @pytest.fixture
    def bot_user(self, settings) -> BotUser:
        tenant = Tenant.objects.create(slug="census-2071", name="Census", timezone="Europe/Moscow")
        settings.MAX_BOT_TENANT_SLUG = "census-2071"
        settings.MAX_BOT_TOKEN = BOT_TOKEN
        return BotUser.all_tenants.create(
            tenant=tenant, channel="max", channel_user_id="92071", display_name="Перепись"
        )

    def test_the_census_is_not_empty(self):
        routes = _nutrition_routes()
        # 5 wellness/ + 3 food/ + 2 saved-meals + 2 diary/ на dev 1365892f;
        # меньше — семейство выпало из ``urlpatterns`` или сменило префикс.
        assert len(routes) >= 12, routes
        for prefix in NUTRITION_ROUTE_PREFIXES:
            assert any(route.startswith(prefix) for _, route in routes), prefix

    def test_each_route_each_accepted_method_is_404_nutrition_disabled(
        self, client: Client, bot_user: BotUser, nutrition_off
    ):
        ayla = Mock(name="ayla-must-not-be-called")
        seen: dict[str, list[str]] = {}
        with (
            patch("apps.integrations.ayla.get_nutrition_client", return_value=ayla),
            patch(
                "apps.orchestrator.personal_surface.personal_records_consent_open",
                return_value=True,
            ),
        ):
            for name, route in _nutrition_routes():
                for method in METHODS:
                    kwargs = (
                        {}
                        if method == "get"
                        else {"data": "{}", "content_type": "application/json"}
                    )
                    response = getattr(client, method)(
                        _url(name, route),
                        HTTP_AUTHORIZATION=_init_data_header(bot_user.channel_user_id),
                        **kwargs,
                    )
                    if response.status_code == 405:
                        continue  # маршрут этот метод не принимает — не вход
                    seen.setdefault(route, []).append(method)
                    assert response.status_code == 404, (route, method, response.status_code)
                    assert response.json()["error"] == "nutrition_disabled", (route, method)
        # Каждый маршрут принял хотя бы один метод — иначе он выпал из переписи.
        assert set(seen) == {route for _, route in _nutrition_routes()}, seen
        assert not ayla.method_calls, ayla.method_calls


# ---------------------------------------------------------------------------
# 2. Кнопки и текст в чате — по семействам payload'ов
# ---------------------------------------------------------------------------

#: Все формы ``cb:food:*`` и ``cb:anketa:*``, которые разбирают навыки
#: (``parse_callback`` + регулярки), и два текстовых входа дневника.
CHAT_ENTRIES: tuple[str, ...] = (
    # анкета — все формы из ``NutritionAnketaSkill.matches``
    "/anketa",
    "cb:anketa:start",
    "cb:anketa:edit:weight",
    "cb:anketa:choice:gender:female",
    CONSENT_GRANT_CALLBACK,
    CONSENT_DECLINE_CALLBACK,
    WITHDRAW_CALLBACK,
    WITHDRAW_CONFIRM_CALLBACK,
    WITHDRAW_KEEP_CALLBACK,
    CB_CONFIRM_TARGETS,
    # сканер — кнопки под карточкой фото
    "cb:food:to_diary:scan-1",
    "cb:food:clarify:scan-1",
    "cb:food:reject:scan-1",
    # правка под карточкой сканера
    "cb:food:correct:grams:scan-1",
    "cb:food:correct:name:scan-1",
    "cb:food:correct:macros:scan-1",
    # текстовый ввод еды — карточка «Это про еду?» и оценка
    "cb:food:diary",
    "cb:food:typo",
    text_entry.CB_LOG,
    text_entry.CB_GRAMS,
    text_entry.CB_REJECT,
    # сохранённая запись — исправить/удалить/вернуть
    "cb:food:entry_fix:e1",
    "cb:food:entry_del:e1",
    "cb:food:entry_undo:e1",
    # вода и дневник текстом (чипы шлют ровно эти строки)
    "стакан воды",
    "что я ел сегодня",
)

#: Навыки контура питания — те, к которым ``nutrition_global`` ведёт
#: структурные ходы (``try_handle_structured_nutrition_turn``) и инструменты
#: (``execute_nutrition_tool``), плюс вода (пишет в дневник). Меню-fallback
#: забирает ЛЮБОЙ нераспознанный текст «я пока не понял» — это не вход в
#: контур, а его отсутствие, поэтому перепись смотрит на семейство, а не на
#: весь реестр. Каждое имя обязано быть в реестре — иначе перепись устарела.
NUTRITION_SKILLS = frozenset(
    {"food_scanner", "food_correction", "nutrition_anketa", "food_clarify", "water"}
)

#: По замыслу без ворот: молчаливый ack, ничего не пишет и не спрашивает.
ACK_BY_DESIGN: dict[str, str] = {
    "cb:food:reject:scan-1": REJECTED_ACK,
    "cb:food:typo": TYPO_ACK,
    text_entry.CB_REJECT: text_entry.REJECTED_TEXT,
}


def _ctx(text: str) -> SkillContext:
    # Разговор с карточкой сканера и активной анкетой, чтобы ход забирали
    # и «кнопочные», и «продолжающие» ветки ``matches``.
    conversation = SimpleNamespace(
        id=1,
        skill_state={
            "food_scan": {"scan_id": "scan-1", "dish": "борщ"},
            "nutrition_anketa": {"current_step": "gender"},
        },
    )
    return SkillContext(conversation=conversation, bot_user=Mock(), message_text=text)  # type: ignore[arg-type]


@pytest.mark.django_db
class TestEveryChatEntryIsClaimedAndAnswersTheStubWhenOff:
    @pytest.mark.parametrize("text", CHAT_ENTRIES)
    def test_entry(self, nutrition_off, text):
        if text == "что я ел сегодня":
            # Дневник — не навык реестра, а функция за той же лестницей, что
            # кнопки (``try_handle_structured_nutrition_turn`` → ``render_diary``).
            result = try_handle_structured_nutrition_turn(
                text=text,
                attachments=[],
                bot_user=Mock(),
                conversation=_ctx(text).conversation,
                trace_id="",
            )
            assert result is not None, text
            assert result.reply_text == NUTRITION_UNAVAILABLE_TEXT, (text, result.reply_text)
            return

        family = {s.name: s for s in registered() if s.name in NUTRITION_SKILLS}
        assert set(family) == NUTRITION_SKILLS, NUTRITION_SKILLS - set(family)
        claimed = [s for s in family.values() if s.matches(_ctx(text))]
        assert claimed, f"{text!r} не забирает ни один навык контура — вход выпал из переписи"

        for skill in claimed:
            result = skill.handle(_ctx(text))
            if text in ACK_BY_DESIGN:
                assert result.reply_text == ACK_BY_DESIGN[text], (skill.name, result.reply_text)
                continue
            assert result.reply_text in STUBS or result.should_send is False, (
                skill.name,
                text,
                result.reply_text,
            )
