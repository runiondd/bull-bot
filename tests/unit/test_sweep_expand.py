from bullbot.evolver.sweep import expand_spec, StrategySpec


def test_expand_produces_cartesian_product():
    spec = StrategySpec(
        class_name="PutCreditSpread",
        ranges={
            "short_delta": [0.15, 0.20, 0.25, 0.30],
            "width": [5, 10],
            "dte": [21, 30, 45],
            "iv_rank_min": [10, 20, 30, 40],
            "profit_target_pct": [0.5],
            "stop_loss_mult": [2.0],
        },
        max_loss_per_trade=350.0,
        stop_loss_pct=None,
    )
    cells = expand_spec(spec, n_cells_max=200)
    assert len(cells) == 4 * 2 * 3 * 4 * 1 * 1  # 96
    assert all("short_delta" in c.params for c in cells)
    assert cells[0].class_name == "PutCreditSpread"
    assert set(cells[0].params.keys()) == {"short_delta", "width", "dte", "iv_rank_min", "profit_target_pct", "stop_loss_mult"}
    assert cells[0].params == {"dte": 21, "iv_rank_min": 10, "profit_target_pct": 0.5, "short_delta": 0.15, "stop_loss_mult": 2.0, "width": 5}


def test_expand_respects_n_cells_max():
    spec = StrategySpec(
        class_name="IronCondor",
        ranges={
            "short_delta": [0.1, 0.15, 0.2, 0.25, 0.3],
            "width": [5, 10, 15],
            "dte": [21, 30, 45, 60],
        },
        max_loss_per_trade=500.0,
    )
    # 5*3*4 = 60 cells, but cap at 30
    cells = expand_spec(spec, n_cells_max=30)
    assert len(cells) == 30


def test_expand_returns_empty_when_cap_is_zero():
    spec = StrategySpec(
        class_name="PutCreditSpread",
        ranges={"short_delta": [0.15, 0.20], "width": [5, 10]},
        max_loss_per_trade=350.0,
    )
    assert expand_spec(spec, n_cells_max=0) == []
    assert expand_spec(spec, n_cells_max=-5) == []
