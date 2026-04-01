import requests
import json
import os
from datetime import datetime
from time import sleep

JOBS_FILE = "jobs.json"

INCLUDE_KEYWORDS = [
    "product manager", "data", "advertising", "analytics",
    "program manager", "product lead", "product owner",
    "growth", "monetization", "ads", "revenue"
]

EXCLUDE_TITLES = [
    "engineer", "account manager", "account executive",
    "software", "developer", "devops", "infrastructure",
    "sales", "recruiter", "designer", "scientist",
    "attorney", "lawyer", "finance", "accounting",
    "hr ", "human resources", "coordinator", "assistant",
    "technician", "operator", "specialist", "science", "accountant"
]

LOCATION_KEYWORDS = ["atlanta", "remote", "multiple locations", "united states"]


def load_existing():
    if os.path.exists(JOBS_FILE):
        with open(JOBS_FILE) as f:
            return json.load(f)
    return {}


def save_jobs(jobs):
    with open(JOBS_FILE, "w") as f:
        json.dump(jobs, f, indent=2)


def is_relevant_title(title):
    title_l = title.lower()
    if any(ex in title_l for ex in EXCLUDE_TITLES):
        return False
    return any(kw in title_l for kw in INCLUDE_KEYWORDS)


def is_relevant_location(location):
    loc_l = location.lower()
    return any(l in loc_l for l in LOCATION_KEYWORDS)


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


# ─── MICROSOFT ────────────────────────────────────────────────────────────────
def fetch_microsoft():
    print("Fetching Microsoft...")
    results = []
    for kw in ["product manager", "data", "advertising", "analytics"]:
        try:
            url = f"https://apply.careers.microsoft.com/api/pcsx/search?domain=microsoft.com&query={requests.utils.quote(kw)}&start=0"
            r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
            jobs = r.json().get("data", {}).get("positions", [])
            for j in jobs:
                locs = ", ".join(j.get("locations", []))
                work_opt = j.get("workLocationOption", "")
                full_loc = f"{locs} {work_opt}"
                title = j.get("name", "")
                if not is_relevant_title(title):
                    continue
                if not is_relevant_location(full_loc):
                    continue
                results.append(make_job(
                    id=f"msft_{j['id']}",
                    company="Microsoft",
                    title=title,
                    location=locs,
                    url="https://apply.careers.microsoft.com" + j.get("positionUrl", ""),
                    posted_ts=j.get("postedTs", 0)
                ))
        except Exception as e:
            print(f"  Microsoft error: {e}")
    print(f"  Found {len(results)} Microsoft jobs")
    return results


# ─── AMAZON ───────────────────────────────────────────────────────────────────
def fetch_amazon():
    print("Fetching Amazon...")
    results = []
    searches = [
        ("product manager", "atlanta"),
        ("product manager", "remote"),
        ("data", "atlanta"),
        ("advertising", "remote"),
        ("analytics", "remote"),
    ]
    for kw, loc in searches:
        try:
            url = f"https://amazon.jobs/en/search.json?base_query={requests.utils.quote(kw)}&loc_query={loc}&job_count=20&result_limit=20&sort=recent"
            r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            jobs = r.json().get("jobs", [])
            for j in jobs:
                title = j.get("title", "")
                location = j.get("location", "")
                if not is_relevant_title(title):
                    continue
                if not is_relevant_location(location + " " + loc):
                    continue
                results.append(make_job(
                    id=f"amzn_{j.get('job_id', '')}",
                    company="Amazon",
                    title=title,
                    location=location,
                    url="https://amazon.jobs" + j.get("job_path", "")
                ))
        except Exception as e:
            print(f"  Amazon error: {e}")
        sleep(0.5)
    print(f"  Found {len(results)} Amazon jobs")
    return results


# ─── THE TRADE DESK ───────────────────────────────────────────────────────────
def fetch_trade_desk():
    print("Fetching The Trade Desk...")
    results = []
    try:
        url = "https://careers.thetradedesk.com/us/en/search-results?keywords=product%20manager"
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        # Trade Desk uses Phenom People ATS - scrape job listings from API
        api_url = "https://careers.thetradedesk.com/api/apply/v2/jobs?domain=thetradedesk.com&start=0&num=50&keyword=product+manager"
        r2 = requests.get(api_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        jobs = r2.json().get("positions", [])
        for j in jobs:
            title = j.get("name", "")
            locs = ", ".join(j.get("locations", []))
            if not is_relevant_title(title):
                continue
            results.append(make_job(
                id=f"ttd_{j.get('id', '')}",
                company="The Trade Desk",
                title=title,
                location=locs,
                url="https://careers.thetradedesk.com/us/en/job/" + str(j.get("id", ""))
            ))
    except Exception as e:
        print(f"  Trade Desk error: {e}")
    print(f"  Found {len(results)} Trade Desk jobs")
    return results


# ─── REDDIT ───────────────────────────────────────────────────────────────────
def fetch_reddit():
    print("Fetching Reddit...")
    results = []
    try:
        api_url = "https://boards-api.greenhouse.io/v1/boards/reddit/jobs?content=true"
        r = requests.get(api_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        jobs = r.json().get("jobs", [])
        for j in jobs:
            title = j.get("title", "")
            location = j.get("location", {}).get("name", "")
            if not is_relevant_title(title):
                continue
            if not is_relevant_location(location + " remote"):
                continue
            results.append(make_job(
                id=f"reddit_{j.get('id', '')}",
                company="Reddit",
                title=title,
                location=location,
                url=j.get("absolute_url", ""),
                description=j.get("content", "")[:500]
            ))
    except Exception as e:
        print(f"  Reddit error: {e}")
    print(f"  Found {len(results)} Reddit jobs")
    return results


# ─── ROKU ─────────────────────────────────────────────────────────────────────
def fetch_roku():
    print("Fetching Roku...")
    results = []
    try:
        api_url = "https://boards-api.greenhouse.io/v1/boards/roku/jobs?content=true"
        r = requests.get(api_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        jobs = r.json().get("jobs", [])
        for j in jobs:
            title = j.get("title", "")
            location = j.get("location", {}).get("name", "")
            if not is_relevant_title(title):
                continue
            if not is_relevant_location(location + " remote"):
                continue
            results.append(make_job(
                id=f"roku_{j.get('id', '')}",
                company="Roku",
                title=title,
                location=location,
                url=j.get("absolute_url", ""),
                description=j.get("content", "")[:500]
            ))
    except Exception as e:
        print(f"  Roku error: {e}")
    print(f"  Found {len(results)} Roku jobs")
    return results


# ─── UNITY ────────────────────────────────────────────────────────────────────
def fetch_unity():
    print("Fetching Unity...")
    results = []
    try:
        api_url = "https://boards-api.greenhouse.io/v1/boards/unity3d/jobs?content=true"
        r = requests.get(api_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        jobs = r.json().get("jobs", [])
        for j in jobs:
            title = j.get("title", "")
            location = j.get("location", {}).get("name", "")
            if not is_relevant_title(title):
                continue
            if not is_relevant_location(location + " remote"):
                continue
            results.append(make_job(
                id=f"unity_{j.get('id', '')}",
                company="Unity",
                title=title,
                location=location,
                url=j.get("absolute_url", ""),
                description=j.get("content", "")[:500]
            ))
    except Exception as e:
        print(f"  Unity error: {e}")
    print(f"  Found {len(results)} Unity jobs")
    return results


# ─── NETFLIX ──────────────────────────────────────────────────────────────────
def fetch_netflix():
    print("Fetching Netflix...")
    results = []
    try:
        api_url = "https://jobs.netflix.com/api/search?q=product+manager&location=remote&limit=50"
        r = requests.get(api_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        jobs = r.json().get("records", {}).get("postings", [])
        for j in jobs:
            title = j.get("text", "")
            location = ", ".join(j.get("tags", {}).get("location", []))
            if not is_relevant_title(title):
                continue
            results.append(make_job(
                id=f"netflix_{j.get('id', '')}",
                company="Netflix",
                title=title,
                location=location,
                url="https://jobs.netflix.com/jobs/" + j.get("id", "")
            ))
    except Exception as e:
        print(f"  Netflix error: {e}")
    print(f"  Found {len(results)} Netflix jobs")
    return results


# ─── FOX / TUBI ───────────────────────────────────────────────────────────────
def fetch_fox():
    print("Fetching Fox/Tubi...")
    results = []
    for board, company in [("fox", "Fox"), ("tubi", "Tubi")]:
        try:
            api_url = f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"
            r = requests.get(api_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            jobs = r.json().get("jobs", [])
            for j in jobs:
                title = j.get("title", "")
                location = j.get("location", {}).get("name", "")
                if not is_relevant_title(title):
                    continue
                if not is_relevant_location(location + " remote"):
                    continue
                results.append(make_job(
                    id=f"{board}_{j.get('id', '')}",
                    company=company,
                    title=title,
                    location=location,
                    url=j.get("absolute_url", ""),
                    description=j.get("content", "")[:500]
                ))
        except Exception as e:
            print(f"  {company} error: {e}")
    print(f"  Found {len(results)} Fox/Tubi jobs")
    return results


# ─── WELLFOUND (AngelList) ────────────────────────────────────────────────────
def fetch_wellfound():
    print("Fetching Wellfound...")
    results = []
    try:
        url = "https://wellfound.com/role/l/product-manager"
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        # Wellfound blocks scraping — use their job board API if available
        # Fallback: add known URL as a placeholder entry to check manually
        results.append(make_job(
            id="wellfound_browse",
            company="Wellfound",
            title="Browse PM Jobs on Wellfound",
            location="Remote / Atlanta",
            url="https://wellfound.com/role/l/product-manager/atlanta",
        ))
    except Exception as e:
        print(f"  Wellfound error: {e}")
    print(f"  Found {len(results)} Wellfound jobs")
    return results


# ─── Y COMBINATOR WORK AT A STARTUP ──────────────────────────────────────────
def fetch_ycombinator():
    print("Fetching Y Combinator...")
    results = []
    try:
        api_url = "https://www.workatastartup.com/jobs.json?demographic=any&hasEquity=any&hasSalary=any&industry=any&interviewProcess=any&jobType=any&layout=list-compact&query=product+manager&remote=yes&role=pm&sortBy=default&tab=any&usVisaNotRequired=any"
        r = requests.get(api_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
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
                    company=f"YC: {company_name}",
                    title=title,
                    location=location,
                    url=j.get("url", "https://www.workatastartup.com/jobs")
                ))
    except Exception as e:
        print(f"  YC error: {e}")
    print(f"  Found {len(results)} YC jobs")
    return results


# ─── WE WORK REMOTELY ─────────────────────────────────────────────────────────
def fetch_weworkremotely():
    print("Fetching We Work Remotely...")
    results = []
    try:
        import xml.etree.ElementTree as ET
        url = "https://weworkremotely.com/categories/remote-management-and-finance-jobs.rss"
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        root = ET.fromstring(r.content)
        for item in root.findall(".//item"):
            title = item.findtext("title", "")
            link = item.findtext("link", "")
            if not is_relevant_title(title):
                continue
            results.append(make_job(
                id=f"wwr_{hash(link)}",
                company="We Work Remotely",
                title=title,
                location="Remote",
                url=link
            ))
    except Exception as e:
        print(f"  WWR error: {e}")
    print(f"  Found {len(results)} WWR jobs")
    return results


# ─── INDEED ───────────────────────────────────────────────────────────────────
def fetch_indeed():
    print("Fetching Indeed (direct link)...")
    results = []
    # Indeed blocks API access — add browse links for manual reference
    for query, loc in [("product+manager", "Atlanta%2C+GA"), ("product+manager+remote", "")]:
        url = f"https://www.indeed.com/jobs?q={query}&l={loc}&sort=date"
        results.append(make_job(
            id=f"indeed_{query}_{loc}",
            company="Indeed",
            title=f"Browse: {query.replace('+', ' ').title()} on Indeed",
            location="Atlanta / Remote",
            url=url
        ))
    print(f"  Added {len(results)} Indeed browse links")
    return results


# ─── DICE ─────────────────────────────────────────────────────────────────────
def fetch_dice():
    print("Fetching Dice...")
    results = []
    try:
        api_url = "https://job-search-api.svc.dhigroupinc.com/v1/dice/jobs/search?q=product+manager&location=remote&country=US&radius=30&radiusUnit=mi&page=1&pageSize=20&filters.postedDate=ONE&language=en"
        r = requests.get(api_url, headers={"User-Agent": "Mozilla/5.0", "x-api-key": "1YAt0R9wBg4WfsF9VB2778F5CHLAPMVW3WAZcKd8"}, timeout=10)
        jobs = r.json().get("data", [])
        for j in jobs:
            title = j.get("title", "")
            location = j.get("location", "")
            if not is_relevant_title(title):
                continue
            results.append(make_job(
                id=f"dice_{j.get('id', '')}",
                company=j.get("advertiserName") or "Dice",
                title=title,
                location=location,
                url=j.get("applyDataItems", [{}])[0].get("applyUrl", "https://dice.com")
            ))
    except Exception as e:
        print(f"  Dice error: {e}")
    print(f"  Found {len(results)} Dice jobs")
    return results


# ─── GLASSDOOR ────────────────────────────────────────────────────────────────
def fetch_glassdoor():
    print("Fetching Glassdoor (browse links)...")
    results = []
    results.append(make_job(
        id="glassdoor_pm_atlanta",
        company="Glassdoor",
        title="Browse: Product Manager Jobs in Atlanta on Glassdoor",
        location="Atlanta, GA",
        url="https://www.glassdoor.com/Job/atlanta-product-manager-jobs-SRCH_IL.0,7_IC1155583_KO8,23.htm"
    ))
    results.append(make_job(
        id="glassdoor_pm_remote",
        company="Glassdoor",
        title="Browse: Remote Product Manager Jobs on Glassdoor",
        location="Remote",
        url="https://www.glassdoor.com/Job/remote-product-manager-jobs-SRCH_IL.0,6_IS11047_KO7,23.htm"
    ))
    print(f"  Added {len(results)} Glassdoor browse links")
    return results


# ─── BUILTIN ──────────────────────────────────────────────────────────────────
def fetch_builtin():
    print("Fetching Built In...")
    results = []
    try:
        api_url = "https://api.builtin.com/api/jobs?title=product+manager&remote=true&page=1&perPage=20"
        r = requests.get(api_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        jobs = r.json().get("jobs", [])
        for j in jobs:
            title = j.get("title", "")
            location = j.get("builtInJobLocation", {}).get("name", "Remote")
            if not is_relevant_title(title):
                continue
            results.append(make_job(
                id=f"builtin_{j.get('id', '')}",
                company=j.get("company", {}).get("name", "Built In"),
                title=title,
                location=location,
                url="https://builtin.com/job/" + str(j.get("slug", ""))
            ))
    except Exception as e:
        print(f"  Built In error: {e}")
    print(f"  Found {len(results)} Built In jobs")
    return results


# ─── UNDERDOG.IO ──────────────────────────────────────────────────────────────
def fetch_underdog():
    print("Fetching Underdog.io (browse link)...")
    results = [make_job(
        id="underdog_pm",
        company="Underdog.io",
        title="Browse: Product Manager Jobs on Underdog.io",
        location="Remote",
        url="https://underdog.io/jobs?role=product-manager"
    )]
    print(f"  Added 1 Underdog.io browse link")
    return results


# ─── TRUE UP ──────────────────────────────────────────────────────────────────
def fetch_trueup():
    print("Fetching True Up (browse link)...")
    results = [make_job(
        id="trueup_pm",
        company="True Up",
        title="Browse: Product Manager Jobs on True Up",
        location="Remote / Atlanta",
        url="https://www.trueup.io/jobs?query=product+manager&locationId=&remoteType=remote"
    )]
    print(f"  Added 1 True Up browse link")
    return results


# ─── TECH FETCH ───────────────────────────────────────────────────────────────
def fetch_techfetch():
    print("Fetching Tech Fetch (browse link)...")
    results = [make_job(
        id="techfetch_pm",
        company="Tech Fetch",
        title="Browse: Product Manager Jobs on Tech Fetch",
        location="Remote / Atlanta",
        url="https://www.techfetch.com/job/search?jobTitle=product+manager&location=remote"
    )]
    print(f"  Added 1 Tech Fetch browse link")
    return results


# ─── PRODUCT COMMUNITY JOB BOARDS ────────────────────────────────────────────
def fetch_product_boards():
    print("Fetching product community job boards...")
    results = [
        make_job("pmhq_browse", "Product Manager HQ", "Browse: PM Jobs on ProductHired / PMHQ", "Remote", "https://productmanagerhq.com/jobs/"),
        make_job("mindtheproduct_browse", "Mind the Product", "Browse: PM Jobs on Mind the Product", "Remote", "https://jobs.mindtheproduct.com/"),
        make_job("productfolks_browse", "The Product Folks", "Browse: PM Jobs on The Product Folks", "Remote", "https://www.theproductfolks.com/jobs"),
        make_job("productjobs_browse", "ProductJobs.com", "Browse: PM Jobs on ProductJobs.com", "Remote", "https://productjobs.com/"),
    ]
    print(f"  Added {len(results)} product board browse links")
    return results


# ─── MAIN ─────────────────────────────────────────────────────────────────────
def run():
    print(f"\n{'='*50}")
    print(f"Jobify Scraper — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*50}\n")

    existing = load_existing()
    new_count = 0

    fetchers = [
        fetch_microsoft,
        fetch_amazon,
        fetch_trade_desk,
        fetch_reddit,
        fetch_roku,
        fetch_unity,
        fetch_netflix,
        fetch_fox,
        fetch_wellfound,
        fetch_ycombinator,
        fetch_weworkremotely,
        fetch_indeed,
        fetch_dice,
        fetch_glassdoor,
        fetch_builtin,
        fetch_underdog,
        fetch_trueup,
        fetch_techfetch,
        fetch_product_boards,
    ]

    all_fresh = []
    for fetcher in fetchers:
        try:
            all_fresh += fetcher()
        except Exception as e:
            print(f"  Error in {fetcher.__name__}: {e}")
        sleep(0.3)

    seen = set()
    for job in all_fresh:
        jid = job["id"]
        if jid in seen:
            continue
        seen.add(jid)
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
