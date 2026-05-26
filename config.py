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
    that source is skipped with a warning; the run continues.
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
    "evaluation measurement and verification",
    "EM&V",
    "M&V",
    "IPMVP",
    "measurement and verification",
    "energy savings verification",
    "energy efficiency evaluation",
    # Common RFP title patterns for evaluation work
    "EM&V services",
    "M&V services",
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
    "free ridership",
    "baseline study",
    "deemed savings",
    "custom measure evaluation",
    "portfolio evaluation",
    "energy efficiency program evaluation",
    "utility program evaluation",
    "DSM evaluation",
    "demand-side management evaluation",
    "load impact study",
    "non-energy impacts",
    # EM&V-adjacent research terms
    "energy savings study",
    "energy audit verification",
]

KEYWORDS_TERTIARY = [
    # Broader energy efficiency terms -- active in "broad" mode only
    "energy efficiency",
    "demand response evaluation",
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
    "building performance",
    "decarbonization study",
    "greenhouse gas evaluation",
]

# ---------------------------------------------------------------------------
# RELEVANCE SCORING WEIGHTS AND THRESHOLDS
#
# Tuning guide:
#   Getting too many unrelated results? Raise MIN_SCORE_INCLUDE.
#   Missing real EM&V RFPs?           Lower MIN_SCORE_INCLUDE or promote
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
# 541711 = Research and Development in Biotechnology (sometimes used for energy R&D)
# 541712 = R&D in the Physical, Engineering, and Life Sciences (covers energy research)
SAM_NAICS_CODES = ["541690", "541620", "541330", "541712"]

# SAM.gov keyword queries -- short phrases that work with title-only search.
# More queries = better coverage but more API calls. Keep under ~15 to stay
# within the 1,000/day rate limit even on a system with multiple runs.
SAM_SEARCH_QUERIES = [
    "evaluation measurement verification",
    "M&V measurement verification",
    "program evaluation energy",
    "impact evaluation energy efficiency",
    "IPMVP",
    "baseline study energy",
    "energy savings verification",
    "DSM evaluation",
    "demand side management evaluation",
    "energy efficiency evaluation services",
    "utility program evaluation",
]

# ---------------------------------------------------------------------------
# UTILITY AND QUASI-PUBLIC SOURCES
# High-probability issuers of EM&V work. These are scraped directly.
#
# js_render: True  = page requires JavaScript to load (skipped in basic mode;
#                    marked for Phase 2 Playwright upgrade)
# active:    False = temporarily disable a source without deleting it
#
# KNOWN FAILURE POINTS:
#   - Page structure changes break scrapers silently (0 results instead of error).
#     If a normally-productive source drops to 0, inspect the page manually.
#   - Some sites 403 bot-looking requests. The User-Agent in REQUEST_HEADERS
#     is set to a legitimate-looking string, but some WAFs still block.
# ---------------------------------------------------------------------------

UTILITY_SOURCES = [
    # --- National / multi-state quasi-publics ---
    {
        "name": "NASEO RFP Board",
        "url": "https://www.naseo.org/rfps",
        "js_render": False,
        "active": True,
        "notes": "National Association of State Energy Officials. Broad coverage.",
    },
    {
        "name": "NEEP (Northeast Energy Efficiency Partnerships)",
        "url": "https://neep.org/about-neep/work-neep",
        "js_render": False,
        "active": True,
        "notes": "Regional evaluation coordination. Issues EM&V RFPs directly.",
    },
    {
        "name": "ACEEE",
        "url": "https://www.aceee.org/about/work-aceee",
        "js_render": False,
        "active": True,
        "notes": "American Council for an Energy-Efficient Economy. National scope.",
    },
    {
        "name": "E4TheFuture",
        "url": "https://e4thefuture.org/",
        "js_render": False,
        "active": True,
        "notes": "Northeast nonprofit. Aggregates and issues NE energy efficiency RFPs.",
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
        "url": "https://www.iso-ne.com/participate/solicitations-rfps/",
        "js_render": False,
        "active": True,
        "notes": "Regional ISO. Load research, market studies.",
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
        "url": "https://greenmountainpower.com/about/procurement/",
        "js_render": False,
        "active": True,
        "notes": "Vermont primary utility. Most relevant for CxA geography.",
    },
    {
        "name": "National Grid (NY/NE)",
        "url": "https://www.nationalgridus.com/Our-Company/Doing-Business-with-National-Grid/Procurement-Portal",
        "js_render": True,    # Requires Playwright -- skipped in basic mode
        "active": True,
        "notes": "JS-rendered portal. Phase 2 upgrade needed for full scraping.",
    },
    {
        "name": "Avangrid / United Illuminating (CT)",
        "url": "https://www.avangrid.com/our-company/procurement",
        "js_render": True,    # Requires Playwright
        "active": True,
        "notes": "CT utility. JS-rendered. Phase 2.",
    },
    # --- Mid-Atlantic / broader regional utilities ---
    {
        "name": "PJM Interconnection Solicitations",
        "url": "https://www.pjm.com/about-pjm/who-we-are/vendor-information/solicitations",
        "js_render": False,
        "active": True,
        "notes": "Mid-Atlantic ISO. Market and load studies.",
    },
    {
        "name": "NYISO Procurement",
        "url": "https://www.nyiso.com/vendor-registration",
        "js_render": False,
        "active": True,
        "notes": "NY ISO. Evaluation and market studies.",
    },
    {
        "name": "Efficiency Vermont",
        "url": "https://www.efficiencyvermont.com/about/partners-vendors/rfp",
        "js_render": False,
        "active": True,
        "notes": "Vermont's efficiency utility. Direct EM&V issuer.",
    },
    {
        "name": "Efficiency Maine",
        "url": "https://www.efficiencymaine.com/about/requests-for-proposals/",
        "js_render": False,
        "active": True,
        "notes": "Maine's efficiency program administrator.",
    },
    {
        "name": "Mass Save / EEAC",
        "url": "https://www.masssave.com/saving/business-programs",
        "js_render": False,
        "active": True,
        "notes": "MA energy efficiency program administrator coalition.",
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
        "url": "https://www.energystar.gov/about/EPA_RFPs",
        "js_render": False,
        "active": True,
        "notes": "EPA program evaluation and market studies.",
    },
]

# ---------------------------------------------------------------------------
# STATE PROCUREMENT COVERAGE STRATEGY
#
# Covering all 50 states with individual scrapers is impractical -- state
# portal HTML structures are too diverse and change too often to maintain.
#
# Instead, we use a TWO-LAYER approach:
#
#   Layer 1 -- SAM.gov: Federal opportunities already cover work issued by
#              federal agencies in any state. This is handled above.
#
#   Layer 2 -- Google Custom Search (CSE): We query Google targeting the
#              ~20 highest-value state procurement portals by URL pattern.
#              Google has already indexed these pages; we get keyword-matched
#              results without scraping each portal directly.
#
#   Layer 3 -- Direct scrape of highest-priority state portals:
#              Vermont (most relevant for CxA) and a small set of other
#              states that are high-probability EM&V issuers get direct
#              scraping. These are maintained individually.
#
# Google CSE SETUP (one-time):
#   1. Go to https://programmablesearchengine.google.com/
#   2. Create a new search engine
#   3. Add the state portal domains below under "Sites to search"
#   4. Get your Search Engine ID (cx parameter)
#   5. Enable the Custom Search JSON API in Google Cloud Console
#   6. Get an API key (100 free queries/day; 10,000/day with billing)
#   7. Set GOOGLE_CSE_KEY and GOOGLE_CSE_ID in GitHub Secrets
#
# KNOWN FAILURE POINT: Google CSE free tier is 100 queries/day. We issue
# one query per keyword phrase across all sites, so this is sufficient for
# weekly runs but monitor usage if you expand the keyword list significantly.
# ---------------------------------------------------------------------------

# State procurement portal domains to include in Google CSE
# These are added in the CSE configuration (not scraped directly)
STATE_PORTAL_DOMAINS_FOR_CSE = [
    # New England (highest priority for CxA)
    "vsigns.vermont.gov",
    "commbuys.com",
    "biznet.ct.gov",
    "nyscr.ny.gov",
    "maine.gov",
    "das.nh.gov",
    "purchasing.ri.gov",
    # Mid-Atlantic
    "epiq.dgs.pa.gov",         # Pennsylvania
    "ebid.net",                 # New Jersey
    "procurement.maryland.gov", # Maryland
    "eva.virginia.gov",         # Virginia
    "vendor.emarket.pa.gov",    # PA alternate
    # Southeast
    "mfmp.myflorida.com",      # Florida
    "team.georgia.gov",         # Georgia
    "iphub.nc.gov",            # North Carolina
    # Midwest
    "ipp.illinois.gov",         # Illinois
    "vendornet.state.wi.us",    # Wisconsin
    "mn.gov/admin/osp",         # Minnesota
    "procurement.ohio.gov",     # Ohio
    "bidding.michigan.gov",     # Michigan
    # West
    "caleprocure.ca.gov",       # California
    "orpin.oregon.gov",         # Oregon
    "ga.wa.gov",                # Washington state
    "purchasing.colorado.gov",  # Colorado
    # Others worth indexing
    "des.az.gov/procure",       # Arizona
    "purchasing.texas.gov",     # Texas
]

# States to scrape DIRECTLY (highest priority, maintained individually)
# Vermont is #1 given CxA HQ. The others are top EM&V markets.
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
        "url": "https://www.commbuys.com/bso/external/publicBids.sdo",
        "type": "commbuys",
        "notes": "Large EM&V market. DOER and utilities issue frequently here.",
    },
    {
        "name": "NYSERDA Funding (direct)",
        "state": "NY",
        "url": "https://www.nyserda.ny.gov/Funding-Opportunities/Requests-for-Proposals",
        "type": "generic_list",
        "notes": "Also covered in UTILITY_SOURCES, but direct state portal version here.",
    },
    {
        "name": "California CaleProcure",
        "state": "CA",
        "url": "https://caleprocure.ca.gov/pages/public-search.aspx",
        "type": "ca_eprocure",
        "notes": "CEC issues large EM&V and program evaluation RFPs. High value.",
    },
]

# Google CSE keyword queries (will be cross-queried against state portal domains)
# Keep this list short -- each query = 1 API call against the 100/day limit
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
# SEEN OPPORTUNITIES STATE FILE
# Tracks reported notice IDs to prevent duplicates across runs.
#
# KNOWN FAILURE POINT: GitHub Actions must have write permissions to commit
# this file back to the repo after each run. Set under:
# Settings > Actions > General > Workflow permissions > Read and write.
# If the commit fails, duplicates will appear on the next run.
# ---------------------------------------------------------------------------

STATE_FILE_PATH    = "state/seen_opportunities.json"
STATE_EXPIRY_DAYS  = 180   # Expire entries after 6 months (prevents file bloat)

# ---------------------------------------------------------------------------
# EMAIL DELIVERY (SendGrid)
# Free tier: 100 emails/day. We send at most 1 per run. Sufficient.
#
# KNOWN FAILURE POINT: SendGrid requires domain authentication (SPF/DKIM)
# for reliable inbox delivery. Complete domain auth in the SendGrid dashboard
# if emails land in spam.
# ---------------------------------------------------------------------------

SENDGRID_API_KEY_ENV = "SENDGRID_API_KEY"         # GitHub Secret name
EMAIL_FROM           = "rfp-monitor@cx-associates.com"
EMAIL_TO = [
    "eric@cx-associates.com",
    # Add others as needed, e.g. "matt@cx-associates.com"
]
EMAIL_SUBJECT_PREFIX = "[CxA RFP Monitor]"

# ---------------------------------------------------------------------------
# GITHUB PAGES DASHBOARD
# Written to docs/index.html by the workflow on every run (even with no results,
# to keep the "last updated" timestamp fresh).
#
# KNOWN FAILURE POINT: Repo must have GitHub Pages enabled:
# Settings > Pages > Source = Deploy from branch > Branch = main > Folder = /docs
# ---------------------------------------------------------------------------

DASHBOARD_OUTPUT_PATH = "docs/index.html"
DASHBOARD_MAX_DISPLAY = 150   # Max rows shown in the dashboard table

# ---------------------------------------------------------------------------
# HTTP REQUEST SETTINGS
# ---------------------------------------------------------------------------

REQUEST_HEADERS = {
    "User-Agent": (
        "CxA-RFP-Monitor/1.0 "
        "(Cx Associates commissioning firm; contact eric@cx-associates.com)"
    )
}
REQUEST_TIMEOUT       = 20   # Seconds per HTTP request
REQUEST_DELAY_SECONDS = 2    # Seconds between requests to the same domain
REQUEST_MAX_RETRIES   = 3    # Retries on transient errors (429, 502, 503)
