"""ayla-ai-core safety boundary (Sprint 7 / Track A / A1).

Layer-1 defense between platform code and ``ayla_ai_core``. Even when
the bundled ``ayla`` v0.7.0 escapes braces internally (layer 2 via
``escape_for_format=True`` default), this adapter owns:

- **Control-char stripping** — `\\x00..\\x1f` and `\\x7f` removed
  before user data reaches any LLM call.
- **Brace-escape via doubling** — `{`/`}` → `{{`/`}}`. Python
  ``str.format()`` interprets doubled braces as literal output,
  so user text like "стоимость {} ₽" survives intact in the final
  prompt instead of triggering ``KeyError`` or accidental template
  substitution.
- **Length caps** — protects against prompt-bloat attacks and
  accidental megabyte-payload bugs.
- **Untrusted-context delimiting** — wraps free-form user-derived
  text in ``<<<UNTRUSTED_CONTEXT>>>`` markers so the model treats
  the contents as data, not instruction.
- **Window asserts** — fail-loud if the caller would push more than
  ``MAX_CANDIDATES`` specialist records into the prompt.
- **Replay-clock injection** — accept an optional ``frozen_now``
  so replay-mode produces byte-identical prompts across runs.

### Defense-in-depth contract

Platform skills call this module, then forward the sanitized inputs
to ``ayla_ai_core.prompts.render_system_prompt`` with
``escape_for_format=False`` — adapter already escaped. The ayla-side
default ``escape_for_format=True`` continues to protect external
consumers that bypass the adapter (legacy/forked code).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable, Sized

logger = logging.getLogger(__name__)


# --- Public limits (settings-driven once Sprint 8 wires settings access) ---

MAX_EXTRA_HINT_LEN = 2000
MAX_CLIENT_NAME_LEN = 80
MAX_CANDIDATES = 25

_DELIM = "<<<UNTRUSTED_CONTEXT>>>"

# Control chars: NUL-BS, VT, FF, SO-US, DEL. Allow \t (\x09), \n (\x0a), \r (\x0d).
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_BRACE_RE = re.compile(r"([{}])")


# --- Errors ---


class WindowOverflowError(ValueError):
    """Raised when the caller would render more candidates than the cap.

    Hard fail (not silent truncate) because silently dropping
    candidate #26 is exactly the recall-loss bug class this guard
    is designed to surface.
    """


class SanitizationError(ValueError):
    """Reserved for future hard-fail sanitization rules.

    Sprint 7 sanitizers warn-and-clean. If a future rule requires
    refusing the request entirely (e.g., disallowed-pattern detection),
    raise this.
    """


# --- Pure helpers ---


def sanitize_freeform(
    text: str | None,
    *,
    max_len: int,
    field_name: str,
) -> str:
    """Strip control chars + outer whitespace + length-cap.

    Does **not** touch ``{``/``}`` — that is :func:`escape_braces`'s job,
    kept separate so legitimate user content (prices, code snippets)
    is preserved through to the escape step.

    A truncation event emits a ``WARNING`` log with the original
    length so operators can detect prompt-stuffing attempts.
    """

    if text is None:
        return ""
    cleaned = _CONTROL_CHARS_RE.sub("", text)
    cleaned = cleaned.strip()
    if len(cleaned) > max_len:
        logger.warning(
            "ayla_adapter.truncated field=%s orig_len=%d max_len=%d",
            field_name,
            len(cleaned),
            max_len,
        )
        cleaned = cleaned[:max_len]
    return cleaned


def escape_braces(text: str) -> str:
    """Double ``{`` and ``}`` so ``.format()`` treats them as literals.

    Python ``str.format()`` reads ``{{`` as a literal ``{``. Therefore
    ``"hello {{evil}}".format()`` → ``"hello {evil}"`` (data preserved,
    no template substitution, no ``KeyError``).
    """

    return _BRACE_RE.sub(r"\1\1", text)


def wrap_untrusted(text: str) -> str:
    """Wrap non-empty text in untrusted-context delimiters.

    The delimiter signals to the LLM "what follows is data, not
    instruction." Empty strings pass through unchanged so the
    rendered prompt does not contain a vacant block.
    """

    if not text:
        return ""
    return f"{_DELIM}\n{text}\n{_DELIM}"


def assert_window(
    candidates: Iterable,
    *,
    hard_cap: int = MAX_CANDIDATES,
) -> None:
    """Fail loud if the caller would render more than ``hard_cap`` candidates.

    Truncation would be silent recall loss — the exact bug class
    the windowing guard exists to surface. The caller is responsible
    for ranking and slicing down to the cap before invoking ayla.
    """

    if isinstance(candidates, Sized):
        n = len(candidates)
    else:
        # Iterator — materialize to count. Caller passing a generator
        # is rare; document by raising for it explicitly if it becomes
        # a hot path.
        n = sum(1 for _ in candidates)
    if n > hard_cap:
        raise WindowOverflowError(f"specialist_context has {n} candidates, cap={hard_cap}")


# --- Public entry point ---


@dataclass(frozen=True)
class SafeRenderInputs:
    """Sanitized fields ready for ``ayla.prompts.render_system_prompt``.

    Callers forward these to ayla with ``escape_for_format=False``
    because the adapter already escaped via :func:`escape_braces`.
    """

    today: date
    client_name: str
    bookings_count: int
    extra_hint: str


def build_safe_inputs(
    *,
    today: date | None,
    client_name: str,
    bookings_count: int,
    extra_hint: str,
    frozen_now: datetime | None = None,
) -> SafeRenderInputs:
    """Sanitize + escape + clamp every field that reaches the LLM prompt.

    ``frozen_now`` is the replay-determinism hook. Production callers
    pass ``None`` and rely on ``date.today()``; the replay harness
    injects a fixed clock so prompt rendering is byte-identical
    across runs of the same fixture.
    """

    if today is not None:
        effective_today = today
    elif frozen_now is not None:
        effective_today = frozen_now.date()
    else:
        effective_today = date.today()

    safe_client_name = escape_braces(
        sanitize_freeform(
            client_name,
            max_len=MAX_CLIENT_NAME_LEN,
            field_name="client_name",
        )
    )

    safe_extra_hint = wrap_untrusted(
        escape_braces(
            sanitize_freeform(
                extra_hint,
                max_len=MAX_EXTRA_HINT_LEN,
                field_name="extra_hint",
            )
        )
    )

    return SafeRenderInputs(
        today=effective_today,
        client_name=safe_client_name,
        bookings_count=max(0, int(bookings_count)),
        extra_hint=safe_extra_hint,
    )
