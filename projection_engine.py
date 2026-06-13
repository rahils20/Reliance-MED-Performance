import streamlit as st
import math
import pandas as pd
import plotly.express as px

st.set_page_config(layout="wide", page_title="RO Projection Engine")

class UtilityProjectionEngine:
    def __init__(self):
        # Initialize default ions in session state to allow UI updates
        if 'ui_ions' not in st.session_state:
            st.session_state.ui_ions = {
                'Ca': 150.0, 'Mg': 50.0, 'Na': 300.0, 'K': 10.0, 'NH4': 0.0, 
                'Ba': 0.05, 'Sr': 1.2, 'Fe': 0.02, 'Al': 0.01,
                'HCO3': 250.0, 'Cl': 400.0, 'SO4': 200.0, 'F': 0.5, 
                'NO3': 5.0, 'PO4': 0.0, 'CO3': 0.0, 'SiO2': 15.0, 'CO2': 5.0
            }
            
        # Project X Formulation Database (Active Solids)
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

        # Project X Kinetic k-Factor Matrix
        self.k_factors = {
            "pbtc":        {"lsi": 4.5, "sdsi": 4.5, "caso4": 1.0, "ba_sr": 0.5, "silica": 0.0, "caf2": 0.0, "fe": 0.5},
            "detmpa":      {"lsi": 2.0, "sdsi": 2.0, "caso4": 3.5, "ba_sr": 5.0, "silica": 2.5, "caf2": 0.0, "fe": 4.0},
            "hedp":        {"lsi": 3.0, "sdsi": 3.0, "caso4": 2.5, "ba_sr": 1.5, "silica": 0.0, "caf2": 0.0, "fe": 2.0},
            "atmp":        {"lsi": 3.5, "sdsi": 3.5, "caso4": 2.0, "ba_sr": 2.0, "silica": 0.0, "caf2": 1.0, "fe": 2.5},
            "homopolymer": {"lsi": 2.5, "sdsi": 2.5, "caso4": 0.5, "ba_sr": 0.0, "silica": 0.0, "caf2": 1.0, "fe": 0.0},
            "copolymer":   {"lsi": 2.0, "sdsi": 2.0, "caso4": 2.8, "ba_sr": 2.5, "silica": 1.5, "caf2": 1.0, "fe": 1.5},
            "terpolymer":  {"lsi": 1.5, "sdsi": 1.5, "caso4": 3.0, "ba_sr": 2.0, "silica": 4.0, "caf2": 1.5, "fe": 3.0},
            "pma":         {"lsi": 3.8, "sdsi": 3.8, "caso4": 3.5, "ba_sr": 2.0, "silica": 0.0, "caf2": 2.0, "fe": 1.0},
            "smbs":        {"lsi": 0.0, "sdsi": 0.0, "caso4": 0.0, "ba_sr": 0.0, "silica": 0.0, "caf2": 0.0, "fe": 0.0}
        }

    def format_sci(self, val):
        """Formats numbers to standard scientific notation using Unicode superscripts"""
        if val == 0: return "0.00"
        s = f"{val:.2e}"
        base, exp = s.split('e')
        exp_val = int(exp)
        
        # Map for Unicode superscripts
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

            # Standard Thermodynamic Ksps
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
            
            # --- SILICA SPECIATION & SOLUBILITY LOGIC ---
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
        
        if product_name == "Kem Watreat R 824":
            active_dose = dose_ppm * 0.40
            if effective['LSI'] > 0:
                k_lsi = 1.6 / (effective['LSI'] ** 0.5)
                eta_lsi = 1.0 - math.exp(-k_lsi * active_dose)
                effective['LSI'] = round(effective['LSI'] * (1.0 - eta_lsi), 3)
                effective['CaCO3'] = effective['LSI']
            if effective['SDSI'] > 0:
                k_sdsi = 1.6 / (effective['SDSI'] ** 0.5)
                eta_sdsi = 1.0 - math.exp(-k_sdsi * active_dose)
                effective['SDSI'] = round(effective['SDSI'] * (1.0 - eta_sdsi), 3)
            if effective['CaSO4'] > 0:
                reduction = min((active_dose / 3.2) * 0.20, 0.20)
                effective['CaSO4'] = round(max(0.0, effective['CaSO4'] - reduction), 3)

        elif product_name == "Kem Watreat R 246":
            active_dose = dose_ppm * 0.30
            if effective['LSI'] > 0:
                k_lsi = 1.0 / (effective['LSI'] ** 0.5)
                eta_lsi = 1.0 - math.exp(-k_lsi * active_dose)
                effective['LSI'] = round(effective['LSI'] * (1.0 - eta_lsi), 3)
                effective['CaCO3'] = effective['LSI']
            if effective['SDSI'] > 0:
                k_sdsi = 1.0 / (effective['SDSI'] ** 0.5)
                eta_sdsi = 1.0 - math.exp(-k_sdsi * active_dose)
                effective['SDSI'] = round(effective['SDSI'] * (1.0 - eta_sdsi), 3)
            for sulfate in ['CaSO4', 'BaSO4', 'SrSO4']:
                if effective[sulfate] > 0:
                    k_sulf = 1.2 / (effective[sulfate] ** 0.5)
                    eta_sulf = 1.0 - math.exp(-k_sulf * active_dose)
                    effective[sulfate] = round(effective[sulfate] * (1.0 - eta_sulf), 3)
            for silica in ['Si(OH)4', 'SiO2', 'CaSiO3', 'MgSiO3', 'FeSiO3']:
                if effective[silica] > 0:
                    k_si = 1.5 / (effective[silica] ** 0.5)
                    eta_si = 1.0 - math.exp(-k_si * active_dose)
                    effective[silica] = round(effective[silica] * (1.0 - eta_si), 3)
            if effective['Fe'] > 0:
                k_fe = 1.8 / (effective['Fe'] ** 0.5)
                eta_fe = 1.0 - math.exp(-k_fe * active_dose)
                effective['Fe'] = round(effective['Fe'] * (1.0 - eta_fe), 3)
            if effective['CaF2'] > 0:
                reduction = min((active_dose / 3.0) * 0.15, 0.15)
                effective['CaF2'] = round(max(0.0, effective['CaF2'] - reduction), 3)

        elif product_name == "Kem Watreat R 428 I":
            active_polymer = dose_ppm * 0.10
            active_hedp = dose_ppm * 0.077
            total_active = active_polymer + active_hedp
            if effective['LSI'] > 0:
                k_lsi = 2.0 / (effective['LSI'] ** 0.5)
                eta_lsi = 1.0 - math.exp(-k_lsi * total_active)
                effective['LSI'] = round(effective['LSI'] * (1.0 - eta_lsi), 3)
                effective['CaCO3'] = effective['LSI']
            if effective['SDSI'] > 0:
                k_sdsi = 2.0 / (effective['SDSI'] ** 0.5)
                eta_sdsi = 1.0 - math.exp(-k_sdsi * total_active)
                effective['SDSI'] = round(effective['SDSI'] * (1.0 - eta_sdsi), 3)
            if effective['CaSO4'] > 0:
                k_caso4 = 1.5 / (effective['CaSO4'] ** 0.5)
                eta_caso4 = 1.0 - math.exp(-k_caso4 * total_active)
                effective['CaSO4'] = round(effective['CaSO4'] * (1.0 - eta_caso4), 3)
            for sulf in ['BaSO4', 'SrSO4']:
                if effective[sulf] > 0:
                    k_heavy_sulf = 0.8 / (effective[sulf] ** 0.5)
                    eta_heavy_sulf = 1.0 - math.exp(-k_heavy_sulf * active_hedp)
                    effective[sulf] = round(effective[sulf] * (1.0 - eta_heavy_sulf), 3)
            if effective['Fe'] > 0:
                k_fe = 1.0 / (effective['Fe'] ** 0.5)
                eta_fe = 1.0 - math.exp(-k_fe * active_hedp)
                effective['Fe'] = round(effective['Fe'] * (1.0 - eta_fe), 3)

        elif product_name == "Kem Watreat R 4001":
            active_atmp = dose_ppm * 0.0375
            active_hedp = dose_ppm * 0.066
            total_active_phos = active_atmp + active_hedp
            if effective['LSI'] > 0:
                k_lsi = 2.5 / (effective['LSI'] ** 0.5)
                eta_lsi = 1.0 - math.exp(-k_lsi * total_active_phos)
                effective['LSI'] = round(effective['LSI'] * (1.0 - eta_lsi), 3)
                effective['CaCO3'] = effective['LSI']
            if effective['SDSI'] > 0:
                k_sdsi = 2.5 / (effective['SDSI'] ** 0.5)
                eta_sdsi = 1.0 - math.exp(-k_sdsi * total_active_phos)
                effective['SDSI'] = round(effective['SDSI'] * (1.0 - eta_sdsi), 3)
            if effective['CaSO4'] > 0:
                k_caso4 = 1.8 / (effective['CaSO4'] ** 0.5)
                eta_caso4 = 1.0 - math.exp(-k_caso4 * total_active_phos)
                effective['CaSO4'] = round(effective['CaSO4'] * (1.0 - eta_caso4), 3)
            for sulf in ['BaSO4', 'SrSO4']:
                if effective[sulf] > 0:
                    k_heavy_sulf = 1.0 / (effective[sulf] ** 0.5)
                    eta_heavy_sulf = 1.0 - math.exp(-k_heavy_sulf * total_active_phos)
                    effective[sulf] = round(effective[sulf] * (1.0 - eta_heavy_sulf), 3)
            if effective['Fe'] > 0:
                k_fe = 1.5 / (effective['Fe'] ** 0.5)
                eta_fe = 1.0 - math.exp(-k_fe * active_hedp)
                effective['Fe'] = round(effective['Fe'] * (1.0 - eta_fe), 3)

        elif product_name == "Kem Watreat R 170":
            active_pbtc = dose_ppm * 0.0054
            active_detmpa = dose_ppm * 0.135
            active_poly = dose_ppm * 0.06
            total_active = active_pbtc + active_detmpa + active_poly
            if effective['LSI'] > 0:
                k_lsi = 2.2 / (effective['LSI'] ** 0.5)
                eta_lsi = 1.0 - math.exp(-k_lsi * total_active)
                effective['LSI'] = round(effective['LSI'] * (1.0 - eta_lsi), 3)
                effective['CaCO3'] = effective['LSI']
            if effective['SDSI'] > 0:
                k_sdsi = 2.2 / (effective['SDSI'] ** 0.5)
                eta_sdsi = 1.0 - math.exp(-k_sdsi * total_active)
                effective['SDSI'] = round(effective['SDSI'] * (1.0 - eta_sdsi), 3)
            if effective['CaSO4'] > 0:
                k_caso4 = 2.0 / (effective['CaSO4'] ** 0.5)
                eta_caso4 = 1.0 - math.exp(-k_caso4 * active_detmpa)
                effective['CaSO4'] = round(effective['CaSO4'] * (1.0 - eta_caso4), 3)
            for sulf in ['BaSO4', 'SrSO4']:
                if effective[sulf] > 0:
                    k_heavy_sulf = 1.8 / (effective[sulf] ** 0.5)
                    eta_heavy_sulf = 1.0 - math.exp(-k_heavy_sulf * active_detmpa)
                    effective[sulf] = round(effective[sulf] * (1.0 - eta_heavy_sulf), 3)
            if effective['Fe'] > 0:
                k_fe = 1.6 / (effective['Fe'] ** 0.5)
                eta_fe = 1.0 - math.exp(-k_fe * active_detmpa)
                effective['Fe'] = round(effective['Fe'] * (1.0 - eta_fe), 3)
            for silica in ['Si(OH)4', 'SiO2', 'CaSiO3', 'MgSiO3', 'FeSiO3']:
                if effective[silica] > 0:
                    k_si = 2.5 / (effective[silica] ** 0.5)
                    eta_si = 1.0 - math.exp(-k_si * total_active)
                    effective[silica] = round(effective[silica] * (1.0 - eta_si), 3)

        elif product_name == "Kem Watreat R 6863":
            total_active = (dose_ppm * 0.040) + (dose_ppm * 0.050) + (dose_ppm * 0.080)
            if effective['LSI'] > 0:
                effective['LSI'] = round(effective['LSI'] * math.exp(-(2.3 / (effective['LSI'] ** 0.5)) * total_active), 3)
                effective['CaCO3'] = effective['LSI']
            if effective['CaSO4'] > 0:
                effective['CaSO4'] = round(effective['CaSO4'] * math.exp(-(1.6 / (effective['CaSO4'] ** 0.5)) * total_active), 3)
            for sulf in ['BaSO4', 'SrSO4']:
                if effective[sulf] > 0:
                    effective[sulf] = round(effective[sulf] * math.exp(-(1.2 / (effective[sulf] ** 0.5)) * total_active), 3)

        elif product_name == "Kem Watreat R 6196":
            total_active = (dose_ppm * 0.050) + (dose_ppm * 0.100) 
            if effective['LSI'] > 0:
                effective['LSI'] = round(effective['LSI'] * math.exp(-(2.1 / (effective['LSI'] ** 0.5)) * total_active), 3)
                effective['CaCO3'] = effective['LSI']
            if effective['CaSO4'] > 0:
                effective['CaSO4'] = round(effective['CaSO4'] * math.exp(-(1.2 / (effective['CaSO4'] ** 0.5)) * total_active), 3)

        elif product_name == "Kem Watreat R 428 ID":
            total_active = (dose_ppm * 0.060) + (dose_ppm * 0.080)
            if effective['LSI'] > 0:
                effective['LSI'] = round(effective['LSI'] * math.exp(-(1.9 / (effective['LSI'] ** 0.5)) * total_active), 3)
                effective['CaCO3'] = effective['LSI']
            if effective['CaSO4'] > 0:
                effective['CaSO4'] = round(effective['CaSO4'] * math.exp(-(1.4 / (effective['CaSO4'] ** 0.5)) * total_active), 3)

        elif product_name == "Kem Watreat R 4002":
            total_active = (dose_ppm * 0.030) + (dose_ppm * 0.040) + (dose_ppm * 0.080)
            if effective['LSI'] > 0:
                effective['LSI'] = round(effective['LSI'] * math.exp(-(2.6 / (effective['LSI'] ** 0.5)) * total_active), 3)
                effective['CaCO3'] = effective['LSI']
            if effective['CaSO4'] > 0:
                effective['CaSO4'] = round(effective['CaSO4'] * math.exp(-(1.7 / (effective['CaSO4'] ** 0.5)) * total_active), 3)

        elif product_name == "Kem Watreat R 3687":
            total_active = (dose_ppm * 0.150) + (dose_ppm * 0.050)
            if effective['LSI'] > 0:
                effective['LSI'] = round(effective['LSI'] * math.exp(-(2.8 / (effective['LSI'] ** 0.5)) * total_active), 3)
                effective['CaCO3'] = effective['LSI']
            if effective['CaSO4'] > 0:
                effective['CaSO4'] = round(effective['CaSO4'] * math.exp(-(2.2 / (effective['CaSO4'] ** 0.5)) * total_active), 3)
            for sulf in ['BaSO4', 'SrSO4']:
                if effective[sulf] > 0:
                    effective[sulf] = round(effective[sulf] * math.exp(-(1.5 / (effective[sulf] ** 0.5)) * (dose_ppm * 0.050)), 3)
            
        return effective

    def render_engine(self):
        st.title("RO Projection Engine")
        
        tab_inputs, tab_results, tab_project_x, tab_report = st.tabs([
            "1. Inputs", "2. Results", "3. Project X Matrix", "4. Projection Report"
        ])
        
        with tab_inputs:
            st.subheader("System & Water Parameters")
            
            col1, col2, col3 = st.columns([1.2, 1, 1])
            
            with col1:
                st.write("**Operational Parameters**")
                feed_temp = st.number_input("Feed Temperature (°C)", min_value=1.0, max_value=50.0, value=25.0)
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
                auto_balance = st.checkbox("⚖️ Calculate Na/Cl to Balance", value=(abs(error_pct) > 5.0))
            with bal_col2:
                target_tds = st.number_input("Override Target TDS (mg/L)", min_value=0.0, value=float(round(calc_tds, 2)))
                scale_tds = st.checkbox("📈 Scale all ions proportionally to match Target TDS", value=False)
                
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

            if st.button("⚡ Apply Adjustments to Input Fields"):
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
            acid_dose_container.success(f"**Required Acid Dose (100% H2SO4):** {round(acid_dose_ppm, 2)} ppm")
        
        raw_conc_ions = {ion: val * cf for ion, val in calc_ions.items()}
        treated_conc_ions = {ion: val * cf for ion, val in treated_feed_ions.items()}
        perm_ions = {ion: val * passage_rate for ion, val in calc_ions.items()}
        
        raw_conc_ph = feed_ph + math.log10(cf)
        treated_conc_ph = treated_ph + math.log10(cf)

        with tab_inputs:
            st.write("---")
            st.write("**System Hydraulic Performance**")
            col_m1, col_m2 = st.columns(2)
            col_m1.metric(label="Concentration Factor (CF)", value=f"{round(cf, 2)}x")
            col_m2.metric(label="Mineral Passage", value=f"{round(passage_rate * 100, 2)}%")
        
        with tab_results:
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
                st.info("ℹ️ **Note:** The *Operational Limits* shown below represent the Apparent Solubility Products (Ksp), mathematically adjusted for the higher salinity (Ionic Strength) inside the RO Concentrate stream.")
                
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
                        f"{(treated_conc_ions.get('SiO2', 0) * treated_conc_data['Fraction_SiO3']):.4e} (Active)",
                        f"{(treated_conc_ions.get('SiO2', 0) * treated_conc_data['Fraction_SiO3']):.4e} (Active)",
                        f"{(treated_conc_ions.get('SiO2', 0) * treated_conc_data['Fraction_SiO3']):.4e} (Active)"
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
                    st.info("ℹ️ **Note:** Metal silicates ($CaSiO_3$, $MgSiO_3$, $FeSiO_3$) are reading 0.0 because the treated concentrate pH is 8.0 or below.")
                
                st.write("---")
                st.write("**Scaling Risk & Potential (Treated Concentrate)**")
                
                intensity_data = []
                
                # Math for LSI / SDSI (Desired = 0.0)
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
                    
                # Math for Salts (Desired Ratio = 1.0)
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
                
                # Upgraded Plotly Chart Styling
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

        # ==========================================
        # TAB 3: PROJECT X KINETIC GRID
        # ==========================================
        with tab_project_x:
            st.subheader("Project X: Formulation Kinetic Efficiency Grid")
            st.info("Explore the absolute kinetic efficiency of each product based on its proprietary active raw material blend. This matrix models theoretical inhibition efficiency (%) at escalating saturation intensities.")

            col_px1, col_px2 = st.columns(2)
            with col_px1:
                px_product = st.selectbox("Select Kem Watreat Formulation", list(self.formulations.keys()), key="px_prod")
            with col_px2:
                px_dose = st.slider("Active Product Dose (ppm)", 1.0, 10.0, 5.0, 0.5, key="px_dose")

            formulation = self.formulations[px_product]
            
            # Map Table Columns to K-Factor Categories
            cat_map = {
                "LSI": "lsi", "SDSI": "sdsi", "CaSO4": "caso4", 
                "BaSO4": "ba_sr", "SrSO4": "ba_sr", "CaF2": "caf2", 
                "Si(OH)4": "silica", "CaSiO3": "silica", "MgSiO3": "silica", "FeSiO3": "silica"
            }

            # Pre-calculate the total sum of (active_ppm * k_factor) for this specific dose
            sum_kd = {}
            for cat in set(cat_map.values()):
                val = 0.0
                for ing, pct in formulation.items():
                    active_ppm = px_dose * pct
                    val += active_ppm * self.k_factors[ing][cat]
                sum_kd[cat] = val

            # Build the Dataframe (Rows = SI Levels from 0.5 to 5.0)
            si_range = [round(0.5 + i*0.1, 1) for i in range(46)]
            grid_data = []
            
            for raw_si in si_range:
                row = {"Raw Saturation Index (SI)": f"{raw_si:.1f}"}
                for col_name, cat in cat_map.items():
                    kd = sum_kd[cat]
                    # Inverse Square Root Decay Law
                    efficiency = (1.0 - math.exp(-kd / math.sqrt(raw_si))) * 100
                    row[col_name] = efficiency
                grid_data.append(row)

            df_grid = pd.DataFrame(grid_data)
            df_grid.set_index("Raw Saturation Index (SI)", inplace=True)

            # Map colors based on percentage to bypass Matplotlib background_gradient completely
            def color_cells(val):
                if isinstance(val, str):
                    return ''
                if val >= 80:
                    return 'background-color: #2ecc71; color: black'
                elif val >= 50:
                    return 'background-color: #f1c40f; color: black'
                else:
                    return 'background-color: #e74c3c; color: white'

            # Use .map or .applymap safely depending on pandas version
            try:
                styled_grid = df_grid.style.map(color_cells).format("{:.1f}%")
            except AttributeError:
                styled_grid = df_grid.style.applymap(color_cells).format("{:.1f}%")

            st.dataframe(styled_grid, use_container_width=True, height=600)

        with tab_report:
            st.subheader("Kinetic Performance & Dosage Projection")
            st.info("Review product performance below to track chemical suppression trends. Double-click an item in the legend to isolate it.")
            
            col1, col2 = st.columns(2)
            with col1:
                selected_product = st.selectbox(
                    "Select Antiscalant Formulation", 
                    ["Kem Watreat R 824", "Kem Watreat R 246", "Kem Watreat R 428 I", "Kem Watreat R 4001", "Kem Watreat R 170", "Kem Watreat R 6863", "Kem Watreat R 6196", "Kem Watreat R 428 ID", "Kem Watreat R 4002", "Kem Watreat R 3687"]
                )
            with col2:
                manual_dose = st.number_input("Target Dose (ppm) [For Final Report]", min_value=0.0, value=5.0)

            if 'treated_conc_data' in locals() and treated_conc_data:
                st.write(f"### Performance Curve Matrix: {selected_product}")
                
                dose_range = [x * 0.5 for x in range(0, 21)] 
                performance_data = []
                
                graph_keys = ["LSI", "SDSI", "CaCO3", "CaSO4", "BaSO4", "SrSO4", "CaF2", "Si(OH)4", "SiO2", "CaSiO3", "MgSiO3", "FeSiO3", "Fe"]
                
                for d in dose_range:
                    eff_data = self.calculate_effective_scaling(treated_conc_data, selected_product, d)
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
                    title=f"Scaling Suppression Projection: {selected_product}",
                    hovermode="x unified",
                    legend_title_text="Mineral Indices",
                    height=600,
                    margin=dict(l=20, r=20, t=50, b=20)
                )
                
                fig.add_hline(y=0, line_dash="dash", line_color="green", annotation_text="Safe Zone")

                st.plotly_chart(fig, use_container_width=True)
                
                with st.expander("View Comprehensive Kinetic Matrix"):
                    st.dataframe(df_performance, use_container_width=True, hide_index=True)

if __name__ == "__main__":
    app = UtilityProjectionEngine()
    app.render_engine()
