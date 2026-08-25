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


__all__ = [
    "FORBIDDEN_PII_KEYS",
    "SELF_PII_EXEMPT_PATHS",
    "find_forbidden_pii",
]
