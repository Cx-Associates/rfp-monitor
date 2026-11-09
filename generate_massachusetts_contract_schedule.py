"""Generate the Massachusetts Statewide Contract Schedule page.

The page is an advance-planning aid separate from the live opportunity
dashboards. Supabase persistence, when explicitly requested, stores only
sanitized operational metadata about the check--not PDF rows or contacts.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

import requests
from pypdf import PdfReader

import config


SOURCE_URL = (
    "https://www.mass.gov/doc/statewide-contract-procurement-schedule/download"
)
USER_GUIDE_URL = "https://www.statewidecontractuserguide.mass.gov/"
OUTPUT_PATH = Path(config.MASSACHUSETTS_CONTRACT_SCHEDULE_OUTPUT_PATH)
RUNS_TABLE = "massachusetts_statewide_contract_schedule_runs"
PARSER_VERSION = "1"
SAFE_ERROR_SUMMARY = (
    "Schedule generation failed; review the corresponding GitHub Actions run."
)
RUN_CONTEXT: dict[str, object] = {}
STATUS_OUTPUT_PATH: Path | None = None

PERIOD_RE = re.compile(
    r"OSD\s+Procurement\s+Schedule\s+"
    r"(?P<start>\d{1,2}/\d{1,2}/\d{4})\s*"
    r"(?:-|\N{EN DASH}|\N{EM DASH})\s*"
    r"(?P<end>\d{1,2}/\d{1,2}/\d{4})",
    re.IGNORECASE,
)
DOCUMENT_NUMBER_RE = re.compile(r"\b[A-Z]{2,6}\d{2,3}(?:-[A-Z0-9]+)*\b")
COMMBUYS_BID_RE = re.compile(
    r"\bBD-\d{2}(?:-[A-Z0-9]+){4,}\b",
    re.IGNORECASE,
)
DATE_RE = re.compile(
    r"\b(?:January|February|March|April|May|June|July|August|September|"
    r"October|November|December)\s+\d{1,2},\s+\d{4}\b",
    re.IGNORECASE,
)
UPDATED_RE = re.compile(
    r"\bUpdated\s*:\s*([A-Z][a-z]+\s+\d{4}|\d{1,2}/\d{1,2}/\d{4})",
    re.IGNORECASE,
)
CURRENT_SECTION_END = (
    "Statewide Contracts Allowing Payments & Performance Beyond "
    "the Statewide Contract End Date"
)
USER_GUIDE_NOTE_RE = re.compile(
    r"To\s+learn\s+how\s+to\s+use\s+each\s+Statewide\s+Contract,\s*"
    r"refer\s+to\s+user\s+guide\s+for\s+detailed\s+description\.",
    re.IGNORECASE,
)

RELEVANCE_TERMS = (
    "building commissioning",
    "commissioning",
    "retro commissioning",
    "energy efficiency",
    "energy consulting",
    "energy services",
    "energy and environmental",
    "engineering, energy",
    "facilities engineering",
    "facility engineering",
    "sustainable facilities",
    "decarbonization",
    "environmental consulting",
    "professional environmental",
    "building energy",
)


@dataclass
class ScheduleItem:
    title: str
    document_number: str
    category: str
    schedule_date: str
    contact: str
    comment: str
    action: str = "Other schedule update"
    commbuys_ids: list[str] = field(default_factory=list)
    relevance_matches: list[str] = field(default_factory=list)
    source_page: int = 0
    source_order: int = 0


def compact(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def normalize_for_matching(value: str) -> str:
    """Normalize PDF glyphs only for internal matching, never for display."""
    normalized = (value or "").translate(
        str.maketrans(
            {
                "\ufb00": "ff",
                "\ufb01": "fi",
                "\ufb02": "fl",
                "\ufb03": "ffi",
                "\ufb04": "ffl",
            }
        )
    )
    return compact(normalized).lower()


def esc(value: object) -> str:
    return html.escape(str(value or ""), quote=True)


def repair_pdf_identifiers(value: str) -> str:
    """Join PDF-wrapped identifier components without joining ordinary prose."""
    repaired = value or ""
    repaired = re.sub(r"-\s+(?=[A-Z0-9])", "-", repaired)
    repaired = re.sub(r"(?<=BD)-\s*", "-", repaired, flags=re.IGNORECASE)
    return compact(repaired)


def classify_action(item: ScheduleItem) -> str:
    text = normalize_for_matching(
        f"{item.title} {item.category} {item.comment}"
    )
    if re.search(r"bid submissions? (?:are|is) being accepted|responses? due", text):
        return "Open/active bid referenced"
    if item.commbuys_ids and re.search(r"bid|solicitation|request for response|rfr", text):
        return "Bid/solicitation referenced"
    if re.search(r"\breopen(?:ed|ing)?\b", text):
        return "Contract reopening"
    if re.search(r"\breplac(?:e|ed|ement|ing)\b|new statewide contract", text):
        return "Replacement/new contract"
    if re.search(r"\bextend(?:ed|ing|s)?\b", text):
        return "Contract extension"
    if re.search(r"\brenew(?:ed|al|ing)?\b", text):
        return "Contract renewal"
    if re.search(r"\bexpir(?:e|ed|ation|ing)\b", text):
        return "Contract expiration"
    if re.search(r"will begin|effective date|start date", text):
        return "Contract start/update"
    return "Other schedule update"


def find_column_starts(layout_pages: list[str]) -> list[int]:
    """Infer column boundaries from the centers of the six visual headers."""
    terms = (
        "Contract Title",
        "Statewide Contract",
        "Contract Category",
        "Contract End Date or",
        "Contact Person/E-mail",
        "Comment",
    )

    header_lines: list[str] = []
    for page in layout_pages:
        for line in page.splitlines():
            lowered = line.lower()
            if all(term.lower() in lowered for term in terms):
                header_lines.append(line)

    if not header_lines:
        raise RuntimeError("No complete six-column visual table header was found.")

    header = header_lines[0]
    centers = [
        header.lower().find(term.lower()) + (len(term) / 2)
        for term in terms
    ]
    starts = [0]
    starts.extend(
        round((left + right) / 2)
        for left, right in zip(centers, centers[1:])
    )

    if starts != sorted(starts) or len(set(starts)) != 6:
        raise RuntimeError(f"Detected invalid PDF column starts: {starts}")
    return starts


def split_columns(line: str, starts: list[int]) -> list[str]:
    cells: list[str] = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else None
        cells.append(line[start:end].strip())
    return cells


def is_header_or_footer(cells: list[str], full_line: str) -> bool:
    lowered = full_line.lower()
    return any(
        marker in lowered
        for marker in (
            "contract title",
            "statewide contract document",
            "contract category",
            "contact person/e-mail",
            "commonwealth of massachusetts",
            "to learn how to use each statewide contract",
            "osd procurement schedule",
        )
    ) or compact(" ".join(cells)).lower() == "statewide contract"


def parse_layout_page(layout_text: str, starts: list[int]) -> list[ScheduleItem]:
    """Parse one visual table page using its preserved column alignment."""
    lines = layout_text.splitlines()
    for index, line in enumerate(lines):
        if "statewide contracts allowing payments" in compact(line).lower():
            lines = lines[:index]
            break
    sliced = [split_columns(line, starts) for line in lines]

    anchor_indexes: list[tuple[int, str]] = []
    for index, cells in enumerate(sliced):
        if is_header_or_footer(cells, lines[index]):
            continue
        document_cell = compact(cells[1])
        matches = DOCUMENT_NUMBER_RE.findall(document_cell)
        if matches and not document_cell.upper().startswith("BD-"):
            anchor_indexes.append((index, matches[0]))

    items: list[ScheduleItem] = []
    for position, (start_index, document_number) in enumerate(anchor_indexes):
        end_index = (
            anchor_indexes[position + 1][0]
            if position + 1 < len(anchor_indexes)
            else len(lines)
        )

        block_cells = [[], [], [], [], [], []]
        for line_index in range(start_index, end_index):
            cells = sliced[line_index]
            if is_header_or_footer(cells, lines[line_index]):
                continue
            for column, cell in enumerate(cells):
                cleaned = compact(cell)
                if cleaned:
                    block_cells[column].append(cleaned)

        joined = [compact(" ".join(values)) for values in block_cells]
        joined[1] = document_number

        # If the title wrapped immediately above the row's document-number
        # anchor, recover title-only lines that belong to this visual row.
        title = joined[0]
        if not title and start_index > 0:
            previous_cells = sliced[start_index - 1]
            if previous_cells[0].strip() and not any(
                cell.strip() for cell in previous_cells[1:]
            ):
                title = compact(previous_cells[0])

        combined = repair_pdf_identifiers(" ".join(joined))
        commbuys_ids = sorted({value.upper() for value in COMMBUYS_BID_RE.findall(combined)})
        date_match = DATE_RE.search(joined[3])
        schedule_date = date_match.group(0) if date_match else joined[3]

        item = ScheduleItem(
            title=title,
            document_number=document_number,
            category=joined[2],
            schedule_date=schedule_date,
            contact=joined[4],
            comment=repair_pdf_identifiers(joined[5]),
            commbuys_ids=commbuys_ids,
        )
        relevance_text = normalize_for_matching(
            f"{item.title} {item.category} {item.comment}"
        )
        item.relevance_matches = [
            term for term in RELEVANCE_TERMS if term in relevance_text
        ]
        item.action = classify_action(item)
        items.append(item)

    return items


def render_html(
    *,
    items: list[ScheduleItem],
    period_start: str,
    period_end: str,
    update_label: str,
    user_guide_note: str,
    content_sha256: str,
    content_bytes: int,
    page_count: int,
) -> str:
    relevant_count = sum(bool(item.relevance_matches) for item in items)
    bid_count = sum(bool(item.commbuys_ids) for item in items)
    generated_at = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
    linked_user_guide_note, link_count = re.subn(
        r"\buser guide\b",
        (
            f'<a href="{esc(USER_GUIDE_URL)}" target="_blank" '
            'rel="noopener">user guide</a>'
        ),
        esc(user_guide_note),
        count=1,
        flags=re.IGNORECASE,
    )
    if link_count != 1:
        raise RuntimeError("The PDF user-guide note does not contain 'user guide'.")

    def linked_comment(item: ScheduleItem) -> str:
        rendered = esc(item.comment)
        for bid_id in item.commbuys_ids:
            url = (
                "https://www.commbuys.com/bso/external/bidDetail.sda?docId="
                f"{bid_id}&external=true&parentUrl=close"
            )
            rendered = rendered.replace(
                esc(bid_id),
                f'<a href="{esc(url)}" target="_blank" rel="noopener">'
                f"{esc(bid_id)}</a>",
            )
        return rendered

    rows = []
    for item in items:
        relevance = (
            '<span class="badge relevant">Potential internal relevance</span><br>'
            + esc(", ".join(item.relevance_matches))
            if item.relevance_matches
            else '<span class="badge neutral">No automatic relevance flag</span>'
        )
        bid_links = []
        for bid_id in item.commbuys_ids:
            url = (
                "https://www.commbuys.com/bso/external/bidDetail.sda?docId="
                f"{bid_id}&external=true&parentUrl=close"
            )
            bid_links.append(
                f'<a href="{esc(url)}" target="_blank" rel="noopener">'
                f"{esc(bid_id)}</a>"
            )

        rows.append(
            "<tr>"
            f"<td><strong>{esc(item.title)}</strong></td>"
            f"<td><code>{esc(item.document_number)}</code></td>"
            f"<td>{esc(item.category)}</td>"
            f"<td>{esc(item.schedule_date)}</td>"
            f"<td>{esc(item.contact)}</td>"
            f"<td>{linked_comment(item)}</td>"
            f"<td class=\"aid\">{'<br>'.join(bid_links) if bid_links else '&mdash;'}</td>"
            f"<td class=\"aid\"><span class=\"badge action\">{esc(item.action)}</span></td>"
            f"<td class=\"aid\">{relevance}</td>"
            "</tr>"
        )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Massachusetts Statewide Contract Schedule</title>
  <style>
    *{{box-sizing:border-box}}body{{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif;background:#f3f5f7;color:#222}}
    header{{background:#1a1a2e;color:white;padding:24px 30px}}header h1{{margin:0 0 7px;font-size:24px}}header p{{margin:4px 0;color:#d7d8e3;font-size:13px}}
    .dash-nav{{display:flex;gap:8px;flex-wrap:wrap;padding:12px 28px;background:#fff;border-bottom:1px solid #e3e6ea}}.dash-nav a{{display:inline-block;padding:7px 12px;border:1px solid #d7dbe0;border-radius:6px;background:#fff;color:#1a1a2e;font-size:13px;font-weight:600;text-decoration:none}}.dash-nav a:hover{{background:#f3f5f8}}.dash-nav a.active{{background:#1a1a2e;color:#fff;border-color:#1a1a2e}}
    .notice{{margin:20px 28px;padding:14px 16px;background:#fff8db;border-left:5px solid #d89b00;border-radius:5px;line-height:1.45}}
    .cards{{display:flex;gap:12px;flex-wrap:wrap;margin:20px 28px}}.card{{background:white;padding:16px 20px;border-radius:8px;box-shadow:0 1px 4px #0002;min-width:155px}}
    .card strong{{display:block;font-size:25px;color:#1a1a2e}}.card span{{font-size:12px;color:#666}}
    .wrap{{margin:20px 28px 34px;background:white;border-radius:8px;overflow:hidden;box-shadow:0 1px 4px #0002}}
    table{{border:1px solid #000;border-collapse:collapse;width:100%;table-layout:fixed}}th{{background:#1a1a2e;color:white;border:1px solid #000;text-align:left;padding:9px 7px;font-size:11px;vertical-align:top;overflow-wrap:anywhere}}td{{padding:9px 7px;vertical-align:top;border:1px solid #000;font-size:11px;line-height:1.4;overflow-wrap:anywhere}}tr:hover td{{background:#f8fbff}}
    th.aid{{background:#fff0db;color:#613600}}td.aid{{background:#fffaf3}}tr:hover td.aid{{background:#fff4e6}}
    .group-heading th{{text-align:center;text-transform:uppercase;letter-spacing:.05em;font-size:10px;padding:7px}}.group-heading .source-group{{background:#1a1a2e}}.group-heading .aid-group{{background:#ffe6c2;color:#613600}}
    .badge{{display:inline-block;padding:3px 7px;border-radius:10px;font-size:10px;font-weight:700;margin-bottom:4px}}.relevant{{background:#dff4e5;color:#176b31}}.neutral{{background:#f1ede7;color:#666}}.action{{background:#fff0d8;color:#7a4300}}
    a{{color:#0667c7}}code{{white-space:nowrap}}footer{{padding:0 28px 30px;font-size:12px;color:#666;line-height:1.5}}
    .source-note{{margin:0 28px 20px;padding:14px 16px;background:#fff;border-left:5px solid #1a1a2e;border-radius:5px;line-height:1.55}}
  </style>
</head>
<body>
  <header>
    <h1>Massachusetts Statewide Contract Schedule</h1>
    <p>Advance-planning schedule &mdash; schedule period {esc(period_start)} through {esc(period_end)}</p>
    <p>Document update marker: {esc(update_label or 'not detected')} &mdash; Generated {esc(generated_at)}</p>
  </header>
  <nav class="dash-nav" aria-label="Dashboard navigation">
    <a href="index.html">Home</a>
    <a href="emv.html">EM&amp;V Dashboard</a>
    <a href="commissioning.html">Commissioning / RCx Dashboard</a>
    <a href="source-health.html">Source Health Dashboard</a>
    <a class="active" href="massachusetts-contract-schedule.html">Massachusetts Contract Schedule</a>
  </nav>
  <div class="notice"><strong>Advance-planning schedule.</strong> This page is separate from the live opportunity dashboards and does not create opportunity records or list schedule entries in email digests. Rows are ordered by potential internal relevance, while the source table headers and content retain the PDF's terminology.</div>
  <div class="cards">
    <div class="card"><strong>{len(items)}</strong><span>schedule entries</span></div>
    <div class="card"><strong>{relevant_count}</strong><span>potentially relevant entries</span></div>
    <div class="card"><strong>{bid_count}</strong><span>rows with COMMBUYS IDs</span></div>
    <div class="card"><strong>{page_count}</strong><span>PDF pages</span></div>
  </div>
  <div class="wrap">
    <table>
      <colgroup><col style="width:13%"><col style="width:9%"><col style="width:11%"><col style="width:11%"><col style="width:9%"><col style="width:22%"><col style="width:9%"><col style="width:8%"><col style="width:8%"></colgroup>
      <thead>
        <tr class="group-heading"><th class="source-group" colspan="6">Massachusetts source fields</th><th class="aid-group" colspan="3">Internal Planning Aids</th></tr>
        <tr><th>Contract Title</th><th>Statewide Contract Document Number</th><th>Contract Category</th><th>Contract End Date or Solicitation Start Date</th><th>Contact Person/E-mail</th><th>Comment</th><th class="aid">COMMBUYS Bid</th><th class="aid">Planning Action</th><th class="aid">Internal Relevance</th></tr>
      </thead>
      <tbody>{''.join(rows)}</tbody>
    </table>
  </div>
  <div class="source-note">
    <strong>Updated: {esc(update_label or 'not detected')}</strong><br>
    {linked_user_guide_note}
  </div>
  <footer>
    <div><a href="{esc(SOURCE_URL)}" target="_blank" rel="noopener">Open the official Mass.gov schedule PDF</a></div>
    <div>Operational check metadata is retained server-side; PDF rows and contact details are not written to Supabase.</div>
  </footer>
</body>
</html>"""



def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate the Massachusetts Statewide Contract Schedule page."
    )
    parser.add_argument(
        "--output",
        default=str(OUTPUT_PATH),
        help="Static HTML output path.",
    )
    parser.add_argument(
        "--persist",
        action="store_true",
        help=(
            "Record sanitized operational check metadata in Supabase. "
            "Local runs omit this flag."
        ),
    )
    parser.add_argument(
        "--status-file",
        help=(
            "Optional file receiving one digest status: new, none, or "
            "not_checked."
        ),
    )
    return parser.parse_args()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _database_date(value: object) -> str | None:
    if not value:
        return None
    return datetime.strptime(str(value), "%m/%d/%Y").date().isoformat()


def _relevant_row_fingerprints(items: list[ScheduleItem]) -> list[str]:
    """Hash stable relevant-opportunity identities without storing source text."""
    fingerprints = []
    for item in items:
        if not item.relevance_matches:
            continue
        identity = "\x1f".join(
            (
                compact(item.document_number).lower(),
                compact(item.title).lower(),
            )
        )
        fingerprints.append(hashlib.sha256(identity.encode("utf-8")).hexdigest())
    return sorted(set(fingerprints))


def _load_previous_relevant_fingerprints(client) -> set[str] | None:
    """Return the most recent successful fingerprint set, or None on failure."""
    if client is None:
        return None
    try:
        response = (
            client.table(RUNS_TABLE)
            .select("relevant_row_fingerprints")
            .eq("status", "ok")
            .order("checked_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = getattr(response, "data", None)
        if not isinstance(rows, list):
            raise RuntimeError("Supabase returned an invalid history payload.")
        if not rows:
            return set()
        values = rows[0].get("relevant_row_fingerprints") or []
        if not isinstance(values, list):
            raise RuntimeError("Stored relevant-row fingerprints are invalid.")
        return {str(value) for value in values}
    except Exception as exc:
        print(
            "WARNING: Could not compare the Massachusetts schedule with the "
            f"previous successful check: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return None


def _write_digest_status(status: str) -> None:
    if STATUS_OUTPUT_PATH is None:
        return
    STATUS_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATUS_OUTPUT_PATH.write_text(status + "\n", encoding="utf-8")


def _get_supabase_client():
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_KEY", "").strip()
    if not url or not key:
        print(
            "WARNING: SUPABASE_URL or SUPABASE_KEY is unavailable; "
            "Massachusetts schedule check history was not persisted.",
            file=sys.stderr,
        )
        return None
    try:
        from supabase import create_client
        return create_client(url, key)
    except Exception as exc:
        print(
            "WARNING: Could not create the Supabase client for Massachusetts "
            f"schedule history: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return None


def persist_schedule_check(
    *,
    status: str,
    context: dict[str, object],
    error: Exception | None = None,
    client=None,
) -> bool:
    """Insert one sanitized operational check row; never mutate prior rows."""
    client = client or _get_supabase_client()
    if client is None:
        return False

    row = {
        "status": status,
        "parser_version": PARSER_VERSION,
        "github_run_id": os.environ.get("GITHUB_RUN_ID"),
        "github_run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
        "github_workflow": os.environ.get("GITHUB_WORKFLOW"),
        "github_event_name": os.environ.get("GITHUB_EVENT_NAME"),
        "checked_at": context.get("checked_at") or _utc_now_iso(),
        "completed_at": _utc_now_iso(),
        "source_url": SOURCE_URL,
        "final_url": context.get("final_url"),
        "http_status": context.get("http_status"),
        "content_type": context.get("content_type"),
        "content_bytes": context.get("content_bytes"),
        "content_sha256": context.get("content_sha256"),
        "pdf_page_count": context.get("pdf_page_count"),
        "schedule_period_start": _database_date(context.get("period_start")),
        "schedule_period_end": _database_date(context.get("period_end")),
        "source_document_updated": context.get("update_label"),
        "structured_row_count": context.get("structured_row_count"),
        "potentially_relevant_row_count": context.get("relevant_row_count"),
        "commbuys_row_count": context.get("commbuys_row_count"),
        "repeated_document_number_count": context.get(
            "repeated_document_number_count"
        ),
        "digest_status": context.get("digest_status"),
        "relevant_row_fingerprints": context.get("relevant_row_fingerprints") or [],
        "new_relevant_row_count": context.get("new_relevant_row_count"),
        "error_type": type(error).__name__ if error else None,
        # Never copy an exception string into operational history. Parser
        # exceptions can contain source-row or contact text; detailed errors
        # remain available in the corresponding GitHub Actions log.
        "error_message": SAFE_ERROR_SUMMARY if error else None,
    }
    try:
        client.table(RUNS_TABLE).insert(row).execute()
        print(f"Supabase operational history: recorded {status} check")
        return True
    except Exception as exc:
        print(
            "WARNING: Massachusetts schedule check history could not be "
            f"persisted: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return False


def main() -> int:
    global STATUS_OUTPUT_PATH
    args = _parse_args()
    STATUS_OUTPUT_PATH = Path(args.status_file) if args.status_file else None
    RUN_CONTEXT.clear()
    RUN_CONTEXT.update({"checked_at": _utc_now_iso(), "source_url": SOURCE_URL})
    print("MASSACHUSETTS STATEWIDE CONTRACT SCHEDULE GENERATOR")
    print("Anonymous public PDF read and static HTML generation.")
    print(
        "Supabase operational history: "
        + ("requested" if args.persist else "not requested")
        + "\n"
    )

    response = requests.get(
        SOURCE_URL,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (compatible; CxA-RFP-Monitor-Preview/1.0; "
                "+https://github.com/Cx-Associates/rfp-monitor)"
            ),
            "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.8",
        },
        timeout=60,
        allow_redirects=True,
    )
    RUN_CONTEXT.update(
        {
            "final_url": response.url,
            "http_status": response.status_code,
            "content_type": response.headers.get("Content-Type", ""),
        }
    )
    response.raise_for_status()
    if not response.content.startswith(b"%PDF"):
        raise RuntimeError("Mass.gov response does not begin with a PDF signature.")

    content_sha256 = hashlib.sha256(response.content).hexdigest()
    reader = PdfReader(BytesIO(response.content))
    RUN_CONTEXT.update(
        {
            "content_bytes": len(response.content),
            "content_sha256": content_sha256,
            "pdf_page_count": len(reader.pages),
        }
    )
    all_plain_pages = [page.extract_text() or "" for page in reader.pages]
    all_layout_pages = [
        page.extract_text(extraction_mode="layout") or ""
        for page in reader.pages
    ]
    full_plain_text = "\n".join(all_plain_pages)

    period_match = PERIOD_RE.search(full_plain_text)
    if not period_match:
        raise RuntimeError("No current OSD Procurement Schedule period was found.")
    period_start = period_match.group("start")
    period_end = period_match.group("end")
    RUN_CONTEXT.update(
        {"period_start": period_start, "period_end": period_end}
    )

    current_start_page = next(
        index
        for index, text in enumerate(all_plain_pages)
        if PERIOD_RE.search(text)
    )
    current_end_page = len(all_plain_pages)
    for index in range(current_start_page + 1, len(all_plain_pages)):
        later_match = PERIOD_RE.search(all_plain_pages[index])
        if later_match and (
            later_match.group("start"), later_match.group("end")
        ) != (period_start, period_end):
            current_end_page = index
            break

    plain_pages = all_plain_pages[current_start_page:current_end_page]
    layout_pages = all_layout_pages[current_start_page:current_end_page]
    current_plain_text = "\n".join(plain_pages)

    update_match = UPDATED_RE.search(current_plain_text)
    update_label = compact(update_match.group(1)) if update_match else ""
    user_guide_match = USER_GUIDE_NOTE_RE.search(current_plain_text)
    if not user_guide_match:
        raise RuntimeError("The PDF's Statewide Contract user-guide note was not found.")
    user_guide_note = compact(user_guide_match.group(0))
    RUN_CONTEXT["update_label"] = update_label

    items: list[ScheduleItem] = []
    for page_number, layout_text in enumerate(layout_pages, start=1):
        page_starts = find_column_starts([layout_text])
        print(f"page {page_number} visual column starts: {page_starts}")
        page_items = parse_layout_page(layout_text, page_starts)
        for item in page_items:
            item.source_page = page_number
        print(f"page {page_number}: {len(page_items)} structured rows")
        items.extend(page_items)

    unique_items: list[ScheduleItem] = []
    seen_row_keys: set[tuple[str, str, str, str]] = set()
    rows_by_document_number: dict[str, list[ScheduleItem]] = {}
    for item in items:
        row_key = (
            item.document_number,
            compact(item.title).lower(),
            compact(item.schedule_date).lower(),
            compact(item.comment).lower(),
        )
        if row_key in seen_row_keys:
            raise RuntimeError(
                "Exact duplicate schedule row parsed: "
                f"{item.document_number} / {item.title} / {item.schedule_date}"
            )
        seen_row_keys.add(row_key)
        rows_by_document_number.setdefault(item.document_number, []).append(item)
        unique_items.append(item)
    items = unique_items

    repeated_document_numbers = {
        document_number: rows
        for document_number, rows in rows_by_document_number.items()
        if len(rows) > 1
    }
    if repeated_document_numbers:
        print("distinct rows sharing a contract document number:")
        for document_number, rows in repeated_document_numbers.items():
            print(f"  {document_number}: {len(rows)} rows")
            for row in rows:
                print(
                    f"    page {row.source_page}: {row.title} | "
                    f"{row.schedule_date} | {row.action}"
                )

    if len(items) < 8:
        print("\nPDF LAYOUT DIAGNOSTIC (first current-schedule page)")
        print("The numbered excerpt below preserves spaces used for table alignment.")
        print("         1         2         3         4         5         6         7         8         9        10")
        print("1234567890123456789012345678901234567890123456789012345678901234567890123456789012345678901234567890")
        for line_number, line in enumerate(layout_pages[0].splitlines()[:90], start=1):
            print(f"{line_number:03d}|{line}")
        raise RuntimeError(
            f"Only {len(items)} structured rows were parsed; refusing to write a misleading preview."
        )

    incomplete = [
        item.document_number
        for item in items
        if not item.title or not item.category or not item.comment
    ]
    if incomplete:
        raise RuntimeError(
            "Required row fields were missing for: " + ", ".join(incomplete)
        )

    invalid_dates = [
        f"{item.document_number}={item.schedule_date!r}"
        for item in items
        if not DATE_RE.fullmatch(compact(item.schedule_date))
    ]
    if invalid_dates:
        raise RuntimeError(
            "Incomplete or malformed schedule dates were parsed: "
            + ", ".join(invalid_dates)
        )

    invalid_contacts = [
        f"{item.document_number}={item.contact!r}"
        for item in items
        if re.match(r"^\d{2,4}\b", compact(item.contact))
    ]
    if invalid_contacts:
        raise RuntimeError(
            "Contact fields contain likely date fragments: "
            + ", ".join(invalid_contacts)
        )

    for source_order, item in enumerate(items):
        item.source_order = source_order
    items.sort(key=lambda item: (-len(item.relevance_matches), item.source_order))
    RUN_CONTEXT.update(
        {
            "structured_row_count": len(items),
            "relevant_row_count": sum(
                bool(item.relevance_matches) for item in items
            ),
            "commbuys_row_count": sum(bool(item.commbuys_ids) for item in items),
            "repeated_document_number_count": len(repeated_document_numbers),
        }
    )
    current_fingerprints = _relevant_row_fingerprints(items)
    history_client = None
    if args.persist:
        history_client = _get_supabase_client()
        previous_fingerprints = _load_previous_relevant_fingerprints(
            history_client
        )
        if previous_fingerprints is None:
            digest_status = "not_checked"
            new_relevant_row_count = None
        else:
            new_fingerprints = set(current_fingerprints) - previous_fingerprints
            digest_status = "new" if new_fingerprints else "none"
            new_relevant_row_count = len(new_fingerprints)
    else:
        digest_status = "new" if current_fingerprints else "none"
        new_relevant_row_count = len(current_fingerprints)
    RUN_CONTEXT.update(
        {
            "digest_status": digest_status,
            "relevant_row_fingerprints": current_fingerprints,
            "new_relevant_row_count": new_relevant_row_count,
        }
    )

    preview_html = render_html(
        items=items,
        period_start=period_start,
        period_end=period_end,
        update_label=update_label,
        user_guide_note=user_guide_note,
        content_sha256=content_sha256,
        content_bytes=len(response.content),
        page_count=len(reader.pages),
    )
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(preview_html, encoding="utf-8")
    _write_digest_status(digest_status)
    if args.persist:
        persist_schedule_check(
            status="ok",
            context=RUN_CONTEXT,
            client=history_client,
        )

    print(f"schedule period: {period_start} through {period_end}")
    print(f"document update marker: {update_label or 'not detected'}")
    print(f"structured rows: {len(items)}")
    print(
        "potentially internally relevant rows: "
        + str(sum(bool(item.relevance_matches) for item in items))
    )
    print(
        "rows with COMMBUYS IDs: "
        + str(sum(bool(item.commbuys_ids) for item in items))
    )
    print(f"output: {output_path}")
    print("\nMASSACHUSETTS CONTRACT SCHEDULE GENERATION PASSED")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        _write_digest_status("not_checked")
        if "--persist" in sys.argv[1:]:
            persist_schedule_check(status="error", context=RUN_CONTEXT, error=exc)
        print(
            "\nMASSACHUSETTS CONTRACT SCHEDULE GENERATION FAILED: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        raise
