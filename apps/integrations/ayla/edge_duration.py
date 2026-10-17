"""Which duration a (specialist × service) edge row quotes (DRF-2678).

The catalog's edge row carries two
(``beautygo_backend/docs/CATALOG_INTERNAL_API_CONTRACT.md`` §2):

* ``duration_minutes`` — «raw specialist override (may be null)»;
* ``resolved_duration`` — «effective duration: specialist → salon →
  template. Non-null on an active bookable. **Use this.**»

It is the resolved one the catalog stamps onto a new appointment, so it is
the one a quote shows and sends back as ``quoted_duration_minutes``. Both
quote readers — the chat preview and the Mini App's ``/customer/quote`` —
ask here, so the two cannot drift apart again.

The raw field stays as the fallback for one case only: the row has no
``resolved_duration`` key at all — a catalog older than the field. When the
key is there, its value is the answer even if that answer is «unknown»; the
raw number never stands in for a resolved one the catalog did send.
"""

from __future__ import annotations

from typing import Any


def duration_from_edge(row: dict[str, Any]) -> int | None:
    """Minutes the edge resolves to, or ``None`` when no usable value is known.

    ``None`` — never ``0``: the caller shows nothing and sends nothing for
    an unknown duration (DRF-1708). A value that is not a positive ``int``
    (a JSON string, a float, a bool) is unknown, not coerced.
    """
    value = row["resolved_duration"] if "resolved_duration" in row else row.get("duration_minutes")
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value
