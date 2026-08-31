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

### What is deliberately absent

**No phone number, by any path.** ``DRF-1039`` is the owner's decision
that a client's phone never reaches an executor; it does not speak about
staff, and no other decision does either. Rather than infer permission
from silence, this reader never loads the column: both queries below are
``.values()`` with explicit field lists, so a phone cannot reach the
serialiser even by way of a future ``select_related``.

**No actions.** This module answers «who works here and what are they»
and nothing else. Changing a role and revoking access are owner
decisions that were deliberately not part of this work.

### Cost

Three queries, all indexed, regardless of salon size: staff rows, master
rows, and the used invites that explain how each role was granted. The
merge itself is in Python because it spans two tables that have no join
between them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from apps.catalog.models import CatalogMaster
from apps.tenancy.models import StaffInvite, TenantStaff

#: Hard ceiling on people returned in one answer.
#:
#: There is no pagination here on purpose. A cursor over a merged view of
#: two tables that share no sort key is a real chunk of machinery, and no
#: salon has this many employees — the pilot has five. The cap exists so
#: that a data anomaly (a botched catalog sync inventing rows) degrades
#: into a truncated list with a flag rather than an unbounded response.
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


@dataclass
class RoleGrant:
    """One role one person holds (or held) at this salon.

    ``active`` is per-role, not per-person: an admin who is still a master
    has one live grant and one revoked one, and flattening that to a
    single person-level flag would report her as either fully present or
    fully gone. Both are wrong.
    """

    role: str
    active: bool
    source: RoleSource
    since: datetime | None

    def to_payload(self) -> dict[str, Any]:
        return {
            "role": self.role,
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

    ``all_tenants`` managers are used with an explicit ``tenant=`` filter
    throughout. The caller is already inside ``tenant_scope`` (the admin
    auth decorator enters it), so the default manager would do — but this
    reader crosses two apps and one of them (``CatalogMaster``) is a sync
    mirror, and an explicit filter is the difference between «scoped» and
    «scoped as long as nobody calls this from a Celery task».
    """

    # --- 1. used invite codes: how each grant was made -------------------
    #
    # Read first so both loops below can consult it. Only redeemed invites
    # matter: an outstanding code has not granted anything yet.
    invites = StaffInvite.all_tenants.filter(
        tenant=tenant,
        used_at__isnull=False,
    ).values("role", "used_by_id", "used_at", "catalog_master_id")

    staff_code_grants: set[tuple[UUID, str]] = set()
    master_code_grants: dict[UUID, datetime] = {}
    for inv in invites:
        if inv["role"] == StaffInvite.Role.MASTER:
            if inv["catalog_master_id"] is not None:
                master_code_grants[inv["catalog_master_id"]] = inv["used_at"]
        elif inv["used_by_id"] is not None:
            staff_code_grants.add((inv["used_by_id"], inv["role"]))

    people: dict[str, Person] = {}

    # --- 2. the admin side: TenantStaff ---------------------------------
    #
    # Revoked rows are included on purpose — see ``Person.is_active``.
    # Explicit ``.values()`` rather than ``select_related``: it names every
    # column that crosses this boundary, and ``phone`` is not among them.
    staff_rows = TenantStaff.all_tenants.filter(tenant=tenant).values(
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
                active=row["deactivated_at"] is None,
                source=(
                    "access_code" if (bot_user_id, row["role"]) in staff_code_grants else "direct"
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
    # are. ``is_active`` on the payload mirrors what the Команда screen
    # already calls «Активные / Архив», so the two surfaces agree about
    # what an archived master is.
    master_rows = CatalogMaster.all_tenants.filter(tenant=tenant).values(
        "id",
        "name",
        "linked_bot_user_id",
        "is_active",
        "archived_at",
        "invited_at",
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
                active=row["archived_at"] is None and bool(row["is_active"]),
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
