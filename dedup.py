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
