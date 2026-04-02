# requirements: pandas, numpy
import streamlit as st
import pandas as pd
import numpy as np
import datetime
import io

st.set_page_config(page_title="Chembond | RIL MED Management", layout="wide")

# --- INITIALIZE SESSION STATE ---
if 'daily_logs' not in st.session_state:
    st.session_state.daily_logs = pd.DataFrame(columns=[
        "Date", "Cluster", "Steam (TPH)", "Distillate (TPH)", "SW Feed (m3/h)", "GOR", "Avg HTC"
    ])

# --- CONSTANTS ---
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

# Baseline for MRA Explainer (Design/Typical Values)
MRA_BASELINE = {"LP_Steam": 70.0, "SW_Feed": 2000.0, "Temp_1st": 68.0, "Brine_Temp_1st": 65.0}

# RFQ Water Quality Limits (Min, Max)
WATER_SPECS = {
    "Feed": {
        "pH": (7.5, 9.2), "Turbidity (NTU)": (0, 5), "TSS (ppm)": (0, 10), 
        "TDS (ppm)": (0, 42000), "Total Alkalinity": (160, 190), 
        "Calcium Hardness": (950, 1100), "Chlorides": (21000, 22000), "Sulphate": (3050, 3250)
    },
    "Product": {
        "pH": (5.5, 7.0), "Conductivity (μs/cm)": (0, 15), "TDS (ppm)": (0, 10), 
        "Total Iron": (0, 0.1), "Chlorides": (0, 5), "Sulphate": (0, 1.0)
    }
}

def generate_csv_template():
    df = pd.DataFrame({
        "Effect ID": [f"Effect {i}" for i in range(1, 12)],
        "Vapor Temp (°C)": [0.0]*11,
        "Brine Temp (°C)": [0.0]*11
    })
    return df.to_csv(index=False).encode('utf-8')

def check_spec(val, limits):
    if limits[0] <= val <= limits[1]: return "✅ Pass"
    return f"🚨 Fail (Target: {limits[0]} - {limits[1]})"

def main():
    st.title("🏭 RIL Thermal Desalination (MED) - Management Suite")
    st.caption("Developed by Chembond Chemicals Ltd.")
    
    st.sidebar.header("📅 Daily Setup")
    log_date = st.sidebar.date_input("Date", datetime.date.today())
    cluster = st.sidebar.selectbox("Plant", list(PLANT_SPECS.keys()))
    plant_area = PLANT_SPECS[cluster]["area_m2"]
    
    tabs = st.tabs([
        "🌊 1. Flows & STEC", "🔥 2. Thermo & HTC", "🧪 3. Water Analysis",
        "🧠 4. MRA Explainer", "📂 5. Master Log Database"
    ])

    # ==========================================
    # TAB 1: FLOWS
    # ==========================================
    with tabs[0]:
        st.subheader("Daily Mass Balance")
        c1, c2, c3 = st.columns(3)
        steam = c1.number_input("Motive Steam (TPH)", 70.0)
        distillate = c2.number_input("Distillate (TPH)", 746.0)
        sw_feed = c3.number_input("Sea Water Feed (m³/h)", 1970.0)
        
        brine = sw_feed - distillate
        st.info(f"**Calculated Brine Return:** {brine:,.1f} m³/h")
        
        gor = distillate / steam if steam > 0 else 0
        st.metric("Gain Output Ratio (GOR)", f"{gor:.2f}:1")

    # ==========================================
    # TAB 2: THERMODYNAMIC (CSV UPLOAD & ERRORS)
    # ==========================================
    with tabs[1]:
        st.subheader("11-Effect Temperature Tracking")
        
        # Innovative File Upload System
        upload_col, template_col = st.columns([3, 1])
        with template_col:
            st.download_button("📥 Download Daily Template", generate_csv_template(), "MED_Temp_Template.csv", "text/csv")
        
        with upload_col:
            uploaded_file = st.file_uploader("Upload filled CSV file to auto-populate", type=["csv"])
        
        if uploaded_file:
            df_input = pd.read_csv(uploaded_file)
            st.success("Data imported successfully!")
        else:
            # Default Data
            df_input = pd.DataFrame({
                "Effect ID": [f"Effect {i}" for i in range(1, 12)],
                "Vapor Temp (°C)": np.round(np.linspace(69.0, 42.0, 11), 1),
                "Brine Temp (°C)": np.round(np.linspace(66.3, 40.0, 11), 1)
            })
            
        edited_input = st.data_editor(df_input, use_container_width=True, hide_index=True)
        
        # Thermodynamics
        edited_input['ΔT (°C)'] = edited_input['Vapor Temp (°C)'] - edited_input['Brine Temp (°C)']
        edited_input['Q (kW)'] = ((distillate / 11) * 1000 / 3600) * LATENT_HEAT_VAPOR_KJ_KG
        edited_input['HTC (U)'] = edited_input['Q (kW)'] / (plant_area * edited_input['ΔT (°C)'])
        
        st.markdown("### ⚠️ Scaling Alerts (ΔT > 2.0°C)")
        safe = True
        for idx, row in edited_input.iterrows():
            if row['ΔT (°C)'] > 2.0:
                st.error(f"🚨 {row['Effect ID']}: ΔT is {row['ΔT (°C)']:.2f}°C. This indicates thermal resistance (fouling) on the tube bundle.")
                safe = False
        if safe: st.success("✅ All effects operating below the 2.0°C limit.")

        st.dataframe(edited_input.style.format({"ΔT (°C)": "{:.2f}", "HTC (U)": "{:.2f}"}), hide_index=True, use_container_width=True)

    # ==========================================
    # TAB 3: WATER ANALYSIS (RESTORED PARAMS)
    # ==========================================
    with tabs[2]:
        st.subheader("Laboratory Analysis vs RFQ Limits")
        w_col1, w_col2 = st.columns(2)
        
        with w_col1:
            st.markdown("#### 🌊 Sea Water Feed")
            for param, limits in WATER_SPECS["Feed"].items():
                col_in, col_chk = st.columns([2, 2])
                val = col_in.number_input(param, value=limits[0] + (limits[1]-limits[0])/2, key=f"f_{param}")
                col_chk.markdown(f"<div style='margin-top:30px'>{check_spec(val, limits)}</div>", unsafe_allow_html=True)
                
        with w_col2:
            st.markdown("#### 🚰 Desal Product")
            for param, limits in WATER_SPECS["Product"].items():
                col_in, col_chk = st.columns([2, 2])
                val = col_in.number_input(param, value=limits[0], key=f"p_{param}")
                col_chk.markdown(f"<div style='margin-top:30px'>{check_spec(val, limits)}</div>", unsafe_allow_html=True)

    # ==========================================
    # TAB 4: MRA (PLAIN ENGLISH EXPLAINER)
    # ==========================================
    with tabs[3]:
        st.subheader("MRA Production Variance Explainer")
        st.markdown("This tab translates the mathematical weights into an operational narrative for management.")
        
        live_steam = st.slider("LP Steam (TPH)", 50.0, 100.0, 70.0)
        live_sw = st.slider("SW Feed (m³/h)", 1500.0, 2500.0, 2000.0)
        
        predicted = (MRA_COEF["Intercept"] + (MRA_COEF["LP_Steam"] * live_steam) + 
                     (MRA_COEF["SW_Feed"] * live_sw) + (MRA_COEF["Brine_Temp_1st"] * 65.0) + (MRA_COEF["Temp_1st"] * 68.0))
        
        st.metric("Predicted Output based on Sliders", f"{predicted:,.1f} TPH")
        
        st.markdown("### 🗣️ How to explain this to Reliance:")
        st.markdown("*(Compared to standard baseline operating conditions)*")
        
        # The Story-teller logic
        steam_diff = live_steam - MRA_BASELINE["LP_Steam"]
        sw_diff = live_sw - MRA_BASELINE["SW_Feed"]
        
        steam_impact = steam_diff * MRA_COEF["LP_Steam"]
        sw_impact = sw_diff * MRA_COEF["SW_Feed"]
        
        if steam_impact != 0:
            direction = "increased" if steam_impact > 0 else "decreased"
            st.info(f"💨 Because Steam Flow {direction} by {abs(steam_diff):.1f} TPH, production mathematically shifted by **{steam_impact:+.1f} TPH**.")
        if sw_impact != 0:
            direction = "increased" if sw_impact > 0 else "decreased"
            st.info(f"🌊 Because Sea Water Feed {direction} by {abs(sw_diff):.1f} m³/h, production mathematically shifted by **{sw_impact:+.1f} TPH**.")

    # ==========================================
    # TAB 5: DATABASE & LOG MANAGEMENT
    # ==========================================
    with tabs[4]:
        st.subheader("Monthly Database Management")
        
        c_up, c_save = st.columns(2)
        with c_up:
            old_db = st.file_uploader("📂 Load Previous Master Database (CSV)", type=["csv"])
            if old_db:
                st.session_state.daily_logs = pd.read_csv(old_db)
                st.success("Database loaded! You can now append today's data.")
                
        with c_save:
            st.markdown("<br>", unsafe_allow_html=True)
            if st.button("💾 Append Today's Log to Database"):
                new_entry = pd.DataFrame({
                    "Date": [log_date], "Cluster": [cluster], "Steam (TPH)": [steam], 
                    "Distillate (TPH)": [distillate], "SW Feed (m3/h)": [sw_feed],
                    "GOR": [round(gor, 2)], "Avg HTC": [round(edited_input['HTC (U)'].mean(), 2)]
                })
                st.session_state.daily_logs = pd.concat([st.session_state.daily_logs, new_entry], ignore_index=True)
                st.success("Log added below!")

        st.markdown("### ✏️ Edit or Delete Entries")
        st.caption("To delete a row, click the checkbox on the far left of the row, then press your 'Delete' or 'Backspace' key.")
        
        # Dynamic Data Editor allows row deletion and editing
        st.session_state.daily_logs = st.data_editor(
            st.session_state.daily_logs, 
            num_rows="dynamic", 
            use_container_width=True
        )
        
        if not st.session_state.daily_logs.empty:
            st.download_button(
                "📥 Download Updated Master Database", 
                st.session_state.daily_logs.to_csv(index=False).encode('utf-8'), 
                f"RIL_Master_Log_{log_date}.csv", 
                "text/csv"
            )

if __name__ == "__main__":
    main()
