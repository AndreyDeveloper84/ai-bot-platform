"""Tests for the 4a attribution layer on BookingRequest."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.booking.models import BookingRequest
from apps.booking.services.attribution import (
    build_customer_attribution_metadata,
    compute_assist_score,
    compute_billable,
)
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="att-test", name="Attribution Test")


class TestComputeBillable:
    def test_ai_direct_confirmed_billable(self) -> None:
        billable, reason = compute_billable(booking_source="ai_direct", status="confirmed")
        assert billable is True
        assert "ai_direct" in reason

    def test_ai_direct_cancelled_not_billable(self) -> None:
        billable, reason = compute_billable(booking_source="ai_direct", status="cancelled")
        assert billable is False
        assert "status=cancelled" in reason

    def test_human_direct_not_billable(self) -> None:
        billable, reason = compute_billable(booking_source="human_direct", status="confirmed")
        assert billable is False

    def test_test_admin_not_billable(self) -> None:
        billable, _ = compute_billable(booking_source="test_admin", status="confirmed")
        assert billable is False

    def test_external_not_billable(self) -> None:
        billable, _ = compute_billable(booking_source="external", status="confirmed")
        assert billable is False

    # ---- Q12-α continuation chain (issue #478 — founder ACK 2026-05-22) ----

    def test_q12a_reschedule_without_continuation_kwarg_is_billable(self) -> None:
        """Default path: a fresh ai_direct + CONFIRMED + created_by=execute_confirm
        is billable. Reschedule call sites pass the explicit
        ``is_reschedule_continuation`` flag — when neither is set, the
        behaviour matches the pre-Q12-α default (billable=True)."""

        billable, _ = compute_billable(
            booking_source="ai_direct",
            status="confirmed",
            created_by="execute_confirm",
        )
        assert billable is True

    def test_q12a_continuation_marks_not_billable(self) -> None:
        """Continuation chain (same service, ≤90d, no break): a reschedule's
        new row is NOT billable. ``billing_reason='reschedule_continuation'``
        so finance reports can dedupe these explicitly."""

        billable, reason = compute_billable(
            booking_source="ai_direct",
            status="confirmed",
            created_by="execute_reschedule",
            is_reschedule_continuation=True,
        )
        assert billable is False
        assert reason == "reschedule_continuation"

    def test_q12a_chain_break_service_swap_is_billable(self) -> None:
        """Service swap breaks the chain → new row is billable. Founder
        decision 2026-05-22: «strict service_id equality. Category/price-
        equivalent logic не делаем»."""

        billable, reason = compute_billable(
            booking_source="ai_direct",
            status="confirmed",
            created_by="execute_reschedule",
            is_reschedule_continuation=False,
            chain_break_reason="service_swap",
        )
        assert billable is True
        assert "service_swap" in reason

    def test_q12a_chain_break_over_90d_is_billable(self) -> None:
        """>90 days from chain root visit_at breaks the chain. Founder
        decision 2026-05-22: «90 days from original root visit_at»."""

        billable, reason = compute_billable(
            booking_source="ai_direct",
            status="confirmed",
            created_by="execute_reschedule",
            is_reschedule_continuation=False,
            chain_break_reason="over_90d",
        )
        assert billable is True
        assert "over_90d" in reason

    def test_q12a_reschedule_without_continuation_flag_defaults_to_break(
        self,
    ) -> None:
        """Defence: if a future caller forgets to compute continuation
        (passes ``created_by=execute_reschedule`` without
        ``is_reschedule_continuation``), the safe default is to treat the
        chain as broken (= billable). Better to over-charge a customer
        than to silently undercharge the salon.

        Q12-α #533 D6: tightened from substring match («either
        ``chain_broken`` OR ``missing_continuation_signal``») to exact
        equality. A future relaxation of the safe-default tag would
        otherwise pass silently — finance teams grep for this exact
        literal in audit logs to find «caller bug — should have
        computed continuation» rows. Once #561 (Prometheus counter
        on the safe-default branch) ships, the same literal will
        also drive an alert rule — coordinate any change to this
        string across both sites.
        """

        billable, reason = compute_billable(
            booking_source="ai_direct",
            status="confirmed",
            created_by="execute_reschedule",
        )
        assert billable is True
        # Q12-α #533 D6: exact match — the literal is finance-greppable.
        assert reason == "reschedule_chain_broken: missing_continuation_signal"

    def test_q12a_561_missing_signal_emits_warning_log(self, caplog) -> None:
        """Q12-α #561 (FOLLOW_UP — Variant B): safe-default branch emits
        a structured WARN log so ops can detect the «caller forgot to
        compute continuation» regression via log aggregator grep, even
        before a Prometheus counter is wired (the log is the cheap
        always-on signal; the counter is a future infra add).

        The literal ``billing.q12a.missing_continuation_signal`` is
        load-bearing — it's the grep token in
        ``docs/runbooks/q12a-partial-failure-triage.md``. Any future
        rename MUST update the runbook + memory pin.
        """

        with caplog.at_level("WARNING", logger="apps.booking.services.attribution"):
            compute_billable(
                booking_source="ai_direct",
                status="confirmed",
                created_by="execute_reschedule",
                # NOT passing is_reschedule_continuation — hits the
                # safe-default branch that #561 instruments.
            )

        # Exactly one WARN with the load-bearing literal.
        warns = [
            r
            for r in caplog.records
            if r.levelname == "WARNING" and "billing.q12a.missing_continuation_signal" in r.message
        ]
        assert len(warns) == 1, (
            f"Expected exactly 1 WARN with the missing-continuation-signal "
            f"literal; got {len(warns)}. All records: {caplog.records}"
        )
        # Belt-and-braces: assert all 5 structured fields are present in
        # the log message (friendly-review NICE_TO_HAVE). A future
        # refactor that drops fields would still emit the literal but
        # lose the diagnostic context — this catches that drift.
        msg = warns[0].message
        assert "booking_source=ai_direct" in msg
        assert "status=confirmed" in msg
        assert "created_by=execute_reschedule" in msg
        assert "is_reschedule_continuation=False" in msg
        assert "chain_break_reason=None" in msg

    def test_q12a_561_explicit_chain_break_reason_does_not_log(self, caplog) -> None:
        """Q12-α #561: the WARN signal MUST fire ONLY on the safe-default
        path («caller forgot to compute continuation»). When the caller
        explicitly passes ``chain_break_reason="over_90d"`` (or similar
        legitimate break), no WARN — that's a normal chain-break event,
        not a caller bug. Distinguishes ops triage signal-vs-noise.
        """

        with caplog.at_level("WARNING", logger="apps.booking.services.attribution"):
            compute_billable(
                booking_source="ai_direct",
                status="confirmed",
                created_by="execute_reschedule",
                is_reschedule_continuation=False,
                chain_break_reason="over_90d",  # Legitimate caller signal.
            )

        warns = [
            r for r in caplog.records if "billing.q12a.missing_continuation_signal" in r.message
        ]
        assert warns == [], (
            "Explicit chain_break_reason MUST NOT emit the "
            "missing_continuation_signal WARN — that signal is reserved "
            "for the «caller forgot continuation flag» regression."
        )


class TestComputeAssistScore:
    def test_ai_direct_one(self) -> None:
        assert compute_assist_score(booking_source="ai_direct") == Decimal("1.00")

    def test_human_zero(self) -> None:
        assert compute_assist_score(booking_source="human_direct") == Decimal("0.00")


class TestBuildCustomerAttributionMetadata:
    def test_minimum_fields(self) -> None:
        meta = build_customer_attribution_metadata()
        assert meta["actor_type"] == "customer"
        assert meta["started_by"] == "customer"
        assert meta["created_by"] == "execute_confirm"
        assert meta["test_mode"] is False

    def test_optional_keys(self) -> None:
        meta = build_customer_attribution_metadata(
            conversation_id="abc",
            test_mode=True,
            booking_created_at="2026-05-18T10:00:00Z",
        )
        assert meta["conversation_id"] == "abc"
        assert meta["test_mode"] is True
        assert meta["booking_created_at"] == "2026-05-18T10:00:00Z"


class TestBookingRequestValidator:
    def test_legacy_external_no_validator(self, tenant: Tenant) -> None:
        # Legacy callsite — booking_source defaults to 'external', no actor_type.
        # Validator must NOT fire (transition concession).
        with tenant_scope(tenant):
            BookingRequest.objects.create(
                tenant=tenant,
                service_name="Manicure",
                client_name="Test",
                client_phone="+79991234567",
            )

    def test_ai_direct_requires_actor_type(self, tenant: Tenant) -> None:
        with tenant_scope(tenant):
            with pytest.raises(ValidationError, match="actor_type"):
                BookingRequest.objects.create(
                    tenant=tenant,
                    service_name="Manicure",
                    client_name="Test",
                    client_phone="+79991234567",
                    booking_source="ai_direct",
                )

    def test_ai_direct_invalid_actor_type_rejected(self, tenant: Tenant) -> None:
        with tenant_scope(tenant):
            with pytest.raises(ValidationError, match="actor_type"):
                BookingRequest.objects.create(
                    tenant=tenant,
                    service_name="Manicure",
                    client_name="Test",
                    client_phone="+79991234567",
                    booking_source="ai_direct",
                    attribution_metadata={"actor_type": "intruder"},
                )

    def test_ai_direct_valid_actor_type_passes(self, tenant: Tenant) -> None:
        with tenant_scope(tenant):
            req = BookingRequest.objects.create(
                tenant=tenant,
                service_name="Manicure",
                client_name="Test",
                client_phone="+79991234567",
                visit_at=timezone.now() + timedelta(days=1),
                duration_min=60,
                booking_source="ai_direct",
                billable=True,
                billing_reason="ai_direct + confirmed",
                attribution_metadata={
                    "actor_type": "customer",
                    "created_by": "execute_confirm",
                },
            )
        assert req.booking_source == "ai_direct"
        assert req.billable is True
        assert req.visit_at is not None


# ---------------------------------------------------------------------------
# Q12-α continuation chain — helper unit tests
# ---------------------------------------------------------------------------


class TestComputeRescheduleContinuation:
    """Unit tests for ``compute_reschedule_continuation`` — the chain-
    break decision helper. Issue #478 / founder ACK 2026-05-22.

    The helper takes the OLD booking + the new (service_id, visit_at)
    pair and returns ``(is_continuation, chain_break_reason, chain_root_id)``.
    Pure-function semantics so we test it without DB writes.
    """

    @pytest.fixture
    def tenant(self, db) -> Tenant:
        return Tenant.objects.create(slug="q12a", name="Q12-α Test")

    @pytest.fixture
    def chain_root(self, tenant: Tenant) -> BookingRequest:
        # Fresh ai_direct booking that anchors a chain.
        with tenant_scope(tenant):
            return BookingRequest.objects.create(
                tenant=tenant,
                service_name="Manicure",
                client_name="Test",
                client_phone="+79991234567",
                visit_at=timezone.now() + timedelta(days=7),
                duration_min=60,
                booking_source="ai_direct",
                billable=True,
                billing_reason="ai_direct + confirmed",
                attribution_metadata={
                    "actor_type": "customer",
                    "created_by": "execute_confirm",
                },
            )

    def test_first_reschedule_within_90d_same_service_is_continuation(
        self, chain_root: BookingRequest
    ) -> None:
        """Happy path: customer reschedules within 90d, no service change,
        no prior chain → continuation."""

        from apps.booking.services.attribution import (
            compute_reschedule_continuation,
        )

        is_cont, break_reason, root_id = compute_reschedule_continuation(
            old=chain_root,
            new_service_id=chain_root.service_id,
            new_visit_at=chain_root.visit_at + timedelta(days=10),  # type: ignore[operator,union-attr,arg-type]
        )
        assert is_cont is True
        assert break_reason is None
        assert root_id == chain_root.id

    def test_q12a_533_d7_backward_reschedule_continues_chain(
        self, chain_root: BookingRequest
    ) -> None:
        """Q12-α #533 D7: backward reschedule (``new_visit_at`` < ``root.visit_at``)
        preserves the continuation chain.

        Customer scenario: appointment on May-15 → customer wants to
        move it EARLIER to May-10. The 90d threshold check is
        ``new_visit_at > root.visit_at + 90d`` — strictly greater. With
        a negative timedelta, the comparison is False → no
        ``over_90d`` break → chain continues. This is the founder-
        intended semantics («same booking window»), but pre-#533 was
        not explicitly tested. Pinning so a future refactor (e.g.
        switching to ``abs(delta) > 90d``) is caught loudly.
        """

        from apps.booking.services.attribution import (
            compute_reschedule_continuation,
        )

        is_cont, break_reason, root_id = compute_reschedule_continuation(
            old=chain_root,
            new_service_id=chain_root.service_id,
            # 3 days BEFORE the root's visit_at.
            new_visit_at=chain_root.visit_at - timedelta(days=3),  # type: ignore[operator,union-attr,arg-type]
        )
        assert is_cont is True
        assert break_reason is None
        assert root_id == chain_root.id

    def test_q12a_533_d7_extreme_backward_reschedule_still_continues(
        self, chain_root: BookingRequest
    ) -> None:
        """Q12-α #533 D7 companion: an EXTREME backward reschedule
        (365 days before root.visit_at) is also continuation under
        current code — the helper has NO lower bound on backward
        delta, only the strict-greater upper-bound 90-day check.

        Why pin this separately from the 3-day case: if a future
        founder decision adds an «absolute-delta > 90d breaks chain»
        rule, the 3-day test still passes (3 < 90), but THIS test
        would fail-loud. Coupling the «no backward bound» invariant
        to a single test means an asymmetric refactor can never slip
        through silently.
        """

        from apps.booking.services.attribution import (
            compute_reschedule_continuation,
        )

        is_cont, break_reason, root_id = compute_reschedule_continuation(
            old=chain_root,
            new_service_id=chain_root.service_id,
            # A year BEFORE the root's visit_at — would fail under an
            # `abs(delta) > 90d` rule.
            new_visit_at=chain_root.visit_at - timedelta(days=365),  # type: ignore[operator,union-attr,arg-type]
        )
        assert is_cont is True, (
            "Extreme backward reschedule MUST continue chain under "
            "current rule (only forward >90d breaks). If this fails, "
            "someone added a lower-bound check — review the founder "
            "rationale before unblocking."
        )
        assert break_reason is None
        assert root_id == chain_root.id

    def test_over_90_days_breaks_chain(self, chain_root: BookingRequest) -> None:
        """Founder rule: >90d from chain root visit_at → new sale."""

        from apps.booking.services.attribution import (
            compute_reschedule_continuation,
        )

        is_cont, break_reason, root_id = compute_reschedule_continuation(
            old=chain_root,
            new_service_id=chain_root.service_id,
            new_visit_at=chain_root.visit_at + timedelta(days=91),  # type: ignore[operator,union-attr,arg-type]
        )
        assert is_cont is False
        assert break_reason == "over_90d"
        assert root_id is None

    def test_exactly_90_days_inclusive_is_continuation(self, chain_root: BookingRequest) -> None:
        """Boundary: exactly 90d is still continuation. Strictly-greater
        check (>) matches founder intent «больше чем на 90 дней»."""

        from apps.booking.services.attribution import (
            compute_reschedule_continuation,
        )

        is_cont, break_reason, _ = compute_reschedule_continuation(
            old=chain_root,
            new_service_id=chain_root.service_id,
            new_visit_at=chain_root.visit_at + timedelta(days=90),  # type: ignore[operator,union-attr,arg-type]
        )
        assert is_cont is True
        assert break_reason is None

    def test_service_swap_breaks_chain(self, chain_root: BookingRequest) -> None:
        """Founder rule: strict service_id equality. Different service →
        new sale, even within 90d."""

        from apps.booking.services.attribution import (
            compute_reschedule_continuation,
        )

        is_cont, break_reason, root_id = compute_reschedule_continuation(
            old=chain_root,
            new_service_id="00000000-0000-4000-8000-000000000099",
            new_visit_at=chain_root.visit_at + timedelta(days=10),  # type: ignore[operator,union-attr,arg-type]
        )
        assert is_cont is False
        assert break_reason == "service_swap"
        assert root_id is None

    def test_continuation_of_continuation_walks_to_root(
        self, tenant: Tenant, chain_root: BookingRequest
    ) -> None:
        """If the OLD row is itself a continuation (has ``original_booking_event_id``
        set), the new row's chain root is the SAME root, not the OLD row.
        90-day threshold is measured from the ROOT, not from the most
        recent reschedule. Otherwise a customer could indefinitely
        extend non-billable chains by rescheduling every 89 days."""

        from apps.booking.services.attribution import (
            compute_reschedule_continuation,
        )

        # Second link: 10d after root, points at root.
        with tenant_scope(tenant):
            link2 = BookingRequest.objects.create(
                tenant=tenant,
                service_name="Manicure",
                client_name="Test",
                client_phone="+79991234567",
                visit_at=chain_root.visit_at + timedelta(days=10),  # type: ignore[operator,union-attr,arg-type]
                duration_min=60,
                booking_source="ai_direct",
                billable=False,
                billing_reason="reschedule_continuation",
                original_booking_event=chain_root,
                attribution_metadata={
                    "actor_type": "customer",
                    "created_by": "execute_reschedule",
                },
            )

        # New reschedule from link2, 85d after link2's visit (= 95d after root).
        # Threshold is measured from ROOT visit_at, so this MUST break.
        is_cont, break_reason, _ = compute_reschedule_continuation(
            old=link2,
            new_service_id=chain_root.service_id,
            new_visit_at=link2.visit_at + timedelta(days=85),
        )
        assert is_cont is False
        assert break_reason == "over_90d", (
            "threshold measured from chain root, not most-recent reschedule"
        )

    def test_continuation_chain_root_resolves_to_self_when_no_predecessor(
        self, chain_root: BookingRequest
    ) -> None:
        """When ``old.original_booking_event_id`` is NULL, the old row IS
        the chain root. The helper returns ``old.id`` as the root."""

        from apps.booking.services.attribution import (
            compute_reschedule_continuation,
        )

        _, _, root_id = compute_reschedule_continuation(
            old=chain_root,
            new_service_id=chain_root.service_id,
            new_visit_at=chain_root.visit_at + timedelta(days=1),  # type: ignore[operator,union-attr,arg-type]
        )
        assert root_id == chain_root.id


# ---------------------------------------------------------------------------
# Q12-α — adversarial-pass regression tests (4 blockers + design issues)
# ---------------------------------------------------------------------------


class TestQ12aAdversarialPassRegressions:
    """Regression tests for the 4 blockers + design issues surfaced by
    the adversarial Code Reviewer on PR #526.

    Each maps to a specific catch — see commit message + memory
    `h3-waiver-pattern`.
    """

    @pytest.fixture
    def tenant(self, db) -> Tenant:
        return Tenant.objects.create(slug="q12a-adv", name="Q12-α adversarial")

    def _make_row(
        self,
        tenant: Tenant,
        *,
        visit_at,
        service_id=None,
        original_booking_event=None,
        status="confirmed",
    ) -> BookingRequest:
        with tenant_scope(tenant):
            return BookingRequest.objects.create(
                tenant=tenant,
                service_name="Manicure",
                client_name="Test",
                client_phone="+79991234567",
                visit_at=visit_at,
                duration_min=60,
                status=status,
                booking_source="ai_direct",
                billable=True,
                billing_reason="ai_direct + confirmed",
                attribution_metadata={
                    "actor_type": "customer",
                    "created_by": "execute_confirm",
                },
                service_id=service_id,
                original_booking_event=original_booking_event,
            )

    # ---- B1: LLM tool path — service_id=None on both sides → no swap ----

    def test_b1_llm_path_both_sides_no_service_fk_is_not_swap(self, tenant: Tenant) -> None:
        """Adversarial-pass B1: LLM-created rows have ``service_id=None``
        (no service FK set at write time). Pre-fix the helper compared
        ``str(None)='None'`` vs `""` → 100% of LLM reschedules silently
        registered as service_swap, breaking the entire feature on the
        dominant production path. Fix: ``_normalise_service_id`` coerces
        None → "", so «both sides None» passes the equality check.
        """

        from apps.booking.services.attribution import (
            compute_reschedule_continuation,
        )

        old = self._make_row(tenant, visit_at=timezone.now() + timedelta(days=7), service_id=None)
        is_cont, break_reason, root_id = compute_reschedule_continuation(
            old=old,
            new_service_id="",  # LLM tool passes "" when booking.service_id is None
            new_visit_at=old.visit_at + timedelta(days=10),  # type: ignore[operator,union-attr,arg-type]
        )
        assert is_cont is True, (
            "LLM tool path: both sides no service FK must pass swap check; "
            f"got break_reason={break_reason!r}"
        )
        assert break_reason is None
        assert root_id == old.id

    def test_b1_asymmetric_service_id_is_swap(self, tenant: Tenant) -> None:
        """Safety: when one side has a service identifier and the other
        does not, that IS still a swap. Defensive — better to over-charge
        once than to silently extend a chain across mismatched services.

        Mirror scenario: row has no FK (LLM path) + caller supplies a
        new non-empty service id (e.g. catalog migration brought a
        service in). Helper detects mismatch.
        """

        from apps.booking.services.attribution import (
            compute_reschedule_continuation,
        )

        old = self._make_row(
            tenant,
            visit_at=timezone.now() + timedelta(days=7),
            service_id=None,
        )
        is_cont, break_reason, _ = compute_reschedule_continuation(
            old=old,
            new_service_id="some-different-service-id",
            new_visit_at=old.visit_at + timedelta(days=10),  # type: ignore[operator,union-attr,arg-type]
        )
        assert is_cont is False
        assert break_reason == "service_swap"

    # ---- B2: chain root cancelled breaks the chain ----

    def test_b2_cancelled_root_breaks_chain_even_when_old_is_confirmed(
        self, tenant: Tenant
    ) -> None:
        """Adversarial-pass B2: founder rule #1 («cancel breaks chain»)
        applies to the ROOT, not just to ``old``. The structural
        CONFIRMED-only check on ``old`` doesn't catch the case where
        the chain root was cancelled (admin data fix, GDPR erasure,
        audit job) while ``old`` is still CONFIRMED. Without this fix,
        a cancelled chain root silently extends non-billable chain
        forever via subsequent reschedules of intermediate links.
        """

        from apps.booking.services.attribution import (
            compute_reschedule_continuation,
        )

        root_visit = timezone.now() + timedelta(days=7)
        root = self._make_row(
            tenant,
            visit_at=root_visit,
            service_id=None,
            status="cancelled",  # Cancelled root.
        )
        link2 = self._make_row(
            tenant,
            visit_at=root_visit + timedelta(days=5),
            service_id=None,
            original_booking_event=root,
            status="confirmed",
        )

        is_cont, break_reason, root_id = compute_reschedule_continuation(
            old=link2,
            new_service_id=None,
            new_visit_at=root_visit + timedelta(days=10),
        )
        assert is_cont is False
        # Q12-α #560: reason flipped from exclude-list literal
        # ``chain_root_cancelled`` to the ALLOW-list form
        # ``chain_root_invalid_status:<status>``. The status name is
        # embedded so finance + ops can grep specific terminal states.
        assert break_reason == "chain_root_invalid_status:cancelled"
        assert root_id is None

    # ---- B4: timezone-naive new_visit_at must not crash the batch ----

    def test_b4_naive_new_visit_at_coerced_via_root_tzinfo(self, tenant: Tenant) -> None:
        """Adversarial-pass B4: pre-fix a naive ``new_visit_at`` against
        a TZ-aware ``root.visit_at`` raised ``TypeError`` mid-helper,
        propagating into the reschedule transaction → rollback → in
        the LLM tool path the old row stays CONFIRMED while YClients-
        side is already cancelled → split-brain. Fix: helper adopts
        root's tzinfo defensively.
        """

        from apps.booking.services.attribution import (
            compute_reschedule_continuation,
        )

        old = self._make_row(
            tenant,
            visit_at=timezone.now() + timedelta(days=7),
            service_id=None,
        )
        # Naive datetime — strip tzinfo.
        naive = (old.visit_at + timedelta(days=10)).replace(tzinfo=None)  # type: ignore[operator,union-attr,arg-type]

        # MUST NOT raise TypeError.
        is_cont, break_reason, _ = compute_reschedule_continuation(
            old=old,
            new_service_id=None,
            new_visit_at=naive,
        )
        # The helper coerced naive → tz-aware via root.tzinfo; same-
        # day plus 10 is well within 90d → continuation.
        assert is_cont is True
        assert break_reason is None

    # ---- D1: chain-root invariant enforcement ----

    def test_d1_chain_root_invariant_violation_treated_as_broken(self, tenant: Tenant) -> None:
        """Adversarial-pass D1: writers MUST set
        ``original_booking_event_id`` to the ROOT (a row whose own
        ``original_booking_event_id`` is None). If a buggy writer or
        DB tampering leaves a non-root in the FK, the 90d window would
        be measured from the wrong anchor. Defence: helper detects the
        invariant violation and returns chain_broken instead of trusting
        the inconsistent pointer.
        """

        from apps.booking.services.attribution import (
            compute_reschedule_continuation,
        )

        root_visit = timezone.now() + timedelta(days=7)
        true_root = self._make_row(
            tenant,
            visit_at=root_visit,
            service_id=None,
        )
        # link2 INCORRECTLY points at... another link2-like row that
        # itself has original_booking_event_id set. Simulate the bug.
        fake_root = self._make_row(
            tenant,
            visit_at=root_visit + timedelta(days=2),
            service_id=None,
            original_booking_event=true_root,
        )
        compromised = self._make_row(
            tenant,
            visit_at=root_visit + timedelta(days=5),
            service_id=None,
            original_booking_event=fake_root,  # NOT a root!
        )

        is_cont, break_reason, _ = compute_reschedule_continuation(
            old=compromised,
            new_service_id=None,
            new_visit_at=root_visit + timedelta(days=10),
        )
        assert is_cont is False
        assert break_reason == "chain_root_invariant_violated"

    # ---- D2: contradiction guard on compute_billable ----

    def test_d2_contradictory_kwargs_raise(self) -> None:
        """Adversarial-pass D2: contradictory kwargs should not silently
        produce wrong audit — raise loud."""

        with pytest.raises(ValueError, match="incompatible"):
            compute_billable(
                booking_source="ai_direct",
                status="confirmed",
                created_by="execute_reschedule",
                is_reschedule_continuation=True,
                chain_break_reason="over_90d",
            )

    # ---- Tech-lead double-pass round-2 — N2 ERROR-log signals ----

    def test_n2_chain_root_missing_logs_error(self, tenant: Tenant, caplog, monkeypatch) -> None:
        """Tech-lead N2: `chain_root_missing` is a PROTECT-FK-bypass
        signal — must surface as ERROR for ops, not silent fallthrough.

        SQLite enforces FK integrity even on raw UPDATE, so we can't
        truly simulate a PROTECT bypass at the DB level. Instead we
        mock the manager's ``.get()`` to raise ``DoesNotExist`` — the
        exact branch the helper handles for the «PROTECT was bypassed»
        defence.
        """

        from apps.booking.services.attribution import (
            compute_reschedule_continuation,
        )

        root = self._make_row(tenant, visit_at=timezone.now() + timedelta(days=7), service_id=None)
        old = self._make_row(
            tenant,
            visit_at=root.visit_at + timedelta(days=5),  # type: ignore[operator,union-attr,arg-type]
            service_id=None,
            original_booking_event=root,
        )

        # Monkeypatch the manager to simulate PROTECT-bypassed root.
        # Q12-α #616 (2026-05-24): root read is now
        # ``select_for_update().get()``, so we patch select_for_update
        # to return a stub queryset whose .get raises.
        from apps.booking.models import BookingRequest as BR

        class _MissingRootQS:
            def get(self, *args, **kwargs):  # noqa: ANN001
                raise BR.DoesNotExist("simulated PROTECT bypass")

        monkeypatch.setattr(BR.all_tenants, "select_for_update", lambda *a, **kw: _MissingRootQS())

        with caplog.at_level("ERROR", logger="apps.booking.services.attribution"):
            is_cont, break_reason, _ = compute_reschedule_continuation(
                old=old,
                new_service_id=None,
                new_visit_at=old.visit_at + timedelta(days=5),  # type: ignore[operator,union-attr,arg-type]
            )

        assert is_cont is False
        assert break_reason == "chain_root_missing"
        assert any("chain_root_missing" in rec.message for rec in caplog.records)

    def test_n2_chain_root_invariant_violated_logs_error(self, tenant: Tenant, caplog) -> None:
        """Tech-lead N2: `chain_root_invariant_violated` is a writer/DB
        corruption signal — must ERROR-log for forensic audit."""

        from apps.booking.services.attribution import (
            compute_reschedule_continuation,
        )

        root_visit = timezone.now() + timedelta(days=7)
        true_root = self._make_row(tenant, visit_at=root_visit, service_id=None)
        fake_root = self._make_row(
            tenant,
            visit_at=root_visit + timedelta(days=2),
            service_id=None,
            original_booking_event=true_root,
        )
        compromised = self._make_row(
            tenant,
            visit_at=root_visit + timedelta(days=5),
            service_id=None,
            original_booking_event=fake_root,
        )

        with caplog.at_level("ERROR", logger="apps.booking.services.attribution"):
            is_cont, break_reason, _ = compute_reschedule_continuation(
                old=compromised,
                new_service_id=None,
                new_visit_at=root_visit + timedelta(days=10),
            )

        assert is_cont is False
        assert break_reason == "chain_root_invariant_violated"
        assert any("chain_root_invariant_violated" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# #541 — Commercial identity snapshot (founder ACK 2026-05-23)
# ---------------------------------------------------------------------------


class TestCommercialIdentitySnapshot:
    """4-field commercial-identity comparator: service_id +
    sticker_price_amount + currency + duration_minutes. Founder spec
    2026-05-23: chain continues ONLY if all 4 unchanged. NULL snapshot
    on either side → skip check (preserves legacy chains per memory
    `q12a-billing-founder-gate`).
    """

    @pytest.fixture
    def tenant(self, db) -> Tenant:
        return Tenant.objects.create(slug="ci-snap", name="Commercial-id snap")

    def _row(
        self,
        tenant: Tenant,
        *,
        visit_at,
        snapshot=None,
        original_booking_event=None,
        status="confirmed",
    ) -> BookingRequest:

        with tenant_scope(tenant):
            return BookingRequest.objects.create(
                tenant=tenant,
                service_name="Manicure",
                client_name="Test",
                client_phone="+79991234567",
                visit_at=visit_at,
                duration_min=60,
                status=status,
                booking_source="ai_direct",
                billable=True,
                billing_reason="ai_direct + confirmed",
                attribution_metadata={
                    "actor_type": "customer",
                    "created_by": "execute_confirm",
                },
                service_id=None,
                original_booking_event=original_booking_event,
                commercial_identity_snapshot=snapshot,
            )

    def _snapshot(
        self,
        *,
        service_id="9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
        service_name="Маникюр",
        sticker_price_amount="1500.00",
        currency="RUB",
        duration_minutes=60,
    ) -> dict:
        return {
            "service_id": service_id,
            "service_name": service_name,
            "sticker_price_amount": sticker_price_amount,
            "currency": currency,
            "duration_minutes": duration_minutes,
        }

    # ---- Happy path: 4 fields unchanged → continuation ----

    def test_identity_unchanged_continues_chain(self, tenant: Tenant) -> None:
        from apps.booking.services.attribution import (
            compute_reschedule_continuation,
        )

        snap = self._snapshot()
        old = self._row(tenant, visit_at=timezone.now() + timedelta(days=7), snapshot=snap)

        is_cont, break_reason, root_id = compute_reschedule_continuation(
            old=old,
            new_service_id=None,
            new_visit_at=old.visit_at + timedelta(days=10),  # type: ignore[operator,union-attr,arg-type]
            new_commercial_identity=self._snapshot(),  # Identical.
        )
        assert is_cont is True
        assert break_reason is None
        assert root_id == old.id

    # ---- 4 break-vectors per field ----

    def test_price_changed_breaks_chain(self, tenant: Tenant) -> None:
        from apps.booking.services.attribution import (
            compute_reschedule_continuation,
        )

        old = self._row(
            tenant,
            visit_at=timezone.now() + timedelta(days=7),
            snapshot=self._snapshot(sticker_price_amount="1500.00"),
        )

        is_cont, break_reason, _ = compute_reschedule_continuation(
            old=old,
            new_service_id=None,
            new_visit_at=old.visit_at + timedelta(days=10),  # type: ignore[operator,union-attr,arg-type]
            new_commercial_identity=self._snapshot(sticker_price_amount="5000.00"),
        )
        assert is_cont is False
        assert break_reason is not None
        assert "sticker_price_amount" in break_reason

    def test_currency_changed_breaks_chain(self, tenant: Tenant) -> None:
        from apps.booking.services.attribution import (
            compute_reschedule_continuation,
        )

        old = self._row(
            tenant,
            visit_at=timezone.now() + timedelta(days=7),
            snapshot=self._snapshot(currency="RUB"),
        )

        is_cont, break_reason, _ = compute_reschedule_continuation(
            old=old,
            new_service_id=None,
            new_visit_at=old.visit_at + timedelta(days=10),  # type: ignore[operator,union-attr,arg-type]
            new_commercial_identity=self._snapshot(currency="USD"),
        )
        assert is_cont is False
        assert break_reason is not None
        assert "currency" in break_reason

    def test_duration_changed_breaks_chain(self, tenant: Tenant) -> None:
        from apps.booking.services.attribution import (
            compute_reschedule_continuation,
        )

        old = self._row(
            tenant,
            visit_at=timezone.now() + timedelta(days=7),
            snapshot=self._snapshot(duration_minutes=60),
        )

        is_cont, break_reason, _ = compute_reschedule_continuation(
            old=old,
            new_service_id=None,
            new_visit_at=old.visit_at + timedelta(days=10),  # type: ignore[operator,union-attr,arg-type]
            new_commercial_identity=self._snapshot(duration_minutes=90),
        )
        assert is_cont is False
        assert break_reason is not None
        assert "duration_minutes" in break_reason

    def test_service_id_in_snapshot_changed_breaks_chain(self, tenant: Tenant) -> None:
        """`service_id` is one of the 4 compared fields too — captured
        in the snapshot at write time so a service rename + relink
        (rare admin op) breaks the chain even when the row's local
        `service_id` field happens to match."""

        from apps.booking.services.attribution import (
            compute_reschedule_continuation,
        )

        old = self._row(
            tenant,
            visit_at=timezone.now() + timedelta(days=7),
            snapshot=self._snapshot(service_id="aaa11111-1111-4111-8111-111111111111"),
        )

        is_cont, break_reason, _ = compute_reschedule_continuation(
            old=old,
            new_service_id=None,
            new_visit_at=old.visit_at + timedelta(days=10),  # type: ignore[operator,union-attr,arg-type]
            new_commercial_identity=self._snapshot(
                service_id="bbb22222-2222-4222-8222-222222222222"
            ),
        )
        assert is_cont is False
        assert break_reason is not None
        assert "service_id" in break_reason

    # ---- service_name informational only ----

    def test_service_name_renamed_does_not_break_chain(self, tenant: Tenant) -> None:
        """Founder spec: «service_name informational, не сравнивается».
        Salon renames «Маникюр» → «Маникюр Premium» without changing
        price/duration/currency → chain continues."""

        from apps.booking.services.attribution import (
            compute_reschedule_continuation,
        )

        old = self._row(
            tenant,
            visit_at=timezone.now() + timedelta(days=7),
            snapshot=self._snapshot(service_name="Маникюр"),
        )

        is_cont, break_reason, _ = compute_reschedule_continuation(
            old=old,
            new_service_id=None,
            new_visit_at=old.visit_at + timedelta(days=10),  # type: ignore[operator,union-attr,arg-type]
            new_commercial_identity=self._snapshot(
                service_name="Маникюр Premium"  # ← renamed but otherwise identical
            ),
        )
        assert is_cont is True
        assert break_reason is None

    # ---- NULL snapshot — skip check ----

    def test_null_snapshot_on_old_skips_check(self, tenant: Tenant) -> None:
        """Legacy pre-#541 rows have NULL `commercial_identity_snapshot`.
        Per memory `q12a-billing-founder-gate`: chains rooted before
        deploy are NOT backfilled. Skip the check to preserve those
        chains (safer than retroactive break of already-billed rows)."""

        from apps.booking.services.attribution import (
            compute_reschedule_continuation,
        )

        old = self._row(
            tenant,
            visit_at=timezone.now() + timedelta(days=7),
            snapshot=None,  # ← legacy
        )

        is_cont, break_reason, _ = compute_reschedule_continuation(
            old=old,
            new_service_id=None,
            new_visit_at=old.visit_at + timedelta(days=10),  # type: ignore[operator,union-attr,arg-type]
            new_commercial_identity=self._snapshot(sticker_price_amount="9999.00"),
        )
        assert is_cont is True
        assert break_reason is None

    def test_null_snapshot_on_new_skips_check(self, tenant: Tenant) -> None:
        """Caller forgot to pass `new_commercial_identity` (None). Old
        path → skip the check, preserve chain continuity at this layer.
        Service-layer caller is responsible for passing the snapshot
        when available."""

        from apps.booking.services.attribution import (
            compute_reschedule_continuation,
        )

        old = self._row(
            tenant,
            visit_at=timezone.now() + timedelta(days=7),
            snapshot=self._snapshot(),
        )

        is_cont, break_reason, _ = compute_reschedule_continuation(
            old=old,
            new_service_id=None,
            new_visit_at=old.visit_at + timedelta(days=10),  # type: ignore[operator,union-attr,arg-type]
            new_commercial_identity=None,  # ← caller didn't snapshot
        )
        assert is_cont is True
        assert break_reason is None

    # ---- Decimal normalisation ----

    def test_price_decimal_normalised_boundary(self, tenant: Tenant) -> None:
        """`Decimal('1500.00')` vs `Decimal('1500')` quantize to same
        2-decimal value → no spurious break. Founder spec: «strict
        equality + Decimal normalisation»."""

        from apps.booking.services.attribution import (
            compute_reschedule_continuation,
        )

        old = self._row(
            tenant,
            visit_at=timezone.now() + timedelta(days=7),
            snapshot=self._snapshot(sticker_price_amount="1500.00"),
        )

        is_cont, break_reason, _ = compute_reschedule_continuation(
            old=old,
            new_service_id=None,
            new_visit_at=old.visit_at + timedelta(days=10),  # type: ignore[operator,union-attr,arg-type]
            new_commercial_identity=self._snapshot(
                sticker_price_amount="1500"  # ← no trailing zeros
            ),
        )
        assert is_cont is True
        assert break_reason is None

    def test_price_sub_kopeck_difference_breaks_chain(self, tenant: Tenant) -> None:
        """Sub-kopeck differences (rare but possible from external
        rounding) DO break chain after quantize. 1500.00 vs 1500.01 →
        different at 2-decimal precision → break."""

        from apps.booking.services.attribution import (
            compute_reschedule_continuation,
        )

        old = self._row(
            tenant,
            visit_at=timezone.now() + timedelta(days=7),
            snapshot=self._snapshot(sticker_price_amount="1500.00"),
        )

        is_cont, break_reason, _ = compute_reschedule_continuation(
            old=old,
            new_service_id=None,
            new_visit_at=old.visit_at + timedelta(days=10),  # type: ignore[operator,union-attr,arg-type]
            new_commercial_identity=self._snapshot(sticker_price_amount="1500.01"),
        )
        assert is_cont is False
        assert break_reason is not None
        assert "sticker_price_amount" in break_reason


class TestStatusAllowLists:
    """Q12-α #560 (PRE_PILOT 2026-07-15): explicit ALLOW-list constants
    for status gates. Pinning tests so a future enum addition can't
    silently invalidate the gate — the test FAILS LOUDLY whenever
    ``BookingRequest.Status`` gains a new value, forcing whoever adds
    it to update the ALLOW-list explicitly.
    """

    def test_reschedulable_statuses_is_exactly_confirmed(self) -> None:
        from apps.booking.services.attribution import get_reschedulable_statuses

        assert get_reschedulable_statuses() == frozenset({BookingRequest.Status.CONFIRMED})

    def test_valid_chain_root_statuses_is_exactly_confirmed_plus_rescheduled(self) -> None:
        from apps.booking.services.attribution import get_valid_chain_root_statuses

        assert get_valid_chain_root_statuses() == frozenset(
            {BookingRequest.Status.CONFIRMED, BookingRequest.Status.RESCHEDULED}
        )

    def test_every_status_enum_value_classified(self) -> None:
        """If a developer adds a new ``Status`` enum value without
        updating the ALLOW-lists, this test fails — directing them to
        decide whether the new status is reschedulable / valid as chain
        root. Default-deny behaviour ships safely; this is a
        documentation-only nudge.
        """

        from apps.booking.services.attribution import (
            get_reschedulable_statuses,
            get_valid_chain_root_statuses,
        )

        all_statuses = frozenset(s.value for s in BookingRequest.Status)
        # Snapshot the lists as of #560. If the enum changes, this set
        # also changes and the assertion below pushes the developer to
        # update the allow-lists deliberately.
        expected_known = frozenset(
            {
                BookingRequest.Status.CONFIRMED,
                BookingRequest.Status.CANCEL_REQUESTED,
                BookingRequest.Status.RESCHEDULE_REQUESTED,
                BookingRequest.Status.CANCELLED,
                BookingRequest.Status.RESCHEDULED,
            }
        )
        assert all_statuses == expected_known, (
            "BookingRequest.Status changed — review ALLOW-lists in "
            "apps/booking/services/attribution.py:_build_status_allowlists "
            "to decide if the new status is reschedulable / valid root, "
            "AND update docs/design/policies/attribution-policy.md §6.5 "
            "per-status verdict table to keep doc in sync with code."
        )

        # Sanity: ALLOW-lists are subsets of the enum (no orphaned values).
        assert get_reschedulable_statuses() <= all_statuses
        assert get_valid_chain_root_statuses() <= all_statuses

    @pytest.mark.parametrize(
        "root_status,expected_continuation,expected_reason_fragment",
        [
            ("confirmed", True, None),  # Happy path: CONFIRMED root continues.
            ("rescheduled", True, None),  # RESCHEDULED root also valid.
            ("cancelled", False, "chain_root_invalid_status:cancelled"),
            (
                "cancel_requested",
                False,
                "chain_root_invalid_status:cancel_requested",
            ),
            (
                "reschedule_requested",
                False,
                "chain_root_invalid_status:reschedule_requested",
            ),
        ],
    )
    def test_chain_root_status_gate_per_enum_value(
        self,
        tenant: Tenant,
        root_status: str,
        expected_continuation: bool,
        expected_reason_fragment: str | None,
    ) -> None:
        """Parametrized check: every current ``Status`` enum value
        produces the expected continuation verdict at the root gate.
        """

        from apps.booking.services.attribution import compute_reschedule_continuation

        root_visit = timezone.now() + timedelta(days=7)
        with tenant_scope(tenant):
            root = BookingRequest.objects.create(
                tenant=tenant,
                service_name="Test",
                client_name="Test",
                client_phone="+79991234567",
                visit_at=root_visit,
                duration_min=60,
                status=root_status,
                booking_source="ai_direct",
                billable=True,
                billing_reason="root",
                attribution_metadata={
                    "actor_type": "customer",
                    "created_by": "execute_confirm",
                },
                service_id=None,
            )
            link = BookingRequest.objects.create(
                tenant=tenant,
                service_name="Test",
                client_name="Test",
                client_phone="+79991234567",
                visit_at=root_visit + timedelta(days=5),
                duration_min=60,
                status="confirmed",
                booking_source="ai_direct",
                billable=False,
                billing_reason="link",
                attribution_metadata={
                    "actor_type": "customer",
                    "created_by": "execute_reschedule",
                },
                service_id=None,
                original_booking_event=root,
            )

        is_cont, break_reason, _ = compute_reschedule_continuation(
            old=link,
            new_service_id=None,
            new_visit_at=root_visit + timedelta(days=10),
        )

        assert is_cont is expected_continuation
        if expected_reason_fragment is None:
            assert break_reason is None
        else:
            assert break_reason == expected_reason_fragment

    def test_rescheduled_root_continues_chain_under_new_semantics(self, tenant: Tenant) -> None:
        """New positive case under #560: a RESCHEDULED root (the natural
        post-reschedule terminal state on the original sale row) still
        anchors a continuation chain. Under the old exclude-list this
        worked accidentally (only CANCELLED was rejected); under the
        new ALLOW-list this is explicit and load-bearing.
        """

        from apps.booking.services.attribution import compute_reschedule_continuation

        root_visit = timezone.now() + timedelta(days=7)
        with tenant_scope(tenant):
            root = BookingRequest.objects.create(
                tenant=tenant,
                service_name="Test",
                client_name="Test",
                client_phone="+79991234567",
                visit_at=root_visit,
                duration_min=60,
                status=BookingRequest.Status.RESCHEDULED,
                booking_source="ai_direct",
                billable=True,
                billing_reason="root",
                attribution_metadata={
                    "actor_type": "customer",
                    "created_by": "execute_confirm",
                },
                service_id=None,
            )
            link = BookingRequest.objects.create(
                tenant=tenant,
                service_name="Test",
                client_name="Test",
                client_phone="+79991234567",
                visit_at=root_visit + timedelta(days=5),
                duration_min=60,
                status=BookingRequest.Status.CONFIRMED,
                booking_source="ai_direct",
                billable=False,
                billing_reason="link",
                attribution_metadata={
                    "actor_type": "customer",
                    "created_by": "execute_reschedule",
                },
                service_id=None,
                original_booking_event=root,
            )

        is_cont, break_reason, root_id = compute_reschedule_continuation(
            old=link,
            new_service_id=None,
            new_visit_at=root_visit + timedelta(days=10),
        )

        assert is_cont is True
        assert break_reason is None
        assert root_id == root.id
