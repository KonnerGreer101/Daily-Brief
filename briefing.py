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
ANTHROPIC_KEY  = os.environ["ANTHROPIC_API_KEY"]
NEWS_KEY       = os.environ["NEWS_API_KEY"]
FINNHUB_KEY    = os.environ.get("FINNHUB_API_KEY", "")
GNEWS_KEY      = os.environ.get("GNEWS_API_KEY", "")
FRED_KEY       = os.environ.get("FRED_API_KEY", "")
FMP_KEY        = os.environ.get("FMP_API_KEY", "")
AV_KEY         = os.environ.get("ALPHA_VANTAGE_API_KEY", "")
GMAIL_USER     = os.environ["GMAIL_USER"]
GMAIL_PASS     = os.environ["GMAIL_APP_PASS"]

MT          = ZoneInfo("America/Denver")
NOW         = datetime.now(MT)
TODAY       = NOW.strftime("%A, %B %d, %Y")
IS_SATURDAY = NOW.weekday() == 5

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/rss+xml, application/xml, application/json, text/xml, */*",
    "Accept-Language": "en-US,en;q=0.9",
}


# ══════════════════════════════════════════════════════════════════════════
#  LAYER 1A — MARKET DATA (yfinance — expanded dashboard)
# ══════════════════════════════════════════════════════════════════════════

def fetch_market_data():
    """Pull comprehensive market dashboard including VIX, MOVE, yield curve, FX, both crude benchmarks."""
    try:
        import yfinance as yf
        tickers = {
            # Equity indices
            "^GSPC":    "S&P 500",
            "^IXIC":    "Nasdaq",
            "^DJI":     "Dow Jones",
            # Volatility
            "^VIX":     "VIX (Fear Index)",
            # Rates & Bonds
            "^IRX":     "2-Yr Yield",
            "^TNX":     "10-Yr Yield",
            "^TYX":     "30-Yr Yield",
            # Commodities
            "CL=F":     "WTI Crude Oil",
            "BZ=F":     "Brent Crude Oil",
            "GC=F":     "Gold",
            "SI=F":     "Silver",
            # FX
            "DX-Y.NYB": "US Dollar Index",
            "EURUSD=X": "EUR/USD",
            "JPYUSD=X": "JPY/USD",
            # Crypto
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
                        "price":      round(curr, 4 if "USD=X" in symbol else 2),
                        "change_pct": round(pct, 2),
                        "direction":  "up" if pct > 0.05 else ("down" if pct < -0.05 else "flat"),
                    }
            except Exception as ex:
                print(f"    yfinance [{symbol}]: {ex}")

        # Compute yield curve spread (10Y - 2Y)
        if "^TNX" in result and "^IRX" in result:
            spread = round(result["^TNX"]["price"] - result["^IRX"]["price"], 3)
            result["YIELD_CURVE"] = {
                "name":       f"10Y-2Y Spread",
                "price":      spread,
                "change_pct": 0,
                "direction":  "up" if spread > 0 else "down",
                "inverted":   spread < 0,
            }

        print(f"    [yfinance] {len(result)} instruments fetched")
        return result
    except ImportError:
        print("    yfinance not installed")
        return {}


# ══════════════════════════════════════════════════════════════════════════
#  LAYER 1B — FRED API (St. Louis Fed — macro economic data)
# ══════════════════════════════════════════════════════════════════════════

FRED_SERIES = {
    "CPIAUCSL":   "CPI (All Items, YoY %)",
    "CPILFESL":   "Core CPI (ex Food/Energy)",
    "UNRATE":     "Unemployment Rate",
    "GDP":        "Real GDP Growth",
    "FEDFUNDS":   "Fed Funds Rate",
    "T10Y2Y":     "10Y-2Y Treasury Spread (FRED)",
    "DCOILWTICO": "WTI Oil Price (FRED)",
    "BAMLH0A0HYM2": "High Yield Credit Spread (OAS)",
    "BAMLC0A0CM":   "Investment Grade Credit Spread",
    "UMCSENT":    "U Michigan Consumer Sentiment",
    "M2SL":       "M2 Money Supply",
}

def fetch_fred_data():
    """Pull latest macro indicators from FRED. Returns dict of series_id -> {name, value, date}."""
    if not FRED_KEY:
        print("    [FRED] no API key")
        return {}
    results = {}
    for series_id, name in FRED_SERIES.items():
        try:
            params = urllib.parse.urlencode({
                "series_id": series_id,
                "api_key": FRED_KEY,
                "file_type": "json",
                "sort_order": "desc",
                "limit": 2,
            })
            req = urllib.request.Request(
                f"https://api.stlouisfed.org/fred/series/observations?{params}",
                headers=HEADERS
            )
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read().decode())
            obs = [o for o in data.get("observations", []) if o.get("value") != "."]
            if obs:
                latest = obs[0]
                prior  = obs[1] if len(obs) > 1 else None
                val = float(latest["value"])
                chg = round(val - float(prior["value"]), 3) if prior else None
                results[series_id] = {
                    "name":   name,
                    "value":  val,
                    "date":   latest.get("date",""),
                    "change": chg,
                }
        except Exception as ex:
            print(f"    [FRED {series_id}]: {ex}")
    print(f"    [FRED] {len(results)} series fetched")
    return results


def fmt_fred_data(fred):
    """Format FRED data for Claude prompt."""
    if not fred:
        return "FRED data unavailable."
    lines = []
    for sid, d in fred.items():
        chg_str = f" (Δ {d['change']:+.3f} from prior)" if d.get("change") is not None else ""
        lines.append(f"• {d['name']}: {d['value']}{chg_str} (as of {d['date']})")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════
#  LAYER 1C — FMP (Financial Modeling Prep — earnings calendar + fundamentals)
# ══════════════════════════════════════════════════════════════════════════

def fetch_fmp_earnings_calendar(days_ahead=5):
    """Get upcoming earnings releases from FMP."""
    if not FMP_KEY:
        return []
    try:
        today    = NOW.strftime("%Y-%m-%d")
        end_date = (NOW + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
        params   = urllib.parse.urlencode({"from": today, "to": end_date, "apikey": FMP_KEY})
        req = urllib.request.Request(
            f"https://financialmodelingprep.com/api/v3/earning_calendar?{params}",
            headers=HEADERS
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode())
        # Filter for notable companies (market cap proxy: eps estimate > 0.5 or known tickers)
        calendar = []
        for item in data[:20]:
            if item.get("epsEstimated") or item.get("revenueEstimated"):
                calendar.append({
                    "ticker":       item.get("symbol",""),
                    "date":         item.get("date",""),
                    "eps_est":      item.get("epsEstimated","N/A"),
                    "rev_est":      item.get("revenueEstimated","N/A"),
                    "time":         item.get("time",""),
                })
        print(f"    [FMP earnings calendar] {len(calendar)} upcoming reports")
        return calendar
    except Exception as ex:
        print(f"    [FMP earnings calendar]: {ex}")
        return []


def fetch_fmp_sector_performance():
    """Get S&P 500 sector performance from FMP."""
    if not FMP_KEY:
        return []
    try:
        params = urllib.parse.urlencode({"apikey": FMP_KEY})
        req = urllib.request.Request(
            f"https://financialmodelingprep.com/api/v3/sectors-performance?{params}",
            headers=HEADERS
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode())
        sectors = []
        for s in (data.get("sectorPerformance") or data)[:11]:
            sectors.append({
                "sector":  s.get("sector",""),
                "change":  s.get("changesPercentage",""),
            })
        print(f"    [FMP sectors] {len(sectors)} sectors")
        return sectors
    except Exception as ex:
        print(f"    [FMP sectors]: {ex}")
        return []


def fmt_earnings_calendar(calendar):
    if not calendar:
        return "No earnings calendar data available."
    lines = []
    for e in calendar[:10]:
        eps = f"EPS est: ${e['eps_est']}" if e.get("eps_est") and e["eps_est"] != "N/A" else ""
        rev = f"Rev est: ${e['rev_est']:,.0f}" if isinstance(e.get("rev_est"), (int, float)) else ""
        detail = " · ".join(filter(None, [eps, rev]))
        lines.append(f"• {e['ticker']} — {e['date']} {e.get('time','')} {detail}")
    return "\n".join(lines)


def fmt_sectors(sectors):
    if not sectors:
        return "Sector data unavailable."
    lines = []
    for s in sectors:
        chg = s.get("change","")
        try:
            val = float(str(chg).replace("%",""))
            arrow = "▲" if val > 0 else "▼"
            lines.append(f"• {s['sector']}: {arrow}{abs(val):.2f}%")
        except:
            lines.append(f"• {s['sector']}: {chg}")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════
#  LAYER 1D — ALPHA VANTAGE (sector trends + economic indicators)
# ══════════════════════════════════════════════════════════════════════════

def fetch_av_sector_performance():
    """Alpha Vantage sector performance as backup/complement to FMP."""
    if not AV_KEY:
        return []
    try:
        params = urllib.parse.urlencode({"function": "SECTOR", "apikey": AV_KEY})
        req = urllib.request.Request(
            f"https://www.alphavantage.co/query?{params}", headers=HEADERS
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode())
        day_perf = data.get("Rank A: Real-Time Performance", {})
        sectors = [{"sector": k, "change": v} for k, v in day_perf.items()]
        print(f"    [Alpha Vantage sectors] {len(sectors)} sectors")
        return sectors
    except Exception as ex:
        print(f"    [Alpha Vantage sectors]: {ex}")
        return []


# ══════════════════════════════════════════════════════════════════════════
#  LAYER 1E — RSS FEEDS
# ══════════════════════════════════════════════════════════════════════════

RSS_FEEDS = {
    "ft_markets":       "https://www.ft.com/rss/home",
    "seeking_alpha":    "https://seekingalpha.com/feed.xml",
    "marketwatch_top":  "https://feeds.marketwatch.com/marketwatch/topstories/",
    "marketwatch_mk":   "https://feeds.marketwatch.com/marketwatch/marketpulse/",
    "bbc_world":        "https://feeds.bbci.co.uk/news/world/rss.xml",
    "bbc_business":     "https://feeds.bbci.co.uk/news/business/rss.xml",
    "guardian_world":   "https://www.theguardian.com/world/rss",
    "guardian_biz":     "https://www.theguardian.com/business/rss",
    "guardian_us":      "https://www.theguardian.com/us-news/rss",
    "npr_news":         "https://feeds.npr.org/1001/rss.xml",
    "npr_business":     "https://feeds.npr.org/1006/rss.xml",
    "politico":         "https://www.politico.com/rss/politicopicks.xml",
    "sec_litigation":   "https://www.sec.gov/rss/litigation/litreleases.xml",
    "sec_enforcement":  "https://www.sec.gov/rss/litigation/admin.xml",
    "mlb_yankees":      "https://www.mlb.com/feeds/news/rss.xml?teamId=147",
    "espn_mlb":         "https://www.espn.com/espn/rss/mlb/news",
}

SOURCE_NAMES = {
    "ft_markets": "FT", "seeking_alpha": "Seeking Alpha",
    "marketwatch_top": "MarketWatch", "marketwatch_mk": "MarketWatch",
    "bbc_world": "BBC", "bbc_business": "BBC",
    "guardian_world": "The Guardian", "guardian_biz": "The Guardian",
    "guardian_us": "The Guardian", "npr_news": "NPR", "npr_business": "NPR",
    "politico": "Politico", "sec_litigation": "SEC", "sec_enforcement": "SEC",
    "mlb_yankees": "MLB", "espn_mlb": "ESPN",
}


def fetch_rss(feed_key, max_items=8, max_age_hours=30):
    url = RSS_FEEDS.get(feed_key)
    if not url:
        return []
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=15) as r:
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


def fetch_rss_multi(feed_keys, max_per_feed=5, max_age_hours=30):
    results = []
    for key in feed_keys:
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
        print(f"    [newsapi: {query[:40]}] {len(articles)} articles")
        return articles
    except Exception as ex:
        print(f"    NewsAPI '{query[:40]}': {ex}")
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
        print(f"    [newsapi headlines:{category}] {len(articles)} articles")
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
        cutoff   = datetime.now(timezone.utc) - timedelta(hours=26)
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
        print(f"    [finnhub:{category}] {len(articles)} articles")
        return articles
    except Exception as ex:
        print(f"    [finnhub] {ex}")
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
        print(f"    [gnews:{query[:35]}] {len(articles)} articles")
        return articles
    except Exception as ex:
        print(f"    [gnews] {ex}")
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
        print(f"    [gnews top:{topic}] {len(articles)} articles")
        return articles
    except Exception as ex:
        print(f"    [gnews top] {ex}")
        return []


# ══════════════════════════════════════════════════════════════════════════
#  LAYER 1I — FORMATTERS
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
    # Equity indices first
    for sym in ["^GSPC","^IXIC","^DJI"]:
        if sym in md:
            info  = md[sym]
            arrow = "▲" if info["direction"]=="up" else ("▼" if info["direction"]=="down" else "–")
            lines.append(f"• {info['name']}: {info['price']:,.2f} ({arrow}{info['change_pct']:+.2f}%)")
    # Volatility
    if "^VIX" in md:
        info  = md["^VIX"]
        arrow = "▲" if info["direction"]=="up" else ("▼" if info["direction"]=="down" else "–")
        level = "ELEVATED" if info["price"] > 25 else ("HIGH" if info["price"] > 20 else "low")
        lines.append(f"• {info['name']}: {info['price']:.2f} ({arrow}{info['change_pct']:+.2f}%) — fear level: {level}")
    # Yield curve
    for sym in ["^IRX","^TNX","^TYX"]:
        if sym in md:
            info  = md[sym]
            arrow = "▲" if info["direction"]=="up" else ("▼" if info["direction"]=="down" else "–")
            lines.append(f"• {info['name']}: {info['price']:.2f}% ({arrow}{info['change_pct']:+.2f}%)")
    if "YIELD_CURVE" in md:
        yc    = md["YIELD_CURVE"]
        inv   = " ⚠️ INVERTED" if yc.get("inverted") else ""
        lines.append(f"• {yc['name']}: {yc['price']:+.3f}%{inv}")
    # Commodities
    for sym in ["CL=F","BZ=F","GC=F","SI=F"]:
        if sym in md:
            info  = md[sym]
            arrow = "▲" if info["direction"]=="up" else ("▼" if info["direction"]=="down" else "–")
            lines.append(f"• {info['name']}: ${info['price']:,.2f} ({arrow}{info['change_pct']:+.2f}%)")
    # FX & Crypto
    for sym in ["DX-Y.NYB","EURUSD=X","JPYUSD=X","BTC-USD"]:
        if sym in md:
            info  = md[sym]
            arrow = "▲" if info["direction"]=="up" else ("▼" if info["direction"]=="down" else "–")
            lines.append(f"• {info['name']}: {info['price']:,.4f} ({arrow}{info['change_pct']:+.2f}%)")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════
#  LAYER 1J — DATA GATHERERS
# ══════════════════════════════════════════════════════════════════════════

SILICON_SLOPES_COMPANIES = [
    "Entrata", "Podium", "Divvy", "Merit Medical", "Domo",
    "Lucid Motors Utah", "Black Diamond Equipment", "Overstock",
    "Ancestry.com", "Pluralsight", "Qualtrics", "Weave Communications",
    "Health Catalyst", "Recursion Pharmaceuticals", "Instructure",
]

def gather_weekday_data():
    print("\n  → Market dashboard (yfinance)...")
    market_data = fetch_market_data()

    print("\n  → FRED macro indicators...")
    fred_data = fetch_fred_data()

    print("\n  → FMP earnings calendar & sector performance...")
    earnings_calendar = fetch_fmp_earnings_calendar(days_ahead=5)
    sectors_fmp       = fetch_fmp_sector_performance()

    print("\n  → Alpha Vantage sector performance...")
    sectors_av = fetch_av_sector_performance()
    sectors    = sectors_fmp if sectors_fmp else sectors_av

    print("\n  → Markets & Finance...")
    markets  = newsapi_headlines(category="business", page_size=8)
    markets += newsapi_search("stock market S&P 500 Nasdaq earnings Wall Street equities sector movers", page_size=6)
    markets += finnhub_news(category="general")
    markets += gnews_top(topic="business", max_results=6)
    markets += fetch_rss_multi(["marketwatch_top","marketwatch_mk","bbc_business","ft_markets"], max_per_feed=4)

    print("\n  → Earnings...")
    earnings  = newsapi_search("quarterly earnings EPS revenue beat miss guidance raised lowered", page_size=8)
    earnings += newsapi_search("earnings results profit fiscal quarter analyst estimate outlook", page_size=5)
    earnings += finnhub_news(category="general")
    earnings += fetch_rss_multi(["marketwatch_top","seeking_alpha"], max_per_feed=4)

    print("\n  → M&A / IPO / Deals...")
    deals  = newsapi_search("merger acquisition takeover buyout billion deal agreed signed", page_size=6)
    deals += newsapi_search("IPO initial public offering listing debut S-1 filed valuation", page_size=5)
    deals += newsapi_search("fundraise venture capital raised funding round series billion", page_size=4)
    deals += finnhub_news(category="merger")
    deals += fetch_rss_multi(["marketwatch_top","guardian_biz"], max_per_feed=3)

    print("\n  → Macro & Policy...")
    macro  = newsapi_search("Federal Reserve rate decision CPI inflation GDP jobs report data", page_size=6)
    macro += newsapi_search("trade tariffs Treasury bonds yield curve economic policy recession", page_size=5)
    macro += newsapi_search("consumer spending retail sales housing starts economic indicator", page_size=4)
    macro += gnews_search("Federal Reserve inflation GDP economic policy", max_results=5)
    macro += fetch_rss_multi(["npr_business","ft_markets","guardian_biz"], max_per_feed=4)

    print("\n  → Regulatory (major only)...")
    regulatory  = fetch_rss_multi(["sec_litigation","sec_enforcement"], max_per_feed=4)
    regulatory += newsapi_search("SEC fraud indictment charged billion settlement major enforcement", page_size=4)
    regulatory += newsapi_search("Fed rate decision FOMC bank failure financial crisis systemic", page_size=3)

    print("\n  → Finance headlines (broad sectors)...")
    fin_headlines  = newsapi_headlines(category="business", page_size=6)
    fin_headlines += newsapi_search("energy oil pharma biotech retail consumer auto airline semiconductor", page_size=5)
    fin_headlines += newsapi_search("real estate housing banking insurance fintech payments crypto", page_size=4)
    fin_headlines += gnews_top(topic="business", max_results=5)
    fin_headlines += fetch_rss_multi(["bbc_business","guardian_biz","marketwatch_top"], max_per_feed=4)

    print("\n  → Global News...")
    global_news  = newsapi_headlines(category="general", page_size=6)
    global_news += newsapi_search("China Europe Russia Middle East war election crisis diplomacy", page_size=6)
    global_news += gnews_top(topic="world", max_results=6)
    global_news += gnews_search("geopolitics international trade sanctions foreign policy", max_results=5)
    global_news += fetch_rss_multi(["bbc_world","guardian_world","guardian_us","npr_news"], max_per_feed=4)

    print("\n  → Silicon Slopes (Utah tech)...")
    slopes_query = " OR ".join(SILICON_SLOPES_COMPANIES[:8])
    silicon_slopes = newsapi_search(slopes_query, page_size=5, days_back=3)
    silicon_slopes += gnews_search("Utah tech startup Silicon Slopes funding", max_results=4)

    print("\n  → Yankees...")
    yankees  = fetch_rss_multi(["mlb_yankees","espn_mlb"], max_per_feed=6)
    yankees += newsapi_search("New York Yankees MLB baseball", page_size=5, days_back=2)

    return {
        "date":              TODAY,
        "market_data":       market_data,
        "fred_data":         fred_data,
        "earnings_calendar": earnings_calendar,
        "sectors":           sectors,
        "markets":           markets,
        "earnings":          earnings,
        "deals":             deals,
        "macro":             macro,
        "regulatory":        regulatory,
        "fin_headlines":     fin_headlines,
        "global_news":       global_news,
        "silicon_slopes":    silicon_slopes,
        "yankees":           yankees,
    }


def gather_saturday_data():
    print("\n  → Real-time prices...")
    market_data = fetch_market_data()

    print("\n  → FRED macro data...")
    fred_data = fetch_fred_data()

    print("\n  → Week's markets...")
    markets  = newsapi_headlines(category="business", page_size=8)
    markets += newsapi_search("S&P 500 Nasdaq stock market weekly sector performance", page_size=8, days_back=6)
    markets += fetch_rss_multi(["marketwatch_top","bbc_business","ft_markets"], max_per_feed=5, max_age_hours=150)

    print("\n  → Week's earnings & deals...")
    earnings_deals  = newsapi_search("earnings results quarterly revenue IPO merger acquisition", page_size=8, days_back=6)
    earnings_deals += fetch_rss_multi(["seeking_alpha","marketwatch_top","guardian_biz"], max_per_feed=5, max_age_hours=150)

    print("\n  → Week's macro...")
    macro  = newsapi_search("Federal Reserve inflation GDP trade policy tariffs economic data", page_size=6, days_back=6)
    macro += fetch_rss_multi(["npr_business","ft_markets","guardian_biz"], max_per_feed=4, max_age_hours=150)

    print("\n  → Week's regulatory...")
    regulatory = newsapi_search("SEC fraud DOJ indictment Fed rate FOMC systemic financial crisis", page_size=5, days_back=6)

    print("\n  → Week's global news...")
    global_news  = newsapi_headlines(category="general", page_size=6)
    global_news += newsapi_search("geopolitics war sanctions diplomacy election crisis international", page_size=6, days_back=6)
    global_news += fetch_rss_multi(["bbc_world","guardian_world","npr_news"], max_per_feed=5, max_age_hours=150)

    print("\n  → Next week calendar...")
    calendar  = newsapi_search("CPI FOMC Fed meeting earnings next week economic calendar", page_size=5, days_back=3)
    calendar += fetch_fmp_earnings_calendar(days_ahead=7)

    print("\n  → Yankees week...")
    yankees  = fetch_rss_multi(["mlb_yankees","espn_mlb"], max_per_feed=6, max_age_hours=150)
    yankees += newsapi_search("New York Yankees MLB", page_size=5, days_back=6)

    return {
        "date":           TODAY,
        "market_data":    market_data,
        "fred_data":      fred_data,
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

def call_claude(system_prompt, user_prompt, max_tokens=4500):
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


SYSTEM_PROMPT = """You are the writer of "The Daily Brief" — a high-finance morning newsletter for Konner Greer, a Finance & Fintech student at the University of Utah (graduating December 2027). He interns at University of Utah Financial Services and is deeply interested in financial markets, economic policy, fintech, management consulting, SEC/DOJ enforcement, and the New York Yankees. His career goals include working in finance, consulting, or financial regulation in DC, NYC, or SF.

TONE & STYLE:
- Write like a sharp sell-side analyst briefing a smart junior — direct, precise, no fluff
- Professional but conversational — polished without being stiff
- Always explain jargon when first used (e.g. "free cash flow (FCF) — the cash a company generates after capital expenditures")
- Structure every story: what happened → why it happened → why it matters
- Be specific — use real numbers, percentages, and names from the source data
- Synthesize and explain — never just restate headlines
- Think like a banker: connect every story to valuations, margins, multiples, or capital flows where possible

REGULATORY / FED FILTER:
- Only include SEC/DOJ/Fed content if genuinely market-moving
- Qualifies: Fed rate decision, major fraud indictment (billion-dollar scale), systemic policy shift, banking crisis
- Does NOT qualify: routine enforcement, small fines, standard speeches
- Return [] for regulatory if nothing clears this bar

DIVERSITY:
- Each section covers DIFFERENT stories — never repeat the same company across sections
- Spread across sectors: tech, finance, energy, healthcare, consumer, industrials, macro, international
- Movers: always lead with indices and macro assets before individual stocks
- 3 Finance Headlines from 3 completely different sectors
- Slow news day = say so clearly in the opening, don't pad

YIELD CURVE CONTEXT:
- If the 10Y-2Y spread is negative (inverted), note this is historically a recession signal
- If VIX > 20, note elevated market anxiety; if VIX > 30, note fear/stress conditions
- Connect yield moves to their real-world implications (mortgage rates, borrowing costs, etc.)

OUTPUT: Valid JSON only. No markdown, no preamble, no code fences. Raw JSON object only.
"""


def generate_weekday_briefing(data):
    sectors_str = fmt_sectors(data.get("sectors", []))
    user_prompt = f"""Today is {data['date']}. Write today's Daily Brief from all source material below.

Return a JSON object with EXACTLY these keys:

{{
  "opening": "3-4 sentences: biggest themes, what kind of morning, yield curve/VIX context if notable, what to watch",

  "macro_dashboard": {{
    "yield_curve_note": "1-2 sentences interpreting the yield curve — is it inverted? steepening? what does it signal?",
    "vix_note": "1 sentence on VIX level and what it means for market sentiment",
    "key_macro": "2-3 sentences on the most important FRED data points — CPI trend, unemployment, credit spreads",
    "sector_leaders": "1 sentence on which S&P sectors are leading and lagging today"
  }},

  "earnings": [
    {{"ticker":"TICKER","company":"Full Name","headline":"Sharp headline",
      "what":"What happened — real EPS/revenue numbers vs estimates",
      "why":"Why it happened — business context, industry dynamics",
      "matters":"Why it matters — mention margins, FCF, or multiples where relevant"}}
  ],

  "earnings_preview": "1-2 sentences on what's reporting later this week based on the earnings calendar",

  "movers": [
    {{"name":"Index or asset","change":"exact value","direction":"up/down/flat",
      "reason":"One clear sentence — connect to macro or news catalyst"}}
  ],

  "deals": [
    {{"type":"M&A/IPO/Fundraise","headline":"Sharp headline",
      "what":"What happened — deal size, parties, structure",
      "matters":"Why it matters — strategic rationale, implied premium if M&A, sector signal"}}
  ],

  "fin_headlines": [
    {{"source":"Outlet","tag":"Sector tag","headline":"Sharp headline",
      "what":"What happened","matters":"Why it matters",
      "context":"Broader context — connect to macro, valuations, or sector trends"}}
  ],

  "regulatory": [
    {{"agency":"SEC/DOJ/Fed","tag":"Topic","headline":"Sharp headline",
      "what":"What happened","matters":"Why this is genuinely market-moving"}}
  ],

  "global_news": [
    {{"region":"Region","tag":"Topic","headline":"Sharp headline",
      "summary":"2-3 sentences: what happened, market implications"}}
  ],

  "silicon_slopes": {{
    "has_news": true or false,
    "company": "Company name or empty string",
    "note": "1 sentence on a Utah/Silicon Slopes company — funding, hiring, product launch, or earnings. If no news, write a 1-sentence fact about the Utah tech ecosystem instead."
  }},

  "term_of_the_day": {{
    "term": "The finance term",
    "definition": "Plain English definition in 1-2 sentences",
    "context": "1 sentence connecting this term to something in today's brief"
  }},

  "yankees": {{
    "result": "Score or Off day",
    "detail": "2-3 sentences on game or latest news",
    "next_game": "Opponent · Date · Time ET · Broadcast"
  }},

  "closing": {{"text": "Quote or insight", "attribution": "— Source"}}
}}

RULES:
- earnings: 3-5 companies. If slow, 1-2 is fine.
- movers: 6-8 items. Use REAL prices from market data. Lead with S&P/Nasdaq/Dow/VIX/yields/oil/gold.
- fin_headlines: exactly 3 from 3 different sectors
- regulatory: [] unless genuinely market-moving
- global_news: exactly 3
- term_of_the_day: pick the most relevant finance concept from today's news (e.g. if yields spike → duration risk; if M&A → implied premium; if oil moves → backwardation/contango)
- silicon_slopes: always populate — real news if available, ecosystem fact if not
- Never use placeholder values — real data or omit

--- EXPANDED MARKET DASHBOARD ---
{fmt_market_data(data.get('market_data', {}))}

--- FRED MACRO INDICATORS (Federal Reserve Economic Data) ---
{fmt_fred_data(data.get('fred_data', {}))}

--- S&P 500 SECTOR PERFORMANCE ---
{sectors_str}

--- UPCOMING EARNINGS CALENDAR ---
{fmt_earnings_calendar(data.get('earnings_calendar', []))}

--- MARKETS & FINANCE ---
{fmt_articles(data['markets'], 16)}

--- EARNINGS NEWS ---
{fmt_articles(data['earnings'], 14)}

--- M&A / IPO / DEALS ---
{fmt_articles(data['deals'], 10)}

--- MACRO & POLICY ---
{fmt_articles(data['macro'], 12)}

--- REGULATORY (strict market-moving filter) ---
{fmt_articles(data['regulatory'], 8)}

--- FINANCE HEADLINES (broad sectors) ---
{fmt_articles(data['fin_headlines'], 14)}

--- GLOBAL NEWS ---
{fmt_articles(data['global_news'], 12)}

--- SILICON SLOPES / UTAH TECH ---
{fmt_articles(data.get('silicon_slopes', []), 6)}

--- YANKEES ---
{fmt_articles(data['yankees'], 6)}
"""
    raw = call_claude(SYSTEM_PROMPT, user_prompt, max_tokens=5000)
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
  "opening": "3-4 sentences: dominant themes, market/macro story, yield curve or VIX context if notable",
  "themes": [
    {{"title":"Theme title","body":"3-4 sentences: what happened, why, what it means going forward"}}
  ],
  "scoreboard": [
    {{"name":"Asset","value":"Price/level","change":"WTD change","direction":"up/down/flat"}}
  ],
  "macro_wrap": {{
    "fred_highlights": "2-3 sentences on the most important macro data released this week — CPI, jobs, GDP, credit spreads",
    "yield_curve": "1-2 sentences on where the yield curve ended the week and what it signals",
    "sectors": "1 sentence on sector rotation — what led, what lagged"
  }},
  "earnings_deals_recap": "3-4 paragraphs synthesizing earnings and deals. What story did earnings tell about the economy? Mention margins, guidance, and sector trends.",
  "macro_policy_geo": [
    {{"tag":"Topic","headline":"Sharp headline","summary":"3-4 sentences: what happened, why it matters, what to watch"}}
  ],
  "regulatory_recap": "1-2 paragraphs ONLY if genuinely market-moving. Otherwise empty string.",
  "watch_next_week": [
    {{"day":"MON/TUE/WED/THU/FRI","event":"Event name","detail":"Why it matters and what to expect"}}
  ],
  "term_of_the_week": {{
    "term": "The week's most important finance concept",
    "definition": "Plain English definition in 2-3 sentences",
    "context": "1-2 sentences connecting it to this week's events"
  }},
  "yankees_week": {{
    "record":"X-Y this week · XX-XX season",
    "summary":"2-3 sentences on the week",
    "next_week":"Upcoming opponents"
  }},
  "closing": {{"text":"Quote fitting for end of week","attribution":"— Source"}}
}}

themes=3 | scoreboard: S&P 500, Nasdaq, Dow, VIX, 10-Yr Yield, 2-Yr Yield, 10Y-2Y Spread, Brent Crude, WTI, Gold, Bitcoin | macro_policy_geo=3 | watch_next_week=4-5

--- REAL-TIME PRICES ---
{fmt_market_data(data.get('market_data', {}))}

--- FRED MACRO DATA ---
{fmt_fred_data(data.get('fred_data', {}))}

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
{fmt_articles(data['calendar'] if isinstance(data['calendar'], list) and all(isinstance(x,dict) and 'title' in x for x in data['calendar']) else [], 8)}

--- YANKEES (WEEK) ---
{fmt_articles(data['yankees'], 8)}
"""
    raw = call_claude(SYSTEM_PROMPT, user_prompt, max_tokens=5000)
    raw = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
    return json.loads(raw)


# ══════════════════════════════════════════════════════════════════════════
#  LAYER 3 — HTML EMAIL RENDERER
# ══════════════════════════════════════════════════════════════════════════

CSS = """<style>
@import url('https://fonts.googleapis.com/css2?family=Playfair+Display:wght@700;900&family=Source+Sans+3:wght@300;400;600&display=swap');
*{margin:0;padding:0;box-sizing:border-box}
body{background:#f0ece4;font-family:'Source Sans 3',Georgia,sans-serif;color:#1a1a1a;font-size:15px;line-height:1.65}
.wrap{max-width:680px;margin:0 auto;background:#faf8f4}
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
.lbl.purple{background:#4a1942}.lbl.rust{background:#8b3a1a}.lbl.charcoal{background:#2a2a2a}
.sec h2{font-family:'Playfair Display',serif;font-size:20px;font-weight:700;color:#0d1b2a;margin-bottom:14px;line-height:1.2}
.story{margin-bottom:20px;padding-bottom:20px;border-bottom:1px dashed #ddd8cf}
.story:last-child{border-bottom:none;margin-bottom:0;padding-bottom:0}
.story-name{font-size:11px;font-weight:600;letter-spacing:1.5px;text-transform:uppercase;color:#c9973a;margin-bottom:5px}
.story-hed{font-family:'Playfair Display',serif;font-size:16px;font-weight:700;color:#0d1b2a;margin-bottom:10px;line-height:1.3}
.story-body{font-size:13.5px;color:#3a3a3a;line-height:1.65}
.wwm p{font-size:13.5px;color:#3a3a3a;margin-bottom:8px;line-height:1.65;padding-left:12px;border-left:2px solid #e2ddd4}
.wwm p strong{color:#0d1b2a}
.mv{display:flex;align-items:baseline;gap:10px;margin-bottom:11px;padding-bottom:11px;border-bottom:1px dashed #ddd8cf}
.mv:last-child{border-bottom:none;margin-bottom:0;padding-bottom:0}
.mv-tk{font-weight:600;font-size:13px;color:#0d1b2a;min-width:110px;letter-spacing:0.5px}
.mv-ch{font-size:13px;font-weight:600;min-width:70px}
.up{color:#1e6b35}.down{color:#8b1a1a}.flat{color:#8a9bb0}
.mv-why{font-size:13px;color:#3a3a3a;flex:1}
/* Macro dashboard grid */
.dash-grid{display:flex;flex-wrap:wrap;gap:0;margin-bottom:16px}
.dash-item{flex:1 1 30%;background:#f0ece4;padding:10px 12px;border:1px solid #e2ddd4;margin:3px;border-radius:2px;min-width:140px}
.dash-lbl{font-size:9px;font-weight:600;letter-spacing:1px;text-transform:uppercase;color:#8a9bb0;margin-bottom:3px}
.dash-val{font-family:'Playfair Display',serif;font-size:16px;font-weight:700;color:#0d1b2a}
.dash-chg{font-size:11px;font-weight:600;margin-top:2px}
.dash-note{font-size:12px;color:#3a3a3a;margin-top:10px;line-height:1.6;padding:10px;background:#f5f2ec;border-left:3px solid #c9973a}
/* Term of the day */
.term-box{background:#0d1b2a;padding:18px 24px;border-radius:3px;margin-top:0}
.term-label{font-size:9px;font-weight:600;letter-spacing:2px;text-transform:uppercase;color:#c9973a;margin-bottom:6px}
.term-word{font-family:'Playfair Display',serif;font-size:20px;font-weight:700;color:#fff;margin-bottom:8px}
.term-def{font-size:13px;color:#d4dfe8;line-height:1.65}
.term-ctx{font-size:12px;color:#8a9bb0;margin-top:8px;font-style:italic}
/* Silicon Slopes */
.slopes-box{background:#f5f2ec;border-left:3px solid #1e4d2b;padding:12px 16px;border-radius:0 3px 3px 0}
.slopes-label{font-size:9px;font-weight:600;letter-spacing:2px;text-transform:uppercase;color:#1e4d2b;margin-bottom:4px}
.slopes-text{font-size:13.5px;color:#3a3a3a;line-height:1.65}
/* Scoreboard */
.sb{display:flex;flex-wrap:wrap;gap:0;margin-bottom:8px}
.sb-item{flex:1 1 28%;background:#f0ece4;padding:10px 12px;border:1px solid #e2ddd4;margin:3px;border-radius:2px}
.sb-lbl{font-size:9px;font-weight:600;letter-spacing:1px;text-transform:uppercase;color:#8a9bb0;margin-bottom:3px}
.sb-val{font-family:'Playfair Display',serif;font-size:16px;font-weight:700;color:#0d1b2a}
.sb-chg{font-size:11px;font-weight:600;margin-top:2px}
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


def render_market_dashboard(market_data, macro_dash):
    """Render the expanded market dashboard with mini tiles."""
    # Key tiles to show
    tile_order = ["^GSPC","^IXIC","^DJI","^VIX","^IRX","^TNX","YIELD_CURVE","CL=F","BZ=F","GC=F","BTC-USD","EURUSD=X"]
    tiles_html = ""
    for sym in tile_order:
        if sym not in market_data:
            continue
        info  = market_data[sym]
        cls   = "up" if info["direction"]=="up" else ("down" if info["direction"]=="down" else "flat")
        arrow = "▲" if info["direction"]=="up" else ("▼" if info["direction"]=="down" else "–")
        if sym == "YIELD_CURVE":
            inv_tag = " ⚠️" if info.get("inverted") else ""
            val_str = f"{info['price']:+.3f}%{inv_tag}"
            chg_str = "Inverted — recession signal" if info.get("inverted") else "Normal slope"
        elif sym in ["^IRX","^TNX","^TYX"]:
            val_str = f"{info['price']:.2f}%"
            chg_str = f"{arrow}{info['change_pct']:+.2f}%"
        elif sym in ["EURUSD=X","JPYUSD=X"]:
            val_str = f"{info['price']:.4f}"
            chg_str = f"{arrow}{info['change_pct']:+.2f}%"
        else:
            val_str = f"{info['price']:,.2f}"
            chg_str = f"{arrow}{info['change_pct']:+.2f}%"
        tiles_html += f"""<div class="dash-item">
          <div class="dash-lbl">{e(info['name'])}</div>
          <div class="dash-val">{e(val_str)}</div>
          <div class="dash-chg {cls}">{e(chg_str)}</div>
        </div>"""

    notes_html = ""
    if macro_dash:
        for key, label in [("yield_curve_note","Yield Curve"), ("vix_note","Volatility"), ("key_macro","Macro"), ("sector_leaders","Sectors")]:
            val = macro_dash.get(key,"")
            if val:
                notes_html += f'<div class="dash-note"><strong>{label}:</strong> {e(val)}</div>'

    return f"""<div class="dash-grid">{tiles_html}</div>{notes_html}"""


def render_weekday(d):
    market_data = d.get("market_data", {})
    macro_dash  = d.get("macro_dashboard", {})

    # Market dashboard
    dashboard_html = render_market_dashboard(market_data, macro_dash)

    # Earnings
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

    ep = d.get("earnings_preview","")
    if ep:
        earnings_html += f'<p class="story-body" style="margin-top:14px;font-style:italic;color:#666;">📅 Coming up: {e(ep)}</p>'

    # Movers
    movers_html = ""
    for mv in d.get("movers", []):
        cls = "up" if mv.get("direction")=="up" else ("down" if mv.get("direction")=="down" else "flat")
        movers_html += f"""<div class="mv">
          <span class="mv-tk">{e(mv.get('name',''))}</span>
          <span class="mv-ch {cls}">{e(mv.get('change',''))}</span>
          <span class="mv-why">{e(mv.get('reason',''))}</span></div>"""
    if not movers_html:
        movers_html = '<p class="story-body">Market data unavailable.</p>'

    # Deals
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

    # Finance headlines
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

    # Regulatory
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

    # Global
    global_html = ""
    for story in d.get("global_news", []):
        global_html += f"""<div class="story">
          <div class="story-name">{e(story.get('region',''))} &nbsp;·&nbsp; {e(story.get('tag',''))}</div>
          <div class="story-hed">{e(story.get('headline',''))}</div>
          <div class="story-body">{e(story.get('summary',''))}</div></div>"""

    # Silicon Slopes
    ss = d.get("silicon_slopes", {})
    ss_company = f"<strong>{e(ss.get('company','Utah Tech'))}</strong> &nbsp;·&nbsp; " if ss.get("company") else ""
    slopes_section = f"""<div class="sec">
  <div class="lbl green">Silicon Slopes</div>
  <h2>Utah Tech Spotlight</h2>
  <div class="slopes-box">
    <div class="slopes-label">Local Intel</div>
    <div class="slopes-text">{ss_company}{e(ss.get('note','No Utah tech news today.'))}</div>
  </div>
</div>"""

    # Term of the Day
    tod = d.get("term_of_the_day", {})
    term_section = f"""<div class="sec">
  <div class="lbl charcoal">Finance Vocab</div>
  <h2>Term of the Day</h2>
  <div class="term-box">
    <div class="term-label">Today's Concept</div>
    <div class="term-word">{e(tod.get('term',''))}</div>
    <div class="term-def">{e(tod.get('definition',''))}</div>
    <div class="term-ctx">{e(tod.get('context',''))}</div>
  </div>
</div>"""

    # Yankees
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

<div class="sec">
  <div class="lbl slate">Market Dashboard</div>
  <h2>Pre-Market Snapshot</h2>
  {dashboard_html}
</div>

<div class="sec">
  <div class="lbl gold">Earnings Corner</div>
  <h2>Who Reported &amp; What It Means</h2>
  {earnings_html}
</div>

<div class="sec">
  <div class="lbl slate">On the Move</div>
  <h2>Major Movers &amp; Why</h2>
  {movers_html}
</div>

<div class="sec">
  <div class="lbl">M&amp;A · IPO · Capital Markets</div>
  <h2>Deals &amp; Raises</h2>
  {deals_html}
</div>

<div class="sec">
  <div class="lbl gold">Today's Headlines</div>
  <h2>Finance &amp; Markets</h2>
  {fin_html}
</div>

{reg_section}

<div class="sec">
  <div class="lbl green">Global News</div>
  <h2>World Stories That Matter</h2>
  {global_html}
</div>

{slopes_section}

{term_section}

<div class="sec">
  <div class="lbl navy">Yankees</div>
  <h2>Bronx Update</h2>
  <div class="ynk">
    <div class="ynk-score">{e(y.get('result','No game data'))}</div>
    <div class="ynk-detail">{e(y.get('detail',''))}</div>
    <div class="ynk-next">▶ Next: {e(y.get('next_game','Check MLB.com'))}</div>
  </div>
</div>

<div class="closing">
  <blockquote>"{e(cl.get('text',''))}"</blockquote>
  <cite>{e(cl.get('attribution',''))}</cite>
</div>

<div class="footer">
  <p>The Daily Brief &nbsp;·&nbsp; Built for <span>Konner Greer</span> &nbsp;·&nbsp; University of Utah, Finance &amp; Fintech '27</p>
  <p style="margin-top:4px;">Delivered every weekday at 7:00 AM MT &nbsp;·&nbsp; <span>Markets open at 7:30 AM MT</span></p>
</div>

</div></body></html>"""


def render_saturday(d):
    market_data = d.get("market_data", {})

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

    # Macro wrap
    mw = d.get("macro_wrap", {})
    macro_wrap_html = ""
    if mw:
        for key, label in [("fred_highlights","Key Macro Data"), ("yield_curve","Yield Curve"), ("sectors","Sector Rotation")]:
            val = mw.get(key,"")
            if val:
                macro_wrap_html += f'<div class="dash-note" style="margin-bottom:8px"><strong>{label}:</strong> {e(val)}</div>'

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

    # Term of the week
    tow = d.get("term_of_the_week", {})
    term_section = ""
    if tow.get("term"):
        term_section = f"""<div class="sec">
  <div class="lbl charcoal">Finance Vocab</div>
  <h2>Term of the Week</h2>
  <div class="term-box">
    <div class="term-label">This Week's Concept</div>
    <div class="term-word">{e(tow.get('term',''))}</div>
    <div class="term-def">{e(tow.get('definition',''))}</div>
    <div class="term-ctx">{e(tow.get('context',''))}</div>
  </div>
</div>"""

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

<div class="sec">
  <div class="lbl gold">The Big Picture</div>
  <h2>This Week's Defining Themes</h2>
  {themes_html}
</div>

<div class="sec">
  <div class="lbl slate">Markets</div>
  <h2>Weekly Scoreboard</h2>
  {sb_html}
  {macro_wrap_html}
</div>

<div class="sec">
  <div class="lbl gold">Earnings &amp; Deals</div>
  <h2>What Moved Needles This Week</h2>
  <div class="story-body" style="font-size:14px;line-height:1.75;color:#3a3a3a">{e(d.get('earnings_deals_recap',''))}</div>
</div>

<div class="sec">
  <div class="lbl green">Macro · Policy · World</div>
  <h2>The Bigger Forces at Work</h2>
  {macro_html}
</div>

{reg_section}

<div class="sec">
  <div class="lbl teal">What to Watch</div>
  <h2>Next Week's Calendar</h2>
  {watch_html}
</div>

{term_section}

<div class="sec">
  <div class="lbl navy">Yankees</div>
  <h2>Week in the Bronx</h2>
  <div class="ynk">
    <div class="ynk-score">{e(y.get('record',''))}</div>
    <div class="ynk-detail">{e(y.get('summary',''))}</div>
    <div class="ynk-next">▶ Next week: {e(y.get('next_week','Check MLB.com'))}</div>
  </div>
</div>

<div class="closing">
  <blockquote>"{e(cl.get('text',''))}"</blockquote>
  <cite>{e(cl.get('attribution',''))}</cite>
</div>

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
        mon  = NOW - timedelta(days=NOW.weekday())
        fri  = mon + timedelta(days=4)
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
