"""TraceRecorder — pipeline-step capture (DRF-498 / Sprint 5 / A2).

Per PHASE0_DESIGN §7.1: every ``pipeline.turn()`` invocation creates
``trace_id = uuid7()`` and calls :meth:`TraceRecorder.capture` with
the structured step snapshots. The recorder:

  1. Decides sampling (per-env rate from settings)
  2. Reads the tenant from ContextVar
  3. Runs the steps through the regex redactor (B-track)
  4. Persists a :class:`ReplayTrace` row with `expires_at` stamped
     as `now + REPLAY_RETENTION_DAYS`
  5. Emits the canonical ``replay_captured`` event (B6 vocab)

### Telemetry contract: never raises

Replay capture is observability infrastructure. A misbehaving recorder
must not break the request path. The capture method swallows
exceptions and logs them — Sprint 6 pipeline will call us from the
hot path and we have a duty of care.

### Sampling per env

`settings.REPLAY_SAMPLE_RATE_PROD/STAGING/TEST` — recorder picks the
right one based on `settings.DEBUG` + presence of `PYTEST_CURRENT_TEST`
env var. Tests opt into recording by setting
`settings.REPLAY_SAMPLE_RATE_TEST = 1.0` explicitly.
"""

from __future__ import annotations

import logging
import os
import random
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


class TraceRecorder:
    """Captures pipeline step snapshots as :class:`ReplayTrace` rows.

    Stateless — one shared instance per worker is fine. Build with a
    custom redactor in tests, default reads from settings allowlist.
    """

    def __init__(self, redactor: Any | None = None) -> None:
        """Initialize with optional redactor override.

        Args:
          redactor: instance of :class:`apps.replay.redactor.Redactor`
            or compatible. None → late-build at first capture using
            `settings.REPLAY_REDACTION_ALLOWLIST`.
        """

        self._redactor = redactor

    # --- Public API -------------------------------------------------------

    def capture(self, trace_id: str, pipeline_steps: list[Any]) -> Any | None:
        """Persist one trace iff sampling + tenant context permit.

        Args:
          trace_id: uuid7 stringified by the caller.
          pipeline_steps: list of dict step snapshots.

        Returns:
          The persisted ``ReplayTrace`` instance, or None if sampled-
          out / no tenant / DB error.
        """

        try:
            return self._capture_inner(trace_id, pipeline_steps)
        except Exception:  # noqa: BLE001 — telemetry never breaks request
            logger.exception("replay.capture_failed trace_id=%s", trace_id)
            return None

    # --- Internals --------------------------------------------------------

    def _capture_inner(self, trace_id: str, pipeline_steps: list[Any]) -> Any | None:
        # Late imports — avoids pulling Django models at module-load
        # before AppConfig.ready completes.
        from apps.events.services import emit
        from apps.events.vocabulary import REPLAY_CAPTURED
        from apps.replay.models import ReplayTrace
        from apps.replay.redactor import REDACTION_METHOD, Redactor
        from apps.tenancy.context import current_tenant

        # Sampling decision first — cheapest gate.
        if not self._should_sample():
            return None

        tenant = current_tenant()
        if tenant is None:
            logger.info("replay.capture_skipped reason=no_tenant trace_id=%s", trace_id)
            return None

        redactor = self._redactor or Redactor()
        redacted_steps = redactor.redact_steps(pipeline_steps)

        expires_at = timezone.now() + timedelta(days=getattr(settings, "REPLAY_RETENTION_DAYS", 30))
        trace = ReplayTrace.all_tenants.create(
            tenant=tenant,
            trace_id=trace_id,
            pipeline_steps=redacted_steps,
            redacted=True,
            redaction_method=REDACTION_METHOD,
            expires_at=expires_at,
        )

        emit(
            REPLAY_CAPTURED,
            properties={
                "trace_id": trace_id,
                "redaction_method": REDACTION_METHOD,
                "steps_count": len(redacted_steps),
            },
        )
        logger.info(
            "replay.captured trace_id=%s tenant=%s steps=%d",
            trace_id,
            tenant.id,
            len(redacted_steps),
        )
        return trace

    def _should_sample(self) -> bool:
        """Pick the per-env rate + roll the die.

        Defaults:
        - prod / staging: 1.0 (Phase 0 100%)
        - test: 0.0 (opt-in via settings override)
        """

        rate = self._effective_rate()
        if rate <= 0.0:
            return False
        if rate >= 1.0:
            return True
        return random.random() < rate

    def _effective_rate(self) -> float:
        """Pick the right sample rate by env.

        Heuristic:
        - PYTEST_CURRENT_TEST in env → REPLAY_SAMPLE_RATE_TEST
        - settings.DEBUG True → REPLAY_SAMPLE_RATE_STAGING (dev mirrors staging)
        - otherwise → REPLAY_SAMPLE_RATE_PROD
        """

        if "PYTEST_CURRENT_TEST" in os.environ:
            return float(getattr(settings, "REPLAY_SAMPLE_RATE_TEST", 0.0))
        if getattr(settings, "DEBUG", False):
            return float(getattr(settings, "REPLAY_SAMPLE_RATE_STAGING", 1.0))
        return float(getattr(settings, "REPLAY_SAMPLE_RATE_PROD", 1.0))


# Module-level singleton — Sprint 6 pipeline imports and uses this.
_DEFAULT_RECORDER = TraceRecorder()


def capture(trace_id: str, pipeline_steps: list[Any]) -> Any | None:
    """Module-level convenience for the default recorder instance."""

    return _DEFAULT_RECORDER.capture(trace_id, pipeline_steps)
