"""Shared lookup for the ``global_kb`` system tenant (KB-RAG Sub-2 / GH #115).

This module is the single source of truth for resolving the system
tenant that owns the cross-salon knowledge-base corpus. The retriever
(:mod:`apps.kb.services.retriever`) and the upcoming seed command
(:mod:`apps.kb.management.commands.seed_kb_from_gdocs`, Sub-5) both
call :func:`get_global_kb_tenant` instead of re-implementing the slug
lookup.

Sub-3 (PR #122) shipped the lookup inline in the retriever to avoid a
hard dependency on Sub-2 landing first. Sub-2 now extracts the shared
helper; the retriever is refactored to import from here, keeping a
single place to evolve cache strategy or surface the slug in the
future.

Lookup contract:
  * By slug (``GLOBAL_KB_TENANT_SLUG``), via ``Tenant.all_objects`` so
    deactivated rows remain visible.
  * NOT filtered by ``Tenant.is_system`` — that flag is admin safety
    against accidental delete, not the public lookup key. A
    misconfigured operator who provisioned the slug without ``--system``
    still surfaces in retrieval.
  * Cached via ``functools.lru_cache`` (size 1) — the row is created
    once per environment and never mutated; we don't want each retrieval
    cycle hitting Postgres for the same answer. Tests use
    ``get_global_kb_tenant.cache_clear()`` to reset between scenarios.
  * Missing → ``None`` + a single WARN log. Callers (retriever, seed
    command) decide what "no shared corpus available" means in their
    own domain.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import TYPE_CHECKING

from apps.kb.constants import GLOBAL_KB_TENANT_SLUG

if TYPE_CHECKING:
    from apps.tenancy.models import Tenant

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_global_kb_tenant() -> "Tenant | None":
    """Resolve the system tenant that owns shared cross-salon KB content.

    Returns the :class:`apps.tenancy.models.Tenant` row matching
    :data:`apps.kb.constants.GLOBAL_KB_TENANT_SLUG`, or ``None`` (with a
    single WARN log) when no such row exists. Callers treat ``None`` as
    "no global fallback available" and serve tenant-only results.

    The result is process-cached — the slug is provisioned once and
    never changes. Tests can force a fresh lookup with
    ``get_global_kb_tenant.cache_clear()``.
    """
    # Local import to avoid Django app-loading order issues at module
    # import time (apps.kb may be imported before apps.tenancy in some
    # contexts).
    from apps.tenancy.models import Tenant

    row = Tenant.all_objects.filter(slug=GLOBAL_KB_TENANT_SLUG).first()
    if row is None:
        logger.warning(
            "kb.global_tenant.missing slug=%s falling_back_to_tenant_only",
            GLOBAL_KB_TENANT_SLUG,
        )
    return row
