"""Report where an env file's declared value and the live process disagree.

### The failure this exists for (DRF-1391)

`.env.staging` on the pilot said

    DJANGO_ALLOWED_HOSTS=api-dev.gobeauty.site

and the running container had `ALLOWED_HOSTS == ['*']`. Host checking was
off on a contour with a public entrance, and the operator had written the
correct value nine weeks earlier.

The mechanism is ordinary compose semantics: for a service present in
both `-f` files, the `environment:` mappings are merged key by key, and
`environment:` wins over `env_file:`. `docker-compose.yml` declared
`DJANGO_ALLOWED_HOSTS: "*"` for `web` / `worker` / `shadow-worker`;
`docker-compose.staging.yml` never mentions the key, so the base value
survived the merge and beat `.env.staging`. Two more variables drifted
the same way (`CHROMA_HTTP_HOST`, `ORCHESTRATOR_SHADOW_ENABLED`), and the
two services with no base counterpart (`celery-worker`, `celery-beat`)
got the env file's values — one stack, one env file, two answers.

Nothing in that chain is a bug. Every layer did what it documents. The
defect is that **the disagreement is silent**: the only way to see it was
to guess that something was wrong and read `settings.ALLOWED_HOSTS` from
inside a running container.

DRF-1391 removed the specific override. This module is the part that
outlives it: three variables drifted, so a fourth will, and it will drift
just as quietly.

### What it compares, and what it deliberately does not

Declared side: the `KEY=value` lines of the env file(s) named by
`DJANGO_ENV_FILE_DRIFT_PATHS` (CSV; relative names resolve against
`BASE_DIR`), defaulting to `.env.staging`.

Actual side: `os.environ` in this process.

`.env` is NOT compared by default, and the omission is the point.
`manage.py` / `config.wsgi` / `config.asgi` autoload `.env` through
python-dotenv with `override=False`, so a shell export that beats a `.env`
line is documented, intentional behaviour (see `.env.example`). Flagging
it would make the report noise, and a noisy report is the thing that got
ignored in the first place. `.env.staging` has no such loader: it reaches
a process through compose `env_file:` and nothing else, so any
disagreement there is genuinely somebody's value being discarded.

### Values are never reported

Every finding names the KEY and the file. Not the declared value, not the
live one. These files hold `DJANGO_SECRET_KEY`, `MAX_BOT_TOKEN`,
`OPENAI_API_KEY` and the eventbus HMAC secret; a drift report is written
to deploy logs and Sentry breadcrumbs, and a check that leaks a secret to
make a config point is a worse defect than the one it reports. The key
name is the whole answer to "where did it diverge" anyway.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

__all__ = ["EnvDrift", "compute_env_file_drift", "resolve_drift_paths"]

#: Compared unless ``DJANGO_ENV_FILE_DRIFT_PATHS`` names something else.
DEFAULT_ENV_FILE_NAMES = (".env.staging",)

#: Env var holding a CSV of env files to compare. Relative entries are
#: resolved against ``BASE_DIR``. Set it to an empty string to disable.
DRIFT_PATHS_ENV_VAR = "DJANGO_ENV_FILE_DRIFT_PATHS"


@dataclass(frozen=True)
class EnvDrift:
    """One key whose env-file declaration did not survive into the process.

    ``kind`` is ``"differs"`` (process has some other value) or
    ``"missing"`` (process has no such variable at all). No value is
    carried — see the module docstring.
    """

    key: str
    path: Path
    kind: str

    def describe(self) -> str:
        if self.kind == "missing":
            return f"{self.key} (declared in {self.path.name}, absent from the process)"
        return f"{self.key} (declared in {self.path.name}, process has a different value)"


def resolve_drift_paths(base_dir: Path, environ: Mapping[str, str] | None = None) -> list[Path]:
    """Return the existing env files to compare, honouring the override var.

    An unset override means "the default set". An override set to the
    empty string means "compare nothing" — the off switch, distinct from
    unset on purpose so a contour can silence this without editing code.
    """

    environ = os.environ if environ is None else environ
    raw = environ.get(DRIFT_PATHS_ENV_VAR)
    if raw is None:
        names: tuple[str, ...] | list[str] = DEFAULT_ENV_FILE_NAMES
    else:
        names = [part.strip() for part in raw.split(",") if part.strip()]

    paths: list[Path] = []
    for name in names:
        candidate = Path(name)
        if not candidate.is_absolute():
            candidate = base_dir / candidate
        if candidate.is_file():
            paths.append(candidate)
    return paths


def _parse_env_file(path: Path) -> dict[str, str | None]:
    """Parse ``path`` with python-dotenv, which is already a hard dependency.

    Hand-rolling this would have to re-implement quoting, ``export``
    prefixes and inline comments — and would get it subtly differently
    from the parser compose and the autoloader actually use, which is how
    a drift *reporter* would come to report drift that isn't there.
    """

    from dotenv import dotenv_values

    return dict(dotenv_values(path, encoding="utf-8"))


def compute_env_file_drift(
    paths: list[Path],
    environ: Mapping[str, str] | None = None,
) -> list[EnvDrift]:
    """Compare each declared key against the live environment.

    Rules, and the reasoning for the two that are not obvious:

    * A key declared with no ``=`` at all carries no claim about a value
      (python-dotenv yields ``None``) — skipped.
    * A key declared empty (``KEY=``) and absent or empty in the process
      is NOT drift: compose's ``env_file`` turns ``KEY=`` into an empty
      string, and a template's blank placeholder means "unset". But a key
      declared empty that the process reports as *non-empty* IS drift —
      that is precisely the shape ``CHROMA_HTTP_HOST`` had on the pilot
      (declared off, overridden to ``chromadb`` by the base compose file).
    """

    environ = os.environ if environ is None else environ
    findings: list[EnvDrift] = []

    for path in paths:
        for key, declared in _parse_env_file(path).items():
            if declared is None:
                continue
            actual = environ.get(key)
            if actual is None:
                if declared == "":
                    continue
                findings.append(EnvDrift(key=key, path=path, kind="missing"))
                continue
            if actual != declared:
                findings.append(EnvDrift(key=key, path=path, kind="differs"))

    return findings
