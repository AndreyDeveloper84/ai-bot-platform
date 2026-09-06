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
      "max_dm_delivery", "max_dm_error", "fallback_link", "invite_link"
    }

``invite_link`` (DRF-1424) is the addition to that envelope: a
``https://max.ru/<bot>?start=master_invite_<token>`` link the owner can
hand over by any route at all. The DM above it can only reach a MAX
username the salon already knows.

``max_dm_error`` (DRF-1505) is the second: the slug of whatever stopped
the DM, so a refusal reaches the person who tapped «Пригласить» and not
only the log. See :func:`_response_payload`.

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

DRF-1507 — «повторно приглашённый» больше не означает «вторая
строка». Формулировка выше («re-issuing intentionally creates a new
PENDING row») описывала поведение до этой правки и была верна ровно до
неё:

* приглашение того же человека с ПРОТУХШИМ или отменённым токеном
  перевыпускается **на существующей строке** — свежий токен, свежие
  семь дней, тот же ``master_id``. Владелец получает рабочую ссылку,
  мастер по ней доходит до кабинета; растёт число приглашений, а не
  число мастеров в ростере;
* приглашение человека, который в салон уже приземлился, отвечает 200
  ``X-Idempotent`` его же строкой и НЕ выписывает второго токена: этот
  токен всё равно привёл бы к сессии первой строки (``onboarding_accept``,
  идемпотентная проба), а выписанная строка осталась бы PENDING навсегда;
* «тот же человек» определяется по нормализованному MAX-хэндлу
  (:mod:`apps.catalog.handles`) или по ``raw["invite_phone"]``. До
  нормализации ``anna_styl`` и ``@anna_styl`` были разными людьми, и
  вторая строка появлялась просто оттого, что владелец в этот раз не
  поставил собаку.

Ограничения уникальности на ``max_handle`` при этом НЕТ — почему,
написано в ``apps/catalog/models.py`` над ``constraints``.

### Side effects (all inside one transaction.atomic)

1. ``CatalogMaster`` row created (or reused via idempotency lookup).
2. ``MasterService`` rows seeded — one per id in ``services[]``.
3. Audit row ``master.invited`` written.

Then, **after the atomic block has committed** (not via
``transaction.on_commit`` — see below): the MAX bot DM is dispatched
with an ``open_app`` button carrying the invite token, and audit row
``master.invite_dispatched`` is written.

Two corrections to what this list used to say, both of which had gone
stale under it: ``WorkingHours`` seeding was removed by DRF-1062, and
the dispatch is a plain synchronous call after the block, not an
``on_commit`` callback — deliberately, so the response can carry the
authoritative dispatch outcome (the inline comment at the call site
explains the trade). What is true either way is that the network call
stays outside the atomic block: inside, it would hold the row lock for
the length of the request and could roll the master row back on a
transient MAX 5xx.

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
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Max
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from apps.admin_api.auth import RoleContext, require_admin_role
from apps.audit.services import write_audit
from apps.catalog.provenance import MasterServiceSource, master_service_write
from apps.catalog.handles import canonical_handle, normalize_handle
from apps.catalog.models import CatalogMaster, CatalogService, MasterService
from apps.channels.bot_context import current_bot
from apps.channels.max import outbound as max_outbound
from apps.events.services import emit
from apps.events.vocabulary import MASTER_INVITE_DISPATCHED, MASTER_INVITED
from apps.identity.models import BotUser
from apps.master_api.auth import generate_invite_token

logger = logging.getLogger(__name__)


INVITE_TTL_DAYS = 7

INVITE_EXTERNAL_ID_FLOOR = 1_000_000
"""Начало диапазона синтетических ``external_id`` для приглашённых мастеров.

``external_id`` у синхронизированных строк принадлежит Ayla; приглашение
своего номера от Ayla не получает и обязано его выдумать, не заняв чужой.
Миллион — граница, ниже которой номера Ayla, выше — наши.
"""

EXTERNAL_ID_MAX_ATTEMPTS = 5
"""Сколько раз повторить вставку, проигравшую гонку за ``external_id``.

Пять, а не один: столько же одновременных приглашений в ОДНОМ салоне
должно совпасть по секунде, чтобы исчерпать попытки. Не бесконечность —
неснимаемый ``IntegrityError`` (например, по другому ограничению) обязан
закончиться ответом, а не циклом внутри запроса.
"""
"""Q-MM2 lock — invite tokens expire 7 days after issuance."""

DEFAULT_SITE_DOMAIN = "http://localhost:5173"
"""Used when settings.SITE_DOMAIN is unset — Vite dev default."""

PILOT_SITE_DOMAIN = "https://miniapp-dev.gobeauty.site"
"""The pilot's Mini App origin — the value ``SITE_DOMAIN`` must carry.

**Not** ``https://api-dev.gobeauty.site``. That is the *backend* host;
it answers 404 on ``/onboarding/master`` and on its own root, because
the route does not exist there. ``/onboarding/master`` is a client-side
route of the Mini App SPA (``apps/miniapp/src/App.tsx``, the
``MasterOnboardingScreen`` element) and is served only from the Mini App
origin — the same origin CI already points the drift check at
(``.github/workflows/miniapp-drift.yml``).

Both earlier statements of this hint named the backend host. Verified by
live request 2026-08-25:

    api-dev.gobeauty.site/onboarding/master?token=...      404
    miniapp-dev.gobeauty.site/onboarding/master?token=...  200

### Three hosts, and this setting names exactly one of them

The contour has three public addresses, and confusing them is how the
wrong one came to be written down twice:

* ``api-dev.gobeauty.site``     — this Django backend, and the Django
  admin, which is why ``DJANGO_CSRF_TRUSTED_ORIGINS`` *does* name it;
* ``miniapp-dev.gobeauty.site`` — the Mini App SPA. **This setting.**
* ``dev.gobeauty.site/api/v1``  — what the mobile apps talk to
  (``packages/shared/src/api/client.ts`` in the mobile repo).

``SITE_DOMAIN`` is the web fallback of a MAX invite, so it is the Mini
App origin and nothing else. Pointing it at either neighbour yields a
link that opens nothing with no error on our side — the same silence
this guard exists to break. Which host is canonical for the product is
an owner question; it does not change what *this* variable means.
"""

SITE_DOMAIN_HINT = (
    f"Set SITE_DOMAIN to the Mini App origin (pilot: {PILOT_SITE_DOMAIN}). "
    "It is NOT the backend host https://api-dev.gobeauty.site — that one "
    "404s on /onboarding/master, the route lives in the Mini App SPA."
)
"""One text, two readers: the deploy log and the runtime ERROR line.

The hint was previously written out twice — here and in
:mod:`apps.admin_api.checks` — and both copies named the wrong host. A
variable gets set once; a hint gets read by everyone who touches this
next. Keeping a single string is what stops the second copy from
drifting back.
"""

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
            "SITE_DOMAIN resolves to %s. %s Until then the admin screen has "
            "no address to show and the invite DM has only its button.",
            _site_domain(),
            SITE_DOMAIN_HINT,
        )
        return ""
    return f"{_site_domain()}/onboarding/master?token={token}"


MASTER_INVITE_PAYLOAD_PREFIX = "master_invite_"
"""Prefix of the ``open_app`` button payload that carries an invitation.

The Mini App declares the same constant under the same name in
``apps/miniapp/src/lib/max-sdk.ts`` and resolves
``master_invite_<uuid>`` to ``/onboarding/master?token=<uuid>``.
``apps/admin_api/tests/test_invite_entry.py`` reads both sources and
fails when they drift — the two halves are in different languages, so
nothing but a test can hold them together.

MAX restricts an ``open_app`` payload to a flat slug: no ``=``, no
``&``, no ``?`` (Guard 3 in ``apps/channels/max/outbound.py`` — a
querystring-shaped payload is answered with HTTP 400 and poisons the
consumer PEL). A UUID's hyphens are fine; the querystring is assembled
on the Mini App side, after the payload has arrived.
"""


def _invite_payload(token: uuid.UUID) -> str:
    """The ``open_app`` payload for one invitation."""

    return f"{MASTER_INVITE_PAYLOAD_PREFIX}{token}"


def _bot_start_link(tenant, token: uuid.UUID) -> str:
    """The shareable start link for this salon's staff bot, or ``""``.

    Thin wrapper over :func:`apps.channels.max.start_links.salon_start_link`
    — the rule about *which* bot a start link may name, and what to do
    when there is none, is shared with the staff access code
    (``views_staff_invite``) and lives in one module so the two cannot
    drift. What stays here is only the payload this endpoint issues.

    Kept as a named function rather than inlined at the call site:
    ``apps/channels/tests/test_salon_web_app_enablement.py`` calls it
    directly to prove the pilot's own configuration produces a link.
    """

    from apps.channels.max.start_links import salon_start_link

    return salon_start_link(tenant, _invite_payload(token))


def _sender_web_app() -> str:
    """Mini App name of the bot this DM will actually be sent as.

    Must be resolved the same way ``max_outbound.send_message`` resolves
    the API token, or the button points at one bot's Mini App while the
    message arrives from another. So this mirrors ``outbound._token()``
    rung for rung: the surrounding ``bot_scope`` first, the legacy global
    second.

    ``admin_api`` enters no ``bot_scope`` today, so in practice this is
    ``MAX_BOT_WEB_APP`` — the same global whose token
    (``MAX_BOT_TOKEN``) actually sends the message. Reading the global
    directly would give the same answer today and the wrong one the day
    the master DM moves to the salon bot: ``.env.staging.template``
    already instructs operators to set ``MAX_BOT_SALON_WEB_APP`` per bot
    rather than the global, so a contour can already be configured where
    the global is empty and the per-bot value is not.
    """

    scoped = current_bot()
    if scoped is not None and scoped.web_app:
        return scoped.web_app
    return getattr(settings, "MAX_BOT_WEB_APP", "")


def _last_dispatch_outcome(master: CatalogMaster) -> tuple[str, str]:
    """What the *previous* dispatch for ``master`` actually reported.

    The idempotent replay used to answer a hardcoded ``"queued"``. That
    was a white lie while every dispatch attempted a send; it stopped
    being one when a dispatch became able to fail without sending at all
    (``no_entry_configured``). The failure mode it would create is the
    one this module is being fixed for: the operator sets the missing
    variable, the owner taps «Пригласить» again, the idempotency probe
    matches the still-PENDING row, and the reply says ``queued`` about a
    message that was never sent and is not being sent now. The only exit
    would be waiting out the 7-day TTL.

    So the replay reports the stored outcome instead of inventing one.
    ``master.invite_dispatched`` is written on every dispatch, successful
    or not, and is the only durable record of what happened — the
    response of the original call is long gone.

    Falls back to ``"queued"`` when no audit row is found, which keeps
    the historical answer for rows created before the audit existed
    rather than inventing a failure.

    Returns ``(delivery, error)``. The second half is why this is a
    tuple: the owner is told *why* a delivery failed (DRF-1505), and on
    the replay path the reason has to come from the same stored row as
    the verdict — otherwise the screen shows «не доставлено» with no
    cause on exactly the repeat tap the owner makes to check.
    ``error`` is ``""`` when the stored row carries none.
    """

    from apps.audit.models import AuditLog

    row = (
        AuditLog.all_tenants.filter(
            tenant_id=master.tenant_id,
            target_id=master.id,
            action=MASTER_INVITE_DISPATCHED,
        )
        .order_by("-created_at")
        .values_list("payload", flat=True)
        .first()
    )
    if isinstance(row, dict) and isinstance(row.get("delivery"), str):
        error = row.get("error") or row.get("reason") or ""
        return row["delivery"], error if isinstance(error, str) else ""
    return "queued", ""


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
    actor_id: uuid.UUID | None = None,
) -> None:
    """Seed the invited master's service set.

    DRF-975: this is a ``bulk_create``, which sends no ``pre_save`` — the gate
    for it lives in ``MasterServiceQuerySet.bulk_create``. Same contract, same
    exception; the context below is what satisfies it. ``actor_id`` is the
    admin who issued the invite, so the seeded edges carry a person and not
    just a machine label — the 2026-07-22 rows had neither.
    """

    if not service_ids:
        return
    # DRF-1507 — приглашение теперь умеет перевыпускаться на существующей
    # строке, и на ней часть услуг уже есть. ``bulk_create`` без этого
    # фильтра положил бы вторую копию каждой связки: у ``MasterService``
    # нет ограничения уникальности по ``(master, service)``, так что
    # дубль был бы не отказом, а тихой парой одинаковых строк.
    already = set(
        MasterService.all_tenants.filter(master=master, service_id__in=service_ids).values_list(
            "service_id", flat=True
        )
    )
    missing = [sid for sid in service_ids if sid not in already]
    if not missing:
        return
    rows = [MasterService(tenant_id=tenant_id, master=master, service_id=sid) for sid in missing]
    with master_service_write(MasterServiceSource.INVITE_SEED, actor_id=actor_id):
        MasterService.all_tenants.bulk_create(rows)


def _dispatch_max_dm(
    *,
    master_id: uuid.UUID,
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

    ### DRF-1349 — why this is a button and not a link

    Until 30.08 this DM carried two addresses and no button, and the
    owner's first live invitation of the pilot went nowhere. Both
    addresses were unreachable *by construction*, not by accident:

    * ``max://bot/<slug>?start=…`` — MAX does not implement the scheme.
      The device answered «Не удалось открыть ссылку. Установите
      браузер на устройстве».
    * ``https://<miniapp>/onboarding/master?token=…`` — opens the
      external browser, and MAX gives a browser no ``initData``. The
      Mini App says «MAX не передал данные для входа», and the backend
      agrees: :func:`apps.master_api.auth.validate_invite_token`
      resolves the token through the tenant of the session's
      ``BotUser``, so with no session there is no tenant to look in.

    A MAX Mini App is entered from a button **on the message** —
    ``{"type": "open_app", "web_app": <bot Mini App name>, "payload":
    <flat slug>}``. That is how the welcome grid already works
    (:mod:`apps.skills.welcome.skill`); the invite simply never built
    one.

    ### The ladder, and why the bottom rung reports failure

    1. ``MAX_BOT_WEB_APP`` set → the button. Nothing else; an https
       address underneath it would just re-offer the path that fails,
       and in a chat any address is one tap away from the browser.
    2. No Mini App name, but a usable ``SITE_DOMAIN`` → the address
       alone, captioned without any promise, plus an ERROR line naming
       the variable to set.
    3. Neither → **``failed``**, and nothing is sent. A message with no
       way into the onboarding is not a delivered invitation, and
       reporting it as ``queued`` would leave the owner reading
       «получит сообщение в течение минуты» about a message that can do
       nothing. Silence indistinguishable from success is the defect
       this whole change exists to remove.

    The text no longer says «Не открывается в MAX? Используйте
    веб-версию». That sentence pointed at the one path that cannot
    work, and on 30.08 the owner followed it.
    """

    if contact_method == "max_phone":
        return {"delivery": "skipped", "reason": "max_phone_lookup_deferred"}

    # max_username — strip leading @ for the MAX REST chat_id param.
    chat_id = contact_value.lstrip("@")
    web_app = _sender_web_app()
    web_url = _fallback_link(token)

    if not web_app and not web_url:
        # Neither a button nor an address: whatever we send, the invited
        # master has no way to act on it. Sending it anyway and reporting
        # `queued` is the failure mode this whole change exists to remove
        # — silence indistinguishable from success.
        #
        # DRF-1505 — the owner DOES see this now. The slug below is
        # returned as `max_dm_error` and the add-person screen renders
        # it as «сообщение не ушло», with the reason, next to the start
        # link the owner can forward instead. Until then this branch's
        # honest outcome reached the audit row and this log line and
        # stopped there, so the screen said «получит сообщение в течение
        # минуты» about a message that was never sent — silence
        # indistinguishable from success, one layer up from the one this
        # branch itself exists to remove.
        logger.error(
            "admin_api.invite.no_entry_configured — invite NOT sent: neither "
            "MAX_BOT_WEB_APP (open_app button) nor a usable SITE_DOMAIN (web "
            "address) is configured, so the message would contain no way into "
            "the onboarding at all. %s",
            SITE_DOMAIN_HINT,
        )
        return {"delivery": "failed", "error": "no_entry_configured"}

    parts = [
        f"Здравствуйте, {master_name}!\n\n",
        f"Салон «{salon_name}» приглашает вас как мастера.\n\n",
    ]
    attachments: list[dict[str, Any]] | None = None

    try:
        if web_app:
            # The only entry that works. A MAX Mini App opens from a
            # button ON the message; an address in the text cannot open
            # it at all.
            #
            # Built inside the try on purpose.
            # `make_inline_keyboard_attachment` raises `ValueError` for a
            # payload MAX would reject (Guard 3 — `=`, `&`, `?`). A UUID
            # cannot trip it today, but this runs AFTER the atomic block
            # committed the master row, so an escape here would answer
            # 500 with a PENDING invite already in the roster and no
            # `master.invite_dispatched` row to say what happened — which
            # then feeds the idempotency probe a token nobody will ever
            # dispatch. One `?src=…` appended to the payload is all it
            # would take.
            attachments = [
                max_outbound.make_inline_keyboard_attachment(
                    [
                        {
                            "label": "Принять приглашение",
                            "callback": _invite_payload(token),
                            "web_app": web_app,
                        }
                    ]
                )
            ]
            parts.append("Нажмите кнопку ниже — анкета откроется прямо здесь, в MAX.\n\n")
        else:
            # Degraded branch — no Mini App name for the sending bot, so
            # no button can be built. The address is all that is left,
            # and it is offered without any promise: opened outside MAX
            # it cannot work, because the Mini App is entered through
            # `initData` that MAX hands only to its own webview, and
            # `validate_invite_token` resolves the token through the
            # tenant of the session's BotUser.
            parts.append(f"Откройте ссылку, не выходя из MAX:\n{web_url}\n\n")
            if not settings.DEBUG:
                # In DEBUG this branch IS the expected local setup — the
                # Vite URL is the right answer and there is no Mini App
                # name to have. An ERROR on every dev invite would train
                # the reader to skip the line on the one contour where it
                # means something.
                logger.error(
                    "admin_api.invite.web_app_unset — invite sent without an "
                    "open_app button: no Mini App name for the sending bot "
                    "(the bot registry entry's `web_app`, else the global "
                    "MAX_BOT_WEB_APP), so the DM carries only a web address, "
                    "and an address opened outside MAX gets no initData."
                )

        parts.append("Приглашение действительно 7 дней.")
        text = "".join(parts)

        max_outbound.send_message(chat_id=chat_id, text=text, attachments=attachments)
    except max_outbound.MaxAPIError as exc:
        logger.warning(
            "admin_api.invite.max_dispatch_failed master_id=%s status=%s",
            master_id,
            exc.status_code,
        )
        return {"delivery": "failed", "error": f"max_status_{exc.status_code}"}
    except Exception:  # noqa: BLE001 — DM dispatch must not crash the request
        logger.exception(
            "admin_api.invite.max_dispatch_unexpected master_id=%s",
            master_id,
        )
        # A fixed slug, not ``str(exc)[:200]``.
        #
        # This used to carry the exception's own text, which was tolerable
        # while it went only into an audit row nobody parsed. DRF-1505
        # published the same value as ``max_dm_error`` — a documented,
        # enumerable field the Mini App reads by prefix — and that changes
        # what it is allowed to contain.
        #
        # It also changes what it is allowed to LEAK.
        # ``make_inline_keyboard_attachment`` raises ``ValueError`` whose
        # message embeds the rejected payload verbatim (Guard 3 in
        # ``apps/channels/max/outbound.py``), and that payload is
        # ``master_invite_<uuid>`` — the invitation credential itself. Its
        # prefix plus a repr of the slug fits inside 200 characters, so
        # the whole token survived the truncation and settled into the
        # audit row, which is a place people look.
        #
        # The detail is not lost: ``logger.exception`` above carries the
        # full traceback to the platform team, who are the only readers it
        # was ever useful to.
        return {"delivery": "failed", "error": "unexpected"}
    return {"delivery": "queued"}


def _response_payload(
    master: CatalogMaster,
    *,
    tenant,
    dispatch_delivery: str,
    dispatch_error: str = "",
) -> dict[str, Any]:
    """Build the 201/200 JSON envelope.

    For ``mode=catalog_only`` (no invite_token) ``invite_token`` and
    ``invite_expires_at`` are null in the response, ``fallback_link``
    and ``invite_link`` are empty, and the caller passes
    ``dispatch_delivery="skipped"``.

    ``invite_link`` (DRF-1424) is the one the owner can hand over by any
    route — see
    :data:`apps.channels.max.start_links.MAX_START_LINK_TEMPLATE`. It is
    a sibling of ``fallback_link``, not a replacement: ``fallback_link``
    is the web address of the Mini App and works only inside MAX's own
    webview, while ``invite_link`` starts the bot from anywhere.

    ``max_dm_error`` (DRF-1505) carries the dispatch's own slug —
    ``no_entry_configured``, ``max_status_404``, ``max_phone_lookup_deferred``
    — instead of leaving it in a log line nobody reads. Until this field
    existed the honest outcome of a refused dispatch reached the audit
    row and the ERROR line but never the person who had just tapped
    «Пригласить»; the screen either said «получит сообщение в течение
    минуты» about a message that was never sent, or said «не удалось»
    with no cause. Empty string when the dispatch has nothing to
    confess, so «is there a problem» is a truth test on one field.

    ``tenant`` is passed rather than read off ``master.tenant``: the
    idempotency path hands us a row fetched without ``select_related``,
    so the attribute would be a lazy query — and the view already holds
    the tenant it authorised the caller against, which is the one that
    must decide the link.
    """

    if master.invite_token is None:
        return {
            "master_id": str(master.id),
            "invite_token": None,
            "invite_expires_at": None,
            "max_dm_delivery": dispatch_delivery,
            "max_dm_error": dispatch_error,
            "fallback_link": "",
            "invite_link": "",
        }
    return {
        "master_id": str(master.id),
        "invite_token": str(master.invite_token),
        "invite_expires_at": master.invite_expires_at.isoformat()
        if master.invite_expires_at is not None
        else None,
        "max_dm_delivery": dispatch_delivery,
        "max_dm_error": dispatch_error,
        "fallback_link": _fallback_link(master.invite_token),
        "invite_link": _bot_start_link(tenant, master.invite_token),
    }


def _contact_matches(master: CatalogMaster, *, contact_method: str, contact_value: str) -> bool:
    """Тот ли это человек, которого сейчас приглашают.

    Для ``max_username`` сравнение идёт по нормализованному хэндлу
    (:func:`apps.catalog.handles.normalize_handle`), а не по сырой строке.
    До DRF-1507 сравнивалось сырое: владелец, набравший во второй раз
    ``anna_styl`` вместо ``@anna_styl``, получал вторую строку на того же
    мастера — и это выглядело как «идемпотентность не сработала», хотя
    сработала ровно так, как написана.

    Для ``max_phone`` ключ лежит в ``raw["invite_phone"]``: колонки
    телефона у ``CatalogMaster`` нет (пробел назван в докстринге модуля).
    Телефон сравнивается как есть — его нормализацию делает валидатор
    тела запроса, и придумывать здесь вторую значило бы завести два
    разных представления одного номера.
    """

    if contact_method == "max_username":
        key = normalize_handle(contact_value)
        return bool(key) and normalize_handle(master.max_handle) == key
    return bool(contact_value) and (master.raw or {}).get("invite_phone") == contact_value


def _is_superseded(master: CatalogMaster) -> bool:
    """Строка, погашенная приземлением в другую строку того же человека.

    Ставит метку ``apps/master_api/views.py::_land_on_glue_row``. Такая
    строка — надгробие: переиспользовать её нельзя, иначе приглашение
    поедет в отменённую половину склейки.
    """

    return bool((master.raw or {}).get("superseded_by_master_id"))


def _person_rows(
    *,
    tenant_id: uuid.UUID,
    contact_method: str,
    contact_value: str,
) -> list[CatalogMaster]:
    """Все строки этого салона, относящиеся к приглашаемому человеку.

    Выборка сужается в базе настолько, насколько ключ это позволяет, а
    нормализованное сравнение доделывается в Python: индекса по
    ``lower(ltrim(max_handle, '@'))`` нет и ради салона на десятки строк
    он не нужен. Порядок — стабильный, чтобы выбор строки не зависел от
    того, как база решила вернуть страницу.
    """

    qs = CatalogMaster.all_tenants.filter(tenant_id=tenant_id)
    if contact_method == "max_username":
        qs = qs.exclude(max_handle="")
    else:
        qs = qs.filter(raw__invite_phone=contact_value)
    rows = [
        m
        for m in qs.order_by("invited_at", "external_id")
        if _contact_matches(m, contact_method=contact_method, contact_value=contact_value)
    ]
    return [m for m in rows if not _is_superseded(m)]


def _already_a_master(rows: list[CatalogMaster]) -> CatalogMaster | None:
    """Строка, в которую этот человек уже приземлился.

    Приглашать её повторно нечем: ``linked_bot_user`` стоит, доступ у
    мастера есть, а второй токен на того же человека — это ровно вторая
    строка, которую задача и запрещает.

    Признак ровно один — ``linked_bot_user``. НЕ ``invite_status ==
    ACCEPTED``: этот статус по умолчанию стоит и у синхронизированных
    строк, и у зеркал ``mode=catalog_only`` (см. ``help_text`` поля —
    иначе они не были бы записываемыми). За ними человека в боте нет, и
    считать их «уже приземлившимися» значило бы отказать владельцу в
    приглашении мастера, которого он видит в каталоге.
    """

    for master in rows:
        if master.linked_bot_user_id is not None:
            return master
    return None


def _reusable_row(rows: list[CatalogMaster]) -> CatalogMaster | None:
    """Строка, на которой можно перевыпустить приглашение.

    DRF-1507 — половина «писателя» ключа ``(tenant, max_handle)``.

    Было: ``master_invite_create`` на протухшем приглашении заводил ВТОРУЮ
    строку с тем же ``max_handle`` намеренно, и это было закреплено тестом
    ``test_expired_invite_creates_new_row`` («Now 2 rows in catalog»).
    Поведение для человека при этом правильное — повторное приглашение
    должно работать; неправильным был способ: новая строка вместо нового
    токена на старой. Через неделю неотвеченных приглашений салон получал
    столько же фантомов в ростере, сколько раз владелец нажал «Пригласить
    ещё раз», и ``resolve_master`` выбирал из них по случайному признаку.

    Стало: перевыпуск на существующей строке. Пользовательское поведение
    сохранено дословно — владелец получает рабочую ссылку, мастер по ней
    доходит до кабинета; изменилось только то, что строка остаётся одна.

    Отбор узкий намеренно: перевыпуск допустим только на строке, которую
    завёл этот же путь и которая никого в бот не пустила —
    ``mode=invite``, ``linked_bot_user`` пуст, приглашение не принято.

    Что сюда НЕ попадает и почему:

    * ``linked_bot_user`` стоит — человек уже в салоне, случай выше;
    * ``mode=catalog_only`` — зеркало каталога, а не приглашение;
      перевыпуск на нём подменил бы смысл строки;
    * ``invite_status=ACCEPTED`` без связи — так выглядит СИНХРОНИЗИРОВАННАЯ
      строка. Перевести её в ``PENDING`` значило бы вынуть работающего
      мастера из записи (``booking/services/create.py`` требует ACCEPTED)
      на всё время, пока он не откроет ссылку. Приглашение такого мастера
      по-прежнему заводит свою строку; сводит их приземление
      (``master_api/views.py::_land_on_glue_row``), когда становится
      известен ``ayla_user_id``.
    """

    for master in rows:
        if master.linked_bot_user_id is not None:
            continue
        if master.mode != CatalogMaster.Mode.INVITE:
            continue
        if master.invite_status == CatalogMaster.InviteStatus.ACCEPTED:
            continue
        return master
    return None


def _idempotency_lookup(
    *,
    tenant_id: uuid.UUID,
    name: str,
    contact_value: str,
    contact_method: str,
) -> CatalogMaster | None:
    """Find an existing PENDING invite within the 7-day TTL window.

    Idempotency key = ``(tenant, name, contact, status=PENDING,
    invite_expires_at > now())`` — «владелец нажал дважды за пять
    секунд», и ответ обязан быть тем же самым, включая тот же токен.

    Отличается от :func:`_reusable_row` тем, что здесь НИЧЕГО не пишется:
    живое приглашение возвращается как есть, с прежним токеном и прежним
    сроком. Перевыпуск — соседний случай, и он не должен молча сбрасывать
    отсчёт семи дней у приглашения, которое ещё действует.

    Two rows with the same key but different ``contact_method`` are
    treated as separate invites — re-sending via phone after a
    username invite was already issued is intentional, not idempotent.
    """

    now = timezone.now()
    candidates = CatalogMaster.all_tenants.filter(
        tenant_id=tenant_id,
        name=name,
        invite_status=CatalogMaster.InviteStatus.PENDING,
        invite_expires_at__gt=now,
    ).order_by("invited_at", "external_id")
    for master in candidates:
        if _is_superseded(master):
            continue
        if _contact_matches(master, contact_method=contact_method, contact_value=contact_value):
            return master
    return None


def _next_external_id(tenant_id: uuid.UUID) -> int:
    """Следующий синтетический ``external_id`` для этого салона.

    DRF-1507, пункт 3 — разрыв Р7.

    Было: ``count(мастеров тенанта) + 1_000_000``. Два одновременных
    приглашения в одном салоне считают одно и то же число, вторая вставка
    ловит ``unique_together (tenant, external_id)``, и вид ловил её общим
    ``except Exception`` — **500 без ретрая**. Хуже: ``count()`` даёт
    одинаковый результат и НЕ одновременно — достаточно удалить строку,
    чтобы следующий номер совпал с уже занятым.

    Стало: ``max`` по нашему же диапазону плюс один, и повтор вставки в
    :func:`master_invite_create`. Уникальность по-прежнему держит база —
    ограничение ``unique_together (tenant, external_id)`` не менялось;
    убран источник гарантированного столкновения и добавлен ответ на
    столкновение случайное.

    ``max`` берётся только по диапазону ``>= INVITE_EXTERNAL_ID_FLOOR``:
    номера синхронизированных строк принадлежат Ayla, и втягивать их в
    свою нумерацию значило бы уезжать вверх на чужой рост.
    """

    highest = CatalogMaster.all_tenants.filter(
        tenant_id=tenant_id,
        external_id__gte=INVITE_EXTERNAL_ID_FLOOR,
    ).aggregate(top=Max("external_id"))["top"]
    if highest is None:
        return INVITE_EXTERNAL_ID_FLOOR
    return int(highest) + 1


def _reissue_invite(
    *,
    master: CatalogMaster,
    name: str,
    max_handle: str,
    raw: dict[str, Any],
    mode: str,
    token: uuid.UUID | None,
    expires: datetime | None,
    invite_status: str,
    now: datetime,
) -> CatalogMaster:
    """Выписать новое приглашение НА СУЩЕСТВУЮЩУЮ строку.

    DRF-1507 — писатель ключа ``(tenant, max_handle)``.

    Пользовательское поведение сохраняется дословно: владелец получает
    свежий токен и рабочую ссылку, мастер по ней доходит до кабинета.
    Меняется только то, что строка остаётся одна — вместо второй с тем же
    ``max_handle``, которую заводил старый путь.

    ``is_active`` не трогается сознательно: строка, на которую
    перевыпускают, доступа не имела (``linked_bot_user`` пуст — это
    условие отбора в :func:`_reusable_row`), а значит уже неактивна;
    решение о том, кто и когда её включает, живёт в DRF-1521 и здесь ему
    не место.

    ``external_id`` не трогается: он уже занят этой строкой, и менять
    номер существующего мастера значит ломать ключ, по которому его
    находит всё остальное.

    ``raw`` сливается, а не заменяется: там могут лежать поля,
    поставленные не этим путём (``invite_phone`` предыдущего
    приглашения, диагностика). Ключи нового приглашения перекрывают
    старые, остальное остаётся.
    """

    master.name = name
    master.mode = CatalogMaster.Mode(mode)
    master.invite_status = invite_status
    master.invite_token = token
    master.invite_expires_at = expires
    master.invited_at = now if token is not None else master.invited_at
    master.external_updated_at = now
    if max_handle:
        master.max_handle = max_handle
    master.raw = {**(master.raw or {}), **raw}
    master.save(
        update_fields=[
            "name",
            "mode",
            "invite_status",
            "invite_token",
            "invite_expires_at",
            "invited_at",
            "external_updated_at",
            "max_handle",
            "raw",
        ]
    )
    logger.info(
        "admin_api.invite.reissued tenant=%s master_id=%s — повторное "
        "приглашение выписано на существующую строку, вторая не заведена "
        "(DRF-1507).",
        master.tenant_id,
        master.id,
    )
    return master


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
    reused: CatalogMaster | None = None
    if mode == "invite":
        existing = _idempotency_lookup(
            tenant_id=tenant.id,
            name=name,
            contact_value=contact_value,
            contact_method=contact_method,
        )
        if existing is not None:
            delivery, delivery_error = _last_dispatch_outcome(existing)
            payload = _response_payload(
                existing,
                tenant=tenant,
                dispatch_delivery=delivery,
                dispatch_error=delivery_error,
            )
            response = JsonResponse(payload, status=200)
            response["X-Idempotent"] = "true"
            return response

        # DRF-1507 — «один человек, одна строка» держит писатель.
        #
        # Живого приглашения нет, но человек в салоне может уже быть: с
        # протухшим приглашением, с отклонённым, или уже приземлившийся.
        # До этой правки все три случая давали ВТОРУЮ строку с тем же
        # ``max_handle``, и ростер салона распухал ровно на число нажатий
        # «Пригласить ещё раз».
        rows = _person_rows(
            tenant_id=tenant.id,
            contact_method=contact_method,
            contact_value=contact_value,
        )
        landed = _already_a_master(rows)
        if landed is not None:
            # Мастер уже в салоне и уже с доступом. Выписывать второй
            # токен не на что: ``onboarding_accept`` вернёт ему сессию
            # ЭТОЙ строки (идемпотентная проба), а выписанная вторая
            # осталась бы PENDING навсегда — фантом из разрыва Р5.
            # ``invite_token`` у неё пуст, поэтому ответ честно несёт
            # ``null`` вместо ссылки: посылать нечего.
            logger.info(
                "admin_api.invite.already_a_master tenant=%s master_id=%s "
                "contact_method=%s — повторное приглашение сведено в "
                "существующую строку вместо второй (DRF-1507).",
                tenant.id,
                landed.id,
                contact_method,
            )
            delivery, delivery_error = _last_dispatch_outcome(landed)
            payload = _response_payload(
                landed,
                tenant=tenant,
                dispatch_delivery=delivery,
                dispatch_error=delivery_error,
            )
            response = JsonResponse(payload, status=200)
            response["X-Idempotent"] = "true"
            return response

        reused = _reusable_row(rows)

    now = timezone.now()
    expires_at = now + timedelta(days=INVITE_TTL_DAYS)

    # Build the master row. ``max_handle`` carries the value for
    # ``max_username`` contact_method; for ``max_phone`` we stash the
    # phone in ``raw["invite_phone"]`` because CatalogMaster has no
    # phone column (gap intentionally documented in the module
    # docstring).
    raw: dict[str, Any] = {}
    max_handle = ""
    if contact_method == "max_username":
        # ``canonical_handle`` — форма хранения, одна на обоих писателей
        # (DRF-1507). Раньше сюда клалось дословно набранное владельцем,
        # и «anna_styl» со «@anna_styl» были разными людьми для проб выше.
        max_handle = canonical_handle(contact_value)
    elif contact_method == "max_phone":
        raw["invite_phone"] = contact_value

    issue_token = mode == "invite"
    token: uuid.UUID | None = generate_invite_token() if issue_token else None
    expires = expires_at if issue_token else None
    invite_status = (
        CatalogMaster.InviteStatus.PENDING if issue_token else CatalogMaster.InviteStatus.ACCEPTED
    )

    # DRF-1507, пункт 3 — вставка повторяется, а не падает 500.
    #
    # ``_next_external_id`` больше не даёт гарантированного столкновения,
    # но два запроса, прочитавшие ``max`` до вставки друг друга, всё ещё
    # получат одно число: гонку нельзя убрать чтением, её можно только
    # разрешить. Уникальность держит база (``unique_together (tenant,
    # external_id)``), а здесь — ответ на её срабатывание: пересчитать и
    # повторить. Каждая попытка в своём ``atomic``: транзакция, поймавшая
    # ``IntegrityError``, дальше непригодна, и повтор внутри неё был бы
    # вторым отказом на том же месте.
    master: CatalogMaster | None = None
    for attempt in range(EXTERNAL_ID_MAX_ATTEMPTS):
        try:
            with transaction.atomic():
                if reused is not None:
                    master = _reissue_invite(
                        master=reused,
                        name=name,
                        max_handle=max_handle,
                        raw=raw,
                        mode=mode,
                        token=token,
                        expires=expires,
                        invite_status=invite_status,
                        now=now,
                    )
                else:
                    master = CatalogMaster.all_tenants.create(
                        tenant=tenant,
                        external_id=_next_external_id(tenant.id),
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
                # This branch manufactured the 10:00-19:00 stub that all
                # four pilot masters now carry — a schedule the salon
                # never set, indistinguishable from one it did. Worse,
                # `apps.scheduling` is not what serves slots: with
                # BOOKING_VIA_AYLA_REST the backend answers, so the rows
                # shaped nothing except the summary line in the master
                # card.
                #
                # `schedule_preset` stays in the invite contract on
                # purpose — it is part of the request shape the admin
                # screen sends. It simply no longer has a side effect.
                # Where a new master's schedule comes from is the
                # schedule window's call.
                _seed_services(
                    tenant_id=tenant.id,
                    master=master,
                    service_ids=service_ids,
                    actor_id=bot_user.id,
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
                        "reused_row": reused is not None,
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
                        "reused_row": reused is not None,
                    },
                )
            break
        except IntegrityError:
            master = None
            if attempt + 1 >= EXTERNAL_ID_MAX_ATTEMPTS:
                logger.exception("admin_api.invite.external_id_exhausted tenant=%s", tenant.id)
                return _error("server_error", "failed to create invite", 500)
            logger.warning(
                "admin_api.invite.external_id_retry tenant=%s attempt=%d — "
                "одновременное приглашение в этом салоне заняло номер; "
                "пересчитываю и повторяю (DRF-1507).",
                tenant.id,
                attempt + 1,
            )
        except Exception:  # noqa: BLE001 — any other error inside atomic → 500 + rollback
            logger.exception("admin_api.invite.create_failed")
            return _error("server_error", "failed to create invite", 500)

    if master is None:  # pragma: no cover — цикл выходит либо break, либо return
        logger.error("admin_api.invite.create_failed tenant=%s — no row after retries", tenant.id)
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

    payload = _response_payload(
        master,
        tenant=tenant,
        dispatch_delivery=outcome["delivery"],
        # `error` for a refusal, `reason` for a deliberate skip. The
        # screen tells them apart by `max_dm_delivery`; what it needs
        # from here is the cause, and both keys are one.
        dispatch_error=str(outcome.get("error") or outcome.get("reason") or ""),
    )
    return JsonResponse(payload, status=201)


__all__ = ["master_invite_create"]
