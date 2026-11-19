#!/usr/bin/env python3
"""
EU agri tracker - source test.

Contacts each source the tracker would rely on, once, from wherever this runs
(on GitHub, that means GitHub's servers), and reports which sources answer
properly, which block automated traffic, which only work with JavaScript,
and which have stale data.

Outputs:
  report.md / report.json         - the full result
  GitHub run summary               - same table, visible on the run page
  e-mail (optional)                - sent if SMTP_USER, SMTP_PASSWORD and MAIL_TO are set

Every request identifies itself honestly as a personal, non-commercial test and
sources are contacted ~1.5 s apart. Nothing here tries to get around a block:
if a site blocks us, that is the finding.
"""

from __future__ import annotations

import datetime as dt
import email.utils
import html
import json
import os
import re
import smtplib
import ssl
import sys
import time
from email.message import EmailMessage

import requests

# --------------------------------------------------------------------------- settings

TIMEOUT = 30          # seconds per request
PAUSE = 1.5           # seconds between requests
MAX_BYTES = 15_000_000
REPO = os.getenv("GITHUB_REPOSITORY", "local-test")
USER_AGENT = (
    "EU-agri-tracker-source-test/1.0 (personal, non-commercial monitoring of public "
    f"EU legislative data; https://github.com/{REPO})"
)

EP = "https://data.europarl.europa.eu/api/v2"
LD = "format=application%2Fld%2Bjson"
SAMPLE_REF = "2024/0319(COD)"   # farmers' position in the food supply chain (AGRI lead)
SAMPLE_EP_ID = "2024-0319"
SAMPLE_EURLEX = "2024_319"
SAMPLE_CELEX = "52024PC0577"

CELLAR_QUERY = """PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>
SELECT ?procedure WHERE { ?procedure a cdm:procedure_interinstitutional } LIMIT 3"""
COUNCIL_QUERY = "SELECT * WHERE { ?s ?p ?o } LIMIT 3"

# Result categories
OK = "ok"
JS = "js"
CHECK = "check"
STALE = "stale"
BLOCKED = "blocked"
WRONG = "wrong"
ERROR = "error"

LABEL = {
    OK: "✅ OK",
    JS: "⚠️ Needs JavaScript",
    CHECK: "⚠️ Unexpected content",
    STALE: "⚠️ Works, but data is old",
    BLOCKED: "⛔ Blocked",
    WRONG: "❓ Wrong address (my side)",
    ERROR: "❌ Error",
}

# --------------------------------------------------------------------------- sources
# expect: jsonld | json | rss | html | pdf | sparql | head | timestamp

SOURCES = [
    # ---- European Parliament: official open data API
    dict(group="European Parliament: open data API", name="File record",
         purpose="Committees, rapporteur, stage of one file",
         url=f"{EP}/procedures/{SAMPLE_EP_ID}?{LD}", expect="jsonld"),
    dict(group="European Parliament: open data API", name="File timeline",
         purpose="Events of one file (history calendar)",
         url=f"{EP}/procedures/{SAMPLE_EP_ID}/events?{LD}", expect="jsonld"),
    dict(group="European Parliament: open data API", name="Change feed: files",
         purpose="What changed this week (daily updates, notifications)",
         url=f"{EP}/procedures/feed?timeframe=one-week", expect="jsonld"),
    dict(group="European Parliament: open data API", name="Change feed: events",
         purpose="Meetings and events added this week",
         url=f"{EP}/events/feed?timeframe=one-week", expect="jsonld"),
    dict(group="European Parliament: open data API", name="Plenary sittings",
         purpose="Plenary calendar",
         url=f"{EP}/meetings?year={dt.date.today().year}&limit=100&{LD}", expect="jsonld",
         key="meetings"),
    dict(group="European Parliament: open data API", name="Plenary votes (beta)",
         purpose="How each MEP voted, latest sitting",
         url=None, expect="jsonld", chained="vote_results"),
    dict(group="European Parliament: open data API", name="Current MEPs",
         purpose="MEP names, groups, countries",
         url=f"{EP}/meps/show-current?limit=5&{LD}", expect="jsonld"),
    dict(group="European Parliament: open data API", name="Current bodies",
         purpose="Committees and their membership",
         url=f"{EP}/corporate-bodies/show-current?limit=300&{LD}", expect="jsonld"),
    dict(group="European Parliament: open data API", name="AGRI committee documents",
         purpose="Draft reports, amendments, agendas",
         url=f"{EP}/committee-documents?committee=AGRI&parliamentary-term=10&limit=3&{LD}",
         expect="jsonld", note="Parameter value 'AGRI' is my guess"),

    # ---- European Parliament: websites
    dict(group="European Parliament: websites", name="Legislative Observatory (OEIL)",
         purpose="Official file page to link to",
         url=f"https://oeil.europarl.europa.eu/oeil/en/procedure-file?reference={SAMPLE_REF}",
         expect="html", must=["2024/0319"]),
    dict(group="European Parliament: websites", name="Legislative Train",
         purpose="Train page per file (monthly)",
         url="https://www.europarl.europa.eu/legislative-train/", expect="html"),
    dict(group="European Parliament: websites", name="AGRI committee page",
         purpose="Committee news and meetings",
         url="https://www.europarl.europa.eu/committees/en/agri/home/highlights", expect="html",
         must=["agricultur"]),
    dict(group="European Parliament: websites", name="AGRI members",
         purpose="Composition, chair, vice-chairs",
         url="https://www.europarl.europa.eu/committees/en/agri/home/members", expect="html",
         must=["chair"]),
    dict(group="European Parliament: websites", name="PECH members",
         purpose="Composition, chair, vice-chairs",
         url="https://www.europarl.europa.eu/committees/en/pech/home/members", expect="html",
         must=["chair"]),
    dict(group="European Parliament: websites", name="REGI members",
         purpose="Composition, chair, vice-chairs",
         url="https://www.europarl.europa.eu/committees/en/regi/home/members", expect="html",
         must=["chair"]),
    dict(group="European Parliament: websites", name="AGRI agenda PDF (doceo)",
         purpose="Committee agendas with file numbers",
         url="https://www.europarl.europa.eu/doceo/document/AGRI-OJ-2024-04-15-1_EN.pdf",
         expect="pdf"),
    dict(group="European Parliament: websites", name="AGRI agenda PDF (meeting docs)",
         purpose="Committee agendas, current term",
         url=("https://redmapl3.europarl.europa.eu/RedmapFront/media/reds_iCmOj/"
              "AGRI-OJ-2026-07-13-1/AGRI-OJ-2026-07-13-1_en.pdf"),
         expect="pdf"),

    # ---- EUR-Lex / Publications Office
    dict(group="EUR-Lex and Publications Office", name="Cellar SPARQL",
         purpose="Proposals, final acts, Council/Commission steps",
         url="https://publications.europa.eu/webapi/rdf/sparql", expect="sparql",
         params={"query": CELLAR_QUERY, "format": "application/sparql-results+json"}),
    dict(group="EUR-Lex and Publications Office", name="EUR-Lex procedure page",
         purpose="Official file page to link to",
         url=f"https://eur-lex.europa.eu/procedure/EN/{SAMPLE_EURLEX}", expect="html",
         must=["2024/0319"]),
    dict(group="EUR-Lex and Publications Office", name="EUR-Lex document",
         purpose="Link to the Commission proposal text",
         url=f"https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:{SAMPLE_CELEX}",
         expect="html", must=["577"]),
    dict(group="EUR-Lex and Publications Office", name="EU Law Tracker (home)",
         purpose="Official file page to link to",
         url="https://law-tracker.europa.eu/", expect="html"),
    dict(group="EUR-Lex and Publications Office", name="EU Law Tracker (file)",
         purpose="Official file page to link to",
         url=f"https://law-tracker.europa.eu/procedure/{SAMPLE_EURLEX}?lang=en", expect="html",
         note="URL pattern is my guess"),

    # ---- Council
    dict(group="Council of the EU", name="RSS: press releases",
         purpose="General approaches, mandates, agreements",
         url="https://www.consilium.europa.eu/en/rss/pressreleases.ashx", expect="rss"),
    dict(group="Council of the EU", name="RSS: AGRIFISH meetings",
         purpose="Upcoming and past AGRIFISH Councils",
         url="https://www.consilium.europa.eu/en/rss/meetings.ashx?cat=agri", expect="rss"),
    dict(group="Council of the EU", name="RSS: agri working parties",
         purpose="Working party meetings",
         url="https://www.consilium.europa.eu/en/rss/meetings.ashx?cat=wp-agri", expect="rss"),
    dict(group="Council of the EU", name="RSS: Coreper meetings",
         purpose="Coreper meetings",
         url="https://www.consilium.europa.eu/en/rss/meetings.ashx?cat=coreper", expect="rss"),
    dict(group="Council of the EU", name="Register RSS: SCA agendas",
         purpose="Special Committee on Agriculture agendas",
         url="https://www.consilium.europa.eu/en/register/rss/AGNDCSA.xml", expect="rss"),
    dict(group="Council of the EU", name="Register RSS: Coreper I agendas",
         purpose="Coreper I agendas",
         url="https://www.consilium.europa.eu/en/register/rss/AGNDCRP1.xml", expect="rss"),
    dict(group="Council of the EU", name="Register RSS: agri & fisheries docs",
         purpose="New Council documents on agri/fish files",
         url="https://www.consilium.europa.eu/en/register/rss/THMAF.xml", expect="rss"),
    dict(group="Council of the EU", name="Register RSS: latest public docs",
         purpose="All new public Council documents",
         url="https://www.consilium.europa.eu/en/register/rss/LPD.xml", expect="rss"),
    dict(group="Council of the EU", name="Press releases page",
         purpose="Fallback if RSS is incomplete",
         url="https://www.consilium.europa.eu/en/press/press-releases/", expect="html",
         must=["council"]),
    dict(group="Council of the EU", name="Meeting calendar page",
         purpose="Fallback for future meetings",
         url="https://www.consilium.europa.eu/en/meetings/calendar/", expect="html",
         must=["council"]),
    dict(group="Council of the EU", name="Council votes SPARQL",
         purpose="How each government voted",
         url="https://data.consilium.europa.eu/sparql", expect="sparql",
         params={"query": COUNCIL_QUERY, "format": "application/sparql-results+json"}),

    # ---- Commission
    dict(group="European Commission", name="Have Your Say (page)",
         purpose="Consultations and feedback deadlines",
         url="https://ec.europa.eu/info/law/better-regulation/have-your-say/initiatives_en",
         expect="html"),
    dict(group="European Commission", name="Have Your Say (data behind it)",
         purpose="Same, as structured data",
         url=("https://ec.europa.eu/info/law/better-regulation/brpapi/searchInitiatives"
              "?page=0&size=1&language=EN"),
         expect="json", note="Unofficial address, my guess"),
    dict(group="European Commission", name="Comitology register",
         purpose="Draft implementing acts, committee meetings",
         url="https://ec.europa.eu/transparency/comitology-register/screen/home",
         expect="html"),
    dict(group="European Commission", name="Register of delegated acts",
         purpose="Delegated and implementing acts",
         url="https://webgate.ec.europa.eu/regdel/", expect="html"),

    # ---- Open projects whose data could be reused
    dict(group="Open projects (data reuse)", name="Parltrack dumps page",
         purpose="Shadows, amendments, committee votes",
         url="https://parltrack.eu/dumps", expect="html", must=["ep_dossiers"]),
    dict(group="Open projects (data reuse)", name="Parltrack dossiers dump",
         purpose="Is Parltrack still updating?",
         url="https://parltrack.eu/dumps/ep_dossiers.json.lz", expect="head", fresh_days=14),
    dict(group="Open projects (data reuse)", name="HowTheyVote export date",
         purpose="Plenary votes + amendment authors (weekly)",
         url="https://github.com/HowTheyVote/data/releases/latest/download/last_updated.txt",
         expect="timestamp", fresh_days=14),
    dict(group="Open projects (data reuse)", name="HowTheyVote developers page",
         purpose="API documentation",
         url="https://howtheyvote.eu/developers", expect="html"),
]

# --------------------------------------------------------------------------- helpers

BLOCK_MARKERS = [
    "just a moment", "cf-chl", "challenge-platform", "attention required",
    "cf-browser-verification", "access denied", "request unsuccessful",
    "_incapsula_resource", "captcha", "awswaf", "aws-waf-token",
    "verify you are human", "please enable cookies", "the requested url was rejected",
    "support id is", "pardon our interruption", "bot detection",
]
JS_MARKERS = ["<app-root", "ng-version", 'id="root"></div>', "id=\"app\"></div>",
              "please enable javascript", "enable javascript to", "you need to enable javascript"]

session = requests.Session()
session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en"})


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def visible_text(body: str) -> str:
    body = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", body)
    body = re.sub(r"(?s)<[^>]+>", " ", body)
    return re.sub(r"\s+", " ", html.unescape(body)).strip()


def fetch(src: dict) -> tuple[requests.Response | None, bytes, float, str | None]:
    """One request (one retry on connection problems). Returns response, body, seconds, error."""
    method = "HEAD" if src["expect"] == "head" else "GET"
    headers = {}
    if src["expect"] in ("jsonld",):
        headers["Accept"] = "application/ld+json"
    elif src["expect"] in ("json",):
        headers["Accept"] = "application/json"
    elif src["expect"] == "sparql":
        headers["Accept"] = "application/sparql-results+json"
    last_err = None
    for attempt in (1, 2):
        start = time.monotonic()
        try:
            resp = session.request(method, src["url"], params=src.get("params"), headers=headers,
                                   timeout=TIMEOUT, allow_redirects=True, stream=True)
            if method == "HEAD" and resp.status_code in (403, 405, 501):
                resp.close()
                resp = session.get(src["url"], headers=headers, timeout=TIMEOUT,
                                   allow_redirects=True, stream=True)
                body = b""
            elif method == "HEAD":
                body = b""
            else:
                chunks, size = [], 0
                for chunk in resp.iter_content(65536):
                    chunks.append(chunk)
                    size += len(chunk)
                    if size > MAX_BYTES:
                        break
                body = b"".join(chunks)
            resp.close()
            return resp, body, time.monotonic() - start, None
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_err = f"{type(exc).__name__}: {str(exc)[:160]}"
            if attempt == 1:
                time.sleep(5)
        except requests.RequestException as exc:
            return None, b"", time.monotonic() - start, f"{type(exc).__name__}: {str(exc)[:160]}"
    return None, b"", float(TIMEOUT), last_err


def count_items(data) -> int | None:
    if isinstance(data, dict):
        for key in ("data", "@graph", "items", "results", "content"):
            if isinstance(data.get(key), list):
                return len(data[key])
        if isinstance(data.get("results"), dict) and isinstance(data["results"].get("bindings"), list):
            return len(data["results"]["bindings"])
    if isinstance(data, list):
        return len(data)
    return None


def newest_rss_date(text: str) -> dt.datetime | None:
    found = []
    for raw in re.findall(r"<pubDate>([^<]+)</pubDate>", text):
        try:
            found.append(email.utils.parsedate_to_datetime(raw.strip()))
        except (TypeError, ValueError):
            pass
    for raw in re.findall(r"<(?:\w+:)?updated>([^<]+)</(?:\w+:)?updated>", text):
        try:
            found.append(dt.datetime.fromisoformat(raw.strip().replace("Z", "+00:00")))
        except ValueError:
            pass
    found = [d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc) for d in found]
    return max(found) if found else None


def parse_timestamp(text: str) -> dt.datetime | None:
    text = text.strip()
    if re.fullmatch(r"\d{9,13}(\.\d+)?", text):
        value = float(text)
        if value > 1e12:
            value /= 1000
        return dt.datetime.fromtimestamp(value, dt.timezone.utc)
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def age_text(when: dt.datetime) -> tuple[str, int]:
    days = (now_utc() - when).days
    return f"{when:%Y-%m-%d} ({plural(days, 'day')} ago)", days


def classify(src: dict, resp: requests.Response | None, body: bytes, err: str | None) -> tuple[str, str]:
    if resp is None:
        return ERROR, err or "No response"

    code = resp.status_code
    text = body.decode(resp.encoding or "utf-8", errors="replace") if body else ""
    lower = text.lower()
    small = len(text) < 20000
    marker = next((m for m in BLOCK_MARKERS if m in lower), None)

    if code in (401, 403, 429) or (code in (202, 503) and marker):
        return BLOCKED, f"HTTP {code}" + (f", page says “{marker}”" if marker else "")
    if code in (400, 404, 410):
        return WRONG, f"HTTP {code}" + (f". {src['note']}" if src.get("note") else "")
    if code >= 500:
        return ERROR, f"Server error HTTP {code}"
    if code >= 300 or code < 200:
        return CHECK, f"HTTP {code}"
    if marker and small and src["expect"] not in ("pdf", "head"):
        return BLOCKED, f"Challenge page (“{marker}”)"

    expect = src["expect"]

    if expect == "head":
        modified = resp.headers.get("Last-Modified")
        size = resp.headers.get("Content-Length")
        size_txt = f", {int(size) / 1e6:.1f} MB" if size and size.isdigit() else ""
        if not modified:
            return OK, f"Reachable{size_txt}, no date given"
        when = email.utils.parsedate_to_datetime(modified)
        label, days = age_text(when)
        if days > src.get("fresh_days", 14):
            return STALE, f"Last updated {label}{size_txt}"
        return OK, f"Last updated {label}{size_txt}"

    if expect == "timestamp":
        when = parse_timestamp(text)
        if not when:
            return CHECK, f"Could not read a date from: {text[:60]!r}"
        label, days = age_text(when)
        if days > src.get("fresh_days", 14):
            return STALE, f"Last export {label}"
        return OK, f"Last export {label}"

    if expect in ("jsonld", "json", "sparql"):
        try:
            data = json.loads(text)
        except ValueError:
            if "<html" in lower[:2000]:
                if any(m in lower for m in JS_MARKERS):
                    return JS, "Returned a JavaScript page, not data"
                return CHECK, "Returned a web page instead of data"
            return CHECK, f"Not valid JSON ({len(body):,} bytes)"
        if expect == "sparql":
            if isinstance(data, dict) and ("boolean" in data or "results" in data):
                return OK, f"Query answered, {plural(count_items(data) or 0, 'row')}"
            return CHECK, "JSON, but not a query result"
        n = count_items(data)
        if expect == "jsonld" and isinstance(data, dict) and not data.get("data") and n == 0:
            return OK, "Valid data, but empty for this request"
        return OK, "Valid data" + (f", {plural(n, 'item')}" if n is not None else "")

    if expect == "rss":
        if not re.search(r"<(rss|feed|rdf:RDF)[\s>]", text[:5000]):
            if "<html" in lower[:2000]:
                return CHECK, "Returned a web page instead of a feed"
            return CHECK, "Not an RSS/Atom feed"
        items = len(re.findall(r"<item[\s>]", text)) + len(re.findall(r"<entry[\s>]", text))
        newest = newest_rss_date(text)
        detail = f"Feed, {plural(items, 'item')}"
        if newest:
            detail += f", newest {newest:%Y-%m-%d}"
        return OK, detail

    if expect == "pdf":
        if body[:5] == b"%PDF-":
            return OK, f"PDF, {len(body) / 1024:.0f} KB"
        if "<html" in lower[:2000]:
            return CHECK, "Got a web page instead of the PDF"
        return CHECK, "Not a PDF"

    # html
    visible = visible_text(text)
    js_hint = any(m in lower for m in JS_MARKERS)
    if len(visible) < 400 or (js_hint and len(visible) < 1500):
        return JS, f"Page shell only ({len(visible)} characters of text): content is built by JavaScript"
    missing = [m for m in src.get("must", []) if m.lower() not in lower]
    if missing:
        return CHECK, f"Page loaded, but expected text not found: {', '.join(missing)}"
    return OK, f"Page loaded, {len(visible):,} characters of text"


def resolve_chained(src: dict, results: list[dict]) -> None:
    """Fill in URLs that depend on an earlier answer."""
    if src.get("chained") != "vote_results":
        return
    meetings = next((r for r in results if r.get("key") == "meetings"), None)
    raw = meetings.get("_raw", "") if meetings else ""
    today = dt.date.today().isoformat()
    dates = sorted({d for d in re.findall(r"MTG-PL-(\d{4}-\d{2}-\d{2})", raw) if d <= today})
    if not dates:
        src["url"] = None
        src["_skip"] = "No past plenary sitting found in the sittings list, so nothing to test"
        return
    src["url"] = f"{EP}/meetings/MTG-PL-{dates[-1]}/vote-results?limit=5&{LD}"
    src["purpose"] += f" ({dates[-1]})"


# --------------------------------------------------------------------------- report

def build_reports(results: list[dict], started: dt.datetime) -> tuple[str, str, str]:
    counts = {k: sum(1 for r in results if r["status"] == k) for k in LABEL}
    attention = counts[JS] + counts[CHECK] + counts[STALE]
    summary = (f"{counts[OK]} OK · {attention} need attention · {counts[BLOCKED]} blocked · "
               f"{counts[WRONG]} wrong address (my side) · {plural(counts[ERROR], 'error')}")
    subject = f"[EU agri tracker] Source test: {counts[OK]}/{len(results)} OK, {counts[BLOCKED]} blocked"

    lines = [
        "# Source test: EU agri tracker",
        "",
        f"Run {started:%Y-%m-%d %H:%M} UTC from {'GitHub Actions' if os.getenv('GITHUB_ACTIONS') else 'a local machine'}"
        f" · {len(results)} sources · each contacted once, ~{PAUSE:.1f} s apart",
        "",
        f"**{summary}**",
        "",
    ]

    def bullet_list(status: str, heading: str) -> None:
        hits = [r for r in results if r["status"] == status]
        if hits:
            lines.append(f"**{heading}**")
            lines.extend(f"- {r['group']}: {r['name']}: {r['detail']}" for r in hits)
            lines.append("")

    bullet_list(BLOCKED, "Blocked (the main finding: these need another route)")
    bullet_list(JS, "Needs JavaScript (need to find the data the page loads)")
    bullet_list(STALE, "Old data")
    bullet_list(CHECK, "Unexpected content")
    bullet_list(WRONG, "Wrong address on my side (I fix these; not a block)")
    bullet_list(ERROR, "Errors (retry before drawing conclusions)")

    current = None
    for r in results:
        if r["group"] != current:
            current = r["group"]
            lines += ["", f"## {current}", "",
                      "| Source | Used for | Result | Detail | HTTP | Time |",
                      "|---|---|---|---|---|---|"]
        link = f"[{r['name']}]({r['url']})" if r.get("url") else r["name"]
        detail = r["detail"].replace("|", "/")
        lines.append(f"| {link} | {r['purpose']} | {LABEL[r['status']]} | {detail} | "
                     f"{r.get('http') or '-'} | {r['seconds']:.1f} s |")
    md = "\n".join(lines) + "\n"

    # HTML version for e-mail
    colours = {OK: "#1a7f37", JS: "#9a6700", CHECK: "#9a6700", STALE: "#9a6700",
               BLOCKED: "#cf222e", WRONG: "#57606a", ERROR: "#cf222e"}
    rows, current = [], None
    for r in results:
        if r["group"] != current:
            current = r["group"]
            rows.append(f'<tr><td colspan="4" style="padding:14px 6px 4px;font-weight:600;'
                        f'border-bottom:2px solid #d0d7de">{html.escape(current)}</td></tr>')
        name = html.escape(r["name"])
        if r.get("url"):
            name = f'<a href="{html.escape(r["url"])}">{name}</a>'
        rows.append(
            "<tr>"
            f'<td style="padding:5px 6px;border-bottom:1px solid #eee">{name}<br>'
            f'<span style="color:#57606a;font-size:12px">{html.escape(r["purpose"])}</span></td>'
            f'<td style="padding:5px 6px;border-bottom:1px solid #eee;white-space:nowrap;'
            f'color:{colours[r["status"]]};font-weight:600">{html.escape(LABEL[r["status"]])}</td>'
            f'<td style="padding:5px 6px;border-bottom:1px solid #eee;font-size:13px">'
            f'{html.escape(r["detail"])}</td>'
            f'<td style="padding:5px 6px;border-bottom:1px solid #eee;font-size:12px;color:#57606a;'
            f'white-space:nowrap">'
            f'{r.get("http") or "-"} · {r["seconds"]:.1f}s</td>'
            "</tr>")
    findings = []
    for status, heading in ((BLOCKED, "Blocked"), (JS, "Needs JavaScript"), (STALE, "Old data"),
                            (CHECK, "Unexpected content"), (WRONG, "Wrong address (my side)"),
                            (ERROR, "Errors")):
        hits = [r for r in results if r["status"] == status]
        if hits:
            items = "".join(f"<li>{html.escape(r['name'])}: {html.escape(r['detail'])}</li>" for r in hits)
            findings.append(f'<p style="margin:10px 0 2px;color:{colours[status]};font-weight:600">'
                            f'{heading}</p><ul style="margin:0;padding-left:20px">{items}</ul>')
    html_body = (
        '<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#1f2328;max-width:900px">'
        "<h2 style=\"margin:0 0 6px\">Source test: EU agri tracker</h2>"
        f'<p style="margin:0 0 4px;color:#57606a">Run {started:%Y-%m-%d %H:%M} UTC · '
        f"{len(results)} sources</p>"
        f"<p style=\"margin:0 0 6px\"><b>{html.escape(summary)}</b></p>"
        f"{''.join(findings)}<div style=\"height:8px\"></div>"
        f'<table style="border-collapse:collapse;width:100%">{"".join(rows)}</table>'
        '<p style="color:#57606a;font-size:12px;margin-top:16px">Sent automatically by the '
        f"source test in {html.escape(REPO)}.</p></div>")
    return md, html_body, subject


def send_email(subject: str, text: str, html_body: str) -> tuple[bool, str]:
    user = os.getenv("SMTP_USER", "").strip()
    password = os.getenv("SMTP_PASSWORD", "").replace(" ", "").strip()
    recipients = [a.strip() for a in re.split(r"[,;]", os.getenv("MAIL_TO", "")) if a.strip()]
    if not (user and password and recipients):
        return False, "E-mail not configured (SMTP_USER, SMTP_PASSWORD and MAIL_TO secrets not all set)"
    host = os.getenv("SMTP_HOST", "smtp.gmail.com").strip() or "smtp.gmail.com"
    port = int(os.getenv("SMTP_PORT", "465").strip() or "465")

    msg = EmailMessage()
    msg["From"] = email.utils.formataddr(("EU agri tracker", user))
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg.set_content(text)
    msg.add_alternative(html_body, subtype="html")
    try:
        context = ssl.create_default_context()
        if port == 465:
            with smtplib.SMTP_SSL(host, port, context=context, timeout=30) as smtp:
                smtp.login(user, password)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=30) as smtp:
                smtp.starttls(context=context)
                smtp.login(user, password)
                smtp.send_message(msg)
    except smtplib.SMTPAuthenticationError:
        return False, ("E-mail login refused: check SMTP_USER and that SMTP_PASSWORD is a Google "
                       "app password (not the normal Gmail password)")
    except (smtplib.SMTPException, OSError) as exc:
        return False, f"E-mail failed: {type(exc).__name__}: {str(exc)[:200]}"
    return True, f"E-mail sent to {len(recipients)} address(es)"


# --------------------------------------------------------------------------- main

def main() -> int:
    started = now_utc()
    results: list[dict] = []
    print(f"Testing {len(SOURCES)} sources as: {USER_AGENT}\n")

    for i, base in enumerate(SOURCES):
        src = dict(base)
        resolve_chained(src, results)
        if src.get("_skip"):
            status, detail, seconds, code, raw = CHECK, src["_skip"], 0.0, None, ""
        else:
            resp, body, seconds, err = fetch(src)
            status, detail = classify(src, resp, body, err)
            code = resp.status_code if resp is not None else None
            raw = body.decode("utf-8", errors="replace") if src.get("key") else ""
            if resp is not None and resp.url and src.get("url") and resp.url.split("?")[0] != src["url"].split("?")[0]:
                host_from = re.sub(r"^https?://([^/]+).*", r"\1", src["url"])
                host_to = re.sub(r"^https?://([^/]+).*", r"\1", resp.url)
                if host_from != host_to:
                    detail += f" (redirected to {host_to})"
        result = {
            "group": src["group"], "name": src["name"], "purpose": src["purpose"],
            "url": src.get("url"), "status": status, "detail": detail,
            "http": code, "seconds": round(seconds, 2), "key": src.get("key"), "_raw": raw,
        }
        results.append(result)
        print(f"[{i + 1:2}/{len(SOURCES)}] {LABEL[status]:28} {src['name']}: {detail}")
        if i < len(SOURCES) - 1:
            time.sleep(PAUSE)

    md, html_body, subject = build_reports(results, started)

    mail_ok, mail_msg = send_email(subject, md, html_body)
    mail_configured = bool(os.getenv("SMTP_USER") and os.getenv("SMTP_PASSWORD") and os.getenv("MAIL_TO"))
    md += f"\n---\n{'✉️' if mail_ok else 'ℹ️'} {mail_msg}\n"
    print("\n" + mail_msg)

    clean = [{k: v for k, v in r.items() if not k.startswith("_") and k != "key"} for r in results]
    with open("report.md", "w", encoding="utf-8") as fh:
        fh.write(md)
    with open("report.json", "w", encoding="utf-8") as fh:
        json.dump({"run_utc": started.isoformat(), "user_agent": USER_AGENT, "results": clean},
                  fh, ensure_ascii=False, indent=2)
    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as fh:
            fh.write(md)

    # The run only "fails" if e-mail was set up and did not work, so GitHub tells you.
    return 1 if (mail_configured and not mail_ok) else 0


if __name__ == "__main__":
    sys.exit(main())
