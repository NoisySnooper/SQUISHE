"""Naming-profile engine tests (v1.5 flexible ingestion).

Pure engine tests, no GUI. Lock the guarantees that (1) the builtin
grammar is byte-identical through the profile path, (2) custom profiles
parse reordered/re-separated names, and (3) per-file overrides can fix or
resurrect anything.
"""
import os

import numpy as np

import engine


# ---- builtin passthrough --------------------------------------------------
def test_builtin_passthrough_identical():
    names = [
        "vis_Boba_Alm100_12p5_bg_C.001",
        "vis_D42_fo90_s.003",
        "vis_D42_fo90.002",                # dark
        "vis_D42_fo90_0p5_s_D_2.004",
        "not_a_vis_file.txt",
        "vis_D42.001",                     # too few tokens -> skip
    ]
    for n in names:
        a = engine.parse_segment_filename(n)
        b = engine.parse_with_profile(n, None)
        c = engine.parse_with_profile(n, engine.BUILTIN_PROFILE)
        assert a == b == c, n


# ---- custom grammars --------------------------------------------------------
def _dash_profile():
    p = engine.default_profile("dash")
    p["prefix"] = ""
    p["sep"] = "-"
    p["pressure_decimal"] = "."
    return p


def test_dash_separated_reordered():
    p = _dash_profile()
    r = engine.parse_with_profile("D42-fo90-15.3GPa-s-C-2.003", p)
    assert not r.get("skip")
    assert r["dac"] == "D42" and r["sample"] == "fo90"
    assert abs(r["pressure_val"] - 15.3) < 1e-9
    assert r["pressure_str"] == "15p3"
    assert r["meas"] == "sample" and r["branch"] == "C"
    assert r["rep"] == 2 and r["seq"] == 3
    assert r["pdefault"] is False


def test_optional_fields_absent():
    p = _dash_profile()
    r = engine.parse_with_profile("D42-fo90", p)
    assert not r.get("skip")
    assert r["pressure_val"] == 0.0 and r["pdefault"] is True
    assert r["meas"] == "dark" and r["rep"] == 1 and r["seq"] == 1


def test_comma_decimal_and_prefix():
    p = engine.default_profile("comma")
    p["prefix"] = "run"
    p["pressure_decimal"] = ","
    r = engine.parse_with_profile("run_A_B_12,5_bg", p)
    assert not r.get("skip")
    assert abs(r["pressure_val"] - 12.5) < 1e-9
    assert r["meas"] == "background"
    r2 = engine.parse_with_profile("walk_A_B_1p0_bg", p)
    assert r2.get("skip") and "prefix" in r2["reason"]


def test_branch_token_remap():
    p = _dash_profile()
    p["branch_tokens"] = {"up": "C", "down": "D"}
    r = engine.parse_with_profile("D42-fo90-1.5-s-up", p)
    assert not r.get("skip") and r["branch"] == "C", r
    r2 = engine.parse_with_profile("D42-fo90-1.5-s-down", p)
    assert r2["branch"] == "D"
    # empty branch_tokens falls back to the classic c / d
    p2 = _dash_profile()
    p2["branch_tokens"] = {}
    r3 = engine.parse_with_profile("D42-fo90-1.5-s-c", p2)
    assert not r3.get("skip") and r3["branch"] == "C", r3


def test_role_keyword_remap():
    p = _dash_profile()
    p["role_map"] = {"ref": "background", "smp": "sample", "": "dark"}
    r = engine.parse_with_profile("D42-fo90-1.5-ref", p)
    assert r["meas"] == "background"
    r2 = engine.parse_with_profile("D42-fo90-1.5-smp", p)
    assert r2["meas"] == "sample"
    r3 = engine.parse_with_profile("D42-fo90-1.5", p)
    assert r3["meas"] == "dark"


def test_ignore_field_and_trailing_junk():
    p = _dash_profile()
    p["order"] = ["ignore", "dac", "sample", "pressure", "role"]
    r = engine.parse_with_profile("junk-D42-fo90-2.0-s", p)
    assert not r.get("skip") and r["dac"] == "D42"
    p2 = _dash_profile()
    r2 = engine.parse_with_profile("D42-fo90-1.5-s-whatisthis", p2)
    assert r2.get("skip") and "trailing" in r2["reason"]


def test_validate_profile():
    p = _dash_profile()
    assert engine.validate_profile(p) == []
    p["role_map"] = {"x": "nonsense"}
    p["order"] = ["sample", "wat"]
    probs = engine.validate_profile(p)
    assert any("nonsense" in s for s in probs)
    assert any("wat" in s for s in probs)
    assert any("'dac' missing" in s for s in probs)
    assert engine.validate_profile(engine.BUILTIN_PROFILE) == []


# ---- overrides --------------------------------------------------------------
def test_override_patches_and_resurrects():
    rec = engine.parse_segment_filename("vis_D42_fo90_1p0_s.001")
    out = engine.apply_override(rec, {"pressure": "2.5", "meas": "background"})
    assert abs(out["pressure_val"] - 2.5) < 1e-9
    assert out["pressure_str"] == "2p5" and out["meas"] == "background"
    # resurrect a skipped file: role is the minimum needed
    bad = engine.parse_segment_filename("totally_random_name.001")
    assert bad.get("skip")
    res = engine.apply_override(bad, {"meas": "sample", "dac": "D9",
                                      "sample": "gl", "pressure": 3})
    assert not res.get("skip") and res["dac"] == "D9"
    assert res["pressure_str"] == "3"or res["pressure_str"] == "3p0" \
        or res["pressure_str"] == "3"
    still = engine.apply_override(bad, {"pressure": 3})   # no role -> stays
    assert still.get("skip")
    # explicit exclude
    ex = engine.apply_override(rec, {"skip": True})
    assert ex.get("skip") and "excluded" in ex["reason"]


# ---- scan_folder with profile + overrides ----------------------------------
def test_scan_folder_custom_profile(tmp_path):
    d = tmp_path / "raw"
    d.mkdir()
    for n in ("D42-fo90-1.5-s.001", "D42-fo90-1.5-ref.001",
              "D42-fo90-1.5.001", "mystery.001"):
        (d / n).write_text("x")
    p = _dash_profile()
    p["role_map"] = {"ref": "background", "s": "sample", "": "dark"}
    groups, skipped = engine.scan_folder(str(d), p)
    assert len(groups) == 1
    g = groups[("D42", "fo90", "1p5", None)]
    assert set(g["meas"]) == {"sample", "background", "dark"}
    assert [s["raw"] for s in skipped] == ["mystery.001"]
    # override resurrects the mystery file into the same group
    ov = {"mystery.001": {"meas": "sample", "dac": "D42", "sample": "fo90",
                          "pressure": "1.5", "rep": 2}}
    groups2, skipped2 = engine.scan_folder(str(d), p, ov)
    assert skipped2 == []
    assert 2 in groups2[("D42", "fo90", "1p5", None)]["meas"]["sample"]


# ---- guess_profile (the Name-format dialog's Guess button) -----------------

def test_guess_recovers_builtin_convention():
    names = ["vis_Y04_arch29_12p5_bg.001", "vis_Y04_arch29_12p5_s.001",
             "vis_Y04_arch29_12p5.001", "vis_Y04_arch29_26p0_bg.002",
             "vis_Y04_arch29_26p0_s.002"]
    prof, n = engine.guess_profile(names)
    assert prof["prefix"] == "vis"
    assert prof["sep"] == "_"
    assert prof["order"][:3] == ["dac", "sample", "pressure"]
    assert prof["role_map"].get("bg") == "background"
    assert prof["role_map"].get("s") == "sample"
    assert n == len(names)


def test_guess_dash_grammar_with_units():
    names = ["IR-Y04-arch29-12.5-bg-2.001", "IR-Y04-arch29-12.5-s-2.001",
             "IR-Y04-arch29-12.5-2.001", "IR-Y04-arch29-26.0GPa-bg.002"]
    prof, n = engine.guess_profile(names)
    assert prof["sep"] == "-"
    assert prof["prefix"] == "ir"
    assert "pressure" in prof["order"] and "role" in prof["order"]
    assert n == len(names)


def test_guess_gives_up_gracefully():
    prof, n = engine.guess_profile(["IMG0001", "IMG0002", "notes"])
    assert n == 0    # falls back to an editable default, matching nothing


# ---- health_flags -----------------------------------------------------------

def test_health_clean_point_has_no_flags():
    r = {"absorbance": np.linspace(0, 2, 200), "samp_c": np.ones(200),
         "bg_c": np.ones(200), "dark_c": np.zeros(200)}
    assert engine.health_flags(r) == []


def test_health_raw_only_is_bad():
    nan = np.full(50, np.nan)
    r = {"absorbance": nan, "samp_c": nan, "bg_c": np.ones(50),
         "dark_c": nan}
    flags = engine.health_flags(r)
    assert flags and flags[0][0] == "bad"
    assert "background" in flags[0][1]


def test_health_saturation_warns():
    a = np.linspace(0, 2, 200)
    a[:20] = 5.0
    r = {"absorbance": a, "samp_c": np.ones(200), "bg_c": np.ones(200),
         "dark_c": np.zeros(200)}
    flags = engine.health_flags(r)
    assert any("saturated" in m for _l, m in flags)


# ---- segment numbering (seq_sep / seq_scheme / seq_missing) -----------------

def _seg_profile(**kw):
    p = engine.default_profile("seg")
    p["prefix"] = ""
    p["sep"] = "-"
    p["pressure_decimal"] = "."
    p.update(kw)
    return p


def test_segment_padding_equivalent():
    p = _seg_profile()
    a = engine.parse_with_profile("D42-fo90.1", p)
    b = engine.parse_with_profile("D42-fo90.001", p)
    assert a["seq"] == b["seq"] == 1
    assert engine.parse_with_profile("D42-fo90.012", p)["seq"] == 12


def test_segment_multichar_separator():
    p = _seg_profile(seq_sep="_seg")
    r = engine.parse_with_profile("D42-fo90_seg003", p)
    assert not r.get("skip") and r["seq"] == 3
    # rightmost occurrence splits, and a dotted pressure survives intact
    r = engine.parse_with_profile("D42-fo90-2.5_seg7", p)
    assert not r.get("skip") and r["seq"] == 7
    assert abs(r["pressure_val"] - 2.5) < 1e-9


def test_segment_letters_scheme():
    p = _seg_profile(seq_scheme="letters")
    assert engine.parse_with_profile("D42-fo90.a", p)["seq"] == 1
    assert engine.parse_with_profile("D42-fo90.B", p)["seq"] == 2   # case
    assert engine.parse_with_profile("D42-fo90.aa", p)["seq"] == 27
    # a plain data extension must NOT read as a segment (len cap):
    # '.dat' stays in the token and the file counts as suffix-less
    r = engine.parse_with_profile("D42-fo90.dat", p)
    assert r["seq"] == 1 and r["sample"] == "fo90.dat"


def test_segment_missing_reject():
    p = _seg_profile(seq_missing="reject")
    r = engine.parse_with_profile("D42-fo90", p)
    assert r.get("skip") and "segment" in r.get("reason", "")
    r = engine.parse_with_profile("D42-fo90.2", p)
    assert not r.get("skip") and r["seq"] == 2


def test_segment_empty_separator():
    p = _seg_profile(seq_sep="")
    r = engine.parse_with_profile("D42-fo90", p)
    assert not r.get("skip") and r["seq"] == 1
    # nothing is split off: a dotted tail stays part of the token
    r = engine.parse_with_profile("D42-fo90.001", p)
    assert not r.get("skip") and r["seq"] == 1 and r["sample"] == "fo90.001"


def test_segment_missing_custom_index():
    p = _seg_profile(seq_missing=4)
    assert engine.parse_with_profile("D42-fo90", p)["seq"] == 4
    assert engine.parse_with_profile("D42-fo90.2", p)["seq"] == 2


def test_segment_validator():
    assert engine.validate_profile(_seg_profile()) == []
    assert any("scheme" in s for s in
               engine.validate_profile(_seg_profile(seq_scheme="roman")))
    assert any("whole number" in s for s in
               engine.validate_profile(_seg_profile(seq_missing=0)))
    assert any("whole number" in s for s in
               engine.validate_profile(_seg_profile(seq_missing="maybe")))
    assert any("separator" in s for s in
               engine.validate_profile(_seg_profile(seq_sep="",
                                                    seq_missing="reject")))
    assert engine.validate_profile(_seg_profile(seq_missing="reject")) == []


def test_guess_detects_letter_segments():
    names = ["run_D1_x_10.5_s.a", "run_D1_x_10.5_bg.a",
             "run_D1_x_10.5_s.b", "run_D1_x_10.5_bg.b",
             "run_D1_x_12_s.a", "run_D1_x_12_bg.a"]
    prof, n = engine.guess_profile(names)
    assert prof["seq_sep"] == "." and prof["seq_scheme"] == "letters"
    assert prof["seq_missing"] == "reject"     # every file was numbered
    assert n == len(names)
    r = engine.parse_with_profile("run_D1_x_10.5_s.b", prof)
    assert not r.get("skip") and r["seq"] == 2


def test_guess_partial_coverage_keeps_default_missing():
    names = ["run_D1_x_10.5_s.001", "run_D1_x_10.5_s.002",
             "run_D1_x_10.5_bg.001", "run_D1_x_10.5_bg.002",
             "run_D1_x_12_s", "run_D1_x_12_bg"]
    prof, n = engine.guess_profile(names)
    assert prof["seq_sep"] == "." and prof["seq_scheme"] == "digits"
    assert prof["seq_missing"] == 1            # bare names exist -> seg 1
    assert n == len(names)


def test_override_sets_segment_index():
    p = _seg_profile()
    rec = engine.parse_with_profile("D42-fo90.001", p)
    fixed = engine.apply_override(rec, {"seq": "5"})
    assert fixed["seq"] == 5 and not fixed.get("skip")


# ---- dac / sample omissible via defaults ------------------------------------

def test_defaulted_dac_and_sample():
    p = _seg_profile()
    p["order"] = ["sample", "pressure", "role", "branch", "rep"]
    p["defaults"] = dict(p["defaults"], dac="D42")
    assert engine.validate_profile(p) == []
    r = engine.parse_with_profile("fo90-12.5-s.001", p)
    assert not r.get("skip")
    assert r["dac"] == "D42" and r["sample"] == "fo90"
    p2 = _seg_profile()
    p2["order"] = ["pressure", "role"]
    p2["defaults"] = dict(p2["defaults"], dac="D42", sample="fo90")
    r = engine.parse_with_profile("12.5-s.002", p2)
    assert not r.get("skip")
    assert (r["dac"], r["sample"], r["seq"]) == ("D42", "fo90", 2)


def test_missing_dac_without_default_is_flagged():
    p = _seg_profile()
    p["order"] = ["sample", "pressure"]
    probs = engine.validate_profile(p)
    assert any("dac" in s for s in probs)
    p["defaults"] = dict(p["defaults"], dac="D42")
    assert engine.validate_profile(p) == []


# ---- separator alternatives + guesser retry ---------------------------------

def test_separator_alternatives():
    p = _seg_profile(sep="_,-")
    r = engine.parse_with_profile("D42-fo90_10.5-bg.001", p)
    assert not r.get("skip")
    assert (r["dac"], r["sample"], r["meas"]) == ("D42", "fo90",
                                                  "background")
    assert abs(r["pressure_val"] - 10.5) < 1e-9 and r["seq"] == 1
    toks, gaps = engine.split_tokens_gaps("D42-fo90_10.5-bg", "_,-")
    assert toks == ["D42", "fo90", "10.5", "bg"]
    assert gaps == ["-", "_", "-"]


def test_separator_literal_comma():
    assert engine.split_tokens("a,b,c", ",") == ["a", "b", "c"]


def test_guess_single_cell_no_prefix():
    names = ["D42_ol1_10p5_bg.001", "D42_ol1_10p5_bg.002",
             "D42_ol1_10p5_s.001", "D42_ol1_10p5_s.002",
             "D42_ol1_12p0_bg.001", "D42_ol1_12p0_s.001"]
    prof, n = engine.guess_profile(names)
    # the shared dac token must NOT be eaten as a prefix
    assert prof["prefix"] == "" and n == len(names)
    r = engine.parse_with_profile(names[0], prof)
    assert (r["dac"], r["sample"], r["meas"]) == ("D42", "ol1",
                                                  "background")
