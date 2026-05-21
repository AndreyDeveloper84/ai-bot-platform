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
from dataclasses import dataclass
from typing import Any

from django.conf import settings

from apps.events.services import emit
from apps.ingress import streams as ingress_streams
from apps.ingress.streams import DEFAULT_GROUP_NAME
from apps.workers.registry import registered_streams

logger = logging.getLogger(__name__)

REAPER_CONSUMER_NAME = "reaper"
DLQ_SUFFIX = ":dlq"
PEL_REAPED_EVENT_TYPE = "worker.pel_reaped"


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

    # XAUTOCLAIM returns [next_cursor, [(entry_id, fields), ...], [deleted_ids]].
    # The fake-redis stub and real redis-py both honour this shape.
    result = client.xautoclaim(
        name=stream,
        groupname=group,
        consumername=REAPER_CONSUMER_NAME,
        min_idle_time=idle_ms,
        start_id="0",
        count=batch_size,
    )

    if not result:
        return 0

    # redis-py returns a tuple; some versions return a list. Normalise.
    if len(result) >= 2:
        _next_cursor, claimed = result[0], result[1]
    else:  # defensive — shape changed upstream
        return 0

    if not claimed:
        return 0

    dlq_stream = _dlq_stream_for(stream)
    reaped = 0

    for entry_id, raw_fields in claimed:
        if isinstance(entry_id, bytes):
            entry_id = entry_id.decode("utf-8")
        fields = {
            (k.decode("utf-8") if isinstance(k, bytes) else k): (
                v.decode("utf-8") if isinstance(v, bytes) else v
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
                # Copy to DLQ stream with a forensic header so operators
                # can trace provenance back to the source-stream entry.
                dlq_fields = {
                    **fields,
                    "_reaped_from": stream,
                    "_reaped_entry_id": entry_id,
                    "_reaped_classification": decision.classification,
                }
                client.xadd(dlq_stream, dlq_fields)
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
                    "dlq_stream": dlq_stream
                    if decision.decision == "terminal"
                    else None,
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

    idle_seconds = int(getattr(settings, "PEL_REAPER_IDLE_SECONDS", 3600))
    batch_size = int(getattr(settings, "PEL_REAPER_BATCH_SIZE", 100))
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
