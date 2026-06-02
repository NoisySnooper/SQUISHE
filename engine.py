"""
engine.py  --  DAC Beamline Quick-Look: concatenation + absorbance core.

Stage 1 of the NSLS-II 22-IR-1 visible-absorption workflow.
Takes raw spectrometer segments, concatenates the 4 grating segments per
measurement, computes absorbance A = -log10[(Sample - Dark)/(Background - Dark)],
and writes one tidy CSV per pressure point. NO defringe / notch / thickness here
(that is a separate downstream step, handled elsewhere).

Filename grammar (case-insensitive, optional trailing .csv twin tolerated):
    vis_{DAC}_{SAMPLE}_{PRESSURE}[_bg|_s][_<rep>].<seq>
  - no measurement suffix      -> dark
  - _bg                        -> background
  - _s                         -> sample
  - _<rep> (e.g. _2, _3)       -> replicate / retake of that measurement
  - <seq> = 001..004           -> grating segment, concatenated in order
  - PRESSURE encodes the decimal with 'p': 1p39 -> 1.39 GPa

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

    # Must end in a numeric segment: <base>.<seq>
    if "." not in name:
        return {"skip": True, "reason": "no segment number", "raw": raw}
    base, seq_str = name.rsplit(".", 1)
    if not seq_str.isdigit():
        return {"skip": True, "reason": "segment is not numeric", "raw": raw}
    # A clean base never contains a dot. A leftover dot means a double extension
    # like vis_..._bg.001.002 -- reject it loudly rather than mis-bin it.
    if "." in base:
        return {"skip": True, "reason": "malformed (extra extension)", "raw": raw}
    seq = int(seq_str)

    tokens = base.split("_")
    if len(tokens) < 4 or tokens[0].lower() != "vis":
        return {"skip": True, "reason": "does not match vis_DAC_SAMPLE_PRESSURE",
                "raw": raw}

    dac, sample, pressure_str = tokens[1], tokens[2], tokens[3]
    rest = tokens[4:]

    meas, rep = "dark", 1
    if rest and rest[0].lower() in ("bg", "s"):
        meas = "background" if rest[0].lower() == "bg" else "sample"
        rest = rest[1:]
    if rest and rest[0].isdigit():
        rep = int(rest[0])
        rest = rest[1:]
    branch = None
    if rest and rest[0].upper() in ("C", "D"):
        branch = rest[0].upper()        # optional compression/decompression tag
        rest = rest[1:]
    if rest:
        return {"skip": True, "reason": "unrecognized trailing token '%s'"
                % "_".join(rest), "raw": raw}

    try:
        pressure_val = float(pressure_str.replace("p", "."))
    except ValueError:
        return {"skip": True, "reason": "pressure '%s' not numeric" % pressure_str,
                "raw": raw}
    if pressure_val <= 0:
        return {"skip": True, "reason": "pressure <= 0", "raw": raw}

    return {"dac": dac, "sample": sample, "pressure_str": pressure_str,
            "pressure_val": pressure_val, "meas": meas, "rep": rep,
            "branch": branch, "seq": seq, "raw": raw}


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
    Build the absorbance curve for one (dac, sample, pressure) group.

    Uses the LATEST sample retake only (max replicate index), paired with the
    matching-rep background and dark when present, else the latest available.
    One curve per group.

    Returns list of result dicts (0 or 1 element):
      {label, dac, sample, pressure_str, pressure_val, rep,
       wl (nm), wn (cm^-1), absorbance, dark_c, bg_c, samp_c}
    Groups missing dark or background are skipped (warned).
    """
    dac, sample, pstr, branch = gkey
    meas = group["meas"]
    results = []

    if "sample" not in meas or "dark" not in meas or "background" not in meas:
        have = ", ".join(sorted(meas)) or "none"
        if warn:
            warn("%s_%s_%s: incomplete (have: %s) -- skipped"
                 % (dac, sample, pstr, have))
        return results

    for srep in [max(meas["sample"])]:
        s_segs = meas["sample"][srep]
        drep, d_segs = _pick_source(meas["dark"], srep)
        brep, b_segs = _pick_source(meas["background"], srep)

        common = sorted(set(s_segs) & set(d_segs) & set(b_segs))
        if not common:
            if warn:
                warn("%s_%s_%s rep%d: no shared segments -- skipped"
                     % (dac, sample, pstr, srep))
            continue

        wl, s_c = _concat_segments(s_segs, common)
        _, d_c = _concat_segments(d_segs, common)
        _, b_c = _concat_segments(b_segs, common)

        n = min(len(wl), len(s_c), len(d_c), len(b_c))
        wl, s_c, d_c, b_c = wl[:n], s_c[:n], d_c[:n], b_c[:n]

        order = np.argsort(wl)
        wl, s_c, d_c, b_c = wl[order], s_c[order], d_c[order], b_c[order]

        with np.errstate(divide="ignore", invalid="ignore"):
            trans = (s_c - d_c) / (b_c - d_c)
            absb = -np.log10(trans)
        absb[~np.isfinite(absb)] = np.nan

        wn = np.where(wl > 0, 1.0e7 / wl, np.nan)  # nm -> cm^-1
        label = "%s %s %.2f GPa" % (dac, sample, group["pressure_val"])
        if branch:
            label += " [%s]" % branch
        if srep > 1:
            label += " [latest retake r%d]" % srep
        results.append({"label": label, "dac": dac, "sample": sample,
                        "pressure_str": pstr,
                        "pressure_val": group["pressure_val"], "rep": srep,
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


def run(in_dir, out_dir, log=print):
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

    results = []
    for gkey in sorted(groups):
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
