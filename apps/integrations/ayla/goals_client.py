"""Ayla goal-layer REST client (DRF-1190).

Thin sync proxy from bot-platform to Ayla's goal-layer endpoints
(shipped in beautygo_backend feat/goal-layer):

* ``GET  /api/v1/internal/me/decision-context/`` — эфемерный документ
  состояния (known / missing / suggestions / intents). Mini App — тупой
  отрисовщик: документ проксируется как есть, без трансляции.
* ``POST /api/v1/internal/me/goals/select/`` — фиксация выбора
  (``goal_key`` XOR ``goal_text`` XOR ``intent=need_guidance``); ответ —
  обновлённый документ состояния.

Auth per the identity-bridging contract (same as recommendations_client):
``Authorization: Bearer {AYLA_INTERNAL_API_TOKEN}`` +
``X-External-User-ID: bot:{channel}:{channel_user_id}``; client initData
never leaves bot-platform. URLs via AylaUrlBuilder (#1049).

Resilience mirrors recommendations_client (#1048): inline circuit
breaker (5 failures in 60s → 30s cooldown); 4xx does NOT trip it.

Холодный контур пилота (DRF-1435). Соединения переиспользуются через
пул на весь процесс (:func:`_get_client`), бюджеты на соединение и на
чтение разделены, а POST, истёкший по чтению, закрывается сверкой
состояния, а не повтором записи. Обоснование каждого числа и замер по
фазам — у констант ниже и в :func:`_reconcile_goal_select`.

Failure surface:

* :class:`GoalsConfigError` — token / base URL not configured → 503.
* :class:`GoalsBadRequest` — Ayla 4xx, body forwarded → 400.
* :class:`GoalsUnavailable` — timeout / network / 5xx / malformed JSON /
  circuit open → 502.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Final

import httpx
from django.conf import settings

from apps.integrations.ayla.url_builder import AylaUrlBuilder, AylaUrlError

logger = logging.getLogger(__name__)

# Goal calls happen on Mini App tap — the user is staring at a spinner.
#
# DRF-1435. Один общий бюджет на все фазы прятал, какая из них его съела.
# Замер по фазам на пилоте (2026-09-01, 2 vCPU, loadavg 6–15, iowait
# 54–86%, ~1.6 ГБ в свопе, 7–10 процессов постоянно в D-state):
#
#     фаза                                  холодный   тёплый
#     TLS-рукопожатие (appconnect-connect)    3.77 s    0.025 s
#     обработка запроса (ttfb-appconnect)     0.38 s    0.048 s
#     итого                                   4.04 s    0.09 s
#
# Стоимости отличаются в ~80 раз, поэтому и бюджеты разные:
#
# * соединение — редкое (пул живёт весь процесс) и холодное; 6 s это
#   измеренные 3.77 s с полуторным запасом. Больше не ставим сознательно:
#   пересидеть насыщенный диск хоста всё равно нельзя, а каждая лишняя
#   секунда здесь — секунда, которую человек смотрит на индикатор;
# * чтение — частое и тёплое (0.05–0.57 s по замеру); 5 s оставлены как
#   были, чтобы правка не удлинила ожидание ни на одном обычном запросе;
# * сверка (:func:`_reconcile_goal_select`) идёт по соединению, которое
#   только что доказало свою работоспособность, поэтому бюджет короче.
CONNECT_TIMEOUT_S: Final[float] = 6.0
READ_TIMEOUT_S: Final[float] = 5.0
RECONCILE_READ_TIMEOUT_S: Final[float] = 3.0

# Держим keep-alive заведомо короче, чем nginx перед Ayla (keepalive_timeout
# 65 s по умолчанию): иначе мы будем переиспользовать соединение, которое
# сервер уже закрыл, и платить за это разрывом на ровном месте.
KEEPALIVE_EXPIRY_S: Final[float] = 50.0

CIRCUIT_FAILURE_WINDOW_S: Final[float] = 60.0
CIRCUIT_FAILURE_THRESHOLD: Final[int] = 5
CIRCUIT_OPEN_DURATION_S: Final[float] = 30.0
_BREAKER_NAME: Final[str] = "ayla.goals"


def _fire_breaker_alert(transition: str, failures: int) -> None:
    """CR-3 alert path on a breaker transition — forensic only, must
    NEVER break the breaker (same contract as the other Ayla clients)."""
    try:
        from apps.orchestrator.llm.telegram_alert import send_breaker_alert

        send_breaker_alert(
            provider=_BREAKER_NAME,
            transition=transition,
            details={"failures": failures},
        )
    except Exception:  # noqa: BLE001 — alerting must NEVER break the breaker
        logger.exception("goals_client.alert_failed transition=%s", transition)


@dataclass
class _Circuit:
    """In-process breaker — mirrors recommendations_client._Circuit shape."""

    failures: list[float] = field(default_factory=list)
    opened_at: float | None = None

    def is_open(self, *, now: float) -> bool:
        if self.opened_at is None:
            return False
        if now - self.opened_at >= CIRCUIT_OPEN_DURATION_S:
            failures_before = len(self.failures)
            self.opened_at = None
            self.failures = []
            _fire_breaker_alert("open → closed", failures_before)
            return False
        return True

    def record_failure(self, *, now: float) -> None:
        cutoff = now - CIRCUIT_FAILURE_WINDOW_S
        self.failures = [t for t in self.failures if t >= cutoff]
        self.failures.append(now)
        if len(self.failures) >= CIRCUIT_FAILURE_THRESHOLD and self.opened_at is None:
            self.opened_at = now
            logger.warning(
                "goals_client.circuit_opened failures=%d window_s=%.0f",
                len(self.failures),
                CIRCUIT_FAILURE_WINDOW_S,
            )
            _fire_breaker_alert("closed → open", len(self.failures))

    def record_success(self) -> None:
        self.failures = []
        self.opened_at = None


# Module-level breaker — single instance per worker process.
_circuit = _Circuit()


def reset_goals_circuit() -> None:
    """Reset the module breaker — used by tests to isolate cases."""
    _circuit.record_success()


class GoalsConfigError(Exception):
    """``AYLA_BASE_URL`` / ``AYLA_INTERNAL_API_TOKEN`` not configured (or base malformed)."""


class GoalsBadRequest(Exception):
    """Ayla returned 4xx — body forwarded so the caller sees Ayla's reason."""

    def __init__(self, status_code: int, body: Any) -> None:
        self.status_code = status_code
        self.body = body
        super().__init__(f"ayla goals HTTP {status_code}")


class GoalsUnavailable(Exception):
    """Network/timeout/5xx/malformed JSON — caller maps to 502.

    ``cause`` — исходное исключение httpx, если отказ был сетевым. Нужно,
    чтобы отличить «запрос до Ayla не доехал» (ConnectTimeout/ConnectError)
    от «Ayla запрос получила и не успела ответить» (ReadTimeout): для
    записи это разные события, и обходятся они по-разному (DRF-1435).
    """

    def __init__(self, reason: str, *, cause: BaseException | None = None) -> None:
        self.reason = reason
        self.cause = cause
        super().__init__(reason)


# Пул на весь процесс — по образцу booking_client (CR-SF1). До DRF-1435
# ``_request`` строил httpx.Client НА КАЖДЫЙ вызов, то есть каждое нажатие
# пользователя платило полное DNS+TCP+TLS рукопожатие. На пилоте это
# измеренные 3.77 s накладных расходов, которые мы налагали на себя сами:
# экран целей открывается через GET decision-context, и идущий сразу за ним
# POST goals/select обязан ехать по уже открытому соединению.
_http: httpx.Client | None = None


def _get_client() -> httpx.Client:
    """Ленивый переиспользуемый клиент с пулом соединений."""
    global _http
    if _http is None or _http.is_closed:
        _http = httpx.Client(
            timeout=httpx.Timeout(
                connect=CONNECT_TIMEOUT_S,
                read=READ_TIMEOUT_S,
                write=READ_TIMEOUT_S,
                pool=CONNECT_TIMEOUT_S,
            ),
            limits=httpx.Limits(keepalive_expiry=KEEPALIVE_EXPIRY_S),
        )
    return _http


def close_goals_client() -> None:
    """Закрыть пул — для тестов и корректного завершения процесса."""
    global _http
    if _http is not None and not _http.is_closed:
        _http.close()
    _http = None


def _request(
    method: str,
    path: str,
    *,
    external_user_id: str,
    payload: dict[str, Any] | None = None,
    read_timeout_s: float | None = None,
) -> dict[str, Any]:
    """Single request seam for the goal layer — see module docstring for
    the failure contract. ``path`` is relative to ``/api/v1/``."""
    base_url = getattr(settings, "AYLA_BASE_URL", "")
    token = getattr(settings, "AYLA_INTERNAL_API_TOKEN", "")
    if not base_url or not token:
        raise GoalsConfigError("AYLA_BASE_URL or AYLA_INTERNAL_API_TOKEN not configured")

    try:
        url = AylaUrlBuilder(base_url).build(path)
    except AylaUrlError as exc:
        raise GoalsConfigError(f"invalid AYLA_BASE_URL: {exc}") from exc

    now = time.monotonic()
    if _circuit.is_open(now=now):
        raise GoalsUnavailable("circuit_open")

    headers = {
        "Authorization": f"Bearer {token}",
        "X-External-User-ID": external_user_id,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    # Бюджеты читаются на КАЖДЫЙ запрос, а не запекаются в клиент при
    # постройке: пул живёт весь процесс, и константы должны оставаться
    # наблюдаемыми (в том числе для тестов).
    timeout = httpx.Timeout(
        connect=CONNECT_TIMEOUT_S,
        read=READ_TIMEOUT_S if read_timeout_s is None else read_timeout_s,
        write=READ_TIMEOUT_S,
        pool=CONNECT_TIMEOUT_S,
    )

    try:
        http = _get_client()
        resp = http.request(method, url, headers=headers, json=payload, timeout=timeout)
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        _circuit.record_failure(now=time.monotonic())
        logger.warning(
            "goals_client.network_failure ext_user=%s exc=%s",
            external_user_id,
            type(exc).__name__,
        )
        raise GoalsUnavailable(f"network: {type(exc).__name__}", cause=exc) from exc

    if resp.status_code >= 500:
        _circuit.record_failure(now=time.monotonic())
        logger.warning(
            "goals_client.server_error ext_user=%s status=%d",
            external_user_id,
            resp.status_code,
        )
        raise GoalsUnavailable(f"server: HTTP {resp.status_code}")

    if 400 <= resp.status_code < 500:
        # 4xx = WE sent garbage, not that Ayla is down — forward the body,
        # do NOT trip the breaker.
        try:
            body: Any = resp.json()
        except ValueError:
            body = {"detail": resp.text[:500]}
        logger.warning(
            "goals_client.client_error ext_user=%s status=%d",
            external_user_id,
            resp.status_code,
        )
        raise GoalsBadRequest(resp.status_code, body)

    if resp.status_code != 200:
        _circuit.record_failure(now=time.monotonic())
        raise GoalsUnavailable(f"unexpected: HTTP {resp.status_code}")

    try:
        body = resp.json()
    except ValueError as exc:
        _circuit.record_failure(now=time.monotonic())
        raise GoalsUnavailable(f"malformed_json: {exc}") from exc

    if not isinstance(body, dict):
        _circuit.record_failure(now=time.monotonic())
        raise GoalsUnavailable("malformed: top-level is not an object")

    _circuit.record_success()
    return body


def fetch_decision_context(*, external_user_id: str) -> dict[str, Any]:
    """GET ``/internal/me/decision-context/`` — документ состояния as-is."""
    return _request(
        "GET",
        "internal/me/decision-context/",
        external_user_id=external_user_id,
    )


def post_goal_select(
    *,
    external_user_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """POST ``/internal/me/goals/select/`` — возвращает обновлённый документ.

    ``payload`` forwarded as-is (``goal_key`` / ``goal_text`` / ``intent``
    + ``source_channel``); shape validation lives on Ayla's side — this
    layer is a translation hop, not a schema gate.

    DRF-1435: истёкшее чтение НЕ означает, что запись не прошла — ReadTimeout
    это «Ayla запрос получила и не успела ответить». Поэтому такой отказ
    закрывается сверкой состояния (:func:`_reconcile_goal_select`), а не
    повтором POST: повтор не идемпотентен по событию воронки
    (``goals/api.py:_emit_goal_selected`` создаёт новую строку на каждый
    вызов) и по строке ``ClientGoal``.
    """
    try:
        return _request(
            "POST",
            "internal/me/goals/select/",
            external_user_id=external_user_id,
            payload=payload,
        )
    except GoalsUnavailable as exc:
        reconciled = _reconcile_goal_select(
            external_user_id=external_user_id,
            payload=payload,
            exc=exc,
        )
        if reconciled is None:
            raise
        return reconciled


def _selected_goal_matches(goal: Any, payload: dict[str, Any]) -> bool:
    """Стоит ли в документе ровно та цель, которую мы пытались записать."""
    if not isinstance(goal, dict):
        return False
    goal_key = payload.get("goal_key")
    if goal_key:
        return goal.get("goal_key") == goal_key
    goal_text = (payload.get("goal_text") or "").strip()
    if goal_text:
        return (goal.get("goal_text") or "").strip() == goal_text
    return False


def _reconcile_goal_select(
    *,
    external_user_id: str,
    payload: dict[str, Any],
    exc: GoalsUnavailable,
) -> dict[str, Any] | None:
    """Один дешёвый GET: не оказалась ли цель уже записанной.

    Возвращает актуальный документ, если в нём стоит ровно та цель, которую
    мы отправляли, иначе ``None`` — и тогда вызывающая сторона поднимает
    исходный отказ.

    Сверка делается ТОЛЬКО когда:

    * отказ был именно по чтению (``ReadTimeout``). При ConnectTimeout /
      ConnectError запрос до Ayla не доехал, сверять нечего, и лишний
      запрос лишь удлинит ожидание;
    * в payload есть ``goal_key`` или ``goal_text``. У
      ``intent=need_guidance`` durable-следа нет вовсе — ``ClientGoal`` не
      создаётся (``goals/api.py``), — поэтому подтвердить его по документу
      невозможно, и притворяться, что можно, нельзя.

    Что здесь сознательно НЕ различается: наш это был запрос или та же цель
    уже стояла раньше. Подтверждается желаемое состояние («цель — X»), а не
    авторство конкретной записи; сравнивать ``selected_at`` с локальными
    часами бота через расхождение часов двух машин было бы менее надёжно,
    чем сам факт.
    """
    if not isinstance(exc.cause, httpx.ReadTimeout):
        return None
    leaves_durable_trace = bool(payload.get("goal_key")) or bool(
        (payload.get("goal_text") or "").strip()
    )
    if not leaves_durable_trace:
        return None

    try:
        document = _request(
            "GET",
            "internal/me/decision-context/",
            external_user_id=external_user_id,
            read_timeout_s=RECONCILE_READ_TIMEOUT_S,
        )
    except (GoalsUnavailable, GoalsBadRequest, GoalsConfigError):
        logger.warning(
            "goals_client.reconcile_failed ext_user=%s reason=%s",
            external_user_id,
            exc.reason,
        )
        return None

    known = document.get("known")
    goal = known.get("goal") if isinstance(known, dict) else None
    if not _selected_goal_matches(goal, payload):
        logger.warning(
            "goals_client.reconcile_miss ext_user=%s reason=%s",
            external_user_id,
            exc.reason,
        )
        return None

    logger.info(
        "goals_client.reconciled ext_user=%s reason=%s",
        external_user_id,
        exc.reason,
    )
    return document
