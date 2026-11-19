#!/usr/bin/env python3
"""
EU agri tracker - source test, round 2.

Round 1 showed which sources GitHub's servers can reach. Round 2:
  - fixes the requests that were wrong on my side (EP change feeds are Atom XML,
    committee-document parameters, the Cellar query),
  - checks whether the data the tracker needs is actually inside the sources that
    work (rapporteurs and shadows, committee memberships, Council documents),
  - looks inside the JavaScript-only pages for the data addresses they load,
  - re-tests a few blocked pages to see whether the block is consistent,
  - gives step-by-step e-mail diagnostics, trying both of Gmail's ports.

Outputs: report.md, report.json, the GitHub run summary, and an e-mail if
SMTP_USER, SMTP_PASSWORD and MAIL_TO are set.

Every request identifies itself honestly, sources are contacted ~1.5 s apart, and
nothing tries to get around a block: if a site blocks us, that is the finding.
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
from urllib.parse import urljoin, urlparse

import requests

# --------------------------------------------------------------------------- settings

TIMEOUT = 30
PAUSE = 1.5
MAX_BYTES = 15_000_000
REPO = os.getenv("GITHUB_REPOSITORY", "local-test")
USER_AGENT = (
    "EU-agri-tracker-source-test/2.0 (personal, non-commercial monitoring of public "
    f"EU legislative data; https://github.com/{REPO})"
)

EP = "https://data.europarl.europa.eu/api/v2"
LD = "format=application%2Fld%2Bjson"
SAMPLE_REF = "2024/0319(COD)"   # farmers' position in the food supply chain (AGRI lead)
SAMPLE_EP_ID = "2024-0319"
SAMPLE_EURLEX = "2024_319"
SAMPLE_CELEX = "52024PC0577"
WEEK_AGO = (dt.date.today() - dt.timedelta(days=7)).isoformat()

CDM = "PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>\n"
RDFS = "PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>\n"
CELLAR_SUBCLASS_QUERY = CDM + RDFS + """SELECT ?procedure ?type WHERE {
  ?type rdfs:subClassOf cdm:procedure_interinstitutional .
  ?procedure a ?type .
} LIMIT 5"""
CELLAR_CELEX_QUERY = CDM + """SELECT ?work ?p ?o WHERE {
  VALUES ?celex { "52024PC0577" "52024PC0577"^^<http://www.w3.org/2001/XMLSchema#string> }
  ?work cdm:resource_legal_id_celex ?celex .
  ?work ?p ?o .
} LIMIT 200"""

OK, JS, CHECK, STALE, BLOCKED, WRONG, ERROR = "ok", "js", "check", "stale", "blocked", "wrong", "error"
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
# expect: jsonld | json | feed | html | pdf | sparql | timestamp | any
# accept: Accept header to send (None = let the server choose)
# inspect: [(label, regex)] -> counts how often each appears, to check the data is there
# key: keep the raw answer so a later check can use it
# chained: fill in the URL from an earlier answer
# discover: if the page is a JavaScript app, look for the data addresses it loads

G_API = "European Parliament: open data API"
G_WEB = "European Parliament: websites"
G_LEX = "EUR-Lex and Publications Office"
G_CON = "Council of the EU"
G_COM = "European Commission"
G_OPEN = "Open projects (data reuse)"

SOURCES = [
    # ---- European Parliament API
    dict(group=G_API, name="File record", purpose="Committees, rapporteur, stage of one file",
         url=f"{EP}/procedures/{SAMPLE_EP_ID}?{LD}", expect="jsonld", accept="application/ld+json",
         inspect=[("'rapporteur' mentions", r"(?i)rapporteur"), ("'shadow' mentions", r"(?i)shadow"),
                  ("committee codes", r"\b(AGRI|PECH|REGI|ENVI|INTA|BUDG)\b")]),
    dict(group=G_API, name="File timeline", purpose="Events of one file (history calendar)",
         url=f"{EP}/procedures/{SAMPLE_EP_ID}/events?{LD}", expect="jsonld", accept="application/ld+json"),
    dict(group=G_API, name="Change feed: files (1 day)", purpose="What changed yesterday (daily updates)",
         url=f"{EP}/procedures/feed?timeframe=one-day", expect="feed", accept=None,
         note="Round 1 asked for JSON; this feed is Atom XML"),
    dict(group=G_API, name="Change feed: files (since date)", purpose="What changed since a given day",
         url=f"{EP}/procedures/feed?timeframe=custom&start-date={WEEK_AGO}", expect="feed", accept=None),
    dict(group=G_API, name="Change feed: events (1 week)", purpose="Meetings/events added (incl. committees?)",
         url=f"{EP}/events/feed?timeframe=one-week", expect="feed", accept=None,
         inspect=[("committee mentions", r"(?i)committee|\bCOM\b|MTG-CO"),
                  ("AGRI mentions", r"AGRI")]),
    dict(group=G_API, name="Plenary sittings", purpose="Plenary calendar",
         url=f"{EP}/meetings?year={dt.date.today().year}&limit=100&{LD}", expect="jsonld",
         accept="application/ld+json", key="meetings"),
    dict(group=G_API, name="Plenary votes (beta)", purpose="How each MEP voted, latest sitting",
         url=None, expect="jsonld", accept="application/ld+json", chained="vote_results"),
    dict(group=G_API, name="Current MEPs", purpose="MEP names, groups, countries",
         url=f"{EP}/meps/show-current?limit=3&{LD}", expect="jsonld", accept="application/ld+json",
         key="meps"),
    dict(group=G_API, name="One MEP in detail", purpose="Does it list committee memberships and roles?",
         url=None, expect="jsonld", accept="application/ld+json", chained="mep_detail",
         inspect=[("membership entries", r"(?i)membership"),
                  ("chair/vice-chair roles", r"(?i)chair"),
                  ("substitute roles", r"(?i)substitute"),
                  ("committee codes", r"\b(AGRI|PECH|REGI|ENVI|INTA|BUDG|ITRE|AFET)\b")]),
    dict(group=G_API, name="Standing committees", purpose="Committee list (AGRI, PECH, REGI present?)",
         url=f"{EP}/corporate-bodies?body-classification=COMMITTEE_PARLIAMENTARY_STANDING&limit=100&{LD}",
         expect="jsonld", accept="application/ld+json",
         inspect=[("AGRI", r"\bAGRI\b"), ("PECH", r"\bPECH\b"), ("REGI", r"\bREGI\b")]),
    dict(group=G_API, name="Committee docs: AGRI, no term", purpose="Draft reports and opinions",
         url=f"{EP}/committee-documents?committee=AGRI&limit=3&{LD}", expect="jsonld",
         accept="application/ld+json"),
    dict(group=G_API, name="Committee docs: AGRI, term 10, draft reports",
         purpose="Draft reports, current term",
         url=(f"{EP}/committee-documents?committee=AGRI&parliamentary-term=10"
              f"&work-type=REPORT_PARLIAMENTARY_COMMITTEE_DRAFT&limit=3&{LD}"),
         expect="jsonld", accept="application/ld+json"),
    dict(group=G_API, name="Committee docs: AFET (spec example)", purpose="Does this endpoint work at all?",
         url=f"{EP}/committee-documents?committee=AFET&limit=3&{LD}", expect="jsonld",
         accept="application/ld+json"),

    # ---- European Parliament websites
    dict(group=G_WEB, name="Legislative Observatory (OEIL)",
         purpose="File page: rapporteur, shadows, key events",
         url=f"https://oeil.europarl.europa.eu/oeil/en/procedure-file?reference={SAMPLE_REF}",
         expect="html", must=["2024/0319"],
         inspect=[("'shadow' mentions", r"(?i)shadow"), ("'rapporteur' mentions", r"(?i)rapporteur"),
                  ("'amendment' mentions", r"(?i)amendment"), ("'trilogue' mentions", r"(?i)trilogue")]),
    dict(group=G_WEB, name="Legislative Train (re-test)", purpose="Is the block consistent?",
         url="https://www.europarl.europa.eu/legislative-train/", expect="html"),
    dict(group=G_WEB, name="AGRI members page (re-test)", purpose="Is the block consistent?",
         url="https://www.europarl.europa.eu/committees/en/agri/home/members", expect="html"),
    dict(group=G_WEB, name="Agenda PDF (meeting docs host)", purpose="Committee agendas, current term",
         url=("https://redmapl3.europarl.europa.eu/RedmapFront/media/reds_iCmOj/"
              "AGRI-OJ-2026-07-13-1/AGRI-OJ-2026-07-13-1_en.pdf"), expect="pdf"),
    dict(group=G_WEB, name="eMeeting host", purpose="Committee meeting documents (another host)",
         url="https://emeeting.europarl.europa.eu/", expect="any", discover=True),

    # ---- EUR-Lex / Publications Office
    dict(group=G_LEX, name="Cellar: procedure types", purpose="Fixed query: are procedures there?",
         url="https://publications.europa.eu/webapi/rdf/sparql", expect="sparql",
         accept="application/sparql-results+json",
         params={"query": CELLAR_SUBCLASS_QUERY, "format": "application/sparql-results+json"}),
    dict(group=G_LEX, name="Cellar: one proposal by CELEX", purpose="Can we find a proposal and its links?",
         url="https://publications.europa.eu/webapi/rdf/sparql", expect="sparql",
         accept="application/sparql-results+json",
         params={"query": CELLAR_CELEX_QUERY, "format": "application/sparql-results+json"},
         inspect=[("dossier links", r"(?i)dossier"), ("procedure links", r"(?i)procedure"),
                  ("EuroVoc subjects", r"eurovoc")]),
    dict(group=G_LEX, name="EUR-Lex procedure page (re-test)", purpose="Bot check or real JavaScript?",
         url=f"https://eur-lex.europa.eu/procedure/EN/{SAMPLE_EURLEX}", expect="html"),
    dict(group=G_LEX, name="EU Law Tracker", purpose="Find the data address the page loads",
         url=f"https://law-tracker.europa.eu/procedure/{SAMPLE_EURLEX}?lang=en", expect="html",
         discover=True),

    # ---- Council
    dict(group=G_CON, name="RSS: press releases", purpose="General approaches, mandates, agreements",
         url="https://www.consilium.europa.eu/en/rss/pressreleases.ashx", expect="feed"),
    dict(group=G_CON, name="RSS: AGRIFISH meetings", purpose="Upcoming and past AGRIFISH Councils",
         url="https://www.consilium.europa.eu/en/rss/meetings.ashx?cat=agri", expect="feed"),
    dict(group=G_CON, name="RSS: agri working parties", purpose="Working party meetings",
         url="https://www.consilium.europa.eu/en/rss/meetings.ashx?cat=wp-agri", expect="feed"),
    dict(group=G_CON, name="RSS: Coreper meetings", purpose="Coreper meetings",
         url="https://www.consilium.europa.eu/en/rss/meetings.ashx?cat=coreper", expect="feed"),
    dict(group=G_CON, name="Register RSS: SCA agendas", purpose="Special Committee on Agriculture agendas",
         url="https://www.consilium.europa.eu/en/register/rss/AGNDCSA.xml", expect="feed"),
    dict(group=G_CON, name="Register RSS: Coreper I agendas", purpose="Coreper I agendas",
         url="https://www.consilium.europa.eu/en/register/rss/AGNDCRP1.xml", expect="feed"),
    dict(group=G_CON, name="Register RSS: agri & fisheries docs", purpose="New Council documents on agri/fish",
         url="https://www.consilium.europa.eu/en/register/rss/THMAF.xml", expect="feed", key="thmaf",
         inspect=[("items quoting a file number", r"\d{4}/\d{4}\s*\((?:COD|CNS|NLE|APP)\)")]),
    dict(group=G_CON, name="Register RSS: summary of Council acts", purpose="Adopted acts (vote results)",
         url="https://www.consilium.europa.eu/en/register/rss/ACT.xml", expect="feed"),
    dict(group=G_CON, name="Register RSS: minutes on adoption", purpose="Minutes with voting results",
         url="https://www.consilium.europa.eu/en/register/rss/MINLA.xml", expect="feed"),
    dict(group=G_CON, name="Council document (from the agri feed)", purpose="Can we open the documents?",
         url=None, expect="any", chained="council_doc"),
    dict(group=G_CON, name="Press releases page (re-test)", purpose="Is the block consistent?",
         url="https://www.consilium.europa.eu/en/press/press-releases/", expect="html"),

    # ---- Commission
    dict(group=G_COM, name="Have Your Say data", purpose="Consultations: what fields are there?",
         url=("https://ec.europa.eu/info/law/better-regulation/brpapi/searchInitiatives"
              "?page=0&size=3&language=EN"),
         expect="json", accept="application/json",
         inspect=[("'feedback' fields", r"(?i)feedback"), ("date fields", r"(?i)date"),
                  ("'topic' fields", r"(?i)topic"), ("'AGRI' mentions", r"AGRI")]),
    dict(group=G_COM, name="Have Your Say page", purpose="Find more data addresses",
         url="https://ec.europa.eu/info/law/better-regulation/have-your-say/initiatives_en",
         expect="html", discover=True),
    dict(group=G_COM, name="Comitology register", purpose="Find the data address the page loads",
         url="https://ec.europa.eu/transparency/comitology-register/screen/home", expect="html",
         discover=True),
    dict(group=G_COM, name="Register of delegated acts", purpose="Find the data address the page loads",
         url="https://webgate.ec.europa.eu/regdel/", expect="html", discover=True),

    # ---- Open projects
    dict(group=G_OPEN, name="HowTheyVote export date", purpose="Plenary votes + amendment authors (weekly)",
         url="https://github.com/HowTheyVote/data/releases/latest/download/last_updated.txt",
         expect="timestamp", fresh_days=14),
    dict(group=G_OPEN, name="HowTheyVote API", purpose="Votes by search, as data",
         url="https://howtheyvote.eu/api/votes?page=1&page_size=1", expect="json",
         accept="application/json", note="Address is my guess"),
]

# --------------------------------------------------------------------------- helpers

BLOCK_MARKERS = [
    "just a moment", "cf-chl", "challenge-platform", "attention required",
    "cf-browser-verification", "access denied", "request unsuccessful",
    "_incapsula_resource", "captcha", "awswaf", "aws-waf-token",
    "verify you are human", "please enable cookies", "the requested url was rejected",
    "support id is", "pardon our interruption", "bot detection",
]
JS_MARKERS = ["<app-root", "ng-version", 'id="root"></div>', 'id="app"></div>',
              "please enable javascript", "enable javascript to", "you need to enable javascript"]
SKIP_SCRIPTS = ("webtools", "analytics", "piwik", "matomo", "europa-analytics", "cookie", "cck",
                "googletagmanager", "polyfill", "jquery")

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


def host(url: str) -> str:
    return urlparse(url).netloc.lower()


def get(url: str, accept: str | None = None, params: dict | None = None
        ) -> tuple[requests.Response | None, bytes, float, str | None]:
    """One GET (one retry on connection problems). Returns response, body, seconds, error."""
    headers = {"Accept": accept} if accept else {}
    last_err = None
    for attempt in (1, 2):
        start = time.monotonic()
        try:
            resp = session.get(url, params=params, headers=headers, timeout=TIMEOUT,
                               allow_redirects=True, stream=True)
            chunks, size = [], 0
            for chunk in resp.iter_content(65536):
                chunks.append(chunk)
                size += len(chunk)
                if size > MAX_BYTES:
                    break
            resp.close()
            return resp, b"".join(chunks), time.monotonic() - start, None
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_err = f"{type(exc).__name__}: {str(exc)[:160]}"
            if attempt == 1:
                time.sleep(5)
        except requests.RequestException as exc:
            return None, b"", time.monotonic() - start, f"{type(exc).__name__}: {str(exc)[:160]}"
    return None, b"", float(TIMEOUT), last_err


def decode(resp: requests.Response | None, body: bytes) -> str:
    if not body:
        return ""
    return body.decode((resp.encoding if resp is not None else None) or "utf-8", errors="replace")


def count_items(data) -> int | None:
    if isinstance(data, dict):
        for key in ("data", "@graph", "items", "results", "content", "votes"):
            if isinstance(data.get(key), list):
                return len(data[key])
        if isinstance(data.get("results"), dict) and isinstance(data["results"].get("bindings"), list):
            return len(data["results"]["bindings"])
    if isinstance(data, list):
        return len(data)
    return None


def newest_feed_date(text: str) -> dt.datetime | None:
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
    text = decode(resp, body)
    lower = text.lower()
    small = len(text) < 20000
    marker = next((m for m in BLOCK_MARKERS if m in lower), None)
    waf = resp.headers.get("x-amzn-waf-action") or resp.headers.get("cf-mitigated")

    if code in (401, 403, 429) or (code in (202, 503) and marker):
        return BLOCKED, f"HTTP {code}" + (f", page says “{marker}”" if marker else "")
    if waf or (code == 202 and len(text.strip()) < 2000):
        return BLOCKED, (f"Bot check (HTTP {code}"
                         + (f", {waf}" if waf else ", empty page that requires running a script")
                         + ")")
    if code in (400, 404, 405, 410):
        return WRONG, f"HTTP {code}" + (f". {src['note']}" if src.get("note") else "")
    if code == 204:
        return CHECK, "HTTP 204: request accepted, but no content for these parameters"
    if code >= 500:
        return ERROR, f"Server error HTTP {code}" + (f". {src['note']}" if src.get("note") else "")
    if code >= 300 or code < 200:
        return CHECK, f"HTTP {code}"
    if marker and small and src["expect"] not in ("pdf",):
        return BLOCKED, f"Challenge page (“{marker}”)"

    expect = src["expect"]
    ctype = resp.headers.get("Content-Type", "").split(";")[0].strip()

    if expect == "any":
        if body[:5] == b"%PDF-":
            return OK, f"PDF, {len(body) / 1024:.0f} KB"
        if ctype.startswith("text/html") or "<html" in lower[:3000]:
            visible = visible_text(text)
            if len(visible) < 400 or any(m in lower for m in JS_MARKERS) and len(visible) < 1500:
                return JS, f"Page shell only ({len(visible)} characters of text)"
            return OK, f"Web page, {len(visible):,} characters of text"
        return OK, f"{ctype or 'unknown type'}, {len(body) / 1024:.0f} KB"

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
            return CHECK, f"Not valid JSON ({len(body):,} bytes, type {ctype or 'unknown'})"
        if expect == "sparql":
            if isinstance(data, dict) and ("boolean" in data or "results" in data):
                return OK, f"Query answered, {plural(count_items(data) or 0, 'row')}"
            return CHECK, "JSON, but not a query result"
        n = count_items(data)
        return OK, "Valid data" + (f", {plural(n, 'item')}" if n is not None else "")

    if expect == "feed":
        if not re.search(r"<(rss|feed|rdf:RDF)[\s>]", text[:5000]):
            if "<html" in lower[:2000]:
                return CHECK, "Returned a web page instead of a feed"
            return CHECK, f"Not an RSS/Atom feed (type {ctype or 'unknown'})"
        items = len(re.findall(r"<item[\s>]", text)) + len(re.findall(r"<entry[\s>]", text))
        newest = newest_feed_date(text)
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


def inspect(src: dict, text: str) -> str:
    parts = []
    for label, pattern in src.get("inspect", []):
        parts.append(f"{label}: {len(re.findall(pattern, text))}")
    return "; ".join(parts)


API_HINT = re.compile(r"(?i)(api|rest|service|services|graphql|brpapi|search|json|backend|data)")
URL_IN_JS = re.compile(r"""["'`](https?://[^"'`\s<>]{6,200})["'`]""")
PATH_IN_JS = re.compile(r"""["'`](/[A-Za-z0-9_\-./]{2,120})["'`]""")


def discover(page_url: str, page_text: str) -> list[str]:
    """Look inside a JavaScript app for the addresses it loads its data from."""
    found: list[str] = []

    def harvest(code: str, base: str) -> None:
        for m in URL_IN_JS.findall(code):
            if API_HINT.search(m) and not any(s in m.lower() for s in SKIP_SCRIPTS) \
                    and not m.lower().endswith((".js", ".css", ".png", ".svg", ".woff2", ".ico")) \
                    and "w3.org" not in m and "schema.org" not in m:
                found.append(m)
        for m in PATH_IN_JS.findall(code):
            if re.search(r"(?i)/(api|rest|services?|brpapi|graphql|backend)(/|$)", m):
                found.append(urljoin(base, m))

    harvest(page_text, page_url)
    scripts = []
    for src in re.findall(r"""<script[^>]+src=["']([^"']+)["']""", page_text, flags=re.I):
        full = urljoin(page_url, src)
        if any(s in full.lower() for s in SKIP_SCRIPTS):
            continue
        if host(full).endswith("europa.eu") or host(full) == host(page_url):
            scripts.append(full)
    for script in scripts[:6]:
        time.sleep(1.0)
        resp, body, _, _ = get(script)
        if resp is not None and resp.status_code == 200:
            harvest(decode(resp, body), page_url)
    unique = []
    for f in found:
        if f not in unique:
            unique.append(f)
    return unique[:12]


def resolve_chained(src: dict, results: list[dict]) -> None:
    """Fill in URLs that depend on an earlier answer."""
    raw = {r["key"]: r.get("_raw", "") for r in results if r.get("key")}
    kind = src.get("chained")
    if kind == "vote_results":
        today = dt.date.today().isoformat()
        dates = sorted({d for d in re.findall(r"MTG-PL-(\d{4}-\d{2}-\d{2})", raw.get("meetings", ""))
                        if d <= today})
        if dates:
            src["url"] = f"{EP}/meetings/MTG-PL-{dates[-1]}/vote-results?limit=5&{LD}"
            src["purpose"] += f" ({dates[-1]})"
        else:
            src["_skip"] = "No past plenary sitting found in the sittings list"
    elif kind == "mep_detail":
        text = raw.get("meps", "")
        ids = re.findall(r"person/(\d{3,7})", text) or re.findall(r'"identifier"\s*:\s*"(\d{3,7})"', text)
        if ids:
            src["url"] = f"{EP}/meps/{ids[0]}?{LD}"
            src["purpose"] += f" (MEP id {ids[0]})"
        else:
            src["_skip"] = "Could not find an MEP id in the MEP list"
    elif kind == "council_doc":
        text = raw.get("thmaf", "")
        links = re.findall(r"<item[\s>].*?<link>\s*([^<\s]+)\s*</link>", text, flags=re.S)
        if links:
            src["url"] = html.unescape(links[0])
        else:
            src["_skip"] = "Could not find a document link in the agri & fisheries feed"


def council_followup(resp: requests.Response | None, body: bytes) -> str:
    """If the Council link is a page, try the PDF it points to."""
    if resp is None or body[:5] == b"%PDF-":
        return ""
    text = decode(resp, body)
    pdfs = re.findall(r"""https?://data\.consilium\.europa\.eu/doc/document/[^"'\s<>]+""", text)
    if not pdfs:
        return "no document download link found on the page"
    time.sleep(PAUSE)
    r2, b2, _, err = get(html.unescape(pdfs[0]))
    if r2 is None:
        return f"document link failed: {err}"
    if b2[:5] == b"%PDF-":
        return f"document PDF downloads ({len(b2) / 1024:.0f} KB)"
    return f"document link answered HTTP {r2.status_code}, not a PDF"


# --------------------------------------------------------------------------- e-mail

def send_email(subject: str, text: str, html_body: str) -> tuple[bool, str]:
    user = os.getenv("SMTP_USER", "").strip()
    password = re.sub(r"\s+", "", os.getenv("SMTP_PASSWORD", ""))
    recipients = [a.strip() for a in re.split(r"[,;]", os.getenv("MAIL_TO", "")) if a.strip()]
    if not (user and password and recipients):
        missing = [n for n, v in (("SMTP_USER", user), ("SMTP_PASSWORD", password), ("MAIL_TO", recipients))
                   if not v]
        return False, f"E-mail not configured (missing: {', '.join(missing)})"
    host_name = os.getenv("SMTP_HOST", "").strip() or "smtp.gmail.com"
    notes = [f"sender {user[:3]}…@{user.split('@')[-1] if '@' in user else '?'}",
             f"password length {len(password)} (a Google app password has 16)",
             f"{len(recipients)} recipient(s)"]

    msg = EmailMessage()
    msg["From"] = email.utils.formataddr(("EU agri tracker", user))
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg.set_content(text)
    msg.add_alternative(html_body, subtype="html")

    attempts = []
    for port in (465, 587):
        stage = "connect"
        try:
            context = ssl.create_default_context()
            if port == 465:
                smtp = smtplib.SMTP_SSL(host_name, port, context=context, timeout=30)
            else:
                smtp = smtplib.SMTP(host_name, port, timeout=30)
            with smtp:
                stage = "greeting"
                smtp.ehlo()
                if port == 587:
                    stage = "encryption"
                    smtp.starttls(context=context)
                    smtp.ehlo()
                stage = "login"
                smtp.login(user, password)
                stage = "send"
                refused = smtp.send_message(msg)
            ok_msg = f"E-mail sent via port {port} to {len(recipients) - len(refused)} address(es)"
            if refused:
                ok_msg += f"; refused: {', '.join(refused)}"
            return True, ok_msg + f" ({'; '.join(notes)})"
        except smtplib.SMTPAuthenticationError as exc:
            reply = exc.smtp_error.decode(errors="replace") if isinstance(exc.smtp_error, bytes) else str(exc.smtp_error)
            attempts.append(f"port {port}: login refused ({exc.smtp_code} {reply[:160]})")
            break   # trying the other port will not change a wrong password
        except smtplib.SMTPServerDisconnected as exc:
            attempts.append(f"port {port}: server closed the connection during {stage} ({str(exc)[:120]})")
        except smtplib.SMTPResponseException as exc:
            reply = exc.smtp_error.decode(errors="replace") if isinstance(exc.smtp_error, bytes) else str(exc.smtp_error)
            attempts.append(f"port {port}: server refused during {stage} ({exc.smtp_code} {reply[:160]})")
        except (smtplib.SMTPException, OSError) as exc:
            attempts.append(f"port {port}: {type(exc).__name__} during {stage} ({str(exc)[:120]})")
    return False, "E-mail failed. " + " | ".join(attempts) + f" ({'; '.join(notes)})"


# --------------------------------------------------------------------------- report

def build_reports(results: list[dict], started: dt.datetime) -> tuple[str, str, str]:
    counts = {k: sum(1 for r in results if r["status"] == k) for k in LABEL}
    attention = counts[JS] + counts[CHECK] + counts[STALE]
    summary = (f"{counts[OK]} OK · {attention} need attention · {counts[BLOCKED]} blocked · "
               f"{counts[WRONG]} wrong address (my side) · {plural(counts[ERROR], 'error')}")
    subject = (f"[EU agri tracker] Source test round 2: {counts[OK]}/{len(results)} OK, "
               f"{counts[BLOCKED]} blocked")

    lines = [
        "# Source test, round 2: EU agri tracker",
        "",
        f"Run {started:%Y-%m-%d %H:%M} UTC from "
        f"{'GitHub Actions' if os.getenv('GITHUB_ACTIONS') else 'a local machine'}"
        f" · {len(results)} checks · each source contacted once, ~{PAUSE:.1f} s apart",
        "",
        f"**{summary}**",
        "",
    ]
    order = ((BLOCKED, "Blocked"), (JS, "Needs JavaScript"), (STALE, "Old data"),
             (CHECK, "Unexpected content"), (WRONG, "Wrong address on my side (not a block)"),
             (ERROR, "Errors"))
    for status, heading in order:
        hits = [r for r in results if r["status"] == status]
        if hits:
            lines.append(f"**{heading}**")
            lines.extend(f"- {r['group']}: {r['name']}: {r['detail']}" for r in hits)
            lines.append("")

    found = [r for r in results if r.get("discovered")]
    if found:
        lines += ["**Data addresses found inside JavaScript pages**", ""]
        for r in found:
            lines.append(f"- {r['name']}:")
            lines.extend(f"  - `{u}`" for u in r["discovered"])
        lines.append("")

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

    colours = {OK: "#1a7f37", JS: "#9a6700", CHECK: "#9a6700", STALE: "#9a6700",
               BLOCKED: "#cf222e", WRONG: "#57606a", ERROR: "#cf222e"}
    findings = []
    for status, heading in order:
        hits = [r for r in results if r["status"] == status]
        if hits:
            items = "".join(f"<li>{html.escape(r['name'])}: {html.escape(r['detail'])}</li>" for r in hits)
            findings.append(f'<p style="margin:10px 0 2px;color:{colours[status]};font-weight:600">'
                            f'{heading}</p><ul style="margin:0;padding-left:20px">{items}</ul>')
    rows, current = [], None
    for r in results:
        if r["group"] != current:
            current = r["group"]
            rows.append(f'<tr><td colspan="4" style="padding:14px 6px 4px;font-weight:600;'
                        f'border-bottom:2px solid #d0d7de">{html.escape(current)}</td></tr>')
        name = html.escape(r["name"])
        if r.get("url"):
            name = f'<a href="{html.escape(r["url"])}">{name}</a>'
        extra = ""
        if r.get("discovered"):
            extra = "<br>" + "<br>".join(f'<code style="font-size:11px">{html.escape(u)}</code>'
                                         for u in r["discovered"])
        rows.append(
            "<tr>"
            f'<td style="padding:5px 6px;border-bottom:1px solid #eee">{name}<br>'
            f'<span style="color:#57606a;font-size:12px">{html.escape(r["purpose"])}</span></td>'
            f'<td style="padding:5px 6px;border-bottom:1px solid #eee;white-space:nowrap;'
            f'color:{colours[r["status"]]};font-weight:600">{html.escape(LABEL[r["status"]])}</td>'
            f'<td style="padding:5px 6px;border-bottom:1px solid #eee;font-size:13px">'
            f'{html.escape(r["detail"])}{extra}</td>'
            f'<td style="padding:5px 6px;border-bottom:1px solid #eee;font-size:12px;color:#57606a;'
            f'white-space:nowrap">{r.get("http") or "-"} · {r["seconds"]:.1f}s</td>'
            "</tr>")
    html_body = (
        '<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#1f2328;max-width:900px">'
        '<h2 style="margin:0 0 6px">Source test, round 2: EU agri tracker</h2>'
        f'<p style="margin:0 0 4px;color:#57606a">Run {started:%Y-%m-%d %H:%M} UTC · '
        f"{len(results)} checks</p>"
        f'<p style="margin:0 0 6px"><b>{html.escape(summary)}</b></p>'
        f'{"".join(findings)}<div style="height:8px"></div>'
        f'<table style="border-collapse:collapse;width:100%">{"".join(rows)}</table>'
        '<p style="color:#57606a;font-size:12px;margin-top:16px">Sent automatically by the '
        f"source test in {html.escape(REPO)}.</p></div>")
    return md, html_body, subject


# --------------------------------------------------------------------------- main

def run_one(base: dict, results: list[dict]) -> dict:
    src = dict(base)
    resolve_chained(src, results)
    raw, discovered = "", []
    if src.get("_skip"):
        status, detail, seconds, code = CHECK, src["_skip"], 0.0, None
    else:
        resp, body, seconds, err = get(src["url"], accept=src.get("accept"), params=src.get("params"))
        status, detail = classify(src, resp, body, err)
        code = resp.status_code if resp is not None else None
        text = decode(resp, body)
        if src.get("key"):
            raw = text
        if status == OK and src.get("inspect"):
            detail += f" ({inspect(src, text)})"
        if resp is not None and src.get("url") and host(resp.url or src["url"]) != host(src["url"]):
            detail += f" (redirected to {host(resp.url)})"
        if src.get("chained") == "council_doc" and status in (OK, JS):
            follow = council_followup(resp, body)
            if follow:
                detail += f"; {follow}"
        if src.get("discover") and status in (OK, JS) and resp is not None:
            discovered = discover(resp.url or src["url"], text)
            n = len(discovered)
            detail += (f"; {n} possible data address{'' if n == 1 else 'es'} found"
                       if discovered else "; no data addresses found in its scripts")
    return {
        "group": src["group"], "name": src["name"], "purpose": src["purpose"],
        "url": src.get("url"), "status": status, "detail": detail, "http": code,
        "seconds": round(seconds, 2), "key": src.get("key"), "_raw": raw, "discovered": discovered,
    }


def main() -> int:
    started = now_utc()
    results: list[dict] = []
    print(f"Running {len(SOURCES)} checks as: {USER_AGENT}\n")
    for i, base in enumerate(SOURCES):
        result = run_one(base, results)
        results.append(result)
        print(f"[{i + 1:2}/{len(SOURCES)}] {LABEL[result['status']]:28} {result['name']}: {result['detail']}")
        for u in result["discovered"]:
            print(f"        -> {u}")
        if i < len(SOURCES) - 1:
            time.sleep(PAUSE)

    md, html_body, subject = build_reports(results, started)
    mail_ok, mail_msg = send_email(subject, md, html_body)
    mail_configured = any(os.getenv(k) for k in ("SMTP_USER", "SMTP_PASSWORD", "MAIL_TO"))
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
    return 1 if (mail_configured and not mail_ok) else 0


if __name__ == "__main__":
    sys.exit(main())
