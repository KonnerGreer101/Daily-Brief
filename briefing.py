"""
Konner's Daily Brief — Agentic Email System
Weekdays at 7:00 AM MT  |  Saturday at 8:00 AM MT
"""

import os, json, smtplib, urllib.request, urllib.parse, xml.etree.ElementTree as ET, re
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from zoneinfo import ZoneInfo

# ── Config ─────────────────────────────────────────────────────────────────
ANTHROPIC_KEY = os.environ["ANTHROPIC_API_KEY"]
NEWS_KEY      = os.environ["NEWS_API_KEY"]
FINNHUB_KEY   = os.environ.get("FINNHUB_API_KEY", "")
GNEWS_KEY     = os.environ.get("GNEWS_API_KEY", "")
GMAIL_USER    = os.environ["GMAIL_USER"]
GMAIL_PASS    = os.environ["GMAIL_APP_PASS"]

MT          = ZoneInfo("America/Denver")
NOW         = datetime.now(MT)
TODAY       = NOW.strftime("%A, %B %d, %Y")
IS_SATURDAY = NOW.weekday() == 5


# ══════════════════════════════════════════════════════════════════════════
#  LAYER 1A — RSS FEEDS (verified to work from cloud servers)
# ══════════════════════════════════════════════════════════════════════════

RSS_FEEDS = {
    # Finance & Markets
    "ft_markets":       "https://www.ft.com/rss/home",
    "seeking_alpha":    "https://seekingalpha.com/feed.xml",
    "investing_news":   "https://www.investing.com/rss/news.rss",
    "marketwatch_top":  "https://feeds.marketwatch.com/marketwatch/topstories/",
    "marketwatch_mk":   "https://feeds.marketwatch.com/marketwatch/marketpulse/",

    # Global / General News
    "bbc_world":        "https://feeds.bbci.co.uk/news/world/rss.xml",
    "bbc_business":     "https://feeds.bbci.co.uk/news/business/rss.xml",
    "guardian_world":   "https://www.theguardian.com/world/rss",
    "guardian_biz":     "https://www.theguardian.com/business/rss",
    "guardian_us":      "https://www.theguardian.com/us-news/rss",
    "npr_news":         "https://feeds.npr.org/1001/rss.xml",
    "npr_business":     "https://feeds.npr.org/1006/rss.xml",
    "politico":         "https://www.politico.com/rss/politicopicks.xml",

    # SEC EDGAR (direct from the agency)
    "sec_litigation":   "https://www.sec.gov/rss/litigation/litreleases.xml",
    "sec_enforcement":  "https://www.sec.gov/rss/litigation/admin.xml",

    # Yankees / MLB
    "mlb_yankees":      "https://www.mlb.com/feeds/news/rss.xml?teamId=147",
    "espn_mlb":         "https://www.espn.com/espn/rss/mlb/news",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

SOURCE_NAMES = {
    "ft_markets": "FT", "seeking_alpha": "Seeking Alpha",
    "investing_news": "Investing.com", "marketwatch_top": "MarketWatch",
    "marketwatch_mk": "MarketWatch", "bbc_world": "BBC",
    "bbc_business": "BBC", "guardian_world": "The Guardian",
    "guardian_biz": "The Guardian", "guardian_us": "The Guardian",
    "npr_news": "NPR", "npr_business": "NPR", "politico": "Politico",
    "mlb_yankees": "MLB", "espn_mlb": "ESPN",
    "sec_litigation": "SEC", "sec_enforcement": "SEC",
}


def fetch_rss(feed_key, max_items=8, max_age_hours=30):
    url = RSS_FEEDS.get(feed_key)
    if not url:
        return []
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read()
        root   = ET.fromstring(raw)
        ns     = {"atom": "http://www.w3.org/2005/Atom"}
        entries = root.findall(".//item") or root.findall(".//atom:entry", ns)
        cutoff  = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
        items   = []

        for entry in entries[:max_items * 2]:
            title_el = entry.find("title") or entry.find("atom:title", ns)
            title = (title_el.text or "").strip() if title_el is not None else ""
            if not title or "[Removed]" in title:
                continue

            desc_el = (entry.find("description") or entry.find("summary") or
                       entry.find("atom:summary", ns))
            desc = re.sub(r'<[^>]+>', '', (desc_el.text or "") if desc_el is not None else "").strip()[:200]

            pub_el  = entry.find("pubDate") or entry.find("published") or entry.find("atom:published", ns)
            pub_str = pub_el.text.strip() if pub_el is not None and pub_el.text else ""

            pub_dt = None
            for fmt in ["%a, %d %b %Y %H:%M:%S %z", "%Y-%m-%dT%H:%M:%S%z",
                        "%Y-%m-%dT%H:%M:%SZ", "%a, %d %b %Y %H:%M:%S GMT"]:
                try:
                    pub_dt = datetime.strptime(pub_str[:30], fmt[:len(pub_str[:30])])
                    if pub_dt.tzinfo is None:
                        pub_dt = pub_dt.replace(tzinfo=timezone.utc)
                    break
                except:
                    continue

            if pub_dt and pub_dt < cutoff:
                continue

            items.append({
                "title": title, "description": desc,
                "source": SOURCE_NAMES.get(feed_key, feed_key),
                "published": pub_str[:16],
            })
            if len(items) >= max_items:
                break

        print(f"    [{feed_key}] {len(items)} articles")
        return items

    except Exception as ex:
        print(f"    RSS [{feed_key}]: {ex}")
        return []


def fetch_rss_multi(feed_keys, max_per_feed=5, max_age_hours=30):
    results = []
    for key in feed_keys:
        results.extend(fetch_rss(key, max_items=max_per_feed, max_age_hours=max_age_hours))
    return results


# ══════════════════════════════════════════════════════════════════════════
#  LAYER 1B — NEWSAPI (primary data source)
# ══════════════════════════════════════════════════════════════════════════

def newsapi_search(query, page_size=8, days_back=1):
    since = (datetime.utcnow() - timedelta(days=days_back)).strftime("%Y-%m-%dT%H:%M:%SZ")
    params = urllib.parse.urlencode({
        "q": query, "from": since, "sortBy": "publishedAt",
        "pageSize": page_size, "language": "en", "apiKey": NEWS_KEY,
    })
    try:
        req = urllib.request.Request(
            f"https://newsapi.org/v2/everything?{params}", headers=HEADERS)
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode())
        articles = [
            {"title": a.get("title",""),
             "description": (a.get("description") or "")[:200],
             "source": a.get("source",{}).get("name",""),
             "published": a.get("publishedAt","")[:16]}
            for a in data.get("articles",[])
            if a.get("title") and "[Removed]" not in a.get("title","")
        ]
        print(f"    [newsapi: {query[:40]}] {len(articles)} articles")
        return articles
    except Exception as ex:
        print(f"    NewsAPI '{query[:40]}': {ex}")
        return []


def newsapi_headlines(category="business", page_size=8):
    params = urllib.parse.urlencode({
        "category": category, "country": "us",
        "pageSize": page_size, "apiKey": NEWS_KEY,
    })
    try:
        req = urllib.request.Request(
            f"https://newsapi.org/v2/top-headlines?{params}", headers=HEADERS)
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode())
        articles = [
            {"title": a.get("title",""),
             "description": (a.get("description") or "")[:200],
             "source": a.get("source",{}).get("name",""),
             "published": a.get("publishedAt","")[:16]}
            for a in data.get("articles",[])
            if a.get("title") and "[Removed]" not in a.get("title","")
        ]
        print(f"    [newsapi headlines: {category}] {len(articles)} articles")
        return articles
    except Exception as ex:
        print(f"    NewsAPI headlines '{category}': {ex}")
        return []


# ══════════════════════════════════════════════════════════════════════════
#  LAYER 1C — FINNHUB (real-time finance news, no 403 issues)
# ══════════════════════════════════════════════════════════════════════════

def finnhub_news(category="general", min_id=0):
    """Fetch finance news from Finnhub. Category: general, forex, crypto, merger."""
    if not FINNHUB_KEY:
        print("    [finnhub] no API key — skipping")
        return []
    try:
        params = urllib.parse.urlencode({"category": category, "minId": min_id, "token": FINNHUB_KEY})
        req = urllib.request.Request(
            f"https://finnhub.io/api/v1/news?{params}", headers=HEADERS)
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode())
        cutoff = datetime.now(timezone.utc) - timedelta(hours=26)
        articles = []
        for a in data:
            ts = a.get("datetime", 0)
            pub_dt = datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None
            if pub_dt and pub_dt < cutoff:
                continue
            headline = a.get("headline","").strip()
            if not headline:
                continue
            articles.append({
                "title":       headline,
                "description": (a.get("summary","") or "")[:200],
                "source":      a.get("source","Finnhub"),
                "published":   pub_dt.strftime("%Y-%m-%dT%H:%M") if pub_dt else "",
            })
        print(f"    [finnhub:{category}] {len(articles)} articles")
        return articles
    except Exception as ex:
        print(f"    [finnhub] {ex}")
        return []


def finnhub_company_news(symbol, days_back=1):
    """Fetch news for a specific ticker from Finnhub."""
    if not FINNHUB_KEY:
        return []
    try:
        today = datetime.utcnow().strftime("%Y-%m-%d")
        from_date = (datetime.utcnow() - timedelta(days=days_back)).strftime("%Y-%m-%d")
        params = urllib.parse.urlencode({
            "symbol": symbol, "from": from_date, "to": today, "token": FINNHUB_KEY
        })
        req = urllib.request.Request(
            f"https://finnhub.io/api/v1/company-news?{params}", headers=HEADERS)
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode())
        articles = []
        for a in data[:6]:
            headline = a.get("headline","").strip()
            if headline:
                articles.append({
                    "title": headline,
                    "description": (a.get("summary","") or "")[:200],
                    "source": a.get("source", "Finnhub"),
                    "published": "",
                })
        return articles
    except Exception as ex:
        print(f"    [finnhub company {symbol}] {ex}")
        return []


# ══════════════════════════════════════════════════════════════════════════
#  LAYER 1D — GNEWS (global news API, free tier)
# ══════════════════════════════════════════════════════════════════════════

def gnews_search(query, max_results=8, lang="en", country="us"):
    """Search GNews API. Free tier: 100 requests/day."""
    if not GNEWS_KEY:
        print("    [gnews] no API key — skipping")
        return []
    try:
        params = urllib.parse.urlencode({
            "q": query, "lang": lang, "country": country,
            "max": max_results, "apikey": GNEWS_KEY,
        })
        req = urllib.request.Request(
            f"https://gnews.io/api/v4/search?{params}", headers=HEADERS)
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode())
        articles = []
        for a in data.get("articles", []):
            title = (a.get("title") or "").strip()
            if not title:
                continue
            articles.append({
                "title":       title,
                "description": (a.get("description") or "")[:200],
                "source":      a.get("source", {}).get("name", "GNews"),
                "published":   (a.get("publishedAt") or "")[:16],
            })
        print(f"    [gnews: {query[:35]}] {len(articles)} articles")
        return articles
    except Exception as ex:
        print(f"    [gnews] {ex}")
        return []


def gnews_top(topic="business", max_results=8):
    """Fetch top headlines by topic from GNews. Topics: breaking-news, world, business, technology, sports, science, health."""
    if not GNEWS_KEY:
        return []
    try:
        params = urllib.parse.urlencode({
            "topic": topic, "lang": "en", "country": "us",
            "max": max_results, "apikey": GNEWS_KEY,
        })
        req = urllib.request.Request(
            f"https://gnews.io/api/v4/top-headlines?{params}", headers=HEADERS)
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode())
        articles = []
        for a in data.get("articles", []):
            title = (a.get("title") or "").strip()
            if not title:
                continue
            articles.append({
                "title":       title,
                "description": (a.get("description") or "")[:200],
                "source":      a.get("source", {}).get("name", "GNews"),
                "published":   (a.get("publishedAt") or "")[:16],
            })
        print(f"    [gnews top:{topic}] {len(articles)} articles")
        return articles
    except Exception as ex:
        print(f"    [gnews top] {ex}")
        return []


# ══════════════════════════════════════════════════════════════════════════
#  LAYER 1E — REAL MARKET DATA (yfinance)
# ══════════════════════════════════════════════════════════════════════════

def fetch_market_data():
    try:
        import yfinance as yf
        tickers = {
            "^GSPC":    "S&P 500",
            "^IXIC":    "Nasdaq",
            "^DJI":     "Dow Jones",
            "^TNX":     "10-Yr Yield",
            "CL=F":     "Crude Oil (WTI)",
            "GC=F":     "Gold",
            "DX-Y.NYB": "US Dollar Index",
            "BTC-USD":  "Bitcoin",
        }
        result = {}
        for symbol, name in tickers.items():
            try:
                hist = yf.Ticker(symbol).history(period="2d")
                if len(hist) >= 2:
                    prev = float(hist["Close"].iloc[-2])
                    curr = float(hist["Close"].iloc[-1])
                    pct  = ((curr - prev) / prev) * 100
                    result[symbol] = {
                        "name":       name,
                        "price":      round(curr, 2),
                        "change_pct": round(pct, 2),
                        "direction":  "up" if pct > 0.05 else ("down" if pct < -0.05 else "flat"),
                    }
            except Exception as ex:
                print(f"    yfinance [{symbol}]: {ex}")
        print(f"    [yfinance] {len(result)} tickers fetched")
        return result
    except ImportError:
        print("    yfinance not installed")
        return {}


# ══════════════════════════════════════════════════════════════════════════
#  LAYER 1F — FORMATTERS & GATHERERS
# ══════════════════════════════════════════════════════════════════════════

def fmt_articles(articles, n=12):
    if not articles:
        return "No articles found."
    seen, lines = set(), []
    for a in articles:
        t = a.get("title","").strip()
        if not t or t in seen:
            continue
        seen.add(t)
        desc = f" — {a['description'][:150]}" if a.get("description") else ""
        lines.append(f"• [{a.get('source','')}] {t}{desc}")
        if len(lines) >= n:
            break
    return "\n".join(lines) if lines else "No articles found."


def fmt_market_data(md):
    if not md:
        return "No real-time data available — markets may be closed."
    lines = []
    for symbol, info in md.items():
        arrow = "▲" if info["direction"] == "up" else ("▼" if info["direction"] == "down" else "–")
        if symbol == "^TNX":
            lines.append(f"• {info['name']}: {info['price']:.2f}% ({arrow}{abs(info['change_pct']):.2f}bp)")
        else:
            lines.append(f"• {info['name']}: {info['price']:,.2f} ({arrow}{info['change_pct']:+.2f}%)")
    return "\n".join(lines)


def gather_weekday_data():
    print("\n  → Real-time market prices (yfinance)...")
    market_data = fetch_market_data()

    print("\n  → Markets & Finance (NewsAPI + Finnhub + GNews)...")
    markets  = newsapi_headlines(category="business", page_size=8)
    markets += newsapi_search("stock market S&P 500 Nasdaq earnings Wall Street equities sector", page_size=6)
    markets += finnhub_news(category="general")          # real-time finance news, no 403
    markets += gnews_top(topic="business", max_results=6)
    markets += fetch_rss_multi(["marketwatch_top", "marketwatch_mk", "bbc_business", "ft_markets"], max_per_feed=4)

    print("\n  → Earnings (NewsAPI + Finnhub)...")
    earnings  = newsapi_search("quarterly earnings EPS revenue beat miss guidance raised lowered", page_size=8)
    earnings += newsapi_search("earnings results profit loss fiscal quarter analyst estimate", page_size=5)
    earnings += finnhub_news(category="general")
    earnings += fetch_rss_multi(["marketwatch_top", "seeking_alpha"], max_per_feed=4)

    print("\n  → M&A / IPO / Deals (NewsAPI + Finnhub merger feed)...")
    deals  = newsapi_search("merger acquisition takeover buyout billion deal agreed signed", page_size=6)
    deals += newsapi_search("IPO initial public offering listing debut S-1 filed valuation", page_size=5)
    deals += newsapi_search("fundraise venture capital raised funding round series billion", page_size=4)
    deals += finnhub_news(category="merger")             # dedicated M&A feed
    deals += fetch_rss_multi(["marketwatch_top", "guardian_biz"], max_per_feed=3)

    print("\n  → Macro & Policy (NewsAPI + GNews)...")
    macro  = newsapi_search("Federal Reserve rate decision CPI inflation GDP jobs report data", page_size=6)
    macro += newsapi_search("trade tariffs Treasury bonds yield curve economic policy recession", page_size=5)
    macro += newsapi_search("consumer spending retail sales housing starts economic indicator", page_size=4)
    macro += gnews_search("Federal Reserve inflation GDP economic policy", max_results=5)
    macro += fetch_rss_multi(["npr_business", "ft_markets", "guardian_biz"], max_per_feed=4)

    print("\n  → Market-moving regulatory (strict filter — SEC feeds + major news)...")
    regulatory  = fetch_rss_multi(["sec_litigation", "sec_enforcement"], max_per_feed=4)  # direct from SEC.gov
    regulatory += newsapi_search("SEC fraud indictment charged billion settlement major enforcement", page_size=4)
    regulatory += newsapi_search("Fed rate decision FOMC bank failure financial crisis systemic", page_size=3)

    print("\n  → Finance headlines (broad sectors)...")
    fin_headlines  = newsapi_headlines(category="business", page_size=6)
    fin_headlines += newsapi_search("energy oil pharma biotech retail consumer auto airline semiconductor", page_size=5)
    fin_headlines += newsapi_search("real estate housing banking insurance fintech payments crypto", page_size=4)
    fin_headlines += gnews_top(topic="business", max_results=5)
    fin_headlines += fetch_rss_multi(["bbc_business", "guardian_biz", "marketwatch_top"], max_per_feed=4)

    print("\n  → Global News (NewsAPI + GNews + RSS)...")
    global_news  = newsapi_headlines(category="general", page_size=6)
    global_news += newsapi_search("China Europe Russia Middle East war election crisis diplomacy", page_size=6)
    global_news += gnews_top(topic="world", max_results=6)
    global_news += gnews_search("geopolitics international trade sanctions foreign policy", max_results=5)
    global_news += fetch_rss_multi(["bbc_world", "guardian_world", "guardian_us", "npr_news"], max_per_feed=4)

    print("\n  → Yankees...")
    yankees  = fetch_rss_multi(["mlb_yankees", "espn_mlb"], max_per_feed=6)
    yankees += newsapi_search("New York Yankees MLB baseball", page_size=5, days_back=2)

    return {
        "date":          TODAY,
        "market_data":   market_data,
        "markets":       markets,
        "earnings":      earnings,
        "deals":         deals,
        "macro":         macro,
        "regulatory":    regulatory,
        "fin_headlines": fin_headlines,
        "global_news":   global_news,
        "yankees":       yankees,
    }


def gather_saturday_data():
    print("\n  → Real-time prices...")
    market_data = fetch_market_data()

    print("\n  → Week's markets...")
    markets  = newsapi_headlines(category="business", page_size=8)
    markets += newsapi_search("S&P 500 Nasdaq stock market weekly sector performance", page_size=8, days_back=6)
    markets += fetch_rss_multi(["marketwatch_top", "bbc_business", "ft_markets"], max_per_feed=5, max_age_hours=150)

    print("\n  → Week's earnings & deals...")
    earnings_deals  = newsapi_search("earnings results quarterly revenue IPO merger acquisition", page_size=8, days_back=6)
    earnings_deals += fetch_rss_multi(["seeking_alpha", "marketwatch_top", "guardian_biz"], max_per_feed=5, max_age_hours=150)

    print("\n  → Week's macro...")
    macro  = newsapi_search("Federal Reserve inflation GDP trade policy tariffs economic data", page_size=6, days_back=6)
    macro += fetch_rss_multi(["npr_business", "ft_markets", "guardian_biz"], max_per_feed=4, max_age_hours=150)

    print("\n  → Week's regulatory...")
    regulatory = newsapi_search("SEC fraud DOJ indictment Fed rate FOMC systemic financial crisis", page_size=5, days_back=6)

    print("\n  → Week's global news...")
    global_news  = newsapi_headlines(category="general", page_size=6)
    global_news += newsapi_search("geopolitics war sanctions diplomacy election crisis international", page_size=6, days_back=6)
    global_news += fetch_rss_multi(["bbc_world", "guardian_world", "npr_news"], max_per_feed=5, max_age_hours=150)

    print("\n  → Next week's calendar...")
    calendar  = newsapi_search("CPI FOMC Fed meeting earnings next week economic calendar", page_size=5, days_back=3)
    calendar += fetch_rss_multi(["npr_business", "marketwatch_top"], max_per_feed=3, max_age_hours=96)

    print("\n  → Yankees week...")
    yankees  = fetch_rss_multi(["mlb_yankees", "espn_mlb"], max_per_feed=6, max_age_hours=150)
    yankees += newsapi_search("New York Yankees MLB", page_size=5, days_back=6)

    return {
        "date":           TODAY,
        "market_data":    market_data,
        "markets":        markets,
        "earnings_deals": earnings_deals,
        "macro":          macro,
        "regulatory":     regulatory,
        "global_news":    global_news,
        "calendar":       calendar,
        "yankees":        yankees,
    }


# ══════════════════════════════════════════════════════════════════════════
#  LAYER 2 — CLAUDE AGENT
# ══════════════════════════════════════════════════════════════════════════

def call_claude(system_prompt, user_prompt, max_tokens=4000):
    payload = json.dumps({
        "model": "claude-opus-4-5",
        "max_tokens": max_tokens,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_prompt}]
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "x-api-key": ANTHROPIC_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            data = json.loads(r.read().decode())
        return data["content"][0]["text"]
    except urllib.error.HTTPError as ex:
        body = ex.read().decode()
        print(f"  Anthropic API error {ex.code}: {body}")
        raise


SYSTEM_PROMPT = """You are the writer of "The Daily Brief" — a personal morning email newsletter for Konner Greer, a Finance & Fintech student at the University of Utah (graduating December 2027). He interns at University of Utah Financial Services and is interested in financial markets, economic policy, fintech, management consulting, and the New York Yankees.

TONE:
- Write like a sharp senior analyst briefing a smart junior — direct, clear, no fluff
- Mix professional with conversational — polished but not stiff
- Plain English for jargon — define it once, then use it
- Always: what happened, why it happened, why it matters
- Be specific — use real numbers, names, percentages from the source material
- Synthesize and explain — never just restate headlines

SOURCE PRIORITY:
- Prefer WSJ and NYT framing when those outlets are cited in the source material
- Reuters, AP, BBC, Guardian, NPR, MarketWatch, FT are all credible — use the best available
- Always attribute stories to the correct outlet

REGULATORY / FED FILTER — CRITICAL:
- Only include SEC, DOJ, Fed, or regulatory content if genuinely market-moving
- "Market-moving" = Fed rate decision, major fraud indictment (billion-dollar scale), systemic policy shift, banking crisis
- Routine enforcement, small fines, standard speeches = skip entirely
- If nothing clears this bar, return empty array [] for regulatory — do not pad

DIVERSITY:
- Each section covers DIFFERENT stories — never repeat the same company/event across sections
- Spread across sectors: tech, finance, energy, healthcare, consumer, industrials, macro, international
- Movers: always lead with indices/macro (S&P, Nasdaq, yields, oil, gold) before individual stocks
- 3 Finance Headlines from 3 different sectors/domains
- Slow news day = say so honestly in opening, don't pad sections

OUTPUT: Valid JSON only. No markdown, no preamble, no code fences. Raw JSON object only.
"""


def generate_weekday_briefing(data):
    user_prompt = f"""Today is {data['date']}. Write today's Daily Brief from the source material below.

Return a JSON object with EXACTLY these keys:

{{
  "opening": "3-4 sentences: biggest themes today, what kind of morning, what to watch",

  "earnings": [
    {{"ticker":"TICKER","company":"Full Name","headline":"Sharp headline",
      "what":"What happened with real numbers","why":"Why it happened",
      "matters":"Why it matters for markets or the sector"}}
  ],

  "movers": [
    {{"name":"Index or asset name","change":"exact value e.g. +1.2% or 5,631","direction":"up/down/flat",
      "reason":"One clear sentence explaining the move"}}
  ],

  "deals": [
    {{"type":"M&A/IPO/Fundraise","headline":"Sharp headline",
      "what":"What happened","matters":"Why it matters"}}
  ],

  "fin_headlines": [
    {{"source":"Outlet name","tag":"Sector/Topic tag","headline":"Sharp headline",
      "what":"What happened","matters":"Why it matters","context":"Broader context"}}
  ],

  "regulatory": [
    {{"agency":"SEC/DOJ/Fed","tag":"Topic","headline":"Sharp headline",
      "what":"What happened","matters":"Why this is genuinely market-moving"}}
  ],

  "global_news": [
    {{"region":"Geographic region","tag":"Topic tag","headline":"Sharp headline",
      "summary":"2-3 sentences: what happened and why it matters"}}
  ],

  "yankees": {{
    "result":"Final score or Off day or Game time TBD",
    "detail":"2-3 sentences on the game, key performances, or latest team news",
    "next_game":"Opponent · Date · Time ET · Broadcast"
  }},

  "closing": {{"text":"Memorable quote or insight","attribution":"— Person or Source"}}
}}

RULES:
- earnings: 3-5 companies. If slow earnings day, 1-2 is fine — don't pad.
- movers: 5-7 items. ALWAYS use real prices from market data section below. Lead with S&P/Nasdaq/Dow/yields/oil/gold.
- fin_headlines: exactly 3 stories from 3 different sectors
- regulatory: [] unless genuinely market-moving — strict filter
- global_news: exactly 3, biggest world stories
- Never return placeholder values like "X.X%" — use real data or omit the item

--- REAL-TIME MARKET PRICES (use these exact numbers) ---
{fmt_market_data(data.get('market_data', {}))}

--- MARKETS & FINANCE ---
{fmt_articles(data['markets'], 16)}

--- EARNINGS ---
{fmt_articles(data['earnings'], 14)}

--- M&A / IPO / DEALS ---
{fmt_articles(data['deals'], 10)}

--- MACRO & POLICY ---
{fmt_articles(data['macro'], 12)}

--- REGULATORY (apply strict market-moving filter) ---
{fmt_articles(data['regulatory'], 8)}

--- FINANCE HEADLINES (broad sectors) ---
{fmt_articles(data['fin_headlines'], 14)}

--- GLOBAL NEWS ---
{fmt_articles(data['global_news'], 12)}

--- YANKEES ---
{fmt_articles(data['yankees'], 6)}
"""
    raw = call_claude(SYSTEM_PROMPT, user_prompt, max_tokens=4500)
    raw = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
    return json.loads(raw)


def generate_saturday_briefing(data):
    mon = NOW - timedelta(days=NOW.weekday())
    fri = mon + timedelta(days=4)
    week_range = f"{mon.strftime('%B %d')}–{fri.strftime('%B %d, %Y')}"

    user_prompt = f"""Today is {data['date']} (Saturday). Write the Weekly Brief for the week of {week_range}.

Return a JSON object with EXACTLY these keys:

{{
  "week_range": "{week_range}",
  "opening": "3-4 sentences: dominant themes this week, overall market and macro story",
  "themes": [
    {{"title":"Theme title","body":"3-4 sentences: what happened, why, what it means going forward"}}
  ],
  "scoreboard": [
    {{"name":"Asset","value":"Price/level","change":"WTD change","direction":"up/down/flat"}}
  ],
  "earnings_deals_recap": "3-4 paragraphs synthesizing the week's earnings and deals. What story did earnings tell about the economy?",
  "macro_policy_geo": [
    {{"tag":"Topic","headline":"Sharp headline","summary":"3-4 sentences: what happened, why it matters, what to watch"}}
  ],
  "regulatory_recap": "1-2 paragraphs ONLY if something genuinely market-moving happened this week. Otherwise empty string.",
  "watch_next_week": [
    {{"day":"MON/TUE/WED/THU/FRI","event":"Event name","detail":"Why it matters and what to expect"}}
  ],
  "yankees_week": {{
    "record":"X-Y this week · XX-XX season","summary":"2-3 sentences on the week",
    "next_week":"Upcoming opponents and series"
  }},
  "closing": {{"text":"Quote fitting for end of week","attribution":"— Source"}}
}}

themes=3 | scoreboard: S&P 500, Nasdaq, Dow, 10-Yr Yield, Brent Crude, Gold | macro_policy_geo=3 | watch_next_week=4-5

--- REAL-TIME PRICES ---
{fmt_market_data(data.get('market_data', {}))}

--- MARKETS (WEEK) ---
{fmt_articles(data['markets'], 16)}

--- EARNINGS & DEALS (WEEK) ---
{fmt_articles(data['earnings_deals'], 14)}

--- MACRO & POLICY (WEEK) ---
{fmt_articles(data['macro'], 12)}

--- REGULATORY (major only) ---
{fmt_articles(data['regulatory'], 6)}

--- GLOBAL NEWS (WEEK) ---
{fmt_articles(data['global_news'], 12)}

--- NEXT WEEK CALENDAR ---
{fmt_articles(data['calendar'], 8)}

--- YANKEES (WEEK) ---
{fmt_articles(data['yankees'], 8)}
"""
    raw = call_claude(SYSTEM_PROMPT, user_prompt, max_tokens=4500)
    raw = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
    return json.loads(raw)


# ══════════════════════════════════════════════════════════════════════════
#  LAYER 3 — HTML EMAIL RENDERER
# ══════════════════════════════════════════════════════════════════════════

CSS = """<style>
@import url('https://fonts.googleapis.com/css2?family=Playfair+Display:wght@700;900&family=Source+Sans+3:wght@300;400;600&display=swap');
*{margin:0;padding:0;box-sizing:border-box}
body{background:#f0ece4;font-family:'Source Sans 3',Georgia,sans-serif;color:#1a1a1a;font-size:15px;line-height:1.65}
.wrap{max-width:660px;margin:0 auto;background:#faf8f4}
.hdr{background:#0d1b2a;padding:36px 40px 28px;border-bottom:4px solid #c9973a}
.hdr-label{font-size:10px;font-weight:600;letter-spacing:3.5px;color:#c9973a;text-transform:uppercase;margin-bottom:10px}
.hdr-title{font-family:'Playfair Display',serif;font-size:34px;font-weight:900;color:#fff;line-height:1.1}
.hdr-date{font-size:12px;color:#8a9bb0;margin-top:10px;letter-spacing:1px}
.hdr-sub{font-size:12px;color:#c9973a;margin-top:4px;font-style:italic}
.lead{background:#1a2e42;padding:24px 40px;border-left:4px solid #c9973a}
.lead p{color:#d4dfe8;font-size:15px;line-height:1.75}
.lead strong{color:#fff}
.sec{padding:26px 40px;border-bottom:1px solid #e2ddd4}
.lbl{display:inline-block;font-size:9.5px;font-weight:600;letter-spacing:3px;text-transform:uppercase;color:#fff;background:#0d1b2a;padding:3px 10px;margin-bottom:14px}
.lbl.gold{background:#c9973a}.lbl.slate{background:#3d5166}.lbl.green{background:#1e4d2b}
.lbl.red{background:#8b1a1a}.lbl.navy{background:#003087}.lbl.teal{background:#1a4d4a}
.lbl.purple{background:#4a1942}
.sec h2{font-family:'Playfair Display',serif;font-size:20px;font-weight:700;color:#0d1b2a;margin-bottom:14px;line-height:1.2}
.story{margin-bottom:20px;padding-bottom:20px;border-bottom:1px dashed #ddd8cf}
.story:last-child{border-bottom:none;margin-bottom:0;padding-bottom:0}
.story-name{font-size:11px;font-weight:600;letter-spacing:1.5px;text-transform:uppercase;color:#c9973a;margin-bottom:5px}
.story-hed{font-family:'Playfair Display',serif;font-size:16px;font-weight:700;color:#0d1b2a;margin-bottom:10px;line-height:1.3}
.story-body{font-size:13.5px;color:#3a3a3a;line-height:1.65}
.wwm p{font-size:13.5px;color:#3a3a3a;margin-bottom:8px;line-height:1.65;padding-left:12px;border-left:2px solid #e2ddd4}
.wwm p strong{color:#0d1b2a}
.mv{display:flex;align-items:baseline;gap:10px;margin-bottom:12px;padding-bottom:12px;border-bottom:1px dashed #ddd8cf}
.mv:last-child{border-bottom:none;margin-bottom:0;padding-bottom:0}
.mv-tk{font-weight:600;font-size:13px;color:#0d1b2a;min-width:80px;letter-spacing:0.5px}
.mv-ch{font-size:13px;font-weight:600;min-width:65px}
.up{color:#1e6b35}.down{color:#8b1a1a}.flat{color:#8a9bb0}
.mv-why{font-size:13.5px;color:#3a3a3a;flex:1}
.sb{display:flex;flex-wrap:wrap;gap:0;margin-bottom:8px}
.sb-item{flex:1 1 30%;background:#f0ece4;padding:12px 14px;border:1px solid #e2ddd4;margin:3px;border-radius:2px}
.sb-lbl{font-size:10px;font-weight:600;letter-spacing:1px;text-transform:uppercase;color:#8a9bb0;margin-bottom:4px}
.sb-val{font-family:'Playfair Display',serif;font-size:18px;font-weight:700;color:#0d1b2a}
.sb-chg{font-size:12px;font-weight:600;margin-top:2px}
.theme{background:#f5f2ec;border-left:3px solid #c9973a;padding:14px 18px;margin-bottom:14px;border-radius:0 3px 3px 0}
.theme:last-child{margin-bottom:0}
.theme-title{font-family:'Playfair Display',serif;font-size:15px;font-weight:700;color:#0d1b2a;margin-bottom:6px}
.theme-body{font-size:13.5px;color:#3a3a3a;line-height:1.65}
.watch{display:flex;gap:14px;margin-bottom:14px;padding-bottom:14px;border-bottom:1px dashed #ddd8cf}
.watch:last-child{border-bottom:none;margin-bottom:0;padding-bottom:0}
.watch-day{font-size:10px;font-weight:600;letter-spacing:1.5px;text-transform:uppercase;color:#fff;background:#3d5166;padding:4px 8px;height:fit-content;min-width:36px;text-align:center;border-radius:2px}
.watch-event{font-family:'Playfair Display',serif;font-size:14px;font-weight:700;color:#0d1b2a;margin-bottom:4px}
.watch-detail{font-size:13px;color:#3a3a3a}
.ynk{background:#003087;padding:20px 24px;border-radius:3px}
.ynk-score{font-family:'Playfair Display',serif;font-size:22px;font-weight:700;color:#fff;margin-bottom:6px}
.ynk-detail{font-size:13px;color:#aac4ff;line-height:1.6}
.ynk-next{font-size:13px;color:#c9973a;margin-top:10px;font-weight:600}
.closing{background:#0d1b2a;padding:28px 40px;text-align:center}
.closing blockquote{font-family:'Playfair Display',serif;font-size:18px;font-style:italic;color:#d4dfe8;line-height:1.5;margin-bottom:8px}
.closing cite{font-size:12px;color:#c9973a;letter-spacing:1.5px;text-transform:uppercase;font-style:normal}
.footer{background:#0a1520;padding:16px 40px;text-align:center}
.footer p{font-size:11px;color:#4a5a6a;letter-spacing:0.5px}
.footer span{color:#c9973a}
</style>"""


def e(t):
    return str(t).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace('"',"&quot;")


def render_weekday(d):
    earnings_html = ""
    for co in d.get("earnings", []):
        earnings_html += f"""<div class="story">
          <div class="story-name">{e(co.get('ticker',''))} &nbsp;·&nbsp; {e(co.get('company',''))}</div>
          <div class="story-hed">{e(co.get('headline',''))}</div>
          <div class="wwm">
            <p><strong>What happened:</strong> {e(co.get('what',''))}</p>
            <p><strong>Why it happened:</strong> {e(co.get('why',''))}</p>
            <p><strong>Why it matters:</strong> {e(co.get('matters',''))}</p>
          </div></div>"""
    if not earnings_html:
        earnings_html = '<p class="story-body">No major earnings reported overnight.</p>'

    movers_html = ""
    for mv in d.get("movers", []):
        cls = "up" if mv.get("direction")=="up" else ("down" if mv.get("direction")=="down" else "flat")
        movers_html += f"""<div class="mv">
          <span class="mv-tk">{e(mv.get('name',''))}</span>
          <span class="mv-ch {cls}">{e(mv.get('change',''))}</span>
          <span class="mv-why">{e(mv.get('reason',''))}</span></div>"""
    if not movers_html:
        movers_html = '<p class="story-body">Market data unavailable — check Bloomberg or CNBC.</p>'

    deals_html = ""
    for deal in d.get("deals", []):
        deals_html += f"""<div class="story">
          <div class="story-name">{e(deal.get('type',''))}</div>
          <div class="story-hed">{e(deal.get('headline',''))}</div>
          <div class="wwm">
            <p><strong>What happened:</strong> {e(deal.get('what',''))}</p>
            <p><strong>Why it matters:</strong> {e(deal.get('matters',''))}</p>
          </div></div>"""
    if not deals_html:
        deals_html = '<p class="story-body">No major deals or IPOs today.</p>'

    fin_html = ""
    for story in d.get("fin_headlines", []):
        fin_html += f"""<div class="story">
          <div class="story-name">{e(story.get('source',''))} &nbsp;·&nbsp; {e(story.get('tag',''))}</div>
          <div class="story-hed">{e(story.get('headline',''))}</div>
          <div class="wwm">
            <p><strong>What happened:</strong> {e(story.get('what',''))}</p>
            <p><strong>Why it matters:</strong> {e(story.get('matters',''))}</p>
            <p><strong>Context:</strong> {e(story.get('context',''))}</p>
          </div></div>"""

    reg_items = d.get("regulatory", [])
    reg_section = ""
    if reg_items:
        reg_html = ""
        for story in reg_items:
            reg_html += f"""<div class="story">
              <div class="story-name">{e(story.get('agency',''))} &nbsp;·&nbsp; {e(story.get('tag',''))}</div>
              <div class="story-hed">{e(story.get('headline',''))}</div>
              <div class="wwm">
                <p><strong>What happened:</strong> {e(story.get('what',''))}</p>
                <p><strong>Why it matters:</strong> {e(story.get('matters',''))}</p>
              </div></div>"""
        reg_section = f"""<div class="sec">
  <div class="lbl purple">Market-Moving Policy</div>
  <h2>Regulatory &amp; Policy Shifts</h2>{reg_html}</div>"""

    global_html = ""
    for story in d.get("global_news", []):
        global_html += f"""<div class="story">
          <div class="story-name">{e(story.get('region',''))} &nbsp;·&nbsp; {e(story.get('tag',''))}</div>
          <div class="story-hed">{e(story.get('headline',''))}</div>
          <div class="story-body">{e(story.get('summary',''))}</div></div>"""

    y  = d.get("yankees", {})
    cl = d.get("closing", {})

    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>The Daily Brief — {e(d.get('date',''))}</title>{CSS}</head>
<body><div class="wrap">
<div class="hdr">
  <div class="hdr-label">The Daily Brief</div>
  <div class="hdr-title">Good Morning, Konner.</div>
  <div class="hdr-date">{e(d.get('date',''))} &nbsp;·&nbsp; Markets open in ~30 min</div>
  <div class="hdr-sub">Everything you need. Nothing you don't.</div>
</div>
<div class="lead"><p>{e(d.get('opening',''))}</p></div>
<div class="sec"><div class="lbl gold">Earnings Corner</div><h2>Who Reported &amp; What It Means</h2>{earnings_html}</div>
<div class="sec"><div class="lbl slate">On the Move</div><h2>Major Movers &amp; Why</h2>{movers_html}</div>
<div class="sec"><div class="lbl">M&amp;A · IPO · Capital Markets</div><h2>Deals &amp; Raises</h2>{deals_html}</div>
<div class="sec"><div class="lbl gold">Today's Headlines</div><h2>Finance &amp; Markets</h2>{fin_html}</div>
{reg_section}
<div class="sec"><div class="lbl green">Global News</div><h2>World Stories That Matter</h2>{global_html}</div>
<div class="sec"><div class="lbl navy">Yankees</div><h2>Bronx Update</h2>
  <div class="ynk">
    <div class="ynk-score">{e(y.get('result','No game data available'))}</div>
    <div class="ynk-detail">{e(y.get('detail',''))}</div>
    <div class="ynk-next">▶ Next: {e(y.get('next_game','Check MLB.com'))}</div>
  </div>
</div>
<div class="closing"><blockquote>"{e(cl.get('text',''))}"</blockquote><cite>{e(cl.get('attribution',''))}</cite></div>
<div class="footer">
  <p>The Daily Brief &nbsp;·&nbsp; Built for <span>Konner Greer</span> &nbsp;·&nbsp; University of Utah, Finance &amp; Fintech '27</p>
  <p style="margin-top:4px;">Delivered every weekday at 7:00 AM MT &nbsp;·&nbsp; <span>Markets open at 7:30 AM MT</span></p>
</div>
</div></body></html>"""


def render_saturday(d):
    themes_html = ""
    for i, t in enumerate(d.get("themes",[]), 1):
        themes_html += f"""<div class="theme">
          <div class="theme-title">{i}. {e(t.get('title',''))}</div>
          <div class="theme-body">{e(t.get('body',''))}</div></div>"""

    sb_html = '<div class="sb">'
    for item in d.get("scoreboard",[]):
        cls = "up" if item.get("direction")=="up" else ("down" if item.get("direction")=="down" else "flat")
        sb_html += f"""<div class="sb-item">
          <div class="sb-lbl">{e(item.get('name',''))}</div>
          <div class="sb-val">{e(item.get('value','—'))}</div>
          <div class="sb-chg {cls}">{e(item.get('change',''))}</div></div>"""
    sb_html += "</div>"

    macro_html = ""
    for story in d.get("macro_policy_geo",[]):
        macro_html += f"""<div class="story">
          <div class="story-name">{e(story.get('tag',''))}</div>
          <div class="story-hed">{e(story.get('headline',''))}</div>
          <div class="story-body">{e(story.get('summary',''))}</div></div>"""

    reg_recap = d.get("regulatory_recap","")
    reg_section = ""
    if reg_recap and len(reg_recap.strip()) > 30:
        reg_section = f"""<div class="sec">
  <div class="lbl purple">Market-Moving Policy</div>
  <h2>Regulatory &amp; Policy This Week</h2>
  <div class="story-body" style="font-size:14px;line-height:1.75">{e(reg_recap)}</div></div>"""

    watch_html = ""
    for item in d.get("watch_next_week",[]):
        watch_html += f"""<div class="watch">
          <div class="watch-day">{e(item.get('day',''))}</div>
          <div><div class="watch-event">{e(item.get('event',''))}</div>
          <div class="watch-detail">{e(item.get('detail',''))}</div></div></div>"""

    y  = d.get("yankees_week",{})
    cl = d.get("closing",{})
    week_range = d.get("week_range","This Week")

    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>The Weekly Brief — {e(week_range)}</title>{CSS}</head>
<body><div class="wrap">
<div class="hdr" style="background:linear-gradient(150deg,#1a1200 0%,#3a2800 50%,#0d1b2a 100%)">
  <div class="hdr-label">The Weekly Brief &nbsp;·&nbsp; Saturday Edition</div>
  <div class="hdr-title">The Week in Review.</div>
  <div class="hdr-date">Week of {e(week_range)}</div>
  <div class="hdr-sub">Read it once, sound sharp all weekend.</div>
</div>
<div class="lead"><p>{e(d.get('opening',''))}</p></div>
<div class="sec"><div class="lbl gold">The Big Picture</div><h2>This Week's Defining Themes</h2>{themes_html}</div>
<div class="sec"><div class="lbl slate">Markets</div><h2>Weekly Scoreboard</h2>{sb_html}</div>
<div class="sec"><div class="lbl gold">Earnings &amp; Deals</div><h2>What Moved Needles This Week</h2>
  <div class="story-body" style="font-size:14px;line-height:1.75;color:#3a3a3a">{e(d.get('earnings_deals_recap',''))}</div>
</div>
<div class="sec"><div class="lbl green">Macro · Policy · World</div><h2>The Bigger Forces at Work</h2>{macro_html}</div>
{reg_section}
<div class="sec"><div class="lbl teal">What to Watch</div><h2>Next Week's Calendar</h2>{watch_html}</div>
<div class="sec"><div class="lbl navy">Yankees</div><h2>Week in the Bronx</h2>
  <div class="ynk">
    <div class="ynk-score">{e(y.get('record',''))}</div>
    <div class="ynk-detail">{e(y.get('summary',''))}</div>
    <div class="ynk-next">▶ Next week: {e(y.get('next_week','Check MLB.com'))}</div>
  </div>
</div>
<div class="closing"><blockquote>"{e(cl.get('text',''))}"</blockquote><cite>{e(cl.get('attribution',''))}</cite></div>
<div class="footer">
  <p>The Weekly Brief &nbsp;·&nbsp; Built for <span>Konner Greer</span> &nbsp;·&nbsp; University of Utah, Finance &amp; Fintech '27</p>
  <p style="margin-top:4px;">Saturday Edition &nbsp;·&nbsp; <span>The Daily Brief</span> returns Monday at 7:00 AM MT</p>
</div>
</div></body></html>"""


# ══════════════════════════════════════════════════════════════════════════
#  LAYER 4 — EMAIL SENDER
# ══════════════════════════════════════════════════════════════════════════

def send_email(subject, html):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = GMAIL_USER
    msg["To"]      = GMAIL_USER
    msg.attach(MIMEText(html, "html"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_USER, GMAIL_PASS)
        server.sendmail(GMAIL_USER, GMAIL_USER, msg.as_string())
    print(f"  ✅ Email sent to {GMAIL_USER}")


# ══════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print(f"\n🌅 Daily Brief — {TODAY} ({'Saturday' if IS_SATURDAY else 'Weekday'} edition)\n")

    if IS_SATURDAY:
        print("[1/3] Gathering week's data...")
        data = gather_saturday_data()
        print("\n[2/3] Claude writing Saturday recap...")
        brief = generate_saturday_briefing(data)
        print("\n[3/3] Rendering & sending...")
        html = render_saturday(brief)
        mon = NOW - timedelta(days=NOW.weekday())
        fri = mon + timedelta(days=4)
        send_email(f"📊 Weekly Brief — Week of {mon.strftime('%b %d')}–{fri.strftime('%b %d')}", html)
    else:
        print("[1/3] Gathering today's data...")
        data = gather_weekday_data()
        print("\n[2/3] Claude writing today's briefing...")
        brief = generate_weekday_briefing(data)
        print("\n[3/3] Rendering & sending...")
        html = render_weekday(brief)
        send_email(f"☀️ Daily Brief — {NOW.strftime('%a %b %d')}", html)

    print("\n✅ Done!\n")
