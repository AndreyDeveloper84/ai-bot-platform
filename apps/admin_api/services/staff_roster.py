"""The salon's people, merged across the two tables that hold roles.

### Why a service and not a queryset

Roles are additive and split over two tables (ADR-0008 decision 2):
``TenantStaff`` carries owner / admin / receptionist, and the master role
lives on ``CatalogMaster.linked_bot_user``. Neither table is the roster.

* Read only ``TenantStaff`` and every master is invisible — on the pilot
  that is four of the five people who work there.
* Read only ``CatalogMaster`` and every administrator is invisible —
  including the owner, unless she happens to also cut hair.
* Read both and concatenate and the owner-who-is-also-a-master appears
  twice, once under each of her rows.

The merge is the whole job, so it lives in one place with the identity
rule written down next to it.

### The identity rule, and why it is the one ``is_solo_provider`` uses

A person is keyed by ``bot_user_id`` when they have one — that is what
collapses «Karina the owner» and «Karina the master» into a single row,
because the catalog row points at the same ``BotUser``. A master row with
no ``linked_bot_user`` is keyed by the master row itself.

``apps.identity.services.solo_onboarding.is_solo_provider`` settled the
same question for counting people (DRF-1149) and settled it this way,
including the deliberate asymmetry: when two rows *cannot* be proven to
be one human — a staff row and an unlinked catalog row that merely share
a name — they stay two. Names are not identity, and a roster that
silently merges two employees because their names match is worse than
one that shows a duplicate the owner can recognise and bridge.

Keeping the rule identical to ``is_solo_provider`` also keeps two
surfaces from disagreeing about how many people a salon has.

One case the rule does NOT collapse, and cannot: ``BotUser`` is unique
per ``(tenant, channel, channel_user_id)``, so the same human reachable
on both MAX and a future Telegram bot is two ``BotUser`` rows and
therefore two people here. Inherent to the identity model rather than to
this reader, and shared with every other surface that counts people.

### What is deliberately absent

**No phone number, by any path.** ``DRF-1039`` is the owner's decision
that a client's phone never reaches an executor; it does not speak about
staff, and no other decision does either. Rather than infer permission
from silence, this reader never loads the column: all three queries below
are ``.values()`` with explicit field lists, so a phone cannot reach the
serialiser even by way of a future ``select_related``.

``bot_user_id`` and ``master_id`` ARE returned, and they are the primary
keys of the rows a phone hangs off. They are list identity — what a
future revoke button would name a person with — not an invitation to
join back to ``BotUser`` and read the rest of it. A screen that does
that has reopened the question this module closed.

**No unredeemed invites.** An outstanding admin code lives entirely in
``StaffInvite`` and writes no ``TenantStaff`` row, so a person who was
sent a code and never used it does not appear here at all. A master
invited through the master flow DOES appear, because that path writes the
catalog row up front — hence ``pending`` on the master role and no
equivalent for the admin ones. The asymmetry belongs to the two invite
flows, not to this reader; closing it means listing outstanding invites,
which is a different screen.

**No actions.** This module answers «who works here and what are they»
and nothing else. Changing a role and revoking access are owner
decisions that were deliberately not part of this work.

### Cost

Three queries, all indexed, regardless of salon size: staff rows, master
rows, and the used invites that explain how each role was granted. The
merge itself is in Python because it spans two tables that have no join
between them.

### Why not route this through ``resolve_role``

It answers a different question. ``resolve_role(bot_user)`` decides what
ONE person may do; this module inventories everyone. Handing it the job
would drop every master with no ``BotUser`` — the pilot's dominant shape,
three rows of four — because it takes a ``BotUser`` as input and those
people do not have one. It also filters out exactly what a roster must
show (revoked grants, archived and pending masters), carries neither
``source`` nor ``since``, and would turn three queries into two per head.

What must NOT be re-derived is the semantics, and it is not: the identity
rule is ``is_solo_provider``'s, the three-word state comes from
:func:`apps.catalog.master_state.master_state` (DRF-1506 — one definition
for five sites), and ``test_the_roster_and_the_resolver_agree_on_who_is_
a_master`` pins this module's answer to the resolver's on the case where
the two could drift (a PENDING catalog row, which the invite path writes
with ``is_active=False, archived_at=None`` and which would otherwise read
as «доступ отозван» here while the resolver calls that person a customer
who is simply still expected).

``MAX_ROSTER_PEOPLE`` bounds the RESPONSE, not the read: every row is
still fetched and sorted before the slice. Bounding the read would cost
the flat query count, because ``total_count`` would then need its own
``COUNT(*)`` — and the cap exists to keep an anomaly from reaching a
phone, not to keep it out of Postgres.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal
from uuid import UUID

from apps.catalog.master_state import RoleState as _RoleState
from apps.catalog.master_state import master_state
from apps.catalog.models import CatalogMaster
from apps.tenancy.context import tenant_scope
from apps.tenancy.models import StaffInvite, TenantStaff

#: Hard ceiling on people returned in one answer.
#:
#: There is no pagination here on purpose. A cursor over a merged view of
#: two tables that share no sort key is a real chunk of machinery, and no
#: salon has this many employees — the pilot has five. The cap exists so
#: that a data anomaly (a botched catalog sync inventing rows) degrades
#: into a truncated list with a flag rather than an unbounded response —
#: it bounds what reaches the phone, not what leaves Postgres.
MAX_ROSTER_PEOPLE = 200

#: Increasing privilege, mirroring ``role_resolver._ROLE_PRIVILEGE``.
#: Used only for ordering — the roster asserts no primary role, because
#: collapsing «owner and master» to «owner» is precisely the information
#: this screen exists to stop losing.
_ROLE_RANK: dict[str, int] = {
    "master": 1,
    "receptionist": 2,
    "admin": 3,
    "owner": 4,
}

RoleSource = Literal["access_code", "master_invite", "direct"]

#: What a grant is doing right now.
#:
#: Three values and not a boolean, because a boolean forced two unlike
#: things to share one label. A master invited yesterday who has not
#: opened the bot yet is not active — but calling her «доступ отозван» on
#: the screen is a lie about a person nobody has taken anything from, and
#: the owner's next move (resend the invite) is the opposite of the one
#: that word suggests.
#:
#: ``pending`` is reachable only for the master role. An administrator's
#: invite lives entirely in ``StaffInvite`` until it is redeemed — no
#: ``TenantStaff`` row exists, so an unredeemed admin code does not appear
#: in this roster at all. That gap is real and deliberately not closed
#: here; see the module docstring.
#: Re-exported, not redefined: the three words and the rule that picks
#: between them live in :mod:`apps.catalog.master_state` (DRF-1506), so
#: the roster and the booking surface cannot drift on what «active»
#: means. ``Literal`` is still spelled out in the import there.
RoleState = _RoleState


@dataclass
class RoleGrant:
    """One role one person holds (or held) at this salon.

    ``state`` is per-role, not per-person: an admin who is still a master
    has one live grant and one revoked one, and flattening that to a
    single person-level flag would report her as either fully present or
    fully gone. Both are wrong.
    """

    role: str
    state: RoleState
    source: RoleSource
    since: datetime | None

    @property
    def active(self) -> bool:
        return self.state == "active"

    def to_payload(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "state": self.state,
            # Derived, and kept because «активна ли роль» is the question
            # the owner actually asked. A reader that only wants the
            # boolean never has to learn the three-value vocabulary.
            "active": self.active,
            "source": self.source,
            "since": self.since.isoformat() if self.since is not None else None,
        }


@dataclass
class Person:
    """One human at this salon, with every role they hold."""

    key: str
    bot_user_id: UUID | None
    master_id: UUID | None
    name: str
    roles: list[RoleGrant] = field(default_factory=list)

    @property
    def is_active(self) -> bool:
        """True while at least one grant is live.

        A person whose every grant is revoked stays in the answer rather
        than vanishing: «Ирина, доступ отозван» is what the owner needs to
        read to know the revoke worked. An absence proves nothing.
        """

        return any(r.active for r in self.roles)

    @property
    def has_account(self) -> bool:
        """Whether a MAX account is attached.

        False means this person cannot open the Mini App at all — the
        commonest state on the pilot, where three of four catalog masters
        had never bridged to a ``BotUser`` (DRF-1149). Worth showing,
        because «why can't Natalia log in» has this as its answer.
        """

        return self.bot_user_id is not None

    def to_payload(self) -> dict[str, Any]:
        return {
            "id": self.key,
            "bot_user_id": str(self.bot_user_id) if self.bot_user_id else None,
            "master_id": str(self.master_id) if self.master_id else None,
            "name": self.name,
            "has_account": self.has_account,
            "is_active": self.is_active,
            "roles": [r.to_payload() for r in self.effective_roles()],
        }

    def effective_roles(self) -> list[RoleGrant]:
        """One grant per role name, ordered for display.

        A person can hold two ``TenantStaff`` rows for the SAME role —
        revoke an admin and grant the role again and the old row stays,
        by design, because ``deactivated_at`` is a soft delete kept for
        audit. Only the ``owner`` role has a partial unique index; admin
        and receptionist do not.

        Emitting both would report «Аня — Администратор» next to «Аня —
        Администратор, доступ отозван», which reads as a contradiction
        rather than as history, and hands the frontend two list entries
        with the same identity. The roster answers «what is this person
        now»; the audit log is where superseded rows belong.

        The live grant wins; among equals the most recent one does.
        """

        best: dict[str, RoleGrant] = {}
        for grant in self.roles:
            incumbent = best.get(grant.role)
            if incumbent is None or _supersedes(grant, incumbent):
                best[grant.role] = grant
        return sorted(best.values(), key=_role_sort_key)


def _supersedes(candidate: RoleGrant, incumbent: RoleGrant) -> bool:
    """Whether ``candidate`` is the truer answer for its role name."""

    if candidate.active != incumbent.active:
        return candidate.active
    # Same liveness — the later grant is the current one. A grant with no
    # date loses to one that has a date rather than winning by accident.
    if candidate.since is None:
        return False
    if incumbent.since is None:
        return True
    return candidate.since > incumbent.since


#: How far apart a ``TenantStaff`` row and the ``StaffInvite.used_at``
#: that produced it may be before they stop counting as the same event.
#:
#: ``_grant_staff_role`` runs inside the same ``transaction.atomic()``
#: that stamps ``used_at`` (``apps.identity.services.staff_invites``), so
#: in practice the gap is milliseconds. A minute is generous enough to
#: survive clock skew and a slow transaction, and far too short to
#: accidentally capture a grant made on another day.
_CODE_MATCH_WINDOW = timedelta(minutes=1)


def _staff_source(created_at: datetime, code_moments: list[datetime] | None) -> RoleSource:
    """Whether THIS staff row came from a redeemed code.

    Not «has this person ever redeemed a code for this role» — that
    question has a different answer and telling the owner the wrong one
    sends her looking for a live code that nobody holds.
    """

    if not code_moments:
        return "direct"
    for moment in code_moments:
        if abs(created_at - moment) <= _CODE_MATCH_WINDOW:
            return "access_code"
    return "direct"


def _role_sort_key(grant: RoleGrant) -> tuple[int, int, str]:
    """Live grants first, then highest privilege. Stable for the UI."""

    return (0 if grant.active else 1, -_ROLE_RANK.get(grant.role, 0), grant.role)


def _person_sort_key(person: Person) -> tuple[int, int, str, str]:
    """Active people first, then by the highest role they hold, then name."""

    top = max((_ROLE_RANK.get(r.role, 0) for r in person.roles), default=0)
    return (0 if person.is_active else 1, -top, person.name.casefold(), person.key)


def build_staff_roster(tenant: Any) -> tuple[list[Person], int, bool]:
    """Return ``(people, total_count, truncated)`` for ``tenant``.

    ``total_count`` counts every person found, including any the cap
    dropped — a truncated list that also under-reports its own size would
    hide the anomaly the cap exists to surface.

    Every read goes through the tenant-scoped default manager AND names
    ``tenant=`` explicitly, inside a ``tenant_scope`` this function enters
    itself. Three things at once, and each earns its place:

    * ``.objects`` rather than ``.all_tenants`` is what the catalog
      boundary rule requires (MKT1 / #1018 — ``CatalogMaster.all_tenants``
      is reserved for ``apps.marketplace.discovery``). The baselined
      crossings elsewhere all justify themselves with «``.objects`` is
      unavailable because this runs outside a request, where no tenant
      ContextVar is set». That is not true here, so a baseline entry would
      have been a false claim rather than an accepted debt.

    * Entering the scope here rather than relying on the caller's is what
      makes the first point safe. ``STRICT_TENANT_SCOPE`` defaults to
      ``audit``, and in that mode a scoped manager with no tenant in
      context returns ``.none()`` **silently**. Called from a job or a
      test without a scope, this function would then answer «staff yes,
      masters no» — a roster missing exactly the half that has no
      ``TenantStaff`` row, which reads as a salon with no masters rather
      than as a failure. ``tenant_scope`` is a ContextVar push/pop, so
      re-entering it under the admin decorator's own scope costs nothing.

    * The explicit ``tenant=`` filter is redundant by construction once
      the scope above is entered, and is kept anyway as cheap defence for
      the day somebody drops the scope «because the decorator already does
      it» — which is how the ``all_tenants`` this replaced got here. It is
      measured, not assumed: deleting the filter leaves the suite green,
      deleting the scope turns four tests red. No test claims otherwise.

    One join is worth naming: ``CatalogMaster.linked_bot_user`` is a
    globally unique ``OneToOneField`` with no same-tenant constraint, so a
    foreign ``BotUser`` bound to a local master row would surface that
    person's name through it. Not reachable through
    ``redeem_staff_invite``, which finds the invite by the caller's own
    tenant and links the caller's own ``BotUser`` — but the filter above
    guards the master row, not who is on the other end of that FK.
    """

    with tenant_scope(tenant):
        return _build(tenant)


def _build(tenant: Any) -> tuple[list[Person], int, bool]:
    """The reader proper. Always runs inside ``tenant_scope(tenant)``."""

    # --- 1. used invite codes: how each grant was made -------------------
    #
    # Read first so both loops below can consult it. Only redeemed invites
    # matter: an outstanding code has not granted anything yet.
    #
    # Ordered oldest-first, overriding ``StaffInvite.Meta.ordering``
    # (``-created_at``). The master map below is last-write-wins, so
    # inheriting the descending default would leave it holding the OLDEST
    # redemption — a master unlinked and re-invited would report the first
    # attempt as «с».
    invites = (
        StaffInvite.objects.filter(tenant=tenant, used_at__isnull=False)
        .order_by("used_at")
        .values("role", "used_by_id", "used_at", "catalog_master_id")
    )

    # ``(bot_user_id, role) -> every moment a code granted that pair``.
    #
    # A list rather than a set membership test, because «this person once
    # redeemed an admin code» is not the same claim as «THIS admin row came
    # from a code». Аня redeems a code, is revoked, and is later re-added
    # by a management command: stamping both rows «по коду доступа» tells
    # the owner a live code let her back in, which is the opposite of what
    # the field exists to say — and is the reading that would send her
    # hunting for a code nobody holds.
    staff_code_grants: dict[tuple[UUID, str], list[datetime]] = {}
    master_code_grants: dict[UUID, datetime] = {}
    for inv in invites:
        if inv["role"] == StaffInvite.Role.MASTER:
            if inv["catalog_master_id"] is not None:
                master_code_grants[inv["catalog_master_id"]] = inv["used_at"]
        elif inv["used_by_id"] is not None:
            staff_code_grants.setdefault((inv["used_by_id"], inv["role"]), []).append(
                inv["used_at"]
            )

    people: dict[str, Person] = {}

    # --- 2. the admin side: TenantStaff ---------------------------------
    #
    # Revoked rows are included on purpose — see ``Person.is_active``.
    # Explicit ``.values()`` rather than ``select_related``: it names every
    # column that crosses this boundary, and ``phone`` is not among them.
    staff_rows = TenantStaff.objects.filter(tenant=tenant).values(
        "bot_user_id",
        "role",
        "created_at",
        "deactivated_at",
        "bot_user__display_name",
        "bot_user__client_name",
    )
    for row in staff_rows:
        bot_user_id = row["bot_user_id"]
        key = f"bot:{bot_user_id}"
        person = people.get(key)
        if person is None:
            person = Person(
                key=key,
                bot_user_id=bot_user_id,
                master_id=None,
                name=_name_from(row["bot_user__display_name"], row["bot_user__client_name"]),
            )
            people[key] = person
        person.roles.append(
            RoleGrant(
                role=row["role"],
                state="active" if row["deactivated_at"] is None else "revoked",
                source=_staff_source(
                    row["created_at"],
                    staff_code_grants.get((bot_user_id, row["role"])),
                ),
                # The moment the role began, which is the row's own
                # birthday. The invite's ``used_at`` is the same instant
                # when a code was involved and does not exist when one
                # was not, so the row is the answer that always exists.
                since=row["created_at"],
            )
        )

    # --- 3. the service-delivery side: CatalogMaster ---------------------
    #
    # Archived masters are included for the same reason revoked staff rows
    # are.
    #
    # ``invite_status`` is read because without it this roster contradicts
    # the auth layer. ``resolve_role`` grants the master role only on the
    # landed predicate; a PENDING row — the state the invite-create path
    # writes, with ``is_active=False, archived_at=None`` — would read as
    # «доступ отозван» if judged by the other two columns first, which is
    # the lie DRF-1506 fixed: nobody revoked anything, and the owner's
    # next move is to resend the invite. Agreeing with ``resolve_role``
    # on who is a master is the whole point of a screen that answers
    # «кто здесь кто».
    master_rows = CatalogMaster.objects.filter(tenant=tenant).values(
        "id",
        "name",
        "linked_bot_user_id",
        "is_active",
        "archived_at",
        "invited_at",
        "invite_status",
        "linked_bot_user__display_name",
        "linked_bot_user__client_name",
    )
    for row in master_rows:
        linked_id = row["linked_bot_user_id"]
        # The bridge: a linked master lands on the SAME key as her staff
        # rows, which is the whole reason an owner-master appears once.
        key = f"bot:{linked_id}" if linked_id is not None else f"master:{row['id']}"
        person = people.get(key)
        if person is None:
            person = Person(
                key=key,
                bot_user_id=linked_id,
                master_id=row["id"],
                name="",
            )
            people[key] = person
        else:
            person.master_id = row["id"]
        # The catalog name wins over the channel-reported display name:
        # it is what the salon calls this person on every other screen.
        #
        # Overwriting unconditionally looks like it could clobber a good
        # staff-side name with «Без имени», and cannot. The key is
        # ``bot:{linked_id}``, so an existing person here can only have
        # come from a staff row carrying the SAME ``bot_user`` — the
        # fallback below therefore reads the identical two columns and
        # produces the identical answer.
        person.name = row["name"] or _name_from(
            row["linked_bot_user__display_name"], row["linked_bot_user__client_name"]
        )

        code_used_at = master_code_grants.get(row["id"])
        if code_used_at is not None:
            source: RoleSource = "access_code"
            since: datetime | None = code_used_at
        elif row["invited_at"] is not None:
            # The master-invite flow stamps ``invited_at`` and nothing
            # else does; ``invite_token`` is cleared on acceptance, so it
            # cannot answer this after the fact.
            source, since = "master_invite", row["invited_at"]
        else:
            # A row the catalog sync produced. Saying «direct» with no
            # date is the honest answer — inventing ``synced_at`` here
            # would report the last mirror run as a hiring date.
            source, since = "direct", None

        person.roles.append(
            RoleGrant(
                role="master",
                state=master_state(
                    archived_at=row["archived_at"],
                    is_active=bool(row["is_active"]),
                    invite_status=row["invite_status"],
                ),
                source=source,
                since=since,
            )
        )

    ordered = sorted(people.values(), key=_person_sort_key)
    total = len(ordered)
    truncated = total > MAX_ROSTER_PEOPLE
    return ordered[:MAX_ROSTER_PEOPLE], total, truncated


def _name_from(display_name: str | None, client_name: str | None) -> str:
    """Best available human-readable name, never a phone number."""

    for candidate in (display_name, client_name):
        if candidate and candidate.strip():
            return candidate.strip()
    return "Без имени"


__all__ = ["MAX_ROSTER_PEOPLE", "Person", "RoleGrant", "build_staff_roster"]
