import json
import os
import anthropic

RESUME_ADS = os.environ["RESUME_ADS"]
RESUME_DATA = os.environ["RESUME_DATA"]

client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

def score_job(job: dict) -> dict:
    title = job.get("title", "")
    company = job.get("company", "")
    description = job.get("description", "")[:3000]  # cap to save tokens

    # Pick the better-fit resume based on job title keywords
    ads_keywords = ["adtech", "ads", "programmatic", "rtb", "monetization",
                    "attribution", "measurement", "publisher", "dsp", "ssp", "sdk"]
    title_lower = title.lower()
    resume_label = "ADS" if any(k in title_lower for k in ads_keywords) else "DATA"
    resume_text = RESUME_ADS if resume_label == "ADS" else RESUME_DATA

    prompt = f"""You are an expert technical recruiter evaluating job fit.

RESUME ({resume_label}):
{resume_text}

JOB:
Title: {title}
Company: {company}
Description: {description}

Score this job from 0-100 based on how well the resume matches. Consider:
- Seniority level match (Director/Lead roles = higher score)
- Technical skill overlap (data platforms, adtech, ML, analytics)
- Industry relevance (adtech, data, SaaS)
- Location fit (LA, Remote, Atlanta preferred)

Respond ONLY with valid JSON, no markdown:
{{
  "score": <integer 0-100>,
  "resume_used": "{resume_label}",
  "reason": "<one sentence why this score>",
  "skills_gap": "<one sentence on what's missing or NA if strong match>"
}}"""

    message = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = message.content[0].text.strip()
    result = json.loads(raw)
    return result

def main():
    jobs_path = "docs/jobs.json"
    with open(jobs_path) as f:
        jobs = json.load(f)

    scored = 0
    for job in jobs:
        # Only score jobs that haven't been scored yet
        if job.get("score") is not None:
            continue
        try:
            result = score_job(job)
            job["score"] = result["score"]
            job["resume_used"] = result["resume_used"]
            job["reason"] = result["reason"]
            job["skills_gap"] = result["skills_gap"]
            scored += 1
            print(f"✅ {job['company']} | {job['title']} → {result['score']}")
        except Exception as e:
            print(f"❌ Failed: {job.get('company')} | {e}")
            job["score"] = 50  # fallback neutral score

    with open(jobs_path, "w") as f:
        json.dump(jobs, f, indent=2)

    print(f"\nDone. Scored {scored} new jobs.")

if __name__ == "__main__":
    main()
