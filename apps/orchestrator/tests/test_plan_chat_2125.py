"""План в чате — предложение Ayla, подтверждение кнопкой, «N из M» с действиями (DRF-2125, §51).

Красное листа: «мой план» без плана отвечал «составить можно в приложении»
без предложения и без кнопок; ``cb:plan:*`` не существовало; слага
«Мой план» для ``open_app`` не было; ``per_2_weeks`` рисовался как неделя.

* p1 — без плана: карточка предложения «Для цели «…» Ayla предлагает: …
  Почему: …» + «Подтвердить план» (``cb:plan:accept:<v>``) / «Изменить»
  (``open_app`` → «Мой план») / «Не сейчас»; без цели — «Сначала выберем
  цель» + ``open_goal_select``; без шаблона — прежний текст + «Изменить»;
* p2 — с планом: «N из M» + «Записаться» / «В дневник» (``cb:food:diary``) /
  «Изменить план»; ``per_2_weeks`` — «Эти 2 недели: …»;
* p3 — подтверждение: свежее предложение → сверка версии → POST с
  ``template_version``; версия разошлась → «Предложение обновилось» + новая
  карточка, POST нет; 409 → «План уже есть» + карточка; нет цели → «Сначала
  выберем цель»; сеть → «не отвечает»;
* p4 — сторожа: текст «да» план не создаёт; тап без флага — «кнопка не
  действует», не модель; набранное руками «cb:plan: …» — не тап; логи без тел;
* p5 — «Не сейчас»: маркер ``plan_proposal_declined_at`` для A4 (DRF-2126);
  повторный «мой план» предложение показывает;
* p6 — «Записаться»: услуги по курируемому ключу цели (``show_services``,
  не рекомендательный движок); без живой услуги — «пока нет услуг» + каталог;
* p7 — история тапов: фраза = метка кнопки, без версии шаблона;
* p8 — слаг ``open_plan`` в обеих картах; пункта меню нет (§37).
"""

from __future__ import annotations

import logging
import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from django.conf import settings as dj_settings
from django.utils import timezone

from apps.integrations.ayla.wellness_context_client import (
    PlanLite,
    PlanLiteAction,
    PlanLiteAlreadyActiveError,
    PlanLiteGoalNotFoundError,
    PlanLiteNoTemplateError,
    PlanLiteProposal,
    PlanLiteProposalAction,
    WellnessContext,
    WellnessContextUnavailableError,
)
from apps.orchestrator import plan_lite_card as card
from apps.orchestrator.nutrition_global import (
    resolve_plan_tap,
    try_handle_structured_nutrition_turn,
)
from apps.orchestrator.plan_lite_card import PLAN_LITE_COPY

pytestmark = pytest.mark.django_db(transaction=True)

PROPOSAL = PlanLiteProposal(
    goal_key="relax",
    why="Отдых закрепляется, когда есть регулярность и вода.",
    template_version=3,
    actions=(
        PlanLiteProposalAction("book_service", "per_week", 1),
        PlanLiteProposalAction("log_food", "per_week", 3),
        PlanLiteProposalAction("log_water", "per_day", 6),
    ),
)

PLAN = PlanLite(
    plan_id="p-1",
    goal_key="relax",
    actions=(
        PlanLiteAction("book_service", "per_week", 1, 0, "2026-09-14", "2026-09-21"),
        PlanLiteAction("log_food", "per_week", 3, 1, "2026-09-14", "2026-09-21"),
    ),
)

PLAN_BIWEEKLY = PlanLite(
    plan_id="p-2",
    goal_key="relax",
    actions=(
        PlanLiteAction("book_service", "per_2_weeks", 1, 0, "2026-09-14", "2026-09-28"),
        PlanLiteAction("log_water", "per_day", 6, 2, "2026-09-18", "2026-09-19"),
    ),
)

NO_PLAN = WellnessContext(has_plan=False, gated=True, plan_lite=None)
WITH_PLAN = WellnessContext(has_plan=False, gated=True, plan_lite=PLAN)


@pytest.fixture(autouse=True)
def _flags(settings):
    settings.NUTRITION_ENABLED = True
    settings.PLAN_LITE_ENABLED = True
    settings.AYLA_BASE_URL = "https://ayla.test"
    settings.AYLA_INTERNAL_API_TOKEN = "svc"  # noqa: S105  # pragma: allowlist secret
    settings.MAX_BOT_WEB_APP = "aylabot"


@pytest.fixture(autouse=True)
def _goal_labels(monkeypatch):
    monkeypatch.setattr(
        "apps.marketplace.discovery._known_goals", lambda: {"relax": "Расслабиться"}
    )


def _bot_user() -> Mock:
    bot_user = Mock()
    bot_user.channel = "max"
    bot_user.channel_user_id = "2125"
    bot_user.pk = 9
    return bot_user


def _fake(*, ctx=NO_PLAN, proposal=PROPOSAL, created=PLAN) -> Mock:
    fake = Mock()
    for attr, value in (
        ("get_wellness_context", ctx),
        ("get_plan_lite_proposal", proposal),
        ("create_plan_lite", created),
    ):
        if isinstance(value, Exception):
            getattr(fake, attr).side_effect = value
        else:
            getattr(fake, attr).return_value = value
    return fake


def _turn(text: str, fake: Mock, *, conversation=None):
    conversation = conversation or SimpleNamespace(id="c", skill_state={})
    with patch("apps.orchestrator.plan_lite_card.WellnessContextHttpClient", return_value=fake):
        return try_handle_structured_nutrition_turn(
            text=text,
            attachments=None,
            bot_user=_bot_user(),
            conversation=conversation,
            trace_id="t-2125",
        )


def _buttons(result) -> list[dict]:
    data = result.action_data or {}
    return [b for att in data.get("attachments", []) for b in att["payload"]["buttons"]]


def _labels(result) -> list[str]:
    return [b["label"] for b in _buttons(result)]


# ─── p1: без плана — предложение ────────────────────────────────────────────


class TestProposal:
    def test_my_plan_without_a_plan_shows_the_proposal_with_why_and_three_buttons(self) -> None:
        fake = _fake()
        result = _turn("мой план", fake)
        assert result is not None and result.meta["reply_kind"] == "plan_lite_proposal"
        assert fake.get_plan_lite_proposal.call_args.kwargs == {"external_user_id": "bot:max:2125"}
        text = result.reply_text
        assert text.startswith("Для цели «Расслабиться» Ayla предлагает:")
        assert "• записаться на услугу под цель" in text
        assert "• вести дневник еды 3 дня в неделю" in text
        assert "• пить воду 6 раз в день" in text
        assert "Почему: Отдых закрепляется, когда есть регулярность и вода." in text
        assert _labels(result) == ["Подтвердить план", "Изменить", "Не сейчас"]
        accept, edit, later = _buttons(result)
        assert accept["callback"] == "cb:plan:accept:3"
        assert edit == {"label": "Изменить", "callback": "open_plan", "web_app": "aylabot"}
        assert later["callback"] == "cb:plan:later"
        assert fake.create_plan_lite.call_count == 0  # предложение ничего не создаёт

    def test_no_active_goal_asks_for_the_goal_first(self) -> None:
        result = _turn("мой план", _fake(proposal=PlanLiteGoalNotFoundError("no goal")))
        assert result is not None and result.meta["reply_kind"] == "plan_lite_no_goal"
        assert result.reply_text == PLAN_LITE_COPY.no_goal
        (goal,) = _buttons(result)
        assert goal["callback"] == "open_goal_select" and goal["web_app"] == "aylabot"

    def test_no_template_keeps_the_app_constructor_line(self) -> None:
        result = _turn("мой план", _fake(proposal=PlanLiteNoTemplateError("no template")))
        assert result is not None and result.meta["reply_kind"] == "plan_lite_none"
        assert result.reply_text == PLAN_LITE_COPY.no_plan
        assert _labels(result) == ["Изменить"]

    def test_proposal_unavailable_is_named(self) -> None:
        result = _turn("мой план", _fake(proposal=WellnessContextUnavailableError("down")))
        assert result is not None and result.reply_text == PLAN_LITE_COPY.unavailable

    def test_per_2_weeks_in_the_proposal(self) -> None:
        proposal = PlanLiteProposal(
            goal_key="relax",
            why="",
            template_version=4,
            actions=(PlanLiteProposalAction("book_service", "per_2_weeks", 1),),
        )
        result = _turn("мой план", _fake(proposal=proposal))
        assert "• записаться на услугу под цель — эти 2 недели" in result.reply_text
        assert "Почему:" not in result.reply_text  # пустое «почему» не печатается

    def test_without_mini_app_the_edit_button_is_absent_not_dead(self, settings) -> None:
        settings.MAX_BOT_WEB_APP = ""
        settings.MAX_MINIAPP_URL = ""
        result = _turn("мой план", _fake())
        assert _labels(result) == ["Подтвердить план", "Не сейчас"]


# ─── p2: с планом — карточка и действия ─────────────────────────────────────


class TestPlanCard:
    """DRF-2283 / §77 (24.09): над списком действий — слова человека, и
    ничего нашего. Здесь слов человека нет (цель выбрана чипом), поэтому
    стоит курируемая подпись — без ярлыка «Твоя цель:» и без точки,
    которых владелец не писал."""

    def test_plan_card_with_three_action_buttons(self) -> None:
        result = _turn("мой план", _fake(ctx=WITH_PLAN))
        assert result is not None and result.meta["reply_kind"] == "plan_lite_card"
        assert result.reply_text == (
            "Расслабиться\nНа этой неделе: записаться на услугу —, дневник 1 из 3."
        )
        assert _labels(result) == ["Записаться", "В дневник", "Изменить план"]
        book, diary, edit = _buttons(result)
        assert book["callback"] == "cb:plan:book"
        assert diary["callback"] == "cb:food:diary"  # структурный ход текста DRF-1837
        assert edit["callback"] == "open_plan"

    def test_per_2_weeks_gets_its_own_line(self) -> None:
        result = _turn(
            "мой план", _fake(ctx=WellnessContext(has_plan=False, plan_lite=PLAN_BIWEEKLY))
        )
        assert result.reply_text == (
            "Расслабиться\n"
            "На этой неделе: вода 2 из 6 (сегодня).\n"
            "Эти 2 недели: записаться на услугу —."
        )

    def test_unknown_goal_key_falls_back_to_the_key(self, monkeypatch) -> None:
        monkeypatch.setattr("apps.marketplace.discovery._known_goals", lambda: {})
        result = _turn("мой план", _fake(ctx=WITH_PLAN))
        assert result.reply_text.startswith("relax\n")


# ─── p3: подтверждение кнопкой ──────────────────────────────────────────────


class TestAccept:
    def test_accept_posts_the_proposal_actions_with_the_template_version(self) -> None:
        fake = _fake()
        result = _turn("cb:plan:accept:3", fake)
        assert result is not None and result.meta["reply_kind"] == "plan_lite_card"
        assert fake.create_plan_lite.call_args.kwargs == {
            "external_user_id": "bot:max:2125",
            "actions": [
                {"action_type": "book_service", "cadence": "per_week", "target_count": 1},
                {"action_type": "log_food", "cadence": "per_week", "target_count": 3},
                {"action_type": "log_water", "cadence": "per_day", "target_count": 6},
            ],
            "template_version": 3,
        }
        assert result.reply_text.startswith("План составлен.\nРасслабиться\n")
        assert _labels(result) == ["Записаться", "В дневник", "Изменить план"]

    def test_changed_template_version_sends_a_fresh_card_and_creates_nothing(self) -> None:
        fake = _fake()  # текущая версия 3, тап — по старой карточке v2
        result = _turn("cb:plan:accept:2", fake)
        assert result is not None and result.meta["reply_kind"] == "plan_lite_proposal"
        assert result.reply_text.startswith("Предложение обновилось — посмотри свежее:\nДля цели")
        assert _buttons(result)[0]["callback"] == "cb:plan:accept:3"
        assert fake.create_plan_lite.call_count == 0

    def test_already_active_says_so_and_shows_the_plan(self) -> None:
        fake = _fake(ctx=WITH_PLAN, created=PlanLiteAlreadyActiveError("409"))
        result = _turn("cb:plan:accept:3", fake)
        assert result is not None and result.meta["reply_kind"] == "plan_lite_card"
        assert result.reply_text.startswith("План уже есть — вот он.\nРасслабиться")

    def test_already_active_without_a_document_does_not_promise_a_card(self) -> None:
        fake = _fake(ctx=NO_PLAN, created=PlanLiteAlreadyActiveError("409"))
        result = _turn("cb:plan:accept:3", fake)
        assert result.reply_text == PLAN_LITE_COPY.already_active_no_card
        assert _labels(result) == ["Изменить план"]

    def test_goal_gone_at_accept_asks_for_the_goal(self) -> None:
        result = _turn("cb:plan:accept:3", _fake(proposal=PlanLiteGoalNotFoundError("no")))
        assert result.reply_text == PLAN_LITE_COPY.no_goal

    def test_network_failure_at_create_is_named(self) -> None:
        result = _turn("cb:plan:accept:3", _fake(created=WellnessContextUnavailableError("x")))
        assert result.reply_text == PLAN_LITE_COPY.unavailable


# ─── p4: сторожа ────────────────────────────────────────────────────────────


class TestConfirmOnlyByButton:
    @pytest.mark.parametrize("text", ["да", "Да, подтверждаю", "подтвердить план", "ок"])
    def test_text_never_creates_a_plan(self, text: str) -> None:
        fake = _fake()
        result = _turn(text, fake)
        assert fake.create_plan_lite.call_count == 0
        assert result is None or result.meta.get("reply_kind") != "plan_lite_card"

    def test_tap_without_the_flag_is_a_stale_button_not_the_model(self, settings) -> None:
        settings.PLAN_LITE_ENABLED = False
        fake = _fake()
        result = _turn("cb:plan:accept:3", fake)
        assert result is not None and result.meta["reply_kind"] == "plan_lite_stale"
        assert fake.create_plan_lite.call_count == 0 and fake.get_plan_lite_proposal.call_count == 0

    @pytest.mark.parametrize(
        "text", ["cb:plan:accept:", "cb:plan:accept:0", "cb:plan: later", "cb:plan:x"]
    )
    def test_hand_typed_shapes_are_not_taps(self, text: str) -> None:
        assert card.is_plan_callback(text) is False
        assert resolve_plan_tap(text) is None
        fake = _fake()
        assert _turn(text, fake) is None
        assert fake.create_plan_lite.call_count == 0

    def test_logs_carry_no_bodies(self, caplog) -> None:
        with caplog.at_level(logging.INFO, logger="apps.orchestrator.plan_lite_card"):
            _turn("cb:plan:accept:3", _fake())
        joined = " ".join(r.getMessage() for r in caplog.records)
        assert "plan_lite.accepted" in joined and "template_version=3" in joined
        assert "Расслабиться" not in joined and "Отдых" not in joined


# ─── p5: «Не сейчас» ────────────────────────────────────────────────────────


class TestLater:
    def test_later_answers_and_writes_no_marker(self) -> None:
        """DRF-2356 — «Не сейчас» отвечает человеку и ничего не копит.

        Прежде здесь писался `plan_proposal_declined_at` для недельного
        возврата A4 (DRF-2126), которого нет, и читателей вне тестов у него
        не было. Пометка, которую никто не читает, выглядит работающим
        механизмом — поэтому её не стало. Вернётся вместе с правилом
        возврата, которое называет владелец.
        """
        conversation = SimpleNamespace(id="c", skill_state={})
        result = _turn("cb:plan:later", _fake(), conversation=conversation)
        assert result.reply_text == PLAN_LITE_COPY.later
        assert result.meta["reply_kind"] == "plan_lite_later"
        assert conversation.skill_state == {}
        again = _turn("мой план", _fake(), conversation=conversation)
        assert again.meta["reply_kind"] == "plan_lite_proposal"  # явный запрос — показываем

    def test_later_leaves_an_existing_marker_alone(self) -> None:
        """Уже записанные значения остаются: не писать новые и стирать
        старые — разные решения, и второе не наше."""
        from apps.conversations.models import Conversation
        from apps.identity.models import BotUser
        from apps.identity.services.global_tenant import get_global_bot_tenant

        tenant = get_global_bot_tenant()
        bot_user = BotUser.all_tenants.create(
            tenant=tenant, channel="max", channel_user_id="2356-l", chat_id="2356-l"
        )
        conversation = Conversation.all_tenants.create(
            tenant=tenant,
            bot_user=bot_user,
            skill_state={"plan_lite": {"plan_proposal_declined_at": "2026-09-01T10:00:00+00:00"}},
        )

        _turn("cb:plan:later", _fake(), conversation=conversation)

        conversation.refresh_from_db()
        assert (
            conversation.skill_state["plan_lite"]["plan_proposal_declined_at"]
            == "2026-09-01T10:00:00+00:00"
        )


# ─── p6: «Записаться» — подбор по ключу цели ─────────────────────────────────


class TestBook:
    def test_book_selects_services_by_the_goal_key_not_by_the_label(self) -> None:
        from apps.marketplace.dto import ServiceCard

        card_row = ServiceCard(
            tenant_id=uuid.uuid4(),
            service_id=uuid.uuid4(),
            name="Релакс-массаж",
            price_from=Decimal("3000"),
            duration_min=60,
            salon_name="Формула тела",
            city="Пенза",
            has_bookable_master=True,
        )
        with patch(
            "apps.marketplace.discovery.discover_services", return_value=[card_row]
        ) as discover:
            result = _turn("cb:plan:book", _fake(ctx=WITH_PLAN))
        assert discover.call_args.kwargs["goal_key"] == "relax"  # ключ, не разбор метки
        assert "query" not in discover.call_args.kwargs
        assert result.meta["reply_kind"] == "plan_lite_book"
        assert "Релакс-массаж" in result.reply_text
        assert result.action_data is not None  # карточки с тапами записи — как у show_services

    def test_book_without_a_live_service_points_to_the_catalog(self) -> None:
        with patch("apps.marketplace.discovery.discover_services", return_value=[]):
            result = _turn("cb:plan:book", _fake(ctx=WITH_PLAN))
        assert result.reply_text == PLAN_LITE_COPY.no_services_for_goal
        assert result.meta["reply_kind"] == "plan_lite_book_none"
        (catalog,) = _buttons(result)
        assert catalog["callback"] == "open_catalog"

    @pytest.mark.skipif(
        "postgresql" not in str(dj_settings.DATABASES["default"]["ENGINE"]),
        reason="jsonb containment по ключу цели требует Postgres (как test_discovery_goal_selection).",
    )
    def test_discover_services_by_goal_key_hits_the_curated_key_directly(self) -> None:
        """Живой запрос: услуга несёт ключ relax → найдена по ключу; чужой ключ — пусто."""
        from apps.catalog.models import CatalogMaster, CatalogService, MasterService
        from apps.marketplace.discovery import discover_services
        from apps.tenancy.models import Tenant

        tenant = Tenant.objects.create(slug="plan-2125", name="Формула тела", city="Пенза")
        service = CatalogService.all_tenants.create(
            tenant=tenant,
            slug="relax-massage",
            name="Релакс-массаж",
            is_active=True,
            goals=[{"key": "relax", "label": "Расслабиться"}],
            external_updated_at=timezone.now(),
        )
        master = CatalogMaster.all_tenants.create(
            tenant=tenant,
            external_updated_at=timezone.now(),
            name="Анна",
            is_active=True,
            invite_status=CatalogMaster.InviteStatus.ACCEPTED,
            ayla_user_id=uuid.uuid4(),
        )
        MasterService.all_tenants.create(tenant=tenant, master=master, service=service)
        assert [c.name for c in discover_services(goal_key="relax")] == ["Релакс-массаж"]
        assert discover_services(goal_key="body_shape") == []

    def test_book_without_a_plan_falls_back_to_the_proposal(self) -> None:
        result = _turn("cb:plan:book", _fake())
        assert result.meta["reply_kind"] == "plan_lite_proposal"


# ─── p7: история тапов ──────────────────────────────────────────────────────


class TestTapHistory:
    @pytest.mark.parametrize(
        ("payload", "phrase"),
        [
            ("cb:plan:accept:3", "Подтвердить план"),
            ("cb:plan:accept:12", "Подтвердить план"),
            ("cb:plan:later", "Не сейчас"),
            ("cb:plan:book", "Записаться"),
        ],
    )
    def test_phrase_is_the_button_label_without_the_version(self, payload, phrase) -> None:
        tap = resolve_plan_tap(payload)
        assert tap is not None and tap.history_text == phrase

    def test_other_families_are_not_ours(self) -> None:
        assert resolve_plan_tap("cb:food:diary") is None
        assert resolve_plan_tap("мой план") is None


# ─── p8: слаг и меню ────────────────────────────────────────────────────────


class TestSlugAndMenu:
    def test_open_plan_slug_lives_in_both_maps(self) -> None:
        from pathlib import Path

        from apps.skills.welcome.skill import MINIAPP_ROUTES

        assert MINIAPP_ROUTES["open_plan"] == "customer/plan"
        ts = (Path(__file__).resolve().parents[3] / "apps/miniapp/src/lib/max-sdk.ts").read_text(
            encoding="utf-8"
        )
        assert 'open_plan: "/customer/plan"' in ts

    def test_the_menu_still_has_no_plan_item(self) -> None:
        from apps.skills.menu.marketplace import main_items

        labels = [item.label for item in main_items(bot_user=_bot_user())]
        assert "Помощь" in labels and "Мой план" not in labels
