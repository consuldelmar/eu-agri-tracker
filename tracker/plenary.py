"""European Parliament plenary sessions: agenda (debates and votes), times and results.

Source: the EP Open Data Portal, per sitting day:
- /meetings/{day}/foreseen-activities: the agenda (time slots, debates, votes, with the file,
  report, committees and rapporteurs of each item). Published about a week before the session.
- /meetings/{day}/vote-results and /decisions: what was voted and the result (after the vote).
- /meetings/{day}/activities: the minutes' items, used when an old day has no agenda data.

Days are kept in data/plenary_days.json. Past days are read once and then left alone; days
around today are read again on every run, since agendas change until the last minute.
"""

from __future__ import annotations

import datetime as dt
import html
import re

from .common import BudgetExceeded, Http, log, today

API = "https://data.europarl.europa.eu/api/v2"
LD = {"format": "application/ld+json"}
START = "2024-07-16"                     # first sitting of the 10th term
DOCEO = "https://www.europarl.europa.eu/doceo/document"
BACKFILL_PER_RUN = 30
FINAL_AFTER = 21                         # days after which a past sitting no longer changes
PROCEDURAL = re.compile(
    r"(?i)^(opening|resumption|closure|approval of the minutes|composition|corrigend|documents received|"
    r"voting time|order of business|apertura|reanudaci|cierre|aprobaci[oó]n del acta|composici|"
    r"correcci[oó]n de errores|presentaci[oó]n de documentos|turno de votaciones|orden de los trabajos|"
    r"(?:announcement|comunicaci[oó]n|declaraci[oó]n) (?:by|de) (?:the )?(?:president|la presidencia)|"
    r"calendar of part-sessions|calendario|transfers of appropriations|transferencias de cr[eé]ditos|"
    r"verification of credentials|verificaci[oó]n de credenciales|signature of acts|firma de actos|"
    r"explanations of vote|explicaciones de voto|corrections to votes|correcciones e intenciones de voto|"
    r"agenda of the next sitting|orden del d[ií]a de la pr[oó]xima sesi[oó]n|dates of forthcoming|"
    r"calendario de los pr[oó]ximos)")


def _lang(value, lang: str = "es") -> str:
    if isinstance(value, dict):
        return value.get(lang) or value.get("en") or value.get("mul") or next(iter(value.values()), "")
    if isinstance(value, list):
        return _lang(value[0], lang) if value else ""
    return value or ""


def _tail(v) -> str:
    v = v[0] if isinstance(v, list) and v else v
    v = v.get("id") if isinstance(v, dict) else v
    return str(v or "").rstrip("/").split("/")[-1]


def _ids(v) -> list[str]:
    out = []
    for x in v if isinstance(v, list) else ([v] if v else []):
        out.append(str(x.get("id") if isinstance(x, dict) else x))
    return out


def _hhmm(v) -> str:
    v = v[0] if isinstance(v, list) and v else v
    m = re.search(r"T(\d\d:\d\d)", str(v or ""))
    return m.group(1) if m else ""


def _items(payload) -> list[dict]:
    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        return [x for x in payload["data"] if isinstance(x, dict)]
    return []


def _doc_code(doc_id: str) -> str:
    """'A-10-2026-0304' -> 'A10-0304/2026'."""
    m = re.match(r"([A-Z]+)-(\d+)-(\d{4})-(\d{4})$", doc_id)
    return f"{m.group(1)}{m.group(2)}-{m.group(4)}/{m.group(3)}" if m else doc_id


def _structured(xml: str) -> dict:
    """The agenda item's details: <item><title/><type/><process><label typeof=proc/>...</item>."""
    out: dict = {}
    if not xml:
        return out
    t = re.search(r"<title>(.*?)</title>", xml, re.S)
    if t:
        out["long"] = html.unescape(re.sub(r"<[^>]+>", "", t.group(1))).strip()
    ty = re.search(r"<type>(.*?)</type>", xml, re.S)
    if ty:
        out["type"] = html.unescape(re.sub(r"<[^>]+>", "", ty.group(1))).strip().rstrip(":")
    for typeof, res, text in re.findall(r'<label typeof="([^"]+)"(?: resource="([^"]*)")?\s*/?>([^<]*)', xml):
        text = html.unescape(text).strip()
        if typeof == "symbol":
            out["symbol"] = text
        elif typeof == "proc" and text:
            out.setdefault("refs", []).append(text)
        elif typeof.endswith("/REPORT") or typeof.endswith("MOTION_FOR_RESOLUTION") or "RC-B" in text:
            if text:
                out.setdefault("docs", []).append(text)
        elif typeof == "org" and res:
            out.setdefault("orgs", []).append(res.split("/")[-1])
        elif typeof == "person" and text:
            out.setdefault("people", []).append(text)
        elif typeof == "external" and text.startswith("COM"):
            out["com"] = text
    return out


# --------------------------------------------------------------------------- one sitting day

def read_day(http: Http, date: str, lang: str) -> dict:
    """Everything known about one sitting day. Raises BudgetExceeded like any request."""
    mid = f"MTG-PL-{date}"
    day = {"date": date, "items": [], "source": "", "fetched": today()}
    agenda = _items(http.json(f"{API}/meetings/{mid}/foreseen-activities", {**LD, "limit": 1000}))
    items: dict[str, dict] = {}
    if agenda:
        day["source"] = "agenda"
        slots = [x for x in agenda if str(x.get("had_activity_type", "")).endswith("MEETING_PART")]
        slot_of: dict[str, dict] = {}
        for s in slots:
            for cid in _ids(s.get("consists_of")):
                slot_of[cid.split("/")[-1]] = s
        for x in agenda:
            kind = str(x.get("had_activity_type", ""))
            if not (kind.endswith("PLENARY_DEBATE") or kind.endswith("PLENARY_VOTE")):
                continue
            aid = x["activity_id"]
            point = x.get("notation_agendaPoint") or aid.split("-ITM-")[-1]
            sc = _structured(_lang(x.get("structuredContent"), lang))
            s = slot_of.get(aid, {})
            it = {"id": point, "kind": "vote" if kind.endswith("VOTE") else "debate",
                  "title": _lang(x.get("activity_label"), lang).strip() or sc.get("long", ""),
                  "order": float(x.get("activity_order") or 0),
                  "time": _hhmm(s.get("activity_start_date")), "end": _hhmm(s.get("activity_end_date")),
                  "slot": _lang(s.get("agendaLabel"), lang).strip(), **sc}
            if not it.get("docs"):
                it["docs"] = [_doc_code(_tail(d)) for d in _ids(x.get("based_on_a_realization_of"))
                              if re.search(r"/[AB]-\d", d)]
            items[point] = it
    if date < today():
        votes = _items(http.json(f"{API}/meetings/{mid}/vote-results", {**LD, "limit": 500}))
        results = [v for v in votes if str(v.get("had_activity_type", "")).endswith("PLENARY_VOTE_RESULTS")]
        decisions = {}
        if results:
            decisions = {d.get("id"): d for d in
                         _items(http.json(f"{API}/meetings/{mid}/decisions", {**LD, "limit": 3000}))}
        for v in results:
            res = _result([decisions[i] for i in _ids(v.get("consists_of")) if i in decisions])
            points = [e.split("-ITM-")[-1] for e in _ids(v.get("executed"))]
            target = next((items[p] for p in points if p in items), None)
            if target is None:
                label = re.sub(r"<[^>]+>", " ", _lang(v.get("structuredLabel"), lang))
                proc = [_tail(p) for p in _ids(v.get("inverse_consists_of")) if "/proc/" in p]
                target = {"id": points[0] if points else "R-" + str(v.get("notation_dlvId") or len(items)),
                          "kind": "vote", "title": _lang(v.get("activity_label"), lang).strip(),
                          "order": float(v.get("activity_order") or 500 + len(items)), "time": "", "end": "",
                          "slot": "", "docs": [_doc_code(_tail(d)) for d in _ids(v.get("based_on_a_realization_of"))],
                          "procs": proc, "detail": re.sub(r"\s+", " ", label).strip()}
                items[target["id"]] = target
            if res:
                target["result"] = res
        if not agenda:
            minutes = _items(http.json(f"{API}/meetings/{mid}/activities", {**LD, "limit": 1000}))
            for x in minutes:
                raw = x.get("activity_label")
                if isinstance(raw, dict) and not (raw.get(lang) or raw.get("en")):
                    continue                     # only in another language: a procedural line
                label = re.sub(r"^\d+(\.\d+)*\.\s*", "", _lang(raw, lang)).strip()
                label_en = re.sub(r"^\d+(\.\d+)*\.\s*", "", _lang(x.get("activity_label"), "en")).strip()
                if not label or PROCEDURAL.search(label) or PROCEDURAL.search(label_en):
                    continue
                is_debate = bool(re.search(r"\((?:debate|debates)\)\s*$", label))
                key = "M-" + x["activity_id"].split("-ITM-")[-1]
                items[key] = {"id": key, "kind": "debate" if is_debate else "other",
                              "title": re.sub(r"\s*\((?:debate|debates)\)\s*$", "", label),
                              "order": float(x["activity_id"].split("-ITM-")[-1].split("-")[0] or 0)
                              if x["activity_id"].split("-ITM-")[-1].split("-")[0].isdigit() else 0,
                              "time": _hhmm(x.get("activity_start_date")), "end": _hhmm(x.get("activity_end_date")),
                              "slot": ""}
            if minutes:
                day["source"] = "minutes"
    day["items"] = sorted(items.values(), key=lambda i: (i.get("time") or "99", i.get("order") or 0))
    return day


def _result(decs: list[dict]) -> dict | None:
    """The final vote of an item: the last decision that was not 'lapsed'."""
    decs = [d for d in decs if d.get("decision_outcome")]
    if not decs:
        return None
    decs.sort(key=lambda d: float(d.get("activity_order") or 0))
    final = next((d for d in reversed(decs) if not str(d["decision_outcome"]).endswith("LAPSED")), decs[-1])
    out = {"outcome": _tail(final["decision_outcome"])}
    for k, short in (("number_of_votes_favor", "for"), ("number_of_votes_against", "against"),
                     ("number_of_votes_abstention", "abst")):
        if final.get(k) is not None:
            out[short] = final[k]
    return out


# --------------------------------------------------------------------------- collector

def sitting_days(http: Http) -> list[tuple[str, str]]:
    """[(date, place)] of every sitting day from the start of the term to the end of next year."""
    out = []
    for y in range(int(START[:4]), int(today()[:4]) + 2):
        for m in _items(http.json(f"{API}/meetings", {**LD, "year": y, "limit": 500})):
            d = str(m.get("activity_date") or "")[:10]
            if d >= START:
                place = _tail(m.get("hasLocality"))
                out.append((d, {"FRA_SXB": "Estrasburgo", "BEL_BRU": "Bruselas"}.get(place, "")))
    return sorted(set(out))


def collect(cfg: dict, http: Http, state: dict) -> dict:
    """Updates state['days'] (date -> day). Returns a status dict."""
    lang = cfg.get("language", "es")
    days: dict = state.setdefault("days", {})
    status = {"read": 0, "left": 0}
    try:
        listed = sitting_days(http)
    except BudgetExceeded:
        return {"error": "request budget"}
    if listed:
        state["sittings"] = listed
    t = today()
    near = lambda d: abs((dt.date.fromisoformat(d) - dt.date.fromisoformat(t)).days)  # noqa: E731
    todo, backfill = [], []
    for date, place in state.get("sittings", []):
        cur = days.get(date)
        if cur and cur.get("final"):
            continue
        if cur is None:
            (todo if near(date) <= 30 or date > t else backfill).append(date)
        elif date >= t and near(date) > 21:
            if cur.get("fetched") != t:
                todo.append(date)                 # far future: once a day is plenty
        elif near(date) <= 4 or cur.get("fetched") != t:
            todo.append(date)                     # this week: every run; recent: once a day
    backfill.sort(reverse=True)                  # newest first
    for date in todo + backfill[:BACKFILL_PER_RUN]:
        try:
            day = read_day(http, date, lang)
        except BudgetExceeded:
            break
        except Exception as exc:  # noqa: BLE001 - one odd day must not stop the run
            log(f"  plenary {date}: {type(exc).__name__}: {exc}")
            continue
        place = dict(state.get("sittings", [])).get(date, "")
        day["place"] = place
        old = days.get(date)
        if not day["items"] and old and old.get("items"):
            day = {**old, "fetched": t}          # keep what we had if the server returned nothing
        day["final"] = date < (dt.date.fromisoformat(t) - dt.timedelta(days=FINAL_AFTER)).isoformat()
        days[date] = day
        status["read"] += 1
    status["left"] = sum(1 for d, _ in state.get("sittings", []) if d not in days)
    status["days"] = len(days)
    log(f"  plenary: {status}")
    return status


# --------------------------------------------------------------------------- website

AGRI_WORDS = re.compile(
    r"(?i)agricult|agrari|agr[ií]col|\bfarm(?:s|er|ers|ing)?\b|ganader|livestock|\bfish|\bpesca|pesquer|"
    r"acuicult|aquacult|\brural|\bPAC\b|\bCAP\b|cohesi[oó]n|cohesion|desarrollo regional|pol[ií]tica regional|"
    r"regional (?:development|policy)|\bfood\b|aliment(?:o|os|aria|ario|arias|arios|aci[oó]n)\b|forest|bosque|"
    r"bienestar animal|animal welfare|sanidad (?:animal|vegetal)|\bplant(?:a|as|s)?\b|vegetal|fitosanit|"
    r"phytosanit|veterinar|pesticid|plaguicid|semilla|\bseeds?\b|\bvinos?\b|\bwine|az[uú]car|\bsugar|"
    r"\bMFF\b|\bMFP\b|marco financiero plurianual|multiannual financial framework|Mercosur|"
    r"indicaciones geogr[aá]ficas|geographical indications")


def sessions(days: dict, records: dict, cfg: dict) -> tuple[list[dict], dict[str, dict]]:
    """Groups sitting days into sessions and marks the items that concern AGRI, PECH or REGI."""
    committees = set(cfg.get("committees", []))
    by_ref = {r["ref"]: r for r in records.values()}
    by_proc = {r["ref"].split("(")[0].replace("/", "-"): r for r in records.values()}
    by_doc: dict[str, dict] = {}
    for r in records.values():
        for d in r.get("docs", []):
            code = re.search(r"\b([AB]\d+-\d{4}/\d{4})\b", d.get("title", "") + " " + d.get("code", ""))
            if code:
                by_doc[code.group(1)] = r
    dates = sorted(days)
    groups: list[list[str]] = []
    for d in dates:
        if groups and (dt.date.fromisoformat(d) - dt.date.fromisoformat(groups[-1][-1])).days <= 3:
            groups[-1].append(d)
        else:
            groups.append([d])
    index, full = [], {}
    for g in groups:
        sid = g[0]
        out_days, n_items, n_ours, n_votes = [], 0, 0, 0
        for d in g:
            items = []
            for it in days[d].get("items", []):
                it = dict(it)
                rec = None
                for ref in it.get("refs", []):
                    rec = rec or by_ref.get(ref)
                for p in it.get("procs", []):
                    rec = rec or by_proc.get(p)
                for doc in it.get("docs", []):
                    rec = rec or by_doc.get(doc)
                why = sorted(set(it.get("orgs", [])) & committees)
                if rec:
                    it["file"] = {"id": rec["id"], "ref": rec["ref"]}
                    it.setdefault("refs", [rec["ref"]])
                    why = why or sorted({c["code"] for c in rec.get("committees", []) if c["code"] in committees})
                    if not why:
                        why = ["seguido"]
                elif not why and AGRI_WORDS.search(f"{it.get('title', '')} {it.get('long', '')}"):
                    text = f"{it.get('title', '')} {it.get('long', '')}".lower()
                    why = [t for t, words in (cfg.get("tag_words") or {}).items() if any(w in text for w in words)]
                    why = why or ["tema"]
                    it["topic"] = True
                if why:
                    it["ours"] = why
                    n_ours += 1
                n_items += 1
                n_votes += it.get("kind") == "vote"
                items.append(it)
            out_days.append({"date": d, "source": days[d].get("source", ""), "items": items,
                             "agenda": f"{DOCEO}/OJQ-10-{d}_ES.html", "minutes": f"{DOCEO}/PV-10-{d}_ES.html"})
        place = next((days[d].get("place") for d in g if days[d].get("place")), "")
        index.append({"id": sid, "start": g[0], "end": g[-1], "place": place, "days": len(g),
                      "items": n_items, "ours": n_ours, "votes": n_votes})
        full[sid] = {"id": sid, "start": g[0], "end": g[-1], "place": place, "days": out_days}
    return index, full
