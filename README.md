# CxA RFP Monitor

Automated scanner for EM&V (Evaluation, Measurement & Verification), program evaluation, energy efficiency, and related RFP opportunities across federal, state, utility, and quasi-public sources.

The monitor runs through GitHub Actions and produces two outputs:

1. **Email digest** through SendGrid.
2. **GitHub Pages dashboard** with a main opportunity table and a collapsed manual-review section.

The main table shows opportunities that pass the scoring threshold. The manual-review section shows filtered below-threshold opportunities that may still be worth occasional human review.

---

## What It Does

Each run:

1. Queries **SAM.gov** for federal opportunities matching configured keywords and NAICS codes.
2. Scrapes configured **utility and quasi-public sources**.
3. Scrapes configured **priority state portals**.
4. Scores all raw opportunities using tiered keyword matching.
5. Splits scored opportunities into:
   - passing opportunities,
   - below-threshold manual-review candidates,
   - all scored opportunities.
6. Deduplicates passing opportunities against previously reported records in Supabase.
7. Sends an email digest for new passing opportunities when email delivery is enabled.
8. Generates a static GitHub Pages dashboard.

---

## Current Status

| Feature / Source | Status             | Notes                                                                                                                            |
|---|--------------------|----------------------------------------------------------------------------------------------------------------------------------|
| SAM.gov federal scraping | Working            | Requires `SAM_API_KEY`. Uses keyword and NAICS searches.                                                                         |
| Supabase deduplication | Working            | Uses `rfp_seen_opportunities` table.                                                                                             |
| Email digest via SendGrid | Working            | Controlled by workflow input for manual runs.                                                                                    |
| GitHub Pages dashboard | Working            | Deploys from `main`; feature branches upload preview artifact only.                                                              |
| Dashboard manual-review section | Working            | Shows filtered below-threshold candidates.                                                                                       |
| NASEO RFP Board | Working            | Dedicated parser targets open RFP/RFI section.                                                                                   |
| NEEP | Working            | Dedicated parser avoids informational false positives.                                                                           |
| AESP Active RFPs | Working            | Dedicated parser for active RFP/RFQ/RFI listings.                                                                                |
| Efficiency Maine | Working            | Dedicated parser skips closed/awarded/prequalified postings.                                                                     |
| VEIC & Efficiency Vermont | Working            | Uses VEIC RFP page. Efficiency Vermont direct page no longer lists open RFPs.                                                    |
| Vermont DPS RFP page | Working            | Dedicated parser and added as Vermont source.                                                                                    |
| Vermont Business Registry | Working            | Dedicated parser and added as broader Vermont fallback source.                                                                   |
| Massachusetts COMMBUYS | Working            | Dedicated parser for current public open-bid HTML.                                                                               |
| NYSERDA | Working            | Included as utility source and direct NY source.                                                                                 |
| California CaleProcure | Working            | Direct scrape source.                                                                                                            |
| Green Mountain Power | Working            | Generic source; some older PDFs may remain in manual review.                                                                     |
| Mass Save / EEAC | Working            | Generic RFP source.                                                                                                              |
| DOE EERE Funding Opportunities | Working            | Broad national source.                                                                                                           |
| ISO-NE Solicitations | Working / noisy    | Generic source; some non-RFP links may fall into manual review.                                                                  |
| NYISO Procurement | Needs follow-up    | Current configured URL returns 404; left unchanged for now.                                                                      |
| National Grid | Phase 2            | Skipped because source is JavaScript-rendered.                                                                                   |
| Avangrid / United Illuminating | Phase 2            | Skipped because source is JavaScript-rendered.                                                                                   |
| Google CSE | Disabled / Phase 2 | Google Custom Search JSON API was blocked/closed for new customers. Google’s replacement option appears to be Vertex AI Search / Agent Builder, but it is not a free service. |
| EPA ENERGY STAR Solicitations | Disabled           | No confirmed current replacement URL.                                                                                            |
| PJM solicitations | Needs follow-up | Configured URL returned 0 candidates and appears to be broken/not useful as a scrapeable RFP source. |
---

## Repository Structure

```text
rfp-monitor/
├── main.py                    # Orchestrator / entry point
├── config.py                  # Keywords, sources, thresholds, email settings
├── models.py                  # Opportunity dataclass and shared utilities
├── scorer.py                  # Keyword scoring and manual-review filtering
├── dedup.py                   # Supabase deduplication
├── delivery.py                # SendGrid email + GitHub Pages dashboard generator
├── requirements.txt           # Python dependencies
├── docs/
│   └── index.html             # Dashboard output file
├── scrapers/
│   ├── __init__.py
│   ├── sam_gov.py             # SAM.gov federal API scraper
│   ├── web_sources.py         # Utility/quasi-public and state portal scrapers
│   └── google_cse.py          # Google CSE scraper, currently disabled in main.py
└── .github/
    └── workflows/
        └── rfp_monitor.yml    # GitHub Actions workflow
```

---

## Main Run Flow

The full monitoring cycle is handled in `main.py`:

1. Parse workflow/CLI arguments.
2. Load and expire Supabase deduplication records unless running in dry-run mode.
3. Run selected scrapers.
4. Score all raw opportunities.
5. Filter below-threshold candidates for manual review.
6. Deduplicate passing opportunities.
7. Send email digest if SendGrid is available/enabled.
8. Generate dashboard.
9. Save newly delivered opportunities to Supabase if at least one delivery channel succeeds.

If no opportunities pass the scoring threshold, the dashboard is still generated with manual-review candidates as long as raw opportunities were scraped and survived the manual-review cleanup filter.

If all scrapers return zero raw opportunities, the run generates an empty dashboard.

---

## GitHub Actions Workflow

The workflow can run on schedule or manually.

Scheduled run:

```text
cron: 0 12 * * 1
```

This is Monday at 12:00 UTC. Depending on daylight saving time, that is either 7:00 AM or 8:00 AM Eastern.

Manual runs are available from:

```text
GitHub → Actions → CxA RFP Monitor → Run workflow
```

### Manual Workflow Inputs

| Input | Description |
|---|---|
| `mode` | Keyword mode: `broad` or `medium`. |
| `dry_run` | If `true`, runs scrapers/scoring only and skips delivery/state update. |
| `sources` | Source group to run: `sam`, `utilities`, `states_direct`, `google_cse`, or `all`. |
| `force_all` | If `true`, skips deduplication and reports all passing opportunities. Use carefully. |
| `send_email` | If `true`, passes the SendGrid key and allows email delivery. If `false`, email is skipped. |

### Recommended Manual Test Settings

Dashboard-only test:

```text
mode: broad
dry_run: false
sources: utilities
force_all: false
send_email: false
```

Full-source dashboard test without email:

```text
mode: broad
dry_run: false
sources: all
force_all: false
send_email: false
```

Controlled email test:

```text
mode: broad
dry_run: false
sources: utilities
force_all: true
send_email: true
```

Use `force_all: true` only for controlled testing because it bypasses the seen-set and can resend opportunities that were already reported.

---

## GitHub Pages Deployment Behavior

Dashboard generation and GitHub Pages deployment are separated:

- On **feature branches**, the workflow uploads a downloadable `rfp-dashboard-preview` artifact.
- On **main**, the workflow deploys the dashboard to GitHub Pages.
- This allows dashboard testing before merge without changing the live dashboard.

The dashboard output path is configured in `config.py`:

```python
DASHBOARD_OUTPUT_PATH = "docs/index.html"
```

---

## Email Delivery Behavior

Email is sent through SendGrid using `SENDGRID_API_KEY`.

For manual workflow runs:

- `send_email: false` leaves `SENDGRID_API_KEY` blank and skips email delivery.
- `send_email: true` passes the SendGrid key and allows email delivery.

For scheduled runs:

- The workflow passes the SendGrid key automatically, so the weekly digest can send if the secret and recipient configuration are valid.

Email settings are configured in `config.py`:

```python
SENDGRID_API_KEY_ENV = "SENDGRID_API_KEY"
EMAIL_FROM = "..."
EMAIL_TO = [...]
EMAIL_SUBJECT_PREFIX = "[CxA RFP Monitor]"
```

The sending address must be authorized/accepted by SendGrid.

---

## GitHub Secrets Required

These are configured under:

```text
GitHub repo → Settings → Secrets and variables → Actions
```

| Secret | Purpose |
|---|---|
| `SAM_API_KEY` | SAM.gov federal opportunities API. |
| `SENDGRID_API_KEY` | SendGrid email delivery. |
| `SUPABASE_URL` | Supabase project URL for deduplication. |
| `SUPABASE_KEY` | Supabase key for deduplication. |
| `GOOGLE_CSE_KEY` | Google Custom Search key; currently unused/disabled. |
| `GOOGLE_CSE_ID` | Google Custom Search engine ID; currently unused/disabled. |

---

## Supabase Deduplication

Deduplication is stored in Supabase table:

```text
rfp_seen_opportunities
```

Expected table columns:

| Column | Type |
|---|---|
| `unique_key` | text |
| `date_found` | text |
| `expiry_date` | text |
| `source` | text |
| `title` | text |

Each opportunity has a stable unique key based on source and notice ID. If a notice ID is not available, the code falls back to a source + URL hash.

Entries expire after the configured retention period:

```python
STATE_EXPIRY_DAYS = 180
```

If Supabase credentials are missing or unavailable, deduplication is skipped and all passing opportunities may appear as new for that run.

---

## Keyword Scoring

Keyword scoring is defined in `config.py` and applied in `scorer.py`.

Two modes are available:

| Mode | Behavior |
|---|---|
| `broad` | Uses primary, secondary, and tertiary keywords. Wider net. Current default. |
| `medium` | Uses primary and secondary keywords only. Tighter EM&V focus. |

Scoring tiers:

| Tier | Example Focus | Points |
|---|---|---|
| Primary | EM&V, M&V, IPMVP, savings verification | Highest |
| Secondary | Program evaluation, impact evaluation, NTG, deemed savings | Medium |
| Tertiary | Broader energy efficiency / building performance terms | Lowest; broad mode only |

Title matches receive an additional title bonus because titles are often the strongest available signal.

### Manual Review Candidates

The scoring criteria were not changed when the manual-review section was added.

The code now separates results into:

1. Passing opportunities.
2. Below-threshold manual-review candidates.
3. All scored opportunities.

The dashboard displays passing opportunities in the main table. A filtered subset of below-threshold opportunities appears in the collapsed manual-review section.

Manual-review filtering removes obvious navigation/support links such as “skip to content,” email-protection links, generic program pages, and other non-procurement noise.

---

## Source Groups

Use the `--sources` argument locally or the `sources` workflow input in GitHub Actions.

| Source Group | What it runs |
|---|---|
| `sam` | SAM.gov federal opportunities only. |
| `utilities` | Utility and quasi-public sources from `UTILITY_SOURCES`, plus NASEO. |
| `states_direct` | Priority direct state portal scrapers from `DIRECT_SCRAPE_STATES`. |
| `google_cse` | Currently disabled in `main.py`. |
| `all` | SAM.gov, utility/quasi-public sources, and direct state scrapes. |

---

## Running Locally

Install dependencies:

```bash
pip install -r requirements.txt
```

Set environment variables as needed.

PowerShell example:

```powershell
$env:SAM_API_KEY="your-key"
$env:SENDGRID_API_KEY="your-key"
$env:SUPABASE_URL="your-url"
$env:SUPABASE_KEY="your-key"
```

Run examples:

```powershell
# Dry run against utility sources
python main.py --dry-run --sources utilities

# Dry run against all enabled sources
python main.py --dry-run --sources all

# Medium mode test
python main.py --dry-run --mode medium --sources all

# Live local run
python main.py --sources utilities
```

A dry run skips delivery and state updates.

---

## Common Maintenance Tasks

### Add or edit keywords

Edit the keyword lists in `config.py`:

```python
KEYWORDS_PRIMARY
KEYWORDS_SECONDARY
KEYWORDS_TERTIARY
```

Use primary terms for core EM&V language, secondary terms for program evaluation language, and tertiary terms for broader adjacent energy-efficiency language.

### Add or disable a source

Edit `UTILITY_SOURCES` or `DIRECT_SCRAPE_STATES` in `config.py`.

To temporarily disable a source without deleting it:

```python
"active": False
```

To mark a source as JavaScript-rendered and skip it until Phase 2:

```python
"js_render": True
```

### Add a dedicated parser

1. Add or update a source entry in `config.py` with a custom `type`.
2. Add a branch for that type in `_scrape_by_type()` in `scrapers/web_sources.py`.
3. Add the dedicated parser function in `scrapers/web_sources.py`.
4. Test the parser function locally before running the full monitor.

### Test a single parser locally

Example:

```powershell
python -c "from scrapers.web_sources import fetch_utility_sources; opps=fetch_utility_sources(); print(len(opps)); [print(o.title, '|', o.deadline, '|', o.url) for o in opps[:10]]"
```

---

## Known Issues / Future Work

| Item | Status / Next Step |
|---|---|
| NYISO Procurement | Current configured URL returns 404. Need replacement source or disable source. |
| National Grid | JavaScript-rendered; requires Playwright or alternate static RFP feed. |
| Avangrid / United Illuminating | JavaScript-rendered; requires Playwright or targeted static page if available. |
| Google CSE | Disabled because current Google project/API access is blocked. Re-enable only with an eligible API key/project. |
| Generic scrapers | Can still collect old PDFs or broader informational pages. Manual-review section helps surface these without polluting the main table. |
| Source drift | Website redesigns may silently reduce candidates to zero. If a normally productive source drops to zero, inspect the HTML and update selectors. |
| PJM solicitations | Configured URL appears broken or no longer exposes a useful solicitation page. Disable or replace once a reliable static PJM RFP/procurement source is identified. |
---

## Deployment Checklist

Before making the monitor fully live:

1. Confirm `EMAIL_FROM` and `EMAIL_TO` in `config.py`.
2. Confirm SendGrid sender/domain authentication.
3. Confirm GitHub Pages is set to deploy through GitHub Actions.
4. Run manual workflow from `main` with:

```text
mode: broad
dry_run: false
sources: all
force_all: false
send_email: false
```

5. Confirm dashboard deploys successfully.
6. Run controlled email test if needed:

```text
mode: broad
dry_run: false
sources: utilities
force_all: true
send_email: true
```

7. Confirm scheduled Monday run is enabled.

---

## Troubleshooting

### Email did not send

Check the workflow log for:

```text
SENDGRID_API_KEY not set. Skipping email delivery.
```

For manual runs, make sure `send_email` was set to `true`.

Also check:

- `SENDGRID_API_KEY` exists in GitHub Actions secrets.
- `EMAIL_FROM` is authorized in SendGrid.
- Recipients are listed in `EMAIL_TO`.

### Dashboard did not deploy

Check whether the workflow was run from `main`.

Feature branches upload a preview artifact but do not deploy to GitHub Pages.

### Too many false positives

Options:

- Switch mode from `broad` to `medium`.
- Raise `MIN_SCORE_INCLUDE_BROAD`.
- Move broad terms from tertiary to commented-out.
- Add source-specific excludes in a dedicated parser.
- Keep broad mode but use the manual-review section for lower-confidence items.

### Real opportunities are missing

Options:

- Lower the relevant score threshold.
- Promote a keyword from tertiary to secondary or secondary to primary.
- Add missing source-specific terms.
- Inspect whether the source page changed and the scraper returned zero candidates.
- Add a dedicated parser for that source.

### Duplicate opportunities are appearing

Check:

- Supabase credentials are configured.
- `rfp_seen_opportunities` table exists.
- `save_seen_set()` succeeded after the prior run.
- `force_all` was not set to `true`.

---

## Notes for Future Developers

The code is intentionally organized so most routine tuning happens in `config.py`.

Use dedicated parsers for important sources when generic scraping creates false positives. The dedicated parser approach is currently used for several sources where page structure or document context matters.

The dashboard is static HTML with client-side filtering. It does not require a server.

The monitor is designed for partial success. One broken source should not stop the full run.
