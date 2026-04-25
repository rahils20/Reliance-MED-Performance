# requirements: pandas, numpy, python-docx, altair, gspread, oauth2client, scikit-learn, xgboost, joblib
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

st.set_page_config(page_title="Chembond Water | MED-4 Management", layout="wide")

# ==========================================
# 1. CLOUD "GHOST SHEET" & CONFIG ENGINE
# ==========================================
GOOGLE_SHEET_NAME = "MED4_Cloud_Database"
LOCAL_DB_FILE = "MED4_Master_Database.csv"
LOCAL_CONFIG_FILE = "mra_config.json"
AI_MODEL_FILE = "mra_ai_model.pkl"

MRA_COEF_2014 = {
    "model_type": "OLS",
    "Intercept": -161.5638, 
    "Press_1st": 0.6136, 
    "Temp_1st": 3.6392, 
    "SW_Upper": 0.8111, 
    "Brine_Temp_1st": -7.6638, 
    "Brine_Flow": -0.2329, 
    "LP_Steam": 8.2539, 
    "Steam_Temp": 2.1924,
    "Anti_PPM": -7.0301
}

MRA_BASELINE = {
    "Press_1st": 231.76, 
    "Temp_1st": 68.47, 
    "SW_Upper": 553.63, 
    "Brine_Temp_1st": 65.46, 
    "Brine_Flow": 1275.50, 
    "LP_Steam": 71.75, 
    "Steam_Temp": 165.54, 
    "Anti_PPM": 4.82
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
        except: 
            pass
    return {"type": "local", "client": None}

def load_database(db):
    if db["type"] == "cloud":
        try:
            records = db["client"].get_all_records()
            if records: return pd.DataFrame(records)
        except: 
            pass
    if os.path.exists(LOCAL_DB_FILE): 
        return pd.read_csv(LOCAL_DB_FILE)
    return pd.DataFrame()

def save_database(db, df):
    df['Date'] = pd.to_datetime(df['Date'], dayfirst=True, errors='coerce').dt.strftime('%Y-%m-%d')
    df = df.fillna(0)
    if db["type"] == "cloud":
        try:
            db["client"].clear()
            db["client"].update([df.columns.values.tolist()] + df.values.tolist())
            df.to_csv(LOCAL_DB_FILE, index=False)
            return True
        except: 
            pass
    df.to_csv(LOCAL_DB_FILE, index=False)
    return True

def load_config(db):
    if os.path.exists(LOCAL_CONFIG_FILE):
        try:
            with open(LOCAL_CONFIG_FILE, "r") as f: 
                return json.load(f)
        except: 
            pass
    return MRA_COEF_2014.copy()

def save_config(db, coef_dict):
    with open(LOCAL_CONFIG_FILE, "w") as f: 
        json.dump(coef_dict, f)

db_conn = init_db_connection()

# ==========================================
# 2. REPORT & CSV EXPORT GENERATORS
# ==========================================
def generate_daily_csv(date, ops, display_effect_df, w_data, chem_data, mra):
    data_dict = {
        "Date": date.strftime('%d/%m/%Y'),
        "SW Feed to 1st Effect": ops['SW_Feed_1st'],
        "Total SW Feed (FFC)": ops['SW Total'],
        "Brine Water Return": ops['Brine Return'],
        "Desal Production": ops['Desal'],
        "LP Steam Consumption": ops['Steam'],
        "Gross Production": ops['Gross Prod'],
        "Recovery": round(ops['Recovery'], 2),
        "Gain Output Ratio": round(ops['GOR'], 2),
        "Overall HTC": round(ops['htc_overall'], 2),
        "1st Effect HTC": round(ops['htc_1st'], 2),
        "MRA Residual": round(mra['Residual'], 2),
        "Antiscalant Dosing (PPM)": chem_data['anti_ppm'],
        "Antiscalant Consumption (kg/hr)": chem_data['anti_cons'],
        "Antifoam Dosing (PPM)": chem_data['foam_ppm'],
        "Antifoam Consumption (kg/hr)": chem_data['foam_cons']
    }
    
    for param, details in w_data['Feed'].items(): 
        data_dict[f"Feed Water - {param}"] = details['val']
    for param, details in w_data['Product'].items(): 
        data_dict[f"Desal Product - {param}"] = details['val']
        
    for idx, row in display_effect_df.iterrows():
        data_dict[f"{row['Effect ID']} Vapor Temp"] = row['Live Vapor (°C)']
        data_dict[f"{row['Effect ID']} Brine Temp"] = row['Live Brine (°C)']
        
    df = pd.DataFrame([data_dict])
    return df.to_csv(index=False).encode('utf-8')

def generate_comprehensive_report(date, ops, display_effect_df, w_data, chem_data, mra, skip_eff, skip_wq, remarks):
    doc = Document()
    doc.add_heading('MED-4 Daily Operational & Performance Report', 0).alignment = WD_ALIGN_PARAGRAPH.CENTER
    p = doc.add_paragraph()
    p.add_run('Prepared by: ').bold = True
    p.add_run('Chembond Water\n')
    p.add_run('Date: ').bold = True
    p.add_run(date.strftime('%d/%m/%Y'))
    p.alignment = WD_ALIGN_PARAGRAPH.RIGHT

    doc.add_heading('1. Executive Summary', level=1)
    doc.add_paragraph(f"On {date.strftime('%d/%m/%Y')}, the MED-4 unit achieved a Gross Production of {ops['Gross Prod']} m³/h and a Gain Output Ratio (GOR) of {ops['GOR']:.2f}:1. The Specific Thermal Energy Consumption (STEC) was {ops['STEC']:.2f} kWh/ton with a system recovery of {ops['Recovery']:.1f}%.")

    doc.add_heading('2. Operational Data Summary', level=1)
    t_ops = doc.add_table(rows=1, cols=4)
    t_ops.style = 'Table Grid'
    for i, h in enumerate(['Parameter', 'UOM', 'Design', 'Actual']): 
        t_ops.rows[0].cells[i].text = h
        
    ops_rows = [
        ['Total Sea Water Feed (FFC)', 'm³/h', '2400', str(ops['SW Total'])], 
        ['SW Feed to 1st Effect', 'm³/h', '580', str(ops['SW_Feed_1st'])], 
        ['Brine Return', 'm³/h', '1400', str(ops['Brine Return'])], 
        ['Desal', 'm³/h', '1000', str(ops['Desal'])], 
        ['Gross Prod', 'm³/h', '-', str(ops['Gross Prod'])], 
        ['LP Steam', 'TPH', '92-94.5', str(ops['Steam'])], 
        ['Recovery', '%', '40.0', f"{ops['Recovery']:.2f}"], 
        ['GOR', 'Ratio', '10.5 : 1', f"{ops['GOR']:.2f} : 1"]
    ]
    for row in ops_rows:
        rc = t_ops.add_row().cells
        for i, val in enumerate(row): 
            rc[i].text = val

    doc.add_heading('3. Chemical Dosing Status', level=1)
    t_chem = doc.add_table(rows=1, cols=3)
    t_chem.style = 'Table Grid'
    for i, h in enumerate(['Chemical', 'Target Dosing (PPM)', 'Actual Consumption (kg/hr)']): 
        t_chem.rows[0].cells[i].text = h
        
    rc1 = t_chem.add_row().cells
    rc1[0].text, rc1[1].text, rc1[2].text = "Kem Watreat r 3687 (Antiscalant)", f"{chem_data['anti_ppm']:.1f}", f"{chem_data['anti_cons']:.2f}"
    rc2 = t_chem.add_row().cells
    rc2[0].text, rc2[1].text, rc2[2].text = "Kem Antifoam 1795", f"{chem_data['foam_ppm']:.1f}", f"{chem_data['foam_cons']:.2f}"

    doc.add_heading('4. Thermal Integrity (HTC)', level=1)
    doc.add_paragraph(f"Overall Plant HTC: {ops['htc_overall']:.2f} W/m²K | 1st Effect HTC: {ops['htc_1st']:.2f} W/m²K")
    
    doc.add_heading('5. Water Quality', level=1)
    if skip_wq: 
        doc.add_paragraph("NOTE: Laboratory water quality parameters were not recorded for this operational day.", style='BodyText')
    else:
        t_wq = doc.add_table(rows=1, cols=4)
        t_wq.style = 'Table Grid'
        for i, h in enumerate(['Parameter', 'Stream', 'Limit/Spec', 'Actual']): 
            t_wq.rows[0].cells[i].text = h
        for param, data in w_data['Feed'].items():
            rc = t_wq.add_row().cells
            rc[0].text, rc[1].text, rc[2].text, rc[3].text = str(param), 'Sea Water Feed', f"{data['min']}-{data['max']}", str(data['val'])
        for param, data in w_data['Product'].items():
            rc = t_wq.add_row().cells
            rc[0].text, rc[1].text, rc[2].text, rc[3].text = str(param), 'Desal Product', f"{data['min']}-{data['max']}", str(data['val'])

    doc.add_heading('6. MRA Fouling Indicator', level=1)
    diff_pct = (mra['Residual'] / mra['Predicted']) * 100 if mra['Predicted'] > 0 else 0
    doc.add_paragraph(f"Actual Gross: {mra['Actual']:.1f} m³/h | MRA Predicted: {mra['Predicted']:.1f} m³/h | Difference: {diff_pct:.1f}%")
    
    if diff_pct <= -5.0: 
        doc.add_paragraph(f"STATUS: FOULING DETECTED ({diff_pct:.1f}% loss). Please clean the machine.").runs[0].font.color.rgb = RGBColor(255, 0, 0)
    elif diff_pct <= -4.0: 
        doc.add_paragraph(f"STATUS: WARNING ({diff_pct:.1f}% loss). Increase antiscalant dosing.").runs[0].font.color.rgb = RGBColor(255, 140, 0)
    else: 
        doc.add_paragraph(f"STATUS: CLEAN ({diff_pct:.1f}% loss). System operating normally.").runs[0].font.color.rgb = RGBColor(0, 128, 0)
    
    if remarks and str(remarks).strip() != "":
        doc.add_heading('7. Remarks & Observations', level=1)
        doc.add_paragraph(str(remarks))

    bio = io.BytesIO()
    doc.save(bio)
    return bio.getvalue()

def generate_monthly_report(df_month, month_str, year_str):
    doc = Document()
    doc.add_heading(f'MED-4 Monthly Performance Summary: {month_str} {year_str}', 0).alignment = WD_ALIGN_PARAGRAPH.CENTER
    doc.add_heading('1. Monthly Aggregation', level=1)
    
    t_agg = doc.add_table(rows=1, cols=4)
    t_agg.style = 'Table Grid'
    for i, h in enumerate(['Metric', 'Minimum', 'Maximum', 'Average']): 
        t_agg.rows[0].cells[i].text = h
    
    metrics = [
        ("Gross Production (m³/h)", df_month['Gross Prod (m3/h)']), 
        ("Gain Output Ratio (GOR)", df_month['GOR']), 
        ("Overall HTC (W/m²K)", df_month['Overall HTC']), 
        ("1st Effect HTC", df_month['1st Effect HTC'])
    ]
    
    for name, series in metrics:
        rc = t_agg.add_row().cells
        rc[0].text = name
        rc[1].text = f"{pd.to_numeric(series, errors='coerce').min():.2f}"
        rc[2].text = f"{pd.to_numeric(series, errors='coerce').max():.2f}"
        rc[3].text = f"{pd.to_numeric(series, errors='coerce').mean():.2f}"
        
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
    'skip_eff': False, 'skip_wq': False, 'remarks': "", 'area_1st': 1757.49, 'area_overall': 19332.0
}

SYNC_MAP = {
    'steam': ['in_steam', 't1_steam', 't5_steam'], 
    'desal': ['in_desal', 't1_desal'], 
    'gross': ['in_gross', 't1_gross'],
    'sw_upper': ['in_sw_up', 't1_sw_up', 't5_sw_up'], 
    'sw_total': ['in_sw_tot', 't1_sw_tot', 't4_sw_tot'], 
    'brine_ret': ['in_brine', 't1_brine', 't5_bflow'],
    'sw_in_t': ['in_sw_in', 't2_sw_in'], 
    'brine_out_t': ['in_brine_out', 't2_brine_out'], 
    'stm_in_t': ['in_stm_in', 't2_stm_in', 't5_stm_t'], 
    'vap_out_t': ['in_vap_out', 't2_vap_out'],
    'mra_press': ['in_press', 't5_press'], 
    'mra_t1': ['in_t1', 't5_t1'], 
    'mra_bt1': ['in_bt1', 't5_bt1'],
    'f_ph': ['in_f_ph', 't3_f_ph'], 
    'f_turb': ['in_f_turb', 't3_f_turb'], 
    'f_tss': ['in_f_tss', 't3_f_tss'], 
    'f_tds': ['in_f_tds', 't3_f_tds'],
    'f_alk': ['in_f_alk', 't3_f_alk'], 
    'f_ca': ['in_f_ca', 't3_f_ca'], 
    'f_cl': ['in_f_cl', 't3_f_cl'], 
    'f_so4': ['in_f_so4', 't3_f_so4'],
    'p_ph': ['in_p_ph', 't3_p_ph'], 
    'p_cond': ['in_p_cond', 't3_p_cond'], 
    'p_tds': ['in_p_tds', 't3_p_tds'], 
    'p_iron': ['in_p_iron', 't3_p_iron'], 
    'p_cl': ['in_p_cl', 't3_p_cl'], 
    'p_so4': ['in_p_so4', 't3_p_so4'],
    'chem_anti_ppm': ['in_anti_ppm', 't4_anti_ppm', 't5_anti'], 
    'chem_anti_cons': ['in_anti_cons', 't4_anti_cons'],
    'chem_foam_ppm': ['in_foam_ppm', 't4_foam_ppm'], 
    'chem_foam_cons': ['in_foam_cons', 't4_foam_cons'],
    'remarks': ['in_remarks'], 
    'area_1st': ['t2_area_1st'], 
    'area_overall': ['t2_area_overall']
}

if 'vars' not in st.session_state: 
    st.session_state.vars = DEFAULTS.copy()
    
for k, v in DEFAULTS.items():
    if k not in st.session_state.vars: 
        st.session_state.vars[k] = v

if 'sync_initialized' not in st.session_state:
    for var_name, keys in SYNC_MAP.items():
        for k in keys: 
            if k not in st.session_state: 
                st.session_state[k] = st.session_state.vars[var_name]
    st.session_state.sync_initialized = True

if 'shared_effect_df' not in st.session_state:
    st.session_state.shared_effect_df = pd.DataFrame({
        "Effect ID": [f"Effect {i}" for i in range(1, 12)], 
        "Live Vapor (°C)": np.round(np.linspace(69.0, 42.0, 11), 1), 
        "Live Brine (°C)": np.round(np.linspace(66.3, 40.0, 11), 1)
    })

if 'daily_logs' not in st.session_state: 
    st.session_state.daily_logs = load_database(db_conn)
if 'mra_coef' not in st.session_state: 
    st.session_state.mra_coef = load_config(db_conn)

if "messages" not in st.session_state:
    st.session_state.messages = [{"role": "assistant", "content": "Hello! I am the MED-4 Support Assistant. Ask me anything about how the calculations work."}]

def sync_var(var_name, source_key):
    st.session_state.vars[var_name] = st.session_state[source_key]
    for target_key in SYNC_MAP[var_name]:
        if target_key != source_key: 
            st.session_state[target_key] = st.session_state[source_key]

def get_v(var_name): 
    return st.session_state.vars[var_name]

LATENT_HEAT_STEAM_KJ_KG = 2260.0 
WATER_SPECS = {
    "Feed": {"pH": {"lim": (7.5, 9.2), "var": "f_ph"}, "Turbidity (NTU)": {"lim": (0.0, 5.0), "var": "f_turb"}, "TSS (ppm)": {"lim": (0.0, 10.0), "var": "f_tss"}, "TDS (ppm)": {"lim": (0.0, 42000.0), "var": "f_tds"}, "Total Alkalinity": {"lim": (160.0, 190.0), "var": "f_alk"}, "Calcium Hardness": {"lim": (950.0, 1100.0), "var": "f_ca"}, "Chlorides": {"lim": (21000.0, 22000.0), "var": "f_cl"}, "Sulphate": {"lim": (3050.0, 3250.0), "var": "f_so4"}},
    "Product": {"pH": {"lim": (5.5, 7.0), "var": "p_ph"}, "Conductivity (μs/cm)": {"lim": (0.0, 15.0), "var": "p_cond"}, "TDS (ppm)": {"lim": (0.0, 10.0), "var": "p_tds"}, "Total Iron": {"lim": (0.0, 0.1), "var": "p_iron"}, "Chlorides": {"lim": (0.0, 5.0), "var": "p_cl"}, "Sulphate": {"lim": (0.0, 1.0), "var": "p_so4"}}
}

# ==========================================
# MAIN APPLICATION
# ==========================================
def main():
    try: 
        st.sidebar.image("chembond_logo.png", use_container_width=True)
    except: 
        st.sidebar.markdown("### 🔹 CHEMBOND WATER") 
        
    st.sidebar.divider()
    
    log_date = st.sidebar.date_input("Date", datetime.date.today(), format="DD/MM/YYYY")
    log_date_str = log_date.strftime('%Y-%m-%d')
    
    if 'last_selected_date' not in st.session_state: 
        st.session_state.last_selected_date = None

    # THE FIX: Extremely robust, exact data mapping to prevent historical data wiping
    if log_date_str != st.session_state.last_selected_date:
        st.session_state.last_selected_date = log_date_str
        if not st.session_state.daily_logs.empty and 'Date' in st.session_state.daily_logs.columns:
            db_dates = pd.to_datetime(st.session_state.daily_logs['Date'], errors='coerce').dt.strftime('%Y-%m-%d').values
            if log_date_str in db_dates:
                row_idx = np.where(db_dates == log_date_str)[0][0]
                row = st.session_state.daily_logs.iloc[row_idx]
                
                db_to_var_mapping = {
                    'gross': ['Gross Prod (m3/h)', 'Gross Prod'], 
                    'desal': ['Desal (m3/h)'], 
                    'steam': ['Steam (TPH)'],
                    'sw_total': ['Total SW Feed (m3/h)', 'SW Feed (m3/h)', 'Total Sea Water Feed (m3/h)', 'Total Sea Water Feed (FFC)'], 
                    'sw_upper': ['SW Feed to 1st Effect (m3/h)', 'SW_Upper'],
                    'chem_anti_cons': ['Antiscalant (kg)'], 
                    'chem_foam_cons': ['Antifoam (kg)'], 
                    'mra_press': ['Press_1st'], 
                    'mra_t1': ['Temp_1st'], 
                    'mra_bt1': ['Brine_Temp_1st'], 
                    'brine_ret': ['Brine_Flow'], 
                    'stm_in_t': ['Steam_Temp'], 
                    'chem_anti_ppm': ['Anti_PPM'], 
                    'sw_in_t': ['SW_Cond_Inlet_Temp', 'SW_In_Temp', 'SW Cond I/L Temp'], 
                    'brine_out_t': ['Final_Brine_Temp', 'Brine_Out_Temp', 'Final Brine Temp'], 
                    'vap_out_t': ['Vap_Out_Temp'], 
                    'remarks': ['Remarks'],
                    'area_1st': ['Area_1st'], 
                    'area_overall': ['Area_Overall']
                }
                
                for cat in ['Feed', 'Product']:
                    for param, d in WATER_SPECS[cat].items(): 
                        db_to_var_mapping[d['var']] = [d['var']]

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

    st.title("🏭 Reliance MED-4 Management Suite")
    
    tabs = st.tabs([
        "📥 0. Inputs", "🌊 1. KPIs", "🔥 2. HTC", "🧪 3. Quality", 
        "🛢️ 4. Chemicals", "🧠 5. MRA", "📂 6. Reporting", 
        "🤖 7. AI Model Select", "📤 8. Bulk Uploads"
    ])

    # THE FIX: Restored full dictionary with all 10 core variables to prevent KeyError
    ops_data = {
        'Steam': get_v('steam'), 
        'Desal': get_v('desal'), 
        'Gross Prod': get_v('gross'), 
        'SW Upper': get_v('sw_upper'), # Fallback mapping
        'SW_Feed_1st': get_v('sw_upper'), # Modern mapping
        'SW Total': get_v('sw_total'), 
        'Brine Return': get_v('brine_ret'),
        'SW In': get_v('sw_in_t'), 
        'Brine Out': get_v('brine_out_t'), 
        'Stm In': get_v('stm_in_t'), 
        'Vap Out': get_v('vap_out_t')
    }
    
    ops_data['GOR'] = ops_data['Gross Prod'] / ops_data['Steam'] if ops_data['Steam'] > 0 else 0
    ops_data['STEC'] = (((ops_data['Steam'] * 1000) / 3600) * LATENT_HEAT_STEAM_KJ_KG) / ops_data['Desal'] if ops_data['Desal'] > 0 else 0
    ops_data['Recovery'] = (ops_data['Gross Prod'] / ops_data['SW Total']) * 100 if ops_data['SW Total'] > 0 else 0
    ops_data['Conversion'] = ops_data['Desal'] / ops_data['SW Total'] if ops_data['SW Total'] > 0 else 0
    ops_data['Economy'] = ops_data['Steam'] / ops_data['Desal'] if ops_data['Desal'] > 0 else 0

    display_effect_df = pd.merge(BASE_EFFECTS, st.session_state.shared_effect_df, on="Effect ID")
    display_effect_df = display_effect_df[["Effect ID", "Base Vapor (°C)", "Live Vapor (°C)", "Base Brine (°C)", "Live Brine (°C)", "Base HTC"]]

    # 1st Effect HTC vs Overall HTC using exact physics described by Client
    try:
        brine_4_to_7_avg = st.session_state.shared_effect_df[st.session_state.shared_effect_df['Effect ID'].isin(['Effect 4', 'Effect 5', 'Effect 6', 'Effect 7'])]['Live Brine (°C)'].mean()
    except: 
        brine_4_to_7_avg = 55.0

    ops_data['dt_1st'] = get_v('mra_t1') - get_v('mra_bt1')
    ops_data['q_1st'] = get_v('sw_upper') * (get_v('mra_bt1') - brine_4_to_7_avg) * 0.930
    ops_data['htc_1st'] = (ops_data['q_1st'] / (get_v('area_1st') * ops_data['dt_1st'])) * 1000 if ops_data['dt_1st'] > 0 and get_v('area_1st') > 0 else 0
    ops_data['fouling_1st'] = 1 / ops_data['htc_1st'] if ops_data['htc_1st'] > 0 else 0

    ops_data['dt_overall'] = get_v('mra_t1') - get_v('brine_out_t') # Simple Delta T due to broken Vapor Out sensor
    ops_data['q_overall'] = get_v('sw_total') * (get_v('brine_out_t') - get_v('sw_in_t')) * 0.930
    ops_data['htc_overall'] = (ops_data['q_overall'] / (get_v('area_overall') * ops_data['dt_overall'])) * 1000 if ops_data['dt_overall'] > 0 and get_v('area_overall') > 0 else 0
    ops_data['fouling_overall'] = 1 / ops_data['htc_overall'] if ops_data['htc_overall'] > 0 else 0

    mra_data = {}
    coefs = st.session_state.mra_coef 
    model_type = coefs.get("model_type", "OLS")
    live_input_arr = [get_v('mra_press'), get_v('mra_t1'), get_v('sw_upper'), get_v('mra_bt1'), get_v('brine_ret'), get_v('steam'), get_v('stm_in_t'), get_v('chem_anti_ppm')]
    
    if model_type == "OLS":
        mra_data['Predicted'] = (coefs["Intercept"] + (coefs["Press_1st"] * live_input_arr[0]) + (coefs["Temp_1st"] * live_input_arr[1]) + (coefs["SW_Upper"] * live_input_arr[2]) + (coefs["Brine_Temp_1st"] * live_input_arr[3]) + (coefs["Brine_Flow"] * live_input_arr[4]) + (coefs["LP_Steam"] * live_input_arr[5]) + (coefs["Steam_Temp"] * live_input_arr[6]) + (coefs.get("Anti_PPM", MRA_COEF_2014["Anti_PPM"]) * live_input_arr[7]))
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
    param_names = ["1st Effect Press", "1st Effect Temp", "SW Feed to 1st Effect", "1st Brine Temp", "Brine Flow", "LP Steam", "Steam Temp", "Antiscalant PPM"]
    
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
            water_data[cat][param] = {'min': details['lim'][0], 'max': details['lim'][1], 'val': val, 'status': status}
            
    chem_data = {
        'anti_ppm': get_v('chem_anti_ppm'), 
        'anti_cons': get_v('chem_anti_cons'), 
        'foam_ppm': get_v('chem_foam_ppm'), 
        'foam_cons': get_v('chem_foam_cons')
    }

    # --- TAB 0: INPUTS ---
    with tabs[0]:
        st.subheader("Central Data Entry Panel")
        with st.expander("1. Hydraulics & Mass Balance", expanded=True):
            c1, c2, c3 = st.columns(3)
            with c1:
                st.number_input("LP Steam (TPH)", key="in_steam", on_change=sync_var, args=('steam', 'in_steam'))
                st.number_input("SW Feed to 1st Effect (m³/h)", key="in_sw_up", on_change=sync_var, args=('sw_upper', 'in_sw_up'))
            with c2:
                st.number_input("Desal Production (m³/h)", key="in_desal", on_change=sync_var, args=('desal', 'in_desal'))
                st.number_input("Total Sea Water Feed (FFC) (m³/h)", key="in_sw_tot", on_change=sync_var, args=('sw_total', 'in_sw_tot'))
            with c3:
                st.number_input("Gross Production (m³/h)", key="in_gross", on_change=sync_var, args=('gross', 'in_gross'))
                st.number_input("Brine Water Return (m³/h)", key="in_brine", on_change=sync_var, args=('brine_ret', 'in_brine'))
                
        with st.expander("2. Plant Temperatures & MRA Variables", expanded=False):
            t1, t2, t3, t4 = st.columns(4)
            with t1: 
                st.number_input("SW Cond I/L Temp (°C) [FFC]", key="in_sw_in", on_change=sync_var, args=('sw_in_t', 'in_sw_in'))
                st.number_input("1st Effect Press (mmHg)", key="in_press", on_change=sync_var, args=('mra_press', 'in_press'))
            with t2: 
                st.number_input("Final Brine Temp (°C)", key="in_brine_out", on_change=sync_var, args=('brine_out_t', 'in_brine_out'))
                st.number_input("1st Effect Temp (°C)", key="in_t1", on_change=sync_var, args=('mra_t1', 'in_t1'))
            with t3: 
                st.number_input("LP Steam Inlet Temp (°C)", key="in_stm_in", on_change=sync_var, args=('stm_in_t', 'in_stm_in'))
                st.number_input("1st Brine Temp (°C)", key="in_bt1", on_change=sync_var, args=('mra_bt1', 'in_bt1'))
            with t4: 
                st.number_input("Vapour Outlet Temp (°C)", key="in_vap_out", on_change=sync_var, args=('vap_out_t', 'in_vap_out'))
                
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
        
        st.text_area("Remarks & Observations", key="in_remarks", on_change=sync_var, args=('remarks', 'in_remarks'), placeholder="Enter shift notes, TT errors, or daily observations here...")

    # --- TAB 1: FLOW KPIs ---
    with tabs[1]:
        st.subheader("Mass Balance & KPI Dashboard")
        c1, c2, c3 = st.columns(3)
        with c1:
            st.number_input("LP Steam (TPH)", key="t1_steam", on_change=sync_var, args=('steam', 't1_steam'))
            st.number_input("SW Feed to 1st Effect (m³/h)", key="t1_sw_up", on_change=sync_var, args=('sw_upper', 't1_sw_up'))
        with c2:
            st.number_input("Desal Production (m³/h)", key="t1_desal", on_change=sync_var, args=('desal', 't1_desal'))
            st.number_input("Total Sea Water Feed (FFC) (m³/h)", key="t1_sw_tot", on_change=sync_var, args=('sw_total', 't1_sw_tot'))
        with c3:
            st.number_input("Gross Production (m³/h)", key="t1_gross", on_change=sync_var, args=('gross', 't1_gross'))
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
        st.subheader("Thermal Integrity & Fouling")
        c_area1, c_area2 = st.columns(2)
        with c_area1: st.number_input("1st Effect Surface Area (m²)", key="t2_area_1st", on_change=sync_var, args=('area_1st', 't2_area_1st'))
        with c_area2: st.number_input("Overall Surface Area (m²)", key="t2_area_overall", on_change=sync_var, args=('area_overall', 't2_area_overall'))

        h1, h2, h3 = st.columns(3)
        with h1: st.number_input("SW Cond I/L Temp (°C) [FFC Inlet]", key="t2_sw_in", on_change=sync_var, args=('sw_in_t', 't2_sw_in'))
        with h2: st.number_input("Final Brine Temp (°C)", key="t2_brine_out", on_change=sync_var, args=('brine_out_t', 't2_brine_out'))
        with h3: st.number_input("Vapour Outlet Temp (°C)", key="t2_vap_out", on_change=sync_var, args=('vap_out_t', 't2_vap_out'))
        
        st.divider()
        st.markdown("### Overall Plant Performance")
        r1, r2, r3, r4 = st.columns(4)
        r1.metric("Overall ΔT", f"{ops_data['dt_overall']:.2f} °C")
        r2.metric("Plant Q (Actual)", f"{ops_data['q_overall']:,.0f} Kcal/hr")
        r3.metric("Overall HTC (U)", f"{ops_data['htc_overall']:.2f} W/m²K")
        r4.metric("Overall Fouling Factor", f"{ops_data['fouling_overall']:.6f}")

        st.markdown("### 1st Effect Performance")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("1st Effect ΔT", f"{ops_data['dt_1st']:.2f} °C")
        m2.metric("1st Effect Q (Actual)", f"{ops_data['q_1st']:,.0f} Kcal/hr")
        m3.metric("1st Effect HTC (U)", f"{ops_data['htc_1st']:.2f} W/m²K")
        m4.metric("1st Effect Fouling Factor", f"{ops_data['fouling_1st']:.6f}")
        
        if not get_v('skip_eff'):
            st.divider()
            st.markdown("#### Effect-wise Heat Transfer Coefficients (Base vs Actual)")
            e_df2 = display_effect_df
            htc_data = []
            
            # THE FIX: Cascade logic updated to start exactly at the 1st Effect Vapor Temperature 
            prev_vap = get_v('mra_t1') 
            
            for idx, row in e_df2.iterrows():
                live_bri = row['Live Brine (°C)']
                dt_eff = prev_vap - live_bri 
                
                # Assuming area_1st is an approximate average bundle area for missing effects
                eff_htc = (ops_data['Q_act'] / (get_v('area_1st') * dt_eff)) * 1000 if dt_eff > 0 and ops_data['Q_act'] > 0 else 0
                
                htc_data.append({
                    "Effect ID": row['Effect ID'], 
                    "Base HTC": row['Base HTC'], 
                    "Actual HTC": round(eff_htc, 1)
                })
                prev_vap = row['Live Vapor (°C)'] 
                
            htc_df = pd.DataFrame(htc_data)
            htc_col1, htc_col2 = st.columns([1, 1.5])
            with htc_col1: 
                st.dataframe(htc_df, use_container_width=True, hide_index=True)
            with htc_col2:
                chart_df = htc_df.melt(id_vars='Effect ID', value_vars=['Base HTC', 'Actual HTC'], var_name='Metric', value_name='HTC (W/m²K)')
                chart_df['Effect ID'] = pd.Categorical(chart_df['Effect ID'], categories=[f"Effect {i}" for i in range(1, 12)], ordered=True)
                htc_chart = alt.Chart(chart_df).mark_bar().encode(
                    x=alt.X('Metric:N', title=None, axis=alt.Axis(labels=False, ticks=False)),
                    y=alt.Y('HTC (W/m²K):Q'),
                    color=alt.Color('Metric:N', scale=alt.Scale(range=['#1f77b4', '#ff7f0e'])),
                    column=alt.Column('Effect ID:N', header=alt.Header(labelOrient='bottom', titleOrient='bottom'))
                ).properties(width=40)
                st.altair_chart(htc_chart, use_container_width=False)

    # --- TAB 3: WATER ANALYSIS ---
    with tabs[3]:
        st.subheader("Laboratory QA/QC vs Limits")
        if not get_v('skip_wq'):
            w_col1, w_col2 = st.columns(2)
            with w_col1:
                st.markdown("### 🌊 Feed Sea Water")
                for param, d in WATER_SPECS["Feed"].items():
                    c_in, c_chk = st.columns([2, 2])
                    with c_in: st.number_input(f"{param} ({d['lim'][0]}-{d['lim'][1]})", key=f"t3_{d['var']}", on_change=sync_var, args=(d['var'], f"t3_{d['var']}"))
                    c_chk.markdown(f"<div style='margin-top:30px'>{water_data['Feed'][param]['status']}</div>", unsafe_allow_html=True)
            with w_col2:
                st.markdown("### 🚰 Desal Product")
                for param, d in WATER_SPECS["Product"].items():
                    c_in, c_chk = st.columns([2, 2])
                    with c_in: st.number_input(f"{param} ({d['lim'][0]}-{d['lim'][1]})", key=f"t3_{d['var']}", on_change=sync_var, args=(d['var'], f"t3_{d['var']}"))
                    c_chk.markdown(f"<div style='margin-top:30px'>{water_data['Product'][param]['status']}</div>", unsafe_allow_html=True)

    # --- TAB 4: CHEMICAL DOSING ---
    with tabs[4]:
        st.subheader("Chemical Dosing & Inventory Tracking")
        st.number_input("Total Sea Water Feed (FFC) (m³/h)", key="t4_sw_tot", on_change=sync_var, args=('sw_total', 't4_sw_tot'))
        st.divider()
        cc1, cc2 = st.columns(2)
        with cc1:
            st.markdown("### 🧪 Kem Watreat r 3687 (Antiscalant)")
            st.number_input("Target Dosing Level (PPM)", key="t4_anti_ppm", on_change=sync_var, args=('chem_anti_ppm', 't4_anti_ppm'))
            if st.button("🧪 Auto-Calculate Optimal Dose", key="btn_auto_anti_4"): 
                st.info("🚀 AI-driven Thermodynamic Scaling Engine & Auto-Dosing will be available shortly!")
            theo_anti = (ops_data['SW Total'] * get_v('chem_anti_ppm')) / 1000
            st.info(f"**Theoretical Requirement:** {theo_anti:.2f} kg/hr")
            st.number_input("Actual Consumption (kg/hr)", key="t4_anti_cons", on_change=sync_var, args=('chem_anti_cons', 't4_anti_cons'))
        with cc2:
            st.markdown("### 🫧 Kem Antifoam 1795")
            st.number_input("Target Dosing Level (PPM)", key="t4_foam_ppm", on_change=sync_var, args=('chem_foam_ppm', 't4_foam_ppm'))
            if st.button("🧪 Auto-Calculate Optimal Dose", key="btn_auto_foam_4"): 
                st.info("🚀 AI-driven Thermodynamic Scaling Engine & Auto-Dosing will be available shortly!")
            theo_foam = (ops_data['SW Total'] * get_v('chem_foam_ppm')) / 1000
            st.info(f"**Theoretical Requirement:** {theo_foam:.2f} kg/hr")
            st.number_input("Actual Consumption (kg/hr)", key="t4_foam_cons", on_change=sync_var, args=('chem_foam_cons', 't4_foam_cons'))

    # --- TAB 5: MRA NORMALIZATION ---
    with tabs[5]:
        st.subheader("MRA Fouling Defense")
        controls_col, calc_col = st.columns([1, 2])
        with controls_col:
            st.slider("1st Effect Press (mmHg)", key="t5_press", min_value=100.0, max_value=400.0, on_change=sync_var, args=('mra_press', 't5_press'))
            st.slider("1st Effect Temp (°C)", key="t5_t1", min_value=50.0, max_value=90.0, on_change=sync_var, args=('mra_t1', 't5_t1'))
            st.slider("SW Feed to 1st Effect (m³/h)", key="t5_sw_up", min_value=300.0, max_value=1500.0, on_change=sync_var, args=('sw_upper', 't5_sw_up'))
            st.slider("1st Brine Temp (°C)", key="t5_bt1", min_value=40.0, max_value=80.0, on_change=sync_var, args=('mra_bt1', 't5_bt1'))
            st.slider("Brine Flow (m³/h)", key="t5_bflow", min_value=800.0, max_value=2000.0, on_change=sync_var, args=('brine_ret', 't5_bflow'))
            st.slider("LP Steam (TPH)", key="t5_steam", min_value=40.0, max_value=150.0, on_change=sync_var, args=('steam', 't5_steam'))
            st.slider("Steam Temp (°C)", key="t5_stm_t", min_value=140.0, max_value=220.0, on_change=sync_var, args=('stm_in_t', 't5_stm_t'))
            st.slider("Antiscalant PPM", key="t5_anti", min_value=0.0, max_value=10.0, step=0.1, on_change=sync_var, args=('chem_anti_ppm', 't5_anti'))

        with calc_col:
            k1, k2, k3 = st.columns(3)
            k1.metric("Actual Gross SCADA", f"{mra_data['Actual']:.1f} m³/h")
            k2.metric(f"Predicted ({model_type})", f"{mra_data['Predicted']:.1f} m³/h")
            
            diff_pct = (mra_data['Residual'] / mra_data['Predicted']) * 100 if mra_data['Predicted'] > 0 else 0
            if diff_pct <= -5.0: 
                k3.error(f"Residual: {mra_data['Residual']:.1f} TPH ({diff_pct:.1f}%) - Please clean the machine")
            elif diff_pct <= -4.0: 
                k3.warning(f"Residual: {mra_data['Residual']:.1f} TPH ({diff_pct:.1f}%) - Increase antiscalant dosing")
            else: 
                k3.success(f"Residual: {mra_data['Residual']:.1f} TPH ({diff_pct:.1f}%) - CLEAN")
                
            if model_type != "OLS": 
                st.info("ℹ️ **AI Model Active:** Variance Impact breakdown is only available for purely linear OLS models.")
            st.dataframe(mra_data['Variance_DF'].style.format({"Baseline": "{:.1f}", "Live Input": "{:.1f}", "Deviation": "{:+.1f}", "Regression Weight": "{:.3f}", "Impact (TPH)": "{:+.1f}"}, na_rep="-"), use_container_width=True, hide_index=True)

    # --- TAB 6: ENTERPRISE REPORTING SUITE ---
    with tabs[6]:
        st.subheader("Intelligence & Reporting Center")
        rep_tabs = st.tabs(["📅 Today's Dashboard", "📆 Master Database", "📊 Long-Term Health", "📈 Interactive Explorer"])
        
        with rep_tabs[0]:
            m_col1, m_col2, m_col3, m_col4 = st.columns(4)
            m_col1.metric("Date", log_date.strftime('%d/%m/%Y')) 
            m_col2.metric("Gross Production", f"{ops_data['Gross Prod']} m³/h", delta=f"{ops_data['Gross Prod'] - 1000:.0f} from Design" if ops_data['Gross Prod'] < 1000 else None)
            m_col3.metric("System GOR", f"{ops_data['GOR']:.2f}", delta=f"{ops_data['GOR'] - 10.5:.2f} from Target" if ops_data['GOR'] < 10.5 else None)
            
            diff_pct = (mra_data['Residual'] / mra_data['Predicted']) * 100 if mra_data['Predicted'] > 0 else 0
            if diff_pct <= -5.0: 
                delta_text, d_color = f"{diff_pct:.1f}% (Please clean machine)", "inverse"
            elif diff_pct <= -4.0: 
                delta_text, d_color = f"{diff_pct:.1f}% (Increase antiscalant)", "inverse"
            else: 
                delta_text, d_color = f"{diff_pct:.1f}% (Clean)", "normal"
                
            m_col4.metric("Fouling Residual", f"{mra_data['Residual']:.1f} TPH", delta=delta_text, delta_color=d_color)
            
            st.divider()
            graph_col1, graph_col2 = st.columns(2)
            with graph_col1:
                if model_type == "OLS":
                    st.markdown("#### ⚖️ Variance Impact (TPH)")
                    impact_chart = alt.Chart(mra_data['Variance_DF']).mark_bar().encode(x=alt.X('Impact (TPH):Q'), y=alt.Y('Parameter:N', sort='-x', title=''), color=alt.condition(alt.datum['Impact (TPH)'] > 0, alt.value('#2ca02c'), alt.value('#d62728')), tooltip=['Parameter', 'Impact (TPH)']).properties(height=300)
                    st.altair_chart(impact_chart, use_container_width=True)
                else:
                    st.markdown("#### ⚖️ Feature Importances (AI Mode)")
                    impact_chart = alt.Chart(mra_data['Variance_DF']).mark_bar(color='#1f77b4').encode(x=alt.X('Regression Weight:Q', title="Importance Weight"), y=alt.Y('Parameter:N', sort='-x', title=''), tooltip=['Parameter', 'Regression Weight']).properties(height=300)
                    st.altair_chart(impact_chart, use_container_width=True)

            with graph_col2:
                st.markdown("#### 🌊 Flow Distribution")
                unaccounted = ops_data['SW Total'] - (ops_data['Desal'] + ops_data['Brine Return'])
                mb_data = pd.DataFrame({'Stream': ['Desal (Net)', 'Brine', 'Losses'], 'Volume': [ops_data['Desal'], ops_data['Brine Return'], unaccounted if unaccounted > 0 else 0]})
                donut = alt.Chart(mb_data).mark_arc(innerRadius=50).encode(theta=alt.Theta("Volume:Q"), color=alt.Color("Stream:N", scale=alt.Scale(scheme='set2')), tooltip=['Stream', 'Volume']).properties(height=300)
                st.altair_chart(donut, use_container_width=True)

            st.divider()
            st.markdown("### 💾 Commit Today's Data")
            c_pwd, c_save, c_export, c_csv = st.columns([1.5, 1, 1, 1])
            with c_pwd: 
                pwd_append = st.text_input("Master Password", type="password", key="pwd_append", label_visibility="collapsed", placeholder="🔑 Enter Master Password to Commit")
            with c_save:
                if st.button("💾 Append Data", use_container_width=True):
                    if pwd_append == "12345678":
                        new_log = pd.DataFrame({
                            "Date": [log_date_str], 
                            "Gross Prod (m3/h)": [ops_data['Gross Prod']], 
                            "Desal (m3/h)": [ops_data['Desal']], 
                            "Steam (TPH)": [ops_data['Steam']], 
                            "Total SW Feed (m3/h)": [ops_data['SW Total']], 
                            "SW Feed to 1st Effect (m3/h)": [get_v('sw_upper')], 
                            "GOR": [round(ops_data['GOR'], 2)], 
                            "Overall HTC": [round(ops_data['htc_overall'], 2)], 
                            "1st Effect HTC": [round(ops_data['htc_1st'], 2)], 
                            "Residual": [round(mra_data['Residual'], 1)], 
                            "Antiscalant (kg)": [chem_data['anti_cons']], 
                            "Antifoam (kg)": [chem_data['foam_cons']], 
                            "Press_1st": [get_v('mra_press')], 
                            "Temp_1st": [get_v('mra_t1')], 
                            "Brine_Temp_1st": [get_v('mra_bt1')], 
                            "Brine_Flow": [get_v('brine_ret')], 
                            "Steam_Temp": [get_v('stm_in_t')], 
                            "Anti_PPM": [get_v('chem_anti_ppm')], 
                            "SW_Cond_Inlet_Temp": [get_v('sw_in_t')], 
                            "Final_Brine_Temp": [get_v('brine_out_t')], 
                            "Vap_Out_Temp": [get_v('vap_out_t')], 
                            "Remarks": [get_v('remarks')],
                            "Area_1st": [get_v('area_1st')], 
                            "Area_Overall": [get_v('area_overall')]
                        })
                        for cat in ['Feed', 'Product']:
                            for param, details in WATER_SPECS[cat].items(): 
                                new_log[details['var']] = [get_v(details['var'])]
                            
                        st.session_state.daily_logs = pd.concat([st.session_state.daily_logs, new_log], ignore_index=True)
                        save_database(db_conn, st.session_state.daily_logs)
                        st.success("✅ Master Database Updated!")
                    elif pwd_append != "": 
                        st.error("❌ Incorrect Password.")
            with c_export:
                word_file = generate_comprehensive_report(log_date, ops_data, display_effect_df, water_data, chem_data, mra_data, get_v('skip_eff'), get_v('skip_wq'), get_v('remarks'))
                st.download_button("📄 Export Report (.docx)", data=word_file, file_name=f"MED4_Daily_{log_date_str}.docx", mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document", use_container_width=True)
            with c_csv:
                csv_file = generate_daily_csv(log_date, ops_data, display_effect_df, water_data, chem_data, mra_data)
                st.download_button("📊 Export Report (.csv)", data=csv_file, file_name=f"MED4_Daily_{log_date_str}.csv", mime="text/csv", use_container_width=True)

        with rep_tabs[1]:
            st.markdown("#### 📆 Editable Master Log Database")
            edited_db = st.data_editor(st.session_state.daily_logs, num_rows="dynamic", use_container_width=True)
            c_sync_pwd, c_sync, c_dl = st.columns([2, 1, 1])
            with c_sync_pwd: 
                pwd_sync = st.text_input("Master Password", type="password", key="pwd_sync", label_visibility="collapsed", placeholder="🔑 Enter Master Password to Sync")
            with c_sync:
                if st.button("☁️ Sync Edits", use_container_width=True):
                    if pwd_sync == "12345678":
                        st.session_state.daily_logs = edited_db
                        save_database(db_conn, st.session_state.daily_logs)
                        st.success("✅ Database Overwritten!")
                    else: 
                        st.error("❌ Incorrect Password.")
            with c_dl:
                st.download_button("📥 Download CSV Backup", data=st.session_state.daily_logs.to_csv(index=False).encode('utf-8'), file_name=f"MED4_Master.csv", mime='text/csv', use_container_width=True)

            st.divider()
            st.markdown("#### 📊 Monthly Report Generator")
            if not st.session_state.daily_logs.empty:
                df_logs = st.session_state.daily_logs.copy()
                df_logs['Date'] = pd.to_datetime(df_logs['Date'], dayfirst=True, errors='coerce')
                month_data = df_logs[(df_logs['Date'].dt.month == log_date.month) & (df_logs['Date'].dt.year == log_date.year)].copy()
                if not month_data.empty:
                    if st.button("📄 Generate Monthly Report (.docx)", use_container_width=True):
                        monthly_doc = generate_monthly_report(month_data, log_date.strftime('%B'), str(log_date.year))
                        st.download_button("📥 Download Monthly Report", data=monthly_doc, file_name=f"MED4_Monthly_{log_date.strftime('%b_%Y')}.docx", mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document")

        with rep_tabs[2]:
            if not st.session_state.daily_logs.empty:
                df_logs = st.session_state.daily_logs.copy()
                df_logs['Date'] = pd.to_datetime(df_logs['Date'], dayfirst=True, errors='coerce')
                
                # Dynamic mapping for SW Feed historical naming conventions
                df_logs['Total SW Feed (m3/h)'] = pd.to_numeric(df_logs.get('Total SW Feed (m3/h)', df_logs.get('SW Feed (m3/h)', df_logs.get('Total Sea Water Feed (FFC)', 0))), errors='coerce')
                df_logs['Recovery (%)'] = np.where(df_logs['Total SW Feed (m3/h)'] > 0, (pd.to_numeric(df_logs['Gross Prod (m3/h)'], errors='coerce') / df_logs['Total SW Feed (m3/h)']) * 100, 0)
                
                df_logs['Actual Production'] = pd.to_numeric(df_logs['Gross Prod (m3/h)'], errors='coerce')
                df_logs['Residual_Val'] = pd.to_numeric(df_logs['Residual'], errors='coerce')
                df_logs['Predicted Production'] = df_logs['Actual Production'] - df_logs['Residual_Val']
                df_logs['Overall_HTC_Val'] = pd.to_numeric(df_logs['Overall HTC'], errors='coerce')
                df_logs['GOR_Val'] = pd.to_numeric(df_logs['GOR'], errors='coerce')
                
                q_col1, q_col2 = st.columns(2)
                with q_col1:
                    st.markdown("#### 📉 Recovery Trend")
                    if len(df_logs) > 1:
                        rec_chart = alt.Chart(df_logs).mark_circle().encode(x=alt.X('Date:T', title="Date"), y=alt.Y('Recovery (%):Q', scale=alt.Scale(zero=False)))
                        st.altair_chart(rec_chart + rec_chart.transform_regression('Date', 'Recovery (%)').mark_line(color='red'), use_container_width=True)
                with q_col2:
                    st.markdown("#### 🌡️ HTC Degradation")
                    if len(df_logs) > 1:
                        htc_chart = alt.Chart(df_logs).mark_line(point=True, color='orange').encode(x=alt.X('Date:T', title="Date"), y=alt.Y('Overall_HTC_Val:Q', scale=alt.Scale(zero=False), title="Overall HTC (W/m²K)"))
                        st.altair_chart(htc_chart + htc_chart.transform_regression('Date', 'Overall_HTC_Val').mark_line(color='black'), use_container_width=True)

                st.divider()
                
                q_col3, q_col4 = st.columns(2)
                with q_col3:
                    st.markdown("#### ⚖️ Actual vs. Predicted Production")
                    if len(df_logs) > 1:
                        fold_df = df_logs[['Date', 'Actual Production', 'Predicted Production']].melt('Date', var_name='Metric', value_name='Volume (m³/h)')
                        prod_chart = alt.Chart(fold_df).mark_line(point=True).encode(
                            x=alt.X('Date:T', title="Date"),
                            y=alt.Y('Volume (m³/h):Q', scale=alt.Scale(zero=False)),
                            color=alt.Color('Metric:N', scale=alt.Scale(domain=['Actual Production', 'Predicted Production'], range=['#1f77b4', '#ff7f0e'])),
                            strokeDash=alt.condition(alt.datum.Metric == 'Predicted Production', alt.value([5, 5]), alt.value([0])),
                            tooltip=['Date:T', 'Metric', 'Volume (m³/h)']
                        )
                        st.altair_chart(prod_chart, use_container_width=True)
                with q_col4:
                    st.markdown("#### 💰 System GOR Trend")
                    if len(df_logs) > 1:
                        gor_chart = alt.Chart(df_logs).mark_line(point=True, color='green').encode(
                            x=alt.X('Date:T', title="Date"),
                            y=alt.Y('GOR_Val:Q', scale=alt.Scale(zero=False), title="Gain Output Ratio"),
                            tooltip=['Date:T', 'GOR_Val']
                        )
                        st.altair_chart(gor_chart + gor_chart.transform_regression('Date', 'GOR_Val').mark_line(color='red', strokeDash=[5, 5]), use_container_width=True)

        with rep_tabs[3]:
            st.markdown("#### 📈 Interactive Custom Chart Builder")
            if not st.session_state.daily_logs.empty:
                exp_df = st.session_state.daily_logs.copy()
                num_cols = [col for col in exp_df.columns if col not in ['Date']]
                
                x_c, y_c, t_c = st.columns(3)
                with x_c: 
                    exp_x = st.selectbox("Select X-Axis", ['Date'] + num_cols, index=0)
                with y_c: 
                    exp_y = st.selectbox("Select Y-Axis", num_cols, index=0)
                with t_c: 
                    exp_type = st.selectbox("Select Chart Type", ["Line Chart", "Scatter Plot", "Bar Chart"])
                
                if exp_x == 'Date': 
                    exp_df['Date'] = pd.to_datetime(exp_df['Date'], dayfirst=True, errors='coerce')
                
                if exp_type == "Line Chart":
                    chart = alt.Chart(exp_df).mark_line(point=True).encode(x=alt.X(f"{exp_x}{':T' if exp_x == 'Date' else ':Q'}"), y=alt.Y(f"{exp_y}:Q", scale=alt.Scale(zero=False)), tooltip=[exp_x, exp_y])
                elif exp_type == "Scatter Plot":
                    chart = alt.Chart(exp_df).mark_circle(size=80).encode(x=alt.X(f"{exp_x}{':T' if exp_x == 'Date' else ':Q'}"), y=alt.Y(f"{exp_y}:Q", scale=alt.Scale(zero=False)), tooltip=[exp_x, exp_y])
                else:
                    chart = alt.Chart(exp_df).mark_bar().encode(x=alt.X(f"{exp_x}{':T' if exp_x == 'Date' else ':N'}"), y=alt.Y(f"{exp_y}:Q"), tooltip=[exp_x, exp_y])
                
                st.altair_chart(chart.interactive(), use_container_width=True)
            else:
                st.info("Upload data to the Master Database to build custom charts.")

    # --- TAB 7: AI MODEL SELECTOR ---
    with tabs[7]:
        st.subheader("🤖 AI & OLS Calibration Suite")
        if not SKLEARN_INSTALLED:
            st.error("🚨 'scikit-learn' is not installed. Please add it to your requirements.txt.")
        else:
            st.markdown("### 💾 Manage Baseline Model")
            st.markdown(f"**Current Active Brain:** `{model_type}`")
            c_reset, _ = st.columns([1, 1])
            with c_reset:
                if st.button("🔄 Factory Reset to purely Linear 2014 Defaults", use_container_width=True):
                    st.session_state.mra_coef = MRA_COEF_2014.copy()
                    save_config(db_conn, st.session_state.mra_coef)
                    st.success("✅ Restored verified 2014 baseline model. Reloading...")
                    time.sleep(1.5)
                    st.rerun()

            st.divider()

            st.markdown("### 📊 Train & Compare Multi-Models")
            st.markdown("Upload step-test data to simultaneously train pure OLS, Random Forest, and XGBoost models. You can then compare their accuracies and select the best brain for the plant.")
            
            req_cols = ["Date", "Gross Prod", "Press_1st", "Temp_1st", "SW_Upper", "Brine_Temp_1st", "Brine_Flow", "LP_Steam", "Steam_Temp", "Anti_PPM"]
            template_df = pd.DataFrame(columns=req_cols)
            st.download_button(label="1️⃣ Download Blank Training CSV Template", data=template_df.to_csv(index=False).encode('utf-8'), file_name='MED4_ML_Template.csv', mime='text/csv')
            
            st.divider()
            
            uploaded_file = st.file_uploader("2️⃣ Upload Populated Training Data", type=["csv"], key="mra_trainer")
            
            if uploaded_file is not None:
                try:
                    df_train = pd.read_csv(uploaded_file)
                    if not all(col in df_train.columns for col in req_cols):
                        st.error(f"❌ Uploaded CSV is missing required columns.")
                    else:
                        for col in req_cols:
                            if col != "Date":
                                if df_train[col].dtype == object:
                                    df_train[col] = pd.to_numeric(df_train[col].astype(str).str.replace(',', '', regex=False), errors='coerce')
                        
                        df_train = df_train.dropna(subset=[c for c in req_cols if c != "Date"])
                        st.success(f"✅ Training Initialized on {len(df_train)} operational records.")
                        
                        if len(df_train) > 0:
                            X = df_train[["Press_1st", "Temp_1st", "SW_Upper", "Brine_Temp_1st", "Brine_Flow", "LP_Steam", "Steam_Temp", "Anti_PPM"]]
                            Y = df_train["Gross Prod"]
                            
                            model_ols = LinearRegression(fit_intercept=True).fit(X, Y)
                            r2_ols = r2_score(Y, model_ols.predict(X))
                            
                            model_rf = RandomForestRegressor(n_estimators=100, random_state=42).fit(X, Y)
                            r2_rf = r2_score(Y, model_rf.predict(X))
                            
                            if XGB_INSTALLED:
                                model_xgb = xgb.XGBRegressor(n_estimators=100, random_state=42).fit(X, Y)
                                r2_xgb = r2_score(Y, model_xgb.predict(X))
                            
                            st.markdown("### 🏆 Model Scoreboard")
                            m1, m2, m3 = st.columns(3)
                            m1.metric("1. OLS (Linear)", f"{r2_ols * 100:.2f}%")
                            m2.metric("2. Random Forest", f"{r2_rf * 100:.2f}%")
                            if XGB_INSTALLED: 
                                m3.metric("3. XGBoost", f"{r2_xgb * 100:.2f}%")
                            else: 
                                m3.warning("XGBoost library not installed.")
                            
                            st.markdown("#### Feature Importances / Coefficients")
                            comp_dict = {
                                "Parameter": ["Press_1st", "Temp_1st", "SW_Upper", "Brine_Temp_1st", "Brine_Flow", "LP_Steam", "Steam_Temp", "Anti_PPM"],
                                "OLS (Coefficients)": np.round(model_ols.coef_, 4),
                                "Random Forest (Importance %)": np.round(model_rf.feature_importances_ * 100, 2)
                            }
                            if XGB_INSTALLED: 
                                comp_dict["XGBoost (Importance %)"] = np.round(model_xgb.feature_importances_ * 100, 2)
                            
                            st.dataframe(pd.DataFrame(comp_dict).style.format(precision=4), use_container_width=True, hide_index=True)
                            
                            st.markdown("### 💾 Deploy Selected Brain")
                            opts = ["OLS (Linear)", "Random Forest"]
                            if XGB_INSTALLED: 
                                opts.append("XGBoost")
                                
                            selected_model = st.radio("Select which model logic to run the dashboard predictions:", opts)
                            
                            if st.button("🔥 Deploy Model Permanently", type="primary", use_container_width=True):
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
                                    
                                st.success(f"✅ Successfully deployed {selected_model}! Reloading application...")
                                time.sleep(1.5)
                                st.rerun()
                        else:
                            st.error("🚨 No valid numeric data found.")
                except Exception as e:
                    st.error(f"Error processing file: {e}")

    # --- TAB 8: BULK UPLOAD ---
    with tabs[8]:
        st.subheader("📤 Bulk Data Upload & Sync")
        st.markdown("Download the Bulk Upload Template, fill in your daily historical data, and upload it here. The system will automatically calculate all KPIs, MRA Residuals, and HTC for every row, allowing you to quickly backfill your Master Database.")
        
        bulk_cols = [
            "Date (DD/MM/YYYY)", "Gross Prod (m3/h)", "Desal (m3/h)", "Steam (TPH)", "Total Sea Water Feed (m3/h)", 
            "SW Feed to 1st Effect (m3/h)", "Brine_Flow", "Press_1st", "Temp_1st", "Brine_Temp_1st", "Steam_Temp", 
            "SW Cond I/L Temp", "Final Brine Temp", "Anti_PPM", "Antiscalant (kg)", "Antifoam (kg)", "Remarks"
        ]
        
        bulk_template = pd.DataFrame(columns=bulk_cols)
        st.download_button(label="1️⃣ Download Bulk Upload Template", data=bulk_template.to_csv(index=False).encode('utf-8'), file_name='MED4_Bulk_Template.csv', mime='text/csv')
        
        st.divider()
        
        bulk_file = st.file_uploader("2️⃣ Upload Populated Bulk Data", type=["csv"], key="bulk_uploader")
        
        if bulk_file is not None:
            try:
                df_bulk = pd.read_csv(bulk_file)
                if 'Date' in df_bulk.columns and 'Date (DD/MM/YYYY)' not in df_bulk.columns:
                    df_bulk.rename(columns={'Date': 'Date (DD/MM/YYYY)'}, inplace=True)

                missing = [c for c in bulk_cols if c not in df_bulk.columns]
                
                if missing:
                    st.error(f"❌ Uploaded CSV is missing required columns: {', '.join(missing)}")
                else:
                    num_cols = [c for c in bulk_cols if c not in ["Date (DD/MM/YYYY)", "Remarks"]]
                    for col in num_cols:
                        if df_bulk[col].dtype == object:
                            df_bulk[col] = pd.to_numeric(df_bulk[col].astype(str).str.replace(',', '', regex=False), errors='coerce')
                    
                    df_bulk = df_bulk.dropna(subset=["Date (DD/MM/YYYY)", "Gross Prod (m3/h)"])
                    
                    if len(df_bulk) > 0:
                        for col_name, baseline_val in zip(
                            ['Press_1st', 'Temp_1st', 'SW Feed to 1st Effect (m3/h)', 'Brine_Temp_1st', 'Brine_Flow', 'Steam (TPH)', 'Steam_Temp', 'Anti_PPM'],
                            [231.76, 68.47, 553.63, 65.46, 1275.50, 71.75, 165.54, 4.82]
                        ):
                            df_bulk[col_name] = df_bulk[col_name].fillna(baseline_val)
                            
                        df_bulk['Date_Clean'] = pd.to_datetime(df_bulk['Date (DD/MM/YYYY)'], dayfirst=True, errors='coerce').dt.strftime('%Y-%m-%d')
                        df_bulk['GOR'] = np.where(df_bulk['Steam (TPH)'] > 0, df_bulk['Gross Prod (m3/h)'] / df_bulk['Steam (TPH)'], 0)
                        
                        if model_type == "OLS":
                            df_bulk['Predicted'] = (
                                coefs["Intercept"] + (coefs["Press_1st"] * df_bulk['Press_1st']) + 
                                (coefs["Temp_1st"] * df_bulk['Temp_1st']) + (coefs["SW_Upper"] * df_bulk['SW Feed to 1st Effect (m3/h)']) + 
                                (coefs["Brine_Temp_1st"] * df_bulk['Brine_Temp_1st']) + (coefs["Brine_Flow"] * df_bulk['Brine_Flow']) + 
                                (coefs["LP_Steam"] * df_bulk['Steam (TPH)']) + (coefs["Steam_Temp"] * df_bulk['Steam_Temp']) +
                                (coefs.get("Anti_PPM", MRA_COEF_2014["Anti_PPM"]) * df_bulk['Anti_PPM'])
                            )
                        else:
                            try:
                                active_model = joblib.load(AI_MODEL_FILE)
                                bulk_input_df = df_bulk[["Press_1st", "Temp_1st", "SW Feed to 1st Effect (m3/h)", "Brine_Temp_1st", "Brine_Flow", "Steam (TPH)", "Steam_Temp", "Anti_PPM"]].copy()
                                bulk_input_df.columns = ["Press_1st", "Temp_1st", "SW_Upper", "Brine_Temp_1st", "Brine_Flow", "LP_Steam", "Steam_Temp", "Anti_PPM"]
                                df_bulk['Predicted'] = active_model.predict(bulk_input_df)
                            except Exception: 
                                df_bulk['Predicted'] = 0.0
                                
                        df_bulk['Residual'] = df_bulk['Gross Prod (m3/h)'] - df_bulk['Predicted']
                        
                        df_bulk['SW Cond I/L Temp'] = df_bulk['SW Cond I/L Temp'].fillna(30.0)
                        df_bulk['Final Brine Temp'] = df_bulk['Final Brine Temp'].fillna(41.0)
                        df_bulk['Total Sea Water Feed (m3/h)'] = df_bulk['Total Sea Water Feed (m3/h)'].fillna(2100.0)
                        
                        area_overall = get_v('area_overall')
                        dt_overall = df_bulk['Temp_1st'] - df_bulk['Final Brine Temp']
                        q_overall = df_bulk['Total Sea Water Feed (m3/h)'] * (df_bulk['Final Brine Temp'] - df_bulk['SW Cond I/L Temp']) * 0.930
                        df_bulk['Overall HTC'] = np.where(dt_overall > 0, (q_overall / (area_overall * dt_overall)) * 1000, 0)

                        area_1st = get_v('area_1st')
                        brine_avg = 55.0 
                        dt_1st = df_bulk['Temp_1st'] - df_bulk['Brine_Temp_1st']
                        q_1st = df_bulk['SW Feed to 1st Effect (m3/h)'] * (df_bulk['Brine_Temp_1st'] - brine_avg) * 0.930
                        df_bulk['1st Effect HTC'] = np.where(dt_1st > 0, (q_1st / (area_1st * dt_1st)) * 1000, 0)
                        
                        db_ready_df = pd.DataFrame({
                            "Date": df_bulk['Date_Clean'], 
                            "Gross Prod (m3/h)": df_bulk['Gross Prod (m3/h)'],
                            "Desal (m3/h)": df_bulk['Desal (m3/h)'].fillna(0), 
                            "Steam (TPH)": df_bulk['Steam (TPH)'],
                            "Total SW Feed (m3/h)": df_bulk['Total Sea Water Feed (m3/h)'], 
                            "SW Feed to 1st Effect (m3/h)": df_bulk['SW Feed to 1st Effect (m3/h)'],
                            "GOR": df_bulk['GOR'].round(2),
                            "Overall HTC": df_bulk['Overall HTC'].round(2), 
                            "1st Effect HTC": df_bulk['1st Effect HTC'].round(2),
                            "Residual": df_bulk['Residual'].round(1),
                            "Antiscalant (kg)": df_bulk['Antiscalant (kg)'].fillna(0), 
                            "Antifoam (kg)": df_bulk['Antifoam (kg)'].fillna(0),
                            "Press_1st": df_bulk['Press_1st'], 
                            "Temp_1st": df_bulk['Temp_1st'],
                            "Brine_Temp_1st": df_bulk['Brine_Temp_1st'], 
                            "Brine_Flow": df_bulk['Brine_Flow'], 
                            "Steam_Temp": df_bulk['Steam_Temp'],
                            "Anti_PPM": df_bulk['Anti_PPM'], 
                            "SW_Cond_Inlet_Temp": df_bulk['SW Cond I/L Temp'],
                            "Final_Brine_Temp": df_bulk['Final Brine Temp'], 
                            "Remarks": df_bulk['Remarks'].fillna(""),
                            "Area_1st": area_1st, 
                            "Area_Overall": area_overall
                        })
                        for cat in ['Feed', 'Product']:
                            for param, details in WATER_SPECS[cat].items(): 
                                db_ready_df[details['var']] = get_v(details['var'])
                        
                        st.success(f"✅ Automatically calculated KPIs, HTC, and Residuals for {len(db_ready_df)} valid rows.")
                        st.dataframe(db_ready_df.style.format(precision=2), use_container_width=True, hide_index=True)
                        
                        st.markdown("### 💾 Commit Bulk Data")
                        c_pwd, c_save = st.columns([2, 2])
                        with c_pwd: 
                            pwd_bulk = st.text_input("Master Password", type="password", key="pwd_bulk", label_visibility="collapsed", placeholder="🔑 Enter Master Password to Sync")
                        with c_save:
                            if st.button("🔄 Append all to Master Database", use_container_width=True):
                                if pwd_bulk == "12345678":
                                    st.session_state.daily_logs = pd.concat([st.session_state.daily_logs, db_ready_df], ignore_index=True)
                                    st.session_state.daily_logs = st.session_state.daily_logs.drop_duplicates(subset=['Date'], keep='last').reset_index(drop=True)
                                    save_database(db_conn, st.session_state.daily_logs)
                                    st.success("✅ Bulk Data Successfully Synced to Database!")
                                    time.sleep(1.5)
                                    st.rerun()
                                elif pwd_bulk != "": 
                                    st.error("❌ Incorrect Password.")
                    else:
                        st.error("🚨 No valid data found in CSV.")
            except Exception as e:
                st.error(f"Error processing file: {e}")

    # ==========================================
    # PERSISTENT SIDEBAR CHATBOT
    # ==========================================
    st.sidebar.divider()
    st.sidebar.markdown("### 💬 MED-4 Assistant")
    
    chat_container = st.sidebar.container(height=350)
    for message in st.session_state.messages:
        chat_container.chat_message(message["role"]).markdown(message["content"])

    if prompt := st.sidebar.chat_input("Ask a question about formulas..."):
        st.session_state.messages.append({"role": "user", "content": prompt})
        chat_container.chat_message("user").markdown(prompt)

        p_lower = prompt.lower()
        if "password" in p_lower:
            response = "For security reasons, I cannot provide the Master Password. Please contact the plant administrator."
        elif "auto" in p_lower or "dose" in p_lower or "optimal" in p_lower or "calculate" in p_lower and "auto" in p_lower:
            response = "The **Auto-Calculate Optimal Dose** feature is currently in development! In a future update, it will use real-time feed water chemistry, Concentration Factors, and scaling indices to scientifically recommend the exact PPM needed."
        elif re.search(r'\bgor\b', p_lower) or "gain output ratio" in p_lower:
            response = "**GOR (Gain Output Ratio)** is calculated as:\n`Gross Production (m³/h) / LP Steam (TPH)`\n\nIt represents the 'fuel economy' of the plant—how many tons of water are produced per ton of steam."
        elif "recovery" in p_lower:
            response = "**System Recovery** is calculated as:\n`(Gross Production / Total SW Feed) * 100`\n\nThis shows the percentage of incoming seawater that is successfully converted into distillate."
        elif re.search(r'\blmtd\b', p_lower) or "log mean" in p_lower:
            response = "**LMTD (Log Mean Temperature Difference)** is currently calculated using a **Simple Delta T** because the Vapor Outlet sensor is unavailable.\n\n* Overall ΔT = `1st Effect Vapor - Final Brine Temp`."
        elif "1st effect htc" in p_lower:
            response = "**1st Effect HTC (U)** is calculated as:\n`(Q_1st / (Area_1st * ΔT_1st)) * 1000`\n\nWhere:\n* `Q_1st` = `SW Feed to 1st Effect * (1st Brine - Avg Brine 4 to 7) * 0.930`\n* `ΔT_1st` = `1st Vapor - 1st Brine`."
        elif "overall htc" in p_lower or "htc" in p_lower:
            response = "**Overall HTC (U)** is calculated as:\n`(Q_overall / (Area_overall * ΔT_overall)) * 1000`\n\nWhere:\n* `Q_overall` = `Total SW Feed * (Final Brine - SW Cond I/L Temp) * 0.930`\n* `ΔT_overall` = `1st Vapor - Final Brine`."
        elif "fouling factor" in p_lower:
            response = "**Fouling Factor** is calculated simply as:\n`1 / HTC`\n\nIt represents the thermal resistance added by scale buildup on the tubes."
        elif re.search(r'\bols\b', p_lower) or "linear regression" in p_lower:
            response = "**OLS (Ordinary Least Squares)** is the standard mathematical method used to draw a straight line of best fit through data points. It creates the 'Digital Twin' of the plant's clean physics."
        elif "xgboost" in p_lower or "random forest" in p_lower or "ai" in p_lower:
            response = "**Random Forest and XGBoost** are advanced AI models that use Decision Trees instead of linear math. They are highly accurate at tracking complex plant behavior, but they don't give you simple linear 'coefficients' like OLS does."
        elif "residual" in p_lower:
            response = "**Residual** is calculated as:\n`Actual Gross Production - Predicted Production`\n\nA negative residual means the plant is underperforming compared to its clean digital twin, indicating scale is blocking heat transfer."
        elif "fouling" in p_lower or "alert" in p_lower or "status" in p_lower:
            response = "The software calculates a **% Difference**:\n`(Residual / Predicted) * 100`\n\n* **Better than -4%:** CLEAN\n* **-4% to -5%:** WARNING (Increase antiscalant dosing)\n* **Worse than -5%:** FOULING (Please clean the machine)"
        elif "bulk" in p_lower or "upload" in p_lower:
            response = "In the **Bulk Uploads** tab, you can upload an entire month of logs. The software automatically calculates GOR, HTC, Predicted Production, and Residuals for every row, safely handling missing sensor data by borrowing from the 2014 baseline."
        elif "remarks" in p_lower or "observation" in p_lower:
            response = "You can add custom notes, TT errors, or shift observations in the **Remarks & Observations** box at the bottom of the Inputs tab. These automatically save to the database and print on the Daily Word Report!"
        else:
            response = "I am the Chembond Water Assistant. I can explain the new formulas for **1st Effect HTC, Overall HTC, Remarks, GOR, LMTD, Recovery, Residuals, Fouling alerts, OLS**, and **AI Models**. What would you like to know?"

        st.session_state.messages.append({"role": "assistant", "content": response})
        chat_container.chat_message("assistant").markdown(response)

if __name__ == "__main__":
    main()
