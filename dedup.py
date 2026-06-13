"""
dedup.py -- Deduplication and State Persistence via Supabase
=============================================================
Tracks which opportunities have already been reported so repeated
weekly runs don't send the same RFP again.

State is stored in the Supabase table `opportunity_seen` which
you created manually. This avoids needing GitHub write permissions.

On each run:
  1. Load the seen-set from Supabase
  2. Filter new opportunities to only those NOT already in the table
  3. After successful delivery, insert the new ones into Supabase
  4. Supabase handles persistence -- no file commits needed

Table schema (created manually in Supabase):
  monitor_type text   -- e.g. "emv", "commissioning", "rcx"
  unique_key   text   -- primary identifier scoped by monitor_type
  date_found   text   -- YYYY-MM-DD when first seen
  expiry_date  text   -- YYYY-MM-DD when this entry can be deleted
  source       text   -- e.g. "SAM.gov", "NASEO RFP Board"
  title        text   -- truncated title for human reference

KNOWN FAILURE POINTS:
  1. SUPABASE_URL and SUPABASE_KEY must be set in GitHub Secrets.
     If either is missing, the scraper logs a warning and returns an
     empty seen-set -- meaning ALL opportunities will appear as new
     that run (duplicates possible). Check secrets if this happens.
  2. If the Supabase table doesn't exist, inserts will fail with a
     404-style error. Make sure the table name matches exactly:
     opportunity_seen
  3. The supabase-py package must be in requirements.txt. If missing,
     the import will fail and dedup will be skipped entirely.
  4. Supabase free tier has a 500MB database limit. Each row in this
     table is tiny (< 1KB), so even after years of weekly runs this
     won't be an issue.
"""

import json
import logging
import os
from datetime import datetime, timedelta
from typing import Dict, List, Tuple

import config
from models import Opportunity

logger = logging.getLogger(__name__)

# Type alias for the seen-set: unique_key -> row dict
SeenSet = Dict[str, Dict[str, str]]

# Supabase table name -- must match what you created manually
SEEN_TABLE_NAME = "opportunity_seen"
SUPPRESSED_TABLE_NAME = "manual_review_suppressed"
ACTIVE_TABLE_NAME = "opportunity_active"

# Scope this monitor's records so future commissioning/RCx monitors can use the same tables
MONITOR_TYPE = os.environ.get("MONITOR_TYPE", "emv").strip() or "emv"


def _get_supabase_client():
    """
    Create and return a Supabase client using credentials from environment.

    Returns None if credentials are missing or the supabase package
    is not installed -- callers check for None and handle gracefully.

    KNOWN FAILURE POINT: The supabase package changed its import path
    between v1 and v2. We try both import styles to handle either version.
    If you see an ImportError, check which version is installed:
      pip show supabase
    v1: from supabase import create_client
    v2: from supabase import create_client  (same, but different internals)
    """
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_KEY", "").strip()

    if not url or not key:
        logger.warning(
            "SUPABASE_URL or SUPABASE_KEY not set in environment. "
            "Deduplication will be skipped -- all opportunities will "
            "appear as new this run."
        )
        return None

    try:
        from supabase import create_client
        client = create_client(url, key)
        return client
    except ImportError:
        logger.error(
            "supabase package not installed. "
            "Add 'supabase>=2.0.0' to requirements.txt."
        )
        return None
    except Exception as e:
        logger.error(f"Failed to create Supabase client: {e}")
        return None


def load_seen_set() -> SeenSet:
    """
    Load all non-expired entries from the Supabase table into a local dict.

    We load the full table into memory at the start of each run so that
    the dedup check (filter_new_opportunities) is a fast local dict lookup
    rather than a database query per opportunity.

    Returns empty dict if Supabase is unavailable -- this means all
    opportunities will be treated as new for this run.

    KNOWN FAILURE POINT: If the table has grown very large (thousands of
    entries), loading it all into memory could be slow. In practice this
    won't happen -- we expire entries after STATE_EXPIRY_DAYS and a weekly
    run typically adds fewer than 50 rows.
    """
    client = _get_supabase_client()
    if not client:
        return {}

    try:
        # Calculate expiry cutoff -- only load non-expired entries
        cutoff = (
            datetime.utcnow() - timedelta(days=config.STATE_EXPIRY_DAYS)
        ).strftime("%Y-%m-%d")

        # Query all rows where date_found >= cutoff (not yet expired)
        # KNOWN FAILURE POINT: Supabase's .gte() filter on a text column
        # works correctly for YYYY-MM-DD format because ISO dates sort
        # lexicographically in the same order as chronologically.
        response = (
            client.table(SEEN_TABLE_NAME)
            .select("monitor_type, unique_key, date_found, expiry_date, source, title")
            .eq("monitor_type", MONITOR_TYPE)
            .gte("date_found", cutoff)
            .execute()
        )

        # Build the local dict from the returned rows
        seen = {}
        for row in (response.data or []):
            key = row.get("unique_key", "")
            if key:
                seen[key] = row

        logger.info(f"Loaded {len(seen)} entries from Supabase seen-set")
        return seen

    except Exception as e:
        logger.warning(
            f"Failed to load seen-set from Supabase: {e}. "
            f"Dedup skipped -- duplicates may appear this run."
        )
        return {}

def load_suppressed_manual_review_set() -> SeenSet:
    """
    Load manually suppressed below-threshold opportunities from Supabase.

    These records are used only to hide items from the dashboard's
    manual-review section. They do not affect passing opportunities,
    email delivery, or the main seen-set.
    """
    client = _get_supabase_client()
    if not client:
        return {}

    try:
        response = (
            client.table(SUPPRESSED_TABLE_NAME)
            .select("monitor_type, unique_key, suppressed_at, source, title, reason, suppressed_by")
            .eq("monitor_type", MONITOR_TYPE)
            .execute()
        )

        suppressed = {}
        for row in (response.data or []):
            key = row.get("unique_key", "")
            if key:
                suppressed[key] = row

        logger.info(
            f"Loaded {len(suppressed)} entries from Supabase manual-review suppression table"
        )
        return suppressed

    except Exception as e:
        logger.warning(
            f"Failed to load manual-review suppression set from Supabase: {e}. "
            f"Manual-review dashboard items will not be suppressed this run."
        )
        return {}


def filter_suppressed_manual_review(
    opportunities: List[Opportunity],
    suppressed: SeenSet,
) -> List[Opportunity]:
    """
    Remove manually suppressed opportunities from the manual-review list.

    Suppression is scoped by MONITOR_TYPE in the Supabase query, so this
    will not hide items across future commissioning or RCx monitors.
    """
    if not opportunities or not suppressed:
        logger.info(
            f"Manual-review suppression: 0 hidden, {len(opportunities)} remaining"
        )
        return opportunities

    filtered = []
    skipped = 0

    for opp in opportunities:
        if opp.unique_key() in suppressed:
            skipped += 1
            continue
        filtered.append(opp)

    logger.info(
        f"Manual-review suppression: {skipped} hidden, {len(filtered)} remaining"
    )
    return filtered

def save_seen_set(opportunities: List[Opportunity]) -> bool:
    """
    Insert newly reported opportunities into the Supabase table.

    Uses upsert (insert or update) so that if a unique_key already exists
    for any reason, it updates rather than throwing a duplicate key error.

    Args:
        opportunities: Newly delivered opportunities to mark as seen

    Returns:
        True if all inserts succeeded, False if any failed

    KNOWN FAILURE POINT: Supabase's upsert requires the table to have
    a primary key or unique constraint matching the conflict target.
    For this schema, opportunity_seen must have:
    primary key (monitor_type, unique_key).
    """
    if not opportunities:
        logger.info("No opportunities to mark as seen")
        return True

    client = _get_supabase_client()
    if not client:
        return False

    today  = datetime.utcnow().strftime("%Y-%m-%d")
    expiry = (
        datetime.utcnow() + timedelta(days=config.STATE_EXPIRY_DAYS)
    ).strftime("%Y-%m-%d")

    # Build rows to insert
    rows = []
    for opp in opportunities:
        rows.append({
            "monitor_type": MONITOR_TYPE,
            "unique_key": opp.unique_key(),
            "date_found": today,
            "expiry_date": expiry,
            "source": opp.source,
            "title": opp.title[:100],  # Truncate for storage
        })

    try:
        # Upsert: insert new rows, update existing ones on monitor_type + unique_key conflict
        client.table(SEEN_TABLE_NAME).upsert(
            rows,
            on_conflict="monitor_type,unique_key",
        ).execute()
        logger.info(f"Saved {len(rows)} entries to Supabase")
        return True

    except Exception as e:
        logger.error(f"Failed to save seen-set to Supabase: {e}")
        return False


def expire_old_entries() -> int:
    """
    Delete entries from Supabase older than STATE_EXPIRY_DAYS.

    Called at the start of each run to keep the table tidy.
    Returns the number of rows deleted (for logging).

    KNOWN FAILURE POINT: If Supabase is unavailable, this is skipped
    silently. Old entries staying in the table longer than intended is
    harmless -- they just prevent re-reporting of very old opportunities,
    which is the desired behavior anyway.
    """
    client = _get_supabase_client()
    if not client:
        return 0

    cutoff = (
        datetime.utcnow() - timedelta(days=config.STATE_EXPIRY_DAYS)
    ).strftime("%Y-%m-%d")

    try:
        response = (
            client.table(SEEN_TABLE_NAME)
            .delete()
            .eq("monitor_type", MONITOR_TYPE)
            .lt("date_found", cutoff)  # Delete rows older than cutoff
            .execute()
        )
        deleted = len(response.data or [])
        if deleted:
            logger.info(f"Expired {deleted} old entries from Supabase")
        return deleted

    except Exception as e:
        logger.warning(f"Failed to expire old Supabase entries: {e}")
        return 0



def _opportunity_from_active_row(row: dict) -> Opportunity:
    """
    Rebuild an Opportunity object from the JSON payload stored in
    opportunity_active.
    """
    data = row.get("opportunity") or {}
    if isinstance(data, str):
        data = json.loads(data)

    allowed = {
        "source",
        "notice_id",
        "url",
        "title",
        "description",
        "issuer",
        "posted_date",
        "deadline",
        "state",
        "naics_code",
        "set_aside",
        "contact_name",
        "contact_email",
        "contact_phone",
        "relevance_score",
        "matched_keywords",
        "confidence",
        "found_at",
    }

    kwargs = {k: v for k, v in data.items() if k in allowed}

    kwargs.setdefault("source", row.get("source") or "Unknown")
    kwargs.setdefault("notice_id", row.get("unique_key") or "")
    kwargs.setdefault("url", data.get("url") or "")
    kwargs.setdefault("title", row.get("title") or data.get("title") or "Untitled")
    kwargs.setdefault("description", data.get("description") or "")
    kwargs.setdefault("issuer", data.get("issuer") or row.get("source") or "Unknown")

    return Opportunity(**kwargs)


def _active_visible_until(opp: Opportunity, first_seen: str) -> str:
    """
    Determine how long a passing opportunity should stay visible on the
    dashboard.

    If the opportunity has a deadline, keep it visible through that deadline.
    If it has no deadline, keep it visible for 30 days from first_seen.
    """
    if opp.deadline:
        return opp.deadline

    try:
        first_seen_dt = datetime.strptime(first_seen, "%Y-%m-%d")
    except ValueError:
        first_seen_dt = datetime.utcnow()

    return (first_seen_dt + timedelta(days=30)).strftime("%Y-%m-%d")


def upsert_active_dashboard_opportunities(opportunities: List[Opportunity]) -> bool:
    """
    Upsert passing opportunities into Supabase for dashboard persistence.

    This does not affect email deduplication. It only controls what remains
    visible on the dashboard after the first run where an opportunity was found.
    """
    if not opportunities:
        logger.info("No passing opportunities to upsert into active dashboard cache")
        return True

    client = _get_supabase_client()
    if not client:
        logger.warning(
            "Active dashboard cache skipped because Supabase is unavailable."
        )
        return False

    today = datetime.utcnow().strftime("%Y-%m-%d")
    keys = [opp.unique_key() for opp in opportunities]

    try:
        existing_response = (
            client.table(ACTIVE_TABLE_NAME)
            .select("unique_key, first_seen")
            .eq("monitor_type", MONITOR_TYPE)
            .in_("unique_key", keys)
            .execute()
        )

        existing_first_seen = {
            row.get("unique_key"): row.get("first_seen")
            for row in (existing_response.data or [])
            if row.get("unique_key")
        }

        rows = []
        for opp in opportunities:
            key = opp.unique_key()
            first_seen = existing_first_seen.get(key) or today
            visible_until = _active_visible_until(opp, first_seen)

            rows.append({
                "monitor_type": MONITOR_TYPE,
                "unique_key": key,
                "first_seen": first_seen,
                "last_seen": today,
                "visible_until": visible_until,
                "source": opp.source,
                "title": opp.title[:500],
                "deadline": opp.deadline,
                "opportunity": opp.to_dict(),
            })

        (
            client.table(ACTIVE_TABLE_NAME)
            .upsert(rows, on_conflict="monitor_type,unique_key")
            .execute()
        )

        logger.info(
            f"Active dashboard cache: upserted {len(rows)} passing opportunities"
        )
        return True

    except Exception as e:
        logger.warning(f"Failed to upsert active dashboard cache: {e}")
        return False


def load_active_dashboard_opportunities() -> List[Opportunity]:
    """
    Load active dashboard opportunities from Supabase.

    Active means visible_until is today or later. This lets the dashboard keep
    showing already-found RFPs until their due date, or for 30 days if no due
    date exists.
    """
    client = _get_supabase_client()
    if not client:
        logger.warning(
            "Active dashboard cache unavailable because Supabase is unavailable."
        )
        return []

    today = datetime.utcnow().strftime("%Y-%m-%d")

    try:
        response = (
            client.table(ACTIVE_TABLE_NAME)
            .select("unique_key, source, title, deadline, visible_until, opportunity")
            .eq("monitor_type", MONITOR_TYPE)
            .gte("visible_until", today)
            .execute()
        )

        opportunities = []
        for row in (response.data or []):
            try:
                opportunities.append(_opportunity_from_active_row(row))
            except Exception as row_error:
                logger.warning(
                    f"Skipping malformed active dashboard row "
                    f"{row.get('unique_key')}: {row_error}"
                )

        opportunities.sort(
            key=lambda opp: (
                -opp.relevance_score,
                opp.deadline or "9999-12-31",
                opp.source,
                opp.title,
            )
        )

        logger.info(
            f"Active dashboard cache: loaded {len(opportunities)} active opportunities"
        )
        return opportunities

    except Exception as e:
        logger.warning(f"Failed to load active dashboard cache: {e}")
        return []


def merge_active_dashboard_opportunities(
    current: List[Opportunity],
    active: List[Opportunity],
) -> List[Opportunity]:
    """
    Merge current passing opportunities with active cached opportunities.

    Current opportunities win over cached copies so the dashboard uses the
    latest score, deadline, and metadata when the item is still scraped.
    """
    merged = {opp.unique_key(): opp for opp in active}
    for opp in current:
        merged[opp.unique_key()] = opp

    opportunities = list(merged.values())
    opportunities.sort(
        key=lambda opp: (
            -opp.relevance_score,
            opp.deadline or "9999-12-31",
            opp.source,
            opp.title,
        )
    )

    logger.info(
        f"Active dashboard merge: {len(current)} current + "
        f"{len(active)} cached = {len(opportunities)} dashboard opportunities"
    )
    return opportunities

def filter_new_opportunities(
    opportunities: List[Opportunity],
    seen: SeenSet,
) -> Tuple[List[Opportunity], List[Opportunity]]:
    """
    Split opportunities into new (never reported) and already-seen.

    Also filters out opportunities whose deadline has already passed --
    no point reporting something that's already closed.

    Args:
        opportunities: Scored, filtered list from scorer.py
        seen:          Seen-set loaded from Supabase at run start

    Returns:
        (new_opportunities, skipped_opportunities)
    """
    today    = datetime.utcnow().strftime("%Y-%m-%d")
    new_opps = []
    skipped  = []

    for opp in opportunities:
        key = opp.unique_key()

        # Already reported in a previous run
        if key in seen:
            logger.debug(f"Already seen: {opp.title[:60]}")
            skipped.append(opp)
            continue

        # Deadline already passed
        if opp.deadline and opp.deadline < today:
            logger.debug(f"Deadline passed ({opp.deadline}): {opp.title[:60]}")
            skipped.append(opp)
            continue

        new_opps.append(opp)

    logger.info(
        f"Dedup: {len(new_opps)} new, {len(skipped)} skipped "
        f"(already seen or expired)"
    )
    return new_opps, skipped
