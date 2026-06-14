"""
scorer.py -- Mode-Aware Relevance Scoring for the CxA RFP Monitor
==================================================================
Scores Opportunity objects against the keyword lists in config.py,
respecting the current KEYWORD_MODE ("broad" or "medium").

BROAD mode:  All three tiers (primary + secondary + tertiary) are active.
             Lower score threshold (MIN_SCORE_INCLUDE_BROAD).
             Wider net -- expect more results, some false positives.

MEDIUM mode: Only primary and secondary tiers are active.
             Tertiary keywords (generic EE terms) are ignored.
             Higher threshold (MIN_SCORE_INCLUDE_MEDIUM).
             Tighter focus -- fewer results, higher EM&V relevance.

The mode is read from config.KEYWORD_MODE at import time but can be
overridden by passing mode="medium" to filter_and_sort().

KNOWN FAILURE POINTS:
  - Score thresholds were calibrated against a sample of known EM&V RFPs.
    If you find real EM&V opportunities being dropped (score too low),
    lower MIN_SCORE_INCLUDE_BROAD or promote relevant terms to a higher tier.
  - The title bonus (+5) is intentionally generous because title matches are
    the strongest signal on platforms where full text isn't searchable.
"""

import logging
import re
from typing import List, Optional, Tuple

import config
from models import Opportunity

logger = logging.getLogger(__name__)


def _normalize_for_matching(text: str) -> str:
    """
    Lowercase and normalize text for keyword matching.

    Converts ampersands and hyphens to spaces so that "M&V", "M & V",
    and "M-V" all match the keyword "M V" after normalization.

    KNOWN FAILURE POINT: The ampersand-to-space conversion is intentional
    but could cause short abbreviation collisions if you later add very
    short keywords (< 3 characters). Keep keywords at 3+ meaningful chars.
    """
    text = text.lower()
    text = re.sub(r"[&\-]", " ", text)       # Normalize & and - to spaces
    text = re.sub(r"\s+", " ", text).strip()  # Collapse whitespace
    return text


def _keyword_in_text(keyword: str, text_normalized: str) -> bool:
    """
    Check whether a single keyword appears in a pre-normalized text string.

    Short abbreviations (normalized length <= 4 chars) use word-boundary
    matching to avoid substring false positives (e.g., "MV" inside "COMMBUYS").
    Longer phrases use substring matching (the phrase itself is specific enough).

    KNOWN FAILURE POINT: Word boundary (\b) in Python regex treats digits as
    word characters, so "M V" won't match at the start/end of digit strings.
    This is acceptable behavior -- we don't expect keyword matches in the
    middle of numeric codes.
    """
    kw_norm = _normalize_for_matching(keyword)

    if len(kw_norm.replace(" ", "")) <= 4:
        # Short term: require word boundaries to avoid false positives
        pattern = r"\b" + re.escape(kw_norm) + r"\b"
        return bool(re.search(pattern, text_normalized))

    # Longer phrase: substring match is reliable enough
    return kw_norm in text_normalized


def score_opportunity(
    opportunity: Opportunity,
    mode: str = None,
    monitor_type: str = None,
) -> Opportunity:
    """
    Evaluate one Opportunity against the keyword lists and assign:
      - relevance_score (integer, sum of all keyword match points)
      - matched_keywords (list of keyword strings that fired)
      - confidence ("High", "Medium", "Low", or "Below threshold")

    Modifies the opportunity in place and returns it for chaining.

    Args:
        opportunity: The Opportunity to score (modified in place)
        mode:        "broad" or "medium". Defaults to config.KEYWORD_MODE.

    Scoring summary (per keyword match):
      Primary in title:       10 (base) + 5 (title bonus) = 15
      Primary in body:        10
      Secondary in title:     5 + 5 = 10
      Secondary in body:      5
      Tertiary in title:      2 + 5 = 7   [broad mode only]
      Tertiary in body:       2            [broad mode only]
    """
    if mode is None:
        mode = config.KEYWORD_MODE
    monitor_type = config.normalize_monitor_type(monitor_type)
    keyword_tiers = config.get_keyword_tiers(monitor_type)

    # Pre-normalize title and description once (used repeatedly below)
    title_norm = _normalize_for_matching(opportunity.title or "")
    desc_norm  = _normalize_for_matching(opportunity.description or "")

    score   = 0
    matched = []

    def check_tier(keywords: List[str], base_pts: int):
        """
        Inner helper: check every keyword in a tier against title and body.
        Accumulates score and matched keywords list.
        """
        nonlocal score
        for kw in keywords:
            in_title = _keyword_in_text(kw, title_norm)
            in_desc  = _keyword_in_text(kw, desc_norm)

            if in_title:
                pts = base_pts + config.SCORE_TITLE_BONUS
                score += pts
                if kw not in matched:
                    matched.append(kw)
                logger.debug(f"  TITLE +{pts}pts: '{kw}'")
            elif in_desc:
                score += base_pts
                if kw not in matched:
                    matched.append(kw)
                logger.debug(f"  DESC  +{base_pts}pts: '{kw}'")

    # Always check primary and secondary tiers
    check_tier(keyword_tiers["primary"],   config.SCORE_PRIMARY_MATCH)
    check_tier(keyword_tiers["secondary"], config.SCORE_SECONDARY_MATCH)

    # Tertiary tier: broad mode only
    if mode == "broad":
        check_tier(keyword_tiers["tertiary"], config.SCORE_TERTIARY_MATCH)

    # Assign confidence label
    if score >= config.MIN_SCORE_HIGH_CONFIDENCE:
        confidence = "High"
    elif score >= _min_score_for_mode(mode, monitor_type=monitor_type):
        confidence = "Medium"
    elif score > 0:
        confidence = "Low"
    else:
        confidence = "Below threshold"

    opportunity.relevance_score  = score
    opportunity.matched_keywords = matched
    opportunity.confidence       = confidence

    logger.debug(
        f"Scored [{confidence}|{score}pts]: {opportunity.title[:60]!r}"
    )
    return opportunity


def _min_score_for_mode(mode: str, monitor_type: str = None) -> int:
    """
    Return the minimum inclusion score for the given mode.

    Kept as a function (rather than inline) so callers can check the
    threshold without duplicating the config lookup logic.
    """
    return config.get_min_score_for_mode(mode, monitor_type=monitor_type)

def score_split_and_sort(
    opportunities: List[Opportunity],
    mode: Optional[str] = None,
    monitor_type: Optional[str] = None,
) -> Tuple[List[Opportunity], List[Opportunity], List[Opportunity]]:
    """
    Score all opportunities and split them into:
      - passing: opportunities at or above the mode threshold
      - manual_review: below-threshold opportunities for optional human review
      - all_scored: all scored opportunities

    This supports the dashboard's main table plus a collapsible manual-review
    section without changing the email digest behavior.
    """
    if mode is None:
        mode = config.KEYWORD_MODE
    monitor_type = config.normalize_monitor_type(monitor_type)

    min_score = _min_score_for_mode(mode, monitor_type=monitor_type)

    logger.info(
        f"Scoring {len(opportunities)} raw opportunities "
        f"(monitor_type={monitor_type}, mode={mode}, threshold={min_score})"
    )

    all_scored = [
        score_opportunity(opp, mode=mode, monitor_type=monitor_type)
        for opp in opportunities
    ]

    passing = [opp for opp in all_scored if opp.relevance_score >= min_score]
    manual_review = [opp for opp in all_scored if opp.relevance_score < min_score]

    def sort_key(opp: Opportunity) -> Tuple:
        deadline_sort = opp.deadline or "9999-12-31"
        return (-opp.relevance_score, deadline_sort, opp.source, opp.title)

    passing.sort(key=sort_key)
    manual_review.sort(key=sort_key)
    all_scored.sort(key=sort_key)

    high_count = sum(1 for o in passing if o.confidence == "High")
    medium_count = sum(1 for o in passing if o.confidence == "Medium")
    low_count = sum(1 for o in passing if o.confidence == "Low")

    logger.info(
        f"Scoring complete: {len(passing)}/{len(opportunities)} passed "
        f"(High={high_count}, Medium={medium_count}, Low={low_count}); "
        f"{len(manual_review)} below threshold for manual review"
    )

    for opp in passing[:15]:
        logger.info(
            f"  [{opp.confidence:6s}|{opp.relevance_score:3d}pts] "
            f"{opp.source:<25s} {opp.title[:65]}"
        )

    return passing, manual_review, all_scored

def filter_manual_review_candidates(
    opportunities: List[Opportunity],
) -> List[Opportunity]:
    """
    Keep only below-threshold items that are still plausibly procurement-related.

    This does not affect scoring, the main dashboard, or the email digest.
    It only prevents obvious navigation/footer/support links from cluttering
    the manual-review section.
    """
    exclude_title_terms = [
        "skip to content",
        "back to top",
        "contact",
        "search",
        "menu",
        "home",
        "about",
        "careers",
        "privacy",
        "sitemap",
        "subscribe",
        "grants.gov",
        "program information center",
        "committees and groups",
        "key study areas",
        "planning advisory committee",
        "transmission planning guides",
        "solar power impact",
        "our impact",
        "see all",
    ]

    include_terms = [
        "rfp",
        "rfq",
        "rfi",
        "request for proposal",
        "request for proposals",
        "request for qualifications",
        "request for quotation",
        "request for information",
        "solicitation",
        "bid",
        "procurement",
        "proposal",
        "evaluation",
        "measurement",
        "verification",
        "em&v",
        "m&v",
        "impact evaluation",
        "program evaluation",
    ]

    filtered = []
    seen = set()

    for opp in opportunities:
        title_l = (opp.title or "").lower()
        desc_l = (opp.description or "").lower()
        url_l = (opp.url or "").lower()
        combined_l = f"{title_l} {desc_l} {url_l}"

        if not opp.url or opp.url.startswith("mailto:"):
            continue

        if "/cdn-cgi/l/email-protection" in url_l:
            continue

        if any(term in title_l for term in exclude_title_terms):
            continue

        if not any(term in combined_l for term in include_terms):
            continue

        key = opp.unique_key()
        if key in seen:
            continue
        seen.add(key)

        filtered.append(opp)

    return filtered

def filter_and_sort(
    opportunities: List[Opportunity],
    mode: Optional[str] = None,
    monitor_type: Optional[str] = None,
) -> List[Opportunity]:
    """
    Score, filter, and sort opportunities using the existing scoring logic.

    Backward-compatible wrapper around score_split_and_sort().
    Returns only opportunities that meet the inclusion threshold, which is the
    same behavior this function had before.

    The scoring criteria are unchanged:
      - same keyword lists
      - same primary/secondary/tertiary weights
      - same title bonus
      - same broad/medium thresholds
      - same confidence labeling from score_opportunity()
    """
    passing, _manual_review, _all_scored = score_split_and_sort(
        opportunities,
        mode=mode,
        monitor_type=monitor_type,
    )
    return passing