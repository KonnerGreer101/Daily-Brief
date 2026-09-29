"""
The Daily Brief v3.0
Morning edition (pre-market), Closing edition (after the bell), Weekly edition (Saturday).
Schedules run in UTC; the script checks Eastern time so daylight saving never shifts delivery.
"""

import os, json, smtplib, time, urllib.request, urllib.parse, urllib.error, xml.etree.ElementTree as ET, re, html as htmllib
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
WEB_SEARCH_USES = {"morning": 10, "close": 8, "weekly": 10}   # live searches Claude may run per edition

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
            out[sym] = {"name": name, "price": round(curr, 2), "change_pct": pct,
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
            lines.append(f"• {i['name']}: {i['price']:,.2f} ({i['change_pct']:+.2f}%)")
    return "\n".join(lines)

def fmt_movers(m):
    return "\n".join(f"• {r['ticker']}: ${r['price']:,.2f} ({r['change_pct']:+.2f}%)" for r in m) or "No mover data."


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
    "gnw_ma":          "https://www.globenewswire.com/RssFeed/subjectcode/27-Mergers%20And%20Acquisitions/feedTitle/GlobeNewswire%20-%20Mergers%20And%20Acquisitions",
}

SOURCE_NAMES = {
    "marketwatch_top": "MarketWatch", "marketwatch_mk": "MarketWatch", "ft_home": "FT",
    "cnbc_markets": "CNBC", "cnbc_finance": "CNBC", "bbc_world": "BBC", "bbc_business": "BBC",
    "bbc_tech": "BBC", "guardian_world": "The Guardian", "guardian_tech": "The Guardian",
    "npr_politics": "NPR", "npr_business": "NPR", "politico": "Politico", "techmeme": "Techmeme",
    "venturebeat": "VentureBeat", "wired_ai": "Wired", "axios": "Axios", "techcrunch_ma": "TechCrunch",
    "techcrunch_fund": "TechCrunch", "crunchbase_news": "Crunchbase", "prn_ma": "PR Newswire",
    "gnw_ma": "GlobeNewswire",
}


# ══════════════════════════════════════════════════════════════════════════
#  GATHERERS (one per edition)
# ══════════════════════════════════════════════════════════════════════════

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
        edgar_days, news_hours = (3 if NOW.weekday() == 0 else 1), (72 if NOW.weekday() == 0 else 24)
        congress_days = 3 if NOW.weekday() == 0 else 1
    elif edition == "close":
        print("  → Session close + movers...")
        d["quotes"] = fetch_quotes(INDEX_TICKERS, "session")
        d["movers"] = fetch_movers("session")
        print("  → Today's earnings...")
        d["earnings"] = fetch_earnings(DATE_KEY, DATE_KEY)
        print("  → X feeds...")
        d["x_posts"] = fetch_x_feeds_fast()
        d["morning_headlines"] = state.get("morning_headlines", {}).get(DATE_KEY, [])
        edgar_days, news_hours, congress_days = 0, 12, 1
    else:  # weekly
        print("  → Weekly performance...")
        d["quotes"] = fetch_quotes(INDEX_TICKERS, "week")
        d["movers"] = fetch_movers("week")
        mon = (NOW + timedelta(days=(7 - NOW.weekday()))).strftime("%Y-%m-%d")
        fri = (NOW + timedelta(days=(11 - NOW.weekday()))).strftime("%Y-%m-%d")
        d["earnings"] = fetch_earnings(mon, fri)
        edgar_days, news_hours, congress_days = 6, 150, 6
    print("  → Macro (BLS, BEA, EIA, Treasury, FRED)...")
    d["macro"], d["new_releases"] = gather_macro(state)
    print("  → SEC EDGAR deals...")
    d["edgar_deals"], d["edgar_other"] = fetch_edgar_deals(days_back=edgar_days, max_docs=8 if edition == "weekly" else 6)
    print("  → Congress + Federal Register...")
    d["congress"] = fetch_congress(congress_days)
    d["fedreg"]   = fetch_federal_register(congress_days)
    print("  → News feeds...")
    d.update(_news_block(news_hours))
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


SYSTEM_PROMPT = """You write "The Daily Brief," a personal newsletter for Konner Greer, a Finance & Fintech student at the University of Utah (graduating Spring 2028) who is recruiting for front-office finance roles (investment banking / M&A) and investing his own money.

GOALS: make him (1) a sharper investor, (2) a better M&A analyst, and (3) fluent in what is happening across markets, deals, tech, macro, and policy.

VOICE:
- Senior analyst briefing a junior: sharp, opinionated, specific. Synthesize and connect dots; do not just list headlines.
- Plain English: whenever you use a finance term (EBITDA multiple, CVR, go-shop, basis points, term premium, etc.), the item's "plain_english" field explains it in one or two simple sentences a smart student can repeat.
- High-level only. Skip minor stories. If a section has nothing important, return fewer items rather than padding.
- Never use em dashes. Use commas, colons, or separate sentences.

ACCURACY (critical):
- Use the provided data first. Use web search to confirm facts, fill in deal terms, find what moved a stock, or catch major news the feeds missed.
- Never invent numbers, multiples, advisors, quotes, or dates. If a figure is not disclosed or you cannot verify it, say "not disclosed" or leave it out.
- Numbers in the MARKET DATA blocks are authoritative; do not contradict them.
- Each story appears in exactly one section.

DEAL ANALYST LENS (for every deal):
- Equity value vs enterprise value (and the difference: net debt), consideration (cash / stock / mix), premium to the unaffected price.
- Implied multiple (EV/EBITDA or EV/Revenue) when disclosed or directly computable from disclosed figures; show the math briefly. Compare to what is typical for the sector when you can support it.
- Structure features worth learning (CVR, go-shop, termination fees, financing, tender offer vs merger vote, regulatory risk), advisors if disclosed.
- One "takeaway": the lesson an analyst should draw.

OUTPUT: valid JSON only. No markdown, no code fences, no text before or after the JSON object."""

DEAL_SCHEMA = """{"headline": "sharp headline", "parties": "Acquirer / Target", "size": "deal value or 'not disclosed'", "type": "Strategic / Take-private / Merger of equals / Tender offer / etc.",
       "what": "2-3 sentences: terms, consideration, premium, timing",
       "analyst_lens": ["3-5 bullets: EV vs equity value, implied multiple with math, premium context, structure features, financing, advisors"],
       "plain_english": "explain the key concept(s) simply", "takeaway": "the lesson for an analyst"}"""

STORY_SCHEMA = """{"headline": "sharp headline", "what": "1-2 sentences: what happened, specific", "why": "1-2 sentences: why it matters for markets, valuations, or competition", "watch": "1 sentence: what to watch next", "plain_english": "optional: simple explanation of any jargon, or empty string"}"""

def _common_blocks(d):
    return f"""=== MACRO DATA (🆕 = released since the last edition) ===
{d['macro']}

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

=== CONGRESS: MAJOR BILL ACTIONS ===
{chr(10).join(d['congress']) or 'None.'}

=== FEDERAL REGISTER: EXECUTIVE ACTIONS + SIGNIFICANT RULES ===
{chr(10).join(d['fedreg']) or 'None.'}

=== POLITICS / WORLD / GEOPOLITICS NEWS ===
{fmt_articles(d['policy'], 30)}"""

def build_prompt(d):
    ed = d["edition"]
    if ed == "morning":
        return f"""Today is {d['date']}. Write the MORNING EDITION (read before the 9:30 ET open). Target length: about 2,500 words of content (a 10-15 minute read).

=== FUTURES (live) ===
{fmt_quotes(d['futures'])}

=== PRIOR SESSION CLOSE ===
{fmt_quotes(d['quotes'])}

=== LARGE-CAP EARNINGS TODAY ===
{fmt_earnings(d['earnings'])}

{_common_blocks(d)}

Return JSON with exactly these keys:
{{
  "opening": ["3-5 one-line bullets: the defining themes of the day, opinionated"],
  "premarket": {{"bullets": ["3-5 bullets: where futures are and why, key overnight moves in yields, oil, dollar, Asia/Europe, and what it sets up for today"], "plain_english": "1-2 sentences"}},
  "top_tier_event": "ONE line only if today has payrolls, CPI, PPI, PCE, GDP, or a Fed decision/minutes/Chair speech (say time ET and why it matters); otherwise empty string",
  "earnings_today": [{{"ticker": "TICKER", "company": "Name", "timing": "Before open / After close", "expectations": "EPS and revenue consensus plus the one metric that matters most", "why_it_matters": "read-through for the stock, sector, or AI trade", "plain_english": "optional"}}],
  "deals": [{DEAL_SCHEMA}  // 3-5 most important deals announced since the last edition],
  "street": [{STORY_SCHEMA}  // 2-4 industry items: banks, dealmaking trends, hiring, fees, league tables, PE fundraising],
  "tech_ai": [{STORY_SCHEMA}  // 3-5: frontier labs (OpenAI, Anthropic, Google/Gemini, xAI/Grok, Meta), Mag 7, AI infrastructure (chips, data centers, power)],
  "policy_politics": [{STORY_SCHEMA}  // each item ALSO gets a "tag" field ("Geopolitics", "Congress", "White House", "Fed", "Elections", "Regulation"); 3-5, only market-moving or major world events]
}}
earnings_today: include every company in the earnings list (it is pre-filtered to large caps); empty list if none."""
    if ed == "close":
        return f"""Today is {d['date']}. Write the CLOSING EDITION (read after the 4:00 ET close). Target length: about 2,300 words of content (a 10-15 minute read).

This morning's edition already covered these headlines, so do NOT repeat them unless there is a material update (then say what changed):
{chr(10).join('• ' + h for h in d['morning_headlines']) or 'None recorded.'}

=== TODAY'S CLOSE (authoritative) ===
{fmt_quotes(d['quotes'])}

=== BIGGEST LARGE-CAP MOVERS TODAY ===
{fmt_movers(d['movers'])}

=== LARGE-CAP EARNINGS TODAY (actuals may be missing for after-close reporters; use web search for results and after-hours reaction) ===
{fmt_earnings(d['earnings'])}

{_common_blocks(d)}

=== X FEED (curated accounts) ===
{fmt_x_posts(d['x_posts'], 30)}

Return JSON with exactly these keys:
{{
  "opening": ["2-4 one-line bullets: how the day ended and why"],
  "recap": {{"bullets": ["4-6 bullets: indexes, sectors, yields, oil, dollar, and the WHY behind the tape"], "plain_english": "1-2 sentences on the most important concept from today"}},
  "movers": [{{"name": "TICKER (Company)", "change": "+x.x%", "reason": "one sentence catalyst, verified"}}  // 4-6 from the movers list that have a real catalyst],
  "earnings_results": [{{"ticker": "TICKER", "company": "Name", "result": "EPS and revenue vs estimates, guidance", "why": "what drove it", "reaction": "stock move, including after hours if known", "plain_english": "optional"}}],
  "macro": [{{"release": "name", "result": "actual vs prior (and vs expectations only if verified)", "takeaway": "what it means for the Fed, yields, and stocks"}}  // only data released today; empty list if none],
  "late_breaking": [{STORY_SCHEMA}  // each item ALSO gets a "tag" field ("Deal", "Tech & AI", "Policy", "Politics", "Street"); 2-5 important items that broke during the trading day and were NOT in the morning edition; deals here use the analyst mindset in "why"],
  "what_matters_next": [{{"item": "short title", "why": "1-2 sentences"}}  // 3-5 genuinely market-moving things to follow; no minor calendar items],
  "trending_x": {{"has_content": true, "signals": [{{"account": "@handle", "signal": "what they said", "why_it_matters": "1 sentence", "image_url": "copy exact URL if the post has [HAS IMAGE: url], else empty"}}], "x_note": "1 sentence read on the feed"}}
}}
trending_x: use only posts from the X feed above; prioritize data and sharp takes. If there are no posts, set has_content to false and signals to []."""
    return f"""Today is {d['date']}. Write the WEEKLY EDITION (Saturday recap of Monday-Friday). Target length: about 3,000 words of content.

=== WEEKLY PERFORMANCE (authoritative, 5-session change) ===
{fmt_quotes(d['quotes'])}

=== BIGGEST LARGE-CAP MOVERS THIS WEEK ===
{fmt_movers(d['movers'])}

=== LARGE-CAP EARNINGS NEXT WEEK ===
{fmt_earnings(d['earnings'])}

{_common_blocks(d)}

Return JSON with exactly these keys:
{{
  "opening": ["3-5 one-line bullets: the story of the week"],
  "week_in_markets": {{"bullets": ["5-7 bullets: what drove stocks, rates, oil, dollar this week"], "plain_english": "1-2 sentences"}},
  "movers": [{{"name": "TICKER (Company)", "change": "+x.x%", "reason": "one sentence"}}  // 4-6],
  "top_deals": [{DEAL_SCHEMA}  // the 3-5 most important deals of the week],
  "street": [{STORY_SCHEMA}  // 2-3 industry themes of the week],
  "tech_ai": [{STORY_SCHEMA}  // 3-5 biggest tech and AI developments],
  "macro": [{{"release": "name", "result": "actual vs prior", "takeaway": "what it means"}}  // the week's key data],
  "policy_politics": [{STORY_SCHEMA}  // each item ALSO gets a "tag" field; 3-5],
  "week_ahead": [{{"item": "short title", "why": "1-2 sentences"}}  // 3-6 genuinely important events next week, including major earnings from the list]
}}"""

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

def story_cards(items, meta_key="tag"):
    html = ""
    for it in items or []:
        meta = f'<div class="bcard-meta">{e(it[meta_key])}</div>' if it.get(meta_key) else ""
        body = li("What", it.get("what")) + li("Why", it.get("why"), True) + li("Watch", it.get("watch"), True) + li("In plain English", it.get("plain_english"), True)
        html += f'<div class="bcard">{meta}<div class="bcard-hed">{e(it.get("headline",""))}</div><ul class="blist">{body}</ul></div>'
    return html

def deal_cards(items):
    html = ""
    for it in items or []:
        meta = " · ".join(x for x in [it.get("type"), it.get("size")] if x)
        lens = "".join(f"<li class=\"sub\">{e(x)}</li>" for x in it.get("analyst_lens") or [])
        body = (li("Parties", it.get("parties")) + li("What", it.get("what"))
                + (f'<li><strong>Analyst lens:</strong></li>{lens}' if lens else "")
                + li("In plain English", it.get("plain_english"), True) + li("Takeaway", it.get("takeaway")))
        html += (f'<div class="bcard"><div class="bcard-meta">{e(meta)}</div>'
                 f'<div class="bcard-hed">{e(it.get("headline",""))}</div><ul class="blist">{body}</ul></div>')
    return html

def earnings_cards(items, results=False):
    html = ""
    for it in items or []:
        if results:
            body = li("Result", it.get("result")) + li("Why", it.get("why"), True) + li("Reaction", it.get("reaction"), True)
        else:
            body = li("Expectations", it.get("expectations")) + li("Why it matters", it.get("why_it_matters"), True)
        body += li("In plain English", it.get("plain_english"), True)
        meta = e(it.get("timing", "")) if not results else ""
        html += (f'<div class="bcard">{f"<div class=bcard-meta>{meta}</div>" if meta else ""}'
                 f'<div class="bcard-hed">{e(it.get("company",""))} ({e(it.get("ticker",""))})</div><ul class="blist">{body}</ul></div>')
    return html

def mover_list(items):
    rows = "".join(f'<li><strong>{e(m.get("name",""))} {e(m.get("change",""))}:</strong> {e(m.get("reason",""))}</li>' for m in items or [])
    return f'<ul class="blist">{rows}</ul>' if rows else ""

def macro_cards(items):
    html = ""
    for m in items or []:
        html += (f'<div class="bcard"><div class="bcard-hed">{e(m.get("release",""))}</div><ul class="blist">'
                 f'{li("Result", m.get("result"))}{li("Takeaway", m.get("takeaway"), True)}</ul></div>')
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
    if ed == "morning":
        pm = brief.get("premarket", {})
        top = brief.get("top_tier_event", "")
        body = (section("Pre-Market", "navy", "Futures & Overnight",
                        tiles(d["futures"], ["ES=F", "NQ=F", "YM=F", "RTY=F"])
                        + tiles(d["quotes"], ["^TNX", "^VIX", "BZ=F", "GC=F", "DX-Y.NYB", "BTC-USD"])
                        + render_bullets(pm.get("bullets")) + pe_note(pm.get("plain_english"))
                        + (f'<div class="dash-note" style="margin-top:12px"><strong>Today:</strong> {e(top)}</div>' if top else ""))
                + section("Earnings Today", "gold", "Who Reports", earnings_cards(brief.get("earnings_today")))
                + section("Deals & Street", "copper", "M&A With an Analyst Lens", deal_cards(brief.get("deals")))
                + section("The Street", "slate", "Industry News", story_cards(brief.get("street")))
                + section("Tech & AI", "purple", "Frontier Labs & Infrastructure", story_cards(brief.get("tech_ai")))
                + section("Policy & Politics", "red", "What Moves Markets", story_cards(brief.get("policy_politics")), last=True))
        return page("The Daily Brief · Morning Edition", "Good Morning, Konner.", TODAY,
                    "Everything you need before the bell.", brief.get("opening"), body, "Morning")
    if ed == "close":
        rc = brief.get("recap", {})
        body = (section("The Close", "navy", "Market Recap",
                        tiles(d["quotes"], ["^GSPC", "^IXIC", "^DJI", "^RUT", "^VIX", "^TNX", "^IRX", "CURVE", "BZ=F", "CL=F", "GC=F", "DX-Y.NYB"])
                        + render_bullets(rc.get("bullets")) + pe_note(rc.get("plain_english")))
                + section("Movers", "slate", "Biggest Moves & Why", mover_list(brief.get("movers")))
                + section("Earnings Results", "gold", "Beat or Miss", earnings_cards(brief.get("earnings_results"), results=True))
                + section("Macro", "green", "Today's Data", macro_cards(brief.get("macro")))
                + section("Late-Breaking", "copper", "What Broke During the Session", story_cards(brief.get("late_breaking")))
                + section("What Matters Next", "charcoal", "Only the Big Stuff", watch_list(brief.get("what_matters_next")))
                + section("Trending on X", "indigo", "The Feed", x_section(brief.get("trending_x")), last=True))
        return page("The Daily Brief · Closing Edition", "Good Evening, Konner.", TODAY,
                    "How the day ended, and what matters next.", brief.get("opening"), body, "Close")
    wm = brief.get("week_in_markets", {})
    body = (section("The Week", "navy", "Markets in Review",
                    tiles(d["quotes"], ["^GSPC", "^IXIC", "^DJI", "^RUT", "^VIX", "^TNX", "BZ=F", "GC=F", "DX-Y.NYB", "BTC-USD"])
                    + render_bullets(wm.get("bullets")) + pe_note(wm.get("plain_english")))
            + section("Movers", "slate", "Biggest Moves of the Week", mover_list(brief.get("movers")))
            + section("Deals & Street", "copper", "Deals of the Week", deal_cards(brief.get("top_deals")))
            + section("The Street", "slate", "Industry Themes", story_cards(brief.get("street")))
            + section("Tech & AI", "purple", "The Week in AI", story_cards(brief.get("tech_ai")))
            + section("Macro", "green", "The Week's Data", macro_cards(brief.get("macro")))
            + section("Policy & Politics", "red", "What Moved Markets", story_cards(brief.get("policy_politics")))
            + section("Week Ahead", "charcoal", "Only the Big Stuff", watch_list(brief.get("week_ahead")), last=True))
    mon = NOW - timedelta(days=NOW.weekday())
    return page("The Weekly Brief · Saturday Edition", "The Week in Review.",
                f"Week of {mon.strftime('%b %d')} to {(mon + timedelta(days=4)).strftime('%b %d')}",
                "Read it once, sound sharp all weekend.", brief.get("opening"), body, "Weekly")


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
    subjects = {"morning": f"☀️ Morning Brief · {NOW.strftime('%a %b %d')}",
                "close":   f"🌙 Closing Brief · {NOW.strftime('%a %b %d')}",
                "weekly":  f"📊 Weekly Brief · Week of {(NOW - timedelta(days=NOW.weekday())).strftime('%b %d')}"}
    send_email(subjects[edition], html)

    state.setdefault("sent", {})[edition] = DATE_KEY
    if edition == "morning":
        heads = [x.get("headline", "") for k in ("deals", "street", "tech_ai", "policy_politics") for x in brief.get(k) or []]
        state["morning_headlines"] = {DATE_KEY: [h for h in heads if h]}
    save_state(state)
    print("\n✅ Done!\n")
