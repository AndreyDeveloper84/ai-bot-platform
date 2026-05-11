"""Consent guard decorator (DRF-458 / Sprint 3 / A3).

`@consent_required(consent_type)` wraps a function whose first
positional argument (or `bot_user` keyword) is a BotUser. Before the
wrapped function runs, the decorator checks `has_consent(bot_user,
consent_type)`. On denial it raises :class:`ConsentDenied` and emits
a ``safety_triggered`` event with reason ``"consent_denied"``.

This is a Sprint-3 primitive. The Tool registry ships in Sprint 4+;
the PrivacyConsentSkill (D2) will be the first user. Shipping the
decorator now means D2's `data_delete` tool can be annotated on day
one without touching consent code.

### bot_user resolution rule

The decorator inspects:
  1. ``kwargs["bot_user"]`` if present
  2. otherwise ``args[0]`` (the first positional)

If neither yields a BotUser, ``TypeError`` is raised at call time
(programmer error — the decorator was applied to a function whose
signature doesn't match the contract).
"""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import TYPE_CHECKING, Any, TypeVar

from apps.consent.exceptions import ConsentDenied
from apps.consent.services import has_consent
from apps.events.services import emit

if TYPE_CHECKING:
    from apps.identity.models import BotUser

F = TypeVar("F", bound=Callable[..., Any])


def _extract_bot_user(args: tuple[Any, ...], kwargs: dict[str, Any]) -> "BotUser":
    """Pull the BotUser out of (args, kwargs) per the decorator contract."""

    # Late import to avoid loading the identity app at module import time
    # (decorators are commonly imported into __init__ of skills/tools).
    from apps.identity.models import BotUser

    candidate = kwargs.get("bot_user")
    if candidate is None and args:
        candidate = args[0]
    if not isinstance(candidate, BotUser):
        raise TypeError(
            "consent_required: expected a BotUser as the first positional "
            "argument or as the 'bot_user' keyword; got "
            f"{type(candidate).__name__}."
        )
    return candidate


def consent_required(consent_type: str) -> Callable[[F], F]:
    """Decorator factory enforcing a consent check before the function runs.

    Args:
      consent_type: one of :class:`apps.consent.models.ConsentRecord.ConsentType`
                    values (e.g. ``"photo_biometric"``).

    Raises:
      ConsentDenied: when the bot_user has no active consent of this type
                     at call time. A ``safety_triggered`` event is emitted
                     with reason ``"consent_denied"`` before the exception.
      TypeError: when the wrapped function's signature does not expose a
                 BotUser as expected (programmer error).
    """

    def decorator(fn: F) -> F:
        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            bot_user = _extract_bot_user(args, kwargs)
            if not has_consent(bot_user, consent_type):
                emit(
                    "safety_triggered",
                    payload={
                        "reason": "consent_denied",
                        "consent_type": consent_type,
                        "bot_user_id": str(bot_user.id),
                        "tool": fn.__qualname__,
                    },
                )
                raise ConsentDenied(consent_type, bot_user_id=str(bot_user.id))
            return fn(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator
