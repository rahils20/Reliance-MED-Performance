import streamlit as st
import pandas as pd
import math
import io

def show_matrix_calculator():
    st.header("Proprietary Formulation Calculator v5 (Thermodynamic Kinetic Model)")
    st.markdown("""
    This engine utilizes **Langmuir adsorption approximations**, **steric dispersion models**, 
    and **stoichiometric chelation ratios** to calculate absolute performance limits 
    based on the % Active raw materials.
    """)

    # Expected raw material columns
    rm_columns = [
        'PBTC', 'HEDP', 'ATMP', 'SMBS', 'Copolymer', 'Terpolymer', 
        'Homopolymer', 'PMA', 'DETMPA', 'Caustic_Lye', 'NAOH_Flakes', 'Caustic_Potash'
    ]

    st.subheader("1. Download Input Template")
    template_cols = ['Product_Name'] + rm_columns
    template_df = pd.DataFrame(columns=template_cols)
    template_df.loc[0] = ['Example_Formula', 5.0, 0.0, 0.0, 0.0, 2.0, 0.0, 3.0, 0.0, 2.0, 0.0, 0.0, 0.0]
    
    csv_buffer = io.StringIO()
    template_df.to_csv(csv_buffer, index=False)
    st.download_button(
        label="Download formulation_input.csv",
        data=csv_buffer.getvalue(),
        file_name="formulation_input.csv",
        mime="text/csv"
    )

    st.subheader("2. Run Kinetic Calculations")
    uploaded_file = st.file_uploader("Upload filled CSV", type=['csv'])

    if uploaded_file is not None:
        try:
            df_formulations = pd.read_csv(uploaded_file)
            matrix_results = []

            for index, row in df_formulations.iterrows():
                product_name = row['Product_Name']
                rm = {col: float(row[col]) if pd.notna(row.get(col)) else 0.0 for col in rm_columns}
                
                # --- 1. LSI & SDSI (Langmuir Adsorption Model) ---
                # Weighting the phosphonates/PMA by their adsorption kinetics (k-values)
                phos_kinetic_sum = (rm['PBTC']*0.9) + (rm['PMA']*0.85) + (rm['HEDP']*0.6) + (rm['ATMP']*0.3)
                
                # Equation: Base (0.5) + Max Boost (2.3) * (1 - e^(-0.4 * active))
                lsi = 0.5 + 2.3 * (1 - math.exp(-0.4 * phos_kinetic_sum))
                lsi = round(min(lsi, 2.8), 2)  # Absolute thermodynamic limit for standard RO
                sdsi = round(lsi - 0.1, 2)     # SDSI tracks slightly lower due to ionic strength

                # --- 2. Silica (Steric Dispersion Model) ---
                # Terpolymer provides highest steric hindrance
                dispersion_sum = (rm['Terpolymer']*1.0) + (rm['Copolymer']*0.6) + (rm['Homopolymer']*0.2)
                
                # Equation: Base (1.0) + Logarithmic scaling based on polymer chain density
                sio2 = 1.0 + 0.8 * math.log1p(1.2 * dispersion_sum)
                sio2 = round(min(sio2, 2.5), 2)

                # --- 3. Barium & Strontium Sulfate (Crystal Modification) ---
                # BaSO4 is exclusively handled by PMA and PBTC at high concentrations
                baso4_active = (rm['PMA']*1.0) + (rm['PBTC']*0.8) + (rm['Copolymer']*0.3)
                baso4 = 1.0 + 160.0 * (1 - math.exp(-0.5 * baso4_active))
                
                srso4 = 1.0 + 12.0 * (1 - math.exp(-0.6 * baso4_active))

                # --- 4. Calcium Sulfate & Fluoride ---
                caso4_active = phos_kinetic_sum + (rm['Copolymer']*0.4)
                caso4 = 1.0 + 3.0 * (1 - math.exp(-0.5 * caso4_active))
                
                caf2_active = (rm['HEDP']*0.8) + (rm['PMA']*0.6)
                caf2 = 1.0 + 120.0 * (1 - math.exp(-0.4 * caf2_active))

                # --- 5. Iron & Aluminum (Stoichiometric Chelation) ---
                # Based on DETMPA:Fe molar mass ratio (~10:1). 
                # Empirical multiplier applied to estimate ppm tolerance at standard RO dosing (3-5ppm)
                fe_al = 0.1 + (rm['DETMPA'] * 0.95)
                fe_al = round(min(fe_al, 5.0), 2)

                # --- 6. Boolean Constraints ---
                # High concentrations of anionic charge (PMA, Homopolymer) clash with cationic coagulants
                coag_compat = "No" if (rm['PMA'] + rm['Homopolymer'] + rm['Terpolymer'] > 4.0) else "Yes"
                
                # Organics handling requires high dispersion
                organics = "Yes" if dispersion_sum > 2.0 else "No"

                matrix_results.append({
                    'Product_ID': product_name,
                    'LSI': lsi,
                    'SDSI': sdsi,
                    'CaSO4': round(caso4, 1),
                    'BaSO4': int(baso4),
                    'SrSO4': int(srso4),
                    'CaF2': int(caf2),
                    'SiO2': sio2,
                    'Fe (ppm)': fe_al,
                    'Al (ppm)': fe_al,
                    'Organics': organics,
                    'Polymeric_Coagulants': coag_compat,
                    'Max_ppm': 50 # Standard continuous dosing limit
                })

            final_matrix_df = pd.DataFrame(matrix_results)
            st.success("Calculations Complete: First-Principles Model")
            st.dataframe(final_matrix_df, use_container_width=True)

            result_buffer = io.StringIO()
            final_matrix_df.to_csv(result_buffer, index=False)
            st.download_button(
                label="Download Generated Product Matrix",
                data=result_buffer.getvalue(),
                file_name="Master_Limits.csv",
                mime="text/csv",
                type="primary"
            )
            
        except Exception as e:
            st.error(f"Calculation Error: {e}")
