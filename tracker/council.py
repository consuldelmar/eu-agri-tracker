"""Council of the EU: meetings, agendas, documents and press releases, via its RSS feeds.

The Council's web pages block automated reading, but its RSS feeds and its document
server (data.consilium.europa.eu) do not. Register feeds give the document number as
title and its subject as description, e.g. "... - Mandate for negotiations with the
European Parliament". File numbers such as 2025/0410(COD) are on the first page of the
PDF ("Interinstitutional files: ..."), so new PDFs are read to link them to files.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from .common import Http, days_ago, log, parse_date, pdf_text, refs_in, today

BASE = "https://www.consilium.europa.eu/en"

MEETING_FEEDS = {
    "agri": ("Consejo AGRIFISH", f"{BASE}/rss/meetings.ashx?cat=agri"),
    "wp-agri": ("Grupo de trabajo del Consejo", f"{BASE}/rss/meetings.ashx?cat=wp-agri"),
    "coreper": ("Coreper", f"{BASE}/rss/meetings.ashx?cat=coreper"),
}
AGENDA_FEEDS = {
    "AGNDCSA": ("Comité Especial de Agricultura (CEA)", f"{BASE}/register/rss/AGNDCSA.xml"),
    "AGNDCRP1": ("Coreper I", f"{BASE}/register/rss/AGNDCRP1.xml"),
}
DOC_FEED = f"{BASE}/register/rss/THMAF.xml"
PRESS_FEED = f"{BASE}/rss/pressreleases.ashx"

SKIP_MEETING = re.compile(r"(?i)listening room|briefing|press conference|doorstep|media")
AGRI_WORDS = re.compile(
    r"(?i)agricult|farm|fisher|fishing|aquacult|\bfood\b|rural|\bCAP\b|common agricultural|forest|animal|"
    r"plant health|pesticid|\bseeds?\b|\bwine|sugar|\bmilk|dairy|livestock|veterinar|phytosanitar|mercosur|"
    r"fertili[sz]|\bsoil|genomic techniques|organic|deforestation|\bcohesion|regional (?:policy|development)")
AGRI_CATEGORIES = re.compile(r"(?i)food and farming|fisheries|agricultur|forestry|animal|plant|cohesion|"
                             r"regional policy|rural")
MEETING_NAME = re.compile(
    r"(Working Party on [A-Z][A-Za-z ,\-]{3,70}(?:\([^)]{2,60}\))?|Special Committee on Agriculture|"
    r"Permanent Representatives Committee \(Part [12]\)|Agriculture and Fisheries Council)")

# Council steps recognised in document subjects and press releases (checked in this order)
STEPS = [
    (re.compile(r"(?i)partial (?:general approach|mandate)"), "Orientación general o mandato parcial"),
    (re.compile(r"(?i)general approach"), "Orientación general"),
    (re.compile(r"(?i)mandate for negotiations|negotiating mandate"), "Mandato de negociación con el PE"),
    (re.compile(r"(?i)final compromise text|with a view to agreement|analysis of the final compromise"),
     "Texto final de compromiso (acuerdo con el PE)"),
    (re.compile(r"(?i)provisional (?:political )?agreement|deal with (?:the )?(?:european )?parliament|"
                r"strike deal|reach deal"), "Acuerdo provisional con el PE"),
    (re.compile(r"(?i)position (?:of the council )?at first reading|council'?s position"), "Posición del Consejo"),
    (re.compile(r"(?i)adoption of the legislative act|final green light|council adopts"), "Adopción por el Consejo"),
    (re.compile(r"(?i)presidency compromise|compromise text|revised text"), "Texto de compromiso de la Presidencia"),
    (re.compile(r"(?i)progress report"), "Informe de situación"),
]


BODY_ES = {"Special Committee on Agriculture": "Comité Especial de Agricultura (CEA)",
           "Permanent Representatives Committee": "Coreper", "Agriculture and Fisheries Council": "Consejo AGRIFISH"}


def latest_agendas(docs: list[dict]) -> list[dict]:
    """One agenda per meeting (body + date): revisions replace the original."""
    best: dict[tuple, dict] = {}
    for d in docs:
        if d.get("kind") != "agenda":
            continue
        key = (d.get("body"), d.get("meeting_date"))
        rev = int((re.search(r"REV-(\d+)", d.get("id", "")) or [0, 0])[1])
        cur = best.get(key)
        if cur is None or rev > cur[0] or (rev == cur[0] and (d.get("date") or "") > (cur[1].get("date") or "")):
            best[key] = (rev, d)
    out = []
    for (_, d) in best.values():
        refs = sorted({r for x in docs if x.get("kind") == "agenda" and (x.get("body"), x.get("meeting_date")) ==
                       (d.get("body"), d.get("meeting_date")) for r in x.get("refs", [])})
        out.append({**d, "refs": refs})
    return out


def step_of(text: str) -> str | None:
    for pattern, label in STEPS:
        if pattern.search(text or ""):
            return label
    return None


# --------------------------------------------------------------------------- RSS

def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_feed(xml_text: str) -> list[dict]:
    """RSS 2.0 or Atom -> [{title, link, date, description, categories, guid}]."""
    try:
        root = ET.fromstring(xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text)
    except ET.ParseError as exc:
        log(f"  feed could not be read: {exc}")
        return []
    items = []
    for node in root.iter():
        if _local(node.tag) not in ("item", "entry"):
            continue
        rec: dict = {"categories": []}
        for child in node:
            name = _local(child.tag)
            value = (child.text or "").strip()
            if name == "title":
                rec["title"] = value
            elif name == "link":
                rec["link"] = value or child.attrib.get("href", "")
            elif name in ("pubDate", "updated", "published", "date") and not rec.get("date"):
                rec["date"] = parse_date(value)
            elif name in ("description", "summary", "content"):
                rec["description"] = re.sub(r"<[^>]+>", " ", value)[:3000].strip()
            elif name == "category":
                rec["categories"].append(value or child.attrib.get("term", ""))
            elif name in ("guid", "id"):
                rec["guid"] = value
        if rec.get("title") or rec.get("link"):
            items.append(rec)
    return items


def doc_id_from(url: str, fallback: str = "") -> str:
    m = re.search(r"/doc/document/([^/]+)/", url or "")
    return m.group(1) if m else (fallback or url)


def doc_code(doc_id: str) -> str:
    """'ST-13143-2026-INIT' -> 'ST 13143/26'."""
    m = re.fullmatch(r"([A-Z]+)-(\d+)-(\d{4})-([A-Z0-9-]+)", doc_id or "")
    if not m:
        return doc_id
    suffix = "" if m.group(4) == "INIT" else " " + m.group(4).replace("-", " ")
    return f"{m.group(1)} {m.group(2)}/{m.group(3)[2:]}{suffix}"


DATE_IN_TEXT = re.compile(
    r"\b(\d{1,2})(?:\s*(?:-|–|and)\s*\d{1,2})?\s+(January|February|March|April|May|June|July|August|"
    r"September|October|November|December)\s+((?:19|20)\d{2})\b", re.I)


def meeting_date(text: str, published: str) -> str | None:
    """First date in an agenda that is after its publication date (the meeting), else None."""
    for m in DATE_IN_TEXT.finditer(text or ""):
        d = parse_date(f"{m.group(1)} {m.group(2)} {m.group(3)}")
        if d and d > (published or "0000"):
            return d
    return None


def read_pdf(http: Http, url: str, pages: int) -> tuple[str, str]:
    """Download a Council document -> (text of first pages, status)."""
    r = http.get(url, timeout=90, tries=2)
    if r is None:
        return "", "unreachable"
    if r.status_code != 200:
        return "", f"HTTP {r.status_code}"
    if r.content[:5] != b"%PDF-":
        return r.text[:200000], "not a PDF"
    return pdf_text(r.content, pages), "ok"


# --------------------------------------------------------------------------- collector

def collect(cfg: dict, http: Http, state: dict) -> dict:
    """Returns {calendar, docs, press, status, first_run_done}."""
    docs: dict = dict(state.get("docs", {}))
    first_run = not state.get("first_run_done")
    window = days_ago(cfg["council"]["first_run_days"]) if first_run else days_ago(60)
    budget = int(cfg["council"]["max_pdfs_per_run"])
    calendar, press, status = [], [], {}

    # Meetings: AGRIFISH, agriculture/fisheries working parties, Coreper (date = a10:updated)
    for key, (label, url) in MEETING_FEEDS.items():
        r = http.get(url)
        if r is None or r.status_code != 200:
            status[key] = f"failed ({r.status_code if r is not None else 'no answer'})"
            continue
        items = parse_feed(r.text)
        kept = 0
        for it in items:
            title = re.sub(r"\s*\(\d+\)\s*$", "", it.get("title", "")).strip()
            if not it.get("date") or SKIP_MEETING.search(title):
                continue
            possible = bool(re.match(r"(?i)\(?\s*poss\.?\s*\)?", title))
            title = re.sub(r"(?i)^\(?\s*poss\.?\s*\)?\s*", "", title)
            body = label
            if key == "coreper":
                body = "Coreper I" if re.search(r"Coreper 1\b", title) else "Coreper II"
            elif key == "wp-agri":
                title = re.sub(r"(?i)^(att\.|informal vtc\.?)\s*", "", title).replace("Wp On", "Grupo de trabajo:")
            calendar.append({
                "id": f"council-{key}-{it.get('guid') or it.get('link')}", "date": it["date"], "source": "council",
                "body": body, "title": title + (" (posible)" if possible else ""),
                "description": (it.get("description") or "")[:600], "url": it.get("link", ""), "refs": [],
            })
            kept += 1
        status[key] = f"{kept} meetings"

    # Agendas and documents: collect new items, then read their PDFs within the budget
    pending: list[dict] = []

    def take(it: dict, kind: str, body: str) -> None:
        did = doc_id_from(it.get("link", ""), it.get("guid", ""))
        date = it.get("date") or ""
        if date and date < window:
            return
        subject = it.get("description") or it.get("title", "")
        rec = docs.get(did)
        if rec is None:
            rec = {"id": did, "code": doc_code(did), "kind": kind, "body": body, "title": subject,
                   "url": it.get("link", ""), "date": date, "step": step_of(subject),
                   "refs": refs_in(subject), "read": False}
            docs[did] = rec
        if not rec.get("read"):
            pending.append(rec)

    for key, (label, url) in AGENDA_FEEDS.items():
        r = http.get(url)
        if r is None or r.status_code != 200:
            status[key] = f"failed ({r.status_code if r is not None else 'no answer'})"
            continue
        items = parse_feed(r.text)
        status[key] = f"{len(items)} documents in feed"
        for it in items:
            take(it, "agenda" if it.get("title", "").startswith("CM") else "document", label)

    r = http.get(DOC_FEED)
    if r is None or r.status_code != 200:
        status["THMAF"] = f"failed ({r.status_code if r is not None else 'no answer'})"
    else:
        items = parse_feed(r.text)
        status["THMAF"] = f"{len(items)} documents in feed"
        for it in items:
            take(it, "document", "Consejo")

    # Agendas first (they announce meetings), then the newest documents
    agendas = [d for d in pending if d["kind"] == "agenda"]
    others = sorted((d for d in pending if d["kind"] != "agenda"), key=lambda d: d.get("date") or "", reverse=True)
    read = 0
    for rec in agendas + others:
        if read >= budget:
            break
        if "data.consilium.europa.eu" not in rec.get("url", ""):
            rec["read"] = True
            continue
        text, result = read_pdf(http, rec["url"], pages=8 if rec["kind"] == "agenda" else 2)
        read += 1
        rec["refs"] = sorted(set(rec.get("refs", [])) | set(refs_in(text)))
        rec["read"] = result in ("ok", "not a PDF") or result.startswith("HTTP 4")
        if rec["kind"] == "agenda" or re.search(r"(?i)provisional agenda", text[:3000]):
            rec["kind"] = "agenda"
            rec["meeting_date"] = meeting_date(text[:4000], rec.get("date") or "") or rec.get("date")
            name = MEETING_NAME.search(text[:4000])
            if name and rec["body"] == "Consejo":
                rec["body"] = BODY_ES.get(name.group(1).split(" (")[0], name.group(1).replace(
                    "Working Party on", "Grupo de trabajo:").strip())
            m = re.search(r"(?i)(\d{3,4}(?:st|nd|rd|th) meeting of the [A-Za-z ]+)", text[:3000])
            if m:
                rec["title"] = m.group(1).strip()
        if not rec.get("step"):
            rec["step"] = step_of(text[:2500])
    left = sum(1 for d in pending if not d.get("read"))
    status["pdfs"] = f"{read} read, {left} left for the next run"
    log(f"  Council: {status}")

    for rec in latest_agendas(list(docs.values())):
        if rec.get("meeting_date") and rec.get("refs"):
            calendar.append({
                "id": f"council-agenda-{rec['id']}", "date": rec["meeting_date"], "source": "council",
                "body": rec["body"], "title": f"Orden del día ({rec['code']})", "url": rec["url"],
                "refs": rec["refs"],
            })

    # Press releases on agri/fish/regional topics, flagging Council steps
    r = http.get(PRESS_FEED)
    if r is not None and r.status_code == 200:
        for it in parse_feed(r.text):
            text = " ".join([it.get("title", ""), it.get("description", "")])
            if not (AGRI_WORDS.search(text) or AGRI_CATEGORIES.search(" ".join(it["categories"]))):
                continue
            press.append({"id": f"press-{it.get('guid') or it.get('link')}", "date": it.get("date") or today(),
                          "title": it.get("title", ""), "summary": it.get("description", ""),
                          "url": it.get("link", ""), "step": step_of(text), "refs": refs_in(text),
                          "categories": it["categories"]})
        status["press"] = f"{len(press)} relevant press releases"

    cutoff = days_ago(400)
    docs = {k: v for k, v in docs.items() if (v.get("date") or today()) >= cutoff}
    return {"calendar": calendar, "docs": docs, "press": press, "status": status, "first_run_done": True}
