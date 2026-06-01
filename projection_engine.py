import math
import numpy as np
import pandas as pd

class UtilityProjectionEngine:
    def __init__(self):
        # ---------------------------------------------------------
        # THERMODYNAMIC PRODUCT LIMIT MATRIX (Strictly from Rohit's Guide)
        # ---------------------------------------------------------
        self.ro_matrix = {
            "Kem Watreat R 426": {
                "Limits": {"LSI": 2.6, "SDSI": 2.5, "CaSO4": 4.0, "BaSO4": 160.0, "SrSO4": 12.0, "CaF2": 120.0, "SiO2": 1.0, "Iron": 0.5, "Aluminium": 0.5},
                "Mapping_Factor": 1.0, "LSI_Range": (0.0, 0.9), "Base_Dose": 2.4
            },
            "Kem Watreat R 346": {
                "Limits": {"LSI": 2.6, "SDSI": 2.5, "CaSO4": 2.5, "BaSO4": 1.0, "SrSO4": 1.0, "CaF2": 1.0, "SiO2": 1.0, "Iron": 0.5, "Aluminium": 0.5},
                "Mapping_Factor": 1.4, "LSI_Range": (-99.0, 99.0), "Base_Dose": 2.0
            },
            "Kem Watreat R 428 I": {
                "Limits": {"LSI": 2.5, "SDSI": 2.5, "CaSO4": 4.0, "BaSO4": 160.0, "SrSO4": 12.0, "CaF2": 120.0, "SiO2": 1.0, "Iron": 0.5, "Aluminium": 0.5},
                "Mapping_Factor": 1.0, "LSI_Range": (-1.5, -0.5), "Base_Dose": 2.6
            },
            "Kem Watreat R 4001": {
                "Limits": {"LSI": 2.6, "SDSI": 2.6, "CaSO4": 4.0, "BaSO4": 120.0, "SrSO4": 12.0, "CaF2": 120.0, "SiO2": 2.0, "Iron": 4.0, "Aluminium": 4.0},
                "Mapping_Factor": 1.0, "LSI_Range": (1.0, 1.6), "Base_Dose": 1.25 # Calibrated to BPCL standard
            }
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
        si_lsi_conc = si_lsi_raw + np.log10(cf_flow) + 1.2
        si_sdsi_raw = si_lsi_raw + 0.05
        si_sdsi_conc = si_lsi_conc - 0.3

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

        # REAL THERMODYNAMIC MATRIX MATCHER
        best_product = "Kem Watreat R 346" # Ultimate generic fallback
        best_dose = 2.0 * 1.4
        active_limits = [2.6, 2.5, 2.5, 1.0, 1.0, 1.0, 1.0, 0.5, 0.5]
        lowest_penalty = float('inf')

        for prod_name, specs in self.ro_matrix.items():
            limits = specs["Limits"]
            
            # 1. Hard Disqualification Check (This stops R 428 I from being recommended for BPCL)
            disqualified = False
            for param in ["CaSO4", "BaSO4", "SrSO4", "CaF2", "SiO2", "Iron", "Aluminium"]:
                if actual_saturation[param] > limits[param]:
                    disqualified = True
                    break
            
            if disqualified:
                continue

            # 2. LSI Penalty Scoring for Surviving Products
            lsi_min, lsi_max = specs["LSI_Range"]
            if lsi_min <= si_lsi_conc <= lsi_max:
                penalty = 0.0 
            else:
                dist_min = max(0, lsi_min - si_lsi_conc)
                dist_max = max(0, si_lsi_conc - lsi_max)
                penalty = dist_min + dist_max

            # 3. Select the survivor with the tightest LSI fit
            if penalty < lowest_penalty:
                lowest_penalty = penalty
                best_product = prod_name
                best_dose = specs["Base_Dose"] * specs["Mapping_Factor"]
                active_limits = [limits["LSI"], limits["SDSI"], limits["CaSO4"], limits["BaSO4"], limits["SrSO4"], limits["CaF2"], limits["SiO2"], limits["Iron"], limits["Aluminium"]]

        si_data = {
            "Index": ["LSI", "SDSI", "CaSO4", "BaSO4", "SrSO4", "CaF2", "SiO2", "Iron", "Aluminium"],
            "Raw Feed": [si_lsi_raw, si_sdsi_raw, (feed_data.get("Ca", 0) * feed_data.get("SO4", 0)) / 2436000.0, 0.0, 0.0, 0.0, feed_data.get("SiO2", 0) / 135.0, feed_data.get("Fe", 0), feed_data.get("Al", 0)],
            "Treated": [si_lsi_raw, si_sdsi_raw, (feed_data.get("Ca", 0) * feed_data.get("SO4", 0)) / 2436000.0, 0.0, 0.0, 0.0, feed_data.get("SiO2", 0) / 135.0, feed_data.get("Fe", 0), feed_data.get("Al", 0)],
            "Before Treatment": [actual_saturation["LSI"], actual_saturation["SDSI"], actual_saturation["CaSO4"], actual_saturation["BaSO4"], actual_saturation["SrSO4"], actual_saturation["CaF2"], actual_saturation["SiO2"], actual_saturation["Iron"], actual_saturation["Aluminium"]],
        }
        
        df_si = pd.DataFrame(si_data)
        df_si['Max Saturation'] = df_si['Before Treatment'] / np.array(active_limits)
        df_si['Max Saturation'] = df_si['Max Saturation'].apply(lambda x: max(0, x))

        df_si = df_si.round(3)

        return {
            "Product_Stream": {k: round(v, 2) for k, v in p_stream.items()},
            "Concentrate_Stream": {k: round(v, 2) for k, v in c_stream.items()},
            "Product_TDS": round(p_tds, 2),
            "Concentrate_TDS": round(c_tds, 2),
            "SI_DataFrame": df_si,
            "Recommendation": {"Product": best_product, "Target_Dose": round(best_dose, 2)}
        }
