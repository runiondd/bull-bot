"""Generate the Bull-Bot HTML dashboard from the database."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from bullbot import config
from bullbot.dashboard import queries, tabs, templates


def generate(conn: sqlite3.Connection, output_path: Path | None = None) -> Path:
    if output_path is None:
        output_path = config.REPORTS_DIR / "dashboard.html"

    now_str = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Pull all data once
    summary = queries.summary_metrics(conn)
    extended = queries.extended_metrics(conn)
    account = queries.account_summary(conn)
    eq_curve = queries.equity_curve(conn, days=30)
    universe = queries.universe_with_edge(conn)
    activity = queries.recent_activity(conn, limit=20)
    proposals = queries.evolver_proposals(conn)
    positions = queries.positions_list(conn)
    orders = queries.orders_list(conn)
    costs = queries.cost_breakdown(conn)
    inventory = queries.long_inventory_summary(conn)

    metrics = {**summary, **extended}
    total_pnl = metrics.get("realized_pnl", 0) + metrics.get("unrealized_pnl", 0)

    # Adapt query rows to the JSX/data.js shape that tabs.py expects
    adapted_proposals = [_proposal_to_jsx_shape(p) for p in proposals]
    adapted_positions = [_position_to_jsx_shape(p) for p in positions]
    adapted_orders = [_order_to_jsx_shape(o) for o in orders]
    adapted_costs = _costs_to_jsx_shape(costs)
    adapted_inventory = [_inventory_to_jsx_shape(i) for i in inventory]

    data = {
        "metrics": {**metrics, "paperTradeCount": metrics.get("paper_trade_count", 0),
                    "backtestCount": metrics.get("backtest_count", 0)},
        "account": account,
        "equity_curve": eq_curve,
        "universe": universe,
        "pnl_by_ticker": summary["pnl_by_ticker"],
        "activity": [_event_to_activity(e) for e in activity],
        "proposals": adapted_proposals,
        "positions": adapted_positions,
        "orders": adapted_orders,
        "costs": adapted_costs,
        "inventory": adapted_inventory,
    }

    # Health data — pulled separately because health module owns the brief
    try:
        from bullbot.research import health as research_health
        brief = research_health.generate_health_brief(conn)
        data["health"] = _brief_to_dashboard_dict(brief, universe)
    except Exception:
        data["health"] = {"universe": _phase_counts(universe), "checks": []}

    counts = {
        "positions": sum(1 for p in adapted_positions if p.get("isOpen")),
        "evolver": len(adapted_proposals),
        "universe": len(universe),
        "transactions": len(adapted_orders),
        "health": sum(1 for c in data["health"]["checks"] if c.get("status") != "ok"),
        "inventory": len(adapted_inventory),
    }

    body_parts = [
        templates.header_section(generated_at=now_str, total_pnl=total_pnl),
        '<div class="layout">',
        templates.sidebar_section(active_tab="overview", counts=counts),
        '<main>',
        '<div class="page-title-row"><div><div class="page-title">Overview</div></div></div>',
        templates.kpi_strip(account=account, metrics=data["metrics"], equity_curve=eq_curve),
    ]

    tab_funcs = [
        ("overview", tabs.overview_tab),
        ("positions", tabs.positions_tab),
        ("evolver", tabs.evolver_tab),
        ("universe", tabs.universe_tab),
        ("transactions", tabs.transactions_tab),
        ("health", tabs.health_tab),
        ("costs", tabs.costs_tab),
        ("inventory", tabs.inventory_tab),
    ]
    for i, (name, fn) in enumerate(tab_funcs):
        display = "block" if i == 0 else "none"
        try:
            content = fn(data)
        except Exception as exc:
            content = f'<div class="card"><div class="card-body">Error rendering {name}: {exc}</div></div>'
        body_parts.append(
            f'<div class="tab-content" id="tab-{name}" style="display: {display}">'
            f'{content}'
            f'</div>'
        )

    body_parts.extend(['</main>', '</div>'])
    body = "\n".join(body_parts)
    html = templates.page_shell(now_str, body)
    output_path.write_text(html, encoding="utf-8")
    return output_path


def _event_to_activity(event: dict) -> dict:
    return {
        "ts": _short_ts(event.get("ts")),
        "ticker": event.get("ticker", ""),
        "type": _map_event_type(event.get("event_type", "")),
        "text": event.get("detail", ""),
    }


def _short_ts(epoch) -> str:
    if not epoch:
        return ""
    try:
        dt = datetime.fromtimestamp(int(epoch), tz=timezone.utc)
        return dt.strftime("%H:%M")
    except Exception:
        return ""


def _map_event_type(event_type: str) -> str:
    mapping = {"proposal": "proposal", "order": "fill",
               "promotion": "promotion"}
    return mapping.get(event_type, event_type)


def _phase_counts(universe: list[dict]) -> dict:
    counts = {"total": len(universe), "live": 0, "paper_trial": 0,
              "discovering": 0, "no_edge": 0}
    for u in universe:
        p = u.get("phase", "")
        if p in counts:
            counts[p] += 1
    return counts


def _brief_to_dashboard_dict(brief, universe) -> dict:
    """Map a HealthBrief into the dashboard's expected health-data shape."""
    checks = []
    for r in brief.results:
        status = "ok" if r.passed else "warn"
        detail = " · ".join(r.findings) if r.findings else "OK"
        checks.append({"name": r.title, "status": status, "detail": detail})
    return {
        "universe": _phase_counts(universe),
        "checks": checks,
    }


def _proposal_to_jsx_shape(p: dict) -> dict:
    """Adapt evolver_proposals row to JSX-shape with camelCase fields."""
    return {
        "id": f"ep_{p.get('id', '')}",
        "ticker": p.get("ticker", ""),
        "className": p.get("class_name", ""),
        "iteration": p.get("iteration", 0),
        "passed": bool(p.get("passed_gate", 0)),
        "createdAt": _format_ts_iso(p.get("created_at")),
        "pf_oos": p.get("pf_oos") or 0.0,
        "pf_is": p.get("pf_is") or 0.0,
        "max_dd_pct": p.get("max_dd_pct") or 0.0,
        "trade_count": p.get("trade_count") or 0,
        "llm_cost": p.get("llm_cost_usd") or 0.0,
        "params": p.get("params") or {},
        "rationale": p.get("rationale") or "",
    }


def _position_to_jsx_shape(p: dict) -> dict:
    """Adapt positions_list row to JSX shape."""
    pnl_realized = p.get("pnl_realized") or 0.0
    unrealized = p.get("unrealized_pnl") or 0.0
    pnl = pnl_realized if not p.get("is_open") else unrealized
    open_price = p.get("open_price") or 0.0
    pnl_pct = (pnl / abs(open_price)) if open_price else 0.0
    return {
        "id": p.get("id", 0),
        "ticker": p.get("ticker", ""),
        "className": "",  # strategy_id known but not joined here; OK to leave blank for now
        "isOpen": bool(p.get("is_open")),
        "openedAt": _format_ts_date(p.get("opened_at")),
        "closedAt": _format_ts_date(p.get("closed_at")),
        "entrySpot": p.get("entry_spot") or 0.0,
        "mark": p.get("mark_to_mkt") or 0.0,
        "openPrice": open_price,
        "pnl": pnl,
        "pnlPct": pnl_pct,
        "dte": 0,  # not currently computed; tabs.py handles 0/None gracefully
        "legs": p.get("legs") or [],
        "exitRules": p.get("exit_rules") or {},
        "rationale": p.get("rationale") or "",
    }


def _order_to_jsx_shape(o: dict) -> dict:
    return {
        "date": _format_ts_datetime(o.get("placed_at")),
        "ticker": o.get("ticker", ""),
        "className": "",  # strategy not joined
        "intent": o.get("intent", ""),
        "legs": o.get("legs_abbrev", ""),
        "pnl": o.get("pnl_realized"),
        "commission": o.get("commission") or 0.0,
        "isBacktest": bool(o.get("is_backtest")),
    }


def _costs_to_jsx_shape(c: dict) -> dict:
    return {
        "llmPerTicker": c.get("llm_per_ticker", {}),
        "llmTotal": c.get("llm_ledger_total", 0.0),
        "llmBudget": 50.0,
        "paperCommissions": c.get("paper_commissions", 0.0),
        "backtestCommissions": c.get("backtest_commissions", 0.0),
    }


def _inventory_to_jsx_shape(i: dict) -> dict:
    return {
        "account": i.get("account", "income"),
        "ticker": i.get("ticker", ""),
        "kind": i.get("kind", "S"),
        "strike": i.get("strike", 0) or 0,
        "expiry": i.get("expiry", "") or "",
        "qty": i.get("qty", 0),
        "costBasis": i.get("cost_basis", 0.0),
    }


def _format_ts_date(epoch) -> str:
    if not epoch:
        return ""
    try:
        return datetime.fromtimestamp(int(epoch), tz=timezone.utc).strftime("%Y-%m-%d")
    except Exception:
        return ""


def _format_ts_datetime(epoch) -> str:
    if not epoch:
        return ""
    try:
        return datetime.fromtimestamp(int(epoch), tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ""


def _format_ts_iso(epoch) -> str:
    if not epoch:
        return ""
    try:
        return datetime.fromtimestamp(int(epoch), tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return ""


if __name__ == "__main__":
    conn = sqlite3.connect(str(config.DB_PATH))
    conn.row_factory = sqlite3.Row
    path = generate(conn)
    print(f"Dashboard written to {path}")
    conn.close()
