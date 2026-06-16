"""
engine.py  --  DAC Beamline Quick-Look: concatenation + absorbance core.

Stage 1 of the NSLS-II 22-IR-1 visible-absorption workflow.
Takes raw spectrometer segments, concatenates the 4 grating segments per
measurement, computes absorbance A = -log10[(Sample - Dark)/(Background - Dark)],
and writes one tidy CSV per pressure point. NO defringe / notch / thickness here
(that is a separate downstream step, handled elsewhere).

Filename grammar (case-insensitive, optional trailing .csv twin tolerated):
    vis_{DAC}_{SAMPLE}[_{PRESSURE}][_bg|_s][_C|_D][_<rep>][.<seq>]
  - no measurement suffix      -> dark
  - _bg                        -> background
  - _s                         -> sample
  - _<rep> (e.g. _2, _3)       -> replicate / retake of that measurement
  - <seq> = 001..004           -> grating segment, concatenated in order;
    a missing .<seq> means a single-segment (one-stitch) measurement
  - PRESSURE encodes the decimal with 'p': 1p39 -> 1.39 GPa. 0 is allowed,
    and a missing pressure field is assumed to be 0 GPa.
Measurements with an incomplete channel set (e.g. background only) load as
raw counts; absorbance needs all of sample + background + dark.

Pure functions, no GUI imports, so this module is unit-testable on its own.

NQT / Lee Lab -- Jun 2026
"""

import os
import csv
import numpy as np

VALID_MEAS = ("dark", "background", "sample")


# ---------------------------------------------------------------------------
# Filename parsing
# ---------------------------------------------------------------------------
def parse_segment_filename(fname):
    """
    Parse one raw segment filename.

    Returns dict(dac, sample, pressure_str, pressure_val, meas, rep, seq, raw)
    on success, or {'skip': True, 'reason': str, 'raw': fname} on failure.
    """
    raw = fname
    name = fname
    # Tolerate a single trailing .csv twin (old batch tool copied raw -> .csv)
    if name.lower().endswith(".csv"):
        name = name[:-4]

    # Numeric segment suffix <base>.<seq>; a missing suffix is a valid
    # single-segment (one-stitch) measurement -> seq 1.
    if "." in name:
        base, seq_str = name.rsplit(".", 1)
        if not seq_str.isdigit():
            return {"skip": True, "reason": "segment is not numeric", "raw": raw}
        seq = int(seq_str)
    else:
        base, seq = name, 1
    # A clean base never contains a dot. A leftover dot means a double extension
    # like vis_..._bg.001.002 -- reject it loudly rather than mis-bin it.
    if "." in base:
        return {"skip": True, "reason": "malformed (extra extension)", "raw": raw}

    tokens = base.split("_")
    if len(tokens) < 3 or tokens[0].lower() != "vis":
        return {"skip": True,
                "reason": "does not match vis_DAC_SAMPLE[_PRESSURE]",
                "raw": raw}

    dac, sample = tokens[1], tokens[2]
    rest = tokens[3:]
    # Pressure: numeric token ('p' = decimal). It may be absent entirely
    # (assumed 0 GPa) when the name ends after the sample or the next token
    # is a measurement/branch suffix (bg, s, C, D).
    pressure_str, pdefault = "0", True
    if rest:
        tok = rest[0]
        try:
            float(tok.replace("p", "."))
        except ValueError:
            if tok.lower() not in ("bg", "s", "c", "d"):
                return {"skip": True,
                        "reason": "pressure '%s' not numeric" % tok,
                        "raw": raw}
        else:
            pressure_str, pdefault = tok, False
            rest = rest[1:]

    meas, rep = "dark", 1
    if rest and rest[0].lower() in ("bg", "s"):
        meas = "background" if rest[0].lower() == "bg" else "sample"
        rest = rest[1:]
    # rep (_2/_3) and branch (_C/_D) may appear in EITHER order:
    # canonical is [_C|_D][_2|_3]; the old [_2|_3][_C|_D] is still accepted.
    branch = None
    for _ in range(2):
        if rest and rest[0].isdigit():
            rep = int(rest[0]); rest = rest[1:]
        elif rest and rest[0].upper() in ("C", "D"):
            branch = rest[0].upper(); rest = rest[1:]
        else:
            break
    if rest:
        return {"skip": True, "reason": "unrecognized trailing token '%s'"
                % "_".join(rest), "raw": raw}

    try:
        pressure_val = float(pressure_str.replace("p", "."))
    except ValueError:
        return {"skip": True, "reason": "pressure '%s' not numeric" % pressure_str,
                "raw": raw}
    if pressure_val < 0:
        return {"skip": True, "reason": "pressure < 0", "raw": raw}

    return {"dac": dac, "sample": sample, "pressure_str": pressure_str,
            "pressure_val": pressure_val, "meas": meas, "rep": rep,
            "branch": branch, "seq": seq, "pdefault": pdefault, "raw": raw}


# ---------------------------------------------------------------------------
# Spectrum file reader
# ---------------------------------------------------------------------------
def read_spectrum(path):
    """
    Read a two-column (wavelength_nm, counts) spectrum.

    The instrument writes a leading metadata line that begins with a quoted
    '# ' token; any line whose first two fields are not floats is skipped.
    Returns (wavelengths, counts) as float ndarrays.
    """
    wl, cts = [], []
    with open(path, "r", newline="") as f:
        for row in csv.reader(f):
            if len(row) < 2:
                continue
            try:
                w = float(row[0])
                c = float(row[1])
            except ValueError:
                continue  # header / comment / blank
            wl.append(w)
            cts.append(c)
    return np.asarray(wl, float), np.asarray(cts, float)


# ---------------------------------------------------------------------------
# Folder scan -> grouped segments
# ---------------------------------------------------------------------------
def scan_folder(in_dir):
    """
    Walk in_dir, parse every file, and group segments by measurement.

    Returns (groups, skipped) where:
      groups[(dac, sample, pressure_str)] = {
          'pressure_val': float,
          'meas': { 'dark'|'background'|'sample': { rep: { seq: path } } }
      }
      skipped = list of {'raw', 'reason'}
    Deduplicates raw/.csv twins: the same logical segment is stored once.
    """
    groups = {}
    skipped = []
    seen = set()  # canonical keys to drop raw/.csv duplicates

    for fname in sorted(os.listdir(in_dir)):
        full = os.path.join(in_dir, fname)
        if not os.path.isfile(full):
            continue
        info = parse_segment_filename(fname)
        if info.get("skip"):
            # Only log a skip once per logical name (avoid raw + .csv double log)
            canon = fname[:-4] if fname.lower().endswith(".csv") else fname
            if ("SKIP", canon) not in seen:
                seen.add(("SKIP", canon))
                skipped.append({"raw": fname, "reason": info["reason"]})
            continue

        canon = (info["dac"], info["sample"], info["pressure_str"],
                 info["meas"], info["rep"], info["branch"], info["seq"])
        if canon in seen:
            continue  # raw/.csv twin already captured
        seen.add(canon)

        gkey = (info["dac"], info["sample"], info["pressure_str"], info["branch"])
        g = groups.setdefault(gkey, {"pressure_val": info["pressure_val"],
                                     "branch_tag": info["branch"], "meas": {}})
        if info.get("pdefault"):
            g["pdefault"] = True
        m = g["meas"].setdefault(info["meas"], {})
        r = m.setdefault(info["rep"], {})
        r[info["seq"]] = full

    return groups, skipped


# ---------------------------------------------------------------------------
# Concatenation + absorbance
# ---------------------------------------------------------------------------
def _concat_segments(seg_by_seq, seqs):
    """Concatenate counts over the given seqs (ascending). Returns (wl, cts)."""
    wl_parts, ct_parts = [], []
    for s in seqs:
        w, c = read_spectrum(seg_by_seq[s])
        wl_parts.append(w)
        ct_parts.append(c)
    return np.concatenate(wl_parts), np.concatenate(ct_parts)


def _pick_source(meas_dict, rep):
    """Pick the rep-matching measurement if present, else the latest available."""
    if rep in meas_dict:
        return rep, meas_dict[rep]
    k = max(meas_dict)
    return k, meas_dict[k]


def process_group(gkey, group, warn=None):
    """
    Build the curve(s) for one (dac, sample, pressure, branch) group.

    Anchors on the sample channel when present (else background, else dark),
    uses the LATEST retake of the anchor (max replicate index), and pairs the
    other channels by matching rep when present, else the latest available.

    Absorbance is computed only when sample + background + dark all exist;
    otherwise the available channel(s) load as raw counts (absorbance is
    all-NaN) so single-channel collections can still be plotted and QC'd.
    Missing channels are NaN-filled to keep the result schema constant.

    Returns list of result dicts (0 or 1 element):
      {label, dac, sample, pressure_str, pressure_val, rep,
       wl (nm), wn (cm^-1), absorbance, dark_c, bg_c, samp_c}
    """
    dac, sample, pstr, branch = gkey
    meas = group["meas"]
    results = []

    present = [m for m in ("sample", "background", "dark") if m in meas]
    if not present:
        return results
    full = len(present) == 3
    anchor = present[0]

    for arep in [max(meas[anchor])]:
        srcs = {anchor: meas[anchor][arep]}
        for m in present:
            if m != anchor:
                _, srcs[m] = _pick_source(meas[m], arep)

        common = sorted(set.intersection(*(set(s) for s in srcs.values())))
        if not common:
            if warn:
                warn("%s_%s_%s rep%d: no shared segments -- skipped"
                     % (dac, sample, pstr, arep))
            continue

        wl = None
        chans = {}
        for m in present:
            w, c = _concat_segments(srcs[m], common)
            if wl is None:
                wl = w
            chans[m] = c

        n = min([len(wl)] + [len(c) for c in chans.values()])
        wl = wl[:n]
        for m in chans:
            chans[m] = chans[m][:n]

        order = np.argsort(wl)
        wl = wl[order]
        for m in chans:
            chans[m] = chans[m][order]

        def _ch(name):
            return chans[name] if name in chans else np.full(len(wl), np.nan)
        s_c, b_c, d_c = _ch("sample"), _ch("background"), _ch("dark")

        if full:
            with np.errstate(divide="ignore", invalid="ignore"):
                trans = (s_c - d_c) / (b_c - d_c)
                absb = -np.log10(trans)
            absb[~np.isfinite(absb)] = np.nan
        else:
            absb = np.full(len(wl), np.nan)

        wn = np.where(wl > 0, 1.0e7 / wl, np.nan)  # nm -> cm^-1
        label = "%s %s %.2f GPa" % (dac, sample, group["pressure_val"])
        if branch:
            label += " [%s]" % branch
        if arep > 1:
            label += " [latest retake r%d]" % arep
        if not full:
            abbr = {"sample": "S", "background": "B", "dark": "D"}
            tag = "+".join(abbr[m] for m in present)
            label += ((" [%s only]" % tag) if len(present) == 1
                      else (" [%s]" % tag))
            if warn:
                warn("%s_%s_%s: raw channel(s) only (%s) -- no absorbance"
                     % (dac, sample, pstr, ", ".join(present)))
        results.append({"label": label, "dac": dac, "sample": sample,
                        "pressure_str": pstr,
                        "pressure_val": group["pressure_val"], "rep": arep,
                        "branch_tag": branch,
                        "wl": wl, "wn": wn, "absorbance": absb,
                        "dark_c": d_c, "bg_c": b_c, "samp_c": s_c})
    return results


# ---------------------------------------------------------------------------
# CSV writer + top-level driver
# ---------------------------------------------------------------------------
def write_absorbance_csv(result, out_dir):
    """Write one result dict to {DAC}_{SAMPLE}_{PRESSURE}[_rN]_absorbance.csv."""
    dac, sample, pstr = result["dac"], result["sample"], result["pressure_str"]
    stem = "%s_%s_%s" % (dac, sample, pstr)
    if result.get("branch_tag"):
        stem += "_" + result["branch_tag"]
    path = os.path.join(out_dir, stem + "_absorbance.csv")
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Wavelength_nm", "Wavenumber_cm-1", "Absorbance",
                    "Dark", "Background", "Sample"])
        for row in zip(result["wl"], result["wn"], result["absorbance"],
                       result["dark_c"], result["bg_c"], result["samp_c"]):
            w.writerow(["" if (isinstance(v, float) and np.isnan(v)) else v
                        for v in row])
    return path


def load_processed_folder(in_dir, log=None):
    """Re-import this tool's own {stem}_absorbance.csv outputs as plottable
    results (viewer mode: nothing is recomputed or written).

    Accepts the writer's schema (Wavelength_nm, Wavenumber_cm-1, Absorbance,
    Dark, Background, Sample); blank cells -> NaN. *_absorbance_notch.csv
    companions and files that do not match are ignored.
    """
    results = []
    for fname in sorted(os.listdir(in_dir)):
        low = fname.lower()
        if not low.endswith("_absorbance.csv") \
                or low.endswith("_absorbance_notch.csv"):
            continue
        stem = fname[:-len("_absorbance.csv")]
        toks = stem.split("_")
        branch = None
        if toks and toks[-1].upper() in ("C", "D"):
            branch = toks[-1].upper()
            toks = toks[:-1]
        if len(toks) < 3:
            continue
        dac, sample, pstr = toks[0], toks[1], "_".join(toks[2:])
        try:
            pval = float(pstr.replace("p", "."))
        except ValueError:
            continue
        path = os.path.join(in_dir, fname)
        keys = ["wavelength_nm", "absorbance", "dark", "background", "sample"]
        data = {k: [] for k in keys}
        try:
            with open(path, "r", newline="") as f:
                rd = csv.reader(f)
                header = next(rd, [])
                idx = {h.strip().lower(): i for i, h in enumerate(header)}
                if "wavelength_nm" not in idx:
                    continue
                for row in rd:
                    for k in keys:
                        i = idx.get(k)
                        if i is None or i >= len(row) or row[i] == "":
                            data[k].append(np.nan)
                            continue
                        try:
                            data[k].append(float(row[i]))
                        except ValueError:
                            data[k].append(np.nan)
        except OSError:
            continue
        wl = np.asarray(data["wavelength_nm"], float)
        if wl.size < 2:
            continue
        absb = np.asarray(data["absorbance"], float)
        d_c = np.asarray(data["dark"], float)
        b_c = np.asarray(data["background"], float)
        s_c = np.asarray(data["sample"], float)
        wn = np.where(wl > 0, 1.0e7 / wl, np.nan)
        label = "%s %s %.2f GPa" % (dac, sample, pval)
        if branch:
            label += " [%s]" % branch
        if not np.isfinite(absb).any():
            present = [t for t, a in (("S", s_c), ("B", b_c), ("D", d_c))
                       if np.isfinite(a).any()]
            if present:
                label += ((" [%s only]" % present[0]) if len(present) == 1
                          else (" [%s]" % "+".join(present)))
        if log:
            log("  LOAD  %-34s  %5d pts  <- %s" % (label, len(wl), fname))
        results.append({"label": label, "dac": dac, "sample": sample,
                        "pressure_str": pstr, "pressure_val": pval, "rep": 1,
                        "branch_tag": branch,
                        "wl": wl, "wn": wn, "absorbance": absb,
                        "dark_c": d_c, "bg_c": b_c, "samp_c": s_c})
    return results


def run(in_dir, out_dir, log=print, should_cancel=None):
    """
    Full Stage-1 pass over a folder.

    Returns (results, skipped). Writes one CSV per result into out_dir and
    streams progress through log(callback).
    """
    os.makedirs(out_dir, exist_ok=True)
    groups, skipped = scan_folder(in_dir)
    log("Found %d measurement group(s); %d file(s) skipped."
        % (len(groups), len(skipped)))
    for sk in skipped:
        log("  SKIP  %-45s  %s" % (sk["raw"], sk["reason"]))
    # branch may be None or 'C'/'D'; map None to '' so the sort never
    # compares None against a string (same pressure with and without a tag)
    gsort = lambda k: (k[0], k[1], k[2], k[3] or "")
    for gkey in sorted(groups, key=gsort):
        if groups[gkey].get("pdefault"):
            log("  NOTE  %s_%s_%s: no pressure in filename, assumed 0 GPa"
                % (gkey[0], gkey[1], gkey[2]))
    if not groups:
        try:
            subs = [d for d in sorted(os.listdir(in_dir))
                    if os.path.isdir(os.path.join(in_dir, d))]
        except OSError:
            subs = []
        if subs:
            log("  HINT  no matching data files at this level, but there are "
                "subfolder(s): %s%s" % (", ".join(subs[:8]),
                                        " ..." if len(subs) > 8 else ""))
            log("        Pick one subfolder as the Input folder (the scan "
                "does not recurse).")

    results = []
    for gkey in sorted(groups, key=gsort):
        if should_cancel and should_cancel():
            log("Run cancelled by user.")
            break
        for res in process_group(gkey, groups[gkey], warn=log):
            path = write_absorbance_csv(res, out_dir)
            n_valid = int(np.isfinite(res["absorbance"]).sum())
            log("  OK    %-30s  %5d pts (%d valid)  -> %s"
                % (res["label"], len(res["wl"]), n_valid,
                   os.path.basename(path)))
            results.append(res)
    log("Done. %d absorbance curve(s) written to %s" % (len(results), out_dir))
    return results, skipped


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 3:
        print("usage: python engine.py <input_folder> <output_folder>")
        sys.exit(1)
    run(sys.argv[1], sys.argv[2])
