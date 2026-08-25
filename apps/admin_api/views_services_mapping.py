"""Admin REST views — services ↔ masters M2M editing (PR 4 / MM4 backend).

Endpoints under ``/api/v1/admin/services-mapping/``:

* ``GET   /``         — full matrix snapshot (services, masters, mapping,
                        orphans, snapshot_token).
* ``POST  /bulk/``    — atomic diff apply (per-row enable/disable). Returns
                        409 + conflict details when the caller's snapshot is
                        stale; all-or-nothing semantics per spec line 671-672.

Per master-management handoff §MM4 backend contract (lines 654-675)::

    GET /api/v1/services-mapping
    POST /api/v1/services-mapping/bulk
    Body: { "changes": [ {"service_id", "master_id", "enabled"} ] }
    Response 200: { "applied": N, "conflicts": [] }
    Response 409: { "applied": 0, "conflicts": [...] }
    Audit: one master.services_changed event per master with diff payload.

These endpoints are mounted under ``/api/v1/admin/`` (Owner+Admin only) —
the front-end matrix UI is a separate frontend PR. Both Owner and Admin
can edit the mapping; deactivation gates (MM5) are out of scope here.

### Snapshot-token strategy (Option A — single hash)

The matrix is small at Phase 1 scale (~5 masters × ~30 services ≈ 150
cells). A single ``MAX(updated_at)`` of ``MasterService`` rows for the
tenant is enough to detect ANY concurrent edit. We HMAC-sign the
timestamp with ``settings.SECRET_KEY`` so the token is opaque to
clients but cheap on the server.

Option B (per-pair token) is more granular but the win is theoretical:
two admins editing different rows simultaneously is rare in a single
salon, and the UI re-fetches on 409 anyway. We can revisit if the
matrix grows past ~500 cells in Phase 2.

### Audit semantics

One ``master.services_changed`` event PER master with non-empty diff,
NOT per change. A bulk apply that toggles 5 services on Anna and 2 on
Maria produces exactly 2 audit rows. This makes the audit feed
readable ("Anna's services were updated" instead of 5 spammy entries).

### Out of scope (separate PRs)

* Frontend matrix UI / Mini App parity — separate frontend PR.
* DELETE endpoint for a single mapping — bulk POST with ``enabled=false``
  covers this.
* Pessimistic cell-level locking — Option A snapshot suffices.
* MM5 deactivation cascade — separate PR.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import uuid
from datetime import datetime
from typing import Any

from django.conf import settings
from django.db import transaction
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from apps.admin_api.auth import RoleContext, require_admin_role
from apps.audit.services import write_audit
from apps.catalog.provenance import MasterServiceSource, master_service_write
from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.events.vocabulary import MASTER_SERVICES_CHANGED
from apps.identity.models import BotUser

logger = logging.getLogger(__name__)


# --- helpers --------------------------------------------------------------


def _error(slug: str, detail: str, status: int) -> JsonResponse:
    return JsonResponse({"error": slug, "detail": detail}, status=status)


def _parse_json_body(request: HttpRequest) -> dict[str, Any] | JsonResponse:
    if not request.body:
        return {}
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return _error("bad_request", "invalid JSON body", 400)
    if not isinstance(data, dict):
        return _error("bad_request", "JSON body must be an object", 400)
    return data


def _coerce_uuid(value: Any) -> uuid.UUID | None:
    """Tolerant UUID coercion. Returns None for any non-UUID input."""

    if isinstance(value, uuid.UUID):
        return value
    if not isinstance(value, str):
        return None
    try:
        return uuid.UUID(value)
    except (TypeError, ValueError):
        return None


# --- snapshot-token (Option A) -------------------------------------------


def _snapshot_max_updated_at(tenant_id: Any) -> datetime | None:
    """Latest ``updated_at`` across all ``MasterService`` rows in the
    tenant. None when the tenant has zero mappings (empty matrix).
    """

    row = (
        MasterService.all_tenants.filter(tenant=tenant_id)
        .order_by("-updated_at")
        .values_list("updated_at", flat=True)
        .first()
    )
    return row


def _compute_snapshot_token(tenant_id: Any) -> str:
    """HMAC-SHA256 of the latest ``MasterService.updated_at`` for the
    tenant. The HMAC uses ``settings.SECRET_KEY`` so a malicious caller
    cannot forge a "looks current" token to bypass conflict detection.

    Empty matrix → constant ``"empty"`` token; the HMAC still guards
    cross-tenant token reuse because the input is per-tenant.
    """

    max_updated = _snapshot_max_updated_at(tenant_id)
    raw = "empty" if max_updated is None else max_updated.isoformat()
    key = (settings.SECRET_KEY or "fallback-key").encode("utf-8")
    msg = f"{tenant_id}:{raw}".encode("utf-8")
    digest = hmac.new(key, msg, hashlib.sha256).hexdigest()
    # Embed the raw timestamp inside the token so future debuggers can
    # see what state it pinned. The HMAC is the integrity guard.
    return f"{digest[:16]}.{raw}"


def _token_is_current(tenant_id: Any, supplied: str) -> bool:
    """True iff ``supplied`` equals the freshly-computed snapshot token
    for the tenant. Constant-time compare to dodge timing oracles.
    """

    expected = _compute_snapshot_token(tenant_id)
    return hmac.compare_digest(supplied, expected)


# --- GET /api/v1/admin/services-mapping ----------------------------------


@require_http_methods(["GET"])
@require_admin_role
def services_mapping_get(request: HttpRequest) -> HttpResponse:
    """Full matrix snapshot — services, masters, mapping, orphans, token.

    Response shape (per master-management handoff §MM4 backend contract,
    lines 657-661)::

        {
          "services": [{"id", "name", "duration_min", "price_from",
                        "is_active"}, ...],
          "masters":  [{"id", "name", "is_active", "invite_status"}, ...],
          "mapping":  [{"service_id", "master_id"}, ...],
          "orphans":  {"services_without_masters": [...],
                       "masters_without_services": [...]},
          "snapshot_token": "<opaque>"
        }

    Notes:
    * ``services`` filtered to ``is_active=True``, ordered by name.
    * ``masters`` filtered to ``is_active=True AND archived_at IS NULL``,
      ordered by name. Pending-invite masters ARE included — they may
      already have an intended services assignment.
    * Orphans computed in Python via set diff (one extra pass over a
      small dataset; cleaner than two ``Exists`` annotations).
    * ``snapshot_token`` is the HMAC-Option-A token described above.
    """

    tenant = request.tenant  # type: ignore[attr-defined]

    services_qs = list(
        CatalogService.objects.filter(tenant=tenant, is_active=True)
        .order_by("name")
        .values("id", "name", "duration_min", "price_from", "is_active")
    )
    services_payload = [
        {
            "id": str(s["id"]),
            "name": s["name"],
            "duration_min": s["duration_min"],
            "price_from": str(s["price_from"]) if s["price_from"] is not None else None,
            "is_active": s["is_active"],
        }
        for s in services_qs
    ]

    masters_qs = list(
        CatalogMaster.objects.filter(
            tenant=tenant,
            is_active=True,
            archived_at__isnull=True,
        )
        .order_by("name")
        .values("id", "name", "is_active", "invite_status")
    )
    masters_payload = [
        {
            "id": str(m["id"]),
            "name": m["name"],
            "is_active": m["is_active"],
            "invite_status": m["invite_status"],
        }
        for m in masters_qs
    ]

    mapping_rows = list(
        MasterService.objects.filter(tenant=tenant).values("master_id", "service_id")
    )
    mapping_payload = [
        {"master_id": str(r["master_id"]), "service_id": str(r["service_id"])} for r in mapping_rows
    ]

    # Orphan detection — set diff in Python. Only consider services /
    # masters that ACTUALLY appear in the GET response (filtered to
    # active). Inactive entities are simply absent and out of scope.
    service_ids = {s["id"] for s in services_payload}
    master_ids = {m["id"] for m in masters_payload}
    services_with_masters: set[str] = set()
    masters_with_services: set[str] = set()
    for row in mapping_payload:
        if row["service_id"] in service_ids:
            services_with_masters.add(row["service_id"])
        if row["master_id"] in master_ids:
            masters_with_services.add(row["master_id"])

    orphans = {
        "services_without_masters": sorted(service_ids - services_with_masters),
        "masters_without_services": sorted(master_ids - masters_with_services),
    }

    return JsonResponse(
        {
            "services": services_payload,
            "masters": masters_payload,
            "mapping": mapping_payload,
            "orphans": orphans,
            "snapshot_token": _compute_snapshot_token(tenant.id),
        }
    )


# --- POST /api/v1/admin/services-mapping/bulk ----------------------------


@csrf_exempt
@require_http_methods(["POST"])
@require_admin_role
def services_mapping_bulk(request: HttpRequest) -> HttpResponse:
    """Atomic bulk diff apply.

    Request body::

        {
          "changes": [
            {"service_id": "<uuid>", "master_id": "<uuid>", "enabled": true},
            {"service_id": "<uuid>", "master_id": "<uuid>", "enabled": false}
          ],
          "snapshot_token": "<opaque_str>"   # optional
        }

    Behaviour:

    1. Validate every ``service_id`` + ``master_id`` belongs to the
       current tenant. Inactive masters / services → 400. Cross-tenant
       references → 400.
    2. Reject duplicate ``(service_id, master_id)`` pairs in the same
       request — caller should consolidate client-side.
    3. If ``snapshot_token`` supplied and stale → 409 with the latest
       state of EVERY conflicting pair (i.e. every pair whose current
       value differs from the caller's intent). All-or-nothing: no
       changes applied. Per spec line 671-672.
    4. Otherwise: apply inside one ``transaction.atomic()``.
       ``enabled=true`` → ``get_or_create``; ``enabled=false`` → delete.
    5. Audit: one ``master.services_changed`` event per master with a
       diff payload — ``services_added`` + ``services_removed`` UUID
       lists. Group changes by master before emitting.
    6. Return ``new_snapshot_token`` so the UI can chain follow-up edits
       without a re-fetch.
    """

    tenant = request.tenant  # type: ignore[attr-defined]
    role_ctx: RoleContext = request.role_context  # type: ignore[attr-defined]
    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]

    body = _parse_json_body(request)
    if isinstance(body, JsonResponse):
        return body

    changes_raw = body.get("changes")
    if not isinstance(changes_raw, list):
        return _error("bad_request", "changes must be a list", 400)

    # --- normalise + validate every entry --------------------------------
    seen_pairs: set[tuple[uuid.UUID, uuid.UUID]] = set()
    normalised: list[tuple[uuid.UUID, uuid.UUID, bool]] = []
    for idx, entry in enumerate(changes_raw):
        if not isinstance(entry, dict):
            return _error("bad_request", f"changes[{idx}] must be an object", 400)
        service_uuid = _coerce_uuid(entry.get("service_id"))
        master_uuid = _coerce_uuid(entry.get("master_id"))
        enabled = entry.get("enabled")
        if service_uuid is None:
            return _error(
                "bad_request",
                f"changes[{idx}].service_id is not a valid UUID",
                400,
            )
        if master_uuid is None:
            return _error(
                "bad_request",
                f"changes[{idx}].master_id is not a valid UUID",
                400,
            )
        if not isinstance(enabled, bool):
            return _error(
                "bad_request",
                f"changes[{idx}].enabled must be a boolean",
                400,
            )
        pair = (service_uuid, master_uuid)
        if pair in seen_pairs:
            return _error(
                "bad_request",
                f"duplicate change for (service_id={service_uuid}, master_id={master_uuid})",
                400,
            )
        seen_pairs.add(pair)
        normalised.append((service_uuid, master_uuid, enabled))

    # Empty list → no-op + fresh token. Cheaper than dropping into the
    # validation loop with an empty queryset.
    if not normalised:
        return JsonResponse(
            {
                "applied": 0,
                "conflicts": [],
                "new_snapshot_token": _compute_snapshot_token(tenant.id),
            }
        )

    # --- tenant-scoped entity validation --------------------------------
    service_ids = {s for s, _, _ in normalised}
    master_ids = {m for _, m, _ in normalised}

    services_in_tenant = {
        s["id"]: s
        for s in CatalogService.objects.filter(tenant=tenant, id__in=service_ids).values(
            "id", "is_active"
        )
    }
    masters_in_tenant = {
        m["id"]: m
        for m in CatalogMaster.objects.filter(tenant=tenant, id__in=master_ids).values(
            "id", "is_active", "archived_at"
        )
    }

    for sid in service_ids:
        if sid not in services_in_tenant:
            return _error(
                "bad_request",
                f"service_id {sid} not found in this tenant",
                400,
            )
        if not services_in_tenant[sid]["is_active"]:
            return _error(
                "bad_request",
                f"service_id {sid} is inactive — cannot map",
                400,
            )

    for mid in master_ids:
        if mid not in masters_in_tenant:
            return _error(
                "bad_request",
                f"master_id {mid} not found in this tenant",
                400,
            )
        m_row = masters_in_tenant[mid]
        if not m_row["is_active"] or m_row["archived_at"] is not None:
            return _error(
                "bad_request",
                f"master_id {mid} is inactive — cannot map",
                400,
            )

    # --- snapshot-token conflict check ----------------------------------
    supplied_token = body.get("snapshot_token")
    if supplied_token is not None:
        if not isinstance(supplied_token, str):
            return _error("bad_request", "snapshot_token must be a string", 400)
        if not _token_is_current(tenant.id, supplied_token):
            # Build a CONFLICT list: every requested pair whose current
            # state differs from what the caller intends. The caller can
            # then merge or retry. All-or-nothing — applied=0.
            current_rows = {
                (r["service_id"], r["master_id"]): r
                for r in MasterService.objects.filter(
                    tenant=tenant,
                    master_id__in=master_ids,
                    service_id__in=service_ids,
                ).values("service_id", "master_id", "updated_at", "created_by_id")
            }
            conflicts = []
            for service_uuid, master_uuid, enabled in normalised:
                current_present = (service_uuid, master_uuid) in current_rows
                if current_present == enabled:
                    # Current state already matches caller's intent; not
                    # a real conflict — skip.
                    continue
                row = current_rows.get((service_uuid, master_uuid))
                conflicts.append(
                    {
                        "service_id": str(service_uuid),
                        "master_id": str(master_uuid),
                        "current_value": current_present,
                        "your_value": enabled,
                        "changed_by": (
                            str(row["created_by_id"]) if row and row.get("created_by_id") else None
                        ),
                        "changed_at": (
                            row["updated_at"].isoformat() if row and row["updated_at"] else None
                        ),
                    }
                )
            if conflicts:
                return JsonResponse(
                    {"applied": 0, "conflicts": conflicts},
                    status=409,
                )
            # Token stale but no actual diff between request + DB →
            # caller's intent already matches current state. Treat as
            # success with applied=0 + fresh token (idempotent).

    # --- apply + audit (single transaction) -----------------------------
    # Group by master so we emit one audit row per master.
    per_master_added: dict[uuid.UUID, list[uuid.UUID]] = {}
    per_master_removed: dict[uuid.UUID, list[uuid.UUID]] = {}

    # Snapshot current mapping for the affected (master, service) pairs so
    # we audit only the REAL diff (don't emit if caller asks to enable
    # something that's already enabled).
    current_pairs = set(
        MasterService.objects.filter(
            tenant=tenant,
            master_id__in=master_ids,
            service_id__in=service_ids,
        ).values_list("service_id", "master_id")
    )

    applied = 0
    # DRF-975 — the MM4 matrix names itself as the writer. This is the path
    # that already audits (``master.services_changed``, one bundled row per
    # master); the context adds the per-row stamp + per-edge audit row that
    # survive after the bundled row ages out of AuditLog retention, and it is
    # what the model gate requires before it will accept the INSERT.
    with (
        master_service_write(MasterServiceSource.MM4_MATRIX, actor_id=bot_user.id),
        transaction.atomic(),
    ):
        for service_uuid, master_uuid, enabled in normalised:
            pair = (service_uuid, master_uuid)
            currently = pair in current_pairs
            if enabled and not currently:
                MasterService.all_tenants.create(
                    tenant=tenant,
                    master_id=master_uuid,
                    service_id=service_uuid,
                )
                per_master_added.setdefault(master_uuid, []).append(service_uuid)
                applied += 1
            elif (not enabled) and currently:
                MasterService.all_tenants.filter(
                    tenant=tenant,
                    master_id=master_uuid,
                    service_id=service_uuid,
                ).delete()
                per_master_removed.setdefault(master_uuid, []).append(service_uuid)
                applied += 1
            # else: no-op (enabled=True for already-mapped pair, or
            # enabled=False for already-unmapped pair). Don't count in
            # ``applied`` and don't audit.

        # Audit: one event per master with non-empty diff.
        affected_masters = set(per_master_added.keys()) | set(per_master_removed.keys())
        now_iso = timezone.now().isoformat()
        for master_uuid in affected_masters:
            added = per_master_added.get(master_uuid, [])
            removed = per_master_removed.get(master_uuid, [])
            write_audit(
                MASTER_SERVICES_CHANGED,
                target="catalog.CatalogMaster",
                target_id=master_uuid,
                payload={
                    "master_id": str(master_uuid),
                    "actor_id": str(bot_user.id),
                    "actor_role": role_ctx.primary_role,
                    "tenant_id": str(tenant.id),
                    "services_added": [str(s) for s in added],
                    "services_removed": [str(s) for s in removed],
                    "occurred_at": now_iso,
                },
                actor_id=bot_user.id,
            )

    return JsonResponse(
        {
            "applied": applied,
            "conflicts": [],
            "new_snapshot_token": _compute_snapshot_token(tenant.id),
        }
    )


__all__ = [
    "services_mapping_bulk",
    "services_mapping_get",
]
