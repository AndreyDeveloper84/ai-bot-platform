"""The drift reporter itself (DRF-1391, second half of the ticket).

The removal of one compose override closes one hole. This is the part
that survives it: three variables drifted silently between `.env.staging`
and the running process, so a fourth will, and nothing in the stack would
say so.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from apps.observability.checks import (
    ENV_DRIFT_CHECK_ID,
    WILDCARD_HOSTS_CHECK_ID,
    check_allowed_hosts_not_wildcard,
    check_env_file_drift,
)
from config.env_file_drift import (
    DRIFT_PATHS_ENV_VAR,
    compute_env_file_drift,
    resolve_drift_paths,
)


def _write_env(tmp_path: Path, body: str, name: str = ".env.staging") -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


def test_agreement_reports_nothing(tmp_path):
    """Silence must mean "compared and agreed", not "compared nothing".

    The armed case runs first over the same file and the same key, so a
    detector that had quietly stopped looking fails here by name instead
    of passing the quiet assertion below.
    """

    path = _write_env(tmp_path, "DJANGO_ALLOWED_HOSTS=api-dev.gobeauty.site\n")

    findings = compute_env_file_drift([path], {"DJANGO_ALLOWED_HOSTS": "*"})
    assert [f.key for f in findings] == ["DJANGO_ALLOWED_HOSTS"]

    findings = compute_env_file_drift([path], {"DJANGO_ALLOWED_HOSTS": "api-dev.gobeauty.site"})
    assert findings == []


def test_the_pilot_case_is_reported(tmp_path):
    """`.env.staging` said the domain; the process had `*`."""

    path = _write_env(tmp_path, "DJANGO_ALLOWED_HOSTS=api-dev.gobeauty.site\n")

    findings = compute_env_file_drift([path], {"DJANGO_ALLOWED_HOSTS": "*"})

    assert [(f.key, f.kind) for f in findings] == [("DJANGO_ALLOWED_HOSTS", "differs")]


def test_all_three_drifted_pilot_variables_are_reported(tmp_path):
    """The measured 2026-08-25 state, reproduced key for key."""

    path = _write_env(
        tmp_path,
        "DJANGO_ALLOWED_HOSTS=api-dev.gobeauty.site\n"
        "ORCHESTRATOR_SHADOW_ENABLED=true\n"
        "CHROMA_HTTP_HOST=disabled\n"
        "SITE_DOMAIN=https://api-dev.gobeauty.site\n",
    )

    findings = compute_env_file_drift(
        [path],
        {
            "DJANGO_ALLOWED_HOSTS": "*",
            "ORCHESTRATOR_SHADOW_ENABLED": "false",
            "CHROMA_HTTP_HOST": "chromadb",
            # SITE_DOMAIN was the one variable that passed through intact.
            "SITE_DOMAIN": "https://api-dev.gobeauty.site",
        },
    )

    assert sorted(f.key for f in findings) == [
        "CHROMA_HTTP_HOST",
        "DJANGO_ALLOWED_HOSTS",
        "ORCHESTRATOR_SHADOW_ENABLED",
    ]


def test_declared_blank_overridden_to_a_value_is_drift(tmp_path):
    """`CHROMA_HTTP_HOST=` means off; compose set it to `chromadb` anyway."""

    path = _write_env(tmp_path, "CHROMA_HTTP_HOST=\n")

    findings = compute_env_file_drift([path], {"CHROMA_HTTP_HOST": "chromadb"})

    assert [f.key for f in findings] == ["CHROMA_HTTP_HOST"]


def test_declared_blank_and_unset_is_not_drift(tmp_path):
    """A template's blank placeholder means "unset" — not a disagreement."""

    path = _write_env(tmp_path, "CHROMA_HTTP_HOST=\nOPTIONAL_THING=\n")

    findings = compute_env_file_drift([path], {"CHROMA_HTTP_HOST": "chromadb"})
    assert [f.key for f in findings] == ["CHROMA_HTTP_HOST"]

    findings = compute_env_file_drift([path], {"CHROMA_HTTP_HOST": ""})
    assert findings == []


def test_declared_but_absent_from_the_process_is_reported(tmp_path):
    path = _write_env(tmp_path, "MAX_BOT_TENANT_SLUG=penza\n")

    findings = compute_env_file_drift([path], {})

    assert [(f.key, f.kind) for f in findings] == [("MAX_BOT_TENANT_SLUG", "missing")]


def test_no_value_is_ever_exposed(tmp_path):
    """A drift report goes to deploy logs — it must not carry the secret."""

    secret = "s3cr3t-signing-key-value"  # pragma: allowlist secret
    path = _write_env(tmp_path, f"DJANGO_SECRET_KEY={secret}\n")

    findings = compute_env_file_drift([path], {"DJANGO_SECRET_KEY": "something-else"})

    assert len(findings) == 1
    rendered = repr(findings[0]) + findings[0].describe()
    assert secret not in rendered
    assert "something-else" not in rendered


def test_check_message_names_keys_but_not_values(tmp_path, settings, monkeypatch):
    secret = "another-signing-key"  # pragma: allowlist secret
    _write_env(tmp_path, f"DJANGO_SECRET_KEY={secret}\n")
    settings.BASE_DIR = tmp_path
    monkeypatch.setenv("DJANGO_SECRET_KEY", "live-value-that-differs")

    warnings = check_env_file_drift()

    assert len(warnings) == 1
    assert warnings[0].id == ENV_DRIFT_CHECK_ID
    assert "DJANGO_SECRET_KEY" in warnings[0].msg
    assert secret not in warnings[0].msg
    assert "live-value-that-differs" not in warnings[0].msg


def test_check_is_silent_when_no_env_file_exists(tmp_path, settings, monkeypatch):
    """CI and a fresh clone have no `.env.staging` — and must stay quiet.

    Proven by removal: the same BASE_DIR reports a drift while the file is
    there, so the silence afterwards is attributable to the file's absence
    and not to a check that never ran.
    """

    settings.BASE_DIR = tmp_path
    monkeypatch.setenv("SOME_PILOT_VAR", "process-value")
    path = _write_env(tmp_path, "SOME_PILOT_VAR=declared-value\n")

    assert len(check_env_file_drift()) == 1

    path.unlink()

    assert check_env_file_drift() == []


def test_drift_paths_are_configurable_and_disablable(tmp_path, monkeypatch):
    other = _write_env(tmp_path, "A=1\n", name=".env.custom")
    _write_env(tmp_path, "B=2\n")

    monkeypatch.setenv(DRIFT_PATHS_ENV_VAR, ".env.custom")
    assert resolve_drift_paths(tmp_path) == [other]

    monkeypatch.setenv(DRIFT_PATHS_ENV_VAR, "")
    assert resolve_drift_paths(tmp_path) == []

    monkeypatch.delenv(DRIFT_PATHS_ENV_VAR)
    assert [p.name for p in resolve_drift_paths(tmp_path)] == [".env.staging"]


def test_dotenv_file_is_not_compared_by_default(tmp_path, monkeypatch):
    """`.env` is autoloaded with override=False — a shell win is not drift.

    Both files sit in the same directory, so the resolver is demonstrably
    looking there: it returns `.env.staging` and leaves `.env` out. An
    assertion that it merely returns nothing would also pass against a
    resolver that had stopped working.
    """

    monkeypatch.delenv(DRIFT_PATHS_ENV_VAR, raising=False)
    _write_env(tmp_path, "X=1\n", name=".env")
    _write_env(tmp_path, "Y=2\n")

    names = [p.name for p in resolve_drift_paths(tmp_path)]

    assert names == [".env.staging"]


# --------------------------------------------------------------------------
# The wildcard guard: drift reporting alone cannot catch a `*` that an env
# file and the process agree on.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("hosts", [["*"], ["api-dev.gobeauty.site", "*"]])
def test_wildcard_allowed_hosts_is_reported_outside_debug(settings, hosts):
    settings.DEBUG = False
    settings.ALLOWED_HOSTS = hosts

    warnings = check_allowed_hosts_not_wildcard()

    assert [w.id for w in warnings] == [WILDCARD_HOSTS_CHECK_ID]
    # The hint must name every probe host, or acting on it breaks a deploy.
    assert "localhost" in warnings[0].hint
    assert "127.0.0.1" in warnings[0].hint


def test_wildcard_is_fine_in_debug(settings):
    """`config/settings/local.py` sets `*` on purpose — do not nag developers.

    DEBUG is the only thing that changes between the two halves, so the
    silence is attributable to it rather than to a check that never fires.
    """

    settings.ALLOWED_HOSTS = ["*"]

    settings.DEBUG = False
    assert len(check_allowed_hosts_not_wildcard()) == 1

    settings.DEBUG = True
    assert check_allowed_hosts_not_wildcard() == []


def test_explicit_host_list_is_not_reported(settings):
    """Same DEBUG, same check — only the host list differs."""

    settings.DEBUG = False

    settings.ALLOWED_HOSTS = ["*"]
    assert len(check_allowed_hosts_not_wildcard()) == 1

    settings.ALLOWED_HOSTS = ["api-dev.gobeauty.site", "localhost", "127.0.0.1"]
    assert check_allowed_hosts_not_wildcard() == []
