"""Admin master-invite endpoint — POST /api/v1/admin/masters/invite (PR 3 / MM2).

Issues a fresh CatalogMaster invite for a salon owner or admin.

### Backend contract (verbatim from master-management handoff §Backend contract)

The master-management handoff (lines 458-489) defines the request /
response envelope. This module implements the master-role-only subset:

    POST /api/v1/admin/masters/invite
    Body: {
      "name", "contact_method", "contact_value",
      "services": [<service_uuid>...],
      "schedule_preset": "default_mon_fri_10_19" | "none",
      "mode": "invite" | "catalog_only"
    }

    Response 201: {
      "master_id", "invite_token", "invite_expires_at",
      "max_dm_delivery", "fallback_link"
    }

### Scope cuts (separate PRs)

* **Admin / Receptionist invite** — writes :class:`TenantStaff`, NOT
  :class:`CatalogMaster`. Different model + lifecycle; lands in a
  separate PR. The handoff's ``role`` field is therefore not accepted
  in this PR (master is the implicit + only role).
* **Email fallback** — needs SMTP/SES backend; deferred.
* **Re-invite / cancel-invite** — separate endpoints.
* **``schedule_preset == custom``** — needs the schedule-editor UI to
  land first.

### Idempotency

The endpoint dedupes within a 7-day window on
``(tenant, name, contact_value)`` where ``invite_status=PENDING``. A
second call returns the EXISTING row (200, not 201) with header
``X-Idempotent: true``. This means a salon owner who taps "Send invite"
twice in 5 seconds (network glitch) does NOT get two PENDING rows in
the roster.

Cancelled or accepted invites do NOT block a fresh invite — the
contact may have been re-hired or the previous invite mis-sent.
Expired invites also DON'T block: re-issuing intentionally creates a
new PENDING row (the old one stays around as historical audit; the new
token is what gets dispatched).

### Side effects (all inside one transaction.atomic)

1. ``CatalogMaster`` row created (or reused via idempotency lookup).
2. ``WorkingHours`` seeded — 5 Mon-Fri rows 10:00-19:00 when
   ``schedule_preset == "default_mon_fri_10_19"``.
3. ``MasterService`` rows seeded — one per id in ``services[]``.
4. Audit row ``master.invited`` written.
5. Post-commit (``transaction.on_commit``) — MAX bot DM dispatched
   with the invite deeplink + audit row ``master.invite_dispatched``
   written.

The on-commit dispatch follows the same pattern as
:mod:`apps.booking.services.transitions` — a network call inside the
atomic block would block the row lock for the duration of the request
and might trigger rollback on a transient MAX 5xx.

### CatalogMaster.phone — not stored

The spec accepts ``contact_method="max_phone"``. :class:`CatalogMaster`
has no ``phone`` field today; the phone value lives only in the
:class:`apps.identity.models.BotUser` table once the master accepts.
For Phase 1 we accept ``max_phone`` and store the value in
``raw["invite_phone"]`` so it's recoverable for ops + the future re-
invite endpoint can use it. The MAX DM dispatch path for
``max_phone`` is also DEFERRED (we don't have a phone→chat lookup
yet) and the response carries ``max_dm_delivery="skipped"``.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import timedelta
from typing import Any
from urllib.parse import urlsplit

from django.conf import settings
from django.db import transaction
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from apps.admin_api.auth import RoleContext, require_admin_role
from apps.audit.services import write_audit
from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.channels.max import outbound as max_outbound
from apps.events.services import emit
from apps.events.vocabulary import MASTER_INVITE_DISPATCHED, MASTER_INVITED
from apps.identity.models import BotUser
from apps.master_api.auth import generate_invite_token

logger = logging.getLogger(__name__)


INVITE_TTL_DAYS = 7
"""Q-MM2 lock — invite tokens expire 7 days after issuance."""

DEFAULT_SITE_DOMAIN = "http://localhost:5173"
"""Used when settings.SITE_DOMAIN is unset — Vite dev default."""

LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0", "[::1]", "::1")
"""Hosts that mean «this developer machine» and nobody else's.

A web fallback pointing at one of these is not a degraded link, it is a
dead end on every device except the one that generated it.
"""

ALLOWED_CONTACT_METHODS = {"max_username", "max_phone"}
"""``email`` deferred to a follow-up PR (needs SMTP backend)."""

ALLOWED_SCHEDULE_PRESETS = {"default_mon_fri_10_19", "none"}
"""``custom`` deferred — needs the schedule-editor UI to land first."""

ALLOWED_MODES = {"invite", "catalog_only"}
"""Per ``CatalogMaster.Mode`` enum. Other modes don't exist."""

MAX_NAME_LEN = 200
MAX_CONTACT_VALUE_LEN = 128


# --- helpers --------------------------------------------------------------


def _error(slug: str, detail: str, status: int) -> JsonResponse:
    return JsonResponse({"error": slug, "detail": detail}, status=status)


def _parse_json_body(request: HttpRequest) -> dict[str, Any] | JsonResponse:
    if not request.body:
        return _error("bad_request", "request body is required", 400)
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return _error("bad_request", "invalid JSON body", 400)
    if not isinstance(data, dict):
        return _error("bad_request", "JSON body must be an object", 400)
    return data


def _site_domain() -> str:
    host = getattr(settings, "SITE_DOMAIN", "") or DEFAULT_SITE_DOMAIN
    if "://" not in host:
        host = f"https://{host}"
    return host.rstrip("/")


def _site_domain_is_loopback() -> bool:
    """True when the configured domain only resolves on the dev machine."""

    host = urlsplit(_site_domain()).hostname or ""
    return host.lower() in LOOPBACK_HOSTS


def _bot_slug(tenant_slug: str) -> str:
    return getattr(settings, "MASTER_BOT_USERNAME", "") or f"{tenant_slug}_bot"


def _fallback_link(token: uuid.UUID) -> str:
    """Web fallback URL, or ``""`` when it would point at localhost.

    DRF-1079 — on the pilot ``SITE_DOMAIN`` is not set at all, so the
    repository default (``http://localhost:5173``,
    ``config/settings/base.py:537``) is what got embedded into every
    invite DM and every API response. That link opens nothing on the
    phone of the person it was sent to.

    Returning an empty string rather than the localhost URL is the
    point of the fix: a missing fallback is a visible gap, a fallback
    to localhost is a working-looking link that wastes the invited
    master's attempt and tells nobody. The ERROR line names the exact
    variable to set, because the failure is otherwise silent — it lives
    in a DM the platform team never sees.

    In DEBUG the localhost link is the correct answer and is returned
    unchanged; the whole guard is off there.
    """

    if not settings.DEBUG and _site_domain_is_loopback():
        logger.error(
            "admin_api.invite.site_domain_unset — web fallback suppressed: "
            "SITE_DOMAIN resolves to %s. Set SITE_DOMAIN to the Mini App "
            "origin (pilot: https://api-dev.gobeauty.site) or invited "
            "masters get a link that opens nothing.",
            _site_domain(),
        )
        return ""
    return f"{_site_domain()}/onboarding/master?token={token}"


def _deeplink(tenant_slug: str, token: uuid.UUID) -> str:
    return f"max://bot/{_bot_slug(tenant_slug)}?start=master_invite_{token}"


def _validate_body(body: dict[str, Any]) -> tuple[dict[str, Any], JsonResponse | None]:
    """Centralised request-body validation.

    Returns (clean, error). Either ``clean`` is the normalised payload
    or ``error`` is the JSON response.
    """

    # name
    name = body.get("name")
    if not isinstance(name, str) or not name.strip():
        return {}, _error("bad_request", "name is required", 400)
    name = name.strip()
    if len(name) > MAX_NAME_LEN:
        return {}, _error("bad_request", f"name exceeds {MAX_NAME_LEN} chars", 400)

    # contact_method
    contact_method = body.get("contact_method")
    if contact_method not in ALLOWED_CONTACT_METHODS:
        return {}, _error(
            "bad_request",
            f"contact_method must be one of {sorted(ALLOWED_CONTACT_METHODS)} "
            "(email is deferred to a separate PR)",
            400,
        )

    # contact_value
    contact_value = body.get("contact_value")
    if not isinstance(contact_value, str) or not contact_value.strip():
        return {}, _error("bad_request", "contact_value is required", 400)
    contact_value = contact_value.strip()
    if len(contact_value) > MAX_CONTACT_VALUE_LEN:
        return {}, _error(
            "bad_request",
            f"contact_value exceeds {MAX_CONTACT_VALUE_LEN} chars",
            400,
        )

    # mode (default: invite)
    mode = body.get("mode", "invite")
    if mode not in ALLOWED_MODES:
        return {}, _error(
            "bad_request",
            f"mode must be one of {sorted(ALLOWED_MODES)}",
            400,
        )

    # schedule_preset (default: default_mon_fri_10_19)
    schedule_preset = body.get("schedule_preset", "default_mon_fri_10_19")
    if schedule_preset not in ALLOWED_SCHEDULE_PRESETS:
        return {}, _error(
            "bad_request",
            f"schedule_preset must be one of {sorted(ALLOWED_SCHEDULE_PRESETS)} "
            "('custom' is deferred — needs schedule editor UI)",
            400,
        )

    # services (optional list of uuid strings)
    services_raw = body.get("services") or []
    if not isinstance(services_raw, list):
        return {}, _error("bad_request", "services must be a list", 400)
    services: list[uuid.UUID] = []
    for entry in services_raw:
        if not isinstance(entry, str):
            return {}, _error("bad_request", "services entries must be UUID strings", 400)
        try:
            services.append(uuid.UUID(entry))
        except (TypeError, ValueError):
            return {}, _error(
                "bad_request",
                f"service {entry!r} is not a valid UUID",
                400,
            )

    # role guard — this endpoint CREATES a catalog master. Granting access
    # to a person is a different operation with a different write
    # (TenantStaff, or a link on an existing master), and it lives at
    # POST /api/v1/admin/staff/invite/ (DRF-1061 block 2.4).
    #
    # Kept separate rather than folded in: merging them would produce one
    # endpoint whose required fields depend on a role flag and whose
    # "create" is sometimes a create and sometimes a link.
    role = body.get("role", "master")
    if role != "master":
        return {}, _error(
            "bad_request",
            "this endpoint creates a catalog master; to grant access to a "
            "person use POST /api/v1/admin/staff/invite/ with the desired role",
            400,
        )

    return (
        {
            "name": name,
            "contact_method": contact_method,
            "contact_value": contact_value,
            "mode": mode,
            "schedule_preset": schedule_preset,
            "services": services,
        },
        None,
    )


def _validate_services_in_tenant(
    tenant_id: uuid.UUID,
    service_ids: list[uuid.UUID],
) -> JsonResponse | None:
    """Cross-tenant guard — every service UUID must belong to ``tenant``.

    Returns 400 with the offending ID surfaced in the detail string.
    Returns None on success.
    """

    if not service_ids:
        return None
    found = set(
        CatalogService.all_tenants.filter(tenant_id=tenant_id, id__in=service_ids).values_list(
            "id", flat=True
        )
    )
    missing = [str(sid) for sid in service_ids if sid not in found]
    if missing:
        return _error(
            "bad_request",
            f"service not in tenant: {missing[0]} (and {len(missing) - 1} more)"
            if len(missing) > 1
            else f"service not in tenant: {missing[0]}",
            400,
        )
    return None


def _seed_services(
    *,
    tenant_id: uuid.UUID,
    master: CatalogMaster,
    service_ids: list[uuid.UUID],
) -> None:
    if not service_ids:
        return
    rows = [
        MasterService(tenant_id=tenant_id, master=master, service_id=sid) for sid in service_ids
    ]
    MasterService.all_tenants.bulk_create(rows)


def _dispatch_max_dm(
    *,
    master_id: uuid.UUID,
    tenant_slug: str,
    contact_value: str,
    contact_method: str,
    token: uuid.UUID,
    master_name: str,
    salon_name: str,
) -> dict[str, Any]:
    """Send the invite DM via MAX. Returns dispatch outcome dict.

    The outcome dict has keys ``delivery`` (``queued`` / ``failed`` /
    ``skipped``) and optionally ``error`` (str).

    ``skipped`` covers the ``max_phone`` case — we don't have a
    phone→chat lookup pipeline in Phase 1, so we acknowledge the
    spec contract but punt the actual delivery to the follow-up PR.
    """

    if contact_method == "max_phone":
        return {"delivery": "skipped", "reason": "max_phone_lookup_deferred"}

    # max_username — strip leading @ for the MAX REST chat_id param.
    chat_id = contact_value.lstrip("@")
    deeplink = _deeplink(tenant_slug, token)
    web_url = _fallback_link(token)
    parts = [
        f"Здравствуйте, {master_name}!\n\n",
        f"Салон «{salon_name}» приглашает вас как мастера. ",
        "Откройте ссылку в MAX, чтобы продолжить:\n",
        f"{deeplink}\n\n",
    ]
    # DRF-1079 — the web-fallback block only when there IS a fallback.
    # `_fallback_link` returns "" when the configured domain is loopback,
    # and «Используйте веб-версию:» followed by nothing reads as a
    # delivery bug rather than as the misconfiguration it is.
    if web_url:
        parts.append(f"Не открывается в MAX? Используйте веб-версию:\n{web_url}\n\n")
    parts.append("Ссылка действительна 7 дней.")
    text = "".join(parts)

    try:
        max_outbound.send_message(chat_id=chat_id, text=text)
    except max_outbound.MaxAPIError as exc:
        logger.warning(
            "admin_api.invite.max_dispatch_failed master_id=%s status=%s",
            master_id,
            exc.status_code,
        )
        return {"delivery": "failed", "error": f"max_status_{exc.status_code}"}
    except Exception as exc:  # noqa: BLE001 — DM dispatch must not crash the request
        logger.exception(
            "admin_api.invite.max_dispatch_unexpected master_id=%s",
            master_id,
        )
        return {"delivery": "failed", "error": str(exc)[:200]}
    return {"delivery": "queued"}


def _response_payload(master: CatalogMaster, *, dispatch_delivery: str) -> dict[str, Any]:
    """Build the 201/200 JSON envelope.

    For ``mode=catalog_only`` (no invite_token) ``invite_token`` and
    ``invite_expires_at`` are null in the response, ``fallback_link``
    is empty, and the caller passes ``dispatch_delivery="skipped"``.
    """

    if master.invite_token is None:
        return {
            "master_id": str(master.id),
            "invite_token": None,
            "invite_expires_at": None,
            "max_dm_delivery": dispatch_delivery,
            "fallback_link": "",
        }
    return {
        "master_id": str(master.id),
        "invite_token": str(master.invite_token),
        "invite_expires_at": master.invite_expires_at.isoformat()
        if master.invite_expires_at is not None
        else None,
        "max_dm_delivery": dispatch_delivery,
        "fallback_link": _fallback_link(master.invite_token),
    }


def _idempotency_lookup(
    *,
    tenant_id: uuid.UUID,
    name: str,
    contact_value: str,
) -> CatalogMaster | None:
    """Find an existing PENDING invite within the 7-day TTL window.

    Idempotency key = ``(tenant, name, contact_value, status=PENDING,
    invite_expires_at > now())``. We match on ``max_handle`` because
    that's where ``max_username`` contact_values are stored. For
    ``max_phone`` we also match via ``raw["invite_phone"]`` (set when
    the original row was created).

    Two rows with the same key but different ``contact_method`` are
    treated as separate invites — re-sending via phone after a
    username invite was already issued is intentional, not idempotent.
    """

    now = timezone.now()
    qs = CatalogMaster.all_tenants.filter(
        tenant_id=tenant_id,
        name=name,
        invite_status=CatalogMaster.InviteStatus.PENDING,
        invite_expires_at__gt=now,
    )
    by_handle = qs.filter(max_handle=contact_value).first()
    if by_handle is not None:
        return by_handle
    # Phone-stored case — `raw["invite_phone"]`. SQLite (test) accepts
    # the JSON lookup the same as Postgres.
    return qs.filter(raw__invite_phone=contact_value).first()


# --- POST /api/v1/admin/masters/invite ------------------------------------


@csrf_exempt
@require_http_methods(["POST"])
@require_admin_role
def master_invite_create(request: HttpRequest) -> HttpResponse:
    """Issue a fresh master invite. See module docstring for the contract."""

    tenant = request.tenant  # type: ignore[attr-defined]
    role_ctx: RoleContext = request.role_context  # type: ignore[attr-defined]
    bot_user: BotUser = request.bot_user  # type: ignore[attr-defined]

    parsed = _parse_json_body(request)
    if isinstance(parsed, JsonResponse):
        return parsed
    clean, err = _validate_body(parsed)
    if err is not None:
        return err

    name = clean["name"]
    contact_method = clean["contact_method"]
    contact_value = clean["contact_value"]
    mode = clean["mode"]
    service_ids = clean["services"]
    # `schedule_preset` is validated in `_validate_invite_body` and echoed
    # back, but is no longer read here: seeding was removed (DRF-1062). The
    # field stays in the contract; it just has no side effect.

    # Cross-tenant guard for services.
    service_err = _validate_services_in_tenant(tenant.id, service_ids)
    if service_err is not None:
        return service_err

    # Idempotency probe — only relevant for ``mode=invite``. The
    # ``catalog_only`` row has no token + carries different semantics
    # (the spec allows multiple "catalog-only mirror" rows per same
    # name); we skip idempotency lookup for it.
    if mode == "invite":
        existing = _idempotency_lookup(tenant_id=tenant.id, name=name, contact_value=contact_value)
        if existing is not None:
            payload = _response_payload(existing, dispatch_delivery="queued")
            response = JsonResponse(payload, status=200)
            response["X-Idempotent"] = "true"
            return response

    now = timezone.now()
    expires_at = now + timedelta(days=INVITE_TTL_DAYS)
    next_external_id = CatalogMaster.all_tenants.filter(tenant=tenant).count() + 1_000_000

    # Build the master row. ``max_handle`` carries the value for
    # ``max_username`` contact_method; for ``max_phone`` we stash the
    # phone in ``raw["invite_phone"]`` because CatalogMaster has no
    # phone column (gap intentionally documented in the module
    # docstring).
    raw: dict[str, Any] = {}
    max_handle = ""
    if contact_method == "max_username":
        max_handle = contact_value
    elif contact_method == "max_phone":
        raw["invite_phone"] = contact_value

    issue_token = mode == "invite"
    token: uuid.UUID | None = generate_invite_token() if issue_token else None
    expires = expires_at if issue_token else None
    invite_status = (
        CatalogMaster.InviteStatus.PENDING if issue_token else CatalogMaster.InviteStatus.ACCEPTED
    )

    try:
        with transaction.atomic():
            master = CatalogMaster.all_tenants.create(
                tenant=tenant,
                external_id=next_external_id,
                external_updated_at=now,
                name=name,
                is_active=False,
                invite_status=invite_status,
                invite_token=token,
                invite_expires_at=expires,
                invited_at=now if issue_token else None,
                max_handle=max_handle,
                mode=CatalogMaster.Mode(mode),
                raw=raw,
            )

            # DRF-1062: no working-hours seeding here any more.
            #
            # This branch manufactured the 10:00-19:00 stub that all four
            # pilot masters now carry — a schedule the salon never set,
            # indistinguishable from one it did. Worse, `apps.scheduling`
            # is not what serves slots: with BOOKING_VIA_AYLA_REST the
            # backend answers, so the rows shaped nothing except the
            # summary line in the master card.
            #
            # `schedule_preset` stays in the invite contract on purpose —
            # it is part of the request shape the admin screen sends. It
            # simply no longer has a side effect. Where a new master's
            # schedule comes from is the schedule window's call.
            _seed_services(
                tenant_id=tenant.id,
                master=master,
                service_ids=service_ids,
            )

            write_audit(
                MASTER_INVITED,
                target="catalog.CatalogMaster",
                target_id=master.id,
                payload={
                    "master_id": str(master.id),
                    "actor_id": str(bot_user.id),
                    "actor_role": role_ctx.primary_role,
                    "role": "master",
                    "contact_method": contact_method,
                    "mode": mode,
                    "services_count": len(service_ids),
                    "idempotent": False,
                },
                actor_id=bot_user.id,
            )

            emit(
                MASTER_INVITED,
                properties={
                    "master_id": str(master.id),
                    "actor_role": role_ctx.primary_role,
                    "contact_method": contact_method,
                    "mode": mode,
                    "services_count": len(service_ids),
                },
            )
    except Exception:  # noqa: BLE001 — any error inside atomic → 500 + rollback
        logger.exception("admin_api.invite.create_failed")
        return _error("server_error", "failed to create invite", 500)

    # Dispatch the MAX DM AFTER the atomic block has committed the
    # master + side-effect rows. Failure here MUST NOT roll back the
    # master row (it exists, the dispatch is observable via the audit
    # row + response). The dispatch helper already swallows MAX errors
    # via ``MaxAPIError``. We then write the second audit row +
    # event so the dispatch outcome is forensically attached to the
    # same master_id.
    #
    # Note: doing this synchronously (rather than via ``on_commit``
    # which would fire AFTER the view return) lets the response
    # carry the authoritative dispatch outcome. The cost is an extra
    # ~10s timeout budget on the request — acceptable for a "create
    # invite" admin action that the owner is actively waiting on.
    if not issue_token:
        # mode=catalog_only — no dispatch attempt; audit as skipped.
        outcome: dict[str, Any] = {"delivery": "skipped", "reason": "catalog_only_mode"}
    else:
        assert token is not None  # narrow for type-checker
        outcome = _dispatch_max_dm(
            master_id=master.id,
            tenant_slug=tenant.slug,
            contact_value=contact_value,
            contact_method=contact_method,
            token=token,
            master_name=master.name,
            salon_name=tenant.name,
        )

    dispatch_audit_payload: dict[str, Any] = {
        "master_id": str(master.id),
        "channel": "max",
        "delivery": outcome["delivery"],
    }
    if "error" in outcome:
        dispatch_audit_payload["error"] = outcome["error"]
    if "reason" in outcome:
        dispatch_audit_payload["reason"] = outcome["reason"]
    # Re-enter tenant_scope is unnecessary — require_admin_role wraps
    # the entire view in tenant_scope. write_audit reads it.
    write_audit(
        MASTER_INVITE_DISPATCHED,
        target="catalog.CatalogMaster",
        target_id=master.id,
        payload=dispatch_audit_payload,
        actor_id=bot_user.id,
    )
    emit(
        MASTER_INVITE_DISPATCHED,
        properties={
            "master_id": str(master.id),
            "channel": "max",
            "delivery": outcome["delivery"],
        },
    )

    payload = _response_payload(master, dispatch_delivery=outcome["delivery"])
    return JsonResponse(payload, status=201)


__all__ = ["master_invite_create"]
