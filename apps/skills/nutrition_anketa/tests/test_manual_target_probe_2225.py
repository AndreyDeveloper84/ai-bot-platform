"""Проба ручного ориентира — короткий таймаут, не кормит общий breaker (DRF-2225).

На входе в анкету (и на пути отзыва) бот спрашивает каталог, стоит ли у
человека ориентир от специалиста (``user_entered``). Проба шла общим
``get_profile`` — таймаут клиента 10 с, и таймаут записывался в общий
circuit breaker питания: медленный каталог держал вход в анкету до 10 с, а
несколько таких входов открывали breaker для ВСЕГО питания — дневника,
сканера, анкеты.

Проба — вежливость, не ворота. Поэтому:

* p1 — проба ходит с коротким таймаутом (:data:`MANUAL_TARGET_PROBE_TIMEOUT_S`)
  и с ``feeds_circuit=False``;
* p2 — клиент: таймаут с ``feeds_circuit=False`` — ``NutritionUnavailableError``,
  а счётчик отказов breaker'а НЕ растёт; без флага — растёт как раньше
  (присутствие рядом с отсутствием);
* p3 — вход в анкету при таймауте пробы — анкета начинается, фразы «анкета не
  заменит твой ориентир от специалиста» НЕТ: без ответа каталога бот не
  утверждает, что у человека есть ориентир специалиста (выбор назван);
* p4 — отзыв при таймауте пробы и без согласия M — прежнее поведение
  («отключать нечего»), но уже с коротким таймаутом и мимо breaker'а;
  поведение отзыва этим листом не меняется (предел назван в PR);
* p5 — положительные пары: ``user_entered`` → фраза есть; расчёт → фразы нет.
"""

from __future__ import annotations

from unittest.mock import Mock, patch

import httpx
import pytest

from apps.integrations.ayla import nutrition_client as nc
from apps.skills.base import SkillContext
from apps.skills.nutrition_anketa.skill import (
    ANKETA_OVER_MANUAL_NOTE,
    MANUAL_TARGET_PROBE_TIMEOUT_S,
    WITHDRAW_CALLBACK,
    NutritionAnketaSkill,
)
from apps.skills.nutrition_anketa.tests.test_skill import _profile, _StatefulConversation


def _ctx(text: str) -> SkillContext:
    return SkillContext(
        conversation=_StatefulConversation({}),  # type: ignore[arg-type]
        bot_user=Mock(channel="max", channel_user_id="2225"),
        message_text=text,
    )


def _client_with(get_profile) -> Mock:
    client = Mock()
    client.get_profile = get_profile
    return client


def _turn(text: str, client: Mock, *, m_granted: bool = True):
    with (
        patch("apps.skills.nutrition_anketa.skill.get_nutrition_client", return_value=client),
        patch("apps.consent.personal_calculation.is_granted", return_value=m_granted),
    ):
        return NutritionAnketaSkill().handle(_ctx(text))


async def _timeout(**kwargs):
    raise nc.NutritionUnavailableError("network: ReadTimeout")


class TestP1ShortTimeoutAndNoCircuit:
    def test_probe_passes_a_short_timeout_and_opts_out_of_the_circuit(self) -> None:
        seen: dict = {}

        async def _get(**kwargs):
            seen.update(kwargs)
            return None

        _turn("/anketa", _client_with(_get))
        assert seen["timeout_s"] == MANUAL_TARGET_PROBE_TIMEOUT_S
        assert seen["feeds_circuit"] is False
        assert MANUAL_TARGET_PROBE_TIMEOUT_S < nc.DEFAULT_TIMEOUT_S


class TestP2ClientTimeoutDoesNotFeedTheBreaker:
    def _client(self, handler) -> nc.NutritionClient:
        client = nc.NutritionClient(
            base_url="https://ayla.test", service_token="t"
        )  # pragma: allowlist secret
        original = httpx.AsyncClient

        def _factory(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            return original(*args, **kwargs)

        self._patch = patch.object(httpx, "AsyncClient", _factory)
        self._patch.start()
        return client

    def teardown_method(self) -> None:
        patch.stopall()

    @pytest.mark.asyncio
    async def test_probe_timeout_raises_but_the_breaker_does_not_count_it(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("slow", request=request)

        client = self._client(handler)
        with pytest.raises(nc.NutritionUnavailableError):
            await client.get_profile(external_user_id="bot:1", timeout_s=0.5, feeds_circuit=False)
        assert client._circuit.failures == []
        # Присутствие рядом: обычный вызов тот же таймаут считает.
        with pytest.raises(nc.NutritionUnavailableError):
            await client.get_profile(external_user_id="bot:1")
        assert len(client._circuit.failures) == 1

    @pytest.mark.asyncio
    async def test_the_short_timeout_reaches_httpx(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["timeout"] = request.extensions.get("timeout")
            return httpx.Response(200, json={"data": {"exists": False}})

        client = self._client(handler)
        await client.get_profile(external_user_id="bot:1", timeout_s=0.5, feeds_circuit=False)
        assert seen["timeout"]["read"] == 0.5


class TestP3EntryOnTimeout:
    def test_anketa_starts_without_the_manual_note(self) -> None:
        result = _turn("/anketa", _client_with(_timeout))
        assert result.action_type == "anketa_step_gender"
        assert ANKETA_OVER_MANUAL_NOTE not in result.reply_text


class TestP4WithdrawOnTimeout:
    def test_unknown_keeps_the_previous_answer_via_the_short_probe(self) -> None:
        seen: dict = {}

        async def _slow(**kwargs):
            seen.update(kwargs)
            raise nc.NutritionUnavailableError("network: ReadTimeout")

        result = _turn(WITHDRAW_CALLBACK, _client_with(_slow), m_granted=False)
        assert seen["timeout_s"] == MANUAL_TARGET_PROBE_TIMEOUT_S
        assert seen["feeds_circuit"] is False
        assert result.meta["reply_kind"] == "anketa_withdraw_nothing"


class TestP5PositivePairs:
    def test_user_entered_prepends_the_note(self) -> None:
        from dataclasses import replace

        async def _get(**kwargs):
            return replace(_profile(), targets_source="user_entered")

        result = _turn("/anketa", _client_with(_get))
        assert result.reply_text.startswith(ANKETA_OVER_MANUAL_NOTE)

    def test_calculated_has_no_note_and_withdraw_without_m_is_nothing(self) -> None:
        async def _get(**kwargs):
            return _profile()

        result = _turn("/anketa", _client_with(_get))
        assert result.action_type == "anketa_step_gender"
        assert ANKETA_OVER_MANUAL_NOTE not in result.reply_text
        nothing = _turn(WITHDRAW_CALLBACK, _client_with(_get), m_granted=False)
        assert nothing.meta["reply_kind"] == "anketa_withdraw_nothing"
