"""The probe must measure the vendor the product actually calls (DRF-1631).

# The defect this guard is built around

``apps/llm/health.py`` named its vendor in an import::

    from apps.llm.providers.openai_provider import OpenAIProvider
    provider = OpenAIProvider(retry_policy=RetryPolicy(max_attempts=1))

while the serving path picks its vendor from settings, through
``apps.llm.router``. The pilot has run ``LLM_PROVIDER=anthropic`` since
DRF-1443. So on 10.09.2026 the owner's panel read::

    🔴 LLM недоступна
    Неудачных проверок подряд: 2018
    Ошибка: LLMVendorCreditsExhausted
    Детали: openai.complete: vendor credits exhausted: 429 no credits remaining

— true about OpenAI's wallet, and about nothing the bot does. Two hours
of logs held zero Anthropic errors and zero emergency replies. The
alarm was furniture. And the reverse case is the one that costs money:
had Anthropic failed, this probe would have stayed **green**, because it
was asking somebody else.

# Why the guard is shaped this way

Three candidate shapes were on the table.

* *Assert the probe builds ``AnthropicProvider``.* Rejected — it pins
  today's setting into a test. Flip ``LLM_PROVIDER`` back to openai and
  the test goes red over a correct system, so somebody deletes it.
* *Assert the two agree on the default settings.* Rejected — that is
  one sample, and the one sample they happened to agree on before
  DRF-1443 is exactly how the bug survived three weeks.
* **Parametrise over every vendor in the registry, drive
  ``LLM_PROVIDER`` to each in turn, and compare what the probe builds
  against what ``get_provider`` serves.** Chosen. It has no opinion
  about which vendor is right; it has an opinion about the two answers
  being the same one. A vendor added to ``_PROVIDER_REGISTRY``
  tomorrow is covered by this file the moment it lands, with no edit
  here.

Two independent readings of "the same", because either alone can be
satisfied by an accident:

1. **structural** — the class the probe instantiates is the class the
   serving path ends up calling (:func:`test_probe_builds_the_class_the_router_serves`);
2. **behavioural** — a probe run really lands in that vendor's
   ``complete`` (:func:`test_probe_call_lands_on_the_resolved_vendor`).
   A class comparison would still pass if the probe built the right
   object and then called something else.

Plus a source-level guard (:func:`test_health_module_names_no_vendor_module`)
that fails if anybody reintroduces a concrete-provider import into
``apps/llm/health.py`` — the literal shape of the original defect.

# Proving the guard bites

Recorded so the next reader does not have to take it on faith. With
``apps/llm/health.py`` reverted to the hard-coded ``OpenAIProvider``
and everything else as it is on this branch, this file reports
``2 failed`` in the ``anthropic`` parameters and passes the ``openai``
ones — which is the discrimination we want: red exactly when the two
paths disagree, green when they agree. Numbers in the PR body.
"""

from __future__ import annotations

import pytest

from apps.llm import health
from apps.llm.router import (
    build_provider,
    configured_vendor_names,
    get_router,
    provider_class,
    registered_provider_names,
    reset_router_cache,
)

pytestmark = pytest.mark.django_db

# Every vendor the registry knows. Not a literal list: adding a
# ``ProviderSpec`` row must extend this guard automatically, or the next
# vendor arrives unwatched exactly as Anthropic did.
VENDORS = registered_provider_names()


@pytest.fixture(autouse=True)
def _both_vendors_configured(settings):
    """Keys for every registered vendor, and a clean router cache.

    Both keys are set because the point of comparison is the RESOLUTION,
    not key configuration — a vendor whose constructor refuses for want
    of a key would fail the comparison for the wrong reason.
    """

    settings.OPENAI_API_KEY = "sk-test-not-a-real-key"  # pragma: allowlist secret
    settings.ANTHROPIC_API_KEY = "sk-ant-test-not-a-real-key"  # pragma: allowlist secret
    settings.OPENAI_PROXY = ""
    settings.ANTHROPIC_PROXY = ""
    settings.SKILL_LLM_PROVIDER = {}
    settings.LLM_QUOTA_FALLBACK_ENABLED = False
    reset_router_cache()
    yield
    reset_router_cache()


def _innermost(provider: object) -> object:
    """Strip the serving path's transparent wrappers.

    ``get_provider`` returns a ``PIITokenizingProvider`` around the
    concrete provider, sometimes inside a ``QuotaFallbackProvider``.
    Both are deliberately transparent — they copy ``.name`` and the
    model defaults through — so the object that actually talks to the
    vendor is the one at the bottom, and that is what the probe has to
    match.
    """

    seen = 0
    while seen < 10:
        inner = getattr(provider, "_wrapped", None) or getattr(provider, "_primary", None)
        if inner is None:
            return provider
        provider = inner
        seen += 1
    raise AssertionError("provider wrapper chain did not terminate")


@pytest.mark.parametrize("vendor", VENDORS)
def test_probe_builds_the_class_the_router_serves(settings, vendor):
    """Structural reading: same class, for every vendor in the registry."""

    settings.LLM_PROVIDER = vendor

    served = _innermost(get_router().get_provider())
    # Presence before absence: prove the serving path really produced a
    # vendor object of this deployment, so the comparison below has
    # something to compare.
    assert served.name == vendor

    probed_name, source = health.probe_target()
    probed = health.build_probe_provider(probed_name)

    assert probed_name == served.name
    assert type(probed) is type(served)
    # The probe has no tenant and no skill, so tier 3 is the honest
    # answer and the audit label must say so rather than imply more.
    assert source == "org_default"


@pytest.mark.parametrize("vendor", VENDORS)
def test_probe_call_lands_on_the_resolved_vendor(settings, monkeypatch, vendor):
    """Behavioural reading: the request really reaches that vendor.

    Matching classes is not the same as calling one.

    ``complete`` is patched on EVERY registered vendor class, not only
    the expected one, for two reasons. It turns the assertion into a
    counter — "one call, and it landed here, and nowhere else" — which a
    single-class patch cannot express: with only the expected class
    stubbed, a probe that called the wrong vendor would reach the real
    SDK, and the test would go red for the right reason by accident,
    over the network, after a 401. (Verified: under a deliberate
    divergence this test issued a live ``POST api.openai.com`` before
    the patch was widened.) A monitor's test must not need egress to
    tell the truth.
    """

    settings.LLM_PROVIDER = vendor

    calls: list[tuple[str, str]] = []

    def _stub(name: str):
        async def _complete(self, messages, **kwargs):
            calls.append((name, "complete"))
            return object()

        async def _aclose(self):
            calls.append((name, "aclose"))

        return _complete, _aclose

    for known in VENDORS:
        cls = provider_class(known)
        complete, aclose = _stub(known)
        monkeypatch.setattr(cls, "complete", complete, raising=False)
        monkeypatch.setattr(cls, "aclose", aclose, raising=False)

    result = health.run_probe_sync()

    assert result.ok is True
    assert result.provider == vendor
    # Exactly one completion, on the resolved vendor, then its close: a
    # fresh client per tick that is never closed leaks an httpx pool into
    # the worker every five minutes.
    assert calls == [(vendor, "complete"), (vendor, "aclose")]


@pytest.mark.parametrize("vendor", VENDORS)
def test_every_registered_vendor_can_be_closed(vendor):
    """Every vendor the probe may build must own an ``aclose``.

    ``AnthropicProvider`` had none until DRF-1631: the probe could not
    reach it, so the gap was invisible. Reached now, a missing hook is a
    connection pool leaked into the Celery worker every five minutes.
    """

    provider = build_provider(vendor)
    assert callable(getattr(provider, "aclose", None)), (
        f"{vendor} has no aclose(); the probe builds a fresh client per tick "
        "and would leak its pool"
    )


def test_health_module_names_no_vendor_module():
    """The literal shape of the defect: a concrete provider import.

    Guards the source text rather than behaviour, because this is how
    the bug is *written*, and a reviewer reading a one-line
    ``from apps.llm.providers.<x> import <X>Provider`` in a monitor does
    not reliably see a three-week outage in it.
    """

    import inspect

    source = inspect.getsource(health)

    # Positive control first — an absence assertion over a string we
    # never proved we loaded is a hope, not a check.
    assert len(source) > 2000
    assert "resolve_provider_tier" in source
    assert "build_provider" in source

    for vendor in VENDORS:
        module_hint = f"apps.llm.providers.{vendor}_provider"
        assert module_hint not in source, (
            f"apps/llm/health.py names {module_hint} directly. The probe must "
            "resolve its vendor through apps.llm.router, or it measures a "
            "vendor the product may not be using — DRF-1631."
        )


def test_uncovered_vendors_are_named_not_swallowed(settings):
    """A second vendor serving live turns is stated, not left implicit.

    One cheap call answers for one vendor. When ``SKILL_LLM_PROVIDER``
    routes a skill elsewhere, that vendor is unwatched — this does not
    fix that (a probe per vendor is its own ticket), it refuses to let
    it be silent.
    """

    other = [name for name in VENDORS if name != VENDORS[0]]
    assert other, "registry must hold more than one vendor for this case to exist"

    settings.LLM_PROVIDER = VENDORS[0]
    settings.SKILL_LLM_PROVIDER = {"faq": other[0]}

    # Presence: both vendors really are reachable configuration.
    assert configured_vendor_names() == [VENDORS[0], other[0]]

    probed, _source = health.probe_target()
    assert probed == VENDORS[0]
    assert health._log_uncovered_vendors(probed) == [other[0]]

    # And with no per-skill routing there is nothing to warn about.
    settings.SKILL_LLM_PROVIDER = {}
    assert health._log_uncovered_vendors(probed) == []  # empty-assert-ok: single-vendor config


@pytest.mark.parametrize("vendor", VENDORS)
def test_key_gate_asks_about_the_resolved_vendor(settings, monkeypatch, vendor):
    """The skip-for-no-key check must read the CHOSEN vendor's key.

    Pre-DRF-1631 it read ``OPENAI_API_KEY`` whatever the deployment
    ran on. On a deployment that sets only ``ANTHROPIC_API_KEY`` — the
    correct configuration for an Anthropic pilot — every tick would have
    skipped, and a skipped probe reads as a quiet one.
    """

    from apps.llm.router import _PROVIDER_SPECS

    settings.LLM_PROVIDER = vendor
    monkeypatch.setattr(
        health, "run_probe_sync", lambda **kw: health.ProbeResult(True, 0.5, vendor)
    )

    # Presence: with its own key set, the probe runs.
    assert health.check_llm_availability()["ok"] is True

    # Now empty exactly that vendor's key and nothing else.
    setattr(settings, _PROVIDER_SPECS[vendor].key_setting_name, "")
    assert health.check_llm_availability()["skipped"] == health.SKIP_NO_API_KEY


def test_openai_health_does_not_colour_the_anthropic_verdict(settings, monkeypatch):
    """Owner decision В-14: OpenAI's health does not determine Ayla's.

    The serving path wraps the resolved provider in
    ``QuotaFallbackProvider``, which hops to another vendor when the
    first reports its credits exhausted. A probe that inherited that
    wrapper would answer "the LLM is fine" on the strength of the vendor
    the product was *not* configured to use — the same confusion
    DRF-1631 is undoing, arriving through a different door.

    So this pins the probe half of В-14: with Anthropic out of credits
    and OpenAI answering happily, the verdict is DOWN and it is named
    against ``anthropic``.
    """

    from apps.llm.protocol import LLMVendorCreditsExhausted

    settings.LLM_PROVIDER = "anthropic"
    settings.LLM_QUOTA_FALLBACK_ENABLED = True

    async def _exhausted(self, messages, **kwargs):
        raise LLMVendorCreditsExhausted("anthropic.complete: vendor credits exhausted")

    async def _happy(self, messages, **kwargs):
        return object()

    async def _aclose(self):
        return None

    monkeypatch.setattr(provider_class("anthropic"), "complete", _exhausted, raising=False)
    monkeypatch.setattr(provider_class("anthropic"), "aclose", _aclose, raising=False)
    monkeypatch.setattr(provider_class("openai"), "complete", _happy, raising=False)
    monkeypatch.setattr(provider_class("openai"), "aclose", _aclose, raising=False)

    result = health.run_probe_sync()

    # Presence: the probe really ran against the configured vendor.
    assert result.provider == "anthropic"
    assert result.ok is False
    assert result.error_class == "LLMVendorCreditsExhausted"


def test_serving_path_still_hops_vendors_silently(settings, monkeypatch):
    """Records a FORBIDDEN behaviour so its removal has to be deliberate.

    This assertion does NOT endorse what it asserts. Under
    ``LLM_PROVIDER=anthropic`` the serving path today answers a user
    from OpenAI when Anthropic reports its credits exhausted, without
    telling anyone — which owner decision В-14 forbids outright: a
    fall-back is permitted only under a policy designed, approved and
    tested, and none exists.

    It is pinned here rather than fixed because the fix is a policy
    decision about completions and belongs to its own ticket, and
    because an unwatched defect is how this one lasted three weeks. When
    the policy lands, this test changes with it — visibly, in a diff,
    instead of a behaviour quietly ceasing.

    Holding a secret is not permission to fall back: note that nothing
    below configures a fall-back. It is armed by ``OPENAI_API_KEY``
    merely being set (:func:`apps.llm.router.provider_is_configured`).
    """

    import asyncio

    from apps.llm.protocol import LLMVendorCreditsExhausted

    settings.LLM_PROVIDER = "anthropic"
    settings.LLM_QUOTA_FALLBACK_ENABLED = True

    served_by: list[str] = []

    async def _exhausted(self, messages, **kwargs):
        served_by.append("anthropic")
        raise LLMVendorCreditsExhausted("anthropic.complete: vendor credits exhausted")

    async def _happy(self, messages, **kwargs):
        served_by.append("openai")
        return object()

    monkeypatch.setattr(provider_class("anthropic"), "complete", _exhausted, raising=False)
    monkeypatch.setattr(provider_class("openai"), "complete", _happy, raising=False)

    provider = get_router().get_provider()
    assert provider.name == "anthropic"  # presence: this is the configured vendor

    asyncio.run(provider.complete([{"role": "user", "content": "ping"}]))

    # DEFECT, pinned: the user was answered by a vendor nobody chose.
    assert served_by == ["anthropic", "openai"]
