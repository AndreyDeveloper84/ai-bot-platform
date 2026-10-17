"""The base compose publishes its data stores on loopback only (DRF-2647).

### What was wrong

``docker-compose.yml`` published ``postgres`` («5432:5432», password
``platform`` in the same file), ``redis`` («6379:6379», no password) and
``chromadb`` («8001:8000», anonymous without ``CHROMA_AUTH_TOKEN``; image
0.5.20, under Dependabot alerts №34 and №36) on EVERY interface. The pilot is
not exposed — the staging overlay drops those ports and switches Chroma off —
but production deploys the base file without that overlay, and anyone who
brings it up on a host with a public address exposes all three.

### What this file holds — by the FILE, not by a running container

The node reads the declaration. A check against running containers would pass
on the pilot, where none of these ports is open, while the defect sits in the
file.

* each store publishes only on ``127.0.0.1``;
* each store still publishes something (local development keeps localhost);
* the resolver (``docker compose config``) agrees: the bound host IP is
  ``127.0.0.1``, not empty.

### What it deliberately does not hold

``web`` («8000:8000») is NOT in this file's scope: whether the production
front reaches it from outside is the owner's decision (C), not a store's.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.test_staging_stack_completeness import BASE_COMPOSE, _load

STORES = ("postgres", "redis", "chromadb")
LOOPBACK = "127.0.0.1"


def _published(service: str) -> list[str]:
    return [str(p) for p in (_load(BASE_COMPOSE)["services"][service].get("ports") or [])]


@pytest.mark.parametrize("service", STORES)
def test_a_store_publishes_on_loopback_only(service: str) -> None:
    ports = _published(service)
    assert ports, f"{service} publishes nothing — local development would lose localhost"
    assert all(p.startswith(f"{LOOPBACK}:") for p in ports), (
        f"{service} publishes {ports}: a port without a host address listens on every "
        "interface. Bind it to 127.0.0.1 — containers reach each other by service name."
    )


def test_the_three_stores_are_the_ones_in_the_file() -> None:
    """If a store is renamed or added, this list must follow — or the node above checks nothing."""

    services = set(_load(BASE_COMPOSE)["services"])
    assert set(STORES) <= services


def test_the_resolver_binds_the_stores_to_loopback(tmp_path: Path) -> None:
    """What `docker compose` will actually do with the declaration."""

    if shutil.which("docker") is None:  # pragma: no cover - depends on the host
        pytest.skip("docker CLI is unavailable; the file-level nodes above still run")
    shutil.copy(BASE_COMPOSE, tmp_path / BASE_COMPOSE.name)
    result = subprocess.run(
        ["docker", "compose", "-f", BASE_COMPOSE.name, "config", "--format", "json"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if result.returncode != 0:  # pragma: no cover - depends on the host
        pytest.skip(f"`docker compose config` did not run here: {result.stderr.strip()[:300]}")
    services = json.loads(result.stdout)["services"]
    for service in STORES:
        ports = services[service].get("ports") or []
        assert ports, service
        assert {p.get("host_ip") for p in ports} == {LOOPBACK}, (service, ports)
