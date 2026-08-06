# CxA RFP Monitor

Automated scanner for RFP/RFQ/RFI opportunities relevant to Cx Associates.

The same codebase currently supports two monitor types:

1. **EM&V / Evaluation** (`emv`)
2. **Commissioning / RCx** (`commissioning`)

The monitor runs through GitHub Actions, scrapes configured federal, utility, quasi-public, state, municipal, and priority procurement sources, scores opportunities using monitor-specific keyword tiers, sends opportunity email digests, publishes opportunity and source-health GitHub Pages dashboards, persists source-health history to Supabase, and sends a first-Monday monthly source-health summary.

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

Scheduled monitor runs expose `SENDGRID_API_KEY` for the two opportunity digests. Source-health email is not sent from either monitor process; one monthly summary is sent after a successful Pages deployment on the first Monday.

---

## Outputs

The monitor produces three categories of outputs.

### 1. Opportunity Email Digest

The opportunity digest is sent through SendGrid.

It reports newly identified passing opportunities only. It does not re-email opportunities already saved to the Supabase seen-set unless deduplication is bypassed with `--force-all` or Supabase is unavailable.

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

The dashboard is a static GitHub Pages site with a landing page, two opportunity dashboards, and one monthly source-health dashboard.

| Page | Purpose |
| --- | --- |
| `docs/index.html` | Landing page linking to all dashboards |
| `docs/emv.html` | EM&V / Evaluation opportunity dashboard |
| `docs/commissioning.html` | Commissioning / RCx opportunity dashboard |
| `docs/source-health.html` | Previous-month source-health dashboard |

Current live URLs are configured in `config.py`:

```text
https://cx-associates.github.io/rfp-monitor/emv.html
https://cx-associates.github.io/rfp-monitor/commissioning.html
https://cx-associates.github.io/rfp-monitor/source-health.html
```

The dashboard has:

- a main opportunity table for opportunities that pass the scoring threshold;
- an active opportunity cache so previously identified passing opportunities remain visible until their deadline, or for 30 days when no deadline is available;
- a collapsed manual-review section for filtered below-threshold / low-score opportunities;
- live team-review fields for each row:
  - Review Status;
  - Reviewer Fit;
  - Tech Owner;
  - Admin Owner;
  - Admin reviewed;
  - EM&V tech reviewed;
  - Cx tech reviewed;
  - Technical Review Notes;
  - Admin Review Notes;
- review-field loading and saving through the `opportunity-review` Supabase Edge Function;
- manual-review promotion logic that can move reviewed manual candidates into the main dashboard table;
- a visible **Promoted from Manual Review** badge for manually promoted rows;
- preservation of the original automated confidence score when a row is promoted manually;
- manual-review X buttons that call the `suppress-manual-review` Supabase Edge Function and write suppression records;
- client-side filtering/searching;
- a `NEW` indicator for opportunities newly identified in the current run.

Manual promotion does not change the automated scoring result. It adds a human-review layer on top of the automated score so the team can intentionally surface a below-threshold or low-confidence item in the main dashboard while still seeing how the monitor originally scored it.

### 3. Monthly Source-Health Review

Source health is reviewed through a static dashboard plus one monthly SendGrid notification. The old per-monitor/per-run source-health emails have been removed; opportunity digest emails are unchanged.

The source-health dashboard is:

```text
https://cx-associates.github.io/rfp-monitor/source-health.html
```

It is generated after both scheduled monitors finish and is deployed in the same GitHub Pages artifact as the opportunity dashboards. The public page contains aggregate statuses, counts, recent-run indicators, and data-completeness checks. It intentionally excludes raw exception messages, GitHub run identifiers, credentials, and request details.

The page is regenerated after every eligible non-dry workflow, but it intentionally reports the **previous Eastern calendar month**, not the month currently in progress. For example, workflows running during August publish the July report; August history first appears after the first eligible September workflow. If the previous month contains no completed source-health runs, a valid empty dashboard is still generated and deployed. This is expected behavior rather than a dashboard-generation failure.

On the **first Monday of each month**, after GitHub Pages deploys successfully, the workflow sends a summary for the previous Eastern calendar month when that reporting month contains at least one completed source-health run. An empty reporting month is intentionally skipped, while the dashboard remains deployed. Notifications are sent to:

```text
riazul.hoque@cx-assoc.com
liza.boyle@cx-assoc.com
eric@cx-assoc.com
```

The subject format is:

```text
[CxA RFP Monitor] Monthly Source Health - <Month Year>: <count> sources to review
```

The email includes a concise metric summary, a sanitized list of sources requiring investigation, and a link to the deployed dashboard. A workflow rerun (`GITHUB_RUN_ATTEMPT` greater than `1`) does not resend the monthly notification. Manual workflow dispatches do not send it. If the previous month has zero completed source-health runs, the workflow records a successful intentional skip and does not call SendGrid.

Each completed non-dry monitor run still attempts to persist its in-memory source-health snapshot to `source_health_runs` and `source_health_records`. Persistence failures are logged but remain isolated from opportunity delivery, opportunity dashboards, and seen-state saves.

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
   - manual-review candidates;
   - all scored opportunities.
8. Filter manual-review candidates to remove obvious navigation/support-page noise.
9. Load manual-review suppressions from Supabase and remove suppressed manual-review rows.
10. For non-dry runs, upsert current passing opportunities into the active dashboard cache.
11. For non-dry runs, load active cached dashboard opportunities.
12. Merge current passing opportunities with cached active opportunities.
13. Deduplicate current passing opportunities against the monitor-specific Supabase seen-set unless `--force-all` is used.
14. For non-dry runs, perform the opportunity delivery actions applicable to that completion path:
   - the opportunity digest;
   - the monitor-specific opportunity dashboard files.
15. Persist the run-level summary and source-level health records to Supabase. This write is nonfatal.
16. Save newly delivered opportunities to the Supabase seen-set if at least one main delivery channel succeeds.

Important behavior:

- The **opportunity digest** is for newly identified passing opportunities.
- The **dashboard** is an active opportunity board.
- The **source-health dashboard and monthly email** summarize persisted source checks across both monitors; they are generated by the workflow after the individual monitor processes finish.
- A broken source should not stop the full run.
- Dry runs stop before delivery and state update.
- If both email and dashboard delivery fail, opportunities are not marked as seen so the next run can retry delivery.

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
|-- dedup.py                                     # Supabase deduplication, active cache, suppression filtering, active-cache reload
|-- delivery.py                                  # Opportunity email, opportunity dashboards, and landing page
|-- source_health.py                             # Source-health collection and Supabase persistence
|-- source_health_report.py                      # Aggregation, streaks, completeness, and Eastern time
|-- source_health_dashboard.py                   # Sanitized static source-health dashboard renderer
|-- generate_source_health_dashboard.py          # Paginated read-only Supabase report generator
|-- source_health_email.py                       # Monthly email summary and renderers
|-- send_monthly_source_health_email.py          # First-Monday guarded SendGrid sender
|-- requirements.txt                             # Python dependencies
|-- docs/
|   |-- index.html                               # Landing page output
|   |-- emv.html                                 # EM&V opportunity dashboard output
|   |-- commissioning.html                       # Commissioning opportunity dashboard output
|   `-- source-health.html                       # Monthly source-health dashboard output
|-- scrapers/
|   |-- __init__.py
|   |-- sam_gov.py                               # SAM.gov federal API scraper
|   |-- web_sources.py                           # Utility/quasi-public and direct state/municipal scrapers
|   `-- google_cse.py                            # Google CSE scraper, currently disabled in main.py
|-- supabase/
|   |-- functions/
|   |   |-- opportunity-review/
|   |   |   `-- index.ts                         # Review-field save/load and manual-promotion Edge Function
|   |   `-- suppress-manual-review/
|   |       `-- index.ts                         # Manual-review X-button suppression Edge Function
|   |-- sql/
|   |   |-- 001_opportunity_review_status.sql    # Review table setup
|   |   `-- 002_source_health_persistence.sql    # Source-health history tables
|   `-- .temp/                                  # Local Supabase CLI temp files; should be ignored by git
`-- .github/
    `-- workflows/
        |-- rfp_monitor.yml                      # RFP monitor workflow
        `-- supabase_keepalive.yml               # Daily Supabase keepalive workflow
```

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

Scheduled runs provide SendGrid credentials for the opportunity digests. After both monitors persist source health, the workflow reads the previous Eastern month, generates `docs/source-health.html`, and deploys all dashboard pages together.

Expected output for every completed scheduled run:

```text
1 EM&V opportunity digest
1 commissioning opportunity digest
1 shared GitHub Pages dashboard deployment
```

On the first Monday only, the original workflow attempt sends one additional monthly source-health summary after deployment.

The opportunity digest may be a `No new RFPs in this run` email if no new passing opportunities survive deduplication.

### Manual Workflow Inputs

Manual runs are available from:

```text
GitHub -> Actions -> CxA RFP Monitor -> Run workflow
```

| Input | Description |
| --- | --- |
| `mode` | Keyword mode: `broad` or `medium`. |
| `monitor_type` | Monitor type: `emv`, `commissioning`, or `both`. `both` runs EM&V first, then commissioning. |
| `dry_run` | If `true`, runs scrapers/scoring only and skips delivery/state update. |
| `sources` | Source group to run: `sam`, `utilities`, `states_direct`, `google_cse`, or `all`. |
| `force_all` | If `true`, skips deduplication and reports all passing opportunities. Use carefully. |
| `send_email` | If `true`, exposes `SENDGRID_API_KEY` to the monitor run and allows opportunity digest emails. It does not trigger the monthly source-health email. |

Manual runs execute the selected `monitor_type`. If `both` is selected, the workflow runs EM&V first and then commissioning from the same workflow run.

Manual runs only send opportunity digest email when `send_email` is set to `true`. Monthly source-health notification is restricted to the first-Monday scheduled workflow.

### GitHub Pages Behavior

Dashboard generation and GitHub Pages deployment are separated.

- Feature branches upload a downloadable `rfp-dashboard-preview` artifact.
- `main` deploys to GitHub Pages.
- Dry-run workflow dispatches do not upload/deploy dashboards.
- There is no separate `push` trigger in the workflow. Dashboard deployment occurs when the workflow itself runs from `main`.

The preview/deploy artifact includes:

```text
docs/index.html
docs/emv.html
docs/commissioning.html
docs/source-health.html
```

### Supabase Keepalive Workflow

The repository also includes a separate lightweight workflow:

```text
.github/workflows/supabase_keepalive.yml
```

It runs daily at:

```text
17 13 * * *
```

That is 13:17 UTC.

The keepalive workflow performs a lightweight Supabase REST query against `opportunity_seen` to reduce the risk of free-plan inactivity pause. It uses the same GitHub Actions secrets as the Python workflow:

```text
SUPABASE_URL
SUPABASE_KEY
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
SUPABASE_URL or SUPABASE_KEY not set. Source-health persistence will be skipped.
Delivery: email=FAILED | dashboard=OK
Source health persistence: FAILED
```

Because Supabase variables are blank in this test, warnings about skipped deduplication, skipped source-health persistence, and failed seen-set save are expected.

### Useful Local Test Commands

Compile key files:

```powershell
python -m py_compile source_health.py source_health_report.py source_health_dashboard.py source_health_email.py generate_source_health_dashboard.py send_monthly_source_health_email.py config.py delivery.py main.py dedup.py scorer.py models.py scrapers/web_sources.py scrapers/sam_gov.py
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

`docs/source-health.html` is generated by the workflow and may be untracked
after a local production-generator test. Inspect it with `git status`; remove
that specific local file only when it is confirmed to be disposable, and do
not commit it unless a checked-in generated snapshot is intentional.

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

The current production inventory contains **48 configured source concepts**. A normal monitor run creates source-health observations for **44 tracked sources**:

- 42 active entries from `UTILITY_SOURCES` and `DIRECT_SCRAPE_STATES`;
- SAM.gov and NASEO, which are handled by dedicated scrapers outside those two lists.

The other four configured entries are inactive. Two of the 44 tracked sources--National Grid and Avangrid / United Illuminating--are intentionally recorded as JavaScript-rendered skips rather than actively scraped. With the current inventory, each completed monitor run should therefore persist 44 detail records. A normal scheduled workflow runs both monitor types and should persist 88 detail records across two parent runs. If the source inventory changes, these expected counts must be updated.

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

The old direct NYSERDA source is not part of the current direct-source inventory. NYSERDA is covered through the dedicated API-backed utility/quasi-public parser.

---

## Source-Health Reporting

Source-health collection, persistence, aggregation, dashboard rendering, and monthly email rendering are separated across:

| File | Responsibility |
| --- | --- |
| `source_health.py` | In-memory records and nonfatal Supabase persistence |
| `source_health_report.py` | UTC/Eastern conversion, paired-run consolidation, streaks, and completeness checks |
| `source_health_dashboard.py` | Sanitized static public dashboard rendering |
| `generate_source_health_dashboard.py` | Paginated, read-only Supabase loading and monthly dashboard generation |
| `source_health_email.py` | Side-effect-free monthly summary and email rendering |
| `send_monthly_source_health_email.py` | First-Monday/rerun guard, read-only report loading, and SendGrid delivery |

### Health Codes

| Code | Meaning |
| --- | --- |
| `HEALTH_OK_NONZERO` | The source returned one or more raw candidates. These candidates are not necessarily scored or delivered opportunities. |
| `HEALTH_WARN_ZERO` | The source returned no raw candidates. This can be normal and is not automatically a failure. |
| `HEALTH_WARN_SKIPPED_JS` | The source is intentionally skipped because it is JavaScript-rendered / deferred. |
| `HEALTH_WARN_PARTIAL` | A multi-request source completed some API/parser work, but one or more requests or parsing steps failed; results may be incomplete. |
| `HEALTH_ERROR_EXCEPTION` | The source/API was unavailable or failed without producing a valid source result. |
| `HEALTH_WARN_TOTAL_ZERO` | Every source in a major source group returned zero candidates during that monitor execution. |

SAM.gov now emits one explicit source-health record summarizing all configured API queries:

- complete API success with candidates -> `HEALTH_OK_NONZERO`;
- complete API success with zero candidates -> `HEALTH_WARN_ZERO`;
- mixed query/parser success and failure -> `HEALTH_WARN_PARTIAL`;
- no valid API responses, or a missing API key -> `HEALTH_ERROR_EXCEPTION`.

### How Monthly Aggregation Works

A normal scheduled GitHub workflow runs EM&V and commissioning sequentially. Those parent rows share `github_run_id`. Source observations sharing the same workflow ID, source group, and source name are consolidated so a scheduled date counts once rather than twice.

The dashboard displays monthly counts for the previous Eastern calendar month, while consecutive no-result streaks may use earlier persisted history through that month’s end. This prevents a streak from resetting on the first day of a month.

A source appears in **Sources Requiring Investigation** when:

- its latest consolidated result is `ERROR`;
- its latest consolidated result is `PARTIAL`; or
- it has returned no results in **12 consecutive distinct live workflows**.

Twelve no-result runs represent approximately 1.5 months at the current Monday/Thursday schedule. This is an investigation threshold, not automatic proof that a scraper failed. A later candidate-producing observation resets the consecutive no-result count.

`HEALTH_WARN_TOTAL_ZERO` records are excluded from individual-source streaks. They are separately consolidated by workflow and source group for the dashboard’s Entire-Group Zero Events summary.

### Time Zones

Supabase stores all source-health timestamps as UTC `timestamptz` values. Human-facing dashboard and email timestamps are converted with `America/New_York` and labeled `EST` or `EDT` according to the date.

For example:

```text
Stored:    2026-07-28T15:40:00+00:00
Displayed: July 28, 2026 at 11:40 AM EDT
```

UTC remains the correct storage format because it is unambiguous and portable. Eastern conversion happens only at the reporting layer.

### Supabase Persistence and Permissions

Each completed non-dry monitor run attempts:

1. one parent insert into `source_health_runs`;
2. zero or more detail inserts into `source_health_records`.

The migration is `supabase/sql/002_source_health_persistence.sql`. It provides:

- a foreign key from detail rows to parent runs with cascading deletion;
- query indexes for report reads;
- row-level security on both tables;
- `service_role` select and insert access;
- sequence usage required for detail-row IDs;
- no public `anon` or ordinary `authenticated` policies.

The production key must be the matching project’s modern secret/service-role credential. Controlled local checks verified client construction, SDK reads, and real parent/detail insert/select permission. REST deletion with the service credential is not granted; disposable cleanup can be performed in the Supabase SQL Editor under an administrative database role.

Persistence in `main.py` is deliberately nonfatal. A failed history write must not block opportunity email delivery, opportunity-dashboard generation, or seen-state saves. In contrast, the workflow’s monthly source-health read/generation step must succeed before the Pages artifact deploys, preventing a stale or missing source-health page from being advertised as current.

### Public Dashboard Privacy

The source-health page is deployed on the existing public GitHub Pages site. It requests and renders only fields needed for aggregation. Raw diagnostic messages and unnecessary workflow metadata are not requested by the production reader and are never rendered into the HTML or monthly email.

### V1 Test Scope

V1 was validated with visible, deterministic external scripts covering SAM result classification, aggregation, month-boundary streaks, UTC-to-Eastern conversion, HTML sanitization, pagination, workflow ordering, dry-run isolation, failure isolation, fake SendGrid delivery, real Supabase read/insert/select permission, and removal of per-run health emails.

These validation scripts are not committed as a formal automated test suite. Adding committed unit/integration tests and CI execution is explicitly deferred to V2.

### Production Source-Health Verification

After a scheduled production workflow, use the GitHub run ID from the Actions log to reconcile each parent row with its persisted detail rows. This query is read-only:

```sql
select
  r.monitor_type,
  r.github_run_id,
  r.github_run_attempt,
  r.total_records as expected_records,
  count(d.id) as persisted_records,
  r.ok_count,
  r.warn_count,
  r.error_count
from public.source_health_runs r
left join public.source_health_records d
  on d.run_id = r.id
where r.github_run_id = 'PASTE_GITHUB_RUN_ID_HERE'
group by
  r.id,
  r.monitor_type,
  r.github_run_id,
  r.github_run_attempt,
  r.total_records,
  r.ok_count,
  r.warn_count,
  r.error_count
order by r.monitor_type;
```

For a normal scheduled workflow using the current 44-source inventory, expect two rows--one `emv` and one `commissioning`--with 44 expected and 44 persisted records in each row.

To exercise the production read/aggregation/render path without changing the database, generate a preview for a completed month. The reference time must fall in the month after the reporting month:

```powershell
$healthPreview = Join-Path $env:TEMP "source_health_monthly_preview.html"

python -B .\generate_source_health_dashboard.py `
  --output $healthPreview `
  --reference-time "2026-09-01T12:00:00-04:00"

if ($LASTEXITCODE -ne 0) {
    throw "SOURCE-HEALTH DASHBOARD READ TEST FAILED"
}

Get-Item -LiteralPath $healthPreview |
    Select-Object FullName, Length, LastWriteTime
Start-Process -FilePath $healthPreview
```

This command performs Supabase SELECT requests and writes only the specified local preview file. It does not insert, update, or delete database rows and does not send email.

---

## Supabase State and Record Scoping

The monitor uses Supabase for:

1. opportunity email deduplication;
2. active dashboard persistence;
3. manual-review suppression;
4. dashboard review-field storage;
5. manual-review promotion persistence;
6. source-health run history.

Current tables:

```text
opportunity_seen
opportunity_active
manual_review_suppressed
opportunity_review_status
source_health_runs
source_health_records
```

The source-health tables are scoped by `monitor_type`. V1 enables row-level security and grants access to `service_role`. The server-side workflow uses that role to read the history and generate sanitized static HTML; browser-loaded dashboards do not query these tables directly.

All opportunity-state tables are scoped by `monitor_type` where applicable.

The same source/opportunity can therefore exist independently for:

```text
emv
commissioning
```

This is intentional. It prevents the EM&V monitor from hiding or deduplicating commissioning results, and vice versa.

The review table uses a shared `review_key` so the same opportunity can carry review data across both dashboards when the source and notice ID match.

### Important Import/Environment Behavior

`dedup.py` reads `MONITOR_TYPE` from the environment when it is imported.

`main.py` normalizes the CLI/environment monitor type and sets:

```python
os.environ["MONITOR_TYPE"] = monitor_type
```

before importing `dedup.py` functions during the run. The scheduled workflow runs each monitor as a separate Python process, so each scheduled monitor run gets the correct Supabase scope.

---

## Supabase Table: `opportunity_seen`

Stores delivered opportunities so future scheduled runs do not resend the same RFP.

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

Recommended check:

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

Stores opportunities that should remain visible on the dashboard. This includes automatically passing opportunities and manually promoted review candidates.

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
| Manually promoted with deadline | Equal to the deadline date. |
| Manually promoted without deadline | 30 days after first promotion / first active-cache record. |

Recommended check:

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

Check promoted manual rows:

```sql
select
  monitor_type,
  unique_key,
  source,
  title,
  deadline,
  visible_until,
  opportunity ->> 'confidence' as confidence,
  opportunity ->> 'promoted_from_manual_review' as promoted_from_manual_review,
  opportunity ->> 'manual_promoted' as manual_promoted,
  opportunity ->> 'promotion_label' as promotion_label,
  last_seen
from public.opportunity_active
where opportunity ->> 'promoted_from_manual_review' = 'true'
order by last_seen desc nulls last
limit 20;
```

---

## Supabase Table: `opportunity_review_status`

Stores live dashboard team-review fields. The dashboards load these records on page load and save them through the `opportunity-review` Edge Function.

Canonical setup SQL is stored at:

```text
supabase/sql/001_opportunity_review_status.sql
```

Expected schema:

```sql
create table if not exists public.opportunity_review_status (
  review_key text primary key,
  source text,
  notice_id text,
  title text,
  url text,

  review_status text,
  reviewer_fit text,
  tech_owner text,
  admin_owner text,

  admin_reviewed boolean not null default false,
  emv_technical_reviewed boolean not null default false,
  commissioning_technical_reviewed boolean not null default false,

  technical_review_notes text,
  admin_review_notes text,

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  updated_by text
);
```

Expected indexes:

```sql
create index if not exists idx_opportunity_review_status_source
on public.opportunity_review_status (source);

create index if not exists idx_opportunity_review_status_notice_id
on public.opportunity_review_status (notice_id);
```

Expected grants:

```sql
grant usage on schema public to service_role;

grant select, insert, update, delete
on public.opportunity_review_status
to service_role;
```

Recommended check:

```sql
select
  review_key,
  source,
  title,
  review_status,
  reviewer_fit,
  tech_owner,
  admin_owner,
  admin_reviewed,
  emv_technical_reviewed,
  commissioning_technical_reviewed,
  technical_review_notes,
  admin_review_notes,
  updated_at
from public.opportunity_review_status
order by updated_at desc
limit 50;
```

Check a specific title:

```sql
select
  review_key,
  source,
  title,
  review_status,
  reviewer_fit,
  tech_owner,
  admin_owner,
  admin_reviewed,
  emv_technical_reviewed,
  commissioning_technical_reviewed,
  technical_review_notes,
  admin_review_notes,
  updated_at
from public.opportunity_review_status
where title ilike '%PASTE PART OF TITLE HERE%'
order by updated_at desc;
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

## Dashboard Review Fields, Manual Promotion, and Manual-Review Suppression

The dashboards are static HTML files, but they have live review-field behavior through Supabase Edge Functions.

There are two related dashboard write paths:

| Dashboard action | Edge Function | Supabase table affected |
| --- | --- | --- |
| Load review fields | `opportunity-review` | reads `opportunity_review_status` |
| Save review fields | `opportunity-review` | upserts `opportunity_review_status` |
| Promote eligible manual row | `opportunity-review` | upserts `opportunity_active` |
| Remove no-longer-eligible manual promotion | `opportunity-review` | deletes matching row from `opportunity_active` |
| Hide manual-review row with X button | `suppress-manual-review` | upserts `manual_review_suppressed` |

### Review Fields

Review fields include:

```text
Review Status
Reviewer Fit
Tech Owner
Admin Owner
Admin reviewed
EM&V tech reviewed
Cx tech reviewed
Technical Review Notes
Admin Review Notes
```

The dashboard can load review data without a user-entered token. Saving review data requires the dashboard edit token.

The dashboard sends the token as:

```text
x-rfp-admin-token
```

The token is stored in browser `localStorage` as:

```text
rfpAdminToken
```

The static dashboard does not contain the token or the Supabase service-role key.

### Manual Promotion Rules

Manual-review rows can be promoted into the main dashboard when human review indicates that the item should be watched despite the automated score.

A row is promoted only when all of the following are complete:

1. **Reviewer Fit** is filled and is not `Poor Fit`.
2. **Review Status** is filled.
3. Either **Technical Review Notes** or **Admin Review Notes** is filled.
4. At least one review-complete checkbox is checked:
   - Admin reviewed;
   - EM&V tech reviewed;
   - Cx tech reviewed.
5. At least one owner field is filled:
   - Tech Owner;
   - Admin Owner.

Manual-review section membership is the source of truth for promotion eligibility. A row does not need to have the literal confidence label `Below threshold`; low-confidence manual-review rows can also be promoted if the human review criteria above are complete.

When a row is promoted:

- it moves into the main dashboard table immediately in the browser;
- it receives a **Promoted from Manual Review** badge;
- it keeps the original automated confidence label, such as `Low` or `Below threshold`;
- it is visually highlighted using the promoted-manual row style;
- it is saved to `opportunity_active` by the `opportunity-review` Edge Function;
- it remains visible through its deadline, or for 30 days if no deadline exists.

If a promoted manual row later no longer satisfies the promotion criteria, the dashboard moves it back to the manual-review section and the Edge Function removes the manual-promotion row from `opportunity_active`.

### Manual Promotion Persistence

When a manual-review row satisfies the promotion criteria, the `opportunity-review` Edge Function writes a promoted row to:

```text
public.opportunity_active
```

The saved opportunity JSON includes:

```text
promoted_from_manual_review = true
promotion_label = Promoted from Manual Review
```

The promoted row keeps the original automated confidence label. The dashboard therefore can show, for example, a `Low` confidence row in the main table with a **Promoted from Manual Review** badge.

### Manual-Review Suppression / Dashboard X Button

The dashboard manual-review section includes an X button on each manual-review row.

Clicking the X button:

1. Prompts the user for the dashboard removal/edit token if the browser does not already have one.
2. Sends a POST request to the `suppress-manual-review` Supabase Edge Function.
3. Writes a row to `manual_review_suppressed`.
4. Removes the row from the current page immediately.
5. Keeps the row hidden from future dashboard generations for the same monitor type.

### Supabase Edge Functions

Review and promotion function:

```text
opportunity-review
```

Endpoint currently used by the generated dashboard JavaScript:

```text
https://udxcbyoohgzdkjxytxzg.functions.supabase.co/opportunity-review
```

Supported actions:

| Action | Requires dashboard token? | Behavior |
| --- | --- | --- |
| `list` | No user-entered token | Loads review records by `review_key`. |
| `save` | Yes | Saves review fields and updates/removes active-cache manual promotion as applicable. |

Manual-review suppression function:

```text
suppress-manual-review
```

Endpoint currently used by the generated dashboard JavaScript:

```text
https://udxcbyoohgzdkjxytxzg.functions.supabase.co/suppress-manual-review
```

Supported behavior:

| Function | Requires dashboard token? | Behavior |
| --- | --- | --- |
| `suppress-manual-review` | Yes | Upserts a suppression row into `manual_review_suppressed`. |

Expected Supabase Edge Function secrets:

```text
RFP_ADMIN_TOKEN
RFP_SUPABASE_URL
RFP_SUPABASE_SERVICE_ROLE_KEY
```

These are Supabase secrets, not GitHub Actions secrets.

Deploy commands:

```powershell
supabase functions deploy opportunity-review --project-ref udxcbyoohgzdkjxytxzg --no-verify-jwt
supabase functions deploy suppress-manual-review --project-ref udxcbyoohgzdkjxytxzg --no-verify-jwt
```

Deploy with `--no-verify-jwt` because the dashboards are hosted as public static HTML on GitHub Pages. Write protection is handled by the custom `x-rfp-admin-token` header.

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
| `SUPABASE_URL` | Supabase project URL for deduplication, active dashboard cache, suppression filtering, and source-health persistence during workflow runs. |
| `SUPABASE_KEY` | Supabase service/API key used by the Python Supabase logic. |
| `GOOGLE_CSE_KEY` | Google Custom Search key; currently unused because Google CSE is disabled in `main.py`. |
| `GOOGLE_CSE_ID` | Google Custom Search engine ID; currently unused because Google CSE is disabled in `main.py`. |

Do not commit secret values to the repository.

Important distinction:

- `SUPABASE_URL` and `SUPABASE_KEY` are GitHub Actions secrets used by the Python workflow and keepalive workflow.
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
| --- | ---: |
| Primary keyword match | 10 |
| Secondary keyword match | 5 |
| Tertiary keyword match | 2 |
| Title bonus | +5 |

The title bonus is added when the keyword appears in the opportunity title.

Confidence labels:

| Label | Rule |
| --- | --- |
| High | Score at or above `MIN_SCORE_HIGH_CONFIDENCE`. |
| Medium | Score is at or above the monitor/mode inclusion threshold but below high-confidence threshold. |
| Low | Used for active/manual/persisted rows when applicable. |
| Below threshold | Score below the monitor/mode inclusion threshold. |

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
2. manual-review candidates;
3. all scored opportunities.

The dashboard displays passing and active cached opportunities in the main table. A filtered subset of below-threshold or low-score opportunities appears in the collapsed manual-review section.

Manual-review filtering removes obvious navigation/support links and other low-value rows. Suppressed manual-review rows are removed before dashboard generation.

The manual-review section is intentionally broad. It is useful for spotting possible missed opportunities and reviewing noisy source behavior without pushing those rows into the main opportunity table or email digest automatically.

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

NYSERDA is covered through the dedicated `nyserda_current_funding` parser. This parser uses NYSERDA’s current funding opportunities data and captures current procurement-style RFP/RFI/RFQ/RFQL listings, including notice IDs, descriptions, solicitation type, and due dates.

The NYSERDA API also includes broad funding/program opportunities in addition to procurement-style RFPs/RFQs/RFIs/RFQLs. Program Opportunity Notices (PONs) are intentionally excluded from the monitor because they are generally funding, incentive, training, loan, open-enrollment, or program-participation opportunities rather than CxA service procurements.

Rachael Straub receives NYSERDA PONs through direct NYSERDA email notifications and monitors those separately. The RFP monitor is therefore focused on NYSERDA procurement-style opportunities rather than duplicating the PON email stream.

The old direct NYSERDA entry is not part of the current direct-source inventory. If duplicate rows from that old source appear on the dashboard, they are likely stale records in `public.opportunity_active` and can be removed from Supabase without touching `opportunity_seen`.

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

### Maine Municipal Association

The Maine Municipal Association parser prioritizes explicit proposal, bid, response, or submission deadlines found in the detail page body before falling back to the MMA page-level End Date.

This is important because the MMA End Date can represent the listing expiration date rather than the actual proposal due date.

Example issue fixed: `RFP - Assessment of Heating and Electrical Systems - Gouldsboro`. The page-level MMA End Date was July 30, 2026, but the actual proposal due date had already passed. The parser now avoids keeping that stale opportunity active.

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
4. Enter the dashboard removal/edit token.
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

### Remove a test manual-promotion record

Use this after an end-to-end test of the review/promotion workflow:

```sql
delete from public.opportunity_review_status
where title ilike '%PASTE TEST TITLE HERE%';

delete from public.opportunity_active
where
  title ilike '%PASTE TEST TITLE HERE%'
  or opportunity ->> 'title' ilike '%PASTE TEST TITLE HERE%';
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
python -m py_compile source_health.py source_health_report.py source_health_dashboard.py source_health_email.py generate_source_health_dashboard.py send_monthly_source_health_email.py config.py delivery.py main.py dedup.py scorer.py models.py scrapers/web_sources.py scrapers/sam_gov.py
```

3. Run both full dry runs when scraper/scoring behavior changed:

```powershell
python main.py --mode broad --monitor-type emv --sources all --dry-run
python main.py --mode broad --monitor-type commissioning --sources all --dry-run
```

4. Regenerate dashboards when dashboard HTML/JavaScript changed:

```powershell
python main.py --mode broad --monitor-type emv --sources all
python main.py --mode broad --monitor-type commissioning --sources all
```

5. Validate generated dashboards when dashboard logic changed:

```powershell
@'
from pathlib import Path

files = [Path("docs/emv.html"), Path("docs/commissioning.html")]

for p in files:
    s = p.read_text(encoding="utf-8-sig")

    print(f"\nChecking {p}...")

    checks = {
        "has Low/manual promotion comment": "Manual candidates may display confidence as Low" in s,
        "does not have old Below-threshold-only gate": "toLowerCase() !== 'below threshold'" not in s,
        "has manual promotion function": "function rowQualifiesForManualPromotion" in s,
        "has duplicate guard": "Keep the already-promoted main-table row" in s,
        "has manual promoted data attr": "data-manual-promoted" in s,
        "has matched keywords data attr": "data-matched-keywords" in s,
        "has opportunity review endpoint": "functions.supabase.co/opportunity-review" in s,
        "has suppression endpoint": "functions.supabase.co/suppress-manual-review" in s,
    }

    failed = False
    for name, ok in checks.items():
        print(f"{'OK  ' if ok else 'FAIL'} {name}")
        failed = failed or not ok

    if failed:
        raise SystemExit(f"{p} failed generated-dashboard validation.")

print("\nGenerated dashboard validation passed.")
'@ | python
```

6. Restore generated dashboard files if a local live run regenerated them unintentionally:

```powershell
git restore docs/index.html docs/emv.html docs/commissioning.html
```

`docs/source-health.html` is generated by the workflow and may be untracked
after a local production-generator test. Inspect it with `git status`; remove
that specific local file only when it is confirmed to be disposable, and do
not commit it unless a checked-in generated snapshot is intentional.

7. Confirm GitHub Actions secrets exist:

```text
SAM_API_KEY
SENDGRID_API_KEY
SUPABASE_URL
SUPABASE_KEY
GOOGLE_CSE_KEY
GOOGLE_CSE_ID
```

8. Confirm Supabase tables exist:

```text
opportunity_seen
opportunity_active
manual_review_suppressed
opportunity_review_status
source_health_runs
source_health_records
```

9. Confirm Supabase Edge Function secrets exist:

```text
RFP_ADMIN_TOKEN
RFP_SUPABASE_URL
RFP_SUPABASE_SERVICE_ROLE_KEY
```

10. Deploy changed Edge Functions before relying on live dashboard writes:

```powershell
supabase functions deploy opportunity-review --project-ref udxcbyoohgzdkjxytxzg --no-verify-jwt
supabase functions deploy suppress-manual-review --project-ref udxcbyoohgzdkjxytxzg --no-verify-jwt
```

11. For review/promotion changes, perform one end-to-end test:

   - open the regenerated local dashboard;
   - choose a disposable manual-review row;
   - fill Review Status, Reviewer Fit, one owner, one review checkbox, and notes;
   - save the row;
   - confirm the row appears in `opportunity_active` with `promoted_from_manual_review = true`;
   - delete the test review row and active-cache row before production rollout.

12. Commit intentional files only. Do not commit local Supabase temp files.

13. Merge to `main`.

14. Run the workflow from `main` if the dashboard should be deployed immediately rather than waiting for the next scheduled run.

15. Confirm the workflow runs successfully from `main`.

16. After the production run, check:
    - both monitor runs executed if this was a scheduled run;
    - the selected monitor ran if this was a manual workflow dispatch;
    - EM&V dashboard timestamp updated;
    - commissioning dashboard timestamp updated;
    - landing page links work;
    - opportunity digest email behavior is as expected;
    - `source-health.html` deployed and its navigation links work;
    - the monthly source-health email was sent only if this was the original first-Monday scheduled attempt and the previous reporting month contained at least one completed source-health run; otherwise, confirm the intentional skip in the workflow log;
    - no per-run source-health emails were sent;
    - one `source_health_runs` row and the expected `source_health_records` rows were persisted for each completed non-dry monitor run;
    - Supabase active cache updated by monitor type;
    - `opportunity_seen` rows are scoped correctly by monitor type;
    - review fields load and save;
    - manual-review promotion works;
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

### Monthly source-health email did not send

The monthly email is intentionally narrower than opportunity email delivery. Confirm all of the following:

- the event was the original scheduled workflow run, not `workflow_dispatch`;
- the run occurred on the first Monday in Eastern Time;
- `GITHUB_RUN_ATTEMPT` was `1` (reruns skip duplicate notification);
- the previous Eastern calendar month contains at least one completed `source_health_runs` row (empty reporting months are intentionally skipped);
- the Pages deployment completed successfully before the email steps;
- `SENDGRID_API_KEY`, `SUPABASE_URL`, and `SUPABASE_KEY` exist in GitHub Actions secrets;
- `SOURCE_HEALTH_EMAIL_TO` and `EMAIL_FROM` in `config.py` are correct;
- SendGrid accepted each message with HTTP `202`.

The workflow step is named:

```text
Send first-Monday source-health email
```

Manual runs do not send the monthly source-health email, even when the manual `send_email` input is `true`. That input controls opportunity digest delivery only.

### Dashboard did not deploy

First confirm that the workflow ran from `main` and was not a dry run. Feature branches create a downloadable dashboard preview but do not publish GitHub Pages, and dry runs do not create or publish dashboard files.

Next, inspect the two jobs separately:

- If **RFP Monitor Run** failed, troubleshoot the monitor before attempting a deployment.
- If **RFP Monitor Run** succeeded and its `rfp-dashboard-preview` artifact contains `index.html`, `emv.html`, `commissioning.html`, and `source-health.html`, the dashboard files are preserved. Do not rerun the monitors merely to publish those same files.
- If only **Deploy dashboard to GitHub Pages** failed or remained queued, check [GitHub Status](https://www.githubstatus.com/) for an Actions or Pages incident.

While GitHub reports an Actions or Pages incident, wait rather than repeatedly rerunning the workflow. Repeated attempts cannot correct a GitHub service outage and can add confusing failed runs. After both services return to operational, allow approximately ten minutes for GitHub to process its backlog.

To publish the preserved files after service recovery:

1. Open **Actions** -> **Redeploy Existing Dashboard** -> **Run workflow**.
2. Select the `main` branch.
3. Enter the numeric run ID of the successful **CxA RFP Monitor** run containing `rfp-dashboard-preview` as `source_run_id`.
4. Start one new recovery run and verify that all four dashboard files pass validation before deployment.

The recovery workflow only downloads, validates, and deploys the existing dashboard files. It does not scrape sources, write Supabase records, send opportunity emails, or send the monthly source-health email. If this failure occurs on the first Monday of a month, record that the monthly email was not sent and decide separately whether a follow-up notification is needed after the dashboard is available.

The regular and recovery workflows use run-and-attempt-specific Pages artifact names, so a failed-job retry will not collide with an earlier immutable artifact. The Pages action still stops after its documented ten-minute wait. If a deployment again remains queued for the full interval, recheck GitHub Status and wait before starting a new recovery run. Keep the original `rfp-dashboard-preview` artifact because it is the source for deployment-only recovery.

### Dashboard landing page is updated but one monitor page is stale

The landing page is regenerated whenever `generate_dashboard()` runs for either monitor. Each monitor dashboard is written only when that monitor run generates its dashboard.

On scheduled production runs, both monitor dashboards should be regenerated because the workflow runs both monitor types sequentially.

On manual workflow runs, only the selected monitor runs.

### Dashboard does not show an expected RFP

Check:

1. Did the source return candidates?
2. Did the item pass the scoring threshold for the selected monitor?
3. Is it in the manual-review section?
4. Is it suppressed in `manual_review_suppressed`?
5. Is it cached in `opportunity_active`?
6. Is `visible_until` still today or later?
7. Was it manually reviewed but not promoted because one of the promotion requirements is incomplete?

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

### Review fields do not load

Check that the generated dashboard points to the `opportunity-review` endpoint and that the Edge Function is deployed.

The dashboard should contain:

```text
https://udxcbyoohgzdkjxytxzg.functions.supabase.co/opportunity-review
```

Also confirm the review table exists:

```sql
select count(*) from public.opportunity_review_status;
```

### Review save returns Unauthorized

The entered token does not match the Supabase Edge Function secret:

```text
RFP_ADMIN_TOKEN
```

Clear browser localStorage or re-enter the correct token.

### Review save works but manual row does not promote

Check the promotion requirements:

1. Reviewer Fit must be filled and cannot be `Poor Fit`.
2. Review Status must be filled.
3. Technical Review Notes or Admin Review Notes must be filled.
4. At least one reviewed checkbox must be checked.
5. Tech Owner or Admin Owner must be filled.

A saved row with `Reviewer Fit = Poor Fit` is expected not to promote.

### Manual row promotes in browser but is not in `opportunity_active`

This usually means the browser applied the promotion based on a saved review record, but the Edge Function did not write the active-cache promotion.

Check:

```sql
select
  review_key,
  title,
  review_status,
  reviewer_fit,
  tech_owner,
  admin_owner,
  emv_technical_reviewed,
  commissioning_technical_reviewed,
  admin_reviewed,
  updated_at
from public.opportunity_review_status
where title ilike '%PASTE PART OF TITLE HERE%';
```

Then check:

```sql
select
  monitor_type,
  unique_key,
  source,
  title,
  opportunity ->> 'promoted_from_manual_review' as promoted_from_manual_review,
  opportunity ->> 'promotion_label' as promotion_label,
  last_seen
from public.opportunity_active
where
  title ilike '%PASTE PART OF TITLE HERE%'
  or opportunity ->> 'title' ilike '%PASTE PART OF TITLE HERE%';
```

If the review row exists but the active row does not, refresh the dashboard after confirming the latest `opportunity-review` function is deployed, then save the row again.

### X button returns Unauthorized

The entered token does not match the Supabase Edge Function secret:

```text
RFP_ADMIN_TOKEN
```

Clear browser localStorage or re-enter the correct token.

### X button returns Invalid API key or Supabase upsert failed

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
- the source did not change notice IDs/URLs for the same posting;
- the duplicate is not a stale row in `opportunity_active` from an old source name or old unique key.

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
| Source-health reporting | V1 now persists history, publishes a sanitized monthly dashboard, flags current errors/partial results and 12-run no-result streaks, and sends a first-Monday summary. A private raw-diagnostic drill-down is future work. |
| Repository and dashboard access | V1 retains the current public GitHub Pages delivery under the organization's free GitHub plan. V2 should move proprietary source code to a private repository and place internal review dashboards behind authenticated hosting. Evaluate private GitHub Pages under a qualifying organization plan or an alternate authenticated host, and audit public forks, workflow artifacts/logs, and exposed review fields before migration. |
| Vermont VSIGNS health code | Recent local runs show a DNS/name-resolution warning, but V1 records `HEALTH_WARN_ZERO` because the fetch helper returns an empty result. Future health tracking should distinguish fetch failure from true zero candidates. |
| NYISO Procurement | Current configured URL has returned 404. Need replacement URL or disable source. |
| National Grid | JavaScript-rendered; requires Playwright or alternate static/feed source. |
| Avangrid / United Illuminating | JavaScript-rendered; requires Playwright or alternate static/feed source. |
| Google CSE | Disabled in `main.py`; keep disabled unless an eligible working Google CSE project/API key is available. |
| Generic scrapers | Can collect old PDFs, informational pages, or broad procurement rows. Dedicated parsers and manual-review suppression help manage noise. |
| Source drift | Website redesigns may cause zero results without raising exceptions. The dashboard flags 12 consecutive no-result runs for investigation, but lower-level fetch helpers still need richer failure classification in V2. |
| BED detail pages | Detail pages may be Cloudflare-blocked. Parser uses listing links and may have limited scope/deadline text. |
| NYSCR detail links | Detail pages may require login. Parser uses public listing fields and stable CR numbers. |
| CT DEEP metadata | Search-result metadata may not expose due dates. |
| Local Supabase warnings | Expected when local shells do not define `SUPABASE_URL` and `SUPABASE_KEY`. |
| Committed automated tests | V1 used visible external deterministic checks. A committed automated unit/integration suite and CI test job are deferred to V2. |
| `datetime.utcnow()` deprecation warning | Newer Python versions may warn that `datetime.utcnow()` is deprecated. This warning is not currently breaking the workflow but should be cleaned up later with timezone-aware UTC datetimes. |
| GitHub Actions Node runtime notices | The August 2026 production deployment succeeded, but GitHub logged notices that some action internals were being moved from Node 20 to Node 24. Periodically review and update the pinned official action versions before GitHub removes compatibility fallbacks. Treat this as maintenance work, not evidence that the current workflow failed. |

---

## Notes for Future Developers

- Routine tuning should happen in `config.py` whenever possible.
- Use dedicated parsers for important sources when generic scraping creates false positives.
- Do not commit generated local dashboard files unless the dashboard output change is intentional.
- Dry runs are the safest way to test scrapers and scoring.
- Manual live local runs can send emails and update Supabase if credentials are set.
- The dashboard is static HTML and does not require a server.
- The active dashboard cache is managed by the Python workflow and Supabase.
- Review fields and manual promotion use the `opportunity-review` Supabase Edge Function.
- The manual-review X button uses the `suppress-manual-review` Supabase Edge Function.
- Secrets are not embedded in static HTML.
- V1 intentionally retains the current public Pages deployment. Private repository and authenticated dashboard hosting are documented V2 hardening work, not a completed V1 security control.
- Per-run source-health emails are retired. The first-Monday monthly email is sent only after the shared Pages dashboard deploys; completed non-dry monitor runs continue to persist history independently.
- The scheduled production workflow must run from `main` for GitHub Pages deployment.
