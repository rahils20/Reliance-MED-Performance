import streamlit as st
import pandas as pd
import io

def show_matrix_calculator():
    st.header("Proprietary Formulation Calculator")
    st.markdown("""
    This local engine calculates the absolute performance limits (LSI, Silica, Iron) 
    of your products based on their **% Active** raw material percentages. 
    **Your formulation data is processed locally and is not saved or transmitted.**
    """)

    # Base limits for untreated RO systems
    BASE_LSI = 0.5
    BASE_SILICA = 1.0 
    BASE_IRON = 0.1    

    # Multipliers: How much limit is added per 1% of ACTIVE raw material
    CONSTANTS = {
        'PBTC': {'LSI': 0.07, 'Silica': 0.01, 'Iron': 0.00},
        'Polymaleic_Acid': {'LSI': 0.06, 'Silica': 0.02, 'Iron': 0.00},
        'HEDP': {'LSI': 0.05, 'Silica': 0.01, 'Iron': 0.01},
        'ATMP': {'LSI': 0.04, 'Silica': 0.01, 'Iron': 0.00},
        'Terpolymer': {'LSI': 0.02, 'Silica': 0.06, 'Iron': 0.01},
        'Homopolymer': {'LSI': 0.01, 'Silica': 0.04, 'Iron': 0.00},
        'DETMPA': {'LSI': 0.02, 'Silica': 0.00, 'Iron': 0.05},
        'Caustic_Lye': {'LSI': 0.00, 'Silica': 0.00, 'Iron': 0.00},    # pH Adjuster only
        'Caustic_Potash': {'LSI': 0.00, 'Silica': 0.00, 'Iron': 0.00}  # pH Adjuster only
    }

    st.subheader("1. Download Template")
    template_cols = ['Product_Name'] + list(CONSTANTS.keys())
    template_df = pd.DataFrame(columns=template_cols)
    # Dummy row demonstrating % ACTIVE values
    template_df.loc[0] = ['Example_Product_A', 5.0, 0.0, 3.5, 2.5, 10.0, 0.0, 2.5, 5.0, 0.0]
    
    csv_buffer = io.StringIO()
    template_df.to_csv(csv_buffer, index=False)
    
    st.download_button(
        label="Download formulations_template.csv",
        data=csv_buffer.getvalue(),
        file_name="formulations_template.csv",
        mime="text/csv"
    )

    st.subheader("2. Upload & Generate Matrix")
    uploaded_file = st.file_uploader("Upload your filled formulations CSV", type=['csv'])

    if uploaded_file is not None:
        try:
            df_formulations = pd.read_csv(uploaded_file)
            calculated_results = []

            for index, row in df_formulations.iterrows():
                product_name = row['Product_Name']
                calc_lsi = BASE_LSI
                calc_silica = BASE_SILICA
                calc_iron = BASE_IRON
                
                for rm in CONSTANTS.keys():
                    if rm in df_formulations.columns:
                        percentage = float(row[rm])
                        calc_lsi += percentage * CONSTANTS[rm]['LSI']
                        calc_silica += percentage * CONSTANTS[rm]['Silica']
                        calc_iron += percentage * CONSTANTS[rm]['Iron']
                
                calculated_results.append({
                    'Product_ID': product_name,
                    'LSI_Limit': round(calc_lsi, 2),
                    'Silica_Limit': round(calc_silica, 2),
                    'Iron_Tolerance_ppm': round(calc_iron, 2)
                })

            matrix_df = pd.DataFrame(calculated_results)
            st.success("Matrix Calculated Successfully!")
            st.dataframe(matrix_df, use_container_width=True)

            result_buffer = io.StringIO()
            matrix_df.to_csv(result_buffer, index=False)
            st.download_button(
                label="Download Generated Product Matrix (Master_Limits.csv)",
                data=result_buffer.getvalue(),
                file_name="Master_Limits.csv",
                mime="text/csv",
                type="primary"
            )
            
        except Exception as e:
            st.error(f"Error processing file. Please ensure it matches the template format. Details: {e}")
