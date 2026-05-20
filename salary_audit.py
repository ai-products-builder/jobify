#!/usr/bin/env python3
"""
salary_audit.py — Proactively audit salary coverage in jobs.json.

Purpose: distinguish "company doesn't disclose" (normal, expected) from
"our extractor is broken for a whole ATS" (a real bug we should fix).

It infers each job's ATS from its id prefix (e.g. 'sr_', 'ttd_', 'msft_',
'<board>_<num>' for Greenhouse) and reports:
  - total jobs per ATS
  - how many have a base salary
  - coverage %
  - a RED FLAG when an entire ATS bucket is 0% (likely a bug, not disclosure)

Run locally:  python3 salary_audit.py
Or in CI:     add a workflow step that runs it and prints the summary.
"""

import json
import re
import sys
from collections import defaultdict

JOBS_FILE = "jobs.json"

# Map id-prefix → ATS name. Order matters: check specific prefixes first.
PREFIX_TO_ATS = [
    ("msft_",        "Microsoft (PCSX)"),
    ("amzn_",        "Amazon"),
    ("netflix_",     "Netflix (Eightfold)"),
    ("ttd_",         "Trade Desk (Eightfold)"),
    ("nvidia_",      "NVIDIA (Eightfold)"),
    ("sr_",          "SmartRecruiters"),
    ("servicenow_",  "ServiceNow (SmartRecruiters)"),
    ("salesforce_",  "Salesforce (Workday)"),
    ("snap_",        "Snap (Workday)"),
    ("capitalone_",  "Capital One (Workday)"),
    ("mastercard_",  "Mastercard (Workday)"),
    ("visa_",        "Visa (Workday)"),
    ("walmart_",     "Walmart (Workday)"),
    ("nvidiawd_",    "NVIDIA (Workday)"),
    ("qualcomm_",    "Qualcomm (Workday)"),
    ("deloitte_",    "Deloitte (Avature)"),
    ("intuit_",      "Intuit (Phenom)"),
    ("hibob_",       "HiBob"),
    ("ebay_",        "eBay (Phenom widgets)"),
    ("yc_",          "Y Combinator"),
    ("wwr_",         "We Work Remotely"),
    ("dice_",        "Dice"),
    ("builtin_",     "Built In"),
]

# Workday generic prefixes use the company prefix passed in; Avature/Ashby/Lever
# use the board slug as prefix. We detect those by the base_salary_source and a
# fallback bucket.
KNOWN_WORKDAY_PREFIXES = {
    "snap", "capitalone", "mastercard", "visa", "walmart", "nvidiawd",
    "qualcomm", "salesforce",
}


def classify_ats(job):
    """Best-effort ATS classification from job id + source hints."""
    jid = job.get("id", "")
    for prefix, name in PREFIX_TO_ATS:
        if jid.startswith(prefix):
            return name
    # Greenhouse ids look like '<board>_<numericid>'; Ashby/Lever use slug too.
    # Use base_salary_source + url to disambiguate where possible.
    url = (job.get("url") or "").lower()
    if "greenhouse.io" in url or "boards.greenhouse" in url:
        return "Greenhouse"
    if "lever.co" in url:
        return "Lever"
    if "ashbyhq" in url or "jobs.ashby" in url:
        return "Ashby"
    if "myworkdayjobs" in url:
        return "Workday (generic)"
    if "avature" in url:
        return "Avature"
    if "smartrecruiters" in url:
        return "SmartRecruiters"
    # Fallback: bucket by the leading token of the id
    head = jid.split("_")[0] if "_" in jid else "unknown"
    return f"other:{head}"


def has_salary(job):
    return job.get("base_salary_min") is not None


def main():
    try:
        with open(JOBS_FILE) as f:
            jobs = json.load(f)
    except FileNotFoundError:
        print(f"ERROR: {JOBS_FILE} not found. Run from repo root.")
        sys.exit(1)

    if isinstance(jobs, dict):
        jobs = list(jobs.values())

    by_ats = defaultdict(lambda: {"total": 0, "with_salary": 0, "examples": []})
    by_company = defaultdict(lambda: {"total": 0, "with_salary": 0})

    for j in jobs:
        ats = classify_ats(j)
        company = j.get("company", "?")
        by_ats[ats]["total"] += 1
        by_company[company]["total"] += 1
        if has_salary(j):
            by_ats[ats]["with_salary"] += 1
            by_company[company]["with_salary"] += 1
            if len(by_ats[ats]["examples"]) < 2:
                by_ats[ats]["examples"].append(
                    f"{company}: ${j['base_salary_min']:,}-${j.get('base_salary_max', 0):,}"
                )

    total = len(jobs)
    total_sal = sum(1 for j in jobs if has_salary(j))

    print("=" * 64)
    print(f"SALARY COVERAGE AUDIT — {total} jobs, "
          f"{total_sal} with salary ({100*total_sal//max(total,1)}%)")
    print("=" * 64)
    print()
    print("BY ATS (sorted by coverage):")
    print(f"  {'ATS':<32} {'jobs':>6} {'salary':>7} {'cov':>5}")
    print(f"  {'-'*32} {'-'*6} {'-'*7} {'-'*5}")

    rows = sorted(by_ats.items(), key=lambda kv: kv[1]["with_salary"]/max(kv[1]["total"],1))
    red_flags = []
    for ats, d in rows:
        cov = 100 * d["with_salary"] // max(d["total"], 1)
        flag = ""
        # RED FLAG heuristic: a sizeable ATS bucket (10+ jobs) with 0% coverage
        # is suspicious — most ATSes have at least SOME CA/NY disclosures.
        if d["total"] >= 10 and d["with_salary"] == 0:
            flag = "  ⚠️  0% — possible extractor bug"
            red_flags.append(ats)
        print(f"  {ats:<32} {d['total']:>6} {d['with_salary']:>7} {cov:>4}%{flag}")

    print()
    if red_flags:
        print("⚠️  RED FLAGS — these ATSes have 10+ jobs but ZERO salary.")
        print("    Either the extractor is broken OR none of these companies")
        print("    disclose. Check one job's raw API response to confirm:")
        for ats in red_flags:
            print(f"      - {ats}")
    else:
        print("✓ No red flags. Every ATS with 10+ jobs has at least some salary.")
        print("  Blanks elsewhere are normal non-disclosure, not bugs.")

    print()
    print("TOP COMPANIES BY SALARY COVERAGE (min 5 jobs):")
    comp_rows = [(c, d) for c, d in by_company.items() if d["total"] >= 5]
    comp_rows.sort(key=lambda kv: kv[1]["with_salary"]/max(kv[1]["total"],1), reverse=True)
    for c, d in comp_rows[:15]:
        cov = 100 * d["with_salary"] // max(d["total"], 1)
        print(f"  {c:<28} {d['with_salary']:>3}/{d['total']:<3} ({cov}%)")

    print()
    print("SAMPLE EXTRACTED SALARIES (proof extraction works where present):")
    shown = 0
    for ats, d in by_ats.items():
        for ex in d["examples"]:
            print(f"  [{ats}] {ex}")
            shown += 1
            if shown >= 10:
                break
        if shown >= 10:
            break
    if shown == 0:
        print("  (none found — if total_sal > 0 this is a classification issue)")


if __name__ == "__main__":
    main()
