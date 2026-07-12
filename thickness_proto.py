"""
thickness_proto.py  --  PROTOTYPE: optical thickness (n*t) vs pressure.

Reads a folder of the QuickLook tool's own *_absorbance.csv outputs, runs the
defringe.py FFT fringe detection on the raw Background and Sample channels of
every pressure point, and reports the detected optical thickness n*t (um) per
point. With --n it divides by your refractive index to give physical
thickness t = (n*t)/n.

Physics: a parallel gap of thickness t and index n is an etalon; on a
wavenumber axis (1/lambda) the fringe period is 1/(2*n*t), so the FFT peak
sits at frequency f = 2*n*t. defringe.defringe_channel returns nt_um and
pvalue directly (its defringed curve is unused here); this script only
converts units and plots.

Standalone on purpose: app.py is untouched. The real in-app feature waits on
the architecture discussion with Matt Diamond (his fringe-analysis module).

usage:
  python thickness_proto.py <folder with *_absorbance.csv>
         [--n 1.0] [--channel both|sample|background]
         [--pmax 1e-4] [--out plot.png] [--csv table.csv]

NQT / Lee Lab -- Jul 2026 (prototype)
"""

import os
import sys
import csv
import argparse

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import engine
import defringe


def collect(folder, channels, pmax):
    """Detect n*t on the requested raw channels of every loaded curve.

    Returns rows: dicts with label/GPa/branch/channel/nt_um/pvalue.
    """
    results = engine.load_processed_folder(folder)
    if not results:
        sys.exit("No *_absorbance.csv in %s" % folder)
    rows = []
    for r in sorted(results, key=lambda x: x["pressure_val"]):
        for ch, key in channels:
            y = np.asarray(r[key], float)
            if not np.isfinite(y).any():
                continue
            det = defringe.defringe_channel(r["wl"], y, pvalue_max=pmax)
            rows.append({"label": r["label"], "GPa": r["pressure_val"],
                         "branch": r.get("branch_tag") or "C",
                         "channel": ch,
                         "nt_um": det["nt_um"],
                         "pvalue": det["pvalue"]})
    return rows


def main():
    ap = argparse.ArgumentParser(description="n*t / thickness vs pressure "
                                             "from fringe detection")
    ap.add_argument("folder", help="folder with *_absorbance.csv outputs")
    ap.add_argument("--n", type=float, default=1.0,
                    help="refractive index of the layer in the gap "
                         "(default 1.0 = report raw n*t)")
    ap.add_argument("--channel", default="both",
                    choices=("both", "sample", "background"))
    ap.add_argument("--pmax", type=float, default=1e-4,
                    help="Fisher g-test gate (defringe.py default 1e-4)")
    ap.add_argument("--out", default=None, help="output PNG path")
    ap.add_argument("--csv", default=None, help="output CSV path")
    a = ap.parse_args()
    if a.n <= 0:
        ap.error("--n must be > 0")
    if not os.path.isdir(a.folder):
        sys.exit("Not a folder: %s" % a.folder)

    chans = {"both": [("sample", "samp_c"), ("background", "bg_c")],
             "sample": [("sample", "samp_c")],
             "background": [("background", "bg_c")]}[a.channel]
    rows = collect(a.folder, chans, a.pmax)
    if not rows:
        sys.exit("No raw Sample/Background columns in these CSVs; nothing "
                 "to detect fringes on.")

    ylab = ("thickness t (um), n = %.3f" % a.n) if a.n != 1.0 \
        else "optical thickness n*t (um)"
    print("%-34s %7s %3s %-10s %9s %10s" %
          ("label", "GPa", "br", "channel", "n*t (um)", "p-value"))
    n_det = 0
    for row in rows:
        if row["nt_um"] is None:
            print("%-34s %7.2f %3s %-10s %9s %10s"
                  % (row["label"], row["GPa"], row["branch"], row["channel"],
                     "--", "no fringe"))
            continue
        n_det += 1
        print("%-34s %7.2f %3s %-10s %9.2f %10.1e"
              % (row["label"], row["GPa"], row["branch"], row["channel"],
                 row["nt_um"], row["pvalue"]))
    if not n_det:
        sys.exit("No confident fringe on any curve (p > %.1e everywhere)."
                 % a.pmax)

    csv_path = a.csv or os.path.join(a.folder, "thickness_vs_pressure.csv")
    with open(csv_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["label", "GPa", "branch", "channel", "nt_um",
                    "pvalue", "t_um_at_n", "n_used"])
        for r in rows:
            t = (r["nt_um"] / a.n) if r["nt_um"] is not None else ""
            w.writerow([r["label"], r["GPa"], r["branch"], r["channel"],
                        r["nt_um"] if r["nt_um"] is not None else "",
                        r["pvalue"], t, a.n])

    fig, ax = plt.subplots(figsize=(6.0, 4.2), dpi=200)
    style = {("sample", "C"): dict(marker="o", ls="-", color="#1f77b4",
                                   label="sample channel (C)"),
             ("background", "C"): dict(marker="s", ls="--", color="#7f7f7f",
                                       mfc="none", label="background (C)"),
             ("sample", "D"): dict(marker="o", ls="-", color="#d62728",
                                   mfc="none", label="sample channel (D)"),
             ("background", "D"): dict(marker="s", ls="--", color="#d62728",
                                       mfc="none", label="background (D)")}
    for (ch, br), st in style.items():
        pts = [(r["GPa"], r["nt_um"] / a.n) for r in rows
               if r["channel"] == ch and r["branch"] == br
               and r["nt_um"] is not None]
        if not pts:
            continue
        pts.sort()
        ax.plot([p for p, _ in pts], [t for _, t in pts], ms=4.5, lw=1.0, **st)
    ax.set_xlabel("Pressure (GPa)")
    ax.set_ylabel(ylab)
    ax.set_title(os.path.basename(os.path.normpath(a.folder)))
    ax.grid(alpha=0.25, lw=0.5)
    ax.legend(fontsize=8, frameon=False)
    fig.tight_layout()
    png_path = a.out or os.path.join(a.folder, "thickness_vs_pressure.png")
    fig.savefig(png_path)
    print("\nwrote  %s\nwrote  %s" % (csv_path, png_path))


if __name__ == "__main__":
    main()
