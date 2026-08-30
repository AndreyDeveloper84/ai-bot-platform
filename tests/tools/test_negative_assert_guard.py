"""Tests for tools/lint/negative_assert_guard.py — the DRF-1411 guard.

Four kinds of test here, and the first one is the point.

**Calibration** pins the guard against the defect it was built for. The
scanner must flag the DRF-1406 shape — an absence asserted over a body
nobody proved had content. This is not ceremony: while building this
guard the shape was missed **twice**, both times because the search
happened one line above the assertion and the scanner only looked at the
assertion itself. Both times the scan reported a confident, empty,
wrong result.

    A green run of a scanner that cannot see is indistinguishable from
    an honest "nothing found".

That is the whole reason this file leads with calibration, and the
reason the calibration uses an inline copy of the shape rather than the
live ``test_pii_boundary.py``: when DRF-1406 is fixed that file gains a
presence assertion and stops being an example, and a calibration that
disappears the moment the bug is fixed protects nothing afterwards.

**Mechanics** prove the guard bites: the needle/haystack shape with no
presence assertion fails, and a ``status_code == 200`` does not rescue it
— that mistaken guard is the entire defect.

**Silence** is tested just as hard. A lint that cries wolf gets switched
off. Asserting a fetched collection is itself empty is a legitimate,
common, self-explaining test (242 sites in this repo) and must pass
without a word; so must a cardinality query, a pure function over
literals, and any site with a real presence assertion ahead of it.

**The real tree** is pinned last, as a ratchet. The defect this guard
exists for is not "the guard has a bug" — it is "somebody added an
assertion that checks nothing and nothing said so".
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]

# `tools/` is not a package (no __init__.py) — import via path injection,
# same pattern as test_import_boundaries.py / test_personal_field_guard.py.
sys.path.insert(0, str(_PROJECT_ROOT / "tools" / "lint"))
import negative_assert_guard as guard  # type: ignore[import-not-found]  # noqa: E402


def _scan(body: str) -> list[guard.Site]:
    unguarded, _bare = guard.scan_file(Path("test_sample.py"), body)
    return unguarded


def _bare(body: str) -> list[guard.Site]:
    _unguarded, bare = guard.scan_file(Path("test_sample.py"), body)
    return bare


# --------------------------------------------------------------------------
# 1. Calibration — the guard must see the defect it exists for
# --------------------------------------------------------------------------

#: The DRF-1406 shape, structurally verbatim from
#: ``apps/master_api/tests/test_pii_boundary.py``. Kept inline on purpose:
#: the live file gains a presence assertion when DRF-1406 is fixed, and a
#: calibration that evaporates with the fix leaves the scanner unpinned
#: for every defect after it.
_DRF_1406 = """
def test_no_forbidden_pii_key_anywhere_in_response(client, seeded_surface, route_name):
    url = SWEPT_READ_ROUTES[route_name]()
    resp = client.get(url, HTTP_AUTHORIZATION=init_data_header("12345"))
    assert resp.status_code == 200, (route_name, resp.status_code)

    found = find_forbidden_pii(resp.json())
    assert found == [], f"{route_name} leaked forbidden PII at {found}."
"""


def test_calibration_flags_the_drf_1406_shape() -> None:
    """If this fails, the guard is blind and its silence means nothing."""
    sites = _scan(_DRF_1406)
    assert [s.lineno for s in sites], (
        "The guard no longer sees the defect it was built for. Its green "
        "runs are now worthless — an empty result from a scanner that "
        "cannot see reads exactly like an honest 'nothing found'. Fix the "
        "guard before trusting any report it produces."
    )
    assert sites[0].func == "test_no_forbidden_pii_key_anywhere_in_response"


def test_calibration_survives_the_assignment_hop() -> None:
    """The search happens a line above the assertion.

    Missed twice while building this guard, both times silently. The
    assertion names a plain local (`found`); only following it back to
    `find_forbidden_pii(resp.json())` reveals a search over content.
    """
    inline = """
def test_direct(client):
    resp = client.get("/x")
    assert find_forbidden_pii(resp.json()) == []
"""
    assert _scan(inline), "guard must flag the search even without the hop"
    assert _scan(_DRF_1406), "guard must follow the assignment hop"


# --------------------------------------------------------------------------
# 2. Mechanics — it bites where it must
# --------------------------------------------------------------------------


def test_status_code_is_not_a_presence_guard() -> None:
    """A 200 with an empty body is still a 200. This single mistaken
    guard is the whole of DRF-1406."""
    body = """
def test_x(client):
    resp = client.get("/x")
    assert resp.status_code == 200
    assert "+7999" not in resp.json()
"""
    assert _scan(body), "status_code must never count as proof of content"


def test_needle_not_in_haystack_is_flagged() -> None:
    body = """
def test_x(client):
    resp = client.get("/x")
    text = resp.content.decode()
    assert CUSTOMER_PHONE not in text
"""
    assert _scan(body)


def test_bare_marker_is_itself_a_violation() -> None:
    """«Somebody looked and said why» is the product. A marker with no
    reason skips the looking and keeps the silence."""
    body = """
def test_x(client):
    resp = client.get("/x")
    assert find_forbidden_pii(resp.json()) == []  # empty-assert-ok
"""
    assert _bare(body)
    # empty-assert-ok: the scanner reporting nothing IS the fact under test
    assert not _scan(body)


# --------------------------------------------------------------------------
# 3. Silence — it must not cry wolf
# --------------------------------------------------------------------------


def test_presence_assertion_ahead_of_it_silences_the_site() -> None:
    """The shape every sound test in this repo already uses."""
    body = """
def test_x(client):
    resp = client.get("/x")
    assert resp.json()["items"] != []
    assert find_forbidden_pii(resp.json()) == []
"""
    # empty-assert-ok: the scanner reporting nothing IS the fact under test
    assert not _scan(body)


def test_asserting_the_collection_itself_is_empty_is_fine() -> None:
    """`items == []` IS the fact under test — the test says what it means.
    242 sites in this repo; flagging them would switch the lint off."""
    body = """
def test_x(client):
    resp = client.get("/x")
    assert resp.json()["items"] == []
"""
    # empty-assert-ok: the scanner reporting nothing IS the fact under test
    assert not _scan(body)


def test_cardinality_query_is_fine() -> None:
    body = """
def test_x(client):
    client.post("/x")
    assert BookingRequest.all_tenants.filter(tenant=t).count() == 0
    assert not ScheduleException.all_tenants.filter(master=m).exists()
"""
    # empty-assert-ok: the scanner reporting nothing IS the fact under test
    assert not _scan(body)


def test_pure_function_over_literals_is_fine() -> None:
    """Nothing was fetched, so nothing could have quietly become empty."""
    body = """
def test_x():
    assert parse_registry({}) == ()
    assert redact_data_for_dlq({}) == {}
"""
    # empty-assert-ok: the scanner reporting nothing IS the fact under test
    assert not _scan(body)


def test_marker_with_a_reason_silences_the_site() -> None:
    body = """
def test_x(client):
    resp = client.get("/x")
    # empty-assert-ok: anonymous callers legitimately see an empty body
    assert find_forbidden_pii(resp.json()) == []
"""
    # empty-assert-ok: the scanner reporting nothing IS the fact under test
    assert not _scan(body)


def test_non_test_functions_are_out_of_scope() -> None:
    body = """
def helper(client):
    resp = client.get("/x")
    assert find_forbidden_pii(resp.json()) == []
"""
    # empty-assert-ok: the scanner reporting nothing IS the fact under test
    assert not _scan(body)


# --------------------------------------------------------------------------
# 4. The real tree — a ratchet, not a verdict
# --------------------------------------------------------------------------


def test_the_repository_adds_no_new_unguarded_sites() -> None:
    """Green here means nothing NEW was added.

    It does NOT mean the baseline is sound: every line in
    ``negative_assert_guard_baseline.txt`` is a test that may be checking
    something and may be checking nothing, and nobody has been through
    them. Deleting a line is progress.
    """
    rc = guard.main(
        [
            "negative_assert_guard.py",
            str(_PROJECT_ROOT / "apps"),
            str(_PROJECT_ROOT / "tests"),
        ]
    )
    assert rc == 0, (
        "A new absence assertion landed with no presence assertion ahead "
        "of it. Add the presence assertion, or mark the site "
        "`# empty-assert-ok: <why this is genuinely empty>`."
    )


def test_baseline_is_not_silently_empty() -> None:
    """A baseline file that failed to load reads as «no known sites», and
    then the ratchet silently permits everything. Same failure class as a
    blind scanner."""
    baseline = guard.read_baseline()
    assert len(baseline) > 100, (
        f"baseline holds only {len(baseline)} entries — it probably failed "
        "to load, which would make the ratchet permit every new site."
    )


@pytest.mark.parametrize("root", ["apps", "tests"])
def test_guard_actually_walks_both_roots(root: str) -> None:
    """A scanner pointed at nothing also reports nothing."""
    unguarded, _ = guard.scan([_PROJECT_ROOT / root])
    assert unguarded, f"guard found no sites at all under {root}/ — is it walking the tree?"
