"""
Konner's Daily Brief — Agentic Email System
Runs Mon–Fri at 7:00 AM MT (weekday edition)
Runs Saturday at 8:00 AM MT (weekly recap edition)
"""

import os
import json
import smtplib
import urllib.request
import urllib.parse
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from zoneinfo import ZoneInfo

# ── Config ─────────────────────────────────────────────────────────────────
ANTHROPIC_KEY  = os.environ["ANTHROPIC_API_KEY"]
NEWS_KEY       = os.environ["NEWS_API_KEY"]
GMAIL_USER     = os.environ["GMAIL_USER"]
GMAIL_PASS     = os.environ["GMAIL_APP_PASS"]
SPORTS_KEY     = os.environ.get("SPORTS_API_KEY", "")   # optional, graceful fallback

MT = ZoneInfo("America/Denver")
NOW = datetime.now(MT)
TODAY = NOW.strftime("%A, %B %d, %Y")
IS_SATURDAY = NOW.weekday() == 5


# ══════════════════════════════════════════════════════════════════════════
#  LAYER 1 — RAW DATA FETCHERS
# ══════════════════════════════════════════════════════════════════════════

def http_get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


def newsapi_search(query, page_size=8, days_back=1, sort_by="publishedAt"):
    since = (datetime.utcnow() - timedelta(days=days_back)).strftime("%Y-%m-%dT%H:%M:%SZ")
    params = urllib.parse.urlencode({
        "q": query,
        "from": since,
        "sortBy": sort_by,
        "pageSize": page_size,
        "language": "en",
        "apiKey": NEWS_KEY,
    })
    try:
        data = http_get(f"https://newsapi.org/v2/everything?{params}")
        articles = data.get("articles", [])
        return [
            {
                "title": a.get("title", ""),
                "source": a.get("source", {}).get("name", ""),
                "description": a.get("description", "") or "",
                "url": a.get("url", ""),
                "publishedAt": a.get("publishedAt", ""),
            }
            for a in articles
            if a.get("title") and "[Removed]" not in a.get("title", "")
        ]
    except Exception as e:
        print(f"  NewsAPI error for '{query}': {e}")
        return []


def newsapi_headlines(category="general", page_size=6):
    params = urllib.parse.urlencode({
        "category": category,
        "country": "us",
        "pageSize": page_size,
        "apiKey": NEWS_KEY,
    })
    try:
        data = http_get(f"https://newsapi.org/v2/top-headlines?{params}")
        articles = data.get("articles", [])
        return [
            {
                "title": a.get("title", ""),
                "source": a.get("source", {}).get("name", ""),
                "description": a.get("description", "") or "",
            }
            for a in articles
            if a.get("title") and "[Removed]" not in a.get("title", "")
        ]
    except Exception as e:
        print(f"  NewsAPI headlines error: {e}")
        return []


def fetch_yankees():
    """Pull Yankees news from NewsAPI. Falls back to graceful message."""
    articles = newsapi_search("New York Yankees", page_size=6, days_back=2)
    return articles


def fetch_market_snapshot():
    """
    Returns a basic market context string using free Yahoo Finance-style
    RSS or falls back to asking Claude to note pre-market context from news.
    """
    # Try to get a market-flavored headline set as proxy data
    movers = newsapi_search(
        "S&P 500 OR Nasdaq OR Dow Jones OR pre-market OR stock futures",
        page_size=6
    )
    return movers


def gather_weekday_data():
    print("  → Markets & Finance...")
    markets = newsapi_search(
        "stock market OR S&P 500 OR earnings OR Wall Street OR equities OR pre-market futures",
        page_size=8
    )
    print("  → Earnings...")
    earnings = newsapi_search(
        "quarterly earnings results revenue EPS beat miss guidance",
        page_size=8
    )
    print("  → M&A / IPO / Capital Markets...")
    deals = newsapi_search(
        "merger acquisition IPO fundraise capital markets deal billion",
        page_size=6
    )
    print("  → Macro & Policy...")
    macro = newsapi_search(
        "Federal Reserve interest rates inflation GDP trade tariffs economic policy",
        page_size=8
    )
    print("  → Finance headlines...")
    fin_headlines = newsapi_headlines(category="business", page_size=6)
    print("  → Global news...")
    global_news = newsapi_search(
        "geopolitics international relations war sanctions diplomacy global economy",
        page_size=6
    )
    print("  → Health & research...")
    health = newsapi_search(
        "medical study health research clinical trial scientific breakthrough",
        page_size=5
    )
    print("  → Yankees...")
    yankees = fetch_yankees()

    return {
        "date": TODAY,
        "markets": markets,
        "earnings": earnings,
        "deals": deals,
        "macro": macro,
        "fin_headlines": fin_headlines,
        "global_news": global_news,
        "health": health,
        "yankees": yankees,
    }


def gather_saturday_data():
    print("  → Week's market news...")
    markets = newsapi_search(
        "S&P 500 OR stock market OR Nasdaq weekly performance sector",
        page_size=10, days_back=6
    )
    print("  → Week's earnings & deals...")
    earnings_deals = newsapi_search(
        "earnings results quarterly revenue IPO merger acquisition",
        page_size=10, days_back=6
    )
    print("  → Week's macro & policy...")
    macro = newsapi_search(
        "Federal Reserve inflation GDP trade policy tariffs economic data",
        page_size=8, days_back=6
    )
    print("  → Week's global news...")
    global_news = newsapi_search(
        "geopolitics international war sanctions diplomacy crisis",
        page_size=8, days_back=6
    )
    print("  → Next week's calendar...")
    calendar = newsapi_search(
        "CPI inflation report FOMC Fed meeting earnings next week economic calendar",
        page_size=6, days_back=3
    )
    print("  → Yankees week...")
    yankees = fetch_yankees()

    return {
        "date": TODAY,
        "markets": markets,
        "earnings_deals": earnings_deals,
        "macro": macro,
        "global_news": global_news,
        "calendar": calendar,
        "yankees": yankees,
    }


# ══════════════════════════════════════════════════════════════════════════
#  LAYER 2 — CLAUDE AGENT (writes the actual briefing)
# ══════════════════════════════════════════════════════════════════════════

def call_claude(system_prompt, user_prompt, max_tokens=3500):
    payload = json.dumps({
        "model": "claude-sonnet-4-20250514",
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
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.loads(r.read().decode())
    return data["content"][0]["text"]


SYSTEM_PROMPT = """You are the writer of "The Daily Brief" — a personal morning email newsletter for Konner Greer, a Finance & Fintech student at the University of Utah (graduating December 2027). Konner interns at University of Utah Financial Services and is deeply interested in financial markets, economic policy, SEC/DOJ regulatory enforcement, fintech, management consulting, and the New York Yankees.

Your job is to write a sharp, well-structured briefing that makes Konner feel informed and ahead of the room before markets open. 

TONE RULES:
- Write like a smart senior analyst explaining the day to a smart junior — direct, clear, no fluff
- Mix professional with conversational — polished but not stiff
- Use plain English to explain jargon (define it once, then use it)
- Always include: what happened, why it happened, why it matters
- Be specific — give numbers, names, percentages when available
- Do NOT write like a generic news aggregator. Synthesize and explain, don't just restate headlines
- Never pad a section. If there's nothing meaningful, say so briefly and move on

OUTPUT FORMAT:
You must output a valid JSON object. No markdown, no preamble, no explanation — just the raw JSON.
"""


def generate_weekday_briefing(data):
    def fmt(articles):
        if not articles:
            return "No articles found."
        lines = []
        for a in articles[:8]:
            desc = f" — {a['description'][:120]}" if a.get("description") else ""
            lines.append(f"• [{a['source']}] {a['title']}{desc}")
        return "\n".join(lines)

    user_prompt = f"""Today is {data['date']}. Write today's Daily Brief using ONLY the headlines below as source material.

Return a JSON object with EXACTLY these keys:

{{
  "opening": "3-4 sentence plain-English summary of the biggest 2-3 themes driving markets and news today. What kind of morning is it? What should Konner be watching?",

  "earnings": [
    {{
      "ticker": "TICKER",
      "company": "Full Company Name",
      "headline": "One sharp headline",
      "what": "What happened — numbers, beats/misses, key metrics",
      "why": "Why it happened — business context, industry dynamics",
      "matters": "Why it matters — what it signals about the market, consumer, sector"
    }}
  ],

  "movers": [
    {{
      "name": "Ticker or Asset Name",
      "change": "+X.X% or -X.X% or 4.48% (for yields/rates)",
      "direction": "up or down or flat",
      "reason": "One clear sentence explaining the move"
    }}
  ],

  "deals": [
    {{
      "type": "M&A or IPO or Fundraise or Capital Markets",
      "headline": "One sharp headline",
      "what": "What happened",
      "matters": "Why it matters"
    }}
  ],

  "fin_headlines": [
    {{
      "source": "Source name",
      "tag": "Topic tag (e.g. Fed Policy, Credit Markets, Trade)",
      "headline": "Sharp headline",
      "what": "What happened",
      "matters": "Why it matters",
      "context": "Broader context — what does this connect to?"
    }}
  ],

  "global_news": [
    {{
      "region": "Geographic region",
      "tag": "Topic tag",
      "headline": "Sharp headline",
      "summary": "2-3 sentences: what happened, why it matters for markets or geopolitics"
    }}
  ],

  "health": {{
    "source": "Journal or outlet name",
    "topic": "Topic tag",
    "headline": "Sharp headline",
    "finding": "What the research found",
    "relevance": "Why it's practically relevant to Konner",
    "caveat": "One honest limitation or nuance of the study"
  }},

  "yankees": {{
    "result": "Score or 'No game yesterday' or 'Off day'",
    "detail": "2-3 sentences on the game or recent news",
    "next_game": "Opponent, date, time ET, and broadcast if known"
  }},

  "closing": {{
    "text": "A quote, stat, or insight worth remembering",
    "attribution": "— Source or Author"
  }}
}}

Fill earnings with 3-5 companies. Fill movers with 4-6 items. Fill fin_headlines with exactly 3. Fill global_news with exactly 3. If a section has no meaningful content (e.g. no real deals today), return an empty array [] for that field.

--- SOURCE HEADLINES ---

MARKETS & FINANCE:
{fmt(data['markets'])}

EARNINGS:
{fmt(data['earnings'])}

M&A / IPO / DEALS:
{fmt(data['deals'])}

MACRO & POLICY:
{fmt(data['macro'])}

FINANCE HEADLINES:
{fmt(data['fin_headlines'])}

GLOBAL NEWS:
{fmt(data['global_news'])}

HEALTH & RESEARCH:
{fmt(data['health'])}

YANKEES:
{fmt(data['yankees'])}
"""
    raw = call_claude(SYSTEM_PROMPT, user_prompt, max_tokens=4000)
    # Strip any accidental markdown fences
    raw = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
    return json.loads(raw)


def generate_saturday_briefing(data):
    def fmt(articles, n=10):
        if not articles:
            return "No articles found."
        lines = []
        for a in articles[:n]:
            desc = f" — {a['description'][:120]}" if a.get("description") else ""
            lines.append(f"• [{a['source']}] {a['title']}{desc}")
        return "\n".join(lines)

    # Compute week date range
    mon = NOW - timedelta(days=NOW.weekday())
    fri = mon + timedelta(days=4)
    week_range = f"{mon.strftime('%B %d')}–{fri.strftime('%B %d, %Y')}"

    user_prompt = f"""Today is {data['date']} (Saturday). Write this week's Weekly Brief recap for the week of {week_range}.

Return a JSON object with EXACTLY these keys:

{{
  "week_range": "{week_range}",

  "opening": "3-4 sentence summary of what defined this week. What were the 2-3 dominant themes? What's the overall market and macro story?",

  "themes": [
    {{
      "title": "Short theme title",
      "body": "3-4 sentences explaining the theme, why it developed, and what it means going forward"
    }}
  ],

  "scoreboard": [
    {{
      "name": "S&P 500",
      "value": "e.g. 5,631",
      "change": "e.g. +1.2% WTD",
      "direction": "up or down or flat"
    }}
  ],

  "earnings_deals_recap": "3-4 paragraph narrative summary of the most important earnings reports and deals of the week. Synthesize — don't just list. What was the story earnings told about the economy?",

  "macro_policy_geo": [
    {{
      "tag": "Topic (e.g. Fed Policy, Trade, Geopolitics)",
      "headline": "Sharp headline",
      "summary": "3-4 sentences: what happened, why it matters, what to watch"
    }}
  ],

  "watch_next_week": [
    {{
      "day": "MON/TUE/WED/THU/FRI",
      "event": "Event name",
      "detail": "Why it matters and what to expect"
    }}
  ],

  "yankees_week": {{
    "record": "Week record and season record if known, e.g. 4-2 this week · 24-14 on the season",
    "summary": "2-3 sentences on the week — best performance, concerns, storylines",
    "next_week": "Upcoming series/opponents"
  }},

  "closing": {{
    "text": "A quote or insight fitting for the end of a week",
    "attribution": "— Source"
  }}
}}

Fill themes with 3 items. Fill scoreboard with: S&P 500, Nasdaq, Dow, 10-Yr Yield, Brent Crude, Gold — use approximate values from the headlines or note 'see markets' if unavailable. Fill macro_policy_geo with 3 items. Fill watch_next_week with 4-5 items.

--- SOURCE HEADLINES ---

MARKETS (WEEK):
{fmt(data['markets'])}

EARNINGS & DEALS (WEEK):
{fmt(data['earnings_deals'])}

MACRO & POLICY (WEEK):
{fmt(data['macro'])}

GLOBAL NEWS (WEEK):
{fmt(data['global_news'])}

NEXT WEEK CALENDAR:
{fmt(data['calendar'])}

YANKEES (WEEK):
{fmt(data['yankees'])}
"""
    raw = call_claude(SYSTEM_PROMPT, user_prompt, max_tokens=4000)
    raw = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
    return json.loads(raw)


# ══════════════════════════════════════════════════════════════════════════
#  LAYER 3 — HTML EMAIL RENDERER
# ══════════════════════════════════════════════════════════════════════════

CSS = """
<style>
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
</style>
"""


def e(text):
    """HTML-escape a string."""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def render_weekday(brief):
    d = brief

    # Earnings
    earnings_html = ""
    for co in d.get("earnings", []):
        earnings_html += f"""
        <div class="story">
          <div class="story-name">{e(co.get('ticker',''))} &nbsp;·&nbsp; {e(co.get('company',''))}</div>
          <div class="story-hed">{e(co.get('headline',''))}</div>
          <div class="wwm">
            <p><strong>What happened:</strong> {e(co.get('what',''))}</p>
            <p><strong>Why it happened:</strong> {e(co.get('why',''))}</p>
            <p><strong>Why it matters:</strong> {e(co.get('matters',''))}</p>
          </div>
        </div>"""
    if not earnings_html:
        earnings_html = '<p class="story-body">No major earnings reported overnight.</p>'

    # Movers
    movers_html = ""
    for mv in d.get("movers", []):
        dir_ = mv.get("direction", "flat")
        cls = "up" if dir_ == "up" else ("down" if dir_ == "down" else "flat")
        movers_html += f"""
        <div class="mv">
          <span class="mv-tk">{e(mv.get('name',''))}</span>
          <span class="mv-ch {cls}">{e(mv.get('change',''))}</span>
          <span class="mv-why">{e(mv.get('reason',''))}</span>
        </div>"""
    if not movers_html:
        movers_html = '<p class="story-body">Market data unavailable — check Bloomberg or CNBC pre-market.</p>'

    # Deals
    deals_html = ""
    for deal in d.get("deals", []):
        deals_html += f"""
        <div class="story">
          <div class="story-name">{e(deal.get('type',''))}</div>
          <div class="story-hed">{e(deal.get('headline',''))}</div>
          <div class="wwm">
            <p><strong>What happened:</strong> {e(deal.get('what',''))}</p>
            <p><strong>Why it matters:</strong> {e(deal.get('matters',''))}</p>
          </div>
        </div>"""
    if not deals_html:
        deals_html = '<p class="story-body">No major deals or IPOs today.</p>'

    # Finance headlines
    fin_html = ""
    for story in d.get("fin_headlines", []):
        fin_html += f"""
        <div class="story">
          <div class="story-name">{e(story.get('source',''))} &nbsp;·&nbsp; {e(story.get('tag',''))}</div>
          <div class="story-hed">{e(story.get('headline',''))}</div>
          <div class="wwm">
            <p><strong>What happened:</strong> {e(story.get('what',''))}</p>
            <p><strong>Why it matters:</strong> {e(story.get('matters',''))}</p>
            <p><strong>Context:</strong> {e(story.get('context',''))}</p>
          </div>
        </div>"""

    # Global
    global_html = ""
    for story in d.get("global_news", []):
        global_html += f"""
        <div class="story">
          <div class="story-name">{e(story.get('region',''))} &nbsp;·&nbsp; {e(story.get('tag',''))}</div>
          <div class="story-hed">{e(story.get('headline',''))}</div>
          <div class="story-body">{e(story.get('summary',''))}</div>
        </div>"""

    # Health
    h = d.get("health", {})
    health_html = f"""
        <div class="story">
          <div class="story-name">{e(h.get('source',''))} &nbsp;·&nbsp; {e(h.get('topic',''))}</div>
          <div class="story-hed">{e(h.get('headline',''))}</div>
          <div class="wwm">
            <p><strong>What they found:</strong> {e(h.get('finding',''))}</p>
            <p><strong>Why it's relevant:</strong> {e(h.get('relevance',''))}</p>
            <p><strong>Caveat:</strong> {e(h.get('caveat',''))}</p>
          </div>
        </div>"""

    # Yankees
    y = d.get("yankees", {})
    yankees_html = f"""
        <div class="ynk">
          <div class="ynk-score">{e(y.get('result', 'No game data available'))}</div>
          <div class="ynk-detail">{e(y.get('detail', ''))}</div>
          <div class="ynk-next">▶ Next: {e(y.get('next_game', 'Check MLB.com for schedule'))}</div>
        </div>"""

    # Closing
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

<div class="sec">
  <div class="lbl green">Global News</div>
  <h2>World Stories That Matter</h2>
  {global_html}
</div>

<div class="sec">
  <div class="lbl red">Health &amp; Research</div>
  <h2>What the Science Says</h2>
  {health_html}
</div>

<div class="sec">
  <div class="lbl navy">Yankees</div>
  <h2>Bronx Update</h2>
  {yankees_html}
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


def render_saturday(brief):
    d = brief

    # Themes
    themes_html = ""
    for i, t in enumerate(d.get("themes", []), 1):
        themes_html += f"""
        <div class="theme">
          <div class="theme-title">{i}. {e(t.get('title',''))}</div>
          <div class="theme-body">{e(t.get('body',''))}</div>
        </div>"""

    # Scoreboard
    sb_html = '<div class="sb">'
    for item in d.get("scoreboard", []):
        dir_ = item.get("direction", "flat")
        cls = "up" if dir_ == "up" else ("down" if dir_ == "down" else "flat")
        sb_html += f"""
        <div class="sb-item">
          <div class="sb-lbl">{e(item.get('name',''))}</div>
          <div class="sb-val">{e(item.get('value','—'))}</div>
          <div class="sb-chg {cls}">{e(item.get('change',''))}</div>
        </div>"""
    sb_html += "</div>"

    # Macro/policy/geo
    macro_html = ""
    for story in d.get("macro_policy_geo", []):
        macro_html += f"""
        <div class="story">
          <div class="story-name">{e(story.get('tag',''))}</div>
          <div class="story-hed">{e(story.get('headline',''))}</div>
          <div class="story-body">{e(story.get('summary',''))}</div>
        </div>"""

    # What to watch
    watch_html = ""
    for item in d.get("watch_next_week", []):
        watch_html += f"""
        <div class="watch">
          <div class="watch-day">{e(item.get('day',''))}</div>
          <div>
            <div class="watch-event">{e(item.get('event',''))}</div>
            <div class="watch-detail">{e(item.get('detail',''))}</div>
          </div>
        </div>"""

    # Yankees
    y = d.get("yankees_week", {})
    yankees_html = f"""
        <div class="ynk">
          <div class="ynk-score">{e(y.get('record',''))}</div>
          <div class="ynk-detail">{e(y.get('summary',''))}</div>
          <div class="ynk-next">▶ Next week: {e(y.get('next_week','Check MLB.com'))}</div>
        </div>"""

    cl = d.get("closing", {})
    week_range = d.get("week_range", "This Week")

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

<div class="sec">
  <div class="lbl teal">What to Watch</div>
  <h2>Next Week's Calendar</h2>
  {watch_html}
</div>

<div class="sec">
  <div class="lbl navy">Yankees</div>
  <h2>Week in the Bronx</h2>
  {yankees_html}
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
    print(f"\n🌅 Daily Brief starting — {TODAY} ({'Saturday' if IS_SATURDAY else 'Weekday'} edition)")

    if IS_SATURDAY:
        print("\n[1/3] Gathering week's data...")
        data = gather_saturday_data()

        print("\n[2/3] Claude writing Saturday recap...")
        brief = generate_saturday_briefing(data)

        print("\n[3/3] Rendering & sending...")
        html = render_saturday(brief)
        mon = NOW - timedelta(days=NOW.weekday())
        fri = mon + timedelta(days=4)
        week_range = f"{mon.strftime('%b %d')}–{fri.strftime('%b %d')}"
        send_email(f"📊 Weekly Brief — Week of {week_range}", html)

    else:
        print("\n[1/3] Gathering today's data...")
        data = gather_weekday_data()

        print("\n[2/3] Claude writing today's briefing...")
        brief = generate_weekday_briefing(data)

        print("\n[3/3] Rendering & sending...")
        html = render_weekday(brief)
        day_abbrev = NOW.strftime("%a %b %d")
        send_email(f"☀️ Daily Brief — {day_abbrev}", html)

    print("\n✅ Done!\n")
