"""Optional chart of the ranked results (requires matplotlib).

A horizontal stacked bar per recommendation shows how each criterion
contributes to the total utility. Categorical hues are assigned in a fixed
order (one hue per criterion, never cycled); bars are thin with a surface gap
between segments; text uses neutral ink, never the series colour.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from .models import RankedResult
from .ranking import WEIGHTS

PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
SURFACE, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e6e5e1"


def save_utility_chart(results: Sequence[RankedResult], path: Path, title: str = "Craveable: utility by criterion") -> Path:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    criteria = list(WEIGHTS)                 # fixed order -> fixed hue
    labels = [f"#{r.rank} {r.assignment.restaurant.name}" for r in results][::-1]
    rows = [r.components for r in results][::-1]

    fig_h = 1.2 + 0.55 * len(rows)
    fig, ax = plt.subplots(figsize=(9, fig_h), dpi=150)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)

    left = [0.0] * len(rows)
    for i, crit in enumerate(criteria):
        vals = [row.get(crit, 0.0) for row in rows]
        ax.barh(labels, vals, left=left, height=0.42, color=PALETTE[i], edgecolor=SURFACE, linewidth=1.5, label=crit)
        left = [l + v for l, v in zip(left, vals)]
    for y, total in enumerate(left):
        ax.text(total + 0.01, y, f"{total:.3f}", va="center", ha="left", fontsize=9, color=INK)

    ax.set_xlim(0, 1.0)
    ax.set_xlabel("utility (weighted sum, 0-1)", color=INK2, fontsize=9)
    ax.tick_params(colors=INK2, labelsize=9)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.xaxis.grid(True, color=GRID, linewidth=1)
    ax.set_axisbelow(True)
    ax.set_title(title, loc="left", fontsize=11, color=INK)
    ax.legend(ncol=len(criteria), loc="upper center", bbox_to_anchor=(0.5, -0.28 if len(rows) > 2 else -0.45),
              frameon=False, fontsize=8, handlelength=1.0, columnspacing=1.2)
    fig.tight_layout()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)
    return path
