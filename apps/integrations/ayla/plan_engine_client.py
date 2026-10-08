"""Клиент Plan Engine каталога — сборка эфемерного плана (DRF-2879).

``POST /internal/me/plan/decision/`` собирает план по действующей цели
человека и ничего не сохраняет. Безопасность и реестр планировочных правил
каталог не вычисляет — их приносит бот в теле запроса.

Ответ 200 всегда несёт ``outcome``: ``PLAN`` либо один из штатных исходов без
плана (``SAFETY_BLOCKED``, ``NO_GOAL``, ``NO_CURATED_DECOMPOSITION``,
``PLAN_NOT_JUSTIFIED``). Это ответы, а не ошибки, и клиент их не различает —
отдаёт документ как есть: решение о плане принял каталог.

Ошибки — по имени: функция выключена в каталоге, запрос отвергнут как
неконформный, каталог недоступен. Тела в лог не идут — только статус и класс.

Субъект — ``X-External-User-ID`` из подписанных данных Mini App; в теле
идентификатора человека нет.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from django.conf import settings

from apps.integrations.ayla.request_id import with_request_id
from apps.integrations.ayla.url_builder import AylaUrlBuilder, AylaUrlError

logger = logging.getLogger(__name__)

_DECISION_PATH = "internal/me/plan/decision/"
DEFAULT_TIMEOUT_S = 10.0


class PlanEngineError(Exception):
    """Базовая ошибка клиента Plan Engine."""


class PlanEngineConfigError(PlanEngineError):
    """Адрес или токен каталога не настроены."""


class PlanEngineAuthError(PlanEngineError):
    """Каталог не принял служебную авторизацию."""


class PlanEngineUnavailableError(PlanEngineError):
    """Сеть, 5xx или ответ не в форме договора."""


class PlanEngineDisabledError(PlanEngineError):
    """Plan Engine выключен в каталоге (404 ``PLAN_ENGINE_DISABLED``)."""


class PlanEngineContractError(PlanEngineError):
    """Каталог отверг запрос как неконформный (400 ``PLAN_CONTRACT_VIOLATION``).

    ``reason`` — имя нарушения из каталога, не проза и не данные человека.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason or "contract_violation")
        self.reason = reason


class PlanEngineHttpClient:
    """Параметры конструктора — подмены настроек для тестов; в проде без аргументов."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        token: str | None = None,
        timeout: float | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._base_url = (
            base_url if base_url is not None else getattr(settings, "AYLA_BASE_URL", "")
        )
        self._token = (
            token if token is not None else getattr(settings, "AYLA_INTERNAL_API_TOKEN", "")
        )
        self._timeout = timeout if timeout is not None else DEFAULT_TIMEOUT_S
        self._http: httpx.Client | None = http_client

    def compose_decision(
        self,
        *,
        external_user_id: str,
        safety_state: str,
        safety_policy_version: str,
        rules_registry: dict[str, Any],
        excluded_capability_refs: list[str],
    ) -> dict[str, Any]:
        """Собрать эфемерный план. Возвращает документ ответа каталога как есть."""
        try:
            url = AylaUrlBuilder(self._base_url).build(_DECISION_PATH)
        except AylaUrlError as exc:
            raise PlanEngineConfigError(f"invalid AYLA_BASE_URL: {exc}") from exc
        if not self._token:
            raise PlanEngineConfigError("AYLA_INTERNAL_API_TOKEN not configured")

        body = {
            "safety_state": safety_state,
            "safety_policy_version": safety_policy_version,
            "rules_registry": rules_registry,
            "excluded_capability_refs": list(excluded_capability_refs),
        }
        try:
            response = self._client().post(
                url,
                headers=with_request_id(
                    {
                        "Authorization": f"Bearer {self._token}",
                        "X-External-User-ID": external_user_id,
                        "Accept": "application/json",
                    }
                ),
                json=body,
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            logger.warning("plan_engine.decision.network_failure exc=%s", type(exc).__name__)
            raise PlanEngineUnavailableError(f"network: {type(exc).__name__}") from exc

        if response.status_code in (401, 403):
            raise PlanEngineAuthError(f"plan engine auth failed: HTTP {response.status_code}")
        if response.status_code >= 500:
            logger.warning("plan_engine.decision.server_error status=%d", response.status_code)
            raise PlanEngineUnavailableError(f"server: HTTP {response.status_code}")
        if 400 <= response.status_code < 500:
            raise _refusal(response)

        try:
            payload = response.json()
        except ValueError as exc:
            raise PlanEngineUnavailableError("malformed_json") from exc
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict) or not isinstance(data.get("outcome"), str):
            # Ответ без имени исхода — не «плана нет», а нарушение договора:
            # выдать его экрану значило бы показать пустоту как решение.
            raise PlanEngineUnavailableError("outcome_missing")
        return data

    def _client(self) -> httpx.Client:
        if self._http is None:
            self._http = httpx.Client(timeout=self._timeout)
        return self._http


def _refusal(response: httpx.Response) -> PlanEngineError:
    code, reason = _error_code_and_reason(response)
    if response.status_code == 404 and code == "PLAN_ENGINE_DISABLED":
        return PlanEngineDisabledError("plan_engine_disabled")
    if response.status_code == 400 and code == "PLAN_CONTRACT_VIOLATION":
        return PlanEngineContractError(reason)
    return PlanEngineUnavailableError(f"unexpected 4xx: HTTP {response.status_code} {code}")


def _error_code_and_reason(response: httpx.Response) -> tuple[str, str]:
    try:
        payload = response.json()
    except ValueError:
        return "", ""
    error = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(error, dict):
        return "", ""
    details = error.get("details")
    reason = details.get("reason") if isinstance(details, dict) else None
    code = error.get("code")
    return (code if isinstance(code, str) else "", reason if isinstance(reason, str) else "")


__all__ = [
    "PlanEngineAuthError",
    "PlanEngineConfigError",
    "PlanEngineContractError",
    "PlanEngineDisabledError",
    "PlanEngineError",
    "PlanEngineHttpClient",
    "PlanEngineUnavailableError",
]
