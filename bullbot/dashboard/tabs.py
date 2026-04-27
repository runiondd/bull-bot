"""Tab render functions ported from dashboard/handoff/components-tabs.jsx.

Each function: data: dict -> str (HTML fragment, no <html>/<body> wrapper).
Pure functions over the data dict from queries; no DB access here.
"""
from __future__ import annotations

import html
from datetime import date

from bullbot.dashboard.fmt import fmt_money, fmt_pct, pnl_class, phase_class, phase_label
from bullbot.dashboard.svg_charts import equity_chart_svg


# Overview helper components (kept local since only used here) ---------------

def _pnl_by_ticker(rows: list[dict]) -> str:
    """Diverging-bar visualization. CSS-only, no SVG."""
    filtered = [r for r in rows if r["realized"] != 0 or r["unrealized"] != 0]
    if not filtered:
        return '<div style="color: var(--fg-2); font-size: 12px">No P&amp;L yet — paper trial in progress.</div>'
    max_abs = max(abs(r["realized"] + r["unrealized"]) for r in filtered) or 1.0
    parts = []
    for r in filtered:
        total = r["realized"] + r["unrealized"]
        width_pct = (abs(total) / max_abs) * 100
        margin_left = "50%" if total >= 0 else f"{50 - width_pct / 2:.1f}%"
        gradient = ("linear-gradient(90deg, color-mix(in oklab, var(--pos) 50%, transparent), var(--pos))"
                    if total >= 0 else
                    "linear-gradient(90deg, var(--neg), color-mix(in oklab, var(--neg) 50%, transparent))")
        parts.append(f"""<div class="bar-row">
  <span class="bar-label">{html.escape(r['ticker'])}</span>
  <div class="bar-track" style="background: transparent; display: flex; justify-content: {'flex-start' if total >= 0 else 'flex-end'}; position: relative">
    <div style="position: absolute; left: 50%; top: 0; bottom: 0; width: 1px; background: var(--line-strong)"></div>
    <div class="bar-fill" style="width: {width_pct / 2:.1f}%; margin-left: {margin_left}; background: {gradient}; border-radius: 2px"></div>
  </div>
  <span class="num bar-amt {pnl_class(total)}">{fmt_money(total, signed=True, decimals=0)}</span>
</div>""")
    return "".join(parts)


def _universe_pipeline(universe: list[dict]) -> str:
    """4-column pipeline (discovering / paper_trial / live / no_edge)."""
    phases = ["discovering", "paper_trial", "live", "no_edge"]
    cols = []
    for p in phases:
        items = [u for u in universe if u["phase"] == p]
        tiles = []
        for u in items:
            strat = u["strategy"] or "—"
            pf = u["edge"]["pf_oos"]
            bar_pct = min(100.0, (pf / 2.5) * 100)
            bar_color = "var(--accent)" if pf >= 1.3 else "var(--neg)"
            tiles.append(f"""<div class="pipeline-tile">
  <span class="tile-ticker">{html.escape(u['ticker'])}</span>
  <span class="tile-pf">pf {pf:.2f}</span>
  <span class="tile-meta">{html.escape(strat)}</span>
  <span class="tile-meta">{u['iterations']} it · {u['paperTrades']} pt</span>
  <div class="tile-bar"><div style="width: {bar_pct:.1f}%; background: {bar_color}"></div></div>
</div>""")
        cols.append(f"""<div class="pipeline-col">
  <div class="col-head">
    <span><span class="chip {phase_class(p)}" style="margin-right: 6px">{phase_label(p)}</span></span>
    <span class="count">{len(items)}</span>
  </div>
  {''.join(tiles)}
</div>""")
    return f'<div class="pipeline">{"".join(cols)}</div>'


def _activity_feed(events: list[dict]) -> str:
    if not events:
        return '<div style="color: var(--fg-2); padding: 14px; font-size: 12px">No activity yet.</div>'
    icons = {"fill": "→", "exit": "←", "promotion": "↑",
             "proposal": "◇", "rejection": "×", "demotion": "↓"}
    items = []
    for e in events[:10]:
        icon = icons.get(e.get("type", ""), "·")
        items.append(f"""<div class="activity-item {html.escape(e.get('type', ''))}">
  <span class="time">{html.escape(e.get('ts', ''))}</span>
  <span class="ticker"><span class="icon">{icon}</span>{html.escape(e.get('ticker', ''))}</span>
  <span class="text">{html.escape(e.get('text', ''))}</span>
</div>""")
    return f'<div class="activity-list">{"".join(items)}</div>'


def overview_tab(data: dict) -> str:
    """Overview tab: equity curve + P&L by ticker + universe pipeline + activity feed."""
    eq_values = [float(p["total_equity"]) for p in data["equity_curve"]]
    m = data["metrics"]
    total_pnl = m.get("realized_pnl", 0) + m.get("unrealized_pnl", 0)
    pnl_cls = pnl_class(total_pnl)

    return f"""<div class="cols-2">
  <div class="card">
    <div class="card-head">
      <span class="card-title">Equity Curve · 30d</span>
    </div>
    <div class="card-body">
      {equity_chart_svg(eq_values)}
      <div style="display: flex; gap: 18px; margin-top: 6px; font-size: 11.5px; color: var(--fg-2)">
        <span><span class="num {pnl_cls}">{fmt_money(total_pnl, signed=True)}</span> total P&amp;L</span>
        <span>·</span>
        <span>Sharpe <span class="num">{m.get('sharpe_30d', 0):.2f}</span></span>
        <span>·</span>
        <span>Win {fmt_pct(m.get('win_rate', 0), decimals=0)}</span>
        <span>·</span>
        <span>Avg win <span class="num pos">{fmt_money(m.get('avg_win', 0), decimals=0)}</span></span>
        <span>·</span>
        <span>Avg loss <span class="num neg">{fmt_money(m.get('avg_loss', 0), decimals=0)}</span></span>
      </div>
    </div>
  </div>
  <div class="card">
    <div class="card-head"><span class="card-title">P&amp;L by Ticker</span></div>
    <div class="card-body">
      {_pnl_by_ticker(data['pnl_by_ticker'])}
    </div>
  </div>
</div>
<div class="cols-2">
  <div class="card">
    <div class="card-head">
      <span class="card-title">Universe Pipeline</span>
      <span class="card-title" style="font-size: 10px; color: var(--fg-3)">{len(data['universe'])} tickers</span>
    </div>
    <div class="card-body flush">
      {_universe_pipeline(data['universe'])}
    </div>
  </div>
  <div class="card">
    <div class="card-head"><span class="card-title">Activity</span></div>
    <div class="card-body flush">
      {_activity_feed(data['activity'])}
    </div>
  </div>
</div>"""


# ---- Positions tab helpers --------------------------------------------------

_MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _format_leg(leg: dict) -> str:
    """Format a single option leg as  '[L|S] qty× strikeKind Mon DD'."""
    side = "L" if leg.get("side") == "long" else "S"
    qty = leg.get("qty", 1)
    kind = leg.get("kind", "")
    strike = leg.get("strike", "")
    expiry = leg.get("expiry")  # YYYY-MM-DD string

    if kind == "S":
        return f"{side} {qty}× shares"

    exp_str = ""
    if expiry:
        try:
            y, m, d = expiry.split("-")
            exp_str = f" {_MONTH_ABBR[int(m) - 1]} {int(d)}"
        except (ValueError, IndexError):
            exp_str = f" {expiry}"

    return f"{side} {qty}× {strike}{kind}{exp_str}"


def _position_card(p: dict) -> str:
    """Render a single PositionCard."""
    is_open = p.get("isOpen", False)
    pnl = p.get("pnl", 0.0)
    pnl_pct = p.get("pnlPct", 0.0)
    class_name = p.get("className", "")
    ticker = html.escape(str(p.get("ticker", "")))

    card_class = "pos" if pnl >= 0 else "neg"
    closed_class = "" if is_open else " closed"

    # Category tag
    is_growth = class_name.startswith("Growth")
    tag_label = "growth" if is_growth else "income"
    tag_class = "growth" if is_growth else "income"

    # Strategy label: insert space before each capital letter then strip
    import re
    strat_label = re.sub(r"([A-Z])", r" \1", class_name).strip()

    # Open/closed chip
    chip_label = "open" if is_open else "closed"
    chip_class = "open" if is_open else "closed"

    # DTE tag (only when open and dte > 0)
    dte = p.get("dte", 0)
    dte_tag = f'<span class="tag mono">{dte} DTE</span>' if is_open and dte > 0 else ""

    # Meta line
    entry_spot = p.get("entrySpot", 0.0)
    open_date = html.escape(str(p.get("openedAt", "")))
    meta_extra = ""
    if not is_open:
        closed_at = html.escape(str(p.get("closedAt", "")))
        meta_extra = f' · closed <span class="mono">{closed_at}</span>'
    else:
        mark = p.get("mark", 0.0)
        open_price = p.get("openPrice", 0.0)
        meta_extra = (f' · mark <span class="mono">${mark:.2f}</span>'
                      f' vs open <span class="mono">${open_price:.2f}</span>')

    # P&L display
    pnl_cls = pnl_class(pnl)
    pnl_dollar = fmt_money(pnl, signed=True, decimals=0)
    pnl_percent = fmt_pct(pnl_pct, signed=True)

    # Legs
    legs = p.get("legs", [])
    legs_str = html.escape("  /  ".join(_format_leg(l) for l in legs))

    # Rationale
    rationale_block = ""
    rationale = p.get("rationale")
    if rationale:
        rationale_block = f'\n      <div class="pos-rationale">{html.escape(str(rationale))}</div>'

    # Progress bar (only when open)
    target = (p.get("exitRules") or {}).get("profit_target_pct", 0.5) or 0.5
    progress_block = ""
    if is_open:
        progress_pct = min(100.0, max(0.0, (pnl_pct / target) * 100))
        bar_color = "var(--accent)" if pnl >= 0 else "var(--neg)"
        progress_block = f"""
      <div class="progress" style="margin-top: 4px">
        <div style="width: {progress_pct:.1f}%; background: {bar_color}"></div>
      </div>"""

    return f"""<div class="position-card {card_class}{closed_class}">
  <div style="display: flex; justify-content: space-between; align-items: flex-start">
    <div>
      <div class="pos-head">
        <span class="pos-ticker">{ticker}</span>
        <span class="tag {tag_class}">{tag_label}</span>
        <span class="pos-strat">{html.escape(strat_label)}</span>
        <span class="chip {chip_class}">{chip_label}</span>
        {dte_tag}
      </div>
      <div class="pos-meta">
        Entered <span class="mono">{open_date}</span> · spot <span class="mono">${entry_spot:.2f}</span>{meta_extra}
      </div>
    </div>
    <div>
      <div class="pos-pnl {pnl_cls}">{pnl_dollar}</div>
      <div class="pos-pnl-pct">{pnl_percent}</div>
    </div>
  </div>
  <div class="pos-legs">{legs_str}</div>{rationale_block}{progress_block}
</div>"""


def positions_tab(data: dict) -> str:
    """Positions tab: filter bar + legend chips + position cards."""
    positions = data.get("positions", [])
    open_count = sum(1 for p in positions if p.get("isOpen"))
    closed_count = len(positions) - open_count
    total_count = len(positions)

    filter_bar = f"""<div class="filter-bar">
  <span class="label-sm">Filter</span>
  <div class="segmented">
    <button class="active">All ({total_count})</button>
    <button>Open ({open_count})</button>
    <button>Closed ({closed_count})</button>
  </div>
  <div style="flex: 1"></div>
  <span class="legend"><span class="sw" style="background: var(--info)"></span>open</span>
  <span class="legend"><span class="sw" style="background: var(--pos)"></span>profit</span>
  <span class="legend"><span class="sw" style="background: var(--neg)"></span>loss</span>
</div>"""

    cards = "".join(_position_card(p) for p in positions)
    return f"{filter_bar}\n{cards}"
