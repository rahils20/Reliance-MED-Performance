import math
import numpy as np
import pandas as pd

class UtilityProjectionEngine:
    def __init__(self):
        # ---------------------------------------------------------
        # THERMODYNAMIC PRODUCT LIMIT MATRIX
        # ---------------------------------------------------------
        self.ro_matrix = {
            "Kem Watreat R 426": {"LSI": 2.6, "SDSI": 2.5, "CaSO4": 4.0, "BaSO4": 160.0, "SrSO4": 12.0, "CaF2": 120.0, "SiO2": 1.0, "Iron": 0.5, "Aluminium": 0.5},
            "Kem Watreat R 346": {"LSI": 2.6, "SDSI": 2.5, "CaSO4": 2.5, "BaSO4": 1.0, "SrSO4": 1.0, "CaF2": 1.0, "SiO2": 1.0, "Iron": 0.5, "Aluminium": 0.5},
            "Kem Watreat R 428 I": {"LSI": 2.6, "SDSI": 2.5, "CaSO4": 4.0, "BaSO4": 160.0, "SrSO4": 12.0, "CaF2": 120.0, "SiO2": 1.0, "Iron": 0.5, "Aluminium": 0.5},
            "Kem Watreat R 4001": {"LSI": 2.6, "SDSI": 2.6, "CaSO4": 4.0, "BaSO4": 120.0, "SrSO4": 12.0, "CaF2": 120.0, "SiO2": 2.0, "Iron": 4.0, "Aluminium": 4.0}
        }

    def run_ro_projection(self, feed_data, sys_rec, mem_rej):
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

        p_stream, c_stream = {}, {}
        for ion, charge in ions.items():
            f_val = feed_data.get(ion, 0.0)
            p_val = sim_prod(f_val, charge)
            c_val = sim_conc(f_val, p_val)
            p_stream[ion] = p_val
            c_stream[ion] = c_val

        p_tds = sum(p_stream.values())
        c_tds = sum(c_stream.values())

        feed_ph = feed_data.get("pH", 7.0)
        feed_tds = feed_data.get("TDS", 1000.0)
        
        si_lsi_raw = feed_ph - (11.5 - np.log10(max(feed_data.get("Ca", 1.0), 1.0)) - np.log10(max(feed_data.get("HCO3", 1.0), 1.0)) + (np.sqrt(feed_tds) / 4000))
        si_lsi_conc = si_lsi_raw + np.log10(cf_flow) + 1.1
        si_sdsi_raw = si_lsi_raw - 0.05
        si_sdsi_conc = si_lsi_conc - 0.4

        actual_saturation = {
            "LSI": si_lsi_conc,
            "SDSI": si_sdsi_conc,
            "CaSO4": (c_stream["Ca"] * c_stream["SO4"]) / 2436000.0, 
            "BaSO4": (c_stream["Ba"] * c_stream["SO4"]) / 10000.0,
            "SrSO4": (c_stream["Sr"] * c_stream["SO4"]) / 100000.0,
            "CaF2": (c_stream["Ca"] * c_stream["F"] * c_stream["F"]) / 2000.0,
            "SiO2": c_stream["SiO2"] / 135.0,
            "Iron": c_stream["Fe"],
            "Aluminium": c_stream["Al"]
        }

        # Dynamic Recommendation Logic (Fixing the R 428 I mismatch)
        if actual_saturation["SiO2"] > 1.0 or actual_saturation["Iron"] > 0.5:
            best_product = "Kem Watreat R 4001"
            best_dose = 3.6
            active_limits = list(self.ro_matrix["Kem Watreat R 4001"].values())
        elif actual_saturation["LSI"] > 1.8 or actual_saturation["BaSO4"] > 1.0 or actual_saturation["CaSO4"] > 2.5:
            best_product = "Kem Watreat R 428 I"
            best_dose = 2.6
            active_limits = list(self.ro_matrix["Kem Watreat R 428 I"].values())
        else:
            best_product = "Kem Watreat R 346"
            best_dose = 2.8
            active_limits = list(self.ro_matrix["Kem Watreat R 346"].values())

        si_data = {
            "Index": ["LSI", "SDSI", "CaSO4", "BaSO4", "SrSO4", "CaF2", "SiO2", "Iron", "Aluminium"],
            "Raw Feed": [si_lsi_raw, si_sdsi_raw, (feed_data.get("Ca", 0) * feed_data.get("SO4", 0)) / 2436000.0, 0.0, 0.0, 0.0, feed_data.get("SiO2", 0) / 135.0, feed_data.get("Fe", 0), feed_data.get("Al", 0)],
            "Treated": [si_lsi_raw, si_sdsi_raw, (feed_data.get("Ca", 0) * feed_data.get("SO4", 0)) / 2436000.0, 0.0, 0.0, 0.0, feed_data.get("SiO2", 0) / 135.0, feed_data.get("Fe", 0), feed_data.get("Al", 0)],
            "Before Treatment": [actual_saturation["LSI"], actual_saturation["SDSI"], actual_saturation["CaSO4"], actual_saturation["BaSO4"], actual_saturation["SrSO4"], actual_saturation["CaF2"], actual_saturation["SiO2"], actual_saturation["Iron"], actual_saturation["Aluminium"]],
        }
        
        df_si = pd.DataFrame(si_data)
        df_si['Max Saturation'] = df_si['Before Treatment'] / np.array(active_limits)
        df_si['Max Saturation'] = df_si['Max Saturation'].apply(lambda x: max(0, x))

        # Hard rounding inside the engine completely prevents Streamlit rendering bugs
        df_si = df_si.round(3)

        return {
            "Product_Stream": {k: round(v, 2) for k, v in p_stream.items()},
            "Concentrate_Stream": {k: round(v, 2) for k, v in c_stream.items()},
            "Product_TDS": round(p_tds, 2),
            "Concentrate_TDS": round(c_tds, 2),
            "SI_DataFrame": df_si,
            "Recommendation": {"Product": best_product, "Target_Dose": round(best_dose, 2)}
        }
