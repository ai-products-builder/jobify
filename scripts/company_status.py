"""
Jobify Company Status Check
============================

Runs daily (or on-demand) to verify which company scrapers are actually
returning data. Catches the silent-zero-results bug class (HubSpot, Tubi,
Salesforce, ServiceNow) before it goes unnoticed for weeks.

For each company registered in scraper.py (GREENHOUSE_COMPANIES,
LEVER_COMPANIES, WORKDAY_COMPANIES) plus the dedicated fetchers
(Microsoft, Amazon, Netflix, Trade Desk, etc.), this script:

  1. Hits the underlying ATS endpoint with a non-keyword-filtered request
  2. Records HTTP status, total jobs returned, and any errors
  3. Classifies each company as: OK / EMPTY / HTTP_ERROR / TIMEOUT / EXCEPTION
  4. Writes results to scripts/company_status.json
  5. Prints a summary table grouped by status

Run locally:    python3 scripts/company_status.py
Run in CI:      add to .github/workflows/score_jobs.yml after scraper step

The output makes it trivial to:
  - Catch wrong slugs (HTTP_ERROR or 404)
  - Catch sites that block scrapers (HTTP 403 / cloudflare)
  - Catch live boards that returned 0 jobs (EMPTY — could be real or filter
    misalignment)
  - Track total coverage: "267 companies tracked, 245 healthy, 22 broken"
"""
import requests
import json
import os
import sys
from datetime import datetime
from time import sleep

# Import the registries from scraper.py so this stays in sync automatically.
# Handles three layouts:
#   1. Both files in scripts/         (this file's dir)
#   2. Both files at repo root        (cwd)
#   3. company_status.py in scripts/, scraper.py at repo root   ← your layout
_this_dir = os.path.dirname(os.path.abspath(__file__))
_repo_root = os.path.dirname(_this_dir)  # parent of scripts/
for _p in (_this_dir, _repo_root, os.getcwd(), "scripts"):
    if _p and _p not in sys.path:
        sys.path.insert(0, _p)

from scraper import (
    GREENHOUSE_COMPANIES,
    LEVER_COMPANIES,
    WORKDAY_COMPANIES,
)

STATUS_FILE = os.path.join(os.path.dirname(__file__), "company_status.json")
TIMEOUT = 10
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}


# ─── Probes ──────────────────────────────────────────────────────────────────
def probe_greenhouse(slug):
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        if r.status_code != 200:
            return {"status": "HTTP_ERROR", "http_code": r.status_code, "jobs_total": 0, "url": url}
        data = r.json()
        total = len(data.get("jobs", []))
        return {
            "status": "OK" if total > 0 else "EMPTY",
            "http_code": 200,
            "jobs_total": total,
            "url": url,
        }
    except requests.Timeout:
        return {"status": "TIMEOUT", "http_code": 0, "jobs_total": 0, "url": url}
    except Exception as e:
        return {"status": "EXCEPTION", "http_code": 0, "jobs_total": 0, "url": url, "error": str(e)[:200]}


def probe_lever(slug):
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        if r.status_code != 200:
            return {"status": "HTTP_ERROR", "http_code": r.status_code, "jobs_total": 0, "url": url}
        data = r.json()
        total = len(data) if isinstance(data, list) else 0
        return {
            "status": "OK" if total > 0 else "EMPTY",
            "http_code": 200,
            "jobs_total": total,
            "url": url,
        }
    except requests.Timeout:
        return {"status": "TIMEOUT", "http_code": 0, "jobs_total": 0, "url": url}
    except Exception as e:
        return {"status": "EXCEPTION", "http_code": 0, "jobs_total": 0, "url": url, "error": str(e)[:200]}


def probe_smartrecruiters(slug):
    url = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=1"
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        if r.status_code != 200:
            return {"status": "HTTP_ERROR", "http_code": r.status_code, "jobs_total": 0, "url": url}
        data = r.json()
        total = data.get("totalFound", len(data.get("content", [])))
        return {
            "status": "OK" if total > 0 else "EMPTY",
            "http_code": 200,
            "jobs_total": total,
            "url": url,
        }
    except requests.Timeout:
        return {"status": "TIMEOUT", "http_code": 0, "jobs_total": 0, "url": url}
    except Exception as e:
        return {"status": "EXCEPTION", "http_code": 0, "jobs_total": 0, "url": url, "error": str(e)[:200]}


def probe_workday(tenant, wd_num, site):
    base = f"https://{tenant}.wd{wd_num}.myworkdayjobs.com"
    url = f"{base}/wday/cxs/{tenant}/{site}/jobs"
    try:
        payload = {"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""}
        r = requests.post(url, json=payload, headers={
            "User-Agent": "Mozilla/5.0",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Referer": f"{base}/en-US/{site}",
        }, timeout=TIMEOUT)
        if r.status_code != 200:
            return {"status": "HTTP_ERROR", "http_code": r.status_code, "jobs_total": 0, "url": url}
        data = r.json()
        # Workday returns "total" in some responses; otherwise count jobPostings
        total = data.get("total", len(data.get("jobPostings", [])))
        return {
            "status": "OK" if total > 0 else "EMPTY",
            "http_code": 200,
            "jobs_total": total,
            "url": url,
        }
    except requests.Timeout:
        return {"status": "TIMEOUT", "http_code": 0, "jobs_total": 0, "url": url}
    except Exception as e:
        return {"status": "EXCEPTION", "http_code": 0, "jobs_total": 0, "url": url, "error": str(e)[:200]}


# ─── Dedicated fetchers — probe their actual endpoints ───────────────────────
def probe_microsoft():
    url = "https://apply.careers.microsoft.com/api/pcsx/search?domain=microsoft.com&query=product&start=0"
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        if r.status_code != 200:
            return {"status": "HTTP_ERROR", "http_code": r.status_code, "jobs_total": 0, "url": url}
        total = len(r.json().get("data", {}).get("positions", []))
        return {"status": "OK" if total > 0 else "EMPTY", "http_code": 200, "jobs_total": total, "url": url}
    except Exception as e:
        return {"status": "EXCEPTION", "http_code": 0, "jobs_total": 0, "url": url, "error": str(e)[:200]}


def probe_amazon():
    url = "https://amazon.jobs/en/search.json?base_query=product&loc_query=remote&result_limit=20"
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        if r.status_code != 200:
            return {"status": "HTTP_ERROR", "http_code": r.status_code, "jobs_total": 0, "url": url}
        total = len(r.json().get("jobs", []))
        return {"status": "OK" if total > 0 else "EMPTY", "http_code": 200, "jobs_total": total, "url": url}
    except Exception as e:
        return {"status": "EXCEPTION", "http_code": 0, "jobs_total": 0, "url": url, "error": str(e)[:200]}


def probe_netflix():
    url = "https://explore.jobs.netflix.net/api/apply/v2/jobs?domain=netflix.com&start=0&num=10&Region=ucan"
    try:
        r = requests.get(url, headers={**HEADERS, "Referer": "https://explore.jobs.netflix.net/careers"}, timeout=TIMEOUT)
        if r.status_code != 200:
            return {"status": "HTTP_ERROR", "http_code": r.status_code, "jobs_total": 0, "url": url}
        total = len(r.json().get("positions", []))
        return {"status": "OK" if total > 0 else "EMPTY", "http_code": 200, "jobs_total": total, "url": url}
    except Exception as e:
        return {"status": "EXCEPTION", "http_code": 0, "jobs_total": 0, "url": url, "error": str(e)[:200]}


def probe_trade_desk():
    url = "https://careers.thetradedesk.com/api/apply/v2/jobs?domain=thetradedesk.com&start=0&num=10&keyword=product"
    try:
        r = requests.get(url, headers={**HEADERS, "Referer": "https://careers.thetradedesk.com/"}, timeout=TIMEOUT)
        if r.status_code != 200:
            return {"status": "HTTP_ERROR", "http_code": r.status_code, "jobs_total": 0, "url": url}
        total = len(r.json().get("positions", []))
        return {"status": "OK" if total > 0 else "EMPTY", "http_code": 200, "jobs_total": total, "url": url}
    except Exception as e:
        return {"status": "EXCEPTION", "http_code": 0, "jobs_total": 0, "url": url, "error": str(e)[:200]}


def probe_deloitte():
    url = "https://apply.deloitte.com/en_US/careers/SearchJobs/product?projectOffset=0&projectSort=POSTING_DATE"
    try:
        r = requests.get(url, headers={**HEADERS, "X-Requested-With": "XMLHttpRequest"}, timeout=TIMEOUT)
        if r.status_code != 200:
            return {"status": "HTTP_ERROR", "http_code": r.status_code, "jobs_total": 0, "url": url}
        total = len(r.json().get("projectList", []))
        return {"status": "OK" if total > 0 else "EMPTY", "http_code": 200, "jobs_total": total, "url": url}
    except Exception as e:
        return {"status": "EXCEPTION", "http_code": 0, "jobs_total": 0, "url": url, "error": str(e)[:200]}


def probe_intuit():
    url = "https://jobs.intuit.com/api/jobs?query=product&page=1&pageSize=10"
    try:
        r = requests.get(url, headers={**HEADERS, "Referer": "https://jobs.intuit.com/search-jobs"}, timeout=TIMEOUT)
        if r.status_code != 200:
            return {"status": "HTTP_ERROR", "http_code": r.status_code, "jobs_total": 0, "url": url}
        data = r.json()
        total = len(data.get("jobs") or data.get("results") or [])
        return {"status": "OK" if total > 0 else "EMPTY", "http_code": 200, "jobs_total": total, "url": url}
    except Exception as e:
        return {"status": "EXCEPTION", "http_code": 0, "jobs_total": 0, "url": url, "error": str(e)[:200]}


# ─── TODO companies (no fetcher built yet) ───────────────────────────────────
# Same list as the TODO section in scraper.py — kept here for status tracking
# so the dashboard knows the total target universe vs what's actually scraped.
TODO_COMPANIES = [
    "Samsung Ads", "Triton Digital", "NBCUniversal", "Comcast",
    "Disney Entertainment & ESPN Tech", "Warner Bros. Discovery",
    "Paramount", "Fox", "AEG Presents", "AXS", "Ticketmaster", "Tixr",
    "Klear", "Cohley", "Fandango", "Starz", "XUMO",
    "Apple", "Google", "Meta", "Oracle", "IBM", "Cisco", "CrowdStrike",
    "Saviynt", "Kiteworks", "Domotz", "Wispr Flow", "Belkin", "Newegg",
    "Panasonic", "Epson America", "Samsung", "Sony PlayStation",
    "Electronic Arts", "Zynga", "2K", "Blizzard Entertainment",
    "Alteryx", "Coupa Software", "EPAM Systems", "o9 Solutions",
    "OpenText", "Omnissa", "Publicis Sapient", "Aderant",
    "TikTok",
    "Truist Bank", "UPS", "AT&T", "Verizon", "Bank of America",
    "Wells Fargo", "JPMorgan Chase", "Citi", "USAA", "BIP Wealth",
    "CarMax", "FICO", "FIS", "Fiserv", "Global Payments", "Acuity Brands",
    "LexisNexis Risk Solutions", "ARRIS", "Macy's", "Marriott International",
    "Dollar General", "Target", "Kroger Technology", "Walker Edison",
    "PayPal", "Rocket Companies", "Aletheia", "Alogent", "Americor",
    "Invesco", "Kemper", "First American Trust", "GoodLeap",
    "Guaranteed Rate", "Instant Financial", "Payroc", "Purchasing Power", "Q2",
    "Quest Diagnostics", "Kaiser Permanente", "Medtronic",
    "Thermo Fisher Scientific", "Siemens Healthineers", "Johnson & Johnson",
    "Optum", "Mahmee", "Mediflix", "UpToDate", "Zynx", "FitOn",
    "Care.com", "Wider Circle", "Philips", "Farmers Insurance",
    "Best Buy", "BestReviews", "Glassdoor", "Nike", "Red Bull",
    "Mint Mobile", "Aaron's", "Zoro US", "Saki Products", "DRINKS",
    "Fullsend", "Hapn", "IDIQ", "IOGEAR", "Internet Brands", "Spokeo",
    "Fearless Records", "9 Count",
    "NICE", "Nokia", "Nordson", "ProSearch", "Routeware", "RRD",
    "Siemens", "Sierra Wireless", "Xero", "YouVersion", "Your App Hero",
    "UJET.cx", "Vayner Media", "Telescope", "Network Optix", "Nimble",
    "Raiven", "McGraw Hill", "Elevate K-12", "Conservice", "PeopleReady",
    "Cyncly", "Mitsubishi Electric Trane", "Neptune Technology Group",
    "JBT Marel", "LA Clippers", "EY", "Blitz.gg", "Fortna",
    "General Motors", "T-Mobile", "TK Elevator", "The Weather Channel",
    "Roo", "TechStyleOS", "BlueLabel", "DaySmart", "Digital Element",
    "Dover Food Retail", "EverConnect", "Experian", "Follett Software",
    "GlobalLogic", "GSMA", "Iconfactory", "Ipserlabs",
    "IHG Hotels & Resorts",
    # Patch-in: missed-pass companies with unknown ATS
    "GLS", "Syntellis", "WABE",
]


# ─── Main check ──────────────────────────────────────────────────────────────
def check_all():
    results = []

    print(f"\n{'='*60}")
    print(f"Jobify Company Status — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*60}\n")

    # Greenhouse
    print(f"Checking {len(GREENHOUSE_COMPANIES)} Greenhouse companies...")
    for slug, company in GREENHOUSE_COMPANIES:
        res = probe_greenhouse(slug)
        res.update({"company": company, "ats": "greenhouse", "slug": slug})
        results.append(res)
        sleep(0.15)

    # Lever
    print(f"Checking {len(LEVER_COMPANIES)} Lever companies...")
    for slug, company in LEVER_COMPANIES:
        res = probe_lever(slug)
        res.update({"company": company, "ats": "lever", "slug": slug})
        results.append(res)
        sleep(0.15)

    # Workday
    print(f"Checking {len(WORKDAY_COMPANIES)} Workday companies...")
    for tenant, wd_num, site, company, prefix in WORKDAY_COMPANIES:
        res = probe_workday(tenant, wd_num, site)
        res.update({"company": company, "ats": "workday", "slug": f"{tenant}:{site}"})
        results.append(res)
        sleep(0.2)

    # Dedicated fetchers
    print("Checking dedicated fetchers...")
    for name, probe_fn, ats in [
        ("Microsoft", probe_microsoft, "custom-pcsx"),
        ("Amazon", probe_amazon, "custom-amazon-jobs"),
        ("Netflix", probe_netflix, "eightfold"),
        ("The Trade Desk", probe_trade_desk, "custom-eightfold"),
        ("Salesforce", lambda: probe_workday("salesforce", 12, "External_Career_Site"), "workday"),
        ("ServiceNow", lambda: probe_smartrecruiters("ServiceNow"), "smartrecruiters"),
        ("Snap", lambda: probe_workday("snapchat", 1, "snap"), "workday"),
        ("Capital One", lambda: probe_workday("capitalone", 12, "Capital_One"), "workday"),
        ("Mastercard", lambda: probe_workday("mastercard", 1, "CorporateCareers"), "workday"),
        ("Visa", lambda: probe_workday("visa", 5, "Visa"), "workday"),
        ("Walmart Connect", lambda: probe_workday("walmart", 5, "WalmartExternal"), "workday"),
        ("Deloitte", probe_deloitte, "avature"),
        ("Intuit", probe_intuit, "phenom"),
    ]:
        res = probe_fn()
        res.update({"company": name, "ats": ats, "slug": "dedicated"})
        results.append(res)
        sleep(0.2)

    # TODOs — no probe, just track
    for name in TODO_COMPANIES:
        results.append({
            "company": name,
            "ats": "TODO",
            "slug": "",
            "status": "TODO",
            "http_code": 0,
            "jobs_total": 0,
            "url": "",
        })

    return results


def summarize(results):
    by_status = {}
    for r in results:
        by_status.setdefault(r["status"], []).append(r)

    total_target = len(results)
    total_scrapers = len([r for r in results if r["status"] != "TODO"])
    healthy = len(by_status.get("OK", []))
    total_jobs = sum(r["jobs_total"] for r in results if r["status"] == "OK")

    print(f"\n{'='*60}")
    print(f"SUMMARY")
    print(f"{'='*60}")
    print(f"Total target companies:    {total_target}")
    print(f"Companies with scrapers:   {total_scrapers}")
    print(f"  ✅ OK (returned jobs):   {healthy}")
    print(f"  ⚠️  EMPTY (0 jobs):      {len(by_status.get('EMPTY', []))}")
    print(f"  ❌ HTTP_ERROR:           {len(by_status.get('HTTP_ERROR', []))}")
    print(f"  ⏱️  TIMEOUT:             {len(by_status.get('TIMEOUT', []))}")
    print(f"  💥 EXCEPTION:            {len(by_status.get('EXCEPTION', []))}")
    print(f"  ⏳ TODO (not yet built): {len(by_status.get('TODO', []))}")
    print(f"\nTotal jobs available across OK companies: {total_jobs}")
    print(f"Coverage: {healthy}/{total_target} ({100*healthy/total_target:.1f}%)")

    # Show the broken ones in detail
    for status_name, emoji in [
        ("HTTP_ERROR", "❌"),
        ("EMPTY", "⚠️ "),
        ("TIMEOUT", "⏱️ "),
        ("EXCEPTION", "💥"),
    ]:
        bucket = by_status.get(status_name, [])
        if not bucket:
            continue
        print(f"\n{emoji} {status_name} ({len(bucket)}):")
        for r in bucket[:30]:
            extra = ""
            if r.get("http_code"):
                extra = f" [HTTP {r['http_code']}]"
            if r.get("error"):
                extra += f" — {r['error'][:60]}"
            print(f"   {r['ats']:18s} {r['company']:30s} {r.get('slug', '')}{extra}")
        if len(bucket) > 30:
            print(f"   ... and {len(bucket) - 30} more")


def save_status(results):
    out = {
        "checked_at": datetime.now().isoformat(),
        "summary": {
            "total": len(results),
            "ok": len([r for r in results if r["status"] == "OK"]),
            "empty": len([r for r in results if r["status"] == "EMPTY"]),
            "http_error": len([r for r in results if r["status"] == "HTTP_ERROR"]),
            "timeout": len([r for r in results if r["status"] == "TIMEOUT"]),
            "exception": len([r for r in results if r["status"] == "EXCEPTION"]),
            "todo": len([r for r in results if r["status"] == "TODO"]),
            "total_jobs_available": sum(r["jobs_total"] for r in results if r["status"] == "OK"),
        },
        "companies": sorted(results, key=lambda x: (x["status"], x["company"])),
    }
    with open(STATUS_FILE, "w") as f:
        json.dump(out, f, indent=2)
    print(f"\n📝 Status written to {STATUS_FILE}")


if __name__ == "__main__":
    results = check_all()
    summarize(results)
    save_status(results)
