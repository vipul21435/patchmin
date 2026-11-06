"""Count the trials ddmin makes on synthetic oracles and plot them.

For each number of hunks n and each number k of needed hunks, the needed
hunks are placed at random (200 placements per point, fixed seed) and the
oracle is "every needed hunk is present". The script prints the mean and
the maximum number of trials and, with --plot, writes a line chart.

Run with:

    uv run --with matplotlib python examples/ddmin_cost.py --plot docs/ddmin_cost.png
"""

from __future__ import annotations

import argparse
import random
import statistics

from patchmin.ddmin import DdminTrace, ddmin

SIZES = [2, 4, 6, 8, 12, 16, 24, 32, 48, 64]
NEEDED = [1, 2, 4]


def trials(n: int, k: int, placements: int, rng: random.Random) -> list[int]:
    counts = []
    for _ in range(placements):
        needed = set(rng.sample(range(n), k))
        trace = DdminTrace()
        result = ddmin(list(range(n)), lambda s, needed=needed: needed <= set(s), trace)
        assert set(result) == needed
        counts.append(trace.tests)
    return counts


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--placements", type=int, default=200)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--plot", default=None, help="write a PNG chart here")
    args = ap.parse_args()
    rng = random.Random(args.seed)
    table: dict[int, list[tuple[int, float, int]]] = {k: [] for k in NEEDED}
    print("| hunks n | " + " | ".join(f"k={k} mean (max)" for k in NEEDED) + " | n^2+3n |")
    print("|---" * (len(NEEDED) + 2) + "|")
    for n in SIZES:
        cells = []
        for k in NEEDED:
            if k > n:
                cells.append("-")
                continue
            counts = trials(n, k, args.placements, rng)
            mean = statistics.fmean(counts)
            table[k].append((n, mean, max(counts)))
            cells.append(f"{mean:.1f} ({max(counts)})")
        print(f"| {n} | " + " | ".join(cells) + f" | {n * n + 3 * n} |")
    if args.plot:
        plot(table, args.plot)


def plot(table: dict[int, list[tuple[int, float, int]]], path: str) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    colors = {1: "#2a78d6", 2: "#eb6834", 4: "#1baf7a"}
    markers = {1: "o", 2: "s", 4: "^"}
    fig, ax = plt.subplots(figsize=(7, 4.2), dpi=150)
    fig.patch.set_facecolor("#fcfcfb")
    ax.set_facecolor("#fcfcfb")
    for k, rows in table.items():
        xs = [r[0] for r in rows]
        ys = [r[1] for r in rows]
        ax.plot(xs, ys, color=colors[k], linewidth=2, marker=markers[k], markersize=6,
                label=f"{k} needed")
        ax.annotate(f"{k} needed", (xs[-1], ys[-1]), xytext=(6, 0), textcoords="offset points",
                    va="center", fontsize=9, color="#52514e")
    ax.set_xlabel("hunks in the patch (n)", color="#52514e")
    ax.set_ylabel("ddmin trials, mean of 200 placements", color="#52514e")
    ax.set_title("ddmin trials on a monotone oracle", color="#0b0b0b", loc="left")
    ax.grid(axis="y", color="#e4e3df", linewidth=0.8)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color("#b5b4ae")
    ax.tick_params(colors="#52514e")
    ax.set_xlim(0, max(SIZES) * 1.15)
    ax.set_ylim(bottom=0)
    ax.legend(frameon=False, loc="upper left")
    fig.tight_layout()
    fig.savefig(path, facecolor=fig.get_facecolor())


if __name__ == "__main__":
    main()
