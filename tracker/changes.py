"""Compare yesterday's and today's records and describe what changed, in plain words.

Each change has an importance:
  high    a real step: committee vote, trilogue, mandate, general approach, agreement, plenary
          vote, adoption, publication, a date announced on an agenda, a new rapporteur
  normal  a new file, Council compromise texts and progress reports, shadow rapporteurs
  low     documents and minor procedural entries (amendments tabled, other Council papers)
Old material that only appears now (documents released years later, backfilled entries) is
not reported as news.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re

from .common import days_ago, today

KEY_EVENTS = {
    "COMMITTEE_ADOPTING_REPORT", "COMMITTEE_DECISION_INTERINSTITUTIONAL_NEGOTIATIONS",
    "PLENARY_ENDORSE_COMMITTEE_INTERINSTITUTIONAL_NEGOTIATIONS", "PLENARY_AMEND_PROPOSAL",
    "PLENARY_REFER_COMMITTEE_INTERINSTITUTIONAL_NEGOTIATIONS", "INTERINSTITUTIONAL_NEGOTIATION",
    "COMMITTEE_APPROVE_PROVISIONAL_AGREEMENT", "PLENARY_ADOPT_POSITION", "PLENARY_ADOPT_RESOLUTION",
    "PLENARY_APPROVE_CONSENT", "PLENARY_APPROVE_COUNCIL_POSITION", "PLENARY_REJECT", "PLENARY_DECISION",
    "PLENARY_DEBATE", "SIGNATURE", "PUBLICATION_OFFICIAL_JOURNAL", "COMMITTEE_TABLING_REPORT", "REFERRAL",
}
KEY_STEPS = {"Mandato de negociación con el PE", "Orientación general", "Orientación general o mandato parcial",
             "Texto final de compromiso (acuerdo con el PE)", "Acuerdo provisional con el PE",
             "Adopción por el Consejo", "Posición del Consejo"}
NEWS_DAYS_EVENTS = 45        # an EP entry older than this, appearing only now, is backfill
NEWS_DAYS_COUNCIL = 21       # same for Council documents (by registration date)


def _key(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:12]


def change(ref: str, kind: str, text: str, url: str = "", date: str | None = None,
           importance: str = "normal") -> dict:
    return {"id": _key(ref, kind, text), "date": date or today(), "ref": ref, "kind": kind,
            "text": text, "url": url, "importance": importance}


def _people(rec: dict, role: str) -> set[str]:
    return {f"{p.get('name')} ({p.get('group') or '?'}, {p.get('committee') or '?'})"
            for p in rec.get("people", []) if p.get("role") == role}


def _event_key(e: dict) -> str:
    return f"{e.get('date')}|{e.get('type') or ''}|{e.get('title') or ''}"


def _old_council_doc(d: dict) -> bool:
    """Documents registered long ago (e.g. 'ST 9876/22') or more than three weeks before today."""
    m = re.search(r"/(\d{2})\b", d.get("code") or "")
    if m and 2000 + int(m.group(1)) < dt.date.today().year - 1:
        return True
    return bool(d.get("date")) and d["date"] < days_ago(NEWS_DAYS_COUNCIL)


def diff_record(old: dict | None, new: dict) -> list[dict]:
    """Changes in one file between two runs."""
    ref = new["ref"]
    out: list[dict] = []
    if old is None:
        out.append(change(ref, "new", f"Nuevo expediente en seguimiento: {new.get('title') or ref}",
                          new.get("links", {}).get("oeil", ""), importance="normal"))
        return out

    # New steps in the Parliament
    old_events = {_event_key(e) for e in old.get("events", [])}
    event_titles = set()
    for e in new.get("events", []):
        if _event_key(e) in old_events or (e.get("date") or "") < days_ago(NEWS_DAYS_EVENTS):
            continue
        doc_url = next((d.get("url") for d in e.get("docs", []) if d.get("url")), "")
        imp = "high" if e.get("type") in KEY_EVENTS else "low"
        out.append(change(ref, "event", f"{e.get('date')}: {e.get('title') or e.get('type')}", doc_url,
                          date=e.get("date"), importance=imp))
        event_titles.add(e.get("title"))

    # Stage change, unless it only repeats an event reported just above
    if (old.get("stage") or "") != (new.get("stage") or "") and new.get("stage") \
            and new.get("stage") not in event_titles:
        out.append(change(ref, "stage", f"Fase: {old.get('stage') or '(sin fase)'} → {new.get('stage')}",
                          new.get("links", {}).get("oeil", ""), importance="high"))
    if bool(old.get("ongoing")) != bool(new.get("ongoing")):
        out.append(change(ref, "status", "Expediente concluido" if not new.get("ongoing") else "Expediente reabierto",
                          importance="high"))

    # New documents in the Parliament (minor: the steps above already carry the key documents)
    old_docs = {d.get("url") or d.get("title") for d in old.get("docs", [])}
    for d in new.get("docs", []):
        if (d.get("url") or d.get("title")) not in old_docs and (d.get("date") or "") >= days_ago(NEWS_DAYS_EVENTS):
            out.append(change(ref, "doc", f"Nuevo documento: {d.get('title') or d.get('url')}", d.get("url", ""),
                              date=d.get("date"), importance="low"))

    # People
    for role, label, imp in (("rapporteur", "Ponente", "high"), ("shadow", "Ponente alternativo", "normal"),
                             ("opinion_rapporteur", "Ponente de opinión", "normal")):
        for p in sorted(_people(new, role) - _people(old, role)):
            out.append(change(ref, "people", f"{label}: {p}", importance=imp))
        for p in sorted(_people(old, role) - _people(new, role)):
            out.append(change(ref, "people", f"Ya no figura como {label.lower()}: {p}", importance="normal"))

    # Dates announced on an agenda (plenary, committee, Council)
    old_sched = {s.get("id") for s in old.get("scheduled", [])}
    for s in new.get("scheduled", []):
        if s.get("id") not in old_sched and (s.get("date") or "") >= today():
            out.append(change(ref, "scheduled", f"Previsto: {s.get('body')}, {s.get('date')} — {s.get('title')}",
                              s.get("url", ""), importance="high"))

    # Council documents (agendas are reported above, as announced dates)
    old_cdocs = {d.get("id") for d in old.get("council_docs", [])}
    for d in new.get("council_docs", []):
        if d.get("id") in old_cdocs or d.get("kind") == "agenda" or _old_council_doc(d):
            continue
        step = d.get("step")
        if step in KEY_STEPS:
            imp, text = "high", f"Consejo: {step} — {d.get('code')}"
        elif step:
            imp, text = "normal", f"Consejo: {step} — {d.get('code')}"
        else:
            imp, text = "low", f"Documento del Consejo: {(d.get('title') or '')[:140]} — {d.get('code')}"
        out.append(change(ref, "council_doc", text, d.get("url", ""), date=d.get("date"), importance=imp))
    return out


def diff_all(old: dict, new: dict) -> list[dict]:
    """old/new: {ref: record}. Files that leave the scope are not reported."""
    out: list[dict] = []
    for ref, rec in new.items():
        out.extend(diff_record(old.get(ref), rec))
    return out
