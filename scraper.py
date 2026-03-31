import requests
import json
import os
from datetime import datetime

JOBS_FILE = "jobs.json"
KEYWORDS = ["product manager", "data", "advertising", "analytics"]

def load_existing():
    if os.path.exists(JOBS_FILE):
        with open(JOBS_FILE) as f:
            return json.load(f)
    return {}

def save_jobs(jobs):
    with open(JOBS_FILE, "w") as f:
        json.dump(jobs, f, indent=2)

def fetch_microsoft():
    print("Fetching Microsoft...")
    results = []
    for kw in KEYWORDS:
        try:
            url = f"https://apply.careers.microsoft.com/api/pcsx/search?domain=microsoft.com&query={requests.utils.quote(kw)}&start=0"
            r = requests.get(url, timeout=10)
            jobs = r.json().get("data", {}).get("positions", [])
            for j in jobs:
                locs = ", ".join(j.get("locations", []))
                results.append({
                    "id": f"msft_{j['id']}",
                    "company": "Microsoft",
                    "title": j.get("name"),
                    "location": locs,
                    "url": "https://apply.careers.microsoft.com" + j.get("positionUrl", ""),
                    "posted_ts": j.get("postedTs", 0),
                    "found_date": datetime.now().isoformat(),
                    "status": "new"
                })
        except Exception as e:
            print(f"  Microsoft error: {e}")
    return results

def run():
    print(f"=== Scraper running at {datetime.now().strftime('%Y-%m-%d %H:%M')} ===")
    existing = load_existing()
    new_count = 0
    all_jobs = fetch_microsoft()
    for job in all_jobs:
        jid = job["id"]
        if jid not in existing:
            existing[jid] = job
            new_count += 1
    save_jobs(existing)
    print(f"Done. {new_count} new jobs added. {len(existing)} total.")

if __name__ == "__main__":
    run()
