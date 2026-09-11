"""DRF-1658 (N-a3): анкета прикладывает утверждение о согласии к POST профиля.

Граница каталога (beautygo_backend#324, §92 N-a2) отвечает
``422 CONSENT_REQUIRED`` на любое тело с параметрами тела без
``consent: {type, document_version}``. Все шесть полей анкеты — в закрытом
списке, значит без утверждения анкета мертва целиком.

## Две стражи, и обе нужны

1. **С утверждением** тело = прежние шесть полей + блок ``consent`` в
   форме #324, байт в байт.
2. **Без утверждения клиент не вызван.** Без этой половины набор зеленел
   бы на коде, который отказывает человеку и всё равно шлёт — и на коде,
   который прикладывает утверждение всегда.

## Что здесь НЕ проверяется

Откуда берётся факт согласия — это :mod:`apps.consent.personal_calculation`
и его тесты в ``apps/consent/tests``. Здесь ``current_attestation``
подменяется по имени модуля-источника, потому что скилл импортирует его
лениво, в момент вызова.
"""

from __future__ import annotations

import inspect
from unittest.mock import Mock, patch

from apps.consent.personal_calculation import (
    LOOKUP_FAILED,
    NO_DOCUMENT_VERSION,
    NOT_GRANTED,
    ConsentAttestation,
    ConsentAttestationUnavailable,
)
from apps.integrations.ayla import ProfileResponse
from apps.skills.base import SkillContext
from apps.skills.nutrition_anketa.skill import NutritionAnketaSkill

_ATTESTATION = "apps.consent.personal_calculation.current_attestation"
_CLIENT = "apps.skills.nutrition_anketa.skill.get_nutrition_client"

VERSION = "personal-calculation-v1"

#: Состояние «всё отвечено, остался последний ответ — цель».
_READY_FOR_GOAL = {
    "nutrition_anketa": {
        "current_step": "goal",
        "answers": {"gender": "female", "age": 28, "height": 168, "weight": 62},
        "is_complete": False,
    }
}


class _Conversation:
    def __init__(self, initial: dict | None = None) -> None:
        self.id = "conv-attest"
        self.skill_state = initial or {}

    def save(self, update_fields: list[str] | None = None) -> None:
        return


def _context(text: str, *, state: dict | None = None) -> tuple[SkillContext, _Conversation]:
    conversation = _Conversation(state)
    bot_user = Mock(channel="max", channel_user_id="12345")
    ctx = SkillContext(
        conversation=conversation,  # type: ignore[arg-type]
        bot_user=bot_user,
        message_text=text,
    )
    return ctx, conversation


def _profile() -> ProfileResponse:
    return ProfileResponse(
        gender="female",
        age=28,
        height_cm=168,
        weight_kg=62,
        goal="maintain",
        daily_kcal=1900,
        protein_g=95,
        fat_g=60,
        carbs_g=220,
        water_ml=2100,
        bmr=1450,
        health_flags={},
        disclaimer_acked=None,
        goal_overridden_by=None,
    )


def _capturing_client() -> tuple[Mock, list[dict]]:
    captured: list[dict] = []
    client = Mock()

    async def _upsert(**kwargs):
        captured.append(kwargs)
        return _profile()

    client.upsert_profile = _upsert
    return client, captured


def _client_that_must_not_be_called() -> Mock:
    """Клиент, любой вызов которого валит тест.

    ``assert not called`` после факта прошёл бы и на коде, вызвавшем
    ДРУГОЙ метод; стража стоит на самом клиенте.
    """
    client = Mock()

    async def _boom(**kwargs):  # pragma: no cover — смысл в том, чтобы не дойти
        raise AssertionError(f"Ayla была вызвана без утверждения о согласии: {kwargs}")

    client.upsert_profile = _boom
    return client


def _complete(ctx: SkillContext, client: Mock, attestation_side_effect):
    with (
        patch(_CLIENT, return_value=client),
        patch(_ATTESTATION, side_effect=attestation_side_effect),
    ):
        return NutritionAnketaSkill().handle(ctx)


# ─── стража 1: с утверждением тело несёт блок consent в форме #324 ─────────


class TestWithAttestationTheBodyCarriesIt:
    def test_body_is_six_fields_plus_the_consent_block_verbatim(self) -> None:
        ctx, conversation = _context("cb:anketa:choice:goal:maintain", state=_READY_FOR_GOAL)
        client, captured = _capturing_client()
        attestation = ConsentAttestation(type="personal_calculation", document_version=VERSION)

        result = _complete(ctx, client, lambda bot_user: attestation)

        assert result.action_type == "anketa_complete"
        assert len(captured) == 1
        # Точное равенство, а не «ключ есть»: форма — контракт границы.
        assert captured[0]["data"] == {
            "gender": "female",
            "age": 28,
            "height_cm": 168,
            "weight_kg": 62,
            "goal": "maintain",
            "activity_coefficient": 1.4,
            "consent": {"type": "personal_calculation", "document_version": VERSION},
        }
        assert "nutrition_anketa" not in conversation.skill_state

    def test_the_attestation_is_asked_for_this_person(self) -> None:
        """Утверждение берётся по ``bot_user`` из контекста, не откуда-то ещё."""
        ctx, _ = _context("cb:anketa:choice:goal:maintain", state=_READY_FOR_GOAL)
        client, _ = _capturing_client()
        seen: list[object] = []

        def _lookup(bot_user):
            seen.append(bot_user)
            return ConsentAttestation(type="personal_calculation", document_version=VERSION)

        _complete(ctx, client, _lookup)

        assert seen == [ctx.bot_user]


# ─── стража 2: без утверждения POST не уходит, и отказ назван ─────────────


class TestWithoutAttestationNothingIsSent:
    def _refused(self, reason: str):
        ctx, conversation = _context("cb:anketa:choice:goal:maintain", state=_READY_FOR_GOAL)
        result = _complete(
            ctx,
            _client_that_must_not_be_called(),
            ConsentAttestationUnavailable(reason),
        )
        return result, conversation

    def test_not_granted_refuses_by_name_and_keeps_the_diary(self) -> None:
        result, conversation = self._refused(NOT_GRANTED)

        assert result.action_type == "anketa_consent_required"
        assert result.action_data["reason"] == NOT_GRANTED
        assert "дневник" in result.reply_text.lower(), "§92: отказ не закрывает дневник"
        # Собранные параметры тела не остаются лежать без основания.
        assert "nutrition_anketa" not in conversation.skill_state

    def test_missing_version_is_its_own_reason(self) -> None:
        """Версии нет — это не ``not_granted``: чинится текстом, а не согласием."""
        result, _ = self._refused(NO_DOCUMENT_VERSION)

        assert result.action_type == "anketa_consent_required"
        assert result.action_data["reason"] == NO_DOCUMENT_VERSION

    def test_registry_failure_refuses_rather_than_sends(self) -> None:
        result, _ = self._refused(LOOKUP_FAILED)

        assert result.action_type == "anketa_consent_required"
        assert result.action_data["reason"] == LOOKUP_FAILED

    def test_unpatched_lookup_on_a_non_orm_user_closes_not_opens(self) -> None:
        """Без подмены: Mock вместо BotUser упирается в реестр — и это отказ.

        Fail-closed на живом пути, а не на заглушке: если бы модуль на
        ошибку реестра отвечал «согласие есть», этот тест дошёл бы до
        клиента и упал там.
        """
        ctx, _ = _context("cb:anketa:choice:goal:maintain", state=_READY_FOR_GOAL)
        with patch(_CLIENT, return_value=_client_that_must_not_be_called()):
            result = NutritionAnketaSkill().handle(ctx)

        assert result.action_type == "anketa_consent_required"
        assert result.action_data["reason"] == LOOKUP_FAILED


# ─── тело нельзя собрать без утверждения даже по ошибке ───────────────────


def test_payload_builder_has_no_default_for_the_attestation() -> None:
    """Аргумент обязателен: ``None`` по умолчанию воскресил бы тело без поля.

    Сторож на сигнатуру, а не на поведение: поведение «без утверждения —
    отказ» держит ``_on_complete``, а эта проверка не даёт следующей
    правке тихо вернуть путь, где тело собирается и без него.
    """
    sig = inspect.signature(NutritionAnketaSkill._build_ayla_payload)
    param = sig.parameters["attestation"]
    assert param.default is inspect.Parameter.empty
