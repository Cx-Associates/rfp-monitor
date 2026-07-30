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
from source_health import (
    HEALTH_ERROR_EXCEPTION,
    HEALTH_OK_NONZERO,
    HEALTH_WARN_PARTIAL,
    HEALTH_WARN_ZERO,
    record_source_health,
)

logger = logging.getLogger(__name__)



def fetch_sam_opportunities() -> List[Opportunity]:
    """
    Run all SAM.gov queries and return deduplicated Opportunity objects.

    In addition to returning opportunities, this records one explicit SAM.gov
    health result that distinguishes:
      - successful API queries that returned candidates;
      - successful API queries that returned zero candidates;
      - partial query/API failures;
      - complete API/configuration failure.
    """
    api_key = os.environ.get("SAM_API_KEY", "").strip()
    if not api_key:
        message = (
            "SAM_API_KEY is not set; no SAM.gov API queries were attempted."
        )
        logger.warning(
            "SAM_API_KEY not set. Skipping SAM.gov source. "
            "Add this key to GitHub Actions Secrets."
        )
        record_source_health(
            source_name="SAM.gov",
            source_group="SAM.gov (Federal)",
            code=HEALTH_ERROR_EXCEPTION,
            candidate_count=None,
            message=message,
        )
        return []

    logger.info("SAM.gov: starting queries...")

    lookback = datetime.utcnow() - timedelta(days=config.SAM_LOOKBACK_DAYS)
    posted_from = lookback.strftime("%m/%d/%Y")
    posted_to = datetime.utcnow().strftime("%m/%d/%Y")

    seen_ids: dict = {}

    # SAM is not one request: a run consists of several keyword and NAICS
    # queries. Track each query outcome so a valid empty response is not
    # confused with a transport/API failure that also produced no rows.
    attempted_queries = 0
    successful_queries = 0
    query_failures = []

    def run_query(params: dict, query_label: str) -> list:
        nonlocal attempted_queries, successful_queries

        attempted_queries += 1
        results, query_succeeded, failure_reason = _query_sam(
            api_key=api_key,
            params=params,
        )
        if query_succeeded:
            successful_queries += 1
        else:
            query_failures.append(
                f"{query_label}: {failure_reason or 'unknown API failure'}"
            )
        return results

    for keyword in config.SAM_SEARCH_QUERIES:
        results = run_query(
            params={
                "keyword": keyword,
                "postedFrom": posted_from,
                "postedTo": posted_to,
                "limit": config.SAM_MAX_RESULTS,
                "ptype": "o,p,k",
            },
            query_label=f"keyword {keyword!r}",
        )
        for item in results:
            notice_id = item.get("noticeId", "")
            if notice_id and notice_id not in seen_ids:
                seen_ids[notice_id] = item

        time.sleep(config.REQUEST_DELAY_SECONDS)

    logger.info(
        f"SAM.gov keyword queries: {len(seen_ids)} unique notices after "
        f"{len(config.SAM_SEARCH_QUERIES)} queries"
    )

    naics_new = 0
    for naics in config.SAM_NAICS_CODES:
        results = run_query(
            params={
                "naics": naics,
                "postedFrom": posted_from,
                "postedTo": posted_to,
                "limit": config.SAM_MAX_RESULTS,
                "ptype": "o,p,k",
            },
            query_label=f"NAICS {naics}",
        )
        for item in results:
            notice_id = item.get("noticeId", "")
            if notice_id and notice_id not in seen_ids:
                seen_ids[notice_id] = item
                naics_new += 1

        time.sleep(config.REQUEST_DELAY_SECONDS)

    logger.info(
        f"SAM.gov NAICS queries: {naics_new} additional notices from "
        f"{len(config.SAM_NAICS_CODES)} NAICS codes"
    )
    logger.info(f"SAM.gov total raw: {len(seen_ids)} unique notices")

    opportunities = []
    parse_failures = 0
    for raw in seen_ids.values():
        opportunity = _parse_opportunity(raw)
        if opportunity:
            opportunities.append(opportunity)
        else:
            parse_failures += 1

    failed_queries = attempted_queries - successful_queries

    # Assign exactly one run-level SAM health code. Complete failure has
    # highest priority, followed by partial query/parser failure. Only a
    # fully successful query set may be called a clean candidate/zero run.
    if successful_queries == 0:
        health_code = HEALTH_ERROR_EXCEPTION
    elif failed_queries > 0 or parse_failures > 0:
        health_code = HEALTH_WARN_PARTIAL
    elif opportunities:
        health_code = HEALTH_OK_NONZERO
    else:
        health_code = HEALTH_WARN_ZERO

    message = (
        f"{attempted_queries} queries attempted; "
        f"{successful_queries} succeeded; "
        f"{failed_queries} failed; "
        f"{len(opportunities)} unique candidates parsed"
    )
    if parse_failures:
        message += f"; {parse_failures} candidate records failed to parse"
    message += "."

    if query_failures:
        examples = "; ".join(query_failures[:3])
        message += f" Failure examples: {examples}"

    # One explicit record per monitor execution gives the monthly report
    # consistent SAM coverage without persisting one row per API query.
    record_source_health(
        source_name="SAM.gov",
        source_group="SAM.gov (Federal)",
        code=health_code,
        candidate_count=(
            len(opportunities) if successful_queries > 0 else None
        ),
        message=message,
    )

    logger.info(f"SAM.gov: {len(opportunities)} opportunities parsed")
    return opportunities


def _query_sam(api_key: str, params: dict) -> tuple:
    """
    Execute one SAM.gov query.

    Returns:
        (items, succeeded, failure_reason)

    `succeeded` means the API returned a valid response containing the
    opportunitiesData field. A valid response containing zero items is still
    successful and is intentionally distinguishable from a failed request.
    """
    query_params = {"api_key": api_key, **params}
    last_failure_reason = "all retries exhausted"

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
                last_failure_reason = "HTTP 429 rate limit"
                logger.warning(
                    f"SAM.gov rate limited (429). "
                    f"Waiting {wait}s (attempt {attempt}/{config.REQUEST_MAX_RETRIES})"
                )
                time.sleep(wait)
                continue

            if response.status_code in (500, 502, 503):
                wait = 30 * attempt
                last_failure_reason = f"HTTP {response.status_code} server error"
                logger.warning(
                    f"SAM.gov server error {response.status_code}. "
                    f"Waiting {wait}s (attempt {attempt}/{config.REQUEST_MAX_RETRIES})"
                )
                time.sleep(wait)
                continue

            response.raise_for_status()
            data = response.json()

            # HTTP 200 alone is not proof of success: SAM sometimes embeds
            # an API error in a JSON body without opportunitiesData.
            if "opportunitiesData" not in data:
                logger.warning(
                    "SAM.gov response missing 'opportunitiesData' key. "
                    f"Possible API error. Response snippet: {str(data)[:300]}"
                )
                return [], False, "valid HTTP response lacked opportunitiesData"

            return data.get("opportunitiesData", []), True, ""

        except requests.exceptions.Timeout:
            last_failure_reason = "request timeout"
            logger.warning(
                f"SAM.gov timeout (attempt {attempt}/{config.REQUEST_MAX_RETRIES})"
            )
            if attempt < config.REQUEST_MAX_RETRIES:
                time.sleep(15)

        except requests.exceptions.ConnectionError as exc:
            logger.warning(f"SAM.gov connection error: {exc}")
            return [], False, "connection error"

        except requests.exceptions.RequestException as exc:
            logger.error(f"SAM.gov unexpected error: {exc}")
            return [], False, f"HTTP request error ({type(exc).__name__})"

        except ValueError as exc:
            logger.error(f"SAM.gov returned invalid JSON: {exc}")
            return [], False, "invalid JSON response"

    logger.error(
        f"SAM.gov: all {config.REQUEST_MAX_RETRIES} retries failed. "
        f"Params: {params}"
    )
    return [], False, last_failure_reason

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
