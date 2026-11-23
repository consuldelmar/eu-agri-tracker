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
from . import notify, plenary, progress, site
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
    http = Http(min_gap=1.0, budget=int(os.getenv("TRACKER_REQUEST_BUDGET", "12000")))
    status: dict = {"started": now_iso()}
    old = load_records()
    ep_state = load("ep_state.json", {})
    council_state = load("council_state.json", {})
    notify_state = load("notify_state.json", {})
    changes_log = load("changes.json", [])
    for c in changes_log:      # entries saved before importance levels existed
        if "importance" not in c:
            c["importance"] = "high" if c.get("kind") in ("stage", "status", "scheduled") else "low"
    problems = []

    # Changes are reported only once a complete first load existed before this run
    was_complete = bool(ep_state.get("bootstrap_done"))

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

    # 1b. Plenary sessions (agenda, debates, votes and results)
    log("Plenary…")
    plenary_state = load("plenary_days.json", {})
    try:
        status["plenary"] = plenary.collect(cfg, http, plenary_state)
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        problems.append(f"Plenary: {type(exc).__name__}: {exc}")

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
    council_docs = [d for d in council["docs"].values() if d.get("kind") != "agenda"] + \
        council_mod.latest_agendas(list(council["docs"].values()))

    # Files cited in the Council's agriculture & fisheries documents that the tracker lacks:
    # from the Parliament if it has them, otherwise as Council-only files (e.g. fishing opportunities)
    first_extra = ep_state.get("extra_init") != 2       # rule added or changed: its files are not news
    cited = sorted({r for r in council_mod.agri_refs(council_docs)
                    if int(r[:4]) >= int(cfg["since_year"]) or r in cfg["follow"]})
    for ref in [r for r, rec in records.items() if rec.get("council_only") or
                (rec.get("scope") == [ep_mod.COUNCIL_CITED] and r not in cited)]:
        records.pop(ref)
    extra_refs: set[str] = set()
    try:
        # also_track / follow numbers the Parliament does not have become Council-only files too
        manual = set(cfg["also_track"]) | set(cfg["follow"])
        todo = sorted({r for r in set(cited) | manual if r not in records})
        found, missing = ep_mod.fetch_extra(cfg, http, todo, ep_state)
        for ref, rec in found.items():
            rec["scope"] = [ep_mod.COUNCIL_CITED] if ref in cited else ["seguimiento manual"]
            records[ref] = rec
        for ref in missing:
            records[ref] = council_mod.council_only_record(
                ref, [d for d in council_docs if ref in d.get("refs", [])])
            if ref not in cited:
                records[ref]["scope"] = ["seguimiento manual (no está en el Parlamento)"]
        extra_refs = set(found) | set(missing)
        status["council_cited"] = {"from_parliament": len(found), "council_only": len(missing)}
        log(f"  files cited by the Council: {len(found)} added from the Parliament, {len(missing)} Council-only")
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        problems.append(f"Council-cited files: {type(exc).__name__}: {exc}")
    ep_state["council_cited"] = cited
    ep_state["extra_init"] = 2

    by_ref_docs: dict[str, list] = {}
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
        # Own tags (config.yml) and where the file stands in its procedure
        text = f"{rec.get('title', '')} {rec.get('title_en', '')}".lower()
        tags = [t for t, refs in cfg["tags"].items() if ref in refs]
        tags += [t for t, words in cfg["tag_words"].items() if t not in tags and any(w in text for w in words)]
        rec["tags"] = tags
        rec["progress"] = progress.compute(rec)

    # 4. What changed (skipped while the first full load is still in progress)
    bootstrapping = not ep_state.get("bootstrap_done")
    if old and was_complete and not status.get("parliament", {}).get("parser_changed"):
        found = changes_mod.diff_all(old, records)
        if first_extra:      # files added by this new rule are not news
            found = [c for c in found if not (c["kind"] == "new" and c["ref"] in extra_refs)]
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
        notify_state, followed = notify.run(records, changes_log, cfg, notify_state, site_url())
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
    save("council_state.json", {"docs": council["docs"], "first_run_done": council.get("first_run_done", False),
                                "version": council.get("version", 1),
                                "numbers_checked": council.get("numbers_checked",
                                                              council_state.get("numbers_checked", {}))})
    save("council_calendar.json", council["calendar"])
    save("notify_state.json", notify_state)
    save("plenary_days.json", plenary_state)
    save("changes.json", changes_log)
    status.update({"finished": now_iso(), "requests": http.count, "request_errors": http.errors,
                   "files": len(records), "problems": problems})
    save("status.json", status)
    site.build(records, calendar, changes_log, council, cfg, status, plenary_state.get("days", {}))

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
