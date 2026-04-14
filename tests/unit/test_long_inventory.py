"""Tests for long_inventory data layer."""
import csv
import sqlite3
import tempfile
from pathlib import Path

from bullbot.data import long_inventory


def test_active_inventory_returns_only_active(db_conn):
    db_conn.execute(
        "INSERT INTO long_inventory (account, ticker, kind, quantity, added_at) VALUES (?, ?, ?, ?, ?)",
        ("ira", "TSLA", "shares", 100, 1000),
    )
    db_conn.execute(
        "INSERT INTO long_inventory (account, ticker, kind, quantity, added_at, removed_at) VALUES (?, ?, ?, ?, ?, ?)",
        ("ira", "TSLA", "shares", 50, 900, 950),
    )
    rows = long_inventory.active_inventory(db_conn, "TSLA")
    assert len(rows) == 1
    assert rows[0]["quantity"] == 100


def test_active_inventory_filters_by_ticker(db_conn):
    db_conn.execute(
        "INSERT INTO long_inventory (account, ticker, kind, quantity, added_at) VALUES (?, ?, ?, ?, ?)",
        ("ira", "TSLA", "shares", 100, 1000),
    )
    db_conn.execute(
        "INSERT INTO long_inventory (account, ticker, kind, quantity, added_at) VALUES (?, ?, ?, ?, ?)",
        ("ira", "NVDA", "shares", 200, 1000),
    )
    rows = long_inventory.active_inventory(db_conn, "TSLA")
    assert len(rows) == 1
    assert rows[0]["ticker"] == "TSLA"


def test_active_inventory_filters_by_account(db_conn):
    db_conn.execute(
        "INSERT INTO long_inventory (account, ticker, kind, quantity, added_at) VALUES (?, ?, ?, ?, ?)",
        ("ira", "TSLA", "shares", 100, 1000),
    )
    db_conn.execute(
        "INSERT INTO long_inventory (account, ticker, kind, quantity, added_at) VALUES (?, ?, ?, ?, ?)",
        ("taxable", "TSLA", "shares", 200, 1000),
    )
    rows = long_inventory.active_inventory(db_conn, "TSLA", account="taxable")
    assert len(rows) == 1
    assert rows[0]["account"] == "taxable"
    assert rows[0]["quantity"] == 200


def test_total_coverable_contracts_calls_only(db_conn):
    # 1 call contract
    db_conn.execute(
        "INSERT INTO long_inventory (account, ticker, kind, strike, expiry, quantity, added_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("ira", "TSLA", "call", 300.0, "2027-06-17", 1, 1000),
    )
    # 3 call contracts
    db_conn.execute(
        "INSERT INTO long_inventory (account, ticker, kind, strike, expiry, quantity, added_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("ira", "TSLA", "call", 350.0, "2028-01-21", 3, 1000),
    )
    # 160 shares -> floor(160/100) = 1
    db_conn.execute(
        "INSERT INTO long_inventory (account, ticker, kind, quantity, added_at) VALUES (?, ?, ?, ?, ?)",
        ("ira", "TSLA", "shares", 160, 1000),
    )
    total = long_inventory.total_coverable_contracts(db_conn, "TSLA")
    assert total == 5  # 1 + 3 + floor(160/100)


def test_seed_from_fidelity_csv(db_conn):
    header = [
        "Account Number", "Account Name", "Symbol", "Description", "Quantity",
        "Last Price", "Last Price Change", "Current Value", "Today's Gain/Loss Dollar",
        "Today's Gain/Loss Percent", "Total Gain/Loss Dollar", "Total Gain/Loss Percent",
        "Percent Of Account", "Cost Basis Total", "Average Cost Basis", "Type",
    ]
    rows_data = [
        ["233084385", "IRA", " -TSLA270617C300", "TSLA JUN 17 2027 $300 CALL", "2",
         "150.00", "+1.00", "30000.00", "+200.00", "+0.67%", "+5000.00", "+20.00%",
         "10.00%", "25000.00", "125.00", "Cash"],
        ["X59844055", "TAXABLE", "TSLA", "TESLA INC COM", "160",
         "350.00", "+5.00", "56000.00", "+800.00", "+1.45%", "+10000.00", "+21.74%",
         "30.00%", "46000.00", "287.50", "Cash"],
        ["233084385", "IRA", "SPAXX**", "FID GOVT MM FD", "5000",
         "1.00", "0.00", "5000.00", "0.00", "0.00%", "0.00", "0.00%",
         "5.00%", "5000.00", "1.00", "Cash"],
    ]

    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        for r in rows_data:
            writer.writerow(r)
        csv_path = f.name

    count = long_inventory.seed_from_fidelity_csv(db_conn, csv_path)
    assert count == 2

    all_rows = long_inventory.active_inventory(db_conn, "TSLA")
    assert len(all_rows) == 2

    # Verify call row
    calls = [r for r in all_rows if r["kind"] == "call"]
    assert len(calls) == 1
    assert calls[0]["account"] == "ira"
    assert calls[0]["strike"] == 300.0
    assert calls[0]["expiry"] == "2027-06-17"
    assert calls[0]["quantity"] == 2
    assert calls[0]["cost_basis_per"] == 125.0

    # Verify shares row
    shares = [r for r in all_rows if r["kind"] == "shares"]
    assert len(shares) == 1
    assert shares[0]["account"] == "taxable"
    assert shares[0]["quantity"] == 160
    assert shares[0]["cost_basis_per"] == 287.50

    Path(csv_path).unlink()
