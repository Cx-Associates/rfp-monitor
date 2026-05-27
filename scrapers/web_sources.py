"""
scrapers/web_sources.py -- Utility, Quasi-Public, and Priority State Scrapers
==============================================================================
Scrapes the hand-curated list of utility/quasi-public sources in
config.UTILITY_SOURCES and the priority state portals in
config.DIRECT_SCRAPE_STATES.

These are the highest-probability issuers of EM&V work and warrant direct
scraping (rather than relying solely on Google CSE which has an indexing lag).

STRATEGY:
  - Uses requests + BeautifulSoup for static HTML pages
  - Skips sources flagged js_render=True (requires Phase 2 Playwright upgrade)
  - Skips sources flagged active=False (disabled without deleting config entry)
  - Extracts anchor tags whose text or surrounding context contains RFP keywords
  - Returns minimal Opportunity objects; scorer does the relevance filtering

KNOWN FAILURE POINTS:
  1. These scrapers are the most brittle part of the system. Website redesigns
     break them silently (returning 0 results without an error). If a normally
     productive source drops to 0, inspect the page and update selectors.
  2. Some sites (particularly utility portals) block requests with bot-detection
     WAF rules even with a legitimate User-Agent. If you see consistent 403
     responses, the source needs manual monitoring.
  3. NASEO's page structure has changed across CMS versions. The NASEO scraper
     tries multiple selector patterns before falling back to the generic link
     scraper.
  4. The REQUEST_DELAY_SECONDS delay between sites helps avoid triggering rate
     limits but adds to total runtime. For 15 sources at 2 seconds each = ~30s
     of delay, well within the 30-minute GitHub Actions timeout.
"""

import logging
import time
import urllib.parse
from typing import List, Optional

import requests
from bs4 import BeautifulSoup

import config
from models import Opportunity, clean_text, normalize_date

logger = logging.getLogger(__name__)

# Keywords used specifically for anchor-tag link text matching
# (shorter than the full keyword lists -- link text is often abbreviated)
LINK_TEXT_KEYWORDS = [
    "evaluation", "measurement", "verification", "m&v", "emv",
    "ipmvp", "impact", "baseline", "program eval",
    "rfp", "rfq", "rfi", "solicitation", "proposal", "bid",
    "request for proposal", "request for qualification",
]


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def fetch_utility_sources() -> List[Opportunity]:
    """
    Scrape all active, non-JS-rendered sources in config.UTILITY_SOURCES.

    JS-rendered sources (js_render=True) are logged as skipped.
    Inactive sources (active=False) are silently skipped.

    Returns combined list of Opportunity objects from all scraped sources.
    """
    all_opps = []

    for src in config.UTILITY_SOURCES:
        name      = src["name"]
        url       = src["url"]
        active    = src.get("active", True)
        js_render = src.get("js_render", False)

        if not active:
            continue   # Silently skip disabled sources

        if js_render:
            logger.info(f"Skipping (JS-rendered, Phase 2): {name}")
            continue

        logger.info(f"Scraping utility source: {name}")
        try:
            opps = _scrape_generic_rfp_page(url, source_name=name, state="")
            logger.info(f"  {name}: {len(opps)} candidates")
            all_opps.extend(opps)
        except Exception as e:
            logger.warning(f"  {name}: failed ({type(e).__name__}: {e})")

        time.sleep(config.REQUEST_DELAY_SECONDS)

    # NASEO gets its own scraper because its structure is more regular
    logger.info("Scraping NASEO RFP Board (dedicated scraper)")
    try:
        naseo_opps = fetch_naseo()
        logger.info(f"  NASEO: {len(naseo_opps)} candidates")
        all_opps.extend(naseo_opps)
    except Exception as e:
        logger.warning(f"  NASEO: failed ({type(e).__name__}: {e})")

    logger.info(f"Utility sources total: {len(all_opps)} candidates")
    return all_opps


def fetch_direct_scrape_states() -> List[Opportunity]:
    """
    Scrape the high-priority state portals listed in config.DIRECT_SCRAPE_STATES.

    These get direct scraping (rather than CSE) because they are the most
    important markets for CxA and we want real-time detection without the
    Google indexing lag.

    Returns combined list of Opportunity objects.
    """
    all_opps = []

    for portal in config.DIRECT_SCRAPE_STATES:
        name  = portal["name"]
        url   = portal["url"]
        state = portal.get("state", "")
        ptype = portal.get("type", "generic_list")

        logger.info(f"Direct scraping state portal: {name} ({state})")
        try:
            opps = _scrape_by_type(url, ptype, name, state)
            logger.info(f"  {name}: {len(opps)} candidates")
            all_opps.extend(opps)
        except Exception as e:
            logger.warning(f"  {name}: failed ({type(e).__name__}: {e})")

        time.sleep(config.REQUEST_DELAY_SECONDS)

    logger.info(f"Direct state scrapes total: {len(all_opps)} candidates")
    return all_opps


def fetch_naseo() -> List[Opportunity]:
    """
    Scrape the NASEO RFP board at https://www.naseo.org/rfps

    NASEO posts RFPs from state energy offices nationally and is one of the
    most reliable sources for evaluation work outside of SAM.gov.

    Tries several CSS selector patterns corresponding to different versions
    of NASEO's CMS. Falls back to the generic link scraper on failure.

    KNOWN FAILURE POINT: NASEO redesigned their site in 2024. Update the
    selector list below if this scraper consistently returns 0 results.
    """
    url = "https://www.naseo.org/rfps"
    html = _fetch_page(url)
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    opportunities = []

    # Try selectors corresponding to different NASEO CMS versions
    # KNOWN FAILURE POINT: Add new selectors here if NASEO redesigns again
    entry_selectors = [
        "div.field--name-body",     # 2024 redesign
        "article.rfp",              # Older design
        "div.views-row",            # Drupal views pattern
        "li.rfp-item",              # Another Drupal pattern
        "div.node--type-rfp",       # Node-based Drupal
        "article",                  # Last resort before generic fallback
    ]

    entries = []
    for selector in entry_selectors:
        entries = soup.select(selector)
        if entries:
            logger.debug(f"NASEO: matched selector '{selector}' ({len(entries)} entries)")
            break

    if not entries:
        logger.info("NASEO: no structured entries found; using generic link scraper")
        return _scrape_generic_rfp_page(url, "NASEO RFP Board", state="")

    for entry in entries:
        link = entry.find("a", href=True)
        if not link:
            continue

        title_text = link.get_text(strip=True)
        if not title_text or len(title_text) < 10:
            # If link text is too short, try the full entry text
            title_text = entry.get_text(separator=" ", strip=True)[:200]

        href = urllib.parse.urljoin(url, link["href"])
        entry_text = entry.get_text(separator=" ", strip=True)
        deadline = _extract_deadline_from_text(entry_text)

        opp = Opportunity(
            source="NASEO RFP Board",
            notice_id=href,
            url=href,
            title=clean_text(title_text),
            description=clean_text(entry_text),
            issuer="NASEO / State Energy Office",
            deadline=deadline,
            state="",   # NASEO posts national and state-specific -- scorer handles
        )
        opportunities.append(opp)

    logger.info(f"NASEO: {len(opportunities)} entries parsed")
    return opportunities


# ---------------------------------------------------------------------------
# Type-dispatched scraper for direct-scrape state portals
# ---------------------------------------------------------------------------

def _scrape_by_type(
    url: str, ptype: str, name: str, state: str
) -> List[Opportunity]:
    """
    Dispatch to the appropriate scraper based on portal type.

    All state portal scrapers currently reduce to one of two approaches:
      - "vsigns" / "commbuys": session-dependent portals with table structure
      - "generic_list":        simple HTML listing pages
      - "ca_eprocure":         California's ASPX-based portal

    The VSIGNS and COMMBUYS scrapers attempt structured table extraction
    before falling back to the generic approach.

    KNOWN FAILURE POINT: VSIGNS and COMMBUYS require session state that a
    fresh requests.get() doesn't establish. If these return 0 results,
    the portals may need manual monitoring or a Playwright upgrade.
    """
    if ptype == "vsigns":
        return _scrape_vsigns(url, name, state)
    elif ptype == "commbuys":
        return _scrape_commbuys(url, name, state)
    elif ptype == "ca_eprocure":
        return _scrape_ca_eprocure(url, name, state)
    else:
        # Default: generic link scraper
        return _scrape_generic_rfp_page(url, name, state)


def _scrape_vsigns(url: str, name: str, state: str) -> List[Opportunity]:
    """
    Scrape Vermont VSIGNS.

    VSIGNS is a MERX-based portal. The public bid listing tab can be
    accessed without login but may require cookies from the parent session.

    KNOWN FAILURE POINT: If the scraper returns 0 results and the page
    title contains "login" or "session", VSIGNS has blocked the request.
    In that case, Vermont DPS's own website (publicservice.vermont.gov)
    is an alternative -- DPS posts evaluation RFPs there directly.
    """
    html = _fetch_page(url)
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")

    # Detect login redirect (MERX/VSIGNS pattern)
    title_tag = soup.find("title")
    if title_tag and any(
        w in title_tag.get_text().lower() for w in ["login", "sign in", "session expired"]
    ):
        logger.warning(
            f"VSIGNS: login page detected. "
            f"Portal may require session state. Falling back to generic scrape."
        )
        return _scrape_generic_rfp_page(url, name, state)

    # Try to find a table with bid listings
    # MERX/VSIGNS platforms typically use class names containing "bid" or "solicitation"
    bid_table = (
        soup.find("table", class_=lambda c: c and any(w in c.lower() for w in ["bid", "solicitation"]))
        or soup.find("table", id=lambda i: i and any(w in i.lower() for w in ["bid", "solicitation"]))
        or soup.find("table")   # Last resort: first table on page
    )

    if not bid_table:
        return _scrape_generic_rfp_page(url, name, state)

    opps = []
    for row in bid_table.find_all("tr")[1:]:  # Skip header row
        link = row.find("a", href=True)
        if not link:
            continue

        title = clean_text(link.get_text(strip=True))
        if len(title) < 10:
            continue

        absolute_url = urllib.parse.urljoin(url, link["href"])
        row_text = row.get_text(separator=" ", strip=True)
        deadline = _extract_deadline_from_text(row_text)

        opps.append(Opportunity(
            source=name,
            notice_id=absolute_url,
            url=absolute_url,
            title=title,
            description=clean_text(row_text),
            issuer="State of Vermont",
            state=state,
            deadline=deadline,
        ))

    return opps if opps else _scrape_generic_rfp_page(url, name, state)


def _scrape_commbuys(url: str, name: str, state: str) -> List[Opportunity]:
    """
    Scrape Massachusetts COMMBUYS public bid listing.

    COMMBUYS doesn't support keyword search on the public page; we fetch
    all public bids and let the scorer filter for EM&V relevance.

    KNOWN FAILURE POINT: COMMBUYS may require a prior request to the
    main site to establish a session cookie. Without the cookie, the public
    bid listing may redirect to the homepage. The generic fallback handles
    this by scraping whatever links appear on the page.
    """
    html = _fetch_page(url)
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    opps = []

    # COMMBUYS typically wraps bids in a dataTable
    rows = soup.select("table.dataTable tr, table tr")
    for row in rows:
        link = row.find("a", href=True)
        if not link:
            continue

        title = clean_text(link.get_text(strip=True))
        if len(title) < 10:
            continue

        absolute_url = urllib.parse.urljoin(url, link["href"])
        row_text = row.get_text(separator=" ", strip=True)
        deadline = _extract_deadline_from_text(row_text)

        opps.append(Opportunity(
            source=name,
            notice_id=absolute_url,
            url=absolute_url,
            title=title,
            description=clean_text(row_text),
            issuer="Commonwealth of Massachusetts",
            state=state,
            deadline=deadline,
        ))

    return opps if opps else _scrape_generic_rfp_page(url, name, state)


def _scrape_ca_eprocure(url: str, name: str, state: str) -> List[Opportunity]:
    """
    Scrape California CaleProcure / DGS procurement portal.

    California's portal uses ASPX with JavaScript search, making it
    difficult to scrape directly. We target the public search results
    page with keyword parameters appended to the URL.

    KNOWN FAILURE POINT: California's ASPX portal may require a ViewState
    parameter from the initial page load for search to work. If this returns
    a blank or search-form page, fall back to the generic scraper on the
    energy-specific sub-page of the DGS site.

    Alternative: The California Energy Commission posts evaluation RFPs on
    energy.ca.gov/contracts/ which is a simpler static page.
    """
    # Try CEC's contracts page instead of CaleProcure -- more reliable
    cec_url = "https://www.energy.ca.gov/contracts"
    html = _fetch_page(cec_url)
    if html:
        cec_opps = _scrape_generic_rfp_page(cec_url, "California CEC Contracts", "CA")
        if cec_opps:
            return cec_opps

    # Fall back to the configured CaleProcure URL
    return _scrape_generic_rfp_page(url, name, state)


# ---------------------------------------------------------------------------
# Generic HTML link scraper (used as primary and fallback)
# ---------------------------------------------------------------------------

def _scrape_generic_rfp_page(
    url: str,
    source_name: str,
    state: str = "",
) -> List[Opportunity]:
    """
    Generic scraper: fetch a page and extract links that look like RFP postings.

    Strategy:
      1. Fetch the page HTML
      2. Find all <a> tags with href
      3. Filter to those whose text or parent element text contains
         any keyword from LINK_TEXT_KEYWORDS
      4. Return each qualifying link as a minimal Opportunity

    Works well for: simple listing pages (NASEO, Maine, NH, DOE, EPA)
    Works poorly for: JavaScript-rendered pages, form-based search portals

    KNOWN FAILURE POINT: This scraper can't distinguish between a link to
    an RFP posting and a link to a general "RFP process" page. The scorer
    filters based on title/description content, which handles most false
    positives, but some generic links (e.g., "How to respond to RFPs") may
    slip through as low-scoring results.
    """
    html = _fetch_page(url)
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    opps = []

    for link in soup.find_all("a", href=True):
        link_text = link.get_text(separator=" ", strip=True)

        # Skip very short or navigation-only links
        if len(link_text) < 10:
            continue

        # Skip junk link text -- page artifacts not RFP titles
        # Add to this set if new junk patterns appear in results
        JUNK_TITLES = {
            "home", "about", "contact", "login", "search", "menu",
            "click here", "here", "download", "view", "read more",
            "appendix a", "appendix b", "appendix c", "appendix d",
            "attachment", "exhibit", "more info", "learn more",
            "back", "next", "previous", "submit", "apply",
        }
        if link_text.lower().strip() in JUNK_TITLES:
            continue

        link_lower = link_text.lower()
        parent_lower = ""
        if link.parent:
            parent_lower = link.parent.get_text(separator=" ", strip=True).lower()

        # Check if this link is RFP-related based on text or parent context
        is_rfp = any(kw in link_lower for kw in LINK_TEXT_KEYWORDS)
        if not is_rfp:
            is_rfp = any(kw in parent_lower for kw in LINK_TEXT_KEYWORDS)
        if not is_rfp:
            continue

        href = link.get("href", "")
        absolute_url = urllib.parse.urljoin(url, href)

        # Skip non-HTTP links (mailto:, javascript:, anchors)
        if not absolute_url.startswith(("http://", "https://")):
            continue

        # Use parent element text as description context
        parent_text = ""
        if link.parent:
            parent_text = link.parent.get_text(separator=" ", strip=True)

        deadline = _extract_deadline_from_text(parent_text)

        opps.append(Opportunity(
            source=source_name,
            notice_id=absolute_url,
            url=absolute_url,
            title=clean_text(link_text, max_length=300),
            description=clean_text(parent_text, max_length=500),
            issuer=source_name,
            state=state,
            deadline=deadline,
        ))

    return opps


# ---------------------------------------------------------------------------
# HTTP utilities
# ---------------------------------------------------------------------------

def _fetch_page(url: str) -> Optional[str]:
    """
    Fetch a URL and return the response HTML text, or None on failure.

    Handles common failure modes:
      - SSL errors: retried once with verify=False (with warning logged)
      - 403 Forbidden: logged as warning, returns None
      - 404 Not Found: logged, returns None
      - 429 Rate limit: logged, returns None (no retry -- honor the limit)
      - Timeout: retried up to REQUEST_MAX_RETRIES times

    KNOWN FAILURE POINT: Sites with bot detection (Cloudflare, Akamai WAF)
    may return 403 or redirect to a CAPTCHA regardless of User-Agent.
    These can't be scraped with simple HTTP requests and need either
    Playwright or manual monitoring. Log the 403 URL and investigate.
    """
    for attempt in range(1, config.REQUEST_MAX_RETRIES + 1):
        try:
            response = requests.get(
                url,
                headers=config.REQUEST_HEADERS,
                timeout=config.REQUEST_TIMEOUT,
                verify=True,
                allow_redirects=True,
            )

            if response.status_code == 404:
                logger.warning(f"404 Not Found: {url}")
                return None

            if response.status_code == 403:
                logger.warning(
                    f"403 Forbidden: {url} "
                    f"(site may be blocking scrapers -- consider manual monitoring)"
                )
                return None

            if response.status_code == 429:
                logger.warning(f"429 Rate Limited: {url} -- skipping")
                return None

            response.raise_for_status()
            return response.text

        except requests.exceptions.SSLError:
            # Some older state/government sites have cert issues
            logger.warning(f"SSL error on {url}. Retrying without SSL verification...")
            try:
                response = requests.get(
                    url,
                    headers=config.REQUEST_HEADERS,
                    timeout=config.REQUEST_TIMEOUT,
                    verify=False,   # Only disabled after SSL error
                )
                return response.text
            except Exception as e:
                logger.warning(f"SSL-disabled retry also failed for {url}: {e}")
                return None

        except requests.exceptions.Timeout:
            logger.warning(
                f"Timeout on {url} "
                f"(attempt {attempt}/{config.REQUEST_MAX_RETRIES})"
            )
            if attempt < config.REQUEST_MAX_RETRIES:
                time.sleep(10)

        except requests.exceptions.ConnectionError as e:
            logger.warning(f"Connection error on {url}: {e}")
            return None   # Connection errors rarely resolve on retry

        except requests.exceptions.RequestException as e:
            logger.warning(f"Request error on {url}: {e}")
            return None

    return None


def _extract_deadline_from_text(text: str) -> Optional[str]:
    """
    Extract a deadline date from free-form text near deadline trigger words.

    Looks for patterns like:
      "Proposals due May 1, 2026"
      "Deadline: 05/01/2026"
      "Responses due by 2026-04-15"

    KNOWN FAILURE POINT: Short snippets (< 50 chars) often don't include
    deadline info. When two dates appear near deadline words (e.g., Q&A deadline
    and submission deadline), we return the first match, which may be the
    earlier (Q&A) date rather than the submission date. Treat all extracted
    deadlines as approximate -- verify on the source page.
    """
    import re
    from models import normalize_date as norm

    trigger  = r"(?:due|deadline|close[sd]?|respond by|submit by|proposals due|responses due)"
    date_pat = r"(\d{1,2}/\d{1,2}/\d{2,4}|\w+ \d{1,2},\s*\d{4}|\d{4}-\d{2}-\d{2})"
    combined = f"{trigger}.{{0,50}}{date_pat}"

    match = re.search(combined, text, re.IGNORECASE)
    if match:
        return norm(match.group(1))
    return None
