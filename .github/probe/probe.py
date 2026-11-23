"""Temporary: from which date the EP API has plenary agendas and vote results."""
import json, os, time, requests
API = "https://data.europarl.europa.eu/api/v2"
os.makedirs("out", exist_ok=True)
S = requests.Session(); S.headers["User-Agent"] = "eu-agri-tracker probe"
def get(path, **params):
    params.setdefault("format", "application/ld+json")
    for i in range(4):
        r = S.get(API + path, params=params, timeout=120)
        if r.status_code == 429:
            time.sleep(int(r.headers.get("Retry-After", 10))); continue
        break
    time.sleep(0.4)
    try:
        n = len(r.json().get("data", []))
    except Exception:
        n = 0
    return r, n
days = []
for y in (2024, 2025, 2026):
    r, n = get("/meetings", year=y, limit=500)
    days += sorted(str(m.get("activity_date"))[:10] for m in r.json()["data"])
days = [d for d in days if d >= "2024-07-16"]
print("sitting days since 2024-07-16:", len(days), days[0], days[-1])
rows = []
for d in days:
    if d > "2026-12-31":
        continue
    row = [d]
    for ep in ("foreseen-activities", "vote-results", "activities"):
        r, n = get(f"/meetings/MTG-PL-{d}/{ep}", limit=1000)
        row.append(f"{r.status_code}/{n}")
    rows.append(row); print(*row)
for ev in ("MTG-PL-2024-10-23-OJ-ITM-V-62", "MTG-PL-2024-12-16-OJ-ITM-D-40", "MTG-PL-2024-09-17-OJ-ITM-D-1"):
    r, n = get(f"/events/{ev}")
    print("event", ev, r.status_code, r.text[:1500].replace("\n", " "))
json.dump(rows, open("out/coverage.json", "w"))
