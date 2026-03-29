"""
Auto-layout for entity positions on a Manim canvas.
Places entities in a grid or circular layout depending on count.
"""

import math


# Manim coordinate space roughly -7 to 7 on x, -4 to 4 on y
CANVAS_X = (-5.0, 5.0)
CANVAS_Y = (-3.0, 3.0)

COLORS = ["blue", "green", "red", "orange", "purple", "teal", "yellow", "pink"]


def grid_layout(count: int) -> list[tuple[float, float]]:
    """Place entities in a centered grid."""
    if count == 0:
        return []
    if count == 1:
        return [(0.0, 0.0)]
    if count == 2:
        return [(-2.5, 0.0), (2.5, 0.0)]

    cols = min(count, math.ceil(math.sqrt(count)))
    rows = math.ceil(count / cols)

    x_step = (CANVAS_X[1] - CANVAS_X[0]) / max(cols, 1)
    y_step = (CANVAS_Y[1] - CANVAS_Y[0]) / max(rows, 1)

    positions = []
    for idx in range(count):
        row = idx // cols
        col = idx % cols
        x = CANVAS_X[0] + x_step * (col + 0.5)
        y = CANVAS_Y[1] - y_step * (row + 0.5)
        positions.append((round(x, 1), round(y, 1)))

    return positions


def color_for_index(idx: int) -> str:
    """Return a color for a given entity index."""
    return COLORS[idx % len(COLORS)]
