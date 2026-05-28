# projection_engine.py
import math
import numpy as np
import pandas as pd

class UtilityProjectionEngine:
    def __init__(self):
        # ---------------------------------------------------------
        # MASTER PRODUCT MATRIX (Translation Layer)
        # ---------------------------------------------------------
        self.matrix = {
            "RO": {
                "Chembond Watreat 468": {"LSI_Max": 2.6, "CaSO4_Max_SI": 2.5, "BaSO4_Max_SI": 1.0, "Silica_Max_SI": 1.0, "Target_Dose_PPM": 2.5},
                "Chembond Watreat 428": {"LSI_Max": 2.6, "CaSO4_Max_SI": 4.0, "BaSO4_Max_SI": 120.0, "Silica_Max_SI": 2.0, "Target_Dose_PPM": 3.5},
                "Chembond Watreat 363": {"LSI_Max": 2.5, "CaSO4_Max_SI": 4.0, "BaSO4_Max_SI": 160.0, "Silica_Max_SI": 1.0, "Target_Dose_PPM": 4.0}
            },
            "CWT": {
                "Chembond KemKool 2000 (Anti-Scale)": {"RSI_Min": 5.0, "RSI_Max": 6.5, "LSI_Min": 0.5, "LSI_Max": 2.5, "Target_Dose_PPM": 40},
                "Chembond KemKool 4000 (Corrosion Inhibitor)": {"RSI_Min": 6.5, "RSI_Max": 8.0, "LSI_Min": -1.0, "LSI_Max": 0.5, "Target_Dose_PPM": 60}
            },
            "BWT": {
                "Chembond KemBoil 100 (Oxygen Scavenger)": {"Max_Pressure_bar": 40, "Target_Dose_PPM": 15},
                "Chembond KemBoil 300 (Phosphate Treatment)": {"Max_Pressure_bar": 60, "Target_Dose_PPM": 30}
            },
            "MED": {
                "Chembond KemWatreat R3687 (High Temp Scale)": {"Max_Top_Brine_Temp": 75.0, "Max_CF": 2.5, "Target_Dose_PPM": 4.8}
            }
        }

        # Thermodynamic Constants
        self.ksp = {"CaSO4": 2.4e-5, "BaSO4": 1.1e-10, "SrSO4": 3.2e-7}
        self.mw = {"Ca": 40.078, "Ba": 137.327, "Sr": 87.62, "SO4": 96.06, "SiO2": 60.08}
        self.silica_solubility_ppm = 120.0

    # ==========================================
    # 1. RO MEMBRANE ENGINE
    # ==========================================
    def calculate_ro_saturation(self, feed_ions, recovery_pct, temp_c=25.0):
        cf = 1 / (1 - (recovery_pct / 100.0))
        conc_ions = {ion: (ppm * cf) for ion, ppm in feed_ions.items()}

        molarity = {
            "Ca": conc_ions.get("Ca", 0) / (self.mw["Ca"] * 1000),
            "Ba": conc_ions.get("Ba", 0) / (self.mw["Ba"] * 1000),
            "SO4": conc_ions.get("SO4", 0) / (self.mw["SO4"] * 1000)
        }

        results = {
            "CaSO4_SI": (molarity["Ca"] * molarity["SO4"]) / self.ksp["CaSO4"],
            "BaSO4_SI": (molarity["Ba"] * molarity["SO4"]) / self.ksp["BaSO4"],
            "Silica_SI": conc_ions.get("SiO2", 0) / self.silica_solubility_ppm,
            "Concentrate_LSI": feed_ions.get("LSI", 0) + 0.5 + math.log10(cf)
        }
        return results

    # ==========================================
    # 2. COOLING TOWER ENGINE (CWT)
    # ==========================================
    def calculate_cwt_indices(self, makeup_water, target_coc, temp_c):
        """
        Calculates Ryznar and Langelier indices for open evaporative cooling.
        makeup_water: dict containing pH, Calcium (ppm CaCO3), Alkalinity (ppm CaCO3), TDS
        """
        # Calculate basin concentrations
        basin_ca = makeup_water.get("Ca_Hardness", 100) * target_coc
        basin_alk = makeup_water.get("M_Alkalinity", 100) * target_coc
        basin_tds = makeup_water.get("TDS", 500) * target_coc
        basin_ph = makeup_water.get("pH", 7.5) + math.log10(target_coc) # Approximate pH drift

        # Langelier Saturation pH (pHs) approximation
        temp_k = temp_c + 273.15
        a = (math.log10(basin_tds) - 1) / 10
        b = -13.12 * math.log10(temp_k) + 34.55
        c = math.log10(basin_ca) - 0.4
        d = math.log10(basin_alk)
        
        phs = (9.3 + a + b) - (c + d)
        
        lsi = basin_ph - phs
        rsi = 2 * phs - basin_ph

        return {
            "Basin_pH": round(basin_ph, 2),
            "LSI": round(lsi, 2),
            "RSI": round(rsi, 2),
            "Condition": "Scaling" if rsi < 6.0 else ("Stable" if rsi < 7.0 else "Corrosive")
        }

    # ==========================================
    # 3. BOILER WATER ENGINE (BWT)
    # ==========================================
    def calculate_bwt_limits(self, operating_pressure_bar, feed_silica, feed_hardness):
        """
        Applies ASME guidelines to determine maximum boiler cycles to prevent carryover.
        """
        # Simplified ASME Silica limit lookup based on pressure
        if operating_pressure_bar <= 20: max_boiler_silica = 150.0
        elif operating_pressure_bar <= 40: max_boiler_silica = 90.0
        elif operating_pressure_bar <= 60: max_boiler_silica = 40.0
        else: max_boiler_silica = 8.0

        # Maximum Cycles based on Silica
        max_coc_silica = max_boiler_silica / feed_silica if feed_silica > 0 else 50.0
        
        # Hardness penalty (should ideally be 0 from softener, but if leakage occurs)
        max_coc_hardness = 50.0 if feed_hardness < 1.0 else 5.0

        recommended_coc = min(max_coc_silica, max_coc_hardness, 50.0) # Cap at 50

        return {
            "Max_Allowable_Silica_ppm": max_boiler_silica,
            "Recommended_Max_Cycles": round(recommended_coc, 1),
            "Blowdown_Rate_pct": round((1 / recommended_coc) * 100, 1) if recommended_coc > 0 else 100
        }

    # ==========================================
    # 4. MED ENGINE
    # ==========================================
    def calculate_med_scaling(self, top_brine_temp_c, concentration_factor):
        """
        Thermal desalination scaling potential.
        """
        # Calcium Carbonate scale risk highly depends on temp > 65C
        caco3_risk = "High" if (top_brine_temp_c > 65.0 and concentration_factor > 1.5) else "Low"
        
        # Calcium Sulfate scale risk depends on CF > 2.5
        caso4_risk = "High" if concentration_factor >= 2.5 else "Low"

        return {
            "CaCO3_Scale_Risk": caco3_risk,
            "CaSO4_Scale_Risk": caso4_risk,
            "Max_Recommended_CF": 2.2 if top_brine_temp_c > 70 else 2.5
        }

    # ==========================================
    # ROUTER & RECOMMENDATION ENGINE
    # ==========================================
    def get_recommendation(self, utility, results):
        """
        Cross-references results against the Chembond matrix.
        """
        if utility not in self.matrix:
            return {"Status": "Error", "Message": "Utility not mapped."}

        available_products = self.matrix[utility]
        
        if utility == "RO":
            # [Existing RO logic from previous code]
            pass 
            
        elif utility == "CWT":
            rsi = results.get("RSI", 7.0)
            for prod, limits in available_products.items():
                if limits["RSI_Min"] <= rsi <= limits["RSI_Max"]:
                    return {"Status": "Safe", "Product": prod, "Target_Dose": limits["Target_Dose_PPM"]}
            return {"Status": "Warning", "Product": "Custom Blend Required", "Message": f"RSI {rsi} is outside standard bounds."}

        elif utility == "BWT":
            # Simplified pressure-based selection
            for prod, limits in available_products.items():
                if limits["Max_Pressure_bar"] >= 40: # Example logic
                    return {"Status": "Safe", "Product": prod, "Target_Dose": limits["Target_Dose_PPM"]}

        elif utility == "MED":
             for prod, limits in available_products.items():
                  return {"Status": "Safe", "Product": prod, "Target_Dose": limits["Target_Dose_PPM"]}

        return {"Status": "Error", "Message": "No suitable product found."}
