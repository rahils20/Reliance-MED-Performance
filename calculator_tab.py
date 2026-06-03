import streamlit as st
import pandas as pd
import io
import math

def show_matrix_calculator():
    st.header("Proprietary Formulation Calculator v3 (Thermodynamic Model)")
    st.markdown("""
    This engine uses a **Threshold & Logarithmic Saturation Model** to calculate absolute 
    performance limits based on your **% Active** raw material inputs.
    """)

    # --- RESEARCH-BACKED THERMODYNAMIC LOGIC ---
    
    def calc_lsi(row):
        # Baseline LSI for untreated RO is ~0.5
        lsi = 0.5
        # PMA and PBTC are the primary drivers for high LSI/high stress. 
        # They trigger a massive threshold jump, followed by a logarithmic increase.
        if row.get('PMA', 0) > 1.0 or row.get('PBTC', 0) > 1.0:
            lsi = 2.0 # Threshold achieved
            # Diminishing returns calculation
            active_sum = (row.get('PMA', 0) * 1.5) + row.get('PBTC', 0) + (row.get('HEDP', 0) * 0.5)
            if active_sum > 0:
                lsi += math.log10(active_sum) * 0.5
        elif row.get('HEDP', 0) > 1.0 or row.get('ATMP', 0) > 1.0:
            lsi = 1.5 # Lower threshold for standard phosphonates
            active_sum = row.get('HEDP', 0) + (row.get('ATMP', 0) * 0.8)
            if active_sum > 0:
                lsi += math.log10(active_sum) * 0.4
                
        return round(min(lsi, 2.8), 2) # Hard chemical cap for LSI in standard RO

    def calc_silica(row):
        # Silica relies on steric hindrance (dispersion), driven heavily by Terpolymers
        silica = 1.0
        
        # Terpolymer is king for Silica, followed by Copolymer
        active_dispersion = (row.get('Terpolymer', 0) * 2.0) + (row.get('Copolymer', 0) * 1.2) + (row.get('Homopolymer', 0) * 0.3)
        
        if active_dispersion > 0.5:
            # Logarithmic curve capping around 2.5x to 3.0x saturation
            silica += math.log1p(active_dispersion) * 0.45
            
        return round(min(silica, 2.8), 2)

    def calc_sulfates(row):
        # CaSO4 (Calcium Sulfate) - Driven by PBTC, PMA, HEDP
        caso4_active = row.get('PBTC', 0) + row.get('PMA', 0) + (row.get('HEDP', 0) * 0.8)
        caso4 = 1.0
        if caso4_active > 1.0:
            caso4 = 2.5 + (math.log10(caso4_active) * 1.5)
        
        # BaSO4 (Barium Sulfate) - Extremely insoluble, requires specific functional groups (PMA/PBTC/Copolymers)
        baso4_active = (row.get('PMA', 0) * 2.0) + row.get('PBTC', 0) + row.get('Copolymer', 0)
        baso4 = 1.0
        if baso4_active > 1.0:
            baso4 = 50 + (math.log10(baso4_active) * 80)
            
        # SrSO4 (Strontium Sulfate)
        srso4 = 1.0
        if baso4_active > 1.0:
            srso4 = 5 + (math.log10(baso4_active) * 6)
            
        return round(min(caso4, 5.0), 2), int(min(baso4, 180)), int(min(srso4, 20))

    def calc_iron(row):
        # Stoichiometric chelation via DETMPA
        iron = 0.1
        if row.get('DETMPA', 0) > 0:
            # Direct linear relationship up to a theoretical max
            iron = 0.3 + (row.get('DETMPA', 0) * 0.08)
        return round(min(iron, 1.5), 2)

    def check_coagulant(row):
        # Anionic polymers (PMA, Homopolymer) clash with cationic coagulants at high concentrations
        if row.get('PMA', 0) > 8.0 or row.get('Homopolymer', 0) > 10.0:
            return "No"
        return "Yes"

    # Define exact columns expected from the CSV (based on % ACTIVE)
    rm_columns = [
        'PBTC', 'HEDP', 'ATMP', 'SMBS', 'Copolymer', 'Terpolymer', 
        'Homopolymer', 'PMA', 'DETMPA', 'Caustic_Lye', 'NAOH_Flakes', 'Caustic_Potash'
    ]

    st.subheader("1. Download Extended Template")
    template_cols = ['Product_Name'] + rm_columns
    template_df = pd.DataFrame(columns=template_cols)
    # Example row showing realistic % ACTIVE inputs for a high-end product
    template_df.loc[0] = ['Example_Product_Premium', 5.0, 0.0, 0.0, 0.0, 3.0, 6.0, 0.0, 8.0, 2.0, 4.0, 0.0, 0.0]
    
    csv_buffer = io.StringIO()
    template_df.to_csv(csv_buffer, index=False)
    
    st.download_button(
        label="Download extended_formulations_template.csv",
        data=csv_buffer.getvalue(),
        file_name="extended_formulations_template.csv",
        mime="text/csv"
    )

    st.subheader("2. Upload & Generate Master Projection Matrix")
    uploaded_file = st.file_uploader("Upload your filled formulations CSV", type=['csv'])

    if uploaded_file is not None:
        try:
            df_formulations = pd.read_csv(uploaded_file)
            calculated_results = []

            for index, row in df_formulations.iterrows():
                product_name = row['Product_Name']
                
                # Sanitize inputs: ensure everything is a float and NaNs are 0.0
                clean_row = {col: float(row[col]) if pd.notna(row.get(col)) else 0.0 for col in rm_columns}
                
                # Run Thermodynamic Functions
                lsi_limit = calc_lsi(clean_row)
                silica_limit = calc_silica(clean_row)
                caso4, baso4, srso4 = calc_sulfates(clean_row)
                iron_limit = calc_iron(clean_row)
                coag_compat = check_coagulant(clean_row)

                calculated_results.append({
                    'Product_ID': product_name,
                    'LSI_Limit': lsi_limit,
                    'Silica_Limit': silica_limit,
                    'Iron_Max_ppm': iron_limit,
                    'CaSO4_Limit': caso4,
                    'BaSO4_Limit': baso4,
                    'SrSO4_Limit': srso4,
                    'Coagulant_Compatible': coag_compat
                })

            matrix_df = pd.DataFrame(calculated_results)
            st.success("Master Projection Matrix Calculated Successfully!")
            st.dataframe(matrix_df, use_container_width=True)

            result_buffer = io.StringIO()
            matrix_df.to_csv(result_buffer, index=False)
            st.download_button(
                label="Download Generated Master Projection Matrix (Master_Limits.csv)",
                data=result_buffer.getvalue(),
                file_name="Master_Limits.csv",
                mime="text/csv",
                type="primary"
            )
            
        except Exception as e:
            st.error(f"Error processing file. Please ensure it matches the template format. Details: {e}")
