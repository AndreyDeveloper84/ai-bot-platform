"""B-R — the account reset command (DRF-1617).

The one test that matters is the red line on a NON-empty account (§8 of the
measurement): completeness is red before the reset and green after it. A
green check on an account with no traces proves nothing and is not here.
"""

from __future__ import annotations

import io
import uuid

import pytest
from django.apps import apps
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.catalog.master_state import is_enrolled, sale_block
from apps.catalog.models import CatalogMaster
from apps.consent.models import ConsentRecord
from apps.conversations.models import Conversation, Message
from apps.handoff.models import AdminTask
from apps.identity.models import BotUser, MemoryEntry, UserPersonalContext
from apps.identity.services import account_reset as reset
from apps.observability.models import AIRequestMetric
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import Tenant, TenantStaff

pytestmark = pytest.mark.django_db

ACCOUNT = "max:999000111"


@pytest.fixture
def tenant() -> Tenant:
    return Tenant.objects.create(slug="reset-salon", name="Сброс")


@pytest.fixture
def other_tenant() -> Tenant:
    return Tenant.objects.create(slug="reset-other", name="Другой")


def _shell(tenant: Tenant, spec: str = ACCOUNT, **kw) -> BotUser:
    channel, cid = reset.parse_account(spec)
    return BotUser.all_tenants.create(
        tenant=tenant, channel=channel, channel_user_id=cid, display_name="Тест", **kw
    )


def _with_history(tenant: Tenant, spec: str = ACCOUNT) -> BotUser:
    """An account that has actually been through the bot: a dialogue with a
    message, a consent, and inferred memory keyed on its Ayla id."""
    ayla_id = uuid.uuid4()
    bu = _shell(tenant, spec, ayla_user_id=ayla_id)
    with tenant_scope(tenant):
        conv = Conversation.objects.create(tenant=tenant, bot_user=bu)
        Message.objects.create(
            tenant=tenant, conversation=conv, role=Message.Role.USER, content="привет"
        )
    ConsentRecord.all_tenants.create(
        tenant=tenant,
        bot_user=bu,
        consent_type=ConsentRecord.ConsentType.PERSONAL_DATA.value,
        granted=True,
        source="test:fixture",
    )
    upc = UserPersonalContext.objects.create(user_id=ayla_id)
    MemoryEntry.objects.create(
        user_id=ayla_id,
        personal_context=upc,
        sensitivity_zone=MemoryEntry.SENSITIVITY_GREEN,
        source=MemoryEntry.SOURCE_EXPLICIT,
        provenance=MemoryEntry.PROVENANCE_USER_STATED,
        consent_at=timezone.now(),
        content={"likes": "маникюр"},
        source_tenant_id=tenant.id,
    )
    return bu


def _run(*args: str) -> str:
    out = io.StringIO()
    call_command("reset_test_account", *args, stdout=out)
    return out.getvalue()


# --- the red line ------------------------------------------------------------


class TestTheRedLineOnANonEmptyAccount:
    def test_completeness_is_red_before_and_green_after(self, tenant, settings):
        settings.ACCOUNT_RESET_ALLOWLIST = [ACCOUNT]
        bu = _with_history(tenant)
        ids, ayla = [bu.id], [bu.ayla_user_id]

        before = {lo.label for lo in reset.verify(ids, ayla)}
        assert before >= {
            "identity.BotUser",
            "conversations.Conversation.bot_user",
            "consent.ConsentRecord.bot_user",
            "identity.UserPersonalContext.user_id",
        }
        assert reset.verify(ids, ayla) != []

        report = reset.apply(ACCOUNT, "client-onboarding")

        assert report.leftovers == []
        assert reset.verify(ids, ayla) == []
        assert not BotUser.all_tenants.filter(id=bu.id).exists()
        assert not UserPersonalContext.objects.filter(user_id=bu.ayla_user_id).exists()
        assert not MemoryEntry.objects.filter(user_id=bu.ayla_user_id).exists()
        # Memory went WITH the reset, not soft-deleted the way erasure does:
        # a first run cannot have a soft-deleted memory either.
        assert report.removed["identity.MemoryEntry"] == 1
        assert report.removed["conversations.Message"] == 1

    def test_the_next_start_is_a_first_start(self, tenant, settings):
        settings.ACCOUNT_RESET_ALLOWLIST = [ACCOUNT]
        settings.STRICT_TENANT_SCOPE = "strict"
        from apps.identity.services import resolve_or_create_bot_user

        old = _with_history(tenant)
        reset.apply(ACCOUNT, "client-onboarding")

        channel, cid = reset.parse_account(ACCOUNT)
        with tenant_scope(tenant):
            fresh = resolve_or_create_bot_user(channel=channel, channel_user_id=cid)
        assert fresh.id != old.id
        assert fresh.ayla_user_id is None


# --- the wall ----------------------------------------------------------------


class TestTheAllowlistIsAWallNotAPrompt:
    def test_not_listed_is_refused_before_any_read(self, tenant, settings):
        settings.ACCOUNT_RESET_ALLOWLIST = ["max:1"]
        bu = _with_history(tenant)
        with pytest.raises(reset.NotAllowed):
            reset.apply(ACCOUNT, "client-onboarding")
        assert BotUser.all_tenants.filter(id=bu.id).exists()

    def test_empty_list_refuses_everyone(self, tenant, settings):
        settings.ACCOUNT_RESET_ALLOWLIST = []
        bu = _with_history(tenant)
        with pytest.raises(reset.NotAllowed):
            reset.apply(ACCOUNT, "client-onboarding")
        assert BotUser.all_tenants.filter(id=bu.id).exists()

    def test_the_command_says_why_and_exits_non_zero(self, tenant, settings):
        settings.ACCOUNT_RESET_ALLOWLIST = []
        _with_history(tenant)
        out = io.StringIO()
        with pytest.raises(CommandError, match="отказано"):
            call_command(
                "reset_test_account",
                "--account",
                ACCOUNT,
                "--mode",
                "client-onboarding",
                "--apply",
                stdout=out,
            )
        text = out.getvalue()
        assert "ПУСТ" in text
        assert "нет в ACCOUNT_RESET_ALLOWLIST" in text
        assert BotUser.all_tenants.filter(channel_user_id="999000111").exists()


# --- the dry run is a read ---------------------------------------------------


class TestTheDryRunWritesNothing:
    def test_no_delete_or_update_reaches_the_database(self, tenant, settings):
        settings.ACCOUNT_RESET_ALLOWLIST = [ACCOUNT]
        bu = _with_history(tenant)
        with CaptureQueriesContext(connection) as ctx:
            out = _run("--account", ACCOUNT, "--mode", "client-onboarding")
        verbs = [q["sql"].lstrip().split(" ", 1)[0].upper() for q in ctx.captured_queries]
        assert verbs, "the dry run must have READ something"
        assert set(verbs) <= {"SELECT", "SAVEPOINT", "RELEASE"}, sorted(set(verbs))
        assert BotUser.all_tenants.filter(id=bu.id).exists()
        assert "сухой прогон" in out
        assert "conversations.Conversation.bot_user" in out

    def test_zeros_are_printed_as_outcomes(self, tenant, settings):
        _with_history(tenant)
        out = _run("--account", ACCOUNT, "--mode", "client-onboarding")
        # Every incoming relation appears, whether or not it has rows.
        for rel in reset.incoming_relations():
            assert rel.label in out, rel.label
        assert "PROTECT, 0 строк — не блокирует" in out
        assert "остаётся по замыслу" in out
        for label in reset.KEPT_BY_DESIGN:
            assert label in out, label


# --- PROTECT refuses by name ------------------------------------------------


class TestProtectRefusesByName:
    def test_a_metric_survives_without_its_subject(self, tenant, settings):
        """Owner §16.1: SET_NULL. The metric is about the system; the row
        stays, the link goes. Before this decision the same row was a PROTECT
        blocker, and the three real accounts with history were unresettable."""
        settings.ACCOUNT_RESET_ALLOWLIST = [ACCOUNT]
        bu = _with_history(tenant)
        metric = AIRequestMetric.all_tenants.create(
            tenant=tenant,
            bot_user=bu,
            request_id=uuid.uuid4(),
            message_text_length=1,
            latency_total_ms=1,
            outcome=AIRequestMetric.OUTCOME_SUCCESS,
        )
        line = next(
            ln
            for ln in reset.plan(ACCOUNT, "client-onboarding").lines
            if ln.label == "observability.AIRequestMetric.bot_user"
        )
        assert (line.on_delete, line.disposition, line.rows) == ("SET_NULL", "set_null", 1)

        report = reset.apply(ACCOUNT, "client-onboarding")

        assert report.leftovers == []
        metric.refresh_from_db()
        assert metric.bot_user_id is None
        assert metric.conversation_id is None, "the dialogue went, so its pointer went too"
        assert metric.latency_total_ms == 1, "the technical measure is what the row is for"

    def test_an_admin_task_blocks_and_nothing_is_written(self, tenant, settings):
        settings.ACCOUNT_RESET_ALLOWLIST = [ACCOUNT]
        bu = _with_history(tenant)
        conv = Conversation.all_tenants.get(bot_user=bu)
        with tenant_scope(tenant):
            AdminTask.objects.create(
                tenant=tenant, bot_user=bu, conversation=conv, task_type=AdminTask.TaskType.HANDOFF
            )
        with pytest.raises(reset.Blocked) as exc:
            reset.apply(ACCOUNT, "client-onboarding")
        labels = {ln.label for ln in exc.value.lines}
        # Both the direct PROTECT and the one a dismantled conversation would
        # have hit one level down — named, not discovered mid-delete.
        assert labels == {"handoff.AdminTask.bot_user", "conversations.Conversation.bot_user"}
        held = next(
            ln for ln in exc.value.lines if ln.label == "conversations.Conversation.bot_user"
        )
        assert held.removes["handoff.AdminTask"] == 1
        # Refused BEFORE the first write: the dialogue is still there.
        assert Conversation.all_tenants.filter(bot_user=bu).exists()
        assert BotUser.all_tenants.filter(id=bu.id).exists()

    def test_a_role_blocks_the_client_mode_and_is_dismantled_by_the_master_mode(
        self, tenant, settings
    ):
        settings.ACCOUNT_RESET_ALLOWLIST = [ACCOUNT]
        bu = _with_history(tenant)
        TenantStaff.all_tenants.create(tenant=tenant, bot_user=bu, role=TenantStaff.Role.OWNER)

        client_plan = reset.plan(ACCOUNT, "client-onboarding")
        assert [ln.label for ln in client_plan.blockers] == ["tenancy.TenantStaff.bot_user"]

        master_plan = reset.plan(ACCOUNT, "master-registration")
        assert master_plan.blockers == []
        staff_line = next(
            ln for ln in master_plan.lines if ln.label == "tenancy.TenantStaff.bot_user"
        )
        assert staff_line.disposition == "dismantled"

        report = reset.apply(ACCOUNT, "master-registration")
        assert report.leftovers == []
        assert not TenantStaff.all_tenants.filter(bot_user_id=bu.id).exists()


# --- the master card after a reset ------------------------------------------


class TestTheMasterCardAfterAReset:
    def test_card_survives_unlinked_and_reads_like_a_synced_master(self, tenant, settings):
        """SET_NULL leaves the card; what state does it read as?

        Main-window requirement (11.09): the reset path and the archive path
        (DRF-1654) both touch this row and must not leave it in a state
        nobody has named. Named here: the card stays sellable exactly like a
        master who arrived by sync and never used the bot (``sale_block``
        does not ask about ``linked_bot_user`` on purpose, see
        ``apps/catalog/master_state.py`` module docstring), and it is NOT
        enrolled — no cabinet until a new invite is redeemed. That is what
        «готов к проверке регистрации» means for the card.
        """
        settings.ACCOUNT_RESET_ALLOWLIST = [ACCOUNT]
        bu = _with_history(tenant)
        TenantStaff.all_tenants.create(tenant=tenant, bot_user=bu, role=TenantStaff.Role.OWNER)
        card = CatalogMaster.all_tenants.create(
            tenant=tenant,
            name="Тихонова Ольга",
            external_id=None,
            external_updated_at=timezone.now(),
            invite_status=CatalogMaster.InviteStatus.ACCEPTED,
            is_active=True,
            ayla_user_id=bu.ayla_user_id,
            linked_bot_user=bu,
            accepted_at=timezone.now(),
        )
        assert is_enrolled(card)
        assert sale_block(card) is None, "sold before the reset — the red line has a before"
        assert reset.plan(ACCOUNT, "master-registration").master_cards.rows == 1

        reset.apply(ACCOUNT, "master-registration")

        card.refresh_from_db()
        assert card.linked_bot_user_id is None
        assert card.ayla_user_id is None, "the catalog half deletes that user — no dangling key"
        assert card.archived_at is None, "reset is not archive: the master stays alive"
        assert sale_block(card) == "ayla_unlinked", "not sold until the registration is redone"
        assert not is_enrolled(card), "no cabinet until a new invite is redeemed"

    def test_client_mode_leaves_the_card_key_alone(self, tenant, settings):
        settings.ACCOUNT_RESET_ALLOWLIST = [ACCOUNT]
        bu = _with_history(tenant)
        card = CatalogMaster.all_tenants.create(
            tenant=tenant,
            name="Синхронизированная",
            external_id=1001,
            external_updated_at=timezone.now(),
            invite_status=CatalogMaster.InviteStatus.ACCEPTED,
            is_active=True,
            ayla_user_id=bu.ayla_user_id,
        )
        p = reset.plan(ACCOUNT, "client-onboarding")
        assert p.master_cards.rows == 1
        assert p.master_cards.disposition == "kept"
        reset.apply(ACCOUNT, "client-onboarding")
        card.refresh_from_db()
        assert card.ayla_user_id == bu.ayla_user_id


# --- completeness inside the transaction ------------------------------------


class TestCompletenessRollsBack:
    def test_a_leftover_rolls_the_whole_reset_back(self, tenant, settings, monkeypatch):
        settings.ACCOUNT_RESET_ALLOWLIST = [ACCOUNT]
        bu = _with_history(tenant)

        real_verify = reset.verify

        def lying_verify(bot_user_ids, ayla_user_ids):
            # Simulate a relation the walk missed: something still points here.
            return real_verify(bot_user_ids, ayla_user_ids) + [reset.Leftover("x.Y.z", 1)]

        monkeypatch.setattr(reset, "verify", lying_verify)
        report = reset.apply(ACCOUNT, "client-onboarding")

        assert [lo.label for lo in report.leftovers] == ["x.Y.z"]
        assert BotUser.all_tenants.filter(id=bu.id).exists(), "rolled back — nothing half-freed"
        assert Conversation.all_tenants.filter(bot_user=bu).exists()

    def test_person_level_across_tenants(self, tenant, other_tenant, settings):
        settings.ACCOUNT_RESET_ALLOWLIST = [ACCOUNT]
        a = _with_history(tenant)
        b = _shell(other_tenant)
        p = reset.plan(ACCOUNT, "client-onboarding")
        assert set(p.bot_user_ids) == {a.id, b.id}
        reset.apply(ACCOUNT, "client-onboarding")
        assert not BotUser.all_tenants.filter(id__in=[a.id, b.id]).exists()


# --- what is kept is named, and the names are real ---------------------------


class TestKeptByDesignNamesRealColumns:
    def test_every_entry_resolves(self):
        for label, (family, column, reason) in reset.KEPT_BY_DESIGN.items():
            app_label, model_name, col = label.split(".", 2)
            model = apps.get_model(app_label, model_name)
            assert col == column
            assert family in ("bot_user", "ayla_user")
            assert reason
            f = model._meta.get_field(column)
            assert not f.is_relation, f"{label} is a real FK — it belongs in the walk, not here"

    def test_the_audit_row_of_the_reset_itself_survives_it(self, tenant, settings):
        settings.ACCOUNT_RESET_ALLOWLIST = [ACCOUNT]
        bu = _with_history(tenant)
        assert reset.verify([bu.id], [bu.ayla_user_id]), "red before"
        reset.apply(ACCOUNT, "client-onboarding")
        row = AuditLog.all_tenants.filter(action="identity.account_reset.applied").get()
        assert row.target_id == bu.id
        assert row.payload["account"] == ACCOUNT
        assert row.payload["mode"] == "client-onboarding"
        # And the plan reports it as kept, with its reason, rather than as a leftover.
        p = reset.plan(ACCOUNT, "client-onboarding")
        assert p.bot_user_ids == []  # the account is gone …
        assert reset.verify([bu.id], [bu.ayla_user_id]) == []  # … and nothing points at it …
        kept = AuditLog.all_tenants.filter(target_id=bu.id).count()
        assert kept == 1  # … except the journal, which is the point of a journal.

    def test_the_walk_sees_hidden_relations(self):
        """``related_name="+"`` relations are invisible to ``get_fields()``
        and were missed once (measurement 11.09: 20 → 25). The walk must
        keep asking for them; this names one so the guard is not vacuous."""
        rels = reset.incoming_relations()
        hidden = [r.label for r in rels if r.hidden]
        assert hidden, "no hidden relation at all — the walk dropped include_hidden"
        assert "tenancy.StaffInvite.created_by" in hidden

    def test_every_mode_dismantles_only_relations_that_exist_and_are_protect(self):
        by_label = {r.label: r for r in reset.incoming_relations()}
        for mode in reset.MODES.values():
            for label in mode.dismantles:
                assert label in by_label, f"{mode.name} names a relation that is not there: {label}"
                assert by_label[label].on_delete == "PROTECT", label


# --- the account spec --------------------------------------------------------


class TestTheAccountSpec:
    @pytest.mark.parametrize("bad", ["83146139", "max:", ":1", ""])
    def test_malformed_is_refused(self, bad):
        with pytest.raises(ValueError):
            reset.parse_account(bad)

    def test_unknown_account_is_a_named_zero(self, settings):
        settings.ACCOUNT_RESET_ALLOWLIST = ["max:404"]
        out = _run("--account", "max:404", "--mode", "client-onboarding", "--apply")
        assert "0 оболочек BotUser" in out
