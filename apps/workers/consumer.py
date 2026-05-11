"""Redis Streams consumer loop (DRF-425 / C3).

Reads from ``ingress:*`` streams via XREADGROUP, dispatches each entry
to its registered TenantAwareTask handler, XACKs on success. Handler
failures keep the message in the PEL (Pending Entries List) for retry
or manual escalation.

CLI entrypoints:
  python -m apps.workers.consumer --once     # consume one batch and exit
  python -m apps.workers.consumer --forever  # run until SIGINT

Sprint 1 ships the single-process consumer. Sprint 4 scales to a
worker pool. Replay rerunning (Sprint 5) reuses this loop with a
different ``GROUP_NAME``.
"""

from __future__ import annotations

import argparse
import logging
import socket
import sys
from typing import Iterable

from apps.events.services import emit
from apps.ingress import streams as ingress_streams
from apps.ingress.streams import DEFAULT_GROUP_NAME
from apps.tenancy.context import tenant_scope, trace_id_scope
from apps.workers.registry import lookup, registered_streams

logger = logging.getLogger(__name__)


def _consumer_name() -> str:
    """Unique consumer name within the group. host:pid is good enough.

    XREADGROUP needs unique consumer names so PEL ownership stays
    correct across worker pool members. Sprint 4 scales this further.
    """

    return f"{socket.gethostname()}-{__import__('os').getpid()}"


def consume_once(
    streams: Iterable[str] | None = None,
    *,
    group: str = DEFAULT_GROUP_NAME,
    block_ms: int = 1000,
    count: int = 10,
) -> int:
    """Read up to ``count`` entries across the given streams and dispatch.

    Args:
      streams: Streams to read. Default = every registered stream.
      group: Consumer group name. Default ``consumers``.
      block_ms: How long to block for new entries before returning.
      count: Max entries to consume per call.

    Returns:
      Number of entries successfully processed (XACK'd).
    """

    # Call via module attribute so test monkeypatches on streams._client
    # propagate here without import-time aliasing.
    client = ingress_streams._client()
    targets = list(streams) if streams is not None else registered_streams()
    if not targets:
        logger.info("workers.consume_once.no_streams_registered")
        return 0

    # XREADGROUP wants {stream: ">"} to get only new entries.
    streams_dict = dict.fromkeys(targets, ">")
    consumer = _consumer_name()
    result = client.xreadgroup(
        groupname=group,
        consumername=consumer,
        streams=streams_dict,  # type: ignore[arg-type]
        count=count,
        block=block_ms,
    )

    if not result:
        return 0
    # redis-py types xreadgroup as Awaitable|Any (it's a sync client; the type
    # hint hedges for async variant); cast for mypy.
    result = list(result)  # type: ignore[arg-type]

    processed = 0
    for stream_name, entries in result:
        if isinstance(stream_name, bytes):
            stream_name = stream_name.decode("utf-8")
        handler = lookup(stream_name)
        if handler is None:
            logger.warning(
                "workers.no_handler_for_stream stream=%s",
                stream_name,
            )
            continue

        for entry_id, raw_entry in entries:
            if isinstance(entry_id, bytes):
                entry_id = entry_id.decode("utf-8")
            decoded = {
                (k.decode("utf-8") if isinstance(k, bytes) else k): (
                    v.decode("utf-8") if isinstance(v, bytes) else v
                )
                for k, v in raw_entry.items()
            }

            # Enter tenant + trace scope BEFORE emitting worker.consumed
            # so the event row carries the same trace_id as the rest of
            # the pipeline. Without this, the consumer emit happens
            # outside ContextVar scope and the event is "orphan" (empty
            # trace_id) — Sprint 5 replay can't reconstruct the timeline.
            from apps.tenancy.models import Tenant  # local import

            tenant_id_raw = decoded.get("resolved_tenant_id", "")
            tenant_for_scope = None
            if tenant_id_raw:
                try:
                    tenant_for_scope = Tenant.all_objects.get(id=tenant_id_raw)
                except (Tenant.DoesNotExist, ValueError):
                    tenant_for_scope = None
            trace = decoded.get("trace_id") or None

            handler_failed = False
            with tenant_scope(tenant_for_scope), trace_id_scope(trace):
                emit(
                    "worker.consumed",
                    payload={
                        "stream": stream_name,
                        "entry_id": entry_id,
                        "handler": type(handler).__name__,
                    },
                )
                try:
                    handler(decoded)
                except Exception:  # noqa: BLE001 — handler logged + emitted; consumer continues
                    logger.exception(
                        "workers.handler_raised stream=%s entry_id=%s",
                        stream_name,
                        entry_id,
                    )
                    handler_failed = True

            if handler_failed:
                # Do NOT XACK on failure. Entry stays in PEL for retry
                # / manual claim. Consumer moves on to the next entry.
                continue

            client.xack(stream_name, group, entry_id)
            processed += 1

    return processed


def consume_forever(
    streams: Iterable[str] | None = None,
    *,
    group: str = DEFAULT_GROUP_NAME,
) -> None:
    """Run consume_once in a loop until SIGINT."""

    logger.info(
        "workers.consume_forever.starting streams=%s group=%s consumer=%s",
        list(streams or registered_streams()),
        group,
        _consumer_name(),
    )
    try:
        while True:
            consume_once(streams=streams, group=group)
    except KeyboardInterrupt:
        logger.info("workers.consume_forever.stopped_by_sigint")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="apps.workers.consumer")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true", help="Consume one batch and exit.")
    mode.add_argument(
        "--forever",
        action="store_true",
        help="Run until SIGINT.",
    )
    parser.add_argument(
        "--streams",
        nargs="*",
        default=None,
        help="Override registered streams (default = every registered).",
    )
    parser.add_argument(
        "--group",
        default=DEFAULT_GROUP_NAME,
        help=f"Consumer group name (default {DEFAULT_GROUP_NAME!r}).",
    )
    args = parser.parse_args(argv)

    # Django setup so models/ORM work in the CLI.
    import django

    django.setup()

    if args.once:
        n = consume_once(streams=args.streams, group=args.group)
        print(f"workers.consume_once.processed n={n}")
        return 0
    consume_forever(streams=args.streams, group=args.group)
    return 0


if __name__ == "__main__":
    sys.exit(main())
