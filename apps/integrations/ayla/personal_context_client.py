"""HTTP client for the Ayla **personal-context** internal API (M-B1).

Typed wrapper around ``httpx.Client`` for Ayla's declared-preferences
surface, per the FROZEN contract v1.0 (2026-07-09) —
``beautygo_backend/docs/PERSONAL_CONTEXT_INTERNAL_API_CONTRACT.md``:

* ``GET    /api/v1/internal/users/{ayla_user_id}/personal-context/``
* ``PATCH  /api/v1/internal/users/{ayla_user_id}/personal-context/``
* ``GET    /api/v1/internal/users/{ayla_user_id}/personal-context/ask-eligibility/``
* ``POST   /api/v1/internal/users/{ayla_user_id}/personal-context/mark-asked/``
* ``POST   /api/v1/internal/users/{ayla_user_id}/personal-context/skip/``

### Consent gate

The ``memory_green`` consent gate is enforced **on the bot, BEFORE any
call** (contract §Аутентификация; MEMORY_CONSENT_SPEC). This module is a
pure transport — gating lives in the caller/service layer
(:func:`apps.identity.services.personal_context.can_read_declared_prefs`
and friends). Ayla trusts the internal Bearer.

### Forward compatibility (contract «Договор совместимости»)

Unknown keys inside ``context`` are preserved, never rejected — Ayla may
add declared fields additively. The client never asserts on the field
catalogue.

### Idempotency → retry policy (contract §Идемпотентность)

* ``GET`` — read-only + lazy-create: retried on 5xx/network.
* ``PATCH`` — last-write-wins per field, safe to retry: retried.
* ``mark-asked`` / ``skip`` — **NOT idempotent** (counter increments /
  cooldown stamps): **single attempt, never retried**. A network failure
  raises :class:`PersonalContextTransportError` and the caller decides —
  a blind retry would double-stamp the cooldown / skip counter.

### Errors

* :class:`PersonalContextAuthError` — 401/403 (token mismatch).
* :class:`PersonalContextNotFoundError` — 404 ``USER_NOT_FOUND``.
* :class:`PersonalContextClientError` — other 4xx (``VALIDATION_ERROR``,
  ``TOO_MANY_FIELDS``) — caller/code bug, raised immediately.
* :class:`PersonalContextTransportError` — 5xx after retries (where
  allowed) or network failure.
* :class:`PersonalContextConfigError` — missing token / invalid base URL;
  a :class:`PersonalContextTransportError` subclass, never retried.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import Any

import httpx
from django.conf import settings

from apps.integrations.ayla.url_builder import AylaUrlBuilder, AylaUrlError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DTOs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DeclaredContext:
    """Full declared-prefs catalogue for one user (contract §1/§2).

    ``context`` keeps the raw field mapping verbatim — unknown keys ride
    along untouched (forward-compat). ``filled_fields``/``updated_at``
    mirror ``meta`` when present.
    """

    ayla_user_id: str
    context: dict[str, Any]
    filled_fields: int | None = None
    updated_at: str | None = None
    raw: dict[str, Any] = dc_field(default_factory=dict)


@dataclass(frozen=True)
class AskEligibility:
    """Result of ``GET ask-eligibility`` (contract §3).

    ``should_ask=True`` → ``field`` is the one candidate to ask about,
    with ``prompt_hint`` as Ayla's suggested wording. ``should_ask=False``
    → ``blocked_by`` carries the engine reason (``first_interaction``,
    ``cooldown``, ``skipped_twice``, ``no_candidate``, …).
    """

    should_ask: bool
    field: str | None = None
    prompt_hint: str | None = None
    blocked_by: str | None = None
    explain: str | None = None
    raw: dict[str, Any] = dc_field(default_factory=dict)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class PersonalContextError(Exception):
    """Base — anything personal-context-side that's not the happy path."""


class PersonalContextAuthError(PersonalContextError):
    """401 / 403 from Ayla. Bearer token mismatch or missing."""


class PersonalContextNotFoundError(PersonalContextError):
    """404 ``USER_NOT_FOUND`` — unknown ayla_user_id."""


class PersonalContextClientError(PersonalContextError):
    """Other 4xx (``VALIDATION_ERROR`` / ``TOO_MANY_FIELDS``). Bug on either side."""


class PersonalContextTransportError(PersonalContextError):
    """5xx after retries / network failure."""


class PersonalContextConfigError(PersonalContextTransportError):
    """Config gap (missing token, invalid base URL) — never retried."""


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


_BACKOFF_SECONDS = (0.5, 1.0, 2.0)

# Contract §2: updates is a non-empty list, at most 10 entries.
MAX_PATCH_UPDATES = 10


class PersonalContextHttpClient:
    """Fetch/patch Ayla declared prefs with Bearer auth + retry policy.

    Construction params are settings overrides for tests; prod code calls
    ``PersonalContextHttpClient()`` and reads ``AYLA_BASE_URL`` /
    ``AYLA_INTERNAL_API_TOKEN`` from Django settings. An injected
    ``http_client`` (e.g. ``httpx.MockTransport``) fakes the wire in tests.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        token: str | None = None,
        timeout: int | None = None,
        retries: int | None = None,
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
            else getattr(settings, "PERSONAL_CONTEXT_HTTP_TIMEOUT", 30)
        )
        self._retries = (
            retries
            if retries is not None
            else getattr(settings, "PERSONAL_CONTEXT_HTTP_RETRIES", 3)
        )
        self._http: httpx.Client | None = http_client

    # ------------------------------------------------------------------
    # Public API (contract §1–§5)
    # ------------------------------------------------------------------

    def get_context(self, *, ayla_user_id: str) -> DeclaredContext:
        """``GET /personal-context/`` — full declared catalogue (lazy-create)."""
        payload = self._send_with_retry("GET", f"internal/users/{ayla_user_id}/personal-context/")
        return _declared_from_wire(payload, ayla_user_id=ayla_user_id)

    def patch_context(
        self,
        *,
        ayla_user_id: str,
        updates: list[dict[str, Any]],
    ) -> DeclaredContext:
        """``PATCH /personal-context/`` — batch LWW update (idempotent).

        Local pre-validation mirrors the contract (non-empty, ≤ 10) so a
        caller bug fails before the wire, not after.
        """
        if not updates:
            raise PersonalContextClientError("updates must be a non-empty list")
        if len(updates) > MAX_PATCH_UPDATES:
            raise PersonalContextClientError(
                f"updates limited to {MAX_PATCH_UPDATES} entries per call "
                f"(contract TOO_MANY_FIELDS), got {len(updates)}"
            )
        payload = self._send_with_retry(
            "PATCH",
            f"internal/users/{ayla_user_id}/personal-context/",
            json_body={"updates": updates},
        )
        return _declared_from_wire(payload, ayla_user_id=ayla_user_id)

    def get_ask_eligibility(self, *, ayla_user_id: str) -> AskEligibility:
        """``GET ask-eligibility/`` — the ONE field Ayla allows asking now."""
        payload = self._send_with_retry(
            "GET", f"internal/users/{ayla_user_id}/personal-context/ask-eligibility/"
        )
        data = _unwrap_data(payload)
        return AskEligibility(
            should_ask=bool(data.get("should_ask", False)),
            field=data.get("field"),
            prompt_hint=data.get("prompt_hint"),
            blocked_by=data.get("blocked_by"),
            explain=data.get("explain"),
            raw=data if isinstance(data, dict) else {},
        )

    def mark_asked(self, *, ayla_user_id: str, field: str) -> None:
        """``POST mark-asked/`` — stamp the 24h cooldown. NOT retried."""
        self._send_single_attempt(
            "POST",
            f"internal/users/{ayla_user_id}/personal-context/mark-asked/",
            json_body={"field": field},
        )

    def skip(self, *, ayla_user_id: str, field: str) -> int:
        """``POST skip/`` — increment the skip counter. NOT retried.

        Returns the server's ``skip_count`` (0 when the body omits it).
        """
        payload = self._send_single_attempt(
            "POST",
            f"internal/users/{ayla_user_id}/personal-context/skip/",
            json_body={"field": field},
        )
        data = _unwrap_data(payload)
        try:
            return int(data.get("skip_count", 0))
        except (TypeError, ValueError):
            return 0

    # ------------------------------------------------------------------
    # Plumbing
    # ------------------------------------------------------------------

    def _send_with_retry(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Idempotent verbs (GET/PATCH): up to ``retries`` attempts.

        Config gaps (:class:`PersonalContextConfigError`) are raised
        immediately — retrying a missing token / bad base URL is wasted.
        """
        last_exc: Exception | None = None
        for attempt in range(self._retries):
            try:
                return self._send(method, path, json_body=json_body)
            except PersonalContextConfigError:
                raise
            except PersonalContextTransportError as exc:
                last_exc = exc
                if attempt == self._retries - 1:
                    break
                pause = _BACKOFF_SECONDS[min(attempt, len(_BACKOFF_SECONDS) - 1)]
                logger.warning(
                    "personal_context.http.retry attempt=%s pause=%s path=%s",
                    attempt + 1,
                    pause,
                    path,
                )
                time.sleep(pause)
        raise PersonalContextTransportError(
            f"Ayla personal-context: exhausted {self._retries} retries on {path}"
        ) from last_exc

    def _send_single_attempt(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Non-idempotent verbs (mark-asked/skip): exactly one attempt."""
        return self._send(method, path, json_body=json_body)

    def _send(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            url = AylaUrlBuilder(self._base_url).build(path)
        except AylaUrlError as exc:
            raise PersonalContextConfigError(f"invalid AYLA_BASE_URL: {exc}") from exc
        if not self._token:
            raise PersonalContextConfigError("AYLA_INTERNAL_API_TOKEN not configured")

        try:
            response = self._client().request(
                method,
                url,
                json=json_body,
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            raise PersonalContextTransportError(
                f"network: {exc.__class__.__name__} on {path}"
            ) from exc

        if response.status_code in (401, 403):
            raise PersonalContextAuthError(
                f"Ayla personal-context auth failed: HTTP {response.status_code}"
            )
        if response.status_code == 404:
            raise PersonalContextNotFoundError(f"USER_NOT_FOUND: {path} (HTTP 404)")
        if 400 <= response.status_code < 500:
            raise PersonalContextClientError(
                f"Ayla personal-context 4xx: HTTP {response.status_code} "
                f"code={_err_code(response)!r} body={response.text[:200]!r}"
            )
        if response.status_code >= 500:
            raise PersonalContextTransportError(f"http_{response.status_code} on {path}")
        try:
            return response.json()
        except ValueError as exc:
            raise PersonalContextTransportError(
                f"non-JSON {response.status_code} on {path}"
            ) from exc

    def _client(self) -> httpx.Client:
        if self._http is None:
            self._http = httpx.Client(timeout=self._timeout)
        return self._http

    def close(self) -> None:
        if self._http is not None:
            self._http.close()
            self._http = None

    def __enter__(self) -> "PersonalContextHttpClient":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


# ---------------------------------------------------------------------------
# Wire helpers
# ---------------------------------------------------------------------------


def _unwrap_data(payload: Any) -> dict[str, Any]:
    """Unwrap the ``{"data": ...}`` envelope; tolerate a bare mapping."""
    if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
        return payload["data"]
    return payload if isinstance(payload, dict) else {}


def _declared_from_wire(payload: Any, *, ayla_user_id: str) -> DeclaredContext:
    data = _unwrap_data(payload)
    meta_raw = data.get("meta")
    meta: dict[str, Any] = dict(meta_raw) if isinstance(meta_raw, dict) else {}
    context = data.get("context")
    return DeclaredContext(
        ayla_user_id=str(data.get("ayla_user_id") or ayla_user_id),
        context=dict(context) if isinstance(context, dict) else {},
        filled_fields=meta.get("filled_fields"),
        updated_at=meta.get("updated_at"),
        raw=data,
    )


def _err_code(resp: httpx.Response) -> str:
    """Pull ``error.code`` from an error body, best-effort."""
    try:
        return (resp.json().get("error") or {}).get("code", "") or "unknown"
    except (ValueError, AttributeError):
        return "unknown"
