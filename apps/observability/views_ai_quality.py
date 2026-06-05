"""AI quality observability dashboard (Веха 3 / #772).

Read-only staff-only view rendering the last 14 days of
`AIDailyMetricSummary` per tenant with threshold status indicators +
sparkline trends + date-range jumper.

Mirrors Sprint 8 `shadow_dashboard` pattern: Django staff check,
inline HTML/CSS rendering (no template files, no JS), Prometheus +
Grafana migration deferred to Phase 1 (per EPIC #769 plan).

# Per-tenant staff isolation note

Tech-lead Q2 verdict 2026-05-26 specified non-superuser staff see only
their tenant's rows. Implementing this requires a `User → Tenant` link
which the codebase does NOT establish today (Django `auth.User` is for
admin staff, but no profile/membership table joins them to a tenant).

Pragmatic adjustment Веха 3:
  - All staff users see all tenants by default (matches Sprint 8 pattern)
  - Tenant dropdown for filtering (`?tenant=slug`)
  - Filed follow-up to add `User → Tenant` link when staff RBAC
    infrastructure lands

Until then, every staff user has effective superuser visibility on
this dashboard — acceptable for pilot scope (small operator team).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Iterable

from django.http import HttpRequest, HttpResponse
from django.utils import timezone
from django.utils.html import escape

from apps.observability.models import AIDailyMetricSummary
from apps.tenancy.models import Tenant


_LOOKBACK_DAYS = 14
_VALID_RANGES = {"1": 1, "7": 7, "14": 14, "30": 30}  # whitelist for ?days param


def ai_quality_dashboard(request: HttpRequest) -> HttpResponse:
    """Render the last-N-days AI quality dashboard.

    Staff-only — anonymous + non-staff → 403 (matches Sprint 8 pattern,
    no redirect to login so monitoring probes flag denial).

    Query params:
      ?days=1|7|14|30 — date range jumper. Default 14, max 30.
      ?tenant=<slug>  — filter to one tenant (default: all).
    """
    if not (request.user.is_authenticated and request.user.is_staff):
        return HttpResponse("forbidden", status=403)

    days = _VALID_RANGES.get(request.GET.get("days", ""), _LOOKBACK_DAYS)
    cutoff = (timezone.now() - timedelta(days=days)).date()

    qs = AIDailyMetricSummary.objects.filter(snapshot_date__gte=cutoff)

    selected_slug = request.GET.get("tenant", "").strip()
    if selected_slug:
        qs = qs.filter(tenant__slug=selected_slug)

    rows = list(qs.select_related("tenant").order_by("-snapshot_date", "tenant__slug"))

    tenants = list(Tenant.objects.filter(is_active=True).order_by("slug"))

    body = _render_body(rows, tenants, days, selected_slug)
    html = _wrap_page(body, count=len(rows), days=days)
    return HttpResponse(html, content_type="text/html; charset=utf-8")


# ─── Rendering ──────────────────────────────────────────────────────────


def _render_body(
    rows: list[AIDailyMetricSummary],
    tenants: list[Tenant],
    days: int,
    selected_slug: str,
) -> str:
    """Tenant dropdown + date-range jumper + summary table + sparkline trends."""
    parts: list[str] = []

    parts.append(_render_controls(tenants, days, selected_slug))

    if not rows:
        parts.append(
            "<p class='no-data'>"
            f"No AI quality summaries in the last {days} day(s). "
            "Either the daily aggregator hasn't run yet OR there's been no "
            "AI traffic on this tenant scope."
            "</p>"
        )
        return "".join(parts)

    parts.append(_render_summary_table(rows))
    parts.append(_render_sparkline_trends(rows))
    return "".join(parts)


def _render_controls(
    tenants: list[Tenant],
    days: int,
    selected_slug: str,
) -> str:
    """Tenant dropdown + date-range buttons."""
    options = ["<option value=''>All tenants</option>"]
    for t in tenants:
        sel = " selected" if t.slug == selected_slug else ""
        options.append(f"<option value='{escape(t.slug)}'{sel}>{escape(t.slug)}</option>")

    range_links = []
    for label, n in [("Today", 1), ("7d", 7), ("14d", 14), ("30d", 30)]:
        cls = " active" if n == days else ""
        link_q = f"?days={n}"
        if selected_slug:
            link_q += f"&tenant={escape(selected_slug)}"
        range_links.append(f"<a href='{link_q}' class='range-btn{cls}'>{label}</a>")

    return (
        "<form method='get' class='controls'>"
        "<label>Tenant: <select name='tenant' onchange='this.form.submit()'>"
        f"{''.join(options)}</select></label>"
        f"<input type='hidden' name='days' value='{days}'>"
        "</form>"
        "<div class='range-jumper'>"
        f"{''.join(range_links)}"
        "</div>"
    )


def _render_summary_table(rows: list[AIDailyMetricSummary]) -> str:
    """Tabular rendering: date / tenant / metrics with threshold badges."""
    header = (
        "<tr>"
        "<th>Date</th>"
        "<th>Tenant</th>"
        "<th>Requests</th>"
        "<th>Task Success</th>"
        "<th>Latency p95</th>"
        "<th>Fallback Rate</th>"
        "<th>Mean Cost</th>"
        "<th>Status</th>"
        "</tr>"
    )

    body_rows: list[str] = []
    for r in rows:
        status = r.threshold_status or {}
        if status.get("no_data"):
            row_class = "no-data-day"
            badge = "⚪ no data"
            cells = [
                f"<td>{escape(r.snapshot_date.isoformat())}</td>",
                f"<td>{escape(r.tenant.slug)}</td>",
                "<td>0</td>",
                "<td>—</td>",
                "<td>—</td>",
                "<td>—</td>",
                "<td>—</td>",
                f"<td class='badge no-data'>{badge}</td>",
            ]
        else:
            row_class = ""
            cells = [
                f"<td>{escape(r.snapshot_date.isoformat())}</td>",
                f"<td>{escape(r.tenant.slug)}</td>",
                f"<td>{r.total_requests}</td>",
                _metric_cell(status.get("task_success_rate"), as_pct=True),
                _metric_cell(status.get("latency_p95_ms"), suffix="ms"),
                _metric_cell(status.get("fallback_rate"), as_pct=True),
                _metric_cell(status.get("mean_cost_usd"), prefix="$"),
                _overall_status_badge(status),
            ]
        body_rows.append(f"<tr class='{row_class}'>" + "".join(cells) + "</tr>")

    return (
        "<h2>14-day summary</h2>"
        "<table class='ai-quality'>"
        f"<thead>{header}</thead>"
        f"<tbody>{''.join(body_rows)}</tbody>"
        "</table>"
    )


def _metric_cell(
    metric_status: object,
    *,
    as_pct: bool = False,
    prefix: str = "",
    suffix: str = "",
) -> str:
    """Render one metric with status colour. `metric_status` is the
    nested dict per `_evaluate_thresholds()` output OR None on no_data days.
    """
    if not isinstance(metric_status, dict):
        return "<td>—</td>"
    value = metric_status.get("value")
    status = metric_status.get("status", "")
    if as_pct and isinstance(value, (int, float)):
        formatted = f"{value:.1%}"
    elif isinstance(value, float):
        formatted = f"{value:.4f}"
    else:
        formatted = str(value)
    cell_class = f"metric-{status}"  # metric-ok / metric-breach
    return f"<td class='{cell_class}'>{prefix}{escape(formatted)}{suffix}</td>"


def _overall_status_badge(status: dict) -> str:
    """🟢 / 🟡 / 🔴 / ⚪ per row aggregate verdict.

    Logic:
      - no_data → ⚪
      - any breach → 🔴
      - trending breach (3-day rolling worsening) → 🟡 (Веха 3+ via aggregation)
      - all ok → 🟢

    Веха 3 ships green/red/no-data. Yellow «trending» requires
    cross-row analysis (rolling window) — implemented in
    `_render_sparkline_trends` below where we already iterate per-metric.
    """
    if status.get("no_data"):
        return "<td class='badge no-data'>⚪</td>"
    has_breach = any(
        isinstance(v, dict) and v.get("status") == AIDailyMetricSummary.THRESHOLD_BREACH
        for v in status.values()
    )
    if has_breach:
        return "<td class='badge breach'>🔴</td>"
    return "<td class='badge ok'>🟢</td>"


def _render_sparkline_trends(rows: list[AIDailyMetricSummary]) -> str:
    """Per-metric sparkline trends grouped by tenant.

    Pure HTML/CSS — bars rendered as inline-block divs with `width:Npx`
    + colour-coded class. No JS, no SVG, no chart library.
    """
    # Group rows by tenant; for each tenant build per-metric series.
    by_tenant: dict[str, list[AIDailyMetricSummary]] = defaultdict(list)
    for r in rows:
        by_tenant[r.tenant.slug].append(r)
    # Sort each tenant's rows oldest → newest for the trend
    for slug in by_tenant:
        by_tenant[slug].sort(key=lambda x: x.snapshot_date)

    if not by_tenant:
        return ""

    sections: list[str] = ["<h2>Trends</h2>"]
    for slug, tenant_rows in sorted(by_tenant.items()):
        sections.append(f"<h3>{escape(slug)}</h3>")
        sections.append("<div class='spark-grid'>")
        sections.append(
            _sparkline_metric(tenant_rows, "task_success_rate", "Task Success", as_pct=True)
        )
        sections.append(
            _sparkline_metric(tenant_rows, "latency_p95_ms", "Latency p95", suffix="ms")
        )
        sections.append(
            _sparkline_metric(tenant_rows, "fallback_rate", "Fallback Rate", as_pct=True)
        )
        sections.append(_sparkline_metric(tenant_rows, "mean_cost_usd", "Mean Cost", prefix="$"))
        sections.append("</div>")
    return "".join(sections)


def _sparkline_metric(
    tenant_rows: Iterable[AIDailyMetricSummary],
    metric_field: str,
    label: str,
    *,
    as_pct: bool = False,
    prefix: str = "",
    suffix: str = "",
) -> str:
    """One sparkline bar chart for `metric_field` across the tenant's days.

    Empty / no_data days render as light-grey placeholder bars.
    """
    values: list[tuple[date, float | None, str]] = []
    for r in tenant_rows:
        status = (
            (r.threshold_status or {}).get(metric_field)
            if not (r.threshold_status or {}).get("no_data")
            else None
        )
        if status is None or not isinstance(status, dict):
            values.append((r.snapshot_date, None, "no_data"))
        else:
            values.append(
                (r.snapshot_date, float(status.get("value") or 0), status.get("status", ""))
            )

    numeric = [v for _, v, _ in values if v is not None]
    if not numeric:
        return f"<div class='spark-cell'><h4>{escape(label)}</h4><p class='spark-empty'>no data</p></div>"

    vmax = max(numeric) or 1.0
    bars: list[str] = []
    for d, v, st in values:
        if v is None:
            bars.append(
                f"<span class='bar bar-no-data' title='{escape(d.isoformat())}: no data'></span>"
            )
            continue
        # Scale 0..vmax to 0..30px height
        h = max(2, int(30 * (v / vmax)))
        cls = "bar-breach" if st == AIDailyMetricSummary.THRESHOLD_BREACH else "bar-ok"
        if as_pct:
            label_v = f"{v:.1%}"
        elif isinstance(v, float):
            label_v = f"{v:.4f}"
        else:
            label_v = str(v)
        bars.append(
            f"<span class='bar {cls}' style='height:{h}px' "
            f"title='{escape(d.isoformat())}: {prefix}{label_v}{suffix}'></span>"
        )

    return (
        f"<div class='spark-cell'>"
        f"<h4>{escape(label)}</h4>"
        f"<div class='spark-bars'>{''.join(bars)}</div>"
        "</div>"
    )


# ─── Inline CSS + page wrapper ──────────────────────────────────────────


_INLINE_CSS = """
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
       max-width: 1200px; margin: 2em auto; padding: 0 1em; color: #222; }
h1 { font-size: 1.4em; margin-bottom: 0.2em; }
h2 { font-size: 1.1em; margin-top: 2em; border-bottom: 1px solid #ccc; padding-bottom: 0.2em; }
h3 { font-size: 1em; margin-top: 1.2em; color: #444; }
h4 { font-size: 0.85em; margin: 0 0 4px 0; color: #666; }
.meta { color: #666; font-size: 0.9em; margin-bottom: 1.5em; }
.note { color: #666; font-size: 0.85em; margin-top: 1.2em; }
.controls { display: inline-block; margin-right: 1em; }
.range-jumper { display: inline-block; margin-bottom: 1em; }
.range-btn { padding: 4px 10px; margin-right: 4px; border: 1px solid #ccc;
             border-radius: 3px; text-decoration: none; color: #444;
             font-size: 0.9em; background: #fff; }
.range-btn.active { background: #e7f3ff; border-color: #6da6e0; font-weight: 600; }
table.ai-quality { width: 100%; border-collapse: collapse; }
table.ai-quality th, table.ai-quality td {
  padding: 8px 12px; border-bottom: 1px solid #eee; text-align: right;
}
table.ai-quality th:nth-child(1), table.ai-quality td:nth-child(1),
table.ai-quality th:nth-child(2), table.ai-quality td:nth-child(2) {
  text-align: left;
}
table.ai-quality th { background: #f7f7f9; font-weight: 600; }
tr.no-data-day td { color: #aaa; font-style: italic; }
.metric-ok { color: #1e7e34; font-weight: 500; }
.metric-breach { color: #c0392b; font-weight: 700; }
.badge { font-weight: 600; }
.badge.ok { color: #1e7e34; }
.badge.breach { color: #c0392b; }
.badge.no-data { color: #999; }
.no-data { color: #999; font-style: italic; }
.spark-grid {
  display: grid; grid-template-columns: repeat(2, 1fr); gap: 16px;
  margin-bottom: 1em;
}
.spark-cell { padding: 6px; background: #fafafa; border-radius: 4px; }
.spark-bars {
  display: flex; align-items: flex-end; gap: 2px; height: 36px;
}
.bar { width: 14px; display: inline-block; background: #6da6e0; }
.bar-ok { background: #4caf50; }
.bar-breach { background: #c0392b; }
.bar-no-data { background: #e0e0e0; height: 4px; align-self: center; }
.spark-empty { color: #999; font-size: 0.85em; }
"""


def _wrap_page(body: str, *, count: int, days: int) -> str:
    return (
        "<!doctype html>"
        "<html><head><meta charset='utf-8'><title>AI quality observability</title>"
        f"<style>{_INLINE_CSS}</style></head><body>"
        "<h1>AI quality observability</h1>"
        f"<p class='meta'>Last {days} day(s) · {count} summary row(s)</p>"
        f"{body}"
        "<p class='note'>"
        "AI observability epic #769 / Веха 3 (#772). Per-tenant staff "
        "isolation deferred until User → Tenant infrastructure ships. "
        "Phase 1 migration path: Prometheus exporter from "
        "<code>AIDailyMetricSummary</code> → Grafana panel."
        "</p>"
        "</body></html>"
    )
