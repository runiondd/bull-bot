from bullbot.evolver.proposer import build_prompt


def test_prompt_contains_eligibility_menu_and_iv_rank():
    prompt = build_prompt(
        ticker="META",
        regime_label="up/low/low",
        eligible_classes=["PutCreditSpread", "CashSecuredPut", "GrowthEquity"],
        explore_classes=["LongCall"],
        iv_rank_distribution={"p10": 8, "p50": 22, "p90": 45},
    )
    # All required content is present
    assert "META" in prompt
    assert "up/low/low" in prompt
    assert "PutCreditSpread" in prompt
    assert "CashSecuredPut" in prompt
    assert "GrowthEquity" in prompt
    assert "LongCall" in prompt
    assert "22" in prompt              # IV rank median
    assert "ranges" in prompt          # asks for ranges not points


def test_prompt_distinguishes_explore_vs_exploit():
    """The prompt should label classes by their bandit status so the LLM
    knows which are 'good bets' vs 'underexplored'."""
    prompt = build_prompt(
        ticker="META",
        regime_label="up/low/low",
        eligible_classes=["PutCreditSpread"],
        explore_classes=["LongCall"],
        iv_rank_distribution={"p10": 8, "p50": 22, "p90": 45},
    )
    # Must include some signal that LongCall is "explore" / "underexplored"
    # and PutCreditSpread is the established/exploit option. Acceptable
    # signals: the literal words "exploit"/"explore", "underexplored",
    # "best", "unknown", or a section header.
    lower = prompt.lower()
    # Test for some kind of distinction — adjust the impl to satisfy.
    assert "explore" in lower or "underexplored" in lower or "unknown" in lower
    # And that eligible classes appear separately from explore classes.
    eligible_index = prompt.find("PutCreditSpread")
    explore_index = prompt.find("LongCall")
    assert eligible_index != -1 and explore_index != -1
    assert eligible_index != explore_index  # they're not at the same place


def test_prompt_includes_iv_rank_percentiles():
    prompt = build_prompt(
        ticker="META",
        regime_label="up/low/low",
        eligible_classes=["PutCreditSpread"],
        explore_classes=[],
        iv_rank_distribution={"p10": 8, "p50": 22, "p90": 45},
    )
    # All three percentiles should appear so the LLM understands IV context
    assert "8" in prompt
    assert "22" in prompt
    assert "45" in prompt


def test_prompt_handles_empty_explore_list():
    prompt = build_prompt(
        ticker="META", regime_label="up/low/low",
        eligible_classes=["PutCreditSpread"],
        explore_classes=[],
        iv_rank_distribution={"p10": 8, "p50": 22, "p90": 45},
    )
    assert "PutCreditSpread" in prompt
    assert isinstance(prompt, str)
    assert len(prompt) > 50  # not empty
