"""
Generate the public source-health dashboard from persisted Supabase history.

The command is intentionally read-only with respect to Supabase. It retrieves
only the fields required for aggregation and omits raw diagnostic messages
from the query because the generated dashboard is deployed publicly.

By default, the report covers the previous Eastern calendar month. Monthly
counts use only that period, while the investigation streak uses all saved
history through the period end so a streak does not reset on the first day of
a month.
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

import config
from source_health import get_source_health_supabase_client
from source_health_dashboard import write_source_health_dashboard
from source_health_report import parse_timestamp, previous_month_bounds


logger = logging.getLogger(__name__)

# PostgREST projects commonly cap a response at 1,000 rows. Explicit paging
# prevents a growing history table from being silently truncated.
PAGE_SIZE = 1000

# Raw messages and workflow metadata are deliberately excluded. The renderer
# needs github_run_id only to consolidate the paired EM&V and commissioning
# parent rows created by one GitHub workflow.
RUN_COLUMNS = (
    "id",
    "monitor_type",
    "github_run_id",
    "run_started_at",
    "run_completed_at",
    "total_records",
)
RECORD_COLUMNS = (
    "id",
    "run_id",
    "monitor_type",
    "source_group",
    "source_name",
    "code",
    "candidate_count",
    "recorded_at",
)


@dataclass(frozen=True)
class SourceHealthReportRows:
    """Rows separated into displayed-period data and streak history."""

    period_runs: tuple
    period_records: tuple
    history_runs: tuple
    history_records: tuple


def _fetch_table_rows(
    client,
    *,
    table_name: str,
    columns: Iterable[str],
    timestamp_column: str,
    upper_bound,
    page_size: int = PAGE_SIZE,
) -> list[dict]:
    """
    Fetch every row before an exclusive upper timestamp using stable paging.

    A deterministic secondary ID ordering prevents rows sharing the same
    timestamp from moving between offset pages. Each page constructs a fresh
    query because the Supabase builder is mutable.
    """
    if page_size < 1:
        raise ValueError("page_size must be at least 1")

    upper_bound_utc = parse_timestamp(upper_bound).isoformat()
    selected_columns = ",".join(columns)
    rows = []
    offset = 0

    while True:
        query = (
            client.table(table_name)
            .select(selected_columns)
            .lt(timestamp_column, upper_bound_utc)
            .order(timestamp_column)
            .order("id")
            .range(offset, offset + page_size - 1)
        )
        response = query.execute()
        page = getattr(response, "data", None)
        if page is None:
            raise RuntimeError(
                f"Supabase returned no data payload for {table_name}."
            )
        if not isinstance(page, list):
            raise RuntimeError(
                f"Unexpected Supabase payload type for {table_name}: "
                f"{type(page).__name__}."
            )

        rows.extend(page)
        if len(page) < page_size:
            break
        offset += page_size

    return rows


def _run_completed_at(run: dict):
    """Use completion time for period membership, with start as a fallback."""
    value = run.get("run_completed_at") or run.get("run_started_at")
    if not value:
        raise ValueError(
            f"Source-health run {run.get('id')!r} has no usable timestamp."
        )
    return parse_timestamp(value)


def load_source_health_report_rows(
    client,
    *,
    period_start,
    period_end,
    page_size: int = PAGE_SIZE,
) -> SourceHealthReportRows:
    """
    Load history through period_end and isolate rows for [start, end).

    Parent-run timestamps determine period membership. Detail rows are joined
    locally through run_id, ensuring a record cannot be assigned to a report
    month independently of its parent run.
    """
    start_utc = parse_timestamp(period_start)
    end_utc = parse_timestamp(period_end)
    if start_utc >= end_utc:
        raise ValueError("period_start must be earlier than period_end")

    history_runs = _fetch_table_rows(
        client,
        table_name="source_health_runs",
        columns=RUN_COLUMNS,
        timestamp_column="run_completed_at",
        upper_bound=end_utc,
        page_size=page_size,
    )
    history_records = _fetch_table_rows(
        client,
        table_name="source_health_records",
        columns=RECORD_COLUMNS,
        timestamp_column="recorded_at",
        upper_bound=end_utc,
        page_size=page_size,
    )

    period_runs = [
        run
        for run in history_runs
        if start_utc <= _run_completed_at(run) < end_utc
    ]
    period_run_ids = {
        str(run.get("id") or "")
        for run in period_runs
        if run.get("id")
    }
    period_records = [
        record
        for record in history_records
        if str(record.get("run_id") or "") in period_run_ids
    ]

    return SourceHealthReportRows(
        period_runs=tuple(period_runs),
        period_records=tuple(period_records),
        history_runs=tuple(history_runs),
        history_records=tuple(history_records),
    )


def generate_source_health_dashboard(
    *,
    output_path=config.SOURCE_HEALTH_DASHBOARD_OUTPUT_PATH,
    reference_time: Optional[datetime] = None,
    client=None,
) -> tuple[Path, SourceHealthReportRows]:
    """
    Load the previous Eastern month and write its sanitized static dashboard.

    A client may be injected for deterministic local tests. Production uses
    the same SUPABASE_URL and SUPABASE_KEY client factory as persistence.
    """
    reference_time = reference_time or datetime.now(timezone.utc)
    period_start, period_end = previous_month_bounds(reference_time)

    if client is None:
        client = get_source_health_supabase_client()
    if client is None:
        raise RuntimeError(
            "SUPABASE_URL/SUPABASE_KEY are unavailable or the Supabase "
            "client could not be created."
        )

    rows = load_source_health_report_rows(
        client,
        period_start=period_start,
        period_end=period_end,
    )

    # period_end is exclusive for querying. Subtract a microsecond only for
    # the human-facing label so July is shown as July 1 through July 31 rather
    # than through August 1.
    display_period_end = period_end - timedelta(microseconds=1)
    output = write_source_health_dashboard(
        output_path,
        rows.period_runs,
        rows.period_records,
        history_runs=rows.history_runs,
        history_records=rows.history_records,
        period_start=period_start,
        period_end=display_period_end,
        generated_at=datetime.now(timezone.utc),
    )

    logger.info(
        "Source-health dashboard written to %s: %d period runs, "
        "%d period records, %d historical runs read.",
        output,
        len(rows.period_runs),
        len(rows.period_records),
        len(rows.history_runs),
    )
    return output, rows


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate the previous Eastern calendar month's source-health "
            "dashboard from Supabase."
        )
    )
    parser.add_argument(
        "--output",
        default=config.SOURCE_HEALTH_DASHBOARD_OUTPUT_PATH,
        help="Static HTML output path.",
    )
    parser.add_argument(
        "--reference-time",
        help=(
            "Optional ISO timestamp used to choose the previous month. "
            "Intended for controlled local validation."
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    reference_time = (
        parse_timestamp(args.reference_time)
        if args.reference_time
        else datetime.now(timezone.utc)
    )
    generate_source_health_dashboard(
        output_path=args.output,
        reference_time=reference_time,
    )
    return 0


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    raise SystemExit(main())
