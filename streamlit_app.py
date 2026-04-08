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

# --- INITIALIZE SESSION STATE ---
if 'daily_logs' not in st.session_state:
    st.session_state.daily_logs = pd.DataFrame(columns=[
        "Date", "Gross Prod (m3/h)", "Desal (m3/h)", "Steam (TPH)", "SW Feed (m3/h)", "GOR", "Overall HTC", "Residual"
    ])

# --- CONSTANTS & 2026 MRA COEFFICIENTS ---
LATENT_HEAT_STEAM_KJ_KG = 2260.0 

MRA_COEF = {
    "Intercept": -13.9586, "Press_1st": 0.4697, "Temp_1st": 15.0401, 
    "SW_Upper": 1.1517, "Brine_Temp_1st": -17.7986, "Brine_Flow": -0.3292, 
    "LP_Steam": 1.8876, "Steam_Temp": 1.2511
}

MRA_BASELINE = {
    "Press_1st": 240.0, "Temp_1st": 69.5, "SW_Upper": 775.0, 
    "Brine_Temp_1st": 66.5, "Brine_Flow": 1250.0, "LP_Steam": 72.0, 
    "Steam_Temp": 179.0
}

WATER_SPECS = {
    "Feed": {
        "pH": {"limits": (7.5, 9.2), "default": 8.14},
        "Turbidity (NTU)": {"limits": (0.0, 5.0), "default": 3.2},
        "TSS (ppm)": {"limits": (0.0, 10.0), "default": 6.5},
        "TDS (ppm)": {"limits": (0.0, 42000.0), "default": 41000.0},
        "Total Alkalinity": {"limits": (160.0, 190.0), "default": 170.0},
        "Calcium Hardness": {"limits": (950.0, 1100.0), "default": 1040.0},
        "Chlorides": {"limits": (21000.0, 22000.0), "default": 21500.0},
        "Sulphate": {"limits": (3050.0, 3250.0), "default": 3150.0}
    },
    "Product": {
        "pH": {"limits": (5.5, 7.0), "default": 6.5},
        "Conductivity (μs/cm)": {"limits": (0.0, 15.0), "default": 4.6},
        "TDS (ppm)": {"limits": (0.0, 10.0), "default": 2.5},
        "Total Iron": {"limits": (0.0, 0.1), "default": 0.05},
        "Chlorides": {"limits": (0.0, 5.0), "default": 0.0},
        "Sulphate": {"limits": (0.0, 1.0), "default": 0.0}
    }
}

# --- RELIANCE-GRADE DOCUMENT GENERATOR ---
def generate_comprehensive_report(date, ops_data, effect_df, water_data, mra_data):
    doc = Document()
    
    title = doc.add_heading('MED-4 Daily Operational & Performance Report', 0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    
    p = doc.add_paragraph()
    p.add_run('Prepared by: ').bold = True
    p.add_run('Chembond Chemicals Ltd.\n')
    p.add_run('Client: ').bold = True
    p.add_run('Reliance Industries Limited (RIL)\n')
    p.add_run('Date: ').bold = True
    p.add_run(str(date))
    p.alignment = WD_ALIGN_PARAGRAPH.RIGHT

    doc.add_heading('1. Executive Summary', level=1)
    status_text = f"On {date}, the MED-4 unit achieved a Gross Production of {ops_data['Gross Prod']} m³/h and a Gain Output Ratio (GOR) of {ops_data['GOR']:.2f}:1. The Specific Thermal Energy Consumption (STEC) was {ops_data['STEC']:.2f} kWh/ton with a system recovery of {ops_data['Recovery']:.1f}%."
    doc.add_paragraph(status_text)

    doc.add_heading('2. Operational Data Summary', level=1)
    table_ops = doc.add_table(rows=1, cols=4)
    table_ops.style = 'Table Grid'
    hdr_cells = table_ops.rows[0].cells
    headers = ['Parameter', 'UOM', 'Design', 'Actual']
    for i, h in enumerate(headers): 
        hdr_cells[i].text = h
        hdr_cells[i].paragraphs[0].runs[0].bold = True
        
    ops_rows = [
        ['Total Sea Water Feed', 'm³/h', '2400', str(ops_data['SW Total'])],
        ['Sea Water Upper (1st Effect)', 'm³/h', '580', str(ops_data['SW Upper'])],
        ['Brine Water Return', 'm³/h', '1400', str(ops_data['Brine Return'])],
        ['Desal Production (Net)', 'm³/h', '1000', str(ops_data['Desal'])],
        ['Gross Production', 'm³/h', '-', str(ops_data['Gross Prod'])],
        ['LP Steam Consumption', 'TPH', '92 - 94.5', str(ops_data['Steam'])],
        ['System Recovery', '%', '40.0', f"{ops_data['Recovery']:.2f}"],
        ['Gain Output Ratio (GOR)', 'Ratio', '10.5 : 1', f"{ops_data['GOR']:.2f} : 1"],
        ['Steam Economy', 'Ratio', '-', f"{ops_data['Economy']:.4f}"]
    ]
    for row in ops_rows:
        row_cells = table_ops.add_row().cells
        for i, val in enumerate(row): row_cells[i].text = val

    doc.add_heading('3. Effect-wise Thermodynamic Profile', level=1)
    doc.add_paragraph(f"Overall Plant LMTD: {ops_data['LMTD']:.2f} °C | Overall HTC (U): {ops_data['HTC']:.2f} W/m²K | Fouling Factor: {ops_data['Fouling']:.6f}")
    
    table_eff = doc.add_table(rows=1, cols=4)
    table_eff.style = 'Table Grid'
    hdr_cells = table_eff.rows[0].cells
    for i, h in enumerate(['Effect ID', 'Vapor Temp (°C)', 'Brine Temp (°C)', 'ΔT (Vapor - Brine)']):
        hdr_cells[i].text = h
        hdr_cells[i].paragraphs[0].runs[0].bold = True
        
    for idx, row in effect_df.iterrows():
        row_cells = table_eff.add_row().cells
        row_cells[0].text = str(row['Effect ID'])
        row_cells[1].text = f"{row['Vapor Temp (°C)']:.2f}"
        row_cells[2].text = f"{row['Brine Temp (°C)']:.2f}"
        dt_val = row['ΔT (°C)']
        row_cells[3].text = f"{dt_val:.2f}"
        if dt_val > 2.0:
            row_cells[3].paragraphs[0].runs[0].font.color.rgb = RGBColor(255, 0, 0)
            row_cells[3].paragraphs[0].runs[0].bold = True

    doc.add_heading('4. Water Quality Compliance', level=1)
    table_wq = doc.add_table(rows=1, cols=4)
    table_wq.style = 'Table Grid'
    hdr_cells = table_wq.rows[0].cells
    for i, h in enumerate(['Parameter', 'Stream', 'Limit/Spec', 'Actual']):
        hdr_cells[i].text = h
        hdr_cells[i].paragraphs[0].runs[0].bold = True
        
    for param, data in water_data['Feed'].items():
        row_cells = table_wq.add_row().cells
        row_cells[0].text = str(param)
        row_cells[1].text = 'Sea Water Feed'
        row_cells[2].text = f"{data['min']} - {data['max']}"
        row_cells[3].text = str(data['val'])
        
    for param, data in water_data['Product'].items():
        row_cells = table_wq.add_row().cells
        row_cells[0].text = str(param)
        row_cells[1].text = 'Desal Product'
        row_cells[2].text = f"{data['min']} - {data['max']}"
        row_cells[3].text = str(data['val'])

    doc.add_heading('5. MRA Fouling Indicator & Root Cause Variance', level=1)
    doc.add_paragraph(f"Actual Gross: {mra_data['Actual']:.1f} m³/h | MRA Predicted: {mra_data['Predicted']:.1f} m³/h | Residual: {mra_data['Residual']:.1f} m³/h")
    
    table_mra = doc.add_table(rows=1, cols=5)
    table_mra.style = 'Table Grid'
    hdr_cells = table_mra.rows[0].cells
    for i, h in enumerate(['Parameter', 'Clean Baseline', 'Live Input', 'Deviation', 'Production Impact']):
        hdr_cells[i].text = h
        hdr_cells[i].paragraphs[0].runs[0].bold = True
        
    for idx, row in mra_data['Variance_DF'].iterrows():
        row_cells = table_mra.add_row().cells
        row_cells[0].text = str(row['Parameter'])
        row_cells[1].text = f"{row['Baseline']:.1f}"
        row_cells[2].text = f"{row['Live Input']:.1f}"
        row_cells[3].text = f"{row['Deviation']:+.1f}"
        row_cells[4].text = f"{row['Impact (TPH)']:+.1f}"

    bio = io.BytesIO()
    doc.save(bio)
    return bio.getvalue()

def main():
    st.sidebar.markdown("### 🔹 CHEMBOND CHEMICALS LTD.") 
    st.sidebar.divider()
    
    st.sidebar.header("📅 Daily Setup")
    log_date = st.sidebar.date_input("Date", datetime.date.today())
    area_m2 = st.sidebar.number_input("Overall Surface Area (m²)", value=1757.49)
    
    st.title("🏭 Reliance MED-4 Management Suite")
    
    tabs = st.tabs(["🌊 1. SCADA Flow Data", "🔥 2. Thermo & HTC", "🧪 3. Water Analysis", "🧠 4. MRA Normalization", "📂 5. Enterprise Reporting"])

    ops_data = {}
    water_data = {'Feed': {}, 'Product': {}}
    mra_data = {}

    # ==========================================
    # TAB 1: MANUAL SCADA INPUTS & STEC
    # ==========================================
    with tabs[0]:
        st.subheader("Daily Mass Balance (Raw SCADA Inputs)")
        
        c1, c2, c3 = st.columns(3)
        ops_data['Steam'] = c1.number_input("LP Steam (TPH)", value=73.0)
        ops_data['Desal'] = c2.number_input("Desal Production (m³/h)", value=740.0)
        ops_data['Gross Prod'] = c3.number_input("Gross Production (m³/h)", value=790.0)
        
        c4, c5, c6 = st.columns(3)
        ops_data['SW Upper'] = c4.number_input("Sea Water Upper (Flow to 1st Effect)", value=775.0)
        ops_data['SW Total'] = c5.number_input("Total Sea Water Feed (m³/h)", value=2100.0)
        ops_data['Brine Return'] = c6.number_input("Brine Water Return (m³/h)", value=1250.0)

        st.divider()
        st.subheader("📊 Executive Plant KPIs")
        
        ops_data['GOR'] = ops_data['Gross Prod'] / ops_data['Steam'] if ops_data['Steam'] > 0 else 0
        heat_load_kw = ((ops_data['Steam'] * 1000) / 3600) * LATENT_HEAT_STEAM_KJ_KG
        ops_data['STEC'] = heat_load_kw / ops_data['Desal'] if ops_data['Desal'] > 0 else 0
        ops_data['Recovery'] = (ops_data['Gross Prod'] / ops_data['SW Total']) * 100 if ops_data['SW Total'] > 0 else 0
        ops_data['Conversion'] = ops_data['Desal'] / ops_data['SW Total'] if ops_data['SW Total'] > 0 else 0
        ops_data['Economy'] = ops_data['Steam'] / ops_data['Desal'] if ops_data['Desal'] > 0 else 0
        
        kpi1, kpi2, kpi3, kpi4, kpi5 = st.columns(5)
        kpi1.metric("GOR", f"{ops_data['GOR']:.2f}:1")
        kpi2.metric("Steam Economy", f"{ops_data['Economy']:.4f}")
        kpi3.metric("System Recovery", f"{ops_data['Recovery']:.1f} %")
        kpi4.metric("Conversion Ratio", f"{ops_data['Conversion']:.3f}")
        kpi5.metric("STEC", f"{ops_data['STEC']:.1f} kWh/t")

    # ==========================================
    # TAB 2: OVERALL HTC & VISUAL GRAPH
    # ==========================================
    with tabs[1]:
        st.subheader("1. Overall Plant LMTD & Fouling Factor")
        h1, h2, h3, h4 = st.columns(4)
        sw_in_t = h1.number_input("Sea Water Inlet Temp (°C)", value=30.0)
        brine_out_t = h2.number_input("Brine Outlet Temp (°C)", value=41.0)
        steam_in_t = h3.number_input("LP Steam Inlet Temp (°C)", value=179.0)
        vapor_out_t = h4.number_input("Vapour Outlet Temp (°C)", value=70.0)
        
        dt1 = steam_in_t - brine_out_t
        dt2 = vapor_out_t - sw_in_t
        ops_data['HTC'] = 0
        ops_data['LMTD'] = 0
        ops_data['Fouling'] = 0
        
        if dt1 > 0 and dt2 > 0 and dt1 != dt2:
            ops_data['LMTD'] = (dt1 - dt2) / np.log(dt1 / dt2)
            q_actual = ops_data['SW Total'] * (brine_out_t - sw_in_t) * 0.930
            ops_data['HTC'] = (q_actual / (area_m2 * ops_data['LMTD'])) * 1000 if ops_data['LMTD'] > 0 else 0
            ops_data['Fouling'] = 1 / ops_data['HTC'] if ops_data['HTC'] > 0 else 0
            
            r1, r2, r3, r4 = st.columns(4)
            r1.metric("LMTD", f"{ops_data['LMTD']:.2f} °C")
            r2.metric("Plant Q (Actual)", f"{q_actual:,.0f} Kcal/hr°C")
            r3.metric("Overall HTC (U)", f"{ops_data['HTC']:.2f} W/m²K")
            r4.metric("Fouling Factor (1/U)", f"{ops_data['Fouling']:.6f}")

        st.divider()
        st.subheader("2. 11-Effect Temperature & Scaling Profiler")
        effects = [f"Effect {i}" for i in range(1, 12)]
        df_input = pd.DataFrame({
            "Effect ID": effects,
            "Vapor Temp (°C)": np.round(np.linspace(69.0, 42.0, 11), 1),
            "Brine Temp (°C)": np.round(np.linspace(66.3, 40.0, 11), 1)
        })
        
        edited_input = st.data_editor(df_input, use_container_width=True, hide_index=True)
        edited_input['ΔT (°C)'] = edited_input['Vapor Temp (°C)'] - edited_input['Brine Temp (°C)']
        effect_df = edited_input
        
        warning_triggered = False
        for index, row in edited_input.iterrows():
            if row['ΔT (°C)'] > 2.0:
                st.error(f"🚨 **{row['Effect ID']} ALERT:** ΔT is {row['ΔT (°C)']:.2f}°C (Exceeds 2.0°C Limit)")
                warning_triggered = True
        
        st.markdown("### 📈 Effect-wise ΔT Profile vs. Design Limit")
        edited_input['Effect ID'] = pd.Categorical(edited_input['Effect ID'], categories=effects, ordered=True)
        base_chart = alt.Chart(edited_input).encode(x=alt.X('Effect ID', sort=effects, title=None))
        bar_chart = base_chart.mark_bar(color='#1f77b4', cornerRadiusTopLeft=3, cornerRadiusTopRight=3).encode(y=alt.Y('ΔT (°C)', title='Delta T (°C)'))
        limit_line = alt.Chart(pd.DataFrame({'y': [2.0]})).mark_rule(color='red', strokeDash=[5, 5], strokeWidth=2).encode(y='y')
        st.altair_chart(bar_chart + limit_line, use_container_width=True)

    # ==========================================
    # TAB 3: WATER ANALYSIS COMPLIANCE
    # ==========================================
    with tabs[2]:
        st.subheader("Laboratory Analysis vs RFQ Limits")
        w_col1, w_col2 = st.columns(2)
        with w_col1:
            st.markdown("### 🌊 Feed Sea Water")
            for param, data in WATER_SPECS["Feed"].items():
                col_in, col_chk = st.columns([2, 2])
                val = col_in.number_input(f"{param} ({data['limits'][0]}-{data['limits'][1]})", value=data['default'], key=f"f_{param}")
                status = "✅ Pass" if data['limits'][0] <= val <= data['limits'][1] else "🚨 Fail"
                col_chk.markdown(f"<div style='margin-top:30px'>{status}</div>", unsafe_allow_html=True)
                water_data['Feed'][param] = {'min': data['limits'][0], 'max': data['limits'][1], 'val': val}
            
        with w_col2:
            st.markdown("### 🚰 Desal Product")
            for param, data in WATER_SPECS["Product"].items():
                col_in, col_chk = st.columns([2, 2])
                val = col_in.number_input(f"{param} ({data['limits'][0]}-{data['limits'][1]})", value=data['default'], key=f"p_{param}")
                status = "✅ Pass" if data['limits'][0] <= val <= data['limits'][1] else "🚨 Fail"
                col_chk.markdown(f"<div style='margin-top:30px'>{status}</div>", unsafe_allow_html=True)
                water_data['Product'][param] = {'min': data['limits'][0], 'max': data['limits'][1], 'val': val}

    # ==========================================
    # TAB 4: MRA & RESIDUAL ANALYSIS 
    # ==========================================
    with tabs[3]:
        st.subheader("Performance Normalization (2026 Baseline)")
        controls_col, calc_col = st.columns([1, 2])
        with controls_col:
            p_press = st.slider("1st Effect Press (mbar)", 200.0, 260.0, 240.0)
            p_t1 = st.slider("1st Effect Temp (°C)", 60.0, 75.0, 69.5)
            p_sw_up = st.slider("Sea Water Upper (m³/h)", 400.0, 1000.0, float(ops_data['SW Upper']))
            p_bt1 = st.slider("1st Brine Temp (°C)", 60.0, 75.0, 66.5)
            p_bflow = st.slider("Brine Flow (m³/h)", 1000.0, 1600.0, float(ops_data['Brine Return']))
            p_stm = st.slider("LP Steam (TPH)", 50.0, 100.0, float(ops_data['Steam']))
            p_stm_t = st.slider("Steam Temp (°C)", 160.0, 190.0, 179.0)

        with calc_col:
            mra_data['Predicted'] = (
                MRA_COEF["Intercept"] + (MRA_COEF["Press_1st"] * p_press) + (MRA_COEF["Temp_1st"] * p_t1) +
                (MRA_COEF["SW_Upper"] * p_sw_up) + (MRA_COEF["Brine_Temp_1st"] * p_bt1) +
                (MRA_COEF["Brine_Flow"] * p_bflow) + (MRA_COEF["LP_Steam"] * p_stm) + (MRA_COEF["Steam_Temp"] * p_stm_t)
            )
            mra_data['Actual'] = ops_data['Gross Prod']
            mra_data['Residual'] = mra_data['Actual'] - mra_data['Predicted']
            
            k1, k2, k3 = st.columns(3)
            k1.metric("Actual Gross SCADA", f"{mra_data['Actual']:.1f} m³/h")
            k2.metric("MRA Predicted", f"{mra_data['Predicted']:.1f} m³/h")
            if mra_data['Residual'] < -15.0: k3.error(f"Residual: {mra_data['Residual']:.1f} (FOULING)")
            elif mra_data['Residual'] > 15.0: k3.success(f"Residual: {mra_data['Residual']:.1f} (CLEAN)")
            else: k3.info(f"Residual: {mra_data['Residual']:.1f} (NORMAL)")
                
            var_data = []
            for name, key, live_val in [("1st Effect Press", "Press_1st", p_press), ("1st Effect Temp", "Temp_1st", p_t1), ("Sea Water Upper", "SW_Upper", p_sw_up), ("1st Brine Temp", "Brine_Temp_1st", p_bt1), ("Brine Flow", "Brine_Flow", p_bflow), ("LP Steam", "LP_Steam", p_stm), ("Steam Temp", "Steam_Temp", p_stm_t)]:
                base = MRA_BASELINE[key]
                dev = live_val - base
                var_data.append([name, base, live_val, dev, MRA_COEF[key], dev * MRA_COEF[key]])
            
            mra_data['Variance_DF'] = pd.DataFrame(var_data, columns=["Parameter", "Baseline", "Live Input", "Deviation", "Regression Weight", "Impact (TPH)"])

            st.markdown("### 📊 Parameter Variance Matrix")
            st.dataframe(mra_data['Variance_DF'].style.format({
                "Baseline": "{:.1f}", "Live Input": "{:.1f}", "Deviation": "{:+.1f}",
                "Regression Weight": "{:.3f}", "Impact (TPH)": "{:+.1f}"
            }), use_container_width=True, hide_index=True)

    # ==========================================
    # TAB 5: ENTERPRISE REPORTING SUITE
    # ==========================================
    with tabs[4]:
        st.subheader("Performance Intelligence & Reporting")
        rep_tabs = st.tabs(["📅 Today's Report", "📆 Monthly Summary", "📊 Quarterly & Yearly"])
        
        with rep_tabs[0]:
            st.markdown("### 🔍 Data Verification & Database Commit")
            st.markdown("Verify the critical metrics below before authorizing a write to the Master Database.")
            
            # High-Visibility Preview
            col_p1, col_p2, col_p3, col_p4 = st.columns(4)
            col_p1.metric("Date", str(log_date))
            col_p2.metric("Gross Prod", f"{ops_data['Gross Prod']} m³/h")
            col_p3.metric("GOR", f"{ops_data['GOR']:.2f}")
            col_p4.metric("MRA Residual", f"{mra_data['Residual']:.1f}")
            
            st.divider()
            
            c_save, c_report = st.columns(2)
            with c_save:
                pwd_append = st.text_input("🔑 Authorization Password Required to Append", type="password", key="pwd_append")
                if st.button("💾 Append Today's Data to Database", use_container_width=True):
                    if pwd_append == "12345678":
                        new_log = pd.DataFrame({
                            "Date": [pd.to_datetime(log_date)], "Gross Prod (m3/h)": [ops_data['Gross Prod']],
                            "Desal (m3/h)": [ops_data['Desal']], "Steam (TPH)": [ops_data['Steam']],
                            "SW Feed (m3/h)": [ops_data['SW Total']], "GOR": [round(ops_data['GOR'], 2)], 
                            "Overall HTC": [round(ops_data['HTC'], 2)], "Residual": [round(mra_data['Residual'], 1)]
                        })
                        st.session_state.daily_logs = pd.concat([st.session_state.daily_logs, new_log], ignore_index=True)
                        st.success("✅ Successfully written to memory!")
                    else:
                        st.error("❌ Incorrect Password. Data not saved to Master Database.")
            
            with c_report:
                st.markdown("<br><br>", unsafe_allow_html=True) # Formatting alignment
                if st.button("📄 Export Comprehensive Daily Report (.docx)", type="primary", use_container_width=True):
                    word_file = generate_comprehensive_report(log_date, ops_data, effect_df, water_data, mra_data)
                    st.download_button("📥 Click Here to Download Document", data=word_file, file_name=f"MED4_Daily_Report_{log_date}.docx", mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document")

        with rep_tabs[1]:
            st.markdown("### Monthly OPR & Quality Summary")
            if not st.session_state.daily_logs.empty:
                df_logs = st.session_state.daily_logs.copy()
                df_logs['Date'] = pd.to_datetime(df_logs['Date'])
                current_month_data = df_logs[(df_logs['Date'].dt.month == log_date.month) & (df_logs['Date'].dt.year == log_date.year)]
                
                if not current_month_data.empty:
                    m1, m2, m3, m4 = st.columns(4)
                    m1.metric("Month Avg GOR", f"{current_month_data['GOR'].mean():.2f}")
                    m2.metric("Month Avg Gross", f"{current_month_data['Gross Prod (m3/h)'].mean():.0f}")
                    m3.metric("Month Avg HTC", f"{current_month_data['Overall HTC'].mean():.0f}")
                    m4.metric("Avg MRA Residual", f"{current_month_data['Residual'].mean():.1f}")
                else:
                    st.info(f"No data saved yet for {log_date.strftime('%B %Y')}.")
            else:
                st.info("The database is currently empty. Please save today's log in the 'Today' tab.")
                
            st.divider()
            st.markdown("#### Master Log Database")
            st.session_state.daily_logs = st.data_editor(st.session_state.daily_logs, num_rows="dynamic", use_container_width=True)
            
            st.divider()
            st.markdown("#### 📥 Secure Database Export")
            pwd_dl = st.text_input("🔑 Authorization Password Required to Download Master CSV", type="password", key="pwd_dl")
            
            if pwd_dl == "12345678":
                csv_export = st.session_state.daily_logs.to_csv(index=False).encode('utf-8')
                st.download_button("📥 Unlock and Download Master Log (CSV)", data=csv_export, file_name=f"MED4_Master_Database.csv", mime='text/csv')
            elif pwd_dl != "":
                st.error("❌ Incorrect Password.")

        with rep_tabs[2]:
            st.markdown("### Long-Term Asset Health")
            if not st.session_state.daily_logs.empty:
                df_logs = st.session_state.daily_logs.copy()
                df_logs['Date'] = pd.to_datetime(df_logs['Date'])
                df_logs['Quarter'] = df_logs['Date'].dt.to_period('Q')
                quarterly_avg = df_logs.groupby('Quarter')[['GOR', 'Residual', 'Overall HTC']].mean().reset_index()
                st.dataframe(quarterly_avg.style.format({"GOR": "{:.2f}", "Residual": "{:.1f}", "Overall HTC": "{:.0f}"}), use_container_width=True)
            else:
                st.info("Save more data to view quarterly trends.")

if __name__ == "__main__":
    main()
