"""
delivery.py -- Email and Dashboard Delivery for the CxA RFP Monitor
====================================================================
Two output channels:
  1. Email digest via SendGrid (HTML email to configured recipients)
  2. GitHub Pages HTML dashboard (static, filterable, client-side JS)

Each channel is independent -- a failure in one doesn't prevent the other.
The HTML dashboard is regenerated on every run (even when there are no new
opportunities) so the "last updated" timestamp stays current.

KNOWN FAILURE POINTS (general):
  - Credentials come from environment variables (GitHub Secrets).
    A missing secret skips that channel with a logged warning.
  - If neither channel succeeds, main.py does NOT mark opportunities as
    seen -- they will be retried on the next run.
"""

import json
import logging
import os
from datetime import datetime
from typing import List, Optional

import config
from models import Opportunity

logger = logging.getLogger(__name__)


# ===========================================================================
# 1. EMAIL DELIVERY (SendGrid)
# ===========================================================================

def send_email_digest(
    opportunities: List[Opportunity],
    mode: str = None,
) -> bool:
    """
    Send an HTML email digest of new opportunities via SendGrid.

    When there are no new opportunities, sends a brief status ping
    (subject line only, minimal body) so the monitor's health is visible.

    Args:
        opportunities: New, scored, deduplicated opportunities to report
        mode:          "broad" or "medium" (shown in email for reference)

    Returns:
        True if email was accepted by SendGrid (HTTP 202), False otherwise

    KNOWN FAILURE POINTS:
      1. SendGrid requires SPF/DKIM domain authentication for reliable inbox
         delivery. Without it, emails may land in spam. Complete domain auth
         in the SendGrid dashboard under Settings > Sender Authentication.
      2. SENDGRID_API_KEY must be set in GitHub Actions Secrets. If missing,
         this function returns False immediately and logs a warning.
      3. Free tier limit: 100 emails/day. The monitor sends 1 per run.
         No issue at that volume, but be aware if you add many more recipients.
    """
    api_key = os.environ.get(config.SENDGRID_API_KEY_ENV, "").strip()
    if not api_key:
        logger.warning(
            f"{config.SENDGRID_API_KEY_ENV} not set. "
            f"Skipping email delivery."
        )
        return False

    # Import sendgrid here so a missing package doesn't crash the whole run
    # KNOWN FAILURE POINT: sendgrid must be in requirements.txt
    try:
        import sendgrid
        from sendgrid.helpers.mail import Mail
    except ImportError:
        logger.error(
            "sendgrid package not installed. "
            "Add 'sendgrid>=6.11.0' to requirements.txt."
        )
        return False

    if mode is None:
        mode = config.KEYWORD_MODE

    run_date = datetime.utcnow().strftime("%B %d, %Y")
    count     = len(opportunities)
    high_cnt  = sum(1 for o in opportunities if o.confidence == "High")

    if count == 0:
        subject  = f"{config.EMAIL_SUBJECT_PREFIX} No new EM&V RFPs this week ({run_date})"
        html_body = _render_no_results_email(run_date)
    else:
        subject   = (
            f"{config.EMAIL_SUBJECT_PREFIX} {count} new RFP{'s' if count > 1 else ''} "
            f"({high_cnt} high confidence) -- {run_date}"
        )
        html_body = _render_digest_email(opportunities, mode, run_date)

    sg = sendgrid.SendGridAPIClient(api_key=api_key)
    all_ok = True

    for recipient in config.EMAIL_TO:
        try:
            msg = Mail(
                from_email=config.EMAIL_FROM,
                to_emails=recipient,
                subject=subject,
                html_content=html_body,
            )
            response = sg.client.mail.send.post(request_body=msg.get())

            # SendGrid returns 202 Accepted on success
            if response.status_code == 202:
                logger.info(f"Email sent to {recipient}")
            else:
                logger.warning(
                    f"Unexpected SendGrid status {response.status_code} "
                    f"for recipient {recipient}"
                )
                all_ok = False

        except Exception as e:
            logger.error(f"Email to {recipient} failed: {e}")
            all_ok = False

    return all_ok


def _render_digest_email(
    opportunities: List[Opportunity],
    mode: str,
    run_date: str,
) -> str:
    """
    Build the HTML email body for the opportunity digest.

    Uses inline CSS throughout for email-client compatibility.
    Opportunities are grouped by confidence: High first, then Medium, then Low.

    The "mode" label is shown in the header so recipients know whether
    they're seeing broad or medium-sensitivity results.
    """
    high   = [o for o in opportunities if o.confidence == "High"]
    medium = [o for o in opportunities if o.confidence == "Medium"]
    low    = [o for o in opportunities if o.confidence == "Low"]

    def opp_card(opp: Opportunity) -> str:
        """Render one opportunity as an HTML card block."""
        badge_color = {
            "High":   "#2e7d32",
            "Medium": "#e65100",
            "Low":    "#616161",
        }.get(opp.confidence, "#616161")

        deadline_str = opp.deadline or "Not specified"
        days = opp.days_until_deadline()
        if days is not None:
            days_note = f" <span style='color:{'#c62828' if days <= 14 else '#555'};'>"
            days_note += f"({days} days)</span>"
        else:
            days_note = ""

        keywords_str = ", ".join(opp.matched_keywords[:5]) or "N/A"
        state_str    = opp.state or "N/A"
        desc_html    = (
            f"<p style='font-size:12px;color:#555;font-style:italic;margin:0 0 8px 0;'>"
            f"{opp.description[:280]}...</p>"
        ) if opp.description else ""

        contact_html = ""
        if opp.contact_email:
            contact_html = (
                f"<span style='font-size:12px;color:#555;'>"
                f"Contact: <a href='mailto:{opp.contact_email}' style='color:#0066cc;'>"
                f"{opp.contact_name or opp.contact_email}</a></span><br>"
            )

        return f"""
        <div style="border:1px solid #e0e0e0;border-left:4px solid {badge_color};
                    border-radius:4px;padding:14px 16px;margin-bottom:14px;background:#fff;">
          <div style="margin-bottom:6px;">
            <span style="background:{badge_color};color:#fff;font-size:10px;font-weight:700;
                         padding:2px 7px;border-radius:3px;margin-right:8px;">
              {opp.confidence.upper()}
            </span>
            <span style="font-size:12px;color:#777;">{opp.source} &mdash; {state_str}</span>
          </div>
          <h3 style="margin:0 0 8px 0;font-size:15px;">
            <a href="{opp.url}" style="color:#1a1a2e;text-decoration:none;">{opp.title}</a>
          </h3>
          {desc_html}
          <p style="margin:0 0 6px 0;font-size:13px;color:#444;line-height:1.6;">
            <strong>Issuer:</strong> {opp.issuer}<br>
            <strong>Deadline:</strong> {deadline_str}{days_note}<br>
            <strong>Posted:</strong> {opp.posted_date or 'N/A'}<br>
            {contact_html}
            <strong>Keywords:</strong> <span style="color:#555;">{keywords_str}</span>
            <span style="color:#aaa;font-size:11px;"> (score: {opp.relevance_score})</span>
          </p>
          <a href="{opp.url}" style="display:inline-block;background:#0066cc;color:#fff;
             padding:5px 14px;border-radius:4px;text-decoration:none;font-size:13px;
             font-weight:600;">View RFP &rarr;</a>
        </div>
        """

    def section(label: str, color: str, opps: List[Opportunity]) -> str:
        if not opps:
            return ""
        cards = "".join(opp_card(o) for o in opps)
        return f"""
        <h2 style="font-size:16px;color:{color};margin:24px 0 12px 0;
                   padding-bottom:6px;border-bottom:2px solid {color};">
          {label} ({len(opps)})
        </h2>
        {cards}
        """

    body_sections = (
        section("High Confidence", "#2e7d32", high)
        + section("Medium Confidence", "#e65100", medium)
        + section("Low Confidence", "#616161", low)
    )

    mode_label = "Broad" if mode == "broad" else "Medium"

    return f"""<!DOCTYPE html>
<html>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;
             max-width:680px;margin:0 auto;padding:20px;background:#f5f5f5;color:#333;">
  <div style="background:#1a1a2e;color:#fff;padding:20px 24px;border-radius:6px 6px 0 0;">
    <h1 style="margin:0;font-size:20px;font-weight:700;">CxA RFP Monitor</h1>
    <p style="margin:4px 0 0 0;font-size:13px;opacity:0.75;">
      EM&amp;V Opportunity Digest &mdash; {run_date} &mdash; Mode: {mode_label}
    </p>
  </div>
  <div style="background:#fff;padding:20px 24px;border:1px solid #ddd;border-top:none;
              border-radius:0 0 6px 6px;">
    <p style="color:#444;font-size:14px;margin-top:0;">
      Found <strong>{len(opportunities)}</strong> new opportunities
      ({sum(1 for o in opportunities if o.confidence=='High')} high confidence).
      <a href="https://cx-associates.github.io/rfp-monitor/"
         style="color:#0066cc;">View full dashboard</a>
    </p>
    {body_sections}
    <hr style="border:none;border-top:1px solid #eee;margin:24px 0 16px 0;">
    <p style="font-size:11px;color:#aaa;margin:0;">
      CxA RFP Monitor &mdash; Auto-generated weekly digest &mdash;
      Keyword mode: {mode_label} &mdash;
      <a href="https://cx-associates.github.io/rfp-monitor/" style="color:#aaa;">
        Dashboard
      </a>
    </p>
  </div>
</body>
</html>"""


def _render_no_results_email(run_date: str) -> str:
    """Minimal status-ping email when no new results are found."""
    return f"""<!DOCTYPE html>
<html>
<body style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;padding:20px;">
  <h2 style="color:#1a1a2e;">CxA RFP Monitor</h2>
  <p>No new EM&amp;V RFP opportunities found this week ({run_date}).</p>
  <p>All monitored sources were checked. The monitor is running normally.</p>
  <p style="font-size:12px;color:#888;">
    <a href="https://cx-associates.github.io/rfp-monitor/">View dashboard</a>
  </p>
</body>
</html>"""


# ===========================================================================
# 2. HTML DASHBOARD (GitHub Pages)
# ===========================================================================

def generate_dashboard(
    new_opportunities: List[Opportunity],
    all_scored: List[Opportunity],
    mode: str = None,
    manual_review: Optional[List[Opportunity]] = None,
) -> bool:
    """
    Write a static HTML dashboard to config.DASHBOARD_OUTPUT_PATH.

    The dashboard is regenerated on every run. The GitHub Actions workflow
    commits it back to the repo, where GitHub Pages serves it.

    Features:
      - Summary stat cards (new, high, medium, total)
      - Client-side filtering by confidence, source, state, and keyword search
      - "NEW" badge on opportunities from the current run
      - Color-coded confidence labels
      - Countdown to deadline (red if <= 14 days)
      - Mode indicator showing current keyword sensitivity

    Args:
        new_opportunities: From this run (get "NEW" badge)
        all_scored:        All passing opportunities (full table)
        mode:              "broad" or "medium"

    Returns:
        True if file written successfully, False otherwise

    KNOWN FAILURE POINT: The docs/ directory must exist in the repo before
    the first run. Create it with: mkdir -p docs && touch docs/.gitkeep
    and commit that before deploying.
    """
    if mode is None:
        mode = config.KEYWORD_MODE

    try:
        os.makedirs(os.path.dirname(config.DASHBOARD_OUTPUT_PATH), exist_ok=True)
        html = _render_dashboard_html(
            new_opportunities,
            all_scored,
            mode,
            manual_review or [],
        )
        with open(config.DASHBOARD_OUTPUT_PATH, "w", encoding="utf-8") as f:
            f.write(html)
        logger.info(f"Dashboard written to {config.DASHBOARD_OUTPUT_PATH}")
        return True
    except Exception as e:
        logger.error(f"Dashboard generation failed: {e}")
        return False


def _render_dashboard_html(
    new_opps: List[Opportunity],
    all_opps: List[Opportunity],
    mode: str,
    manual_review: List[Opportunity],
) -> str:
    """
    Build the complete HTML string for the GitHub Pages dashboard.

    Client-side JavaScript handles filtering (no server needed).
    All data is embedded inline as data-* attributes on <tr> elements.

    KNOWN FAILURE POINT: HTML special characters in opportunity data
    (ampersands, angle brackets, quotes in titles) must be escaped to
    prevent broken attribute values and XSS. The _esc() helper handles this.
    """
    run_time   = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    new_keys   = {o.unique_key() for o in new_opps}
    mode_label = "Broad" if mode == "broad" else "Medium"

    # Build filter option sets from the main/passing table only.
    sources = sorted(set(o.source for o in all_opps))
    states  = sorted(s for s in set(o.state or "" for o in all_opps) if s)

    # Summary counts
    new_cnt    = len(new_opps)
    high_cnt   = sum(1 for o in all_opps if o.confidence == "High")
    medium_cnt = sum(1 for o in all_opps if o.confidence == "Medium")
    total_cnt  = len(all_opps)
    manual_cnt = len(manual_review)

    def table_row(opp: Opportunity, allow_remove: bool = False) -> str:
        """Render one <tr> for an opportunities table."""
        is_new      = opp.unique_key() in new_keys
        new_badge   = '<span class="badge-new">NEW</span> ' if is_new else ""
        conf_class  = {"High":"conf-high","Medium":"conf-med","Low":"conf-low"}.get(
            opp.confidence, "conf-low"
        )
        deadline_str = opp.deadline or "--"
        days         = opp.days_until_deadline()
        days_html    = (
            f'<span class="days-urgent">{days}d</span>' if days is not None and days <= 14
            else f"{days}d" if days is not None
            else "--"
        )
        kw_str = ", ".join(opp.matched_keywords[:4]) if opp.matched_keywords else ""
        title_display = _esc(opp.title[:85]) + ("..." if len(opp.title) > 85 else "")

        remove_cell = ""
        if allow_remove:
            remove_cell = (
                '<td class="remove-cell">'
                '<button type="button" class="remove-btn" '
                'title="Hide this manual-review item" '
                'onclick="suppressManualReview(this)">x</button>'
                '</td>'
            )

        return (
            f'<tr data-conf="{_esc(opp.confidence)}" '
            f'data-source="{_esc(opp.source)}" '
            f'data-state="{_esc(opp.state or "")}" '
            f'data-title="{_esc(opp.title.lower())}" '
            f'data-title-full="{_esc(opp.title)}" '
            f'data-unique-key="{_esc(opp.unique_key())}" '
            f'class="{"row-new" if is_new else ""}">'
            f"<td>{new_badge}"
            f'<a href="{_esc(opp.url)}" target="_blank">{title_display}</a></td>'
            f"<td>{_esc(opp.source)}</td>"
            f"<td>{_esc(opp.issuer[:40])}</td>"
            f"<td>{_esc(opp.state or '--')}</td>"
            f'<td><span class="{conf_class}">{opp.confidence}</span></td>'
            f"<td>{opp.relevance_score}</td>"
            f"<td>{deadline_str}</td>"
            f"<td>{days_html}</td>"
            f'<td style="font-size:11px;color:#666;">{_esc(kw_str)}</td>'
            f"{remove_cell}"
            f"</tr>\n"
        )

    table_rows = "".join(
        table_row(o) for o in all_opps[:config.DASHBOARD_MAX_DISPLAY]
    )
    manual_rows = "".join(
        table_row(o, allow_remove=True) for o in manual_review[:config.DASHBOARD_MAX_DISPLAY]
    )

    if not manual_rows:
        manual_rows = (
            '<tr><td colspan="10" style="color:#777;font-style:italic;">'
            'No below-threshold candidates for manual review.'
            '</td></tr>'
        )

    source_options = "".join(f'<option value="{_esc(s)}">{_esc(s)}</option>' for s in sources)
    state_options  = "".join(f'<option value="{_esc(s)}">{s}</option>' for s in states)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1.0">
  <title>CxA RFP Monitor</title>
  <style>
    *{{box-sizing:border-box;margin:0;padding:0;}}
    body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;
         background:#f0f2f5;color:#333;}}
    .hdr{{background:#1a1a2e;color:#fff;padding:18px 28px;}}
    .hdr h1{{font-size:22px;font-weight:700;}}
    .hdr p{{font-size:13px;opacity:.7;margin-top:4px;}}
    .mode-badge{{display:inline-block;background:#e65100;color:#fff;font-size:11px;
                 font-weight:700;padding:2px 8px;border-radius:3px;margin-left:10px;
                 vertical-align:middle;}}
    .stats{{display:flex;gap:14px;padding:18px 28px;flex-wrap:wrap;}}
    .stat{{background:#fff;border-radius:8px;padding:14px 20px;min-width:120px;
           box-shadow:0 1px 3px rgba(0,0,0,.1);}}
    .stat .n{{font-size:30px;font-weight:700;color:#1a1a2e;}}
    .stat .l{{font-size:11px;color:#888;margin-top:2px;}}
    .filters{{padding:0 28px 14px;display:flex;gap:10px;flex-wrap:wrap;align-items:center;}}
    .filters select,.filters input{{padding:6px 10px;border:1px solid #ddd;border-radius:5px;
                                    font-size:13px;background:#fff;}}
    .filters button{{padding:6px 12px;border:1px solid #ddd;border-radius:5px;
                     background:#fff;cursor:pointer;font-size:13px;}}
    .tbl-wrap{{padding:0 28px 28px;overflow-x:auto;}}
    table{{width:100%;border-collapse:collapse;background:#fff;border-radius:8px;
           box-shadow:0 1px 3px rgba(0,0,0,.1);font-size:13px;}}
    th{{background:#1a1a2e;color:#fff;padding:9px 11px;text-align:left;font-size:12px;
        font-weight:600;white-space:nowrap;}}
    td{{padding:9px 11px;border-bottom:1px solid #f0f0f0;}}
    tr:last-child td{{border-bottom:none;}}
    tr:hover td{{background:#f8f8ff;}}
    .row-new td{{background:#f0fff4;}}
    .badge-new{{background:#2e7d32;color:#fff;font-size:10px;font-weight:700;
                padding:1px 5px;border-radius:3px;}}
    .conf-high{{color:#2e7d32;font-weight:700;}}
    .conf-med{{color:#e65100;font-weight:600;}}
    .conf-low{{color:#888;}}
    .days-urgent{{color:#c62828;font-weight:700;}}
    .manual-review{{margin:0 28px 28px;background:#fff;border-radius:8px;
                    box-shadow:0 1px 3px rgba(0,0,0,.1);padding:12px 16px;}}
    .manual-review summary{{cursor:pointer;font-weight:700;color:#1a1a2e;}}
    .manual-review p{{font-size:12px;color:#777;margin:8px 0 10px 0;}}
    .manual-table{{padding:8px 0 0;}}
    .manual-table table{{box-shadow:none;border:1px solid #eee;}}
    .remove-cell{{text-align:center;width:38px;}}
    .remove-btn{{border:0;background:#eee;color:#555;border-radius:50%;
                 width:24px;height:24px;line-height:20px;cursor:pointer;
                 font-weight:700;font-size:16px;}}
    .remove-btn:hover{{background:#d32f2f;color:#fff;}}
    .remove-btn:disabled{{opacity:.6;cursor:wait;}}
    a{{color:#0066cc;text-decoration:none;}}
    a:hover{{text-decoration:underline;}}
    #row-count{{font-size:12px;color:#888;padding:0 28px 8px;}}
  </style>
</head>
<body>
  <div class="hdr">
    <h1>CxA RFP Monitor
      <span class="mode-badge" title="Current keyword sensitivity mode">
        {mode_label} mode
      </span>
    </h1>
    <p>EM&amp;V Opportunity Dashboard &mdash; Last updated: {run_time}</p>
  </div>

  <div class="stats">
    <div class="stat"><div class="n">{new_cnt}</div><div class="l">New this run</div></div>
    <div class="stat"><div class="n" style="color:#2e7d32">{high_cnt}</div>
      <div class="l">High confidence</div></div>
    <div class="stat"><div class="n" style="color:#e65100">{medium_cnt}</div>
      <div class="l">Medium confidence</div></div>
    <div class="stat"><div class="n">{total_cnt}</div><div class="l">Total active</div></div>
    <div class="stat"><div class="n" id="manual-count">{manual_cnt}</div><div class="l">Manual review</div></div>
  </div>

  <div class="filters">
    <input type="text" id="f-text" placeholder="Search titles..." oninput="applyFilters()">
    <select id="f-conf" onchange="applyFilters()">
      <option value="">All confidence</option>
      <option value="High">High</option>
      <option value="Medium">Medium</option>
      <option value="Low">Low</option>
    </select>
    <select id="f-src" onchange="applyFilters()">
      <option value="">All sources</option>
      {source_options}
    </select>
    <select id="f-state" onchange="applyFilters()">
      <option value="">All states</option>
      {state_options}
    </select>
    <button onclick="resetFilters()">Reset</button>
    <label style="font-size:12px;color:#888;display:flex;align-items:center;gap:4px;">
      <input type="checkbox" id="f-new" onchange="applyFilters()"> New only
    </label>
  </div>
  <div id="row-count"></div>

  <div class="tbl-wrap">
    <table>
      <thead>
        <tr>
          <th>Title</th><th>Source</th><th>Issuer</th><th>State</th>
          <th>Conf.</th><th>Score</th><th>Deadline</th><th>Days</th>
          <th>Keywords</th>
        </tr>
      </thead>
      <tbody id="tbody">
        {table_rows}
      </tbody>
    </table>
  </div>

  <details class="manual-review">
    <summary>Manual review candidates (<span id="manual-summary-count">{manual_cnt}</span> below threshold)</summary>
    <p>
      These items were scraped and scored but did not meet the current inclusion
      threshold for the main dashboard/email digest. Review periodically for
      missed opportunities or keyword-tuning ideas.
    </p>
    <div class="tbl-wrap manual-table">
      <table>
        <thead>
          <tr>
            <th>Title</th><th>Source</th><th>Issuer</th><th>State</th>
            <th>Conf.</th><th>Score</th><th>Deadline</th><th>Days</th>
            <th>Keywords</th><th></th>
          </tr>
        </thead>
        <tbody id="manual-review-body">
          {manual_rows}
        </tbody>
      </table>
    </div>
  </details>

  <script>
    function applyFilters() {{
      const text  = document.getElementById('f-text').value.toLowerCase();
      const conf  = document.getElementById('f-conf').value;
      const src   = document.getElementById('f-src').value;
      const state = document.getElementById('f-state').value;
      const newOnly = document.getElementById('f-new').checked;
      const rows  = document.querySelectorAll('#tbody tr');
      let visible = 0;
      rows.forEach(r => {{
        const titleMatch  = !text  || (r.dataset.title  || '').includes(text);
        const confMatch   = !conf  || r.dataset.conf  === conf;
        const srcMatch    = !src   || r.dataset.source === src;
        const stateMatch  = !state || r.dataset.state  === state;
        const newMatch    = !newOnly || r.classList.contains('row-new');
        const show = titleMatch && confMatch && srcMatch && stateMatch && newMatch;
        r.style.display = show ? '' : 'none';
        if (show) visible++;
      }});
      document.getElementById('row-count').textContent =
        `Showing ${{visible}} of ${{rows.length}} opportunities`;
    }}
    function resetFilters() {{
      ['f-text','f-conf','f-src','f-state'].forEach(id => {{
        const el = document.getElementById(id);
        if (el.tagName === 'INPUT') el.value = '';
        else el.selectedIndex = 0;
      }});
      document.getElementById('f-new').checked = false;
      applyFilters();
    }}
    async function suppressManualReview(button) {{
      const row = button.closest('tr');
      if (!row) return;

      let token = localStorage.getItem('rfpAdminToken') || '';
      if (!token) {{
        token = prompt('Enter dashboard removal token');
        if (!token) return;
        localStorage.setItem('rfpAdminToken', token);
      }}

      const payload = {{
        monitor_type: 'emv',
        unique_key: row.dataset.uniqueKey || '',
        source: row.dataset.source || '',
        title: row.dataset.titleFull || row.dataset.title || '',
        reason: 'manual_dashboard_dismissal',
        suppressed_by: 'dashboard'
      }};

      button.disabled = true;
      button.textContent = '...';

      try {{
        const response = await fetch(
          'https://udxcbyoohgzdkjxytxzg.functions.supabase.co/suppress-manual-review',
          {{
            method: 'POST',
            headers: {{
              'Content-Type': 'application/json',
              'x-rfp-admin-token': token
            }},
            body: JSON.stringify(payload)
          }}
        );

        const result = await response.json().catch(() => ({{}}));

        if (!response.ok || !result.ok) {{
          if (response.status === 401) {{
            localStorage.removeItem('rfpAdminToken');
            alert('Removal token was rejected. Try again with the correct token.');
          }} else {{
            alert('Could not hide item: ' + (result.error || response.status));
          }}
          button.disabled = false;
          button.textContent = 'x';
          return;
        }}

        row.remove();

        const manualRows = document.querySelectorAll('#manual-review-body tr[data-unique-key]');
        const manualCount = document.getElementById('manual-count');
        const manualSummaryCount = document.getElementById('manual-summary-count');

        if (manualCount) manualCount.textContent = manualRows.length;
        if (manualSummaryCount) manualSummaryCount.textContent = manualRows.length;

        if (manualRows.length === 0) {{
          document.getElementById('manual-review-body').innerHTML =
            '<tr><td colspan="10" style="color:#777;font-style:italic;">No below-threshold candidates for manual review.</td></tr>';
        }}
      }} catch (err) {{
        alert('Could not hide item. Check network connection and try again.');
        button.disabled = false;
        button.textContent = 'x';
      }}
    }}

    // Initialize row count on load
    applyFilters();
  </script>
</body>
</html>"""


def _esc(text: str) -> str:
    """Escape HTML special characters for safe attribute and content embedding."""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )
