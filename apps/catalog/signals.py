"""``MasterService`` write-provenance enforcement + audit (DRF-975).

Connected from :class:`apps.catalog.apps.CatalogConfig.ready`.

## Why the enforcement point is a model signal

Three mechanisms were on the table. Each catches a different set of writers,
and the incident this fixes was a **hand-run script on the host** — so "which
of these would have stopped 2026-07-22" is the question that decides it.

┌────────────────────────────┬─────────────────┬──────────────────────────────┐
│ mechanism                  │ stops host      │ cost                         │
│                            │ script?         │                              │
├────────────────────────────┼─────────────────┼──────────────────────────────┤
│ service layer + "don't     │ NO — a script   │ Cheapest, but it does not    │
│ write the model directly"  │ imports the     │ address the actual incident. │
│                            │ model           │                              │
├────────────────────────────┼─────────────────┼──────────────────────────────┤
│ model signal (chosen)      │ YES — any ORM   │ Fixtures/tests must declare  │
│                            │ path, shell     │ a source; migrations are      │
│                            │ included        │ unaffected (see below).      │
├────────────────────────────┼─────────────────┼──────────────────────────────┤
│ DB NOT NULL / CHECK on     │ YES, plus raw   │ Requires deciding, in the    │
│ provenance                 │ psql            │ same migration, what the 232 │
│                            │                 │ existing NULL rows become —  │
│                            │                 │ an owner decision, not ours. │
└────────────────────────────┴─────────────────┴──────────────────────────────┘

The signal is picked because it is the strongest mechanism that does not
require pre-deciding the fate of the 232 pilot rows. The DB constraint is
strictly stronger and remains available as a follow-up **once that decision is
made** — it can then be added as a ``NOT VALID`` check so it binds new inserts
without rewriting history. Doing all three now would be three ways to be
wrong; the signal is the one that pays for itself today.

## Why migrations do not break

Data migrations operate on *historical* models from
``apps.registry.apps.get_model`` — plain reconstructions that carry no custom
managers and to which no app-loaded signal receiver is connected by
``sender=MasterService``. Django's ``post_migrate``/``pre_save`` dispatch keys
on the concrete class object, and the historical class is a different object.
So a future data migration touching this table is unaffected — verified by the
migration test in ``apps/catalog/tests/test_master_service_write_provenance.py``.

``loaddata``, by contrast, *is* covered: the deserializer calls
``Model.save_base(raw=True)``, which still fires ``pre_save``. That is
intentional — a fixture load into a live database is a write like any other.

## Audit rows

``write_audit`` is the platform's one audit entry point (``apps.audit``); this
module reuses it rather than introducing a second journal. Two actions:

* ``master.service_edge_created`` — one row per edge created.
* ``master.service_edge_deleted`` — one row per edge deleted.

Note the deliberate asymmetry: **creation is enforced, deletion is only
audited.** ``MasterService`` rows are CASCADE children of ``Tenant``,
``CatalogMaster`` and ``CatalogService``; raising inside ``post_delete`` would
make deleting a master (or tearing down a test tenant) fail on a forensic
concern. Deletion also cannot manufacture the DRF-975 artefact — an absent row
is not an unreconcilable row. So delete gets a trace, not a gate.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

if TYPE_CHECKING:
    from apps.catalog.models import MasterService
    from apps.catalog.provenance import MasterServiceWriteContext

logger = logging.getLogger(__name__)


#: Namespaced audit verbs. Kept alongside the rest of the vocabulary in
#: ``apps.events.vocabulary`` — imported lazily inside the receivers so this
#: module stays importable during app loading.
def _actions() -> tuple[str, str]:
    from apps.events.vocabulary import MASTER_SERVICE_EDGE_CREATED, MASTER_SERVICE_EDGE_DELETED

    return MASTER_SERVICE_EDGE_CREATED, MASTER_SERVICE_EDGE_DELETED


def stamp_provenance(instance: "MasterService", ctx: "MasterServiceWriteContext") -> None:
    """Write the context onto the row. Shared by ``pre_save`` and ``bulk_create``.

    Unconditional on the ``source`` field: the context is the authority, not
    whatever the caller happened to pass. Letting a caller-supplied ``source=``
    win would recreate the hole — a script could stamp ``catalog_sync`` on its
    own rows and disappear into the sync's traffic.
    """

    instance.source = ctx.source.value
    if ctx.actor_id is not None:
        instance.created_by_actor_id = ctx.actor_id


def audit_master_service_created(
    instance: "MasterService", ctx: "MasterServiceWriteContext"
) -> None:
    """Write the ``master.service_edge_created`` row. Never raises.

    ``write_audit`` already swallows its own failures; this wrapper exists so
    ``bulk_create`` and ``post_save`` emit an identical payload shape.
    """

    from apps.audit.services import write_audit

    created_action, _ = _actions()
    write_audit(
        created_action,
        target="catalog.MasterService",
        target_id=instance.pk,
        payload=_payload(instance, ctx),
        actor_id=ctx.actor_id,
    )


def _payload(instance: "MasterService", ctx: "MasterServiceWriteContext | None") -> dict[str, Any]:
    payload: dict[str, Any] = {
        "master_service_id": str(instance.pk),
        "master_id": str(instance.master_id),
        "service_id": str(instance.service_id),
        "tenant_id": str(instance.tenant_id) if instance.tenant_id else None,
        # The sync-ownership discriminator, recorded at write time. Without it
        # the audit row cannot answer the DRF-967 question — "is this edge one
        # the sync can ever reconcile away?" — after the fact.
        "ayla_specialist_service_id": (
            str(instance.ayla_specialist_service_id)
            if instance.ayla_specialist_service_id
            else None
        ),
        "source": instance.source,
    }
    if ctx is not None and ctx.reason:
        payload["reason"] = ctx.reason
    return payload


# ``sender`` as a lazy "app_label.Model" string: ``pre_save``/``post_save``/
# ``post_delete`` are ``ModelSignal``s and resolve it through the app
# registry. Scoping by sender is not cosmetic — an unscoped receiver runs
# on every save of every model in the platform.
@receiver(pre_save, sender="catalog.MasterService", dispatch_uid="drf975_ms_provenance")
def _enforce_provenance_on_save(sender: Any, instance: Any, **kwargs: Any) -> None:
    """Refuse an unprovenanced INSERT; stamp a provenanced one.

    Scoped to INSERTs (``instance._state.adding``). An UPDATE of an existing
    row — the sync refreshing ``resolved_requires_health_check``, say — is not
    the thing that manufactures unattributable data, and gating it would mean
    every read-modify-write path in the codebase needs a context for a field
    that has nothing to do with authorship. The row's original author, which
    is what DRF-975 is about, is already recorded and is not being changed.
    """

    from apps.catalog.provenance import require_master_service_write

    if not instance._state.adding:
        return

    ctx = require_master_service_write("save")
    stamp_provenance(instance, ctx)


@receiver(post_save, sender="catalog.MasterService", dispatch_uid="drf975_ms_audit_created")
def _audit_created(sender: Any, instance: Any, created: bool, **kwargs: Any) -> None:
    from apps.catalog.provenance import current_master_service_write

    if not created:
        return

    ctx = current_master_service_write()
    if ctx is None:
        # Unreachable via pre_save (which would have raised), but a defensive
        # branch beats an AttributeError inside forensic code.
        logger.error(
            "catalog.master_service.audit_without_context id=%s — pre_save gate bypassed",
            instance.pk,
        )
        return
    audit_master_service_created(instance, ctx)


@receiver(post_delete, sender="catalog.MasterService", dispatch_uid="drf975_ms_audit_deleted")
def _audit_deleted(sender: Any, instance: Any, **kwargs: Any) -> None:
    """Trace every edge removal. Deliberately does NOT gate — see module doc."""

    from apps.audit.services import write_audit
    from apps.catalog.provenance import current_master_service_write

    ctx = current_master_service_write()
    _, deleted_action = _actions()
    payload = _payload(instance, ctx)
    # Unlike creation, a delete can legitimately have no context (a CASCADE
    # from tenant/master removal). Record which it was rather than pretending.
    payload["deleted_under_source"] = ctx.source.value if ctx is not None else None
    write_audit(
        deleted_action,
        target="catalog.MasterService",
        target_id=instance.pk,
        payload=payload,
        actor_id=ctx.actor_id if ctx is not None else None,
    )


__all__ = ["audit_master_service_created", "stamp_provenance"]
