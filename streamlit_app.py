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
# 1. CORE ENGINE & STATE INITIALIZATION
# ==========================================
if 'daily_logs' not in st.session_state:
    st.session_state.daily_logs = pd.DataFrame(columns=[
        "Date", "Gross Prod (m3/h)", "Desal (m3/h)", "Steam (TPH)", "SW Feed (m3/h)", "GOR", "Overall HTC", "Residual"
    ])

# Central "Single Source of Truth" Dictionary
if 'vars' not in st.session_state:
    st.session_state.vars = {
        'steam': 73.0, 'desal': 740.0, 'gross': 790.0,
        'sw_upper': 775.0, 'sw_total': 2100.0, 'brine_ret': 1250.0,
        'sw_in_t': 30.0, 'brine_out_t': 41.0, 'stm_in_t': 179.0, 'vap_out_t': 70.0,
        'mra_press': 240.0, 'mra_t1': 69.5, 'mra_bt1': 66.5,
        'f_ph': 8.14, 'f_turb': 3.2, 'f_tss': 6.5, 'f_tds': 41000.0,
        'f_alk': 170.0, 'f_ca': 1040.0, 'f_cl': 21500.0, 'f_so4': 3150.0,
        'p_ph': 6.5, 'p_cond': 4.6, 'p_tds': 2.5, 'p_iron': 0.05,
        'p_cl': 0.0, 'p_so4': 0.0
    }

if 'effect_df' not in st.session_state:
    effects = [f"Effect {i}" for i in range(1, 12)]
    st.session_state.effect_df = pd.DataFrame({
        "Effect ID": effects,
        "Vapor Temp (°C)": np.round(np.linspace(69.0, 42.0, 11), 1),
        "Brine Temp (°C)": np.round(np.linspace(66.3, 40.0, 11), 1)
    })

# Two-Way Sync Callback Function
def update_var(var_key, widget_key):
    st.session_state.vars[var_key] = st.session_state[widget_key]

# UI Helper Functions for automatic syncing
def synced_num_input(label, var_key, widget_key, **kwargs):
    return st.number_input(label, value=float(st.session_state.vars[var_key]), key=widget_key, on_change=update_var, args=(var_key, widget_key), **kwargs)

def synced_slider(label, var_key, widget_key, min_val, max_val, **kwargs):
    return st.slider(label, min_value=float(min_val), max_value=float(max_val), value=float(st.session_state.vars[var_key]), key=widget_key, on_change=update_var, args=(var_key, widget_key), **kwargs)

def render_synced_effect_table(prefix):
    df_edited = st.data_editor(st.session_state.effect_df, key=f"{prefix}_effect_df", use_container_width=True, hide_index=True)
    if not df_edited.equals(st.session_state.effect_df):
        st.session_state.effect_df = df_edited
        st.rerun()
    return df_edited

# ==========================================
# 2. CONSTANTS & BASELINES
# ==========================================
LATENT_HEAT_STEAM_KJ_KG = 2260.0 
MRA_COEF = {"Intercept": -13.9586, "Press_1st": 0.4697, "Temp_1st": 15.0401, "SW_Upper": 1.1517, "Brine_Temp_1st": -17.7986, "Brine_Flow": -0.3292, "LP_Steam": 1.8876, "Steam_Temp": 1.2511}
MRA_BASELINE = {"Press_1st": 240.0, "Temp_1st": 69.5, "SW_Upper": 775.0, "Brine_Temp_1st": 66.5, "Brine_Flow": 1250.0, "LP_Steam": 72.0, "Steam_Temp": 179.0}

WATER_SPECS = {
    "Feed": {"pH": {"lim": (7.5, 9.2), "var": "f_ph"}, "Turbidity (NTU)": {"lim": (0.0, 5.0), "var": "f_turb"}, "TSS (ppm)": {"lim": (0.0, 10.0), "var": "f_tss"}, "TDS (ppm)": {"lim": (0.0, 42000.0), "var": "f_tds"}, "Total Alkalinity": {"lim": (160.0, 190.0), "var": "f_alk"}, "Calcium Hardness": {"lim": (950.0, 1100.0), "var": "f_ca"}, "Chlorides": {"lim": (21000.0, 22000.0), "var": "f_cl"}, "Sulphate": {"lim": (3050.0, 3250.0), "var": "f_so4"}},
    "Product": {"pH": {"lim": (5.5, 7.0), "var": "p_ph"}, "Conductivity (μs/cm)": {"lim": (0.0, 15.0), "var": "p_cond"}, "TDS (ppm)": {"lim": (0.0, 10.0), "var": "p_tds"}, "Total Iron": {"lim": (0.0, 0.1), "var": "p_iron"}, "Chlorides": {"lim": (0.0, 5.0), "var": "p_cl"}, "Sulphate": {"lim": (0.0, 1.0), "var": "p_so4"}}
}

# ==========================================
# 3. GLOBAL CALCULATIONS (The "Brain")
# ==========================================
v = st.session_state.vars

# Ops & KPIs
ops_data = {'Steam': v['steam'], 'Desal': v['desal'], 'Gross Prod': v['gross'], 'SW Upper': v['sw_upper'], 'SW Total': v['sw_total'], 'Brine Return': v['brine_ret'], 'SW In': v['sw_in_t'], 'Brine Out': v['brine_out_t'], 'Stm In': v['stm_in_t'], 'Vap Out': v['vap_out_t']}
ops_data['GOR'] = ops_data['Gross Prod'] / ops_data['Steam'] if ops_data['Steam'] > 0 else 0
heat_load_kw = ((ops_data['Steam'] * 1000) / 3600) * LATENT_HEAT_STEAM_KJ_KG
ops_data['STEC'] = heat_load_kw / ops_data['Desal'] if ops_data['Desal'] > 0 else 0
ops_data['Recovery'] = (ops_data['Gross Prod'] / ops_data['SW Total']) * 100 if ops_data['SW Total'] > 0 else 0
ops_data['Conversion'] = ops_data['Desal'] / ops_data['SW Total'] if ops_data['SW Total'] > 0 else 0
ops_data['Economy'] = ops_data['Steam'] / ops_data['Desal'] if ops_data['Desal'] > 0 else 0

# HTC Math
area_m2 = 1757.49
dt1 = ops_data['Stm In'] - ops_data['Brine Out']
dt2 = ops_data['Vap Out'] - ops_data['SW In']
ops_data['LMTD'], ops_data['HTC'], ops_data['Fouling'], ops_data['Q_act'] = 0, 0, 0, 0
if dt1 > 0 and dt2 > 0 and dt1 != dt2:
    ops_data['LMTD'] = (dt1 - dt2) / np.log(dt1 / dt2)
    ops_data['Q_act'] = ops_data['SW Total'] * (ops_data['Brine Out'] - ops_data['SW In']) * 0.930
    ops_data['HTC'] = (ops_data['Q_act'] / (area_m2 * ops_data['LMTD'])) * 1000 if ops_data['LMTD'] > 0 else 0
    ops_data['Fouling'] = 1 / ops_data['HTC'] if ops_data['HTC'] > 0 else 0

# MRA Math
mra_data = {}
mra_data['Predicted'] = (MRA_COEF["Intercept"] + (MRA_COEF["Press_1st"] * v['mra_press']) + (MRA_COEF["Temp_1st"] * v['mra_t1']) + (MRA_COEF["SW_Upper"] * v['sw_upper']) + (MRA_COEF["Brine_Temp_1st"] * v['mra_bt1']) + (MRA_COEF["Brine_Flow"] * v['brine_ret']) + (MRA_COEF["LP_Steam"] * v['steam']) + (MRA_COEF["Steam_Temp"] * v['stm_in_t']))
mra_data['Actual'] = ops_data['Gross Prod']
mra_data['Residual'] = mra_data['Actual'] - mra_data['Predicted']

var_data = []
for name, key, live_val in [("1st Effect Press", "Press_1st", v['mra_press']), ("1st Effect Temp", "Temp_1st", v['mra_t1']), ("Sea Water Upper", "SW_Upper", v['sw_upper']), ("1st Brine Temp", "Brine_Temp_1st", v['mra_bt1']), ("Brine Flow", "Brine_Flow", v['brine_ret']), ("LP Steam", "LP_Steam", v['steam']), ("Steam Temp", "Steam_Temp", v['stm_in_t'])]:
    dev = live_val - MRA_BASELINE[key]
    var_data.append([name, MRA_BASELINE[key], live_val, dev, MRA_COEF[key], dev * MRA_COEF[key]])
mra_data['Variance_DF'] = pd.DataFrame(var_data, columns=["Parameter", "Baseline", "Live Input", "Deviation", "Regression Weight", "Impact (TPH)"])

# Water Status Math
water_data = {'Feed': {}, 'Product': {}}
for cat in ['Feed', 'Product']:
    for param, details in WATER_SPECS[cat].items():
        val = v[details['var']]
        status = "✅ Pass" if details['lim'][0] <= val <= details['lim'][1] else "🚨 Fail"
        water_data[cat][param] = {'min': details['lim'][0], 'max': details['lim'][1], 'val': val, 'status': status}

# ==========================================
# 4. REPORT GENERATOR DOCX
# ==========================================
def generate_comprehensive_report(date, ops, effect_df, w_data, mra):
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
    t_eff = doc.add_table(rows=1, cols=4)
    t_eff.style = 'Table Grid'
    for i, h in enumerate(['Effect ID', 'Vapor (°C)', 'Brine (°C)', 'ΔT (°C)']): t_eff.rows[0].cells[i].text = h
    for idx, row in effect_df.iterrows():
        rc = t_eff.add_row().cells
        rc[0].text, rc[1].text, rc[2].text, dt_val = str(row['Effect ID']), f"{row['Vapor Temp (°C)']:.2f}", f"{row['Brine Temp (°C)']:.2f}", row['ΔT (°C)']
        rc[3].text = f"{dt_val:.2f}"
        if dt_val > 2.0: rc[3].paragraphs[0].runs[0].font.color.rgb = RGBColor(255, 0, 0)

    doc.add_heading('4. Water Quality', level=1)
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

# ==========================================
# 5. UI RENDERING
# ==========================================
def main():
    st.sidebar.markdown("### 🔹 CHEMBOND CHEMICALS LTD.") 
    st.sidebar.divider()
    log_date = st.sidebar.date_input("Date", datetime.date.today())
    st.sidebar.caption("Surface Area (m²) fixed at 1757.49 for MED-4.")
    
    st.title("🏭 Reliance MED-4 Management Suite")
    
    # 6 TABS NOW!
    tabs = st.tabs(["📥 0. Central Inputs", "🌊 1. Flow KPIs", "🔥 2. Thermo & HTC", "🧪 3. Quality", "🧠 4. MRA", "📂 5. Reporting"])

    # --- TAB 0: THE MASTER INPUT FORM ---
    with tabs[0]:
        st.subheader("Central Data Entry Panel")
        st.markdown("This acts as the master SCADA interface. Any data entered here will instantly synchronize and recalculate metrics across all analytical tabs.")
        
        with st.expander("1. Hydraulics & Mass Balance", expanded=True):
            c1, c2, c3 = st.columns(3)
            with c1:
                synced_num_input("LP Steam (TPH)", "steam", "in_steam")
                synced_num_input("Sea Water Upper (m³/h)", "sw_upper", "in_sw_up")
            with c2:
                synced_num_input("Desal Production (m³/h)", "desal", "in_desal")
                synced_num_input("Total SW Feed (m³/h)", "sw_total", "in_sw_tot")
            with c3:
                synced_num_input("Gross Production (m³/h)", "gross", "in_gross")
                synced_num_input("Brine Water Return (m³/h)", "brine_ret", "in_brine")

        with st.expander("2. Thermodynamics & Plant Temperatures", expanded=False):
            t1, t2, t3, t4 = st.columns(4)
            with t1: 
                synced_num_input("SW Inlet Temp (°C)", "sw_in_t", "in_sw_in")
                synced_num_input("1st Effect Press (mbar)", "mra_press", "in_press")
            with t2: 
                synced_num_input("Brine Outlet Temp (°C)", "brine_out_t", "in_brine_out")
                synced_num_input("1st Effect Temp (°C)", "mra_t1", "in_t1")
            with t3: 
                synced_num_input("LP Steam Inlet Temp (°C)", "stm_in_t", "in_stm_in")
                synced_num_input("1st Brine Temp (°C)", "mra_bt1", "in_bt1")
            with t4: synced_num_input("Vapour Outlet Temp (°C)", "vap_out_t", "in_vap_out")

        with st.expander("3. Effect-wise Cascade (Temperatures)", expanded=False):
            st.caption("Paste the 11 temperatures directly from Excel.")
            render_synced_effect_table("in")

        with st.expander("4. Laboratory Water Analysis", expanded=False):
            w_col1, w_col2 = st.columns(2)
            with w_col1:
                st.markdown("**Feed Water**")
                for p, d in WATER_SPECS["Feed"].items(): synced_num_input(f"{p}", d['var'], f"in_{d['var']}")
            with w_col2:
                st.markdown("**Desal Product**")
                for p, d in WATER_SPECS["Product"].items(): synced_num_input(f"{p}", d['var'], f"in_{d['var']}")

    # --- TAB 1: FLOW KPIs ---
    with tabs[1]:
        st.subheader("Mass Balance & KPI Dashboard")
        c1, c2, c3 = st.columns(3)
        with c1:
            synced_num_input("LP Steam (TPH)", "steam", "t1_steam")
            synced_num_input("Sea Water Upper (m³/h)", "sw_upper", "t1_sw_up")
        with c2:
            synced_num_input("Desal Production (m³/h)", "desal", "t1_desal")
            synced_num_input("Total SW Feed (m³/h)", "sw_total", "t1_sw_tot")
        with c3:
            synced_num_input("Gross Production (m³/h)", "gross", "t1_gross")
            synced_num_input("Brine Water Return (m³/h)", "brine_ret", "t1_brine")
            
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
        with h1: synced_num_input("SW Inlet Temp (°C)", "sw_in_t", "t2_sw_in")
        with h2: synced_num_input("Brine Outlet Temp (°C)", "brine_out_t", "t2_brine_out")
        with h3: synced_num_input("Steam Inlet Temp (°C)", "stm_in_t", "t2_stm_in")
        with h4: synced_num_input("Vapour Outlet Temp (°C)", "vap_out_t", "t2_vap_out")
            
        if ops_data['LMTD'] > 0:
            r1, r2, r3, r4 = st.columns(4)
            r1.metric("LMTD", f"{ops_data['LMTD']:.2f} °C")
            r2.metric("Plant Q (Actual)", f"{ops_data['Q_act']:,.0f} Kcal/hr°C")
            r3.metric("Overall HTC (U)", f"{ops_data['HTC']:.2f} W/m²K")
            r4.metric("Fouling Factor (1/U)", f"{ops_data['Fouling']:.6f}")
        else: st.error("Invalid temperatures for LMTD.")

        st.divider()
        st.subheader("11-Effect Scaling Profiler")
        
        col_t, col_g = st.columns([1, 2])
        with col_t:
            effect_df = render_synced_effect_table("t2")
            
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
        w_col1, w_col2 = st.columns(2)
        with w_col1:
            st.markdown("### 🌊 Feed Sea Water")
            for param, d in WATER_SPECS["Feed"].items():
                c_in, c_chk = st.columns([2, 2])
                with c_in: synced_num_input(f"{param} ({d['lim'][0]}-{d['lim'][1]})", d['var'], f"t3_{d['var']}")
                c_chk.markdown(f"<div style='margin-top:30px'>{water_data['Feed'][param]['status']}</div>", unsafe_allow_html=True)
        with w_col2:
            st.markdown("### 🚰 Desal Product")
            for param, d in WATER_SPECS["Product"].items():
                c_in, c_chk = st.columns([2, 2])
                with c_in: synced_num_input(f"{param} ({d['lim'][0]}-{d['lim'][1]})", d['var'], f"t3_{d['var']}")
                c_chk.markdown(f"<div style='margin-top:30px'>{water_data['Product'][param]['status']}</div>", unsafe_allow_html=True)

    # --- TAB 4: MRA NORMALIZATION ---
    with tabs[4]:
        st.subheader("MRA Fouling Defense")
        controls_col, calc_col = st.columns([1, 2])
        with controls_col:
            synced_slider("1st Effect Press (mbar)", "mra_press", "t4_press", 200, 260)
            synced_slider("1st Effect Temp (°C)", "mra_t1", "t4_t1", 60, 75)
            synced_slider("Sea Water Upper (m³/h)", "sw_upper", "t4_sw_up", 400, 1000)
            synced_slider("1st Brine Temp (°C)", "mra_bt1", "t4_bt1", 60, 75)
            synced_slider("Brine Flow (m³/h)", "brine_ret", "t4_bflow", 1000, 1600)
            synced_slider("LP Steam (TPH)", "steam", "t4_steam", 50, 100)
            synced_slider("Steam Temp (°C)", "stm_in_t", "t4_stm_t", 160, 190)

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
                if st.button("📄 Export Document (.docx)", type="primary", use_container_width=True):
                    word_file = generate_comprehensive_report(log_date, ops_data, effect_df, water_data, mra_data)
                    st.download_button("📥 Click to Download Document", data=word_file, file_name=f"MED4_Daily_{log_date}.docx", mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document")

        with rep_tabs[1]:
            if not st.session_state.daily_logs.empty:
                df_logs = st.session_state.daily_logs.copy()
                df_logs['Date'] = pd.to_datetime(df_logs['Date'])
                month_data = df_logs[(df_logs['Date'].dt.month == log_date.month) & (df_logs['Date'].dt.year == log_date.year)].copy()
                if not month_data.empty:
                    st.markdown("#### 📉 Gross Production vs. Target")
                    line_chart = alt.Chart(month_data).mark_line(point=True).encode(x='Date:T', y=alt.Y('Gross Prod (m3/h):Q', scale=alt.Scale(domain=[500, 1100])))
                    st.altair_chart(line_chart + alt.Chart(pd.DataFrame({'y': [1000]})).mark_rule(color='green').encode(y='y'), use_container_width=True)
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
