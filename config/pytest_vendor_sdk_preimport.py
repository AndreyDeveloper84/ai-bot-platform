"""Import the vendor SDKs before any test can patch what they inherit from.

DRF-1643. Loaded via ``-p`` in ``pyproject.toml``'s ``addopts``, next to
``pytest_env_guard`` and ``pytest_master_service_provenance``, and for a
related reason: a plugin is imported earlier than any ``conftest.py``.

### The defect

Both SDKs execute, at import time::

    class DefaultAsyncHttpxClient(httpx.AsyncClient): ...

The base is bound when the module is imported. And the import is **lazy** — it
happens inside ``OpenAIProvider._get_client`` (``apps/llm/providers/
openai_provider.py:379``) and its Anthropic twin (``anthropic_provider.py:385``),
deliberately, so that tests which mock the provider entirely do not pay for a
heavy SDK.

That economy is sound and it opened a window. The first import in the process
happens wherever a test first reaches it — and if that place is inside
``with patch("httpx.AsyncClient")``, the class is created from the mock. Not as
a subclass of one: the result has no ``__mro__`` at all, it *is* a mock.

``patch`` then does its job perfectly and restores ``httpx.AsyncClient``. It
cannot restore what was derived from it while it was replaced.

Measured rather than reasoned (2026-09-10, `origin/dev` a4f82b61)::

    openai has a module __getattr__ (lazy attribute)?   no
    import outside the patch, touch the name inside     class intact
    import inside the patch                             poisoned
    same for anthropic, with a clean-import control     poisoned / intact

Downstream this shows up as ``StopIteration`` from an exhausted ``side_effect``
belonging to a neighbour, as ``Mock object has no attribute '_mounts'``, and —
most legibly — as ``TypeError: issubclass() arg 2 must be a class``, which says
the thing outright: the argument is not a class, because the class is gone.

### The fix, and why it is here rather than in a conftest

Import both SDKs once, before any test runs. Then there is nothing left to bind
and no window to land in, whatever a test patches.

``pyproject.toml`` already explains why this kind of thing goes in a plugin:
``pytest-django`` calls ``django.setup()`` inside
``pytest_load_initial_conftests``, i.e. **before any conftest is imported**.
There is no root ``conftest.py`` in this repository, and a new one would load
later than the two plugins already there.

### The cost, named so it is accepted rather than discovered

Measured in this environment, three runs: ``openai`` 1.19 / 0.98 / 1.00 s,
``anthropic`` 0.90 / 0.78 / 0.82 s. Under ``-n 4`` that is paid once per worker
process, not once per run. Processes that would have imported the SDK anyway —
most of them — pay nothing extra; the worst case is a worker whose tests never
touch a provider.

``apps/llm/warmup.py`` records much larger numbers from the pilot's own
container (``import anthropic`` 31.1 s on a cold page cache). Those are not CI's
numbers and are not quoted as such; CI runs on a warm image.

### What this plugin does not do

It does not stop anybody patching ``httpx.AsyncClient``, and should not: that
patch is legitimate and the tests using it are correct. It removes the window in
which such a patch could be inherited from, which is a different thing and the
only one that can be fixed here — the alternative, forbidding the patch, would
be fixing the victim.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: The SDKs that derive a class from ``httpx.AsyncClient`` at import time.
#: Both, not one: `anthropic` has the same construct and is not failing today
#: only because nothing has reached it while a patch was active — asleep by
#: exposure, not by mechanism, which is the classification that produced this
#: ticket in the first place.
VENDOR_SDKS: tuple[str, ...] = ("openai", "anthropic")


def _preimport() -> None:
    for name in VENDOR_SDKS:
        try:
            __import__(name)
        except ImportError:  # pragma: no cover - a missing optional SDK is not fatal
            # An absent SDK cannot be poisoned either. Logged rather than raised:
            # this plugin must never be the reason a test session fails to start.
            logger.info("pytest_vendor_sdk_preimport: %s is not installed, skipping", name)


_preimport()
