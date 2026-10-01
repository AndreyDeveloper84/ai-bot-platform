"""DRF-2678: the chat preview quotes the edge's RESOLVED duration.

The catalog's edge row carries two durations
(``beautygo_backend/docs/CATALOG_INTERNAL_API_CONTRACT.md`` §2):

* ``duration_minutes`` — «raw specialist override (may be null)»;
* ``resolved_duration`` — «effective duration: specialist → salon → template
  … Use this».

The quote read the raw one. By the catalog's own resolution the two are equal
whenever the master has an override, so no wrong number was shown — the
defect is the other half: a master who INHERITS the salon's duration has
``duration_minutes: null``, and the preview then showed no duration and sent
no ``quoted_duration_minutes``, so the «what you saw is what is booked» check
(DRF-1708) silently did not run on duration for that master.

The edge fixture has the contract's shape. Each case names which of the two
fields would have answered, so a case cannot pass by reading either.
"""

from __future__ import annotations

from typing import Any

import pytest

from apps.skills.booking.tests import test_quote_changed_dm_1708 as _quote_module

# Fixtures and helpers of the neighbour — by assignment, as the 1933 tests do.
QuotingFake = _quote_module.QuotingFake
_adapter = _quote_module._adapter
_flag_on = _quote_module._flag_on
_payload_of = _quote_module._payload_of
_preview = _quote_module._preview
_SPEC = _quote_module._SPEC
_SVC = _quote_module._SVC
bot_user = _quote_module.bot_user
tenant = _quote_module.tenant

pytestmark = pytest.mark.django_db

_ABSENT = object()


def _edge(*, raw: Any, resolved: Any = _ABSENT) -> dict[str, Any]:
    """An edge row as the contract's §2 example has it; ``resolved`` may be
    left out entirely — a catalog older than the field."""
    row: dict[str, Any] = {
        "id": "a4e00000-0000-4000-8000-000000002678",
        "name": "Маникюр классический",
        "category_slug": "manicure",
        "duration_minutes": raw,
        "requires_health_check": False,
        "resolved_requires_health_check": False,
        "price": "1500.00",
        "buffer_after_minutes": 0,
        "is_active": True,
    }
    if resolved is not _ABSENT:
        row["resolved_duration"] = resolved
    return row


def _duration(edge: dict[str, Any]) -> int | None:
    fake = QuotingFake()
    fake.edges = [edge]
    _price, duration = _adapter(fake).get_specialist_service_quote(staff_id=_SPEC, service_id=_SVC)
    return duration


class TestWhichFieldAnswers:
    def test_a_master_who_inherits_the_salons_duration_is_quoted(self):
        """The live case: no override, the catalog resolved 45."""
        assert _duration(_edge(raw=None, resolved=45)) == 45

    def test_the_resolved_field_wins_over_the_raw_one(self):
        """Two different numbers pin WHICH field is read. Today's catalog
        cannot send this pair (an override IS the resolved value) — the case
        is here so the choice of field is held by more than a null."""
        assert _duration(_edge(raw=30, resolved=45)) == 45

    def test_an_override_is_quoted_as_before(self):
        assert _duration(_edge(raw=30, resolved=30)) == 30

    def test_a_catalog_without_the_field_still_quotes_the_raw_one(self):
        """An older catalog sends no ``resolved_duration`` at all: the raw
        number is all there is, and it is used rather than dropped."""
        assert _duration(_edge(raw=60)) == 60

    @pytest.mark.parametrize("resolved", [None, 0, -5, "45", True, 45.5])
    def test_a_resolved_value_that_is_not_a_duration_is_unknown(self, resolved):
        """The field was sent, so it is the answer — even when the answer is
        «unknown». The raw number does not stand in for it."""
        assert _duration(_edge(raw=30, resolved=resolved)) is None


class TestThePreview:
    def test_the_inherited_duration_is_shown_and_snapshotted(self, tenant, bot_user):
        fake = QuotingFake()
        fake.edges = [_edge(raw=None, resolved=45)]

        result = _preview(fake, tenant, bot_user)

        assert "• Длительность: 45 мин" in result.text
        assert _payload_of(result)["quoted_duration_minutes"] == 45

    def test_an_unknown_duration_is_neither_shown_nor_sent(self, tenant, bot_user):
        fake = QuotingFake()
        fake.edges = [_edge(raw=None, resolved=None)]

        result = _preview(fake, tenant, bot_user)

        # Presence first: the preview and the snapshot were built from this
        # edge — its price is in both — so «no duration» is said of real data.
        payload = _payload_of(result)
        assert "• Цена: 1 500 ₽" in result.text
        assert payload["quoted_price"] == "1500.00"
        assert "Длительность" not in result.text
        assert "quoted_duration_minutes" not in payload
