import math
import numpy as np
import pandas as pd

class UtilityProjectionEngine:
    def __init__(self):
        pass

    def run_ro_projection(self, feed_data, sys_rec, mem_rej):
        """
        Takes raw feed water dictionary, recovery, and rejection.
        Returns calculated Product stream, Concentrate stream, SI Matrix, and Product Recommendation.
        """
        rec_frac = sys_rec / 100.0
        rej_frac = mem_rej / 100.0
        cf_flow = 1 / (1 - rec_frac) if rec_frac < 1.0 else 10.0

        # Split rejection: Divalent ions (Charge 2) are rejected at a much higher rate than Monovalent (Charge 1)
        def sim_prod(val, charge):
            actual_rej = rej_frac if charge == 1 else 1 - ((1 - rej_frac) * 0.15)
            return val * (1.0 - actual_rej) if val > 0 else 0.0

        def sim_conc(feed_val, prod_val):
            if rec_frac >= 1.0: return 0.0
            return (feed_val - (rec_frac * prod_val)) / (1.0 - rec_frac)

        # 1. Calculate Mass Balance for all Ions
        ions = {
            # Divalent (Charge 2)
            "Ca": 2, "Mg": 2, "Ba": 2, "Sr": 2, "Fe": 2, "Al": 2, "SO4": 2, "CO3": 2,
            # Monovalent / Other (Charge 1)
            "Na": 1, "K": 1, "NH4": 1, "HCO3": 1, "Cl": 1, "F": 1, "NO3": 1, "PO4": 1, "SiO2": 1
        }

        prod_stream = {}
        conc_stream = {}

        for ion, charge in ions.items():
            feed_val = feed_data.get(ion, 0.0)
            p_val = sim_prod(feed_val, charge)
            c_val = sim_conc(feed_val, p_val)
            
            prod_stream[ion] = p_val
            conc_stream[ion] = c_val

        # Aggregate TDS
        p_tds = sum(prod_stream.values())
        c_tds = sum(conc_stream.values())

        # pH and LSI Math
        feed_ph = feed_data.get("pH", 7.0)
        feed_tds = feed_data.get("TDS", 1000.0)
        
        si_lsi_raw = feed_ph - (11.5 - np.log10(max(feed_data.get("Ca", 1.0), 1.0)) - np.log10(max(feed_data.get("HCO3", 1.0), 1.0)) + (np.sqrt(feed_tds) / 4000))
        si_lsi_conc = si_lsi_raw + np.log10(cf_flow) + 1.2
        si_sdsi_raw = si_lsi_raw + 0.05
        si_sdsi_conc = si_lsi_conc - 0.3

        # 2. Build Saturation Index Matrix
        si_data = {
            "Index": ["LSI", "SDSI", "CaSO4", "BaSO4", "SrSO4", "CaF2", "SiO2", "Iron", "Aluminium"],
            "Raw Feed": [si_lsi_raw, si_sdsi_raw, (feed_data.get("Ca", 0) * feed_data.get("SO4", 0)) / 750000, 0.0, 0.0, 0.0, feed_data.get("SiO2", 0) / 140, feed_data.get("Fe", 0) * 20, feed_data.get("Al", 0)],
            "Treated": [si_lsi_raw, si_sdsi_raw, (feed_data.get("Ca", 0) * feed_data.get("SO4", 0)) / 750000, 0.0, 0.0, 0.0, feed_data.get("SiO2", 0) / 140, feed_data.get("Fe", 0) * 20, feed_data.get("Al", 0)],
            "Concentrate": [si_lsi_conc, si_sdsi_conc, (conc_stream["Ca"] * conc_stream["SO4"]) / 750000, 0.0, 0.0, 0.0, conc_stream["SiO2"] / 140, conc_stream["Fe"] * 20, conc_stream["Al"]],
        }
        
        df_si = pd.DataFrame(si_data)
        max_limits = np.array([2.6, 2.6, 4.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0])
        df_si['Max Saturation'] = df_si['Concentrate'] / max_limits

        # 3. Dynamic Product Recommendation Logic (Based on your PDFs)
        if feed_data.get("SO4", 0) > 150 or feed_data.get("Ca", 0) > 200:
            product = "Kem Watreat R 4001"
            dose = 3.6
        elif feed_data.get("HCO3", 0) > 300 or feed_tds > 1000:
            product = "Kem Watreat R 246"
            dose = 2.4
        else:
            product = "Kem Watreat R 428"
            dose = 2.6

        return {
            "Product_Stream": prod_stream,
            "Concentrate_Stream": conc_stream,
            "Product_TDS": p_tds,
            "Concentrate_TDS": c_tds,
            "SI_DataFrame": df_si,
            "Recommendation": {"Product": product, "Target_Dose": dose}
        }
