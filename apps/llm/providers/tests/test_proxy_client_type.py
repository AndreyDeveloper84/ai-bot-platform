"""The proxied HTTP client must be of the type its own SDK validates (DRF-1437).

### The defect

Both providers built the proxied client the same way::

    import httpx
    kwargs["http_client"] = httpx.AsyncClient(proxy=self._proxy, timeout=timeout)

That hard-codes one HTTP stack for two SDKs that no longer share one.
``anthropic >= 1.0`` depends on ``httpx2`` — the httpx 2.x line, published
under a NEW distribution name so it can be installed alongside httpx
0.x/1.x — and its base client rejects anything else outright::

    if http_client is not None and not isinstance(http_client, httpx2.AsyncClient):
        raise TypeError(
            "Invalid `http_client` argument; Expected an instance of "
            "`httpx2.AsyncClient` but got <class 'httpx.AsyncClient'>"
        )

``openai`` still depends on ``httpx<1`` and validates against
``httpx.AsyncClient``. So on the pilot, where ``anthropic==1.2.0`` and
``openai==1.109.1`` are installed side by side, ONE line of shared code
cannot satisfy both: with a proxy configured, the Anthropic client failed
to construct at all and every reply the bot owed a user was lost.

### Why the existing proxy tests did not catch it

They are not shallow — ``test_anthropic_settings_plumbing.py`` really does
call ``provider._get_client()`` and inspect the injected transport. They
stayed green because CI and the pilot install DIFFERENT anthropic majors:
CI resolves ``uv.lock`` (``anthropic==0.101.0``, httpx-based, where the
old line is correct), while the pilot image runs ``pip install -e .``
against an unpinned ``anthropic>=0.40`` and resolved ``1.2.0``. The test
was right; the environment under it was not the environment that shipped.

### The claim these tests encode

    A proxied client is built from the SDK's OWN
    ``DefaultAsyncHttpxClient`` — never from a bare ``httpx.AsyncClient``
    named at this call site.

That is stronger than "it works on today's versions", and deliberately so.
Asserting only "the SDK accepted it" is green on any environment whose
anthropic still uses httpx, which is exactly the environment that let this
ship. Asserting the class PROVENANCE is version-independent: each SDK's
``DefaultAsyncHttpxClient`` subclasses whichever ``AsyncClient`` that SDK
was compiled against, so it is by construction the type that SDK's own
``isinstance`` check will accept — today, and after the next major bump
that moves one vendor's stack and not the other's.

### On the proxy address below

An ``*.invalid`` hostname with no credentials in the URL. Per the
secrets-gate incident recorded in ``test_anthropic_settings_plumbing.py``,
a test proxy address carrying ``user:password@`` fails the
``detect-secrets`` gate. Never put credentials in a test URL, even a fake
one. No request is ever made here — only clients are constructed.
"""

from __future__ import annotations

from typing import Any

import pytest

from apps.llm.providers.anthropic_provider import AnthropicProvider
from apps.llm.providers.openai_provider import OpenAIProvider

# Non-credential placeholders. No vendor prefix, no entropy.
PLACEHOLDER_ANTHROPIC_KEY = "unit-test-placeholder-anthropic"
PLACEHOLDER_OPENAI_KEY = "unit-test-placeholder-openai"

# No userinfo component by design — see the module docstring.
PROXY_URL = "http://proxy-client-type.invalid:3128"


def _sdk_default_client_class(sdk: Any) -> type:
    """The SDK's own default async httpx client class.

    Read as a plain attribute, never through ``getattr(..., default)``: if
    a vendor renames it, this must raise by name rather than degrade into
    a test that quietly checks nothing.
    """
    return sdk.DefaultAsyncHttpxClient  # type: ignore[no-any-return]


def _sdk_async_client_base(sdk: Any) -> type:
    """The ``AsyncClient`` class the SDK's base client validates against.

    Derived from the SDK's own default client rather than from an ``httpx``
    import in this file — the whole point is that this test must not name
    an HTTP stack of its own.
    """
    default_cls = _sdk_default_client_class(sdk)
    for base in default_cls.__mro__[1:]:
        if base.__name__ == "AsyncClient":
            return base
    raise AssertionError(
        f"{default_cls!r} does not subclass any AsyncClient — the SDK's client "
        "layout changed and this test can no longer identify the expected type"
    )


class TestProxiedClientCarriesTheSdksOwnHttpxFlavor:
    """The provenance claim, asserted per vendor on a real constructed client."""

    def test_anthropic_proxied_client_comes_from_the_anthropic_sdk(self, settings: Any) -> None:
        import anthropic

        settings.ANTHROPIC_PROXY = PROXY_URL
        settings.OPENAI_PROXY = ""

        provider = AnthropicProvider(api_key=PLACEHOLDER_ANTHROPIC_KEY)
        client = provider._get_client()

        http_client = client._client
        assert http_client is not None, "the SDK client must carry an httpx client"
        assert isinstance(http_client, _sdk_default_client_class(anthropic)), (
            "the proxied client must be built from anthropic's own "
            f"DefaultAsyncHttpxClient, got {type(http_client).__module__}."
            f"{type(http_client).__name__}"
        )

    def test_openai_proxied_client_comes_from_the_openai_sdk(self, settings: Any) -> None:
        import openai

        settings.OPENAI_PROXY = PROXY_URL

        provider = OpenAIProvider(api_key=PLACEHOLDER_OPENAI_KEY)
        client = provider._get_client()

        http_client = client._client
        assert http_client is not None, "the SDK client must carry an httpx client"
        assert isinstance(http_client, _sdk_default_client_class(openai)), (
            "the proxied client must be built from openai's own "
            f"DefaultAsyncHttpxClient, got {type(http_client).__module__}."
            f"{type(http_client).__name__}"
        )

    def test_the_two_vendors_client_classes_are_independent(self) -> None:
        """The guard for the two tests above.

        If both SDKs happened to expose the same class, "came from
        anthropic" and "came from openai" would be one claim wearing two
        names, and a provider that used the wrong vendor's client would
        still pass. This asserts they are genuinely distinct objects, so
        each isinstance check above can actually fail.
        """
        import anthropic
        import openai

        anthropic_cls = _sdk_default_client_class(anthropic)
        openai_cls = _sdk_default_client_class(openai)

        assert anthropic_cls is not openai_cls, (
            "the two SDKs' default client classes must be distinct for the "
            "provenance assertions to discriminate anything"
        )
        # Both operands are asserted present and distinct above. These two
        # close the remaining hole: one class might still be a SUBCLASS of
        # the other, which would let `isinstance` accept the wrong vendor's
        # client while the identity check above still passed.
        # empty-assert-ok: no fetched data here — a structural claim about two classes.
        assert not issubclass(anthropic_cls, openai_cls)
        # empty-assert-ok: the other direction of the same claim, see above.
        assert not issubclass(openai_cls, anthropic_cls)


class TestTheSdkAcceptsTheProxiedClient:
    """The live symptom: on the pilot the Anthropic client did not construct.

    ``_get_client()`` runs the SDK's own ``isinstance`` validation, so these
    reproduce the production ``TypeError`` directly on whatever anthropic
    major the environment has installed.
    """

    def test_anthropic_client_constructs_with_a_proxy(self, settings: Any) -> None:
        import anthropic

        settings.ANTHROPIC_PROXY = PROXY_URL
        settings.OPENAI_PROXY = ""

        provider = AnthropicProvider(api_key=PLACEHOLDER_ANTHROPIC_KEY)
        try:
            client = provider._get_client()
        except TypeError as exc:  # pragma: no cover - the defect itself
            pytest.fail(
                f"the anthropic SDK rejected the proxied http_client this provider built: {exc}"
            )

        expected_base = _sdk_async_client_base(anthropic)
        http_client = client._client
        assert http_client is not None, "the SDK client must carry an httpx client"
        assert isinstance(http_client, expected_base), (
            f"expected {expected_base.__module__}.{expected_base.__name__}, got "
            f"{type(http_client).__module__}.{type(http_client).__name__}"
        )

    def test_openai_client_constructs_with_a_proxy(self, settings: Any) -> None:
        import openai

        settings.OPENAI_PROXY = PROXY_URL

        provider = OpenAIProvider(api_key=PLACEHOLDER_OPENAI_KEY)
        try:
            client = provider._get_client()
        except TypeError as exc:  # pragma: no cover - the defect itself
            pytest.fail(
                f"the openai SDK rejected the proxied http_client this provider built: {exc}"
            )

        expected_base = _sdk_async_client_base(openai)
        http_client = client._client
        assert http_client is not None, "the SDK client must carry an httpx client"
        assert isinstance(http_client, expected_base), (
            f"expected {expected_base.__module__}.{expected_base.__name__}, got "
            f"{type(http_client).__module__}.{type(http_client).__name__}"
        )


class TestTheProxyIsActuallyWired:
    """Type-correct is not the same as routed.

    A client of the right class that forgot the proxy would satisfy every
    assertion above while sending pilot traffic straight out of the
    container — the failure the type fix must not trade itself for.
    """

    def test_a_configured_proxy_mounts_exactly_one_transport(self, settings: Any) -> None:
        settings.ANTHROPIC_PROXY = PROXY_URL
        settings.OPENAI_PROXY = PROXY_URL

        anthropic_http = AnthropicProvider(api_key=PLACEHOLDER_ANTHROPIC_KEY)._get_client()._client
        openai_http = OpenAIProvider(api_key=PLACEHOLDER_OPENAI_KEY)._get_client()._client

        assert len(anthropic_http._mounts) == 1, (
            "a proxied anthropic client mounts exactly one proxy transport"
        )
        assert len(openai_http._mounts) == 1, (
            "a proxied openai client mounts exactly one proxy transport"
        )

    def test_no_proxy_configured_mounts_nothing(self, settings: Any) -> None:
        """The positive/negative pair for the test above.

        ``_mounts`` must be able to come back empty, or asserting it holds
        one entry proves nothing about the proxy. The presence assertions
        come first and on the same objects: a real client exists, of the
        SDK's own class, before emptiness is claimed.
        """
        import anthropic
        import openai

        settings.ANTHROPIC_PROXY = ""
        settings.OPENAI_PROXY = ""

        anthropic_http = AnthropicProvider(api_key=PLACEHOLDER_ANTHROPIC_KEY)._get_client()._client
        openai_http = OpenAIProvider(api_key=PLACEHOLDER_OPENAI_KEY)._get_client()._client

        # `_mounts` is read as a plain attribute here, before the isinstance
        # checks below: those narrow the value to `object` for mypy, which
        # then cannot see the field. Read early, asserted in order.
        anthropic_mounts = len(anthropic_http._mounts)
        openai_mounts = len(openai_http._mounts)

        assert anthropic_http is not None, "the SDK builds a client, proxy or not"
        assert openai_http is not None, "the SDK builds a client, proxy or not"
        assert isinstance(anthropic_http, _sdk_default_client_class(anthropic))
        assert isinstance(openai_http, _sdk_default_client_class(openai))
        # empty-assert-ok: emptiness IS the claim — no proxy configured means
        # no mounted proxy transport. The four assertions above prove there
        # were real, correctly-typed objects to inspect.
        assert anthropic_mounts == 0
        assert openai_mounts == 0
