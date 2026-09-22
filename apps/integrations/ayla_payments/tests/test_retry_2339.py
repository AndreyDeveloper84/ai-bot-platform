"""Повтор платежа через каталог — ``retry_payment`` (DRF-2339).

Кнопка «Оплатить» в личном сообщении об отказе платежа звала заглушку:
клиент платежей умел только ``create_payment``, а метода повтора не было
вовсе (TODO(#67) в ``payment_failed/skill.py``). Ручка каталога при этом
давно есть: ``POST /api/v1/payments/internal/<id>/retry/``,
``IsBotServiceWithVerifiedClient`` — bearer + ``X-External-User-ID`` +
перекрёстная проверка ``body.client_id``.

Контракт ручки (замер по ``payments/views.py`` и
``InternalPaymentRetrySerializer``):

* тело: ``{"client_id": <Ayla user uuid>}`` (``return_url`` необязателен —
  у каталога своё умолчание, бот адрес не выдумывает);
* заголовки: ``Authorization: Bearer``, ``X-External-User-ID``,
  ``X-Idempotency-Key`` — каноническое имя ключа идемпотентности
  (``_idempotency_key_from``); без него каталог сочинит новый ключ на
  каждый тап, и YooKassa создаст по платежу на попытку;
* ответы: 201 ``{payment_id, confirmation_url, amount}``, 401, 403
  (``client_id`` не тот), 404, 409 (``INVALID_STATUS`` — повтору не
  подлежит), 502, 503.

### Тестовый режим здесь НЕ предохранитель

``AYLA_PAYMENTS_TEST_MODE`` глушит ``create_payment`` заглушечной ссылкой.
Для повтора это было бы хуже отказа: человек получил бы «готово» и
поддельную ссылку после несостоявшегося платежа — тот самый класс
«сообщили о сделанном, которого не было». Поэтому повтор тестовый режим НЕ
читает: либо настоящий вызов, либо честный отказ. (Само умолчание
``getattr(settings, …, True)`` — отдельный лист DRF-2340.)

### Предохранитель — платёжный, не общий

Счётчик отказов живёт в экземпляре клиента платежей и ни с кем не делится:
5xx каталога по платежам не должен выключать запись. Деловой отказ (409) и
отказ доступа (401/403/404) предохранитель НЕ кормят — это ответ, а не
поломка.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
import requests  # type: ignore[import-untyped]

from apps.integrations.ayla_payments import (
    AylaPaymentsAPIError,
    AylaPaymentsClient,
    AylaPaymentsUnavailableError,
    reset_ayla_payments_client,
)
from apps.integrations.ayla_payments.client import (
    AylaPaymentsNotAuthorized,
    AylaPaymentsRetryRefused,
)

PAYMENT_ID = "6f1c6d3e-1f2a-4a7f-9a44-2f0d5a9b1111"
AYLA_USER_ID = "2b7f9c10-5d3e-4c1a-9f88-11aa22bb3344"
EXTERNAL_USER_ID = "bot:max:12345"
URL = "https://ayla.test/api/v1/payments/internal/" + PAYMENT_ID + "/retry"


@pytest.fixture(autouse=True)
def _reset_singleton():
    reset_ayla_payments_client()
    yield
    reset_ayla_payments_client()


def _client() -> AylaPaymentsClient:
    return AylaPaymentsClient(
        base_url="https://ayla.test",
        api_token="secret-token",
        test_mode=True,  # повтор его не читает — проверяется ниже
    )


def _response(status_code: int, json_body: dict | None = None, text: str = "") -> MagicMock:
    response = MagicMock(spec=requests.Response)
    response.status_code = status_code
    response.json.return_value = json_body if json_body is not None else {}
    response.text = text or ""
    return response


def _ok_body() -> dict:
    return {
        "data": {
            "payment_id": PAYMENT_ID,
            "confirmation_url": "https://yoomoney.example/checkout/abc",
            "amount": 2500.0,
        }
    }


def _retry(client: AylaPaymentsClient, response: MagicMock | Exception):
    with patch.object(client._session, "post") as post:
        if isinstance(response, Exception):
            post.side_effect = response
        else:
            post.return_value = response
        result = client.retry_payment(
            payment_id=PAYMENT_ID,
            ayla_user_id=AYLA_USER_ID,
            external_user_id=EXTERNAL_USER_ID,
            idempotency_key=f"payment_retry:{PAYMENT_ID}",
        )
    return result, post


class TestTheWire:
    def test_url_headers_and_body_match_the_catalogue_contract(self) -> None:
        client = _client()
        result, post = _retry(client, _response(201, _ok_body()))

        assert result.confirmation_url == "https://yoomoney.example/checkout/abc"
        assert result.payment_id == PAYMENT_ID
        kwargs = post.call_args.kwargs
        assert post.call_args.args[0] == URL
        assert kwargs["json"] == {"client_id": AYLA_USER_ID}
        headers = kwargs["headers"]
        assert headers["Authorization"] == "Bearer secret-token"
        assert headers["X-External-User-ID"] == EXTERNAL_USER_ID
        # Каноническое имя (``_idempotency_key_from`` читает его первым):
        # повтор тапа не создаёт второй платёж.
        assert headers["X-Idempotency-Key"] == f"payment_retry:{PAYMENT_ID}"

    def test_a_flat_body_without_the_data_envelope_is_read_too(self) -> None:
        client = _client()
        result, _ = _retry(
            client,
            _response(201, {"payment_id": PAYMENT_ID, "confirmation_url": "https://y.example/x"}),
        )
        assert result.confirmation_url == "https://y.example/x"

    def test_test_mode_does_not_stub_the_retry(self) -> None:
        # Присутствие вызова: заглушки здесь нет — иначе человек получил бы
        # «готово» и поддельную ссылку после неудачной оплаты.
        client = _client()
        assert client.test_mode is True
        _result, post = _retry(client, _response(201, _ok_body()))
        post.assert_called_once()


class TestRefusalsSoundDifferent:
    def test_409_is_a_business_answer_not_a_breakdown(self) -> None:
        client = _client()
        with pytest.raises(AylaPaymentsRetryRefused):
            _retry(client, _response(409, {"error": {"code": "INVALID_STATUS"}}))
        assert client._circuit.failures == []

    @pytest.mark.parametrize("status", [401, 403, 404])
    def test_auth_and_not_found_do_not_feed_the_breaker(self, status: int) -> None:
        client = _client()
        with pytest.raises(AylaPaymentsNotAuthorized):
            _retry(client, _response(status, {"error": {"code": "NOT_FOUND"}}))
        assert client._circuit.failures == []

    @pytest.mark.parametrize("status", [502, 503])
    def test_provider_errors_are_transient_and_do_feed_the_breaker(self, status: int) -> None:
        client = _client()
        with pytest.raises(AylaPaymentsUnavailableError):
            _retry(client, _response(status, {}))
        assert len(client._circuit.failures) == 1

    def test_a_timeout_is_transient(self) -> None:
        client = _client()
        with pytest.raises(AylaPaymentsUnavailableError):
            _retry(client, requests.exceptions.Timeout("slow"))
        assert len(client._circuit.failures) == 1

    def test_a_reply_without_a_url_is_not_a_success(self) -> None:
        client = _client()
        with pytest.raises(AylaPaymentsAPIError):
            _retry(client, _response(201, {"data": {"payment_id": PAYMENT_ID}}))


class TestPreconditions:
    @pytest.mark.parametrize(
        ("base_url", "token"),
        [("", "secret-token"), ("https://ayla.test", "")],
        ids=["no-base-url", "no-token"],
    )
    def test_an_unconfigured_client_refuses_instead_of_guessing(
        self, base_url: str, token: str
    ) -> None:
        client = AylaPaymentsClient(base_url=base_url, api_token=token, test_mode=False)
        with pytest.raises(AylaPaymentsAPIError):
            client.retry_payment(
                payment_id=PAYMENT_ID,
                ayla_user_id=AYLA_USER_ID,
                external_user_id=EXTERNAL_USER_ID,
                idempotency_key=f"payment_retry:{PAYMENT_ID}",
            )

    def test_an_open_breaker_answers_unavailable_without_calling(self) -> None:
        client = _client()
        with patch.object(client._circuit, "is_open", return_value=True):
            with patch.object(client._session, "post") as post:
                with pytest.raises(AylaPaymentsUnavailableError):
                    client.retry_payment(
                        payment_id=PAYMENT_ID,
                        ayla_user_id=AYLA_USER_ID,
                        external_user_id=EXTERNAL_USER_ID,
                        idempotency_key=f"payment_retry:{uuid4()}",
                    )
            post.assert_not_called()


class TestTheBreakerIsPaymentsOnly:
    def test_payment_failures_do_not_touch_other_clients(self) -> None:
        """Отказ платежей не выключает запись: счётчик — в экземпляре клиента.

        Сторож на то, за что уже дважды платили: штатный 5xx одной ручки
        выключал запись целиком. Счётчик здесь — поле экземпляра
        (``self._circuit``), а не модульный синглтон, поэтому соседние
        клиенты его не видят.
        """
        client = _client()
        for _ in range(3):
            with pytest.raises(AylaPaymentsUnavailableError):
                _retry(client, _response(503, {}))
        assert len(client._circuit.failures) == 3  # присутствие: счётчик рос

        # Другой экземпляр (и любой другой клиент) об этом не знает.
        second = _client()
        assert second._circuit.failures == []
        assert second._circuit is not client._circuit
