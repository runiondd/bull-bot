"""HTML template functions for the Bull-Bot dashboard.

Each function takes pre-queried data and returns an HTML string fragment.
"""

from __future__ import annotations

import html
from datetime import datetime, timezone
from itertools import groupby
from operator import itemgetter

from bullbot import config

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
    day = dt.day
    if 11 <= day <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return dt.strftime(f"%B {day}{suffix}, %Y")


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
                expiry_str = dt.strftime("%b-%d-%y")
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


def _category_badge(ticker: str) -> str:
    """Return a small 'income' or 'growth' badge for the ticker."""
    cat = config.TICKER_CATEGORY.get(ticker, "income")
    if cat == "growth":
        return '<span style="font-size:10px;padding:1px 5px;border-radius:2px;background:#2a1a4e;color:#b388ff;margin-left:6px">growth</span>'
    return '<span style="font-size:10px;padding:1px 5px;border-radius:2px;background:#1a2a3e;color:#4cc9f0;margin-left:6px">income</span>'


_STRATEGY_DESCRIPTION: dict[str, str] = {
    "PutCreditSpread": "Sell a put spread to collect premium — profits if the stock stays above the short strike.",
    "CallCreditSpread": "Sell a call spread to collect premium — profits if the stock stays below the short strike.",
    "IronCondor": "Sell both a put spread and a call spread — profits if the stock stays in a range.",
    "CashSecuredPut": "Sell a put backed by cash — profits if the stock stays above the strike, or you buy shares at a discount.",
    "GrowthLEAPS": "Buy a long-dated call option — profits if the stock rises significantly over time.",
    "BearPutSpread": "Buy a put spread — profits if the stock drops below the long strike.",
    "LongPut": "Buy a put option — profits if the stock drops.",
    "GrowthEquity": "Buy shares directly for long-term growth.",
}


def _strategy_description(class_name: str) -> str:
    return _STRATEGY_DESCRIPTION.get(class_name, "")


def _exit_reason_plain(exit_rules: dict, pnl: float | None) -> str:
    """Describe why a position closed in plain English."""
    if pnl is None:
        return "Position closed."
    if pnl > 0:
        pt = exit_rules.get("profit_target_pct")
        if pt is not None:
            return f"Closed at the {pt:.0%} profit target — the trade worked as expected."
        return "Closed profitably."
    else:
        sl = exit_rules.get("stop_loss_mult")
        dte = exit_rules.get("min_dte_close")
        if sl is not None and abs(pnl) > 0:
            return f"Hit the {sl}x stop loss — the trade moved against the thesis."
        if dte is not None:
            return f"Closed at {dte} days to expiration to avoid assignment risk."
        return "Closed at a loss."


def _closed_position_analysis(pos: dict) -> str:
    """Return HTML block analyzing a closed position's outcome."""
    pnl = pos.get("pnl_realized") or 0
    exit_rules = pos.get("exit_rules", {})
    reason = _exit_reason_plain(exit_rules, pnl)
    pnl_str = f"+${pnl:,.2f}" if pnl >= 0 else f"-${abs(pnl):,.2f}"
    color = "#53d769" if pnl >= 0 else "#ff6b6b"

    return (
        f'<div style="background:#1a1a2e;border-radius:4px;padding:8px;margin-top:6px;font-size:12px">'
        f'<strong style="color:{color}">Result: {pnl_str}</strong> — {html.escape(reason)}'
        f'</div>'
    )


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

def page_shell(generated_at: str, body: str) -> str:
    """Outer HTML document. Embeds the lifted CSS, IBM Plex font link, and
    minimal tab-switching JS. `body` is the assembled inner content (header,
    layout, sidebar, main, all the tab divs)."""
    from bullbot.dashboard import styles_css
    template = (
        '<!DOCTYPE html>\n'
        '<html lang="en" data-theme="dark" data-density="default" data-accent="green">\n'
        '<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=1280">\n'
        '<meta http-equiv="refresh" content="60">\n'
        '<title>Bull-Bot — Dashboard</title>\n'
        '<link rel="preconnect" href="https://fonts.googleapis.com">\n'
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n'
        '<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700'
        '&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">\n'
        '<style>\n'
        '{css}\n'
        '</style>\n'
        '</head>\n'
        '<body>\n'
        '{body}\n'
        '<script>\n'
        '(function() {\n'
        '  function showTab(name) {\n'
        '    document.querySelectorAll(\'.nav-item\').forEach(function(el) {\n'
        '      el.classList.toggle(\'active\', el.dataset.tab === name);\n'
        '    });\n'
        '    document.querySelectorAll(\'.tab-content\').forEach(function(el) {\n'
        '      el.style.display = (el.id === \'tab-\' + name) ? \'block\' : \'none\';\n'
        '    });\n'
        '  }\n'
        '  document.querySelectorAll(\'.nav-item\').forEach(function(el) {\n'
        '    el.addEventListener(\'click\', function() { showTab(el.dataset.tab); });\n'
        '  });\n'
        '})();\n'
        '</script>\n'
        '</body>\n'
        '</html>'
    )
    return template.replace('{css}', styles_css.CSS).replace('{body}', body)


# ---------------------------------------------------------------------------
# 2. Summary cards
# ---------------------------------------------------------------------------

def summary_cards(metrics: dict) -> str:
    total_equity = 265_000
    open_pos = metrics.get("open_positions", 0)
    # Prefer the split metrics; fall back to combined paper_pnl for legacy callers.
    realized = metrics.get("realized_pnl", 0.0)
    unrealized = metrics.get("unrealized_pnl", 0.0)
    if "realized_pnl" not in metrics and "unrealized_pnl" not in metrics:
        # Legacy single-metric fallback
        realized = metrics.get("paper_pnl", 0.0)
    llm = metrics.get("llm_spend", 0.0)

    realized_html = _fmt_pnl(realized)
    unrealized_html = _fmt_pnl(unrealized)

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
    <div class="label">Realized P&amp;L</div>
    <div class="value">{realized_html}</div>
  </div>
  <div class="summary-card">
    <div class="label">Unrealized P&amp;L</div>
    <div class="value">{unrealized_html}</div>{pnl_breakdown}
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
            f'<span style="color:#b0b0b0">{ts}</span> {desc}'
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
                f'<div style="margin:4px 0;font-size:12px;color:#b0b0b0">Params: {html.escape(params_str)}</div>'
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
        entry_date = _fmt_ts(pos.get("opened_at"))
        entry_spot = pos.get("entry_spot")
        entry_spot_str = f"${entry_spot:,.2f}" if entry_spot else "—"

        hide = "display:none;" if is_bt else ""
        lines.append(
            f'<div class="card{dim}" style="{hide}{border}" '
            f'data-ticker="{ticker}" data-open="{str(is_open).lower()}" '
            f'data-backtest="{str(is_bt).lower()}" data-filter-target>'
            f'<div style="display:flex;justify-content:space-between;align-items:center">'
            f'<strong>{ticker} — {class_name}</strong>{_category_badge(ticker)}'
            f'{badge}'
            f'</div>'
            f'<div style="font-size:12px;color:#ccc;margin:4px 0;font-style:italic">{html.escape(_strategy_description(class_name))}</div>'
            f'<div style="font-size:12px;color:#b0b0b0;margin:2px 0">Entered {entry_date} · {ticker} at {entry_spot_str}</div>'
            f'<div class="metric-row">'
        )
        if open_price is not None:
            lines.append(f'<span>Open: ${open_price:,.2f}</span>')
        if mark is not None:
            lines.append(f'<span>Mark: ${mark:,.2f}</span>')
        if pnl_realized is not None:
            lines.append(f'<span>P&L: {_fmt_pnl(pnl_realized)}</span>')
        lines.append('</div>')
        rules_html = _format_exit_rules(exit_rules)
        if rules_html:
            lines.append(f'<div style="font-size:12px;color:#b0b0b0">{html.escape(rules_html)}</div>')
        lines.append(f'<div style="font-size:12px;color:#ccc;margin-top:4px">{legs_html}</div>')
        rationale = pos.get("rationale")
        if rationale:
            lines.append(
                f'<blockquote style="font-size:12px;margin-top:6px;color:#ccc">'
                f'<strong>Why this trade:</strong> {html.escape(rationale)}</blockquote>'
            )
        if not is_open:
            lines.append(_closed_position_analysis(pos))
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
        '<thead><tr><th>Date</th><th>Ticker</th><th>Strategy</th><th>Intent</th><th>Legs</th><th>P&L</th><th>Commission</th></tr></thead>',
        '<tbody>',
    ]
    total_pnl = 0.0
    total_comm = 0.0
    for o in orders:
        ticker = html.escape(o.get("ticker", ""))
        class_name = html.escape(o.get("class_name", ""))
        strat_desc = html.escape(_strategy_description(class_name))
        intent = html.escape(o.get("intent", ""))
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
            f'<td>{ticker}{_category_badge(ticker)}</td>'
            f'<td style="font-size:12px">{class_name}<br><span style="color:#b0b0b0;font-size:10px">{strat_desc}</span></td>'
            f'<td>{intent}</td>'
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

def inventory_section(inventory: list[dict]) -> str:
    if not inventory:
        return '<div class="card">No long inventory positions.</div>'
    lines = [
        '<table>',
        '<thead><tr><th>Account</th><th>Ticker</th><th>Type</th><th>Strike</th><th>Expiry</th><th>Qty</th><th>Cost Basis</th></tr></thead>',
        '<tbody>',
    ]
    for row in inventory:
        account = html.escape(str(row.get("account", "")))
        ticker = html.escape(str(row.get("ticker", "")))
        kind = html.escape(str(row.get("kind", "")))
        strike = f"${row['strike']:,.0f}" if row.get("strike") else "—"
        expiry = row.get("expiry") or "—"
        qty = row.get("quantity", 0)
        qty_str = f"{qty:g}"
        cost = f"${row['cost_basis_per']:,.2f}" if row.get("cost_basis_per") else "—"
        lines.append(
            f'<tr data-ticker="{ticker}">'
            f'<td>{account}</td><td>{ticker}{_category_badge(ticker)}</td>'
            f'<td>{kind}</td><td>{strike}</td><td>{expiry}</td>'
            f'<td>{qty_str}</td><td>{cost}</td></tr>'
        )
    lines.append('</tbody></table>')
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 9. Costs section
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


# ---------------------------------------------------------------------------
# 10. Header section
# ---------------------------------------------------------------------------

def header_section(*, generated_at: str, total_pnl: float) -> str:
    """The sticky top header: brand mark, status dot, generated-at timestamp,
    and 30-day total P&L. Ports components-shell.jsx:Header."""
    from bullbot.dashboard.fmt import fmt_money, pnl_class
    pnl_cls = pnl_class(total_pnl)
    pnl_str = fmt_money(total_pnl, signed=True)
    return f"""<header class="app-header">
  <div class="brand">
    <div class="brand-mark"></div>
    <div>
      <span class="brand-name">Bull-Bot</span>
      <span class="brand-sub">v3.2 / paper</span>
    </div>
  </div>
  <div class="header-meta">
    <div class="item"><span class="dot"></span>Engine running</div>
    <div class="item mono" title="Page auto-refreshes every 60 seconds"><span style="color: var(--fg-2)">Last updated</span> {html.escape(generated_at)}</div>
    <div class="item">
      <span class="num" style="color: var(--fg-2)">P&amp;L 30d</span>
      <span class="num {pnl_cls}">{pnl_str}</span>
    </div>
  </div>
</header>"""


# ---------------------------------------------------------------------------
# 11. Sidebar section
# ---------------------------------------------------------------------------

def sidebar_section(*, active_tab: str, counts: dict[str, int]) -> str:
    """Left sidebar nav. 2 groups: Operations, Diagnostics. Each item has
    a stable data-tab attribute the JS uses to switch tabs.

    counts: per-tab badge count. Zero or missing → no badge.
    """
    operations = [
        ("overview", "Overview"),
        ("positions", "Positions"),
        ("evolver", "Evolver"),
        ("universe", "Universe"),
        ("leaderboard", "Leaderboard"),
        ("transactions", "Transactions"),
    ]
    diagnostics = [
        ("health", "Health"),
        ("costs", "Costs"),
        ("inventory", "Inventory"),
    ]

    def render_item(key: str, label: str) -> str:
        active = " active" if key == active_tab else ""
        n = counts.get(key, 0)
        badge = f'<span class="badge">{n}</span>' if n else ""
        return (
            f'<div class="nav-item{active}" data-tab="{key}">'
            f'<span>{html.escape(label)}</span>{badge}'
            f'</div>'
        )

    ops_html = "".join(render_item(k, l) for k, l in operations)
    diag_html = "".join(render_item(k, l) for k, l in diagnostics)
    return f"""<aside class="sidebar">
  <div class="nav-group">Operations</div>
  {ops_html}
  <div class="nav-divider"></div>
  <div class="nav-group">Diagnostics</div>
  {diag_html}
</aside>"""


def status_tiles(daemon: dict, cost: dict, sweep: dict) -> str:
    """Render three operational-status tiles: daemon heartbeat, today's LLM
    cost vs cap, and 24h sweep success rate. Each dict provides
    ``value`` and ``color`` (one of green/amber/red/gray); the color
    drives the ``tile-{color}`` CSS class. Visual style mirrors
    ``kpi_strip`` — small grid of bordered cards above the main KPI strip.
    """
    def _tile(label: str, value: str, color: str) -> str:
        safe_value = html.escape(value)
        safe_label = html.escape(label)
        return (
            f'<div class="tile tile-{color}">'
            f'<div class="label">{safe_label}</div>'
            f'<div class="value">{safe_value}</div>'
            f'</div>'
        )

    return (
        '<div class="status-tiles">'
        + _tile("Daemon", daemon.get("value", ""), daemon.get("color", "gray"))
        + _tile("LLM Cost", cost.get("value", ""), cost.get("color", "gray"))
        + _tile("Sweep Success", sweep.get("value", ""), sweep.get("color", "gray"))
        + '</div>'
    )


def kpi_strip(*, account: dict, metrics: dict, equity_curve: list) -> str:
    """Top-of-overview KPI strip: 5 cards. Ports components-shell.jsx:KPIStrip."""
    from bullbot.dashboard.fmt import fmt_money, fmt_pct, pnl_class
    from bullbot.dashboard.svg_charts import sparkline_svg

    eq_values = [float(p["total_equity"]) for p in equity_curve] if equity_curve else []
    realized = metrics.get("realized_pnl", 0)
    unrealized = metrics.get("unrealized_pnl", 0)
    target_progress = (account["month_to_date"] / account["target_monthly"]
                       if account.get("target_monthly") else 0.0)
    llm_progress = metrics.get("llm_spend", 0) / 50.0
    llm_warn = llm_progress > 0.5

    spark_eq = sparkline_svg(eq_values) if eq_values else ""

    realized_cls = pnl_class(realized)
    unrealized_cls = pnl_class(unrealized)

    return f"""<div class="kpi-grid">
  <div class="kpi">
    <div class="label">Total Equity</div>
    <div class="value">{fmt_money(account["total_equity"], decimals=0)}</div>
    <div class="sub"><span>{account.get('days_to_target', 0)}d to target</span></div>
    <div class="spark">{spark_eq}</div>
  </div>
  <div class="kpi">
    <div class="label">Realized P&amp;L</div>
    <div class="value"><span class="{realized_cls}">{fmt_money(realized, signed=True, decimals=0)}</span></div>
    <div class="sub"><span>WR {fmt_pct(metrics.get('win_rate', 0), decimals=0)}</span><span>·</span><span>PF {metrics.get('profit_factor', 0):.2f}</span></div>
  </div>
  <div class="kpi">
    <div class="label">Unrealized P&amp;L</div>
    <div class="value"><span class="{unrealized_cls}">{fmt_money(unrealized, signed=True, decimals=0)}</span></div>
    <div class="sub"><span>{metrics.get('open_positions', 0)} open</span><span>·</span><span>Sharpe {metrics.get('sharpe_30d', 0):.2f}</span></div>
  </div>
  <div class="kpi">
    <div class="label">Target Progress</div>
    <div class="value">{fmt_money(account['month_to_date'], decimals=0)}<span style="color: var(--fg-2); font-size: 14px"> / {fmt_money(account['target_monthly'], decimals=0)}</span></div>
    <div class="sub"><span>{account.get('days_to_target', 0)}d to target date</span></div>
    <div class="progress" style="margin-top: 6px"><div style="width: {min(100.0, target_progress * 100):.1f}%; background: var(--accent)"></div></div>
  </div>
  <div class="kpi">
    <div class="label">LLM Spend (MTD)</div>
    <div class="value">${metrics.get('llm_spend', 0):.2f}<span style="color: var(--fg-2); font-size: 14px"> / $50</span></div>
    <div class="sub"><span>${metrics.get('llm_spend_7d', 0):.2f} this week</span></div>
    <div class="progress" style="margin-top: 6px"><div style="width: {min(100.0, llm_progress * 100):.1f}%; background: var({'--warn' if llm_warn else '--accent'})"></div></div>
  </div>
</div>"""
