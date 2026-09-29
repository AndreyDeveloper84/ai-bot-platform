"""DRF-1643 — the vendor SDK classes are real classes, and stay so.

The defect, measured on 2026-09-10 against `origin/dev` a4f82b61:

Both SDKs execute `class DefaultAsyncHttpxClient(httpx.AsyncClient)` at **import**
time, and both are imported lazily, inside `_get_client()`. A first import
landing inside `with patch("httpx.AsyncClient")` creates that class from the
mock — not as a subclass of one, the result has no `__mro__` at all. `patch`
then restores `httpx.AsyncClient` correctly and cannot restore what was derived
from it meanwhile.

Downstream it looked like somebody else's exhausted `side_effect`
(`StopIteration`), like `Mock object has no attribute '_mounts'`, and — the
legible one — like `TypeError: issubclass() arg 2 must be a class`.

Reproduced in three files and seconds rather than a forty-minute suite:

    apps/llm/providers/tests/test_proxy_client_type.py + apps/llm/tests/test_warmup.py
        alone                                    23 passed, 0 failures
    with apps/orchestrator/tests/test_openai_provider.py first
                                                 31 tests, 6 failures
    the same three, with the pre-import plugin    31 tests, 0 failures
"""

from __future__ import annotations

import inspect
import sys
from unittest.mock import patch

import pytest

VENDORS = ("openai", "anthropic")


def test_both_vendor_client_classes_are_real_classes() -> None:
    """The regression guard for the fix. Cheap, direct, and both vendors.

    `anthropic` is here even though nothing has ever failed on it: it has the
    same construct and the same lazy import, and it is quiet only because
    nothing has reached it while a patch was active. Asleep by exposure, not by
    mechanism — which is the classification that produced this ticket.
    """

    for vendor in VENDORS:
        module = __import__(vendor)
        klass = module.DefaultAsyncHttpxClient
        assert inspect.isclass(klass), f"{vendor}.DefaultAsyncHttpxClient is {type(klass).__name__}"


def test_the_derived_class_really_descends_from_httpx() -> None:
    """The positive control for the test above.

    Without it, a vendor that stopped deriving from `httpx.AsyncClient` — and so
    could never be poisoned — would make the guard pass while it guarded nothing.
    """

    import httpx

    for vendor in VENDORS:
        module = __import__(vendor)
        assert issubclass(module.DefaultAsyncHttpxClient, httpx.AsyncClient)


def test_the_preimport_plugin_is_actually_loaded(pytestconfig: pytest.Config) -> None:
    """The fix is a plugin in `addopts`. A guard that assumed it was loaded would
    pass in a run that had dropped it."""

    plugin_names = set(pytestconfig.pluginmanager.list_name_plugin())
    names = {name for name, _ in pytestconfig.pluginmanager.list_name_plugin()}

    assert plugin_names or names  # presence: the manager really answered
    assert any("vendor_sdk_preimport" in name for name in names), sorted(
        n for n in names if "config" in n or "pytest_" in n
    )


def test_the_sdks_are_imported_before_any_test_runs() -> None:
    """What the plugin is for, stated as the property rather than the mechanism.

    If the SDKs are already in `sys.modules` when a test starts, no test can be
    the one that imports them — and therefore no test can import them inside a
    patch.
    """

    for vendor in VENDORS:
        assert vendor in sys.modules, f"{vendor} was not pre-imported"


def test_patching_httpx_no_longer_reaches_the_vendor_classes() -> None:
    """The defect, attempted, and refused.

    This is the shape that used to poison the process. With the SDKs already
    imported there is nothing left to bind, so the patch is harmless — which is
    the point: the patch was never the problem and is not forbidden here.
    """

    import openai

    before = id(openai.DefaultAsyncHttpxClient)

    with patch("httpx.AsyncClient"):
        import openai as reimported  # already in sys.modules — a no-op

        during = id(reimported.DefaultAsyncHttpxClient)

    after = id(openai.DefaultAsyncHttpxClient)

    assert before == during == after
    assert inspect.isclass(openai.DefaultAsyncHttpxClient)


def test_the_two_vendors_do_not_share_a_client_class() -> None:
    """Each SDK derives its own. Sharing would mean poisoning one poisons both,
    and would make the two-vendor guard above a single guard wearing two names."""

    import anthropic
    import openai

    assert openai.DefaultAsyncHttpxClient is not anthropic.DefaultAsyncHttpxClient
