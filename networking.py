"""
networking.py — Weekly Networking Targets for The Daily Brief
Runs every Monday. Searches for finance professionals with Utah/SEO connections.
Populates three tiers: Core Front Office, Extended Finance, Consulting/Fintech/Gov/Misc.
Uses Google Custom Search API (free tier: 100 queries/day).
Deduplicates against seen_contacts.json committed in repo root.
"""

import os
import json
import urllib.request
import urllib.parse
import re
from datetime import datetime

# ── Config ───────────────────────────────────────────────────────────────
GOOGLE_CSE_KEY = os.environ.get("GOOGLE_CSE_API_KEY", "")
GOOGLE_CSE_CX  = os.environ.get("GOOGLE_CSE_CX", "")       # Custom Search Engine ID
SEEN_FILE      = "seen_contacts.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}

# ── Week rotation logic ──────────────────────────────────────────────────
# Cycles through role categories each Monday so the list stays fresh

TIER1_CATEGORIES = [
    {
        "label": "Investment Banking",
        "keywords": ["investment banking", "investment banker", "M&A", "mergers acquisitions", "leveraged finance"],
    },
    {
        "label": "Private Equity & Growth Equity",
        "keywords": ["private equity", "growth equity", "PE associate", "buyout", "LBO"],
    },
    {
        "label": "Hedge Funds & Trading",
        "keywords": ["hedge fund", "sales trading", "macro trader", "equity trader", "fixed income", "portfolio manager"],
    },
    {
        "label": "Asset Management & Equity Research",
        "keywords": ["asset management", "equity research", "investment analyst", "portfolio manager", "buy side analyst"],
    },
    {
        "label": "Corporate Development & Restructuring",
        "keywords": ["corporate development", "corp dev", "restructuring", "distressed", "M&A analyst"],
    },
    {
        "label": "Venture Capital & Credit",
        "keywords": ["venture capital", "VC", "direct lending", "credit analyst", "real estate private equity"],
    },
]

TIER2_CATEGORIES = [
    {
        "label": "Strategic Finance & IR",
        "keywords": ["strategic finance", "investor relations", "capital markets", "treasury", "FP&A"],
    },
    {
        "label": "Investment Management & Wealth",
        "keywords": ["investment management", "wealth management", "private banking", "commercial banking"],
    },
]

TIER3_CATEGORIES = [
    {
        "label": "Consulting & Strategy",
        "keywords": ["management consulting", "strategy consulting", "McKinsey", "BCG", "Bain", "corporate strategy"],
    },
    {
        "label": "Fintech & Technology",
        "keywords": ["fintech", "financial technology", "payments", "trading technology", "financial software"],
    },
    {
        "label": "Government & Regulation",
        "keywords": ["SEC", "Federal Reserve", "Treasury", "CFPB", "DOJ", "financial regulation", "economic policy"],
    },
]

# Connection source definitions — what to search for and how to label it
CONNECTION_SOURCES = [
    {
        "id":      "uofu",
        "label":   "University of Utah Alumni",
        "queries": [
            '"University of Utah"',
            '"David Eccles School of Business"',
            '"University of Utah" "Eccles"',
        ],
    },
    {
        "id":      "byu",
        "label":   "BYU / Utah Schools Connection",
        "queries": [
            '"Brigham Young University" OR "BYU Marriott"',
            '"Utah State University"',
            '"Westminster University" Salt Lake',
        ],
    },
    {
        "id":      "seo",
        "label":   "SEO Alumni",
        "queries": [
            '"SEO" "Sponsors for Educational Opportunity"',
            '"SEO Career" OR "SEO Alternative Investments"',
        ],
    },
]


# ── Deduplication ────────────────────────────────────────────────────────

def load_seen_contacts():
    try:
        with open(SEEN_FILE, "r") as f:
            data = json.load(f)
        return set(data.get("seen", []))
    except Exception:
        return set()


def save_seen_contacts(seen: set):
    # Keep last 500 to prevent unbounded growth
    seen_list = list(seen)[-500:]
    try:
        with open(SEEN_FILE, "w") as f:
            json.dump({"seen": seen_list, "last_updated": datetime.utcnow().isoformat()}, f, indent=2)
        print("  [networking] seen_contacts.json saved")
    except Exception as ex:
        print(f"  [networking] save error: {ex}")


def make_contact_key(name: str, firm: str) -> str:
    """Stable dedup key: lowercase name + first word of firm."""
    n = re.sub(r'[^a-z0-9]', '', name.lower())
    f = re.sub(r'[^a-z0-9]', '', firm.lower().split()[0] if firm.split() else "")
    return f"{n}:{f}"


# ── Google Custom Search ─────────────────────────────────────────────────

def google_cse_search(query: str, num: int = 5) -> list:
    """
    Searches Google CSE for LinkedIn profiles matching query.
    Returns raw result items.
    """
    if not GOOGLE_CSE_KEY or not GOOGLE_CSE_CX:
        return []
    try:
        params = urllib.parse.urlencode({
            "key":   GOOGLE_CSE_KEY,
            "cx":    GOOGLE_CSE_CX,
            "q":     query,
            "num":   min(num, 10),
            "siteSearch": "linkedin.com/in/",
        })
        req = urllib.request.Request(
            f"https://www.googleapis.com/customsearch/v1?{params}",
            headers=HEADERS,
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode())
        return data.get("items", [])
    except Exception as ex:
        print(f"  [CSE] query failed: {ex}")
        return []


def parse_linkedin_snippet(item: dict, connection_label: str, tier: int) -> dict | None:
    """
    Parses a Google CSE result item into a contact dict.
    Returns None if we can't extract useful info.
    """
    title   = item.get("title", "")
    snippet = item.get("snippet", "")
    url     = item.get("link", "")

    # Title format is usually: "First Last - Role at Firm | LinkedIn"
    # or "First Last - Role | LinkedIn"
    name, role, firm = "", "", ""

    # Strip " | LinkedIn" suffix
    clean_title = re.sub(r'\s*\|\s*LinkedIn.*$', '', title, flags=re.IGNORECASE).strip()
    clean_title = re.sub(r'\s*-\s*LinkedIn.*$', '', clean_title, flags=re.IGNORECASE).strip()

    # Split on " - " to get name vs role/firm
    parts = re.split(r'\s+[-–]\s+', clean_title, maxsplit=2)
    if len(parts) >= 2:
        name = parts[0].strip()
        role_firm = parts[1].strip()
        # Split role from firm on " at " or " @ "
        at_match = re.split(r'\s+(?:at|@)\s+', role_firm, maxsplit=1, flags=re.IGNORECASE)
        if len(at_match) == 2:
            role = at_match[0].strip()
            firm = at_match[1].strip()
        else:
            role = role_firm
            firm = ""
    elif len(parts) == 1:
        name = parts[0].strip()

    # Try to extract city from snippet
    city = ""
    city_match = re.search(
        r'(?:Location:|·)\s*([A-Z][a-zA-Z\s]+(?:,\s*[A-Z]{2})?)',
        snippet
    )
    if city_match:
        city = city_match.group(1).strip()

    # Validate — need at least a name and either role or firm
    if not name or len(name.split()) < 2:
        return None
    if not role and not firm:
        return None

    # Clean up LinkedIn URL to profile only
    profile_url = url
    ln_match = re.search(r'(https?://(?:www\.)?linkedin\.com/in/[^/?&#]+)', url)
    if ln_match:
        profile_url = ln_match.group(1)

    return {
        "name":       name,
        "role":       role,
        "firm":       firm,
        "city":       city,
        "url":        profile_url,
        "connection": connection_label,
        "tier":       tier,
    }


# ── Core search logic ────────────────────────────────────────────────────

def get_week_index() -> int:
    """Returns week number mod number of tier1 categories for rotation."""
    return datetime.utcnow().isocalendar()[1]  # ISO week number


def build_queries(role_keywords: list, connection_queries: list) -> list:
    """
    Builds targeted Google CSE queries combining role keywords
    with connection source queries.
    """
    queries = []
    for conn_q in connection_queries:
        # Use first role keyword per connection source to stay within quota
        kw = role_keywords[0]
        queries.append(f'{conn_q} "{kw}"')
    return queries


def search_tier(
    categories: list,
    tier_num: int,
    target_count: int,
    seen: set,
    week_idx: int,
) -> list:
    """
    Searches one tier's categories and returns up to target_count
    deduplicated contacts.
    """
    contacts = []
    # Rotate which category we focus on this week
    cat = categories[week_idx % len(categories)]
    print(f"  [networking T{tier_num}] Category: {cat['label']}")

    for source in CONNECTION_SOURCES:
        if len(contacts) >= target_count:
            break
        queries = build_queries(cat["keywords"], source["queries"])
        for query in queries[:2]:  # Max 2 queries per source per tier to stay in quota
            if len(contacts) >= target_count:
                break
            print(f"    [CSE] {query[:70]}...")
            items = google_cse_search(query, num=5)
            for item in items:
                contact = parse_linkedin_snippet(item, source["label"], tier_num)
                if not contact:
                    continue
                key = make_contact_key(contact["name"], contact["firm"])
                if key in seen:
                    continue
                seen.add(key)
                contact["category"] = cat["label"]
                contacts.append(contact)
                if len(contacts) >= target_count:
                    break

    return contacts


def fetch_networking_targets() -> dict:
    """
    Main entry point. Returns dict with tier1/tier2/tier3 contact lists
    and metadata. Returns empty result if API keys not configured.
    """
    if not GOOGLE_CSE_KEY or not GOOGLE_CSE_CX:
        print("  [networking] GOOGLE_CSE_API_KEY or GOOGLE_CSE_CX not set — skipping")
        return {"enabled": False, "tier1": [], "tier2": [], "tier3": [], "week_category": ""}

    from datetime import datetime
    from zoneinfo import ZoneInfo
    MT = ZoneInfo("America/Denver")
    now = datetime.now(MT)

    print("  [networking] Fetching daily targets...")
    seen      = load_seen_contacts()
    week_idx  = get_week_index()

    # 3 T1 + 1 T2 + 1 T3 = 5 contacts per day
    tier1 = search_tier(TIER1_CATEGORIES, 1, 3, seen, week_idx)
    tier2 = search_tier(TIER2_CATEGORIES, 2, 1, seen, week_idx + 1)
    tier3 = search_tier(TIER3_CATEGORIES, 3, 1, seen, week_idx + 2)

    # Persist updated seen set
    save_seen_contacts(seen)

    week_cat = TIER1_CATEGORIES[week_idx % len(TIER1_CATEGORIES)]["label"]
    print(f"  [networking] Found: T1={len(tier1)}, T2={len(tier2)}, T3={len(tier3)}")

    return {
        "enabled":       True,
        "tier1":         tier1,
        "tier2":         tier2,
        "tier3":         tier3,
        "week_category": week_cat,
        "week_date":     now.strftime("%B %d, %Y"),
    }
