"""The master surface PII boundary — enforced as a class, not per-field.

DRF-1360 / owner decision OD-W2-2 (24.08), verbatim:

    «`phone_masked` с четырьмя последними цифрами надо убрать из
    мастерского ростера. Формулировка "телефон клиента исполнителю не
    передаётся ни в каком виде" не оставляет исключения "но последние
    четыре цифры — это просто идентификатор".»

### Why this file exists

The prohibition already existed before DRF-1360 — in the Mini App, as
``FORBIDDEN_PII_KEYS`` in ``apps/miniapp/src/lib/master-api.ts``, wired to
exactly one screen (the conversations list). Meanwhile the customer roster
on the next tab shipped ``phone_masked`` in **every row**. The rule was
right; its reach was one screen wide. That is how the hole survived.

So the check here is deliberately NOT "assert the roster has no
``phone_masked``". It is four layers, each covering a way the next field of
this class could arrive:

1. :class:`TestLiveResponseSweep` — every master **read** endpoint is
   fetched with a real customer seeded, and the whole JSON body is walked:
   no forbidden key at any depth, and not one digit of the customer's
   phone number anywhere in the raw bytes. Each route is first asserted
   to have answered with something — see «The positive guard» below.
2. :class:`TestRouteCoverage` — every route in ``master_api.urls`` must be
   either swept or explicitly excluded **with a reason**. A new endpoint
   fails this test until its author classifies it.
3. :class:`TestSourceLiterals` — an AST scan of the whole ``master_api``
   package: no forbidden key may appear as a dict key, a keyword argument,
   or an annotated field name. Catches the field at authoring time, before
   anyone writes an endpoint test for it.
4. :class:`TestForbiddenKeyListParity` — the backend list and the Mini App
   list must not drift apart.

### The positive guard (DRF-1406)

A negative assertion needs a positive guard on the same data. «There is
no customer phone here» is worth nothing next to an empty body, and an
empty body is a 200 like any other.

This file learned that the hard way. The fixture pinned its bookings to
literal May-2026 dates; those dates went past, ``GET /schedule`` started
answering with seven empty days, and the sweep over that route kept
passing for three months without looking at a single booking row. It was
green because there was nothing to see, which is indistinguishable in a
test report from green because there was nothing wrong.

So every entry in :data:`SWEPT_READ_ROUTES` now carries a ``witness`` —
a string that must appear in the response before any «no PII here»
assertion is allowed to run (:func:`_assert_body_is_worth_sweeping`).
Where the route can carry customer data the witness IS the customer's
rendered name, so the sweep is demonstrably walking the record it claims
is clean. And no date in this file is a literal any more: they are
offsets from the salon's today (:func:`_visit_at`).

### The one exemption

``POST /onboarding/claim`` echoes the **master's own** MAX phone, masked,
on the identity-confirm card, so they can see which account they are
claiming the invite with. That is the caller's own data, and DRF-1360
explicitly leaves it alone. It is allowed at exactly one dotted path —
:data:`apps.master_api.pii.SELF_PII_EXEMPT_PATHS` — and nowhere else.
"""

from __future__ import annotations

import ast
import json
import re
import uuid
from dataclasses import dataclass
from datetime import date as date_cls, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone as dj_timezone

from apps.booking.models import BookingRequest, RemoteBookingProxy
from apps.catalog.models import CatalogMaster, MasterService
from apps.conversations.models import Conversation, Message
from apps.identity.models import BotUser
from apps.master_api import urls as master_urls
from apps.master_api.pii import (
    FORBIDDEN_PII_KEYS,
    SELF_PII_EXEMPT_PATHS,
    find_forbidden_pii,
)
from apps.master_api.tests.conftest import init_data_header, make_master
from apps.scheduling.models import ScheduleChangeRequest, WorkingHours
from apps.tenancy.models import Tenant

MSK = ZoneInfo("Europe/Moscow")
REPO_ROOT = Path(__file__).resolve().parents[3]

#: The seeded customer's phone. Chosen so that no 4-digit window of it can
#: collide with a timestamp fragment ("2026", the month, the day) in a
#: response. The dates are no longer literals (DRF-1406), so the argument
#: that keeps this true is now stated once, structurally, beside
#: :data:`VISIT_LOCAL_TIME`.
CUSTOMER_PHONE = "+79997775544"
CUSTOMER_DIGITS = "79997775544"

#: Pinned so the assertions below never depend on random UUID digits.
CUSTOMER_ID = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
CONVERSATION_ID = uuid.UUID("bbbbbbbb-cccc-dddd-eeee-ffffffffffff")
#: The upcoming mirror row — what ``booking_detail`` (DRF-2154) renders.
UPCOMING_APPOINTMENT_ID = uuid.UUID("aaaaaaaa-2154-4000-8000-000000002154")

#: What the master surface renders for :data:`CUSTOMER_ID` — the first
#: name, which is as much of the customer as OD-W2-2 permits. Every route
#: below that *can* carry customer data must show this before the sweep
#: is allowed to conclude anything (see :func:`_fetch_swept`).
CUSTOMER_FIRST_NAME = "Ксения"

#: The master's own name — ``make_master``'s default.
MASTER_NAME = "Анна Петрова"

#: The one service the master performs, seeded by :func:`seeded_surface`.
SERVICE_NAME = "Маникюр гель-лак"

#: Time of day for every seeded visit, in the salon's timezone.
#:
#: A *time*, not a date — dates below are offsets from the salon's today
#: (DRF-1406). Pinning the clock time is not decoration: the second pass
#: of :func:`_assert_no_customer_phone` strips separators inside each
#: string value, so an ISO timestamp collapses to a digit run. Every
#: phone window in :func:`_phone_windows` is drawn from ``{4,5,7,9}``,
#: and in ``YYYY-MM-DDT14:00:00`` no four consecutive digits come from
#: that set: the year contributes ``2026``, the month's first digit is
#: ``0``/``1``, the day's first digit is ``0``–``3``, and the hour starts
#: with ``1``. Each of those breaks any run before it reaches four. A
#: wall-clock time would not — ``…T07:55:44`` collapses to ``…075544``
#: and carries ``5544``, a window of the customer's number.
VISIT_LOCAL_TIME = time(14, 0)

#: Completed visits, as «days before the salon's today». Three of them,
#: because the roster's ``total_visits`` and the «returning customer»
#: chips are computed from the count.
PAST_VISIT_DAYS_AGO = (10, 8, 2)

#: The upcoming visit, as «days after the salon's today». It must land
#: inside the schedule's default window
#: (:data:`apps.master_api.services.schedule.DEFAULT_RANGE_DAYS` = 7 days
#: from today) or ``GET /schedule`` goes back to returning seven empty
#: days — the exact defect DRF-1406 was filed for.
FUTURE_VISIT_DAYS_AHEAD = 1

_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)

#: The fractional-seconds part of an ISO-8601 timestamp — the
#: ``.963311`` in ``2026-08-25T08:34:22.963311+00:00``.
#:
#: Masked out for exactly the reason this file already gives for
#: :data:`_UUID_RE`: server-generated random digits "would otherwise
#: make this assertion flaky rather than meaningful".
#:
#: :data:`CUSTOMER_PHONE` was chosen so no 4-digit window of it collides
#: with a timestamp fragment — but that reasoning only covered the
#: *pinned* parts ("2026", "0521"). ``last_message_at`` and ``sent_at``
#: come from ``Message.created_at``, stamped by ``auto_now_add`` at
#: insert time, so their microseconds are six digits of wall clock that
#: differ every run. Four of the nine swept routes carry one. Each is
#: three fresh 4-digit windows played against eight phone windows, and
#: the separator-stripped second pass concatenates them with the
#: neighbouring date digits into a dozen more — so on a small percentage
#: of runs one window happens to equal a slice of the phone and this
#: file goes red on whatever PR was unlucky enough to be in CI. That is
#: what happened on PR #1289, a branch that touches no master surface.
#:
#: What this hides, stated plainly: sub-second clock noise, nothing
#: else. In the RAW pass the date, the time down to the second, and every
#: other digit in the body remain under the assertion.
_SUBSECOND_RE = re.compile(r"(?<=\d\d:\d\d:\d\d)\.\d{1,9}")

#: DRF-2095 — the collapsed (per-value) pass strips separators, and an ISO
#: timestamp collapses into a digit run that can contain a phone window:
#: ``2026-09-18T17:55:49+00:00`` → ``…17554900…`` → ``7554``. That is what
#: happened on PR #1835 shard 5 at 17:55:49 UTC. Measured: with the date
#: 2026-09-18, 64 clock seconds per day collide (every ``HH:55:44`` gives
#: ``5544``; ``05:54:40``, ``17:55:49`` …); other dates add their own. The
#: clock was a silent parameter of this test.
#:
#: So the collapsed pass masks whole timestamp-shaped tokens BY FORM —
#: ``YYYY-MM-DD``, optionally ``THH:MM[:SS[.ffffff]]`` and ``Z``/``±HH:MM``,
#: and a bare ``HH:MM:SS`` — before collapsing, the way it already masks
#: UUIDs. The form is strict (two-digit month/day/hour…): a phone dressed
#: as ``2026-99-97T77:55:44`` does not parse as a date and stays under the
#: assertion. What this hides, stated plainly: digits of the customer's
#: number that happen to be written INSIDE a well-formed timestamp — a
#: field a server renders from a datetime, not from a phone. The raw pass
#: still sees every timestamp digit, colons and all.
_ISO_DATETIME_RE = re.compile(
    r"(?<!\d)\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])"
    r"(?:[T ](?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d(?:\.\d{1,9})?)?"
    r"(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)?)?(?!\d)"
    r"|(?<!\d)(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?!\d)"
)


def _utc(dt_local: datetime) -> datetime:
    if dt_local.tzinfo is None:
        dt_local = dt_local.replace(tzinfo=MSK)
    return dt_local.astimezone(timezone.utc)


def _salon_today() -> date_cls:
    """Today in the salon's timezone — the date the endpoints work from.

    Not ``date.today()``: every master endpoint computes its day
    boundaries in the tenant's IANA timezone, so a fixture anchored on
    the runner's UTC date can seed a visit into yesterday for a salon
    that is three hours ahead.
    """

    return dj_timezone.now().astimezone(MSK).date()


def _visit_at(*, days_offset: int) -> datetime:
    """A visit ``days_offset`` days from the salon's today, at 14:00 local.

    Offsets, not literals. The May-2026 dates this replaced were in the
    future when written and silently drifted into the past; from then on
    ``GET /schedule`` answered with seven empty days and the sweep below
    walked an empty list, which cannot fail. See :data:`VISIT_LOCAL_TIME`
    for why the clock time stays pinned even though the date does not.
    """

    return _utc(datetime.combine(_salon_today() + timedelta(days=days_offset), VISIT_LOCAL_TIME))


def _phone_windows() -> list[str]:
    """Every 4-consecutive-digit window of the customer's number.

    Four digits is exactly what the removed ``phone_masked`` mask exposed
    ("+7 ... 14 67"), so that is the granularity we hunt for.
    """

    return [CUSTOMER_DIGITS[i : i + 4] for i in range(len(CUSTOMER_DIGITS) - 3)]


def _iter_string_values(payload: object) -> list[str]:
    """Every string value in a JSON-shaped payload, at any depth."""

    out: list[str] = []
    if isinstance(payload, dict):
        for value in payload.values():
            out.extend(_iter_string_values(value))
    elif isinstance(payload, list):
        for item in payload:
            out.extend(_iter_string_values(item))
    elif isinstance(payload, str):
        out.append(payload)
    return out


def _assert_no_customer_phone(raw: str, *, where: str, body: object = None) -> None:
    """No fragment of the customer's number survives anywhere in the response.

    Two passes, because the leak this test exists for was *formatted*:

    * **Raw bytes** — catches an unformatted number. Server-generated UUIDs
      are masked out first; they are random hex and would otherwise make
      this assertion flaky rather than meaningful.
    * **Per string value, separators stripped** — catches a mask. The field
      DRF-1360 removed rendered as ``+7 ••• ••• 14 67``: the four exposed
      digits are *split by a space*, so a contiguous scan of the raw body
      sails straight past them. Digits are collapsed within each individual
      string value rather than across the whole body, so unrelated numbers
      in neighbouring fields cannot concatenate into a false positive.
    """

    scrubbed = _SUBSECOND_RE.sub("", _UUID_RE.sub("<uuid>", raw))
    windows = _phone_windows()

    assert CUSTOMER_DIGITS not in scrubbed, f"{where}: full customer phone leaked"
    for window in windows:
        assert window not in scrubbed, (
            f"{where}: 4 digits of the customer's phone ({window}) leaked. "
            "The owner decision (DRF-1039 / OD-W2-2) leaves no exception for "
            "a partial number — four digits is a phone."
        )

    if body is None:
        body = json.loads(raw)
    for value in _iter_string_values(body):
        digits = re.sub(r"\D", "", _ISO_DATETIME_RE.sub("", _UUID_RE.sub("", value)))
        if not digits:
            continue
        for window in windows:
            assert window not in digits, (
                f"{where}: a formatted value ({value!r}) carries 4 digits of "
                f"the customer's phone ({window}). Masking the separators is "
                "not masking the number — «телефон клиента исполнителю не "
                "передаётся ни в каком виде», DRF-1039 / OD-W2-2."
            )


# --- fixtures -------------------------------------------------------------


@pytest.fixture
def customer(tenant: Tenant) -> BotUser:
    """A real customer of the master, with a phone on record.

    The phone must actually be stored — otherwise every assertion in this
    file would pass vacuously.
    """

    bu = BotUser.all_tenants.create(
        id=CUSTOMER_ID,
        tenant=tenant,
        channel="max",
        channel_user_id="pii-customer-1",
        display_name="Ксения Леонова",
        client_name="Ксения Леонова",
        chat_id="pii-customer-1",
        phone=CUSTOMER_PHONE,
    )
    bu.refresh_from_db()
    assert bu.phone == CUSTOMER_PHONE, "fixture would make the sweep vacuous"
    return bu


@pytest.fixture
def seeded_surface(
    tenant: Tenant,
    accepted_master: CatalogMaster,
    customer: BotUser,
    master_service: MasterService,
) -> Conversation:
    """Give every master read endpoint something about ``customer`` to return.

    Every route in :data:`SWEPT_READ_ROUTES` must come back populated —
    an endpoint with nothing to say answers 200 with an empty list, and
    a sweep over an empty list passes without looking at anything. What
    each route needs:

    * **roster / conversations** — completed :class:`BookingRequest` rows
      in the past, and an active conversation with messages.
    * **schedule / dashboard** — :class:`RemoteBookingProxy` rows. These
      readers moved to the Ayla mirror in DRF-1085 and no longer see
      ``BookingRequest`` at all, so the mirror is seeded alongside it:
      the same three past visits plus one upcoming, which is what puts a
      row in ``/schedule``'s window and a card in ``next_visit``.
    * **catalog / me** — a :class:`MasterService` mapping (the
      ``master_service`` fixture), so the services list is not empty.
    * **availability_pending** — the master's own pending schedule-change
      request.

    Dates are offsets from :func:`_salon_today`, never literals — see
    :func:`_visit_at`.
    """

    # Working hours for every weekday: without them the schedule renders
    # seven off-days and the booking below lands in a day with no frame.
    for weekday in range(7):
        WorkingHours.all_tenants.create(
            tenant=tenant,
            master=accepted_master,
            day_of_week=weekday,
            is_working=True,
            start_time=time(10, 0),
            end_time=time(19, 0),
        )

    # The mirror resolves service names through ``ayla_service_id``, the
    # catalog tab through ``MasterService``. One row, wearing both hats,
    # so the master's screen and the master's mirror agree on the name.
    service = master_service.service
    service.ayla_service_id = uuid.uuid4()
    service.save(update_fields=["ayla_service_id"])

    def _seed_mirror(
        visit_at: datetime, status: str, *, appointment_id: uuid.UUID | None = None
    ) -> None:
        RemoteBookingProxy.all_tenants.create(
            appointment_id=appointment_id or uuid.uuid4(),
            tenant=tenant,
            bot_user=customer,
            specialist_id=accepted_master.id,
            service_id=service.ayla_service_id,
            start_at=visit_at,
            end_at=visit_at + timedelta(minutes=60),
            status=status,
        )

    for days_ago in PAST_VISIT_DAYS_AGO:
        visit_at = _visit_at(days_offset=-days_ago)
        BookingRequest.all_tenants.create(
            tenant=tenant,
            master=accepted_master,
            bot_user=customer,
            service_name=SERVICE_NAME,
            client_name=customer.client_name,
            client_phone=customer.phone,
            visit_at=visit_at,
            duration_min=60,
            status=BookingRequest.Status.CONFIRMED,
            # DRF-1146: the roster counts completed visits only — stamp the
            # completion so these past rows are visits, not mere bookings.
            completed_at=visit_at,
        )
        _seed_mirror(visit_at, RemoteBookingProxy.Status.COMPLETED)

    # The upcoming visit — what ``/schedule`` and ``dashboard.next_visit``
    # exist to render, and the row the May-2026 literals used to withhold.
    _seed_mirror(
        _visit_at(days_offset=FUTURE_VISIT_DAYS_AHEAD),
        RemoteBookingProxy.Status.CONFIRMED,
        appointment_id=UPCOMING_APPOINTMENT_ID,
    )

    ScheduleChangeRequest.all_tenants.create(
        tenant=tenant,
        master=accepted_master,
        status=ScheduleChangeRequest.Status.PENDING,
        requested_start=_visit_at(days_offset=3),
        requested_end=_visit_at(days_offset=4),
        reason_class="vacation",
    )

    conv = Conversation.all_tenants.create(
        id=CONVERSATION_ID,
        tenant=tenant,
        bot_user=customer,
        is_active=True,
    )
    Message.all_tenants.create(
        tenant=tenant,
        conversation=conv,
        role=Message.Role.USER,
        content="Здравствуйте, хочу записаться",
    )
    Conversation.all_tenants.filter(pk=conv.pk).update(last_message_at=_visit_at(days_offset=-1))
    conv.refresh_from_db()
    return conv


# --- 1. live response sweep ----------------------------------------------


@dataclass(frozen=True)
class SweptRoute:
    """One master read endpoint, plus proof that it answered with something.

    ``witness`` is a string that MUST appear in the response body. It is
    not decoration — it is the positive half of the sweep.

    A negative assertion needs a positive guard on the same data. «There
    is no customer phone here» is only worth reading next to «there is
    something here»: an empty list satisfies every PII check ever
    written. Two hundred is not evidence of a body — an empty response
    is a 200 too. That is how DRF-1406 stayed green for three months
    while ``/schedule`` returned seven empty days.

    ``carries_customer_data`` records which half of the surface a route
    is on. Where it is ``True`` the witness is the *customer's own*
    rendered name, so the sweep is demonstrably walking the record it
    claims is clean; where it is ``False`` the route structurally cannot
    name a customer (the master's own profile, their prefs, their
    services), and the witness proves only that the payload is populated.
    """

    path: Callable[[], str]
    witness: str
    why: str
    carries_customer_data: bool


#: Master read endpoints fetched in full by the sweep below. Keyed by the
#: ``master_api`` URL name.
SWEPT_READ_ROUTES: dict[str, SweptRoute] = {
    "me": SweptRoute(
        lambda: reverse("master_api:me"),
        witness=SERVICE_NAME,
        why="the master's own service list",
        carries_customer_data=False,
    ),
    "onboarding_readiness": SweptRoute(
        lambda: reverse("master_api:onboarding_readiness"),
        witness="capability_not_built",
        why="the master's own setup checklist (DRF-1794): items, identity, sale_block",
        carries_customer_data=False,
    ),
    "dashboard": SweptRoute(
        lambda: reverse("master_api:dashboard"),
        witness=CUSTOMER_FIRST_NAME,
        why="next_visit / inbox_preview name the customer",
        carries_customer_data=True,
    ),
    "schedule": SweptRoute(
        lambda: reverse("master_api:schedule"),
        witness=CUSTOMER_FIRST_NAME,
        why="days[].bookings[].client_first_name",
        carries_customer_data=True,
    ),
    "availability_pending": SweptRoute(
        lambda: reverse("master_api:availability_pending"),
        witness="vacation",
        why="the master's own pending schedule-change request",
        carries_customer_data=False,
    ),
    "customers_list": SweptRoute(
        lambda: reverse("master_api:customers_list"),
        witness=CUSTOMER_FIRST_NAME,
        why=(
            "customers[].first_name; with ?q= the same route answers the booking-flow "
            "search (DRF-2154) — {id, name «Имя Ф.», last_visit_date, named} from a "
            "stubbed Ayla lookup, swept in test_master_bookings_2154"
        ),
        carries_customer_data=True,
    ),
    "booking_detail": SweptRoute(
        lambda: reverse("master_api:booking_detail", args=[UPCOMING_APPOINTMENT_ID]),
        witness=CUSTOMER_FIRST_NAME,
        why="client.name_initial on the master's own booking (DRF-2154 / DRF-1185)",
        carries_customer_data=True,
    ),
    "catalog_list": SweptRoute(
        lambda: reverse("master_api:catalog_list"),
        witness=SERVICE_NAME,
        why="services[].name",
        carries_customer_data=False,
    ),
    "notification_prefs": SweptRoute(
        lambda: reverse("master_api:notification_prefs"),
        witness="21:00",
        why="the master's own quiet-hours setting",
        carries_customer_data=False,
    ),
}


def _assert_body_is_worth_sweeping(route_name: str, body: object) -> None:
    """The response must actually carry the thing the sweep is about to clear.

    Checked against the decoded JSON rather than the raw bytes: Django
    serialises with ``ensure_ascii``, so a Cyrillic witness never appears
    literally in ``resp.content``.
    """

    route = SWEPT_READ_ROUTES[route_name]
    values = _iter_string_values(body)
    assert any(route.witness in value for value in values), (
        f"{route_name}: nothing to sweep. Expected {route.witness!r} "
        f"({route.why}) somewhere in the response, and it is not there — "
        "so every «no PII here» assertion below would pass on an empty "
        "body and prove nothing. Fix the fixture, not this assertion: "
        "that is precisely how DRF-1406 kept a PII sweep green over "
        "seven empty schedule days for three months. Body: "
        f"{json.dumps(body, ensure_ascii=False)[:400]}"
    )


def _fetch_swept(client: Client, route_name: str) -> tuple[str, object]:
    """GET a swept route, assert it is worth sweeping, return (raw, body)."""

    resp = client.get(
        SWEPT_READ_ROUTES[route_name].path(),
        HTTP_AUTHORIZATION=init_data_header("12345"),
    )
    assert resp.status_code == 200, (route_name, resp.status_code, resp.content[:400])
    body = resp.json()
    _assert_body_is_worth_sweeping(route_name, body)
    return resp.content.decode("utf-8"), body


class TestLiveResponseSweep:
    """Fetch every master read endpoint and walk the whole body."""

    @pytest.mark.parametrize("route_name", sorted(SWEPT_READ_ROUTES))
    def test_response_is_populated(
        self,
        client: Client,
        seeded_surface: Conversation,
        route_name: str,
    ) -> None:
        """The guard on its own, so a rotted fixture names itself.

        Without this the next stale fixture fails nothing — it just
        quietly narrows what the two sweeps below are looking at.
        """

        _fetch_swept(client, route_name)

    @pytest.mark.parametrize("route_name", sorted(SWEPT_READ_ROUTES))
    def test_no_forbidden_pii_key_anywhere_in_response(
        self,
        client: Client,
        seeded_surface: Conversation,
        route_name: str,
    ) -> None:
        _raw, body = _fetch_swept(client, route_name)

        found = find_forbidden_pii(body)
        assert found == [], (
            f"{route_name} leaked forbidden PII at {found}. Every key in "
            "apps.master_api.pii.FORBIDDEN_PII_KEYS is banned from every "
            "master response — see DRF-1360. If this is the master's own "
            "data, it needs an owner decision and an explicit entry in "
            "SELF_PII_EXEMPT_PATHS, not a quiet exception here."
        )

    @pytest.mark.parametrize("route_name", sorted(SWEPT_READ_ROUTES))
    def test_no_customer_phone_digits_in_response(
        self,
        client: Client,
        seeded_surface: Conversation,
        route_name: str,
    ) -> None:
        raw, body = _fetch_swept(client, route_name)
        _assert_no_customer_phone(raw, where=route_name, body=body)

    def test_the_customer_reaches_more_than_one_route(
        self,
        client: Client,
        seeded_surface: Conversation,
    ) -> None:
        """At least four routes must render the seeded customer.

        The per-route witness catches one route going quiet. This catches
        the fixture going quiet everywhere at once — the shape DRF-1406
        actually had, where the sweep still «covered nine routes» but
        only three of them had ever seen the customer.

        Порог был пять и опущен до четырёх ровно один раз, с причиной:
        DRF-1528 снял ``conversations_list`` и ``conversation_detail``
        вместе с перепиской мастер↔клиент, поэтому клиента показывают
        четыре маршрута — dashboard, schedule, booking_detail,
        customers_list. Порог здесь только против тихого усыхания; в обе
        стороны держит утверждение ``reached == expected`` ниже.
        """

        expected = {
            name for name, route in SWEPT_READ_ROUTES.items() if route.carries_customer_data
        }
        assert len(expected) >= 4, "the customer-facing half of the surface shrank — why?"

        reached = set()
        for name in sorted(SWEPT_READ_ROUTES):
            _raw, body = _fetch_swept(client, name)
            if any(CUSTOMER_FIRST_NAME in v for v in _iter_string_values(body)):
                reached.add(name)

        assert reached == expected, (
            "routes classified as carrying customer data but silent about "
            f"the seeded customer: {sorted(expected - reached)}; routes "
            f"carrying them unexpectedly: {sorted(reached - expected)}"
        )


class TestRosterRegression:
    """The specific hole DRF-1360 closed, pinned so it cannot reopen."""

    def test_roster_row_has_no_phone_field(
        self,
        client: Client,
        seeded_surface: Conversation,
    ) -> None:
        resp = client.get(
            reverse("master_api:customers_list"),
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        assert resp.status_code == 200
        rows = resp.json()["customers"]
        assert rows, "fixture must produce at least one roster row"
        for row in rows:
            assert set(row.keys()) == {
                "bot_user_id",
                "first_name",
                "last_visit_at",
                "last_visit_service_name",
                "total_visits",
                "is_returning",
                "at_risk",
            }, row

    def test_service_layer_never_selects_the_phone_column(
        self,
        seeded_surface: Conversation,
        accepted_master: CatalogMaster,
    ) -> None:
        """``BotUser.phone`` must not even be loaded into the process.

        The roster query uses ``.only()``; if someone re-adds ``phone`` to
        that column list the customer's number is back in memory, one
        attribute access away from a response. Assert on the SQL.
        """

        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        from apps.master_api.services.customers import list_master_customers

        with CaptureQueriesContext(connection) as ctx:
            out = list_master_customers(master=accepted_master)
        assert out, "fixture must produce at least one roster row"

        botuser_sql = [q["sql"] for q in ctx.captured_queries if "identity_botuser" in q["sql"]]
        assert botuser_sql, "expected the BotUser display-field query"
        for sql in botuser_sql:
            assert '"phone"' not in sql, (
                "the customer's phone column is being SELECTed by the roster "
                f"query — DRF-1360 says it must not be: {sql}"
            )


class TestCustomerTypedContactsAreRedacted:
    """The half of the boundary a field-level gate cannot see.

    Everything above checks the *shape* of the response: which keys
    exist, which columns are SELECTed. None of that fires when the
    number arrives inside a value the customer typed themselves —
    «мой номер +7 999 777 55 44, перезвоните». The payload then carries
    no forbidden key at all; it carries ``last_message_excerpt``, and
    the master reads the number off the screen exactly as if there had
    been a ``phone`` field.

    OD-W2-2 says «телефон клиента исполнителю не передаётся ни в каком
    виде». A number the customer typed is a form.

    **DRF-1528: половина этого класса снята вместе со своей поверхностью.**
    Ячейки, звавшие ``conversations_list`` / ``conversation_detail``,
    удалены — не потому, что правило ослабло, а потому, что маршрутов
    нет: свободный текст клиента мастеру больше не echo-ится ниоткуда, и
    тест, зовущий снятую ручку, проверял бы 410, а не редактуру. Сам
    запрет на месте и держится двумя уровнями выше: любой новый маршрут
    попадает в :data:`SWEPT_READ_ROUTES` или в :data:`NOT_SWEPT_ROUTES`
    под присмотром :class:`TestRouteCoverage`, а поле с запретным именем
    ловит :class:`TestSourceLiterals`.

    Что осталось здесь — утверждения о самой редактуре и о маске этого
    файла: они чистые (не ходят по HTTP) и переживают снятие поверхности.
    """

    #: Форматы, которыми номер пишет живой человек. Голые десять цифр важны
    #: отдельно: ``apps/observability/pii_filter.py`` требует буквального
    #: ``+7``/``8`` и мимо них проходит — а редактура обязана поймать.
    TYPED_FORMS = [
        "+79997775544",
        "8 999 777 55 44",
        "+7 (999) 777-55-44",
        "9997775544",
        "8-999-777-55-44",
    ]

    @pytest.mark.parametrize("typed", TYPED_FORMS)
    def test_every_typed_form_is_redacted(self, typed: str) -> None:
        """Утверждение пережило снятие поверхности — здесь оно о функции.

        Раньше те же пять форматов проверялись через ответ снятых ручек.
        Маршрутов нет, правило есть: текст клиента, где бы он ни всплыл
        дальше, проходит через :func:`redact_contacts`.
        """

        from apps.master_api.pii import PHONE_PLACEHOLDER, redact_contacts

        out = redact_contacts(f"Мой номер {typed}, перезвоните пожалуйста")
        assert PHONE_PLACEHOLDER in out, out
        # Положительная пара: сообщение осталось сообщением, а не пустотой.
        assert "перезвоните" in out, out
        _assert_no_customer_phone(out, where="redact_contacts", body={"text": out})

    # --- DRF-2095: the clock is not a parameter of this test any more ------

    #: Clock times whose HH:MM:SS collapse into a window of CUSTOMER_DIGITS —
    #: independent of the date (the window sits inside the six time digits).
    #: Measured by brute force over a day: 64 such seconds. 17:55:49 is the
    #: one that went red on PR #1835 shard 5.
    COLLIDING_TIMES = (
        time(17, 55, 49, 890712),  # ‥17554900‥ → 7554
        time(10, 55, 44, 123456),  # ‥10554400‥ → 5544
        time(5, 54, 40, 0),  # ‥0554400‥ → 5544
    )

    @staticmethod
    def _colliding_instant(clock: time) -> datetime:
        """Tomorrow at ``clock`` (UTC) — newest message on the thread, whatever today is."""

        tomorrow = (dj_timezone.now() + timedelta(days=1)).date()
        return datetime.combine(tomorrow, clock, tzinfo=timezone.utc)

    def test_a_real_leak_next_to_a_colliding_timestamp_is_still_caught(self) -> None:
        """The mask hides the timestamp, not the number beside it."""

        stamp = self._colliding_instant(self.COLLIDING_TIMES[0]).isoformat()
        clean = {"items": [{"last_message_at": stamp, "last_message_excerpt": "перезвоните"}]}
        _assert_no_customer_phone(
            json.dumps(clean), where="probe", body=clean
        )  # timestamp alone passes
        leaking = {
            "items": [
                {"last_message_at": stamp, "last_message_excerpt": "мой номер +7 (999) 777-55-44"}
            ]
        }
        with pytest.raises(AssertionError, match="4 digits of the customer's phone"):
            _assert_no_customer_phone(json.dumps(leaking), where="probe", body=leaking)

    def test_the_mask_is_by_form_not_by_punctuation(self) -> None:
        """A phone dressed as a timestamp does not parse as a date and stays under the assertion."""

        assert _ISO_DATETIME_RE.sub("", "2026-09-18T17:55:49.890712+00:00") == ""
        assert _ISO_DATETIME_RE.sub("", "17:55:49") == ""
        # Invalid month/day/hour: not a timestamp, not masked.
        dressed = "2026-99-97T77:55:44"
        assert _ISO_DATETIME_RE.sub("", dressed) == dressed
        body = {"note": dressed}
        with pytest.raises(AssertionError, match="4 digits of the customer's phone"):
            _assert_no_customer_phone(json.dumps(body), where="probe", body=body)

    def test_redaction_leaves_canonical_uuids_alone(self) -> None:
        """The UUID trap in ``apps/replay/redactor.py``, not repeated here.

        That module's ``OTP_RE`` is ``(?<![\\w\\d])\\d{4}(?![\\w\\d])``.
        Its boundaries are on ``\\w``; a UUID's separator is ``-``, which
        is not ``\\w``. So an all-digit 4-char group inside a canonical
        UUID satisfies both lookarounds and gets replaced — measured at
        ~43% of random UUIDs. ``redact_contacts`` consumes UUIDs as a
        unit before the phone branch can see them.
        """

        from apps.master_api.pii import redact_contacts

        mangled = [u for u in (str(uuid.uuid4()) for _ in range(2000)) if redact_contacts(u) != u]
        assert mangled == [], f"redaction bit into canonical UUIDs: {mangled[:5]}"

    def test_redaction_keeps_the_text_useful(self) -> None:
        """A master still needs times, dates and prices out of the chat.

        Redaction that eats every 4-digit run would make the excerpt
        useless and push masters to open something else to read the
        message — which is how a PII gate gets routed around.
        """

        from apps.master_api.pii import redact_contacts

        kept = "Запишите на 14:00 25.08.2026, услуга за 1500 рублей, код 1234"
        assert redact_contacts(kept) == kept


class TestSelfPiiExemption:
    """The master's own masked phone — allowed, and only where documented."""

    def test_onboarding_claim_returns_only_the_masters_own_phone(
        self,
        client: Client,
        tenant: Tenant,
        bot_user: BotUser,
        customer: BotUser,
    ) -> None:
        master = make_master(tenant)
        resp = client.post(
            reverse("master_api:onboarding_claim"),
            data=json.dumps({"token": str(master.invite_token)}),
            content_type="application/json",
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        assert resp.status_code == 200, resp.content

        body = resp.json()
        # The exemption is real — the field is there...
        assert "phone_masked" in body["max_user"]
        # ...at exactly the one documented path, and nowhere else.
        assert find_forbidden_pii(body) == []
        # ...and it is the CALLER's own phone, not a customer's.
        assert bot_user.phone != CUSTOMER_PHONE
        _assert_no_customer_phone(resp.content.decode("utf-8"), where="onboarding_claim", body=body)

    def test_exemption_list_stays_minimal(self) -> None:
        """Widening the exemption should require touching this assertion."""

        assert SELF_PII_EXEMPT_PATHS == frozenset({"max_user.phone_masked"})


# --- 2. route coverage ----------------------------------------------------

#: Routes not covered by the live sweep, each with the reason. A new route
#: must be added to :data:`SWEPT_READ_ROUTES` or to this map — forcing that
#: choice is the point of :class:`TestRouteCoverage`.
NOT_SWEPT_ROUTES: dict[str, str] = {
    "conversations_retired": (
        "DRF-1528: девять ручек переписки мастер↔клиент сняты (OD-7); маршрут "
        "отвечает постоянным 410 с причиной и не читает ни одной строки — "
        "подметать в нём нечего, см. test_conversations_retired"
    ),
    "onboarding_claim": "swept by TestSelfPiiExemption (carries the one exemption)",
    "onboarding_accept": "POST mutation; response is {master_id, session_token, expires_at}",
    "onboarding_reject": "POST mutation; response carries no customer data",
    "onboarding_profile": "PATCH mutation; response is the master's own profile card",
    "profile": "alias of onboarding_profile — same view function",
    "profile_card": (
        "GET proxy to the catalog's specialist profile (DRF-1814, part A): the master's "
        "own name/bio/photo, the catalog's limits, the badge flag and category chips — "
        "no customer record; shape pinned in test_profile_card_portfolio_1814"
    ),
    "profile_portfolio": (
        "GET/POST proxy to the catalog's portfolio of the signed master (DRF-1814): "
        "the master's own photos, no customer record — test_profile_card_portfolio_1814"
    ),
    "profile_portfolio_item": (
        "DELETE proxy to the catalog's portfolio item (DRF-1814); response is {count, limit}"
    ),
    "availability_request": "POST mutation; response is the master's own request id/status",
    "billing_status": "proxy to the external billing service; shape is the provider's",
    "billing_card_setup": "proxy to the external billing service",
    "billing_pay_debt": "proxy to the external billing service",
    "payout_preview": "proxy to the external billing service",
    "service_selection": (
        "GET/POST proxy to the catalog's service selection (DRF-1895): the master's own "
        "selected canon services and two server counters, no customer record — shape "
        "pinned in test_service_selection_1895"
    ),
    "service_offer": (
        "PUT proxy (DRF-1895): the master's own price/duration; response is the "
        "selection state — pinned in test_service_selection_1895"
    ),
    "selected_service": (
        "DELETE proxy (DRF-1895): removes the master's own selected service; response "
        "is the selection state — pinned in test_service_selection_1895"
    ),
    "publication_readiness": (
        "GET proxy to the catalog's publication readiness (DRF-1797): the master's own "
        "checklist codes, no customer record — pinned in test_publication_proxy_1797"
    ),
    "publication": (
        "POST proxy (DRF-1797): the master's own publish command; response is the "
        "catalog's command record — pinned in test_publication_proxy_1797"
    ),
    "publication_status": (
        "GET proxy (DRF-1797): the master's own profile status and readiness, no "
        "customer record — pinned in test_publication_proxy_1797"
    ),
    "service_directions": (
        "GET proxy to the catalog's canon directions (DRF-1799): roots of the global "
        "taxonomy, rows whitelisted to id/name/slug/icon/sort_order, no customer record — "
        "pinned in test_service_canon_proxy_1799"
    ),
    "service_templates": (
        "GET proxy to the catalog's templates of one direction (DRF-1799): canon service "
        "names and their category, whitelisted, no prices and no customer record — "
        "pinned in test_service_canon_proxy_1799"
    ),
    "assistant_history": (
        "the master's own transcript with Ayla — no customer record is "
        "rendered as fields; swept for forbidden keys and for the "
        "customer's phone digits by test_assistant_api.TestNoCustomerPii, "
        "which seeds a transcript first (a sweep here would run on an "
        "empty thread and prove nothing — DRF-1406)"
    ),
    "assistant_ask": "POST; calls the LLM — covered by test_assistant_api",
    "assistant_context": (
        "GET (DRF-2153): the master's own day context for the Ayla start screen — "
        "{today: {count, next: {client_name_initial «Анна П.», time, service_name, "
        "duration_min}}, chips}; the customer reaches it as first name + initial only, "
        "swept for the customer's phone in test_assistant_context_2153"
    ),
    "assistant_confirm": "POST mutation; body is a signed action token only",
    "working_hours": (
        "GET/PUT proxy to the catalog's working-hours route (DRF-1816): the "
        "response is the master's own weekly template + timezone, no customer "
        "record — swept with a stubbed client in test_working_hours_1816"
    ),
    # DRF-1802 (M10) — «своя услуга» мастера: прокси заявок о разрыве канона
    # в каталог. Отдают только собственные заявки мастера (название, цена,
    # длительность, статус) — клиентских данных там нет по построению; ответ
    # приходит из каталога, поэтому свип живым клиентом не собрать — покрыты
    # подменённым клиентом в test_canon_gap_requests_1802.
    "canon_gap_requests": (
        "proxy of the master's own canon-gap requests to the catalog; no customer "
        "record — covered with a stubbed client in test_canon_gap_requests_1802"
    ),
    "canon_gap_similar": (
        "canonical-template name hint from the catalog; no customer record — covered "
        "with a stubbed client in test_canon_gap_requests_1802"
    ),
    "canon_gap_request_detail": (
        "one own canon-gap request from the catalog; no customer record — covered "
        "with a stubbed client in test_canon_gap_requests_1802"
    ),
    # DRF-2154 (М-2) — записи мастера: создание и слоты идут через тот же
    # сервис, что салонная стойка (admin_api/services/booking); ответы —
    # исход §18 / окна времени, клиентских полей в них нет по построению.
    # Телефон нового гостя — только ВХОД (DRF-1184 «имя + телефон»), в ответ
    # не эхом — пришпилено в test_master_bookings_2154 (_assert_no_customer_phone).
    "create_booking": (
        "POST; the §18 outcome envelope {outcome, detail, appointment_id | reason_code, "
        "alternatives, idempotency_key} — a verdict about the action, never the customer; "
        "the new guest's phone is input only and is pinned as never echoed in "
        "test_master_bookings_2154"
    ),
    "booking_slots": (
        "GET proxy of Ayla's bookable starts for the master's own day and one service: "
        "{date, timezone, service_id, duration_min, slots[]} — no customer record; swept "
        "with a stubbed client in test_master_bookings_2154"
    ),
    "accepting_bookings": (
        "GET/PATCH proxy to the catalog's availability route (DRF-1845): the "
        "response is exactly {accepting_bookings: bool, status} of the master's "
        "own profile, no customer record — shape pinned in test_accepting_bookings_1845"
    ),
    # DRF-1811 (M19) — место работы соло-мастера: прокси в каталог. Ответ —
    # своё место (адрес мастера, подпись клиенту, статус, координаты) и зоны
    # выезда; клиентских данных нет по построению; ответ приходит из каталога,
    # поэтому свип живым клиентом не собрать — покрыты подменённым клиентом
    # в test_service_locations_1811 (shown_to_clients — слово каталога).
    "service_locations": (
        "GET/POST proxy to the catalog's service-locations route (DRF-1811): the "
        "master's own place and travel areas, no customer record — covered with a "
        "stubbed client in test_service_locations_1811"
    ),
    "service_location_detail": (
        "PATCH proxy to one own place/area in the catalog (DRF-1811); no customer "
        "record — covered with a stubbed client in test_service_locations_1811"
    ),
    "address_suggest": (
        "POST; address suggestions from the catalog's geocoder for the master's OWN "
        "address (q in the body, never logged); no customer record — covered with a "
        "stubbed client in test_service_locations_1811"
    ),
    "reviews": (
        "GET proxy to the catalog's own-reviews route (DRF-1857): lives in the "
        "catalog, so a live sweep cannot build it; the client is «Имя Ф.» / «Клиент» / "
        "null, rows whitelisted to id/rating/text/client_name/service_name/created_at — "
        "no phone, surname or username pinned with a stubbed client in test_master_reviews_1857"
    ),
}


class TestRouteCoverage:
    def test_every_master_route_is_classified(self) -> None:
        """A new master endpoint cannot ship unclassified.

        This is the test that would have caught DRF-1360 when the roster
        endpoint was added: the author would have had to say, in writing,
        whether the new surface is swept for customer PII.
        """

        declared = {p.name for p in master_urls.urlpatterns if p.name is not None}
        classified = set(SWEPT_READ_ROUTES) | set(NOT_SWEPT_ROUTES)
        assert len(declared) == len(master_urls.urlpatterns), (
            "an unnamed route in master_api.urls cannot be classified — give it "
            "a name so this test can hold it to the PII boundary."
        )

        unclassified = declared - classified
        assert not unclassified, (
            f"new master route(s) {sorted(unclassified)} are not classified for "
            "customer-PII exposure. Add each to SWEPT_READ_ROUTES (preferred — "
            "it then gets swept for every forbidden key) or to NOT_SWEPT_ROUTES "
            "with a reason."
        )

        stale = classified - declared
        assert not stale, f"classified route(s) {sorted(stale)} no longer exist in urls.py"

    def test_no_route_is_both_swept_and_excluded(self) -> None:
        overlap = set(SWEPT_READ_ROUTES) & set(NOT_SWEPT_ROUTES)
        assert not overlap, sorted(overlap)


# --- 3. source-literal scan ----------------------------------------------

#: The one place in the package allowed to name a forbidden key: the
#: onboarding identity-confirm card, which echoes the MASTER's own phone.
#: Path-scoped only — a customer-phone field added to the same file would
#: still be caught by TestLiveResponseSweep, which checks the dotted path.
SOURCE_LITERAL_ALLOWLIST: set[tuple[str, str]] = {
    ("apps/master_api/views.py", "phone_masked"),
}


def _master_api_sources() -> list[Path]:
    root = REPO_ROOT / "apps" / "master_api"
    return sorted(p for p in root.rglob("*.py") if "tests" not in p.parts and p.name != "pii.py")


class TestSourceLiterals:
    def test_no_forbidden_key_appears_as_a_field_name(self) -> None:
        """Catch the next field of this class at authoring time.

        The live sweep only sees endpoints someone remembered to seed data
        for. This one reads the whole package: dict keys, keyword argument
        names, and annotated (dataclass) field names.
        """

        offences: list[str] = []
        for path in _master_api_sources():
            rel = path.relative_to(REPO_ROOT).as_posix()
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                names: list[str] = []
                lineno = getattr(node, "lineno", 0)
                if isinstance(node, ast.Dict):
                    names = [
                        k.value
                        for k in node.keys
                        if isinstance(k, ast.Constant) and isinstance(k.value, str)
                    ]
                elif isinstance(node, ast.keyword) and node.arg is not None:
                    names = [node.arg]
                elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                    names = [node.target.id]
                for name in names:
                    if name in FORBIDDEN_PII_KEYS and (rel, name) not in SOURCE_LITERAL_ALLOWLIST:
                        offences.append(f"{rel}:{lineno} -> {name!r}")

        assert not offences, (
            "forbidden PII field name(s) in the master surface: "
            + ", ".join(sorted(set(offences)))
            + ". Every key in apps.master_api.pii.FORBIDDEN_PII_KEYS is banned "
            "from master-facing code — DRF-1360. A partial phone is a phone."
        )


# --- 4. backend / Mini App parity ----------------------------------------

_TS_LIST_RE = re.compile(
    r"export const FORBIDDEN_PII_KEYS\s*=\s*\[(?P<body>.*?)\]\s*as const;",
    re.DOTALL,
)


class TestForbiddenKeyListParity:
    def test_backend_and_miniapp_lists_match(self) -> None:
        """The two copies of the rule must not drift apart.

        The backend is the authority; the Mini App copy is
        defence-in-depth. Drift means one surface enforces a rule the other
        does not — which is exactly the shape of DRF-1360.
        """

        ts_path = REPO_ROOT / "apps" / "miniapp" / "src" / "lib" / "master-api.ts"
        if not ts_path.exists():  # pragma: no cover - Mini App moved/renamed
            pytest.skip(f"{ts_path} not found — backend list still enforced above")

        match = _TS_LIST_RE.search(ts_path.read_text(encoding="utf-8"))
        assert match is not None, (
            "FORBIDDEN_PII_KEYS not found in master-api.ts — if the Mini App "
            "dropped its client-side gate, drop this test with it."
        )
        ts_keys = set(re.findall(r'"([^"]+)"', match.group("body")))
        assert ts_keys == set(FORBIDDEN_PII_KEYS), (
            "FORBIDDEN_PII_KEYS drifted between apps/master_api/pii.py and "
            "apps/miniapp/src/lib/master-api.ts. Backend only: "
            f"{sorted(set(FORBIDDEN_PII_KEYS) - ts_keys)}; "
            f"Mini App only: {sorted(ts_keys - set(FORBIDDEN_PII_KEYS))}."
        )
