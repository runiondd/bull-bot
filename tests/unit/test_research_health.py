"""Unit tests for bullbot.research.health."""
from __future__ import annotations

import sqlite3
import time
from datetime import date, datetime, time as dtime, timezone

import pytest

from bullbot import config
from bullbot.research import health as H


# --- Dataclasses ------------------------------------------------------------

def test_check_result_is_frozen():
    r = H.CheckResult(title="X", passed=True, findings=[])
    with pytest.raises(Exception):  # FrozenInstanceError subclass of AttributeError
        r.title = "Y"


def test_check_result_findings_empty_when_passed():
    # Convention, not a hard constraint, but most call sites assume this.
    r = H.CheckResult(title="X", passed=True, findings=[])
    assert r.passed is True
    assert r.findings == []


def test_health_brief_holds_structured_state():
    brief = H.HealthBrief(
        generated_at=1_700_000_000,
        header={"Universe": "16 tickers"},
        results=[H.CheckResult(title="X", passed=True, findings=[])],
    )
    assert brief.generated_at == 1_700_000_000
    assert brief.header["Universe"] == "16 tickers"
    assert len(brief.results) == 1


# --- _safe_check ------------------------------------------------------------

def test_safe_check_returns_result_from_healthy_fn():
    def clean(conn):
        return H.CheckResult(title="clean", passed=True, findings=[])
    result = H._safe_check(clean, conn=None)
    assert result.title == "clean"
    assert result.passed is True


def test_safe_check_converts_exception_to_findings():
    def boom(conn):
        raise ValueError("explicit failure")
    result = H._safe_check(boom, conn=None)
    assert result.title == "boom"
    assert result.passed is False
    assert any("ValueError" in f and "explicit failure" in f for f in result.findings)


# --- check_data_shortfalls --------------------------------------------------


def _make_conn_with_bars(bars_by_ticker: dict[str, int]) -> sqlite3.Connection:
    """Minimal DB with a bars table populated by per-ticker row count."""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("""
        CREATE TABLE bars (
            ticker TEXT, timeframe TEXT, ts INTEGER,
            open REAL, high REAL, low REAL, close REAL, volume INTEGER
        )
    """)
    for ticker, n in bars_by_ticker.items():
        for i in range(n):
            c.execute(
                "INSERT INTO bars (ticker, timeframe, ts, open, high, low, close, volume) "
                "VALUES (?, '1d', ?, 100, 101, 99, 100, 0)",
                (ticker, i),
            )
    return c


def test_check_data_shortfalls_passes_when_all_tickers_have_enough_bars(monkeypatch):
    monkeypatch.setattr(config, "UNIVERSE", ["SPY", "QQQ"])
    monkeypatch.setattr(config, "HEALTH_MIN_BARS_FOR_WF", 10)
    conn = _make_conn_with_bars({"SPY": 50, "QQQ": 20})
    result = H.check_data_shortfalls(conn)
    assert result.passed is True
    assert result.findings == []


def test_check_data_shortfalls_flags_under_threshold_tickers(monkeypatch):
    monkeypatch.setattr(config, "UNIVERSE", ["SPY", "XLK", "HYG"])
    monkeypatch.setattr(config, "HEALTH_MIN_BARS_FOR_WF", 500)
    conn = _make_conn_with_bars({"SPY": 1000, "XLK": 257, "HYG": 257})
    result = H.check_data_shortfalls(conn)
    assert result.passed is False
    assert len(result.findings) == 2
    assert any("XLK" in f and "257" in f and "500" in f for f in result.findings)
    assert any("HYG" in f for f in result.findings)
    # SPY passes, so no finding for it
    assert not any("SPY" in f for f in result.findings)


# --- check_pf_inf ------------------------------------------------------------


def _make_conn_with_ticker_state() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("""
        CREATE TABLE ticker_state (
            id INTEGER PRIMARY KEY,
            ticker TEXT UNIQUE,
            phase TEXT,
            iteration_count INTEGER DEFAULT 0,
            plateau_counter INTEGER DEFAULT 0,
            best_strategy_id INTEGER,
            best_pf_is REAL,
            best_pf_oos REAL,
            best_cagr_oos REAL,
            cumulative_llm_usd REAL DEFAULT 0,
            paper_started_at INTEGER,
            paper_trade_count INTEGER DEFAULT 0,
            live_started_at INTEGER,
            verdict_at INTEGER,
            retired INTEGER DEFAULT 0,
            updated_at INTEGER
        )
    """)
    return c


def test_check_pf_inf_passes_when_all_pf_values_reasonable():
    conn = _make_conn_with_ticker_state()
    conn.execute(
        "INSERT INTO ticker_state (ticker, phase, best_pf_oos, best_strategy_id, updated_at) "
        "VALUES ('SPY', 'paper_trial', 1.8, 10, 0)"
    )
    conn.execute(
        "INSERT INTO ticker_state (ticker, phase, best_pf_oos, best_strategy_id, updated_at) "
        "VALUES ('QQQ', 'discovering', NULL, NULL, 0)"
    )
    result = H.check_pf_inf(conn)
    assert result.passed is True
    assert result.findings == []


def test_check_pf_inf_flags_infinite_and_absurd_pf_values():
    conn = _make_conn_with_ticker_state()
    conn.execute(
        "INSERT INTO ticker_state (ticker, phase, best_pf_oos, best_strategy_id, updated_at) "
        "VALUES ('AAPL', 'no_edge', ?, 123, 0)", (float("inf"),),
    )
    conn.execute(
        "INSERT INTO ticker_state (ticker, phase, best_pf_oos, best_strategy_id, updated_at) "
        "VALUES ('TSLA', 'paper_trial', 1e12, 114, 0)"
    )
    conn.execute(
        "INSERT INTO ticker_state (ticker, phase, best_pf_oos, best_strategy_id, updated_at) "
        "VALUES ('MSFT', 'discovering', 2.5, 99, 0)"
    )
    result = H.check_pf_inf(conn)
    assert result.passed is False
    assert len(result.findings) == 2
    assert any("AAPL" in f and "inf" in f and "123" in f for f in result.findings)
    assert any("TSLA" in f and "114" in f for f in result.findings)
    # MSFT's pf_oos=2.5 is reasonable, should not be flagged
    assert not any("MSFT" in f for f in result.findings)


# --- check_dead_paper_trials -------------------------------------------------


def test_check_dead_paper_trials_passes_when_all_healthy(monkeypatch):
    monkeypatch.setattr(config, "HEALTH_DEAD_PAPER_DAYS", 3)
    now = 1_700_000_000
    conn = _make_conn_with_ticker_state()
    # freshly promoted, not yet past threshold
    conn.execute(
        "INSERT INTO ticker_state (ticker, phase, paper_started_at, "
        "paper_trade_count, verdict_at, updated_at) "
        "VALUES ('GOOGL', 'paper_trial', NULL, 0, ?, ?)",
        (now - 1 * 86400, now),
    )
    # actively trading
    conn.execute(
        "INSERT INTO ticker_state (ticker, phase, paper_started_at, "
        "paper_trade_count, updated_at) "
        "VALUES ('SPY', 'paper_trial', ?, 5, ?)",
        (now - 10 * 86400, now),
    )
    result = H.check_dead_paper_trials(conn, now=now)
    assert result.passed is True


def test_check_dead_paper_trials_flags_never_started(monkeypatch):
    monkeypatch.setattr(config, "HEALTH_DEAD_PAPER_DAYS", 3)
    now = 1_700_000_000
    conn = _make_conn_with_ticker_state()
    conn.execute(
        "INSERT INTO ticker_state (ticker, phase, paper_started_at, "
        "paper_trade_count, verdict_at, updated_at) "
        "VALUES ('SATS', 'paper_trial', NULL, 0, ?, ?)",
        (now - 5 * 86400, now),
    )
    result = H.check_dead_paper_trials(conn, now=now)
    assert result.passed is False
    assert len(result.findings) == 1
    assert "SATS" in result.findings[0]
    assert "never fired" in result.findings[0] or "never started" in result.findings[0]


def test_check_dead_paper_trials_flags_zero_trades_after_threshold(monkeypatch):
    monkeypatch.setattr(config, "HEALTH_DEAD_PAPER_DAYS", 3)
    now = 1_700_000_000
    conn = _make_conn_with_ticker_state()
    conn.execute(
        "INSERT INTO ticker_state (ticker, phase, paper_started_at, "
        "paper_trade_count, updated_at) "
        "VALUES ('XLF', 'paper_trial', ?, 0, ?)",
        (now - 5 * 86400, now),
    )
    result = H.check_dead_paper_trials(conn, now=now)
    assert result.passed is False
    assert len(result.findings) == 1
    assert "XLF" in result.findings[0]
    assert "0 live trades" in result.findings[0] or "0 trades" in result.findings[0]


def test_check_dead_paper_trials_flags_null_verdict_at_via_updated_at(monkeypatch):
    """SATS case: promoted to paper_trial with verdict_at NOT recorded —
    the check should still catch it via the updated_at fallback so we
    notice 'promoted but never started' even when the promotion pipeline
    didn't stamp a verdict time."""
    monkeypatch.setattr(config, "HEALTH_DEAD_PAPER_DAYS", 3)
    now = 1_700_000_000
    conn = _make_conn_with_ticker_state()
    conn.execute(
        "INSERT INTO ticker_state (ticker, phase, paper_started_at, "
        "paper_trade_count, verdict_at, updated_at) "
        "VALUES ('SATS', 'paper_trial', NULL, 0, NULL, ?)",
        (now - 5 * 86400,),
    )
    result = H.check_dead_paper_trials(conn, now=now)
    assert result.passed is False
    assert len(result.findings) == 1
    assert "SATS" in result.findings[0]
    assert "never fired" in result.findings[0] or "never started" in result.findings[0]


# --- check_iteration_failures -----------------------------------------------


def _add_iteration_failures_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE iteration_failures (
            id INTEGER PRIMARY KEY,
            ts INTEGER NOT NULL,
            ticker TEXT,
            phase TEXT NOT NULL,
            exc_type TEXT NOT NULL,
            exc_message TEXT NOT NULL,
            traceback TEXT
        )
    """)


def test_check_iteration_failures_passes_when_no_recent_failures():
    now = 1_700_000_000
    conn = _make_conn_with_ticker_state()
    _add_iteration_failures_table(conn)
    # one old failure, outside 24h window
    conn.execute(
        "INSERT INTO iteration_failures (ts, ticker, phase, exc_type, exc_message) "
        "VALUES (?, 'AAPL', 'discovering', 'ValueError', 'old')",
        (now - 2 * 86400,),
    )
    result = H.check_iteration_failures(conn, now=now)
    assert result.passed is True


def test_check_iteration_failures_flags_and_groups():
    now = 1_700_000_000
    conn = _make_conn_with_ticker_state()
    _add_iteration_failures_table(conn)
    # two recent, same ticker + exc type
    for _ in range(2):
        conn.execute(
            "INSERT INTO iteration_failures (ts, ticker, phase, exc_type, exc_message) "
            "VALUES (?, 'AAPL', 'discovering', 'DailyRefreshError', 'bad bar')",
            (now - 3600,),
        )
    # one recent, different ticker
    conn.execute(
        "INSERT INTO iteration_failures (ts, ticker, phase, exc_type, exc_message) "
        "VALUES (?, 'QQQ', 'paper_trial', 'ZeroDivisionError', 'div by zero')",
        (now - 7200,),
    )
    result = H.check_iteration_failures(conn, now=now)
    assert result.passed is False
    assert len(result.findings) == 2
    assert any("AAPL" in f and "DailyRefreshError" in f and "2" in f for f in result.findings)
    assert any("QQQ" in f and "ZeroDivisionError" in f for f in result.findings)


# --- generate_health_brief ---------------------------------------------------


def _today_utc_ts() -> int:
    return int(datetime.combine(date.today(), dtime.min, tzinfo=timezone.utc).timestamp())


def _make_full_conn() -> sqlite3.Connection:
    """Connection with all tables needed by generate_health_brief."""
    c = _make_conn_with_ticker_state()
    _add_iteration_failures_table(c)
    c.execute("""
        CREATE TABLE bars (
            ticker TEXT, timeframe TEXT, ts INTEGER,
            open REAL, high REAL, low REAL, close REAL, volume INTEGER
        )
    """)
    c.execute("""
        CREATE TABLE strategies (
            id INTEGER PRIMARY KEY, class_name TEXT, class_version INTEGER,
            params TEXT, params_hash TEXT, parent_id INTEGER, created_at INTEGER
        )
    """)
    c.execute("""
        CREATE TABLE positions (
            id INTEGER PRIMARY KEY, run_id TEXT, ticker TEXT, strategy_id INTEGER,
            legs TEXT, contracts INTEGER, open_price REAL, close_price REAL,
            mark_to_mkt REAL, opened_at INTEGER, closed_at INTEGER,
            pnl_realized REAL, exit_rules TEXT
        )
    """)
    c.execute("""
        CREATE TABLE cost_ledger (
            id INTEGER PRIMARY KEY, ts INTEGER, category TEXT, ticker TEXT,
            amount_usd REAL, details TEXT
        )
    """)
    return c


def test_generate_health_brief_returns_populated_header(monkeypatch):
    monkeypatch.setattr(config, "UNIVERSE", ["SPY"])
    monkeypatch.setattr(config, "HEALTH_MIN_BARS_FOR_WF", 10)
    today = _today_utc_ts()
    conn = _make_full_conn()
    conn.execute(
        "INSERT INTO ticker_state (ticker, phase, updated_at) VALUES ('SPY', 'discovering', ?)",
        (today,),
    )
    conn.execute(
        "INSERT INTO strategies (class_name, class_version, params, params_hash, created_at) "
        "VALUES ('PutCreditSpread', 1, '{}', 'hash1', ?)", (today,),
    )
    conn.execute(
        "INSERT INTO cost_ledger (ts, category, amount_usd) VALUES (?, 'llm', 0.42)",
        (today + 100,),
    )
    brief = H.generate_health_brief(conn)
    assert isinstance(brief, H.HealthBrief)
    assert "Universe" in brief.header
    assert "1 tickers" in brief.header["Universe"] or "1 ticker" in brief.header["Universe"]
    assert "1 discovering" in brief.header["Universe"]
    assert brief.header["Strategy pool"].startswith("1")
    assert "+1 today" in brief.header["Strategy pool"]
    assert "$0.42" in brief.header["LLM spend today"]


def test_generate_health_brief_runs_all_four_checks(monkeypatch):
    monkeypatch.setattr(config, "UNIVERSE", ["SPY"])
    monkeypatch.setattr(config, "HEALTH_MIN_BARS_FOR_WF", 10)
    conn = _make_full_conn()
    brief = H.generate_health_brief(conn)
    titles = {r.title for r in brief.results}
    assert {
        "Data shortfalls",
        "pf_oos anomalies",
        "Dead paper trials",
        "Iteration failures (24h)",
    }.issubset(titles)
    assert len(brief.results) == 4


# --- HealthBrief.to_markdown -------------------------------------------------


def _sample_brief() -> H.HealthBrief:
    return H.HealthBrief(
        generated_at=1_700_000_000,
        header={"Universe": "16 tickers (6 discovering)", "LLM spend today": "$0.38"},
        results=[
            H.CheckResult(
                title="Data shortfalls", passed=False,
                findings=["XLK: 257 bars (need ~504)", "HYG: 257 bars (need ~504)"],
            ),
            H.CheckResult(title="pf_oos anomalies", passed=True, findings=[]),
            H.CheckResult(
                title="Dead paper trials", passed=False,
                findings=["SATS: promoted 2 days ago, dispatch never fired"],
            ),
            H.CheckResult(title="Iteration failures (24h)", passed=True, findings=[]),
        ],
    )


def test_to_markdown_includes_header_and_timestamp():
    md = _sample_brief().to_markdown()
    assert md.startswith("# Research Health")
    assert "2023-11-14" in md  # 1_700_000_000 -> 2023-11-14T22:13:20Z
    assert "**Universe:** 16 tickers (6 discovering)" in md
    assert "**LLM spend today:** $0.38" in md


def test_to_markdown_flag_sections_have_count_and_findings():
    md = _sample_brief().to_markdown()
    assert "## Data shortfalls — FLAG (2)" in md
    assert "- XLK: 257 bars (need ~504)" in md
    assert "- HYG: 257 bars (need ~504)" in md
    assert "## Dead paper trials — FLAG (1)" in md
    assert "- SATS: promoted 2 days ago" in md


def test_to_markdown_ok_sections_are_single_line():
    md = _sample_brief().to_markdown()
    assert "## pf_oos anomalies — OK" in md
    assert "## Iteration failures (24h) — OK" in md
    # No bulleted findings under an OK section
    ok_idx = md.index("## pf_oos anomalies — OK")
    next_section_idx = md.index("## Dead paper trials", ok_idx)
    between = md[ok_idx:next_section_idx]
    assert "- " not in between


# --- HealthBrief.to_html -----------------------------------------------------


def test_to_html_has_expected_structure():
    html = _sample_brief().to_html()
    assert '<section class="research-health">' in html
    assert '<h2>Research Health' in html
    assert '<dl class="health-header">' in html
    assert '<dt>Universe</dt>' in html
    assert '<dd>16 tickers (6 discovering)</dd>' in html
    assert '<section class="check check-flag">' in html
    assert '<section class="check check-ok">' in html
    assert '<h3>Data shortfalls — FLAG (2)</h3>' in html
    assert '<h3>pf_oos anomalies — OK</h3>' in html


def test_to_html_escapes_user_content():
    brief = H.HealthBrief(
        generated_at=1_700_000_000,
        header={"Universe": "1 tickers (<script>alert(1)</script>)"},
        results=[
            H.CheckResult(title="X", passed=False, findings=["<script>evil</script>"]),
        ],
    )
    html = brief.to_html()
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


# --- check_pf_inf: growth ticker uses best_cagr_oos -------------------------


def test_health_absurd_detector_uses_cagr_column_for_growth(conn=None):
    """Growth tickers' absurd-CAGR values must be flagged from best_cagr_oos,
    not from best_pf_oos which now only holds profit-factor.
    Uses 2.5e10 for best_cagr_oos so it exceeds HEALTH_PF_OOS_ABSURD_THRESHOLD (1e10)."""
    import time as _time

    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript("""
        CREATE TABLE IF NOT EXISTS ticker_state (
            id INTEGER PRIMARY KEY, ticker TEXT UNIQUE NOT NULL,
            phase TEXT NOT NULL, best_pf_oos REAL, best_cagr_oos REAL,
            best_strategy_id INTEGER, retired INTEGER DEFAULT 0,
            updated_at INTEGER NOT NULL
        );
    """)
    # MSTR: small pf_oos (sensible profit-factor), absurd CAGR (artifact > 1e10) — must flag.
    c.execute(
        "INSERT INTO ticker_state (ticker, phase, best_pf_oos, best_cagr_oos, updated_at) "
        "VALUES ('MSTR','no_edge', 2.5, 2.5e10, ?)",
        (int(_time.time()),),
    )
    issues = H.check_pf_inf(c)
    # The growth ticker MSTR should be flagged because best_cagr_oos is absurd,
    # not because best_pf_oos is (it's a sensible 2.5).
    assert not issues.passed, f"expected MSTR flagged; got passed=True findings={issues.findings}"
    assert any("MSTR" in f for f in issues.findings), (
        f"expected MSTR in findings; got {issues.findings}"
    )


# --- write_latest_brief ------------------------------------------------------


def test_write_latest_brief_creates_file_with_expected_shape(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "UNIVERSE", ["SPY"])
    monkeypatch.setattr(config, "HEALTH_MIN_BARS_FOR_WF", 10)
    conn = _make_full_conn()
    path = H.write_latest_brief(conn, reports_dir=tmp_path)
    assert path.exists()
    assert path.parent == tmp_path
    assert path.name.startswith("research_health_")
    assert path.suffix == ".md"
    content = path.read_text()
    assert content.startswith("# Research Health")
    assert "Universe" in content


def test_write_latest_brief_defaults_to_reports_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "UNIVERSE", ["SPY"])
    monkeypatch.setattr(config, "HEALTH_MIN_BARS_FOR_WF", 10)
    monkeypatch.setattr(config, "REPORTS_DIR", tmp_path)
    conn = _make_full_conn()
    path = H.write_latest_brief(conn)
    assert path.parent == tmp_path
