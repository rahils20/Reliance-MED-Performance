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
    "Date", "Sea Water Upper", "Sea Water Lower", "Sea Water Feed", "Sea Water Pressure",
    "Brine Water Return", "Desal production", "LP Steam consumption", "LP Steam Pressure",
    "Condensate Return", "condensate temp", "Condensate Conductivity",
    "1st Effect Vapour Temp", "1st effect brine temp", "11th Effect Brine Temp", "Feed Temp to Cold Group",
    "Delta T", "1st effect vapour pressure", "Brine Discharge Temp", "Brine Discharge Pressure",
    "Sea Water cond I/L temp", "Sea Water Condenser O/L Temp", 
    "CW supply", "CW Return", "CW Flow", "Gross production", "GOR", "STEC", "Overall HTC", "1st Effect HTC", 
    "Residual", "Antiscalant (kg)", "Antifoam (kg)", "Anti_PPM", "Area_1st", "Area_Overall", "Remarks"
]
for cat in ['Feed', 'Product']:
    for param, details in WATER_SPECS[cat].items(): 
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
    'Antiscalant residual (Brine)', 'Remarks'
]

DEFAULTS = {
    'steam': 71.75, 'stm_press': 4.3, 'desal': 800.0, 'gross': 801.4, 'sw_upper': 553.63, 'sw_total': 2100.0, 'sw_press': 1.7, 
    'brine_ret': 1275.5, 'brine_press': 1.3,
    'sw_in_t': 30.0, 'brine_out_t': 41.0, 'vap_out_t': 70.0, 'mra_press': 231.76, 'mra_t1': 68.47, 'mra_bt1': 65.46,
    'brine_11': 43.0, 'feed_cold': 37.0,
    'f_ph': 8.14, 'f_turb': 3.2, 'f_tss': 6.5, 'f_tds': 41000.0, 'f_alk': 170.0, 'f_ca': 1040.0, 'f_cl': 21500.0, 'f_so4': 3150.0,
    'p_ph': 6.5, 'p_cond': 4.6, 'p_tds': 2.5, 'p_iron': 0.05, 'p_cl': 0.0, 'p_so4': 0.0,
    'chem_anti_ppm': 4.82, 'chem_anti_cons': 13.5, 'chem_foam_ppm': 0.0, 'chem_foam_cons': 0.0,
    'skip_eff': False, 'skip_wq': False, 'remarks': "", 'area_1st': 1757.49, 'area_overall': 19332.0,
    'sw_lower': 0.0, 'cond_flow': 0.0, 'cond_temp': 0.0, 'cond_cond': 3.0, 'sw_out_t': 0.0, 'cw_supply': 0.0, 'cw_return': 0.0, 'cw_flow': 2726.0
}

SYNC_MAP = {
    'steam': ['in_steam', 't5_steam'], 'stm_press': ['in_stm_press'], 'desal': ['in_desal'], 'gross': ['in_gross'],
    'sw_upper': ['in_sw_up', 't5_sw_up', 't2_sw_up'], 'sw_total': ['in_sw_tot', 't4_sw_tot', 't2_sw_tot'], 'sw_press': ['in_sw_press'],
    'brine_ret': ['in_brine', 't5_bflow'], 'brine_press': ['in_brine_press'], 
    'sw_in_t': ['in_sw_in', 't2_sw_in'], 'brine_out_t': ['in_brine_out', 't2_brine_out'], 
    'vap_out_t': ['in_vap_out', 't2_vap_out'], 'mra_press': ['in_press', 't5_press'], 
    'mra_t1': ['in_t1', 't5_t1', 't2_t1'], 'mra_bt1': ['in_bt1', 't5_bt1', 't2_bt1'], 
    'brine_11': ['in_brine_11'], 'feed_cold': ['in_feed_cold'],
    'f_ph': ['in_f_ph', 't3_f_ph'], 
    'f_turb': ['in_f_turb', 't3_f_turb'], 'f_tss': ['in_f_tss', 't3_f_tss'], 'f_tds': ['in_f_tds', 't3_f_tds'],
    'f_alk': ['in_f_alk', 't3_f_alk'], 'f_ca': ['in_f_ca', 't3_f_ca'], 'f_cl': ['in_f_cl', 't3_f_cl'], 'f_so4': ['in_f_so4', 't3_f_so4'],
    'p_ph': ['in_p_ph', 't3_p_ph'], 'p_cond': ['in_p_cond', 't3_p_cond'], 'p_tds': ['in_p_tds', 't3_p_tds'], 
    'p_iron': ['in_p_iron', 't3_p_iron'], 'p_cl': ['in_p_cl', 't3_p_cl'], 'p_so4': ['in_p_so4', 't3_p_so4'],
    'chem_anti_ppm': ['in_anti_ppm', 't4_anti_ppm', 't5_anti'], 'chem_anti_cons': ['in_anti_cons', 't4_anti_cons'],
    'chem_foam_ppm': ['in_foam_ppm', 't4_foam_ppm'], 'chem_foam_cons': ['in_foam_cons', 't4_foam_cons'],
    'remarks': ['in_remarks'], 'area_1st': ['t2_area_1st'], 'area_overall': ['t2_area_overall'],
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
    log_date = st.sidebar.date_input("Date", datetime.date.today(), format="DD/MM/YYYY")
    log_date_str = log_date.strftime('%Y-%m-%d')
    
    if 'last_selected_date' not in st.session_state: 
        st.session_state.last_selected_date = None

    if log_date_str != st.session_state.last_selected_date:
        st.session_state.last_selected_date = log_date_str
        if not st.session_state.daily_logs.empty and 'Date' in st.session_state.daily_logs.columns:
            # CORE FIX: Standardize all registry dates right now, extract as safe strings
            db_dates_parsed = standardize_dates(st.session_state.daily_logs['Date'])
            db_dates = db_dates_parsed.dt.strftime('%Y-%m-%d').values
            
            if log_date_str in db_dates:
                row_idx = np.where(db_dates == log_date_str)[0][-1]
                row = st.session_state.daily_logs.iloc[row_idx]
                
                db_to_var_mapping = {
                    'gross': ['Gross production'], 
                    'desal': ['Desal production'], 
                    'steam': ['LP Steam consumption'], 'stm_press': ['LP Steam Pressure'],
                    'sw_total': ['Sea Water Feed'], 'sw_press': ['Sea Water Pressure'],
                    'sw_upper': ['Sea Water Upper'], 'sw_lower': ['Sea Water Lower'],
                    'cond_flow': ['Condensate Return'], 'cond_temp': ['condensate temp'], 'cond_cond': ['Condensate Conductivity'],
                    'sw_out_t': ['Sea Water Condenser O/L Temp'], 
                    'cw_supply': ['CW supply'], 'cw_return': ['CW Return'], 'cw_flow': ['CW Flow'],
                    'chem_anti_cons': ['Antiscalant (kg)'], 'chem_foam_cons': ['Antifoam (kg)'], 
                    'mra_press': ['1st effect vapour pressure'], 
                    'mra_t1': ['1st Effect Vapour Temp'], 
                    'mra_bt1': ['1st effect brine temp'], 
                    'brine_11': ['11th Effect Brine Temp'], 'feed_cold': ['Feed Temp to Cold Group'],
                    'brine_ret': ['Brine Water Return'], 'brine_press': ['Brine Discharge Pressure'],
                    'chem_anti_ppm': ['Anti_PPM'], 
                    'sw_in_t': ['Sea Water cond I/L temp'], 
                    'brine_out_t': ['Brine Discharge Temp'], 
                    'vap_out_t': ['Vap_Out_Temp'], 
                    'remarks': ['Remarks'], 'area_1st': ['Area_1st'], 'area_overall': ['Area_Overall']
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
                    st.sidebar.success(f"Auto-loaded historical data for {log_date.strftime('%d-%m-%Y')}")
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

    ops_data['q_1st'] = (ops_data['Steam'] * LATENT_HEAT_STEAM_KJ_KG * 1000) / 3600
    ops_data['q_overall'] = ops_data['q_1st'] 

    dt_1st_hot = ops_data['Stm In_1st'] - ops_data['Brine_1st']
    try: 
        dt_1st_cold = get_v('cond_temp') - st.session_state.shared_effect_df.loc[1, 'Live Brine (°C)'] 
        if pd.isna(dt_1st_cold) or dt_1st_cold <= 0: dt_1st_cold = dt_1st_hot * 0.8
    except:
        dt_1st_cold = dt_1st_hot * 0.8

    if dt_1st_hot > 0 and dt_1st_cold > 0 and dt_1st_hot != dt_1st_cold:
        lmtd_1st = (dt_1st_hot - dt_1st_cold) / math.log(dt_1st_hot / dt_1st_cold)
    else:
        lmtd_1st = dt_1st_hot

    ops_data['dt_1st'] = dt_1st_hot
    ops_data['htc_1st'] = ops_data['q_1st'] / (get_v('area_1st') * lmtd_1st) if lmtd_1st > 0 and get_v('area_1st') > 0 else 0
    ops_data['fouling_1st'] = 1 / ops_data['htc_1st'] if ops_data['htc_1st'] > 0 else 0

    ops_data['dt_overall_simple'] = ops_data['Brine_1st'] - get_v('brine_11')
    dt_ov_hot = ops_data['Stm In_1st'] - ops_data['Brine Out_overall']
    dt_ov_cold = get_v('cond_temp') - ops_data['SW In_overall']
    
    if dt_ov_hot > 0 and dt_ov_cold > 0 and dt_ov_hot != dt_ov_cold:
        lmtd_ov = (dt_ov_hot - dt_ov_cold) / math.log(dt_ov_hot / dt_ov_cold)
    else:
        lmtd_ov = dt_ov_hot
        
    ops_data['htc_overall'] = ops_data['q_overall'] / (get_v('area_overall') * lmtd_ov) if lmtd_ov > 0 and get_v('area_overall') > 0 else 0
    ops_data['fouling_overall'] = 1 / ops_data['htc_overall'] if ops_data['htc_overall'] > 0 else 0

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
            st.subheader("Central Data Entry Panel")
            if mra_data['Predicted'] > 950: 
                st.warning("MRA Prediction is unusually high (>950 m³/h). Please verify you did not accidentally enter the 'Sea Water Feed' (~2100) into the 'Sea Water Upper' (~550) input.")
                
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
                    st.number_input("Sea Water Condenser O/L Temp (°C)", key="in_sw_out", on_change=sync_var, args=('sw_out_t', 'in_sw_out'))
                    st.number_input("Sea Water Pressure (kg/cm2-g)", key="in_sw_press", on_change=sync_var, args=('sw_press', 'in_sw_press'))
                    st.number_input("CW supply", key="in_cw_supply", on_change=sync_var, args=('cw_supply', 'in_cw_supply'))
                with t2: 
                    st.number_input("Brine Discharge Temp (°C)", key="in_brine_out", on_change=sync_var, args=('brine_out_t', 'in_brine_out'))
                    st.number_input("Brine Discharge Pressure (kg/cm2-g)", key="in_brine_press", on_change=sync_var, args=('brine_press', 'in_brine_press'))
                    st.number_input("CW Return", key="in_cw_return", on_change=sync_var, args=('cw_return', 'in_cw_return'))
                    st.number_input("CW Flow (m3/h)", key="in_cw_flow", on_change=sync_var, args=('cw_flow', 'in_cw_flow'))
                with t3: 
                    st.number_input("1st Effect Vapour Temp (°C)", key="in_t1", on_change=sync_var, args=('mra_t1', 'in_t1'))
                    st.number_input("1st effect vapour pressure (mmHg)", key="in_press", on_change=sync_var, args=('mra_press', 'in_press'))
                    st.number_input("1st effect brine temp (°C)", key="in_bt1", on_change=sync_var, args=('mra_bt1', 'in_bt1'))
                    st.number_input("11th Effect Brine Temp (°C)", key="in_brine_11", on_change=sync_var, args=('brine_11', 'in_brine_11'))
                with t4: 
                    st.number_input("Condensate Return (m3/h)", key="in_cond_flow", on_change=sync_var, args=('cond_flow', 'in_cond_flow'))
                    st.number_input("condensate temp (°C)", key="in_cond_temp", on_change=sync_var, args=('cond_temp', 'in_cond_temp'))
                    st.number_input("Condensate Conductivity (µS/cm)", key="in_cond_cond", on_change=sync_var, args=('cond_cond', 'in_cond_cond'))
                    st.number_input("Feed Temp to Cold Group (°C)", key="in_feed_cold", on_change=sync_var, args=('feed_cold', 'in_feed_cold'))
                    st.number_input("LP Steam Pressure (kg/cm2-g)", key="in_stm_press", on_change=sync_var, args=('stm_press', 'in_stm_press'))
                    
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
                with ch2: 
                    st.number_input("Actual Consumption (kg/hr)", key="in_anti_cons", on_change=sync_var, args=('chem_anti_cons', 'in_anti_cons'))
                    
                st.markdown("**Kem Antifoam 1795**")
                ch3, ch4 = st.columns(2)
                with ch3: 
                    st.number_input("Dosing Level (PPM)", key="in_foam_ppm", on_change=sync_var, args=('chem_foam_ppm', 'in_foam_ppm'))
                with ch4: 
                    st.number_input("Actual Consumption (kg/hr)", key="in_foam_cons", on_change=sync_var, args=('chem_foam_cons', 'in_foam_cons'))

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

        st.markdown("### I) CHEMICAL CONSUMPTION")
        df_i = pd.DataFrame([
            {"Parameter": "Antiscalant (ID204)/IN-204AS", "UOM": "gm/m3 sea water", "Design": "7", "SOR Base": 10.5, "Actual": anti_gm_m3, "Difference": anti_gm_m3 - 10.5},
            {"Parameter": "Antifoam", "UOM": "gm/m3 sea water", "Design": "0.25", "SOR Base": 0.16, "Actual": foam_gm_m3, "Difference": foam_gm_m3 - 0.16}
        ])
        st.dataframe(df_i.style.map(color_diff, subset=['Difference']).format({"SOR Base": "{:.2f}", "Actual": "{:.2f}", "Difference": "{:+.2f}"}), use_container_width=True, hide_index=True)
        
        sor_export_dfs = {
            "A) SEA WATER": df_a, "B) LP STEAM": df_b, "C) COOLING WATER": df_c, 
            "D) DESALINATED WATER": df_d, "E) BRINE DISCHARGE": df_e, 
            "F) CONDENSATE RETURN": df_f, "H) PLANT CAPACITY DETAILS": df_h, 
            "I) CHEMICAL CONSUMPTION": df_i
        }

    # --- TAB 2: OVERALL HTC ---
    with tabs[2]:
        st.subheader("Thermal Integrity & Fouling Analysis")
        col1, col2 = st.columns(2)
        with col1:
            st.markdown("### 1st Effect HTC Performance")
            st.number_input("1st Effect Surface Area (m²)", key="t2_area_1st", on_change=sync_var, args=('area_1st', 't2_area_1st'))
            st.number_input("Sea Water Upper (m³/h)", key="t2_sw_up", on_change=sync_var, args=('sw_upper', 't2_sw_up'))
            st.number_input("1st Effect Vapour Temp (°C)", key="t2_t1", on_change=sync_var, args=('mra_t1', 't2_t1'))
            st.number_input("1st effect brine temp (°C)", key="t2_bt1", on_change=sync_var, args=('mra_bt1', 't2_bt1'))
            st.divider()
            st.metric("1st Effect ΔT", f"{ops_data['dt_1st']:.2f} °C")
            st.metric("1st Effect Q (Heat Load)", f"{ops_data['q_1st']/1000:,.0f} kW")
            st.metric("1st Effect HTC (U)", f"{ops_data['htc_1st']:.2f} W/m²K")
            st.metric("1st Effect Fouling Factor", f"{ops_data['fouling_1st']:.6f}")

        with col2:
            st.markdown("### Overall Plant HTC Performance")
            st.number_input("Overall Surface Area (m²)", key="t2_area_overall", on_change=sync_var, args=('area_overall', 't2_area_overall'))
            st.number_input("Sea Water Feed (m³/h)", key="t2_sw_tot", on_change=sync_var, args=('sw_total', 't2_sw_tot'))
            st.number_input("Sea Water cond I/L temp (°C)", key="t2_sw_in", on_change=sync_var, args=('sw_in_t', 't2_sw_in'))
            st.number_input("Brine Discharge Temp (°C)", key="t2_brine_out", on_change=sync_var, args=('brine_out_t', 't2_brine_out'))
            st.divider()
            st.metric("Overall ΔT (Simple)", f"{ops_data['dt_overall_simple']:.2f} °C")
            st.metric("Overall Q (Heat Load)", f"{ops_data['q_overall']/1000:,.0f} kW")
            st.metric("Overall HTC (U)", f"{ops_data['htc_overall']:.2f} W/m²K")
            st.metric("Overall Fouling Factor", f"{ops_data['fouling_overall']:.6f}")

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
                            "Area_1st": [get_v('area_1st')], 
                            "Area_Overall": [get_v('area_overall')], 
                            "Remarks": [get_v('remarks')]
                        }
                        for cat in ['Feed', 'Product']:
                            for param, details in WATER_SPECS[cat].items(): 
                                db_dict[details['db_col']] = [get_v(details['var'])]
                        
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
        st.markdown("Upload your monthly Excel/CSV logs directly. The system utilizes the exact layout from the native operational registry.")
        
        bulk_template = pd.DataFrame(columns=RIL_EXCEL_HEADERS)
        st.download_button(label="Download Exact RIL Matrix Schema Template", data=bulk_template.to_csv(index=False).encode('utf-8'), file_name='MED4_Bulk_Template.csv', mime='text/csv')
        
        st.divider()
        bulk_file = st.file_uploader("Upload CSV Data File", type=["csv"], key="bulk_uploader")
        
        if bulk_file is not None:
            try:
                df_bulk = pd.read_csv(bulk_file)
                
                # Filter out the Design, Unit, and TAG metadata rows if present
                df_bulk = df_bulk[~df_bulk['Parameter'].isin(['Design', 'Unit', 'TAG'])]

                # Fix Antiscalant columns dynamically based on native CSV vs Template Upload
                if 'Antiscalant residual' in df_bulk.columns:
                    df_bulk.rename(columns={
                        'Antiscalant residual': 'Anti_PPM',
                        'Unnamed: 27': 'Anti_Hot',
                        'Unnamed: 28': 'Anti_Brine'
                    }, inplace=True)
                else:
                    df_bulk.rename(columns={
                        'Antiscalant residual (Cold group)': 'Anti_PPM',
                        'Antiscalant residual (Hot group)': 'Anti_Hot',
                        'Antiscalant residual (Brine)': 'Anti_Brine'
                    }, inplace=True)

                # Map exact Excel Headers to Internal Logic 
                df_bulk.rename(columns={
                    'Parameter': 'Date',
                    'Sea water Upper': 'Sea Water Upper',
                    'Sea water Lower': 'Sea Water Lower',
                    'Sea water feed': 'Sea Water Feed',
                    'Brine return': 'Brine Water Return',
                    ' Desal Production': 'Desal production',
                    'LP Steam Consumption': 'LP Steam consumption',
                    'Condensate return': 'Condensate Return',
                    'Condensate Temp': 'condensate temp',
                    "1'st effect vapour Temp": '1st Effect Vapour Temp',
                    '1st Effect Brine Temp': '1st effect brine temp',
                    '(1st effect vapour-1st effect brine) Delta Temp': 'Delta T',
                    '1st Effect Vapour pres': '1st effect vapour pressure',
                    'Steam Inlet Temp': 'Steam inlet temp',
                    'Brine DischargeTemp': 'Brine Discharge Temp',
                    'Sea water cond (FFC) I/L temp': 'Sea Water cond I/L temp',
                    'Sea water cond (FFC) o/L temp': 'Sea Water Condenser O/L Temp',
                    'CW (FCC) supply': 'CW supply',
                    'CW (FCC) return': 'CW Return',
                    'Gross desal water production': 'Gross production',
                    '11 effect brine Temp': '11th Effect Brine Temp',
                    'Overall delta T(1st eff brine temp - 11th eff brine temp)': 'Overall Delta T'
                }, inplace=True)

                missing = [c for c in ['Date', 'Sea Water Upper', 'Sea Water Feed', 'Brine Water Return', 'Desal production', 'LP Steam consumption'] if c not in df_bulk.columns]
                if missing:
                    st.warning(f"Missing core columns will be auto-filled: {', '.join(missing)}")
                    for c in missing: 
                        df_bulk[c] = np.nan
                
                num_cols = [c for c in df_bulk.columns if c not in ["Date", "Remarks", "Unnamed: 27", "Unnamed: 28"]]
                for col in num_cols:
                    if col in df_bulk.columns:
                        df_bulk[col] = pd.to_numeric(df_bulk[col].astype(str).str.replace(',', '', regex=False), errors='coerce')
                
                df_bulk = df_bulk.dropna(subset=["Date"])
                
                if len(df_bulk) > 0:
                    for col_name, baseline_val in zip(
                        ['1st effect vapour pressure', '1st Effect Vapour Temp', 'Sea Water Upper', '1st effect brine temp', 'Brine Water Return', 'LP Steam consumption'],
                        [231.76, 68.47, 553.63, 65.46, 1275.50, 71.75]
                    ):
                        if col_name in df_bulk.columns:
                            df_bulk[col_name] = df_bulk[col_name].fillna(baseline_val)
                    
                    if 'Anti_PPM' in df_bulk.columns: df_bulk['Anti_PPM'] = df_bulk['Anti_PPM'].fillna(4.82)
                    if 'Gross production' in df_bulk.columns: df_bulk['Gross production'] = df_bulk['Gross production'].fillna(0.0)
                    
                    # MASTER DATE FIX: Aggressively standardize raw CSV string and dump bad lines BEFORE adding to DB.
                    df_bulk['Date_Clean'] = standardize_dates(df_bulk['Date']).dt.strftime('%Y-%m-%d')
                    df_bulk = df_bulk.dropna(subset=['Date_Clean'])
                    
                    df_bulk['GOR'] = np.where(df_bulk['LP Steam consumption'] > 0, df_bulk['Gross production'] / df_bulk['LP Steam consumption'], 0)
                    if 'Delta T' not in df_bulk.columns or df_bulk['Delta T'].isnull().all():
                        df_bulk['Delta T'] = df_bulk['1st Effect Vapour Temp'] - df_bulk['1st effect brine temp']

                    if model_type == "OLS":
                        df_bulk['Predicted'] = (
                            coefs["Intercept"] + 
                            (coefs["Press_1st"] * df_bulk['1st effect vapour pressure']) + 
                            (coefs["Temp_1st"] * df_bulk['1st Effect Vapour Temp']) + 
                            (coefs["SW_Upper"] * df_bulk['Sea Water Upper']) + 
                            (coefs["Brine_Temp_1st"] * df_bulk['1st effect brine temp']) + 
                            (coefs["Brine_Flow"] * df_bulk['Brine Water Return']) + 
                            (coefs["LP_Steam"] * df_bulk['LP Steam consumption']) + 
                            (coefs.get("Anti_PPM", MRA_COEF_2014["Anti_PPM"]) * df_bulk['Anti_PPM'])
                        )
                    else:
                        try:
                            active_model = joblib.load(AI_MODEL_FILE)
                            bulk_input_df = df_bulk[['1st effect vapour pressure', '1st Effect Vapour Temp', 'Sea Water Upper', '1st effect brine temp', 'Brine Water Return', 'LP Steam consumption', 'Anti_PPM']].copy()
                            bulk_input_df.columns = ["Press_1st", "Temp_1st", "SW_Upper", "Brine_Temp_1st", "Brine_Flow", "LP_Steam", "Anti_PPM"]
                            df_bulk['Predicted'] = active_model.predict(bulk_input_df)
                        except: 
                            df_bulk['Predicted'] = 0.0
                            
                    df_bulk['Residual'] = df_bulk['Gross production'] - df_bulk['Predicted']
                    
                    if 'Sea Water cond I/L temp' in df_bulk.columns: df_bulk['Sea Water cond I/L temp'] = df_bulk['Sea Water cond I/L temp'].fillna(30.0)
                    if 'Brine Discharge Temp' in df_bulk.columns: df_bulk['Brine Discharge Temp'] = df_bulk['Brine Discharge Temp'].fillna(41.0)
                    if 'Sea Water Feed' in df_bulk.columns: df_bulk['Sea Water Feed'] = df_bulk['Sea Water Feed'].fillna(2100.0)

                    # Ensure condensate temp exists as a clean numeric column. A missing value here is
                    # treated as "not provided" (NaN), never silently as a real 0°C reading, since a bare
                    # 0 would otherwise wreck the cold-side LMTD driving force below.
                    if 'condensate temp' not in df_bulk.columns:
                        df_bulk['condensate temp'] = np.nan
                    df_bulk['condensate temp'] = pd.to_numeric(df_bulk['condensate temp'], errors='coerce')
                    cond_temp_raw = df_bulk['condensate temp']

                    area_overall = get_v('area_overall')
                    area_1st = get_v('area_1st')
                    q_overall = (df_bulk['LP Steam consumption'] * LATENT_HEAT_STEAM_KJ_KG * 1000) / 3600
                    q_1st = q_overall

                    # --- STEC: Specific Thermal Energy Consumption (kWh/ton), same formula as manual entry ---
                    df_bulk['STEC'] = np.where(
                        df_bulk['Desal production'] > 0,
                        ((df_bulk['LP Steam consumption'] * 1000) / 3600 * LATENT_HEAT_STEAM_KJ_KG) / df_bulk['Desal production'],
                        0
                    )

                    # --- Overall HTC via LMTD (matches the manual-entry formula, vectorized for bulk rows) ---
                    # Hot side: 1st effect vapour temp vs brine discharge temp (always present in bulk data).
                    dt_ov_hot = df_bulk['1st Effect Vapour Temp'] - df_bulk['Brine Discharge Temp']
                    # Cold side: condensate temp vs seawater condenser inlet temp, when condensate temp was
                    # actually supplied in the CSV; otherwise fall back to the same 0.8x proxy manual entry
                    # uses when its own cold-side reading is unavailable/invalid.
                    dt_ov_cold_raw = cond_temp_raw - df_bulk['Sea Water cond I/L temp']
                    dt_ov_cold = np.where(
                        cond_temp_raw.notna() & (dt_ov_cold_raw > 0),
                        dt_ov_cold_raw,
                        dt_ov_hot * 0.8
                    )
                    ratio_ov = np.where(dt_ov_cold > 0, dt_ov_hot / dt_ov_cold, 1.0)
                    log_ov = np.log(np.where(ratio_ov > 0, ratio_ov, 1.0))
                    lmtd_ov = np.where(
                        (dt_ov_hot > 0) & (dt_ov_cold > 0) & (dt_ov_hot != dt_ov_cold) & (log_ov != 0),
                        (dt_ov_hot - dt_ov_cold) / log_ov,
                        dt_ov_hot
                    )
                    df_bulk['Overall HTC'] = np.where((lmtd_ov > 0) & (area_overall > 0), q_overall / (area_overall * lmtd_ov), 0)

                    # --- 1st Effect HTC via LMTD ---
                    # Hot side: same as Delta T (1st effect vapour temp vs 1st effect brine temp).
                    dt_1st_hot = df_bulk['Delta T']
                    # Cold side: bulk data has no per-effect live brine reading (that field is a manual-
                    # dashboard-only input on the Effects tab), so this always uses the same 0.8x fallback
                    # manual entry itself falls back to when that live reading is missing.
                    dt_1st_cold = dt_1st_hot * 0.8
                    ratio_1st = np.where(dt_1st_cold > 0, dt_1st_hot / dt_1st_cold, 1.0)
                    log_1st = np.log(np.where(ratio_1st > 0, ratio_1st, 1.0))
                    lmtd_1st = np.where(
                        (dt_1st_hot > 0) & (dt_1st_cold > 0) & (dt_1st_hot != dt_1st_cold) & (log_1st != 0),
                        (dt_1st_hot - dt_1st_cold) / log_1st,
                        dt_1st_hot
                    )
                    df_bulk['1st Effect HTC'] = np.where((lmtd_1st > 0) & (area_1st > 0), q_1st / (area_1st * lmtd_1st), 0)
                    
                    db_ready_dict = {
                        "Date": df_bulk['Date_Clean'], 
                        "Sea Water Upper": df_bulk['Sea Water Upper'], 
                        "Sea Water Lower": df_bulk.get('Sea Water Lower', pd.Series(0, index=df_bulk.index)).fillna(0),
                        "Sea Water Feed": df_bulk['Sea Water Feed'], 
                        "Sea Water Pressure": df_bulk.get('Sea Water Pressure', pd.Series(1.7, index=df_bulk.index)).fillna(1.7),
                        "Brine Water Return": df_bulk['Brine Water Return'],
                        "Desal production": df_bulk['Desal production'].fillna(0), 
                        "LP Steam consumption": df_bulk['LP Steam consumption'],
                        "LP Steam Pressure": df_bulk.get('LP Steam Pressure', pd.Series(4.3, index=df_bulk.index)).fillna(4.3),
                        "Condensate Return": df_bulk.get('Condensate Return', pd.Series(0, index=df_bulk.index)).fillna(0), 
                        "condensate temp": df_bulk.get('condensate temp', pd.Series(0, index=df_bulk.index)).fillna(0),
                        "Condensate Conductivity": df_bulk.get('Condensate Conductivity', pd.Series(3.0, index=df_bulk.index)).fillna(3.0),
                        "1st Effect Vapour Temp": df_bulk['1st Effect Vapour Temp'], 
                        "1st effect brine temp": df_bulk['1st effect brine temp'],
                        "11th Effect Brine Temp": df_bulk.get('11th Effect Brine Temp', pd.Series(43.0, index=df_bulk.index)).fillna(43.0),
                        "Feed Temp to Cold Group": df_bulk.get('Feed Temp to Cold Group', pd.Series(37.0, index=df_bulk.index)).fillna(37.0),
                        "Delta T": df_bulk['Delta T'], 
                        "1st effect vapour pressure": df_bulk['1st effect vapour pressure'],
                        "Brine Discharge Temp": df_bulk['Brine Discharge Temp'],
                        "Brine Discharge Pressure": df_bulk.get('Brine Discharge Pressure', pd.Series(1.3, index=df_bulk.index)).fillna(1.3),
                        "Sea Water cond I/L temp": df_bulk['Sea Water cond I/L temp'], 
                        "Sea Water Condenser O/L Temp": df_bulk.get('Sea Water Condenser O/L Temp', pd.Series(0, index=df_bulk.index)).fillna(0),
                        "CW supply": df_bulk.get('CW supply', pd.Series(0, index=df_bulk.index)).fillna(0), 
                        "CW Return": df_bulk.get('CW Return', pd.Series(0, index=df_bulk.index)).fillna(0),
                        "CW Flow": df_bulk.get('CW Flow', pd.Series(2726.0, index=df_bulk.index)).fillna(2726.0),
                        "Gross production": df_bulk['Gross production'],
                        "GOR": df_bulk['GOR'].round(2), 
                        "STEC": df_bulk['STEC'].round(2),
                        "Overall HTC": df_bulk['Overall HTC'].round(2), 
                        "1st Effect HTC": df_bulk['1st Effect HTC'].round(2),
                        "Residual": df_bulk['Residual'].round(1),
                        "Antiscalant (kg)": df_bulk.get('Antiscalant (kg)', pd.Series(0, index=df_bulk.index)).fillna(0), 
                        "Antifoam (kg)": df_bulk.get('Antifoam (kg)', pd.Series(0, index=df_bulk.index)).fillna(0),
                        "Anti_PPM": df_bulk['Anti_PPM'], 
                        "Remarks": df_bulk.get('Remarks', pd.Series("", index=df_bulk.index)).fillna(""),
                        "Area_1st": area_1st, 
                        "Area_Overall": area_overall
                    }
                    
                    for cat in ['Feed', 'Product']:
                        for param, details in WATER_SPECS[cat].items(): 
                            db_ready_dict[details['db_col']] = df_bulk.get(details['db_col'], pd.Series(details['avg'], index=df_bulk.index)).fillna(details['avg'])
                            
                    db_ready_df = pd.DataFrame(db_ready_dict)
                    
                    st.success(f"Calculated KPIs for {len(db_ready_df)} rows.")
                    st.dataframe(db_ready_df.style.format(precision=2), use_container_width=True, hide_index=True)
                    
                    st.markdown("### Save Bulk Data")
                    c_pwd, c_save = st.columns([2, 2])
                    with c_pwd: 
                        pwd_bulk = st.text_input("Master Password", type="password", key="pwd_bulk", label_visibility="collapsed", placeholder="Enter Password to Sync")
                    with c_save:
                        if st.button("Append to Database", use_container_width=True):
                            if pwd_bulk == "12345678":
                                st.session_state.daily_logs = pd.concat([st.session_state.daily_logs, db_ready_df], ignore_index=True)
                                
                                # MASTER DATE FIX: Standardize database once more before doing the final duplicate drop.
                                st.session_state.daily_logs['Date'] = standardize_dates(st.session_state.daily_logs['Date']).dt.strftime('%Y-%m-%d')
                                st.session_state.daily_logs = st.session_state.daily_logs.dropna(subset=['Date'])
                                st.session_state.daily_logs = st.session_state.daily_logs.drop_duplicates(subset=['Date'], keep='last').reset_index(drop=True)
                                
                                save_database(db_conn, st.session_state.daily_logs, LOCAL_DB_FILE)
                                st.success("Data Synced!")
                                time.sleep(1.5)
                                st.rerun()
                            elif pwd_bulk != "": 
                                st.error("Incorrect Password.")
                else: 
                    st.error("No valid data found in CSV.")
            except Exception as e: 
                st.error(f"Error processing file: {e}")
                
    render_chatbot()
