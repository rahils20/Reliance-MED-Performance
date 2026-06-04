import streamlit as st
import math
import pandas as pd

class UtilityProjectionEngine:
    def __init__(self):
        pass

    def calculate_accurate_lsi(self, pH, temp_c, ions):
        """
        Calculates a precise LSI using thermodynamic equilibrium constants 
        and ionic strength activity corrections (Davies Equation).
        """
        try:
            T = temp_c + 273.15  
            A_dh = 0.4918 + 0.0007 * temp_c  
            
            log_K2 = -(2902.39 / T) + 6.498 - (0.02379 * T)
            pK2 = -log_K2
            
            log_Ks = -171.9065 - (0.077993 * T) + (2839.319 / T) + (71.595 * math.log10(T))
            pKs = -log_Ks

            mol_wts = {'Ca': 40.08, 'Mg': 24.31, 'Na': 22.99, 'K': 39.10, 
                       'HCO3': 61.02, 'Cl': 35.45, 'SO4': 96.06}
            
            molarity = {ion: (val / 1000) / mol_wts[ion] for ion, val in ions.items() if ion in mol_wts}
            
            I = 0.5 * (
                molarity.get('Ca', 0)*(2**2) + molarity.get('Mg', 0)*(2**2) + 
                molarity.get('Na', 0)*(1**2) + molarity.get('K', 0)*(1**2) +
                molarity.get('HCO3', 0)*(1**2) + molarity.get('Cl', 0)*(1**2) + 
                molarity.get('SO4', 0)*(2**2)
            )

            def get_activity_coef(charge, ionic_strength):
                if ionic_strength == 0: return 1.0
                log_gamma = -A_dh * (charge**2) * ((math.sqrt(ionic_strength) / (1 + math.sqrt(ionic_strength))) - 0.3 * ionic_strength)
                return 10**log_gamma

            gamma_Ca = get_activity_coef(2, I)
            gamma_HCO3 = get_activity_coef(1, I)

            if molarity.get('Ca', 0) <= 0 or molarity.get('HCO3', 0) <= 0:
                return None

            pHs = pK2 - pKs - math.log10(molarity['Ca']) - math.log10(molarity['HCO3']) - math.log10(gamma_Ca) - math.log10(gamma_HCO3)
            
            lsi = pH - pHs
            
            return {
                "True Ionic Strength": round(I, 4),
                "pK2": round(pK2, 3),
                "pKs": round(pKs, 3),
                "pHs": round(pHs, 3),
                "LSI": round(lsi, 3)
            }
        except Exception as e:
            st.error(f"Calculation error: {e}")
            return None

    def render_engine(self):
        st.header("RO Projection Engine")
        
        # --- Create the Workflow Tabs ---
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
                feed_ph = st.number_input("Feed pH", min_value=1.0, max_value=14.0, value=7.5)
                recovery = st.slider("System Recovery (%)", min_value=10, max_value=95, value=75)
                
            with col2:
                st.write("**Feed Water Ions (ppm / mg/L)**")
                feed_ions = {
                    'Ca': st.number_input("Calcium (Ca2+)", min_value=0.0, value=150.0),
                    'Mg': st.number_input("Magnesium (Mg2+)", min_value=0.0, value=50.0),
                    'Na': st.number_input("Sodium (Na+)", min_value=0.0, value=300.0),
                    'K': st.number_input("Potassium (K+)", min_value=0.0, value=15.0),
                    'HCO3': st.number_input("Bicarbonate (HCO3-)", min_value=0.0, value=250.0),
                    'Cl': st.number_input("Chloride (Cl-)", min_value=0.0, value=400.0),
                    'SO4': st.number_input("Sulfate (SO4 2-)", min_value=0.0, value=200.0)
                }

        # --- BACKGROUND CALCULATIONS ---
        # These run immediately so the downstream tabs have the data ready
        cf = 1 / (1 - (recovery / 100))
        conc_ions = {ion: val * cf for ion, val in feed_ions.items()}
        conc_ph = feed_ph + 0.3 # Empirical estimate for RO concentrate pH shift
        
        # --- TAB 2: RESULTS ---
        with tab_results:
            st.subheader("Saturation Indices")
            st.markdown("Precision thermodynamic calculations based on the Davies Equation.")
            
            feed_results = self.calculate_accurate_lsi(feed_ph, feed_temp, feed_ions)
            conc_results = self.calculate_accurate_lsi(conc_ph, feed_temp, conc_ions)
            
            if feed_results and conc_results:
                col1, col2, col3 = st.columns(3)
                col1.metric(label="Concentration Factor (CF)", value=f"{round(cf, 2)}x")
                col2.metric(label="Feed Water LSI", value=feed_results['LSI'])
                
                delta_color = "inverse" if conc_results['LSI'] > 0 else "normal"
                col3.metric(label="Concentrate LSI", value=conc_results['LSI'], 
                            delta="Scaling Risk" if conc_results['LSI'] > 0 else "Corrosive", 
                            delta_color=delta_color)
                
                st.write("---")
                st.write("**Thermodynamic Engine Breakdown**")
                
                data = {
                    "Metric": ["pH", "Ionic Strength (I)", "Alkalinity Constant (pK2)", "Solubility Constant (pKs)", "Saturation pH (pHs)", "Final LSI"],
                    "Feed Water": [
                        round(feed_ph, 2), 
                        feed_results['True Ionic Strength'], 
                        feed_results['pK2'], 
                        feed_results['pKs'], 
                        feed_results['pHs'], 
                        feed_results['LSI']
                    ],
                    "Concentrate": [
                        round(conc_ph, 2), 
                        conc_results['True Ionic Strength'], 
                        conc_results['pK2'], 
                        conc_results['pKs'], 
                        conc_results['pHs'], 
                        conc_results['LSI']
                    ]
                }
                st.dataframe(pd.DataFrame(data), use_container_width=True)
                
                with st.expander("View Concentrate Ion Profile"):
                    st.json({ion: round(val, 2) for ion, val in conc_ions.items()})
            else:
                st.warning("Please ensure Calcium and Bicarbonate values are greater than zero to calculate LSI.")
                
        # --- TAB 3: PROJECTION REPORT ---
        with tab_report:
            st.subheader("Final Projection Report")
            st.info("The automated product selection and dosing recommendations will populate here based on the results from Tab 2.")
            
            st.write("---")
            st.write("**Manual Overrides**")
            # Setting up the UI shells for the future override logic
            col1, col2 = st.columns(2)
            with col1:
                override_product = st.checkbox("Override Recommended Product")
                if override_product:
                    st.selectbox("Select Manual Product", ["ameROyal 468", "ameROyal 428", "ameROyal 642"])
            with col2:
                override_dose = st.checkbox("Override Recommended Dose")
                if override_dose:
                    st.number_input("Manual Dose (ppm)", min_value=0.0, value=5.0)
