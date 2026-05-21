"""PEL reaper — XAUTOCLAIM-based drainer for ``ingress:*`` streams (issue #499).

Background
==========

``STRICT_TENANT_REFUSE=True`` makes ``TenantAwareTask.__call__`` raise
``TenantRequiredButMissing`` when a handler tagged ``requires_tenant=True``
receives an entry with empty/invalid ``resolved_tenant_id``. The consumer
loop in ``apps.workers.consumer`` does NOT XACK on exception, so the
entry stays in the Pending Entries List (PEL) — Redis's per-consumer-group
buffer of «delivered but not acknowledged» entries.

Pre-#499, that meant entries sat in the PEL indefinitely until an
operator ran ``XCLAIM`` manually. With even modest misbehaving ingress
the PEL would grow unbounded — see the runbook's «D-2 operational
ceilings» section.

What this module does
=====================

A Celery beat task calls XAUTOCLAIM on each registered ``ingress:*``
stream to claim entries idle past a configurable threshold (default 1h),
classifies each claimed entry, then routes it:

* **Terminal** — XADD to ``<stream>:dlq`` (a parallel stream operators
  can inspect / manually replay), then XACK the original. Default for
  all reaper-claimed entries today — the PEL stuck on a real failure
  (``TenantRequiredButMissing`` or any other handler raise) is by
  definition unrecoverable without operator intervention.
* **Replay** — re-XADD to the source stream with corrected metadata,
  then XACK. (Hook in place for future classifiers; no default replay
  path in this PR.)

Every reaped entry emits a ``worker.pel_reaped`` audit row with
``stream``, ``entry_id``, ``classification``, ``decision``.

Operational notes
=================

* **Opt-in via ``settings.PEL_REAPER_ENABLED``** (default False during
  rollout). The Celery task no-ops when disabled so adding the beat
  schedule entry is safe before the flip.
* **Idle threshold ``settings.PEL_REAPER_IDLE_SECONDS``** (default 3600).
  Entries claimed only if pending longer than this — guards against
  reaping entries that a slow handler is still working on.
* **Batch size ``settings.PEL_REAPER_BATCH_SIZE``** (default 100). Caps
  the work per beat tick; entries past the batch wait for the next tick.
* **DLQ stream name = ``<source_stream>:dlq``**. Lowercase, parallel to
  source convention. Operators can XRANGE the DLQ stream to triage,
  XADD back to the source after fixing ingress, or just XDEL after
  manual review.

The reaper itself runs as a single consumer named ``reaper`` against
the same ``consumers`` group; no extra consumer-group setup required.
"""

from __future__ import annotations

import logging
import socket
from dataclasses import dataclass
from typing import Any

from django.conf import settings

from apps.events.services import emit
from apps.ingress import streams as ingress_streams
from apps.ingress.streams import DEFAULT_GROUP_NAME
from apps.workers.registry import registered_streams

logger = logging.getLogger(__name__)

DLQ_SUFFIX = ":dlq"
PEL_REAPED_EVENT_TYPE = "worker.pel_reaped"

# D-1 (adversarial-pass): hostname-suffixed consumer name so two beat
# processes (deploy race, ops mistake) don't share the same XAUTOCLAIM
# consumer identity and race on partial overlapping batches.
REAPER_CONSUMER_NAME = f"reaper@{socket.gethostname()}"

# B2 (adversarial-pass): namespaced forensic-header keys to prevent
# field collision with attacker-controlled ingress payloads. Double-
# underscore + dot makes accidental collision unlikely + visually
# distinct in DLQ triage. If an original payload field happens to use
# one of these keys (e.g. unlucky ingress chose ``__reaper.from``),
# the original value is preserved under ``__reaper.shadowed.<key>``.
_FORENSIC_NAMESPACE = "__reaper"
_FORENSIC_FROM = f"{_FORENSIC_NAMESPACE}.from"
_FORENSIC_ENTRY_ID = f"{_FORENSIC_NAMESPACE}.entry_id"
_FORENSIC_CLASSIFICATION = f"{_FORENSIC_NAMESPACE}.classification"
_FORENSIC_SHADOWED_PREFIX = f"{_FORENSIC_NAMESPACE}.shadowed."

# D-3 (adversarial-pass): MAXLEN cap on DLQ stream XADD so a misbehaving
# ingress can't produce unbounded `<stream>:dlq` growth (Redis memory).
# Approximate trimming (~) is much cheaper than exact trimming and the
# tail-trim by a few entries is operationally fine.
DLQ_STREAM_MAXLEN = 100_000

# B3 (adversarial-pass): per-stream cursor key for resumable XAUTOCLAIM
# pagination so a permanently-failing low-id entry doesn't starve the
# tail. Cursor persists in Redis across beat ticks.
_CURSOR_KEY_PREFIX = "pel_reaper:cursor:"
# Cursor TTL: 24h so a misconfiguration / long pause doesn't leave a
# stale cursor blocking the reaper indefinitely. The reaper resets to
# "0" on drain-complete anyway; this is belt-and-suspenders.
_CURSOR_TTL_SECONDS = 86400


@dataclass(frozen=True)
class PelEntryDecision:
    """Classifier output for a single reaped entry.

    Attributes:
      classification: Human-readable label (e.g. ``"tenant_required_missing"``,
        ``"handler_failure"``, ``"unknown"``). Goes onto the audit row.
      decision: ``"terminal"`` (move to DLQ + XACK) or ``"replay"``
        (re-XADD to source + XACK). Today only ``"terminal"`` is
        produced; ``"replay"`` is reserved for future classifier hooks.
    """

    classification: str
    decision: str


def _coerce_setting(raw: Any, *, default: int, min_value: int, name: str) -> tuple[int, bool]:
    """Coerce a settings value to a clamped int with diagnostics.

    Returns ``(value, was_clamped)``. ``was_clamped`` is True when the
    raw value was unparseable OR below ``min_value`` (in either case we
    log + return the safe default / clamped value). B1 (adversarial-pass).
    """

    try:
        parsed = int(raw)
    except (TypeError, ValueError):
        logger.error(
            "workers.reaper.invalid_setting name=%s raw=%r — defaulting to %d",
            name,
            raw,
            default,
        )
        return default, True
    if parsed < min_value:
        logger.error(
            "workers.reaper.below_min_setting name=%s raw=%r min=%d — clamping to %d",
            name,
            raw,
            min_value,
            min_value,
        )
        return min_value, True
    return parsed, False


def _read_cursor(client: Any, cursor_key: str) -> str:
    """Read the persisted XAUTOCLAIM cursor for a stream.

    Returns ``"0"`` (start-from-beginning) if the cursor has never been
    written, expired, or is unreadable. The reaper proceeds with that
    default — at worst it re-scans entries that real Redis would skip
    via the IDLE filter anyway. B3 (adversarial-pass).
    """

    try:
        raw = client.get(cursor_key)
    except Exception:  # noqa: BLE001 — defensive against Redis API surface
        return "0"
    if raw is None:
        return "0"
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    return str(raw) or "0"


def _write_cursor(client: Any, cursor_key: str, cursor: str) -> None:
    """Persist the next-XAUTOCLAIM cursor with a 24h TTL.

    Failure here is non-fatal — the next reap tick will fall back to
    ``"0"`` and re-scan from the beginning, which is the same behaviour
    as pre-B3. We log but don't raise. B3 (adversarial-pass).
    """

    try:
        client.set(cursor_key, cursor, ex=_CURSOR_TTL_SECONDS)
    except Exception:  # noqa: BLE001
        logger.exception(
            "workers.reaper.cursor_write_failed cursor_key=%s cursor=%s",
            cursor_key,
            cursor,
        )


def _build_dlq_fields(
    fields: dict[str, Any],
    stream: str,
    entry_id: str,
    classification: str,
) -> dict[str, Any]:
    """Build DLQ XADD fields with the ``__reaper`` forensic namespace,
    preserving any colliding original-payload values under a shadowed
    key prefix so operator triage doesn't lose information.

    B2 (adversarial-pass): a malicious / accidentally-overlapping
    ingress payload field ``__reaper.from = "ingress:vk"`` would have
    been silently overwritten by the naïve ``{**fields, "_reaped_from": stream}``
    dict-spread. Now we explicitly preserve any colliding original
    value under ``__reaper.shadowed.__reaper.from`` so the spoof is
    visible at triage time.
    """

    out: dict[str, Any] = {}
    reserved = {_FORENSIC_FROM, _FORENSIC_ENTRY_ID, _FORENSIC_CLASSIFICATION}
    for key, value in fields.items():
        if key in reserved:
            out[f"{_FORENSIC_SHADOWED_PREFIX}{key}"] = value
        else:
            out[key] = value
    out[_FORENSIC_FROM] = stream
    out[_FORENSIC_ENTRY_ID] = entry_id
    out[_FORENSIC_CLASSIFICATION] = classification
    return out


def _dlq_stream_for(stream: str) -> str:
    """Return the DLQ stream name parallel to ``stream``.

    Convention: ``ingress:max`` → ``ingress:max:dlq``. Suffix-based so
    a future change to source-stream namespacing only needs to update
    this single helper.
    """

    return f"{stream}{DLQ_SUFFIX}"


def _classify_pel_entry(
    stream: str,
    entry_id: str,
    fields: dict[str, Any],
) -> PelEntryDecision:
    """Classify a reaped PEL entry.

    Today's rule: every reaped entry is terminal — by definition it has
    been idle past the threshold AND failed at least one dispatch
    attempt (the original delivery that put it in PEL). Replaying with
    identical payload would re-fail. Operator can manually replay from
    the DLQ stream after fixing upstream (e.g. ingress tenant resolution).

    Hook is here for future classifiers — e.g. «entry's stored
    resolved_tenant_id is now valid» → replay candidate.

    Args:
      stream: Source stream name.
      entry_id: Redis stream entry ID.
      fields: Decoded entry fields (data, trace_id, resolved_tenant_id, …).

    Returns:
      :class:`PelEntryDecision`.
    """

    # Heuristic: when resolved_tenant_id is empty/missing, surface that
    # specifically on the audit row so operators can spot the B4
    # strict-mode refusal cluster. Otherwise label it generically.
    resolved_tenant_id = fields.get("resolved_tenant_id", "")
    if not resolved_tenant_id:
        classification = "tenant_required_missing"
    else:
        classification = "handler_failure"

    return PelEntryDecision(
        classification=classification,
        decision="terminal",
    )


def reap_pel_once(
    stream: str,
    *,
    group: str = DEFAULT_GROUP_NAME,
    idle_ms: int,
    batch_size: int,
) -> int:
    """Reap one batch of PEL entries from ``stream``.

    Calls XAUTOCLAIM to claim entries idle past ``idle_ms`` ms, then for
    each claimed entry: classifies → XADD to DLQ stream OR re-XADD to
    source → XACK the original → emit ``worker.pel_reaped`` audit.

    Args:
      stream: Source stream name (e.g. ``"ingress:max"``).
      group: Consumer-group name. Default ``"consumers"`` (matches
        :func:`apps.workers.consumer.consume_once`).
      idle_ms: Minimum idle time (milliseconds) before an entry is
        eligible for reaping.
      batch_size: Maximum entries claimed per call.

    Returns:
      Number of entries reaped (terminal + replay combined).
    """

    client = ingress_streams._client()

    # B3 (adversarial-pass): resumable cursor. ``start_id="0"`` every
    # call would scan from the lowest PEL id, so a permanently-failing
    # low-id entry (XACK-fail loop) would starve everything past
    # ``batch_size``. Persisting the cursor in Redis lets us continue
    # from where the previous tick left off, returning to "0" only when
    # the previous tick drained the eligible set.
    cursor_key = f"{_CURSOR_KEY_PREFIX}{stream}"
    start_id = _read_cursor(client, cursor_key)

    # XAUTOCLAIM returns [next_cursor, [(entry_id, fields), ...], [deleted_ids]].
    # The fake-redis stub and real redis-py both honour this shape.
    result = client.xautoclaim(
        name=stream,
        groupname=group,
        consumername=REAPER_CONSUMER_NAME,
        min_idle_time=idle_ms,
        start_id=start_id,
        count=batch_size,
    )

    if not result:
        # No cursor write — leaves any persisted cursor unchanged.
        return 0

    # redis-py returns a tuple; some versions return a list. Normalise.
    if len(result) >= 2:
        next_cursor, claimed = result[0], result[1]
    else:  # defensive — shape changed upstream
        return 0

    # Normalise cursor (bytes → str). "0" means «drained — start over
    # next tick»; anything else is the resume point.
    if isinstance(next_cursor, bytes):
        next_cursor = next_cursor.decode("utf-8")
    next_cursor = str(next_cursor) if next_cursor is not None else "0"
    _write_cursor(client, cursor_key, next_cursor)

    if not claimed:
        return 0

    dlq_stream = _dlq_stream_for(stream)
    reaped = 0

    for entry_id, raw_fields in claimed:
        if isinstance(entry_id, bytes):
            entry_id = entry_id.decode("utf-8")
        # D-5 (adversarial-pass): non-UTF8 bytes in field values would
        # crash the entire batch with UnicodeDecodeError. Use
        # ``errors="replace"`` so a malicious / corrupt payload only
        # garbles its own DLQ row, not the surrounding batch.
        fields = {
            (k.decode("utf-8", errors="replace") if isinstance(k, bytes) else k): (
                v.decode("utf-8", errors="replace") if isinstance(v, bytes) else v
            )
            for k, v in raw_fields.items()
        }

        decision = _classify_pel_entry(stream, entry_id, fields)

        # Phase H first-pass follow-up: split XADD/XACK/emit into separate
        # try blocks. Each side-effect's failure mode is distinct:
        #
        # 1. XADD-to-DLQ fails → entry stays in PEL (no XACK) → next tick
        #    re-claims it. Safe — at worst a per-tick log-spam until Redis
        #    recovers.
        # 2. XACK fails after XADD succeeds → entry is in DLQ AND PEL →
        #    next tick re-reaps → duplicate DLQ row. We log and skip the
        #    emit on this entry so the duplicate isn't ALSO audited
        #    twice; the duplicate-DLQ-row is a known degenerate state
        #    documented in the runbook (operator dedup by
        #    ``_reaped_entry_id`` field if it surfaces).
        # 3. emit() fails after XADD+XACK succeed → forensic gap (no
        #    audit row) but no other state corruption. Log and continue
        #    to the next entry instead of breaking the batch.
        try:
            if decision.decision == "terminal":
                # B2 (adversarial-pass): build DLQ fields with the
                # forensic namespace, preserving any original payload
                # value that collides with a reserved key under a
                # ``__reaper.shadowed.<key>`` shadow. Triage tooling
                # can trust the ``__reaper.*`` values are reaper-set.
                dlq_fields = _build_dlq_fields(fields, stream, entry_id, decision.classification)
                # D-3 (adversarial-pass): MAXLEN cap on DLQ XADD so a
                # misbehaving ingress can't produce unbounded growth.
                # ``approximate=True`` (~) is much cheaper than exact
                # trim and acceptable for operator triage.
                client.xadd(
                    dlq_stream,
                    dlq_fields,
                    maxlen=DLQ_STREAM_MAXLEN,
                    approximate=True,
                )
            elif decision.decision == "replay":
                # Future classifier hook — re-XADD with corrected metadata.
                # Caller decides what «corrected» means; today's classifier
                # never emits replay.
                client.xadd(stream, fields)
            else:
                logger.error(
                    "workers.reaper.unknown_decision stream=%s entry_id=%s "
                    "decision=%s — skipping XACK to leave entry in PEL",
                    stream,
                    entry_id,
                    decision.decision,
                )
                continue
        except Exception:  # noqa: BLE001
            logger.exception(
                "workers.reaper.xadd_failed stream=%s entry_id=%s "
                "decision=%s — leaving entry in PEL for next tick",
                stream,
                entry_id,
                decision.decision,
            )
            continue

        try:
            client.xack(stream, group, entry_id)
        except Exception:  # noqa: BLE001
            logger.exception(
                "workers.reaper.xack_failed stream=%s entry_id=%s — "
                "entry now in DLQ AND PEL (will re-reap on next tick; "
                "operator may see duplicate _reaped_entry_id rows)",
                stream,
                entry_id,
            )
            # Skip the emit too — next tick's audit row will cover it.
            continue

        try:
            emit(
                PEL_REAPED_EVENT_TYPE,
                payload={
                    "stream": stream,
                    "entry_id": entry_id,
                    "classification": decision.classification,
                    "decision": decision.decision,
                    "dlq_stream": dlq_stream if decision.decision == "terminal" else None,
                },
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "workers.reaper.audit_emit_failed stream=%s entry_id=%s — "
                "entry already moved to DLQ + XACK'd; forensic gap on this "
                "row only, batch continues",
                stream,
                entry_id,
            )
        reaped += 1

    if reaped:
        logger.info(
            "workers.reaper.batch stream=%s reaped=%d batch_size=%d idle_ms=%d",
            stream,
            reaped,
            batch_size,
            idle_ms,
        )

    return reaped


def reap_pel_streams() -> int:
    """Reap PEL entries from every registered ``ingress:*`` stream.

    Reads settings:

      * ``PEL_REAPER_ENABLED`` (default False) — no-op when False.
      * ``PEL_REAPER_IDLE_SECONDS`` (default 3600).
      * ``PEL_REAPER_BATCH_SIZE`` (default 100).

    Returns:
      Total entries reaped across all streams in this tick.
    """

    if not bool(getattr(settings, "PEL_REAPER_ENABLED", False)):
        logger.debug("workers.reaper.disabled — PEL_REAPER_ENABLED=False")
        return 0

    # B1 (adversarial-pass): clamp at read site so a misconfigured
    # env var (typo `=0`, accidental `=-1`, or empty string) can't
    # silently disable the reaper or crash the Celery beat. Log if
    # we had to coerce so ops can spot the misconfiguration.
    idle_seconds, idle_was_clamped = _coerce_setting(
        getattr(settings, "PEL_REAPER_IDLE_SECONDS", 3600),
        default=3600,
        min_value=0,
        name="PEL_REAPER_IDLE_SECONDS",
    )
    batch_size, batch_was_clamped = _coerce_setting(
        getattr(settings, "PEL_REAPER_BATCH_SIZE", 100),
        default=100,
        min_value=1,
        name="PEL_REAPER_BATCH_SIZE",
    )
    if idle_was_clamped or batch_was_clamped:
        logger.warning(
            "workers.reaper.settings_clamped effective_idle=%d effective_batch=%d",
            idle_seconds,
            batch_size,
        )
    idle_ms = idle_seconds * 1000

    streams = registered_streams()
    if not streams:
        logger.debug("workers.reaper.no_streams_registered")
        return 0

    total = 0
    for stream in streams:
        total += reap_pel_once(
            stream,
            idle_ms=idle_ms,
            batch_size=batch_size,
        )

    return total
