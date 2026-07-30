"""
Send the monthly source-health review email after dashboard deployment.

Normal execution is intentionally restricted to the first Monday of an
original scheduled GitHub Actions run. Workflow reruns are skipped to avoid
duplicate monthly messages. ``--force`` exists only for an explicitly
authorized controlled test and is never used by the production workflow.
"""

from __future__ import annotations

import argparse
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Optional

import config
from generate_source_health_dashboard import load_source_health_report_rows
from source_health import get_source_health_supabase_client
from source_health_email import (
    MonthlySourceHealthSummary,
    build_monthly_source_health_summary,
    monthly_source_health_subject,
    render_monthly_source_health_html,
    render_monthly_source_health_text,
)
from source_health_report import EASTERN, parse_timestamp, previous_month_bounds


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MonthlyEmailDecision:
    """Explain whether this invocation may send the monthly notification."""

    should_send: bool
    reason: str


def is_first_monday(reference_time) -> bool:
    """Return True when reference_time falls on days 1-7 and a Monday ET."""
    eastern_time = parse_timestamp(reference_time).astimezone(EASTERN)
    return eastern_time.weekday() == 0 and 1 <= eastern_time.day <= 7


def monthly_email_send_decision(
    reference_time=None,
    *,
    event_name: Optional[str] = None,
    run_attempt: Optional[str] = None,
    force: bool = False,
) -> MonthlyEmailDecision:
    """
    Apply schedule and rerun protections before credentials or data are read.

    GitHub keeps ``event_name`` as ``schedule`` when a scheduled run is
    manually rerun, so GITHUB_RUN_ATTEMPT is also required to prevent a
    duplicate email from attempt 2 or later.
    """
    if force:
        return MonthlyEmailDecision(
            True,
            "Forced by an explicitly supplied command-line option.",
        )

    resolved_event = (
        event_name
        if event_name is not None
        else os.environ.get("GITHUB_EVENT_NAME", "")
    ).strip()
    if resolved_event != "schedule":
        return MonthlyEmailDecision(
            False,
            "Not a scheduled GitHub Actions event.",
        )

    resolved_attempt = str(
        run_attempt
        if run_attempt is not None
        else os.environ.get("GITHUB_RUN_ATTEMPT", "1")
    ).strip()
    if resolved_attempt != "1":
        return MonthlyEmailDecision(
            False,
            f"Workflow rerun attempt {resolved_attempt}; duplicate send blocked.",
        )

    reference_time = reference_time or datetime.now(timezone.utc)
    if not is_first_monday(reference_time):
        return MonthlyEmailDecision(
            False,
            "Current Eastern date is not the first Monday of the month.",
        )

    return MonthlyEmailDecision(
        True,
        "Original scheduled run on the first Monday in Eastern Time.",
    )


def deliver_monthly_source_health_summary(
    summary: MonthlySourceHealthSummary,
    *,
    recipients: Iterable[str] = config.SOURCE_HEALTH_EMAIL_TO,
    api_key: Optional[str] = None,
    sendgrid_client=None,
    mail_factory=None,
) -> bool:
    """
    Render and deliver one copy per configured recipient.

    Client/factory injection keeps deterministic tests entirely local. In
    production, SendGrid is imported lazily only after scheduling, credentials,
    Supabase reads, and summary construction have succeeded.
    """
    recipients = tuple(
        str(recipient).strip()
        for recipient in recipients
        if str(recipient).strip()
    )
    if not recipients:
        logger.error("No source-health email recipients are configured.")
        return False

    api_key = (
        api_key
        if api_key is not None
        else os.environ.get(config.SENDGRID_API_KEY_ENV, "")
    ).strip()
    if not api_key:
        logger.error(
            "%s is not set; monthly source-health email was not sent.",
            config.SENDGRID_API_KEY_ENV,
        )
        return False

    if sendgrid_client is None or mail_factory is None:
        try:
            import sendgrid
            from sendgrid.helpers.mail import Mail
        except ImportError:
            logger.error(
                "sendgrid package is unavailable; install repository "
                "requirements before sending the monthly health email."
            )
            return False

        if sendgrid_client is None:
            sendgrid_client = sendgrid.SendGridAPIClient(api_key=api_key)
        if mail_factory is None:
            mail_factory = Mail

    subject = monthly_source_health_subject(summary)
    plain_text = render_monthly_source_health_text(summary)
    html = render_monthly_source_health_html(summary)
    all_accepted = True

    for recipient in recipients:
        try:
            message = mail_factory(
                from_email=config.EMAIL_FROM,
                to_emails=recipient,
                subject=subject,
                plain_text_content=plain_text,
                html_content=html,
            )
            response = sendgrid_client.client.mail.send.post(
                request_body=message.get()
            )
            if response.status_code == 202:
                logger.info(
                    "Monthly source-health email accepted for %s.",
                    recipient,
                )
            else:
                logger.error(
                    "Unexpected SendGrid status %s for monthly "
                    "source-health recipient %s.",
                    response.status_code,
                    recipient,
                )
                all_accepted = False
        except Exception as exc:
            logger.error(
                "Monthly source-health email to %s failed: %s",
                recipient,
                exc,
            )
            all_accepted = False

    return all_accepted


def send_monthly_source_health_email(
    *,
    reference_time=None,
    force: bool = False,
    event_name: Optional[str] = None,
    run_attempt: Optional[str] = None,
    supabase_client=None,
    sendgrid_client=None,
    mail_factory=None,
) -> bool:
    """
    Apply the send gate, load the previous month, and deliver its summary.

    A skipped invocation is successful because no email was due. A first-Monday
    report with no completed runs is also skipped so launch months and genuine
    data gaps do not produce empty notifications. A scheduled invocation returns
    False if required reads, credentials, rendering, or SendGrid acceptance fail,
    causing the notification step to fail visibly after the dashboard deployed.
    """
    reference_time = reference_time or datetime.now(timezone.utc)
    decision = monthly_email_send_decision(
        reference_time,
        event_name=event_name,
        run_attempt=run_attempt,
        force=force,
    )
    if not decision.should_send:
        logger.info("Monthly source-health email skipped: %s", decision.reason)
        return True

    client = supabase_client or get_source_health_supabase_client()
    if client is None:
        logger.error(
            "Supabase client is unavailable; monthly source-health email "
            "cannot load its report data."
        )
        return False

    period_start, period_end = previous_month_bounds(reference_time)
    rows = load_source_health_report_rows(
        client,
        period_start=period_start,
        period_end=period_end,
    )

    # A monthly notification is useful only when the reporting month contains
    # completed monitor history. Returning True treats an intentional no-data
    # skip as successful, while the already-generated dashboard stays deployed.
    # The check happens before reading SendGrid credentials or rendering content,
    # which guarantees that an empty month cannot reach the email delivery path.
    if not rows.period_runs:
        period_label = period_start.strftime("%B %Y")
        logger.info(
            "Monthly source-health email skipped for %s: no completed "
            "source-health runs were recorded in the reporting month.",
            period_label,
        )
        return True

    api_key = os.environ.get(config.SENDGRID_API_KEY_ENV, "").strip()
    if not api_key:
        logger.error(
            "%s is not set; monthly source-health email cannot run.",
            config.SENDGRID_API_KEY_ENV,
        )
        return False

    summary = build_monthly_source_health_summary(
        rows.period_runs,
        rows.period_records,
        period_start=period_start,
        period_end=period_end,
        history_runs=rows.history_runs,
        history_records=rows.history_records,
    )
    logger.info(
        "Monthly source-health summary ready for %s: %d distinct runs, "
        "%d sources needing review.",
        summary.month_label,
        summary.distinct_runs,
        summary.need_investigation,
    )
    return deliver_monthly_source_health_summary(
        summary,
        api_key=api_key,
        sendgrid_client=sendgrid_client,
        mail_factory=mail_factory,
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Send the previous month's source-health summary on the first "
            "Monday after the dashboard deploys."
        )
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Bypass schedule/rerun checks for an explicitly authorized "
            "controlled send test."
        ),
    )
    parser.add_argument(
        "--reference-time",
        help="Optional ISO time for controlled schedule validation.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    reference_time = (
        parse_timestamp(args.reference_time)
        if args.reference_time
        else datetime.now(timezone.utc)
    )
    succeeded = send_monthly_source_health_email(
        reference_time=reference_time,
        force=args.force,
    )
    return 0 if succeeded else 1


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    raise SystemExit(main())
