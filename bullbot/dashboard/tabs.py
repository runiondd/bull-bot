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


# ---- Evolver tab ------------------------------------------------------------

def evolver_tab(data: dict) -> str:
    """Evolver tab: filter bar + proposals table + proposal detail cards."""
    import re
    proposals = data.get("proposals", [])
    passed_count = sum(1 for p in proposals if p.get("passed"))
    rejected_count = len(proposals) - passed_count
    total_count = len(proposals)

    filter_bar = f"""<div class="filter-bar">
  <span class="label-sm">Filter</span>
  <div class="segmented">
    <button class="active">All ({total_count})</button>
    <button>Passed ({passed_count})</button>
    <button>Rejected ({rejected_count})</button>
  </div>
  <div style="flex: 1"></div>
  <span class="label-sm" style="color: var(--fg-3)">Gate: pf_oos &ge; 1.30 &middot; trades &ge; 5 &middot; pf_is &ge; 1.50</span>
</div>"""

    def _table_row(p: dict) -> str:
        created_at = p.get("createdAt", "")
        parts = created_at.split(" ")
        time_str = parts[1] if len(parts) > 1 else ""
        date_str = parts[0][5:] if parts else ""
        pf_oos = p.get("pf_oos", 0.0)
        pf_is = p.get("pf_is", 0.0)
        max_dd_pct = p.get("max_dd_pct", 0.0)
        trade_count = p.get("trade_count", 0)
        llm_cost = p.get("llm_cost", 0.0)
        passed = p.get("passed", False)
        pf_oos_cls = "pos" if pf_oos >= 1.3 else "neg"
        verdict_cls = "pass" if passed else "fail"
        verdict_label = "PASS" if passed else "FAIL"
        return f"""<tr>
  <td><strong>{html.escape(str(p.get('ticker', '')))}</strong></td>
  <td><span class="mono" style="font-size: 11.5px">{html.escape(str(p.get('className', '')))}</span></td>
  <td class="num">{p.get('iteration', '')}</td>
  <td class="num t-right {pf_oos_cls}">{pf_oos:.2f}</td>
  <td class="num t-right">{pf_is:.2f}</td>
  <td class="num t-right neg">{max_dd_pct * 100:.1f}%</td>
  <td class="num t-right">{trade_count}</td>
  <td class="num t-right muted">${llm_cost:.2f}</td>
  <td><span class="chip {verdict_cls}">{verdict_label}</span></td>
  <td class="num muted" style="font-size: 11px">{html.escape(time_str)} {html.escape(date_str)}</td>
</tr>"""

    rows = "".join(_table_row(p) for p in proposals)

    table = f"""<div class="card">
  <table>
    <thead>
      <tr>
        <th>Ticker</th>
        <th>Strategy</th>
        <th>Iter</th>
        <th class="t-right">PF OOS</th>
        <th class="t-right">PF IS</th>
        <th class="t-right">Max DD</th>
        <th class="t-right">Trades</th>
        <th class="t-right">LLM</th>
        <th>Verdict</th>
        <th>Created</th>
      </tr>
    </thead>
    <tbody>
      {rows}
    </tbody>
  </table>
</div>"""

    def _detail_card(p: dict) -> str:
        passed = p.get("passed", False)
        card_cls = "pos" if passed else "neg"
        verdict_cls = "pass" if passed else "fail"
        verdict_label = "PASS" if passed else "FAIL"
        class_name = p.get("className", "")
        strat_label = re.sub(r"([A-Z])", r" \1", class_name).strip()
        params = p.get("params") or {}
        params_str = ", ".join(f"{html.escape(str(k))}={html.escape(str(v))}" for k, v in params.items())
        rationale = html.escape(str(p.get("rationale", "")))
        return f"""<div class="position-card {card_cls}" style="display: block">
  <div class="pos-head" style="justify-content: space-between">
    <div class="pos-head" style="gap: 10px">
      <span class="pos-ticker">{html.escape(str(p.get('ticker', '')))}</span>
      <span class="pos-strat">{html.escape(strat_label)}</span>
      <span class="tag mono">iter {p.get('iteration', '')}</span>
      <span class="tag mono">{html.escape(str(p.get('id', '')))}</span>
    </div>
    <span class="chip {verdict_cls}">{verdict_label}</span>
  </div>
  <div class="pos-meta" style="margin-top: 6px">
    params: <span class="mono">{params_str}</span>
  </div>
  <div class="pos-rationale" style="margin-top: 8px">{rationale}</div>
</div>"""

    detail_cards = "".join(_detail_card(p) for p in proposals[:4])

    return f"""{filter_bar}
{table}
<div class="subhead">Proposal Detail</div>
{detail_cards}"""


# ---- Universe tab -----------------------------------------------------------

def universe_tab(data: dict) -> str:
    """Universe tab: single card with a table of all universe rows."""

    def _row(u: dict) -> str:
        ticker = html.escape(str(u.get("ticker", "")))
        category = html.escape(str(u.get("category", "")))
        phase = u.get("phase", "")
        strategy = u.get("strategy")
        edge = u.get("edge", {})
        pf_oos = edge.get("pf_oos", 0.0)
        pf_is = edge.get("pf_is", 0.0)
        dd = edge.get("dd", 0.0)
        iterations = u.get("iterations", 0)
        paper_trades = u.get("paperTrades", 0)

        pf_oos_cls = "pos" if pf_oos >= 1.3 else "neg"
        strategy_cell = (
            f'<span class="mono" style="font-size: 11.5px">{html.escape(str(strategy))}</span>'
            if strategy else
            '<span class="muted">—</span>'
        )

        return f"""<tr class="clickable">
  <td><strong>{ticker}</strong></td>
  <td><span class="tag {category}">{category}</span></td>
  <td><span class="chip {phase_class(phase)}">{phase_label(phase)}</span></td>
  <td>{strategy_cell}</td>
  <td class="num t-right {pf_oos_cls}">{pf_oos:.2f}</td>
  <td class="num t-right">{pf_is:.2f}</td>
  <td class="num t-right neg">{dd * 100:.1f}%</td>
  <td class="num t-right">{iterations}</td>
  <td class="num t-right">{paper_trades}</td>
</tr>"""

    rows = "".join(_row(u) for u in data.get("universe", []))

    return f"""<div class="card">
  <table>
    <thead>
      <tr>
        <th>Ticker</th>
        <th>Cat</th>
        <th>Phase</th>
        <th>Best Strategy</th>
        <th class="t-right">PF OOS</th>
        <th class="t-right">PF IS</th>
        <th class="t-right">Max DD</th>
        <th class="t-right">Iter</th>
        <th class="t-right">Paper Trades</th>
      </tr>
    </thead>
    <tbody>
      {rows}
    </tbody>
  </table>
</div>"""


# ---- Transactions tab -------------------------------------------------------

def transactions_tab(data: dict) -> str:
    """Transactions tab: single card with a table of orders and a totals footer."""
    orders = data.get("orders", [])
    total_pnl = sum(o.get("pnl") or 0 for o in orders)
    total_comm = sum(o.get("commission") or 0 for o in orders)

    def _order_row(o: dict) -> str:
        pnl = o.get("pnl")
        commission = o.get("commission") or 0
        intent = o.get("intent", "")
        chip_cls = "open" if intent == "open" else "closed"
        pnl_cell = (fmt_money(pnl, signed=True, decimals=2)
                    if pnl is not None else "&mdash;")
        pnl_cls = pnl_class(pnl)
        return f"""<tr>
  <td class="num" style="font-size: 11.5px; color: var(--fg-1)">{html.escape(str(o.get('date', '')))}</td>
  <td><strong>{html.escape(str(o.get('ticker', '')))}</strong></td>
  <td><span class="mono" style="font-size: 11.5px">{html.escape(str(o.get('className', '')))}</span></td>
  <td><span class="chip {chip_cls}">{html.escape(intent)}</span></td>
  <td class="mono" style="font-size: 11px; color: var(--fg-1)">{html.escape(str(o.get('legs', '')))}</td>
  <td class="num t-right {pnl_cls}">{pnl_cell}</td>
  <td class="num t-right muted">${commission:.2f}</td>
</tr>"""

    rows = "".join(_order_row(o) for o in orders)
    total_pnl_cls = pnl_class(total_pnl)
    total_pnl_str = fmt_money(total_pnl, signed=True, decimals=2)

    return f"""<div class="card">
  <table>
    <thead>
      <tr>
        <th>Date</th>
        <th>Ticker</th>
        <th>Strategy</th>
        <th>Intent</th>
        <th>Legs</th>
        <th class="t-right">P&amp;L</th>
        <th class="t-right">Comm</th>
      </tr>
    </thead>
    <tbody>
      {rows}
    </tbody>
    <tfoot>
      <tr style="font-weight: 600">
        <td colspan="5" class="t-right" style="color: var(--fg-2); text-transform: uppercase; font-size: 11px; letter-spacing: 0.08em">Totals</td>
        <td class="num t-right {total_pnl_cls}">{total_pnl_str}</td>
        <td class="num t-right">${total_comm:.2f}</td>
      </tr>
    </tfoot>
  </table>
</div>"""


# ---- Health tab -------------------------------------------------------------

def health_tab(data: dict) -> str:
    """Health tab: universe summary stats + system check cards."""
    h = data["health"]
    u = h["universe"]

    def _stat(label: str, value: int, subtext: str, num_extra_class: str = "") -> str:
        num_cls = f"num {num_extra_class}".strip() if num_extra_class else "num"
        return f"""<div>
  <div class="label-sm" style="color: var(--fg-2); font-size: 10px; letter-spacing: 0.08em; text-transform: uppercase">{html.escape(label)}</div>
  <div class="{num_cls}" style="font-size: 22px; margin-top: 4px">{value}</div>
  <div style="font-size: 11.5px; color: var(--fg-2)">{html.escape(subtext)}</div>
</div>"""

    universe_grid = f"""<div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 24px">
  {_stat("Universe", u["total"], "tickers tracked")}
  {_stat("Live", u["live"], "strategies in production", "pos")}
  {_stat("Paper Trial", u["paper_trial"], "under evaluation")}
  {_stat("No Edge", u["no_edge"], "retired this cycle", "muted")}
</div>"""

    def _check_card(c: dict) -> str:
        status = c.get("status", "")
        card_extra = " warn" if status == "warn" else (" fail" if status == "fail" else "")
        chip_cls = "pass" if status == "ok" else ("warn" if status == "warn" else "fail")
        return f"""<div class="health-card{card_extra}">
  <div class="h-name">
    <span>{html.escape(c.get("name", ""))}</span>
    <span class="chip {chip_cls}">{html.escape(status)}</span>
  </div>
  <div class="h-detail">{html.escape(c.get("detail", ""))}</div>
</div>"""

    checks_html = "".join(_check_card(c) for c in h.get("checks", []))

    return f"""<div class="card">
  <div class="card-body">
    {universe_grid}
  </div>
</div>
<div class="subhead">Checks</div>
<div class="health-grid">
  {checks_html}
</div>"""
