import streamlit as st
import math
import pandas as pd

# FORCE WIDE LAYOUT
st.set_page_config(layout="wide", page_title="RO Projection Engine")

class UtilityProjectionEngine:
    def __init__(self):
        pass

    def calculate_acid_chemistry(self, raw_ph, target_ph, raw_hco3, temp_c):
        """
        Calculates exact stoichiometric H2SO4 dosing required to reach target pH
        using Carbonate Equilibrium (Henderson-Hasselbalch approximation).
        Returns: (New HCO3 ppm, Added SO4 ppm, Acid Dose ppm)
        """
        if target_ph >= raw_ph or raw_hco3 <= 0:
            return raw_hco3, 0.0, 0.0
            
        T_K = temp_c + 273.15
        # Approximate pKa1 for Carbonic Acid based on temperature
        pKa1 = (3404.71 / T_K) + 0.032786 * T_K - 14.8435
        
        # 1. Find Total CO2 species in raw water
        total_co2 = raw_hco3 * (1.0 + 10**(pKa1 - raw_ph))
        
        # 2. Find remaining HCO3 at the new lower pH
        target_hco3 = total_co2 / (1.0 + 10**(pKa1 - target_ph))
        hco3_destroyed = max(0.0, raw_hco3 - target_hco3)
        
        # 3. Stoichiometry: 1 mole H2SO4 (98.08g) neutralizes 2 moles HCO3 (122.02g)
        # leaving behind 1 mole SO4 (96.06g)
        acid_dose_ppm = hco3_destroyed * (98.08 / 122.02) # 100% H2SO4
        so4_added = hco3_destroyed * (96.06 / 122.02)
        
        return target_hco3, so4_added, acid_dose_ppm

    def calculate_scaling_indices(self, pH, temp_c, ions):
        try:
            T_K = temp_c + 273.15  
            A_dh = 0.4918 + 0.0007 * temp_c  
            
            pK2 = (2902.39 / T_K) - 6.498 + (0.02379 * T_K)
            pKs = 171.9065 + (0.077993 * T_K) - (2839.319 / T_K) - (71.595 * math.log10(T_K))

            ion_properties = {
                'Ca': {'mw': 40.08, 'z': 2}, 'Mg': {'mw': 24.31, 'z': 2},
                'Na': {'mw': 22.99, 'z': 1}, 'K':  {'mw': 39.10, 'z': 1},
                'Ba': {'mw': 137.33, 'z': 2}, 'Sr': {'mw': 87.62, 'z': 2},
                'HCO3': {'mw': 61.02, 'z': 1}, 'Cl': {'mw': 35.45, 'z': 1},
                'SO4': {'mw': 96.06, 'z': 2}, 'F':  {'mw': 19.00, 'z': 1},
                'NO3': {'mw': 62.00, 'z': 1}, 'PO4': {'mw': 94.97, 'z': 3},
                'SiO2': {'mw': 60.08, 'z': 0}, 'Fe': {'mw': 55.84, 'z': 2}, 
                'Al': {'mw': 26.98, 'z': 3}
            }
            
            molarity = {}
            for ion, val in ions.items():
                if ion in ion_properties:
                    molarity[ion] = (val / 1000) / ion_properties[ion]['mw']
            
            ionic_sum = sum(molarity[ion] * (ion_properties[ion]['z']**2) for ion in molarity)
            I = 0.5 * ionic_sum

            if molarity.get('Ca', 0) <= 0 or molarity.get('HCO3', 0) <= 0:
                return {"Ionic_Strength": 0.0, "LSI": -5.0, "SDSI": -5.0, "CaSO4": 0.0, "BaSO4": 0.0, "SrSO4": 0.0, "CaF2": 0.0, "SiO2": 0.0, "Fe": 0.0, "Al": 0.0}

            def get_activity_coef(charge, ionic_strength):
                if ionic_strength == 0: return 1.0
                log_gamma = -A_dh * (charge**2) * ((math.sqrt(ionic_strength) / (1 + math.sqrt(ionic_strength))) - 0.3 * ionic_strength)
                return 10**log_gamma

            # --- LSI & SDSI ---
            gamma_Ca = get_activity_coef(2, I)
            gamma_HCO3 = get_activity_coef(1, I)

            pHs_lsi = pK2 - pKs - math.log10(molarity['Ca']) - math.log10(molarity['HCO3']) - math.log10(gamma_Ca) - math.log10(gamma_HCO3)
            true_lsi = pH - pHs_lsi

            pCa = -math.log10(molarity['Ca'])
            pAlk = -math.log10(molarity['HCO3'])
            K_stiff_davis = pK2 - pKs + (2.5 * math.sqrt(I) / (1 + 1.5 * math.sqrt(I)))
            true_sdsi = pH - (pCa + pAlk + K_stiff_davis)

            # --- MINERAL SATURATION INDICES (SI) ---
            ksp_CaSO4 = 2.4e-5
            ksp_BaSO4 = 1.1e-10
            ksp_SrSO4 = 3.2e-7
            ksp_CaF2 = 3.9e-11
            
            gamma_SO4 = get_activity_coef(2, I)
            gamma_Ba = get_activity_coef(2, I)
            gamma_Sr = get_activity_coef(2, I)
            gamma_F = get_activity_coef(1, I)

            def calc_si_activity(m1, g1, m2, g2, ksp, is_fluoride=False):
                if m1 == 0 or m2 == 0: return 0.0
                if is_fluoride:
                    iap = (m1 * g1) * ((m2 * g2)**2)
                else:
                    iap = (m1 * g1) * (m2 * g2)
                
                if iap == 0: return 0.0
                si = math.log10(iap / ksp)
                return max(0.0, si) # Truncate negative (safe) numbers to 0.00

            si_CaSO4 = calc_si_activity(molarity.get('Ca', 0), gamma_Ca, molarity.get('SO4', 0), gamma_SO4, ksp_CaSO4)
            si_BaSO4 = calc_si_activity(molarity.get('Ba', 0), gamma_Ba, molarity.get('SO4', 0), gamma_SO4, ksp_BaSO4)
            si_SrSO4 = calc_si_activity(molarity.get('Sr', 0), gamma_Sr, molarity.get('SO4', 0), gamma_SO4, ksp_SrSO4)
            si_CaF2 = calc_si_activity(molarity.get('Ca', 0), gamma_Ca, molarity.get('F', 0), gamma_F, ksp_CaF2, is_fluoride=True)
            
            # Silica, Iron, Al limits (Simple concentration ratios)
            si_SiO2 = max(0.0, math.log10(ions.get('SiO2', 0.001) / 120.0)) if ions.get('SiO2', 0) > 120 else 0.0
            si_Fe = max(0.0, math.log10(ions.get('Fe', 0.001) / 0.1)) if ions.get('Fe', 0) > 0.1 else 0.0
            si_Al = max(0.0, math.log10(ions.get('Al', 0.001) / 0.05)) if ions.get('Al', 0) > 0.05 else 0.0

            return {
                "Ionic_Strength": round(I, 4),
                "LSI": round(true_lsi, 3),
                "SDSI": round(true_sdsi, 3),
                "CaSO4": round(si_CaSO4, 3),
                "BaSO4": round(si_BaSO4, 3),
                "SrSO4": round(si_SrSO4, 3),
                "CaF2": round(si_CaF2, 3),
                "SiO2": round(si_SiO2, 3),
                "Fe": round(si_Fe, 3),
                "Al": round(si_Al, 3)
            }
            
        except Exception as e:
            return None

    def render_engine(self):
        st.title("RO Projection Engine")
        
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
                salt_rejection = st.slider("Membrane Salt Rejection (%)", min_value=90.0, max_value=99.8, value=99.0, step=0.1)
                
                st.write("**Chemical Pre-Treatment (pH & Acid)**")
                feed_ph = st.number_input("Raw Feed pH", min_value=1.0, max_value=14.0, value=7.5)
                
                auto_acid = st.checkbox("🪄 Auto-Optimize Acid Dosing (Target Concentrate LSI ≤ 2.5)", value=True)
                
                if not auto_acid:
                    treated_ph = st.number_input("Adjusted Feed pH (Manual Acid Dosing)", min_value=1.0, max_value=14.0, value=7.5)
                else:
                    treated_ph = feed_ph # Placeholder until calculation
                    
                # Placeholder for Acid Dose Output
                acid_dose_container = st.empty()
                
                perm_ph = st.number_input("Permeate pH (RO Water)", min_value=1.0, max_value=14.0, value=6.0)
                
            with col2:
                st.write("**Feed Water Ions (ppm / mg/L)**")
                feed_ions = {
                    'Ca': st.number_input("Calcium (Ca2+)", min_value=0.0, value=150.0),
                    'Mg': st.number_input("Magnesium (Mg2+)", min_value=0.0, value=50.0),
                    'Na': st.number_input("Sodium (Na+)", min_value=0.0, value=300.0),
                    'HCO3': st.number_input("Bicarbonate (HCO3-)", min_value=0.0, value=250.0),
                    'Cl': st.number_input("Chloride (Cl-)", min_value=0.0, value=400.0),
                    'SO4': st.number_input("Sulfate (SO4 2-)", min_value=0.0, value=200.0),
                    'Ba': st.number_input("Barium (Ba2+)", min_value=0.0, value=0.05),
                    'Sr': st.number_input("Strontium (Sr2+)", min_value=0.0, value=1.2),
                    'F': st.number_input("Fluoride (F-)", min_value=0.0, value=0.5),
                    'SiO2': st.number_input("Silica (SiO2)", min_value=0.0, value=15.0),
                    'Fe': st.number_input("Iron (Fe)", min_value=0.0, value=0.02),
                    'Al': st.number_input("Aluminium (Al)", min_value=0.0, value=0.01)
                }

        # --- BACKGROUND CALCULATIONS ---
        cf = 1 / (1 - (recovery / 100))
        passage_rate = 1 - (salt_rejection / 100)
        
        treated_feed_ions = feed_ions.copy()
        acid_dose_ppm = 0.0
        
        # Determine Treated pH and Acid Dosing
        if auto_acid:
            test_ph = feed_ph
            while test_ph > 4.0:
                adj_hco3, added_so4, dose = self.calculate_acid_chemistry(feed_ph, test_ph, feed_ions.get('HCO3', 0), feed_temp)
                
                # Test concentrate LSI with this pH drop
                test_conc_ions = {ion: val * cf for ion, val in feed_ions.items()}
                test_conc_ions['HCO3'] = adj_hco3 * cf
                test_conc_ions['SO4'] = (feed_ions.get('SO4', 0) + added_so4) * cf
                
                conc_ph = test_ph + math.log10(cf)
                res = self.calculate_scaling_indices(conc_ph, feed_temp, test_conc_ions)
                
                if res and res['LSI'] <= 2.5:
                    treated_ph = round(test_ph, 2)
                    treated_feed_ions['HCO3'] = adj_hco3
                    treated_feed_ions['SO4'] = feed_ions.get('SO4', 0) + added_so4
                    acid_dose_ppm = dose
                    break
                test_ph -= 0.05
        else:
            # Manual Mode: Calculate acid needed to hit user's target pH
            adj_hco3, added_so4, dose = self.calculate_acid_chemistry(feed_ph, treated_ph, feed_ions.get('HCO3', 0), feed_temp)
            treated_feed_ions['HCO3'] = adj_hco3
            treated_feed_ions['SO4'] = feed_ions.get('SO4', 0) + added_so4
            acid_dose_ppm = dose

        # Display the Acid Dose in the UI
        if acid_dose_ppm > 0:
            acid_dose_container.success(f"**Required Acid Dose (100% H2SO4):** {round(acid_dose_ppm, 2)} ppm")
        
        # Project Downstream Streams
        raw_conc_ions = {ion: val * cf for ion, val in feed_ions.items()}
        treated_conc_ions = {ion: val * cf for ion, val in treated_feed_ions.items()}
        perm_ions = {ion: val * passage_rate for ion, val in feed_ions.items()}
        
        raw_conc_ph = feed_ph + math.log10(cf)
        treated_conc_ph = treated_ph + math.log10(cf)
        
        # --- TAB 2: RESULTS ---
        with tab_results:
            st.subheader("Saturation Index (SI) Report")
            
            feed_data = self.calculate_scaling_indices(feed_ph, feed_temp, feed_ions)
            treated_feed_data = self.calculate_scaling_indices(treated_ph, feed_temp, treated_feed_ions)
            perm_data = self.calculate_scaling_indices(perm_ph, feed_temp, perm_ions) 
            raw_conc_data = self.calculate_scaling_indices(raw_conc_ph, feed_temp, raw_conc_ions)
            treated_conc_data = self.calculate_scaling_indices(treated_conc_ph, feed_temp, treated_conc_ions)
            
            if feed_data and treated_feed_data and perm_data and raw_conc_data and treated_conc_data:
                
                report_data = {
                    "Saturation Index (SI)": ["pH", "Ionic Strength", "LSI", "SDSI", "CaSO4", "BaSO4", "SrSO4", "CaF2", "SiO2", "Iron", "Aluminium"],
                    
                    "Raw Feed": [
                        f"{feed_ph:.2f}", f"{feed_data['Ionic_Strength']:.4f}", f"{feed_data['LSI']:.3f}", f"{feed_data['SDSI']:.3f}", 
                        f"{feed_data['CaSO4']:.3f}", f"{feed_data['BaSO4']:.3f}", f"{feed_data['SrSO4']:.3f}", f"{feed_data['CaF2']:.3f}", 
                        f"{feed_data['SiO2']:.3f}", f"{feed_data['Fe']:.3f}", f"{feed_data['Al']:.3f}"
                    ],
                    
                    "Treated Feed": [
                        f"{treated_ph:.2f}", f"{treated_feed_data['Ionic_Strength']:.4f}", f"{treated_feed_data['LSI']:.3f}", f"{treated_feed_data['SDSI']:.3f}", 
                        f"{treated_feed_data['CaSO4']:.3f}", f"{treated_feed_data['BaSO4']:.3f}", f"{treated_feed_data['SrSO4']:.3f}", f"{treated_feed_data['CaF2']:.3f}", 
                        f"{treated_feed_data['SiO2']:.3f}", f"{treated_feed_data['Fe']:.3f}", f"{treated_feed_data['Al']:.3f}"
                    ],
                    
                    "Permeate": [
                        f"{perm_ph:.2f}", f"{perm_data['Ionic_Strength']:.4f}", f"{perm_data['LSI']:.3f}", f"{perm_data['SDSI']:.3f}", 
                        f"{perm_data['CaSO4']:.3f}", f"{perm_data['BaSO4']:.3f}", f"{perm_data['SrSO4']:.3f}", f"{perm_data['CaF2']:.3f}", 
                        f"{perm_data['SiO2']:.3f}", f"{perm_data['Fe']:.3f}", f"{perm_data['Al']:.3f}"
                    ],
                    
                    "Raw Concentrate": [
                        f"{raw_conc_ph:.2f}", f"{raw_conc_data['Ionic_Strength']:.4f}", f"{raw_conc_data['LSI']:.3f}", f"{raw_conc_data['SDSI']:.3f}", 
                        f"{raw_conc_data['CaSO4']:.3f}", f"{raw_conc_data['BaSO4']:.3f}", f"{raw_conc_data['SrSO4']:.3f}", f"{raw_conc_data['CaF2']:.3f}", 
                        f"{raw_conc_data['SiO2']:.3f}", f"{raw_conc_data['Fe']:.3f}", f"{raw_conc_data['Al']:.3f}"
                    ],
                    
                    "Treated Concentrate": [
                        f"{treated_conc_ph:.2f}", f"{treated_conc_data['Ionic_Strength']:.4f}", f"{treated_conc_data['LSI']:.3f}", f"{treated_conc_data['SDSI']:.3f}", 
                        f"{treated_conc_data['CaSO4']:.3f}", f"{treated_conc_data['BaSO4']:.3f}", f"{treated_conc_data['SrSO4']:.3f}", f"{treated_conc_data['CaF2']:.3f}", 
                        f"{treated_conc_data['SiO2']:.3f}", f"{treated_conc_data['Fe']:.3f}", f"{treated_conc_data['Al']:.3f}"
                    ]
                }
                
                df_report = pd.DataFrame(report_data)
                st.dataframe(df_report, use_container_width=True, hide_index=True)

                st.write("---")
                col_m1, col_m2 = st.columns(2)
                col_m1.metric(label="Concentration Factor (CF)", value=f"{round(cf, 2)}x")
                col_m2.metric(label="Mineral Passage", value=f"{round(passage_rate * 100, 2)}%")
                
            else:
                st.warning("Please ensure Calcium and Bicarbonate values are greater than zero.")
                
        # --- TAB 3: PROJECTION REPORT ---
        with tab_report:
            st.subheader("Final Projection Report")
            col1, col2 = st.columns(2)
            with col1:
                st.selectbox("Select Manual Product", ["ameROyal 468", "ameROyal 428", "ameROyal 642", "ameROyal 363"])
            with col2:
                st.number_input("Manual Dose (ppm)", min_value=0.0, value=5.0)

# Instantiate and run
if __name__ == "__main__":
    app = UtilityProjectionEngine()
    app.render_engine()
