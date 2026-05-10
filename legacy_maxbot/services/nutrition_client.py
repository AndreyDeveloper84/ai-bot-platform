"""HTTP client for Ayla nutrition API (DRF-246).

Service-to-service calls from the MAX bot to Ayla. Authenticates via
`X-Service-Token` (shared secret) + `X-External-User-ID` (e.g. `bot:12345`).

Endpoints (all under `AYLA_BASE_URL`):
- POST /api/v1/nutrition/internal/scan/         — multipart photo
- POST /api/v1/nutrition/internal/food-log/     — DRF-247 (placeholder)
- GET  /api/v1/nutrition/internal/summary/      — DRF-247 (placeholder)
- GET  /api/v1/nutrition/internal/deficits/     — DRF-248 (placeholder)

Resilience:
- Per-call timeout (default 10s).
- Circuit breaker: 3 failures within 60s → 60s cool-down on subsequent calls.
- Caller-side retries are out of scope (MAX webhook fires once per photo).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from django.conf import settings


logger = logging.getLogger("maxbot.nutrition_client")

DEFAULT_TIMEOUT_S = 10.0
CIRCUIT_FAILURE_WINDOW_S = 60.0
CIRCUIT_FAILURE_THRESHOLD = 3
CIRCUIT_OPEN_DURATION_S = 60.0


@dataclass
class _Circuit:
    """Tiny in-process circuit breaker.

    Not thread-safe across worker processes, which is fine: each MAX bot
    worker tracks its own failures; if Ayla goes down, every worker will
    independently open its breaker within seconds. We don't need shared
    state to be useful.
    """

    failures: list[float] = field(default_factory=list)
    opened_at: float | None = None

    def is_open(self, *, now: float) -> bool:
        if self.opened_at is None:
            return False
        if now - self.opened_at >= CIRCUIT_OPEN_DURATION_S:
            self.opened_at = None
            self.failures = []
            return False
        return True

    def record_failure(self, *, now: float) -> None:
        cutoff = now - CIRCUIT_FAILURE_WINDOW_S
        self.failures = [t for t in self.failures if t >= cutoff]
        self.failures.append(now)
        if len(self.failures) >= CIRCUIT_FAILURE_THRESHOLD:
            self.opened_at = now
            logger.warning(
                "nutrition_client.circuit_opened failures=%d window_s=%.0f",
                len(self.failures), CIRCUIT_FAILURE_WINDOW_S,
            )

    def record_success(self) -> None:
        self.failures = []
        self.opened_at = None


class NutritionAPIError(Exception):
    """Base for client-visible failures."""


class NutritionUnavailableError(NutritionAPIError):
    """Ayla is down or the circuit is open — caller should show a fallback message."""


class FoodNotRecognizedError(NutritionAPIError):
    """Ayla returned 400 FOOD_NOT_RECOGNIZED — not food / unreadable photo."""


@dataclass(frozen=True)
class ScanResponse:
    """Subset of `FoodScanResponseSerializer` data we care about in the bot."""

    scan_id: str
    dish_name: str
    confidence: float
    portion_g: float | None
    nutrition: dict[str, Any] | None  # {calories, protein_g, fat_g, carbs_g, vitamins?}
    provider: str
    raw: dict[str, Any]


@dataclass(frozen=True)
class FoodLogResponse:
    """Subset of `FoodLogEntrySerializer` data we care about in the bot."""

    log_id: str
    dish_name: str
    meal_type: str
    calories: float
    raw: dict[str, Any]


@dataclass(frozen=True)
class SummaryResponse:
    """Subset of `NutritionSummaryResponseSerializer` data we care about."""

    date: str
    calories_total: float
    calories_goal: int
    protein_g: float
    fat_g: float
    carbs_g: float
    entries: list[dict[str, Any]]
    raw: dict[str, Any]
    ai_comment: str | None = None  # Part 2D.3: server-generated <=220 chars


@dataclass(frozen=True)
class ProfileResponse:
    """Phase 3 T03: nutrition profile + computed BMR/norms.

    Server-side calculations (bmr, daily_kcal, p/f/c, water_ml) считаются Ayla'ой —
    бот их только показывает. `goal_overridden_by` ∈ {None, "pregnancy", "breastfeeding",
    "eating_disorder", "bmi_floor"} — Ayla сообщает что цель переопределена при
    health_screening / BMI floor ladder.
    """

    gender: str  # "male" | "female" | ""
    age: int
    height_cm: int
    weight_kg: int
    goal: str  # "lose" | "maintain" | "gain" | "tone" | ""
    daily_kcal: int
    protein_g: int
    fat_g: int
    carbs_g: int
    water_ml: int
    bmr: int
    health_flags: dict[str, Any]
    disclaimer_acked: dict[str, Any] | None
    goal_pace: str = ""  # "slow" | "balanced" | ""
    activity: str = ""
    diet_preference: str = ""
    goal_overridden_by: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WaterEntryResponse:
    """Phase 3 T03: водомер — Ayla применяет water_coefficient на стороне сервера.

    `water_ml` ≤ `ml` для напитков с coef < 1 (кофе 0.95, чай 1.0, алкоголь 0.0).
    `milestone_text` — текст «Половина нормы — отлично!» / «Норма! 💧» / null.
    `alcohol_recovery_hint` — true для категорий wine/beer/spirits → бот добавляет
    UX-блок «Может выпьешь воды?».
    """

    entry_id: str
    ml: int
    water_ml: int
    kcal: int
    milestone_text: str | None
    today_total_ml: int
    today_norm_ml: int
    alcohol_recovery_hint: bool
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WaterTodayResponse:
    """Список water entries за день — для undo-UI и daily report.

    Field naming: DTO keeps `total_ml` / `norm_ml` for backward compat with
    callers (ai_ui.render_water_today, tasks.send_water_reminders). Parser
    reads Ayla's actual envelope keys `today_total_water_ml` /
    `today_norm_water_ml` (DRF-301 / B22 — sister bug to PR #133's nested
    norms fix). Optional fields (kcal/caffeine/coffee_cups/tea_cups) are
    extracted from `today_*` keys for daily report + caffeine warnings.
    """

    total_ml: int
    norm_ml: int
    entries: list[dict[str, Any]]
    kcal_from_beverages: float = 0.0
    caffeine_mg: float = 0.0
    coffee_cups: int = 0
    tea_cups: int = 0
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DeficitsResponse:
    """Cross-domain bridge signal from /internal/deficits/ (DRF-248)."""

    days_observed: int
    protein_avg_pct_goal: float | None
    protein_low_streak_days: int
    hint: str  # may be empty — caller checks before passing to prompt
    fired_keys: list[str]
    raw: dict[str, Any]


@dataclass(frozen=True)
class CrossDomainInsight:
    """DRF-274 (B-4) — cross-domain insight surfaced after a food log.

    Mapped from Ayla `GET /api/v1/nutrition/internal/insights/cross_domain/`
    nested envelope: `{"data": {"has_insight": true, "insight": {...}}}`.

    Fields are personalized — `insight_text` / `rationale_text` MUST NOT be
    logged at INFO level (PII-safe convention).
    """

    shown_id: str
    rule_slug: str
    insight_text: str
    rationale_text: str
    service_category_slug: str
    disclaimer_text: str


class NutritionClient:
    """Async client. One instance shared by handlers; circuit state is per-instance.

    Construct via `get_nutrition_client()` — module-level singleton.
    """

    def __init__(
        self,
        *,
        base_url: str,
        service_token: str,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        if not base_url:
            raise ValueError("AYLA_BASE_URL is empty — nutrition client cannot start")
        if not service_token:
            raise ValueError("NUTRITION_SERVICE_TOKEN is empty — nutrition client cannot start")
        self._base_url = base_url.rstrip("/")
        self._token = service_token
        self._timeout_s = timeout_s
        self._circuit = _Circuit()

    async def scan_photo(
        self,
        *,
        external_user_id: str,
        image_bytes: bytes,
        filename: str = "meal.jpg",
        portion_multiplier: float | None = None,
    ) -> ScanResponse:
        """POST /api/v1/nutrition/internal/scan/.

        Raises:
            NutritionUnavailableError: circuit open, network error, 5xx, timeout.
            FoodNotRecognizedError: 400 FOOD_NOT_RECOGNIZED.
            NutritionAPIError: other 4xx.
        """
        now = time.monotonic()
        if self._circuit.is_open(now=now):
            raise NutritionUnavailableError("circuit_open")

        url = f"{self._base_url}/api/v1/nutrition/internal/scan/"
        headers = {
            "X-Service-Token": self._token,
            "X-External-User-ID": external_user_id,
        }
        files = {"image": (filename, image_bytes, "image/jpeg")}
        data: dict[str, str] = {}
        if portion_multiplier is not None:
            data["portion_multiplier"] = str(portion_multiplier)

        try:
            async with httpx.AsyncClient(timeout=self._timeout_s) as http:
                resp = await http.post(url, headers=headers, files=files, data=data)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            self._circuit.record_failure(now=now)
            logger.warning("nutrition_client.scan.network ext=%s err=%s",
                           external_user_id, type(exc).__name__)
            raise NutritionUnavailableError(f"network: {type(exc).__name__}") from exc

        return self._parse_scan_response(resp, external_user_id=external_user_id)

    def _parse_scan_response(
        self, resp: httpx.Response, *, external_user_id: str,
    ) -> ScanResponse:
        now = time.monotonic()
        if resp.status_code == 200:
            self._circuit.record_success()
            body = resp.json().get("data", {})
            return ScanResponse(
                scan_id=str(body.get("id") or body.get("scan_id") or ""),
                dish_name=body.get("dish_name") or "",
                confidence=float(body.get("confidence") or 0.0),
                portion_g=body.get("portion_g"),
                nutrition=body.get("nutrition"),
                provider=body.get("provider") or "",
                raw=body,
            )

        if resp.status_code >= 500:
            self._circuit.record_failure(now=now)
            logger.warning("nutrition_client.scan.5xx status=%d ext=%s",
                           resp.status_code, external_user_id)
            raise NutritionUnavailableError(f"http_{resp.status_code}")

        # 4xx: classify by error code in body envelope.
        try:
            err_code = (resp.json().get("error") or {}).get("code", "")
        except ValueError:
            err_code = ""

        if err_code == "FOOD_NOT_RECOGNIZED":
            raise FoodNotRecognizedError("low_confidence")
        if err_code == "FOOD_API_UNAVAILABLE":
            self._circuit.record_failure(now=now)
            raise NutritionUnavailableError(err_code)

        logger.info("nutrition_client.scan.4xx status=%d ext=%s code=%s",
                    resp.status_code, external_user_id, err_code)
        raise NutritionAPIError(f"http_{resp.status_code}_{err_code or 'unknown'}")


    async def log_meal(
        self,
        *,
        external_user_id: str,
        scan_id: str | None = None,
        dish_name: str | None = None,
        meal_type: str,
        portion_multiplier: float = 1.0,
        idempotency_key: str | None = None,
    ) -> FoodLogResponse:
        """POST /api/v1/nutrition/internal/food-log/.

        At least one of scan_id / dish_name must be provided.
        """
        now = time.monotonic()
        if self._circuit.is_open(now=now):
            raise NutritionUnavailableError("circuit_open")

        url = f"{self._base_url}/api/v1/nutrition/internal/food-log/"
        headers: dict[str, str] = {
            "X-Service-Token": self._token,
            "X-External-User-ID": external_user_id,
        }
        if idempotency_key:
            headers["X-Idempotency-Key"] = idempotency_key
        body: dict[str, Any] = {
            "meal_type": meal_type,
            "portion_multiplier": portion_multiplier,
        }
        if scan_id:
            body["scan_id"] = scan_id
        if dish_name:
            body["dish_name"] = dish_name

        try:
            async with httpx.AsyncClient(timeout=self._timeout_s) as http:
                resp = await http.post(url, headers=headers, json=body)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            self._circuit.record_failure(now=now)
            logger.warning("nutrition_client.log.network ext=%s err=%s",
                           external_user_id, type(exc).__name__)
            raise NutritionUnavailableError(f"network: {type(exc).__name__}") from exc

        return self._parse_log_response(resp, external_user_id=external_user_id)

    async def daily_summary(
        self,
        *,
        external_user_id: str,
        date: str | None = None,
        with_comment: bool = False,
    ) -> SummaryResponse:
        """GET /api/v1/nutrition/internal/summary/?date=YYYY-MM-DD&with_comment=true.

        Args:
            with_comment: Phase 2D.3 — request server-generated tip ≤220 chars.
                Server may ignore (older Ayla deploy) → ai_comment will be None.
        """
        now = time.monotonic()
        if self._circuit.is_open(now=now):
            raise NutritionUnavailableError("circuit_open")

        url = f"{self._base_url}/api/v1/nutrition/internal/summary/"
        headers = {
            "X-Service-Token": self._token,
            "X-External-User-ID": external_user_id,
        }
        params: dict[str, str] = {}
        if date:
            params["date"] = date
        if with_comment:
            params["with_comment"] = "true"

        try:
            async with httpx.AsyncClient(timeout=self._timeout_s) as http:
                resp = await http.get(url, headers=headers, params=params)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            self._circuit.record_failure(now=now)
            logger.warning("nutrition_client.summary.network ext=%s err=%s",
                           external_user_id, type(exc).__name__)
            raise NutritionUnavailableError(f"network: {type(exc).__name__}") from exc

        return self._parse_summary_response(resp, external_user_id=external_user_id)

    def _parse_log_response(
        self, resp: httpx.Response, *, external_user_id: str,
    ) -> FoodLogResponse:
        now = time.monotonic()
        if resp.status_code in (200, 201):
            self._circuit.record_success()
            body = resp.json().get("data", {})
            return FoodLogResponse(
                log_id=str(body.get("id") or ""),
                dish_name=body.get("dish_name") or "",
                meal_type=body.get("meal_type") or "",
                calories=float(body.get("calories") or 0.0),
                raw=body,
            )
        if resp.status_code >= 500:
            self._circuit.record_failure(now=now)
            raise NutritionUnavailableError(f"http_{resp.status_code}")
        try:
            err_code = (resp.json().get("error") or {}).get("code", "")
        except ValueError:
            err_code = ""
        if err_code == "FOOD_NOT_RECOGNIZED":
            raise FoodNotRecognizedError("nutrition_missing")
        raise NutritionAPIError(f"http_{resp.status_code}_{err_code or 'unknown'}")

    def _parse_summary_response(
        self, resp: httpx.Response, *, external_user_id: str,
    ) -> SummaryResponse:
        now = time.monotonic()
        if resp.status_code == 200:
            self._circuit.record_success()
            body = resp.json().get("data", {})
            return SummaryResponse(
                date=str(body.get("date") or ""),
                calories_total=float(body.get("calories_total") or 0.0),
                calories_goal=int(body.get("calories_goal") or 0),
                protein_g=float(body.get("protein_g") or 0.0),
                fat_g=float(body.get("fat_g") or 0.0),
                carbs_g=float(body.get("carbs_g") or 0.0),
                entries=list(body.get("entries") or []),
                raw=body,
                ai_comment=body.get("ai_comment") or None,
            )
        if resp.status_code >= 500:
            self._circuit.record_failure(now=now)
            raise NutritionUnavailableError(f"http_{resp.status_code}")
        raise NutritionAPIError(f"http_{resp.status_code}")

    async def weekly_deficits(
        self,
        *,
        external_user_id: str,
        days: int = 7,
    ) -> DeficitsResponse:
        """GET /api/v1/nutrition/internal/deficits/?days=N — DRF-248 bridge signal."""
        now = time.monotonic()
        if self._circuit.is_open(now=now):
            raise NutritionUnavailableError("circuit_open")

        url = f"{self._base_url}/api/v1/nutrition/internal/deficits/"
        headers = {
            "X-Service-Token": self._token,
            "X-External-User-ID": external_user_id,
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout_s) as http:
                resp = await http.get(url, headers=headers, params={"days": str(days)})
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            self._circuit.record_failure(now=now)
            raise NutritionUnavailableError(f"network: {type(exc).__name__}") from exc

        if resp.status_code == 200:
            self._circuit.record_success()
            body = resp.json().get("data", {})
            return DeficitsResponse(
                days_observed=int(body.get("days_observed") or 0),
                protein_avg_pct_goal=body.get("protein_avg_pct_goal"),
                protein_low_streak_days=int(body.get("protein_low_streak_days") or 0),
                hint=_sanitize_hint(body.get("hint")),
                fired_keys=list(body.get("fired_keys") or []),
                raw=body,
            )
        if resp.status_code >= 500:
            self._circuit.record_failure(now=now)
            raise NutritionUnavailableError(f"http_{resp.status_code}")
        raise NutritionAPIError(f"http_{resp.status_code}")


    # ─── Phase 3 T03: profile ──────────────────────────────────────────────

    async def get_profile(
        self,
        *,
        external_user_id: str,
    ) -> ProfileResponse | None:
        """GET /api/v1/nutrition/internal/profile/.

        Returns:
            ProfileResponse: профиль найден.
            None: 404 PROFILE_NOT_FOUND (анкета ещё не пройдена).

        Raises:
            NutritionUnavailableError: circuit open / 5xx / network error.
            NutritionAPIError: иные 4xx.
        """
        now = time.monotonic()
        if self._circuit.is_open(now=now):
            raise NutritionUnavailableError("circuit_open")

        url = f"{self._base_url}/api/v1/nutrition/internal/profile/"
        headers = {
            "X-Service-Token": self._token,
            "X-External-User-ID": external_user_id,
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout_s) as http:
                resp = await http.get(url, headers=headers)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            self._circuit.record_failure(now=now)
            raise NutritionUnavailableError(f"network: {type(exc).__name__}") from exc

        return self._parse_profile_response(resp)

    async def upsert_profile(
        self,
        *,
        external_user_id: str,
        data: dict[str, Any],
    ) -> ProfileResponse:
        """POST /api/v1/nutrition/internal/profile/.

        Принимает full или partial dict — Ayla на стороне сервера применяет ladder
        (pregnancy → maintain, BMI floor) и возвращает рассчитанные нормы +
        `goal_overridden_by` для UX-блока «Учла важное».
        """
        now = time.monotonic()
        if self._circuit.is_open(now=now):
            raise NutritionUnavailableError("circuit_open")

        url = f"{self._base_url}/api/v1/nutrition/internal/profile/"
        headers = {
            "X-Service-Token": self._token,
            "X-External-User-ID": external_user_id,
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout_s) as http:
                resp = await http.post(url, headers=headers, json=data)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            self._circuit.record_failure(now=now)
            raise NutritionUnavailableError(f"network: {type(exc).__name__}") from exc

        result = self._parse_profile_response(resp, allow_404=False)
        # _parse_profile_response с allow_404=False бросит ошибку до возврата None,
        # так что assert защита от type-checker'а — Pylance видит Optional.
        assert result is not None
        return result

    def _parse_profile_response(
        self, resp: httpx.Response, *, allow_404: bool = True,
    ) -> ProfileResponse | None:
        now = time.monotonic()
        if resp.status_code in (200, 201):
            self._circuit.record_success()
            # Ayla оборачивает ответ в {"data": ...} — это корректно.
            body = resp.json().get("data", {})
            # DRF-270 (B-1): GET /profile/ может вернуть 200 с `exists=false`
            # вместо 404 — обработаем как «нет профиля».
            if body.get("exists") is False:
                return None
            # Norms живут strictly под `data.norms.*` с `daily_` префиксом
            # per Ayla spec §1.1. DRF-270: убрали flat top-level fallback —
            # старая схема больше не поддерживается.
            norms = body.get("norms") or {}
            return ProfileResponse(
                gender=str(body.get("gender") or ""),
                age=int(body.get("age") or 0),
                height_cm=int(body.get("height_cm") or 0),
                weight_kg=int(body.get("weight_kg") or 0),
                goal=str(body.get("goal") or ""),
                # Ayla spec использует "pace"; "goal_pace" — fallback для обратной совместимости.
                goal_pace=str(body.get("pace") or body.get("goal_pace") or ""),
                # Ayla spec использует "activity_coefficient" (число), не "activity" (строка).
                activity=str(body.get("activity_coefficient") or body.get("activity") or ""),
                diet_preference=str(body.get("diet_preference") or ""),
                daily_kcal=int(norms.get("daily_kcal") or 0),
                protein_g=int(norms.get("daily_protein_g") or 0),
                fat_g=int(norms.get("daily_fat_g") or 0),
                carbs_g=int(norms.get("daily_carbs_g") or 0),
                water_ml=int(norms.get("daily_water_ml") or 0),
                bmr=int(norms.get("bmr") or 0),
                health_flags=dict(body.get("health_flags") or {}),
                disclaimer_acked=body.get("disclaimer_acked"),
                goal_overridden_by=body.get("goal_overridden_by"),
                raw=body,
            )

        if resp.status_code == 404 and allow_404:
            self._circuit.record_success()  # 404 = валидный «нет профиля» для GET
            return None

        if resp.status_code >= 500:
            self._circuit.record_failure(now=now)
            raise NutritionUnavailableError(f"http_{resp.status_code}")

        try:
            err_code = (resp.json().get("error") or {}).get("code", "")
        except ValueError:
            err_code = ""
        raise NutritionAPIError(f"http_{resp.status_code}_{err_code or 'unknown'}")


    # ─── Phase 3 T03: water ────────────────────────────────────────────────

    async def add_water(
        self,
        *,
        external_user_id: str,
        ml: int,
        beverage_slug: str | None = None,
        ts: str | None = None,
        idempotency_key: str | None = None,
    ) -> WaterEntryResponse:
        """POST /api/v1/nutrition/internal/water/.

        Body: `{ml, beverage_slug?, ts?}`. Ayla server-side применяет
        `water_coefficient` из Beverage-каталога и возвращает фактический `water_ml`.
        """
        now = time.monotonic()
        if self._circuit.is_open(now=now):
            raise NutritionUnavailableError("circuit_open")

        url = f"{self._base_url}/api/v1/nutrition/internal/water/"
        headers: dict[str, str] = {
            "X-Service-Token": self._token,
            "X-External-User-ID": external_user_id,
        }
        if idempotency_key:
            headers["X-Idempotency-Key"] = idempotency_key
        body: dict[str, Any] = {"ml": ml}
        if beverage_slug:
            body["beverage_slug"] = beverage_slug
        if ts:
            body["ts"] = ts

        try:
            async with httpx.AsyncClient(timeout=self._timeout_s) as http:
                resp = await http.post(url, headers=headers, json=body)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            self._circuit.record_failure(now=now)
            raise NutritionUnavailableError(f"network: {type(exc).__name__}") from exc

        return self._parse_water_entry_response(resp)

    def _parse_water_entry_response(self, resp: httpx.Response) -> WaterEntryResponse:
        now = time.monotonic()
        if resp.status_code in (200, 201):
            self._circuit.record_success()
            body = resp.json().get("data", {})
            return WaterEntryResponse(
                entry_id=str(body.get("entry_id") or ""),
                ml=int(body.get("ml") or 0),
                water_ml=int(body.get("water_ml") or 0),
                kcal=int(body.get("kcal") or 0),
                milestone_text=body.get("milestone_text"),
                # Ayla actual envelope keys (DRF-301 / B22) — see WaterTodayResponse
                # docstring for naming rationale.
                today_total_ml=int(body.get("today_total_water_ml") or 0),
                today_norm_ml=int(body.get("today_norm_water_ml") or 0),
                alcohol_recovery_hint=bool(body.get("alcohol_recovery_hint") or False),
                raw=body,
            )
        if resp.status_code >= 500:
            self._circuit.record_failure(now=now)
            raise NutritionUnavailableError(f"http_{resp.status_code}")
        try:
            err_code = (resp.json().get("error") or {}).get("code", "")
        except ValueError:
            err_code = ""
        raise NutritionAPIError(f"http_{resp.status_code}_{err_code or 'unknown'}")


    async def undo_water(
        self,
        *,
        external_user_id: str,
        entry_id: str,
    ) -> bool:
        """DELETE /api/v1/nutrition/internal/water/{entry_id}/.

        Returns:
            True: 200/204 — soft-delete успешно (запись восстановима
                в restore window). Live Ayla `InternalWaterDeleteView`
                отдаёт 200 с aggregate body; spec допускал 204. Принимаем
                оба варианта (B29 / DRF-357).
            False: 404 — restore window истёк или entry уже не существует.

        Raises:
            NutritionUnavailableError: circuit / 5xx / network.
        """
        now = time.monotonic()
        if self._circuit.is_open(now=now):
            raise NutritionUnavailableError("circuit_open")

        url = f"{self._base_url}/api/v1/nutrition/internal/water/{entry_id}/"
        headers = {
            "X-Service-Token": self._token,
            "X-External-User-ID": external_user_id,
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout_s) as http:
                resp = await http.delete(url, headers=headers)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            self._circuit.record_failure(now=now)
            raise NutritionUnavailableError(f"network: {type(exc).__name__}") from exc

        if resp.status_code in (200, 204):
            self._circuit.record_success()
            return True
        if resp.status_code == 404:
            self._circuit.record_success()
            return False
        if resp.status_code >= 500:
            self._circuit.record_failure(now=now)
            raise NutritionUnavailableError(f"http_{resp.status_code}")
        raise NutritionAPIError(f"http_{resp.status_code}")

    async def get_water_today(
        self,
        *,
        external_user_id: str,
    ) -> WaterTodayResponse:
        """GET /api/v1/nutrition/internal/water/today/."""
        now = time.monotonic()
        if self._circuit.is_open(now=now):
            raise NutritionUnavailableError("circuit_open")

        url = f"{self._base_url}/api/v1/nutrition/internal/water/today/"
        headers = {
            "X-Service-Token": self._token,
            "X-External-User-ID": external_user_id,
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout_s) as http:
                resp = await http.get(url, headers=headers)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            self._circuit.record_failure(now=now)
            raise NutritionUnavailableError(f"network: {type(exc).__name__}") from exc

        if resp.status_code == 200:
            self._circuit.record_success()
            body = resp.json().get("data", {})
            return WaterTodayResponse(
                # Ayla actual envelope keys (DRF-301 / B22) — see DTO docstring.
                total_ml=int(body.get("today_total_water_ml") or 0),
                norm_ml=int(body.get("today_norm_water_ml") or 0),
                entries=list(body.get("entries") or []),
                kcal_from_beverages=float(body.get("today_kcal_from_beverages") or 0.0),
                caffeine_mg=float(body.get("today_caffeine_mg") or 0.0),
                coffee_cups=int(body.get("today_total_coffee_cups") or 0),
                tea_cups=int(body.get("today_total_tea_cups") or 0),
                raw=body,
            )
        if resp.status_code >= 500:
            self._circuit.record_failure(now=now)
            raise NutritionUnavailableError(f"http_{resp.status_code}")
        raise NutritionAPIError(f"http_{resp.status_code}")

    # ── DRF-274 (B-4): cross-domain insights ───────────────────────────────

    async def get_cross_domain_insights(
        self, *, external_user_id: str,
    ) -> CrossDomainInsight | None:
        """GET /api/v1/nutrition/internal/insights/cross_domain/.

        Returns:
            CrossDomainInsight: when Ayla returns `has_insight=True`.
            None: when Ayla returns `has_insight=False` OR 404.

        Raises:
            NutritionUnavailableError: circuit open, network error, 5xx, timeout.
        """
        now = time.monotonic()
        if self._circuit.is_open(now=now):
            raise NutritionUnavailableError("circuit_open")

        url = (
            f"{self._base_url}/api/v1/nutrition/internal/insights/cross_domain/"
        )
        headers = {
            "X-Service-Token": self._token,
            "X-External-User-ID": external_user_id,
        }

        try:
            async with httpx.AsyncClient(timeout=self._timeout_s) as http:
                resp = await http.get(url, headers=headers)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            self._circuit.record_failure(now=now)
            logger.warning(
                "nutrition_client.cross_domain.network ext=%s err=%s",
                external_user_id, type(exc).__name__,
            )
            raise NutritionUnavailableError(
                f"network: {type(exc).__name__}",
            ) from exc

        if resp.status_code == 404:
            return None
        if resp.status_code == 200:
            self._circuit.record_success()
            body = resp.json().get("data", {})
            if not body.get("has_insight"):
                return None
            insight = body.get("insight") or {}
            return CrossDomainInsight(
                shown_id=str(insight.get("shown_id") or ""),
                rule_slug=str(insight.get("rule_slug") or ""),
                insight_text=str(insight.get("insight_text") or ""),
                rationale_text=str(insight.get("rationale_text") or ""),
                service_category_slug=str(
                    insight.get("service_category_slug") or "",
                ),
                disclaimer_text=str(insight.get("disclaimer_text") or ""),
            )
        if resp.status_code >= 500:
            self._circuit.record_failure(now=now)
            logger.warning(
                "nutrition_client.cross_domain.5xx status=%d ext=%s",
                resp.status_code, external_user_id,
            )
            raise NutritionUnavailableError(f"http_{resp.status_code}")
        raise NutritionAPIError(f"http_{resp.status_code}")

    async def post_cross_domain_seen(
        self, *, external_user_id: str, shown_id: str,
    ) -> bool:
        """POST /insights/cross_domain/seen/{shown_id}/. Idempotent telemetry."""
        return await self._post_cross_domain_action(
            external_user_id=external_user_id,
            shown_id=shown_id,
            action="seen",
        )

    async def post_cross_domain_dismiss(
        self, *, external_user_id: str, shown_id: str,
    ) -> bool:
        """POST /insights/cross_domain/dismiss/{shown_id}/."""
        return await self._post_cross_domain_action(
            external_user_id=external_user_id,
            shown_id=shown_id,
            action="dismiss",
        )

    async def post_cross_domain_convert(
        self,
        *,
        external_user_id: str,
        shown_id: str,
        appointment_id: str,
    ) -> bool:
        """POST /insights/cross_domain/convert/{shown_id}/ with appointment_id."""
        return await self._post_cross_domain_action(
            external_user_id=external_user_id,
            shown_id=shown_id,
            action="convert",
            json_body={"appointment_id": appointment_id},
        )

    async def _post_cross_domain_action(
        self,
        *,
        external_user_id: str,
        shown_id: str,
        action: str,
        json_body: dict[str, Any] | None = None,
    ) -> bool:
        now = time.monotonic()
        if self._circuit.is_open(now=now):
            raise NutritionUnavailableError("circuit_open")

        url = (
            f"{self._base_url}/api/v1/nutrition/internal/insights/"
            f"cross_domain/{action}/{shown_id}/"
        )
        headers = {
            "X-Service-Token": self._token,
            "X-External-User-ID": external_user_id,
        }

        try:
            async with httpx.AsyncClient(timeout=self._timeout_s) as http:
                resp = await http.post(url, headers=headers, json=json_body or {})
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            self._circuit.record_failure(now=now)
            logger.warning(
                "nutrition_client.cross_domain.%s.network ext=%s err=%s",
                action, external_user_id, type(exc).__name__,
            )
            raise NutritionUnavailableError(
                f"network: {type(exc).__name__}",
            ) from exc

        if 200 <= resp.status_code < 300:
            self._circuit.record_success()
            return True
        if resp.status_code >= 500:
            self._circuit.record_failure(now=now)
            raise NutritionUnavailableError(f"http_{resp.status_code}")
        raise NutritionAPIError(f"http_{resp.status_code}")


# ─── DRF-248 P1 hint sanitization ──────────────────────────────────────────


_HINT_MAX_LEN = 300
_HINT_INJECTION_MARKERS = (
    "ignore previous", "ignore above", "disregard previous",
    "забудь правила", "забудь инструкции", "забудь предыдущие",
    "system:", "###system", "###user", "###assistant",
    "</system>", "</user>", "</assistant>",
)


def _sanitize_hint(raw: object) -> str:
    """P1: блокирует prompt-injection через cross-domain hint.

    Hint попадает прямо в SYSTEM_PROMPT_TEMPLATE → если на стороне Ayla
    данные подменены (compromised DB / bad ingest), злоумышленник может
    инъектить инструкции для LLM (jailbreak / disable rules).

    Защита (defence in depth, не серебряная пуля):
    1. Cap длины — длинная инъекция не пройдёт.
    2. Блок-лист маркеров «забудь правила» / «system:» / etc — на наличие
       drop'аем hint целиком (приёма рисковых hint'ов нет, лучше пустой).
    3. Только str — typed-cast гарантия.
    """
    if not raw:
        return ""
    text = str(raw).strip()
    if not text:
        return ""
    text = text[:_HINT_MAX_LEN]
    lowered = text.lower()
    for marker in _HINT_INJECTION_MARKERS:
        if marker in lowered:
            logger.warning(
                "nutrition_client: discarding suspicious hint (marker=%r)",
                marker,
            )
            return ""
    return text


_SINGLETON: NutritionClient | None = None


def get_nutrition_client() -> NutritionClient:
    """Module-level singleton. Lazy — fail loudly if env not set on first use."""
    global _SINGLETON
    if _SINGLETON is None:
        _SINGLETON = NutritionClient(
            base_url=getattr(settings, "AYLA_BASE_URL", ""),
            service_token=getattr(settings, "NUTRITION_SERVICE_TOKEN", ""),
        )
    return _SINGLETON


def reset_nutrition_client() -> None:
    """Drop singleton — used by tests to reset state between cases."""
    global _SINGLETON
    _SINGLETON = None
