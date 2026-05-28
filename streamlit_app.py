# requirements: pandas, numpy, python-docx, altair, gspread, oauth2client, scikit-learn, xgboost, joblib, Pillow
import streamlit as st
import pandas as pd
import numpy as np
import datetime
import io
import os
import json
import time
import altair as alt
import joblib
import re
import base64
from io import BytesIO
from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH

try:
    import gspread
    from oauth2client.service_account import ServiceAccountCredentials
    GSPREAD_INSTALLED = True
except ImportError:
    GSPREAD_INSTALLED = False

try:
    from sklearn.linear_model import LinearRegression
    from sklearn.ensemble import RandomForestRegressor
    from sklearn.metrics import r2_score
    SKLEARN_INSTALLED = True
except ImportError:
    SKLEARN_INSTALLED = False

try:
    import xgboost as xgb
    XGB_INSTALLED = True
except ImportError:
    XGB_INSTALLED = False

try:
    from PIL import Image
    PIL_INSTALLED = True
except ImportError:
    PIL_INSTALLED = False

st.set_page_config(page_title="Chembond Water Technologies Limited | Enterprise Hub", layout="wide")

# ==========================================
# 1. CLOUD "GHOST SHEET" & CONFIG ENGINE
# ==========================================
GOOGLE_SHEET_NAME = "MED4_Cloud_Database"
LOCAL_DB_FILE = "MED4_Master_Database.csv"
LOCAL_CONFIG_FILE = "mra_config.json"
AI_MODEL_FILE = "mra_ai_model.pkl"

RO_LOCAL_DB_FILE = "RO_Master_Database.csv"
RO_LOCAL_CONFIG_FILE = "ro_mra_config.json"
RO_AI_MODEL_FILE = "ro_mra_ai_model.pkl"

MRA_COEF_2014 = {
    "model_type": "OLS",
    "Intercept": -161.5638, "Press_1st": 0.6136, "Temp_1st": 3.6392, 
    "SW_Upper": 0.8111, "Brine_Temp_1st": -7.6638, "Brine_Flow": -0.2329, 
    "LP_Steam": 8.2539, "Steam_Temp": 2.1924, "Anti_PPM": -7.0301
}

MRA_BASELINE = {
    "Press_1st": 231.76, "Temp_1st": 68.47, "SW_Upper": 553.63, 
    "Brine_Temp_1st": 65.46, "Brine_Flow": 1275.50, "LP_Steam": 71.75, 
    "Steam_Temp": 165.54, "Anti_PPM": 4.82
}

RO_MRA_COEF_BASE = {
    "model_type": "OLS", "Intercept": 50.0, "Feed_Flow": 0.85, 
    "Feed_TDS": -0.01, "Coag_PPM": 2.5, "SMBS_PPM": 1.1
}

RO_MRA_BASELINE = {
    "Feed_Flow": 450.0, "Feed_TDS": 2000.0, "Coag_PPM": 2.0, "SMBS_PPM": 3.0
}

BASE_EFFECTS = pd.DataFrame({
    "Effect ID": [f"Effect {i}" for i in range(1, 12)],
    "Base Vapor (°C)": np.round(np.linspace(69.0, 42.0, 11), 1),
    "Base Brine (°C)": np.round(np.linspace(66.3, 40.0, 11), 1),
    "Base HTC": np.round(np.linspace(2800.0, 1500.0, 11), 1) 
})

@st.cache_resource(ttl=600)
def init_db_connection():
    if not GSPREAD_INSTALLED: 
        return {"type": "local", "client": None}
    if "gcp_service_account" in st.secrets:
        try:
            scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
            creds_dict = dict(st.secrets["gcp_service_account"])
            if "\\n" in creds_dict["private_key"]: 
                creds_dict["private_key"] = creds_dict["private_key"].replace("\\n", "\n")
            creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
            client = gspread.authorize(creds)
            return {"type": "cloud", "client": client.open(GOOGLE_SHEET_NAME).sheet1}
        except: pass
    return {"type": "local", "client": None}

def load_database(db, target_file=LOCAL_DB_FILE):
    if db["type"] == "cloud" and target_file == LOCAL_DB_FILE:
        try:
            records = db["client"].get_all_records()
            if records: return pd.DataFrame(records)
        except: pass
    if os.path.exists(target_file): return pd.read_csv(target_file)
    return pd.DataFrame()

def save_database(db, df, target_file=LOCAL_DB_FILE):
    # THE FIX: Added safety check to prevent KeyError if the database is empty
    if 'Date' in df.columns:
        df['Date'] = pd.to_datetime(df['Date'], dayfirst=True, errors='coerce').dt.strftime('%Y-%m-%d')
    df = df.fillna(0)
    if db["type"] == "cloud" and target_file == LOCAL_DB_FILE:
        try:
            db["client"].clear()
            db["client"].update([df.columns.values.tolist()] + df.values.tolist())
            df.to_csv(target_file, index=False)
            return True
        except: pass
    df.to_csv(target_file, index=False)
    return True

def load_config(db, target_file=LOCAL_CONFIG_FILE, baseline_dict=MRA_COEF_2014):
    if os.path.exists(target_file):
        try:
            with open(target_file, "r") as f: 
                return json.load(f)
        except: pass
    return baseline_dict.copy()

def save_config(db, coef_dict, target_file=LOCAL_CONFIG_FILE):
    with open(target_file, "w") as f: json.dump(coef_dict, f)

db_conn = init_db_connection()

# ==========================================
# UNIFIED DATABASE SCHEMA & SPECS
# ==========================================
WATER_SPECS = {
    "Feed": {
        "pH": {"lim": (7.5, 9.2), "var": "f_ph", "db_col": "Feed_pH", "avg": 8.14},
        "Turbidity (NTU)": {"lim": (0.0, 5.0), "var": "f_turb", "db_col": "Feed_Turbidity", "avg": 3.2},
        "TSS (ppm)": {"lim": (0.0, 10.0), "var": "f_tss", "db_col": "Feed_TSS", "avg": 6.5},
        "TDS (ppm)": {"lim": (0.0, 42000.0), "var": "f_tds", "db_col": "Feed_TDS", "avg": 41000.0},
        "Total Alkalinity": {"lim": (160.0, 190.0), "var": "f_alk", "db_col": "Feed_Alkalinity", "avg": 170.0},
        "Calcium Hardness": {"lim": (950.0, 1100.0), "var": "f_ca", "db_col": "Feed_Calcium", "avg": 1040.0},
        "Chlorides": {"lim": (21000.0, 22000.0), "var": "f_cl", "db_col": "Feed_Chlorides", "avg": 21500.0},
        "Sulphate": {"lim": (3050.0, 3250.0), "var": "f_so4", "db_col": "Feed_Sulphate", "avg": 3150.0}
    },
    "Product": {
        "pH": {"lim": (5.5, 7.0), "var": "p_ph", "db_col": "Product_pH", "avg": 6.5},
        "Conductivity (μs/cm)": {"lim": (0.0, 15.0), "var": "p_cond", "db_col": "Product_Cond", "avg": 4.6},
        "TDS (ppm)": {"lim": (0.0, 10.0), "var": "p_tds", "db_col": "Product_TDS", "avg": 2.5},
        "Total Iron": {"lim": (0.0, 0.1), "var": "p_iron", "db_col": "Product_Iron", "avg": 0.05},
        "Chlorides": {"lim": (0.0, 5.0), "var": "p_cl", "db_col": "Product_Chlorides", "avg": 0.0},
        "Sulphate": {"lim": (0.0, 1.0), "var": "p_so4", "db_col": "Product_Sulphate", "avg": 0.0}
    }
}

EXACT_DB_COLUMNS = [
    "Date", "Sea Water Upper", "Sea Water Lower", "Sea Water Feed", "Brine Water Return", 
    "Desal production", "LP Steam consumption", "condensate flow", "condensate temp", 
    "1st effect vapour temp", "1st effect brine temp", "Delta T", "1st effect vapour pressure", 
    "Steam inlet temp", "Brine outlet temp", "Sea Water cond I/L temp", "Sea Water o/L temp", 
    "CW supply", "SW return", "Gross production", "GOR", "Overall HTC", "1st Effect HTC", 
    "Residual", "Antiscalant (kg)", "Antifoam (kg)", "Anti_PPM", "Area_1st", "Area_Overall", "Remarks"
]
for cat in ['Feed', 'Product']:
    for param, details in WATER_SPECS[cat].items(): 
        EXACT_DB_COLUMNS.append(details['db_col'])

RO_EXACT_DB_COLUMNS = [
    "Date", "Feed Flow", "Permeate Flow", "Feed TDS", "Permeate TDS", 
    "Clarifier TSS", "PDMF TSS", "SDMF TSS", "Softener Hardness", "HRU Hardness",
    "Cartridge SDI", "Permeate pH", "Permeate COD", "Coagulant PPM", "Flocculant PPM", "SMBS PPM",
    "Recovery", "Rejection", "Residual", "Remarks"
]

# ==========================================
# 2. REPORT & CSV EXPORT GENERATORS
# ==========================================
def generate_daily_csv(date, ops, display_effect_df, w_data, chem_data, mra, extra_tags):
    data_dict = {
        "Date": date.strftime('%d/%m/%Y'),
        "Sea Water Upper": ops['SW_Feed_1st'], "Sea Water Lower": extra_tags['sw_lower'],
        "Sea Water Feed": ops['SW Total'], "Brine Water Return": ops['Brine Return'],
        "Desal production": ops['Desal'], "LP Steam consumption": ops['Steam'],
        "condensate flow": extra_tags['cond_flow'], "condensate temp": extra_tags['cond_temp'],
        "1st effect vapour temp": ops['Stm In_1st'], "1st effect brine temp": ops['Brine_1st'],
        "Delta T": ops['dt_1st'], "1st effect vapour pressure": ops['Press_1st'],
        "Steam inlet temp": ops['Stm In_overall'], "Brine outlet temp": ops['Brine Out_overall'],
        "Sea Water cond I/L temp": ops['SW In_overall'], "Sea Water o/L temp": extra_tags['sw_out_t'],
        "CW supply": extra_tags['cw_supply'], "SW return": extra_tags['sw_return'],
        "Gross production": ops['Gross Prod'], "Recovery (%)": round(ops['Recovery'], 2),
        "GOR": round(ops['GOR'], 2), "Overall HTC": round(ops['htc_overall'], 2),
        "1st Effect HTC": round(ops['htc_1st'], 2), "Residual": round(mra['Residual'], 2),
        "Antiscalant Dosing (PPM)": chem_data['anti_ppm'], "Antiscalant (kg)": chem_data['anti_cons'],
        "Antifoam Dosing (PPM)": chem_data['foam_ppm'], "Antifoam (kg)": chem_data['foam_cons'],
        "Remarks": extra_tags['remarks']
    }
    for cat in ['Feed', 'Product']:
        for param, details in w_data[cat].items(): data_dict[details['db_col']] = details['val']
        
    df = pd.DataFrame([data_dict])
    return df.to_csv(index=False).encode('utf-8')

def generate_ro_daily_csv(date, state, mra):
    data_dict = {
        "Date": date.strftime('%d/%m/%Y'),
        "Feed Flow": state.ro_feed_flow, "Permeate Flow": state.ro_perm_flow,
        "Feed TDS": state.ro_feed_tds, "Permeate TDS": state.ro_perm_tds,
        "Clarifier TSS": state.ro_clarifier_tss, "PDMF TSS": state.ro_pdmf_tss,
        "SDMF TSS": state.ro_sdmf_tss, "Softener Hardness": state.ro_soft_hard,
        "HRU Hardness": state.ro_hru_hard, "Cartridge SDI": state.ro_sdi,
        "Permeate pH": state.ro_perm_ph, "Permeate COD": state.ro_perm_cod,
        "Coagulant PPM": state.ro_coag_ppm, "Flocculant PPM": state.ro_floc_ppm,
        "SMBS PPM": state.ro_smbs_ppm, 
        "Recovery": round((state.ro_perm_flow / state.ro_feed_flow * 100) if state.ro_feed_flow > 0 else 0, 2),
        "Rejection": round(((state.ro_feed_tds - state.ro_perm_tds) / state.ro_feed_tds * 100) if state.ro_feed_tds > 0 else 0, 2),
        "Residual": round(mra['Residual'], 2), "Remarks": state.ro_remarks
    }
    df = pd.DataFrame([data_dict])
    return df.to_csv(index=False).encode('utf-8')

def generate_comprehensive_report(date, ops, display_effect_df, w_data, chem_data, mra, skip_eff, skip_wq, remarks):
    doc = Document()
    doc.add_heading('MED-4 Daily Operational & Performance Report', 0).alignment = WD_ALIGN_PARAGRAPH.CENTER
    p = doc.add_paragraph()
    p.add_run('Prepared by: ').bold = True
    p.add_run('Chembond Water Technologies Limited\n')
    p.add_run('Date: ').bold = True
    p.add_run(date.strftime('%d/%m/%Y'))
    p.alignment = WD_ALIGN_PARAGRAPH.RIGHT

    doc.add_heading('1. Executive Summary', level=1)
    doc.add_paragraph(f"On {date.strftime('%d/%m/%Y')}, the MED-4 unit achieved a Gross Production of {ops['Gross Prod']} m³/h and a Gain Output Ratio (GOR) of {ops['GOR']:.2f}:1. The Specific Thermal Energy Consumption (STEC) was {ops['STEC']:.2f} kWh/ton with a system recovery of {ops['Recovery']:.1f}%.")

    doc.add_heading('2. Operational Data Summary', level=1)
    t_ops = doc.add_table(rows=1, cols=4); t_ops.style = 'Table Grid'
    for i, h in enumerate(['Parameter', 'UOM', 'Design', 'Actual']): t_ops.rows[0].cells[i].text = h
    ops_rows = [
        ['Sea Water Feed', 'm³/h', '2400', str(ops['SW Total'])], 
        ['Sea Water Upper', 'm³/h', '580', str(ops['SW_Feed_1st'])], 
        ['Brine Water Return', 'm³/h', '1400', str(ops['Brine Return'])], 
        ['Desal production', 'm³/h', '1000', str(ops['Desal'])], 
        ['Gross production', 'm³/h', '-', str(ops['Gross Prod'])], 
        ['LP Steam consumption', 'TPH', '92-94.5', str(ops['Steam'])], 
        ['Recovery', '%', '40.0', f"{ops['Recovery']:.2f}"], 
        ['GOR', 'Ratio', '10.5 : 1', f"{ops['GOR']:.2f} : 1"]
    ]
    for row in ops_rows:
        rc = t_ops.add_row().cells
        for i, val in enumerate(row): rc[i].text = val

    doc.add_heading('3. Chemical Dosing Status', level=1)
    t_chem = doc.add_table(rows=1, cols=3); t_chem.style = 'Table Grid'
    for i, h in enumerate(['Chemical', 'Target Dosing (PPM)', 'Actual Consumption (kg/hr)']): t_chem.rows[0].cells[i].text = h
    rc1 = t_chem.add_row().cells
    rc1[0].text, rc1[1].text, rc1[2].text = "Kem Watreat r 3687 (Antiscalant)", f"{chem_data['anti_ppm']:.1f}", f"{chem_data['anti_cons']:.2f}"
    rc2 = t_chem.add_row().cells
    rc2[0].text, rc2[1].text, rc2[2].text = "Kem Antifoam 1795", f"{chem_data['foam_ppm']:.1f}", f"{chem_data['foam_cons']:.2f}"

    doc.add_heading('4. Thermal Integrity (HTC)', level=1)
    doc.add_paragraph(f"Overall Plant HTC: {ops['htc_overall']:.2f} W/m²K | 1st Effect HTC: {ops['htc_1st']:.2f} W/m²K")
    
    doc.add_heading('5. Water Quality', level=1)
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

    doc.add_heading('6. MRA Fouling Indicator', level=1)
    diff_pct = (mra['Residual'] / mra['Predicted']) * 100 if mra['Predicted'] > 0 else 0
    doc.add_paragraph(f"Actual Gross: {mra['Actual']:.1f} m³/h | MRA Predicted: {mra['Predicted']:.1f} m³/h | Difference: {diff_pct:.1f}%")
    if diff_pct <= -5.0: doc.add_paragraph(f"STATUS: FOULING DETECTED ({diff_pct:.1f}% loss). Please clean the machine.").runs[0].font.color.rgb = RGBColor(255, 0, 0)
    elif diff_pct <= -4.0: doc.add_paragraph(f"STATUS: WARNING ({diff_pct:.1f}% loss). Increase antiscalant dosing.").runs[0].font.color.rgb = RGBColor(255, 140, 0)
    else: doc.add_paragraph(f"STATUS: CLEAN ({diff_pct:.1f}% loss). System operating normally.").runs[0].font.color.rgb = RGBColor(0, 128, 0)
    
    if remarks and str(remarks).strip() != "":
        doc.add_heading('7. Remarks & Observations', level=1)
        doc.add_paragraph(str(remarks))

    bio = io.BytesIO()
    doc.save(bio)
    return bio.getvalue()

def generate_ro_comprehensive_report(date, state, mra):
    doc = Document()
    doc.add_heading('HERO RO Plant Daily Operational Report', 0).alignment = WD_ALIGN_PARAGRAPH.CENTER
    p = doc.add_paragraph()
    p.add_run('Prepared by: ').bold = True
    p.add_run('Chembond Water Technologies Limited\n')
    p.add_run('Date: ').bold = True
    p.add_run(date.strftime('%d/%m/%Y'))
    p.alignment = WD_ALIGN_PARAGRAPH.RIGHT

    doc.add_heading('1. Executive Summary', level=1)
    recovery = (state.ro_perm_flow / state.ro_feed_flow * 100) if state.ro_feed_flow > 0 else 0
    rejection = ((state.ro_feed_tds - state.ro_perm_tds) / state.ro_feed_tds * 100) if state.ro_feed_tds > 0 else 0
    doc.add_paragraph(f"On {date.strftime('%d/%m/%Y')}, the HERO RO Plant processed {state.ro_feed_flow} m³/h of feed, achieving a Permeate Flow of {state.ro_perm_flow} m³/h. The overall plant recovery was {recovery:.1f}% with a salt rejection rate of {rejection:.1f}%.")

    doc.add_heading('2. Quality Guarantees', level=1)
    t_wq = doc.add_table(rows=1, cols=4); t_wq.style = 'Table Grid'
    for i, h in enumerate(['Parameter', 'Limit/Spec', 'Actual', 'Status']): t_wq.rows[0].cells[i].text = h
    
    guarantees = [
        ("Clarifier Outlet TSS", "< 10 ppm", state.ro_clarifier_tss, "Pass" if state.ro_clarifier_tss < 10.0 else "Fail"),
        ("PDMF Outlet TSS", "< 3 ppm", state.ro_pdmf_tss, "Pass" if state.ro_pdmf_tss < 3.0 else "Fail"),
        ("SDMF Outlet TSS", "< 1 ppm", state.ro_sdmf_tss, "Pass" if state.ro_sdmf_tss < 1.0 else "Fail"),
        ("Softener O/L Hardness", "< 5 ppm", state.ro_soft_hard, "Pass" if state.ro_soft_hard < 5.0 else "Fail"),
        ("HRU O/L Hardness", "< 1 ppm", state.ro_hru_hard, "Pass" if state.ro_hru_hard < 1.0 else "Fail"),
        ("Cartridge Filter SDI", "< 3", state.ro_sdi, "Pass" if state.ro_sdi < 3.0 else "Fail"),
        ("RO Permeate COD", "< 10 ppm", state.ro_perm_cod, "Pass" if state.ro_perm_cod < 10.0 else "Fail"),
        ("RO Permeate pH", "7.0 - 7.5", state.ro_perm_ph, "Pass" if 7.0 <= state.ro_perm_ph <= 7.5 else "Fail")
    ]
    for param, limit, val, status in guarantees:
        rc = t_wq.add_row().cells
        rc[0].text, rc[1].text, rc[2].text, rc[3].text = str(param), str(limit), str(val), str(status)

    doc.add_heading('3. MRA Fouling Indicator (Normalized Flow)', level=1)
    diff_pct = (mra['Residual'] / mra['Predicted']) * 100 if mra['Predicted'] > 0 else 0
    doc.add_paragraph(f"Actual Permeate: {mra['Actual']:.1f} m³/h | Normalized Predicted Permeate: {mra['Predicted']:.1f} m³/h | Difference: {diff_pct:.1f}%")
    if diff_pct <= -5.0: doc.add_paragraph(f"STATUS: MEMBRANE FOULING DETECTED ({diff_pct:.1f}% flow loss). Initiate CIP protocol.").runs[0].font.color.rgb = RGBColor(255, 0, 0)
    elif diff_pct <= -4.0: doc.add_paragraph(f"STATUS: WARNING ({diff_pct:.1f}% flow loss). Verify antiscalant and SMBS dosing.").runs[0].font.color.rgb = RGBColor(255, 140, 0)
    else: doc.add_paragraph(f"STATUS: CLEAN ({diff_pct:.1f}% flow loss). Membranes operating normally.").runs[0].font.color.rgb = RGBColor(0, 128, 0)
    
    if state.ro_remarks and str(state.ro_remarks).strip() != "":
        doc.add_heading('4. Remarks & Observations', level=1)
        doc.add_paragraph(str(state.ro_remarks))

    bio = io.BytesIO()
    doc.save(bio)
    return bio.getvalue()

def generate_monthly_report(df_month, month_str, year_str):
    doc = Document()
    doc.add_heading(f'MED-4 Monthly Performance Summary: {month_str} {year_str}', 0).alignment = WD_ALIGN_PARAGRAPH.CENTER
    doc.add_heading('1. Monthly Aggregation', level=1)
    t_agg = doc.add_table(rows=1, cols=4); t_agg.style = 'Table Grid'
    for i, h in enumerate(['Metric', 'Minimum', 'Maximum', 'Average']): t_agg.rows[0].cells[i].text = h
    metrics = [("Gross production (m³/h)", df_month['Gross production']), ("Gain Output Ratio (GOR)", df_month['GOR']), ("Overall HTC (W/m²K)", df_month['Overall HTC']), ("1st Effect HTC", df_month['1st Effect HTC'])]
    for name, series in metrics:
        rc = t_agg.add_row().cells
        rc[0].text, rc[1].text, rc[2].text, rc[3].text = name, f"{pd.to_numeric(series, errors='coerce').min():.2f}", f"{pd.to_numeric(series, errors='coerce').max():.2f}", f"{pd.to_numeric(series, errors='coerce').mean():.2f}"
    bio = io.BytesIO()
    doc.save(bio)
    return bio.getvalue()

def generate_ro_monthly_report(df_month, month_str, year_str):
    doc = Document()
    doc.add_heading(f'HERO RO Monthly Performance Summary: {month_str} {year_str}', 0).alignment = WD_ALIGN_PARAGRAPH.CENTER
    doc.add_heading('1. Monthly Aggregation', level=1)
    t_agg = doc.add_table(rows=1, cols=4); t_agg.style = 'Table Grid'
    for i, h in enumerate(['Metric', 'Minimum', 'Maximum', 'Average']): t_agg.rows[0].cells[i].text = h
    metrics = [("Permeate Flow (m³/h)", df_month['Permeate Flow']), ("Plant Recovery (%)", df_month['Recovery']), ("Salt Rejection (%)", df_month['Rejection']), ("Permeate TDS (ppm)", df_month['Permeate TDS'])]
    for name, series in metrics:
        rc = t_agg.add_row().cells
        rc[0].text, rc[1].text, rc[2].text, rc[3].text = name, f"{pd.to_numeric(series, errors='coerce').min():.2f}", f"{pd.to_numeric(series, errors='coerce').max():.2f}", f"{pd.to_numeric(series, errors='coerce').mean():.2f}"
    bio = io.BytesIO()
    doc.save(bio)
    return bio.getvalue()

# ==========================================
# 3. SYNCHRONIZATION ENGINE
# ==========================================
DEFAULTS = {
    'steam': 71.75, 'desal': 800.0, 'gross': 801.4, 'sw_upper': 553.63, 'sw_total': 2100.0, 'brine_ret': 1275.5,
    'sw_in_t': 30.0, 'brine_out_t': 41.0, 'stm_in_t': 165.54, 'vap_out_t': 70.0, 'mra_press': 231.76, 'mra_t1': 68.47, 'mra_bt1': 65.46,
    'f_ph': 8.14, 'f_turb': 3.2, 'f_tss': 6.5, 'f_tds': 41000.0, 'f_alk': 170.0, 'f_ca': 1040.0, 'f_cl': 21500.0, 'f_so4': 3150.0,
    'p_ph': 6.5, 'p_cond': 4.6, 'p_tds': 2.5, 'p_iron': 0.05, 'p_cl': 0.0, 'p_so4': 0.0,
    'chem_anti_ppm': 4.82, 'chem_anti_cons': 13.5, 'chem_foam_ppm': 0.0, 'chem_foam_cons': 0.0,
    'skip_eff': False, 'skip_wq': False, 'remarks': "", 'area_1st': 1757.49, 'area_overall': 19332.0,
    'sw_lower': 0.0, 'cond_flow': 0.0, 'cond_temp': 0.0, 'sw_out_t': 0.0, 'cw_supply': 0.0, 'sw_return': 0.0
}

SYNC_MAP = {
    'steam': ['in_steam', 't1_steam', 't5_steam'], 'desal': ['in_desal', 't1_desal'], 'gross': ['in_gross', 't1_gross'],
    'sw_upper': ['in_sw_up', 't1_sw_up', 't5_sw_up', 't2_sw_up'], 'sw_total': ['in_sw_tot', 't1_sw_tot', 't4_sw_tot', 't2_sw_tot'], 
    'brine_ret': ['in_brine', 't1_brine', 't5_bflow'], 'sw_in_t': ['in_sw_in', 't2_sw_in'], 'brine_out_t': ['in_brine_out', 't2_brine_out'], 
    'stm_in_t': ['in_stm_in', 't5_stm_t'], 'vap_out_t': ['in_vap_out', 't2_vap_out'], 'mra_press': ['in_press', 't5_press'], 
    'mra_t1': ['in_t1', 't5_t1', 't2_t1'], 'mra_bt1': ['in_bt1', 't5_bt1', 't2_bt1'], 'f_ph': ['in_f_ph', 't3_f_ph'], 
    'f_turb': ['in_f_turb', 't3_f_turb'], 'f_tss': ['in_f_tss', 't3_f_tss'], 'f_tds': ['in_f_tds', 't3_f_tds'],
    'f_alk': ['in_f_alk', 't3_f_alk'], 'f_ca': ['in_f_ca', 't3_f_ca'], 'f_cl': ['in_f_cl', 't3_f_cl'], 'f_so4': ['in_f_so4', 't3_f_so4'],
    'p_ph': ['in_p_ph', 't3_p_ph'], 'p_cond': ['in_p_cond', 't3_p_cond'], 'p_tds': ['in_p_tds', 't3_p_tds'], 
    'p_iron': ['in_p_iron', 't3_p_iron'], 'p_cl': ['in_p_cl', 't3_p_cl'], 'p_so4': ['in_p_so4', 't3_p_so4'],
    'chem_anti_ppm': ['in_anti_ppm', 't4_anti_ppm', 't5_anti'], 'chem_anti_cons': ['in_anti_cons', 't4_anti_cons'],
    'chem_foam_ppm': ['in_foam_ppm', 't4_foam_ppm'], 'chem_foam_cons': ['in_foam_cons', 't4_foam_cons'],
    'remarks': ['in_remarks'], 'area_1st': ['t2_area_1st'], 'area_overall': ['t2_area_overall'],
    'sw_lower': ['in_sw_low'], 'cond_flow': ['in_cond_flow'], 'cond_temp': ['in_cond_temp'], 
    'sw_out_t': ['in_sw_out'], 'cw_supply': ['in_cw_supply'], 'sw_return': ['in_sw_return']
}

if 'vars' not in st.session_state: st.session_state.vars = DEFAULTS.copy()
for k, v in DEFAULTS.items():
    if k not in st.session_state.vars: st.session_state.vars[k] = v

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

if 'daily_logs' not in st.session_state: st.session_state.daily_logs = load_database(db_conn, LOCAL_DB_FILE)
if 'ro_daily_logs' not in st.session_state: st.session_state.ro_daily_logs = load_database(db_conn, RO_LOCAL_DB_FILE)

if 'mra_coef' not in st.session_state: st.session_state.mra_coef = load_config(db_conn, LOCAL_CONFIG_FILE, MRA_COEF_2014)
if 'ro_mra_coef' not in st.session_state: st.session_state.ro_mra_coef = load_config(db_conn, RO_LOCAL_CONFIG_FILE, RO_MRA_COEF_BASE)

if "messages" not in st.session_state:
    st.session_state.messages = [{"role": "assistant", "content": "Hello! I am the Chembond Water Assistant. Ask me anything about how the calculations work."}]

def sync_var(var_name, source_key):
    st.session_state.vars[var_name] = st.session_state[source_key]
    for target_key in SYNC_MAP[var_name]:
        if target_key != source_key: st.session_state[target_key] = st.session_state[source_key]

def get_v(var_name): return st.session_state.vars[var_name]

LATENT_HEAT_STEAM_KJ_KG = 2260.0 

def render_chatbot():
    st.sidebar.divider()
    st.sidebar.markdown("### 💬 Chembond Water Assistant")
    
    chat_container = st.sidebar.container(height=350)
    for message in st.session_state.messages:
        chat_container.chat_message(message["role"]).markdown(message["content"])

    if prompt := st.sidebar.chat_input("Ask a question about formulas..."):
        st.session_state.messages.append({"role": "user", "content": prompt})
        chat_container.chat_message("user").markdown(prompt)

        p_lower = prompt.lower()
        if "password" in p_lower: response = "For security reasons, I cannot provide the Master Password. Please contact the plant administrator."
        elif "auto" in p_lower or "dose" in p_lower or "optimal" in p_lower or "calculate" in p_lower and "auto" in p_lower:
            response = "The **Auto-Calculate Optimal Dose** feature is currently in development! In a future update, it will use real-time feed water chemistry, Concentration Factors, and scaling indices to scientifically recommend the exact PPM needed."
        elif re.search(r'\bgor\b', p_lower) or "gain output ratio" in p_lower:
            response = "**GOR (Gain Output Ratio)** is calculated as:\n`Gross Production (m³/h) / LP Steam (TPH)`\n\nIt represents the 'fuel economy' of the plant—how many tons of water are produced per ton of steam."
        elif "recovery" in p_lower: response = "**System Recovery** is calculated as:\n`(Gross Production / Total SW Feed) * 100` for MED, or `(Permeate Flow / Feed Flow) * 100` for RO."
        elif re.search(r'\blmtd\b', p_lower) or "log mean" in p_lower:
            response = "**LMTD (Log Mean Temperature Difference)** is currently calculated using a **Simple Delta T** because the Vapor Outlet sensor is unavailable.\n\n* Overall ΔT = `1st Effect Vapor - Final Brine Temp`."
        elif "1st effect htc" in p_lower:
            response = "**1st Effect HTC (U)** is calculated as:\n`(Q_1st / (Area_1st * ΔT_1st)) * 1000`\n\nWhere:\n* `Q_1st` = `1st Effect SW Feed * (1st Brine - Avg Brine 4 to 7) * 0.930`\n* `ΔT_1st` = `1st Vapor - 1st Brine`."
        elif "overall htc" in p_lower or "htc" in p_lower:
            response = "**Overall HTC (U)** is calculated as:\n`(Q_overall / (Area_overall * ΔT_overall)) * 1000`\n\nWhere:\n* `Q_overall` = `Total SW Feed * (Final Brine - SW Cond I/L Temp) * 0.930`\n* `ΔT_overall` = `1st Vapor - Final Brine`."
        elif "fouling factor" in p_lower: response = "**Fouling Factor** is calculated simply as:\n`1 / HTC`"
        elif re.search(r'\bols\b', p_lower) or "linear regression" in p_lower:
            response = "**OLS (Ordinary Least Squares)** is the standard mathematical method used to draw a straight line of best fit through data points. It creates the 'Digital Twin' of the plant's clean physics."
        elif "xgboost" in p_lower or "random forest" in p_lower or "ai" in p_lower:
            response = "**Random Forest and XGBoost** are advanced AI models that use Decision Trees instead of linear math. They are highly accurate at tracking complex plant behavior, but they don't give you simple linear 'coefficients' like OLS does. Your selected model is saved and will persist across reboots."
        elif "residual" in p_lower:
            response = "**Residual** is calculated as:\n`Actual Production - Predicted Production`\n\nA negative residual means the plant is underperforming compared to its clean digital twin, indicating scale/fouling is blocking performance."
        elif "fouling" in p_lower or "alert" in p_lower or "status" in p_lower:
            response = "The software calculates a **% Difference**:\n`(Residual / Predicted) * 100`\n\n* **Better than -4%:** CLEAN\n* **-4% to -5%:** WARNING\n* **Worse than -5%:** FOULING DETECTED"
        elif "bulk" in p_lower or "upload" in p_lower:
            response = "In the **Bulk Uploads** tab, you can upload an entire month of logs using your exact Excel sequence. The software automatically calculates GOR, HTC, Predicted Production, and Residuals for every row, safely handling missing sensor data by borrowing from the 2014 baseline and averaging missing water quality parameters."
        elif "remarks" in p_lower or "observation" in p_lower:
            response = "You can add custom notes, TT errors, or shift observations in the **Remarks & Observations** box in the Reporting Tab. These automatically save to the database and print on the Daily Word Report!"
        else:
            response = "I am the Chembond Water Assistant. I can explain the new formulas for **1st Effect HTC, Overall HTC, Remarks, GOR, RO Rejection, Recovery, Residuals, Fouling alerts, OLS**, and **AI Models**. What would you like to know?"

        st.session_state.messages.append({"role": "assistant", "content": response})
        chat_container.chat_message("assistant").markdown(response)

# ==========================================
# MAIN APPLICATION HUBS AND ROUTER
# ==========================================
def main():
    try: st.sidebar.image("chembond_logo.png", use_container_width=True)
    except: st.sidebar.markdown("### 🔹 CHEMBOND WATER TECHNOLOGIES LIMITED") 
        
    st.sidebar.divider()
    st.sidebar.markdown("### 🌍 Utility Network Dashboard")
    
    # ------------------------------------------
    # LANDING PAGE ROUTER
    # ------------------------------------------

    utility_choice = st.sidebar.selectbox(
        "Select Utility System",
        ["-- Central Hub --", "Cooling Towers", "Boilers", "RO Plant", "Multi-Effect Distillation (MED)", "Projection Engine"]
    )

    if utility_choice == "-- Central Hub --":
        st.title("🏭 Centralized Site Utility Management Suite")
        st.markdown("---")
        st.markdown("Welcome to the centralized plant health network configured for Reliance facilities. Use the left navigation panel to monitor plant efficiencies, evaluate heat exchanger health parameters, and access machine-learning normalization predictors.")
        
        # Calculate Live Status for Landing Page
        med_status = "No Data"
        ro_status = "No Data"
        
        if not st.session_state.daily_logs.empty:
            last_med_row = st.session_state.daily_logs.iloc[-1]
            med_diff = (pd.to_numeric(last_med_row.get('Residual', 0)) / pd.to_numeric(last_med_row.get('Gross production', 1))) * 100
            if med_diff <= -5.0: med_status = "🔴 Please Check Plant"
            elif med_diff <= -4.0: med_status = "🟡 Warning: Deviation Detected"
            else: med_status = "🟢 Good Working Condition"

        if not st.session_state.ro_daily_logs.empty:
            last_ro_row = st.session_state.ro_daily_logs.iloc[-1]
            ro_diff = (pd.to_numeric(last_ro_row.get('Residual', 0)) / pd.to_numeric(last_ro_row.get('Permeate Flow', 1))) * 100
            if ro_diff <= -5.0: ro_status = "🔴 Please Check Plant"
            elif ro_diff <= -4.0: ro_status = "🟡 Warning: Deviation Detected"
            else: ro_status = "🟢 Good Working Condition"

        c_layout1, c_layout2 = st.columns(2)
        with c_layout1:
            st.info("### ❄️ Cooling Towers\nMonitor real-time system concentration cycles, calculated thermal approach limits, and active biocidal inventory parameters.\n\n*Status: Hidden from menu during updates*")
        with c_layout2:
            st.error("### 🔥 Industrial Boiler Infrastructure\nTrack steam header drum pressures, automated surface continuous blowdown rates, and reserve chemical levels.\n\n*Status: Hidden from menu during updates*")
            
        c_layout3, c_layout4 = st.columns(2)
        with c_layout3:
            st.success(f"### 💧 High Pressure RO Plants\nEvaluate membrane permeate flux decay rates, normalized cartridge delta pressures, and specific power consumption benchmarks.\n\n*Status: HERO Plant Configured*\n\n**Latest Performance:** {ro_status}")
        with c_layout4:
            st.warning(f"### 🌊 Multi-Effect Distillation (MED)\nAccess advanced baseline multi-variable regression analysis, thermal heat transfer evaluation, and active antiscalant tracking.\n\n*Status: Unit MED-4 Online & Verified*\n\n**Latest Performance:** {med_status}")
            
        render_chatbot()
        return

elif utility_choice == "Projection Engine":
        st.title("🧮 Enterprise Chemical Projection Engine")
        st.markdown("Thermodynamic simulation and predictive dosing portal.")

        target_utility = st.radio("Active Simulation Module", ["RO Plant", "MED", "Cooling Tower", "Boiler"], horizontal=True)
        st.divider()

        if target_utility == "RO Plant":
            st.subheader("RO Pre-Treatment & Membrane Scaling Simulator")
            
            col1, col2 = st.columns([1, 2.5])
            
            with col1:
                st.markdown("### 💧 Feed Chemistry (mg/L)")
                with st.container(border=True):
                    feed_ca = st.number_input("Calcium (Ca²⁺)", value=150.0, step=10.0)
                    feed_ba = st.number_input("Barium (Ba²⁺)", value=0.05, step=0.01, format="%.3f")
                    feed_sr = st.number_input("Strontium (Sr²⁺)", value=1.2, step=0.1)
                    feed_so4 = st.number_input("Sulfate (SO₄²⁻)", value=250.0, step=10.0)
                    feed_sio2 = st.number_input("Silica (SiO₂)", value=15.0, step=1.0)
                    feed_lsi = st.number_input("Feed LSI", value=0.2, step=0.1)
                
                st.markdown("### ⚙️ System Parameters")
                with st.container(border=True):
                    sys_recovery = st.slider("Target Recovery (%)", min_value=50.0, max_value=95.0, value=85.0, step=0.5)
                    sys_flow = st.number_input("Feed Flow (m³/h)", value=450.0, step=10.0)

            with col2:
                try:
                    # Hooking into the thermodynamic kernel we built previously
                    from projection_engine import UtilityProjectionEngine
                    engine = UtilityProjectionEngine()
                    
                    # 1. Run the Math
                    feed_ions = {"Ca": feed_ca, "Ba": feed_ba, "Sr": feed_sr, "SO4": feed_so4, "SiO2": feed_sio2, "LSI": feed_lsi}
                    si_results = engine.calculate_ro_saturation(feed_ions, sys_recovery)
                    rec = engine.get_recommendation("RO", si_results)
                    
                    # 2. Display the Recommendation Box
                    st.markdown("### 📋 Chembond Treatment Specification")
                    if rec["Status"] == "Safe":
                        st.success(f"**Recommended Product:** {rec['Product']}")
                        st.info(f"**Target Dosing:** {rec['Target_Dose']} PPM  |  **Required Consumption:** {(sys_flow * rec['Target_Dose'])/1000:.2f} kg/hr")
                    else:
                        st.error(f"**CRITICAL WARNING:** {rec['Message']}")

                    # 3. Display Saturation Metrics
                    st.markdown("### 🔬 Concentrate Saturation Indices (Trailing Element)")
                    m1, m2, m3, m4 = st.columns(4)
                    m1.metric("Langelier (LSI)", f"{si_results['Concentrate_LSI']:.2f}")
                    m2.metric("BaSO4 (x Sat)", f"{si_results['BaSO4_SI']:.1f}")
                    m3.metric("CaSO4 (x Sat)", f"{si_results['CaSO4_SI']:.2f}")
                    m4.metric("Silica (x Sat)", f"{si_results['Silica_SI']:.2f}")

                    # 4. Draw the Professional Curve
                    st.markdown("### 📈 Scaling Potential vs. System Recovery")
                    st.markdown("Observe how ionic saturation exponentially increases as recovery pushes past 80%.")
                    
                    curve_df = engine.generate_projection_curve(feed_ions, min_rec=50, max_rec=95, steps=30)
                    melted_df = curve_df.melt('Recovery (%)', var_name='Saturation Metric', value_name='Index Value')
                    
                    chart = alt.Chart(melted_df).mark_line(point=True).encode(
                        x=alt.X('Recovery (%):Q', scale=alt.Scale(domain=[50, 95])),
                        y=alt.Y('Index Value:Q', scale=alt.Scale(zero=False)),
                        color='Saturation Metric:N',
                        tooltip=['Recovery (%)', 'Saturation Metric', 'Index Value']
                    ).properties(height=400)
                    
                    st.altair_chart(chart, use_container_width=True)

                except ImportError:
                    st.error("🚨 **System Architecture Error:** The `projection_engine.py` file was not found.")
                    st.markdown("Please ensure you have saved the `projection_engine.py` file (which contains the thermodynamic math and your Word file matrix) in the exact same folder as this `streamlit_app.py` file.")
                    
        else:
            st.info(f"🚧 The thermodynamic interface for {target_utility} is currently being mapped.")
            
        render_chatbot()
        return
        
    elif utility_choice == "Cooling Towers" or utility_choice == "Boilers":
        st.title(f"🏭 {utility_choice} Diagnostic Interface")
        st.info(f"🚧 **Work in Progress:** The specialized tracking network for {utility_choice} is currently undergoing structural file mapping. Features will go live shortly.")
        render_chatbot()
        return

    # ------------------------------------------
    # RO PLANT ENGINE (HERO)
    # ------------------------------------------
    elif utility_choice == "RO Plant":
        st.title("🏭 High Efficiency Reverse Osmosis (HERO) Suite")
        st.markdown("Monitor high-recovery RO metrics, pretreatment guarantees, and antiscalant/coagulant dosing for the SEZ RO facility.")
        
        log_date = st.sidebar.date_input("Date", datetime.date.today(), format="DD/MM/YYYY")
        log_date_str = log_date.strftime('%Y-%m-%d')
        
        # Setup session states for RO parameters safely
        ro_vars = {
            'ro_feed_flow': 450.0, 'ro_perm_flow': 385.0, 'ro_feed_tds': 2000.0, 'ro_perm_tds': 90.0,
            'ro_clarifier_tss': 8.0, 'ro_pdmf_tss': 2.0, 'ro_sdmf_tss': 0.5, 'ro_soft_hard': 4.0,
            'ro_hru_hard': 0.5, 'ro_sdi': 2.5, 'ro_perm_ph': 7.2, 'ro_perm_cod': 8.0,
            'ro_coag_ppm': 2.0, 'ro_floc_ppm': 1.0, 'ro_smbs_ppm': 3.0, 'ro_remarks': ""
        }
        for k, v in ro_vars.items():
            if k not in st.session_state: st.session_state[k] = v

        # RO Database Autoloader
        if 'ro_last_selected_date' not in st.session_state: st.session_state.ro_last_selected_date = None

        if log_date_str != st.session_state.ro_last_selected_date:
            st.session_state.ro_last_selected_date = log_date_str
            if not st.session_state.ro_daily_logs.empty and 'Date' in st.session_state.ro_daily_logs.columns:
                db_dates = pd.to_datetime(st.session_state.ro_daily_logs['Date'], errors='coerce').dt.strftime('%Y-%m-%d').values
                if log_date_str in db_dates:
                    row_idx = np.where(db_dates == log_date_str)[0][0]
                    row = st.session_state.ro_daily_logs.iloc[row_idx]
                    
                    db_to_var_mapping = {
                        'ro_feed_flow': 'Feed Flow', 'ro_perm_flow': 'Permeate Flow',
                        'ro_feed_tds': 'Feed TDS', 'ro_perm_tds': 'Permeate TDS',
                        'ro_clarifier_tss': 'Clarifier TSS', 'ro_pdmf_tss': 'PDMF TSS',
                        'ro_sdmf_tss': 'SDMF TSS', 'ro_soft_hard': 'Softener Hardness',
                        'ro_hru_hard': 'HRU Hardness', 'ro_sdi': 'Cartridge SDI',
                        'ro_perm_ph': 'Permeate pH', 'ro_perm_cod': 'Permeate COD',
                        'ro_coag_ppm': 'Coagulant PPM', 'ro_floc_ppm': 'Flocculant PPM',
                        'ro_smbs_ppm': 'SMBS PPM', 'ro_remarks': 'Remarks'
                    }
                    
                    loaded_vars = False
                    for var_key, col_name in db_to_var_mapping.items():
                        if col_name in row.index and pd.notna(row[col_name]):
                            try:
                                val_str = str(row[col_name]).strip()
                                if val_str and val_str.lower() not in ['nan', 'none', 'null', 'na']:
                                    if var_key == 'ro_remarks': st.session_state[var_key] = val_str
                                    else: st.session_state[var_key] = float(val_str.replace(',', ''))
                                    loaded_vars = True
                            except: pass 
                    if loaded_vars: st.sidebar.success(f"📅 Auto-loaded historical RO data for {log_date.strftime('%d/%m/%Y')}")

        # --- RO MRA CALCULATION ---
        ro_mra_data = {}
        ro_coefs = st.session_state.ro_mra_coef 
        ro_model_type = ro_coefs.get("model_type", "OLS")
        
        ro_live_input = [st.session_state.ro_feed_flow, st.session_state.ro_feed_tds, st.session_state.ro_coag_ppm, st.session_state.ro_smbs_ppm]
        
        if ro_model_type == "OLS":
            ro_mra_data['Predicted'] = (
                ro_coefs["Intercept"] + 
                (ro_coefs["Feed_Flow"] * ro_live_input[0]) + 
                (ro_coefs["Feed_TDS"] * ro_live_input[1]) + 
                (ro_coefs["Coag_PPM"] * ro_live_input[2]) + 
                (ro_coefs["SMBS_PPM"] * ro_live_input[3])
            )
        else:
            try:
                active_model = joblib.load(RO_AI_MODEL_FILE)
                live_df = pd.DataFrame([ro_live_input], columns=["Feed_Flow", "Feed_TDS", "Coag_PPM", "SMBS_PPM"])
                ro_mra_data['Predicted'] = float(active_model.predict(live_df)[0])
            except: ro_mra_data['Predicted'] = 0.0
                
        ro_mra_data['Actual'] = st.session_state.ro_perm_flow
        ro_mra_data['Residual'] = ro_mra_data['Actual'] - ro_mra_data['Predicted']

        ro_var_data = []
        ro_param_keys = ["Feed_Flow", "Feed_TDS", "Coag_PPM", "SMBS_PPM"]
        ro_param_names = ["Feed Flow", "Feed TDS", "Coagulant PPM", "SMBS PPM"]
        
        for i in range(4):
            dev = ro_live_input[i] - RO_MRA_BASELINE[ro_param_keys[i]]
            weight = ro_coefs.get(ro_param_keys[i], 0.0) 
            if ro_model_type == "OLS": impact = dev * weight
            else: impact = np.nan 
            ro_var_data.append([ro_param_names[i], RO_MRA_BASELINE[ro_param_keys[i]], ro_live_input[i], dev, weight, impact])
            
        ro_mra_data['Variance_DF'] = pd.DataFrame(ro_var_data, columns=["Parameter", "Baseline", "Live Input", "Deviation", "Regression Weight", "Impact (m³/h)"])

        # --- THE FIX: MERGED RO TABS ---
        ro_tabs = st.tabs(["📥 System Inputs", "🌊 KPIs & Quality Guarantees", "🛢️ Chemical Dosing", "🧠 Normalization MRA", "📂 Reporting & Logbook", "🤖 AI Model Select", "📤 Bulk Uploads"])
        
        with ro_tabs[0]:
            st.subheader("HERO Plant Inputs")
            col1, col2 = st.columns(2)
            with col1:
                st.session_state.ro_feed_flow = st.number_input("Feed Flow (m³/hr)", value=st.session_state.ro_feed_flow, key="in_ro_feed_flow")
                st.session_state.ro_feed_tds = st.number_input("Feed TDS (ppm)", value=st.session_state.ro_feed_tds, key="in_ro_feed_tds")
            with col2:
                st.session_state.ro_perm_flow = st.number_input("Permeate Flow (m³/hr)", value=st.session_state.ro_perm_flow, key="in_ro_perm_flow")
                st.session_state.ro_perm_tds = st.number_input("Permeate TDS (ppm)", value=st.session_state.ro_perm_tds, key="in_ro_perm_tds")
                
            st.markdown("### Pre-treatment Parameters")
            p1, p2, p3 = st.columns(3)
            with p1:
                st.session_state.ro_clarifier_tss = st.number_input("Clarifier Outlet TSS (ppm)", value=st.session_state.ro_clarifier_tss, key="in_ro_clarifier_tss")
                st.session_state.ro_pdmf_tss = st.number_input("PDMF Outlet TSS (ppm)", value=st.session_state.ro_pdmf_tss, key="in_ro_pdmf_tss")
                st.session_state.ro_sdmf_tss = st.number_input("SDMF Outlet TSS (ppm)", value=st.session_state.ro_sdmf_tss, key="in_ro_sdmf_tss")
            with p2:
                st.session_state.ro_soft_hard = st.number_input("Softener Outlet Hardness (ppm)", value=st.session_state.ro_soft_hard, key="in_ro_soft_hard")
                st.session_state.ro_hru_hard = st.number_input("HRU Outlet Hardness (ppm)", value=st.session_state.ro_hru_hard, key="in_ro_hru_hard")
                st.session_state.ro_sdi = st.number_input("Cartridge Filter SDI", value=st.session_state.ro_sdi, key="in_ro_sdi")
            with p3:
                st.session_state.ro_perm_ph = st.number_input("Permeate pH", value=st.session_state.ro_perm_ph, key="in_ro_perm_ph")
                st.session_state.ro_perm_cod = st.number_input("Permeate COD (ppm)", value=st.session_state.ro_perm_cod, key="in_ro_perm_cod")
                
        with ro_tabs[1]:
            st.subheader("HERO Key Performance Indicators")
            recovery = (st.session_state.ro_perm_flow / st.session_state.ro_feed_flow * 100) if st.session_state.ro_feed_flow > 0 else 0
            rejection = ((st.session_state.ro_feed_tds - st.session_state.ro_perm_tds) / st.session_state.ro_feed_tds * 100) if st.session_state.ro_feed_tds > 0 else 0
            
            k1, k2, k3 = st.columns(3)
            rec_delta = f"{recovery - 85.0:.1f}% from Target" if recovery < 85.0 else "Target Met"
            k1.metric("Overall Plant Recovery", f"{recovery:.1f} %", delta=rec_delta, delta_color="normal" if recovery >= 85.0 else "inverse")
            k2.metric("Salt Rejection", f"{rejection:.1f} %")
            k3.metric("Permeate TDS", f"{st.session_state.ro_perm_tds:.1f} ppm", delta="Target: <150 ppm", delta_color="off")
            
            st.divider()
            st.subheader("Guaranteed Treatment Parameters Check")
            guarantees = [
                ("Clarifier Outlet TSS", st.session_state.ro_clarifier_tss, "< 10 ppm", 10.0),
                ("PDMF Outlet TSS", st.session_state.ro_pdmf_tss, "< 3 ppm", 3.0),
                ("SDMF Outlet TSS", st.session_state.ro_sdmf_tss, "< 1 ppm", 1.0),
                ("Softener O/L Hardness", st.session_state.ro_soft_hard, "< 5 ppm as CaCO3", 5.0),
                ("HRU O/L Hardness", st.session_state.ro_hru_hard, "< 1 ppm as CaCO3", 1.0),
                ("Cartridge Filter SDI", st.session_state.ro_sdi, "< 3", 3.0),
                ("RO Permeate COD", st.session_state.ro_perm_cod, "< 10 ppm", 10.0)
            ]
            for name, val, target_text, limit in guarantees:
                col_name, col_val, col_target, col_status = st.columns([2, 1, 1, 1])
                col_name.write(f"**{name}**")
                col_val.write(f"{val}")
                col_target.write(target_text)
                if val < limit: col_status.success("✅ Pass")
                else: col_status.error("🚨 Fail")
                    
            c_name, c_val, c_target, c_status = st.columns([2, 1, 1, 1])
            c_name.write("**RO Permeate pH**")
            c_val.write(f"{st.session_state.ro_perm_ph}")
            c_target.write("7.0 - 7.5")
            if 7.0 <= st.session_state.ro_perm_ph <= 7.5: c_status.success("✅ Pass")
            else: c_status.error("🚨 Fail")
                
        with ro_tabs[2]:
            st.subheader("Chemical Dosing Control")
            st.info("AI-driven Optimal Dose Recommendations currently in development.")
            c1, c2, c3 = st.columns(3)
            with c1:
                st.markdown("### Coagulant (Clarifier)")
                st.session_state.ro_coag_ppm = st.number_input("Target Dosing (PPM)", value=st.session_state.ro_coag_ppm, key="in_ro_coag_ppm")
                st.info(f"**Requirement:** {(st.session_state.ro_feed_flow * st.session_state.ro_coag_ppm) / 1000:.2f} kg/hr")
            with c2:
                st.markdown("### Flocculant (Clarifier)")
                st.session_state.ro_floc_ppm = st.number_input("Target Dosing (PPM)", value=st.session_state.ro_floc_ppm, key="in_ro_floc_ppm")
                st.info(f"**Requirement:** {(st.session_state.ro_feed_flow * st.session_state.ro_floc_ppm) / 1000:.2f} kg/hr")
            with c3:
                st.markdown("### SMBS (RO Feed)")
                st.session_state.ro_smbs_ppm = st.number_input("Target Dosing (PPM)", value=st.session_state.ro_smbs_ppm, key="in_ro_smbs_ppm")
                st.info(f"**Requirement:** {(st.session_state.ro_feed_flow * st.session_state.ro_smbs_ppm) / 1000:.2f} kg/hr")

        with ro_tabs[3]:
            st.subheader("Permeate Normalization Predictor (MRA)")
            st.markdown("Modify process inputs to execute 'What-If' scenarios and check for membrane fouling.")
            controls_col, calc_col = st.columns([1, 2])
            
            with controls_col:
                st.session_state.ro_feed_flow = st.number_input("Feed Flow (m³/hr)", value=st.session_state.ro_feed_flow, key="t5_ro_feed_flow")
                st.session_state.ro_feed_tds = st.number_input("Feed TDS (ppm)", value=st.session_state.ro_feed_tds, key="t5_ro_feed_tds")
                st.session_state.ro_coag_ppm = st.number_input("Coagulant PPM", value=st.session_state.ro_coag_ppm, key="t5_ro_coag_ppm")
                st.session_state.ro_smbs_ppm = st.number_input("SMBS PPM", value=st.session_state.ro_smbs_ppm, key="t5_ro_smbs_ppm")

            with calc_col:
                k1, k2, k3 = st.columns(3)
                k1.metric("Actual Permeate SCADA", f"{ro_mra_data['Actual']:.1f} m³/h")
                k2.metric(f"Normalized Twin ({ro_model_type})", f"{ro_mra_data['Predicted']:.1f} m³/h")
                
                ro_diff_pct = (ro_mra_data['Residual'] / ro_mra_data['Predicted']) * 100 if ro_mra_data['Predicted'] > 0 else 0
                if ro_diff_pct <= -5.0: k3.error(f"Residual: {ro_mra_data['Residual']:.1f} m³/h ({ro_diff_pct:.1f}%) - Membrane CIP Required")
                elif ro_diff_pct <= -4.0: k3.warning(f"Residual: {ro_mra_data['Residual']:.1f} m³/h ({ro_diff_pct:.1f}%) - Warning: Scaling detected")
                else: k3.success(f"Residual: {ro_mra_data['Residual']:.1f} m³/h ({ro_diff_pct:.1f}%) - CLEAN")
                    
                if ro_model_type != "OLS": st.info("ℹ️ **AI Model Active:** Variance Impact breakdown is only available for purely linear OLS models.")
                st.dataframe(ro_mra_data['Variance_DF'].style.format({"Baseline": "{:.1f}", "Live Input": "{:.1f}", "Deviation": "{:+.1f}", "Regression Weight": "{:.3f}", "Impact (m³/h)": "{:+.1f}"}, na_rep="-"), use_container_width=True, hide_index=True)

        with ro_tabs[4]:
            st.subheader("Reporting & Data Logging")
            rep_tabs = st.tabs(["📅 Daily Execution Dashboard", "📆 Master Historical Database", "📊 Long-Term Performance Trends", "📈 Interactive Explorer"])
            
            with rep_tabs[0]:
                m_col1, m_col2, m_col3, m_col4 = st.columns(4)
                m_col1.metric("Target Record Date", log_date.strftime('%d/%m/%Y')) 
                m_col2.metric("Plant Recovery", f"{(st.session_state.ro_perm_flow / st.session_state.ro_feed_flow * 100) if st.session_state.ro_feed_flow > 0 else 0:.1f} %")
                m_col3.metric("Salt Rejection", f"{((st.session_state.ro_feed_tds - st.session_state.ro_perm_tds) / st.session_state.ro_feed_tds * 100) if st.session_state.ro_feed_tds > 0 else 0:.1f} %")
                
                ro_diff_pct = (ro_mra_data['Residual'] / ro_mra_data['Predicted']) * 100 if ro_mra_data['Predicted'] > 0 else 0
                if ro_diff_pct <= -5.0: delta_text, d_color = f"{ro_diff_pct:.1f}% (Fouling Critical)", "inverse"
                elif ro_diff_pct <= -4.0: delta_text, d_color = f"{ro_diff_pct:.1f}% (Deviation Warning)", "inverse"
                else: delta_text, d_color = f"{ro_diff_pct:.1f}% (Clean Baseline)", "normal"
                    
                m_col4.metric("Normalized Flow Gap", f"{ro_mra_data['Residual']:.1f} m³/h", delta=delta_text, delta_color=d_color)
                
                st.divider()
                st.text_area("Remarks & Performance Observations", key="ro_in_remarks", placeholder="Record operational shift anomalies, CEB/CIP clean notes here...")
                
                st.markdown("### 💾 Record and Commit Log Payload")
                c_pwd, c_save, c_export, c_csv = st.columns([1.5, 1, 1, 1])
                with c_pwd: pwd_append = st.text_input("Security Key Access", type="password", key="ro_pwd_append", label_visibility="collapsed", placeholder="🔑 Enter Master Security Password to Commit")
                with c_save:
                    if st.button("💾 Save Operational Record", use_container_width=True):
                        if pwd_append == "12345678":
                            db_dict = {
                                "Date": [log_date_str], 
                                "Feed Flow": [st.session_state.ro_feed_flow], "Permeate Flow": [st.session_state.ro_perm_flow],
                                "Feed TDS": [st.session_state.ro_feed_tds], "Permeate TDS": [st.session_state.ro_perm_tds],
                                "Clarifier TSS": [st.session_state.ro_clarifier_tss], "PDMF TSS": [st.session_state.ro_pdmf_tss],
                                "SDMF TSS": [st.session_state.ro_sdmf_tss], "Softener Hardness": [st.session_state.ro_soft_hard],
                                "HRU Hardness": [st.session_state.ro_hru_hard], "Cartridge SDI": [st.session_state.ro_sdi],
                                "Permeate pH": [st.session_state.ro_perm_ph], "Permeate COD": [st.session_state.ro_perm_cod],
                                "Coagulant PPM": [st.session_state.ro_coag_ppm], "Flocculant PPM": [st.session_state.ro_floc_ppm],
                                "SMBS PPM": [st.session_state.ro_smbs_ppm], 
                                "Recovery": [round((st.session_state.ro_perm_flow / st.session_state.ro_feed_flow * 100) if st.session_state.ro_feed_flow > 0 else 0, 2)],
                                "Rejection": [round(((st.session_state.ro_feed_tds - st.session_state.ro_perm_tds) / st.session_state.ro_feed_tds * 100) if st.session_state.ro_feed_tds > 0 else 0, 2)],
                                "Residual": [round(ro_mra_data['Residual'], 2)], "Remarks": [st.session_state.get('ro_in_remarks', '')]
                            }
                            new_log = pd.DataFrame(db_dict)
                            st.session_state.ro_daily_logs = pd.concat([st.session_state.ro_daily_logs, new_log], ignore_index=True)
                            save_database(db_conn, st.session_state.ro_daily_logs, RO_LOCAL_DB_FILE)
                            st.success("✅ Operational record successfully integrated into file engine!")
                        elif pwd_append != "": st.error("❌ Master verification credential failed.")
                with c_export:
                    word_file = generate_ro_comprehensive_report(log_date, st.session_state, ro_mra_data)
                    st.download_button("📄 Export Word Document (.docx)", data=word_file, file_name=f"RO_ExecutiveReport_{log_date_str}.docx", mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document", use_container_width=True)
                with c_csv:
                    csv_file = generate_ro_daily_csv(log_date, st.session_state, ro_mra_data)
                    st.download_button("📊 Export Tabular Values (.csv)", data=csv_file, file_name=f"RO_DataRecord_{log_date_str}.csv", mime="text/csv", use_container_width=True)

            with rep_tabs[1]:
                st.markdown("#### 📆 Master System Registry Database")
                display_cols = [c for c in RO_EXACT_DB_COLUMNS if c in st.session_state.ro_daily_logs.columns]
                edited_db = st.data_editor(st.session_state.ro_daily_logs[display_cols] if not st.session_state.ro_daily_logs.empty else st.session_state.ro_daily_logs, num_rows="dynamic", use_container_width=True)
                c_sync_pwd, c_sync, c_dl = st.columns([2, 1, 1])
                with c_sync_pwd: pwd_sync = st.text_input("Database Write-Access Password", type="password", key="ro_pwd_sync", label_visibility="collapsed", placeholder="🔑 Enter Database Master Password to Save Modifications")
                with c_sync:
                    if st.button("☁️ Synchronize Registry", use_container_width=True, key="ro_sync_btn"):
                        if pwd_sync == "12345678":
                            st.session_state.ro_daily_logs = edited_db
                            save_database(db_conn, st.session_state.ro_daily_logs, RO_LOCAL_DB_FILE)
                            st.success("✅ Master registry records updated successfully!")
                        else: st.error("❌ System modification credentials failed.")
                with c_dl:
                    st.download_button("📥 Download Database Offline Backup", data=st.session_state.ro_daily_logs.to_csv(index=False).encode('utf-8'), file_name=f"RO_MasterRegistry_Backup.csv", mime='text/csv', use_container_width=True, key="ro_dl_btn")

                st.divider()
                st.markdown("#### 📊 Aggregated Monthly Performance Generator")
                if not st.session_state.ro_daily_logs.empty:
                    df_logs = st.session_state.ro_daily_logs.copy()
                    df_logs['Date'] = pd.to_datetime(df_logs['Date'], dayfirst=True, errors='coerce')
                    month_data = df_logs[(df_logs['Date'].dt.month == log_date.month) & (df_logs['Date'].dt.year == log_date.year)].copy()
                    if not month_data.empty:
                        if st.button("📄 Compile and Generate Monthly Summary (.docx)", use_container_width=True, key="ro_month_btn"):
                            monthly_doc = generate_ro_monthly_report(month_data, log_date.strftime('%B'), str(log_date.year))
                            st.download_button("📥 Download Monthly Briefing Document", data=monthly_doc, file_name=f"RO_MonthlySummary_{log_date.strftime('%b_%Y')}.docx", mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document", key="ro_dl_month")

            with rep_tabs[2]:
                if not st.session_state.ro_daily_logs.empty:
                    df_logs = st.session_state.ro_daily_logs.copy()
                    df_logs['Date'] = pd.to_datetime(df_logs['Date'], dayfirst=True, errors='coerce')
                    
                    min_date = df_logs['Date'].min().date() if not df_logs['Date'].isnull().all() else datetime.date(2023, 1, 1)
                    max_date = df_logs['Date'].max().date() if not df_logs['Date'].isnull().all() else datetime.date.today()
                    
                    st.markdown("##### 📅 Performance Evaluation Horizon Filter")
                    d_col1, d_col2 = st.columns(2)
                    with d_col1: start_date = st.date_input("Start Threshold Date", min_date, key="ro_start_d1")
                    with d_col2: end_date = st.date_input("End Threshold Date", max_date, key="ro_end_d1")
                    
                    mask = (df_logs['Date'].dt.date >= start_date) & (df_logs['Date'].dt.date <= end_date)
                    df_filtered = df_logs.loc[mask]
                    
                    q_col1, q_col2 = st.columns(2)
                    with q_col1:
                        st.markdown("#### 📉 Performance Recovery Trend")
                        if len(df_filtered) > 1:
                            rec_chart = alt.Chart(df_filtered).mark_circle().encode(x=alt.X('Date:T', title="Evaluation Timeline"), y=alt.Y('Recovery:Q', scale=alt.Scale(zero=False)))
                            st.altair_chart(rec_chart + rec_chart.transform_regression('Date', 'Recovery').mark_line(color='red'), use_container_width=True)
                    with q_col2:
                        st.markdown("#### 🌡️ Membrane Normalization Gap (Residual)")
                        if len(df_filtered) > 1:
                            htc_chart = alt.Chart(df_filtered).mark_line(point=True, color='orange').encode(x=alt.X('Date:T', title="Evaluation Timeline"), y=alt.Y('Residual:Q', scale=alt.Scale(zero=False), title="Fouling Residual (m³/h)"))
                            st.altair_chart(htc_chart + htc_chart.transform_regression('Date', 'Residual').mark_line(color='black'), use_container_width=True)

            with rep_tabs[3]:
                st.markdown("#### 📈 Multivariable Cross-Correlation Explorer")
                if not st.session_state.ro_daily_logs.empty:
                    exp_df = st.session_state.ro_daily_logs.copy()
                    exp_df['Date'] = pd.to_datetime(exp_df['Date'], dayfirst=True, errors='coerce')
                    
                    min_date2 = exp_df['Date'].min().date() if not exp_df['Date'].isnull().all() else datetime.date(2023, 1, 1)
                    max_date2 = exp_df['Date'].max().date() if not exp_df['Date'].isnull().all() else datetime.date.today()
                    
                    d_col1, d_col2 = st.columns(2)
                    with d_col1: start_date2 = st.date_input("Start Horizon Date", min_date2, key="ro_start_d2")
                    with d_col2: end_date2 = st.date_input("End Horizon Date", max_date2, key="ro_end_d2")
                    
                    mask2 = (exp_df['Date'].dt.date >= start_date2) & (exp_df['Date'].dt.date <= end_date2)
                    exp_df = exp_df.loc[mask2]
                    
                    num_cols = [col for col in exp_df.columns if col not in ['Date', 'Remarks']]
                    x_c, y_c, t_c = st.columns(3)
                    with x_c: exp_x = st.selectbox("Select Independent Domain X-Axis", ['Date'] + num_cols, index=0, key="ro_x")
                    with y_c: exp_y = st.selectbox("Select Dependent Variable Y-Axis", num_cols, index=0, key="ro_y")
                    with t_c: exp_type = st.selectbox("Select Functional Chart Variant", ["Line Chart", "Scatter Plot", "Bar Chart"], key="ro_chart_type")
                    
                    if exp_type == "Line Chart": chart = alt.Chart(exp_df).mark_line(point=True).encode(x=alt.X(f"{exp_x}{':T' if exp_x == 'Date' else ':Q'}"), y=alt.Y(f"{exp_y}:Q", scale=alt.Scale(zero=False)), tooltip=[exp_x, exp_y])
                    elif exp_type == "Scatter Plot": chart = alt.Chart(exp_df).mark_circle(size=80).encode(x=alt.X(f"{exp_x}{':T' if exp_x == 'Date' else ':Q'}"), y=alt.Y(f"{exp_y}:Q", scale=alt.Scale(zero=False)), tooltip=[exp_x, exp_y])
                    else: chart = alt.Chart(exp_df).mark_bar().encode(x=alt.X(f"{exp_x}{':T' if exp_x == 'Date' else ':N'}"), y=alt.Y(f"{exp_y}:Q"), tooltip=[exp_x, exp_y])
                    st.altair_chart(chart.interactive(), use_container_width=True)
                else:
                    st.info("No active historical registry values detected to perform correlation modeling.")

        with ro_tabs[5]:
            st.subheader("🤖 Machine Learning & OLS Calibration Suite")
            if not SKLEARN_INSTALLED:
                st.error("🚨 Mathematical package 'scikit-learn' is missing from file dependencies.")
            else:
                st.markdown("### 💾 Manage Baseline Evaluation Multipliers")
                st.markdown(f"**Current Evaluator Logic Subroutine:** `{ro_model_type}`")
                c_reset, _ = st.columns([1, 1])
                with c_reset:
                    if st.button("🔄 Execute Subroutine Calibration Factory Reset", use_container_width=True, key="ro_factory_reset"):
                        st.session_state.ro_mra_coef = RO_MRA_COEF_BASE.copy()
                        save_config(db_conn, st.session_state.ro_mra_coef, RO_LOCAL_CONFIG_FILE)
                        st.success("✅ Baseline parameters successfully reverted back to original OLS multipliers!")
                        time.sleep(1.5)
                        st.rerun()

                st.divider()
                st.markdown("### 📊 Multi-Variable Predictive Optimization Logic Model Builder")
                
                req_cols = ["Date", "Permeate Flow", "Feed Flow", "Feed TDS", "Coagulant PPM", "SMBS PPM"]
                template_df = pd.DataFrame(columns=req_cols)
                st.download_button(label="1️⃣ Download Standard Structural Training Template File", data=template_df.to_csv(index=False).encode('utf-8'), file_name='RO_ML_CalibrationTemplate.csv', mime='text/csv', key="ro_train_dl")
                
                st.divider()
                uploaded_file = st.file_uploader("2️⃣ Inject Completed Optimization Dataset", type=["csv"], key="ro_mra_trainer")
                
                if uploaded_file is not None:
                    try:
                        df_train = pd.read_csv(uploaded_file)
                        if not all(col in df_train.columns for col in req_cols): st.error(f"❌ Structural training template verification failed due to parameter column omissions.")
                        else:
                            for col in req_cols:
                                if col != "Date":
                                    if df_train[col].dtype == object: df_train[col] = pd.to_numeric(df_train[col].astype(str).str.replace(',', '', regex=False), errors='coerce')
                            
                            df_train = df_train.dropna(subset=[c for c in req_cols if c != "Date"])
                            st.success(f"✅ Training Initialized successfully utilizing {len(df_train)} localized validation rows.")
                            
                            if len(df_train) > 0:
                                X = df_train[["Feed Flow", "Feed TDS", "Coagulant PPM", "SMBS PPM"]]
                                Y = df_train["Permeate Flow"]
                                
                                model_ols = LinearRegression(fit_intercept=True).fit(X, Y)
                                r2_ols = r2_score(Y, model_ols.predict(X))
                                
                                model_rf = RandomForestRegressor(n_estimators=100, random_state=42).fit(X, Y)
                                r2_rf = r2_score(Y, model_rf.predict(X))
                                
                                if XGB_INSTALLED:
                                    model_xgb = xgb.XGBRegressor(n_estimators=100, random_state=42).fit(X, Y)
                                    r2_xgb = r2_score(Y, model_xgb.predict(X))
                                
                                st.markdown("### 🏆 Algorithm Accuracy Evaluation Matrix")
                                m1, m2, m3 = st.columns(3)
                                m1.metric("1. Linear OLS Fit (R² Coefficient)", f"{r2_ols * 100:.2f}%")
                                m2.metric("2. Random Forest Tree Logic (R²)", f"{r2_rf * 100:.2f}%")
                                if XGB_INSTALLED: m3.metric("3. Extreme Gradient Boost XGB (R²)", f"{r2_xgb * 100:.2f}%")
                                else: m3.warning("Advanced Gradient boosting library dependency not activated.")
                                
                                st.markdown("#### Dynamic Feature Sensitivity Weights / Scaling Coefficients")
                                comp_dict = {
                                    "Parameter": ["Feed_Flow", "Feed_TDS", "Coag_PPM", "SMBS_PPM"],
                                    "OLS (Coefficients)": np.round(model_ols.coef_, 4),
                                    "Random Forest (Importance %)": np.round(model_rf.feature_importances_ * 100, 2)
                                }
                                if XGB_INSTALLED: comp_dict["XGBoost (Importance %)"] = np.round(model_xgb.feature_importances_ * 100, 2)
                                st.dataframe(pd.DataFrame(comp_dict).style.format(precision=4), use_container_width=True, hide_index=True)
                                
                                st.markdown("### 💾 Commit & Lock Mathematical Subroutine Target")
                                opts = ["OLS (Linear)", "Random Forest"]
                                if XGB_INSTALLED: opts.append("XGBoost")
                                selected_model = st.radio("Configure Active Live Prediction Logic Block:", opts, key="ro_model_radio")
                                
                                if st.button("🔥 Confirm and Hardlock Active Operational Subroutine", type="primary", use_container_width=True, key="ro_model_lock"):
                                    if selected_model == "OLS (Linear)":
                                        new_coefs = {
                                            "model_type": "OLS", "Intercept": float(model_ols.intercept_),
                                            "Feed_Flow": float(model_ols.coef_[0]), "Feed_TDS": float(model_ols.coef_[1]), 
                                            "Coag_PPM": float(model_ols.coef_[2]), "SMBS_PPM": float(model_ols.coef_[3])
                                        }
                                        st.session_state.ro_mra_coef = new_coefs
                                        save_config(db_conn, new_coefs, RO_LOCAL_CONFIG_FILE)
                                    else:
                                        target_m = model_rf if selected_model == "Random Forest" else model_xgb
                                        joblib.dump(target_m, RO_AI_MODEL_FILE)
                                        ai_coefs = {
                                            "model_type": selected_model,
                                            "Feed_Flow": float(target_m.feature_importances_[0]), "Feed_TDS": float(target_m.feature_importances_[1]), 
                                            "Coag_PPM": float(target_m.feature_importances_[2]), "SMBS_PPM": float(target_m.feature_importances_[3])
                                        }
                                        st.session_state.ro_mra_coef = ai_coefs
                                        save_config(db_conn, ai_coefs, RO_LOCAL_CONFIG_FILE)
                                        
                                    st.success(f"✅ System evaluation subroutine locked into {selected_model} logic sequence.")
                                    time.sleep(1.5)
                                    st.rerun()
                            else: st.error("🚨 Structural data parsing produced empty float ranges inside parameters.")
                    except Exception as e: st.error(f"Structural data matrix crash: {e}")

        with ro_tabs[6]:
            st.subheader("📤 Batch Log Matrix Ingestion Subroutine")
            st.markdown("Download the target spreadsheet schema file. Copy/pasting raw values in the exact historical Excel configuration is fully supported.")
            
            bulk_template = pd.DataFrame(columns=RO_EXACT_DB_COLUMNS)
            st.download_button(label="1️⃣ Download Schema Verification Template File", data=bulk_template.to_csv(index=False).encode('utf-8'), file_name='RO_BulkMatrixInletSchema.csv', mime='text/csv', key="ro_bulk_dl")
            
            st.divider()
            bulk_file = st.file_uploader("2️⃣ Ingest Completed System Batch File (.csv)", type=["csv"], key="ro_bulk_uploader")
            
            if bulk_file is not None:
                try:
                    df_bulk = pd.read_csv(bulk_file)
                    
                    missing = [c for c in RO_EXACT_DB_COLUMNS if c not in df_bulk.columns]
                    if missing:
                        st.warning(f"⚠️ Omissions detected inside uploaded parameters. Empty slots will auto-fill utilizing historical parameter baseline means. Missing: {', '.join(missing)}")
                        for c in missing: df_bulk[c] = np.nan
                    
                    num_cols = [c for c in RO_EXACT_DB_COLUMNS if c not in ["Date", "Remarks"]]
                    for col in num_cols:
                        if col in df_bulk.columns:
                            if df_bulk[col].dtype == object: df_bulk[col] = pd.to_numeric(df_bulk[col].astype(str).str.replace(',', '', regex=False), errors='coerce')
                    
                    df_bulk = df_bulk.dropna(subset=["Date"])
                    
                    if len(df_bulk) > 0:
                        df_bulk['Feed Flow'] = df_bulk['Feed Flow'].fillna(450.0)
                        df_bulk['Feed TDS'] = df_bulk['Feed TDS'].fillna(2000.0)
                        df_bulk['Coagulant PPM'] = df_bulk['Coagulant PPM'].fillna(2.0)
                        df_bulk['SMBS PPM'] = df_bulk['SMBS PPM'].fillna(3.0)
                        df_bulk['Permeate Flow'] = df_bulk['Permeate Flow'].fillna(0.0)
                        df_bulk['Permeate TDS'] = df_bulk['Permeate TDS'].fillna(90.0)
                        
                        df_bulk['Date_Clean'] = pd.to_datetime(df_bulk['Date'], dayfirst=True, errors='coerce').dt.strftime('%Y-%m-%d')
                        df_bulk['Recovery'] = np.where(df_bulk['Feed Flow'] > 0, df_bulk['Permeate Flow'] / df_bulk['Feed Flow'] * 100, 0)
                        df_bulk['Rejection'] = np.where(df_bulk['Feed TDS'] > 0, (df_bulk['Feed TDS'] - df_bulk['Permeate TDS']) / df_bulk['Feed TDS'] * 100, 0)

                        if ro_model_type == "OLS":
                            df_bulk['Predicted'] = (
                                ro_coefs["Intercept"] + 
                                (ro_coefs["Feed_Flow"] * df_bulk['Feed Flow']) + 
                                (ro_coefs["Feed_TDS"] * df_bulk['Feed TDS']) + 
                                (ro_coefs["Coag_PPM"] * df_bulk['Coagulant PPM']) + 
                                (ro_coefs["SMBS_PPM"] * df_bulk['SMBS PPM'])
                            )
                        else:
                            try:
                                active_model = joblib.load(RO_AI_MODEL_FILE)
                                bulk_input_df = df_bulk[['Feed Flow', 'Feed TDS', 'Coagulant PPM', 'SMBS PPM']].copy()
                                bulk_input_df.columns = ["Feed_Flow", "Feed_TDS", "Coag_PPM", "SMBS_PPM"]
                                df_bulk['Predicted'] = active_model.predict(bulk_input_df)
                            except: df_bulk['Predicted'] = 0.0
                                
                        df_bulk['Residual'] = df_bulk['Permeate Flow'] - df_bulk['Predicted']
                        
                        db_ready_dict = {
                            "Date": df_bulk['Date_Clean'], 
                            "Feed Flow": df_bulk['Feed Flow'], "Permeate Flow": df_bulk['Permeate Flow'],
                            "Feed TDS": df_bulk['Feed TDS'], "Permeate TDS": df_bulk['Permeate TDS'],
                            "Clarifier TSS": df_bulk['Clarifier TSS'].fillna(8.0), "PDMF TSS": df_bulk['PDMF TSS'].fillna(2.0),
                            "SDMF TSS": df_bulk['SDMF TSS'].fillna(0.5), "Softener Hardness": df_bulk['Softener Hardness'].fillna(4.0),
                            "HRU Hardness": df_bulk['HRU Hardness'].fillna(0.5), "Cartridge SDI": df_bulk['Cartridge SDI'].fillna(2.5),
                            "Permeate pH": df_bulk['Permeate pH'].fillna(7.2), "Permeate COD": df_bulk['Permeate COD'].fillna(8.0),
                            "Coagulant PPM": df_bulk['Coagulant PPM'], "Flocculant PPM": df_bulk['Flocculant PPM'].fillna(1.0),
                            "SMBS PPM": df_bulk['SMBS PPM'], "Recovery": df_bulk['Recovery'].round(2),
                            "Rejection": df_bulk['Rejection'].round(2), "Residual": df_bulk['Residual'].round(1),
                            "Remarks": df_bulk['Remarks'].fillna("")
                        }
                                
                        db_ready_df = pd.DataFrame(db_ready_dict)
                        
                        st.success(f"✅ Dynamic verification evaluation complete for {len(db_ready_df)} matrix rows.")
                        st.dataframe(db_ready_df.style.format(precision=2), use_container_width=True, hide_index=True)
                        
                        st.markdown("### 💾 Append Transferred Batch Elements")
                        c_pwd, c_save = st.columns([2, 2])
                        with c_pwd: pwd_bulk = st.text_input("Security Key Verification Entry", type="password", key="ro_pwd_bulk", label_visibility="collapsed", placeholder="🔑 Enter Master Security Password to Commit Batch Ingestion")
                        with c_save:
                            if st.button("🔄 Append Ingested Records into Registry", use_container_width=True, key="ro_bulk_save"):
                                if pwd_bulk == "12345678":
                                    st.session_state.ro_daily_logs = pd.concat([st.session_state.ro_daily_logs, db_ready_df], ignore_index=True)
                                    st.session_state.ro_daily_logs = st.session_state.ro_daily_logs.drop_duplicates(subset=['Date'], keep='last').reset_index(drop=True)
                                    save_database(db_conn, st.session_state.ro_daily_logs, RO_LOCAL_DB_FILE)
                                    st.success("✅ Batch matrix rows integrated securely within master registry file!")
                                    time.sleep(1.5)
                                    st.rerun()
                                elif pwd_bulk != "": st.error("❌ Identification credentials mismatched.")
                    else: st.error("🚨 Data matrix parse sequence returned zero active rows.")
                except Exception as e: st.error(f"Structural verification crash during upload parsing: {e}")
        
        render_chatbot()
        return # RO Execution ends here

    # ==========================================
    # VERIFIED UNTOUCHED MED-4 APPLICATION CORE
    # ==========================================
    elif utility_choice == "Multi-Effect Distillation (MED)":
        med_unit_choice = st.sidebar.selectbox("Select Active Unit Train", [f"MED-{unit_idx}" for unit_idx in range(1, 12)], index=3)
        if med_unit_choice != "MED-4":
            st.title(f"🏭 {med_unit_choice} Diagnostic Interface")
            st.info(f"🚧 **Work in Progress:** System data hooks for {med_unit_choice} are under configuration. Diagnostic dashboard metrics will become available upon plant startup.")
            render_chatbot()
            return
            
    st.sidebar.divider()
    log_date = st.sidebar.date_input("Date", datetime.date.today(), format="DD/MM/YYYY")
    log_date_str = log_date.strftime('%Y-%m-%d')
    
    if 'last_selected_date' not in st.session_state: 
        st.session_state.last_selected_date = None

    if log_date_str != st.session_state.last_selected_date:
        st.session_state.last_selected_date = log_date_str
        if not st.session_state.daily_logs.empty and 'Date' in st.session_state.daily_logs.columns:
            db_dates = pd.to_datetime(st.session_state.daily_logs['Date'], errors='coerce').dt.strftime('%Y-%m-%d').values
            if log_date_str in db_dates:
                row_idx = np.where(db_dates == log_date_str)[0][0]
                row = st.session_state.daily_logs.iloc[row_idx]
                
                db_to_var_mapping = {
                    'gross': ['Gross production', 'Gross Prod (m3/h)', 'Gross Prod'], 
                    'desal': ['Desal production', 'Desal (m3/h)'], 
                    'steam': ['LP Steam consumption', 'Steam (TPH)'],
                    'sw_total': ['Sea Water Feed', 'Total Sea Water Feed (FFC)', 'Total SW Feed (m3/h)', 'SW Feed (m3/h)'], 
                    'sw_upper': ['Sea Water Upper', '1st Effect SW Feed', 'SW Feed to 1st Effect (m3/h)', 'SW_Upper'],
                    'sw_lower': ['Sea Water Lower'],
                    'cond_flow': ['condensate flow', 'Cond_Flow'], 
                    'cond_temp': ['condensate temp', 'Cond_Temp'],
                    'sw_out_t': ['Sea Water o/L temp', 'SW_Out_Temp'], 
                    'cw_supply': ['CW supply', 'CW_Supply'], 
                    'sw_return': ['SW return', 'SW_Return'],
                    'chem_anti_cons': ['Antiscalant (kg)'], 
                    'chem_foam_cons': ['Antifoam (kg)'], 
                    'mra_press': ['1st effect vapour pressure', 'Press_1st'], 
                    'mra_t1': ['1st effect vapour temp', 'Temp_1st'], 
                    'mra_bt1': ['1st effect brine temp', 'Brine_Temp_1st'], 
                    'brine_ret': ['Brine Water Return', 'Brine_Flow'], 
                    'stm_in_t': ['Steam inlet temp', 'Steam_Temp'], 
                    'chem_anti_ppm': ['Anti_PPM'], 
                    'sw_in_t': ['Sea Water cond I/L temp', 'SW Cond I/L Temp', 'SW_Cond_Inlet_Temp', 'SW_In_Temp'], 
                    'brine_out_t': ['Brine outlet temp', 'Final Brine Temp', 'Final_Brine_Temp', 'Brine_Out_Temp'], 
                    'vap_out_t': ['Vap_Out_Temp'], 
                    'remarks': ['Remarks'],
                    'area_1st': ['Area_1st'], 
                    'area_overall': ['Area_Overall']
                }
                
                for cat in ['Feed', 'Product']:
                    for param, d in WATER_SPECS[cat].items(): 
                        db_to_var_mapping[d['var']] = [d['db_col']]

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
                                    st.session_state.vars[var_key] = val
                                    for tk in SYNC_MAP.get(var_key, []): 
                                        st.session_state[tk] = val
                                    loaded_vars = True
                                break
                            except: 
                                pass 
                if loaded_vars: 
                    st.sidebar.success(f"📅 Auto-loaded historical data for {log_date.strftime('%d/%m/%Y')}")

    # Display MED-4 Title
    st.title("🏭 MED-4 Management Suite")

    tabs = st.tabs([
        "📥 0. Inputs", "🌊 1. KPIs", "🔥 2. HTC", "🧪 3. Quality", 
        "🛢️ 4. Chemicals", "🧠 5. MRA", "📂 6. Reporting", 
        "🤖 7. AI Model Select", "📤 8. Bulk Uploads"
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
        'Stm In_overall': get_v('stm_in_t'), 
        'Vap Out_overall': get_v('vap_out_t'),
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
    
    # THE FIX: Added safety net to ensure columns exist before filtering to prevent KeyError
    for col in ["Base Vapor (°C)", "Live Vapor (°C)", "Base Brine (°C)", "Live Brine (°C)", "Base HTC"]:
        if col not in display_effect_df.columns:
            display_effect_df[col] = np.nan
            
    display_effect_df = display_effect_df[["Effect ID", "Base Vapor (°C)", "Live Vapor (°C)", "Base Brine (°C)", "Live Brine (°C)", "Base HTC"]]

    try: 
        brine_4_to_7_avg = st.session_state.shared_effect_df[st.session_state.shared_effect_df['Effect ID'].isin(['Effect 4', 'Effect 5', 'Effect 6', 'Effect 7'])]['Live Brine (°C)'].mean()
    except: 
        brine_4_to_7_avg = 55.0

    ops_data['dt_1st'] = get_v('mra_t1') - get_v('mra_bt1')
    ops_data['q_1st'] = get_v('sw_upper') * ops_data['dt_1st'] * 0.930 
    ops_data['htc_1st'] = (ops_data['q_1st'] / (get_v('area_1st') * ops_data['dt_1st'])) * 1000 if ops_data['dt_1st'] > 0 and get_v('area_1st') > 0 else 0
    ops_data['fouling_1st'] = 1 / ops_data['htc_1st'] if ops_data['htc_1st'] > 0 else 0

    ops_data['dt_overall'] = get_v('mra_t1') - get_v('brine_out_t')
    ops_data['q_overall'] = get_v('sw_total') * (get_v('brine_out_t') - get_v('sw_in_t')) * 0.930
    ops_data['htc_overall'] = (ops_data['q_overall'] / (get_v('area_overall') * ops_data['dt_overall'])) * 1000 if ops_data['dt_overall'] > 0 and get_v('area_overall') > 0 else 0
    ops_data['fouling_overall'] = 1 / ops_data['htc_overall'] if ops_data['htc_overall'] > 0 else 0

    mra_data = {}
    coefs = st.session_state.mra_coef 
    model_type = coefs.get("model_type", "OLS")
    
    live_input_arr = [get_v('mra_press'), get_v('mra_t1'), get_v('sw_upper'), get_v('mra_bt1'), get_v('brine_ret'), get_v('steam'), get_v('stm_in_t'), get_v('chem_anti_ppm')]
    
    if model_type == "OLS":
        mra_data['Predicted'] = (
            coefs["Intercept"] + 
            (coefs["Press_1st"] * live_input_arr[0]) + 
            (coefs["Temp_1st"] * live_input_arr[1]) + 
            (coefs["SW_Upper"] * live_input_arr[2]) + 
            (coefs["Brine_Temp_1st"] * live_input_arr[3]) + 
            (coefs["Brine_Flow"] * live_input_arr[4]) + 
            (coefs["LP_Steam"] * live_input_arr[5]) + 
            (coefs["Steam_Temp"] * live_input_arr[6]) + 
            (coefs.get("Anti_PPM", MRA_COEF_2014["Anti_PPM"]) * live_input_arr[7])
        )
    else:
        try:
            active_model = joblib.load(AI_MODEL_FILE)
            live_df = pd.DataFrame([live_input_arr], columns=["Press_1st", "Temp_1st", "SW_Upper", "Brine_Temp_1st", "Brine_Flow", "LP_Steam", "Steam_Temp", "Anti_PPM"])
            mra_data['Predicted'] = float(active_model.predict(live_df)[0])
        except: 
            mra_data['Predicted'] = 0.0
            
    mra_data['Actual'] = ops_data['Gross Prod']
    mra_data['Residual'] = mra_data['Actual'] - mra_data['Predicted']

    var_data = []
    param_keys = ["Press_1st", "Temp_1st", "SW_Upper", "Brine_Temp_1st", "Brine_Flow", "LP_Steam", "Steam_Temp", "Anti_PPM"]
    param_names = ["1st effect vapour pressure", "1st effect vapour temp", "Sea Water Upper", "1st effect brine temp", "Brine Water Return", "LP Steam consumption", "Steam inlet temp", "Antiscalant PPM"]
    
    for i in range(8):
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
            status = "✅ Pass" if details['lim'][0] <= val <= details['lim'][1] else "🚨 Fail"
            water_data[cat][param] = {'min': details['lim'][0], 'max': details['lim'][1], 'val': val, 'status': status, 'db_col': details['db_col']}
            
    chem_data = {
        'anti_ppm': get_v('chem_anti_ppm'), 
        'anti_cons': get_v('chem_anti_cons'), 
        'foam_ppm': get_v('chem_foam_ppm'), 
        'foam_cons': get_v('chem_foam_cons')
    }

    # --- TAB 0: INPUTS & PFD ---
    with tabs[0]:
        tab0_subtabs = st.tabs(["📋 Data Entry", "🗺️ Live PFD Monitor"])
        
        with tab0_subtabs[0]:
            st.subheader("Central Data Entry Panel")
            if mra_data['Predicted'] > 950: 
                st.warning("⚠️ **MRA Prediction is unusually high (>950 m³/h).** Please verify you did not accidentally enter the 'Sea Water Feed' (~2100) into the 'Sea Water Upper' (~550) input.")
                
            with st.expander("1. Hydraulics & Mass Balance", expanded=True):
                c1, c2, c3, c4 = st.columns(4)
                with c1:
                    st.number_input("LP Steam consumption (TPH)", key="in_steam", on_change=sync_var, args=('steam', 'in_steam'))
                    st.number_input("Sea Water Upper (m³/h)", key="in_sw_up", on_change=sync_var, args=('sw_upper', 'in_sw_up'))
                with c2:
                    st.number_input("Sea Water Feed (m³/h)", key="in_sw_tot", on_change=sync_var, args=('sw_total', 'in_sw_tot'))
                    st.number_input("Sea Water Lower (m³/h)", key="in_sw_low", on_change=sync_var, args=('sw_lower', 'in_sw_low'))
                with c3:
                    st.number_input("Gross production (m³/h)", key="in_gross", on_change=sync_var, args=('gross', 'in_gross'))
                    st.number_input("Desal production (m³/h)", key="in_desal", on_change=sync_var, args=('desal', 'in_desal'))
                with c4:
                    st.number_input("Brine Water Return (m³/h)", key="in_brine", on_change=sync_var, args=('brine_ret', 'in_brine'))
                    
            with st.expander("2. Plant Temperatures & Pressures", expanded=False):
                t1, t2, t3, t4 = st.columns(4)
                with t1: 
                    st.number_input("Sea Water cond I/L temp (°C)", key="in_sw_in", on_change=sync_var, args=('sw_in_t', 'in_sw_in'))
                    st.number_input("Sea Water o/L temp (°C)", key="in_sw_out", on_change=sync_var, args=('sw_out_t', 'in_sw_out'))
                    st.number_input("CW supply", key="in_cw_supply", on_change=sync_var, args=('cw_supply', 'in_cw_supply'))
                with t2: 
                    st.number_input("Brine outlet temp (°C)", key="in_brine_out", on_change=sync_var, args=('brine_out_t', 'in_brine_out'))
                    st.number_input("SW return", key="in_sw_return", on_change=sync_var, args=('sw_return', 'in_sw_return'))
                    st.number_input("1st effect vapour pressure (mmHg)", key="in_press", on_change=sync_var, args=('mra_press', 'in_press'))
                with t3: 
                    st.number_input("Steam inlet temp (°C)", key="in_stm_in", on_change=sync_var, args=('stm_in_t', 'in_stm_in'))
                    st.number_input("1st effect vapour temp (°C)", key="in_t1", on_change=sync_var, args=('mra_t1', 'in_t1'))
                    st.number_input("1st effect brine temp (°C)", key="in_bt1", on_change=sync_var, args=('mra_bt1', 'in_bt1'))
                with t4: 
                    st.number_input("condensate flow", key="in_cond_flow", on_change=sync_var, args=('cond_flow', 'in_cond_flow'))
                    st.number_input("condensate temp", key="in_cond_temp", on_change=sync_var, args=('cond_temp', 'in_cond_temp'))
                    
            with st.expander("3. Effect-wise Cascade (Temperatures)", expanded=False):
                st.checkbox("Skip Effect-wise Temperatures for today", key="in_skip_eff", on_change=sync_var, args=('skip_eff', 'in_skip_eff'))
                if not get_v('skip_eff'):
                    e_df = st.data_editor(display_effect_df, key="in_effect_df", use_container_width=True, hide_index=True, disabled=["Effect ID", "Base Vapor (°C)", "Base Brine (°C)", "Base HTC"])
                    if not e_df[["Live Vapor (°C)", "Live Brine (°C)"]].equals(st.session_state.shared_effect_df[["Live Vapor (°C)", "Live Brine (°C)"]]):
                        st.session_state.shared_effect_df["Live Vapor (°C)"] = e_df["Live Vapor (°C)"]
                        st.session_state.shared_effect_df["Live Brine (°C)"] = e_df["Live Brine (°C)"]
                        st.rerun()
                        
            with st.expander("4. Laboratory Water Analysis", expanded=False):
                st.checkbox("Skip Water Analysis for today", key="in_skip_wq", on_change=sync_var, args=('skip_wq', 'in_skip_wq'))
                if not get_v('skip_wq'):
                    w_col1, w_col2 = st.columns(2)
                    with w_col1:
                        st.markdown("**Feed Water**")
                        for p, d in WATER_SPECS["Feed"].items(): 
                            st.number_input(f"{p}", key=f"in_{d['var']}", on_change=sync_var, args=(d['var'], f"in_{d['var']}"))
                    with w_col2:
                        st.markdown("**Desal Product**")
                        for p, d in WATER_SPECS["Product"].items(): 
                            st.number_input(f"{p}", key=f"in_{d['var']}", on_change=sync_var, args=(d['var'], f"in_{d['var']}"))
                        
            with st.expander("5. Chemical Dosing", expanded=False):
                st.markdown("**Kem Watreat r 3687 (Antiscalant)**")
                ch1, ch2 = st.columns(2)
                with ch1: 
                    st.number_input("Dosing Level (PPM)", key="in_anti_ppm", on_change=sync_var, args=('chem_anti_ppm', 'in_anti_ppm'))
                    if st.button("🧪 Auto-Calculate Optimal Dose", key="btn_auto_anti_0"): 
                        st.info("🚀 AI-driven Thermodynamic Scaling Engine & Auto-Dosing will be available shortly!")
                with ch2: 
                    st.number_input("Actual Consumption (kg/hr)", key="in_anti_cons", on_change=sync_var, args=('chem_anti_cons', 'in_anti_cons'))
                    
                st.markdown("**Kem Antifoam 1795**")
                ch3, ch4 = st.columns(2)
                with ch3: 
                    st.number_input("Dosing Level (PPM)", key="in_foam_ppm", on_change=sync_var, args=('chem_foam_ppm', 'in_foam_ppm'))
                    if st.button("🧪 Auto-Calculate Optimal Dose", key="btn_auto_foam_0"): 
                        st.info("🚀 AI-driven Thermodynamic Scaling Engine & Auto-Dosing will be available shortly!")
                with ch4: 
                    st.number_input("Actual Consumption (kg/hr)", key="in_foam_cons", on_change=sync_var, args=('chem_foam_cons', 'in_foam_cons'))

        with tab0_subtabs[1]:
            st.markdown("### Process Flow Diagram - Live Tags")
            if PIL_INSTALLED and (os.path.exists("Desal PFD (1).TIF") or os.path.exists("Desal PFD (1).tiff") or os.path.exists("Desal PFD.TIF")):
                try:
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
                            Sea Water o/L temp: {get_v('sw_out_t')} °C<br>
                            CW supply: {get_v('cw_supply')}
                        </div>
                        
                        <div style="position: absolute; top: 5%; right: 2%; background: rgba(50,0,0,0.85); color: #ff3333; padding: 6px 12px; font-family: monospace; border: 1px solid #ff3333; border-radius: 4px; box-shadow: 0 0 8px #ff3333; font-size: 13px;">
                            <strong>STEAM & 1ST EFFECT</strong><br>
                            LP Steam consumption: {ops_data['Steam']} TPH<br>
                            Steam inlet temp: {ops_data['Stm In_overall']} °C<br>
                            1st effect vapour temp: {ops_data['Stm In_1st']} °C<br>
                            1st effect vapour pressure: {ops_data['Press_1st']} mmHg<br>
                            1st effect brine temp: {ops_data['Brine_1st']} °C<br>
                            Delta T: {ops_data['dt_1st']:.2f} °C
                        </div>

                        <div style="position: absolute; bottom: 5%; left: 2%; background: rgba(0,50,50,0.85); color: #00ffff; padding: 6px 12px; font-family: monospace; border: 1px solid #00ffff; border-radius: 4px; box-shadow: 0 0 8px #00ffff; font-size: 13px;">
                            <strong>PRODUCTION</strong><br>
                            Gross production: {ops_data['Gross Prod']} m³/h<br>
                            Desal production: {ops_data['Desal']} m³/h<br>
                            condensate flow: {get_v('cond_flow')}<br>
                            condensate temp: {get_v('cond_temp')} °C
                        </div>
                        
                        <div style="position: absolute; bottom: 5%; right: 2%; background: rgba(50,25,0,0.85); color: #ff9900; padding: 6px 12px; font-family: monospace; border: 1px solid #ff9900; border-radius: 4px; box-shadow: 0 0 8px #ff9900; font-size: 13px;">
                            <strong>BRINE SYSTEM</strong><br>
                            Brine Water Return: {ops_data['Brine Return']} m³/h<br>
                            Brine outlet temp: {ops_data['Brine Out_overall']} °C<br>
                            SW return: {get_v('sw_return')}
                        </div>
                    </div>
                    """
                    st.components.v1.html(html_view, height=800)
                except Exception as e:
                    st.error(f"Could not render TIF overlay. Error: {e}")
            else:
                st.info("📌 **Digital Twin HUD:** Please upload 'Desal PFD (1).TIF' into the application directory to unlock the live interactive diagram overlay.")

    # --- TAB 1: FLOW KPIs ---
    with tabs[1]:
        st.subheader("Mass Balance & KPI Dashboard")
        c1, c2, c3 = st.columns(3)
        with c1:
            st.number_input("LP Steam consumption (TPH)", key="t1_steam", on_change=sync_var, args=('steam', 't1_steam'))
            st.number_input("Sea Water Upper (m³/h)", key="t1_sw_up", on_change=sync_var, args=('sw_upper', 't1_sw_up'))
        with c2:
            st.number_input("Desal production (m³/h)", key="t1_desal", on_change=sync_var, args=('desal', 't1_desal'))
            st.number_input("Sea Water Feed (m³/h)", key="t1_sw_tot", on_change=sync_var, args=('sw_total', 't1_sw_tot'))
        with c3:
            st.number_input("Gross production (m³/h)", key="t1_gross", on_change=sync_var, args=('gross', 't1_gross'))
            st.number_input("Brine Water Return (m³/h)", key="t1_brine", on_change=sync_var, args=('brine_ret', 't1_brine'))
            
        st.divider()
        kpi1, kpi2, kpi3, kpi4, kpi5 = st.columns(5)
        kpi1.metric("GOR", f"{ops_data['GOR']:.2f}:1")
        kpi2.metric("Steam Economy", f"{ops_data['Economy']:.4f}")
        kpi3.metric("System Recovery", f"{ops_data['Recovery']:.1f} %")
        kpi4.metric("Conversion Ratio", f"{ops_data['Conversion']:.3f}")
        kpi5.metric("STEC", f"{ops_data['STEC']:.1f} kWh/t")

    # --- TAB 2: OVERALL HTC ---
    with tabs[2]:
        st.subheader("Thermal Integrity & Fouling Analysis")
        col1, col2 = st.columns(2)
        with col1:
            st.markdown("### ⚙️ 1st Effect HTC Performance")
            st.markdown("Calculates scaling specifically inside the hottest effect stage.")
            st.number_input("1st Effect Surface Area (m²)", key="t2_area_1st", on_change=sync_var, args=('area_1st', 't2_area_1st'))
            st.number_input("Sea Water Upper (m³/h)", key="t2_sw_up", on_change=sync_var, args=('sw_upper', 't2_sw_up'))
            st.number_input("1st effect vapour temp (°C)", key="t2_t1", on_change=sync_var, args=('mra_t1', 't2_t1'))
            st.number_input("1st effect brine temp (°C)", key="t2_bt1", on_change=sync_var, args=('mra_bt1', 't2_bt1'))
            st.divider()
            st.metric("1st Effect ΔT", f"{ops_data['dt_1st']:.2f} °C")
            st.metric("1st Effect Q (Heat Load)", f"{ops_data['q_1st']:,.0f} Kcal/hr")
            st.metric("1st Effect HTC (U)", f"{ops_data['htc_1st']:.2f} W/m²K")
            st.metric("1st Effect Fouling Factor", f"{ops_data['fouling_1st']:.6f}")

        with col2:
            st.markdown("### 🏭 Overall Plant HTC Performance")
            st.markdown("Calculates averaged baseline fouling rates across all tube banks.")
            st.number_input("Overall Surface Area (m²)", key="t2_area_overall", on_change=sync_var, args=('area_overall', 't2_area_overall'))
            st.number_input("Sea Water Feed (m³/h)", key="t2_sw_tot", on_change=sync_var, args=('sw_total', 't2_sw_tot'))
            st.number_input("Sea Water cond I/L temp (°C)", key="t2_sw_in", on_change=sync_var, args=('sw_in_t', 't2_sw_in'))
            st.number_input("Brine outlet temp (°C)", key="t2_brine_out", on_change=sync_var, args=('brine_out_t', 't2_brine_out'))
            st.divider()
            st.metric("Overall ΔT", f"{ops_data['dt_overall']:.2f} °C")
            st.metric("Overall Q (Heat Load)", f"{ops_data['q_overall']:,.0f} Kcal/hr")
            st.metric("Overall HTC (U)", f"{ops_data['htc_overall']:.2f} W/m²K")
            st.metric("Overall Fouling Factor", f"{ops_data['fouling_overall']:.6f}")

    # --- TAB 3: WATER ANALYSIS TAB ---
    with tabs[3]:
        st.subheader("Laboratory Analysis Evaluation")
        if not get_v('skip_wq'):
            w_col1, w_col2 = st.columns(2)
            with w_col1:
                st.markdown("### 🌊 Intake Seawater Matrix")
                for param, d in WATER_SPECS["Feed"].items():
                    c_in, c_chk = st.columns([2, 2])
                    with c_in: 
                        st.number_input(f"{param} ({d['lim'][0]}-{d['lim'][1]})", key=f"t3_{d['var']}", on_change=sync_var, args=(d['var'], f"t3_{d['var']}"))
                    c_chk.markdown(f"<div style='margin-top:30px'>{water_data['Feed'][param]['status']}</div>", unsafe_allow_html=True)
            with w_col2:
                st.markdown("### 🚰 Product Distillate Matrix")
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
            st.markdown("### 🧪 Kem Watreat r 3687 (Antiscalant Evaluation)")
            st.number_input("Target Dosing Level (PPM)", key="t4_anti_ppm", on_change=sync_var, args=('chem_anti_ppm', 't4_anti_ppm'))
            if st.button("🧪 Auto-Calculate Optimal Dose", key="btn_auto_anti_4"): 
                st.info("🚀 AI-driven Thermodynamic Scaling Engine & Auto-Dosing will be available shortly!")
            theo_anti = (ops_data['SW Total'] * get_v('chem_anti_ppm')) / 1000
            st.info(f"**Theoretical Flow Target Requirements:** {theo_anti:.2f} kg/hr")
            st.number_input("Actual Consumption (kg/hr)", key="t4_anti_cons", on_change=sync_var, args=('chem_anti_cons', 't4_anti_cons'))
        with cc2:
            st.markdown("### 🫧 Kem Antifoam 1795 Performance")
            st.number_input("Target Dosing Level (PPM)", key="t4_foam_ppm", on_change=sync_var, args=('chem_foam_ppm', 't4_foam_ppm'))
            if st.button("🧪 Auto-Calculate Optimal Dose", key="btn_auto_foam_4"): 
                st.info("🚀 AI-driven Thermodynamic Scaling Engine & Auto-Dosing will be available shortly!")
            theo_foam = (ops_data['SW Total'] * get_v('chem_foam_ppm')) / 1000
            st.info(f"**Theoretical Flow Target Requirements:** {theo_foam:.2f} kg/hr")
            st.number_input("Actual Consumption (kg/hr)", key="t4_foam_cons", on_change=sync_var, args=('chem_foam_cons', 't4_foam_cons'))

    # --- TAB 5: MRA EVALUATION ENGINE ---
    with tabs[5]:
        st.subheader("Multi-Variable Normalization Predictor")
        st.markdown("Modify process inputs to execute 'What-If' scenarios. Input limits dynamically unbind to prevent system crashes.")
        controls_col, calc_col = st.columns([1, 2])
        
        with controls_col:
            st.number_input("1st effect vapour pressure (mmHg)", key="t5_press", on_change=sync_var, args=('mra_press', 't5_press'))
            st.number_input("1st effect vapour temp (°C)", key="t5_t1", on_change=sync_var, args=('mra_t1', 't5_t1'))
            st.number_input("Sea Water Upper (m³/h)", key="t5_sw_up", on_change=sync_var, args=('sw_upper', 't5_sw_up'))
            st.number_input("1st effect brine temp (°C)", key="t5_bt1", on_change=sync_var, args=('mra_bt1', 't5_bt1'))
            st.number_input("Brine Water Return (m³/h)", key="t5_bflow", on_change=sync_var, args=('brine_ret', 't5_bflow'))
            st.number_input("LP Steam consumption (TPH)", key="t5_steam", on_change=sync_var, args=('steam', 't5_steam'))
            st.number_input("Steam inlet temp (°C)", key="t5_stm_t", on_change=sync_var, args=('stm_in_t', 't5_stm_t'))
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
                st.info("ℹ️ **Machine Learning Evaluation Mode Active:** Multi-variable parameter expansion is only available under pure linear OLS logic.")
            st.dataframe(mra_data['Variance_DF'].style.format({"Baseline": "{:.1f}", "Live Input": "{:.1f}", "Deviation": "{:+.1f}", "Regression Weight": "{:.3f}", "Impact (TPH)": "{:+.1f}"}, na_rep="-"), use_container_width=True, hide_index=True)

    # --- TAB 6: REPORTING & ANALYTICS ---
    with tabs[6]:
        st.subheader("Central Data Logging & Historical Analytics")
        rep_tabs = st.tabs(["📅 Daily Execution Dashboard", "📆 Master Historical Database", "📊 Long-Term Performance Trends", "📈 Interactive Explorer"])
        
        with rep_tabs[0]:
            m_col1, m_col2, m_col3, m_col4 = st.columns(4)
            m_col1.metric("Target Record Date", log_date.strftime('%d/%m/%Y')) 
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
                    st.markdown("#### ⚖️ Parameter Deviation Impact (m³/h)")
                    impact_chart = alt.Chart(mra_data['Variance_DF']).mark_bar().encode(
                        x=alt.X('Impact (TPH):Q'), 
                        y=alt.Y('Parameter:N', sort='-x', title=''), 
                        color=alt.condition(alt.datum['Impact (TPH)'] > 0, alt.value('#2ca02c'), alt.value('#d62728')), 
                        tooltip=['Parameter', 'Impact (TPH)']
                    ).properties(height=300)
                    st.altair_chart(impact_chart, use_container_width=True)
                else:
                    st.markdown("#### ⚖️ Component Weight Importance (ML Mode)")
                    impact_chart = alt.Chart(mra_data['Variance_DF']).mark_bar(color='#1f77b4').encode(
                        x=alt.X('Regression Weight:Q', title="Importance Weight Matrix %"), 
                        y=alt.Y('Parameter:N', sort='-x', title=''), 
                        tooltip=['Parameter', 'Regression Weight']
                    ).properties(height=300)
                    st.altair_chart(impact_chart, use_container_width=True)

            with graph_col2:
                st.markdown("#### 🌊 Mass Distribution Profile")
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
            
            st.markdown("### 💾 Record and Commit Log Payload")
            c_pwd, c_save, c_export, c_csv = st.columns([1.5, 1, 1, 1])
            with c_pwd: 
                pwd_append = st.text_input("Security Key Access", type="password", key="pwd_append", label_visibility="collapsed", placeholder="🔑 Enter Master Security Password to Commit")
            with c_save:
                if st.button("💾 Save Operational Record", use_container_width=True):
                    if pwd_append == "12345678":
                        db_dict = {
                            "Date": [log_date_str], 
                            "Sea Water Upper": [get_v('sw_upper')], 
                            "Sea Water Lower": [get_v('sw_lower')],
                            "Sea Water Feed": [ops_data['SW Total']], 
                            "Brine Water Return": [ops_data['Brine Return']], 
                            "Desal production": [ops_data['Desal']], 
                            "LP Steam consumption": [ops_data['Steam']],
                            "condensate flow": [get_v('cond_flow')], 
                            "condensate temp": [get_v('cond_temp')],
                            "1st effect vapour temp": [get_v('mra_t1')], 
                            "1st effect brine temp": [get_v('mra_bt1')], 
                            "Delta T": [ops_data['dt_1st']], 
                            "1st effect vapour pressure": [get_v('mra_press')], 
                            "Steam inlet temp": [get_v('stm_in_t')], 
                            "Brine outlet temp": [get_v('brine_out_t')], 
                            "Sea Water cond I/L temp": [get_v('sw_in_t')], 
                            "Sea Water o/L temp": [get_v('sw_out_t')], 
                            "CW supply": [get_v('cw_supply')], 
                            "SW return": [get_v('sw_return')], 
                            "Gross production": [ops_data['Gross Prod']],
                            "GOR": [round(ops_data['GOR'], 2)], 
                            "Overall HTC": [round(ops_data['htc_overall'], 2)], 
                            "1st Effect HTC": [round(ops_data['htc_1st'], 2)], 
                            "Residual": [round(mra_data['Residual'], 1)], 
                            "Antiscalant (kg)": [chem_data['anti_cons']], 
                            "Antifoam (kg)": [chem_data['foam_cons']], 
                            "Anti_PPM": [get_v('chem_anti_ppm')], 
                            "Area_1st": [get_v('area_1st')], 
                            "Area_Overall": [get_v('area_overall')], 
                            "Remarks": [get_v('remarks')]
                        }
                        for cat in ['Feed', 'Product']:
                            for param, details in WATER_SPECS[cat].items(): 
                                db_dict[details['db_col']] = [get_v(details['var'])]
                            
                        new_log = pd.DataFrame(db_dict)
                        st.session_state.daily_logs = pd.concat([st.session_state.daily_logs, new_log], ignore_index=True)
                        save_database(db_conn, st.session_state.daily_logs)
                        st.success("✅ Operational record successfully integrated into file engine!")
                    elif pwd_append != "": 
                        st.error("❌ Master verification credential failed.")
            with c_export:
                word_file = generate_comprehensive_report(log_date, ops_data, display_effect_df, water_data, chem_data, mra_data, get_v('skip_eff'), get_v('skip_wq'), get_v('remarks'))
                st.download_button("📄 Export Word Document (.docx)", data=word_file, file_name=f"MED4_ExecutiveReport_{log_date_str}.docx", mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document", use_container_width=True)
            with c_csv:
                csv_file = generate_daily_csv(log_date, ops_data, display_effect_df, water_data, chem_data, mra_data, st.session_state.vars)
                st.download_button("📊 Export Tabular Values (.csv)", data=csv_file, file_name=f"MED4_DataRecord_{log_date_str}.csv", mime="text/csv", use_container_width=True)

        with rep_tabs[1]:
            st.markdown("#### 📆 Master System Registry Database")
            display_cols = [c for c in EXACT_DB_COLUMNS if c in st.session_state.daily_logs.columns]
            edited_db = st.data_editor(st.session_state.daily_logs[display_cols] if not st.session_state.daily_logs.empty else st.session_state.daily_logs, num_rows="dynamic", use_container_width=True)
            c_sync_pwd, c_sync, c_dl = st.columns([2, 1, 1])
            with c_sync_pwd: 
                pwd_sync = st.text_input("Database Write-Access Password", type="password", key="pwd_sync", label_visibility="collapsed", placeholder="🔑 Enter Database Master Password to Save Modifications")
            with c_sync:
                if st.button("☁️ Synchronize Registry", use_container_width=True):
                    if pwd_sync == "12345678":
                        st.session_state.daily_logs = edited_db
                        save_database(db_conn, st.session_state.daily_logs)
                        st.success("✅ Master registry records updated successfully!")
                    else: 
                        st.error("❌ System modification credentials failed.")
            with c_dl:
                st.download_button("📥 Download Database Offline Backup", data=st.session_state.daily_logs.to_csv(index=False).encode('utf-8'), file_name=f"MED4_MasterRegistry_Backup.csv", mime='text/csv', use_container_width=True)

            st.divider()
            st.markdown("#### 📊 Aggregated Monthly Performance Generator")
            if not st.session_state.daily_logs.empty:
                df_logs = st.session_state.daily_logs.copy()
                df_logs['Date'] = pd.to_datetime(df_logs['Date'], dayfirst=True, errors='coerce')
                month_data = df_logs[(df_logs['Date'].dt.month == log_date.month) & (df_logs['Date'].dt.year == log_date.year)].copy()
                if not month_data.empty:
                    if st.button("📄 Compile and Generate Monthly Summary (.docx)", use_container_width=True):
                        monthly_doc = generate_monthly_report(month_data, log_date.strftime('%B'), str(log_date.year))
                        st.download_button("📥 Download Monthly Briefing Document", data=monthly_doc, file_name=f"MED4_MonthlySummary_{log_date.strftime('%b_%Y')}.docx", mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document")

        with rep_tabs[2]:
            if not st.session_state.daily_logs.empty:
                df_logs = st.session_state.daily_logs.copy()
                df_logs['Date'] = pd.to_datetime(df_logs['Date'], dayfirst=True, errors='coerce')
                
                df_logs['Total SW Feed (m3/h)'] = pd.to_numeric(df_logs.get('Sea Water Feed', 0), errors='coerce')
                df_logs['Recovery (%)'] = np.where(df_logs['Total SW Feed (m3/h)'] > 0, (pd.to_numeric(df_logs.get('Gross production', 0), errors='coerce') / df_logs['Total SW Feed (m3/h)']) * 100, 0)
                
                df_logs['Actual Production'] = pd.to_numeric(df_logs.get('Gross production', 0), errors='coerce')
                df_logs['Residual_Val'] = pd.to_numeric(df_logs.get('Residual', 0), errors='coerce')
                df_logs['Predicted Production'] = df_logs['Actual Production'] - df_logs['Residual_Val']
                df_logs['Overall_HTC_Val'] = pd.to_numeric(df_logs.get('Overall HTC', 0), errors='coerce')
                df_logs['GOR_Val'] = pd.to_numeric(df_logs.get('GOR', 0), errors='coerce')
                
                min_date = df_logs['Date'].min().date() if not df_logs['Date'].isnull().all() else datetime.date(2023, 1, 1)
                max_date = df_logs['Date'].max().date() if not df_logs['Date'].isnull().all() else datetime.date.today()
                
                st.markdown("##### 📅 Performance Evaluation Horizon Filter")
                d_col1, d_col2 = st.columns(2)
                with d_col1: 
                    start_date = st.date_input("Start Threshold Date", min_date, key="start_d1")
                with d_col2: 
                    end_date = st.date_input("End Threshold Date", max_date, key="end_d1")
                
                mask = (df_logs['Date'].dt.date >= start_date) & (df_logs['Date'].dt.date <= end_date)
                df_filtered = df_logs.loc[mask]
                
                q_col1, q_col2 = st.columns(2)
                with q_col1:
                    st.markdown("#### 📉 Performance Recovery Rate Deviation Trend")
                    if len(df_filtered) > 1:
                        rec_chart = alt.Chart(df_filtered).mark_circle().encode(x=alt.X('Date:T', title="Evaluation Timeline"), y=alt.Y('Recovery (%):Q', scale=alt.Scale(zero=False)))
                        st.altair_chart(rec_chart + rec_chart.transform_regression('Date', 'Recovery (%)').mark_line(color='red'), use_container_width=True)
                with q_col2:
                    st.markdown("#### 🌡️ Seawater Coefficient Degradation Rate (HTC)")
                    if len(df_filtered) > 1:
                        htc_chart = alt.Chart(df_filtered).mark_line(point=True, color='orange').encode(x=alt.X('Date:T', title="Evaluation Timeline"), y=alt.Y('Overall_HTC_Val:Q', scale=alt.Scale(zero=False), title="Overall HTC (W/m²K)"))
                        st.altair_chart(htc_chart + htc_chart.transform_regression('Date', 'Overall_HTC_Val').mark_line(color='black'), use_container_width=True)

                st.divider()
                
                q_col3, q_col4 = st.columns(2)
                with q_col3:
                    st.markdown("#### ⚖️ Actual Mass Output vs Normalized Twin Output")
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
                    st.markdown("#### 💰 Specific Unit Thermal Efficiency GOR Performance")
                    if len(df_filtered) > 1:
                        gor_chart = alt.Chart(df_filtered).mark_line(point=True, color='green').encode(
                            x=alt.X('Date:T', title="Evaluation Timeline"), y=alt.Y('GOR_Val:Q', scale=alt.Scale(zero=False), title="Gain Output Ratio"),
                            tooltip=['Date:T', 'GOR_Val']
                        )
                        st.altair_chart(gor_chart + gor_chart.transform_regression('Date', 'GOR_Val').mark_line(color='red', strokeDash=[5, 5]), use_container_width=True)

        with rep_tabs[3]:
            st.markdown("#### 📈 Multivariable Cross-Correlation Explorer")
            if not st.session_state.daily_logs.empty:
                exp_df = st.session_state.daily_logs.copy()
                exp_df['Date'] = pd.to_datetime(exp_df['Date'], dayfirst=True, errors='coerce')
                
                min_date2 = exp_df['Date'].min().date() if not exp_df['Date'].isnull().all() else datetime.date(2023, 1, 1)
                max_date2 = exp_df['Date'].max().date() if not exp_df['Date'].isnull().all() else datetime.date.today()
                
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
        st.subheader("🤖 Machine Learning & OLS Calibration Suite")
        if not SKLEARN_INSTALLED:
            st.error("🚨 Mathematical package 'scikit-learn' is missing from file dependencies.")
        else:
            st.warning("📌 **Ephemeral Server Parameter Caution:** Since this tracking node runs on temporary testing cloud containers, manual machine-learning logic selection targets revert back to historical OLS baseline models after inactive shutdown flags are generated. Selection options remain permanently hardlocked upon local internal node integration.")
            st.markdown("### 💾 Manage Baseline Evaluation Multipliers")
            st.markdown(f"**Current Evaluator Logic Subroutine:** `{model_type}`")
            c_reset, _ = st.columns([1, 1])
            with c_reset:
                if st.button("🔄 Execute Subroutine Calibration Factory Reset", use_container_width=True):
                    st.session_state.mra_coef = MRA_COEF_2014.copy()
                    save_config(db_conn, st.session_state.mra_coef)
                    st.success("✅ Baseline parameters successfully reverted back to original OLS multipliers!")
                    time.sleep(1.5)
                    st.rerun()

            st.divider()
            st.markdown("### 📊 Multi-Variable Predictive Optimization Logic Model Builder")
            st.markdown("Upload plant calibration verification matrices to evaluate structural variations between standard linear regression loops and active tree configurations.")
            
            req_cols = ["Date", "Gross production", "1st effect vapour pressure", "1st effect vapour temp", "Sea Water Upper", "1st effect brine temp", "Brine Water Return", "LP Steam consumption", "Steam inlet temp", "Anti_PPM"]
            template_df = pd.DataFrame(columns=req_cols)
            st.download_button(label="1️⃣ Download Standard Structural Training Template File", data=template_df.to_csv(index=False).encode('utf-8'), file_name='MED4_ML_CalibrationTemplate.csv', mime='text/csv')
            
            st.divider()
            uploaded_file = st.file_uploader("2️⃣ Inject Completed Optimization Dataset", type=["csv"], key="mra_trainer")
            
            if uploaded_file is not None:
                try:
                    df_train = pd.read_csv(uploaded_file)
                    if not all(col in df_train.columns for col in req_cols): 
                        st.error(f"❌ Structural training template verification failed due to parameter column omissions.")
                    else:
                        for col in req_cols:
                            if col != "Date":
                                if df_train[col].dtype == object: 
                                    df_train[col] = pd.to_numeric(df_train[col].astype(str).str.replace(',', '', regex=False), errors='coerce')
                        
                        df_train = df_train.dropna(subset=[c for c in req_cols if c != "Date"])
                        st.success(f"✅ Training Initialized successfully utilizing {len(df_train)} localized validation rows.")
                        
                        if len(df_train) > 0:
                            X = df_train[["1st effect vapour pressure", "1st effect vapour temp", "Sea Water Upper", "1st effect brine temp", "Brine Water Return", "LP Steam consumption", "Steam inlet temp", "Anti_PPM"]]
                            Y = df_train["Gross production"]
                            
                            model_ols = LinearRegression(fit_intercept=True).fit(X, Y)
                            r2_ols = r2_score(Y, model_ols.predict(X))
                            
                            model_rf = RandomForestRegressor(n_estimators=100, random_state=42).fit(X, Y)
                            r2_rf = r2_score(Y, model_rf.predict(X))
                            
                            if XGB_INSTALLED:
                                model_xgb = xgb.XGBRegressor(n_estimators=100, random_state=42).fit(X, Y)
                                r2_xgb = r2_score(Y, model_xgb.predict(X))
                            
                            st.markdown("### 🏆 Algorithm Accuracy Evaluation Matrix")
                            m1, m2, m3 = st.columns(3)
                            m1.metric("1. Linear OLS Fit (R² Coefficient)", f"{r2_ols * 100:.2f}%")
                            m2.metric("2. Random Forest Tree Logic (R²)", f"{r2_rf * 100:.2f}%")
                            if XGB_INSTALLED: 
                                m3.metric("3. Extreme Gradient Boost XGB (R²)", f"{r2_xgb * 100:.2f}%")
                            else: 
                                m3.warning("Advanced Gradient boosting library dependency not activated.")
                            
                            st.markdown("#### Dynamic Feature Sensitivity Weights / Scaling Coefficients")
                            comp_dict = {
                                "Parameter": ["Press_1st", "Temp_1st", "SW_Upper", "Brine_Temp_1st", "Brine_Flow", "LP_Steam", "Steam_Temp", "Anti_PPM"],
                                "OLS (Coefficients)": np.round(model_ols.coef_, 4),
                                "Random Forest (Importance %)": np.round(model_rf.feature_importances_ * 100, 2)
                            }
                            if XGB_INSTALLED: 
                                comp_dict["XGBoost (Importance %)"] = np.round(model_xgb.feature_importances_ * 100, 2)
                            st.dataframe(pd.DataFrame(comp_dict).style.format(precision=4), use_container_width=True, hide_index=True)
                            
                            st.markdown("### 💾 Commit & Lock Mathematical Subroutine Target")
                            opts = ["OLS (Linear)", "Random Forest"]
                            if XGB_INSTALLED: 
                                opts.append("XGBoost")
                                
                            selected_model = st.radio("Configure Active Live Prediction Logic Block:", opts)
                            
                            if st.button("🔥 Confirm and Hardlock Active Operational Subroutine", type="primary", use_container_width=True):
                                if selected_model == "OLS (Linear)":
                                    new_coefs = {
                                        "model_type": "OLS", "Intercept": float(model_ols.intercept_),
                                        "Press_1st": float(model_ols.coef_[0]), "Temp_1st": float(model_ols.coef_[1]), 
                                        "SW_Upper": float(model_ols.coef_[2]), "Brine_Temp_1st": float(model_ols.coef_[3]), 
                                        "Brine_Flow": float(model_ols.coef_[4]), "LP_Steam": float(model_ols.coef_[5]), 
                                        "Steam_Temp": float(model_ols.coef_[6]), "Anti_PPM": float(model_ols.coef_[7])
                                    }
                                    st.session_state.mra_coef = new_coefs
                                    save_config(db_conn, new_coefs)
                                else:
                                    target_m = model_rf if selected_model == "Random Forest" else model_xgb
                                    joblib.dump(target_m, AI_MODEL_FILE)
                                    ai_coefs = {
                                        "model_type": selected_model,
                                        "Press_1st": float(target_m.feature_importances_[0]), "Temp_1st": float(target_m.feature_importances_[1]), 
                                        "SW_Upper": float(target_m.feature_importances_[2]), "Brine_Temp_1st": float(target_m.feature_importances_[3]), 
                                        "Brine_Flow": float(target_m.feature_importances_[4]), "LP_Steam": float(target_m.feature_importances_[5]), 
                                        "Steam_Temp": float(target_m.feature_importances_[6]), "Anti_PPM": float(target_m.feature_importances_[7])
                                    }
                                    st.session_state.mra_coef = ai_coefs
                                    save_config(db_conn, ai_coefs)
                                    
                                st.success(f"✅ System evaluation subroutine locked into {selected_model} logic sequence.")
                                time.sleep(1.5)
                                st.rerun()
                        else: 
                            st.error("🚨 Structural data parsing produced empty float ranges inside parameters.")
                except Exception as e: 
                    st.error(f"Structural data matrix crash: {e}")

    # --- TAB 8: BULK EXCEL UPLOADER PANEL ---
    with tabs[8]:
        st.subheader("📤 Batch Log Matrix Ingestion Subroutine")
        st.markdown("Download the target spreadsheet schema file. Copy/pasting raw values in the exact historical Excel configuration is fully supported.")
        
        bulk_template = pd.DataFrame(columns=EXACT_DB_COLUMNS)
        st.download_button(label="1️⃣ Download Schema Verification Template File", data=bulk_template.to_csv(index=False).encode('utf-8'), file_name='MED4_BulkMatrixInletSchema.csv', mime='text/csv')
        
        st.divider()
        bulk_file = st.file_uploader("2️⃣ Ingest Completed System Batch File (.csv)", type=["csv"], key="bulk_uploader")
        
        if bulk_file is not None:
            try:
                df_bulk = pd.read_csv(bulk_file)
                if 'Date' in df_bulk.columns and 'Date (DD/MM/YYYY)' not in df_bulk.columns:
                    pass 
                if 'Date (DD/MM/YYYY)' in df_bulk.columns:
                     df_bulk.rename(columns={'Date (DD/MM/YYYY)': 'Date'}, inplace=True)

                missing = [c for c in EXACT_DB_COLUMNS if c not in df_bulk.columns]
                if missing:
                    st.warning(f"⚠️ Omissions detected inside uploaded parameters. Empty slots will auto-fill utilizing historical parameter baseline means. Missing: {', '.join(missing)}")
                    for c in missing: 
                        df_bulk[c] = np.nan
                
                num_cols = [c for c in EXACT_DB_COLUMNS if c not in ["Date", "Remarks"]]
                for col in num_cols:
                    if col in df_bulk.columns:
                        if df_bulk[col].dtype == object: 
                            df_bulk[col] = pd.to_numeric(df_bulk[col].astype(str).str.replace(',', '', regex=False), errors='coerce')
                
                df_bulk = df_bulk.dropna(subset=["Date"])
                
                if len(df_bulk) > 0:
                    for col_name, baseline_val in zip(
                        ['1st effect vapour pressure', '1st effect vapour temp', 'Sea Water Upper', '1st effect brine temp', 'Brine Water Return', 'LP Steam consumption', 'Steam inlet temp'],
                        [231.76, 68.47, 553.63, 65.46, 1275.50, 71.75, 165.54]
                    ):
                        df_bulk[col_name] = df_bulk[col_name].fillna(baseline_val)
                    
                    df_bulk['Anti_PPM'] = df_bulk['Anti_PPM'].fillna(4.82)
                    df_bulk['Gross production'] = df_bulk['Gross production'].fillna(0.0)
                    
                    for cat in ['Feed', 'Product']:
                        for param, details in WATER_SPECS[cat].items():
                            df_bulk[details['db_col']] = df_bulk[details['db_col']].fillna(details['avg'])
                        
                    df_bulk['Date_Clean'] = pd.to_datetime(df_bulk['Date'], dayfirst=True, errors='coerce').dt.strftime('%Y-%m-%d')
                    df_bulk['GOR'] = np.where(df_bulk['LP Steam consumption'] > 0, df_bulk['Gross production'] / df_bulk['LP Steam consumption'], 0)
                    df_bulk['Delta T'] = df_bulk['Delta T'].fillna(df_bulk['1st effect vapour temp'] - df_bulk['1st effect brine temp'])

                    if model_type == "OLS":
                        df_bulk['Predicted'] = (
                            coefs["Intercept"] + 
                            (coefs["Press_1st"] * df_bulk['1st effect vapour pressure']) + 
                            (coefs["Temp_1st"] * df_bulk['1st effect vapour temp']) + 
                            (coefs["SW_Upper"] * df_bulk['Sea Water Upper']) + 
                            (coefs["Brine_Temp_1st"] * df_bulk['1st effect brine temp']) + 
                            (coefs["Brine_Flow"] * df_bulk['Brine Water Return']) + 
                            (coefs["LP_Steam"] * df_bulk['LP Steam consumption']) + 
                            (coefs["Steam_Temp"] * df_bulk['Steam inlet temp']) +
                            (coefs.get("Anti_PPM", MRA_COEF_2014["Anti_PPM"]) * df_bulk['Anti_PPM'])
                        )
                    else:
                        try:
                            active_model = joblib.load(AI_MODEL_FILE)
                            bulk_input_df = df_bulk[['1st effect vapour pressure', '1st effect vapour temp', 'Sea Water Upper', '1st effect brine temp', 'Brine Water Return', 'LP Steam consumption', 'Steam inlet temp', 'Anti_PPM']].copy()
                            bulk_input_df.columns = ["Press_1st", "Temp_1st", "SW_Upper", "Brine_Temp_1st", "Brine_Flow", "LP_Steam", "Steam_Temp", "Anti_PPM"]
                            df_bulk['Predicted'] = active_model.predict(bulk_input_df)
                        except: 
                            df_bulk['Predicted'] = 0.0
                            
                    df_bulk['Residual'] = df_bulk['Gross production'] - df_bulk['Predicted']
                    df_bulk['Sea Water cond I/L temp'] = df_bulk['Sea Water cond I/L temp'].fillna(30.0)
                    df_bulk['Brine outlet temp'] = df_bulk['Brine outlet temp'].fillna(41.0)
                    df_bulk['Sea Water Feed'] = df_bulk['Sea Water Feed'].fillna(2100.0)
                    
                    area_overall = get_v('area_overall')
                    dt_overall = df_bulk['1st effect vapour temp'] - df_bulk['Brine outlet temp']
                    q_overall = df_bulk['Sea Water Feed'] * (df_bulk['Brine outlet temp'] - df_bulk['Sea Water cond I/L temp']) * 0.930
                    df_bulk['Overall HTC'] = np.where(dt_overall > 0, (q_overall / (area_overall * dt_overall)) * 1000, 0)

                    area_1st = get_v('area_1st')
                    brine_avg = 55.0 
                    q_1st = df_bulk['Sea Water Upper'] * (df_bulk['1st effect brine temp'] - brine_avg) * 0.930
                    df_bulk['1st Effect HTC'] = np.where(df_bulk['Delta T'] > 0, (q_1st / (area_1st * df_bulk['Delta T'])) * 1000, 0)
                    
                    db_ready_dict = {
                        "Date": df_bulk['Date_Clean'], 
                        "Sea Water Upper": df_bulk['Sea Water Upper'], 
                        "Sea Water Lower": df_bulk['Sea Water Lower'].fillna(0),
                        "Sea Water Feed": df_bulk['Sea Water Feed'], 
                        "Brine Water Return": df_bulk['Brine Water Return'],
                        "Desal production": df_bulk['Desal production'].fillna(0), 
                        "LP Steam consumption": df_bulk['LP Steam consumption'],
                        "condensate flow": df_bulk['condensate flow'].fillna(0), 
                        "condensate temp": df_bulk['condensate temp'].fillna(0),
                        "1st effect vapour temp": df_bulk['1st effect vapour temp'], 
                        "1st effect brine temp": df_bulk['1st effect brine temp'],
                        "Delta T": df_bulk['Delta T'], 
                        "1st effect vapour pressure": df_bulk['1st effect vapour pressure'],
                        "Steam inlet temp": df_bulk['Steam inlet temp'], 
                        "Brine outlet temp": df_bulk['Brine outlet temp'],
                        "Sea Water cond I/L temp": df_bulk['Sea Water cond I/L temp'], 
                        "Sea Water o/L temp": df_bulk['Sea Water o/L temp'].fillna(0),
                        "CW supply": df_bulk['CW supply'].fillna(0), 
                        "SW return": df_bulk['SW return'].fillna(0),
                        "Gross production": df_bulk['Gross production'],
                        "GOR": df_bulk['GOR'].round(2), 
                        "Overall HTC": df_bulk['Overall HTC'].round(2), 
                        "1st Effect HTC": df_bulk['1st Effect HTC'].round(2),
                        "Residual": df_bulk['Residual'].round(1),
                        "Antiscalant (kg)": df_bulk['Antiscalant (kg)'].fillna(0), 
                        "Antifoam (kg)": df_bulk['Antifoam (kg)'].fillna(0),
                        "Anti_PPM": df_bulk['Anti_PPM'], 
                        "Remarks": df_bulk['Remarks'].fillna(""),
                        "Area_1st": area_1st, 
                        "Area_Overall": area_overall
                    }
                    
                    for cat in ['Feed', 'Product']:
                        for param, details in WATER_SPECS[cat].items(): 
                            db_ready_dict[details['db_col']] = df_bulk[details['db_col']]
                            
                    db_ready_df = pd.DataFrame(db_ready_dict)
                    
                    st.success(f"✅ Dynamic verification evaluation complete for {len(db_ready_df)} matrix rows.")
                    st.dataframe(db_ready_df.style.format(precision=2), use_container_width=True, hide_index=True)
                    
                    st.markdown("### 💾 Append Transferred Batch Elements")
                    c_pwd, c_save = st.columns([2, 2])
                    with c_pwd: 
                        pwd_bulk = st.text_input("Security Key Verification Entry", type="password", key="pwd_bulk", label_visibility="collapsed", placeholder="🔑 Enter Master Security Password to Commit Batch Ingestion")
                    with c_save:
                        if st.button("🔄 Append Ingested Records into Registry", use_container_width=True):
                            if pwd_bulk == "12345678":
                                st.session_state.daily_logs = pd.concat([st.session_state.daily_logs, db_ready_df], ignore_index=True)
                                st.session_state.daily_logs = st.session_state.daily_logs.drop_duplicates(subset=['Date'], keep='last').reset_index(drop=True)
                                save_database(db_conn, st.session_state.daily_logs)
                                st.success("✅ Batch matrix rows integrated securely within master registry file!")
                                time.sleep(1.5)
                                st.rerun()
                            elif pwd_bulk != "": 
                                st.error("❌ Identification credentials mismatched.")
                else: 
                    st.error("🚨 Data matrix parse sequence returned zero active rows.")
            except Exception as e: 
                st.error(f"Structural verification crash during upload parsing: {e}")

    render_chatbot()

if __name__ == "__main__":
    main()
