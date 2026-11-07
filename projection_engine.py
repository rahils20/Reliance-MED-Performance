"""
RO Projection Engine - antiscalant and acid dosing projection for reverse osmosis systems.

ENGINE VERSION 2.0.0 (7 Nov 2026)

This version implements the corrections listed in "RO_Projection_Engine_Review.pdf" (docs/review/).
Review finding IDs are quoted in comments as [M1], [B3], ... so every change can be traced back.

Layout of this file
  1. Constants and editable data tables (formulations, k-factors, PRODUCT LIMITS, SG, prices)
  2. Pure chemistry functions (no Streamlit needed - Flux can call these from its backend)
  3. Report builder (Word .docx, pure function)
  4. Streamlit UI (UtilityProjectionEngine.render_engine), used by streamlit_app.py

Summary of changes from v1 (see the review PDF for detail)
  [M1]  Solubility products are corrected with the same Davies activity coefficients as the LSI
        (the v1 charge factor was wrong and overstated sulfate/fluoride saturation ~3x).
  [M2]  Ion pairing (CaSO4, MgSO4, NaSO4-, KSO4-, SrSO4, BaSO4, CaHCO3+, MgHCO3+, MgF+).
  [M3]  Ca = 0 or HCO3 = 0 now only disables LSI/S&DSI - every other salt is still evaluated.
  [M4]  Amorphous silica solubility corrected for temperature (Fournier & Rowe) and pH.
  [M5]  Unvalidated metal-silicate indices removed; replaced by a qualitative high-pH advisory.
  [M6]  CO2 calculated from pH and alkalinity, acid-generated CO2 carried; entered CO2 cross-checked.
  [M7]  Exact concentrate/permeate mass balance with salt passage, from the TREATED feed.
  [M8]  Temperature-dependent log K (van 't Hoff) with sources (phreeqc.dat) cited in one table.
  [M9]  Governing CaCO3 index chosen by concentrate TDS (LSI <= 10,000 mg/L, S&DSI above).
  [M10] TDS excludes dissolved CO2; Fe/Al shown as a feed pre-treatment advisory, not as an "SI";
        balanced default water.
  [M11] Product maximum-limit table (PRODUCT_LIMITS) - PLACEHOLDER values to be replaced by
        Chembond lab/field data. A product is rejected when the concentrate exceeds its limits.
  [M12] BaSO4 / SrSO4 / CaF2 judged on saturation against the product limit (no mass escape).
  [M13] Precipitable mass solved from the equilibrium condition (no arbitrary 0.05 factor,
        no double counting for silica); one shared implementation.
  [M14] Viable products ranked by required dose, or by cost/m3 once prices are entered.
  [M15] Acid optimiser: H2SO4 or HCl, adjustable LSI target, warns when the target is unreachable
        and when H2SO4 pushes CaSO4 over saturation.
  [M16] Dose labelled as product dose on feed flow.
  [B1]  Undersaturated CaCO3 (negative LSI) is no longer treated as a failure.
  [B2]  Softened / dealkalised feeds can be simulated (follows from M3 + B1).
  [B3]  Executive summary generated from the results; measured wording; disclaimer added.
  [B4]  Hard-coded CIP / ROI block removed.
  [B5]  Report table shows the real concentrate values against product limits (no 1.000 rows).
  [B6]  Report includes the full inputs, assumptions, engine version; charts via matplotlib in
        memory (no kaleido, no temp files).
  [B7]  Inputs validated explicitly; errors shown instead of being swallowed.
  [B8]  Custom blend total validated (<= 100 %), message survives the rerun, blend can be cleared.
  [B9]  Dose limits aligned (1-8 ppm everywhere); set_page_config only when run standalone;
        print CSS no longer relies on auto-generated class names.
  [B10] NEW: a manual "adjusted pH" above the raw pH (caustic) was used for the concentrate pH
        although no caustic was dosed - now rejected with a warning.
  [S1]  Admin override code removed from source; read from st.secrets["projection_admin_code"].
"""

import hmac
import math
from datetime import datetime, timedelta, timezone
from io import BytesIO

try:  # UI dependencies are optional so the chemistry can be imported/tested without them.
    import streamlit as st
except ImportError:  # pragma: no cover
    st = None
try:
    import pandas as pd
except ImportError:  # pragma: no cover
    pd = None
try:
    import plotly.express as px
except ImportError:  # pragma: no cover
    px = None

ENGINE_VERSION = "2.0.0 (2026-11-07)"

# =====================================================================================
# 1. CONSTANTS AND EDITABLE DATA TABLES
# =====================================================================================

# Dose search window, used everywhere (search, finalise input, slider, messages)  [B9]
MIN_DOSE_PPM = 1.0
MAX_DOSE_PPM = 8.0
DOSE_STEP_PPM = 0.5

# Report timestamps are printed in one labelled time zone  [W7]
REPORT_TZ = timezone(timedelta(hours=5, minutes=30))
REPORT_TZ_LABEL = "IST (UTC+05:30)"

# Lowest treated pH the acid optimiser will go to (below ~5 the alkalinity is gone)  [M15]
ACID_PH_FLOOR = 5.0

# Concentrate TDS above which S&DSI (not LSI) governs CaCO3 scaling  [M9]
SDSI_TDS_THRESHOLD = 10000.0
# Davies activity model is not reliable above this ionic strength (mol/L)
DAVIES_MAX_I = 0.5

ION_PROPS = {
    'Ca': {'mw': 40.08, 'z': 2}, 'Mg': {'mw': 24.31, 'z': 2},
    'Na': {'mw': 22.99, 'z': 1}, 'K': {'mw': 39.10, 'z': 1},
    'NH4': {'mw': 18.04, 'z': 1}, 'Ba': {'mw': 137.33, 'z': 2},
    'Sr': {'mw': 87.62, 'z': 2}, 'Fe': {'mw': 55.84, 'z': 2},
    'Al': {'mw': 26.98, 'z': 3}, 'HCO3': {'mw': 61.02, 'z': -1},
    'Cl': {'mw': 35.45, 'z': -1}, 'SO4': {'mw': 96.06, 'z': -2},
    'F': {'mw': 19.00, 'z': -1}, 'NO3': {'mw': 62.00, 'z': -1},
    'PO4': {'mw': 94.97, 'z': -3}, 'CO3': {'mw': 60.01, 'z': -2},
    'SiO2': {'mw': 60.08, 'z': 0}, 'CO2': {'mw': 44.01, 'z': 0},
}
CATIONS = ['Ca', 'Mg', 'Na', 'K', 'NH4', 'Ba', 'Sr', 'Fe', 'Al']
ANIONS = ['HCO3', 'Cl', 'SO4', 'F', 'NO3', 'PO4', 'CO3']
GASES = ['CO2']  # pass the membrane unchanged

# Equivalent weights (mg/meq). Iron is taken as Fe2+  [M10]
EQ_WT = {ion: ION_PROPS[ion]['mw'] / abs(ION_PROPS[ion]['z']) for ion in CATIONS + ANIONS}

# Default water - now ion-balanced (Cl 400 -> 587.3 mg/L) and CO2 consistent with pH 7.5 (5 -> 12.8)  [M6][M10]
DEFAULT_IONS = {
    'Ca': 150.0, 'Mg': 50.0, 'Na': 300.0, 'K': 10.0, 'NH4': 0.0,
    'Ba': 0.05, 'Sr': 1.2, 'Fe': 0.02, 'Al': 0.01,
    'HCO3': 250.0, 'Cl': 587.3, 'SO4': 200.0, 'F': 0.5,
    'NO3': 5.0, 'PO4': 0.0, 'CO3': 0.0, 'SiO2': 15.0, 'CO2': 12.8,
}

ION_LABELS = {
    'Ca': 'Ca++', 'Mg': 'Mg++', 'Na': 'Na+', 'K': 'K+', 'NH4': 'NH4+', 'Ba': 'Ba++', 'Sr': 'Sr++',
    'Fe': 'Fe (as Fe2+)', 'Al': 'Al+++', 'HCO3': 'HCO3-', 'Cl': 'Cl-', 'SO4': 'SO4--', 'F': 'F-',
    'NO3': 'NO3-', 'PO4': 'PO4---', 'CO3': 'CO3--', 'SiO2': 'SiO2', 'CO2': 'CO2',
}

# Thermodynamic data at 25 C: log K and dissolution/association enthalpy (kcal/mol), taken from
# the PHREEQC database phreeqc.dat (Parkhurst & Appelo). Extrapolated with van 't Hoff.  [M8]
MINERALS = {
    #          log Ksp,  dH (kcal), cation, anion, anion stoichiometry, formula weight
    # "analytic" = phreeqc.dat analytical expression A1 + A2*T + A3/T + A4*log10(T) (T in K); used
    # in preference to log K/dH exactly as PHREEQC does.
    "CaSO4": {"logk": -4.58, "dh": -0.109, "analytic": (68.2401, 0.0, -3221.51, -25.0627),
              "cat": "Ca", "an": "SO4", "n": 1, "fw": 136.14, "name": "Gypsum"},
    "BaSO4": {"logk": -9.97, "dh": 6.35, "analytic": (-282.43, -8.972e-2, 5822.0, 113.08),
              "cat": "Ba", "an": "SO4", "n": 1, "fw": 233.39, "name": "Barite"},
    "SrSO4": {"logk": -6.63, "dh": -4.037, "analytic": None,
              "cat": "Sr", "an": "SO4", "n": 1, "fw": 183.68, "name": "Celestite"},
    "CaF2": {"logk": -10.60, "dh": 4.69, "analytic": (66.348, 0.0, -4298.2, -25.271),
             "cat": "Ca", "an": "F", "n": 2, "fw": 78.08, "name": "Fluorite"},
}
ION_PAIRS = [
    # cation, anion, log K (association), dH (kcal/mol), charge of the pair     [M2]
    ("Ca", "SO4", 2.25, 1.325, 0), ("Mg", "SO4", 2.37, 4.55, 0),
    ("Na", "SO4", 0.70, 1.12, -1), ("K", "SO4", 0.85, 2.25, -1),
    ("Sr", "SO4", 2.29, 2.08, 0), ("Ba", "SO4", 2.70, 0.0, 0),
    ("Ca", "HCO3", 1.106, 2.69, 1), ("Mg", "HCO3", 1.07, 0.79, 1),
    ("Mg", "F", 1.82, 3.20, 1),  # CaF+ omitted: commented out in current phreeqc.dat
]

# ---- Product data ------------------------------------------------------------------
FORMULATIONS = {
    "Kem Watreat R 824": {"homopolymer": 0.40},
    "Kem Watreat R 246": {"terpolymer": 0.30},
    "Kem Watreat R 428 I": {"homopolymer": 0.10, "hedp": 0.077},
    "Kem Watreat R 4001": {"atmp": 0.0375, "hedp": 0.066},
    "Kem Watreat R 170": {"pbtc": 0.0054, "detmpa": 0.135, "homopolymer": 0.06},
    "Kem Watreat R 6863": {"atmp": 0.1125, "hedp": 0.0275, "copolymer": 0.02},
    "Kem Watreat R 6196": {"atmp": 0.1875, "smbs": 0.03, "homopolymer": 0.04},
    "Kem Watreat R 428 ID": {"hedp": 0.04565, "homopolymer": 0.048},
    "Kem Watreat R 4002": {"hedp": 0.066, "atmp": 0.0175},
    "Kem Watreat R 3687": {"hedp": 0.1375, "pma": 0.0326},
}
CUSTOM_BLEND_NAME = "Kem Watreat Custom Blend"

# Catalogue order, used as the tie-breaker after dose/cost  [M14]
CATALOG_ORDER = [
    "Kem Watreat R 4001", "Kem Watreat R 4002", "Kem Watreat R 428 ID",
    "Kem Watreat R 428 I", "Kem Watreat R 6196", "Kem Watreat R 824",
    "Kem Watreat R 246", "Kem Watreat R 6863", "Kem Watreat R 170", "Kem Watreat R 3687",
]

# Heuristic inhibition factors (unchanged from v1 - still need calibration, see [M11])
_KF_SALTS = ["LSI", "SDSI", "CaCO3", "CaSO4", "BaSO4", "SrSO4", "CaF2", "Si(OH)4", "SiO2"]
def _kf(lsi, caso4, ba_sr, caf2, si):
    return dict(zip(_KF_SALTS, [lsi, lsi, lsi, caso4, ba_sr, ba_sr, caf2, si, si]))
K_FACTORS = {
    "pbtc": _kf(3.50, 1.50, 0.50, 0.50, 0.0),
    "detmpa": _kf(2.20, 3.00, 4.50, 1.50, 0.0),
    "hedp": _kf(2.65, 2.20, 0.80, 1.50, 0.0),
    "atmp": _kf(2.80, 1.80, 1.00, 1.20, 0.0),
    "homopolymer": _kf(1.00, 1.50, 0.50, 0.50, 0.20),
    "copolymer": _kf(1.40, 2.80, 3.00, 1.80, 1.50),
    "terpolymer": _kf(1.88, 2.50, 2.50, 2.00, 3.50),
    "pma": _kf(2.50, 3.50, 1.50, 1.00, 0.0),
    "smbs": _kf(0.0, 0.0, 0.0, 0.0, 0.0),
}
POLYMERS = ["homopolymer", "copolymer", "terpolymer", "pma"]

# ---------------------------------------------------------------------------------------
# PRODUCT MAXIMUM LIMITS  [M11][M12]
# !!! PLACEHOLDER VALUES - typical published ranges for this class of chemistry, NOT Chembond
# !!! data. Replace with limits from Chembond induction-time / jar tests and field history.
# LSI = max concentrate LSI (or S&DSI); salts = max saturation ratio IAP/Ksp; SiO2 = max ratio of
# concentrate silica to the temperature/pH-corrected amorphous silica solubility.
# ---------------------------------------------------------------------------------------
PRODUCT_LIMITS = {
    "Kem Watreat R 824":    {"LSI": 1.8, "CaSO4": 2.0, "BaSO4": 30, "SrSO4": 6,  "CaF2": 20, "SiO2": 1.0},
    "Kem Watreat R 246":    {"LSI": 2.0, "CaSO4": 2.5, "BaSO4": 40, "SrSO4": 8,  "CaF2": 30, "SiO2": 2.0},
    "Kem Watreat R 428 I":  {"LSI": 2.5, "CaSO4": 2.5, "BaSO4": 40, "SrSO4": 8,  "CaF2": 50, "SiO2": 1.0},
    "Kem Watreat R 428 ID": {"LSI": 2.5, "CaSO4": 2.5, "BaSO4": 40, "SrSO4": 8,  "CaF2": 50, "SiO2": 1.0},
    "Kem Watreat R 4001":   {"LSI": 2.5, "CaSO4": 2.0, "BaSO4": 40, "SrSO4": 8,  "CaF2": 50, "SiO2": 1.0},
    "Kem Watreat R 4002":   {"LSI": 2.5, "CaSO4": 2.0, "BaSO4": 40, "SrSO4": 8,  "CaF2": 50, "SiO2": 1.0},
    "Kem Watreat R 170":    {"LSI": 2.8, "CaSO4": 3.0, "BaSO4": 80, "SrSO4": 12, "CaF2": 50, "SiO2": 1.0},
    "Kem Watreat R 6863":   {"LSI": 2.5, "CaSO4": 2.5, "BaSO4": 60, "SrSO4": 10, "CaF2": 50, "SiO2": 1.5},
    "Kem Watreat R 6196":   {"LSI": 2.5, "CaSO4": 2.0, "BaSO4": 40, "SrSO4": 8,  "CaF2": 30, "SiO2": 1.0},
    "Kem Watreat R 3687":   {"LSI": 2.5, "CaSO4": 3.0, "BaSO4": 50, "SrSO4": 8,  "CaF2": 50, "SiO2": 1.0},
}
DEFAULT_PRODUCT_LIMITS = {"LSI": 2.0, "CaSO4": 2.0, "BaSO4": 30, "SrSO4": 6, "CaF2": 20, "SiO2": 1.0}
LIMIT_KEYS = [("LSI", None), ("CaSO4", "Ratio_CaSO4"), ("BaSO4", "Ratio_BaSO4"),
              ("SrSO4", "Ratio_SrSO4"), ("CaF2", "Ratio_CaF2"), ("SiO2", "Ratio_SiOH4")]

# Product specific gravity for L/h conversion - PLACEHOLDER (1.20 for R 3687 per the MED module)
PRODUCT_SG = {name: 1.15 for name in FORMULATIONS}
PRODUCT_SG["Kem Watreat R 3687"] = 1.20
# Product price (Rs/kg) - enter values to rank viable products by cost/m3  [M14]
PRODUCT_PRICE_PER_KG = {name: None for name in FORMULATIONS}

ACIDS = {
    "H2SO4 (98%)": {"mw": 98.08, "eq_per_mol": 2, "strength": 0.98, "sg": 1.84, "anion": "SO4", "anion_mw": 96.06},
    "HCl (33%)": {"mw": 36.46, "eq_per_mol": 1, "strength": 0.33, "sg": 1.16, "anion": "Cl", "anion_mw": 35.45},
}

# =====================================================================================
# 2. PURE CHEMISTRY FUNCTIONS
# =====================================================================================

def davies_A(temp_c):
    """Debye-Hueckel A (log10 basis), linear fit 0-50 C."""
    return 0.4918 + 0.0007 * temp_c


def davies_log_gamma(z, ionic_strength, temp_c):
    if z == 0 or ionic_strength <= 0:
        return 0.0
    s = math.sqrt(ionic_strength)
    return -davies_A(temp_c) * z * z * (s / (1.0 + s) - 0.3 * ionic_strength)


def vant_hoff(logk25, dh_kcal, temp_c):
    """log K at temp_c from log K(25 C) and dH (kcal/mol)."""
    T = temp_c + 273.15
    return logk25 - (dh_kcal * 4184.0) / (2.302585 * 8.314462) * (1.0 / T - 1.0 / 298.15)


def mineral_logk(salt, temp_c):
    """log Ksp at temp_c: phreeqc.dat analytical expression when available, else van 't Hoff  [M8]."""
    d = MINERALS[salt]
    if d.get("analytic"):
        a1, a2, a3, a4 = d["analytic"]
        T = temp_c + 273.15
        return a1 + a2 * T + a3 / T + a4 * math.log10(T)
    return vant_hoff(d["logk"], d["dh"], temp_c)


def carbonate_constants(temp_c):
    """pK1 (Harned & Davis), pK2 (Harned & Scholes), pKs calcite (Plummer & Busenberg)."""
    T = temp_c + 273.15
    pK1 = (3404.71 / T) + 0.032786 * T - 14.8435
    pK2 = (2902.39 / T) - 6.498 + (0.02379 * T)
    pKs = 171.9065 + (0.077993 * T) - (2839.319 / T) - (71.595 * math.log10(T))
    return pK1, pK2, pKs


def co2_from_alkalinity(hco3_mgL, pH, temp_c):
    """Dissolved CO2 (mg/L) in equilibrium with HCO3 at pH  [M6]."""
    if hco3_mgL <= 0:
        return 0.0
    pK1 = carbonate_constants(temp_c)[0]
    return hco3_mgL / 61.02 * 10 ** (pK1 - pH) * 44.01


def silica_solubility_mgL(temp_c, pH):
    """Amorphous silica solubility, mg/L SiO2  [M4].
    Neutral-pH solubility: Fournier & Rowe (1977), log C = 4.52 - 731/T(K)  (~117 mg/L at 25 C).
    pH correction for silicic-acid ionisation: x (1 + 10^(pH - pKa)), pKa = 9.84."""
    T = temp_c + 273.15
    s0 = 10 ** (4.52 - 731.0 / T)
    return s0 * (1.0 + 10 ** (pH - 9.84))


def ion_balance(ions):
    cat = sum(ions.get(k, 0.0) / EQ_WT[k] for k in CATIONS)
    an = sum(ions.get(k, 0.0) / EQ_WT[k] for k in ANIONS)
    err = (cat - an) / (cat + an) * 100.0 if (cat + an) > 0 else 0.0
    return cat, an, err


def calc_tds(ions):
    """TDS (mg/L) as the sum of ions, excluding dissolved CO2 (a gas)  [M10]."""
    return sum(v for k, v in ions.items() if k not in GASES)


def balance_ions(ions):
    """Add Na or Cl to close the ion balance. Returns (new_ions, description)."""
    cat, an, _ = ion_balance(ions)
    out = dict(ions)
    if cat > an:
        add = (cat - an) * EQ_WT['Cl']
        out['Cl'] = out.get('Cl', 0.0) + add
        return out, f"Cl- increased by {add:.1f} mg/L"
    if an > cat:
        add = (an - cat) * EQ_WT['Na']
        out['Na'] = out.get('Na', 0.0) + add
        return out, f"Na+ increased by {add:.1f} mg/L"
    return out, "no adjustment needed"


def validate_inputs(ions, feed_flow, temp_c, recovery_pct, rejection_pct, feed_ph, treated_ph=None):
    """Explicit input validation  [B7][W4]. Returns a list of error strings (empty = OK)."""
    errors = []
    for k, v in ions.items():
        if v is None or v < 0:
            errors.append(f"{k} must be zero or positive (got {v}).")
    if feed_flow is None or feed_flow <= 0:
        errors.append("Feed flow must be greater than zero.")
    if not (5.0 <= temp_c <= 45.0):
        errors.append("Feed temperature must be between 5 and 45 C.")
    if not (10.0 <= recovery_pct <= 95.0):
        errors.append("Recovery must be between 10 and 95 %.")
    if not (90.0 <= rejection_pct < 100.0):
        errors.append("Salt rejection must be between 90 and 99.9 %.")
    if not (3.0 <= feed_ph <= 11.0):
        errors.append("Raw feed pH must be between 3 and 11.")
    if treated_ph is not None and not (3.0 <= treated_ph <= 11.0):
        errors.append("Adjusted feed pH must be between 3 and 11.")
    if calc_tds(ions) <= 0:
        errors.append("Water analysis is empty.")
    return errors


def calculate_acid_chemistry(raw_ph, target_ph, raw_hco3, temp_c, acid="H2SO4 (98%)"):
    """Acid needed to move from raw_ph to target_ph.
    Returns (target_hco3_mgL, anion_added_mgL, acid_dose_ppm_commercial)."""
    if target_ph >= raw_ph or raw_hco3 <= 0:
        return raw_hco3, 0.0, 0.0
    pK1 = carbonate_constants(temp_c)[0]
    total_co2 = raw_hco3 * (1.0 + 10 ** (pK1 - raw_ph))  # as mg/L HCO3 equivalent
    target_hco3 = total_co2 / (1.0 + 10 ** (pK1 - target_ph))
    destroyed = max(0.0, raw_hco3 - target_hco3)
    a = ACIDS[acid]
    eq_destroyed = destroyed / 61.02  # meq/L
    acid_dose = eq_destroyed / a["eq_per_mol"] * a["mw"] / a["strength"]
    anion_added = eq_destroyed / a["eq_per_mol"] * a["anion_mw"] if a["anion"] == "SO4" else eq_destroyed * a["anion_mw"]
    return target_hco3, anion_added, acid_dose


def apply_acid(feed_ions, feed_ph, target_ph, temp_c, acid):
    """Treated-feed analysis after acid. CO2 recalculated at the treated pH (includes acid CO2)."""
    adj_hco3, anion_add, dose = calculate_acid_chemistry(feed_ph, target_ph, feed_ions.get('HCO3', 0.0), temp_c, acid)
    out = dict(feed_ions)
    out['HCO3'] = adj_hco3
    an = ACIDS[acid]["anion"]
    out[an] = out.get(an, 0.0) + anion_add
    ph = min(target_ph, feed_ph)
    out['CO2'] = co2_from_alkalinity(adj_hco3, ph, temp_c)
    return out, dose


def stream_concentrations(feed_ions, recovery_pct, rejection_pct):
    """Exact mass balance with salt passage  [M7].
    Permeate = SP x average feed-side concentration (log-mean CF); concentrate by mass balance.
    Dissolved CO2 passes the membrane (permeate = concentrate = feed)."""
    Y = recovery_pct / 100.0
    sp = 1.0 - rejection_pct / 100.0
    cf_avg = math.log(1.0 / (1.0 - Y)) / Y
    perm, conc = {}, {}
    for k, v in feed_ions.items():
        if k in GASES:
            perm[k] = conc[k] = v
            continue
        perm[k] = sp * v * cf_avg
        conc[k] = (v - Y * perm[k]) / (1.0 - Y)
    cf = (1.0 - Y * sp * cf_avg) / (1.0 - Y)
    return perm, conc, cf


def speciate(ions, temp_c, max_iter=200):
    """Free-ion molarities with ion pairing and Davies activity coefficients  [M2].
    Returns dict(free, total, pairs, I, log_gamma(z))."""
    total = {}
    for ion, val in ions.items():
        p = ION_PROPS.get(ion)
        if p and p['z'] != 0 and val > 0:
            total[ion] = val / 1000.0 / p['mw']
    pairs = []
    for c, a, logk, dh, zp in ION_PAIRS:
        if c in total and a in total:
            pairs.append((c, a, 10 ** vant_hoff(logk, dh, temp_c), zp))
    free = dict(total)
    I = 0.5 * sum(m * ION_PROPS[i]['z'] ** 2 for i, m in total.items())
    pair_conc = {}
    for _ in range(max_iter):
        lg = {z: davies_log_gamma(z, I, temp_c) for z in (0, 1, 2, 3)}
        kc = [(c, a, K * 10 ** (lg[abs(ION_PROPS[c]['z'])] + lg[abs(ION_PROPS[a]['z'])] - lg[abs(zp)]), zp)
              for c, a, K, zp in pairs]
        new_free = {}
        for ion, tot in total.items():
            s = 1.0
            for c, a, k, _zp in kc:
                if ion == c:
                    s += k * free[a]
                elif ion == a:
                    s += k * free[c]
            new_free[ion] = tot / s
        delta = max((abs(new_free[i] - free[i]) / total[i] for i in total), default=0.0)
        free = {i: 0.5 * free[i] + 0.5 * new_free[i] for i in total}
        pair_conc = {(c, a): k * free[c] * free[a] for c, a, k, _zp in kc}
        zmap = {(c, a): zp for c, a, _k, zp in kc}
        I_new = 0.5 * (sum(m * ION_PROPS[i]['z'] ** 2 for i, m in free.items())
                       + sum(m * zmap[p] ** 2 for p, m in pair_conc.items()))
        if abs(I_new - I) < 1e-12 and delta < 1e-9:
            I = I_new
            break
        I = I_new
    return {"free": free, "total": total, "pairs": pair_conc, "I": I,
            "log_gamma": lambda z: davies_log_gamma(z, I, temp_c)}


def calculate_scaling_indices(pH, temp_c, ions):
    """Scaling indices for one stream.
    Keys kept from v1 where possible: LSI, SDSI, CaCO3, CaSO4, BaSO4, SrSO4, CaF2, Si(OH)4, SiO2
    (positive SI, 0 when undersaturated - used by the heuristic dose model), Ratio_* (IAP/Ksp),
    IAP_*, Ksp_*, Ionic_Strength. LSI/SDSI/CaCO3 are None when Ca or HCO3 is zero  [M3]."""
    sp = speciate(ions, temp_c)
    I = sp["I"]
    lg = sp["log_gamma"]
    tot, free = sp["total"], sp["free"]
    res = {"Ionic_Strength": round(I, 4), "Ionic_Strength_raw": I}

    # --- CaCO3: classic Langelier with total Ca and alkalinity  ---------------------------
    pK1, pK2, pKs = carbonate_constants(temp_c)
    m_ca, m_hco3 = tot.get('Ca', 0.0), tot.get('HCO3', 0.0)
    if m_ca > 0 and m_hco3 > 0:
        pHs = pK2 - pKs - math.log10(m_ca) - math.log10(m_hco3) - lg(2) - lg(1)
        lsi = pH - pHs
        sqI = math.sqrt(I)
        k_sd = pK2 - pKs + (2.5 * sqI / (1 + 1.5 * sqI))
        sdsi = pH - (-math.log10(m_ca) - math.log10(m_hco3) + k_sd)
        res.update({"LSI": round(lsi, 3), "SDSI": round(sdsi, 3), "CaCO3": round(lsi, 3)})
    else:
        res.update({"LSI": None, "SDSI": None, "CaCO3": None})

    # --- Sparingly soluble salts on free-ion activities  [M1][M2][M8] -----------------------
    for salt, d in MINERALS.items():
        ksp = 10 ** mineral_logk(salt, temp_c)
        mc, ma = free.get(d["cat"], 0.0), free.get(d["an"], 0.0)
        if mc > 0 and ma > 0:
            zc, za = abs(ION_PROPS[d["cat"]]['z']), abs(ION_PROPS[d["an"]]['z'])
            iap = (10 ** lg(zc) * mc) * (10 ** lg(za) * ma) ** d["n"]
        else:
            iap = 0.0
        ratio = iap / ksp
        res[f"IAP_{salt}"] = iap
        res[f"Ksp_{salt}"] = ksp
        res[f"Ratio_{salt}"] = ratio
        res[f"SIlog_{salt}"] = math.log10(ratio) if ratio > 0 else None
        res[salt] = round(math.log10(ratio), 3) if ratio > 1.0 else 0.0

    # --- Amorphous silica  [M4] ------------------------------------------------------------
    limit = silica_solubility_mgL(temp_c, pH)
    sio2 = ions.get('SiO2', 0.0)
    r_si = sio2 / limit if limit > 0 else 0.0
    res.update({"SiO2_limit_mgL": limit, "Ratio_SiOH4": r_si,
                "Si(OH)4": round(math.log10(r_si), 3) if r_si > 1.0 else 0.0})
    res["SiO2"] = res["Si(OH)4"]
    res["CO2_calc_mgL"] = co2_from_alkalinity(ions.get('HCO3', 0.0), pH, temp_c)
    res["_speciation"] = sp
    res["_pH"] = pH
    res["_temp"] = temp_c
    return res


def governing_index(conc_tds):
    """LSI for brackish concentrates, S&DSI above 10,000 mg/L  [M9]."""
    return "SDSI" if conc_tds > SDSI_TDS_THRESHOLD else "LSI"


def precipitable_mass_mgL(salt, ions, indices):
    """Worst-case mass (mg/L) that would precipitate to reach equilibrium  [M13].
    Sulfates/CaF2: solved on free ions with constant free fractions and activity coefficients.
    CaCO3: closed-system Langelier equilibrium (alkalinity consumed, CO2 released, pH falls).
    Silica: concentration above the corrected solubility."""
    sp = indices["_speciation"]
    lg = sp["log_gamma"]
    temp_c = indices["_temp"]
    if salt == "Si(OH)4":
        return max(0.0, ions.get('SiO2', 0.0) - indices["SiO2_limit_mgL"])
    if salt == "CaCO3":
        # Closed system: Ca2+ + 2 HCO3- -> CaCO3 + CO2 + H2O. Precipitation consumes alkalinity and
        # releases CO2, so the pH falls as it proceeds; solve for the amount x that brings LSI to 0.
        if indices.get("LSI") is None or indices["LSI"] <= 0:
            return 0.0
        pK1, pK2, pKs = carbonate_constants(temp_c)
        ca, hco3 = sp["total"]['Ca'], sp["total"]['HCO3']
        g1, g2 = lg(1), lg(2)
        co2 = hco3 * 10 ** (g1 + pK1 - indices["_pH"])          # CO2 consistent with the stream pH

        def lsi(x):
            c, h, d = ca - x, hco3 - 2 * x, co2 + x
            if c <= 0 or h <= 0:
                return -99.0
            ph = pK1 + math.log10(h) + g1 - math.log10(d)
            return ph - (pK2 - pKs - math.log10(c) - math.log10(h) - g2 - g1)
        lo, hi = 0.0, min(ca, hco3 / 2.0)
        for _ in range(100):
            mid = 0.5 * (lo + hi)
            if lsi(mid) > 0:
                lo = mid
            else:
                hi = mid
        return lo * 100.09 * 1000.0
    d = MINERALS[salt]
    if indices.get(f"Ratio_{salt}", 0.0) <= 1.0:
        return 0.0
    tc, ta = sp["total"].get(d["cat"], 0.0), sp["total"].get(d["an"], 0.0)
    fc, fa = sp["free"][d["cat"]] / tc, sp["free"][d["an"]] / ta
    zc, za = abs(ION_PROPS[d["cat"]]['z']), abs(ION_PROPS[d["an"]]['z'])
    ksp = indices[f"Ksp_{salt}"]
    gc, ga = 10 ** lg(zc), 10 ** lg(za)
    n = d["n"]

    def f(x):  # IAP(x) - Ksp, monotone decreasing in x
        return gc * fc * (tc - x) * (ga * fa * (ta - n * x)) ** n - ksp
    lo, hi = 0.0, min(tc, ta / n)
    for _ in range(100):
        mid = 0.5 * (lo + hi)
        if f(mid) > 0:
            lo = mid
        else:
            hi = mid
    return lo * d["fw"] * 1000.0


def precipitable_mass_table(conc_ions, indices, conc_flow_m3h):
    rows = []
    for salt in ["CaCO3", "CaSO4", "BaSO4", "SrSO4", "CaF2", "Si(OH)4"]:
        mgL = precipitable_mass_mgL(salt, conc_ions, indices)
        rows.append({"Salt": salt, "mg/L": mgL, "kg/day": mgL * conc_flow_m3h * 24.0 / 1000.0})
    return rows


def calculate_effective_scaling(raw_data, recipe, dose_ppm):
    """Heuristic 'effective SI' after antiscalant (unchanged model; needs calibration [M11]).
    Used only for the dose response - limits are enforced separately by PRODUCT_LIMITS."""
    effective = dict(raw_data)
    if not recipe:
        return effective
    is_pure_polymer = all(p in POLYMERS + ["smbs"] for p in recipe)
    secondary = sum(1 for s in ["Ratio_CaSO4", "Ratio_BaSO4", "Ratio_SrSO4", "Ratio_CaF2", "Ratio_SiOH4"]
                    if raw_data.get(s, 0) > 1.0)
    high_lsi = (raw_data.get('LSI') or 0) > 1.5
    penalty = 0.25 if (is_pure_polymer and high_lsi and secondary > 0) else 1.0
    for salt in _KF_SALTS:
        raw_si = effective.get(salt)
        if raw_si is None or raw_si <= 0:
            continue
        total_kd = sum(dose_ppm * pct * K_FACTORS.get(ing, {}).get(salt, 0.0) for ing, pct in recipe.items())
        total_kd *= penalty
        decay = math.exp(-total_kd / (raw_si ** 0.5))
        if is_pure_polymer and salt in ["LSI", "SDSI", "CaCO3"]:
            decay = max(decay, 0.40)
        effective[salt] = round(raw_si * decay, 3)
    return effective


def check_product_limits(product, conc_indices, gov_key, limits_table=None):
    """Compare concentrate values with the product maximum limits  [M11][M12].
    Returns list of (parameter, value, limit, ok)."""
    lim = (limits_table or PRODUCT_LIMITS).get(product, DEFAULT_PRODUCT_LIMITS)
    rows = []
    for key, ratio_key in LIMIT_KEYS:
        if key == "LSI":
            val = conc_indices.get(gov_key)
            if val is None:
                continue
            rows.append((gov_key, val, lim["LSI"], val <= lim["LSI"]))
        else:
            val = conc_indices.get(ratio_key, 0.0)
            rows.append((key, val, lim[key], val <= lim[key]))
    return rows


def run_expert_simulation(conc_indices, conc_ions, formulations, gov_key="LSI", prices=None, sg=None):
    """Find the lowest viable dose for each product.
    Returns (successes, rejections). successes are sorted by cost/m3 when every price is known,
    otherwise by required dose, then catalogue order  [M14]."""
    prices = prices or PRODUCT_PRICE_PER_KG
    successes, rejections = [], []
    gov_val = conc_indices.get(gov_key)
    names = [p for p in CATALOG_ORDER if p in formulations] + [p for p in formulations if p not in CATALOG_ORDER]
    caso4_mass = precipitable_mass_mgL("CaSO4", conc_ions, conc_indices)
    sio2_mass = precipitable_mass_mgL("Si(OH)4", conc_ions, conc_indices)
    doses = [MIN_DOSE_PPM + i * DOSE_STEP_PPM for i in range(int((MAX_DOSE_PPM - MIN_DOSE_PPM) / DOSE_STEP_PPM) + 1)]
    for prod in names:
        recipe = formulations[prod]
        failed = [f"{p} {v:.2f} > max {l:g}" for p, v, l, ok in check_product_limits(prod, conc_indices, gov_key) if not ok]
        if failed:
            rejections.append({"Product": prod, "Reason": "Exceeds product limits: " + "; ".join(failed)})
            continue
        has_polymer = any(p in recipe for p in POLYMERS)
        if not has_polymer and gov_val is not None and gov_val > 2.5:
            rejections.append({"Product": prod, "Reason": "Phosphonate-only product at index > 2.5 (calcium phosphonate risk)"})
            continue
        found = None
        for dose in doses:
            eff = calculate_effective_scaling(conc_indices, recipe, dose)
            if gov_val is not None and eff.get(gov_key, 0) > 0.5:          # upper limit only  [B1]
                continue
            ok = True
            for salt, raw_mass in (("CaSO4", caso4_mass), ("Si(OH)4", sio2_mass)):
                raw_si, eff_si = conc_indices.get(salt, 0), eff.get(salt, 0)
                if eff_si > 0.021:
                    residual = raw_mass * (eff_si / raw_si) if raw_si > 0 else 0.0
                    if residual > 0.5:
                        ok = False
                        break
            if ok:
                found = (dose, eff)
                break
        if found is None:
            rejections.append({"Product": prod, "Reason": f"No dose up to {MAX_DOSE_PPM:g} ppm meets the dose-response criteria"})
            continue
        dose, eff = found
        row = {"Product": prod, "Required Dose (ppm)": dose,
               f"Effective {gov_key} at dose": eff.get(gov_key) if gov_val is not None else None,
               "Cost (Rs/m3 feed)": None}
        price = prices.get(prod)
        if price:
            row["Cost (Rs/m3 feed)"] = round(dose / 1000.0 * price, 3)  # mg/L = g/m3
        successes.append(row)
    all_priced = successes and all(r["Cost (Rs/m3 feed)"] is not None for r in successes)
    order = {p: i for i, p in enumerate(names)}
    if all_priced:
        successes.sort(key=lambda r: (r["Cost (Rs/m3 feed)"], order[r["Product"]]))
    else:
        successes.sort(key=lambda r: (r["Required Dose (ppm)"], order[r["Product"]]))
    return successes, rejections


def optimise_acid(feed_ions, feed_ph, temp_c, recovery_pct, rejection_pct, acid, target_index):
    """Lowest acid dose that brings the governing concentrate index to <= target  [M15].
    Returns dict(treated_ph, dose, reachable, caso4_warning)."""
    def evaluate(ph):
        treated, dose = apply_acid(feed_ions, feed_ph, ph, temp_c, acid)
        _p, conc, cf = stream_concentrations(treated, recovery_pct, rejection_pct)
        idx = calculate_scaling_indices(ph + math.log10(cf), temp_c, conc)
        return treated, dose, idx, conc
    _t, _d, idx0, conc0 = evaluate(feed_ph)
    gov = governing_index(calc_tds(conc0))
    base_caso4 = idx0["Ratio_CaSO4"]
    if idx0.get(gov) is None or idx0[gov] <= target_index:
        return {"treated_ph": feed_ph, "dose": 0.0, "reachable": True, "caso4_warning": False}
    ph = feed_ph
    while ph > ACID_PH_FLOOR + 1e-9:
        ph = round(ph - 0.05, 2)
        _t, dose, idx, _c = evaluate(ph)
        if idx.get(gov) is not None and idx[gov] <= target_index:
            c = idx["Ratio_CaSO4"]
            warn = ACIDS[acid]["anion"] == "SO4" and c > 1.0 and c > 1.1 * base_caso4
            return {"treated_ph": ph, "dose": dose, "reachable": True, "caso4_warning": warn}
    return {"treated_ph": feed_ph, "dose": 0.0, "reachable": False, "caso4_warning": False}


def run_projection(raw_ions, feed_flow, temp_c, recovery_pct, rejection_pct, feed_ph, perm_ph,
                   auto_acid=True, manual_ph=None, acid="H2SO4 (98%)", target_index=2.5,
                   auto_balance=True, formulations=None):
    """Complete projection without any UI. Returns a dict with every intermediate result.
    This is the function a web backend (e.g. Chembond Flux) should call."""
    formulations = formulations or FORMULATIONS
    errors = validate_inputs(raw_ions, feed_flow, temp_c, recovery_pct, rejection_pct, feed_ph,
                             None if auto_acid else manual_ph)
    out = {"errors": errors, "warnings": [], "engine_version": ENGINE_VERSION}
    if errors:
        return out
    cat, an, err = ion_balance(raw_ions)
    out["balance_entered"] = {"cations_meq": cat, "anions_meq": an, "error_pct": err}
    ions = dict(raw_ions)
    out["balance_adjustment"] = "not applied"
    if auto_balance and abs(err) > 0.01:
        ions, out["balance_adjustment"] = balance_ions(ions)
    if abs(err) > 10.0:
        out["warnings"].append(f"Entered analysis is {err:.1f}% out of ion balance - check the lab analysis before relying on results.")
    co2_calc = co2_from_alkalinity(ions.get('HCO3', 0.0), feed_ph, temp_c)
    out["co2_entered"], out["co2_calc"] = ions.get('CO2', 0.0), co2_calc
    if abs(co2_calc - ions.get('CO2', 0.0)) > max(2.0, 0.5 * co2_calc):
        out["warnings"].append(f"Entered CO2 ({ions.get('CO2', 0.0):.1f} mg/L) is inconsistent with pH and alkalinity "
                               f"(calculated {co2_calc:.1f} mg/L); the calculated value is used.")
    ions['CO2'] = co2_calc
    out["feed_ions"] = ions

    if auto_acid:
        opt = optimise_acid(ions, feed_ph, temp_c, recovery_pct, rejection_pct, acid, target_index)
        treated_ph = opt["treated_ph"]
        if not opt["reachable"]:
            out["warnings"].append(f"Auto-acid could not reach the index target of {target_index} above pH {ACID_PH_FLOOR} - no acid applied. "
                                   "Consider lower recovery, softening, or manual pH.")
        if opt["caso4_warning"]:
            out["warnings"].append("H2SO4 dosing raises CaSO4 saturation in the concentrate above 1.0 - consider HCl.")
    else:
        treated_ph = manual_ph
        if manual_ph > feed_ph:  # [B10]
            out["warnings"].append("Adjusted pH is above the raw pH; caustic dosing is not modelled - raw pH used.")
            treated_ph = feed_ph
    treated, acid_dose = apply_acid(ions, feed_ph, treated_ph, temp_c, acid)
    out.update({"treated_ph": treated_ph, "acid_dose_ppm": acid_dose, "acid": acid, "treated_ions": treated})

    raw_perm, raw_conc, cf = stream_concentrations(ions, recovery_pct, rejection_pct)
    perm, conc, _cf = stream_concentrations(treated, recovery_pct, rejection_pct)
    raw_conc_ph = feed_ph + math.log10(cf)
    conc_ph = treated_ph + math.log10(cf)
    out.update({"cf": cf, "perm_ions": perm, "raw_conc_ions": raw_conc, "conc_ions": conc,
                "raw_conc_ph": raw_conc_ph, "conc_ph": conc_ph,
                "conc_flow_m3h": feed_flow * (1 - recovery_pct / 100.0)})
    out["idx_feed"] = calculate_scaling_indices(feed_ph, temp_c, ions)
    out["idx_treated"] = calculate_scaling_indices(treated_ph, temp_c, treated)
    out["idx_perm"] = calculate_scaling_indices(perm_ph, temp_c, perm)
    out["idx_raw_conc"] = calculate_scaling_indices(raw_conc_ph, temp_c, raw_conc)
    out["idx_conc"] = idx = calculate_scaling_indices(conc_ph, temp_c, conc)
    out["gov_key"] = gov = governing_index(calc_tds(conc))
    if idx["Ionic_Strength_raw"] > DAVIES_MAX_I:
        out["warnings"].append(f"Concentrate ionic strength {idx['Ionic_Strength_raw']:.2f} M exceeds the Davies model range "
                               f"({DAVIES_MAX_I} M); results are indicative only (use a Pitzer model for SWRO).")
    if conc_ph > 8.5 and conc.get('SiO2', 0) > 20 and (conc.get('Mg', 0) > 0 or conc.get('Fe', 0) > 0):  # [M5]
        out["warnings"].append("Concentrate pH > 8.5 with silica present: magnesium/iron silicate fouling is possible. "
                               "This is not quantified by the model.")
    out["feal_advisory"] = ions.get('Fe', 0) > 0.05 or ions.get('Al', 0) > 0.05  # feed basis  [M10]
    out["precip"] = precipitable_mass_table(conc, idx, out["conc_flow_m3h"])
    out["successes"], out["rejections"] = run_expert_simulation(idx, conc, formulations, gov)
    return out


def _join(items):
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def summary_text(proj, product, dose, recovery_pct):
    """Executive summary generated from the results  [B3]."""
    idx = proj["idx_conc"]
    gov = proj["gov_key"]
    risks = []
    if idx.get(gov) is not None and idx[gov] > 0:
        risks.append(f"calcium carbonate ({gov} {idx[gov]:.2f})")
    for salt in ["CaSO4", "BaSO4", "SrSO4", "CaF2"]:
        r = idx[f"Ratio_{salt}"]
        if r > 1.0:
            risks.append(f"{salt} ({r:.2f}x saturation)")
    if idx["Ratio_SiOH4"] > 1.0:
        risks.append(f"amorphous silica ({idx['Ratio_SiOH4']:.2f}x solubility)")
    acid_txt = (f" after {proj['acid']} dosing of {proj['acid_dose_ppm']:.1f} mg/L (treated pH {proj['treated_ph']:.2f})"
                if proj["acid_dose_ppm"] > 0 else "")
    if risks:
        risk_txt = ("Based on the supplied water analysis, the projected concentrate at "
                    f"{recovery_pct:.0f}% recovery{acid_txt} is supersaturated with respect to " + _join(risks) + ".")
    else:
        risk_txt = ("Based on the supplied water analysis, the projected concentrate at "
                    f"{recovery_pct:.0f}% recovery{acid_txt} is not supersaturated with respect to the salts evaluated; "
                    "scaling potential is low and antiscalant is recommended as a precaution.")
    return (risk_txt + f" The model projects that {product} at {dose:g} mg/L (product, on feed flow) keeps the "
            "concentrate within the product's limits. This is a projection based on the stated assumptions and "
            "should be confirmed by site monitoring (normalised permeate flow, differential pressure and salt passage).")


ASSUMPTIONS = [
    "Activity coefficients: Davies equation; ion pairing for the major sulfate, bicarbonate and fluoride complexes.",
    "Equilibrium constants: phreeqc.dat (25 C) with van 't Hoff temperature correction; carbonate constants per Harned/Plummer.",
    "Concentrate pH = treated feed pH + log10(CF), assuming full CO2 passage (ASTM D4582 approach).",
    "Mass balance includes salt passage; permeate from the log-mean feed-side concentration.",
    "Amorphous silica solubility: Fournier & Rowe (1977) with pH correction (pKa 9.84).",
    "Antiscalant dose response is a heuristic model; product maximum limits are PLACEHOLDERS pending Chembond test data.",
    "Precipitable mass is a worst-case equilibrium estimate; inhibition keeps salts in solution rather than removing them.",
    "Not valid for concentrate ionic strength above 0.5 M (seawater) without a Pitzer model.",
]
DISCLAIMER = ("This projection is based on the water analysis and operating data supplied and on the model assumptions "
              "listed. It is not a performance guarantee. Actual performance depends on feed variability, pre-treatment, "
              "operation and maintenance.")


# =====================================================================================
# 3. REPORT BUILDER (pure function)
# =====================================================================================

def _chart_png(kind, proj):
    """Render report charts in memory with matplotlib  [B6]. Returns PNG bytes or None."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return None
    fig, ax = plt.subplots(figsize=(7.0, 3.0), dpi=150)
    try:
        if kind == "si":
            idx = proj["idx_conc"]
            labels, vals = [], []
            gov = proj["gov_key"]
            if idx.get(gov) is not None:
                labels.append(gov); vals.append(idx[gov])
            for s in ["CaSO4", "BaSO4", "SrSO4", "CaF2"]:
                r = idx[f"Ratio_{s}"]
                labels.append(s); vals.append(math.log10(r) if r > 0 else -3)
            labels.append("SiO2"); vals.append(math.log10(idx["Ratio_SiOH4"]) if idx["Ratio_SiOH4"] > 0 else -3)
            ax.bar(labels, vals, color=["#D64545" if v > 0 else "#1F9D55" for v in vals])
            ax.axhline(0, color="#333", lw=1, ls="--")
            ax.set_ylabel("Saturation index (log10)")
            ax.set_title("Concentrate saturation (above 0 = supersaturated)")
        else:
            rows = proj["precip"]
            ax.bar([r["Salt"] for r in rows], [r["kg/day"] for r in rows], color="#E67E22")
            ax.set_ylabel("kg/day")
            ax.set_title("Worst-case precipitable mass without inhibition")
        ax.grid(axis="y", alpha=0.3)
        fig.tight_layout()
        buf = BytesIO()
        fig.savefig(buf, format="png")
        return buf.getvalue()
    finally:
        plt.close(fig)


def build_docx_report(proj, meta):
    """Word report  [B3][B4][B5][B6]. meta: product, dose, recovery_pct, rejection_pct, feed_flow,
    temp_c, membrane_type, feed_ph, site (optional). Returns bytes."""
    from docx import Document
    from docx.shared import Inches, Pt

    product, dose = meta["product"], meta["dose"]
    doc = Document()
    doc.styles["Normal"].font.size = Pt(10)
    h = doc.add_heading('CHEMBOND WATER TECHNOLOGIES', 0)
    h.alignment = 1
    doc.add_heading('RO SYSTEM PROJECTION & TREATMENT PROPOSAL', level=1).alignment = 1

    doc.add_heading('Executive Summary', level=2)
    doc.add_paragraph(summary_text(proj, product, dose, meta["recovery_pct"]))
    for w in proj["warnings"]:
        doc.add_paragraph("Note: " + w)
    if proj["feal_advisory"]:
        doc.add_paragraph("Pre-treatment advisory: feed iron or aluminium above 0.05 mg/L. Antiscalant and acid do not "
                          "dissolve oxidised metals; improve pre-treatment (filtration / coagulation).")

    doc.add_heading('Treatment Specification', level=2)
    sg = PRODUCT_SG.get(product, 1.15)
    prod_lph = dose * meta["feed_flow"] / 1000.0 / sg
    acid = ACIDS[proj["acid"]]
    acid_lph = proj["acid_dose_ppm"] * meta["feed_flow"] / 1000.0 / acid["sg"]
    spec = [
        ("Date generated", datetime.now(REPORT_TZ).strftime('%Y-%m-%d %H:%M') + " " + REPORT_TZ_LABEL),
        ("Engine version", ENGINE_VERSION),
        ("Site / analysis", meta.get("site") or "-"),
        ("Membrane type", meta["membrane_type"]),
        ("Feed flow / temperature", f"{meta['feed_flow']:g} m3/h / {meta['temp_c']:g} C"),
        ("Recovery / salt rejection", f"{meta['recovery_pct']:g} % / {meta['rejection_pct']:g} %"),
        ("Concentration factor", f"{proj['cf']:.2f}"),
        ("Raw / treated feed pH", f"{meta['feed_ph']:.2f} / {proj['treated_ph']:.2f}"),
        ("Acid", f"{proj['acid']}: {proj['acid_dose_ppm']:.2f} mg/L ({acid_lph:.3f} L/h)" if proj['acid_dose_ppm'] > 0 else "None"),
        ("Antiscalant", f"{product} at {dose:g} mg/L product on feed flow ({prod_lph:.3f} L/h at SG {sg})"),
        ("Governing CaCO3 index", proj["gov_key"]),
    ]
    t = doc.add_table(rows=0, cols=2)
    t.style = 'Table Grid'
    for k, v in spec:
        r = t.add_row().cells
        r[0].text, r[1].text = k, str(v)

    doc.add_heading('Concentrate Saturation vs Product Limits', level=2)
    t = doc.add_table(rows=1, cols=4)
    t.style = 'Table Grid'
    for i, hd in enumerate(["Parameter", "Concentrate value", "Product max (placeholder)", "Status"]):
        t.rows[0].cells[i].text = hd
    for p, v, l, ok in check_product_limits(product, proj["idx_conc"], proj["gov_key"]):
        r = t.add_row().cells
        r[0].text = p if p in ("LSI", "SDSI") else (p + " (ratio)")
        r[1].text, r[2].text, r[3].text = f"{v:.3f}", f"{l:g}", "Within limit" if ok else "EXCEEDS"
    doc.add_paragraph("Salt values are saturation ratios (ion activity product / solubility product); above 1.0 the "
                      "concentrate is supersaturated and relies on antiscalant inhibition. Product limits are placeholders "
                      "pending Chembond test data.")
    img = _chart_png("si", proj)
    if img:
        doc.add_picture(BytesIO(img), width=Inches(6.0))

    doc.add_heading('Worst-Case Precipitable Mass (without inhibition)', level=2)
    t = doc.add_table(rows=1, cols=3)
    t.style = 'Table Grid'
    for i, hd in enumerate(["Salt", "mg/L in concentrate", "kg/day"]):
        t.rows[0].cells[i].text = hd
    for row in proj["precip"]:
        r = t.add_row().cells
        r[0].text, r[1].text, r[2].text = row["Salt"], f"{row['mg/L']:.2f}", f"{row['kg/day']:.3f}"
    doc.add_paragraph("Mass that would deposit if the concentrate reached equilibrium. An effective antiscalant keeps "
                      "this material in solution (delays precipitation); it does not remove it.")
    img = _chart_png("mass", proj)
    if img:
        doc.add_picture(BytesIO(img), width=Inches(6.0))

    doc.add_heading('Annex A - Water Analysis (mg/L)', level=2)
    doc.add_paragraph(f"Entered ion balance error: {proj['balance_entered']['error_pct']:.2f}%. "
                      f"Adjustment applied: {proj['balance_adjustment']}. CO2 entered {proj['co2_entered']:.1f} mg/L, "
                      f"calculated {proj['co2_calc']:.1f} mg/L (calculated value used).")
    cols = ["Ion", "Entered", "Feed used", "Treated feed", "Permeate", "Concentrate"]
    t = doc.add_table(rows=1, cols=len(cols))
    t.style = 'Table Grid'
    for i, hd in enumerate(cols):
        t.rows[0].cells[i].text = hd
    entered = meta.get("entered_ions", {})
    for k in ION_PROPS:
        r = t.add_row().cells
        vals = [ION_LABELS[k], entered.get(k, proj["feed_ions"].get(k, 0)), proj["feed_ions"].get(k, 0),
                proj["treated_ions"].get(k, 0), proj["perm_ions"].get(k, 0), proj["conc_ions"].get(k, 0)]
        r[0].text = vals[0]
        for i, v in enumerate(vals[1:], start=1):
            r[i].text = f"{v:.2f}"

    doc.add_heading('Annex B - Assumptions', level=2)
    for a in ASSUMPTIONS:
        doc.add_paragraph(a, style='List Bullet')
    doc.add_heading('Disclaimer', level=2)
    doc.add_paragraph(DISCLAIMER)
    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()


# =====================================================================================
# 4. STREAMLIT UI
# =====================================================================================

def _fmt(v, nd=3):
    return "n/a" if v is None else f"{v:.{nd}f}"


class UtilityProjectionEngine:
    """Streamlit front end. Interface unchanged from v1: UtilityProjectionEngine().render_engine()."""

    def __init__(self):
        ss = st.session_state
        if 'ui_ions' not in ss:
            ss.ui_ions = dict(DEFAULT_IONS)
        # Widgets use stable keys so edits are not reset when other inputs change
        for k in ION_PROPS:
            ss.setdefault(f"ion_{k}", float(ss.ui_ions.get(k, 0.0)))
        if 'auto_balance' not in ss:
            ss.auto_balance = abs(ion_balance(ss.ui_ions)[2]) > 5.0
        ss.setdefault('final_product', None)
        ss.setdefault('final_dose', 0.0)
        self.formulations = dict(FORMULATIONS)
        if ss.get('custom_recipe'):
            self.formulations[CUSTOM_BLEND_NAME] = ss.custom_recipe

    # Kept for backward compatibility with v1 callers
    def calculate_scaling_indices(self, pH, temp_c, ions):
        return calculate_scaling_indices(pH, temp_c, ions)

    def calculate_acid_chemistry(self, raw_ph, target_ph, raw_hco3, temp_c, acid="H2SO4 (98%)"):
        return calculate_acid_chemistry(raw_ph, target_ph, raw_hco3, temp_c, acid)

    def calculate_effective_scaling(self, raw_data, product_name, dose_ppm):
        return calculate_effective_scaling(raw_data, self.formulations.get(product_name, {}), dose_ppm)

    @staticmethod
    def format_sci(val):
        if val == 0:
            return "0.00"
        base, exp = f"{val:.2e}".split('e')
        sup = str.maketrans("0123456789-", "⁰¹²³⁴⁵⁶⁷⁸⁹⁻")
        return f"{base} × 10{str(int(exp)).translate(sup)}"

    # ---------------------------------------------------------------- inputs
    def _inputs(self):
        ss = st.session_state
        st.subheader("System & Water Parameters")
        c1, c2, c3 = st.columns([1.2, 1, 1])
        with c1:
            st.write("**Operational Parameters**")
            feed_flow = st.number_input("Feed Flow Rate (m³/h)", min_value=0.1, value=10.0, step=1.0)
            temp_c = st.number_input("Feed Temperature (°C)", min_value=5.0, max_value=45.0, value=25.0)
            membrane = st.selectbox("Membrane Type Selection",
                                    ["Standard Brackish Water (BWRO)", "Fouling Resistant (FRRO)", "Seawater (SWRO)"])
            swro = "SWRO" in membrane
            rec_default, rec_text = (40, "35% - 45%") if swro else (75, "70% - 80%" if "FRRO" in membrane else "75% - 85%")
            st.caption(f"Recommended system recovery for {membrane}: **{rec_text}**")
            recovery = st.slider("System Recovery (%)", min_value=10, max_value=95, value=rec_default)
            rejection = st.slider("Membrane Salt Rejection (%)", min_value=90.0, max_value=99.9,
                                  value=99.7 if swro else 99.0, step=0.1)
            if swro:
                st.warning("Seawater concentrates exceed the Davies activity model range; S&DSI governs and results are indicative.")
            st.write("**Chemical Pre-Treatment (pH & Acid)**")
            feed_ph = st.number_input("Raw Feed pH", min_value=3.0, max_value=11.0, value=7.5)
            acid = st.selectbox("Acid", list(ACIDS.keys()))
            auto_acid = st.checkbox("Auto-optimise acid dose", value=True)
            target_index = 2.5
            manual_ph = None
            if auto_acid:
                target_index = st.number_input("Target concentrate LSI / S&DSI (before antiscalant)", min_value=0.0,
                                               max_value=2.8, value=2.5, step=0.1,
                                               help="Set at or below the limit of the antiscalant you intend to use.")
            else:
                manual_ph = st.number_input("Adjusted Feed pH (manual acid dosing)", min_value=3.0, max_value=11.0, value=feed_ph)
            perm_ph = st.number_input("Permeate pH (RO water)", min_value=3.0, max_value=11.0, value=6.0)
        with c2:
            st.write("**Cations (mg/L)**")
            for k, lbl in [('Ca', "Calcium (Ca++)"), ('Mg', "Magnesium (Mg++)"), ('Na', "Sodium (Na+)"),
                           ('K', "Potassium (K+)"), ('NH4', "Ammonium (NH4+)"), ('Ba', "Barium (Ba++)"),
                           ('Sr', "Strontium (Sr++)"), ('Fe', "Iron (as Fe2+)"), ('Al', "Aluminium (Al+++)")]:
                fmt = "%.4f" if k in ('Ba', 'Sr', 'Fe', 'Al') else "%.2f"
                ss.ui_ions[k] = st.number_input(lbl, min_value=0.0, format=fmt, key=f"ion_{k}")
        with c3:
            st.write("**Anions & Neutrals (mg/L)**")
            for k, lbl in [('HCO3', "Bicarbonate (HCO3-)"), ('Cl', "Chloride (Cl-)"), ('SO4', "Sulfate (SO4--)"),
                           ('F', "Fluoride (F-)"), ('NO3', "Nitrate (NO3-)"), ('PO4', "Phosphate (PO4---)"),
                           ('CO3', "Carbonate (CO3--)"), ('SiO2', "Silica (SiO2)"), ('CO2', "Carbon Dioxide (CO2)")]:
                fmt = "%.4f" if k == 'F' else "%.2f"
                ss.ui_ions[k] = st.number_input(lbl, min_value=0.0, format=fmt, key=f"ion_{k}")

        entered = dict(ss.ui_ions)
        cat, an, err = ion_balance(entered)
        st.write("---")
        st.subheader("Water Profile Verification (as entered)")
        v1, v2, v3, v4 = st.columns(4)
        v1.metric("Cations (meq/L)", round(cat, 3))
        v2.metric("Anions (meq/L)", round(an, 3))
        if abs(err) <= 5.0:
            v3.success(f"Entered balance error: {err:.2f}% (OK)")
        elif abs(err) <= 10.0:
            v3.warning(f"Entered balance error: {err:.2f}%")
        else:
            v3.error(f"Entered balance error: {err:.2f}% - check the lab analysis")
        v4.metric("TDS excl. CO2 (mg/L)", round(calc_tds(entered), 2))
        b1, b2 = st.columns(2)
        with b1:
            auto_balance = st.checkbox("Calculate Na/Cl to balance (used in the calculation)", key="auto_balance")
        with b2:
            target_tds = st.number_input("Override target TDS (mg/L)", min_value=0.0, value=float(round(calc_tds(entered), 2)))
            scale_tds = st.checkbox("Scale all ions proportionally to match target TDS", value=False)
        calc_ions = dict(entered)
        if scale_tds and target_tds > 0 and calc_tds(calc_ions) > 0:
            mult = target_tds / calc_tds(calc_ions)
            calc_ions = {k: (v if k in GASES else v * mult) for k, v in calc_ions.items()}
        def _apply(adj=calc_ions, bal=auto_balance):
            if bal:
                adj, _d = balance_ions(adj)
            ss.ui_ions = dict(adj)
            for key, val in adj.items():
                ss[f"ion_{key}"] = float(round(val, 4))
        st.button("Apply adjustments to input fields", on_click=_apply)
        return dict(feed_flow=feed_flow, temp_c=temp_c, membrane_type=membrane, recovery_pct=float(recovery),
                    rejection_pct=float(rejection), feed_ph=feed_ph, acid=acid, auto_acid=auto_acid,
                    target_index=target_index, manual_ph=manual_ph, perm_ph=perm_ph, auto_balance=auto_balance,
                    entered_ions=entered, calc_ions=calc_ions)

    # ---------------------------------------------------------------- main
    def render_engine(self):
        st.title("RO Projection Engine")
        st.caption(f"Engine version {ENGINE_VERSION}")
        tab_in, tab_thermo, tab_form, tab_rep = st.tabs([
            "1. System Configuration", "2. Thermodynamic Profiling", "3. Formulation Engine", "4. Engineering Projection Report"])
        with tab_in:
            inp = self._inputs()
        proj = run_projection(inp["calc_ions"], inp["feed_flow"], inp["temp_c"], inp["recovery_pct"], inp["rejection_pct"],
                              inp["feed_ph"], inp["perm_ph"], auto_acid=inp["auto_acid"], manual_ph=inp["manual_ph"],
                              acid=inp["acid"], target_index=inp["target_index"], auto_balance=inp["auto_balance"],
                              formulations=self.formulations)
        with tab_in:
            st.write("---")
            if proj["errors"]:
                for e in proj["errors"]:
                    st.error(e)
            else:
                if inp["auto_balance"]:
                    st.info(f"Ion balance adjustment used in the calculation: {proj['balance_adjustment']}.")
                if proj["acid_dose_ppm"] > 0:
                    st.success(f"Required acid dose ({inp['acid']}): {proj['acid_dose_ppm']:.2f} mg/L - treated feed pH {proj['treated_ph']:.2f}")
                for w in proj["warnings"]:
                    st.warning(w)
                m1, m2, m3 = st.columns(3)
                m1.metric("Concentration Factor (CF)", f"{proj['cf']:.2f}x")
                m2.metric("Salt passage", f"{100 - inp['rejection_pct']:.1f}%")
                m3.metric("Governing CaCO3 index", proj["gov_key"])
        if proj["errors"]:
            for t in (tab_thermo, tab_form, tab_rep):
                with t:
                    st.error("Correct the input errors in tab 1 to see results.")
            return
        with tab_thermo:
            self._thermo_tab(proj)
        with tab_form:
            self._formulation_tab(proj, inp)
        with tab_rep:
            self._report_tab(proj, inp)

    def _thermo_tab(self, proj):
        if proj["feal_advisory"]:
            st.warning("Pre-treatment advisory: feed iron or aluminium above 0.05 mg/L. Antiscalant and acid do not dissolve "
                       "oxidised metals; consider greensand/Birm or coagulation-assisted multimedia filtration upstream.")
        streams = [("Raw Feed", proj["feed_ions"], proj["idx_feed"]), ("Treated Feed", proj["treated_ions"], proj["idx_treated"]),
                   ("Permeate", proj["perm_ions"], proj["idx_perm"]), ("Raw Concentrate", proj["raw_conc_ions"], proj["idx_raw_conc"]),
                   ("Treated Concentrate", proj["conc_ions"], proj["idx_conc"])]
        st.write("**Ion concentrations (mg/L)** - Raw Feed is the analysis used in the calculation (after any balance adjustment)")
        st.dataframe(pd.DataFrame({"Ion": [ION_LABELS[k] for k in ION_PROPS],
                                   **{n: [f"{ions.get(k, 0):.2f}" for k in ION_PROPS] for n, ions, _i in streams}}),
                     use_container_width=True, hide_index=True)
        st.write("**Indices and ionic strength**")
        st.dataframe(pd.DataFrame({"Parameter": ["LSI", "S&DSI (approx.)", "Ionic strength (M)", "TDS excl. CO2 (mg/L)"],
                                   **{n: [_fmt(i["LSI"]), _fmt(i["SDSI"]), f"{i['Ionic_Strength']:.4f}", f"{calc_tds(ions):.1f}"]
                                      for n, ions, i in streams}}),
                     use_container_width=True, hide_index=True)
        if proj["idx_conc"]["LSI"] is None:
            st.info("LSI / S&DSI are not applicable because calcium or bicarbonate is zero; all other salts are still evaluated.")
        st.write("**Saturation ratios (IAP / Ksp; silica: concentration / solubility)** - above 1.0 = supersaturated")
        keys = [("CaSO4", "Ratio_CaSO4"), ("BaSO4", "Ratio_BaSO4"), ("SrSO4", "Ratio_SrSO4"), ("CaF2", "Ratio_CaF2"),
                ("Amorphous SiO2", "Ratio_SiOH4")]
        df = pd.DataFrame({"Salt": [k for k, _ in keys],
                           **{n: [f"{i[r]:.3f}" for _k, r in keys] for n, _ions, i in streams}})
        df["Status (treated conc.)"] = ["High" if proj["idx_conc"][r] > 1.0 else "Okay" for _k, r in keys]
        st.dataframe(df, use_container_width=True, hide_index=True)
        ic = proj["idx_conc"]
        st.caption(f"Treated concentrate: pH {proj['conc_ph']:.2f}, silica solubility {ic['SiO2_limit_mgL']:.0f} mg/L "
                   f"at {ic['_temp']:g} °C. Ksp values are thermodynamic (activity basis) at the feed temperature.")
        st.write("**Ion activity products vs Ksp - treated concentrate**")
        st.dataframe(pd.DataFrame({"Salt": list(MINERALS),
                                   "Mineral": [d["name"] for d in MINERALS.values()],
                                   "IAP": [self.format_sci(ic[f"IAP_{s}"]) for s in MINERALS],
                                   "Ksp (T)": [self.format_sci(ic[f"Ksp_{s}"]) for s in MINERALS]}),
                     use_container_width=True, hide_index=True)
        # Chart on a log (SI) scale so one salt cannot flatten the others  [W8]
        labels, vals = [], []
        gov = proj["gov_key"]
        if ic.get(gov) is not None:
            labels.append(gov); vals.append(ic[gov])
        for k, r in keys:
            labels.append(k); vals.append(math.log10(ic[r]) if ic[r] > 0 else None)
        dfc = pd.DataFrame({"Parameter": labels, "SI (log10)": vals}).dropna()
        fig = px.bar(dfc, x="Parameter", y="SI (log10)", title="Treated concentrate saturation index (log10; >0 = supersaturated)",
                     color=dfc["SI (log10)"] > 0, color_discrete_map={True: "#D64545", False: "#1F9D55"})
        fig.add_hline(y=0, line_dash="dash", line_color="#333")
        fig.update_layout(showlegend=False, yaxis_title="Saturation index (log10 IAP/Ksp; LSI)")
        st.plotly_chart(fig, use_container_width=True)

    def _formulation_tab(self, proj, inp):
        ss = st.session_state
        st.subheader("Project X: formulation efficiency grid (heuristic)")
        st.info("Theoretical inhibition efficiency of a single product against one salt at a time, from the heuristic "
                "k-factor model. Product limits and multi-salt effects are applied in the automated selection below.")
        a, b = st.columns(2)
        with a:
            px_product = st.selectbox("Select Kem Watreat formulation", list(self.formulations), key="px_prod")
        with b:
            px_dose = st.slider("Product dose (mg/L on feed flow)", MIN_DOSE_PPM, MAX_DOSE_PPM, 5.0, DOSE_STEP_PPM, key="px_dose")
        recipe = self.formulations[px_product]
        salts = ["LSI", "SDSI", "CaSO4", "BaSO4", "SrSO4", "CaF2", "Si(OH)4"]
        kd = {s: sum(px_dose * p * K_FACTORS.get(i, {}).get(s, 0.0) for i, p in recipe.items()) for s in salts}
        grid = [{"Raw SI": f"{0.5 + i * 0.1:.1f}", **{s: (1 - math.exp(-kd[s] / math.sqrt(0.5 + i * 0.1))) * 100 for s in salts}}
                for i in range(46)]
        dfg = pd.DataFrame(grid).set_index("Raw SI")
        st.dataframe(dfg.style.format("{:.1f}%"), use_container_width=True, height=400)

        st.markdown("---")
        st.subheader("Automated product selection")
        st.caption("Products are rejected when the concentrate exceeds the product's maximum limits "
                   "(PLACEHOLDER values pending Chembond data), then the lowest dose meeting the dose-response criteria is found.")
        succ, rej = proj["successes"], proj["rejections"]
        if succ:
            st.success(f"{len(succ)} viable product(s), ranked by required dose (or cost when prices are set).")
            st.dataframe(pd.DataFrame(succ), use_container_width=True, hide_index=True)
            c1, c2 = st.columns(2)
            with c1:
                sel = st.selectbox("Select commercial product", [r["Product"] for r in succ])
            with c2:
                d0 = next(r["Required Dose (ppm)"] for r in succ if r["Product"] == sel)
                dose = st.number_input("Finalise dose (mg/L on feed flow)", min_value=d0, max_value=MAX_DOSE_PPM,
                                       value=float(d0), step=0.1, help="Cannot be set below the required dose.")
            if st.button("Finalise and generate projection report"):
                ss.final_product, ss.final_dose = sel, dose
                st.success("Selection saved - open tab 4 for the report.")
        else:
            st.error(f"No catalogue product is viable for this water up to {MAX_DOSE_PPM:g} mg/L "
                     "(see reasons below). Options: lower recovery, more acid, softening, or a custom formulation.")
        if rej:
            with st.expander(f"Rejected products and reasons ({len(rej)})", expanded=not succ):
                st.dataframe(pd.DataFrame(rej), use_container_width=True, hide_index=True)
        if not succ:
            self._custom_blend()
        if ss.final_product and ss.final_dose > 0:
            sg = PRODUCT_SG.get(ss.final_product, 1.15)
            lph = ss.final_dose * inp["feed_flow"] / 1000.0 / sg
            st.info(f"**Required chemical flow:** {lph:.3f} L/h of {ss.final_product} (SG {sg})")

    def _custom_blend(self):
        """Custom synthesis, protected by a code stored in st.secrets  [S1][B8]."""
        ss = st.session_state
        st.write("### Custom formulation")
        try:
            secret = st.secrets.get("projection_admin_code")
        except Exception:
            secret = None
        if not secret:
            st.info("Custom synthesis is disabled: set `projection_admin_code` in .streamlit/secrets.toml to enable it.")
            return
        code = st.text_input("Admin code for custom synthesis", type="password")
        if not (code and hmac.compare_digest(code.encode(), str(secret).encode())):
            if code:
                st.error("Incorrect code.")
            return
        c1, c2, c3, c4 = st.columns(4)
        pct = {"terpolymer": c1.number_input("% Terpolymer", 0.0, 100.0, 20.0),
               "detmpa": c2.number_input("% DETMPA", 0.0, 100.0, 15.0),
               "pma": c3.number_input("% PMA", 0.0, 100.0, 10.0),
               "hedp": c4.number_input("% HEDP", 0.0, 100.0, 10.0)}
        dose = st.number_input("Custom blend dose (mg/L on feed flow)", MIN_DOSE_PPM, MAX_DOSE_PPM, 6.0, 0.5)
        total = sum(pct.values())
        st.caption(f"Total actives: {total:.1f}% (must be between 0 and 100%). Custom blends use the default placeholder limits.")
        b1, b2 = st.columns(2)
        if b1.button("Use custom blend"):
            if total <= 0 or total > 100:
                st.error("Active percentages must add up to more than 0% and no more than 100%.")
            else:
                ss.custom_recipe = {k: v / 100.0 for k, v in pct.items() if v > 0}
                ss.final_product, ss.final_dose = CUSTOM_BLEND_NAME, dose
                ss.flash = "Custom blend saved - open tab 4 for the report."
                st.rerun()
        if b2.button("Clear custom blend"):
            ss.pop("custom_recipe", None)
            if ss.final_product == CUSTOM_BLEND_NAME:
                ss.final_product, ss.final_dose = None, 0.0
            st.rerun()
        if ss.get("flash"):
            st.success(ss.pop("flash"))

    def _report_tab(self, proj, inp):
        ss = st.session_state
        st.markdown("<style>@media print { header, [data-testid='stHeader'], [data-baseweb='tab-list'] "
                    "{ display: none !important; } }</style>", unsafe_allow_html=True)
        st.subheader("Final Projection Report")
        prod, dose = ss.final_product, ss.final_dose
        if not prod or prod not in self.formulations:
            st.warning("Finalise a product selection in tab 3 to view the projection report.")
            return
        st.success(f"Report for: **{prod}** at **{dose:g} mg/L** (product, on feed flow)")
        st.write(summary_text(proj, prod, dose, inp["recovery_pct"]))
        rows = check_product_limits(prod, proj["idx_conc"], proj["gov_key"])
        st.dataframe(pd.DataFrame([{"Parameter": p, "Concentrate value": round(v, 3), "Product max (placeholder)": l,
                                    "Status": "Within limit" if ok else "EXCEEDS"} for p, v, l, ok in rows]),
                     use_container_width=True, hide_index=True)
        meta = dict(product=prod, dose=dose, recovery_pct=inp["recovery_pct"], rejection_pct=inp["rejection_pct"],
                    feed_flow=inp["feed_flow"], temp_c=inp["temp_c"], membrane_type=inp["membrane_type"],
                    feed_ph=inp["feed_ph"], entered_ions=inp["entered_ions"])
        try:
            st.download_button("Download Word report", data=build_docx_report(proj, meta),
                               file_name=f"Kem_Watreat_Projection_{datetime.now(REPORT_TZ):%Y%m%d_%H%M}.docx",
                               mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        except ImportError:
            st.error("python-docx is not installed - add it to requirements.txt to enable the Word report.")

        st.write("### Worst-case precipitable mass (without inhibition)")
        dfm = pd.DataFrame(proj["precip"])
        st.dataframe(dfm.style.format({"mg/L": "{:.2f}", "kg/day": "{:.3f}"}), use_container_width=True, hide_index=True)
        st.caption("Mass that would deposit if the concentrate reached equilibrium. An effective antiscalant keeps it in "
                   "solution (delays precipitation); it does not remove it.")

        st.write(f"### Dose response (heuristic): {prod}")
        recipe = self.formulations[prod]
        keys = [proj["gov_key"], "CaSO4", "BaSO4", "SrSO4", "CaF2", "Si(OH)4"]
        perf = []
        for i in range(0, int(MAX_DOSE_PPM / DOSE_STEP_PPM) + 1):
            d = i * DOSE_STEP_PPM
            eff = calculate_effective_scaling(proj["idx_conc"], recipe, d)
            perf.append({"Dose (ppm)": d, **{k: eff.get(k) for k in keys if eff.get(k) is not None}})
        dfp = pd.DataFrame(perf)
        fig = px.line(dfp, x="Dose (ppm)", y=[c for c in dfp.columns if c != "Dose (ppm)"])
        fig.add_hline(y=0, line_dash="dash", line_color="green", annotation_text="Saturation (SI = 0)")
        fig.add_vline(x=dose, line_dash="dash", line_color="red", annotation_text=f"Finalised dose ({dose:g} ppm)")
        fig.update_layout(yaxis_title="Effective SI (heuristic)", xaxis_title="Product dose (mg/L on feed flow)",
                          legend_title_text="Index / salt", height=500)
        st.plotly_chart(fig, use_container_width=True)
        with st.expander("Assumptions and disclaimer"):
            for a in ASSUMPTIONS:
                st.write("- " + a)
            st.caption(DISCLAIMER)


if __name__ == "__main__":
    if st is not None:
        st.set_page_config(layout="wide", page_title="RO Projection Engine")  # standalone only  [B9]
        UtilityProjectionEngine().render_engine()
