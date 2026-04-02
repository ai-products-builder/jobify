import json
import os
import time
import urllib.request
from html.parser import HTMLParser
import anthropic

RESUME_ADS = os.environ["RESUME_ADS"]
RESUME_DATA = os.environ["RESUME_DATA"]

client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

SKIP_COMPANIES = {
    "wellfound", "underdog", "trueup", "techfetch", "pmhq",
    "mindtheproduct", "productfolks", "productjobs",
    "roundel", "adcolony", "target roundel",
    "dice", "indeed", "glassdoor", "mind the product", "innovid", "moloco",
    "product manager hq", "product jobs", "tech fetch", "the product folks",
    "builtin", "we work", "remotely", "true up"
}

# These companies use JavaScript-rendered career sites — live URL fetching
# will always return a JS shell with no job content. Description must be
# stored at scrape time or scored on title/company only.
NO_FETCH_DOMAINS = [
    "explore.jobs.netflix.net",      # Eightfold AI — JS rendered
    "apply.careers.microsoft.com",   # Microsoft — JS rendered, blocks scrapers
    "careers.snap.com",              # Workday — JS rendered
    "apply.deloitte.com",            # Avature — JS rendered
    "myworkdayjobs.com",             # All Workday sites — JS rendered
    "smartrecruiters.com",           # SmartRecruiters — JS rendered
    "jobs.netflix.com",              # Netflix legacy URL — JS rendered
]


class HTMLTextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.result = []
        self.skip_tags = {"script", "style", "nav", "footer", "header"}
        self.current_skip = False

    def handle_starttag(self, tag, attrs):
        if tag in self.skip_tags:
            self.current_skip = True

    def handle_endtag(self, tag):
        if tag in self.skip_tags:
            self.current_skip = False

    def handle_data(self, data):
        if not self.current_skip:
            self.result.append(data)

    def get_text(self):
        return " ".join(" ".join(self.result).split())


def should_skip_fetch(url: str) -> bool:
    """Return True if live URL fetching is known to be useless for this URL."""
    for domain in NO_FETCH_DOMAINS:
        if domain in url:
            return True
    return False


def fetch_description(url: str) -> str:
    if should_skip_fetch(url):
        print(f"   ⏭️  Skipping live fetch (JS-rendered site): {url[:60]}")
        return ""
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; JobifyBot/1.0)"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
        parser = HTMLTextExtractor()
        parser.feed(html)
        text = parser.get_text()
        return text[500:4000]
    except Exception as e:
        print(f"   ⚠️  Fetch failed ({url[:60]}...): {e}")
        return ""


def clean_html(raw: str) -> str:
    if not raw:
        return ""
    parser = HTMLTextExtractor()
    parser.feed(raw)
    return parser.get_text()[:2500]


def should_skip(job: dict) -> tuple[bool, str]:
    company = job.get("company", "").lower()
    job_id = job.get("id", "").lower()
    for skip in SKIP_COMPANIES:
        if skip in company or job_id.startswith(skip):
            return True, f"Job board aggregator: {company}"
    return False, ""


def is_properly_scored(job: dict) -> bool:
    """
    Only skip jobs that have a real non-zero score.
    0,0 scores from previous failed runs must be re-scored.
    """
    if job.get("description_source") == "skipped":
        return True
    score = job.get("match_score")
    ats = job.get("ats_score")
    return (
        score is not None and ats is not None and
        isinstance(score, (int, float)) and isinstance(ats, (int, float)) and
        (score > 0 or ats > 0)
    )


def score_job(job: dict, description: str, desc_source: str) -> dict:
    title = job.get("title", "")
    company = job.get("company", "")
    location = job.get("location", "")

    ads_keywords = [
        "adtech", "ads", "programmatic", "rtb", "monetization",
        "attribution", "measurement", "publisher", "dsp", "ssp", "sdk",
        "advertising", "ad platform", "ad exchange",
    ]
    resume_label = "ADS" if any(k in title.lower() for k in ads_keywords) else "DATA"
    resume_text = RESUME_ADS if resume_label == "ADS" else RESUME_DATA

    desc_note = ""
    if desc_source == "none":
        desc_note = "\nNOTE: No job description available. Score based on title/company only. Lower confidence — reflect this with conservative scores."

    prompt = f"""You are an expert technical recruiter evaluating job fit for a senior product leader with 15+ years in AdTech and Data platforms.

RESUME ({resume_label}):
{resume_text}

JOB:
Title: {title}
Company: {company}
Location: {location}
Description: {description if description else "Not available"}{desc_note}

Respond ONLY with valid JSON, no markdown, no extra text:
{{
  "match_score": <integer 0-100, overall resume-to-job fit>,
  "ats_score": <integer 0-100, keyword overlap between resume and likely ATS filters for this role>,
  "resume_used": "{resume_label}",
  "reason": "<one concise sentence explaining the score>",
  "skills_gap": "<one concise sentence on key missing skills, or 'Strong match — no significant gaps' if well aligned>",
  "confidence": "<high|medium|low depending on how much job info was available>"
}}

Scoring guide:
- match_score 80+: Strong fit, should apply
- match_score 60-79: Decent fit, worth considering
- match_score <60: Weak fit, likely not worth applying
- ats_score: how well resume keywords match what an ATS would filter for in this specific role
- If no description available, set confidence to low and be conservative with scores"""

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = message.content[0].text.strip()

    # Strip markdown fences if Claude wraps response in ```json ... ```
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    if not raw:
        raise ValueError("Claude returned empty response")

    result = json.loads(raw)

    for field in ["match_score", "ats_score", "reason", "skills_gap", "confidence"]:
        if field not in result:
            raise ValueError(f"Missing field in Claude response: {field}")

    return result


def main():
    jobs_path = "jobs.json"
    with open(jobs_path) as f:
        jobs = json.load(f)

    scored = 0
    skipped = 0
    already_done = 0
    failed = 0
    no_desc = 0

    for job in jobs.values():
        if is_properly_scored(job):
            already_done += 1
            continue

        skip, reason = should_skip(job)
        if skip:
            job["match_score"] = 0
            job["ats_score"] = 0
            job["resume_used"] = "N/A"
            job["reason"] = f"Auto-skipped: {reason}"
            job["skills_gap"] = "N/A"
            job["confidence"] = "N/A"
            job["description_source"] = "skipped"
            skipped += 1
            print(f"⏭️  SKIP | {job.get('company')} | {job.get('title')}")
            continue

        # Determine description source
        stored_desc = clean_html(job.get("description", ""))
        if len(stored_desc) > 200:
            description = stored_desc
            desc_source = "stored"
            print(f"📄 Stored desc | {job.get('company')} | {job.get('title')}")
        else:
            print(f"🌐 Fetching desc | {job.get('company')} | {job.get('title')}")
            description = fetch_description(job.get("url", ""))
            time.sleep(1)
            if len(description) > 200:
                desc_source = "fetched"
            else:
                desc_source = "none"
                no_desc += 1
                print(f"   ⚠️  No description found — scoring on title/company only")

        job["description_source"] = desc_source

        try:
            result = score_job(job, description, desc_source)
            job["match_score"] = result["match_score"]
            job["ats_score"] = result["ats_score"]
            job["resume_used"] = result["resume_used"]
            job["reason"] = result["reason"]
            job["skills_gap"] = result["skills_gap"]
            job["confidence"] = result.get("confidence", "medium")
            job.pop("score", None)
            scored += 1
            print(f"✅ match:{result['match_score']} ats:{result['ats_score']} conf:{result.get('confidence','?')} | {job['company']} | {job['title']}")
        except Exception as e:
            print(f"❌ Failed: {job.get('company')} | {job.get('title')} | {e}")
            failed += 1

        time.sleep(0.5)

    with open(jobs_path, "w") as f:
        json.dump(jobs, f, indent=2)

    print(f"\n{'='*50}")
    print(f"✅ Scored:            {scored}")
    print(f"⏭️  Skipped:           {skipped}")
    print(f"✔️  Already done:      {already_done}")
    print(f"⚠️  No description:   {no_desc}")
    print(f"❌ Failed:            {failed}")
    print(f"💰 API calls used:    {scored}")


if __name__ == "__main__":
    main()
