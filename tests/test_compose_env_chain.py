"""The settings CHAIN, not just its current value (DRF-1391).

`test_allowed_hosts_guard.py` asserts what Django does with a host list.
It would stay green with `DJANGO_ALLOWED_HOSTS: "*"` back in
`docker-compose.yml`, because that value never reaches a unit test — it
reaches the pilot.

So these tests assert the link that actually broke: **no compose layer
may declare `DJANGO_ALLOWED_HOSTS`**, because a compose `environment:`
mapping outranks the same service's `env_file:`, and mappings merge
across `-f` files key by key. That is how `docker-compose.yml`'s `"*"`
survived into the staging stack and overrode `.env.staging`, measured
with `docker compose config`:

    web / worker / shadow-worker   DJANGO_ALLOWED_HOSTS: '*'
    celery-worker / celery-beat    DJANGO_ALLOWED_HOSTS: <.env.staging>

Restoring the value to either compose file fails
`test_no_compose_layer_declares_allowed_hosts`.

The second half guards the readiness probes: the names those probes send
must be documented in `.env.staging.template`, so that tightening the
list on the pilot cannot silently turn every deploy red.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml", reason="PyYAML is needed to read the compose files")

REPO_ROOT = Path(__file__).resolve().parent.parent
BASE_COMPOSE = REPO_ROOT / "docker-compose.yml"
STAGING_COMPOSE = REPO_ROOT / "docker-compose.staging.yml"
ENV_STAGING_TEMPLATE = REPO_ROOT / ".env.staging.template"
DEPLOY_DEV_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "deploy-dev.yml"

#: Services that run Django code and therefore have an ALLOWED_HOSTS.
APP_SERVICES = ("web", "worker", "shadow-worker", "celery-worker", "celery-beat")

GUARDED_KEY = "DJANGO_ALLOWED_HOSTS"


class _TagTolerantLoader(yaml.SafeLoader):
    """SafeLoader that does not choke on compose's `!override` / `!reset`.

    `docker-compose.staging.yml` uses `ports: !override` (a real Compose
    v2.24+ tag). SafeLoader refuses unknown tags outright, so without
    this the file cannot be read at all — and a test that cannot read the
    file is a test that cannot fail.
    """


def _ignore_unknown_tag(loader: yaml.SafeLoader, tag_suffix: str, node: yaml.Node):
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node, deep=True)
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node, deep=True)
    return loader.construct_scalar(node)


_TagTolerantLoader.add_multi_constructor("!", _ignore_unknown_tag)


def _load(path: Path) -> dict:
    return yaml.load(path.read_text(encoding="utf-8"), Loader=_TagTolerantLoader) or {}


def _environment_keys(service: dict) -> set[str]:
    """Keys a service's `environment:` declares, in either compose form."""

    env = service.get("environment") or {}
    if isinstance(env, dict):
        return set(env)
    # List form: ["KEY=value", "KEY"].
    return {str(item).split("=", 1)[0] for item in env}


@pytest.mark.parametrize("compose_path", [BASE_COMPOSE, STAGING_COMPOSE], ids=lambda p: p.name)
@pytest.mark.parametrize("service_name", APP_SERVICES)
def test_no_compose_layer_declares_allowed_hosts(compose_path: Path, service_name: str):
    """A compose `environment:` entry would outrank `.env.staging` again."""

    services = _load(compose_path).get("services") or {}
    service = services.get(service_name)
    if service is None:
        pytest.skip(f"{service_name} is not defined in {compose_path.name}")

    keys = _environment_keys(service)

    # Presence first: prove we are reading a real `environment:` block. A
    # renamed service or a restructured file would otherwise yield an empty
    # set, and "the key is not in an empty set" is not a check.
    assert "DJANGO_SETTINGS_MODULE" in keys, (
        f"{compose_path.name}: service `{service_name}` has no readable "
        "`environment:` block — this test can no longer see what it guards."
    )

    assert GUARDED_KEY not in keys, (
        f"{compose_path.name}: service `{service_name}` declares {GUARDED_KEY} "
        "under `environment:`. Compose merges `environment:` across -f files "
        "key by key and it beats `env_file:`, so this value silently replaces "
        "whatever .env.staging says — the DRF-1391 defect, restored. The host "
        "list belongs in the contour's own env file."
    )


def test_staging_anchor_does_not_declare_allowed_hosts():
    """Also guard the YAML anchor, which the per-service assert cannot see."""

    raw = _load(STAGING_COMPOSE)
    anchor = raw.get("x-staging-app-env") or {}

    assert GUARDED_KEY not in anchor, (
        "x-staging-app-env declares "
        f"{GUARDED_KEY}. The anchor is spliced into every staging app "
        "service's `environment:`, so this is the same override with one "
        "more level of indirection."
    )


def _staging_web_healthcheck_host() -> str:
    """The Host the `web` container sends to itself."""

    service = (_load(STAGING_COMPOSE).get("services") or {})["web"]
    test = service["healthcheck"]["test"]
    url = next(part for part in test if str(part).startswith("http"))
    return re.sub(r"^https?://([^:/]+).*$", r"\1", str(url))


def _deploy_probe_host() -> str:
    """The Host `deploy-dev.yml`'s readiness curl sends (no -H override)."""

    text = DEPLOY_DEV_WORKFLOW.read_text(encoding="utf-8")
    match = re.search(r"curl [^\n]*http://([^:/\s]+):\d+/readyz/", text)
    assert match, "deploy-dev.yml no longer has a recognisable /readyz/ probe"
    return match.group(1)


@pytest.mark.parametrize(
    "host_source",
    [_staging_web_healthcheck_host, _deploy_probe_host],
    ids=["container-healthcheck", "deploy-readiness-probe"],
)
def test_probe_hosts_are_documented_in_the_staging_template(host_source):
    """Tightening the list must not silently break a probe.

    Both probes address the app by a loopback name and send it verbatim
    as `Host`. If either name is missing from the documented pilot value,
    an operator following the template turns a healthy contour red — the
    exact accident this ticket was told not to cause.
    """

    host = host_source()
    template = ENV_STAGING_TEMPLATE.read_text(encoding="utf-8")

    documented = re.search(rf"^#\s*{GUARDED_KEY}=(.+)$", template, re.MULTILINE)
    assert documented, (
        f"{ENV_STAGING_TEMPLATE.name} no longer carries a commented-out "
        f"`{GUARDED_KEY}=...` pilot value to check probe hosts against."
    )
    names = [n.strip() for n in documented.group(1).split(",") if n.strip()]

    assert host in names, (
        f"`{host}` is the Host header {host_source.__name__} sends, but the "
        f"pilot value documented in {ENV_STAGING_TEMPLATE.name} is {names}. "
        "Django would answer 400 DisallowedHost and the probe would fail on a "
        "contour that is serving traffic fine."
    )
