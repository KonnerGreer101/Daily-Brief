"""
networking_renderer.py
HTML renderer for the networking section.
Paste the render_networking_section() function into your briefing.py,
and add the CSS block to your existing CSS string.
"""


# ── CSS to ADD inside your existing CSS = """<style>...""" block ──────────
# Paste this block anywhere inside the <style> tag in briefing.py

NETWORKING_CSS = """
/* ── Networking Section ─────────────────────── */
.net-section-intro{font-size:13px;color:#666;margin-bottom:18px;font-style:italic;line-height:1.6}
.net-tier-label{font-size:9.5px;font-weight:700;letter-spacing:2.5px;text-transform:uppercase;
  padding:4px 12px;margin-bottom:14px;display:inline-block;border-radius:2px;color:#fff}
.net-tier-1{background:#0d1b2a}
.net-tier-2{background:#3d5166}
.net-tier-3{background:#2a2a2a}
.net-card{display:flex;gap:14px;margin-bottom:14px;padding-bottom:14px;
  border-bottom:1px dashed #ddd8cf;align-items:flex-start}
.net-card:last-child{border-bottom:none;margin-bottom:0;padding-bottom:0}
.net-num{font-family:'Playfair Display',serif;font-size:20px;font-weight:700;
  color:#c9973a;min-width:22px;line-height:1}
.net-body{}
.net-name{font-family:'Playfair Display',serif;font-size:15px;font-weight:700;
  color:#0d1b2a;margin-bottom:2px}
.net-role{font-size:13px;color:#3d5166;margin-bottom:4px;font-weight:600}
.net-firm{font-size:12px;color:#555}
.net-city{font-size:11px;color:#8a9bb0;margin-top:1px}
.net-conn{display:inline-block;font-size:10px;font-weight:600;letter-spacing:1px;
  text-transform:uppercase;color:#fff;background:#c9973a;padding:2px 8px;
  border-radius:10px;margin-top:6px}
.net-conn.seo{background:#1e4d2b}
.net-conn.byu{background:#003087}
.net-conn.uofu{background:#cc0000}
.net-url{font-size:11px;margin-top:5px}
.net-url a{color:#3d5166;text-decoration:none;border-bottom:1px solid #ddd8cf}
.net-tier-divider{margin:18px 0 14px;border:none;border-top:1px solid #e2ddd4}
.net-empty{font-size:13px;color:#888;font-style:italic;padding:8px 0}
"""


def render_networking_section(net: dict) -> str:
    """
    Renders the weekly networking section HTML.
    net = result of networking.fetch_networking_targets()
    Returns empty string if networking is disabled (non-Monday) or no results.
    """
    if not net or not net.get("enabled"):
        return ""

    tier1 = net.get("tier1", [])
    tier2 = net.get("tier2", [])
    tier3 = net.get("tier3", [])

    if not tier1 and not tier2 and not tier3:
        return ""

    week_cat  = net.get("week_category", "Finance")
    week_date = net.get("week_date", "")

    def conn_class(label: str) -> str:
        l = label.lower()
        if "utah" in l and "byu" not in l and "state" not in l:
            return "uofu"
        if "byu" in l or "brigham" in l or "utah state" in l or "westminster" in l:
            return "byu"
        if "seo" in l:
            return "seo"
        return ""

    def render_cards(contacts: list, tier_label: str, tier_class: str) -> str:
        if not contacts:
            return f"""
            <div class="net-tier-label {tier_class}">{tier_label}</div>
            <div class="net-empty">No contacts found this week in this tier.</div>"""

        html = f'<div class="net-tier-label {tier_class}">{tier_label}</div>'
        for i, c in enumerate(contacts, 1):
            name   = c.get("name", "")
            role   = c.get("role", "")
            firm   = c.get("firm", "")
            city   = c.get("city", "")
            url    = c.get("url", "")
            conn   = c.get("connection", "")
            cat    = c.get("category", "")

            role_line  = f'<div class="net-role">{role}</div>' if role else ""
            firm_line  = f'<div class="net-firm">🏢 {firm}</div>' if firm else ""
            city_line  = f'<div class="net-city">📍 {city}</div>' if city else ""
            cat_line   = f'<div class="net-firm" style="color:#8a9bb0;font-size:11px">Focus: {cat}</div>' if cat else ""
            conn_badge = f'<span class="net-conn {conn_class(conn)}">{conn}</span>' if conn else ""
            url_line   = f'<div class="net-url"><a href="{url}" target="_blank">View LinkedIn Profile →</a></div>' if url else ""

            html += f"""
            <div class="net-card">
              <div class="net-num">{i}</div>
              <div class="net-body">
                <div class="net-name">{name}</div>
                {role_line}
                {firm_line}
                {city_line}
                {cat_line}
                {conn_badge}
                {url_line}
              </div>
            </div>"""
        return html

    t1_html = render_cards(tier1, "Tier 1 — Core Front Office / Deal Exposure", "net-tier-1")
    t2_html = render_cards(tier2, "Tier 2 — Extended Finance", "net-tier-2")
    t3_html = render_cards(tier3, "Tier 3 — Consulting · Fintech · Gov · Misc", "net-tier-3")

    total = len(tier1) + len(tier2) + len(tier3)

    return f"""
<div class="sec" style="background:#fdf9f2;border-left:4px solid #c9973a">
  <div class="lbl copper">Weekly Networking</div>
  <h2>This Week's Targets — {week_cat}</h2>
  <p class="net-section-intro">
    {total} curated contacts for week of {week_date}. Prioritize Tier 1.
    Draft your outreach in Claude — paste the profile and ask for a personalized message.
    Deduplicated: you won't see the same person twice.
  </p>

  {t1_html}
  <hr class="net-tier-divider">
  {t2_html}
  <hr class="net-tier-divider">
  {t3_html}
</div>"""
