"""
Filename-grammar tests for engine.parse_segment_filename.

These lock the parser behavior added in v1.2.x: optional segment suffix,
0 GPa allowed, missing-pressure-defaults-to-0, branch/rep in either order,
and the rejection paths. Pure string logic, no file I/O.
"""
import engine


def P(name):
    return engine.parse_segment_filename(name)


def test_full_name_with_segment():
    r = P("vis_DAC1_olivine_1p39_s.001")
    assert not r.get("skip")
    assert r["dac"] == "DAC1" and r["sample"] == "olivine"
    assert r["pressure_val"] == 1.39 and r["meas"] == "sample"
    assert r["seq"] == 1 and r["pdefault"] is False


def test_missing_segment_is_single_stitch():
    r = P("vis_DAC1_olivine_1p39_s")
    assert not r.get("skip") and r["seq"] == 1


def test_segment_optional_on_dark():
    r = P("vis_DAC1_olivine_1p39")
    assert not r.get("skip") and r["meas"] == "dark" and r["seq"] == 1


def test_zero_pressure_allowed():
    r = P("vis_DAC1_olivine_0_bg")
    assert not r.get("skip")
    assert r["pressure_val"] == 0.0 and r["meas"] == "background"
    assert r["pdefault"] is False


def test_missing_pressure_defaults_to_zero():
    r = P("vis_DAC1_olivine_bg")
    assert not r.get("skip")
    assert r["pressure_val"] == 0.0 and r["pdefault"] is True
    assert r["meas"] == "background"


def test_missing_pressure_bare_dark():
    r = P("vis_DAC1_olivine")
    assert not r.get("skip")
    assert r["pressure_val"] == 0.0 and r["pdefault"] is True


def test_branch_and_rep_either_order():
    a = P("vis_DAC1_olivine_1p0_s_C_2")
    b = P("vis_DAC1_olivine_1p0_s_2_C")
    for r in (a, b):
        assert not r.get("skip")
        assert r["branch"] == "C" and r["rep"] == 2


def test_csv_twin_stripped():
    r = P("vis_DAC1_olivine_1p39_s.001.csv")
    assert not r.get("skip") and r["seq"] == 1 and r["meas"] == "sample"


def test_negative_pressure_rejected():
    r = P("vis_DAC1_olivine_-1")
    assert r.get("skip") and "< 0" in r["reason"]


def test_garbage_pressure_rejected():
    r = P("vis_DAC1_olivine_5o2_s")
    assert r.get("skip") and "not numeric" in r["reason"]


def test_non_numeric_segment_rejected():
    r = P("vis_DAC1_olivine_1p39.abc")
    assert r.get("skip") and "segment" in r["reason"]


def test_double_extension_rejected():
    r = P("vis_DAC1_olivine_1p39_bg.001.002")
    assert r.get("skip") and "extension" in r["reason"]


def test_non_vis_rejected():
    r = P("foo_bar_baz.001")
    assert r.get("skip")
