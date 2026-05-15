"""Operator CLI. python -m bullbot.cli <command> [args]"""
from __future__ import annotations
import argparse, json, logging, sqlite3, sys, time
from bullbot import config
from bullbot.db import connection as db_connection
from bullbot.risk import cost_ledger, kill_switch


def _open_db():
    return db_connection.open_persistent_connection(config.DB_PATH)


def _build_anthropic_client():
    import anthropic

    return anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)


def _build_uw_client():
    from bullbot.data import fetchers

    return fetchers.UWHttpClient(api_key=config.UW_API_KEY)


def cmd_status(args):
    conn = _open_db()
    kill_active = kill_switch.is_tripped(conn)
    print(f"Bull-Bot status — kill_switch_active={kill_active}")
    rows = conn.execute(
        "SELECT ticker, phase, iteration_count, best_pf_oos, best_cagr_oos, "
        "cumulative_llm_usd, retired "
        "FROM ticker_state ORDER BY ticker"
    ).fetchall()
    if not rows:
        print("(no tickers in database)")
        return 0
    for r in rows:
        category = config.TICKER_CATEGORY.get(r['ticker'], 'income')
        if category == 'growth':
            oos_label = "cagr_oos"
            oos_val = r['best_cagr_oos']
        else:
            oos_label = "pf_oos"
            oos_val = r['best_pf_oos']
        oos_str = f"{oos_val:.4g}" if oos_val is not None else "n/a"
        print(
            f"  {r['ticker']:<8} {r['phase']:<15} iters={r['iteration_count']}"
            f"  {oos_label}={oos_str}"
        )
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


def cmd_run_daily(args):
    """One-shot daily job: refresh Yahoo bars, then run a single scheduler tick.

    Intended for invocation from launchd (see deploy/com.bullbot.daily.plist).
    Returns non-zero if any ticker failed to refresh — launchd surfaces the
    exit code so operational failures are visible.
    """
    from bullbot import scheduler
    from bullbot.data import daily_refresh

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    log = logging.getLogger("bullbot.cli.run_daily")

    conn = _open_db()
    tickers = daily_refresh.discover_tracked_tickers(conn)
    if not tickers:
        log.warning("run-daily: no tickers to refresh (bars table is empty)")
        return 0

    log.info("run-daily: refreshing %d tickers", len(tickers))
    result = daily_refresh.refresh_all_bars(conn, tickers)
    failures = [t for t, n in result.items() if n == 0]

    anthropic_client = _build_anthropic_client()
    uw_client = _build_uw_client()

    log.info("run-daily: calling scheduler.tick()")
    scheduler.tick(conn=conn, anthropic_client=anthropic_client, data_client=uw_client)
    conn.commit()

    if failures:
        log.warning("run-daily: %d tickers failed refresh: %s", len(failures), ",".join(failures))
        return 1
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


def cmd_ab_report(args):
    """Print pass-rate / avg pf_oos / total cost grouped by proposer_model.

    Used to decide whether to ship Sonnet after the 7-day A/B window
    (decision rule: ship Sonnet if its pass rate is ≥ 80% of Opus's).
    """
    conn = _open_db()
    cutoff = int(time.time()) - args.days * 86_400
    rows = conn.execute(
        "SELECT proposer_model, "
        "       COUNT(*) AS n, "
        "       SUM(passed_gate) AS passes, "
        "       AVG(pf_oos) AS avg_pf_oos, "
        "       SUM(llm_cost_usd) AS total_cost "
        "FROM evolver_proposals "
        "WHERE proposer_model IS NOT NULL AND created_at >= ? "
        "GROUP BY proposer_model "
        "ORDER BY proposer_model",
        (cutoff,),
    ).fetchall()
    if not rows:
        print(f"No proposer_model data in the last {args.days} day(s).")
        return 0
    print(f"A/B report — last {args.days} day(s)")
    print(f"{'model':<25} {'n':>4} {'pass_rate':>10} {'avg_pf_oos':>11} {'total_$':>10}")
    for r in rows:
        n = r["n"] or 0
        passes = r["passes"] or 0
        rate = (passes / n * 100.0) if n else 0.0
        avg_pf = r["avg_pf_oos"] if r["avg_pf_oos"] is not None else 0.0
        cost = r["total_cost"] or 0.0
        print(f"{r['proposer_model']:<25} {n:>4} {rate:>9.1f}% {avg_pf:>11.3f} {cost:>9.3f}")
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
    p_run_daily = sub.add_parser("run-daily", help="Refresh bars and run one scheduler tick.")
    p_run_daily.set_defaults(fn=cmd_run_daily)
    p_ab = sub.add_parser("ab-report", help="Print Phase 2 A/B comparison (Opus vs Sonnet) by proposer_model.")
    p_ab.add_argument("--days", type=int, default=30, help="How many days back to include (default: 30).")
    p_ab.set_defaults(fn=cmd_ab_report)
    args = parser.parse_args(argv)
    if not hasattr(args, "fn"):
        parser.print_help()
        return 1
    return args.fn(args)

if __name__ == "__main__":
    raise SystemExit(main())
