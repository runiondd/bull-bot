"""HTML template functions for the Bull-Bot dashboard.

Each function takes pre-queried data and returns an HTML string fragment.
"""

from __future__ import annotations

import html
from datetime import datetime, timezone
from itertools import groupby
from operator import itemgetter

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt_pnl(val: float | None) -> str:
    """Return colored PnL string: +$X.XX green, -$X.XX red, $0.00 neutral."""
    if val is None or val == 0:
        return '<span style="color:#e0e0e0">$0.00</span>'
    if val > 0:
        return f'<span style="color:#53d769">+${val:,.2f}</span>'
    return f'<span style="color:#ff6b6b">-${abs(val):,.2f}</span>'


def _fmt_ts(epoch: float | int | None) -> str:
    """Format a unix epoch as a readable date string."""
    if epoch is None:
        return "—"
    dt = datetime.fromtimestamp(epoch, tz=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M UTC")


def _abbreviate_legs(legs: list[dict]) -> str:
    """Return human-readable leg summary like 'Short 1x 500P May-15 / Long 1x 490P May-15'."""
    parts = []
    for leg in legs:
        side = "Long" if leg.get("side") == "long" else "Short"
        qty = leg.get("quantity", 1)
        strike = leg.get("strike", 0)
        kind = leg.get("kind", "?")
        expiry = leg.get("expiry", "")
        if expiry:
            try:
                dt = datetime.strptime(expiry, "%Y-%m-%d")
                expiry_str = dt.strftime("%b-%d")
            except ValueError:
                expiry_str = expiry
        else:
            expiry_str = "?"
        strike_str = f"{strike:g}" if strike == int(strike) else f"{strike:.2f}"
        parts.append(f"{side} {qty}x {strike_str}{kind} {expiry_str}")
    return " / ".join(parts)


def _format_exit_rules(rules: dict) -> str:
    """Return human-readable exit rules like 'Close at 50% profit, 2x stop, or 8 DTE'."""
    if not rules:
        return ""
    parts = []
    pt = rules.get("profit_target_pct")
    if pt is not None:
        parts.append(f"{pt:.0%} profit target")
    sl = rules.get("stop_loss_mult")
    if sl is not None:
        parts.append(f"{sl}x stop loss")
    dte = rules.get("min_dte_close")
    if dte is not None:
        parts.append(f"close at {dte} DTE")
    return "Exit: " + ", ".join(parts) if parts else ""


def _phase_color(phase: str) -> str:
    """Return CSS color for a pipeline phase."""
    return {
        "paper_trial": "#53d769",
        "discovering": "#ffa500",
        "no_edge": "#ff6b6b",
        "live": "#4cc9f0",
    }.get(phase, "#e0e0e0")


# ---------------------------------------------------------------------------
# 1. Page shell
# ---------------------------------------------------------------------------

def page_shell(updated_at: str, body: str) -> str:
    """Full <!DOCTYPE html> page wrapping *body* content."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Bull-Bot Dashboard</title>
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ background:#1a1a2e; color:#e0e0e0; font-family:'Courier New',monospace; font-size:14px; }}
  a {{ color:#4cc9f0; }}
  .header {{ display:flex; justify-content:space-between; align-items:center; padding:12px 24px; background:#0f3460; }}
  .header h1 {{ font-size:18px; color:#4cc9f0; }}
  .header .updated {{ font-size:12px; color:#888; }}
  .container {{ max-width:1400px; margin:0 auto; padding:16px; }}
  .tab-bar {{ display:flex; gap:4px; margin-bottom:16px; flex-wrap:wrap; }}
  .tab-btn {{ padding:8px 16px; background:#0f3460; color:#e0e0e0; border:1px solid #4cc9f0; cursor:pointer; font-family:inherit; font-size:13px; }}
  .tab-btn.active {{ background:#4cc9f0; color:#1a1a2e; }}
  .tab-content {{ display:none; }}
  .tab-content.active {{ display:block; }}
  .card {{ background:#0f3460; border-radius:6px; padding:16px; margin-bottom:12px; }}
  .summary-card {{ background:#0f3460; border-radius:6px; padding:16px; text-align:center; min-width:180px; }}
  .summary-card .label {{ font-size:11px; color:#888; text-transform:uppercase; margin-bottom:4px; }}
  .summary-card .value {{ font-size:22px; font-weight:bold; }}
  .summary-row {{ display:flex; gap:16px; flex-wrap:wrap; margin-bottom:16px; }}
  .metric-row {{ display:flex; gap:24px; flex-wrap:wrap; font-size:13px; margin:4px 0; }}
  .phase-badge {{ padding:2px 8px; border-radius:3px; font-size:11px; font-weight:bold; }}
  .pass-border {{ border-left:4px solid #53d769; }}
  .fail-border {{ border-left:4px solid #ff6b6b; }}
  .dimmed {{ opacity:0.6; }}
  .filter-btn {{ padding:4px 12px; background:#1a1a2e; color:#e0e0e0; border:1px solid #555; cursor:pointer; font-family:inherit; font-size:12px; margin:2px; }}
  .filter-btn.active {{ background:#4cc9f0; color:#1a1a2e; border-color:#4cc9f0; }}
  table {{ width:100%; border-collapse:collapse; }}
  th, td {{ padding:6px 10px; text-align:left; border-bottom:1px solid #222; font-size:13px; }}
  th {{ color:#888; font-size:11px; text-transform:uppercase; }}
  blockquote {{ border-left:3px solid #4cc9f0; padding:4px 12px; margin:8px 0; color:#aaa; font-style:italic; }}
</style>
</head>
<body>
<div class="header">
  <h1>Bull-Bot Dashboard</h1>
  <span class="updated">Updated {html.escape(updated_at)}</span>
</div>
<div class="container">
{body}
</div>
<script>
function switchTab(tabName) {{
  document.querySelectorAll('.tab-content').forEach(function(el) {{ el.style.display = 'none'; }});
  document.querySelectorAll('.tab-btn').forEach(function(el) {{ el.classList.remove('active'); }});
  var tab = document.getElementById('tab-' + tabName);
  if (tab) tab.style.display = 'block';
  document.querySelectorAll('.tab-btn').forEach(function(btn) {{
    if (btn.textContent.trim() === tabName) btn.classList.add('active');
  }});
}}
function filterTicker(ticker) {{
  document.querySelectorAll('[data-ticker]').forEach(function(el) {{
    el.style.display = (el.getAttribute('data-ticker') === ticker) ? '' : 'none';
  }});
}}
function clearFilter() {{
  document.querySelectorAll('[data-ticker]').forEach(function(el) {{
    el.style.display = '';
  }});
}}
function toggleFilter(type) {{
  document.querySelectorAll('.filter-btn').forEach(function(btn) {{
    btn.classList.remove('active');
  }});
  event.target.classList.add('active');
  document.querySelectorAll('[data-filter-target]').forEach(function(el) {{
    if (type === 'all') {{ el.style.display = ''; return; }}
    if (type === 'open') {{ el.style.display = el.getAttribute('data-open') === 'true' ? '' : 'none'; return; }}
    if (type === 'closed') {{ el.style.display = el.getAttribute('data-open') === 'false' ? '' : 'none'; return; }}
    if (type === 'paper') {{ el.style.display = el.getAttribute('data-backtest') === 'false' ? '' : 'none'; return; }}
    if (type === 'backtest') {{ el.style.display = el.getAttribute('data-backtest') === 'true' ? '' : 'none'; return; }}
  }});
}}
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# 2. Summary cards
# ---------------------------------------------------------------------------

def summary_cards(metrics: dict) -> str:
    total_equity = 265_000
    open_pos = metrics.get("open_positions", 0)
    pnl = metrics.get("paper_pnl", 0.0)
    llm = metrics.get("llm_spend", 0.0)

    pnl_html = _fmt_pnl(pnl)

    pnl_breakdown = ""
    pnl_by_ticker = metrics.get("pnl_by_ticker", [])
    if pnl_by_ticker:
        rows = []
        for t in pnl_by_ticker:
            total = t["realized"] + t["unrealized"]
            if total == 0:
                continue
            rows.append(
                f'<span style="margin-right:12px">{t["ticker"]}: {_fmt_pnl(total)}</span>'
            )
        if rows:
            pnl_breakdown = f'<div style="font-size:11px;margin-top:4px">{"".join(rows)}</div>'

    return f"""<div class="summary-row">
  <div class="summary-card">
    <div class="label">Total Equity</div>
    <div class="value">${total_equity:,}</div>
  </div>
  <div class="summary-card">
    <div class="label">Open Positions</div>
    <div class="value">{open_pos}</div>
  </div>
  <div class="summary-card">
    <div class="label">Paper P&amp;L</div>
    <div class="value">{pnl_html}</div>{pnl_breakdown}
  </div>
  <div class="summary-card">
    <div class="label">LLM Spend</div>
    <div class="value">${llm:,.2f}</div>
  </div>
</div>"""


# ---------------------------------------------------------------------------
# 3. Ticker grid
# ---------------------------------------------------------------------------

def ticker_grid(rows: list[dict]) -> str:
    lines = [
        '<table>',
        '<thead><tr><th>Ticker</th><th>Phase</th><th>Strategy</th><th>Paper Trades</th></tr></thead>',
        '<tbody>',
    ]
    for r in rows:
        ticker = html.escape(r["ticker"])
        phase = html.escape(r["phase"])
        color = _phase_color(r["phase"])
        strategy = html.escape(r.get("strategy") or "—")
        trades = r.get("paper_trade_count", 0)
        lines.append(
            f'<tr data-ticker="{ticker}" onclick="filterTicker(\'{ticker}\')" style="cursor:pointer">'
            f'<td><strong>{ticker}</strong></td>'
            f'<td><span class="phase-badge" style="background:{color};color:#1a1a2e">{phase}</span></td>'
            f'<td>{strategy}</td>'
            f'<td>{trades}</td>'
            f'</tr>'
        )
    lines.append('</tbody></table>')
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 4. Activity feed
# ---------------------------------------------------------------------------

def activity_feed(events: list[dict]) -> str:
    if not events:
        return '<div class="card">No recent activity.</div>'
    lines = ['<div class="card">']
    for e in events:
        ts = _fmt_ts(e.get("timestamp"))
        desc = html.escape(e.get("description", ""))
        ticker = html.escape(e.get("ticker", ""))
        lines.append(
            f'<div data-ticker="{ticker}" style="margin:4px 0">'
            f'<span style="color:#888">{ts}</span> {desc}'
            f'</div>'
        )
    lines.append('</div>')
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 5. Evolver section
# ---------------------------------------------------------------------------

def evolver_section(proposals: list[dict]) -> str:
    if not proposals:
        return '<div class="card">No evolver proposals.</div>'

    sorted_props = sorted(proposals, key=itemgetter("ticker"))
    lines = []
    for ticker, group in groupby(sorted_props, key=itemgetter("ticker")):
        lines.append(f'<h3 style="margin:12px 0 8px;color:#4cc9f0">{html.escape(ticker)}</h3>')
        for p in group:
            passed = p.get("passed_gate", False)
            border_class = "pass-border" if passed else "fail-border"
            dim = "" if passed else ' style="opacity:0.6"'
            badge_color = "#53d769" if passed else "#ff6b6b"
            badge_text = "PASS" if passed else "FAIL"

            class_name = html.escape(p.get("class_name", ""))
            rationale = html.escape(p.get("rationale", ""))
            pf_is = p.get("pf_is") or 0
            pf_oos = p.get("pf_oos") or 0
            pf_is_str = "inf" if pf_is == float("inf") else f"{pf_is:.2f}"
            pf_oos_str = "inf" if pf_oos == float("inf") else f"{pf_oos:.2f}"
            max_dd = p.get("max_dd_pct") or 0
            max_dd_str = f"{max_dd:.1%}"
            trade_count = p.get("trade_count", 0)
            params = p.get("params", {})
            params_str = ", ".join(f"{k}={v}" for k, v in params.items()) if params else "—"
            llm_cost = p.get("llm_cost_usd", 0)
            created = _fmt_ts(p.get("created_at"))

            lines.append(
                f'<div class="card {border_class}" data-ticker="{html.escape(p["ticker"])}"{dim}>'
                f'<div style="display:flex;justify-content:space-between;align-items:center">'
                f'<strong>{class_name}</strong>'
                f'<span class="phase-badge" style="background:{badge_color};color:#1a1a2e">{badge_text}</span>'
                f'</div>'
                f'<div class="metric-row">'
                f'<span>Trades: {trade_count}</span>'
                f'<span>PF OOS: {pf_oos_str}</span>'
                f'<span>PF IS: {pf_is_str}</span>'
                f'<span>Max DD: {max_dd_str}</span>'
                f'<span>LLM: ${llm_cost:.2f}</span>'
                f'</div>'
                f'<div style="margin:4px 0;font-size:12px;color:#888">Params: {html.escape(params_str)}</div>'
                f'<blockquote>{rationale}</blockquote>'
                f'<div style="font-size:11px;color:#666">{created}</div>'
                f'</div>'
            )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 6. Positions section
# ---------------------------------------------------------------------------

def positions_section(positions: list[dict]) -> str:
    lines = [
        '<div style="margin-bottom:8px">',
        '<button class="filter-btn" onclick="toggleFilter(\'all\')">All</button>',
        '<button class="filter-btn" onclick="toggleFilter(\'open\')">Open</button>',
        '<button class="filter-btn" onclick="toggleFilter(\'closed\')">Closed</button>',
        '<button class="filter-btn active" onclick="toggleFilter(\'paper\')">Paper</button>',
        '<button class="filter-btn" onclick="toggleFilter(\'backtest\')">Backtest</button>',
        '</div>',
    ]
    for pos in positions:
        is_open = pos.get("is_open", True)
        is_bt = pos.get("is_backtest", False)
        ticker = html.escape(pos.get("ticker", ""))
        class_name = html.escape(pos.get("class_name", ""))

        if is_open:
            border = "border-left:4px solid #4cc9f0"
            badge = '<span class="phase-badge" style="background:#4cc9f0;color:#1a1a2e">OPEN</span>'
            dim = ""
        else:
            pnl_val = pos.get("pnl_realized", 0) or 0
            border_color = "#53d769" if pnl_val >= 0 else "#ff6b6b"
            border = f"border-left:4px solid {border_color}"
            badge = '<span class="phase-badge" style="background:#888;color:#1a1a2e">CLOSED</span>'
            dim = " dimmed"

        open_price = pos.get("open_price")
        mark = pos.get("mark_to_mkt")
        pnl_realized = pos.get("pnl_realized")
        exit_rules = pos.get("exit_rules", {})
        legs = pos.get("legs", [])
        legs_html = _abbreviate_legs(legs)

        hide = "display:none;" if is_bt else ""
        lines.append(
            f'<div class="card{dim}" style="{hide}{border}" '
            f'data-ticker="{ticker}" data-open="{str(is_open).lower()}" '
            f'data-backtest="{str(is_bt).lower()}" data-filter-target>'
            f'<div style="display:flex;justify-content:space-between;align-items:center">'
            f'<strong>{ticker} — {class_name}</strong>'
            f'{badge}'
            f'</div>'
            f'<div class="metric-row">'
            f'<span>Open: ${open_price:,.2f}</span>' if open_price is not None else ''
        )
        if mark is not None:
            lines.append(f'<span>Mark: ${mark:,.2f}</span>')
        if pnl_realized is not None:
            lines.append(f'<span>P&L: {_fmt_pnl(pnl_realized)}</span>')
        lines.append('</div>')
        rules_html = _format_exit_rules(exit_rules)
        if rules_html:
            lines.append(f'<div style="font-size:12px;color:#888">{html.escape(rules_html)}</div>')
        lines.append(f'<div style="font-size:12px;color:#aaa;margin-top:4px">{legs_html}</div>')
        lines.append('</div>')

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 7. Transactions section
# ---------------------------------------------------------------------------

def transactions_section(orders: list[dict]) -> str:
    lines = [
        '<div style="margin-bottom:8px">',
        '<button class="filter-btn" onclick="toggleFilter(\'all\')">All</button>',
        '<button class="filter-btn active" onclick="toggleFilter(\'paper\')">Paper</button>',
        '<button class="filter-btn" onclick="toggleFilter(\'backtest\')">Backtest</button>',
        '</div>',
        '<table>',
        '<thead><tr><th>Date</th><th>Ticker</th><th>Intent</th><th>Status</th><th>Legs</th><th>P&L</th><th>Commission</th></tr></thead>',
        '<tbody>',
    ]
    total_pnl = 0.0
    total_comm = 0.0
    for o in orders:
        ticker = html.escape(o.get("ticker", ""))
        intent = html.escape(o.get("intent", ""))
        status = html.escape(o.get("status", ""))
        legs = _abbreviate_legs(o.get("legs", []))
        pnl = o.get("pnl")
        comm = o.get("commission", 0) or 0
        is_bt = o.get("is_backtest", False)
        placed = _fmt_ts(o.get("placed_at"))
        pnl_html = _fmt_pnl(pnl) if pnl is not None else "—"
        if pnl:
            total_pnl += pnl
        total_comm += comm

        hide = ' style="display:none"' if is_bt else ""
        lines.append(
            f'<tr data-ticker="{ticker}" data-backtest="{str(is_bt).lower()}" data-filter-target{hide}>'
            f'<td>{placed}</td>'
            f'<td>{ticker}</td>'
            f'<td>{intent}</td>'
            f'<td>{status}</td>'
            f'<td style="font-size:11px">{html.escape(legs)}</td>'
            f'<td>{pnl_html}</td>'
            f'<td>${comm:,.2f}</td>'
            f'</tr>'
        )
    lines.append('</tbody>')
    lines.append(
        f'<tfoot><tr style="font-weight:bold">'
        f'<td colspan="5">Totals</td>'
        f'<td>{_fmt_pnl(total_pnl)}</td>'
        f'<td>${total_comm:,.2f}</td>'
        f'</tr></tfoot>'
    )
    lines.append('</table>')
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 8. Costs section
# ---------------------------------------------------------------------------

def costs_section(costs: dict) -> str:
    llm_per_ticker = costs.get("llm_per_ticker", {})
    llm_total = costs.get("llm_ledger_total", 0)
    paper_comm = costs.get("paper_commissions", 0)
    bt_comm = costs.get("backtest_commissions", 0)

    lines = [
        '<h3 style="color:#4cc9f0;margin-bottom:8px">LLM Costs</h3>',
        '<table>',
        '<thead><tr><th>Ticker</th><th>Cost</th></tr></thead>',
        '<tbody>',
    ]
    for ticker, cost in sorted(llm_per_ticker.items()):
        lines.append(f'<tr><td>{html.escape(ticker)}</td><td>${cost:,.2f}</td></tr>')
    lines.append('</tbody>')
    lines.append(f'<tfoot><tr style="font-weight:bold"><td>Total</td><td>${llm_total:,.2f}</td></tr></tfoot>')
    lines.append('</table>')

    lines.append('<h3 style="color:#4cc9f0;margin:16px 0 8px">Commission Summary</h3>')
    lines.append('<table>')
    lines.append(f'<tr><td>Paper Commissions</td><td>${paper_comm:,.2f}</td></tr>')
    lines.append(f'<tr><td>Backtest Commissions</td><td>${bt_comm:,.2f}</td></tr>')
    lines.append(f'<tr style="font-weight:bold"><td>Total</td><td>${paper_comm + bt_comm:,.2f}</td></tr>')
    lines.append('</table>')

    return "\n".join(lines)
