"""
Strategy class registry + canonicalization helpers.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from bullbot.strategies.base import Strategy
from bullbot.strategies.call_credit_spread import CallCreditSpread
from bullbot.strategies.cash_secured_put import CashSecuredPut
from bullbot.strategies.iron_condor import IronCondor
from bullbot.strategies.long_call import LongCall
from bullbot.strategies.long_put import LongPut
from bullbot.strategies.put_credit_spread import PutCreditSpread


class UnknownStrategyError(KeyError):
    pass


_REGISTRY: dict[str, type[Strategy]] = {
    "PutCreditSpread": PutCreditSpread,
    "CallCreditSpread": CallCreditSpread,
    "IronCondor": IronCondor,
    "CashSecuredPut": CashSecuredPut,
    "LongCall": LongCall,
    "LongPut": LongPut,
}


def get_class(class_name: str) -> type[Strategy]:
    try:
        return _REGISTRY[class_name]
    except KeyError:
        raise UnknownStrategyError(class_name)


def list_all_names() -> list[str]:
    return sorted(_REGISTRY.keys())


def canonicalize_params(params: dict[str, Any]) -> str:
    return json.dumps(params, sort_keys=True, separators=(",", ":"))


def params_hash(params: dict[str, Any]) -> str:
    return hashlib.sha1(canonicalize_params(params).encode("utf-8")).hexdigest()


def materialize(class_name: str, params: dict[str, Any]) -> Strategy:
    cls = get_class(class_name)
    return cls(params=params)
