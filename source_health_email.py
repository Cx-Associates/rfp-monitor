"""
Monthly source-health email summary and HTML rendering.

This module is deliberately side-effect free: it aggregates already-loaded
rows and returns a subject, plain-text body, or HTML body. Supabase loading,
SendGrid delivery, and first-Monday scheduling live in separate integration
code so the template can be previewed and tested without external effects.

Raw diagnostic messages and GitHub identifiers are never rendered because the
email is designed as a concise review notification, not a debug log.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from datetime import timedelta
from html import escape
from typing import Iterable, Optional

import config
from source_health_report import (
    EASTERN,
    STATUS_ERROR,
    STATUS_OK,
    STATUS_OTHER,
    STATUS_PARTIAL,
    STATUS_SKIPPED,
    STATUS_ZERO,
    ZERO_INVESTIGATION_THRESHOLD,
    build_source_observations,
    find_incomplete_runs,
    parse_timestamp,
    summarize_sources,
)


STATUS_LABELS = {
    STATUS_ERROR: "Error",
    STATUS_PARTIAL: "Partial Results",
    STATUS_OK: "Candidates Returned",
    STATUS_ZERO: "No Results",
    STATUS_SKIPPED: "Expected JS Skip",
    STATUS_OTHER: "Other",
}
STATUS_COLORS = {
    STATUS_ERROR: "#c62828",
    STATUS_PARTIAL: "#a96600",
    STATUS_OK: "#267a4b",
    STATUS_ZERO: "#d47a00",
    STATUS_SKIPPED: "#687b91",
    STATUS_OTHER: "#666666",
}


@dataclass(frozen=True)
class MonthlyAttentionSource:
    """Sanitized information for one source needing human review."""

    source_name: str
    source_group: str
    status: str
    reason: str


@dataclass(frozen=True)
class MonthlySourceHealthSummary:
    """Email-ready metrics for one Eastern calendar month."""

    month_label: str
    period_label: str
    distinct_runs: int
    monitor_runs: int
    sources_tracked: int
    need_investigation: int
    current_errors: int
    current_partial_results: int
    runs_with_missing_data: int
    attention_sources: tuple[MonthlyAttentionSource, ...]


def _merge_period_and_history_summaries(
    period_summaries,
    history_summaries,
) -> list:
    """
    Keep period counts while taking the streak/current state from history.

    This mirrors the dashboard rule: July totals show July activity, while a
    no-results streak that began in June continues into July. Both inputs are
    derived from rows no later than the report's exclusive period end.
    """
    history_by_source = {
        (summary.source_group, summary.source_name): summary
        for summary in history_summaries
    }
    merged = []

    for period_summary in period_summaries:
        key = (period_summary.source_group, period_summary.source_name)
        history_summary = history_by_source.get(key, period_summary)
        merged.append(
            replace(
                period_summary,
                latest_observed_at=history_summary.latest_observed_at,
                latest_status=history_summary.latest_status,
                zero_streak=history_summary.zero_streak,
                needs_investigation=history_summary.needs_investigation,
                attention_reason=history_summary.attention_reason,
            )
        )

    return sorted(
        merged,
        key=lambda item: (
            not item.needs_investigation,
            -item.zero_streak,
            item.source_group.lower(),
            item.source_name.lower(),
        ),
    )


def build_monthly_source_health_summary(
    runs: Iterable[dict],
    records: Iterable[dict],
    *,
    period_start,
    period_end,
    history_runs: Optional[Iterable[dict]] = None,
    history_records: Optional[Iterable[dict]] = None,
    zero_threshold: int = ZERO_INVESTIGATION_THRESHOLD,
) -> MonthlySourceHealthSummary:
    """
    Build a monthly notification summary from parent/detail rows.

    ``runs`` and ``records`` contain only [period_start, period_end). Optional
    history rows include earlier data through period_end for the 12-run rule.
    The period end is exclusive, matching database queries.
    """
    runs = list(runs)
    records = list(records)
    if (history_runs is None) != (history_records is None):
        raise ValueError(
            "history_runs and history_records must be provided together"
        )
    history_runs = list(history_runs) if history_runs is not None else runs
    history_records = (
        list(history_records) if history_records is not None else records
    )

    start_eastern = parse_timestamp(period_start).astimezone(EASTERN)
    end_eastern = parse_timestamp(period_end).astimezone(EASTERN)
    if start_eastern >= end_eastern:
        raise ValueError("period_start must be earlier than period_end")
    month_label, period_label = _normalized_period_label(
        period_start,
        period_end,
    )

    period_observations = build_source_observations(runs, records)
    history_observations = build_source_observations(
        history_runs,
        history_records,
    )
    period_summaries = summarize_sources(
        period_observations,
        zero_threshold=zero_threshold,
    )
    history_summaries = summarize_sources(
        history_observations,
        zero_threshold=zero_threshold,
    )
    summaries = _merge_period_and_history_summaries(
        period_summaries,
        history_summaries,
    )

    attention_sources = tuple(
        MonthlyAttentionSource(
            source_name=summary.source_name,
            source_group=summary.source_group,
            status=summary.latest_status,
            reason=summary.attention_reason,
        )
        for summary in summaries
        if summary.needs_investigation
    )
    workflow_keys = {
        observation.workflow_key for observation in period_observations
    }
    latest_status_counts = Counter(
        summary.latest_status for summary in summaries
    )

    return MonthlySourceHealthSummary(
        month_label=month_label,
        period_label=period_label,
        distinct_runs=len(workflow_keys),
        monitor_runs=len(runs),
        sources_tracked=len(summaries),
        need_investigation=len(attention_sources),
        current_errors=latest_status_counts[STATUS_ERROR],
        current_partial_results=latest_status_counts[STATUS_PARTIAL],
        runs_with_missing_data=len(find_incomplete_runs(runs, records)),
        attention_sources=attention_sources,
    )


def _format_day_without_leading_zero(value) -> str:
    """Format a portable month/day/year string on Windows and Linux."""
    return value.strftime("%B %d, %Y").replace(" 0", " ")


def _normalized_period_label(period_start, period_end) -> tuple[str, str]:
    """Return month and inclusive date-range labels in Eastern Time."""
    start_eastern = parse_timestamp(period_start).astimezone(EASTERN)
    end_eastern = parse_timestamp(period_end).astimezone(EASTERN)
    inclusive_end = end_eastern - timedelta(microseconds=1)
    return (
        start_eastern.strftime("%B %Y"),
        f"{_format_day_without_leading_zero(start_eastern)} through "
        f"{_format_day_without_leading_zero(inclusive_end)} (Eastern Time)",
    )


def monthly_source_health_subject(
    summary: MonthlySourceHealthSummary,
) -> str:
    """Return a factual subject that does not label investigation as failure."""
    source_word = "source" if summary.need_investigation == 1 else "sources"
    return (
        f"[CxA RFP Monitor] Monthly Source Health - "
        f"{summary.month_label}: {summary.need_investigation} "
        f"{source_word} to review"
    )


def render_monthly_source_health_text(
    summary: MonthlySourceHealthSummary,
    *,
    dashboard_url: str = config.SOURCE_HEALTH_DASHBOARD_URL,
) -> str:
    """Render a useful plain-text alternative for email clients."""
    lines = [
        f"CxA RFP Monitor Source Health - {summary.month_label}",
        summary.period_label,
        "",
        (
            f"{summary.need_investigation} sources need review. "
            "An investigation signal is not automatically a confirmed failure."
        ),
        f"Distinct runs: {summary.distinct_runs}",
        f"Monitor runs: {summary.monitor_runs}",
        f"Sources tracked: {summary.sources_tracked}",
        f"Current errors: {summary.current_errors}",
        f"Current partial results: {summary.current_partial_results}",
        f"Runs with missing data: {summary.runs_with_missing_data}",
        "",
    ]
    if summary.attention_sources:
        lines.append("Sources requiring investigation:")
        for item in summary.attention_sources:
            status = STATUS_LABELS.get(item.status, item.status.title())
            lines.append(
                f"- {item.source_name} ({item.source_group}): "
                f"{status}. {item.reason}"
            )
        lines.append("")
    lines.extend(
        [
            f"Review the full dashboard: {dashboard_url}",
            "",
            "Times are displayed in Eastern Time; Supabase stores UTC.",
        ]
    )
    return "\n".join(lines)


def _esc(value) -> str:
    return escape(str(value if value is not None else ""), quote=True)


def _status_badge(status: str) -> str:
    """Render a compact, inline-styled badge for broad email-client support."""
    label = STATUS_LABELS.get(status, status.title())
    color = STATUS_COLORS.get(status, STATUS_COLORS[STATUS_OTHER])
    return (
        f'<span style="display:inline-block;background:{color};color:#ffffff;'
        "border-radius:12px;padding:4px 9px;font-size:10px;font-weight:700;"
        f'white-space:nowrap;">{_esc(label)}</span>'
    )


def render_monthly_source_health_html(
    summary: MonthlySourceHealthSummary,
    *,
    dashboard_url: str = config.SOURCE_HEALTH_DASHBOARD_URL,
) -> str:
    """
    Render a compact table-based HTML email suitable for Outlook/SendGrid.

    Inline styles and table layout are intentional because many email clients
    strip external CSS and have limited support for modern page layout.
    """
    if summary.need_investigation:
        source_word = (
            "source" if summary.need_investigation == 1 else "sources"
        )
        alert_background = "#fff4df"
        alert_border = "#d47a00"
        alert_heading = (
            f"{summary.need_investigation} {source_word} need review"
        )
        alert_detail = (
            "These are investigation signals, not automatic confirmation "
            "that a source failed."
        )
    else:
        alert_background = "#edf8f1"
        alert_border = "#267a4b"
        alert_heading = "No sources currently meet investigation criteria"
        alert_detail = (
            "The dashboard remains available for routine monthly review."
        )

    attention_rows = []
    for item in summary.attention_sources:
        attention_rows.append(
            "<tr>"
            '<td style="padding:11px 10px;border-top:1px solid #e1e6eb;'
            'vertical-align:top;">'
            f"<strong>{_esc(item.source_name)}</strong><br>"
            '<span style="color:#687b91;font-size:11px;">'
            f"{_esc(item.source_group)}</span></td>"
            '<td style="padding:11px 10px;border-top:1px solid #e1e6eb;'
            f'vertical-align:top;">{_status_badge(item.status)}</td>'
            '<td style="padding:11px 10px;border-top:1px solid #e1e6eb;'
            'vertical-align:top;line-height:1.4;">'
            f"{_esc(item.reason)}</td>"
            "</tr>"
        )

    if attention_rows:
        attention_section = (
            '<h2 style="font-size:17px;color:#1a1a2e;margin:24px 0 10px;">'
            "Sources Requiring Investigation</h2>"
            '<table role="presentation" width="100%" cellspacing="0" '
            'cellpadding="0" style="border-collapse:collapse;border:1px solid '
            '#d9e0e7;font-size:12px;">'
            '<tr style="background:#edf2f6;color:#3d5368;">'
            '<th align="left" style="padding:9px 10px;">Source</th>'
            '<th align="left" style="padding:9px 10px;">Latest Result</th>'
            '<th align="left" style="padding:9px 10px;">Why Review</th>'
            "</tr>"
            + "".join(attention_rows)
            + "</table>"
        )
    else:
        attention_section = ""

    # Four equal cells provide a quick scan without repeating every metric in
    # the full dashboard. Current error/partial counts remain in the note below.
    metrics = (
        ("Distinct Runs", summary.distinct_runs),
        ("Sources Tracked", summary.sources_tracked),
        ("Need Investigation", summary.need_investigation),
        ("Runs With Missing Data", summary.runs_with_missing_data),
    )
    metric_cells = "".join(
        '<td width="25%" valign="top" style="padding:0 5px;">'
        '<div style="border:1px solid #dde3e9;border-radius:7px;'
        'padding:12px 10px;background:#ffffff;min-height:58px;">'
        '<div style="font-size:10px;font-weight:700;letter-spacing:.3px;'
        f'color:#687b91;text-transform:uppercase;">{_esc(label)}</div>'
        f'<div style="font-size:23px;font-weight:700;color:#1a1a2e;'
        f'margin-top:5px;">{value}</div></div></td>'
        for label, value in metrics
    )

    safe_url = _esc(dashboard_url)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1.0">
  <title>{_esc(monthly_source_health_subject(summary))}</title>
</head>
<body style="margin:0;padding:0;background:#f0f2f5;color:#25313d;font-family:Arial,sans-serif;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f0f2f5;">
    <tr>
      <td align="center" style="padding:24px 12px;">
        <table role="presentation" width="720" cellspacing="0" cellpadding="0" style="width:100%;max-width:720px;background:#ffffff;border-collapse:separate;border-spacing:0;border-radius:8px;overflow:hidden;">
          <tr>
            <td style="background:#1a1a2e;color:#ffffff;padding:22px 26px;">
              <h1 style="font-size:21px;margin:0;">CxA RFP Monitor Source Health</h1>
              <p style="font-size:13px;margin:6px 0 0;opacity:.8;">Monthly review &mdash; {_esc(summary.month_label)}</p>
            </td>
          </tr>
          <tr>
            <td style="padding:24px 26px;">
              <p style="font-size:14px;line-height:1.5;margin:0 0 5px;">
                The {_esc(summary.month_label)} source-health dashboard is ready for review.
              </p>
              <p style="font-size:12px;color:#687b91;margin:0 0 18px;">
                Reporting period: {_esc(summary.period_label)}
              </p>

              <div style="background:{alert_background};border-left:4px solid {alert_border};padding:12px 14px;margin-bottom:20px;">
                <strong style="font-size:14px;">{_esc(alert_heading)}</strong>
                <div style="font-size:12px;line-height:1.45;margin-top:3px;">{_esc(alert_detail)}</div>
              </div>

              <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="table-layout:fixed;margin:0 -5px 18px;">
                <tr>{metric_cells}</tr>
              </table>

              <p style="font-size:12px;line-height:1.5;color:#526476;margin:0;">
                Latest source results include <strong>{summary.current_errors} errors</strong>
                and <strong>{summary.current_partial_results} partial results</strong>.
                “No Results” can be normal and appears for investigation only after
                {ZERO_INVESTIGATION_THRESHOLD} consecutive distinct live runs.
              </p>

              {attention_section}

              <div style="text-align:center;margin:26px 0 10px;">
                <a href="{safe_url}" style="display:inline-block;background:#0066cc;color:#ffffff;text-decoration:none;padding:11px 18px;border-radius:5px;font-size:13px;font-weight:700;">Review Source Health Dashboard</a>
              </div>
            </td>
          </tr>
          <tr>
            <td style="background:#f7f9fb;border-top:1px solid #e1e6eb;color:#687b91;font-size:11px;line-height:1.45;padding:14px 26px;">
              Times are displayed in America/New_York as EST or EDT. Supabase
              timestamps remain stored in UTC. This email excludes raw error
              messages, workflow identifiers, credentials, and request details.
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""
