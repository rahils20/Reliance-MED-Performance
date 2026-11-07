"""Regression tests for projection_engine.py (v2.0.0).

Run from the repository root:  python -m pytest tests/
Reference values marked PHREEQC were produced with PHREEQC 3 (phreeqc.dat) on the same concentrate
compositions, so they check the chemistry independently of this code.
"""
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import projection_engine as pe  # noqa: E402

DEF = dict(pe.DEFAULT_IONS)


def concentrate(ions, ph, temp_c, recovery, rejection=99.0):
    ions, _ = pe.balance_ions(ions)
    _perm, conc, cf = pe.stream_concentrations(ions, recovery, rejection)
    cph = ph + math.log10(cf)
    return conc, pe.calculate_scaling_indices(cph, temp_c, conc)


def test_carbonate_constants_25c():
    pK1, pK2, pKs = pe.carbonate_constants(25.0)
    assert pK1 == pytest.approx(6.351, abs=0.002)
    assert pK2 == pytest.approx(10.330, abs=0.002)
    assert pKs == pytest.approx(8.480, abs=0.002)


def test_default_water_is_balanced_and_co2_consistent():
    assert abs(pe.ion_balance(DEF)[2]) < 0.05
    assert pe.co2_from_alkalinity(DEF['HCO3'], 7.5, 25) == pytest.approx(DEF['CO2'], abs=0.2)


@pytest.mark.parametrize("ions,ph,t,rec,expected", [
    # PHREEQC saturation indices (log10 IAP/Ksp)
    (DEF, 7.5, 25, 75, {"CaSO4": -0.490, "BaSO4": 0.675, "SrSO4": -0.848, "CaF2": -0.096}),
    ({**DEF, 'Ca': 100, 'HCO3': 8, 'SO4': 1500, 'Na': 300, 'Cl': 200, 'Mg': 20}, 6.0, 25, 75,
     {"CaSO4": -0.002, "BaSO4": 1.151, "SrSO4": -0.203, "CaF2": -0.451}),
    ({**DEF, 'Ca': 0, 'Mg': 0.5, 'Ba': 0.15, 'Sr': 3.0, 'HCO3': 200, 'SO4': 400, 'Cl': 400}, 7.5, 25, 75,
     {"BaSO4": 1.460, "SrSO4": -0.068}),
])
def test_saturation_matches_phreeqc(ions, ph, t, rec, expected):  # [M1][M2][M8]
    _conc, idx = concentrate(ions, ph, t, rec)
    for salt, si in expected.items():
        assert idx[f"SIlog_{salt}"] == pytest.approx(si, abs=0.05), salt


def test_silica_matches_phreeqc():  # [M4]
    _conc, idx = concentrate(DEF, 7.5, 25, 75)
    assert math.log10(idx["Ratio_SiOH4"]) == pytest.approx(-0.291, abs=0.03)


def test_silica_solubility_rises_with_ph_and_temperature():  # [M4]
    assert pe.silica_solubility_mgL(25, 9.5) > pe.silica_solubility_mgL(25, 7.0) * 1.3
    assert pe.silica_solubility_mgL(35, 7.0) > pe.silica_solubility_mgL(25, 7.0)


@pytest.mark.parametrize("ions,ph,t,rec,phreeqc_mgL", [
    (DEF, 7.5, 25, 75, 417.0),
    ({**DEF, 'Ca': 200, 'HCO3': 400, 'SO4': 300, 'Ba': 0.1, 'Sr': 2.0, 'SiO2': 30, 'Cl': 400}, 7.3, 30, 80, 1047.0),
])
def test_caco3_precipitable_mass_matches_phreeqc(ions, ph, t, rec, phreeqc_mgL):  # [M13]
    conc, idx = concentrate(ions, ph, t, rec)
    assert pe.precipitable_mass_mgL("CaCO3", conc, idx) == pytest.approx(phreeqc_mgL, rel=0.03)


def test_barite_precipitable_mass_matches_phreeqc():  # [M13]
    ions = {**DEF, 'Ca': 0, 'Mg': 0.5, 'Ba': 0.15, 'Sr': 3.0, 'HCO3': 200, 'SO4': 400, 'Cl': 400}
    conc, idx = concentrate(ions, 7.5, 25, 75)
    assert pe.precipitable_mass_mgL("BaSO4", conc, idx) == pytest.approx(0.977, rel=0.03)


def test_mass_balance_closes():  # [M7]
    perm, conc, _cf = pe.stream_concentrations(DEF, 75, 99.0)
    for k in ('Ca', 'Cl', 'SO4'):
        assert 0.25 * conc[k] + 0.75 * perm[k] == pytest.approx(DEF[k], rel=1e-9)


def test_zero_calcium_still_evaluates_other_salts():  # [M3][B2]
    ions = {**DEF, 'Ca': 0, 'Mg': 0.5, 'Ba': 0.15, 'Sr': 3.0, 'HCO3': 200, 'SO4': 400, 'Cl': 400}
    p = pe.run_projection(ions, 10, 25, 75, 99.0, 7.5, 6.0)
    assert p["idx_conc"]["LSI"] is None
    assert p["idx_conc"]["Ratio_BaSO4"] > 10
    assert p["idx_conc"]["Ionic_Strength"] > 0.05
    assert p["successes"], "softened feed must not report 'no product'"


def test_negative_lsi_is_not_a_failure():  # [B1]
    ions = {**DEF, 'Ca': 100, 'HCO3': 8, 'SO4': 1500, 'Na': 300, 'Cl': 200, 'Mg': 20}
    p = pe.run_projection(ions, 10, 25, 75, 99.0, 6.0, 6.0, auto_acid=False, manual_ph=6.0)
    assert p["idx_conc"]["LSI"] < -0.5
    assert p["successes"]


def test_product_limits_reject_excess_saturation():  # [M11][M12]
    idx = {"LSI": 1.0, "Ratio_CaSO4": 26.0, "Ratio_BaSO4": 1.0, "Ratio_SrSO4": 0.1, "Ratio_CaF2": 0.1, "Ratio_SiOH4": 0.1}
    rows = pe.check_product_limits("Kem Watreat R 824", idx, "LSI")
    assert any(p == "CaSO4" and not ok for p, _v, _l, ok in rows)


def test_invalid_inputs_are_rejected():  # [B7][W4]
    p = pe.run_projection({**DEF, 'Ca': -50}, 0, 150, 75, 99.0, 15, 6.0)
    assert len(p["errors"]) >= 4


def test_caustic_manual_ph_is_not_used():  # [B10]
    p = pe.run_projection(DEF, 10, 25, 75, 99.0, 7.5, 6.0, auto_acid=False, manual_ph=8.5)
    assert p["treated_ph"] == 7.5
    assert any("caustic" in w for w in p["warnings"])


def test_hcl_adds_chloride_not_sulfate():  # [M15]
    hard = {**DEF, 'Ca': 200, 'HCO3': 400, 'SO4': 300, 'Cl': 515.4}
    p = pe.run_projection(hard, 10, 30, 80, 99.0, 7.8, 6.0, acid="HCl (33%)")
    assert p["acid_dose_ppm"] > 0
    assert p["treated_ions"]["SO4"] == pytest.approx(300.0)
    assert p["treated_ions"]["Cl"] > 515.4


def test_summary_is_data_driven():  # [B3]
    soft = {**DEF, 'Ca': 10, 'Mg': 3, 'HCO3': 30, 'SO4': 20, 'Cl': 60, 'Na': 40, 'Ba': 0, 'Sr': 0, 'F': 0}
    p = pe.run_projection(soft, 10, 25, 75, 99.0, 7.0, 6.0)
    text = pe.summary_text(p, "Kem Watreat R 4001", 1.0, 75)
    assert "not supersaturated" in text
    assert "imminent" not in text


def test_docx_report_builds():  # [B6]
    pytest.importorskip("docx")
    p = pe.run_projection(DEF, 10, 25, 75, 99.0, 7.5, 6.0)
    meta = dict(product=p["successes"][0]["Product"], dose=p["successes"][0]["Required Dose (ppm)"], recovery_pct=75,
                rejection_pct=99.0, feed_flow=10, temp_c=25, membrane_type="BWRO", feed_ph=7.5, entered_ions=DEF)
    data = pe.build_docx_report(p, meta)
    assert data[:2] == b"PK" and len(data) > 10000
