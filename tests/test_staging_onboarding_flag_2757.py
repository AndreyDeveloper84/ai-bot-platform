"""The pilot's onboarding flag is configuration, not a line in an untracked file (DRF-2757).

### What was wrong

``GLOBAL_BOT_ONBOARDING`` gates the welcome + 152-ФЗ consent capture on the
global (client) bot. The code default is OFF. On the pilot it was switched ON
on 20.08 by editing ``.env.staging`` on the host — a file that is not in git.
So the repository said nothing about the pilot's value: the code said OFF,
the documents said «ON since 20.08», and the only way to tell which was true
was to read the stand.

### What this file holds

Owner decision 02.10.2026: the value for the stand lives in
``docker-compose.staging.yml`` — visible, reviewed, in the diff.

* file level — the staging overlay declares it for every Django service, with
  the exact string the setting parses; the base file (local development) does
  not declare it; the code default stays OFF;
* process level — ``docker compose config``, the resolver the deploy uses:
  each service really receives ``true``, **even when ``.env.staging`` says
  ``false``**. That is the point of moving it: a line in the host file can no
  longer flip the contour either way.

### What it does not hold

* That the stand is running this configuration: merging is not deploying. The
  value arrives with the next deploy of the stand.
* ``docker-compose.staging.local.yml`` — the third file the deploy passes. It
  exists only on the host; if it declared this variable it would win, and
  nothing here can see it.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from tests.test_staging_stack_completeness import (
    BASE_COMPOSE,
    EXPECTED_STAGING_DJANGO_SERVICES,
    STAGING_COMPOSE,
    _compose_config,
    _environment,
    _load,
)

FLAG = "GLOBAL_BOT_ONBOARDING"
BASE_SETTINGS = Path(__file__).resolve().parents[1] / "config" / "settings" / "base.py"


def _reads_as_on(value: str | None) -> bool:
    """The setting's own rule, restated: ``os.environ.get(FLAG, "false").lower() == "true"``."""

    return (value if value is not None else "false").lower() == "true"


def test_the_setting_still_parses_the_way_this_file_assumes() -> None:
    """If the parser in ``base.py`` changes, the string in compose must be re-read."""

    source = BASE_SETTINGS.read_text(encoding="utf-8")
    assert (
        'GLOBAL_BOT_ONBOARDING = os.environ.get("GLOBAL_BOT_ONBOARDING", "false").lower() == "true"'
        in source
    )
    # …and that rule is narrow: the usual «truthy» spellings are OFF.
    assert _reads_as_on("true") and _reads_as_on("True")
    assert [_reads_as_on(v) for v in ("1", "yes", "on", "", None)] == [False] * 5


@pytest.mark.parametrize("service_name", sorted(EXPECTED_STAGING_DJANGO_SERVICES))
def test_the_staging_overlay_declares_the_flag_for_every_django_service(
    service_name: str,
) -> None:
    service = (_load(STAGING_COMPOSE).get("services") or {})[service_name]
    value = _environment(service).get(FLAG)
    assert value == "true", (
        f"{STAGING_COMPOSE.name}: `{service_name}` carries {FLAG}={value!r}. The pilot's "
        "onboarding is switched on by the `x-staging-app-env` anchor; a service that "
        "does not splice it runs the global bot without consent capture."
    )
    assert _reads_as_on(value)


def test_local_development_is_left_on_the_code_default() -> None:
    """Scope: the stand only. The base file must not switch onboarding on for everyone."""

    services = _load(BASE_COMPOSE).get("services") or {}
    assert services, "the base file has services — otherwise «none declares it» is empty"
    declaring = sorted(
        name
        for name, service in services.items()
        if isinstance(service, dict) and FLAG in _environment(service)
    )
    assert declaring == []


@pytest.fixture
def resolved_with_the_host_file_saying_off(tmp_path: Path) -> dict:
    """The stack as the deploy resolves it, with ``.env.staging`` saying the opposite."""

    for path in (BASE_COMPOSE, STAGING_COMPOSE):
        shutil.copy(path, tmp_path / path.name)
    (tmp_path / ".env.staging").write_text(
        f"{FLAG}=false\nENV_FILE_REACHED_2757=yes\n", encoding="utf-8"
    )
    return _compose_config(tmp_path)


@pytest.mark.parametrize("service_name", sorted(EXPECTED_STAGING_DJANGO_SERVICES))
def test_every_service_receives_the_flag_whatever_the_host_file_says(
    resolved_with_the_host_file_saying_off: dict, service_name: str
) -> None:
    env = resolved_with_the_host_file_saying_off["services"][service_name]["environment"]
    # Positive pair: the host file WAS read — otherwise «compose wins» proves nothing.
    assert env.get("ENV_FILE_REACHED_2757") == "yes"
    assert env.get(FLAG) == "true"
    assert _reads_as_on(env.get(FLAG))
