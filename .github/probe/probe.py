"""Temporary: what the EP Open Data API gives about plenary sessions (agenda, debates, votes)."""
import json, os, time, requests
API = "https://data.europarl.europa.eu/api/v2"
os.makedirs("out", exist_ok=True)
S = requests.Session(); S.headers["User-Agent"] = "eu-agri-tracker probe"
def get(name, path, **params):
    params.setdefault("format", "application/ld+json")
    for i in range(4):
        try:
            r = S.get(API + path, params=params, timeout=90)
        except Exception as e:
            print(name, "ERR", e); return None
        if r.status_code == 429:
            time.sleep(int(r.headers.get("Retry-After", 10))); continue
        break
    n = ""
    try:
        d = r.json(); n = len(d.get("data", [])) if isinstance(d, dict) else ""
    except Exception:
        d = None
    print(f"{name}: {r.status_code} {len(r.content)} bytes items={n} {path} {params}")
    open(f"out/{name}.json", "wb").write(r.content)
    time.sleep(0.5)
    return d
for y in (2024, 2025, 2026):
    get(f"meetings_{y}", "/meetings", year=y, limit=500)
for day in ("2026-11-23", "2026-11-25", "2026-11-12", "2025-03-12", "2024-09-17", "2024-07-18"):
    mid = f"MTG-PL-{day}"
    get(f"{day}_meeting", f"/meetings/{mid}")
    get(f"{day}_foreseen", f"/meetings/{mid}/foreseen-activities", limit=1000)
    get(f"{day}_activities", f"/meetings/{mid}/activities", limit=1000)
    get(f"{day}_decisions", f"/meetings/{mid}/decisions", limit=2000)
    get(f"{day}_votes", f"/meetings/{mid}/vote-results", limit=500)
get("psd_list", "/plenary-session-documents", limit=20)
get("oj_doc", "/plenary-session-documents/OJQ-10-2026-11-23")
get("oj_doc_items", "/plenary-session-documents/OJQ-10-2026-11-23/items", limit=500)
get("plenary_docs", "/plenary-documents", limit=20)
for u in ("https://www.europarl.europa.eu/doceo/document/OJQ-10-2026-11-23_EN.html",
          "https://www.europarl.europa.eu/plenary/en/agendas.html",
          "https://data.europarl.europa.eu/distribution/doc/OJQ-10-2026-11-23_en.xml",
          "https://www.europarl.europa.eu/doceo/document/OJQ-10-2026-11-23_EN.xml"):
    try:
        r = S.get(u, timeout=60); print("URL", r.status_code, len(r.content), r.headers.get("content-type"), u)
        open("out/url_" + u.rsplit("/", 1)[1][:60], "wb").write(r.content)
    except Exception as e:
        print("URL ERR", u, e)
