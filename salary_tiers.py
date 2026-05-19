"""
salary_tiers.py — base salary + TC estimation logic for Jobify.

Two distinct metrics:
  - base_salary_min / base_salary_max:  pulled from ATS API when available, else None
  - tc_estimate_min / tc_estimate_max:  base × (1 + 0.15 bonus + equity_pct/100)

Equity is estimated using a company-tier × seniority-level matrix.
If base salary isn't in the ATS data, both metrics are left blank (no fabrication).
"""

# ─── Equity matrix: equity as % of base, by company tier × seniority level ──
# Row index: tier name → dict of level → pct
EQUITY_MATRIX = {
    "bigtech":      {"senior_pm": 40, "principal": 60, "director": 90, "vp": 130},
    "growth":       {"senior_pm": 35, "principal": 50, "director": 75, "vp": 110},
    "midcap":       {"senior_pm": 30, "principal": 45, "director": 65, "vp": 95},
    "late_private": {"senior_pm": 25, "principal": 35, "director": 50, "vp": 70},
    "midstage":     {"senior_pm": 20, "principal": 30, "director": 45, "vp": 60},
    "default":      {"senior_pm": 25, "principal": 35, "director": 50, "vp": 70},
}

BONUS_PCT = 15  # flat 15% target bonus

# ─── Company → tier mapping ──────────────────────────────────────────────────
# Lowercased lookup. Anything not listed defaults to "default" tier.
COMPANY_TIER = {
    # Big tech (mature public)
    "nvidia": "bigtech", "google": "bigtech", "meta": "bigtech",
    "apple": "bigtech", "microsoft": "bigtech", "amazon": "bigtech",
    "oracle": "bigtech", "adobe": "bigtech", "salesforce": "bigtech",
    "servicenow": "bigtech", "workday": "bigtech", "ibm": "bigtech",
    "cisco": "bigtech", "intel": "bigtech", "qualcomm": "bigtech",
    "broadcom": "bigtech", "hp": "bigtech", "hewlett packard enterprise": "bigtech",
    "ebay": "bigtech", "paypal": "bigtech", "intuit": "bigtech",
    "western digital": "bigtech", "marvell technology": "bigtech",
    "skyworks solutions": "bigtech", "autodesk": "bigtech",

    # Growth public
    "spotify": "growth", "atlassian": "growth", "snowflake": "growth",
    "hubspot": "growth", "pinterest": "growth", "reddit": "growth",
    "snap": "growth", "snapchat": "growth", "roblox": "growth",
    "coinbase": "growth", "roku": "growth", "twilio": "growth",
    "okta": "growth", "datadog": "growth", "dropbox": "growth",
    "elastic": "growth", "cloudera": "growth", "zoom": "growth",
    "netflix": "growth", "shopify": "growth", "square": "growth",
    "block": "growth", "duolingo": "growth", "zillow": "growth",

    # Mid-cap public
    "the trade desk": "midcap", "goodrx": "midcap", "fanduel": "midcap",
    "carvana": "midcap", "procore technologies": "midcap",
    "procore": "midcap", "robinhood": "midcap", "affirm": "midcap",
    "hims & hers": "midcap", "magnite": "midcap", "quantcast": "midcap",
    "liveramp": "midcap", "servicetitan": "midcap", "twitch": "midcap",
    "instacart": "midcap", "instacart ads": "midcap", "doubleverify": "midcap",
    "ias": "midcap", "criteo": "midcap", "applovin": "midcap",
    "appsflyer": "midcap", "branch": "midcap", "klaviyo": "midcap",
    "braze": "midcap", "siriusxm": "midcap", "yahoo": "midcap",
    "directv": "midcap", "the new york times": "midcap",
    "fandom": "midcap", "crunchyroll": "midcap",

    # Late-stage private
    "faire": "late_private", "ramp": "late_private", "mercury": "late_private",
    "calendly": "late_private", "ashby": "late_private", "clickup": "late_private",
    "drata": "late_private", "calm": "late_private", "cedar": "late_private",
    "houzz": "late_private", "attentive": "late_private",
    "iterable": "late_private", "rockerbox": "late_private",
    "fullstory": "late_private", "pindrop": "late_private",
    "movable ink": "late_private", "movableink": "late_private",
    "outbrain": "late_private", "taboola": "late_private",
    "openx": "late_private", "index exchange": "late_private",
    "sharethrough": "late_private", "sovrn": "late_private",
    "gumgum": "late_private", "zeta global": "late_private",
    "mediaocean": "late_private", "tinder": "late_private",
    "talkiatry": "late_private", "tebra": "late_private",
    "simplepractice": "late_private", "equip health": "late_private",
    "reformation": "late_private",

    # Mid-stage startup
    "madhive": "midstage", "raptive": "midstage", "acorns": "midstage",
    "creatoriq": "midstage", "altruist": "midstage", "bill.com": "midstage",
    "moneylion": "midstage", "greenlight": "midstage", "weedmaps": "midstage",
    "grindr": "midstage", "ro": "midstage", "olaplex": "midstage",
    "edmunds": "midstage", "tubi": "midstage",
    "innovid": "midstage", "adjust": "midstage", "cognitiv": "midstage",
    "inmobi": "midstage", "scopely": "midstage",
    "flock": "midstage", "flock safety": "midstage",
    "chartbeat": "midstage", "duckduckgo": "midstage",
    "zepz": "midstage", "honeycomb": "midstage", "onetrust": "midstage",
    "fossa": "midstage", "aura": "midstage", "crexi": "midstage",
    "justanswer": "midstage", "bitly": "midstage",
    "the trade desk": "midcap",  # explicit override stays
}


def get_company_tier(company: str) -> str:
    """Resolve a company string to a tier key. Defaults to 'default'."""
    if not company:
        return "default"
    c = company.lower().strip()
    # Strip "YC:" prefix
    if c.startswith("yc:"):
        c = c.split(":", 1)[1].strip()
    return COMPANY_TIER.get(c, "default")


# ─── Title → seniority level ─────────────────────────────────────────────────
def get_seniority_level(title: str) -> str:
    """Detect IC level from job title.
    Returns one of: 'senior_pm', 'principal', 'director', 'vp', or None.
    Returns None for plain 'product manager' (no equity multiplier applied)."""
    if not title:
        return None
    t = title.lower()

    # Order matters — check most senior first
    if any(kw in t for kw in [
        "vp ", "vp,", "vice president",
        "senior director", "sr. director", "sr director",
        "head of", "chief product"
    ]):
        return "vp"
    if "director" in t:
        return "director"
    if any(kw in t for kw in ["principal", "staff", "lead product manager"]):
        return "principal"
    if any(kw in t for kw in ["senior product manager", "sr. product manager",
                              "sr product manager", "senior pm", "sr pm",
                              "senior program manager"]):
        return "senior_pm"

    return None  # plain PM — no equity estimate


# ─── Compute TC range ────────────────────────────────────────────────────────
def compute_tc(base_min, base_max, company: str, title: str) -> dict:
    """
    Given a base salary range + company + title, return:
      {
        "bonus_pct": 15,
        "equity_pct": 30,
        "tc_estimate_min": 261000,
        "tc_estimate_max": 319000,
        "tier": "midcap",
        "level": "senior_pm",
        "confidence": "high"
      }

    If base_min/base_max are missing OR level is unknown, returns dict with
    all values None except tier/level for diagnostic purposes.
    """
    out = {
        "bonus_pct": None,
        "equity_pct": None,
        "tc_estimate_min": None,
        "tc_estimate_max": None,
        "tier": get_company_tier(company),
        "level": get_seniority_level(title),
        "confidence": None,
    }

    # Need base to compute TC
    if base_min is None and base_max is None:
        return out

    # If only one bound given, use it for both
    if base_min is None:
        base_min = base_max
    if base_max is None:
        base_max = base_min

    # If level unknown, we can't apply equity — just compute bonus
    if out["level"] is None:
        equity_pct = 0
    else:
        equity_pct = EQUITY_MATRIX[out["tier"]][out["level"]]

    multiplier = 1 + (BONUS_PCT / 100) + (equity_pct / 100)
    out["bonus_pct"] = BONUS_PCT
    out["equity_pct"] = equity_pct
    out["tc_estimate_min"] = round(base_min * multiplier)
    out["tc_estimate_max"] = round(base_max * multiplier)
    out["confidence"] = "high" if out["level"] else "medium"
    return out


# ─── Self-test ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    tests = [
        ("NVIDIA", "Senior Product Manager, AI Infrastructure", 200000, 200000),
        ("Magnite", "Director of Product, SSP", 220000, 240000),
        ("Madhive", "Senior Product Manager, CTV", 180000, 200000),
        ("Acorns", "Director of Product", 200000, 220000),
        ("HubSpot", "Principal Product Manager, Growth", 190000, 230000),
        ("Reddit", "VP of Product, Ads", 280000, 320000),
        ("UnknownCo", "Product Manager", 150000, 170000),
        ("Madhive", "Product Manager", 140000, 160000),  # plain PM → no equity
        ("Anywhere", "Director of Product", None, None),  # no base
    ]
    for company, title, lo, hi in tests:
        r = compute_tc(lo, hi, company, title)
        base_str = f"${lo/1000:.0f}K–${hi/1000:.0f}K" if lo else "—"
        if r["tc_estimate_min"]:
            tc_str = f"${r['tc_estimate_min']/1000:.0f}K–${r['tc_estimate_max']/1000:.0f}K"
        else:
            tc_str = "—"
        print(f"{company:15s} | {title[:45]:45s} | base={base_str:18s} → TC={tc_str:18s} "
              f"(tier={r['tier']}, lvl={r['level']}, eq={r['equity_pct']}%)")
