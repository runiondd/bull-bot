"""Tests that the lifted CSS contains expected design tokens."""
from bullbot.dashboard import styles_css


def test_styles_css_contains_oklch_tokens():
    assert "oklch(15% 0.005 250)" in styles_css.CSS  # --bg-0
    assert "oklch(72% 0.16 145)" in styles_css.CSS   # --pos


def test_styles_css_contains_chip_classes():
    for cls in ("chip.live", "chip.paper", "chip.discovering",
                "chip.no_edge", "chip.pass", "chip.fail",
                "chip.warn", "chip.open", "chip.closed"):
        assert f".{cls}" in styles_css.CSS, f"missing .{cls}"


def test_styles_css_contains_density_modes():
    assert '[data-density="comfortable"]' in styles_css.CSS
    assert '[data-density="compact"]' in styles_css.CSS


def test_styles_css_contains_tnum_feature():
    """tnum is required for column alignment of monospace numbers."""
    assert '"tnum"' in styles_css.CSS
