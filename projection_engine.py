import streamlit as st
import math
import pandas as pd

st.set_page_config(layout="wide", page_title="RO Projection Engine")

class UtilityProjectionEngine:
    def __init__(self):
        pass

    def calculate_acid_chemistry(self, raw_ph, target_ph, raw_hco3, temp_c):
        if target_ph >= raw_ph or raw_hco3 <= 0:
            return raw_hco3, 0.0, 0.0
            
        T_K = temp_c + 273.15
        pKa1 = (3404.71 / T_K) + 0.032786 * T_K - 14.8435
        
        total_co2 = raw_hco3 * (1.0 + 10**(pKa1 - raw_ph))
        
        target_hco3 = total_co2 / (1.0 + 10**(pKa1 - target_ph))
        hco3_destroyed = max(0.0, raw_hco3 - target_hco3)
        
        acid_dose_ppm = hco3_destroyed * (98.08 / 122.02)
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

            gamma_Ca = get_activity_coef(2, I)
            gamma_HCO3 = get_activity_coef(1, I)

            pHs_lsi = pK2 - pKs - math.log10(molarity['Ca']) - math.log10(molarity['HCO3']) - math.log10(gamma_Ca) - math.log10(gamma_HCO3)
            true_lsi = pH - pHs_lsi

            pCa = -math.log10(molarity['Ca'])
            pAlk = -math.log10(molarity['HCO3'])
            K_stiff_davis = pK2 - pKs + (2.5 * math.sqrt(I) / (1 + 1.5 * math.sqrt(I)))
            true_sdsi = pH - (pCa + pAlk + K_stiff_davis)

            def get_pKsp_apparent(pKsp_pure, z_product, ionic_strength):
                A_dh_const = 0.509
                sqrt_I = math.sqrt(ionic_strength)
                correction = A_dh_const * z_product * (sqrt_I / (1 + 1.4 * sqrt_I))
                return pKsp_pure - correction

            pksp_CaSO4 = 4.62
            pksp_BaSO4 = 9.96
            pksp_SrSO4 = 6.49
            pksp_CaF2  = 10.40

            Ksp_prime_CaSO4 = 10**-(get_pKsp_apparent(pksp_CaSO4, 4, I))
            Ksp_prime_BaSO4 = 10**-(get_pKsp_apparent(pksp_BaSO4, 4, I))
            Ksp_prime_SrSO4 = 10**-(get_pKsp_apparent(pksp_SrSO4, 4, I))
            Ksp_prime_CaF2  = 10**-(get_pKsp_apparent(pksp_CaF2, 2, I)) 

            def calc_si_apparent(m1, m2, ksp_prime, is_fluoride=False):
                if m1 == 0 or m2 == 0: return 0.0
                if is_fluoride:
                    iap = m1 * (m2**2)
                else:
                    iap = m1 * m2
                if iap == 0: return 0.0
                si = math.log10(iap / ksp_prime)
                return max(0.0, si)

            si_CaSO4 = calc_si_apparent(molarity.get('Ca', 0), molarity.get('SO4', 0), Ksp_prime_CaSO4)
            si_BaSO4 = calc_si_apparent(molarity.get('Ba', 0), molarity.get('SO4', 0), Ksp_prime_BaSO4)
            si_SrSO4 = calc_si_apparent(molarity.get('Sr', 0), molarity.get('SO4', 0), Ksp_prime_SrSO4)
            si_CaF2 = calc_si_apparent(molarity.get('Ca', 0), molarity.get('F', 0), Ksp_prime_CaF2, is_fluoride=True)
            
            # --- LINEAR RATIO MINERALS ---
            if temp_c <= 25:
                sio2_limit = 125.0
            elif temp_c <= 30:
                sio2_limit = 125.0 + ((temp_c - 25.0) * ((135.0 - 125.0) / 5.0))
            else:
                sio2_limit = 135.0 + ((temp_c - 30.0) * ((144.8 - 135.0) / 5.0))
                
            si_SiO2 = ions.get('SiO2', 0) / sio2_limit
            si_Fe = ions.get('Fe', 0) / 0.05
            si_Al = ions.get('Al', 0) / 0.05

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

    def calculate_effective_scaling(self, raw_data, product_name, dose_ppm):
        """
        Applies kinetic reduction algorithms to raw thermodynamic data
        based on specific formulation chemistry and active solid limits.
        """
        effective = raw_data.copy()
        
        if product_name == "Kem Watreat R 824":
            # 40% active PAA homopolymer logic
            active_dose = dose_ppm * 0.40
            
            def get_efficiency(raw_si, active_d, ceiling):
                if raw_si <= 0: return 1.0 # Safe
                if raw_si > ceiling: return 0.10 # Homopolymer calcium gel failure
                
                # Sigmoid curve parameters targeting ~3.0 active ppm max
                alpha = 3.0
                gamma = 2.5
                exponent = -((alpha * active_d) - (gamma * raw_si))
                exponent = max(min(exponent, 100), -100) # prevent overflow
                return 1.0 / (1.0 + math.exp(exponent))
            
            # Reduce LSI and SDSI
            eta_lsi = get_efficiency(effective['LSI'], active_dose, ceiling=2.50)
            eta_sdsi = get_efficiency(effective['SDSI'], active_dose, ceiling=2.00)
            
            effective['LSI'] = round(effective['LSI'] * (1 - eta_lsi), 3)
            effective['SDSI'] = round(effective['SDSI'] * (1 - eta_sdsi), 3)
            
            # Moderate reduction for CaSO4 (Max 0.20 offset at 3.2 active ppm)
            if effective['CaSO4'] > 0:
                reduction = min((active_dose / 3.2) * 0.20, 0.20)
                effective['CaSO4'] = round(max(0.0, effective['CaSO4'] - reduction), 3)
                
            # BaSO4, SrSO4, CaF2, SiO2, Fe, Al remain strictly unaffected
            
        return effective

    def render_engine(self):
        # MASSIVE TITLE CHANGE TO PROVE CACHE CLEAR
        st.title("RO Projection Engine - V3 (Kinetic Update)")
        
        tab_inputs, tab_results, tab_report = st.tabs([
            "1. Inputs", "2. Results", "3. Projection Report"
        ])
        
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
                    treated_ph = feed_ph
                    
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

        cf = 1 / (1 - (recovery / 100))
        passage_rate = 1 - (salt_rejection / 100)
        
        treated_feed_ions = feed_ions.copy()
        acid_dose_ppm = 0.0
        
        if auto_acid:
            test_ph = feed_ph
            while test_ph > 4.0:
                adj_hco3, added_so4, dose = self.calculate_acid_chemistry(feed_ph, test_ph, feed_ions.get('HCO3', 0), feed_temp)
                
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
            adj_hco3, added_so4, dose = self.calculate_acid_chemistry(feed_ph, treated_ph, feed_ions.get('HCO3', 0), feed_temp)
            treated_feed_ions['HCO3'] = adj_hco3
            treated_feed_ions['SO4'] = feed_ions.get('SO4', 0) + added_so4
            acid_dose_ppm = dose

        if acid_dose_ppm > 0:
            acid_dose_container.success(f"**Required Acid Dose (100% H2SO4):** {round(acid_dose_ppm, 2)} ppm")
        
        raw_conc_ions = {ion: val * cf for ion, val in feed_ions.items()}
        treated_conc_ions = {ion: val * cf for ion, val in treated_feed_ions.items()}
        perm_ions = {ion: val * passage_rate for ion, val in feed_ions.items()}
        
        raw_conc_ph = feed_ph + math.log10(cf)
        treated_conc_ph = treated_ph + math.log10(cf)
        
        with tab_results:
            st.subheader("Raw Thermodynamic Saturation (Untreated)")
            
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
                
        with tab_report:
            st.subheader("Kinetic Performance & Dosage Projection")
            st.info("Check this tab to see the dynamic performance chart updating in real time.")
            col1, col2 = st.columns(2)
            with col1:
                selected_product = st.selectbox(
                    "Select Antiscalant Formulation", 
                    ["Kem Watreat R 824", "Kem Watreat R 246", "Kem Watreat R 4001", "Kem Watreat R 170", "Kem Watreat R 6863", "Kem Watreat R 6196"]
                )
            with col2:
                manual_dose = st.number_input("Target Dose (ppm) [For Final Report]", min_value=0.0, value=5.0)

            if 'treated_conc_data' in locals() and treated_conc_data:
                st.write(f"### Performance Curve: {selected_product}")
                
                dose_range = [x * 0.5 for x in range(0, 21)] 
                performance_data = []
                
                for d in dose_range:
                    eff_data = self.calculate_effective_scaling(treated_conc_data, selected_product, d)
                    performance_data.append({
                        "Dose (ppm)": d,
                        "Effective LSI": eff_data['LSI'],
                        "Effective SDSI": eff_data['SDSI'],
                        "Effective CaSO4": eff_data['CaSO4']
                    })
                
                df_performance = pd.DataFrame(performance_data)
                
                st.line_chart(
                    df_performance.set_index("Dose (ppm)")[["Effective LSI", "Effective SDSI", "Effective CaSO4"]],
                    use_container_width=True
                )
                
                with st.expander("View Raw Kinetic Data Matrix"):
                    st.dataframe(df_performance, use_container_width=True, hide_index=True)

if __name__ == "__main__":
    app = UtilityProjectionEngine()
    app.render_engine()
