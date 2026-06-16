import streamlit as st
import math
import pandas as pd
import plotly.express as px
import base64
from datetime import datetime
import os

st.set_page_config(layout="wide", page_title="RO Projection Engine")

class UtilityProjectionEngine:
    def __init__(self):
        if 'ui_ions' not in st.session_state:
            st.session_state.ui_ions = {
                'Ca': 150.0, 'Mg': 50.0, 'Na': 300.0, 'K': 10.0, 'NH4': 0.0, 
                'Ba': 0.05, 'Sr': 1.2, 'Fe': 0.02, 'Al': 0.01,
                'HCO3': 250.0, 'Cl': 400.0, 'SO4': 200.0, 'F': 0.5, 
                'NO3': 5.0, 'PO4': 0.0, 'CO3': 0.0, 'SiO2': 15.0, 'CO2': 5.0
            }
            
        if 'final_product' not in st.session_state:
            st.session_state.final_product = None
        if 'final_dose' not in st.session_state:
            st.session_state.final_dose = 0.0
            
        self.formulations = {
            "Kem Watreat R 824": {"homopolymer": 0.40},
            "Kem Watreat R 246": {"terpolymer": 0.30},
            "Kem Watreat R 428 I": {"homopolymer": 0.10, "hedp": 0.077},
            "Kem Watreat R 4001": {"atmp": 0.0375, "hedp": 0.066},
            "Kem Watreat R 170": {"pbtc": 0.0054, "detmpa": 0.135, "homopolymer": 0.06},
            "Kem Watreat R 6863": {"atmp": 0.1125, "hedp": 0.0275, "copolymer": 0.02},
            "Kem Watreat R 6196": {"atmp": 0.1875, "smbs": 0.03, "homopolymer": 0.04},
            "Kem Watreat R 428 ID": {"hedp": 0.04565, "homopolymer": 0.048},
            "Kem Watreat R 4002": {"hedp": 0.066, "atmp": 0.0175},
            "Kem Watreat R 3687": {"hedp": 0.1375, "pma": 0.0326}
        }

        if 'custom_recipe' in st.session_state:
            self.formulations["Kem Watreat Custom Blend"] = st.session_state.custom_recipe

        self.k_factors = {
            "pbtc":        {"LSI": 3.50, "SDSI": 3.50, "CaCO3": 3.50, "CaSO4": 1.50, "BaSO4": 0.50, "SrSO4": 0.50, "CaF2": 0.50, "Si(OH)4": 0.0,  "SiO2": 0.0,  "CaSiO3": 0.0,  "MgSiO3": 0.0,  "FeSiO3": 0.0,  "Fe": 0.0},
            "detmpa":      {"LSI": 2.20, "SDSI": 2.20, "CaCO3": 2.20, "CaSO4": 3.00, "BaSO4": 4.50, "SrSO4": 4.50, "CaF2": 1.50, "Si(OH)4": 0.0,  "SiO2": 0.0,  "CaSiO3": 0.0,  "MgSiO3": 0.0,  "FeSiO3": 0.0,  "Fe": 0.0},
            "hedp":        {"LSI": 2.65, "SDSI": 2.65, "CaCO3": 2.65, "CaSO4": 2.20, "BaSO4": 0.80, "SrSO4": 0.80, "CaF2": 1.50, "Si(OH)4": 0.0,  "SiO2": 0.0,  "CaSiO3": 0.0,  "MgSiO3": 0.0,  "FeSiO3": 0.0,  "Fe": 0.0},
            "atmp":        {"LSI": 2.80, "SDSI": 2.80, "CaCO3": 2.80, "CaSO4": 1.80, "BaSO4": 1.00, "SrSO4": 1.00, "CaF2": 1.20, "Si(OH)4": 0.0,  "SiO2": 0.0,  "CaSiO3": 0.0,  "MgSiO3": 0.0,  "FeSiO3": 0.0,  "Fe": 0.0},
            "homopolymer": {"LSI": 1.00, "SDSI": 1.00, "CaCO3": 1.00, "CaSO4": 1.50, "BaSO4": 0.50, "SrSO4": 0.50, "CaF2": 0.50, "Si(OH)4": 0.20, "SiO2": 0.20, "CaSiO3": 0.20, "MgSiO3": 0.20, "FeSiO3": 0.20, "Fe": 0.0},
            "copolymer":   {"LSI": 1.40, "SDSI": 1.40, "CaCO3": 1.40, "CaSO4": 2.80, "BaSO4": 3.00, "SrSO4": 3.00, "CaF2": 1.80, "Si(OH)4": 1.50, "SiO2": 1.50, "CaSiO3": 1.50, "MgSiO3": 1.50, "FeSiO3": 1.50, "Fe": 0.0},
            "terpolymer":  {"LSI": 1.88, "SDSI": 1.88, "CaCO3": 1.88, "CaSO4": 2.50, "BaSO4": 2.50, "SrSO4": 2.50, "CaF2": 2.00, "Si(OH)4": 3.50, "SiO2": 3.50, "CaSiO3": 3.50, "MgSiO3": 3.50, "FeSiO3": 3.50, "Fe": 0.0},
            "pma":         {"LSI": 2.50, "SDSI": 2.50, "CaCO3": 2.50, "CaSO4": 3.50, "BaSO4": 1.50, "SrSO4": 1.50, "CaF2": 1.00, "Si(OH)4": 0.0,  "SiO2": 0.0,  "CaSiO3": 0.0,  "MgSiO3": 0.0,  "FeSiO3": 0.0,  "Fe": 0.0},
            "smbs":        {"LSI": 0.00, "SDSI": 0.00, "CaCO3": 0.00, "CaSO4": 0.00, "BaSO4": 0.00, "SrSO4": 0.00, "CaF2": 0.00, "Si(OH)4": 0.0,  "SiO2": 0.0,  "CaSiO3": 0.0,  "MgSiO3": 0.0,  "FeSiO3": 0.0,  "Fe": 0.0}
        }

    def format_sci(self, val):
        if val == 0: return "0.00"
        s = f"{val:.2e}"
        base, exp = s.split('e')
        exp_val = int(exp)
        super_map = {'0': '⁰', '1': '¹', '2': '²', '3': '³', '4': '⁴',
                     '5': '⁵', '6': '⁶', '7': '⁷', '8': '⁸', '9': '⁹', '-': '⁻'}
        exp_str = str(exp_val)
        super_exp = "".join(super_map.get(c, c) for c in exp_str)
        return f"{base} × 10{super_exp}"

    def calculate_acid_chemistry(self, raw_ph, target_ph, raw_hco3, temp_c):
        if target_ph >= raw_ph or raw_hco3 <= 0:
            return raw_hco3, 0.0, 0.0
            
        T_K = temp_c + 273.15
        pKa1 = (3404.71 / T_K) + 0.032786 * T_K - 14.8435
        total_co2 = raw_hco3 * (1.0 + 10**(pKa1 - raw_ph))
        target_hco3 = total_co2 / (1.0 + 10**(pKa1 - target_ph))
        hco3_destroyed = max(0.0, raw_hco3 - target_hco3)
        acid_dose_ppm = (hco3_destroyed * (98.08 / 122.02)) / 0.98
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
                'NH4': {'mw': 18.04, 'z': 1}, 'Ba': {'mw': 137.33, 'z': 2}, 
                'Sr': {'mw': 87.62, 'z': 2}, 'Fe': {'mw': 55.84, 'z': 2}, 
                'Al': {'mw': 26.98, 'z': 3}, 'HCO3': {'mw': 61.02, 'z': 1}, 
                'Cl': {'mw': 35.45, 'z': 1}, 'SO4': {'mw': 96.06, 'z': 2}, 
                'F':  {'mw': 19.00, 'z': 1}, 'NO3': {'mw': 62.00, 'z': 1}, 
                'PO4': {'mw': 94.97, 'z': 3}, 'CO3': {'mw': 60.01, 'z': 2},
                'SiO2': {'mw': 60.08, 'z': 0}, 'CO2': {'mw': 44.01, 'z': 0}
            }
            
            molarity = {}
            for ion, val in ions.items():
                if ion in ion_properties:
                    molarity[ion] = (val / 1000) / ion_properties[ion]['mw']
            
            ionic_sum = sum(molarity[ion] * (ion_properties[ion]['z']**2) for ion in molarity)
            I = 0.5 * ionic_sum

            if molarity.get('Ca', 0) <= 0 or molarity.get('HCO3', 0) <= 0:
                return {
                    "Ionic_Strength": 0.0, "LSI": -5.0, "SDSI": -5.0, "CaCO3": -5.0, "CaSO4": 0.0, 
                    "BaSO4": 0.0, "SrSO4": 0.0, "CaF2": 0.0, "Si(OH)4": 0.0, "SiO2": 0.0,
                    "CaSiO3": 0.0, "MgSiO3": 0.0, "FeSiO3": 0.0, "Fe": 0.0, "Al": 0.0,
                    "IAP_CaSO4": 0.0, "IAP_BaSO4": 0.0, "IAP_SrSO4": 0.0, "IAP_CaF2": 0.0,
                    "IAP_CaSiO3": 0.0, "IAP_MgSiO3": 0.0, "IAP_FeSiO3": 0.0,
                    "IAP_SiOH4": 0.0, "Ksp_SiOH4": 1.0, "Fraction_SiO3": 0.0,
                    "Ratio_CaSO4": 0.0, "Ratio_BaSO4": 0.0, "Ratio_SrSO4": 0.0, "Ratio_CaF2": 0.0,
                    "Ratio_SiOH4": 0.0, "Ratio_CaSiO3": 0.0, "Ratio_MgSiO3": 0.0, "Ratio_FeSiO3": 0.0
                }

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
            pksp_CaSiO3 = 8.00 

            Ksp_prime_CaSO4 = 10**-(get_pKsp_apparent(pksp_CaSO4, 4, I))
            Ksp_prime_BaSO4 = 10**-(get_pKsp_apparent(pksp_BaSO4, 4, I))
            Ksp_prime_SrSO4 = 10**-(get_pKsp_apparent(pksp_SrSO4, 4, I))
            Ksp_prime_CaF2  = 10**-(get_pKsp_apparent(pksp_CaF2, 2, I)) 
            Ksp_prime_CaSiO3 = 10**-(get_pKsp_apparent(pksp_CaSiO3, 4, I))

            def calc_si_and_iap(m1, m2, ksp_prime, is_fluoride=False):
                if m1 == 0 or m2 == 0: return 0.0, 0.0, 0.0
                iap = (m1 * (m2**2)) if is_fluoride else (m1 * m2)
                ratio = iap / ksp_prime if ksp_prime > 0 else 0.0
                si = math.log10(ratio) if ratio > 1.0 else 0.0 
                return max(0.0, si), iap, ratio

            si_CaSO4, iap_CaSO4, ratio_CaSO4 = calc_si_and_iap(molarity.get('Ca', 0), molarity.get('SO4', 0), Ksp_prime_CaSO4)
            si_BaSO4, iap_BaSO4, ratio_BaSO4 = calc_si_and_iap(molarity.get('Ba', 0), molarity.get('SO4', 0), Ksp_prime_BaSO4)
            si_SrSO4, iap_SrSO4, ratio_SrSO4 = calc_si_and_iap(molarity.get('Sr', 0), molarity.get('SO4', 0), Ksp_prime_SrSO4)
            si_CaF2, iap_CaF2, ratio_CaF2 = calc_si_and_iap(molarity.get('Ca', 0), molarity.get('F', 0), Ksp_prime_CaF2, is_fluoride=True)
            
            if temp_c <= 25:
                sio2_limit_ppm = 125.0
            elif temp_c <= 30:
                sio2_limit_ppm = 125.0 + ((temp_c - 25.0) * ((135.0 - 125.0) / 5.0))
            else:
                sio2_limit_ppm = 135.0 + ((temp_c - 30.0) * ((144.8 - 135.0) / 5.0))
                
            sio2_limit_molar = sio2_limit_ppm / 1000 / 60.08
            si_molarity_total = molarity.get('SiO2', 0)
            
            ratio_SiOH4 = si_molarity_total / sio2_limit_molar if sio2_limit_molar > 0 else 0.0
            si_SiO2 = max(0.0, math.log10(ratio_SiOH4)) if ratio_SiOH4 > 1.0 else 0.0
            
            si_Fe = ions.get('Fe', 0) / 0.05
            si_Al = ions.get('Al', 0) / 0.05

            pKa1_si = 9.8
            pKa2_si = 11.8
            denominator = 1.0 + 10**(pH - pKa1_si) + 10**(2*pH - pKa1_si - pKa2_si)
            fraction_SiO3_anion = (10**(2*pH - pKa1_si - pKa2_si)) / denominator
            
            active_sio3_molarity = si_molarity_total * fraction_SiO3_anion

            if pH > 8.0:
                si_CaSiO3, iap_CaSiO3, ratio_CaSiO3 = calc_si_and_iap(molarity.get('Ca', 0), active_sio3_molarity, Ksp_prime_CaSiO3)
                
                Ksp_prime_MgSiO3 = Ksp_prime_CaSiO3 * 1.5 
                Ksp_prime_FeSiO3 = Ksp_prime_CaSiO3 * 0.05
                
                si_MgSiO3, iap_MgSiO3, ratio_MgSiO3 = calc_si_and_iap(molarity.get('Mg', 0), active_sio3_molarity, Ksp_prime_MgSiO3)
                si_FeSiO3, iap_FeSiO3, ratio_FeSiO3 = calc_si_and_iap(molarity.get('Fe', 0), active_sio3_molarity, Ksp_prime_FeSiO3)
            else:
                si_CaSiO3, iap_CaSiO3, ratio_CaSiO3 = 0.0, 0.0, 0.0
                si_MgSiO3, iap_MgSiO3, ratio_MgSiO3 = 0.0, 0.0, 0.0
                si_FeSiO3, iap_FeSiO3, ratio_FeSiO3 = 0.0, 0.0, 0.0

            return {
                "Ionic_Strength": round(I, 4),
                "LSI": round(true_lsi, 3),
                "SDSI": round(true_sdsi, 3),
                "CaCO3": round(true_lsi, 3), 
                "CaSO4": round(si_CaSO4, 3),
                "BaSO4": round(si_BaSO4, 3),
                "SrSO4": round(si_SrSO4, 3),
                "CaF2": round(si_CaF2, 3),
                "Si(OH)4": round(si_SiO2, 3),
                "SiO2": round(si_SiO2, 3),
                "CaSiO3": round(si_CaSiO3, 3),
                "MgSiO3": round(si_MgSiO3, 3),
                "FeSiO3": round(si_FeSiO3, 3),
                "Fe": round(si_Fe, 3),
                "Al": round(si_Al, 3),
                "IAP_CaSO4": iap_CaSO4,
                "IAP_BaSO4": iap_BaSO4,
                "IAP_SrSO4": iap_SrSO4,
                "IAP_CaF2": iap_CaF2,
                "IAP_SiOH4": si_molarity_total,
                "Ksp_SiOH4": sio2_limit_molar,
                "IAP_CaSiO3": iap_CaSiO3,
                "IAP_MgSiO3": iap_MgSiO3,
                "IAP_FeSiO3": iap_FeSiO3,
                "Fraction_SiO3": fraction_SiO3_anion,
                "Ratio_CaSO4": ratio_CaSO4,
                "Ratio_BaSO4": ratio_BaSO4,
                "Ratio_SrSO4": ratio_SrSO4,
                "Ratio_CaF2": ratio_CaF2,
                "Ratio_SiOH4": ratio_SiOH4,
                "Ratio_CaSiO3": ratio_CaSiO3,
                "Ratio_MgSiO3": ratio_MgSiO3,
                "Ratio_FeSiO3": ratio_FeSiO3
            }
            
        except Exception as e:
            return None

    def calculate_effective_scaling(self, raw_data, product_name, dose_ppm):
        effective = raw_data.copy()

        if product_name not in self.formulations:
            return effective

        product_recipe = self.formulations[product_name]
        
        target_salts = ["LSI", "SDSI", "CaCO3", "CaSO4", "BaSO4", "SrSO4", "CaF2", "Si(OH)4", "SiO2", "CaSiO3", "MgSiO3", "FeSiO3", "Fe"]
        
        is_pure_polymer = all(p in ["homopolymer", "copolymer", "terpolymer", "pma", "smbs"] for p in product_recipe)
        
        active_secondary_salts = sum(1 for s in ["Ratio_CaSO4", "Ratio_BaSO4", "Ratio_SrSO4", "Ratio_CaF2", "Ratio_SiOH4"] if raw_data.get(s, 0) > 1.0)
        high_lsi = raw_data.get('LSI', 0) > 1.5
        
        polymer_stress_penalty = 1.0
        if is_pure_polymer and high_lsi and active_secondary_salts > 0:
            polymer_stress_penalty = 0.25 
            
        for salt in target_salts:
            if salt in effective and effective[salt] > 0:
                total_kd = 0.0
                for ingredient, active_pct in product_recipe.items():
                    active_ppm = dose_ppm * active_pct
                    base_k = self.k_factors.get(ingredient, {}).get(salt, 0.0)
                    total_kd += active_ppm * base_k
                
                total_kd *= polymer_stress_penalty
                
                raw_si = effective[salt]
                decay_multiplier = math.exp(-total_kd / (raw_si ** 0.5))
                
                if is_pure_polymer and salt in ["LSI", "SDSI", "CaCO3"]:
                    decay_multiplier = max(decay_multiplier, 0.40)
                    
                effective[salt] = round(raw_si * decay_multiplier, 3)

        return effective

    def run_expert_simulation(self, effective, treated_conc_ions, feed_temp):
        successes = []
        economic_sort_order = [
            "Kem Watreat R 4001", "Kem Watreat R 4002", "Kem Watreat R 428 ID", 
            "Kem Watreat R 428 I", "Kem Watreat R 6196", "Kem Watreat R 824", 
            "Kem Watreat R 246", "Kem Watreat R 6863", "Kem Watreat R 170", "Kem Watreat R 3687"
        ]

        high_lsi_risk = effective.get('LSI', 0) > 2.5

        def get_excess_mass(salt, b_ratio, ions, temp_c, b_si, t_si):
            max_mass = 0.0
            if salt == "CaCO3": max_mass = min(ions.get('Ca',0)/40.08, ions.get('HCO3',0)/61.02) * 100.09 * 0.05
            elif salt == "CaSO4": max_mass = min(ions.get('Ca',0)/40.08, ions.get('SO4',0)/96.06) * 136.14
            elif salt == "BaSO4": max_mass = min(ions.get('Ba',0)/137.33, ions.get('SO4',0)/96.06) * 233.39
            elif salt == "SrSO4": max_mass = min(ions.get('Sr',0)/87.62, ions.get('SO4',0)/96.06) * 183.68
            elif salt == "CaF2": max_mass = min(ions.get('Ca',0)/40.08, (ions.get('F',0)/19.00)/2) * 78.08
            elif salt == "Si(OH)4":
                limit = 125.0
                if temp_c > 25 and temp_c <= 30: limit = 125.0 + ((temp_c - 25.0) * 2.0)
                elif temp_c > 30: limit = 135.0 + ((temp_c - 30.0) * 1.96)
                max_mass = max(0.0, ions.get('SiO2',0) - limit)
            
            # Induction Time Gate: If antiscalant drives SI below threshold, precipitation doesn't occur in RO residence time
            if salt == "CaCO3" and t_si <= 0.4:
                return 0.0
            elif salt != "CaCO3" and t_si <= 0.05:
                return 0.0
            else:
                t_ratio = 10 ** t_si if t_si > 0 else 1.0
                return max_mass * ((t_ratio - 1.0)/t_ratio) if t_ratio > 1.0 else 0.0

        for prod in economic_sort_order:
            recipe = self.formulations.get(prod, {})
            has_polymer = any(p in recipe for p in ["homopolymer", "copolymer", "terpolymer", "pma"])
            is_pure_phos = not has_polymer

            if is_pure_phos and high_lsi_risk:
                continue 

            for dose in [x * 0.5 for x in range(2, 17)]: 
                sim_res = self.calculate_effective_scaling(effective, prod, dose)
                
                lsi_safe = -0.3 <= sim_res.get('LSI', 0) <= 0.3
                sdsi_safe = -0.3 <= sim_res.get('SDSI', 0) <= 0.3
                
                salts_safe = True
                for s_key in ["CaSO4", "BaSO4", "SrSO4", "CaF2", "Si(OH)4"]:
                    si_val = sim_res.get(s_key, 0)
                    if si_val > 0.021:
                        internal_k = f"Ratio_{s_key.replace('Si(OH)4', 'SiOH4')}"
                        b_ratio = 10 ** effective.get(s_key, 0)
                        mass = get_excess_mass(s_key, b_ratio, treated_conc_ions, feed_temp, effective.get(s_key, 0), si_val)
                        
                        if mass > 0.5:
                            if s_key == "CaF2" and si_val <= 1.5:
                                continue
                            salts_safe = False
                            break

                if lsi_safe and sdsi_safe and salts_safe:
                    successes.append({
                        "Product": prod, 
                        "Required Dose (ppm)": dose, 
                        "Final LSI": sim_res.get('LSI', 0)
                    })
                    break 

        return successes

    def render_engine(self):
        st.title("RO Projection Engine")
        
        tab_inputs, tab_results, tab_project_x, tab_report = st.tabs([
            "1. System Configuration", "2. Thermodynamic Profiling", "3. AI Formulation Engine", "4. Engineering Projection Report"
        ])
        
        with tab_inputs:
            st.subheader("System & Water Parameters")
            
            col1, col2, col3 = st.columns([1.2, 1, 1])
            
            with col1:
                st.write("**Operational Parameters**")
                feed_temp = st.number_input("Feed Temperature (°C)", min_value=1.0, max_value=50.0, value=25.0)
                recovery = st.slider("System Recovery (%)", min_value=10, max_value=95, value=75)
                salt_rejection = st.slider("Membrane Salt Rejection (%)", min_value=90.0, max_value=99.8, value=99.0, step=0.1)
                membrane_type = st.selectbox("Membrane Type Selection", ["Standard Brackish Water (BWRO)", "Fouling Resistant (FRRO)", "Seawater (SWRO)"])
                
                st.write("**Chemical Pre-Treatment (pH & Acid)**")
                feed_ph = st.number_input("Raw Feed pH", min_value=1.0, max_value=14.0, value=7.5)
                
                auto_acid = st.checkbox("Auto-Optimize Acid Dosing (Target Concentrate LSI <= 2.5)", value=True)
                
                if not auto_acid:
                    treated_ph = st.number_input("Adjusted Feed pH (Manual Acid Dosing)", min_value=1.0, max_value=14.0, value=7.5)
                else:
                    treated_ph = feed_ph
                    
                acid_dose_container = st.empty()
                perm_ph = st.number_input("Permeate pH (RO Water)", min_value=1.0, max_value=14.0, value=6.0)
                
            with col2:
                st.write("**Cations (mg/L)**")
                st.session_state.ui_ions['Ca'] = st.number_input("Calcium (Ca++)", min_value=0.0, value=float(st.session_state.ui_ions['Ca']))
                st.session_state.ui_ions['Mg'] = st.number_input("Magnesium (Mg++)", min_value=0.0, value=float(st.session_state.ui_ions['Mg']))
                st.session_state.ui_ions['Na'] = st.number_input("Sodium (Na+)", min_value=0.0, value=float(st.session_state.ui_ions['Na']))
                st.session_state.ui_ions['K'] = st.number_input("Potassium (K+)", min_value=0.0, value=float(st.session_state.ui_ions['K']))
                st.session_state.ui_ions['NH4'] = st.number_input("Ammonium (NH4+)", min_value=0.0, value=float(st.session_state.ui_ions['NH4']))
                st.session_state.ui_ions['Ba'] = st.number_input("Barium (Ba++)", min_value=0.0, value=float(st.session_state.ui_ions['Ba']), format="%.4f")
                st.session_state.ui_ions['Sr'] = st.number_input("Strontium (Sr++)", min_value=0.0, value=float(st.session_state.ui_ions['Sr']), format="%.4f")
                st.session_state.ui_ions['Fe'] = st.number_input("Iron (Fe2+/3+)", min_value=0.0, value=float(st.session_state.ui_ions['Fe']), format="%.4f")
                st.session_state.ui_ions['Al'] = st.number_input("Aluminium (Al+++)", min_value=0.0, value=float(st.session_state.ui_ions['Al']), format="%.4f")

            with col3:
                st.write("**Anions & Neutrals (mg/L)**")
                st.session_state.ui_ions['HCO3'] = st.number_input("Bicarbonate (HCO3-)", min_value=0.0, value=float(st.session_state.ui_ions['HCO3']))
                st.session_state.ui_ions['Cl'] = st.number_input("Chloride (Cl-)", min_value=0.0, value=float(st.session_state.ui_ions['Cl']))
                st.session_state.ui_ions['SO4'] = st.number_input("Sulfate (SO4--)", min_value=0.0, value=float(st.session_state.ui_ions['SO4']))
                st.session_state.ui_ions['F'] = st.number_input("Fluoride (F-)", min_value=0.0, value=float(st.session_state.ui_ions['F']), format="%.4f")
                st.session_state.ui_ions['NO3'] = st.number_input("Nitrate (NO3-)", min_value=0.0, value=float(st.session_state.ui_ions['NO3']))
                st.session_state.ui_ions['PO4'] = st.number_input("Phosphate (PO4---)", min_value=0.0, value=float(st.session_state.ui_ions['PO4']))
                st.session_state.ui_ions['CO3'] = st.number_input("Carbonate (CO3--)", min_value=0.0, value=float(st.session_state.ui_ions['CO3']))
                st.session_state.ui_ions['SiO2'] = st.number_input("Silica (SiO2)", min_value=0.0, value=float(st.session_state.ui_ions['SiO2']))
                st.session_state.ui_ions['CO2'] = st.number_input("Carbon Dioxide (CO2)", min_value=0.0, value=float(st.session_state.ui_ions['CO2']))

            calc_ions = st.session_state.ui_ions.copy()

            eq_wt = {
                'Ca': 20.04, 'Mg': 12.15, 'Na': 22.99, 'K': 39.10, 'NH4': 18.04, 
                'Ba': 68.67, 'Sr': 43.81, 'Fe': 27.92, 'Al': 8.99,
                'HCO3': 61.02, 'Cl': 35.45, 'SO4': 48.03, 'F': 19.00, 
                'NO3': 62.00, 'PO4': 31.66, 'CO3': 30.01
            }
            
            cat_keys = ['Ca', 'Mg', 'Na', 'K', 'NH4', 'Ba', 'Sr', 'Fe', 'Al']
            an_keys = ['HCO3', 'Cl', 'SO4', 'F', 'NO3', 'PO4', 'CO3']
            
            cat_meq = sum(calc_ions[k] / eq_wt[k] for k in cat_keys)
            an_meq = sum(calc_ions[k] / eq_wt[k] for k in an_keys)
            
            if cat_meq + an_meq > 0:
                error_pct = ((cat_meq - an_meq) / (cat_meq + an_meq)) * 100
            else:
                error_pct = 0.0
                
            calc_tds = sum(calc_ions.values())
            
            st.write("---")
            st.subheader("Water Profile Verification")
            
            v_col1, v_col2, v_col3, v_col4 = st.columns(4)
            v_col1.metric("Cations (meq/L)", round(cat_meq, 3))
            v_col2.metric("Anions (meq/L)", round(an_meq, 3))
            
            if abs(error_pct) <= 5.0:
                v_col3.success(f"Balance Error: {round(error_pct, 2)}% (OK)")
            else:
                v_col3.error(f"Balance Error: {round(error_pct, 2)}% (Check Imbalance)")
                
            v_col4.metric("Calculated TDS (mg/L)", round(calc_tds, 2))
            
            st.write("**Balance & Scale Adjustments**")
            bal_col1, bal_col2 = st.columns(2)
            with bal_col1:
                auto_balance = st.checkbox("Calculate Na/Cl to Balance", value=(abs(error_pct) > 5.0))
            with bal_col2:
                target_tds = st.number_input("Override Target TDS (mg/L)", min_value=0.0, value=float(round(calc_tds, 2)))
                scale_tds = st.checkbox("Scale all ions proportionally to match Target TDS", value=False)
                
            if auto_balance and abs(error_pct) > 0.01:
                if cat_meq > an_meq:
                    calc_ions['Cl'] += (cat_meq - an_meq) * eq_wt['Cl']
                else:
                    calc_ions['Na'] += (an_meq - cat_meq) * eq_wt['Na']
                    
            if scale_tds and target_tds > 0 and calc_tds > 0:
                new_calc_tds = sum(calc_ions.values())
                multiplier = target_tds / new_calc_tds
                for k in calc_ions:
                    calc_ions[k] *= multiplier

            if st.button("Apply Adjustments to Input Fields"):
                st.session_state.ui_ions = calc_ions.copy()
                st.rerun()

        cf = 1 / (1 - (recovery / 100))
        passage_rate = 1 - (salt_rejection / 100)
        
        treated_feed_ions = calc_ions.copy()
        acid_dose_ppm = 0.0
        
        if auto_acid:
            test_ph = feed_ph
            while test_ph > 4.0:
                adj_hco3, added_so4, dose = self.calculate_acid_chemistry(feed_ph, test_ph, calc_ions.get('HCO3', 0), feed_temp)
                
                test_conc_ions = {ion: val * cf for ion, val in calc_ions.items()}
                test_conc_ions['HCO3'] = adj_hco3 * cf
                test_conc_ions['SO4'] = (calc_ions.get('SO4', 0) + added_so4) * cf
                test_conc_ions['CO2'] = calc_ions.get('CO2', 0) 
                
                conc_ph = test_ph + math.log10(cf)
                res = self.calculate_scaling_indices(conc_ph, feed_temp, test_conc_ions)
                
                if res and res['LSI'] <= 2.5:
                    treated_ph = round(test_ph, 2)
                    treated_feed_ions['HCO3'] = adj_hco3
                    treated_feed_ions['SO4'] = calc_ions.get('SO4', 0) + added_so4
                    acid_dose_ppm = dose
                    break
                test_ph -= 0.05
        else:
            adj_hco3, added_so4, dose = self.calculate_acid_chemistry(feed_ph, treated_ph, calc_ions.get('HCO3', 0), feed_temp)
            treated_feed_ions['HCO3'] = adj_hco3
            treated_feed_ions['SO4'] = calc_ions.get('SO4', 0) + added_so4
            acid_dose_ppm = dose

        if acid_dose_ppm > 0:
            acid_dose_container.success(f"Required Acid Dose (98% H2SO4): {round(acid_dose_ppm, 2)} ppm")
        
        raw_conc_ions = {ion: val * cf for ion, val in calc_ions.items()}
        raw_conc_ions['CO2'] = calc_ions.get('CO2', 0)
        treated_conc_ions = {ion: val * cf for ion, val in treated_feed_ions.items()}
        treated_conc_ions['CO2'] = treated_feed_ions.get('CO2', 0)
        
        perm_ions = {ion: val * passage_rate for ion, val in calc_ions.items()}
        perm_ions['CO2'] = calc_ions.get('CO2', 0)
        
        raw_conc_ph = feed_ph + math.log10(cf)
        treated_conc_ph = treated_ph + math.log10(cf)

        with tab_inputs:
            st.write("---")
            st.write("**System Hydraulic Performance**")
            col_m1, col_m2 = st.columns(2)
            col_m1.metric(label="Concentration Factor (CF)", value=f"{round(cf, 2)}x")
            col_m2.metric(label="Mineral Passage", value=f"{round(passage_rate * 100, 2)}%")
        
        with tab_results:
            if calc_ions.get('Fe', 0) > 0.05 or calc_ions.get('Al', 0) > 0.05:
                st.warning("Pre-Treatment Advisory: Elevated Iron (Fe) or Aluminum (Al) detected. Chemical antiscalants and acid dosing do not dissolve oxidized metals. We highly recommend installing Manganese Greensand, Birm, or coagulation-assisted multimedia filtration prior to the RO unit to prevent severe membrane fouling.")

            st.subheader("System Concentration & Thermodynamic Saturation")
            
            feed_data = self.calculate_scaling_indices(feed_ph, feed_temp, calc_ions)
            treated_feed_data = self.calculate_scaling_indices(treated_ph, feed_temp, treated_feed_ions)
            perm_data = self.calculate_scaling_indices(perm_ph, feed_temp, perm_ions) 
            raw_conc_data = self.calculate_scaling_indices(raw_conc_ph, feed_temp, raw_conc_ions)
            treated_conc_data = self.calculate_scaling_indices(treated_conc_ph, feed_temp, treated_conc_ions)
            
            if feed_data and treated_feed_data and perm_data and raw_conc_data and treated_conc_data:
                
                st.write("**Ion Concentrations (ppm)**")
                display_ions = {
                    'Ca': 'Ca++', 'Mg': 'Mg++', 'Na': 'Na+', 'K': 'K+', 'NH4': 'NH4+', 
                    'Ba': 'Ba++', 'Sr': 'Sr++', 'Fe': 'Fe2+/3+', 'Al': 'Al+++', 
                    'HCO3': 'HCO3-', 'Cl': 'Cl-', 'SO4': 'SO4--', 'F': 'F-', 
                    'NO3': 'NO3-', 'PO4': 'PO4---', 'CO3': 'CO3--', 'SiO2': 'SiO2', 'CO2': 'CO2'
                }
                
                raw_keys = list(display_ions.keys())
                display_keys = list(display_ions.values())
                
                ion_data = {
                    "Ion Species": display_keys,
                    "Raw Feed": [f"{calc_ions.get(k, 0):.2f}" for k in raw_keys],
                    "Treated Feed": [f"{treated_feed_ions.get(k, 0):.2f}" for k in raw_keys],
                    "Permeate": [f"{perm_ions.get(k, 0):.2f}" for k in raw_keys],
                    "Raw Concentrate": [f"{raw_conc_ions.get(k, 0):.2f}" for k in raw_keys],
                    "Treated Concentrate": [f"{treated_conc_ions.get(k, 0):.2f}" for k in raw_keys]
                }
                
                df_ions = pd.DataFrame(ion_data)
                st.dataframe(df_ions, use_container_width=True, hide_index=True)
                
                st.write("---")
                st.write("**Thermodynamic Indicators & Ionic Strength**")
                
                ind_data = {
                    "Parameter": ["LSI (True Value)", "SDSI (True Value)", "Ionic Strength"],
                    "Raw Feed": [f"{feed_data['LSI']:.3f}", f"{feed_data['SDSI']:.3f}", f"{feed_data['Ionic_Strength']:.4f}"],
                    "Treated Feed": [f"{treated_feed_data['LSI']:.3f}", f"{treated_feed_data['SDSI']:.3f}", f"{treated_feed_data['Ionic_Strength']:.4f}"],
                    "Permeate": [f"{perm_data['LSI']:.3f}", f"{perm_data['SDSI']:.3f}", f"{perm_data['Ionic_Strength']:.4f}"],
                    "Raw Concentrate": [f"{raw_conc_data['LSI']:.3f}", f"{raw_conc_data['SDSI']:.3f}", f"{raw_conc_data['Ionic_Strength']:.4f}"],
                    "Treated Concentrate": [f"{treated_conc_data['LSI']:.3f}", f"{treated_conc_data['SDSI']:.3f}", f"{treated_conc_data['Ionic_Strength']:.4f}"]
                }
                
                df_indicators = pd.DataFrame(ind_data)
                st.dataframe(df_indicators, use_container_width=True, hide_index=True)

                st.write("---")
                st.write("**Ion Activity Products (IAP) vs Operational Solubility Limits - Treated Concentrate**")
                st.info("Note: The Operational Limits shown below represent the Apparent Solubility Products (Ksp), mathematically adjusted for the higher salinity (Ionic Strength) inside the RO Concentrate stream.")
                
                iap_data = {
                    "Salt Species": [
                        "CaSO4", "BaSO4", "SrSO4", "CaF2", 
                        "Si(OH)4 (Amorphous)", "CaSiO3 (Ionized)", "MgSiO3 (Ionized)", "FeSiO3 (Ionized)"
                    ],
                    "Cation [ppm]": [
                        f"{treated_conc_ions.get('Ca', 0):.2f}", 
                        f"{treated_conc_ions.get('Ba', 0):.2f}", 
                        f"{treated_conc_ions.get('Sr', 0):.2f}", 
                        f"{treated_conc_ions.get('Ca', 0):.2f}",
                        "N/A",
                        f"{treated_conc_ions.get('Ca', 0):.2f}", 
                        f"{treated_conc_ions.get('Mg', 0):.2f}", 
                        f"{treated_conc_ions.get('Fe', 0):.2f}"
                    ],
                    "Anion [ppm]": [
                        f"{treated_conc_ions.get('SO4', 0):.2f}", 
                        f"{treated_conc_ions.get('SO4', 0):.2f}", 
                        f"{treated_conc_ions.get('SO4', 0):.2f}", 
                        f"{treated_conc_ions.get('F', 0):.2f}",
                        f"{treated_conc_ions.get('SiO2', 0):.2f} (Total)",
                        f"{self.format_sci(treated_conc_ions.get('SiO2', 0) * treated_conc_data['Fraction_SiO3'])} (Active)",
                        f"{self.format_sci(treated_conc_ions.get('SiO2', 0) * treated_conc_data['Fraction_SiO3'])} (Active)",
                        f"{self.format_sci(treated_conc_ions.get('SiO2', 0) * treated_conc_data['Fraction_SiO3'])} (Active)"
                    ],
                    "Active Concentration (IAP / Molarity)": [
                        self.format_sci(treated_conc_data['IAP_CaSO4']), 
                        self.format_sci(treated_conc_data['IAP_BaSO4']), 
                        self.format_sci(treated_conc_data['IAP_SrSO4']), 
                        self.format_sci(treated_conc_data['IAP_CaF2']),
                        self.format_sci(treated_conc_data['IAP_SiOH4']),
                        self.format_sci(treated_conc_data['IAP_CaSiO3']),
                        self.format_sci(treated_conc_data['IAP_MgSiO3']),
                        self.format_sci(treated_conc_data['IAP_FeSiO3'])
                    ],
                    "Operational Limit (Apparent Ksp)": [
                        "Approx Threshold", "Approx Threshold", "Approx Threshold", "Approx Threshold",
                        self.format_sci(treated_conc_data['Ksp_SiOH4']),
                        "Approx Threshold", "Approx Threshold", "Approx Threshold"
                    ]
                }
                
                df_iap = pd.DataFrame(iap_data)
                st.dataframe(df_iap, use_container_width=True, hide_index=True)
                
                st.write("---")
                st.write("**Slightly Soluble Salts (Saturation Index: Ratio of IAP / Limit)**")
                
                salt_keys_display = ["CaSO4", "BaSO4", "SrSO4", "CaF2", "Si(OH)4", "CaSiO3", "MgSiO3", "FeSiO3"]
                salt_keys_internal = ["Ratio_CaSO4", "Ratio_BaSO4", "Ratio_SrSO4", "Ratio_CaF2", "Ratio_SiOH4", "Ratio_CaSiO3", "Ratio_MgSiO3", "Ratio_FeSiO3"]
                
                salt_data = {
                    "Salt Species": salt_keys_display,
                    "Raw Feed": [f"{feed_data[k]:.3f}" for k in salt_keys_internal],
                    "Treated Feed": [f"{treated_feed_data[k]:.3f}" for k in salt_keys_internal],
                    "Permeate": [f"{perm_data[k]:.3f}" for k in salt_keys_internal],
                    "Raw Concentrate": [f"{raw_conc_data[k]:.3f}" for k in salt_keys_internal],
                    "Treated Concentrate": [f"{treated_conc_data[k]:.3f}" for k in salt_keys_internal]
                }
                
                df_salts = pd.DataFrame(salt_data)
                st.dataframe(df_salts, use_container_width=True, hide_index=True)
                
                if treated_conc_ph <= 8.0:
                    st.info("Note: Metal silicates (CaSiO3, MgSiO3, FeSiO3) are reading 0.0 because the treated concentrate pH is 8.0 or below.")
                
                st.write("---")
                st.write("**Scaling Risk & Potential (Treated Concentrate)**")
                
                intensity_data = []
                
                for k in ["LSI", "SDSI"]:
                    val = treated_conc_data[k]
                    intensity = max(0.0, val * 100.0)
                    intensity_data.append({
                        "Salt / Index": k,
                        "Data Value": f"{val:.3f}",
                        "Desired Limit": "0.0",
                        "Intensity_Num": intensity,
                        "Scaling Potential (%)": f"{intensity:.1f}%"
                    })
                    
                for idx, k in enumerate(["CaSO4", "BaSO4", "SrSO4", "CaF2", "SiOH4", "CaSiO3", "MgSiO3", "FeSiO3"]):
                    display_name = salt_keys_display[idx]
                    val = treated_conc_data[salt_keys_internal[idx]]
                    intensity = max(0.0, (val - 1.0) * 100.0)
                    intensity_data.append({
                        "Salt / Index": display_name,
                        "Data Value": f"{val:.3f}",
                        "Desired Limit": "1.0",
                        "Intensity_Num": intensity,
                        "Scaling Potential (%)": f"{intensity:.1f}%"
                    })
                    
                df_intensity = pd.DataFrame(intensity_data)
                st.dataframe(df_intensity.drop(columns=["Intensity_Num"]), use_container_width=True, hide_index=True)
                
                fig_intensity = px.bar(
                    df_intensity,
                    x="Salt / Index",
                    y="Intensity_Num",
                    title="Baseline Scaling Potential Before Antiscalant Addition (%)",
                    labels={"Intensity_Num": "Scaling Potential (%)", "Salt / Index": ""},
                    color="Intensity_Num",
                    color_continuous_scale=["#2ecc71", "#f1c40f", "#e74c3c"]
                )
                
                fig_intensity.update_layout(
                    plot_bgcolor="rgba(0,0,0,0)",
                    paper_bgcolor="rgba(0,0,0,0)",
                    font=dict(family="Arial, sans-serif", size=14, color="#333"),
                    coloraxis_showscale=False,
                    margin=dict(l=40, r=40, t=60, b=40),
                    title_font=dict(size=20, color="#111")
                )
                
                fig_intensity.update_yaxes(showgrid=True, gridcolor="#e0e0e0", zeroline=True, zerolinecolor="#999")
                fig_intensity.update_traces(marker_line_width=0, opacity=0.9, texttemplate='%{y:.1f}%', textposition='outside')
                fig_intensity.add_hline(y=0, line_width=2, line_dash="dash", line_color="#2ecc71", annotation_text="Safe Zone (0%)", annotation_position="top right")
                
                st.plotly_chart(fig_intensity, use_container_width=True)
                
            else:
                st.warning("Please ensure Calcium and Bicarbonate values are greater than zero.")

        with tab_project_x:
            st.subheader("Project X: Formulation Kinetic Efficiency Grid")
            st.warning("Theoretical Matrix Notice: This grid displays isolated, absolute kinetic efficiency for a single foulant in a vacuum. Real-world RO water contains multiple competing salts that stretch chemical efficiency, and high LSI introduces Calcium Phosphonate precipitation limits. Those complex multi-salt interactions are fully calculated and accounted for in the Automated AI section below.")
            st.info("Explore the absolute kinetic efficiency of each product based on its proprietary active raw material blend. This matrix models theoretical inhibition efficiency (%) at escalating saturation intensities.")

            col_px1, col_px2 = st.columns(2)
            with col_px1:
                px_product = st.selectbox("Select Kem Watreat Formulation", list(self.formulations.keys()), key="px_prod")
            with col_px2:
                px_dose = st.slider("Active Product Dose (ppm)", 1.0, 10.0, 5.0, 0.5, key="px_dose")

            formulation = self.formulations[px_product]
            
            cat_map = {
                "LSI": "lsi", "SDSI": "sdsi", "CaSO4": "caso4", 
                "BaSO4": "ba_sr", "SrSO4": "ba_sr", "CaF2": "caf2", 
                "Si(OH)4": "silica", "CaSiO3": "silica", "MgSiO3": "silica", "FeSiO3": "silica"
            }

            target_salts = ["LSI", "SDSI", "CaSO4", "BaSO4", "SrSO4", "CaF2", "Si(OH)4", "CaSiO3", "MgSiO3", "FeSiO3"]

            sum_kd_safe = {}
            for salt in target_salts:
                val = 0.0
                for ing, pct in formulation.items():
                    active_ppm = px_dose * pct
                    val += active_ppm * self.k_factors.get(ing, {}).get(salt, 0.0)
                sum_kd_safe[salt] = val

            si_range = [round(0.5 + i*0.1, 1) for i in range(46)]
            grid_data = []
            
            for raw_si in si_range:
                row = {"Raw Saturation Index (SI)": f"{raw_si:.1f}"}
                for salt in target_salts:
                    kd = sum_kd_safe[salt]
                    efficiency = (1.0 - math.exp(-kd / math.sqrt(raw_si))) * 100
                    row[salt] = efficiency
                grid_data.append(row)

            df_grid = pd.DataFrame(grid_data)
            df_grid.set_index("Raw Saturation Index (SI)", inplace=True)

            def color_cells(val):
                if isinstance(val, str):
                    return ''
                if val >= 80:
                    return 'background-color: #2ecc71; color: black'
                elif val >= 50:
                    return 'background-color: #f1c40f; color: black'
                else:
                    return 'background-color: #e74c3c; color: white'

            try:
                styled_grid = df_grid.style.map(color_cells).format("{:.1f}%")
            except AttributeError:
                styled_grid = df_grid.style.applymap(color_cells).format("{:.1f}%")

            st.dataframe(styled_grid, use_container_width=True, height=600)

            st.markdown("---")
            st.subheader("Automated AI Product Selection & Simulation")
            
            if 'treated_conc_data' in locals() and treated_conc_data:
                successful_options = self.run_expert_simulation(treated_conc_data, treated_conc_ions, feed_temp)
                
                if len(successful_options) > 0:
                    st.success(f"Simulation Complete: Found {len(successful_options)} viable product(s) to maintain the Safe Zone.")
                    df_options = pd.DataFrame(successful_options)
                    st.dataframe(df_options, use_container_width=True, hide_index=True)
                    
                    st.write("---")
                    st.write("**Finalize Engineering Selection**")
                    col_sel1, col_sel2 = st.columns(2)
                    with col_sel1:
                        selected_product_ui = st.selectbox("Select Commercial Product", df_options["Product"].tolist())
                    with col_sel2:
                        default_dose = df_options.loc[df_options["Product"] == selected_product_ui, "Required Dose (ppm)"].values[0]
                        selected_dose_ui = st.number_input("Finalize Dose (ppm)", min_value=1.0, max_value=10.0, value=float(default_dose), step=0.1)
                        
                    if st.button("Finalize and Generate Projection Report"):
                        st.session_state.final_product = selected_product_ui
                        st.session_state.final_dose = selected_dose_ui
                        st.success("Selection saved! Proceed to Tab 4 for the finalized projection.")
                        
                else:
                    st.error("Standard Catalog Limits Exceeded. No standard product can safely maintain this water profile below 8.0 ppm. Custom formulation required.")
                    unlock_code = st.text_input("Enter Admin Override Code for Custom Synthesis", type="password")
                    
                    if unlock_code == "KEMPRO2026":
                        st.success("Admin Override Accepted.")
                        st.write("### Recommended Custom Formulation")
                        
                        high_si = treated_conc_data.get('Ratio_SiOH4', 0) > 1.0
                        high_ba = treated_conc_data.get('Ratio_BaSO4', 0) > 1.0 or treated_conc_data.get('Ratio_SrSO4', 0) > 1.0
                        high_so4 = treated_conc_data.get('Ratio_CaSO4', 0) > 1.5
                        
                        st.write("**Define Custom Synthesis Percentages**")
                        col_c1, col_c2, col_c3, col_c4 = st.columns(4)
                        c_terp = col_c1.number_input("% Terpolymer", min_value=0.0, max_value=100.0, value=20.0 if high_si else 0.0)
                        c_detmpa = col_c2.number_input("% DETMPA", min_value=0.0, max_value=100.0, value=15.0 if high_ba else 0.0)
                        c_pma = col_c3.number_input("% PMA", min_value=0.0, max_value=100.0, value=10.0 if high_so4 else 0.0)
                        c_hedp = col_c4.number_input("% HEDP", min_value=0.0, max_value=100.0, value=10.0)
                        
                        st.write("---")
                        st.write("**Force Custom Selection**")
                        selected_dose_ui = st.number_input("Override Dose (ppm)", value=6.0, step=0.5)
                        
                        if st.button("Force Generate Report"):
                            custom_recipe = {}
                            if c_terp > 0: custom_recipe['terpolymer'] = c_terp / 100.0
                            if c_detmpa > 0: custom_recipe['detmpa'] = c_detmpa / 100.0
                            if c_pma > 0: custom_recipe['pma'] = c_pma / 100.0
                            if c_hedp > 0: custom_recipe['hedp'] = c_hedp / 100.0
                            
                            st.session_state.custom_recipe = custom_recipe
                            st.session_state.final_product = "Kem Watreat Custom Blend" 
                            st.session_state.final_dose = selected_dose_ui
                            st.success("Custom Selection forced. Please refresh or click proceed to view Tab 4.")
                            st.rerun()

        with tab_report:
            st.markdown("""
            <style>
            @media print {
              header, .st-emotion-cache-1avcm0n, .st-emotion-cache-1v0mbdj { display: none !important; }
              .stTabs [data-baseweb="tab-list"] { display: none !important; }
              body { background-color: white !important; }
            }
            </style>
            """, unsafe_allow_html=True)
            
            st.info("To save this report as a PDF: Press Ctrl + P (Windows) or Cmd + P (Mac). The layout has been specially CSS-optimized to print cleanly without menus or sidebars.")
            
            if calc_ions.get('Fe', 0) > 0.05 or calc_ions.get('Al', 0) > 0.05:
                st.warning("Pre-Treatment Advisory: Elevated Iron (Fe) or Aluminum (Al) detected. Chemical antiscalants and acid dosing do not dissolve oxidized metals. We highly recommend installing Manganese Greensand, Birm, or coagulation-assisted multimedia filtration prior to the RO unit to prevent severe membrane fouling.")

            st.subheader("Final Projection Report")
            
            if st.session_state.final_product is None:
                st.warning("Please finalize a product selection in Tab 3 to view the Projection Report.")
            else:
                final_prod = st.session_state.final_product
                final_dose = st.session_state.final_dose
                
                st.success(f"**Generating Final Report For:** {final_prod} at {final_dose} ppm")
                
                if 'treated_conc_data' in locals() and treated_conc_data:
                    
                    # PDF GENERATION LOGIC 
                    try:
                        from fpdf import FPDF
                        import tempfile
                        
                        pdf = FPDF()
                        pdf.add_page()
                        
                        # Chembond Header
                        pdf.set_font("Arial", 'B', 22)
                        pdf.set_text_color(0, 51, 102)
                        pdf.cell(0, 15, "CHEMBOND WATER TECHNOLOGIES", ln=True, align="C")
                        
                        pdf.set_font("Arial", 'B', 16)
                        pdf.set_text_color(50, 50, 50)
                        pdf.cell(0, 10, "RO SYSTEM PROJECTION & TREATMENT PROPOSAL", ln=True, align="C")
                        pdf.ln(5)
                        
                        # Executive Summary
                        pdf.set_font("Arial", 'B', 12)
                        pdf.set_text_color(0, 0, 0)
                        pdf.cell(0, 10, "Executive Summary", ln=True)
                        pdf.set_font("Arial", '', 11)
                        summary_text = (
                            f"Based on the thermodynamic analysis of the supplied water chemistry at a system recovery of {recovery}%, "
                            f"the raw concentrate presents a severe scaling risk. Without intervention, rapid loss of flux and permanent membrane damage is imminent. "
                            f"To safely inhibit precipitation and maximize plant uptime, Chembond Water Technologies recommends dosing {final_dose} ppm of {final_prod}. "
                            f"This proprietary formulation has been simulated to completely suppress the slightly soluble salts and maintain the saturation indices within safe operational parameters."
                        )
                        pdf.multi_cell(0, 7, summary_text)
                        pdf.ln(5)
                        
                        # Basic Info
                        pdf.set_font("Arial", 'B', 12)
                        pdf.cell(0, 10, "Treatment Specification", ln=True)
                        pdf.set_font("Arial", size=11)
                        pdf.cell(0, 8, f"Date Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}", ln=True)
                        pdf.cell(0, 8, f"Selected Membrane Type: {membrane_type}", ln=True)
                        pdf.cell(0, 8, f"Selected Product: {final_prod}", ln=True)
                        pdf.cell(0, 8, f"Recommended Dose: {final_dose} ppm", ln=True)
                        pdf.ln(5)
                        
                        # Data Extraction
                        pdf.set_font("Arial", 'B', 12)
                        pdf.cell(0, 10, "Thermodynamic Projection (Before vs After)", ln=True)
                        
                        eff_data_pdf = self.calculate_effective_scaling(treated_conc_data, final_prod, final_dose)
                        
                        # Indices Table Header
                        pdf.set_fill_color(220, 220, 220)
                        pdf.set_font("Arial", 'B', 11)
                        pdf.cell(60, 10, "Parameter", border=1, fill=True)
                        pdf.cell(65, 10, "Baseline (Raw Ratio/SI)", border=1, fill=True)
                        pdf.cell(65, 10, f"Treated ({final_dose} ppm)", border=1, fill=True, ln=True)
                        
                        # Populate Rows
                        pdf.set_font("Arial", size=11)
                        keys_to_print = ["LSI", "SDSI", "CaCO3", "CaSO4", "BaSO4", "SrSO4", "CaF2", "Si(OH)4", "CaSiO3", "MgSiO3"]
                        
                        def get_excess_mass_pdf(salt, b_ratio, t_ratio, ions, temp_c, b_si, t_si):
                            max_m = 0.0
                            if salt == "CaCO3": max_m = min(ions.get('Ca',0)/40.08, ions.get('HCO3',0)/61.02) * 100.09 * 0.05
                            elif salt == "CaSO4": max_m = min(ions.get('Ca',0)/40.08, ions.get('SO4',0)/96.06) * 136.14
                            elif salt == "BaSO4": max_m = min(ions.get('Ba',0)/137.33, ions.get('SO4',0)/96.06) * 233.39
                            elif salt == "SrSO4": max_m = min(ions.get('Sr',0)/87.62, ions.get('SO4',0)/96.06) * 183.68
                            elif salt == "CaF2": max_m = min(ions.get('Ca',0)/40.08, (ions.get('F',0)/19.00)/2.0) * 78.08
                            elif salt == "Si(OH)4":
                                limit = 125.0
                                if temp_c > 25 and temp_c <= 30: limit = 125.0 + ((temp_c - 25.0) * 2.0)
                                elif temp_c > 30: limit = 135.0 + ((temp_c - 30.0) * 1.96)
                                max_m = max(0.0, ions.get('SiO2',0) - limit)
                            
                            b_m = max_m * ((b_ratio - 1.0)/b_ratio) if b_ratio > 1.0 else 0.0
                            
                            if salt == "CaCO3" and t_si <= 0.4: t_m = 0.0
                            elif salt != "CaCO3" and t_si <= 0.05: t_m = 0.0
                            else: t_m = max_m * ((t_ratio - 1.0)/t_ratio) if t_ratio > 1.0 else 0.0
                            
                            return b_m, t_m

                        for k in keys_to_print:
                            if k in ["LSI", "SDSI"]:
                                b_val = treated_conc_data.get(k, 0)
                                t_val = eff_data_pdf.get(k, b_val)
                            else:
                                internal_key = f"Ratio_{k.replace('Si(OH)4', 'SiOH4')}"
                                b_val = treated_conc_data.get(internal_key, 1.0)
                                b_si = treated_conc_data.get(k, 0)
                                t_si = eff_data_pdf.get(k, b_si)
                                
                                if t_si < b_si and t_si > 0:
                                    t_val = 10 ** t_si
                                elif t_si <= 0:
                                    t_val = 1.0
                                else:
                                    t_val = b_val
                            
                            b_str = f"{b_val:.3f}"
                            t_str = f"{t_val:.3f}"
                            
                            pdf.cell(60, 10, k, border=1)
                            pdf.cell(65, 10, b_str, border=1)
                            pdf.cell(65, 10, t_str, border=1, ln=True)
                            
                        pdf.ln(5)
                        pdf.set_font("Arial", 'I', 9)
                        pdf.cell(0, 5, "Note: Values for Salts (CaSO4, BaSO4, etc.) represent the Ratio of IAP to Solubility Limit.", ln=True)
                        pdf.cell(0, 5, "A ratio greater than 1.0 indicates a scaling risk without intervention.", ln=True)
                        
                        try:
                            import plotly.io as pio
                            mass_pdf_data = []
                                
                            for idx, k in enumerate(["CaCO3", "CaSO4", "BaSO4", "SrSO4", "CaF2", "Si(OH)4"]):
                                internal_k = "Ratio_" + k.replace("Si(OH)4", "SiOH4").replace("CaCO3", "CaCO3") 
                                if k == "CaCO3":
                                    b_si = treated_conc_data.get('LSI', 0)
                                    t_si = eff_data_pdf.get('LSI', 0)
                                    b_ratio = 10 ** b_si if b_si > 0 else 1.0
                                    t_ratio = 10 ** t_si if t_si > 0 else 1.0
                                else:
                                    b_ratio = treated_conc_data.get(internal_k, 1.0)
                                    b_si = treated_conc_data.get(k, 0)
                                    t_si = eff_data_pdf.get(k, b_si)
                                    t_ratio = 10 ** t_si if (t_si < b_si and t_si > 0) else (1.0 if t_si <= 0 else b_ratio)
                                
                                bm, tm = get_excess_mass_pdf(k, b_ratio, t_ratio, treated_conc_ions, feed_temp, b_si, t_si)
                                mass_pdf_data.append({"Salt": k, "Mass": bm, "Type": "Baseline"})
                                mass_pdf_data.append({"Salt": k, "Mass": tm, "Type": "Treated"})
                            
                            df_mpdf = pd.DataFrame(mass_pdf_data)
                            fig_pdf = px.bar(df_mpdf, x="Salt", y="Mass", color="Type", barmode="group", title="Precipitate Mass Risk (ppm)")
                            
                            with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmpfile:
                                fig_pdf.write_image(tmpfile.name, engine="kaleido")
                                pdf.ln(10)
                                pdf.image(tmpfile.name, x=10, w=190)
                        except Exception:
                            pdf.ln(10)
                            pdf.set_font("Arial", 'I', 10)
                            pdf.cell(0, 5, "[Note: Graphical elements omitted. Server requires 'kaleido' library to render chart images in PDF.]", ln=True)

                        pdf_bytes = pdf.output(dest="S").encode("latin-1")
                        
                        st.download_button(
                            label="Download Professional PDF Report",
                            data=pdf_bytes,
                            file_name="Kem_Watreat_Projection.pdf",
                            mime="application/pdf"
                        )
                        
                    except ImportError:
                        st.error("System Dependency Missing: To enable Professional PDF downloads, you must add `fpdf` to your `requirements.txt` file.")
                    
                    st.markdown("---")
                    
                    # ROI & Operational Impact
                    st.write("### Operational Impact & ROI Projection")
                    
                    b_max_mass = 0
                    for k in ["CaCO3", "CaSO4", "BaSO4", "SrSO4", "CaF2", "Si(OH)4"]:
                        if k == "CaCO3": br = 10 ** treated_conc_data.get('LSI', 0)
                        else: br = treated_conc_data.get(f"Ratio_{k.replace('Si(OH)4', 'SiOH4')}", 1.0)
                        if br > 1.0: b_max_mass += br 
                        
                    if b_max_mass > 50: cip_freq = "1 - 2 Weeks"
                    elif b_max_mass > 10: cip_freq = "3 - 4 Weeks"
                    else: cip_freq = "2 - 3 Months"
                    
                    roi_c1, roi_c2 = st.columns(2)
                    roi_c1.info(f"**Baseline (No Treatment)**\n* Estimated CIP Frequency: **Every {cip_freq}**\n* Membrane Degradation Risk: **High**\n* Energy Consumption: **Elevated (Due to scaling differential pressure)**")
                    roi_c2.success(f"**Treated ({final_prod} @ {final_dose} ppm)**\n* Estimated CIP Frequency: **Every 3 - 6 Months**\n* Membrane Lifespan: **Optimal / Maintained**\n* Energy Consumption: **Stable**")

                    st.markdown("---")

                    # 1. Comparative Grouped Bar Chart (Baseline vs Treated)
                    st.write("### Scaling Potential Comparative Analysis")
                    
                    baseline_intensity = []
                    treated_intensity = []
                    
                    eff_data_final = self.calculate_effective_scaling(treated_conc_data, final_prod, final_dose)
                    
                    # LSI / SDSI are true indices (target = 0.0)
                    for k in ["LSI", "SDSI"]:
                        base_val = treated_conc_data[k]
                        treat_val = eff_data_final.get(k, base_val)
                        
                        baseline_intensity.append({
                            "Salt / Index": k,
                            "Intensity_Num": max(0.0, base_val * 100.0)
                        })
                        treated_intensity.append({
                            "Salt / Index": k,
                            "Intensity_Num": max(0.0, treat_val * 100.0)
                        })

                    # Salts must be translated back from SI -> Ratio to display correctly
                    salt_keys_display = ["CaSO4", "BaSO4", "SrSO4", "CaF2", "Si(OH)4", "CaSiO3", "MgSiO3"]
                    salt_keys_internal = ["Ratio_CaSO4", "Ratio_BaSO4", "Ratio_SrSO4", "Ratio_CaF2", "Ratio_SiOH4", "Ratio_CaSiO3", "Ratio_MgSiO3"]
                    
                    for idx, k in enumerate(salt_keys_display):
                        internal_k = salt_keys_internal[idx]
                        
                        base_ratio = treated_conc_data[internal_k]
                        base_si = treated_conc_data.get(k, 0)
                        treat_si = eff_data_final.get(k, base_si)
                        
                        if treat_si < base_si and treat_si > 0:
                            treat_ratio = 10 ** treat_si
                        elif treat_si <= 0:
                            treat_ratio = 1.0
                        else:
                            treat_ratio = base_ratio
                        
                        baseline_intensity.append({
                            "Salt / Index": k,
                            "Intensity_Num": max(0.0, (base_ratio - 1.0) * 100.0)
                        })
                        treated_intensity.append({
                            "Salt / Index": k,
                            "Intensity_Num": max(0.0, (treat_ratio - 1.0) * 100.0)
                        })
                        
                    df_baseline = pd.DataFrame(baseline_intensity)
                    df_baseline["State"] = "Before Treatment"
                    
                    df_treated = pd.DataFrame(treated_intensity)
                    df_treated["State"] = f"With {final_prod} ({final_dose} ppm)"
                    
                    df_combined = pd.concat([df_baseline, df_treated])
                    
                    fig_comp = px.bar(
                        df_combined, x="Salt / Index", y="Intensity_Num", color="State", barmode="group",
                        title=f"Scaling Intensity Before vs After Treatment (%)",
                        labels={"Intensity_Num": "Scaling Potential (%)", "Salt / Index": ""},
                        color_discrete_map={"Before Treatment": "#e74c3c", f"With {final_prod} ({final_dose} ppm)": "#3498db"}
                    )
                    
                    fig_comp.update_layout(
                        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                        font=dict(family="Arial, sans-serif", size=14, color="#333"),
                        margin=dict(l=40, r=40, t=60, b=40),
                        legend_title_text="Treatment State"
                    )
                    fig_comp.update_yaxes(showgrid=True, gridcolor="#e0e0e0", zeroline=True, zerolinecolor="#999")
                    fig_comp.add_hline(y=0, line_width=2, line_dash="dash", line_color="#2ecc71", annotation_text="Safe Zone (0%)", annotation_position="top right")
                    
                    st.plotly_chart(fig_comp, use_container_width=True)
                    
                    st.markdown("---")
                    
                    # 2. Precipitate Mass Comparative Chart
                    st.write("### Scaling Mass Potential (Precipitate at Risk)")
                    st.info("This graph evaluates the stoichiometry of the limiting reactants to determine the physical worst-case mass (mg/L) that could instantaneously precipitate in the concentrate stream. Note: This mass is governed by the saturation thermodynamics, not cumulative deposition over time.")
                    
                    mass_data = []
                    
                    def get_excess_mass(salt, b_ratio, t_ratio, ions, temp_c, b_si, t_si):
                        max_mass = 0.0
                        if salt == "CaCO3": max_mass = min(ions.get('Ca',0)/40.08, ions.get('HCO3',0)/61.02) * 100.09 * 0.05
                        elif salt == "CaSO4": max_mass = min(ions.get('Ca',0)/40.08, ions.get('SO4',0)/96.06) * 136.14
                        elif salt == "BaSO4": max_mass = min(ions.get('Ba',0)/137.33, ions.get('SO4',0)/96.06) * 233.39
                        elif salt == "SrSO4": max_mass = min(ions.get('Sr',0)/87.62, ions.get('SO4',0)/96.06) * 183.68
                        elif salt == "CaF2": max_mass = min(ions.get('Ca',0)/40.08, (ions.get('F',0)/19.00)/2.0) * 78.08
                        elif salt == "Si(OH)4":
                            limit = 125.0
                            if temp_c > 25 and temp_c <= 30: limit = 125.0 + ((temp_c - 25.0) * 2.0)
                            elif temp_c > 30: limit = 135.0 + ((temp_c - 30.0) * 1.96)
                            max_mass = max(0.0, ions.get('SiO2',0) - limit)
                        
                        base_mass = max_mass * ((b_ratio - 1.0)/b_ratio) if b_ratio > 1.0 else 0.0
                        
                        if salt == "CaCO3" and t_si <= 0.4:
                            treat_mass = 0.0
                        elif salt != "CaCO3" and t_si <= 0.05:
                            treat_mass = 0.0
                        else:
                            treat_mass = max_mass * ((t_ratio - 1.0)/t_ratio) if t_ratio > 1.0 else 0.0
                            
                        return base_mass, treat_mass

                    for idx, k in enumerate(["CaCO3", "CaSO4", "BaSO4", "SrSO4", "CaF2", "Si(OH)4"]):
                        if k == "CaCO3":
                            b_si = treated_conc_data.get('LSI', 0)
                            t_si = eff_data_final.get('LSI', 0)
                            b_ratio = 10 ** b_si if b_si > 0 else 1.0
                            t_ratio = 10 ** t_si if t_si > 0 else 1.0
                        else:
                            internal_k = ["Ratio_CaSO4", "Ratio_BaSO4", "Ratio_SrSO4", "Ratio_CaF2", "Ratio_SiOH4"][idx-1]
                            b_ratio = treated_conc_data[internal_k]
                            
                            b_si = treated_conc_data.get(k, 0)
                            t_si = eff_data_final.get(k, b_si)
                            if t_si < b_si and t_si > 0:
                                t_ratio = 10 ** t_si
                            elif t_si <= 0:
                                t_ratio = 1.0
                            else:
                                t_ratio = b_ratio
                            
                        b_mass, t_mass = get_excess_mass(k, b_ratio, t_ratio, treated_conc_ions, feed_temp, b_si, t_si)
                        
                        mass_data.append({"Salt": k, "Mass (ppm)": b_mass, "State": "Before Treatment"})
                        mass_data.append({"Salt": k, "Mass (ppm)": t_mass, "State": f"With {final_prod} ({final_dose} ppm)"})
                        
                    df_mass = pd.DataFrame(mass_data)
                    fig_mass = px.bar(
                        df_mass, x="Salt", y="Mass (ppm)", color="State", barmode="group",
                        title="Worst-Case Precipitate Mass at Risk (ppm)",
                        color_discrete_map={"Before Treatment": "#e67e22", f"With {final_prod} ({final_dose} ppm)": "#27ae60"}
                    )
                    fig_mass.update_layout(
                        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                        font=dict(family="Arial, sans-serif", size=14, color="#333"),
                        margin=dict(l=40, r=40, t=60, b=40)
                    )
                    fig_mass.update_yaxes(showgrid=True, gridcolor="#e0e0e0")
                    fig_mass.add_hline(y=1.0, line_dash="dash", line_color="red", annotation_text="Dangerous Mass Threshold (>1.0 ppm)", annotation_position="top left")
                    st.plotly_chart(fig_mass, use_container_width=True)
                    
                    st.markdown("---")
                    
                    # 3. Line Chart Performance Matrix
                    st.write(f"### Extended Performance Curve Matrix: {final_prod}")
                    dose_range = [x * 0.5 for x in range(0, 21)] 
                    performance_data = []
                    
                    # Completely extracted Iron from graph keys
                    graph_keys = ["LSI", "SDSI", "CaCO3", "CaSO4", "BaSO4", "SrSO4", "CaF2", "Si(OH)4", "SiO2", "CaSiO3", "MgSiO3"]
                    
                    for d in dose_range:
                        eff_data = self.calculate_effective_scaling(treated_conc_data, final_prod, d)
                        row_data = {"Dose (ppm)": d}
                        for k in graph_keys:
                            if k in eff_data:
                                row_data[k] = eff_data[k]
                        performance_data.append(row_data)
                    
                    df_performance = pd.DataFrame(performance_data)
                    
                    fig = px.line(
                        df_performance,
                        x="Dose (ppm)",
                        y=[col for col in df_performance.columns if col != "Dose (ppm)"],
                        labels={
                            "value": "Effective Saturation Index (SI)",
                            "variable": "Mineral Species",
                            "Dose (ppm)": "Product Dose (ppm)"
                        }
                    )
                    
                    fig.update_layout(
                        title=f"Scaling Suppression Projection: {final_prod}",
                        hovermode="x unified",
                        legend_title_text="Mineral Indices",
                        height=600,
                        margin=dict(l=20, r=20, t=50, b=20)
                    )
                    
                    fig.add_hline(y=0, line_dash="dash", line_color="green", annotation_text="Safe Zone")
                    # Highlight the finalized dose on the graph
                    fig.add_vline(x=final_dose, line_dash="dash", line_color="red", annotation_text=f"Selected Dose ({final_dose} ppm)")

                    st.plotly_chart(fig, use_container_width=True)
                    
                    with st.expander("View Comprehensive Kinetic Matrix"):
                        st.dataframe(df_performance, use_container_width=True, hide_index=True)

if __name__ == "__main__":
    app = UtilityProjectionEngine()
    app.render_engine()
