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
from typing import Any, TypedDict

import httpx
from django.conf import settings

from apps.integrations.ayla.request_id import with_request_id
from apps.integrations.ayla.url_builder import AylaUrlBuilder, AylaUrlError

logger = logging.getLogger(__name__)

_DECISION_PATH = "internal/me/plan/decision/"
_PLAN_PATH = "internal/me/plan/"
_LABELS_PATH = "internal/me/plan/capability-labels/"
_REPLACE_PATH = "internal/me/plan/replace/"
_STATE_PATH = "internal/me/plan/state/"
DEFAULT_TIMEOUT_S = 10.0


class CapabilityDetails(TypedDict):
    """Слова каталога о способности: подпись шага и «зачем» (если есть)."""

    label: str
    expected_effect: str | None


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


class PlanSaveSafetyBlockedError(PlanEngineError):
    """409 ``PLAN_SAVE_SAFETY_BLOCKED`` — вердикт хода не даёт сохранить план."""


class PlanIdempotencyConflictError(PlanEngineError):
    """409 ``PLAN_IDEMPOTENCY_CONFLICT`` — это подтверждение уже ушло под другой план."""


class PlanGoalNotFoundError(PlanEngineError):
    """404 ``NOT_FOUND`` / ``goal_not_found`` — цели, к которой собран план, уже нет."""


class PlanCapabilityNotConfirmedError(PlanEngineError):
    """409 ``PLAN_CAPABILITY_NOT_CONFIRMED`` — способность шага больше не в знании."""


class PlanReplacementTargetChangedError(PlanEngineError):
    """409 ``PLAN_REPLACEMENT_TARGET_CHANGED`` — действующим стал другой план
    (или никакой): «да» человека относилось не к нему."""


class PlanTransitionRefusedError(PlanEngineError):
    """409 ``PLAN_TRANSITION_REFUSED`` — план уже не в том состоянии: это не
    предложение (замещено новым или отклонено), либо переход не разрешён."""


class PlanNotFoundError(PlanEngineError):
    """404 ``plan_not_found`` — план не этого человека или его нет."""


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

    def save_plan(self, *, external_user_id: str, command: dict[str, Any]) -> dict[str, Any]:
        """``POST internal/me/plan/`` — сохранить подтверждённое решение (DRF-2885).

        ``command`` — команда каталога целиком: решение из ``decision/`` без
        правок, подтверждение, тройка хода подтверждения, ограничения. Ответ —
        ``{"plan": <документ>, "created": bool}``; повтор той же команды каталог
        узнаёт сам (``created=False``), второго плана не будет.

        У цели уже есть действующий план — каталог сохраняет новый как
        ПРЕДЛОЖЕНИЕ (``plan.status == "proposed"``) и называет, какой план оно
        заменит: ``replaces.plan_id``. Действующим его делает только
        :meth:`replace_plan` — отдельным подтверждением человека.
        """
        data = self._post(_PLAN_PATH, external_user_id=external_user_id, body=command, op="save")
        if not isinstance(data.get("plan"), dict) or not isinstance(data.get("created"), bool):
            raise PlanEngineUnavailableError("plan_missing")
        return data

    def replace_plan(
        self,
        *,
        external_user_id: str,
        plan_id: str,
        replaces_plan_id: str,
        safety_state: str,
        safety_policy_version: str,
        evaluated_at_revision: int,
    ) -> dict[str, Any]:
        """``POST internal/me/plan/replace/`` — подтверждённая замена плана.

        ``replaces_plan_id`` — план, о замене которого человек сказал «да».
        Ответ ``{"plan": <документ>, "replaced": bool}``; ``replaced=False`` —
        замена уже выполнена (повторное нажатие), не ошибка.
        """
        data = self._post(
            _REPLACE_PATH,
            external_user_id=external_user_id,
            body={
                "plan_id": plan_id,
                "replaces_plan_id": replaces_plan_id,
                "safety_state": safety_state,
                "safety_policy_version": safety_policy_version,
                "evaluated_at_revision": evaluated_at_revision,
            },
            op="replace",
        )
        if not isinstance(data.get("plan"), dict) or not isinstance(data.get("replaced"), bool):
            raise PlanEngineUnavailableError("plan_missing")
        return data

    def archive_plan(self, *, external_user_id: str, plan_id: str) -> dict[str, Any]:
        """``POST internal/me/plan/state/`` c ``archived`` — отказ от предложения.

        Действующий план при этом не меняется. Повтор — не ошибка: каталог
        отвечает тем же планом.
        """
        data = self._post(
            _STATE_PATH,
            external_user_id=external_user_id,
            body={"plan_id": plan_id, "state": "archived"},
            op="state",
        )
        if not isinstance(data.get("plan"), dict):
            raise PlanEngineUnavailableError("plan_missing")
        return data

    def get_plan(self, *, external_user_id: str) -> dict[str, Any] | None:
        """``GET internal/me/plan/`` — сохранённый план действующей цели или ``None``.

        ``None`` — штатный ответ каталога «плана нет» (в том числе при
        выключенном механизме), не ошибка.
        """
        try:
            url = AylaUrlBuilder(self._base_url).build(_PLAN_PATH)
        except AylaUrlError as exc:
            raise PlanEngineConfigError(f"invalid AYLA_BASE_URL: {exc}") from exc
        if not self._token:
            raise PlanEngineConfigError("AYLA_INTERNAL_API_TOKEN not configured")
        try:
            response = self._client().get(
                url,
                headers=with_request_id(
                    {
                        "Authorization": f"Bearer {self._token}",
                        "X-External-User-ID": external_user_id,
                        "Accept": "application/json",
                    }
                ),
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            logger.warning("plan_engine.read.network_failure exc=%s", type(exc).__name__)
            raise PlanEngineUnavailableError(f"network: {type(exc).__name__}") from exc
        if response.status_code in (401, 403):
            raise PlanEngineAuthError(f"plan engine auth failed: HTTP {response.status_code}")
        if response.status_code != 200:
            raise PlanEngineUnavailableError(f"read: HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise PlanEngineUnavailableError("malformed_json") from exc
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict) or "plan" not in data:
            raise PlanEngineUnavailableError("plan_key_missing")
        plan = data["plan"]
        if plan is not None and not isinstance(plan, dict):
            raise PlanEngineUnavailableError("plan_malformed")
        return plan

    def capability_labels(self, *, external_user_id: str, keys: list[str]) -> dict[str, str]:
        """``POST …/capability-labels/`` → ``{ключ: подпись}`` только для подписанных.

        Способность без подтверждённой подписи в ответ не попадает: показывать
        человеку ключ вместо слов нельзя, а сочинять подпись — тем более.
        """
        details = self.capability_details(external_user_id=external_user_id, keys=keys)
        return {key: row["label"] for key, row in details.items()}

    def capability_details(
        self, *, external_user_id: str, keys: list[str]
    ) -> dict[str, CapabilityDetails]:
        """Тот же вызов, что :meth:`capability_labels`, — подпись и «зачем».

        ``expected_effect`` — курируемый «ожидаемый эффект» способности из
        знания каталога: ответ на «почему этот шаг» (решение главного окна
        09.10 по выбору владельца «из обоснования»). Каталог начнёт отдавать
        его отдельным PR; пока поля нет — ``None``, и это не ошибка: шаг без
        текста «зачем» показывается без него, сочинять текст нельзя.

        Способность без подтверждённой подписи в ответ не попадает вовсе —
        и её «зачем» тоже: объяснение шага, который нечем назвать, не нужно.
        """
        data = self._post(
            _LABELS_PATH, external_user_id=external_user_id, body={"keys": list(keys)}, op="labels"
        )
        labels = data.get("labels")
        if not isinstance(labels, dict):
            raise PlanEngineUnavailableError("labels_missing")
        out: dict[str, CapabilityDetails] = {}
        for key, item in labels.items():
            if (
                isinstance(item, dict)
                and item.get("state") == "labelled"
                and isinstance(item.get("label"), str)
                and item["label"].strip()
            ):
                effect = item.get("expected_effect")
                out[str(key)] = {
                    "label": item["label"].strip(),
                    "expected_effect": effect.strip()
                    if isinstance(effect, str) and effect.strip()
                    else None,
                }
        return out

    def _post(
        self, path: str, *, external_user_id: str, body: dict[str, Any], op: str
    ) -> dict[str, Any]:
        try:
            url = AylaUrlBuilder(self._base_url).build(path)
        except AylaUrlError as exc:
            raise PlanEngineConfigError(f"invalid AYLA_BASE_URL: {exc}") from exc
        if not self._token:
            raise PlanEngineConfigError("AYLA_INTERNAL_API_TOKEN not configured")
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
            logger.warning("plan_engine.%s.network_failure exc=%s", op, type(exc).__name__)
            raise PlanEngineUnavailableError(f"network: {type(exc).__name__}") from exc

        if response.status_code in (401, 403):
            raise PlanEngineAuthError(f"plan engine auth failed: HTTP {response.status_code}")
        if response.status_code >= 500:
            logger.warning("plan_engine.%s.server_error status=%d", op, response.status_code)
            raise PlanEngineUnavailableError(f"server: HTTP {response.status_code}")
        if 400 <= response.status_code < 500:
            raise _refusal(response)
        try:
            payload = response.json()
        except ValueError as exc:
            raise PlanEngineUnavailableError("malformed_json") from exc
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            raise PlanEngineUnavailableError("data_missing")
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
    if response.status_code == 409 and code == "PLAN_SAVE_SAFETY_BLOCKED":
        return PlanSaveSafetyBlockedError("plan_save_safety_blocked")
    if response.status_code == 409 and code == "PLAN_IDEMPOTENCY_CONFLICT":
        return PlanIdempotencyConflictError("plan_idempotency_conflict")
    if response.status_code == 409 and code == "PLAN_CAPABILITY_NOT_CONFIRMED":
        return PlanCapabilityNotConfirmedError("plan_capability_not_confirmed")
    if response.status_code == 409 and code == "PLAN_REPLACEMENT_TARGET_CHANGED":
        return PlanReplacementTargetChangedError("plan_replacement_target_changed")
    if response.status_code == 409 and code == "PLAN_TRANSITION_REFUSED":
        return PlanTransitionRefusedError("plan_transition_refused")
    if response.status_code == 404 and reason == "goal_not_found":
        return PlanGoalNotFoundError("goal_not_found")
    if response.status_code == 404 and (reason == "plan_not_found" or code == "NOT_FOUND"):
        return PlanNotFoundError("plan_not_found")
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
    "PlanCapabilityNotConfirmedError",
    "PlanEngineAuthError",
    "PlanEngineConfigError",
    "PlanEngineContractError",
    "PlanEngineDisabledError",
    "PlanEngineError",
    "PlanEngineHttpClient",
    "PlanEngineUnavailableError",
]
