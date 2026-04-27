from bullbot.dashboard import svg_charts


def test_sparkline_returns_empty_for_short_data():
    """0 or 1 data point: empty string (matches JSX behavior)."""
    assert svg_charts.sparkline_svg([]) == ""
    assert svg_charts.sparkline_svg([1.0]) == ""


def test_sparkline_renders_polyline():
    svg = svg_charts.sparkline_svg([100.0, 110.0, 105.0, 120.0])
    assert svg.startswith("<svg")
    assert "polyline" in svg
    assert 'class="spark"' in svg


def test_sparkline_uses_pos_color_for_uptrend():
    svg = svg_charts.sparkline_svg([100.0, 110.0])
    assert "var(--pos)" in svg


def test_sparkline_uses_neg_color_for_downtrend():
    svg = svg_charts.sparkline_svg([110.0, 100.0])
    assert "var(--neg)" in svg


def test_equity_chart_renders_with_gridlines_and_labels():
    data = [265000.0 + i * 100 for i in range(30)]
    svg = svg_charts.equity_chart_svg(data)
    assert svg.startswith("<svg")
    assert 'class="equity-chart"' in svg
    assert "polyline" in svg
    # 5 horizontal gridlines (4 ticks + bottom)
    assert svg.count("<line") >= 4
    # x-axis labels
    assert "30d ago" in svg
    assert "today" in svg


def test_equity_chart_handles_empty_data():
    """Empty input: single-line flat-line placeholder, no crash."""
    svg = svg_charts.equity_chart_svg([])
    assert svg.startswith("<svg")
    # Should still be a valid <svg> with the chart container; can be a flat
    # line at zero or just an empty plot — the only requirement is it doesn't
    # raise and the page can embed it.
