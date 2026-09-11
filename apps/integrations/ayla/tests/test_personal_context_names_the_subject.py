"""The bot names the subject it acts for — CP-2 / DRF-1617, bot half.

Upstream (`beautygo_backend`) now authorises the subject in the path against
the one ``X-External-User-ID`` resolves to. This client was the only one of
ten Ayla clients that named nobody, and it happens to carry every
personal-data route: the 152-ФЗ export, the erasure, and the declared profile
that erasure empties.

### What these tests are for, and what the route-table contract already covers

``test_contract_route_table`` asserts that each route carries the header at
all. It does not — deliberately, see its own docstring — assert header
*values*. So it would stay green if every call sent the same hard-coded id, or
the id of whoever called last.

These tests assert the value: the header names **this** person, and it is
built from the same ``(channel, channel_user_id)`` pair that the path's
``ayla_user_id`` was resolved from. That is the property upstream actually
checks, and the one that turns into a 403 in production when it is wrong.

### Why the parameter is required rather than defaulted

A default would let a new call site forget and never find out — the request
would go out unnamed, upstream would allow it while the enforcement flag is
off, and the gap would surface months later as "some calls are anonymous". A
required keyword makes the omission a mypy error at the call site.
:class:`TestOmissionIsNotPossible` pins that, because "it's required" is a
property of a signature and signatures get edited.
"""

from __future__ import annotations

import inspect
from typing import Any

import httpx
import pytest

from apps.integrations.ayla.personal_context_client import (
    PersonalContextConfigError,
    PersonalContextHttpClient,
)

_UID = "11111111-2222-3333-4444-555555555555"
_EXT = "bot:telegram:987654"

#: Every public method, with the arguments it needs beyond the two ids. The
#: list is here rather than inline so a method added without a row shows up as
#: a failure in :meth:`TestEveryRouteNamesTheSubject.test_no_public_method_is_missing_from_this_list`
#: — otherwise route nine ships unnamed and this file still passes.
_CALLS: dict[str, dict[str, Any]] = {
    "get_context": {},
    "patch_context": {"updates": [{"field": "diet_type", "value": "vegan"}]},
    "get_ask_eligibility": {},
    "mark_asked": {"field": "diet_type"},
    "skip": {"field": "diet_type"},
    "get_personal_data_export": {},
    "delete_personal_data": {},
}


@pytest.fixture
def captured() -> list[httpx.Request]:
    return []


def _client(captured: list[httpx.Request]) -> PersonalContextHttpClient:
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"data": {}})

    return PersonalContextHttpClient(
        base_url="https://ayla.test",
        token="test-token",  # noqa: S106 — sentinel, not a secret
        retries=1,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


class TestEveryRouteNamesTheSubject:
    @pytest.mark.parametrize("method", sorted(_CALLS))
    def test_the_header_carries_this_persons_external_id(self, method, captured):
        client = _client(captured)

        getattr(client, method)(ayla_user_id=_UID, external_user_id=_EXT, **_CALLS[method])

        assert len(captured) == 1
        assert captured[0].headers.get("X-External-User-ID") == _EXT

    @pytest.mark.parametrize("method", sorted(_CALLS))
    def test_the_service_credential_still_goes_too(self, method, captured):
        """The header names the subject; it does not replace the token.

        Both halves are needed upstream: the bearer says which SERVICE called,
        the header says which SUBJECT it acts for. Dropping either one would
        make this change a regression rather than a tightening.
        """
        client = _client(captured)

        getattr(client, method)(ayla_user_id=_UID, external_user_id=_EXT, **_CALLS[method])

        assert captured[0].headers.get("Authorization") == "Bearer test-token"

    @pytest.mark.parametrize("method", sorted(_CALLS))
    def test_two_people_are_two_different_headers(self, method, captured):
        """Guards the failure this file exists to prevent.

        A client that hard-coded one id, or reused the previous caller's,
        would pass every "the header is present" check and quietly hand one
        person's identity to another person's request. Upstream would then
        authorise the wrong subject — or, worse while the enforcement flag is
        off, authorise nothing and leave no trace of the mix-up.
        """
        client = _client(captured)
        other_uid = "99999999-8888-7777-6666-555555555555"
        other_ext = "bot:max:111222"

        getattr(client, method)(ayla_user_id=_UID, external_user_id=_EXT, **_CALLS[method])
        getattr(client, method)(
            ayla_user_id=other_uid, external_user_id=other_ext, **_CALLS[method]
        )

        assert captured[0].headers["X-External-User-ID"] == _EXT
        assert captured[1].headers["X-External-User-ID"] == other_ext
        assert _UID in str(captured[0].url)
        assert other_uid in str(captured[1].url)

    def test_no_public_method_is_missing_from_this_list(self):
        """The guard on the guard.

        Without it, a new personal-data method could ship without a header and
        every test above would still be green — they only cover what the list
        names.
        """
        public = {
            name
            for name, member in inspect.getmembers(
                PersonalContextHttpClient, predicate=inspect.isfunction
            )
            if not name.startswith("_") and name not in {"close"}
        }
        assert public == set(_CALLS), (
            f"methods not covered here: {sorted(public - set(_CALLS))}; "
            f"listed but gone: {sorted(set(_CALLS) - public)}"
        )


class TestOmissionIsNotPossible:
    """ "It is required" is a property of a signature, and signatures get edited."""

    @pytest.mark.parametrize("method", sorted(_CALLS))
    def test_the_parameter_is_keyword_only_and_has_no_default(self, method):
        param = inspect.signature(getattr(PersonalContextHttpClient, method)).parameters.get(
            "external_user_id"
        )

        assert param is not None, f"{method} stopped naming the acting subject"
        assert param.kind is inspect.Parameter.KEYWORD_ONLY
        assert param.default is inspect.Parameter.empty, (
            f"{method} gained a default for external_user_id — a default is a "
            f"way for a new call site to forget and never find out"
        )

    @pytest.mark.parametrize("method", sorted(_CALLS))
    def test_calling_without_it_raises_rather_than_sending_anonymously(
        self,
        method,
        captured,
    ):
        client = _client(captured)

        with pytest.raises(TypeError):
            getattr(client, method)(ayla_user_id=_UID, **_CALLS[method])

        assert captured == [], "a request went out without naming its subject"

    @pytest.mark.parametrize("method", sorted(_CALLS))
    def test_an_empty_value_is_refused_here_not_upstream(self, method, captured):
        """An empty header is indistinguishable on the wire from no header.

        Upstream that is exactly the difference between "the caller named
        itself" and "the caller named nobody". Failing here keeps the guilty
        call site in the traceback instead of turning it into a 403 two
        services away.
        """
        client = _client(captured)

        with pytest.raises(PersonalContextConfigError):
            getattr(client, method)(ayla_user_id=_UID, external_user_id="", **_CALLS[method])

        assert captured == []
