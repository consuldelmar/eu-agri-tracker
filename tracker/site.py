"""Builds the website: copies site/ to _site/ and writes the data files it reads."""

from __future__ import annotations

import json
import os
import shutil

from .common import ROOT, days_ago, now_iso, today

OUT = os.path.join(ROOT, "_site")


def _write(name: str, value) -> None:
    p = os.path.join(OUT, "data", name)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(value, fh, ensure_ascii=False, separators=(",", ":"))


def summary(rec: dict) -> dict:
    nxt = sorted((s for s in rec.get("scheduled", []) if (s.get("date") or "") >= today()),
                 key=lambda s: s["date"])
    return {
        "id": rec["id"], "ref": rec["ref"], "type": rec.get("type"), "title": rec.get("title"),
        "title_en": rec.get("title_en"), "stage": rec.get("stage"), "ongoing": rec.get("ongoing"),
        "committees": [{"code": c["code"], "role": c["role"]} for c in rec.get("committees", [])],
        "rapporteurs": [p["name"] for p in rec.get("people", []) if p.get("role") == "rapporteur"],
        "last": rec.get("last_activity"), "started": rec.get("started"),
        "followed": bool(rec.get("followed")), "follow_url": rec.get("follow_url", ""),
        "next": {"date": nxt[0]["date"], "body": nxt[0]["body"]} if nxt else None,
    }


def build(records: dict, calendar: list, changes_log: list, council: dict, cfg: dict, status: dict) -> None:
    if os.path.exists(OUT):
        shutil.rmtree(OUT)
    shutil.copytree(os.path.join(ROOT, "site"), OUT)

    repo = os.getenv("GITHUB_REPOSITORY", "")
    _write("meta.json", {
        "updated": now_iso(), "repo": repo, "committees": cfg.get("committees", []),
        "language": cfg.get("language"), "counts": {"files": len(records)}, "status": status,
    })
    _write("index.json", [summary(r) for r in records.values()])
    for rec in records.values():
        _write(f"files/{rec['id']}.json", rec)

    cutoff = days_ago(120)
    _write("calendar.json", sorted((e for e in calendar if (e.get("date") or "") >= cutoff),
                                   key=lambda e: (e["date"], e.get("body", ""))))
    recent = [c for c in changes_log if (c.get("seen") or c.get("date") or "") >= days_ago(90)]
    _write("changes.json", recent)

    people: dict[str, dict] = {}
    for rec in records.values():
        for p in rec.get("people", []):
            key = str(p.get("id") or p.get("name"))
            entry = people.setdefault(key, {"id": p.get("id"), "name": p.get("name"), "group": p.get("group"),
                                            "country": p.get("country"), "files": []})
            entry["files"].append({"ref": rec["ref"], "role": p.get("role"), "committee": p.get("committee")})
    _write("people.json", people)
    _write("council.json", {
        "docs": sorted((d for d in council.get("docs", {}).values() if d.get("refs")),
                       key=lambda d: d.get("date") or "", reverse=True)[:300],
        "press": council.get("press", []),
    })
