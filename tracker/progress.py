"""Where a file stands in its procedure: stages done, current and pending.

Built from the Parliament's activities and the Council steps detected in documents.
Each stage: {key, label, state, date, detail}
  state: done | implied (a later stage happened, so this one must have) | partial |
         current | pending
The Council part depends on detecting the right documents, so a stage can stay
pending although it happened; "implied" covers most of those cases.
"""

from __future__ import annotations

from .common import today

COUNCIL_MANDATE = {"Mandato de negociación con el PE", "Orientación general"}
COUNCIL_PARTIAL = {"Orientación general o mandato parcial"}
COUNCIL_AGREEMENT = {"Texto final de compromiso (acuerdo con el PE)", "Acuerdo provisional con el PE"}
COUNCIL_ADOPTION = {"Adopción por el Consejo"}

EP_MANDATE = {
    "COMMITTEE_DECISION_INTERINSTITUTIONAL_NEGOTIATIONS": "mandato de la comisión para negociar",
    "PLENARY_ENDORSE_COMMITTEE_INTERINSTITUTIONAL_NEGOTIATIONS": "mandato confirmado por el Pleno",
    "PLENARY_AMEND_PROPOSAL": "enmiendas del Pleno; vuelve a comisión para negociar",
    "PLENARY_REFER_COMMITTEE_INTERINSTITUTIONAL_NEGOTIATIONS": "devuelto a comisión para negociar",
}
PLENARY_FINAL = ("PLENARY_ADOPT", "PLENARY_APPROVE", "PLENARY_DECISION", "PLENARY_REJECT", "PLENARY_VOTE_RESULTS",
                 "PLENARY_OUTCOME", "PLENARY_CONSENT")

NEXT_HINT = {
    "ep_committee": "La comisión del PE prepara y vota su informe",
    "ep_mandate": "El PE fija su posición o el mandato para negociar",
    "council_mandate": "El Consejo fija su orientación general o mandato",
    "trilogues": "Negociaciones a tres bandas (trílogos)",
    "agreement": "Acuerdo provisional entre PE y Consejo",
    "ep_approval": "Votación final en el Pleno del PE",
    "council_adoption": "Adopción formal por el Consejo",
    "published": "Publicación en el Diario Oficial",
    "ep_plenary": "Votación en el Pleno del PE",
    "scrutiny": "El PE puede objetar durante el plazo de control",
}


def _first(events: list[dict], *types: str) -> dict | None:
    for e in events:
        if any(e["type"].startswith(t) for t in types):
            return e
    return None


def _council(docs: list[dict], steps: set[str]) -> dict | None:
    hits = sorted((d for d in docs if d.get("step") in steps and d.get("kind") != "agenda"),
                  key=lambda d: d.get("date") or "")
    return hits[0] if hits else None


def _stage(key: str, label: str) -> dict:
    return {"key": key, "label": label, "state": "pending", "date": None, "detail": ""}


def _finish(stages: list[dict], ongoing: bool, imply_from: int = 0) -> dict:
    """Fill implied stages, mark the current one, and return the summary."""
    last_done = max((i for i, s in enumerate(stages) if s["state"] in ("done",)), default=-1)
    for i in range(last_done):
        # The first two stages (start, committee) always precede anything else; in the ordinary
        # procedure, the Parliament's and the Council's positions run in parallel, so they are only
        # implied by later stages (trilogues onwards).
        if stages[i]["state"] == "pending" and (i < 2 or last_done >= imply_from):
            stages[i]["state"] = "implied"
    current = None
    if ongoing:
        for s in stages:
            if s["state"] in ("pending", "partial"):
                if s["state"] == "pending":
                    s["state"] = "current"
                current = s
                break
    nxt = None
    if current:
        idx = stages.index(current)
        nxt = next((s for s in stages[idx + 1:] if s["state"] == "pending"), None)
    done = sum(1 for s in stages if s["state"] in ("done", "implied"))
    return {
        "stages": stages, "done": done, "total": len(stages),
        "current": current["key"] if current else None,
        "current_label": current["label"] if current else (stages[-1]["label"] if not ongoing else ""),
        "hint": NEXT_HINT.get(current["key"], "") if current else "",
        "next_label": nxt["label"] if nxt else "",
    }


def _set(stage: dict, ev: dict | None, detail: str = "", state: str = "done") -> None:
    if ev:
        stage["state"] = state
        stage["date"] = ev.get("date")
        stage["detail"] = detail


def compute(rec: dict) -> dict:
    events = rec.get("events", [])
    docs = rec.get("council_docs", [])
    typ = rec.get("type")
    ongoing = rec.get("ongoing", True)
    types = " ".join(e["type"] for e in events)
    withdrawn = any(w in types for w in ("WITHDRAW", "LAPSE"))

    if typ == "COD":
        st = [_stage("proposal", "Propuesta"), _stage("ep_committee", "Comisión del PE"),
              _stage("ep_mandate", "Posición del PE"), _stage("council_mandate", "Posición del Consejo"),
              _stage("trilogues", "Trílogos"), _stage("agreement", "Acuerdo provisional"),
              _stage("ep_approval", "Aprobación del PE"), _stage("council_adoption", "Adopción del Consejo"),
              _stage("published", "Diario Oficial")]
        S = {s["key"]: s for s in st}
        _set(S["proposal"], events[0] if events else None, "remitido al Parlamento")
        _set(S["ep_committee"], _first(events, "COMMITTEE_ADOPTING_REPORT"), "informe aprobado")
        agreement_ev = _first(events, "COMMITTEE_APPROVE_PROVISIONAL_AGREEMENT")
        agreement_doc = _council(docs, COUNCIL_AGREEMENT)
        agreement_date = min(filter(None, [agreement_ev and agreement_ev["date"],
                                           agreement_doc and agreement_doc.get("date")]), default=None)
        # Parliament's position or negotiating mandate
        mandate = next((e for e in events if e["type"] in EP_MANDATE), None)
        first_reading = next((e for e in events if e["type"] == "PLENARY_ADOPT_POSITION"
                              and (not agreement_date or e["date"] < agreement_date)), None)
        cand = [x for x in (mandate, first_reading) if x]
        if cand:
            ev = min(cand, key=lambda e: e["date"])
            _set(S["ep_mandate"], ev, EP_MANDATE.get(ev["type"], "posición en primera lectura"))
        # Council's general approach or mandate (from Council documents)
        c_full = _council(docs, COUNCIL_MANDATE)
        c_part = _council(docs, COUNCIL_PARTIAL)
        if c_full:
            _set(S["council_mandate"], c_full, f"{c_full['step'].lower()} ({c_full.get('code')})")
        elif c_part:
            _set(S["council_mandate"], c_part, f"parcial ({c_part.get('code')})", state="partial")
        # Trilogues
        tri = [e for e in events if e["type"] == "INTERINSTITUTIONAL_NEGOTIATION"]
        if tri:
            S["trilogues"]["date"] = tri[0]["date"]
            S["trilogues"]["detail"] = f"{len(tri)} trílogo(s), último {tri[-1]['date']}"
            S["trilogues"]["state"] = "done" if agreement_date else "partial"
        # Provisional agreement
        if agreement_date:
            S["agreement"].update(state="done", date=agreement_date,
                                  detail="aprobado en comisión" if agreement_ev else
                                  f"texto final del Consejo ({agreement_doc.get('code')})")
        # Final approval by Parliament (after the agreement, or second reading)
        final = next((e for e in events if (e["type"] == "PLENARY_ADOPT_POSITION" and agreement_date
                                            and e["date"] >= agreement_date)
                      or e["type"] == "PLENARY_APPROVE_COUNCIL_POSITION"), None)
        _set(S["ep_approval"], final, "votado en el Pleno")
        _set(S["council_adoption"], _council(docs, COUNCIL_ADOPTION) or _first(events, "SIGNATURE"),
             "adoptado y firmado")
        _set(S["published"], _first(events, "PUBLICATION_OFFICIAL_JOURNAL"), "publicado")
        result = _finish(st, ongoing and not withdrawn, imply_from=4)
    elif typ in ("DEA", "RPS"):
        st = [_stage("transmitted", "Transmitido al PE"), _stage("scrutiny", "Plazo de control"),
              _stage("outcome", "Resultado")]
        S = {s["key"]: s for s in st}
        _set(S["transmitted"], events[0] if events else None)
        vote = _first(events, *PLENARY_FINAL)
        if vote:
            _set(S["scrutiny"], vote)
            _set(S["outcome"], vote, "votación en el Pleno")
        elif not ongoing:
            S["scrutiny"]["state"] = "done"
            S["outcome"].update(state="done", detail="sin objeción")
        result = _finish(st, ongoing)
    elif typ in ("CNS", "APP"):
        label = "Dictamen del PE" if typ == "CNS" else "Aprobación del PE"
        st = [_stage("proposal", "Propuesta"), _stage("ep_committee", "Comisión del PE"),
              _stage("ep_plenary", label), _stage("council_adoption", "Adopción del Consejo"),
              _stage("published", "Diario Oficial")]
        S = {s["key"]: s for s in st}
        _set(S["proposal"], events[0] if events else None)
        _set(S["ep_committee"], _first(events, "COMMITTEE_ADOPTING_REPORT"), "informe aprobado")
        _set(S["ep_plenary"], _first(events, *PLENARY_FINAL), "votado en el Pleno")
        _set(S["council_adoption"], _council(docs, COUNCIL_ADOPTION) or _first(events, "SIGNATURE"))
        _set(S["published"], _first(events, "PUBLICATION_OFFICIAL_JOURNAL"))
        result = _finish(st, ongoing and not withdrawn)
    else:   # NLE, INI, INL and others: committee, then plenary
        st = [_stage("start", "Inicio"), _stage("ep_committee", "Comisión del PE"),
              _stage("ep_plenary", "Pleno del PE")]
        S = {s["key"]: s for s in st}
        _set(S["start"], events[0] if events else None)
        _set(S["ep_committee"], _first(events, "COMMITTEE_ADOPTING_REPORT", "COMMITTEE_ADOPTING_OPINION"),
             "informe aprobado")
        _set(S["ep_plenary"], _first(events, *PLENARY_FINAL), "votado en el Pleno")
        result = _finish(st, ongoing and not withdrawn)

    if withdrawn:
        result["current_label"] = "Retirado o caducado"
    # Next announced date for this file, if any
    nxt = sorted((s for s in rec.get("scheduled", []) if (s.get("date") or "") >= today()),
                 key=lambda s: s["date"])
    result["next_date"] = {"date": nxt[0]["date"], "body": nxt[0]["body"]} if nxt else None
    return result
