"""CLI tests (DRF-520 / Sprint 5 / D7)."""

from __future__ import annotations

import json

import pytest

from apps.replay import __main__ as cli


class TestParser:
    def test_run_subcommand_help(self):
        p = cli._parser()
        # Should parse a minimal run invocation.
        args = p.parse_args(
            [
                "run",
                "--tenant",
                "x",
                "--fixture-set",
                "fixtures/golden",
            ]
        )
        assert args.subcommand == "run"
        assert args.tenant == "x"
        assert args.report == "text"
        assert args.strict_must_pass is False
        assert args.strict_forbidden is False

    def test_run_subcommand_strict_flags(self):
        p = cli._parser()
        args = p.parse_args(
            [
                "run",
                "--tenant",
                "x",
                "--fixture-set",
                "f/",
                "--strict-must-pass",
                "--strict-forbidden",
                "--report",
                "json",
            ]
        )
        assert args.strict_must_pass is True
        assert args.strict_forbidden is True
        assert args.report == "json"

    def test_isolated_flag_default_false(self):
        p = cli._parser()
        args = p.parse_args(["run", "--tenant", "x", "--fixture-set", "f/"])
        assert args.isolated is False

    def test_isolated_flag_enabled(self):
        p = cli._parser()
        args = p.parse_args(["run", "--tenant", "x", "--fixture-set", "f/", "--isolated"])
        assert args.isolated is True

    def test_diff_subcommand(self):
        p = cli._parser()
        args = p.parse_args(
            [
                "diff",
                "--baseline",
                "a.json",
                "--candidate",
                "b.json",
                "--threshold-similarity",
                "0.9",
            ]
        )
        assert args.subcommand == "diff"
        assert args.threshold_similarity == 0.9

    def test_no_subcommand_raises(self):
        p = cli._parser()
        with pytest.raises(SystemExit):
            p.parse_args([])


class TestRenderReports:
    def _mk_report(self, name, passed, failures=None, voice=None):
        from apps.replay.runner import RunReport

        return RunReport(
            fixture_name=name,
            passed=passed,
            trace_id="t-id",
            failures=failures or [],
            voice_check_failures=voice or [],
        )

    def test_text_summary_all_pass(self, capsys):
        reports = [self._mk_report("a", True), self._mk_report("b", True)]
        args = cli._parser().parse_args(["run", "--tenant", "x", "--fixture-set", "/"])
        rc = cli._render_reports(reports, args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "2/2 passed" in out

    def test_text_summary_with_failure(self, capsys):
        reports = [
            self._mk_report("a", True),
            self._mk_report("b", False, failures=["must_pass.intent: expected 'faq'"]),
        ]
        args = cli._parser().parse_args(["run", "--tenant", "x", "--fixture-set", "/"])
        rc = cli._render_reports(reports, args)
        assert rc == 1
        out = capsys.readouterr().out
        assert "[FAIL] b" in out
        assert "1/2 passed, 1 failed" in out

    def test_json_report(self, capsys):
        reports = [self._mk_report("a", False, failures=["x"])]
        args = cli._parser().parse_args(
            ["run", "--tenant", "x", "--fixture-set", "/", "--report", "json"]
        )
        rc = cli._render_reports(reports, args)
        assert rc == 1
        out = capsys.readouterr().out
        payload = json.loads(out)
        assert payload["total"] == 1
        assert payload["failed"] == 1
        assert payload["fixtures"][0]["failures"] == ["x"]


class TestDiffCommand:
    def test_diff_within_threshold(self, tmp_path, capsys):
        a = tmp_path / "a.json"
        b = tmp_path / "b.json"
        a.write_text(
            json.dumps(
                {"response_text": "hello world", "tool_calls": [], "latency_ms": 100, "tokens": 50}
            )
        )
        b.write_text(
            json.dumps(
                {"response_text": "hello world!", "tool_calls": [], "latency_ms": 100, "tokens": 50}
            )
        )
        args = cli._parser().parse_args(
            [
                "diff",
                "--baseline",
                str(a),
                "--candidate",
                str(b),
                "--threshold-similarity",
                "0.7",
            ]
        )
        rc = cli._cmd_diff(args)
        assert rc == 0

    def test_diff_below_threshold(self, tmp_path):
        a = tmp_path / "a.json"
        b = tmp_path / "b.json"
        a.write_text(
            json.dumps({"response_text": "abc", "tool_calls": [], "latency_ms": 0, "tokens": 0})
        )
        b.write_text(
            json.dumps(
                {
                    "response_text": "completely different",
                    "tool_calls": [],
                    "latency_ms": 0,
                    "tokens": 0,
                }
            )
        )
        args = cli._parser().parse_args(
            [
                "diff",
                "--baseline",
                str(a),
                "--candidate",
                str(b),
                "--threshold-similarity",
                "0.9",
            ]
        )
        rc = cli._cmd_diff(args)
        assert rc == 1

    def test_diff_missing_file(self, tmp_path):
        args = cli._parser().parse_args(
            [
                "diff",
                "--baseline",
                str(tmp_path / "nope.json"),
                "--candidate",
                str(tmp_path / "nada.json"),
            ]
        )
        rc = cli._cmd_diff(args)
        assert rc == 2
