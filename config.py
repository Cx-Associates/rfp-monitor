"""
config.py -- Central Configuration for the CxA RFP Monitor
===========================================================
All tunable parameters live here. Edit this file (never the core logic
modules) to adjust keywords, sources, delivery settings, and thresholds.

KEYWORD MODE SWITCH
-------------------
Set KEYWORD_MODE to "broad" or "medium" to control sensitivity.

  "broad"  -- Catches the widest net. Will include some non-EM&V energy
              efficiency work. Recommended starting point; tune down if
              the digest gets noisy.

  "medium" -- EM&V core terms + program evaluation language. Fewer
              false positives. Switch here once you've characterized
              what the broad mode is catching.

Change it to "medium" here and re-deploy (or just set it when running
locally via the --mode CLI flag) to narrow the net without touching
keyword lists.

KNOWN FAILURE POINTS:
  - API keys are read from environment variables. If a key is missing,
    that source/channel is skipped with a warning; the run continues.
  - ClickUp delivery is intentionally excluded from this build.
  - State portal scrapers use a federated approach (SAM.gov + Google
    Custom Search) for broad US coverage rather than hand-coding 50
    individual state portal scrapers, most of which would be brittle.
"""

# ---------------------------------------------------------------------------
# KEYWORD MODE SWITCH
# Set to "broad" to start. Change to "medium" when ready to narrow.
# Can also be overridden at runtime: python main.py --mode medium
# ---------------------------------------------------------------------------

KEYWORD_MODE = "broad"   # Options: "broad" | "medium"

# ---------------------------------------------------------------------------
# KEYWORD LISTS BY TIER
#
# Primary: Core EM&V terms -- high confidence, always active in both modes
# Secondary: Program evaluation language -- active in both modes
# Tertiary: Adjacent energy efficiency scope -- active in "broad" mode only
#
# To ADD a keyword: append it to the appropriate list below.
# To SUPPRESS a keyword without deleting it: prefix with "#" or move it
# to a commented-out section. No other files need to change.
# ---------------------------------------------------------------------------

KEYWORDS_PRIMARY = [
    # EM&V core terminology
    "evaluation, measurement and verification",
    "evaluation, measurement, and verification",
    "evaluation measurement and verification",
    "evaluation measurement verification",
    "EM&V",
    "EMV",
    "M&V",
    "MV",
    "IPMVP",
    "measurement and verification",
    "measurement & verification",
    "savings verification",
    "energy savings verification",
    "energy efficiency evaluation",

    # Common RFP title patterns for evaluation work
    "EM&V services",
    "EMV services",
    "M&V services",
    "MV services",
    "evaluation services energy",
]

KEYWORDS_SECONDARY = [
    # Program evaluation language
    "program evaluation",
    "impact evaluation",
    "impact study",
    "process evaluation",
    "market effects study",
    "net-to-gross",
    "net to gross",
    "free ridership",
    "free-ridership",
    "spillover",
    "baseline study",
    "baseline research",
    "deemed savings",
    "custom measure evaluation",
    "portfolio evaluation",
    "energy efficiency program evaluation",
    "utility program evaluation",
    "DSM evaluation",
    "demand-side management evaluation",
    "demand side management evaluation",
    "load impact study",
    "load impact evaluation",
    "demand side management",
    "evaluation and research",
    "research and evaluation",
    "planning, evaluation and research",
    "planning evaluation research",
    "evaluation research tasks",

    # EM&V-adjacent research terms
    "energy savings study",
    "energy audit verification",
    "technical engineering support",
    "technical review",
    "engineering review",
    "project review",
    "custom project review",
    "custom savings review",
    "savings analysis",
    "ex post evaluation",
    "ex ante review",
    "realization rate",
    "gross savings",
    "verified savings",
    "claimed savings",
    "reported savings",
]

KEYWORDS_TERTIARY = [
    # Broader energy efficiency terms -- active in "broad" mode only
    "energy efficiency",
    # "energy",  # intentionally not included; too broad/noisy
    "demand response evaluation",
    "demand response",
    "load research",
    "load forecasting study",
    "building energy study",
    "energy benchmarking",
    "utility program",
    "rate case analysis",
    "integrated resource plan",
    "energy data analysis",
    "energy consulting services",
    "energy technical assistance",
    "commissioning evaluation",        # CxA-specific: Cx overlap with EM&V
    "retrocommissioning evaluation",
    "retro-commissioning evaluation",
    "building performance",
    "decarbonization study",
    "greenhouse gas evaluation",
    "greenhouse gas accounting",
    "technical support services",
    "energy audit",
    "energy audits",
    "commercial energy audit",
    "energy assessment",
    "energy assessments",
    "energy engineering services",
    "energy program support",
    "program implementation support",
    "quality assurance",
    "quality control",
    "QA/QC",
    "TRM review",
    "technical reference manual",
    "deemed measure",
    "custom measures",
    "benefit cost analysis",
    "cost effectiveness",
    "cost-effectiveness",
    "non-energy impacts",
    "non energy impacts",
]

# ---------------------------------------------------------------------------
# RELEVANCE SCORING WEIGHTS AND THRESHOLDS
#
# Tuning guide:
#   Getting too many unrelated results? Raise MIN_SCORE_INCLUDE_BROAD.
#   Missing real EM&V RFPs?           Lower MIN_SCORE_INCLUDE_BROAD or promote
#                                     a keyword from tertiary to secondary.
# ---------------------------------------------------------------------------

SCORE_PRIMARY_MATCH   = 10   # Primary keyword found in title or description
SCORE_SECONDARY_MATCH = 5    # Secondary keyword match
SCORE_TERTIARY_MATCH  = 2    # Tertiary keyword match (broad mode only)
SCORE_TITLE_BONUS     = 5    # Extra points when keyword appears in TITLE

# Minimum score to include in output at all
MIN_SCORE_INCLUDE_BROAD  = 2    # Very low bar in broad mode; scorer handles noise
MIN_SCORE_INCLUDE_MEDIUM = 5    # Requires at least one secondary match or title hit

# Score at or above this is labeled "High Relevance"
MIN_SCORE_HIGH_CONFIDENCE = 15

# ---------------------------------------------------------------------------
# SAM.GOV API (Federal opportunities -- the cleanest, most reliable source)
#
# Free API key: https://sam.gov/profile/details
# Rate limit: 10 req/day (public), 1,000 req/day (entity-registered)
#
# KNOWN FAILURE POINTS:
#   - Title-only search (no full-text search of attachments) means some
#     EM&V RFPs with generic titles slip through. NAICS code queries
#     compensate for this.
#   - Rate limit: do not run more than once per day on a public (10/day) key.
#   - API schema changes without notice; field mapping is in sam_gov.py.
# ---------------------------------------------------------------------------

SAM_API_BASE_URL   = "https://api.sam.gov/prod/opportunities/v2/search"
SAM_LOOKBACK_DAYS  = 9      # Days back to search each run (9 = weekly + 2-day buffer)
SAM_MAX_RESULTS    = 100    # Results per query (API max 1000; keep lower)

# NAICS codes most likely to yield EM&V / engineering evaluation consulting work
# 541690 = Other Scientific and Technical Consulting
# 541620 = Environmental Consulting
# 541330 = Engineering Services
# 541712 = R&D in the Physical, Engineering, and Life Sciences (covers energy research)
SAM_NAICS_CODES = ["541690", "541620", "541330", "541712"]

# SAM.gov keyword queries -- short phrases that work with title-only search.
SAM_SEARCH_QUERIES = [
    "evaluation measurement verification",
    "M&V measurement verification",
    "measurement verification energy",
    "program evaluation energy",
    "impact evaluation energy efficiency",
    "IPMVP",
    "baseline study energy",
    "energy savings verification",
    "DSM evaluation",
    "demand side management evaluation",
    "energy efficiency evaluation services",
    "evaluation services energy efficiency",
    "utility program evaluation",
    "demand response evaluation",
    "load impact evaluation",
]

# ---------------------------------------------------------------------------
# UTILITY AND QUASI-PUBLIC SOURCES
# ---------------------------------------------------------------------------

UTILITY_SOURCES = [
    # --- National / multi-state quasi-publics ---
    # NOTE: NASEO is handled by its own dedicated scraper (fetch_naseo) and is
    # intentionally NOT listed here to avoid scraping it twice.
    {
        "name": "NEEP (Northeast Energy Efficiency Partnerships)",
        "url": "https://neep.org/about/requests-proposals",
        "state": "",
        "type": "neep_rfps",
        "js_render": False,
        "active": True,
        "notes": (
            "NEEP RFP page. Uses a dedicated parser to avoid informational pages."
        ),
    },
    {
        "name": "ACEEE",
        "url": "https://www.aceee.org/about/work-aceee",
        "js_render": False,
        "active": False,
        "notes": "No confirmed RFP page. Disabled until a valid URL is found.",
    },
    {
        "name": "E4TheFuture",
        "url": "https://e4thefuture.org/resources/rfps/",
        "js_render": False,
        "active": False,
        "notes": "Site appears JS-rendered. Disabled until Playwright upgrade.",
    },
    # --- New England utilities ---
    {
        "name": "NYSERDA",
        "url": "https://www.nyserda.ny.gov/Funding-Opportunities/Requests-for-Proposals",
        "js_render": False,
        "active": True,
        "notes": "Top EM&V issuer. Check weekly.",
    },
    {
        "name": "ISO-NE Solicitations",
        "url": "https://www.iso-ne.com/system-planning/transmission-planning/competitive-transmission",
        "js_render": False,
        "active": True,
        "notes": "Confirmed URL for ISO-NE competitive transmission RFPs.",
    },
    {
        "name": "Eversource (MA/CT/NH)",
        "url": "https://www.eversource.com/content/ema/about/doing-business-with-us/vendors-suppliers/request-for-proposals",
        "js_render": False,
        "active": True,
        "notes": "Regularly issues program evaluation and EM&V RFPs.",
    },
    {
        "name": "Green Mountain Power",
        "url": "https://greenmountainpower.com/regulatory/",
        "js_render": False,
        "active": True,
        "notes": "GMP posts RFPs under regulatory filings page.",
    },
    {
        "name": "National Grid (NY/NE)",
        "url": "https://www.nationalgridus.com/Our-Company/Doing-Business-with-National-Grid/Procurement-Portal",
        "js_render": True,
        "active": True,
        "notes": "JS-rendered portal. Phase 2 upgrade needed for full scraping.",
    },
    {
        "name": "Avangrid / United Illuminating (CT)",
        "url": "https://www.avangrid.com/our-company/procurement",
        "js_render": True,
        "active": True,
        "notes": "CT utility. JS-rendered. Phase 2.",
    },
    {
        "name": "VEIC & Efficiency Vermont",
        "url": "https://www.veic.org/organization/rfps",
        "state": "VT",
        "type": "veic_rfps",
        "js_render": False,
        "active": True,
        "notes": (
            "VEIC RFP page, including Efficiency Vermont RFPs"
            "Efficiency Vermont's own RFP page points users to VEIC for current opportunities."
        ),
    },
    {
        "name": "Energy Trust of Oregon Contracting Opportunities",
        "state": "OR",
        "url": "https://www.energytrust.org/about/work-with-us/how-to-work-with-energy-trust/contracting-opportunities/",
        "type": "energy_trust_rfps",
        "notes": (
            "Energy Trust of Oregon contracting opportunities page. "
            "Uses a dedicated parser for module-rfps__rfp opportunity blocks."
        ),
    },
    {
        "name": "PG&E Energy Efficiency Solicitations",
        "state": "CA",
        "url": "https://www.pge.com/en/about/doing-business-with-pge/solicitations.html",
        "type": "pge_ee_solicitations",
        "notes": (
            "PG&E energy efficiency third-party solicitations page. "
            "Uses a dedicated parser for the AEM data-table titled "
            "'Active and upcoming solicitations'."
        ),
    },
    # --- Mid-Atlantic / broader regional utilities ---
    {
        "name": "PJM Interconnection Solicitations",
        "url": "https://www.pjm.com/about-pjm/who-we-are/vendor-information/solicitations",
        "js_render": False,
        "active": False,
        "notes": "Disabled June 2026. Previous configured solicitations URL is broken and PJM's search-results page is JavaScript/browser-rendered rather than a stable scrapeable RFP listing.",
    },
    {
        "name": "NYISO Procurement",
        "url": "https://www.nyiso.com/vendor-registration",
        "js_render": False,
        "active": True,
        "notes": "NY ISO. Evaluation and market studies.",
    },
    {
        "name": "AESP Active RFPs",
        "url": "https://aesp.org/community/news-and-rpfs/",
        "state": "",
        "type": "aesp_rfps",
        "js_render": False,
        "active": True,
        "notes": (
            "AESP member news and active RFP/RFQ/RFI page. Dedicated parser should "
            "only capture the Active RFPs, RFQs, and RFIs section and stop before "
            "Members news."
        ),
    },
    {
        "name": "Efficiency Maine",
        "url": "https://www.efficiencymaine.com/opportunities/",
        "state": "ME",
        "type": "efficiency_maine_rfps",
        "js_render": False,
        "active": True,
        "notes": "Efficiency Maine opportunity/RFP page. Uses dedicated parser to extract detail-page deadlines.",
    },
    {
        "name": "Burlington Electric Department RFPs",
        "url": "https://www.burlingtonelectric.com/rfp/",
        "state": "VT",
        "type": "burlington_electric_rfps",
        "js_render": False,
        "active": True,
        "notes": "Burlington Electric Department public RFP page. Uses dedicated parser to avoid navigation links.",
    },
    {
        "name": "Mass Save / EEAC",
        "url": "https://www.masssave.com/trade-partners/requests-for-proposals",
        "js_render": False,
        "active": True,
        "notes": "Updated URL -- MA energy efficiency program administrator.",
    },
    {
        "name": "Cape Light Compact RFPs",
        "state": "MA",
        "url": "https://www.capelightcompact.org/news-and-resources/request-for-proposals-rfp/",
        "type": "cape_light_rfps",
        "notes": (
            "Cape Light Compact RFP/RFI page. Uses a dedicated parser that reads "
            "current listings before the Archive section and skips Q&A/addenda/support documents."
        ),
    },
    {
        "name": "Entergy RFPs",
        "state": "Multi",
        "url": "https://rfp.entergy.com/",
        "type": "entergy_rfps",
        "notes": (
            "Entergy System Planning and Operations RFP page. "
            "Uses a dedicated parser for the static RFP link list and keeps only "
            "current/recent RFPs to avoid historical procurement clutter."
        ),
    },
    # --- National / DOE ecosystem ---
    {
        "name": "DOE EERE Funding Opportunities",
        "url": "https://www.energy.gov/eere/funding-opportunities",
        "js_render": False,
        "active": True,
        "notes": "DOE Office of Energy Efficiency. Broad national scope.",
    },
    {
        "name": "EPA ENERGY STAR Solicitations",
        "url": "https://www.energystar.gov/about/our_work/contracts_rfps",
        "js_render": False,
        "active": False,
        "notes": "404 -- no confirmed replacement URL found. Disabled for now.",
    },
]

# ---------------------------------------------------------------------------
# STATE PROCUREMENT COVERAGE STRATEGY
#
# Layer 1 -- SAM.gov: Covers federal opportunities in any state.
# Layer 2 -- Google CSE: Disabled until Google Cloud billing is configured.
# Layer 3 -- Direct scrape of highest-priority state portals below.
# ---------------------------------------------------------------------------

STATE_PORTAL_DOMAINS_FOR_CSE = [
    "vsigns.vermont.gov",
    "commbuys.com",
    "biznet.ct.gov",
    "nyscr.ny.gov",
    "maine.gov",
    "das.nh.gov",
    "caleprocure.ca.gov",
    "energy.ca.gov",
    "procurement.maryland.gov",
    "eva.virginia.gov",
    "ipp.illinois.gov",
    "procurement.ohio.gov",
    "purchasing.texas.gov",
    "purchasing.colorado.gov",
    "naseo.org",
    "nyserda.ny.gov",
]

DIRECT_SCRAPE_STATES = [
    {
        "name": "Vermont VSIGNS",
        "state": "VT",
        "url": "https://vsigns.vermont.gov/bso/external/publicBids.sdo?tabcode=PUBLIC_RFPS",
        "type": "vsigns",
        "notes": "CxA home state. Most important direct scrape.",
    },
    {
        "name": "Massachusetts COMMBUYS",
        "state": "MA",
        "url": "https://www.commbuys.com/bso/view/search/external/advancedSearchBid.xhtml?openBids=true",
        "type": "commbuys",
        "notes": "Massachusetts public open-bid search. Uses a dedicated parser for bidDetail.sda links exposed in the COMMBUYS search results HTML.",
    },
    {
        "name": "NYSERDA Funding (direct)",
        "state": "NY",
        "url": "https://www.nyserda.ny.gov/Funding-Opportunities/Requests-for-Proposals",
        "type": "generic_list",
        "notes": "Also covered in UTILITY_SOURCES.",
    },
    {
        "name": "California CaleProcure",
        "state": "CA",
        "url": "https://caleprocure.ca.gov/pages/public-search.aspx",
        "type": "ca_eprocure",
        "notes": "CEC issues large EM&V and program evaluation RFPs.",
    },
    {
        "name": "NYS Contract Reporter",
        "url": "https://www.nyscr.ny.gov/Ads/Search",
        "state": "NY",
        "type": "nyscr_contract_reporter",
        "js_render": False,
        "active": True,
        "notes": "New York State Contract Reporter open opportunities page. Uses dedicated text-block parser because detail links require login.",
    },
    {
        "name": "Connecticut DEEP RFP Search",
        "url": "https://portal.ct.gov/deep/search-results?SearchKeyword=RFP",
        "state": "CT",
        "type": "ct_deep_rfp_search",
        "js_render": False,
        "active": True,
        "notes": "CT DEEP search results filtered for current RFP/proposal-related pages.",
    },
    {
        "name": "Vermont DPS Requests for Proposals",
        "url": "https://publicservice.vermont.gov/document-categories/requests-proposals",
        "state": "VT",
        "type": "vermont_dps_rfps",
        "notes": "Fallback Vermont DPS RFP page. Added because VSIGNS fails DNS resolution from GitHub/local environments.",
    },
    {
        "name": "Vermont Business Registry Bid Search",
        "url": "https://www.vermontbusinessregistry.com/bidsearch.aspx?type=1",
        "state": "VT",
        "type": "vermont_business_registry",
        "notes":  "Broader Vermont statewide bid search fallback."
    },
    {
        "name": "NH Department of Energy RFPs",
        "state": "NH",
        "url": "https://www.energy.nh.gov/rules-and-regulatory/requests-proposals",
        "type": "nh_energy_rfps",
        "notes": (
            "NH Department of Energy/Public Utilities Commission RFP page. "
            "Uses a dedicated parser because each RFP heading includes support "
            "documents such as Q&A, proposals received, and rankings."
        ),
    },
    {
        "name": "Connecticut Energy Efficiency Board RFPs",
        "state": "CT",
        "url": "https://www.energizect.com/connecticut-energy-efficiency-board/rfps",
        "type": "ct_eeb_rfps",
        "notes": (
            "Connecticut Energy Efficiency Board RFP/RFQ page. "
            "Uses a dedicated parser to read only the Open RFPs/RFQs section "
            "and avoid supporting documents."
        ),
    },
]

GOOGLE_CSE_QUERIES = [
    "evaluation measurement verification RFP",
    "program evaluation energy efficiency RFP",
    "EM&V services request for proposals",
    "impact evaluation energy RFP",
    "M&V measurement verification solicitation",
    "energy savings verification RFP",
    "baseline study energy efficiency RFP",
    "DSM evaluation request for proposals",
]

# ---------------------------------------------------------------------------
# SEEN OPPORTUNITIES STATE FILE (now stored in Supabase, not a file)
# ---------------------------------------------------------------------------

STATE_FILE_PATH    = "state/seen_opportunities.json"
STATE_EXPIRY_DAYS  = 180

# ---------------------------------------------------------------------------
# EMAIL DELIVERY (SendGrid)
# ---------------------------------------------------------------------------

SENDGRID_API_KEY_ENV = "SENDGRID_API_KEY"
EMAIL_FROM           = "eric@cx-assoc.com"
EMAIL_TO = [
    "riazul.hoque@cx-assoc.com",
    "eric@cx-assoc.com",
    "carrie.napolitan@cx-assoc.com",
    "liza.boyle@cx-assoc.com",
    "rachael@cx-assoc.com",
    "matt@cx-assoc.com"
]
EMAIL_SUBJECT_PREFIX = "[CxA RFP Monitor]"

# ---------------------------------------------------------------------------
# GITHUB PAGES DASHBOARD
# ---------------------------------------------------------------------------

DASHBOARD_OUTPUT_PATH = "docs/index.html"
DASHBOARD_MAX_DISPLAY = 150

# ---------------------------------------------------------------------------
# HTTP REQUEST SETTINGS
# ---------------------------------------------------------------------------

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "identity",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

REQUEST_TIMEOUT       = 20
REQUEST_DELAY_SECONDS = 2
REQUEST_MAX_RETRIES   = 3
