from bullbot.dashboard import fmt


def test_fmt_money_basic():
    assert fmt.fmt_money(0) == "$0.00"
    assert fmt.fmt_money(1234.56) == "$1,234.56"
    assert fmt.fmt_money(-89.10) == "-$89.10"
    assert fmt.fmt_money(None) == "—"


def test_fmt_money_signed_positive():
    assert fmt.fmt_money(100, signed=True) == "+$100.00"


def test_fmt_money_decimals_zero_for_large_values():
    assert fmt.fmt_money(50_000) == "$50,000"
    assert fmt.fmt_money(50_000, decimals=2) == "$50,000.00"


def test_fmt_pct():
    assert fmt.fmt_pct(0) == "0.0%"
    assert fmt.fmt_pct(0.42) == "42.0%"
    assert fmt.fmt_pct(0.42, signed=True) == "+42.0%"
    assert fmt.fmt_pct(-0.05) == "-5.0%"
    assert fmt.fmt_pct(None) == "—"


def test_pnl_class():
    assert fmt.pnl_class(0) == "muted"
    assert fmt.pnl_class(None) == "muted"
    assert fmt.pnl_class(1) == "pos"
    assert fmt.pnl_class(-1) == "neg"


def test_phase_class():
    assert fmt.phase_class("live") == "live"
    assert fmt.phase_class("paper_trial") == "paper"
    assert fmt.phase_class("discovering") == "discovering"
    assert fmt.phase_class("no_edge") == "no_edge"
    assert fmt.phase_class("anything_else") == "no_edge"  # safe default


def test_phase_label():
    assert fmt.phase_label("paper_trial") == "paper trial"
    assert fmt.phase_label("no_edge") == "no edge"
    assert fmt.phase_label("live") == "live"
