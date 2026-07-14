import streamlit as st
import datetime
import pandas as pd
import numpy as np
import io
import os
import time
import math
import joblib
import base64
import altair as alt
from io import BytesIO
from docx import Document
from docx.shared import RGBColor, Pt, Inches
from docx.enum.text import WD_ALIGN_PARAGRAPH

def standardize_dates(date_series):
    """Robust master parser for multi-format date registries.
    Intercepts any format (1-Apr-26, 2026-04-01, 01/04/2026, 01-04-2026) and aligns them.
    All ambiguous numeric formats (slash or hyphen) are treated as DAY-FIRST (DD-MM-YYYY),
    matching the plant's standard convention. Never falls back to pandas' default
    month-first interpretation, which was silently swapping day/month for any
    day-of-month <= 12 (e.g. 09-07-2026 read as 7 September instead of 9 July)."""
    parsed = pd.to_datetime(date_series, format='%d-%b-%y', errors='coerce')
    parsed = parsed.fillna(pd.to_datetime(date_series, format='%d-%b-%Y', errors='coerce'))
    parsed = parsed.fillna(pd.to_datetime(date_series, format='%Y-%m-%d', errors='coerce'))
    parsed = parsed.fillna(pd.to_datetime(date_series, format='%d/%m/%Y', errors='coerce'))
    parsed = parsed.fillna(pd.to_datetime(date_series, format='%d-%m-%Y', errors='coerce'))
    # Final catch-all: force dayfirst=True instead of pandas' default month-first
    # inference, so any leftover ambiguous numeric date is still read as DD-MM-YYYY.
    parsed = parsed.fillna(pd.to_datetime(date_series, errors='coerce', dayfirst=True))
    return parsed

def upsert_daily_logs(existing_df, new_df):
    """Merge new_df into existing_df by Date, updating ONLY the columns new_df actually provides for
    matching dates, and leaving every other existing column for that date untouched. New rows are
    appended for dates that don't exist yet.

    This replaces the old concat + drop_duplicates(keep='last') pattern, which does a destructive
    whole-row replace: uploading a narrower file (e.g. HTC-only data) for a date that already has
    operational data would silently wipe that operational data out, since the "new" row for that
    date wouldn't have those columns at all. With separate Operational / HTC / Water Quality bulk
    uploads now sharing the same master registry, that whole-row-replace behavior would destroy data
    on every single upload - this function is what makes running them independently safe.
    """
    new_df = new_df.copy()
    new_df['Date'] = standardize_dates(new_df['Date']).dt.strftime('%Y-%m-%d')
    new_df = new_df.dropna(subset=['Date']).drop_duplicates(subset=['Date'], keep='last').set_index('Date')

    if existing_df is None or existing_df.empty or 'Date' not in existing_df.columns:
        return new_df.reset_index()

    existing_df = existing_df.copy()
    existing_df['Date'] = standardize_dates(existing_df['Date']).dt.strftime('%Y-%m-%d')
    existing_df = existing_df.dropna(subset=['Date']).drop_duplicates(subset=['Date'], keep='last').set_index('Date')

    # Make sure both frames share the same columns before combining, so combine_first has a clean
    # NaN to fall back on rather than silently dropping a column one side doesn't have.
    for col in new_df.columns:
        if col not in existing_df.columns:
            existing_df[col] = np.nan
    for col in existing_df.columns:
        if col not in new_df.columns:
            new_df[col] = np.nan

    # new_df's non-null values always win (it's the fresh upload); anything new_df leaves null
    # (including entire columns it doesn't cover) falls back to whatever existing_df already had.
    merged = new_df.combine_first(existing_df)
    return merged.reset_index()

# MED GLOBAL CONSTANTS
MRA_COEF_2014 = {
    "model_type": "OLS",
    "Intercept": -161.5638, "Press_1st": 0.6136, "Temp_1st": 3.6392, 
    "SW_Upper": 0.8111, "Brine_Temp_1st": -7.6638, "Brine_Flow": -0.2329, 
    "LP_Steam": 8.2539, "Anti_PPM": -7.0301
}

MRA_BASELINE = {
    "Press_1st": 231.76, "Temp_1st": 68.47, "SW_Upper": 553.63, 
    "Brine_Temp_1st": 65.46, "Brine_Flow": 1275.50, "LP_Steam": 71.75, 
    "Anti_PPM": 4.82
}

BASE_EFFECTS = pd.DataFrame({
    "Effect ID": [f"Effect {i}" for i in range(1, 12)],
    "Base Vapor (°C)": np.round(np.linspace(69.0, 42.0, 11), 1),
    "Base Brine (°C)": np.round(np.linspace(66.3, 40.0, 11), 1),
    "Base HTC": np.round(np.linspace(2800.0, 1500.0, 11), 1) 
})

WATER_SPECS = {
    "Feed": {
        "pH": {"lim": (7.5, 9.2), "var": "f_ph", "db_col": "Feed_pH", "avg": 8.14},
        "Turbidity (NTU)": {"lim": (0.0, 5.0), "var": "f_turb", "db_col": "Feed_Turbidity", "avg": 3.2},
        "TSS (ppm)": {"lim": (0.0, 10.0), "var": "f_tss", "db_col": "Feed_TSS", "avg": 6.5},
        "TDS (ppm)": {"lim": (0.0, 42000.0), "var": "f_tds", "db_col": "Feed_TDS", "avg": 41000.0},
        "Total Alkalinity": {"lim": (160.0, 190.0), "var": "f_alk", "db_col": "Feed_Alkalinity", "avg": 170.0},
        "Calcium Hardness": {"lim": (950.0, 1100.0), "var": "f_ca", "db_col": "Feed_Calcium", "avg": 1040.0},
        "Mg Hardness": {"lim": (5400.0, 5700.0), "var": "f_mg", "db_col": "Feed_MgHardness", "avg": 5550.0},
        "Total Hardness": {"lim": (0.0, 7000.0), "var": "f_hard", "db_col": "Feed_TotalHardness", "avg": 6640.0},
        "Conductivity (μs/cm)": {"lim": (0.0, 70000.0), "var": "f_cond", "db_col": "Feed_Cond", "avg": 57000.0},
        "Silica": {"lim": (0.0, 0.67), "var": "f_sio2", "db_col": "Feed_Silica", "avg": 0.3},
        "Chlorides": {"lim": (21000.0, 22000.0), "var": "f_cl", "db_col": "Feed_Chlorides", "avg": 21500.0},
        "Sulphate": {"lim": (3050.0, 3250.0), "var": "f_so4", "db_col": "Feed_Sulphate", "avg": 3150.0},
        "Sulphide": {"lim": (0.0, 1.0), "var": "f_sulfide", "db_col": "Feed_Sulphide", "avg": 0.0}
    },
    "Product": {
        "pH": {"lim": (5.5, 7.0), "var": "p_ph", "db_col": "Product_pH", "avg": 6.5},
        "Turbidity (NTU)": {"lim": (0.0, 1.0), "var": "p_turb", "db_col": "Product_Turbidity", "avg": 0.1},
        "Conductivity (μs/cm)": {"lim": (0.0, 15.0), "var": "p_cond", "db_col": "Product_Cond", "avg": 4.6},
        "TDS (ppm)": {"lim": (0.0, 10.0), "var": "p_tds", "db_col": "Product_TDS", "avg": 2.5},
        "Total Alkalinity": {"lim": (0.0, 10.0), "var": "p_alk", "db_col": "Product_Alkalinity", "avg": 2.0},
        "Calcium Hardness": {"lim": (0.0, 1.0), "var": "p_ca", "db_col": "Product_Calcium", "avg": 0.0},
        "Mg Hardness": {"lim": (0.0, 1.0), "var": "p_mg", "db_col": "Product_MgHardness", "avg": 0.0},
        "Total Hardness": {"lim": (0.0, 0.1), "var": "p_hard", "db_col": "Product_TotalHardness", "avg": 0.0},
        "Total Iron": {"lim": (0.0, 0.1), "var": "p_iron", "db_col": "Product_Iron", "avg": 0.05},
        "Silica": {"lim": (0.0, 0.02), "var": "p_sio2", "db_col": "Product_Silica", "avg": 0.0},
        "Chlorides": {"lim": (0.0, 5.0), "var": "p_cl", "db_col": "Product_Chlorides", "avg": 0.0},
        "Sulphate": {"lim": (0.0, 1.0), "var": "p_so4", "db_col": "Product_Sulphate", "avg": 0.0}
    }
}

# Brine water analysis - right-hand block of the 'Feed & Brine Water Analysis' sheet.
# The sheet lists no specified limits for brine, so these are tracked/trended, not pass-fail graded.
BRINE_SPECS = {
    "pH": {"var": "b_ph", "db_col": "Brine_pH", "avg": 8.4},
    "Turbidity (NTU)": {"var": "b_turb", "db_col": "Brine_Turbidity", "avg": 14.0},
    "Conductivity (μs/cm)": {"var": "b_cond", "db_col": "Brine_Cond", "avg": 80500.0},
    "TDS (ppm)": {"var": "b_tds", "db_col": "Brine_TDS", "avg": 52325.0},
    "Total Alkalinity": {"var": "b_alk", "db_col": "Brine_Alkalinity", "avg": 218.0},
    "Calcium Hardness": {"var": "b_ca", "db_col": "Brine_Calcium", "avg": 1790.0},
    "Mg Hardness": {"var": "b_mg", "db_col": "Brine_MgHardness", "avg": 10710.0},
    "Total Hardness": {"var": "b_hard", "db_col": "Brine_TotalHardness", "avg": 12500.0},
    "Silica": {"var": "b_sio2", "db_col": "Brine_Silica", "avg": 0.0},
    "Chlorides": {"var": "b_cl", "db_col": "Brine_Chlorides", "avg": 31200.0},
}

EXACT_DB_COLUMNS = [
    "Date", "Sea Water Upper", "Sea Water Lower", "Sea Water Feed", "Sea Water Pressure",
    "Brine Water Return", "Desal production", "LP Steam consumption", "LP Steam Pressure",
    "Condensate Return", "condensate temp", "Condensate Conductivity",
    "1st Effect Vapour Temp", "1st effect brine temp", "11th Effect Brine Temp", "Feed Temp to Cold Group",
    "Intermediate Effects Avg Brine Temp", "Delta T", "1st effect vapour pressure", "Brine Discharge Temp", "Brine Discharge Pressure",
    "Sea Water cond I/L temp", "Sea Water Condenser O/L Temp", 
    "CW supply", "CW Return", "CW Flow", "Gross production", "GOR", "STEC", "Overall HTC", "1st Effect HTC", 
    "Residual", "Antiscalant (kg)", "Antifoam (kg)", "Anti_PPM", "Foam_PPM", "Area_1st", "Area_Overall", "Remarks",
    # --- Operational sheet extras ---
    "Steam Inlet Temp", "Recovery", "Conversion", "Steam Economy", "Overall Delta T",
    "Anti_PPM_Hot", "Anti_PPM_Brine",
    # --- 1st Effect HTC sheet: its OWN inputs. "Feed flow" here is flow to the 1st effect (~514 m3/hr)
    #     and "Feed Temp" here is the AVG BRINE TEMP OF EFFECTS 4,5,6,7 (~49C) - both are physically
    #     different measurements from the identically-named columns on the Overall-HTC sheet.
    "HTC1_Feed_Flow", "HTC1_Product_Flow", "HTC1_Cond_Flow", "HTC1_Steam_TPH",
    "HTC1_Feed_Temp_Eff4to7", "HTC1_Brine_Temp", "HTC1_Vapor_Temp", "HTC1_Cond_Temp",
    "HTC1_dT1", "HTC1_dT2", "HTC1_LMTD", "HTC1_Q_Steam", "HTC1_Fouling", "HTC1_Rf",
    # --- Overall HTC sheet: its OWN inputs. "Feed flow" here is TOTAL seawater feed (~2062 m3/hr)
    #     and "Feed Temp" here is the FEED TEMP TO COLD GROUP (~40C). Area is 11x12950x1.15.
    "HTCO_Feed_Flow", "HTCO_Product_Flow", "HTCO_Cond_Flow", "HTCO_Steam_TPH",
    "HTCO_Feed_Temp_ColdGrp", "HTCO_Brine_Disch_Temp", "HTCO_Vapor_Temp", "HTCO_Cond_Temp",
    "HTCO_dT1", "HTCO_dT2", "HTCO_LMTD", "HTCO_Q_Steam", "HTCO_Fouling", "HTCO_Rf",
]
for cat in ['Feed', 'Product']:
    for param, details in WATER_SPECS[cat].items(): 
        EXACT_DB_COLUMNS.append(details['db_col'])
for param, details in BRINE_SPECS.items():
    EXACT_DB_COLUMNS.append(details['db_col'])

RIL_EXCEL_HEADERS = [
    'Parameter', 'Sea water Upper', 'Sea water Lower', 'Sea water feed', 'Brine return', 
    ' Desal Production', 'LP Steam Consumption', 'Condensate return', 'Condensate Temp', 
    "1'st effect vapour Temp", '1st Effect Brine Temp', '(1st effect vapour-1st effect brine) Delta Temp', 
    '1st Effect Vapour pres', 'Steam Inlet Temp', 'Brine DischargeTemp', 'Sea water cond (FFC) I/L temp', 
    'Sea water cond (FFC) o/L temp', 'CW (FCC) supply', 'CW (FCC) return', 
    'Gross desal water production', 'Recovery', 'Conversion (Product to Feed)', 'Gain Output Ratio', 
    '11 effect brine Temp', 'Overall delta T(1st eff brine temp - 11th eff brine temp)', 
    'Steam Economy (Steam/Desal)', 'Antiscalant residual (Cold group)', 'Antiscalant residual (Hot group)', 
    'Antiscalant residual (Brine)', 'Feed Temp to Cold Group', 'Intermediate Effects Avg Brine Temp (4,5,6,7)', 'Remarks'
]

# --- HTC reference constants, read straight off rows 5 (Design) and 6 (SOR/Baseline) of the two HTC sheets.
# Rf (fouling resistance) = 1/U_actual - 1/U_clean, where the sheets use the SOR baseline as "clean".
HTC_1ST_AREA = 12950.013120000001      # 1st effect-HTC!K  = pi * 5.5m * 31244 tubes * 0.024m OD
HTC_OVERALL_AREA = 163818.0            # Overall-HTC!K     = 11 effects * 12950 * 1.15
HTC_1ST_U_SOR = 415.31060504252554     # 1st effect-HTC!AA6 (steam condensation basis, SOR baseline)
HTC_OVERALL_U_SOR = 17.726796070321715 # Overall-HTC!AA6   (steam condensation basis, SOR baseline)
CP_WATER_KJ_KGC = 4.186                # specific heat, both sheets col P

# --- 1st Effect HTC bulk template: mirrors the '1st effect-HTC' sheet's INPUT columns (A-K) exactly.
# Everything from dT1 onward (L..AC) is recomputed by the calculator, not read from the file.
HTC_1ST_BULK_HEADERS = [
    'Date', 'Feed flow', 'Product flow', 'Condensate Flow', 'Steam consumption rate',
    'Feed Temp', 'Brine Temp', '1st effect vapor temp', 'Condensate temperature', 'Heat Transfer Area'
]

# --- Overall HTC bulk template: mirrors the 'Overall-HTC' sheet's INPUT columns (A-K) exactly.
# NOTE: 'Feed flow', 'Feed Temp' and the brine column here are DIFFERENT physical measurements from the
# same-named columns on the 1st-effect sheet - which is exactly why these need to be two separate uploads.
HTC_OVERALL_BULK_HEADERS = [
    'Date', 'Feed flow', 'Product flow', 'Condensate Flow', 'Steam consumption rate',
    'Feed Temp', 'Brine discharge Temp', '1st effect vapor temp', 'Condensate temperature', 'Heat Transfer Area'
]

# --- Feed & Brine Water Analysis template: mirrors that sheet's columns A-X.
FEEDBRINE_BULK_HEADERS = [
    'Date', 'pH', 'Turbidity', 'TSS', 'Conductivity', 'TDS', 'Total Alkalinity', 'Calcium Hardness',
    'Mg Hardness', 'Total Hardness', 'Silica', 'Chloride', 'Sulphate', 'Sulphide',
    'Brine pH', 'Brine Turbidity', 'Brine Conductivity', 'Brine TDS', 'Brine Total Alkalinity',
    'Brine Calcium Hardness', 'Brine Mg Hardness', 'Brine Total Hardness', 'Brine Silica', 'Brine Chloride',
    'REMARKS'
]

# --- Desal (product) Analysis template: mirrors that sheet's columns A-N.
DESAL_BULK_HEADERS = [
    'Date', 'pH', 'Turbidity', 'Conductivity', 'TDS', 'Total Alkalinity', 'Calcium Hardness',
    'Mg Hardness', 'Total Hardness', 'Chloride', 'Total Iron', 'Silica', 'Sulphate', 'REMARKS'
]

# --- Operational Data bulk template: throughput/production/chemicals only. Matches your existing
# 'Operational data' sheet / DCS export format exactly, so your existing file works unmodified.
# Computes GOR, STEC and MRA Residual - never touches any HTC field.
OPERATIONAL_BULK_HEADERS = [
    'Parameter', 'Sea water Upper', 'Sea water Lower', 'Sea water feed', 'Brine return',
    ' Desal Production', 'LP Steam Consumption', 'Condensate return', 'Condensate Temp',
    "1'st effect vapour Temp", '1st Effect Brine Temp', '1st Effect Vapour pres',
    'Steam Inlet Temp', 'Brine DischargeTemp',
    'Sea water cond (FFC) I/L temp', 'Sea water cond (FFC) o/L temp',
    'CW (FCC) supply', 'CW (FCC) return', 'Gross desal water production', '11 effect brine Temp',
    'Antiscalant residual (Cold group)', 'Antiscalant residual (Hot group)', 'Antiscalant residual (Brine)',
    'Remarks'
]
# Derived on the sheet, deliberately NOT in the template: Delta Temp, Recovery, Conversion,
# Gain Output Ratio, Overall delta T, Steam Economy. The calculator recomputes all of these.

# --- HTC Data bulk template: mirrors your '1st effect-HTC' and 'Overall-HTC' calculation sheets exactly.
# Column names are unambiguous about which effect they belong to (unlike the source sheets, which reuse
# generic names like "Feed Temp" for two physically different measurements - that ambiguity is exactly
# what's been causing confusion). Computes Overall HTC and 1st Effect HTC via LMTD - never touches any
# operational/production field.
HTC_BULK_HEADERS = [
    'Date', 'LP Steam Consumption (TPH)', '1st Effect Vapour Temp (C)', 'Condensate Temp (C)',
    '1st Effect Brine Temp (C)', 'Intermediate Effects Avg Brine Temp 4-5-6-7 (C)',
    'Brine Discharge Temp (C)', 'Feed Temp to Cold Group (C)',
    '1st Effect Vapour Pressure (optional)', '11th Effect Brine Temp (optional)', 'Remarks'
]

DEFAULTS = {
    'steam': 71.75, 'stm_press': 4.3, 'desal': 800.0, 'gross': 801.4, 'sw_upper': 553.63, 'sw_total': 2100.0, 'sw_press': 1.7, 
    'brine_ret': 1275.5, 'brine_press': 1.3,
    'sw_in_t': 30.0, 'brine_out_t': 41.0, 'vap_out_t': 70.0, 'mra_press': 231.76, 'mra_t1': 68.47, 'mra_bt1': 65.46,
    'brine_11': 40.17, 'feed_cold': 40.0, 'mid_effects_temp': 49.14, 'htc1_feed_flow': 514.0,
    'steam_in_t': 172.34,
    'f_ph': 8.14, 'f_turb': 3.2, 'f_tss': 6.5, 'f_tds': 41000.0, 'f_alk': 170.0, 'f_ca': 1040.0, 'f_mg': 5550.0,
    'f_hard': 6640.0, 'f_cond': 57000.0, 'f_sio2': 0.3, 'f_cl': 21500.0, 'f_so4': 3150.0, 'f_sulfide': 0.0,
    'p_ph': 6.5, 'p_turb': 0.1, 'p_cond': 4.6, 'p_tds': 2.5, 'p_alk': 2.0, 'p_ca': 0.0, 'p_mg': 0.0,
    'p_hard': 0.0, 'p_iron': 0.05, 'p_sio2': 0.0, 'p_cl': 0.0, 'p_so4': 0.0,
    'b_ph': 8.4, 'b_turb': 14.0, 'b_cond': 80500.0, 'b_tds': 52325.0, 'b_alk': 218.0, 'b_ca': 1790.0,
    'b_mg': 10710.0, 'b_hard': 12500.0, 'b_sio2': 0.0, 'b_cl': 31200.0,
    'chem_anti_ppm': 4.82, 'chem_anti_cons': 13.5, 'chem_foam_ppm': 0.0, 'chem_foam_cons': 0.0,
    # Area_1st = pi * tube_length(5.5m) * tube_count(31244) * tube_OD(0.024m); Area_Overall = 11 effects * Area_1st * 1.15
    # (correction factor). Previous defaults (1757.49 / 19332.0) were roughly 7-8x too small, which alone made HTC
    # numbers wrong by close to an order of magnitude regardless of anything else. See tube-geometry calc sheet.
    'skip_eff': False, 'skip_wq': False, 'remarks': "", 'area_1st': HTC_1ST_AREA, 'area_overall': HTC_OVERALL_AREA,
    'sw_lower': 0.0, 'cond_flow': 0.0, 'cond_temp': 0.0, 'cond_cond': 3.0, 'sw_out_t': 0.0, 'cw_supply': 0.0, 'cw_return': 0.0, 'cw_flow': 2726.0
}

SYNC_MAP = {
    'steam': ['in_steam', 't5_steam'], 'stm_press': ['in_stm_press'], 'desal': ['in_desal'], 'gross': ['in_gross'],
    'sw_upper': ['in_sw_up', 't5_sw_up', 't2_sw_up'], 'sw_total': ['in_sw_tot', 't4_sw_tot', 't2_sw_tot'], 'sw_press': ['in_sw_press'],
    'brine_ret': ['in_brine', 't5_bflow'], 'brine_press': ['in_brine_press'], 
    'sw_in_t': ['in_sw_in', 't2_sw_in'], 'brine_out_t': ['in_brine_out', 't2_brine_out'], 
    'vap_out_t': ['in_vap_out', 't2_vap_out'], 'mra_press': ['in_press', 't5_press'], 
    'mra_t1': ['in_t1', 't5_t1', 't2_t1'], 'mra_bt1': ['in_bt1', 't5_bt1', 't2_bt1'], 
    'brine_11': ['in_brine_11'], 'feed_cold': ['in_feed_cold', 't2_feed_cold'],
    'mid_effects_temp': ['in_mid_effects_temp', 't2_mid_effects_temp'],
    'htc1_feed_flow': ['in_htc1_feed_flow', 't2_htc1_feed_flow'], 'steam_in_t': ['in_steam_in_t'],
    'f_ph': ['in_f_ph', 't3_f_ph'], 
    'f_turb': ['in_f_turb', 't3_f_turb'], 'f_tss': ['in_f_tss', 't3_f_tss'], 'f_tds': ['in_f_tds', 't3_f_tds'],
    'f_alk': ['in_f_alk', 't3_f_alk'], 'f_ca': ['in_f_ca', 't3_f_ca'], 'f_mg': ['in_f_mg', 't3_f_mg'],
    'f_hard': ['in_f_hard', 't3_f_hard'], 'f_cond': ['in_f_cond', 't3_f_cond'],
    'f_sio2': ['in_f_sio2', 't3_f_sio2'], 'f_cl': ['in_f_cl', 't3_f_cl'], 'f_so4': ['in_f_so4', 't3_f_so4'],
    'f_sulfide': ['in_f_sulfide', 't3_f_sulfide'],
    'p_ph': ['in_p_ph', 't3_p_ph'], 'p_turb': ['in_p_turb', 't3_p_turb'], 'p_cond': ['in_p_cond', 't3_p_cond'],
    'p_tds': ['in_p_tds', 't3_p_tds'], 'p_alk': ['in_p_alk', 't3_p_alk'], 'p_ca': ['in_p_ca', 't3_p_ca'],
    'p_mg': ['in_p_mg', 't3_p_mg'], 'p_hard': ['in_p_hard', 't3_p_hard'], 'p_iron': ['in_p_iron', 't3_p_iron'],
    'p_sio2': ['in_p_sio2', 't3_p_sio2'], 'p_cl': ['in_p_cl', 't3_p_cl'], 'p_so4': ['in_p_so4', 't3_p_so4'],
    'b_ph': ['in_b_ph'], 'b_turb': ['in_b_turb'], 'b_cond': ['in_b_cond'], 'b_tds': ['in_b_tds'],
    'b_alk': ['in_b_alk'], 'b_ca': ['in_b_ca'], 'b_mg': ['in_b_mg'], 'b_hard': ['in_b_hard'],
    'b_sio2': ['in_b_sio2'], 'b_cl': ['in_b_cl'],
    'chem_anti_ppm': ['in_anti_ppm', 't4_anti_ppm', 't5_anti'], 'chem_anti_cons': ['in_anti_cons', 't4_anti_cons'],
    'chem_foam_ppm': ['in_foam_ppm', 't4_foam_ppm'], 'chem_foam_cons': ['in_foam_cons', 't4_foam_cons'],
    'remarks': ['in_remarks', 't0_remarks'],
    'area_1st': ['in_area_1st', 't2_area_1st'], 'area_overall': ['in_area_overall', 't2_area_overall'],
    'sw_lower': ['in_sw_low'], 'cond_flow': ['in_cond_flow'], 'cond_temp': ['in_cond_temp'], 'cond_cond': ['in_cond_cond'],
    'sw_out_t': ['in_sw_out'], 'cw_supply': ['in_cw_supply'], 'cw_return': ['in_cw_return'], 'cw_flow': ['in_cw_flow']
}

LATENT_HEAT_STEAM_KJ_KG = 2330.0

def generate_daily_csv(date, ops, w_data, chem_data, mra, extra_tags):
    data_dict = {
        "Date": date.strftime('%d-%m-%Y'),
        "Sea Water Upper": ops['SW_Feed_1st'], "Sea Water Lower": extra_tags['sw_lower'],
        "Sea Water Feed": ops['SW Total'], "Sea Water Pressure": extra_tags['sw_press'], 
        "Brine Water Return": ops['Brine Return'], "Desal production": ops['Desal'], 
        "LP Steam consumption": ops['Steam'], "LP Steam Pressure": extra_tags['stm_press'],
        "Condensate Return": extra_tags['cond_flow'], "condensate temp": extra_tags['cond_temp'], "Condensate Conductivity": extra_tags['cond_cond'],
        "1st Effect Vapour Temp": ops['Stm In_1st'], "1st effect brine temp": ops['Brine_1st'],
        "11th Effect Brine Temp": extra_tags['brine_11'], "Feed Temp to Cold Group": extra_tags['feed_cold'],
        "Intermediate Effects Avg Brine Temp": extra_tags['mid_effects_temp'],
        "Delta T": ops['dt_1st'], "1st effect vapour pressure": ops['Press_1st'],
        "Brine Discharge Temp": ops['Brine Out_overall'], "Brine Discharge Pressure": extra_tags['brine_press'],
        "Sea Water cond I/L temp": ops['SW In_overall'], "Sea Water Condenser O/L Temp": extra_tags['sw_out_t'],
        "CW supply": extra_tags['cw_supply'], "CW Return": extra_tags['cw_return'], "CW Flow": extra_tags['cw_flow'],
        "Gross production": ops['Gross Prod'], "Recovery (%)": round(ops['Recovery'], 2),
        "GOR": round(ops['GOR'], 2), "STEC": round(ops['STEC'], 2), "Overall HTC": round(ops['htc_overall'], 2),
        "1st Effect HTC": round(ops['htc_1st'], 2), "Residual": round(mra['Residual'], 2),
        "Antiscalant Dosing (PPM)": chem_data['anti_ppm'], "Antiscalant (kg)": chem_data['anti_cons'],
        "Antifoam Dosing (PPM)": chem_data['foam_ppm'], "Antifoam (kg)": chem_data['foam_cons'],
        "Remarks": extra_tags['remarks']
    }
    for cat in ['Feed', 'Product']:
        for param, details in w_data[cat].items(): data_dict[details['db_col']] = details['val']
        
    df = pd.DataFrame([data_dict])
    return df.to_csv(index=False).encode('utf-8')

def generate_comprehensive_report(date, ops, sor_dfs, w_data, chem_data, mra, skip_wq, remarks):
    doc = Document()
    doc.add_heading('MED-4 Daily Operational & Performance Report', 0).alignment = WD_ALIGN_PARAGRAPH.CENTER
    p = doc.add_paragraph()
    p.add_run('Prepared by: ').bold = True
    p.add_run('Chembond Water Technologies Limited\n')
    p.add_run('Date: ').bold = True
    p.add_run(date.strftime('%d-%m-%Y'))
    p.alignment = WD_ALIGN_PARAGRAPH.RIGHT

    doc.add_heading('1. Executive Summary', level=1)
    doc.add_paragraph(f"On {date.strftime('%d-%m-%Y')}, the MED-4 unit achieved a Gross Production of {ops['Gross Prod']} m³/h and a Gain Output Ratio (GOR) of {ops['GOR']:.2f}:1. The Specific Thermal Energy Consumption (STEC) was {ops['STEC']:.2f} kWh/ton with a system recovery of {ops['Recovery']:.1f}%.")

    doc.add_heading('2. SOR Performance Matrix', level=1)
    for section_name, df in sor_dfs.items():
        doc.add_heading(section_name, level=2)
        t_ops = doc.add_table(rows=1, cols=6); t_ops.style = 'Table Grid'
        for i, h in enumerate(['Parameter', 'UOM', 'Design', 'SOR Base', 'Actual', 'Diff']): t_ops.rows[0].cells[i].text = h
        
        for index, row in df.iterrows():
            rc = t_ops.add_row().cells
            rc[0].text = str(row['Parameter'])
            rc[1].text = str(row['UOM'])
            rc[2].text = str(row['Design'])
            rc[3].text = str(row['SOR Base'])
            rc[4].text = str(row['Actual'])
            rc[5].text = str(row['Difference'])

    doc.add_heading('3. Thermal Integrity (HTC)', level=1)
    doc.add_paragraph(f"Overall Plant HTC: {ops['htc_overall']:.2f} W/m²K | 1st Effect HTC: {ops['htc_1st']:.2f} W/m²K")
    
    doc.add_heading('4. Water Quality', level=1)
    if skip_wq: doc.add_paragraph("NOTE: Laboratory water quality parameters were not recorded for this operational day.", style='BodyText')
    else:
        t_wq = doc.add_table(rows=1, cols=4); t_wq.style = 'Table Grid'
        for i, h in enumerate(['Parameter', 'Stream', 'Limit/Spec', 'Actual']): t_wq.rows[0].cells[i].text = h
        for param, data in w_data['Feed'].items():
            rc = t_wq.add_row().cells
            rc[0].text, rc[1].text, rc[2].text, rc[3].text = str(param), 'Sea Water Feed', f"{data['min']}-{data['max']}", str(data['val'])
        for param, data in w_data['Product'].items():
            rc = t_wq.add_row().cells
            rc[0].text, rc[1].text, rc[2].text, rc[3].text = str(param), 'Desal Product', f"{data['min']}-{data['max']}", str(data['val'])

    doc.add_heading('5. MRA Fouling Indicator', level=1)
    diff_pct = (mra['Residual'] / mra['Predicted']) * 100 if mra['Predicted'] > 0 else 0
    doc.add_paragraph(f"Actual Gross: {mra['Actual']:.1f} m³/h | MRA Predicted: {mra['Predicted']:.1f} m³/h | Difference: {diff_pct:.1f}%")
    if diff_pct <= -5.0: doc.add_paragraph(f"STATUS: FOULING DETECTED ({diff_pct:.1f}% loss). Please clean the machine.").runs[0].font.color.rgb = RGBColor(255, 0, 0)
    elif diff_pct <= -4.0: doc.add_paragraph(f"STATUS: WARNING ({diff_pct:.1f}% loss). Increase antiscalant dosing.").runs[0].font.color.rgb = RGBColor(255, 140, 0)
    else: doc.add_paragraph(f"STATUS: CLEAN ({diff_pct:.1f}% loss). System operating normally.").runs[0].font.color.rgb = RGBColor(0, 128, 0)
    
    if remarks and str(remarks).strip() != "":
        doc.add_heading('6. Remarks & Observations', level=1)
        doc.add_paragraph(str(remarks))

    bio = io.BytesIO()
    doc.save(bio)
    return bio.getvalue()

def generate_monthly_report(df_month, month_str, year_str):
    doc = Document()
    doc.add_heading(f'MED-4 Monthly Performance Summary: {month_str} {year_str}', 0).alignment = WD_ALIGN_PARAGRAPH.CENTER
    doc.add_heading('1. Monthly Aggregation', level=1)
    t_agg = doc.add_table(rows=1, cols=4); t_agg.style = 'Table Grid'
    for i, h in enumerate(['Metric', 'Minimum', 'Maximum', 'Average']): t_agg.rows[0].cells[i].text = h
    metrics = [("Gross production (m³/h)", df_month['Gross production']), ("Gain Output Ratio (GOR)", df_month['GOR']), ("Specific Thermal Energy Consumption (STEC, kWh/ton)", df_month.get('STEC', pd.Series(np.nan, index=df_month.index))), ("Overall HTC (W/m²K)", df_month['Overall HTC']), ("1st Effect HTC", df_month['1st Effect HTC'])]
    for name, series in metrics:
        rc = t_agg.add_row().cells
        rc[0].text, rc[1].text, rc[2].text, rc[3].text = name, f"{pd.to_numeric(series, errors='coerce').min():.2f}", f"{pd.to_numeric(series, errors='coerce').max():.2f}", f"{pd.to_numeric(series, errors='coerce').mean():.2f}"
    bio = io.BytesIO()
    doc.save(bio)
    return bio.getvalue()


def render_med_suite(db_conn, LOCAL_DB_FILE, LOCAL_CONFIG_FILE, AI_MODEL_FILE, save_database, save_config, render_chatbot, SKLEARN_INSTALLED, XGB_INSTALLED, PIL_INSTALLED):
    
    # MED Internal State Setup
    if 'vars' not in st.session_state: st.session_state.vars = DEFAULTS.copy()
    for k, v in DEFAULTS.items():
        if k not in st.session_state.vars: st.session_state.vars[k] = v

    def sync_var(var_name, source_key):
        st.session_state.vars[var_name] = st.session_state[source_key]
        for target_key in SYNC_MAP.get(var_name, []):
            if target_key != source_key: st.session_state[target_key] = st.session_state[source_key]

    def get_v(var_name): return st.session_state.vars[var_name]

    if 'sync_initialized' not in st.session_state:
        for var_name, keys in SYNC_MAP.items():
            for k in keys: 
                if k not in st.session_state: st.session_state[k] = st.session_state.vars[var_name]
        st.session_state.sync_initialized = True

    if 'shared_effect_df' not in st.session_state or 'Live Vapor (°C)' not in st.session_state.shared_effect_df.columns:
        st.session_state.shared_effect_df = pd.DataFrame({
            "Effect ID": [f"Effect {i}" for i in range(1, 12)], 
            "Live Vapor (°C)": [np.nan] * 11, 
            "Live Brine (°C)": [np.nan] * 11
        })

    med_unit_choice = st.sidebar.selectbox("Select Active Unit Train", [f"MED-{unit_idx}" for unit_idx in range(1, 12)], index=3)
    if med_unit_choice != "MED-4":
        st.title(f"{med_unit_choice} Diagnostic Interface")
        st.info(f"System data hooks for {med_unit_choice} are under configuration. Diagnostic dashboard metrics will become available upon plant startup.")
        render_chatbot()
        return
            
    st.sidebar.divider()
    # Default to the most recent date that actually has data, not today. Defaulting to today meant
    # that whenever the registry lagged the calendar (e.g. data ends 6 Jul, today is 14 Jul) the app
    # opened on a date with no record, reset every field to 0, and showed HTC/KPIs as 0 - even though
    # the registry and the trend charts were perfectly fine.
    _default_date = datetime.date.today()
    _logs0 = st.session_state.get('daily_logs')
    if _logs0 is not None and not _logs0.empty and 'Date' in _logs0.columns:
        _d = standardize_dates(_logs0['Date']).dropna()
        if not _d.empty:
            _default_date = _d.max().date()
    log_date = st.sidebar.date_input("Date", _default_date, format="DD/MM/YYYY")
    log_date_str = log_date.strftime('%Y-%m-%d')
    if _default_date != datetime.date.today() and log_date == _default_date:
        st.sidebar.caption(f"Showing latest record ({_default_date.strftime('%d-%m-%Y')}).")
    
    if 'last_selected_date' not in st.session_state: 
        st.session_state.last_selected_date = None

    if log_date_str != st.session_state.last_selected_date:
        st.session_state.last_selected_date = log_date_str
        date_found = False
        if not st.session_state.daily_logs.empty and 'Date' in st.session_state.daily_logs.columns:
            # CORE FIX: Standardize all registry dates right now, extract as safe strings
            db_dates_parsed = standardize_dates(st.session_state.daily_logs['Date'])
            db_dates = db_dates_parsed.dt.strftime('%Y-%m-%d').values
            
            if log_date_str in db_dates:
                date_found = True
                row_idx = np.where(db_dates == log_date_str)[0][-1]
                row = st.session_state.daily_logs.iloc[row_idx]
                
                db_to_var_mapping = {
                    'gross': ['Gross production'], 
                    'stm_press': ['LP Steam Pressure'],
                    'sw_press': ['Sea Water Pressure'],
                    'sw_upper': ['Sea Water Upper'], 'sw_lower': ['Sea Water Lower'],
                    'cond_cond': ['Condensate Conductivity'],
                    'sw_out_t': ['Sea Water Condenser O/L Temp'], 
                    'cw_supply': ['CW supply'], 'cw_return': ['CW Return'], 'cw_flow': ['CW Flow'],
                    'chem_anti_cons': ['Antiscalant (kg)'], 'chem_foam_cons': ['Antifoam (kg)'], 
                    'mra_press': ['1st effect vapour pressure'], 
                    'brine_11': ['11th Effect Brine Temp'],
                    'brine_ret': ['Brine Water Return'], 'brine_press': ['Brine Discharge Pressure'],
                    'chem_anti_ppm': ['Anti_PPM'], 'chem_foam_ppm': ['Foam_PPM'],
                    'sw_in_t': ['Sea Water cond I/L temp'], 
                    'vap_out_t': ['Vap_Out_Temp'], 
                    'remarks': ['Remarks'], 'area_1st': ['Area_1st'], 'area_overall': ['Area_Overall'],

                    # --- HTC-critical inputs: list EVERY column that can carry the value, in priority
                    # order, because the same physical reading is stored under different names depending
                    # on which uploader wrote it. Previously these pointed at a single Operational column:
                    #   - 'Feed Temp to Cold Group' is no longer written by ANY uploader (the Overall HTC
                    #     uploader writes HTCO_Feed_Temp_ColdGrp), so feed_cold never loaded.
                    #   - 'Brine Discharge Temp' is '-' (blank) for every row of the Operational sheet,
                    #     so brine_out_t never loaded either.
                    # Both silently stayed 0, which collapsed the Overall HTC driving forces to 0.
                    'feed_cold': ['HTCO_Feed_Temp_ColdGrp', 'Feed Temp to Cold Group'],
                    'brine_out_t': ['HTCO_Brine_Disch_Temp', 'Brine Discharge Temp'],
                    'cond_temp': ['condensate temp', 'HTC1_Cond_Temp', 'HTCO_Cond_Temp'],
                    'mra_t1': ['1st Effect Vapour Temp', 'HTC1_Vapor_Temp', 'HTCO_Vapor_Temp'],
                    'mra_bt1': ['1st effect brine temp', 'HTC1_Brine_Temp'],
                    'steam': ['LP Steam consumption', 'HTC1_Steam_TPH', 'HTCO_Steam_TPH'],
                    'sw_total': ['Sea Water Feed', 'HTCO_Feed_Flow'],
                    'desal': ['Desal production', 'HTC1_Product_Flow', 'HTCO_Product_Flow'],
                    'cond_flow': ['Condensate Return', 'HTC1_Cond_Flow', 'HTCO_Cond_Flow'],
                }
                
                for cat in ['Feed', 'Product']:
                    for param, d in WATER_SPECS[cat].items(): 
                        db_to_var_mapping[d['var']] = [d['db_col']]
                for param, d in BRINE_SPECS.items():
                    db_to_var_mapping[d['var']] = [d['db_col']]
                db_to_var_mapping['mid_effects_temp'] = ['HTC1_Feed_Temp_Eff4to7', 'Intermediate Effects Avg Brine Temp']
                db_to_var_mapping['htc1_feed_flow'] = ['HTC1_Feed_Flow']
                db_to_var_mapping['steam_in_t'] = ['Steam Inlet Temp']

                loaded_vars = False
                for var_key, col_names in db_to_var_mapping.items():
                    for col_name in col_names:
                        if col_name in row.index and pd.notna(row[col_name]):
                            try:
                                val_str = str(row[col_name]).strip()
                                if val_str and val_str.lower() not in ['nan', 'none', 'null', 'na']:
                                    if var_key == 'remarks': 
                                        val = val_str
                                    else: 
                                        val = float(val_str.replace(',', ''))
                                    # Heat transfer areas are fixed equipment geometry. An old row that
                                    # stored 0/blank must not overwrite the real constant, or every HTC
                                    # on this date silently reads 0.
                                    if var_key in ('area_1st', 'area_overall') and (not val or val <= 0):
                                        break
                                    st.session_state.vars[var_key] = val
                                    for tk in SYNC_MAP.get(var_key, []): 
                                        st.session_state[tk] = val
                                    loaded_vars = True
                                break
                            except: 
                                pass 
                if loaded_vars: 
                    st.sidebar.success(f"Auto-loaded historical data for {log_date.strftime('%d-%m-%Y')}")
                    st.rerun() 

        if not date_found:
            # No record exists for this date at all - reset every MEASURED field to 0 rather than leaving
            # whatever values were on screen from the last date viewed. Showing stale/default numbers
            # for a day with no actual log entry makes it look like real data exists when it doesn't.
            # Plant CONSTANTS are excluded: the heat transfer areas are fixed equipment geometry, not
            # daily readings, and zeroing them would force HTC to 0 even after valid data is entered.
            PLANT_CONSTANTS = ('area_1st', 'area_overall')
            for var_key, default_val in DEFAULTS.items():
                if var_key in PLANT_CONSTANTS:
                    continue
                zero_val = 0.0 if isinstance(default_val, (int, float)) and not isinstance(default_val, bool) else default_val
                if var_key in ('remarks',): zero_val = ""
                if var_key in ('skip_eff', 'skip_wq'): zero_val = False
                st.session_state.vars[var_key] = zero_val
                for tk in SYNC_MAP.get(var_key, []):
                    st.session_state[tk] = zero_val
            st.sidebar.info(f"No record found for {log_date.strftime('%d-%m-%Y')} - measured fields reset to 0.")
            st.rerun()

    # Display MED-4 Title
    st.title("MED-4 Management Suite")

    tabs = st.tabs([
        "0. Inputs", "1. SOR KPIs", "2. HTC", "3. Quality", 
        "4. Chemicals", "5. MRA", "6. Reporting", 
        "7. AI Model Select", "8. Bulk Uploads"
    ])

    ops_data = {
        'Steam': get_v('steam'), 
        'Desal': get_v('desal'), 
        'Gross Prod': get_v('gross'), 
        'SW_Feed_1st': get_v('sw_upper'), 
        'SW Total': get_v('sw_total'), 
        'Brine Return': get_v('brine_ret'),
        'SW In_overall': get_v('sw_in_t'), 
        'Brine Out_overall': get_v('brine_out_t'), 
        'Stm In_1st': get_v('mra_t1'), 
        'Brine_1st': get_v('mra_bt1'), 
        'Press_1st': get_v('mra_press')
    }
    
    ops_data['GOR'] = ops_data['Gross Prod'] / ops_data['Steam'] if ops_data['Steam'] > 0 else 0
    ops_data['STEC'] = (((ops_data['Steam'] * 1000) / 3600) * LATENT_HEAT_STEAM_KJ_KG) / ops_data['Desal'] if ops_data['Desal'] > 0 else 0
    ops_data['Recovery'] = (ops_data['Gross Prod'] / ops_data['SW Total']) * 100 if ops_data['SW Total'] > 0 else 0
    ops_data['Conversion'] = ops_data['Desal'] / ops_data['SW Total'] if ops_data['SW Total'] > 0 else 0
    ops_data['Economy'] = ops_data['Steam'] / ops_data['Desal'] if ops_data['Desal'] > 0 else 0

    display_effect_df = pd.merge(BASE_EFFECTS, st.session_state.shared_effect_df, on="Effect ID")
    for col in ["Base Vapor (°C)", "Live Vapor (°C)", "Base Brine (°C)", "Live Brine (°C)", "Base HTC"]:
        if col not in display_effect_df.columns:
            display_effect_df[col] = np.nan
            
    display_effect_df = display_effect_df[["Effect ID", "Base Vapor (°C)", "Live Vapor (°C)", "Base Brine (°C)", "Live Brine (°C)", "Base HTC"]]

    # ---- HEAT DUTY (steam condensation basis) --------------------------------------------------
    # Mirrors cols V/W/X of BOTH HTC sheets:
    #   ms (kg/hr) = Steam(TPH) x 1000
    #   W  (kJ/hr) = ms x latent heat
    #   Q  (W)     = W x 1000 / 3600
    ops_data['q_1st'] = (ops_data['Steam'] * 1000 * LATENT_HEAT_STEAM_KJ_KG * 1000) / 3600
    ops_data['q_overall'] = ops_data['q_1st']

    def _lmtd_scalar(dt1, dt2):
        """LMTD = (dT1 - dT2) / ln(dT1/dT2), col N of both sheets. Returns 0 when either driving
        force is missing or non-positive, so the HTC downstream honestly reports 0 rather than a
        fabricated number. Note dT2 > dT1 in this plant's data - the formula handles that fine."""
        try:
            if dt1 is None or dt2 is None or pd.isna(dt1) or pd.isna(dt2):
                return 0.0
            if dt1 <= 0 or dt2 <= 0:
                return 0.0
            if dt1 == dt2:
                return float(dt1)
            return (dt1 - dt2) / math.log(dt1 / dt2)
        except Exception:
            return 0.0

    # ---- 1st EFFECT HTC  (sheet: '1st effect-HTC') ----------------------------------------------
    # dT1 = 1st effect vapour temp - 1st effect brine temp        (col L)
    # dT2 = condensate temp - AVG BRINE TEMP OF EFFECTS 4,5,6,7   (col M)
    #       NB: the sheet labels that column "Feed Temp", but its tag row reads "Avg of effects of
    #       7,6,5,4". It is NOT a seawater temperature.
    ops_data['dt_1st'] = get_v('mra_t1') - get_v('mra_bt1')
    ops_data['dt2_1st'] = get_v('cond_temp') - get_v('mid_effects_temp')
    ops_data['lmtd_1st'] = _lmtd_scalar(ops_data['dt_1st'], ops_data['dt2_1st'])
    _a1 = get_v('area_1st')
    ops_data['htc_1st'] = (
        ops_data['q_1st'] / (_a1 * ops_data['lmtd_1st'])
        if ops_data['lmtd_1st'] > 0 and _a1 > 0 else 0
    )
    ops_data['fouling_1st'] = 1 / ops_data['htc_1st'] if ops_data['htc_1st'] > 0 else 0
    # Rf = 1/U_actual - 1/U_SOR_baseline   (col AC)
    ops_data['rf_1st'] = (
        (1 / ops_data['htc_1st']) - (1 / HTC_1ST_U_SOR) if ops_data['htc_1st'] > 0 else 0
    )

    # ---- OVERALL HTC  (sheet: 'Overall-HTC') ----------------------------------------------------
    # dT1 = 1st effect vapour temp - brine DISCHARGE temp   (col L)
    # dT2 = condensate temp - FEED TEMP TO COLD GROUP       (col M)
    #       NB: this sheet ALSO labels its column "Feed Temp", but here it means the cold-group feed
    #       temp (~40 C) - a different measurement from the 1st-effect sheet's "Feed Temp" (~49 C).
    ops_data['dt1_overall'] = get_v('mra_t1') - get_v('brine_out_t')
    ops_data['dt2_overall'] = get_v('cond_temp') - get_v('feed_cold')
    ops_data['lmtd_overall'] = _lmtd_scalar(ops_data['dt1_overall'], ops_data['dt2_overall'])
    _ao = get_v('area_overall')
    ops_data['htc_overall'] = (
        ops_data['q_overall'] / (_ao * ops_data['lmtd_overall'])
        if ops_data['lmtd_overall'] > 0 and _ao > 0 else 0
    )
    ops_data['fouling_overall'] = 1 / ops_data['htc_overall'] if ops_data['htc_overall'] > 0 else 0
    ops_data['rf_overall'] = (
        (1 / ops_data['htc_overall']) - (1 / HTC_OVERALL_U_SOR) if ops_data['htc_overall'] > 0 else 0
    )

    # Simple (non-LMTD) cascade delta, shown for reference only.
    ops_data['dt_overall_simple'] = get_v('mra_t1') - get_v('brine_11')

    mra_data = {}
    coefs = st.session_state.mra_coef 
    model_type = coefs.get("model_type", "OLS")
    
    live_input_arr = [get_v('mra_press'), get_v('mra_t1'), get_v('sw_upper'), get_v('mra_bt1'), get_v('brine_ret'), get_v('steam'), get_v('chem_anti_ppm')]
    
    if model_type == "OLS":
        mra_data['Predicted'] = (
            coefs["Intercept"] + 
            (coefs["Press_1st"] * live_input_arr[0]) + 
            (coefs["Temp_1st"] * live_input_arr[1]) + 
            (coefs["SW_Upper"] * live_input_arr[2]) + 
            (coefs["Brine_Temp_1st"] * live_input_arr[3]) + 
            (coefs["Brine_Flow"] * live_input_arr[4]) + 
            (coefs["LP_Steam"] * live_input_arr[5]) + 
            (coefs.get("Anti_PPM", MRA_COEF_2014["Anti_PPM"]) * live_input_arr[6])
        )
    else:
        try:
            active_model = joblib.load(AI_MODEL_FILE)
            live_df = pd.DataFrame([live_input_arr], columns=["Press_1st", "Temp_1st", "SW_Upper", "Brine_Temp_1st", "Brine_Flow", "LP_Steam", "Anti_PPM"])
            mra_data['Predicted'] = float(active_model.predict(live_df)[0])
        except: 
            mra_data['Predicted'] = 0.0
            
    mra_data['Actual'] = ops_data['Gross Prod']
    mra_data['Residual'] = mra_data['Actual'] - mra_data['Predicted']

    var_data = []
    param_keys = ["Press_1st", "Temp_1st", "SW_Upper", "Brine_Temp_1st", "Brine_Flow", "LP_Steam", "Anti_PPM"]
    param_names = ["1st effect vapour pressure", "1st Effect Vapour Temp", "Sea Water Upper", "1st effect brine temp", "Brine Water Return", "LP Steam consumption", "Antiscalant PPM"]
    
    for i in range(7):
        dev = live_input_arr[i] - MRA_BASELINE[param_keys[i]]
        weight = coefs.get(param_keys[i], 0.0) 
        if model_type == "OLS": 
            impact = dev * weight
        else: 
            impact = np.nan 
        var_data.append([param_names[i], MRA_BASELINE[param_keys[i]], live_input_arr[i], dev, weight, impact])
        
    mra_data['Variance_DF'] = pd.DataFrame(var_data, columns=["Parameter", "Baseline", "Live Input", "Deviation", "Regression Weight", "Impact (TPH)"])

    water_data = {'Feed': {}, 'Product': {}}
    for cat in ['Feed', 'Product']:
        for param, details in WATER_SPECS[cat].items():
            val = get_v(details['var'])
            status = "Pass" if details['lim'][0] <= val <= details['lim'][1] else "Fail"
            water_data[cat][param] = {'min': details['lim'][0], 'max': details['lim'][1], 'val': val, 'status': status, 'db_col': details['db_col']}
            
    chem_data = {
        'anti_ppm': get_v('chem_anti_ppm'), 
        'anti_cons': get_v('chem_anti_cons'), 
        'foam_ppm': get_v('chem_foam_ppm'), 
        'foam_cons': get_v('chem_foam_cons')
    }

    # --- TAB 0: INPUTS & PFD ---
    with tabs[0]:
        tab0_subtabs = st.tabs(["Data Entry", "Live PFD Monitor"])

        with tab0_subtabs[0]:
            st.subheader("Daily Data Entry")
            st.caption(
                "Five sections, one per source sheet in the plant workbook. Shared readings entered under "
                "**Operational** flow straight into the HTC sections - you only re-enter what's genuinely "
                "specific to each HTC calculation."
            )
            if mra_data['Predicted'] > 950:
                st.warning("MRA Prediction is unusually high (>950 m³/h). Check that 'Sea Water Feed' (~2100) wasn't entered into 'Sea Water Upper' (~550).")

            entry = st.tabs([
                "1 · Operational",
                "2 · HTC — 1st Effect",
                "3 · HTC — Overall",
                "4 · Feed & Brine",
                "5 · Desal Product",
            ])

            # ---------------------------------------------------------------- 1 · OPERATIONAL
            with entry[0]:
                st.caption("Source sheet: **Operational data**. Everything the plant logs daily from the DCS.")

                st.markdown("**Flows** — m³/h (steam in TPH)")
                f1, f2, f3, f4 = st.columns(4)
                with f1:
                    st.number_input("Sea Water Upper", key="in_sw_up", on_change=sync_var, args=('sw_upper', 'in_sw_up'))
                    st.number_input("Sea Water Lower", key="in_sw_low", on_change=sync_var, args=('sw_lower', 'in_sw_low'))
                with f2:
                    st.number_input("Sea Water Feed (total)", key="in_sw_tot", on_change=sync_var, args=('sw_total', 'in_sw_tot'))
                    st.number_input("Brine Water Return", key="in_brine", on_change=sync_var, args=('brine_ret', 'in_brine'))
                with f3:
                    st.number_input("Desal Production (net)", key="in_desal", on_change=sync_var, args=('desal', 'in_desal'))
                    st.number_input("Gross Production", key="in_gross", on_change=sync_var, args=('gross', 'in_gross'))
                with f4:
                    st.number_input("LP Steam Consumption (TPH)", key="in_steam", on_change=sync_var, args=('steam', 'in_steam'))
                    st.number_input("Condensate Return", key="in_cond_flow", on_change=sync_var, args=('cond_flow', 'in_cond_flow'))

                st.divider()
                st.markdown("**Temperatures** — °C")
                t1, t2, t3, t4 = st.columns(4)
                with t1:
                    st.number_input("1st Effect Vapour Temp", key="in_t1", on_change=sync_var, args=('mra_t1', 'in_t1'),
                                    help="Tag Z711TIT414. Feeds BOTH HTC calculations as the hot-side source temp.")
                    st.number_input("1st Effect Brine Temp", key="in_bt1", on_change=sync_var, args=('mra_bt1', 'in_bt1'),
                                    help="Tag Z711TIT401. Hot-side sink for the 1st Effect HTC.")
                with t2:
                    st.number_input("Condensate Temp", key="in_cond_temp", on_change=sync_var, args=('cond_temp', 'in_cond_temp'),
                                    help="Tag Z711TIT415. Cold-side source for BOTH HTC calculations.")
                    st.number_input("Brine Discharge Temp", key="in_brine_out", on_change=sync_var, args=('brine_out_t', 'in_brine_out'),
                                    help="Hot-side sink for the Overall HTC.")
                with t3:
                    st.number_input("11th Effect Brine Temp", key="in_brine_11", on_change=sync_var, args=('brine_11', 'in_brine_11'))
                    st.number_input("Steam Inlet Temp", key="in_steam_in_t", on_change=sync_var, args=('steam_in_t', 'in_steam_in_t'))
                with t4:
                    st.number_input("SW Condenser (FFC) I/L Temp", key="in_sw_in", on_change=sync_var, args=('sw_in_t', 'in_sw_in'))
                    st.number_input("SW Condenser (FFC) O/L Temp", key="in_sw_out", on_change=sync_var, args=('sw_out_t', 'in_sw_out'))

                st.divider()
                st.markdown("**Pressures, Cooling Water & Chemicals**")
                p1, p2, p3, p4 = st.columns(4)
                with p1:
                    st.number_input("1st Effect Vapour Pressure (mmHg)", key="in_press", on_change=sync_var, args=('mra_press', 'in_press'))
                    st.number_input("LP Steam Pressure (kg/cm²g)", key="in_stm_press", on_change=sync_var, args=('stm_press', 'in_stm_press'))
                with p2:
                    st.number_input("Sea Water Pressure (kg/cm²g)", key="in_sw_press", on_change=sync_var, args=('sw_press', 'in_sw_press'))
                    st.number_input("Brine Discharge Pressure (kg/cm²g)", key="in_brine_press", on_change=sync_var, args=('brine_press', 'in_brine_press'))
                with p3:
                    st.number_input("CW Supply Temp (°C)", key="in_cw_supply", on_change=sync_var, args=('cw_supply', 'in_cw_supply'))
                    st.number_input("CW Return Temp (°C)", key="in_cw_return", on_change=sync_var, args=('cw_return', 'in_cw_return'))
                    st.number_input("CW Flow (m³/h)", key="in_cw_flow", on_change=sync_var, args=('cw_flow', 'in_cw_flow'))
                with p4:
                    st.number_input("Antiscalant Residual (ppm)", key="in_anti_ppm", on_change=sync_var, args=('chem_anti_ppm', 'in_anti_ppm'))
                    st.number_input("Antiscalant Consumption (kg/hr)", key="in_anti_cons", on_change=sync_var, args=('chem_anti_cons', 'in_anti_cons'))
                    st.number_input("Antifoam Residual (ppm)", key="in_foam_ppm", on_change=sync_var, args=('chem_foam_ppm', 'in_foam_ppm'))
                    st.number_input("Antifoam Consumption (kg/hr)", key="in_foam_cons", on_change=sync_var, args=('chem_foam_cons', 'in_foam_cons'))

                st.divider()
                st.number_input("Condensate Conductivity (µS/cm)", key="in_cond_cond", on_change=sync_var, args=('cond_cond', 'in_cond_cond'))
                st.text_area("Remarks", key="t0_remarks", on_change=sync_var, args=('remarks', 't0_remarks'), height=68,
                             help="Mirrors the Remarks box on the Reporting tab - edit either one.")

                with st.expander("Effect-wise Temperature Cascade (optional)"):
                    st.checkbox("Skip effect-wise temperatures today", key="in_skip_eff", on_change=sync_var, args=('skip_eff', 'in_skip_eff'))
                    if not get_v('skip_eff'):
                        e_df = st.data_editor(display_effect_df, key="in_effect_df", use_container_width=True, hide_index=True,
                                              disabled=["Effect ID", "Base Vapor (°C)", "Base Brine (°C)", "Base HTC"])
                        if not e_df[["Live Vapor (°C)", "Live Brine (°C)"]].equals(
                                st.session_state.shared_effect_df[["Live Vapor (°C)", "Live Brine (°C)"]]):
                            st.session_state.shared_effect_df["Live Vapor (°C)"] = e_df["Live Vapor (°C)"]
                            st.session_state.shared_effect_df["Live Brine (°C)"] = e_df["Live Brine (°C)"]
                            st.rerun()

            # ---------------------------------------------------------------- 2 · HTC 1st EFFECT
            with entry[1]:
                st.caption("Source sheet: **1st effect-HTC**. Heat transfer across the 1st effect tube bundle only.")
                st.success(
                    "**Already taken from Operational** — steam rate, 1st effect vapour temp, 1st effect brine temp, "
                    "condensate temp. Only the two genuinely 1st-effect-specific readings are below."
                )
                h1a, h1b = st.columns(2)
                with h1a:
                    st.number_input(
                        "Avg Brine Temp of Effects 4-5-6-7 (°C)", key="in_mid_effects_temp",
                        on_change=sync_var, args=('mid_effects_temp', 'in_mid_effects_temp'),
                        help="On the source sheet this column is labelled 'Feed Temp', but the tag row reads "
                             "'Avg of effects of 7,6,5,4'. It is the COLD-SIDE reference (ΔT2) for this calculation "
                             "— NOT a seawater temperature. Typically ~49 °C."
                    )
                    st.number_input(
                        "Feed Flow to 1st Effect (m³/h)", key="in_htc1_feed_flow",
                        on_change=sync_var, args=('htc1_feed_flow', 'in_htc1_feed_flow'),
                        help="Tag Z711FIT424 as recorded on the 1st-effect sheet (~514 m³/h). This is NOT the total "
                             "seawater feed (~2062) used on the Overall sheet."
                    )
                with h1b:
                    st.number_input("1st Effect Heat Transfer Area (m²)", key="in_area_1st",
                                    on_change=sync_var, args=('area_1st', 'in_area_1st'),
                                    help="π × 5.5 m × 31,244 tubes × 0.024 m OD = 12,950 m²")

                st.divider()
                d1, d2, d3, d4 = st.columns(4)
                d1.metric("ΔT1 (vapour − brine)", f"{ops_data['dt_1st']:.2f} °C")
                d2.metric("ΔT2 (condensate − eff 4-7)", f"{ops_data.get('dt2_1st', 0):.2f} °C")
                d3.metric("LMTD", f"{ops_data.get('lmtd_1st', 0):.2f} °C")
                d4.metric("1st Effect HTC", f"{ops_data['htc_1st']:.1f} W/m²K")

            # ---------------------------------------------------------------- 3 · HTC OVERALL
            with entry[2]:
                st.caption("Source sheet: **Overall-HTC**. Heat transfer across all 11 effects combined.")
                st.success(
                    "**Already taken from Operational** — steam rate, 1st effect vapour temp, brine discharge temp, "
                    "condensate temp, total seawater feed. Only the two Overall-specific readings are below."
                )
                hoa, hob = st.columns(2)
                with hoa:
                    st.number_input(
                        "Feed Temp to Cold Group (°C)", key="in_feed_cold",
                        on_change=sync_var, args=('feed_cold', 'in_feed_cold'),
                        help="On the source sheet this column is also labelled 'Feed Temp' — but here it means the "
                             "feed temperature into the cold group (~40 °C), a DIFFERENT measurement from the "
                             "'Feed Temp' on the 1st-effect sheet. It is the cold-side reference (ΔT2) here."
                    )
                with hob:
                    st.number_input("Overall Heat Transfer Area (m²)", key="in_area_overall",
                                    on_change=sync_var, args=('area_overall', 'in_area_overall'),
                                    help="11 effects × 12,950 m² × 1.15 correction = 163,818 m²")

                st.divider()
                o1, o2, o3, o4 = st.columns(4)
                o1.metric("ΔT1 (vapour − brine disch.)", f"{ops_data.get('dt1_overall', 0):.2f} °C")
                o2.metric("ΔT2 (condensate − cold grp)", f"{ops_data.get('dt2_overall', 0):.2f} °C")
                o3.metric("LMTD", f"{ops_data.get('lmtd_overall', 0):.2f} °C")
                o4.metric("Overall HTC", f"{ops_data['htc_overall']:.2f} W/m²K")

            # ---------------------------------------------------------------- 4 · FEED & BRINE
            with entry[3]:
                st.caption("Source sheet: **Feed & Brine Water Analysis**. Daily lab results.")
                st.checkbox("Skip water analysis today", key="in_skip_wq", on_change=sync_var, args=('skip_wq', 'in_skip_wq'))
                if not get_v('skip_wq'):
                    wf, wb_ = st.columns(2)
                    with wf:
                        st.markdown("**Feed Water (Sea Water)**")
                        for p, dd in WATER_SPECS["Feed"].items():
                            lo, hi = dd['lim']
                            st.number_input(f"{p}", key=f"in_{dd['var']}", on_change=sync_var,
                                            args=(dd['var'], f"in_{dd['var']}"), help=f"Specified limit: {lo} – {hi}")
                    with wb_:
                        st.markdown("**Brine Water**")
                        st.caption("No specified limits on the source sheet — tracked for trending.")
                        for p, dd in BRINE_SPECS.items():
                            st.number_input(f"{p}", key=f"in_{dd['var']}", on_change=sync_var,
                                            args=(dd['var'], f"in_{dd['var']}"))

            # ---------------------------------------------------------------- 5 · DESAL PRODUCT
            with entry[4]:
                st.caption("Source sheet: **Desal Analysis**. Product water quality.")
                if get_v('skip_wq'):
                    st.info("Water analysis is currently skipped for today (toggle on the Feed & Brine tab).")
                else:
                    pc1, pc2 = st.columns(2)
                    items = list(WATER_SPECS["Product"].items())
                    half = (len(items) + 1) // 2
                    for col, chunk in ((pc1, items[:half]), (pc2, items[half:])):
                        with col:
                            for p, dd in chunk:
                                lo, hi = dd['lim']
                                st.number_input(f"{p}", key=f"in_{dd['var']}", on_change=sync_var,
                                                args=(dd['var'], f"in_{dd['var']}"), help=f"Specified limit: {lo} – {hi}")

        with tab0_subtabs[1]:
            st.markdown("### Process Flow Diagram - Live Tags")
            if PIL_INSTALLED and (os.path.exists("Desal PFD (1).TIF") or os.path.exists("Desal PFD (1).tiff") or os.path.exists("Desal PFD.TIF")):
                try:
                    from PIL import Image
                    file_name = "Desal PFD (1).TIF" if os.path.exists("Desal PFD (1).TIF") else ("Desal PFD (1).tiff" if os.path.exists("Desal PFD (1).tiff") else "Desal PFD.TIF")
                    img = Image.open(file_name).convert("RGB")
                    buffered = BytesIO()
                    img.save(buffered, format="PNG")
                    img_str = base64.b64encode(buffered.getvalue()).decode()
                    
                    html_view = f"""
                    <div style="position: relative; width: 100%; max-width: 1200px; margin: auto; background: #fff; border: 2px solid #ddd; border-radius: 8px; overflow: hidden;">
                        <img src="data:image/png;base64,{img_str}" style="width: 100%; display: block;" alt="MED PFD"/>
                        
                        <div style="position: absolute; top: 5%; left: 2%; background: rgba(0,20,50,0.85); color: #00ff00; padding: 6px 12px; font-family: monospace; border: 1px solid #00ff00; border-radius: 4px; box-shadow: 0 0 8px #00ff00; font-size: 13px;">
                            <strong>SEA WATER SYSTEM</strong><br>
                            Sea Water Feed: {ops_data['SW Total']} m³/h<br>
                            Sea Water Upper: {ops_data['SW_Feed_1st']} m³/h<br>
                            Sea Water Lower: {get_v('sw_lower')} m³/h<br>
                            Sea Water cond I/L temp: {ops_data['SW In_overall']} °C<br>
                            Sea Water Condenser O/L Temp: {get_v('sw_out_t')} °C<br>
                            CW supply: {get_v('cw_supply')}
                        </div>
                        
                        <div style="position: absolute; top: 5%; right: 2%; background: rgba(50,0,0,0.85); color: #ff3333; padding: 6px 12px; font-family: monospace; border: 1px solid #ff3333; border-radius: 4px; box-shadow: 0 0 8px #ff3333; font-size: 13px;">
                            <strong>STEAM & 1ST EFFECT</strong><br>
                            LP Steam consumption: {ops_data['Steam']} TPH<br>
                            1st Effect Vapour Temp: {ops_data['Stm In_1st']} °C<br>
                            1st effect vapour pressure: {ops_data['Press_1st']} mmHg<br>
                            1st effect brine temp: {ops_data['Brine_1st']} °C<br>
                            Delta T: {ops_data['dt_1st']:.2f} °C
                        </div>

                        <div style="position: absolute; bottom: 5%; left: 2%; background: rgba(0,50,50,0.85); color: #00ffff; padding: 6px 12px; font-family: monospace; border: 1px solid #00ffff; border-radius: 4px; box-shadow: 0 0 8px #00ffff; font-size: 13px;">
                            <strong>PRODUCTION</strong><br>
                            Gross production: {ops_data['Gross Prod']} m³/h<br>
                            Desal production: {ops_data['Desal']} m³/h<br>
                            Condensate Return: {get_v('cond_flow')}<br>
                            condensate temp: {get_v('cond_temp')} °C
                        </div>
                        
                        <div style="position: absolute; bottom: 5%; right: 2%; background: rgba(50,25,0,0.85); color: #ff9900; padding: 6px 12px; font-family: monospace; border: 1px solid #ff9900; border-radius: 4px; box-shadow: 0 0 8px #ff9900; font-size: 13px;">
                            <strong>BRINE SYSTEM</strong><br>
                            Brine Water Return: {ops_data['Brine Return']} m³/h<br>
                            Brine Discharge Temp: {ops_data['Brine Out_overall']} °C<br>
                            CW Return: {get_v('cw_return')}
                        </div>
                    </div>
                    """
                    st.components.v1.html(html_view, height=800)
                except Exception as e:
                    st.error(f"Could not render TIF overlay. Error: {e}")
            else:
                st.info("Digital Twin HUD: Please upload 'Desal PFD (1).TIF' into the application directory to unlock the live interactive diagram overlay.")

    # --- TAB 1: FLOW KPIs & SOR MATRIX ---
    with tabs[1]:
        st.subheader("System Operating Reference (SOR) Dashboard")

        anti_gm_m3 = (get_v('chem_anti_cons') / ops_data['SW Total']) * 1000 if ops_data['SW Total'] > 0 else 0
        foam_gm_m3 = (get_v('chem_foam_cons') / ops_data['SW Total']) * 1000 if ops_data['SW Total'] > 0 else 0
        has_anti_kg = get_v('chem_anti_cons') > 0
        has_foam_kg = get_v('chem_foam_cons') > 0

        # --- Headline KPI cards: the numbers Reliance/Chembond actually track day to day, up front ---
        st.markdown("##### Headline Performance Indicators")
        kpi1, kpi2, kpi3, kpi4, kpi5 = st.columns(5)
        kpi1.metric("GOR", f"{ops_data['GOR']:.2f}", f"{ops_data['GOR'] - 11.4:+.2f} vs SOR", help="Gain Output Ratio: Gross production / Steam consumption. SOR baseline: 11.4")
        kpi2.metric("STEC", f"{ops_data['STEC']:.1f} kWh/t", help="Specific Thermal Energy Consumption per tonne of distillate")
        kpi3.metric("Overall HTC", f"{ops_data['htc_overall']:.1f} W/m²K", help="Whole-plant heat transfer coefficient (steam condensation basis)")
        kpi4.metric("1st Effect HTC", f"{ops_data['htc_1st']:.1f} W/m²K", help="1st effect heat transfer coefficient (steam condensation basis)")
        kpi5.metric("Recovery", f"{ops_data['Recovery']:.1f}%", help="Gross production / Total sea water feed")
        st.divider()

        def color_diff(val):
            try:
                v = float(val)
                color = 'green' if v >= 0 else 'red'
                return f'color: {color}; font-weight: bold'
            except:
                return ''

        st.markdown("### A) SEA WATER")
        df_a = pd.DataFrame([
            {"Parameter": "Temp.", "UOM": "°C", "Design": "19-35", "SOR Base": 29.0, "Actual": get_v('sw_in_t'), "Difference": get_v('sw_in_t') - 29.0},
            {"Parameter": "Pressure", "UOM": "kg/cm2-g", "Design": "2.5", "SOR Base": 1.7, "Actual": get_v('sw_press'), "Difference": get_v('sw_press') - 1.7},
            {"Parameter": "Total sea water flow to desal unit", "UOM": "m3/hr", "Design": "2400", "SOR Base": 2112.0, "Actual": ops_data['SW Total'], "Difference": ops_data['SW Total'] - 2112.0}
        ])
        st.dataframe(df_a.style.map(color_diff, subset=['Difference']).format({"SOR Base": "{:.1f}", "Actual": "{:.1f}", "Difference": "{:+.1f}"}), use_container_width=True, hide_index=True)

        st.markdown("### B) LP STEAM")
        df_b = pd.DataFrame([
            {"Parameter": "Total Flow (Thermocompressor + NCG)", "UOM": "Tonne/hr", "Design": "97.5", "SOR Base": 76.94, "Actual": ops_data['Steam'], "Difference": ops_data['Steam'] - 76.94},
            {"Parameter": "Pressure", "UOM": "kg/cm2-g", "Design": "3.5", "SOR Base": 4.3, "Actual": get_v('stm_press'), "Difference": get_v('stm_press') - 4.3},
            {"Parameter": "Temp.", "UOM": "°C", "Design": "147", "SOR Base": 176.0, "Actual": get_v('mra_t1'), "Difference": get_v('mra_t1') - 176.0}
        ])
        st.dataframe(df_b.style.map(color_diff, subset=['Difference']).format({"SOR Base": "{:.2f}", "Actual": "{:.2f}", "Difference": "{:+.2f}"}), use_container_width=True, hide_index=True)

        st.markdown("### C) COOLING WATER")
        df_c = pd.DataFrame([
            {"Parameter": "Flow", "UOM": "m3/hr", "Design": "4200", "SOR Base": 2726.0, "Actual": get_v('cw_flow'), "Difference": get_v('cw_flow') - 2726.0},
            {"Parameter": "Cooling Water Supply Temp", "UOM": "°C", "Design": "32", "SOR Base": 31.9, "Actual": get_v('cw_supply'), "Difference": get_v('cw_supply') - 31.9},
            {"Parameter": "Cooling Water Return Temp", "UOM": "°C", "Design": "41", "SOR Base": 37.5, "Actual": get_v('cw_return'), "Difference": get_v('cw_return') - 37.5}
        ])
        st.dataframe(df_c.style.map(color_diff, subset=['Difference']).format({"SOR Base": "{:.1f}", "Actual": "{:.1f}", "Difference": "{:+.1f}"}), use_container_width=True, hide_index=True)

        st.markdown("### D) DESALINATED WATER")
        df_d = pd.DataFrame([
            {"Parameter": "Desal water production", "UOM": "m3/hr", "Design": "1000", "SOR Base": 824.0, "Actual": ops_data['Desal'], "Difference": ops_data['Desal'] - 824.0},
            {"Parameter": "Conductivity", "UOM": "microS/cm", "Design": "<15", "SOR Base": 2.5, "Actual": get_v('p_cond'), "Difference": get_v('p_cond') - 2.5}
        ])
        st.dataframe(df_d.style.map(color_diff, subset=['Difference']).format({"SOR Base": "{:.1f}", "Actual": "{:.1f}", "Difference": "{:+.1f}"}), use_container_width=True, hide_index=True)

        st.markdown("### E) BRINE DISCHARGE")
        df_e = pd.DataFrame([
            {"Parameter": "Flow", "UOM": "m3/hr", "Design": "1400", "SOR Base": 1315.0, "Actual": ops_data['Brine Return'], "Difference": ops_data['Brine Return'] - 1315.0},
            {"Parameter": "Temp.", "UOM": "°C", "Design": "43.5", "SOR Base": 40.5, "Actual": ops_data['Brine Out_overall'], "Difference": ops_data['Brine Out_overall'] - 40.5},
            {"Parameter": "Pressure", "UOM": "kg/cm2-g", "Design": "6", "SOR Base": 1.3, "Actual": get_v('brine_press'), "Difference": get_v('brine_press') - 1.3}
        ])
        st.dataframe(df_e.style.map(color_diff, subset=['Difference']).format({"SOR Base": "{:.1f}", "Actual": "{:.1f}", "Difference": "{:+.1f}"}), use_container_width=True, hide_index=True)

        st.markdown("### F) CONDENSATE RETURN")
        df_f = pd.DataFrame([
            {"Parameter": "Quantity", "UOM": "m3/hr", "Design": "100", "SOR Base": 127.0, "Actual": get_v('cond_flow'), "Difference": get_v('cond_flow') - 127.0},
            {"Parameter": "Temp.", "UOM": "°C", "Design": "70", "SOR Base": 71.0, "Actual": get_v('cond_temp'), "Difference": get_v('cond_temp') - 71.0},
            {"Parameter": "Conductivity", "UOM": "microS/cm", "Design": "<15", "SOR Base": 3.0, "Actual": get_v('cond_cond'), "Difference": get_v('cond_cond') - 3.0}
        ])
        st.dataframe(df_f.style.map(color_diff, subset=['Difference']).format({"SOR Base": "{:.1f}", "Actual": "{:.1f}", "Difference": "{:+.1f}"}), use_container_width=True, hide_index=True)

        st.markdown("### H) PLANT CAPACITY DETAILS")
        df_h = pd.DataFrame([
            {"Parameter": "Gross desal water production", "UOM": "tph", "Design": "1000", "SOR Base": 873.0, "Actual": ops_data['Gross Prod'], "Difference": ops_data['Gross Prod'] - 873.0},
            {"Parameter": "Conversion (Product to Feed)", "UOM": "%", "Design": "41.6", "SOR Base": 41.4, "Actual": ops_data['Conversion'] * 100, "Difference": (ops_data['Conversion'] * 100) - 41.4},
            {"Parameter": "GOR / Steam Economy", "UOM": "-", "Design": "10.5", "SOR Base": 11.4, "Actual": ops_data['GOR'], "Difference": ops_data['GOR'] - 11.4},
            {"Parameter": "Steam Economy (Steam/Desal)", "UOM": "Norms", "Design": "0.088", "SOR Base": 0.088, "Actual": ops_data['Economy'], "Difference": ops_data['Economy'] - 0.088},
            {"Parameter": "1st effect vapour temp.", "UOM": "°C", "Design": "74", "SOR Base": 72.0, "Actual": get_v('mra_t1'), "Difference": get_v('mra_t1') - 72.0},
            {"Parameter": "1st effect pressure", "UOM": "mm Hg", "Design": "248", "SOR Base": 256.0, "Actual": get_v('mra_press'), "Difference": get_v('mra_press') - 256.0},
            {"Parameter": "1st effect brine temp.", "UOM": "°C", "Design": "69", "SOR Base": 69.0, "Actual": get_v('mra_bt1'), "Difference": get_v('mra_bt1') - 69.0},
            {"Parameter": "11th effect brine temp", "UOM": "°C", "Design": "44", "SOR Base": 42.0, "Actual": get_v('brine_11'), "Difference": get_v('brine_11') - 42.0},
            {"Parameter": "Delta T (1st effect vapour temp -1st effect brine temp)", "UOM": "°C", "Design": "4", "SOR Base": 2.5, "Actual": ops_data['dt_1st'], "Difference": ops_data['dt_1st'] - 2.5},
            {"Parameter": "Overall delta T(1st eff brine temp - 11th eff brine temp)", "UOM": "°C", "Design": "25", "SOR Base": 27.1, "Actual": ops_data['dt_overall_simple'], "Difference": ops_data['dt_overall_simple'] - 27.1},
            {"Parameter": "Feed temp to cold group", "UOM": "°C", "Design": "40", "SOR Base": 37.0, "Actual": get_v('feed_cold'), "Difference": get_v('feed_cold') - 37.0}
        ])
        st.dataframe(df_h.style.map(color_diff, subset=['Difference']).format({"SOR Base": "{:.3f}", "Actual": "{:.3f}", "Difference": "{:+.3f}"}), use_container_width=True, hide_index=True)

        st.markdown("### I) CHEMICAL DOSING & RESIDUAL")
        st.caption("Dosing rate (gm/m³) needs a logged kg-consumption figure for the day; residual PPM comes from lab analysis and is tracked independently.")
        df_i = pd.DataFrame([
            {"Parameter": "Antiscalant (ID204)/IN-204AS", "UOM": "gm/m3 sea water", "Design": "7", "SOR Base": 10.5,
             "Actual": anti_gm_m3 if has_anti_kg else np.nan, "Difference": (anti_gm_m3 - 10.5) if has_anti_kg else np.nan,
             "Residual (PPM)": get_v('chem_anti_ppm')},
            {"Parameter": "Antifoam", "UOM": "gm/m3 sea water", "Design": "0.25", "SOR Base": 0.16,
             "Actual": foam_gm_m3 if has_foam_kg else np.nan, "Difference": (foam_gm_m3 - 0.16) if has_foam_kg else np.nan,
             "Residual (PPM)": get_v('chem_foam_ppm')}
        ])
        st.dataframe(
            df_i.style.map(color_diff, subset=['Difference']).format({"SOR Base": "{:.2f}", "Actual": "{:.2f}", "Difference": "{:+.2f}", "Residual (PPM)": "{:.2f}"}, na_rep="No kg data logged"),
            use_container_width=True, hide_index=True
        )
        if not has_anti_kg or not has_foam_kg:
            missing_chem = ([] if has_anti_kg else ["antiscalant"]) + ([] if has_foam_kg else ["antifoam"])
            st.info(f"No {' or '.join(missing_chem)} consumption (kg) is logged for this date, so the gm/m³ dosing rate can't be calculated - only the PPM residual is shown. Log daily kg consumption on the Chemical Dosing tab to enable this.")
        
        sor_export_dfs = {
            "A) SEA WATER": df_a, "B) LP STEAM": df_b, "C) COOLING WATER": df_c, 
            "D) DESALINATED WATER": df_d, "E) BRINE DISCHARGE": df_e, 
            "F) CONDENSATE RETURN": df_f, "H) PLANT CAPACITY DETAILS": df_h, 
            "I) CHEMICAL CONSUMPTION": df_i
        }

    # --- TAB 2: OVERALL HTC ---
    with tabs[2]:
        st.subheader("Thermal Integrity & Fouling Analysis")
        st.caption(
            "Both calculations use the steam-condensation basis: **U = Q / (A × LMTD)**, with "
            "**LMTD = (ΔT1 − ΔT2) / ln(ΔT1/ΔT2)**. They differ in which temperatures define ΔT1 and ΔT2, "
            "and in heat transfer area — exactly as in the two source sheets."
        )

        htc_headline = st.columns(4)
        _d1 = ops_data['htc_1st'] - HTC_1ST_U_SOR
        _d2 = ops_data['htc_overall'] - HTC_OVERALL_U_SOR
        htc_headline[0].metric("1st Effect HTC", f"{ops_data['htc_1st']:.1f} W/m²K",
                               f"{_d1:+.1f} vs SOR ({HTC_1ST_U_SOR:.0f})")
        htc_headline[1].metric("Overall HTC", f"{ops_data['htc_overall']:.2f} W/m²K",
                               f"{_d2:+.2f} vs SOR ({HTC_OVERALL_U_SOR:.1f})")
        htc_headline[2].metric("1st Effect Fouling Rf", f"{ops_data['rf_1st']:.6f}",
                               help="Rf = 1/U_actual − 1/U_SOR. Rising = fouling building up.")
        htc_headline[3].metric("Overall Fouling Rf", f"{ops_data['rf_overall']:.5f}",
                               help="Rf = 1/U_actual − 1/U_SOR. Rising = fouling building up.")

        if ops_data['htc_1st'] == 0 or ops_data['htc_overall'] == 0:
            st.warning(
                "An HTC reads 0, which means one of its required temperatures is missing or non-physical "
                "(ΔT ≤ 0). Check the Inputs tab — the calculator reports 0 rather than inventing a value."
            )

        st.divider()
        c1, c2 = st.columns(2)

        with c1:
            st.markdown("#### 1st Effect")
            st.caption("Source: `1st effect-HTC` · Area 12,950 m² (single tube bundle)")
            st.number_input("1st Effect Heat Transfer Area (m²)", key="t2_area_1st",
                            on_change=sync_var, args=('area_1st', 't2_area_1st'))
            st.number_input("Avg Brine Temp, Effects 4-5-6-7 (°C)", key="t2_mid_effects_temp",
                            on_change=sync_var, args=('mid_effects_temp', 't2_mid_effects_temp'),
                            help="Cold-side reference. The source sheet calls this 'Feed Temp' — it is not a seawater temp.")
            st.dataframe(pd.DataFrame([
                {"Step": "ΔT1  =  vapour − 1st eff. brine", "Value": ops_data['dt_1st'], "Unit": "°C"},
                {"Step": "ΔT2  =  condensate − eff 4-7 avg", "Value": ops_data['dt2_1st'], "Unit": "°C"},
                {"Step": "LMTD", "Value": ops_data['lmtd_1st'], "Unit": "°C"},
                {"Step": "Q  (heat duty)", "Value": ops_data['q_1st'] / 1000, "Unit": "kW"},
                {"Step": "A  (area)", "Value": get_v('area_1st'), "Unit": "m²"},
                {"Step": "U = Q / (A × LMTD)", "Value": ops_data['htc_1st'], "Unit": "W/m²K"},
            ]).style.format({"Value": "{:,.2f}"}), use_container_width=True, hide_index=True)

        with c2:
            st.markdown("#### Overall Plant")
            st.caption("Source: `Overall-HTC` · Area 163,818 m² (11 × 12,950 × 1.15)")
            st.number_input("Overall Heat Transfer Area (m²)", key="t2_area_overall",
                            on_change=sync_var, args=('area_overall', 't2_area_overall'))
            st.number_input("Feed Temp to Cold Group (°C)", key="t2_feed_cold",
                            on_change=sync_var, args=('feed_cold', 't2_feed_cold'),
                            help="Cold-side reference. The source sheet also calls this 'Feed Temp', but it is a "
                                 "different measurement from the 1st-effect sheet's column of the same name.")
            st.dataframe(pd.DataFrame([
                {"Step": "ΔT1  =  vapour − brine discharge", "Value": ops_data['dt1_overall'], "Unit": "°C"},
                {"Step": "ΔT2  =  condensate − cold grp feed", "Value": ops_data['dt2_overall'], "Unit": "°C"},
                {"Step": "LMTD", "Value": ops_data['lmtd_overall'], "Unit": "°C"},
                {"Step": "Q  (heat duty)", "Value": ops_data['q_overall'] / 1000, "Unit": "kW"},
                {"Step": "A  (area)", "Value": get_v('area_overall'), "Unit": "m²"},
                {"Step": "U = Q / (A × LMTD)", "Value": ops_data['htc_overall'], "Unit": "W/m²K"},
            ]).style.format({"Value": "{:,.2f}"}), use_container_width=True, hide_index=True)

        st.divider()
        st.markdown("#### Fouling Trend")
        _logs = st.session_state.daily_logs
        if _logs is not None and not _logs.empty and 'Date' in _logs.columns:
            tdf = _logs.copy()
            tdf['Date'] = standardize_dates(tdf['Date'])
            for c in ['1st Effect HTC', 'Overall HTC']:
                tdf[c] = pd.to_numeric(tdf.get(c), errors='coerce')
            tdf = tdf.dropna(subset=['Date']).sort_values('Date')
            tdf = tdf[(tdf['1st Effect HTC'].fillna(0) > 0) | (tdf['Overall HTC'].fillna(0) > 0)]
            if len(tdf) > 1:
                g1, g2 = st.columns(2)
                for col, metric, base, colr in (
                    (g1, '1st Effect HTC', HTC_1ST_U_SOR, '#1f77b4'),
                    (g2, 'Overall HTC', HTC_OVERALL_U_SOR, '#2ca02c'),
                ):
                    sub = tdf[tdf[metric] > 0]
                    if len(sub) > 1:
                        ch = alt.Chart(sub).mark_line(point=True, color=colr).encode(
                            x=alt.X('Date:T', title=None),
                            y=alt.Y(f'{metric}:Q', scale=alt.Scale(zero=False), title='W/m²K'),
                            tooltip=['Date:T', f'{metric}:Q'])
                        rule = alt.Chart(pd.DataFrame({'y': [base]})).mark_rule(
                            color='red', strokeDash=[4, 4]).encode(y='y:Q')
                        trend = ch.transform_regression('Date', metric).mark_line(
                            color='black', strokeDash=[5, 5])
                        col.markdown(f"**{metric}** (red = SOR baseline)")
                        col.altair_chart(ch + rule + trend, use_container_width=True)
                    else:
                        col.info(f"Not enough {metric} history yet.")
            else:
                st.info("No HTC history in the registry yet. Upload HTC data on the Bulk Uploads tab to build a trend.")
        else:
            st.info("No HTC history in the registry yet.")

    # --- TAB 3: WATER ANALYSIS TAB ---
    with tabs[3]:
        st.subheader("Laboratory Analysis Evaluation")
        if not get_v('skip_wq'):
            w_col1, w_col2 = st.columns(2)
            with w_col1:
                st.markdown("**Intake Seawater Matrix**")
                for param, d in WATER_SPECS["Feed"].items():
                    c_in, c_chk = st.columns([2, 2])
                    with c_in: 
                        st.number_input(f"{param} ({d['lim'][0]}-{d['lim'][1]})", key=f"t3_{d['var']}", on_change=sync_var, args=(d['var'], f"t3_{d['var']}"))
                    c_chk.markdown(f"<div style='margin-top:30px'>{water_data['Feed'][param]['status']}</div>", unsafe_allow_html=True)
            with w_col2:
                st.markdown("**Product Distillate Matrix**")
                for param, d in WATER_SPECS["Product"].items():
                    c_in, c_chk = st.columns([2, 2])
                    with c_in: 
                        st.number_input(f"{param} ({d['lim'][0]}-{d['lim'][1]})", key=f"t3_{d['var']}", on_change=sync_var, args=(d['var'], f"t3_{d['var']}"))
                    c_chk.markdown(f"<div style='margin-top:30px'>{water_data['Product'][param]['status']}</div>", unsafe_allow_html=True)

    # --- TAB 4: CHEMICAL DOSING ---
    with tabs[4]:
        st.subheader("Chemical Treatment Monitoring")
        st.number_input("Sea Water Feed (m³/h)", key="t4_sw_tot", on_change=sync_var, args=('sw_total', 't4_sw_tot'))
        st.divider()
        cc1, cc2 = st.columns(2)
        with cc1:
            st.markdown("### Kem Watreat r 3687 (Antiscalant Evaluation)")
            st.number_input("Target Dosing Level (PPM)", key="t4_anti_ppm", on_change=sync_var, args=('chem_anti_ppm', 't4_anti_ppm'))
            theo_anti = (ops_data['SW Total'] * get_v('chem_anti_ppm')) / 1000
            st.info(f"Theoretical Flow Target Requirements: {theo_anti:.2f} kg/hr")
            st.number_input("Actual Consumption (kg/hr)", key="t4_anti_cons", on_change=sync_var, args=('chem_anti_cons', 't4_anti_cons'))
        with cc2:
            st.markdown("### Kem Antifoam 1795 Performance")
            st.number_input("Target Dosing Level (PPM)", key="t4_foam_ppm", on_change=sync_var, args=('chem_foam_ppm', 't4_foam_ppm'))
            theo_foam = (ops_data['SW Total'] * get_v('chem_foam_ppm')) / 1000
            st.info(f"Theoretical Flow Target Requirements: {theo_foam:.2f} kg/hr")
            st.number_input("Actual Consumption (kg/hr)", key="t4_foam_cons", on_change=sync_var, args=('chem_foam_cons', 't4_foam_cons'))

    # --- TAB 5: MRA EVALUATION ENGINE ---
    with tabs[5]:
        st.subheader("Multi-Variable Normalization Predictor")
        st.markdown("Modify process inputs to execute 'What-If' scenarios. Input limits dynamically unbind to prevent system crashes.")
        controls_col, calc_col = st.columns([1, 2])
        
        with controls_col:
            st.number_input("1st effect vapour pressure (mmHg)", key="t5_press", on_change=sync_var, args=('mra_press', 't5_press'))
            st.number_input("1st Effect Vapour Temp (°C)", key="t5_t1", on_change=sync_var, args=('mra_t1', 't5_t1'))
            st.number_input("Sea Water Upper (m³/h)", key="t5_sw_up", on_change=sync_var, args=('sw_upper', 't5_sw_up'))
            st.number_input("1st effect brine temp (°C)", key="t5_bt1", on_change=sync_var, args=('mra_bt1', 't5_bt1'))
            st.number_input("Brine Water Return (m³/h)", key="t5_bflow", on_change=sync_var, args=('brine_ret', 't5_bflow'))
            st.number_input("LP Steam consumption (TPH)", key="t5_steam", on_change=sync_var, args=('steam', 't5_steam'))
            st.number_input("Antiscalant PPM", key="t5_anti", on_change=sync_var, args=('chem_anti_ppm', 't5_anti'))

        with calc_col:
            k1, k2, k3 = st.columns(3)
            k1.metric("Actual Gross SCADA", f"{mra_data['Actual']:.1f} m³/h")
            k2.metric(f"Predicted Twin Mode ({model_type})", f"{mra_data['Predicted']:.1f} m³/h")
            
            diff_pct = (mra_data['Residual'] / mra_data['Predicted']) * 100 if mra_data['Predicted'] > 0 else 0
            if diff_pct <= -5.0: 
                k3.error(f"Residual Gap: {mra_data['Residual']:.1f} TPH ({diff_pct:.1f}%) - Shutdown/Acid Clean Required")
            elif diff_pct <= -4.0: 
                k3.warning(f"Residual Gap: {mra_data['Residual']:.1f} TPH ({diff_pct:.1f}%) - Optimize Scale Treatment Dosing")
            else: 
                k3.success(f"Residual Gap: {mra_data['Residual']:.1f} TPH ({diff_pct:.1f}%) - Operational Thermal Base Clean")
                
            if model_type != "OLS": 
                st.info("Machine Learning Evaluation Mode Active: Multi-variable parameter expansion is only available under pure linear OLS logic.")
            st.dataframe(mra_data['Variance_DF'].style.format({"Baseline": "{:.1f}", "Live Input": "{:.1f}", "Deviation": "{:+.1f}", "Regression Weight": "{:.3f}", "Impact (TPH)": "{:+.1f}"}, na_rep="-"), use_container_width=True, hide_index=True)

    # --- TAB 6: REPORTING & ANALYTICS ---
    with tabs[6]:
        st.subheader("Central Data Logging & Historical Analytics")
        rep_tabs = st.tabs(["Daily Execution Dashboard", "Master Historical Database", "Long-Term Performance Trends", "Interactive Explorer"])
        
        with rep_tabs[0]:
            m_col1, m_col2, m_col3, m_col4 = st.columns(4)
            m_col1.metric("Target Record Date", log_date.strftime('%d-%m-%Y')) 
            m_col2.metric("Gross Volumetric Production", f"{ops_data['Gross Prod']} m³/h", delta=f"{ops_data['Gross Prod'] - 1000:.0f} from Design" if ops_data['Gross Prod'] < 1000 else None)
            m_col3.metric("System GOR", f"{ops_data['GOR']:.2f}", delta=f"{ops_data['GOR'] - 10.5:.2f} from Target" if ops_data['GOR'] < 10.5 else None)
            
            diff_pct = (mra_data['Residual'] / mra_data['Predicted']) * 100 if mra_data['Predicted'] > 0 else 0
            if diff_pct <= -5.0: 
                delta_text, d_color = f"{diff_pct:.1f}% (Scaling Critical)", "inverse"
            elif diff_pct <= -4.0: 
                delta_text, d_color = f"{diff_pct:.1f}% (Deviation Warning)", "inverse"
            else: 
                delta_text, d_color = f"{diff_pct:.1f}% (Clean Baseline)", "normal"
                
            m_col4.metric("Twin MRA Performance Gap", f"{mra_data['Residual']:.1f} TPH", delta=delta_text, delta_color=d_color)
            
            st.divider()
            graph_col1, graph_col2 = st.columns(2)
            with graph_col1:
                if model_type == "OLS":
                    st.markdown("#### Parameter Deviation Impact (m³/h)")
                    impact_chart = alt.Chart(mra_data['Variance_DF']).mark_bar().encode(
                        x=alt.X('Impact (TPH):Q'), 
                        y=alt.Y('Parameter:N', sort='-x', title=''), 
                        color=alt.condition(alt.datum['Impact (TPH)'] > 0, alt.value('#2ca02c'), alt.value('#d62728')), 
                        tooltip=['Parameter', 'Impact (TPH)']
                    ).properties(height=300)
                    st.altair_chart(impact_chart, use_container_width=True)
                else:
                    st.markdown("#### Component Weight Importance (ML Mode)")
                    impact_chart = alt.Chart(mra_data['Variance_DF']).mark_bar(color='#1f77b4').encode(
                        x=alt.X('Regression Weight:Q', title="Importance Weight Matrix %"), 
                        y=alt.Y('Parameter:N', sort='-x', title=''), 
                        tooltip=['Parameter', 'Regression Weight']
                    ).properties(height=300)
                    st.altair_chart(impact_chart, use_container_width=True)

            with graph_col2:
                st.markdown("#### Mass Distribution Profile")
                unaccounted = ops_data['SW Total'] - (ops_data['Desal'] + ops_data['Brine Return'])
                mb_data = pd.DataFrame({'Stream': ['Product Net', 'Brine Blowdown', 'Loss Matrix'], 'Volume': [ops_data['Desal'], ops_data['Brine Return'], unaccounted if unaccounted > 0 else 0]})
                donut = alt.Chart(mb_data).mark_arc(innerRadius=50).encode(
                    theta=alt.Theta("Volume:Q"), 
                    color=alt.Color("Stream:N", scale=alt.Scale(scheme='set2')), 
                    tooltip=['Stream', 'Volume']
                ).properties(height=300)
                st.altair_chart(donut, use_container_width=True)

            st.divider()
            st.text_area("Remarks & Performance Observations", key="in_remarks", on_change=sync_var, args=('remarks', 'in_remarks'), placeholder="Record operational shift anomalies, sensor calibrations, or clean notes here...")
            
            st.markdown("### Record and Commit Log Payload")
            c_pwd, c_save, c_export, c_csv = st.columns([1.5, 1, 1, 1])
            with c_pwd: 
                pwd_append = st.text_input("Security Key Access", type="password", key="pwd_append", label_visibility="collapsed", placeholder="Enter Master Security Password to Commit")
            with c_save:
                if st.button("Save Operational Record", use_container_width=True):
                    if pwd_append == "12345678":
                        db_dict = {
                            "Date": [log_date_str], 
                            "Sea Water Upper": [get_v('sw_upper')], 
                            "Sea Water Lower": [get_v('sw_lower')],
                            "Sea Water Feed": [ops_data['SW Total']], 
                            "Sea Water Pressure": [get_v('sw_press')],
                            "Brine Water Return": [ops_data['Brine Return']], 
                            "Desal production": [ops_data['Desal']], 
                            "LP Steam consumption": [ops_data['Steam']],
                            "LP Steam Pressure": [get_v('stm_press')],
                            "Condensate Return": [get_v('cond_flow')], 
                            "condensate temp": [get_v('cond_temp')],
                            "Condensate Conductivity": [get_v('cond_cond')],
                            "1st Effect Vapour Temp": [get_v('mra_t1')], 
                            "1st effect brine temp": [get_v('mra_bt1')], 
                            "11th Effect Brine Temp": [get_v('brine_11')],
                            "Feed Temp to Cold Group": [get_v('feed_cold')],
                            "Intermediate Effects Avg Brine Temp": [get_v('mid_effects_temp')],
                            "Delta T": [ops_data['dt_1st']], 
                            "1st effect vapour pressure": [get_v('mra_press')], 
                            "Brine Discharge Temp": [get_v('brine_out_t')], 
                            "Brine Discharge Pressure": [get_v('brine_press')],
                            "Sea Water cond I/L temp": [get_v('sw_in_t')], 
                            "Sea Water Condenser O/L Temp": [get_v('sw_out_t')], 
                            "CW supply": [get_v('cw_supply')], 
                            "CW Return": [get_v('cw_return')], 
                            "CW Flow": [get_v('cw_flow')],
                            "Gross production": [ops_data['Gross Prod']],
                            "GOR": [round(ops_data['GOR'], 2)], 
                            "STEC": [round(ops_data['STEC'], 2)],
                            "Overall HTC": [round(ops_data['htc_overall'], 2)], 
                            "1st Effect HTC": [round(ops_data['htc_1st'], 2)], 
                            "Residual": [round(mra_data['Residual'], 1)], 
                            "Antiscalant (kg)": [chem_data['anti_cons']], 
                            "Antifoam (kg)": [chem_data['foam_cons']], 
                            "Anti_PPM": [get_v('chem_anti_ppm')], 
                            "Foam_PPM": [get_v('chem_foam_ppm')], 
                            "Area_1st": [get_v('area_1st')], 
                            "Area_Overall": [get_v('area_overall')], 
                            "Remarks": [get_v('remarks')]
                        }
                        for cat in ['Feed', 'Product']:
                            for param, details in WATER_SPECS[cat].items(): 
                                db_dict[details['db_col']] = [get_v(details['var'])]
                        for param, details in BRINE_SPECS.items():
                            db_dict[details['db_col']] = [get_v(details['var'])]

                        # Persist the HTC sheets' own inputs + derived values so a manually-entered day
                        # appears on the HTC trends exactly like a bulk-uploaded one.
                        db_dict.update({
                            "Steam Inlet Temp": [get_v('steam_in_t')],
                            "HTC1_Feed_Flow": [get_v('htc1_feed_flow')],
                            "HTC1_Steam_TPH": [get_v('steam')],
                            "HTC1_Feed_Temp_Eff4to7": [get_v('mid_effects_temp')],
                            "HTC1_Brine_Temp": [get_v('mra_bt1')],
                            "HTC1_Vapor_Temp": [get_v('mra_t1')],
                            "HTC1_Cond_Temp": [get_v('cond_temp')],
                            "HTC1_dT1": [round(ops_data['dt_1st'], 3)],
                            "HTC1_dT2": [round(ops_data['dt2_1st'], 3)],
                            "HTC1_LMTD": [round(ops_data['lmtd_1st'], 3)],
                            "HTC1_Rf": [round(ops_data['rf_1st'], 8)],
                            "HTCO_Feed_Flow": [get_v('sw_total')],
                            "HTCO_Steam_TPH": [get_v('steam')],
                            "HTCO_Feed_Temp_ColdGrp": [get_v('feed_cold')],
                            "HTCO_Brine_Disch_Temp": [get_v('brine_out_t')],
                            "HTCO_Vapor_Temp": [get_v('mra_t1')],
                            "HTCO_Cond_Temp": [get_v('cond_temp')],
                            "HTCO_dT1": [round(ops_data['dt1_overall'], 3)],
                            "HTCO_dT2": [round(ops_data['dt2_overall'], 3)],
                            "HTCO_LMTD": [round(ops_data['lmtd_overall'], 3)],
                            "HTCO_Rf": [round(ops_data['rf_overall'], 8)],
                        })
                        
                        new_log = pd.DataFrame(db_dict)
                        st.session_state.daily_logs = pd.concat([st.session_state.daily_logs, new_log], ignore_index=True)
                        
                        # MASTER DATE FIX: Standardize before dropping duplicates to eradicate "ghost" format duplication
                        st.session_state.daily_logs['Date'] = standardize_dates(st.session_state.daily_logs['Date']).dt.strftime('%Y-%m-%d')
                        st.session_state.daily_logs = st.session_state.daily_logs.dropna(subset=['Date'])
                        st.session_state.daily_logs = st.session_state.daily_logs.drop_duplicates(subset=['Date'], keep='last').reset_index(drop=True)
                        
                        save_database(db_conn, st.session_state.daily_logs, LOCAL_DB_FILE)
                        st.success("Operational record successfully integrated into file engine!")
                        time.sleep(1.0)
                        st.rerun()  
                    elif pwd_append != "": 
                        st.error("Master verification credential failed.")
            with c_export:
                word_file = generate_comprehensive_report(log_date, ops_data, sor_export_dfs, water_data, chem_data, mra_data, get_v('skip_wq'), get_v('remarks'))
                st.download_button("Export Word Document (.docx)", data=word_file, file_name=f"MED4_ExecutiveReport_{log_date_str}.docx", mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document", use_container_width=True)
            
            with c_csv:
                csv_file = generate_daily_csv(log_date, ops_data, water_data, chem_data, mra_data, st.session_state.vars)
                st.download_button("Export Tabular Values (.csv)", data=csv_file, file_name=f"MED4_DataRecord_{log_date_str}.csv", mime="text/csv", use_container_width=True)

        with rep_tabs[1]:
            st.markdown("#### Master System Registry Database")
            display_cols = [c for c in EXACT_DB_COLUMNS if c in st.session_state.daily_logs.columns]
            edited_db = st.data_editor(st.session_state.daily_logs[display_cols] if not st.session_state.daily_logs.empty else st.session_state.daily_logs, num_rows="dynamic", use_container_width=True)
            c_sync_pwd, c_sync, c_dl = st.columns([2, 1, 1])
            with c_sync_pwd: 
                pwd_sync = st.text_input("Database Write-Access Password", type="password", key="pwd_sync", label_visibility="collapsed", placeholder="Enter Database Master Password to Save Modifications")
            with c_sync:
                if st.button("Synchronize Registry", use_container_width=True):
                    if pwd_sync == "12345678":
                        # MASTER DATE FIX: Standardize manually edited database
                        edited_db['Date'] = standardize_dates(edited_db['Date']).dt.strftime('%Y-%m-%d')
                        st.session_state.daily_logs = edited_db.dropna(subset=['Date']).drop_duplicates(subset=['Date'], keep='last').reset_index(drop=True)
                        
                        save_database(db_conn, st.session_state.daily_logs, LOCAL_DB_FILE)
                        st.success("Master registry records updated successfully!")
                    else: 
                        st.error("System modification credentials failed.")
            with c_dl:
                st.download_button("Download Database Offline Backup", data=st.session_state.daily_logs.to_csv(index=False).encode('utf-8'), file_name=f"MED4_MasterRegistry_Backup.csv", mime='text/csv', use_container_width=True)

            st.divider()
            st.markdown("#### Aggregated Monthly Performance Generator")
            if not st.session_state.daily_logs.empty:
                df_logs = st.session_state.daily_logs.copy()
                
                df_logs['Date'] = standardize_dates(df_logs['Date'])
                df_logs = df_logs.dropna(subset=['Date'])
                
                month_data = df_logs[(df_logs['Date'].dt.month == log_date.month) & (df_logs['Date'].dt.year == log_date.year)].copy()
                if not month_data.empty:
                    if st.button("Compile and Generate Monthly Summary (.docx)", use_container_width=True):
                        monthly_doc = generate_monthly_report(month_data, log_date.strftime('%B'), str(log_date.year))
                        st.download_button("Download Monthly Briefing Document", data=monthly_doc, file_name=f"MED4_MonthlySummary_{log_date.strftime('%b_%Y')}.docx", mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document")

        with rep_tabs[2]:
            if not st.session_state.daily_logs.empty:
                df_logs = st.session_state.daily_logs.copy()
                
                df_logs['Date'] = standardize_dates(df_logs['Date'])
                df_logs = df_logs.dropna(subset=['Date'])
                
                if not df_logs.empty:
                    df_logs['Total SW Feed (m3/h)'] = pd.to_numeric(df_logs.get('Sea Water Feed', 0), errors='coerce')
                    df_logs['Recovery (%)'] = np.where(df_logs['Total SW Feed (m3/h)'] > 0, (pd.to_numeric(df_logs.get('Gross production', 0), errors='coerce') / df_logs['Total SW Feed (m3/h)']) * 100, 0)
                    
                    df_logs['Actual Production'] = pd.to_numeric(df_logs.get('Gross production', 0), errors='coerce')
                    df_logs['Residual_Val'] = pd.to_numeric(df_logs.get('Residual', 0), errors='coerce')
                    df_logs['Predicted Production'] = df_logs['Actual Production'] - df_logs['Residual_Val']
                    df_logs['Overall_HTC_Val'] = pd.to_numeric(df_logs.get('Overall HTC', 0), errors='coerce')
                    df_logs['GOR_Val'] = pd.to_numeric(df_logs.get('GOR', 0), errors='coerce')
                    df_logs['STEC_Val'] = pd.to_numeric(df_logs.get('STEC', np.nan), errors='coerce')
                    
                    min_date = df_logs['Date'].min().date() 
                    max_date = df_logs['Date'].max().date()
                    
                    st.markdown("##### Performance Evaluation Horizon Filter")
                    d_col1, d_col2 = st.columns(2)
                    with d_col1: 
                        start_date = st.date_input("Start Threshold Date", min_date, key="start_d1")
                    with d_col2: 
                        end_date = st.date_input("End Threshold Date", max_date, key="end_d1")
                    
                    mask = (df_logs['Date'].dt.date >= start_date) & (df_logs['Date'].dt.date <= end_date)
                    df_filtered = df_logs.loc[mask]
                    
                    q_col1, q_col2 = st.columns(2)
                    with q_col1:
                        st.markdown("#### Performance Recovery Rate Deviation Trend")
                        if len(df_filtered) > 1:
                            rec_chart = alt.Chart(df_filtered).mark_circle().encode(x=alt.X('Date:T', title="Evaluation Timeline"), y=alt.Y('Recovery (%):Q', scale=alt.Scale(zero=False)))
                            st.altair_chart(rec_chart + rec_chart.transform_regression('Date', 'Recovery (%)').mark_line(color='red'), use_container_width=True)
                    with q_col2:
                        st.markdown("#### Seawater Coefficient Degradation Rate (HTC)")
                        if len(df_filtered) > 1:
                            htc_chart = alt.Chart(df_filtered).mark_line(point=True, color='orange').encode(x=alt.X('Date:T', title="Evaluation Timeline"), y=alt.Y('Overall_HTC_Val:Q', scale=alt.Scale(zero=False), title="Overall HTC (W/m²K)"))
                            st.altair_chart(htc_chart + htc_chart.transform_regression('Date', 'Overall_HTC_Val').mark_line(color='black'), use_container_width=True)

                    st.divider()
                    
                    q_col3, q_col4 = st.columns(2)
                    with q_col3:
                        st.markdown("#### Actual Mass Output vs Normalized Twin Output")
                        if len(df_filtered) > 1:
                            fold_df = df_filtered[['Date', 'Actual Production', 'Predicted Production']].melt('Date', var_name='Metric', value_name='Mass Flow Volume (m³/h)')
                            prod_chart = alt.Chart(fold_df).mark_line(point=True).encode(
                                x=alt.X('Date:T', title="Evaluation Timeline"), y=alt.Y('Mass Flow Volume (m³/h):Q', scale=alt.Scale(zero=False)),
                                color=alt.Color('Metric:N', scale=alt.Scale(domain=['Actual Production', 'Predicted Production'], range=['#1f77b4', '#ff7f0e'])),
                                strokeDash=alt.condition(alt.datum.Metric == 'Predicted Production', alt.value([5, 5]), alt.value([0])),
                                tooltip=['Date:T', 'Metric', 'Mass Flow Volume (m³/h)']
                            )
                            st.altair_chart(prod_chart, use_container_width=True)
                    with q_col4:
                        st.markdown("#### Specific Unit Thermal Efficiency GOR Performance")
                        if len(df_filtered) > 1:
                            gor_chart = alt.Chart(df_filtered).mark_line(point=True, color='green').encode(
                                x=alt.X('Date:T', title="Evaluation Timeline"), y=alt.Y('GOR_Val:Q', scale=alt.Scale(zero=False), title="Gain Output Ratio"),
                                tooltip=['Date:T', 'GOR_Val']
                            )
                            st.altair_chart(gor_chart + gor_chart.transform_regression('Date', 'GOR_Val').mark_line(color='red', strokeDash=[5, 5]), use_container_width=True)

                    st.divider()

                    st.markdown("#### Specific Thermal Energy Consumption (STEC) Trend")
                    df_stec = df_filtered.dropna(subset=['STEC_Val'])
                    if len(df_stec) > 1:
                        stec_chart = alt.Chart(df_stec).mark_line(point=True, color='purple').encode(
                            x=alt.X('Date:T', title="Evaluation Timeline"), y=alt.Y('STEC_Val:Q', scale=alt.Scale(zero=False), title="STEC (kWh/ton)"),
                            tooltip=['Date:T', 'STEC_Val']
                        )
                        st.altair_chart(stec_chart + stec_chart.transform_regression('Date', 'STEC_Val').mark_line(color='black', strokeDash=[5, 5]), use_container_width=True)
                    else:
                        st.info("No STEC data available yet for the selected range. Rows saved before this update won't have a stored STEC value.")
                else:
                    st.info("No valid dates found in registry to draw charts.")

        with rep_tabs[3]:
            st.markdown("#### Multivariable Cross-Correlation Explorer")
            if not st.session_state.daily_logs.empty:
                exp_df = st.session_state.daily_logs.copy()
                
                exp_df['Date'] = standardize_dates(exp_df['Date'])
                exp_df = exp_df.dropna(subset=['Date'])
                
                if not exp_df.empty:
                    min_date2 = exp_df['Date'].min().date() 
                    max_date2 = exp_df['Date'].max().date()
                    
                    d_col1, d_col2 = st.columns(2)
                    with d_col1: 
                        start_date2 = st.date_input("Start Horizon Date", min_date2, key="start_d2")
                    with d_col2: 
                        end_date2 = st.date_input("End Horizon Date", max_date2, key="end_d2")
                    
                    mask2 = (exp_df['Date'].dt.date >= start_date2) & (exp_df['Date'].dt.date <= end_date2)
                    exp_df = exp_df.loc[mask2]
                    
                    num_cols = [col for col in exp_df.columns if col not in ['Date']]
                    x_c, y_c, t_c = st.columns(3)
                    with x_c: 
                        exp_x = st.selectbox("Select Independent Domain X-Axis", ['Date'] + num_cols, index=0)
                    with y_c: 
                        exp_y = st.selectbox("Select Dependent Variable Y-Axis", num_cols, index=0)
                    with t_c: 
                        exp_type = st.selectbox("Select Functional Chart Variant", ["Line Chart", "Scatter Plot", "Bar Chart"])
                    
                    if exp_type == "Line Chart": 
                        chart = alt.Chart(exp_df).mark_line(point=True).encode(x=alt.X(f"{exp_x}{':T' if exp_x == 'Date' else ':Q'}"), y=alt.Y(f"{exp_y}:Q", scale=alt.Scale(zero=False)), tooltip=[exp_x, exp_y])
                    elif exp_type == "Scatter Plot": 
                        chart = alt.Chart(exp_df).mark_circle(size=80).encode(x=alt.X(f"{exp_x}{':T' if exp_x == 'Date' else ':Q'}"), y=alt.Y(f"{exp_y}:Q", scale=alt.Scale(zero=False)), tooltip=[exp_x, exp_y])
                    else: 
                        chart = alt.Chart(exp_df).mark_bar().encode(x=alt.X(f"{exp_x}{':T' if exp_x == 'Date' else ':N'}"), y=alt.Y(f"{exp_y}:Q"), tooltip=[exp_x, exp_y])
                    st.altair_chart(chart.interactive(), use_container_width=True)
                else:
                    st.info("No active historical registry values detected to perform correlation modeling.")

    # --- TAB 7: AI MODEL SELECTOR ---
    with tabs[7]:
        st.subheader("Machine Learning & OLS Calibration Suite")
        if not SKLEARN_INSTALLED:
            st.error("Mathematical package 'scikit-learn' is missing from file dependencies.")
        else:
            from sklearn.linear_model import LinearRegression
            from sklearn.ensemble import RandomForestRegressor
            from sklearn.metrics import r2_score
            
            st.warning("Ephemeral Server Parameter Caution: Since this tracking node runs on temporary testing cloud containers, manual machine-learning logic selection targets revert back to historical OLS baseline models after inactive shutdown flags are generated. Selection options remain permanently hardlocked upon local internal node integration.")
            st.markdown("### Manage Baseline Evaluation Multipliers")
            st.markdown(f"**Current Evaluator Logic Subroutine:** `{model_type}`")
            c_reset, _ = st.columns([1, 1])
            with c_reset:
                if st.button("Execute Subroutine Calibration Factory Reset", use_container_width=True):
                    st.session_state.mra_coef = MRA_COEF_2014.copy()
                    save_config(db_conn, st.session_state.mra_coef, LOCAL_CONFIG_FILE)
                    st.success("Baseline parameters successfully reverted back to original OLS multipliers!")
                    time.sleep(1.5)
                    st.rerun()

            st.divider()
            st.markdown("### Multi-Variable Predictive Optimization Logic Model Builder")
            st.markdown("Upload plant calibration verification matrices to evaluate structural variations between standard linear regression loops and active tree configurations.")
            
            req_cols = ["Date", "Gross production", "1st effect vapour pressure", "1st Effect Vapour Temp", "Sea Water Upper", "1st effect brine temp", "Brine Water Return", "LP Steam consumption", "Anti_PPM"]
            template_df = pd.DataFrame(columns=req_cols)
            st.download_button(label="Download Standard Structural Training Template File", data=template_df.to_csv(index=False).encode('utf-8'), file_name='MED4_ML_CalibrationTemplate.csv', mime='text/csv')
            
            st.divider()
            uploaded_file = st.file_uploader("Inject Completed Optimization Dataset", type=["csv"], key="mra_trainer")
            
            if uploaded_file is not None:
                try:
                    df_train = pd.read_csv(uploaded_file)
                    if not all(col in df_train.columns for col in req_cols): 
                        st.error(f"Structural training template verification failed due to parameter column omissions.")
                    else:
                        for col in req_cols:
                            if col != "Date":
                                if df_train[col].dtype == object: 
                                    df_train[col] = pd.to_numeric(df_train[col].astype(str).str.replace(',', '', regex=False), errors='coerce')
                        
                        df_train = df_train.dropna(subset=[c for c in req_cols if c != "Date"])
                        st.success(f"Training Initialized successfully utilizing {len(df_train)} localized validation rows.")
                        
                        if len(df_train) > 0:
                            X = df_train[["1st effect vapour pressure", "1st Effect Vapour Temp", "Sea Water Upper", "1st effect brine temp", "Brine Water Return", "LP Steam consumption", "Anti_PPM"]]
                            Y = df_train["Gross production"]
                            
                            model_ols = LinearRegression(fit_intercept=True).fit(X, Y)
                            r2_ols = r2_score(Y, model_ols.predict(X))
                            
                            model_rf = RandomForestRegressor(n_estimators=100, random_state=42).fit(X, Y)
                            r2_rf = r2_score(Y, model_rf.predict(X))
                            
                            if XGB_INSTALLED:
                                import xgboost as xgb
                                model_xgb = xgb.XGBRegressor(n_estimators=100, random_state=42).fit(X, Y)
                                r2_xgb = r2_score(Y, model_xgb.predict(X))
                            
                            st.markdown("### Algorithm Accuracy Evaluation Matrix")
                            m1, m2, m3 = st.columns(3)
                            m1.metric("1. Linear OLS Fit (R² Coefficient)", f"{r2_ols * 100:.2f}%")
                            m2.metric("2. Random Forest Tree Logic (R²)", f"{r2_rf * 100:.2f}%")
                            if XGB_INSTALLED: 
                                m3.metric("3. Extreme Gradient Boost XGB (R²)", f"{r2_xgb * 100:.2f}%")
                            else: 
                                m3.warning("Advanced Gradient boosting library dependency not activated.")
                            
                            st.markdown("#### Dynamic Feature Sensitivity Weights / Scaling Coefficients")
                            comp_dict = {
                                "Parameter": ["Press_1st", "Temp_1st", "SW_Upper", "Brine_Temp_1st", "Brine_Flow", "LP_Steam", "Anti_PPM"],
                                "OLS (Coefficients)": np.round(model_ols.coef_, 4),
                                "Random Forest (Importance %)": np.round(model_rf.feature_importances_ * 100, 2)
                            }
                            if XGB_INSTALLED: 
                                comp_dict["XGBoost (Importance %)"] = np.round(model_xgb.feature_importances_ * 100, 2)
                            
                            st.dataframe(pd.DataFrame(comp_dict).style.format(precision=4), use_container_width=True, hide_index=True)
                            
                            st.markdown("### Commit & Lock Mathematical Subroutine Target")
                            opts = ["OLS (Linear)", "Random Forest"]
                            if XGB_INSTALLED: 
                                opts.append("XGBoost")
                                
                            selected_model = st.radio("Configure Active Live Prediction Logic Block:", opts)
                            
                            if st.button("Confirm and Hardlock Active Operational Subroutine", type="primary", use_container_width=True):
                                if selected_model == "OLS (Linear)":
                                    new_coefs = {
                                        "model_type": "OLS", "Intercept": float(model_ols.intercept_),
                                        "Press_1st": float(model_ols.coef_[0]), "Temp_1st": float(model_ols.coef_[1]), 
                                        "SW_Upper": float(model_ols.coef_[2]), "Brine_Temp_1st": float(model_ols.coef_[3]), 
                                        "Brine_Flow": float(model_ols.coef_[4]), "LP_Steam": float(model_ols.coef_[5]), 
                                        "Anti_PPM": float(model_ols.coef_[6])
                                    }
                                    st.session_state.mra_coef = new_coefs
                                    save_config(db_conn, new_coefs, LOCAL_CONFIG_FILE)
                                else:
                                    target_m = model_rf if selected_model == "Random Forest" else model_xgb
                                    joblib.dump(target_m, AI_MODEL_FILE)
                                    ai_coefs = {
                                        "model_type": selected_model,
                                        "Press_1st": float(target_m.feature_importances_[0]), "Temp_1st": float(target_m.feature_importances_[1]), 
                                        "SW_Upper": float(target_m.feature_importances_[2]), "Brine_Temp_1st": float(target_m.feature_importances_[3]), 
                                        "Brine_Flow": float(target_m.feature_importances_[4]), "LP_Steam": float(target_m.feature_importances_[5]), 
                                        "Anti_PPM": float(target_m.feature_importances_[6])
                                    }
                                    st.session_state.mra_coef = ai_coefs
                                    save_config(db_conn, ai_coefs, LOCAL_CONFIG_FILE)
                                    
                                st.success(f"System evaluation subroutine locked into {selected_model} logic sequence.")
                                time.sleep(1.5)
                                st.rerun()
                        else: 
                            st.error("Structural data parsing produced empty float ranges inside parameters.")
                except Exception as e: 
                    st.error(f"Structural data matrix crash: {e}")

    # --- TAB 8: BULK EXCEL UPLOADER PANEL ---
    with tabs[8]:
        st.subheader("Bulk Data Upload")
        st.caption(
            "Each uploader mirrors ONE tab of the plant workbook exactly. Upload only the INPUT columns - "
            "every derived value (LMTD, HTC, GOR, STEC, Recovery, Fouling) is recomputed by the calculator "
            "and any such column in your file is ignored. Uploads are merged by date: loading HTC data never "
            "overwrites Operational or Water Quality data for the same day, and vice versa."
        )

        def _clean_num(df, cols):
            """Coerce to numeric. The plant sheets use '-' for 'not measured'; that must become NaN
            (genuinely missing), never 0, because a 0 temperature would silently corrupt an LMTD."""
            for c in cols:
                if c not in df.columns:
                    df[c] = np.nan
                df[c] = pd.to_numeric(
                    df[c].astype(str).str.replace(',', '', regex=False).str.strip().replace({'-': np.nan, '': np.nan}),
                    errors='coerce'
                )
            return df

        def _lmtd(dt1, dt2):
            """LMTD = (dT1 - dT2) / ln(dT1/dT2), exactly as in both HTC sheets (col N).
            Valid only when both driving forces are present and positive. Note dT2 > dT1 in this
            plant's data, which the formula handles fine (both numerator and log go negative)."""
            valid = dt1.notna() & dt2.notna() & (dt1 > 0) & (dt2 > 0) & (dt1 != dt2)
            ratio = np.where(valid, dt1 / dt2, 1.0)
            logr = np.log(np.where(ratio > 0, ratio, 1.0))
            return np.where(valid & (logr != 0), (dt1 - dt2) / logr, np.nan)

        def _backfill_from_db(d, mapping):
            """For any HTC input the uploaded file doesn't supply, fall back to the value already
            stored in the master registry for that same date (typically loaded by the Operational
            upload, which shares most of these readings). HTC is only left blank when a value is
            available NOWHERE - not merely because it was absent from this one file.
            mapping: {htc_column_in_this_file: master_registry_column}"""
            logs = st.session_state.daily_logs
            if logs is None or logs.empty or 'Date' not in logs.columns:
                return d, []
            ref = logs.copy()
            ref['Date'] = standardize_dates(ref['Date']).dt.strftime('%Y-%m-%d')
            ref = ref.dropna(subset=['Date']).drop_duplicates(subset=['Date'], keep='last').set_index('Date')
            filled = []
            for htc_col, db_col in mapping.items():
                if db_col not in ref.columns:
                    continue
                src = pd.to_numeric(d['Date'].map(ref[db_col]), errors='coerce')
                if htc_col not in d.columns:
                    d[htc_col] = np.nan
                n_before = int(d[htc_col].isna().sum())
                d[htc_col] = d[htc_col].fillna(src)
                n_after = int(d[htc_col].isna().sum())
                if n_before > n_after:
                    filled.append(f"{htc_col.split('_', 1)[-1].replace('_', ' ')} ({n_before - n_after})")
            return d, filled

        bulk_subtabs = st.tabs([
            "A) Operational Data", "B) 1st Effect HTC", "C) Overall HTC", "D) Water Quality"
        ])

        # ===================================================================================
        # A) OPERATIONAL DATA  <-  'Operational data' sheet
        # ===================================================================================
        with bulk_subtabs[0]:
            st.markdown(
                "Source: **`Operational data`** tab. Upload the sheet as-is. The calculator recomputes "
                "**Recovery**, **Conversion**, **GOR**, **Steam Economy**, **Overall Delta T** and **STEC** "
                "from the raw readings - the versions of those columns already in your sheet are ignored."
            )
            st.download_button(
                "Download Operational Template", key='dl_op',
                data=pd.DataFrame(columns=OPERATIONAL_BULK_HEADERS).to_csv(index=False).encode('utf-8'),
                file_name='MED4_Operational_Template.csv', mime='text/csv'
            )
            st.divider()
            op_file = st.file_uploader("Upload Operational Data CSV", type=["csv"], key="op_up")

            if op_file is not None:
                try:
                    d = pd.read_csv(op_file)
                    if 'Parameter' in d.columns:
                        d = d[~d['Parameter'].astype(str).isin(['Design', 'Unit', 'TAG', 'SOR/  Base line'])]
                    d.rename(columns={
                        'Parameter': 'Date',
                        'Sea water Upper': 'Sea Water Upper', 'Sea water Lower': 'Sea Water Lower',
                        'Sea water feed': 'Sea Water Feed', 'Brine return': 'Brine Water Return',
                        ' Desal Production': 'Desal production', 'Desal Production': 'Desal production',
                        'LP Steam Consumption': 'LP Steam consumption',
                        'Condensate return': 'Condensate Return', 'Condensate Temp': 'condensate temp',
                        "1'st effect vapour Temp": '1st Effect Vapour Temp',
                        '1st Effect Brine Temp': '1st effect brine temp',
                        '1st Effect Vapour pres': '1st effect vapour pressure',
                        'Steam Inlet Temp': 'Steam Inlet Temp',
                        'Brine DischargeTemp': 'Brine Discharge Temp',
                        'Sea water cond (FFC) I/L temp': 'Sea Water cond I/L temp',
                        'Sea water cond (FFC) o/L temp': 'Sea Water Condenser O/L Temp',
                        'CW (FCC) supply': 'CW supply', 'CW (FCC) return': 'CW Return',
                        'Gross desal water production': 'Gross production',
                        '11 effect brine Temp': '11th Effect Brine Temp',
                        'Antiscalant residual (Cold group)': 'Anti_PPM',
                        'Antiscalant residual': 'Anti_PPM',
                        'Antiscalant residual (Hot group)': 'Anti_PPM_Hot',
                        'Antiscalant residual (Brine)': 'Anti_PPM_Brine',
                        'Unnamed: 27': 'Anti_PPM_Hot', 'Unnamed: 28': 'Anti_PPM_Brine',
                        'Remarks': 'Remarks', 'REMARKS': 'Remarks',
                    }, inplace=True)

                    op_inputs = [
                        'Sea Water Upper', 'Sea Water Lower', 'Sea Water Feed', 'Brine Water Return',
                        'Desal production', 'LP Steam consumption', 'Condensate Return', 'condensate temp',
                        '1st Effect Vapour Temp', '1st effect brine temp', '1st effect vapour pressure',
                        'Steam Inlet Temp', 'Brine Discharge Temp', 'Sea Water cond I/L temp',
                        'Sea Water Condenser O/L Temp', 'CW supply', 'CW Return', 'Gross production',
                        '11th Effect Brine Temp', 'Anti_PPM', 'Anti_PPM_Hot', 'Anti_PPM_Brine',
                    ]
                    d = _clean_num(d, op_inputs)
                    d['Date'] = standardize_dates(d['Date']).dt.strftime('%Y-%m-%d')
                    d = d.dropna(subset=['Date'])

                    if len(d) == 0:
                        st.error("No valid dated rows found.")
                    else:
                        steam = d['LP Steam consumption']
                        gross = d['Gross production']
                        desal = d['Desal production']
                        swfeed = d['Sea Water Feed']

                        # Derived - recomputed, never trusted from the file.
                        d['Delta T'] = d['1st Effect Vapour Temp'] - d['1st effect brine temp']
                        d['Overall Delta T'] = d['1st Effect Vapour Temp'] - d['11th Effect Brine Temp']
                        d['GOR'] = np.where(steam > 0, gross / steam, np.nan)
                        d['Recovery'] = np.where(swfeed > 0, (gross / swfeed) * 100, np.nan)
                        d['Conversion'] = d['Recovery'] / 100
                        d['Steam Economy'] = np.where(desal > 0, steam / desal, np.nan)
                        d['STEC'] = np.where(
                            desal > 0, ((steam * 1000) / 3600 * LATENT_HEAT_STEAM_KJ_KG) / desal, np.nan
                        )

                        out_cols = ['Date'] + op_inputs + [
                            'Delta T', 'Overall Delta T', 'GOR', 'Recovery', 'Conversion',
                            'Steam Economy', 'STEC'
                        ]
                        ready = d[out_cols].copy()
                        ready['Remarks'] = d.get('Remarks', pd.Series("", index=d.index)).fillna("")

                        st.success(f"Recomputed operational KPIs for {len(ready)} rows.")
                        st.dataframe(ready.style.format(precision=2), use_container_width=True, hide_index=True)

                        cp, cs = st.columns([2, 2])
                        pw = cp.text_input("Password", type="password", key="pw_op",
                                           label_visibility="collapsed", placeholder="Master password to sync")
                        if cs.button("Update Database (Operational)", use_container_width=True, key="b_op"):
                            if pw == "12345678":
                                st.session_state.daily_logs = upsert_daily_logs(st.session_state.daily_logs, ready)
                                save_database(db_conn, st.session_state.daily_logs, LOCAL_DB_FILE)
                                st.success("Operational data synced. HTC and Water Quality untouched.")
                                time.sleep(1.2); st.rerun()
                            elif pw != "":
                                st.error("Incorrect password.")
                except Exception as e:
                    st.error(f"Error processing file: {e}")

        # ===================================================================================
        # B) 1st EFFECT HTC  <-  '1st effect-HTC' sheet
        # ===================================================================================
        with bulk_subtabs[1]:
            st.markdown(
                "Source: **`1st effect-HTC`** tab. Upload only columns **A-K** (the process inputs). "
                "The calculator recomputes ΔT1, ΔT2, LMTD, Heat Duty, HTC and Fouling."
            )
            st.info(
                "**Column meanings on this sheet** (they differ from the Overall-HTC sheet):\n\n"
                "- **Feed flow** = feed to the 1st effect (~514 m³/hr), tag Z711FIT424\n"
                "- **Feed Temp** = *average brine temp of effects 4, 5, 6 and 7* (~49 °C) - this is the cold-side reference, not a seawater temp\n"
                "- **Brine Temp** = 1st effect brine temp (~66 °C), tag Z711TIT401\n"
                "- **1st effect vapor temp** = ~69 °C, tag Z711TIT414\n"
                "- **Condensate temperature** = ~75 °C, tag Z711TIT415\n"
                "- **Heat Transfer Area** = 12,950 m² (leave blank to use this default)\n\n"
                "ΔT1 = vapor − brine · ΔT2 = condensate − Feed Temp(eff 4-7)"
            )
            st.download_button(
                "Download 1st Effect HTC Template", key='dl_h1',
                data=pd.DataFrame(columns=HTC_1ST_BULK_HEADERS).to_csv(index=False).encode('utf-8'),
                file_name='MED4_1stEffect_HTC_Template.csv', mime='text/csv'
            )
            st.divider()
            h1_file = st.file_uploader("Upload 1st Effect HTC CSV", type=["csv"], key="h1_up")

            if h1_file is not None:
                try:
                    d = pd.read_csv(h1_file)
                    first = d.columns[0]
                    d = d[~d[first].astype(str).isin(['Unit ', 'Unit', 'Tag', 'Desigen', 'Design', 'SOR/  Base line'])]
                    d.rename(columns={
                        first: 'Date',
                        'Feed flow': 'HTC1_Feed_Flow', 'Product flow ': 'HTC1_Product_Flow',
                        'Product flow': 'HTC1_Product_Flow',
                        'Condensate Flow ': 'HTC1_Cond_Flow', 'Condensate Flow': 'HTC1_Cond_Flow',
                        'Steam consumption rate': 'HTC1_Steam_TPH',
                        'Feed Temp': 'HTC1_Feed_Temp_Eff4to7',
                        'Brine Temp': 'HTC1_Brine_Temp',
                        '1st effect vapor temp': 'HTC1_Vapor_Temp',
                        ' Condensate temperature': 'HTC1_Cond_Temp',
                        'Condensate temperature': 'HTC1_Cond_Temp',
                        'Heat Transfer Area ': 'HTC1_Area', 'Heat Transfer Area': 'HTC1_Area',
                    }, inplace=True)

                    h1_inputs = ['HTC1_Feed_Flow', 'HTC1_Product_Flow', 'HTC1_Cond_Flow', 'HTC1_Steam_TPH',
                                 'HTC1_Feed_Temp_Eff4to7', 'HTC1_Brine_Temp', 'HTC1_Vapor_Temp',
                                 'HTC1_Cond_Temp', 'HTC1_Area']
                    d = _clean_num(d, h1_inputs)
                    d['Date'] = standardize_dates(d['Date']).dt.strftime('%Y-%m-%d')
                    d = d.dropna(subset=['Date'])

                    # Pull anything this file didn't supply from the registry (Operational upload shares
                    # steam rate, vapour/brine/condensate temps, product and condensate flows).
                    d, filled = _backfill_from_db(d, {
                        'HTC1_Steam_TPH': 'LP Steam consumption',
                        'HTC1_Vapor_Temp': '1st Effect Vapour Temp',
                        'HTC1_Brine_Temp': '1st effect brine temp',
                        'HTC1_Cond_Temp': 'condensate temp',
                        'HTC1_Product_Flow': 'Desal production',
                        'HTC1_Cond_Flow': 'Condensate Return',
                        'HTC1_Feed_Temp_Eff4to7': 'HTC1_Feed_Temp_Eff4to7',
                        'HTC1_Feed_Flow': 'HTC1_Feed_Flow',
                    })
                    if filled:
                        st.info("Filled from existing registry data: " + ", ".join(filled))

                    if len(d) == 0:
                        st.error("No valid dated rows found.")
                    else:
                        d['HTC1_Area'] = d['HTC1_Area'].fillna(HTC_1ST_AREA)

                        # ΔT1 = 1st effect vapor temp - 1st effect brine temp   (sheet col L)
                        # ΔT2 = condensate temp - avg brine temp of effects 4-7 (sheet col M)
                        d['HTC1_dT1'] = d['HTC1_Vapor_Temp'] - d['HTC1_Brine_Temp']
                        d['HTC1_dT2'] = d['HTC1_Cond_Temp'] - d['HTC1_Feed_Temp_Eff4to7']
                        d['HTC1_LMTD'] = _lmtd(d['HTC1_dT1'], d['HTC1_dT2'])

                        # Steam-condensation heat duty (sheet cols V,W,X):
                        # ms(kg/hr) = TPH*1000 ; W(kJ/hr) = ms*lambda ; Q(W) = W*1000/3600
                        d['HTC1_Q_Steam'] = (d['HTC1_Steam_TPH'] * 1000 * LATENT_HEAT_STEAM_KJ_KG * 1000) / 3600

                        # U (steam condensation basis) = Q / (A * LMTD)   (sheet col AA)
                        denom = d['HTC1_Area'] * d['HTC1_LMTD']
                        d['1st Effect HTC'] = np.where(
                            d['HTC1_Q_Steam'].notna() & pd.notna(denom) & (denom > 0),
                            d['HTC1_Q_Steam'] / denom, np.nan
                        )
                        d['HTC1_Fouling'] = np.where(d['1st Effect HTC'] > 0, 1 / d['1st Effect HTC'], np.nan)
                        # Rf = 1/U_actual - 1/U_SOR_baseline   (sheet col AC)
                        d['HTC1_Rf'] = np.where(
                            d['1st Effect HTC'] > 0,
                            (1 / d['1st Effect HTC']) - (1 / HTC_1ST_U_SOR), np.nan
                        )
                        d['Area_1st'] = d['HTC1_Area']

                        ready = d[['Date'] + h1_inputs + [
                            'HTC1_dT1', 'HTC1_dT2', 'HTC1_LMTD', 'HTC1_Q_Steam',
                            '1st Effect HTC', 'HTC1_Fouling', 'HTC1_Rf', 'Area_1st'
                        ]].copy()

                        n_bad = int(ready['1st Effect HTC'].isna().sum())
                        st.success(f"Computed 1st Effect HTC for {len(ready) - n_bad} of {len(ready)} rows.")
                        if n_bad:
                            st.warning(f"{n_bad} row(s) left blank - missing one of: steam rate, vapor temp, "
                                       f"brine temp, condensate temp, or Feed Temp (eff 4-7).")
                        st.dataframe(ready.style.format(precision=2), use_container_width=True, hide_index=True)

                        cp, cs = st.columns([2, 2])
                        pw = cp.text_input("Password", type="password", key="pw_h1",
                                           label_visibility="collapsed", placeholder="Master password to sync")
                        if cs.button("Update Database (1st Effect HTC)", use_container_width=True, key="b_h1"):
                            if pw == "12345678":
                                st.session_state.daily_logs = upsert_daily_logs(st.session_state.daily_logs, ready)
                                save_database(db_conn, st.session_state.daily_logs, LOCAL_DB_FILE)
                                st.success("1st Effect HTC synced. Operational, Overall HTC and Water Quality untouched.")
                                time.sleep(1.2); st.rerun()
                            elif pw != "":
                                st.error("Incorrect password.")
                except Exception as e:
                    st.error(f"Error processing file: {e}")

        # ===================================================================================
        # C) OVERALL HTC  <-  'Overall-HTC ' sheet
        # ===================================================================================
        with bulk_subtabs[2]:
            st.markdown(
                "Source: **`Overall-HTC`** tab. Upload only columns **A-K** (the process inputs). "
                "The calculator recomputes ΔT1, ΔT2, LMTD, Heat Duty, HTC and Fouling."
            )
            st.info(
                "**Column meanings on this sheet** (they differ from the 1st-effect sheet):\n\n"
                "- **Feed flow** = *total* seawater feed (~2062 m³/hr), tag Z711FIT424\n"
                "- **Feed Temp** = *feed temp to the cold group* (~40 °C) - the cold-side reference\n"
                "- **Brine discharge Temp** = ~42 °C, tag Z711TIT401\n"
                "- **1st effect vapor temp** = ~69 °C, tag Z711TIT414\n"
                "- **Condensate temperature** = ~75 °C\n"
                "- **Heat Transfer Area** = 163,818 m² (11 × 12,950 × 1.15; leave blank to use this default)\n\n"
                "ΔT1 = vapor − brine discharge · ΔT2 = condensate − Feed Temp(cold group)"
            )
            st.download_button(
                "Download Overall HTC Template", key='dl_ho',
                data=pd.DataFrame(columns=HTC_OVERALL_BULK_HEADERS).to_csv(index=False).encode('utf-8'),
                file_name='MED4_Overall_HTC_Template.csv', mime='text/csv'
            )
            st.divider()
            ho_file = st.file_uploader("Upload Overall HTC CSV", type=["csv"], key="ho_up")

            if ho_file is not None:
                try:
                    d = pd.read_csv(ho_file)
                    first = d.columns[0]
                    d = d[~d[first].astype(str).isin(['Unit ', 'Unit', 'Tag', 'Desigen', 'Design', 'SOR/  Base line'])]
                    d.rename(columns={
                        first: 'Date',
                        'Feed flow': 'HTCO_Feed_Flow', 'Product flow ': 'HTCO_Product_Flow',
                        'Product flow': 'HTCO_Product_Flow',
                        'Condensate Flow ': 'HTCO_Cond_Flow', 'Condensate Flow': 'HTCO_Cond_Flow',
                        'Steam consumption rate': 'HTCO_Steam_TPH',
                        'Feed Temp': 'HTCO_Feed_Temp_ColdGrp',
                        'Brine discharge Temp': 'HTCO_Brine_Disch_Temp',
                        'Brine Discharge Temp': 'HTCO_Brine_Disch_Temp',
                        '1st effect vapor temp': 'HTCO_Vapor_Temp',
                        ' Condensate temperature': 'HTCO_Cond_Temp',
                        'Condensate temperature': 'HTCO_Cond_Temp',
                    }, inplace=True)
                    # Area column header on this sheet carries the formula in its name, so match by prefix.
                    for c in list(d.columns):
                        if str(c).strip().startswith('Heat Transfer Area'):
                            d.rename(columns={c: 'HTCO_Area'}, inplace=True)

                    ho_inputs = ['HTCO_Feed_Flow', 'HTCO_Product_Flow', 'HTCO_Cond_Flow', 'HTCO_Steam_TPH',
                                 'HTCO_Feed_Temp_ColdGrp', 'HTCO_Brine_Disch_Temp', 'HTCO_Vapor_Temp',
                                 'HTCO_Cond_Temp', 'HTCO_Area']
                    d = _clean_num(d, ho_inputs)
                    d['Date'] = standardize_dates(d['Date']).dt.strftime('%Y-%m-%d')
                    d = d.dropna(subset=['Date'])

                    # Pull anything this file didn't supply from the registry (Operational upload shares
                    # steam rate, vapour/condensate temps, brine discharge temp, seawater feed, flows).
                    d, filled = _backfill_from_db(d, {
                        'HTCO_Steam_TPH': 'LP Steam consumption',
                        'HTCO_Vapor_Temp': '1st Effect Vapour Temp',
                        'HTCO_Brine_Disch_Temp': 'Brine Discharge Temp',
                        'HTCO_Cond_Temp': 'condensate temp',
                        'HTCO_Feed_Flow': 'Sea Water Feed',
                        'HTCO_Product_Flow': 'Desal production',
                        'HTCO_Cond_Flow': 'Condensate Return',
                        'HTCO_Feed_Temp_ColdGrp': 'HTCO_Feed_Temp_ColdGrp',
                    })
                    if filled:
                        st.info("Filled from existing registry data: " + ", ".join(filled))

                    if len(d) == 0:
                        st.error("No valid dated rows found.")
                    else:
                        d['HTCO_Area'] = d['HTCO_Area'].fillna(HTC_OVERALL_AREA)

                        # ΔT1 = 1st effect vapor temp - brine discharge temp     (sheet col L)
                        # ΔT2 = condensate temp - feed temp to cold group        (sheet col M)
                        d['HTCO_dT1'] = d['HTCO_Vapor_Temp'] - d['HTCO_Brine_Disch_Temp']
                        d['HTCO_dT2'] = d['HTCO_Cond_Temp'] - d['HTCO_Feed_Temp_ColdGrp']
                        d['HTCO_LMTD'] = _lmtd(d['HTCO_dT1'], d['HTCO_dT2'])

                        d['HTCO_Q_Steam'] = (d['HTCO_Steam_TPH'] * 1000 * LATENT_HEAT_STEAM_KJ_KG * 1000) / 3600

                        denom = d['HTCO_Area'] * d['HTCO_LMTD']
                        d['Overall HTC'] = np.where(
                            d['HTCO_Q_Steam'].notna() & pd.notna(denom) & (denom > 0),
                            d['HTCO_Q_Steam'] / denom, np.nan
                        )
                        d['HTCO_Fouling'] = np.where(d['Overall HTC'] > 0, 1 / d['Overall HTC'], np.nan)
                        d['HTCO_Rf'] = np.where(
                            d['Overall HTC'] > 0,
                            (1 / d['Overall HTC']) - (1 / HTC_OVERALL_U_SOR), np.nan
                        )
                        d['Area_Overall'] = d['HTCO_Area']

                        ready = d[['Date'] + ho_inputs + [
                            'HTCO_dT1', 'HTCO_dT2', 'HTCO_LMTD', 'HTCO_Q_Steam',
                            'Overall HTC', 'HTCO_Fouling', 'HTCO_Rf', 'Area_Overall'
                        ]].copy()

                        n_bad = int(ready['Overall HTC'].isna().sum())
                        st.success(f"Computed Overall HTC for {len(ready) - n_bad} of {len(ready)} rows.")
                        if n_bad:
                            st.warning(f"{n_bad} row(s) left blank - missing one of: steam rate, vapor temp, "
                                       f"brine discharge temp, condensate temp, or Feed Temp (cold group).")
                        st.dataframe(ready.style.format(precision=2), use_container_width=True, hide_index=True)

                        cp, cs = st.columns([2, 2])
                        pw = cp.text_input("Password", type="password", key="pw_ho",
                                           label_visibility="collapsed", placeholder="Master password to sync")
                        if cs.button("Update Database (Overall HTC)", use_container_width=True, key="b_ho"):
                            if pw == "12345678":
                                st.session_state.daily_logs = upsert_daily_logs(st.session_state.daily_logs, ready)
                                save_database(db_conn, st.session_state.daily_logs, LOCAL_DB_FILE)
                                st.success("Overall HTC synced. Operational, 1st Effect HTC and Water Quality untouched.")
                                time.sleep(1.2); st.rerun()
                            elif pw != "":
                                st.error("Incorrect password.")
                except Exception as e:
                    st.error(f"Error processing file: {e}")

        # ===================================================================================
        # D) WATER QUALITY  <-  'Feed & Brine Water Analysis' + 'Desal Analysis' sheets
        # ===================================================================================
        with bulk_subtabs[3]:
            st.markdown(
                "Two separate lab sheets, uploaded independently. Both are pure lab readings - nothing is "
                "derived from them, so they are stored exactly as supplied (`-` becomes blank, not 0)."
            )

            st.markdown("##### Feed & Brine Water Analysis")
            st.download_button(
                "Download Feed & Brine Template", key='dl_fb',
                data=pd.DataFrame(columns=FEEDBRINE_BULK_HEADERS).to_csv(index=False).encode('utf-8'),
                file_name='MED4_FeedBrine_Template.csv', mime='text/csv'
            )
            fb_file = st.file_uploader("Upload Feed & Brine Analysis CSV", type=["csv"], key="fb_up")

            if fb_file is not None:
                try:
                    d = pd.read_csv(fb_file)
                    first = d.columns[0]
                    d = d[~d[first].astype(str).isin(['UOM', 'Specified Limit'])]
                    fb_map = {
                        first: 'Date',
                        'pH': 'Feed_pH', 'Turbidity': 'Feed_Turbidity', 'TSS': 'Feed_TSS',
                        'Conductivity': 'Feed_Cond', 'TDS': 'Feed_TDS',
                        'Total Alkalinity': 'Feed_Alkalinity', 'Calcium Hardness': 'Feed_Calcium',
                        'Mg Hardness': 'Feed_MgHardness', 'Total Hardness': 'Feed_TotalHardness',
                        'Silica': 'Feed_Silica', 'Chloride ': 'Feed_Chlorides', 'Chloride': 'Feed_Chlorides',
                        'Sulphate': 'Feed_Sulphate', 'Sulphide': 'Feed_Sulphide',
                        'Brine pH': 'Brine_pH', 'Brine Turbidity': 'Brine_Turbidity',
                        'Brine Conductivity': 'Brine_Cond', 'Brine TDS': 'Brine_TDS',
                        'Brine Total Alkalinity': 'Brine_Alkalinity',
                        'Brine Calcium Hardness': 'Brine_Calcium', 'Brine Mg Hardness': 'Brine_MgHardness',
                        'Brine Total Hardness': 'Brine_TotalHardness', 'Brine Silica': 'Brine_Silica',
                        'Brine Chloride': 'Brine_Chlorides',
                        # Raw sheet exports the brine block with duplicate names, which pandas suffixes '.1'
                        'pH.1': 'Brine_pH', 'Turbidity.1': 'Brine_Turbidity',
                        'Conductivity.1': 'Brine_Cond', 'TDS.1': 'Brine_TDS',
                        'Total Alkalinity.1': 'Brine_Alkalinity', 'Calcium Hardness.1': 'Brine_Calcium',
                        'Mg Hardness.1': 'Brine_MgHardness', 'Total Hardness.1': 'Brine_TotalHardness',
                        'Silica.1': 'Brine_Silica', 'Chloride .1': 'Brine_Chlorides',
                        'Chloride.1': 'Brine_Chlorides',
                        'REMARKS': 'Remarks',
                    }
                    d.rename(columns=fb_map, inplace=True)
                    fb_cols = [c for c in dict.fromkeys(fb_map.values()) if c not in ('Date', 'Remarks')]
                    d = _clean_num(d, fb_cols)
                    d['Date'] = standardize_dates(d['Date']).dt.strftime('%Y-%m-%d')
                    d = d.dropna(subset=['Date'])

                    if len(d) == 0:
                        st.error("No valid dated rows found.")
                    else:
                        ready = d[['Date'] + fb_cols].copy()
                        ready['Remarks'] = d.get('Remarks', pd.Series("", index=d.index)).fillna("")
                        st.success(f"Prepared Feed & Brine analysis for {len(ready)} rows.")
                        st.dataframe(ready.style.format(precision=2), use_container_width=True, hide_index=True)

                        cp, cs = st.columns([2, 2])
                        pw = cp.text_input("Password", type="password", key="pw_fb",
                                           label_visibility="collapsed", placeholder="Master password to sync")
                        if cs.button("Update Database (Feed & Brine)", use_container_width=True, key="b_fb"):
                            if pw == "12345678":
                                st.session_state.daily_logs = upsert_daily_logs(st.session_state.daily_logs, ready)
                                save_database(db_conn, st.session_state.daily_logs, LOCAL_DB_FILE)
                                st.success("Feed & Brine analysis synced.")
                                time.sleep(1.2); st.rerun()
                            elif pw != "":
                                st.error("Incorrect password.")
                except Exception as e:
                    st.error(f"Error processing Feed & Brine file: {e}")

            st.divider()
            st.markdown("##### Desal (Product) Analysis")
            st.download_button(
                "Download Desal Analysis Template", key='dl_ds',
                data=pd.DataFrame(columns=DESAL_BULK_HEADERS).to_csv(index=False).encode('utf-8'),
                file_name='MED4_Desal_Template.csv', mime='text/csv'
            )
            ds_file = st.file_uploader("Upload Desal Analysis CSV", type=["csv"], key="ds_up")

            if ds_file is not None:
                try:
                    d = pd.read_csv(ds_file)
                    first = d.columns[0]
                    d = d[~d[first].astype(str).isin(['UOM', 'Specified Limit'])]
                    ds_map = {
                        first: 'Date',
                        'pH': 'Product_pH', 'Turbidity': 'Product_Turbidity',
                        'Conductivity': 'Product_Cond', 'TDS': 'Product_TDS',
                        'Total Alkalinity': 'Product_Alkalinity', 'Calcium Hardness': 'Product_Calcium',
                        'Mg Hardness': 'Product_MgHardness', 'Total Hardness': 'Product_TotalHardness',
                        'Chloride ': 'Product_Chlorides', 'Chloride': 'Product_Chlorides',
                        'Total Iron ': 'Product_Iron', 'Total Iron': 'Product_Iron',
                        'Silica': 'Product_Silica', 'Sulphate': 'Product_Sulphate',
                        'REMARKS': 'Remarks',
                    }
                    d.rename(columns=ds_map, inplace=True)
                    ds_cols = [c for c in dict.fromkeys(ds_map.values()) if c not in ('Date', 'Remarks')]
                    d = _clean_num(d, ds_cols)
                    d['Date'] = standardize_dates(d['Date']).dt.strftime('%Y-%m-%d')
                    d = d.dropna(subset=['Date'])

                    if len(d) == 0:
                        st.error("No valid dated rows found.")
                    else:
                        ready = d[['Date'] + ds_cols].copy()
                        ready['Remarks'] = d.get('Remarks', pd.Series("", index=d.index)).fillna("")
                        st.success(f"Prepared Desal product analysis for {len(ready)} rows.")
                        st.dataframe(ready.style.format(precision=2), use_container_width=True, hide_index=True)

                        cp, cs = st.columns([2, 2])
                        pw = cp.text_input("Password", type="password", key="pw_ds",
                                           label_visibility="collapsed", placeholder="Master password to sync")
                        if cs.button("Update Database (Desal Analysis)", use_container_width=True, key="b_ds"):
                            if pw == "12345678":
                                st.session_state.daily_logs = upsert_daily_logs(st.session_state.daily_logs, ready)
                                save_database(db_conn, st.session_state.daily_logs, LOCAL_DB_FILE)
                                st.success("Desal product analysis synced.")
                                time.sleep(1.2); st.rerun()
                            elif pw != "":
                                st.error("Incorrect password.")
                except Exception as e:
                    st.error(f"Error processing Desal file: {e}")

    render_chatbot()
