"""Attribution computation — billable / billing_reason / score.

Implements the **locked billable rule** from the founder Q3 delta:

    billable = (booking_source == 'ai_direct') AND (status reaches CONFIRMED)
    billing_reason is set whenever billable is True (audit trail)
    billable is set once at attribution time, never recomputed

This is a deliberate simplification of the longer rule in
``docs/design/policies/attribution-policy.md`` §6 — the policy adds
``actor_type == 'customer'`` and ``created_by != 'execute_reschedule'``
gates that this rule does NOT enforce. The locked rule is the source of
truth for the BookingRequest writer; the policy file needs an r3
update via decisions-log batch (Q-ATT-LOCKED-1).

Score heuristic: mirrors attribution-policy §5 — pure ``ai_direct``
booking earns 1.00; everything else 0.00 unless the source explicitly
overrides (e.g. ai_assisted writer computes weighted score).

Q12-α continuation chain (issue #478, founder ACK 2026-05-22):
    Reschedules are NOT a billable event when they preserve the
    continuation chain (same service, within 90d of the chain root,
    no prior cancel). When the chain breaks (service swap, >90d,
    partial-failure terminator), the new row IS billable as a fresh
    sale. See :func:`compute_reschedule_continuation` for the decision
    helper and :func:`compute_billable` ``is_reschedule_continuation``
    / ``chain_break_reason`` kwargs for the writer.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, TypedDict
from uuid import UUID

logger = logging.getLogger(__name__)

if TYPE_CHECKING:  # avoid circular import at runtime
    from apps.booking.models import BookingRequest
    from apps.catalog.models import CatalogService


# Q12-α threshold: founder ACK 2026-05-22 — 90 days from chain root
# visit_at. Strictly greater than → break; exactly equal → continuation.
RESCHEDULE_CONTINUATION_THRESHOLD_DAYS = 90


def _normalise_service_id(service_id_value: object) -> str:
    """Coerce a service identifier (UUID / str / int / None) to a
    comparable string. ``None`` → ``""`` so chains where neither old
    nor new has a service FK can pass through.

    Q12-α adversarial-pass B1: the LLM tool path (`apps/skills/booking/
    tools.py::execute_reschedule`) creates rows WITHOUT setting the
    ``service`` FK — so ``booking.service_id`` is None. Pre-fix the
    helper compared ``str(None)`` (= "None") vs ``""``, falsely flagging
    every LLM reschedule as a service_swap → 100 % of LLM reschedules
    silently overbilled. This helper normalises both sides so an
    «old None, new None/""» pair passes the swap check.
    """

    if service_id_value is None:
        return ""
    return str(service_id_value)


def compute_billable(
    *,
    booking_source: str,
    status: str,
    created_by: str = "execute_confirm",
    is_reschedule_continuation: bool = False,
    chain_break_reason: str | None = None,
) -> tuple[bool, str]:
    """Return ``(billable, billing_reason)`` per locked rule.

    ``status`` must be one of :class:`BookingRequest.Status` values
    ('confirmed' / 'cancelled' / 'rescheduled'). The rule fires
    ``billable=True`` only when status is 'confirmed'.

    Q12-α (issue #478): when ``created_by='execute_reschedule'``, the
    caller MUST compute continuation and pass the result via
    ``is_reschedule_continuation`` (+ ``chain_break_reason`` when
    False). The default for a reschedule with neither kwarg set is
    «chain broken» — better to over-charge once than to silently
    undercharge the salon.

    The caller is responsible for never re-running this on an existing
    row — :attr:`BookingRequest.billable` is set at write time and
    frozen.
    """

    # Adversarial-pass D2: refuse contradictory kwargs at function entry
    # — `is_reschedule_continuation=True` AND `chain_break_reason="..."`
    # would silently produce wrong audit. Better to raise loud than to
    # let the True branch win + leave an inconsistent finance row.
    if is_reschedule_continuation and chain_break_reason:
        raise ValueError(
            "compute_billable: is_reschedule_continuation=True is "
            "incompatible with a non-None chain_break_reason "
            f"({chain_break_reason!r}). Caller bug."
        )

    if booking_source != "ai_direct":
        return (False, f"NOT billable: booking_source={booking_source}")
    if status != "confirmed":
        return (False, f"NOT billable: status={status} (must be confirmed)")
    if created_by == "execute_reschedule":
        if is_reschedule_continuation:
            return (False, "reschedule_continuation")
        # Chain broken or caller forgot to compute continuation.
        # Default to «new sale» (billable=True) so we never silently
        # undercharge. Tag the reason so finance can grep.
        reason_tag = chain_break_reason or "missing_continuation_signal"
        # Q12-α #561 (FOLLOW_UP): structured WARN signal when the
        # safe-default «caller forgot to compute continuation» branch
        # fires. Without this log, a regression where a caller starts
        # systematically forgetting the continuation flag would silently
        # overbill customers — visible only to finance during reconciliation.
        #
        # JSON-log shape (matches the project's structured logger):
        #   logger=apps.booking.services.attribution
        #   level=WARNING
        #   msg=billing.q12a.missing_continuation_signal
        #
        # Today: ops greps the log aggregator for the literal
        # ``billing.q12a.missing_continuation_signal``. See
        # ``docs/runbooks/q12a-partial-failure-triage.md`` §«Safe-default
        # signal triage».
        #
        # Future: when a MeterProvider (OTel metrics) or prometheus_client
        # is wired in apps/observability/, add a ``Counter.inc()`` call
        # adjacent to this log so a Prometheus alert rule can fire on
        # sustained non-zero rate. The log line stays as the structured
        # complement (debug-grade context the counter doesn't carry).
        #
        # **Counter name (LOCKED)** — when wiring this metric, use
        # ``billing_q12a_missing_signal_total`` to match the runbook
        # (``docs/runbooks/q12a-partial-failure-triage.md`` §«Future:
        # Prometheus counter») and the original #561 acceptance spec.
        # Any alternative name will silently break the runbook's grep
        # / alert-routing references.
        if reason_tag == "missing_continuation_signal":
            logger.warning(
                "billing.q12a.missing_continuation_signal "
                "booking_source=%s status=%s created_by=%s "
                "is_reschedule_continuation=%s chain_break_reason=%s",
                booking_source,
                status,
                created_by,
                is_reschedule_continuation,
                chain_break_reason,
            )
        return (True, f"reschedule_chain_broken: {reason_tag}")
    return (True, "ai_direct + confirmed: customer-initiated via execute_confirm")


_COMMERCIAL_IDENTITY_FIELDS = (
    "service_id",
    "sticker_price_amount",
    "currency",
    "duration_minutes",
)


class CommercialIdentitySnapshot(TypedDict):
    """Q12-α #541 (founder ACK 2026-05-23) — 5-field shape of the
    commercial-identity snapshot stored on ``BookingRequest`` rows.

    Q12-α #642 follow-up: tightened from a loose ``dict`` return type
    so mypy catches drift — a refactor that renames or removes a key
    used to silently no-op the comparator (which uses ``.get(field)``
    on hardcoded field names) and lose chain-break detection.

    Schema is shared by:
    * :func:`build_live_commercial_identity` — producer (single source
      of truth for snapshot construction).
    * :data:`_COMMERCIAL_IDENTITY_FIELDS` — the 4-field tuple the
      comparator iterates over. ``service_name`` is informational
      (in the snapshot for audit/UI, NOT compared).
    * ``BookingRequest.commercial_identity_snapshot`` JSONField —
      schema-on-read on the DB side.

    Note: TypedDict gives structural typing at static-check time but
    is a plain ``dict`` at runtime — JSONField (de)serialisation is
    unchanged.
    """

    service_id: str
    service_name: str
    sticker_price_amount: str | None
    currency: str
    duration_minutes: int | None


# Q12-α #560 (PRE_PILOT 2026-07-15): explicit ALLOW-lists for status
# gates. Exclude-list pattern (``status != CONFIRMED`` / ``status ==
# CANCELLED``) silently grandfathers ANY future enum addition into
# «reschedulable» / «valid chain root» — a footgun that's invisible at
# code review. ALLOW-list inverts the default: a new enum value is
# rejected by both gates until someone explicitly adds it here. That
# forces a deliberate semantic decision rather than an accidental one.
#
# Read these as documentation of the policy:
#   ``CONFIRMED``  is the only state where a customer can reschedule
#                  their booking (RESCHEDULE_REQUESTED is mid-flight;
#                  CANCEL_REQUESTED is in the 5s undo window; CANCELLED
#                  + RESCHEDULED are terminal).
#   ``CONFIRMED + RESCHEDULED`` are the only states where a row can
#                  serve as the anchor of a continuation chain — a
#                  RESCHEDULED root means «this chain already advanced
#                  once; the original commercial sale is still the
#                  anchor». Interim states (CANCEL_REQUESTED /
#                  RESCHEDULE_REQUESTED) and CANCELLED → chain broken.
def _build_status_allowlists() -> tuple[frozenset[str], frozenset[str]]:
    """Lazy resolution of ``BookingRequest.Status`` — top-level import
    is not safe (Django app-registry boot ordering).
    """

    from apps.booking.models import BookingRequest

    return (
        frozenset({BookingRequest.Status.CONFIRMED}),
        frozenset({BookingRequest.Status.CONFIRMED, BookingRequest.Status.RESCHEDULED}),
    )


# Resolved on first access to avoid Django app-registry boot ordering
# issues; cached after first call. Direct importers (tests, callers)
# should use ``get_reschedulable_statuses()`` /
# ``get_valid_chain_root_statuses()``.
_RESCHEDULABLE_STATUSES_CACHE: frozenset[str] | None = None
_VALID_CHAIN_ROOT_STATUSES_CACHE: frozenset[str] | None = None


def get_reschedulable_statuses() -> frozenset[str]:
    """Return the ALLOW-list of statuses where customer reschedule is
    permitted. Q12-α #560 (PRE_PILOT 2026-07-15)."""

    global _RESCHEDULABLE_STATUSES_CACHE
    if _RESCHEDULABLE_STATUSES_CACHE is None:
        _RESCHEDULABLE_STATUSES_CACHE, _ = _build_status_allowlists()
    return _RESCHEDULABLE_STATUSES_CACHE


def get_valid_chain_root_statuses() -> frozenset[str]:
    """Return the ALLOW-list of statuses where a row may serve as a
    valid chain root. Q12-α #560 (PRE_PILOT 2026-07-15)."""

    global _VALID_CHAIN_ROOT_STATUSES_CACHE
    if _VALID_CHAIN_ROOT_STATUSES_CACHE is None:
        _, _VALID_CHAIN_ROOT_STATUSES_CACHE = _build_status_allowlists()
    return _VALID_CHAIN_ROOT_STATUSES_CACHE


def build_live_commercial_identity(
    service: "CatalogService",
) -> CommercialIdentitySnapshot:
    """Build the 5-field commercial-identity snapshot from a live
    :class:`CatalogService` row. Single source of truth for snapshot
    construction — referenced from ``services/create.py``,
    ``services/reschedule.py``, and ``skills/booking/tools.py``.

    Q12-α #541 (founder ACK 2026-05-23) — schema is locked at 5 fields:
    ``service_id``, ``service_name``, ``sticker_price_amount``,
    ``currency``, ``duration_minutes``. ``service_name`` is informational
    (not compared by the chain comparator); the other 4 are part of
    the chain-break decision via
    :func:`_commercial_identity_changed_field`.

    Field mapping (bot-platform catalog → founder spec):

    * ``service.price_from``  → ``sticker_price_amount`` (string-ified
      Decimal; ``None`` preserved)
    * ``"RUB"``               → ``currency`` (hardcoded for pilot —
      see #617; bot-platform catalog has no currency field at Phase 0
      and the Penza pilot is RUB-only per
      ``project_pricing_model_hybrid``)
    * ``service.duration_min``→ ``duration_minutes`` (int; ``None``
      preserved when service has no configured duration)

    Q12-α #618 (DRY follow-up to #541): extracted from three duplicated
    inline dict-builders. A future schema addition (new field) is now
    a single-site change.

    Q12-α #642 (typing follow-up to #541): return type tightened from
    loose ``dict`` to :class:`CommercialIdentitySnapshot` (TypedDict)
    so mypy catches schema drift at edit time.

    **TRUST BOUNDARY (Q12-α #641 follow-up to #541).** This function is
    module-public but is NOT a request-handler. Callers MUST pass a
    DB-loaded ``CatalogService`` row — never a user-supplied object
    constructed from a REST payload, webhook body, LLM tool argument,
    or any other untrusted source. The returned snapshot is
    billing-affecting (it drives the chain-break decision in
    :func:`compute_reschedule_continuation` via the comparator at
    :func:`_commercial_identity_changed_field`); a forged ``service``
    whose attributes spoof a legitimate row could produce a snapshot
    that bypasses the chain-break check → silent overcharge or
    silent under-charge.

    Forged-object spoof surface: a malicious caller would need to
    fake **4 attribute reads** — ``.id`` (UUID), ``.name`` (str),
    ``.price_from`` (Decimal | None), ``.duration_min`` (int | None).

    If a future caller needs to construct a snapshot from external
    input, add a server-side integrity check that re-fetches the
    matching ``CatalogService`` row by FK and compares it against the
    supplied values BEFORE calling this helper.
    """

    # TODO(#617): hardcoded "RUB" — must be sourced from tenant /
    # service before any non-RUB tenant onboards. PRE_PILOT? No —
    # downgraded to FOLLOW_UP because the pilot scope is Penza/RUB-only
    # (Ayla-first strategic pivot 2026-05-19). Re-elevate when
    # multi-currency expansion (Phase 2+) starts.
    return {
        "service_id": str(service.id),
        "service_name": service.name,
        "sticker_price_amount": (
            str(service.price_from) if service.price_from is not None else None
        ),
        "currency": "RUB",
        "duration_minutes": (int(service.duration_min) if service.duration_min else None),
    }


def _normalise_commercial_identity_field(field: str, value: object) -> object:
    """Normalise a single commercial-identity field for strict comparison.

    Founder spec 2026-05-23: «strict equality + Decimal normalisation».
    Only ``sticker_price_amount`` needs normalisation (Decimal.quantize
    to 2 places — eliminates spurious differences like ``"1500"`` vs
    ``"1500.00"`` which represent the same kopeck-precision price).
    Other fields are string / int — direct equality.
    """

    if value is None:
        return None
    if field == "sticker_price_amount":
        try:
            return Decimal(str(value)).quantize(Decimal("0.01"))
        except (ValueError, ArithmeticError):
            # Malformed price — return raw so the equality compare
            # surfaces the discrepancy (defensive — should never happen
            # from CatalogService.price_from which is DecimalField).
            return value
    return value


def _commercial_identity_changed_field(
    old_snapshot: "CommercialIdentitySnapshot | dict | None",
    new_snapshot: "CommercialIdentitySnapshot | dict | None",
) -> str | None:
    """Compare two commercial-identity snapshots; return the first
    differing field name, or None if identical / either side missing.

    Founder spec 2026-05-23: 4 fields are compared
    (``service_id, sticker_price_amount, currency, duration_minutes``).
    ``service_name`` is informational — captured in the snapshot for
    audit/UI but NOT part of the chain-break decision.

    NULL on either side → return None (skip check). Per memory
    ``q12a-billing-founder-gate``: pre-#541 rows have NULL snapshot,
    chain preserved by policy (legacy chains not retroactively broken).
    """

    if old_snapshot is None or new_snapshot is None:
        return None

    for field in _COMMERCIAL_IDENTITY_FIELDS:
        old_norm = _normalise_commercial_identity_field(field, old_snapshot.get(field))
        new_norm = _normalise_commercial_identity_field(field, new_snapshot.get(field))
        if old_norm != new_norm:
            return field
    return None


def compute_reschedule_continuation(
    *,
    old: "BookingRequest",
    new_service_id: str | UUID | int | None,
    new_visit_at: datetime,
    new_commercial_identity: "CommercialIdentitySnapshot | None" = None,
    threshold_days: int = RESCHEDULE_CONTINUATION_THRESHOLD_DAYS,
) -> tuple[bool, str | None, UUID | None]:
    """Decide whether a reschedule preserves the continuation chain.

    **CONTRACT — caller MUST hold an open ``transaction.atomic()`` block.**
    Per #616 (PRE_PILOT 2026-07-15) this helper issues
    ``select_for_update()`` on the chain root row when walking
    ``old.original_booking_event_id``. Outside ``transaction.atomic()``
    Django raises ``TransactionManagementError``. Both current callers
    (``services/reschedule.py::reschedule_customer_booking`` and
    ``skills/booking/tools.py::execute_reschedule``) wrap their entire
    flow in ``transaction.atomic()`` — any future caller MUST do the
    same. If you're calling this from an analytics backfill, admin
    shell, or management command, wrap in ``transaction.atomic()``
    yourself.

    Per founder ACK 2026-05-22 (issue #478 close-out), the chain is
    preserved when ALL of the following hold:

    * **Same service_id** (strict equality — no category/price-equivalent
      lookup; founder explicit «strict service_id equality»).
    * **Within ``threshold_days`` of chain ROOT visit_at** (not the
      most-recent reschedule — the threshold is anchored to the original
      sale so the customer can't extend a non-billable chain forever by
      rescheduling every 89 days). Default 90.
    * (Implicit) The chain root is reachable — i.e. ``old`` is
      CONFIRMED. The CONFIRMED-only precondition is enforced upstream
      by :func:`apps.booking.services.reschedule.reschedule_customer_booking`;
      this helper trusts the caller.

    The «cancel breaks chain» rule from the founder ACK is enforced
    structurally — a cancelled row can't be the source of a reschedule
    (the service-layer check at ``services/reschedule.py`` rejects
    non-CONFIRMED status), so a continuation can't span a cancel.
    Partial-failure leaves the old row CANCELLED and writes NO new
    row, so there's no continuation surface to break.

    Args:
      old: The existing :class:`BookingRequest` being rescheduled.
        Already locked + verified CONFIRMED by the caller.
      new_service_id: The proposed new service identifier. ``str`` or
        ``int`` or ``UUID`` accepted; compared via ``==`` against
        ``old.service_id`` (the LLM tool path stores strings; the
        service-layer path stores UUIDs — both compare cleanly via
        ``str(...)`` coercion). Pass the value the caller intends to
        write on the new row.
      new_visit_at: The proposed new ``visit_at`` datetime.
      threshold_days: Override for tests. Production callers must use
        the default (90) — the founder-ACK'd threshold.

    Returns:
      ``(is_continuation, chain_break_reason, chain_root_id)``:

      * ``is_continuation`` — ``True`` when all chain-preservation
        conditions hold.
      * ``chain_break_reason`` — short tag (``"service_swap"`` /
        ``"over_90d"``) when False; ``None`` when True.
      * ``chain_root_id`` — the chain root's ``id`` when continuation
        (write this onto the new row's ``original_booking_event_id``);
        ``None`` when chain is broken (write None to start a fresh chain).
    """

    # Resolve the chain root: either ``old`` itself (if ``old`` is the
    # root) or the row ``old.original_booking_event_id`` points at.
    # We trust the FK invariant: a continuation row's ``original_booking_event_id``
    # always points at the ROOT, not at the immediate predecessor —
    # that's what the writers in services/reschedule.py and
    # skills/booking/tools.py enforce. Single-hop chain walk.
    from apps.booking.models import BookingRequest

    if old.original_booking_event_id is not None:
        # ``old`` is itself a continuation — its FK column already points
        # at the chain ROOT (not the immediate predecessor — that's the
        # invariant the writers enforce). Use ``all_tenants`` so a
        # tenant-active flag rotation can't blind the lookup; the
        # caller already holds ``tenant_scope(tenant)``.
        #
        # #616 (PRE_PILOT 2026-07-15) concurrency lock — the root read
        # uses bare ``select_for_update()`` so two parallel reschedules
        # on different links of the same chain serialize on the root
        # row. ``of=`` is omitted intentionally: Postgres-only argument,
        # and our ``.get(id=...)`` has no JOIN to scope, so the default
        # «lock the FROM row» is correct AND keeps SQLite-test parity.
        # Both callers wrap this in ``transaction.atomic()`` so the lock
        # is held for the duration of the comparator. See
        # ``services/reschedule.py`` and ``skills/booking/tools.py`` for
        # the symmetric carry-snapshot lock.
        #
        # FOOT-GUN: do NOT add ``.select_related(...)`` before this
        # ``.get(...)`` without re-evaluating the lock scope — joining
        # a related table expands the FOR UPDATE to those rows on
        # Postgres. If you need related fields, fetch them in a
        # separate query after the lock window.
        try:
            root = BookingRequest.all_tenants.select_for_update().get(
                id=old.original_booking_event_id
            )
        except BookingRequest.DoesNotExist:
            # Root deleted (shouldn't happen under PROTECT — defence
            # in depth). Treat as chain broken. Tech-lead double-pass
            # N2: ERROR-log so ops sees the PROTECT-bypass signal.
            logger.error(
                "billing.q12a.chain_root_missing old_id=%s root_id=%s "
                "tenant_id=%s — PROTECT FK bypassed, treating as new chain",
                old.id,
                old.original_booking_event_id,
                old.tenant_id,
            )
            return (False, "chain_root_missing", None)

        # Adversarial-pass D1: defence-in-depth — `original_booking_event_id`
        # MUST point at a ROOT (i.e. a row whose own
        # ``original_booking_event_id`` is None). If a DB-tampering /
        # buggy writer left a non-root in the FK, the 90d window would
        # be measured from the wrong anchor. Treat as chain broken
        # rather than trust the inconsistent pointer. Tech-lead N2:
        # ERROR-log so ops sees the invariant violation.
        if root.original_booking_event_id is not None:
            logger.error(
                "billing.q12a.chain_root_invariant_violated old_id=%s "
                "claimed_root_id=%s claimed_root.original_booking_event_id=%s "
                "tenant_id=%s — writer/DB corruption suspected",
                old.id,
                root.id,
                root.original_booking_event_id,
                old.tenant_id,
            )
            return (False, "chain_root_invariant_violated", None)
    else:
        root = old

    # Adversarial-pass B2: «cancel breaks chain» applies to the ROOT, not
    # just to ``old``. The structural CONFIRMED check on ``old`` doesn't
    # catch the case where the root has been cancelled (manual data fix,
    # GDPR erasure stub, audit job, etc.) while ``old`` (a non-root
    # link) is still CONFIRMED. Founder rule #1 is explicit — a
    # cancelled chain anywhere terminates the continuation.
    #
    # Q12-α #560 (PRE_PILOT 2026-07-15): converted from exclude-list
    # (``status == CANCELLED``) to ALLOW-list. CANCELLED, CANCEL_REQUESTED,
    # RESCHEDULE_REQUESTED all break the chain; only CONFIRMED + RESCHEDULED
    # are valid roots. Default-deny for any future enum addition.
    if root.status not in get_valid_chain_root_statuses():
        return (False, f"chain_root_invalid_status:{root.status}", None)

    # 1. Strict service equality. Normalise both sides via
    # ``_normalise_service_id`` so the LLM tool path (where rows lack
    # the ``service`` FK — see B1 adversarial-pass note) doesn't
    # falsely register a swap from ``str(None) != ""``. Both sides
    # ``""`` (no FK on either) → no swap, trust the caller's higher-
    # layer enforcement.
    new_svc = _normalise_service_id(new_service_id)
    if _normalise_service_id(old.service_id) != new_svc:
        # ``old.service_id`` is the most-recent link's service; if THAT
        # differs from the proposed new service, the chain breaks here.
        return (False, "service_swap", None)
    if _normalise_service_id(root.service_id) != new_svc:
        # Defence-in-depth: the chain root's service must match too.
        return (False, "service_swap", None)

    # 2. 90-day threshold measured from ROOT visit_at.
    if root.visit_at is None:
        # Legacy row without visit_at can't anchor a chain. Treat as
        # missing root → chain broken (fresh sale). Tech-lead N2:
        # ERROR-log so ops sees that a legacy pre-Phase-1 row got
        # caught in a chain — likely needs data backfill.
        logger.error(
            "billing.q12a.chain_root_no_visit_at root_id=%s tenant_id=%s "
            "— legacy row, fresh-sale fallback",
            root.id,
            root.tenant_id,
        )
        return (False, "chain_root_no_visit_at", None)
    # Adversarial-pass B4: defensive guard against TZ-naive new_visit_at.
    # ``root.visit_at`` is TZ-aware (Django USE_TZ=True). A naive
    # ``new_visit_at`` would raise `TypeError: can't compare offset-
    # naive and offset-aware datetimes` mid-transaction → rollback +
    # split-brain in the LLM tool path (YClients already cancelled).
    # Convert defensively by adopting root's tzinfo if naive; this is
    # safe because the caller's intent is «same wall-clock instant».
    if new_visit_at.tzinfo is None:
        new_visit_at = new_visit_at.replace(tzinfo=root.visit_at.tzinfo)
    # Founder phrasing: «больше чем на 90 дней» = strictly greater.
    # Exactly 90d is continuation; 90d + 1 microsecond breaks.
    #
    # Backward-reschedule semantics (#533 D7): when ``new_visit_at`` is
    # STRICTLY EARLIER than ``root.visit_at`` (customer moved their
    # appointment to a date BEFORE the original sale's visit_at — e.g.
    # rescheduling the May-15 appointment to May-10), this `>`
    # comparison is False → no `over_90d` break → chain continues.
    # This matches the founder intent: a backward move is still a
    # «same booking window» reschedule. Pinned in
    # ``apps/booking/tests/test_attribution.py::test_q12a_533_d7_backward_reschedule_continues_chain``.
    if new_visit_at > root.visit_at + timedelta(days=threshold_days):
        return (False, "over_90d", None)

    # 3. Commercial-identity check (issue #541, founder ACK 2026-05-23).
    # Compare the ROOT's snapshot — not ``old``'s — so a service
    # mutation that happened mid-chain (admin price hike after link2
    # was created) still breaks the chain on the next reschedule. The
    # root snapshot is the «commercial truth» of the original sale.
    #
    # NULL on either side → skip (legacy pre-#541 rows OR caller didn't
    # snapshot at this layer). Per memory q12a-billing-founder-gate.
    root_snapshot = getattr(root, "commercial_identity_snapshot", None)
    changed_field = _commercial_identity_changed_field(root_snapshot, new_commercial_identity)
    if changed_field is not None:
        return (False, f"commercial_identity_changed:{changed_field}", None)

    return (True, None, root.id)


def compute_assist_score(*, booking_source: str) -> Decimal:
    """Return the analytics-only AI assist score for a booking source.

    Pure-ai_direct = 1.00; human/external = 0.00. The ai_assisted
    intermediate value is the writer's responsibility (depends on
    conversation metadata that the booking creation event doesn't carry
    natively — the writer computes and passes in).
    """

    if booking_source == "ai_direct":
        return Decimal("1.00")
    return Decimal("0.00")


def build_customer_attribution_metadata(
    *,
    conversation_id: str | None = None,
    test_mode: bool = False,
    booking_created_at: str | None = None,
    created_by: str = "execute_confirm",
) -> dict:
    """Build the minimal valid ``attribution_metadata`` for a customer
    booking from the Mini App's ``POST /api/v1/customer/bookings``.

    The customer-side writer always uses these values:

    * ``actor_type='customer'`` (per attribution-policy §4)
    * ``created_by='execute_confirm'`` (per attribution-policy §3
      ai_direct recognition rule)
    * ``started_by='customer'`` (customer-initiated by definition)

    Extra keys (``campaign_id``, ``from_inline_button`` per marketing
    Q3 delta) can be merged in by callers — the BookingRequest JSONField
    accepts arbitrary well-formed keys.
    """

    meta: dict = {
        "actor_type": "customer",
        "started_by": "customer",
        "created_by": created_by,
        "test_mode": test_mode,
    }
    if conversation_id is not None:
        meta["conversation_id"] = conversation_id
    if booking_created_at is not None:
        meta["booking_created_at"] = booking_created_at
    return meta


__all__ = [
    "build_customer_attribution_metadata",
    "compute_assist_score",
    "compute_billable",
]
