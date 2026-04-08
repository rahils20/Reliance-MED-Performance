# requirements: pandas, numpy, python-docx
import streamlit as st
import pandas as pd
import numpy as np
import datetime
import io
from docx import Document
from docx.shared import Pt, Inches, RGBColor

st.set_page_config(page_title="Chembond | RIL MED-4 Management", layout="wide")

# --- INITIALIZE SESSION STATE ---
if 'daily_logs' not in st.session_state:
    st.session_state.daily_logs = pd.DataFrame(columns=[
        "Date", "Steam (TPH)", "Desal (m3/h)", "Gross Prod (m3/h)", "SW Feed (m3/h)", "GOR", "Avg HTC"
    ])

# --- CONSTANTS & MRA COEFFICIENTS ---
LATENT_HEAT_STEAM_KJ_KG = 2260.0 
LATENT_HEAT_VAPOR_KJ_KG = 2330.0 

# True Linear Regression Coefficients from MED-4 Tool
MRA_COEF = {
    "Intercept": -161.5637, "Press_1st": 0.6135, "Temp_1st": 3.6391, 
    "SW_Upper": 0.8111, "Brine_Temp_1st": -7.6638, "Brine_Flow": -0.2328, 
    "LP_Steam": 8.2539, "Steam_Temp": 2.1924, "Antiscalant": -7.0300
}

# Accurate operational baselines for the variance explainer (Based on clean design runs)
MRA_BASELINE = {
    "Press_1st": 230.8, "Temp_1st": 69.2, "SW_Upper": 584.8, 
    "Brine_Temp_1st": 66.4, "Brine_Flow": 1361.9, "LP_Steam": 75.2, 
    "Steam_Temp": 177.8, "Antiscalant": 5.2
}

# --- DOCUMENT GENERATOR ---
def generate_word_report(date, actual_gross, predicted_gross, residual, gor, stec, variance_df):
    doc = Document()
    
    # Title formatting
    title = doc.add_heading('MED-4 Daily Operational & Performance Report', 0)
    title.alignment = 1 # Center
    
    p = doc.add_paragraph()
    p.add_run('Prepared by: ').bold = True
    p.add_run('Chembond Chemicals Ltd.\n')
    p.add_run('Client: ').bold = True
    p.add_run('Reliance Industries Limited (RIL)\n')
    p.add_run('Date: ').bold = True
    p.add_run(str(date))
    
    doc.add_heading('1. Executive Summary', level=1)
    doc.add_paragraph(f"On {date}, the MED-4 unit operated continuously. The thermodynamic profile achieved a Gain Output Ratio (GOR) of {gor:.2f}:1 and a Specific Thermal Energy Consumption (STEC) of {stec:.2f} kWh/ton. Steam economy remained stable, indicating efficient energy utilization.")
    
    doc.add_heading('2. Multiple Regression Analysis (MRA) & Fouling Indicator', level=1)
    doc.add_paragraph("The MRA model utilizes 8 independent SCADA variables to normalize production against historical clean-plant baselines. By calculating the 'Residual' (the difference between actual and mathematically predicted gross production), we isolate the pure impact of scaling or fouling.")
    
    mra_p = doc.add_paragraph()
    mra_p.add_run(f"Actual Gross Production: {actual_gross:.1f} m³/h\n")
    mra_p.add_run(f"MRA Predicted Gross Production: {predicted_gross:.1f} m³/h\n")
    
    res_run = mra_p.add_run(f"Calculated Residual: {residual:.1f} m³/h\n")
    res_run.bold = True
    if residual < -15.0: res_run.font.color.rgb = RGBColor(255, 0, 0)
    
    if residual < -15.0:
        doc.add_paragraph("WARNING: A significant negative residual indicates potential thermal resistance (fouling) forming on the tube bundles. Antiscalant dosing adjustments or a scheduled acid wash should be evaluated.", style='BodyText')
    elif residual > 15.0:
        doc.add_paragraph("NOTE: Positive residual indicates the plant is over-performing the baseline model, showing excellent heat transfer efficiency.", style='BodyText')
    else:
        doc.add_paragraph("STATUS: The plant is operating perfectly within the normalized thermodynamic baseline. No heat transfer bottlenecks detected.", style='BodyText')
    
    doc.add_heading('3. Parameter Variance Impact Matrix', level=2)
    table = doc.add_table(rows=1, cols=6)
    table.style = 'Table Grid'
    hdr_cells = table.rows[0].cells
    headers = ['Parameter', 'Baseline', 'Live Input', 'Deviation', 'Regression Weight', 'Impact (TPH)']
    for i, h in enumerate(headers):
        hdr_cells[i].text = h
        hdr_cells[i].paragraphs[0].runs[0].bold = True
    
    for idx, row in variance_df.iterrows():
        row_cells = table.add_row().cells
        row_cells[0].text = str(row['Parameter'])
        row_cells[1].text = f"{row['Baseline']:.1f}"
        row_cells[2].text = f"{row['Live Input']:.1f}"
        row_cells[3].text = f"{row['Deviation']:.1f}"
        row_cells[4].text = f"{row['Regression Weight']:.3f}"
        row_cells[5].text = f"{row['Impact (TPH)']:.1f}"
        
    bio = io.BytesIO()
    doc.save(bio)
    return bio.getvalue()

def main():
    st.title("🏭 Reliance MED-4 Management Suite")
    st.caption("Developed by Chembond Chemicals Ltd.")
    
    st.sidebar.header("📅 Daily Setup")
    log_date = st.sidebar.date_input("Date", datetime.date.today())
    area_m2 = st.sidebar.number_input("Surface Area per Effect (m²)", value=1757.49, help="Calculates exact HTC matching Chembond logs")
    
    tabs = st.tabs(["🌊 1. Daily Inputs & STEC", "🔥 2. Thermo & HTC", "🧪 3. Water Analysis", "🧠 4. MRA & Residual Defense", "📂 5. Database & Report Generator"])

    # ==========================================
    # TAB 1: MANUAL INPUTS & STEC
    # ==========================================
    with tabs[0]:
        st.subheader("Daily Mass Balance (Manual Logging)")
        st.markdown("*(Calculations by subtraction have been disabled to ensure SCADA accuracy)*")
        
        st.markdown("#### Primary Production")
        c1, c2, c3, c4 = st.columns(4)
        steam = c1.number_input("LP Steam (TPH)", value=75.0)
        desal = c2.number_input("Desal Production (m³/h)", value=750.0)
        condensate = c3.number_input("Condensate Flow (m³/h)", value=120.0)
        gross_prod = c4.number_input("Gross Production (m³/h)", value=870.0, help="Target variable for MRA Prediction")
        
        st.markdown("#### Plant Hydraulics")
        c5, c6, c7, c8 = st.columns(4)
        sw_upper = c5.number_input("Sea Water Upper (m³/h)", value=585.0, help="Flow to 1st Effect - Crucial for MRA")
        sw_lower = c6.number_input("Sea Water Lower (m³/h)", value=1400.0)
        sw_total = c7.number_input("Total Sea Water Feed (m³/h)", value=2080.0)
        brine_return = c8.number_input("Brine Water Return (m³/h)", value=1360.0)

        st.divider()
        gor = desal / steam if steam > 0 else 0
        heat_load_kw = ((steam * 1000) / 3600) * LATENT_HEAT_STEAM_KJ_KG
        stec = heat_load_kw / desal if desal > 0 else 0
        
        k1, k2 = st.columns(2)
        with k1:
            st.latex(f"Q_{{input}} = \\frac{{{steam} \\times 1000}}{{3600}} \\times {LATENT_HEAT_STEAM_KJ_KG} = {heat_load_kw:,.0f} \\text{{ kW}}")
            st.metric("Gain Output Ratio (GOR)", f"{gor:.2f}:1")
        with k2:
            st.latex(f"\\text{{STEC}} = \\frac{{{heat_load_kw:,.0f}}}{{{desal}}} = {stec:,.1f} \\text{{ kWh/ton}}")

    # ==========================================
    # TAB 2: THERMODYNAMIC & HTC
    # ==========================================
    with tabs[1]:
        st.subheader("11-Effect Temperature Inputs")
        st.info("💡 **Pro-Tip for Operators:** Copy a column of 11 numbers directly from your Excel sheet and press `Ctrl + V` to paste them into the table below.")
        
        effects = [f"Effect {i}" for i in range(1, 12)]
        df_input = pd.DataFrame({
            "Effect ID": effects,
            "Vapor Temp (°C)": np.round(np.linspace(69.0, 42.0, 11), 1),
            "Brine Temp (°C)": np.round(np.linspace(66.3, 40.0, 11), 1)
        })
        
        edited_input = st.data_editor(df_input, use_container_width=True, hide_index=True)
        
        edited_input['ΔT (°C)'] = edited_input['Vapor Temp (°C)'] - edited_input['Brine Temp (°C)']
        edited_input['Distillate (TPH)'] = desal / 11
        edited_input['Q (kW)'] = (edited_input['Distillate (TPH)'] * 1000 / 3600) * LATENT_HEAT_VAPOR_KJ_KG
        edited_input['HTC (W/m²K)'] = (edited_input['Q (kW)'] * 1000) / (area_m2 * edited_input['ΔT (°C)']) # Converted to Watts matching Excel
        
        st.divider()
        st.subheader("HTC Results & Warning Profiler")
        
        warning_triggered = False
        for index, row in edited_input.iterrows():
            if row['ΔT (°C)'] > 3.0: # Set to 3.0 based on typical MED 4 logs
                st.error(f"🚨 **{row['Effect ID']} ALERT:** ΔT is {row['ΔT (°C)']:.2f}°C. This indicates severe localized scaling/choking.")
                warning_triggered = True
        if not warning_triggered:
            st.success("✅ All 11 effects are operating safely below the ΔT limits.")
        
        st.dataframe(edited_input.style.format({
            "ΔT (°C)": "{:.2f}", "Distillate (TPH)": "{:.1f}",
            "Q (kW)": "{:.0f}", "HTC (W/m²K)": "{:.2f}"
        }), use_container_width=True, hide_index=True)

        edited_input['Effect ID'] = pd.Categorical(edited_input['Effect ID'], categories=effects, ordered=True)
        st.bar_chart(edited_input.set_index("Effect ID")['HTC (W/m²K)'])

    # ==========================================
    # TAB 3: WATER ANALYSIS COMPLIANCE
    # ==========================================
    with tabs[2]:
        st.subheader("Daily Water Chemistry")
        w_col1, w_col2 = st.columns(2)
        
        with w_col1:
            st.markdown("**🌊 Feed Sea Water**")
            f_ph = st.number_input("Feed pH (7.5-8.2)", value=8.14)
            f_tds = st.number_input("Feed TDS (Max 42000)", value=41000.0)
            f_ca = st.number_input("Feed Calcium (950-1100)", value=1040.0)
            f_alk = st.number_input("Total Alkalinity (160-190)", value=170.0)
            
        with w_col2:
            st.markdown("**🚰 Desal Product**")
            p_ph = st.number_input("Product pH (5.5-7.0)", value=6.5)
            p_cond = st.number_input("Product Cond. (Max 15)", value=4.6)
            p_cl = st.number_input("Product Chlorides (Max 5)", value=0.0)
            p_iron = st.number_input("Total Iron (Max 0.1)", value=0.05)

    # ==========================================
    # TAB 4: MRA & RESIDUAL ANALYSIS (ALL 8 VARIABLES)
    # ==========================================
    with tabs[3]:
        st.subheader("Performance Data: Actual vs. Predicted (MRA)")
        st.markdown("This model utilizes all 8 regression variables to normalize today's production against historical clean-plant baselines.")
        
        controls_col, calc_col = st.columns([1, 2])
        
        with controls_col:
            st.markdown("### Operational Sliders")
            p_press = st.slider("1st Effect Press (mbar)", 200.0, 260.0, 230.8)
            p_t1 = st.slider("1st Effect Temp (°C)", 60.0, 75.0, 69.2)
            p_sw_up = st.slider("Sea Water Upper (m³/h)", 400.0, 1000.0, float(sw_upper))
            p_bt1 = st.slider("1st Brine Temp (°C)", 60.0, 75.0, 66.4)
            p_bflow = st.slider("Brine Flow (m³/h)", 1000.0, 1600.0, float(brine_return))
            p_stm = st.slider("LP Steam (TPH)", 50.0, 100.0, float(steam))
            p_stm_t = st.slider("Steam Temp (°C)", 160.0, 190.0, 177.8)
            p_anti = st.slider("Antiscalant (PPM)", 1.0, 10.0, 5.2)

        with calc_col:
            # Corrected MRA Engine
            predicted = (
                MRA_COEF["Intercept"] + 
                (MRA_COEF["Press_1st"] * p_press) + (MRA_COEF["Temp_1st"] * p_t1) +
                (MRA_COEF["SW_Upper"] * p_sw_up) + (MRA_COEF["Brine_Temp_1st"] * p_bt1) +
                (MRA_COEF["Brine_Flow"] * p_bflow) + (MRA_COEF["LP_Steam"] * p_stm) +
                (MRA_COEF["Steam_Temp"] * p_stm_t) + (MRA_COEF["Antiscalant"] * p_anti)
            )
            
            residual = gross_prod - predicted
            
            k1, k2, k3 = st.columns(3)
            k1.metric("Actual Gross SCADA", f"{gross_prod:,.1f} m³/h")
            k2.metric("MRA Predicted", f"{predicted:,.1f} m³/h")
            
            if residual < -15.0:
                k3.error(f"Residual: {residual:,.1f} (FOULING)")
            elif residual > 15.0:
                k3.success(f"Residual: {residual:,.1f} (CLEAN)")
            else:
                k3.info(f"Residual: {residual:,.1f} (NORMAL)")
                
            st.divider()
            st.markdown("### 📊 Parameter Variance Matrix")
            
            # Map all 8 parameters
            params = [
                ("1st Effect Press", "Press_1st", p_press),
                ("1st Effect Temp", "Temp_1st", p_t1),
                ("Sea Water Upper", "SW_Upper", p_sw_up),
                ("1st Brine Temp", "Brine_Temp_1st", p_bt1),
                ("Brine Flow", "Brine_Flow", p_bflow),
                ("LP Steam", "LP_Steam", p_stm),
                ("Steam Temp", "Steam_Temp", p_stm_t),
                ("Antiscalant", "Antiscalant", p_anti)
            ]
            
            var_data = []
            for name, key, live_val in params:
                base = MRA_BASELINE[key]
                dev = live_val - base
                impact = dev * MRA_COEF[key]
                var_data.append([name, base, live_val, dev, MRA_COEF[key], impact])
                
            variance_df = pd.DataFrame(var_data, columns=["Parameter", "Baseline", "Live Input", "Deviation", "Regression Weight", "Impact (TPH)"])
            st.dataframe(variance_df.style.format({
                "Baseline": "{:.1f}", "Live Input": "{:.1f}", "Deviation": "{:+.1f}",
                "Regression Weight": "{:.3f}", "Impact (TPH)": "{:+.1f}"
            }), use_container_width=True, hide_index=True)

    # ==========================================
    # TAB 5: DATABASE & REPORT GENERATOR
    # ==========================================
    with tabs[4]:
        st.subheader("Data Logging & Export")
        
        if st.button("💾 Save Today's Log"):
            new_log = pd.DataFrame({
                "Date": [log_date], "Steam (TPH)": [steam],
                "Desal (m3/h)": [desal], "Gross Prod (m3/h)": [gross_prod],
                "SW Feed (m3/h)": [sw_total], "GOR": [round(gor, 2)], 
                "Avg HTC": [round(edited_input['HTC (W/m²K)'].mean(), 2)]
            })
            st.session_state.daily_logs = pd.concat([st.session_state.daily_logs, new_log], ignore_index=True)
            st.success(f"Log saved for {log_date}!")
            
        st.markdown("*(Click the checkbox on the left of any row and press 'Delete' or 'Backspace' to remove mistakes)*")
        
        st.session_state.daily_logs = st.data_editor(st.session_state.daily_logs, num_rows="dynamic", use_container_width=True)
        
        if not st.session_state.daily_logs.empty:
            csv_export = st.session_state.daily_logs.to_csv(index=False).encode('utf-8')
            st.download_button(label="📥 Download Master Log (CSV)", data=csv_export, file_name=f"MED4_Log_{log_date}.csv", mime='text/csv')
            
        st.divider()
        st.subheader("Generate Enterprise Executive Report (.docx)")
        st.markdown("Generates a highly formatted Microsoft Word document detailing today's performance, mass balances, and the complete MRA Variance Matrix ready to be sent to RIL Management.")
        
        if st.button("📄 Generate RIL Executive Report", use_container_width=True):
            word_file = generate_word_report(log_date, gross_prod, predicted, residual, gor, stec, variance_df)
            st.download_button(
                label="📥 Download Microsoft Word Document",
                data=word_file,
                file_name=f"MED4_Performance_Report_{log_date}.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                type="primary"
            )

    st.sidebar.markdown("---")
    st.sidebar.caption("Prepared by Rahil Shah | Chembond Chemicals Ltd.")

if __name__ == "__main__":
    main()
