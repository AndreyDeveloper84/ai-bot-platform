"""Tests for tools/ci/miniapp_bundle_drift.py — the DRF-1257 served-bundle guard.

Why this file exists at all
---------------------------
The guard shipped without tests and was blind for its whole life. It pinned the
sourcemap prefix to `../../src/`, the depth an in-place `npx vite build` emits,
while the release script it was written to accompany
(`infra/deploy/miniapp-release.sh`) builds into `releases/<id>/` and therefore
emits `../../../src/`. Every run against a bundle published the intended way
died with "could not run" instead of naming the drift — and the release
script's own closing proof step, which invokes this file, could never pass.

The regression that matters is therefore not "does it detect drift" but "does
it detect drift at BOTH output depths". That is the first test below, and it is
pinned with the two paths that actually occur rather than a generic one.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]

# `tools/` is not a package (no __init__.py) — import via path injection,
# same pattern as test_import_boundaries.py.
sys.path.insert(0, str(_PROJECT_ROOT / "tools" / "ci"))
import miniapp_bundle_drift as guard  # type: ignore[import-not-found]  # noqa: E402


_INDEX_HTML = (
    "<!doctype html><html><head>"
    '<script type="module" crossorigin src="/assets/index-AAAA1111.js"></script>'
    '<link rel="stylesheet" crossorigin href="/assets/index-BBBB2222.css">'
    "</head><body><div id=root></div></body></html>"
)


def _sourcemap(sources: dict[str, str]) -> bytes:
    return json.dumps(
        {
            "version": 3,
            "file": "index-AAAA1111.js",
            "sources": list(sources),
            "sourcesContent": list(sources.values()),
            "names": [],
            "mappings": "",
        }
    ).encode("utf-8")


def _install_fetch(monkeypatch: pytest.MonkeyPatch, responses: dict[str, bytes]) -> None:
    def fake_fetch(url: str) -> bytes:
        try:
            return responses[url]
        except KeyError:  # pragma: no cover - a miswritten test, not a guard failure
            raise guard.CannotCheck(f"unstubbed URL {url}") from None

    monkeypatch.setattr(guard, "fetch", fake_fetch)


def _stub(monkeypatch: pytest.MonkeyPatch, sources: dict[str, str]) -> None:
    _install_fetch(
        monkeypatch,
        {
            "https://pilot.test/": _INDEX_HTML.encode("utf-8"),
            "https://pilot.test/assets/index-AAAA1111.js.map": _sourcemap(sources),
        },
    )


# --------------------------------------------------------------------------
# The regression this file was written for.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("prefix", "layout"),
    [
        ("../../", "npx vite build -> dist/assets/"),
        ("../../../", "miniapp-release.sh -> releases/<id>/assets/"),
    ],
)
def test_application_modules_are_found_at_either_output_depth(
    monkeypatch: pytest.MonkeyPatch, prefix: str, layout: str
) -> None:
    """Both build layouts in this repository must be readable by the guard.

    Pinning one depth is what made the guard blind on 2026-08-23; `layout`
    names the command that produces each so a future change can tell which
    one it is breaking.
    """
    _stub(
        monkeypatch,
        {
            f"{prefix}src/App.tsx": "export const App = 1;\n",
            f"{prefix}src/lib/max-sdk.ts": "export const sdk = 2;\n",
            f"{prefix}node_modules/react/index.js": "module.exports = {};\n",
        },
    )

    modules, js_name, css_name = guard.served_modules("https://pilot.test")

    assert set(modules) == {"App.tsx", "lib/max-sdk.ts"}, layout
    assert js_name == "index-AAAA1111.js"
    assert css_name == "index-BBBB2222.css"


def test_node_modules_are_never_treated_as_application_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dependencies are pinned by package-lock.json, not by this guard.

    They sit at the same depth as `src/`, so widening the depth match must not
    widen what counts as application code.
    """
    _stub(
        monkeypatch,
        {
            "../../../node_modules/react/index.js": "module.exports = {};\n",
            "../../../node_modules/@remix-run/router/dist/router.js": "export {};\n",
            "../../../src/main.tsx": "export const main = 1;\n",
        },
    )

    modules, _, _ = guard.served_modules("https://pilot.test")

    assert set(modules) == {"main.tsx"}


def test_a_bundle_of_some_other_app_is_a_refusal_not_a_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No application modules at all must still be `CannotCheck`, never OK."""
    _stub(monkeypatch, {"../../../node_modules/react/index.js": "module.exports = {};\n"})

    with pytest.raises(guard.CannotCheck) as excinfo:
        guard.served_modules("https://pilot.test")

    # The message must say what it did see; the original said only what it wanted.
    assert "node_modules" in str(excinfo.value)


def test_missing_sources_content_is_a_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    """`build.sourcemap` off would make the guard silently unable to compare."""
    stripped = json.dumps({"version": 3, "sources": ["../../src/App.tsx"]}).encode("utf-8")
    _install_fetch(
        monkeypatch,
        {
            "https://pilot.test/": _INDEX_HTML.encode("utf-8"),
            "https://pilot.test/assets/index-AAAA1111.js.map": stripped,
        },
    )

    with pytest.raises(guard.CannotCheck):
        guard.served_modules("https://pilot.test")


# --------------------------------------------------------------------------
# Comparison semantics.
# --------------------------------------------------------------------------


def test_drift_is_reported_per_module(tmp_path: Path) -> None:
    src = tmp_path / "src"
    (src / "lib").mkdir(parents=True)
    (src / "App.tsx").write_text("export const App = 1;\n", encoding="utf-8")
    (src / "lib" / "max-sdk.ts").write_text("export const sdk = 2;\n", encoding="utf-8")

    drifted, vanished = guard.compare_sources(
        {"App.tsx": "export const App = 1;\n", "lib/max-sdk.ts": "export const sdk = 999;\n"},
        src,
    )

    assert drifted == ["lib/max-sdk.ts"]
    assert vanished == []


def test_a_module_deleted_from_the_tree_is_reported_separately(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()

    drifted, vanished = guard.compare_sources({"screens/Gone.tsx": "export {};\n"}, src)

    assert drifted == []
    assert vanished == ["screens/Gone.tsx"]


def test_line_endings_are_not_drift(tmp_path: Path) -> None:
    """A Windows checkout carries CRLF; a Linux build embeds LF. Not a change."""
    src = tmp_path / "src"
    src.mkdir()
    # Written as bytes on purpose: `write_text` would re-translate the newlines
    # on Windows and the file would no longer be the artefact under test.
    (src / "App.tsx").write_bytes(b"\xef\xbb\xbfexport const App = 1;\r\n")

    drifted, vanished = guard.compare_sources({"App.tsx": "export const App = 1;\n"}, src)

    assert drifted == []
    assert vanished == []
