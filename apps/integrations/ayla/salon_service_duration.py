"""Which duration field a salon-service row answers with (DRF-2705).

The catalog's ``salon-services`` row carries two
(``beautygo_backend/docs/CATALOG_INTERNAL_API_CONTRACT.md`` §1):

* ``duration_minutes`` — «**raw** salon-level default. ``null`` = the salon
  set nothing of its own; the value then comes from the template»;
* ``resolved_duration`` — «**effective** salon-level duration: salon →
  template. **Use this.**» Added 2026-10-01.

Until that field existed the row said «null ⇒ resolves from template» and
gave the bot nothing to resolve with: the mirror stored ``None``, and the
Mini App refused slots («service has no duration configured») for a service
the catalog sells. Both readers of the row ask here, so they cannot disagree
about which field is the answer.

The raw field stays as the fallback for one case only: the row has no
``resolved_duration`` key at all — a catalog older than the field. When the
key is there, its value is the answer even if that answer is ``null``; the
raw number never stands in for a resolved one the catalog did send. Same
rule as :func:`apps.integrations.ayla.edge_duration.duration_from_edge`.

This returns the WIRE value, unparsed. The two readers keep their own
policies for a malformed one, and those differ on purpose: the mirror raises
so the row is dropped loudly (DRF-1494), the booking client reads it as
unknown.
"""

from __future__ import annotations

from typing import Any


def salon_service_duration_field(row: dict[str, Any]) -> Any:
    """``resolved_duration`` when the catalog sent the key, else ``duration_minutes``."""
    if "resolved_duration" in row:
        return row["resolved_duration"]
    return row.get("duration_minutes")
