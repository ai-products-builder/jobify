import json
import os
import time
import urllib.request
from html.parser import HTMLParser
import anthropic

RESUME_ADS = os.environ["RESUME_ADS"]
RESUME_DATA = os.environ["RESUME_DATA"]

client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

# ── Only skip job board aggregators — not real job postings ─────────────────
SKIP_COMPANIES = {
    "wellfound", "underdog", "trueup", "techfetch", "pmhq",
    "mindtheproduct", "productfolks", "productjobs",
    "roundel", "adcolony", "target roundel",
    "dice", "indeed", "glassdoor", "mind the product", "innovid" , "moloco", "product manager HQ", "product jobs", "tech fetch", "the product folks" , "builtin" , "we work", "remotely" , "true up"
}


class HTMLTextExtractor(HTMLParser):
    """Strip HTML tags and return clean text."""
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


def fetch_description(url: str) -> str:
    """Fetch job page and extract clean text description."""
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
        print(f"   ⚠️  Could not fetch description: {e}")
        return ""


def clean_html(raw: str) -> str:
    """Clean HTML from stored description field."""
    if not raw:
        return ""
    parser = HTMLTextExtractor()
    parser.feed(raw)
    return parser.get_text()[:2500]


def should_skip(job: dict) -> tuple[bool, str]:
    """Only skip job board aggregators."""
    company = job.get("company", "").lower()
    job_id = job.get("id", "").lower()

    for skip in SKIP_COMPANIES:
        if skip in company or job_id.startswith(skip):
            return True, f"Job board aggregator: {company}"

    return False, ""


def score_job(job: dict, description: str) -> dict:
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

    prompt = f"""You are an expert technical recruiter evaluating job fit for a senior product leader.

RESUME ({resume_label}):
{resume_text}

JOB:
Title: {title}
Company: {company}
Location: {location}
Description: {description}

Respond ONLY with valid JSON, no markdown:
{{
  "match_score": <integer 0-100, overall resume-to-job fit>,
  "ats_score": <integer 0-100, keyword overlap with likely ATS filters>,
  "resume_used": "{resume_label}",
  "reason": "<one concise sentence on why this score>",
  "skills_gap": "<one concise sentence on what is missing, or 'Strong match' if none>"
}}

Scoring guide:
- match_score 80+: Strong fit, should apply
- match_score 60-79: Decent fit, worth considering
- match_score <60: Weak fit
- ats_score reflects keyword overlap between resume and ATS filters for this specific role"""

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=250,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = message.content[0].text.strip()
    result = json.loads(raw)
    return result


def main():
    jobs_path = "jobs.json"
    with open(jobs_path) as f:
        jobs = json.load(f)

    scored = 0
    skipped = 0
    already_done = 0
    failed = 0

    for job in jobs.values():
        # Skip already properly scored jobs
        if (job.get("match_score") is not None and
                job.get("ats_score") is not None):
            already_done += 1
            continue

        # Only skip job board aggregators
        skip, reason = should_skip(job)
        if skip:
            job["match_score"] = 0
            job["ats_score"] = 0
            job["resume_used"] = "N/A"
            job["reason"] = f"Auto-skipped: {reason}"
            job["skills_gap"] = "N/A"
            skipped += 1
            print(f"⏭️  SKIP | {job.get('company')} | {job.get('title')}")
            continue

        # Use stored description if available, else fetch live
        stored_desc = clean_html(job.get("description", ""))
        if len(stored_desc) > 200:
            description = stored_desc
            print(f"📄 Stored | {job.get('company')} | {job.get('title')}")
        else:
            print(f"🌐 Fetching | {job.get('company')} | {job.get('title')}")
            description = fetch_description(job.get("url", ""))
            time.sleep(1)

        try:
            result = score_job(job, description)
            job["match_score"] = result["match_score"]
            job["ats_score"] = result["ats_score"]
            job["resume_used"] = result["resume_used"]
            job["reason"] = result["reason"]
            job["skills_gap"] = result["skills_gap"]
            job.pop("score", None)  # remove old score field
            scored += 1
            print(f"✅ match:{result['match_score']} ats:{result['ats_score']} | {job['company']} | {job['title']}")
        except Exception as e:
            print(f"❌ Failed: {job.get('company')} | {job.get('title')} | {e}")
            failed += 1

        time.sleep(0.5)  # rate limit Claude calls

    with open(jobs_path, "w") as f:
        json.dump(jobs, f, indent=2)

    print(f"\n{'='*50}")
    print(f"✅ Scored:        {scored}")
    print(f"⏭️  Skipped:       {skipped}")
    print(f"✔️  Already done:  {already_done}")
    print(f"❌ Failed:        {failed}")
    print(f"💰 API calls used: {scored}")

if __name__ == "__main__":
    main()
