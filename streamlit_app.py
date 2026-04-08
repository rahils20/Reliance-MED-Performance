# requirements: pandas, numpy, python-docx, altair
import streamlit as st
import pandas as pd
import numpy as np
import datetime
import io
import altair as alt
from docx import Document
from docx.shared import RGBColor

st.set_page_config(page_title="Chembond | MED-4 Management", layout="wide")

# --- INITIALIZE SESSION STATE ---
if 'daily_logs' not in st.session_state:
    st.session_state.daily_logs = pd.DataFrame(columns=[
        "Date", "Gross Prod (m3/h)", "Desal (m3/h)", "Steam (TPH)", "SW Feed (m3/h)", "GOR", "Overall HTC", "Residual"
    ])

# --- CONSTANTS & MRA COEFFICIENTS ---
LATENT_HEAT_STEAM_KJ_KG = 2260.0 

# 2026 OLS Regression Coefficients
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

# --- DOCUMENT GENERATOR ---
def generate_word_report(date, actual_gross, predicted_gross, residual, gor, stec, variance_df):
    doc = Document()
    doc.add_heading('MED-4 Daily Operational Performance Report', 0)
    
    p = doc.add_paragraph()
    p.add_run('Prepared by: ').bold = True
    p.add_run('Chembond Chemicals Ltd.\n')
    p.add_run('Client: ').bold = True
    p.add_run('Reliance Industries Limited (RIL)\n')
    p.add_run('Date: ').bold = True
    p.add_run(str(date))
    
    doc.add_heading('1. Executive Summary', level=1)
    doc.add_paragraph(f"On {date}, the MED-4 unit achieved a Gain Output Ratio (GOR) of {gor:.2f}:1 and a Specific Thermal Energy Consumption (STEC) of {stec:.2f} kWh/ton.")
    
    doc.add_heading('2. Multiple Regression Analysis (MRA) & Fouling Indicator', level=1)
    mra_p = doc.add_paragraph()
    mra_p.add_run(f"Actual Gross Production: {actual_gross:.1f} m³/h\n")
    mra_p.add_run(f"MRA Predicted Gross Production: {predicted_gross:.1f} m³/h\n")
    res_run = mra_p.add_run(f"Calculated Residual: {residual:.1f} m³/h\n")
    res_run.bold = True
    
    if residual < -15.0: doc.add_paragraph("WARNING: A significant negative residual indicates potential thermal resistance (fouling) forming on the tube bundles.", style='BodyText')
    elif residual > 15.0: doc.add_paragraph("NOTE: Positive residual indicates the plant is over-performing the baseline model.", style='BodyText')
    else: doc.add_paragraph("STATUS: The plant is operating perfectly within the normalized thermodynamic baseline.", style='BodyText')
    
    doc.add_heading('3. Parameter Variance Impact Matrix', level=2)
    table = doc.add_table(rows=1, cols=6)
    table.style = 'Table Grid'
    hdr_cells = table.rows[0].cells
    headers = ['Parameter', 'Baseline', 'Live Input', 'Deviation', 'Weight', 'Impact']
    for i, h in enumerate(headers): hdr_cells[i].text = h
    
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
    st.sidebar.markdown("### 🔹 CHEMBOND CHEMICALS LTD.") 
    st.sidebar.divider()
    
    st.sidebar.header("📅 Daily Setup")
    log_date = st.sidebar.date_input("Date", datetime.date.today())
    area_m2 = st.sidebar.number_input("Overall Surface Area (m²)", value=1757.49)
    
    st.title("🏭 Reliance MED-4 Management Suite")
    
    tabs = st.tabs(["🌊 1. SCADA Inputs & KPIs", "🔥 2. Thermo & HTC", "🧪 3. Water Analysis", "🧠 4. MRA Root Cause", "📂 5. Enterprise Reporting"])

    # ==========================================
    # GLOBAL VARIABLES (Calculated in Tab 1, used everywhere)
    # ==========================================
    with tabs[0]:
        st.subheader("Daily Mass Balance (Raw SCADA Inputs)")
        
        c1, c2, c3 = st.columns(3)
        steam = c1.number_input("LP Steam (TPH)", value=73.0)
        desal = c2.number_input("Desal Production (m³/h)", value=740.0)
        gross_prod = c3.number_input("Gross Production (m³/h)", value=790.0)
        
        c4, c5, c6 = st.columns(3)
        sw_upper = c4.number_input("Sea Water Upper (Flow to 1st Effect)", value=775.0)
        sw_total = c5.number_input("Total Sea Water Feed (m³/h)", value=2100.0)
        brine_return = c6.number_input("Brine Water Return (m³/h)", value=1250.0)

        st.divider()
        st.subheader("📊 Executive Plant KPIs")
        
        gor = gross_prod / steam if steam > 0 else 0
        heat_load_kw = ((steam * 1000) / 3600) * LATENT_HEAT_STEAM_KJ_KG
        stec = heat_load_kw / desal if desal > 0 else 0
        recovery = (gross_prod / sw_total) * 100 if sw_total > 0 else 0
        conversion = desal / sw_total if sw_total > 0 else 0
        steam_economy = steam / desal if desal > 0 else 0
        
        kpi1, kpi2, kpi3, kpi4, kpi5 = st.columns(5)
        kpi1.metric("GOR", f"{gor:.2f}:1")
        kpi2.metric("Steam Economy", f"{steam_economy:.4f}")
        kpi3.metric("System Recovery", f"{recovery:.1f} %")
        kpi4.metric("Conversion Ratio", f"{conversion:.3f}")
        kpi5.metric("STEC", f"{stec:.1f} kWh/t")

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
        htc_u = 0
        if dt1 > 0 and dt2 > 0 and dt1 != dt2:
            lmtd = (dt1 - dt2) / np.log(dt1 / dt2)
            q_actual = sw_total * (brine_out_t - sw_in_t) * 0.930
            htc_u = (q_actual / (area_m2 * lmtd)) * 1000 if lmtd > 0 else 0
            fouling_factor = 1 / htc_u if htc_u > 0 else 0
            
            r1, r2, r3, r4 = st.columns(4)
            r1.metric("LMTD", f"{lmtd:.2f} °C")
            r2.metric("Plant Q (Actual)", f"{q_actual:,.0f} Kcal/hr°C")
            r3.metric("Overall HTC (U)", f"{htc_u:.2f} W/m²K")
            r4.metric("Fouling Factor (1/U)", f"{fouling_factor:.6f}")

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
        
        # The Alarms
        warning_triggered = False
        for index, row in edited_input.iterrows():
            if row['ΔT (°C)'] > 2.0:
                st.error(f"🚨 **{row['Effect ID']} ALERT:** ΔT is {row['ΔT (°C)']:.2f}°C (Exceeds 2.0°C Limit)")
                warning_triggered = True
        if not warning_triggered: st.success("✅ All 11 effects are safely below the 2.0°C ΔT limit.")
            
        # The New Visual Graph (Replaces repetitive table)
        st.markdown("### 📈 Effect-wise ΔT Profile vs. Design Limit")
        
        # Altair chart with a red threshold line
        edited_input['Effect ID'] = pd.Categorical(edited_input['Effect ID'], categories=effects, ordered=True)
        base_chart = alt.Chart(edited_input).encode(x=alt.X('Effect ID', sort=effects, title=None))
        bar_chart = base_chart.mark_bar(color='#1f77b4', cornerRadiusTopLeft=3, cornerRadiusTopRight=3).encode(y=alt.Y('ΔT (°C)', title='Delta T (°C)'))
        limit_line = alt.Chart(pd.DataFrame({'y': [2.0]})).mark_rule(color='red', strokeDash=[5, 5], strokeWidth=2).encode(y='y')
        
        st.altair_chart(bar_chart + limit_line, use_container_width=True)

    # ==========================================
    # TAB 3: WATER ANALYSIS COMPLIANCE
    # ==========================================
    with tabs[2]:
        st.subheader("Laboratory Analysis")
        # (Water analysis content remains standard)
        st.info("Water quality inputs hidden for brevity in this step.")

    # ==========================================
    # TAB 4: MRA & RESIDUAL ANALYSIS 
    # ==========================================
    with tabs[3]:
        st.subheader("Performance Normalization")
        
        controls_col, calc_col = st.columns([1, 2])
        with controls_col:
            p_press = st.slider("1st Effect Press (mbar)", 200.0, 260.0, 240.0)
            p_t1 = st.slider("1st Effect Temp (°C)", 60.0, 75.0, 69.5)
            p_sw_up = st.slider("Sea Water Upper (m³/h)", 400.0, 1000.0, float(sw_upper))
            p_bt1 = st.slider("1st Brine Temp (°C)", 60.0, 75.0, 66.5)
            p_bflow = st.slider("Brine Flow (m³/h)", 1000.0, 1600.0, float(brine_return))
            p_stm = st.slider("LP Steam (TPH)", 50.0, 100.0, float(steam))
            p_stm_t = st.slider("Steam Temp (°C)", 160.0, 190.0, 179.0)

        with calc_col:
            predicted = (
                MRA_COEF["Intercept"] + (MRA_COEF["Press_1st"] * p_press) + (MRA_COEF["Temp_1st"] * p_t1) +
                (MRA_COEF["SW_Upper"] * p_sw_up) + (MRA_COEF["Brine_Temp_1st"] * p_bt1) +
                (MRA_COEF["Brine_Flow"] * p_bflow) + (MRA_COEF["LP_Steam"] * p_stm) + (MRA_COEF["Steam_Temp"] * p_stm_t)
            )
            residual = gross_prod - predicted
            
            k1, k2, k3 = st.columns(3)
            k1.metric("Actual Gross SCADA", f"{gross_prod:,.1f} m³/h")
            k2.metric("MRA Predicted", f"{predicted:,.1f} m³/h")
            
            if residual < -15.0: k3.error(f"Residual: {residual:,.1f} (FOULING)")
            elif residual > 15.0: k3.success(f"Residual: {residual:,.1f} (CLEAN)")
            else: k3.info(f"Residual: {residual:,.1f} (NORMAL)")
                
            var_data = []
            for name, key, live_val in [("1st Effect Press", "Press_1st", p_press), ("1st Effect Temp", "Temp_1st", p_t1), ("Sea Water Upper", "SW_Upper", p_sw_up), ("1st Brine Temp", "Brine_Temp_1st", p_bt1), ("Brine Flow", "Brine_Flow", p_bflow), ("LP Steam", "LP_Steam", p_stm), ("Steam Temp", "Steam_Temp", p_stm_t)]:
                base = MRA_BASELINE[key]
                dev = live_val - base
                var_data.append([name, base, live_val, dev, MRA_COEF[key], dev * MRA_COEF[key]])
            variance_df = pd.DataFrame(var_data, columns=["Parameter", "Baseline", "Live Input", "Deviation", "Regression Weight", "Impact (TPH)"])

    # ==========================================
    # TAB 5: ENTERPRISE REPORTING SUITE
    # ==========================================
    with tabs[4]:
        st.subheader("Performance Intelligence & Reporting")
        
        # Sub-tabs for different timeframes
        rep_tabs = st.tabs(["📅 Today's Log", "📆 Monthly Dashboard", "📊 Quarterly Analysis", "📈 Yearly Summary"])
        
        # --- 1. TODAY'S DATA ---
        with rep_tabs[0]:
            st.markdown("### Today's Operational Snapshot")
            
            # Display current un-saved data
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Date", str(log_date))
            col2.metric("Gross Prod", f"{gross_prod} m³/h")
            col3.metric("GOR", f"{gor:.2f}")
            col4.metric("MRA Residual", f"{residual:.1f}")
            
            st.divider()
            
            c_save, c_report = st.columns(2)
            with c_save:
                if st.button("💾 Append Today's Data to Master Database", use_container_width=True):
                    new_log = pd.DataFrame({
                        "Date": [pd.to_datetime(log_date)], "Gross Prod (m3/h)": [gross_prod],
                        "Desal (m3/h)": [desal], "Steam (TPH)": [steam],
                        "SW Feed (m3/h)": [sw_total], "GOR": [round(gor, 2)], 
                        "Overall HTC": [round(htc_u, 2)], "Residual": [round(residual, 1)]
                    })
                    st.session_state.daily_logs = pd.concat([st.session_state.daily_logs, new_log], ignore_index=True)
                    st.success("Successfully written to memory!")
            
            with c_report:
                if st.button("📄 Generate RIL Daily Executive Report (.docx)", use_container_width=True):
                    word_file = generate_word_report(log_date, gross_prod, predicted, residual, gor, stec, variance_df)
                    st.download_button("📥 Download Report", data=word_file, file_name=f"MED4_Daily_Report_{log_date}.docx", mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document")

        # --- 2. MONTHLY DATA ---
        with rep_tabs[1]:
            st.markdown("### Monthly Aggregation & Review")
            st.caption("Averages below are calculated dynamically from your saved database for the current month.")
            
            if not st.session_state.daily_logs.empty:
                # Ensure Date is datetime for filtering
                df_logs = st.session_state.daily_logs.copy()
                df_logs['Date'] = pd.to_datetime(df_logs['Date'])
                
                # Filter for current month selected in the sidebar
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
            st.markdown("*(Click the checkbox on the left of any row and press 'Delete' to remove mistakes)*")
            st.session_state.daily_logs = st.data_editor(st.session_state.daily_logs, num_rows="dynamic", use_container_width=True)
            
            if not st.session_state.daily_logs.empty:
                csv_export = st.session_state.daily_logs.to_csv(index=False).encode('utf-8')
                st.download_button("📥 Download Master Log (CSV)", data=csv_export, file_name=f"MED4_Master_Database.csv", mime='text/csv')

        # --- 3. QUARTERLY DATA ---
        with rep_tabs[2]:
            st.markdown("### Quarterly Long-Term Trends")
            st.markdown("In the finalized cloud version, this tab will aggregate 90-day periods to show **Cumulative Recovery Loss** and long-term MRA Fouling trend lines, exactly as required for the Management Review.")
            
            if not st.session_state.daily_logs.empty:
                df_logs = st.session_state.daily_logs.copy()
                df_logs['Date'] = pd.to_datetime(df_logs['Date'])
                # Group by Quarter
                df_logs['Quarter'] = df_logs['Date'].dt.to_period('Q')
                quarterly_avg = df_logs.groupby('Quarter')[['GOR', 'Residual', 'Overall HTC']].mean().reset_index()
                st.dataframe(quarterly_avg.style.format({"GOR": "{:.2f}", "Residual": "{:.1f}", "Overall HTC": "{:.0f}"}), use_container_width=True)
            else:
                st.info("Save more data to view quarterly trends.")

        # --- 4. YEARLY DATA ---
        with rep_tabs[3]:
            st.markdown("### Yearly Performance Assessment")
            st.markdown("This tab will serve as the engine for your **Two-Year Performance Review** documents, comparing historical Clean-Plant performance to current-year averages.")
            
            if not st.session_state.daily_logs.empty:
                df_logs = st.session_state.daily_logs.copy()
                df_logs['Date'] = pd.to_datetime(df_logs['Date'])
                df_logs['Year'] = df_logs['Date'].dt.year
                yearly_avg = df_logs.groupby('Year')[['Gross Prod (m3/h)', 'GOR', 'Residual']].mean().reset_index()
                st.dataframe(yearly_avg.style.format({"Gross Prod (m3/h)": "{:.0f}", "GOR": "{:.2f}", "Residual": "{:.1f}"}), use_container_width=True)
            else:
                st.info("Save more data to view yearly trends.")

if __name__ == "__main__":
    main()
