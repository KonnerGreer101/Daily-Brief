"""
The Daily Brief v3.0
Morning edition (pre-market), Closing edition (after the bell), Weekly edition (Saturday).
Schedules run in UTC; the script checks Eastern time so daylight saving never shifts delivery.
"""

import os, json, smtplib, time, urllib.request, urllib.parse, urllib.error, xml.etree.ElementTree as ET, re, html as htmllib
import imaplib, email, email.utils
from email.header import decode_header, make_header
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from zoneinfo import ZoneInfo

# ── Config ──────────────────────────────────────────────────────────────
ANTHROPIC_KEY = os.environ["ANTHROPIC_API_KEY"]
NEWS_KEY      = os.environ.get("NEWS_API_KEY", "")
FINNHUB_KEY   = os.environ.get("FINNHUB_API_KEY", "")
GNEWS_KEY     = os.environ.get("GNEWS_API_KEY", "")
FRED_KEY      = os.environ.get("FRED_API_KEY", "")
FMP_KEY       = os.environ.get("FMP_API_KEY", "")
AV_KEY        = os.environ.get("ALPHA_VANTAGE_API_KEY", "")
BLS_KEY       = os.environ.get("BLS_API_KEY", "")
BEA_KEY       = os.environ.get("BEA_API_KEY", "")
DATA_GOV_KEY  = os.environ.get("DATA_GOV_API_KEY", "")
EIA_KEY       = os.environ.get("EIA_API_KEY", "")
GMAIL_USER    = os.environ["GMAIL_USER"]
GMAIL_PASS    = os.environ["GMAIL_APP_PASS"]

MODEL           = "claude-opus-4-5"
WEB_SEARCH_USES = {"morning": 10, "close": 8, "weekly": 10}
NEWSLETTER_MODELS = ["claude-haiku-5-5", "claude-haiku-4-5"]   # pass 1 (cheap extraction); falls back if the first is unavailable
NEWSLETTER_LABEL  = "Brief Sources"                             # Gmail label the filter applies   # live searches Claude may run per edition

ET_TZ  = ZoneInfo("America/New_York")
MT     = ZoneInfo("America/Denver")
NOW    = datetime.now(ET_TZ)
TODAY  = NOW.strftime("%A, %B %d, %Y")
DATE_KEY = NOW.strftime("%Y-%m-%d")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/rss+xml, application/xml, application/json, text/xml, */*",
    "Accept-Language": "en-US,en;q=0.9",
}
# SEC requires a descriptive User-Agent with contact info
SEC_HEADERS = {"User-Agent": f"Daily Brief personal research {GMAIL_USER}", "Accept-Encoding": "identity"}

STATE_FILE = "brief_state.json"

# Delivery windows in Eastern time (minutes after midnight)
WINDOWS = {
    "morning": (6 * 60 + 45, 9 * 60 + 25),    # before the 9:30 open
    "close":   (16 * 60 + 20, 19 * 60 + 30),  # after the 4:00 close
}


# ══════════════════════════════════════════════════════════════════════════
#  STATE + EDITION LOGIC
# ══════════════════════════════════════════════════════════════════════════

def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}

def save_state(state):
    state["last_run"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

def resolve_edition():
    """Returns (edition, forced). Manual runs pass EDITION; scheduled runs decide from ET time."""
    forced = os.environ.get("EDITION", "").strip().lower()
    if forced in ("morning", "close", "weekly"):
        return forced, True
    wd, mins = NOW.weekday(), NOW.hour * 60 + NOW.minute
    if wd == 5:
        return "weekly", False
    if wd < 5:
        for ed, (start, end) in WINDOWS.items():
            if start <= mins < end:
                return ed, False
    return None, False


def http_json(url, headers=None, data=None, timeout=15):
    req = urllib.request.Request(url, headers=headers or HEADERS, data=data)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())

def http_text(url, headers=None, timeout=15):
    req = urllib.request.Request(url, headers=headers or HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")

def strip_html(s):
    s = re.sub(r"(?is)<(script|style).*?</\1>", " ", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = htmllib.unescape(s)
    return re.sub(r"\s+", " ", s).strip()
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
    "CompoundingW":    "Compounding W",
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

                # Extract image from post (charts, graphs, screenshots)
                image_url = ""
                desc_el = entry.find("description")
                desc_html = desc_el.text if desc_el is not None and desc_el.text else ""
                img_match = re.search(r'<img[^>]+src="([^"]+)"', desc_html)
                if img_match:
                    src = img_match.group(1)
                    # Convert Nitter proxy URL to direct pbs.twimg.com URL (more reliable in email)
                    m = re.search(r'/pic/(?:orig/)?(.+)', src)
                    if m:
                        decoded = urllib.parse.unquote(m.group(1))
                        image_url = f"https://pbs.twimg.com/{decoded}"
                    else:
                        image_url = src

                # Clean HTML from title
                title = re.sub(r'<[^>]+>', '', title).strip()
                items.append({
                    "title":       title,
                    "description": "",
                    "source":      f"@{handle} ({display_name})",
                    "published":   pub_str[:16],
                    "image_url":   image_url,
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

def _first(entry, *tags, ns=None):
    """Return the first matching child element. (The old `a or b` pattern was a bug:
    ElementTree elements with no children are falsy, so every RSS title was dropped.)"""
    for t in tags:
        el = entry.find(t, ns) if ":" in t else entry.find(t)
        if el is not None:
            return el
    return None

def _link(entry, ns):
    el = entry.find("link")
    if el is not None and (el.text or "").strip():
        return el.text.strip()
    el = entry.find("atom:link", ns)
    return el.get("href", "") if el is not None else ""

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
            title_el = _first(entry, "title", "atom:title", ns=ns)
            title = (title_el.text or "").strip() if title_el is not None else ""
            if not title or "[Removed]" in title:
                continue
            desc_el = _first(entry, "description", "summary", "atom:summary", ns=ns)
            desc = re.sub(r'<[^>]+>', '', (desc_el.text or "") if desc_el is not None else "").strip()[:200]
            pub_el  = _first(entry, "pubDate", "published", "atom:published", "atom:updated", ns=ns)
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
                "title": title, "description": desc, "link": _link(entry, ns),
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
        desc = f" | {a['description'][:150]}" if a.get("description") else ""
        lines.append(f"• [{a.get('source','')}] {t}{desc}")
        if len(lines) >= n:
            break
    return "\n".join(lines) if lines else "No articles found."


def fmt_x_posts(posts, n=10):
    """X post list for Claude prompt — flags posts with chart/data images."""
    if not posts:
        return "No posts found."
    seen, lines = set(), []
    for p in posts:
        t = p.get("title","").strip()
        if not t or t in seen:
            continue
        seen.add(t)
        img = f" [HAS IMAGE: {p['image_url']}]" if p.get("image_url") else ""
        lines.append(f"• [{p.get('source','')}] {t}{img}")
        if len(lines) >= n:
            break
    return "\n".join(lines) if lines else "No posts found."


# ══════════════════════════════════════════════════════════════════════════
#  MARKET DATA (yfinance)
# ══════════════════════════════════════════════════════════════════════════

INDEX_TICKERS = {
    "^GSPC": "S&P 500", "^IXIC": "Nasdaq", "^DJI": "Dow", "^RUT": "Russell 2000",
    "^VIX": "VIX", "^IRX": "3-Mo T-Bill", "^TNX": "10-Yr Yield", "^TYX": "30-Yr Yield",
    "CL=F": "WTI Crude", "BZ=F": "Brent Crude", "GC=F": "Gold",
    "DX-Y.NYB": "Dollar Index", "BTC-USD": "Bitcoin",
}
FUTURES_TICKERS = {"ES=F": "S&P Futures", "NQ=F": "Nasdaq Futures", "YM=F": "Dow Futures", "RTY=F": "Russell Futures"}

# Large-cap watchlist used for movers and earnings filtering
WATCHLIST = [
    "AAPL","MSFT","NVDA","GOOGL","AMZN","META","TSLA","AVGO","ORCL","AMD","MU","TSM","ASML","ARM","QCOM","INTC",
    "CRM","ADBE","NOW","PLTR","SNOW","ANET","SMCI","DELL","VRT","CRWV","IBM","NFLX",
    "JPM","GS","MS","BAC","C","WFC","BLK","BX","KKR","APO","SCHW","V","MA","AXP","PYPL","COIN","HOOD",
    "BRK-B","UNH","LLY","NVO","JNJ","ABBV","MRK","PFE",
    "XOM","CVX","COP","WMT","COST","HD","NKE","MCD","SBUX","DIS","BA","CAT","GE","LMT","RTX","UBER",
]

def _pct(curr, prev):
    return round((curr - prev) / prev * 100, 2) if prev else 0.0

def fetch_quotes(tickers, mode="session"):
    """mode='session': most recent COMPLETED session vs prior (indexes).
       mode='live': latest price vs prior close (futures, overnight).
       mode='week': last close vs close 5 sessions earlier."""
    try:
        import yfinance as yf
    except ImportError:
        print("    yfinance not available")
        return {}
    out = {}
    today_et = NOW.date()
    market_done = NOW.hour >= 16
    for sym, name in tickers.items():
        try:
            closes = yf.Ticker(sym).history(period="15d")["Close"].dropna()
            if len(closes) < 2:
                continue
            if mode == "week" and len(closes) >= 6:
                curr, prev = float(closes.iloc[-1]), float(closes.iloc[-6])
            elif mode == "live":
                curr, prev = float(closes.iloc[-1]), float(closes.iloc[-2])
            else:
                last_is_today = closes.index[-1].date() == today_et
                if last_is_today and not market_done and len(closes) >= 3:
                    curr, prev = float(closes.iloc[-2]), float(closes.iloc[-3])
                else:
                    curr, prev = float(closes.iloc[-1]), float(closes.iloc[-2])
            pct = _pct(curr, prev)
            asof = closes.index[-1].date() if mode != "session" or not (closes.index[-1].date() == today_et and not market_done) else closes.index[-2].date()
            out[sym] = {"name": name, "price": round(curr, 2), "change_pct": pct, "asof": asof.strftime("%a %b %d"),
                        "direction": "up" if pct > 0.05 else ("down" if pct < -0.05 else "flat")}
        except Exception as ex:
            print(f"    yfinance [{sym}]: {ex}")
    if "^TNX" in out and "^IRX" in out:
        spread = round(out["^TNX"]["price"] - out["^IRX"]["price"], 2)
        out["CURVE"] = {"name": "10Y-3M Spread", "price": spread, "change_pct": 0,
                        "direction": "up" if spread > 0 else "down", "inverted": spread < 0}
    print(f"    [yfinance {mode}] {len(out)} instruments")
    return out

def fetch_movers(mode="session", n=6):
    """Biggest movers among the large-cap watchlist (one batched download)."""
    try:
        import yfinance as yf
        df = yf.download(WATCHLIST, period="15d", progress=False, auto_adjust=True, threads=True)["Close"]
        rows = []
        for sym in WATCHLIST:
            s = df[sym].dropna() if sym in df else None
            if s is None or len(s) < 6:
                continue
            curr, prev = (float(s.iloc[-1]), float(s.iloc[-6])) if mode == "week" else (float(s.iloc[-1]), float(s.iloc[-2]))
            rows.append({"ticker": sym, "price": round(curr, 2), "change_pct": _pct(curr, prev)})
        rows.sort(key=lambda r: r["change_pct"])
        movers = rows[-n:][::-1] + rows[:n]
        print(f"    [movers {mode}] {len(movers)}")
        return movers
    except Exception as ex:
        print(f"    [movers]: {ex}")
        return []

def fmt_quotes(q):
    if not q:
        return "No market data."
    lines = []
    for sym, i in q.items():
        if sym == "CURVE":
            lines.append(f"• 10Y-3M spread: {i['price']:+.2f} pts{' (INVERTED)' if i.get('inverted') else ''}")
        else:
            lines.append(f"• {i['name']}: {i['price']:,.2f} ({i['change_pct']:+.2f}%)" + (f" [as of {i['asof']}]" if i.get("asof") else ""))
    return "\n".join(lines)

def fmt_movers(m):
    return "\n".join(f"• {r['ticker']}: ${r['price']:,.2f} ({r['change_pct']:+.2f}%)" for r in m) or "No mover data."

# ══════════════════════════════════════════════════════════════════════════
#  MARKET-WIDE MOVERS, PRE-MARKET MOVERS, INSIDER BUYS, ACTIVIST STAKES
# ══════════════════════════════════════════════════════════════════════════

def fetch_market_screens(min_cap=2e9, n=6):
    """Market-wide top gainers, losers, and most active (companies over $2B), via Yahoo screeners."""
    out = {}
    try:
        import yfinance as yf
        for key, query in [("gainers", "day_gainers"), ("losers", "day_losers"), ("most_active", "most_actives")]:
            try:
                res = yf.screen(query, count=100)
                rows = []
                for q in res.get("quotes", []):
                    if (q.get("marketCap") or 0) < min_cap or (q.get("regularMarketPrice") or 0) < 5:
                        continue
                    rows.append({"ticker": q.get("symbol"), "name": q.get("shortName") or q.get("longName") or "",
                                 "price": q.get("regularMarketPrice"), "change_pct": round(q.get("regularMarketChangePercent") or 0, 2),
                                 "cap_b": round((q.get("marketCap") or 0) / 1e9, 1), "volume": q.get("regularMarketVolume")})
                out[key] = rows[:n]
            except Exception as ex:
                print(f"    [screen {query}]: {ex}")
    except ImportError:
        pass
    print(f"    [market screens] " + ", ".join(f"{k}={len(v)}" for k, v in out.items()))
    return out

def fmt_screens(s):
    if not s or not any(s.values()):
        return "Market-wide screens unavailable; use the large-cap movers list."
    parts = []
    for key, label in [("gainers", "TOP GAINERS"), ("losers", "TOP LOSERS"), ("most_active", "MOST ACTIVE")]:
        rows = s.get(key) or []
        parts.append(label + ":\n" + "\n".join(
            f"• {r['ticker']} ({r['name']}, ${r['cap_b']}B cap): ${r['price']} ({r['change_pct']:+.2f}%)" for r in rows))
    return "\n".join(parts)

def fetch_premarket_movers(n=8):
    """Pre-market moves for the large-cap watchlist: latest extended-hours price vs prior regular close."""
    try:
        import yfinance as yf
        df = yf.download(WATCHLIST, period="5d", interval="5m", prepost=True, progress=False, auto_adjust=True, threads=True)["Close"]
        rows = []
        today = NOW.date()
        for sym in WATCHLIST:
            if sym not in df:
                continue
            s = df[sym].dropna()
            if s.empty:
                continue
            idx = s.index.tz_convert(ET_TZ)
            prior = s[(idx.date < today) & (idx.hour * 60 + idx.minute < 16 * 60) & (idx.hour * 60 + idx.minute >= 9 * 60 + 30)]
            if prior.empty:
                continue
            prev, last = float(prior.iloc[-1]), float(s.iloc[-1])
            rows.append({"ticker": sym, "price": round(last, 2), "change_pct": _pct(last, prev)})
        rows.sort(key=lambda r: abs(r["change_pct"]), reverse=True)
        print(f"    [pre-market movers] {len(rows[:n])}")
        return rows[:n]
    except Exception as ex:
        print(f"    [pre-market movers]: {ex}")
        return []

def fetch_insider_buys(days=4, min_value=250_000):
    """Open-market insider purchases (Form 4 code P) at watchlist companies, via Finnhub."""
    if not FINNHUB_KEY:
        return []
    since = (NOW - timedelta(days=days)).strftime("%Y-%m-%d")
    out = []
    for sym in WATCHLIST:
        try:
            q = urllib.parse.urlencode({"symbol": sym, "from": since, "token": FINNHUB_KEY})
            for t in http_json(f"https://finnhub.io/api/v1/stock/insider-transactions?{q}").get("data", []):
                if t.get("transactionCode") != "P":
                    continue
                value = abs(t.get("change") or 0) * (t.get("transactionPrice") or 0)
                if value >= min_value:
                    out.append(f"• {sym}: {t.get('name')} bought {abs(t.get('change')):,} shares at ${t.get('transactionPrice')} (≈${value/1e6:.1f}M) on {t.get('transactionDate')}")
        except Exception as ex:
            print(f"    [insider {sym}]: {ex}")
            if "429" in str(ex):
                time.sleep(5)
        time.sleep(1.05)   # Finnhub free tier: 60 calls/minute
    print(f"    [insider buys] {len(out)}")
    return out[:15]

def fetch_activist_13d(n=40):
    """New Schedule 13D filings (activist or control-intent stakes) from EDGAR's latest-filings feed."""
    out, seen = [], set()
    for form in ["SCHEDULE 13D", "SC 13D"]:
        try:
            q = urllib.parse.urlencode({"action": "getcurrent", "type": form, "owner": "include", "count": n, "output": "atom"})
            root = ET.fromstring(http_text(f"https://www.sec.gov/cgi-bin/browse-edgar?{q}", headers=SEC_HEADERS))
            ns = {"a": "http://www.w3.org/2005/Atom"}
            for ent in root.findall("a:entry", ns):
                title = (ent.findtext("a:title", "", ns) or "").strip()
                if "(Subject)" in title and title not in seen and not title.upper().startswith(form + "/A"):
                    seen.add(title)
                    out.append("• " + title.replace("(Subject)", "").strip())
        except Exception as ex:
            print(f"    [13D {form}]: {ex}")
    print(f"    [13D] {len(out)}")
    return out[:15]

def fetch_nyfed_rates():
    try:
        rows = http_json("https://markets.newyorkfed.org/api/rates/all/latest.json").get("refRates", [])
        out = [f"• {r.get('type')}: {r.get('percentRate')}% ({r.get('effectiveDate')}), volume ${r.get('volumeInBillions')}B"
               for r in rows if r.get("type") in ("SOFR", "EFFR", "OBFR", "TGCR")]
        print(f"    [NY Fed] {len(out)}")
        return out
    except Exception as ex:
        print(f"    [NY Fed]: {ex}")
        return []

COT_MARKETS = {"E-MINI S&P 500": "S&P 500 e-mini", "UST 10Y NOTE": "10-Yr Treasury note",
               "CRUDE OIL, LIGHT SWEET": "WTI crude", "GOLD - COMMODITY EXCHANGE": "Gold",
               "U.S. DOLLAR INDEX": "Dollar index", "NASDAQ-100": "Nasdaq-100"}

def fetch_cot():
    """CFTC Commitments of Traders: speculator (non-commercial) net positioning, week over week."""
    try:
        since = (NOW - timedelta(days=21)).strftime("%Y-%m-%dT00:00:00")
        q = urllib.parse.urlencode({"$where": f"report_date_as_yyyy_mm_dd >= '{since}'", "$limit": 5000,
                                    "$order": "report_date_as_yyyy_mm_dd DESC"})
        rows = http_json(f"https://publicreporting.cftc.gov/resource/6dca-aqww.json?{q}", timeout=30)
        out = []
        for key, label in COT_MARKETS.items():
            hits = [r for r in rows if r.get("market_and_exchange_names", "").upper().startswith(key)]
            if len(hits) >= 2:
                net = [int(float(h["noncomm_positions_long_all"])) - int(float(h["noncomm_positions_short_all"])) for h in hits[:2]]
                out.append(f"• {label}: speculators net {net[0]:+,} contracts (week of {hits[0]['report_date_as_yyyy_mm_dd'][:10]}), change {net[0]-net[1]:+,}")
        print(f"    [CFTC COT] {len(out)}")
        return out
    except Exception as ex:
        print(f"    [CFTC]: {ex}")
        return []



# ══════════════════════════════════════════════════════════════════════════
#  EARNINGS (Finnhub calendar, FMP fallback) filtered to large caps
# ══════════════════════════════════════════════════════════════════════════

def _market_cap_b(sym):
    try:
        import yfinance as yf
        mc = yf.Ticker(sym).fast_info.get("marketCap") or 0
        return mc / 1e9
    except Exception:
        return 0

def fetch_earnings(start, end, min_cap_b=50):
    items = []
    if FINNHUB_KEY:
        try:
            q = urllib.parse.urlencode({"from": start, "to": end, "token": FINNHUB_KEY})
            data = http_json(f"https://finnhub.io/api/v1/calendar/earnings?{q}")
            for i in data.get("earningsCalendar", []):
                items.append({"ticker": i.get("symbol", ""), "date": i.get("date", ""),
                              "hour": {"bmo": "before open", "amc": "after close"}.get(i.get("hour", ""), i.get("hour", "")),
                              "eps_est": i.get("epsEstimate"), "eps_act": i.get("epsActual"),
                              "rev_est": i.get("revenueEstimate"), "rev_act": i.get("revenueActual")})
        except Exception as ex:
            print(f"    [finnhub earnings]: {ex}")
    if not items and FMP_KEY:
        try:
            q = urllib.parse.urlencode({"from": start, "to": end, "apikey": FMP_KEY})
            for i in http_json(f"https://financialmodelingprep.com/api/v3/earning_calendar?{q}"):
                items.append({"ticker": i.get("symbol", ""), "date": i.get("date", ""), "hour": i.get("time", ""),
                              "eps_est": i.get("epsEstimated"), "eps_act": i.get("eps"),
                              "rev_est": i.get("revenueEstimated"), "rev_act": i.get("revenue")})
        except Exception as ex:
            print(f"    [FMP earnings]: {ex}")
    # Filter: watchlist names always; others only if revenue estimate suggests size, then confirm market cap
    keep = []
    for i in items:
        t = i["ticker"]
        if not t or "." in t:
            continue
        if t in WATCHLIST:
            keep.append(i)
        elif (i.get("rev_est") or 0) >= 1.5e9 and _market_cap_b(t) >= min_cap_b:
            keep.append(i)
    print(f"    [earnings {start}..{end}] {len(keep)} large-cap of {len(items)}")
    return keep[:20]

def fmt_earnings(items):
    if not items:
        return "No large-cap earnings in this window."
    def money(v):
        return f"${v/1e9:.2f}B" if v and v > 1e6 else ("n/a" if v is None else str(v))
    lines = []
    for i in items:
        s = f"• {i['ticker']} ({i['date']}, {i['hour'] or 'time n/a'}): EPS est {i['eps_est']}, rev est {money(i.get('rev_est'))}"
        if i.get("eps_act") is not None:
            s += f" | ACTUAL EPS {i['eps_act']}, rev {money(i.get('rev_act'))}"
        lines.append(s)
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════
#  MACRO: BLS, BEA, EIA, Treasury auctions, FRED
# ══════════════════════════════════════════════════════════════════════════

BLS_SERIES = {
    "CUSR0000SA0":        ("CPI (SA index)", "mom_pct"),
    "CUSR0000SA0L1E":     ("Core CPI (SA index)", "mom_pct"),
    "WPUFD4":             ("PPI final demand (SA index)", "mom_pct"),
    "CES0000000001":      ("Nonfarm payrolls (thousands)", "diff"),
    "LNS14000000":        ("Unemployment rate (%)", "level"),
    "CES0500000003":      ("Avg hourly earnings ($)", "mom_pct"),
    "JTS000000000000000JOL": ("JOLTS job openings (thousands)", "diff"),
}

def fetch_bls():
    if not BLS_KEY:
        return {}
    try:
        body = json.dumps({"seriesid": list(BLS_SERIES), "startyear": str(NOW.year - 1),
                           "endyear": str(NOW.year), "registrationkey": BLS_KEY}).encode()
        data = http_json("https://api.bls.gov/publicAPI/v2/timeseries/data/",
                         headers={"Content-Type": "application/json"}, data=body, timeout=25)
        out = {}
        for s in data.get("Results", {}).get("series", []):
            sid = s.get("seriesID")
            obs = [o for o in s.get("data", []) if o.get("value") not in ("-", "")]
            if len(obs) < 2 or sid not in BLS_SERIES:
                continue
            name, kind = BLS_SERIES[sid]
            v0, v1 = float(obs[0]["value"]), float(obs[1]["value"])
            chg = round((v0 - v1) / v1 * 100, 2) if kind == "mom_pct" else (round(v0 - v1, 1) if kind == "diff" else round(v0 - v1, 2))
            out[sid] = {"name": name, "period": f"{obs[0]['periodName']} {obs[0]['year']}",
                        "value": v0, "change": chg, "kind": kind}
        print(f"    [BLS] {len(out)} series")
        return out
    except Exception as ex:
        print(f"    [BLS]: {ex}")
        return {}

def _bea_table(table, freq, line_match):
    q = urllib.parse.urlencode({"UserID": BEA_KEY, "method": "GetData", "datasetname": "NIPA",
                                "TableName": table, "Frequency": freq, "Year": f"{NOW.year-1},{NOW.year}",
                                "ResultFormat": "JSON"})
    data = http_json(f"https://apps.bea.gov/api/data/?{q}", timeout=25)
    rows = data.get("BEAAPI", {}).get("Results", {}).get("Data", [])
    out = {}
    for label, pattern in line_match.items():
        hits = [r for r in rows if re.search(pattern, r.get("LineDescription", ""), re.I)]
        hits.sort(key=lambda r: r.get("TimePeriod", ""))
        if hits:
            out[label] = {"period": hits[-1]["TimePeriod"], "value": hits[-1]["DataValue"],
                          "prior": hits[-2]["DataValue"] if len(hits) > 1 else None}
    return out

def fetch_bea():
    if not BEA_KEY:
        return {}
    out = {}
    try:
        g = _bea_table("T10101", "Q", {"Real GDP growth (annualized %)": r"^Gross domestic product$"})
        out.update(g)
    except Exception as ex:
        print(f"    [BEA GDP]: {ex}")
    try:
        p = _bea_table("T20807", "M", {"PCE prices (m/m %)": r"^Personal consumption expenditures \(PCE\)$",
                                        "Core PCE prices (m/m %)": r"excluding food and energy"})
        out.update(p)
    except Exception as ex:
        print(f"    [BEA PCE]: {ex}")
    print(f"    [BEA] {len(out)} series")
    return out

def fetch_eia():
    if not EIA_KEY:
        return {}
    series = {
        "Crude inventories ex-SPR (k bbl)": ("petroleum/stoc/wstk", "WCESTUS1"),
        "Natural gas storage (Bcf)":        ("natural-gas/stor/wkly", "NW2_EPG0_SWO_R48_BCF"),
    }
    out = {}
    for name, (route, sid) in series.items():
        try:
            q = urllib.parse.urlencode({"api_key": EIA_KEY, "frequency": "weekly", "data[0]": "value",
                                        "facets[series][]": sid, "sort[0][column]": "period",
                                        "sort[0][direction]": "desc", "length": 2})
            rows = http_json(f"https://api.eia.gov/v2/{route}/data/?{q}").get("response", {}).get("data", [])
            if len(rows) >= 2:
                v0, v1 = float(rows[0]["value"]), float(rows[1]["value"])
                out[name] = {"period": rows[0]["period"], "value": v0, "change": round(v0 - v1, 1)}
        except Exception as ex:
            print(f"    [EIA {sid}]: {ex}")
    print(f"    [EIA] {len(out)} series")
    return out

def fetch_treasury_auctions(days_back=2):
    try:
        since = (NOW - timedelta(days=days_back)).strftime("%Y-%m-%d")
        q = urllib.parse.urlencode({"filter": f"auction_date:gte:{since}", "sort": "-auction_date", "page[size]": 25})
        rows = http_json("https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/od/auctions_query?" + q).get("data", [])
        out = [{"term": r.get("security_term"), "type": r.get("security_type"), "date": r.get("auction_date"),
                "high_yield": r.get("high_yield"), "bid_to_cover": r.get("bid_to_cover_ratio"),
                "size": r.get("offering_amt")}
               for r in rows if r.get("security_type") in ("Note", "Bond") and r.get("high_yield") not in (None, "", "null")]
        print(f"    [Treasury auctions] {len(out)}")
        return out
    except Exception as ex:
        print(f"    [Treasury]: {ex}")
        return []

FRED_SERIES = {
    "DFF": "Effective Fed Funds Rate", "T10Y2Y": "10Y-2Y Spread", "DGS2": "2-Yr Treasury Yield",
    "BAMLH0A0HYM2": "High Yield Credit Spread", "BAMLC0A0CM": "Investment Grade Spread",
    "UMCSENT": "Consumer Sentiment", "RSAFS": "Retail Sales ($M)", "ICSA": "Initial Jobless Claims",
}

def fetch_fred():
    if not FRED_KEY:
        return {}
    out = {}
    for sid, name in FRED_SERIES.items():
        try:
            q = urllib.parse.urlencode({"series_id": sid, "api_key": FRED_KEY, "file_type": "json",
                                        "sort_order": "desc", "limit": 2})
            obs = [o for o in http_json(f"https://api.stlouisfed.org/fred/series/observations?{q}", timeout=10)
                   .get("observations", []) if o.get("value") != "."]
            if obs:
                v = float(obs[0]["value"])
                out[sid] = {"name": name, "value": v, "date": obs[0]["date"],
                            "change": round(v - float(obs[1]["value"]), 3) if len(obs) > 1 else None}
        except Exception as ex:
            print(f"    [FRED {sid}]: {ex}")
    print(f"    [FRED] {len(out)} series")
    return out

def gather_macro(state):
    """Pulls all macro sources and flags anything that changed since the last run (= newly released)."""
    bls, bea, eia, fred = fetch_bls(), fetch_bea(), fetch_eia(), fetch_fred()
    seen = state.setdefault("macro_seen", {})
    lines, new_releases = [], []
    def add(key, text, stamp):
        is_new = key in seen and seen[key] != stamp
        seen[key] = stamp
        lines.append(("🆕 NEW RELEASE: " if is_new else "• ") + text)
        if is_new:
            new_releases.append(text)
    for sid, d in bls.items():
        unit = "% m/m" if d["kind"] == "mom_pct" else ("k change" if d["kind"] == "diff" else " pt change")
        add(f"bls:{sid}", f"{d['name']} for {d['period']}: {d['value']:,} ({d['change']:+}{unit})", d["period"])
    for name, d in bea.items():
        add(f"bea:{name}", f"{name} for {d['period']}: {d['value']} (prior {d['prior']})", d["period"])
    for name, d in eia.items():
        add(f"eia:{name}", f"{name} week of {d['period']}: {d['value']:,} ({d['change']:+,} w/w)", d["period"])
    for sid, d in fred.items():
        chg = f" (change {d['change']:+})" if d.get("change") is not None else ""
        add(f"fred:{sid}", f"{d['name']}: {d['value']}{chg} as of {d['date']}", d["date"])
    auctions = fetch_treasury_auctions()
    for a in auctions:
        lines.append(f"• Treasury auction {a['date']}: {a['term']} {a['type']} high yield {a['high_yield']}%, bid-to-cover {a['bid_to_cover']}")
    return "\n".join(lines) or "Macro data unavailable.", new_releases


# ══════════════════════════════════════════════════════════════════════════
#  SEC EDGAR: new merger agreements + press-release text (deal terms)
# ══════════════════════════════════════════════════════════════════════════

def edgar_search(query, forms, start, end, size=40):
    q = urllib.parse.urlencode({"q": query, "forms": forms, "dateRange": "custom",
                                "startdt": start, "enddt": end})
    data = http_json(f"https://efts.sec.gov/LATEST/search-index?{q}", headers=SEC_HEADERS, timeout=20)
    return data.get("hits", {}).get("hits", [])[:size]

def _edgar_press_release(cik, adsh, max_chars=4500):
    """Find the EX-99 press release inside a filing and return its text."""
    base = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{adsh.replace('-', '')}"
    idx = http_json(f"{base}/index.json", headers=SEC_HEADERS)
    names = [i["name"] for i in idx.get("directory", {}).get("item", [])]
    pick = next((n for n in names if re.search(r"ex[-_]?99|dex99", n, re.I) and n.lower().endswith((".htm", ".html", ".txt"))), None)
    if not pick:
        return ""
    time.sleep(0.2)
    return strip_html(http_text(f"{base}/{pick}", headers=SEC_HEADERS, timeout=20))[:max_chars]

def fetch_edgar_deals(days_back=1, max_docs=6):
    start = (NOW - timedelta(days=days_back)).strftime("%Y-%m-%d")
    end = NOW.strftime("%Y-%m-%d")
    deals, seen = [], set()
    try:
        hits = edgar_search('"Agreement and Plan of Merger"', "8-K", start, end)
        for h in hits:
            src = h.get("_source", {})
            adsh = src.get("adsh") or h.get("_id", "").split(":")[0]
            items = src.get("items") or []
            if not adsh or adsh in seen or "1.01" not in items:
                continue   # 1.01 = entry into a material definitive agreement (new deals, not closings)
            seen.add(adsh)
            names = src.get("display_names") or []
            cik = (src.get("ciks") or ["0"])[0]
            text = ""
            if len(deals) < max_docs:
                try:
                    text = _edgar_press_release(cik, adsh)
                except Exception as ex:
                    print(f"    [EDGAR ex99 {adsh}]: {ex}")
            deals.append({"filer": "; ".join(names), "date": src.get("file_date", ""), "adsh": adsh,
                          "url": f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{adsh.replace('-', '')}/",
                          "press_release": text})
            time.sleep(0.2)
    except Exception as ex:
        print(f"    [EDGAR 8-K]: {ex}")
    # Tender offers, going-private, and merger proxies filed in the window
    other = []
    for form in ["SC TO-T", "SC 13E3", "DEFM14A", "S-4"]:
        try:
            for h in edgar_search("merger OR tender OR acquisition", form, start, end, size=8):
                src = h.get("_source", {})
                other.append(f"{form}: {'; '.join(src.get('display_names') or [])} ({src.get('file_date','')})")
        except Exception as ex:
            print(f"    [EDGAR {form}]: {ex}")
    print(f"    [EDGAR] {len(deals)} new merger 8-Ks, {len(other)} other deal filings")
    return deals, sorted(set(other))

def fetch_edgar_earnings(tickers, day, max_docs=10):
    """Earnings press releases (8-K Item 2.02) filed today for the given tickers: the company's own explanation."""
    out = []
    if not tickers:
        return out
    try:
        hits = edgar_search('"Results of Operations and Financial Condition"', "8-K", day, day, size=100)
        for h in hits:
            src = h.get("_source", {})
            if "2.02" not in (src.get("items") or []):
                continue
            names = " ".join(src.get("display_names") or [])
            tk = next((t for t in tickers if f"({t})" in names or f"({t}," in names or f", {t})" in names), None)
            if not tk or any(o["ticker"] == tk for o in out):
                continue
            adsh = src.get("adsh") or h.get("_id", "").split(":")[0]
            cik = (src.get("ciks") or ["0"])[0]
            try:
                text = _edgar_press_release(cik, adsh, max_chars=5000)
            except Exception as ex:
                print(f"    [EDGAR earnings {tk}]: {ex}")
                text = ""
            if text:
                out.append({"ticker": tk, "text": text})
            if len(out) >= max_docs:
                break
            time.sleep(0.2)
    except Exception as ex:
        print(f"    [EDGAR earnings]: {ex}")
    print(f"    [EDGAR earnings] {len(out)} press releases")
    return out

def fmt_edgar_earnings(items):
    return "\n\n".join(f"• {i['ticker']} EARNINGS PRESS RELEASE (SEC 8-K): {i['text']}" for i in items) or "None found."

def fmt_edgar(deals, other):
    parts = []
    for d in deals:
        pr = f"\n  PRESS RELEASE TEXT: {d['press_release']}" if d["press_release"] else ""
        parts.append(f"• {d['filer']} | filed {d['date']} | {d['url']}{pr}")
    if other:
        parts.append("OTHER DEAL FILINGS:\n" + "\n".join(f"• {o}" for o in other[:15]))
    return "\n\n".join(parts) or "No new SEC merger filings found."


# ══════════════════════════════════════════════════════════════════════════
#  POLICY + POLITICS: Congress.gov, Federal Register, GDELT
# ══════════════════════════════════════════════════════════════════════════

MAJOR_ACTION = re.compile(r"passed|agreed to|became public law|signed by president|presented to president|"
                          r"cloture|reported by|ordered to be reported|vetoed|conference", re.I)

def fetch_congress(days_back=1):
    if not DATA_GOV_KEY:
        return []
    try:
        since = (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime("%Y-%m-%dT%H:%M:%SZ")
        q = urllib.parse.urlencode({"fromDateTime": since, "sort": "updateDate desc", "limit": 250,
                                    "format": "json", "api_key": DATA_GOV_KEY})
        bills = http_json(f"https://api.congress.gov/v3/bill?{q}", timeout=25).get("bills", [])
        out = []
        for b in bills:
            act = (b.get("latestAction") or {}).get("text", "")
            if MAJOR_ACTION.search(act):
                out.append(f"• {b.get('type','')}{b.get('number','')}: {b.get('title','')[:160]} | {act[:200]}")
        print(f"    [Congress] {len(out)} major actions of {len(bills)} updated bills")
        return out[:25]
    except Exception as ex:
        print(f"    [Congress]: {ex}")
        return []

def fetch_federal_register(days_back=1):
    try:
        since = (NOW - timedelta(days=days_back)).strftime("%Y-%m-%d")
        params = [("conditions[publication_date][gte]", since), ("conditions[type][]", "RULE"),
                  ("conditions[type][]", "PRESDOCU"), ("per_page", "40"), ("order", "newest")]
        for f in ["title", "type", "agency_names", "abstract", "significant", "subtype"]:
            params.append(("fields[]", f))
        docs = http_json("https://www.federalregister.gov/api/v1/documents.json?" + urllib.parse.urlencode(params),
                         timeout=20).get("results", [])
        out = []
        for d in docs:
            if d.get("type") == "Presidential Document" or d.get("significant"):
                ag = ", ".join(d.get("agency_names") or [])
                out.append(f"• [{d.get('subtype') or d.get('type')}] {d.get('title','')} ({ag}) {(d.get('abstract') or '')[:200]}")
        print(f"    [Federal Register] {len(out)} significant of {len(docs)}")
        return out[:15]
    except Exception as ex:
        print(f"    [Federal Register]: {ex}")
        return []

def fetch_gdelt(query, timespan="24h", n=25):
    try:
        q = urllib.parse.urlencode({"query": f"{query} sourcelang:english", "mode": "ArtList",
                                    "maxrecords": n, "format": "json", "timespan": timespan, "sort": "HybridRel"})
        arts = http_json(f"https://api.gdeltproject.org/api/v2/doc/doc?{q}", timeout=25).get("articles", [])
        out = [{"title": a.get("title", ""), "description": "", "source": a.get("domain", "GDELT"),
                "published": a.get("seendate", "")} for a in arts if a.get("title")]
        print(f"    [GDELT] {len(out)}")
        return out
    except Exception as ex:
        print(f"    [GDELT]: {ex}")
        return []


# ══════════════════════════════════════════════════════════════════════════
#  TECH: Hacker News front page
# ══════════════════════════════════════════════════════════════════════════

def fetch_hn(hours=24, n=30):
    try:
        since = int((datetime.now(timezone.utc) - timedelta(hours=hours)).timestamp())
        q = urllib.parse.urlencode({"tags": "story", "numericFilters": f"created_at_i>{since},points>150",
                                    "hitsPerPage": n})
        hits = http_json(f"https://hn.algolia.com/api/v1/search?{q}").get("hits", [])
        out = [{"title": h.get("title", ""), "description": f"{h.get('points',0)} points, {h.get('num_comments',0)} comments",
                "source": "Hacker News", "published": h.get("created_at", "")[:16]} for h in hits if h.get("title")]
        print(f"    [HN] {len(out)}")
        return out
    except Exception as ex:
        print(f"    [HN]: {ex}")
        return []


# ══════════════════════════════════════════════════════════════════════════
#  RSS FEEDS
# ══════════════════════════════════════════════════════════════════════════

RSS_FEEDS = {
    "marketwatch_top": "https://feeds.marketwatch.com/marketwatch/topstories/",
    "marketwatch_mk":  "https://feeds.marketwatch.com/marketwatch/marketpulse/",
    "ft_home":         "https://www.ft.com/rss/home",
    "cnbc_markets":    "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20910258",
    "cnbc_finance":    "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10000664",
    "bbc_world":       "https://feeds.bbci.co.uk/news/world/rss.xml",
    "bbc_business":    "https://feeds.bbci.co.uk/news/business/rss.xml",
    "bbc_tech":        "https://feeds.bbci.co.uk/news/technology/rss.xml",
    "guardian_world":  "https://www.theguardian.com/world/rss",
    "guardian_tech":   "https://www.theguardian.com/technology/rss",
    "npr_politics":    "https://feeds.npr.org/1014/rss.xml",
    "npr_business":    "https://feeds.npr.org/1006/rss.xml",
    "politico":        "https://www.politico.com/rss/politicopicks.xml",
    "techmeme":        "https://www.techmeme.com/feed.xml",
    "venturebeat":     "https://venturebeat.com/feed/",
    "wired_ai":        "https://www.wired.com/feed/tag/artificial-intelligence/rss",
    "axios":           "https://www.axios.com/feeds/feed.rss",
    "techcrunch_ma":   "https://techcrunch.com/category/mergers-acquisitions/feed/",
    "techcrunch_fund": "https://techcrunch.com/category/fundings-exits/feed/",
    "crunchbase_news": "https://news.crunchbase.com/feed/",
    "prn_ma":          "https://www.prnewswire.com/rss/financial-services-latest-news/acquisitions-mergers-and-takeovers-list.rss",
    "fed_press":       "https://www.federalreserve.gov/feeds/press_all.xml",
    "fed_speeches":    "https://www.federalreserve.gov/feeds/speeches.xml",
    "ftc_competition": "https://www.ftc.gov/feeds/press-release-competition.xml",
    "doj_news":        "https://www.justice.gov/feeds/opa/justice-news.xml",
    "gnw_ma":          "https://www.globenewswire.com/RssFeed/subjectcode/27-Mergers%20And%20Acquisitions/feedTitle/GlobeNewswire%20-%20Mergers%20And%20Acquisitions",
}

SOURCE_NAMES = {
    "marketwatch_top": "MarketWatch", "marketwatch_mk": "MarketWatch", "ft_home": "FT",
    "cnbc_markets": "CNBC", "cnbc_finance": "CNBC", "bbc_world": "BBC", "bbc_business": "BBC",
    "bbc_tech": "BBC", "guardian_world": "The Guardian", "guardian_tech": "The Guardian",
    "npr_politics": "NPR", "npr_business": "NPR", "politico": "Politico", "techmeme": "Techmeme",
    "venturebeat": "VentureBeat", "wired_ai": "Wired", "axios": "Axios", "techcrunch_ma": "TechCrunch",
    "techcrunch_fund": "TechCrunch", "crunchbase_news": "Crunchbase", "prn_ma": "PR Newswire",
    "gnw_ma": "GlobeNewswire", "fed_press": "Federal Reserve", "fed_speeches": "Federal Reserve",
    "ftc_competition": "FTC", "doj_news": "DOJ",
}


# ══════════════════════════════════════════════════════════════════════════
#  GATHERERS (one per edition)
# ══════════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════════
#  NEWSLETTERS: read the "Brief Sources" Gmail label, extract high-impact items
# ══════════════════════════════════════════════════════════════════════════

NEWSLETTER_NAMES = [   # sender fragment -> display name
    ("macroglide", "MacroGlide"), ("execsum", "Exec Sum"), ("mike@axios", "Axios AM/PM"),
    ("markets@axios", "Axios Markets"), ("macro@axios", "Axios Macro"), ("closer@axios", "Axios Closer"),
    ("dan@axios", "Axios Pro Rata"), ("lucinda.shen@axios", "Axios Pro Rata"), ("saltlakecity@axios", "Axios Salt Lake City"),
    ("thedailypeel", "The Daily Peel"), ("shortsqueez", "Short Squeez"), ("bloomberg", "Money Stuff (Matt Levine)"), ("ethmos", "Ethmos (AI summary)"),
    ("thewallstreetrollup", "The Wall Street Rollup"), ("wso-weekly", "WSO Weekly"), ("aswathdamodaran", "Aswath Damodaran"),
    ("paripassu", "Pari Passu"), ("illiquidinsights", "Illiquid Insights"), ("gs.com", "Goldman Sachs Briefings"),
    ("stls.frb.org", "FRED (St. Louis Fed)"), ("linkedin", "LinkedIn News"), ("axios", "Axios"),
]

def newsletter_name(sender):
    return next((name for frag, name in NEWSLETTER_NAMES if frag in sender), sender)

DEEP_SOURCES = ("ethmos", "aswathdamodaran", "paripassu", "illiquidinsights", "gs.com", "wso-weekly", "stls.frb.org")

def _decode(h):
    try:
        return str(make_header(decode_header(h or "")))
    except Exception:
        return h or ""

def _email_text(msg):
    plain, htm = "", ""
    for part in (msg.walk() if msg.is_multipart() else [msg]):
        ctype = part.get_content_type()
        if part.get("Content-Disposition", "").startswith("attachment"):
            continue
        try:
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        except Exception:
            continue
        if ctype == "text/plain" and not plain:
            plain = text
        elif ctype == "text/html" and not htm:
            htm = text
    text = plain if len(plain) > 500 else strip_html(htm) if htm else plain
    text = re.sub(r"https?://\S+", "", text)                 # links add length, not meaning
    text = re.sub(r"\[\]\(\)|\(\s*\)|\|", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    cut = re.search(r"(unsubscribe|manage your email preferences|you received this email)", text, re.I)
    if cut and cut.start() > len(text) * 0.6:                # drop the footer only if it's near the end
        text = text[:cut.start()]
    return text.strip()[:14000]

def fetch_newsletters(hours, seen_ids, max_emails=60, reread=()):
    """Reads emails under the Brief Sources label from the last `hours` hours."""
    out = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    try:
        M = imaplib.IMAP4_SSL("imap.gmail.com")
        M.login(GMAIL_USER, GMAIL_PASS)
        M.select(f'"{NEWSLETTER_LABEL}"', readonly=True)
        since = (cutoff - timedelta(days=1)).strftime("%d-%b-%Y")
        typ, data = M.search(None, f"(SINCE {since})")
        for num in (data[0].split() if data and data[0] else [])[-max_emails:]:
            typ, msg_data = M.fetch(num, "(RFC822)")
            msg = email.message_from_bytes(msg_data[0][1])
            mid = (msg.get("Message-ID") or "").strip()
            try:
                sent = email.utils.parsedate_to_datetime(msg.get("Date"))
            except Exception:
                continue
            if sent.tzinfo is None:
                sent = sent.replace(tzinfo=timezone.utc)
            if sent < cutoff or (mid in seen_ids and not any(k in (msg.get("From") or "").lower() for k in reread)):
                continue
            sender = email.utils.parseaddr(msg.get("From"))[1].lower()
            out.append({"id": mid, "sender": sender, "subject": _decode(msg.get("Subject")),
                        "sent": sent.astimezone(ET_TZ).strftime("%a %b %d %I:%M %p ET"), "text": _email_text(msg)})
        M.logout()
    except Exception as ex:
        print(f"    [Newsletters] IMAP read failed: {ex}")
    print(f"    [Newsletters] {len(out)} new emails")
    return out

NEWSLETTER_EXTRACT_PROMPT = """You extract the most important content from one financial newsletter for a personal brief. Return JSON only, no other text.

If it is a NEWS newsletter (market recap, deals roundup, daily briefing), return:
{"type": "news", "items": [{"headline": "short", "detail": "1-3 sentences with the key numbers and the stated reason or cause", "section": "markets|macro|investing|high_finance|political|tech_ai|earnings", "impact": 1-5, "original_source": "the outlet the newsletter credits for this item (e.g. Reuters, Bloomberg, WSJ, CNBC, a company press release), or empty string if none is named"}]}
Include only items with real market, economic, deal, or policy significance (impact 3+), up to 10. Impact guide: 5 = moves major indexes or a $10B+ deal or a Fed/policy shift; 4 = large-cap stock moving 5%+, $1B+ deal, major data surprise; 3 = notable but narrower. Skip ads, sponsored content, memes, job postings, and trivia.

If it is an ANALYSIS piece (an essay or deep dive making an argument), return:
{"type": "analysis", "title": "piece title", "author": "author or publication", "thesis": "the core argument in 2-3 sentences", "evidence": ["key data points or reasoning, up to 5"], "implications": "what it means if right, 1-2 sentences"}

Keep exact numbers as written. Never add facts that are not in the newsletter."""

def _call_newsletter_model(text):
    for model in NEWSLETTER_MODELS:
        payload = {"model": model, "max_tokens": 2000, "system": NEWSLETTER_EXTRACT_PROMPT,
                   "messages": [{"role": "user", "content": text}]}
        for attempt, delay in enumerate([10, 30, 0]):
            try:
                data = _post_claude(payload)
                return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
            except urllib.error.HTTPError as ex:
                if ex.code in (400, 404):      # model unavailable, try the next one
                    break
                if ex.code in (429, 500, 502, 503, 529) and delay:
                    time.sleep(delay)
                    continue
                raise
    raise RuntimeError("no newsletter model available")

def extract_newsletter(nl):
    header = f"From: {nl['sender']}\nSubject: {nl['subject']}\nSent: {nl['sent']}\n\n"
    try:
        result = parse_json(_call_newsletter_model(header + nl["text"]))
    except Exception as ex:
        print(f"    [Newsletters] extraction failed for {nl['sender']} ({ex}); using trimmed text")
        result = {"type": "raw", "text": nl["text"][:2500]}
    result.update({"sender": nl["sender"], "subject": nl["subject"], "sent": nl["sent"]})
    return result

def gather_newsletters(edition, hours, state):
    seen = set(state.get("newsletter_seen", []))
    emails = fetch_newsletters(hours, seen, max_emails=200 if edition == "weekly" else 60,
                              reread=("news.bloomberg.com",) if edition == "weekly" else ())   # weekly re-reads Money Stuff for its analysis
    is_deep = lambda e: any(k in e["sender"] for k in DEEP_SOURCES)
    if edition == "weekly":   # weekly: deep-analysis pieces plus weekly wrap-ups
        emails = [e for e in emails if is_deep(e) or "weekly" in e["subject"].lower() or "news.bloomberg.com" in e["sender"]]
    else:                     # daily: news only; essays wait for the weekly
        emails = [e for e in emails if not is_deep(e)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        extracted = list(pool.map(extract_newsletter, emails))
    return extracted, [e["id"] for e in emails if e["id"]]

def fmt_newsletters(items):
    if not items:
        return "None."
    lines = []
    for nl in items:
        src = f"[Newsletter: {newsletter_name(nl['sender'])} · {nl['sent']}]"
        if nl.get("type") == "analysis":
            lines.append(f"{src} ANALYSIS: {nl.get('title', nl['subject'])} ({nl.get('author', '')})\n  Thesis: {nl.get('thesis', '')}\n"
                         + "".join(f"  - {ev}\n" for ev in nl.get("evidence", [])) + f"  Implications: {nl.get('implications', '')}")
        elif nl.get("type") == "news":
            for it in sorted(nl.get("items", []), key=lambda x: -int(x.get("impact", 0) or 0)):
                lines.append(f"{src} ({it.get('section', '?')}, impact {it.get('impact', '?')}) {it.get('headline', '')}: {it.get('detail', '')}" + (f" (originally: {it['original_source']})" if it.get("original_source") else ""))
        else:
            lines.append(f"{src} {nl['subject']}\n{nl.get('text', '')}")
    return "\n".join(lines)


def fetch_x_feeds_fast():
    """Stops early if the Nitter mirrors are all down, so a dead feed can't stall the run."""
    posts, misses = [], 0
    for handle, name in X_ACCOUNTS.items():
        got = fetch_nitter_rss(handle, name, max_items=4)
        posts.extend(got)
        misses = 0 if got else misses + 1
        if misses >= 3 and not posts:
            print("    [X] mirrors unavailable, skipping remaining accounts")
            break
    return posts

def _news_block(hours):
    days = max(1, round(hours / 24))
    return {
        "markets": finnhub_news("general") + fetch_rss_multi(["marketwatch_top", "marketwatch_mk", "cnbc_markets", "ft_home", "bbc_business"], 5, hours)
                   + gnews_top("business", 8) + newsapi_headlines("business", 8),
        "deals":   fetch_rss_multi(["prn_ma", "gnw_ma", "techcrunch_ma", "techcrunch_fund", "crunchbase_news", "axios"], 6, hours)
                   + finnhub_news("merger") + gnews_search("acquire OR merger OR takeover OR buyout billion", 10)
                   + newsapi_search("merger acquisition takeover buyout agreed billion deal", 8, days),
        "street":  gnews_search("investment banking dealmaking OR \"league table\" OR \"M&A advisory\"", 8)
                   + gnews_search("Goldman Sachs OR \"Morgan Stanley\" OR JPMorgan OR Evercore OR Lazard OR Centerview bankers", 8)
                   + fetch_rss_multi(["cnbc_finance"], 6, hours),
        "tech":    fetch_rss_multi(["techmeme", "venturebeat", "wired_ai", "bbc_tech", "guardian_tech"], 6, hours)
                   + fetch_hn(hours) + gnews_search("OpenAI OR Anthropic OR Gemini OR xAI OR Nvidia OR \"data center\"", 10)
                   + newsapi_search("OpenAI Anthropic Google Gemini xAI Grok Nvidia Microsoft Meta AI", 8, days),
        "fed":     fetch_rss_multi(["fed_press", "fed_speeches"], 8, max(hours, 24)),
        "antitrust": fetch_rss_multi(["ftc_competition"], 8, max(hours, 48))
                   + [a for a in fetch_rss("doj_news", 40, max(hours, 48)) if re.search(r"antitrust|merger|acquisition|monopol", a["title"] + " " + a["description"], re.I)],
        "policy":  fetch_rss_multi(["politico", "npr_politics", "bbc_world", "guardian_world"], 6, hours)
                   + gnews_top("world", 8) + gnews_top("nation", 8)
                   + fetch_gdelt("(missile OR airstrike OR sanctions OR ceasefire OR invasion OR tariffs)", f"{hours}h" if hours <= 72 else "7d"),
    }

def gather(edition, state):
    d = {"date": TODAY, "edition": edition}
    if edition == "morning":
        print("  → Futures + prior close...")
        d["futures"] = fetch_quotes(FUTURES_TICKERS, "live")
        d["quotes"]  = fetch_quotes(INDEX_TICKERS, "session")
        print("  → Today's earnings...")
        d["earnings"] = fetch_earnings(DATE_KEY, DATE_KEY)
        print("  → Pre-market movers + insider buys...")
        d["premarket_movers"] = fetch_premarket_movers()
        d["insider_buys"] = fetch_insider_buys(days=4 if NOW.weekday() == 0 else 2)
        d["close_headlines"] = state.get("close_headlines", {}).get("items", [])
        edgar_days, news_hours = (3 if NOW.weekday() == 0 else 1), (72 if NOW.weekday() == 0 else 24)
        congress_days = 3 if NOW.weekday() == 0 else 1
    elif edition == "close":
        print("  → Session close + movers...")
        d["quotes"] = fetch_quotes(INDEX_TICKERS, "session")
        d["movers"] = fetch_movers("session")
        d["screens"] = fetch_market_screens()
        print("  → Today's earnings...")
        d["earnings"] = fetch_earnings(DATE_KEY, DATE_KEY)
        d["earnings_releases"] = fetch_edgar_earnings([i["ticker"] for i in d["earnings"]], DATE_KEY)
        d["morning_headlines"] = state.get("morning_headlines", {}).get(DATE_KEY, [])
        edgar_days, news_hours, congress_days = 0, 12, 1
    else:  # weekly
        print("  → Weekly performance...")
        d["quotes"] = fetch_quotes(INDEX_TICKERS, "week")
        d["movers"] = fetch_movers("week")
        mon = (NOW + timedelta(days=(7 - NOW.weekday()))).strftime("%Y-%m-%d")
        fri = (NOW + timedelta(days=(11 - NOW.weekday()))).strftime("%Y-%m-%d")
        d["earnings"] = fetch_earnings(mon, fri)
        d["cot"] = fetch_cot()
        edgar_days, news_hours, congress_days = 6, 150, 6
    print("  → Macro (BLS, BEA, EIA, Treasury, FRED)...")
    d["macro"], d["new_releases"] = gather_macro(state)
    d["nyfed"] = fetch_nyfed_rates()
    d["activist"] = fetch_activist_13d()
    print("  → SEC EDGAR deals...")
    d["edgar_deals"], d["edgar_other"] = fetch_edgar_deals(days_back=edgar_days, max_docs=8 if edition == "weekly" else 6)
    print("  → Congress + Federal Register...")
    d["congress"] = fetch_congress(congress_days)
    d["fedreg"]   = fetch_federal_register(congress_days)
    print("  → News feeds...")
    d.update(_news_block(news_hours))
    print("  → Newsletters (Brief Sources label)...")
    d["newsletters"], d["newsletter_ids"] = gather_newsletters(edition, {"morning": 72 if NOW.weekday() == 0 else 24, "close": 14, "weekly": 170}[edition], state)
    return d


# ══════════════════════════════════════════════════════════════════════════
#  CLAUDE (with live web search)
# ══════════════════════════════════════════════════════════════════════════

def _post_claude(payload):
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=json.dumps(payload).encode(),
        headers={"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.loads(r.read().decode())

def call_claude(system_prompt, user_prompt, max_tokens=16000, search_uses=0, max_retries=3):
    messages = [{"role": "user", "content": user_prompt}]
    payload = {"model": MODEL, "max_tokens": max_tokens, "system": system_prompt, "messages": messages}
    if search_uses:
        payload["tools"] = [{"type": "web_search_20250305", "name": "web_search", "max_uses": search_uses}]
    delays = [15, 45, 90]
    content = []
    for turn in range(4):   # web search can pause long turns; resume up to 3 times
        for attempt in range(max_retries + 1):
            try:
                data = _post_claude(payload)
                break
            except urllib.error.HTTPError as ex:
                body = ex.read().decode()
                print(f"  Anthropic error {ex.code}: {body[:500]}")
                if ex.code == 400 and "tools" in payload and "web_search" in body:
                    print("  Web search unavailable on this account, continuing without it.")
                    payload.pop("tools")
                    continue
                if ex.code in {429, 500, 502, 503, 529} and attempt < max_retries:
                    time.sleep(delays[attempt])
                    continue
                raise
        content += data.get("content", [])
        if data.get("stop_reason") == "pause_turn":
            messages.append({"role": "assistant", "content": data["content"]})
            continue
        if data.get("stop_reason") == "max_tokens":
            print("  ⚠️ Hit max_tokens; output may be truncated.")
        break
    # The JSON answer is the text written after the final search result
    last_tool = max((i for i, b in enumerate(content) if b.get("type") == "web_search_tool_result"), default=-1)
    text = "".join(b.get("text", "") for b in content[last_tool + 1:] if b.get("type") == "text")
    searches = sum(1 for b in content if b.get("type") == "server_tool_use")
    print(f"  Claude used {searches} web searches")
    return text

def parse_json(raw):
    s = raw.replace("```json", "").replace("```", "").strip()
    start, end = s.find("{"), s.rfind("}")
    return json.loads(s[start:end + 1])


SYSTEM_PROMPT = """You write "The Compiled Brief," a personal digest for Konner Greer, a Finance & Fintech student at the University of Utah. It compiles his newsletter subscriptions and news feeds into one email so he never has to read them separately.

PURPOSE: keep him informed and curious about markets, the economy, investing, high finance (M&A, PE, banking), politics, and tech/AI. This is not recruiting prep. Daily editions inform; the weekly edition helps him understand themes and form his own views.

READER LEVEL: intro finance. He knows basics like what P/E and EBITDA stand for, but not market mechanics or deal jargon. Every time a technical term appears, explain it briefly in plain English in the same sentence, every time, not just the first time. Calibration examples:
- "S&P futures are up 0.31% (futures trade before the open, so this points to stocks opening about 0.3% higher)."
- "The price is about 9.3x EBITDA, meaning the buyer pays roughly $9.30 for every $1 of the company's annual operating profit."
- "Yields rose 10 basis points (0.10 percentage points)."
Explanations are one clause, never a paragraph. Niche jargon (DIP financing, term premium, squeeze-out) is simply translated into plain words without teaching it.

SELECTION (impact thresholds): include an item only if it clears at least one bar:
- Markets: a major index moves 1%+, a $100B+ company moves 4%+, any $10B+ company moves 8%+, or a big move in yields (10+ bps), oil (3%+), gold (2%+), or the dollar.
- Macro: Fed decisions, minutes, and Chair or key-official remarks; major data (jobs, CPI, PCE, GDP, retail sales, sentiment) especially when it surprises; meaningful changes in rate expectations.
- Investing: notable shifts in valuations, flows, positioning, or a well-argued call from a credible source.
- High finance: M&A, buyouts, IPOs, or financings of $1B+; major PE/bank news; notable restructurings.
- Political: policy, sanctions, trade, elections, or geopolitical events that move markets or the economy.
- Tech/AI: frontier AI labs, big tech, chips, data centers, and major funding ($500M+).
A high-impact story reported by only one newsletter still belongs. If a section has nothing that clears the bar, return an empty list. Never pad.

SOURCES: every item gets a "sources" field naming where it came from: the newsletter name(s) (e.g. "Exec Sum, Axios Pro Rata") and the original outlet when known (e.g. "originally Reuters"), or the feed outlet shown in brackets (CNBC, Bloomberg, FT...), "SEC filing" for EDGAR, "company press release", or "web search: outlet". Format: "Exec Sum, Axios Pro Rata · originally Reuters". Never invent a source.

ACCURACY:
- Numbers in the MARKET DATA blocks are authoritative; if a newsletter disagrees, use MARKET DATA.
- Never invent numbers, quotes, or dates. If unknown, leave it out.
- Each story appears in exactly one section. Merge duplicates across sources into one item and list all sources.
- When quoting a company's explanation, attribute it ("Delta says...") and note when the stock reaction or analysts suggest otherwise.

STYLE: direct and readable. Never use em dashes; use commas, colons, or separate sentences.

OUTPUT: valid JSON only. No markdown, no code fences, no text before or after the JSON object."""

ITEM_SCHEMA = """{"headline": "short, specific headline", "summary": "1-3 sentences: what happened with key numbers, technical terms explained inline", "why": "1 sentence: why it matters, or empty string", "sources": "newsletter(s) · originally outlet"}"""

SECTIONS_SCHEMA = f"""{{
    "markets": [{ITEM_SCHEMA}],
    "macro": [{ITEM_SCHEMA}],
    "investing": [{ITEM_SCHEMA}],
    "high_finance": [{ITEM_SCHEMA}],
    "political": [{ITEM_SCHEMA}],
    "tech_ai": [{ITEM_SCHEMA}]
  }}"""

def _common_blocks(d):
    return f"""=== NEWSLETTER HIGHLIGHTS (pre-extracted from Konner's subscriptions; sorted by impact) ===
Treat these as high-quality, curated sources. Include an item if its impact is high even when only one newsletter reported it. Merge duplicates across newsletters and with the feeds below so each story appears once. If a newsletter's number conflicts with the MARKET DATA blocks, use MARKET DATA.
{fmt_newsletters(d.get('newsletters'))}

=== MACRO DATA (🆕 = released since the last edition) ===
{d['macro']}

=== NY FED FUNDING RATES ===
{chr(10).join(d['nyfed']) or 'Unavailable.'}

=== FEDERAL RESERVE: PRESS RELEASES + SPEECHES ===
{fmt_articles(d['fed'], 12)}

=== SEC: NEW ACTIVIST / CONTROL STAKES (Schedule 13D, subject company shown) ===
{chr(10).join(d['activist']) or 'None.'}

=== SEC EDGAR: NEW MERGER AGREEMENTS (with press-release text) ===
{fmt_edgar(d['edgar_deals'], d['edgar_other'])}

=== DEAL NEWS ===
{fmt_articles(d['deals'], 30)}

=== WALL STREET / INDUSTRY NEWS ===
{fmt_articles(d['street'], 16)}

=== MARKET NEWS ===
{fmt_articles(d['markets'], 25)}

=== TECH & AI NEWS (incl. Hacker News) ===
{fmt_articles(d['tech'], 30)}

=== ANTITRUST (FTC competition + DOJ) ===
{fmt_articles(d['antitrust'], 10)}

=== CONGRESS: MAJOR BILL ACTIONS ===
{chr(10).join(d['congress']) or 'None.'}

=== FEDERAL REGISTER: EXECUTIVE ACTIONS + SIGNIFICANT RULES ===
{chr(10).join(d['fedreg']) or 'None.'}

=== POLITICS / WORLD / GEOPOLITICS NEWS ===
{fmt_articles(d['policy'], 30)}"""

def build_prompt(d):
    ed = d["edition"]
    if ed == "morning":
        return f"""Today is {d['date']}. Write the MORNING EDITION of The Compiled Brief, read around 5:30am MT before the 9:30 ET open.

FOCUS: overnight and today. Do NOT recap yesterday's session; last night's close edition already covered it. Exceptions: news that broke after last night's edition (after-hours earnings, late-evening developments) belongs here as overnight news.
Last night's close edition covered these headlines (skip unless there is a material update, then say what changed):
{chr(10).join('• ' + h for h in d.get('close_headlines', [])) or 'None recorded.'}

LENGTH: about 600-800 words total (a 5-minute read). Fewer items on quiet days.

=== FUTURES (latest; "as of" shows the date of the last trade) ===
{fmt_quotes(d['futures'])}

=== PRIOR SESSION CLOSE ===
{fmt_quotes(d['quotes'])}

=== LARGE-CAP EARNINGS TODAY (from the earnings calendar; filtered to major companies) ===
{fmt_earnings(d['earnings'])}

=== PRE-MARKET MOVERS (large-cap watchlist) ===
{fmt_movers(d['premarket_movers'])}

=== INSIDER OPEN-MARKET PURCHASES ===
{chr(10).join(d['insider_buys']) or 'None.'}

{_common_blocks(d)}

Return JSON with exactly these keys:
{{
  "yesterday": "1-2 sentences of context on yesterday's session (e.g. 'Stocks closed higher on Microsoft's beat; the 10Y yield hit 5.24%'). Continuity only, no detail.",
  "snapshot_note": "1 sentence in plain English on what futures and overnight markets suggest for today's open",
  "sections": {SECTIONS_SCHEMA},
  "earnings_today": [{{"ticker": "TICKER", "company": "Name", "timing": "Before open / After close", "expectations": "what analysts expect and the one thing to watch, terms explained", "sources": "earnings calendar · newsletter if previewed"}}],
  "today_calendar": ["each important scheduled event today with time in MT and one clause on why it matters (data releases, Fed speakers, auctions); empty list if none"]
}}
earnings_today: include every company in the earnings list above (major companies only), plus any major company a newsletter previews for today. Empty list if none."""
    if ed == "close":
        return f"""Today is {d['date']}. Write the CLOSE EDITION of The Compiled Brief, read around 4:00pm MT, two hours after the market close.

FOCUS: what happened today. This morning's edition already covered these headlines; do NOT repeat them unless there is a material update (then say what changed):
{chr(10).join('• ' + h for h in d['morning_headlines']) or 'None recorded.'}

LENGTH: about 400-700 words (a 2-4 minute read); up to 1,000 on heavy days (big earnings, Fed decisions). Fewer items on quiet days.

=== TODAY'S CLOSE (authoritative) ===
{fmt_quotes(d['quotes'])}

=== MARKET-WIDE MOVERS TODAY (companies over $2B) ===
{fmt_screens(d['screens'])}

=== LARGE-CAP WATCHLIST MOVERS TODAY ===
{fmt_movers(d['movers'])}

=== LARGE-CAP EARNINGS TODAY (estimates and actuals; actuals may be missing for after-close reporters) ===
{fmt_earnings(d['earnings'])}

=== COMPANY EARNINGS PRESS RELEASES FILED WITH THE SEC TODAY (the company's own explanation) ===
{fmt_edgar_earnings(d.get('earnings_releases', []))}

{_common_blocks(d)}

Return JSON with exactly these keys:
{{
  "opening": "1-2 sentences: how the day ended and the main reason why",
  "movers": {{"gainers": [{{"name": "TICKER (Company)", "change": "+x.x%", "reason": "the verified catalyst in one sentence, or 'no clear catalyst'"}}], "losers": [same shape]}},
  "sections": {SECTIONS_SCHEMA},
  "earnings_results": [{{"ticker": "TICKER", "company": "Name", "result": "beat or miss on EPS and revenue vs estimates, with numbers, plus any guidance change", "company_says": "management's explanation for the results, attributed (e.g. 'Delta says fuel costs rose 60%...'), from the press release or newsletters", "reaction": "stock move, including after hours if known", "pushback": "where analysts, newsletters, or the stock reaction suggest a different story than management's, or empty string", "sources": "SEC filing · newsletter(s)"}}]
}}
movers: up to 3 gainers and 3 losers that matter (large caps or big news), verified. earnings_results: every major company that reported today; use web search for after-close results if needed. Markets section covers the session recap (indexes, sectors, yields, oil) and why."""
    return f"""Today is {d['date']}. Write the WEEKLY EDITION of The Compiled Brief (Saturday). This is the sit-down read: 10-15 minutes, about 2,000-2,500 words. Its job is to help Konner understand the week's underlying themes and form his own views, not to list news.

The deep-analysis pieces in the NEWSLETTER HIGHLIGHTS (marked ANALYSIS: Damodaran, Matt Levine's Money Stuff, Goldman Sachs Briefings, Illiquid Insights, Pari Passu, WSO, and others) are the core of this edition. Build the themes around them, connect them to the week's news, and note where writers agree or disagree. Ethmos items are AI-generated summaries of podcasts and research: use them for ideas and leads, but do not rely on a figure or quote from Ethmos unless another source confirms it, and label it "per Ethmos" when used.

=== WEEKLY PERFORMANCE (authoritative, 5-session change) ===
{fmt_quotes(d['quotes'])}

=== BIGGEST LARGE-CAP MOVERS THIS WEEK ===
{fmt_movers(d['movers'])}

=== LARGE-CAP EARNINGS NEXT WEEK ===
{fmt_earnings(d['earnings'])}

=== CFTC POSITIONING (speculators, weekly) ===
{chr(10).join(d['cot']) or 'Unavailable.'}

{_common_blocks(d)}

Return JSON with exactly these keys:
{{
  "opening": "2-3 sentences: the story of the week",
  "week_in_markets": ["4-6 bullets: what drove stocks, rates, oil, and the dollar this week, terms explained"],
  "themes": [{{"title": "theme title", "what_writers_argue": "3-5 sentences: the theses and evidence from the analysis pieces and news, naming who argues what", "what_it_could_mean": "2-4 sentences: second-order effects on rates, sectors, deals, and policy", "counterpoint": "2-3 sentences: the strongest case against", "what_to_watch": "1-2 sentences: specific signals or data that would confirm or break it", "concepts": "plain-English explanation of the 1-2 concepts central to this theme, or empty string", "sources": "writers and newsletters drawn on"}}],
  "reading_list": [{{"title": "piece title", "author": "author or newsletter", "why_read": "1 sentence: why it is worth reading in full"}}],
  "top_stories": [{ITEM_SCHEMA}],
  "week_ahead": [{{"item": "short title", "why": "1 sentence"}}]
}}
themes: 2-4, built from the strongest analysis pieces. reading_list: only analysis pieces from the newsletters, 0-4. top_stories: the 4-6 most important news items of the week not already covered in a theme. week_ahead: 3-6 important events next week, including major earnings from the list."""

def generate(d):
    raw = call_claude(SYSTEM_PROMPT, build_prompt(d), search_uses=WEB_SEARCH_USES[d["edition"]])
    try:
        return parse_json(raw)
    except Exception as ex:
        print(f"  JSON parse failed ({ex}); retrying once without web search...")
        raw = call_claude(SYSTEM_PROMPT, build_prompt(d) + "\n\nReturn ONLY the JSON object.", search_uses=0)
        return parse_json(raw)

# ══ EMAIL TEMPLATE ══

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
#  RENDERING
# ══════════════════════════════════════════════════════════════════════════

def tiles(quotes, order):
    out = []
    for sym in order:
        q = quotes.get(sym)
        if not q:
            continue
        if sym == "CURVE":
            val, chg, color = f"{q['price']:+.2f}", ("Inverted" if q.get("inverted") else "Normal"), "#8a9bb0"
        else:
            is_rate = sym in ("^TNX", "^IRX", "^TYX")
            val = f"{q['price']:.2f}%" if is_rate else (f"${q['price']:,.2f}" if sym in ("CL=F", "BZ=F", "GC=F") else f"{q['price']:,.2f}")
            chg = f"{q['change_pct']:+.2f}%"
            color = "#1e4d2b" if q["change_pct"] > 0 else ("#8b1a1a" if q["change_pct"] < 0 else "#8a9bb0")
        out.append(f'<div class="tile"><div class="tile-lbl">{e(q["name"])}</div><div class="tile-val">{val}</div>'
                   f'<div class="tile-chg" style="color:{color}">{chg}</div></div>')
    return f'<div class="tile-grid">{"".join(out)}</div>' if out else ""

def section(label, color, title, body, last=False):
    if not body:
        return ""
    style = ' style="border-bottom:none"' if last else ""
    return f'<div class="sec"{style}><div class="lbl {color}">{e(label)}</div><h2>{e(title)}</h2>{body}</div>'

def pe_note(text):
    return f'<div class="dash-note"><strong>In plain English:</strong> {e(text)}</div>' if text else ""

def li(label, text, sub=False):
    if not text:
        return ""
    cls = ' class="sub"' if sub else ""
    lab = f"<strong>{e(label)}:</strong> " if label else ""
    return f"<li{cls}>{lab}{e(text)}</li>"

def src_line(text):
    return f'<div style="font-size:11px;color:#8a8a8a;margin-top:6px;font-style:italic">Source: {e(text)}</div>' if text else ""

def item_cards(items):
    html = ""
    for it in items or []:
        body = f'<p style="font-size:14px;line-height:1.65;color:#2a2a2a">{e(it.get("summary",""))}</p>'
        if it.get("why"):
            body += f'<p style="font-size:13px;line-height:1.6;color:#555;margin-top:4px"><strong>Why it matters:</strong> {e(it["why"])}</p>'
        html += f'<div class="bcard"><div class="bcard-hed">{e(it.get("headline",""))}</div>{body}{src_line(it.get("sources"))}</div>'
    return html

SECTION_ORDER = [("markets", "Markets", "navy"), ("macro", "Macro & Economy", "green"), ("investing", "Investing", "teal"),
                 ("high_finance", "High Finance", "copper"), ("political", "Political & Geopolitics", "red"), ("tech_ai", "Tech & AI", "purple")]

def six_sections(secs, extra_markets=""):
    html = ""
    for key, label, color in SECTION_ORDER:
        body = (extra_markets if key == "markets" else "") + item_cards((secs or {}).get(key))
        html += section(label, color, label, body)
    return html

def earnings_cards(items, results=False):
    html = ""
    for it in items or []:
        if results:
            body = (li("Result", it.get("result")) + li("Company says", it.get("company_says"), True)
                    + li("Reaction", it.get("reaction"), True) + li("Pushback", it.get("pushback"), True))
        else:
            body = li("Timing", it.get("timing")) + li("What to expect", it.get("expectations"), True)
        html += (f'<div class="bcard"><div class="bcard-hed">{e(it.get("company",""))} ({e(it.get("ticker",""))})</div>'
                 f'<ul class="blist">{body}</ul>{src_line(it.get("sources"))}</div>')
    return html

def theme_cards(items):
    html = ""
    for t in items or []:
        body = (li("What the writers argue", t.get("what_writers_argue")) + li("What it could mean", t.get("what_it_could_mean"))
                + li("Counterpoint", t.get("counterpoint")) + li("What to watch", t.get("what_to_watch"))
                + li("Concepts", t.get("concepts"), True))
        html += f'<div class="bcard"><div class="bcard-hed">{e(t.get("title",""))}</div><ul class="blist">{body}</ul>{src_line(t.get("sources"))}</div>'
    return html

def reading_list(items):
    rows = "".join(f'<li><strong>{e(r.get("title",""))}</strong> ({e(r.get("author",""))}): {e(r.get("why_read",""))}</li>' for r in items or [])
    return f'<ul class="blist">{rows}</ul>' if rows else ""

def stale_note(quotes, label):
    dates = {q.get("asof") for q in (quotes or {}).values() if q.get("asof")}
    today = NOW.strftime("%a %b %d")
    if dates and today not in dates:
        return f'<div class="dash-note"><strong>Note:</strong> {e(label)} as of {e(max(dates))}; markets have not traded since.</div>'
    return ""

def mover_list(items):
    rows = "".join(f'<li><strong>{e(m.get("name",""))} {e(m.get("change",""))}:</strong> {e(m.get("reason",""))}</li>' for m in items or [])
    return f'<ul class="blist">{rows}</ul>' if rows else ""

def macro_cards(items):
    html = ""
    for m in items or []:
        html += (f'<div class="bcard"><div class="bcard-hed">{e(m.get("release",""))}</div><ul class="blist">'
                 f'{li("Result", m.get("result"))}{li("Takeaway", m.get("takeaway"), True)}</ul></div>')
    return html

def watch_stocks(items):
    html = ""
    for it in items or []:
        body = li("Catalyst", it.get("catalyst")) + li("Why", it.get("why"), True) + li("Watch", it.get("watch"), True)
        html += f'<div class="bcard"><div class="bcard-hed">{e(it.get("company",""))} ({e(it.get("ticker",""))})</div><ul class="blist">{body}</ul></div>'
    return html

def top_movers(tm):
    if not tm:
        return ""
    html = ""
    for key, label in [("gainers", "Top Gainers"), ("losers", "Top Losers"), ("most_active", "Most Active")]:
        rows = tm.get(key) or []
        if rows:
            html += f'<div class="bcard-meta" style="margin-top:10px">{label}</div>' + mover_list(rows)
    return html

def watch_list(items):
    html = ""
    for i, w in enumerate(items or [], 1):
        html += (f'<div class="watch"><div class="watch-num">{i}</div><div class="watch-content">'
                 f'<div class="watch-item">{e(w.get("item",""))}</div><div class="watch-why">{e(w.get("why",""))}</div></div></div>')
    return html

def x_section(tx):
    if not tx or not tx.get("has_content") or not tx.get("signals"):
        return '<p class="story-body">X feed unavailable or quiet today.</p>'
    html = ""
    for s in tx["signals"]:
        img = (s.get("image_url") or "").strip()
        img_html = f'<img src="{e(img)}" alt="Chart from post" style="max-width:100%;border-radius:8px;margin-top:8px;border:1px solid #e5e5e5" />' if img.startswith("https://") else ""
        html += (f'<div class="x-signal"><div class="x-handle">{e(s.get("account",""))}</div>'
                 f'<div class="x-text">{e(s.get("signal",""))}</div>{img_html}<div class="x-why">{e(s.get("why_it_matters",""))}</div></div>')
    if tx.get("x_note"):
        html += f'<p class="story-body" style="margin-top:12px;font-style:italic;color:#666">{e(tx["x_note"])}</p>'
    return html

def page(label, title, date_line, sub, lead, body, footer):
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{e(label)}</title>{CSS}</head>
<body><div class="wrap">
<div class="hdr"><div class="hdr-label">{e(label)}</div><div class="hdr-title">{e(title)}</div>
<div class="hdr-date">{e(date_line)}</div><div class="hdr-sub">{e(sub)}</div></div>
<div class="lead">{render_bullets(lead, light=True)}</div>
{body}
<div class="footer"><p>The Daily Brief · <span>{e(footer)}</span></p></div>
</div></body></html>"""

def render(brief, d):
    ed = d["edition"]
    n_nl = len(d.get("newsletters") or [])
    footer = f"Compiled from {n_nl} newsletter{'s' if n_nl != 1 else ''} plus news feeds, market data, and SEC filings"
    if ed == "morning":
        snap = (tiles(d["futures"], ["ES=F", "NQ=F", "YM=F", "RTY=F"])
                + tiles(d["quotes"], ["^TNX", "^VIX", "BZ=F", "GC=F", "DX-Y.NYB", "BTC-USD"])
                + stale_note(d["futures"], "Futures")
                + (f'<div class="dash-note"><strong>What it means:</strong> {e(brief["snapshot_note"])}</div>' if brief.get("snapshot_note") else ""))
        cal = brief.get("today_calendar") or []
        body = (section("Snapshot", "navy", "Before the Open", snap)
                + six_sections(brief.get("sections"))
                + section("Earnings Today", "gold", "Who Reports", earnings_cards(brief.get("earnings_today")))
                + section("Today", "charcoal", "On the Calendar", render_bullets(cal), last=True))
        lead = [f"Yesterday: {brief['yesterday']}"] if brief.get("yesterday") else []
        return page("The Compiled Brief · Morning Edition", "Good Morning, Konner.", TODAY,
                    "Overnight news and what's ahead today.", lead, body, footer)
    if ed == "close":
        mv = brief.get("movers") or {}
        movers_html = "".join(f'<div class="bcard-meta" style="margin-top:10px">{lab}</div>' + mover_list(mv.get(k))
                              for k, lab in [("gainers", "Top Gainers"), ("losers", "Top Losers")] if mv.get(k))
        snap = (tiles(d["quotes"], ["^GSPC", "^IXIC", "^DJI", "^RUT", "^VIX", "^TNX", "BZ=F", "CL=F", "GC=F", "DX-Y.NYB"])
                + stale_note(d["quotes"], "Prices"))
        body = (section("Snapshot", "navy", "At the Close", snap)
                + six_sections(brief.get("sections"), extra_markets=movers_html)
                + section("Earnings", "gold", "Results and Reasons", earnings_cards(brief.get("earnings_results"), results=True), last=True))
        lead = [brief["opening"]] if brief.get("opening") else []
        return page("The Compiled Brief · Close Edition", "Good Evening, Konner.", TODAY,
                    "What happened today, compiled.", lead, body, footer)
    body = (section("The Week", "navy", "Markets in Review",
                    tiles(d["quotes"], ["^GSPC", "^IXIC", "^DJI", "^RUT", "^VIX", "^TNX", "BZ=F", "GC=F", "DX-Y.NYB", "BTC-USD"])
                    + render_bullets(brief.get("week_in_markets")))
            + section("Themes", "copper", "Understanding the Week", theme_cards(brief.get("themes")))
            + section("Reading List", "teal", "Worth Reading in Full", reading_list(brief.get("reading_list")))
            + section("Top Stories", "slate", "What Else Mattered", item_cards(brief.get("top_stories")))
            + section("Week Ahead", "charcoal", "What's Next", watch_list(brief.get("week_ahead")), last=True))
    mon = NOW - timedelta(days=NOW.weekday())
    lead = [brief["opening"]] if brief.get("opening") else []
    return page("The Compiled Brief · Weekly Edition", "The Week in Review.",
                f"Week of {mon.strftime('%b %d')} to {(mon + timedelta(days=4)).strftime('%b %d')}",
                "Sit down with this one.", lead, body, footer)


# ══════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    edition, forced = resolve_edition()
    state = load_state()
    print(f"\n📰 Daily Brief v3.0 · {NOW.strftime('%Y-%m-%d %H:%M ET')} · edition={edition} forced={forced}\n")

    if edition is None:
        print("Outside all delivery windows; nothing to send.")
        save_state(state)
        raise SystemExit(0)
    if not forced and state.get("sent", {}).get(edition) == DATE_KEY:
        print(f"{edition} edition already sent today; skipping.")
        save_state(state)
        raise SystemExit(0)

    print("[1/3] Gathering data...")
    data = gather(edition, state)
    print("\n[2/3] Claude writing...")
    brief = generate(data)
    print("\n[3/3] Rendering & sending...")
    html = render(brief, data)
    subjects = {"morning": f"🗞️ Compiled Brief · Morning · {NOW.strftime('%a %b %d')}",
                "close":   f"🗞️ Compiled Brief · Close · {NOW.strftime('%a %b %d')}",
                "weekly":  f"🗞️ Compiled Brief · Weekly · Week of {(NOW - timedelta(days=NOW.weekday())).strftime('%b %d')}"}
    send_email(subjects[edition], html)

    state.setdefault("sent", {})[edition] = DATE_KEY
    state["newsletter_seen"] = (state.get("newsletter_seen", []) + data.get("newsletter_ids", []))[-500:]
    heads = [x.get("headline", "") for items in (brief.get("sections") or {}).values() for x in items or []]
    heads = [h for h in heads if h]
    if edition == "morning":
        state["morning_headlines"] = {DATE_KEY: heads}
    elif edition == "close":
        state["close_headlines"] = {"date": DATE_KEY, "items": heads}
    save_state(state)
    print("\n✅ Done!\n")
