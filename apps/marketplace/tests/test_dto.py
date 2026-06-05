"""MasterCard DTO field-leak guard (#1018).

The public DTO is the safety boundary of the cross-tenant carve-out. This
test pins its exact field set so a future edit can't widen it to leak a
commercial / identity field (yclients_staff_id, ayla_user_id, invite_*,
linked_bot_user, raw, cache_version, schedules, prices, is_active).
"""

from __future__ import annotations

import dataclasses

from apps.marketplace.dto import MasterCard

_PUBLIC_FIELDS = {
    "tenant_id",
    "master_id",
    "name",
    "specialization",
    "rating",
    "photo_url",
    "city",
}

# Names that must NEVER appear on the public card.
_FORBIDDEN = {
    "yclients_staff_id",
    "ayla_user_id",
    "ayla_service_id",
    "linked_bot_user",
    "max_handle",
    "invite_status",
    "invite_token",
    "raw",
    "cache_version",
    "is_active",
    "mode",
}


def test_dto_exposes_only_public_fields() -> None:
    names = {f.name for f in dataclasses.fields(MasterCard)}
    assert names == _PUBLIC_FIELDS


def test_dto_has_no_commercial_fields() -> None:
    names = {f.name for f in dataclasses.fields(MasterCard)}
    assert names & _FORBIDDEN == set()
