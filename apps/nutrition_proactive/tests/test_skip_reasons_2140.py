"""Причины пропусков в сводке проактива + сторож «отчёт реально ушёл» (DRF-2140).

Красное листа: стенд 20.09 — ``report.summary planned=16 sent=0 skipped=16``
каждый час сутки напролёт, и ни слова, какие ворота съели шестнадцать.

* s1 — ``skipped_by_reason=`` в строке сводки и в возврате задачи: по одному
  человеку на причину, ключи — только из ``KNOWN_SKIP_REASONS``, без
  идентификаторов; report / water / coach_hint;
* s2 — незнакомый слаг → ``other`` в сводке и отдельная строка
  ``skipped_other=<slug>:n`` — новое не прячется молча;
* s3 — сторож «реально ушло»: кандидат проходит все ворота в час отчёта →
  ``sent=1``, ``_deliver`` вызван с текстом отчёта; ложный вход —
  ``channel_user_id`` опустел между планированием и доставкой →
  ``skipped_by_reason=no_channel:1``, ``failed=0``, не молча;
* s4 — перепись словаря: каждый литерал ``reason`` в пакете есть в
  ``KNOWN_SKIP_REASONS`` (grep по ``selection`` / планировщикам / ``coach``),
  и наоборот — каждый ключ словаря где-то производится.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from pathlib import Path
from unittest.mock import patch

import pytest

from apps.identity.models import BotUser
from apps.nutrition_proactive import coach, prefs, selection, tasks
from apps.nutrition_proactive.tests.test_tasks import (
    ELEVEN_PM,
    NOON,
    at_msk,
    configured_profile,
    make_user,
    summary_reader,
    water_reader,
)
from apps.tenancy.models import Tenant

pytestmark = pytest.mark.django_db


@pytest.fixture
def tenant(db) -> Tenant:
    return Tenant.objects.create(slug="np-2140", name="Salon 2140", timezone="Europe/Moscow")


@pytest.fixture
def armed(settings):
    settings.NUTRITION_PROACTIVE_ENABLED = True
    settings.NUTRITION_PROACTIVE_DRY_RUN = False


def _summary_line(caplog, kind: str) -> str:
    lines = [
        r.getMessage()
        for r in caplog.records
        if f"nutrition_proactive.{kind}.summary" in r.getMessage()
    ]
    assert lines, [r.getMessage() for r in caplog.records]
    return lines[-1]


def _run_report(caplog, *, now):
    with (
        caplog.at_level(logging.INFO, logger="apps.nutrition_proactive.tasks"),
        patch(
            "apps.nutrition_proactive.tasks._fetch_daily",
            side_effect=summary_reader(configured_profile()),
        ),
        patch("apps.nutrition_proactive.tasks.dj_timezone.now", return_value=now),
        patch("apps.nutrition_proactive.tasks.send_message") as send,
    ):
        result = tasks.send_daily_reports()
    return result, send


# ─── s1: причины в сводке ───────────────────────────────────────────────────


class TestReasonsInTheSummary:
    def test_report_summary_names_every_gate_without_identifiers(
        self, tenant, armed, caplog
    ) -> None:
        make_user(tenant, suffix="off", report=prefs.REPORT_OFF)  # report_off
        make_user(
            tenant, suffix="opt", report="19:00", opt_out=True
        )  # opt_out — фильтр выборки: не в planned
        make_user(tenant, suffix="nocons", report="19:00", consented=False)  # no_consent
        make_user(tenant, suffix="later", report="20:00")  # not_report_hour
        make_user(tenant, suffix="due", report="19:00")  # due → sent

        result, send = _run_report(caplog, now=at_msk(19))

        assert result["sent"] == 1 and send.call_count == 1
        assert result["skipped"] == 3 and result["failed"] == 0
        assert result["skipped_by_reason"] == {
            "report_off": 1,
            "no_consent": 1,
            "not_report_hour": 1,
        }
        line = _summary_line(caplog, "report")
        assert "planned=4 would_send=1 sent=1 skipped=3 failed=0" in line
        assert "skipped_by_reason=no_consent:1,not_report_hour:1,report_off:1" in line
        # Без идентификаторов: ни pk, ни внешнего id, ни channel_user_id.
        assert "np-" not in line and "bot:max" not in line
        assert set(result["skipped_by_reason"]) <= set(tasks.KNOWN_SKIP_REASONS)

    def test_water_summary_carries_its_own_ladder(self, tenant, armed, caplog) -> None:
        make_user(tenant, suffix="w1", water=True)  # 23:00 → quiet_hours
        make_user(tenant, suffix="w2", water=False)  # water_off
        with (
            caplog.at_level(logging.INFO, logger="apps.nutrition_proactive.tasks"),
            patch("apps.nutrition_proactive.tasks._fetch_water", side_effect=water_reader(0)),
            patch("apps.nutrition_proactive.tasks.dj_timezone.now", return_value=ELEVEN_PM),
            patch("apps.nutrition_proactive.tasks.send_message") as send,
        ):
            result = tasks.send_water_reminders()
        assert send.call_count == 0
        assert result["skipped_by_reason"] == {"quiet_hours": 1, "water_off": 1}
        assert "skipped_by_reason=quiet_hours:1,water_off:1" in _summary_line(caplog, "water")
        assert set(result["skipped_by_reason"]) <= set(tasks.KNOWN_SKIP_REASONS)

    def test_coach_summary_goes_through_the_same_line(
        self, tenant, armed, caplog, settings
    ) -> None:
        settings.NUTRITION_COACH_ENABLED = True
        settings.NUTRITION_COACH_DRY_RUN = False
        make_user(tenant, suffix="c1")  # без coach_hints pref → hints_off / no_*_consent
        with (
            caplog.at_level(logging.INFO, logger="apps.nutrition_proactive.tasks"),
            patch("apps.nutrition_proactive.tasks.dj_timezone.now", return_value=NOON),
            patch("apps.nutrition_proactive.coach.dj_timezone.now", return_value=NOON),
            patch("apps.nutrition_proactive.tasks.send_message") as send,
        ):
            result = tasks.send_coach_hints()
        assert send.call_count == 0
        assert result["planned"] == 1 and result["skipped"] == 1
        assert sum(result["skipped_by_reason"].values()) == 1
        assert set(result["skipped_by_reason"]) <= set(tasks.KNOWN_SKIP_REASONS)
        assert "skipped_by_reason=" in _summary_line(caplog, "coach_hint")

    def test_disabled_task_returns_the_old_shape_plus_nothing_skipped(self, settings) -> None:
        settings.NUTRITION_PROACTIVE_ENABLED = False
        result = tasks.send_daily_reports()
        assert result == {"planned": 0, "sent": 0, "skipped": 0, "failed": 0, "dry_run": 1}


# ─── s2: незнакомый слаг → other, но не молча ────────────────────────────────


class TestUnknownReasonIsOtherButNamed:
    def test_summarise_folds_unknown_into_other_and_returns_the_slug(self) -> None:
        by_reason, other = tasks._summarise_skips(
            Counter({"report_off": 6, "brand_new_gate": 2, "outbound_safety_medical": 1, "": 1})
        )
        assert by_reason == {"report_off": 6, "other": 3, "outbound_safety_hit": 1}
        assert other == {"brand_new_gate": 2, "other": 1}

    def test_format_orders_largest_first_then_by_name(self) -> None:
        assert tasks._format_counts({"b": 2, "a": 2, "c": 5}) == "c:5,a:2,b:2"
        assert tasks._format_counts({}) == "-"

    def test_run_task_prints_skipped_other_line(self, armed, caplog) -> None:
        decisions = [
            tasks.Decision("u1", "bot:max:1", False, "brand_new_gate"),
            tasks.Decision("u2", "bot:max:2", False, "report_off"),
        ]
        with caplog.at_level(logging.INFO, logger="apps.nutrition_proactive.tasks"):
            result = tasks._run_task("report", lambda: decisions)
        assert result["skipped_by_reason"] == {"other": 1, "report_off": 1}
        messages = [r.getMessage() for r in caplog.records]
        assert any("skipped_by_reason=other:1,report_off:1" in m for m in messages)
        assert any(
            "nutrition_proactive.report.skipped_other=brand_new_gate:1" in m for m in messages
        )
        assert not any("bot:max:1" in m for m in messages)


# ─── s3: сторож «отчёт реально ушёл» ────────────────────────────────────────


class TestReportReallyGoesOut:
    def test_candidate_through_every_gate_is_delivered_with_the_report_text(
        self, tenant, armed, caplog
    ) -> None:
        user = make_user(tenant, suffix="go", report="19:00")
        with patch("apps.nutrition_proactive.tasks._deliver", wraps=tasks._deliver) as deliver:
            result, send = _run_report(caplog, now=at_msk(19))
        assert result["sent"] == 1 and result["failed"] == 0 and result["skipped"] == 0
        assert result["skipped_by_reason"] == {}
        deliver.assert_called_once()
        decision = deliver.call_args.args[0]
        assert decision.reason == "due" and decision.bot_user_id == user.pk
        assert "Калории" in decision.text or "ккал" in decision.text
        send.assert_called_once()
        assert send.call_args.kwargs["user_id"] == "np-go"
        assert send.call_args.kwargs["text"] == decision.text
        assert "skipped_by_reason=-" in _summary_line(caplog, "report")

    def test_channel_vanished_after_planning_is_a_named_skip_not_a_failure(
        self, tenant, armed, caplog
    ) -> None:
        user = make_user(tenant, suffix="gone", report="19:00")

        def _fetch(ext):
            # Планирование прошло — человек стёр канал до доставки.
            BotUser.all_tenants.filter(pk=user.pk).update(channel_user_id="")
            return summary_reader(configured_profile())(ext)

        with (
            caplog.at_level(logging.INFO, logger="apps.nutrition_proactive.tasks"),
            patch("apps.nutrition_proactive.tasks._fetch_daily", side_effect=_fetch),
            patch("apps.nutrition_proactive.tasks.dj_timezone.now", return_value=at_msk(19)),
            patch("apps.nutrition_proactive.tasks.send_message") as send,
        ):
            result = tasks.send_daily_reports()
        send.assert_not_called()
        assert result["sent"] == 0 and result["failed"] == 0
        assert result["skipped"] == 1 and result["skipped_by_reason"] == {"no_channel": 1}
        assert "skipped_by_reason=no_channel:1" in _summary_line(caplog, "report")
        assert not any("Traceback" in (r.exc_text or "") for r in caplog.records)
        # Ключ отправки не поднят: следующий тик попробует снова.
        assert prefs.get_prefs(BotUser.all_tenants.get(pk=user.pk)).get("last_report_date") is None


# ─── s4: перепись словаря ───────────────────────────────────────────────────

_PACKAGE = Path(tasks.__file__).resolve().parent
_LITERAL = re.compile(
    r'(?:decide\(\s*"|Decision\([^)]*?,\s*False,\s*"|return\s+"|\breason=")([a-z_]+)"'
)
#: Слаги, которые встречаются как литералы, но пропуском не являются.
_NOT_SKIPS = {"due", "behind_proportional_norm"}


def _literals_in_package() -> set[str]:
    found: set[str] = set()
    for path in _PACKAGE.glob("*.py"):
        found.update(_LITERAL.findall(path.read_text(encoding="utf-8")))
    return found


class TestSkipReasonVocabularyCensus:
    def test_every_literal_reason_in_the_package_is_in_the_vocabulary(self) -> None:
        literals = _literals_in_package() - _NOT_SKIPS
        # Присутствие впереди отсутствия: перепись что-то нашла.
        assert {"report_off", "quiet_hours", "no_trigger", "on_track"} <= literals, literals
        missing = literals - set(tasks.KNOWN_SKIP_REASONS)
        assert missing == set(), f"reason без места в KNOWN_SKIP_REASONS (уедет в other): {missing}"

    def test_declared_vocabularies_are_covered(self) -> None:
        assert set(selection.BLOCK_REASONS) <= set(tasks.KNOWN_SKIP_REASONS)
        assert set(coach.BLOCK_REASONS) - {"due"} <= set(tasks.KNOWN_SKIP_REASONS)
        assert {"weekly_cap_surface", "weekly_cap_total"} <= set(tasks.KNOWN_SKIP_REASONS)
        assert {"shadow", "unresolved", "no_channel"} <= set(tasks.KNOWN_SKIP_REASONS)

    def test_no_duplicates_and_no_other_in_the_vocabulary(self) -> None:
        assert len(set(tasks.KNOWN_SKIP_REASONS)) == len(tasks.KNOWN_SKIP_REASONS)
        assert tasks.OTHER_REASON not in tasks.KNOWN_SKIP_REASONS
