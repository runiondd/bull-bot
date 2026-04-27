"""Inline SVG chart generators ported from components-shell.jsx.

Pure functions: data in, SVG string out. No external chart libraries.
The SVG uses CSS variables (--pos, --neg, --accent, --line, --fg-2) so
colors follow the active theme.
"""
from __future__ import annotations


def sparkline_svg(data: list[float], width: int = 120, height: int = 32,
                  color: str | None = None) -> str:
    """Render a tiny inline sparkline. Empty/single-point data → empty string.

    Stroke color follows trend direction (last vs first) unless `color` is
    explicitly provided.
    """
    if not data or len(data) < 2:
        return ""
    mn, mx = min(data), max(data)
    rng = mx - mn or 1.0
    step = width / (len(data) - 1)

    pts: list[str] = []
    for i, v in enumerate(data):
        x = i * step
        y = height - ((v - mn) / rng) * (height - 4) - 2
        pts.append(f"{x:.1f},{y:.1f}")
    pts_str = " ".join(pts)

    last, first = data[-1], data[0]
    is_up = last >= first
    stroke = color or ("var(--pos)" if is_up else "var(--neg)")
    fill = (f"color-mix(in oklab, {'var(--pos)' if is_up else 'var(--neg)'} "
            "14%, transparent)")

    return (
        f'<svg class="spark" width="{width}" height="{height}">'
        f'<polyline points="0,{height} {pts_str} {width},{height}" '
        f'fill="{fill}" stroke="none" />'
        f'<polyline points="{pts_str}" fill="none" stroke="{stroke}" '
        f'stroke-width="1.5" />'
        f'</svg>'
    )


def equity_chart_svg(data: list[float], height: int = 200) -> str:
    """Render the larger 30-day equity area chart with gridlines + labels.

    Empty data → an empty plot placeholder (still valid SVG).
    """
    w, h = 880, height
    pad = {"l": 48, "r": 12, "t": 14, "b": 22}

    if not data:
        return (
            f'<svg class="equity-chart" viewBox="0 0 {w} {h}" '
            f'preserveAspectRatio="none">'
            f'<text x="{w/2}" y="{h/2}" text-anchor="middle" '
            f'font-family="IBM Plex Mono, monospace" font-size="11" '
            f'fill="var(--fg-2)">No equity history yet</text>'
            f'</svg>'
        )

    mn, mx = min(data), max(data)
    rng = mx - mn or 1.0
    inner_w = w - pad["l"] - pad["r"]
    inner_h = h - pad["t"] - pad["b"]
    step = inner_w / (len(data) - 1) if len(data) > 1 else inner_w

    def y_for(v: float) -> float:
        return pad["t"] + inner_h - ((v - mn) / rng) * inner_h

    pts = " ".join(
        f"{pad['l'] + i * step:.1f},{y_for(v):.1f}"
        for i, v in enumerate(data)
    )

    # 5 gridlines + y-axis labels
    ticks = 4
    grid_parts: list[str] = []
    for i in range(ticks + 1):
        v = mn + (rng * i) / ticks
        y = y_for(v)
        label = f"${v / 1000:.0f}k"
        grid_parts.append(
            f'<line x1="{pad["l"]}" x2="{w - pad["r"]}" y1="{y:.1f}" y2="{y:.1f}" '
            f'stroke="var(--line)" stroke-dasharray="2 3" />'
            f'<text x="{pad["l"] - 6}" y="{y + 3:.1f}" text-anchor="end" '
            f'font-family="IBM Plex Mono, monospace" font-size="9.5" fill="var(--fg-2)">'
            f'{label}</text>'
        )

    # x-axis labels (start, mid, end)
    n = len(data)
    x_label_indices = [0] if n == 1 else [0, n // 2, n - 1]
    x_parts: list[str] = []
    for i in x_label_indices:
        x = pad["l"] + i * step
        if i == 0:
            text = "30d ago"
        elif i == n - 1:
            text = "today"
        else:
            text = f"{n - i}d"
        x_parts.append(
            f'<text x="{x:.1f}" y="{h - 6}" text-anchor="middle" '
            f'font-family="IBM Plex Mono, monospace" font-size="9.5" fill="var(--fg-2)">'
            f'{text}</text>'
        )

    return (
        f'<svg class="equity-chart" viewBox="0 0 {w} {h}" '
        f'preserveAspectRatio="none">'
        f'{"".join(grid_parts)}'
        f'<polyline points="{pad["l"]},{h - pad["b"]} {pts} {w - pad["r"]},{h - pad["b"]}" '
        f'fill="color-mix(in oklab, var(--accent) 12%, transparent)" />'
        f'<polyline points="{pts}" fill="none" stroke="var(--accent)" stroke-width="1.5" />'
        f'{"".join(x_parts)}'
        f'</svg>'
    )
