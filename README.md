# CxA RFP Monitor

Automated weekly scanner for EM&V (Evaluation, Measurement & Verification) and related RFP opportunities across federal, state, and utility/quasi-public sources.

Runs every Monday at 7am ET via GitHub Actions. Results are delivered by email digest to configured recipients.

---

## What It Does

Each weekly run:

1. Queries **SAM.gov** API for federal opportunities matching EM&V keywords and NAICS codes
2. Scrapes **utility and quasi-public sources** (NYSERDA, NEEP, NASEO, Green Mountain Power, ISO-NE, DOE EERE, and others)
3. Scrapes **priority state portals** (Vermont VSIGNS, Massachusetts COMMBUYS, NYSERDA direct, California CEC)
4. Scores all results against a tiered keyword list (primary/secondary/tertiary)
5. Deduplicates against previously-reported opportunities (stored in Supabase)
6. Delivers new findings via **email digest** (SendGrid)

---

## Current Status

| Feature | Status |
|---|---|
| SAM.gov federal scraping | Working |
| NASEO RFP Board | Working |
| NYSERDA | Working |
| NEEP | Working |
| Green Mountain Power | Working |
| ISO-NE | Working |
| DOE EERE | Working |
| California CEC | Working |
| Supabase deduplication | Working |
| Email digest (SendGrid) | Working |
| GitHub Pages dashboard | Pending (needs GitHub org write permissions) |
| Google CSE state portals | Pending (needs Google Cloud billing account) |
| Vermont VSIGNS | Failing (portal-level DNS issue) |
| Massachusetts COMMBUYS | Failing (gzip encoding error) |
| Efficiency Maine | 404 (URL needs updating) |
| Mass Save / EEAC | 404 (URL needs updating) |
| National Grid | Skipped (JS-rendered, Phase 2) |
| Avangrid | Skipped (JS-rendered, Phase 2) |
| Efficiency Vermont | Blocked (403, manual monitoring recommended) |

---

## Repository Structure

```
rfp-monitor/
├── main.py                    # Orchestrator -- entry point
├── config.py                  # All keywords, sources, thresholds, settings
├── models.py                  # Opportunity dataclass + shared utilities
├── scorer.py                  # Keyword relevance scoring engine
├── dedup.py                   # Deduplication via Supabase
├── delivery.py                # Email delivery (SendGrid) + dashboard generator
├── requirements.txt           # Python dependencies
├── scrapers/
│   ├── __init__.py
│   ├── sam_gov.py             # SAM.gov federal API scraper
│   ├── web_sources.py         # Utility/quasi-public and state portal scrapers
│   └── google_cse.py         # Google Custom Search (currently disabled)
└── .github/
    └── workflows/
        └── rfp_monitor.yml    # GitHub Actions workflow
```

---

## GitHub Secrets Required

These must be set under **Settings > Secrets and variables > Actions**:

| Secret | Purpose | Where to get it |
|---|---|---|
| `SAM_API_KEY` | SAM.gov federal API | sam.gov > Profile > Public API Key |
| `SENDGRID_API_KEY` | Email delivery | app.sendgrid.com > Settings > API Keys |
| `GOOGLE_CSE_KEY` | Google state portal search (disabled) | console.cloud.google.com |
| `GOOGLE_CSE_ID` | Google search engine ID (disabled) | programmablesearchengine.google.com |
| `SUPABASE_URL` | Deduplication database | Supabase project > Settings > API |
| `SUPABASE_KEY` | Supabase anon key | Supabase project > Settings > API |

---

## Supabase Table

The deduplication state is stored in a table called `rfp_seen_opportunities` in the existing CxA Supabase project (the same project used for the PTO Tracker).

Table schema:
| Column | Type |
|---|---|
| `unique_key` | text |
| `date_found` | text |
| `expiry_date` | text |
| `source` | text |
| `title` | text |

---

## Keyword Mode

Two sensitivity modes are available. Set `KEYWORD_MODE` in `config.py` or pass `--mode` at runtime:

| Mode | Description |
|---|---|
| `broad` | All three keyword tiers active. Wider net, more results. **Current default.** |
| `medium` | Primary + secondary tiers only. Tighter EM&V focus, fewer results. |

To switch to medium mode temporarily, use the manual workflow trigger in GitHub Actions and select "medium" from the Mode dropdown.

---

## Manual Trigger Options

From GitHub Actions > CxA RFP Monitor > Run workflow:

| Option | Description |
|---|---|
| Mode | broad or medium keyword sensitivity |
| Dry run | Score and log only -- no email, no state update |
| Sources | sam, utilities, states_direct, google_cse, or all |
| Force all | Ignore seen-set and report everything (use carefully) |

---

## Running Locally

```bash
# Install dependencies
pip install -r requirements.txt

# Set environment variables
export SAM_API_KEY="your-key"
export SENDGRID_API_KEY="your-key"
export SUPABASE_URL="your-url"
export SUPABASE_KEY="your-key"

# Dry run against SAM.gov only (fastest test)
python main.py --dry-run --sources sam

# Full dry run
python main.py --dry-run

# Live run
python main.py
```

---

## Email Configuration

Recipients are configured in `config.py`:

```python
EMAIL_TO = [
    "eric@cx-assoc.com",
    # Add others here as needed
]
```

The sending address (`EMAIL_FROM`) must match a verified sender in SendGrid. Domain authentication for `cx-assoc.com` has been completed via CNAME records in Network Solutions.

---

## Pending Items / Phase 2

- **GitHub Pages dashboard**: Requires GitHub org-level write permissions to be enabled so the workflow can commit the generated `docs/index.html` back to the repo after each run.
- **Google CSE**: Requires a Google Cloud billing account to be attached to the `rfp-monitor` project. Free tier (100 queries/day) is sufficient -- billing account is required even for free usage.
- **Playwright upgrade**: National Grid and Avangrid portals are JavaScript-rendered and can't be scraped with basic requests. Playwright would enable scraping these.
- **Vermont VSIGNS**: DNS resolution failure from GitHub Actions runner -- may need direct IP or alternative URL.
- **Massachusetts COMMBUYS**: gzip decoding error -- needs a custom request header fix.
