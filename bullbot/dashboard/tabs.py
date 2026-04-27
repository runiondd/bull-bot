"""Tab render functions ported from dashboard/handoff/components-tabs.jsx.

Each function: data: dict -> str (HTML fragment, no <html>/<body> wrapper).
Pure functions over the data dict from queries; no DB access here.
"""
from __future__ import annotations

import html

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
