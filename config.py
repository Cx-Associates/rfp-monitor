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
#   Getting too many unrelated results? Raise MIN_SCORE_INCLUDE_BROAD.
#   Missing real EM&V RFPs?           Lower MIN_SCORE_INCLUDE_BROAD or promote
#                                     a keyword from tertiary to secondary.
# ---------------------------------------------------------------------------

SCORE_PRIMARY_MATCH   = 10   # Primary keyword found in title or description
SCORE_SECONDARY_MATCH = 5    # Secondary keyword match
SCORE_TERTIARY_MATCH  = 2    # Tertiary keyword match (broad mode only)
SCORE_TITLE_BONUS
