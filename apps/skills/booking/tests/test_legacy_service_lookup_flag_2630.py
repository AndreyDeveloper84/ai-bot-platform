"""DRF-2630: the legacy int ``external_id`` service lookup belongs to flag OFF only.

Three places on the booking path look a service up by the legacy YClients
integer when the id is not a UUID. Two already ran that branch only with
``BOOKING_VIA_AYLA_REST`` off; ``_has_contraindication_text`` tried the int on
the Ayla path too (log-only, but a guess by int where a service is its UUID).

Measured 29.09: the pilot runs flag ON (``config.settings.staging``), and no
sync writes ``CatalogService.external_id`` any more (the upserter keys by
``ayla_service_id``). So on the pilot the legacy lookup must not execute.

The guard is a pair on each site, both flag values, one row that the legacy
key WOULD find:

* flag ON  → no query touches ``catalogservice.external_id`` (red if it does);
* flag OFF → the legacy query runs and finds the row — the guard must not
  forbid the OFF contour: its fate is the owner's decision, not this test's.
"""

from __future__ import annotations

import ast
import pathlib
import re
from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.catalog.models import CatalogService
from apps.skills.booking.skill import _has_contraindication_text, _service_requires_health_check
from apps.skills.booking.tools import calc_price
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db

LEGACY_ID = 2630


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="legacy-2630", name="Legacy 2630")


@pytest.fixture
def legacy_service(tenant: Tenant) -> CatalogService:
    """A row the legacy key would find — so «no query» means «not asked»,
    not «asked and missed»."""
    return CatalogService.all_tenants.create(
        tenant=tenant,
        external_id=LEGACY_ID,
        external_updated_at=timezone.now(),
        slug="peel",
        name="Пилинг",
        price_from=Decimal("2000.00"),
        duration_min=60,
        requires_health_check=True,
        contraindications="беременность",
    )


#: Any comparison on the legacy column — ``=``, ``IN``, ranges — not just
#: equality: a batched ``external_id__in`` would otherwise slip past.
_LEGACY_COLUMN = re.compile(r'"catalog_catalogservice"\."external_id"\s*(=|IN\b|>=|<=|>|<)', re.I)


def _legacy_queries(ctx: CaptureQueriesContext) -> list[str]:
    return [q["sql"] for q in ctx.captured_queries if _LEGACY_COLUMN.search(q["sql"])]


def _run(site: str, tenant: Tenant, flag_on: bool):
    service_id: int | str = str(LEGACY_ID) if flag_on else LEGACY_ID
    if site == "contraindication_text":
        return _has_contraindication_text(tenant, service_id)
    if site == "health_check":
        return _service_requires_health_check(tenant, service_id, None)
    return calc_price(
        tenant=tenant,
        client=None,
        arguments={"service_id": service_id},
        allowed_service_ids={service_id},
        service_lookup={service_id: "Пилинг"},
    )


SITES = ("contraindication_text", "health_check", "calc_price")


@pytest.mark.parametrize("site", SITES)
def test_flag_on_never_looks_a_service_up_by_the_legacy_int(settings, tenant, legacy_service, site):
    """Same site, same row, both flags, one capture each: the capture SEES the
    legacy query when the flag is off — so its absence with the flag on is
    «not asked», not a blind capture and not «asked and missed»."""
    settings.BOOKING_VIA_AYLA_REST = False
    with CaptureQueriesContext(connection) as off:
        _run(site, tenant, flag_on=False)
    settings.BOOKING_VIA_AYLA_REST = True
    with CaptureQueriesContext(connection) as on:
        _run(site, tenant, flag_on=True)
    seen = (bool(_legacy_queries(off)), _legacy_queries(on))
    assert seen == (True, []), seen


@pytest.mark.parametrize("site", SITES)
def test_flag_off_still_looks_it_up_and_finds_the_row(settings, tenant, legacy_service, site):
    """The other half: the OFF contour keeps its lookup — and it works."""
    settings.BOOKING_VIA_AYLA_REST = False
    with CaptureQueriesContext(connection) as ctx:
        result = _run(site, tenant, flag_on=False)
    assert _legacy_queries(ctx), f"{site}: the flag-OFF legacy lookup did not run"
    if site == "calc_price":
        assert result.price is not None and result.price.original_price == Decimal("2000.00")
    else:
        assert result is True  # found: contraindication text present / health check required


#: Every legacy-key lookup on the booking path, by (file, function) — the
#: three sites the pair above covers. Named, not counted: removing one and
#: adding another elsewhere must not stay green.
KNOWN_LEGACY_LOOKUPS = {
    ("skill.py", "_has_contraindication_text"),
    ("skill.py", "_service_requires_health_check"),
    ("tools.py", "calc_price"),
}


def _legacy_lookups(tree: ast.AST, name: str) -> set[tuple[str, str]]:
    """``filter``/``get``/``exclude``/``Q`` with any ``external_id…`` keyword
    (``external_id``, ``external_id__in``, ranges), by enclosing function."""
    found: set[tuple[str, str]] = set()

    def visit(node: ast.AST, fn: str) -> None:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            fn = node.name
        if isinstance(node, ast.Call):
            callee = getattr(node.func, "attr", getattr(node.func, "id", ""))
            if callee in {"filter", "get", "exclude", "Q"} and any(
                (k.arg or "").startswith("external_id") for k in node.keywords
            ):
                found.add((name, fn))
        for child in ast.iter_child_nodes(node):
            visit(child, fn)

    visit(tree, "<module>")
    return found


def test_the_census_of_legacy_lookups_on_the_booking_path():
    """Counted by construction over the whole booking package: a new
    legacy-key lookup is a site this guard does not cover — red until it is
    added to SITES above (and put behind the flag)."""
    root = pathlib.Path(__file__).resolve().parents[1]
    found: set[tuple[str, str]] = set()
    for path in sorted(root.glob("*.py")):
        found |= _legacy_lookups(ast.parse(path.read_text(encoding="utf-8")), path.name)
    assert found == KNOWN_LEGACY_LOOKUPS, sorted(found)


def test_the_census_sees_every_shape_it_claims():
    """Self-check with the census's own function on the shapes it claims."""
    tree = ast.parse(
        "def f(qs):\n"
        "    qs.filter(external_id=1)\n"
        "    qs.get(external_id__in=[1])\n"
        "    qs.exclude(external_id__gte=1)\n"
        "    Q(external_id=1)\n"
        "def g(qs):\n"
        "    qs.filter(ayla_service_id=1)\n"
    )
    assert _legacy_lookups(tree, "x.py") == {("x.py", "f")}


def test_the_sql_match_sees_every_comparison_it_claims():
    """Self-check of ``_LEGACY_COLUMN``: equality, a batched IN, a range —
    and nothing when the column is not compared."""
    col = '"catalog_catalogservice"."external_id"'
    assert _LEGACY_COLUMN.search(f"WHERE {col} = 2630")
    assert _LEGACY_COLUMN.search(f"WHERE {col} IN (1, 2)")
    assert _LEGACY_COLUMN.search(f"WHERE {col} >= 1")
    assert not _LEGACY_COLUMN.search('WHERE "catalog_catalogservice"."ayla_service_id" = 1')
