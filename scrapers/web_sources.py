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
import re

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
    elif ptype == "vermont_dps_rfps":
        return _scrape_vermont_dps_rfps(url, name, state)
    elif ptype == "vermont_business_registry":
        return _scrape_vermont_business_registry(url, name, state)
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
# Vermont-specific scrapers
# ---------------------------------------------------------------------------

def _scrape_vermont_dps_rfps(url: str, name: str, state: str) -> List[Opportunity]:
    """
    Scrape the Vermont Department of Public Service Requests for Proposals page.

    The generic link scraper is too broad for this page because it captures
    navigation and sidebar links that contain energy-related terms. This parser
    limits extraction to the main content area and only keeps links that look
    like actual RFP/proposal postings.

    Target page:
      https://publicservice.vermont.gov/document-categories/requests-proposals

    Notes:
      - Vermont DPS often posts both a document landing page and a direct PDF
        for the same RFP. To avoid duplicate opportunities, this parser keeps
        the DPS document landing pages and skips direct /sites/dps/files/ PDF
        links.
      - Q&A/addendum/response documents are intentionally excluded as standalone
        opportunities. They are useful supporting documents, but they usually
        duplicate an already-posted RFP and can create noise in the digest.
      - Deadline extraction is currently limited to text visible on the listing
        page or nearby HTML container. Many DPS deadlines may only appear inside
        the linked PDF, so deadline may remain None.
    """
    html = _fetch_page(url)
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")

    # Prefer the main page content to avoid sidebar/header/footer navigation.
    main_content = (
        soup.select_one("main")
        or soup.select_one("#main-content")
        or soup.select_one(".main-content")
        or soup.select_one(".region-content")
        or soup.select_one(".view-content")
        or soup
    )

    # Keep only links that look like original RFP/proposal postings.
    include_terms = [
        "rfp",
        "request for proposal",
        "request for proposals",
    ]

    # Exclude supporting documents that are not standalone opportunities.
    # These are useful after someone opens the RFP, but should not create
    # separate digest entries.
    support_doc_terms = [
        "question and answer",
        "questions and answers",
        "responses to questions",
        "response to questions",
        "q&a",
        "addendum",
        "addenda",
    ]

    # Exclude common navigation/sidebar/footer links and general program pages.
    # These may contain energy-related terms and can pass broad scoring if not
    # filtered out before creating Opportunity objects.
    exclude_terms = [
        "skip to main content",
        "home",
        "careers",
        "about us",
        "document library",
        "public advocacy",
        "regulated utilities",
        "energy efficiency utilities",
        "efficiency",
        "renewables",
        "telecommunications and connectivity",
        "consumer information",
        "vermont energy atlas",
        "vermont energy saver",
        "electric vehicle public charging map",
        "clean energy development fund",
        "building energy standards",
        "read more",
    ]

    opportunities = []
    seen_urls = set()

    # Vermont DPS listing titles are regular links in the page content.
    # We inspect links, but only keep those with original RFP/proposal-like
    # title text and skip supporting documents/direct PDFs that duplicate the
    # document landing pages.
    for link in main_content.find_all("a", href=True):
        title = clean_text(link.get_text(" ", strip=True))
        if not title or len(title) < 10:
            continue

        title_l = title.lower()
        absolute_url = urllib.parse.urljoin(url, link["href"])
        absolute_url_l = absolute_url.lower()

        # Skip obvious navigation/program links.
        if any(term in title_l for term in exclude_terms):
            continue

        # Skip Q&A/addendum/response documents as standalone opportunities.
        if any(term in title_l for term in support_doc_terms):
            continue

        # Skip direct DPS file/PDF links because the same RFP usually also has
        # a cleaner DPS document landing page. This reduces duplicate entries.
        if "/sites/dps/files/" in absolute_url_l:
            continue

        # Keep only original RFP/proposal-looking links.
        if not any(term in title_l for term in include_terms):
            continue

        # Avoid duplicate entries if the same document link appears more than once.
        if absolute_url in seen_urls:
            continue
        seen_urls.add(absolute_url)

        # Use a nearby container for description/deadline context.
        container = (
            link.find_parent("article")
            or link.find_parent("div", class_=lambda c: c and "views-row" in c)
            or link.find_parent("div")
            or link.parent
        )

        entry_text = ""
        if container:
            entry_text = clean_text(container.get_text(" ", strip=True))

        deadline = _extract_deadline_from_text(entry_text)

        opportunities.append(Opportunity(
            source=name,
            notice_id=absolute_url,
            url=absolute_url,
            title=title,
            description=entry_text or title,
            issuer="Vermont Department of Public Service",
            state=state,
            deadline=deadline,
        ))

    logger.info(f"Vermont DPS dedicated parser: {len(opportunities)} entries parsed")
    return opportunities

def _scrape_vermont_business_registry(url: str, name: str, state: str) -> List[Opportunity]:
    """
    Scrape the Vermont Business Registry and Bid System open bid search page.

    Target page:
      https://www.vermontbusinessregistry.com/bidsearch.aspx?type=1

    The generic link scraper does not work well for this page because bid links
    are not normal hrefs. They are JavaScript calls like:

      javascript:openPrintView('BidPreview.aspx?BidID=73790', 'Window73790');

    This parser extracts the BidID from those JavaScript links and converts each
    one into a direct BidPreview.aspx URL. It also attempts to pull nearby row
    text for issuer and deadline context.

    Notes:
      - This source is intentionally broad. It includes statewide, municipal,
        federal, private, and sources-sought bids shown by the registry.
      - The scorer is responsible for filtering broad bid results down to
        energy/evaluation/EM&V-relevant opportunities.
      - The page is ASP.NET-generated and table-heavy, so this parser uses
        link pattern extraction plus nearby table-row context rather than
        relying on stable CSS classes.
    """
    html = _fetch_page(url)
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    opportunities = []
    seen_bid_ids = set()

    # Pattern for:
    # javascript:openPrintView('BidPreview.aspx?BidID=73790', 'Window73790');
    bid_pattern = re.compile(r"BidPreview\.aspx\?BidID=(\d+)", re.IGNORECASE)

    for link in soup.find_all("a", href=True):
        title = clean_text(link.get_text(" ", strip=True))
        href = link.get("href", "")

        if not title or len(title) < 5:
            continue

        match = bid_pattern.search(href)
        if not match:
            continue

        bid_id = match.group(1)
        if bid_id in seen_bid_ids:
            continue
        seen_bid_ids.add(bid_id)

        bid_url = urllib.parse.urljoin(url, f"/BidPreview.aspx?BidID={bid_id}")

        # Try to use the surrounding table row for posted date, issuer, and
        # close date. The page is generated from nested ASP.NET tables, so the
        # row structure may vary, but the nearest <tr> is still the best local
        # context.
        row = link.find_parent("tr")
        row_text = clean_text(row.get_text(" ", strip=True)) if row else title

        # Extract all cell text from the row. The visible bid list appears to
        # include posted date, title, bid/code, issuer, and closing date.
        cells = []
        if row:
            cells = [
                clean_text(cell.get_text(" ", strip=True))
                for cell in row.find_all(["td", "th"])
            ]
            cells = [c for c in cells if c]

        # Default values if row parsing is incomplete.
        issuer = "Vermont Business Registry"
        posted_date = None
        deadline = _extract_deadline_from_text(row_text)

        # Heuristic extraction:
        # The row often contains title + issuer + close date, but because this
        # is table-heavy ASP.NET markup, cell positions may not always be stable.
        # Use the title cell as an anchor, then look for dates and likely issuer
        # text in the remaining cells.
        date_candidates = []
        for cell_text in cells:
            normalized_date = normalize_date(cell_text)
            if normalized_date:
                date_candidates.append(normalized_date)

        if date_candidates:
            # The first date is usually the posted date; the last date is usually
            # the close/deadline date.
            posted_date = date_candidates[0]
            deadline = date_candidates[-1]

        # Pick the longest non-date, non-title-ish cell after the title as issuer.
        # This avoids assigning short bid codes as the issuer where possible.
        non_date_cells = []
        for cell_text in cells:
            if normalize_date(cell_text):
                continue
            if cell_text == title:
                continue
            if bid_id in cell_text:
                continue
            non_date_cells.append(cell_text)

        if non_date_cells:
            # Prefer a cell that looks like an agency/department/municipality name.
            # Fall back to the longest remaining cell.
            issuer_candidates = [
                c for c in non_date_cells
                if any(token in c.lower() for token in [
                    "department",
                    "agency",
                    "office",
                    "town",
                    "city",
                    "village",
                    "state of",
                    "division",
                    "district",
                    "commission",
                    "county",
                ])
            ]
            issuer = (
                max(issuer_candidates, key=len)
                if issuer_candidates
                else max(non_date_cells, key=len)
            )

        opportunities.append(Opportunity(
            source=name,
            notice_id=bid_id,
            url=bid_url,
            title=title,
            description=row_text or title,
            issuer=issuer,
            state=state,
            posted_date=posted_date,
            deadline=deadline,
        ))

    logger.info(
        f"Vermont Business Registry dedicated parser: "
        f"{len(opportunities)} entries parsed"
    )
    return opportunities

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
