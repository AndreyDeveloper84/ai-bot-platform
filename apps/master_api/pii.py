"""Canonical PII boundary for master-facing API responses (DRF-1039 / DRF-1360).

### The rule

Owner decision DRF-1039, restated verbatim in DRF-1360 (OD-W2-2):

    «телефон клиента исполнителю не передаётся ни в каком виде»

There is **no** "but the last four digits are only an identifier"
exception. A partial phone is a phone: it narrows the customer's number
to a 10⁻⁴ slice and, combined with a first name and a visit date, is
re-identifying on its own. DRF-1360 removed the one surface that shipped
such a mask (`GET /api/v1/master/customers`).

### Why this list lives in Python

Until DRF-1360 the same list existed **only** in the Mini App
(`apps/miniapp/src/lib/master-api.ts` → ``FORBIDDEN_PII_KEYS``), and it
was wired to exactly one screen — the conversations list. A neighbouring
master surface shipped ``phone_masked`` in every roster row for months
because the prohibition existed on the client, on one screen, instead of
on the server, for the whole surface.

The client-side list stays (defence-in-depth, `console.warn`-level), but
the authority is here, and the enforcement is
``apps/master_api/tests/test_pii_boundary.py`` — a sweep over every
master read endpoint. A new field of this class fails CI.

### Not to be built

A phone-reveal endpoint (even with an audit event) is **out of scope, not
deferred** — it must not be built without a new, separate owner decision
on PII. See DRF-1360.
"""

from __future__ import annotations

import re
from typing import Any

#: Response keys that must never appear in a master-facing payload.
#: Kept in lockstep with ``FORBIDDEN_PII_KEYS`` in
#: ``apps/miniapp/src/lib/master-api.ts`` (drift is asserted by
#: ``test_pii_boundary.py::TestForbiddenKeyList``).
FORBIDDEN_PII_KEYS: frozenset[str] = frozenset(
    {
        "phone",
        "phone_number",
        "phone_masked",
        "ltv",
        "ltv_rub",
        "email",
        "client_last_name",
        "client_full_name",
    }
)

#: The single documented exemption: the master's **own** MAX phone,
#: echoed back on the onboarding identity-confirm card so they can verify
#: which account they are claiming the invite with. It is the caller's own
#: data, not a customer's — DRF-1360 explicitly leaves it alone.
#:
#: Dotted path into the JSON body, matched exactly. Anything else that
#: carries a forbidden key — at any depth, under any endpoint — is a leak.
SELF_PII_EXEMPT_PATHS: frozenset[str] = frozenset({"max_user.phone_masked"})


def find_forbidden_pii(payload: Any, *, _path: str = "") -> list[str]:
    """Walk a JSON-shaped payload and return dotted paths of forbidden keys.

    List indices collapse to ``[]`` so ``customers[0].phone_masked`` and
    ``customers[7].phone_masked`` report as one path — the finding is the
    field, not the row.

    Returns an empty list when the payload is clean. Paths in
    :data:`SELF_PII_EXEMPT_PATHS` are not reported.
    """

    found: list[str] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            path = f"{_path}.{key}" if _path else str(key)
            if key in FORBIDDEN_PII_KEYS and path not in SELF_PII_EXEMPT_PATHS:
                found.append(path)
            found.extend(find_forbidden_pii(value, _path=path))
    elif isinstance(payload, list):
        list_path = f"{_path}[]" if _path else "[]"
        for item in payload:
            found.extend(find_forbidden_pii(item, _path=list_path))
    # Deduplicate while preserving first-seen order.
    return list(dict.fromkeys(found))


# ---------------------------------------------------------------------------
# Free-text redaction (DRF-1039 / OD-W2-2, second half of the rule)
# ---------------------------------------------------------------------------
#
# Stripping fields is only half the boundary. The master surface also
# echoes text the **customer wrote themselves** — the conversations-list
# excerpt and every message body in the conversation detail. When a
# customer types their own number into the chat ("мой номер +7 999 777 55
# 44, перезвоните"), a field-level gate sees nothing wrong: the payload
# carries no ``phone`` key, only ``last_message_excerpt``. The number
# reaches the master all the same, and the owner decision does not
# distinguish how it got there.
#
# So customer-authored text is redacted on the way out.

#: Canonical UUID, matched as a unit so the phone branch below can never
#: bite a piece of one.
#:
#: This is the trap in ``apps/replay/redactor.py``: its ``OTP_RE`` is
#: ``(?<![\w\d])\d{4}(?![\w\d])`` — the boundaries are on ``\w``, and a
#: UUID's separator is ``-``, which is not ``\w``. So an all-digit
#: 4-char group in a canonical UUID ("…-1234-…") satisfies both
#: lookarounds and gets replaced. Each 4-char group is all digits with
#: probability (10/16)^4 ≈ 15%, and a UUID has three of them plus a
#: 12-char tail, so a large minority of UUIDs come back mangled. We do
#: not reuse that pattern; we consume UUIDs first instead.
_UUID_RE_SRC = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"

#: A phone number as a human types one. Ten significant digits in
#: 3-3-2-2 shape, optional country code, optional ``()``/space/dash
#: separators. Anchored on both ends with digit lookarounds so it cannot
#: start or stop in the middle of a longer number.
#:
#: Deliberately broader than ``apps/observability/pii_filter.py``'s
#: ``_PHONE_RE``, which requires a literal ``+7``/``8`` prefix: a
#: customer very often types the bare ten digits ("9997775544"), and
#: under OD-W2-2 that reaches the master just the same.
_PHONE_RE_SRC = (
    r"(?<![\d])"
    r"(?:\+?\d{1,3}[\s\-]?)?"
    r"\(?\d{3}\)?[\s\-]?"
    r"\d{3}[\s\-]?\d{2}[\s\-]?\d{2}"
    r"(?!\d)"
)

#: E-mail is on :data:`FORBIDDEN_PII_KEYS` as a field; it is the same
#: class of contact detail when typed into a message.
_EMAIL_RE_SRC = r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"

#: One pass, UUID branch first. ``re`` alternation is ordered, so a
#: canonical UUID is consumed whole and the phone branch never sees its
#: digits — the false positive described above cannot occur.
_REDACT_RE = re.compile(
    rf"(?P<uuid>{_UUID_RE_SRC})"
    rf"|(?P<phone>{_PHONE_RE_SRC})"
    rf"|(?P<email>{_EMAIL_RE_SRC})"
)

#: What the master sees instead. Not a mask: a mask that keeps the last
#: four digits is precisely what OD-W2-2 struck down.
PHONE_PLACEHOLDER = "[номер скрыт]"
EMAIL_PLACEHOLDER = "[почта скрыта]"


def _redact_match(match: re.Match[str]) -> str:
    if match.lastgroup == "uuid":
        return match.group(0)
    if match.lastgroup == "email":
        return EMAIL_PLACEHOLDER
    return PHONE_PLACEHOLDER


def redact_contacts(text: str | None) -> str:
    """Strip phone numbers and e-mails out of customer-authored text.

    Applied to every free-text value the master surface echoes back —
    message bodies, list excerpts, AI drafts. Canonical UUIDs are passed
    through untouched.

    Must run **before** any truncation: truncating first can cut a phone
    in half and leave a four-digit tail in the excerpt, which is exactly
    the thing OD-W2-2 forbids.
    """

    if not text:
        return text or ""
    return _REDACT_RE.sub(_redact_match, text)


__all__ = [
    "FORBIDDEN_PII_KEYS",
    "SELF_PII_EXEMPT_PATHS",
    "PHONE_PLACEHOLDER",
    "EMAIL_PLACEHOLDER",
    "find_forbidden_pii",
    "redact_contacts",
]
