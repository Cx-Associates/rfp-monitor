"""
source_health.py -- Source Health Tracking for the CxA RFP Monitor
==================================================================

Collects source-level health records during a monitor run. V1 is in-memory
and email-only. A future version can persist these records to Supabase.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

logger = logging.getLogger(__name__)

HEALTH_OK_NONZERO = "HEALTH_OK_NONZERO"
HEALTH_WARN_ZERO = "HEALTH_WARN_ZERO"
HEALTH_WARN_SKIPPED_JS = "HEALTH_WARN_SKIPPED_JS"
HEALTH_ERROR_EXCEPTION = "HEALTH_ERROR_EXCEPTION"
HEALTH_WARN_TOTAL_ZERO = "HEALTH_WARN_TOTAL_ZERO"

_HEALTH_RECORDS = []


@dataclass
class SourceHealthRecord:
    source_name: str
    source_group: str
    code: str
    candidate_count: Optional[int] = None
    message: str = ""
    recorded_at: str = ""


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
        recorded_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
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
        HEALTH_ERROR_EXCEPTION: 0,
        HEALTH_WARN_TOTAL_ZERO: 0,
    }
    for record in records:
        summary[record.code] = summary.get(record.code, 0) + 1
    summary["TOTAL"] = len(records)
    return summary
