"""
scrapers/google_cse.py -- Google Custom Search Engine Scraper
=============================================================
Uses the Google Custom Search JSON API to query across ~25 state
procurement portal domains simultaneously. This gives broad US state
coverage without maintaining 50 individual HTML scrapers.

WHY GOOGLE CSE INSTEAD OF DIRECT SCRAPING?
  State procurement portals are individually fragile to scrape:
    - Different HTML structures (tables, lists, CMS widgets)
    - Frequent redesigns that silently break scrapers
    - JavaScript rendering requirements on many modern portals
    - Form-based search that can't be replicated with simple GET requests
    - Login requirements on some states for full listings

  Google has already crawled and indexed these pages. A CSE query
  returns keyword-matched results across all configured domains at once,
  and the result structure is stable (Google's API is versioned).

SETUP (one-time):
  1. Go to https://programmablesearchengine.google.com/
  2. Click "Add" to create a new search engine
  3. Under "Sites to search", add each domain from
     config.STATE_PORTAL_DOMAINS_FOR_CSE (one per line)
  4. Copy the "Search engine ID" (also called "cx")
  5. Go to console.cloud.google.com > APIs > Enable "Custom Search API"
  6. Create an API key under Credentials
  7. Add GOOGLE_CSE_KEY and GOOGLE_CSE_ID to GitHub Actions Secrets

RATE LIMITS:
  - Free tier: 100 queries/day (10 results per query)
  - $5 per 1,000 additional queries if you exceed the free tier
  - We issue one API call per search query string (8 queries by default)
  - Total: 8 API calls per weekly run -- well within the free tier

KNOWN FAILURE POINTS:
  1. Google CSE only returns pages that Google has indexed. Newly posted
     RFPs may not appear until Google's crawler indexes the page (hours to
     days). SAM.gov catches federal opportunities in real time; CSE catches
     state portals with a slight lag.
  2. Some state portal pages are JavaScript-rendered and may not be fully
     indexed by Google. The portal pages that list individual RFPs (rather
     than just the search interface) are usually static enough to index.
  3. Google's indexing of government portals is not guaranteed -- some
     portals block Googlebot. Supplement with direct scraping for the
     most important states (see scrapers/state_portals.py).
  4. CSE free tier pauses if you exceed 100 queries/day. The workflow
     runs once per week so this should not be an issue, but watch if you
     add many more queries or run manual tests frequently.
  5. The CSE "cx" ID is specific to your search engine configuration.
     If you create a new CSE, update the GOOGLE_CSE_ID secret.
"""

import logging
import os
import time
from typing import List, Optional
import urllib.parse

import requests

import config
from models import Opportunity, clean_text, normalize_date

logger = logging.getLogger(__name__)

# Google Custom Search JSON API endpoint
# v1 is the only production version; do not use Alpha endpoints
GOOGLE_CSE_API_URL = "https://www.googleapis.com/customsearch/v1"


def fetch_google_cse_results() -> List[Opportunity]:
    """
    Main entry point: query the Google CSE for each search query in
    config.GOOGLE_CSE_QUERIES and return a combined, deduplicated list
    of Opportunity objects.

    Returns empty list (not exception) if credentials are missing.
    """
    api_key = os.environ.get("GOOGLE_CSE_KEY", "").strip()
    cse_id  = os.environ.get("GOOGLE_CSE_ID", "").strip()

    if not api_key or not cse_id:
        logger.warning(
            "GOOGLE_CSE_KEY or GOOGLE_CSE_ID not set. "
            "Skipping Google CSE state portal coverage. "
            "Set both secrets in GitHub Actions to enable this source."
        )
        return []

    logger.info(
        f"Google CSE: querying {len(config.GOOGLE_CSE_QUERIES)} keyword phrases "
        f"across {len(config.STATE_PORTAL_DOMAINS_FOR_CSE)} state portal domains"
    )

    # Deduplicate by URL across all queries
    seen_urls: dict = {}   # url -> raw result dict

    for query_str in config.GOOGLE_CSE_QUERIES:
        results = _query_google_cse(api_key, cse_id, query_str)
        for item in results:
            url = item.get("link", "")
            if url and url not in seen_urls:
                seen_urls[url] = item

        # Polite delay between API calls
        time.sleep(config.REQUEST_DELAY_SECONDS)

    logger.info(
        f"Google CSE: {len(seen_urls)} unique results across all queries"
    )

    # Parse raw results into Opportunity objects
    opportunities = []
    for url, raw in seen_urls.items():
        opp = _parse_cse_result(raw)
        if opp:
            opportunities.append(opp)

    logger.info(f"Google CSE: {len(opportunities)} opportunities parsed")
    return opportunities


def _query_google_cse(
    api_key: str,
    cse_id: str,
    query: str,
    num_results: int = 10,
) -> list:
    """
    Execute one Google CSE query and return raw result items.

    Google CSE returns up to 10 results per query on the free tier.
    We do not paginate (that would cost additional queries) -- 10 results
    per keyword phrase is sufficient given that we run multiple phrases.

    Args:
        api_key:     Google API key
        cse_id:      Custom Search Engine ID (the "cx" parameter)
        query:       Search query string
        num_results: Results to request (max 10 on free tier)

    Returns:
        List of raw result item dicts from Google's API response

    KNOWN FAILURE POINT: Google returns HTTP 429 if you exceed the daily
    quota. The handler logs the error and returns an empty list (this run's
    remaining CSE queries will also fail at that point, but other sources
    like SAM.gov will still complete).
    """
    params = {
        "key": api_key,
        "cx":  cse_id,
        "q":   query,
        "num": num_results,
    }

    for attempt in range(1, config.REQUEST_MAX_RETRIES + 1):
        try:
            response = requests.get(
                GOOGLE_CSE_API_URL,
                params=params,
                headers=config.REQUEST_HEADERS,
                timeout=config.REQUEST_TIMEOUT,
            )

            if response.status_code == 429:
                logger.warning(
                    f"Google CSE rate limit hit (429) on query '{query}'. "
                    f"Daily quota may be exhausted."
                )
                return []   # Don't retry rate limit errors -- quota is reset daily

            if response.status_code == 403:
                logger.warning(
                    f"Google CSE 403 on query '{query}'. "
                    f"API key may be invalid or CSE not configured. "
                    f"Verify GOOGLE_CSE_KEY and GOOGLE_CSE_ID in Secrets."
                )
                return []   # Auth errors won't recover on retry

            response.raise_for_status()
            data = response.json()

            # Google returns "items" key with result list.
            # If no results, "items" key is absent (not an empty list).
            items = data.get("items", [])
            logger.debug(
                f"Google CSE query '{query}': {len(items)} results"
            )
            return items

        except requests.exceptions.Timeout:
            logger.warning(
                f"Google CSE timeout for '{query}' "
                f"(attempt {attempt}/{config.REQUEST_MAX_RETRIES})"
            )
            if attempt < config.REQUEST_MAX_RETRIES:
                time.sleep(10)

        except requests.exceptions.RequestException as e:
            logger.warning(f"Google CSE request error for '{query}': {e}")
            return []

    return []


def _parse_cse_result(raw: dict) -> Optional[Opportunity]:
    """
    Convert one Google CSE result item into an Opportunity.

    CSE result items have this structure:
      {
        "title": "...",            # Page title from Google's index
        "link": "...",             # The URL
        "snippet": "...",          # Short text excerpt Google extracted
        "displayLink": "...",      # Domain (e.g., "vsigns.vermont.gov")
        "pagemap": {...},          # Optional structured metadata (rarely useful)
      }

    We use the title as the opportunity title, snippet as description,
    and displayLink to infer the state.

    KNOWN FAILURE POINT: Google's "title" for a government portal page may
    be the site's name ("Vermont VSIGNS") rather than the RFP title, especially
    if the page is a search results or listing page. The scorer will evaluate
    this on keyword content; low-quality titles will score low and be filtered.
    The URL in "link" is the authoritative reference regardless.
    """
    try:
        url = raw.get("link", "").strip()
        if not url:
            return None

        title = clean_text(raw.get("title", ""), max_length=300)
        if not title:
            return None

        # Use the snippet as description (short, but better than nothing)
        description = clean_text(raw.get("snippet", ""), max_length=500)

        # Infer source name and state from the domain
        domain = raw.get("displayLink", "")
        source_name = _domain_to_source_name(domain)
        state_code  = _domain_to_state(domain)

        # Use the URL as the notice ID (most reliable unique identifier for CSE results)
        # KNOWN FAILURE POINT: If Google returns a search results page URL (rather
        # than the RFP posting URL), the notice_id will be the search results page.
        # This is an acceptable dedup key -- different searches returning the same
        # result page will correctly deduplicate.
        notice_id = url

        # Try to extract a deadline from the snippet text
        deadline = _extract_deadline_from_snippet(description)

        return Opportunity(
            source=f"CSE:{source_name}",   # Prefix CSE: to distinguish from direct scrapes
            notice_id=notice_id,
            url=url,
            title=title,
            description=description,
            issuer=source_name,
            state=state_code,
            deadline=deadline,
        )

    except Exception as e:
        logger.warning(f"Google CSE parse error: {e}. Raw: {str(raw)[:200]}")
        return None


def _domain_to_source_name(domain: str) -> str:
    """
    Map a domain string to a human-readable source name.

    KNOWN FAILURE POINT: New domains not listed here fall back to the
    domain string itself, which is readable enough for the digest but
    less clean. Add mappings here as you discover new state portal domains
    appearing in CSE results.
    """
    domain_map = {
        "vsigns.vermont.gov":        "Vermont VSIGNS",
        "commbuys.com":              "Massachusetts COMMBUYS",
        "biznet.ct.gov":             "Connecticut Biznet",
        "nyscr.ny.gov":              "New York NYSCR",
        "maine.gov":                 "Maine Procurement",
        "das.nh.gov":                "New Hampshire DAS",
        "purchasing.ri.gov":         "Rhode Island Purchasing",
        "epiq.dgs.pa.gov":           "Pennsylvania eMarket",
        "procurement.maryland.gov":  "Maryland Procurement",
        "eva.virginia.gov":          "Virginia eVA",
        "mfmp.myflorida.com":        "Florida MFMP",
        "team.georgia.gov":          "Georgia Team",
        "iphub.nc.gov":              "North Carolina IPH",
        "ipp.illinois.gov":          "Illinois ProcureIllinois",
        "vendornet.state.wi.us":     "Wisconsin VendorNet",
        "procurement.ohio.gov":      "Ohio Procurement",
        "bidding.michigan.gov":      "Michigan Bidding",
        "caleprocure.ca.gov":        "California CaleProcure",
        "orpin.oregon.gov":          "Oregon ORPIN",
        "ga.wa.gov":                 "Washington GA",
        "purchasing.colorado.gov":   "Colorado Purchasing",
        "des.az.gov":                "Arizona DES",
        "purchasing.texas.gov":      "Texas Purchaing",
    }
    # Try direct match, then subdomain match
    for pattern, name in domain_map.items():
        if pattern in domain:
            return name
    return domain   # Fallback: return domain as-is


def _domain_to_state(domain: str) -> str:
    """
    Map a domain string to a two-letter state code.
    Returns empty string if the domain doesn't map to a known state.
    """
    domain_state_map = {
        "vermont.gov": "VT", "vsigns": "VT",
        "commbuys": "MA", "mass.gov": "MA",
        "ct.gov": "CT", "biznet.ct": "CT",
        "ny.gov": "NY", "nyscr.ny": "NY", "nyserda": "NY",
        "maine.gov": "ME",
        "nh.gov": "NH",
        "ri.gov": "RI",
        "pa.gov": "PA", "pennsylvania": "PA",
        "maryland.gov": "MD",
        "virginia.gov": "VA",
        "florida": "FL", "myflorida": "FL",
        "georgia": "GA",
        "nc.gov": "NC",
        "illinois.gov": "IL",
        "wisconsin": "WI",
        "ohio.gov": "OH",
        "michigan.gov": "MI",
        "california": "CA", "ca.gov": "CA",
        "oregon.gov": "OR",
        "wa.gov": "WA",
        "colorado.gov": "CO",
        "az.gov": "AZ",
        "texas.gov": "TX",
    }
    domain_lower = domain.lower()
    for pattern, state in domain_state_map.items():
        if pattern in domain_lower:
            return state
    return ""


def _extract_deadline_from_snippet(text: str) -> Optional[str]:
    """
    Attempt to find a deadline date in Google's snippet text.

    Google's snippets are short (~150 chars) so date extraction is hit-or-miss.
    Returns ISO date string or None.

    KNOWN FAILURE POINT: Snippet text is truncated and often doesn't include
    deadline information. Treat any extracted deadline as approximate and
    verify on the source page.
    """
    import re
    from models import normalize_date as norm

    # Look for date patterns near deadline-indicating words
    trigger = r"(?:due|deadline|close[sd]?|respond by|submit by)"
    date_pat = r"(\d{1,2}/\d{1,2}/\d{2,4}|\w+ \d{1,2},\s*\d{4}|\d{4}-\d{2}-\d{2})"
    combined = f"{trigger}.{{0,40}}{date_pat}"

    match = re.search(combined, text, re.IGNORECASE)
    if match:
        return norm(match.group(1))
    return None
