"""
job_dedup.py — shared deduplication logic for Jobify.

Used by:
  - scraper.py at write-time (prevent new dupes)
  - cleanup_jobs.py at maintenance-time (merge existing dupes in jobs.json)

The key insight: two job entries are duplicates if they share a
canonical (company, title, location) tuple after normalization.

Canonical-key rules:
  - Company: lowercase, strip punctuation, collapse "Inc/Corp/LLC/Ltd",
    strip "YC:" prefix from YC jobs so they match parent listing.
  - Title: lowercase, strip seniority noise ("senior", "sr.", "ii", "iii",
    "lead", "staff", "principal"), strip punctuation, collapse whitespace.
    NOTE: we keep the original title in the kept record — we only strip
    for matching purposes.
  - Location: lowercase, split on "/" and ",", sort + dedupe city tokens,
    so "NYC / Remote" matches "Remote / NYC".

Tie-breaking when merging (which entry to KEEP):
  1. Has a real ats_score (>0 and not flagged as default) → wins
  2. Has a real match_score (>0) → wins
  3. Has a description (non-empty) → wins
  4. Newer found_date → wins
  5. Lexically smaller id (deterministic tiebreak)
"""
import re

# ─── Normalization helpers ───────────────────────────────────────────────────
_COMPANY_NOISE = re.compile(r"\b(inc|llc|corp|corporation|ltd|limited|co|the)\b\.?", re.I)
_PUNCT = re.compile(r"[^\w\s]")
_WS = re.compile(r"\s+")
_TITLE_NOISE = re.compile(
    r"\b(senior|sr|jr|junior|lead|staff|principal|"
    r"i|ii|iii|iv|v|"
    r"vice president|vp|head|chief)\b\.?",
    re.I,
)


def normalize_company(company: str) -> str:
    if not company:
        return ""
    c = company.lower().strip()
    # Strip "YC:" prefix so YC jobs match parent company entries
    c = re.sub(r"^yc:\s*", "", c)
    c = _PUNCT.sub(" ", c)
    c = _COMPANY_NOISE.sub("", c)
    c = _WS.sub(" ", c).strip()
    return c


def normalize_title(title: str) -> str:
    if not title:
        return ""
    t = title.lower().strip()
    t = _PUNCT.sub(" ", t)
    # Expand common abbreviations BEFORE stripping noise (otherwise
    # "PM" and "Product Manager" never align).
    t = re.sub(r"\bpm\b", "product manager", t)
    t = re.sub(r"\btpm\b", "technical product manager", t)
    t = re.sub(r"\bgpm\b", "group product manager", t)
    t = re.sub(r"\bsdm\b", "software development manager", t)
    t = _TITLE_NOISE.sub("", t)
    t = _WS.sub(" ", t).strip()
    return t


def normalize_location(location: str) -> str:
    """Split on / and , — sort + dedupe city tokens — return canonical join."""
    if not location:
        return ""
    parts = re.split(r"[,/]", location.lower())
    # Strip state suffixes that vary noisily: "los angeles ca" vs "los angeles"
    cleaned = []
    for p in parts:
        p = _PUNCT.sub(" ", p)
        p = _WS.sub(" ", p).strip()
        # collapse common state codes that flap (e.g. "ny" vs "new york")
        p = re.sub(r"\b(ca|ny|tx|wa|ga|fl|il|ma|or|co|az|nc|va|md|nj|pa|oh|mi|nv|ut)\b$", "", p).strip()
        if p:
            cleaned.append(p)
    return "|".join(sorted(set(cleaned)))


def canonical_key(job: dict) -> str:
    """Generate the dedup key for a job entry."""
    c = normalize_company(job.get("company", ""))
    t = normalize_title(job.get("title", ""))
    l = normalize_location(job.get("location", ""))
    return f"{c}::{t}::{l}"


# ─── Tie-breaking ────────────────────────────────────────────────────────────
def _score_richness(job: dict) -> tuple:
    """Return a comparable tuple — higher means "keep this one".
    
    Order (descending priority):
      1. has real ats_score (>0)
      2. has real match_score (>0)
      3. has a description (non-empty)
      4. has found_date (newer wins — ISO format sorts naturally)
      5. has shorter id (just for determinism)
    """
    ats = float(job.get("ats_score") or 0)
    match = float(job.get("match_score") or 0)
    desc_len = len(job.get("description", "") or "")
    found = job.get("found_date", "") or ""
    jid = job.get("id", "") or ""
    return (
        1 if ats > 0 else 0,
        1 if match > 0 else 0,
        1 if desc_len > 50 else 0,  # >50 char description counts as "real"
        found,
        -len(jid),  # shorter id wins on final tie
    )


def pick_winner(a: dict, b: dict) -> tuple:
    """Given two duplicate jobs, return (winner, loser) by richness."""
    if _score_richness(a) >= _score_richness(b):
        return a, b
    return b, a


def merge_into_winner(winner: dict, loser: dict) -> dict:
    """Fill in any missing fields on the winner from the loser.
    Preserves the winner's id (the canonical record).
    """
    merged = dict(winner)
    for k, v in loser.items():
        if k == "id":
            continue
        # Only overwrite if winner's field is empty/missing/zero
        existing = merged.get(k)
        if existing in (None, "", 0, 0.0, []):
            merged[k] = v
        elif k == "description":
            # Take the longer description
            if len(str(v or "")) > len(str(existing or "")):
                merged[k] = v
    return merged


# ─── Bulk dedup ──────────────────────────────────────────────────────────────
def _location_tokens(location: str) -> set:
    """Return the set of city tokens (used for overlap checks)."""
    norm = normalize_location(location)
    if not norm:
        return set()
    return set(norm.split("|"))


def _locations_overlap(a: str, b: str) -> bool:
    """Two locations 'overlap' if they share at least one city token,
    OR if both are empty, OR if either is empty (be lenient)."""
    ta, tb = _location_tokens(a), _location_tokens(b)
    if not ta or not tb:
        return True  # missing-location side: treat as match
    return bool(ta & tb)


def _company_title_key(job: dict) -> str:
    """Coarse key — company + title only, ignoring location."""
    c = normalize_company(job.get("company", ""))
    t = normalize_title(job.get("title", ""))
    return f"{c}::{t}"


def dedup_job_list(jobs: list) -> tuple:
    """
    Dedup a LIST of job dicts (used by scraper.py before saving).

    Two-pass approach:
      1. Bucket by (company, title) — coarse key
      2. Within each bucket, merge entries whose locations overlap

    Returns: (deduped_list, num_dupes_collapsed)
    """
    # Pass 1: bucket by coarse key
    buckets = {}
    for job in jobs:
        ck = _company_title_key(job)
        if not ck or ck == "::":
            continue
        buckets.setdefault(ck, []).append(job)

    # Pass 2: within each bucket, merge overlapping-location entries
    out = []
    dupes_collapsed = 0
    for ck, group in buckets.items():
        if len(group) == 1:
            out.append(group[0])
            continue
        # Greedy merge: walk the group, merge anything that overlaps with kept
        kept = []
        for job in group:
            merged_into = None
            for i, k in enumerate(kept):
                if _locations_overlap(job.get("location", ""), k.get("location", "")):
                    winner, loser = pick_winner(k, job)
                    kept[i] = merge_into_winner(winner, loser)
                    merged_into = i
                    dupes_collapsed += 1
                    break
            if merged_into is None:
                kept.append(job)
        out.extend(kept)
    return out, dupes_collapsed


def dedup_jobs_dict(jobs_dict: dict) -> tuple:
    """
    Dedup a DICT of jobs keyed by id (used by cleanup_jobs.py).

    Returns: (deduped_dict, removed_ids, merges_log)
      - deduped_dict: the cleaned jobs_dict (keyed by the WINNER's id)
      - removed_ids: list of ids that were dropped
      - merges_log: list of (winner_id, loser_id, company, title) tuples
    """
    # Materialize jobs with their id so we can track who-merged-into-who
    jobs_with_id = []
    for jid in sorted(jobs_dict.keys()):
        job = jobs_dict[jid]
        if "id" not in job:
            job = {**job, "id": jid}
        jobs_with_id.append(job)

    # Pass 1: bucket
    buckets = {}
    for job in jobs_with_id:
        ck = _company_title_key(job)
        if not ck or ck == "::":
            continue
        buckets.setdefault(ck, []).append(job)

    out = {}
    removed_ids = []
    merges_log = []

    for ck, group in buckets.items():
        if len(group) == 1:
            out[group[0]["id"]] = group[0]
            continue
        kept = []
        for job in group:
            merged_into = None
            for i, k in enumerate(kept):
                if _locations_overlap(job.get("location", ""), k.get("location", "")):
                    winner, loser = pick_winner(k, job)
                    kept[i] = merge_into_winner(winner, loser)
                    removed_ids.append(loser["id"])
                    merges_log.append((
                        winner["id"],
                        loser["id"],
                        winner.get("company", ""),
                        winner.get("title", ""),
                    ))
                    merged_into = i
                    break
            if merged_into is None:
                kept.append(job)
        for k in kept:
            out[k["id"]] = k

    return out, removed_ids, merges_log


# ─── Self-test ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    samples = [
        {"id": "a1", "company": "HubSpot", "title": "Senior Product Manager, Ads",
         "location": "Remote", "description": "long description here ..." * 10,
         "match_score": 80, "ats_score": 70, "found_date": "2026-05-01T10:00:00"},
        {"id": "b2", "company": "HubSpot Inc.", "title": "Sr. Product Manager, Ads",
         "location": "Remote / Atlanta", "description": "",
         "match_score": 0, "ats_score": 0, "found_date": "2026-05-15T10:00:00"},
        {"id": "c3", "company": "HubSpot", "title": "Product Manager — Growth",
         "location": "Atlanta, GA", "found_date": "2026-05-10T10:00:00"},
        {"id": "d4", "company": "HubSpot", "title": "Senior Product Manager, Ads",
         "location": "New York", "found_date": "2026-05-12T10:00:00"},
    ]
    deduped, n = dedup_job_list(samples)
    print(f"Input: {len(samples)} jobs, deduped to {len(deduped)} ({n} collapsed)")
    for j in deduped:
        print(f"  {j['id']}: {j['company']} — {j['title']} @ {j['location']}")
    # Expected: a1 + b2 collapse (Remote overlaps with Remote/Atlanta) -> a1 wins (has scores)
    #           d4 stays separate (New York doesn't overlap with Remote/Atlanta) — actually a1's
    #             location became "Remote" + b2's "Remote/Atlanta" — d4 NY doesn't overlap
    #             with merged set. WAIT: a1+b2 merge keeps a1.location='Remote', so d4 (NY)
    #             would not match. Correct.
    #           c3 stays separate (different title key: 'growth' vs 'ads')
