"""CLI: regenerate the contract-fixture hash manifest.

python -m tests.fixtures.contracts --write-manifest
"""

from __future__ import annotations

import sys

from . import MANIFEST_PATH, _write_manifest

if "--write-manifest" in sys.argv:
    _write_manifest()
    print(f"wrote {MANIFEST_PATH}")
else:
    print("usage: python -m tests.fixtures.contracts --write-manifest")
