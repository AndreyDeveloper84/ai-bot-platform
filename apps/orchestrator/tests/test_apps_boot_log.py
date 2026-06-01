"""Block A9 boot-log smoke (maintainability roadmap, 2026-06-01).

`OrchestratorConfig.ready()` MUST log a single INFO line
`orchestrator.ayla_ai_core.boot version=<X.Y.Z> source=<url>@<sha>`
when ayla-ai-core is installed, и MUST stay silent (no WARN/ERROR)
when the package is missing — non-AI contributors syncing without
`--extra ai-core` should see a clean boot.
"""

from __future__ import annotations

import importlib

import pytest

from apps.orchestrator.apps import OrchestratorConfig


def _ayla_installed() -> bool:
    try:
        importlib.import_module("ayla_ai_core")
    except ImportError:
        return False
    return True


class TestAylaCoreBootLog:
    @pytest.mark.skipif(
        not _ayla_installed(),
        reason="ayla-ai-core not installed — boot log skips silently by design",
    )
    def test_boot_log_emits_version(self, caplog):
        caplog.clear()
        with caplog.at_level("INFO", logger="apps.orchestrator.apps"):
            OrchestratorConfig._log_ayla_ai_core_version()

        # Exactly one INFO line, with the expected prefix + version=0.8.1
        # (per pyproject.toml pin) + source containing the git URL + SHA.
        boot_records = [r for r in caplog.records if r.name == "apps.orchestrator.apps"]
        assert len(boot_records) == 1, f"expected 1 boot log line, got {len(boot_records)}"
        msg = boot_records[0].getMessage()
        assert "orchestrator.ayla_ai_core.boot" in msg
        assert "version=0.8.1" in msg
        # Direct-URL PEP 610 metadata is present (git install) so the
        # source field should NOT be the pypi fallback.
        assert "source=<pypi-or-unknown>" not in msg
        assert "source=" in msg

    def test_boot_log_silent_when_package_absent(self, caplog, monkeypatch):
        """Force the importlib lookup to raise PackageNotFoundError и
        confirm `ready()` swallows it without logging."""
        from importlib import metadata

        original = metadata.distribution

        def _raise(*args, **kwargs):
            raise metadata.PackageNotFoundError("ayla-ai-core")

        monkeypatch.setattr(metadata, "distribution", _raise)

        caplog.clear()
        with caplog.at_level("INFO", logger="apps.orchestrator.apps"):
            OrchestratorConfig._log_ayla_ai_core_version()
        # Restore is automatic via monkeypatch fixture teardown.
        _ = original  # silence ruff F841 if it complains

        boot_records = [r for r in caplog.records if r.name == "apps.orchestrator.apps"]
        assert boot_records == [], (
            f"Expected silent skip when package absent, but got "
            f"{[r.getMessage() for r in boot_records]}"
        )
