"""Mandatory write-provenance for ``MasterService`` edges (DRF-975).

## What went wrong

On 2026-07-22 16:14 UTC, 232 ``MasterService`` rows appeared for the
``formula-tela`` pilot tenant on ``api-dev.gobeauty.site`` in a single bulk
act. ``created_by`` was NULL on every one of them, and **no** ``AuditLog`` row
exists for the window. It was not the MM4 matrix, not the invite seeder, not a
sync beat (those run :00/:15, not :14), and not the dev seed. The only
remaining explanation is a hand-run script on the host that went through the
ORM and past every audit call site, because every audit call site in this
codebase lives in a *view*.

The cost was not the missing journal line. ``MasterService`` ownership is
discriminated by ``ayla_specialist_service_id`` (NULL ⇒ operator-owned, and
catalog sync must never reconcile an operator row away — see
``apps.catalog.services.upserter.upsert_master_services``). All 232 rows are
NULL, so sync will not touch them, so a data fix on the Ayla side does not
reach the pilot. Absence of audit did not merely hide the author — it
manufactured rows the system cannot repair on its own (DRF-967).

## The rule this module enforces

**An INSERT into ``catalog_masterservice`` through the Django ORM is refused
unless the caller has named itself.** Naming yourself means entering
:func:`master_service_write` with a :class:`MasterServiceSource`. In exchange
the write is stamped on the row (``source``, ``created_by_actor_id``) and an
``AuditLog`` row is written by :mod:`apps.catalog.signals`.

There is no settings flag to turn this off. A flag would be the first thing a
hand-run script sets.

## What this does and does not stop

* Stops: ``.create()``, ``.save()``, ``.get_or_create()``,
  ``.update_or_create()``, ``.bulk_create()``, ``loaddata`` — i.e. everything
  a `manage.py shell` / `manage.py runscript` operator would plausibly reach
  for, including the exact shape of the 2026-07-22 event.
* Does not stop: a raw ``INSERT`` in ``psql``. Nothing in Python can. What it
  does instead is remove the *accidental* unaudited path: an operator who
  wants an edge now has to either name themselves
  (``MasterServiceSource.MANUAL_SCRIPT``, which stamps the row and audits it)
  or deliberately step outside the ORM, which is no longer something you do
  by writing the obvious three lines. See the module docstring of
  ``apps.catalog.signals`` for why a DB-level ``NOT NULL`` was not chosen
  instead.

## Usage

    from apps.catalog.provenance import MasterServiceSource, master_service_write

    with master_service_write(MasterServiceSource.MM4_MATRIX, actor_id=bot_user.id):
        MasterService.all_tenants.create(tenant=tenant, master=m, service=s)

Nesting is supported and the innermost context wins — a production writer that
opens its own context inside an ambient one (the test-suite fixture, a batch
wrapper) stamps its own source, which is what the per-writer tests assert.

## Design note — why a ContextVar

Same reason ``apps.tenancy.context`` uses one: the alternative is threading a
``source=`` kwarg through every signature between the entry point and the ORM
call, and any function that forgets is a silent hole. A ContextVar read at the
ORM boundary cannot be forgotten — it is either set or the write is refused.
``contextvars`` (not ``threading.local``) so the value survives ``await`` and
``sync_to_async`` boundaries.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from enum import StrEnum
from typing import Iterator
from uuid import UUID


class MasterServiceSource(StrEnum):
    """Who is writing this edge. Stored verbatim in ``MasterService.source``.

    Values are stable wire/DB strings — renaming one silently reclassifies
    every historical row, so treat them as a schema, not as labels.
    """

    #: ``apps.catalog.services.upserter.upsert_master_services`` — the catalog
    #: sync beat mirroring Ayla's canonical ``SpecialistService`` edges. Rows
    #: also carry a non-NULL ``ayla_specialist_service_id``.
    CATALOG_SYNC = "catalog_sync"

    #: ``apps.admin_api.views_services_mapping`` — an owner/admin ticking a
    #: cell in the MM4 master×service matrix.
    MM4_MATRIX = "mm4_matrix"

    #: ``apps.admin_api.views_invite`` — the service set seeded when a master
    #: is invited.
    INVITE_SEED = "invite_seed"

    #: ``apps.catalog.management.commands.seed_dev_formula_tela`` — the dev
    #: fixture command. Never expected on a tenant that syncs.
    DEV_SEED = "dev_seed"

    #: A hand-run script or a `manage.py shell` session. This exists so the
    #: 2026-07-22 event has a *name* rather than being impossible-and-therefore
    #: done some other way. Using it is legitimate; using it without a
    #: ``reason`` is not, and :func:`master_service_write` refuses that.
    MANUAL_SCRIPT = "manual_script"

    #: Test fixtures. Set by the ``config.pytest_master_service_provenance``
    #: plugin for the whole suite so that ~60 existing fixture call sites did
    #: not have to be rewritten across five teams' worktrees. Never reachable
    #: from production code.
    TEST_FIXTURE = "test_fixture"


#: Sources that must supply a human-readable ``reason``. A hand-run bulk write
#: with no stated purpose is precisely the artefact DRF-975 exists to prevent.
_REASON_REQUIRED: frozenset[MasterServiceSource] = frozenset({MasterServiceSource.MANUAL_SCRIPT})


class UnprovenancedMasterServiceWrite(RuntimeError):
    """Raised when a ``MasterService`` row would be created with no author.

    Deliberately a hard error and not a warning. A warning is a log line
    nobody reads on a host nobody is watching, which is the exact failure
    mode that produced 232 unattributable rows.
    """


@dataclass(frozen=True, slots=True)
class MasterServiceWriteContext:
    """The active provenance declaration."""

    source: MasterServiceSource
    actor_id: UUID | None = None
    reason: str = ""


_WRITE_CTX: ContextVar[MasterServiceWriteContext | None] = ContextVar(
    "master_service_write_ctx", default=None
)


def current_master_service_write() -> MasterServiceWriteContext | None:
    """Return the provenance context in scope, or None."""

    return _WRITE_CTX.get()


@contextmanager
def master_service_write(
    source: MasterServiceSource,
    *,
    actor_id: UUID | str | None = None,
    reason: str = "",
) -> Iterator[MasterServiceWriteContext]:
    """Declare who is about to write ``MasterService`` rows.

    Args:
      source: One of :class:`MasterServiceSource`. Not a free string — an
        unknown writer is a writer nobody reviewed.
      actor_id: UUID of the acting ``BotUser``, when there is a human. Stamped
        onto ``MasterService.created_by_actor_id`` so the "who" survives
        ``AUDIT_LOG_RETENTION_DAYS`` (90d), after which the AuditLog row is
        gone and the row itself is the only remaining evidence.
      reason: Free text. Mandatory for
        :attr:`MasterServiceSource.MANUAL_SCRIPT` (e.g. a Linear id).

    Raises:
      TypeError: ``source`` is not a :class:`MasterServiceSource`.
      ValueError: ``reason`` missing where it is required, or ``actor_id`` is
        not UUID-shaped.
    """

    if not isinstance(source, MasterServiceSource):
        raise TypeError(
            f"source must be a MasterServiceSource, got {type(source).__name__!r}. "
            "Add a member to the enum rather than passing a string — every writer "
            "of this table is meant to be reviewable by reading one file."
        )
    if source in _REASON_REQUIRED and not reason.strip():
        raise ValueError(
            f"source={source.value!r} requires a reason (e.g. the Linear issue id). "
            "An unexplained bulk write to catalog_masterservice is the DRF-975 "
            "artefact itself."
        )

    coerced: UUID | None
    if actor_id is None:
        coerced = None
    elif isinstance(actor_id, UUID):
        coerced = actor_id
    else:
        try:
            coerced = UUID(str(actor_id))
        except (ValueError, AttributeError, TypeError) as exc:
            raise ValueError(f"actor_id must be UUID-shaped, got {actor_id!r}") from exc

    ctx = MasterServiceWriteContext(source=source, actor_id=coerced, reason=reason.strip())
    token = _WRITE_CTX.set(ctx)
    try:
        yield ctx
    finally:
        _WRITE_CTX.reset(token)


def require_master_service_write(operation: str) -> MasterServiceWriteContext:
    """Return the active context or raise :class:`UnprovenancedMasterServiceWrite`.

    The single choke point. Called from the ``pre_save`` signal and from
    ``MasterServiceQuerySet.bulk_create``.
    """

    ctx = _WRITE_CTX.get()
    if ctx is None:
        raise UnprovenancedMasterServiceWrite(
            f"MasterService.{operation} refused: no write provenance in scope (DRF-975).\n"
            "Every master↔service edge must record who created it. Wrap the write:\n"
            "\n"
            "    from apps.catalog.provenance import MasterServiceSource, "
            "master_service_write\n"
            "\n"
            "    with master_service_write(MasterServiceSource.MANUAL_SCRIPT,\n"
            "                              actor_id=<bot_user_id or None>,\n"
            "                              reason='DRF-xxx: why'):\n"
            "        ...\n"
            "\n"
            "This stamps MasterService.source / .created_by_actor_id and writes an "
            "AuditLog row. On 2026-07-22 a script skipped all of that and left 232 "
            "rows the catalog sync can never reconcile."
        )
    return ctx


__all__ = [
    "MasterServiceSource",
    "MasterServiceWriteContext",
    "UnprovenancedMasterServiceWrite",
    "current_master_service_write",
    "master_service_write",
    "require_master_service_write",
]
