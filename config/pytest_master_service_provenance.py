"""pytest plugin: ambient ``MasterService`` write provenance for tests (DRF-975).

Wired in via ``addopts = "-p config.pytest_master_service_provenance ..."`` in
``pyproject.toml``, alongside ``config.pytest_env_guard``.

## Why this exists

DRF-975 makes an unprovenanced ``MasterService`` INSERT raise. Roughly sixty
existing test fixtures across ten apps build edges with a bare
``MasterService.all_tenants.create(...)`` — booking, marketplace discovery,
orchestrator, channels, admin_api, master_api, miniapp_api. Rewriting all of
them would (a) be pure noise in a PR about provenance and (b) collide head-on
with four other worktrees that are live in those exact directories right now.

So the suite gets an ambient :attr:`MasterServiceSource.TEST_FIXTURE` context
and fixtures keep working unchanged.

## Why this does not defeat the point

The obvious objection: if every test runs inside a context, a production
writer that forgot to open its own would still pass its tests. Two things
answer that.

1. Contexts nest, innermost wins. A writer that opens
   ``master_service_write(MM4_MATRIX)`` stamps ``source="mm4_matrix"`` even
   under the ambient one — so asserting the *specific* source is a real test
   of the production path. ``test_master_service_write_provenance.py`` does
   exactly that, per writer.
2. The test that proves the gate itself
   (``test_unprovenanced_create_is_refused``) uses the
   :func:`no_master_service_provenance` fixture below to tear the ambient
   context down first, so it is testing the same bare state a script on the
   host runs in.

## Why a ``-p`` plugin and not a root ``conftest.py``

There is no root ``conftest.py`` in this repo, deliberately (see
``config/pytest_env_guard`` for the app-registry reason). Adding one just for
this would be a new shared file for five concurrent worktrees to conflict
over; ``-p`` follows the pattern already established here.
"""

from __future__ import annotations

from typing import Iterator

import pytest


@pytest.fixture(autouse=True)
def _ambient_master_service_provenance() -> Iterator[None]:
    """Open a TEST_FIXTURE provenance context around every test.

    Imports are function-local: this module is imported at pytest preparse
    time, long before ``django.setup()``, and importing ``apps.catalog`` that
    early would blow up the app registry.
    """

    from apps.catalog.provenance import MasterServiceSource, master_service_write

    with master_service_write(
        MasterServiceSource.TEST_FIXTURE,
        reason="pytest ambient context (config.pytest_master_service_provenance)",
    ):
        yield


@pytest.fixture
def no_master_service_provenance() -> Iterator[None]:
    """Tear the ambient context down for the duration of one test.

    Request this to assert what happens with **no** provenance in scope — i.e.
    the state a ``manage.py shell`` session on the host runs in. Without it,
    a test asserting that the gate refuses a write would silently be running
    inside the ambient context and would fail for the wrong reason.
    """

    from apps.catalog.provenance import _WRITE_CTX

    token = _WRITE_CTX.set(None)
    try:
        yield
    finally:
        _WRITE_CTX.reset(token)
