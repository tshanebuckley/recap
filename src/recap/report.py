"""Report across images, methods and manual counts (`recap report`).

Inputs: one or more recap output folders (e.g. a StarDist run and a Cellpose run of the same
images) and optionally manual_counts CSVs (from the spot check or `recap import-counts`).
Images are matched to manual counts by file content (SHA-256), so renamed or moved copies match.

Outputs (in the report folder):
  program_counts.csv    every image x method: counts, VRC+, count range, QC flags
  vs_manual.csv         every image x method x counter: program vs manual, and with clicked points:
                        matched / missed / merged / extra nuclei and the per-nucleus VRC+ table
  method_agreement.csv  every image x pair of methods: nuclei matched by overlap, found by one only
  inter_rater.csv       images counted by more than one person
  summary.csv           per method vs manual: totals, bias and limits of agreement, VRC+ agreement
  *.png, report.md      plots and a readable summary
"""

from __future__ import annotations

import itertools
import logging
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import tifffile

from recap.compare import latest, match, run_images
from recap.counts import ManualCount, compare_counts, read_manual_counts
from recap.errors import RecapError
from recap.io import file_sha256

log = logging.getLogger(__name__)

# categorical palette (fixed order, never cycled) and neutral inks
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]
INK, INK2, GRID, SURFACE = "#0b0b0b", "#52514e", "#e6e5e0", "#fcfcfb"


@dataclass
class RunData:
    name: str
    folder: Path
    summary: pd.DataFrame
    images: dict[str, Path]  # sha256 -> image path (as processed)
    stems: dict[str, str]  # sha256 -> image file stem

    def nuclei(self, sha: str) -> pd.DataFrame | None:
        p = self.folder / f"{self.stems[sha]}_nuclei.csv"
        return pd.read_csv(p) if p.exists() else None

    def labels(self, sha: str) -> np.ndarray | None:
        p = self.folder / f"{self.stems[sha]}_labels.tif"
        return tifffile.imread(p) if p.exists() else None


@dataclass
class ReportResult:
    out: Path
    files: dict[str, Path] = field(default_factory=dict)
    summary: pd.DataFrame | None = None


def load_run(folder: Path, name: str) -> RunData:
    summary = pd.read_csv(latest(folder, "image_summary_*.csv"))
    images, stems = {}, {}
    for p in run_images(folder):
        if not p.exists():
            log.warning("%s: image %s not found; it cannot be matched to manual counts", name, p)
            continue
        sha = file_sha256(p)
        images[sha], stems[sha] = p, p.stem
    summary["sha256"] = summary.image.map(
        lambda s: next((h for h, p in images.items() if Path(s).name == p.name), None)
    )
    return RunData(name, folder, summary, images, stems)


def _program_counts(runs: list[RunData]) -> pd.DataFrame:
    cols = [
        "n_nuclei",
        "n_infected",
        "pct_infected",
        "n_nuclei_low",
        "n_nuclei_high",
        "n_infected_low",
        "n_infected_high",
        "qc_flags",
    ]
    rows = []
    for r in runs:
        for _, s in r.summary.iterrows():
            rows.append({"image": Path(s.image).name, "method": r.name, **{c: s.get(c) for c in cols if c in s}})
    return pd.DataFrame(rows)


def _vs_manual(runs: list[RunData], manual: list[ManualCount]) -> pd.DataFrame:
    rows = []
    for r in runs:
        for mc in manual:
            if mc.sha256 not in r.images:
                continue
            table, labels = r.nuclei(mc.sha256), r.labels(mc.sha256)
            if table is None:
                continue
            if labels is None:  # no label image: totals only
                labels = np.zeros((1, 1), np.uint16)
                mc = mc.model_copy(update={"points": []})
            row = compare_counts(mc, SimpleNamespace(labels=labels, table=table))
            srow = r.summary[r.summary.sha256 == mc.sha256]
            lo = srow.n_nuclei_low.iloc[0] if "n_nuclei_low" in srow and len(srow) else np.nan
            hi = srow.n_nuclei_high.iloc[0] if "n_nuclei_high" in srow and len(srow) else np.nan
            rows.append(
                {
                    "image": mc.image_name,
                    "method": r.name,
                    "counted_by": mc.counted_by or "",
                    **{k: v for k, v in row.items() if k not in ("image_name", "source")},
                    "manual_within_range": bool(lo <= mc.nuclei <= hi) if np.isfinite(lo) else None,
                }
            )
    return pd.DataFrame(rows)


def _method_agreement(runs: list[RunData], iou: float) -> pd.DataFrame:
    rows = []
    for a, b in itertools.combinations(runs, 2):
        for sha in sorted(set(a.images) & set(b.images)):
            la, lb = a.labels(sha), b.labels(sha)
            ta, tb = a.nuclei(sha), b.nuclei(sha)
            if la is None or lb is None or ta is None or tb is None:
                continue
            pairs = match(la, lb, iou)
            ia, ib = ta.set_index("label").infected, tb.set_index("label").infected
            agree = [bool(ia[p]) == bool(ib[q]) for p, q, _ in pairs]
            rows.append(
                {
                    "image": a.images[sha].name,
                    "method_a": a.name,
                    "method_b": b.name,
                    "n_a": int(la.max()),
                    "n_b": int(lb.max()),
                    "matched": len(pairs),
                    "only_a": int(la.max()) - len(pairs),
                    "only_b": int(lb.max()) - len(pairs),
                    "mean_iou_matched": round(float(np.mean([p[2] for p in pairs])), 3) if pairs else np.nan,
                    "infection_agreement": round(float(np.mean(agree)), 3) if agree else np.nan,
                }
            )
    return pd.DataFrame(rows)


def _inter_rater(manual: list[ManualCount]) -> pd.DataFrame:
    by_img: dict[str, list[ManualCount]] = {}
    for mc in manual:
        by_img.setdefault(mc.sha256, []).append(mc)
    rows = []
    for counts in by_img.values():
        for a, b in itertools.combinations(counts, 2):
            rows.append(
                {
                    "image": a.image_name,
                    "counter_a": a.counted_by or "",
                    "counter_b": b.counted_by or "",
                    "nuclei_a": a.nuclei,
                    "nuclei_b": b.nuclei,
                    "difference": b.nuclei - a.nuclei,
                    "pct_difference": round(100 * (b.nuclei - a.nuclei) / a.nuclei, 2) if a.nuclei else np.nan,
                    "vrc_pos_a": a.infected,
                    "vrc_pos_b": b.infected,
                }
            )
    return pd.DataFrame(rows)


def _summary(vm: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for method, g in vm.groupby("method", sort=False):
        d = (g.program_nuclei - g.manual_nuclei).astype(float)
        sd = float(d.std(ddof=1)) if len(d) > 1 else np.nan
        man, prog = int(g.manual_nuclei.sum()), int(g.program_nuclei.sum())
        inf = g[g.manual_infected.notna()]
        row = {
            "method": method,
            "n_comparisons": len(g),
            "n_images": g.image.nunique(),
            "manual_total": man,
            "program_total": prog,
            "total_pct_difference": round(100 * (prog - man) / man, 2) if man else np.nan,
            "mean_difference": round(float(d.mean()), 2),
            "sd_difference": round(sd, 2),
            "loa_low": round(float(d.mean()) - 1.96 * sd, 2) if np.isfinite(sd) else np.nan,
            "loa_high": round(float(d.mean()) + 1.96 * sd, 2) if np.isfinite(sd) else np.nan,
            "mean_abs_pct_difference": round(float(g.pct_difference.abs().mean()), 2),
            "max_abs_pct_difference": round(float(g.pct_difference.abs().max()), 2),
        }
        if len(inf):
            mi, mn = float(inf.manual_infected.sum()), float(inf.manual_nuclei.sum())
            pi, pn = float(inf.program_infected.sum()), float(inf.program_nuclei.sum())
            row.update(
                manual_pct_vrc_pos=round(100 * mi / mn, 2) if mn else np.nan,
                program_pct_vrc_pos=round(100 * pi / pn, 2) if pn else np.nan,
            )
            row["pct_vrc_pos_difference"] = round(row["program_pct_vrc_pos"] - row["manual_pct_vrc_pos"], 2)
        if "matched" in g and g.matched.notna().any():
            pts = g[g.matched.notna()]
            row.update(
                images_with_points=len(pts),
                matched=int(pts.matched.sum()),
                missed=int(pts.missed.sum()),
                merged=int(pts.merged.sum()),
                extra=int(pts.extra.sum()),
            )
            if "both_infected" in pts and pts.both_infected.notna().any():
                cells = [
                    int(pts[c].fillna(0).sum())
                    for c in (
                        "both_infected",
                        "both_uninfected",
                        "manual_inf_program_uninf",
                        "manual_uninf_program_inf",
                    )
                ]
                row.update(
                    both_vrc_pos=cells[0],
                    both_vrc_neg=cells[1],
                    manual_pos_program_neg=cells[2],
                    manual_neg_program_pos=cells[3],
                    infection_agreement=round((cells[0] + cells[1]) / sum(cells), 3) if sum(cells) else np.nan,
                )
        if "manual_within_range" in g and g.manual_within_range.notna().any():
            row["images_manual_within_range"] = int(g.manual_within_range.fillna(False).sum())
        rows.append(row)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- plots
def _style(ax):
    ax.set_facecolor(SURFACE)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color("#c3c2b7")
    ax.tick_params(colors=INK2, labelsize=8.5)
    ax.grid(color=GRID, lw=0.6)
    ax.set_axisbelow(True)


def _plots(vm: pd.DataFrame, methods: list[str], out: Path) -> dict[str, Path]:
    # Figure objects, not pyplot: no global backend switch, safe in the app's background thread
    from matplotlib.figure import Figure
    from matplotlib.ticker import MaxNLocator

    files = {}
    color = {m: SERIES[i % len(SERIES)] for i, m in enumerate(methods)}

    # program vs manual: nuclei and VRC+
    fig = Figure(figsize=(10, 4.4), facecolor=SURFACE)
    axes = fig.subplots(1, 2)
    for ax, (mcol, pcol, title) in zip(
        axes,
        [
            ("manual_nuclei", "program_nuclei", "Nuclei per image"),
            ("manual_infected", "program_infected", "VRC+ nuclei per image"),
        ],
    ):
        _style(ax)
        sub = vm[vm[mcol].notna()]
        hi = float(max(sub[mcol].max(), sub[pcol].max(), 1)) * 1.08 if len(sub) else 1
        lo = -0.04 * hi
        ax.plot([0, hi], [0, hi], color=INK2, lw=1, ls="--", zorder=1)
        ax.text(hi * 0.98, hi * 0.93, "program = manual", color=INK2, fontsize=8, ha="right")
        for m in methods:
            g = sub[sub.method == m]
            ax.scatter(g[mcol], g[pcol], s=42, color=color[m], edgecolor=SURFACE, lw=1.5, alpha=0.85, label=m, zorder=3)
        ax.set_xlim(lo, hi)
        ax.set_ylim(lo, hi)
        ax.xaxis.set_major_locator(MaxNLocator(integer=True))
        ax.yaxis.set_major_locator(MaxNLocator(integer=True))
        title += f"  (n = {sub.groupby('method').size().max() if len(sub) else 0} per method)"
        ax.set_xlabel("manual count", color=INK2)
        ax.set_ylabel("program count", color=INK2)
        ax.set_title(title, loc="left", color=INK, fontsize=10)
    if len(methods) > 1:
        axes[0].legend(frameon=False, fontsize=8.5, loc="upper left")
    fig.tight_layout()
    files["counts_vs_manual"] = out / "counts_vs_manual.png"
    fig.savefig(files["counts_vs_manual"], dpi=150, facecolor=SURFACE)

    # agreement (Bland-Altman), one panel per method
    fig = Figure(figsize=(4.6 * len(methods), 3.8), facecolor=SURFACE)
    axes = fig.subplots(1, len(methods), squeeze=False)
    for ax, m in zip(axes[0], methods):
        _style(ax)
        g = vm[vm.method == m]
        mean = (g.manual_nuclei + g.program_nuclei) / 2
        diff = (g.program_nuclei - g.manual_nuclei).astype(float)
        ax.axhline(0, color=INK2, lw=0.8)
        ax.scatter(mean, diff, s=42, color=color[m], edgecolor=SURFACE, lw=1.5, alpha=0.85, zorder=3)
        if len(diff) > 1:
            bias, sd = diff.mean(), diff.std(ddof=1)
            for y, lab in (
                (bias, f"bias {bias:+.1f}"),
                (bias - 1.96 * sd, f"lower limit {bias - 1.96 * sd:+.1f}"),
                (bias + 1.96 * sd, f"upper limit {bias + 1.96 * sd:+.1f}"),
            ):
                ax.axhline(y, color=INK2, lw=1, ls="--" if lab.startswith("bias") else ":")
                ax.text(mean.max(), y, lab + " ", color=INK2, fontsize=7.5, va="bottom", ha="right")
        ax.set_xlabel("mean of manual and program", color=INK2)
        ax.set_ylabel("program - manual (nuclei)", color=INK2)
        ax.yaxis.set_major_locator(MaxNLocator(integer=True))
        ax.set_title(f"Agreement: {m}  (n = {len(g)})", loc="left", color=INK, fontsize=10)
    fig.tight_layout()
    files["agreement"] = out / "agreement_nuclei.png"
    fig.savefig(files["agreement"], dpi=150, facecolor=SURFACE)
    return files


def _md_table(df: pd.DataFrame, cols: list[str]) -> str:
    cols = [c for c in cols if c in df]
    head = "| " + " | ".join(cols) + " |\n|" + "---|" * len(cols) + "\n"
    body = "".join("| " + " | ".join("" if pd.isna(v) else str(v) for v in r) + " |\n" for r in df[cols].values)
    return head + body


def build_report(runs: list[tuple[str, Path]], manual_files: list[Path], out: Path, iou: float = 0.5) -> ReportResult:
    """Write the report for runs [(name, output folder), ...] and manual count CSVs into `out`."""
    if not runs:
        raise RecapError("give at least one run folder")
    if len({n for n, _ in runs}) != len(runs):
        raise RecapError("method names must be unique (use --name)")
    out.mkdir(parents=True, exist_ok=True)
    data = [load_run(folder, name) for name, folder in runs]
    manual = [mc for f in manual_files for mc in read_manual_counts(f)]
    res = ReportResult(out)

    pc = _program_counts(data)
    pc.to_csv(out / "program_counts.csv", index=False)
    res.files["program_counts"] = out / "program_counts.csv"
    if len(data) > 1:
        ma = _method_agreement(data, iou)
        ma.to_csv(out / "method_agreement.csv", index=False)
        res.files["method_agreement"] = out / "method_agreement.csv"
    else:
        ma = pd.DataFrame()
    vm = _vs_manual(data, manual) if manual else pd.DataFrame()
    unmatched = {mc.image_name for mc in manual} - set(vm.image if len(vm) else [])
    if unmatched:
        log.warning(
            "manual counts for %d image(s) match no image in the runs: %s", len(unmatched), ", ".join(sorted(unmatched))
        )
    ir = _inter_rater(manual) if manual else pd.DataFrame()
    summary = pd.DataFrame()
    if len(vm):
        vm.to_csv(out / "vs_manual.csv", index=False)
        res.files["vs_manual"] = out / "vs_manual.csv"
        summary = _summary(vm)
        summary.to_csv(out / "summary.csv", index=False)
        res.files["summary"] = out / "summary.csv"
        res.files.update(_plots(vm, [n for n, _ in runs], out))
        res.summary = summary
    if len(ir):
        ir.to_csv(out / "inter_rater.csv", index=False)
        res.files["inter_rater"] = out / "inter_rater.csv"

    # readable summary
    md = [
        "# recap report",
        "",
        "*recap: Reconstruction of Cellular Architecture and Phenotypes*",
        "",
        f"Runs: {', '.join(f'{n} ({f})' for n, f in runs)}  ",
        f"Manual counts: {len(manual)} from {len(manual_files)} file(s)" if manual_files else "Manual counts: none",
        "",
    ]
    if len(summary):
        md += [
            "## Program vs manual",
            "",
            _md_table(
                summary,
                [
                    "method",
                    "n_images",
                    "manual_total",
                    "program_total",
                    "total_pct_difference",
                    "mean_difference",
                    "loa_low",
                    "loa_high",
                    "mean_abs_pct_difference",
                    "manual_pct_vrc_pos",
                    "program_pct_vrc_pos",
                    "infection_agreement",
                    "missed",
                    "merged",
                    "extra",
                    "images_manual_within_range",
                ],
            ),
            "mean_difference is the average (program - manual) nuclei per image; loa_low/high are the 95% "
            "limits of agreement (mean +/- 1.96 SD). missed / merged / extra need clicked points "
            "(Cell Counter or Multi-point imports, or the spot check).",
            "",
            "![counts](counts_vs_manual.png)",
            "",
            "![agreement](agreement_nuclei.png)",
            "",
        ]
    if len(ma):
        agg = (
            ma.groupby(["method_a", "method_b"])
            .agg(
                images=("image", "size"),
                matched=("matched", "sum"),
                only_a=("only_a", "sum"),
                only_b=("only_b", "sum"),
                infection_agreement=("infection_agreement", "mean"),
            )
            .reset_index()
        )
        agg["infection_agreement"] = agg.infection_agreement.round(3)
        md += [
            "## Methods vs each other",
            "",
            _md_table(agg, ["method_a", "method_b", "images", "matched", "only_a", "only_b", "infection_agreement"]),
            f"Nuclei are matched one-to-one by overlap (IoU >= {iou:.2f}).",
            "",
        ]
    if len(ir):
        md += [
            "## Between counters",
            "",
            _md_table(
                ir,
                ["image", "counter_a", "counter_b", "nuclei_a", "nuclei_b", "pct_difference", "vrc_pos_a", "vrc_pos_b"],
            ),
            "",
        ]
    flagged = pc[pc.qc_flags.fillna("").astype(str) != ""] if "qc_flags" in pc else pc.iloc[0:0]
    md += ["## QC flags", "", _md_table(flagged, ["image", "method", "qc_flags"]) if len(flagged) else "None.", ""]
    md += ["## Files", ""] + [f"- `{p.name}`" for p in res.files.values()]
    (out / "report.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    res.files["report"] = out / "report.md"
    return res
