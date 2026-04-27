"""Format helpers ported from dashboard/handoff/components-shell.jsx fmtMoney/fmtPct.

Pure functions, no side effects. Used by templates.py and tabs.py to
keep money/percent/PnL formatting consistent across the dashboard.
"""
from __future__ import annotations


def fmt_money(v: float | None, *, signed: bool = False, decimals: int | None = None) -> str:
    """Format a dollar amount. None → em-dash. Negatives use a minus sign.
    decimals defaults to 0 for |v| >= 10_000, else 2."""
    if v is None:
        return "—"
    if decimals is None:
        decimals = 0 if abs(v) >= 10_000 else 2
    sign = "-" if v < 0 else ("+" if signed and v > 0 else "")
    abs_v = abs(v)
    if decimals == 0:
        formatted = f"{abs_v:,.0f}"
    else:
        formatted = f"{abs_v:,.{decimals}f}"
    return f"{sign}${formatted}"


def fmt_pct(v: float | None, *, signed: bool = False, decimals: int = 1) -> str:
    """Format a fraction as a percent. None → em-dash."""
    if v is None:
        return "—"
    sign = "" if v < 0 else ("+" if signed else "")
    return f"{sign}{v * 100:.{decimals}f}%"


def pnl_class(v: float | None) -> str:
    """CSS class for a P&L value: 'pos', 'neg', or 'muted'."""
    if v is None or v == 0:
        return "muted"
    return "pos" if v > 0 else "neg"


_PHASE_TO_CHIP = {
    "live": "live",
    "paper_trial": "paper",
    "discovering": "discovering",
    "no_edge": "no_edge",
}


def phase_class(phase: str) -> str:
    """Map a ticker_state.phase to its chip CSS class."""
    return _PHASE_TO_CHIP.get(phase, "no_edge")


def phase_label(phase: str) -> str:
    """Human-readable phase name for chip labels."""
    return phase.replace("_", " ")
