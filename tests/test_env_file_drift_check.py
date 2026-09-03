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
    path = _write_env(tmp_path, "DJANGO_ALLOWED_HOSTS=api-dev.gobeauty.site\n")

    assert compute_env_file_drift([path], {"DJANGO_ALLOWED_HOSTS": "api-dev.gobeauty.site"}) == []


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

    assert compute_env_file_drift([path], {"CHROMA_HTTP_HOST": ""}) == []


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


def test_check_is_silent_when_no_env_file_exists(tmp_path, settings):
    """CI and a fresh clone have no `.env.staging` — and must stay quiet."""

    settings.BASE_DIR = tmp_path

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
    """`.env` is autoloaded with override=False — a shell win is not drift."""

    _write_env(tmp_path, "X=1\n", name=".env")
    monkeypatch.delenv(DRIFT_PATHS_ENV_VAR, raising=False)

    assert resolve_drift_paths(tmp_path) == []


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
    """`config/settings/local.py` sets `*` on purpose — do not nag developers."""

    settings.DEBUG = True
    settings.ALLOWED_HOSTS = ["*"]

    assert check_allowed_hosts_not_wildcard() == []


def test_explicit_host_list_is_not_reported(settings):
    settings.DEBUG = False
    settings.ALLOWED_HOSTS = ["api-dev.gobeauty.site", "localhost", "127.0.0.1"]

    assert check_allowed_hosts_not_wildcard() == []
