"""DRF-2633: the stable-test-id guard, calibrated on both known cases.

Each check must go red on a known-volatile id and stay silent on a stable one
— on a throwaway pytest project, so the calibration does not depend on what
the real suite happens to contain today.
"""

from __future__ import annotations

import importlib.util
import pathlib
import textwrap

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "stable_test_ids", ROOT / "tools/lint/stable_test_ids.py"
)
assert _spec is not None and _spec.loader is not None
sti = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sti)

VOLATILE_UUID = """
    import uuid
    import pytest

    @pytest.mark.parametrize("value", [str(uuid.uuid4())])
    def test_probe(value):
        assert value
"""
STABLE = """
    import pytest

    @pytest.mark.parametrize("value", ["a", "b"])
    def test_probe(value):
        assert value
"""
REORDER = """
    import pytest

    @pytest.mark.parametrize("x", {"alpha", "beta", "gamma", "delta", "epsilon", "zeta"})
    def test_probe(x):
        assert x
"""
BROKEN = """
    def test_probe(:
        pass
"""
ADDRESS = """
    import pytest

    def handler():
        pass

    @pytest.mark.parametrize("fn", [handler], ids=str)
    def test_probe(fn):
        assert fn
"""


def _project(tmp_path: pathlib.Path, body: str) -> list[str]:
    (tmp_path / "test_probe.py").write_text(textwrap.dedent(body), encoding="utf-8")
    # A bare project: no repo conftest, no Django.
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    # `-q` here and not in the guard: the repo's addopts already carry it, and
    # quiet 2 prints no ids. A bare project has no addopts, so it asks itself.
    return ["-q", "-p", "no:django", "-p", "no:randomly", str(tmp_path)]


def _twice(tmp_path: pathlib.Path, body: str) -> tuple[list[str], list[str]]:
    args = _project(tmp_path, body)
    return sti.collect(args, seed="1", cwd=str(tmp_path)), sti.collect(
        args, seed="2", cwd=str(tmp_path)
    )


class TestDoubleCollection:
    def test_a_uuid4_in_parametrize_is_volatile(self, tmp_path):
        first, second = _twice(tmp_path, VOLATILE_UUID)
        assert len(first) == 1  # presence: it collected
        assert sti.volatile(first, second) != []

    def test_a_stable_parameter_is_not(self, tmp_path):
        first, second = _twice(tmp_path, STABLE)
        assert len(first) == 2  # presence
        assert (
            sti.volatile(first, second),
            sti.reordered(first, second),
            sti.address_ids(first),
        ) == ([], [], [])

    def test_the_same_ids_in_another_order_are_reported(self, tmp_path):
        """xdist compares ORDERED lists: a set of strings in parametrize gives
        the same ids in a hash-dependent order — the shard fails, a set
        comparison would stay green. Seeds 1 and 2 order these six differently."""
        first, second = _twice(tmp_path, REORDER)
        assert sorted(first) == sorted(second)  # presence: same ids
        assert sti.reordered(first, second) != []

    def test_a_broken_collection_is_refused_not_compared(self, tmp_path):
        """«Nothing collected twice» compares equal to itself — refused loudly."""
        with pytest.raises(SystemExit):
            _twice(tmp_path, BROKEN)


class TestAddressPattern:
    def test_a_function_id_carries_an_address(self, tmp_path):
        """The case double collection may miss (addresses can coincide
        between processes) — the pattern names it regardless."""
        first, _ = _twice(tmp_path, ADDRESS)
        assert len(sti.address_ids(first)) == 1, first

    @pytest.mark.parametrize(
        ("node_id", "flagged"),
        [
            ("t.py::f[<function h at 0x0000026FB04AC180>]", True),
            ("t.py::f[<Obj object at 0x7f00aa11>]", True),
            ("t.py::f[2026-09-19T12:14:30+00:00-15]", False),  # stable, from a constant
            ("t.py::f[apps.eventbus.consumers.booking.handle_x]", False),
        ],
    )
    def test_the_shape(self, node_id, flagged):
        assert bool(sti.address_ids({node_id})) is flagged

    def test_a_hex_literal_written_in_the_source_is_not_an_address(self, tmp_path):
        """Pair: the same shape, once written in the file (a snippet under
        test), once not — only the second is an address."""
        (tmp_path / "t.py").write_text("SNIPPET = 'x & 0xFFFFFFFF'", encoding="utf-8")
        literal = "t.py::f[x & 0xFFFFFFFF]"
        address = "t.py::f[<function h at 0x7f00aa11bb22>]"
        assert sti.address_ids({literal, address}, root=str(tmp_path)) == [address]


class TestTupleMindedIds:
    OLD = (
        "import pytest\n"
        '@pytest.mark.parametrize("key,handler", [], '
        'ids=lambda hk: f"{hk[0]}@v{hk[1]}" if isinstance(hk, tuple) else str(hk))\n'
        "def test_x(key, handler): pass\n"
    )
    FIXED = (
        "import pytest\n"
        "def _pid(v):\n"
        "    if isinstance(v, tuple):\n"
        '        return f"{v[0]}@v{v[1]}"\n'
        "    if isinstance(v, str | int):\n"
        "        return str(v)\n"
        "    return type(v).__name__\n"
        '@pytest.mark.parametrize("key,handler", [], ids=_pid)\n'
        "def test_x(key, handler): pass\n"
    )

    @pytest.mark.parametrize(
        "ids",
        [
            'lambda v: f"{v[0]}-{v[1]}" if isinstance(v, tuple) else (v if isinstance(v, str) else str(v))',
            'lambda v: f"{v[0]}-{v[1]}" if isinstance(v, tuple) else f"{v}"',
            'lambda v: f"{v[0]}-{v[1]}" if isinstance(v, tuple) else "%s" % v',
            'lambda v: f"{v[0]}-{v[1]}" if isinstance(v, tuple) else format(v)',
        ],
        ids=["str-in-the-else-of-a-scalar-check", "f-string", "percent", "format"],
    )
    def test_every_way_of_stringifying_the_rest_is_seen(self, ids):
        source = "\n".join(
            [
                "import pytest",
                f"@pytest.mark.parametrize(argnames='a,b', argvalues=[], ids={ids})",
                "def test_x(a, b): pass",
            ]
        )
        assert sti.tuple_minded_ids(source, "t.py") == ["t.py:2"]

    def test_the_old_form_is_flagged_and_the_fixed_one_is_not(self):
        pair = (
            sti.tuple_minded_ids(self.OLD, "old.py"),
            sti.tuple_minded_ids(self.FIXED, "fixed.py"),
        )
        assert pair == (["old.py:2"], [])

    def test_the_whole_repo_is_clean_today(self):
        """The census, by construction, over apps/ and tests/ (the known site
        in tests/contracts is fixed in the same change)."""
        assert sti.tuple_minded_census([str(ROOT / "apps"), str(ROOT / "tests")]) == []
