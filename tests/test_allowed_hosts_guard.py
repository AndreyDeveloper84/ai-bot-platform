"""ALLOWED_HOSTS actually rejects a foreign Host header (DRF-1391).

Both directions are asserted against the SAME host list and the SAME
endpoint. A one-sided test ("a foreign Host is refused") passes just as
happily against an application that refuses everything, which is the
failure mode a security regression test most needs to rule out.

The list under test is the pilot's: `api-dev.gobeauty.site` (public nginx
vhost), `localhost` (the web container's own healthcheck) and `127.0.0.1`
(the host-side deploy probe). Keeping the real list here means this file
also documents that dropping either loopback name breaks a probe, not
just that the wildcard is gone.
"""

from __future__ import annotations

import pytest

PILOT_ALLOWED_HOSTS = ["api-dev.gobeauty.site", "localhost", "127.0.0.1"]

# `/healthz/` is the right probe for a Host-header test: it takes no
# arguments, touches no backing service and answers 200 unconditionally,
# so a non-200 can only come from the host check itself.
HEALTHZ = "/healthz/"


@pytest.fixture
def pilot_hosts(settings):
    """Pin the pilot host list, with DEBUG off as on the real contour.

    `django.test.utils.setup_test_environment` appends 'testserver' to
    ALLOWED_HOSTS for the whole session; assigning the list here replaces
    it, so 'testserver' is NOT allowed inside these tests. That is
    deliberate — it is what lets the negative case below be a real
    rejection rather than a lucky one.
    """

    settings.DEBUG = False
    settings.ALLOWED_HOSTS = list(PILOT_ALLOWED_HOSTS)
    return settings


@pytest.mark.parametrize("host", PILOT_ALLOWED_HOSTS)
def test_configured_host_is_served(client, pilot_hosts, host):
    """Positive half: every name the contour actually uses answers 200."""

    response = client.get(HEALTHZ, HTTP_HOST=host)

    assert response.status_code == 200, (
        f"Host: {host} was refused. Every name in DJANGO_ALLOWED_HOSTS is a "
        "caller that exists today — nginx, the container healthcheck, or the "
        "deploy readiness probe. A 400 here is a red deploy on a healthy box."
    )


@pytest.mark.parametrize(
    "host",
    [
        "evil.example",
        "attacker.gobeauty.site.evil.example",
        "api-dev.gobeauty.site.evil.example",
    ],
)
def test_foreign_host_is_refused(client, pilot_hosts, host):
    """Negative half: anything else is 400 DisallowedHost.

    The last two cases are the ones a naive substring or suffix check
    would let through — an attacker-controlled domain that merely *ends*
    with, or *contains*, the real one.
    """

    response = client.get(HEALTHZ, HTTP_HOST=host)

    assert response.status_code == 400, (
        f"Host: {host} was accepted. With ALLOWED_HOSTS wide open Django "
        "trusts request.get_host(), and every absolute URL built from it "
        "(invite links, payment return URLs) can be pointed elsewhere."
    )


def test_wildcard_accepts_the_same_foreign_host(client, settings):
    """The state the pilot was in: '*' serves the host the test above refuses.

    Without this, `test_foreign_host_is_refused` proves only that the
    fixture list works — not that the wildcard was the thing that mattered.
    """

    settings.DEBUG = False
    settings.ALLOWED_HOSTS = ["*"]

    response = client.get(HEALTHZ, HTTP_HOST="evil.example")

    assert response.status_code == 200
