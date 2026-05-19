import requests
import json
import os
import xml.etree.ElementTree as ET
from datetime import datetime
from time import sleep

JOBS_FILE = "jobs.json"

# ─── MASTER FILTERS (single source of truth) ──────────────────────────────────
INCLUDE_KEYWORDS = [
    "product manager", "data", "advertising", "analytics",
    "program manager", "product lead", "product owner",
    "growth", "monetization", "ads", "revenue",
    "artificial intelligence", "ai product", "machine learning product",
    "generative ai", "llm", "ai platform",
    "director of product", "director of data", "director of analytics",
    "director of advertising", "director of ai", "director of growth"
]

EXCLUDE_TITLES = [
    "engineer", "account manager", "account executive",
    "software", "developer", "devops", "infrastructure",
    "sales", "recruiter", "designer", "scientist",
    "attorney", "lawyer", "finance", "accounting",
    "hr ", "human resources", "coordinator", "assistant",
    "technician", "operator", "specialist",
    "data center", "data science manager", "accountant",
    "partner growth manager", "media manager",
    "associate general counsel", "commercial ctv",
    "gm business development", "intern", "internship",
    "growth manager analyst", "general manager", "architect"
]

LOCATION_KEYWORDS = [
    "atlanta", "georgia", "remote",
    "los angeles", "irvine", "santa monica", "culver city", "ventura"
]

SEARCH_QUERIES = [
    "product manager", "data", "advertising", "analytics",
    "AI product manager", "generative AI", "director of product", "program manager"
]
SEARCH_LOCATIONS = LOCATION_KEYWORDS


# ─── FILTER FUNCTIONS ─────────────────────────────────────────────────────────
def is_relevant_title(title):
    t = title.lower()
    if any(ex in t for ex in EXCLUDE_TITLES):
        return False
    return any(kw in t for kw in INCLUDE_KEYWORDS)


def is_relevant_location(location):
    return any(lk in location.lower() for lk in LOCATION_KEYWORDS)


def passes(title, location, extra_loc=""):
    return is_relevant_title(title) and is_relevant_location(location + " " + extra_loc)


# ─── HELPERS ──────────────────────────────────────────────────────────────────
def load_existing():
    if os.path.exists(JOBS_FILE):
        with open(JOBS_FILE) as f:
            return json.load(f)
    return {}


def save_jobs(jobs):
    with open(JOBS_FILE, "w") as f:
        json.dump(jobs, f, indent=2)


def make_job(id, company, title, location, url, posted_ts=0, description=""):
    return {
        "id": id,
        "company": company,
        "title": title,
        "location": location,
        "url": url,
        "posted_ts": posted_ts,
        "found_date": datetime.now().isoformat(),
        "status": "new",
        "description": description
    }


def safe_fetch(fn, *args, **kwargs):
    """Defensive wrapper — ensures a broken scraper never crashes the full pipeline.
    Also returns the company name + count so company_status.py can audit results."""
    try:
        result = fn(*args, **kwargs)
        return result if result is not None else []
    except Exception as e:
        print(f"  ⚠️  {fn.__name__} crashed: {e}")
        return []


# ─── MICROSOFT ────────────────────────────────────────────────────────────────
def fetch_microsoft():
    print("Fetching Microsoft...")
    results = []
    for kw in SEARCH_QUERIES:
        try:
            url = f"https://apply.careers.microsoft.com/api/pcsx/search?domain=microsoft.com&query={requests.utils.quote(kw)}&start=0"
            r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
            for j in r.json().get("data", {}).get("positions", []):
                title = j.get("name", "")
                locs = ", ".join(j.get("locations", []))
                work_opt = j.get("workLocationOption", "")
                if not passes(title, locs + " " + work_opt):
                    continue
                results.append(make_job(
                    id=f"msft_{j['id']}", company="Microsoft",
                    title=title, location=locs,
                    url="https://apply.careers.microsoft.com" + j.get("positionUrl", ""),
                    posted_ts=j.get("postedTs", 0)
                ))
        except Exception as e:
            print(f"  Microsoft error ({kw}): {e}")
    print(f"  Found {len(results)} Microsoft jobs")
    return results


# ─── AMAZON ───────────────────────────────────────────────────────────────────
def fetch_amazon():
    print("Fetching Amazon...")
    results = []
    for kw in SEARCH_QUERIES:
        for loc in SEARCH_LOCATIONS:
            try:
                url = f"https://amazon.jobs/en/search.json?base_query={requests.utils.quote(kw)}&loc_query={loc}&job_count=20&result_limit=20&sort=recent"
                r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
                for j in r.json().get("jobs", []):
                    title = j.get("title", "")
                    location = j.get("location", "")
                    if not passes(title, location, loc):
                        continue
                    results.append(make_job(
                        id=f"amzn_{j.get('job_id', '')}",
                        company="Amazon", title=title, location=location,
                        url="https://amazon.jobs" + j.get("job_path", "")
                    ))
                sleep(0.3)
            except Exception as e:
                print(f"  Amazon error ({kw}/{loc}): {e}")
    print(f"  Found {len(results)} Amazon jobs")
    return results


# ─── NETFLIX ──────────────────────────────────────────────────────────────────
def fetch_netflix():
    """Netflix uses Eightfold AI ATS. API caps at 10/page — paginate."""
    print("Fetching Netflix...")
    results = []
    seen = set()
    us_indicators = [
        "united states", "los angeles", "atlanta", "california",
        "new york", "seattle", "los gatos", "beverly hills", "burbank"
    ]
    page_size = 10
    start = 0
    total_fetched = 0
    while True:
        try:
            url = (
                f"https://explore.jobs.netflix.net/api/apply/v2/jobs"
                f"?domain=netflix.com&start={start}&num={page_size}"
                f"&Teams=Product%20Management&Region=ucan"
            )
            r = requests.get(url, headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json",
                "Referer": "https://explore.jobs.netflix.net/careers"
            }, timeout=15)
            if r.status_code != 200 or not r.text.strip():
                break
            data = r.json()
            positions = data.get("positions", [])
            total_fetched += len(positions)
            print(f"  Netflix page start={start}: {len(positions)} positions")
            for j in positions:
                if not isinstance(j, dict):
                    continue
                jid = str(j.get("id", ""))
                if not jid or jid in seen:
                    continue
                seen.add(jid)
                title = j.get("name", j.get("posting_name", ""))
                raw_loc = j.get("location", "")
                if isinstance(raw_loc, list):
                    raw_loc = raw_loc[0] if raw_loc else ""
                parts = [p.strip() for p in raw_loc.split(",")]
                locs = ", ".join(parts) if parts else ""
                if not is_relevant_title(title):
                    continue
                if locs and not any(ind in locs.lower() for ind in us_indicators):
                    continue
                results.append(make_job(
                    id=f"netflix_{jid}",
                    company="Netflix",
                    title=title,
                    location=locs,
                    url=f"https://explore.jobs.netflix.net/careers?pid={jid}&domain=netflix.com"
                ))
            if len(positions) < page_size:
                break
            start += page_size
            sleep(0.5)
        except Exception as e:
            print(f"  Netflix error (start={start}): {e}")
            break
    print(f"  Netflix: fetched {total_fetched} total, {len(results)} relevant jobs")
    return results


# ─── GREENHOUSE BOARDS ────────────────────────────────────────────────────────
def fetch_greenhouse(board, company):
    print(f"Fetching {company}...")
    results = []
    try:
        url = f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        if r.status_code != 200:
            print(f"  {company}: HTTP {r.status_code} (slug '{board}' may be wrong)")
            return results
        for j in r.json().get("jobs", []):
            title = j.get("title", "")
            location = j.get("location", {}).get("name", "")
            if not passes(title, location, "remote"):
                continue
            results.append(make_job(
                id=f"{board}_{j.get('id', '')}",
                company=company, title=title, location=location,
                url=j.get("absolute_url", ""),
                description=j.get("content", "")[:500]
            ))
    except Exception as e:
        print(f"  {company} error: {e}")
    print(f"  Found {len(results)} {company} jobs")
    return results


# ─── LEVER BOARDS ─────────────────────────────────────────────────────────────
def fetch_lever(slug, company):
    """Lever public API — no auth required."""
    print(f"Fetching {company} (Lever)...")
    results = []
    try:
        url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        if r.status_code != 200:
            print(f"  {company}: HTTP {r.status_code} (slug '{slug}' may be wrong)")
            return results
        data = r.json()
        if not isinstance(data, list):
            return results
        for j in data:
            title = j.get("text", "")
            categories = j.get("categories", {})
            location = categories.get("location", "") or ""
            if not passes(title, location, "remote"):
                continue
            results.append(make_job(
                id=f"lever_{slug}_{j.get('id', '')}",
                company=company, title=title, location=location,
                url=j.get("hostedUrl", ""),
                description=j.get("descriptionPlain", "")[:500]
            ))
    except Exception as e:
        print(f"  {company} error: {e}")
    print(f"  Found {len(results)} {company} jobs")
    return results


# ─── SMARTRECRUITERS BOARDS ───────────────────────────────────────────────────
def fetch_smartrecruiters(slug, company):
    print(f"Fetching {company} (SmartRecruiters)...")
    results = []
    seen = set()
    try:
        url = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100"
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        if r.status_code != 200:
            print(f"  {company}: HTTP {r.status_code} (slug '{slug}' may be wrong)")
            return results
        for j in r.json().get("content", []):
            jid = str(j.get("id", ""))
            if jid in seen:
                continue
            seen.add(jid)
            title = j.get("name", "")
            loc_obj = j.get("location", {})
            city = loc_obj.get("city", "")
            region = loc_obj.get("region", "")
            remote = loc_obj.get("remote", False)
            location = f"{city}, {region}".strip(", ")
            if remote:
                location = "Remote" if not location else f"{location} / Remote"
            if not passes(title, location, "remote" if remote else ""):
                continue
            results.append(make_job(
                id=f"sr_{slug}_{jid}",
                company=company, title=title, location=location,
                url=f"https://careers.smartrecruiters.com/{slug}/{jid}"
            ))
    except Exception as e:
        print(f"  {company} error: {e}")
    print(f"  Found {len(results)} {company} jobs")
    return results


# ─── ASHBY BOARDS ─────────────────────────────────────────────────────────────
def fetch_ashby(slug, company):
    print(f"Fetching {company} (Ashby)...")
    results = []
    try:
        url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        if r.status_code != 200:
            print(f"  {company}: HTTP {r.status_code} (slug '{slug}' may be wrong)")
            return results
        for j in r.json().get("jobs", []):
            title = j.get("title", "")
            location = j.get("location", "")
            if not passes(title, location, "remote"):
                continue
            results.append(make_job(
                id=f"ashby_{slug}_{j.get('id', '')}",
                company=company, title=title, location=location,
                url=j.get("jobUrl", "")
            ))
    except Exception as e:
        print(f"  {company} error: {e}")
    print(f"  Found {len(results)} {company} jobs")
    return results


# ─── THE TRADE DESK ───────────────────────────────────────────────────────────
def fetch_trade_desk():
    print("Fetching The Trade Desk...")
    results = []
    seen = set()
    for kw in SEARCH_QUERIES:
        try:
            url = (
                f"https://careers.thetradedesk.com/api/apply/v2/jobs"
                f"?domain=thetradedesk.com&start=0&num=50"
                f"&keyword={requests.utils.quote(kw)}"
            )
            r = requests.get(url, headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json",
                "Referer": "https://careers.thetradedesk.com/"
            }, timeout=15)
            print(f"  Trade Desk status: {r.status_code} | {kw}")
            if r.status_code != 200 or not r.text.strip():
                continue
            data = r.json()
            for j in data.get("positions", []):
                jid = str(j.get("id", ""))
                if not jid or jid in seen:
                    continue
                seen.add(jid)
                title = j.get("name", "")
                locs = ", ".join(j.get("locations", []))
                if not passes(title, locs, "remote"):
                    continue
                results.append(make_job(
                    id=f"ttd_{jid}",
                    company="The Trade Desk", title=title, location=locs,
                    url="https://careers.thetradedesk.com/us/en/job/" + jid
                ))
            sleep(1)
        except Exception as e:
            print(f"  Trade Desk error ({kw}): {e}")
    print(f"  Found {len(results)} Trade Desk jobs")
    return results


# ─── SALESFORCE (Workday) ─────────────────────────────────────────────────────
def fetch_salesforce():
    print("Fetching Salesforce...")
    results = []
    seen = set()
    for kw in SEARCH_QUERIES:
        try:
            url = (
                "https://salesforce.wd12.myworkdayjobs.com/wday/cxs/salesforce"
                "/External_Career_Site/jobs"
            )
            payload = {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": kw}
            r = requests.post(url, json=payload, headers={
                "User-Agent": "Mozilla/5.0",
                "Content-Type": "application/json",
                "Accept": "application/json"
            }, timeout=15)
            print(f"  Salesforce status: {r.status_code} | {kw}")
            if r.status_code != 200:
                continue
            data = r.json()
            for j in data.get("jobPostings", []):
                ext_path = j.get("externalPath", "")
                jid = ext_path.strip("/").split("/")[-1]
                if jid in seen:
                    continue
                seen.add(jid)
                title = j.get("title", "")
                location = j.get("locationsText", "")
                if not passes(title, location, "remote"):
                    continue
                results.append(make_job(
                    id=f"salesforce_{jid}",
                    company="Salesforce", title=title, location=location,
                    url="https://salesforce.wd12.myworkdayjobs.com/en-US/External_Career_Site" + ext_path
                ))
        except Exception as e:
            print(f"  Salesforce error ({kw}): {e}")
    print(f"  Found {len(results)} Salesforce jobs")
    return results


# ─── SERVICENOW (SmartRecruiters) ────────────────────────────────────────────
def fetch_servicenow():
    print("Fetching ServiceNow...")
    results = []
    seen = set()
    for kw in SEARCH_QUERIES:
        try:
            url = (
                f"https://api.smartrecruiters.com/v1/companies/ServiceNow/postings"
                f"?q={requests.utils.quote(kw)}&limit=100"
            )
            r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
            print(f"  ServiceNow status: {r.status_code} | {kw}")
            if r.status_code != 200:
                continue
            data = r.json()
            for j in data.get("content", []):
                jid = str(j.get("id", ""))
                if jid in seen:
                    continue
                seen.add(jid)
                title = j.get("name", "")
                loc_obj = j.get("location", {})
                city = loc_obj.get("city", "")
                region = loc_obj.get("region", "")
                remote = loc_obj.get("remote", False)
                location = f"{city}, {region}".strip(", ")
                if remote:
                    location = "Remote" if not location else f"{location} / Remote"
                if not passes(title, location, "remote" if remote else ""):
                    continue
                results.append(make_job(
                    id=f"servicenow_{jid}",
                    company="ServiceNow", title=title, location=location,
                    url=f"https://careers.smartrecruiters.com/ServiceNow/{jid}"
                ))
        except Exception as e:
            print(f"  ServiceNow error ({kw}): {e}")
    print(f"  Found {len(results)} ServiceNow jobs")
    return results


# ─── GENERIC WORKDAY FETCHER ─────────────────────────────────────────────────
def fetch_workday(tenant, wd_num, site, company, prefix):
    """Generic Workday fetcher — works for any company on Workday ATS."""
    print(f"Fetching {company}...")
    results = []
    seen = set()
    base = f"https://{tenant}.wd{wd_num}.myworkdayjobs.com"
    api_url = f"{base}/wday/cxs/{tenant}/{site}/jobs"
    for kw in SEARCH_QUERIES:
        try:
            payload = {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": kw}
            r = requests.post(api_url, json=payload, headers={
                "User-Agent": "Mozilla/5.0",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Referer": f"{base}/en-US/{site}"
            }, timeout=15)
            if r.status_code != 200 or not r.text.strip():
                if kw == SEARCH_QUERIES[0]:  # only log once per company
                    print(f"  {company} status: {r.status_code} (tenant/site may be wrong)")
                continue
            data = r.json()
            for j in data.get("jobPostings", []):
                ext_path = j.get("externalPath", "")
                jid = ext_path.strip("/").split("/")[-1]
                if not jid or jid in seen:
                    continue
                seen.add(jid)
                title = j.get("title", "")
                location = j.get("locationsText", "")
                if not passes(title, location, "remote"):
                    continue
                results.append(make_job(
                    id=f"{prefix}_{jid}",
                    company=company, title=title, location=location,
                    url=f"{base}/en-US/{site}" + ext_path
                ))
            sleep(1)
        except Exception as e:
            print(f"  {company} error ({kw}): {e}")
    print(f"  Found {len(results)} {company} jobs")
    return results


# ─── ORIGINAL WORKDAY WRAPPERS (kept for backwards compat) ───────────────────
def fetch_snap():          return fetch_workday("snapchat", 1, "snap", "Snap", "snap")
def fetch_capital_one():   return fetch_workday("capitalone", 12, "Capital_One", "Capital One", "capitalone")
def fetch_mastercard():    return fetch_workday("mastercard", 1, "CorporateCareers", "Mastercard", "mastercard")
def fetch_visa():          return fetch_workday("visa", 5, "Visa", "Visa", "visa")
def fetch_walmart_connect(): return fetch_workday("walmart", 5, "WalmartExternal", "Walmart Connect", "walmart")


# ─── DELOITTE (Avature) ──────────────────────────────────────────────────────
def fetch_deloitte():
    print("Fetching Deloitte...")
    results = []
    seen = set()
    for kw in SEARCH_QUERIES:
        try:
            url = f"https://apply.deloitte.com/en_US/careers/SearchJobs/{requests.utils.quote(kw)}?projectOffset=0&projectSort=POSTING_DATE&projectSortDirection=DESC"
            r = requests.get(url, headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json, text/javascript, */*",
                "X-Requested-With": "XMLHttpRequest"
            }, timeout=15)
            print(f"  Deloitte status: {r.status_code} | {kw}")
            if r.status_code != 200 or not r.text.strip():
                continue
            data = r.json()
            for j in data.get("projectList", []):
                jid = str(j.get("id", ""))
                if not jid or jid in seen:
                    continue
                seen.add(jid)
                title = j.get("projectTitle", "")
                location = j.get("projectCustomField3", j.get("projectCustomField1", ""))
                if not passes(title, location, "remote"):
                    continue
                results.append(make_job(
                    id=f"deloitte_{jid}",
                    company="Deloitte", title=title, location=location,
                    url=f"https://apply.deloitte.com/en_US/careers/JobDetail/{jid}"
                ))
            sleep(1)
        except Exception as e:
            print(f"  Deloitte error ({kw}): {e}")
    print(f"  Found {len(results)} Deloitte jobs")
    return results


# ─── INTUIT (Phenom People) ──────────────────────────────────────────────────
def fetch_intuit():
    print("Fetching Intuit...")
    results = []
    seen = set()
    for kw in SEARCH_QUERIES:
        try:
            url = "https://jobs.intuit.com/api/jobs"
            params = {"query": kw, "location": "", "page": 1, "pageSize": 20, "facets": "", "sort": "relevance"}
            r = requests.get(url, params=params, headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json",
                "Referer": "https://jobs.intuit.com/search-jobs"
            }, timeout=15)
            print(f"  Intuit status: {r.status_code} | {kw}")
            if r.status_code != 200 or not r.text.strip():
                continue
            data = r.json()
            jobs_list = (data.get("jobs") or data.get("results") or
                         data.get("data", {}).get("jobs") or [])
            for j in jobs_list:
                if not isinstance(j, dict):
                    continue
                jid = str(j.get("id", j.get("jobId", "")))
                if not jid or jid in seen:
                    continue
                seen.add(jid)
                title = j.get("title", j.get("jobTitle", ""))
                location = j.get("location", j.get("jobLocation", ""))
                if isinstance(location, dict):
                    location = location.get("city", "") + ", " + location.get("state", "")
                if not passes(title, str(location), "remote"):
                    continue
                job_url = j.get("url", j.get("applyUrl", f"https://jobs.intuit.com/job/{jid}"))
                results.append(make_job(
                    id=f"intuit_{jid}",
                    company="Intuit", title=title, location=str(location),
                    url=job_url
                ))
            sleep(1)
        except Exception as e:
            print(f"  Intuit error ({kw}): {e}")
    print(f"  Found {len(results)} Intuit jobs")
    return results


# ─── Y COMBINATOR ─────────────────────────────────────────────────────────────
def fetch_ycombinator():
    print("Fetching Y Combinator...")
    results = []
    seen = set()
    for kw in SEARCH_QUERIES:
        try:
            url = (
                f"https://www.workatastartup.com/jobs.json"
                f"?query={requests.utils.quote(kw)}&remote=yes&role=pm&sortBy=default"
            )
            r = requests.get(url, headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json",
                "Referer": "https://www.workatastartup.com/"
            }, timeout=15)
            print(f"  YC status: {r.status_code} | {kw}")
            if r.status_code != 200 or not r.text.strip():
                continue
            jobs = r.json()
            if not isinstance(jobs, list):
                continue
            for j in jobs:
                jid = str(j.get("id", ""))
                if not jid or jid in seen:
                    continue
                seen.add(jid)
                title = j.get("title", "")
                location = j.get("locations", ["Remote"])[0] if j.get("locations") else "Remote"
                company_name = j.get("company", {}).get("name", "YC Startup")
                if not is_relevant_title(title):
                    continue
                results.append(make_job(
                    id=f"yc_{jid}",
                    company=f"YC: {company_name}", title=title, location=location,
                    url=j.get("url", "https://www.workatastartup.com/jobs")
                ))
            sleep(1)
        except Exception as e:
            print(f"  YC error ({kw}): {e}")
    print(f"  Found {len(results)} YC jobs")
    return results


# ─── WE WORK REMOTELY ─────────────────────────────────────────────────────────
def fetch_weworkremotely():
    print("Fetching We Work Remotely...")
    results = []
    try:
        url = "https://weworkremotely.com/categories/remote-management-and-finance-jobs.rss"
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        root = ET.fromstring(r.content)
        for item in root.findall(".//item"):
            title = item.findtext("title", "")
            link = item.findtext("link", "")
            if not is_relevant_title(title):
                continue
            results.append(make_job(
                id=f"wwr_{abs(hash(link))}",
                company="We Work Remotely", title=title,
                location="Remote", url=link
            ))
    except Exception as e:
        print(f"  WWR error: {e}")
    print(f"  Found {len(results)} WWR jobs")
    return results


# ─── DICE ─────────────────────────────────────────────────────────────────────
def fetch_dice():
    print("Fetching Dice...")
    results = []
    for kw in SEARCH_QUERIES:
        for loc in SEARCH_LOCATIONS:
            try:
                url = f"https://job-search-api.svc.dhigroupinc.com/v1/dice/jobs/search?q={requests.utils.quote(kw)}&location={loc}&country=US&page=1&pageSize=20&language=en"
                r = requests.get(url, headers={"User-Agent": "Mozilla/5.0", "x-api-key": "1YAt0R9wBg4WfsF9VB2778F5CHLAPMVW3WAZcKd8"}, timeout=10)
                for j in r.json().get("data", []):
                    title = j.get("title", "")
                    location = j.get("location", "")
                    if not passes(title, location, loc):
                        continue
                    results.append(make_job(
                        id=f"dice_{j.get('id', '')}",
                        company=j.get("advertiserName") or "Dice",
                        title=title, location=location,
                        url=j.get("applyDataItems", [{}])[0].get("applyUrl", "https://dice.com")
                    ))
            except Exception as e:
                print(f"  Dice error ({kw}/{loc}): {e}")
    print(f"  Found {len(results)} Dice jobs")
    return results


# ─── BUILT IN ─────────────────────────────────────────────────────────────────
def fetch_builtin():
    print("Fetching Built In...")
    results = []
    seen = set()
    for kw in SEARCH_QUERIES:
        try:
            url = (
                f"https://api.builtin.com/api/jobs"
                f"?title={requests.utils.quote(kw)}&remote=true&page=1&perPage=20"
            )
            r = requests.get(url, headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json",
                "Referer": "https://builtin.com/"
            }, timeout=15)
            print(f"  Built In status: {r.status_code} | {kw}")
            if r.status_code != 200 or not r.text.strip():
                continue
            data = r.json()
            for j in data.get("jobs", []):
                jid = str(j.get("id", ""))
                if not jid or jid in seen:
                    continue
                seen.add(jid)
                title = j.get("title", "")
                location = j.get("builtInJobLocation", {}).get("name", "Remote")
                if not passes(title, location, "remote"):
                    continue
                results.append(make_job(
                    id=f"builtin_{jid}",
                    company=j.get("company", {}).get("name", "Built In"),
                    title=title, location=location,
                    url="https://builtin.com/job/" + str(j.get("slug", ""))
                ))
            sleep(1)
        except Exception as e:
            print(f"  Built In error ({kw}): {e}")
    print(f"  Found {len(results)} Built In jobs")
    return results


# ─── BROWSE LINKS ─────────────────────────────────────────────────────────────
def fetch_browse_links():
    print("Adding browse links...")
    results = []

    for loc in ["Atlanta, GA", "Remote", "California"]:
        for kw in ["product+manager", "data+product+manager", "advertising+product+manager"]:
            results.append(make_job(
                id=f"indeed_{kw}_{loc.lower().replace(' ', '_').replace(',', '')}",
                company="Indeed",
                title=f"Browse: {kw.replace('+', ' ').title()} — {loc}",
                location=loc,
                url=f"https://www.indeed.com/jobs?q={kw}&l={requests.utils.quote(loc)}&sort=date"
            ))

    for id, loc, url in [
        ("glassdoor_atlanta", "Atlanta, GA", "https://www.glassdoor.com/Job/atlanta-product-manager-jobs-SRCH_IL.0,7_IC1155583_KO8,23.htm"),
        ("glassdoor_remote", "Remote", "https://www.glassdoor.com/Job/remote-product-manager-jobs-SRCH_IL.0,6_IS11047_KO7,23.htm"),
        ("glassdoor_california", "California", "https://www.glassdoor.com/Job/california-product-manager-jobs-SRCH_IL.0,10_IS2280_KO11,26.htm"),
    ]:
        results.append(make_job(id=id, company="Glassdoor", title=f"Browse: PM Jobs — {loc} on Glassdoor", location=loc, url=url))

    for id, company, url, loc in [
        ("wellfound", "Wellfound", "https://wellfound.com/role/l/product-manager", "Remote / Atlanta / California"),
        ("underdog", "Underdog.io", "https://underdog.io/jobs?role=product-manager", "Remote"),
        ("trueup", "True Up", "https://www.trueup.io/jobs?query=product+manager&remoteType=remote", "Remote / Atlanta / California"),
        ("techfetch", "Tech Fetch", "https://www.techfetch.com/job/search?jobTitle=product+manager&location=remote", "Remote / Atlanta / California"),
        ("pmhq", "Product Manager HQ", "https://productmanagerhq.com/jobs/", "Remote"),
        ("mindtheproduct", "Mind the Product", "https://jobs.mindtheproduct.com/", "Remote"),
        ("productfolks", "The Product Folks", "https://www.theproductfolks.com/jobs", "Remote"),
        ("productjobs", "ProductJobs.com", "https://productjobs.com/", "Remote"),
    ]:
        results.append(make_job(id=f"{id}_browse", company=company, title=f"Browse: PM Jobs on {company}", location=loc, url=url))

    print(f"  Added {len(results)} browse links")
    return results


# ─── COMPANY REGISTRY (used by run() AND company_status.py) ──────────────────
# Format: (Display Name, ATS spec)
# Specs:
#   greenhouse:<slug>
#   lever:<slug>
#   smartrecruiters:<slug>
#   ashby:<slug>
#   workday:<tenant>:<wd_num>:<site>
#
# Companies needing dedicated/custom fetchers (Microsoft, Amazon, Netflix,
# Trade Desk, Salesforce, ServiceNow, Snap, Capital One, Mastercard, Visa,
# Walmart Connect, Deloitte, Intuit) are NOT in this list — they have their
# own functions called from run() below.
#
# Companies marked TODO at the bottom are pending ATS verification — they
# do NOT scrape but ARE tracked by company_status.py so we know what's missing.

GREENHOUSE_COMPANIES = [
    # ── Already in scraper (kept here for unified status tracking) ──
    ("reddit", "Reddit"),
    ("roku", "Roku"),
    ("unity3d", "Unity"),
    ("tubitv", "Tubi"),
    ("hubspotjobs", "HubSpot"),
    ("thetradedesk", "The Trade Desk"),
    ("doubleverify", "DoubleVerify"),
    ("integraladsscience", "IAS"),
    ("appsflyer", "AppsFlyer"),
    ("adjust", "Adjust"),
    ("branch", "Branch"),
    ("liveramp", "LiveRamp"),
    ("innovid", "Innovid"),
    ("outbrain", "Outbrain"),
    ("taboola", "Taboola"),
    ("applovin", "AppLovin"),
    ("criteo", "Criteo"),
    ("openx", "OpenX"),
    ("indexexchange", "Index Exchange"),
    ("sharethrough", "Sharethrough"),
    ("sovrn", "Sovrn"),
    ("pinterest", "Pinterest"),
    ("cognitiv", "Cognitiv"),
    ("quantcast", "Quantcast"),
    ("gumgum", "GumGum"),
    ("zetaglobal", "Zeta Global"),
    ("mediaocean", "Mediaocean"),
    ("klaviyo", "Klaviyo"),
    ("braze", "Braze"),
    ("iterable", "Iterable"),
    ("rockerbox", "Rockerbox"),
    ("inmobi", "InMobi"),
    ("instacart", "Instacart Ads"),
    ("zillow", "Zillow"),

    # ── New: AdTech / Media ──
    ("magnite", "Magnite"),
    ("madhive", "Madhive"),
    ("twitch", "Twitch"),
    ("siriusxm", "SiriusXM"),
    ("mediaalpha", "MediaAlpha"),
    ("raptive", "Raptive"),
    ("chartbeat", "Chartbeat"),
    ("liveperson", "LivePerson"),
    ("creatoriq", "CreatorIQ"),
    ("fandom", "Fandom"),
    ("crunchyroll", "Crunchyroll"),
    ("thenewyorktimes", "The New York Times"),

    # ── New: Big Tech / Enterprise SaaS ──
    ("dropbox", "Dropbox"),
    ("elastic", "Elastic"),
    ("cloudera", "Cloudera"),
    ("okta", "Okta"),
    ("twilio", "Twilio"),
    ("duckduckgo", "DuckDuckGo"),
    ("duolingo", "Duolingo"),
    ("honeycomb", "Honeycomb"),
    ("onetrust", "OneTrust"),
    ("drata", "Drata"),
    ("fossa", "FOSSA"),
    ("cricut", "Cricut"),
    ("riotgames", "Riot Games"),
    ("epicgames", "Epic Games"),
    ("scopely", "Scopely"),
    ("xperi", "Xperi"),
    ("attentive", "Attentive"),
    ("clickup", "ClickUp"),
    ("movableink", "Movable Ink"),
    ("pindrop", "Pindrop"),
    ("fullstory", "FullStory"),
    ("servicetitan", "ServiceTitan"),
    ("aura", "Aura"),
    ("crexi", "Crexi"),
    ("justanswer", "JustAnswer"),
    ("flock", "Flock"),
    ("onxmaps", "onXmaps"),

    # ── New: Atlanta / Fintech ──
    ("calendly", "Calendly"),
    ("fanduel", "FanDuel"),
    ("carvana", "Carvana"),
    ("greenlight", "Greenlight"),
    ("robinhood", "Robinhood"),
    ("affirm", "Affirm"),
    ("mercury", "Mercury"),
    ("gemini", "Gemini"),
    ("square", "Square"),
    ("moneylion", "MoneyLion"),
    ("acorns", "Acorns"),
    ("legalzoom", "LegalZoom"),
    ("billcom", "Bill.com"),
    ("altruistllc", "Altruist"),
    ("relaypayments", "Relay Payments"),
    ("wrgrouponline", "Zepz"),

    # ── New: Health / Consumer ──
    ("himsandhers", "Hims & Hers"),
    ("goodrx", "GoodRx"),
    ("cedar", "Cedar"),
    ("talkiatry", "Talkiatry"),
    ("tebra", "Tebra"),
    ("equip", "Equip Health"),
    ("simplepractice", "SimplePractice"),
    ("calm", "Calm"),
    ("olaplex", "Olaplex"),
    ("reformation", "Reformation"),
    ("houzz", "Houzz"),
    ("procoretechnologies", "Procore Technologies"),
    ("edmunds", "Edmunds"),
    ("faireinc", "Faire"),
    ("taskrabbit", "Taskrabbit"),
    ("tinder", "Tinder"),
    ("grindr", "Grindr"),
    ("bitly", "Bitly"),
    ("weedmaps", "Weedmaps"),
    ("ro", "Ro"),

    # ── Patch-in: companies missed in initial pass (10 missed entries) ──
    ("movableink", "Movable Ink"),    # was duplicated/mis-keyed earlier
    ("clover", "Clover"),             # Fiserv sub — Greenhouse most likely
    ("splash", "Splash Business Intelligence"),
]

LEVER_COMPANIES = [
    ("spotify", "Spotify"),
    ("atlassian", "Atlassian"),
]

# Format: (tenant, wd_num, site, display name, prefix)
WORKDAY_COMPANIES = [
    ("yahoo", 1, "Yahoo", "Yahoo", "yahoo"),
    ("directv", 1, "DIRECTVCareers", "DIRECTV", "directv"),
    ("nvidia", 1, "NVIDIAExternalCareerSite", "NVIDIA", "nvidia"),
    ("adobe", 5, "external_experienced", "Adobe", "adobe"),
    ("hp", 5, "ExternalCareerSite", "HP", "hp"),
    ("hpe", 5, "jobs", "Hewlett Packard Enterprise", "hpe"),
    ("qualcomm", 1, "External", "Qualcomm", "qualcomm"),
    ("broadcom", 1, "External_Career_Site", "Broadcom", "broadcom"),
    ("westerndigital", 1, "External", "Western Digital", "wd"),
    ("marvell", 1, "MarvellCareers2", "Marvell Technology", "marvell"),
    ("skyworks", 1, "External", "Skyworks Solutions", "skyworks"),
    ("autodesk", 1, "Ext", "Autodesk", "autodesk"),
    ("zoom", 1, "Zoom", "Zoom", "zoom"),
    ("logitech", 1, "External", "Logitech", "logitech"),
    ("ebay", 1, "eBay", "eBay", "ebay"),
    ("lululemon", 5, "lululemon", "lululemon", "lulu"),
    ("americanexpress", 1, "External", "American Express", "amex"),
    ("cox", 5, "CoxAutoCareerSite", "Cox Automotive", "coxauto"),
    ("cox", 5, "CoxEnterprises", "Cox Communications", "coxcomm"),
    ("delta", 5, "DeltaCareers", "Delta Air Lines", "delta"),
    ("homedepot", 5, "THDExternal", "The Home Depot", "homedepot"),
    ("equifax", 1, "ext", "Equifax", "equifax"),
    ("honeywell", 5, "1", "Honeywell", "honeywell"),

    # ── Patch-in: missed companies, Workday-likely (verify with company_status.py) ──
    ("adp", 5, "ADP", "ADP", "adp"),
    ("axon", 1, "Axon", "Axon", "axon"),
    ("costar", 5, "CoStarCareers", "CoStar Group", "costar"),
    ("neustar", 1, "External", "Neustar", "neustar"),
]


# ─── MAIN ─────────────────────────────────────────────────────────────────────
def run():
    print(f"\n{'='*50}")
    print(f"Jobify Scraper — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"Locations: {', '.join(SEARCH_LOCATIONS)}")
    print(f"Keywords:  {', '.join(SEARCH_QUERIES)}")
    print(f"{'='*50}\n")

    existing = load_existing()
    new_count = 0
    all_fresh = []

    # Dedicated fetchers
    all_fresh += safe_fetch(fetch_microsoft)
    all_fresh += safe_fetch(fetch_amazon)
    all_fresh += safe_fetch(fetch_netflix)

    # Greenhouse (bulk)
    for board, company in GREENHOUSE_COMPANIES:
        all_fresh += safe_fetch(fetch_greenhouse, board, company)
        sleep(0.2)

    # Lever (bulk)
    for slug, company in LEVER_COMPANIES:
        all_fresh += safe_fetch(fetch_lever, slug, company)
        sleep(0.2)

    # Workday (bulk via generic fetcher)
    for tenant, wd_num, site, company, prefix in WORKDAY_COMPANIES:
        all_fresh += safe_fetch(fetch_workday, tenant, wd_num, site, company, prefix)
        sleep(0.3)

    # Other dedicated fetchers
    all_fresh += safe_fetch(fetch_trade_desk)
    all_fresh += safe_fetch(fetch_salesforce)
    all_fresh += safe_fetch(fetch_servicenow)
    all_fresh += safe_fetch(fetch_snap)
    all_fresh += safe_fetch(fetch_capital_one)
    all_fresh += safe_fetch(fetch_mastercard)
    all_fresh += safe_fetch(fetch_visa)
    all_fresh += safe_fetch(fetch_walmart_connect)
    all_fresh += safe_fetch(fetch_deloitte)
    all_fresh += safe_fetch(fetch_intuit)
    all_fresh += safe_fetch(fetch_ycombinator)
    all_fresh += safe_fetch(fetch_weworkremotely)
    all_fresh += safe_fetch(fetch_dice)
    all_fresh += safe_fetch(fetch_builtin)
    all_fresh += safe_fetch(fetch_browse_links)

    # Dedup — aggressive: collapses scraper-overlap dupes, normalizes seniority
    # noise in titles, and merges entries with overlapping locations. See
    # job_dedup.py for the full ruleset.
    import sys as _sys, os as _os
    _here = _os.path.dirname(_os.path.abspath(__file__))
    for _p in (_here, _os.path.dirname(_here), _os.getcwd(), "scripts"):
        if _p and _p not in _sys.path:
            _sys.path.insert(0, _p)
    from job_dedup import dedup_job_list

    deduped, dupes_collapsed = dedup_job_list(all_fresh)
    print(f"\nDedup: {len(all_fresh)} scraped → {len(deduped)} unique ({dupes_collapsed} dupes collapsed)")

    # Merge into existing jobs.json, preserving scoring history
    for job in deduped:
        jid = job["id"]
        if jid not in existing:
            existing[jid] = job
            new_count += 1
        else:
            # Refresh mutable fields but DON'T clobber scoring/notes/status
            existing[jid]["title"] = job["title"]
            existing[jid]["url"] = job["url"]
            if job.get("description") and not existing[jid].get("description"):
                existing[jid]["description"] = job["description"]

    # Also run a dict-level pass to catch dupes that already snuck into jobs.json
    # (e.g. before this dedup was added, or from older scraper runs)
    try:
        from job_dedup import dedup_jobs_dict
        before = len(existing)
        existing, removed_ids, merges = dedup_jobs_dict(existing)
        if removed_ids:
            print(f"Dedup (existing jobs.json): {before} → {len(existing)} "
                  f"({len(removed_ids)} legacy dupes collapsed)")
    except Exception as e:
        print(f"  ⚠️  jobs.json dedup pass skipped: {e}")

    save_jobs(existing)

    print(f"\n{'='*50}")
    print(f"Done! {new_count} new jobs added.")
    print(f"Total jobs tracked: {len(existing)}")
    print(f"{'='*50}\n")


# ─── TODO: COMPANIES NEEDING ATS VERIFICATION ────────────────────────────────
# These companies appear on the target list but their ATS slug/tenant has NOT
# been verified. Run `python3 scripts/company_status.py` to probe candidate
# endpoints; once a working slug is confirmed, move the entry into the
# appropriate registry above (GREENHOUSE_COMPANIES, LEVER_COMPANIES, etc.)
#
# ── Patch-in TODO (missed in initial pass) ──
# GLS                  | unknown ATS — small logistics co
# Syntellis            | unknown ATS — recently acquired by Roper
# WABE                 | custom (wabe.org/careers) — Atlanta NPR/PBS
#
# Format: company name | suspected ATS | notes
#
# AdTech / Media:
#   Samsung Ads          | Workday (Samsung)    | verify tenant
#   Triton Digital       | iCIMS                | iHeartMedia parent — own portal
#   NBCUniversal         | Workday (nbcuni)     | verify tenant/site
#   Comcast              | Workday              | parent of NBCU
#   Disney / ESPN Tech   | custom (jobs.disneycareers.com) | needs dedicated fetcher
#   Warner Bros. Discovery | Workday (wbd)      | verify tenant
#   Paramount            | Workday              | verify tenant
#   Fox                  | Workday              | verify tenant
#   AEG Presents / AXS   | iCIMS?               | unknown
#   Ticketmaster         | Workday (livenation) | Live Nation parent
#   Tixr                 | unknown              | smaller co, probably custom
#   Klear                | unknown              | Meltwater sub
#   Cohley               | unknown
#   Fandango             | Workday (NBCU)       | NBCU sub
#   Starz                | Workday (Lionsgate)
#   XUMO                 | Workday (Comcast)
#
# Big Tech:
#   Cisco                | custom (jobs.cisco.com)
#   CrowdStrike          | custom (crowdstrike.com/careers)
#   Saviynt              | unknown
#   Kiteworks            | unknown
#   Domotz               | unknown
#   Wispr Flow           | unknown
#   Belkin               | Workday?
#   Newegg               | unknown
#   Panasonic            | Workday
#   Epson America        | Workday
#   Samsung              | custom (samsung.com/careers)
#   Sony PlayStation     | Workday (sonyglobal)
#   Electronic Arts      | Workday (ea)
#   Zynga                | Workday (take-two parent)
#   2K                   | Workday (take-two)
#   Blizzard Entertainment | Workday (microsoft) — post-acquisition
#   Alteryx              | Workday or iCIMS
#   Coupa Software       | Workday (coupa)
#   EPAM Systems         | custom
#   o9 Solutions         | Workday
#   OpenText             | Workday (opentext)
#   Omnissa              | Workday (spun out of VMware)
#   Publicis Sapient     | Workday
#   Aderant              | unknown
#   Movable Ink → moved to Greenhouse list (verify)
#
# Atlanta / Banking:
#   Truist Bank          | Workday (truist)
#   UPS                  | custom (ups.com/careers)
#   AT&T                 | custom
#   Verizon              | custom (mycareer.verizon.com)
#   Bank of America      | custom
#   Wells Fargo          | custom
#   JPMorgan Chase       | custom
#   Citi                 | custom (jobs.citi.com)
#   USAA                 | Workday (usaa)
#   BIP Wealth           | unknown
#   CarMax               | Workday (carmax)
#   FICO                 | Workday (fico)
#   FIS                  | Workday (fisglobal)
#   Fiserv               | Workday
#   Global Payments      | Workday (globalpayments)
#   Acuity Brands        | Workday (acuitybrands)
#   LexisNexis Risk Solutions | Workday (relx)
#   ARRIS                | CommScope sub — Workday
#   Macy's               | Workday (macys)
#   Marriott             | Workday (marriott)
#   Dollar General       | Workday
#   Target               | Workday (target)
#   Kroger Technology    | Workday (kroger)
#
# Fintech:
#   PayPal               | Workday (paypal)
#   Rocket Companies     | Workday (rocketcompanies)
#   Aletheia             | unknown
#   Alogent              | unknown
#   Americor             | unknown
#   Invesco              | Workday
#   Kemper               | Workday
#   First American Trust | Workday
#   GoodLeap             | Greenhouse?
#   Guaranteed Rate      | Workday
#   Instant Financial    | unknown
#   Payroc               | unknown
#   Purchasing Power     | unknown
#   Q2                   | Greenhouse?
#
# Health:
#   Quest Diagnostics    | Workday
#   Kaiser Permanente    | custom (jobs.kaiserpermanente.org)
#   Medtronic            | Workday (medtronic)
#   Thermo Fisher        | Workday (thermofisher)
#   Siemens Healthineers | Workday (siemens)
#   J&J                  | Workday (jnj)
#   Optum / UnitedHealth | custom
#   Mahmee, Mediflix, UpToDate, Zynx, FitOn, Care.com, Wider Circle, Philips, Farmers Insurance | various
#
# Retail / Consumer:
#   Best Buy             | Workday (bestbuy)
#   Glassdoor            | (owned by Indeed)
#   Nike                 | Workday (nike) — needs verification
#   Red Bull             | SmartRecruiters
#   Mint Mobile          | unknown
#   Aaron's, Zoro US, Saki Products, DRINKS, Fullsend, Hapn, IDIQ, IOGEAR | unknown
#   Internet Brands      | Workday
#   Spokeo               | unknown
#   Fearless Records, 9 Count | unknown
#
# Enterprise / Misc:
#   NICE                 | Workday (nice)
#   Nokia                | Workday
#   Nordson              | Workday
#   ProSearch            | unknown
#   Routeware, RRD       | unknown
#   Siemens              | Workday (siemens)
#   Sierra Wireless      | Workday
#   Xero                 | Workday (xero)
#   YouVersion           | unknown
#   Your App Hero        | unknown
#   UJET.cx              | Greenhouse?
#   Vayner Media         | Greenhouse?
#   Telescope, Network Optix, Nimble, Raiven | unknown
#   McGraw Hill          | Workday
#   Elevate K-12, Conservice, PeopleReady, Cyncly, Mitsubishi Electric Trane,
#   Neptune Technology Group, JBT Marel, LA Clippers, EY, Blitz.gg, Fortna,
#   General Motors, T-Mobile, TK Elevator, The Weather Channel, Roo,
#   TechStyleOS, GLS, Aaron's, BlueLabel, BestReviews, DaySmart, Digital Element,
#   Dover Food Retail, EverConnect, Experian, Follett Software, GlobalLogic,
#   GSMA, Iconfactory, Ipserlabs, IHG Hotels & Resorts | various — verify
# ─────────────────────────────────────────────────────────────────────────────


if __name__ == "__main__":
    run()
