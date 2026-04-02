# requirements: pandas, numpy, python-docx
import streamlit as st
import pandas as pd
import numpy as np
import datetime
import io
from docx import Document

st.set_page_config(page_title="Chembond | RIL MED Management", layout="wide")

# --- INITIALIZE SESSION STATE ---
if 'daily_logs' not in st.session_state:
    st.session_state.daily_logs = pd.DataFrame(columns=[
        "Date", "Cluster", "Steam (TPH)", "Distillate (TPH)", "SW Feed (m3/h)", "GOR", "Avg HTC"
    ])

# --- CONSTANTS & MRA COEFFICIENTS ---
LATENT_HEAT_STEAM_KJ_KG = 2260.0 
LATENT_HEAT_VAPOR_KJ_KG = 2330.0 

PLANT_SPECS = {
    "DTA MED 1-4": {"area_m2": 6434.0},
    "DTA PCG MED 6": {"area_m2": 7264.0},
    "SEZ MED 1-6": {"area_m2": 14134.0}
}

MRA_COEF = {
    "Intercept": -161.56, "Press_1st": 0.613, "Temp_1st": 3.639, 
    "SW_Feed": 0.811, "Brine_Temp_1st": -7.66, "Brine_Flow": -0.23, 
    "LP_Steam": 8.25, "Steam_Temp": 2.19, "Antiscalant": -7.03
}

MRA_BASELINE = {
    "Press_1st": 230.0, "Temp_1st": 69.0, "SW_Feed": 1970.0, 
    "Brine_Temp_1st": 66.0, "Brine_Flow": 1209.0, "LP_Steam": 70.0, 
    "Steam_Temp": 176.0, "Antiscalant": 2.5
}

# --- DOCUMENT GENERATOR ---
def generate_word_report(date, cluster, actual, predicted, residual, gor, stec, variance_df):
    doc = Document()
    doc.add_heading('MED Daily Operational Performance Report', 0)
    
    p = doc.add_paragraph()
    p.add_run('Prepared by: ').bold = True
    p.add_run('Chembond Chemicals Ltd.\n')
    p.add_run('Client: ').bold = True
    p.add_run('Reliance Industries Limited (RIL)\n')
    p.add_run('Date: ').bold = True
    p.add_run(str(date) + '\n')
    p.add_run('Plant ID: ').bold = True
    p.add_run(cluster)
    
    doc.add_heading('1. Executive Summary', level=1)
    doc.add_paragraph(f"On {date}, the {cluster} unit achieved a Gain Output Ratio (GOR) of {gor:.2f}:1 and a Specific Thermal Energy Consumption (STEC) of {stec:.2f} kWh/ton.")
    
    doc.add_heading('2. Multiple Regression Analysis (MRA) & Fouling Indicator', level=1)
    mra_p = doc.add_paragraph()
    mra_p.add_run(f"Actual SCADA Production: {actual:.1f} TPH\n")
    mra_p.add_run(f"MRA Predicted Production: {predicted:.1f} TPH\n")
    mra_p.add_run(f"Calculated Residual (Actual - Predicted): {residual:.1f} TPH\n").bold = True
    
    if residual < -15.0:
        doc.add_paragraph("WARNING: A significant negative residual indicates potential thermal resistance (fouling) forming on the tube bundles. Antiscalant dosing adjustments or a scheduled acid wash should be evaluated.", style='BodyText')
    elif residual > 15.0:
        doc.add_paragraph("NOTE: Positive residual indicates the plant is over-performing the baseline model, showing excellent heat transfer efficiency.", style='BodyText')
    else:
        doc.add_paragraph("STATUS: The plant is operating perfectly within the normalized thermodynamic baseline. No fouling detected.", style='BodyText')
    
    doc.add_heading('3. Parameter Variance Impact', level=2)
    table = doc.add_table(rows=1, cols=4)
    table.style = 'Table Grid'
    hdr_cells = table.rows[0].cells
    hdr_cells[0].text = 'Parameter'
    hdr_cells[1].text = 'Baseline'
    hdr_cells[2].text = 'Actual Input'
    hdr_cells[3].text = 'Production Impact (TPH)'
    
    for idx, row in variance_df.iterrows():
        row_cells = table.add_row().cells
        row_cells[0].text = str(row['Parameter'])
        row_cells[1].text = str(row['Base Value'])
        row_cells[2].text = str(row['Live Value'])
        row_cells[3].text = f"{row['Impact (TPH)']:.1f}"
        
    bio = io.BytesIO()
    doc.save(bio)
    return bio.getvalue()

def main():
    st.title("🏭 RIL Thermal Desalination (MED) - Management Suite")
    st.caption("Developed by Chembond Chemicals Ltd.")
    
    st.sidebar.header("📅 Daily Setup")
    log_date = st.sidebar.date_input("Date", datetime.date.today())
    cluster = st.sidebar.selectbox("Plant", list(PLANT_SPECS.keys()))
    plant_area = PLANT_SPECS[cluster]["area_m2"]
    
    tabs = st.tabs(["🌊 1. Flows & STEC", "🔥 2. Thermo", "🧪 3. Water", "🧠 4. MRA & Residual", "📂 5. Database & Report"])

    # ==========================================
    # TAB 1: FLOWS & STEC
    # ==========================================
    with tabs[0]:
        st.subheader("Daily Mass Balance")
        c1, c2, c3 = st.columns(3)
        steam = c1.number_input("Motive Steam (TPH)", value=70.0)
        distillate = c2.number_input("Actual Distillate (TPH)", value=746.0)
        sw_feed = c3.number_input("Sea Water Feed (m³/h)", value=1970.0)
        
        brine_flow = sw_feed - distillate
        st.info(f"**Calculated Brine Return:** {brine_flow:,.1f} m³/h")
        
        gor = distillate / steam if steam > 0 else 0
        heat_load_kw = ((steam * 1000) / 3600) * LATENT_HEAT_STEAM_KJ_KG
        stec = heat_load_kw / distillate if distillate > 0 else 0
        
        math_c1, math_c2 = st.columns(2)
        with math_c1:
            st.latex(f"Q_{{input}} = \\frac{{{steam} \\times 1000}}{{3600}} \\times {LATENT_HEAT_STEAM_KJ_KG} = {heat_load_kw:,.0f} \\text{{ kW}}")
        with math_c2:
            st.latex(f"\\text{{STEC}} = \\frac{{{heat_load_kw:,.0f}}}{{{distillate}}} = {stec:,.1f} \\text{{ kWh/ton}}")
            
        st.metric("Gain Output Ratio (GOR)", f"{gor:.2f}:1")

    # ==========================================
    # TAB 2: THERMODYNAMIC & HTC
    # ==========================================
    with tabs[1]:
        st.subheader("11-Effect Temperature Inputs")
        st.info("💡 **Pro-Tip for Operators:** You do not need to type these one by one. You can copy a column of 11 numbers directly from your Excel sheet and press `Ctrl + V` to paste them into the table below.")
        
        effects = [f"Effect {i}" for i in range(1, 12)]
        df_input = pd.DataFrame({
            "Effect ID": effects,
            "Vapor Temp (°C)": np.round(np.linspace(69.0, 42.0, 11), 1),
            "Brine Temp (°C)": np.round(np.linspace(66.3, 40.0, 11), 1)
        })
        
        edited_input = st.data_editor(df_input, use_container_width=True, hide_index=True)
        
        # Math Execution
        edited_input['ΔT (°C)'] = edited_input['Vapor Temp (°C)'] - edited_input['Brine Temp (°C)']
        edited_input['Distillate per Effect (TPH)'] = distillate / 11
        edited_input['Q (kW)'] = (edited_input['Distillate per Effect (TPH)'] * 1000 / 3600) * LATENT_HEAT_VAPOR_KJ_KG
        edited_input['HTC (kW/m²K)'] = edited_input['Q (kW)'] / (plant_area * edited_input['ΔT (°C)'])
        
        st.divider()
        st.subheader("HTC Results & Warning Profiler")
        
        # Restore the Delta T Warnings
        warning_triggered = False
        for index, row in edited_input.iterrows():
            if row['ΔT (°C)'] > 2.0:
                st.error(f"🚨 **{row['Effect ID']} ALERT:** ΔT is {row['ΔT (°C)']:.2f}°C. This exceeds the 2.0°C limit and indicates severe localized scaling.")
                warning_triggered = True
        if not warning_triggered:
            st.success("✅ All 11 effects are operating safely below the 2.0°C ΔT limit.")
        
        st.dataframe(edited_input.style.format({
            "ΔT (°C)": "{:.2f}", 
            "Distillate per Effect (TPH)": "{:.1f}",
            "Q (kW)": "{:.0f}", 
            "HTC (kW/m²K)": "{:.3f}"
        }), use_container_width=True, hide_index=True)

        edited_input['Effect ID'] = pd.Categorical(edited_input['Effect ID'], categories=effects, ordered=True)
        st.bar_chart(edited_input.set_index("Effect ID")['HTC (kW/m²K)'])

    # ==========================================
    # TAB 3: WATER ANALYSIS COMPLIANCE
    # ==========================================
    with tabs[2]:
        st.subheader("Daily Water Chemistry")
        w_col1, w_col2 = st.columns(2)
        
        with w_col1:
            st.markdown("**🌊 Feed Sea Water**")
            f_ph = st.number_input("Feed pH (7.5-8.2)", value=8.0)
            f_tds = st.number_input("Feed TDS (Max 42000)", value=41000.0)
            f_ca = st.number_input("Feed Calcium (950-1100)", value=1000.0)
            f_alk = st.number_input("Total Alkalinity (160-190)", value=170.0)
            
        with w_col2:
            st.markdown("**🚰 Desal Product**")
            p_ph = st.number_input("Product pH (5.5-7.0)", value=6.5)
            p_cond = st.number_input("Product Cond. (Max 15)", value=8.0)
            p_cl = st.number_input("Product Chlorides (Max 5)", value=2.0)
            p_iron = st.number_input("Total Iron (Max 0.1)", value=0.05)

    # ==========================================
    # TAB 4: MRA & RESIDUAL ANALYSIS
    # ==========================================
    with tabs[3]:
        st.subheader("Performance Data: Actual vs. Predicted")
        st.markdown("This module isolates scaling by comparing Actual SCADA production against the MRA theoretical baseline.")
        
        controls_col, calc_col = st.columns([1, 2])
        
        with controls_col:
            st.markdown("### Operational Inputs")
            p_stm = st.slider("LP Steam (TPH)", 50.0, 95.0, steam)
            p_sw = st.slider("SW Feed (m³/h)", 1500.0, 2500.0, sw_feed)
            p_t1 = st.slider("1st Effect Temp (°C)", 60.0, 75.0, 69.0)
            p_bt1 = st.slider("1st Brine Temp (°C)", 60.0, 75.0, 66.0)
            
            # Static background variables for cleaner UI
            p_press = 230.0
            p_stm_t = 176.0
            p_bflow = sw_feed - distillate
            p_anti = 2.5

        with calc_col:
            predicted = (
                MRA_COEF["Intercept"] + 
                (MRA_COEF["Press_1st"] * p_press) + (MRA_COEF["Temp_1st"] * p_t1) +
                (MRA_COEF["SW_Feed"] * p_sw) + (MRA_COEF["Brine_Temp_1st"] * p_bt1) +
                (MRA_COEF["Brine_Flow"] * p_bflow) + (MRA_COEF["LP_Steam"] * p_stm) +
                (MRA_COEF["Steam_Temp"] * p_stm_t) + (MRA_COEF["Antiscalant"] * p_anti)
            )
            
            residual = distillate - predicted
            
            k1, k2, k3 = st.columns(3)
            k1.metric("Actual SCADA", f"{distillate:,.1f} TPH")
            k2.metric("MRA Predicted", f"{predicted:,.1f} TPH")
            
            if residual < -15.0:
                k3.error(f"Residual: {residual:,.1f} TPH (FOULING)")
            elif residual > 15.0:
                k3.success(f"Residual: {residual:,.1f} TPH (CLEAN)")
            else:
                k3.info(f"Residual: {residual:,.1f} TPH (NORMAL)")
                
            st.divider()
            st.markdown("### 📊 Parameter Variance & Root Cause")
            
            var_data = [
                ["LP Steam", MRA_BASELINE["LP_Steam"], p_stm, (p_stm - MRA_BASELINE["LP_Steam"]) * MRA_COEF["LP_Steam"]],
                ["Sea Water Feed", MRA_BASELINE["SW_Feed"], p_sw, (p_sw - MRA_BASELINE["SW_Feed"]) * MRA_COEF["SW_Feed"]],
                ["1st Effect Temp", MRA_BASELINE["Temp_1st"], p_t1, (p_t1 - MRA_BASELINE["Temp_1st"]) * MRA_COEF["Temp_1st"]],
                ["1st Brine Temp", MRA_BASELINE["Brine_Temp_1st"], p_bt1, (p_bt1 - MRA_BASELINE["Brine_Temp_1st"]) * MRA_COEF["Brine_Temp_1st"]],
            ]
            variance_df = pd.DataFrame(var_data, columns=["Parameter", "Base Value", "Live Value", "Impact (TPH)"])
            st.dataframe(variance_df.style.format({"Base Value": "{:.1f}", "Live Value": "{:.1f}", "Impact (TPH)": "{:+.1f}"}), use_container_width=True, hide_index=True)

    # ==========================================
    # TAB 5: DATABASE & REPORT GENERATOR
    # ==========================================
    with tabs[4]:
        st.subheader("Data Logging & Export")
        
        if st.button("💾 Save Today's Log"):
            new_log = pd.DataFrame({
                "Date": [log_date],
                "Cluster": [cluster],
                "Steam (TPH)": [steam],
                "Distillate (TPH)": [distillate],
                "SW Feed (m3/h)": [sw_feed],
                "GOR": [round(gor, 2)],
                "Avg HTC": [round(edited_input['HTC (kW/m²K)'].mean(), 2)]
            })
            st.session_state.daily_logs = pd.concat([st.session_state.daily_logs, new_log], ignore_index=True)
            st.success(f"Log saved for {log_date}!")
            
        st.markdown("*(Click the checkbox on the left of any row and press 'Delete' to remove mistakes)*")
        
        st.session_state.daily_logs = st.data_editor(
            st.session_state.daily_logs, 
            num_rows="dynamic", 
            use_container_width=True
        )
        
        if not st.session_state.daily_logs.empty:
            csv_export = st.session_state.daily_logs.to_csv(index=False).encode('utf-8')
            st.download_button(
                label="📥 Download Master Log (CSV)",
                data=csv_export,
                file_name=f"RIL_MED_Log_{log_date}.csv",
                mime='text/csv'
            )
            
        st.divider()
        st.subheader("Generate Professional Daily Report")
        st.markdown("Compile today's actual data and the MRA Residual into a formatted Microsoft Word (.docx) document.")
        
        if st.button("📄 Generate RIL Executive Report (.docx)", use_container_width=True):
            word_file = generate_word_report(log_date, cluster, distillate, predicted, residual, gor, stec, variance_df)
            st.download_button(
                label="📥 Download Word Document",
                data=word_file,
                file_name=f"RIL_MED_Report_{log_date}.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                type="primary"
            )

    st.sidebar.markdown("---")
    st.sidebar.caption("Prepared by Rahil Shah | Chembond Chemicals Ltd.")

if __name__ == "__main__":
    main()
