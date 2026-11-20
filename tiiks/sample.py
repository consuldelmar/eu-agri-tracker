#!/usr/bin/env python3
"""
Downloads a few real examples of each kind of data the tracker reads, so the
collector can be written against the real structure. Saves them in samples/.
Run once from the Actions tab ("Data samples"); the result is a zip to download.
"""

from __future__ import annotations

import io
import json
import os
import re
import time

import requests

EP = "https://data.europarl.europa.eu/api/v2"
LD = {"format": "application/ld+json"}
UA = ("EU-agri-tracker-samples/1.0 (personal, non-commercial; "
      f"https://github.com/{os.getenv('GITHUB_REPOSITORY', 'local')})")
OUT = "samples"
MAX_SAVE = 400_000  # characters per file

s = requests.Session()
s.headers.update({"User-Agent": UA})
log: list[str] = []


def save(name: str, text: str) -> None:
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, name), "w", encoding="utf-8") as fh:
        fh.write(text[:MAX_SAVE])


def fetch(name: str, url: str, params: dict | None = None, accept: str | None = None,
          keep: int | None = None) -> str:
    """GET url and save the answer. keep: for JSON with a 'data' list, keep only the first N items."""
    time.sleep(0.8)
    try:
        r = s.get(url, params=params, headers={"Accept": accept} if accept else {}, timeout=60)
    except requests.RequestException as exc:
        log.append(f"{name}: ERROR {exc}")
        return ""
    text = r.text
    note = f"{name}: HTTP {r.status_code}, {len(r.content):,} bytes, {r.headers.get('Content-Type', '')}"
    if keep is not None:
        try:
            data = json.loads(text)
            if isinstance(data, dict) and isinstance(data.get("data"), list):
                note += f", {len(data['data'])} items in 'data'"
                data["data"] = data["data"][:keep]
            text = json.dumps(data, ensure_ascii=False, indent=1)
        except ValueError:
            pass
    log.append(note)
    save(name, text)
    return r.text


def main() -> None:
    year = time.gmtime().tm_year

    # --- Parliament: procedures
    fetch("ep_procedures_list_COD.json", f"{EP}/procedures", {**LD, "process-type": "COD", "limit": 5}, keep=5)
    big = fetch("ep_procedures_list_COD_limit1000.json", f"{EP}/procedures",
                {**LD, "process-type": "COD", "limit": 1000, "offset": 0}, keep=3)
    try:
        ids = [d.get("id") or d.get("process_id") for d in json.loads(big).get("data", [])]
        log.append(f"  list ids sample (first/last): {ids[:3]} ... {ids[-3:]}")
    except (ValueError, AttributeError):
        pass
    fetch("ep_procedure_2024-0319.json", f"{EP}/procedures/2024-0319", LD)
    fetch("ep_procedure_2024-0319_events.json", f"{EP}/procedures/2024-0319/events", LD)
    fetch("ep_procedure_2025-0241.json", f"{EP}/procedures/2025-0241", LD)   # a 2025 file, likely ongoing
    fetch("ep_procedures_list_DEA.json", f"{EP}/procedures", {**LD, "process-type": "DEA", "limit": 5}, keep=5)
    feed = fetch("ep_procedures_feed_one_day.xml", f"{EP}/procedures/feed", {"timeframe": "one-day"})

    # One procedure that appears in the feed (fresh example)
    m = re.search(r"eli/dl/proc/(\d{4}-\d{4}[A-Z]?)", feed or "")
    if m:
        fetch(f"ep_procedure_from_feed_{m.group(1)}.json", f"{EP}/procedures/{m.group(1)}", LD)

    # --- Parliament: plenary
    meetings = fetch("ep_meetings_year.json", f"{EP}/meetings", {**LD, "year": year, "limit": 100}, keep=5)
    today = time.strftime("%Y-%m-%d", time.gmtime())
    dates = sorted({d for d in re.findall(r"MTG-PL-(\d{4}-\d{2}-\d{2})", meetings or "")})
    past = [d for d in dates if d <= today]
    future = [d for d in dates if d > today]
    log.append(f"  plenary dates: {len(dates)} found, last past {past[-1:]} next {future[:1]}")
    if past:
        sid = f"MTG-PL-{past[-1]}"
        fetch("ep_meeting_past.json", f"{EP}/meetings/{sid}", LD)
        fetch("ep_meeting_past_activities.json", f"{EP}/meetings/{sid}/activities", {**LD, "limit": 20}, keep=20)
        fetch("ep_meeting_past_vote_results.json", f"{EP}/meetings/{sid}/vote-results", {**LD, "limit": 3}, keep=3)
        fetch("ep_meeting_past_decisions.json", f"{EP}/meetings/{sid}/decisions", {**LD, "limit": 2}, keep=2)
    if future:
        sid = f"MTG-PL-{future[0]}"
        fetch("ep_meeting_next.json", f"{EP}/meetings/{sid}", LD)
        fetch("ep_meeting_next_foreseen.json", f"{EP}/meetings/{sid}/foreseen-activities",
              {**LD, "limit": 30}, keep=30)

    # --- Parliament: events (committee meetings?)
    ev = fetch("ep_events_feed_one_day.xml", f"{EP}/events/feed", {"timeframe": "one-day"})
    ev_ids = re.findall(r"<id>[^<]*?/((?:MTG|EV)[^<]*)</id>", ev or "")
    if not ev_ids:
        ev_ids = re.findall(r"eli/dl/event/([^<\"\s]+)", ev or "")
    log.append(f"  event ids sample: {ev_ids[:8]}")
    com = [e for e in ev_ids if "AGRI" in e] or [e for e in ev_ids if "-PL-" not in e]
    for i, eid in enumerate(com[:3]):
        fetch(f"ep_event_{i}_{re.sub(r'[^A-Za-z0-9_-]', '_', eid)[:60]}.json", f"{EP}/events/{eid}", LD)

    # --- Parliament: bodies and one MEP
    bodies = fetch("ep_corporate_bodies_current.json", f"{EP}/corporate-bodies/show-current",
                   {**LD, "limit": 1000})
    try:
        data = json.loads(bodies).get("data", [])
        keep = [b for b in data if b.get("label") in ("AGRI", "PECH", "REGI", "ENVI", "BUDG")]
        save("ep_corporate_bodies_AGRI_PECH_REGI.json", json.dumps(keep, ensure_ascii=False, indent=1))
        log.append(f"  current bodies: {len(data)}; AGRI/PECH/REGI entries: "
                   f"{[(b.get('label'), b.get('id')) for b in keep]}")
        agri = next((b for b in keep if b.get("label") == "AGRI"), None)
        if agri:
            bid = str(agri.get("id", "")).split("/")[-1]
            fetch("ep_corporate_body_AGRI.json", f"{EP}/corporate-bodies/{bid}", LD)
    except (ValueError, AttributeError) as exc:
        log.append(f"  bodies parse error: {exc}")
    fetch("ep_mep_1294.json", f"{EP}/meps/1294", LD)
    fetch("ep_committee_documents_AGRI.json", f"{EP}/committee-documents",
          {**LD, "committee": "AGRI", "parliamentary-term": 10, "limit": 3}, keep=3)

    # --- Council feeds
    feeds = {
        "council_meetings_agri.xml": "https://www.consilium.europa.eu/en/rss/meetings.ashx?cat=agri",
        "council_meetings_wp_agri.xml": "https://www.consilium.europa.eu/en/rss/meetings.ashx?cat=wp-agri",
        "council_meetings_coreper.xml": "https://www.consilium.europa.eu/en/rss/meetings.ashx?cat=coreper",
        "council_register_THMAF.xml": "https://www.consilium.europa.eu/en/register/rss/THMAF.xml",
        "council_register_AGNDCSA.xml": "https://www.consilium.europa.eu/en/register/rss/AGNDCSA.xml",
        "council_register_ACT.xml": "https://www.consilium.europa.eu/en/register/rss/ACT.xml",
        "council_press.xml": "https://www.consilium.europa.eu/en/rss/pressreleases.ashx",
    }
    thmaf = ""
    for name, url in feeds.items():
        text = fetch(name, url)
        if name.endswith("THMAF.xml"):
            thmaf = text

    # First pages of a few Council documents: do they state the interinstitutional file number?
    try:
        from pypdf import PdfReader
    except ImportError:
        PdfReader = None
    links = re.findall(r"<link>\s*(https?://data\.consilium\.europa\.eu/doc/document/[^<\s]+)\s*</link>", thmaf)
    found = []
    for i, link in enumerate(links[:8]):
        time.sleep(0.8)
        try:
            r = s.get(link, timeout=60)
        except requests.RequestException as exc:
            log.append(f"council pdf {i}: ERROR {exc}")
            continue
        if PdfReader and r.content[:5] == b"%PDF-":
            pages = None
            try:
                reader = PdfReader(io.BytesIO(r.content))
                pages = len(reader.pages)
                first = "\n".join((reader.pages[p].extract_text() or "") for p in range(min(2, pages)))
            except Exception as exc:  # noqa: BLE001 - report any PDF problem
                first = f"(could not read PDF: {exc})"
            refs = re.findall(r"\d{4}/\d{4}\s*\([A-Z]{3}\)", first)
            found.append({"link": link, "pages": pages, "refs_found": refs,
                          "first_page_start": first[:1500]})
        else:
            found.append({"link": link, "status": r.status_code, "type": r.headers.get("Content-Type")})
    save("council_pdf_first_pages.json", json.dumps(found, ensure_ascii=False, indent=1))
    log.append(f"council pdfs read: {len(found)}; with file numbers: {sum(1 for f in found if f.get('refs_found'))}")

    save("_log.txt", "\n".join(log))
    print("\n".join(log))
    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write("## Data samples\n\n```\n" + "\n".join(log) + "\n```\n")


if __name__ == "__main__":
    main()
