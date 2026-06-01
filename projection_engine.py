import math
import numpy as np
import pandas as pd

class UtilityProjectionEngine:
    def __init__(self):
        # ---------------------------------------------------------
        # THERMODYNAMIC PRODUCT LIMIT MATRIX
        # ---------------------------------------------------------
        self.ro_matrix = {
            "Kem Watreat R 426": {"Limits": {"LSI": 2.6, "SDSI": 2.5, "CaSO4": 4.0, "BaSO4": 160.0, "SrSO4": 12.0, "CaF2": 120.0, "SiO2": 1.0, "Iron": 0.5, "Aluminium": 0.5}, "Base_Dose": 2.4},
            "Kem Watreat R 246": {"Limits": {"LSI": 2.6, "SDSI": 2.5, "CaSO4": 4.0, "BaSO4": 160.0, "SrSO4": 12.0, "CaF2": 120.0, "SiO2": 1.0, "Iron": 0.5, "Aluminium": 0.5}, "Base_Dose": 2.4},
            "Kem Watreat R 346": {"Limits": {"LSI": 2.6, "SDSI": 2.5, "CaSO4": 2.5, "BaSO4": 1.0, "SrSO4": 1.0, "CaF2": 1.0, "SiO2": 1.0, "Iron": 0.5, "Aluminium": 0.5}, "Base_Dose": 2.8},
            "Kem Watreat R 428 I": {"Limits": {"LSI": 2.5, "SDSI": 2.5, "CaSO4": 4.0, "BaSO4": 160.0, "SrSO4": 12.0, "CaF2": 120.0, "SiO2": 1.0, "Iron": 0.5, "Aluminium": 0.5}, "Base_Dose": 2.6},
            "Kem Watreat R 4001": {"Limits": {"LSI": 2.6, "SDSI": 2.6, "CaSO4": 4.0, "BaSO4": 120.0, "SrSO4": 12.0, "CaF2": 120.0, "SiO2": 2.0, "Iron": 4.0, "Aluminium": 4.0}, "Base_Dose": 3.6}
        }

    def run_ro_projection(self, feed_data, sys_rec, mem_rej, manual_prod=None, manual_dose=None):
        rec_frac = sys_rec / 100.0
        rej_frac = mem_rej / 100.0
        cf_flow = 1 / (1 - rec_frac) if rec_frac < 1.0 else 10.0

        def sim_prod(val, charge):
            actual_rej = rej_frac if charge == 1 else 0.998 
            return val * (1.0 - actual_rej) if val > 0 else 0.0

        def sim_conc(feed_val, prod_val):
            if rec_frac >= 1.0: return 0.0
            return (feed_val - (rec_frac * prod_val)) / (1.0 - rec_frac)

        ions = {
            "Ca": 2, "Mg": 2, "Ba": 2, "Sr": 2, "Fe": 2, "Al": 2, "SO4": 2, "CO3": 2,
            "Na": 1, "K": 1, "NH4": 1, "HCO3": 1, "Cl": 1, "F": 1, "NO3": 1, "PO4": 1, "SiO2": 1
        }

        # 1. Initial Mass Balance
        p_stream, c_stream = {}, {}
        for ion, charge in ions.items():
            f_val = feed_data.get(ion, 0.0)
            p_val = sim_prod(f_val, charge)
            c_val = sim_conc(f_val, p_val)
            p_stream[ion] = p_val
            c_stream[ion] = c_val

        feed_ph = feed_data.get("pH", 7.0)
        feed_tds = feed_data.get("TDS", 1000.0)
        
        si_lsi_raw = feed_ph - (11.5 - np.log10(max(feed_data.get("Ca", 1.0), 1.0)) - np.log10(max(feed_data.get("HCO3", 1.0), 1.0)) + (np.sqrt(feed_tds) / 4000))
        si_lsi_conc = si_lsi_raw + np.log10(cf_flow) + 1.2
        si_sdsi_raw = si_lsi_raw - 0.05
        si_sdsi_conc = si_lsi_conc - 0.4

        # 2. ACID DOSING STOICHIOMETRY LOOP
        acid_dose = 0.0
        if si_lsi_conc > 2.6:
            delta_lsi = si_lsi_conc - 2.5
            target_log_hco3 = np.log10(max(feed_data.get("HCO3", 1.0), 1.0)) - delta_lsi
            target_hco3 = 10 ** target_log_hco3
            alk_destroyed = feed_data.get("HCO3", 0.0) - target_hco3
            
            if alk_destroyed > 0:
                acid_dose = alk_destroyed * 0.98
                
                # Dynamically Adjust Feed based on Acid Injection
                feed_data["HCO3"] = target_hco3
                feed_data["SO4"] += acid_dose
                
                # Re-run Mass Balance for adjusted ions
                p_stream["HCO3"] = sim_prod(feed_data["HCO3"], 1)
                c_stream["HCO3"] = sim_conc(feed_data["HCO3"], p_stream["HCO3"])
                p_stream["SO4"] = sim_prod(feed_data["SO4"], 2)
                c_stream["SO4"] = sim_conc(feed_data["SO4"], p_stream["SO4"])
                
                # Recalculate Indices safely below 2.6
                si_lsi_raw = feed_ph - (11.5 - np.log10(max(feed_data.get("Ca", 1.0), 1.0)) - np.log10(max(target_hco3, 1.0)) + (np.sqrt(feed_tds) / 4000))
                si_lsi_conc = si_lsi_raw + np.log10(cf_flow) + 1.2
                si_sdsi_raw = si_lsi_raw - 0.05
                si_sdsi_conc = si_lsi_conc - 0.4

        p_tds = sum(p_stream.values())
        c_tds = sum(c_stream.values())

        # 3. Calculate Final Saturations
        actual_saturation = {
            "LSI": si_lsi_conc,
            "SDSI": si_sdsi_conc,
            "CaSO4": (c_stream["Ca"] * c_stream["SO4"]) / 1800000.0, 
            "BaSO4": (c_stream["Ba"] * c_stream["SO4"]) / 10000.0,
            "SrSO4": (c_stream["Sr"] * c_stream["SO4"]) / 100000.0,
            "CaF2": (c_stream["Ca"] * (c_stream["F"] ** 2)) / 800.0,
            "SiO2": c_stream["SiO2"] / 140.0,
            "Iron": c_stream["Fe"],
            "Aluminium": c_stream["Al"]
        }

        # 4. Product Selection (Auto vs Manual)
        if manual_prod and manual_prod in self.ro_matrix:
            # Engineer Overrode the Software
            best_product = manual_prod
            best_dose = manual_dose if manual_dose else self.ro_matrix[manual_prod]["Base_Dose"]
        else:
            # Software Auto-Optimization
            best_product = "Kem Watreat R 346" 
            best_dose = 2.8
            survivors = []
            for prod_name, specs in self.ro_matrix.items():
                limits = specs["Limits"]
                disqualified = False
                for param in ["CaSO4", "BaSO4", "SrSO4", "CaF2", "SiO2", "Iron", "Aluminium"]:
                    if actual_saturation[param] > limits[param]:
                        disqualified = True
                        break
                if not disqualified:
                    survivors.append(prod_name)

            if "Kem Watreat R 4001" in survivors and (actual_saturation["Iron"] > 0.5 or actual_saturation["SiO2"] > 0.9):
                best_product = "Kem Watreat R 4001"
                best_dose = 3.6
            elif "Kem Watreat R 428 I" in survivors and (actual_saturation["CaF2"] > 1.0 or actual_saturation["BaSO4"] > 1.0):
                best_product = "Kem Watreat R 428 I"
                best_dose = 2.6
            elif "Kem Watreat R 246" in survivors and (si_lsi_conc > 1.5 or feed_data.get("HCO3", 0) > 100):
                best_product = "Kem Watreat R 246"
                best_dose = 2.4
            elif len(survivors) > 0:
                best_product = survivors[0]
                best_dose = self.ro_matrix[best_product]["Base_Dose"]

        active_limits = list(self.ro_matrix[best_product]["Limits"].values())

        si_data = {
            "Index": ["LSI", "SDSI", "CaSO4", "BaSO4", "SrSO4", "CaF2", "SiO2", "Iron", "Aluminium"],
            "Raw Feed": [si_lsi_raw, si_sdsi_raw, (feed_data.get("Ca", 0) * feed_data.get("SO4", 0)) / 1800000.0, 0.0, 0.0, 0.0, feed_data.get("SiO2", 0) / 140.0, feed_data.get("Fe", 0), feed_data.get("Al", 0)],
            "Treated": [si_lsi_raw, si_sdsi_raw, (feed_data.get("Ca", 0) * feed_data.get("SO4", 0)) / 1800000.0, 0.0, 0.0, 0.0, feed_data.get("SiO2", 0) / 140.0, feed_data.get("Fe", 0), feed_data.get("Al", 0)],
            "Before Treatment": [actual_saturation["LSI"], actual_saturation["SDSI"], actual_saturation["CaSO4"], actual_saturation["BaSO4"], actual_saturation["SrSO4"], actual_saturation["CaF2"], actual_saturation["SiO2"], actual_saturation["Iron"], actual_saturation["Aluminium"]],
        }
        
        df_si = pd.DataFrame(si_data)
        df_si['Max Saturation'] = df_si['Before Treatment'] / np.array(active_limits)
        df_si['Max Saturation'] = df_si['Max Saturation'].apply(lambda x: max(0, x))

        return {
            "Product_Stream": {k: round(v, 2) for k, v in p_stream.items()},
            "Concentrate_Stream": {k: round(v, 2) for k, v in c_stream.items()},
            "Product_TDS": round(p_tds, 2),
            "Concentrate_TDS": round(c_tds, 2),
            "SI_DataFrame": df_si.round(3),
            "Recommendation": {"Product": best_product, "Target_Dose": round(best_dose, 2), "Acid_Dose": round(acid_dose, 2)}
        }
