# CxA RFP Monitor

Automated scanner for EM&V (Evaluation, Measurement & Verification), program evaluation, energy efficiency, and related RFP opportunities across federal, state, utility, and quasi-public sources.

The monitor runs through GitHub Actions and produces two outputs:

1. **Email digest** through SendGrid.
2. **GitHub Pages dashboard** with a main opportunity table and a collapsed manual-review section.

The main table shows opportunities that pass the scoring threshold. The manual-review section shows filtered below-threshold opportunities that may still be worth occasional human review.

The dashboard also supports:

1. **Active opportunity persistence** through Supabase, so passing opportunities stay visible until their due date, or for 30 days if no due date is available.
2. **Manual suppression** of manual-review rows. Authorized users can click the X button on a manual-review item, enter the removal token, and permanently hide that item from future dashboard generations.

---

## What It Does

Each run:

1. Queries **SAM.gov** for federal opportunities matching configured keywords and NAICS codes.
2. Scrapes configured **utility and quasi-public sources**.
3. Scrapes configured **priority state portals**.
4. Scores all raw opportunities using tiered keyword matching.
5. Splits scored opportunities into:

   * passing opportunities,
   * below-threshold manual-review candidates,
   * all scored opportunities.
6. Loads the Supabase manual-review suppression table and removes suppressed manual-review rows.
7. Updates the Supabase active dashboard cache with passing opportunities.
8. Loads active cached dashboard opportunities that should remain visible.
9. Merges current passing opportunities with active cached opportunities.
10. Deduplicates passing opportunities against previously reported records in Supabase.
11. Sends an email digest for new passing opportunities when email delivery is enabled.
12. Generates a static GitHub Pages dashboard.

The important distinction is:

* **Email digest** is for newly identified passing opportunities only.
* **Dashboard** is an active opportunity board. Passing opportunities remain visible until their deadline, or for 30 days from first seen if no deadline is known.

---

## Current Status

| Feature / Source                   | Status             | Notes                                                                                                                                                                         |
| ---------------------------------- | ------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| SAM.gov federal scraping           | Working            | Requires `SAM_API_KEY`. Uses keyword and NAICS searches.                                                                                                                      |
| Supabase deduplication             | Working            | Uses `opportunity_seen` table. Controls whether opportunities are treated as new for email/reporting.                                                                         |
| Supabase active dashboard cache    | Working            | Uses `opportunity_active` table. Keeps passing opportunities visible until deadline, or for 30 days if no deadline exists.                                                    |
| Supabase manual-review suppression | Working            | Uses `manual_review_suppressed` table.                                                                                                                                        |
| Dashboard manual-review X button   | Working            | Calls Supabase Edge Function and writes suppression records.                                                                                                                  |
| Supabase Edge Function             | Working            | Function name: `suppress-manual-review`.                                                                                                                                      |
| Email digest via SendGrid          | Working            | Controlled by workflow input for manual runs. Scheduled production run sends the digest.                                                                                      |
| GitHub Pages dashboard             | Working            | Deploys from `main`; feature branches upload preview artifact only.                                                                                                           |
| Dashboard manual-review section    | Working            | Shows filtered below-threshold candidates.                                                                                                                                    |
| NASEO RFP Board                    | Working            | Dedicated parser targets open RFP/RFI section.                                                                                                                                |
| NEEP                               | Working            | Dedicated parser avoids informational false positives.                                                                                                                        |
| AESP Active RFPs                   | Working            | Dedicated parser for active RFP/RFQ/RFI listings; expired dated postings are filtered out.                                                                                    |
| Efficiency Maine                   | Working            | Dedicated parser skips closed/awarded/prequalified postings.                                                                                                                  |
| VEIC & Efficiency Vermont          | Working            | Uses VEIC RFP page. Efficiency Vermont direct page no longer lists open RFPs.                                                                                                 |
| Vermont DPS RFP page               | Working            | Dedicated parser and added as Vermont source.                                                                                                                                 |
| Vermont Business Registry          | Working            | Dedicated parser and added as broader Vermont fallback source.                                                                                                                |
| Massachusetts COMMBUYS             | Working            | Dedicated parser for current public open-bid HTML.                                                                                                                            |
| NYSERDA                            | Working            | Included as utility source and direct NY source. Can occasionally time out; run continues.                                                                                    |
| California CaleProcure             | Working            | Direct scrape source; now prefers a dedicated California Energy Commission contracts parser that filters inactive/expired CEC solicitations before falling back.                 |
| Green Mountain Power               | Working / noisy    | Generic source; some older PDFs may remain in manual review.                                                                                                                  |
| Mass Save / EEAC                   | Working            | Generic RFP source.                                                                                                                                                           |
| DOE EERE Funding Opportunities     | Working            | Broad national source.                                                                                                                                                        |
| ISO-NE Solicitations               | Working / noisy    | Generic source; some non-RFP links may fall into manual review.                                                                                                               |
| Entergy RFPs                       | Working            | Dedicated parser skips stale prior-year RFPs.                                                                                                                                 |
| Energy Trust of Oregon             | Working            | Dedicated parser for contracting opportunities; current RFQ/PER-style opportunities can score as high confidence when evaluation/research terms are present.                   |
| PG&E Energy Efficiency Solicitations | Working          | Dedicated parser for PG&E energy efficiency third-party solicitations.                                                                                                         |
| Cape Light Compact RFPs             | Working            | Dedicated parser reads current listing cards and filters non-current/closed listing noise.                                                                                     |
| Burlington Electric Department RFPs | Working / limited  | Dedicated parser monitors the stable BED listing page and keeps dynamic `/rfpdetail?rfp=...` links; detail pages may be Cloudflare-blocked, so items usually stay manual review. |
| NYS Contract Reporter               | Working / broad    | Dedicated parser extracts public listing fields from NYSCR text blocks, including CR number, issuer, issue date, due date, category, and ad type; detail pages may require login. |
| Connecticut DEEP RFP Search         | Working / filtered | Dedicated parser filters CT DEEP search results to energy/RFP-related items and removes pagination, older press releases, public-comment pages, parks/concession noise, and closed/no-award items. |
| NH Department of Energy RFPs        | Working            | Dedicated parser for NH Department of Energy/Public Utilities Commission RFP page and detail pages.                                                                            |
| Connecticut Energy Efficiency Board RFPs | Working       | Dedicated parser reads only open RFP/RFQ content from the CT Energy Efficiency Board page.                                                                                     |
| NYISO Procurement                  | Needs follow-up    | Current configured URL returns 404; left unchanged for now.                                                                                                                   |
| National Grid                      | Phase 2            | Skipped because source is JavaScript-rendered.                                                                                                                                |
| Avangrid / United Illuminating     | Phase 2            | Skipped because source is JavaScript-rendered.                                                                                                                                |
| Google CSE                         | Disabled / Phase 2 | Google Custom Search JSON API was blocked/closed for new customers. Google’s replacement option appears to be Vertex AI Search / Agent Builder, but it is not a free service. |
| EPA ENERGY STAR Solicitations      | Disabled           | No confirmed current replacement URL.                                                                                                                                         |
| PJM solicitations                  | Needs follow-up    | Configured URL returned 0 candidates and appears to be broken/not useful as a scrapeable RFP source.                                                                          |

---

## Repository Structure

```text
rfp-monitor/
├── main.py                                      # Orchestrator / entry point
├── config.py                                    # Keywords, sources, thresholds, email settings
├── models.py                                    # Opportunity dataclass and shared utilities
├── scorer.py                                    # Keyword scoring and manual-review filtering
├── dedup.py                                     # Supabase deduplication, active cache, and suppression filtering
├── delivery.py                                  # SendGrid email + GitHub Pages dashboard generator
├── requirements.txt                             # Python dependencies
├── docs/
│   └── index.html                               # Dashboard output file
├── scrapers/
│   ├── __init__.py
│   ├── sam_gov.py                               # SAM.gov federal API scraper
│   ├── web_sources.py                           # Utility/quasi-public and state portal scrapers
│   └── google_cse.py                            # Google CSE scraper, currently disabled in main.py
├── supabase/
│   └── functions/
│       └── suppress-manual-review/
│           └── index.ts                         # Edge Function used by dashboard X button
└── .github/
    └── workflows/
        └── rfp_monitor.yml                      # GitHub Actions workflow
```

---

## Main Run Flow

The full monitoring cycle is handled in `main.py`.

1. Parse workflow/CLI arguments.

2. Run selected scrapers.

3. Score all raw opportunities.

4. Filter below-threshold candidates for manual review.

5. Load manual-review suppressions from Supabase.

6. Remove suppressed manual-review candidates from the dashboard list.

7. Update/load active dashboard cache:

   * Upsert current passing opportunities into `opportunity_active`.
   * Set `visible_until` to the opportunity deadline if available.
   * If no deadline is available, set `visible_until` to 30 days after first seen.
   * Load all active cached opportunities where `visible_until` is today or later.
   * Merge current passing opportunities with active cached opportunities.

8. Load Supabase deduplication records.

9. Deduplicate current passing opportunities.

10. Send email digest if SendGrid is available/enabled.

11. Generate dashboard using the merged active dashboard opportunity list.

12. Save newly delivered opportunities to Supabase if at least one delivery channel succeeds.

The code is designed for partial success. One broken source should not stop the full run.

### No-Passing-Opportunity Behavior

If no opportunities pass the scoring threshold, the dashboard is still generated with active cached opportunities and manual-review candidates as long as raw opportunities were scraped and survived the manual-review cleanup filter.

If all scrapers return zero raw opportunities, the run generates an empty dashboard.

---

## Dashboard Persistence Behavior

The dashboard is intended to act as an active opportunity board, not just a list of items found in the most recent scrape.

Passing opportunities are cached in Supabase table `opportunity_active`.

Visibility rules:

| Opportunity Type | Dashboard Visibility Rule                    |
| ---------------- | -------------------------------------------- |
| Has deadline     | Remains visible through the deadline date.   |
| No deadline      | Remains visible for 30 days from first seen. |

This protects against source-page drift or temporary scraper misses. For example, if an RFP is scraped and scored once, but the source page temporarily stops listing it, the dashboard can still show it until its `visible_until` date.

The dashboard cache does **not** change email behavior. Email deduplication still uses `opportunity_seen`, so previously identified opportunities are not repeatedly emailed just because they remain visible on the dashboard.

---

## GitHub Actions Workflow

The workflow can run on schedule or manually.

Scheduled run:

```text
cron: "57 9 * * 1"
```

This is Monday at 9:57 UTC. Depending on daylight saving time, that is either 4:57 AM or 5:57 AM Eastern.

Manual runs are available from:

```text
GitHub → Actions → CxA RFP Monitor → Run workflow
```

### Manual Workflow Inputs

| Input        | Description                                                                                 |
| ------------ | ------------------------------------------------------------------------------------------- |
| `mode`       | Keyword mode: `broad` or `medium`.                                                          |
| `dry_run`    | If `true`, runs scrapers/scoring only and skips delivery/state update.                      |
| `sources`    | Source group to run: `sam`, `utilities`, `states_direct`, `google_cse`, or `all`.           |
| `force_all`  | If `true`, skips deduplication and reports all passing opportunities. Use carefully.        |
| `send_email` | If `true`, passes the SendGrid key and allows email delivery. If `false`, email is skipped. |

### Monitor Type

The GitHub Actions workflow sets:

```text
MONITOR_TYPE: emv
```

This scopes Supabase deduplication, active dashboard cache records, and manual-review suppressions to the EM&V monitor. The Supabase tables include a `monitor_type` column so future monitors, such as commissioning or RCx, can share the same tables without mixing records.

### Recommended Manual Test Settings

Dashboard and Supabase active-cache test without email:

```text
mode: broad
dry_run: false
sources: utilities
force_all: false
send_email: false
```

Full-source dashboard and active-cache test without email:

```text
mode: broad
dry_run: false
sources: all
force_all: false
send_email: false
```

Scrape/scoring-only test with no delivery or state update:

```text
mode: broad
dry_run: true
sources: utilities
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

### Scheduled Production Run

The scheduled Monday run is the real production behavior. It should:

1. Run from `main`.
2. Use GitHub Actions secrets.
3. Deduplicate using Supabase.
4. Update/load active dashboard opportunities from Supabase.
5. Send the email digest.
6. Regenerate and publish the live dashboard.
7. Save newly delivered opportunities to Supabase.

Do not manually run the production workflow unless you intentionally want to send an email digest.

---

## GitHub Pages Deployment Behavior

Dashboard generation and GitHub Pages deployment are separated:

* On **feature branches**, the workflow uploads a downloadable `rfp-dashboard-preview` artifact.
* On **main**, the workflow deploys the dashboard to GitHub Pages.
* This allows dashboard testing before merge without changing the live dashboard.

The dashboard output path is configured in `config.py`:

```python
DASHBOARD_OUTPUT_PATH = "docs/index.html"
```

The live dashboard is published at:

```text
https://cx-associates.github.io/rfp-monitor/
```

If the live dashboard does not show recent code changes, check the “Last updated” timestamp. The live dashboard only changes after the workflow regenerates and publishes `docs/index.html`.

---

## Email Delivery Behavior

Email is sent through SendGrid using `SENDGRID_API_KEY`.

For manual workflow runs:

* `send_email: false` leaves the SendGrid key unavailable to the Python process and skips email delivery.
* `send_email: true` allows email delivery if the secret and recipient configuration are valid.

Scheduled production runs are expected to send the digest.

Important distinction:

* `dry_run: false` allows real delivery/state behavior.
* `send_email: false` prevents email delivery for manual workflow runs.
* A useful dashboard/Supabase test usually uses `dry_run: false` and `send_email: false`.

Email settings are configured in `config.py`:

```python
SENDGRID_API_KEY_ENV = "SENDGRID_API_KEY"
EMAIL_FROM = "..."
EMAIL_TO = [...]
EMAIL_SUBJECT_PREFIX = "[CxA RFP Monitor]"
```

The sending address must be authorized/accepted by SendGrid.

Current recipients are controlled only by the `EMAIL_TO` list in `config.py`. As of the attached configuration, that list includes Riazul, Eric, Carrie, Liza, Rachael, and Matt.


---

## GitHub Secrets Required

These are configured under:

```text
GitHub repo → Settings → Secrets and variables → Actions
```

| Secret             | Purpose                                                                                                         |
| ------------------ | --------------------------------------------------------------------------------------------------------------- |
| `SAM_API_KEY`      | SAM.gov federal opportunities API.                                                                              |
| `SENDGRID_API_KEY` | SendGrid email delivery.                                                                                        |
| `SUPABASE_URL`     | Supabase project URL for deduplication, active dashboard cache, and suppression filtering during workflow runs. |
| `SUPABASE_KEY`     | Supabase service/API key used by Python Supabase logic.                                                         |
| `GOOGLE_CSE_KEY`   | Google Custom Search key; currently unused/disabled.                                                            |
| `GOOGLE_CSE_ID`    | Google Custom Search engine ID; currently unused/disabled.                                                      |

Important distinction:

* `SUPABASE_URL` and `SUPABASE_KEY` are **GitHub Actions secrets** used by the Python workflow.
* `RFP_ADMIN_TOKEN`, `RFP_SUPABASE_URL`, and `RFP_SUPABASE_SERVICE_ROLE_KEY` are **Supabase Edge Function secrets**, not GitHub secrets.

Do not commit any secret values to the repository.

---

## Supabase Tables

The monitor uses three Supabase tables:

1. `opportunity_seen`
2. `opportunity_active`
3. `manual_review_suppressed`

All are scoped by `monitor_type`, which defaults to:

```text
emv
```

This allows the same Supabase project to support future commissioning, RCx, or other monitors without key collisions.

### Table: `opportunity_seen`

This table stores delivered opportunities so future weekly runs do not resend the same RFP.

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

### Table: `opportunity_active`

This table stores passing opportunities that should remain visible on the dashboard.

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

Recommended verification query:

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

Expected behavior:

* Opportunities with deadlines should have `visible_until` equal to the deadline.
* Opportunities without deadlines should have `visible_until` equal to `first_seen + 30 days`.

### Table: `manual_review_suppressed`

This table stores manual-review rows hidden through the dashboard X button.

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

---

## Supabase Deduplication

Deduplication is handled in `dedup.py`.

On each non-dry run:

1. Load non-expired `opportunity_seen` rows from Supabase.
2. Compare current passing opportunities against the seen-set.
3. Treat unseen passing opportunities as new.
4. After successful delivery, save the new opportunities to `opportunity_seen`.

Each opportunity has a stable unique key based on source and notice ID. If a notice ID is not available, the code falls back to a source + URL hash.

Entries expire after the configured retention period:

```python
STATE_EXPIRY_DAYS = 180
```

If Supabase credentials are missing or unavailable, deduplication is skipped and all passing opportunities may appear as new for that run.

Local dry runs often show this warning unless you set Supabase variables locally:

```text
SUPABASE_URL or SUPABASE_KEY not set in environment. Deduplication will be skipped
```

That warning is expected for local shells without Supabase environment variables. The scheduled GitHub Actions run should use the GitHub secrets.

---

## Supabase Active Dashboard Cache

Active dashboard persistence is handled in `dedup.py`.

Main helper functions:

```python
upsert_active_dashboard_opportunities()
load_active_dashboard_opportunities()
merge_active_dashboard_opportunities()
```

On each non-dry run:

1. Current passing opportunities are upserted into `opportunity_active`.

2. Existing `first_seen` dates are preserved.

3. `last_seen` is updated to the current run date.

4. `visible_until` is calculated:

   * deadline date, if the opportunity has a deadline,
   * otherwise `first_seen + 30 days`.

5. Active rows are loaded where:

```sql
visible_until >= today
```

6. Current passing opportunities are merged with cached active opportunities.
7. Current versions win over cached versions if the same unique key appears in both lists.

This means a previously identified RFP can remain visible on the dashboard even if it is not scraped again in a later run, as long as it has not passed its `visible_until` date.

This cache is separate from the email seen-set. Keeping an opportunity visible on the dashboard does not cause repeat emails.

If Supabase credentials are missing or unavailable, the active dashboard cache is skipped and the dashboard falls back to the currently scored opportunities only.

---

## Manual-Review Suppression

The dashboard manual-review section includes an X button on each manual-review row.

Clicking the X button:

1. Prompts the user for the dashboard removal token if the browser does not already have one.
2. Sends a request to the Supabase Edge Function.
3. Writes a row to `manual_review_suppressed`.
4. Removes the row from the current page immediately.
5. Keeps the row hidden from future dashboard generations.

The token is not stored in the repository or the static HTML dashboard.

Browser behavior:

* The first X click prompts for the token.
* The token is stored in the user’s browser `localStorage` as `rfpAdminToken`.
* Future X clicks from that same browser should not prompt again unless localStorage is cleared or the token is rejected.

Security behavior:

* The dashboard sends the entered token as the request header `x-rfp-admin-token`.
* The Edge Function compares that value to its private `RFP_ADMIN_TOKEN` secret.
* If the token is wrong, the function returns `401 Unauthorized`.
* The service role key is only used server-side inside the Edge Function.
* Do not embed the token or service role key in `docs/index.html`.

---

## Supabase Edge Function

Function name:

```text
suppress-manual-review
```

Function path:

```text
supabase/functions/suppress-manual-review/index.ts
```

Endpoint:

```text
https://udxcbyoohgzdkjxytxzg.functions.supabase.co/suppress-manual-review
```

The function:

1. Accepts POST requests from the static dashboard.
2. Allows CORS and includes the custom `x-rfp-admin-token` header.
3. Rejects requests without the correct admin token.
4. Uses the server-side Supabase service role key.
5. Upserts into `manual_review_suppressed`.

### Supabase Edge Function Secrets

These are configured in Supabase, not GitHub:

```text
RFP_ADMIN_TOKEN
RFP_SUPABASE_URL
RFP_SUPABASE_SERVICE_ROLE_KEY
```

Set or update them with Supabase CLI:

```powershell
supabase secrets set RFP_ADMIN_TOKEN="$RfpAdminToken"
supabase secrets set RFP_SUPABASE_URL="https://udxcbyoohgzdkjxytxzg.supabase.co"
supabase secrets set RFP_SUPABASE_SERVICE_ROLE_KEY="your-service-role-key"
```

Deploy the function with JWT verification disabled because the dashboard uses the custom removal token instead of Supabase auth:

```powershell
supabase functions deploy suppress-manual-review --no-verify-jwt
```

Test behavior:

* `401 Unauthorized` means the entered dashboard removal token does not match `RFP_ADMIN_TOKEN`.
* `Invalid API key` means `RFP_SUPABASE_SERVICE_ROLE_KEY` is wrong.
* Successful response includes `"ok": true`.

---

## Keyword Scoring

Keyword scoring is defined in `config.py` and applied in `scorer.py`.

Two modes are available:

| Mode     | Behavior                                                                    |
| -------- | --------------------------------------------------------------------------- |
| `broad`  | Uses primary, secondary, and tertiary keywords. Wider net. Current default. |
| `medium` | Uses primary and secondary keywords only. Tighter EM&V focus.               |

Scoring tiers:

| Tier      | Example Focus                                                                                                                                                                                                                             | Points                  |
| --------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------- |
| Primary   | Core EM&V/M&V terminology, IPMVP, measurement and verification, savings verification, and EM&V/M&V services                                                                                                                               | Highest                 |
| Secondary | Program evaluation, impact evaluation, process evaluation, NTG, free ridership/spillover, deemed savings, custom measure evaluation, load impact, realization rate, claimed/reported/verified savings, and technical/project review terms | Medium                  |
| Tertiary  | Broader adjacent energy-efficiency, demand response, load research, benchmarking, commissioning/retrocommissioning, decarbonization, greenhouse-gas, audit, QA/QC, TRM, and cost-effectiveness terms                                      | Lowest; broad mode only |

Title matches receive an additional title bonus because titles are often the strongest available signal.

The keyword lists intentionally include common EM&V spelling and punctuation permutations, including:

```text
EM&V
EMV
M&V
MV
measurement and verification
measurement & verification
evaluation, measurement and verification
evaluation, measurement, and verification
evaluation measurement verification
```

The standalone keyword `energy` should remain commented out or excluded. It is too broad for the current scoring threshold because a single tertiary match can qualify an opportunity in broad mode, which would create substantial non-procurement and non-EM&V noise.

---

## Manual Review Candidates

The scoring criteria were not changed when the manual-review section was added.

The code separates results into:

1. Passing opportunities.
2. Below-threshold manual-review candidates.
3. All scored opportunities.

The dashboard displays passing and active cached opportunities in the main table. A filtered subset of below-threshold opportunities appears in the collapsed manual-review section.

Manual-review filtering removes obvious navigation/support links such as:

```text
skip to content
email-protection links
generic program pages
supporting-document-only links
non-procurement navigation links
```

Suppressed manual-review rows are removed before dashboard generation.

---

## Source Groups

Use the `--sources` argument locally or the `sources` workflow input in GitHub Actions.

| Source Group    | What it runs                                                         |
| --------------- | -------------------------------------------------------------------- |
| `sam`           | SAM.gov federal opportunities only.                                  |
| `utilities`     | Utility and quasi-public sources from `UTILITY_SOURCES`, plus NASEO. |
| `states_direct` | Priority direct state portal scrapers from `DIRECT_SCRAPE_STATES`.   |
| `google_cse`    | Currently disabled in `main.py`.                                     |
| `all`           | SAM.gov, utility/quasi-public sources, and direct state scrapes.     |

---

## Source-Specific Filters and Notes

### AESP Active RFPs

The AESP parser targets the “Active RFPs, RFQs, and RFIs” section.

AESP listings often appear as:

```text
Due: July 10, 2026 / Request for Proposal: ...
```

The parser extracts the due date from the heading and filters out expired postings. If a deadline exists and is before today, the opportunity is skipped before scoring.

This prevents stale postings such as expired utility non-wires alternative RFPs from appearing as new opportunities.

### Entergy RFPs

The Entergy parser skips stale prior-year RFPs based on the year in the title.

Example behavior:

```text
2025 ETI Demand Response RFP
```

will be skipped when the current year is later than 2025.

### Efficiency Maine

The Efficiency Maine parser skips closed, awarded, and prequalified postings.

### COMMBUYS

The COMMBUYS parser uses the current public open-bid HTML table layout and extracts:

* bid detail URL,
* issuer,
* contact,
* title,
* deadline,
* status.

If COMMBUYS changes its layout, this parser may need to be updated.

### Energy Trust of Oregon

The Energy Trust parser reads contracting opportunity listings and captures opportunities such as Planning, Evaluation and Research RFQs. These can score highly when the title or description includes evaluation/research terminology.

### PG&E Energy Efficiency Solicitations

The PG&E parser targets energy-efficiency solicitation listings and extracts solicitation title, issuer, URL, and source context for scoring.

### Cape Light Compact RFPs

The Cape Light parser reads current RFP/RFI listing cards and is intended to avoid stale or closed listing noise.

### Burlington Electric Department RFPs

The BED parser monitors the stable listing page:

```text
https://www.burlingtonelectric.com/rfp/
```

It keeps only dynamic detail links that match the pattern:

```text
/rfpdetail?rfp=...
```

This avoids navigation, contact, vendor, and footer links such as Contact Us, Email, Privacy Policy, and Contractor Application.

Important limitation: BED detail pages can be Cloudflare-blocked from direct `requests` access. Because of that, the parser does not rely on fetching the detail page. It uses the RFP number from the listing link and displays titles like:

```text
BED RFP 071-26
```

Since the accessible listing text may not include scope, title, or deadline detail, BED opportunities may not score into the main dashboard/email. Check the manual-review section for BED items.

### California CEC Contracts / CaleProcure

The `ca_eprocure` source now prefers a dedicated California Energy Commission contracts parser. This avoids the generic scraper pulling stale or inactive CEC solicitations.

The CEC parser:

* reads the public CEC contracts/solicitations page,
* skips inactive statuses such as awarded, closed, cancelled/canceled, expired, intent to award, notice of proposed award, no longer accepting, and not accepting,
* skips support-document links such as addenda, Q&A, question/answer, award notices, bid results, and tabulations,
* extracts submission deadlines where visible,
* skips opportunities with parsed deadlines before today.

If the CEC parser returns no active entries, the source can fall back to the generic CaleProcure page behavior.

### NYS Contract Reporter

The NYSCR parser reads the public search-result text blocks from:

```text
https://www.nyscr.ny.gov/Ads/Search
```

It extracts listing fields including:

* title,
* CR number,
* issuing agency or company,
* issue date,
* due date,
* category,
* ad type,
* note text when available.

The CR number is used as the stable internal notice ID, using the pattern:

```text
NYSCR-<CR number>
```

NYSCR detail links may require login. For that reason, the dashboard URL points to the NYSCR search page, while the deduplication key remains stable through the CR number.

Because NYSCR is broad and can return many construction/general procurement records, most NYSCR items are expected to remain below threshold unless their title or description matches EM&V/energy keywords.

### Connecticut DEEP RFP Search

The CT DEEP parser reads the public CT DEEP search results page for RFP-related results and applies source-specific filters.

The current parser keeps energy-related terms such as:

* energy efficiency,
* zero carbon,
* solar,
* wind,
* renewable,
* clean energy,
* grid,
* resilience,
* ratepayer,
* decarbonization.

It excludes common noise such as pagination controls, public-comment pages, draft RFP pages, “receives proposals,” “no award,” older year pages, parks/concession pages, paddlecraft, boat launch/marina, food and beverage, solid-waste/CSWSP pages, and addenda.

CT DEEP result pages often do not expose a clean due date in the search result. When no deadline is found, the dashboard may show `--` for deadline and the active cache will use the no-deadline persistence rule.

### NH Department of Energy RFPs

The NH Department of Energy parser is source-specific because RFP summary pages and detail pages have more useful context than generic anchor text alone. It targets current RFP records and extracts available title, deadline, issuer, and description context.

### Connecticut Energy Efficiency Board RFPs

The CT EEB parser reads the Energy Efficiency Board RFP/RFQ page and targets open opportunities rather than archived or informational content.

### NYISO Procurement

The currently configured NYISO procurement URL returns 404. This source needs a replacement URL or should be disabled if no reliable public solicitation page is identified.

### JavaScript-Rendered Sources

Sources marked `js_render=True` are skipped until a Phase 2 Playwright or alternate-feed implementation is added.

Currently skipped:

* National Grid
* Avangrid / United Illuminating

---

## Running Locally

Install dependencies:

```powershell
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

Avoid running a live local run unless you intentionally want local credentials to send email and update Supabase state.

---

## Useful Local Test Commands

Compile key files:

```powershell
python -m py_compile delivery.py main.py dedup.py scrapers/web_sources.py
```

Inspect AESP output:

```powershell
python -c "from scrapers.web_sources import fetch_utility_sources; xs=[o for o in fetch_utility_sources() if o.source=='AESP Active RFPs']; print(len(xs)); [print(o.title, '| deadline=', o.deadline, '| url=', o.url) for o in xs]"
```

Run utility dry run:

```powershell
python main.py --dry-run --sources utilities --mode broad
```

Test active dashboard cache locally if Supabase env vars are set:

```powershell
@'
from scrapers.web_sources import fetch_utility_sources
from scorer import score_split_and_sort
from dedup import (
    upsert_active_dashboard_opportunities,
    load_active_dashboard_opportunities,
    merge_active_dashboard_opportunities,
)

raw = fetch_utility_sources()
scored, manual_review, all_scored = score_split_and_sort(raw, mode="broad")

print("raw:", len(raw))
print("scored:", len(scored))

ok = upsert_active_dashboard_opportunities(scored)
print("active upsert ok:", ok)

active = load_active_dashboard_opportunities()
print("active loaded:", len(active))

merged = merge_active_dashboard_opportunities(scored, active)
print("dashboard merged:", len(merged))

for opp in merged[:15]:
    print(opp.title, "| deadline=", opp.deadline, "| source=", opp.source)
'@ | Set-Content .\test_active_dashboard_cache.py -Encoding UTF8

python .\test_active_dashboard_cache.py
Remove-Item .\test_active_dashboard_cache.py -ErrorAction SilentlyContinue
```

Check git status:

```powershell
git status
```

Show recent commits:

```powershell
git log --oneline -5
```

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

### Suppress a manual-review item from the dashboard

1. Open the dashboard.
2. Expand the manual-review section.
3. Click the X button on the item.
4. Enter the dashboard removal token.
5. Confirm the item disappears.
6. Confirm a row was added to Supabase table `manual_review_suppressed`.

### Un-suppress a manual-review item

Delete the row from Supabase:

```sql
delete from public.manual_review_suppressed
where monitor_type = 'emv'
  and unique_key = 'PASTE_UNIQUE_KEY_HERE';
```

The item may reappear on the next dashboard generation if it is still scraped and still qualifies for manual review.

### Inspect active dashboard opportunities

Use:

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

### Remove an active dashboard opportunity manually

Use only if an opportunity was cached incorrectly or should no longer appear before its visible-until date:

```sql
delete from public.opportunity_active
where monitor_type = 'emv'
  and unique_key = 'PASTE_UNIQUE_KEY_HERE';
```

---

## Known Issues / Future Work

| Item                                        | Status / Next Step                                                                                                                                                                         |
| ------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| NYISO Procurement                           | Current configured URL returns 404. Need replacement source or disable source.                                                                                                             |
| National Grid                               | JavaScript-rendered; requires Playwright or alternate static RFP feed.                                                                                                                     |
| Avangrid / United Illuminating              | JavaScript-rendered; requires Playwright or targeted static page if available.                                                                                                             |
| Google CSE                                  | Disabled because current Google project/API access is blocked. Re-enable only with an eligible API key/project.                                                                            |
| Generic scrapers                            | Can still collect old PDFs or broader informational pages. Manual-review section helps surface these without polluting the main table.                                                     |
| Source drift                                | Website redesigns may silently reduce candidates to zero. Active dashboard cache protects passing opportunities after first detection, but source-specific parsers still need maintenance. |
| PJM solicitations                           | Configured URL appears broken or no longer exposes a useful solicitation page. Disable or replace once a reliable static PJM RFP/procurement source is identified.                         |
| COMMBUYS noise                              | COMMBUYS can produce many below-threshold manual-review rows. Use suppression or source-specific filtering if it becomes too noisy.                                                        |
| BED RFP detail pages                         | BED detail URLs may be Cloudflare-blocked from direct scraping. The parser uses the listing page and dynamic `/rfpdetail?rfp=...` links only, so BED items may remain manual review due to limited text. |
| NYS Contract Reporter detail links          | NYSCR detail pages can require login. The parser extracts public listing fields and uses the CR number as the stable notice ID; dashboard links point back to the NYSCR search page.                   |
| CT DEEP search-result metadata              | CT DEEP search results may not expose due dates. The parser filters heavily for energy-related RFP pages, but deadline fields may remain blank.                                             |
| Local Supabase warning                      | Local dry runs may warn that `SUPABASE_URL` / `SUPABASE_KEY` are missing. This is expected unless those variables are set locally.                                                         |
| Deprecation warning for `datetime.utcnow()` | Python may warn that `datetime.utcnow()` is deprecated in newer versions. This is not currently breaking the workflow but can be cleaned up later with timezone-aware datetimes.           |

---

## Deployment Checklist

Before making the monitor fully live:

1. Confirm `EMAIL_FROM` and `EMAIL_TO` in `config.py`.

2. Confirm SendGrid sender/domain authentication.

3. Confirm GitHub Pages is set to deploy through GitHub Actions.

4. Confirm GitHub Actions secrets:

   * `SAM_API_KEY`
   * `SENDGRID_API_KEY`
   * `SUPABASE_URL`
   * `SUPABASE_KEY`

5. Confirm Supabase Edge Function secrets:

   * `RFP_ADMIN_TOKEN`
   * `RFP_SUPABASE_URL`
   * `RFP_SUPABASE_SERVICE_ROLE_KEY`

6. Confirm Supabase tables:

   * `opportunity_seen`
   * `opportunity_active`
   * `manual_review_suppressed`

7. Confirm scheduled Monday run is enabled.

8. Test feature-branch dashboard artifact before merge when code changes dashboard behavior.

9. Let the scheduled Monday run execute if validating schedule behavior.

10. After scheduled run completes, check:

* GitHub Actions run event is `schedule`.
* Dashboard timestamp updated.
* Email digest was sent.
* AESP expired opportunities were skipped.
* Entergy stale prior-year opportunities were skipped.
* Active opportunities were loaded from `opportunity_active`.
* Manual-review rows show X buttons.
* Suppressed rows stay hidden after the next dashboard generation.

---

## Troubleshooting

### Email did not send

Check the workflow log for:

```text
SENDGRID_API_KEY not set. Skipping email delivery.
```

For manual runs, make sure `send_email` was set to `true`.

Also check:

* `SENDGRID_API_KEY` exists in GitHub Actions secrets.
* `EMAIL_FROM` is authorized in SendGrid.
* Recipients are listed in `EMAIL_TO`.

### Dashboard did not deploy

Check whether the workflow was run from `main`.

Feature branches upload a preview artifact but do not deploy to GitHub Pages.

Also check the live dashboard timestamp. If it is old, the workflow has not regenerated and published the dashboard yet.

### Dashboard does not show an expected RFP

Check whether the RFP exists in the active cache:

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
  and title ilike '%PASTE PART OF TITLE HERE%';
```

If it is in `opportunity_active` and `visible_until` is today or later, it should appear in the dashboard after a successful dashboard-generation run.

If it is not in `opportunity_active`, the opportunity may not have passed scoring on any successful non-dry run after the active-cache feature was added.

### Dashboard shows an old RFP

Check the `visible_until` date in `opportunity_active`.

If it has a deadline, the row is expected to remain visible through the deadline.

If it has no deadline, the row is expected to remain visible for 30 days from `first_seen`.

If the row should be removed early, delete it manually from `opportunity_active`.

### X buttons are missing from the live dashboard

The live dashboard was probably generated before the X-button code was merged.

Check:

1. GitHub Actions has run successfully from `main` after the merge.
2. GitHub Pages deployment completed.
3. Browser cache has been refreshed.

Use:

```text
Ctrl + F5
```

### X button asks for a token

This is expected.

The dashboard is static/public HTML and cannot safely contain the token. The first authorized user action must enter the dashboard removal token. The browser stores it in localStorage for future X clicks.

### X button returns Unauthorized

The entered token does not match the Supabase Edge Function secret `RFP_ADMIN_TOKEN`.

Fix:

1. Confirm the correct token value.
2. Re-enter the token.
3. Clear browser localStorage if the browser saved an old token.
4. If necessary, reset `RFP_ADMIN_TOKEN` in Supabase and test again.

### X button returns Invalid API key

The Supabase Edge Function secret `RFP_SUPABASE_SERVICE_ROLE_KEY` is wrong or stale.

Fix the Supabase Function secret and redeploy/retest if needed.

### Active dashboard cache did not update

Check the workflow log for messages like:

```text
Active dashboard cache: upserted X passing opportunities
Active dashboard cache: loaded X active opportunities
Active dashboard merge: X current + Y cached = Z dashboard opportunities
```

If the log says Supabase is unavailable, confirm GitHub Actions secrets:

```text
SUPABASE_URL
SUPABASE_KEY
```

Also confirm the `opportunity_active` table exists and service-role grants were applied.

### Too many false positives

Options:

* Switch mode from `broad` to `medium`.
* Raise `MIN_SCORE_INCLUDE_BROAD`.
* Move broad terms from tertiary to commented-out.
* Add source-specific excludes in a dedicated parser.
* Keep broad mode but use the manual-review section for lower-confidence items.
* Suppress repeated manual-review noise with the dashboard X button.

### Real opportunities are missing

Options:

* Lower the relevant score threshold.
* Promote a keyword from tertiary to secondary or secondary to primary.
* Add missing source-specific terms.
* Inspect whether the source page changed and the scraper returned zero candidates.
* Add a dedicated parser for that source.
* Check whether the item is present in `opportunity_active` but has expired from the dashboard.

### Duplicate opportunities are appearing

Check:

* Supabase credentials are configured in GitHub Actions.
* `opportunity_seen` table exists.
* `save_seen_set()` succeeded after the prior run.
* `force_all` was not set to `true`.

### AESP expired opportunities appear

Check whether the AESP listing has a parseable `Due:` date. The current filter only skips items when a deadline is successfully parsed and the date is before today.

### Entergy stale-year opportunities appear

Check whether the posting title includes a recognizable year. The current stale filter is year-based.

---

## Notes for Future Developers

The code is intentionally organized so most routine tuning happens in `config.py`.

Use dedicated parsers for important sources when generic scraping creates false positives. The dedicated parser approach is currently used for several sources where page structure, deadline context, or closed/open status matters.

The dashboard is static HTML with client-side filtering. It does not require a server.

The active dashboard cache is managed server-side through the Python workflow and Supabase. It exists to keep valid RFPs visible until their due date or for 30 days if no due date is available.

The dashboard X button is implemented with client-side JavaScript calling a Supabase Edge Function. The token is never committed to the repository or embedded in the static dashboard.

The monitor is designed for partial success. One broken source should not stop the full run.

Do not commit generated local dashboard tests unless that is intentional. In normal operation, the GitHub Actions workflow regenerates `docs/index.html`.
