"""Shadow-mode delta dashboard view (DRF-721 / Sprint 8 / D1).

Read-only staff-only Django view that surfaces the last-14-days
:class:`apps.observability.models.ShadowDeltaSnapshot` rows so the
operator can spot-check agreement metrics without opening psql.

### Why Django admin template + inline CSS

Decision 10 in `docs/plans/sprint-8-observability-shadow.md`:
Sprint 8 is observability-first; no React. The Phase 1 migration path
is "drop the rows into Grafana via a Prometheus exporter", at which
point this view becomes the cold-storage operator backstop and
Grafana the hot one. Keep this view dependency-free + dependency-shallow.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.http import HttpRequest, HttpResponse
from django.utils import timezone
from django.utils.html import escape

from apps.observability.constants import (
    AGREEMENT_THRESHOLD_AMBER,
    AGREEMENT_THRESHOLD_GREEN,
)
from apps.observability.models import ShadowDeltaSnapshot


_LOOKBACK_DAYS = 14


def shadow_dashboard(request: HttpRequest) -> HttpResponse:
    """Render the last-N-days delta table.

    Staff-only. Anonymous + non-staff users → 403. We do NOT use
    `staff_member_required` because that decorator redirects to a
    login page (HTTP 302) — for operator dashboards we want an
    explicit 403 so monitoring probes flag the access denial.
    """
    if not (request.user.is_authenticated and request.user.is_staff):
        return HttpResponse("forbidden", status=403)

    cutoff = (timezone.now() - timedelta(days=_LOOKBACK_DAYS)).date()
    rows = list(
        ShadowDeltaSnapshot.objects.filter(snapshot_date__gte=cutoff)
        .select_related("tenant")
        .order_by("-snapshot_date", "tenant__slug")
    )

    # Plain-Python rendering — no template file. Keeps the dashboard
    # self-contained and avoids template-loader path drift. A few rows
    # of inline HTML are easier to maintain than the equivalent
    # template + loader config for an admin-style table.
    body = _render_table(rows)
    html = _wrap_page(body, count=len(rows))
    return HttpResponse(html, content_type="text/html; charset=utf-8")


def _render_table(rows: list[ShadowDeltaSnapshot]) -> str:
    if not rows:
        return "<p>No ShadowDeltaSnapshot rows in the last 14 days.</p>"

    header = (
        "<tr>"
        "<th>Date</th>"
        "<th>Tenant</th>"
        "<th>Intent agreement</th>"
        "<th>Action-type agreement</th>"
        "<th>Latency Δ p50 (ms)</th>"
        "<th>Latency Δ p95 (ms)</th>"
        "<th>Error Δ %</th>"
        "<th>Sample count</th>"
        "</tr>"
    )

    body_rows: list[str] = []
    for r in rows:
        payload: dict[str, Any] = r.payload or {}
        p50 = payload.get("latency_p50_delta_ms", 0)
        p95 = payload.get("latency_p95_delta_ms", 0)
        err = payload.get("error_delta_pct", 0)
        body_rows.append(
            "<tr>"
            f"<td>{escape(r.snapshot_date.isoformat())}</td>"
            f"<td>{escape(r.tenant.slug)}</td>"
            f"<td class='{_agreement_class(r.intent_agreement)}'>"
            f"{r.intent_agreement:.1%}</td>"
            f"<td>{r.action_type_agreement:.1%}</td>"
            f"<td>{p50:+.0f}</td>"
            f"<td>{p95:+.0f}</td>"
            f"<td>{err:.1%}</td>"
            f"<td>{r.sample_count}</td>"
            "</tr>"
        )
    return f"<table class='shadow-delta'><thead>{header}</thead><tbody>{''.join(body_rows)}</tbody></table>"


def _agreement_class(agreement: float) -> str:
    """Three-tier badge class — matches the S4 Telegram digest emoji
    bands so the dashboard + the alert tell the same story.
    Thresholds live in :mod:`apps.observability.constants`.
    """
    if agreement >= AGREEMENT_THRESHOLD_GREEN:
        return "agreement-green"
    if agreement >= AGREEMENT_THRESHOLD_AMBER:
        return "agreement-amber"
    return "agreement-red"


_INLINE_CSS = """
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
       max-width: 1200px; margin: 2em auto; padding: 0 1em; color: #222; }
h1 { font-size: 1.4em; margin-bottom: 0.2em; }
.meta { color: #666; font-size: 0.9em; margin-bottom: 1.5em; }
table.shadow-delta { width: 100%; border-collapse: collapse; }
table.shadow-delta th, table.shadow-delta td {
  padding: 8px 12px; border-bottom: 1px solid #eee; text-align: right;
}
table.shadow-delta th:nth-child(1), table.shadow-delta td:nth-child(1),
table.shadow-delta th:nth-child(2), table.shadow-delta td:nth-child(2) {
  text-align: left;
}
table.shadow-delta th { background: #f7f7f9; font-weight: 600; }
.agreement-green { color: #1e7e34; font-weight: 600; }
.agreement-amber { color: #b07c00; font-weight: 600; }
.agreement-red   { color: #c0392b; font-weight: 600; }
.note { color: #666; font-size: 0.85em; margin-top: 1.2em; }
"""


def _wrap_page(body: str, *, count: int) -> str:
    return (
        "<!doctype html>"
        "<html><head><meta charset='utf-8'><title>Shadow delta</title>"
        f"<style>{_INLINE_CSS}</style></head><body>"
        "<h1>Shadow-mode delta</h1>"
        f"<p class='meta'>Last 14 days · {count} row(s)</p>"
        f"{body}"
        "<p class='note'>"
        "Sprint 8 / D1 (DRF-721). Phase 1 migration path: Prometheus exporter "
        "from <code>ShadowDeltaSnapshot</code> → Grafana panel; "
        "this view becomes the cold-storage operator backstop.</p>"
        "</body></html>"
    )
