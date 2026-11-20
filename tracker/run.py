"""Daily run: collect, compare, notify, build the website.

    python -m tracker.run
"""

from __future__ import annotations

import os
import sys
import traceback

from . import changes as changes_mod
from . import council as council_mod
from . import ep as ep_mod
from . import notify, site
from .common import (Http, days_ago, links_for, load, load_config, log, log_lines, now_iso, path, save,
                     today)


def site_url() -> str:
    repo = os.getenv("GITHUB_REPOSITORY", "")
    if "/" not in repo:
        return ""
    owner, name = repo.split("/", 1)
    return f"https://{owner.lower()}.github.io/{name}/"


def load_records() -> dict:
    out = {}
    folder = path("files")
    if os.path.isdir(folder):
        import json
        for fn in os.listdir(folder):
            if fn.endswith(".json"):
                with open(os.path.join(folder, fn), encoding="utf-8") as fh:
                    try:
                        rec = json.load(fh)
                        out[rec["ref"]] = rec
                    except (ValueError, KeyError):
                        continue
    return out


def save_records(records: dict) -> None:
    folder = path("files")
    os.makedirs(folder, exist_ok=True)
    keep = set()
    for rec in records.values():
        name = f"{rec['id']}.json"
        keep.add(name)
        save(f"files/{name}", rec)
    for fn in os.listdir(folder):
        if fn.endswith(".json") and fn not in keep:
            os.remove(os.path.join(folder, fn))


def main() -> int:
    cfg = load_config()
    http = Http(min_gap=0.7, budget=int(os.getenv("TRACKER_REQUEST_BUDGET", "9000")))
    status: dict = {"started": now_iso()}
    old = load_records()
    ep_state = load("ep_state.json", {})
    council_state = load("council_state.json", {})
    notify_state = load("notify_state.json", {})
    changes_log = load("changes.json", [])
    problems = []

    # 1. Parliament
    log("Parliament…")
    try:
        ep_out = ep_mod.collect(cfg, http, old, ep_state)
        records, ep_calendar, ep_state = ep_out["records"], ep_out["calendar"], ep_out["state"]
        status["parliament"] = ep_out.get("status", {})
    except Exception as exc:  # noqa: BLE001 - keep yesterday's data and continue
        traceback.print_exc()
        problems.append(f"Parliament: {type(exc).__name__}: {exc}")
        records = {k: dict(v) for k, v in old.items()}
        ep_calendar = load("ep_calendar.json", [])
        status["parliament"] = {"error": str(exc)[:300]}

    # 2. Council
    log("Council…")
    try:
        council = council_mod.collect(cfg, http, council_state)
        status["council"] = council.get("status", {})
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        problems.append(f"Council: {type(exc).__name__}: {exc}")
        council = {"calendar": load("council_calendar.json", []), "docs": council_state.get("docs", {}),
                   "press": [], "status": {"error": str(exc)[:300]},
                   "first_run_done": council_state.get("first_run_done", False)}
        status["council"] = council["status"]

    # 3. Put everything together per file
    calendar = ep_calendar + council["calendar"]
    by_ref_cal: dict[str, list] = {}
    for e in calendar:
        for ref in e.get("refs", []):
            by_ref_cal.setdefault(ref, []).append(e)
    by_ref_docs: dict[str, list] = {}
    council_docs = [d for d in council["docs"].values() if d.get("kind") != "agenda"] + \
        council_mod.latest_agendas(list(council["docs"].values()))
    for d in council_docs:
        for ref in d.get("refs", []):
            by_ref_docs.setdefault(ref, []).append({k: d.get(k) for k in ("id", "code", "date", "title", "url",
                                                                           "kind", "body", "step",
                                                                           "meeting_date")})
    for ref, rec in records.items():
        rec.setdefault("links", links_for(ref))
        rec["links"].update({k: v for k, v in links_for(ref).items() if k not in rec["links"]})
        if ref in cfg["legislative_train"]:
            rec["links"]["train"] = cfg["legislative_train"][ref]
        own = rec.get("ep_scheduled", [])
        own_dates = {e["date"] for e in own}
        merged = {e["id"]: e for e in own}
        for e in by_ref_cal.get(ref, []):
            if e["id"].startswith("ep-plenary-") and e["date"] in own_dates:
                continue                       # the file's own plenary item is more precise
            merged[e["id"]] = e
        agenda = sorted(({k: e.get(k) for k in ("id", "date", "body", "title", "url", "source")}
                         for e in merged.values()), key=lambda e: (e["date"], e["body"] or ""))
        rec["agenda"] = agenda
        rec["scheduled"] = [e for e in agenda if (e.get("date") or "") >= today()]
        rec["council_docs"] = sorted(by_ref_docs.get(ref, []), key=lambda d: d.get("date") or "")

    # 4. What changed (skipped while the first full load is still in progress)
    bootstrapping = not ep_state.get("bootstrap_done")
    if old and not bootstrapping:
        found = changes_mod.diff_all(old, records)
    else:
        found = []
        log("  first load in progress: changes are not reported yet")
    seen_ids = {c["id"] for c in changes_log}
    fresh = [c for c in found if c["id"] not in seen_ids]
    for c in fresh:
        c["seen"] = today()
        c["file_id"] = records.get(c["ref"], {}).get("id")
    changes_log = [c for c in changes_log if (c.get("seen") or "") >= days_ago(180)] + fresh
    status["changes"] = len(fresh)
    log(f"  {len(fresh)} change(s) detected")

    # 5. Notifications (GitHub issues)
    log("Notifications…")
    try:
        notify_state, followed = notify.run(records, fresh, cfg, notify_state, site_url())
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        problems.append(f"Notifications: {type(exc).__name__}: {exc}")
        followed = {}
    for ref, rec in records.items():
        rec["followed"] = ref in followed
        rec["follow_url"] = followed.get(ref, "")

    # 6. Save state and build the site
    save_records(records)
    save("ep_state.json", ep_state)
    save("ep_calendar.json", ep_calendar)
    save("council_state.json", {"docs": council["docs"], "first_run_done": council.get("first_run_done", False)})
    save("council_calendar.json", council["calendar"])
    save("notify_state.json", notify_state)
    save("changes.json", changes_log)
    status.update({"finished": now_iso(), "requests": http.count, "request_errors": http.errors,
                   "files": len(records), "problems": problems})
    save("status.json", status)
    site.build(records, calendar, changes_log, council, cfg, status)

    # Run report on the GitHub run page
    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as fh:
            fh.write("## EU agri tracker run\n\n")
            fh.write(f"- Files tracked: {len(records)}\n- Changes detected: {len(fresh)}\n"
                     f"- Requests: {http.count} ({http.errors} errors)\n")
            if bootstrapping:
                fh.write(f"- First load in progress: {ep_state.get('bootstrap_note', '')}\n")
            for p in problems:
                fh.write(f"- ⚠️ {p}\n")
            fh.write("\n<details><summary>Log</summary>\n\n```\n" + "\n".join(log_lines()[-300:]) +
                     "\n```\n</details>\n")
    if problems:
        log("Problems: " + " | ".join(problems))
    # Partial failures do not fail the run: the site is still rebuilt with what was collected.
    return 0


if __name__ == "__main__":
    sys.exit(main())
