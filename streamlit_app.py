# requirements: pandas, numpy, python-docx, altair
import streamlit as st
import pandas as pd
import numpy as np
import datetime
import io
import altair as alt
from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH

st.set_page_config(page_title="Chembond | MED-4 Management", layout="wide")

# ==========================================
# 1. CORE SYNCHRONIZATION ENGINE
# ==========================================
DEFAULTS = {
    'steam': 73.0, 'desal': 740.0, 'gross': 790.0,
    'sw_upper': 775.0, 'sw_total': 2100.0, 'brine_ret': 1250.0,
    'sw_in_t': 30.0, 'brine_out_t': 41.0, 'stm_in_t': 179.0, 'vap_out_t': 70.0,
    'mra_press': 248.0, 'mra_t1': 69.5, 'mra_bt1': 66.5,
    'f_ph': 8.14, 'f_turb': 3.2, 'f_tss': 6.5, 'f_tds': 41000.0,
    'f_alk': 170.0, 'f_ca': 1040.0, 'f_cl': 21500.0, 'f_so4': 3150.0,
    'p_ph': 6.5, 'p_cond': 4.6, 'p_tds': 2.5, 'p_iron': 0.05,
    'p_cl': 0.0, 'p_so4': 0.0,
    'skip_eff': False, 'skip_wq': False
}

SYNC_MAP = {
    'steam': ['in_steam', 't1_steam', 't4_steam'],
    'desal': ['in_desal', 't1_desal'],
    'gross': ['in_gross', 't1_gross'],
    'sw_upper': ['in_sw_up', 't1_sw_up', 't4_sw_up'],
    'sw_total': ['in_sw_tot', 't1_sw_tot'],
    'brine_ret': ['in_brine', 't1_brine', 't4_bflow'],
    'sw_in_t': ['in_sw_in', 't2_sw_in'],
    'brine_out_t': ['in_brine_out', 't2_brine_out'],
    'stm_in_t': ['in_stm_in', 't2_stm_in', 't4_stm_t'],
    'vap_out_t': ['in_vap_out', 't2_vap_out'],
    'mra_press': ['in_press', 't4_press'],
    'mra_t1': ['in_t1', 't4_t1'],
    'mra_bt1': ['in_bt1', 't4_bt1'],
    'f_ph': ['in_f_ph', 't3_f_ph'], 'f_turb': ['in_f_turb', 't3_f_turb'],
    'f_tss': ['in_f_tss', 't3_f_tss'], 'f_tds': ['in_f_tds', 't3_f_tds'],
    'f_alk': ['in_f_alk', 't3_f_alk'], 'f_ca': ['in_f_ca', 't3_f_ca'],
    'f_cl': ['in_f_cl', 't3_f_cl'], 'f_so4': ['in_f_so4', 't3_f_so4'],
    'p_ph': ['in_p_ph', 't3_p_ph'], 'p_cond': ['in_p_cond', 't3_p_cond'],
    'p_tds': ['in_p_tds', 't3_p_tds'], 'p_iron': ['in_p_iron', 't3_p_iron'],
    'p_cl': ['in_p_cl', 't3_p_cl'], 'p_so4': ['in_p_so4', 't3_p_so4'],
    'skip_eff': ['in_skip_eff'], 'skip_wq': ['in_skip_wq']
}

if 'sync_initialized' not in st.session_state:
    for var_name, keys in SYNC_MAP.items():
        for k in keys: st.session_state[k] = DEFAULTS[var_name]
    st.session_state.vars = DEFAULTS.copy()
    st.session_state.sync_initialized = True

if 'daily_logs' not in st.session_state:
    st.session_state.daily_logs = pd.DataFrame(columns=["Date", "Gross Prod (m3/h)", "Desal (m3/h)", "Steam (TPH)", "SW Feed (m3/h)", "GOR", "Overall HTC", "Residual"])

if 'shared_effect_df' not in st.session_state:
    st.session_state.shared_effect_df = pd.DataFrame({
        "Effect ID": [f"Effect {i}" for i in range(1, 12)],
        "Vapor Temp (°C)": np.round(np.linspace(69.0, 42.0, 11), 1),
        "Brine Temp (°C)": np.round(np.linspace(66.3, 40.0, 11), 1)
    })

def update_var(var_key, widget_key):
    st.session_state.vars[var_key] = st.session_state[widget_key]
    
def sync_var(var_name, source_key):
    new_val = st.session_state[source_key]
    st.session_state.vars[var_name] = new_val
    for target_key in SYNC_MAP[var_name]:
        if target_key != source_key: st.session_state[target_key] = new_val

def get_v(var_name): return st.session_state.vars[var_name]

# ==========================================
# 2. CONSTANTS & BASELINES
# ==========================================
LATENT_HEAT_STEAM_KJ_KG = 2260.0 
# Note: Pressure unit is now mmHg
MRA_COEF = {"Intercept": -13.9586, "Press_1st": 0.4697, "Temp_1st": 15.0401, "SW_Upper": 1.1517, "Brine_Temp_1st": -17.7986, "Brine_Flow": -0.3292, "LP_Steam": 1.8876, "Steam_Temp": 1.2511}
MRA_BASELINE = {"Press_1st": 248.0, "Temp_1st": 69.5, "SW_Upper": 775.0, "Brine_Temp_1st": 66.5, "Brine_Flow": 1250.0, "LP_Steam": 72.0, "Steam_Temp": 179.0}

WATER_SPECS = {
    "Feed": {"pH": {"lim": (7.5, 9.2), "var": "f_ph"}, "Turbidity (NTU)": {"lim": (0.0, 5.0), "var": "f_turb"}, "TSS (ppm)": {"lim": (0.0, 10.0), "var": "f_tss"}, "TDS (ppm)": {"lim": (0.0, 42000.0), "var": "f_tds"}, "Total Alkalinity": {"lim": (160.0, 190.0), "var": "f_alk"}, "Calcium Hardness": {"lim": (950.0, 1100.0), "var": "f_ca"}, "Chlorides": {"lim": (21000.0, 22000.0), "var": "f_cl"}, "Sulphate": {"lim": (3050.0, 3250.0), "var": "f_so4"}},
    "Product": {"pH": {"lim": (5.5, 7.0), "var": "p_ph"}, "Conductivity (μs/cm)": {"lim": (0.0, 15.0), "var": "p_cond"}, "TDS (ppm)": {"lim": (0.0, 10.0), "var": "p_tds"}, "Total Iron": {"lim": (0.0, 0.1), "var": "p_iron"}, "Chlorides": {"lim": (0.0, 5.0), "var": "p_cl"}, "Sulphate": {"lim": (0.0, 1.0), "var": "p_so4"}}
}

# ==========================================
# 3. REPORT GENERATORS (DOCX)
# ==========================================
def generate_comprehensive_report(date, ops, effect_df, w_data, mra, skip_eff, skip_wq):
    doc = Document()
    doc.add_heading('MED-4 Daily Operational & Performance Report', 0).alignment = WD_ALIGN_PARAGRAPH.CENTER
    p = doc.add_paragraph()
    p.add_run('Prepared by: ').bold = True
    p.add_run('Chembond Chemicals Ltd.\n')
    p.add_run('Date: ').bold = True
    p.add_run(str(date))
    p.alignment = WD_ALIGN_PARAGRAPH.RIGHT

    doc.add_heading('1. Executive Summary', level=1)
    doc.add_paragraph(f"On {date}, the MED-4 unit achieved a Gross Production of {ops['Gross Prod']} m³/h and a Gain Output Ratio (GOR) of {ops['GOR']:.2f}:1. The Specific Thermal Energy Consumption (STEC) was {ops['STEC']:.2f} kWh/ton with a system recovery of {ops['Recovery']:.1f}%.")

    doc.add_heading('2. Operational Data Summary', level=1)
    t_ops = doc.add_table(rows=1, cols=4)
    t_ops.style = 'Table Grid'
    for i, h in enumerate(['Parameter', 'UOM', 'Design', 'Actual']): t_ops.rows[0].cells[i].text = h
    ops_rows = [['Total SW Feed', 'm³/h', '2400', str(ops['SW Total'])], ['SW Upper', 'm³/h', '580', str(ops['SW Upper'])], ['Brine Return', 'm³/h', '1400', str(ops['Brine Return'])], ['Desal', 'm³/h', '1000', str(ops['Desal'])], ['Gross Prod', 'm³/h', '-', str(ops['Gross Prod'])], ['LP Steam', 'TPH', '92-94.5', str(ops['Steam'])], ['Recovery', '%', '40.0', f"{ops['Recovery']:.2f}"], ['GOR', 'Ratio', '10.5 : 1', f"{ops['GOR']:.2f} : 1"], ['Steam Economy', 'Ratio', '-', f"{ops['Economy']:.4f}"]]
    for row in ops_rows:
        rc = t_ops.add_row().cells
        for i, val in enumerate(row): rc[i].text = val

    doc.add_heading('3. Effect-wise Profile', level=1)
    doc.add_paragraph(f"Overall Plant LMTD: {ops['LMTD']:.2f} °C | Overall HTC (U): {ops['HTC']:.2f} W/m²K | Fouling Factor: {ops['Fouling']:.6f}")
    
    if skip_eff:
        doc.add_paragraph("NOTE: The 11-Effect Temperature Cascade was not recorded for this operational day.", style='BodyText')
    else:
        t_eff = doc.add_table(rows=1, cols=4)
        t_eff.style = 'Table Grid'
        for i, h in enumerate(['Effect ID', 'Vapor (°C)', 'Brine (°C)', 'ΔT (°C)']): t_eff.rows[0].cells[i].text = h
        for idx, row in effect_df.iterrows():
            rc = t_eff.add_row().cells
            rc[0].text, rc[1].text, rc[2].text, dt_val = str(row['Effect ID']), f"{row['Vapor Temp (°C)']:.2f}", f"{row['Brine Temp (°C)']:.2f}", row['ΔT (°C)']
            rc[3].text = f"{dt_val:.2f}"
            if dt_val > 2.0: rc[3].paragraphs[0].runs[0].font.color.rgb = RGBColor(255, 0, 0)

    doc.add_heading('4. Water Quality', level=1)
    if skip_wq:
        doc.add_paragraph("NOTE: Laboratory water quality parameters were not recorded for this operational day.", style='BodyText')
    else:
        t_wq = doc.add_table(rows=1, cols=4)
        t_wq.style = 'Table Grid'
        for i, h in enumerate(['Parameter', 'Stream', 'Limit/Spec', 'Actual']): t_wq.rows[0].cells[i].text = h
        for param, data in w_data['Feed'].items():
            rc = t_wq.add_row().cells
            rc[0].text, rc[1].text, rc[2].text, rc[3].text = str(param), 'Sea Water Feed', f"{data['min']}-{data['max']}", str(data['val'])
        for param, data in w_data['Product'].items():
            rc = t_wq.add_row().cells
            rc[0].text, rc[1].text, rc[2].text, rc[3].text = str(param), 'Desal Product', f"{data['min']}-{data['max']}", str(data['val'])

    doc.add_heading('5. MRA Fouling Indicator', level=1)
    doc.add_paragraph(f"Actual Gross: {mra['Actual']:.1f} m³/h | MRA Predicted: {mra['Predicted']:.1f} m³/h | Residual: {mra['Residual']:.1f} m³/h")
    t_mra = doc.add_table(rows=1, cols=5)
    t_mra.style = 'Table Grid'
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
    
    t_agg = doc.add_table(rows=1, cols=4)
    t_agg.style = 'Table Grid'
    for i, h in enumerate(['Metric', 'Minimum', 'Maximum', 'Average']): t_agg.rows[0].cells[i].text = h
    
    metrics = [
        ("Gross Production (m³/h)", df_month['Gross Prod (m3/h)']),
        ("Gain Output Ratio (GOR)", df_month['GOR']),
        ("Overall HTC (W/m²K)", df_month['Overall HTC']),
        ("MRA Residual (TPH)", df_month['Residual'])
    ]
    for name, series in metrics:
        rc = t_agg.add_row().cells
        rc[0].text = name
        rc[1].text = f"{series.min():.2f}"
        rc[2].text = f"{series.max():.2f}"
        rc[3].text = f"{series.mean():.2f}"
        
    doc.add_heading('2. Daily Operational Log', level=1)
    t_log = doc.add_table(rows=1, cols=5)
    t_log.style = 'Table Grid'
    for i, h in enumerate(['Date', 'Gross Prod', 'GOR', 'HTC', 'Residual']): t_log.rows[0].cells[i].text = h
    
    for _, row in df_month.iterrows():
        rc = t_log.add_row().cells
        rc[0].text = row['Date'].strftime('%Y-%m-%d')
        rc[1].text = f"{row['Gross Prod (m3/h)']:.1f}"
        rc[2].text = f"{row['GOR']:.2f}"
        rc[3].text = f"{row['Overall HTC']:.1f}"
        rc[4].text = f"{row['Residual']:.1f}"

    bio = io.BytesIO()
    doc.save(bio)
    return bio.getvalue()

# ==========================================
# 4. MAIN APP LOGIC
# ==========================================
def main():
    st.sidebar.markdown("### 🔹 CHEMBOND CHEMICALS LTD.") 
    st.sidebar.divider()
    log_date = st.sidebar.date_input("Date", datetime.date.today())
    area_m2 = st.sidebar.number_input("Overall Surface Area (m²)", value=1757.49)
    
    st.title("🏭 Reliance MED-4 Management Suite")
    tabs = st.tabs(["📥 0. Central Inputs", "🌊 1. Flow KPIs", "🔥 2. Thermo & HTC", "🧪 3. Quality", "🧠 4. MRA", "📂 5. Reporting"])

    # --- CALCULATE LIVE DATA FOR ALL TABS ---
    ops_data = {'Steam': get_v('steam'), 'Desal': get_v('desal'), 'Gross Prod': get_v('gross'), 'SW Upper': get_v('sw_upper'), 'SW Total': get_v('sw_total'), 'Brine Return': get_v('brine_ret'), 'SW In': get_v('sw_in_t'), 'Brine Out': get_v('brine_out_t'), 'Stm In': get_v('stm_in_t'), 'Vap Out': get_v('vap_out_t')}
    
    # Mathematical Formulas explicit definition
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
    mra_data['Predicted'] = (MRA_COEF["Intercept"] + (MRA_COEF["Press_1st"] * get_v('mra_press')) + (MRA_COEF["Temp_1st"] * get_v('mra_t1')) + (MRA_COEF["SW_Upper"] * get_v('sw_upper')) + (MRA_COEF["Brine_Temp_1st"] * get_v('mra_bt1')) + (MRA_COEF["Brine_Flow"] * get_v('brine_ret')) + (MRA_COEF["LP_Steam"] * get_v('steam')) + (MRA_COEF["Steam_Temp"] * get_v('stm_in_t')))
    mra_data['Actual'] = ops_data['Gross Prod']
    mra_data['Residual'] = mra_data['Actual'] - mra_data['Predicted']

    var_data = []
    for name, key, live_val in [("1st Effect Press", "Press_1st", get_v('mra_press')), ("1st Effect Temp", "Temp_1st", get_v('mra_t1')), ("Sea Water Upper", "SW_Upper", get_v('sw_upper')), ("1st Brine Temp", "Brine_Temp_1st", get_v('mra_bt1')), ("Brine Flow", "Brine_Flow", get_v('brine_ret')), ("LP Steam", "LP_Steam", get_v('steam')), ("Steam Temp", "Steam_Temp", get_v('stm_in_t'))]:
        dev = live_val - MRA_BASELINE[key]
        var_data.append([name, MRA_BASELINE[key], live_val, dev, MRA_COEF[key], dev * MRA_COEF[key]])
    mra_data['Variance_DF'] = pd.DataFrame(var_data, columns=["Parameter", "Baseline", "Live Input", "Deviation", "Regression Weight", "Impact (TPH)"])

    water_data = {'Feed': {}, 'Product': {}}
    for cat in ['Feed', 'Product']:
        for param, details in WATER_SPECS[cat].items():
            val = get_v(details['var'])
            status = "✅ Pass" if details['lim'][0] <= val <= details['lim'][1] else "🚨 Fail"
            water_data[cat][param] = {'min': details['lim'][0], 'max': details['lim'][1], 'val': val, 'status': status}

    # --- UI COMPONENTS ---
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

        with st.expander("2. Thermodynamics & Plant Temperatures", expanded=False):
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
                st.caption("Paste the 11 temperatures directly from Excel.")
                edited = st.data_editor(st.session_state.shared_effect_df, key="in_effect_df", use_container_width=True, hide_index=True)
                if not edited.equals(st.session_state.shared_effect_df):
                    st.session_state.shared_effect_df = edited
                    st.rerun()
            else: st.info("Effect-wise data logging is disabled for today.")

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
            else: st.info("Water quality data logging is disabled for today.")

    # --- TAB 1: FLOW KPIs ---
    with tabs[1]:
        st.subheader("Mass Balance & KPI Dashboard")
        st.caption("Formulas: GOR = Gross / Steam | Steam Economy = Steam / Desal | Recovery = Gross / Total SW Feed")
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
        else: st.error("Invalid temperatures for LMTD.")

        st.divider()
        st.subheader("11-Effect Scaling Profiler")
        
        if get_v('skip_eff'):
            st.info("Effect-wise data logging has been skipped for today.")
        else:
            col_t, col_g = st.columns([1, 2])
            with col_t:
                effect_df = st.data_editor(st.session_state.shared_effect_df, key="t2_effect_df", use_container_width=True, hide_index=True)
                if not effect_df.equals(st.session_state.shared_effect_df):
                    st.session_state.shared_effect_df = effect_df
                    st.rerun()
                
            with col_g:
                effect_df['ΔT (°C)'] = effect_df['Vapor Temp (°C)'] - effect_df['Brine Temp (°C)']
                for _, row in effect_df.iterrows():
                    if row['ΔT (°C)'] > 2.0: st.error(f"🚨 **{row['Effect ID']} ALERT:** ΔT is {row['ΔT (°C)']:.2f}°C")
                
                effect_df['Effect ID'] = pd.Categorical(effect_df['Effect ID'], categories=[f"Effect {i}" for i in range(1, 12)], ordered=True)
                base_chart = alt.Chart(effect_df).encode(x=alt.X('Effect ID', title=None))
                bar_chart = base_chart.mark_bar(color='#1f77b4', cornerRadiusTopLeft=3, cornerRadiusTopRight=3).encode(y=alt.Y('ΔT (°C)', title='Delta T (°C)'))
                limit_line = alt.Chart(pd.DataFrame({'y': [2.0]})).mark_rule(color='red', strokeDash=[5, 5], strokeWidth=2).encode(y='y')
                st.altair_chart(bar_chart + limit_line, use_container_width=True)

    # --- TAB 3: WATER ANALYSIS ---
    with tabs[3]:
        st.subheader("Laboratory QA/QC vs Limits")
        if get_v('skip_wq'):
            st.info("Water Quality data logging has been skipped for today.")
        else:
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

    # --- TAB 4: MRA NORMALIZATION ---
    with tabs[4]:
        st.subheader("MRA Fouling Defense")
        controls_col, calc_col = st.columns([1, 2])
        with controls_col:
            st.slider("1st Effect Press (mmHg)", key="t4_press", min_value=100.0, max_value=400.0, on_change=sync_var, args=('mra_press', 't4_press'))
            st.slider("1st Effect Temp (°C)", key="t4_t1", min_value=50.0, max_value=90.0, on_change=sync_var, args=('mra_t1', 't4_t1'))
            st.slider("Sea Water Upper (m³/h)", key="t4_sw_up", min_value=300.0, max_value=1500.0, on_change=sync_var, args=('sw_upper', 't4_sw_up'))
            st.slider("1st Brine Temp (°C)", key="t4_bt1", min_value=40.0, max_value=80.0, on_change=sync_var, args=('mra_bt1', 't4_bt1'))
            st.slider("Brine Flow (m³/h)", key="t4_bflow", min_value=800.0, max_value=2000.0, on_change=sync_var, args=('brine_ret', 't4_bflow'))
            st.slider("LP Steam (TPH)", key="t4_steam", min_value=40.0, max_value=150.0, on_change=sync_var, args=('steam', 't4_steam'))
            st.slider("Steam Temp (°C)", key="t4_stm_t", min_value=140.0, max_value=220.0, on_change=sync_var, args=('stm_in_t', 't4_stm_t'))

        with calc_col:
            k1, k2, k3 = st.columns(3)
            k1.metric("Actual Gross SCADA", f"{mra_data['Actual']:.1f} m³/h")
            k2.metric("MRA Predicted", f"{mra_data['Predicted']:.1f} m³/h")
            if mra_data['Residual'] < -15.0: k3.error(f"Residual: {mra_data['Residual']:.1f} (FOULING)")
            elif mra_data['Residual'] > 15.0: k3.success(f"Residual: {mra_data['Residual']:.1f} (CLEAN)")
            else: k3.info(f"Residual: {mra_data['Residual']:.1f} (NORMAL)")
            st.dataframe(mra_data['Variance_DF'].style.format({"Baseline": "{:.1f}", "Live Input": "{:.1f}", "Deviation": "{:+.1f}", "Regression Weight": "{:.3f}", "Impact (TPH)": "{:+.1f}"}), use_container_width=True, hide_index=True)

    # --- TAB 5: ENTERPRISE REPORTING SUITE ---
    with tabs[5]:
        st.subheader("Intelligence & Reporting Center")
        rep_tabs = st.tabs(["📅 Today's Dashboard", "📆 Monthly Trend", "📊 Quarterly Health"])
        
        with rep_tabs[0]:
            m_col1, m_col2, m_col3, m_col4 = st.columns(4)
            m_col1.metric("Date", str(log_date))
            m_col2.metric("Gross Production", f"{ops_data['Gross Prod']} m³/h", delta=f"{ops_data['Gross Prod'] - 1000:.0f} from Design" if ops_data['Gross Prod'] < 1000 else None)
            m_col3.metric("System GOR", f"{ops_data['GOR']:.2f}", delta=f"{ops_data['GOR'] - 10.5:.2f} from Target" if ops_data['GOR'] < 10.5 else None)
            m_col4.metric("Fouling Residual", f"{mra_data['Residual']:.1f} TPH", delta="Stable" if mra_data['Residual'] >= -15 else "Fouling", delta_color="normal" if mra_data['Residual'] >= -15 else "inverse")
            
            st.divider()
            graph_col1, graph_col2 = st.columns(2)
            with graph_col1:
                st.markdown("#### ⚖️ Variance Impact (TPH)")
                impact_chart = alt.Chart(mra_data['Variance_DF']).mark_bar().encode(
                    x=alt.X('Impact (TPH):Q'), y=alt.Y('Parameter:N', sort='-x', title=''),
                    color=alt.condition(alt.datum['Impact (TPH)'] > 0, alt.value('#2ca02c'), alt.value('#d62728')),
                    tooltip=['Parameter', 'Impact (TPH)']
                ).properties(height=300)
                st.altair_chart(impact_chart, use_container_width=True)

            with graph_col2:
                st.markdown("#### 🌊 Flow Distribution")
                unaccounted = ops_data['SW Total'] - (ops_data['Desal'] + ops_data['Brine Return'])
                mb_data = pd.DataFrame({'Stream': ['Desal (Net)', 'Brine', 'Losses'], 'Volume': [ops_data['Desal'], ops_data['Brine Return'], unaccounted if unaccounted > 0 else 0]})
                donut = alt.Chart(mb_data).mark_arc(innerRadius=50).encode(theta=alt.Theta("Volume:Q"), color=alt.Color("Stream:N", scale=alt.Scale(scheme='set2')), tooltip=['Stream', 'Volume']).properties(height=300)
                st.altair_chart(donut, use_container_width=True)

            st.divider()
            st.markdown("### 🔐 Commit & Export")
            c_save, c_report = st.columns(2)
            with c_save:
                pwd_append = st.text_input("🔑 Master Password", type="password", key="pwd_append")
                if st.button("💾 Append Today's Data", use_container_width=True):
                    if pwd_append == "12345678":
                        new_log = pd.DataFrame({"Date": [pd.to_datetime(log_date)], "Gross Prod (m3/h)": [ops_data['Gross Prod']], "Desal (m3/h)": [ops_data['Desal']], "Steam (TPH)": [ops_data['Steam']], "SW Feed (m3/h)": [ops_data['SW Total']], "GOR": [round(ops_data['GOR'], 2)], "Overall HTC": [round(ops_data['HTC'], 2)], "Residual": [round(mra_data['Residual'], 1)]})
                        st.session_state.daily_logs = pd.concat([st.session_state.daily_logs, new_log], ignore_index=True)
                        st.success("✅ Master Database Updated!")
                    else: st.error("❌ Incorrect Password.")
            
            with c_report:
                st.markdown("<br><br>", unsafe_allow_html=True)
                if st.button("📄 Export Daily Report (.docx)", type="primary", use_container_width=True):
                    word_file = generate_comprehensive_report(log_date, ops_data, st.session_state.shared_effect_df, water_data, mra_data, get_v('skip_eff'), get_v('skip_wq'))
                    st.download_button("📥 Click to Download Document", data=word_file, file_name=f"MED4_Daily_{log_date}.docx", mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document")

        with rep_tabs[1]:
            if not st.session_state.daily_logs.empty:
                df_logs = st.session_state.daily_logs.copy()
                df_logs['Date'] = pd.to_datetime(df_logs['Date'])
                month_data = df_logs[(df_logs['Date'].dt.month == log_date.month) & (df_logs['Date'].dt.year == log_date.year)].copy()
                
                if not month_data.empty:
                    m1, m2, m3, m4 = st.columns(4)
                    m1.metric("Month Avg GOR", f"{month_data['GOR'].mean():.2f}")
                    m2.metric("Month Avg Gross", f"{month_data['Gross Prod (m3/h)'].mean():.0f}")
                    m3.metric("Month Avg HTC", f"{month_data['Overall HTC'].mean():.0f}")
                    m4.metric("Avg MRA Residual", f"{month_data['Residual'].mean():.1f}")
                    
                    st.markdown("#### 📉 Gross Production vs. Target")
                    line_chart = alt.Chart(month_data).mark_line(point=True).encode(x='Date:T', y=alt.Y('Gross Prod (m3/h):Q', scale=alt.Scale(domain=[500, 1100])))
                    st.altair_chart(line_chart + alt.Chart(pd.DataFrame({'y': [1000]})).mark_rule(color='green').encode(y='y'), use_container_width=True)
                    
                    # New Button: Download Monthly Report
                    if st.button("📄 Generate Monthly Report (.docx)", use_container_width=True):
                        monthly_doc = generate_monthly_report(month_data, log_date.strftime('%B'), str(log_date.year))
                        st.download_button("📥 Download Monthly Report", data=monthly_doc, file_name=f"MED4_Monthly_{log_date.strftime('%b_%Y')}.docx", mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document")

            st.divider()
            st.markdown("#### 📥 Secure Export")
            st.session_state.daily_logs = st.data_editor(st.session_state.daily_logs, num_rows="dynamic", use_container_width=True)
            if st.text_input("🔑 Export Password", type="password", key="pwd_dl") == "12345678":
                st.download_button("📥 Download CSV", data=st.session_state.daily_logs.to_csv(index=False).encode('utf-8'), file_name=f"MED4_Master.csv", mime='text/csv')

        with rep_tabs[2]:
            if not st.session_state.daily_logs.empty:
                df_logs = st.session_state.daily_logs.copy()
                df_logs['Date'] = pd.to_datetime(df_logs['Date'])
                df_logs['Recovery (%)'] = (df_logs['Gross Prod (m3/h)'] / df_logs['SW Feed (m3/h)']) * 100
                q_col1, q_col2 = st.columns(2)
                with q_col1:
                    st.markdown("#### 📉 Recovery Trend")
                    rec_chart = alt.Chart(df_logs).mark_circle().encode(x='Date:T', y=alt.Y('Recovery (%):Q', scale=alt.Scale(zero=False)))
                    st.altair_chart(rec_chart + rec_chart.transform_regression('Date', 'Recovery (%)').mark_line(color='red'), use_container_width=True)
                with q_col2:
                    st.markdown("#### 🌡️ HTC Degradation")
                    htc_chart = alt.Chart(df_logs).mark_line(point=True, color='orange').encode(x='Date:T', y=alt.Y('Overall HTC:Q', scale=alt.Scale(zero=False)))
                    st.altair_chart(htc_chart + htc_chart.transform_regression('Date', 'Overall HTC').mark_line(color='black'), use_container_width=True)

if __name__ == "__main__":
    main()
