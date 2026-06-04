import streamlit as st
import math
import pandas as pd

class UtilityProjectionEngine:
    def __init__(self):
        pass

    def calculate_classic_lsi(self, pH, temp_c, tds, ca_ppm, hco3_ppm):
        """
        Calculates classic Langelier Saturation Index (LSI) 
        using the standard TDS-based A+B-C-D approximation.
        """
        try:
            # Convert to "as CaCO3" equivalents for classic formula
            ca_caco3 = ca_ppm * 2.497
            alk_caco3 = hco3_ppm * 0.8202

            if ca_caco3 <= 0 or alk_caco3 <= 0 or tds <= 0:
                return None

            T_k = temp_c + 273.15
            A = (math.log10(tds) - 1) / 10.0
            B = -13.12 * math.log10(T_k) + 34.55
            C = math.log10(ca_caco3) - 0.4
            D = math.log10(alk_caco3)

            pHs = (9.3 + A + B) - (C + D)
            return round(pH - pHs, 2)
        except Exception:
            return None

    def calculate_sdsi(self, pH, temp_c, ions):
        """
        Calculates Stiff & Davis Stability Index (SDSI) utilizing 
        True Ionic Strength and Davies Equation activity coefficients.
        """
        try:
            T = temp_c + 273.15  
            A_dh = 0.4918 + 0.0007 * temp_c  
            
            log_K2 = -(2902.39 / T) + 6.498 - (0.02379 * T)
            pK2 = -log_K2
            
            log_Ks = -171.9065 - (0.077993 * T) + (2839.319 / T) + (71.595 * math.log10(T))
            pKs = -log_Ks

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
            
            ionic_sum = sum(molarity[ion] * (ion_properties[ion]['z']**2) for ion in molarity)
            I = 0.5 * ionic_sum

            def get_activity_coef(charge, ionic_strength):
                if ionic_strength == 0: return 1.0
                log_gamma = -A_dh * (charge**2) * ((math.sqrt(ionic_strength) / (1 + math.sqrt(ionic_strength))) - 0.3 * ionic_strength)
                return 10**log_gamma

            gamma_Ca = get_activity_coef(2, I)
            gamma_HCO3 = get_activity_coef(1, I)

            if molarity.get('Ca', 0) <= 0 or molarity.get('HCO3', 0) <= 0:
                return None

            pHs = pK2 - pKs - math.log10(molarity['Ca']) - math.log10(molarity['HCO3']) - math.log10(gamma_Ca) - math.log10(gamma_HCO3)
            
            sdsi = pH - pHs
            
            return {
                "Ionic_Strength": round(I, 4),
                "SDSI": round(sdsi, 2)
            }
        except Exception:
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
                # ADDED TDS INPUT HERE
                feed_tds = st.number_input("Feed TDS (ppm)", min_value=1.0, value=1000.0)
                
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
        conc_tds = feed_tds * cf
        conc_ions = {ion: val * cf for ion, val in feed_ions.items()}
        
        # --- TAB 2: RESULTS ---
        with tab_results:
            st.subheader("Saturation Indices")
            
            # Feed Calculations
            feed_lsi = self.calculate_classic_lsi(feed_ph, feed_temp, feed_tds, feed_ions['Ca'], feed_ions['HCO3'])
            feed_sdsi_data = self.calculate_sdsi(feed_ph, feed_temp, feed_ions)
            
            # Concentrate Calculations
            conc_lsi = self.calculate_classic_lsi(conc_ph, feed_temp, conc_tds, conc_ions['Ca'], conc_ions['HCO3'])
            conc_sdsi_data = self.calculate_sdsi(conc_ph, feed_temp, conc_ions)
            
            if feed_sdsi_data and conc_sdsi_data:
                st.metric(label="Concentration Factor (CF)", value=f"{round(cf, 2)}x")
                st.write("---")

                col1, col2 = st.columns(2)
                
                with col1:
                    st.markdown("### Feed Water")
                    st.metric(label="Langelier Index (LSI)", value=feed_lsi)
                    st.metric(label="Stiff & Davis (SDSI)", value=feed_sdsi_data['SDSI'])
                    st.caption(f"Calculated Ionic Strength: {feed_sdsi_data['Ionic_Strength']}")

                with col2:
                    st.markdown("### Concentrate Stream")
                    
                    lsi_color = "inverse" if conc_lsi > 0 else "normal"
                    sdsi_color = "inverse" if conc_sdsi_data['SDSI'] > 0 else "normal"
                    
                    st.metric(label="Langelier Index (LSI)", value=conc_lsi, 
                              delta="Scaling Risk" if conc_lsi > 0 else "Corrosive", delta_color=lsi_color)
                    st.metric(label="Stiff & Davis (SDSI)", value=conc_sdsi_data['SDSI'],
                              delta="Scaling Risk" if conc_sdsi_data['SDSI'] > 0 else "Corrosive", delta_color=sdsi_color)
                    st.caption(f"Calculated Ionic Strength: {conc_sdsi_data['Ionic_Strength']}")
                    
                with st.expander("View Full Concentrate Ion Profile"):
                    st.json({ion: round(val, 3) for ion, val in conc_ions.items()})
            else:
                st.warning("Please ensure Calcium, Bicarbonate, and TDS values are greater than zero.")
                
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
