"""Generate the Bull-Bot HTML dashboard from the database."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from bullbot import config
from bullbot.dashboard import queries, templates
from bullbot.research import health as research_health


def generate(conn: sqlite3.Connection, output_path: Path | None = None) -> Path:
    if output_path is None:
        output_path = config.REPORTS_DIR / "dashboard.html"

    now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    metrics = queries.summary_metrics(conn)
    grid = queries.ticker_grid(conn)
    activity = queries.recent_activity(conn)
    proposals = queries.evolver_proposals(conn)
    positions = queries.positions_list(conn)
    orders = queries.orders_list(conn)
    costs = queries.cost_breakdown(conn)
    inventory = queries.long_inventory_summary(conn)

    overview_html = templates.ticker_grid(grid) + templates.activity_feed(activity)
    evolver_html = templates.evolver_section(proposals)
    positions_html = templates.positions_section(positions)
    transactions_html = templates.transactions_section(orders)
    costs_html = templates.costs_section(costs)
    inventory_html = templates.inventory_section(inventory)

    try:
        health_html = research_health.generate_health_brief(conn).to_html()
    except Exception:
        # Dashboard must render even if health module breaks
        health_html = '<p class="research-health-error">Health brief unavailable this run.</p>'

    tabs = {
        "Overview": overview_html,
        "Health": health_html,
        "Evolver": evolver_html,
        "Positions": positions_html,
        "Transactions": transactions_html,
        "Costs": costs_html,
        "Inventory": inventory_html,
    }

    body_parts = [templates.summary_cards(metrics)]
    body_parts.append('<div class="tab-bar">')
    for i, name in enumerate(tabs):
        active = " active" if i == 0 else ""
        body_parts.append(
            f'<button class="tab-btn{active}" onclick="switchTab(\'{name}\')">{name}</button>'
        )
    body_parts.append('</div>')
    body_parts.append('<div id="filter-indicator" style="display:none"></div>')

    for i, (name, content) in enumerate(tabs.items()):
        display = "block" if i == 0 else "none"
        body_parts.append(
            f'<div class="tab-content" id="tab-{name}" style="display:{display}">{content}</div>'
        )

    body = "\n".join(body_parts)
    html = templates.page_shell(now, body)
    output_path.write_text(html, encoding="utf-8")
    return output_path


if __name__ == "__main__":
    conn = sqlite3.connect(str(config.DB_PATH))
    conn.row_factory = sqlite3.Row
    path = generate(conn)
    print(f"Dashboard written to {path}")
    conn.close()
