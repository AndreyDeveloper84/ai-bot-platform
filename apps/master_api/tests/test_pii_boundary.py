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
   phone number anywhere in the raw bytes.
2. :class:`TestRouteCoverage` — every route in ``master_api.urls`` must be
   either swept or explicitly excluded **with a reason**. A new endpoint
   fails this test until its author classifies it.
3. :class:`TestSourceLiterals` — an AST scan of the whole ``master_api``
   package: no forbidden key may appear as a dict key, a keyword argument,
   or an annotated field name. Catches the field at authoring time, before
   anyone writes an endpoint test for it.
4. :class:`TestForbiddenKeyListParity` — the backend list and the Mini App
   list must not drift apart.

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
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from django.test import Client
from django.urls import reverse

from apps.booking.models import BookingRequest
from apps.catalog.models import CatalogMaster
from apps.conversations.models import Conversation, Message
from apps.identity.models import BotUser
from apps.master_api import urls as master_urls
from apps.master_api.pii import (
    FORBIDDEN_PII_KEYS,
    SELF_PII_EXEMPT_PATHS,
    find_forbidden_pii,
)
from apps.master_api.tests.conftest import init_data_header, make_master
from apps.tenancy.models import Tenant

MSK = ZoneInfo("Europe/Moscow")
REPO_ROOT = Path(__file__).resolve().parents[3]

#: The seeded customer's phone. Chosen so that no 4-digit window of it can
#: collide with a timestamp fragment ("2026", "0521", ...) in a response.
CUSTOMER_PHONE = "+79997775544"
CUSTOMER_DIGITS = "79997775544"

#: Pinned so the assertions below never depend on random UUID digits.
CUSTOMER_ID = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
CONVERSATION_ID = uuid.UUID("bbbbbbbb-cccc-dddd-eeee-ffffffffffff")

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
#: else. The date, the time down to the second, and every other digit in
#: the body remain under the assertion, in both passes.
_SUBSECOND_RE = re.compile(r"(?<=\d\d:\d\d:\d\d)\.\d{1,9}")


def _utc(dt_local: datetime) -> datetime:
    if dt_local.tzinfo is None:
        dt_local = dt_local.replace(tzinfo=MSK)
    return dt_local.astimezone(timezone.utc)


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
        digits = re.sub(r"\D", "", _SUBSECOND_RE.sub("", _UUID_RE.sub("", value)))
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
) -> Conversation:
    """Give every master read endpoint something about ``customer`` to return.

    Bookings in the past (roster + dashboard history) and in the future
    (schedule), plus an active conversation with messages (conversations
    list + detail).
    """

    for day in (18, 20, 26):
        BookingRequest.all_tenants.create(
            tenant=tenant,
            master=accepted_master,
            bot_user=customer,
            service_name="маникюр гель-лак",
            client_name=customer.client_name,
            client_phone=customer.phone,
            visit_at=_utc(datetime(2026, 5, day, 14, 0)),
            duration_min=60,
            status=BookingRequest.Status.CONFIRMED,
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
    Conversation.all_tenants.filter(pk=conv.pk).update(
        last_message_at=_utc(datetime(2026, 5, 21, 14, 0))
    )
    conv.refresh_from_db()
    return conv


# --- 1. live response sweep ----------------------------------------------

#: Master read endpoints fetched in full by the sweep below. Keyed by the
#: ``master_api`` URL name; the value builds the path.
SWEPT_READ_ROUTES: dict[str, object] = {
    "me": lambda: reverse("master_api:me"),
    "dashboard": lambda: reverse("master_api:dashboard"),
    "schedule": lambda: reverse("master_api:schedule"),
    "availability_pending": lambda: reverse("master_api:availability_pending"),
    "conversations_list": lambda: reverse("master_api:conversations_list"),
    "conversation_detail": lambda: reverse(
        "master_api:conversation_detail", args=[CONVERSATION_ID]
    ),
    "customers_list": lambda: reverse("master_api:customers_list"),
    "catalog_list": lambda: reverse("master_api:catalog_list"),
    "notification_prefs": lambda: reverse("master_api:notification_prefs"),
}


class TestLiveResponseSweep:
    """Fetch every master read endpoint and walk the whole body."""

    @pytest.mark.parametrize("route_name", sorted(SWEPT_READ_ROUTES))
    def test_no_forbidden_pii_key_anywhere_in_response(
        self,
        client: Client,
        seeded_surface: Conversation,
        route_name: str,
    ) -> None:
        url = SWEPT_READ_ROUTES[route_name]()  # type: ignore[operator]
        resp = client.get(url, HTTP_AUTHORIZATION=init_data_header("12345"))
        assert resp.status_code == 200, (route_name, resp.status_code, resp.content[:400])

        found = find_forbidden_pii(resp.json())
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
        url = SWEPT_READ_ROUTES[route_name]()  # type: ignore[operator]
        resp = client.get(url, HTTP_AUTHORIZATION=init_data_header("12345"))
        assert resp.status_code == 200, (route_name, resp.content[:400])
        _assert_no_customer_phone(resp.content.decode("utf-8"), where=route_name, body=resp.json())


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

    The formats below are the ones a person actually types. The bare
    ten digits matter in particular: ``apps/observability/pii_filter.py``
    requires a literal ``+7``/``8`` prefix and would sail past it.
    """

    TYPED_FORMS = [
        "+79997775544",
        "8 999 777 55 44",
        "+7 (999) 777-55-44",
        "9997775544",
        "8-999-777-55-44",
    ]

    @pytest.mark.parametrize("typed", TYPED_FORMS)
    def test_list_excerpt_carries_no_typed_number(
        self,
        client: Client,
        tenant: Tenant,
        seeded_surface: Conversation,
        typed: str,
    ) -> None:
        Message.all_tenants.create(
            tenant=tenant,
            conversation=seeded_surface,
            role=Message.Role.USER,
            content=f"Мой номер {typed}, перезвоните пожалуйста",
        )
        resp = client.get(
            reverse("master_api:conversations_list"),
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        assert resp.status_code == 200, resp.content[:400]
        _assert_no_customer_phone(
            resp.content.decode("utf-8"), where="conversations_list", body=resp.json()
        )

    @pytest.mark.parametrize("typed", TYPED_FORMS)
    def test_detail_message_body_carries_no_typed_number(
        self,
        client: Client,
        tenant: Tenant,
        seeded_surface: Conversation,
        typed: str,
    ) -> None:
        Message.all_tenants.create(
            tenant=tenant,
            conversation=seeded_surface,
            role=Message.Role.USER,
            content=f"Мой номер {typed}, перезвоните пожалуйста",
        )
        resp = client.get(
            reverse("master_api:conversation_detail", args=[CONVERSATION_ID]),
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        assert resp.status_code == 200, resp.content[:400]
        _assert_no_customer_phone(
            resp.content.decode("utf-8"), where="conversation_detail", body=resp.json()
        )

    def test_truncation_cannot_leave_a_four_digit_tail(
        self,
        client: Client,
        tenant: Tenant,
        seeded_surface: Conversation,
    ) -> None:
        """Redaction must run BEFORE the 100-char excerpt truncation.

        Truncating first and redacting the excerpt afterwards leaves the
        head of a sliced number in the excerpt — and a four-digit head is
        a phone under OD-W2-2 just as a four-digit tail is. The number is
        placed so the cut lands inside it.
        """

        from apps.master_api.services.conversations import EXCERPT_MAX_LEN

        padding = "а" * (EXCERPT_MAX_LEN - 12)
        Message.all_tenants.create(
            tenant=tenant,
            conversation=seeded_surface,
            role=Message.Role.USER,
            content=f"{padding} {CUSTOMER_PHONE} хвост",
        )
        resp = client.get(
            reverse("master_api:conversations_list"),
            HTTP_AUTHORIZATION=init_data_header("12345"),
        )
        assert resp.status_code == 200, resp.content[:400]
        _assert_no_customer_phone(
            resp.content.decode("utf-8"), where="conversations_list", body=resp.json()
        )

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
    "onboarding_claim": "swept by TestSelfPiiExemption (carries the one exemption)",
    "onboarding_accept": "POST mutation; response is {master_id, session_token, expires_at}",
    "onboarding_reject": "POST mutation; response carries no customer data",
    "onboarding_profile": "PATCH mutation; response is the master's own profile card",
    "profile": "alias of onboarding_profile — same view function",
    "availability_request": "POST mutation; response is the master's own request id/status",
    "conversation_send_message": "POST mutation; body is the master's own outbound message",
    "conversation_mark_read": "POST mutation; response is an ack",
    "conversation_promote": "POST mutation; response is the conversation tier",
    "conversation_draft_generate": "POST mutation; calls the LLM — covered by test_ai_drafts",
    "conversation_draft_send_as_me": "POST mutation; covered by test_ai_drafts",
    "conversation_draft_release_to_ai": "POST mutation; covered by test_ai_drafts",
    "billing_status": "proxy to the external billing service; shape is the provider's",
    "billing_card_setup": "proxy to the external billing service",
    "billing_pay_debt": "proxy to the external billing service",
    "payout_preview": "proxy to the external billing service",
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
