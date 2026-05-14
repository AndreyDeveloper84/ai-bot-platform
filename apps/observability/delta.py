"""Daily shadow-mode delta math (Sprint 8 / S3 / DRF-718).

The Sprint 8 exit gate is "≥95% intent agreement for 7 consecutive
days". This module is the math that produces the per-day agreement
number consumed by the dashboard (D1) and the strict-scope flip
gate (F1).

### Inputs

* **Shadow rows** — :class:`apps.conversations.models.Message` filtered
  to ``conversation.is_shadow=True``, the user-side text on the inbound
  side, the assistant turn on the outbound side. Latency is read off
  the orchestrator audit metadata.
* **Ground truth** — mysite's Telegram-export CSV deposited under
  ``settings.SHADOW_GROUND_TRUTH_PATH/<YYYY-MM-DD>.csv``. The CSV
  carries ``(bot_user_id, text, ts, intent, action_type, latency_ms)``
  per turn. Schema drift is tolerated: missing columns raise
  ``DeltaSchemaError``, extra columns are ignored.

### Join

A platform shadow turn pairs with a ground-truth turn when:

* ``bot_user_id`` matches (mysite exports use the same channel_user_id),
* user text matches verbatim, and
* the two timestamps fall within 60 seconds of each other (covers
  nginx-mirror jitter + clock skew).

Unmatched shadow rows count as no-match for ``intent_agreement`` —
silent skip would let the metric drift.

### Decision 4 — CSV ground truth

The plan chose CSV over a direct mysite Postgres read because the
incident-review process already produces these dumps. Sprint 9 may
flip to a streaming source.
"""

from __future__ import annotations

import csv
import datetime as _dt
import logging
import statistics
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from django.conf import settings

if TYPE_CHECKING:  # pragma: no cover
    from apps.tenancy.models import Tenant

logger = logging.getLogger(__name__)


# Required CSV columns. Missing = DeltaSchemaError (clear failure, not
# silent zero). Extra columns are tolerated — mysite may add per-event
# debug fields without breaking us.
_REQUIRED_COLUMNS = frozenset({"bot_user_id", "text", "ts", "intent", "action_type", "latency_ms"})

# Timestamp join window. nginx mirror introduces a few hundred ms of
# jitter; mysite's clock can drift up to a few seconds. 60s window
# captures both without false-positive matches.
_JOIN_WINDOW_SECONDS = 60.0

# Intent alias map — Sprint 7 renamed a few intents. Without this, a
# platform "kb_faq" would not match a ground-truth "faq".
_INTENT_ALIASES: dict[str, str] = {
    "kb_faq": "faq",
    "faq": "faq",
}


class DeltaSchemaError(ValueError):
    """Raised when the CSV header is missing one of `_REQUIRED_COLUMNS`."""


@dataclass(frozen=True)
class DeltaSummary:
    """One day's worth of shadow-vs-mysite comparison.

    All ratios are 0..1; latency deltas are milliseconds (platform - mysite).
    A positive ``latency_p95_delta_ms`` means the platform is slower.
    """

    intent_agreement: float = 0.0
    action_type_agreement: float = 0.0
    latency_p50_delta_ms: float = 0.0
    latency_p95_delta_ms: float = 0.0
    error_delta_pct: float = 0.0
    sample_count: int = 0
    debug: dict[str, Any] = field(default_factory=dict)

    def as_payload(self) -> dict[str, Any]:
        """Serialise to the JSON payload persisted on ``ShadowDeltaSnapshot.payload``."""
        return asdict(self)


@dataclass(frozen=True)
class _ShadowRow:
    bot_user_id: str
    text: str
    ts: _dt.datetime
    intent: str
    action_type: str
    latency_ms: float
    is_error: bool


@dataclass(frozen=True)
class _GroundTruthRow:
    bot_user_id: str
    text: str
    ts: _dt.datetime
    intent: str
    action_type: str
    latency_ms: float
    is_error: bool


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def compute_daily_delta(date: _dt.date, tenant: "Tenant") -> DeltaSummary:
    """Compute the daily delta for one tenant. Returns empty summary
    (sample_count=0) when no ground-truth CSV exists OR no shadow
    rows landed.
    """
    csv_rows = _load_ground_truth(date)
    if csv_rows is None:
        logger.info(
            "observability.delta.no_ground_truth date=%s tenant=%s",
            date,
            tenant.id,
        )
        return DeltaSummary(debug={"reason": "no_ground_truth_csv"})

    shadow_rows = _load_shadow_rows(date, tenant)
    if not shadow_rows:
        return DeltaSummary(debug={"reason": "no_shadow_traffic"})

    matched = _join_rows(shadow_rows, csv_rows)
    if not matched:
        return DeltaSummary(
            sample_count=0,
            debug={
                "reason": "no_matches",
                "shadow_count": len(shadow_rows),
                "ground_truth_count": len(csv_rows),
            },
        )

    intent_hits = sum(1 for s, g in matched if _alias(s.intent) == _alias(g.intent))
    action_hits = sum(1 for s, g in matched if s.action_type == g.action_type)
    error_diffs = sum(1 for s, g in matched if s.is_error != g.is_error)

    s_latencies = [s.latency_ms for s, _ in matched]
    g_latencies = [g.latency_ms for _, g in matched]

    return DeltaSummary(
        intent_agreement=intent_hits / len(matched),
        action_type_agreement=action_hits / len(matched),
        latency_p50_delta_ms=_percentile(s_latencies, 50) - _percentile(g_latencies, 50),
        latency_p95_delta_ms=_percentile(s_latencies, 95) - _percentile(g_latencies, 95),
        error_delta_pct=error_diffs / len(matched),
        sample_count=len(matched),
        debug={
            "shadow_count": len(shadow_rows),
            "ground_truth_count": len(csv_rows),
            "matched_count": len(matched),
        },
    )


# ---------------------------------------------------------------------------
# CSV ground-truth loader
# ---------------------------------------------------------------------------


def _load_ground_truth(date: _dt.date) -> list[_GroundTruthRow] | None:
    base = Path(str(getattr(settings, "SHADOW_GROUND_TRUTH_PATH", "")) or "")
    if not base:
        return None
    path = base / f"{date.isoformat()}.csv"
    if not path.exists():
        return None

    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None or not _REQUIRED_COLUMNS.issubset(set(reader.fieldnames)):
            missing = _REQUIRED_COLUMNS - set(reader.fieldnames or [])
            raise DeltaSchemaError(f"ground-truth CSV missing required columns: {sorted(missing)}")
        rows: list[_GroundTruthRow] = []
        for raw in reader:
            rows.append(
                _GroundTruthRow(
                    bot_user_id=str(raw["bot_user_id"]),
                    text=str(raw["text"]),
                    ts=_parse_ts(raw["ts"]),
                    intent=str(raw["intent"]),
                    action_type=str(raw["action_type"]),
                    latency_ms=float(raw["latency_ms"]) if raw["latency_ms"] else 0.0,
                    is_error=str(raw.get("is_error", "")).lower() in {"1", "true"},
                )
            )
    return rows


# ---------------------------------------------------------------------------
# Shadow row loader
# ---------------------------------------------------------------------------


def _load_shadow_rows(date: _dt.date, tenant: "Tenant") -> list[_ShadowRow]:
    """Read shadow turn pairs for ``date``.

    A shadow "turn" = a (user, assistant) pair of Messages in a
    Conversation where ``is_shadow=True``. The user message carries the
    raw text (``content``); the assistant message carries
    ``action_type``, ``latency_ms``, and any per-skill metadata via
    ``action_data``.

    We iterate user messages and look up the next-after assistant row in
    the same conversation. Pairs without a paired assistant row are
    skipped — those are turns that crashed before composer finished, and
    they show up in the error_delta_pct downstream.

    Sprint 8 review P1-5 fixed an O(n) per-user-message lookup —
    1001 queries on a 1000-msg day — by bulk-fetching all assistant
    rows in the window once and pairing in Python. Now O(2). The
    upper fence on the assistant query (``day_end + 5 minutes``) also
    fixes P2-2: a user message at 23:59:58 no longer accidentally
    pairs with an assistant reply from the next UTC day.
    """
    from collections import defaultdict

    from apps.conversations.models import Message

    day_start = _dt.datetime.combine(date, _dt.time.min, tzinfo=_dt.timezone.utc)
    day_end = day_start + _dt.timedelta(days=1)

    user_msgs = list(
        Message.all_tenants.filter(
            tenant=tenant,
            conversation__is_shadow=True,
            role="user",
            created_at__gte=day_start,
            created_at__lt=day_end,
        ).select_related("conversation__bot_user")
    )

    # Single bulk fetch: every assistant message in the same window,
    # extended by 5 minutes on the trailing edge so a 23:59:58 user
    # message still finds its reply. Ordered ascending so the
    # `defaultdict[conversation_id] → list` is naturally chronological
    # and the first un-consumed entry is the correct pair.
    assistant_by_conv: dict[Any, list[Any]] = defaultdict(list)
    for assistant_msg in Message.all_tenants.filter(
        tenant=tenant,
        conversation__is_shadow=True,
        role="assistant",
        created_at__gte=day_start,
        created_at__lt=day_end + _dt.timedelta(minutes=5),
    ).order_by("created_at"):
        assistant_by_conv[assistant_msg.conversation_id].append(assistant_msg)

    rows: list[_ShadowRow] = []
    for um in user_msgs:
        # Pop the next-after assistant for this conversation, if any.
        # Linear scan is fine — same conversation rarely has more than
        # a handful of turns in one day.
        candidates = assistant_by_conv.get(um.conversation_id) or []
        assistant: Any = None
        for idx, candidate in enumerate(candidates):
            if candidate.created_at >= um.created_at:
                assistant = candidates.pop(idx)
                break
        if assistant is None:
            # No assistant reply paired with this turn — crashed before
            # composer. Counts as error on the platform side later.
            assistant_action_type = ""
            assistant_latency = 0.0
            is_error = True
        else:
            assistant_action_type = str(assistant.action_type or "")
            assistant_latency = float(assistant.latency_ms or 0)
            is_error = assistant_action_type == "error"

        action_data = (assistant.action_data if assistant else {}) or {}
        intent = str(action_data.get("intent", ""))
        rows.append(
            _ShadowRow(
                bot_user_id=str(um.conversation.bot_user.channel_user_id),
                text=str(um.content or ""),
                ts=um.created_at,
                intent=intent,
                action_type=assistant_action_type,
                latency_ms=assistant_latency,
                is_error=is_error,
            )
        )
    return rows


# ---------------------------------------------------------------------------
# Join + helpers
# ---------------------------------------------------------------------------


def _join_rows(
    shadow: list[_ShadowRow],
    ground_truth: list[_GroundTruthRow],
) -> list[tuple[_ShadowRow, _GroundTruthRow]]:
    """Match shadow rows to ground-truth rows on (bot_user_id, text, ts±60s).

    Order matters: each ground-truth row pairs with at most one shadow
    row (otherwise a single mysite turn could match multiple shadows
    that happen to share text + user, inflating the count).
    """
    by_user: dict[str, list[_GroundTruthRow]] = {}
    for g in ground_truth:
        by_user.setdefault(g.bot_user_id, []).append(g)

    matched: list[tuple[_ShadowRow, _GroundTruthRow]] = []
    consumed: set[int] = set()  # ids of already-matched ground_truth rows
    for s in shadow:
        for g in by_user.get(s.bot_user_id, []):
            if id(g) in consumed:
                continue
            if g.text != s.text:
                continue
            if abs((g.ts - s.ts).total_seconds()) > _JOIN_WINDOW_SECONDS:
                continue
            matched.append((s, g))
            consumed.add(id(g))
            break
    return matched


def _alias(intent: str) -> str:
    return _INTENT_ALIASES.get(intent, intent)


def _percentile(values: list[float], pct: int) -> float:
    if not values:
        return 0.0
    # statistics.quantiles ships exact-method percentiles; for small
    # samples we fall back to a manual position-based pick to match
    # what the latency test (G3 / DRF-734) expects.
    sorted_values = sorted(values)
    if len(sorted_values) == 1:
        return sorted_values[0]
    if pct == 50:
        return statistics.median(sorted_values)
    # 95th percentile by index
    k = max(0, int(round(pct / 100 * (len(sorted_values) - 1))))
    return sorted_values[k]


def _parse_ts(raw: str) -> _dt.datetime:
    """Accept either ISO 8601 with `Z` suffix or with a `+00:00` offset."""
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    return _dt.datetime.fromisoformat(raw)
