"""
models.py -- Shared Data Structures for the CxA RFP Monitor
============================================================
Defines the Opportunity dataclass that every scraper and source
produces, so all downstream code (scoring, dedup, delivery) works
with a single consistent shape regardless of where the data came from.

Also contains date normalization and text cleaning utilities used
across multiple modules.
"""

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class Opportunity:
    """
    A single RFP / solicitation found from any source.

    All scrapers normalize their output into this structure before
    passing it to the scoring and delivery layers. Fields are strings
    unless typed otherwise; Optional fields may be None if the source
    doesn't provide that data.
    """

    # -- Identity --
    source: str        # Human-readable source name, e.g. "SAM.gov", "NYSERDA"
    notice_id: str     # Unique ID within the source (used for dedup)
    url: str           # Direct link to the posting

    # -- Content --
    title: str
    description: str   # Body/summary text (may be truncated by scraper)
    issuer: str        # Issuing agency or organization

    # -- Dates (ISO format: YYYY-MM-DD) --
    posted_date: Optional[str] = None
    deadline: Optional[str]    = None

    # -- Classification --
    state: Optional[str]       = None   # Two-letter state code, or "Federal"
    naics_code: Optional[str]  = None
    set_aside: Optional[str]   = None

    # -- Contact --
    contact_name:  Optional[str] = None
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None

    # -- Scoring (populated by scorer.py, not by scrapers) --
    relevance_score:  int  = 0
    matched_keywords: list = field(default_factory=list)
    confidence:       str  = "Unknown"

    # -- Internal --
    found_at: str = field(
        default_factory=lambda: datetime.utcnow().isoformat()
    )

    # -----------------------------------------------------------------------
    # Methods
    # -----------------------------------------------------------------------

    def unique_key(self) -> str:
        """
        Stable deduplication key: source + notice_id.

        Falls back to a hash of source + URL when notice_id is empty
        (some scrapers can't extract a formal notice ID from the page).

        KNOWN FAILURE POINT: If a source re-issues the same RFP under a
        new notice ID (e.g., after a major amendment), it will re-appear
        as new. This is intentional -- amendments can change scope and
        deadline meaningfully.
        """
        if self.notice_id:
            return f"{self.source}::{self.notice_id}"
        url_hash = hashlib.md5(
            f"{self.source}::{self.url}".encode("utf-8")
        ).hexdigest()[:12]
        return f"{self.source}::url-{url_hash}"

    def days_until_deadline(self) -> Optional[int]:
        """
        Days from today to deadline. Negative = already past.
        Returns None if deadline is unknown.
        """
        if not self.deadline:
            return None
        try:
            deadline_dt = datetime.strptime(self.deadline, "%Y-%m-%d")
            today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
            return (deadline_dt - today).days
        except ValueError:
            return None

    def is_expired(self) -> bool:
        """
        True if deadline has passed. Opportunities with no deadline are
        treated as not expired (we don't want to suppress them).

        KNOWN FAILURE POINT: Deadlines scraped without a time component
        are treated as end-of-day. There is a ~24 hour window where an
        opportunity is technically closed but still listed as active here.
        """
        days = self.days_until_deadline()
        return days is not None and days < 0

    def to_dict(self) -> dict:
        """Serialize to a plain dict for JSON storage and HTML templating."""
        return {
            "source": self.source,
            "notice_id": self.notice_id,
            "url": self.url,
            "title": self.title,
            "description": self.description,
            "issuer": self.issuer,
            "posted_date": self.posted_date,
            "deadline": self.deadline,
            "state": self.state,
            "naics_code": self.naics_code,
            "set_aside": self.set_aside,
            "contact_name": self.contact_name,
            "contact_email": self.contact_email,
            "contact_phone": self.contact_phone,
            "relevance_score": self.relevance_score,
            "matched_keywords": self.matched_keywords,
            "confidence": self.confidence,
            "found_at": self.found_at,
            "unique_key": self.unique_key(),
        }

    def __repr__(self):
        return (
            f"<Opportunity [{self.confidence}|{self.relevance_score}pts] "
            f"source={self.source!r} title={self.title[:60]!r}>"
        )


# ---------------------------------------------------------------------------
# Shared utility functions
# ---------------------------------------------------------------------------

def normalize_date(raw: str) -> Optional[str]:
    """
    Parse a date string in any common format and return ISO "YYYY-MM-DD".
    Returns None if the string can't be parsed.

    Handles the date formats seen across SAM.gov, state portals, and
    utility pages. Add new format strings here as you encounter them.

    KNOWN FAILURE POINT: Locale-specific formats like "1st April 2026"
    are not handled. If you see unparsed dates showing up in logs, add
    the format string to the list below.
    """
    if not raw:
        return None

    # Strip whitespace and trailing punctuation
    raw = raw.strip().rstrip(".")

    # Try formats in order of frequency across observed sources
    formats = [
        "%Y-%m-%d",      # ISO: 2026-05-01
        "%m/%d/%Y",      # US: 05/01/2026
        "%m/%d/%y",      # US short: 05/01/26
        "%B %d, %Y",     # Long: May 01, 2026
        "%b %d, %Y",     # Short: May 01, 2026
        "%d %B %Y",      # Day-first long: 01 May 2026
        "%d-%b-%Y",      # Day-dash: 01-May-2026
        "%Y%m%d",        # Compact: 20260501
    ]
    for fmt in formats:
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue

    # Fallback: extract year, month, day via regex
    # Catches non-standard separators like 2026/05/01 or 2026.05.01
    m = re.search(r"(\d{4})[^\d](\d{1,2})[^\d](\d{1,2})", raw)
    if m:
        y, mo, d = m.groups()
        try:
            return datetime(int(y), int(mo), int(d)).strftime("%Y-%m-%d")
        except ValueError:
            pass

    return None


def clean_text(raw: str, max_length: int = 2000) -> str:
    """
    Clean scraped text: collapse whitespace, decode common HTML entities,
    and truncate to max_length characters at a word boundary.

    KNOWN FAILURE POINT: BeautifulSoup's .get_text() occasionally leaves
    JavaScript fragments or CSS snippets if a page has unusual structure.
    If you see garbled descriptions, add source-specific cleanup in the
    relevant scraper file.
    """
    if not raw:
        return ""

    # Collapse all whitespace (spaces, tabs, newlines) to single spaces
    cleaned = re.sub(r"\s+", " ", raw).strip()

    # Decode common HTML entity residue that BS4 sometimes misses
    cleaned = (
        cleaned
        .replace("&amp;", "&")
        .replace("&nbsp;", " ")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&#39;", "'")
        .replace("&quot;", '"')
    )

    # Truncate at a word boundary
    if len(cleaned) > max_length:
        truncated = cleaned[:max_length]
        last_space = truncated.rfind(" ")
        if last_space > max_length * 0.8:
            truncated = truncated[:last_space]
        cleaned = truncated + "..."

    return cleaned
