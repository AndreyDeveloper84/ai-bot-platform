"""Daily delta math tests (DRF-720 / Sprint 8 / S5 — exercises S3).

Eight scripted scenarios covering CSV-driven agreement math:

1. Happy path — synthetic shadow Messages + matching CSV → 100% intent agreement.
2. Half-disagreement — 1/2 intent mismatch → 50%.
3. Timestamp drift — within 60s window matches, outside skips.
4. Missing ground-truth row — counted as no-match for the shadow row.
5. Intent alias map — `kb_faq` vs `faq` match.
6. CSV schema drift — missing required column → DeltaSchemaError.
7. Latency delta math — verifies p50/p95 delta (ms).
8. Empty tenant data → DeltaSummary with `sample_count=0` and a debug
   reason.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

import pytest

from apps.conversations.models import Conversation, Message
from apps.identity.models import BotUser
from apps.observability.delta import (
    DeltaSchemaError,
    DeltaSummary,
    compute_daily_delta,
)
from apps.tenancy.models import Tenant


# All tests need DB plus tmp_path-backed ground-truth CSV.
pytestmark = pytest.mark.django_db


@pytest.fixture
def shadow_tenant() -> Tenant:
    return Tenant.objects.create(slug="s5-tenant", name="S5", shadow_mode=True)


@pytest.fixture
def shadow_date() -> _dt.date:
    return _dt.date(2026, 5, 13)


@pytest.fixture(autouse=True)
def _ground_truth_dir(tmp_path: Path, settings) -> Path:
    settings.SHADOW_GROUND_TRUTH_PATH = str(tmp_path)
    return tmp_path


def _write_csv(
    dir_path: Path,
    date: _dt.date,
    rows: list[dict[str, str]],
    *,
    omit_columns: tuple[str, ...] = (),
) -> Path:
    columns = [
        c
        for c in ("bot_user_id", "text", "ts", "intent", "action_type", "latency_ms", "is_error")
        if c not in omit_columns
    ]
    path = dir_path / f"{date.isoformat()}.csv"
    lines = [",".join(columns)]
    for r in rows:
        lines.append(",".join(r.get(c, "") for c in columns))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _seed_shadow_turn(
    tenant: Tenant,
    *,
    channel_user_id: str,
    text: str,
    ts: _dt.datetime,
    action_type: str = "faq",
    intent: str = "faq",
    latency_ms: int = 800,
) -> None:
    bot = BotUser.all_tenants.create(
        tenant=tenant,
        channel="max",
        channel_user_id=channel_user_id,
    )
    conv = Conversation.all_tenants.create(tenant=tenant, bot_user=bot, is_shadow=True)
    Message.all_tenants.create(
        tenant=tenant,
        conversation=conv,
        role="user",
        content=text,
        created_at=ts,
    )
    Message.all_tenants.create(
        tenant=tenant,
        conversation=conv,
        role="assistant",
        content=f"reply to {text}",
        action_type=action_type,
        action_data={"intent": intent},
        latency_ms=latency_ms,
        created_at=ts + _dt.timedelta(milliseconds=latency_ms),
    )
    # `created_at` is auto_now_add → not honoured at create-time, so
    # force-set via update for both rows.
    Message.all_tenants.filter(conversation=conv, role="user").update(created_at=ts)
    Message.all_tenants.filter(conversation=conv, role="assistant").update(
        created_at=ts + _dt.timedelta(milliseconds=latency_ms)
    )


def _ts(date: _dt.date, hour: int = 12, minute: int = 0) -> str:
    dt = _dt.datetime(date.year, date.month, date.day, hour, minute, tzinfo=_dt.timezone.utc)
    return dt.isoformat()


# ---------------------------------------------------------------------------
# 1. Happy path
# ---------------------------------------------------------------------------


def test_happy_path_100_percent_agreement(
    shadow_tenant: Tenant, shadow_date: _dt.date, _ground_truth_dir: Path
) -> None:
    _seed_shadow_turn(
        shadow_tenant,
        channel_user_id="u1",
        text="привет",
        ts=_dt.datetime(2026, 5, 13, 12, 0, tzinfo=_dt.timezone.utc),
    )
    _write_csv(
        _ground_truth_dir,
        shadow_date,
        [
            {
                "bot_user_id": "u1",
                "text": "привет",
                "ts": _ts(shadow_date),
                "intent": "faq",
                "action_type": "faq",
                "latency_ms": "800",
                "is_error": "false",
            }
        ],
    )
    result = compute_daily_delta(shadow_date, shadow_tenant)
    assert result.sample_count == 1
    assert result.intent_agreement == 1.0
    assert result.action_type_agreement == 1.0


# ---------------------------------------------------------------------------
# 2. Half-disagreement
# ---------------------------------------------------------------------------


def test_half_disagreement(
    shadow_tenant: Tenant, shadow_date: _dt.date, _ground_truth_dir: Path
) -> None:
    ts0 = _dt.datetime(2026, 5, 13, 10, 0, tzinfo=_dt.timezone.utc)
    _seed_shadow_turn(shadow_tenant, channel_user_id="u1", text="привет", ts=ts0, intent="faq")
    _seed_shadow_turn(
        shadow_tenant,
        channel_user_id="u2",
        text="запиши",
        ts=ts0 + _dt.timedelta(minutes=10),
        intent="booking",
    )
    _write_csv(
        _ground_truth_dir,
        shadow_date,
        [
            {
                "bot_user_id": "u1",
                "text": "привет",
                "ts": _ts(shadow_date, 10, 0),
                "intent": "faq",
                "action_type": "faq",
                "latency_ms": "800",
                "is_error": "false",
            },
            {
                "bot_user_id": "u2",
                "text": "запиши",
                "ts": _ts(shadow_date, 10, 10),
                # mysite produced a different intent → disagreement
                "intent": "small_talk",
                "action_type": "faq",
                "latency_ms": "900",
                "is_error": "false",
            },
        ],
    )
    result = compute_daily_delta(shadow_date, shadow_tenant)
    assert result.sample_count == 2
    assert result.intent_agreement == 0.5


# ---------------------------------------------------------------------------
# 3. Timestamp drift inside / outside window
# ---------------------------------------------------------------------------


def test_timestamp_within_60s_matches(
    shadow_tenant: Tenant, shadow_date: _dt.date, _ground_truth_dir: Path
) -> None:
    ts0 = _dt.datetime(2026, 5, 13, 10, 0, 0, tzinfo=_dt.timezone.utc)
    _seed_shadow_turn(shadow_tenant, channel_user_id="u1", text="x", ts=ts0)
    # ground truth is 45 seconds later — inside window
    _write_csv(
        _ground_truth_dir,
        shadow_date,
        [
            {
                "bot_user_id": "u1",
                "text": "x",
                "ts": (ts0 + _dt.timedelta(seconds=45)).isoformat(),
                "intent": "faq",
                "action_type": "faq",
                "latency_ms": "800",
                "is_error": "false",
            }
        ],
    )
    result = compute_daily_delta(shadow_date, shadow_tenant)
    assert result.sample_count == 1


def test_timestamp_outside_60s_skips(
    shadow_tenant: Tenant, shadow_date: _dt.date, _ground_truth_dir: Path
) -> None:
    ts0 = _dt.datetime(2026, 5, 13, 10, 0, 0, tzinfo=_dt.timezone.utc)
    _seed_shadow_turn(shadow_tenant, channel_user_id="u1", text="x", ts=ts0)
    _write_csv(
        _ground_truth_dir,
        shadow_date,
        [
            {
                "bot_user_id": "u1",
                "text": "x",
                "ts": (ts0 + _dt.timedelta(seconds=120)).isoformat(),
                "intent": "faq",
                "action_type": "faq",
                "latency_ms": "800",
                "is_error": "false",
            }
        ],
    )
    result = compute_daily_delta(shadow_date, shadow_tenant)
    assert result.sample_count == 0


# ---------------------------------------------------------------------------
# 4. Missing ground-truth row
# ---------------------------------------------------------------------------


def test_no_ground_truth_csv_returns_empty(shadow_tenant: Tenant, shadow_date: _dt.date) -> None:
    result = compute_daily_delta(shadow_date, shadow_tenant)
    assert isinstance(result, DeltaSummary)
    assert result.sample_count == 0
    assert result.debug.get("reason") == "no_ground_truth_csv"


# ---------------------------------------------------------------------------
# 5. Intent alias map (kb_faq → faq)
# ---------------------------------------------------------------------------


def test_intent_alias_matches(
    shadow_tenant: Tenant, shadow_date: _dt.date, _ground_truth_dir: Path
) -> None:
    ts0 = _dt.datetime(2026, 5, 13, 10, 0, tzinfo=_dt.timezone.utc)
    _seed_shadow_turn(shadow_tenant, channel_user_id="u1", text="x", ts=ts0, intent="kb_faq")
    _write_csv(
        _ground_truth_dir,
        shadow_date,
        [
            {
                "bot_user_id": "u1",
                "text": "x",
                "ts": _ts(shadow_date, 10, 0),
                "intent": "faq",  # mysite still uses the old name
                "action_type": "faq",
                "latency_ms": "800",
                "is_error": "false",
            }
        ],
    )
    result = compute_daily_delta(shadow_date, shadow_tenant)
    assert result.intent_agreement == 1.0


# ---------------------------------------------------------------------------
# 6. CSV schema drift
# ---------------------------------------------------------------------------


def test_missing_column_raises(
    shadow_tenant: Tenant, shadow_date: _dt.date, _ground_truth_dir: Path
) -> None:
    _seed_shadow_turn(
        shadow_tenant,
        channel_user_id="u1",
        text="x",
        ts=_dt.datetime(2026, 5, 13, 10, 0, tzinfo=_dt.timezone.utc),
    )
    _write_csv(
        _ground_truth_dir,
        shadow_date,
        [
            {
                "bot_user_id": "u1",
                "text": "x",
                "ts": _ts(shadow_date, 10, 0),
                "intent": "faq",
                # Note: `action_type` omitted — schema drift.
                "latency_ms": "800",
            }
        ],
        omit_columns=("action_type",),
    )
    with pytest.raises(DeltaSchemaError, match="action_type"):
        compute_daily_delta(shadow_date, shadow_tenant)


# ---------------------------------------------------------------------------
# 7. Latency delta
# ---------------------------------------------------------------------------


def test_latency_delta_signed(
    shadow_tenant: Tenant, shadow_date: _dt.date, _ground_truth_dir: Path
) -> None:
    ts0 = _dt.datetime(2026, 5, 13, 10, 0, tzinfo=_dt.timezone.utc)
    _seed_shadow_turn(
        shadow_tenant,
        channel_user_id="u1",
        text="x",
        ts=ts0,
        latency_ms=1200,  # platform slower
    )
    _write_csv(
        _ground_truth_dir,
        shadow_date,
        [
            {
                "bot_user_id": "u1",
                "text": "x",
                "ts": _ts(shadow_date, 10, 0),
                "intent": "faq",
                "action_type": "faq",
                "latency_ms": "800",  # mysite faster
                "is_error": "false",
            }
        ],
    )
    result = compute_daily_delta(shadow_date, shadow_tenant)
    # Platform was 400ms slower than mysite.
    assert result.latency_p50_delta_ms == 400.0
    assert result.latency_p95_delta_ms == 400.0


# ---------------------------------------------------------------------------
# 8. Empty tenant data
# ---------------------------------------------------------------------------


def test_empty_tenant_returns_zero_sample(
    shadow_tenant: Tenant, shadow_date: _dt.date, _ground_truth_dir: Path
) -> None:
    # CSV exists but no shadow rows for the tenant.
    _write_csv(
        _ground_truth_dir,
        shadow_date,
        [
            {
                "bot_user_id": "u999",
                "text": "anything",
                "ts": _ts(shadow_date),
                "intent": "faq",
                "action_type": "faq",
                "latency_ms": "800",
                "is_error": "false",
            }
        ],
    )
    result = compute_daily_delta(shadow_date, shadow_tenant)
    assert result.sample_count == 0
    assert result.debug.get("reason") == "no_shadow_traffic"
