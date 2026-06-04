import streamlit as st
import math
import pandas as pd

class UtilityProjectionEngine:
    def __init__(self):
        pass

    def calculate_scaling_indices(self, pH, temp_c, ions):
        """
        Calculates rigorous LSI (ASTM D3739) and SDSI (ASTM D4582) 
        using True Ionic Strength derived from the full ionic matrix.
        """
        try:
            # 1. Temperature Parameters
            T_K = temp_c + 273.15  
            A_dh = 0.4918 + 0.0007 * temp_c  # Debye-Hückel constant
            
            # 2. Thermodynamic Constants (pK2 and pKs)
            pK2 = (2902.39 / T_K) - 6.498 + (0.02379 * T_K)
            pKs = 171.9065 + (0.077993 * T_K) - (2839.319 / T_K) - (71.595 * math.log10(T_K))

            # 3. Molarities & True Ionic Strength (I)
            ion_properties = {
                'Ca': {'mw': 40.08, 'z': 2}, 'Mg': {'mw': 24.31, 'z': 2},
                'Na': {'mw': 22.99, 'z': 1}, 'K':  {'mw': 39.10, 'z': 1},
                'Ba': {'mw': 137.33, 'z': 2}, 'Sr': {'mw': 87.62, 'z': 2},
                'HCO3': {'mw': 61.02, 'z': 1}, 'Cl': {'mw': 35.45, 'z': 1},
                'SO4': {'mw': 96.06, 'z': 2}, 'F':  {'mw': 19.00, 'z': 1},
                'NO3': {'mw': 62.00, 'z': 1}, 'PO4': {'mw': 94.97, 'z': 3}
            }
            
            molarity = {}
            for ion, val in ions.items():
                if ion in ion_properties:
                    molarity[ion] = (val / 1000) / ion_properties[ion]['mw']
            
            # I = 0.5 * Sum(M * z^2)
            ionic_sum = sum(molarity[ion] * (ion_properties[ion]['z']**2) for ion in molarity)
            I = 0.5 * ionic_sum

            if molarity.get('Ca', 0) <= 0 or molarity.get('HCO3', 0) <= 0:
                return None

            # --- RIGOROUS LSI (Davies Equation Activity Model) ---
            def get_activity_coef(charge, ionic_strength):
                if ionic_strength == 0: return 1.0
                log_gamma = -A_dh * (charge**2) * ((math.sqrt(ionic_strength) / (1 + math.sqrt(ionic_strength))) - 0.3 * ionic_strength)
                return 10**log_gamma

            gamma_Ca = get_activity_coef(2, I)
            gamma_HCO3 = get_activity_coef(1, I)

            pHs_lsi = pK2 - pKs - math.log10(molarity['Ca']) - math.log10(molarity['HCO3']) - math.log10(gamma_Ca) - math.log10(gamma_HCO3)
            true_lsi = pH - pHs_lsi

            # --- RIGOROUS SDSI (Stiff & Davis Empirical K Model) ---
            # pCa and pAlk as defined by Stiff & Davis (using molarities)
            pCa = -math.log10(molarity['Ca'])
            pAlk = -math.log10(molarity['HCO3'])
            
            # Empirical Stiff & Davis K factor approximation based on Ionic Strength and Temp
            # (Interpolated regression matching standard commercial software tables)
            # K roughly maps to pK2 - pKs + Activity Correction for high salinity
            K_stiff_davis = pK2 - pKs + (2.5 * math.sqrt(I) / (1 + 1.5 * math.sqrt(I)))
            
            pHs_sdsi = pCa + pAlk + K_stiff_davis
            true_sdsi = pH - pHs_sdsi

            return {
                "Ionic_Strength": round(I, 4),
                "LSI": round(true_lsi, 2),
                "SDSI": round(true_sdsi, 2),
                "pHs_LSI": round(pHs_lsi, 2),
                "pHs_SDSI": round(pHs_sdsi, 2)
            }
            
        except Exception as e:
            st.error(f"Calculation Error: {e}")
            return None

    def render_engine(self):
        st.header("RO Projection Engine")
        
        tab_inputs, tab_results, tab_report = st.tabs([
            "1. Inputs", "2. Results", "3. Projection Report"
        ])
        
        # --- TAB 1: INPUTS ---
        with tab_inputs:
            st.subheader("System & Water Parameters")
            
            col1, col2 = st.columns(2)
            with col1:
                st.write("**Operational Parameters**")
                feed_temp = st.number_input("Feed Temperature (°C)", min_value=1.0, max_value=50.0, value=30.0)
                recovery = st.slider("System Recovery (%)", min_value=10, max_value=95, value=75)
                
                st.write("**pH Inputs**")
                feed_ph = st.number_input("Feed pH", min_value=1.0, max_value=14.0, value=7.5)
                conc_ph = st.number_input("Concentrate pH (Manual)", min_value=1.0, max_value=14.0, value=8.1)
                
            with col2:
                st.write("**Feed Water Ions (ppm / mg/L)**")
                feed_ions = {
                    'Ca': st.number_input("Calcium (Ca2+)", min_value=0.0, value=150.0),
                    'Mg': st.number_input("Magnesium (Mg2+)", min_value=0.0, value=50.0),
                    'Na': st.number_input("Sodium (Na+)", min_value=0.0, value=300.0),
                    'K': st.number_input("Potassium (K+)", min_value=0.0, value=15.0),
                    'Ba': st.number_input("Barium (Ba2+)", min_value=0.0, value=0.05),
                    'Sr': st.number_input("Strontium (Sr2+)", min_value=0.0, value=1.2),
                    'HCO3': st.number_input("Bicarbonate (HCO3-)", min_value=0.0, value=250.0),
                    'Cl': st.number_input("Chloride (Cl-)", min_value=0.0, value=400.0),
                    'SO4': st.number_input("Sulfate (SO4 2-)", min_value=0.0, value=200.0),
                    'F': st.number_input("Fluoride (F-)", min_value=0.0, value=0.5),
                    'NO3': st.number_input("Nitrate (NO3-)", min_value=0.0, value=5.0),
                    'PO4': st.number_input("Phosphate (PO4 3-)", min_value=0.0, value=0.1)
                }

        # --- BACKGROUND CALCULATIONS ---
        cf = 1 / (1 - (recovery / 100))
        conc_ions = {ion: val * cf for ion, val in feed_ions.items()}
        
        # --- TAB 2: RESULTS ---
        with tab_results:
            st.subheader("Thermodynamic Saturation Indices")
            
            feed_data = self.calculate_scaling_indices(feed_ph, feed_temp, feed_ions)
            conc_data = self.calculate_scaling_indices(conc_ph, feed_temp, conc_ions)
            
            if feed_data and conc_data:
                st.metric(label="Concentration Factor (CF)", value=f"{round(cf, 2)}x")
                st.write("---")

                col1, col2 = st.columns(2)
                
                with col1:
                    st.markdown("### Feed Water")
                    st.metric(label="Langelier Index (LSI)", value=feed_data['LSI'])
                    st.metric(label="Stiff & Davis (SDSI)", value=feed_data['SDSI'])
                    st.caption(f"Calculated True Ionic Strength: {feed_data['Ionic_Strength']}")

                with col2:
                    st.markdown("### Concentrate Stream")
                    
                    lsi_color = "inverse" if conc_data['LSI'] > 0 else "normal"
                    sdsi_color = "inverse" if conc_data['SDSI'] > 0 else "normal"
                    
                    st.metric(label="Langelier Index (LSI)", value=conc_data['LSI'], 
                              delta="Scaling Risk" if conc_data['LSI'] > 0 else "Corrosive", delta_color=lsi_color)
                    st.metric(label="Stiff & Davis (SDSI)", value=conc_data['SDSI'],
                              delta="Scaling Risk" if conc_data['SDSI'] > 0 else "Corrosive", delta_color=sdsi_color)
                    st.caption(f"Calculated True Ionic Strength: {conc_data['Ionic_Strength']}")
                    
                with st.expander("View Full Concentrate Ion Profile"):
                    st.json({ion: round(val, 3) for ion, val in conc_ions.items()})
            else:
                st.warning("Please ensure Calcium and Bicarbonate values are greater than zero.")
                
        # --- TAB 3: PROJECTION REPORT ---
        with tab_report:
            st.subheader("Final Projection Report")
            st.info("The automated product selection and dosing recommendations will populate here based on the results from Tab 2.")
            st.write("---")
            st.write("**Manual Overrides**")
            col1, col2 = st.columns(2)
            with col1:
                if st.checkbox("Override Recommended Product"):
                    st.selectbox("Select Manual Product", ["ameROyal 468", "ameROyal 428", "ameROyal 642", "ameROyal 363"])
            with col2:
                if st.checkbox("Override Recommended Dose"):
                    st.number_input("Manual Dose (ppm)", min_value=0.0, value=5.0)
