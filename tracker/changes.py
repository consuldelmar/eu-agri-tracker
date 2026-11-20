"""Compare yesterday's and today's records and describe what changed, in plain words."""

from __future__ import annotations

import hashlib

from .common import today


def _key(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:12]


def change(ref: str, kind: str, text: str, url: str = "", date: str | None = None) -> dict:
    return {"id": _key(ref, kind, text), "date": date or today(), "ref": ref, "kind": kind,
            "text": text, "url": url}


def _people(rec: dict, role: str) -> set[str]:
    return {f"{p.get('name')} ({p.get('group') or '?'}, {p.get('committee') or '?'})"
            for p in rec.get("people", []) if p.get("role") == role}


def _event_key(e: dict) -> str:
    return f"{e.get('date')}|{e.get('type') or ''}|{e.get('title') or ''}"


def diff_record(old: dict | None, new: dict) -> list[dict]:
    """Changes in one file between two runs."""
    ref = new["ref"]
    out: list[dict] = []
    if old is None:
        out.append(change(ref, "new", f"Nuevo expediente en seguimiento: {new.get('title') or ref}",
                          new.get("links", {}).get("oeil", "")))
        return out

    if (old.get("stage") or "") != (new.get("stage") or "") and new.get("stage"):
        out.append(change(ref, "stage",
                          f"Fase: {old.get('stage') or '(sin fase)'} → {new.get('stage')}",
                          new.get("links", {}).get("oeil", "")))
    if (old.get("status") or "") != (new.get("status") or "") and new.get("status"):
        out.append(change(ref, "status", f"Estado: {old.get('status') or '(sin estado)'} → {new.get('status')}"))

    old_events = {_event_key(e) for e in old.get("events", [])}
    for e in new.get("events", []):
        if _event_key(e) not in old_events:
            doc_url = next((d.get("url") for d in e.get("docs", []) if d.get("url")), "")
            out.append(change(ref, "event", f"{e.get('date')}: {e.get('title') or e.get('type')}", doc_url,
                              date=e.get("date")))

    old_docs = {d.get("url") or d.get("title") for d in old.get("docs", [])}
    for d in new.get("docs", []):
        if (d.get("url") or d.get("title")) not in old_docs:
            out.append(change(ref, "doc", f"Nuevo documento: {d.get('title') or d.get('url')}", d.get("url", ""),
                              date=d.get("date")))

    for role, label in (("rapporteur", "Ponente"), ("shadow", "Ponente alternativo"),
                        ("opinion_rapporteur", "Ponente de opinión")):
        added = _people(new, role) - _people(old, role)
        removed = _people(old, role) - _people(new, role)
        for p in sorted(added):
            out.append(change(ref, "people", f"{label}: {p}"))
        for p in sorted(removed):
            out.append(change(ref, "people", f"Ya no figura como {label.lower()}: {p}"))

    old_sched = {s.get("id") for s in old.get("scheduled", [])}
    for s in new.get("scheduled", []):
        if s.get("id") not in old_sched and (s.get("date") or "") >= today():
            out.append(change(ref, "scheduled", f"Previsto: {s.get('body')}, {s.get('date')} — {s.get('title')}",
                              s.get("url", "")))

    old_cdocs = {d.get("id") for d in old.get("council_docs", [])}
    for d in new.get("council_docs", []):
        if d.get("id") not in old_cdocs:
            what = d.get("step") or (d.get("title") or "")[:160]
            if d.get("kind") == "agenda":
                when = d.get("meeting_date") or d.get("date")
                text = f"En el orden del día de {d.get('body')} ({when}) — {d.get('code')}"
            else:
                text = f"Consejo: {what} — {d.get('code')}"
            out.append(change(ref, "council_doc", text, d.get("url", ""), date=d.get("date")))
    return out


def diff_all(old: dict, new: dict) -> list[dict]:
    """old/new: {ref: record}. Files that leave the scope are not reported."""
    out: list[dict] = []
    for ref, rec in new.items():
        out.extend(diff_record(old.get(ref), rec))
    return out
