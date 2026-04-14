"""
Strategy abstract base class + StrategySnapshot data container.

Every strategy is a subclass that reads a StrategySnapshot and returns a
Signal or None. Strategies are deterministic at execution time — no LLM
calls inside evaluate(). LLM reasoning happens ONLY in the evolver
proposer at parameter-tuning time.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from bullbot.data.schemas import Bar, OptionContract, Signal


@dataclass(frozen=True)
class StrategySnapshot:
    """Everything a strategy needs to decide one signal."""
    ticker: str
    asof_ts: int
    spot: float
    bars_1d: list[Bar]
    indicators: dict[str, float]
    atm_greeks: dict[str, float]
    iv_rank: float
    regime: str   # 'bull' | 'bear' | 'chop'
    chain: list[OptionContract]
    market_brief: str = ""   # Daily market regime brief (empty during backtesting)
    ticker_brief: str = ""   # Daily per-ticker brief (empty during backtesting)


class Strategy(ABC):
    """Abstract base class for all strategies."""

    CLASS_NAME: str = ""
    CLASS_VERSION: int = 1

    def __init__(self, params: dict[str, Any]) -> None:
        self.params = params

    @abstractmethod
    def evaluate(
        self,
        snapshot: StrategySnapshot,
        open_positions: list[dict[str, Any]],
        **kwargs: Any,
    ) -> Signal | None:
        """Return a Signal to open/close a position, or None to stand pat."""

    @abstractmethod
    def max_loss_per_contract(self) -> float:
        """Max dollar loss per contract for position sizing."""
