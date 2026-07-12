"""
decomp.py  --  known decompression (D-branch) pressure points.

In a DAC run, pressure is ramped up (compression, C) then released
(decompression, D). The raw filenames do not record which branch a point is on,
so for the ten historical experiments we port the hardcoded lists from
DAC_AutoPlot_v4.ipf (GetDecompPressures). Only five of the ten had
decompression points; the other five are omitted here (empty in the .ipf)
and correctly fall through to the empty-set default. New experiments can
instead tag files with an optional _C / _D suffix, or be set manually in
the GUI.

Keyed by "{DAC}_{Sample}" (matches engine's dac/sample tokens). Values are sets
of pressure strings exactly as they appear in filenames (e.g. "21p1").

NQT / Lee Lab -- Jun 2026
"""

DECOMP = {
    "Boba_Alm100": {"70p3", "58p7", "56p9", "54p1", "50p9", "44p3", "41p1",
                    "34p6", "26p7", "18p3", "13p7", "8p22", "5p61", "1p39"},
    "Chewy_Alm100G": {"70p3", "65p8", "61p2", "55p2", "45p7", "37p4", "30p3",
                      "22p5", "17p3", "14p0", "10p3", "6p80", "3p49", "1p74"},
    "Chewy_ch29": {"58p4", "45p1", "32p9", "25p7", "14p7", "7p99", "4p47"},
    "Y04_ch114": {"52p3", "50p9", "48p8", "47p0", "43p5", "38p0", "33p5",
                  "27p0", "11p4", "3p73"},
    "Boba_ch114": {"56p9", "50p9", "42p9", "30p9", "21p1", "10p1", "3p73"},
}


def decompression_set(dac, sample):
    """Set of decompression pressure strings for a known experiment, else empty."""
    return DECOMP.get("%s_%s" % (dac, sample), set())
