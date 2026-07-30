"""
Source-health reporting and aggregation for the CxA RFP Monitor.

Supabase timestamps remain stored in UTC. Human-facing reports use
America/New_York so each timestamp is correctly labeled EST or EDT.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from source_health import (
    HEALTH_ERROR_EXCEPTION,
    HEALTH_OK_NONZERO,
    HEALTH_WARN_PARTIAL,
    HEALTH_WARN_SKIPPED_JS,
    HEALTH_WARN_TOTAL_ZERO,
    HEALTH_WARN_ZERO,
)


EASTERN_TIMEZONE_NAME = "America/New_York"
ZERO_INVESTIGATION_THRESHOLD = 12

# These are report-level statuses, distinct from persisted health codes.
# Multiple EM&V/commissioning codes may collapse into one report status.
STATUS_ERROR = "ERROR"
STATUS_PARTIAL = "PARTIAL"
STATUS_OK = "OK"
STATUS_ZERO = "ZERO"
STATUS_SKIPPED = "SKIPPED"
STATUS_OTHER = "OTHER"


def get_eastern_timezone() -> ZoneInfo:
    try:
        return ZoneInfo(EASTERN_TIMEZONE_NAME)
    except ZoneInfoNotFoundError as exc:
        raise RuntimeError(
            "Eastern timezone data is unavailable. Install the repository "
            "requirements so the tzdata package is present."
        ) from exc


EASTERN = get_eastern_timezone()


def parse_timestamp(value) -> datetime:
    """Parse a Supabase/display timestamp and return an aware UTC datetime."""
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            raise ValueError("Timestamp is empty.")

        if text.endswith(" UTC"):
            parsed = datetime.strptime(text, "%Y-%m-%d %H:%M UTC").replace(
                tzinfo=timezone.utc
            )
        else:
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            parsed = datetime.fromisoformat(text)

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc)


def format_eastern(value, include_timezone: bool = True) -> str:
    eastern_value = parse_timestamp(value).astimezone(EASTERN)
    pattern = "%B %d, %Y at %I:%M %p %Z" if include_timezone else "%B %d, %Y at %I:%M %p"
    return eastern_value.strftime(pattern).replace(" 0", " ")


def previous_month_bounds(reference: Optional[datetime] = None) -> tuple:
    """Return [start, end) boundaries for the previous Eastern calendar month."""
    current = reference or datetime.now(EASTERN)
    if current.tzinfo is None:
        current = current.replace(tzinfo=EASTERN)
    else:
        current = current.astimezone(EASTERN)

    current_month_start = datetime(
        current.year,
        current.month,
        1,
        tzinfo=EASTERN,
    )
    previous_month_day = current_month_start - timedelta(days=1)
    previous_month_start = datetime(
        previous_month_day.year,
        previous_month_day.month,
        1,
        tzinfo=EASTERN,
    )
    return previous_month_start, current_month_start


@dataclass(frozen=True)
class SourceObservation:
    workflow_key: str
    github_run_id: Optional[str]
    source_name: str
    source_group: str
    observed_at: datetime
    status: str
    codes: tuple
    monitors: tuple
    candidate_count: Optional[int]
    messages: tuple


@dataclass(frozen=True)
class SourceSummary:
    source_name: str
    source_group: str
    latest_observed_at: datetime
    latest_status: str
    observations: int
    zero_observations: int
    zero_streak: int
    error_observations: int
    partial_observations: int
    candidate_observations: int
    skipped_observations: int
    needs_investigation: bool
    attention_reason: str


def _classify_codes(codes: Iterable[str]) -> str:
    code_set = set(codes)

    # REVIEWER NOTE: The ordering below is intentional. If paired monitor
    # checks disagree, the more operationally important condition wins.
    # An error/partial result must remain visible even when the other
    # monitor returned candidates moments earlier or later.
    if HEALTH_ERROR_EXCEPTION in code_set:
        return STATUS_ERROR
    if HEALTH_WARN_PARTIAL in code_set:
        return STATUS_PARTIAL
    if HEALTH_OK_NONZERO in code_set:
        return STATUS_OK
    if code_set and code_set <= {HEALTH_WARN_ZERO}:
        return STATUS_ZERO
    if code_set and code_set <= {HEALTH_WARN_SKIPPED_JS}:
        return STATUS_SKIPPED
    return STATUS_OTHER


def build_source_observations(
    runs: Iterable[dict],
    records: Iterable[dict],
) -> list[SourceObservation]:
    """
    Collapse duplicate EM&V/commissioning checks from one GitHub workflow.

    Rows sharing github_run_id, source_group, and source_name become one live-run
    observation. Records without github_run_id remain separate by parent run ID.
    Group-level HEALTH_WARN_TOTAL_ZERO rows are excluded from source streaks.
    """
    runs_by_id = {str(run.get("id")): run for run in runs}
    buckets = {}

    for record in records:
        if record.get("code") == HEALTH_WARN_TOTAL_ZERO:
            continue

        run_id = str(record.get("run_id") or "")
        run = runs_by_id.get(run_id, {})
        github_run_id = str(run.get("github_run_id") or "").strip() or None
        # Both monitors run inside the same scheduled GitHub workflow and
        # therefore share github_run_id. Collapsing on that ID prevents the
        # 12-run threshold from firing after only six scheduled dates.
        # Local rows have no GitHub ID, so their parent UUID remains unique.
        workflow_key = (
            f"github:{github_run_id}" if github_run_id else f"run:{run_id}"
        )
        source_name = str(record.get("source_name") or "Unknown source")
        source_group = str(record.get("source_group") or "Unknown group")
        bucket_key = (workflow_key, source_group, source_name)

        timestamp_value = (
            run.get("run_completed_at")
            or record.get("recorded_at")
            or run.get("run_started_at")
        )
        observed_at = parse_timestamp(timestamp_value)

        bucket = buckets.setdefault(
            bucket_key,
            {
                "workflow_key": workflow_key,
                "github_run_id": github_run_id,
                "source_name": source_name,
                "source_group": source_group,
                "observed_at": observed_at,
                "codes": [],
                "monitors": set(),
                "candidate_counts": [],
                "messages": [],
            },
        )
        if observed_at > bucket["observed_at"]:
            bucket["observed_at"] = observed_at

        code = str(record.get("code") or "")
        if code:
            bucket["codes"].append(code)

        monitor_type = str(
            record.get("monitor_type") or run.get("monitor_type") or ""
        ).strip()
        if monitor_type:
            bucket["monitors"].add(monitor_type)

        candidate_count = record.get("candidate_count")
        if isinstance(candidate_count, int):
            bucket["candidate_counts"].append(candidate_count)

        message = str(record.get("message") or "").strip()
        if message:
            bucket["messages"].append(message)

    observations = []
    for bucket in buckets.values():
        candidate_count = (
            max(bucket["candidate_counts"])
            if bucket["candidate_counts"]
            else None
        )
        observations.append(
            SourceObservation(
                workflow_key=bucket["workflow_key"],
                github_run_id=bucket["github_run_id"],
                source_name=bucket["source_name"],
                source_group=bucket["source_group"],
                observed_at=bucket["observed_at"],
                status=_classify_codes(bucket["codes"]),
                codes=tuple(sorted(set(bucket["codes"]))),
                monitors=tuple(sorted(bucket["monitors"])),
                candidate_count=candidate_count,
                messages=tuple(dict.fromkeys(bucket["messages"])),
            )
        )

    return sorted(
        observations,
        key=lambda item: (
            item.source_group.lower(),
            item.source_name.lower(),
            item.observed_at,
        ),
    )


def summarize_sources(
    observations: Iterable[SourceObservation],
    zero_threshold: int = ZERO_INVESTIGATION_THRESHOLD,
) -> list[SourceSummary]:
    if zero_threshold < 1:
        raise ValueError("zero_threshold must be at least 1")

    by_source = defaultdict(list)
    for observation in observations:
        by_source[
            (observation.source_group, observation.source_name)
        ].append(observation)

    summaries = []
    for (source_group, source_name), source_observations in by_source.items():
        newest_first = sorted(
            source_observations,
            key=lambda item: item.observed_at,
            reverse=True,
        )
        latest = newest_first[0]

        # Count only the current consecutive streak. A candidate-producing,
        # skipped, partial, or error observation ends the zero streak; old
        # historical zeros must not keep a recovered source flagged.
        zero_streak = 0
        for observation in newest_first:
            if observation.status != STATUS_ZERO:
                break
            zero_streak += 1

        counts = Counter(
            observation.status for observation in source_observations
        )
        if latest.status == STATUS_ERROR:
            needs_investigation = True
            attention_reason = "Latest live-run observation is an error."
        elif latest.status == STATUS_PARTIAL:
            needs_investigation = True
            attention_reason = (
                "Some source requests succeeded, but one or more requests or "
                "parsing steps failed; results may be incomplete."
            )
        elif zero_streak >= zero_threshold:
            needs_investigation = True
            attention_reason = (
                f"No results returned in {zero_streak} consecutive live runs "
                f"(investigation threshold: {zero_threshold})."
            )
        else:
            needs_investigation = False
            attention_reason = ""

        summaries.append(
            SourceSummary(
                source_name=source_name,
                source_group=source_group,
                latest_observed_at=latest.observed_at,
                latest_status=latest.status,
                observations=len(source_observations),
                zero_observations=counts[STATUS_ZERO],
                zero_streak=zero_streak,
                error_observations=counts[STATUS_ERROR],
                partial_observations=counts[STATUS_PARTIAL],
                candidate_observations=counts[STATUS_OK],
                skipped_observations=counts[STATUS_SKIPPED],
                needs_investigation=needs_investigation,
                attention_reason=attention_reason,
            )
        )

    return sorted(
        summaries,
        key=lambda item: (
            not item.needs_investigation,
            -item.zero_streak,
            item.source_group.lower(),
            item.source_name.lower(),
        ),
    )


def find_incomplete_runs(
    runs: Iterable[dict],
    records: Iterable[dict],
) -> list[dict]:
    """Find parent runs whose expected detail count does not match persisted rows."""
    # Persistence uses separate parent and detail inserts. Comparing the
    # parent's declared total with actual detail rows detects a detail
    # insert that failed after the parent had already been committed.
    detail_counts = Counter(str(record.get("run_id") or "") for record in records)
    incomplete = []

    for run in runs:
        run_id = str(run.get("id") or "")
        expected = int(run.get("total_records") or 0)
        actual = detail_counts[run_id]
        if expected != actual:
            incomplete.append(
                {
                    "run_id": run_id,
                    "github_run_id": run.get("github_run_id"),
                    "monitor_type": run.get("monitor_type"),
                    "run_completed_at": run.get("run_completed_at"),
                    "expected_records": expected,
                    "actual_records": actual,
                }
            )

    return sorted(
        incomplete,
        key=lambda item: str(item.get("run_completed_at") or ""),
        reverse=True,
    )
