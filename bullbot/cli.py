"""Operator CLI. python -m bullbot.cli <command> [args]"""
from __future__ import annotations
import argparse, json, sqlite3, sys, time
from bullbot import config
from bullbot.db import connection as db_connection
from bullbot.risk import cost_ledger, kill_switch


def _open_db():
    return db_connection.open_persistent_connection(config.DB_PATH)


def cmd_status(args):
    conn = _open_db()
    kill_active = kill_switch.is_tripped(conn)
    print(f"Bull-Bot status — kill_switch_active={kill_active}")
    rows = conn.execute(
        "SELECT ticker, phase, iteration_count, best_pf_oos, cumulative_llm_usd, retired "
        "FROM ticker_state ORDER BY ticker"
    ).fetchall()
    if not rows:
        print("(no tickers in database)")
        return 0
    for r in rows:
        print(f"  {r['ticker']:<8} {r['phase']:<15} iters={r['iteration_count']}")
    print(f"\nTotal LLM spend: ${cost_ledger.cumulative_llm_usd(conn):.2f}")
    return 0


def cmd_add_ticker(args):
    conn = _open_db()
    conn.execute(
        "INSERT OR IGNORE INTO ticker_state (ticker, phase, updated_at) "
        "VALUES (?, 'discovering', ?)",
        (args.ticker.upper(), int(time.time())),
    )
    print(f"Added {args.ticker.upper()} to discovering phase")
    return 0


def cmd_retire_ticker(args):
    conn = _open_db()
    conn.execute(
        "UPDATE ticker_state SET retired=1, updated_at=? WHERE ticker=?",
        (int(time.time()), args.ticker.upper()),
    )
    print(f"Retired {args.ticker.upper()}")
    return 0


def cmd_rearm(args):
    if not args.acknowledge_risk:
        print("Error: --acknowledge-risk flag required", file=sys.stderr)
        return 1
    conn = _open_db()
    kill_switch.rearm(conn)
    conn.execute(
        "UPDATE ticker_state SET phase='paper_trial', paper_started_at=?, "
        "paper_trade_count=0, updated_at=? WHERE ticker=?",
        (int(time.time()), int(time.time()), args.ticker.upper()),
    )
    print(f"Rearmed. {args.ticker.upper()} → paper_trial")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="bullbot")
    sub = parser.add_subparsers(dest="command")
    p_status = sub.add_parser("status")
    p_status.set_defaults(fn=cmd_status)
    p_add = sub.add_parser("add-ticker")
    p_add.add_argument("ticker")
    p_add.set_defaults(fn=cmd_add_ticker)
    p_retire = sub.add_parser("retire-ticker")
    p_retire.add_argument("ticker")
    p_retire.set_defaults(fn=cmd_retire_ticker)
    p_rearm = sub.add_parser("rearm")
    p_rearm.add_argument("--ticker", required=True)
    p_rearm.add_argument("--acknowledge-risk", action="store_true")
    p_rearm.set_defaults(fn=cmd_rearm)
    args = parser.parse_args(argv)
    if not hasattr(args, "fn"):
        parser.print_help()
        return 1
    return args.fn(args)

if __name__ == "__main__":
    raise SystemExit(main())
