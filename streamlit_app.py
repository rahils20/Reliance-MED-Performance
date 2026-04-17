# requirements: pandas, numpy, python-docx, altair, gspread, oauth2client, scikit-learn
import streamlit as st
import pandas as pd
import numpy as np
import datetime
import io
import os
import json
import time
import altair as alt
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
    from sklearn.metrics import r2_score
    SKLEARN_INSTALLED = True
except ImportError:
    SKLEARN_INSTALLED = False

st.set_page_config(page_title="Chembond | MED-4 Management", layout="wide")

# ==========================================
# 1. CLOUD "GHOST SHEET" & CONFIG ENGINE
# ==========================================
GOOGLE_SHEET_NAME = "MED4_Cloud_Database"
LOCAL_DB_FILE = "MED4_Master_Database.csv"
LOCAL_CONFIG_FILE = "mra_config.json"

# EXCEL VERIFIED 2014-2015 BASELINE COEFFICIENTS
MRA_COEF_2014 = {
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

# EXCEL VERIFIED 2014-2015 BASELINE AVERAGES
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
    "Base Brine (°C)": np.round(np.linspace(66.3, 40.0, 11), 1)
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
            sheet = client.open(GOOGLE_SHEET_NAME).sheet1
            return {"type": "cloud", "client": sheet}
        except Exception as e:
            st.sidebar.error(f"Cloud Secret Failed: {e}")

    if os.path.exists('service_account.json'):
        try:
            scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
            creds = ServiceAccountCredentials.from_json_keyfile_name('service_account.json', scope)
            client = gspread.authorize(creds)
            sheet = client.open(GOOGLE_SHEET_NAME).sheet1
            return {"type": "cloud", "client": sheet}
        except Exception as e:
            st.sidebar.error(f"JSON File Failed: {e}")

    return {"type": "local", "client": None}

def load_database(db):
    if db["type"] == "cloud":
        try:
            records = db["client"].get_all_records()
            if records: return pd.DataFrame(records)
        except: pass
    if os.path.exists(LOCAL_DB_FILE):
        return pd.read_csv(LOCAL_DB_FILE)
    return pd.DataFrame(columns=["Date", "Gross Prod (m3/h)", "Desal (m3/h)", "Steam (TPH)", "SW Feed (m3/h)", "GOR", "Overall HTC", "Residual", "Antiscalant (kg)", "Antifoam (kg)", "Press_1st", "Temp_1st", "SW_Upper", "Brine_Temp_1st", "Brine_Flow", "Steam_Temp", "Anti_PPM", "SW_In_Temp", "Brine_Out_Temp", "Vap_Out_Temp"])

def save_database(db, df):
    df['Date'] = pd.to_datetime(df['Date']).dt.strftime('%Y-%m-%d')
    df = df.fillna(0)
    if db["type"] == "cloud":
        try:
            db["client"].clear()
            db["client"].update([df.columns.values.tolist()] + df.values.tolist())
            df.to_csv(LOCAL_DB_FILE, index=False)
            return True
        except Exception as e:
            st.error(f"Cloud Save Error: {e}")
    df.to_csv(LOCAL_DB_FILE, index=False)
    return True

def load_config(db):
    if db["type"] == "cloud":
        try:
            config_sheet = db["client"].spreadsheet.worksheet("Config")
            records = config_sheet.get_all_records()
            if records and "Anti_PPM" in records[0]:
                return {k: float(v) for k, v in records[0].items()}
        except Exception:
            pass 
    if os.path.exists(LOCAL_CONFIG_FILE):
        try:
            with open(LOCAL_CONFIG_FILE, "r") as f: 
                cfg = json.load(f)
                if "Anti_PPM" in cfg: return cfg
        except: pass
    return MRA_COEF_2014.copy()

def save_config(db, coef_dict):
    if db["type"] == "cloud":
        try:
            try:
                config_sheet = db["client"].spreadsheet.worksheet("Config")
            except:
                config_sheet = db["client"].spreadsheet.add_worksheet(title="Config", rows=2, cols=10)
            config_sheet.clear()
            config_sheet.update([list(coef_dict.keys()), list(coef_dict.values())])
        except Exception as e:
            st.error(f"Failed to sync config to cloud: {e}")
    with open(LOCAL_CONFIG_FILE, "w") as f:
        json.dump(coef_dict, f)

db_conn = init_db_connection()

# ==========================================
# 2. REPORT & CSV EXPORT GENERATORS
# ==========================================
def generate_daily_csv(date, ops, display_effect_df, w_data, chem_data, mra):
    data_dict = {
        "Date": date.strftime('%d/%m/%Y'),
        "Sea water Upper": ops['SW Upper'],
        "Sea water feed": ops['SW Total'],
        "Brine Water Return": ops['Brine Return'],
        "Desal Production": ops['Desal'],
        "LP Steam Consumption": ops['Steam'],
        "Gross Production": ops['Gross Prod'],
        "Recovery": round(ops['Recovery'], 2),
        "Gain Output Ratio": round(ops['GOR'], 2),
        "Steam Economy": round(ops['Economy'], 4),
        "Overall HTC": round(ops['HTC'], 2),
        "Fouling Factor": round(ops['Fouling'], 6),
        "MRA Residual": round(mra['Residual'], 2),
        "Antiscalant Dosing (PPM)": chem_data['anti_ppm'],
        "Antiscalant Consumption (kg/hr)": chem_data['anti_cons'],
        "Antifoam Dosing (PPM)": chem_data['foam_ppm'],
        "Antifoam Consumption (kg/hr)": chem_data['foam_cons']
    }
    for param, details in w_data['Feed'].items(): data_dict[f"Feed Water - {param}"] = details['val']
    for param, details in w_data['Product'].items(): data_dict[f"Desal Product - {param}"] = details['val']
    for idx, row in display_effect_df.iterrows():
        data_dict[f"{row['Effect ID']} Vapor Temp"] = row['Live Vapor (°C)']
        data_dict[f"{row['Effect ID']} Brine Temp"] = row['Live Brine (°C)']
    df = pd.DataFrame([data_dict])
    return df.to_csv(index=False).encode('utf-8')

def generate_comprehensive_report(date, ops, display_effect_df, w_data, chem_data, mra, skip_eff, skip_wq):
    doc = Document()
    doc.add_heading('MED-4 Daily Operational & Performance Report', 0).alignment = WD_ALIGN_PARAGRAPH.CENTER
    p = doc.add_paragraph()
    p.add_run('Prepared by: ').bold = True
    p.add_run('Chembond Chemicals Ltd.\n')
    p.add_run('Date: ').bold = True
    p.add_run(date.strftime('%d/%m/%Y'))
    p.alignment = WD_ALIGN_PARAGRAPH.RIGHT

    doc.add_heading('1. Executive Summary', level=1)
    doc.add_paragraph(f"On {date.strftime('%d/%m/%Y')}, the MED-4 unit achieved a Gross Production of {ops['Gross Prod']} m³/h and a Gain Output Ratio (GOR) of {ops['GOR']:.2f}:1. The Specific Thermal Energy Consumption (STEC) was {ops['STEC']:.2f} kWh/ton with a system recovery of {ops['Recovery']:.1f}%.")

    doc.add_heading('2. Operational Data Summary', level=1)
    t_ops = doc.add_table(rows=1, cols=4); t_ops.style = 'Table Grid'
    for i, h in enumerate(['Parameter', 'UOM', 'Design', 'Actual']): t_ops.rows[0].cells[i].text = h
    ops_rows = [['Total SW Feed', 'm³/h', '2400', str(ops['SW Total'])], ['SW Upper', 'm³/h', '580', str(ops['SW Upper'])], ['Brine Return', 'm³/h', '1400', str(ops['Brine Return'])], ['Desal', 'm³/h', '1000', str(ops['Desal'])], ['Gross Prod', 'm³/h', '-', str(ops['Gross Prod'])], ['LP Steam', 'TPH', '92-94.5', str(ops['Steam'])], ['Recovery', '%', '40.0', f"{ops['Recovery']:.2f}"], ['GOR', 'Ratio', '10.5 : 1', f"{ops['GOR']:.2f} : 1"], ['Steam Economy', 'Ratio', '-', f"{ops['Economy']:.4f}"]]
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

    doc.add_heading('4. Effect-wise Profile', level=1)
    doc.add_paragraph(f"Overall Plant LMTD: {ops['LMTD']:.2f} °C | Overall HTC (U): {ops['HTC']:.2f} W/m²K | Fouling Factor: {ops['Fouling']:.6f}")
    if skip_eff: 
        doc.add_paragraph("NOTE: The 11-Effect Temperature Cascade was not recorded for this operational day.", style='BodyText')
    else:
        t_eff = doc.add_table(rows=1, cols=5)
        t_eff.style = 'Table Grid'
        for i, h in enumerate(['Effect ID', 'Live Vapor (°C)', 'Vapor Dev.', 'Live Brine (°C)', 'Brine Dev.']): t_eff.rows[0].cells[i].text = h
        for idx, row in display_effect_df.iterrows():
            rc = t_eff.add_row().cells
            v_dev = abs(float(row['Live Vapor (°C)']) - float(row['Base Vapor (°C)']))
            b_dev = abs(float(row['Live Brine (°C)']) - float(row['Base Brine (°C)']))
            
            rc[0].text, rc[1].text, rc[2].text, rc[3].text, rc[4].text = str(row['Effect ID']), f"{row['Live Vapor (°C)']:.1f}", f"{v_dev:.1f}", f"{row['Live Brine (°C)']:.1f}", f"{b_dev:.1f}"
            if v_dev > 1.0: rc[2].paragraphs[0].runs[0].font.color.rgb = RGBColor(255, 0, 0)
            if b_dev > 1.0: rc[4].paragraphs[0].runs[0].font.color.rgb = RGBColor(255, 0, 0)

    doc.add_heading('5. Water Quality', level=1)
    if skip_wq: 
        doc.add_paragraph("NOTE: Laboratory water quality parameters were not recorded for this operational day.", style='BodyText')
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
    
    # PDF % Difference Logic added to Word Doc too
    diff_pct = (mra['Residual'] / mra['Predicted']) * 100 if mra['Predicted'] > 0 else 0
    doc.add_paragraph(f"Actual Gross: {mra['Actual']:.1f} m³/h | MRA Predicted: {mra['Predicted']:.1f} m³/h | Difference: {diff_pct:.1f}%")
    
    t_mra = doc.add_table(rows=1, cols=5); t_mra.style = 'Table Grid'
    for i, h in enumerate(['Parameter', 'Baseline', 'Live Input', 'Deviation', 'Impact']): t_mra.rows[0].cells[i].text = h
    for idx, row in mra['Variance_DF'].iterrows():
        rc = t_mra.add_row().cells
        rc[0].text, rc[1].text, rc[2].text, rc[3].text, rc[4].text = str(row['Parameter']), f"{row['Baseline']:.1f}", f"{row['Live Input']:.1f}", f"{row['Deviation']:+.1f}", f"{row['Impact (TPH)']:+.1f}"

    bio = io.BytesIO()
    doc.save(bio)
    return bio.getvalue()

def generate_monthly_report(df_month, month_str, year_str):
    doc = Document()
    doc.add_heading(f'MED-4 Monthly Performance Summary: {month_str} {year_str}', 0).alignment = WD_ALIGN_PARAGRAPH.CENTER
    doc.add_heading('1. Monthly Aggregation', level=1)
    doc.add_paragraph("The following metrics represent the arithmetic averages for the operational days recorded in this month.")
    
    t_agg = doc.add_table(rows=1, cols=4); t_agg.style = 'Table Grid'
    for i, h in enumerate(['Metric', 'Minimum', 'Maximum', 'Average']): t_agg.rows[0].cells[i].text = h
    
    metrics = [("Gross Production (m³/h)", df_month['Gross Prod (m3/h)']), ("Gain Output Ratio (GOR)", df_month['GOR']), ("Overall HTC (W/m²K)", df_month['Overall HTC']), ("MRA Residual (TPH)", df_month['Residual'])]
    for name, series in metrics:
        rc = t_agg.add_row().cells
        rc[0].text, rc[1].text, rc[2].text, rc[3].text = name, f"{series.min():.2f}", f"{series.max():.2f}", f"{series.mean():.2f}"
        
    doc.add_heading('2. Daily Operational Log', level=1)
    t_log = doc.add_table(rows=1, cols=5); t_log.style = 'Table Grid'
    for i, h in enumerate(['Date', 'Gross Prod', 'GOR', 'HTC', 'Residual']): t_log.rows[0].cells[i].text = h
    for _, row in df_month.iterrows():
        rc = t_log.add_row().cells
        try: date_str = pd.to_datetime(row['Date']).strftime('%d/%m/%Y')
        except: date_str = str(row['Date'])
        rc[0].text, rc[1].text, rc[2].text, rc[3].text, rc[4].text = date_str, f"{row['Gross Prod (m3/h)']:.1f}", f"{row['GOR']:.2f}", f"{row['Overall HTC']:.1f}", f"{row['Residual']:.1f}"

    bio = io.BytesIO()
    doc.save(bio)
    return bio.getvalue()

# ==========================================
# 3. BULLETPROOF SYNCHRONIZATION ENGINE
# ==========================================
DEFAULTS = {
    'steam': 71.75, 'desal': 800.0, 'gross': 801.4,
    'sw_upper': 553.63, 'sw_total': 2100.0, 'brine_ret': 1275.5,
    'sw_in_t': 30.0, 'brine_out_t': 41.0, 'stm_in_t': 165.54, 'vap_out_t': 70.0,
    'mra_press': 231.76, 'mra_t1': 68.47, 'mra_bt1': 65.46,
    'f_ph': 8.14, 'f_turb': 3.2, 'f_tss': 6.5, 'f_tds': 41000.0,
    'f_alk': 170.0, 'f_ca': 1040.0, 'f_cl': 21500.0, 'f_so4': 3150.0,
    'p_ph': 6.5, 'p_cond': 4.6, 'p_tds': 2.5, 'p_iron': 0.05,
    'p_cl': 0.0, 'p_so4': 0.0,
    'chem_anti_ppm': 4.82, 'chem_anti_cons': 13.5,
    'chem_foam_ppm': 0.0, 'chem_foam_cons': 0.0,
    'skip_eff': False, 'skip_wq': False
}

SYNC_MAP = {
    'steam': ['in_steam', 't1_steam', 't5_steam'], 'desal': ['in_desal', 't1_desal'], 'gross': ['in_gross', 't1_gross'],
    'sw_upper': ['in_sw_up', 't1_sw_up', 't5_sw_up'], 'sw_total': ['in_sw_tot', 't1_sw_tot', 't4_sw_tot'], 'brine_ret': ['in_brine', 't1_brine', 't5_bflow'],
    'sw_in_t': ['in_sw_in', 't2_sw_in'], 'brine_out_t': ['in_brine_out', 't2_brine_out'], 'stm_in_t': ['in_stm_in', 't2_stm_in', 't5_stm_t'], 'vap_out_t': ['in_vap_out', 't2_vap_out'],
    'mra_press': ['in_press', 't5_press'], 'mra_t1': ['in_t1', 't5_t1'], 'mra_bt1': ['in_bt1', 't5_bt1'],
    'f_ph': ['in_f_ph', 't3_f_ph'], 'f_turb': ['in_f_turb', 't3_f_turb'], 'f_tss': ['in_f_tss', 't3_f_tss'], 'f_tds': ['in_f_tds', 't3_f_tds'],
    'f_alk': ['in_f_alk', 't3_f_alk'], 'f_ca': ['in_f_ca', 't3_f_ca'], 'f_cl': ['in_f_cl', 't3_f_cl'], 'f_so4': ['in_f_so4', 't3_f_so4'],
    'p_ph': ['in_p_ph', 't3_p_ph'], 'p_cond': ['in_p_cond', 't3_p_cond'], 'p_tds': ['in_p_tds', 't3_p_tds'], 'p_iron': ['in_p_iron', 't3_p_iron'], 'p_cl': ['in_p_cl', 't3_p_cl'], 'p_so4': ['in_p_so4', 't3_p_so4'],
    'chem_anti_ppm': ['in_anti_ppm', 't4_anti_ppm', 't5_anti'], 'chem_anti_cons': ['in_anti_cons', 't4_anti_cons'],
    'chem_foam_ppm': ['in_foam_ppm', 't4_foam_ppm'], 'chem_foam_cons': ['in_foam_cons', 't4_foam_cons'],
    'skip_eff': ['in_skip_eff'], 'skip_wq': ['in_skip_wq']
}

if 'vars' not in st.session_state: st.session_state.vars = DEFAULTS.copy()
for k, v in DEFAULTS.items():
    if k not in st.session_state.vars: st.session_state.vars[k] = v

if 'sync_initialized' not in st.session_state:
    for var_name, keys in SYNC_MAP.items():
        for k in keys: 
            if k not in st.session_state: st.session_state[k] = st.session_state.vars[var_name]
    st.session_state.sync_initialized = True

if 'shared_effect_df' not in st.session_state or 'Vapor Temp (°C)' in st.session_state.shared_effect_df.columns:
    st.session_state.shared_effect_df = pd.DataFrame({
        "Effect ID": [f"Effect {i}" for i in range(1, 12)], 
        "Live Vapor (°C)": np.round(np.linspace(69.0, 42.0, 11), 1), 
        "Live Brine (°C)": np.round(np.linspace(66.3, 40.0, 11), 1)
    })

if 'daily_logs' not in st.session_state: st.session_state.daily_logs = load_database(db_conn)
if 'mra_coef' not in st.session_state: st.session_state.mra_coef = load_config(db_conn)

def sync_var(var_name, source_key):
    new_val = st.session_state[source_key]
    st.session_state.vars[var_name] = new_val
    for target_key in SYNC_MAP[var_name]:
        if target_key != source_key: st.session_state[target_key] = new_val

def get_v(var_name): return st.session_state.vars[var_name]

# ==========================================
# 4. CONSTANTS & BASELINES
# ==========================================
LATENT_HEAT_STEAM_KJ_KG = 2260.0 

WATER_SPECS = {
    "Feed": {"pH": {"lim": (7.5, 9.2), "var": "f_ph"}, "Turbidity (NTU)": {"lim": (0.0, 5.0), "var": "f_turb"}, "TSS (ppm)": {"lim": (0.0, 10.0), "var": "f_tss"}, "TDS (ppm)": {"lim": (0.0, 42000.0), "var": "f_tds"}, "Total Alkalinity": {"lim": (160.0, 190.0), "var": "f_alk"}, "Calcium Hardness": {"lim": (950.0, 1100.0), "var": "f_ca"}, "Chlorides": {"lim": (21000.0, 22000.0), "var": "f_cl"}, "Sulphate": {"lim": (3050.0, 3250.0), "var": "f_so4"}},
    "Product": {"pH": {"lim": (5.5, 7.0), "var": "p_ph"}, "Conductivity (μs/cm)": {"lim": (0.0, 15.0), "var": "p_cond"}, "TDS (ppm)": {"lim": (0.0, 10.0), "var": "p_tds"}, "Total Iron": {"lim": (0.0, 0.1), "var": "p_iron"}, "Chlorides": {"lim": (0.0, 5.0), "var": "p_cl"}, "Sulphate": {"lim": (0.0, 1.0), "var": "p_so4"}}
}


# ==========================================
# 5. MAIN UI APPLICATION
# ==========================================
def main():
    try: st.sidebar.image("chembond_logo.png", use_container_width=True)
    except: st.sidebar.markdown("### 🔹 CHEMBOND CHEMICALS LTD.") 
    st.sidebar.divider()
    
    log_date = st.sidebar.date_input("Date", datetime.date.today(), format="DD/MM/YYYY")
    log_date_str = log_date.strftime('%Y-%m-%d')
    
    if 'last_selected_date' not in st.session_state:
        st.session_state.last_selected_date = None

    if log_date_str != st.session_state.last_selected_date:
        st.session_state.last_selected_date = log_date_str
        
        if not st.session_state.daily_logs.empty and 'Date' in st.session_state.daily_logs.columns:
            dates_in_db = pd.to_datetime(st.session_state.daily_logs['Date'], errors='coerce').dt.strftime('%Y-%m-%d').values
            if log_date_str in dates_in_db:
                row_idx = np.where(dates_in_db == log_date_str)[0][0]
                row = st.session_state.daily_logs.iloc[row_idx]
                
                db_to_var_mapping = {
                    'gross': 'Gross Prod (m3/h)', 'desal': 'Desal (m3/h)', 'steam': 'Steam (TPH)',
                    'sw_total': 'SW Feed (m3/h)', 'chem_anti_cons': 'Antiscalant (kg)', 'chem_foam_cons': 'Antifoam (kg)',
                    'mra_press': 'Press_1st', 'mra_t1': 'Temp_1st', 'sw_upper': 'SW_Upper',
                    'mra_bt1': 'Brine_Temp_1st', 'brine_ret': 'Brine_Flow', 'stm_in_t': 'Steam_Temp',
                    'chem_anti_ppm': 'Anti_PPM', 'sw_in_t': 'SW_In_Temp', 'brine_out_t': 'Brine_Out_Temp',
                    'vap_out_t': 'Vap_Out_Temp'
                }
                
                loaded_vars = False
                for var_key, col_name in db_to_var_mapping.items():
                    if col_name in row.index and pd.notna(row[col_name]):
                        try:
                            val_str = str(row[col_name]).replace(',', '').strip()
                            if val_str and val_str.lower() not in ['nan', 'none', 'null', 'na']:
                                val = float(val_str)
                                st.session_state.vars[var_key] = val
                                for tk in SYNC_MAP[var_key]:
                                    st.session_state[tk] = val
                                loaded_vars = True
                        except Exception:
                            pass 
                
                if loaded_vars:
                    st.sidebar.success(f"📅 Auto-loaded historical data for {log_date.strftime('%d/%m/%Y')}")

    area_m2 = st.sidebar.number_input("Overall Surface Area (m²)", value=1757.49)
    
    if db_conn["type"] == "cloud": st.sidebar.success("☁️ Connected to Cloud Database")
    else: st.sidebar.warning("💾 Operating on Local Backup (CSV)")
    
    st.title("🏭 Reliance MED-4 Management Suite")
    tabs = st.tabs(["📥 0. Inputs", "🌊 1. KPIs", "🔥 2. HTC", "🧪 3. Quality", "🛢️ 4. Chemicals", "🧠 5. MRA", "📂 6. Reporting", "⚙️ 7. MRA Calibration Tool", "📤 8. Bulk Uploads"])

    # --- CALCULATE LIVE DATA ---
    ops_data = {'Steam': get_v('steam'), 'Desal': get_v('desal'), 'Gross Prod': get_v('gross'), 'SW Upper': get_v('sw_upper'), 'SW Total': get_v('sw_total'), 'Brine Return': get_v('brine_ret'), 'SW In': get_v('sw_in_t'), 'Brine Out': get_v('brine_out_t'), 'Stm In': get_v('stm_in_t'), 'Vap Out': get_v('vap_out_t')}
    ops_data['GOR'] = ops_data['Gross Prod'] / ops_data['Steam'] if ops_data['Steam'] > 0 else 0
    heat_load_kw = ((ops_data['Steam'] * 1000) / 3600) * LATENT_HEAT_STEAM_KJ_KG
    ops_data['STEC'] = heat_load_kw / ops_data['Desal'] if ops_data['Desal'] > 0 else 0
    ops_data['Recovery'] = (ops_data['Gross Prod'] / ops_data['SW Total']) * 100 if ops_data['SW Total'] > 0 else 0
    ops_data['Conversion'] = ops_data['Desal'] / ops_data['SW Total'] if ops_data['SW Total'] > 0 else 0
    ops_data['Economy'] = ops_data['Steam'] / ops_data['Desal'] if ops_data['Desal'] > 0 else 0

    dt1 = ops_data['Stm In'] - ops_data['Brine Out']
    dt2 = ops_data['Vap Out'] - ops_data['SW In']
    ops_data['LMTD'], ops_data['HTC'], ops_data['Fouling'], ops_data['Q_act'] = 0, 0, 0, 0
    if dt1 > 0 and dt2 > 0 and dt1 != dt2:
        ops_data['LMTD'] = (dt1 - dt2) / np.log(dt1 / dt2)
        ops_data['Q_act'] = ops_data['SW Total'] * (ops_data['Brine Out'] - ops_data['SW In']) * 0.930
        ops_data['HTC'] = (ops_data['Q_act'] / (area_m2 * ops_data['LMTD'])) * 1000 if ops_data['LMTD'] > 0 else 0
        ops_data['Fouling'] = 1 / ops_data['HTC'] if ops_data['HTC'] > 0 else 0

    mra_data = {}
    coefs = st.session_state.mra_coef 
    
    mra_data['Predicted'] = (
        coefs["Intercept"] + 
        (coefs["Press_1st"] * get_v('mra_press')) + 
        (coefs["Temp_1st"] * get_v('mra_t1')) + 
        (coefs["SW_Upper"] * get_v('sw_upper')) + 
        (coefs["Brine_Temp_1st"] * get_v('mra_bt1')) + 
        (coefs["Brine_Flow"] * get_v('brine_ret')) + 
        (coefs["LP_Steam"] * get_v('steam')) + 
        (coefs["Steam_Temp"] * get_v('stm_in_t')) +
        (coefs.get("Anti_PPM", MRA_COEF_2014["Anti_PPM"]) * get_v('chem_anti_ppm'))
    )
    
    mra_data['Actual'] = ops_data['Gross Prod']
    mra_data['Residual'] = mra_data['Actual'] - mra_data['Predicted']

    var_data = []
    for name, key, live_val in [("1st Effect Press", "Press_1st", get_v('mra_press')), ("1st Effect Temp", "Temp_1st", get_v('mra_t1')), ("Sea Water Upper", "SW_Upper", get_v('sw_upper')), ("1st Brine Temp", "Brine_Temp_1st", get_v('mra_bt1')), ("Brine Flow", "Brine_Flow", get_v('brine_ret')), ("LP Steam", "LP_Steam", get_v('steam')), ("Steam Temp", "Steam_Temp", get_v('stm_in_t')), ("Antiscalant PPM", "Anti_PPM", get_v('chem_anti_ppm'))]:
        dev = live_val - MRA_BASELINE[key]
        var_data.append([name, MRA_BASELINE[key], live_val, dev, coefs.get(key, MRA_COEF_2014[key]), dev * coefs.get(key, MRA_COEF_2014[key])])
    mra_data['Variance_DF'] = pd.DataFrame(var_data, columns=["Parameter", "Baseline", "Live Input", "Deviation", "Regression Weight", "Impact (TPH)"])

    water_data = {'Feed': {}, 'Product': {}}
    for cat in ['Feed', 'Product']:
        for param, details in WATER_SPECS[cat].items():
            val = get_v(details['var'])
            status = "✅ Pass" if details['lim'][0] <= val <= details['lim'][1] else "🚨 Fail"
            water_data[cat][param] = {'min': details['lim'][0], 'max': details['lim'][1], 'val': val, 'status': status}

    chem_data = {'anti_ppm': get_v('chem_anti_ppm'), 'anti_cons': get_v('chem_anti_cons'), 'foam_ppm': get_v('chem_foam_ppm'), 'foam_cons': get_v('chem_foam_cons')}
    
    display_effect_df = pd.merge(BASE_EFFECTS, st.session_state.shared_effect_df, on="Effect ID")
    display_effect_df = display_effect_df[["Effect ID", "Base Vapor (°C)", "Live Vapor (°C)", "Base Brine (°C)", "Live Brine (°C)"]]

    # --- TAB 0: INPUTS ---
    with tabs[0]:
        st.subheader("Central Data Entry Panel")
        with st.expander("1. Hydraulics & Mass Balance", expanded=True):
            c1, c2, c3 = st.columns(3)
            with c1:
                st.number_input("LP Steam (TPH)", key="in_steam", on_change=sync_var, args=('steam', 'in_steam'))
                st.number_input("Sea Water Upper (m³/h)", key="in_sw_up", on_change=sync_var, args=('sw_upper', 'in_sw_up'))
            with c2:
                st.number_input("Desal Production (m³/h)", key="in_desal", on_change=sync_var, args=('desal', 'in_desal'))
                st.number_input("Total SW Feed (m³/h)", key="in_sw_tot", on_change=sync_var, args=('sw_total', 'in_sw_tot'))
            with c3:
                st.number_input("Gross Production (m³/h)", key="in_gross", on_change=sync_var, args=('gross', 'in_gross'))
                st.number_input("Brine Water Return (m³/h)", key="in_brine", on_change=sync_var, args=('brine_ret', 'in_brine'))

        with st.expander("2. Plant Temperatures & MRA Variables", expanded=False):
            t1, t2, t3, t4 = st.columns(4)
            with t1: 
                st.number_input("SW Inlet Temp (°C)", key="in_sw_in", on_change=sync_var, args=('sw_in_t', 'in_sw_in'))
                st.number_input("1st Effect Press (mmHg)", key="in_press", on_change=sync_var, args=('mra_press', 'in_press'))
            with t2: 
                st.number_input("Brine Outlet Temp (°C)", key="in_brine_out", on_change=sync_var, args=('brine_out_t', 'in_brine_out'))
                st.number_input("1st Effect Temp (°C)", key="in_t1", on_change=sync_var, args=('mra_t1', 'in_t1'))
            with t3: 
                st.number_input("LP Steam Inlet Temp (°C)", key="in_stm_in", on_change=sync_var, args=('stm_in_t', 'in_stm_in'))
                st.number_input("1st Brine Temp (°C)", key="in_bt1", on_change=sync_var, args=('mra_bt1', 'in_bt1'))
            with t4: 
                st.number_input("Vapour Outlet Temp (°C)", key="in_vap_out", on_change=sync_var, args=('vap_out_t', 'in_vap_out'))

        with st.expander("3. Effect-wise Cascade (Temperatures)", expanded=False):
            st.checkbox("Skip Effect-wise Temperatures for today", key="in_skip_eff", on_change=sync_var, args=('skip_eff', 'in_skip_eff'))
            if not get_v('skip_eff'):
                e_df = st.data_editor(display_effect_df, key="in_effect_df", use_container_width=True, hide_index=True, disabled=["Effect ID", "Base Vapor (°C)", "Base Brine (°C)"])
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
                    for p, d in WATER_SPECS["Feed"].items(): st.number_input(f"{p}", key=f"in_{d['var']}", on_change=sync_var, args=(d['var'], f"in_{d['var']}"))
                with w_col2:
                    st.markdown("**Desal Product**")
                    for p, d in WATER_SPECS["Product"].items(): st.number_input(f"{p}", key=f"in_{d['var']}", on_change=sync_var, args=(d['var'], f"in_{d['var']}"))
                    
        with st.expander("5. Chemical Dosing", expanded=False):
            st.markdown("**Kem Watreat r 3687 (Antiscalant)**")
            ch1, ch2 = st.columns(2)
            with ch1: st.number_input("Dosing Level (PPM)", key="in_anti_ppm", on_change=sync_var, args=('chem_anti_ppm', 'in_anti_ppm'))
            with ch2: st.number_input("Actual Consumption (kg/hr)", key="in_anti_cons", on_change=sync_var, args=('chem_anti_cons', 'in_anti_cons'))
            
            st.markdown("**Kem Antifoam 1795**")
            ch3, ch4 = st.columns(2)
            with ch3: st.number_input("Dosing Level (PPM)", key="in_foam_ppm", on_change=sync_var, args=('chem_foam_ppm', 'in_foam_ppm'))
            with ch4: st.number_input("Actual Consumption (kg/hr)", key="in_foam_cons", on_change=sync_var, args=('chem_foam_cons', 'in_foam_cons'))

    # --- TAB 1: FLOW KPIs ---
    with tabs[1]:
        st.subheader("Mass Balance & KPI Dashboard")
        c1, c2, c3 = st.columns(3)
        with c1:
            st.number_input("LP Steam (TPH)", key="t1_steam", on_change=sync_var, args=('steam', 't1_steam'))
            st.number_input("Sea Water Upper (m³/h)", key="t1_sw_up", on_change=sync_var, args=('sw_upper', 't1_sw_up'))
        with c2:
            st.number_input("Desal Production (m³/h)", key="t1_desal", on_change=sync_var, args=('desal', 't1_desal'))
            st.number_input("Total SW Feed (m³/h)", key="t1_sw_tot", on_change=sync_var, args=('sw_total', 't1_sw_tot'))
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
        h1, h2, h3, h4 = st.columns(4)
        with h1: st.number_input("SW Inlet Temp (°C)", key="t2_sw_in", on_change=sync_var, args=('sw_in_t', 't2_sw_in'))
        with h2: st.number_input("Brine Outlet Temp (°C)", key="t2_brine_out", on_change=sync_var, args=('brine_out_t', 't2_brine_out'))
        with h3: st.number_input("Steam Inlet Temp (°C)", key="t2_stm_in", on_change=sync_var, args=('stm_in_t', 't2_stm_in'))
        with h4: st.number_input("Vapour Outlet Temp (°C)", key="t2_vap_out", on_change=sync_var, args=('vap_out_t', 't2_vap_out'))
            
        if ops_data['LMTD'] > 0:
            r1, r2, r3, r4 = st.columns(4)
            r1.metric("LMTD", f"{ops_data['LMTD']:.2f} °C")
            r2.metric("Plant Q (Actual)", f"{ops_data['Q_act']:,.0f} Kcal/hr°C")
            r3.metric("Overall HTC (U)", f"{ops_data['HTC']:.2f} W/m²K")
            r4.metric("Fouling Factor (1/U)", f"{ops_data['Fouling']:.6f}")
        
        if not get_v('skip_eff'):
            st.markdown("#### 11-Effect Temperature Deviation Profile")
            col_t, col_g = st.columns([5, 2])
            with col_t:
                e_df2 = st.data_editor(display_effect_df, key="t2_effect_df", use_container_width=True, hide_index=True, disabled=["Effect ID", "Base Vapor (°C)", "Base Brine (°C)"])
                if not e_df2[["Live Vapor (°C)", "Live Brine (°C)"]].equals(st.session_state.shared_effect_df[["Live Vapor (°C)", "Live Brine (°C)"]]):
                    st.session_state.shared_effect_df["Live Vapor (°C)"] = e_df2["Live Vapor (°C)"]
                    st.session_state.shared_effect_df["Live Brine (°C)"] = e_df2["Live Brine (°C)"]
                    st.rerun()
            
            with col_g:
                has_errors = False
                for _, row in e_df2.iterrows():
                    vap_diff = abs(row['Live Vapor (°C)'] - row['Base Vapor (°C)'])
                    bri_diff = abs(row['Live Brine (°C)'] - row['Base Brine (°C)'])
                    if vap_diff > 1.0:
                        st.error(f"🚨 **{row['Effect ID']} Vapor:** Dev by {vap_diff:.1f}°C")
                        has_errors = True
                    if bri_diff > 1.0:
                        st.error(f"🚨 **{row['Effect ID']} Brine:** Dev by {bri_diff:.1f}°C")
                        has_errors = True
                if not has_errors: st.success("✅ All temps within 1.0°C of Base.")

            st.divider()
            st.markdown("#### Effect-wise Heat Transfer Coefficients (HTC)")
            
            htc_data = []
            prev_vap = ops_data['Stm In']
            for idx, row in e_df2.iterrows():
                live_bri = row['Live Brine (°C)']
                dt_eff = prev_vap - live_bri 
                
                if dt_eff > 0 and ops_data['Q_act'] > 0:
                    eff_htc = (ops_data['Q_act'] / (area_m2 * dt_eff)) * 1000
                else:
                    eff_htc = 0
                    
                htc_data.append({"Effect ID": row['Effect ID'], "Driving Temp (°C)": round(prev_vap, 1), "Live Brine Temp (°C)": round(live_bri, 1), "Tube ΔT (°C)": round(dt_eff, 1), "Effect HTC (W/m²K)": round(eff_htc, 1)})
                prev_vap = row['Live Vapor (°C)'] 
                
            htc_df = pd.DataFrame(htc_data)
            
            htc_col1, htc_col2 = st.columns([1, 1])
            with htc_col1: st.dataframe(htc_df, use_container_width=True, hide_index=True)
            with htc_col2:
                htc_df['Effect ID'] = pd.Categorical(htc_df['Effect ID'], categories=[f"Effect {i}" for i in range(1, 12)], ordered=True)
                htc_chart = alt.Chart(htc_df).mark_bar(color='#ff7f0e', cornerRadiusTopLeft=3, cornerRadiusTopRight=3).encode(x=alt.X('Effect ID', title=None), y=alt.Y('Effect HTC (W/m²K)', title='HTC (W/m²K)'))
                st.altair_chart(htc_chart, use_container_width=True)

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
        st.number_input("Total SW Feed (m³/h)", key="t4_sw_tot", on_change=sync_var, args=('sw_total', 't4_sw_tot'))
        st.divider()
        cc1, cc2 = st.columns(2)
        with cc1:
            st.markdown("### 🧪 Kem Watreat r 3687 (Antiscalant)")
            st.number_input("Target Dosing Level (PPM)", key="t4_anti_ppm", on_change=sync_var, args=('chem_anti_ppm', 't4_anti_ppm'))
            theo_anti = (ops_data['SW Total'] * get_v('chem_anti_ppm')) / 1000
            st.info(f"**Theoretical Requirement:** {theo_anti:.2f} kg/hr")
            st.number_input("Actual Consumption (kg/hr)", key="t4_anti_cons", on_change=sync_var, args=('chem_anti_cons', 't4_anti_cons'))
        with cc2:
            st.markdown("### 🫧 Kem Antifoam 1795")
            st.number_input("Target Dosing Level (PPM)", key="t4_foam_ppm", on_change=sync_var, args=('chem_foam_ppm', 't4_foam_ppm'))
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
            st.slider("Sea Water Upper (m³/h)", key="t5_sw_up", min_value=300.0, max_value=1500.0, on_change=sync_var, args=('sw_upper', 't5_sw_up'))
            st.slider("1st Brine Temp (°C)", key="t5_bt1", min_value=40.0, max_value=80.0, on_change=sync_var, args=('mra_bt1', 't5_bt1'))
            st.slider("Brine Flow (m³/h)", key="t5_bflow", min_value=800.0, max_value=2000.0, on_change=sync_var, args=('brine_ret', 't5_bflow'))
            st.slider("LP Steam (TPH)", key="t5_steam", min_value=40.0, max_value=150.0, on_change=sync_var, args=('steam', 't5_steam'))
            st.slider("Steam Temp (°C)", key="t5_stm_t", min_value=140.0, max_value=220.0, on_change=sync_var, args=('stm_in_t', 't5_stm_t'))
            st.slider("Antiscalant PPM", key="t5_anti", min_value=0.0, max_value=10.0, step=0.1, on_change=sync_var, args=('chem_anti_ppm', 't5_anti'))

        with calc_col:
            k1, k2, k3 = st.columns(3)
            k1.metric("Actual Gross SCADA", f"{mra_data['Actual']:.1f} m³/h")
            k2.metric("MRA Predicted", f"{mra_data['Predicted']:.1f} m³/h")
            
            # THE FIX: Percentage Based Alert Logic
            diff_pct = (mra_data['Residual'] / mra_data['Predicted']) * 100 if mra_data['Predicted'] > 0 else 0
            
            if diff_pct <= -5.0:
                k3.error(f"Residual: {mra_data['Residual']:.1f} TPH ({diff_pct:.1f}%) - Please clean the machine")
            elif diff_pct <= -4.0:
                k3.warning(f"Residual: {mra_data['Residual']:.1f} TPH ({diff_pct:.1f}%) - Increase antiscalant dosing")
            else:
                k3.success(f"Residual: {mra_data['Residual']:.1f} TPH ({diff_pct:.1f}%) - CLEAN")
                
            st.dataframe(mra_data['Variance_DF'].style.format({"Baseline": "{:.1f}", "Live Input": "{:.1f}", "Deviation": "{:+.1f}", "Regression Weight": "{:.3f}", "Impact (TPH)": "{:+.1f}"}), use_container_width=True, hide_index=True)

    # --- TAB 6: ENTERPRISE REPORTING SUITE ---
    with tabs[6]:
        st.subheader("Intelligence & Reporting Center")
        rep_tabs = st.tabs(["📅 Today's Dashboard", "📆 Master Database", "📊 Long-Term Health"])
        
        with rep_tabs[0]:
            m_col1, m_col2, m_col3, m_col4 = st.columns(4)
            m_col1.metric("Date", log_date.strftime('%d/%m/%Y')) 
            m_col2.metric("Gross Production", f"{ops_data['Gross Prod']} m³/h", delta=f"{ops_data['Gross Prod'] - 1000:.0f} from Design" if ops_data['Gross Prod'] < 1000 else None)
            m_col3.metric("System GOR", f"{ops_data['GOR']:.2f}", delta=f"{ops_data['GOR'] - 10.5:.2f} from Target" if ops_data['GOR'] < 10.5 else None)
            
            # THE FIX: Percentage Based Reporting Dashboard
            diff_pct = (mra_data['Residual'] / mra_data['Predicted']) * 100 if mra_data['Predicted'] > 0 else 0
            if diff_pct <= -5.0:
                delta_text = f"{diff_pct:.1f}% (Please clean machine)"
                d_color = "inverse"
            elif diff_pct <= -4.0:
                delta_text = f"{diff_pct:.1f}% (Increase antiscalant)"
                d_color = "inverse"
            else:
                delta_text = f"{diff_pct:.1f}% (Clean)"
                d_color = "normal"
                
            m_col4.metric("Fouling Residual", f"{mra_data['Residual']:.1f} TPH", delta=delta_text, delta_color=d_color)
            
            st.divider()
            graph_col1, graph_col2 = st.columns(2)
            with graph_col1:
                st.markdown("#### ⚖️ Variance Impact (TPH)")
                impact_chart = alt.Chart(mra_data['Variance_DF']).mark_bar().encode(x=alt.X('Impact (TPH):Q'), y=alt.Y('Parameter:N', sort='-x', title=''), color=alt.condition(alt.datum['Impact (TPH)'] > 0, alt.value('#2ca02c'), alt.value('#d62728')), tooltip=['Parameter', 'Impact (TPH)']).properties(height=300)
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
                            "Gross Prod (m3/h)": [ops_data['Gross Prod']], "Desal (m3/h)": [ops_data['Desal']], 
                            "Steam (TPH)": [ops_data['Steam']], "SW Feed (m3/h)": [ops_data['SW Total']], 
                            "GOR": [round(ops_data['GOR'], 2)], "Overall HTC": [round(ops_data['HTC'], 2)], 
                            "Residual": [round(mra_data['Residual'], 1)],
                            "Antiscalant (kg)": [chem_data['anti_cons']], "Antifoam (kg)": [chem_data['foam_cons']],
                            "Press_1st": [get_v('mra_press')], "Temp_1st": [get_v('mra_t1')], 
                            "SW_Upper": [get_v('sw_upper')], "Brine_Temp_1st": [get_v('mra_bt1')], 
                            "Brine_Flow": [get_v('brine_ret')], "Steam_Temp": [get_v('stm_in_t')], 
                            "Anti_PPM": [get_v('chem_anti_ppm')],
                            "SW_In_Temp": [get_v('sw_in_t')], "Brine_Out_Temp": [get_v('brine_out_t')],
                            "Vap_Out_Temp": [get_v('vap_out_t')]
                        })
                        st.session_state.daily_logs = pd.concat([st.session_state.daily_logs, new_log], ignore_index=True)
                        save_database(db_conn, st.session_state.daily_logs)
                        st.success("✅ Master Database Updated!")
                    elif pwd_append != "": st.error("❌ Incorrect Password.")
            with c_export:
                word_file = generate_comprehensive_report(log_date, ops_data, display_effect_df, water_data, chem_data, mra_data, get_v('skip_eff'), get_v('skip_wq'))
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
                    else: st.error("❌ Incorrect Password.")
            with c_dl:
                st.download_button("📥 Download CSV Backup", data=st.session_state.daily_logs.to_csv(index=False).encode('utf-8'), file_name=f"MED4_Master.csv", mime='text/csv', use_container_width=True)

            st.divider()
            st.markdown("#### 📊 Monthly Report Generator")
            if not st.session_state.daily_logs.empty:
                df_logs = st.session_state.daily_logs.copy()
                df_logs['Date'] = pd.to_datetime(df_logs['Date'])
                month_data = df_logs[(df_logs['Date'].dt.month == log_date.month) & (df_logs['Date'].dt.year == log_date.year)].copy()
                if not month_data.empty:
                    if st.button("📄 Generate Monthly Report (.docx)", use_container_width=True):
                        monthly_doc = generate_monthly_report(month_data, log_date.strftime('%B'), str(log_date.year))
                        st.download_button("📥 Download Monthly Report", data=monthly_doc, file_name=f"MED4_Monthly_{log_date.strftime('%b_%Y')}.docx", mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document")

        with rep_tabs[2]:
            if not st.session_state.daily_logs.empty:
                df_logs = st.session_state.daily_logs.copy()
                df_logs['Date'] = pd.to_datetime(df_logs['Date'], errors='coerce')
                df_logs['Recovery (%)'] = np.where(pd.to_numeric(df_logs['SW Feed (m3/h)'], errors='coerce') > 0, (pd.to_numeric(df_logs['Gross Prod (m3/h)'], errors='coerce') / pd.to_numeric(df_logs['SW Feed (m3/h)'], errors='coerce')) * 100, 0)
                
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

    # --- TAB 7: TEMPLATE UPLOAD MODEL TRAINER ---
    with tabs[7]:
        st.subheader("🤖 Pure OLS Model Calibration (Template Upload)")
        if not SKLEARN_INSTALLED:
            st.error("🚨 'scikit-learn' is not installed. Please add it to your requirements.txt.")
        else:
            st.markdown("### 💾 Manage Baseline Model")
            st.markdown("If you wish to revert to the mathematically verified 2014-15 baseline, you can reset the system at any time.")
            c_reset, _ = st.columns([1, 1])
            with c_reset:
                if st.button("🔄 Factory Reset to 2014 Defaults", use_container_width=True):
                    st.session_state.mra_coef = MRA_COEF_2014.copy()
                    save_config(db_conn, st.session_state.mra_coef)
                    st.success("✅ Restored verified 2014 baseline model. Reloading...")
                    time.sleep(1.5)
                    st.rerun()

            st.divider()

            st.markdown("### 📊 Upload New Baseline (Step-Test Data)")
            st.markdown("Download the 10-column template, paste your historical data, and upload it here. The system uses pure Ordinary Least Squares (OLS) regression to calculate the coefficients, identical to Excel's LINEST function.")
            
            req_cols = ["Date", "Gross Prod", "Press_1st", "Temp_1st", "SW_Upper", "Brine_Temp_1st", "Brine_Flow", "LP_Steam", "Steam_Temp", "Anti_PPM"]
            template_df = pd.DataFrame(columns=req_cols)
            st.download_button(label="1️⃣ Download Blank Training CSV Template", data=template_df.to_csv(index=False).encode('utf-8'), file_name='MED4_ML_Template.csv', mime='text/csv')
            
            st.divider()
            
            uploaded_file = st.file_uploader("2️⃣ Upload Populated Training Data", type=["csv"], key="mra_trainer")
            
            if uploaded_file is not None:
                try:
                    df_train = pd.read_csv(uploaded_file)
                    
                    if not all(col in df_train.columns for col in req_cols):
                        st.error(f"❌ Uploaded CSV is missing required columns. Please use the exact 10-column Template format.")
                    else:
                        for col in req_cols:
                            if col != "Date":
                                if df_train[col].dtype == object:
                                    df_train[col] = pd.to_numeric(df_train[col].astype(str).str.replace(',', '', regex=False), errors='coerce')
                        
                        df_train = df_train.dropna(subset=[c for c in req_cols if c != "Date"])
                        
                        st.success(f"✅ Data Accepted: Evaluated {len(df_train)} clean operational records.")
                        
                        if len(df_train) > 0:
                            X = df_train[["Press_1st", "Temp_1st", "SW_Upper", "Brine_Temp_1st", "Brine_Flow", "LP_Steam", "Steam_Temp", "Anti_PPM"]]
                            Y = df_train["Gross Prod"]
                            
                            model = LinearRegression(fit_intercept=True)
                            model.fit(X, Y)
                            
                            predictions = model.predict(X)
                            r2 = r2_score(Y, predictions)
                            
                            st.markdown("### ⚙️ Regression Results")
                            m1, m2 = st.columns(2)
                            m1.metric("Mathematical Accuracy (R² Score)", f"{r2 * 100:.2f}%")
                            m2.metric("Calculated Intercept", f"{model.intercept_:.4f}")
                            
                            new_coefs = {
                                "Intercept": float(model.intercept_),
                                "Press_1st": float(model.coef_[0]), "Temp_1st": float(model.coef_[1]), 
                                "SW_Upper": float(model.coef_[2]), "Brine_Temp_1st": float(model.coef_[3]), 
                                "Brine_Flow": float(model.coef_[4]), "LP_Steam": float(model.coef_[5]), 
                                "Steam_Temp": float(model.coef_[6]), "Anti_PPM": float(model.coef_[7])
                            }
                            
                            comp_df = pd.DataFrame({
                                "Parameter": list(new_coefs.keys()),
                                "Current Coefficient": [st.session_state.mra_coef.get(k, MRA_COEF_2014.get(k, 0)) for k in new_coefs.keys()],
                                "Calculated OLS Coefficient": [new_coefs[k] for k in new_coefs.keys()]
                            })
                            st.dataframe(comp_df.style.format({"Current Coefficient": "{:.4f}", "Calculated OLS Coefficient": "{:.4f}"}), use_container_width=True, hide_index=True)
                            
                            st.markdown("### 💾 Commit New Model")
                            c_apply, _ = st.columns(2)
                            with c_apply:
                                if st.button("🔥 Save New Coefficients Permanently", type="primary", use_container_width=True):
                                    st.session_state.mra_coef = new_coefs
                                    save_config(db_conn, new_coefs)
                                    st.success("✅ Coefficients updated! Reloading application...")
                                    time.sleep(1.5)
                                    st.rerun()
                        else:
                            st.error("🚨 No valid numeric data found. Please ensure the CSV is properly populated.")
                                
                except Exception as e:
                    st.error(f"Error processing file: {e}")

    # --- TAB 8: BULK UPLOAD ---
    with tabs[8]:
        st.subheader("📤 Bulk Data Upload & Sync")
        st.markdown("Download the Bulk Upload Template, fill in your daily historical data, and upload it here. The system will automatically calculate all KPIs, MRA Residuals, and HTC for every row, allowing you to quickly backfill your Master Database.")
        
        bulk_cols = [
            "Date (DD/MM/YYYY)", "Gross Prod (m3/h)", "Desal (m3/h)", "Steam (TPH)", "SW Feed (m3/h)", 
            "SW_Upper", "Brine_Flow", "Press_1st", "Temp_1st", "Brine_Temp_1st", "Steam_Temp", 
            "SW_In_Temp", "Brine_Out_Temp", "Vap_Out_Temp", "Anti_PPM", "Antiscalant (kg)", "Antifoam (kg)"
        ]
        
        bulk_template = pd.DataFrame(columns=bulk_cols)
        st.download_button(label="1️⃣ Download Bulk Upload Template", data=bulk_template.to_csv(index=False).encode('utf-8'), file_name='MED4_Bulk_Template.csv', mime='text/csv')
        
        st.divider()
        
        bulk_file = st.file_uploader("2️⃣ Upload Populated Bulk Data", type=["csv"], key="bulk_uploader")
        
        if bulk_file is not None:
            try:
                df_bulk = pd.read_csv(bulk_file)
                missing = [c for c in bulk_cols if c not in df_bulk.columns]
                
                if missing:
                    st.error(f"❌ Uploaded CSV is missing required columns: {', '.join(missing)}")
                else:
                    num_cols = [c for c in bulk_cols if c != "Date (DD/MM/YYYY)"]
                    for col in num_cols:
                        if df_bulk[col].dtype == object:
                            df_bulk[col] = pd.to_numeric(df_bulk[col].astype(str).str.replace(',', '', regex=False), errors='coerce')
                    
                    df_bulk = df_bulk.dropna(subset=["Date (DD/MM/YYYY)", "Gross Prod (m3/h)"])
                    
                    if len(df_bulk) > 0:
                        
                        for col_name, baseline_val in zip(
                            ['Press_1st', 'Temp_1st', 'SW_Upper', 'Brine_Temp_1st', 'Brine_Flow', 'Steam (TPH)', 'Steam_Temp', 'Anti_PPM'],
                            [231.76, 68.47, 553.63, 65.46, 1275.50, 71.75, 165.54, 4.82]
                        ):
                            df_bulk[col_name] = df_bulk[col_name].fillna(baseline_val)
                            
                        df_bulk['Date_Clean'] = pd.to_datetime(df_bulk['Date (DD/MM/YYYY)'], dayfirst=True, errors='coerce').dt.strftime('%Y-%m-%d')
                        
                        df_bulk['GOR'] = np.where(df_bulk['Steam (TPH)'] > 0, df_bulk['Gross Prod (m3/h)'] / df_bulk['Steam (TPH)'], 0)
                        
                        df_bulk['Predicted'] = (
                            coefs["Intercept"] + 
                            (coefs["Press_1st"] * df_bulk['Press_1st']) + 
                            (coefs["Temp_1st"] * df_bulk['Temp_1st']) + 
                            (coefs["SW_Upper"] * df_bulk['SW_Upper']) + 
                            (coefs["Brine_Temp_1st"] * df_bulk['Brine_Temp_1st']) + 
                            (coefs["Brine_Flow"] * df_bulk['Brine_Flow']) + 
                            (coefs["LP_Steam"] * df_bulk['Steam (TPH)']) + 
                            (coefs["Steam_Temp"] * df_bulk['Steam_Temp']) +
                            (coefs.get("Anti_PPM", MRA_COEF_2014["Anti_PPM"]) * df_bulk['Anti_PPM'])
                        )
                        df_bulk['Residual'] = df_bulk['Gross Prod (m3/h)'] - df_bulk['Predicted']
                        
                        df_bulk['SW_In_Temp'] = df_bulk['SW_In_Temp'].fillna(30.0)
                        df_bulk['Brine_Out_Temp'] = df_bulk['Brine_Out_Temp'].fillna(41.0)
                        df_bulk['Vap_Out_Temp'] = df_bulk['Vap_Out_Temp'].fillna(70.0)
                        df_bulk['SW Feed (m3/h)'] = df_bulk['SW Feed (m3/h)'].fillna(2100.0)
                        
                        dt1 = df_bulk['Steam_Temp'] - df_bulk['Brine_Out_Temp']
                        dt2 = df_bulk['Vap_Out_Temp'] - df_bulk['SW_In_Temp']
                        
                        valid_dt = (dt1 > 0) & (dt2 > 0) & (dt1 != dt2)
                        
                        lmtd = np.where(valid_dt, (dt1 - dt2) / np.log(dt1 / dt2), 0)
                        q_act = df_bulk['SW Feed (m3/h)'] * (df_bulk['Brine_Out_Temp'] - df_bulk['SW_In_Temp']) * 0.930
                        
                        df_bulk['Overall HTC'] = np.where(lmtd > 0, (q_act / (area_m2 * lmtd)) * 1000, 0)
                        
                        db_ready_df = pd.DataFrame({
                            "Date": df_bulk['Date_Clean'],
                            "Gross Prod (m3/h)": df_bulk['Gross Prod (m3/h)'],
                            "Desal (m3/h)": df_bulk['Desal (m3/h)'].fillna(0),
                            "Steam (TPH)": df_bulk['Steam (TPH)'],
                            "SW Feed (m3/h)": df_bulk['SW Feed (m3/h)'],
                            "GOR": df_bulk['GOR'].round(2),
                            "Overall HTC": df_bulk['Overall HTC'].round(2),
                            "Residual": df_bulk['Residual'].round(1),
                            "Antiscalant (kg)": df_bulk['Antiscalant (kg)'].fillna(0),
                            "Antifoam (kg)": df_bulk['Antifoam (kg)'].fillna(0),
                            "Press_1st": df_bulk['Press_1st'],
                            "Temp_1st": df_bulk['Temp_1st'],
                            "SW_Upper": df_bulk['SW_Upper'],
                            "Brine_Temp_1st": df_bulk['Brine_Temp_1st'],
                            "Brine_Flow": df_bulk['Brine_Flow'],
                            "Steam_Temp": df_bulk['Steam_Temp'],
                            "Anti_PPM": df_bulk['Anti_PPM'],
                            "SW_In_Temp": df_bulk['SW_In_Temp'],
                            "Brine_Out_Temp": df_bulk['Brine_Out_Temp'],
                            "Vap_Out_Temp": df_bulk['Vap_Out_Temp']
                        })
                        
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

if __name__ == "__main__":
    main()
