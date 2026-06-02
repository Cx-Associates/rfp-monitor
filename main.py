"""
main.py -- Orchestrator for the CxA RFP Monitor
================================================
Entry point for the full monitoring cycle. Called by GitHub Actions weekly
or manually from the command line.

Run sequence:
  1. Parse arguments (mode, dry-run, sources)
  2. Load deduplication state
  3. Run configured scrapers
  4. Score and filter all raw opportunities
  5. Deduplicate against seen-set
  6. Deliver: email digest + GitHub Pages dashboard
  7. Mark delivered opportunities as seen; save state

CLI flags:
  --mode [broad|medium]  Override config.KEYWORD_MODE for this run.
                         Default: value from config.py (currently "broad").
  --dry-run              Run all scrapers and scoring; skip delivery and
                         state update. Use for testing and calibration.
  --force-all            Ignore seen-set; report all passing opportunities.
                         Use carefully -- creates duplicate email entries.
  --sources [...]        Comma-separated: sam, utilities, states_direct,
                       google_cse (disabled), all. Default: all.
  --debug                Enable DEBUG logging (very verbose).

Exit codes:
  0 -- Normal completion (including "no new opportunities" -- that's not an error)
  1 -- All scrapers returned 0 results AND state save failed (systemic failure)

KNOWN FAILURE POINTS:
  - Script assumes it runs from the repo root. GitHub Actions uses the repo
    root as working-directory by default.
  - If a scraper hangs on a single HTTP request longer than REQUEST_TIMEOUT,
    the requests library will eventually raise a Timeout exception. The
    scraper catches it and moves on. The GitHub Actions 30-minute job
    timeout is the outer safety net for any unexpected hangs.
  - The script exits 0 even when some sources fail (partial success is
    acceptable -- one broken state portal shouldn't block the whole run).
"""

import argparse
import logging
import sys
from datetime import datetime
from typing import List

# ---------------------------------------------------------------------------
# Logging -- structured output that GitHub Actions renders clearly
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-7s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("rfp_monitor")


def parse_args() -> argparse.Namespace:
    """
    Parse and return command-line arguments.

    All flags have safe defaults so the script runs correctly in the
    GitHub Actions environment with no arguments.
    """
    p = argparse.ArgumentParser(description="CxA EM&V RFP Monitor")

    p.add_argument(
        "--mode",
        choices=["broad", "medium"],
        default=None,
        help=(
            "Keyword sensitivity mode. "
            "'broad' = all three keyword tiers active (wider net, more results). "
            "'medium' = primary + secondary only (tighter, fewer results). "
            "Defaults to the value of KEYWORD_MODE in config.py."
        ),
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Score and log but skip delivery and state update.",
    )
    p.add_argument(
        "--force-all",
        action="store_true",
        help="Ignore seen-set and report all passing opportunities.",
    )
    p.add_argument(
        "--sources",
        default="all",
        help=(
            "Comma-separated list of sources: "
            "sam, utilities, states_direct, google_cse (disabled), all"
        ),
    )
    p.add_argument(
        "--debug",
        action="store_true",
        help="Enable DEBUG-level logging.",
    )
    return p.parse_args()


def run_scrapers(source_str: str) -> List:
    """
    Run the configured scrapers and return all raw Opportunity objects.

    Each scraper is wrapped in try/except so a single failure doesn't
    abort the run. Missing API keys cause graceful skips (logged as warnings).

    Args:
        source_str: Comma-separated source names or "all"

    Returns:
        Combined list of raw (unscored) Opportunity objects
    """
    sources  = [s.strip().lower() for s in source_str.split(",")]
    run_all  = "all" in sources
    raw_opps = []

    def run_source(label: str, key: str, fetch_fn):
        """
        Helper: run one fetch function and extend raw_opps.
        Catches all exceptions so one broken source can't stop the run.
        """
        if not (run_all or key in sources):
            return

        logger.info(f"{'='*55}")
        logger.info(f"SOURCE: {label}")
        logger.info(f"{'='*55}")
        try:
            results = fetch_fn()
            logger.info(f"{label}: {len(results)} raw opportunities returned")
            raw_opps.extend(results)
        except Exception as e:
            logger.error(
                f"{label}: unhandled exception ({type(e).__name__}: {e})",
                exc_info=True,
            )

    # SAM.gov (federal, API-based -- most reliable source)
    from scrapers.sam_gov import fetch_sam_opportunities
    run_source("SAM.gov (Federal)", "sam", fetch_sam_opportunities)

    # Utility and quasi-public sources (NYSERDA, NEEP, NASEO, Eversource, etc.)
    from scrapers.web_sources import fetch_utility_sources
    run_source("Utility / Quasi-Public Sources", "utilities", fetch_utility_sources)

    # Priority state portals (VT, MA, NY, CA -- direct scrape)
    from scrapers.web_sources import fetch_direct_scrape_states
    run_source("Priority State Portals (direct)", "states_direct", fetch_direct_scrape_states)

    # Google CSE (broad US state portal coverage via search API)
    # Uses GOOGLE_CSE_KEY and GOOGLE_CSE_ID from environment variables /
    # GitHub Actions secrets. The fetcher will skip gracefully if either
    # credential is missing.
    # from scrapers.google_cse import fetch_google_cse_results
    # run_source("Google CSE (State Portals)", "google_cse", fetch_google_cse_results)
    #
    # logger.info(f"{'='*55}")
    # logger.info(f"All scrapers complete. Total raw: {len(raw_opps)}")
    # logger.info(f"{'='*55}")
    #
    # return raw_opps

    # Google CSE (State Portals)
    # NOTE: Tested June 2026. Google Custom Search JSON API returned 403
    # permission errors for the new CxA rfp-monitor Google Cloud project even
    # after enabling the API, creating a Programmable Search Engine, creating
    # an API key, and upgrading billing. Google documentation now says Custom
    # Search JSON API is closed to new customers. Keep this disabled unless CxA
    # has access through an older eligible Google Cloud project/API key.
    if "google_cse" in sources:
        logger.warning(
            "Google CSE was requested, but it is disabled because Google "
            "Custom Search JSON API is closed to new customers / blocked for "
            "this project. Use direct scrapers or an eligible older Google "
            "Cloud project if available."
        )

    logger.info(f"{'='*55}")
    logger.info(f"All scrapers complete. Total raw: {len(raw_opps)}")
    logger.info(f"{'='*55}")

    return raw_opps

def main():
    """Full monitoring cycle. See module docstring for step-by-step."""
    args = parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    # Apply mode override if provided on command line
    import config
    mode = args.mode or config.KEYWORD_MODE

    logger.info("=" * 55)
    logger.info("CxA RFP Monitor -- Starting run")
    logger.info(f"  Time:      {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    logger.info(f"  Mode:      {mode} keyword sensitivity")
    logger.info(f"  Run type:  {'DRY RUN' if args.dry_run else 'LIVE'}")
    logger.info(f"  Sources:   {args.sources}")
    logger.info(f"  Force-all: {args.force_all}")
    logger.info("=" * 55)

    # -------------------------------------------------------------------------
    # Step 1: Load deduplication state from Supabase
    # expire_old_entries() cleans up rows older than STATE_EXPIRY_DAYS
    # load_seen_set() pulls the remaining rows into a local dict for fast lookup
    # -------------------------------------------------------------------------
    from dedup import load_seen_set, expire_old_entries
    if args.dry_run:
        seen = {}
    else:
        expire_old_entries()        # Clean up old rows first
        seen = load_seen_set()      # Then load what remains

    # -------------------------------------------------------------------------
    # Step 2: Scrape all sources
    # -------------------------------------------------------------------------
    raw_opps = run_scrapers(args.sources)

    if not raw_opps:
        logger.warning(
            "All scrapers returned 0 results. "
            "Check API keys and source URLs. "
            "Generating empty dashboard."
        )
        if not args.dry_run:
            from delivery import generate_dashboard
            generate_dashboard([], [], mode=mode)
        sys.exit(0)

    # -------------------------------------------------------------------------
    # Step 3: Score and filter
    # -------------------------------------------------------------------------
    logger.info("=" * 55)
    logger.info(f"SCORING (mode={mode})")
    logger.info("=" * 55)
    from scorer import score_split_and_sort, filter_manual_review_candidates
    scored, manual_review, all_scored = score_split_and_sort(raw_opps, mode=mode)
    manual_review = filter_manual_review_candidates(manual_review)

    if not scored:
        logger.info(
            f"No opportunities passed the relevance threshold "
            f"(mode={mode}). Generating dashboard with manual-review candidates."
        )
        if not args.dry_run:
            from delivery import generate_dashboard
            generate_dashboard(
                [],
                [],
                mode=mode,
                manual_review=manual_review,
            )
        sys.exit(0)

    # -------------------------------------------------------------------------
    # Step 4: Deduplicate
    # -------------------------------------------------------------------------
    logger.info("=" * 55)
    logger.info("DEDUPLICATION")
    logger.info("=" * 55)
    from dedup import filter_new_opportunities

    if args.force_all:
        logger.info("--force-all: skipping deduplication")
        new_opps = scored
    else:
        new_opps, skipped = filter_new_opportunities(scored, seen)

    # -------------------------------------------------------------------------
    # Step 5: Log the delivery manifest
    # -------------------------------------------------------------------------
    if new_opps:
        logger.info("=" * 55)
        logger.info(f"DELIVERY MANIFEST: {len(new_opps)} new opportunities")
        logger.info("=" * 55)
        for opp in new_opps:
            logger.info(
                f"  [{opp.confidence:6s}|{opp.relevance_score:3d}pts|"
                f"{(opp.state or 'FED'):<3s}] "
                f"{opp.source:<28s} {opp.title[:60]}"
            )
    else:
        logger.info("No new (unseen) opportunities this run.")

    if args.dry_run:
        logger.info("DRY RUN: stopping before delivery and state update.")
        sys.exit(0)

    # -------------------------------------------------------------------------
    # Step 6: Deliver
    # -------------------------------------------------------------------------
    from delivery import send_email_digest, generate_dashboard

    email_ok    = send_email_digest(new_opps, mode=mode)
    dashboard_ok = generate_dashboard(
        new_opps,
        scored,
        mode=mode,
        manual_review=manual_review,
    )

    logger.info(
        f"Delivery: email={'OK' if email_ok else 'FAILED'} | "
        f"dashboard={'OK' if dashboard_ok else 'FAILED'}"
    )

    # -------------------------------------------------------------------------
    # Step 7: Mark as seen and save state
    # Only mark as seen if at least one delivery channel succeeded.
    # If both fail, leave opportunities unseen so the next run retries.
    # -------------------------------------------------------------------------
    if email_ok or dashboard_ok:
        if new_opps:
            from dedup import save_seen_set
            saved = save_seen_set(new_opps)
            if not saved:
                logger.error(
                    "CRITICAL: Failed to save seen-set to Supabase. "
                    "Duplicates will appear on the next run."
                )
    else:
        logger.warning(
            "Both delivery channels failed. NOT marking as seen. "
            "Opportunities will be retried on the next run."
        )

    logger.info("=" * 55)
    logger.info("CxA RFP Monitor -- Run complete")
    logger.info("=" * 55)


if __name__ == "__main__":
    main()
