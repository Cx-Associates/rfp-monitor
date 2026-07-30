"""
source_health.py -- Source Health Tracking for the CxA RFP Monitor
==================================================================

Collects source-level health records during a monitor run and can persist
those records to Supabase for historical tracking.
"""

import logging
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

logger = logging.getLogger(__name__)

HEALTH_OK_NONZERO = "HEALTH_OK_NONZERO"
# Health codes are persisted as text rather than a database enum so new
# operational states can be added without a schema migration. PARTIAL is
# used when a multi-query source (currently SAM.gov) returns a mixture of
# valid responses and failures; it must not be counted as a full success.
HEALTH_WARN_ZERO = "HEALTH_WARN_ZERO"
HEALTH_WARN_SKIPPED_JS = "HEALTH_WARN_SKIPPED_JS"
HEALTH_WARN_PARTIAL = "HEALTH_WARN_PARTIAL"
HEALTH_ERROR_EXCEPTION = "HEALTH_ERROR_EXCEPTION"
HEALTH_WARN_TOTAL_ZERO = "HEALTH_WARN_TOTAL_ZERO"

SOURCE_HEALTH_RUNS_TABLE = "source_health_runs"
SOURCE_HEALTH_RECORDS_TABLE = "source_health_records"

_HEALTH_RECORDS = []


@dataclass
class SourceHealthRecord:
    source_name: str
    source_group: str
    code: str
    candidate_count: Optional[int] = None
    message: str = ""
    recorded_at: str = ""


def _utc_now_for_display() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _recorded_at_for_supabase(recorded_at: str) -> str:
    """
    Convert the existing display timestamp format to an ISO timestamp for Supabase.

    Existing source-health email rendering expects recorded_at to remain a readable
    string like '2026-07-28 15:40 UTC', so record_source_health keeps that format.
    This helper only normalizes the value when persisting to timestamptz columns.
    """
    if not recorded_at:
        return _utc_now_iso()

    try:
        return (
            datetime.strptime(recorded_at, "%Y-%m-%d %H:%M UTC")
            .replace(tzinfo=timezone.utc)
            .isoformat()
        )
    except ValueError:
        return recorded_at


def record_source_health(
    source_name: str,
    source_group: str,
    code: str,
    candidate_count: Optional[int] = None,
    message: str = "",
) -> None:
    record = SourceHealthRecord(
        source_name=source_name,
        source_group=source_group,
        code=code,
        candidate_count=candidate_count,
        message=message,
        recorded_at=_utc_now_for_display(),
    )
    _HEALTH_RECORDS.append(record)

    count_text = "" if candidate_count is None else f" | count={candidate_count}"
    logger.info(
        f"SOURCE_HEALTH {code} | {source_group} | {source_name}"
        f"{count_text} | {message}"
    )


def get_source_health_records() -> List[SourceHealthRecord]:
    return list(_HEALTH_RECORDS)


def clear_source_health_records() -> None:
    _HEALTH_RECORDS.clear()


def summarize_source_health(records: Optional[List[SourceHealthRecord]] = None) -> dict:
    records = records if records is not None else get_source_health_records()
    summary = {
        HEALTH_OK_NONZERO: 0,
        HEALTH_WARN_ZERO: 0,
        HEALTH_WARN_SKIPPED_JS: 0,
        HEALTH_WARN_PARTIAL: 0,
        HEALTH_ERROR_EXCEPTION: 0,
        HEALTH_WARN_TOTAL_ZERO: 0,
    }
    for record in records:
        summary[record.code] = summary.get(record.code, 0) + 1
    summary["TOTAL"] = len(records)
    return summary


def _get_supabase_client():
    """
    Create a Supabase client for source-health persistence.

    This mirrors the repo's existing SUPABASE_URL / SUPABASE_KEY environment
    pattern, so no new GitHub Actions secrets are required.
    """
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_KEY", "").strip()

    if not url or not key:
        logger.warning(
            "SUPABASE_URL or SUPABASE_KEY not set. "
            "Source-health persistence will be skipped."
        )
        return None

    try:
        from supabase import create_client
        return create_client(url, key)

    except ImportError:
        logger.error(
            "supabase package not installed. "
            "Add 'supabase>=2.0.0' to requirements.txt."
        )
        return None

    except Exception as e:
        logger.error(f"Failed to create Supabase client for source health: {e}")
        return None


def get_source_health_supabase_client():
    """Return the configured client used by persistence and reporting.

    Keeping one client factory ensures dashboard reads use the same
    SUPABASE_URL/SUPABASE_KEY validation and dependency handling as
    source-health writes. The wrapper is public so reporting code does
    not depend directly on the private implementation helper.
    """
    return _get_supabase_client()


def _health_level_counts(summary: dict) -> tuple:
    error_count = summary.get(HEALTH_ERROR_EXCEPTION, 0)
    warn_count = (
        summary.get(HEALTH_WARN_ZERO, 0)
        + summary.get(HEALTH_WARN_SKIPPED_JS, 0)
        + summary.get(HEALTH_WARN_PARTIAL, 0)
        + summary.get(HEALTH_WARN_TOTAL_ZERO, 0)
    )
    ok_count = summary.get(HEALTH_OK_NONZERO, 0)
    return ok_count, warn_count, error_count


def persist_source_health_records(
    records: Optional[List[SourceHealthRecord]] = None,
    monitor_type: Optional[str] = None,
    mode: Optional[str] = None,
    run_started_at: Optional[str] = None,
) -> bool:
    """
    Persist source-health records to Supabase.

    This should be called once per non-dry monitor run. It is intentionally
    nonfatal: failed health persistence should not prevent email delivery,
    dashboard generation, or seen-state saves.
    """
    records = records if records is not None else get_source_health_records()
    monitor_type = (monitor_type or os.environ.get("MONITOR_TYPE", "emv")).strip() or "emv"
    mode = (mode or "").strip() or None

    client = _get_supabase_client()
    if not client:
        return False

    summary = summarize_source_health(records)
    ok_count, warn_count, error_count = _health_level_counts(summary)

    run_id = str(uuid.uuid4())
    now_iso = _utc_now_iso()
    run_started_at_iso = (
        _recorded_at_for_supabase(run_started_at)
        if run_started_at
        else now_iso
    )

    run_row = {
        "id": run_id,
        "monitor_type": monitor_type,
        "mode": mode,
        "github_run_id": os.environ.get("GITHUB_RUN_ID"),
        "github_run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
        "github_workflow": os.environ.get("GITHUB_WORKFLOW"),
        "run_started_at": run_started_at_iso,
        "run_completed_at": now_iso,
        "total_records": len(records),
        "ok_count": ok_count,
        "warn_count": warn_count,
        "error_count": error_count,
        "summary": summary,
    }

    record_rows = [
        {
            "run_id": run_id,
            "monitor_type": monitor_type,
            "source_group": record.source_group,
            "source_name": record.source_name,
            "code": record.code,
            "candidate_count": record.candidate_count,
            "message": record.message,
            "recorded_at": _recorded_at_for_supabase(record.recorded_at),
        }
        for record in records
    ]

    try:
        client.table(SOURCE_HEALTH_RUNS_TABLE).insert(run_row).execute()

        if record_rows:
            client.table(SOURCE_HEALTH_RECORDS_TABLE).insert(record_rows).execute()

        logger.info(
            f"Persisted source health run {run_id}: "
            f"{len(record_rows)} records "
            f"({ok_count} ok, {warn_count} warnings, {error_count} errors)"
        )
        return True

    except Exception as e:
        logger.warning(f"Failed to persist source-health records to Supabase: {e}")
        return False
