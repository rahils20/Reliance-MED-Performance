# projection_engine.py
import math
import numpy as np
import pandas as pd

class UtilityProjectionEngine:
    def __init__(self):
        # ---------------------------------------------------------
        # MASTER PRODUCT MATRIX (From Corporate Word Document)
        # ---------------------------------------------------------
        self.matrix = {
            "RO": {
                # Mapped from AmeROyal 468 (Basic - High LSI tolerance)
                "Chembond Watreat 468": {
                    "LSI_Max": 2.6, "CaSO4_Max_SI": 2.5, "BaSO4_Max_SI": 1.0, 
                    "SrSO4_Max_SI": 1.0, "Silica_Max_SI": 1.0, "Iron_Max": 0.5, "Al_Max": 0.5,
                    "Target_Dose_PPM": 2.5
                },
                # Mapped from AmeROyal 428 (Broad Spectrum - High Silica/Sulfate)
                "Chembond Watreat 428": {
                    "LSI_Max": 2.6, "CaSO4_Max_SI": 4.0, "BaSO4_Max_SI": 120.0, 
                    "SrSO4_Max_SI": 12.0, "Silica_Max_SI": 2.0, "Iron_Max": 0.5, "Al_Max": 0.5,
                    "Target_Dose_PPM": 3.5
                },
                # Mapped from AmeROyal 363 (Extreme Barium Sulfate Duty)
                "Chembond Watreat 363": {
                    "LSI_Max": 2.5, "CaSO4_Max_SI": 4.0, "BaSO4_Max_SI": 160.0, 
                    "SrSO4_Max_SI": 12.0, "Silica_Max_SI": 1.0, "Iron_Max": 0.5, "Al_Max": 0.5,
                    "Target_Dose_PPM": 4.0
                }
            },
            "MED": {}, # Architecture reserved for thermal scaling formulas
            "CWT": {}, # Architecture reserved for Ryznar/Puckorius indices
            "BWT": {}  # Architecture reserved for ASME Boiler guidelines
        }

        # Thermodynamic Solubility Constants (Ksp) at 25C & Molar Masses (g/mol)
        self.ksp = {"CaSO4": 2.4e-5, "BaSO4": 1.1e-10, "SrSO4": 3.2e-7}
        self.mw = {"Ca": 40.078, "Ba": 137.327, "Sr": 87.62, "SO4": 96.06, "SiO2": 60.08}
        self.silica_solubility_ppm = 120.0 # Standard at 25C, pH 7.0

    def calculate_ro_saturation(self, feed_ions, recovery_pct, temp_c=25.0):
        """
        Calculates Saturation Indices (SI) in the RO Concentrate Stream.
        feed_ions: dict of ion concentrations in ppm (mg/L)
        recovery_pct: Float (e.g., 85.0 for 85%)
        """
        # 1. Calculate Concentration Factor (CF)
        recovery_frac = recovery_pct / 100.0
        # CF at the trailing edge of the membrane (most concentrated)
        cf = 1 / (1 - recovery_frac)

        # 2. Project Concentrate Ion Concentrations (ppm)
        # Assuming 100% rejection for multivalent scaling ions
        conc_ions = {ion: (ppm * cf) for ion, ppm in feed_ions.items()}

        # 3. Convert to Molarity (mol/L) for Thermodynamic Equations
        molarity = {
            "Ca": conc_ions.get("Ca", 0) / (self.mw["Ca"] * 1000),
            "Ba": conc_ions.get("Ba", 0) / (self.mw["Ba"] * 1000),
            "Sr": conc_ions.get("Sr", 0) / (self.mw["Sr"] * 1000),
            "SO4": conc_ions.get("SO4", 0) / (self.mw["SO4"] * 1000)
        }

        # 4. Calculate Ion Activity Products (IAP) & Saturation Indices (SI)
        # Note: Professional engines use Davies/Pitzer equations for activity coefficients.
        # This is a robust standard approximation scaled for speed.
        results = {}
        
        # CaSO4 (Gypsum)
        iap_caso4 = molarity["Ca"] * molarity["SO4"]
        results["CaSO4_SI"] = iap_caso4 / self.ksp["CaSO4"]

        # BaSO4 (Barite)
        iap_baso4 = molarity["Ba"] * molarity["SO4"]
        results["BaSO4_SI"] = iap_baso4 / self.ksp["BaSO4"]

        # SrSO4 (Celestite)
        iap_srso4 = molarity["Sr"] * molarity["SO4"]
        results["SrSO4_SI"] = iap_srso4 / self.ksp["SrSO4"]

        # Silica (SiO2) - Measured as a ratio to its saturation limit
        conc_silica = conc_ions.get("SiO2", 0)
        results["Silica_SI"] = conc_silica / self.silica_solubility_ppm

        # Simplistic LSI projection (Assuming pH rises ~0.5 units in concentrate)
        feed_lsi = feed_ions.get("LSI", 0)
        results["Concentrate_LSI"] = feed_lsi + 0.5 + math.log10(cf)

        return results

    def get_recommendation(self, utility, si_results):
        """
        Cross-references thermodynamic results against the Chembond matrix 
        to find the optimal, safest product.
        """
        if utility not in self.matrix or not self.matrix[utility]:
            return {"Status": "Error", "Message": f"Matrix for {utility} not loaded."}

        available_products = self.matrix[utility]
        eligible_products = []

        lsi = si_results.get("Concentrate_LSI", 0)
        caso4 = si_results.get("CaSO4_SI", 0)
        baso4 = si_results.get("BaSO4_SI", 0)
        srso4 = si_results.get("SrSO4_SI", 0)
        silica = si_results.get("Silica_SI", 0)

        # Evaluate every product in the matrix
        for product_name, limits in available_products.items():
            if (lsi <= limits["LSI_Max"] and 
                caso4 <= limits["CaSO4_Max_SI"] and 
                baso4 <= limits["BaSO4_Max_SI"] and 
                srso4 <= limits["SrSO4_Max_SI"] and 
                silica <= limits["Silica_Max_SI"]):
                
                eligible_products.append({
                    "Product": product_name,
                    "Target_Dose": limits["Target_Dose_PPM"]
                })

        if not eligible_products:
            return {
                "Status": "Critical Warning", 
                "Product": "None", 
                "Message": "Scaling potential exceeds ALL standard product limits. Plant recovery must be reduced or specialized chemistry required."
            }

        # Recommend the most cost-effective product that covers the limits
        # (Assuming the list is ordered from basic to high-duty)
        best_product = eligible_products[0]
        
        return {
            "Status": "Safe",
            "Product": best_product["Product"],
            "Target_Dose": best_product["Target_Dose"],
            "Message": f"Calculated scaling indices are within the safe operating envelope for {best_product['Product']}."
        }

    def generate_projection_curve(self, feed_ions, min_rec=50, max_rec=90, steps=20):
        """
        Generates a DataFrame mapping scaling curves across different recovery rates.
        This is what Streamlit will use to draw the beautiful graphs.
        """
        recoveries = np.linspace(min_rec, max_rec, steps)
        data = []

        for rec in recoveries:
            si = self.calculate_ro_saturation(feed_ions, rec)
            data.append({
                "Recovery (%)": round(rec, 1),
                "LSI": round(si["Concentrate_LSI"], 2),
                "CaSO4 SI": round(si["CaSO4_SI"], 2),
                "BaSO4 SI": round(si["BaSO4_SI"], 2),
                "Silica SI": round(si["Silica_SI"], 2)
            })

        return pd.DataFrame(data)

# Test block - This will not run when imported by Streamlit, only if you run this file directly
if __name__ == "__main__":
    engine = UtilityProjectionEngine()
    test_ions = {"Ca": 150, "Ba": 0.05, "Sr": 1.2, "SO4": 250, "SiO2": 15, "LSI": 0.2}
    
    print("Testing 85% Recovery Saturation...")
    si = engine.calculate_ro_saturation(test_ions, 85.0)
    print(si)
    
    print("\nTesting Matrix Recommendation...")
    rec = engine.get_recommendation("RO", si)
    print(rec)
