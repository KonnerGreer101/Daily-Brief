"""
Konner's Daily Brief — v2.0
Rebuilt with All-In inspired structure
Weekdays at 7:00 AM MT  |  Saturday at 8:00 AM MT
"""

import os, json, smtplib, urllib.request, urllib.parse, xml.etree.ElementTree as ET, re
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from zoneinfo import ZoneInfo

# ── Config ──────────────────────────────────────────────────────────────
ANTHROPIC_KEY = os.environ["ANTHROPIC_API_KEY"]
NEWS_KEY      = os.environ["NEWS_API_KEY"]
FINNHUB_KEY   = os.environ.get("FINNHUB_API_KEY", "")
GNEWS_KEY     = os.environ.get("GNEWS_API_KEY", "")
FRED_KEY      = os.environ.get("FRED_API_KEY", "")
FMP_KEY       = os.environ.get("FMP_API_KEY", "")
AV_KEY        = os.environ.get("ALPHA_VANTAGE_API_KEY", "")
GMAIL_USER    = os.environ["GMAIL_USER"]
GMAIL_PASS    = os.environ["GMAIL_APP_PASS"]

MT          = ZoneInfo("America/Denver")
NOW         = datetime.now(MT)
TODAY       = NOW.strftime("%A, %B %d, %Y")
IS_SATURDAY = NOW.weekday() == 5

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/rss+xml, application/xml, application/json, text/xml, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

# Trend radar file — stored in repo root, read/written each run
TREND_RADAR_FILE = "trend_radar.json"


# ══════════════════════════════════════════════════════════════════════════
#  TREND RADAR — persistent narrative tracking
# ══════════════════════════════════════════════════════════════════════════

def load_trend_radar():
    try:
        with open(TREND_RADAR_FILE, "r") as f:
            return json.load(f)
    except:
        return {"narratives": [], "last_updated": ""}

def save_trend_radar(radar_data):
    try:
        with open(TREND_RADAR_FILE, "w") as f:
            json.dump(radar_data, f, indent=2)
        print("  [trend radar] saved")
    except Exception as ex:
        print(f"  [trend radar] save error: {ex}")

def fmt_trend_radar(radar):
    if not radar.get("narratives"):
        return "No active narratives yet — first run."
    lines = []
    for n in radar["narratives"][:5]:
        lines.append(f"• [{n.get('tag','')}] {n.get('narrative','')} — Day {n.get('day_count',1)} — Last signal: {n.get('last_signal','')}")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════
#  LAYER 1A — MARKET DATA (yfinance)
# ══════════════════════════════════════════════════════════════════════════

def fetch_market_data():
    try:
        import yfinance as yf
        tickers = {
            "^GSPC":    "S&P 500",
            "^IXIC":    "Nasdaq",
            "^DJI":     "Dow Jones",
            "^VIX":     "VIX",
            "^IRX":     "2-Yr Yield",
            "^TNX":     "10-Yr Yield",
            "^TYX":     "30-Yr Yield",
            "CL=F":     "WTI Crude",
            "BZ=F":     "Brent Crude",
            "GC=F":     "Gold",
            "BTC-USD":  "Bitcoin",
            "DX-Y.NYB": "Dollar Index",
            "EURUSD=X": "EUR/USD",
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
                        "price":      round(curr, 4 if "USD=X" in symbol else 2),
                        "change_pct": round(pct, 2),
                        "direction":  "up" if pct > 0.05 else ("down" if pct < -0.05 else "flat"),
                    }
            except Exception as ex:
                print(f"    yfinance [{symbol}]: {ex}")

        # Yield curve spread
        if "^TNX" in result and "^IRX" in result:
            spread = round(result["^TNX"]["price"] - result["^IRX"]["price"], 3)
            result["YIELD_CURVE"] = {
                "name":      "10Y-2Y Spread",
                "price":     spread,
                "change_pct": 0,
                "direction": "up" if spread > 0 else "down",
                "inverted":  spread < 0,
            }
        print(f"    [yfinance] {len(result)} instruments")
        return result
    except ImportError:
        print("    yfinance not available")
        return {}


# ══════════════════════════════════════════════════════════════════════════
#  LAYER 1B — FRED API
# ══════════════════════════════════════════════════════════════════════════

FRED_SERIES = {
    "CPIAUCSL":       "CPI",
    "CPILFESL":       "Core CPI",
    "UNRATE":         "Unemployment Rate",
    "FEDFUNDS":       "Fed Funds Rate",
    "T10Y2Y":         "10Y-2Y Spread (FRED)",
    "BAMLH0A0HYM2":   "High Yield Credit Spread",
    "BAMLC0A0CM":     "Investment Grade Spread",
    "UMCSENT":        "Consumer Sentiment",
    "DFF":            "Effective Fed Funds Rate",
}

def fetch_fred_data():
    if not FRED_KEY:
        return {}
    results = {}
    for series_id, name in FRED_SERIES.items():
        try:
            params = urllib.parse.urlencode({
                "series_id": series_id, "api_key": FRED_KEY,
                "file_type": "json", "sort_order": "desc", "limit": 2,
            })
            req = urllib.request.Request(
                f"https://api.stlouisfed.org/fred/series/observations?{params}", headers=HEADERS)
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read().decode())
            obs = [o for o in data.get("observations",[]) if o.get("value") != "."]
            if obs:
                val  = float(obs[0]["value"])
                chg  = round(val - float(obs[1]["value"]), 3) if len(obs) > 1 else None
                results[series_id] = {"name": name, "value": val, "date": obs[0].get("date",""), "change": chg}
        except Exception as ex:
            print(f"    [FRED {series_id}]: {ex}")
    print(f"    [FRED] {len(results)} series")
    return results


# ══════════════════════════════════════════════════════════════════════════
#  LAYER 1C — FMP (earnings calendar + sector performance)
# ══════════════════════════════════════════════════════════════════════════

def fetch_fmp_earnings_calendar(days_ahead=5):
    if not FMP_KEY:
        return []
    try:
        today    = NOW.strftime("%Y-%m-%d")
        end_date = (NOW + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
        params   = urllib.parse.urlencode({"from": today, "to": end_date, "apikey": FMP_KEY})
        req = urllib.request.Request(
            f"https://financialmodelingprep.com/api/v3/earning_calendar?{params}", headers=HEADERS)
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode())
        return [
            {"ticker": i.get("symbol",""), "date": i.get("date",""),
             "eps_est": i.get("epsEstimated",""), "time": i.get("time","")}
            for i in data[:15] if i.get("epsEstimated")
        ]
    except Exception as ex:
        print(f"    [FMP earnings]: {ex}")
        return []

def fetch_fmp_sector_performance():
    if not FMP_KEY:
        return []
    try:
        params = urllib.parse.urlencode({"apikey": FMP_KEY})
        req = urllib.request.Request(
            f"https://financialmodelingprep.com/api/v3/sectors-performance?{params}", headers=HEADERS)
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode())
        sectors = data.get("sectorPerformance") or data
        return [{"sector": s.get("sector",""), "change": s.get("changesPercentage","")} for s in sectors[:11]]
    except Exception as ex:
        print(f"    [FMP sectors]: {ex}")
        return []


# ══════════════════════════════════════════════════════════════════════════
#  LAYER 1D — NITTER RSS (X/Twitter accounts)
# ══════════════════════════════════════════════════════════════════════════

# Confirmed X handles for Konner's feed
X_ACCOUNTS = {
    "litcapital":      "Litquidity",
    "BoringBiz_":      "Boring Business",
    "exec_sum":        "Exec Sum",
    "HighYieldHarry":  "High Yield Harry",
    "BillAckman":      "Bill Ackman",
    "illiquidinsights": "Illiquid Insights",
    "Bondoro":         "Bondoro",
    "Restructuring_":  "Restructuring",
    "Jason":           "Jason Calacanis",
    "chamath":         "Chamath",
    "Geiger_Capital":  "Geiger Capital",
}

# Public Nitter instances — tries each until one works
NITTER_INSTANCES = [
    "https://nitter.net",
    "https://nitter.privacydev.net",
    "https://nitter.poast.org",
    "https://nitter.1d4.us",
]

def fetch_nitter_rss(handle, display_name, max_items=5):
    for instance in NITTER_INSTANCES:
        try:
            url = f"{instance}/{handle}/rss"
            req = urllib.request.Request(url, headers={
                **HEADERS,
                "User-Agent": "Mozilla/5.0 (compatible; RSS Reader)",
            })
            with urllib.request.urlopen(req, timeout=10) as r:
                raw = r.read()
            root    = ET.fromstring(raw)
            entries = root.findall(".//item")
            cutoff  = datetime.now(timezone.utc) - timedelta(hours=26)
            items   = []
            for entry in entries[:max_items * 2]:
                title_el = entry.find("title")
                title = (title_el.text or "").strip() if title_el is not None else ""
                if not title or len(title) < 15:
                    continue
                # Filter out pure retweets and empty image posts
                if title.startswith("RT @"):
                    continue

                pub_el  = entry.find("pubDate")
                pub_str = pub_el.text.strip() if pub_el is not None and pub_el.text else ""
                pub_dt  = None
                for fmt in ["%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S GMT"]:
                    try:
                        pub_dt = datetime.strptime(pub_str[:30], fmt[:len(pub_str[:30])])
                        if pub_dt.tzinfo is None:
                            pub_dt = pub_dt.replace(tzinfo=timezone.utc)
                        break
                    except:
                        continue
                if pub_dt and pub_dt < cutoff:
                    continue

                # Clean HTML from title
                title = re.sub(r'<[^>]+>', '', title).strip()
                items.append({
                    "title":       title,
                    "description": "",
                    "source":      f"@{handle} ({display_name})",
                    "published":   pub_str[:16],
                })
                if len(items) >= max_items:
                    break

            if items:
                print(f"    [nitter @{handle}] {len(items)} posts via {instance}")
                return items
        except Exception as ex:
            continue  # Try next instance
    print(f"    [nitter @{handle}] all instances failed")
    return []

def fetch_all_x_feeds():
    """Fetch posts from all curated X accounts."""
    all_posts = []
    for handle, name in X_ACCOUNTS.items():
        posts = fetch_nitter_rss(handle, name, max_items=4)
        all_posts.extend(posts)
    print(f"    [X feeds total] {len(all_posts)} posts")
    return all_posts


# ══════════════════════════════════════════════════════════════════════════
#  LAYER 1E — RSS FEEDS
# ══════════════════════════════════════════════════════════════════════════

RSS_FEEDS = {
    # Finance
    "marketwatch_top":  "https://feeds.marketwatch.com/marketwatch/topstories/",
    "marketwatch_mk":   "https://feeds.marketwatch.com/marketwatch/marketpulse/",
    "ft_home":          "https://www.ft.com/rss/home",
    "seeking_alpha":    "https://seekingalpha.com/feed.xml",
    # Global news
    "bbc_world":        "https://feeds.bbci.co.uk/news/world/rss.xml",
    "bbc_business":     "https://feeds.bbci.co.uk/news/business/rss.xml",
    "bbc_tech":         "https://feeds.bbci.co.uk/news/technology/rss.xml",
    "guardian_biz":     "https://www.theguardian.com/business/rss",
    "guardian_tech":    "https://www.theguardian.com/technology/rss",
    "guardian_world":   "https://www.theguardian.com/world/rss",
    "npr_business":     "https://feeds.npr.org/1006/rss.xml",
    "npr_tech":         "https://feeds.npr.org/1019/rss.xml",
    # Tech / AI
    "techmeme":         "https://www.techmeme.com/feed.xml",
    "venturebeat":      "https://venturebeat.com/feed/",
    "wired_ai":         "https://www.wired.com/feed/tag/artificial-intelligence/rss",
    # Science / Space
    # "nasa": removed — rate limited
    "science_daily":    "https://www.sciencedaily.com/rss/top/science.xml",
    "space_com":        "https://www.space.com/feeds/all",
    # Crypto
    "coindesk":         "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "cointelegraph":    "https://cointelegraph.com/rss",
    # Policy / Gov
    "politico":         "https://www.politico.com/rss/politicopicks.xml",
    # SEC
    "sec_litigation":   "https://www.sec.gov/rss/litigation/litreleases.xml",
    "sec_enforcement":  "https://www.sec.gov/rss/litigation/admin.xml",
    # Utah
    "slc_tribune":      "https://www.sltrib.com/feed/",
    "deseret_news":     "https://www.deseret.com/arc/outboundfeeds/rss/",
    # Deal Flow — M&A, VC, IPO, PE, Secondaries
    "axios_deals":      "https://www.axios.com/feeds/feed.rss",
    "techcrunch_fundr": "https://techcrunch.com/category/fundings-exits/feed/",
    "techcrunch_ma":    "https://techcrunch.com/category/mergers-acquisitions/feed/",
    "crunchbase_news":  "https://news.crunchbase.com/feed/",
    "reuters_ma":       "https://feeds.reuters.com/reuters/mergersNews",
    "sec_s1":           "https://efts.sec.gov/LATEST/search-index?q=%22S-1%22&dateRange=custom&startdt={from_date}&forms=S-1&_source=hits.hits._source.period_of_report,hits.hits._source.entity_name,hits.hits._source.file_date&hits.hits.total.value=true",
    "sec_8k_ma":        "https://efts.sec.gov/LATEST/search-index?q=%22Agreement+and+Plan+of+Merger%22&forms=8-K&_source=hits.hits._source",
    "ft_deals":         "https://www.ft.com/rss/home/uk",
    "wsj_deals":        "https://feeds.content.dowjones.io/public/rss/mw_realtimeheadlines",
    # Yankees
    "mlb_yankees":      "https://www.mlb.com/feeds/news/rss.xml?teamId=147",
    "espn_mlb":         "https://www.espn.com/espn/rss/mlb/news",
}

SOURCE_NAMES = {
    "marketwatch_top": "MarketWatch", "marketwatch_mk": "MarketWatch",
    "ft_home": "FT", "seeking_alpha": "Seeking Alpha",
    "bbc_world": "BBC", "bbc_business": "BBC", "bbc_tech": "BBC",
    "guardian_biz": "The Guardian", "guardian_tech": "The Guardian",
    "guardian_world": "The Guardian", "npr_business": "NPR", "npr_tech": "NPR",
    "techmeme": "Techmeme", "venturebeat": "VentureBeat", "wired_ai": "Wired",
    "nasa": "NASA", "science_daily": "ScienceDaily", "space_com": "Space.com",
    "coindesk": "CoinDesk", "cointelegraph": "CoinTelegraph",
    "politico": "Politico",
    "sec_litigation": "SEC", "sec_enforcement": "SEC",
    "slc_tribune": "SL Tribune", "deseret_news": "Deseret News",
    "mlb_yankees": "MLB", "espn_mlb": "ESPN",
    "axios_deals": "Axios Pro Rata", "techcrunch_fundr": "TechCrunch",
    "techcrunch_ma": "TechCrunch", "crunchbase_news": "Crunchbase",
    "reuters_ma": "Reuters", "sec_s1": "SEC EDGAR",
    "sec_8k_ma": "SEC EDGAR", "ft_deals": "FT", "wsj_deals": "WSJ/MarketWatch",
}

def fetch_rss(feed_key, max_items=8, max_age_hours=30):
    url = RSS_FEEDS.get(feed_key)
    if not url:
        return []
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=12) as r:
            raw = r.read()
        root    = ET.fromstring(raw)
        ns      = {"atom": "http://www.w3.org/2005/Atom"}
        entries = root.findall(".//item") or root.findall(".//atom:entry", ns)
        cutoff  = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
        items   = []
        for entry in entries[:max_items * 2]:
            title_el = entry.find("title") or entry.find("atom:title", ns)
            title = (title_el.text or "").strip() if title_el is not None else ""
            if not title or "[Removed]" in title:
                continue
            desc_el = entry.find("description") or entry.find("summary") or entry.find("atom:summary", ns)
            desc = re.sub(r'<[^>]+>', '', (desc_el.text or "") if desc_el is not None else "").strip()[:200]
            pub_el  = entry.find("pubDate") or entry.find("published") or entry.find("atom:published", ns)
            pub_str = pub_el.text.strip() if pub_el is not None and pub_el.text else ""
            pub_dt  = None
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

def fetch_rss_multi(keys, max_per_feed=5, max_age_hours=30):
    results = []
    for key in keys:
        results.extend(fetch_rss(key, max_items=max_per_feed, max_age_hours=max_age_hours))
    return results


# ══════════════════════════════════════════════════════════════════════════
#  LAYER 1F — NEWSAPI
# ══════════════════════════════════════════════════════════════════════════

def newsapi_search(query, page_size=8, days_back=1):
    since = (datetime.utcnow() - timedelta(days=days_back)).strftime("%Y-%m-%dT%H:%M:%SZ")
    params = urllib.parse.urlencode({
        "q": query, "from": since, "sortBy": "publishedAt",
        "pageSize": page_size, "language": "en", "apiKey": NEWS_KEY,
    })
    try:
        req = urllib.request.Request(f"https://newsapi.org/v2/everything?{params}", headers=HEADERS)
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode())
        articles = [
            {"title": a.get("title",""), "description": (a.get("description") or "")[:200],
             "source": a.get("source",{}).get("name",""), "published": a.get("publishedAt","")[:16]}
            for a in data.get("articles",[])
            if a.get("title") and "[Removed]" not in a.get("title","")
        ]
        print(f"    [newsapi: {query[:35]}] {len(articles)}")
        return articles
    except Exception as ex:
        print(f"    NewsAPI: {ex}")
        return []

def newsapi_headlines(category="business", page_size=8):
    params = urllib.parse.urlencode({
        "category": category, "country": "us", "pageSize": page_size, "apiKey": NEWS_KEY,
    })
    try:
        req = urllib.request.Request(f"https://newsapi.org/v2/top-headlines?{params}", headers=HEADERS)
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode())
        articles = [
            {"title": a.get("title",""), "description": (a.get("description") or "")[:200],
             "source": a.get("source",{}).get("name",""), "published": a.get("publishedAt","")[:16]}
            for a in data.get("articles",[])
            if a.get("title") and "[Removed]" not in a.get("title","")
        ]
        print(f"    [newsapi headlines:{category}] {len(articles)}")
        return articles
    except Exception as ex:
        print(f"    NewsAPI headlines: {ex}")
        return []


# ══════════════════════════════════════════════════════════════════════════
#  LAYER 1G — FINNHUB
# ══════════════════════════════════════════════════════════════════════════

def finnhub_news(category="general"):
    if not FINNHUB_KEY:
        return []
    try:
        params = urllib.parse.urlencode({"category": category, "token": FINNHUB_KEY})
        req = urllib.request.Request(f"https://finnhub.io/api/v1/news?{params}", headers=HEADERS)
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode())
        cutoff = datetime.now(timezone.utc) - timedelta(hours=26)
        articles = []
        for a in data:
            ts     = a.get("datetime", 0)
            pub_dt = datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None
            if pub_dt and pub_dt < cutoff:
                continue
            headline = a.get("headline","").strip()
            if headline:
                articles.append({
                    "title": headline, "description": (a.get("summary","") or "")[:200],
                    "source": a.get("source","Finnhub"),
                    "published": pub_dt.strftime("%Y-%m-%dT%H:%M") if pub_dt else "",
                })
        print(f"    [finnhub:{category}] {len(articles)}")
        return articles
    except Exception as ex:
        print(f"    [finnhub]: {ex}")
        return []


# ══════════════════════════════════════════════════════════════════════════
#  LAYER 1H — GNEWS
# ══════════════════════════════════════════════════════════════════════════

def gnews_search(query, max_results=8):
    if not GNEWS_KEY:
        return []
    try:
        params = urllib.parse.urlencode({
            "q": query, "lang": "en", "country": "us",
            "max": max_results, "apikey": GNEWS_KEY,
        })
        req = urllib.request.Request(f"https://gnews.io/api/v4/search?{params}", headers=HEADERS)
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode())
        articles = [
            {"title": (a.get("title") or "").strip(),
             "description": (a.get("description") or "")[:200],
             "source": a.get("source",{}).get("name","GNews"),
             "published": (a.get("publishedAt") or "")[:16]}
            for a in data.get("articles",[]) if a.get("title")
        ]
        print(f"    [gnews:{query[:30]}] {len(articles)}")
        return articles
    except Exception as ex:
        print(f"    [gnews]: {ex}")
        return []

def gnews_top(topic="business", max_results=8):
    if not GNEWS_KEY:
        return []
    try:
        params = urllib.parse.urlencode({
            "topic": topic, "lang": "en", "country": "us",
            "max": max_results, "apikey": GNEWS_KEY,
        })
        req = urllib.request.Request(f"https://gnews.io/api/v4/top-headlines?{params}", headers=HEADERS)
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode())
        articles = [
            {"title": (a.get("title") or "").strip(),
             "description": (a.get("description") or "")[:200],
             "source": a.get("source",{}).get("name","GNews"),
             "published": (a.get("publishedAt") or "")[:16]}
            for a in data.get("articles",[]) if a.get("title")
        ]
        print(f"    [gnews top:{topic}] {len(articles)}")
        return articles
    except Exception as ex:
        print(f"    [gnews top]: {ex}")
        return []


# ══════════════════════════════════════════════════════════════════════════
#  LAYER 1I — FORMATTERS
# ══════════════════════════════════════════════════════════════════════════

def fmt_articles(articles, n=12):
    """Deduplicated article list for Claude prompt."""
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
        return "No market data — markets may be closed."
    lines = []
    for sym in ["^GSPC","^IXIC","^DJI"]:
        if sym in md:
            info  = md[sym]
            arrow = "▲" if info["direction"]=="up" else ("▼" if info["direction"]=="down" else "–")
            lines.append(f"• {info['name']}: {info['price']:,.2f} ({arrow}{info['change_pct']:+.2f}%)")
    if "^VIX" in md:
        info  = md["^VIX"]
        arrow = "▲" if info["direction"]=="up" else ("▼" if info["direction"]=="down" else "–")
        level = "HIGH FEAR" if info["price"] > 30 else ("elevated" if info["price"] > 20 else "calm")
        lines.append(f"• VIX: {info['price']:.2f} ({arrow}{info['change_pct']:+.2f}%) — {level}")
    for sym in ["^IRX","^TNX","^TYX"]:
        if sym in md:
            info  = md[sym]
            arrow = "▲" if info["direction"]=="up" else ("▼" if info["direction"]=="down" else "–")
            lines.append(f"• {info['name']}: {info['price']:.2f}% ({arrow}{info['change_pct']:+.2f}%)")
    if "YIELD_CURVE" in md:
        yc  = md["YIELD_CURVE"]
        inv = " ⚠️ INVERTED" if yc.get("inverted") else ""
        lines.append(f"• 10Y-2Y Spread: {yc['price']:+.3f}%{inv}")
    for sym in ["CL=F","BZ=F","GC=F"]:
        if sym in md:
            info  = md[sym]
            arrow = "▲" if info["direction"]=="up" else ("▼" if info["direction"]=="down" else "–")
            lines.append(f"• {info['name']}: ${info['price']:,.2f} ({arrow}{info['change_pct']:+.2f}%)")
    for sym in ["BTC-USD","DX-Y.NYB","EURUSD=X"]:
        if sym in md:
            info  = md[sym]
            arrow = "▲" if info["direction"]=="up" else ("▼" if info["direction"]=="down" else "–")
            lines.append(f"• {info['name']}: {info['price']:,.2f} ({arrow}{info['change_pct']:+.2f}%)")
    return "\n".join(lines)


def fmt_fred_data(fred):
    if not fred:
        return "FRED data unavailable."
    lines = []
    for sid, d in fred.items():
        chg = f" (Δ {d['change']:+.3f})" if d.get("change") is not None else ""
        lines.append(f"• {d['name']}: {d['value']}{chg} (as of {d['date']})")
    return "\n".join(lines)


def fmt_sectors(sectors):
    if not sectors:
        return "Sector data unavailable."
    lines = []
    for s in sectors:
        try:
            val   = float(str(s.get("change","0")).replace("%",""))
            arrow = "▲" if val > 0 else "▼"
            lines.append(f"• {s['sector']}: {arrow}{abs(val):.2f}%")
        except:
            lines.append(f"• {s['sector']}: {s.get('change','')}")
    return "\n".join(lines)


def fmt_earnings_calendar(cal):
    if not cal:
        return "No upcoming earnings data."
    lines = []
    for e in cal[:8]:
        eps = f" · EPS est ${e['eps_est']}" if e.get("eps_est") else ""
        lines.append(f"• {e['ticker']} — {e['date']} {e.get('time','')}{eps}")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════
#  LAYER 1J — DATA GATHERERS
# ══════════════════════════════════════════════════════════════════════════

def gather_weekday_data():
    print("\n  → Market data (yfinance)...")
    market_data = fetch_market_data()

    print("\n  → FRED macro indicators...")
    fred_data = fetch_fred_data()

    print("\n  → FMP earnings calendar & sectors...")
    earnings_cal = fetch_fmp_earnings_calendar(days_ahead=5)
    sectors      = fetch_fmp_sector_performance()

    print("\n  → AI & Compute...")
    ai  = fetch_rss_multi(["techmeme","venturebeat","wired_ai","bbc_tech","guardian_tech","npr_tech"], max_per_feed=5)
    ai += newsapi_search("artificial intelligence OpenAI Anthropic Google DeepMind Nvidia chips LLM agents", page_size=8)
    ai += newsapi_search("AI infrastructure compute datacenter GPU semiconductor funding", page_size=6)
    ai += gnews_search("AI model release compute chips funding", max_results=6)

    print("\n  → Markets & Economy...")
    markets  = newsapi_headlines(category="business", page_size=8)
    markets += newsapi_search("stock market S&P 500 earnings Wall Street equities IPO M&A deal", page_size=5)
    markets += newsapi_search("earnings results revenue EPS beat miss guidance raised", page_size=5)
    markets += newsapi_search("merger acquisition IPO fundraise venture capital billion", page_size=5)
    markets += finnhub_news(category="general")
    markets += fetch_rss_multi(["marketwatch_top","marketwatch_mk","ft_home","seeking_alpha"], max_per_feed=4)
    markets += gnews_top(topic="business", max_results=6)

    print("\n  → Government, Policy & Regulation...")
    policy  = newsapi_search("Federal Reserve rate decision CPI inflation GDP jobs trade tariffs", page_size=6)
    policy += newsapi_search("Congress Senate White House executive order legislation policy", page_size=5)
    policy += newsapi_search("state governor economic policy major legislation US", page_size=4)
    policy += fetch_rss_multi(["politico","npr_business"], max_per_feed=5)
    policy += gnews_search("US government policy Federal Reserve regulation", max_results=5)
    # SEC/DOJ — only pulled when major
    policy += fetch_rss_multi(["sec_litigation","sec_enforcement"], max_per_feed=3)
    policy += newsapi_search("SEC DOJ fraud indictment billion charged financial crime systemic", page_size=4)

    print("\n  → Crypto & Fintech...")
    crypto  = fetch_rss_multi(["coindesk","cointelegraph"], max_per_feed=5)
    crypto += newsapi_search("Bitcoin Ethereum crypto stablecoin DeFi blockchain fintech payments", page_size=6)
    crypto += newsapi_search("fintech regulation CBDC stablecoin legislation digital assets", page_size=5)
    crypto += gnews_search("Bitcoin crypto stablecoin fintech", max_results=5)

    print("\n  → Science & Space...")
    science  = fetch_rss_multi(["nasa","science_daily","space_com"], max_per_feed=5)
    science += newsapi_search("SpaceX launch NASA space mission breakthrough", page_size=5)
    science += newsapi_search("biotech GLP-1 obesity drug FDA approval clinical trial", page_size=5)
    science += newsapi_search("robotics quantum computing fusion energy breakthrough longevity", page_size=4)
    science += gnews_top(topic="science", max_results=5)

    print("\n  → Deal Flow (M&A, VC, IPO, PE, Secondaries)...")
    deals  = fetch_rss_multi(["axios_deals","techcrunch_fundr","techcrunch_ma","crunchbase_news","reuters_ma"], max_per_feed=6)
    deals += newsapi_search("merger acquisition M&A takeover buyout agreed signed billion deal", page_size=7)
    deals += newsapi_search("venture capital Series A B C funding raised startup investment round", page_size=7)
    deals += newsapi_search("IPO initial public offering S-1 filed listing debut priced valuation", page_size=6)
    deals += newsapi_search("block trade secondary offering PE fund raise debt issuance bond", page_size=5)
    deals += newsapi_search("private equity fund close LP commit secondary GP-led NAV lending", page_size=5)
    deals += finnhub_news(category="merger")
    deals += gnews_search("merger acquisition IPO venture capital funding round", max_results=6)

    print("\n  → Trending on X (curated accounts)...")
    x_posts = fetch_all_x_feeds()

    print("\n  → Utah & Regional Economy...")
    utah  = fetch_rss_multi(["slc_tribune","deseret_news"], max_per_feed=5)
    utah += newsapi_search("Utah Silicon Slopes tech startup data center Salt Lake City economy", page_size=5, days_back=3)
    utah += newsapi_search("Entrata Podium Divvy Recursion Pharmaceuticals Domo Utah tech", page_size=4, days_back=7)

    return {
        "date":          TODAY,
        "market_data":   market_data,
        "fred_data":     fred_data,
        "earnings_cal":  earnings_cal,
        "sectors":       sectors,
        "ai":            ai,
        "markets":       markets,
        "deals":         deals,
        "policy":        policy,
        "crypto":        crypto,
        "science":       science,
        "x_posts":       x_posts,
        "utah":          utah,
    }


def gather_saturday_data():
    print("\n  → Real-time prices...")
    market_data = fetch_market_data()
    fred_data   = fetch_fred_data()

    print("\n  → Week's AI & Compute...")
    ai  = fetch_rss_multi(["techmeme","venturebeat","wired_ai"], max_per_feed=5, max_age_hours=150)
    ai += newsapi_search("AI artificial intelligence OpenAI Anthropic Nvidia chips", page_size=8, days_back=6)

    print("\n  → Week's Markets...")
    markets  = newsapi_headlines(category="business", page_size=8)
    markets += newsapi_search("stock market earnings IPO M&A deal acquisition weekly", page_size=8, days_back=6)
    markets += fetch_rss_multi(["marketwatch_top","ft_home","seeking_alpha"], max_per_feed=5, max_age_hours=150)

    print("\n  → Week's Policy...")
    policy  = newsapi_search("Federal Reserve inflation GDP trade tariffs Congress policy", page_size=6, days_back=6)
    policy += fetch_rss_multi(["politico","npr_business"], max_per_feed=4, max_age_hours=150)

    print("\n  → Week's Crypto...")
    crypto  = fetch_rss_multi(["coindesk","cointelegraph"], max_per_feed=4, max_age_hours=150)
    crypto += newsapi_search("Bitcoin crypto stablecoin fintech regulation", page_size=5, days_back=6)

    print("\n  → Week's Science...")
    science  = fetch_rss_multi(["nasa","science_daily","space_com"], max_per_feed=4, max_age_hours=150)
    science += newsapi_search("SpaceX biotech GLP-1 AI robotics quantum space", page_size=5, days_back=6)

    print("\n  → Global news...")
    global_news  = newsapi_headlines(category="general", page_size=6)
    global_news += newsapi_search("geopolitics war China Russia Europe Middle East election", page_size=6, days_back=6)
    global_news += fetch_rss_multi(["bbc_world","guardian_world"], max_per_feed=5, max_age_hours=150)

    print("\n  → Next week calendar...")
    calendar = newsapi_search("CPI FOMC Fed earnings next week economic calendar", page_size=5, days_back=3)
    calendar += fetch_fmp_earnings_calendar(days_ahead=7)

    return {
        "date":        TODAY,
        "market_data": market_data,
        "fred_data":   fred_data,
        "ai":          ai,
        "markets":     markets,
        "policy":      policy,
        "crypto":      crypto,
        "science":     science,
        "global_news": global_news,
        "calendar":    calendar,
    }


# ══════════════════════════════════════════════════════════════════════════
#  LAYER 2 — CLAUDE AGENT
# ══════════════════════════════════════════════════════════════════════════

def call_claude(system_prompt, user_prompt, max_tokens=5000):
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
        with urllib.request.urlopen(req, timeout=180) as r:
            data = json.loads(r.read().decode())
        return data["content"][0]["text"]
    except urllib.error.HTTPError as ex:
        body = ex.read().decode()
        print(f"  Anthropic error {ex.code}: {body}")
        raise


SYSTEM_PROMPT = """You are the writer of "The Daily Brief" — a personal morning newsletter for Konner Greer, a Finance & Fintech student at the University of Utah (graduating December 2027). He interns at University of Utah Financial Services and is building toward a career in finance, fintech, or financial regulation.

AUDIENCE & VOICE:
- Write like a well-informed tech investor and senior analyst — opinionated, sharp, direct
- NOT a news anchor — synthesize, connect dots, take a point of view
- Student-friendly: explain every piece of jargon in plain English when first used
- Always connect stories to the bigger picture: valuations, capital flows, policy implications
- What happened → why it happened → why it matters → what to watch

MARKET DASHBOARD — STUDENT-FRIENDLY RULES:
- Never just show a number. Every metric gets a plain-English "story" sentence
- Treasury yields: explain what "high" means right now — mortgage rates, corporate borrowing, equity multiples, govt debt service
- VIX: explain the fear level in plain terms a student can repeat in conversation
- Yield curve: explain what inversion/steepening signals and why it matters
- Credit spreads: explain as "the price of risk" — tight = confidence, wide = fear
- If 10-yr yield is above 4.25%, flag it explicitly as elevated and explain the ripple effects

SECTION RULES:
- Each section covers COMPLETELY DIFFERENT stories — zero repetition across sections
- Government/Policy: cover broad US governance, major state news, Fed, trade — include SEC/DOJ ONLY if genuinely market-moving (billion-dollar fraud, rate decision, systemic shift)
- Utah section: ONLY include if there is real, specific local news — skip entirely if nothing material
- Trending on X: summarize the 3-5 most interesting/relevant signals from the curated feed — include sharp takes, memes with substance, and developing narratives. Flag if a meme account is onto something real.
- Slow news day: say so in the opening — never pad sections with filler

TREND RADAR:
- Read yesterday's active narratives carefully
- Update each narrative with today's signal
- Add new narratives if a genuinely new thread is emerging
- Drop narratives that have resolved or gone quiet (>5 days no signal)
- Be predictive: where is each narrative heading?

OUTPUT: Valid JSON only. No markdown, no preamble, no code fences. Raw JSON object only.
"""


def generate_weekday_briefing(data, trend_radar):
    sectors_str  = fmt_sectors(data.get("sectors", []))
    earnings_str = fmt_earnings_calendar(data.get("earnings_cal", []))

    user_prompt = f"""Today is {data['date']}. Write today's Daily Brief.

Return a JSON object with EXACTLY these keys:

{{
  "opening": ["3-5 short bullets, each one punchy line on a defining theme of the day — opinionated, takes a view. NOT a paragraph."],

  "market_dashboard": {{
    "tiles": [
      {{"name": "asset name", "value": "price or level", "change": "+/-X.X%", "direction": "up/down/flat",
        "story": "1-2 sentences in plain English explaining what this number means TODAY and why it matters"}}
    ],
    "yield_curve_story": "2-3 sentences explaining the yield curve right now — is it inverted? steepening? what does it signal for the economy and markets? If 10-yr is above 4.25%, explain the ripple effects on mortgages, corporate debt, equity multiples, and government interest payments.",
    "macro_snapshot": "2-3 sentences on the most important FRED data points — CPI trend, unemployment, credit spreads. Plain English, student-friendly."
  }},

  "ai_compute": [
    {{"headline": "Sharp headline",
      "bullet_what": "One sentence: what happened — specific, factual",
      "bullet_why": "One sentence: why it matters — valuation, competition, or infrastructure angle",
      "bullet_watch": "One sentence: what signal to watch for next"}}
  ],

  "markets_economy": {{
    "earnings": [
      {{"ticker": "TICKER", "company": "Name", "headline": "Sharp headline",
        "bullet_what": "One sentence: EPS vs estimate, revenue, key guidance number",
        "bullet_why": "One sentence: business context — why did they beat/miss",
        "bullet_watch": "One sentence: sector read-through or what to watch next quarter"}}
    ],
    "movers": [
      {{"name": "Asset", "change": "exact value", "direction": "up/down/flat",
        "reason": "One sentence — connect to catalyst"}}
    ],
    "deals": [
      {{"type": "M&A/IPO/Fundraise", "headline": "Sharp headline",
        "what": "Deal details", "matters": "Strategic rationale, implied premium, sector signal"}}
    ],
    "earnings_preview": "1-2 sentences on what's reporting later this week"
  }},

  "deal_flow": {{
    "ma": [
      {{"headline": "Sharp headline", "parties": "Acquirer / Target",
        "size": "Deal size if known",
        "bullet_what": "One sentence: deal details and structure",
        "bullet_why": "One sentence: strategic rationale and implied premium if known"}}
    ],
    "vc": [
      {{"company": "Company name", "stage": "Series A/B/C/Growth/Seed",
        "amount": "Raise amount", "investors": "Lead investor(s)",
        "bullet_what": "One sentence: what the company does",
        "bullet_why": "One sentence: why this round is notable — valuation signal or sector trend"}}
    ],
    "ipo_listings": [
      {{"company": "Company name", "type": "IPO/Direct Listing/Block Trade/Debt Issuance",
        "size": "Offering size",
        "bullet_what": "One sentence: what happened",
        "bullet_why": "One sentence: what it signals about market appetite"}}
    ],
    "funds_secondaries": [
      {{"firm": "Firm name", "type": "Fund Close/Secondary/GP-Led/NAV Lending",
        "size": "Fund or deal size",
        "bullet_what": "One sentence: what happened",
        "bullet_why": "One sentence: what it signals about LP sentiment or private market dynamics"}}
    ]
  }},

  "government_policy": [
    {{"tag": "Fed/Trade/Congress/State/SEC-DOJ", "headline": "Sharp headline",
      "bullet_what": "One sentence: what happened",
      "bullet_why": "One sentence: market or policy implication",
      "bullet_watch": "One sentence: what to watch as this develops"}}
  ],

  "crypto_fintech": [
    {{"headline": "Sharp headline",
      "bullet_what": "One sentence: what happened",
      "bullet_why": "One sentence: why it matters for crypto or fintech"}}
  ],

  "science_space": [
    {{"headline": "Sharp headline",
      "bullet_what": "One sentence: what happened",
      "bullet_why": "One sentence: investment or policy implication"}}
  ],

  "trending_x": {{
    "has_content": true or false,
    "signals": [
      {{"account": "@handle", "signal": "What they said or the meme/take that's trending",
        "why_it_matters": "Is this onto something real? Explain in 1-2 sentences."}}
    ],
    "x_note": "1 sentence overall read on what the X feed is focused on today"
  }},

  "utah_regional": {{
    "has_news": true or false,
    "headline": "Story headline if has_news is true, else empty string",
    "bullet_what": "One sentence: what happened locally",
    "bullet_why": "One sentence: why it connects to broader trends (AI, fintech, regional economy)"
  }},

  "worth_watching": [
    {{"item": "Forward-looking item to follow", "why": "Why this matters and what signal to watch for"}}
  ],

  "trend_radar": {{
    "narratives": [
      {{"tag": "Short tag e.g. AI-Infrastructure-Capex", "narrative": "One sentence describing the thread",
        "day_count": number of days tracked,
        "last_signal": "What happened today that relates to this narrative",
        "direction": "escalating/stable/resolving"}}
    ],
    "new_this_week": "1 sentence on any new narrative thread emerging today that wasn't in yesterday's radar"
  }},

  "term_of_the_day": {{
    "term": "Finance or econ term",
    "definition": "2-3 sentences in plain English — no jargon in the definition itself",
    "context": "1 sentence connecting this term directly to today's biggest story"
  }},

  "one_thing": {{
    "headline": "The developing story worth following",
    "bullets": ["What's happening (one line)", "Why it matters (one line)", "The bigger picture (one line)", "What signal to watch for next (one line)"]
  }}
}}

RULES:
- market_dashboard tiles: use REAL prices from market data below. Include S&P, Nasdaq, Dow, VIX, 2-Yr, 10-Yr, yield curve spread, WTI, Brent, Gold, Bitcoin. Every tile MUST have a story sentence.
- ai_compute: 2-4 items, no overlap with markets_economy
- markets_economy earnings: 2-4 companies. movers: 5-7 using real prices. deals: 1-3 if any real deals.
- deal_flow: populate each sub-bucket with what's real from the source data. ma: 1-3 deals. vc: 2-4 rounds (prioritize notable size or investor). ipo_listings: 1-3 (include block trades and debt deals when notable). funds_secondaries: 1-2 if any real fund closes or secondaries. Return empty array [] for any sub-bucket with no real content today — never pad.
- government_policy: 2-4 items. SEC/DOJ only if major.
- crypto_fintech: 1-3 items. Skip if nothing real today — return []
- science_space: 1-3 items. Skip if nothing real — return []
- trending_x: use actual posts from the X feed below. If no good posts, set has_content to false and signals to []
- utah_regional: ONLY if real specific news — otherwise has_news: false, story: ""
- worth_watching: exactly 3 items. TODAY IS {TODAY}. ONLY include events that are in the FUTURE — never past events or things that already happened. If a calendar item like "Memorial Day" or any other event has already passed, skip it entirely and pick something genuinely upcoming instead.
- trend_radar: update from yesterday's narratives + add new ones. day_count should increment from yesterday.
- term_of_the_day: pick the most relevant concept from today's dominant story
- one_thing: the single most interesting developing story — something with a bigger picture arc

--- YESTERDAY'S TREND RADAR (update this) ---
{fmt_trend_radar(trend_radar)}

--- REAL-TIME MARKET PRICES ---
{fmt_market_data(data.get('market_data', {}))}

--- FRED MACRO DATA ---
{fmt_fred_data(data.get('fred_data', {}))}

--- S&P 500 SECTOR PERFORMANCE ---
{sectors_str}

--- UPCOMING EARNINGS CALENDAR ---
{earnings_str}

--- AI & COMPUTE ---
{fmt_articles(data['ai'], 8)}

--- MARKETS & ECONOMY ---
{fmt_articles(data['markets'], 8)}

--- GOVERNMENT, POLICY & REGULATION ---
{fmt_articles(data['policy'], 6)}

--- CRYPTO & FINTECH ---
{fmt_articles(data['crypto'], 7)}

--- SCIENCE & SPACE ---
{fmt_articles(data['science'], 7)}

--- TRENDING ON X (curated accounts: Litquidity, Ackman, Chamath, Exec Sum, Geiger Capital, etc.) ---
{fmt_articles(data.get('x_posts', []), 10)}

--- DEAL FLOW (M&A, VC, IPO, PE, SECONDARIES — Axios Pro Rata, TechCrunch, Reuters, Crunchbase, SEC EDGAR) ---
{fmt_articles(data.get('deals', []), 12)}

--- UTAH & REGIONAL ECONOMY ---
{fmt_articles(data.get('utah', []), 5)}
"""
    raw = call_claude(SYSTEM_PROMPT, user_prompt, max_tokens=8000)
    raw = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
    # Safety: if JSON is truncated, try to fix it
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Truncation recovery: find last complete top-level key and close the JSON
        print("  ⚠️ JSON truncated — attempting recovery...")
        # Find the last complete closing brace/bracket pair
        last_good = max(raw.rfind('}},'), raw.rfind('},'), raw.rfind(']},'), raw.rfind('"]'))
        if last_good > 0:
            truncated = raw[:last_good + 2]
            # Count open braces vs closed
            opens = truncated.count('{') - truncated.count('}')
            closes = truncated.count('[') - truncated.count(']')
            recovery = truncated + (']' * max(0, closes)) + ('}' * max(0, opens))
            try:
                return json.loads(recovery)
            except:
                pass
        # Last resort: return minimal valid structure
        print("  ❌ JSON recovery failed — returning minimal structure")
        return {
            "opening": "Brief generation encountered an error today. Markets data and key stories were pulled but the summary could not be completed.",
            "market_dashboard": {"tiles": [], "yield_curve_story": "", "macro_snapshot": ""},
            "ai_compute": [], "markets_economy": {"earnings": [], "movers": [], "deals": [], "earnings_preview": ""},
            "deal_flow": {"ma": [], "vc": [], "ipo_listings": [], "funds_secondaries": []},
            "government_policy": [], "crypto_fintech": [], "science_space": [],
            "trending_x": {"has_content": False, "signals": [], "x_note": ""},
            "utah_regional": {"has_news": False, "headline": "", "bullet_what": "", "bullet_why": ""},
            "worth_watching": [{"item": "Check back tomorrow", "why": "Brief generation encountered an issue today"}],
            "trend_radar": {"narratives": [], "new_this_week": ""},
            "term_of_the_day": {"term": "N/A", "definition": "Brief unavailable today.", "context": ""},
            "one_thing": {"headline": "Brief unavailable today", "body": "An error occurred during generation. Check the GitHub Actions log for details."}
        }


def generate_saturday_briefing(data, trend_radar):
    mon = NOW - timedelta(days=NOW.weekday())
    fri = mon + timedelta(days=4)
    week_range = f"{mon.strftime('%B %d')}–{fri.strftime('%B %d, %Y')}"

    user_prompt = f"""Today is {data['date']} (Saturday). Write the Weekly Brief for the week of {week_range}.

Return a JSON object with EXACTLY these keys:

{{
  "week_range": "{week_range}",
  "opening": ["3-5 short bullets, each one punchy line on what defined this week — opinionated. NOT a paragraph."],

  "themes": [
    {{"title": "Theme title",
      "what": "One sentence: what happened — specific and factual",
      "why": "One sentence: why it matters — the implication for markets, policy, or the economy",
      "watch": "One sentence: what signal to watch for next"}}
  ],

  "scoreboard": [
    {{"name": "Asset", "value": "Price/level", "change": "WTD change", "direction": "up/down/flat",
      "story": "1 sentence on what this move means"}}
  ],

  "macro_wrap": {{
    "yield_story": "2-3 sentences on where Treasury yields ended the week and what it means",
    "fred_highlights": "2-3 sentences on key economic data released this week",
    "sector_rotation": "1-2 sentences on what led and what lagged"
  }},

  "ai_week": ["3-5 bullets covering the week's biggest AI and tech developments — each a self-contained point with the development and why it matters. NOT paragraphs."],

  "markets_week": ["3-5 bullets on the week's earnings, deals, and market moves — each a self-contained point with what happened and what it signals. NOT paragraphs."],

  "policy_week": [
    {{"tag": "Topic", "headline": "Sharp headline",
      "what": "One sentence: what happened",
      "why": "One sentence: why it matters",
      "watch": "One sentence: what to watch"}}
  ],

  "crypto_week": ["2-4 bullets on the week in crypto and fintech. NOT paragraphs. Empty array if slow week."],

  "science_week": ["2-4 bullets on the week's science and space highlights. NOT paragraphs."],

  "worth_watching": [
    {{"day": "MON/TUE/WED/THU/FRI", "event": "Event name", "detail": "Why it matters"}}
  ],

  "trend_radar": {{
    "narratives": [
      {{"tag": "Tag", "narrative": "One sentence", "day_count": number,
        "last_signal": "This week's signal", "direction": "escalating/stable/resolving"}}
    ],
    "weekly_convergence": "1-2 sentences: are multiple narratives converging toward something bigger this week?"
  }},

  "term_of_the_week": {{
    "term": "Finance concept",
    "definition": "2-3 sentences plain English",
    "context": "How it connects to this week's events"
  }},

  "one_thing": {{
    "headline": "The week's most important developing story",
    "bullets": ["What's happening (one line)", "Why it matters (one line)", "The bigger picture arc (one line)", "What to watch next week (one line)"]
  }}
}}

themes=3 | scoreboard: S&P, Nasdaq, Dow, VIX, 10-Yr, 2-Yr, Spread, Brent, WTI, Gold, Bitcoin | policy_week=3 | worth_watching=4-5 FUTURE EVENTS ONLY — today is {TODAY}, never include anything that has already happened

--- YESTERDAY'S TREND RADAR ---
{fmt_trend_radar(trend_radar)}

--- REAL-TIME PRICES ---
{fmt_market_data(data.get('market_data', {}))}

--- FRED DATA ---
{fmt_fred_data(data.get('fred_data', {}))}

--- AI (WEEK) ---
{fmt_articles(data['ai'], 7)}

--- MARKETS (WEEK) ---
{fmt_articles(data['markets'], 8)}

--- POLICY (WEEK) ---
{fmt_articles(data['policy'], 6)}

--- CRYPTO (WEEK) ---
{fmt_articles(data['crypto'], 6)}

--- SCIENCE (WEEK) ---
{fmt_articles(data['science'], 6)}

--- GLOBAL NEWS (WEEK) ---
{fmt_articles(data['global_news'], 6)}

--- NEXT WEEK CALENDAR ---
{fmt_articles([x for x in data['calendar'] if isinstance(x, dict) and 'title' in x], 8)}
"""
    raw = call_claude(SYSTEM_PROMPT, user_prompt, max_tokens=8000)
    raw = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
    # Safety: if JSON is truncated, try to fix it
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Truncation recovery: find last complete top-level key and close the JSON
        print("  ⚠️ JSON truncated — attempting recovery...")
        # Find the last complete closing brace/bracket pair
        last_good = max(raw.rfind('}},'), raw.rfind('},'), raw.rfind(']},'), raw.rfind('"]'))
        if last_good > 0:
            truncated = raw[:last_good + 2]
            # Count open braces vs closed
            opens = truncated.count('{') - truncated.count('}')
            closes = truncated.count('[') - truncated.count(']')
            recovery = truncated + (']' * max(0, closes)) + ('}' * max(0, opens))
            try:
                return json.loads(recovery)
            except:
                pass
        # Last resort: return minimal valid structure
        print("  ❌ JSON recovery failed — returning minimal structure")
        return {
            "opening": "Brief generation encountered an error today. Markets data and key stories were pulled but the summary could not be completed.",
            "market_dashboard": {"tiles": [], "yield_curve_story": "", "macro_snapshot": ""},
            "ai_compute": [], "markets_economy": {"earnings": [], "movers": [], "deals": [], "earnings_preview": ""},
            "deal_flow": {"ma": [], "vc": [], "ipo_listings": [], "funds_secondaries": []},
            "government_policy": [], "crypto_fintech": [], "science_space": [],
            "trending_x": {"has_content": False, "signals": [], "x_note": ""},
            "utah_regional": {"has_news": False, "headline": "", "bullet_what": "", "bullet_why": ""},
            "worth_watching": [{"item": "Check back tomorrow", "why": "Brief generation encountered an issue today"}],
            "trend_radar": {"narratives": [], "new_this_week": ""},
            "term_of_the_day": {"term": "N/A", "definition": "Brief unavailable today.", "context": ""},
            "one_thing": {"headline": "Brief unavailable today", "body": "An error occurred during generation. Check the GitHub Actions log for details."}
        }


# ══════════════════════════════════════════════════════════════════════════
#  LAYER 3 — HTML EMAIL RENDERER
# ══════════════════════════════════════════════════════════════════════════

CSS = """<style>
@import url('https://fonts.googleapis.com/css2?family=Playfair+Display:wght@700;900&family=Source+Sans+3:wght@300;400;600&display=swap');
*{margin:0;padding:0;box-sizing:border-box}
body{background:#f0ece4;font-family:'Source Sans 3',Georgia,sans-serif;color:#1a1a1a;font-size:15px;line-height:1.65}
.wrap{max-width:680px;margin:0 auto;background:#faf8f4}
/* Header */
.hdr{background:#0d1b2a;padding:36px 40px 28px;border-bottom:4px solid #c9973a}
.hdr-label{font-size:10px;font-weight:600;letter-spacing:3.5px;color:#c9973a;text-transform:uppercase;margin-bottom:10px}
.hdr-title{font-family:'Playfair Display',serif;font-size:34px;font-weight:900;color:#fff;line-height:1.1}
.hdr-date{font-size:12px;color:#8a9bb0;margin-top:10px;letter-spacing:1px}
.hdr-sub{font-size:12px;color:#c9973a;margin-top:4px;font-style:italic}
/* Lead */
.lead{background:#1a2e42;padding:24px 40px;border-left:4px solid #c9973a}
.lead p{color:#d4dfe8;font-size:15px;line-height:1.75}
.lead strong{color:#fff}
/* Sections */
.sec{padding:26px 40px;border-bottom:1px solid #e2ddd4}
.lbl{display:inline-block;font-size:9.5px;font-weight:600;letter-spacing:3px;text-transform:uppercase;color:#fff;background:#0d1b2a;padding:3px 10px;margin-bottom:14px}
.lbl.gold{background:#c9973a}.lbl.slate{background:#3d5166}.lbl.green{background:#1e4d2b}
.lbl.red{background:#8b1a1a}.lbl.navy{background:#003087}.lbl.teal{background:#1a4d4a}
.lbl.purple{background:#4a1942}.lbl.charcoal{background:#2a2a2a}.lbl.rust{background:#8b3a1a}
.lbl.indigo{background:#2d3561}.lbl.forest{background:#2d4a2d}.lbl.copper{background:#b87333}
.sec h2{font-family:'Playfair Display',serif;font-size:20px;font-weight:700;color:#0d1b2a;margin-bottom:14px;line-height:1.2}
/* Stories */
.story{margin-bottom:20px;padding-bottom:20px;border-bottom:1px dashed #ddd8cf}
.story:last-child{border-bottom:none;margin-bottom:0;padding-bottom:0}
.story-name{font-size:11px;font-weight:600;letter-spacing:1.5px;text-transform:uppercase;color:#c9973a;margin-bottom:5px}
.story-hed{font-family:'Playfair Display',serif;font-size:16px;font-weight:700;color:#0d1b2a;margin-bottom:10px;line-height:1.3}
.story-body{font-size:13.5px;color:#3a3a3a;line-height:1.65}
.wwm p{font-size:13.5px;color:#3a3a3a;margin-bottom:8px;line-height:1.65;padding-left:12px;border-left:2px solid #e2ddd4}
.wwm p strong{color:#0d1b2a}
/* Market tiles */
.tile-grid{display:flex;flex-wrap:wrap;gap:0;margin-bottom:16px}
.tile{flex:1 1 28%;background:#f0ece4;padding:10px 12px;border:1px solid #e2ddd4;margin:3px;border-radius:2px;min-width:130px}
.tile-lbl{font-size:9px;font-weight:600;letter-spacing:1px;text-transform:uppercase;color:#8a9bb0;margin-bottom:3px}
.tile-val{font-family:'Playfair Display',serif;font-size:16px;font-weight:700;color:#0d1b2a}
.tile-chg{font-size:11px;font-weight:600;margin-top:2px}
.tile-story{font-size:12px;color:#555;margin-top:4px;line-height:1.4;font-style:italic}
.up{color:#1e6b35}.down{color:#8b1a1a}.flat{color:#8a9bb0}
/* Dashboard notes */
.dash-note{font-size:13px;color:#3a3a3a;margin-top:10px;line-height:1.65;padding:12px 14px;background:#f5f2ec;border-left:3px solid #c9973a}
.dash-note strong{color:#0d1b2a}
/* Movers */
.mv{display:flex;align-items:baseline;gap:10px;margin-bottom:10px;padding-bottom:10px;border-bottom:1px dashed #ddd8cf}
.mv:last-child{border-bottom:none;margin-bottom:0;padding-bottom:0}
.mv-tk{font-weight:600;font-size:13px;color:#0d1b2a;min-width:100px}
.mv-ch{font-size:13px;font-weight:600;min-width:65px}
.mv-why{font-size:13px;color:#3a3a3a;flex:1}
/* X feed */
.x-signal{margin-bottom:14px;padding-bottom:14px;border-bottom:1px dashed #ddd8cf}
.x-signal:last-child{border-bottom:none;margin-bottom:0;padding-bottom:0}
.x-handle{font-size:11px;font-weight:600;color:#1d9bf0;margin-bottom:4px;letter-spacing:0.5px}
.x-text{font-size:13.5px;color:#3a3a3a;line-height:1.6;margin-bottom:4px}
.x-why{font-size:12px;color:#666;font-style:italic}
/* Trend radar */
.radar-item{margin-bottom:12px;padding-bottom:12px;border-bottom:1px dashed #ddd8cf}
.radar-item:last-child{border-bottom:none;margin-bottom:0;padding-bottom:0}
.radar-tag{font-size:10px;font-weight:600;letter-spacing:1px;text-transform:uppercase;margin-bottom:4px}
.radar-tag.escalating{color:#8b1a1a}.radar-tag.stable{color:#3d5166}.radar-tag.resolving{color:#1e6b35}
.radar-narrative{font-size:13.5px;color:#1a1a1a;margin-bottom:3px}
.radar-signal{font-size:12px;color:#666;font-style:italic}
/* Worth watching */
.watch{display:flex;gap:12px;margin-bottom:12px;padding-bottom:12px;border-bottom:1px dashed #ddd8cf}
.watch:last-child{border-bottom:none;margin-bottom:0;padding-bottom:0}
.watch-num{font-size:18px;font-weight:700;color:#c9973a;font-family:'Playfair Display',serif;min-width:24px}
.watch-content .watch-item{font-family:'Playfair Display',serif;font-size:14px;font-weight:700;color:#0d1b2a;margin-bottom:3px}
.watch-content .watch-why{font-size:13px;color:#3a3a3a}
/* Term box */
.term-box{background:#0d1b2a;padding:20px 24px;border-radius:3px}
.term-label{font-size:9px;font-weight:600;letter-spacing:2px;text-transform:uppercase;color:#c9973a;margin-bottom:6px}
.term-word{font-family:'Playfair Display',serif;font-size:22px;font-weight:700;color:#fff;margin-bottom:10px}
.term-def{font-size:13.5px;color:#d4dfe8;line-height:1.65}
.term-ctx{font-size:12px;color:#8a9bb0;margin-top:8px;font-style:italic}
/* One thing */
.one-thing{background:#1a2e42;padding:22px 28px;border-radius:3px}
.one-label{font-size:9px;font-weight:600;letter-spacing:2px;text-transform:uppercase;color:#c9973a;margin-bottom:8px}
.one-hed{font-family:'Playfair Display',serif;font-size:18px;font-weight:700;color:#fff;margin-bottom:10px;line-height:1.3}
.one-body{font-size:13.5px;color:#d4dfe8;line-height:1.75}
/* Scoreboard */
.sb{display:flex;flex-wrap:wrap;gap:0;margin-bottom:8px}
.sb-item{flex:1 1 28%;background:#f0ece4;padding:10px 12px;border:1px solid #e2ddd4;margin:3px;border-radius:2px}
.sb-lbl{font-size:9px;font-weight:600;letter-spacing:1px;text-transform:uppercase;color:#8a9bb0;margin-bottom:3px}
.sb-val{font-family:'Playfair Display',serif;font-size:16px;font-weight:700;color:#0d1b2a}
.sb-chg{font-size:11px;font-weight:600;margin-top:2px}
.sb-story{font-size:11px;color:#666;margin-top:3px;font-style:italic}
/* Saturday themes */
.theme{background:#f5f2ec;border-left:3px solid #c9973a;padding:14px 18px;margin-bottom:14px;border-radius:0 3px 3px 0}
.theme:last-child{margin-bottom:0}
.theme-title{font-family:'Playfair Display',serif;font-size:15px;font-weight:700;color:#0d1b2a;margin-bottom:6px}
.theme-body{font-size:13.5px;color:#3a3a3a;line-height:1.65}
/* Sat watch */
.sat-watch{display:flex;gap:14px;margin-bottom:14px;padding-bottom:14px;border-bottom:1px dashed #ddd8cf}
.sat-watch:last-child{border-bottom:none;margin-bottom:0;padding-bottom:0}
.sat-day{font-size:10px;font-weight:600;letter-spacing:1.5px;text-transform:uppercase;color:#fff;background:#3d5166;padding:4px 8px;height:fit-content;min-width:36px;text-align:center;border-radius:2px}
.sat-event{font-family:'Playfair Display',serif;font-size:14px;font-weight:700;color:#0d1b2a;margin-bottom:4px}
.sat-detail{font-size:13px;color:#3a3a3a}
/* Bullet cards */
.bcard{margin-bottom:18px;padding-bottom:18px;border-bottom:1px dashed #ddd8cf}
.bcard:last-child{border-bottom:none;margin-bottom:0;padding-bottom:0}
.bcard-hed{font-family:'Playfair Display',serif;font-size:15px;font-weight:700;color:#0d1b2a;margin-bottom:8px;line-height:1.3}
.bcard-meta{font-size:11px;font-weight:600;letter-spacing:1px;text-transform:uppercase;color:#c9973a;margin-bottom:8px}
.blist{list-style:none;padding:0;margin:0}
.blist li{font-size:13.5px;color:#3a3a3a;line-height:1.6;padding:4px 0 4px 18px;position:relative}
.blist li::before{content:"•";position:absolute;left:4px;color:#c9973a;font-weight:700}
.blist li.sub{padding-left:32px;color:#555;font-size:13px}
.blist li.sub::before{content:"↳";left:18px;color:#8a9bb0}
.blist li strong{color:#0d1b2a;font-weight:600}
.blist.light li{color:#d4dfe8}
.blist.light li.sub{color:#a9b8c8}
.blist.light li strong{color:#fff}
/* Footer */
.footer{background:#0a1520;padding:16px 40px;text-align:center}
.footer p{font-size:11px;color:#4a5a6a}
.footer span{color:#c9973a}
</style>"""


def e(t):
    return str(t).replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace('"',"&quot;")


def render_bullets(val, light=False):
    """Render a list of strings as a bullet list. If given an old-style
    paragraph string instead, fall back to a styled paragraph so nothing breaks."""
    cls = "blist light" if light else "blist"
    if isinstance(val, list):
        lis = "".join(f"<li>{e(x)}</li>" for x in val if str(x).strip())
        if lis:
            return f'<ul class="{cls}">{lis}</ul>'
        return ""
    if val:
        color = "#d4dfe8" if light else "#3a3a3a"
        return f'<p style="font-size:14px;line-height:1.75;color:{color}">{e(val)}</p>'
    return ""


def render_weekday(d):
    md   = d.get("market_dashboard", {})
    me   = d.get("markets_economy", {})
    gp   = d.get("government_policy", [])
    df   = d.get("deal_flow", {})
    cr   = d.get("crypto_fintech", [])
    sc   = d.get("science_space", [])
    tx   = d.get("trending_x", {})
    utah = d.get("utah_regional", {})
    ww   = d.get("worth_watching", [])
    tr   = d.get("trend_radar", {})
    tod  = d.get("term_of_the_day", {})
    ot   = d.get("one_thing", {})

    # Market dashboard tiles
    tiles_html = ""
    for tile in md.get("tiles", []):
        cls   = "up" if tile.get("direction")=="up" else ("down" if tile.get("direction")=="down" else "flat")
        story = f'<div class="tile-story">{e(tile.get("story",""))}</div>' if tile.get("story") else ""
        tiles_html += f"""<div class="tile">
          <div class="tile-lbl">{e(tile.get('name',''))}</div>
          <div class="tile-val">{e(tile.get('value',''))}</div>
          <div class="tile-chg {cls}">{e(tile.get('change',''))}</div>
          {story}
        </div>"""

    dashboard_notes = ""
    if md.get("yield_curve_story"):
        dashboard_notes += f'<div class="dash-note"><strong>📈 Yield Curve:</strong> {e(md["yield_curve_story"])}</div>'
    if md.get("macro_snapshot"):
        dashboard_notes += f'<div class="dash-note" style="margin-top:8px"><strong>📊 Macro:</strong> {e(md["macro_snapshot"])}</div>'

    # AI & Compute
    ai_html = ""
    for item in d.get("ai_compute", []):
        bullets = ""
        if item.get("bullet_what"):
            bullets += f'<li><strong>What:</strong> {e(item["bullet_what"])}</li>'
        if item.get("bullet_why"):
            bullets += f'<li class="sub"><strong>Why:</strong> {e(item["bullet_why"])}</li>'
        if item.get("bullet_watch"):
            bullets += f'<li class="sub">👀 <strong>Watch for:</strong> {e(item["bullet_watch"])}</li>'
        ai_html += f"""<div class="bcard">
          <div class="bcard-hed">{e(item.get('headline',''))}</div>
          <ul class="blist">{bullets}</ul>
        </div>"""
    if not ai_html:
        ai_html = '<p class="story-body">No major AI developments today.</p>'

    # Earnings
    earnings_html = ""
    for co in me.get("earnings", []):
        bullets = ""
        if co.get("bullet_what"):
            bullets += f'<li><strong>What:</strong> {e(co["bullet_what"])}</li>'
        if co.get("bullet_why"):
            bullets += f'<li class="sub"><strong>Why:</strong> {e(co["bullet_why"])}</li>'
        if co.get("bullet_watch"):
            bullets += f'<li class="sub">👀 <strong>Watch for:</strong> {e(co["bullet_watch"])}</li>'
        earnings_html += f"""<div class="bcard">
          <div class="bcard-meta">{e(co.get('ticker',''))} · {e(co.get('company',''))}</div>
          <div class="bcard-hed">{e(co.get('headline',''))}</div>
          <ul class="blist">{bullets}</ul>
        </div>"""
    if not earnings_html:
        earnings_html = '<p class="story-body">No major earnings overnight.</p>'
    if me.get("earnings_preview"):
        earnings_html += f'<p class="story-body" style="margin-top:12px;font-style:italic;color:#666">📅 Coming up: {e(me["earnings_preview"])}</p>'

    # Movers
    movers_html = ""
    for mv in me.get("movers", []):
        cls = "up" if mv.get("direction")=="up" else ("down" if mv.get("direction")=="down" else "flat")
        movers_html += f"""<div class="mv">
          <span class="mv-tk">{e(mv.get('name',''))}</span>
          <span class="mv-ch {cls}">{e(mv.get('change',''))}</span>
          <span class="mv-why">{e(mv.get('reason',''))}</span></div>"""
    if not movers_html:
        movers_html = '<p class="story-body">Market data unavailable.</p>'

    # Deals
    deals_html = ""
    for deal in me.get("deals", []):
        deals_html += f"""<div class="story">
          <div class="story-name">{e(deal.get('type',''))}</div>
          <div class="story-hed">{e(deal.get('headline',''))}</div>
          <div class="wwm">
            <p><strong>What:</strong> {e(deal.get('what',''))}</p>
            <p><strong>Matters:</strong> {e(deal.get('matters',''))}</p>
          </div></div>"""

    # Deal Flow
    df = d.get("deal_flow", {})

    def deal_flow_subsection(items, label):
        if not items:
            return ""
        html = f'<div class="bcard-meta" style="margin-bottom:10px;margin-top:18px">{label}</div>'
        for item in items:
            meta = " · ".join(filter(None, [
                item.get("parties","") or item.get("company","") or item.get("firm",""),
                item.get("size","") or item.get("amount",""),
                item.get("stage","") or item.get("type",""),
                item.get("investors",""),
            ]))
            bullets = ""
            bw = item.get("bullet_what","")
            by = item.get("bullet_why","")
            if bw:
                bullets += f'<li><strong>What:</strong> {e(bw)}</li>'
            if by:
                bullets += f'<li class="sub"><strong>Why:</strong> {e(by)}</li>'
            html += f"""<div class="bcard">
              <div class="bcard-hed">{e(item.get('headline','') or item.get('company','') or item.get('firm',''))}</div>
              {"<div style='font-size:11px;color:#8a9bb0;margin-bottom:6px'>" + e(meta) + "</div>" if meta else ""}
              <ul class="blist">{bullets}</ul>
            </div>"""
        return html

    ma_html   = deal_flow_subsection(df.get("ma",[]), "M&A / Strategic")
    vc_html   = deal_flow_subsection(df.get("vc",[]), "VC / Private Funding")
    ipo_html  = deal_flow_subsection(df.get("ipo_listings",[]), "IPOs / Listings / Block Trades")
    fund_html = deal_flow_subsection(df.get("funds_secondaries",[]), "Funds / Secondaries")

    deal_flow_content = ma_html + vc_html + ipo_html + fund_html
    deal_flow_section = f"""<div class="sec">
  <div class="lbl copper">Deal Flow</div>
  <h2>M&amp;A · VC · IPOs · Funds &amp; Secondaries</h2>
  {deal_flow_content if deal_flow_content else '<p class="story-body">No major deal activity today.</p>'}
</div>"""

    # Government & Policy
    policy_html = ""
    for item in gp:
        bullets = ""
        if item.get("bullet_what"):
            bullets += f'<li><strong>What:</strong> {e(item["bullet_what"])}</li>'
        if item.get("bullet_why"):
            bullets += f'<li class="sub"><strong>Why:</strong> {e(item["bullet_why"])}</li>'
        if item.get("bullet_watch"):
            bullets += f'<li class="sub">👀 <strong>Watch for:</strong> {e(item["bullet_watch"])}</li>'
        policy_html += f"""<div class="bcard">
          <div class="bcard-meta">{e(item.get('tag',''))}</div>
          <div class="bcard-hed">{e(item.get('headline',''))}</div>
          <ul class="blist">{bullets}</ul>
        </div>"""
    if not policy_html:
        policy_html = '<p class="story-body">No major policy developments today.</p>'

    # Crypto & Fintech
    crypto_html = ""
    for item in cr:
        bullets = ""
        if item.get("bullet_what"):
            bullets += f'<li><strong>What:</strong> {e(item["bullet_what"])}</li>'
        if item.get("bullet_why"):
            bullets += f'<li class="sub"><strong>Why:</strong> {e(item["bullet_why"])}</li>'
        crypto_html += f"""<div class="bcard">
          <div class="bcard-hed">{e(item.get('headline',''))}</div>
          <ul class="blist">{bullets}</ul>
        </div>"""
    crypto_section = f"""<div class="sec">
  <div class="lbl indigo">Crypto &amp; Fintech</div>
  <h2>Digital Assets &amp; Financial Tech</h2>
  {crypto_html if crypto_html else '<p class="story-body">Quiet day in crypto and fintech.</p>'}
</div>""" if cr else ""

    # Science & Space
    sci_html = ""
    for item in sc:
        bullets = ""
        if item.get("bullet_what"):
            bullets += f'<li><strong>What:</strong> {e(item["bullet_what"])}</li>'
        if item.get("bullet_why"):
            bullets += f'<li class="sub"><strong>Why:</strong> {e(item["bullet_why"])}</li>'
        sci_html += f"""<div class="bcard">
          <div class="bcard-hed">{e(item.get('headline',''))}</div>
          <ul class="blist">{bullets}</ul>
        </div>"""
    sci_section = f"""<div class="sec">
  <div class="lbl forest">Science &amp; Space</div>
  <h2>Friedberg Corner</h2>
  {sci_html if sci_html else '<p class="story-body">No major science/space news today.</p>'}
</div>""" if sc else ""

    # Trending on X
    x_html = ""
    if tx.get("has_content") and tx.get("signals"):
        for sig in tx["signals"]:
            x_html += f"""<div class="x-signal">
              <div class="x-handle">{e(sig.get('account',''))}</div>
              <div class="x-text">{e(sig.get('signal',''))}</div>
              <div class="x-why">{e(sig.get('why_it_matters',''))}</div>
            </div>"""
        if tx.get("x_note"):
            x_html += f'<p class="story-body" style="margin-top:12px;font-style:italic;color:#666">{e(tx["x_note"])}</p>'
    else:
        x_html = '<p class="story-body">X feed unavailable or quiet today.</p>'

    # Utah
    utah_section = ""
    if utah.get("has_news") and utah.get("bullet_what"):
        u_bullets = f'<li><strong>What:</strong> {e(utah.get("bullet_what",""))}</li>'
        if utah.get("bullet_why"):
            u_bullets += f'<li class="sub"><strong>Why:</strong> {e(utah["bullet_why"])}</li>'
        utah_section = f"""<div class="sec">
  <div class="lbl green">Utah &amp; Regional</div>
  <h2>Silicon Slopes &amp; Local Economy</h2>
  <div class="bcard">
    <div class="bcard-hed">{e(utah.get("headline","Local News"))}</div>
    <ul class="blist">{u_bullets}</ul>
  </div>
</div>"""

    # Worth Watching
    ww_html = ""
    for i, item in enumerate(ww, 1):
        ww_html += f"""<div class="watch">
          <div class="watch-num">{i}</div>
          <div class="watch-content">
            <div class="watch-item">{e(item.get('item',''))}</div>
            <div class="watch-why">{e(item.get('why',''))}</div>
          </div></div>"""

    # Trend Radar
    radar_html = ""
    for n in tr.get("narratives", []):
        dir_cls = n.get("direction","stable")
        dir_label = {"escalating": "↑ Escalating", "stable": "→ Stable", "resolving": "↓ Resolving"}.get(dir_cls, dir_cls)
        radar_html += f"""<div class="radar-item">
          <div class="radar-tag {dir_cls}">{e(n.get('tag',''))} · Day {n.get('day_count',1)} · {dir_label}</div>
          <div class="radar-narrative">{e(n.get('narrative',''))}</div>
          <div class="radar-signal">Today: {e(n.get('last_signal',''))}</div>
        </div>"""
    if tr.get("new_this_week"):
        radar_html += f'<p class="story-body" style="margin-top:12px;font-style:italic;color:#666">🆕 New: {e(tr["new_this_week"])}</p>'

    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>The Daily Brief — {e(d.get('date',''))}</title>{CSS}</head>
<body><div class="wrap">

<div class="hdr">
  <div class="hdr-label">The Daily Brief</div>
  <div class="hdr-title">Good Morning, Konner.</div>
  <div class="hdr-date">{e(d.get('date',''))} · Markets open in ~30 min</div>
  <div class="hdr-sub">Everything you need. Nothing you don't.</div>
</div>

<div class="lead">{render_bullets(d.get('opening',''), light=True)}</div>

<div class="sec">
  <div class="lbl slate">Market Dashboard</div>
  <h2>Pre-Market Snapshot</h2>
  <div class="tile-grid">{tiles_html}</div>
  {dashboard_notes}
</div>

<div class="sec">
  <div class="lbl charcoal">AI &amp; Compute</div>
  <h2>Intelligence &amp; Infrastructure</h2>
  {ai_html}
</div>

<div class="sec">
  <div class="lbl gold">Markets &amp; Economy</div>
  <h2>Earnings, Movers &amp; Deals</h2>
  {earnings_html}
  <div style="margin-top:20px">
    <div class="story-name" style="margin-bottom:10px">On the Move</div>
    {movers_html}
  </div>
  {f'<div style="margin-top:20px"><div class="story-name" style="margin-bottom:10px">Deals &amp; Raises</div>{deals_html}</div>' if deals_html else ''}
</div>

{deal_flow_section}

<div class="sec">
  <div class="lbl purple">Government, Policy &amp; Regulation</div>
  <h2>Washington &amp; Beyond</h2>
  {policy_html}
</div>

{crypto_section}
{sci_section}

<div class="sec">
  <div class="lbl" style="background:#1d9bf0">Trending on X</div>
  <h2>What Your Feed Is Saying</h2>
  {x_html}
</div>

{utah_section}

<div class="sec">
  <div class="lbl teal">Worth Watching</div>
  <h2>3 Things to Follow This Week</h2>
  {ww_html}
</div>

<div class="sec">
  <div class="lbl rust">Trend Radar</div>
  <h2>Narratives in Motion</h2>
  {radar_html if radar_html else '<p class="story-body">Building narrative database — check back tomorrow.</p>'}
</div>

<div class="sec">
  <div class="lbl charcoal">Finance Vocab</div>
  <h2>Term of the Day</h2>
  <div class="term-box">
    <div class="term-label">Today's Concept</div>
    <div class="term-word">{e(tod.get('term',''))}</div>
    <div class="term-def">{e(tod.get('definition',''))}</div>
    <div class="term-ctx">{e(tod.get('context',''))}</div>
  </div>
</div>

<div class="sec" style="border-bottom:none">
  <div class="lbl gold">One Thing to Think About</div>
  <h2>The Developing Story</h2>
  <div class="one-thing">
    <div class="one-label">Follow This</div>
    <div class="one-hed">{e(ot.get('headline',''))}</div>
    {render_bullets(ot.get('bullets') if ot.get('bullets') else ot.get('body',''), light=True)}
  </div>
</div>

<div class="footer">
  <p>The Daily Brief · Built for <span>Konner Greer</span> · University of Utah, Finance &amp; Fintech '27</p>
  <p style="margin-top:4px">Delivered every weekday at 7:00 AM MT · <span>Markets open at 7:30 AM MT</span></p>
</div>

</div></body></html>"""


def render_saturday(d):
    md   = d.get("macro_wrap", {})
    tr   = d.get("trend_radar", {})
    tow  = d.get("term_of_the_week", {})
    ot   = d.get("one_thing", {})

    themes_html = ""
    for i, t in enumerate(d.get("themes",[]), 1):
        bullets = ""
        if t.get("what"):
            bullets += f'<li><strong>What:</strong> {e(t["what"])}</li>'
        if t.get("why"):
            bullets += f'<li class="sub"><strong>Why:</strong> {e(t["why"])}</li>'
        if t.get("watch"):
            bullets += f'<li class="sub">👀 <strong>Watch for:</strong> {e(t["watch"])}</li>'
        # Fallback: if the model still returns an old-style paragraph "body", show it
        if not bullets and t.get("body"):
            bullets = f'<li>{e(t["body"])}</li>'
        themes_html += f"""<div class="theme">
          <div class="theme-title">{i}. {e(t.get('title',''))}</div>
          <ul class="blist">{bullets}</ul></div>"""

    sb_html = '<div class="sb">'
    for item in d.get("scoreboard",[]):
        cls = "up" if item.get("direction")=="up" else ("down" if item.get("direction")=="down" else "flat")
        story = f'<div class="sb-story">{e(item.get("story",""))}</div>' if item.get("story") else ""
        sb_html += f"""<div class="sb-item">
          <div class="sb-lbl">{e(item.get('name',''))}</div>
          <div class="sb-val">{e(item.get('value','—'))}</div>
          <div class="sb-chg {cls}">{e(item.get('change',''))}</div>
          {story}
        </div>"""
    sb_html += "</div>"

    macro_notes = ""
    for key, label in [("yield_story","Yield Curve"),("fred_highlights","Macro Data"),("sector_rotation","Sectors")]:
        val = md.get(key,"")
        if val:
            macro_notes += f'<div class="dash-note" style="margin-top:8px"><strong>{label}:</strong> {e(val)}</div>'

    policy_html = ""
    for item in d.get("policy_week",[]):
        bullets = ""
        if item.get("what"):
            bullets += f'<li><strong>What:</strong> {e(item["what"])}</li>'
        if item.get("why"):
            bullets += f'<li class="sub"><strong>Why:</strong> {e(item["why"])}</li>'
        if item.get("watch"):
            bullets += f'<li class="sub">👀 <strong>Watch for:</strong> {e(item["watch"])}</li>'
        if not bullets and item.get("summary"):
            bullets = f'<li>{e(item["summary"])}</li>'
        policy_html += f"""<div class="bcard">
          <div class="bcard-meta">{e(item.get('tag',''))}</div>
          <div class="bcard-hed">{e(item.get('headline',''))}</div>
          <ul class="blist">{bullets}</ul></div>"""

    watch_html = ""
    for item in d.get("worth_watching",[]):
        watch_html += f"""<div class="sat-watch">
          <div class="sat-day">{e(item.get('day',''))}</div>
          <div><div class="sat-event">{e(item.get('event',''))}</div>
          <div class="sat-detail">{e(item.get('detail',''))}</div></div></div>"""

    radar_html = ""
    for n in tr.get("narratives",[]):
        dir_cls   = n.get("direction","stable")
        dir_label = {"escalating":"↑ Escalating","stable":"→ Stable","resolving":"↓ Resolving"}.get(dir_cls, dir_cls)
        radar_html += f"""<div class="radar-item">
          <div class="radar-tag {dir_cls}">{e(n.get('tag',''))} · Day {n.get('day_count',1)} · {dir_label}</div>
          <div class="radar-narrative">{e(n.get('narrative',''))}</div>
          <div class="radar-signal">This week: {e(n.get('last_signal',''))}</div>
        </div>"""
    if tr.get("weekly_convergence"):
        radar_html += f'<div class="dash-note" style="margin-top:12px"><strong>Convergence signal:</strong> {e(tr["weekly_convergence"])}</div>'

    week_range = d.get("week_range","This Week")

    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>The Weekly Brief — {e(week_range)}</title>{CSS}</head>
<body><div class="wrap">

<div class="hdr" style="background:linear-gradient(150deg,#1a1200 0%,#3a2800 50%,#0d1b2a 100%)">
  <div class="hdr-label">The Weekly Brief · Saturday Edition</div>
  <div class="hdr-title">The Week in Review.</div>
  <div class="hdr-date">Week of {e(week_range)}</div>
  <div class="hdr-sub">Read it once, sound sharp all weekend.</div>
</div>

<div class="lead">{render_bullets(d.get('opening',''), light=True)}</div>

<div class="sec">
  <div class="lbl gold">The Big Picture</div>
  <h2>This Week's Defining Themes</h2>
  {themes_html}
</div>

<div class="sec">
  <div class="lbl slate">Markets</div>
  <h2>Weekly Scoreboard</h2>
  {sb_html}
  {macro_notes}
</div>

<div class="sec">
  <div class="lbl charcoal">AI &amp; Compute</div>
  <h2>The Week in Intelligence</h2>
  {render_bullets(d.get('ai_week'))}
</div>

<div class="sec">
  <div class="lbl gold">Markets &amp; Economy</div>
  <h2>Earnings, Deals &amp; What They Signal</h2>
  {render_bullets(d.get('markets_week'))}
</div>

<div class="sec">
  <div class="lbl purple">Government, Policy &amp; Regulation</div>
  <h2>The Policy Landscape</h2>
  {policy_html}
</div>

<div class="sec">
  <div class="lbl teal">What to Watch</div>
  <h2>Next Week's Calendar</h2>
  {watch_html}
</div>

<div class="sec">
  <div class="lbl rust">Trend Radar</div>
  <h2>Narratives in Motion</h2>
  {radar_html if radar_html else '<p class="story-body">Building narrative database.</p>'}
</div>

<div class="sec">
  <div class="lbl charcoal">Finance Vocab</div>
  <h2>Term of the Week</h2>
  <div class="term-box">
    <div class="term-label">This Week's Concept</div>
    <div class="term-word">{e(tow.get('term',''))}</div>
    <div class="term-def">{e(tow.get('definition',''))}</div>
    <div class="term-ctx">{e(tow.get('context',''))}</div>
  </div>
</div>

<div class="sec" style="border-bottom:none">
  <div class="lbl gold">One Thing to Think About</div>
  <h2>The Week's Developing Story</h2>
  <div class="one-thing">
    <div class="one-label">Follow This</div>
    <div class="one-hed">{e(ot.get('headline',''))}</div>
    {render_bullets(ot.get('bullets') if ot.get('bullets') else ot.get('body',''), light=True)}
  </div>
</div>

<div class="footer">
  <p>The Weekly Brief · Built for <span>Konner Greer</span> · University of Utah, Finance &amp; Fintech '27</p>
  <p style="margin-top:4px">Saturday Edition · <span>The Daily Brief</span> returns Monday at 7:00 AM MT</p>
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
    print(f"\n🌅 Daily Brief v2.0 — {TODAY} ({'Saturday' if IS_SATURDAY else 'Weekday'})\n")

    # Load trend radar
    print("[0/3] Loading trend radar...")
    trend_radar = load_trend_radar()

    if IS_SATURDAY:
        print("[1/3] Gathering week's data...")
        data = gather_saturday_data()
        print("\n[2/3] Claude writing Saturday recap...")
        brief = generate_saturday_briefing(data, trend_radar)
        print("\n[3/3] Rendering & sending...")
        html = render_saturday(brief)
        mon  = NOW - timedelta(days=NOW.weekday())
        fri  = mon + timedelta(days=4)
        send_email(f"📊 Weekly Brief — Week of {mon.strftime('%b %d')}–{fri.strftime('%b %d')}", html)
    else:
        print("[1/3] Gathering today's data...")
        data = gather_weekday_data()
        print("\n[2/3] Claude writing today's briefing...")
        brief = generate_weekday_briefing(data, trend_radar)
        print("\n[3/3] Rendering & sending...")
        html = render_weekday(brief)
        send_email(f"☀️ Daily Brief — {NOW.strftime('%a %b %d')}", html)

    # Save updated trend radar
    if brief.get("trend_radar"):
        new_radar = {
            "narratives":    brief["trend_radar"].get("narratives", []),
            "last_updated":  TODAY,
        }
        save_trend_radar(new_radar)

    print("\n✅ Done!\n")
