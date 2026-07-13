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
import json
import hashlib
import numpy as np

VALID_MEAS = ("dark", "background", "sample")


# ---------------------------------------------------------------------------
# Provenance sidecars (pure stdlib; the GUI supplies version/params/timestamp)
# ---------------------------------------------------------------------------
def file_sha1(path, _chunk=1 << 16):
    """SHA-1 of a file's bytes (for recording what an export actually wrote)."""
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(_chunk), b""):
            h.update(block)
    return h.hexdigest()


def write_provenance(sidecar_path, payload):
    """Write a provenance dict to sidecar_path as pretty JSON; returns the
    path. Non-serializable values fall back to str()."""
    with open(sidecar_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=1, default=str)
    return sidecar_path


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
# Naming profiles: user-defined filename grammars
# ---------------------------------------------------------------------------
# A profile is a JSON-able dict describing how a filename maps to the
# semantic fields the pipeline needs (who/where the measurement is, which
# channel it is, its pressure, and the grating-segment index). The classic
# 22-IR-1 grammar stays as the hand-written parser above and is selected by
# the sentinel BUILTIN_PROFILE (or profile=None), so its behavior can never
# drift. Custom profiles run through parse_with_profile below.

BUILTIN_PROFILE = {"builtin": True, "name": "22-IR-1 default"}

FIELD_CHOICES = ("dac", "sample", "pressure", "role", "branch", "rep",
                 "ignore")


def default_profile(name="custom"):
    """A fresh, editable profile template equivalent to the builtin grammar.
    The dialog starts from this when the user makes a new profile."""
    return {
        "name": name,
        "prefix": "vis",             # required first token ("" = none)
        "sep": "_",
        "order": ["dac", "sample", "pressure", "role", "branch", "rep"],
        "pressure_decimal": "p",     # '12p5' -> 12.5; also accepts '.'/','
        "pressure_unit_strip": ["gpa"],
        "role_map": {"bg": "background", "s": "sample", "": "dark"},
        "branch_tokens": {"c": "C", "d": "D"},
        "seq_sep": ".",              # '<base>.003' -> segment 3
        "seq_missing": 1,
        "defaults": {"pressure": 0.0, "role": "dark", "rep": 1},
    }


def validate_profile(profile):
    """Return a list of human-readable problems ([] = usable)."""
    probs = []
    if profile.get("builtin"):
        return probs
    if not profile.get("sep"):
        probs.append("separator is empty")
    order = profile.get("order") or []
    for f in order:
        if f not in FIELD_CHOICES:
            probs.append("unknown field '%s' in order" % f)
    for req in ("dac", "sample"):
        if req not in order:
            probs.append("'%s' missing from token order" % req)
    for k, v in (profile.get("role_map") or {}).items():
        if v not in VALID_MEAS:
            probs.append("role '%s' maps to unknown channel '%s'" % (k, v))
    if "role" in order and not (profile.get("role_map") or {}):
        probs.append("role is in the order but the role map is empty")
    return probs


def _parse_pressure_token(tok, dec, strip_units):
    """Try to read tok as a pressure. Returns (canonical_str, value) or
    (None, None). Accepts the profile decimal char plus '.' and ','."""
    t = tok.strip().lower()
    for u in (strip_units or []):
        u = (u or "").lower()
        if u and t.endswith(u):
            t = t[: -len(u)]
            break
    if not t:
        return None, None
    if dec and dec != ".":
        t = t.replace(dec, ".")
    t = t.replace(",", ".")
    try:
        v = float(t)
    except ValueError:
        return None, None
    if v < 0:
        return None, None
    return ("%g" % v).replace(".", "p"), v


def parse_with_profile(fname, profile=None):
    """Parse one filename with a naming profile. profile=None or the
    BUILTIN_PROFILE sentinel uses the classic hand-written 22-IR-1 parser;
    anything else runs the generic tokenizer. Returns the same record shape
    as parse_segment_filename."""
    if not profile or profile.get("builtin"):
        return parse_segment_filename(fname)

    raw = fname
    name = fname
    if name.lower().endswith(".csv"):
        name = name[:-4]

    # A trailing <seq_sep><digits> is the grating-segment index; anything
    # else after the separator is NOT treated as a segment (permissive:
    # dotted pressures like '1.5' must survive when seq_sep is '.').
    # Known ambiguity: if the pressure decimal char equals seq_sep and the
    # name has no real segment suffix ('...-2.5'), the '5' reads as a
    # segment. The dialog's live preview makes this visible; pick a
    # different decimal or segment separator in that case.
    seq_sep = profile.get("seq_sep", ".")
    seq = int(profile.get("seq_missing", 1) or 1)
    if seq_sep and seq_sep in name:
        base, seq_str = name.rsplit(seq_sep, 1)
        if seq_str.isdigit():
            seq, name = int(seq_str), base

    sep = profile.get("sep", "_") or "_"
    tokens = [t for t in name.split(sep) if t != ""]

    prefix = (profile.get("prefix") or "").strip()
    if prefix:
        if not tokens or tokens[0].lower() != prefix.lower():
            return {"skip": True,
                    "reason": "missing prefix '%s'" % prefix, "raw": raw}
        tokens = tokens[1:]

    order = list(profile.get("order") or [])
    role_map = {str(k).lower(): v
                for k, v in (profile.get("role_map") or {}).items()}
    branch_tokens = {str(k).lower(): v for k, v in
                     (profile.get("branch_tokens") or
                      {"c": "C", "d": "D"}).items()}
    dec = profile.get("pressure_decimal", "p")
    strip_units = profile.get("pressure_unit_strip", ["gpa"])
    defaults = profile.get("defaults") or {}

    p_default = defaults.get("pressure", 0.0)
    rec = {"dac": "", "sample": "",
           "pressure_str": ("%g" % p_default).replace(".", "p"),
           "pressure_val": float(p_default),
           "meas": role_map.get("", defaults.get("role", "dark")),
           "rep": int(defaults.get("rep", 1)), "branch": None,
           "seq": seq, "pdefault": True, "raw": raw}

    i = 0
    for field in order:
        tok = tokens[i] if i < len(tokens) else None
        if field == "dac" or field == "sample":
            if tok is None:
                return {"skip": True,
                        "reason": "missing %s token" % field, "raw": raw}
            rec[field] = tok
            i += 1
        elif field == "ignore":
            if tok is not None:
                i += 1
        elif field == "pressure":
            if tok is not None:
                ps, pv = _parse_pressure_token(tok, dec, strip_units)
                if ps is not None:
                    rec["pressure_str"], rec["pressure_val"] = ps, pv
                    rec["pdefault"] = False
                    i += 1
        elif field == "role":
            if tok is not None and tok.lower() in role_map:
                rec["meas"] = role_map[tok.lower()]
                i += 1
        elif field == "branch":
            if tok is not None and tok.lower() in branch_tokens:
                rec["branch"] = branch_tokens[tok.lower()]
                i += 1
        elif field == "rep":
            if tok is not None and tok.isdigit():
                rec["rep"] = int(tok)
                i += 1
    if i < len(tokens):
        return {"skip": True, "reason": "unrecognized trailing token '%s'"
                % sep.join(tokens[i:]), "raw": raw}
    if rec["meas"] not in VALID_MEAS:
        return {"skip": True, "reason": "role '%s' is not a valid channel"
                % rec["meas"], "raw": raw}
    return rec


def apply_override(rec, ov):
    """Apply a per-file user override (from the Fix-files grid) to a parsed
    record. ov keys: dac, sample, pressure (str or number), meas, branch,
    rep, seq, skip. A skipped record is resurrected when the override
    supplies at least a channel role (meas)."""
    if not ov:
        return rec
    if ov.get("skip"):
        return {"skip": True, "reason": "excluded by user",
                "raw": rec.get("raw")}
    if rec.get("skip") and not ov.get("meas"):
        return rec                      # not enough info to resurrect
    base = {"dac": "", "sample": "", "pressure_str": "0",
            "pressure_val": 0.0, "meas": "dark", "rep": 1, "branch": None,
            "seq": 1, "pdefault": False, "raw": rec.get("raw")}
    if not rec.get("skip"):
        base.update(rec)
    if "pressure" in ov and ov["pressure"] not in (None, ""):
        try:
            v = float(str(ov["pressure"]).lower().replace("p", "."))
        except ValueError:
            v = None
        if v is not None and v >= 0:
            base["pressure_val"] = v
            base["pressure_str"] = ("%g" % v).replace(".", "p")
            base["pdefault"] = False
    for k in ("dac", "sample", "meas", "branch"):
        if ov.get(k) not in (None, ""):
            base[k] = ov[k]
    for k in ("rep", "seq"):
        if ov.get(k) not in (None, ""):
            try:
                base[k] = int(ov[k])
            except (TypeError, ValueError):
                pass
    if base["meas"] not in VALID_MEAS:
        return {"skip": True, "reason": "override role '%s' invalid"
                % base["meas"], "raw": rec.get("raw")}
    return base



def health_flags(result, sat_ceiling=4.0):
    """Quick quality checks for one reduced point. Returns a list of
    (level, message), level 'warn' or 'bad'. Cheap (numpy only) and
    conservative: it flags things worth a second look at the beamline,
    it does not judge the science."""
    flags = []
    a = np.asarray(result.get("absorbance"), float)
    fin = np.isfinite(a)
    if a.size == 0 or not fin.any():
        chans = [nm for nm, key in (("sample", "samp_c"),
                                    ("background", "bg_c"),
                                    ("dark", "dark_c"))
                 if np.isfinite(np.asarray(result.get(key), float)).any()]
        flags.append(("bad", "no absorbance (raw channels: %s)"
                      % (", ".join(chans) or "none")))
        return flags
    vals = a[fin]
    n = vals.size
    sat = int((vals >= sat_ceiling).sum())
    if sat > max(3, 0.01 * n):
        flags.append(("warn", "%d point(s) at A >= %g: likely saturated or "
                      "blocked beam" % (sat, sat_ceiling)))
    neg = int((vals < -0.05).sum())
    if neg > 0.05 * n:
        flags.append(("warn", "negative absorbance over %d point(s): check "
                      "channel pairing / lamp drift" % neg))
    return flags


def guess_profile(fnames, name="guessed"):
    """Infer a naming profile from real filenames (the Name-format dialog's
    Guess button). Heuristic: choose the separator that tokenizes most
    consistently, take a shared literal first token as the prefix, then
    classify each token position (pressure / channel role / branch /
    retake); the first two unclaimed positions become cell and sample.
    Returns (profile, n_matched). The live preview is the real check."""
    ROLE_WORDS = {"bg": "background", "ref": "background",
                  "back": "background", "background": "background",
                  "s": "sample", "sam": "sample", "samp": "sample",
                  "sample": "sample", "sig": "sample",
                  "dark": "dark", "dk": "dark", "drk": "dark"}
    BRANCH_WORDS = {"c": "C", "comp": "C", "up": "C",
                    "d": "D", "dec": "D", "decomp": "D", "down": "D"}
    bases = []
    for f in fnames:
        n0 = f[:-4] if f.lower().endswith(".csv") else f
        if "." in n0:
            b, ext = n0.rsplit(".", 1)
            if ext.isdigit():
                n0 = b
            elif ext.isalpha() and len(ext) <= 4 and "." not in b:
                n0 = b          # a plain data extension (.txt, .dat, .asc)
        if n0:
            bases.append(n0)
    if not bases:
        return default_profile(name), 0
    best_sep, best_score = "_", -1.0
    for sep in ("_", "-", " ", "+"):
        counts = [len([t for t in b.split(sep) if t]) for b in bases]
        multi = sum(1 for c in counts if c >= 2) / float(len(counts))
        if multi == 0:
            continue
        avg = sum(counts) / float(len(counts))
        var = sum((c - avg) ** 2 for c in counts) / float(len(counts))
        score = multi * 3.0 - var * 0.2 + avg * 0.05
        if score > best_score:
            best_sep, best_score = sep, score
    sep = best_sep
    toks = [[t for t in b.split(sep) if t] for b in bases]
    toks = [t for t in toks if t]
    if not toks:
        return default_profile(name), 0
    first = {}
    for t in toks:
        k = t[0].lower()
        first[k] = first.get(k, 0) + 1
    pfx, pn = max(first.items(), key=lambda kv: kv[1])
    prefix = ""
    if pn >= 0.8 * len(toks) and not pfx[0].isdigit() and len(first) <= 2:
        prefix = pfx
        toks = [t[1:] if t and t[0].lower() == pfx else t for t in toks]
        toks = [t for t in toks if t]
        if not toks:
            return default_profile(name), 0
    ncol = max(len(t) for t in toks)
    if ncol < 2:
        return default_profile(name), 0

    def col(i):
        return [t[i] for t in toks if len(t) > i]

    press_col, press_frac, press_dec = None, 0.0, "p"
    for i in range(ncol):
        vals = col(i)
        hits, dec_hits = 0, {"p": 0, ".": 0, ",": 0}
        marked = 0
        for v in vals:
            for dec in ("p", ".", ","):
                ps, _pv = _parse_pressure_token(v, dec, ["gpa", "kbar"])
                if ps is not None:
                    hits += 1
                    dec_hits[dec] += 1
                    if not v.isdigit():
                        marked += 1
                    break
        frac = hits / float(len(vals) or 1)
        if hits and not marked:
            frac *= 0.5     # bare integers alone are a weak pressure signal
        if frac > max(press_frac, 0.45):
            press_col, press_frac = i, frac
            press_dec = max(dec_hits.items(), key=lambda kv: kv[1])[0]
    role_col, role_frac = None, 0.0
    for i in range(ncol):
        if i == press_col:
            continue
        vals = [v.lower() for v in col(i)]
        hits = sum(1 for v in vals if v in ROLE_WORDS)
        frac = hits / float(len(vals) or 1)
        if hits >= 2 and frac > role_frac:
            role_col, role_frac = i, frac
    branch_col = None
    for i in range(ncol):
        if i in (press_col, role_col):
            continue
        vals = [v.lower() for v in col(i)]
        hits = sum(1 for v in vals if v in BRANCH_WORDS)
        if hits and hits >= 0.5 * len(vals):
            branch_col = i
            break
    rep_col = None
    for i in range(ncol - 1, 1, -1):
        if i in (press_col, role_col, branch_col):
            continue
        vals = col(i)
        if vals and all(v.isdigit() and len(v) <= 2 for v in vals):
            rep_col = i
            break
    ids = [i for i in range(ncol)
           if i not in (press_col, role_col, branch_col, rep_col)]
    if len(ids) < 2:
        return default_profile(name), 0
    dac_col, sample_col = ids[0], ids[1]
    order = []
    for i in range(ncol):
        if i == dac_col:
            order.append("dac")
        elif i == sample_col:
            order.append("sample")
        elif i == press_col:
            order.append("pressure")
        elif i == role_col:
            order.append("role")
        elif i == branch_col:
            order.append("branch")
        elif i == rep_col:
            order.append("rep")
        else:
            order.append("ignore")
    role_map = {"": "dark"}
    if role_col is not None:
        for v in {v.lower() for v in col(role_col)}:
            if v in ROLE_WORDS:
                role_map[v] = ROLE_WORDS[v]
    if branch_col is not None:
        branch_tokens = {v.lower(): BRANCH_WORDS[v.lower()]
                         for v in set(col(branch_col))
                         if v.lower() in BRANCH_WORDS}
    else:
        branch_tokens = {"c": "C", "d": "D"}
    prof = {
        "name": name, "prefix": prefix, "sep": sep, "order": order,
        "pressure_decimal": press_dec,
        "pressure_unit_strip": ["gpa", "kbar"],
        "role_map": role_map,
        "branch_tokens": branch_tokens,
        "seq_sep": ".", "seq_missing": 1,
        "defaults": {"pressure": 0.0, "role": "dark", "rep": 1},
    }
    if validate_profile(prof):
        return default_profile(name), 0
    matched = sum(1 for f in fnames
                  if not parse_with_profile(f, prof).get("skip"))
    return prof, matched


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
def scan_folder(in_dir, profile=None, overrides=None):
    """
    Walk in_dir, parse every file, and group segments by measurement.

    profile: naming profile dict (None / BUILTIN_PROFILE = classic grammar).
    overrides: {filename: field-patch} from the Fix-files grid, applied
    after parsing (see apply_override); keys match the listed name or the
    name without its trailing .csv twin extension.

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
    overrides = overrides or {}

    for fname in sorted(os.listdir(in_dir)):
        full = os.path.join(in_dir, fname)
        if not os.path.isfile(full):
            continue
        info = parse_with_profile(fname, profile)
        ov = overrides.get(fname)
        if ov is None and fname.lower().endswith(".csv"):
            ov = overrides.get(fname[:-4])
        if ov is not None:
            info = apply_override(info, ov)
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
                                     "meas": {}})
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

        lens = {len(wl)} | {len(c) for c in chans.values()}
        if len(lens) > 1 and warn:
            warn("%s_%s_%s rep%d: channel lengths differ %s -- check for a "
                 "truncated segment file (points may misalign)"
                 % (dac, sample, pstr, arep, sorted(lens)))
        n = min(lens)
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
    """Write one result dict to {DAC}_{SAMPLE}_{PRESSURE}[_C|_D]_absorbance.csv.

    Only the latest retake of a group reaches this writer (process_group),
    so the filename needs no replicate suffix."""
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


def run(in_dir, out_dir, log=print, should_cancel=None, profile=None,
        overrides=None):
    """
    Full Stage-1 pass over a folder.

    profile/overrides: naming profile + per-file fixes (see scan_folder).
    Returns (results, skipped). Writes one CSV per result into out_dir and
    streams progress through log(callback).
    """
    os.makedirs(out_dir, exist_ok=True)
    if profile and not profile.get("builtin"):
        log("Naming profile: %s" % profile.get("name", "custom"))
    groups, skipped = scan_folder(in_dir, profile, overrides)
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
