"""A watch over the vendor SDK names, for the tests under ``apps/llm/``.

DRF-1643. The fix for the leak is `config/pytest_vendor_sdk_preimport.py`; this
is the thing that notices if it ever stops working, or if a second channel opens.

### What is watched, and why not everything

An explicit set of names rather than "every attribute of the module". A whole
module's ``__dict__`` moves for reasons that are nobody's fault — caches fill,
lazy submodules attach — and a watch that cried at those would be silenced
within a week, which is the same as not having one.

The set is the classes a test can plausibly patch or inherit from, and the
derived one that cannot be restored once it is bound.

### Two different failures, told apart

A poisoned vendor class is **permanent for the process**: nothing can put it
back. So by the time a test in this directory notices, the damage may be several
tests old, and blaming the test that noticed would send the next person to the
wrong file.

Hence two checks with two different messages:

* at setup — "this was already broken when the test started", i.e. look
  upstream, and the message says where upstream tends to be;
* around the test — "this test changed it", which is a real attribution.

### The limit of this watch, stated because a silent limit reads as coverage

It is installed under ``apps/llm/`` and watches only tests in that tree. The
poisoner measured on 2026-09-10 lives in ``apps/orchestrator/tests/`` — outside
it. This watch would see the *consequence* and not the cause, and it says so in
the failure text rather than only here.

A repository-wide version belongs in the plugin, and was deliberately not added
in one go: the blast radius of an autouse check over ~1600 tests is not
something to introduce without somebody to weigh it.
"""

from __future__ import annotations

import inspect

import pytest

#: module name -> attributes whose identity must survive a test.
WATCHED: dict[str, tuple[str, ...]] = {
    "httpx": ("AsyncClient", "Client"),
    "openai": ("AsyncOpenAI", "DefaultAsyncHttpxClient"),
    "anthropic": ("AsyncAnthropic", "DefaultAsyncHttpxClient"),
}

#: These must be classes, always. `DefaultAsyncHttpxClient` derives from
#: `httpx.AsyncClient` at import time, so a mock in its place is not a mock
#: standing in for a class — it is the class, gone.
MUST_BE_CLASSES: dict[str, tuple[str, ...]] = {
    "openai": ("DefaultAsyncHttpxClient",),
    "anthropic": ("DefaultAsyncHttpxClient",),
}

_UPSTREAM_HINT = (
    "The vendor SDKs derive a class from httpx.AsyncClient at import time, and "
    "the import is lazy (apps/llm/providers/openai_provider.py:379 and its "
    'anthropic twin). A first import landing inside `with patch("httpx.AsyncClient")` '
    "binds that class to the mock permanently — unpatching cannot undo it. "
    "config/pytest_vendor_sdk_preimport.py exists to close that window; if you are "
    "reading this, either it is not loaded (check `addopts` in pyproject.toml) or a "
    "second channel has opened."
)


def _snapshot() -> dict[str, int]:
    import importlib

    seen: dict[str, int] = {}
    for module_name, attributes in WATCHED.items():
        try:
            module = importlib.import_module(module_name)
        except ImportError:  # an SDK that is not installed cannot be poisoned
            continue
        for attribute in attributes:
            if hasattr(module, attribute):
                seen[f"{module_name}.{attribute}"] = id(getattr(module, attribute))
    return seen


def _broken_classes() -> list[str]:
    import importlib

    broken: list[str] = []
    for module_name, attributes in MUST_BE_CLASSES.items():
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            continue
        for attribute in attributes:
            value = getattr(module, attribute, None)
            if value is not None and not inspect.isclass(value):
                broken.append(f"{module_name}.{attribute} is {type(value).__name__}, not a class")
    return broken


@pytest.fixture(autouse=True)
def vendor_sdk_names_survive_this_test() -> object:
    """Autouse for ``apps/llm/**``: the watched names come out as they went in."""

    already = _broken_classes()
    if already:
        pytest.fail(
            "vendor SDK class was ALREADY broken before this test ran — the cause is "
            f"upstream of here, not in this file. Broken: {already}. {_UPSTREAM_HINT}"
        )

    before = _snapshot()
    assert before, "nothing was watched — the SDKs are absent, or WATCHED went empty"

    yield

    after = _snapshot()
    moved = sorted(name for name in before if name in after and before[name] != after[name])
    vanished = sorted(name for name in before if name not in after)
    broken = _broken_classes()

    if moved or vanished or broken:
        pytest.fail(
            "this test changed a vendor SDK name and did not put it back. "
            f"moved={moved} vanished={vanished} not_a_class={broken}. {_UPSTREAM_HINT}"
        )
