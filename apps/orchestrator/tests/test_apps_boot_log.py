"""Block A9 boot-log smoke (maintainability roadmap, 2026-06-01).

`OrchestratorConfig.ready()` MUST log a single INFO line
`orchestrator.ayla_ai_core.boot version=<X.Y.Z> source=<url>@<sha>`
when ayla-ai-core is installed AND the process is a long-running
production entry point, и MUST stay silent (no WARN/ERROR) when:

- the package is missing (non-AI contributors)
- the process is a short-lived management command (pytest / migrate /
  collectstatic / etc.) per ``_QUIET_MANAGEMENT_ARGS``
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, distribution

import pytest

from apps.orchestrator.apps import OrchestratorConfig


def _ayla_installed() -> bool:
    """Adversarial CR F1 — align with the production code path
    (`_log_ayla_ai_core_version` uses `distribution("ayla-ai-core")`)
    instead of `import_module("ayla_ai_core")`. Avoids divergence
    between dist-name and import-name."""
    try:
        distribution("ayla-ai-core")
    except PackageNotFoundError:
        return False
    return True


class TestAylaCoreBootLog:
    @pytest.mark.skipif(
        not _ayla_installed(),
        reason="ayla-ai-core not installed — boot log skips silently by design",
    )
    def test_boot_log_emits_version(self, caplog):
        # Adversarial CR L1 — derive expected version from installed
        # package metadata instead of hardcoding "0.8.1". Future bumps
        # would otherwise silently break this test without it being
        # listed in the bump checklist в pyproject.toml header.
        expected_version = distribution("ayla-ai-core").version

        caplog.clear()
        with caplog.at_level("INFO", logger="apps.orchestrator.apps"):
            OrchestratorConfig._log_ayla_ai_core_version()

        # Exactly one INFO line, with the expected prefix +
        # version=<live> + source containing the git URL + SHA.
        boot_records = [r for r in caplog.records if r.name == "apps.orchestrator.apps"]
        assert len(boot_records) == 1, f"expected 1 boot log line, got {len(boot_records)}"
        msg = boot_records[0].getMessage()
        assert "orchestrator.ayla_ai_core.boot" in msg
        assert f"version={expected_version}" in msg
        # PEP 610 direct_url metadata is present (git install) so the
        # source field should NOT be the pypi fallback.
        assert "source=<pypi-or-unknown>" not in msg
        assert "source=" in msg

    def test_boot_log_silent_when_package_absent(self, caplog, monkeypatch):
        """Force `distribution()` to raise `PackageNotFoundError` и
        confirm `_log_ayla_ai_core_version()` swallows it without
        logging. Adversarial CR M1 — `monkeypatch` auto-restores at
        teardown, no manual capture needed."""
        from importlib import metadata

        def _raise(*args, **kwargs):
            raise metadata.PackageNotFoundError("ayla-ai-core")

        monkeypatch.setattr(metadata, "distribution", _raise)

        caplog.clear()
        with caplog.at_level("INFO", logger="apps.orchestrator.apps"):
            OrchestratorConfig._log_ayla_ai_core_version()

        boot_records = [r for r in caplog.records if r.name == "apps.orchestrator.apps"]
        assert boot_records == [], (
            f"Expected silent skip when package absent, but got "
            f"{[r.getMessage() for r in boot_records]}"
        )


class TestBootLogGate:
    """Adversarial CR M2 — `_should_emit_boot_log()` filters out
    short-lived management commands так production-grep stream stays
    clean."""

    def test_emit_suppressed_when_pytest_in_argv(self, monkeypatch):
        from apps.orchestrator.apps import _should_emit_boot_log

        monkeypatch.setattr("sys.argv", ["pytest", "tests/"])
        assert _should_emit_boot_log() is False

    def test_emit_suppressed_for_migrate(self, monkeypatch):
        from apps.orchestrator.apps import _should_emit_boot_log

        monkeypatch.setattr("sys.argv", ["manage.py", "migrate"])
        assert _should_emit_boot_log() is False

    def test_emit_suppressed_for_collectstatic(self, monkeypatch):
        from apps.orchestrator.apps import _should_emit_boot_log

        monkeypatch.setattr("sys.argv", ["manage.py", "collectstatic", "--noinput"])
        assert _should_emit_boot_log() is False

    def test_emit_enabled_for_runserver(self, monkeypatch):
        from apps.orchestrator.apps import _should_emit_boot_log

        monkeypatch.setattr("sys.argv", ["manage.py", "runserver"])
        monkeypatch.delenv("AYLA_BOOT_LOG_SUPPRESS", raising=False)
        assert _should_emit_boot_log() is True

    def test_emit_enabled_for_gunicorn(self, monkeypatch):
        from apps.orchestrator.apps import _should_emit_boot_log

        # gunicorn launches with its own argv[0]; the gate's «unrecognised
        # entry point defaults к emit» branch covers this so we don't
        # accidentally silence production launches.
        monkeypatch.setattr("sys.argv", ["gunicorn", "config.wsgi"])
        monkeypatch.delenv("AYLA_BOOT_LOG_SUPPRESS", raising=False)
        assert _should_emit_boot_log() is True

    def test_env_var_override_suppresses(self, monkeypatch):
        from apps.orchestrator.apps import _should_emit_boot_log

        monkeypatch.setattr("sys.argv", ["manage.py", "runserver"])
        monkeypatch.setenv("AYLA_BOOT_LOG_SUPPRESS", "1")
        assert _should_emit_boot_log() is False
