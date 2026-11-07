"""Benchmark cases used in RO_Projection_Engine_Review.pdf.

Replays the Streamlit UI pipeline (ion auto-balance -> auto/manual acid -> concentrate -> indices -> product
selection) on five waters using projection_engine.UtilityProjectionEngine, with Streamlit/pandas/plotly stubbed out.
Usage:  python docs/review/benchmark_cases.py .
"""
import sys, math, types, json
class _SS(dict):
    __getattr__ = dict.__getitem__
    def __setattr__(self, k, v): self[k] = v
st = types.ModuleType("streamlit"); st.session_state=_SS(); st.set_page_config=lambda **k:None
sys.modules["streamlit"]=st
for m in ["pandas","plotly","plotly.express"]: sys.modules[m]=types.ModuleType(m)
sys.path.insert(0, sys.argv[1])
from projection_engine import UtilityProjectionEngine
E=UtilityProjectionEngine(); D=dict(st.session_state.ui_ions)
eq={'Ca':20.04,'Mg':12.15,'Na':22.99,'K':39.10,'NH4':18.04,'Ba':68.67,'Sr':43.81,'Fe':27.92,'Al':8.99,'HCO3':61.02,'Cl':35.45,'SO4':48.03,'F':19.00,'NO3':62.00,'PO4':31.66,'CO3':30.01}
cat=['Ca','Mg','Na','K','NH4','Ba','Sr','Fe','Al']; an=['HCO3','Cl','SO4','F','NO3','PO4','CO3']
def run(name, ions, ph, T, rec, auto=True, manual_ph=None):
    c=dict(ions); cm=sum(c[k]/eq[k] for k in cat); am=sum(c[k]/eq[k] for k in an)
    err=(cm-am)/(cm+am)*100
    if abs(err)>5:
        if cm>am: c['Cl']+=(cm-am)*eq['Cl']
        else: c['Na']+=(am-cm)*eq['Na']
    cf=1/(1-rec/100); tf=dict(c); acid=0; tph=ph
    if auto:
        t=ph
        while t>4.0:
            adj,so4,d=E.calculate_acid_chemistry(ph,t,c['HCO3'],T)
            tc={k:v*cf for k,v in c.items()}; tc['HCO3']=adj*cf; tc['SO4']=(c['SO4']+so4)*cf
            r=E.calculate_scaling_indices(t+math.log10(cf),T,tc)
            if r and r['LSI']<=2.5:
                tph=round(t,2); tf['HCO3']=adj; tf['SO4']=c['SO4']+so4; acid=d; break
            t-=0.05
    else:
        adj,so4,acid=E.calculate_acid_chemistry(ph,manual_ph,c['HCO3'],T); tph=manual_ph
        tf['HCO3']=adj; tf['SO4']=c['SO4']+so4
    tc={k:v*cf for k,v in tf.items()}; tc['CO2']=tf['CO2']
    fd=E.calculate_scaling_indices(ph,T,c)
    r=E.calculate_scaling_indices(tph+math.log10(cf),T,tc)
    recs=E.run_expert_simulation(r,tc,T)
    out=dict(case=name, balance_err=round(err,2), Cl_after=round(c['Cl'],1), Na_after=round(c['Na'],1), TDS=round(sum(c.values()),1),
      treated_ph=tph, acid_ppm=round(acid,2), feed_LSI=fd['LSI'], conc_pH=round(tph+math.log10(cf),3), conc_LSI=r['LSI'], conc_SDSI=r['SDSI'],
      I=r['Ionic_Strength'], CaSO4=round(r['Ratio_CaSO4'],3), BaSO4=round(r['Ratio_BaSO4'],2), SrSO4=round(r['Ratio_SrSO4'],3),
      CaF2=round(r['Ratio_CaF2'],3), SiO2=round(r['Ratio_SiOH4'],3),
      recs=[(x['Product'].replace('Kem Watreat ',''),x['Required Dose (ppm)']) for x in recs])
    print(json.dumps(out))
run("A default", D, 7.5, 25, 75)
hard=dict(D); hard.update(Ca=200, HCO3=400, SO4=300, Ba=0.1, Sr=2.0, SiO2=30)
run("B hard 80%", hard, 7.8, 30, 80)
soft=dict(D); soft.update(Ca=10,Mg=3,HCO3=30,SO4=20,Cl=60,Na=40,Ba=0.0,Sr=0.0,F=0.0)
run("C soft", soft, 7.0, 25, 75)
lo=dict(D); lo.update(Ca=100, HCO3=8, SO4=1500, Na=300, Cl=200, Mg=20)
run("D low-alk high-SO4, manual pH 6", lo, 6.0, 25, 75, auto=False, manual_ph=6.0)
sw=dict(D); sw.update(Ca=0.0, Mg=0.5, Ba=0.15, Sr=3.0, HCO3=200, SO4=400)
run("E softened feed Ca=0", sw, 7.5, 25, 75)
