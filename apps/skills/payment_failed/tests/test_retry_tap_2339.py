"""Тап по «Оплатить» действительно платит (DRF-2339).

Личное сообщение об отказе платежа само спрашивает «Попробовать снова?» и
даёт кнопку [Оплатить], а тап отвечал «оплата временно недоступна через
бот» — момент худший из возможных: у человека только что не прошёл платёж.

Причина (замер): половина бота не была дописана. ``_try_call_ayla_retry``
безусловно возвращал заглушку (TODO(#67)), а у клиента платежей не было
метода повтора. Ручка каталога при этом уже есть
(``POST /api/v1/payments/internal/<id>/retry/``), и тексты на все исходы
в скилле уже написаны — новых слов эта правка не сочиняет.

Разводка по задокументированным ответам ручки:

* 201 → ссылка на оплату (``_CLIENT_RETRY_SUCCESS_TEMPLATE``);
* 409 ``INVALID_STATUS`` → «эта оплата уже неактуальна»
  (``_CLIENT_RETRY_OBSOLETE_TEMPLATE``) — платёж прошёл или запись
  отменена; это НЕ поломка;
* 502 / 503 / таймаут → «провайдер недоступен, попробуй через пару минут»
  (``_CLIENT_RETRY_TRANSIENT_ERROR``) — звучит иначе, чем 409: там
  повторять нечего, здесь есть;
* 401 / 403 / 404 → «эта кнопка не для тебя» (``_CALLBACK_NOT_AUTHORIZED``).

Узел (по просьбе главного окна): **кнопка показана → тап не отвечает
«недоступно»**. Он держит саму суть листа и останется красным, если
заглушку вернут.
"""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

from apps.integrations.ayla_payments import (
    AylaPaymentsAPIError,
    AylaPaymentsUnavailableError,
)
from apps.integrations.ayla_payments.client import (
    AylaPaymentsNotAuthorized,
    AylaPaymentsRetryRefused,
    RetryPaymentResult,
)
from apps.skills.base import SkillContext
from apps.skills.payment_failed import skill as payment_skill
from apps.skills.payment_failed.skill import (
    CALLBACK_PAYMENT_RETRY_PREFIX,
    PaymentRetryCallbackSkill,
)

PAYMENT_ID = "6f1c6d3e-1f2a-4a7f-9a44-2f0d5a9b1111"
AYLA_USER_ID = "2b7f9c10-5d3e-4c1a-9f88-11aa22bb3344"
CHECKOUT = "https://yoomoney.example/checkout/abc"


def _context(*, ayla_user_id: str | None = AYLA_USER_ID) -> SkillContext:
    bot_user = Mock(
        channel="max",
        channel_user_id="12345",
        ayla_user_id=ayla_user_id,
        id="bot-user-1",
    )
    return SkillContext(
        conversation=Mock(id="conv-1", skill_state={}),
        bot_user=bot_user,
        message_text=f"{CALLBACK_PAYMENT_RETRY_PREFIX}{PAYMENT_ID}",
    )


def _tap(answer):
    """Тап по кнопке с подставленным клиентом платежей."""
    client = Mock()
    if isinstance(answer, Exception):
        client.retry_payment.side_effect = answer
    else:
        client.retry_payment.return_value = answer
    with patch.object(payment_skill, "get_ayla_payments_client", return_value=client):
        result = PaymentRetryCallbackSkill().handle(_context())
    return result, client


def _ok() -> RetryPaymentResult:
    return RetryPaymentResult(payment_id=PAYMENT_ID, confirmation_url=CHECKOUT, amount="2500")


class TestTheTapPays:
    def test_the_call_carries_the_payment_the_user_and_the_idempotency_key(self) -> None:
        result, client = _tap(_ok())

        assert CHECKOUT in result.reply_text
        kwargs = client.retry_payment.call_args.kwargs
        assert kwargs["payment_id"] == PAYMENT_ID
        assert kwargs["ayla_user_id"] == AYLA_USER_ID
        # Повтор тапа не создаёт второй платёж — ключ детерминирован.
        assert kwargs["idempotency_key"] == f"payment_retry:{PAYMENT_ID}"
        assert kwargs["external_user_id"] == "bot:max:12345"

    def test_tapping_twice_reuses_the_same_key(self) -> None:
        _first, client = _tap(_ok())
        first_key = client.retry_payment.call_args.kwargs["idempotency_key"]
        _second, client2 = _tap(_ok())
        assert client2.retry_payment.call_args.kwargs["idempotency_key"] == first_key


class TestEachRefusalSoundsLikeItself:
    def test_409_says_the_payment_is_no_longer_current(self) -> None:
        result, _ = _tap(AylaPaymentsRetryRefused("http_409"))
        assert result.reply_text == payment_skill._CLIENT_RETRY_OBSOLETE_TEMPLATE

    def test_provider_down_says_try_in_a_couple_of_minutes(self) -> None:
        result, _ = _tap(AylaPaymentsUnavailableError("http_503"))
        assert result.reply_text == payment_skill._CLIENT_RETRY_TRANSIENT_ERROR

    def test_the_two_do_not_share_words(self) -> None:
        obsolete, _ = _tap(AylaPaymentsRetryRefused("http_409"))
        transient, _ = _tap(AylaPaymentsUnavailableError("http_502"))
        assert obsolete.reply_text != transient.reply_text

    def test_not_authorized_says_the_button_is_not_yours(self) -> None:
        result, _ = _tap(AylaPaymentsNotAuthorized("http_403"))
        assert result.reply_text == payment_skill._CALLBACK_NOT_AUTHORIZED

    def test_an_unreadable_answer_is_transient_not_success(self) -> None:
        result, _ = _tap(AylaPaymentsAPIError("missing_confirmation_url"))
        assert result.reply_text == payment_skill._CLIENT_RETRY_TRANSIENT_ERROR
        assert CHECKOUT not in result.reply_text

    def test_a_user_without_an_ayla_bridge_is_refused_before_the_call(self) -> None:
        client = Mock()
        with patch.object(payment_skill, "get_ayla_payments_client", return_value=client):
            result = PaymentRetryCallbackSkill().handle(_context(ayla_user_id=None))
        assert result.reply_text == payment_skill._CALLBACK_NOT_AUTHORIZED
        client.retry_payment.assert_not_called()


class TestTheButtonIsNotALie:
    """Кнопка показана → тап не отвечает «недоступно» (просьба главного окна)."""

    def test_no_reply_text_says_payment_is_unavailable_via_the_bot(self) -> None:
        """Предмет — ТЕКСТЫ модуля, не его исходник: докстринг вправе назвать
        снятую заглушку, а ответ человеку — нет."""
        texts = {
            name: value
            for name, value in vars(payment_skill).items()
            if isinstance(value, str) and name.isupper() or name.startswith("_CLIENT")
        }
        # Присутствие: тексты ответов на месте и читаются — три отказа и успех.
        assert payment_skill._CLIENT_RETRY_SUCCESS_TEMPLATE in texts.values()
        assert payment_skill._CLIENT_RETRY_OBSOLETE_TEMPLATE in texts.values()
        assert payment_skill._CLIENT_RETRY_TRANSIENT_ERROR in texts.values()
        assert payment_skill._CALLBACK_NOT_AUTHORIZED in texts.values()
        # И среди них нет «недоступно через бот» — ни под старым именем, ни
        # под любым другим.
        assert [name for name, value in texts.items() if "временно недоступна" in value] == []
        assert "_CLIENT_RETRY_PENDING_TEMPLATE" not in vars(payment_skill)

    @pytest.mark.parametrize(
        "answer",
        [
            AylaPaymentsRetryRefused("http_409"),
            AylaPaymentsUnavailableError("http_503"),
            AylaPaymentsNotAuthorized("http_401"),
            AylaPaymentsAPIError("invalid_json"),
        ],
    )
    def test_every_outcome_is_one_of_the_written_texts(self, answer: Exception) -> None:
        result, _ = _tap(answer)
        assert result.reply_text in {
            payment_skill._CLIENT_RETRY_OBSOLETE_TEMPLATE,
            payment_skill._CLIENT_RETRY_TRANSIENT_ERROR,
            payment_skill._CALLBACK_NOT_AUTHORIZED,
        }
