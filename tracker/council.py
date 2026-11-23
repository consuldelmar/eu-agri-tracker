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

from .common import REF_RE, Http, days_ago, ep_id, links_for, log, normalize_ref, parse_date, pdf_text, refs_in, today

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
    r"(Working Party on [A-Z][A-Za-z ,\-]{3,70}?(?:\s\([^)\n]{2,60}\))?(?=\s*(?:$|\n|-|–| are | is |\.))|"
    r"Special Committee on Agriculture|Permanent Representatives Committee \(Part [12]\)|"
    r"Agriculture and Fisheries Council|Counsellors/Attachés \([^)\n]{2,80}\)?)", re.M)
DOC_NUMBER = re.compile(r"\b(\d{4,5})/(\d{2})\b")

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


TITLE_BEFORE_REF = re.compile(
    r"((?:Amended\s+)?(?:Proposal|Draft)\s+for\s+an?\s+(?:COUNCIL|Council|REGULATION|Regulation|DIRECTIVE|Directive|"
    r"DECISION|Decision)\b(?:(?!(?:Proposal|Draft)\s+for\s+an?\s).)*)$", re.S)


def clean_title(title: str) -> str:
    """'Proposal for a REGULATION ... - Mandate for negotiations' -> 'REGULATION ...'."""
    t = re.sub(r"\s+", " ", title or "").strip()
    t = re.sub(r"^(?:Amended\s+)?(?:Proposal|Draft)\s+for\s+an?\s+", "", t, flags=re.I)
    cut = t.find(" - ", 40)
    if cut > 0:
        t = t[:cut]
    t = t.strip(" -–,;:(")
    return t[0].upper() + t[1:] if t else t


def ref_titles(text: str) -> dict[str, str]:
    """Title of each file number in a Council document: the "Subject:" of a document whose
    header names the file ("Interinstitutional File: ..."), or the title written just before
    the number in an agenda."""
    out: dict[str, str] = {}
    subject = re.search(r"Subject:\s*(.+?)(?:\n\s*-\s|\n\s*\n|Delegations will find|\n\s*I\.\s)", text or "", re.S)
    proposal_like = re.compile(r"(?i)^\s*(?:(?:amended\s+)?(?:proposal|draft)\s+for\s+an?\s|council\s+(?:regulation|"
                               r"decision|directive)|regulation\s|directive\s|decision\s)")
    if subject and proposal_like.match(subject.group(1)):
        header = refs_in((text or "")[:subject.start()])
        if 1 <= len(header) <= 3:
            title = clean_title(subject.group(1))
            if len(title) >= 20:
                for ref in header:
                    out[ref] = title[:400]
    for m in REF_RE.finditer(text or ""):
        ref = normalize_ref(m.group(0))
        before = re.sub(r"\s+", " ", text[max(0, m.start() - 700):m.start()])
        t = TITLE_BEFORE_REF.search(before)
        if not t:
            continue
        title = re.sub(r"(?i)\s*[-–(]*\s*(interinstitutional files?:?|\bST\b)\s*$", "", t.group(1))
        title = clean_title(title)
        if 20 <= len(title) <= 400 and ref and ref not in out:
            out[ref] = title
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
    if state.get("version") != 2:
        # Version 2 also keeps the title written next to each file number: read cited documents again
        for d in docs.values():
            if d.get("refs") and "ref_titles" not in d:
                d["read"] = False
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
        if rec.get("kind") == "agenda" and rec.get("read") and "doc_numbers" not in rec:
            rec["read"] = False                # read again to collect the document numbers it cites
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
        rec["ref_titles"] = {**rec.get("ref_titles", {}), **ref_titles(text)}
        rec["read"] = result in ("ok", "not a PDF") or result.startswith("HTTP 4")
        if rec["kind"] == "agenda" or re.search(r"(?i)provisional agenda", text[:3000]):
            rec["kind"] = "agenda"
            rec["meeting_date"] = meeting_date(text[:4000], rec.get("date") or "") or rec.get("date")
            plain = re.sub(r"\bCM\s+\d{4,5}/\d{2}", " ", text)    # CM numbers are invitations, not documents
            rec["doc_numbers"] = sorted({f"{a}/{b}" for a, b in DOC_NUMBER.findall(plain)})
            rec["cm_clean"] = True
            if rec["body"] == "Consejo":
                name = MEETING_NAME.search(rec.get("title") or "") or MEETING_NAME.search(text[:1500])
                if name:
                    raw_name = re.sub(r"\s+", " ", name.group(1)).strip()
                    rec["body"] = BODY_ES.get(raw_name.split(" (")[0], raw_name.replace(
                        "Working Party on", "Grupo de trabajo:"))
            m = re.search(r"(?i)(\d{3,4}(?:st|nd|rd|th) meeting of the [A-Za-z ]+)", text[:3000])
            if m:
                rec["title"] = m.group(1).strip()
        if not rec.get("step"):
            rec["step"] = step_of(text[:2500])

    # Documents that agriculture & fisheries agendas cite but no feed brought, e.g. a Commission
    # proposal sent to the Council months earlier: read each one once to learn its file number
    checked: dict = dict(state.get("numbers_checked", {}))
    read += read_cited(http, docs, checked, budget - read, status)

    # Agendas cite Council documents by number ("13143/26"): give them those documents' files
    by_number: dict[str, set] = {}
    for d in docs.values():
        m = re.match(r"[A-Z]+ (\d{4,5}/\d{2})", d.get("code") or "")
        if m and d.get("refs") and d.get("kind") != "agenda":
            by_number.setdefault(m.group(1), set()).update(d["refs"])
    for d in docs.values():
        if d.get("kind") == "agenda" and d.get("doc_numbers"):
            cited = set()
            for n in d["doc_numbers"]:
                cited |= by_number.get(n, set())
            d["refs"] = sorted(set(d.get("refs", [])) | cited)
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
    checked = {k: v for k, v in checked.items() if v >= cutoff}
    return {"calendar": calendar, "docs": docs, "press": press, "status": status, "first_run_done": True,
            "version": 2, "numbers_checked": checked}


SENT_DATE = re.compile(r"Brussels,\s*(\d{1,2}\s+[A-Z][a-z]+\s+20\d\d|\d{1,2}\.\d{1,2}\.20\d\d)")


def read_cited(http: Http, docs: dict, checked: dict, budget: int, status: dict) -> int:
    """Read the first pages of documents cited by recent agriculture & fisheries agendas (not
    Coreper, whose agendas cover every policy) that the tracker does not have yet. Returns the
    number of PDFs read. Documents that are not public (LIMITE) are noted and not tried again."""
    known = set()
    for d in docs.values():
        m = re.match(r"[A-Z]+ (\d{4,5}/\d{2})", d.get("code") or "")
        if m:
            known.add(m.group(1))
    year = int(today()[2:4])
    wanted: dict[str, str] = {}
    for d in sorted(docs.values(), key=lambda d: d.get("meeting_date") or "", reverse=True):
        if d.get("kind") != "agenda" or (d.get("body") or "").startswith("Coreper") or \
                (d.get("meeting_date") or "") < days_ago(150):
            continue
        own = re.match(r"[A-Z]+ (\d{4,5}/\d{2})", d.get("code") or "")
        for n in d.get("doc_numbers", []):
            if n in known or n in checked or n in wanted or (own and own.group(1) == n):
                continue
            if len(n.split("/")[0]) == 4 and not d.get("cm_clean"):
                continue                                # older reading: could be a CM number
            if int(n.split("/")[1]) in (year, year - 1):
                wanted[n] = d.get("meeting_date") or ""
    done = found = 0
    for n in wanted:
        if done >= budget:
            break
        num, yy = n.split("/")
        doc_id = f"ST-{num}-20{yy}-INIT"
        url = f"https://data.consilium.europa.eu/doc/document/{doc_id}/en/pdf"
        text, result = read_pdf(http, url, pages=2)
        done += 1
        if result != "ok" and not result.startswith("HTTP 4") and result != "not a PDF":
            continue                                    # server trouble: try again next run
        checked[n] = today()
        refs = refs_in(text[:6000]) if result == "ok" else []
        if not refs:
            continue
        subject = re.search(r"Subject:\s*(.+?)(?:\n\s*\n|Delegations will find)", text, re.S)
        title = re.sub(r"\s+", " ", subject.group(1)).strip()[:600] if subject else f"Documento {n}"
        sent = SENT_DATE.search(text[:3000])
        docs[doc_id] = {"id": doc_id, "code": f"ST {n}", "kind": "document", "body": "Consejo", "title": title,
                        "url": url, "date": (parse_date(sent.group(1)) if sent else None) or wanted[n],
                        "step": step_of(title), "refs": refs, "ref_titles": ref_titles(text), "read": True,
                        "via_agenda": True}
        found += 1
    status["cited_documents"] = f"{done} read ({found} with a file number), {max(0, len(wanted) - done)} left"
    return done


def council_only_record(ref: str, docs: list[dict]) -> dict:
    """A file known only from Council documents (e.g. fishing opportunities, art. 43(3) TFEU)."""
    docs = sorted(docs, key=lambda d: d.get("date") or "")
    titles = [d.get("ref_titles", {}).get(ref) for d in docs if d.get("ref_titles", {}).get(ref)]
    subjects = [d.get("title", "") for d in docs if d.get("kind") != "agenda"
                and re.match(r"(?i)(proposal|draft|council (regulation|decision))", d.get("title", ""))
                and len(d.get("refs", [])) == 1]
    title = titles[0] if titles else (clean_title(subjects[0]) if subjects else f"Expediente del Consejo {ref}")
    events = []
    papers = [d for d in docs if d.get("kind") != "agenda"]
    if papers:
        events.append({"date": papers[0].get("date"), "type": "COUNCIL_FIRST_DOCUMENT",
                       "title": "Primer documento en el Consejo", "docs": [
                           {"id": papers[0].get("code"), "title": papers[0].get("code"), "url": papers[0].get("url")}]})
    for d in papers:
        if d.get("step"):
            events.append({"date": d.get("date"), "type": "COUNCIL_STEP", "title": f"Consejo: {d['step']}",
                           "docs": [{"id": d.get("code"), "title": d.get("code"), "url": d.get("url")}]})
    adopted = any(d.get("step") == "Adopción por el Consejo" for d in papers)
    last = max((d.get("date") or "" for d in docs), default="")
    links = {k: v for k, v in links_for(ref).items() if k == "eurlex"}
    return {
        "ref": ref, "id": ep_id(ref), "type": ref[-4:-1], "title": title, "title_en": title,
        "stage": events[-1]["title"] if events else "En el Consejo", "ongoing": not adopted and last >= days_ago(300),
        "committees": [], "people": [], "started": docs[0].get("date") if docs else None, "last_activity": last or None,
        "events": events, "docs": [], "council_only": True, "links": links,
        "scope": ["Citado en documentos de agricultura y pesca del Consejo (sin expediente en el Parlamento)"],
    }
