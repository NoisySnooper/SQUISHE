"""
End-to-end engine.run tests on synthetic CSVs (no real beamline data needed).

Locks the v1.2.x behavior: single-stitch (no .seq) files, 0 / assumed-0 GPa,
partial-channel loading with [B only] / [S+D] tags, and the mixed-branch
sort fix (same pressure with branch None and 'C' must not crash).
"""
import os
import numpy as np
import engine


def _spec(path, counts, n=11, wl0=400.0):
    """Write a 2-column wavelength,counts spectrum the reader accepts."""
    with open(path, "w", newline="") as f:
        for i in range(n):
            f.write("%.1f,%.1f\n" % (wl0 + i, counts))


def _run(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    return engine.run(str(tmp_path), str(out), log=lambda s: None)


def test_full_group_single_stitch(tmp_path):
    # dark=100, background=1000, sample=600 -> A finite and positive
    _spec(tmp_path / "vis_D_oliv_0p1", 100.0)
    _spec(tmp_path / "vis_D_oliv_0p1_bg", 1000.0)
    _spec(tmp_path / "vis_D_oliv_0p1_s", 600.0)
    results, skipped = _run(tmp_path)
    assert skipped == []
    oliv = [r for r in results if r["sample"] == "oliv"]
    assert len(oliv) == 1
    assert np.isfinite(oliv[0]["absorbance"]).any()


def test_background_only_is_raw(tmp_path):
    _spec(tmp_path / "vis_D_glass_0_bg", 900.0)
    results, _ = _run(tmp_path)
    glass = [r for r in results if r["sample"] == "glass"]
    assert len(glass) == 1
    assert "[B only]" in glass[0]["label"]
    assert not np.isfinite(glass[0]["absorbance"]).any()


def test_sample_plus_dark_is_raw(tmp_path):
    _spec(tmp_path / "vis_D_quartz_1p0_s", 600.0)
    _spec(tmp_path / "vis_D_quartz_1p0", 100.0)
    results, _ = _run(tmp_path)
    q = [r for r in results if r["sample"] == "quartz"]
    assert len(q) == 1
    assert "[S+D]" in q[0]["label"]
    assert not np.isfinite(q[0]["absorbance"]).any()


def test_assumed_zero_pressure(tmp_path):
    _spec(tmp_path / "vis_D_ice_bg", 800.0)   # no pressure field -> 0 GPa
    results, _ = _run(tmp_path)
    ice = [r for r in results if r["sample"] == "ice"]
    assert len(ice) == 1 and ice[0]["pressure_val"] == 0.0


def test_mixed_branch_does_not_crash(tmp_path):
    # same (dac, sample, pressure) with branch None and branch 'C':
    # the pre-fix code raised TypeError sorting None against 'C'.
    _spec(tmp_path / "vis_M_x_2p0", 100.0)      # dark, branch None
    _spec(tmp_path / "vis_M_x_2p0_C", 100.0)    # dark, branch C
    results, skipped = _run(tmp_path)           # must not raise
    xs = [r for r in results if r["sample"] == "x"]
    assert len(xs) >= 2


def test_unparsable_file_skipped_not_fatal(tmp_path):
    _spec(tmp_path / "vis_D_oliv_0p1", 100.0)
    _spec(tmp_path / "vis_D_oliv_0p1_bg", 1000.0)
    _spec(tmp_path / "vis_D_oliv_0p1_s", 600.0)
    with open(tmp_path / "notes.txt", "w") as f:
        f.write("just my notes, not a spectrum\n")
    results, skipped = _run(tmp_path)
    assert any(r["sample"] == "oliv" for r in results)
    assert any("notes" in s["raw"] for s in skipped)


def test_provenance_and_sha1(tmp_path):
    """file_sha1 is stable and write_provenance round-trips as JSON."""
    import json
    import hashlib
    f = tmp_path / "x.csv"
    f.write_text("Wavelength_nm,Absorbance\n400,0.1\n", encoding="utf-8")
    got = engine.file_sha1(str(f))
    want = hashlib.sha1(f.read_bytes()).hexdigest()
    assert got == want and len(got) == 40

    side = tmp_path / "x.csv.provenance.json"
    payload = {"tool": "t", "version": "v1.4.0-dev", "kind": "smoothed_csv",
               "params": {"n_csv": 1}, "files": [{"name": "x.csv", "sha1": got}]}
    engine.write_provenance(str(side), payload)
    back = json.loads(side.read_text(encoding="utf-8"))
    assert back == payload
