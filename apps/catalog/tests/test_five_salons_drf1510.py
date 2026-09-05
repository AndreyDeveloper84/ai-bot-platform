"""DRF-1510 — deployment rehearsal for the five unconnected salons.

### What this file is for

On 05.09.2026 the Ayla backend held eleven salons and the bot's ``Tenant``
table held six (one of them the ``global_bot`` sentinel, which owns no
catalog). ``sync_catalog_for_all_tenants`` iterates ``Tenant.objects.all()``
— a salon absent from that table is never fetched, so **171 services and all
14 manicures were invisible to clients**, not because the mirror was stale
but because nothing ever asked for them:

    olhovyy-dvor      Ольховый двор     62 services   4 manicures
    fevralskiy-svet   Февральский свет  43 services   —
    sorok-okon        Сорок окон        28 services   7 manicures
    pylca-i-lyon      Пыльца и лён      21 services   —
    mednyy-kovsh      Медный ковш       17 services   3 manicures

    connected already: formula-tela 58, mkt-mediclinic 24, mkt-afrodita 8,
    mkt-lumina 2, mkt-spatrium 2 = 94.   94 + 171 = 265 = the backend total.

This module is the rehearsal of that deploy against a test database, so the
production run is one command with a result already seen once. It exercises
the real path end to end — ``create_tenant`` → the beat fan-out → the
customer-facing discovery readers — with the Ayla feed stubbed.

### The two facts the rehearsal exists to pin down

**1. The slug is not what matches the backend; the primary key is.**
``CatalogSyncService._run_locked`` fetches all three mirrors with
``?tenant=str(tenant.id)``, and Ayla filters on its own Tenant UUID. A
tenant minted with the model default (``uuid.uuid4``) therefore mirrors
nothing — three fetches, zero rows, one ``catalog.sync.empty_fetch`` warning
that reads exactly like a salon which genuinely sells nothing. The slug only
names the row locally, for ``sync_catalog --tenant <slug>``. See
:class:`TestPrimaryKeyIsTheAylaContract`.

**2. A salon with services but no bookable masters does not reach clients.**
Not "appears and refuses at booking" — absent. ``discover_salons``,
``discover_services`` and ``discover_masters`` all gate on ``_bookable_qs``
(``is_active`` AND ``invite_status='accepted'``), and ``discover_services``
additionally restricts to ``tenant_id__in=bookable_tenant_ids``. So the
DRF-1164 rule ("no performer, no booking") is enforced one layer earlier
than the booking gate: such a salon is invisible, which is the safe failure,
not a dead end. See :class:`TestSalonWithoutBookableMasters`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

import pytest
from django.conf import settings
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.catalog.services.http_client import (
    CatalogSalonServiceDTO,
    CatalogSpecialistDTO,
    CatalogSpecialistServiceDTO,
    EdgeSnapshot,
)
from apps.catalog.tasks import sync_catalog_for_all_tenants
from apps.marketplace.discovery import (
    discover_masters,
    discover_masters_for_service,
    discover_salons,
    discover_services,
)
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# The contour, as measured on 05.09.2026
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Salon:
    """One salon's provisioning input plus its measured catalog size."""

    slug: str
    name: str
    city: str
    services: int
    manicures: int
    masters: int = 2

    #: The Ayla Tenant UUID. Deterministic here so a test can assert the
    #: fetch was made with THIS id; on the pilot it comes from the backend.
    ayla_id: uuid.UUID = field(default_factory=uuid.uuid4)


def _salon(slug: str, name: str, services: int, manicures: int, **kw) -> Salon:
    return Salon(
        slug=slug,
        name=name,
        city="Пенза",
        services=services,
        manicures=manicures,
        ayla_id=uuid.uuid5(uuid.NAMESPACE_URL, f"drf1510/{slug}"),
        **kw,
    )


#: The five that DRF-1510 connects.
NEW_SALONS: tuple[Salon, ...] = (
    _salon("olhovyy-dvor", "Ольховый двор", 62, 4),
    _salon("fevralskiy-svet", "Февральский свет", 43, 0),
    _salon("sorok-okon", "Сорок окон", 28, 7),
    _salon("pylca-i-lyon", "Пыльца и лён", 21, 0),
    _salon("mednyy-kovsh", "Медный ковш", 17, 3),
)

#: The five already in the bot. They are here for the PAIRED POSITIVE check:
#: connecting five salons must not cost the connected ones a single row.
CONNECTED_SALONS: tuple[Salon, ...] = (
    _salon("formula-tela", "Формула тела", 58, 0),
    _salon("mkt-mediclinic", "Медиклиник", 24, 0),
    _salon("mkt-afrodita", "Афродита", 8, 0),
    _salon("mkt-lumina", "Люмина", 2, 0),
    _salon("mkt-spatrium", "Спатриум", 2, 0),
)

TOTAL_NEW_SERVICES = 171
TOTAL_NEW_MANICURES = 14
TOTAL_CONNECTED_SERVICES = 94


def test_the_arithmetic_the_ticket_rests_on() -> None:
    """94 connected + 171 unconnected = 265, the backend's own total.

    A guard on the fixtures, not on the code: if someone edits a count above,
    the rehearsal stops describing the contour it claims to rehearse.
    """
    assert sum(s.services for s in NEW_SALONS) == TOTAL_NEW_SERVICES
    assert sum(s.manicures for s in NEW_SALONS) == TOTAL_NEW_MANICURES
    assert sum(s.services for s in CONNECTED_SALONS) == TOTAL_CONNECTED_SERVICES
    assert TOTAL_CONNECTED_SERVICES + TOTAL_NEW_SERVICES == 265


# ---------------------------------------------------------------------------
# Stubbed Ayla feed
# ---------------------------------------------------------------------------


def _ts(hour: int = 10) -> datetime:
    return datetime(2026, 9, 5, hour, 0, tzinfo=timezone.utc)


@dataclass
class _Feed:
    """One salon's three upstream surfaces, keyed by the Ayla tenant UUID."""

    services: list[CatalogSalonServiceDTO]
    specialists: list[CatalogSpecialistDTO]
    edges: list[CatalogSpecialistServiceDTO]


def _build_feed(salon: Salon, *, masters: int | None = None) -> _Feed:
    """Synthesise a feed of exactly ``salon.services`` rows.

    Names are generated, counts are the measured ones. The first
    ``salon.manicures`` rows are manicures so a global «маникюр» query has
    something real to find; the rest are massages.
    """
    tenant_str = str(salon.ayla_id)
    master_count = salon.masters if masters is None else masters

    services: list[CatalogSalonServiceDTO] = []
    for i in range(salon.services):
        kind = "Маникюр" if i < salon.manicures else "Массаж"
        services.append(
            CatalogSalonServiceDTO(
                ayla_service_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{salon.slug}/svc/{i}")),
                external_updated_at=_ts(),
                name=f"{kind} {salon.name} №{i + 1}",
                price_from=Decimal("1500"),
                duration_min=60,
            )
        )

    specialists = [
        CatalogSpecialistDTO(
            ayla_master_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{salon.slug}/master/{i}")),
            user_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{salon.slug}/user/{i}")),
            name=f"Мастер {salon.name} {i + 1}",
            external_updated_at=_ts(),
            tenant=tenant_str,
            raw={"address": f"{salon.city}, ул. {salon.name}, {i + 1}"},
        )
        for i in range(master_count)
    ]

    # Every master performs every service — the rehearsal is about visibility,
    # not about partial coverage (that lives in the marketplace suite).
    edges: list[CatalogSpecialistServiceDTO] = []
    for m_i, specialist in enumerate(specialists):
        for s_i, service in enumerate(services):
            edges.append(
                CatalogSpecialistServiceDTO(
                    ayla_specialist_service_id=str(
                        uuid.uuid5(uuid.NAMESPACE_URL, f"{salon.slug}/edge/{m_i}/{s_i}")
                    ),
                    salon_service=service.ayla_service_id,
                    specialist=specialist.ayla_master_id,
                    external_updated_at=_ts(),
                    tenant=tenant_str,
                    name=service.name,
                    resolved_requires_health_check=False,
                )
            )
    return _Feed(services=services, specialists=specialists, edges=edges)


class FakeAyla:
    """Stands in for ``CatalogHttpClient``, routing on the ``?tenant=`` UUID.

    The routing is the point. The real backend filters by the Ayla Tenant
    UUID and returns **nothing** for an id it does not know, so a fake that
    answered every id with the same rows would hide precisely the defect this
    module is about — a tenant whose local pk was never the upstream one.
    """

    def __init__(self, feeds: dict[str, _Feed]) -> None:
        self._feeds = feeds
        self.tenant_ids_seen: list[str] = []

    def _feed(self, tenant_id: str) -> _Feed:
        self.tenant_ids_seen.append(tenant_id)
        return self._feeds.get(tenant_id, _Feed([], [], []))

    def fetch_salon_services(self, *, tenant_id: str) -> list[CatalogSalonServiceDTO]:
        return self._feed(tenant_id).services

    def fetch_specialists(self, *, tenant_id: str) -> list[CatalogSpecialistDTO]:
        return self._feed(tenant_id).specialists

    def fetch_specialist_services(self, *, tenant_id: str) -> EdgeSnapshot:
        return EdgeSnapshot(edges=self._feed(tenant_id).edges, complete=True)

    def close(self) -> None: ...

    def __enter__(self) -> FakeAyla:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


@pytest.fixture(autouse=True)
def _cache_clear():
    # The sync holds a per-tenant lock in the cache; a leaked one turns the
    # next run into a silent skip.
    cache.clear()
    yield
    cache.clear()


@pytest.fixture(autouse=True)
def _clean_tenant_table(db):
    """Drop the migration-seeded tenants so counts are exact."""
    Tenant.all_objects.all().delete()


def _provision(salon: Salon, *, with_id: bool = True, with_city: bool = True) -> Tenant:
    """Run the real ``create_tenant`` command — no ORM shortcut.

    The deploy runs this command; so does the rehearsal. Constructing the
    ``Tenant`` directly here would test a code path the pilot never takes.
    """
    argv = ["--slug", salon.slug, "--name", salon.name]
    if with_id:
        argv += ["--id", str(salon.ayla_id)]
    if with_city:
        argv += ["--city", salon.city]
    call_command("create_tenant", *argv, stdout=StringIO())
    return Tenant.all_objects.get(slug=salon.slug)


def _run_beat(feeds: dict[str, _Feed]) -> tuple[dict[str, int], FakeAyla]:
    """Run the real beat fan-out against a stubbed Ayla."""
    fake = FakeAyla(feeds)
    with patch("apps.catalog.services.sync.CatalogHttpClient", return_value=fake):
        counters = sync_catalog_for_all_tenants()
    return counters, fake


def _feeds_for(*salons: Salon) -> dict[str, _Feed]:
    return {str(s.ayla_id): _build_feed(s) for s in salons}


# ---------------------------------------------------------------------------
# 1. Provision → sync → the catalog is there
# ---------------------------------------------------------------------------


class TestProvisionThenSync:
    def test_five_salons_land_with_every_service_and_master(self) -> None:
        for salon in NEW_SALONS:
            _provision(salon)

        counters, _ = _run_beat(_feeds_for(*NEW_SALONS))

        assert counters["tenants_run"] == 5
        assert counters["tenants_failed"] == 0

        for salon in NEW_SALONS:
            tenant = Tenant.all_objects.get(slug=salon.slug)
            mirrored = CatalogService.all_tenants.filter(tenant=tenant, is_active=True).count()
            assert mirrored == salon.services, f"{salon.slug}: {mirrored} != {salon.services}"
            assert CatalogMaster.all_tenants.filter(tenant=tenant).count() == salon.masters, (
                salon.slug
            )
            assert MasterService.all_tenants.filter(tenant=tenant).exists(), salon.slug

        total = CatalogService.all_tenants.filter(is_active=True).count()
        assert total == TOTAL_NEW_SERVICES

    def test_every_fetch_used_the_ayla_uuid_not_the_slug(self) -> None:
        """Three fetches per tenant, each carrying the id ``--id`` supplied."""
        for salon in NEW_SALONS:
            _provision(salon)

        _, fake = _run_beat(_feeds_for(*NEW_SALONS))

        expected = {str(s.ayla_id) for s in NEW_SALONS}
        assert set(fake.tenant_ids_seen) == expected
        for wanted in expected:
            assert fake.tenant_ids_seen.count(wanted) == 3

    def test_rerun_of_create_tenant_is_a_no_op(self) -> None:
        """The deploy must be safe to run twice — a half-finished run is
        finished by re-running the same five lines, not by hand-editing."""
        for salon in NEW_SALONS:
            _provision(salon)
        before = {t.id: (t.slug, t.name, t.city) for t in Tenant.all_objects.all()}

        for salon in NEW_SALONS:
            _provision(salon)

        after = {t.id: (t.slug, t.name, t.city) for t in Tenant.all_objects.all()}
        assert after == before
        assert Tenant.all_objects.count() == 5


# ---------------------------------------------------------------------------
# 2. Paired positive: the connected salons lose nothing
# ---------------------------------------------------------------------------


class TestConnectedSalonsAreUnharmed:
    def test_each_connected_salon_keeps_its_exact_service_count(self) -> None:
        """The half of the check that can fail for the opposite reason.

        Adding five tenants moves the fan-out from five iterations to ten and
        widens every cross-tenant discovery read. Asserting only that the new
        salons appeared would pass just as happily if the old ones had
        vanished — so the old counts are asserted BY NUMBER, per salon,
        before and after.
        """
        for salon in CONNECTED_SALONS:
            _provision(salon)
        _run_beat(_feeds_for(*CONNECTED_SALONS))

        baseline = {
            s.slug: CatalogService.all_tenants.filter(tenant__slug=s.slug, is_active=True).count()
            for s in CONNECTED_SALONS
        }
        assert baseline == {s.slug: s.services for s in CONNECTED_SALONS}

        # Now the DRF-1510 deploy, on top of a live contour.
        for salon in NEW_SALONS:
            _provision(salon)
        counters, _ = _run_beat(_feeds_for(*CONNECTED_SALONS, *NEW_SALONS))

        assert counters["tenants_run"] == 10
        assert counters["tenants_failed"] == 0
        after = {
            s.slug: CatalogService.all_tenants.filter(tenant__slug=s.slug, is_active=True).count()
            for s in CONNECTED_SALONS
        }
        assert after == baseline
        assert (
            CatalogService.all_tenants.filter(is_active=True).count()
            == TOTAL_CONNECTED_SERVICES + TOTAL_NEW_SERVICES
        )

    def test_connected_salons_still_answer_their_own_name(self) -> None:
        for salon in (*CONNECTED_SALONS, *NEW_SALONS):
            _provision(salon)
        _run_beat(_feeds_for(*CONNECTED_SALONS, *NEW_SALONS))

        cards = discover_services(salon="Формула тела", limit=200)
        assert cards, "the pilot salon fell off the surface"
        assert {c.salon_name for c in cards} == {"Формула тела"}


# ---------------------------------------------------------------------------
# 3. Global search — owner decision §23
# ---------------------------------------------------------------------------


class TestGlobalSearchReachesTheNewSalons:
    """DB-agnostic half — the cross-tenant reads that need no text matching."""

    def test_the_service_surface_spans_every_salon_not_one(self) -> None:
        """Owner decision §23: one search, all salons.

        ``discover_services`` with no ``salon``/``tenant_id`` returns rows
        from all ten tenants, and its ceiling is ``limit``, not "one salon's
        catalogue" — so the 171 newly connected rows are reachable by the
        same read that already served the 94.
        """
        for salon in (*CONNECTED_SALONS, *NEW_SALONS):
            _provision(salon)
        _run_beat(_feeds_for(*CONNECTED_SALONS, *NEW_SALONS))

        # The unscoped read is capped at ``_MAX_LIMIT`` (200) rather than at
        # one salon's catalogue — the ceiling is a page size, not a scope.
        cards = discover_services(limit=300)
        assert len(cards) == 200
        assert all(card.has_bookable_master for card in cards)

        # Every one of the ten salons is reachable through that same read,
        # each with its full catalogue. Scoping to one salon is the exception
        # a caller must ask for, not the default.
        for salon in (*CONNECTED_SALONS, *NEW_SALONS):
            scoped = discover_services(salon=salon.name, limit=200)
            assert {c.salon_name for c in scoped} == {salon.name}, salon.slug
            assert len(scoped) == salon.services, salon.slug

        total = sum(
            len(discover_services(salon=s.name, limit=200))
            for s in (*CONNECTED_SALONS, *NEW_SALONS)
        )
        assert total == TOTAL_CONNECTED_SERVICES + TOTAL_NEW_SERVICES

    def test_the_new_salons_were_absent_from_that_surface_before(self) -> None:
        """The negative half: the same read on the pre-DRF-1510 contour."""
        for salon in CONNECTED_SALONS:
            _provision(salon)
        _run_beat(_feeds_for(*CONNECTED_SALONS))

        cards = discover_services(limit=300)
        assert len(cards) == TOTAL_CONNECTED_SERVICES
        assert {c.salon_name for c in cards} & {s.name for s in NEW_SALONS} == set()

    def test_new_salons_appear_in_the_salon_directory(self) -> None:
        for salon in (*CONNECTED_SALONS, *NEW_SALONS):
            _provision(salon)
        _run_beat(_feeds_for(*CONNECTED_SALONS, *NEW_SALONS))

        names = {card.name for card in discover_salons(limit=50)}
        assert names == {s.name for s in (*CONNECTED_SALONS, *NEW_SALONS)}

    def test_city_filter_reaches_them_because_city_was_set(self) -> None:
        """``--city`` is what puts a salon into the city-scoped answer.

        ``_bookable_qs(city=...)`` filters on ``tenant__city``, and the query
        parser only recognises a city token that some bookable master's tenant
        actually carries (``_known_cities``). A blank city is not a cosmetic
        gap — it is absence from every city-scoped answer.
        """
        for salon in NEW_SALONS:
            _provision(salon)
        _run_beat(_feeds_for(*NEW_SALONS))

        assert Tenant.all_objects.filter(city="Пенза").count() == 5
        assert len(discover_services(city="Пенза", limit=300)) == TOTAL_NEW_SERVICES
        assert len(discover_masters(city="Пенза", limit=50)) == 10
        assert discover_services(city="Москва", limit=50) == []

    def test_blank_city_hides_the_salon_from_city_scoped_answers(self) -> None:
        """Provisioning WITHOUT ``--city`` — the state before this ticket."""
        for salon in NEW_SALONS:
            _provision(salon, with_city=False)
        _run_beat(_feeds_for(*NEW_SALONS))

        assert Tenant.all_objects.exclude(city="").count() == 0
        # The catalog is mirrored and globally findable...
        assert len(discover_services(limit=300)) == TOTAL_NEW_SERVICES
        # ...and yet «в Пензе» finds nobody, and «Пенза» is not even a city
        # the query parser will recognise.
        assert discover_services(city="Пенза", limit=50) == []
        assert discover_masters(city="Пенза", limit=50) == []


@pytest.mark.skipif(
    "postgresql" not in str(settings.DATABASES["default"]["ENGINE"]),
    reason="Cyrillic ILIKE folding requires Postgres; on SQLite «маникюр» "
    "cannot match the stored «Маникюр …» and the assertions below would "
    "fail (or pass vacuously). Same gate as apps/marketplace/tests.",
)
class TestManicureQueryAcrossSalons:
    """The 14 manicures, found by the words a client actually types."""

    def test_manicure_query_finds_all_fourteen_across_salons(self) -> None:
        for salon in (*CONNECTED_SALONS, *NEW_SALONS):
            _provision(salon)
        _run_beat(_feeds_for(*CONNECTED_SALONS, *NEW_SALONS))

        cards = discover_services(query="маникюр", limit=200)

        assert len(cards) == TOTAL_NEW_MANICURES
        by_salon: dict[str, int] = {}
        for card in cards:
            by_salon[card.salon_name] = by_salon.get(card.salon_name, 0) + 1
        assert by_salon == {"Ольховый двор": 4, "Сорок окон": 7, "Медный ковш": 3}
        # Every one of them is bookable — the chip may be rendered.
        assert all(card.has_bookable_master for card in cards)

    def test_manicure_was_invisible_before_the_five_were_connected(self) -> None:
        for salon in CONNECTED_SALONS:
            _provision(salon)
        _run_beat(_feeds_for(*CONNECTED_SALONS))

        assert discover_services(query="маникюр", limit=200) == []

    def test_city_token_in_the_query_routes_to_the_city_we_set(self) -> None:
        """«маникюр в пензе» — the shape the live contour actually gets."""
        for salon in NEW_SALONS:
            _provision(salon)
        _run_beat(_feeds_for(*NEW_SALONS))

        assert len(discover_services(query="маникюр в пензе", limit=50)) == (TOTAL_NEW_MANICURES)

    def test_a_salon_without_masters_contributes_no_manicures(self) -> None:
        for salon in NEW_SALONS:
            _provision(salon)
        _run_beat(
            {
                str(s.ayla_id): _build_feed(s, masters=0 if s.slug == "sorok-okon" else None)
                for s in NEW_SALONS
            }
        )

        cards = discover_services(query="маникюр", limit=200)
        assert len(cards) == TOTAL_NEW_MANICURES - 7
        assert "Сорок окон" not in {c.salon_name for c in cards}


# ---------------------------------------------------------------------------
# 4. The primary-key contract — the deploy's real failure mode
# ---------------------------------------------------------------------------


class TestPrimaryKeyIsTheAylaContract:
    def test_tenant_created_without_id_mirrors_nothing(self) -> None:
        """A correct slug and a wrong pk produce a salon that sells nothing.

        This is what ``create_tenant --slug ... --name ...`` alone would have
        shipped: five rows in the table, five clean sync runs, and 171
        services still invisible. Nothing in the counters says so — the run
        is not "failed", it is "ran, found nothing".
        """
        for salon in NEW_SALONS:
            _provision(salon, with_id=False)

        counters, fake = _run_beat(_feeds_for(*NEW_SALONS))

        assert counters["tenants_run"] == 5  # every run "succeeded"
        assert counters["tenants_failed"] == 0
        assert counters["total_created"] == 0  # and created nothing
        assert CatalogService.all_tenants.count() == 0
        assert discover_salons(limit=50) == []
        # The ids the fetches carried were not the ones Ayla knows.
        assert not set(fake.tenant_ids_seen) & {str(s.ayla_id) for s in NEW_SALONS}

    def test_id_flag_makes_the_pk_the_ayla_uuid(self) -> None:
        salon = NEW_SALONS[0]
        tenant = _provision(salon)
        assert tenant.id == salon.ayla_id

    def test_id_already_taken_by_another_slug_is_refused(self) -> None:
        """Two salons cannot share one Ayla tenant; the second would mirror
        the first one's catalog under its own name."""
        first = NEW_SALONS[0]
        _provision(first)
        clone = Salon(
            slug="typo-slug",
            name="Опечатка",
            city="Пенза",
            services=0,
            manicures=0,
            ayla_id=first.ayla_id,
        )
        with pytest.raises(CommandError, match="already used by tenant"):
            _provision(clone)
        assert not Tenant.all_objects.filter(slug="typo-slug").exists()

    def test_rerun_with_a_different_id_is_refused_not_silently_ignored(self) -> None:
        """The pk cannot be changed in place, and the command must say so.

        Before DRF-1510 the existing-row path was an unconditional "No-op",
        so a re-run correcting a wrong id printed success and changed
        nothing — the operator's evidence that the fix landed was a lie.
        """
        salon = NEW_SALONS[0]
        _provision(salon, with_id=False)
        with pytest.raises(CommandError, match="cannot be changed in place"):
            _provision(salon)


# ---------------------------------------------------------------------------
# 5. The salon with no bookable master (DRF-1164)
# ---------------------------------------------------------------------------


class TestSalonWithoutBookableMasters:
    """What actually happens to a salon whose masters feed is empty.

    Established by running the code, not by reading it: such a salon's
    services ARE mirrored, and it is absent from every customer-facing
    reader. It cannot become a dead end because it never becomes an offer.
    """

    @pytest.fixture
    def contour(self) -> Salon:
        """Four salons with masters, one (`sorok-okon`) without.

        One master per healthy salon, not two: this fixture is rebuilt for
        each test in the class and every master multiplies the edge rows
        (masters × services), which dominated the class's runtime. Nothing
        below counts masters — the plural case is covered once, in
        :class:`TestProvisionThenSync`.
        """
        orphan = next(s for s in NEW_SALONS if s.slug == "sorok-okon")
        for salon in NEW_SALONS:
            _provision(salon)
        feeds = {
            str(s.ayla_id): _build_feed(s, masters=0 if s.slug == orphan.slug else 1)
            for s in NEW_SALONS
        }
        _run_beat(feeds)
        return orphan

    def test_its_services_are_mirrored(self, contour: Salon) -> None:
        """The mirror is honest — the row exists. Visibility is a later gate."""
        assert (
            CatalogService.all_tenants.filter(tenant__slug=contour.slug, is_active=True).count()
            == contour.services
        )
        assert CatalogMaster.all_tenants.filter(tenant__slug=contour.slug).count() == 0

    def test_it_is_not_in_the_salon_directory(self, contour: Salon) -> None:
        names = {card.name for card in discover_salons(limit=50)}
        assert contour.name not in names
        assert len(names) == 4

    def test_its_services_are_not_offered_by_global_search(self, contour: Salon) -> None:
        """``discover_services`` restricts to tenants with a bookable master.

        All 28 of its services are in the mirror and none of them reaches the
        surface — the global answer drops from 171 rows to 143, which is the
        honest number for this contour.
        """
        cards = discover_services(limit=300)
        assert contour.name not in {c.salon_name for c in cards}
        assert len(cards) == TOTAL_NEW_SERVICES - contour.services

    def test_no_master_can_be_reached_through_its_services(self, contour: Salon) -> None:
        """The booking entry point returns nothing rather than a dead end.

        Even addressing one of its services by id — the strongest handle a
        client could hold — yields no master to book with, and the caller
        must say so instead of inventing one.
        """
        orphan_service = CatalogService.all_tenants.filter(tenant__slug=contour.slug).first()
        assert orphan_service is not None
        assert discover_masters_for_service(orphan_service.id) == []

    def test_it_never_reaches_a_client_as_a_choice(self, contour: Salon) -> None:
        """The chip-tap read, on a healthy salon and on this one.

        The presence half runs first and on the same call shape: if the
        fixture were starved of data, ``discover_salons(tenant_id=…)`` would
        fail there by name instead of letting the empty result below pass for
        the reason the test claims.
        """
        healthy = next(s for s in NEW_SALONS if s.slug != contour.slug)
        healthy_id = Tenant.all_objects.get(slug=healthy.slug).id
        assert discover_salons(tenant_id=healthy_id, limit=5), healthy.slug

        tenant = Tenant.all_objects.get(slug=contour.slug)
        assert discover_salons(tenant_id=tenant.id, limit=5) == []
        assert discover_masters(limit=50)
        assert all(card.salon_name != contour.name for card in discover_services(limit=200))

    def test_the_other_four_are_unaffected(self, contour: Salon) -> None:
        """One salon's missing roster must not cost the others their catalog."""
        expected = {s.name for s in NEW_SALONS if s.slug != contour.slug}
        assert {card.name for card in discover_salons(limit=50)} == expected
