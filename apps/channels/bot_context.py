"""Which bot is speaking — ContextVar propagation (DRF-1061).

### The problem this solves

With one MAX bot, "who is sending this message" was never a question:
``apps/channels/max/outbound.py`` read a single ``MAX_BOT_TOKEN`` and every
reply on every path went out as that bot. With a salon-staff bot alongside
the client bot, the same call sites must send as *different* identities
depending on which bot's update is being handled.

Threading a ``bot=`` argument through every layer would work for the code
this ticket touches, but not for the code it does not: the outbound helper
is called from skills, tasks, notification helpers and admin services, and
any path that forgot to pass it would silently answer the salon bot's user
**as the client bot**. That failure is invisible in tests that stub the
sender and obvious only to the person who receives a message from the wrong
bot — the worst possible place to find it.

So the bot identity travels the same way the tenant already does (ADR-0003,
``apps.tenancy.context``): set once at the boundary that knows the answer —
the consumer handling a stream, or a request whose initData verified against
a specific bot — and read wherever it is needed.

### Precedence

``outbound`` resolves the sending identity in this order:

1. an explicit ``bot=`` argument — always wins, for call sites that
   genuinely address a specific bot regardless of context;
2. ``current_bot()`` — the surrounding scope;
3. ``settings.MAX_BOT_TOKEN`` — the pre-registry single-bot behaviour, so
   nothing changes for deployments and code paths that never opt in.

Same ContextVar reasoning as tenancy: ``contextvars`` (not
``threading.local``) so the value survives ``await`` and
``sync_to_async``/``async_to_sync`` boundaries, and token-based reset via
:func:`bot_scope` so an identity cannot leak into the next message handled
by the same worker task.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import TYPE_CHECKING, Iterator

if TYPE_CHECKING:  # pragma: no cover - typing only
    from apps.channels.bot_registry import BotEntry

_current_bot: ContextVar["BotEntry | None"] = ContextVar("current_bot", default=None)


def current_bot() -> "BotEntry | None":
    """The bot whose conversation this code is running inside, if known.

    ``None`` means "not in a bot-scoped context" — callers fall back to the
    legacy single-bot settings rather than guessing.
    """

    return _current_bot.get()


def set_bot(bot: "BotEntry | None") -> Token:
    """Set the current bot. Returns the reset token — prefer :func:`bot_scope`."""

    return _current_bot.set(bot)


def reset_bot(token: Token) -> None:
    """Restore the previous value. Always pair with :func:`set_bot`."""

    _current_bot.reset(token)


@contextmanager
def bot_scope(bot: "BotEntry | None") -> Iterator[None]:
    """Run a block with ``bot`` as the sending identity.

    Set this at the boundary that knows which bot an update belongs to —
    the stream consumer, or a verified Mini App request. Everything inside
    then sends as that bot without being told.

    The ``finally`` is the point: a worker task handles many messages in
    sequence, and an identity left set would answer the *next* user as the
    previous user's bot.
    """

    token = set_bot(bot)
    try:
        yield
    finally:
        reset_bot(token)
