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


# ---- Costs tab --------------------------------------------------------------

def costs_tab(data: dict) -> str:
    """Costs tab: LLM spend by ticker + commissions table + cost efficiency."""
    c = data["costs"]
    m = data["metrics"]

    llm_per_ticker = c.get("llmPerTicker", {})
    llm_total = c.get("llmTotal", 0.0)
    llm_budget = c.get("llmBudget", 0.0)
    paper_comm = c.get("paperCommissions", 0.0)
    backtest_comm = c.get("backtestCommissions", 0.0)
    paper_trade_count = m.get("paperTradeCount", 0)
    backtest_count = m.get("backtestCount", 0)

    # LLM bar rows sorted by spend descending
    sorted_entries = sorted(llm_per_ticker.items(), key=lambda kv: kv[1], reverse=True)
    max_val = max((v for _, v in sorted_entries), default=1.0) or 1.0

    bar_rows = []
    for ticker, v in sorted_entries:
        width_pct = (v / max_val) * 100
        bar_rows.append(
            f'<div class="bar-row">'
            f'<span class="bar-label">{html.escape(ticker)}</span>'
            f'<div class="bar-track"><div class="bar-fill" style="width: {width_pct:.1f}%"></div></div>'
            f'<span class="num bar-amt">${v:.2f}</span>'
            f'</div>'
        )
    bars_html = "".join(bar_rows) if bar_rows else ""

    # Commissions totals
    total_comm = paper_comm + backtest_comm

    # Cost efficiency — guard against division by zero
    if paper_trade_count:
        per_paper = f"${llm_total / paper_trade_count:.2f}"
    else:
        per_paper = "&mdash;"

    if backtest_count:
        per_backtest = f"${llm_total / backtest_count:.3f}"
    else:
        per_backtest = "&mdash;"

    return f"""<div class="cols-2-eq">
  <div class="card">
    <div class="card-head">
      <span class="card-title">LLM Spend by Ticker</span>
      <span class="num" style="font-size: 12px">${llm_total:.2f} <span style="color: var(--fg-2)">/ ${llm_budget:.2f}</span></span>
    </div>
    <div class="card-body">
      {bars_html}
    </div>
  </div>
  <div class="card">
    <div class="card-head">
      <span class="card-title">Commissions</span>
    </div>
    <div class="card-body">
      <table>
        <tbody>
          <tr><td>Paper trading</td><td class="num t-right">${paper_comm:.2f}</td></tr>
          <tr><td class="muted">Backtest (cumulative)</td><td class="num t-right muted">${backtest_comm:,.2f}</td></tr>
          <tr style="font-weight: 600">
            <td style="border-top: 1px solid var(--line-strong)">Total</td>
            <td class="num t-right" style="border-top: 1px solid var(--line-strong)">${total_comm:,.2f}</td>
          </tr>
        </tbody>
      </table>
      <div style="margin-top: 18px">
        <div class="subhead" style="margin: 0">Cost Efficiency</div>
        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin-top: 10px">
          <div>
            <div style="font-size: 10.5px; color: var(--fg-2); text-transform: uppercase; letter-spacing: 0.08em">$ per paper trade</div>
            <div class="num" style="font-size: 18px">{per_paper}</div>
          </div>
          <div>
            <div style="font-size: 10.5px; color: var(--fg-2); text-transform: uppercase; letter-spacing: 0.08em">$ per backtest</div>
            <div class="num" style="font-size: 18px">{per_backtest}</div>
          </div>
        </div>
      </div>
    </div>
  </div>
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


# ---- Leaderboard tab --------------------------------------------------------

def leaderboard_tab(data: dict) -> str:
    """Leaderboard tab: ranked strategy list sorted by score_a (annualized return).

    Reads ``data['leaderboard']`` — a list of dicts produced by
    ``queries.leaderboard_entries``. Renders as a single table card so the
    static-HTML dashboard surfaces the top strategies discovered by the
    search engine. No client-side sorting (this is static HTML); rows arrive
    pre-sorted by ``score_a`` descending.
    """
    entries = data.get("leaderboard", [])

    if not entries:
        return ('<div class="card"><div class="card-body" '
                'style="color: var(--fg-2); font-size: 12px; padding: 14px">'
                'No entries yet &mdash; search engine warming up.'
                '</div></div>')

    def _row(e: dict) -> str:
        ticker = html.escape(str(e.get("ticker", "")))
        class_name = html.escape(str(e.get("class_name", "")))
        regime = e.get("regime_label")
        regime_cell = (html.escape(str(regime))
                       if regime else '<span class="muted">&mdash;</span>')
        score_a = e.get("score_a", 0.0) or 0.0
        size_units = e.get("size_units", 0) or 0
        max_loss = e.get("max_loss_per_trade", 0.0) or 0.0
        trade_count = e.get("trade_count", 0) or 0
        rank = e.get("rank", 0) or 0
        proposal_id = e.get("proposal_id", "")

        # Green threshold = 100% annualized return on max-BP-held. Aggressive
        # by design: this is the "deploy this" cutoff per the search-engine
        # spec, not "merely profitable." Adjust if Dan re-tunes the bar.
        score_cls = "pos" if score_a >= 1.0 else "neg"

        return f"""<tr>
  <td class="num t-right">{rank}</td>
  <td><strong>{ticker}</strong></td>
  <td><span class="mono" style="font-size: 11.5px">{class_name}</span></td>
  <td>{regime_cell}</td>
  <td class="num t-right {score_cls}">{score_a:.0%}</td>
  <td class="num t-right">{size_units}</td>
  <td class="num t-right">{fmt_money(max_loss, decimals=0)}</td>
  <td class="num t-right">{trade_count}</td>
  <td class="num muted" style="font-size: 11px">ep_{html.escape(str(proposal_id))}</td>
</tr>"""

    rows = "".join(_row(e) for e in entries)

    return f"""<div class="card">
  <table>
    <thead>
      <tr>
        <th class="t-right">Rank</th>
        <th>Ticker</th>
        <th>Strategy</th>
        <th>Regime</th>
        <th class="t-right">Score A (ann.)</th>
        <th class="t-right">Size</th>
        <th class="t-right">Max Loss</th>
        <th class="t-right">Trades</th>
        <th>Proposal</th>
      </tr>
    </thead>
    <tbody>
      {rows}
    </tbody>
  </table>
</div>"""


# ---- v2 Signals tab ---------------------------------------------------------

def v2_signals_tab(data: dict) -> str:
    """V2 Signals tab: latest directional signal per ticker from the rules-based agent.

    Reads ``data['v2_signals']`` — a list of dicts produced by
    ``queries.v2_signals``. Renders as a single table card with one row per
    ticker, color-coded by direction. Empty state when no signals exist yet.
    """
    entries = data.get("v2_signals", [])
    if not entries:
        return ('<div class="card"><div class="card-body" '
                'style="color: var(--fg-2); font-size: 12px; padding: 14px">'
                'No v2 signals yet &mdash; run-v2-daily has not fired.'
                '</div></div>')

    def _row(e: dict) -> str:
        ticker = html.escape(str(e.get("ticker", "")))
        direction = html.escape(str(e.get("direction", "")))
        rationale = html.escape(str(e.get("rationale", "")))
        confidence = e.get("confidence", 0.0) or 0.0
        horizon = e.get("horizon_days", 0) or 0
        asof_ts = e.get("asof_ts", 0) or 0
        from datetime import datetime, timezone
        asof = (datetime.fromtimestamp(asof_ts, tz=timezone.utc).strftime("%Y-%m-%d")
                if asof_ts else "—")

        dir_cls = {
            "bullish": "pos",
            "bearish": "neg",
            "chop": "muted",
            "no_edge": "muted",
        }.get(direction, "muted")

        open_direction = e.get("open_direction")
        open_shares = e.get("open_shares", 0.0) or 0.0
        open_entry = e.get("open_entry", 0.0) or 0.0
        current_price = e.get("current_price", 0.0) or 0.0
        unrealized_pnl = e.get("unrealized_pnl", 0.0) or 0.0
        realized_pnl = e.get("realized_pnl", 0.0) or 0.0
        if open_direction:
            position_cell = (
                f'<span class="{"pos" if open_direction == "long" else "neg"}">'
                f'{html.escape(open_direction)} {open_shares:.0f}@${open_entry:.2f}</span>'
            )
            current_cell = f"${current_price:.2f}" if current_price > 0 else "—"
            unr_cls = "pos" if unrealized_pnl > 0 else ("neg" if unrealized_pnl < 0 else "muted")
            unr_cell = f'<span class="{unr_cls}">${unrealized_pnl:+,.2f}</span>'
        else:
            position_cell = '<span class="muted">—</span>'
            current_cell = '<span class="muted">—</span>'
            unr_cell = '<span class="muted">—</span>'
        pnl_cls = "pos" if realized_pnl > 0 else ("neg" if realized_pnl < 0 else "muted")
        pnl_cell = f'<span class="{pnl_cls}">${realized_pnl:+,.2f}</span>'

        return f"""<tr>
  <td><strong>{ticker}</strong></td>
  <td class="{dir_cls}"><strong>{direction}</strong></td>
  <td class="num t-right">{confidence:.2f}</td>
  <td class="num t-right">{horizon}d</td>
  <td>{position_cell}</td>
  <td class="num t-right">{current_cell}</td>
  <td class="num t-right">{unr_cell}</td>
  <td class="num t-right">{pnl_cell}</td>
  <td style="font-size: 11.5px">{rationale}</td>
  <td class="muted" style="font-size: 11.5px">{asof}</td>
</tr>"""

    rows = "".join(_row(e) for e in entries)
    return f"""<div class="card">
  <table>
    <thead>
      <tr>
        <th>Ticker</th>
        <th>Signal</th>
        <th class="t-right">Confidence</th>
        <th class="t-right">Horizon</th>
        <th>Position</th>
        <th class="t-right">Current</th>
        <th class="t-right">Unrealized</th>
        <th class="t-right">Realized PnL</th>
        <th>Rationale</th>
        <th>As of</th>
      </tr>
    </thead>
    <tbody>
      {rows}
    </tbody>
  </table>
</div>"""


# ---- Inventory tab ----------------------------------------------------------

def inventory_tab(data: dict) -> str:
    """Inventory tab: single card with a table of all held positions."""
    inventory = data.get("inventory", [])

    _KIND_LABEL = {"S": "shares", "C": "call", "P": "put"}

    def _row(r: dict) -> str:
        account = html.escape(str(r.get("account", "")))
        ticker = html.escape(str(r.get("ticker", "")))
        kind = r.get("kind", "")
        kind_label = _KIND_LABEL.get(kind, html.escape(kind))
        strike = r.get("strike")
        strike_html = f"${strike}" if strike else "&mdash;"
        expiry = r.get("expiry", "") or ""
        expiry_html = html.escape(expiry) if expiry else "&mdash;"
        qty = r.get("qty", "")
        cost_basis = fmt_money(r.get("costBasis"), decimals=2)
        return f"""<tr>
  <td><span class="tag {account}">{account}</span></td>
  <td><strong>{ticker}</strong></td>
  <td>{kind_label}</td>
  <td class="num t-right">{strike_html}</td>
  <td class="num mono">{expiry_html}</td>
  <td class="num t-right">{qty}</td>
  <td class="num t-right">{cost_basis}</td>
</tr>"""

    rows = "".join(_row(r) for r in inventory)

    return f"""<div class="card">
  <table>
    <thead>
      <tr>
        <th>Account</th>
        <th>Ticker</th>
        <th>Type</th>
        <th class="t-right">Strike</th>
        <th>Expiry</th>
        <th class="t-right">Qty</th>
        <th class="t-right">Cost Basis</th>
      </tr>
    </thead>
    <tbody>
      {rows}
    </tbody>
  </table>
</div>"""


# ---- v2 Positions tab -------------------------------------------------------

def v2_positions_tab(data: dict) -> str:
    """V2 Positions tab: currently-open Phase C positions with MtM + exit plan.

    Reads ``data['v2_positions']`` from queries.v2_positions. Renders an
    empty-state card when no positions are open."""
    entries = data.get("v2_positions", [])
    if not entries:
        return ('<div class="card"><div class="card-body" '
                'style="color: var(--fg-2); font-size: 12px; padding: 14px">'
                'No open positions (v2).'
                '</div></div>')

    def _row(p: dict) -> str:
        ticker = html.escape(str(p.get("ticker", "")))
        intent = html.escape(str(p.get("intent", "")))
        structure = html.escape(str(p.get("structure_kind", "")))
        legs = html.escape(str(p.get("legs_summary", "")))
        opened = html.escape(str(p.get("opened_date", "")))
        days = p.get("days_held", 0)
        target = p.get("profit_target_price")
        stop = p.get("stop_price")
        tsd = p.get("time_stop_dte")
        mtm = p.get("latest_mtm_value")
        mtm_src = p.get("latest_mtm_source")
        mtm_asof = p.get("latest_mtm_asof_date")
        rationale = html.escape(str(p.get("rationale", "")))

        target_cell = f"${target:.2f}" if target is not None else "—"
        stop_cell = f"${stop:.2f}" if stop is not None else "—"
        tsd_cell = f"{tsd}d" if tsd is not None else "—"
        if mtm is not None:
            mtm_cls = "pos" if mtm > 0 else ("neg" if mtm < 0 else "muted")
            mtm_cell = (f'<span class="{mtm_cls}">${mtm:+,.2f}</span>'
                        f' <span class="muted" style="font-size:10.5px">'
                        f'({html.escape(str(mtm_src))} @ {html.escape(str(mtm_asof))})</span>')
        else:
            mtm_cell = '<span class="muted">—</span>'

        return f"""<tr>
  <td><strong>{ticker}</strong></td>
  <td>{intent}</td>
  <td>{structure}</td>
  <td style="font-size:11.5px">{legs}</td>
  <td>{opened}</td>
  <td class="num t-right">{days}d</td>
  <td class="num t-right">{target_cell}</td>
  <td class="num t-right">{stop_cell}</td>
  <td class="num t-right">{tsd_cell}</td>
  <td class="num t-right">{mtm_cell}</td>
  <td style="font-size:11.5px">{rationale}</td>
</tr>"""

    rows = "".join(_row(p) for p in entries)
    return f"""<div class="card">
  <table>
    <thead>
      <tr>
        <th>Ticker</th><th>Intent</th><th>Structure</th><th>Legs</th>
        <th>Opened</th><th class="t-right">Days</th>
        <th class="t-right">Target</th><th class="t-right">Stop</th>
        <th class="t-right">Time Stop</th><th class="t-right">MtM</th>
        <th>Rationale</th>
      </tr>
    </thead>
    <tbody>
      {rows}
    </tbody>
  </table>
</div>"""


# ---- v2 Backtest tab --------------------------------------------------------

def v2_backtest_tab(data: dict) -> str:
    """V2 Backtest tab: latest backtest report from disk (equity curve + attribution)."""
    report = data.get("v2_backtest")
    if not report:
        return ('<div class="card"><div class="card-body" '
                'style="color: var(--fg-2); font-size: 12px; padding: 14px">'
                'No backtest report yet — run bullbot.v2.backtest.runner.backtest '
                'and write_report to populate.'
                '</div></div>')

    dir_name = html.escape(str(report.get("dir_name", "")))
    modified_ts = report.get("modified_ts", 0) or 0
    from datetime import datetime as _dt
    modified_date = (_dt.fromtimestamp(modified_ts).strftime("%Y-%m-%d %H:%M")
                     if modified_ts else "—")
    equity = report.get("equity_curve", [])
    attr = report.get("attribution", [])

    attr_rows = "".join(
        f"""<tr>
  <td><strong>{html.escape(str(a.get('structure_kind', '')))}</strong></td>
  <td class="num t-right">{html.escape(str(a.get('trade_count', '')))}</td>
  <td class="num t-right">{html.escape(str(a.get('wins', '')))}</td>
  <td class="num t-right">{html.escape(str(a.get('losses', '')))}</td>
  <td class="num t-right">{html.escape(str(a.get('win_rate', '')))}</td>
  <td class="num t-right">${html.escape(str(a.get('total_pnl', '')))}</td>
  <td class="num t-right">${html.escape(str(a.get('avg_pnl', '')))}</td>
</tr>"""
        for a in attr
    )
    last_30_equity = equity[-30:]
    eq_rows = "".join(
        f"""<tr>
  <td>{html.escape(str(e.get('asof_date', '')))}</td>
  <td class="num t-right">${html.escape(str(e.get('nav', '')))}</td>
</tr>"""
        for e in last_30_equity
    )

    return f"""<div class="card">
  <div class="card-body" style="padding:12px 16px; font-size:12px; color:var(--fg-2)">
    <strong>{dir_name}</strong> &mdash; last updated {modified_date}
  </div>
  <h3 style="margin:10px 16px 4px; font-size:13px">Per-vehicle attribution</h3>
  <table>
    <thead>
      <tr>
        <th>Structure</th><th class="t-right">Trades</th>
        <th class="t-right">Wins</th><th class="t-right">Losses</th>
        <th class="t-right">Win rate</th>
        <th class="t-right">Total $</th><th class="t-right">Avg $</th>
      </tr>
    </thead>
    <tbody>{attr_rows}</tbody>
  </table>
  <h3 style="margin:14px 16px 4px; font-size:13px">Equity curve (last 30 days)</h3>
  <table>
    <thead><tr><th>Date</th><th class="t-right">NAV</th></tr></thead>
    <tbody>{eq_rows}</tbody>
  </table>
</div>"""
