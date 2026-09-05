"""DRF-1492 — an answer that names a move carries the move.

The audit of the client surface on 04.09 found the same defect eleven times
over: the bot names an action in words («могу показать, какие салоны есть»,
«попробуйте выбрать другого», «спросите, что ещё есть в этом салоне») and
leaves the person to type it — while the very next message in the same dialog
carries chips. The owner's ruling (``OPEN_DECISIONS`` §25 п.3-4) makes the rule
explicit and lifts DRF-1348's «чипы не называют услуги» for these buttons: that
restriction is about the routing chips of the first screen.

What this module pins is the rule in BOTH directions, because only one of them
is a test:

* **positive** — each fixed reply carries a keyboard, and the tap lands where
  the sentence above it says it will. Asserting the button exists is half a
  test; a chip whose callback nothing answers is the failure the owner named
  as worse than no chip at all, so every callback here is executed.
* **negative, with a positive guard on the same data** (DRF-1411) — the three
  places that deliberately have NO keyboard still have none. Each of those
  assertions sits next to a call on the same renderer that DOES produce one,
  so «no buttons» can never come to mean «this renderer stopped drawing».

The measurement the ticket asks for lives in :class:`TestDeadEndInventory`: one
table naming every reply the audit counted, and what it must carry now.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from apps.orchestrator import handoff as handoff_mod
from apps.orchestrator.discovery import (
    CALLBACK_CATALOG_SALONS,
    CALLBACK_CATALOG_SERVICES_PREFIX,
    CATALOG_STALE_CARD_TEXT,
    execute_catalog_callback,
    render_no_match,
    render_no_salons,
    render_no_services,
    render_stale_card,
    show_salons,
)

pytestmark = pytest.mark.django_db(transaction=True)


def _ts() -> datetime:
    return datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)


def _salon(slug: str, name: str, *, city: str = ""):
    """A salon on the shelf: tenant + one bookable master."""
    from apps.catalog.models import CatalogMaster
    from apps.tenancy.models import Tenant

    tenant = Tenant.objects.create(slug=slug, name=name, city=city)
    CatalogMaster.all_tenants.create(
        tenant=tenant,
        external_updated_at=_ts(),
        name=f"Мастер {name}",
        is_active=True,
        invite_status=CatalogMaster.InviteStatus.ACCEPTED,
    )
    return tenant


def _service(tenant, name: str):
    from apps.catalog.models import CatalogService

    return CatalogService.all_tenants.create(
        tenant=tenant,
        external_updated_at=_ts(),
        slug=name[:40].lower().replace(" ", "-"),
        name=name,
        is_active=True,
        price_from=Decimal("1500"),
    )


def _buttons(reply) -> list[dict[str, str]]:
    if reply.action_data is None:
        return []
    return reply.action_data["attachments"][0]["payload"]["buttons"]


def _callbacks(reply) -> list[str]:
    return [b["callback"] for b in _buttons(reply)]


class TestDiscoveryRefusals:
    """The zero-result replies — the ones the concierge also renders through
    ``render_no_match`` (``apps.orchestrator.concierge._render_zero_result``),
    so fixing them here fixes the live path without touching that module."""

    def test_alternatives_become_chips_and_the_sentence_points_at_them(self) -> None:
        reply = render_no_match(
            city="Пенза",
            specialization="маникюр",
            alternatives=["Массаж спины", "Обёртывание"],
        )

        # The text names them AND tells the reader they are pressable. Before
        # this ticket it said «Показать мастеров по одной из них», which was
        # an offer nothing on screen could accept.
        assert "«Массаж спины»" in reply.text
        assert "Нажмите на услугу" in reply.text
        assert _callbacks(reply) == ["Массаж спины", "Обёртывание"]

    def test_refusal_without_alternatives_still_offers_the_salon_list(self) -> None:
        # No catalog suggestion to make: the honest remaining move is «вот
        # где мы есть», and that is a button rather than a request to type.
        reply = render_no_match(city="Пенза", specialization="маникюр")

        assert "маникюр" in reply.text
        assert _callbacks(reply) == [CALLBACK_CATALOG_SALONS]

    def test_repeated_refusal_keeps_the_way_out(self) -> None:
        reply = render_no_match(city="Пенза", specialization="маникюр", already_refused=True)

        assert "уже ответил" in reply.text
        assert _callbacks(reply) == [CALLBACK_CATALOG_SALONS]

    def test_city_only_refusal_answers_where_we_are(self) -> None:
        _salon("s-penza", "BodyFormula", city="Пенза")

        reply = render_no_match(city="Сочи")

        assert "Сочи" in reply.text
        assert _callbacks(reply) == [CALLBACK_CATALOG_SALONS]
        # The tap really answers «а где вы есть» — with the salon we seeded.
        tapped = execute_catalog_callback(_callbacks(reply)[0])
        assert tapped is not None
        assert "BodyFormula" in tapped.text

    def test_empty_city_salon_list_offers_the_unfiltered_one(self) -> None:
        _salon("s-penza", "BodyFormula", city="Пенза")

        reply = render_no_salons(city="Сочи")

        assert _callbacks(reply) == [CALLBACK_CATALOG_SALONS]
        tapped = execute_catalog_callback(_callbacks(reply)[0])
        assert tapped is not None
        assert "BodyFormula" in tapped.text

    def test_salon_list_without_a_city_draws_no_looping_button(self) -> None:
        """The paired negative — and its positive guard on the same renderer.

        A “Показать салоны” chip under «подключённых салонов пока нет» would
        redraw the sentence it hangs under. A button that loops is worse than
        a full stop, so this branch keeps none — while the city branch above
        (asserted here again, on the same call path) keeps one.
        """
        with_city = render_no_salons(city="Сочи")
        assert _callbacks(with_city) == [CALLBACK_CATALOG_SALONS]

        without_city = render_no_salons()
        assert "Подключённых салонов пока нет" in without_city.text
        assert without_city.action_data is None


class TestServiceRefusals:
    def test_unknown_salon_offers_the_salons_it_names(self) -> None:
        _salon("s1", "BodyFormula", city="Пенза")

        reply = render_no_services(salon="Люмина", salon_known=False)

        assert "Могу показать, какие салоны есть" in reply.text
        assert _callbacks(reply) == [CALLBACK_CATALOG_SALONS]
        tapped = execute_catalog_callback(_callbacks(reply)[0])
        assert tapped is not None
        assert "BodyFormula" in tapped.text

    def test_known_salon_missing_service_opens_that_salon_by_id(self) -> None:
        tenant = _salon("s1", "BodyFormula", city="Пенза")
        _service(tenant, "Массаж спины")

        reply = render_no_services(
            salon="BodyFormula",
            query="маникюр",
            salon_known=True,
            salon_tenant_id=tenant.id,
        )

        assert "Могу показать всё, что там делают" in reply.text
        assert _callbacks(reply) == [f"{CALLBACK_CATALOG_SERVICES_PREFIX}{tenant.id}"]
        tapped = execute_catalog_callback(_callbacks(reply)[0])
        assert tapped is not None
        assert "Массаж спины" in tapped.text

    def test_without_a_salon_id_the_promise_is_withdrawn_not_left_dangling(self) -> None:
        """The ticket's other rule: when there is nothing to hang the button
        on, the SENTENCE changes. «Могу показать всё, что там делают» with no
        way to show it is worse than not offering."""
        reply = render_no_services(salon="BodyFormula", query="маникюр", salon_known=True)

        assert "Могу показать всё, что там делают" not in reply.text
        assert _callbacks(reply) == [CALLBACK_CATALOG_SALONS]

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"salon": "BodyFormula", "salon_known": True},
            {"query": "маникюр", "city": "Пенза"},
            {"query": "маникюр"},
            {"city": "Пенза"},
            {},
        ],
    )
    def test_every_remaining_branch_carries_the_salon_chip(self, kwargs: dict) -> None:
        reply = render_no_services(**kwargs)

        assert _callbacks(reply) == [CALLBACK_CATALOG_SALONS]


class TestStaleCard:
    def test_stale_card_carries_the_move_it_names(self) -> None:
        _salon("s1", "BodyFormula", city="Пенза")

        reply = render_stale_card()

        assert reply.text == CATALOG_STALE_CARD_TEXT
        assert "Спросите" not in reply.text  # the old «type this sentence» form
        assert _callbacks(reply) == [CALLBACK_CATALOG_SALONS]
        tapped = execute_catalog_callback(_callbacks(reply)[0])
        assert tapped is not None
        assert "BodyFormula" in tapped.text


class TestHandoffDeadEnds:
    """``apps/orchestrator/handoff.py`` — the discovery → booking seam."""

    def test_unavailable_master_opens_the_salon_it_belongs_to(self) -> None:
        tenant = _salon("s1", "BodyFormula", city="Пенза")
        _service(tenant, "Массаж спины")

        reply = handoff_mod._unavailable_reply(tenant.id)

        assert "посмотрите, что ещё есть в этом салоне" in reply.text
        assert _callbacks(reply) == [f"{CALLBACK_CATALOG_SERVICES_PREFIX}{tenant.id}"]
        tapped = execute_catalog_callback(_callbacks(reply)[0])
        assert tapped is not None
        assert "Массаж спины" in tapped.text

    def test_unavailable_master_without_a_tenant_falls_back_to_the_salon_list(self) -> None:
        _salon("s1", "BodyFormula", city="Пенза")

        reply = handoff_mod._unavailable_reply()

        # The wording differs from the branch above because the offer does:
        # with T unresolved there is no «этот салон» to point at.
        assert "посмотрите наши салоны" in reply.text
        assert _callbacks(reply) == [CALLBACK_CATALOG_SALONS]

    def test_master_with_no_bookable_service_still_offers_the_salon(self) -> None:
        """``_ask_service_reply`` with empty rows.

        The pre-existing decision (no EMPTY keyboard, no header promising a
        list nobody can see) is untouched: there is still no SERVICE menu
        here. What is added is the one chip that is not a service menu.
        """
        tenant = _salon("s1", "BodyFormula", city="Пенза")
        _service(tenant, "Массаж спины")
        master_id = uuid.uuid4()

        reply = handoff_mod._ask_service_reply(
            tenant_id=tenant.id,
            master_id=master_id,
            master_name="Инна",
            rows=[],
            truncated=False,
            not_offered_name=None,
        )

        assert "Инна" in reply.text
        assert _callbacks(reply) == [f"{CALLBACK_CATALOG_SERVICES_PREFIX}{tenant.id}"]
        tapped = execute_catalog_callback(_callbacks(reply)[0])
        assert tapped is not None
        assert "Массаж спины" in tapped.text

    def test_master_with_services_still_gets_the_service_menu(self) -> None:
        """The positive guard for the branch above: the same function, on the
        same data shape, DOES draw the per-service keyboard — so the single
        chip in the empty case is a decision, not a renderer that broke."""
        tenant = _salon("s1", "BodyFormula", city="Пенза")
        service = _service(tenant, "Массаж спины")
        master_id = uuid.uuid4()

        reply = handoff_mod._ask_service_reply(
            tenant_id=tenant.id,
            master_id=master_id,
            master_name="Инна",
            rows=[(service.id, "Массаж спины")],
            truncated=False,
            not_offered_name=None,
        )

        assert _callbacks(reply) == [f"cb:discover:book:{tenant.id}:{master_id}:{service.id}"]

    def test_unresolved_booking_callback_offers_a_restart(self) -> None:
        _salon("s1", "BodyFormula", city="Пенза")

        reply = handoff_mod._chips(
            handoff_mod._UNRESOLVED_BOOKING_CALLBACK_REPLY,
            [handoff_mod.show_salons_button()],
        )

        assert "выберите заново" in reply.text.lower()
        assert _callbacks(reply) == [CALLBACK_CATALOG_SALONS]


class TestDeadEndInventory:
    """The measurement, as a test rather than as a number in a PR body.

    Every row is one reply the 04.09 audit counted as a dead end. ``True``
    means «must carry a keyboard now»; ``False`` means «deliberately still
    has none» and is the paired negative — each of those has a positive
    sibling above, on the same renderer.
    """

    def _seed(self):
        tenant = _salon("s-inv", "BodyFormula", city="Пенза")
        _service(tenant, "Массаж спины")
        return tenant

    def test_inventory(self) -> None:
        tenant = self._seed()
        cases: list[tuple[str, object, bool]] = [
            (
                "render_no_match / alternatives",
                render_no_match(
                    city="Пенза", specialization="маникюр", alternatives=["Массаж спины"]
                ),
                True,
            ),
            (
                "render_no_match / repeat",
                render_no_match(city="Пенза", specialization="маникюр", already_refused=True),
                True,
            ),
            (
                "render_no_match / service+city",
                render_no_match(city="Пенза", specialization="маникюр"),
                True,
            ),
            ("render_no_match / service", render_no_match(specialization="маникюр"), True),
            ("render_no_match / city", render_no_match(city="Сочи"), True),
            ("render_no_match / bare", render_no_match(), True),
            ("render_no_salons / city", render_no_salons(city="Сочи"), True),
            ("render_no_salons / bare", render_no_salons(), False),
            ("render_no_services / unknown salon", render_no_services(salon="Люмина"), True),
            (
                "render_no_services / salon+query",
                render_no_services(
                    salon="BodyFormula",
                    query="маникюр",
                    salon_known=True,
                    salon_tenant_id=tenant.id,
                ),
                True,
            ),
            (
                "render_no_services / salon",
                render_no_services(salon="BodyFormula", salon_known=True),
                True,
            ),
            (
                "render_no_services / query+city",
                render_no_services(query="маникюр", city="Пенза"),
                True,
            ),
            ("render_no_services / query", render_no_services(query="маникюр"), True),
            ("render_no_services / city", render_no_services(city="Пенза"), True),
            ("render_no_services / bare", render_no_services(), True),
            ("stale catalog card", render_stale_card(), True),
            ("handoff / unavailable in tenant", handoff_mod._unavailable_reply(tenant.id), True),
            ("handoff / unavailable, no tenant", handoff_mod._unavailable_reply(), True),
            (
                "handoff / master with no services",
                handoff_mod._ask_service_reply(
                    tenant_id=tenant.id,
                    master_id=uuid.uuid4(),
                    master_name="Инна",
                    rows=[],
                    truncated=False,
                    not_offered_name=None,
                ),
                True,
            ),
            # Present so the table cannot degenerate into «everything is
            # True»: a populated salon list is what the chips above lead to.
            ("show_salons / populated", show_salons(), True),
        ]

        # Presence first (DRF-1411): the table must actually hold both kinds,
        # or «all buttons present» would pass over an empty inventory.
        assert len(cases) == 20
        assert sum(1 for _, _, expected in cases if expected) == 19
        assert sum(1 for _, _, expected in cases if not expected) == 1

        without_buttons = [
            name for name, reply, expected in cases if expected and not _buttons(reply)
        ]
        assert without_buttons == []
        with_buttons = [name for name, reply, expected in cases if not expected and _buttons(reply)]
        assert with_buttons == []

        # And no reply anywhere carries an EMPTY inline_keyboard — the widget
        # that renders as a broken message rather than as a plain one.
        for name, reply, _expected in cases:
            if reply.action_data is not None:
                assert _buttons(reply), name
