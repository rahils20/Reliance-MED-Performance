# requirements: pandas, numpy
import streamlit as st
import pandas as pd
import numpy as np
import datetime

st.set_page_config(page_title="Chembond Chemicals | RIL MED Management Suite", layout="wide")

if 'daily_logs' not in st.session_state:
    st.session_state.daily_logs = pd.DataFrame()

# --- CONSTANTS & MRA COEFFICIENTS ---
LATENT_HEAT_STEAM_KJ_KG = 2260.0 
LATENT_HEAT_VAPOR_KJ_KG = 2330.0 

PLANT_SPECS = {
    "DTA MED 1-4": {"area_m2": 6434.0, "design_dist": 500},
    "DTA PCG MED 6": {"area_m2": 7264.0, "design_dist": 600},
    "SEZ MED 1-6": {"area_m2": 14134.0, "design_dist": 1000}
}

# Linear Regression Coefficients extracted from your MRA Tool
MRA_COEF = {
    "Intercept": -161.56,
    "Press_1st": 0.613,
    "Temp_1st": 3.639,
    "SW_Feed": 0.811,
    "Brine_Temp_1st": -7.66,
    "Brine_Flow": -0.23,
    "LP_Steam": 8.25,
    "Steam_Temp": 2.19,
    "Antiscalant": -7.03
}

def main():
    st.title("🏭 RIL Thermal Desalination (MED) - Daily Operations Suite")
    st.caption("Developed for Chembond Chemicals Ltd. | Compliant with RIL RFQ Guidelines")
    
    st.sidebar.header("📅 Daily Log Setup")
    log_date = st.sidebar.date_input("Select Date", datetime.date.today())
    cluster = st.sidebar.selectbox("Select Plant", list(PLANT_SPECS.keys()))
    plant_area = PLANT_SPECS[cluster]["area_m2"]
    
    st.sidebar.markdown(f"**Plant Active Area:** {plant_area:,.1f} m²/effect")
    
    tabs = st.tabs([
        "🌊 1. Flows & STEC", 
        "🔥 2. Thermodynamic HTC", 
        "🧪 3. Water Analysis",
        "🧠 4. MRA Root Cause",
        "📂 5. Daily Export"
    ])

    # ==========================================
    # TAB 1: FLOWS & STEC DERIVATION
    # ==========================================
    with tabs[0]:
        st.subheader("Daily Plant Flows")
        
        col1, col2, col3 = st.columns(3)
        motive_steam = col1.number_input("Motive Steam Flow (TPH)", value=70.0, step=1.0)
        distillate = col2.number_input("Total Distillate (TPH)", value=746.0, step=1.0)
        sw_feed = col3.number_input("Sea Water Feed (m³/h)", value=1970.0, step=10.0)
        
        # AUTOMATIC BRINE CALCULATION
        brine_flow = sw_feed - distillate
        st.info(f"**Calculated Brine Return:** {brine_flow:,.1f} m³/h (Based on Feed - Distillate Mass Balance)")

        st.divider()
        st.subheader("STEC Derivation")
        heat_load_kw = ((motive_steam * 1000) / 3600) * LATENT_HEAT_STEAM_KJ_KG
        stec_kwh_t = heat_load_kw / distillate if distillate > 0 else 0
        gor = distillate / motive_steam if motive_steam > 0 else 0
        
        math_c1, math_c2 = st.columns(2)
        with math_c1:
            st.latex(f"Q_{{input}} = \\frac{{{motive_steam} \\times 1000}}{{3600}} \\times {LATENT_HEAT_STEAM_KJ_KG} = {heat_load_kw:,.0f} \\text{{ kW}}")
        with math_c2:
            st.latex(f"\\text{{STEC}} = \\frac{{{heat_load_kw:,.0f}}}{{{distillate}}} = {stec_kwh_t:,.1f} \\text{{ kWh/ton}}")
            
        st.metric("Gain Output Ratio (GOR)", f"{gor:.2f}:1")

    # ==========================================
    # TAB 2: THERMODYNAMIC & HTC PROFILING
    # ==========================================
    with tabs[1]:
        st.subheader("11-Effect Temperature Inputs")
        
        # Input Table
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
        
        # Display the explicit calculated results table
        st.dataframe(edited_input.style.format({
            "ΔT (°C)": "{:.2f}", 
            "Distillate per Effect (TPH)": "{:.1f}",
            "Q (kW)": "{:.0f}", 
            "HTC (kW/m²K)": "{:.3f}"
        }), use_container_width=True, hide_index=True)

        # Ensure correct charting order using Categorical indexing
        edited_input['Effect ID'] = pd.Categorical(edited_input['Effect ID'], categories=effects, ordered=True)
        chart_data = edited_input.set_index("Effect ID")['HTC (kW/m²K)']
        st.bar_chart(chart_data)

    # ==========================================
    # TAB 3: WATER ANALYSIS COMPLIANCE
    # ==========================================
    with tabs[2]:
        st.subheader("Daily Water Chemistry vs. RFQ")
        w_col1, w_col2 = st.columns(2)
        with w_col1:
            st.markdown("**🌊 Feed Sea Water**")
            f_tds = st.number_input("Feed TDS (Max 42000)", value=41000.0)
            f_ca = st.number_input("Feed Calcium (950-1100)", value=1000.0)
        with w_col2:
            st.markdown("**🚰 Desal Product**")
            p_cond = st.number_input("Product Cond. (Max 15)", value=8.0)
            p_cl = st.number_input("Product Chlorides (Max 5)", value=2.0)

    # ==========================================
    # TAB 4: MRA ROOT CAUSE ANALYSIS
    # ==========================================
    with tabs[3]:
        st.subheader("MRA Interactive Explainer")
        st.markdown("Adjust the sliders to mimic Live SCADA conditions. The software will calculate predicted production and mathematically explain the variances.")
        
        m_col1, m_col2 = st.columns([1, 2])
        
        with m_col1:
            st.markdown("#### Live SCADA Controls")
            live_steam = st.slider("LP Steam (TPH)", 50.0, 100.0, float(motive_steam))
            live_sw = st.slider("SW Feed (m³/h)", 1500.0, 2500.0, float(sw_feed))
            live_1st_t = st.slider("1st Effect Temp (°C)", 60.0, 75.0, 69.1)
            live_brine_t = st.slider("1st Brine Temp (°C)", 60.0, 75.0, 66.3)
            live_ppm = st.slider("Antiscalant (PPM)", 1.0, 5.0, 2.5)
            
            # Static assumptions for the rest to keep the UI clean
            live_press = 230.0
            live_steam_t = 176.0

        with m_col2:
            # The MRA Calculation
            predicted_prod = (
                MRA_COEF["Intercept"] + 
                (MRA_COEF["Press_1st"] * live_press) +
                (MRA_COEF["Temp_1st"] * live_1st_t) +
                (MRA_COEF["SW_Feed"] * live_sw) +
                (MRA_COEF["Brine_Temp_1st"] * live_brine_t) +
                (MRA_COEF["Brine_Flow"] * brine_flow) +
                (MRA_COEF["LP_Steam"] * live_steam) +
                (MRA_COEF["Steam_Temp"] * live_steam_t) +
                (MRA_COEF["Antiscalant"] * live_ppm)
            )
            
            st.metric("MRA Predicted Production", f"{predicted_prod:,.1f} TPH")
            
            st.markdown("### 🔍 Root Cause Variance Analysis")
            st.markdown("If production drops, we can isolate the exact variable causing the issue based on its historical regression weight:")
            
            # Calculate impact (Variance from a hypothetical perfect baseline)
            # For demonstration, we show how much each parameter is contributing to the total prediction
            impact_steam = live_steam * MRA_COEF["LP_Steam"]
            impact_sw = live_sw * MRA_COEF["SW_Feed"]
            impact_temp = live_1st_t * MRA_COEF["Temp_1st"]
            impact_brine_t = live_brine_t * MRA_COEF["Brine_Temp_1st"]
            
            impact_df = pd.DataFrame({
                "Parameter": ["LP Steam Flow", "Sea Water Feed", "1st Effect Temp", "1st Brine Temp"],
                "Contribution to Production (TPH)": [impact_steam, impact_sw, impact_temp, impact_brine_t],
                "MRA Coefficient (Weight)": [MRA_COEF["LP_Steam"], MRA_COEF["SW_Feed"], MRA_COEF["Temp_1st"], MRA_COEF["Brine_Temp_1st"]]
            })
            
            st.dataframe(impact_df.style.format({"Contribution to Production (TPH)": "{:,.1f}"}), hide_index=True, use_container_width=True)
            
            st.info(f"**How to read this for RIL:** If the *Contribution* of '1st Brine Temp' drops significantly into the negative, we can definitively prove that thermal resistance (scaling) is the bottleneck, rather than a lack of motive steam.")

    # ==========================================
    # TAB 5: DAILY EXPORT LOG
    # ==========================================
    with tabs[4]:
        st.subheader("Data Logging & Export")
        if st.button("💾 Save Today's Log"):
            new_log = pd.DataFrame({
                "Date": [log_date],
                "Cluster": [cluster],
                "Steam (TPH)": [motive_steam],
                "Distillate (TPH)": [distillate],
                "SW Feed (m3/h)": [sw_feed],
                "Brine Flow (m3/h)": [brine_flow],
                "GOR": [round(gor, 2)],
                "Avg HTC": [round(edited_input['HTC (kW/m²K)'].mean(), 2)]
            })
            st.session_state.daily_logs = pd.concat([st.session_state.daily_logs, new_log], ignore_index=True)
            st.success("Log saved!")
            
        if not st.session_state.daily_logs.empty:
            st.dataframe(st.session_state.daily_logs, use_container_width=True)
            csv_export = st.session_state.daily_logs.to_csv(index=False).encode('utf-8')
            st.download_button(label="📥 Download Master Report (CSV)", data=csv_export, file_name="RIL_MED_Report.csv", mime='text/csv')

    st.sidebar.markdown("---")
    st.sidebar.caption("Prepared by Rahil Shah | Chembond Chemicals Ltd.")

if __name__ == "__main__":
    main()
