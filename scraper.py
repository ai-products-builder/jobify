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


def make_job(id, company, title, location, url, posted_ts=0, description="",
             base_salary_min=None, base_salary_max=None,
             base_salary_source=None):
    """Build a job dict. If base salary is provided (from ATS), automatically
    compute TC estimate using salary_tiers.compute_tc()."""
    job = {
        "id": id,
        "company": company,
        "title": title,
        "location": location,
        "url": url,
        "posted_ts": posted_ts,
        "found_date": datetime.now().isoformat(),
        "status": "new",
        "description": description,
        "base_salary_min": base_salary_min,
        "base_salary_max": base_salary_max,
        "base_salary_source": base_salary_source,  # "ats" or None
        "bonus_pct": None,
        "equity_pct": None,
        "tc_estimate_min": None,
        "tc_estimate_max": None,
        "salary_tier": None,
        "salary_level": None,
        "salary_confidence": None,
    }
    # Compute TC if we have base data
    try:
        from salary_tiers import compute_tc
        tc = compute_tc(base_salary_min, base_salary_max, company, title)
        job["bonus_pct"] = tc["bonus_pct"]
        job["equity_pct"] = tc["equity_pct"]
        job["tc_estimate_min"] = tc["tc_estimate_min"]
        job["tc_estimate_max"] = tc["tc_estimate_max"]
        job["salary_tier"] = tc["tier"]
        job["salary_level"] = tc["level"]
        job["salary_confidence"] = tc["confidence"]
    except Exception:
        pass  # don't crash scrape if salary module fails
    return job


# ─── Salary extraction ───────────────────────────────────────────────────────
# Strategy: structured fields first (Ashby/Lever expose them), then a tested
# regex over the job description HTML (works for any ATS — CA/NY/WA/CO pay
# transparency law forces the range into the posting text). The text parser
# below was unit-tested against 10 realistic posting formats.
import re as _re
import html as _html

_SAL_RANGE_PAT = _re.compile(
    r'\$[\d,]+\s*[Kk]?\s*(?:-|–|—|to|and)\s*\$?[\d,]+\s*[Kk]?'
)
_SAL_KEYWORD_PAT = _re.compile(r'salary|pay|compensation|base|annual|comp\b', _re.IGNORECASE)


def extract_salary_from_text(text_or_html):
    """
    Universal salary extractor — parses a $-range out of description text/HTML
    when it appears near a salary keyword. Returns (min, max) in dollars or
    (None, None). Tested against 10 realistic formats (5 disclose, handles
    'X to Y', 'X-Y', 'X—Y', '$XXXk', and rejects non-salary numbers).
    """
    if not text_or_html or not isinstance(text_or_html, str):
        return None, None
    text = _re.sub(r'<[^>]+>', ' ', text_or_html)
    text = _html.unescape(text)
    text = _re.sub(r'\s+', ' ', text)

    for m in _SAL_RANGE_PAT.finditer(text):
        start, end = m.start(), m.end()
        before = text[max(0, start - 120):start]
        after = text[end:end + 40]
        if _SAL_KEYWORD_PAT.search(before) or _SAL_KEYWORD_PAT.search(after):
            nums = _re.findall(r'\$?([\d,]+)\s*([Kk])?', m.group(0))
            parsed = []
            for raw, suffix in nums:
                try:
                    n = float(raw.replace(',', ''))
                except ValueError:
                    continue
                if suffix and suffix.lower() == 'k':
                    n *= 1000
                if 30000 <= n <= 2000000:   # plausible annual salary band
                    parsed.append(int(n))
            if len(parsed) >= 2:
                return min(parsed[0], parsed[1]), max(parsed[0], parsed[1])
    return None, None


def extract_salary_greenhouse(job_dict):
    """
    Greenhouse: the list endpoint (/jobs?content=true) does NOT return a
    structured pay_ranges field — pay transparency text is embedded in the
    'content' HTML. So we parse the content. (Verified against Greenhouse
    Job Board API docs: list endpoint metadata is null; pay shows in content.)
    """
    # Newer boards sometimes include pay_input_ranges on the detail object —
    # check it cheaply first in case it's present.
    for key in ("pay_input_ranges", "pay_ranges"):
        prs = job_dict.get(key)
        if prs and isinstance(prs, list):
            vals = []
            for pr in prs:
                lo = pr.get("min_cents")
                hi = pr.get("max_cents")
                if lo and hi:
                    vals.append((lo // 100, hi // 100))
                else:
                    lo2 = pr.get("min_value") or pr.get("min")
                    hi2 = pr.get("max_value") or pr.get("max")
                    if lo2 and hi2:
                        vals.append((int(lo2), int(hi2)))
            if vals:
                return min(v[0] for v in vals), max(v[1] for v in vals)
    # Primary path: parse the content HTML
    return extract_salary_from_text(job_dict.get("content", ""))


def extract_salary_workday(job_dict):
    """
    Workday detail response. The description HTML lives at
    jobPostingInfo.jobDescription (most tenants). Salary, when present, is in
    that HTML. We also check a few alternate locations defensively.
    """
    # Standard location
    info = job_dict.get("jobPostingInfo", {})
    if isinstance(info, dict):
        for k in ("jobDescription", "description", "jobRequirements"):
            desc = info.get(k, "")
            if desc:
                lo, hi = extract_salary_from_text(str(desc))
                if lo:
                    return lo, hi
        # Some tenants expose a structured 'jobPostingPay' or similar
        pay = info.get("jobPostingPay") or info.get("compensation")
        if isinstance(pay, dict):
            lo = pay.get("min") or pay.get("minimum")
            hi = pay.get("max") or pay.get("maximum")
            if lo and hi:
                try:
                    return int(float(lo)), int(float(hi))
                except (TypeError, ValueError):
                    pass
    # Fallback: any description-ish field at top level
    for k in ("jobDescription", "description", "content"):
        v = job_dict.get(k)
        if v:
            lo, hi = extract_salary_from_text(str(v))
            if lo:
                return lo, hi
    return None, None


def extract_salary_ashby(job_dict):
    """
    Ashby: structured 'compensation' when the company enables it, else parse
    the descriptionHtml / descriptionPlain. compensationTierSummary is a string
    like '$120K – $160K'. (Verified shape from Ashby public posting API.)
    """
    comp = job_dict.get("compensation", {})
    if isinstance(comp, dict):
        tier = comp.get("compensationTierSummary", "")
        if tier:
            # This field is salary-by-definition but has no keyword — prepend one
            # so the keyword-gated text parser accepts it.
            lo, hi = extract_salary_from_text(f"salary {tier}")
            if lo:
                return lo, hi
        for s in comp.get("summaryComponents", []) or []:
            amt = s.get("amount", {})
            if isinstance(amt, dict):
                lo = amt.get("min") or amt.get("amount")
                hi = amt.get("max") or amt.get("amount")
                if lo and hi:
                    try:
                        return int(lo), int(hi)
                    except (TypeError, ValueError):
                        pass
    # Fallback: description text
    for k in ("descriptionHtml", "descriptionPlain", "description"):
        v = job_dict.get(k)
        if v:
            lo, hi = extract_salary_from_text(str(v))
            if lo:
                return lo, hi
    return None, None


def extract_salary_lever(job_dict):
    """
    Lever: structured 'salaryRange' {min,max} when filled, else parse the
    descriptionPlain and the 'lists' array (Lever puts Compensation/Benefits
    bullets there as {text, content} objects).
    """
    sr = job_dict.get("salaryRange", {})
    if isinstance(sr, dict):
        lo, hi = sr.get("min"), sr.get("max")
        if lo and hi:
            try:
                return int(lo), int(hi)
            except (TypeError, ValueError):
                pass
    # Scan the lists array (Compensation section often lives here)
    for lst in job_dict.get("lists", []) or []:
        if isinstance(lst, dict):
            combined = f"{lst.get('text','')} {lst.get('content','')}"
            lo, hi = extract_salary_from_text(combined)
            if lo:
                return lo, hi
    # Description text
    for k in ("descriptionPlain", "description", "descriptionBody", "additional"):
        v = job_dict.get(k)
        if v:
            lo, hi = extract_salary_from_text(str(v))
            if lo:
                return lo, hi
    return None, None


def extract_salary_phenom(job_dict):
    """Phenom: try structured fields, then any description text."""
    for lo_key, hi_key in [
        ("salaryMin", "salaryMax"),
        ("minSalary", "maxSalary"),
        ("payRangeMin", "payRangeMax"),
    ]:
        lo, hi = job_dict.get(lo_key), job_dict.get(hi_key)
        if lo and hi:
            try:
                return int(lo), int(hi)
            except (TypeError, ValueError):
                pass
    for k in ("description", "jobDescription", "descriptionTeaser", "ats_job_description"):
        v = job_dict.get(k)
        if v:
            lo, hi = extract_salary_from_text(str(v))
            if lo:
                return lo, hi
    return None, None


def extract_salary_generic(job_dict, *desc_keys):
    """Generic: try a list of description keys with the text parser."""
    for k in desc_keys:
        v = job_dict.get(k)
        if v:
            lo, hi = extract_salary_from_text(str(v))
            if lo:
                return lo, hi
    return None, None


def fetch_workday_detail(base, tenant, site, ext_path):
    """
    Fetch a single Workday job's detail to get its description HTML (which may
    contain the salary range). Returns the parsed (min, max) or (None, None).

    IMPORTANT: Workday's externalPath already starts with '/job/...'. The detail
    endpoint is /wday/cxs/{tenant}/{site}{externalPath}. Earlier this function
    inserted an extra '/job' producing '.../job/job/...' → 404 → no salary.
    We normalize defensively so it works whether or not ext_path leads with /job.
    """
    if not ext_path:
        return None, None
    try:
        cxs = f"{base}/wday/cxs/{tenant}/{site}"
        ep = ext_path if ext_path.startswith("/") else "/" + ext_path
        # If ep already contains '/job/', use it as-is after the cxs root.
        if ep.startswith("/job/"):
            url = f"{cxs}{ep}"
        else:
            url = f"{cxs}/job{ep}"
        r = requests.get(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
            "Accept-Language": "en-US",
            "Referer": f"{base}/en-US/{site}",
        }, timeout=12)
        if r.status_code != 200 or not r.text.strip():
            return None, None
        return extract_salary_workday(r.json())
    except Exception:
        return None, None





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
                ms_min, ms_max = extract_salary_generic(j, "description", "jobSummary", "summary")
                results.append(make_job(
                    id=f"msft_{j['id']}", company="Microsoft",
                    title=title, location=locs,
                    url="https://apply.careers.microsoft.com" + j.get("positionUrl", ""),
                    posted_ts=j.get("postedTs", 0),
                    base_salary_min=ms_min, base_salary_max=ms_max,
                    base_salary_source="ats" if ms_min else None,
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
                    az_min, az_max = extract_salary_generic(j, "description_short", "description", "basic_qualifications")
                    results.append(make_job(
                        id=f"amzn_{j.get('job_id', '')}",
                        company="Amazon", title=title, location=location,
                        url="https://amazon.jobs" + j.get("job_path", ""),
                        base_salary_min=az_min, base_salary_max=az_max,
                        base_salary_source="ats" if az_min else None,
                    ))
                sleep(0.3)
            except Exception as e:
                print(f"  Amazon error ({kw}/{loc}): {e}")
    print(f"  Found {len(results)} Amazon jobs")
    return results


# ─── NETFLIX ──────────────────────────────────────────────────────────────────
def fetch_eightfold_detail(host, pid, domain=None):
    """
    Fetch a single Eightfold position's detail to get its full job_description
    (the listing API usually returns job_description=''). Parse salary from it.
    Endpoint: /api/apply/v2/jobs/{pid}?domain={domain}
    Returns (min, max) or (None, None).
    """
    if not pid:
        return None, None
    if domain is None:
        domain = host.split(".")[0] + ".com"
    try:
        url = f"https://{host}/api/apply/v2/jobs/{pid}?domain={domain}"
        r = requests.get(url, headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
            "Referer": f"https://{host}/careers",
        }, timeout=12)
        if r.status_code != 200 or not r.text.strip():
            return None, None
        d = r.json()
        # detail may nest under 'job' or be top-level
        obj = d.get("job", d)
        return extract_salary_generic(
            obj, "job_description", "description", "jobDescription", "descriptionTeaser")
    except Exception:
        return None, None


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
                nf_min, nf_max = extract_salary_generic(j, "job_description", "description", "descriptionTeaser")
                if not nf_min:
                    nf_min, nf_max = fetch_eightfold_detail("explore.jobs.netflix.net", jid, "netflix.com")
                    sleep(0.3)
                results.append(make_job(
                    id=f"netflix_{jid}",
                    company="Netflix",
                    title=title,
                    location=locs,
                    url=f"https://explore.jobs.netflix.net/careers?pid={jid}&domain=netflix.com",
                    base_salary_min=nf_min, base_salary_max=nf_max,
                    base_salary_source="ats" if nf_min else None,
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
            base_min, base_max = extract_salary_greenhouse(j)
            results.append(make_job(
                id=f"{board}_{j.get('id', '')}",
                company=company, title=title, location=location,
                url=j.get("absolute_url", ""),
                description=j.get("content", "")[:500],
                base_salary_min=base_min, base_salary_max=base_max,
                base_salary_source="ats" if base_min else None,
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
            base_min, base_max = extract_salary_lever(j)
            results.append(make_job(
                id=f"lever_{slug}_{j.get('id', '')}",
                company=company, title=title, location=location,
                url=j.get("hostedUrl", ""),
                description=j.get("descriptionPlain", "")[:500],
                base_salary_min=base_min, base_salary_max=base_max,
                base_salary_source="ats" if base_min else None,
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
            # List endpoint rarely fills compensation{}. Fetch the detail to get
            # the description text (jobAd.sections.jobDescription.text), parse salary.
            sr_min = sr_max = None
            # First try structured compensation on the list object
            comp = j.get("compensation", {})
            if isinstance(comp, dict) and comp:
                lo = comp.get("min") or comp.get("minSalary")
                hi = comp.get("max") or comp.get("maxSalary")
                if lo and hi:
                    try:
                        sr_min, sr_max = int(lo), int(hi)
                    except (TypeError, ValueError):
                        pass
            if not sr_min:
                try:
                    durl = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings/{jid}"
                    dr = requests.get(durl, headers={"User-Agent": "Mozilla/5.0"}, timeout=12)
                    if dr.status_code == 200:
                        dd = dr.json()
                        sections = dd.get("jobAd", {}).get("sections", {})
                        desc_text = " ".join(
                            sections.get(k, {}).get("text", "")
                            for k in ("jobDescription", "qualifications", "additionalInformation")
                        )
                        sr_min, sr_max = extract_salary_from_text(desc_text)
                    sleep(0.2)
                except Exception:
                    pass
            results.append(make_job(
                id=f"sr_{slug}_{jid}",
                company=company, title=title, location=location,
                url=f"https://careers.smartrecruiters.com/{slug}/{jid}",
                base_salary_min=sr_min, base_salary_max=sr_max,
                base_salary_source="ats" if sr_min else None,
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
            base_min, base_max = extract_salary_ashby(j)
            results.append(make_job(
                id=f"ashby_{slug}_{j.get('id', '')}",
                company=company, title=title, location=location,
                url=j.get("jobUrl", ""),
                base_salary_min=base_min, base_salary_max=base_max,
                base_salary_source="ats" if base_min else None,
            ))
    except Exception as e:
        print(f"  {company} error: {e}")
    print(f"  Found {len(results)} {company} jobs")
    return results


# ─── PHENOM PEOPLE FETCHER ────────────────────────────────────────────────────
def fetch_phenom(host, company, prefix):
    """
    Generic Phenom People fetcher. host is e.g. "jobs.nvidia.com".
    Works for NVIDIA, Qualcomm, Zoom, eBay, Home Depot, Equifax,
    Procore, ADP, DIRECTV, Cox, Intuit.
    """
    print(f"Fetching {company} (Phenom)...")
    results = []
    seen = set()
    for kw in SEARCH_QUERIES:
        try:
            url = f"https://{host}/api/jobs"
            params = {"query": kw, "page": 1, "pageSize": 20, "sortBy": "relevance"}
            r = requests.get(url, params=params, headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json",
                "Referer": f"https://{host}/search-jobs",
            }, timeout=15)
            if r.status_code != 200 or not r.text.strip():
                if kw == SEARCH_QUERIES[0]:
                    print(f"  {company} Phenom: HTTP {r.status_code} (host may be wrong)")
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
                job_url = j.get("url") or j.get("applyUrl") or f"https://{host}/job/{jid}"
                base_min, base_max = extract_salary_phenom(j)
                results.append(make_job(
                    id=f"{prefix}_{jid}",
                    company=company, title=title, location=str(location),
                    url=job_url,
                    base_salary_min=base_min, base_salary_max=base_max,
                    base_salary_source="ats" if base_min else None,
                ))
            sleep(0.5)
        except Exception as e:
            print(f"  {company} Phenom error ({kw}): {e}")
    print(f"  Found {len(results)} {company} jobs")
    return results


# ─── AVATURE FETCHER ──────────────────────────────────────────────────────────
def fetch_avature(host, company, prefix):
    """
    Generic Avature fetcher. host is e.g. "delta.avature.net" or
    "careers.lululemon.com". Built from the Deloitte pattern.
    """
    print(f"Fetching {company} (Avature)...")
    results = []
    seen = set()
    for kw in SEARCH_QUERIES:
        try:
            url = (f"https://{host}/en_US/careers/SearchJobs/"
                   f"{requests.utils.quote(kw)}?projectOffset=0"
                   f"&projectSort=POSTING_DATE&projectSortDirection=DESC")
            r = requests.get(url, headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json, text/javascript, */*",
                "X-Requested-With": "XMLHttpRequest",
            }, timeout=15)
            if r.status_code != 200 or not r.text.strip():
                if kw == SEARCH_QUERIES[0]:
                    print(f"  {company} Avature: HTTP {r.status_code}")
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
                av_min, av_max = extract_salary_generic(
                    j, "projectDescription", "description", "projectCustomField5")
                results.append(make_job(
                    id=f"{prefix}_{jid}",
                    company=company, title=title, location=location,
                    url=f"https://{host}/en_US/careers/JobDetail/{jid}",
                    base_salary_min=av_min, base_salary_max=av_max,
                    base_salary_source="ats" if av_min else None,
                ))
            sleep(0.5)
        except Exception as e:
            print(f"  {company} Avature error ({kw}): {e}")
    print(f"  Found {len(results)} {company} jobs")
    return results


# ─── HIBOB CAREERS FETCHER ────────────────────────────────────────────────────
def fetch_hibob(slug, company):
    """HiBob HRIS exposes a public careers JSON at careers.hibob.com/<slug>"""
    print(f"Fetching {company} (HiBob)...")
    results = []
    try:
        # HiBob endpoint pattern
        url = f"https://{slug}.careers.hibob.com/api/positions"
        r = requests.get(url, headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
        }, timeout=10)
        if r.status_code != 200:
            print(f"  {company} HiBob: HTTP {r.status_code}")
            return results
        for j in r.json().get("positions", r.json() if isinstance(r.json(), list) else []):
            if not isinstance(j, dict):
                continue
            title = j.get("title", j.get("name", ""))
            loc_obj = j.get("location", "")
            if isinstance(loc_obj, dict):
                loc_obj = loc_obj.get("city", "") + ", " + loc_obj.get("country", "")
            if not passes(title, str(loc_obj), "remote"):
                continue
            jid = str(j.get("id", j.get("jobId", "")))
            hb_min, hb_max = extract_salary_generic(j, "description", "jobDescription", "about")
            results.append(make_job(
                id=f"hibob_{slug}_{jid}",
                company=company, title=title, location=str(loc_obj),
                url=j.get("url", f"https://{slug}.careers.hibob.com/jobs/{jid}"),
                base_salary_min=hb_min, base_salary_max=hb_max,
                base_salary_source="ats" if hb_min else None,
            ))
    except Exception as e:
        print(f"  {company} HiBob error: {e}")
    print(f"  Found {len(results)} {company} jobs")
    return results


# ─── EIGHTFOLD FETCHER ────────────────────────────────────────────────────────
def fetch_eightfold(host, company, prefix, extra_query=""):
    """
    Generic Eightfold AI ATS fetcher. host is e.g. "nvidia.eightfold.ai".
    Uses the same /api/apply/v2/jobs endpoint that powers Netflix's existing
    fetch_netflix() function. Paginates 10 at a time.
    """
    print(f"Fetching {company} (Eightfold)...")
    results = []
    seen = set()
    page_size = 10
    start = 0
    us_indicators = ["united states", "us", "remote", "ca", "ny", "tx", "ga",
                     "los angeles", "atlanta", "san francisco", "santa clara",
                     "seattle", "new york", "austin"]
    while start < 200:  # cap at 20 pages = 200 jobs
        try:
            url = (
                f"https://{host}/api/apply/v2/jobs"
                f"?domain={host.split('.')[0]}.com"
                f"&start={start}&num={page_size}"
                f"{extra_query}"
            )
            r = requests.get(url, headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json",
                "Referer": f"https://{host}/careers",
            }, timeout=15)
            if r.status_code != 200 or not r.text.strip():
                if start == 0:
                    print(f"  {company} Eightfold: HTTP {r.status_code}")
                break
            data = r.json()
            positions = data.get("positions", [])
            if not positions:
                break
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
                locs = ", ".join([p.strip() for p in raw_loc.split(",")]) if raw_loc else ""
                if not is_relevant_title(title):
                    continue
                if locs and not any(ind in locs.lower() for ind in us_indicators):
                    continue
                base_min, base_max = extract_salary_generic(
                    j, "job_description", "description", "descriptionTeaser")
                if not base_min:
                    base_min, base_max = fetch_eightfold_detail(host, jid)
                    sleep(0.3)
                results.append(make_job(
                    id=f"{prefix}_{jid}",
                    company=company, title=title, location=locs,
                    url=f"https://{host}/careers?pid={jid}&domain={host.split('.')[0]}.com",
                    base_salary_min=base_min, base_salary_max=base_max,
                    base_salary_source="ats" if base_min else None,
                ))
            if len(positions) < page_size:
                break
            start += page_size
            sleep(0.4)
        except Exception as e:
            print(f"  {company} Eightfold error (start={start}): {e}")
            break
    print(f"  Found {len(results)} {company} jobs")
    return results


# ─── PHENOM WIDGETS FETCHER (for sites that need refNum + CSRF) ──────────────
def fetch_phenom_widgets(host, company, prefix, ref_num):
    """
    Phenom widgets API — POST to /widgets with refNum + ddoKey.
    More reliable than the /api/jobs path used by fetch_phenom() but requires
    knowing the per-company refNum (extractable from page HTML).
    Currently used for: eBay (EBAEBAUS).
    """
    print(f"Fetching {company} (Phenom widgets)...")
    results = []
    seen = set()
    page_size = 20
    for start_page in range(0, 5):  # 5 pages × 20 = up to 100 jobs
        try:
            url = f"https://{host}/widgets"
            payload = {
                "lang": "en_us",
                "deviceType": "desktop",
                "country": "United States",
                "pageName": "search-results",
                "size": page_size,
                "from": start_page * page_size,
                "jobs": True,
                "counts": True,
                "all_fields": ["category", "country", "city", "type"],
                "clearAll": False,
                "jdsource": "facets",
                "isSliderEnable": False,
                "pageId": "page20",
                "siteType": "external",
                "keywords": "product manager",
                "global": False,
                "selected_fields": {},
                "sort": {"order": "desc", "field": "postedDate"},
                "locationData": {},
                "refNum": ref_num,
                "ddoKey": "refineSearch",
            }
            r = requests.post(url, json=payload, headers={
                "User-Agent": "Mozilla/5.0",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Referer": f"https://{host}/us/en/search-results",
            }, timeout=15)
            if r.status_code != 200 or not r.text.strip():
                if start_page == 0:
                    print(f"  {company} Phenom widgets: HTTP {r.status_code}")
                break
            data = r.json()
            jobs_list = data.get("refineSearch", {}).get("data", {}).get("jobs", [])
            if not jobs_list:
                break
            for j in jobs_list:
                if not isinstance(j, dict):
                    continue
                jid = str(j.get("jobId", j.get("jobSeqNo", "")))
                if not jid or jid in seen:
                    continue
                seen.add(jid)
                title = j.get("title", j.get("jobTitle", ""))
                location = j.get("location", "")
                if isinstance(location, list):
                    location = ", ".join(location[:2])
                if not passes(title, str(location), "remote"):
                    continue
                job_url = j.get("jobUrl") or j.get("applyUrl") or f"https://{host}/job/{jid}"
                base_min, base_max = extract_salary_generic(
                    j, "descriptionTeaser", "description", "jobDescription", "ats_job_description")
                results.append(make_job(
                    id=f"{prefix}_{jid}",
                    company=company, title=title, location=str(location),
                    url=job_url,
                    base_salary_min=base_min, base_salary_max=base_max,
                    base_salary_source="ats" if base_min else None,
                ))
            if len(jobs_list) < page_size:
                break
            sleep(0.3)
        except Exception as e:
            print(f"  {company} Phenom widgets error (page {start_page}): {e}")
            break
    print(f"  Found {len(results)} {company} jobs")
    return results


# ─── THE TRADE DESK ───────────────────────────────────────────────────────────
def fetch_trade_desk():
    """
    Trade Desk uses Eightfold AI ATS (same as Netflix).
    Pagination: 10 per page. Uses Teams/Region filters instead of keyword.
    """
    print("Fetching The Trade Desk...")
    results = []
    seen = set()
    page_size = 10
    start = 0
    us_indicators = ["united states", "us", "remote", "los angeles", "ventura",
                     "santa monica", "culver city", "atlanta", "ca", "ga"]

    while start < 200:
        try:
            url = (
                f"https://careers.thetradedesk.com/api/apply/v2/jobs"
                f"?domain=thetradedesk.com&start={start}&num={page_size}"
                f"&Teams=Product%20Management&Region=ucan"
            )
            r = requests.get(url, headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json",
                "Referer": "https://careers.thetradedesk.com/"
            }, timeout=15)
            if r.status_code != 200 or not r.text.strip():
                if start == 0:
                    print(f"  Trade Desk: HTTP {r.status_code}")
                break
            data = r.json()
            positions = data.get("positions", [])
            print(f"  Trade Desk page start={start}: {len(positions)} positions")
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
                locs = ", ".join([p.strip() for p in raw_loc.split(",")]) if raw_loc else ""
                if not is_relevant_title(title):
                    continue
                if locs and not any(ind in locs.lower() for ind in us_indicators):
                    continue
                ttd_min, ttd_max = extract_salary_generic(j, "job_description", "description", "descriptionTeaser")
                if not ttd_min:
                    ttd_min, ttd_max = fetch_eightfold_detail("careers.thetradedesk.com", jid, "thetradedesk.com")
                    sleep(0.3)
                results.append(make_job(
                    id=f"ttd_{jid}",
                    company="The Trade Desk", title=title, location=locs,
                    url=f"https://careers.thetradedesk.com/careers?pid={jid}&domain=thetradedesk.com",
                    base_salary_min=ttd_min, base_salary_max=ttd_max,
                    base_salary_source="ats" if ttd_min else None,
                ))
            if len(positions) < page_size:
                break
            start += page_size
            sleep(0.4)
        except Exception as e:
            print(f"  Trade Desk error (start={start}): {e}")
            break
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
                sf_min, sf_max = fetch_workday_detail(
                    "https://salesforce.wd12.myworkdayjobs.com",
                    "salesforce", "External_Career_Site", ext_path)
                results.append(make_job(
                    id=f"salesforce_{jid}",
                    company="Salesforce", title=title, location=location,
                    url="https://salesforce.wd12.myworkdayjobs.com/en-US/External_Career_Site" + ext_path,
                    base_salary_min=sf_min, base_salary_max=sf_max,
                    base_salary_source="ats" if sf_min else None,
                ))
                sleep(0.25)
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
                sn_min = sn_max = None
                try:
                    durl = f"https://api.smartrecruiters.com/v1/companies/ServiceNow/postings/{jid}"
                    dr = requests.get(durl, headers={"User-Agent": "Mozilla/5.0"}, timeout=12)
                    if dr.status_code == 200:
                        sections = dr.json().get("jobAd", {}).get("sections", {})
                        desc_text = " ".join(
                            sections.get(k, {}).get("text", "")
                            for k in ("jobDescription", "qualifications", "additionalInformation")
                        )
                        sn_min, sn_max = extract_salary_from_text(desc_text)
                    sleep(0.2)
                except Exception:
                    pass
                results.append(make_job(
                    id=f"servicenow_{jid}",
                    company="ServiceNow", title=title, location=location,
                    url=f"https://careers.smartrecruiters.com/ServiceNow/{jid}",
                    base_salary_min=sn_min, base_salary_max=sn_max,
                    base_salary_source="ats" if sn_min else None,
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
                # Salary isn't in the search result — fetch the job detail.
                # Only done for jobs that passed filters, so cost is bounded.
                base_min, base_max = fetch_workday_detail(base, tenant, site, ext_path)
                results.append(make_job(
                    id=f"{prefix}_{jid}",
                    company=company, title=title, location=location,
                    url=f"{base}/en-US/{site}" + ext_path,
                    base_salary_min=base_min, base_salary_max=base_max,
                    base_salary_source="ats" if base_min else None,
                ))
                sleep(0.25)   # be gentle — one detail call per relevant job
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
                dl_min, dl_max = extract_salary_generic(
                    j, "projectDescription", "description", "projectCustomField5")
                results.append(make_job(
                    id=f"deloitte_{jid}",
                    company="Deloitte", title=title, location=location,
                    url=f"https://apply.deloitte.com/en_US/careers/JobDetail/{jid}",
                    base_salary_min=dl_min, base_salary_max=dl_max,
                    base_salary_source="ats" if dl_min else None,
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
                it_min, it_max = extract_salary_generic(
                    j, "description", "jobDescription", "descriptionTeaser", "ats_job_description")
                results.append(make_job(
                    id=f"intuit_{jid}",
                    company="Intuit", title=title, location=str(location),
                    url=job_url,
                    base_salary_min=it_min, base_salary_max=it_max,
                    base_salary_source="ats" if it_min else None,
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
                yc_min, yc_max = None, None
                sr = j.get("salaryRange") or j.get("salary")
                if isinstance(sr, str) and "$" in sr:
                    yc_min, yc_max = extract_salary_from_text(f"salary {sr}")
                elif isinstance(sr, dict):
                    lo, hi = sr.get("min"), sr.get("max")
                    if lo and hi:
                        try:
                            yc_min, yc_max = int(lo), int(hi)
                        except (TypeError, ValueError):
                            pass
                if not yc_min:
                    yc_min, yc_max = extract_salary_generic(j, "description", "jobType")
                results.append(make_job(
                    id=f"yc_{jid}",
                    company=f"YC: {company_name}", title=title, location=location,
                    url=j.get("url", "https://www.workatastartup.com/jobs"),
                    base_salary_min=yc_min, base_salary_max=yc_max,
                    base_salary_source="board" if yc_min else None,
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
            desc = item.findtext("description", "")
            wwr_min, wwr_max = extract_salary_from_text(f"{title} {desc}")
            results.append(make_job(
                id=f"wwr_{abs(hash(link))}",
                company="We Work Remotely", title=title,
                location="Remote", url=link,
                base_salary_min=wwr_min, base_salary_max=wwr_max,
                base_salary_source="board" if wwr_min else None,
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
                    dc_min, dc_max = extract_salary_generic(j, "salary", "summary", "description")
                    results.append(make_job(
                        id=f"dice_{j.get('id', '')}",
                        company=j.get("advertiserName") or "Dice",
                        title=title, location=location,
                        url=j.get("applyDataItems", [{}])[0].get("applyUrl", "https://dice.com"),
                        base_salary_min=dc_min, base_salary_max=dc_max,
                        base_salary_source="board" if dc_min else None,
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
                bi_min, bi_max = None, None
                lo = j.get("salaryStart") or j.get("compensationStart")
                hi = j.get("salaryEnd") or j.get("compensationEnd")
                if lo and hi:
                    try:
                        bi_min, bi_max = int(lo), int(hi)
                    except (TypeError, ValueError):
                        pass
                if not bi_min:
                    bi_min, bi_max = extract_salary_generic(j, "description", "summary")
                results.append(make_job(
                    id=f"builtin_{jid}",
                    company=j.get("company", {}).get("name", "Built In"),
                    title=title, location=location,
                    url="https://builtin.com/job/" + str(j.get("slug", "")),
                    base_salary_min=bi_min, base_salary_max=bi_max,
                    base_salary_source="board" if bi_min else None,
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
    ("appsflyer", "AppsFlyer"),
    ("branch", "Branch"),
    ("pinterest", "Pinterest"),
    ("cognitiv", "Cognitiv"),
    ("klaviyo", "Klaviyo"),
    ("braze", "Braze"),
    ("iterable", "Iterable"),
    ("inmobi", "InMobi"),
    ("instacart", "Instacart Ads"),
    ("zetaglobal", "Zeta Global"),

    # ── New: AdTech / Media ──
    ("twitch", "Twitch"),
    ("mediaalpha", "MediaAlpha"),
    ("fandom", "Fandom"),
    ("crunchyroll", "Crunchyroll"),
    ("thenewyorktimes", "The New York Times"),

    # ── New: Big Tech / Enterprise SaaS ──
    ("dropbox", "Dropbox"),
    ("elastic", "Elastic"),
    ("okta", "Okta"),
    ("twilio", "Twilio"),
    ("duolingo", "Duolingo"),
    ("honeycomb", "Honeycomb"),
    ("onetrust", "OneTrust"),
    ("riotgames", "Riot Games"),
    ("epicgames", "Epic Games"),
    ("scopely", "Scopely"),
    ("attentive", "Attentive"),
    ("aura", "Aura"),
    ("crexi", "Crexi"),
    ("justanswer", "JustAnswer"),
    ("onxmaps", "onXmaps"),

    # ── New: Atlanta / Fintech ──
    ("calendly", "Calendly"),
    ("fanduel", "FanDuel"),
    ("carvana", "Carvana"),
    ("robinhood", "Robinhood"),
    ("affirm", "Affirm"),
    ("mercury", "Mercury"),
    ("gemini", "Gemini"),
    ("billcom", "Bill.com"),
    ("relaypayments", "Relay Payments"),

    # ── New: Health / Consumer ──
    ("tebra", "Tebra"),
    ("calm", "Calm"),
    ("reformation", "Reformation"),
    ("taskrabbit", "Taskrabbit"),

    # ── VERIFIED corrections from career-URL session ──
    ("altruist", "Altruist"),                # was 'altruistllc' — 404'd
    ("chartbeatinc", "Chartbeat"),           # was 'chartbeat'  — 404'd
    ("weedmaps77", "Weedmaps"),              # was 'weedmaps'   — 404'd
    ("axon", "Axon"),                        # confirmed Greenhouse via URL param
]


LEVER_COMPANIES = [
    ("spotify", "Spotify"),
    ("houzz", "Houzz"),                      # verified via jobs.lever.co/houzz/...
    # NOTE: Atlassian removed — they moved off Lever
]


# ── ASHBY COMPANIES ──
# Confirmed from URLs like jobs.ashbyhq.com/<slug>/... or ?ashby_jid=...
ASHBY_COMPANIES = [
    ("madhive", "Madhive"),
    ("Acorns", "Acorns"),                    # case-sensitive! jobs.ashbyhq.com/Acorns
    ("clickup", "ClickUp"),
    ("creatoriq", "CreatorIQ"),
    ("raptive", "Raptive"),
    ("drata", "Drata"),
    ("hims-and-hers", "Hims & Hers"),
    ("Flock Safety", "Flock Safety"),        # URL-encoded space
    ("fullstory", "FullStory"),
]


# ── PHENOM COMPANIES (basic /api/jobs path) ──
# Only kept the ones that actually respond on /api/jobs (Intuit-style).
# Removed: NVIDIA/Qualcomm/eBay/Zoom/Equifax/Home Depot (use widgets API),
# ADP/Cox/Procore (403/404 — Cloudflare-blocked), DIRECTV (uses /api with different path).
PHENOM_COMPANIES = [
    # Intentionally empty for now — Intuit has its own dedicated fetcher.
    # Phenom is too fragmented per-customer to scrape generically.
]


# ── EIGHTFOLD COMPANIES (Netflix-style API) ──
# Format: (host, display name, prefix, extra_query)
# extra_query optional — e.g. "&Teams=Product%20Management" to pre-filter
# Empty for now — NVIDIA Eightfold endpoint returns 403 (anti-bot).
# NVIDIA Workday entry below should be the primary path.
EIGHTFOLD_COMPANIES = [
]


# ── PHENOM WIDGETS COMPANIES (refNum-based) ──
# Format: (host, display name, prefix, refNum)
# refNum is extracted from each company's careers page HTML.
# These work via POST /widgets with refNum + ddoKey: "refineSearch"
PHENOM_WIDGETS_COMPANIES = [
    ("jobs.ebayinc.com", "eBay", "ebay", "EBAEBAUS"),
]


# ── AVATURE COMPANIES ──
# Format: (host, display name, prefix)
AVATURE_COMPANIES = [
    ("delta.avature.net",     "Delta Air Lines", "delta"),
    ("careers.lululemon.com", "lululemon",       "lulu"),
]


# ── HIBOB COMPANIES ──
HIBOB_COMPANIES = [
    ("zepz", "Zepz"),
]


# Format: (tenant, wd_num, site, display name, prefix)
WORKDAY_COMPANIES = [
    # ── VERIFIED via career URLs ──
    ("integralads",   1, "IAScareers",       "IAS",          "ias"),
    ("liveramp",      5, "LiveRampCareers",  "LiveRamp",     "liveramp"),
    ("goodrx",        1, "careers",          "GoodRx",       "goodrx"),
    ("logitech",      5, "Logitech",         "Logitech",     "logitech"),
    ("ouryahoo",      5, "careers",          "Yahoo",        "yahoo"),
    ("zillow",        5, "Zillow_Group_External", "Zillow",  "zillow"),
    ("servicetitan",  1, "ServiceTitan",     "ServiceTitan", "servicetitan"),
    ("broadcom",      1, "External_Career",  "Broadcom",     "broadcom"),
    ("cloudera",      5, "External_Career",  "Cloudera",     "cloudera"),

    # ── Big tech (existing, kept) ──
    ("autodesk", 1, "Ext", "Autodesk", "autodesk"),

    # ── Existing: Atlanta / fintech ──
    # American Express removed — confirmed Oracle Recruiting Cloud, not Workday

    # ── Patch-in: NVIDIA also has a Workday — keeping as backup to Eightfold ──
    ("nvidia",      5, "NVIDIAExternalCareerSite", "NVIDIA (Workday)", "nvidiawd"),

    # ── Qualcomm Workday — needs verification; trying common qualcomm.wd5 path ──
    ("qualcomm",    5, "External",                 "Qualcomm",         "qualcomm"),
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

    # Ashby (bulk)
    for slug, company in ASHBY_COMPANIES:
        all_fresh += safe_fetch(fetch_ashby, slug, company)
        sleep(0.2)

    # Workday (bulk via generic fetcher)
    for tenant, wd_num, site, company, prefix in WORKDAY_COMPANIES:
        all_fresh += safe_fetch(fetch_workday, tenant, wd_num, site, company, prefix)
        sleep(0.3)

    # Phenom People (bulk via generic fetcher) — for sites using /api/jobs
    for host, company, prefix in PHENOM_COMPANIES:
        all_fresh += safe_fetch(fetch_phenom, host, company, prefix)
        sleep(0.3)

    # Eightfold AI (Netflix-style) — for NVIDIA
    for host, company, prefix, extra_q in EIGHTFOLD_COMPANIES:
        all_fresh += safe_fetch(fetch_eightfold, host, company, prefix, extra_q)
        sleep(0.4)

    # Phenom widgets (POST /widgets with refNum) — for eBay
    for host, company, prefix, ref_num in PHENOM_WIDGETS_COMPANIES:
        all_fresh += safe_fetch(fetch_phenom_widgets, host, company, prefix, ref_num)
        sleep(0.3)

    # Avature (bulk) — Delta, lululemon
    for host, company, prefix in AVATURE_COMPANIES:
        all_fresh += safe_fetch(fetch_avature, host, company, prefix)
        sleep(0.3)

    # HiBob — Zepz
    for slug, company in HIBOB_COMPANIES:
        all_fresh += safe_fetch(fetch_hibob, slug, company)
        sleep(0.2)

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
    SALARY_FIELDS = (
        "base_salary_min", "base_salary_max", "base_salary_source",
        "bonus_pct", "equity_pct",
        "tc_estimate_min", "tc_estimate_max",
        "salary_tier", "salary_level", "salary_confidence",
    )
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

            # Backfill salary/TC fields: if the existing record is missing them
            # (or has them as None), copy from the fresh scrape. This handles
            # the case where jobs.json was populated before salary extraction
            # was added. We never overwrite an already-populated salary value
            # (employers don't change disclosed pay between scrapes).
            for f in SALARY_FIELDS:
                existing_val = existing[jid].get(f)
                new_val = job.get(f)
                if existing_val in (None, "") and new_val not in (None, ""):
                    existing[jid][f] = new_val

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
