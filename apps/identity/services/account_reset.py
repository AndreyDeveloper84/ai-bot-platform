"""Reset a TEST account so it can be used for a fresh pilot check-through.

Owner's problem (11.09.2026): there are very few MAX accounts and every one
of them has already been through the bot, so nothing is left to test a first
registration or a first onboarding questionnaire with. This module frees an
account. It is not erasure and does not pretend to be:

    erasure   protects a PERSON      deliberately KEEPS what must survive
    reset     frees an ACCOUNT       has to remove exactly that

``privacy.delete_personal_data`` anonymises the dialogue and soft-deletes
memory because a retention period says so. A reset that left those behind
would not give a first run; a reset that removed them from a real customer
would break the retention period. Both statements are true at once, which
is why the two operations share a cascade but not a purpose — and why the
only thing that makes this module safe is §5 of the measurement: **it is
structurally impossible to aim it at a real person**, see :func:`allowed`.

### What the plan is made of

Every incoming relation of :class:`BotUser` is enumerated from the ORM at
call time with ``include_hidden=True`` — ``related_name="+"`` relations are
invisible to plain ``get_fields()`` and were missed once already
(``docs/MEASUREMENT_ACCOUNT_RESET_AND_ERASURE_PATHS.md``). Nothing here is a
hand-kept list of tables: a relation added next month shows up in the plan
by itself, and if it is ``PROTECT`` it blocks by itself.

Each relation gets one of four dispositions:

    cascade      goes with the account — the ORM removes it, the plan says what
    set_null     the row stays, its pointer to the account is cleared
    dismantled   PROTECT that the chosen MODE takes apart by design (§6)
    blocks       PROTECT that nothing takes apart → the whole reset refuses

The database itself has no cascades at all — every FK is ``NO ACTION``
(checked on the pilot 11.09) — so the plan reads the ORM's ``Collector``,
the same thing ``.delete()`` reads, and the only way it can differ from what
a delete will do is if a delete would fail. ``Collector.collect`` writes
nothing; that is what makes ``--dry-run`` on the pilot a read and not a
promise, and a test holds it to that with a SQL capture.

### Memory is keyed on the OTHER id

``UserPersonalContext`` / ``MemoryEntry`` hang off ``ayla_user_id`` — the
catalog's id for the person — not off ``BotUser``. Deleting the BotUser
alone leaves the whole inferred memory in place, and the next ``/start``
would resolve to the same catalog id and find it. So the plan carries two
id families and the memory line is explicit.

### What is deliberately left, and why it is named rather than skipped

The completeness check (§7) re-reads every relation from the database and
demands zero. Some columns hold the account's id WITHOUT a foreign key —
journals, provenance handles — and they must survive: an audit log that
forgets who it was about is not an audit log. Those are listed in
:data:`KEPT_BY_DESIGN` with the reason next to the name, and the check
reports their counts instead of asserting on them. A literal «the id occurs
nowhere» would either be red forever or get "fixed" one day by someone
wiping the journal.
"""

from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass, field

from django.apps import apps
from django.conf import settings
from django.db import models, transaction
from django.db.models.deletion import Collector, ProtectedError, RestrictedError

from apps.identity.models import BotUser, UserPersonalContext

# --- modes -------------------------------------------------------------------


@dataclass(frozen=True)
class Mode:
    """A reset mode, named by what it FREES, not by what it deletes.

    «готов к проверке анкеты клиента» is checkable; «стёрты семь таблиц» is
    not (§6 of the measurement). ``dismantles`` is the closed set of PROTECT
    relations this mode takes apart by design — everything else that is
    PROTECT refuses. Widening it is a product decision, not a code fix.
    """

    name: str
    frees: str
    dismantles: frozenset[str]


_CLIENT_DISMANTLES = frozenset(
    {
        # The two the existing admin helper (`delete_bot_user_data`) already
        # takes apart, and for the same reason: a conversation is the
        # account's own history, a first run cannot have one.
        "conversations.Conversation.bot_user",
        "conversations.StaffAssistantThread.bot_user",
    }
)

MODES: dict[str, Mode] = {
    "client-onboarding": Mode(
        name="client-onboarding",
        frees="готов к проверке анкеты онбординга клиента",
        dismantles=_CLIENT_DISMANTLES,
    ),
    "master-registration": Mode(
        name="master-registration",
        frees="готов к проверке регистрации мастера",
        # The role is what a registration creates; freeing the registration
        # means the role goes. The TENANT does not — a solo master's tenant
        # holds the salon's conversations, and removing it is a different
        # decision (owner question 2, DRF-1349).
        dismantles=_CLIENT_DISMANTLES | {"tenancy.TenantStaff.bot_user"},
    ),
}


# --- what survives, by name and with a reason --------------------------------

#: Columns holding a BotUser id or an Ayla user id WITHOUT a foreign key.
#: They are not in the relation walk, so they are not deleted and not
#: nulled; the completeness check prints their counts under «остаётся».
#:
#: label → (id family, column, reason). Guarded by a test that every label
#: resolves to a real model and column, so the list cannot rot silently.
KEPT_BY_DESIGN: dict[str, tuple[str, str, str]] = {
    "audit.AuditLog.actor_id": (
        "bot_user",
        "actor_id",
        "журнал действий обязан пережить того, о ком он",
    ),
    "audit.AuditLog.target_id": (
        "bot_user",
        "target_id",
        "журнал действий обязан пережить того, о ком он",
    ),
    "adminconsole.ClientDataAccessGrant.client_id": (
        "bot_user",
        "client_id",
        "пропуск оператора к клиенту — запись о выданном праве; обращение (AdminTask) само PROTECT",
    ),
    "adminconsole.ClientDataAccessLog.client_id": (
        "bot_user",
        "client_id",
        "журнал доступа оператора к данным клиента",
    ),
    "scheduling.ScheduleChangeRequest.resolved_by_bot_user_id": (
        "bot_user",
        "resolved_by_bot_user_id",
        "провенанс решения по расписанию — кто решил, а не чей аккаунт",
    ),
    "identity.RedZoneAccessLog.user_id": (
        "ayla_user",
        "user_id",
        "журнал доступа к красной зоне памяти (152-ФЗ)",
    ),
    "eventbus.NotificationDispatchDedupe.recipient_id": (
        "ayla_user",
        "recipient_id",
        "ключ exactly-once для уведомлений; повтор после сброса — та же защита",
    ),
}


# --- the plan ----------------------------------------------------------------


@dataclass(frozen=True)
class Relation:
    label: str
    on_delete: str
    model: type[models.Model]
    column: str
    hidden: bool


def incoming_relations(model: type[models.Model] = BotUser) -> list[Relation]:
    """Every relation pointing AT ``model``, hidden ones included."""
    out: list[Relation] = []
    for f in model._meta.get_fields(include_hidden=True):
        fld = getattr(f, "field", None)
        if fld is None or not f.is_relation or f.concrete:
            continue
        on_delete = getattr(getattr(fld.remote_field, "on_delete", None), "__name__", "M2M")
        out.append(
            Relation(
                label=f"{fld.model._meta.label}.{fld.name}",
                on_delete=on_delete,
                model=fld.model,
                column=fld.name,
                hidden=bool(getattr(f, "hidden", False)),
            )
        )
    return sorted(out, key=lambda r: (r.on_delete, r.label))


@dataclass
class Line:
    """One relation in the plan. ``rows`` is printed even when it is zero."""

    label: str
    on_delete: str
    disposition: str  # cascade | set_null | dismantled | blocks | protect_empty
    rows: int
    removes: Counter[str] = field(default_factory=Counter)  # transitive, by model label


@dataclass
class Kept:
    label: str
    rows: int
    reason: str


@dataclass
class Plan:
    account: str
    mode: Mode
    bot_user_ids: list[uuid.UUID]
    ayla_user_ids: list[uuid.UUID]
    lines: list[Line]
    memory: Line
    kept: list[Kept]

    @property
    def blockers(self) -> list[Line]:
        return [ln for ln in self.lines if ln.disposition == "blocks"]

    @property
    def ok(self) -> bool:
        return bool(self.bot_user_ids) and not self.blockers


class NotAllowed(Exception):
    """The account is not on the allowlist. Not a confirmation prompt — a wall."""


class Blocked(Exception):
    """A PROTECT relation nothing dismantles has rows. Named, not bypassed."""

    def __init__(self, lines: list[Line]) -> None:
        self.lines = lines
        super().__init__(", ".join(f"{ln.label}={ln.rows}" for ln in lines))


def parse_account(spec: str) -> tuple[str, str]:
    """``max:83146139`` → ``("max", "83146139")``. Anything else is an error."""
    channel, sep, channel_user_id = spec.partition(":")
    if not sep or not channel or not channel_user_id:
        raise ValueError(f"account must look like channel:channel_user_id, got {spec!r}")
    return channel, channel_user_id


def allowlist() -> frozenset[str]:
    """``ACCOUNT_RESET_ALLOWLIST`` as a set of ``channel:channel_user_id``.

    Empty means NOBODY can be reset. Nothing here reads a confirmation flag
    or an environment name: on a host where the list is empty the command
    refuses every account, and the pilot's list is empty.
    """
    raw = getattr(settings, "ACCOUNT_RESET_ALLOWLIST", ())
    return frozenset(item.strip() for item in raw if item and item.strip())


def allowed(spec: str) -> bool:
    return spec in allowlist()


def _shells(channel: str, channel_user_id: str) -> list[BotUser]:
    # Person-level: one BotUser per tenant for the same (channel, id).
    return list(
        BotUser.all_tenants.filter(channel=channel, channel_user_id=channel_user_id).order_by(
            "first_seen", "id"
        )
    )


def _collect(objs: list[models.Model]) -> Counter[str]:
    """What deleting ``objs`` removes, transitively, by model — read only."""
    if not objs:
        return Counter()
    collector = Collector(using="default")
    collector.collect(objs)
    removes: Counter[str] = Counter()
    for model, instances in collector.data.items():
        removes[model._meta.label] += len(instances)
    for qs in collector.fast_deletes:
        removes[qs.model._meta.label] += qs.count()
    return removes


def _line(rel: Relation, mode: Mode, ids: list[uuid.UUID]) -> Line:
    qs = rel.model._base_manager.filter(**{f"{rel.column}__in": ids})
    rows = qs.count()
    if rel.on_delete in ("PROTECT", "RESTRICT"):
        if rel.label in mode.dismantles:
            disposition = "dismantled"
        elif rows:
            disposition = "blocks"
        else:
            disposition = "protect_empty"
    elif rel.on_delete in ("SET_NULL", "SET_DEFAULT", "SET"):
        disposition = "set_null"
    else:
        disposition = "cascade"

    removes: Counter[str] = Counter()
    if disposition in ("cascade", "dismantled") and rows:
        try:
            removes = _collect(list(qs))
        except (ProtectedError, RestrictedError) as exc:
            # A child of the account is itself protected further down. That
            # is a blocker of this reset as much as a direct one, and it is
            # named by the model that holds it.
            protected: set[models.Model] = set(getattr(exc, "protected_objects", ()) or ())
            protected |= set(getattr(exc, "restricted_objects", ()) or ())
            held = Counter(o._meta.label for o in protected)
            disposition = "blocks"
            removes = held
    return Line(rel.label, rel.on_delete, disposition, rows, removes)


def _memory_line(ayla_user_ids: list[uuid.UUID]) -> Line:
    qs = UserPersonalContext.objects.filter(user_id__in=ayla_user_ids)
    rows = qs.count()
    removes = _collect(list(qs)) if rows else Counter()
    return Line(
        "identity.UserPersonalContext.user_id (ключ ayla_user_id, не FK)",
        "—",
        "cascade",
        rows,
        removes,
    )


def _kept(bot_user_ids: list[uuid.UUID], ayla_user_ids: list[uuid.UUID]) -> list[Kept]:
    out: list[Kept] = []
    for label, (family, column, reason) in KEPT_BY_DESIGN.items():
        app_label, model_name, _ = label.split(".", 2)
        model = apps.get_model(app_label, model_name)
        ids = bot_user_ids if family == "bot_user" else ayla_user_ids
        rows = model._base_manager.filter(**{f"{column}__in": ids}).count() if ids else 0
        out.append(Kept(label, rows, reason))
    return out


def plan(spec: str, mode_name: str) -> Plan:
    """Read-only. What a reset of ``spec`` in ``mode_name`` would do.

    Does not consult the allowlist: a plan for a non-allowlisted account is
    a legitimate question («what would this take?»). :func:`apply` is the
    wall.
    """
    mode = MODES[mode_name]
    channel, channel_user_id = parse_account(spec)
    shells = _shells(channel, channel_user_id)
    bot_user_ids = [s.id for s in shells]
    ayla_user_ids = sorted({s.ayla_user_id for s in shells if s.ayla_user_id is not None})

    lines = [_line(rel, mode, bot_user_ids) for rel in incoming_relations()] if shells else []
    return Plan(
        account=spec,
        mode=mode,
        bot_user_ids=bot_user_ids,
        ayla_user_ids=ayla_user_ids,
        lines=lines,
        memory=_memory_line(ayla_user_ids),
        kept=_kept(bot_user_ids, ayla_user_ids),
    )


# --- applying it -------------------------------------------------------------


@dataclass
class Leftover:
    label: str
    rows: int


def verify(bot_user_ids: list[uuid.UUID], ayla_user_ids: list[uuid.UUID]) -> list[Leftover]:
    """Re-read every relation from the database; anything non-zero is a leftover.

    :data:`KEPT_BY_DESIGN` columns are not consulted here — they are
    reported by :func:`plan` under their reasons, never asserted on.
    """
    left: list[Leftover] = []
    if not bot_user_ids:
        return left
    n = BotUser.all_tenants.filter(id__in=bot_user_ids).count()
    if n:
        left.append(Leftover("identity.BotUser", n))
    for rel in incoming_relations():
        # For SET_NULL the row survives by design; what must be gone is the
        # pointer — and that is the same query as for everything else.
        rows = rel.model._base_manager.filter(**{f"{rel.column}__in": bot_user_ids}).count()
        if rows:
            left.append(Leftover(rel.label, rows))
    if ayla_user_ids:
        m = UserPersonalContext.objects.filter(user_id__in=ayla_user_ids).count()
        if m:
            left.append(Leftover("identity.UserPersonalContext.user_id", m))
    return left


@dataclass
class Report:
    plan: Plan
    removed: Counter[str]
    leftovers: list[Leftover]


def apply(spec: str, mode_name: str) -> Report:
    """Free the account. One transaction; refuses before the first write.

    Order inside the transaction matters and is the order of the plan:
    the mode's dismantled PROTECT sets go first (they would block the
    BotUser delete otherwise), then memory by ``ayla_user_id``, then the
    BotUser rows — whose CASCADE / SET_NULL relations the ORM handles. The
    completeness check runs INSIDE the same transaction, so a leftover
    rolls everything back rather than leaving a half-freed account.
    """
    if not allowed(spec):
        raise NotAllowed(spec)
    from apps.audit.services import write_audit

    with transaction.atomic():
        p = plan(spec, mode_name)
        if not p.bot_user_ids:
            return Report(p, Counter(), [])
        if p.blockers:
            raise Blocked(p.blockers)

        removed: Counter[str] = Counter()
        rels = {r.label: r for r in incoming_relations()}
        for ln in p.lines:
            if ln.disposition != "dismantled" or not ln.rows:
                continue
            rel = rels[ln.label]
            qs = rel.model._base_manager.filter(**{f"{rel.column}__in": p.bot_user_ids})
            for obj in qs:
                # Same breadcrumb the existing admin helper leaves, where
                # the model has one: soft-delete first, then hard-delete.
                mark = getattr(obj, "mark_deleted", None)
                if callable(mark):
                    mark()
            _, per_model = qs.delete()
            removed.update(per_model)

        if p.memory.rows:
            _, per_model = UserPersonalContext.objects.filter(user_id__in=p.ayla_user_ids).delete()
            removed.update(per_model)

        _, per_model = BotUser.all_tenants.filter(id__in=p.bot_user_ids).delete()
        removed.update(per_model)

        leftovers = verify(p.bot_user_ids, p.ayla_user_ids)
        if leftovers:
            # Rolling back on purpose: «часть стёрта, часть жива» is the one
            # state this command exists to make impossible.
            transaction.set_rollback(True)
            return Report(p, Counter(), leftovers)

        write_audit(
            "identity.account_reset.applied",
            target="BotUser",
            target_id=p.bot_user_ids[0],
            payload={
                "account": spec,
                "mode": mode_name,
                "bot_user_ids": [str(i) for i in p.bot_user_ids],
                "ayla_user_ids": [str(i) for i in p.ayla_user_ids],
                "removed": dict(removed),
            },
        )
    return Report(p, removed, [])


__all__ = [
    "KEPT_BY_DESIGN",
    "MODES",
    "Blocked",
    "Kept",
    "Leftover",
    "Line",
    "Mode",
    "NotAllowed",
    "Plan",
    "Relation",
    "Report",
    "allowed",
    "allowlist",
    "apply",
    "incoming_relations",
    "parse_account",
    "plan",
    "verify",
]
