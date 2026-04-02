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
    """Defensive wrapper — ensures a broken scraper never crashes the full pipeline."""
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
# No location filter — Netflix is mostly remote-friendly and their API
# doesn't filter reliably by location. Title filter handles relevance.
def fetch_netflix():
    print("Fetching Netflix...")
    results = []
    seen = set()
    for kw in SEARCH_QUERIES:
        try:
            url = (
                f"https://explore.jobs.netflix.net/api/apply/v2/jobs"
                f"?domain=netflix.com&start=0&num=50"
                f"&keyword={requests.utils.quote(kw)}"
            )
            r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            print(f"  Netflix status: {r.status_code} | {kw}")
            if r.status_code != 200:
                continue
            data = r.json()
            positions = data.get("positions", [])
            for j in positions:
                jid = str(j.get("id", ""))
                if jid in seen:
                    continue
                seen.add(jid)
                title = j.get("name", "")
                locs = ", ".join(j.get("locations", []))
                if not is_relevant_title(title):
                    continue
                # Accept any US-based or remote Netflix job — dashboard handles
                # location filtering. Netflix locations are often "Los Gatos, CA"
                # or "United States" which don't match the narrow LOCATION_KEYWORDS.
                us_indicators = [
                    "united states", "remote", "los angeles", "atlanta",
                    "california", "new york", "new york city", "seattle",
                    "los gatos", "beverly hills"
                ]
                if locs and not any(loc in locs.lower() for loc in us_indicators):
                    continue
                results.append(make_job(
                    id=f"netflix_{jid}",
                    company="Netflix",
                    title=title,
                    location=locs,
                    url="https://explore.jobs.netflix.net/careers?pid=" + jid + "&domain=netflix.com"
                ))
        except Exception as e:
            print(f"  Netflix error ({kw}): {e}")
    print(f"  Found {len(results)} Netflix jobs")
    return results


# ─── GREENHOUSE BOARDS ────────────────────────────────────────────────────────
def fetch_greenhouse(board, company):
    print(f"Fetching {company}...")
    results = []
    try:
        url = f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
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


# ─── THE TRADE DESK ───────────────────────────────────────────────────────────
def fetch_trade_desk():
    print("Fetching The Trade Desk...")
    results = []
    for kw in SEARCH_QUERIES:
        try:
            url = f"https://careers.thetradedesk.com/api/apply/v2/jobs?domain=thetradedesk.com&start=0&num=50&keyword={requests.utils.quote(kw)}"
            r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            for j in r.json().get("positions", []):
                title = j.get("name", "")
                locs = ", ".join(j.get("locations", []))
                if not passes(title, locs, "remote"):
                    continue
                results.append(make_job(
                    id=f"ttd_{j.get('id', '')}",
                    company="The Trade Desk", title=title, location=locs,
                    url="https://careers.thetradedesk.com/us/en/job/" + str(j.get("id", ""))
                ))
        except Exception as e:
            print(f"  Trade Desk error ({kw}): {e}")
    print(f"  Found {len(results)} Trade Desk jobs")
    return results


# ─── SALESFORCE (Workday) ─────────────────────────────────────────────────────
# Salesforce uses Workday, not Greenhouse — the old "salesforce" Greenhouse
# board slug was silently returning 0 jobs every run.
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
            payload = {
                "appliedFacets": {},
                "limit": 20,
                "offset": 0,
                "searchText": kw
            }
            r = requests.post(
                url,
                json=payload,
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Content-Type": "application/json",
                    "Accept": "application/json"
                },
                timeout=15
            )
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
                    company="Salesforce",
                    title=title,
                    location=location,
                    url="https://salesforce.wd12.myworkdayjobs.com/en-US/External_Career_Site" + ext_path
                ))
        except Exception as e:
            print(f"  Salesforce error ({kw}): {e}")
    print(f"  Found {len(results)} Salesforce jobs")
    return results


# ─── SERVICENOW (SmartRecruiters) ────────────────────────────────────────────
# ServiceNow uses SmartRecruiters, not Greenhouse — the old "servicenow"
# Greenhouse board slug was silently returning 0 jobs every run.
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
            r = requests.get(
                url,
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=15
            )
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
                    company="ServiceNow",
                    title=title,
                    location=location,
                    url=f"https://careers.smartrecruiters.com/ServiceNow/{jid}"
                ))
        except Exception as e:
            print(f"  ServiceNow error ({kw}): {e}")
    print(f"  Found {len(results)} ServiceNow jobs")
    return results


# ─── Y COMBINATOR ─────────────────────────────────────────────────────────────
def fetch_ycombinator():
    print("Fetching Y Combinator...")
    results = []
    for kw in SEARCH_QUERIES:
        try:
            url = f"https://www.workatastartup.com/jobs.json?query={requests.utils.quote(kw)}&remote=yes&role=pm&sortBy=default"
            r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            jobs = r.json()
            if isinstance(jobs, list):
                for j in jobs:
                    title = j.get("title", "")
                    location = j.get("locations", ["Remote"])[0] if j.get("locations") else "Remote"
                    company_name = j.get("company", {}).get("name", "YC Startup")
                    if not is_relevant_title(title):
                        continue
                    results.append(make_job(
                        id=f"yc_{j.get('id', '')}",
                        company=f"YC: {company_name}", title=title, location=location,
                        url=j.get("url", "https://www.workatastartup.com/jobs")
                    ))
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
    for kw in SEARCH_QUERIES:
        try:
            url = f"https://api.builtin.com/api/jobs?title={requests.utils.quote(kw)}&remote=true&page=1&perPage=20"
            r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            for j in r.json().get("jobs", []):
                title = j.get("title", "")
                location = j.get("builtInJobLocation", {}).get("name", "Remote")
                if not passes(title, location, "remote"):
                    continue
                results.append(make_job(
                    id=f"builtin_{j.get('id', '')}",
                    company=j.get("company", {}).get("name", "Built In"),
                    title=title, location=location,
                    url="https://builtin.com/job/" + str(j.get("slug", ""))
                ))
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
    all_fresh += safe_fetch(fetch_microsoft)
    all_fresh += safe_fetch(fetch_amazon)
    all_fresh += safe_fetch(fetch_netflix)

    greenhouse_companies = [
        ("reddit", "Reddit"),
        ("roku", "Roku"),
        ("unity3d", "Unity"),
        ("fox", "Fox"),
        ("tubi", "Tubi"),
        ("hubspot", "HubSpot"),
        ("thetradedesk", "The Trade Desk"),
        ("doubleverify", "DoubleVerify"),
        ("integraladsscience", "IAS"),
        ("nielsen", "Nielsen"),
        ("appsflyer", "AppsFlyer"),
        ("adjust", "Adjust"),
        ("branch", "Branch"),
        ("kochava", "Kochava"),
        ("liveramp", "LiveRamp"),
        ("lotame", "Lotame"),
        ("neustar", "Neustar"),
        ("innovid", "Innovid"),
        ("outbrain", "Outbrain"),
        ("taboola", "Taboola"),
        ("nativo", "Nativo"),
        ("applovin", "AppLovin"),
        ("criteo", "Criteo"),
        ("mediamath", "MediaMath"),
        ("openx", "OpenX"),
        ("indexexchange", "Index Exchange"),
        ("sharethrough", "Sharethrough"),
        ("sovrn", "Sovrn"),
        ("triton", "Triton Digital"),
        ("samsung", "Samsung Ads"),
        ("pinterest", "Pinterest"),
        ("snapchat", "Snap"),
        ("cognitiv", "Cognitiv"),
        ("quantcast", "Quantcast"),
        ("gumgum", "GumGum"),
        ("zetaglobal", "Zeta Global"),
        ("mediaocean", "Mediaocean"),
        ("klaviyo", "Klaviyo"),
        ("braze", "Braze"),
        ("iterable", "Iterable"),
        ("rockerbox", "Rockerbox"),
        ("amobee", "Amobee"),
        ("inmobi", "InMobi"),
        ("adcolony", "AdColony"),
        ("conversant", "Conversant"),
        ("walmart", "Walmart Connect"),
        ("instacart", "Instacart Ads"),
        ("roundel", "Target Roundel"),
        ("capitalone", "Capital One"),
        ("mastercard", "Mastercard"),
        ("deloitte", "Deloitte"),
        ("visa", "Visa"),
        ("adp", "ADP"),
        # Removed: salesforce, servicenow — wrong ATS, dedicated fetchers below
        ("cocacola", "Coca Cola"),
        ("zillow", "Zillow"),
        ("warnerbros", "Warner Brothers"),
        ("intuit", "Intuit"),
        ("cox", "Cox"),
    ]
    for board, company in greenhouse_companies:
        all_fresh += safe_fetch(fetch_greenhouse, board, company)
        sleep(0.2)

    all_fresh += safe_fetch(fetch_trade_desk)
    all_fresh += safe_fetch(fetch_salesforce)
    all_fresh += safe_fetch(fetch_servicenow)
    all_fresh += safe_fetch(fetch_ycombinator)
    all_fresh += safe_fetch(fetch_weworkremotely)
    all_fresh += safe_fetch(fetch_dice)
    all_fresh += safe_fetch(fetch_builtin)
    all_fresh += safe_fetch(fetch_browse_links)

    seen_ids = set()
    seen_titles = set()
    for job in all_fresh:
        jid = job["id"]
        if jid in seen_ids:
            continue
        seen_ids.add(jid)
        title_key = f"{job.get('company','').lower()}::{job.get('title','').lower().strip()}"
        if title_key in seen_titles:
            continue
        seen_titles.add(title_key)
        if jid not in existing:
            existing[jid] = job
            new_count += 1
        else:
            existing[jid]["title"] = job["title"]
            existing[jid]["url"] = job["url"]

    save_jobs(existing)

    print(f"\n{'='*50}")
    print(f"Done! {new_count} new jobs added.")
    print(f"Total jobs tracked: {len(existing)}")
    print(f"{'='*50}\n")


if __name__ == "__main__":
    run()
