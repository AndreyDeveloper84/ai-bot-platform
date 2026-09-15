"""DRF-1968 (M2+), второй заход по ревью — кнопка в БОЮ и честность обещания.

Первый заход проверял навыки напрямую и поэтому не видел главного: боевой путь
исполняет их внутри ``tenant_scope(get_global_bot_tenant())`` — сентинельный
тенант, который никогда не ``None``. Признак «тенанта нет» отбивал кнопку на
всех трёх поверхностях, а тесты оставались зелёными. Здесь ход идёт через канал
целиком (``handle_global_max_event``), и проверяются вложения, а не только текст.

Второе: ответ «Готово, согласие есть» — утверждение о 152-ФЗ. Оно допустимо
только там, где согласие действительно записано:

* на салонном пути журнал не пишется (его пишет глобальный онбординг) — значит
  и утверждать нечего;
* если запись журнала не удалась, человеку нельзя говорить, что согласие есть.

Третье: человек, который перед согласием открыл «Узнать что хранится», не должен
терять свой поток — S2a обязан нести тот же исходный вход.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from apps.channels.max import handler as max_handler
from apps.consent.models import ConsentRecord
from apps.identity.services.resolver import resolve_or_create_global_bot_user
from apps.integrations.ayla import WaterEntryResponse
from apps.llm.protocol import CompletionResult, ToolCall
from apps.orchestrator import concierge
from apps.orchestrator.memory import short_term
from apps.skills.base import SkillContext
from apps.skills.food_clarify.text_entry import CONSENT_TEXT

# ``transaction=True``: ход консьержа пишет в БД из другого потока.
pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def _onboarding_on(settings):
    settings.GLOBAL_BOT_ONBOARDING = True
    settings.NUTRITION_ENABLED = True
    settings.FOOD_PHOTO_SCAN_ENABLED = True


@pytest.fixture(autouse=True)
def _no_chat_actions(monkeypatch):
    monkeypatch.setattr(
        "apps.channels.max.outbound.send_chat_action", lambda **kwargs: {"ok": True}
    )


@pytest.fixture(autouse=True)
def _no_second_model_calls(monkeypatch):
    monkeypatch.setattr(max_handler, "resolve_and_log_turn_intent", MagicMock())
    monkeypatch.setattr(max_handler, "maybe_weave_question", lambda _c, _b, reply: reply)


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch):
    from apps.orchestrator.memory.tests.test_short_term import _FakeRedis

    fake = _FakeRedis()
    monkeypatch.setattr(short_term, "_redis_client", lambda: fake)
    return fake


@pytest.fixture
def sent(monkeypatch):
    """Каждая отправка целиком: и текст, и вложения — кнопки живут во вложениях."""
    calls: list[dict] = []

    def fake_send(*, chat_id, text, attachments=None, timeout=10.0):
        calls.append({"text": text, "attachments": attachments})
        return {"ok": True}

    monkeypatch.setattr(max_handler, "send_message", fake_send)
    return calls


@pytest.fixture
def water_writes(monkeypatch):
    writes: list[dict] = []

    async def _add_water(**kwargs):
        writes.append(kwargs)
        return WaterEntryResponse(
            entry_id="e-1968",
            ml=250,
            water_ml=250,
            kcal=0,
            milestone_text=None,
            today_total_ml=250,
            today_norm_ml=None,
            alcohol_recovery_hint=False,
            raw={},
        )

    client = Mock()
    client.add_water = _add_water
    monkeypatch.setattr("apps.skills.water.skill.get_nutrition_client", lambda: client)
    return writes


def _model_calls_log_water(monkeypatch) -> None:
    provider = AsyncMock()
    provider.complete.return_value = CompletionResult(
        text="",
        tool_calls=[ToolCall(id="c1", name="log_water", arguments={"drink_text": "стакан воды"})],
        prompt_tokens=30,
        completion_tokens=6,
        model="gpt-4o-mini",
        provider="openai",
        finish_reason="tool_calls",
    )
    router = Mock()
    router.get_provider.return_value = provider
    monkeypatch.setattr(concierge, "get_router", lambda: router)


def _water_turn(user_id: int) -> None:
    from django.utils import timezone

    bot_user = resolve_or_create_global_bot_user(
        channel="max", channel_user_id=str(user_id), chat_id=str(user_id)
    )
    bot_user.welcomed_at = timezone.now()
    bot_user.save(update_fields=["welcomed_at"])
    max_handler.handle_global_max_event(
        {
            "update_type": "message_created",
            "timestamp": 1731320000000,
            "message": {
                "sender": {"user_id": user_id, "name": "Ирина"},
                "recipient": {"chat_id": user_id, "chat_type": "dialog"},
                "body": {"mid": f"m{user_id}", "seq": 1, "text": "стакан воды", "attachments": []},
            },
        }
    )


def _callbacks_of(attachments) -> list[str]:
    out: list[str] = []
    for att in attachments or []:
        for row in (att or {}).get("payload", {}).get("buttons", []) or []:
            for button in row if isinstance(row, list) else [row]:
                payload = (button or {}).get("payload") or (button or {}).get("callback")
                if payload:
                    out.append(payload)
    return out


# ── 1. Кнопка в бою ──────────────────────────────────────────────────────────


class TestTheButtonReachesTheChannel:
    def test_the_water_refusal_reaches_max_with_the_consent_button(
        self, monkeypatch, sent, water_writes
    ) -> None:
        """Ход идёт под сентинельным тенантом — кнопка обязана дожить до канала."""
        _model_calls_log_water(monkeypatch)

        _water_turn(71968)

        assert water_writes == [], "вода записалась без согласия"
        assert sent, "человек не получил ответа"
        last = sent[-1]
        assert last["text"] == CONSENT_TEXT
        assert "cb:welcome:consent_offer_water" in _callbacks_of(last["attachments"])


# ── 2. Обещание согласия — только там, где оно записано ──────────────────────


class TestThePromiseMatchesTheJournal:
    def test_a_salon_tap_does_not_claim_the_consent(self) -> None:
        """На салонном пути журнал не пишется — «Готово, согласие есть» там ложь."""
        from apps.skills.welcome.skill import CONSENT_RECOVERY_RETURN_TEXTS, WelcomeSkill
        from apps.tenancy.context import tenant_scope
        from apps.tenancy.models import Tenant

        tenant = Tenant.objects.create(slug="salon-recovery", name="Salon Recovery")
        bot_user = resolve_or_create_global_bot_user(
            channel="max", channel_user_id="71969", chat_id="71969"
        )
        ctx = SkillContext(
            conversation=Mock(id="conv-1968", skill_state={}),
            bot_user=bot_user,
            message_text="cb:welcome:consent_yes_photo",
        )
        with tenant_scope(tenant):
            result = WelcomeSkill().handle(ctx)

        assert result.reply_text != CONSENT_RECOVERY_RETURN_TEXTS["photo"]
        assert not ConsentRecord.all_tenants.filter(bot_user=bot_user, granted=True).exists()

    def test_a_failed_journal_does_not_claim_the_consent(self) -> None:
        """Запись не удалась — человеку не говорится, что согласие есть."""
        from apps.channels.max.global_onboarding import run_onboarding_turn
        from apps.conversations.services import resolve_active_global_conversation
        from apps.skills.welcome.skill import CONSENT_RECOVERY_RETURN_TEXTS

        bot_user = resolve_or_create_global_bot_user(
            channel="max", channel_user_id="71970", chat_id="71970"
        )
        conversation = resolve_active_global_conversation(bot_user)
        with patch(
            "apps.consent.services.record_global_consent",
            side_effect=RuntimeError("consent store down"),
        ):
            reply = run_onboarding_turn(conversation, bot_user, "cb:welcome:consent_yes_photo")

        assert reply.text != CONSENT_RECOVERY_RETURN_TEXTS["photo"]
        assert not ConsentRecord.all_tenants.filter(bot_user=bot_user, granted=True).exists()


# ── 3. Обход через «Узнать что хранится» не теряет исходный поток ────────────


class TestTheDetailsFoldKeepsTheOrigin:
    def test_the_details_screen_keeps_the_origin(self) -> None:
        from apps.channels.max.global_onboarding import run_onboarding_turn
        from apps.conversations.services import resolve_active_global_conversation
        from apps.skills.welcome.skill import S2A_DETAILS_TEXT

        bot_user = resolve_or_create_global_bot_user(
            channel="max", channel_user_id="71971", chat_id="71971"
        )
        reply = run_onboarding_turn(
            resolve_active_global_conversation(bot_user),
            bot_user,
            "cb:welcome:consent_details_photo",
        )

        assert reply.text == S2A_DETAILS_TEXT
        callbacks = [b.get("callback") for b in (reply.action_data or {}).get("buttons", [])]
        assert "cb:welcome:consent_yes_via_s2a_photo" in callbacks

    def test_the_via_s2a_grant_returns_to_the_flow(self) -> None:
        from apps.channels.max.global_onboarding import run_onboarding_turn
        from apps.conversations.services import resolve_active_global_conversation
        from apps.skills.welcome.skill import CONSENT_RECOVERY_RETURN_TEXTS

        bot_user = resolve_or_create_global_bot_user(
            channel="max", channel_user_id="71972", chat_id="71972"
        )
        reply = run_onboarding_turn(
            resolve_active_global_conversation(bot_user),
            bot_user,
            "cb:welcome:consent_yes_via_s2a_photo",
        )

        assert reply.text == CONSENT_RECOVERY_RETURN_TEXTS["photo"]
        assert ConsentRecord.all_tenants.filter(
            bot_user=bot_user,
            granted=True,
            consent_type=ConsentRecord.ConsentType.PERSONAL_DATA.value,
        ).exists()


# ── 4. Мелочи, которые ревью назвало незаявленными зависимостями ─────────────


class TestTheRecoveryTurnIsAFullWelcomeTurn:
    def test_the_recovery_grant_stamps_welcomed_at(self) -> None:
        """Человек, впервые заговоривший через отказ, для приветствия уже знаком."""
        from apps.channels.max.global_onboarding import run_onboarding_turn
        from apps.conversations.services import resolve_active_global_conversation

        bot_user = resolve_or_create_global_bot_user(
            channel="max", channel_user_id="71973", chat_id="71973"
        )
        assert bot_user.welcomed_at is None
        run_onboarding_turn(
            resolve_active_global_conversation(bot_user), bot_user, "cb:welcome:consent_yes_photo"
        )

        bot_user.refresh_from_db()
        assert bot_user.welcomed_at is not None

    def test_the_offer_screen_has_its_own_reply_kind(self) -> None:
        """Наружу одно имя — внутрь раздельные: экран из отказа отличим от приветственного."""
        from apps.skills.welcome.skill import WelcomeSkill

        ctx = SkillContext(
            conversation=Mock(id="conv-kind", skill_state={}),
            bot_user=Mock(welcomed_at=object()),
            message_text="cb:welcome:consent_offer_photo",
        )
        result = WelcomeSkill().handle(ctx)

        assert result.meta.get("reply_kind") == "welcome_s2_consent_prompt_recovery"


# ── Сторожа: зелёные и до правки, и после ────────────────────────────────────


class TestGuards:
    def test_on_a_salon_tenant_the_refusal_has_no_button(self) -> None:
        from apps.skills.water import skill as water_skill
        from apps.tenancy.context import tenant_scope
        from apps.tenancy.models import Tenant

        tenant = Tenant.objects.create(slug="salon-no-button", name="Salon No Button")
        ctx = SkillContext(
            conversation=Mock(id="conv-salon", skill_state={}),
            bot_user=Mock(channel="max", channel_user_id="71974"),
            message_text="стакан воды",
        )
        with patch.object(water_skill, "_consent_open", return_value=False), tenant_scope(tenant):
            result = water_skill.WaterSkill().handle(ctx)

        assert result.reply_text == CONSENT_TEXT
        assert result.action_data is None

    def test_the_three_return_texts_are_different(self) -> None:
        """Возврат — в СВОЙ поток: одинаковые фразы сделали бы origin фикцией."""
        from apps.skills.welcome.skill import CONSENT_RECOVERY_ORIGINS, CONSENT_RECOVERY_RETURN_TEXTS

        texts = [CONSENT_RECOVERY_RETURN_TEXTS[o] for o in CONSENT_RECOVERY_ORIGINS]
        assert len(set(texts)) == len(CONSENT_RECOVERY_ORIGINS)

    def test_the_offer_screen_buttons_are_exactly_three(self) -> None:
        from apps.skills.welcome.skill import WelcomeSkill

        ctx = SkillContext(
            conversation=Mock(id="conv-buttons", skill_state={}),
            bot_user=Mock(welcomed_at=object()),
            message_text="cb:welcome:consent_offer_water",
        )
        result = WelcomeSkill().handle(ctx)

        assert [b["callback"] for b in (result.action_data or {})["buttons"]] == [
            "cb:welcome:consent_yes_water",
            "cb:welcome:consent_details_water",
            "cb:welcome:consent_refuse",
        ]

    def test_the_photo_return_still_meets_the_scanners_own_gate(self, monkeypatch) -> None:
        """НАЗВАННЫЙ ДОЛГ, а не починка: у сканера своя вторая колонка.

        Текст возврата обещает «пришли фото ещё раз — запишу в дневник», но
        после PERSONAL_DATA сканер держит ещё один гейт — ``food_scanner_consent_at``
        (``food_scanner/skill.py::_check_gates``, ветка ниже). Человек с фото
        упрётся в него и услышит второй отказ. Здесь предел обещания
        пришпилен, чтобы он не растворился молча; починка — согласие дневника
        в чате, PR-2 этого листа (стекуется на #1776). Сторож зелёный и до
        правки, и после: он про чужое поведение, которое я не меняю.
        """
        from apps.orchestrator import personal_surface
        from apps.skills.food_scanner.skill import _check_gates

        monkeypatch.setattr(personal_surface, "personal_records_consent_open", lambda _u: True)
        ctx = SkillContext(
            conversation=Mock(id="conv-debt", skill_state={}),
            bot_user=Mock(food_scanner_consent_at=None),
            message_text="",
        )

        gate = _check_gates(ctx, require_photo_scan=True, kind="photo")

        assert gate is not None, "второй гейт исчез — обещание возврата стало полным"
        assert gate.meta["reply_kind"] == "food_scanner_consent_required"

    def test_a_revoked_consent_can_be_granted_again(self) -> None:
        """«Нет ИЛИ отозван» — вторая половина предмета листа."""
        from apps.channels.max.global_onboarding import run_onboarding_turn
        from apps.consent.services import withdraw_personal_data_for_bot_users
        from apps.conversations.services import resolve_active_global_conversation

        bot_user = resolve_or_create_global_bot_user(
            channel="max", channel_user_id="71975", chat_id="71975"
        )
        conversation = resolve_active_global_conversation(bot_user)
        run_onboarding_turn(conversation, bot_user, "cb:welcome:consent_yes_photo")
        withdraw_personal_data_for_bot_users(
            type(bot_user).all_tenants.filter(pk=bot_user.pk), source="test:drf1968"
        )
        assert not ConsentRecord.all_tenants.filter(
            bot_user=bot_user,
            granted=True,
            withdrawn_at=None,
            consent_type=ConsentRecord.ConsentType.PERSONAL_DATA.value,
        ).exists()

        run_onboarding_turn(conversation, bot_user, "cb:welcome:consent_yes_photo")

        assert ConsentRecord.all_tenants.filter(
            bot_user=bot_user,
            granted=True,
            withdrawn_at=None,
            consent_type=ConsentRecord.ConsentType.PERSONAL_DATA.value,
        ).exists()
