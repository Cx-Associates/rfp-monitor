"""
Sanitized static source-health dashboard renderer.

This module accepts already-loaded Supabase rows and produces static HTML.
It intentionally omits raw exception messages, GitHub run IDs, credentials,
and request details so the result is suitable for the existing public Pages
deployment.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import replace
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Iterable, Optional

from source_health import HEALTH_WARN_TOTAL_ZERO
from source_health_report import (
    STATUS_ERROR,
    STATUS_OK,
    STATUS_OTHER,
    STATUS_PARTIAL,
    STATUS_SKIPPED,
    STATUS_ZERO,
    ZERO_INVESTIGATION_THRESHOLD,
    build_source_observations,
    find_incomplete_runs,
    format_eastern,
    summarize_sources,
)


# Dashboard labels and colors deliberately describe observations rather
# than declaring a source healthy/unhealthy. A zero result may be valid.
STATUS_LABELS = {
    STATUS_ERROR: "Error",
    STATUS_PARTIAL: "Partial Results",
    STATUS_OK: "Candidates",
    # Plain user-facing label: the underlying fetch/error distinction is
    # intentionally explained in prose rather than compressed into jargon.
    STATUS_ZERO: "No Results",
    STATUS_SKIPPED: "Skipped",
    STATUS_OTHER: "Other",
}

STATUS_CSS = {
    STATUS_ERROR: "status-error",
    STATUS_PARTIAL: "status-partial",
    STATUS_OK: "status-ok",
    STATUS_ZERO: "status-zero",
    STATUS_SKIPPED: "status-skipped",
    STATUS_OTHER: "status-other",
}


def _esc(value) -> str:
    return escape(str(value if value is not None else ""), quote=True)


def _status_badge(status: str) -> str:
    css_class = STATUS_CSS.get(status, STATUS_CSS[STATUS_OTHER])
    label = STATUS_LABELS.get(status, status.title())
    return (
        f'<span class="status-badge {css_class}">{_esc(label)}</span>'
    )


def _info_icon(explanation: str) -> str:
    """Render the same compact hover-help pattern used by the main dashboards."""
    safe_explanation = _esc(explanation)
    return (
        f'<span class="info-icon" tabindex="0" '
        f'title="{safe_explanation}" aria-label="{safe_explanation}">i</span>'
    )


def _period_label(period_start, period_end) -> str:
    if not period_start or not period_end:
        return "Recent persisted history"

    start_text = format_eastern(period_start, include_timezone=False)
    end_text = format_eastern(period_end, include_timezone=False)
    start_date = start_text.split(" at ", 1)[0]
    end_date = end_text.split(" at ", 1)[0]
    return f"{start_date} through {end_date} (Eastern Time)"


def _history_markup(source_observations) -> str:
    # The compact strip shows exactly the window used by the investigation
    # rule. Tooltips contain sanitized status/time only, never raw messages.
    latest_twelve = sorted(
        source_observations,
        key=lambda item: item.observed_at,
    )[-ZERO_INVESTIGATION_THRESHOLD:]
    cells = []
    for observation in latest_twelve:
        css_class = STATUS_CSS.get(
            observation.status,
            STATUS_CSS[STATUS_OTHER],
        )
        label = STATUS_LABELS.get(
            observation.status,
            observation.status.title(),
        )
        tooltip = (
            f"{format_eastern(observation.observed_at)}: {label}"
        )
        cells.append(
            f'<span class="history-cell {css_class}" '
            f'title="{_esc(tooltip)}" aria-label="{_esc(tooltip)}"></span>'
        )
    return '<span class="history-strip">' + "".join(cells) + "</span>"


def render_source_health_dashboard(
    runs: Iterable[dict],
    records: Iterable[dict],
    *,
    period_start=None,
    period_end=None,
    history_runs: Optional[Iterable[dict]] = None,
    history_records: Optional[Iterable[dict]] = None,
    generated_at: Optional[datetime] = None,
    zero_threshold: int = ZERO_INVESTIGATION_THRESHOLD,
) -> str:
    """
    Render a sanitized static dashboard from source-health parent/detail rows.

    The returned HTML does not include raw messages or GitHub run IDs.

    ``runs`` and ``records`` define the displayed reporting period.
    Optional history rows allow the consecutive no-results rule and
    12-run strips to cross calendar-month boundaries without inflating
    the monthly cards and result totals.
    """
    # Materialize iterables once because the same rows feed aggregation,
    # completeness checks, group-alert counts, and summary cards.
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
    generated_at = generated_at or datetime.now(timezone.utc)

    observations = build_source_observations(runs, records)
    history_observations = build_source_observations(
        history_runs,
        history_records,
    )
    period_summaries = summarize_sources(
        observations,
        zero_threshold=zero_threshold,
    )
    history_summary_by_source = {
        (summary.source_group, summary.source_name): summary
        for summary in summarize_sources(
            history_observations,
            zero_threshold=zero_threshold,
        )
    }

    # Counts remain period-only, but the current status, investigation
    # decision, and consecutive streak come from history through the
    # report end. ``replace`` preserves the immutable summary model.
    source_summaries = []
    for period_summary in period_summaries:
        key = (period_summary.source_group, period_summary.source_name)
        history_summary = history_summary_by_source.get(
            key,
            period_summary,
        )
        source_summaries.append(
            replace(
                period_summary,
                latest_observed_at=history_summary.latest_observed_at,
                latest_status=history_summary.latest_status,
                zero_streak=history_summary.zero_streak,
                needs_investigation=history_summary.needs_investigation,
                attention_reason=history_summary.attention_reason,
            )
        )
    source_summaries.sort(
        key=lambda item: (
            not item.needs_investigation,
            -item.zero_streak,
            item.source_group.lower(),
            item.source_name.lower(),
        )
    )
    incomplete_runs = find_incomplete_runs(runs, records)

    observations_by_source = defaultdict(list)
    for observation in history_observations:
        observations_by_source[
            (observation.source_group, observation.source_name)
        ].append(observation)

    workflow_keys = {
        observation.workflow_key for observation in observations
    }
    status_counts = Counter(
        observation.status for observation in observations
    )
    investigation_sources = [
        summary for summary in source_summaries if summary.needs_investigation
    ]
    # A scheduled workflow creates one EM&V parent and one commissioning
    # parent with the same github_run_id. Collapse their duplicate
    # HEALTH_WARN_TOTAL_ZERO rows so the dashboard reports live workflow
    # events rather than double-counting monitor executions.
    runs_by_id = {str(run.get("id") or ""): run for run in runs}
    group_zero_keys = set()
    for record in records:
        if record.get("code") != HEALTH_WARN_TOTAL_ZERO:
            continue
        run_id = str(record.get("run_id") or "")
        run = runs_by_id.get(run_id, {})
        github_run_id = str(run.get("github_run_id") or "").strip()
        workflow_key = (
            f"github:{github_run_id}" if github_run_id else f"run:{run_id}"
        )
        group_name = str(
            record.get("source_group")
            or record.get("source_name")
            or "Unknown group"
        )
        group_zero_keys.add((workflow_key, group_name))
    group_zero_alerts = len(group_zero_keys)

    # Public rows use only aggregate fields from SourceSummary. Raw record
    # messages and GitHub run IDs are intentionally never interpolated.
    attention_rows = []
    for summary in investigation_sources:
        history = _history_markup(
            observations_by_source[
                (summary.source_group, summary.source_name)
            ]
        )
        attention_rows.append(
            f"""
            <tr>
              <td><strong>{_esc(summary.source_name)}</strong><br>
                  <span class="muted">{_esc(summary.source_group)}</span></td>
              <td class="investigation-status">{_status_badge(summary.latest_status)}</td>
              <td class="no-results-count">{summary.zero_streak}</td>
              <td>{_esc(summary.attention_reason)}</td>
              <td>{history}</td>
              <td>{_esc(format_eastern(summary.latest_observed_at))}</td>
            </tr>
            """
        )

    if attention_rows:
        attention_content = f"""
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Source</th>
                <th>Latest Status</th>
                <th class="no-results-count">Number Of Runs With No Results {_info_icon("Current consecutive live-run count in which the source returned no raw items. A run returning candidates resets this count.")}</th>
                <th>Reason</th>
                <th>Recent Live Runs {_info_icon("One square per distinct workflow, ordered from oldest on the left to newest on the right. Colors match the legend.")}</th>
                <th>Latest Observation</th>
              </tr>
            </thead>
            <tbody>{''.join(attention_rows)}</tbody>
          </table>
        </div>
        """
    else:
        attention_content = """
        <div class="empty-state">
          No source currently meets the investigation criteria.
        </div>
        """

    all_source_rows = []
    for summary in source_summaries:
        history = _history_markup(
            observations_by_source[
                (summary.source_group, summary.source_name)
            ]
        )
        all_source_rows.append(
            f"""
            <tr>
              <td><strong>{_esc(summary.source_name)}</strong><br>
                  <span class="muted">{_esc(summary.source_group)}</span></td>
              <td>{_status_badge(summary.latest_status)}</td>
              <td class="number">{summary.observations}</td>
              <td class="number">{summary.candidate_observations}</td>
              <td class="no-results-count">{summary.zero_streak}</td>
              <td class="number">{summary.error_observations}</td>
              <td class="number">{summary.partial_observations}</td>
              <td>{history}</td>
              <td>{_esc(format_eastern(summary.latest_observed_at))}</td>
            </tr>
            """
        )

    if all_source_rows:
        all_sources_content = f"""
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Source</th>
                <th>Latest Status</th>
                <th>Runs</th>
                <th>Candidate Runs {_info_icon("Runs in which the source returned one or more raw items before relevance scoring. These are not necessarily opportunities delivered by email.")}</th>
                <th class="no-results-count">Number Of Runs With No Results {_info_icon("Current consecutive live-run count in which the source returned no raw items. A run returning candidates resets this count.")}</th>
                <th>Errors</th>
                <th>Partial</th>
                <th>Recent Live Runs {_info_icon("One square per distinct workflow, ordered from oldest on the left to newest on the right. Colors match the legend.")}</th>
                <th>Latest Observation</th>
              </tr>
            </thead>
            <tbody>{''.join(all_source_rows)}</tbody>
          </table>
        </div>
        """
    else:
        all_sources_content = """
        <div class="empty-state">
          No persisted source-health records were available for this period.
        </div>
        """

    incomplete_rows = []
    for item in incomplete_runs:
        completed = item.get("run_completed_at")
        completed_text = (
            format_eastern(completed) if completed else "Unknown"
        )
        incomplete_rows.append(
            f"""
            <tr>
              <td>{_esc(item.get("monitor_type") or "Unknown")}</td>
              <td>{_esc(completed_text)}</td>
              <td class="center">{item["expected_records"]}</td>
              <td class="center">{item["actual_records"]}</td>
            </tr>
            """
        )

    if incomplete_rows:
        completeness_content = f"""
        <div class="notice notice-warning">
          Some monitor runs are missing one or more saved source-check results.
          Totals for those runs may therefore be incomplete.
        </div>
        <div class="table-wrap compact-table">
          <table>
            <thead>
              <tr>
                <th>Monitor</th>
                <th>Completed</th>
                <th class="center">Source Checks Expected</th>
                <th class="center">Source Checks Saved</th>
              </tr>
            </thead>
            <tbody>{''.join(incomplete_rows)}</tbody>
          </table>
        </div>
        """
    else:
        completeness_content = """
        <div class="notice notice-ok">
          All source-check results were saved completely for this reporting period.
        </div>
        """

    generated_text = format_eastern(generated_at)
    report_period = _period_label(period_start, period_end)

    # Build complete sentences here rather than exposing implementation
    # terminology such as raw code counters in the rendered dashboard.
    group_run_wording = (
        "live run had" if group_zero_alerts == 1 else "live runs had"
    )
    group_alert_sentence = (
        f"{group_zero_alerts} distinct {group_run_wording} an entire "
        "source group return zero candidates during this period."
    )
    observation_total = sum(status_counts.values())
    observation_sentence = (
        f"Across {observation_total} consolidated source observations, "
        f"{status_counts[STATUS_OK]} returned candidates, "
        f"{status_counts[STATUS_ZERO]} returned no results, "
        f"{status_counts[STATUS_PARTIAL]} returned partial results because "
        "some requests or parsing steps failed, "
        f"{status_counts[STATUS_ERROR]} encountered an error, and "
        f"{status_counts[STATUS_SKIPPED]} were expected JS-rendered skips."
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>CxA RFP Monitor Source Health</title>
  <style>
    :root {{
      /* Match the dark header used by the existing opportunity dashboards. */
      --navy:#1a1a2e;
      --card:#ffffff;
      --border:#dce3ea;
      --text:#1f2933;
      --muted:#66788a;
      --ok:#247447;
      --zero:#d17a00;
      --partial:#9b5b00;
      --error:#b42318;
      --skipped:#65758b;
      --other:#56616f;
    }}
    * {{ box-sizing:border-box; }}
    body {{
      margin:0;
      background:#f0f2f5;
      color:var(--text);
      font-family:Arial,Helvetica,sans-serif;
      line-height:1.45;
    }}
    header {{
      background:var(--navy);
      color:#fff;
      padding:24px;
    }}
    header .inner, .dashboard-nav .inner, main, footer {{
      max-width:1380px;
      margin:0 auto;
    }}
    h1 {{ margin:0 0 6px; font-size:28px; }}
    header p {{ margin:3px 0; opacity:.9; }}
    /* Mirror the white navigation strip and button treatment used by
       the EM&V and Commissioning opportunity dashboards. */
    .dashboard-nav {{
      background:#fff;
      border-bottom:1px solid var(--border);
      padding:10px 24px;
    }}
    .dashboard-nav a {{
      border:1px solid var(--border);
      border-radius:5px;
      color:var(--navy);
      display:inline-block;
      font-size:13px;
      margin-right:7px;
      padding:7px 11px;
      text-decoration:none;
    }}
    .dashboard-nav a.active {{
      background:var(--navy);
      border-color:var(--navy);
      color:#fff;
      font-weight:700;
    }}
    main {{ padding:24px; }}
    .cards {{
      display:grid;
      grid-template-columns:repeat(auto-fit,minmax(175px,1fr));
      gap:14px;
      margin-bottom:24px;
    }}
    .card {{
      background:var(--card);
      border:1px solid var(--border);
      border-radius:8px;
      padding:16px;
      box-shadow:0 2px 6px rgba(15,35,55,.05);
    }}
    .card .label {{
      color:var(--muted);
      font-size:12px;
      font-weight:700;
      letter-spacing:.04em;
      text-transform:none;
    }}
    .card .value {{ font-size:28px; font-weight:700; margin-top:4px; }}
    section {{
      background:var(--card);
      border:1px solid var(--border);
      border-radius:8px;
      margin:0 0 22px;
      padding:20px;
      box-shadow:0 2px 6px rgba(15,35,55,.04);
    }}
    h2 {{ margin:0 0 6px; font-size:20px; color:var(--navy); }}
    .section-note {{ color:var(--muted); font-size:13px; margin:0 0 16px; }}
    .table-wrap {{ overflow-x:auto; }}
    table {{ width:100%; border-collapse:collapse; font-size:13px; }}
    th {{
      background:#eef3f7;
      color:#33475b;
      font-size:11px;
      letter-spacing:.03em;
      text-align:left;
      text-transform:none;
    }}
    th, td {{
      border-bottom:1px solid var(--border);
      padding:10px 9px;
      vertical-align:top;
    }}
    tbody tr:hover {{ background:#f8fafc; }}
    .number {{ text-align:right; font-variant-numeric:tabular-nums; }}
    /* Persistence comparison values are centered for easier scanning. */
    .center {{ text-align:center; font-variant-numeric:tabular-nums; }}
    /* No-result streak counts are centered under their long heading
       so the values scan consistently in both source tables. */
    .no-results-count {{
      text-align:center;
      font-variant-numeric:tabular-nums;
    }}
    /* Investigation badges use one width so ERROR, NO RESULTS, and
       PARTIAL RESULTS have equal visual weight in the compact table. */
    .investigation-status {{ text-align:center; }}
    .investigation-status .status-badge {{
      box-sizing:border-box;
      min-width:108px;
      text-align:center;
      white-space:nowrap;
    }}
    .muted {{ color:var(--muted); font-size:12px; }}
    /* Information icons use the browser's native hover/focus tooltip.
       Static escaped text keeps them lightweight and safe for public Pages. */
    .info-icon {{
      align-items:center;
      background:#e8edf3;
      border:1px solid #c8d2dc;
      border-radius:50%;
      color:#526476;
      cursor:help;
      display:inline-flex;
      font-size:9px;
      font-style:normal;
      font-weight:700;
      height:15px;
      justify-content:center;
      margin-left:4px;
      vertical-align:1px;
      width:15px;
    }}
    .info-icon:focus {{ outline:2px solid #4b79a1; outline-offset:2px; }}
    .status-badge {{
      border-radius:999px;
      color:#fff;
      display:inline-block;
      font-size:10px;
      font-weight:700;
      min-width:63px;
      padding:3px 8px;
      text-align:center;
      text-transform:uppercase;
    }}
    .status-error {{ background:var(--error); }}
    .status-partial {{ background:var(--partial); }}
    .status-ok {{ background:var(--ok); }}
    .status-zero {{ background:var(--zero); }}
    .status-skipped {{ background:var(--skipped); }}
    .status-other {{ background:var(--other); }}
    .history-strip {{
      display:inline-flex;
      gap:3px;
      min-width:130px;
      padding-top:3px;
    }}
    .history-cell {{
      border-radius:2px;
      display:inline-block;
      height:13px;
      width:8px;
    }}
    .notice {{
      border-radius:6px;
      font-size:13px;
      margin-bottom:14px;
      padding:11px 13px;
    }}
    .notice-ok {{ background:#e9f6ee; border:1px solid #b8dec5; }}
    .notice-warning {{ background:#fff4dd; border:1px solid #e8c780; }}
    .period-summary {{
      background:#f7f9fb;
      border:1px solid var(--border);
      border-radius:6px;
      margin-top:14px;
      padding:12px 14px;
    }}
    .period-summary p {{ margin:4px 0; font-size:13px; }}
    .empty-state {{
      background:#eef7f1;
      border:1px solid #bedbc7;
      border-radius:6px;
      padding:16px;
    }}
    .legend {{
      display:flex;
      flex-wrap:wrap;
      gap:12px;
      font-size:12px;
      margin-top:14px;
    }}
    .legend span {{ display:inline-flex; gap:5px; align-items:center; }}
    .legend i {{ height:10px; width:10px; border-radius:2px; }}
    footer {{
      color:var(--muted);
      font-size:12px;
      padding:0 24px 30px;
    }}
    @media (max-width:700px) {{
      header {{ padding:22px 16px; }}
      main {{ padding:16px; }}
      section {{ padding:15px; }}
    }}
  </style>
</head>
<body>
  <header>
    <div class="inner">
      <h1>CxA RFP Monitor Source Health</h1>
      <p>{_esc(report_period)}</p>
      <p>Generated {_esc(generated_text)}</p>
    </div>
  </header>
  <div class="dashboard-nav">
    <div class="inner">
      <a href="index.html">Home</a>
      <a href="emv.html">EM&amp;V Dashboard</a>
      <a href="commissioning.html">Commissioning / RCx Dashboard</a>
      <a class="active" href="source-health.html">Source Health Dashboard</a>
    </div>
  </div>
  <main>
    <div class="cards">
      <div class="card"><div class="label">Distinct Number of Runs {_info_icon("Unique live GitHub workflow executions. Paired EM&V and commissioning checks from the same workflow count once.")}</div><div class="value">{len(workflow_keys)}</div></div>
      <div class="card"><div class="label">Monitor Runs (Both EM&amp;V and Commissioning) {_info_icon("Individual monitor executions. A normal scheduled workflow runs EM&V once and commissioning once, producing two monitor runs.")}</div><div class="value">{len(runs)}</div></div>
      <div class="card"><div class="label">Sources Tracked {_info_icon("Sources with at least one saved source-health observation during the displayed reporting period.")}</div><div class="value">{len(source_summaries)}</div></div>
      <div class="card"><div class="label">Need Investigation</div><div class="value">{len(investigation_sources)}</div></div>
      <div class="card"><div class="label">Current Errors</div><div class="value">{sum(1 for item in source_summaries if item.latest_status == STATUS_ERROR)}</div></div>
      <div class="card"><div class="label">Runs With Missing Data</div><div class="value">{len(incomplete_runs)}</div></div>
    </div>

    <section>
      <h2>Sources Requiring Investigation</h2>
      <p class="section-note">
        A source appears here immediately for a current error or partial result,
        or after {zero_threshold} consecutive distinct live workflows with zero
        candidates. “No Results” means the monitor did not find opportunities
        from that source during the run. This can be normal; investigation begins
        only after 12 consecutive live runs with no results.
        “Partial Results” means a source that uses multiple requests, such as
        SAM.gov, completed some requests while one or more requests or parsing
        steps failed. Opportunities from that run may therefore be incomplete.
      </p>
      {attention_content}
      <div class="legend">
        <span><i class="status-ok"></i>Candidates Returned</span>
        <span><i class="status-zero"></i>No Results Returned {_info_icon("The source returned no raw items during that run. This may be normal; 12 consecutive no-result runs trigger investigation.")}</span>
        <span><i class="status-partial"></i>Partial Results {_info_icon("Some requests or parsing steps succeeded and others failed, so opportunities from that run may be incomplete.")}</span>
        <span><i class="status-error"></i>Error/Unavailable</span>
        <span><i class="status-skipped"></i>Expected JS Skip</span>
      </div>
    </section>

    <section>
      <h2>All Monitored Sources</h2>
      <p class="section-note">
        Paired EM&amp;V and commissioning checks from the same GitHub workflow are
        consolidated into one observation. Candidate counts use the larger result
        from the paired checks rather than double-counting them.
      </p>
      {all_sources_content}
      <div class="legend">
        <span><i class="status-ok"></i>Candidates Returned</span>
        <span><i class="status-zero"></i>No Results Returned {_info_icon("The source returned no raw items during that run. This may be normal; 12 consecutive no-result runs trigger investigation.")}</span>
        <span><i class="status-partial"></i>Partial Results {_info_icon("Some requests or parsing steps succeeded and others failed, so opportunities from that run may be incomplete.")}</span>
        <span><i class="status-error"></i>Error/Unavailable</span>
        <span><i class="status-skipped"></i>Expected JS Skip</span>
      </div>
    </section>

    <section>
      <h2>Data Completeness</h2>
      <p class="section-note">
        Each monitor run reports how many source checks it produced. This section
        compares that number with how many source-check results were actually saved,
        making missing information visible without requiring database knowledge.
      </p>
      {completeness_content}
      <div class="period-summary">
        <p><strong>Entire-Group Zero Events {_info_icon("A distinct live workflow in which every monitored source within a major source group returned no results.")}:</strong> {_esc(group_alert_sentence)}</p>
        <p><strong>Individual Source Results:</strong> {_esc(observation_sentence)}</p>
      </div>
    </section>
  </main>
  <footer>
    Times are displayed in America/New_York as EST or EDT. Supabase timestamps
    remain stored in UTC. This public dashboard intentionally excludes raw error
    messages, GitHub run identifiers, credentials, and request details.
  </footer>
</body>
</html>
"""


# Keep file output separate from rendering so deterministic tests can inspect
# HTML in memory and workflow code can choose the destination explicitly.
def write_source_health_dashboard(
    output_path,
    runs: Iterable[dict],
    records: Iterable[dict],
    **render_options,
) -> Path:
    output = Path(output_path)
    html = render_source_health_dashboard(
        runs,
        records,
        **render_options,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html, encoding="utf-8")
    return output
