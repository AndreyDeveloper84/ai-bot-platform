"""Channel-sender callback registry — breaks the orchestrator↔channels cycle.

Sprint 8 code review P1 (cycle 2). Background:

  * ``apps/channels/max/handler.py`` imports ``apps.orchestrator.memory.short_term``
    at module top — feature → feature.
  * ``apps/orchestrator/pipeline.py::_send_outbound`` had a function-local
    ``from apps.channels.max.outbound import MaxAPIError, send_message`` —
    feature → feature back. Lazy import resolved the load order but left
    a logical cycle.

The clean fix mirrors P1-cycle1 (audit↔tenancy): the upstream module
(orchestrator) declares a registry interface; the downstream module
(channels.max) registers its sender at AppConfig ``ready()`` time. The
back-edge from orchestrator to channels disappears at both the module-
load level AND the architectural-rule level.

### Contract

* Sender signature: ``(chat_id: str, text: str) -> None``
* Sender raises :class:`ChannelSendError` on retriable failure (network,
  rate-limit, 5xx). Pipeline retries with backoff.
* Sender raises any other ``Exception`` for non-retriable failure
  (auth, malformed payload). Pipeline writes DLQ and stops.

### Why a callable, not a Django signal

Same rationale as the audit-writer hook (``apps.tenancy.audit_hook``):
signals add receiver-list ordering ambiguity and force the pipeline
through Django's signal machinery for a one-channel-one-sender contract.
A simple dict-of-callables is the smaller surface.
"""

from __future__ import annotations

import logging
from typing import Callable

logger = logging.getLogger(__name__)


ChannelSender = Callable[[str, str], None]


class ChannelSendError(Exception):
    """Retriable channel-outbound failure.

    Pipeline catches this, retries with backoff, falls back to AdminTask
    DLQ after MAX_ATTEMPTS. Concrete channel adapters wrap their
    transport-specific exceptions (MaxAPIError, TelegramAPIError, etc.)
    into this generic class so the pipeline retry logic is channel-
    agnostic.
    """


_senders: dict[str, ChannelSender] = {}


def register_channel(channel: str, sender: ChannelSender) -> None:
    """Install the outbound sender for ``channel``.

    Called from ``AppConfig.ready()`` of each channel adapter. Idempotent
    — re-registering the same callable is a no-op; replacing logs at
    INFO so test fixtures can audit the swap.

    Pass ``sender=None`` (via :func:`unregister_channel`) to clear the
    registry — test cleanup only.
    """
    if _senders.get(channel) is sender:
        return
    if _senders.get(channel) is not None and _senders.get(channel) is not sender:
        logger.info(
            "orchestrator.channel_registry.replaced channel=%s old=%r new=%r",
            channel,
            _senders.get(channel),
            sender,
        )
    _senders[channel] = sender


def unregister_channel(channel: str) -> None:
    """Remove the sender for ``channel`` — test cleanup."""
    _senders.pop(channel, None)


def get_sender(channel: str) -> ChannelSender | None:
    """Return the registered sender for ``channel`` or None."""
    return _senders.get(channel)


def send(channel: str, chat_id: str, text: str) -> bool:
    """Dispatch to the registered sender. Returns True iff a sender ran.

    Returns ``False`` when no sender is registered for ``channel``
    (handler missing from INSTALLED_APPS, or test environment); the
    pipeline treats this as a non-retriable "no transport" outcome.

    The sender's own exceptions propagate — pipeline catches
    :class:`ChannelSendError` for retry and ``Exception`` for DLQ.
    """
    sender = _senders.get(channel)
    if sender is None:
        logger.warning("orchestrator.channel_registry.no_sender channel=%s", channel)
        return False
    sender(chat_id, text)
    return True
