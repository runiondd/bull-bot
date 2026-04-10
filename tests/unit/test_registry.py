"""Strategy registry tests."""
import hashlib
import json

import pytest

from bullbot.strategies import registry
from bullbot.strategies.put_credit_spread import PutCreditSpread


def test_get_class_by_name():
    assert registry.get_class("PutCreditSpread") is PutCreditSpread


def test_get_class_unknown_raises():
    with pytest.raises(registry.UnknownStrategyError):
        registry.get_class("NonExistentStrategy")


def test_canonicalize_params_sorts_keys():
    canon = registry.canonicalize_params({"b": 2, "a": 1})
    assert canon == '{"a":1,"b":2}'


def test_params_hash_stable():
    h1 = registry.params_hash({"dte": 14, "delta": 0.25})
    h2 = registry.params_hash({"delta": 0.25, "dte": 14})
    assert h1 == h2


def test_materialize_creates_configured_instance():
    s = registry.materialize("PutCreditSpread", {"dte": 14, "short_delta": 0.25, "width": 5, "iv_rank_min": 50})
    assert isinstance(s, PutCreditSpread)
    assert s.params["dte"] == 14


def test_list_all_names_includes_six_seeds():
    names = set(registry.list_all_names())
    assert {"PutCreditSpread", "CallCreditSpread", "IronCondor",
            "CashSecuredPut", "LongCall", "LongPut"} <= names
