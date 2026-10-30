"""Static figures for the README and for people who'd rather not open a web map.

python -m shadelines.figures      # after shadelines.build; writes site/figures/*.png
"""

from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.colors import BoundaryNorm, ListedColormap  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

GRADE_COLORS = {"A": "#76a865", "B": "#7cb5bd", "C": "#e4cb3f", "D": "#d9838d"}
CANOPY = ["#f3eccd", "#c7d9a0", "#8dbb6f", "#4f8f45", "#215f2c"]
INK, MUTED, PAPER = "#23201b", "#6b645a", "#fffdf8"


def _base(ax, city):
    city.boundary.plot(ax=ax, color=MUTED, linewidth=0.6, linestyle=(0, (3, 3)))
    ax.set_axis_off()


def maps(gpkg: Path, out: Path) -> Path:
    zones = gpd.read_file(gpkg, layer="holc_1936_with_2021_measures")
    city = gpd.read_file(gpkg, layer="seattle_city_limits")
    graded = zones[zones["grade"].isin(GRADE_COLORS)]

    fig, axes = plt.subplots(1, 2, figsize=(11, 8), facecolor=PAPER)
    bounds = graded.total_bounds
    pad = 800
    for ax in axes:
        _base(ax, city)
        ax.set_xlim(bounds[0] - pad, bounds[2] + pad)
        ax.set_ylim(bounds[1] - pad, bounds[3] + pad)

    graded.plot(ax=axes[0], color=graded["grade"].map(GRADE_COLORS), edgecolor=INK, linewidth=0.25)
    axes[0].set_title("1936 HOLC grade", color=INK, fontsize=13, loc="left")
    axes[0].legend(
        handles=[Patch(color=c, label=g) for g, c in GRADE_COLORS.items()], loc="lower left", frameon=False, ncol=4
    )

    edges = [0, 10, 20, 30, 40, 100]
    graded.plot(
        ax=axes[1],
        column="canopy_pct_2021",
        cmap=ListedColormap(CANOPY),
        norm=BoundaryNorm(edges, len(CANOPY)),
        edgecolor=INK,
        linewidth=0.25,
        missing_kwds={"color": "#cfc8bb"},
    )
    axes[1].set_title("Tree canopy in 2021, same areas", color=INK, fontsize=13, loc="left")
    labels = [f"{a}–{b}%" for a, b in zip(edges[:-2], edges[1:-1], strict=True)] + [f"{edges[-2]}%+"]
    axes[1].legend(
        handles=[Patch(color=c, label=lab) for c, lab in zip(CANOPY, labels, strict=True)],
        loc="lower left",
        frameon=False,
        ncol=3,
    )
    fig.text(
        0.01,
        0.01,
        "HOLC areas: Mapping Inequality (CC BY-NC-SA 4.0). Canopy: City of Seattle 2021 assessment, by tract, "
        "split by area. Dashed line: today's city limits.",
        fontsize=8,
        color=MUTED,
    )
    fig.tight_layout()
    path = out / "maps.png"
    fig.savefig(path, dpi=150, facecolor=PAPER)
    plt.close(fig)
    return path


def by_grade(summary: Path, out: Path) -> Path:
    s = json.loads(summary.read_text())
    grades = list(GRADE_COLORS)
    fig, axes = plt.subplots(1, 3, figsize=(11, 3.4), facecolor=PAPER)
    panels = [
        ("canopy_pct_2021", "Tree canopy, 2021", "{:.0f}%"),
        ("people_of_color_pct", "People of color", "{:.0f}%"),
        ("median_household_income", "Household income (approx.)", "${:,.0f}"),
    ]
    for ax, (key, title, f) in zip(axes, panels, strict=True):
        vals = [s["grades"][g][key] or 0 for g in grades]
        ax.bar(grades, vals, color=[GRADE_COLORS[g] for g in grades], width=0.62)
        city = s["city"][key]
        if city is not None:
            ax.axhline(city, color=MUTED, linestyle=(0, (4, 3)), linewidth=1)
            ax.text(3.45, city, "city", color=MUTED, fontsize=8, va="bottom", ha="right")
        for i, v in enumerate(vals):
            ax.text(i, v, f.format(v), ha="center", va="bottom", fontsize=9, color=INK)
        ax.set_title(title, loc="left", color=INK, fontsize=11)
        ax.set_facecolor(PAPER)
        ax.spines[["top", "right", "left"]].set_visible(False)
        ax.set_yticks([])
    fig.suptitle("Seattle's 1936 HOLC grades, measured today", x=0.01, ha="left", color=INK, fontsize=12)
    fig.tight_layout()
    path = out / "by_grade.png"
    fig.savefig(path, dpi=150, facecolor=PAPER)
    plt.close(fig)
    return path


def main() -> None:
    out = Path("site/figures")
    out.mkdir(parents=True, exist_ok=True)
    print(maps(Path("data/processed/shadelines.gpkg"), out))
    print(by_grade(Path("site/data/summary.json"), out))


if __name__ == "__main__":
    main()
