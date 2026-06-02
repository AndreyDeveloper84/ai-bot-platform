"""Canonical cross-service contract fixtures (Gamma A10).

Single source of truth for the event/request payloads that flow between
Ayla djangoproject (publisher) and ai-bot-platform (consumer). Before
this package, every consumer test in ``apps/eventbus/tests/`` hand-rolled
its own ``data`` dict — and the Ayla emit tests hand-rolled a *different*
one. The two drifted (codex audit P0-1: ``payment.confirmed`` vs the
canonical ``payment.captured``). These fixtures kill that drift: both
repos load the same bytes.

Contents
--------
- ``booking.created.v1.json``      — event envelope, taxonomy §3.1
- ``payment.captured.v1.json``     — event envelope, taxonomy §3.6
- ``payment.failed.v1.json``       — event envelope, taxonomy §3.7
- ``recommendations.request.json`` — bot -> Ayla REST request body for
  ``POST /api/v1/internal/me/catalog/recommendations/`` (Ayla
  ``RecommendationsRequestSerializer``: ``lat`` / ``lon`` / ``goal``).
  NOT an event — no envelope, no ``event_version``.

Cross-repo mirror (hash guard)
------------------------------
``MANIFEST.sha256`` pins the sha256 of every fixture. ``test_contract
_fixtures.py`` recomputes and compares, so an accidental edit to a
fixture without regenerating the manifest fails CI in THIS repo. The
Ayla side mirrors these files + the manifest and runs the equivalent
assertion; if either copy drifts from its manifest, that repo's CI
fails. See ``README.md`` for the sync procedure and the Alpha handoff.

Regenerate the manifest after an intentional fixture change::

    python -m tests.fixtures.contracts --write-manifest
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

CONTRACTS_DIR = Path(__file__).resolve().parent
MANIFEST_PATH = CONTRACTS_DIR / "MANIFEST.sha256"

# The canonical fixtures. Kept explicit (not a glob) so a stray file
# dropped into the directory can't silently become "a contract".
EVENT_FIXTURES = (
    "booking.created.v1.json",
    "booking.confirmed.v1.json",
    "payment.captured.v1.json",
    "payment.failed.v1.json",
)
REQUEST_FIXTURES = ("recommendations.request.json",)
ALL_FIXTURES = EVENT_FIXTURES + REQUEST_FIXTURES


def contract_path(name: str) -> Path:
    """Absolute path to a fixture. Raises if it isn't a known contract."""
    if name not in ALL_FIXTURES:
        raise KeyError(
            f"{name!r} is not a registered contract fixture. Known: {', '.join(ALL_FIXTURES)}"
        )
    return CONTRACTS_DIR / name


def load_contract(name: str) -> dict[str, Any]:
    """Load and JSON-parse a fixture into a dict."""
    return json.loads(contract_path(name).read_text(encoding="utf-8"))


def sha256_of(name: str) -> str:
    """sha256 hex digest of a fixture's raw bytes (the drift anchor)."""
    return hashlib.sha256(contract_path(name).read_bytes()).hexdigest()


def compute_manifest() -> dict[str, str]:
    """Map fixture name -> sha256, in declared order."""
    return {name: sha256_of(name) for name in ALL_FIXTURES}


def read_manifest() -> dict[str, str]:
    """Parse ``MANIFEST.sha256`` (``<sha>  <name>`` lines, sha256sum format)."""
    manifest: dict[str, str] = {}
    for line in MANIFEST_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        digest, _, name = line.partition("  ")
        manifest[name.strip()] = digest.strip()
    return manifest


def _write_manifest() -> None:
    lines = [
        "# Canonical contract-fixture hashes (Gamma A10). Regenerate with:",
        "#   python -m tests.fixtures.contracts --write-manifest",
        "# Both ai-bot-platform and Ayla assert their local copies match this.",
    ]
    lines += [f"{digest}  {name}" for name, digest in compute_manifest().items()]
    # newline="\n" so regeneration is byte-deterministic on every OS
    # (the fixtures are LF-pinned; the manifest should be too).
    MANIFEST_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
