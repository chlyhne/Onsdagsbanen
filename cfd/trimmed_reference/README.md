# Selected trim for the 3D run

These small input files belong in the Onsdagsbanen repository. The selected
sheeting angles and fixed 2-degree twist are already in geometry.json.
trim_result.json records the optimisation settings and accepted best result;
section_force.json and section_report.json contain its 2D checks when available.
Search status: `budget_exhausted`. This is a ballpark trim pending 3D validation.
Section forces are in N/m, not whole-rig forces.

After pulling the repository on the 3D PC, run from its root:

```powershell
git pull
python -m pip install -r cfd/requirements.txt
python -m cfd.solver --install
python -m cfd.prepare_compact --source cfd/trimmed_reference --case cfd/output/trimmed_3d --threads 8
python -m cfd.marine_rans run --case cfd/output/trimmed_3d --threads 8 --iterations 3000
```

Adjust the thread count for that PC. SU2 automatic installation is for Windows;
other systems need SU2_CFD on PATH. Use a fresh case path if trimmed_3d already
exists. Mesh preparation and the solver are separate; no 3D result is included.
Inspect convergence, loading, separation and background wind-profile checks
before interpreting the final result.
