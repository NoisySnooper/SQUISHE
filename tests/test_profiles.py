"""Naming-profile engine tests (v1.5 flexible ingestion).

Pure engine tests, no GUI. Lock the guarantees that (1) the builtin
grammar is byte-identical through the profile path, (2) custom profiles
parse reordered/re-separated names, and (3) per-file overrides can fix or
resurrect anything.
"""
import os
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
