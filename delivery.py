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
import hashlib
from datetime import datetime
from typing import List, Optional

import config
from models import Opportunity

logger = logging.getLogger(__name__)


def send_source_health_email(
    health_records,
    monitor_type: str = None,
) -> bool:
    """
    Send a separate source-health report email after each non-dry monitor run.

    This is intentionally independent from opportunity delivery and does not
    affect deduplication, dashboard generation, or seen-set updates.
    """
    api_key = os.environ.get(config.SENDGRID_API_KEY_ENV, "").strip()
    if not api_key:
        logger.warning(
            f"{config.SENDGRID_API_KEY_ENV} not set. "
            f"Skipping source health email."
        )
        return False

    try:
        import sendgrid
        from sendgrid.helpers.mail import Mail
    except ImportError:
        logger.error(
            "sendgrid package not installed. "
            "Add 'sendgrid>=6.11.0' to requirements.txt."
        )
        return False

    from source_health import summarize_source_health

    monitor_type = config.normalize_monitor_type(monitor_type)
    monitor_label = config.get_monitor_label(monitor_type)
    run_date = datetime.utcnow().strftime("%B %d, %Y")
    summary = summarize_source_health(health_records)

    error_count = summary.get("HEALTH_ERROR_EXCEPTION", 0)
    warn_count = (
        summary.get("HEALTH_WARN_ZERO", 0)
        + summary.get("HEALTH_WARN_SKIPPED_JS", 0)
        + summary.get("HEALTH_WARN_TOTAL_ZERO", 0)
    )
    ok_count = summary.get("HEALTH_OK_NONZERO", 0)

    subject = (
        f"[CxA RFP Monitor Health] {monitor_label} source report - "
        f"{run_date} ({error_count} errors, {warn_count} warnings)"
    )
    html_body = _render_source_health_email(
        health_records=health_records,
        monitor_label=monitor_label,
        run_date=run_date,
        ok_count=ok_count,
        warn_count=warn_count,
        error_count=error_count,
    )

    sg = sendgrid.SendGridAPIClient(api_key=api_key)
    all_ok = True

    for recipient in config.SOURCE_HEALTH_EMAIL_TO:
        try:
            msg = Mail(
                from_email=config.EMAIL_FROM,
                to_emails=recipient,
                subject=subject,
                html_content=html_body,
            )
            response = sg.client.mail.send.post(request_body=msg.get())

            if response.status_code == 202:
                logger.info(f"Source health email sent to {recipient}")
            else:
                logger.warning(
                    f"Unexpected SendGrid status {response.status_code} "
                    f"for source health recipient {recipient}"
                )
                all_ok = False

        except Exception as e:
            logger.error(f"Source health email to {recipient} failed: {e}")
            all_ok = False

    return all_ok


def _render_source_health_email(
    health_records,
    monitor_label: str,
    run_date: str,
    ok_count: int,
    warn_count: int,
    error_count: int,
) -> str:
    def esc(value) -> str:
        return (
            str(value or "")
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    def badge_style(code: str) -> str:
        if code == "HEALTH_ERROR_EXCEPTION":
            return "background:#c62828;color:#fff;"
        if code in ["HEALTH_WARN_ZERO", "HEALTH_WARN_SKIPPED_JS", "HEALTH_WARN_TOTAL_ZERO"]:
            return "background:#e65100;color:#fff;"
        return "background:#2e7d32;color:#fff;"

    grouped = {}
    for record in health_records:
        grouped.setdefault(record.source_group, []).append(record)

    sections = []
    for group_name, records in grouped.items():
        rows = []
        for record in records:
            count = "" if record.candidate_count is None else record.candidate_count
            rows.append(f"""
              <tr>
                <td style="padding:6px 8px;border-bottom:1px solid #eee;">{esc(record.source_name)}</td>
                <td style="padding:6px 8px;border-bottom:1px solid #eee;">
                  <span style="{badge_style(record.code)}font-size:10px;font-weight:700;padding:2px 6px;border-radius:3px;">
                    {esc(record.code)}
                  </span>
                </td>
                <td style="padding:6px 8px;border-bottom:1px solid #eee;text-align:right;">{esc(count)}</td>
                <td style="padding:6px 8px;border-bottom:1px solid #eee;">{esc(record.message)}</td>
              </tr>
            """)

        sections.append(f"""
          <h3 style="font-size:15px;color:#1a1a2e;margin:18px 0 8px 0;">{esc(group_name)}</h3>
          <table style="width:100%;border-collapse:collapse;font-size:12px;">
            <thead>
              <tr>
                <th style="text-align:left;padding:6px 8px;background:#f0f2f5;">Source</th>
                <th style="text-align:left;padding:6px 8px;background:#f0f2f5;">Health code</th>
                <th style="text-align:right;padding:6px 8px;background:#f0f2f5;">Count</th>
                <th style="text-align:left;padding:6px 8px;background:#f0f2f5;">Message</th>
              </tr>
            </thead>
            <tbody>
              {''.join(rows)}
            </tbody>
          </table>
        """)

    return f"""<!DOCTYPE html>
<html>
<body style="font-family:Arial,sans-serif;max-width:900px;margin:0 auto;padding:20px;background:#f5f5f5;color:#333;">
  <div style="background:#1a1a2e;color:#fff;padding:18px 22px;border-radius:6px 6px 0 0;">
    <h1 style="margin:0;font-size:20px;">CxA RFP Monitor Source Health</h1>
    <p style="margin:4px 0 0 0;font-size:13px;opacity:.8;">{esc(monitor_label)} &mdash; {esc(run_date)}</p>
  </div>
  <div style="background:#fff;padding:18px 22px;border:1px solid #ddd;border-top:none;border-radius:0 0 6px 6px;">
    <p style="font-size:13px;margin:0 0 14px 0;">
      <strong>{ok_count}</strong> OK &nbsp;|&nbsp;
      <strong>{warn_count}</strong> warnings &nbsp;|&nbsp;
      <strong>{error_count}</strong> errors
    </p>
    <p style="font-size:12px;color:#666;margin:0 0 14px 0;">
      Codes: HEALTH_OK_NONZERO = source returned candidates;
      HEALTH_WARN_ZERO = source returned 0 candidates;
      HEALTH_WARN_SKIPPED_JS = source skipped because JS-rendered / Phase 2;
      HEALTH_ERROR_EXCEPTION = source threw exception;
      HEALTH_WARN_TOTAL_ZERO = entire source group returned 0.
    </p>
    {''.join(sections)}
  </div>
</body>
</html>"""


# ===========================================================================
# 1. EMAIL DELIVERY (SendGrid)
# ===========================================================================

def send_email_digest(
    opportunities: List[Opportunity],
    mode: str = None,
    monitor_type: str = None,
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
    monitor_type = config.normalize_monitor_type(monitor_type)
    monitor_label = config.get_monitor_label(monitor_type)
    subject_prefix = config.get_email_subject_prefix(monitor_type)

    run_date = datetime.utcnow().strftime("%B %d, %Y")
    count     = len(opportunities)
    high_cnt  = sum(1 for o in opportunities if o.confidence == "High")

    if count == 0:
        subject  = f"{subject_prefix} No new RFPs this week ({run_date})"
        html_body = _render_no_results_email(run_date, monitor_type)
    else:
        subject   = (
            f"{subject_prefix} {count} new RFP{'s' if count > 1 else ''} "
            f"({high_cnt} high confidence) -- {run_date}"
        )
        html_body = _render_digest_email(opportunities, mode, run_date, monitor_type)

    sg = sendgrid.SendGridAPIClient(api_key=api_key)
    all_ok = True

    for recipient in config.get_email_recipients(monitor_type):
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
    monitor_type: str,
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
    monitor_type = config.normalize_monitor_type(monitor_type)
    monitor_label = config.get_monitor_label(monitor_type)
    dashboard_url = config.get_dashboard_url(monitor_type)

    return f"""<!DOCTYPE html>
<html>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;
             max-width:680px;margin:0 auto;padding:20px;background:#f5f5f5;color:#333;">
  <div style="background:#1a1a2e;color:#fff;padding:20px 24px;border-radius:6px 6px 0 0;">
    <h1 style="margin:0;font-size:20px;font-weight:700;">CxA RFP Monitor</h1>
    <p style="margin:4px 0 0 0;font-size:13px;opacity:0.75;">
      {monitor_label} Opportunity Digest &mdash; {run_date} &mdash; Mode: {mode_label}
    </p>
  </div>
  <div style="background:#fff;padding:20px 24px;border:1px solid #ddd;border-top:none;
              border-radius:0 0 6px 6px;">
    <p style="color:#444;font-size:14px;margin-top:0;">
      Found <strong>{len(opportunities)}</strong> new opportunities
      ({sum(1 for o in opportunities if o.confidence=='High')} high confidence).
      <a href="{dashboard_url}"
         style="color:#0066cc;">View full dashboard</a>
    </p>
    {body_sections}
    <hr style="border:none;border-top:1px solid #eee;margin:24px 0 16px 0;">
    <p style="font-size:11px;color:#aaa;margin:0;">
      CxA RFP Monitor &mdash; Auto-generated weekly digest &mdash;
      Keyword mode: {mode_label} &mdash;
      <a href="{dashboard_url}" style="color:#aaa;">
        Dashboard
      </a>
    </p>
  </div>
</body>
</html>"""


def _render_no_results_email(run_date: str, monitor_type: str) -> str:
    """Minimal status-ping email when no new results are found."""
    monitor_type = config.normalize_monitor_type(monitor_type)
    monitor_label = config.get_monitor_label(monitor_type)
    dashboard_url = config.get_dashboard_url(monitor_type)
    return f"""<!DOCTYPE html>
<html>
<body style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;padding:20px;">
  <h2 style="color:#1a1a2e;">CxA RFP Monitor</h2>
  <p>No new {monitor_label} RFP opportunities found this week ({run_date}).</p>
  <p>All monitored sources were checked. The monitor is running normally.</p>
  <p style="font-size:12px;color:#888;">
    <a href="{dashboard_url}">View dashboard</a>
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
    monitor_type: str = None,
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
    monitor_type = config.normalize_monitor_type(monitor_type)
    output_path = config.get_dashboard_output_path(monitor_type)

    try:
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        html = _render_dashboard_html(
            new_opportunities,
            all_scored,
            mode,
            manual_review or [],
            monitor_type,
        )
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html)
        generate_landing_page()
        logger.info(f"Dashboard written to {output_path}")
        return True
    except Exception as e:
        logger.error(f"Dashboard generation failed: {e}")
        return False


def _render_dashboard_html(
    new_opps: List[Opportunity],
    all_opps: List[Opportunity],
    mode: str,
    manual_review: List[Opportunity],
    monitor_type: str,
) -> str:
    """
    Build the complete HTML string for the GitHub Pages dashboard.

    Client-side JavaScript handles filtering (no server needed).
    All data is embedded inline as data-* attributes on <tr> elements.

    KNOWN FAILURE POINT: HTML special characters in opportunity data
    (ampersands, angle brackets, quotes in titles) must be escaped to
    prevent broken attribute values and XSS. The _esc() helper handles this.
    """
    monitor_type = config.normalize_monitor_type(monitor_type)
    monitor_label = config.get_monitor_label(monitor_type)
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

    def _norm_review_part(value: object) -> str:
        """Normalize a field for cross-dashboard review-key fallback matching."""
        return " ".join(str(value or "").strip().lower().split())

    def _review_key_for(opp: Opportunity) -> str:
        """
        Shared review key used by both EM&V and commissioning dashboards.

        Preferred: source + notice_id
        Fallback:  source + normalized title + normalized URL
        """
        notice_id = getattr(opp, "notice_id", "") or ""
        if notice_id:
            basis = f"{opp.source}|notice|{notice_id.strip().lower()}"
        else:
            basis = (
                f"{opp.source}|fallback|"
                f"{_norm_review_part(opp.title)}|"
                f"{_norm_review_part(opp.url)}"
            )
        return hashlib.sha256(basis.encode("utf-8")).hexdigest()

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
        notice_id = getattr(opp, "notice_id", "") or ""
        review_key = _review_key_for(opp)
        manual_review_attr = "true" if allow_remove else "false"
        is_manual_promoted = bool(
            getattr(opp, "promoted_from_manual_review", False)
            or getattr(opp, "manual_promoted", False)
        )
        manual_promoted_attr = "true" if is_manual_promoted else "false"
        promoted_badge = (
            '<span class="badge-promoted">Promoted from Manual Review</span> '
            if is_manual_promoted
            else ""
        )
        review_cell = (
            '<td class="review-cell">'
            '<div class="review-controls">'
            '<label><span class="review-label">Review Status <span class="review-help" title="Human triage decision for next step.">i</span></span>'
            '<select class="review-status" onchange="saveReviewRow(this)">'
            '<option value=""></option>'
            '<option>Needs Further Tech Review</option>'
            '<option>Needs Further Admin Review</option>'
            '<option>Needs KO Call</option>'
            '<option>No-Go</option>'
            '<option>Already Captured Before</option>'
            '<option>Duplicate</option>'
            '</select></label>'
            '<label><span class="review-label">Reviewer Fit <span class="review-help" title="Human fit rating after reviewing the RFP.">i</span></span>'
            '<select class="reviewer-fit" onchange="saveReviewRow(this)">'
            '<option value=""></option>'
            '<option>Strong Fit</option>'
            '<option>Possible Fit</option>'
            '<option>Poor Fit</option>'
            '<option>Possible Subcontractor Role</option>'
            '<option>Indicative of potential upcoming RFP</option>'
            '</select></label>'
            '<label>Tech Owner <input class="review-tech-owner" type="text" placeholder="Tech reviewer / owner" onblur="saveReviewRow(this)"></label>'
            '<label>Admin Owner <input class="review-admin-owner" type="text" placeholder="Admin reviewer / owner" onblur="saveReviewRow(this)"></label>'
            '<div class="review-checks">'
            '<label><input type="checkbox" class="admin-reviewed" onchange="saveReviewRow(this)"> Admin reviewed</label>'
            '<label><input type="checkbox" class="emv-technical-reviewed" onchange="saveReviewRow(this)"> EM&amp;V tech reviewed</label>'
            '<label><input type="checkbox" class="commissioning-technical-reviewed" onchange="saveReviewRow(this)"> Cx tech reviewed</label>'
            '</div>'
            '<label class="review-note"><span class="review-label">Technical Review Notes <span class="review-help" title="Technical fit, scope, teaming needs, or go/no-go rationale.">i</span></span>'
            '<textarea class="technical-review-notes" rows="2" placeholder="Technical notes"></textarea></label>'
            '<label class="review-note"><span class="review-label">Admin Review Notes <span class="review-help" title="Admin tracking such as F drive, HubSpot, deadline check, or duplicate handling.">i</span></span>'
            '<textarea class="admin-review-notes" rows="2" placeholder="Admin notes / F drive / HubSpot"></textarea></label>'
            '<button type="button" class="review-save" onclick="saveReviewRow(this)">Save notes</button>'
            '<span class="review-msg"></span>'
            '</div></td>'
        )

        kw_str = ", ".join(opp.matched_keywords[:4]) if opp.matched_keywords else ""
        matched_keywords_data = "|".join(str(k) for k in (opp.matched_keywords or []))
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
            f'data-review-key="{_esc(review_key)}" '
            f'data-manual-review="{manual_review_attr}" '
            f'data-manual-promoted="{manual_promoted_attr}" '
            f'data-notice-id="{_esc(notice_id)}" '
            f'data-url="{_esc(opp.url)}" '
            f'data-deadline="{_esc(opp.deadline or "")}" '
            f'data-score="{opp.relevance_score}" '
            f'data-matched-keywords="{_esc(matched_keywords_data)}" '
            f'data-issuer="{_esc(opp.issuer)}" '
            f'class="{"row-new" if is_new else ""}{" promoted-manual" if is_manual_promoted else ""}">'
            f"<td>{new_badge}{promoted_badge}"
            f'<a href="{_esc(opp.url)}" target="_blank">{title_display}</a></td>'
            f"<td>{_esc(opp.source)}</td>"
            f"<td>{_esc(opp.issuer[:40])}</td>"
            f"<td>{_esc(opp.state or '--')}</td>"
            f'<td><span class="{conf_class}">{opp.confidence}</span></td>'
            f"<td>{opp.relevance_score}</td>"
            f"<td>{deadline_str}</td>"
            f"<td>{days_html}</td>"
            f"{review_cell}"
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
            '<tr class="manual-empty"><td colspan="11" style="color:#777;font-style:italic;">'
            'No below-threshold candidates for manual review.'
            '</td></tr>'
        )

    source_options = "".join(f'<option value="{_esc(s)}">{_esc(s)}</option>' for s in sources)
    state_options  = "".join(f'<option value="{_esc(s)}">{s}</option>' for s in states)
    emv_nav_class = "nav-link active" if monitor_type == "emv" else "nav-link"
    cx_nav_class = "nav-link active" if monitor_type == "commissioning" else "nav-link"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1.0">
  <title>CxA RFP Monitor - {monitor_label}</title>
  <style>
    *{{box-sizing:border-box;margin:0;padding:0;}}
    body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;
         background:#f0f2f5;color:#333;}}
    .hdr{{background:#1a1a2e;color:#fff;padding:18px 28px;}}
    .hdr h1{{font-size:22px;font-weight:700;}}
    .hdr p{{font-size:13px;opacity:.7;margin-top:4px;}}
    .dash-nav{{display:flex;gap:8px;flex-wrap:wrap;padding:12px 28px;background:#fff;border-bottom:1px solid #e3e6ea;}}
    .nav-link{{display:inline-block;padding:7px 12px;border:1px solid #d7dbe0;border-radius:6px;background:#fff;color:#1a1a2e;font-size:13px;font-weight:600;text-decoration:none;}}
    .nav-link:hover{{background:#f3f5f8;text-decoration:none;}}
    .nav-link.active{{background:#1a1a2e;color:#fff;border-color:#1a1a2e;}}
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
    table{{width:max-content;min-width:100%;border-collapse:collapse;background:#fff;border-radius:8px;
           box-shadow:0 1px 3px rgba(0,0,0,.1);font-size:12px;table-layout:fixed;}}
    th{{background:#1a1a2e;color:#fff;padding:8px 8px;text-align:left;font-size:11px;
        font-weight:600;white-space:nowrap;}}
    td{{padding:8px 8px;border-bottom:1px solid #f0f0f0;vertical-align:top;}}
    th:nth-child(1),td:nth-child(1){{width:360px;min-width:360px;}}
    th:nth-child(2),td:nth-child(2){{width:105px;min-width:105px;}}
    th:nth-child(3),td:nth-child(3){{width:95px;min-width:95px;}}
    th:nth-child(4),td:nth-child(4){{width:48px;min-width:48px;}}
    th:nth-child(5),td:nth-child(5){{width:70px;min-width:70px;}}
    th:nth-child(6),td:nth-child(6){{width:55px;min-width:55px;}}
    th:nth-child(7),td:nth-child(7){{width:92px;min-width:92px;}}
    th:nth-child(8),td:nth-child(8){{width:55px;min-width:55px;}}
    th:nth-child(9),td:nth-child(9){{width:780px;min-width:780px;}}
    th:nth-child(10),td:nth-child(10){{width:160px;min-width:160px;}}
    .manual-table th:nth-child(11),.manual-table td:nth-child(11){{width:38px;min-width:38px;}}
    tr:last-child td{{border-bottom:none;}}
    tr:hover td{{background:#f8f8ff;}}
    .row-new td{{background:#f0fff4;}}
    .badge-new{{background:#2e7d32;color:#fff;font-size:10px;font-weight:700;
                padding:1px 5px;border-radius:3px;}}
    .badge-promoted{{background:#6a1b9a;color:#fff;font-size:10px;font-weight:700;
                    padding:1px 5px;border-radius:3px;white-space:nowrap;}}
    .promoted-manual td{{background:#fff8e1;}}
    #tbody .remove-cell{{display:none;}}
    .conf-high{{color:#2e7d32;font-weight:700;}}
    .conf-med{{color:#e65100;font-weight:600;}}
    .conf-low{{color:#888;}}
    .days-urgent{{color:#c62828;font-weight:700;}}
    .review-cell{{width:780px;min-width:780px;vertical-align:top;}}
    .review-controls{{display:grid;grid-template-columns:.95fr .95fr 1.05fr 1.05fr;gap:7px 10px;font-size:11px;align-items:start;}}
    .review-controls label{{display:flex;flex-direction:column;gap:2px;color:#444;font-weight:600;min-width:0;}}
    .review-controls select,.review-controls input,.review-controls textarea{{font-size:11px;border:1px solid #d7dbe0;border-radius:4px;padding:4px;background:#fff;}}
    .review-controls textarea{{resize:vertical;min-height:38px;font-family:inherit;}}
    .review-checks{{grid-column:1 / -1;display:flex;flex-wrap:wrap;gap:14px;font-size:11px;padding-top:2px;}}
    .review-checks label{{display:inline-flex;flex-direction:row;align-items:center;gap:4px;font-weight:500;}}
    .review-label{{display:inline-flex;align-items:center;gap:4px;white-space:nowrap;}}
    .review-help{{display:inline-block;width:14px;height:14px;line-height:14px;text-align:center;border-radius:50%;background:#e8edf3;color:#445;font-size:10px;font-weight:700;cursor:help;}}
    .deadline-help{{margin-left:4px;background:#fff;color:#1a1a2e;vertical-align:middle;}}
    .review-note{{grid-column:span 2;}}
    .review-save{{grid-column:1 / 2;border:0;background:#1a1a2e;color:#fff;border-radius:4px;padding:5px 8px;font-size:11px;font-weight:700;cursor:pointer;}}
    .review-save:hover{{background:#30304d;}}
    .review-msg{{align-self:center;color:#666;font-size:10px;}}
    @media (max-width:1200px){{
      .review-cell{{width:620px;min-width:620px;}}
      .review-controls{{grid-template-columns:1fr 1fr;}}
      .review-note{{grid-column:span 1;}}
    }}
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
    <h1>CxA RFP Monitor - {monitor_label}
      <span class="mode-badge" title="Current keyword sensitivity mode">
        {mode_label} mode
      </span>
    </h1>
    <p>{monitor_label} Opportunity Dashboard &mdash; Last updated: {run_time}</p>
  </div>

  <nav class="dash-nav" aria-label="Dashboard navigation">
    <a class="nav-link" href="index.html">Home</a>
    <a class="{emv_nav_class}" href="emv.html">EM&amp;V Dashboard</a>
    <a class="{cx_nav_class}" href="commissioning.html">Commissioning / RCx Dashboard</a>
  </nav>

  <div class="stats">
    <div class="stat"><div class="n">{new_cnt}</div><div class="l">New this run</div></div>
    <div class="stat"><div class="n" style="color:#2e7d32">{high_cnt}</div>
      <div class="l">High confidence</div></div>
    <div class="stat"><div class="n" style="color:#e65100">{medium_cnt}</div>
      <div class="l">Medium confidence</div></div>
    <div class="stat"><div class="n" id="total-count">{total_cnt}</div><div class="l">Total active</div></div>
    <div class="stat"><div class="n" id="manual-count">{manual_cnt}</div><div class="l">Manual review</div></div>
  </div>

  <div class="filters">
    <input type="text" id="f-text" placeholder="Search titles..." oninput="applyFilters()">
    <select id="f-conf" onchange="applyFilters()">
      <option value="">All confidence</option>
      <option value="High">High</option>
      <option value="Medium">Medium</option>
      <option value="Low">Low</option>
      <option value="Below threshold">Below threshold</option>
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
          <th>Conf.</th><th>Score</th><th>Deadline <span class="review-help deadline-help" title="Dashboard retention note: RFPs with no listed deadline remain visible for 30 days. RFPs with a listed deadline remain visible until the deadline passes.">i</span></th><th>Days</th>
          <th>Team Review</th><th>Keywords</th>
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
            <th>Conf.</th><th>Score</th><th>Deadline <span class="review-help deadline-help" title="Dashboard retention note: RFPs with no listed deadline remain visible for 30 days. RFPs with a listed deadline remain visible until the deadline passes.">i</span></th><th>Days</th>
            <th>Team Review</th><th>Keywords</th><th></th>
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
    const REVIEW_ENDPOINT = 'https://udxcbyoohgzdkjxytxzg.functions.supabase.co/opportunity-review';

    function getReviewToken(promptText) {{
      let token = localStorage.getItem('rfpAdminToken') || '';
      if (!token) {{
        token = prompt(promptText || 'Enter dashboard edit token');
        if (!token) return '';
        localStorage.setItem('rfpAdminToken', token);
      }}
      return token;
    }}

    function setReviewField(row, selector, value) {{
      const el = row.querySelector(selector);
      if (!el) return;
      if (el.type === 'checkbox') {{
        el.checked = value === true;
      }} else {{
        el.value = value || '';
      }}
    }}

    function getReviewField(row, selector) {{
      const el = row.querySelector(selector);
      if (!el) return '';
      if (el.type === 'checkbox') return !!el.checked;
      return el.value || '';
    }}

    function applyReviewRecord(row, record) {{
      if (!record) return;

      setReviewField(row, '.review-status', record.review_status);
      setReviewField(row, '.reviewer-fit', record.reviewer_fit);
      setReviewField(row, '.review-tech-owner', record.tech_owner);
      setReviewField(row, '.review-admin-owner', record.admin_owner);
      setReviewField(row, '.admin-reviewed', record.admin_reviewed);
      setReviewField(row, '.emv-technical-reviewed', record.emv_technical_reviewed);
      setReviewField(row, '.commissioning-technical-reviewed', record.commissioning_technical_reviewed);
      setReviewField(row, '.technical-review-notes', record.technical_review_notes);
      setReviewField(row, '.admin-review-notes', record.admin_review_notes);

      const msg = row.querySelector('.review-msg');
      if (msg && record.updated_at) {{
        msg.textContent = 'Saved ' + String(record.updated_at).slice(0, 10);
      }}

      applyManualPromotionState(row);
    }}

    function hasReviewText(value) {{
      return String(value || '').trim().length > 0;
    }}

    function rowQualifiesForManualPromotion(row) {{
      if (!row) return false;

      const isManualCandidate =
        row.dataset.manualReview === 'true' || row.dataset.manualPromoted === 'true';
      if (!isManualCandidate) return false;

      // Manual-review section membership is the source of truth here.
      // Manual candidates may display confidence as Low, Below threshold, etc.
      const reviewerFit = getReviewField(row, '.reviewer-fit');
      if (!hasReviewText(reviewerFit)) return false;
      if (reviewerFit.trim().toLowerCase() === 'poor fit') return false;

      if (!hasReviewText(getReviewField(row, '.review-status'))) return false;

      const hasNotes =
        hasReviewText(getReviewField(row, '.technical-review-notes')) ||
        hasReviewText(getReviewField(row, '.admin-review-notes'));
      if (!hasNotes) return false;

      const hasReviewedCheckbox =
        getReviewField(row, '.admin-reviewed') ||
        getReviewField(row, '.emv-technical-reviewed') ||
        getReviewField(row, '.commissioning-technical-reviewed');
      if (!hasReviewedCheckbox) return false;

      const hasOwner =
        hasReviewText(getReviewField(row, '.review-tech-owner')) ||
        hasReviewText(getReviewField(row, '.review-admin-owner'));
      if (!hasOwner) return false;

      return true;
    }}

    function addPromotionBadge(row) {{
      const titleCell = row ? row.querySelector('td') : null;
      if (!titleCell || row.querySelector('.badge-promoted')) return;
      titleCell.insertAdjacentHTML(
        'afterbegin',
        '<span class="badge-promoted">Promoted from Manual Review</span> '
      );
    }}

    function removePromotionBadge(row) {{
      if (!row) return;
      const badge = row.querySelector('.badge-promoted');
      if (badge) badge.remove();
    }}

    function updateManualReviewCounts() {{
      const manualBody = document.getElementById('manual-review-body');
      const mainBody = document.getElementById('tbody');

      if (manualBody) {{
        manualBody.querySelectorAll('tr.manual-empty').forEach(row => row.remove());
      }}

      const manualRows = manualBody
        ? manualBody.querySelectorAll('tr[data-unique-key]')
        : [];
      const mainRows = mainBody
        ? mainBody.querySelectorAll('tr[data-unique-key]')
        : [];

      const manualCount = document.getElementById('manual-count');
      const manualSummaryCount = document.getElementById('manual-summary-count');
      const totalCount = document.getElementById('total-count');

      if (manualCount) manualCount.textContent = manualRows.length;
      if (manualSummaryCount) manualSummaryCount.textContent = manualRows.length;
      if (totalCount) totalCount.textContent = mainRows.length;

      if (manualBody && manualRows.length === 0) {{
        manualBody.innerHTML =
          '<tr class="manual-empty"><td colspan="11" style="color:#777;font-style:italic;">No below-threshold candidates for manual review.</td></tr>';
      }}
    }}

    function matchingReviewRows(container, row) {{
      if (!container || !row) return [];
      const key = row.dataset.reviewKey || '';
      const uniqueKey = row.dataset.uniqueKey || '';
      return Array.from(container.querySelectorAll('tr[data-review-key]')).filter(other => {{
        if (other === row) return false;
        if (key && other.dataset.reviewKey === key) return true;
        if (uniqueKey && other.dataset.uniqueKey === uniqueKey) return true;
        return false;
      }});
    }}

    function ensureManualRemoveCell(row) {{
      if (!row || row.querySelector('.remove-cell')) return;
      row.insertAdjacentHTML(
        'beforeend',
        '<td class="remove-cell"><button type="button" class="remove-btn" title="Hide this manual-review item" onclick="suppressManualReview(this)">x</button></td>'
      );
    }}

    function removeManualRemoveCell(row) {{
      const cell = row ? row.querySelector('.remove-cell') : null;
      if (cell) cell.remove();
    }}

    function applyManualPromotionState(row) {{
      if (!row) return;

      const mainBody = document.getElementById('tbody');
      const manualBody = document.getElementById('manual-review-body');
      const isManualCandidate =
        row.dataset.manualReview === 'true' || row.dataset.manualPromoted === 'true';
      const qualifies = rowQualifiesForManualPromotion(row);

      if (qualifies) {{
        row.dataset.manualReview = row.dataset.manualReview || 'true';
        row.dataset.manualPromoted = 'true';
        row.classList.add('promoted-manual');
        addPromotionBadge(row);
        removeManualRemoveCell(row);

        // Prevent duplicates when a persisted promoted row is already in the main table
        // and the same scraped item also appears in manual review after regeneration.
        const existingMain = matchingReviewRows(mainBody, row).find(other =>
          other.dataset.manualPromoted === 'true' || other.classList.contains('promoted-manual')
        );

        if (existingMain) {{
          // Keep the already-promoted main-table row and do not reinsert a detached/manual duplicate.
          if (row.parentElement) {{
            row.remove();
          }}
          updateManualReviewCounts();
          applyFilters();
          return;
        }} else if (mainBody && row.parentElement !== mainBody) {{
          mainBody.prepend(row);
        }}

        // Remove any remaining duplicate manual rows for the same opportunity.
        matchingReviewRows(manualBody, row).forEach(other => other.remove());
      }} else if (isManualCandidate) {{
        row.dataset.manualReview = 'true';
        row.dataset.manualPromoted = 'false';
        row.classList.remove('promoted-manual');
        removePromotionBadge(row);
        ensureManualRemoveCell(row);

        if (manualBody && row.parentElement !== manualBody) {{
          manualBody.querySelectorAll('tr.manual-empty').forEach(empty => empty.remove());
          manualBody.prepend(row);
        }}
      }}

      updateManualReviewCounts();
      applyFilters();
    }}

    async function loadReviewStatuses() {{
      const rows = Array.from(document.querySelectorAll('tr[data-review-key]'));
      const keys = Array.from(new Set(rows.map(row => row.dataset.reviewKey).filter(Boolean)));

      if (!keys.length) return;

      try {{
        const response = await fetch(REVIEW_ENDPOINT, {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify({{ action: 'list', review_keys: keys }})
        }});

        const result = await response.json().catch(() => ({{}}));

        if (!response.ok) {{
          console.warn('Could not load review fields:', result.error || response.status);
          return;
        }}

        const records = result.records || [];
        const byKey = new Map(records.map(record => [record.review_key, record]));

        rows.forEach(row => {{
          const record = byKey.get(row.dataset.reviewKey);
          if (record) applyReviewRecord(row, record);
        }});
      }} catch (err) {{
        console.warn('Could not load review fields:', err);
      }}
    }}

    async function saveReviewRow(control) {{
      const row = control.closest('tr');
      if (!row) return;

      const token = getReviewToken('Enter dashboard edit token');
      if (!token) return;

      const msg = row.querySelector('.review-msg');
      if (msg) msg.textContent = 'Saving...';

      const payload = {{
        action: 'save',
        review_key: row.dataset.reviewKey || '',
        source: row.dataset.source || '',
        notice_id: row.dataset.noticeId || '',
        title: row.dataset.titleFull || row.dataset.title || '',
        url: row.dataset.url || '',

        monitor_type: '{monitor_type}',
        unique_key: row.dataset.uniqueKey || '',
        confidence: row.dataset.conf || '',
        state: row.dataset.state || '',
        deadline: row.dataset.deadline || '',
        issuer: row.dataset.issuer || row.dataset.source || '',
        relevance_score: row.dataset.score || '0',
        matched_keywords: row.dataset.matchedKeywords || '',
        manual_review: row.dataset.manualReview === 'true' || row.dataset.manualPromoted === 'true',
        manual_promoted: rowQualifiesForManualPromotion(row),

        review_status: getReviewField(row, '.review-status'),
        reviewer_fit: getReviewField(row, '.reviewer-fit'),
        tech_owner: getReviewField(row, '.review-tech-owner'),
        admin_owner: getReviewField(row, '.review-admin-owner'),

        admin_reviewed: getReviewField(row, '.admin-reviewed'),
        emv_technical_reviewed: getReviewField(row, '.emv-technical-reviewed'),
        commissioning_technical_reviewed: getReviewField(row, '.commissioning-technical-reviewed'),

        technical_review_notes: getReviewField(row, '.technical-review-notes'),
        admin_review_notes: getReviewField(row, '.admin-review-notes'),

        updated_by: getReviewField(row, '.review-tech-owner') || getReviewField(row, '.review-admin-owner') || 'dashboard'
      }};

      try {{
        const response = await fetch(REVIEW_ENDPOINT, {{
          method: 'POST',
          headers: {{
            'Content-Type': 'application/json',
            'x-rfp-admin-token': token
          }},
          body: JSON.stringify(payload)
        }});

        const result = await response.json().catch(() => ({{}}));

        if (!response.ok) {{
          if (response.status === 401) {{
            localStorage.removeItem('rfpAdminToken');
            if (msg) msg.textContent = 'Token rejected';
            alert('Dashboard edit token was rejected. Try again with the correct token.');
          }} else {{
            if (msg) msg.textContent = 'Save failed';
            alert('Could not save review fields: ' + (result.error || response.status));
          }}
          return;
        }}

        if (result.record) {{
          document.querySelectorAll('tr[data-review-key]').forEach(otherRow => {{
            if (otherRow.dataset.reviewKey === result.record.review_key) {{
              applyReviewRecord(otherRow, result.record);
            }}
          }});
        }}

        if (msg) msg.textContent = 'Saved';
      }} catch (err) {{
        if (msg) msg.textContent = 'Save failed';
        alert('Could not save review fields. Check network connection and try again.');
      }}
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
        monitor_type: '{monitor_type}',
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
            '<tr><td colspan="11" style="color:#777;font-style:italic;">No below-threshold candidates for manual review.</td></tr>';
        }}
      }} catch (err) {{
        alert('Could not hide item. Check network connection and try again.');
        button.disabled = false;
        button.textContent = 'x';
      }}
    }}

    // Initialize row count on load
    applyFilters();
    loadReviewStatuses();
  </script>
</body>
</html>"""



def generate_landing_page() -> bool:
    """
    Write docs/index.html as a simple landing page linking to the individual
    monitor dashboards.
    """
    try:
        os.makedirs(os.path.dirname(config.DASHBOARD_LANDING_PATH), exist_ok=True)
        html = _render_landing_html()
        with open(config.DASHBOARD_LANDING_PATH, "w", encoding="utf-8") as f:
            f.write(html)
        logger.info(f"Landing page written to {config.DASHBOARD_LANDING_PATH}")
        return True
    except Exception as e:
        logger.error(f"Landing page generation failed: {e}")
        return False


def _render_landing_html() -> str:
    run_time = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
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
    .hdr{{background:#1a1a2e;color:#fff;padding:24px 32px;}}
    .hdr h1{{font-size:24px;font-weight:700;}}
    .hdr p{{font-size:13px;opacity:.75;margin-top:6px;}}
    .wrap{{padding:28px 32px;display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:18px;}}
    .card{{background:#fff;border-radius:10px;padding:22px 24px;box-shadow:0 1px 4px rgba(0,0,0,.12);}}
    .card h2{{font-size:18px;color:#1a1a2e;margin-bottom:8px;}}
    .card p{{font-size:13px;color:#666;line-height:1.5;margin-bottom:14px;}}
    .card a{{display:inline-block;background:#0066cc;color:#fff;text-decoration:none;
             padding:8px 14px;border-radius:5px;font-size:13px;font-weight:600;}}
    .card a:hover{{background:#004f9e;}}
  </style>
</head>
<body>
  <div class="hdr">
    <h1>CxA RFP Monitor</h1>
    <p>Landing page for internal RFP tracking dashboards &mdash; Last updated: {run_time}</p>
  </div>
  <div class="wrap">
    <div class="card">
      <h2>EM&amp;V / Evaluation RFPs</h2>
      <p>Evaluation, measurement and verification, impact evaluation, savings verification, and related program evaluation opportunities.</p>
      <a href="emv.html">Open EM&amp;V Dashboard</a>
    </div>
    <div class="card">
      <h2>Commissioning / RCx RFPs</h2>
      <p>Commissioning, retro-commissioning, MEP commissioning, building enclosure commissioning, testing, and related facility opportunities.</p>
      <a href="commissioning.html">Open Commissioning Dashboard</a>
    </div>
  </div>
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
