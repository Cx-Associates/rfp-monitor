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
import json

import requests
from bs4 import BeautifulSoup
from datetime import datetime

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
        name = src["name"]
        url = src["url"]
        state = src.get("state", "")
        ptype = src.get("type", "generic_list")
        active = src.get("active", True)
        js_render = src.get("js_render", False)

        if not active:
            continue   # Silently skip disabled sources

        if js_render:
            logger.info(f"Skipping (JS-rendered, Phase 2): {name}")
            continue

        logger.info(f"Scraping utility source: {name}")
        try:
            opps = _scrape_by_type(url, ptype, name, state)
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
    Scrape the NASEO RFP board at https://www.naseo.org/rfps.

    NASEO posts RFPs/RFIs from state energy offices nationally and is one of
    the most reliable sources for energy-office procurement opportunities.

    The previous implementation fell back to the generic link scraper when
    structured selectors failed. That was too broad for this page because it
    captured:
      - navigation links,
      - closed RFPs,
      - appendix/supporting document links,
      - "click here" / "here" links,
      - duplicate links within the same listing.

    This parser instead targets only the "Open RFIs and RFPs" section and
    creates one Opportunity per top-level list item. It intentionally ignores
    the "Closed RFPs, RFRs, and RFIs" section.
    """
    url = "https://www.naseo.org/rfps"
    html = _fetch_page(url)
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")

    # Prefer the main content block if present. This avoids header, menu,
    # footer, and mega-menu links.
    main_content = (
        soup.select_one("#ctl00_mainContent_ctl00_divContent")
        or soup.select_one("main")
        or soup.select_one("#main-content")
        or soup.select_one(".main-content")
        or soup
    )

    # Find the "Open RFIs and RFPs" heading.
    open_heading = None
    for heading in main_content.find_all(["h1", "h2", "h3"]):
        heading_text = clean_text(heading.get_text(" ", strip=True)).lower()
        if "open" in heading_text and ("rfp" in heading_text or "rfi" in heading_text):
            open_heading = heading
            break

    if not open_heading:
        logger.info("NASEO: no Open RFIs/RFPs heading found")
        return []

    # The open opportunities are usually in the first <ul> after the heading.
    # Stop before the closed section.
    open_list = None
    for sibling in open_heading.find_next_siblings():
        if sibling.name in ["h1", "h2", "h3"]:
            sibling_text = clean_text(sibling.get_text(" ", strip=True)).lower()
            if "closed" in sibling_text:
                break

        if sibling.name == "ul":
            open_list = sibling
            break

    if not open_list:
        logger.info("NASEO: no open RFP/RFI list found")
        return []

    opportunities = []
    seen_urls = set()

    support_link_text = {
        "here",
        "click here",
        "full rfp",
        "appendix a",
        "appendix b",
        "appendix c",
        "questions and answers",
        "q&a",
    }

    for item in open_list.find_all("li", recursive=False):
        item_text = clean_text(item.get_text(" ", strip=True), max_length=1000)
        if not item_text:
            continue

        # Pick the first meaningful link in the listing. NASEO listings often
        # contain multiple links: the main RFP, an addendum, "here", appendix
        # files, and email links. We want one opportunity per listing.
        chosen_link = None
        for link in item.find_all("a", href=True):
            link_text = clean_text(link.get_text(" ", strip=True))
            href = link.get("href", "")

            if not link_text or len(link_text) < 5:
                continue

            if link_text.lower() in support_link_text:
                continue

            if href.startswith("mailto:"):
                continue

            absolute_url = urllib.parse.urljoin(url, href)

            if not absolute_url.startswith(("http://", "https://")):
                continue

            chosen_link = (link_text, absolute_url)
            break

        # If the item has no useful anchor, use the first part of the item text
        # as the title and the NASEO RFP page as the URL.
        if chosen_link:
            title, href = chosen_link
        else:
            title = item_text[:200]
            href = url

        if href in seen_urls:
            continue
        seen_urls.add(href)

        deadline = _extract_naseo_deadline_from_text(item_text)

        opportunities.append(Opportunity(
            source="NASEO RFP Board",
            notice_id=href,
            url=href,
            title=clean_text(title, max_length=300),
            description=item_text,
            issuer="NASEO / State Energy Office",
            deadline=deadline,
            state="",
        ))

    logger.info(f"NASEO open RFP parser: {len(opportunities)} entries parsed")
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
    elif ptype == "veic_rfps":
        return _scrape_veic_rfps(url, name, state)
    elif ptype == "aesp_rfps":
        return _scrape_aesp_rfps(url, name, state)
    elif ptype == "neep_rfps":
        return _scrape_neep_rfps(url, name, state)
    elif ptype == "efficiency_maine_rfps":
        return _scrape_efficiency_maine_rfps(url, name, state)
    elif ptype == "nh_energy_rfps":
        return _scrape_nh_energy_rfps(url, name, state)
    elif ptype == "ct_eeb_rfps":
        return _scrape_ct_eeb_rfps(url, name, state)
    elif ptype == "energy_trust_rfps":
        return _scrape_energy_trust_rfps(url, name, state)
    elif ptype == "cape_light_rfps":
        return _scrape_cape_light_rfps(url, name, state)
    elif ptype == "pge_ee_solicitations":
        return _scrape_pge_ee_solicitations(url, name, state)
    elif ptype == "entergy_rfps":
        return _scrape_entergy_rfps(url, name, state)
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


def _normalize_commbuys_date(value: str) -> Optional[str]:
    """
    COMMBUYS dates often include time, e.g. '06/24/2026 15:00:00'.
    normalize_date() may not parse the full timestamp, so try the date
    portion first.
    """
    value = clean_text(value)
    if not value:
        return None

    direct = normalize_date(value)
    if direct:
        return direct

    date_match = re.search(r"\d{1,2}/\d{1,2}/\d{4}", value)
    if date_match:
        return normalize_date(date_match.group(0))

    return None


def _scrape_commbuys(url: str, name: str, state: str) -> List[Opportunity]:
    """
    Scrape Massachusetts COMMBUYS public open bid search results.

    Target page:
      https://www.commbuys.com/bso/view/search/external/advancedSearchBid.xhtml?openBids=true

    COMMBUYS previously used an older publicBids.sdo endpoint, but that URL now
    returns 404. The current public search page exposes open bid detail links in
    the HTML as anchors like:

      /bso/external/bidDetail.sda?docId=BD-26-1211-MSBA-MASS-130091&external=true&parentUrl=close

    This parser extracts those bid detail links and uses the surrounding table
    row as description context.

    Current COMMBUYS row layout:
      - cell 0: doc ID
      - cell 1: doc ID link
      - cell 2: buyer / issuer organization
      - cell 5: contact person
      - cell 6: bid title / description
      - cell 7: closing date / deadline
      - cell 10: status

    Known limitations:
      - This parser relies on the current COMMBUYS table layout. If COMMBUYS
        changes the result table or moves to JavaScript-only rendering, this
        parser may need to be updated or replaced with Playwright.
      - Posted date is not currently available from the visible row cells in the
        search results page, so posted_date is left as None.
    """
    html = _fetch_page(url)
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    opps = []
    seen_doc_ids = set()

    # Pattern for COMMBUYS bid IDs, e.g. BD-26-1211-MSBA-MASS-130091.
    doc_id_pattern = re.compile(r"docId=(BD-[^&\s\"'>]+)", re.IGNORECASE)

    for link in soup.find_all("a", href=True):
        href = link.get("href", "")
        link_text = clean_text(link.get_text(" ", strip=True))

        # Keep only bid detail links; skip pagination, bid acknowledgement
        # lists, login/help links, and other navigation.
        if "bidDetail.sda" not in href:
            continue

        match = doc_id_pattern.search(href)
        if not match:
            continue

        doc_id = match.group(1)
        if doc_id in seen_doc_ids:
            continue
        seen_doc_ids.add(doc_id)

        absolute_url = urllib.parse.urljoin(url, href)

        # Use the nearest table row as source context for title, issuer,
        # contact, and deadline.
        row = link.find_parent("tr")
        row_text = clean_text(row.get_text(" ", strip=True)) if row else link_text

        cells = []
        if row:
            # Preserve empty cells because COMMBUYS uses fixed column positions.
            # Removing blanks shifts the indexes and causes fields like status
            # ("Sent") or "View List" to be misread as the bid title.
            cells = [
                clean_text(cell.get_text(" ", strip=True))
                for cell in row.find_all(["td", "th"])
            ]

        # Guarded helper for fixed-position cell access.
        def cell_at(index: int) -> str:
            return cells[index] if len(cells) > index else ""

        issuer = cell_at(2) or "Commonwealth of Massachusetts"
        contact_name = cell_at(5)
        title = cell_at(6) or link_text or doc_id
        deadline = _normalize_commbuys_date(cell_at(7))
        posted_date = None
        status = cell_at(10)

        # Final safety: do not allow status/navigation labels to become titles.
        if title.lower() in {"sent", "view list", "f", "p", "n", "e"}:
            title = link_text if link_text and link_text != doc_id else doc_id

        description_parts = [
            f"COMMBUYS ID: {doc_id}",
            f"Issuer: {issuer}",
        ]
        if contact_name:
            description_parts.append(f"Contact: {contact_name}")
        if deadline:
            description_parts.append(f"Deadline: {deadline}")
        if status:
            description_parts.append(f"Status: {status}")
        if row_text:
            description_parts.append(row_text)

        description = clean_text(" | ".join(description_parts), max_length=1000)

        opps.append(Opportunity(
            source=name,
            notice_id=doc_id,
            url=absolute_url,
            title=title,
            description=description,
            issuer=issuer,
            state=state,
            posted_date=posted_date,
            deadline=deadline,
        ))

    logger.info(f"COMMBUYS dedicated parser: {len(opps)} entries parsed")
    return opps

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

def _scrape_neep_rfps(url: str, name: str, state: str) -> List[Opportunity]:
    """
    Scrape NEEP's Requests for Proposals page.

    Target page:
      https://neep.org/about/requests-proposals

    The generic scraper is too broad for NEEP because it captures informational
    pages such as "EM&V Products" and "Public Policy and Programs", which score
    highly but are not active procurement opportunities.

    This parser only returns links/listings that look like actual RFP/RFQ/RFI
    opportunities. If NEEP has no active postings, it returns zero candidates.
    """
    html = _fetch_page(url)
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")

    main_content = (
        soup.select_one("main")
        or soup.select_one("#main")
        or soup.select_one("#content")
        or soup.select_one(".main-content")
        or soup.select_one(".region-content")
        or soup
    )

    page_text = clean_text(main_content.get_text(" ", strip=True)).lower()

    no_active_markers = [
        "there are no current requests for proposals",
        "no current requests for proposals",
        "no active requests for proposals",
        "no active rfps",
        "no current rfps",
        "currently no rfps",
    ]

    if any(marker in page_text for marker in no_active_markers):
        logger.info("NEEP RFPs: no active RFPs detected on page")
        return []

    include_terms = [
        "rfp",
        "rfq",
        "rfi",
        "request for proposal",
        "request for proposals",
        "request for qualification",
        "request for qualifications",
        "request for information",
    ]

    exclude_terms = [
        "public policy and programs",
        "em&v products",
        "emv products",
        "regional roundup",
        "legislative and codes tracking",
        "federal policy resources",
        "energy efficiency plus beneficial electrification",
        "home",
        "about",
        "contact",
        "careers",
        "events",
        "news",
        "blog",
        "resources",
        "search",
        "login",
        "privacy",
        "terms",
        "read more",
        "requests for proposals",  # page title/self-link, not an opportunity
    ]

    opportunities = []
    seen_urls = set()

    for link in main_content.find_all("a", href=True):
        title = clean_text(link.get_text(" ", strip=True))
        href = link.get("href", "")

        if not title or len(title) < 8:
            continue

        title_l = title.lower()
        absolute_url = urllib.parse.urljoin(url, href)

        if absolute_url.rstrip("/") == url.rstrip("/"):
            continue

        if any(term in title_l for term in exclude_terms):
            continue

        parent_text = clean_text(
            link.parent.get_text(" ", strip=True),
            max_length=800,
        ) if link.parent else title

        combined_l = f"{title} {parent_text}".lower()

        if not any(term in combined_l for term in include_terms):
            continue

        if absolute_url in seen_urls:
            continue
        seen_urls.add(absolute_url)

        deadline = _extract_deadline_from_text(parent_text)

        opportunities.append(Opportunity(
            source=name,
            notice_id=absolute_url,
            url=absolute_url,
            title=title,
            description=parent_text or title,
            issuer="NEEP",
            state=state,
            deadline=deadline,
        ))

    logger.info(f"NEEP RFP parser: {len(opportunities)} entries parsed")
    return opportunities

def _fetch_nh_energy_page(url: str) -> Optional[str]:
    """
    Fetch the NH Department of Energy RFP page.

    The NH DOE site is protected by Akamai/EdgeSuite and returns 403 with the
    default lightweight requests headers. A fuller browser-like header set was
    confirmed to return the static HTML page with RFP headings and PDF links.
    """
    headers = dict(config.REQUEST_HEADERS)
    headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:151.0) "
            "Gecko/20100101 Firefox/151.0"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
    })

    try:
        response = requests.get(
            url,
            headers=headers,
            timeout=config.REQUEST_TIMEOUT,
            verify=True,
            allow_redirects=True,
        )

        if response.status_code == 403:
            logger.warning(
                f"NH DOE RFPs: 403 Forbidden for {url}. "
                f"The site may have changed bot-detection behavior."
            )
            return None

        if response.status_code == 404:
            logger.warning(f"NH DOE RFPs: 404 Not Found: {url}")
            return None

        if response.status_code == 429:
            logger.warning(f"NH DOE RFPs: 429 Rate Limited: {url}")
            return None

        response.raise_for_status()
        return response.text

    except requests.exceptions.RequestException as e:
        logger.warning(f"NH DOE RFPs: request failed for {url}: {e}")
        return None


def _scrape_nh_energy_rfps(url: str, name: str, state: str) -> List[Opportunity]:
    """
    Scrape the NH Department of Energy / Public Utilities Commission RFP page.

    Target page:
      https://www.energy.nh.gov/rules-and-regulatory/requests-proposals

    Page structure confirmed locally:
      <section>
        <h4>RFP 2026-002 Cost of Service and Rate Design Consultant - Electric</h4>
        <ul>
          <li><a>Main RFP PDF</a></li>
          <li><a>Questions and Answers</a></li>
          <li><a>Proposals Received</a></li>
          <li><a>Proposals Received and Rankings</a></li>
        </ul>
        <h4>Next RFP...</h4>
        <ul>...</ul>
      </section>

    The parser creates one Opportunity per h4/ul RFP block and intentionally
    skips support documents such as Q&A, addenda, proposals received, rankings,
    business requirements, applications, attachments, and cancellation notices.
    """
    html = _fetch_nh_energy_page(url)
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")

    main_content = (
        soup.select_one("main")
        or soup.select_one("#main")
        or soup.select_one("#content")
        or soup.select_one(".main-content")
        or soup.select_one(".region-content")
        or soup
    )

    exclude_link_terms = [
        "question",
        "questions",
        "answers",
        "q&a",
        "q and a",
        "addendum",
        "attachment",
        "application",
        "business requirements",
        "proposals received",
        "proposal received",
        "rankings",
        "notice of cancellation",
        "notice cancellation",
        "cancellation",
        "withdrawal",
        "contract template",
        "davis-bacon",
        "assurance letter",
        "updated questions",
    ]

    opportunities = []
    seen_notice_ids = set()

    for heading in main_content.find_all("h4"):
        heading_text = clean_text(heading.get_text(" ", strip=True), max_length=500)
        if not heading_text:
            continue

        # Keep only RFP headings, e.g. "RFP 2026-002 ..." or "RFP DoIT 2025-042 ..."
        if not heading_text.lower().startswith("rfp"):
            continue

        # Pull a stable notice ID from the heading.
        # Examples:
        #   RFP 2026-002 ...
        #   RFP DoIT 2025-042 ...
        notice_match = re.search(
            r"\bRFP\s+(?:DoIT\s+)?\d{4}-\d{3}\b",
            heading_text,
            flags=re.IGNORECASE,
        )
        notice_id = (
            notice_match.group(0).upper().replace("  ", " ")
            if notice_match
            else heading_text[:80]
        )

        if notice_id in seen_notice_ids:
            continue

        next_ul = heading.find_next_sibling("ul")
        if not next_ul:
            logger.debug(f"NH DOE RFPs: heading has no following list: {heading_text}")
            continue

        # If the listing already has proposals received/rankings posted, it is
        # almost certainly closed. Skip it to avoid cluttering the dashboard with
        # historical NH DOE procurements.
        block_text_l = clean_text(next_ul.get_text(" ", strip=True)).lower()
        closed_block_terms = [
            "proposals received",
            "proposal received",
            "rankings",
            "notice of cancellation",
            "notice cancellation",
            "cancellation",
            "withdrawal",
        ]

        if any(term in block_text_l for term in closed_block_terms):
            logger.debug(f"NH DOE RFPs: skipping likely closed listing: {heading_text}")
            continue

        chosen_link = None

        for link in next_ul.find_all("a", href=True):
            link_text = clean_text(link.get_text(" ", strip=True), max_length=500)
            href = link.get("href", "")

            if not link_text or not href:
                continue

            link_text_l = link_text.lower()
            href_l = href.lower()

            if any(term in link_text_l for term in exclude_link_terms):
                continue

            if any(term.replace(" ", "-") in href_l for term in exclude_link_terms):
                continue

            absolute_url = urllib.parse.urljoin(url, href)
            if not absolute_url.startswith(("http://", "https://")):
                continue

            chosen_link = (link_text, absolute_url)
            break

        if not chosen_link:
            logger.debug(f"NH DOE RFPs: no main RFP link found for {heading_text}")
            continue

        link_text, absolute_url = chosen_link
        seen_notice_ids.add(notice_id)

        # Page does not expose deadline/posted date in the listing. The PDFs may
        # contain deadlines, but this scraper does not parse PDFs.
        opportunities.append(Opportunity(
            source=name,
            notice_id=notice_id,
            url=absolute_url,
            title=heading_text,
            description=heading_text,
            issuer="New Hampshire Department of Energy / Public Utilities Commission",
            state=state,
            deadline=None,
            posted_date=None,
        ))

    logger.info(f"NH DOE RFP parser: {len(opportunities)} entries parsed")
    return opportunities

def _scrape_energy_trust_rfps(url: str, name: str, state: str) -> List[Opportunity]:
    """
    Scrape Energy Trust of Oregon contracting opportunities.

    Target page:
      https://www.energytrust.org/about/work-with-us/how-to-work-with-energy-trust/contracting-opportunities/

    Confirmed page structure:
      <div class="module-rfps__rfp">
        <h2 class="module-rfps__rfp__title">RFQ—...</h2>
        <h3 class="module-rfps__rfp__date">Posted on ...</h3>
        <div class="module-rfps__rfp__description">...</div>
        <div class="module-rfps__rfp__pdf"><a href="...">Download RFQ</a></div>
      </div>

    This parser avoids global navigation/menu links and creates one opportunity
    per visible RFQ/RFP card.
    """
    html = _fetch_page(url)
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")

    cards = soup.select(".module-rfps__rfp")
    if not cards:
        logger.info("Energy Trust RFPs: no opportunity cards found")
        return []

    opportunities = []
    seen_urls = set()

    for card in cards:
        title_el = card.select_one(".module-rfps__rfp__title")
        date_el = card.select_one(".module-rfps__rfp__date")
        desc_el = card.select_one(".module-rfps__rfp__description")
        link_el = card.select_one(".module-rfps__rfp__pdf a[href]") or card.find("a", href=True)

        title = clean_text(title_el.get_text(" ", strip=True), max_length=500) if title_el else ""
        posted_text = clean_text(date_el.get_text(" ", strip=True)) if date_el else ""
        description = clean_text(desc_el.get_text(" ", strip=True), max_length=1000) if desc_el else ""

        if not title:
            continue

        if not link_el:
            logger.debug(f"Energy Trust RFPs: no RFQ/RFP link found for {title}")
            continue

        href = link_el.get("href", "")
        absolute_url = urllib.parse.urljoin(url, href)

        if not absolute_url.startswith(("http://", "https://")):
            continue

        if absolute_url in seen_urls:
            continue
        seen_urls.add(absolute_url)

        posted_date = None
        if posted_text.lower().startswith("posted on"):
            posted_date = normalize_date(posted_text.replace("Posted on", "", 1).strip())
        else:
            posted_date = normalize_date(posted_text)

        # Capture deadline/close date from common page text patterns.
        # Example confirmed on page:
        #   "Consultants will be accepted into the pool on an ongoing basis through December 31, 2026."
        deadline = _extract_deadline_from_text(description)

        if not deadline:
            through_match = re.search(
                r"\bthrough\s+([A-Z][a-z]+\s+\d{1,2},\s+\d{4})",
                description,
                flags=re.IGNORECASE,
            )
            if through_match:
                deadline = normalize_date(through_match.group(1))

        notice_id = title
        if posted_date:
            notice_id = f"{title}::{posted_date}"

        opportunities.append(Opportunity(
            source=name,
            notice_id=notice_id,
            url=absolute_url,
            title=title,
            description=description or title,
            issuer="Energy Trust of Oregon",
            state=state,
            posted_date=posted_date,
            deadline=deadline,
        ))

    logger.info(f"Energy Trust RFP parser: {len(opportunities)} entries parsed")
    return opportunities

def _fetch_cape_light_page(url: str) -> Optional[str]:
    """
    Fetch Cape Light Compact's RFP page.

    The site is usually accessible with normal requests headers, but during
    testing it intermittently returned SSL EOF handshake errors. Use a
    short retry loop and Connection: close to reduce connection reuse issues.
    """
    headers = dict(config.REQUEST_HEADERS)
    headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:151.0) "
            "Gecko/20100101 Firefox/151.0"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Connection": "close",
    })

    last_error = None

    for attempt in range(1, 4):
        try:
            response = requests.get(
                url,
                headers=headers,
                timeout=config.REQUEST_TIMEOUT,
                verify=True,
                allow_redirects=True,
            )
            response.raise_for_status()
            return response.text

        except requests.exceptions.SSLError as e:
            last_error = e
            logger.warning(
                f"Cape Light RFPs: SSL error on attempt {attempt}/3 for {url}: {e}"
            )
            time.sleep(1)

        except requests.exceptions.RequestException as e:
            logger.warning(f"Cape Light RFPs: request failed for {url}: {e}")
            return None

    logger.warning(f"Cape Light RFPs: failed after retries due to SSL error: {last_error}")
    return None


def _scrape_cape_light_rfps(url: str, name: str, state: str) -> List[Opportunity]:
    """
    Scrape Cape Light Compact's RFP/RFI page.

    Target page:
      https://www.capelightcompact.org/news-and-resources/request-for-proposals-rfp/

    Confirmed page structure:
      - current/open listings appear before an "Archive" heading
      - historical listings appear after "Archive"
      - the page includes support documents such as responses to questions,
        Q&A, addenda, confidentiality agreements, attachments, and Zoom links

    This parser only reads links before the Archive heading and skips support
    documents, creating one Opportunity per current RFP/RFI link.
    """
    html = _fetch_cape_light_page(url)
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")

    main_content = (
        soup.find("main")
        or soup.select_one("#content")
        or soup.select_one(".site-main")
        or soup.select_one(".entry-content")
        or soup
    )

    archive_heading = None
    for heading in main_content.find_all(["h2", "h3", "h4", "h5"]):
        heading_text = clean_text(heading.get_text(" ", strip=True)).lower()
        if heading_text == "archive":
            archive_heading = heading
            break

    skip_terms = [
        "responses to questions",
        "response to questions",
        "questions received",
        "questions and answers",
        "q&a",
        "q & a",
        "addendum",
        "addenda",
        "confidentiality agreement",
        "non-disclosure",
        "nda",
        "mutual nda",
        "informational conference",
        "conference call",
        "zoom",
        "click here",
        "attachment",
        "scope of work",
        "form of contract",
        "pricing sheet",
        "cost bid sheet",
        "legal ad",
        "redline",
    ]

    opportunities = []
    seen_urls = set()

    # Walk the main-content descendants in document order and stop at Archive.
    for element in main_content.descendants:
        if archive_heading is not None and element is archive_heading:
            break

        if getattr(element, "name", None) != "a":
            continue

        href = element.get("href", "")
        link_text = clean_text(element.get_text(" ", strip=True), max_length=500)

        if not href or not link_text:
            continue

        link_text_l = link_text.lower()
        href_l = href.lower()

        if any(term in link_text_l for term in skip_terms):
            continue

        if any(term.replace(" ", "-") in href_l for term in skip_terms):
            continue

        # Avoid site navigation links and keep only likely RFP/RFI/RFQ links.
        if not any(term in link_text_l for term in ["rfp", "rfi", "rfq", "proposal", "quotations", "quotes"]):
            continue

        absolute_url = urllib.parse.urljoin(url, href)
        if not absolute_url.startswith(("http://", "https://")):
            continue

        if absolute_url in seen_urls:
            continue
        seen_urls.add(absolute_url)

        # The visible date is in text immediately before the link, but the
        # link text itself is cleaner and stable. Use the file URL or link title
        # as notice_id because Cape Light does not expose formal notice IDs.
        opportunities.append(Opportunity(
            source=name,
            notice_id=absolute_url,
            url=absolute_url,
            title=link_text,
            description=link_text,
            issuer="Cape Light Compact",
            state=state,
            posted_date=None,
            deadline=None,
        ))

    logger.info(f"Cape Light RFP parser: {len(opportunities)} entries parsed")
    return opportunities

def _scrape_pge_ee_solicitations(url: str, name: str, state: str) -> List[Opportunity]:
    """
    Scrape PG&E's Energy Efficiency third-party solicitations page.

    Target page:
      https://www.pge.com/en/about/doing-business-with-pge/solicitations.html

    Confirmed structure:
      - active/upcoming solicitations are embedded in an AEM table component
      - the table is stored as JSON in .table-data[data-table]
      - tableTitle = "Active and upcoming solicitations"
      - columns include Program and Description

    Creates one Opportunity per row in the active/upcoming solicitations table.
    Uses an embedded PowerAdvocate link when available; otherwise uses the PG&E
    solicitations page URL.
    """
    html = _fetch_page(url)
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")

    opportunities = []
    seen_ids = set()

    for table_el in soup.select(".table-data[data-table]"):
        raw_table = table_el.get("data-table", "")

        if not raw_table:
            continue

        try:
            table_data = json.loads(raw_table)
        except json.JSONDecodeError as e:
            logger.warning(f"PG&E EE solicitations: failed to parse data-table JSON: {e}")
            continue

        table_title = clean_text(table_data.get("tableTitle", ""))
        if table_title.lower() != "active and upcoming solicitations":
            continue

        columns = table_data.get("columnDetails", [])
        if not columns:
            continue

        row_count = max(
            len(col.get("rowDetails", []))
            for col in columns
            if isinstance(col.get("rowDetails", []), list)
        )

        for row_index in range(row_count):
            row_values = {}
            row_links = []

            for col in columns:
                column_name = clean_text(col.get("columnName", ""))
                row_details = col.get("rowDetails", [])

                if row_index >= len(row_details):
                    continue

                raw_value = row_details[row_index].get("rowValue", "")
                value_soup = BeautifulSoup(raw_value, "html.parser")
                value_text = clean_text(value_soup.get_text(" ", strip=True), max_length=2000)

                row_values[column_name.lower()] = value_text

                for link in value_soup.find_all("a", href=True):
                    link_text = clean_text(link.get_text(" ", strip=True), max_length=500)
                    href = link.get("href", "")
                    absolute_url = urllib.parse.urljoin(url, href)

                    if absolute_url.startswith(("http://", "https://")):
                        row_links.append((link_text, absolute_url))

            program = row_values.get("program", "")
            description = row_values.get("description", "")

            if not program:
                continue

            # Prefer the PowerAdvocate event link when the row exposes one.
            opportunity_url = url
            for link_text, link_url in row_links:
                if "poweradvocate.com" in link_url.lower():
                    opportunity_url = link_url
                    break

            notice_id = f"{program}::{opportunity_url}"

            if notice_id in seen_ids:
                continue
            seen_ids.add(notice_id)

            opportunities.append(Opportunity(
                source=name,
                notice_id=notice_id,
                url=opportunity_url,
                title=program,
                description=description or program,
                issuer="Pacific Gas and Electric Company",
                state=state,
                posted_date=None,
                deadline=None,
            ))

    logger.info(f"PG&E EE solicitations parser: {len(opportunities)} entries parsed")
    return opportunities

def _scrape_entergy_rfps(url: str, name: str, state: str) -> List[Opportunity]:
    """
    Scrape Entergy System Planning and Operations RFP links.

    Target page:
      https://rfp.entergy.com/

    Confirmed structure:
      - static HTML page
      - left-side RFP menu is normal <a href> links
      - page includes many historical RFPs back to 2014

    This parser keeps only current-year or future-year RFPs based on the
    year in the title. It intentionally skips prior-year RFPs because
    Entergy leaves historical solicitations on the page and they can
    otherwise continue to appear as active opportunities.

    It intentionally does not recurse into every RFP subpage because the
    source is mostly generation/resource procurement and can become noisy.
    """
    html = _fetch_page(url)
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")

    current_year = datetime.utcnow().year

    skip_terms = [
        "archived documents",
        "co-owner website",
        "entergy.com",
        "privacy policy",
        "terms of use",
        "esl request for proposal",
    ]

    opportunities = []
    seen_urls = set()

    for link in soup.find_all("a", href=True):
        title = clean_text(link.get_text(" ", strip=True), max_length=500)
        href = link.get("href", "")

        if not title or not href:
            continue

        title_l = title.lower()

        if any(term in title_l for term in skip_terms):
            continue

        if "rfp" not in title_l:
            continue

        year_match = re.search(r"\b(20\d{2})\b", title)
        if not year_match:
            continue

        year = int(year_match.group(1))

        # Entergy keeps historical RFPs on the live page.
        # For this monitor, skip prior-year solicitations so stale items like
        # "2025 ETI Demand Response RFP" do not keep appearing in 2026 runs.
        if year < current_year:
            logger.info(f"Entergy RFP parser: skipping stale prior-year RFP: {title}")
            continue

        absolute_url = urllib.parse.urljoin(url, href)
        if not absolute_url.startswith(("http://", "https://")):
            continue

        if absolute_url in seen_urls:
            continue
        seen_urls.add(absolute_url)

        opportunities.append(Opportunity(
            source=name,
            notice_id=f"{title}::{absolute_url}",
            url=absolute_url,
            title=title,
            description=title,
            issuer="Entergy",
            state=state,
            posted_date=None,
            deadline=None,
        ))

    logger.info(f"Entergy RFP parser: {len(opportunities)} entries parsed")
    return opportunities

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

def _infer_state_from_text(text: str) -> str:
    """
    Infer a U.S. state abbreviation from opportunity title/description text.

    This is mainly useful for aggregator sources like AESP, which list RFPs
    from multiple utilities and state agencies on one page. If no state can be
    inferred confidently, return an empty string and let the dashboard display
    "--".
    """
    if not text:
        return ""

    text_l = text.lower()

    state_terms = {
        "AL": ["alabama"],
        "AK": ["alaska"],
        "AZ": ["arizona"],
        "AR": ["arkansas"],
        "CA": ["california"],
        "CO": ["colorado"],
        "CT": ["connecticut", "ct energy efficiency board"],
        "DE": ["delaware"],
        "FL": ["florida"],
        "GA": ["georgia"],
        "HI": ["hawaii", "state of hawaii", "hawaii public utilities commission"],
        "ID": ["idaho"],
        "IL": ["illinois", "comed", "commonwealth edison"],
        "IN": ["indiana"],
        "IA": ["iowa"],
        "KS": ["kansas"],
        "KY": ["kentucky"],
        "LA": ["louisiana"],
        "ME": ["maine", "efficiency maine"],
        "MD": ["maryland"],
        "MA": ["massachusetts"],
        "MI": ["michigan"],
        "MN": ["minnesota"],
        "MS": ["mississippi"],
        "MO": ["missouri"],
        "MT": ["montana"],
        "NE": ["nebraska"],
        "NV": ["nevada"],
        "NH": ["new hampshire"],
        "NJ": ["new jersey"],
        "NM": ["new mexico"],
        "NY": ["new york", "national grid ny"],
        "NC": ["north carolina"],
        "ND": ["north dakota"],
        "OH": ["ohio"],
        "OK": ["oklahoma"],
        "OR": ["oregon"],
        "PA": ["pennsylvania"],
        "RI": ["rhode island"],
        "SC": ["south carolina"],
        "SD": ["south dakota"],
        "TN": ["tennessee"],
        "TX": ["texas"],
        "UT": ["utah"],
        "VT": ["vermont"],
        "VA": ["virginia"],
        "WA": ["washington"],
        "WV": ["west virginia"],
        "WI": ["wisconsin"],
        "WY": ["wyoming"],
        "DC": ["district of columbia", "washington dc", "washington, dc"],
    }

    for abbrev, terms in state_terms.items():
        if any(term in text_l for term in terms):
            return abbrev

    return ""

def _scrape_ct_eeb_rfps(url: str, name: str, state: str) -> List[Opportunity]:
    """
    Scrape the Connecticut Energy Efficiency Board RFP/RFQ page.

    Target page:
      https://www.energizect.com/connecticut-energy-efficiency-board/rfps

    The page redirects to:
      https://www.energizect.com/eeb-request-proposals

    Current confirmed structure:
      - "Open RFPs/RFQs:" heading
      - "There are currently no open RFPs/RFQs." when empty
      - "Supporting Documents:" heading after the open section

    The parser only reads the Open RFPs/RFQs section and intentionally stops
    before Supporting Documents so travel guidelines, terms and conditions,
    NDAs, and similar supporting files are not treated as opportunities.
    """
    html = _fetch_page(url)
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")

    main_content = (
        soup.select_one("main")
        or soup.select_one("#main")
        or soup.select_one("#content")
        or soup.select_one(".main-content")
        or soup.select_one(".region-content")
        or soup
    )

    page_text_l = clean_text(main_content.get_text(" ", strip=True)).lower()

    if "there are currently no open rfps/rfqs" in page_text_l:
        logger.info("CT EEB RFPs: page reports no open RFPs/RFQs")
        return []

    open_heading = None
    for heading in main_content.find_all(["h3", "h4", "h5", "h6"]):
        heading_text_l = clean_text(heading.get_text(" ", strip=True)).lower()
        if "open rfps/rfqs" in heading_text_l or "open rfps" in heading_text_l:
            open_heading = heading
            break

    if not open_heading:
        logger.info("CT EEB RFPs: no Open RFPs/RFQs heading found")
        return []

    opportunities = []
    seen_urls = set()

    # Walk forward from the Open RFPs/RFQs heading until the next heading.
    # Stop before Supporting Documents or any later page section.
    for sibling in open_heading.find_next_siblings():
        if sibling.name in ["h2", "h3", "h4", "h5", "h6"]:
            sibling_text_l = clean_text(sibling.get_text(" ", strip=True)).lower()
            if (
                "supporting documents" in sibling_text_l
                or "submit a new technology" in sibling_text_l
                or "apply to become" in sibling_text_l
            ):
                break

        sibling_text_l = clean_text(sibling.get_text(" ", strip=True)).lower()
        if "there are currently no open rfps/rfqs" in sibling_text_l:
            logger.info("CT EEB RFPs: page reports no open RFPs/RFQs")
            return []

        for link in sibling.find_all("a", href=True):
            link_text = clean_text(link.get_text(" ", strip=True), max_length=500)
            href = link.get("href", "")

            if not link_text or not href:
                continue

            link_text_l = link_text.lower()
            href_l = href.lower()

            # Avoid accidental capture of support docs if page structure changes.
            skip_terms = [
                "travel guidelines",
                "terms and conditions",
                "nda",
                "non-disclosure",
                "organizational conflict",
                "supporting document",
            ]
            if any(term in link_text_l for term in skip_terms):
                continue
            if any(term.replace(" ", "-") in href_l for term in skip_terms):
                continue

            absolute_url = urllib.parse.urljoin(url, href)
            if not absolute_url.startswith(("http://", "https://")):
                continue

            if absolute_url in seen_urls:
                continue
            seen_urls.add(absolute_url)

            opportunities.append(Opportunity(
                source=name,
                notice_id=absolute_url,
                url=absolute_url,
                title=link_text,
                description=link_text,
                issuer="Connecticut Energy Efficiency Board",
                state=state,
                deadline=None,
                posted_date=None,
            ))

    logger.info(f"CT EEB RFP parser: {len(opportunities)} entries parsed")
    return opportunities

def _scrape_aesp_rfps(url: str, name: str, state: str) -> List[Opportunity]:
    """
    Scrape AESP's Active RFPs, RFQs, and RFIs section.

    Target page:
      https://aesp.org/community/news-and-rpfs/

    AESP also posts member news on the same page, so this parser only captures
    entries under the "Active RFPs, RFQs, and RFIs" heading and stops before the
    "Members news" heading.
    """
    html = _fetch_page(url)
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")

    main_content = (
        soup.select_one("main")
        or soup.select_one("#main")
        or soup.select_one("#content")
        or soup.select_one(".entry-content")
        or soup
    )

    # Locate the active RFP/RFQ/RFI section.
    active_heading = None
    for heading in main_content.find_all(["h1", "h2", "h3", "h4"]):
        heading_text = clean_text(heading.get_text(" ", strip=True)).lower()
        if (
            "active" in heading_text
            and ("rfp" in heading_text or "rfq" in heading_text or "rfi" in heading_text)
        ):
            active_heading = heading
            break

    if not active_heading:
        logger.info("AESP RFPs: no Active RFP/RFQ/RFI heading found")
        return []

    opportunities = []
    seen_urls = set()

    # AESP entries appear as headings like:
    #   Due: July 10, 2026 / Request for Proposal: ...
    # followed by descriptive text and a "View full RFP/RFQ" link.
    for entry_heading in active_heading.find_all_next(["h1", "h2", "h3", "h4", "h5"]):
        heading_text = clean_text(entry_heading.get_text(" ", strip=True))

        # Stop when the page moves into the member-news section.
        if "members news" in heading_text.lower() or "member news" in heading_text.lower():
            break

        if not heading_text:
            continue

        heading_l = heading_text.lower()
        if not any(term in heading_l for term in ["rfp", "rfq", "rfi", "request for proposal", "request for quotation", "request for information"]):
            continue

        # Collect nearby text until the next heading, so the description includes
        # the RFP summary and the "View full RFP" link context.
        description_parts = [heading_text]
        links = []

        for sibling in entry_heading.find_next_siblings():
            if sibling.name in ["h1", "h2", "h3", "h4", "h5"]:
                break

            sibling_text = clean_text(sibling.get_text(" ", strip=True))
            if sibling_text:
                description_parts.append(sibling_text)

            for link in sibling.find_all("a", href=True):
                link_text = clean_text(link.get_text(" ", strip=True))
                href = link.get("href", "")
                absolute_url = urllib.parse.urljoin(url, href)

                if not absolute_url.startswith(("http://", "https://")):
                    continue

                links.append((link_text, absolute_url))

        description = clean_text(" ".join(description_parts), max_length=1000)

        # Prefer "View full RFP/RFQ/RFI" links. Fall back to first usable link.
        chosen_url = None
        for link_text, absolute_url in links:
            link_l = link_text.lower()
            if "view full" in link_l or "full rfp" in link_l or "full rfq" in link_l or "full rfi" in link_l:
                chosen_url = absolute_url
                break

        if not chosen_url and links:
            chosen_url = links[0][1]

        if not chosen_url:
            chosen_url = url

        if chosen_url in seen_urls:
            continue
        seen_urls.add(chosen_url)

        # Split "Due: date / title" into deadline and title.
        deadline = _extract_deadline_from_text(heading_text)
        title = heading_text

        if "/" in heading_text:
            parts = [p.strip() for p in heading_text.split("/", 1)]
            if len(parts) == 2:
                title = parts[1]

        # Extra deadline fallback for headings like "Due: July 10, 2026 / ..."
        if not deadline:
            due_match = re.search(
                r"due:\s*([A-Z][a-z]+ \d{1,2}, \d{4}|\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d{2}-\d{2})",
                heading_text,
                flags=re.IGNORECASE,
            )
            if due_match:
                deadline = normalize_date(due_match.group(1))

        if deadline:
            try:
                deadline_date = datetime.strptime(deadline, "%Y-%m-%d").date()
                today = datetime.utcnow().date()
                if deadline_date < today:
                    logger.info(
                        f"AESP RFP parser: skipping expired opportunity "
                        f"with deadline {deadline}: {title}"
                    )
                    continue
            except ValueError:
                logger.warning(
                    f"AESP RFP parser: could not parse deadline {deadline!r} "
                    f"for {title}; keeping opportunity"
                )

        opportunities.append(Opportunity(
            source=name,
            notice_id=chosen_url,
            url=chosen_url,
            title=clean_text(title, max_length=300),
            description=description,
            issuer="AESP",
            state=_infer_state_from_text(f"{title} {description}") or state,
            deadline=deadline,
        ))

    logger.info(f"AESP RFP parser: {len(opportunities)} entries parsed")
    return opportunities

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

def _scrape_veic_rfps(url: str, name: str, state: str) -> List[Opportunity]:
    """
    Scrape the VEIC RFP page, which also lists current Efficiency Vermont
    RFPs/RFQs/RFIs.

    Target page:
      https://www.veic.org/organization/rfps

    VEIC's page is currently accessible by the requests-based scraper. When
    there are no active opportunities, the page may contain mostly navigation
    links and a message indicating there are no active RFPs. This parser avoids
    returning navigation links and only emits links that look like actual
    RFP/RFQ/RFI opportunities.
    """
    html = _fetch_page(url)
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    page_text = clean_text(soup.get_text(" ", strip=True)).lower()

    no_active_markers = [
        "there are currently no active rfps",
        "no active rfps",
        "no current rfps",
        "no open requests",
    ]

    if any(marker in page_text for marker in no_active_markers):
        logger.info("VEIC RFPs: no active RFPs detected on page")
        return []

    main_content = (
        soup.select_one("main")
        or soup.select_one("#main")
        or soup.select_one("#main-content")
        or soup.select_one(".main-content")
        or soup
    )

    include_terms = [
        "rfp",
        "rfq",
        "rfi",
        "request for proposal",
        "request for proposals",
        "request for qualification",
        "request for qualifications",
        "request for information",
    ]

    exclude_terms = [
        "skip to main content",
        "privacy policy",
        "conflict of interest policy",
        "contact us",
        "careers",
        "case studies",
        "reports & insights",
        "our capabilities",
        "our organization",
        "who we work with",
        "connect with us",
    ]

    opportunities = []
    seen_urls = set()

    for link in main_content.find_all("a", href=True):
        title = clean_text(link.get_text(" ", strip=True))
        if not title or len(title) < 5:
            continue

        title_l = title.lower()

        if any(term in title_l for term in exclude_terms):
            continue

        href = link.get("href", "")
        absolute_url = urllib.parse.urljoin(url, href)

        if not absolute_url.startswith(("http://", "https://")):
            continue

        parent_text = clean_text(
            link.parent.get_text(" ", strip=True),
            max_length=500,
        ) if link.parent else title

        combined_l = f"{title} {parent_text}".lower()

        if not any(term in combined_l for term in include_terms):
            continue

        if absolute_url in seen_urls:
            continue
        seen_urls.add(absolute_url)

        deadline = _extract_deadline_from_text(parent_text)

        opportunities.append(Opportunity(
            source=name,
            notice_id=absolute_url,
            url=absolute_url,
            title=title,
            description=parent_text or title,
            issuer="VEIC / Efficiency Vermont",
            state=state,
            deadline=deadline,
        ))

    logger.info(f"VEIC RFP dedicated parser: {len(opportunities)} entries parsed")
    return opportunities

def _scrape_efficiency_maine_rfps(url: str, name: str, state: str) -> List[Opportunity]:
    """
    Scrape Efficiency Maine opportunity postings.

    Target page:
      https://www.efficiencymaine.com/opportunities/

    The generic scraper is too broad for this page because it captures
    navigation links such as "Getting Started" and "Income-Based Eligibility
    Verification", and it does not visit individual opportunity pages where
    due dates/deadlines are typically listed.

    This parser keeps only opportunity-detail links and fetches each detail
    page to extract fuller description/deadline context.
    """
    html = _fetch_page(url)
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")

    main_content = (
        soup.select_one("main")
        or soup.select_one("#main")
        or soup.select_one("#content")
        or soup.select_one(".main-content")
        or soup.select_one(".entry-content")
        or soup
    )

    opportunities = []
    seen_urls = set()

    include_url_terms = [
        "/opportunities/rfp-",
        "/opportunities/rfq-",
        "/opportunities/rfi-",
    ]

    include_title_terms = [
        "request for proposal",
        "request for proposals",
        "request for qualifications",
        "request for quotation",
        "request for information",
        "rfp",
        "rfq",
        "rfi",
        "evaluation",
        "verification",
        "grid modernization",
        "support services",
    ]

    exclude_title_terms = [
        "getting started",
        "income-based eligibility verification",
        "vendor support",
        "forms and brochures",
        "home energy loans",
    ]

    for link in main_content.find_all("a", href=True):
        title = clean_text(link.get_text(" ", strip=True))
        href = link.get("href", "")
        absolute_url = urllib.parse.urljoin(url, href)

        if not title or len(title) < 8:
            continue

        title_l = title.lower()
        url_l = absolute_url.lower()

        if absolute_url in seen_urls:
            continue

        if any(term in title_l for term in exclude_title_terms):
            continue

        # Keep only actual opportunity detail pages.
        if not any(term in url_l for term in include_url_terms):
            continue

        # Also require the title to look procurement/evaluation related.
        if not any(term in title_l for term in include_title_terms):
            continue

        seen_urls.add(absolute_url)

        detail_html = _fetch_page(absolute_url)
        detail_text = ""
        if detail_html:
            detail_soup = BeautifulSoup(detail_html, "html.parser")
            detail_links = [
                (
                    clean_text(a.get_text(" ", strip=True)),
                    urllib.parse.urljoin(absolute_url, a.get("href", "")),
                )
                for a in detail_soup.find_all("a", href=True)
            ]

            # If Efficiency Maine has posted a Notice of Award, the RFP/RFQ is
            # no longer an active opportunity. Exclude it from the dashboard.
            closed_notice_terms = [
                "notice of award",
                "notice of prequalification",
            ]

            if any(
                    any(term in link_text.lower() for term in closed_notice_terms)
                    for link_text, _ in detail_links
            ):
                logger.info(f"Efficiency Maine: skipping closed/awarded opportunity: {title}")
                continue
            detail_main = (
                detail_soup.select_one("main")
                or detail_soup.select_one("#main")
                or detail_soup.select_one("#content")
                or detail_soup.select_one(".main-content")
                or detail_soup.select_one(".entry-content")
                or detail_soup
            )
            detail_text = clean_text(detail_main.get_text(" ", strip=True), max_length=1500)

        description = detail_text or title
        deadline = _extract_deadline_from_text(description)

        opportunities.append(Opportunity(
            source=name,
            notice_id=absolute_url,
            url=absolute_url,
            title=title,
            description=description,
            issuer="Efficiency Maine",
            state=state,
            deadline=deadline,
        ))

    logger.info(f"Efficiency Maine parser: {len(opportunities)} entries parsed")
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

def _extract_naseo_deadline_from_text(text: str) -> Optional[str]:
    """
    Extract deadline dates from NASEO listing text.

    NASEO listings often use phrasing such as:
      - "must be received by May 28, 2026..."
      - "postmarked on May 28, 2026..."
      - "received no later than June 8, 2026..."

    The generic deadline extractor may miss these because the sentence can
    include multiple deadlines and location-specific instructions. For NASEO,
    use the latest parsed date near deadline-like language as the practical
    deadline candidate.
    """
    if not text:
        return None

    date_pattern = r"(\b[A-Z][a-z]+ \d{1,2}, \d{4}\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b)"

    trigger_window_pattern = (
        r"(?:due|deadline|received by|must be received by|received no later than|"
        r"postmarked on|postmarked by|responses must be submitted|proposals must be submitted)"
        r".{0,150}?"
        + date_pattern
    )

    matches = re.findall(trigger_window_pattern, text, flags=re.IGNORECASE)

    normalized_dates = []
    for match in matches:
        # re.findall returns strings when there is one capturing group, but
        # tuples if the regex changes later. Keep this guarded.
        date_text = match if isinstance(match, str) else match[-1]
        normalized = normalize_date(date_text)
        if normalized:
            normalized_dates.append(normalized)

    if normalized_dates:
        # Use latest date because NASEO sometimes gives one date for local
        # proposers and a later received-by date for out-of-area proposers.
        return max(normalized_dates)

    return _extract_deadline_from_text(text)
