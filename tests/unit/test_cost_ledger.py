"""Cost ledger tests."""
from bullbot.risk import cost_ledger


def test_append_and_sum_by_category(db_conn):
    cost_ledger.append(db_conn, ts=1000, category="llm", ticker="AAPL", amount_usd=0.04, details={"model": "opus"})
    cost_ledger.append(db_conn, ts=1001, category="llm", ticker="TSLA", amount_usd=0.05, details=None)
    cost_ledger.append(db_conn, ts=1002, category="data_uw", ticker="AAPL", amount_usd=0.0, details=None)

    assert cost_ledger.cumulative_llm_usd(db_conn) == 0.09
    assert cost_ledger.cumulative_by_ticker(db_conn, "AAPL")["llm"] == 0.04


def test_can_afford_returns_true_by_default(db_conn):
    assert cost_ledger.can_afford(db_conn, 0.10, ceiling_usd=1000.0) is True


def test_can_afford_returns_false_when_at_ceiling(db_conn):
    for i in range(30):
        cost_ledger.append(db_conn, ts=i, category="llm", ticker="X", amount_usd=35.0, details=None)
    assert cost_ledger.can_afford(db_conn, 0.10, ceiling_usd=1000.0) is False
