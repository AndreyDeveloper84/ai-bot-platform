"""HTTP client for Ayla's Personal Plan **wellness-context** internal API (DRF-1344).

One read, and only one:

* ``GET /api/v1/internal/me/wellness-context/`` — эфемерный документ
  состояния Personal Plan для одного получателя. Контракт::

      {"data": {
          "plan": {...} | null,
          "outcomes": [
              {"target": ..., "link_status": ..., "horizon_status": ...,
               "progress_state": ...}
          ],
          "gated": {...} | null,
      }}

  ``gated`` не ``null``, когда контур Ayla сам закрыл выдачу (например,
  пока не пройдён узкий consent scope из DRF-1333): план и ряды тогда не
  раскрываются вовсе, и документ — это отказ, а не данные.

Auth per the identity-bridging contract (same as ``goals_client``):
``Authorization: Bearer {AYLA_INTERNAL_API_TOKEN}`` +
``X-External-User-ID: bot:{channel}:{channel_user_id}``. URLs via
:class:`AylaUrlBuilder` (#1049). GET — идемпотентен сам по себе, ключей
идемпотентности не нужно; повторных попыток клиент не делает: вызывающий
планировщик тикает снова, и дешевле пропустить тик, чем держать backoff.

### Что клиент намеренно не делает

* **Не задерживает содержимое ответа.** От ``plan`` и ``gated`` DTO
  хранит только факт наличия, от каждого outcome — четыре кода
  (``target``, ``link_status``, ``horizon_status``, ``progress_state``).
  Значения наблюдений умирают в парсере: решающему слою по контракту
  задачи положены коды и ничего больше. Тела не логируются.
* **Не валидирует каталог кодов.** Ayla может добавлять значения
  аддитивно; неизвестный код — это не ошибка транспорта.

Failure surface:

* :class:`WellnessContextConfigError` — token / base URL не сконфигурированы.
* :class:`WellnessContextAuthError` — 401/403 (токен или прокси-права).
* :class:`WellnessContextClientError` — прочие 4xx (баг контракта).
* :class:`WellnessContextUnavailableError` — сеть / 5xx / битый JSON;
  планировщик маппит в пропуск тика (``ayla_unavailable``).

Plan Lite (DRF-2101, DRF-2123) живёт рядом тем же auth: ``create_plan_lite``
/ ``close_plan_lite`` — писатели, ``get_plan_lite_proposal`` — предложение из
шаблона активной цели (План-A); отказы каталога — по имени
(:class:`PlanLiteDisabledError`, :class:`PlanLiteAlreadyActiveError`,
:class:`PlanLiteGoalNotFoundError`, :class:`PlanLiteNoTemplateError`).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Final

import httpx
from django.conf import settings

from apps.integrations.ayla.url_builder import AylaUrlBuilder, AylaUrlError
from apps.integrations.ayla.request_id import with_request_id

logger = logging.getLogger(__name__)

#: Это чтение для фонового тика, а не для тапа в Mini App: бюджет щедрее,
#: чем у goals_client (5 s), но всё ещё короче любого beat-интервала.
DEFAULT_TIMEOUT_S: Final[float] = 10.0

_PATH: Final[str] = "internal/me/wellness-context/"
#: DRF-2101 — писатель Plan Lite (тот же auth, что у чтения).
_PLAN_LITE_PATH: Final[str] = "internal/me/plan-lite/"
#: DRF-2123 (План-A) — предложение из шаблона цели; ничего не создаёт.
_PLAN_LITE_PROPOSAL_PATH: Final[str] = "internal/me/plan-lite/proposal/"


# ---------------------------------------------------------------------------
# DTOs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OutcomeState:
    """Один Desired Outcome из документа — ТОЛЬКО коды, никогда значения.

    ``target`` — код Desired Outcome (например, «вес» как сущность, не
    число). ``progress_state`` / ``horizon_status`` / ``link_status`` —
    коды состояния ряда наблюдений. Значения наблюдений сюда не попадают
    конструкцией: у DTO просто нет полей под них.
    """

    target: str
    link_status: str
    horizon_status: str
    progress_state: str


@dataclass(frozen=True)
class PlanLiteAction:
    """Одно обязательство Plan Lite (DRF-2101) — форма и факт, ничего о результате.

    ``done_count`` ≤ ``target_count`` за текущее ведро ``[bucket_start,
    bucket_end)``; процентов и «достигнуто» у DTO нет полей — В-5.
    """

    action_type: str
    cadence: str
    target_count: int
    done_count: int
    bucket_start: str
    bucket_end: str


@dataclass(frozen=True)
class PlanLite:
    """Активный Plan Lite человека — ключ цели и обязательства с фактами."""

    plan_id: str
    goal_key: str
    actions: tuple[PlanLiteAction, ...] = ()


@dataclass(frozen=True)
class PlanLiteProposalAction:
    """Одно действие из шаблона (DRF-2123) — только форма: тип, каденс, сколько.

    Фактов (``done_count``, ведро) у предложения нет по построению: план
    ещё не создан. Каденс — код каталога (``per_day`` / ``per_week`` /
    ``per_2_weeks``), не валидируется: каталог добавляет значения аддитивно.
    """

    action_type: str
    cadence: str
    target_count: int


@dataclass(frozen=True)
class PlanLiteProposal:
    """Предложение Plan Lite из шаблона активной цели (DRF-2123, План-A).

    ``why`` — текст шаблона (курируемый, не про человека), ``template_version``
    уходит обратно в ``create_plan_lite`` как провенанс плана. Процентов и
    шкал у DTO нет полей — В-5.
    """

    goal_key: str
    why: str
    template_version: int
    actions: tuple[PlanLiteProposalAction, ...] = ()


@dataclass(frozen=True)
class WellnessContext:
    """Документ ``wellness-context`` одного получателя.

    Presence-only по контракту тикета: Personal Plan поставляет в решающий
    слой код результата, факты о состоянии ряда и семейство — и ничего
    больше. Поэтому от ``plan`` остаётся сам факт наличия, от ``gated`` —
    факт отказа контура; содержимое обоих блоков (там могут лежать
    значения наблюдений) умирает в парсере и в процессе не задерживается.
    """

    has_plan: bool
    outcomes: tuple[OutcomeState, ...] = ()
    gated: bool = False
    #: DRF-2101 — Plan Lite стоит рядом с гейтами, не за ними: факты
    #: действий за текущее ведро. ``None`` — плана нет или флаг выключен.
    plan_lite: PlanLite | None = None


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class WellnessContextError(Exception):
    """Base — anything wellness-context-side that's not the happy path."""


class WellnessContextConfigError(WellnessContextError):
    """``AYLA_BASE_URL`` / ``AYLA_INTERNAL_API_TOKEN`` not configured."""


class WellnessContextAuthError(WellnessContextError):
    """401 / 403 from Ayla. Bearer token mismatch or proxy rights missing."""


class WellnessContextClientError(WellnessContextError):
    """Other 4xx — contract bug on either side."""


class WellnessContextUnavailableError(WellnessContextError):
    """Network / timeout / 5xx / malformed JSON — skip this tick."""


class PlanLiteDisabledError(WellnessContextClientError):
    """404 ``PLAN_LITE_DISABLED`` — каталог держит Plan Lite выключенным (DRF-2101)."""


class PlanLiteAlreadyActiveError(WellnessContextClientError):
    """409 ``PLAN_LITE_ALREADY_ACTIVE`` — активный план уже есть; сперва закрыть."""


class PlanLiteGoalNotFoundError(WellnessContextClientError):
    """404 ``NOT_FOUND`` — цель не у этого человека / не активна, или нет
    активного плана при закрытии; у предложения — ``details.reason=no_active_goal``."""


class PlanLiteNoTemplateError(WellnessContextClientError):
    """404 ``NOT_FOUND`` с ``details.reason=no_template`` — у активной цели нет
    шаблона плана (DRF-2123). Не подкласс :class:`PlanLiteGoalNotFoundError`:
    экран на этих двух 404 расходится (конструктор vs «сначала выбери цель»)."""


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class WellnessContextHttpClient:
    """Fetch the Personal Plan wellness-context document for one recipient.

    Construction params are settings overrides for tests; prod code calls
    ``WellnessContextHttpClient()`` and reads ``AYLA_BASE_URL`` /
    ``AYLA_INTERNAL_API_TOKEN`` from Django settings. An injected
    ``http_client`` (e.g. ``httpx.MockTransport``) fakes the wire in tests.
    """

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
        self._timeout = (
            timeout
            if timeout is not None
            else getattr(settings, "WELLNESS_CONTEXT_HTTP_TIMEOUT", DEFAULT_TIMEOUT_S)
        )
        self._http: httpx.Client | None = http_client

    def get_wellness_context(self, *, external_user_id: str) -> WellnessContext:
        """``GET /internal/me/wellness-context/`` — документ состояния."""
        try:
            url = AylaUrlBuilder(self._base_url).build(_PATH)
        except AylaUrlError as exc:
            raise WellnessContextConfigError(f"invalid AYLA_BASE_URL: {exc}") from exc
        if not self._token:
            raise WellnessContextConfigError("AYLA_INTERNAL_API_TOKEN not configured")

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
            # В лог — только класс ошибки и адресат-идентификатор, не URL
            # с query и не тело: тела на этом контуре могут нести значения.
            logger.warning(
                "wellness_context.http.network_failure ext=%s exc=%s",
                external_user_id,
                type(exc).__name__,
            )
            raise WellnessContextUnavailableError(f"network: {type(exc).__name__}") from exc

        if response.status_code in (401, 403):
            raise WellnessContextAuthError(
                f"Ayla wellness-context auth failed: HTTP {response.status_code}"
            )
        if 400 <= response.status_code < 500:
            raise WellnessContextClientError(
                f"Ayla wellness-context 4xx: HTTP {response.status_code}"
            )
        if response.status_code >= 500:
            logger.warning(
                "wellness_context.http.server_error ext=%s status=%d",
                external_user_id,
                response.status_code,
            )
            raise WellnessContextUnavailableError(f"server: HTTP {response.status_code}")

        try:
            payload = response.json()
        except ValueError as exc:
            raise WellnessContextUnavailableError("malformed_json") from exc
        return _context_from_wire(payload)

    def _client(self) -> httpx.Client:
        if self._http is None:
            self._http = httpx.Client(timeout=self._timeout)
        return self._http

    # ─── Plan Lite — DRF-2101 ─────────────────────────────────────────────

    def _plan_lite_request(
        self,
        method: str,
        *,
        external_user_id: str,
        body: dict[str, Any] | None = None,
        path: str = _PLAN_LITE_PATH,
    ) -> httpx.Response:
        try:
            url = AylaUrlBuilder(self._base_url).build(path)
        except AylaUrlError as exc:
            raise WellnessContextConfigError(f"invalid AYLA_BASE_URL: {exc}") from exc
        if not self._token:
            raise WellnessContextConfigError("AYLA_INTERNAL_API_TOKEN not configured")
        try:
            response = self._client().request(
                method,
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
            # Тела и id не логируются: статус и класс — всё, что нужно (DRF-2009).
            logger.warning(
                "wellness_context.plan_lite.network_failure method=%s exc=%s",
                method,
                type(exc).__name__,
            )
            raise WellnessContextUnavailableError(f"network: {type(exc).__name__}") from exc
        if response.status_code in (401, 403):
            raise WellnessContextAuthError(
                f"Ayla plan-lite auth failed: HTTP {response.status_code}"
            )
        if response.status_code >= 500:
            logger.warning(
                "wellness_context.plan_lite.server_error method=%s status=%d",
                method,
                response.status_code,
            )
            raise WellnessContextUnavailableError(f"server: HTTP {response.status_code}")
        if 400 <= response.status_code < 500:
            raise _plan_lite_refusal(response)
        return response

    def create_plan_lite(
        self,
        *,
        external_user_id: str,
        actions: list[dict[str, Any]],
        goal_id: str | None = None,
        template_version: int | None = None,
    ) -> PlanLite:
        """``POST /internal/me/plan-lite/`` — составить план; 201 → документ.

        ``goal_id`` необязателен: без него каталог строит план от активной
        цели вызывающего (нет активной → :class:`PlanLiteGoalNotFoundError`).
        ``template_version`` (DRF-2123) — версия шаблона, по которому человек
        подтвердил предложение; каталог пишет провенанс
        ``source=template:<goal_key>:v<N>``. Без него ключа в теле нет.

        Raises :class:`PlanLiteDisabledError`, :class:`PlanLiteAlreadyActiveError`,
        :class:`PlanLiteGoalNotFoundError`, :class:`WellnessContextClientError` (400).
        """
        # goal_id и template_version необязательны: отсутствующий ключ в тело
        # не кладётся, чтобы не слать null (PR-1b/2b, DRF-2123).
        body: dict[str, Any] = {"actions": actions}
        if goal_id is not None:
            body = {"goal_id": goal_id, **body}
        if template_version is not None:
            body["template_version"] = template_version
        response = self._plan_lite_request("POST", external_user_id=external_user_id, body=body)
        try:
            payload = response.json()
        except ValueError as exc:
            raise WellnessContextUnavailableError("malformed_json") from exc
        data = payload.get("data") if isinstance(payload, dict) else None
        plan = _plan_lite_from_wire(data)
        if plan is None:
            raise WellnessContextUnavailableError("plan_lite_malformed_body")
        return plan

    def get_plan_lite_proposal(self, *, external_user_id: str) -> PlanLiteProposal:
        """``GET /internal/me/plan-lite/proposal/`` — предложение из шаблона
        активной цели (DRF-2123); ничего не создаёт.

        Raises :class:`PlanLiteDisabledError` (404 ``PLAN_LITE_DISABLED``),
        :class:`PlanLiteGoalNotFoundError` (404 ``no_active_goal``),
        :class:`PlanLiteNoTemplateError` (404 ``no_template``),
        :class:`WellnessContextUnavailableError` (сеть / 5xx / битое тело).
        """
        response = self._plan_lite_request(
            "GET", external_user_id=external_user_id, path=_PLAN_LITE_PROPOSAL_PATH
        )
        try:
            payload = response.json()
        except ValueError as exc:
            raise WellnessContextUnavailableError("malformed_json") from exc
        data = payload.get("data") if isinstance(payload, dict) else None
        proposal = _proposal_from_wire(data)
        if proposal is None:
            raise WellnessContextUnavailableError("plan_lite_proposal_malformed_body")
        return proposal

    def close_plan_lite(self, *, external_user_id: str) -> bool:
        """``DELETE /internal/me/plan-lite/`` — закрыть активный план (append-only).

        Raises :class:`PlanLiteDisabledError`, :class:`PlanLiteGoalNotFoundError`
        (активного плана нет).
        """
        self._plan_lite_request("DELETE", external_user_id=external_user_id)
        return True

    def close(self) -> None:
        if self._http is not None:
            self._http.close()
            self._http = None

    def __enter__(self) -> "WellnessContextHttpClient":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


# ---------------------------------------------------------------------------
# Wire helpers
# ---------------------------------------------------------------------------


def _context_from_wire(payload: Any) -> WellnessContext:
    """Разобрать конверт ``{"data": ...}``; битая форма — пустой документ.

    Толерантность к форме намеренная: документ сегодня приходит в
    gated-виде, и его контракт дорежеутся на стороне Ayla. Отсутствующие
    поля читаются как «нет плана / нет outcomes», а не как ошибка.
    """
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return WellnessContext(has_plan=False)

    outcomes_raw = data.get("outcomes")
    outcomes: list[OutcomeState] = []
    if isinstance(outcomes_raw, list):
        for item in outcomes_raw:
            if not isinstance(item, dict):
                continue
            outcomes.append(
                OutcomeState(
                    target=_code(item.get("target")),
                    link_status=_code(item.get("link_status")),
                    horizon_status=_code(item.get("horizon_status")),
                    progress_state=_code(item.get("progress_state")),
                )
            )
    return WellnessContext(
        has_plan=isinstance(data.get("plan"), dict),
        outcomes=tuple(outcomes),
        gated=isinstance(data.get("gated"), dict),
        plan_lite=_plan_lite_from_wire(data.get("plan_lite")),
    )


def _plan_lite_from_wire(raw: Any) -> PlanLite | None:
    """``plan_lite`` документа → DTO фактов; всё, чего в DTO нет полей
    (проценты, тексты), умирает здесь. Битая форма — ``None``."""
    if not isinstance(raw, dict):
        return None
    actions: list[PlanLiteAction] = []
    for item in raw.get("actions") or []:
        if not isinstance(item, dict):
            continue
        raw_bucket = item.get("bucket")
        bucket: dict[str, Any] = raw_bucket if isinstance(raw_bucket, dict) else {}
        actions.append(
            PlanLiteAction(
                action_type=_code(item.get("action_type")),
                cadence=_code(item.get("cadence")),
                target_count=_count(item.get("target_count")),
                done_count=_count(item.get("done_count")),
                bucket_start=_code(bucket.get("start")),
                bucket_end=_code(bucket.get("end")),
            )
        )
    return PlanLite(
        plan_id=_code(raw.get("plan_id")),
        goal_key=_code(raw.get("goal_key")),
        actions=tuple(actions),
    )


def _proposal_from_wire(raw: Any) -> PlanLiteProposal | None:
    """``data`` предложения → DTO формы; всё, чего в DTO нет полей, умирает
    здесь. Битая форма (не объект / нет версии шаблона ≥ 1) — ``None``."""
    if not isinstance(raw, dict):
        return None
    version = raw.get("template_version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        return None
    actions: list[PlanLiteProposalAction] = []
    for item in raw.get("actions") or []:
        if not isinstance(item, dict):
            continue
        actions.append(
            PlanLiteProposalAction(
                action_type=_code(item.get("action_type")),
                cadence=_code(item.get("cadence")),
                target_count=_count(item.get("target_count")),
            )
        )
    return PlanLiteProposal(
        goal_key=_code(raw.get("goal_key")),
        why=_code(raw.get("why")),
        template_version=version,
        actions=tuple(actions),
    )


def _count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _plan_lite_refusal(response: httpx.Response) -> WellnessContextClientError:
    """4xx Plan Lite → исключение по коду каталога; тело в лог не идёт.

    404 различаются кодом и ``details.reason`` (DRF-2123): ``no_template`` —
    свой класс, прочие ``NOT_FOUND`` (в т.ч. ``no_active_goal``) — цель.
    """
    code, reason = _error_code_and_reason(response)
    if response.status_code == 404 and code == "PLAN_LITE_DISABLED":
        return PlanLiteDisabledError("plan_lite_disabled")
    if response.status_code == 409 and code == "PLAN_LITE_ALREADY_ACTIVE":
        return PlanLiteAlreadyActiveError("already_active")
    if response.status_code == 404 and reason == "no_template":
        return PlanLiteNoTemplateError("no_template")
    if response.status_code == 404:
        return PlanLiteGoalNotFoundError(reason or code or "not_found")
    return WellnessContextClientError(f"Ayla plan-lite 4xx: HTTP {response.status_code} {code}")


def _error_code_and_reason(response: httpx.Response) -> tuple[str, str]:
    """``error.code`` и ``error.details.reason`` конверта отказа; чего нет — ``""``."""
    try:
        payload = response.json()
    except ValueError:
        return "", ""
    error = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(error, dict):
        return "", ""
    details = error.get("details")
    reason = details.get("reason") if isinstance(details, dict) else None
    return _code(error.get("code")), _code(reason)


def _code(value: Any) -> str:
    """Код — это строка; всё остальное читается как пустой код."""
    return value if isinstance(value, str) else ""
