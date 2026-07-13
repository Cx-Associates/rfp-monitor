# CxA RFP Monitor

Automated scanner for RFP/RFQ/RFI opportunities relevant to Cx Associates. The same codebase currently supports two monitor types:

1. **EM&V / Evaluation** (`emv`)
2. **Commissioning / RCx** (`commissioning`)

The monitor runs through GitHub Actions, scrapes configured federal, utility, quasi-public, and state/municipal sources, scores opportunities using monitor-specific keyword tiers, sends email digests, publishes GitHub Pages dashboards, and sends separate source-health emails after each non-dry run.

---

## Current Production Behavior

The scheduled production workflow runs every Monday and Thursday at:

```text
57 9 * * 1,4
```

That is Monday and Thursday at 9:57 UTC. Depending on daylight saving time, this is either 4:57 AM or 5:57 AM Eastern.

On scheduled runs, the GitHub Actions workflow runs both monitors sequentially from the same job:

```bash
python main.py --mode broad --monitor-type emv --sources all
python main.py --mode broad --monitor-type commissioning --sources all
```

Each monitor run is independent. Each run has its own monitor type, keyword set, score thresholds, dashboard output path, opportunity email recipients, source-health records, and Supabase table scope.

---

## Outputs

The monitor produces three categories of outputs.

### 1. Opportunity Email Digest

The opportunity digest is sent through SendGrid.

It reports newly identified passing opportunities only. It does not re-email opportunities that were already saved to the Supabase seen-set unless deduplication is bypassed with `--force-all` or Supabase is unavailable.

Current opportunity digest subjects are monitor-specific:

| Monitor | Subject Prefix |
| --- | --- |
| `emv` | `[CxA RFP Monitor]` |
| `commissioning` | `[CxA Commissioning RFP Monitor]` |

Current opportunity digest recipients are configured in `config.py`.

#### EM&V / Evaluation Digest Recipients

```text
riazul.hoque@cx-assoc.com
eric@cx-assoc.com
carrie.napolitan@cx-assoc.com
liza.boyle@cx-assoc.com
rachael@cx-assoc.com
matt@cx-assoc.com
```

#### Commissioning / RCx Digest Recipients

```text
carrie.napolitan@cx-assoc.com
cathleen.branon-keogh@cx-assoc.com
walker@cx-assoc.com
mike.lacrosse@cx-assoc.com
matt@cx-assoc.com
eric@cx-assoc.com
riazul.hoque@cx-assoc.com
```

### 2. GitHub Pages Dashboard

The dashboard is a static GitHub Pages site with a landing page and one dashboard page per monitor.

| Page | Purpose |
| --- | --- |
| `docs/index.html` | Landing page linking to each monitor dashboard |
| `docs/emv.html` | EM&V / Evaluation dashboard |
| `docs/commissioning.html` | Commissioning / RCx dashboard |

Current live URLs are configured in `config.py`:

```text
https://cx-associates.github.io/rfp-monitor/emv.html
https://cx-associates.github.io/rfp-monitor/commissioning.html
```

The dashboard has:

- a main opportunity table for opportunities that pass the scoring threshold;
- an active opportunity cache so previously identified passing opportunities remain visible until their deadline, or for 30 days when no deadline is available;
- a collapsed manual-review section for filtered below-threshold opportunities;
- manual-review X buttons that call a Supabase Edge Function and write suppression records;
- client-side filtering/searching;
- a "NEW" indicator for opportunities newly identified in the current run.

### 3. Source-Health Email

Each non-dry monitor run sends a separate source-health email through SendGrid. This email is intentionally separate from the opportunity digest.

The source-health email is sent to:

```text
riazul.hoque@cx-assoc.com
liza.boyle@cx-assoc.com
eric@cx-assoc.com
```

The health email subject format is:

```text
[CxA RFP Monitor Health] <Monitor Label> source report - <Date> (<error count> errors, <warning count> warnings)
```

Examples:

```text
[CxA RFP Monitor Health] EM&V / Evaluation source report - July 10, 2026 (0 errors, 12 warnings)
[CxA RFP Monitor Health] Commissioning / RCx source report - July 10, 2026 (0 errors, 12 warnings)
```

Source-health reporting is currently **in-memory and email-only**. It is not yet persisted to Supabase. A future enhancement should persist health results so we can trend sources that repeatedly return zero candidates or repeatedly fail.

---

## What Each Run Does

Each monitor run follows this flow:

1. Parse CLI/workflow arguments:
   - keyword mode: `broad` or `medium`;
   - monitor type: `emv` or `commissioning`;
   - dry-run flag;
   - force-all flag;
   - source group selection.
2. Normalize the monitor type and set `MONITOR_TYPE` for Supabase scoping.
3. Load the seen-set from Supabase unless this is a dry run.
4. Run selected scraper groups:
   - SAM.gov;
   - utility/quasi-public sources;
   - priority state/direct sources.
5. Record source-health results during scraping.
6. Score raw opportunities with the keyword set for the selected monitor type.
7. Split scored opportunities into:
   - passing opportunities;
   - below-threshold manual-review candidates;
   - all scored opportunities.
8. Filter manual-review candidates to remove obvious navigation/support-page noise.
9. Load manual-review suppressions from Supabase and remove suppressed manual-review rows.
10. For non-dry runs, upsert current passing opportunities into the active dashboard cache.
11. For non-dry runs, load active cached dashboard opportunities.
12. Merge current passing opportunities with cached active opportunities.
13. Deduplicate current passing opportunities against the monitor-specific Supabase seen-set unless `--force-all` is used.
14. For non-dry runs, send:
   - the opportunity digest;
   - the source-health email;
   - the dashboard files.
15. Save newly delivered opportunities to the Supabase seen-set if at least one main delivery channel succeeds.

Important behavior:

- The **opportunity digest** is for newly identified passing opportunities.
- The **dashboard** is an active opportunity board.
- The **source-health email** is for source monitoring and troubleshooting.
- A broken source should not stop the full run.
- Dry runs stop before delivery and state update.

---

## Monitor Types

The monitor type controls keyword tiers, score threshold, dashboard output, email subject prefix, digest recipients, and Supabase record scope.

Valid monitor types:

```text
emv
commissioning
```

### EM&V / Evaluation Monitor

The `emv` monitor uses EM&V, M&V, program evaluation, energy-efficiency evaluation, demand-side management, savings verification, technical review, impact/process evaluation, non-energy impacts, TRM, and related terms.

Broad-mode threshold:

```text
2
```

Medium-mode threshold:

```text
5
```

### Commissioning / RCx Monitor

The `commissioning` monitor uses commissioning, retro-commissioning, MBCx, CxA/CxP, functional performance testing, systems verification, TAB/commissioning, LEED commissioning, building enclosure commissioning, envelope testing, HVAC/BAS/controls, building systems, and broader facility project indicators.

Broad-mode threshold:

```text
5
```

Medium-mode threshold:

```text
5
```

The commissioning threshold is intentionally higher than EM&V broad mode so that a single broad tertiary project indicator is not enough to pass.

---

## Repository Structure

```text
rfp-monitor/
|-- main.py                                      # Orchestrator / entry point
|-- config.py                                    # Keywords, sources, thresholds, monitor settings, email settings
|-- models.py                                    # Opportunity dataclass and shared utilities
|-- scorer.py                                    # Monitor-aware keyword scoring and manual-review filtering
|-- dedup.py                                     # Supabase deduplication, active cache, and suppression filtering
|-- delivery.py                                  # SendGrid emails, source-health email, dashboard generator, landing page generator
|-- source_health.py                             # In-memory source-health records and health-code summary
|-- requirements.txt                             # Python dependencies
|-- docs/
|   |-- index.html                               # Landing page output
|   |-- emv.html                                 # EM&V dashboard output
|   `-- commissioning.html                       # Commissioning dashboard output
|-- scrapers/
|   |-- __init__.py
|   |-- sam_gov.py                               # SAM.gov federal API scraper
|   |-- web_sources.py                           # Utility/quasi-public and direct state/municipal scrapers
|   `-- google_cse.py                            # Google CSE scraper, currently disabled in main.py
|-- supabase/
|   `-- functions/
|       `-- suppress-manual-review/
|           `-- index.ts                         # Edge Function used by dashboard X button
`-- .github/
    `-- workflows/
        |-- rfp_monitor.yml                      # RFP monitor workflow
        `-- supabase_keepalive.yml               # Daily Supabase keepalive workflow

---

## GitHub Actions Workflow

Workflow file:

```text
.github/workflows/rfp_monitor.yml
```

The workflow has two triggers:

1. Scheduled twice-weekly run.
2. Manual `workflow_dispatch` run.

### Scheduled Run

The scheduled run ignores manual workflow inputs and runs both monitor types sequentially:

```bash
python main.py --mode broad --monitor-type emv --sources all
python main.py --mode broad --monitor-type commissioning --sources all
```

Scheduled runs provide SendGrid credentials to the Python process through GitHub Secrets, so scheduled runs are expected to send both opportunity digests and source-health emails.

Expected scheduled output volume if both runs complete and SendGrid is available:

```text
1 EM&V opportunity digest
1 EM&V source-health email
1 commissioning opportunity digest
1 commissioning source-health email
```

The opportunity digest may be a "No new RFPs this week" email if no new passing opportunities survive deduplication.

### Manual Workflow Inputs

Manual runs are available from:

```text
GitHub -> Actions -> CxA RFP Monitor -> Run workflow
```

| Input | Description |
| --- | --- |
| `mode` | Keyword mode: `broad` or `medium`. |
| `monitor_type` | Monitor type: `emv` or `commissioning`. |
| `dry_run` | If `true`, runs scrapers/scoring only and skips delivery/state update. |
| `sources` | Source group to run: `sam`, `utilities`, `states_direct`, `google_cse`, or `all`. |
| `force_all` | If `true`, skips deduplication and reports all passing opportunities. Use carefully. |
| `send_email` | If `true`, exposes `SENDGRID_API_KEY` to the run and allows emails. If `false`, opportunity and source-health emails are skipped. |

Manual runs execute only the selected `monitor_type`.

### Supabase Keepalive Workflow

The repository also includes a separate lightweight workflow:

```text
.github/workflows/supabase_keepalive.yml

### GitHub Pages Behavior

Dashboard generation and GitHub Pages deployment are separated.

- Feature branches upload a downloadable `rfp-dashboard-preview` artifact.
- `main` deploys to GitHub Pages.
- Dry-run workflow dispatches do not upload/deploy dashboards.

The preview/deploy artifact includes:

```text
docs/index.html
docs/emv.html
docs/commissioning.html
```

---

## Running Locally

Install dependencies:

```powershell
pip install -r requirements.txt
```

Set environment variables as needed:

```powershell
$env:SAM_API_KEY="your-key"
$env:SENDGRID_API_KEY="your-key"
$env:SUPABASE_URL="your-url"
$env:SUPABASE_KEY="your-key"
```

Run examples:

```powershell
# EM&V dry run across all sources
python main.py --mode broad --monitor-type emv --sources all --dry-run

# Commissioning dry run across all sources
python main.py --mode broad --monitor-type commissioning --sources all --dry-run

# Utility-only EM&V dry run
python main.py --mode broad --monitor-type emv --sources utilities --dry-run

# Direct-state commissioning dry run
python main.py --mode broad --monitor-type commissioning --sources states_direct --dry-run

# Manual live local run, no force-all
python main.py --mode broad --monitor-type emv --sources utilities

# Manual live local run that bypasses deduplication
python main.py --mode broad --monitor-type commissioning --sources utilities --force-all
```

Use live local runs carefully. If `SENDGRID_API_KEY` and Supabase variables are set locally, a live run can send emails and update Supabase.

### Safe Local Live-Path Test Without Email or Supabase

This verifies that the non-dry delivery path is called while preventing email delivery and Supabase writes:

```powershell
$oldSendGrid = $env:SENDGRID_API_KEY
$oldSupabaseUrl = $env:SUPABASE_URL
$oldSupabaseKey = $env:SUPABASE_KEY

$env:SENDGRID_API_KEY = ""
$env:SUPABASE_URL = ""
$env:SUPABASE_KEY = ""

python main.py --mode broad --monitor-type commissioning --sources utilities,states_direct --force-all

$env:SENDGRID_API_KEY = $oldSendGrid
$env:SUPABASE_URL = $oldSupabaseUrl
$env:SUPABASE_KEY = $oldSupabaseKey
```

Expected delivery-path log pattern:

```text
SENDGRID_API_KEY not set. Skipping email delivery.
SENDGRID_API_KEY not set. Skipping source health email.
Delivery: email=FAILED | source_health_email=FAILED | dashboard=OK
```

Because Supabase variables are blank in this test, warnings about skipped deduplication and failed seen-set save are expected.

### Useful Local Test Commands

Compile key files:

```powershell
python -m py_compile source_health.py config.py delivery.py main.py dedup.py scorer.py models.py scrapers/web_sources.py scrapers/sam_gov.py
```

Run both full dry runs:

```powershell
python main.py --mode broad --monitor-type emv --sources all --dry-run
python main.py --mode broad --monitor-type commissioning --sources all --dry-run
```

Check git status before committing:

```powershell
git status
git diff --stat
git diff --check
```

Restore locally generated dashboard output before committing, unless the dashboard files were intentionally changed:

```powershell
git restore docs/index.html docs/emv.html docs/commissioning.html
```

---

## Source Groups

Use the `--sources` CLI argument locally or the `sources` workflow input in GitHub Actions.

| Source Group | What it runs |
| --- | --- |
| `sam` | SAM.gov federal opportunities only. |
| `utilities` | Active utility and quasi-public sources in `UTILITY_SOURCES`, plus the dedicated NASEO parser. |
| `states_direct` | Direct state, municipal, and priority procurement sources in `DIRECT_SCRAPE_STATES`. |
| `google_cse` | Currently disabled in `main.py`. If requested, the run logs a warning and continues without CSE results. |
| `all` | SAM.gov, utility/quasi-public sources, and direct state/municipal sources. |

---

## Current Source Inventory

This inventory is based on the current `UTILITY_SOURCES` and `DIRECT_SCRAPE_STATES` configuration.

### Utility / Quasi-Public Sources

| Source | Parser Type / Status |
| --- | --- |
| NEEP (Northeast Energy Efficiency Partnerships) | `neep_rfps` |
| ACEEE | inactive |
| E4TheFuture | inactive |
| NYSERDA | `nyserda_current_funding`; API-backed parser for current PON/RFP/RFI/RFQ/RFQL listings |
| ISO-NE Solicitations | generic list |
| Eversource (MA/CT/NH) | generic list |
| Green Mountain Power | generic list |
| National Grid (NY/NE) | skipped: `js_render=True` / Phase 2 |
| Avangrid / United Illuminating (CT) | skipped: `js_render=True` / Phase 2 |
| VEIC & Efficiency Vermont | `veic_rfps` |
| Energy Trust of Oregon Contracting Opportunities | `energy_trust_rfps` |
| PG&E Energy Efficiency Solicitations | `pge_ee_solicitations` |
| PJM Interconnection Solicitations | inactive |
| NYISO Procurement | generic list; current URL needs follow-up |
| AESP Active RFPs | `aesp_rfps` |
| Efficiency Maine | `efficiency_maine_rfps` |
| Burlington Electric Department RFPs | `burlington_electric_rfps` |
| Mass Save / EEAC | generic list |
| Cape Light Compact RFPs | `cape_light_rfps` |
| Entergy RFPs | `entergy_rfps` |
| DOE EERE Funding Opportunities | generic list |
| EPA ENERGY STAR Solicitations | inactive |

### Direct State / Municipal / Priority Sources

| Source | Parser Type / Status |
| --- | --- |
| Vermont VSIGNS | `vsigns`; known connection/DNS issue in recent local runs |
| Massachusetts COMMBUYS | `commbuys` |
| NYSERDA Funding (direct) | inactive; disabled to avoid duplicate scraping because NYSERDA is covered in `UTILITY_SOURCES` |
| California CaleProcure | `ca_eprocure` |
| SUNY SUCF Construction Bid Calendar | `suny_sucf_bid_calendar_pdf` |
| NYS Contract Reporter | `nyscr_contract_reporter` |
| Connecticut DEEP RFP Search | `ct_deep_rfp_search` |
| Vermont DPS Requests for Proposals | `vermont_dps_rfps` |
| VT BGS OPC Current Bid Listings | `vt_bgs_opc_bids` |
| Colchester VT Bid Postings | `civicengage_bids` |
| Essex VT Bid Postings | `civicengage_bids` |
| Montpelier VT Bid Postings | `civicengage_bids` |
| South Burlington VT Bid Postings | `civicengage_bids` |
| Fairfax VT RFPs | `fairfax_vt_bids` |
| Essex Junction VT Invitation to Bid | `municipal_document_links` |
| Woodstock VT Request for Proposals | `municipal_document_links` |
| Rutland VT Bids and RFPs | `municipal_document_links` |
| Shelburne VT Bids RFQs and RFPs | `municipal_document_links` |
| Saranac Lake NY Bids RFPs RFQs | `municipal_document_links` |
| Vermont Business Registry Bid Search | `vermont_business_registry` |
| NH Department of Energy RFPs | `nh_energy_rfps` |
| Maine Municipal Association RFPs | `maine_municipal_association_rfps` |
| Maine BGS Business Opportunities | `maine_bgs_business_opportunities` |
| University of Maine System Upcoming Bids | `umaine_upcoming_bids` |
| Connecticut Energy Efficiency Board RFPs | `ct_eeb_rfps` |

---

## Source-Health Reporting

Source-health tracking is implemented in `source_health.py` and used by `main.py`, `scrapers/web_sources.py`, and `delivery.py`.

Health records are stored in memory during a single Python process. At the end of each non-dry run, the records are rendered into a separate HTML email.

Current health codes:

| Code | Meaning |
| --- | --- |
| `HEALTH_OK_NONZERO` | Source returned one or more candidates. |
| `HEALTH_WARN_ZERO` | Source completed but returned 0 candidates. |
| `HEALTH_WARN_SKIPPED_JS` | Source was skipped because it is marked `js_render=True` / Phase 2. |
| `HEALTH_ERROR_EXCEPTION` | Source threw an exception that reached the source wrapper. |
| `HEALTH_WARN_TOTAL_ZERO` | Entire source group returned 0 candidates. |

### Interpreting Source-Health Emails

A warning does not automatically mean the run failed.

Examples:

- `HEALTH_WARN_ZERO` may be normal for sources that simply have no current RFPs.
- `HEALTH_WARN_SKIPPED_JS` is expected for sources intentionally deferred to Phase 2.
- `HEALTH_WARN_TOTAL_ZERO` is expected for SAM.gov in local runs where `SAM_API_KEY` is not set.
- `HEALTH_ERROR_EXCEPTION` is more serious and usually means the scraper/source needs immediate review.

### Important V1 Limitation

Some lower-level fetch helpers catch HTTP/connection problems, log a warning, and return an empty result list. In those cases, the wrapper currently records `HEALTH_WARN_ZERO`, not `HEALTH_ERROR_EXCEPTION`.

Known example from recent local runs:

```text
Vermont VSIGNS
```

Recent local runs logged a DNS/name-resolution connection warning for VSIGNS, but the source-health record was:

```text
HEALTH_WARN_ZERO
```

because the underlying fetch returned an empty result rather than raising an exception through the source wrapper.

This should be noted in internal launch/update communication. A future source-health persistence update should distinguish:

```text
true zero candidates
fetch/page-load failure that returned zero
parser failure that returned zero
```

### Future Source-Health Enhancement

Planned later enhancement:

- add a Supabase source-health table;
- persist source name, group, monitor type, health code, candidate count, message, and run timestamp;
- trend sources that repeatedly return zero candidates;
- flag normally productive sources that suddenly drop to zero;
- distinguish fetch failures from true zero-candidate pages.

---

## Supabase State and Record Scoping

The monitor uses Supabase for:

1. opportunity email deduplication;
2. active dashboard persistence;
3. manual-review suppression.

Current tables:

```text
opportunity_seen
opportunity_active
manual_review_suppressed
```

All three tables are scoped by `monitor_type`.

The same source/opportunity can therefore exist independently for:

```text
emv
commissioning
```

This is intentional. It prevents the EM&V monitor from hiding or deduplicating commissioning results, and vice versa.

### Important Import/Environment Behavior

`dedup.py` reads `MONITOR_TYPE` from the environment when it is imported.

`main.py` normalizes the CLI/environment monitor type and sets:

```python
os.environ["MONITOR_TYPE"] = monitor_type
```

before importing `dedup.py` functions during the run. The scheduled workflow runs each monitor as a separate Python process, so each scheduled monitor run gets the correct Supabase scope.

---

## Supabase Table: `opportunity_seen`

Stores delivered opportunities so future weekly runs do not resend the same RFP.

Expected schema:

```sql
create table if not exists public.opportunity_seen (
  monitor_type text not null,
  unique_key text not null,
  date_found text,
  expiry_date text,
  source text,
  title text,
  primary key (monitor_type, unique_key)
);
```

Expected grants:

```sql
grant usage on schema public to service_role;

grant select, insert, update, delete
on public.opportunity_seen
to service_role;
```

Entries expire after:

```python
STATE_EXPIRY_DAYS = 180
```

Recommended checks:

```sql
select
  monitor_type,
  source,
  title,
  date_found,
  expiry_date
from public.opportunity_seen
order by date_found desc, monitor_type, source, title;
```

Monitor-specific query:

```sql
select
  source,
  title,
  date_found,
  expiry_date
from public.opportunity_seen
where monitor_type = 'commissioning'
order by date_found desc, source, title;
```

---

## Supabase Table: `opportunity_active`

Stores passing opportunities that should remain visible on the dashboard.

Expected schema:

```sql
create table if not exists public.opportunity_active (
  monitor_type text not null,
  unique_key text not null,
  first_seen text,
  last_seen text,
  visible_until text,
  source text,
  title text,
  deadline text,
  opportunity jsonb,
  primary key (monitor_type, unique_key)
);
```

Expected grants:

```sql
grant usage on schema public to service_role;

grant select, insert, update, delete
on public.opportunity_active
to service_role;
```

Visibility rules:

| Opportunity Type | `visible_until` Rule |
| --- | --- |
| Has deadline | Equal to the deadline date. |
| No deadline | 30 days after `first_seen`. |

Recommended checks:

```sql
select
  monitor_type,
  source,
  title,
  deadline,
  first_seen,
  last_seen,
  visible_until
from public.opportunity_active
order by monitor_type, visible_until, source, title;
```

Monitor-specific query:

```sql
select
  source,
  title,
  deadline,
  first_seen,
  last_seen,
  visible_until
from public.opportunity_active
where monitor_type = 'emv'
order by visible_until, source, title;
```

---

## Supabase Table: `manual_review_suppressed`

Stores manual-review rows hidden through the dashboard X button.

Expected schema:

```sql
create table if not exists public.manual_review_suppressed (
  monitor_type text not null,
  unique_key text not null,
  suppressed_at text,
  source text,
  title text,
  reason text,
  suppressed_by text,
  primary key (monitor_type, unique_key)
);
```

Expected grants:

```sql
grant usage on schema public to service_role;

grant select, insert, update, delete
on public.manual_review_suppressed
to service_role;
```

Recommended check:

```sql
select
  monitor_type,
  source,
  title,
  suppressed_at,
  reason,
  suppressed_by
from public.manual_review_suppressed
order by suppressed_at desc, monitor_type, source, title;
```

---

## Manual-Review Suppression / Dashboard X Button

The dashboard manual-review section includes an X button on each manual-review row.

Clicking the X button:

1. Prompts the user for the dashboard removal token if the browser does not already have one.
2. Sends a POST request to the Supabase Edge Function.
3. Writes a row to `manual_review_suppressed`.
4. Removes the row from the current page immediately.
5. Keeps the row hidden from future dashboard generations for the same monitor type.

The dashboard sends the token as:

```text
x-rfp-admin-token
```

The token is stored in browser `localStorage` as:

```text
rfpAdminToken
```

The static dashboard does not contain the token or the Supabase service-role key.

### Supabase Edge Function

Function name:

```text
suppress-manual-review
```

Endpoint currently used by the generated dashboard JavaScript:

```text
https://udxcbyoohgzdkjxytxzg.functions.supabase.co/suppress-manual-review
```

Expected Supabase Edge Function secrets:

```text
RFP_ADMIN_TOKEN
RFP_SUPABASE_URL
RFP_SUPABASE_SERVICE_ROLE_KEY
```

These are Supabase secrets, not GitHub Actions secrets.

Deploy command:

```powershell
supabase functions deploy suppress-manual-review --no-verify-jwt
```

---

## GitHub Secrets Required

Configure these under:

```text
GitHub repo -> Settings -> Secrets and variables -> Actions
```

| Secret | Purpose |
| --- | --- |
| `SAM_API_KEY` | SAM.gov federal opportunities API. |
| `SENDGRID_API_KEY` | SendGrid opportunity digest and source-health email delivery. |
| `SUPABASE_URL` | Supabase project URL for deduplication, active dashboard cache, and suppression filtering during workflow runs. |
| `SUPABASE_KEY` | Supabase service/API key used by the Python Supabase logic. |
| `GOOGLE_CSE_KEY` | Google Custom Search key; currently unused because Google CSE is disabled in `main.py`. |
| `GOOGLE_CSE_ID` | Google Custom Search engine ID; currently unused because Google CSE is disabled in `main.py`. |

Do not commit secret values to the repository.

Important distinction:

- `SUPABASE_URL` and `SUPABASE_KEY` are GitHub Actions secrets used by the Python workflow.
- `RFP_ADMIN_TOKEN`, `RFP_SUPABASE_URL`, and `RFP_SUPABASE_SERVICE_ROLE_KEY` are Supabase Edge Function secrets.

---

## Keyword Scoring

Keyword scoring is defined in `config.py` and applied in `scorer.py`.

Two modes are available:

| Mode | Behavior |
| --- | --- |
| `broad` | Uses primary, secondary, and tertiary keywords. Wider net. Current default. |
| `medium` | Uses primary and secondary keywords only. Tighter result set. |

Scoring weights:

| Match Type | Points |
| --- | --- |
| Primary keyword match | 10 |
| Secondary keyword match | 5 |
| Tertiary keyword match | 2 |
| Title bonus | +5 |

The title bonus is added when the keyword appears in the opportunity title.

Confidence labels:

| Label | Rule |
| --- | --- |
| High | Score at or above `MIN_SCORE_HIGH_CONFIDENCE` |
| Medium | Score is at or above the monitor/mode inclusion threshold but below high-confidence threshold |
| Below threshold | Score below the monitor/mode inclusion threshold |

Current high-confidence threshold:

```text
15
```

Current inclusion thresholds:

| Monitor | Broad | Medium |
| --- | ---: | ---: |
| `emv` | 2 | 5 |
| `commissioning` | 5 | 5 |

---

## Manual Review Candidates

The code separates results into:

1. passing opportunities;
2. below-threshold manual-review candidates;
3. all scored opportunities.

The dashboard displays passing and active cached opportunities in the main table. A filtered subset of below-threshold opportunities appears in the collapsed manual-review section.

Manual-review filtering removes obvious navigation/support links and other low-value rows. Suppressed manual-review rows are removed before dashboard generation.

The manual-review section is intentionally broad. It is useful for spotting possible missed opportunities and reviewing noisy source behavior without pushing those rows into the main opportunity table or email digest.

---

## Source-Specific Notes

### SAM.gov

SAM.gov is API-based and requires:

```text
SAM_API_KEY
```

If `SAM_API_KEY` is missing, the SAM scraper returns zero candidates and the source group records:

```text
HEALTH_WARN_TOTAL_ZERO
```

This is expected in local shells without the key. It is not expected in scheduled GitHub Actions production runs if the secret is configured.

SAM.gov keyword search is title-based. The NAICS search partially compensates for generic titles.

### Google CSE

Google CSE is currently disabled in `main.py`.

If `google_cse` is explicitly requested, the run logs a warning and continues without CSE results. The `google_cse.py` file remains in the repository, but it is not active in the current run flow.

### JavaScript-Rendered Sources

Sources marked `js_render=True` are skipped and recorded as:

```text
HEALTH_WARN_SKIPPED_JS
```

Currently skipped:

```text
National Grid (NY/NE)
Avangrid / United Illuminating (CT)
```

These need a Phase 2 Playwright implementation or an alternate static/feed source.

### Vermont VSIGNS

Vermont VSIGNS remains configured as a direct source.

Recent local runs logged a DNS/name-resolution connection warning, but the V1 source-health code recorded the source as:

```text
HEALTH_WARN_ZERO
```

This is a known V1 limitation because the lower-level fetch helper returned an empty result rather than raising an exception through the source wrapper.

### NYSERDA

NYSERDA is covered through the dedicated `nyserda_current_funding` parser. This parser uses NYSERDA’s current funding opportunities page and captures current PON/RFP/RFI/RFQ/RFQL listings, including notice IDs, descriptions, solicitation type, and due dates.

The duplicate direct NYSERDA source is disabled to avoid returning the same NYSERDA opportunities twice.

Known limitation: NYSERDA items currently link back to the current funding opportunities landing page rather than individual detail pages.

### NYISO Procurement

The configured NYISO procurement URL has returned 404 in recent runs. It remains configured but needs a replacement source URL or should be disabled if no reliable public solicitation page is identified.

### NASEO RFP Board

The NASEO parser targets the open RFP/RFI section and avoids closed/support-document/navigation links where possible. If no open RFP/RFI heading or list is found, it returns zero candidates.

### AESP Active RFPs

The AESP parser targets active RFP/RFQ/RFI listings and filters expired postings when a deadline can be parsed.

### Efficiency Maine

The Efficiency Maine parser skips closed, awarded, and prequalified postings.

### Entergy RFPs

The Entergy parser skips stale prior-year RFPs based on years in titles.

### Burlington Electric Department RFPs

The BED parser monitors the stable BED RFP listing page and keeps dynamic `/rfpdetail?rfp=...` links. Detail pages may be Cloudflare-blocked, so BED items can remain manual-review rows due to limited accessible text.

### California CEC / CaleProcure

The California source uses the `ca_eprocure` parser and prefers California Energy Commission contract/solicitation content. It filters inactive/closed/expired CEC listings when those statuses or deadlines are parseable.

### NYS Contract Reporter

The NYSCR parser reads public listing text blocks and extracts listing fields. Detail pages may require login. The source is broad and can return many general procurement items.

### CT DEEP RFP Search

The CT DEEP parser filters search results toward energy/RFP-related pages and removes common non-procurement noise. Due dates may not always be available in search-result metadata.

### Municipal / Vermont / Maine Sources

Several municipal and regional sources use dedicated parser types such as:

```text
civicengage_bids
municipal_document_links
maine_municipal_association_rfps
maine_bgs_business_opportunities
umaine_upcoming_bids
```

These are useful for commissioning/RCx discovery, but some broad municipal facility projects may require keyword tuning or manual-review suppression over time.

---

## Common Maintenance Tasks

### Add or edit keywords

Edit `config.py`.

For EM&V:

```python
KEYWORDS_PRIMARY
KEYWORDS_SECONDARY
KEYWORDS_TERTIARY
```

For commissioning:

```python
COMMISSIONING_KEYWORDS_PRIMARY
COMMISSIONING_KEYWORDS_SECONDARY
COMMISSIONING_KEYWORDS_TERTIARY
```

Use primary terms for direct/core matches, secondary terms for related technical scope, and tertiary terms for broader project indicators.

### Add or disable a source

Edit `UTILITY_SOURCES` or `DIRECT_SCRAPE_STATES` in `config.py`.

Disable a source without deleting it:

```python
"active": False
```

Mark a source as JavaScript-rendered and skip it until Phase 2:

```python
"js_render": True
```

### Add a dedicated parser

1. Add or update a source entry in `config.py` with a custom `type`.
2. Add a branch for that type in `_scrape_by_type()` in `scrapers/web_sources.py`.
3. Add the dedicated parser function in `scrapers/web_sources.py`.
4. Test the parser function locally.
5. Run the appropriate monitor dry run.
6. Confirm source-health reporting behaves as expected.

### Suppress a manual-review item

1. Open the relevant monitor dashboard.
2. Expand the manual-review section.
3. Click the X button on the item.
4. Enter the dashboard removal token.
5. Confirm the row disappears.
6. Confirm a row was added to `manual_review_suppressed`.

### Un-suppress a manual-review item

Delete the relevant row from Supabase. Example:

```sql
delete from public.manual_review_suppressed
where monitor_type = 'commissioning'
  and unique_key = 'PASTE_UNIQUE_KEY_HERE';
```

### Remove an active dashboard opportunity

Use only if an opportunity was cached incorrectly or should be removed before its `visible_until` date:

```sql
delete from public.opportunity_active
where monitor_type = 'emv'
  and unique_key = 'PASTE_UNIQUE_KEY_HERE';
```

---

## Deployment Checklist

Before merging a major monitor change to `main`:

1. Confirm the branch is clean except intentional changes:

```powershell
git status
```

2. Compile key Python files:

```powershell
python -m py_compile source_health.py config.py delivery.py main.py dedup.py scorer.py models.py scrapers/web_sources.py scrapers/sam_gov.py
```

3. Run both full dry runs:

```powershell
python main.py --mode broad --monitor-type emv --sources all --dry-run
python main.py --mode broad --monitor-type commissioning --sources all --dry-run
```

4. Restore generated dashboard files if a local live run regenerated them unintentionally:

```powershell
git restore docs/index.html docs/emv.html docs/commissioning.html
```

5. Confirm GitHub Actions secrets exist:

```text
SAM_API_KEY
SENDGRID_API_KEY
SUPABASE_URL
SUPABASE_KEY
```

6. Confirm Supabase tables exist:

```text
opportunity_seen
opportunity_active
manual_review_suppressed
```

7. Confirm the Supabase Edge Function still exists and has the required secrets.

8. Merge to `main`.

9. Confirm the workflow runs successfully from `main`.

10. After the next scheduled Monday/Thursday run, check:
    - both monitor runs executed;
    - EM&V dashboard timestamp updated;
    - commissioning dashboard timestamp updated;
    - landing page links work;
    - opportunity digest email behavior is as expected;
    - source-health emails were sent for both monitors;
    - Supabase active cache updated by monitor type;
    - `opportunity_seen` rows are scoped correctly by monitor type;
    - manual-review X buttons still work.

---

## Troubleshooting

### Email did not send

Check the workflow log for:

```text
SENDGRID_API_KEY not set. Skipping email delivery.
```

For manual workflow runs, `send_email` must be set to `true`.

Also check:

- `SENDGRID_API_KEY` exists in GitHub Actions secrets;
- `EMAIL_FROM` is authorized in SendGrid;
- the relevant recipient list in `config.py` is correct.

### Source-health email did not send

Check the workflow log for:

```text
SENDGRID_API_KEY not set. Skipping source health email.
```

The source-health email uses the same `SENDGRID_API_KEY` as the opportunity digest.

For manual workflow runs, `send_email` must be set to `true` for the source-health email to send.

### Dashboard did not deploy

Check whether the workflow ran from `main`.

Feature branches upload a dashboard preview artifact but do not deploy to GitHub Pages.

Also check whether the run was a dry run. Dry-run workflow dispatches do not upload/deploy dashboard files.

### Dashboard landing page is updated but one monitor page is stale

The landing page is regenerated whenever `generate_dashboard()` runs for either monitor. Each monitor dashboard is written only when that monitor run generates its dashboard.

On scheduled production runs, both monitor dashboards should be regenerated because the workflow runs both monitor types sequentially.

### Dashboard does not show an expected RFP

Check:

1. Did the source return candidates?
2. Did the item pass the scoring threshold for the selected monitor?
3. Is it in the manual-review section?
4. Is it suppressed in `manual_review_suppressed`?
5. Is it cached in `opportunity_active`?
6. Is `visible_until` still today or later?

Query:

```sql
select
  monitor_type,
  source,
  title,
  deadline,
  first_seen,
  last_seen,
  visible_until
from public.opportunity_active
where monitor_type = 'emv'
  and title ilike '%PASTE PART OF TITLE HERE%';
```

### Dashboard shows an old RFP

Check the `visible_until` date in `opportunity_active`.

If it has a deadline, it is expected to remain visible through the deadline. If it has no deadline, it is expected to remain visible for 30 days from `first_seen`.

### X button returns Unauthorized

The entered token does not match the Supabase Edge Function secret:

```text
RFP_ADMIN_TOKEN
```

Clear browser localStorage or re-enter the correct token.

### X button returns Invalid API key

The Supabase Edge Function secret is wrong or stale:

```text
RFP_SUPABASE_SERVICE_ROLE_KEY
```

Update the Supabase Function secret and redeploy if needed.

### Duplicate opportunities are appearing

Check:

- Supabase credentials are configured in GitHub Actions;
- `opportunity_seen` exists;
- `save_seen_set()` succeeded after the prior run;
- `force_all` was not set to `true`;
- the source did not change notice IDs/URLs for the same posting.

### Too many false positives

Options:

- switch the relevant monitor from `broad` to `medium`;
- raise the relevant monitor threshold;
- move a broad keyword from tertiary to commented-out;
- replace broad project terms with narrower phrases;
- add source-specific excludes in a dedicated parser;
- use manual-review suppression for repeated low-value manual-review rows.

### Real opportunities are missing

Options:

- inspect source-health records for zero candidates or exceptions;
- check whether the source page changed;
- add or fix a dedicated parser;
- add missing keywords;
- promote a keyword to a higher tier;
- lower the relevant monitor threshold;
- check whether the item is present in manual review but below threshold;
- check whether it is in `opportunity_active` but expired from the dashboard.

---

## Known Issues / Future Work

| Item | Status / Next Step |
| --- | --- |
| Source-health persistence | Health records are currently in-memory and email-only. Add Supabase persistence later to trend repeated zero-candidate sources and repeated failures. |
| Vermont VSIGNS health code | Recent local runs show a DNS/name-resolution warning, but V1 records `HEALTH_WARN_ZERO` because the fetch helper returns an empty result. Future health tracking should distinguish fetch failure from true zero candidates. |
| NYISO Procurement | Current configured URL has returned 404. Need replacement URL or disable source. |
| National Grid | JavaScript-rendered; requires Playwright or alternate static/feed source. |
| Avangrid / United Illuminating | JavaScript-rendered; requires Playwright or alternate static/feed source. |
| Google CSE | Disabled in `main.py`; keep disabled unless an eligible working Google CSE project/API key is available. |
| Generic scrapers | Can collect old PDFs, informational pages, or broad procurement rows. Dedicated parsers and manual-review suppression help manage noise. |
| Source drift | Website redesigns may cause sources to return zero candidates without raising exceptions. Source-health email helps identify this, but persistent trend tracking is still future work. |
| BED detail pages | Detail pages may be Cloudflare-blocked. Parser uses listing links and may have limited scope/deadline text. |
| NYSCR detail links | Detail pages may require login. Parser uses public listing fields and stable CR numbers. |
| CT DEEP metadata | Search-result metadata may not expose due dates. |
| Local Supabase warnings | Expected when local shells do not define `SUPABASE_URL` and `SUPABASE_KEY`. |
| `datetime.utcnow()` deprecation warning | Newer Python versions may warn that `datetime.utcnow()` is deprecated. This warning is not currently breaking the workflow but should be cleaned up later with timezone-aware UTC datetimes. |

---

## Notes for Future Developers

- Routine tuning should happen in `config.py` whenever possible.
- Use dedicated parsers for important sources when generic scraping creates false positives.
- Do not commit generated local dashboard files unless the dashboard output change is intentional.
- Dry runs are the safest way to test scrapers and scoring.
- Manual live local runs can send emails and update Supabase if credentials are set.
- The dashboard is static HTML and does not require a server.
- The active dashboard cache is managed by the Python workflow and Supabase.
- The manual-review X button uses a Supabase Edge Function; secrets are not embedded in static HTML.
- Source-health email is currently operational but not persistent.
- The scheduled production workflow must be merged to `main` to affect Monday's scheduled run.
