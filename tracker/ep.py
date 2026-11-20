"""European Parliament (Open Data Portal API v2): files, people, plenary and committee agendas.

First load: lists every procedure of the tracked types, keeps those started since
`since_year`, and reads each one (resumes on the next run if it runs out of budget).
Daily: reads the change feed and re-reads the files that changed, plus followed files.

Structure of a file record (GET /procedures/{id}), as published by the API:
  label "2024/0319(COD)", process_title {lang: text}, process_type ".../COD",
  had_participation [ {participation_role ".../COMMITTEE_LEAD", had_participant_organization ["org/AGRI"]},
                      {participation_role ".../RAPPORTEUR", had_participant_person ["person/256918"],
                       participation_in_name_of "org/AGRI", politicalGroup "org/PPE", precedenceOrder 1701}, ...],
  consists_of [ {activity_date, had_activity_type ".../COMMITTEE_ADOPTING_REPORT",
                 based_on_a_realization_of ["eli/dl/doc/A-10-2025-0161"], ...}, ...],
  was_scheduled_in [ {activity_date, had_activity_type ".../PLENARY_VOTE", notation_agendaPoint "V-49"}, ...]
"""

from __future__ import annotations

import json
import re
from typing import Any

from .common import (BudgetExceeded, Http, ep_id, links_for, log, normalize_ref, pdf_text, refs_in, today)

API = "https://data.europarl.europa.eu/api/v2"
LD = {"format": "application/ld+json"}
PROC_ID = re.compile(r"eli/dl/proc/(\d{4}-\d{4}[A-Z]?)")
DOCEO = "https://www.europarl.europa.eu/doceo/document"
AGENDA_PDF = ("https://redmapl3.europarl.europa.eu/RedmapFront/media/reds_iCmOj/"
              "{c}-OJ-{d}-1/{c}-OJ-{d}-1_en.pdf")

GROUPS = {"PPE": "PPE", "S-D": "S&D", "PFE": "PfE", "ECR": "ECR", "RENEW": "Renew", "VERTS-ALE": "Verts/ALE",
          "THE-LEFT": "The Left", "GUE-NGL": "The Left", "ESN": "ESN", "NI": "NI", "ID": "ID", "EFDD": "EFDD",
          "ENF": "ENF", "ALDE": "ALDE"}

# Activity type -> (Spanish label, counts as a "stage" milestone)
ACTIVITY = {
    "REFERRAL": ("Remitido a comisión", True),
    "COMMITTEE_TABLING_REPORT": ("Proyecto de informe presentado en comisión", True),
    "COMMITTEE_TABLING_AMENDMENT": ("Enmiendas presentadas en comisión", False),
    "COMMITTEE_ADOPTING_OPINION": ("Opinión aprobada en comisión", False),
    "COMMITTEE_ADOPTING_REPORT": ("Informe aprobado en comisión", True),
    "COMMITTEE_DECISION_INTERINSTITUTIONAL_NEGOTIATIONS": ("Comisión decide abrir negociaciones", True),
    "TABLING_PLENARY": ("Presentado al Pleno", False),
    "PLENARY_DEBATE": ("Debate en el Pleno", True),
    "PLENARY_VOTE": ("Votación en el Pleno", False),
    "PLENARY_VOTE_RESULTS": ("Resultados de votación en el Pleno", False),
    "PLENARY_AMEND": ("Enmiendas aprobadas en el Pleno", True),
    "PLENARY_REFER_COMMITTEE_INTERINSTITUTIONAL_NEGOTIATIONS": ("Devuelto a comisión para negociaciones (trílogos)",
                                                                True),
    "INTERINSTITUTIONAL_NEGOTIATION": ("Trílogo", True),
    "COMMITTEE_APPROVE_PROVISIONAL_AGREEMENT": ("Acuerdo provisional aprobado en comisión", True),
    "PLENARY_ADOPT_POSITION": ("Posición del Parlamento aprobada en el Pleno", True),
    "PLENARY_ADOPT_RESOLUTION": ("Resolución aprobada en el Pleno", True),
    "PLENARY_APPROVE_CONSENT": ("Aprobación del Parlamento", True),
    "PLENARY_REJECT": ("Rechazado en el Pleno", True),
    "SIGNATURE": ("Acto firmado", True),
    "PUBLICATION_OFFICIAL_JOURNAL": ("Publicado en el Diario Oficial", True),
    "PLENARY_OUTCOME": ("Resultado en el Pleno", True),
}
FINISHED = re.compile(r"PUBLICATION_OFFICIAL_JOURNAL|WITHDRAW|LAPSE|PROCEDURE_COMPLETED|CLOSED")
PLENARY_END = re.compile(r"PLENARY_(ADOPT|APPROVE|REJECT|CONSENT|OUTCOME)")


# --------------------------------------------------------------------------- helpers

def tail(v: Any) -> str:
    if isinstance(v, dict):
        v = v.get("id") or v.get("@id") or ""
    return str(v or "").rstrip("/").rsplit("/", 1)[-1]


def listify(v: Any) -> list:
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def text(v: Any, lang: str) -> str:
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    if isinstance(v, list):
        return text(v[0], lang) if v else ""
    if isinstance(v, dict):
        for key in (lang, "en", "fr", "de"):
            if v.get(key):
                return text(v[key], lang)
        return next((x for x in v.values() if isinstance(x, str) and x), "")
    return str(v)


def items_of(payload: Any) -> list[dict]:
    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        return [x for x in payload["data"] if isinstance(x, dict)]
    return []


def to_int(v: Any) -> int | None:
    """precedenceOrder can be a number, a string or a list of them."""
    if isinstance(v, list):
        v = v[0] if v else None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def humanize(code: str) -> str:
    return code.replace("_", " ").capitalize()


def plus_days(date: str, days: int) -> str:
    import datetime as dt
    return (dt.date.fromisoformat(date) + dt.timedelta(days=days)).isoformat()


# --------------------------------------------------------------------------- documents

def doc_info(doc_id: str) -> dict:
    """EP document id -> {id, title, url, kind, amendments} with a public link."""
    d = doc_id
    m = re.fullmatch(r"A-(\d+)-(\d{4})-(\d{4})-AM-(\d{3})-(\d{3})", d)
    if m:
        n = int(m.group(5)) - int(m.group(4)) + 1
        return {"id": d, "title": f"Enmiendas al Pleno {int(m.group(4))}-{int(m.group(5))} (informe A{m.group(1)}-"
                f"{int(m.group(3)):04d}/{m.group(2)})", "url": f"{DOCEO}/{d}_EN.pdf", "kind": "plenary_amendments",
                "amendments": n}
    m = re.fullmatch(r"A-(\d+)-(\d{4})-(\d{4})", d)
    if m:
        return {"id": d, "title": f"Informe A{m.group(1)}-{int(m.group(3)):04d}/{m.group(2)}",
                "url": f"{DOCEO}/{d}_EN.html", "kind": "report"}
    m = re.fullmatch(r"TA-(\d+)-(\d{4})-(\d{4})", d)
    if m:
        return {"id": d, "title": f"Texto aprobado P{m.group(1)}_TA({m.group(2)}){m.group(3)}",
                "url": f"{DOCEO}/{d}_EN.html", "kind": "adopted_text"}
    m = re.fullmatch(r"([A-Z]{4})-(PR|PA|AD|AM|RR|DT|CM)-(\d+)", d)
    if m:
        kinds = {"PR": ("Proyecto de informe", "draft_report"), "PA": ("Proyecto de opinión", "draft_opinion"),
                 "AD": ("Opinión", "opinion"), "AM": ("Enmiendas en comisión", "committee_amendments"),
                 "RR": ("Informe", "report"), "DT": ("Documento de trabajo", "working_document"),
                 "CM": ("Comunicación a los miembros", "communication")}
        label, kind = kinds[m.group(2)]
        return {"id": d, "title": f"{label} {m.group(1)} ({m.group(3)})", "url": f"{DOCEO}/{d}_EN.pdf",
                "kind": kind}
    if d.startswith(("PV-", "CRE-")):
        label = "Acta del Pleno" if d.startswith("PV-") else "Debate en el Pleno (acta literal)"
        return {"id": d, "title": label, "url": f"{DOCEO}/{d}_EN.html", "kind": "minutes"}
    if d.startswith("OJQ-"):
        return {"id": d, "title": "Orden del día del Pleno", "url": f"{DOCEO}/{d}_EN.html", "kind": "agenda"}
    m = re.fullmatch(r"C-(\d+)-(\d{4})-(\d{4})", d)
    if m:
        return {"id": d, "title": f"Propuesta o documento recibido C{m.group(1)}-{int(m.group(3)):04d}/{m.group(2)}",
                "url": "", "kind": "proposal"}
    return {"id": d, "title": d, "url": "", "kind": "other"}


# --------------------------------------------------------------------------- reference data

def load_meps(http: Http, cache: dict) -> None:
    """Current MEPs' names into cache {person id: {name}}."""
    offset = 0
    while True:
        items = items_of(http.json(f"{API}/meps/show-current", {**LD, "limit": 500, "offset": offset}))
        for m in items:
            pid = tail(m.get("id")) or str(m.get("identifier", ""))
            name = m.get("label") or f"{m.get('givenName', '')} {m.get('familyName', '')}".strip()
            if pid and name:
                cache.setdefault(pid, {})["name"] = name
                country = m.get("api:country-of-representation") or ""
                if country:
                    cache[pid]["country"] = country
        if len(items) < 500:
            break
        offset += 500


def person_name(http: Http, pid: str, cache: dict) -> dict:
    if pid in cache and cache[pid].get("name"):
        return cache[pid]
    items = items_of(http.json(f"{API}/meps/{pid}", LD))
    info = {"name": pid}
    if items:
        m = items[0]
        info["name"] = m.get("label") or f"{m.get('givenName', '')} {m.get('familyName', '')}".strip() or pid
        cit = tail(m.get("citizenship"))
        if cit:
            info["country"] = cit[:2] if len(cit) == 2 else cit
    cache[pid] = info
    return info


# --------------------------------------------------------------------------- one file

def normalize(raw: Any, ctx: dict) -> dict | None:
    items = items_of(raw)
    if not items:
        return None
    d = items[0]
    lang = ctx["lang"]
    ref = normalize_ref(d.get("label")) or normalize_ref(json.dumps(d)[:3000])
    if not ref:
        return None
    ptype = tail(d.get("process_type")) or ref[-4:-1]
    title = text(d.get("process_title"), lang)
    title_en = text(d.get("process_title"), "en")

    # Committees
    committees: dict[str, str] = {}
    lead_count = 0
    for p in listify(d.get("had_participation")):
        role = tail(p.get("participation_role")).upper()
        if not role.startswith("COMMITTEE_") or "DECLIN" in role or "REFUS" in role:
            continue
        for org in listify(p.get("had_participant_organization")):
            code = tail(org)
            if role == "COMMITTEE_LEAD":
                committees[code] = "lead"
                lead_count += 1
            elif "JOINT" in role:
                committees[code] = "joint"
            elif "ASSOCIATED" in role:
                committees.setdefault(code, "associated")
            elif "OPINION" in role:
                committees.setdefault(code, "opinion")
            elif "BUDGETARY" in role:
                committees.setdefault(code, "budget")
            else:
                committees.setdefault(code, "other")
    if lead_count > 1:
        committees = {c: ("joint" if r == "lead" else r) for c, r in committees.items()}

    # People. Shadows have no committee: it is found through the rapporteur sharing the
    # same precedenceOrder block (e.g. 1701 rapporteur AGRI, 1702-1708 shadows of AGRI).
    raw_people = []
    block_committee: dict[int, str] = {}
    for p in listify(d.get("had_participation")):
        role = tail(p.get("participation_role")).upper()
        persons = [tail(x) for x in listify(p.get("had_participant_person"))]
        if not persons or "RAPPORTEUR" not in role:
            continue
        kind = ("opinion_shadow" if "SHADOW" in role and "OPINION" in role else
                "shadow" if "SHADOW" in role else
                "opinion_rapporteur" if "OPINION" in role else "rapporteur")
        committee = tail(listify(p.get("participation_in_name_of"))[0] if p.get("participation_in_name_of") else "")
        order = to_int(p.get("precedenceOrder"))
        block = order // 100 if order is not None else None
        if committee and block is not None and kind in ("rapporteur", "opinion_rapporteur"):
            block_committee[block] = committee
        raw_people.append({"pid": persons[0], "kind": kind, "committee": committee, "block": block,
                           "group": GROUPS.get(tail(p.get("politicalGroup")), tail(p.get("politicalGroup"))),
                           "since": str(p.get("activity_date") or "")[:10], "order": order or 0})
    leads = [c for c, r in committees.items() if r in ("lead", "joint")]
    people = []
    for rp in sorted(raw_people, key=lambda x: x["order"]):
        com = rp["committee"] or block_committee.get(rp["block"]) or \
            (leads[0] if rp["kind"] in ("rapporteur", "shadow") and leads else "")
        info = person_name(ctx["http"], rp["pid"], ctx["people"])
        people.append({"id": rp["pid"], "name": info.get("name", rp["pid"]), "group": rp["group"],
                       "country": info.get("country", ""), "role": rp["kind"], "committee": com,
                       "since": rp["since"]})

    # Activities -> timeline (grouped by date and type) and documents
    groups: dict[tuple[str, str], dict] = {}
    docs: dict[str, dict] = {}
    amend_plenary = 0
    for a in listify(d.get("consists_of")):
        date = str(a.get("activity_date") or "")[:10]
        atype = tail(a.get("had_activity_type")).upper()
        if not date:
            continue
        ids = []
        for key in ("based_on_a_realization_of", "decided_on_a_realization_of", "recorded_in_a_realization_of",
                    "documented_by_a_realization_of"):
            ids += [tail(x) for x in listify(a.get(key))]
        g = groups.setdefault((date, atype), {"date": date, "type": atype,
                                              "title": ACTIVITY.get(atype, (humanize(atype), False))[0],
                                              "docs": [], "count": 0})
        g["count"] += 1
        for did in ids:
            info = doc_info(did)
            if info["kind"] == "minutes" and atype != "PLENARY_VOTE":
                continue
            if all(x["id"] != did for x in g["docs"]):
                g["docs"].append(info)
            if did not in docs and info["kind"] not in ("minutes", "agenda"):
                docs[did] = {**info, "date": date}
                amend_plenary += info.get("amendments", 0)
    events = sorted(groups.values(), key=lambda e: (e["date"], e["type"]))
    for e in events:
        if e["count"] > 1 and e["type"] in ("TABLING_PLENARY", "COMMITTEE_TABLING_AMENDMENT"):
            e["title"] += f" ({e['count']} documentos)"
        del e["count"]

    milestones = [e for e in events if ACTIVITY.get(e["type"], ("", True))[1]]
    stage = milestones[-1]["title"] if milestones else (events[-1]["title"] if events else "")
    types = " ".join(e["type"] for e in events)
    finished = bool(FINISHED.search(types)) or (ptype != "COD" and bool(PLENARY_END.search(types)))
    if ptype == "COD" and "SIGNATURE" in types and "PUBLICATION_OFFICIAL_JOURNAL" not in types:
        finished = False

    # Plenary items announced for this file (past and future)
    scheduled = []
    for s in listify(d.get("was_scheduled_in")):
        date = str(s.get("activity_date") or "")[:10]
        stype = tail(s.get("had_activity_type")).upper()
        if not date:
            continue
        what = {"PLENARY_VOTE": "Votación", "PLENARY_DEBATE": "Debate"}.get(stype, humanize(stype))
        point = s.get("notation_agendaPoint") or ""
        scheduled.append({"id": f"ep-pl-{tail(s.get('id'))}", "date": date, "source": "ep",
                          "body": "Pleno del PE", "title": f"{what}" + (f" (punto {point})" if point else ""),
                          "url": f"{DOCEO}/OJQ-10-{date}_EN.html", "refs": [ref]})

    committee_list = [{"code": c, "role": r} for c, r in committees.items()]
    order = {"lead": 0, "joint": 1, "associated": 2, "opinion": 3, "budget": 4, "other": 5}
    committee_list.sort(key=lambda c: (order.get(c["role"], 9), c["code"]))
    committee_amend_docs = sum(1 for x in docs.values() if x["kind"] == "committee_amendments")
    return {
        "ref": ref, "id": ep_id(ref), "type": ptype, "title": title, "title_en": title_en,
        "stage": stage, "ongoing": not finished, "committees": committee_list, "people": people,
        "started": events[0]["date"] if events else None, "last_activity": events[-1]["date"] if events else None,
        "events": events, "docs": sorted(docs.values(), key=lambda x: x["date"]),
        "amendments": {"plenary": amend_plenary, "committee_documents": committee_amend_docs},
        "ep_scheduled": scheduled, "links": links_for(ref),
    }


def in_scope(rec: dict, cfg: dict, forced: set[str]) -> list[str]:
    names = {"lead": "responsable", "joint": "conjunta", "associated": "asociada", "opinion": "opinión"}
    reasons = [f"{c['code']} ({names.get(c['role'], c['role'])})" for c in rec.get("committees", [])
               if c["code"] in cfg["committees"] and c["role"] in cfg["roles"]]
    if rec["ref"] in forced:
        reasons.append("seguimiento manual")
    return reasons


# --------------------------------------------------------------------------- lists and feeds

def list_ids(http: Http, ptype: str, since_year: int) -> list[str]:
    ids: list[str] = []
    offset, page = 0, 1000
    while True:
        items = items_of(http.json(f"{API}/procedures", {**LD, "process-type": ptype, "limit": page,
                                                         "offset": offset}))
        for it in items:
            pid = str(it.get("process_id") or tail(it.get("id")))
            if re.fullmatch(r"\d{4}-\d{4}[A-Z]?", pid) and int(pid[:4]) >= since_year:
                ids.append(pid)
        if len(items) < page:
            break
        offset += page
    return ids


def changed_ids(http: Http, since: str | None) -> list[str]:
    params = {"timeframe": "custom", "start-date": since} if since else {"timeframe": "one-day"}
    r = http.get(f"{API}/procedures/feed", params=params)
    if r is None or r.status_code != 200:
        return []
    out: list[str] = []
    for m in PROC_ID.finditer(r.text):
        if m.group(1) not in out:
            out.append(m.group(1))
    return out


def plenary_sittings(http: Http) -> list[dict]:
    year = int(today()[:4])
    out = []
    for y in (year, year + 1):
        for m in items_of(http.json(f"{API}/meetings", {**LD, "year": y, "limit": 200})):
            date = str(m.get("activity_date") or "")[:10]
            if not date:
                continue
            place = tail(m.get("hasLocality"))
            city = {"FRA_SXB": "Estrasburgo", "BEL_BRU": "Bruselas"}.get(place, "")
            out.append({"id": f"ep-plenary-{date}", "date": date, "source": "ep",
                        "body": "Pleno del PE" + (f" ({city})" if city else ""), "title": "Sesión plenaria",
                        "url": f"{DOCEO}/OJQ-10-{date}_EN.html", "refs": []})
    return out


def committee_agendas(http: Http, cfg: dict, state: dict) -> list[dict]:
    """Agendas of AGRI/PECH/REGI meetings, from their PDFs (published about a week ahead)."""
    import datetime as dt
    known: dict = state.setdefault("committee_agendas", {})
    missing: dict = state.setdefault("committee_agendas_missing", {})
    start = dt.date.fromisoformat(plus_days(today(), -3))
    for c in cfg["committees"]:
        for i in range(0, 25):
            day = start + dt.timedelta(days=i)
            if day.weekday() >= 5:
                continue
            date = day.isoformat()
            key = f"{c}-{date}"
            if key in known or (missing.get(key) and date < today()):
                continue
            url = AGENDA_PDF.format(c=c, d=date)
            r = http.get(url, tries=1, timeout=40)
            if r is None or r.status_code != 200 or r.content[:5] != b"%PDF-":
                missing[key] = today()
                continue
            refs = refs_in(pdf_text(r.content, 12))
            known[key] = {"id": f"ep-com-{key}", "date": date, "source": "ep", "body": f"Comisión {c} del PE",
                          "title": "Orden del día de la reunión", "url": url, "refs": refs}
            log(f"  {c} agenda {date}: {len(refs)} file numbers")
    cutoff = plus_days(today(), -400)
    state["committee_agendas"] = {k: v for k, v in known.items() if v["date"] >= cutoff}
    state["committee_agendas_missing"] = {k: v for k, v in missing.items() if k[-10:] >= plus_days(today(), -10)}
    return list(state["committee_agendas"].values())


# --------------------------------------------------------------------------- collector

def collect(cfg: dict, http: Http, old: dict, state: dict) -> dict:
    state = dict(state)
    people_cache: dict = state.setdefault("people", {})
    ctx = {"http": http, "lang": cfg.get("language", "es"), "people": people_cache}
    forced = set(cfg["also_track"]) | set(cfg["follow"])
    records = {k: dict(v) for k, v in old.items()}
    status: dict = {}

    try:
        load_meps(http, people_cache)
    except BudgetExceeded:
        pass

    queue: list[str] = list(state.get("queue", []))
    if not state.get("bootstrap_listed"):
        log("  first load: listing procedures…")
        for ptype in cfg["procedure_types"]:
            ids = list_ids(http, ptype, int(cfg["since_year"]))
            log(f"    {ptype}: {len(ids)} since {cfg['since_year']}")
            queue.extend(i for i in ids if i not in queue)
        state["bootstrap_listed"] = True
        state["bootstrap_total"] = len(queue)
        state["first_feed_date"] = today()

    since = state.get("last_feed_date") or state.get("first_feed_date")
    daily = changed_ids(http, since) + [ep_id(r) for r in forced if ep_id(r) not in queue]
    for pid in daily:
        if pid not in queue:
            queue.append(pid)
    status["changed_in_feed"] = len(daily)

    done = 0
    forced_ids = {ep_id(r) for r in forced}
    attempts: dict = state.setdefault("attempts", {})
    failed: dict = state.setdefault("failed", {})
    import os
    import time as _time
    deadline = _time.monotonic() + 60 * float(os.getenv("TRACKER_EP_MINUTES", "150"))
    try:
        while queue:
            if _time.monotonic() > deadline:
                log(f"  time limit for this run reached: {len(queue)} files left for the next run")
                break
            pid = queue.pop(0)
            if int(pid[:4]) < int(cfg["since_year"]) and pid not in forced_ids:
                continue
            r = http.get(f"{API}/procedures/{pid}", params=LD, accept="application/ld+json")
            if r is None or (r.status_code != 200 and r.status_code != 404):
                attempts[pid] = attempts.get(pid, 0) + 1
                if attempts[pid] < 4:
                    queue.append(pid)          # try again later in this run or the next one
                else:
                    failed[pid] = f"HTTP {r.status_code if r is not None else 'no answer'}"
                continue
            attempts.pop(pid, None)
            done += 1
            if done % 200 == 0:
                log(f"  {done} files read, {len(queue)} left")
            if r.status_code == 404 or not r.content:
                continue
            try:
                rec = normalize(r.json(), ctx)
            except Exception as exc:  # noqa: BLE001 - one odd file must not stop the run
                failed[pid] = f"{type(exc).__name__}: {str(exc)[:120]}"
                log(f"  could not read {pid}: {failed[pid]}")
                continue
            failed.pop(pid, None)
            if rec is None:
                continue
            if rec["type"] not in cfg["procedure_types"] and rec["ref"] not in forced:
                records.pop(rec["ref"], None)
                continue
            reasons = in_scope(rec, cfg, forced)
            if reasons:
                rec["scope"] = reasons
                records[rec["ref"]] = rec
            else:
                records.pop(rec["ref"], None)
    except BudgetExceeded:
        log(f"  request budget reached: {len(queue)} files left for the next run")

    state["queue"] = queue
    if not queue:
        state["last_feed_date"] = today()
        if not state.get("bootstrap_done"):
            state["bootstrap_done"] = True
            log("  first load complete")
    total = state.get("bootstrap_total", 0)
    state["bootstrap_note"] = "" if state.get("bootstrap_done") else f"{total - len(queue)} of {total} files read"

    calendar: list[dict] = []
    try:
        refs_by_date: dict[str, set] = {}
        for rec in records.values():
            for item in rec.get("ep_scheduled", []):
                refs_by_date.setdefault(item["date"], set()).add(rec["ref"])
        for s in plenary_sittings(http):
            s["refs"] = sorted(refs_by_date.get(s["date"], set()))
            calendar.append(s)
        calendar.extend(committee_agendas(http, cfg, state))
    except BudgetExceeded:
        pass
    # People cache: keep it bounded
    if len(people_cache) > 6000:
        state["people"] = dict(list(people_cache.items())[-6000:])
    status.update({"files_read": done, "queue_left": len(queue), "tracked": len(records),
                   "failed": len(failed), "throttled": http.throttled})
    return {"records": records, "calendar": calendar, "state": state, "status": status}
