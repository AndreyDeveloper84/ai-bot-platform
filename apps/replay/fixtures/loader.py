"""Fixture YAML loader (DRF-509 / Sprint 5 / C2).

Recursive directory walk → parsed :class:`Fixture` list. Used by:
- D7 CLI runner
- G2 e2e exit-gate test

### Error policy

The loader raises ``ValueError`` with a helpful message including the
file path on:
- Malformed YAML
- Missing required keys
- Unknown top-level keys

We don't catch + log — the runner needs to surface fixture errors so
operators see them and fix the YAML.
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from apps.replay.fixtures.schema import Fixture, from_dict

logger = logging.getLogger(__name__)


def load_fixture(path: str | Path) -> Fixture:
    """Load a single YAML file as a Fixture.

    Args:
      path: path to a .yaml/.yml file.

    Raises:
      ValueError: malformed YAML or schema violation. Error message
        carries the file path for operator UX.
    """

    p = Path(path)
    if not p.is_file():
        raise ValueError(f"Fixture {p}: not a file")

    try:
        with p.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        raise ValueError(f"Fixture {p}: malformed YAML — {exc}") from exc

    return from_dict(data, source=str(p))


def load_fixture_set(directory: str | Path) -> list[Fixture]:
    """Recursively load every .yaml / .yml under `directory`.

    Returns:
      List of Fixture instances, sorted by file path for deterministic
      ordering (CI run order = file system order = git diff order).

    Raises:
      ValueError: directory doesn't exist OR any fixture fails to
        parse. We bail on the first error — partial loads are useless
        for a CI gate.
    """

    d = Path(directory)
    if not d.is_dir():
        raise ValueError(f"Fixture set {d}: not a directory")

    paths = sorted(p for p in d.rglob("*") if p.is_file() and p.suffix in {".yaml", ".yml"})
    fixtures: list[Fixture] = []
    for p in paths:
        fixtures.append(load_fixture(p))
    logger.info("replay.fixtures.loaded directory=%s count=%d", d, len(fixtures))
    return fixtures
