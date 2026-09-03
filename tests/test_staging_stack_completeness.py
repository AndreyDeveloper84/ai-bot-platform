"""Every Django service in the base stack must reach staging settings (DRF-1447).

### The failure

`docker-compose.yml` defines three Django services — `web`, `worker`,
`shadow-worker`. `docker-compose.staging.yml` described two of them.
Compose still STARTED the third: `-f a.yml -f b.yml` merges the two
service maps, so a service present only in the base file is part of the
project either way. It simply started on the base file's dev profile.

Measured on the pilot 2026-09-03 with
`docker exec ayla-bot-staging-<svc>-1 printenv`:

    web / worker / celery-worker / celery-beat   config.settings.staging, DEBUG=False
    shadow-worker                                config.settings.local,   DEBUG=True

`DEBUG=True` makes Django render the technical 500 page on any unhandled
exception — every local variable of every frame, which on this service
means bot tokens, MAX init-data payloads and client phone numbers — and
makes `django.db.connection.queries` retain every statement, unbounded,
in a process that stays up for weeks. `shadow-worker` had been in that
state since the staging stack was created.

### Why this file exists next to `test_compose_env_chain.py`

That file (DRF-1391) guards a KEY: no compose layer may declare
`DJANGO_ALLOWED_HOSTS`. Asked about a service that is missing from a
compose file it calls `pytest.skip` — correctly, for its own question.
But "skip" and "clean" print the same green, and the missing service was
the whole defect here. This file guards the SET: which services exist,
and whether each one actually arrives configured.

### Process, not file

Two of the tests below shell out to `docker compose config` — the same
resolver that will build the real stack — on a throwaway copy of the two
compose files plus a synthetic `.env.staging`. Re-implementing compose's
merge in Python would be the mistake this ticket is made of: we would be
asserting against our belief about the merge rather than against the
merge. `config` is client-side and needs no daemon.

The last test is the mutation, kept in the suite rather than performed
once by hand: it drops `shadow-worker` from the staging copy and requires
the check above to fail on it. Without that, "all services configured" is
equally true of a check that quietly stopped looking.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml  # a hard dependency — `apps/replay/fixtures/loader.py` imports it plainly

REPO_ROOT = Path(__file__).resolve().parent.parent
BASE_COMPOSE = REPO_ROOT / "docker-compose.yml"
STAGING_COMPOSE = REPO_ROOT / "docker-compose.staging.yml"

#: Written into the synthetic `.env.staging`. A service that reports it
#: received `env_file:`; a service that does not, did not — a stronger
#: statement than "its settings module looks right", because the settings
#: module could have arrived from either layer.
ENV_FILE_MARKER = "DRF1447_ENV_STAGING_MARKER"

#: Services in the base file that run Django. Recorded rather than only
#: derived, so that adding a fourth one fails HERE — at a line that names
#: the staging file — instead of silently widening a derived set.
EXPECTED_BASE_DJANGO_SERVICES = {"web", "worker", "shadow-worker"}

#: Everything the staging stack must configure: the base file's Django
#: services plus the two the staging file introduces on its own.
EXPECTED_STAGING_DJANGO_SERVICES = EXPECTED_BASE_DJANGO_SERVICES | {
    "celery-worker",
    "celery-beat",
}

STAGING_SETTINGS_MODULE = "config.settings.staging"


class _TagTolerantLoader(yaml.SafeLoader):
    """SafeLoader that tolerates compose's `!override` / `!reset` tags.

    `docker-compose.staging.yml` uses `ports: !override` (Compose v2.24+).
    SafeLoader rejects unknown tags outright, so without this the file
    cannot be parsed at all — and a guard that cannot read its subject is
    a guard that cannot fail.

    Deliberately duplicated from `tests/test_compose_env_chain.py`
    (DRF-1391) rather than imported: the two land as separate PRs, and a
    cross-import would break whichever merges second.
    """


def _ignore_unknown_tag(loader: yaml.SafeLoader, tag_suffix: str, node: yaml.Node) -> object:
    """Return an unknown-tagged node's payload, dropping only the tag."""

    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node, deep=True)
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node, deep=True)
    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    # PyYAML defines no fourth node type; if one ever appears, say so
    # rather than returning a plausible-looking None.
    raise TypeError(f"unexpected YAML node type {type(node).__name__} for tag !{tag_suffix}")


_TagTolerantLoader.add_multi_constructor("!", _ignore_unknown_tag)


def _load(path: Path) -> dict:
    return yaml.load(path.read_text(encoding="utf-8"), Loader=_TagTolerantLoader) or {}


def _environment(service: dict) -> dict[str, str | None]:
    """A service's `environment:`, normalised across compose's two forms."""

    env = service.get("environment") or {}
    if isinstance(env, dict):
        return {str(k): (None if v is None else str(v)) for k, v in env.items()}
    out: dict[str, str | None] = {}
    for item in env:
        key, sep, value = str(item).partition("=")
        out[key] = value if sep else None
    return out


def _django_services(compose: dict) -> set[str]:
    """Services that run Django, identified by their settings module.

    Derived from the file rather than only hard-coded, so a renamed or
    newly added app service is discovered instead of being left out of
    the comparison without a word.
    """

    services = compose.get("services") or {}
    return {
        name
        for name, service in services.items()
        if isinstance(service, dict) and "DJANGO_SETTINGS_MODULE" in _environment(service)
    }


def _env_file_names(service: dict) -> list[str]:
    """The `env_file:` entries of a service, across compose's three forms."""

    entries = service.get("env_file") or []
    if isinstance(entries, str):
        entries = [entries]
    return [str(e.get("path", e) if isinstance(e, dict) else e) for e in entries]


# ---------------------------------------------------------------------------
# File level. These cannot skip: they are what still guards the stack on a
# machine with no docker CLI.
# ---------------------------------------------------------------------------


def test_the_base_file_still_declares_the_services_this_guard_assumes():
    """Arming check: a new Django service in the base file fails here first.

    Everything below compares the base file's app services against the
    staging file. If the discovery above silently returned an empty or
    shrunken set — a renamed key, a restructured file — those comparisons
    would pass vacuously. This pins the set instead.
    """

    found = _django_services(_load(BASE_COMPOSE))

    assert found == EXPECTED_BASE_DJANGO_SERVICES, (
        f"{BASE_COMPOSE.name} now has Django services {sorted(found)}, not "
        f"{sorted(EXPECTED_BASE_DJANGO_SERVICES)}. If you added one, give it a "
        f"stanza in {STAGING_COMPOSE.name} too (environment + env_file, like "
        "`worker`) and record it here. A base-file service with no staging "
        "stanza is still started by the staging stack — on dev settings, with "
        "DEBUG=True."
    )


@pytest.mark.parametrize("service_name", sorted(EXPECTED_BASE_DJANGO_SERVICES))
def test_every_base_django_service_is_described_in_the_staging_file(service_name: str):
    """The positive guard. No `skip` branch: absence IS the defect.

    `x-staging-app-env` is a YAML anchor, not a default — it applies where
    it is written. A service the staging file never mentions receives
    neither it nor `.env.staging`.
    """

    services = _load(STAGING_COMPOSE).get("services") or {}
    service = services.get(service_name)

    assert service is not None, (
        f"{STAGING_COMPOSE.name} does not describe `{service_name}`, which "
        f"{BASE_COMPOSE.name} defines. Compose merges the two service maps, so "
        "the staging stack starts it anyway — on the base file's "
        "`config.settings.local` / `DJANGO_DEBUG=True`, and with no "
        "`.env.staging`. That is DRF-1447: on the pilot `shadow-worker` ran "
        "for weeks in DEBUG, one unhandled exception away from rendering "
        "tokens and client phone numbers into a traceback."
    )

    env = _environment(service)
    assert env.get("DJANGO_SETTINGS_MODULE") == STAGING_SETTINGS_MODULE, (
        f"{STAGING_COMPOSE.name}: `{service_name}` declares "
        f"DJANGO_SETTINGS_MODULE={env.get('DJANGO_SETTINGS_MODULE')!r}. "
        "Splice the `*staging_app_env` anchor into its `environment:`."
    )
    assert env.get("DJANGO_DEBUG") == "False", (
        f"{STAGING_COMPOSE.name}: `{service_name}` declares "
        f"DJANGO_DEBUG={env.get('DJANGO_DEBUG')!r}, not 'False'."
    )

    names = _env_file_names(service)
    assert ".env.staging" in names, (
        f"{STAGING_COMPOSE.name}: `{service_name}` has no `.env.staging` in its "
        f"`env_file:` (found {names}). `env_file:` is per service — a service "
        "without its own entry gets none of the contour's values and reaches "
        "the pilot missing MAX_BOT_TOKEN, AYLA_INTERNAL_API_TOKEN and the rest."
    )


# ---------------------------------------------------------------------------
# Process level: ask the resolver, not the YAML.
# ---------------------------------------------------------------------------


def _compose_config(project_dir: Path) -> dict:
    """Resolve the two-file stack the way the deploy command does.

    `docker compose config` folds `env_file:` into each service's
    `environment:` — precisely the step a hand-rolled merge would have to
    guess at, and the step that was guessed wrong for three days.
    """

    if shutil.which("docker") is None:  # pragma: no cover - depends on the host
        pytest.skip("docker CLI is unavailable; the file-level guards above still run")

    result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            "docker-compose.yml",
            "-f",
            "docker-compose.staging.yml",
            "config",
            "--format",
            "json",
        ],
        cwd=project_dir,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if result.returncode != 0:  # pragma: no cover - depends on the host
        pytest.skip(f"`docker compose config` did not run here: {result.stderr.strip()[:300]}")
    return json.loads(result.stdout)


def _staging_sandbox(tmp_path: Path) -> Path:
    """A throwaway copy of the stack, with a synthetic `.env.staging`.

    Copied rather than resolved in place because the real `.env.staging`
    is out of git: it is absent in CI and on a fresh clone, and where it
    does exist it holds live pilot secrets that must not be read into a
    test process. The synthetic file carries one marker and nothing else.
    """

    for path in (BASE_COMPOSE, STAGING_COMPOSE):
        shutil.copy(path, tmp_path / path.name)
    (tmp_path / ".env.staging").write_text(f"{ENV_FILE_MARKER}=reached\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def resolved_stack(tmp_path: Path) -> dict:
    return _compose_config(_staging_sandbox(tmp_path))


def _assert_service_is_staged(resolved: dict, service_name: str) -> None:
    """The whole contract, in one callable — so the mutation can re-run it."""

    service = (resolved.get("services") or {}).get(service_name)
    assert service is not None, f"`{service_name}` is not in the resolved stack at all."

    env = service.get("environment") or {}
    assert env.get("DJANGO_SETTINGS_MODULE") == STAGING_SETTINGS_MODULE, (
        f"`{service_name}` resolves to DJANGO_SETTINGS_MODULE="
        f"{env.get('DJANGO_SETTINGS_MODULE')!r}. This is the process the pilot "
        "would start, not a file we hope is read."
    )
    assert env.get("DJANGO_DEBUG") == "False", (
        f"`{service_name}` resolves to DJANGO_DEBUG={env.get('DJANGO_DEBUG')!r}. "
        "In DEBUG Django renders unhandled exceptions with every frame's local "
        "variables and retains every SQL statement it ever ran."
    )
    assert env.get(ENV_FILE_MARKER) == "reached", (
        f"`{service_name}` did not receive `.env.staging`. `env_file:` binds to "
        "the service that declares it; a service missing from "
        f"{STAGING_COMPOSE.name} declares nothing."
    )


@pytest.mark.parametrize("service_name", sorted(EXPECTED_STAGING_DJANGO_SERVICES))
def test_resolved_stack_stages_every_django_service(resolved_stack: dict, service_name: str):
    """What `docker compose` will actually hand each container."""

    _assert_service_is_staged(resolved_stack, service_name)


def test_the_resolver_guard_fails_when_a_service_loses_its_staging_stanza(tmp_path: Path):
    """The mutation, run every time instead of once by hand.

    Drops `shadow-worker` from the staging copy — the exact state the
    pilot was in — and requires the check above to fail on it while the
    services around it still pass. A guard that survives its own subject
    being deleted is measuring nothing, and this repo has already paid
    three days for the difference between reading a file and observing a
    process.
    """

    sandbox = _staging_sandbox(tmp_path)
    staging = sandbox / STAGING_COMPOSE.name

    document = _load(staging)
    removed = (document.get("services") or {}).pop("shadow-worker", None)
    assert removed is not None, (
        f"{STAGING_COMPOSE.name} has no `shadow-worker` stanza to remove — the "
        "mutation cannot be applied, so this test proves nothing. That absence "
        "is the DRF-1447 state itself; fix the compose file."
    )
    staging.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")

    mutated = _compose_config(sandbox)

    # It is still STARTED — that is the trap. Compose merges the service
    # maps, so removing the stanza does not remove the container; it only
    # removes its configuration. This is why "the stack is up" never
    # contradicted "the service is misconfigured".
    assert "shadow-worker" in (mutated.get("services") or {}), (
        "Dropping the staging stanza should leave the service running on the "
        "base file's settings — the property that made this defect invisible."
    )

    with pytest.raises(AssertionError, match="DJANGO_SETTINGS_MODULE"):
        _assert_service_is_staged(mutated, "shadow-worker")

    # The neighbours must still pass, or the mutation proves only that the
    # whole check fell over.
    for neighbour in sorted(EXPECTED_STAGING_DJANGO_SERVICES - {"shadow-worker"}):
        _assert_service_is_staged(mutated, neighbour)
