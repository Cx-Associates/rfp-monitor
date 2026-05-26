"""
scrapers/sam_gov.py -- SAM.gov Federal Opportunities Scraper
=============================================================
Queries the SAM.gov Opportunities API (v2) for recent solicitations
matching EM&V-related keywords and NAICS codes.

API documentation: https://open.gsa.gov/api/opportunities-api/

Authentication:
  Requires SAM_API_KEY environment variable.
  Obtain free key: https://sam.gov/profile/details

Rate limits:
  - Public key (no entity registration): 10 requests/day
  - Registered entity: 1,000 requests/day
  The scraper runs ~15 queries (11 keyword + 4 NAICS). Use an entity-
  registered key for the production GitHub Actions deployment.

KNOWN FAILURE POINTS:
  1. SAM.gov title-only search limitation: The "keyword" parameter searches
     only the notice TITLE, not description or attachment text. EM&V RFPs
     with generic titles ("Technical Support Services") are missed. The
     NAICS code queries partially compensate.
  2. Rate limiting: HTTP 429 triggers exponential backoff. If the public
     key (10/day) is exhausted mid-run, remaining queries are skipped.
  3. Schema drift: SAM.gov has changed field names without version bumps.
     Field names to watch: "title", "noticeId", "responseDeadLine",
     "fullParentPathName". See _parse_opportunity() comments.
  4. "opportunitiesData" key missing in response: SAM.gov returns HTTP 200
     even on some API errors, embedding the error in the JSON body.
     We check for the key explicitly and log the raw response on failure.
"""

import logging
import os
import time
from datetime import datetime, timedelta
from typing import List, Optional

import requests

import config
from models import Opportunity, normalize_date, clean_text

logger = logging.getLogger(__name__)


def fetch_sam_opportunities() -> List[Opportunity]:
    """
    Run all SAM.gov queries (keyword + NAICS) and return a deduplicated
    list of Opportunity objects.

    Returns an empty list (not an exception) if the API key is missing or
    the API is unavailable, allowing the rest of the run to continue.
    """
    api_key = os.environ.get("SAM_API_KEY", "").strip()
    if not api_key:
        logger.warning(
            "SAM_API_KEY not set. Skipping SAM.gov source. "
            "Add this key to GitHub Actions Secrets."
        )
        return []

    logger.info("SAM.gov: starting queries...")

    # Calculate date range for this run
    lookback = datetime.utcnow() - timedelta(days=config.SAM_LOOKBACK_DAYS)
    posted_from = lookback.strftime("%m/%d/%Y")          # SAM expects MM/DD/YYYY
    posted_to   = datetime.utcnow().strftime("%m/%d/%Y")

    # Collect raw API result dicts, keyed by noticeId to deduplicate
    # across multiple queries
    seen_ids: dict = {}

    # -----------------------------------------------------------------------
    # Query set 1: Keyword-based searches
    # Each query hits the title field of all recent solicitations
    # -----------------------------------------------------------------------
    for keyword in config.SAM_SEARCH_QUERIES:
        results = _query_sam(
            api_key=api_key,
            params={
                "keyword":     keyword,
                "postedFrom":  posted_from,
                "postedTo":    posted_to,
                "limit":       config.SAM_MAX_RESULTS,
                "ptype":       "o,p,k",   # Solicitations, presolicitations, combined
            },
        )
        for item in results:
            nid = item.get("noticeId", "")
            if nid and nid not in seen_ids:
                seen_ids[nid] = item

        # Polite delay between API calls
        time.sleep(config.REQUEST_DELAY_SECONDS)

    logger.info(
        f"SAM.gov keyword queries: {len(seen_ids)} unique notices after "
        f"{len(config.SAM_SEARCH_QUERIES)} queries"
    )

    # -----------------------------------------------------------------------
    # Query set 2: NAICS code searches
    # Catches EM&V RFPs with non-descriptive titles by filtering on service
    # category. Returns all recent solicitations under each NAICS code,
    # letting the scorer filter for EM&V relevance.
    # -----------------------------------------------------------------------
    naics_new = 0
    for naics in config.SAM_NAICS_CODES:
        results = _query_sam(
            api_key=api_key,
            params={
                "naics":      naics,
                "postedFrom": posted_from,
                "postedTo":   posted_to,
                "limit":      config.SAM_MAX_RESULTS,
                "ptype":      "o,p,k",
            },
        )
        for item in results:
            nid = item.get("noticeId", "")
            if nid and nid not in seen_ids:
                seen_ids[nid] = item
                naics_new += 1

        time.sleep(config.REQUEST_DELAY_SECONDS)

    logger.info(
        f"SAM.gov NAICS queries: {naics_new} additional notices from "
        f"{len(config.SAM_NAICS_CODES)} NAICS codes"
    )
    logger.info(f"SAM.gov total raw: {len(seen_ids)} unique notices")

    # Parse raw dicts into Opportunity objects
    opportunities = []
    for notice_id, raw in seen_ids.items():
        opp = _parse_opportunity(raw)
        if opp:
            opportunities.append(opp)

    logger.info(f"SAM.gov: {len(opportunities)} opportunities parsed")
    return opportunities


def _query_sam(api_key: str, params: dict) -> list:
    """
    Execute one query against the SAM.gov v2 search endpoint.

    Retries on transient HTTP errors (429, 500-503) with exponential backoff.
    Returns an empty list on persistent failure rather than raising.

    KNOWN FAILURE POINTS:
      - HTTP 429: rate limit hit. Backoff waits 60/120/180 seconds.
        If this happens frequently, switch to an entity-registered key.
      - HTTP 200 with error JSON: SAM.gov sometimes returns 200 with an error
        embedded in the body (no "opportunitiesData" key). We log the raw
        response body on this condition for debugging.
      - Connection timeout: Some SAM.gov endpoints are slow under load.
        REQUEST_TIMEOUT in config.py limits individual request wait time.
    """
    query_params = {"api_key": api_key, **params}

    for attempt in range(1, config.REQUEST_MAX_RETRIES + 1):
        try:
            response = requests.get(
                config.SAM_API_BASE_URL,
                params=query_params,
                headers=config.REQUEST_HEADERS,
                timeout=config.REQUEST_TIMEOUT,
            )

            if response.status_code == 429:
                wait = 60 * attempt
                logger.warning(
                    f"SAM.gov rate limited (429). "
                    f"Waiting {wait}s (attempt {attempt}/{config.REQUEST_MAX_RETRIES})"
                )
                time.sleep(wait)
                continue

            if response.status_code in (500, 502, 503):
                wait = 30 * attempt
                logger.warning(
                    f"SAM.gov server error {response.status_code}. "
                    f"Waiting {wait}s (attempt {attempt}/{config.REQUEST_MAX_RETRIES})"
                )
                time.sleep(wait)
                continue

            response.raise_for_status()
            data = response.json()

            # Detect error-in-200 condition (SAM.gov quirk)
            # KNOWN FAILURE POINT: The error structure has changed across
            # API versions. If this check starts missing errors, log
            # response.text and inspect the actual structure.
            if "opportunitiesData" not in data:
                logger.warning(
                    f"SAM.gov response missing 'opportunitiesData' key. "
                    f"Possible API error. Response snippet: {str(data)[:300]}"
                )
                return []

            return data.get("opportunitiesData", [])

        except requests.exceptions.Timeout:
            logger.warning(
                f"SAM.gov timeout (attempt {attempt}/{config.REQUEST_MAX_RETRIES})"
            )
            if attempt < config.REQUEST_MAX_RETRIES:
                time.sleep(15)

        except requests.exceptions.ConnectionError as e:
            logger.warning(f"SAM.gov connection error: {e}")
            return []   # Connection errors are unlikely to be transient; don't retry

        except requests.exceptions.RequestException as e:
            logger.error(f"SAM.gov unexpected error: {e}")
            return []

    logger.error(
        f"SAM.gov: all {config.REQUEST_MAX_RETRIES} retries failed. "
        f"Params: {params}"
    )
    return []


def _parse_opportunity(raw: dict) -> Optional[Opportunity]:
    """
    Convert one raw SAM.gov API result dict into an Opportunity.

    KNOWN FAILURE POINTS (field name drift history):
      - "title" was returned as "solicitationTitle" in some early v2 responses.
        Added fallback below.
      - "pointOfContact" is an array; we take index 0 (primary contact).
        Some notices have no contacts at all.
      - "responseDeadLine" may be null for pre-solicitations and awards.
      - "fullParentPathName" is a dot-separated org hierarchy. We take the
        last segment as the issuer name. If missing, we try "organizationHierarchy".
      - "description" in search results is a SHORT EXCERPT (50-200 chars),
        not the full text. This limits secondary keyword matching because
        the description text is too truncated to be reliable. Title matching
        is therefore the most important signal for SAM.gov results.
    """
    try:
        notice_id = raw.get("noticeId", "")

        # Field name fallback for "title" (SAM.gov drift protection)
        title = (
            raw.get("title", "")
            or raw.get("solicitationTitle", "")
            or raw.get("solicitationNumber", "")
        ).strip()

        if not title or title.lower() in ("n/a", "none", ""):
            return None

        # Build the direct SAM.gov URL for this notice
        url = (
            f"https://sam.gov/opp/{notice_id}/view"
            if notice_id
            else raw.get("uiLink", "")
        )

        # Parse issuer from the org hierarchy path
        full_path = raw.get("fullParentPathName", "")
        if full_path:
            # Take the most specific (deepest) segment of the org path
            issuer = full_path.split(".")[-1].strip().title()
        else:
            # Fallback: try the organizationHierarchy array
            org_hierarchy = raw.get("organizationHierarchy", [])
            if org_hierarchy and isinstance(org_hierarchy, list):
                issuer = org_hierarchy[-1].get("name", "Unknown Agency")
            else:
                issuer = "Unknown Federal Agency"

        # Extract NAICS code (may be a string or a list of dicts)
        naics_raw = raw.get("naicsCode", "")
        if isinstance(naics_raw, list) and naics_raw:
            naics_code = str(naics_raw[0].get("code", ""))
        else:
            naics_code = str(naics_raw)

        # Extract state from place of performance
        # KNOWN FAILURE POINT: Nationwide contracts have no state code.
        # We default to "Federal" which is clearly not a state -- easy to filter.
        pop = raw.get("placeOfPerformance") or {}
        state_code = (pop.get("state") or {}).get("code") or "Federal"

        # Extract primary point of contact
        contacts = raw.get("pointOfContact") or []
        contact = contacts[0] if contacts else {}

        return Opportunity(
            source="SAM.gov",
            notice_id=notice_id,
            url=url,
            title=title,
            description=clean_text(raw.get("description", "")),
            issuer=issuer,
            posted_date=normalize_date(raw.get("postedDate", "")),
            deadline=normalize_date(raw.get("responseDeadLine", "")),
            state=state_code,
            naics_code=naics_code,
            set_aside=raw.get("typeOfSetAside", ""),
            contact_name=contact.get("fullName", ""),
            contact_email=contact.get("email", ""),
            contact_phone=contact.get("phone", ""),
        )

    except Exception as e:
        logger.warning(
            f"SAM.gov parse error (skipping record): {e}. "
            f"Raw snippet: {str(raw)[:200]}"
        )
        return None
