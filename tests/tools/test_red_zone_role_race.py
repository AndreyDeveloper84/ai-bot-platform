"""Every `CREATE ROLE` block in migration 0008 must survive a RACE, not just
a sequential repeat (DRF-1349 / PR #1560).

WHAT BROKE. Sharding `pytest apps/` five ways raised the parallelism, and
shard 3 came back with `failures=0 errors=340` — 340 setup failures, all of
them this:

    django.db.utils.IntegrityError: duplicate key value violates unique
    constraint "pg_authid_rolname_index"
    DETAIL:  Key (rolname)=(ayla_ops_redzone_break_glass) already exists.
    CONTEXT: SQL statement "CREATE ROLE ayla_ops_redzone_break_glass"

WHY THE OLD HANDLER MISSED IT. Roles are a CLUSTER object. Parallel test
databases migrate into one cluster and collide on the role name.
`duplicate_object` (42710) comes from the catalog probe that runs before
the insert, so it only fires on a sequential repeat. Under a real race both
probes find nothing, both proceed, and the loser's insert hits the unique
index on pg_authid — `unique_violation` (23505), which a
`WHEN duplicate_object` handler does not catch.

WHY THIS TEST COUNTS INSTEAD OF SEARCHING. The race is not reproducible on
demand, so there is no honest "run it 100 times and see" assertion here.
What IS checkable, cheaply and without a database, is that no block was
left behind: a per-block search that stops at the first match would stay
green with four blocks still carrying the one-code handler. So assert the
counter — every `DO $$` block, and the same number of two-code handlers.

The behavioural claim (old text raises 23505, new text swallows it) was
proved separately against a real Postgres 16 cluster by holding one
`CREATE ROLE` open in an uncommitted transaction, which makes the racing
backend block on `Lock/transactionid` and then lose deterministically.
"""

from __future__ import annotations

import re
from pathlib import Path

MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "apps"
    / "identity"
    / "migrations"
    / "0008_red_zone_db_security.py"
)

# Only the executable SQL strings, never the prose. The module docstring and
# the explanatory comment both mention `duplicate_object` on its own, and a
# naive grep over the whole file would count those as handlers.
_SQL_BLOCK = re.compile(r"DO \$\$ BEGIN.*?END \$\$;", re.DOTALL)


def _sql_blocks() -> list[str]:
    """Each `DO $$` block, whitespace-normalised.

    Normalising first means the assertions below are plain substring checks
    instead of whitespace-tolerant regexes, so reindenting the migration
    cannot quietly turn this guard green.
    """
    raw = _SQL_BLOCK.findall(MIGRATION.read_text(encoding="utf-8"))
    return [" ".join(block.split()) for block in raw]


# The handler every block must carry, and the one no block may still carry.
BOTH_CODES = "EXCEPTION WHEN duplicate_object OR unique_violation THEN NULL;"
ONE_CODE_ONLY = "EXCEPTION WHEN duplicate_object THEN NULL;"


def test_migration_still_creates_five_roles() -> None:
    """The count this file guards is 5. If a role is added or removed on
    purpose, update it here deliberately — that is the point of pinning it."""
    assert len(_sql_blocks()) == 5, (
        f"expected 5 `DO $$` CREATE ROLE blocks, found {len(_sql_blocks())}. "
        "A new role needs the two-code handler too."
    )


def test_every_create_role_block_handles_the_race() -> None:
    """Each block must name BOTH SQLSTATEs.

    `duplicate_object` alone closes the sequential repeat and leaves the
    concurrent one open — that is the 340-error failure this guards.
    """
    offenders = [block for block in _sql_blocks() if BOTH_CODES not in block]
    assert not offenders, (
        f"{len(offenders)} of {len(_sql_blocks())} CREATE ROLE blocks do not handle "
        "`unique_violation` (23505) alongside `duplicate_object` (42710), so they "
        "still crash when two test databases race to create the role:\n\n" + "\n\n".join(offenders)
    )


def test_no_block_was_left_on_the_one_code_handler() -> None:
    """Positive guard's negative twin: the old text must be gone entirely."""
    stale = [b for b in _sql_blocks() if ONE_CODE_ONLY in b]
    assert not stale, (
        f"{len(stale)} block(s) still carry the one-code handler "
        "`EXCEPTION WHEN duplicate_object THEN NULL`."
    )
