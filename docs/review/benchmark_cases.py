"""Benchmark cases A-F used in RO_Projection_Engine_Review.pdf, run through projection_engine v2.

Usage (from the repository root):  python docs/review/benchmark_cases.py
Needs only the Python standard library (projection_engine's chemistry has no third-party imports).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import projection_engine as pe  # noqa: E402

D0 = {**pe.DEFAULT_IONS, 'Cl': 400.0}  # v1 default water (11.9% out of balance; auto-balanced below)
CASES = [
    ("A default", D0, dict(feed_ph=7.5, temp_c=25, recovery_pct=75)),
    ("B hard 80%", {**D0, 'Ca': 200, 'HCO3': 400, 'SO4': 300, 'Ba': 0.1, 'Sr': 2.0, 'SiO2': 30},
     dict(feed_ph=7.8, temp_c=30, recovery_pct=80)),
    ("C soft", {**D0, 'Ca': 10, 'Mg': 3, 'HCO3': 30, 'SO4': 20, 'Cl': 60, 'Na': 40, 'Ba': 0, 'Sr': 0, 'F': 0},
     dict(feed_ph=7.0, temp_c=25, recovery_pct=75)),
    ("D low-alk hi-SO4 manual pH 6", {**D0, 'Ca': 100, 'HCO3': 8, 'SO4': 1500, 'Na': 300, 'Cl': 200, 'Mg': 20},
     dict(feed_ph=6.0, temp_c=25, recovery_pct=75, auto_acid=False, manual_ph=6.0)),
    ("E softened Ca=0", {**D0, 'Ca': 0, 'Mg': 0.5, 'Ba': 0.15, 'Sr': 3.0, 'HCO3': 200, 'SO4': 400},
     dict(feed_ph=7.5, temp_c=25, recovery_pct=75)),
    ("F invalid inputs", {**D0, 'Ca': -50, 'HCO3': -10, 'SO4': -5},
     dict(feed_ph=15, temp_c=150, recovery_pct=75, feed_flow=0)),
]

if __name__ == "__main__":
    for name, ions, kw in CASES:
        a = dict(feed_flow=10, rejection_pct=99.0, perm_ph=6.0)
        a.update(kw)
        p = pe.run_projection(ions, a.pop('feed_flow'), a.pop('temp_c'), a.pop('recovery_pct'), a.pop('rejection_pct'),
                              a.pop('feed_ph'), a.pop('perm_ph'), **a)
        if p['errors']:
            print(f"== {name}: rejected -> {p['errors']}")
            continue
        i = p['idx_conc']
        print(f"== {name}: treated pH {p['treated_ph']} acid {p['acid_dose_ppm']:.1f} mg/L | conc pH {p['conc_ph']:.2f} "
              f"| {p['gov_key']} governs, LSI {i['LSI']} | I {i['Ionic_Strength']}")
        print(f"   ratios CaSO4 {i['Ratio_CaSO4']:.3f}  BaSO4 {i['Ratio_BaSO4']:.2f}  SrSO4 {i['Ratio_SrSO4']:.3f}  "
              f"CaF2 {i['Ratio_CaF2']:.3f}  SiO2 {i['Ratio_SiOH4']:.3f}")
        print("   viable:", [(s['Product'].replace('Kem Watreat ', ''), s['Required Dose (ppm)']) for s in p['successes']])
        for r in p['rejections']:
            print("   rejected:", r['Product'].replace('Kem Watreat ', ''), '-', r['Reason'])
        for w in p['warnings']:
            print("   warning:", w)
