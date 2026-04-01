# Jobify — AI Job Tracker

A personal job search dashboard that automatically scrapes PM, data, and ads roles from top companies daily and lets you track your applications in one place.

## Live Dashboard

👉 [ai-products-builder.github.io/jobify](https://ai-products-builder.github.io/jobify)

## What It Does

- Scrapes job listings daily from Microsoft, Amazon, Netflix, Reddit, Roku, Unity, Fox, Tubi, The Trade Desk, Dice, Built In, We Work Remotely, and more
- Filters for Product Manager, Data, Ads, and Analytics roles in Atlanta, Georgia, Remote, and Southern California
- Tracks your application status (New → Applied → Interview → Offer)
- Generates cover letters and interview prep questions using Claude AI
- Updates automatically every morning at 8am via GitHub Actions

## Files

| File | Purpose |
|---|---|
| `index.html` | Dashboard UI — hosted on GitHub Pages |
| `scraper.py` | Fetches jobs from all company APIs |
| `jobs.json` | Job database — auto-updated daily |
| `.github/workflows/scraper.yml` | GitHub Actions daily schedule |
| `requirements.txt` | Python dependencies |

## Setup

**1. Clone the repo**
```bash
git clone https://github.com/ai-products-builder/jobify.git
cd jobify
```

**2. Install dependencies**
```bash
pip3 install -r requirements.txt
```

**3. Run the scraper manually**
```bash
python3 scraper.py
```

**4. View locally**
```bash
python3 -m http.server 8080
```
Open [http://localhost:8080](http://localhost:8080)

## Customization

All filters are defined at the top of `scraper.py`:

```python
# What job titles to include
INCLUDE_KEYWORDS = ["product manager", "data", "advertising", ...]

# What job titles to exclude
EXCLUDE_TITLES = ["engineer", "scientist", "recruiter", ...]

# Where to look
LOCATION_KEYWORDS = ["atlanta", "georgia", "remote", "los angeles", ...]

# What to search for on each company API
SEARCH_QUERIES = ["product manager", "data", "advertising", "analytics"]
```

Edit these lists to change what jobs get pulled — no other changes needed.

## Automation

The scraper runs daily at 8am Atlanta time via GitHub Actions.
To trigger it manually: **GitHub → Actions → Daily Job Scraper → Run workflow**

## Adding Your Anthropic API Key

The dashboard uses Claude AI for cover letters and interview prep.
Enter your API key at [console.anthropic.com](https://console.anthropic.com) and paste it when prompted in the dashboard.
