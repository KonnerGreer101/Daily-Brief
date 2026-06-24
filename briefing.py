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
#  LEARN — sequential curriculum (rotates by weekday, tracks progress)
# ══════════════════════════════════════════════════════════════════════════

LEARNING_PROGRESS_FILE = "learning_progress.json"

# Each weekday teaches one track. Mon=0 ... Fri=4 (Python's NOW.weekday()).
WEEKDAY_TRACKS = {
    0: "Accounting & 3-Statement",
    1: "Valuation",
    2: "M&A & Merger Math",
    3: "LBO & PE",
    4: "Markets & Deals",
}

# Vetted, interview-grade curriculum. The AI never writes these — it only adds
# today's real-world "example" (see learn_example in the prompt). Each card:
#   concept / what / why / iq+ia (interview Q&A) / tq+ta (test-yourself Q&A)
# Add cards any time; the index wraps safely at the end of a track.
CURRICULUM = {
  "Accounting & 3-Statement": [
    {"concept":"How the three statements connect",
     "what":"The income statement (revenue to net income over a period), the balance sheet (assets = liabilities + equity at a point in time), and the cash flow statement (net income adjusted to actual cash) form one linked system. Net income flows into retained earnings and tops the cash flow statement; ending cash returns to the balance sheet.",
     "why":"Every modeling and accounting question builds on this linkage. If you can't connect the statements fluidly, you can't model or handle follow-ups.",
     "iq":"Walk me through the three financial statements.",
     "ia":"Income statement: revenue minus expenses, ending in net income over a period. Balance sheet: assets, liabilities, and equity at a point in time, where assets = liabilities + equity. Cash flow statement: starts with net income, adjusts for non-cash items and working-capital changes across operating, investing, and financing. They link — net income feeds retained earnings on the balance sheet and tops the cash flow statement; ending cash on the CFS becomes the balance-sheet cash line.",
     "tq":"Net income links which two statements, and what line does it feed on each?",
     "ta":"It links the income statement to the balance sheet, feeding retained earnings (equity), and it is the starting line of the cash flow statement."},
    {"concept":"The depreciation walkthrough",
     "what":"Tracing a single change through all three statements is the classic test of whether you understand the linkages. Depreciation is non-cash, so it lowers taxable income but is added back on the cash flow statement.",
     "why":"\"Walk an item through the 3 statements\" is asked constantly. Depreciation is the canonical version and the gateway to harder ones.",
     "iq":"Depreciation increases by $10. Walk it through all three statements (40% tax).",
     "ia":"Income statement: pre-tax income falls $10; at 40% tax, net income falls $6. Cash flow: net income down $6, add back the $10 non-cash depreciation, so operating cash rises $4 and ending cash is up $4. Balance sheet: cash up $4, PP&E down $10, so assets down $6; retained earnings down $6. It balances.",
     "tq":"Same $10 depreciation but a 0% tax rate. What happens to ending cash?",
     "ta":"Nothing — net income falls the full $10, but you add back $10 of depreciation, so operating cash and ending cash are unchanged. On the balance sheet, PP&E down $10 and retained earnings down $10 offset."},
    {"concept":"Accrual vs cash accounting",
     "what":"Accrual recognizes revenue when earned and expenses when incurred, regardless of when cash moves. Cash accounting records only when cash changes hands. GAAP uses accrual, which is why the cash flow statement exists.",
     "why":"It's the root of why net income and cash differ — and most accounting curveballs trace back to this distinction.",
     "iq":"What's the difference between accrual and cash accounting?",
     "ia":"Accrual records revenue when earned and expenses when incurred, regardless of cash timing; cash accounting records only actual cash movement. GAAP requires accrual, so the cash flow statement reconciles accrual net income back to real cash.",
     "tq":"A company books $1M of revenue but collects no cash this quarter. Where does the $1M sit on the balance sheet?",
     "ta":"In accounts receivable, a current asset. Revenue is recognized under accrual, but until collected it's a receivable, not cash."},
    {"concept":"Working capital",
     "what":"Operationally, working capital is receivables plus inventory minus payables — the cash tied up running day-to-day operations. Rising working capital consumes cash.",
     "why":"It's a key free-cash-flow line and a fast diagnostic: working capital growing faster than revenue is a warning sign.",
     "iq":"What is working capital and why does it matter?",
     "ia":"Receivables plus inventory minus payables — the cash tied up in operations. It matters because rising working capital consumes cash, it's a major free-cash-flow line, and it flags trouble if it grows faster than revenue.",
     "tq":"Receivables jump while revenue is flat. What does that signal, and what's the cash effect?",
     "ta":"Customers are paying slower, or collections are deteriorating. Rising receivables are a use of working capital, lowering operating cash flow."},
    {"concept":"Why the cash flow statement is king",
     "what":"Net income relies on accrual judgments — revenue timing, depreciation methods, reserves — while operating cash flow tracks actual cash, leaving fewer discretionary levers. Cash ultimately determines survival.",
     "why":"Knowing why cash beats earnings is the mark of someone who reads financials critically, not just mechanically.",
     "iq":"If you could use only one statement to evaluate a company, which and why?",
     "ia":"The cash flow statement. It shows actual cash generated, which is harder to manipulate than accrual net income, and cash ultimately determines whether a company can fund itself and survive.",
     "tq":"Why is net income easier to manipulate than operating cash flow?",
     "ta":"Net income depends on accrual judgments — revenue recognition timing, depreciation methods, reserves — while operating cash flow tracks real cash, leaving fewer discretionary levers."},
    {"concept":"Deferred taxes",
     "what":"A deferred tax liability comes from timing differences between book and tax accounting — classically accelerated depreciation for tax versus straight-line for books. The company pays less tax now, more later.",
     "why":"DTLs show up in models and on balance sheets; knowing their source signals real accounting depth.",
     "iq":"Where does a deferred tax liability come from?",
     "ia":"Timing differences between book and tax accounting — most often accelerated depreciation for tax versus straight-line for books. The company defers tax now and pays more later, and that future obligation sits as a deferred tax liability.",
     "tq":"Name the classic cause of a deferred tax liability.",
     "ta":"Accelerated depreciation for tax versus straight-line for books."},
  ],
  "Valuation": [
    {"concept":"The DCF",
     "what":"A DCF values a company by its future cash flows: project unlevered free cash flows, discount them to today at WACC, add a discounted terminal value, sum to enterprise value, then subtract net debt for equity value.",
     "why":"The DCF is the backbone of intrinsic valuation and the single most-asked valuation question.",
     "iq":"Walk me through a DCF. (Aim for 20 seconds.)",
     "ia":"Project unlevered free cash flows for about five years, discount them to today at WACC, estimate a terminal value (Gordon growth or exit multiple) and discount that back too. Sum for enterprise value, subtract net debt for equity value, divide by shares for value per share.",
     "tq":"In a DCF, what do you discount unlevered free cash flows at, and what value does that produce?",
     "ta":"At WACC, which produces enterprise value — before subtracting net debt to reach equity value."},
    {"concept":"Enterprise value vs equity value",
     "what":"Equity value is the value to shareholders (market cap). Enterprise value is the value of the operating business to all capital providers: equity value plus debt, preferred, and minority interest, minus cash.",
     "why":"Mixing these up breaks every multiple and every DCF bridge — it's foundational plumbing.",
     "iq":"What's the difference between enterprise value and equity value?",
     "ia":"Equity value is the value to shareholders — market cap. Enterprise value adds debt, preferred, and minority interest and subtracts cash, capturing the whole operating business for all capital providers. EV is capital-structure-neutral, which is why it pairs with EBITDA.",
     "tq":"A company has $100 equity value, $40 debt, and $10 cash. What's enterprise value?",
     "ta":"$130 — equity value plus debt minus cash (100 + 40 − 10)."},
    {"concept":"EV/EBITDA vs P/E",
     "what":"EV/EBITDA is independent of capital structure and ignores D&A and taxes, so it compares companies with different leverage and asset intensity cleanly. P/E is distorted by leverage and one-time items.",
     "why":"Choosing the right multiple — and matching numerator to denominator — is core valuation literacy.",
     "iq":"Why use EV/EBITDA instead of P/E?",
     "ia":"EV/EBITDA strips out capital structure, D&A, and taxes, so it compares companies with different leverage and asset bases more cleanly; P/E is distorted by leverage and one-time items. EBITDA also proxies pre-tax operating cash flow.",
     "tq":"Why does EV pair with EBITDA but equity value pair with net income?",
     "ta":"EBITDA is pre-interest, available to all capital providers, matching EV. Net income is after interest, available to equity only, matching equity value. You must match numerator and denominator by capital claim."},
    {"concept":"WACC",
     "what":"WACC is the blended required return of all capital providers: (E/V) × cost of equity + (D/V) × cost of debt × (1 − tax). Cost of equity usually comes from CAPM.",
     "why":"It's the discount rate in every DCF; understanding what moves it shows you grasp risk and capital structure.",
     "iq":"What is WACC and how do you calculate it?",
     "ia":"Weighted average cost of capital — the blended required return across all capital. WACC = (E/V) × cost of equity + (D/V) × cost of debt × (1 − tax rate). Cost of equity typically via CAPM: risk-free + beta × equity risk premium.",
     "tq":"A company adds cheap debt. What usually happens to WACC, and why only up to a point?",
     "ta":"WACC usually falls — debt is cheaper than equity and tax-deductible — but only up to a point; too much debt raises default risk and the cost of both debt and equity, pushing WACC back up."},
    {"concept":"Terminal value",
     "what":"Terminal value captures everything beyond the explicit forecast. Two methods: Gordon growth (final FCF × (1+g) / (WACC − g)) and the exit-multiple method (terminal EV/EBITDA × final EBITDA).",
     "why":"Terminal value is often the majority of a DCF's output, so how you compute it matters enormously.",
     "iq":"How do you calculate terminal value?",
     "ia":"Two ways. Gordon growth: final-year FCF × (1 + g) ÷ (WACC − g), with g a modest perpetual rate around 2–3%. Exit multiple: apply a terminal EV/EBITDA multiple to final-year EBITDA. Discount the result back to present value.",
     "tq":"Name the two methods to calculate terminal value.",
     "ta":"Gordon growth (perpetuity formula) and the exit-multiple method."},
    {"concept":"The three valuation methodologies",
     "what":"Comparable companies, precedent transactions, and DCF. Precedent transactions usually print highest because they include control premiums; comps reflect public trading; DCF swings with assumptions.",
     "why":"Knowing how the methods rank, and why, lets you sanity-check any valuation range.",
     "iq":"Name the three main valuation methods and how their outputs typically rank.",
     "ia":"Comparable companies, precedent transactions, and DCF. Precedent transactions usually give the highest values because they embed control premiums; comps reflect current public trading; DCF varies with assumptions. A football-field chart shows the overlapping ranges.",
     "tq":"Which methodology usually produces the highest valuation, and why?",
     "ta":"Precedent transactions — they bake in the control premiums paid in past M&A deals."},
  ],
  "M&A & Merger Math": [
    {"concept":"Accretion / dilution basics",
     "what":"A deal is accretive if it raises the acquirer's EPS and dilutive if it lowers it. The quick all-stock rule: higher acquirer P/E than target P/E means accretive; lower means dilutive.",
     "why":"Accretion/dilution is the first thing a board asks and a staple of M&A interviews.",
     "iq":"Walk me through the basic merger math — accretive or dilutive?",
     "ia":"Compare the cost of acquiring the target's earnings to the acquirer's earnings yield. All-stock rule: if the acquirer's P/E is higher than the target's, it's accretive; if lower, dilutive. For cash or debt deals, compare the after-tax financing cost to the target's earnings yield (E/P).",
     "tq":"All-stock deal: acquirer P/E 18x, target P/E 22x. Accretive or dilutive?",
     "ta":"Dilutive — the acquirer's P/E is lower than the target's, so it's buying more expensive earnings than its own; EPS falls."},
    {"concept":"Stock deal vs asset deal",
     "what":"In a stock purchase the buyer acquires the legal entity and inherits all assets and liabilities. In an asset purchase the buyer picks specific assets and liabilities and gets a stepped-up tax basis.",
     "why":"Deal structure drives taxes, liabilities, and who prefers what — a frequent technical and modeling point.",
     "iq":"Stock deal vs asset deal — what's the difference?",
     "ia":"A stock purchase takes the whole legal entity with all assets and liabilities. An asset purchase cherry-picks assets and liabilities and gives a stepped-up tax basis (more future depreciation), but transferring contracts is harder. Sellers usually prefer stock deals; buyers often prefer asset deals.",
     "tq":"Which structure gives the buyer a stepped-up tax basis?",
     "ta":"An asset deal — the buyer writes assets up to purchase price, creating more future depreciation and tax savings."},
    {"concept":"The M&A lifecycle players",
     "what":"Sell-side: target, board, sell-side advisor running the process. Buy-side: acquirer, board, buy-side advisor. Plus lawyers, accountants on diligence, financing providers, and regulators.",
     "why":"You'll be one of these players soon; knowing the cast and the sequence shows you understand how deals actually run.",
     "iq":"Walk me through the players in an M&A lifecycle.",
     "ia":"Sell-side: the target, its board, and its sell-side advisor running the process. Buy-side: the acquirer, its board, and its advisor. Plus lawyers drafting the agreement, accountants on diligence, financing providers, and regulators on antitrust. The flow is origination, diligence, negotiation, signing, regulatory approval, then close.",
     "tq":"Who runs the sale process on the sell-side, and name two other parties in any deal.",
     "ta":"The target's sell-side advisor (bank) runs it; others include lawyers, diligence accountants, financing providers, and regulators."},
    {"concept":"Goodwill",
     "what":"Goodwill is the premium paid above the fair value of a target's identifiable net assets. Created in an acquisition; tested for impairment, not amortized, under US GAAP.",
     "why":"It appears in every acquisition model and purchase-price allocation.",
     "iq":"What is goodwill and when is it created?",
     "ia":"Goodwill is the premium paid above the fair value of a target's identifiable net assets — purchase price minus fair value of net tangible and identifiable intangible assets. It's created in an acquisition and, under US GAAP, tested for impairment rather than amortized.",
     "tq":"A buyer pays $500M for a company with $300M fair value of net identifiable assets. How much goodwill?",
     "ta":"$200M — purchase price minus fair value of net identifiable assets."},
    {"concept":"Strategic vs financial buyer",
     "what":"A strategic is an operating company buying for synergies and long-term fit. A financial buyer (PE firm) buys to improve and resell within a few years, relying on leverage.",
     "why":"It explains why bids differ and who tends to win competitive processes.",
     "iq":"Why might a strategic buyer pay more than a financial buyer?",
     "ia":"A strategic can realize synergies — cost and revenue gains from combining operations — that a standalone financial buyer generally can't, which justifies a higher price. Financial buyers rely instead on leverage and operational improvement for returns.",
     "tq":"Why can a strategic often outbid a PE firm?",
     "ta":"Synergies — cost and revenue gains only the combined company can capture — let the strategic justify a higher price."},
    {"concept":"The control premium",
     "what":"The amount paid above a target's current trading price to gain control. Control lets the buyer set strategy and capital allocation and capture synergies. Typically 20–40%.",
     "why":"It's the number a board weighs to accept a bid and that shareholders judge to decide if management overpaid.",
     "iq":"What is the control premium?",
     "ia":"The amount paid above a target's current trading price to gain control. Control lets the buyer direct strategy, capital allocation, and synergies, which is worth more than a passive minority stake. Premiums typically run 20–40%.",
     "tq":"Target trades at $40; an acquirer bids $52. What's the control premium?",
     "ta":"30% — (52 − 40) / 40."},
  ],
  "LBO & PE": [
    {"concept":"Walk me through an LBO",
     "what":"A PE firm buys a company with mostly debt and some equity, uses the target's cash flows to pay down that debt over about five years while improving operations, then exits via sale or IPO.",
     "why":"The LBO is the defining PE structure and the most-asked buy-side question.",
     "iq":"Walk me through an LBO.",
     "ia":"A PE firm buys a company using mostly debt and some equity. It uses the target's own cash flows to pay down the debt over about five years while improving operations, then exits via a sale or IPO. Returns come from debt paydown, EBITDA growth, and multiple expansion, and the leverage magnifies the equity return.",
     "tq":"Name the three sources of LBO returns.",
     "ta":"Debt paydown (deleveraging), EBITDA growth, and multiple expansion."},
    {"concept":"Why leverage boosts returns",
     "what":"With less equity in and more debt, the same value creation spreads over a smaller equity base, magnifying the percentage return — and debt is repaid with the company's own cash flow, shifting enterprise value to equity.",
     "why":"It's the core intuition behind why PE uses leverage at all, and a guaranteed follow-up.",
     "iq":"Why does leverage boost equity returns in an LBO?",
     "ia":"Less equity and more debt means the same dollar of value creation is spread over a smaller equity base, magnifying the percentage return. Debt is also paid down with the company's own cash flow, transferring enterprise value to equity over time. The tradeoff is higher risk.",
     "tq":"In one line, why does more debt magnify equity returns?",
     "ta":"The same value creation is spread over a smaller equity base, and debt is repaid with the company's own cash flow, shifting enterprise value to equity."},
    {"concept":"What makes a good LBO candidate",
     "what":"Stable, predictable cash flows; low existing debt; healthy margins; modest capex; a defensible position; and a clear exit. Cash flow stability matters most because it services the debt.",
     "why":"It's how sponsors screen targets, and it reveals whether you understand the role of leverage.",
     "iq":"What makes a good LBO candidate?",
     "ia":"Stable, predictable cash flows; low existing debt; healthy margins; modest capex; a defensible market position; and room for operational improvement with a clear exit. Cash flow stability matters most, because it services the debt.",
     "tq":"What single characteristic matters most for an LBO target, and why?",
     "ta":"Stable, predictable cash flow — it's what services the debt; without it the leverage becomes dangerous."},
    {"concept":"Return drivers and the paper LBO",
     "what":"Estimate entry equity (entry EV minus debt), grow EBITDA and pay down debt with free cash flow, then exit EV = exit EBITDA × exit multiple minus remaining debt. Return ≈ exit equity ÷ entry equity.",
     "why":"The paper LBO is a live, no-spreadsheet test of whether you can actually run the math.",
     "iq":"How would you do a quick paper LBO?",
     "ia":"Entry equity = entry enterprise value minus debt. Grow EBITDA and use free cash flow to pay down debt over the hold. Exit EV = exit EBITDA × exit multiple; subtract remaining debt for exit equity. Return ≈ exit equity ÷ entry equity — roughly a 2.5–3x over five years is about a 20–25% IRR.",
     "tq":"Roughly what IRR is a 3x return over 5 years?",
     "ta":"About 25% (3x over five years is ~24.6% IRR)."},
    {"concept":"IRR vs MOIC",
     "what":"MOIC is total cash returned ÷ invested, ignoring time. IRR is the annualized return that accounts for timing. The same multiple is a far better IRR when achieved faster.",
     "why":"Sponsors optimize both; understanding the time dimension separates real candidates from memorizers.",
     "iq":"IRR vs MOIC — how do you think about them?",
     "ia":"MOIC is total cash returned divided by invested capital, ignoring time. IRR is the annualized return accounting for timing. A 3x in three years is a much better IRR than a 3x in seven. Sponsors watch both — quick exits and dividend recaps boost IRR specifically.",
     "tq":"Same 3x MOIC — would a sponsor rather hit it in year 3 or year 7, and why?",
     "ta":"Year 3 — a 3x in three years is a far higher IRR (~44%) than in seven (~17%); faster cash return compounds better."},
    {"concept":"Multiple expansion",
     "what":"Exiting at a higher EV/EBITDA multiple than entry. It's the least controllable return lever because it depends on market conditions and sentiment at exit, not operations.",
     "why":"Leaning on multiple expansion to make a deal work is a red flag interviewers probe for.",
     "iq":"Of the three LBO return drivers, which is least reliable and why?",
     "ia":"Multiple expansion — it depends on market conditions at exit, outside the sponsor's control, unlike EBITDA growth and debt paydown, which the sponsor can influence.",
     "tq":"If you underwrite an 8x entry and an 8x exit, which return driver are you NOT relying on?",
     "ta":"Multiple expansion — a flat entry/exit multiple means returns come only from EBITDA growth and debt paydown."},
  ],
  "Markets & Deals": [
    {"concept":"Reading a P/E multiple",
     "what":"A P/E is what investors pay per dollar of annual earnings; inverted, it's the earnings yield. A high multiple implies expected growth, a low one implies skepticism or maturity.",
     "why":"It's the most common multiple in conversation; reading it fluently is basic market literacy.",
     "iq":"A stock trades at a 25x P/E. What does that mean in plain terms?",
     "ia":"Investors are paying $25 for every $1 of annual earnings; inverted, that's a 4% earnings yield. A high multiple signals expectations of strong growth; a low one signals skepticism, or a mature or riskier business.",
     "tq":"A 25x P/E equals what earnings yield?",
     "ta":"4% (1 / 25)."},
    {"concept":"Rates and valuations",
     "what":"Higher rates raise the discount rate on future cash flows, lowering their present value, so valuations compress — hardest on long-duration, high-growth stocks. Rates also raise borrowing costs and make bonds more competitive.",
     "why":"This is the single most important macro-to-equity link, and it's behind most market moves you'll narrate.",
     "iq":"How do rising interest rates affect equity valuations?",
     "ia":"Higher rates raise the discount rate applied to future cash flows, lowering their present value, so valuations compress — hitting long-duration, high-growth stocks hardest. Rates also raise corporate borrowing costs and make bonds a more competitive alternative to equities.",
     "tq":"Why do high-growth stocks fall most when rates rise?",
     "ta":"Their value is weighted toward distant future cash flows (long duration), which get discounted harder as the rate rises."},
    {"concept":"EBITDA and its critics",
     "what":"EBITDA is earnings before interest, taxes, depreciation, and amortization — a proxy for operating cash flow. Critics note it ignores real capex and flatters capital-intensive businesses.",
     "why":"You'll use EBITDA everywhere; knowing its blind spots shows judgment, not just mechanics.",
     "iq":"What is EBITDA, and why do people criticize it?",
     "ia":"Earnings before interest, taxes, depreciation, and amortization — a proxy for operating cash flow that strips out capital structure and accounting choices. Critics note it ignores real capex needs and can flatter capital-intensive businesses, so it can overstate true cash generation.",
     "tq":"What real cost does EBITDA ignore that hurts capital-intensive firms?",
     "ta":"Capital expenditures (and the related D&A) — EBITDA flatters businesses that must constantly reinvest to operate."},
    {"concept":"Sponsor vs strategic",
     "what":"A strategic is an operating company buying for synergies and long-term fit. A sponsor is a financial buyer (PE firm) buying to improve and resell within a few years using leverage.",
     "why":"It frames how any deal gets valued and who's likely bidding — useful for talking about live deals.",
     "iq":"Sponsor vs strategic — what's the difference?",
     "ia":"A strategic is an operating company buying for synergies and long-term fit. A sponsor is a financial buyer, a PE firm, buying to improve and resell within a few years using leverage. They value the same asset differently because their return models differ.",
     "tq":"Which buyer relies on leverage and a near-term resale?",
     "ta":"The financial sponsor (PE firm)."},
    {"concept":"Knowing a live deal cold",
     "what":"For any deal you raise, have five facts ready: acquirer and target, size, consideration (cash/stock/mix), strategic rationale, and the multiple paid. Then give a view.",
     "why":"\"Tell me about a recent deal\" is a near-certain prompt; fumbling it signals you don't follow markets.",
     "iq":"How would you describe a recent deal you've been following?",
     "ia":"Hit five things: the acquirer and target, the deal size, the consideration (cash, stock, or mix), the strategic rationale, and the multiple paid (e.g. EV/EBITDA). Then give a view on whether it's a smart deal. Never get caught without one ready.",
     "tq":"Name the five things to have ready about any deal you discuss.",
     "ta":"Acquirer/target, size, consideration (cash/stock/mix), strategic rationale, and the multiple paid."},
    {"concept":"Financial acumen: revenue vs cash",
     "what":"Revenue is recognized when earned under accrual rules, even before payment; cash is what's collected. A company can grow revenue while burning cash if receivables, inventory, or capex swell.",
     "why":"This literacy underlies P&L fluency and every modeling conversation.",
     "iq":"How can a profitable company run out of cash?",
     "ia":"By tying up cash in working capital — rising receivables or inventory — or in heavy capex, while booking accrual profits. Net income is positive but operating cash flow turns negative.",
     "tq":"Where does booked-but-uncollected revenue sit on the balance sheet?",
     "ta":"In accounts receivable."},
  ],
}


# Multiple-choice drill banks, keyed by concept. correct = 0-based index.
# These are vetted; the AI never writes or alters them.
QUIZZES = {
  # ── Accounting & 3-Statement ──
  "How the three statements connect": [
    {"q":"Where does net income flow after the income statement?","options":["Into retained earnings on the balance sheet and the top of the cash flow statement","Only into the balance sheet","Only into the cash flow statement","Into goodwill"],"correct":0,"why":"Net income feeds retained earnings (equity) and starts the cash flow statement."},
    {"q":"The balance sheet equation is:","options":["Assets = Liabilities + Equity","Assets = Liabilities − Equity","Equity = Assets + Liabilities","Revenue = Assets − Liabilities"],"correct":0,"why":"Assets are funded by liabilities and equity, so they equal their sum."},
    {"q":"Which statement is a point-in-time snapshot rather than a period?","options":["The balance sheet","The income statement","The cash flow statement","None of them"],"correct":0,"why":"The balance sheet is a snapshot; the income and cash flow statements cover a period."},
  ],
  "The depreciation walkthrough": [
    {"q":"$10 of depreciation at a 40% tax rate. Net income:","options":["Falls $6","Falls $10","Falls $4","No change"],"correct":0,"why":"Pre-tax income falls $10; at 40% tax the after-tax hit is $6."},
    {"q":"In that same case, cash from operations changes by:","options":["+$4","−$6","−$10","$0"],"correct":0,"why":"Net income −$6 plus the $10 non-cash add-back = +$4."},
    {"q":"On the balance sheet, total assets:","options":["Fall $6 (cash +$4, PP&E −$10)","Fall $10","Rise $4","Are unchanged"],"correct":0,"why":"Cash +$4 and PP&E −$10 net to −$6, matched by retained earnings −$6."},
  ],
  "Accrual vs cash accounting": [
    {"q":"Under accrual accounting, revenue is recognized when:","options":["It is earned, regardless of cash receipt","Cash is received","The invoice is paid","The fiscal year ends"],"correct":0,"why":"Accrual recognizes revenue when earned; collection timing is separate."},
    {"q":"$1M of revenue booked but uncollected sits in:","options":["Accounts receivable","Cash","Deferred revenue","Retained earnings only"],"correct":0,"why":"Earned-but-uncollected revenue is a receivable."},
    {"q":"Why does the cash flow statement exist under GAAP?","options":["To reconcile accrual net income back to actual cash","To replace the income statement","To track only financing activities","Because tax law requires it"],"correct":0,"why":"Accrual net income differs from cash; the CFS reconciles them."},
  ],
  "Working capital": [
    {"q":"Operating working capital is roughly:","options":["Receivables + inventory − payables","Current assets + current liabilities","Cash − debt","Assets − equity"],"correct":0,"why":"It's the cash tied up in day-to-day operations."},
    {"q":"Rising working capital does what to cash?","options":["Consumes it (a use of cash)","Generates it","Has no effect","Increases net income"],"correct":0,"why":"Cash gets tied up in receivables and inventory."},
    {"q":"Receivables rising while revenue is flat suggests:","options":["Slower collections / a cash drain","Faster growth","Higher margins","Lower leverage"],"correct":0,"why":"Customers are paying slower, draining operating cash."},
  ],
  "Why the cash flow statement is king": [
    {"q":"Which figure is hardest to manipulate?","options":["Operating cash flow","Net income","Revenue","EPS"],"correct":0,"why":"It tracks actual cash, with fewer discretionary levers than accruals."},
    {"q":"Net income relies on judgments like:","options":["Revenue timing, depreciation method, reserves","Only cash receipts","Share count only","Market price"],"correct":0,"why":"These accrual choices give management discretion."},
    {"q":"Cash ultimately determines a company's:","options":["Ability to fund itself and survive","Stock ticker","Tax bracket only","Headcount"],"correct":0,"why":"Solvency depends on cash, not accrual profit."},
  ],
  "Deferred taxes": [
    {"q":"A deferred tax liability arises from:","options":["Timing differences between book and tax accounting","Paying tax twice","Foreign currency only","Goodwill impairment"],"correct":0,"why":"Book and tax recognize items on different schedules."},
    {"q":"The classic cause is:","options":["Accelerated depreciation for tax vs straight-line for books","Higher revenue","Stock buybacks","Dividend payments"],"correct":0,"why":"Tax depreciation runs faster early, deferring tax."},
    {"q":"A DTL means the company will:","options":["Pay more tax later","Never pay tax","Get a refund","Pay tax twice now"],"correct":0,"why":"It defers tax now and pays it in future periods."},
  ],
  # ── Valuation ──
  "The DCF": [
    {"q":"In a DCF, unlevered FCF is discounted at:","options":["WACC","Cost of equity","The risk-free rate","The dividend yield"],"correct":0,"why":"Unlevered cash flows belong to all capital providers, so WACC applies."},
    {"q":"Discounting unlevered FCF produces:","options":["Enterprise value","Equity value","Market cap","Book value"],"correct":0,"why":"It values the whole operating business."},
    {"q":"To get equity value from enterprise value you:","options":["Subtract net debt","Add net debt","Multiply by shares","Add goodwill"],"correct":0,"why":"Equity value = EV − net debt."},
  ],
  "Enterprise value vs equity value": [
    {"q":"EV = equity value plus ___ minus cash.","options":["Debt (and preferred, minority interest)","Revenue","Goodwill","Receivables"],"correct":0,"why":"EV adds all non-equity capital claims and nets out cash."},
    {"q":"Equity value $100, debt $40, cash $10. EV =","options":["$130","$150","$70","$110"],"correct":0,"why":"100 + 40 − 10 = 130."},
    {"q":"EV is preferred for cross-company comparison because it is:","options":["Capital-structure-neutral","Always larger","Tax-free","Based on book value"],"correct":0,"why":"It strips out differences in leverage."},
  ],
  "EV/EBITDA vs P/E": [
    {"q":"EBITDA pairs with EV because EBITDA is:","options":["Pre-interest (available to all capital)","After-tax","Equity-only","Net of capex"],"correct":0,"why":"Both sit above the interest line, so claims match."},
    {"q":"Net income pairs with:","options":["Equity value (the P in P/E)","Enterprise value","EBITDA","Revenue"],"correct":0,"why":"Net income is equity-only, matching equity value."},
    {"q":"A reason to prefer EV/EBITDA over P/E:","options":["It removes capital-structure distortion","It includes one-time items","It uses share price only","It ignores revenue"],"correct":0,"why":"It compares operating performance regardless of leverage."},
  ],
  "WACC": [
    {"q":"WACC is:","options":["The blended required return of all capital providers","The cost of equity only","The interest rate on debt","The risk-free rate"],"correct":0,"why":"It weights equity and after-tax debt costs."},
    {"q":"Cost of equity is usually estimated via:","options":["CAPM","The current ratio","EBITDA margin","Dividend payout"],"correct":0,"why":"CAPM: risk-free + beta × equity risk premium."},
    {"q":"Adding cheap debt lowers WACC only up to a point because:","options":["Too much debt raises default risk and both costs of capital","Debt is always free","Equity becomes cheaper forever","Taxes disappear"],"correct":0,"why":"Beyond a point, risk pushes WACC back up."},
  ],
  "Terminal value": [
    {"q":"The two terminal value methods are:","options":["Gordon growth and exit multiple","DCF and comps","P/E and P/B","LIFO and FIFO"],"correct":0,"why":"Perpetuity-growth or an exit EV/EBITDA multiple."},
    {"q":"The Gordon growth formula is:","options":["Final FCF × (1+g) / (WACC − g)","FCF × WACC","EBITDA × multiple − debt","FCF / shares"],"correct":0,"why":"A growing perpetuity discounted at WACC."},
    {"q":"A reasonable perpetual growth rate is about:","options":["2–3% (near long-run GDP/inflation)","8–10%","0%","Equal to WACC"],"correct":0,"why":"It can't exceed the economy's long-run growth."},
  ],
  "The three valuation methodologies": [
    {"q":"Which usually produces the highest values?","options":["Precedent transactions","Comparable companies","DCF always","Book value"],"correct":0,"why":"They embed control premiums."},
    {"q":"Precedent transactions run high because they include:","options":["Control premiums","Cash discounts","Tax credits","Lower multiples"],"correct":0,"why":"Past acquirers paid to take control."},
    {"q":"Comparable companies reflect:","options":["Current public trading levels","Past deal prices","Liquidation value","Replacement cost"],"correct":0,"why":"They mark to where peers trade now."},
  ],
  # ── M&A & Merger Math ──
  "Accretion / dilution basics": [
    {"q":"All-stock deal, acquirer P/E > target P/E. The deal is:","options":["Accretive","Dilutive","Always neutral","Impossible to tell"],"correct":0,"why":"Buying cheaper earnings than your own raises EPS."},
    {"q":"Acquirer P/E 18x, target P/E 22x, all stock:","options":["Dilutive","Accretive","Neutral","Cash-only"],"correct":0,"why":"Lower acquirer P/E than target means dilution."},
    {"q":"For a cash or debt deal, compare the after-tax financing cost to the target's:","options":["Earnings yield (E/P)","P/E","Revenue","Dividend"],"correct":0,"why":"If earnings yield exceeds financing cost, it's accretive."},
  ],
  "Stock deal vs asset deal": [
    {"q":"A stepped-up tax basis comes from a:","options":["Asset deal","Stock deal","All-stock merger","Tender offer"],"correct":0,"why":"Assets are rewritten to purchase price, boosting future depreciation."},
    {"q":"In a stock purchase the buyer inherits:","options":["All assets and liabilities","Only chosen assets","No liabilities","Only cash"],"correct":0,"why":"You buy the whole legal entity."},
    {"q":"Sellers usually prefer:","options":["Stock deals","Asset deals","Neither","Block trades"],"correct":0,"why":"Stock deals are cleaner and often better taxed for the seller."},
  ],
  "The M&A lifecycle players": [
    {"q":"Who runs the sale process on the sell-side?","options":["The target's sell-side advisor (bank)","The regulator","The acquirer's CEO","The lender"],"correct":0,"why":"The sell-side bank manages the auction/process."},
    {"q":"Antitrust review is handled by:","options":["Regulators","The sell-side bank","The accountants","The board only"],"correct":0,"why":"Agencies like the DOJ/FTC review competition."},
    {"q":"Correct deal sequence:","options":["Origination → diligence → negotiation → signing → approval → close","Close → diligence → signing","Signing → origination → close","Diligence → close → negotiation"],"correct":0,"why":"Deals originate, are diligenced, negotiated, signed, approved, then close."},
  ],
  "Goodwill": [
    {"q":"Goodwill equals:","options":["Purchase price − fair value of net identifiable assets","Purchase price + debt","EBITDA × multiple","Cash paid only"],"correct":0,"why":"It's the premium over identifiable net assets."},
    {"q":"Pay $500M; net identifiable assets fair value $300M. Goodwill:","options":["$200M","$800M","$300M","$0"],"correct":0,"why":"500 − 300 = 200."},
    {"q":"Under US GAAP, goodwill is:","options":["Tested for impairment, not amortized","Amortized over 10 years","Expensed immediately","Ignored"],"correct":0,"why":"It stays on the balance sheet, tested for impairment."},
  ],
  "Strategic vs financial buyer": [
    {"q":"A strategic can often pay more because of:","options":["Synergies","Lower taxes always","Free debt","Government subsidies"],"correct":0,"why":"Only the combined company captures synergies."},
    {"q":"A financial buyer (PE) relies primarily on:","options":["Leverage and operational improvement","Synergies","Brand fit","Vertical integration"],"correct":0,"why":"Returns come from debt and operations, not combination."},
    {"q":"Synergies are:","options":["Cost/revenue gains only the combined company captures","Always exactly zero","A type of debt","A tax credit"],"correct":0,"why":"They justify a strategic's higher bid."},
  ],
  "The control premium": [
    {"q":"Target $40, bid $52. Control premium:","options":["30%","12%","52%","24%"],"correct":0,"why":"(52 − 40) / 40 = 30%."},
    {"q":"The premium is paid to gain:","options":["Control over strategy, capital allocation, and synergies","A tax refund","A lower share count","Board seats and nothing else"],"correct":0,"why":"Control is worth more than a passive stake."},
    {"q":"Typical control premiums run:","options":["20–40%","1–2%","Over 100%","Negative"],"correct":0,"why":"That's the usual observed range."},
  ],
  # ── LBO & PE ──
  "Walk me through an LBO": [
    {"q":"An LBO is financed mostly with:","options":["Debt","Equity","Grants","Convertible preferred only"],"correct":0,"why":"Leverage is the defining feature."},
    {"q":"The debt is repaid using:","options":["The target's own cash flows","New equity each year","Government loans","Sponsor dividends"],"correct":0,"why":"The acquired company services its own debt."},
    {"q":"The three return sources are:","options":["Debt paydown, EBITDA growth, multiple expansion","Dividends, buybacks, splits","Revenue, tax, capex","Comps, DCF, precedents"],"correct":0,"why":"These three drive equity returns."},
  ],
  "Why leverage boosts returns": [
    {"q":"Leverage magnifies equity returns because:","options":["Value creation spreads over a smaller equity base","Debt is free","Taxes rise","Shares increase"],"correct":0,"why":"Less equity means a bigger percentage return per dollar of value."},
    {"q":"As debt is paid down, enterprise value shifts to:","options":["Equity","Lenders","The government","Goodwill"],"correct":0,"why":"Deleveraging transfers value to equity holders."},
    {"q":"The tradeoff of high leverage is:","options":["Higher risk if cash flows disappoint","Lower returns","No tax shield","Guaranteed slower growth"],"correct":0,"why":"Debt service becomes dangerous if cash flow falls."},
  ],
  "What makes a good LBO candidate": [
    {"q":"The most important trait of an LBO target:","options":["Stable, predictable cash flow","High growth at any cost","Lots of existing debt","Heavy capex"],"correct":0,"why":"Cash flow services the debt."},
    {"q":"Why does cash flow stability matter most?","options":["It services the debt","It raises the multiple","It cuts taxes","It boosts revenue"],"correct":0,"why":"Reliable cash keeps the leverage safe."},
    {"q":"A poor LBO candidate would have:","options":["Volatile cash flows and high capex","Low debt","Strong margins","A clear exit"],"correct":0,"why":"Volatility and capex strain debt service."},
  ],
  "Return drivers and the paper LBO": [
    {"q":"Roughly, a 3x return over 5 years is what IRR?","options":["~25%","~10%","~60%","~5%"],"correct":0,"why":"3^(1/5) − 1 ≈ 24.6%."},
    {"q":"Entry equity equals:","options":["Entry EV − debt","Entry EV + debt","Exit EV","EBITDA × multiple"],"correct":0,"why":"Equity is what's left after debt funds the purchase."},
    {"q":"Exit equity equals:","options":["Exit EV − remaining debt","Exit EV + debt","Entry equity × 2","EBITDA only"],"correct":0,"why":"Subtract leftover debt from exit enterprise value."},
  ],
  "IRR vs MOIC": [
    {"q":"MOIC ignores:","options":["Time","Cash","Risk entirely","Leverage"],"correct":0,"why":"It's a simple cash multiple with no time dimension."},
    {"q":"Same 3x MOIC is a better IRR if achieved in:","options":["3 years vs 7","7 years vs 3","Either — identical","Time never matters"],"correct":0,"why":"Faster cash return compounds to a higher IRR."},
    {"q":"A dividend recap tends to:","options":["Boost IRR by pulling cash forward","Lower IRR","Not affect returns","Reduce MOIC to zero"],"correct":0,"why":"Earlier cash raises the annualized return."},
  ],
  "Multiple expansion": [
    {"q":"The least controllable LBO return driver is:","options":["Multiple expansion","Debt paydown","EBITDA growth","Cost cuts"],"correct":0,"why":"It depends on the market, not the sponsor."},
    {"q":"Multiple expansion depends on:","options":["Market conditions at exit","The sponsor's effort","The tax rate","Debt covenants"],"correct":0,"why":"Exit sentiment sets the multiple."},
    {"q":"Underwriting 8x entry and 8x exit means you rely on:","options":["EBITDA growth and debt paydown","Multiple expansion","Dividends","Tax credits"],"correct":0,"why":"A flat multiple removes expansion from the math."},
  ],
  # ── Markets & Deals ──
  "Reading a P/E multiple": [
    {"q":"A 25x P/E equals an earnings yield of:","options":["4%","25%","2.5%","40%"],"correct":0,"why":"1 / 25 = 4%."},
    {"q":"A high P/E typically implies:","options":["Expected growth","Imminent bankruptcy","Low risk always","High dividends"],"correct":0,"why":"Investors pay up for anticipated growth."},
    {"q":"P/E means investors pay:","options":["$X per $1 of annual earnings","$X per share of revenue","$X per dollar of debt","Book value per share"],"correct":0,"why":"Price divided by earnings per share."},
  ],
  "Rates and valuations": [
    {"q":"Rising rates do what to valuations?","options":["Compress them","Expand them","No effect","Only affect bonds"],"correct":0,"why":"A higher discount rate lowers present values."},
    {"q":"Which stocks fall most when rates rise?","options":["Long-duration, high-growth","Dividend value stocks","Utilities only","None"],"correct":0,"why":"Their value sits in distant cash flows."},
    {"q":"Higher rates make ___ more competitive vs equities.","options":["Bonds","Real estate only","Gold only","Crypto"],"correct":0,"why":"Higher yields draw money toward bonds."},
  ],
  "EBITDA and its critics": [
    {"q":"EBITDA stands for earnings before:","options":["Interest, taxes, depreciation, amortization","Investment, trade, debt, assets","Income, total debt, amortization","Interest, time, debt, assets"],"correct":0,"why":"It adds back those four items to operating profit."},
    {"q":"The main criticism of EBITDA:","options":["It ignores real capex","It double-counts revenue","It overstates taxes","It excludes revenue"],"correct":0,"why":"Capex is a real cash cost it leaves out."},
    {"q":"EBITDA flatters which businesses?","options":["Capital-intensive ones","Asset-light software","Service firms only","Banks"],"correct":0,"why":"Heavy reinvestment is hidden by EBITDA."},
  ],
  "Sponsor vs strategic": [
    {"q":"Which buyer relies on leverage and a near-term resale?","options":["Financial sponsor (PE)","Strategic","Neither","Both equally"],"correct":0,"why":"PE firms lever up and exit within years."},
    {"q":"A strategic buys mainly for:","options":["Synergies and long-term fit","A quick flip","Tax losses","Index inclusion"],"correct":0,"why":"Operating fit and synergies drive strategics."},
    {"q":"They value the same asset differently because:","options":["Their return models differ","One pays cash only","One is illegal","They use the same model"],"correct":0,"why":"Synergy-driven vs leverage-driven returns diverge."},
  ],
  "Knowing a live deal cold": [
    {"q":"How many key facts should you have ready on a deal?","options":["Five: parties, size, consideration, rationale, multiple","Two","Ten","One"],"correct":0,"why":"Those five let you discuss any deal credibly."},
    {"q":"'Consideration' refers to:","options":["Cash, stock, or a mix","The buyer's CEO","The fairness opinion","The closing date"],"correct":0,"why":"It's how the buyer pays."},
    {"q":"After the facts, you should give:","options":["A view on whether it's a smart deal","Only the price","The lawyers' names","Nothing"],"correct":0,"why":"Interviewers want your judgment, not just recall."},
  ],
  "Financial acumen: revenue vs cash": [
    {"q":"A profitable company can run out of cash by:","options":["Tying up cash in working capital or capex","Booking too little revenue","Paying no taxes","Issuing stock"],"correct":0,"why":"Accrual profit can mask negative cash flow."},
    {"q":"Booked-but-uncollected revenue sits in:","options":["Accounts receivable","Cash","Goodwill","Equity"],"correct":0,"why":"It's a receivable until collected."},
    {"q":"Net income positive but operating cash flow negative signals:","options":["Cash tied up in working capital or capex","Fraud, always","A dividend","Debt repayment"],"correct":0,"why":"Profit isn't converting to cash."},
  ],
}


def load_learning_progress():
    try:
        with open(LEARNING_PROGRESS_FILE, "r") as f:
            data = json.load(f)
        if "tracks" not in data:
            data = {"tracks": {}}
        return data
    except:
        return {"tracks": {}}

def save_learning_progress(progress):
    try:
        with open(LEARNING_PROGRESS_FILE, "w") as f:
            json.dump(progress, f, indent=2)
        print("  [learning] progress saved")
    except Exception as ex:
        print(f"  [learning] save error: {ex}")

def get_todays_lesson(progress):
    """Today's track by weekday, the next vetted card in it, with its MC drill."""
    track = WEEKDAY_TRACKS.get(NOW.weekday())
    if not track:
        return None
    cards = CURRICULUM.get(track, [])
    if not cards:
        return None
    idx  = progress.get("tracks", {}).get(track, 0)
    card = cards[idx % len(cards)]
    return {
        "track":      track,
        "index":      idx,
        "next_index": idx + 1,
        "card":       card,
        "quiz":       QUIZZES.get(card["concept"], []),
    }


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
        # The newsletter sends mid-morning while today's US session is in progress,
        # so "today's" bar is incomplete. We want the most recent COMPLETED session's
        # close-to-close move. Use a 7-day window and pick the right two closes.
        now_et = datetime.now(ZoneInfo("America/New_York"))
        today_et = now_et.date()
        market_done_today = now_et.hour >= 16  # ~4pm ET close
        for symbol, name in tickers.items():
            try:
                hist = yf.Ticker(symbol).history(period="7d")
                closes = hist["Close"].dropna()
                if len(closes) >= 2:
                    last_is_today = closes.index[-1].date() == today_et
                    if last_is_today and not market_done_today and len(closes) >= 3:
                        # today's in-progress bar — recap the prior completed session
                        curr = float(closes.iloc[-2])
                        prev = float(closes.iloc[-3])
                    else:
                        curr = float(closes.iloc[-1])
                        prev = float(closes.iloc[-2])
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
- Each section covers COMPLETELY DIFFERENT stories — zero repetition across sections. A story or deal appears in exactly ONE section, ever. Before writing each section, check what you've already covered and skip anything that would repeat it.
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


def generate_weekday_briefing(data, trend_radar, lesson=None):
    sectors_str  = fmt_sectors(data.get("sectors", []))
    earnings_str = fmt_earnings_calendar(data.get("earnings_cal", []))
    if not lesson:
        lesson = get_todays_lesson(load_learning_progress())
    card           = lesson["card"]
    lesson_track   = lesson["track"]
    lesson_concept = card["concept"]

    user_prompt = f"""Today is {data['date']}. Write today's Daily Brief.

Return a JSON object with EXACTLY these keys:

{{
  "opening": ["3-5 short bullets, each one punchy line on a defining theme of the day — opinionated, takes a view. NOT a paragraph."],

  "market_dashboard": {{
    "recap": ["3-5 bullets recapping what happened in the markets in the most recent completed session and WHY — past tense, plain-English, focused on the key themes and biggest moves. If Treasury yields moved meaningfully (or the 10-yr is above 4.25%), one bullet must explain the ripple effects on mortgages, corporate borrowing, and stock valuations in plain terms."]
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
        "why_it_matters": "Is this onto something real? Explain in 1-2 sentences.",
        "image_url": "If the post has [HAS IMAGE: url], copy that exact URL here — otherwise empty string"}}
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

  "learn_example": "1-2 sentences anchoring TODAY'S assigned concept — \"{lesson_concept}\" (track: {lesson_track}) — to a REAL story from today's source data if one cleanly illustrates it; otherwise a crisp, specific hypothetical. Plain English. This is the ONLY part of the lesson you write; definitions and theory are already authored.",

  "one_thing": {{
    "headline": "The developing story worth following",
    "bullets": ["What's happening (one line)", "Why it matters (one line)", "The bigger picture (one line)", "What signal to watch for next (one line)"]
  }}
}}

RULES:
- market_dashboard: write `recap` as 3-5 plain-English bullets on what happened in the most recent completed session and why (past tense).
- ai_compute: 2-4 items, no overlap with markets_economy
- markets_economy earnings: 2-4 companies. movers: 6-8 using the REAL prices below — must include the major closes (S&P, Nasdaq, Dow, VIX, 10-Yr, WTI, Gold, Bitcoin when they moved meaningfully) plus any notable single-stock movers. This is the day's closing picture — there is no separate scoreboard.
- ALL deals (M&A, IPOs, fundraises) go in deal_flow ONLY — never in markets_economy or any other section.
- ZERO DUPLICATION: every story appears in exactly ONE section. If the recap already covers the day's big market move (e.g., a yield spike), government_policy must NOT retell it — only include a yields/Fed item there if there is a DISTINCT policy event (Fed decision, speech, auction result), and focus on the policy angle, not the market move.
- deal_flow: populate each sub-bucket with what's real from the source data. ma: 1-3 deals. vc: 2-4 rounds (prioritize notable size or investor). ipo_listings: 1-3 (include block trades and debt deals when notable). funds_secondaries: 1-2 if any real fund closes or secondaries. Return empty array [] for any sub-bucket with no real content today — never pad.
- government_policy: 2-4 items. SEC/DOJ only if major.
- crypto_fintech: 1-3 items. Skip if nothing real today — return []
- science_space: 1-3 items. Skip if nothing real — return []
- trending_x: use actual posts from the X feed below. PRIORITIZE quantitative posts — charts, data, market stats (especially anything tagged [HAS IMAGE]) — over pure memes. Aim for a mix weighted toward substance: data posts first, then sharp takes, then at most 1 meme if it tracks something real. When a post has [HAS IMAGE: url], copy that exact URL into image_url. If no good posts, set has_content to false and signals to []
- utah_regional: ONLY if real specific news — otherwise has_news: false, story: ""
- worth_watching: exactly 3 items. TODAY IS {TODAY}. ONLY include events that are in the FUTURE — never past events or things that already happened. If a calendar item like "Memorial Day" or any other event has already passed, skip it entirely and pick something genuinely upcoming instead.
- trend_radar: update from yesterday's narratives + add new ones. day_count should increment from yesterday.
- learn_example: write ONLY the example sentence(s) for today's concept "{lesson_concept}". Prefer a real story from today's source data; if none fits cleanly, use a clear hypothetical. Do not write definitions or theory — those are already authored and must not be duplicated here.
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
{fmt_x_posts(data.get('x_posts', []), 10)}

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
            "market_dashboard": {"recap": []},
            "ai_compute": [], "markets_economy": {"earnings": [], "movers": [], "earnings_preview": ""},
            "deal_flow": {"ma": [], "vc": [], "ipo_listings": [], "funds_secondaries": []},
            "government_policy": [], "crypto_fintech": [], "science_space": [],
            "trending_x": {"has_content": False, "signals": [], "x_note": ""},
            "utah_regional": {"has_news": False, "headline": "", "bullet_what": "", "bullet_why": ""},
            "worth_watching": [{"item": "Check back tomorrow", "why": "Brief generation encountered an issue today"}],
            "trend_radar": {"narratives": [], "new_this_week": ""},
            "learn": {"track": "", "concept": "N/A", "what": "Lesson unavailable today.", "why": "", "example": ""},
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
            "market_dashboard": {"recap": []},
            "ai_compute": [], "markets_economy": {"earnings": [], "movers": [], "earnings_preview": ""},
            "deal_flow": {"ma": [], "vc": [], "ipo_listings": [], "funds_secondaries": []},
            "government_policy": [], "crypto_fintech": [], "science_space": [],
            "trending_x": {"has_content": False, "signals": [], "x_note": ""},
            "utah_regional": {"has_news": False, "headline": "", "bullet_what": "", "bullet_why": ""},
            "worth_watching": [{"item": "Check back tomorrow", "why": "Brief generation encountered an issue today"}],
            "trend_radar": {"narratives": [], "new_this_week": ""},
            "learn": {"track": "", "concept": "N/A", "what": "Lesson unavailable today.", "why": "", "example": ""},
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
    ln   = d.get("learn", {})
    quiz = ln.get("quiz", [])
    letters = ["A","B","C","D"]
    learn_quiz_html = ""
    for i, item in enumerate(quiz, 1):
        opts = "".join(
            f'<div style="font-size:13px;color:#0d1b2a;padding:2px 0">{letters[j]}. {e(opt)}</div>'
            for j, opt in enumerate(item.get("options", []))
        )
        learn_quiz_html += (
            '<div style="margin-top:12px">'
            f'<div style="font-size:13.5px;color:#0d1b2a;font-weight:600;margin-bottom:5px">Q{i}. {e(item.get("q",""))}</div>'
            f'{opts}</div>'
        )
    answer_key_html = ""
    for i, item in enumerate(quiz, 1):
        ci   = item.get("correct", 0)
        opts = item.get("options", [])
        correct_txt = opts[ci] if 0 <= ci < len(opts) else ""
        letter = letters[ci] if 0 <= ci < len(letters) else "?"
        answer_key_html += (
            '<div style="margin-bottom:10px;padding-bottom:10px;border-bottom:1px dashed #ddd8cf">'
            f'<span style="font-weight:700;color:#0d1b2a">Q{i}: {letter}</span> '
            f'<span style="color:#3a3a3a">— {e(correct_txt)}</span>'
            f'<div style="font-size:12.5px;color:#666;margin-top:3px">{e(item.get("why",""))}</div>'
            '</div>'
        )
    answer_key_section = ""
    if answer_key_html:
        answer_key_section = (
            '<div class="sec">'
            '<div class="lbl charcoal">Answer Key</div>'
            '<h2>Test Yourself — Answers</h2>'
            f'{answer_key_html}</div>'
        )
    ot   = d.get("one_thing", {})

    # Markets recap — what happened in the prior session
    recap_html = render_bullets(md.get("recap"))
    if not recap_html:
        recap_html = '<p class="story-body">Markets recap unavailable today.</p>'

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
            img_html = ""
            img_url = (sig.get("image_url") or "").strip()
            if img_url.startswith("https://"):
                img_html = f'<img src="{e(img_url)}" alt="Chart from post" style="max-width:100%;border-radius:8px;margin-top:8px;border:1px solid #e5e5e5" />'
            x_html += f"""<div class="x-signal">
              <div class="x-handle">{e(sig.get('account',''))}</div>
              <div class="x-text">{e(sig.get('signal',''))}</div>
              {img_html}
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
  <div class="hdr-date">{e(d.get('date',''))}</div>
  <div class="hdr-sub">Everything you need. Nothing you don't.</div>
</div>

<div class="lead">{render_bullets(d.get('opening',''), light=True)}</div>

<div class="sec">
  <div class="lbl slate">Markets Recap</div>
  <h2>What Happened</h2>
  {recap_html}
</div>

<div class="sec">
  <div class="lbl charcoal">AI &amp; Compute</div>
  <h2>Intelligence &amp; Infrastructure</h2>
  {ai_html}
</div>

<div class="sec">
  <div class="lbl gold">Markets &amp; Economy</div>
  <h2>Earnings &amp; Movers</h2>
  {earnings_html}
  <div style="margin-top:20px">
    <div class="story-name" style="margin-bottom:10px">On the Move</div>
    {movers_html}
  </div>
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
  <div class="lbl charcoal">Learn</div>
  <h2>Build Your Edge</h2>
  <div class="term-box">
    <div class="term-label">Today's Lesson · {e(ln.get('track',''))}</div>
    <div class="term-word">{e(ln.get('concept',''))}</div>
    <div class="term-def">{e(ln.get('what',''))}</div>
    <div class="term-ctx" style="margin-top:10px"><strong style="color:#c9973a">Why it matters:</strong> {e(ln.get('why',''))}</div>
    <div class="term-ctx" style="margin-top:6px"><strong style="color:#c9973a">In practice:</strong> {e(ln.get('example',''))}</div>
  </div>
  <div style="margin-top:14px;padding:14px 16px;background:#f5f2ec;border-left:3px solid #0d1b2a;border-radius:0 3px 3px 0">
    <div style="font-size:10.5px;font-weight:700;letter-spacing:1.5px;text-transform:uppercase;color:#0d1b2a;margin-bottom:6px">Interview angle</div>
    <div style="font-size:13px;color:#3a3a3a;line-height:1.6;font-weight:600;margin-bottom:5px">{e(ln.get('interview_q',''))}</div>
    <div style="font-size:13px;color:#3a3a3a;line-height:1.65">{e(ln.get('interview_a',''))}</div>
  </div>
  <div style="margin-top:12px;padding:14px 16px;border:1px dashed #c9973a;border-radius:4px;background:#fbf6ec">
    <div style="font-size:10.5px;font-weight:700;letter-spacing:1.5px;text-transform:uppercase;color:#a9772a;margin-bottom:4px">Test yourself</div>
    <div style="font-size:12px;color:#8a7a55;font-style:italic">Answers are at the very bottom of today's brief.</div>
    {learn_quiz_html}
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

{answer_key_section}

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
    learning_progress = load_learning_progress()
    lesson = None

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
        lesson = get_todays_lesson(learning_progress)
        if lesson:
            print(f"  [learning] today's lesson — {lesson['track']}: {lesson['card']['concept']}")
        data = gather_weekday_data()
        print("\n[2/3] Claude writing today's briefing...")
        brief = generate_weekday_briefing(data, trend_radar, lesson)
        if lesson:
            card = lesson["card"]
            brief["learn"] = {
                "track":       lesson["track"],
                "concept":     card["concept"],
                "what":        card["what"],
                "why":         card["why"],
                "example":     brief.get("learn_example", ""),
                "interview_q": card["iq"],
                "interview_a": card["ia"],
                "quiz":        lesson.get("quiz", []),
            }
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

    # Advance learning progress after a successful weekday run
    if lesson and brief.get("learn_example"):
        learning_progress.setdefault("tracks", {})[lesson["track"]] = lesson["next_index"]
        save_learning_progress(learning_progress)

    print("\n✅ Done!\n")
