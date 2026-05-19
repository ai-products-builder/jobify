"""
cleanup_jobs.py — one-time (or periodic) dedup pass for jobs.json.

Finds duplicate job entries from past scraper runs (different scrapers hitting
the same role, slug renames, etc.) and merges them using job_dedup.

Usage:
    python3 scripts/cleanup_jobs.py             # dry-run (default, safe)
    python3 scripts/cleanup_jobs.py --apply     # actually write changes
    python3 scripts/cleanup_jobs.py --apply --backup  # write + save backup

Dry-run output shows exactly which dupes will be collapsed, grouped by
company, so you can spot-check before running with --apply.

Merge rules (from job_dedup.pick_winner):
  1. Entry with real ats_score (>0) beats unscored
  2. Then entry with real match_score (>0)
  3. Then entry with a non-empty description (>50 chars)
  4. Then newer found_date
  5. Then shorter id (deterministic)

The winner inherits any missing fields from the loser (description, location
variants, notes, etc.) — nothing useful is dropped.
"""
import json
import os
import sys
import shutil
from datetime import datetime
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from job_dedup import dedup_jobs_dict

JOBS_FILE = "jobs.json"


def load_jobs(path):
    if not os.path.exists(path):
        print(f"❌ {path} not found — run from repo root")
        sys.exit(1)
    with open(path) as f:
        return json.load(f)


def print_merges_report(merges, jobs_before, jobs_after):
    """Print a readable dupes-by-company report."""
    if not merges:
        print("✅ No duplicates found — jobs.json is clean.")
        return

    # Group by company for readability
    by_company = defaultdict(list)
    for winner_id, loser_id, company, title in merges:
        by_company[company].append((winner_id, loser_id, title))

    print(f"\n{'='*70}")
    print(f"DUPLICATE MERGES ({len(merges)} total across {len(by_company)} companies)")
    print(f"{'='*70}\n")

    for company in sorted(by_company.keys()):
        merges_here = by_company[company]
        print(f"📦 {company} ({len(merges_here)} merges)")
        for winner_id, loser_id, title in merges_here[:10]:
            w = jobs_before.get(winner_id, {})
            l = jobs_before.get(loser_id, {})
            print(f"   KEEP:  {winner_id[:40]:40s} | {w.get('title', '?')[:50]}")
            print(f"          → @ {w.get('location', '?')[:50]}, score={w.get('ats_score', 0)}")
            print(f"   DROP:  {loser_id[:40]:40s} | {l.get('title', '?')[:50]}")
            print(f"          → @ {l.get('location', '?')[:50]}, score={l.get('ats_score', 0)}")
            print()
        if len(merges_here) > 10:
            print(f"   ... and {len(merges_here) - 10} more for {company}\n")

    print(f"{'='*70}")
    print(f"SUMMARY")
    print(f"{'='*70}")
    print(f"  Jobs before: {len(jobs_before)}")
    print(f"  Jobs after:  {len(jobs_after)}")
    print(f"  Removed:     {len(jobs_before) - len(jobs_after)}")
    print(f"  Reduction:   {100 * (len(jobs_before) - len(jobs_after)) / max(len(jobs_before), 1):.1f}%")


def main():
    args = sys.argv[1:]
    apply = "--apply" in args
    backup = "--backup" in args

    # Locate jobs.json — try both repo-root and ../ from scripts/
    path = JOBS_FILE
    if not os.path.exists(path):
        alt = os.path.join("..", JOBS_FILE)
        if os.path.exists(alt):
            path = alt

    print(f"📂 Reading {path}...")
    jobs_before = load_jobs(path)
    print(f"   {len(jobs_before)} jobs loaded\n")

    print("🔎 Running dedup...")
    jobs_after, removed_ids, merges = dedup_jobs_dict(jobs_before)
    print_merges_report(merges, jobs_before, jobs_after)

    if not merges:
        return

    if not apply:
        print("\n🟡 DRY-RUN MODE — no changes written.")
        print("    Re-run with --apply to commit the changes.")
        print("    Re-run with --apply --backup to also save jobs.json.bak first.")
        return

    if backup:
        backup_path = f"{path}.bak.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        shutil.copy(path, backup_path)
        print(f"💾 Backup saved: {backup_path}")

    with open(path, "w") as f:
        json.dump(jobs_after, f, indent=2)
    print(f"✅ Wrote {len(jobs_after)} deduped jobs to {path}")


if __name__ == "__main__":
    main()
