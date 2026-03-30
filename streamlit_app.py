# requirements: pandas, numpy
import streamlit as st
import pandas as pd
import numpy as np
import datetime

st.set_page_config(page_title="Chembond Chemicals | RIL MED Management Suite", layout="wide", initial_sidebar_state="expanded")

# --- INITIALIZE SESSION STATE FOR DAILY LOGGING ---
if 'daily_logs' not in st.session_state:
    st.session_state.daily_logs = pd.DataFrame()

# --- CONSTANTS & RFQ DATA ---
LATENT_HEAT_STEAM_KJ_KG = 2260.0 
LATENT_HEAT_VAPOR_KJ_KG = 2330.0 

# RFQ Annexure 1: Surface Area Calculations based on Tube Data
# Area = Pi * OD (24mm) * Length * Number of Tubes
PLANT_SPECS = {
    "DTA MED 1-4": {"tubes": 15514, "length": 5.5, "area_m2": 6434.0, "design_dist": 500},
    "DTA PCG MED 6": {"tubes": 17516, "length": 5.5, "area_m2": 7264.0, "design_dist": 600},
    "SEZ MED 1-6": {"tubes": 31244, "length": 6.0, "area_m2": 14134.0, "design_dist": 1000}
}

# RFQ Annexure 2 & 3: Water Quality Specs
WATER_SPECS = {
    "Feed": {"pH_min": 7.5, "pH_max": 8.2, "TDS_max": 42000, "TSS_max": 10, "Ca_min": 950, "Ca_max": 1100},
    "Product": {"pH_min": 5.5, "pH_max": 7.0, "Cond_max": 15, "TDS_max": 10, "Cl_max": 5}
}

def main():
    st.title("🏭 RIL Thermal Desalination (MED) - Daily Operations Suite")
    st.caption("Developed for Chembond | Compliant with RIL RFQ Guidelines")

    # --- SIDEBAR: DAILY SETUP ---
    st.sidebar.header("📅 Daily Log Setup")
    log_date = st.sidebar.date_input("Select Date", datetime.date.today())
    cluster = st.sidebar.selectbox("Select Plant", list(PLANT_SPECS.keys()))
    plant_area = PLANT_SPECS[cluster]["area_m2"]
    
    st.sidebar.markdown(f"**Plant Active Area:** {plant_area:,.1f} m²/effect")
    st.sidebar.divider()
    
    # --- NAVIGATION ---
    tabs = st.tabs([
        "🌊 1. Flows & STEC Derivation", 
        "🔥 2. Thermodynamic & HTC Profiling", 
        "🧪 3. Water Analysis Compliance",
        "🛢️ 4. Chemical & MRA Modeling",
        "📂 5. Daily Export Log"
    ])

    # ==========================================
    # TAB 1: FLOWS & STEC DERIVATION
    # ==========================================
    with tabs[0]:
        st.subheader("Daily Plant Flows & Energy Consumption")
        
        col1, col2, col3, col4 = st.columns(4)
        motive_steam = col1.number_input("Motive Steam Flow (TPH)", value=47.0, step=1.0)
        distillate = col2.number_input("Total Distillate (TPH)", value=500.0, step=1.0)
        sw_feed = col3.number_input("Sea Water Feed (m³/h)", value=1165.0, step=10.0)
        brine_flow = col4.number_input("Brine Return (m³/h)", value=665.0, step=10.0)

        st.divider()
        st.subheader("Specific Thermal Energy Consumption (STEC) Derivation")
        st.markdown("RIL requires transparent calculation of thermal efficiency. Here is the step-by-step physical derivation based on today's inputs:")
        
        # Calculations
        steam_kg_s = (motive_steam * 1000) / 3600
        heat_load_kw = steam_kg_s * LATENT_HEAT_STEAM_KJ_KG
        stec_kwh_t = heat_load_kw / distillate if distillate > 0 else 0
        gor = distillate / motive_steam if motive_steam > 0 else 0
        
        # Display Math
        math_c1, math_c2 = st.columns([1, 1])
        with math_c1:
            st.latex(r"Q_{input} (\text{kW}) = \frac{\text{Steam (TPH)} \times 1000}{3600} \times \lambda_{steam}")
            st.latex(f"Q_{{input}} = \\frac{{{motive_steam} \\times 1000}}{{3600}} \\times {LATENT_HEAT_STEAM_KJ_KG}")
            st.info(f"**Total Heat Load:** {heat_load_kw:,.2f} kW")
            
        with math_c2:
            st.latex(r"\text{STEC (kWh/t)} = \frac{Q_{input} (\text{kW})}{\text{Distillate (TPH)}}")
            st.latex(f"\\text{{STEC}} = \\frac{{{heat_load_kw:,.2f}}}{{{distillate}}}")
            st.success(f"**Derived STEC:** {stec_kwh_t:,.2f} kWh/ton")
            
        st.metric("Gain Output Ratio (GOR)", f"{gor:.2f}:1", delta="Target: > 10.5:1")

    # ==========================================
    # TAB 2: THERMODYNAMIC & HTC PROFILING
    # ==========================================
    with tabs[1]:
        st.subheader("11-Effect Heat Transfer Coefficient (HTC)")
        st.markdown(f"Calculating $U_{{actual}} = \\frac{{Q_{{effect}}}}{{A \\times \\Delta T}}$. Using confirmed area: **{plant_area:,.1f} m²** per effect.")
        
        # Dataframe setup with sequential effects
        effects = [f"Effect {i}" for i in range(1, 12)]
        default_v = np.linspace(70.0, 42.0, 11)
        default_b = np.linspace(69.0, 40.0, 11)
        
        df_htc = pd.DataFrame({
            "Effect ID": effects,
            "Vapor Temp (°C)": np.round(default_v, 2),
            "Brine Temp (°C)": np.round(default_b, 2),
            "Distillate per Effect (TPH)": [distillate / 11] * 11 
        })
        
        edited_htc = st.data_editor(df_htc, use_container_width=True, hide_index=True)
        
        # Rigorous HTC Math
        edited_htc['ΔT (°C)'] = edited_htc['Vapor Temp (°C)'] - edited_htc['Brine Temp (°C)']
        edited_htc['Q_effect (kW)'] = (edited_htc['Distillate per Effect (TPH)'] * 1000 / 3600) * LATENT_HEAT_VAPOR_KJ_KG
        edited_htc['HTC (kW/m²K)'] = edited_htc['Q_effect (kW)'] / (plant_area * edited_htc['ΔT (°C)'])
        
        st.markdown("### ⚠️ Dynamic Scaling Warnings")
        warning_triggered = False
        for index, row in edited_htc.iterrows():
            if row['ΔT (°C)'] > 2.0:  # Adjust threshold as per strict RFQ needs
                st.error(f"🚨 **{row['Effect ID']} ALERT:** ΔT is {row['ΔT (°C)']:.2f}°C. This exceeds limits and indicates severe localized scaling. Check nozzle spray.")
                warning_triggered = True
        if not warning_triggered:
            st.success("✅ All 11 effects are operating within acceptable ΔT thermodynamic limits.")
            
        st.bar_chart(edited_htc.set_index("Effect ID")['HTC (kW/m²K)'])

    # ==========================================
    # TAB 3: WATER ANALYSIS COMPLIANCE
    # ==========================================
    with tabs[2]:
        st.subheader("Daily Water Chemistry vs. RFQ Specs")
        
        w_col1, w_col2 = st.columns(2)
        
        with w_col1:
            st.markdown("### 🌊 Sea Water Feed Analysis")
            f_ph = st.number_input("Feed pH", value=8.0, step=0.1)
            f_tds = st.number_input("Feed TDS (ppm)", value=41000.0, step=100.0)
            f_tss = st.number_input("Feed TSS (ppm)", value=8.0, step=0.5)
            f_ca = st.number_input("Feed Calcium (ppm as CaCO3)", value=1000.0, step=10.0)
            
            # Feed Compliance Checks
            st.markdown("**Compliance Check:**")
            if WATER_SPECS["Feed"]["pH_min"] <= f_ph <= WATER_SPECS["Feed"]["pH_max"]: st.write("✅ pH: Pass") 
            else: st.write("❌ pH: Fail (Target: 7.5-8.2)")
            
            if f_tds <= WATER_SPECS["Feed"]["TDS_max"]: st.write("✅ TDS: Pass") 
            else: st.write("❌ TDS: Fail (Target: <42000)")
                
            if WATER_SPECS["Feed"]["Ca_min"] <= f_ca <= WATER_SPECS["Feed"]["Ca_max"]: st.write("✅ Calcium: Pass") 
            else: st.write("❌ Calcium: Fail (Target: 950-1100)")

        with wq_col2 := w_col2:
            st.markdown("### 🚰 Desal Product Analysis")
            p_ph = st.number_input("Product pH", value=6.5, step=0.1)
            p_cond = st.number_input("Product Conductivity (μs/cm)", value=10.0, step=0.5)
            p_tds = st.number_input("Product TDS (ppm)", value=4.0, step=0.5)
            p_cl = st.number_input("Product Chlorides (ppm)", value=2.0, step=0.5)
            
            # Product Compliance Checks
            st.markdown("**Compliance Check:**")
            if p_cond <= WATER_SPECS["Product"]["Cond_max"]: st.write("✅ Conductivity: Pass") 
            else: st.write("❌ Conductivity: Fail (Target: <=15)")
                
            if p_tds < WATER_SPECS["Product"]["TDS_max"]: st.write("✅ TDS: Pass") 
            else: st.write("❌ TDS: Fail (Target: <10)")
                
            if p_cl < WATER_SPECS["Product"]["Cl_max"]: st.write("✅ Chlorides: Pass") 
            else: st.write("❌ Chlorides: Fail (Target: <5)")

    # ==========================================
    # TAB 4: CHEMICAL & MRA MODELING
    # ==========================================
    with tabs[3]:
        st.subheader("Chemical Dosing & Baseline Prediction")
        
        c_col1, c_col2 = st.columns(2)
        with c_col1:
            st.markdown("#### Antiscalant Dosing")
            target_ppm = st.number_input("Target Antiscalant Dose (PPM)", value=2.5, step=0.1)
            # Theoretical demand (kg/h) = flow (m3/h) * density(~1) * ppm / 1000
            theo_demand_kg_h = (sw_feed * target_ppm) / 1000 
            st.metric("Required Dosing Rate", f"{theo_demand_kg_h:.2f} kg/hr")
            actual_dose = st.number_input("Actual Pump Output (kg/hr)", value=2.9, step=0.1)
            
            if actual_dose > (theo_demand_kg_h * 1.15):
                st.warning("⚠️ High Dosage Variance: You are over-dosing by >15%. RIL will penalize excess consumption.")
                
        with c_col2:
            st.markdown("#### MRA Expected Production")
            st.markdown("Based on linear regression of today's feed and steam inputs:")
            # Simplified MRA execution for dashboard speed
            expected_prod = -161.5 + (8.25 * motive_steam) + (0.81 * sw_feed) 
            deviation = distillate - expected_prod
            st.metric("MRA Expected Distillate", f"{expected_prod:.1f} TPH", delta=f"{deviation:.1f} TPH vs Actual", delta_color="normal")

    # ==========================================
    # TAB 5: DAILY EXPORT LOG
    # ==========================================
    with tabs[4]:
        st.subheader("Data Logging & Export")
        st.markdown("Compile today's inputs into the master database for the Monthly Management Review.")
        
        if st.button("💾 Save Today's Log"):
            new_log = pd.DataFrame({
                "Date": [log_date],
                "Cluster": [cluster],
                "Steam (TPH)": [motive_steam],
                "Distillate (TPH)": [distillate],
                "SW Feed (m3/h)": [sw_feed],
                "STEC (kWh/t)": [round(stec_kwh_t, 2)],
                "GOR": [round(gor, 2)],
                "Feed TDS": [f_tds],
                "Prod Cond": [p_cond],
                "Avg HTC": [round(edited_htc['HTC (kW/m²K)'].mean(), 2)]
            })
            st.session_state.daily_logs = pd.concat([st.session_state.daily_logs, new_log], ignore_index=True)
            st.success(f"Log saved for {log_date}!")
            
        if not st.session_state.daily_logs.empty:
            st.dataframe(st.session_state.daily_logs, use_container_width=True)
            
            csv_export = st.session_state.daily_logs.to_csv(index=False).encode('utf-8')
            st.download_button(
                label="📥 Download Master Report (CSV)",
                data=csv_export,
                file_name=f"RIL_MED_Report_Compiled.csv",
                mime='text/csv'
            )

    st.sidebar.markdown("---")
    st.sidebar.caption("Prepared by Rahil Shah | Chembond Chemicals Ltd.")

if __name__ == "__main__":
    main()
