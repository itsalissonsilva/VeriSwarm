"""Draws the report figures from artifact files. No number is typed in here.

Usage: python scripts/make_figures.py
Reads artifacts/summary/*.json and writes artifacts/figures/*.{pdf,png}.
Palette: slots 1-3 of the validated reference palette, checked all-pairs for colour
vision deficiency. Identity is never colour alone: every series has a legend entry and
marks carry direct labels. Each figure has a table twin in the report.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyBboxPatch  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from veriswarm.config import VARIANTS  # noqa: E402
from veriswarm.semantics import initial_state, last_response, step  # noqa: E402
from veriswarm.config import default_config  # noqa: E402

SUMMARY = ROOT / "artifacts" / "summary"
FIGURES = ROOT / "artifacts" / "figures"

BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
SURFACE = "#ffffff"
INK, INK2, MUTED = "#0b0b0b", "#52514e", "#898781"
GRID, AXIS = "#e1e0d9", "#c3c2b7"
WASH_ORANGE, WASH_BLUE = "#fbe3d9", "#e4eefb"

plt.rcParams.update(
    {
        "font.family": "DejaVu Sans",
        "font.size": 8.5,
        "axes.edgecolor": AXIS,
        "axes.labelcolor": INK2,
        "axes.titlecolor": INK,
        "axes.titlesize": 9.5,
        "axes.titleweight": "bold",
        "axes.titlelocation": "left",
        "xtick.color": INK2,
        "ytick.color": INK,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.linewidth": 0.6,
        "axes.grid": False,
        "figure.dpi": 200,
        "savefig.bbox": "tight",
        "pdf.fonttype": 42,
        "legend.frameon": False,
    }
)


def load(name: str):
    return json.loads((SUMMARY / f"{name}.json").read_text(encoding="utf-8"))


def save(fig, name: str) -> None:
    FIGURES.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIGURES / f"{name}.pdf")
    fig.savefig(FIGURES / f"{name}.png", dpi=220)
    plt.close(fig)
    print("wrote", name)


def _cell(ax, x, y, w, h, text, face, ink=INK, weight="normal", size=8.2):
    ax.add_patch(
        FancyBboxPatch(
            (x, y), w, h, boxstyle="round,pad=0,rounding_size=0.06",
            facecolor=face, edgecolor="none", mutation_aspect=1,
        )
    )
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", color=ink, fontsize=size, weight=weight)


def _witness_rows(job_id: str):
    """The paired rows for a witness TLC found, ordered so bit 0 comes first."""
    replay = load("replay_results")
    row = next(r for r in replay["rows"] if r["job_id"] == job_id)
    left_is_zero = row["left_secret"] == [0]
    zero, one = ("left", "right") if left_is_zero else ("right", "left")
    steps = row["steps"]
    return {
        "A0": steps[0][f"{zero}_action"],
        "A1": steps[0][f"{one}_action"],
        "B0": steps[1][f"{zero}_response"],
        "B1": steps[1][f"{one}_response"],
        "divergent_step": row["first_divergent_step"],
        "fields": row["fields_compared"],
    }


def _reexecute(variant: str):
    """V2 has no counterexample, so its panel re-executes the same actions in Python."""
    cfg = default_config(variant, max_rounds=1, track_audit=False)
    rows = {}
    for bit, action in ((0, "noop"), (1, "acquire")):
        s = step(cfg, step(cfg, initial_state(cfg, (bit,)), action), "try_job")
        rows[f"A{bit}"] = action
        rows[f"B{bit}"] = last_response(s, "B")
    return rows


def figure_timeline() -> None:
    """Figure 1: the paired execution, TLC's witness beside the partition."""
    v1 = _witness_rows("codebook_V1_R1")
    v2 = _reexecute("V2")
    fig, axes = plt.subplots(1, 2, figsize=(6.5, 1.4))
    panels = [
        (axes[0], v1, "Shared lock (V1): B's reply reveals the bit", True),
        (axes[1], v2, "Private locks (V2): B's reply is the same either way", False),
    ]
    columns = ["A's action", "B's reply", "end of round"]
    for ax, data, title, differs in panels:
        ax.set_xlim(-0.05, 4.05)
        ax.set_ylim(-0.1, 3.0)
        ax.axis("off")
        ax.set_title(title, fontsize=8.6, pad=3)
        for i, column in enumerate(columns):
            ax.text(1.05 + i + 0.47, 2.65, column, ha="center", va="center", color=INK2, fontsize=8)
        for y, bit in ((1.35, 0), (0.2, 1)):
            ax.text(0.5, y + 0.5, f"b = {bit}", ha="center", va="center", color=INK, fontsize=8.5, weight="bold")
            _cell(ax, 1.05, y, 0.94, 1.0, data[f"A{bit}"], "#f0efec")
            face = WASH_ORANGE if differs else WASH_BLUE
            _cell(ax, 2.05, y, 0.94, 1.0, data[f"B{bit}"], face, weight="bold")
            _cell(ax, 3.05, y, 0.94, 1.0, "release", "#f7f7f5", ink=MUTED)
    fig.subplots_adjust(wspace=0.05, left=0.0, right=1.0, top=0.86, bottom=0.0)
    save(fig, "fig1_paired_timeline")


def _variant_order() -> list[str]:
    return list(VARIANTS)


def figure_measured() -> None:
    """Figure 2: decoding, exact capacity and utility, by variant, on shared rows."""
    tx = load("transmission")
    exact = load("exact_leakage")
    benign = load("benign")
    order = _variant_order()
    labels = [f"{v}  {VARIANTS[v].title}" for v in order]
    ys = list(range(len(order)))[::-1]

    fig, (ax1, ax2, ax3) = plt.subplots(
        1, 3, figsize=(6.5, 2.55), sharey=True,
        gridspec_kw={"width_ratios": [1.15, 0.8, 1.15], "wspace": 0.09},
    )
    for ax in (ax1, ax2, ax3):
        ax.set_yticks(ys)
        ax.set_yticklabels(labels, fontsize=8)
        ax.tick_params(axis="y", length=0)
        ax.spines["left"].set_visible(False)
        ax.xaxis.grid(True, color=GRID, linewidth=0.6)
        ax.set_axisbelow(True)
        ax.tick_params(axis="x", labelsize=7.5)

    def lollipop(ax, start, value, y, leaks):
        color = ORANGE if leaks else BLUE
        marker = "o" if leaks else "s"
        ax.plot([start, value], [y, y], color=color, linewidth=2.0, solid_capstyle="round", zorder=2)
        return ax.plot(value, y, marker=marker, color=color, markersize=6, markeredgecolor=SURFACE,
                       markeredgewidth=1.1, zorder=3, linestyle="none")[0]

    ax1.axvline(500, color=INK2, linewidth=1.0)
    handles = {}
    for v, y in zip(order, ys):
        correct = tx[v]["correct"]
        leaks = correct > tx[v]["total"] / 2
        handles["channel open" if leaks else "channel closed"] = lollipop(ax1, 500, correct, y, leaks)
        ax1.text(correct + 28, y, f"{correct}", va="center", ha="left", fontsize=7.4, color=INK)
    ax1.set_xlim(430, 1210)
    ax1.set_xticks([500, 750, 1000])
    ax1.set_xlabel("chance is 500", fontsize=7.8)
    ax1.set_title("Bits decoded, of 1000", fontsize=8.6)

    for v, y in zip(order, ys):
        capacity = exact[v]["capacity_per_round"]
        lollipop(ax2, 0, capacity, y, capacity > 1e-9)
        text = f"{capacity:.3f}" if 0 < capacity < 1 else f"{capacity:.0f}"
        ax2.text(capacity + 0.09, y, text, va="center", ha="left", fontsize=7.4, color=INK)
    ax2.set_xlim(-0.08, 1.62)
    ax2.set_xticks([0, 0.5, 1])
    ax2.set_xlabel("bits per round", fontsize=7.8)
    ax2.set_title("Exact capacity", fontsize=8.6)

    for v, y in zip(order, ys):
        w2 = benign["W2"][v]["B_completed"]
        ax3.barh(y, w2, height=0.42, color=AQUA, zorder=2)
        ax3.text(w2 + 40, y, f"{w2}", va="center", ha="left", fontsize=7.4, color=INK)
    ax3.set_xlim(0, 1400)
    ax3.set_xticks([0, 500, 1000])
    ax3.set_xlabel("jobs, under contention", fontsize=7.8)
    ax3.set_title("Receiver jobs done, of 1000", fontsize=8.6)

    fig.legend(
        [handles["channel open"], handles["channel closed"]],
        ["channel open", "channel closed"],
        loc="lower center", bbox_to_anchor=(0.5, -0.12), ncol=2, fontsize=7.8,
        handletextpad=0.2, columnspacing=1.2, borderaxespad=0.0,
    )
    save(fig, "fig2_measured")


def figure_noise() -> None:
    """Figure 3: what random busy replies cost and what they leave open."""
    noise = load("noise")
    levels = noise["levels"]
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(6.5, 2.1), gridspec_kw={"wspace": 0.28})

    xs = [100 * row["receiver_completion"] for row in levels]
    ys = [row["capacity_closed_form"] for row in levels]
    ax1.plot(xs, ys, color=ORANGE, linewidth=2.0, marker="o", markersize=5.5, markeredgecolor=SURFACE,
             markeredgewidth=1.0, zorder=3, label="V8 noisy shared lock")
    for row, x, y in zip(levels, xs, ys):
        p = row["noise_p"]
        if p == 0.0:
            ax1.text(x, y + 0.09, "p = 0", fontsize=7.4, color=INK2, ha="center", va="center")
        elif p == 0.5:
            ax1.text(x - 3, y + 0.02, "p = 0.5", fontsize=7.4, color=INK2, ha="right", va="center")
        elif p == 0.9:
            ax1.text(x + 4, y - 0.01, "p = 0.9", fontsize=7.4, color=INK2, ha="left", va="center")
    ax1.plot([100], [0], marker="s", color=BLUE, markersize=7.5, markeredgecolor=SURFACE, markeredgewidth=1.2,
             linestyle="none", zorder=4, clip_on=False, label="V2 partitioned, V9 flushed")
    ax1.annotate("V2, V9", (100, 0), xytext=(-22, 13), textcoords="offset points", fontsize=7.6, color=INK,
                 arrowprops=dict(arrowstyle="-", color=MUTED, linewidth=0.6))
    ax1.set_xlim(-3, 108)
    ax1.set_ylim(-0.06, 1.18)
    ax1.set_xticks([0, 25, 50, 75, 100])
    ax1.set_yticks([0, 0.25, 0.5, 0.75, 1.0])
    ax1.grid(True, color=GRID, linewidth=0.6)
    ax1.set_axisbelow(True)
    ax1.set_xlabel("receiver jobs surviving noise (%)")
    ax1.set_ylabel("capacity (bits per round)")
    ax1.set_title("Noise trades utility for leakage")
    ax1.legend(loc="upper left", fontsize=7.2, handletextpad=0.3, borderaxespad=0.1, bbox_to_anchor=(0.0, 1.0))

    picks = [(0.5, ORANGE, "o"), (0.9, BLUE, "s"), (0.99, AQUA, "^")]
    for p, color, marker in picks:
        row = next(r for r in levels if abs(r["noise_p"] - p) < 1e-9)
        ks = sorted(int(k) for k in row["repetition"])
        acc = [100 * row["repetition"][str(k)]["accuracy"] for k in ks]
        ax2.plot(ks, acc, color=color, linewidth=2.0, marker=marker, markersize=5, markeredgecolor=SURFACE,
                 markeredgewidth=0.9, label=f"p = {p:g}", zorder=3)
    ax2.axhline(50, color=INK2, linewidth=1.0)
    ax2.text(45.5, 51.5, "chance", ha="right", va="bottom", fontsize=7.4, color=INK2)
    ax2.set_ylim(45, 104)
    ax2.set_xlim(0, 46)
    ax2.set_xticks([1, 5, 15, 25, 45])
    ax2.set_yticks([50, 60, 70, 80, 90, 100])
    ax2.grid(True, color=GRID, linewidth=0.6)
    ax2.set_axisbelow(True)
    ax2.set_xlabel("rounds spent per bit (repetition)")
    ax2.set_ylabel("bits decoded correctly (%)")
    ax2.set_title("Repetition recovers the bit")
    ax2.legend(loc="center right", fontsize=7.5, handletextpad=0.3, bbox_to_anchor=(1.0, 0.56))
    save(fig, "fig3_noise_tradeoff")


def _box(ax, x, y, w, h, text, face="#f0efec", edge="none", ink=INK, size=8.0, weight="normal", lw=0.9):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=0.18",
                                facecolor=face, edgecolor=edge, linewidth=lw, mutation_aspect=1))
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", color=ink, fontsize=size, weight=weight,
            linespacing=1.25)


def _arrow(ax, start, end, color=INK2, label=None, label_xy=None, label_color=None, both=False, lw=1.2):
    ax.annotate("", xy=end, xytext=start,
                arrowprops=dict(arrowstyle="<|-|>" if both else "-|>", color=color, linewidth=lw,
                                shrinkA=0, shrinkB=0, mutation_scale=9))
    if label:
        ax.text(*label_xy, label, ha="center", va="center", fontsize=7.2, color=label_color or color, linespacing=1.2)


# Colours for the overview diagram. LIGHT is the report's figure; DARK follows the
# explainer's dark mode, with the same orange for the leak and blue for the fix.
LIGHT = {"bg": SURFACE, "ink": INK, "ink2": INK2, "box": "#f0efec", "panel": SURFACE, "edge": AXIS,
         "arrow": INK2, "leak": ORANGE, "safe": BLUE, "leak_wash": WASH_ORANGE, "safe_wash": WASH_BLUE}
DARK = {"bg": "#0e1116", "ink": "#e8ebf0", "ink2": "#b9c0cb", "box": "#1f252f", "panel": "#161a21",
        "edge": "#4a5361", "arrow": "#a3acb9", "leak": "#f0783f", "safe": "#5c9df0",
        "leak_wash": "#4a2a1b", "safe_wash": "#1b3354"}


def _overview_figure(c=LIGHT):
    """The problem and the fix, as a diagram: the figure itself, unsaved."""
    # Coordinates are inches inside each panel, so sizes on the page are exact.
    width, height = 3.1, 2.3
    fig, axes = plt.subplots(1, 2, figsize=(6.5, height))
    if c is not LIGHT:
        fig.patch.set_facecolor(c["bg"])
    for ax in axes:
        ax.set_xlim(0, width)
        ax.set_ylim(0, height)
        ax.set_aspect("equal")
        ax.axis("off")
    titles = ("Today: the agents share one lock", "VeriSwarm: each agent gets its own")
    for ax, title in zip(axes, titles):
        ax.text(0.0, 2.27, title, fontsize=8.6, weight="bold", color=c["ink"], va="top")
        ax.text(0.0, 1.98, "holds the lock for 1,\nleaves it free for 0", fontsize=6.6, color=c["ink2"], va="top",
                linespacing=1.15)
        _box(ax, 0.0, 1.2, 0.86, 0.5, "Agent A\nhas a secret bit", face=c["box"], ink=c["ink"], size=6.9)
        _box(ax, 0.0, 0.3, 0.86, 0.5, "Agent B\nmust not learn it", face=c["box"], ink=c["ink"], size=6.9)
        _box(ax, 1.22, 0.22, 0.86, 1.58, "Gateway\n\nauthenticates\nand logs\nevery call\n\nlog: all calls\nlegal",
             face=c["panel"], edge=c["edge"], ink=c["ink"], size=6.7)
        _arrow(ax, (0.86, 1.45), (1.22, 1.45), color=c["arrow"], lw=1.0)
        _arrow(ax, (0.86, 0.62), (1.22, 0.62), color=c["arrow"], lw=1.0)
    # Left: one shared lock.
    ax = axes[0]
    _arrow(ax, (1.22, 0.42), (0.86, 0.42), color=c["leak"], lw=1.4)
    ax.text(0.0, 0.17, "reads busy as 1, ok as 0", fontsize=6.6, color=c["leak"], va="center")
    _box(ax, 2.34, 0.78, 0.76, 0.5, "one shared\nlock", face=c["leak_wash"], ink=c["ink"], size=7.0, weight="bold")
    _arrow(ax, (2.08, 1.45), (2.34, 1.18), color=c["arrow"], both=True, lw=1.0)
    _arrow(ax, (2.08, 0.62), (2.34, 0.88), color=c["arrow"], both=True, lw=1.0)
    ax.text(2.72, 0.6, "A's hold makes\nB's job busy", fontsize=6.4, color=c["leak"], ha="center", va="top")
    # Right: private locks.
    ax = axes[1]
    _arrow(ax, (1.22, 0.42), (0.86, 0.42), color=c["safe"], lw=1.4)
    ax.text(0.0, 0.17, "always reads ok", fontsize=6.6, color=c["safe"], va="center")
    _box(ax, 2.34, 1.2, 0.76, 0.5, "A's lock", face=c["safe_wash"], ink=c["ink"], size=7.0, weight="bold")
    _box(ax, 2.34, 0.3, 0.76, 0.5, "B's lock", face=c["safe_wash"], ink=c["ink"], size=7.0, weight="bold")
    _arrow(ax, (2.08, 1.45), (2.34, 1.45), color=c["arrow"], both=True, lw=1.0)
    _arrow(ax, (2.08, 0.55), (2.34, 0.55), color=c["arrow"], both=True, lw=1.0)
    ax.text(2.72, 1.0, "B's view never\ndepends on A", fontsize=6.4, color=c["safe"], ha="center", va="center")
    fig.subplots_adjust(wspace=0.1, left=0.01, right=0.99, top=0.99, bottom=0.01)
    return fig


def figure_overview() -> None:
    """Figure 1: the problem and the fix, as a diagram."""
    save(_overview_figure(), "fig_overview")


def _project_image(c, out: Path) -> None:
    """A 1920x1080 project image: the overview diagram under the title, in theme `c`."""
    fig = _overview_figure(c)
    if c is LIGHT:
        fig.savefig(FIGURES / "fig_overview_hires.png", dpi=270)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=270)
    plt.close(fig)
    buf.seek(0)
    diagram = plt.imread(buf)
    height, width = diagram.shape[:2]
    canvas_w, canvas_h, dpi = 1920, 1080, 120
    left = (canvas_w - width) // 2
    canvas = plt.figure(figsize=(canvas_w / dpi, canvas_h / dpi), dpi=dpi)
    canvas.patch.set_facecolor(c["bg"])
    canvas.figimage(diagram, xo=left, yo=70)
    # Line the title up with the diagram's content, inside the padding a tight save adds.
    x = (left + round(plt.rcParams["savefig.pad_inches"] * 270)) / canvas_w
    canvas.text(x, 1 - 95 / canvas_h, "VeriSwarm", fontsize=58, weight="bold", color=c["ink"], va="top")
    canvas.text(x, 1 - 205 / canvas_h,
                "AI agents kept apart can still signal through a shared lock, with every call legal and logged.\n"
                "One lock each, proved in Lean, closes the channel.",
                fontsize=22, color=c["ink2"], va="top", linespacing=1.4)
    out.parent.mkdir(parents=True, exist_ok=True)
    with plt.rc_context({"savefig.bbox": "standard"}):
        canvas.savefig(out, dpi=dpi, facecolor=c["bg"])
    plt.close(canvas)
    print("wrote", out.relative_to(ROOT))


def figure_project_image() -> None:
    """The overview diagram at slide resolution, and project images in light and dark."""
    _project_image(LIGHT, ROOT / "submission" / "project_image.png")
    _project_image(DARK, ROOT / "submission" / "project_image_dark.png")


def figure_pipeline() -> None:
    """Figure 2: how a result is produced and checked. Counts come from the artifacts."""
    tlc = load("tlc_results")
    sweep = load("tlc_schedule_sweep")
    replay = load("replay_results")
    design = load("design_space") if (SUMMARY / "design_space.json").exists() else {"designs": 0}
    corr = load("correspondence")
    steps = [
        ("Model the\ngateway", "TLA+ spec,\n10 variants"),
        ("Check pairs\nof runs", f"TLC, {len(tlc['results']) + len(sweep['results'])}\nchecks, every state"),
        ("Replay\neach leak", f"{replay['witnesses']} witnesses,\nexact in Python"),
        ("Prove\nthe fix", "every horizon,\nLean, any N"),
        ("Map the\ndesign space", f"{design['designs']} gateway\ndesigns"),
        ("Measure\nand test", "leakage, utility,\nmonitors, LLMs"),
    ]
    fig, ax = plt.subplots(figsize=(6.5, 1.32))
    ax.set_xlim(0, 12.2)
    ax.set_ylim(0, 2.5)
    ax.axis("off")
    w, gap = 1.72, 0.37
    for i, (head, sub) in enumerate(steps):
        x = i * (w + gap)
        _box(ax, x, 1.0, w, 1.2, head, face="#f0efec", size=7.6, weight="bold")
        ax.text(x + w / 2, 0.52, sub, ha="center", va="center", fontsize=6.9, color=INK2, linespacing=1.2)
        if i < len(steps) - 1:
            _arrow(ax, (x + w + 0.04, 1.6), (x + w + gap - 0.04, 1.6), lw=1.1)
    ax.text(0, 2.45, f"Python and TLC state graphs matched on all {corr['cases']} configurations compared; "
            "numbers in the text are generated from these artifacts.", fontsize=6.9, color=MUTED, va="top")
    fig.subplots_adjust(left=0.0, right=1.0, top=1.0, bottom=0.0)
    save(fig, "fig_pipeline")


AGENT_NAMES = {"claude": "Claude", "open": "GLM", "qwen": "Qwen", "llama": "Llama"}
FAMILIES = ("claude", "open", "qwen", "llama")


def _agent_cells() -> dict:
    """(variant, condition, sender, receiver) -> cell, across all LLM parts."""
    cells = {}
    if (SUMMARY / "llm.json").exists():
        llm = load("llm")
        if llm["status"] != "not_run":
            for key, c in llm["cells"].items():
                variant, condition = key.split("/")
                cells[(variant, condition, "claude", "claude")] = c
    # Later parts replace earlier cells: part 5 reran every Llama cell of part 4.
    for name in ("llm_pairs", "llm_pairs_qwen", "llm_pairs_llama", "llm_pairs_grid"):
        if (SUMMARY / f"{name}.json").exists():
            part = load(name)
            if part["status"] != "not_run":
                for key, c in part["cells"].items():
                    variant, condition, seats = key.split("/")
                    sender, receiver = seats.split("->")
                    cells[(variant, condition, sender, receiver)] = c
    return cells


def figure_agent_grid() -> None:
    """Figure 4: every sender-receiver pairing, told only the goal, shared lock against private locks."""
    from matplotlib.colors import LinearSegmentedColormap

    cells = _agent_cells()
    if not cells:
        print("skipped fig4_agents: no LLM summaries")
        return
    cmap = LinearSegmentedColormap.from_list("leak", ["#fdf3ee", "#f6b796", ORANGE, "#b5461a"])
    fig, axes = plt.subplots(1, 2, figsize=(6.5, 2.75), gridspec_kw={"wspace": 0.42})
    n = len(FAMILIES)
    any_grey = any_single = False
    for ax, variant, title in ((axes[0], "V1", "Shared lock"), (axes[1], "V2", "Private locks")):
        for i, sender in enumerate(FAMILIES):
            for j, receiver in enumerate(FAMILIES):
                c = cells.get((variant, "no_hint", sender, receiver))
                y = n - 1 - i
                if c is None or not c["answered"]:
                    ax.add_patch(plt.Rectangle((j, y), 1, 1, facecolor="#f7f7f5", edgecolor=SURFACE, linewidth=2))
                    ax.text(j + 0.5, y + 0.5, "not run", ha="center", va="center", fontsize=6.5, color=MUTED)
                    continue
                acc = c["correct"] / c["answered"]
                level = min(max((acc - 0.5) / 0.5, 0.0), 1.0)
                # Too few answers to read as a rate: neutral, not a point on the scale.
                mostly_unanswered = c["unanswered"] * 2 > c["rounds"]
                any_grey |= mostly_unanswered
                face = "#dcdbd5" if mostly_unanswered else cmap(level)
                ax.add_patch(plt.Rectangle((j, y), 1, 1, facecolor=face, edgecolor=SURFACE, linewidth=2))
                ink = SURFACE if level > 0.6 and not mostly_unanswered else INK
                ax.text(j + 0.5, y + 0.58, f"{c['correct']}/{c['answered']}", ha="center", va="center", fontsize=7.6,
                        color=ink, weight="bold")
                note = f"{c['unanswered']} unans." if c["unanswered"] else f"{round(100 * acc)}%"
                if c.get("episodes", 2) < 2:
                    note = f"{c['episodes']} episode"
                    any_single = True
                ax.text(j + 0.5, y + 0.28, note, ha="center", va="center", fontsize=6.3, color=ink)
        ax.set_xlim(0, n)
        ax.set_ylim(0, n)
        ax.set_aspect("equal")
        ax.set_xticks([j + 0.5 for j in range(n)])
        ax.set_xticklabels([AGENT_NAMES[f] for f in FAMILIES], fontsize=7.6)
        ax.set_yticks([n - 1 - i + 0.5 for i in range(n)])
        ax.set_yticklabels([AGENT_NAMES[f] for f in FAMILIES], fontsize=7.6)
        ax.tick_params(length=0)
        for spine in ax.spines.values():
            spine.set_visible(False)
        ax.xaxis.set_label_position("top")
        ax.xaxis.tick_top()
        ax.set_xlabel("receiver", fontsize=7.6, color=INK2, labelpad=4)
        ax.set_ylabel("sender", fontsize=7.6, color=INK2)
        ax.set_title(title, fontsize=8.8, pad=24, loc="center")
    sm = plt.cm.ScalarMappable(cmap=cmap, norm=plt.Normalize(50, 100))
    cax = fig.add_axes([0.93, 0.18, 0.012, 0.6])
    cb = fig.colorbar(sm, cax=cax)
    cb.set_ticks([50, 75, 100])
    cb.set_ticklabels(["50% chance\nor below", "75%", "100%"])
    cb.ax.tick_params(labelsize=6.8, length=2, colors=INK2)
    cb.outline.set_visible(False)
    footer = ("Each cell: receiver's correct guesses / answered guesses over 2 episodes of 20 rounds"
              + (" (1 where marked)" if any_single else "") + ", agents told only the goal.")
    if any_grey:
        footer += " Grey: most guesses unanswered."
    fig.text(0.5, 0.01, footer, ha="center", fontsize=6.9, color=INK2)
    fig.subplots_adjust(left=0.08, right=0.9, top=0.84, bottom=0.08)
    save(fig, "fig4_agents")


def _agent_rows() -> list[tuple[str, str, dict | None, dict | None]]:
    """(group, label, shared-lock cell, private-lock cell) for every measured pairing."""
    llm = load("llm") if (SUMMARY / "llm.json").exists() else {"status": "not_run"}
    parts = [load(n) for n in ("llm_pairs", "llm_pairs_qwen") if (SUMMARY / f"{n}.json").exists()]
    pair_cells = {k: c for p in parts if p["status"] != "not_run" for k, c in p["cells"].items()}
    rows = []
    if llm["status"] != "not_run":
        rows.append(("goal", "Claude → Claude", llm["cells"].get("V1/no_hint"), llm["cells"].get("V2/no_hint")))
    for s, r in (("claude", "open"), ("open", "claude"), ("open", "open"), ("claude", "qwen"), ("qwen", "claude"),
                 ("qwen", "qwen"), ("open", "qwen"), ("qwen", "open")):
        shared = pair_cells.get(f"V1/no_hint/{s}->{r}")
        if shared is not None:
            rows.append(("goal", f"{AGENT_NAMES[s]} → {AGENT_NAMES[r]}", shared, pair_cells.get(f"V2/no_hint/{s}->{r}")))
    if llm["status"] != "not_run":
        rows.append(("code", "Claude → Claude", llm["cells"].get("V1/codebook"), llm["cells"].get("V2/codebook")))
    for s in ("open", "qwen"):
        shared = pair_cells.get(f"V1/codebook/{s}->{s}")
        if shared is not None:
            rows.append(("code", f"{AGENT_NAMES[s]} → {AGENT_NAMES[s]}", shared, None))
    return rows


def figure_agents() -> None:
    """Figure 4: bits decoded by language-model pairs, shared lock against private locks."""
    rows = _agent_rows()
    if not rows:
        print("skipped fig4_agents: no LLM summaries")
        return
    n_goal = sum(g == "goal" for g, *_ in rows)
    # One blank row between the two groups.
    ys, y = [], len(rows)
    for i, (group, *_rest) in enumerate(rows):
        if i == n_goal:
            y -= 1
        ys.append(y)
        y -= 1
    fig, ax = plt.subplots(figsize=(6.5, 0.24 * (len(rows) + 1) + 0.85))
    ax.axvline(50, color=INK2, linewidth=1.0, zorder=1)
    handles = {}
    for (group, label, shared, private), y in zip(rows, ys):
        for cell, color, marker, name, dy in ((shared, ORANGE, "o", "shared lock (V1)", 0.13),
                                              (private, BLUE, "s", "private locks (V2)", -0.13)):
            if cell is None:
                continue
            k, n = cell["correct"], cell["answered"]
            value = 100 * k / n if n else 0
            ax.plot([50, value], [y + dy, y + dy], color=color, linewidth=1.8, solid_capstyle="round", zorder=2)
            handles[name] = ax.plot(value, y + dy, marker=marker, color=color, markersize=5.5, markeredgecolor=SURFACE,
                                    markeredgewidth=1.0, linestyle="none", zorder=3)[0]
            unanswered = cell["unanswered"]
            text = f"{k}/{n}" + (f" ({unanswered} unanswered)" if unanswered else "")
            ax.text(value + (2.2 if value >= 50 else -2.2), y + dy, text, va="center",
                    ha="left" if value >= 50 else "right", fontsize=6.9, color=INK)
    ax.set_yticks(ys)
    ax.set_yticklabels([label for _, label, _, _ in rows], fontsize=7.8)
    ax.tick_params(axis="y", length=0)
    ax.spines["left"].set_visible(False)
    ax.set_xlim(-2, 128)
    ax.set_xticks([0, 25, 50, 75, 100])
    ax.xaxis.grid(True, color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)
    ax.set_xlabel("receiver's guesses correct, % of answered (chance is 50)", fontsize=7.6)
    top, mid = ys[0] + 0.62, ys[n_goal] + 0.62 if n_goal < len(rows) else None
    ax.text(-1, top, "Told only the goal (sender → receiver)", fontsize=7.8, color=INK, weight="bold", va="bottom")
    if mid is not None:
        ax.text(-1, mid, "Code supplied (controls)", fontsize=7.8, color=INK, weight="bold", va="bottom")
    ax.set_ylim(ys[-1] - 0.7, top + 0.55)
    order = [h for h in ("shared lock (V1)", "private locks (V2)") if h in handles]
    ax.legend([handles[h] for h in order], order, loc="lower right", fontsize=7.4, handletextpad=0.3, ncol=2,
              bbox_to_anchor=(1.0, 1.0), borderaxespad=0.2)
    save(fig, "fig4_agents")


def main() -> int:
    figure_overview()
    figure_project_image()
    figure_pipeline()
    figure_timeline()
    figure_measured()
    figure_noise()
    figure_agent_grid()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
