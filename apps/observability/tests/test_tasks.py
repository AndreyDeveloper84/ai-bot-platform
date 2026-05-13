"""compute_shadow_delta Celery task tests (DRF-719 / Sprint 8 / S4)."""

from __future__ import annotations

import datetime as _dt
from pathlib import Path
from unittest.mock import patch

import pytest
from django.conf import settings

from apps.audit.models import AuditLog
from apps.observability.models import ShadowDeltaSnapshot
from apps.observability.tasks import compute_shadow_delta
from apps.tenancy.models import Tenant


pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _ground_truth_dir(tmp_path: Path) -> Path:
    settings.SHADOW_GROUND_TRUTH_PATH = str(tmp_path)
    return tmp_path


@pytest.fixture
def shadow_tenant() -> Tenant:
    return Tenant.objects.create(slug="s4-tenant", name="S4", shadow_mode=True)


@pytest.fixture
def non_shadow_tenant() -> Tenant:
    return Tenant.objects.create(slug="s4-off", name="S4 off", shadow_mode=False)


def _empty_csv(dir_path: Path, date: _dt.date) -> Path:
    path = dir_path / f"{date.isoformat()}.csv"
    path.write_text(
        "bot_user_id,text,ts,intent,action_type,latency_ms,is_error\n", encoding="utf-8"
    )
    return path


class TestBeatScheduleRegistration:
    def test_beat_entry_present(self) -> None:
        from django.conf import settings as live

        assert "compute_shadow_delta_daily" in live.CELERY_BEAT_SCHEDULE
        entry = live.CELERY_BEAT_SCHEDULE["compute_shadow_delta_daily"]
        assert entry["task"] == "apps.observability.tasks.compute_shadow_delta"


class TestRun:
    def test_creates_snapshot_per_shadow_tenant(
        self,
        shadow_tenant: Tenant,
        non_shadow_tenant: Tenant,
        _ground_truth_dir: Path,
    ) -> None:
        target = _dt.date(2026, 5, 12)
        _empty_csv(_ground_truth_dir, target)
        compute_shadow_delta.apply(kwargs={"target_date_iso": target.isoformat()})

        # One snapshot for the shadow tenant, none for the non-shadow tenant.
        snaps = list(ShadowDeltaSnapshot.objects.all())
        assert len(snaps) == 1
        assert snaps[0].tenant_id == shadow_tenant.id
        assert snaps[0].snapshot_date == target

    def test_idempotent_on_rerun(self, shadow_tenant: Tenant, _ground_truth_dir: Path) -> None:
        target = _dt.date(2026, 5, 12)
        _empty_csv(_ground_truth_dir, target)
        compute_shadow_delta.apply(kwargs={"target_date_iso": target.isoformat()})
        compute_shadow_delta.apply(kwargs={"target_date_iso": target.isoformat()})
        compute_shadow_delta.apply(kwargs={"target_date_iso": target.isoformat()})

        # Three runs, one row (unique constraint + update_or_create).
        assert ShadowDeltaSnapshot.objects.filter(tenant=shadow_tenant).count() == 1

    def test_audit_row_written(self, shadow_tenant: Tenant, _ground_truth_dir: Path) -> None:
        target = _dt.date(2026, 5, 12)
        _empty_csv(_ground_truth_dir, target)
        compute_shadow_delta.apply(kwargs={"target_date_iso": target.isoformat()})

        actions = set(AuditLog.all_tenants.values_list("action", flat=True))
        assert "observability.shadow_delta.computed" in actions


class TestTelegramDigest:
    def test_no_credentials_skips_post(
        self,
        shadow_tenant: Tenant,
        _ground_truth_dir: Path,
    ) -> None:
        target = _dt.date(2026, 5, 12)
        _empty_csv(_ground_truth_dir, target)
        settings.ADMIN_MAX_CHAT_ID = ""
        settings.MAX_BOT_TOKEN = ""

        with patch("apps.observability.tasks._post_to_max") as mock_post:
            compute_shadow_delta.apply(kwargs={"target_date_iso": target.isoformat()})

        mock_post.assert_not_called()

    def test_with_credentials_calls_post(
        self,
        shadow_tenant: Tenant,
        _ground_truth_dir: Path,
    ) -> None:
        target = _dt.date(2026, 5, 12)
        _empty_csv(_ground_truth_dir, target)
        settings.ADMIN_MAX_CHAT_ID = "1234"
        settings.MAX_BOT_TOKEN = "fake-token"  # noqa: S105 — test literal

        with patch("apps.observability.tasks._post_to_max") as mock_post:
            compute_shadow_delta.apply(kwargs={"target_date_iso": target.isoformat()})

        mock_post.assert_called_once()
        args, _kwargs = mock_post.call_args
        token_arg, chat_arg, text_arg = args
        assert token_arg == "fake-token"
        assert chat_arg == "1234"
        assert "Shadow delta" in text_arg
        assert "s4-tenant" in text_arg


class TestDefaultDateIsYesterday:
    def test_no_target_date_picks_yesterday(
        self,
        shadow_tenant: Tenant,
        _ground_truth_dir: Path,
    ) -> None:
        yesterday = (_dt.datetime.now(tz=_dt.timezone.utc) - _dt.timedelta(days=1)).date()
        _empty_csv(_ground_truth_dir, yesterday)
        result = compute_shadow_delta.apply()
        assert result.get()["snapshot_date"] == yesterday.isoformat()
